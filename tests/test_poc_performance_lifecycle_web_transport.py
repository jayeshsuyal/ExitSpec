from __future__ import annotations

import json
import threading
import time
from contextlib import contextmanager
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from exitspec.confirmations import contract_confirmation_fingerprint
from exitspec.reference_inference import (
    REFERENCE_ENDPOINT_CLASS,
    REFERENCE_ENDPOINT_PATH,
    REFERENCE_MODEL,
    REFERENCE_PROVIDER,
    reference_sse_payload,
)
from exitspec.web import MAX_REQUEST_BYTES, DemoSession, ExitSpecDemoServer


@contextmanager
def _running_server(tmp_path: Path):
    session = DemoSession.synthetic_support_agent(output_root=tmp_path / "runs")
    server = ExitSpecDemoServer(("127.0.0.1", 0), session)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield server
    finally:
        server.shutdown()
        worker.join(timeout=5)
        server.server_close()


def _request(
    server: ExitSpecDemoServer,
    method: str,
    target: str,
    *,
    payload=None,
    raw_body: bytes | None = None,
    content_type: str | None = "application/json",
    origin: str | None = "same",
    headers: dict[str, str] | None = None,
) -> tuple[int, dict | str, str]:
    body = (
        json.dumps(payload).encode("utf-8")
        if raw_body is None and payload is not None
        else raw_body
    )
    request_headers = dict(headers or {})
    if content_type is not None:
        request_headers["Content-Type"] = content_type
    if origin == "same":
        request_headers["Origin"] = "http://127.0.0.1:{0}".format(
            server.server_port
        )
    elif origin is not None:
        request_headers["Origin"] = origin

    connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    try:
        connection.request(method, target, body=body, headers=request_headers)
        response = connection.getresponse()
        data = response.read()
        media_type = response.getheader("Content-Type") or ""
        parsed = (
            json.loads(data.decode("utf-8"))
            if media_type.startswith("application/json")
            else data.decode("utf-8")
        )
        return response.status, parsed, media_type
    finally:
        connection.close()


def _workspace_action(server: ExitSpecDemoServer, poc_id: str) -> str:
    status, payload, _ = _request(
        server,
        "GET",
        "/api/workspace",
        content_type=None,
        origin=None,
    )
    assert status == 200
    assert isinstance(payload, dict)
    return next(
        poc["next_action_code"]
        for poc in payload["pocs"]
        if poc["poc_id"] == poc_id
    )


def _create_defined_performance_poc(server: ExitSpecDemoServer) -> str:
    status, draft, _ = _request(
        server,
        "POST",
        "/api/pocs",
        payload={
            "display_name": "Inference validation",
            "customer_label": "Northstar",
            "use_case": "Validate two bounded performance claims.",
            "owner": "field_engineer",
            "first_source_choice": "DOCUMENT",
            "idempotency_key": "create-agreement-transport",
        },
    )
    assert status == 201
    assert isinstance(draft, dict)
    poc_id = str(draft["poc_id"])

    status, captured, _ = _request(
        server,
        "POST",
        f"/api/pocs/{poc_id}/sources/document",
        payload={
            "document_text": (
                "The p95 time to first token must stay below 500 ms. "
                "Error rate must remain below 1%."
            ),
            "idempotency_key": "capture-agreement-transport",
        },
    )
    assert status == 201
    assert isinstance(captured, dict)
    assert captured["proposal_count"] == 2

    status, listed, _ = _request(
        server,
        "GET",
        f"/api/pocs/{poc_id}/proposals",
        content_type=None,
        origin=None,
    )
    assert status == 200
    assert isinstance(listed, dict)
    proposals = listed["proposals"]
    assert len(proposals) == 2
    for index, proposal in enumerate(proposals):
        status, _, _ = _request(
            server,
            "POST",
            (
                f"/api/pocs/{poc_id}/proposals/"
                f"{proposal['proposal_id']}/decision"
            ),
            payload={
                "decision": "KEEP_FOR_CONTRACT",
                "reviewer": "Jayesh",
                "rationale": "Keep this measurable customer requirement.",
                "idempotency_key": f"keep-agreement-{index}",
            },
        )
        assert status == 201

    definition_root = f"/api/pocs/{poc_id}/definitions"
    for index, (proposal, metric, threshold) in enumerate(
        zip(
            proposals,
            ("TTFT_P95_MS", "ERROR_RATE_PERCENT"),
            (500, 1),
            strict=True,
        )
    ):
        status, _, _ = _request(
            server,
            "POST",
            definition_root,
            payload={
                "proposal_id": proposal["proposal_id"],
                "metric": metric,
                "operator": "LT",
                "threshold": threshold,
                "minimum_samples": 100,
                "concurrency": 4,
                "prompt_tokens_min": 512,
                "prompt_tokens_max": 4096,
                "output_tokens_min": 64,
                "output_tokens_max": 512,
                "reviewer": "Jayesh",
                "rationale": "This exact rule defines POC acceptance.",
                "idempotency_key": f"define-agreement-{index}",
            },
        )
        assert status == 201
    return poc_id


def _prepare_payload() -> dict:
    return _prepare_payload_for(
        "http://127.0.0.1:8000/v1/chat/completions"
    )


def _prepare_payload_for(endpoint: str) -> dict:
    return {
        "target_provider": "Local vLLM",
        "endpoint_class": "OpenAI-compatible chat completions",
        "endpoint": endpoint,
        "model": "Qwen/Qwen2.5-0.5B-Instruct",
        "reviewer": "Jayesh",
        "rationale": "Bind the reviewed requirements to this exact target.",
        "idempotency_key": "prepare-performance-agreement",
    }


def _review_api_path(review_url: str) -> str:
    assert review_url.startswith("/review/")
    token = review_url.rsplit("/", 1)[-1]
    assert token
    return f"/api/review/{token}"


def _record_customer_decision(
    server: ExitSpecDemoServer,
    poc_id: str,
    *,
    decision: str = "CONFIRM",
    agreement_acknowledged: bool = True,
    rationale: str = "The exact target and both criteria were reviewed.",
    idempotency_key: str = "confirm-dynamic-run-transport",
) -> tuple[dict, tuple[int, dict | str, str]]:
    agreement_root = f"/api/pocs/{poc_id}/agreement"
    status, agreement, _ = _request(
        server,
        "GET",
        agreement_root,
        content_type=None,
        origin=None,
    )
    assert status == 200
    assert isinstance(agreement, dict)
    customer_review = agreement["customer_review"]
    assert customer_review["status"] == "PENDING"
    review_api = _review_api_path(customer_review["review_url"])
    status, customer_view, _ = _request(
        server,
        "GET",
        review_api,
        content_type=None,
        origin=None,
    )
    assert status == 200
    assert isinstance(customer_view, dict)
    review = customer_view["review"]
    response = _request(
        server,
        "POST",
        review_api + "/decision",
        payload={
            "review_id": review["review_id"],
            "contract_id": review["contract_id"],
            "contract_version": review["contract_version"],
            "decision": decision,
            "agreement_acknowledged": agreement_acknowledged,
            "rationale": rationale,
            "idempotency_key": idempotency_key,
        },
    )
    return customer_view, response


@contextmanager
def _streaming_endpoint():
    state = {"requests": 0}
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self) -> None:
            with lock:
                state["requests"] += 1
            length = int(self.headers.get("Content-Length", "0"))
            request = json.loads(self.rfile.read(length))
            assert request["stream"] is True
            payload = (
                b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n'
                b"data: [DONE]\n\n"
            )
            self.send_response(200)
            self.send_header(
                "Content-Type",
                "text/event-stream; charset=utf-8",
            )
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: object) -> None:
            return

    endpoint = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=endpoint.serve_forever, daemon=True)
    worker.start()
    try:
        yield (
            "http://127.0.0.1:{0}/v1/chat/completions".format(
                endpoint.server_port
            ),
            state,
        )
    finally:
        endpoint.shutdown()
        worker.join(timeout=5)
        endpoint.server_close()


@contextmanager
def _unready_endpoint():
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            payload = b'{"error":"endpoint not ready"}'
            self.send_response(503)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: object) -> None:
            return

    endpoint = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=endpoint.serve_forever, daemon=True)
    worker.start()
    try:
        yield "http://127.0.0.1:{0}/v1/chat/completions".format(
            endpoint.server_port
        )
    finally:
        endpoint.shutdown()
        worker.join(timeout=5)
        endpoint.server_close()


def _freeze_agreement(
    server: ExitSpecDemoServer,
    poc_id: str,
    endpoint: str,
) -> str:
    root = f"/api/pocs/{poc_id}/agreement"
    prepared = _request(
        server,
        "POST",
        root,
        payload=_prepare_payload_for(endpoint),
    )
    assert prepared[0] == 201
    _, confirmed = _record_customer_decision(
        server,
        poc_id,
        idempotency_key="confirm-dynamic-run-transport",
    )
    assert confirmed[0] == 200
    assert confirmed[1]["decision"]["decision"] == "CONFIRM"
    frozen = _request(
        server,
        "POST",
        root + "/freeze",
        payload={"idempotency_key": "freeze-dynamic-run-transport"},
    )
    assert frozen[0] == 201
    return str(frozen[1]["frozen_contract"]["canonical_hash"])


def _start_and_wait_for_terminal_run(
    server: ExitSpecDemoServer,
    poc_id: str,
    *,
    idempotency_key: str,
) -> dict:
    root = f"/api/pocs/{poc_id}/runs"
    started = _request(
        server,
        "POST",
        root,
        payload={
            "execution_acknowledged": True,
            "idempotency_key": idempotency_key,
        },
    )
    assert started[0] == 202

    deadline = time.monotonic() + 10
    latest = None
    while time.monotonic() < deadline:
        status, latest, _ = _request(
            server,
            "GET",
            root + "/latest",
            content_type=None,
            origin=None,
        )
        assert status == 200
        assert isinstance(latest, dict)
        if latest["is_terminal"]:
            break
        time.sleep(0.01)
    assert latest is not None and latest["is_terminal"] is True
    return latest


def _dynamic_lifecycle_state(
    server: ExitSpecDemoServer,
    poc_id: str,
) -> tuple:
    return (
        server.draft_poc_service.get(poc_id),
        server.poc_source_intake.list_receipts(poc_id),
        server.proposal_review_service.list_proposals(poc_id),
        tuple(
            definition
            for definition in server.contract_definition_service.definitions()
            if definition.poc_id == poc_id
        ),
        server.performance_lifecycle_service.snapshot(poc_id),
        server.poc_performance_run_service.snapshot(poc_id),
        server.poc_closure_service.get(poc_id),
    )


def test_agreement_page_and_api_complete_external_confirm_freeze_loop(tmp_path):
    with _running_server(tmp_path) as server:
        poc_id = _create_defined_performance_poc(server)
        root = f"/api/pocs/{poc_id}/agreement"

        page = _request(
            server,
            "GET",
            f"/app/pocs/{poc_id}/agreement",
            content_type=None,
            origin=None,
        )
        before = _request(
            server,
            "GET",
            root,
            content_type=None,
            origin=None,
        )
        before_action = _workspace_action(server, poc_id)

        prepared = _request(server, "POST", root, payload=_prepare_payload())
        replay = _request(server, "POST", root, payload=_prepare_payload())
        prepared_action = _workspace_action(server, poc_id)

        customer_view, confirmed = _record_customer_decision(
            server,
            poc_id,
            idempotency_key="confirm-performance-agreement",
        )
        confirmed_action = _workspace_action(server, poc_id)

        frozen = _request(
            server,
            "POST",
            root + "/freeze",
            payload={"idempotency_key": "freeze-performance-agreement"},
        )
        frozen_action = _workspace_action(server, poc_id)
        after = _request(
            server,
            "GET",
            root,
            content_type=None,
            origin=None,
        )
        lifecycle_snapshot = server.performance_lifecycle_service.snapshot(
            poc_id,
            allow_empty=False,
        )
        assert lifecycle_snapshot.preparation is not None
        assert lifecycle_snapshot.review_invitation is not None
        approved_contract = lifecycle_snapshot.preparation.approved_contract
        expected_fingerprint = contract_confirmation_fingerprint(
            approved_contract
        )

    assert page[0] == 200
    assert page[2].startswith("text/html")
    assert 'id="agreement-workbench"' in page[1]
    assert before[0] == 200
    assert before[1]["draft"] is None
    assert [item["metric"] for item in before[1]["definitions"]] == [
        "TTFT_P95_MS",
        "ERROR_RATE_PERCENT",
    ]
    assert before_action == "PREPARE_AGREEMENT"
    assert prepared[0] == 201
    assert prepared[1]["disposition"] == "CREATED"
    assert replay[0] == 200
    assert replay[1]["disposition"] == "IDEMPOTENT_REPLAY"
    assert replay[1]["draft"] == prepared[1]["draft"]
    assert prepared_action == "WAIT_FOR_CUSTOMER"
    assert customer_view["review"]["status"] == "PENDING"
    assert customer_view["review"]["contract_id"] == approved_contract.id
    assert customer_view["review"]["contract_version"] == (
        approved_contract.version
    )
    assert customer_view["review"]["confirmation_fingerprint"] == (
        expected_fingerprint
    )
    assert lifecycle_snapshot.review_invitation.confirmation_fingerprint == (
        expected_fingerprint
    )
    assert poc_id not in after[1]["customer_review"]["review_url"]
    assert customer_view["review"]["criteria"][0]["metric"] == (
        "P95 time to first token and error rate"
    )
    assert customer_view["review"]["criteria"][0]["threshold"] == (
        "P95 TTFT below 500 ms · error rate below 1%"
    )
    assert customer_view["review"]["criteria"][0]["sample"] == (
        "100 successful timing samples · 100 attempted requests"
    )
    assert "reviewer" not in json.dumps(customer_view).lower()
    assert "draft_sha256" not in json.dumps(customer_view)
    assert confirmed[0] == 200
    assert confirmed[1]["confirmation"]["agreement_acknowledged"] is True
    assert confirmed[1]["confirmation"]["decision"] == "CONFIRM"
    assert confirmed[1]["review"]["status"] == "CONFIRMED"
    assert confirmed_action == "FREEZE_CONFIRMED_CONTRACT"
    assert frozen[0] == 201
    assert frozen[1]["frozen_contract"]["canonical_hash"]
    assert frozen_action == "RUN_POC"
    assert after[1]["draft"] == prepared[1]["draft"]
    assert after[1]["customer_review"]["status"] == "CONFIRMED"
    assert after[1]["confirmation"]["confirmation_id"] == (
        confirmed[1]["confirmation"]["confirmation_id"]
    )
    assert after[1]["frozen_contract"] == frozen[1]["frozen_contract"]
    serialized = json.dumps((prepared, confirmed, frozen, after)).lower()
    for forbidden in ("evidence_pack", '"verdict"', '"pass"', '"fail"'):
        assert forbidden not in serialized


def test_dynamic_review_capability_rejects_tampering_and_replays_exactly(
    tmp_path,
):
    with _running_server(tmp_path) as server:
        poc_id = _create_defined_performance_poc(server)
        agreement_root = f"/api/pocs/{poc_id}/agreement"
        prepared = _request(
            server,
            "POST",
            agreement_root,
            payload=_prepare_payload(),
        )
        assert prepared[0] == 201
        agreement = _request(
            server,
            "GET",
            agreement_root,
            content_type=None,
            origin=None,
        )[1]
        review_api = _review_api_path(
            agreement["customer_review"]["review_url"]
        )
        customer_view = _request(
            server,
            "GET",
            review_api,
            content_type=None,
            origin=None,
        )[1]
        review = customer_view["review"]
        exact_payload = {
            "review_id": review["review_id"],
            "contract_id": review["contract_id"],
            "contract_version": review["contract_version"],
            "decision": "CONFIRM",
            "agreement_acknowledged": True,
            "rationale": "Confirm the exact capability-bound agreement.",
            "idempotency_key": "dynamic-review-exact-replay",
        }

        invalid_read = _request(
            server,
            "GET",
            "/api/review/not-a-real-capability",
            content_type=None,
            origin=None,
        )
        invalid_write = _request(
            server,
            "POST",
            "/api/review/not-a-real-capability/decision",
            payload=exact_payload,
        )
        crossed_binding = _request(
            server,
            "POST",
            review_api + "/decision",
            payload={**exact_payload, "contract_id": "contract_from_another_poc"},
        )
        missing_acknowledgement = _request(
            server,
            "POST",
            review_api + "/decision",
            payload={
                key: value
                for key, value in exact_payload.items()
                if key != "agreement_acknowledged"
            },
        )
        false_acknowledgement = _request(
            server,
            "POST",
            review_api + "/decision",
            payload={**exact_payload, "agreement_acknowledged": False},
        )
        direct_confirmation = _request(
            server,
            "POST",
            agreement_root + "/confirm",
            payload={
                "confirmer": "Employee self-attestation",
                "agreement_acknowledged": True,
                "rationale": "Attempt to bypass the review capability.",
                "idempotency_key": "removed-direct-confirm-route",
            },
        )
        before_valid_decision = _request(
            server,
            "GET",
            agreement_root,
            content_type=None,
            origin=None,
        )
        confirmed = _request(
            server,
            "POST",
            review_api + "/decision",
            payload=exact_payload,
        )
        replay = _request(
            server,
            "POST",
            review_api + "/decision",
            payload=exact_payload,
        )
        conflicting_replay = _request(
            server,
            "POST",
            review_api + "/decision",
            payload={
                **exact_payload,
                "rationale": "Attempt to mutate an idempotent decision.",
            },
        )

    assert invalid_read[0] == invalid_write[0] == 404
    assert crossed_binding[:2] == (
        409,
        {"error": "contract_id does not match this customer review link."},
    )
    assert missing_acknowledgement[0] == false_acknowledgement[0] == 400
    assert direct_confirmation[:2] == (
        400,
        {"error": "Performance agreement request is invalid."},
    )
    assert before_valid_decision[1]["confirmation"] is None
    assert before_valid_decision[1]["customer_review"]["status"] == "PENDING"
    assert confirmed[0] == replay[0] == 200
    assert confirmed[1]["idempotent_replay"] is False
    assert replay[1]["idempotent_replay"] is True
    assert replay[1]["confirmation_id"] == confirmed[1]["confirmation_id"]
    assert conflicting_replay[:2] == (
        409,
        {"error": "Customer review conflicts with current POC state."},
    )


def test_customer_request_changes_blocks_freeze_and_starts_revision(tmp_path):
    with _running_server(tmp_path) as server:
        poc_id = _create_defined_performance_poc(server)
        agreement_root = f"/api/pocs/{poc_id}/agreement"
        prepared = _request(
            server,
            "POST",
            agreement_root,
            payload=_prepare_payload(),
        )
        assert prepared[0] == 201
        customer_view, changed = _record_customer_decision(
            server,
            poc_id,
            decision="REQUEST_CHANGES",
            agreement_acknowledged=False,
            rationale="The workload does not match the customer call.",
            idempotency_key="request-dynamic-agreement-changes",
        )
        action = _workspace_action(server, poc_id)
        freeze = _request(
            server,
            "POST",
            agreement_root + "/freeze",
            payload={"idempotency_key": "freeze-after-request-changes"},
        )
        after = _request(
            server,
            "GET",
            agreement_root,
            content_type=None,
            origin=None,
        )
        reviewed = _request(
            server,
            "GET",
            _review_api_path(after[1]["customer_review"]["review_url"]),
            content_type=None,
            origin=None,
        )

    assert customer_view["review"]["status"] == "PENDING"
    assert changed[0] == 200
    assert changed[1]["decision"]["decision"] == "REQUEST_CHANGES"
    assert changed[1]["review"]["status"] == "CHANGES_REQUESTED"
    assert action == "START_REVISION"
    assert freeze[:2] == (
        409,
        {"error": "Performance agreement conflicts with current POC state."},
    )
    assert after[1]["customer_review"]["status"] == "CHANGES_REQUESTED"
    assert after[1]["confirmation"]["decision"] == "REQUEST_CHANGES"
    assert after[1]["frozen_contract"] is None
    assert reviewed[1]["review"]["status"] == "CHANGES_REQUESTED"


def test_local_reference_target_is_exact_bounded_and_credential_free(
    tmp_path,
):
    valid = {
        "max_tokens": 64,
        "messages": [{"content": "Measure this request.", "role": "user"}],
        "model": REFERENCE_MODEL,
        "stream": True,
        "temperature": 0,
    }
    with _running_server(tmp_path) as server:
        ok = _request(
            server,
            "POST",
            REFERENCE_ENDPOINT_PATH,
            payload=valid,
            origin=None,
        )
        wrong_method = _request(
            server,
            "GET",
            REFERENCE_ENDPOINT_PATH,
            content_type=None,
            origin=None,
        )
        parameterized = _request(
            server,
            "POST",
            REFERENCE_ENDPOINT_PATH + "?model=other",
            payload=valid,
            origin=None,
        )
        credentialed = _request(
            server,
            "POST",
            REFERENCE_ENDPOINT_PATH,
            payload=valid,
            origin=None,
            headers={"Authorization": "Bearer should-never-be-sent"},
        )
        wrong_model_payload = dict(valid)
        wrong_model_payload["model"] = "some-real-model"
        wrong_model = _request(
            server,
            "POST",
            REFERENCE_ENDPOINT_PATH,
            payload=wrong_model_payload,
            origin=None,
        )

    assert ok == (
        200,
        reference_sse_payload().decode("utf-8"),
        "text/event-stream; charset=utf-8",
    )
    assert wrong_method[:2] == (
        405,
        {"error": "Reference inference method is not allowed."},
    )
    assert parameterized[:2] == (
        400,
        {"error": "Reference inference request is invalid."},
    )
    assert credentialed[:2] == (
        400,
        {"error": "Reference inference does not accept credentials."},
    )
    assert wrong_model[:2] == (
        400,
        {"error": "Reference inference request is invalid."},
    )


@pytest.mark.parametrize(
    ("content_type", "origin", "expected_status", "message"),
    (
        ("text/plain", "same", 415, "Content-Type must be application/json."),
        (None, "same", 415, "Content-Type must be application/json."),
        ("application/json", None, 403, "Origin is not allowed."),
        (
            "application/json",
            "https://evil.test",
            403,
            "Origin is not allowed.",
        ),
    ),
)
def test_agreement_writes_require_json_and_exact_same_origin(
    tmp_path,
    content_type,
    origin,
    expected_status,
    message,
):
    with _running_server(tmp_path) as server:
        poc_id = _create_defined_performance_poc(server)
        response = _request(
            server,
            "POST",
            f"/api/pocs/{poc_id}/agreement",
            payload=_prepare_payload(),
            content_type=content_type,
            origin=origin,
        )

    assert response[:2] == (expected_status, {"error": message})


def test_agreement_transport_rejects_header_authority_and_unsafe_json(
    tmp_path,
):
    with _running_server(tmp_path) as server:
        poc_id = _create_defined_performance_poc(server)
        root = f"/api/pocs/{poc_id}/agreement"
        payload = _prepare_payload()
        header_authority = _request(
            server,
            "POST",
            root,
            payload=payload,
            headers={"Idempotency-Key": "must-not-control-agreement"},
        )
        duplicated = json.dumps(payload).replace(
            '"model": "Qwen/Qwen2.5-0.5B-Instruct"',
            (
                '"model": "Qwen/Qwen2.5-0.5B-Instruct", '
                '"model": "unexpected"'
            ),
        )
        duplicate_json = _request(
            server,
            "POST",
            root,
            raw_body=duplicated.encode("utf-8"),
        )
        too_large = _request(
            server,
            "POST",
            root,
            raw_body=b"{" + b" " * MAX_REQUEST_BYTES + b"}",
        )
        parameterized = _request(
            server,
            "POST",
            root + "?run=true",
            payload=payload,
        )

    invalid = {"error": "Performance agreement request is invalid."}
    assert header_authority[:2] == duplicate_json[:2] == (400, invalid)
    assert parameterized[:2] == (400, invalid)
    assert too_large[:2] == (
        413,
        {"error": "Performance agreement request is too large."},
    )


def test_agreement_assets_archived_routes_and_methods_fail_closed(tmp_path):
    with _running_server(tmp_path) as server:
        poc_id = _create_defined_performance_poc(server)
        page_root = f"/app/pocs/{poc_id}/agreement"
        api_root = f"/api/pocs/{poc_id}/agreement"
        css = _request(
            server,
            "GET",
            "/agreement.css",
            content_type=None,
            origin=None,
        )
        javascript = _request(
            server,
            "GET",
            "/agreement.js",
            content_type=None,
            origin=None,
        )
        parameterized_page = _request(
            server,
            "GET",
            page_root + "?next=run",
            content_type=None,
            origin=None,
        )
        unsupported = _request(
            server,
            "PATCH",
            api_root,
            content_type=None,
            origin=None,
        )
        server.draft_poc_service.archive(poc_id)
        archived_page = _request(
            server,
            "GET",
            page_root,
            content_type=None,
            origin=None,
        )
        archived_read = _request(
            server,
            "GET",
            api_root,
            content_type=None,
            origin=None,
        )
        archived_write = _request(
            server,
            "POST",
            api_root,
            payload=_prepare_payload(),
        )

    assert css[0] == javascript[0] == 200
    assert css[2].startswith("text/css")
    assert "javascript" in javascript[2]
    assert parameterized_page[:2] == (
        400,
        {"error": "Draft POC routes do not accept URL parameters."},
    )
    assert unsupported[:2] == (
        405,
        {"error": "Performance agreement method is not allowed."},
    )
    assert archived_page[:2] == archived_read[:2] == archived_write[:2] == (
        404,
        {"error": "Performance agreement was not found."},
    )


def test_frozen_poc_run_api_returns_only_verified_dynamic_evidence(tmp_path):
    with _streaming_endpoint() as (endpoint, endpoint_state):
        with _running_server(tmp_path) as server:
            poc_id = _create_defined_performance_poc(server)
            contract_hash = _freeze_agreement(
                server,
                poc_id,
                endpoint,
            )
            root = f"/api/pocs/{poc_id}"
            proof_page = _request(
                server,
                "GET",
                f"/app/pocs/{poc_id}",
                content_type=None,
                origin=None,
            )
            proof_css = _request(
                server,
                "GET",
                "/proof.css",
                content_type=None,
                origin=None,
            )
            proof_javascript = _request(
                server,
                "GET",
                "/proof.js",
                content_type=None,
                origin=None,
            )
            action_before_run = _workspace_action(server, poc_id)
            before = _request(
                server,
                "GET",
                root + "/runs/latest",
                content_type=None,
                origin=None,
            )
            started = _request(
                server,
                "POST",
                root + "/runs",
                payload={
                    "execution_acknowledged": True,
                    "idempotency_key": "execute-dynamic-run-transport",
                },
            )
            deadline = time.monotonic() + 10
            latest = before
            while time.monotonic() < deadline:
                latest = _request(
                    server,
                    "GET",
                    root + "/runs/latest",
                    content_type=None,
                    origin=None,
                )
                if latest[1]["is_terminal"]:
                    break
                time.sleep(0.01)
            evidence = _request(
                server,
                "GET",
                root + "/evidence",
                content_type=None,
                origin=None,
            )
            operation = _request(
                server,
                "GET",
                root + "/runs/" + latest[1]["operation_id"],
                content_type=None,
                origin=None,
            )
            replay = _request(
                server,
                "POST",
                root + "/runs",
                payload={
                    "execution_acknowledged": True,
                    "idempotency_key": "execute-dynamic-run-transport",
                },
            )
            workspace_after = _request(
                server,
                "GET",
                "/api/workspace",
                content_type=None,
                origin=None,
            )
            closure_eligibility = _request(
                server,
                "GET",
                f"/api/workspace/pocs/{poc_id}/closure",
                content_type=None,
                origin=None,
            )
            assert isinstance(closure_eligibility[1], dict)
            closure_binding = closure_eligibility[1][
                "eligible_evidence_binding"
            ]
            closed = _request(
                server,
                "POST",
                f"/api/workspace/pocs/{poc_id}/closure",
                payload={
                    "decision": "HANDOFF_COMPLETED",
                    "decided_by": "field_engineer",
                    "rationale": "Performance Evidence Pack handed off.",
                    "evidence_binding": closure_binding,
                    "idempotency_key": "close-dynamic-performance-poc",
                },
            )
            completed_workspace = _request(
                server,
                "GET",
                "/api/workspace?filter=Completed",
                content_type=None,
                origin=None,
            )

    assert proof_page[0] == proof_css[0] == proof_javascript[0] == 200
    assert proof_page[2].startswith("text/html")
    assert 'id="performance-main"' in proof_page[1]
    assert 'id="execution-acknowledged"' in proof_page[1]
    assert proof_css[2].startswith("text/css")
    assert "javascript" in proof_javascript[2]
    assert action_before_run == "RUN_POC"
    assert before[0] == 200
    assert before[1]["status"] == "NOT_STARTED"
    assert before[1]["operation_id"] is None
    assert started[0] == 202
    assert started[1]["replayed"] is False
    assert latest[0] == evidence[0] == operation[0] == 200
    assert latest[1]["contract_hash"] == contract_hash
    assert latest[1]["status"] == "COMPLETED"
    assert latest[1]["verdict"] == "PASS"
    assert latest[1]["attempted_count"] == 100
    assert latest[1]["successful_count"] == 100
    assert latest[1]["error_count"] == 0
    assert latest[1]["p95_ttft_ms"] is not None
    assert latest[1]["error_rate_percent"] == "0"
    assert latest[1]["evidence_pack_url"].startswith("/artifacts/run_")
    assert latest[1]["is_terminal"] is True
    assert evidence[1] == latest[1]
    assert operation[1] == latest[1]
    assert replay[0] == 200
    assert replay[1]["replayed"] is True
    assert replay[1]["operation"] == latest[1]
    assert endpoint_state["requests"] == 111
    assert workspace_after[0] == 200
    projected = next(
        item
        for item in workspace_after[1]["pocs"]
        if item["poc_id"] == poc_id
    )
    assert projected["derived_phase"] == "DECIDE"
    assert projected["next_action_code"] == "RECORD_DECISION_HANDOFF"
    assert projected["latest_evidence_summary"]["status"] == "PASS"
    assert projected["latest_evidence_summary"]["report_url"] == (
        latest[1]["evidence_pack_url"]
    )
    assert closure_eligibility[0] == 200
    assert closure_eligibility[1]["closeable"] is True
    assert closure_binding["contract_hash"] == contract_hash
    assert closure_binding["verdict"] == "PASS"
    assert closed[0] == 201
    assert closed[1]["closure"]["shipping_authorized"] is False
    assert completed_workspace[0] == 200
    completed_projection = next(
        item
        for item in completed_workspace[1]["pocs"]
        if item["poc_id"] == poc_id
    )
    assert completed_projection["archive_state"] == "COMPLETED"
    assert completed_projection["next_action_code"] == "NONE"


def test_unseen_email_completes_reference_evaluator_evidence_and_handoff(
    tmp_path,
):
    raw_identity = "buyer@example.com"
    with _running_server(tmp_path) as server:
        status, draft, _ = _request(
            server,
            "POST",
            "/api/pocs",
            payload={
                "display_name": "Email inference POC",
                "customer_label": "Northstar",
                "use_case": "Turn an email into bounded proof.",
                "owner": "field_engineer",
                "first_source_choice": "EMAIL",
                "idempotency_key": "create-email-reference-e2e",
            },
        )
        assert status == 201
        assert isinstance(draft, dict)
        poc_id = str(draft["poc_id"])

        status, receipt, _ = _request(
            server,
            "POST",
            f"/api/pocs/{poc_id}/sources/email-text",
            payload={
                "email_text": (
                    f"From: {raw_identity}\n"
                    "The p95 time to first token must stay below 500 ms. "
                    "Error rate must remain below 1%. "
                    "Answer quality must feel delightful."
                ),
                "idempotency_key": "capture-email-reference-e2e",
            },
        )
        assert status == 201
        assert receipt["proposal_count"] == 3

        status, proposal_payload, _ = _request(
            server,
            "GET",
            f"/api/pocs/{poc_id}/proposals",
            content_type=None,
            origin=None,
        )
        assert status == 200
        proposals = proposal_payload["proposals"]
        assert len(proposals) == 3
        kept = []
        for index, proposal in enumerate(proposals):
            claim = proposal["normalized_claim"].lower()
            decision = (
                "KEEP_FOR_CONTRACT"
                if "first token" in claim or "error rate" in claim
                else "DISCARD"
            )
            status, _, _ = _request(
                server,
                "POST",
                (
                    f"/api/pocs/{poc_id}/proposals/"
                    f"{proposal['proposal_id']}/decision"
                ),
                payload={
                    "decision": decision,
                    "reviewer": "field_engineer",
                    "rationale": (
                        "Keep the supported measurable rule."
                        if decision == "KEEP_FOR_CONTRACT"
                        else (
                            "The current evaluator does not measure this "
                            "claim; retain it as NOT_PROVEN."
                        )
                    ),
                    "idempotency_key": f"review-email-reference-{index}",
                },
            )
            assert status == 201
            if decision == "KEEP_FOR_CONTRACT":
                kept.append(proposal)
        assert len(kept) == 2

        for index, proposal in enumerate(kept):
            is_ttft = "first token" in proposal["normalized_claim"].lower()
            status, _, _ = _request(
                server,
                "POST",
                f"/api/pocs/{poc_id}/definitions",
                payload={
                    "proposal_id": proposal["proposal_id"],
                    "metric": (
                        "TTFT_P95_MS"
                        if is_ttft
                        else "ERROR_RATE_PERCENT"
                    ),
                    "operator": "LT",
                    "threshold": 500 if is_ttft else 1,
                    "minimum_samples": 100,
                    "concurrency": 4,
                    "prompt_tokens_min": 512,
                    "prompt_tokens_max": 4096,
                    "output_tokens_min": 64,
                    "output_tokens_max": 512,
                    "reviewer": "field_engineer",
                    "rationale": (
                        "Confirm the exact measurable rule from the email."
                    ),
                    "idempotency_key": f"define-email-reference-{index}",
                },
            )
            assert status == 201

        agreement_root = f"/api/pocs/{poc_id}/agreement"
        reference_endpoint = (
            f"http://127.0.0.1:{server.server_port}"
            f"{REFERENCE_ENDPOINT_PATH}"
        )
        status, _, _ = _request(
            server,
            "POST",
            agreement_root,
            payload={
                "target_provider": REFERENCE_PROVIDER,
                "endpoint_class": REFERENCE_ENDPOINT_CLASS,
                "endpoint": reference_endpoint,
                "model": REFERENCE_MODEL,
                "reviewer": "field_engineer",
                "rationale": (
                    "Use the deterministic local target to prove the "
                    "ExitSpec workflow, not production inference."
                ),
                "idempotency_key": "prepare-email-reference-e2e",
            },
        )
        assert status == 201
        status, customer_draft_projection, _ = _request(
            server,
            "GET",
            agreement_root,
            content_type=None,
            origin=None,
        )
        assert status == 200
        _, confirmation_response = _record_customer_decision(
            server,
            poc_id,
            rationale=(
                "The customer reviewed the exact target, supported "
                "criteria, and NOT_PROVEN boundary."
            ),
            idempotency_key="confirm-email-reference-e2e",
        )
        assert confirmation_response[0] == 200
        status, frozen, _ = _request(
            server,
            "POST",
            agreement_root + "/freeze",
            payload={
                "idempotency_key": "freeze-email-reference-e2e"
            },
        )
        assert status == 201

        run_root = f"/api/pocs/{poc_id}/runs"
        status, _, _ = _request(
            server,
            "POST",
            run_root,
            payload={
                "execution_acknowledged": True,
                "idempotency_key": "run-email-reference-e2e",
            },
        )
        assert status == 202
        deadline = time.monotonic() + 10
        latest = None
        while time.monotonic() < deadline:
            status, latest, _ = _request(
                server,
                "GET",
                run_root + "/latest",
                content_type=None,
                origin=None,
            )
            assert status == 200
            if latest["is_terminal"]:
                break
            time.sleep(0.01)
        assert latest is not None and latest["is_terminal"] is True

        evidence_url = str(latest["evidence_pack_url"])
        pack = _request(
            server,
            "GET",
            evidence_url,
            content_type=None,
            origin=None,
        )
        closure_state = _request(
            server,
            "GET",
            f"/api/workspace/pocs/{poc_id}/closure",
            content_type=None,
            origin=None,
        )
        binding = closure_state[1]["eligible_evidence_binding"]
        closed = _request(
            server,
            "POST",
            f"/api/workspace/pocs/{poc_id}/closure",
            payload={
                "decision": "HANDOFF_COMPLETED",
                "decided_by": "field_engineer",
                "rationale": "Customer-safe Evidence Pack handed off.",
                "evidence_binding": binding,
                "idempotency_key": "close-email-reference-e2e",
            },
        )
        completed = _request(
            server,
            "GET",
            "/api/workspace?filter=Completed",
            content_type=None,
            origin=None,
        )

    assert frozen["frozen_contract"]["endpoint"] == reference_endpoint
    assert customer_draft_projection["not_proven_claims"] == [
        "Answer quality must feel delightful."
    ]
    assert latest["status"] == "COMPLETED"
    assert latest["verdict"] == "PASS"
    assert latest["attempted_count"] == 100
    assert latest["successful_count"] == 100
    assert pack[0] == 200
    assert pack[2].startswith("text/html")
    assert "Answer quality must feel delightful." in pack[1]
    assert "NOT_PROVEN" in pack[1]
    assert raw_identity not in pack[1]
    assert closure_state[1]["closeable"] is True
    assert binding["contract_hash"] == (
        frozen["frozen_contract"]["canonical_hash"]
    )
    assert closed[0] == 201
    assert closed[1]["closure"]["shipping_authorized"] is False
    completed_poc = next(
        item
        for item in completed[1]["pocs"]
        if item["poc_id"] == poc_id
    )
    assert completed_poc["archive_state"] == "COMPLETED"
    assert completed_poc["next_action_code"] == "NONE"


def test_dynamic_run_transport_gates_and_pre_freeze_state_fail_closed(tmp_path):
    with _running_server(tmp_path) as server:
        poc_id = _create_defined_performance_poc(server)
        root = f"/api/pocs/{poc_id}/runs"
        body = {
            "execution_acknowledged": True,
            "idempotency_key": "execute-gated-run",
        }
        before_freeze = _request(server, "POST", root, payload=body)
        proof_before_freeze = _request(
            server,
            "GET",
            f"/app/pocs/{poc_id}",
            content_type=None,
            origin=None,
        )
        wrong_media = _request(
            server,
            "POST",
            root,
            payload=body,
            content_type="text/plain",
        )
        missing_origin = _request(
            server,
            "POST",
            root,
            payload=body,
            origin=None,
        )
        header_authority = _request(
            server,
            "POST",
            root,
            payload=body,
            headers={"Idempotency-Key": "must-not-control-run"},
        )
        duplicated = json.dumps(body).replace(
            '"execution_acknowledged": true',
            (
                '"execution_acknowledged": true, '
                '"execution_acknowledged": false'
            ),
        )
        duplicate_json = _request(
            server,
            "POST",
            root,
            raw_body=duplicated.encode("utf-8"),
        )
        too_large = _request(
            server,
            "POST",
            root,
            raw_body=b"{" + b" " * MAX_REQUEST_BYTES + b"}",
        )
        parameterized = _request(
            server,
            "POST",
            root + "?endpoint=https://evil.test",
            payload=body,
        )
        unsupported = _request(
            server,
            "PATCH",
            root,
            content_type=None,
            origin=None,
        )
        server.draft_poc_service.archive(poc_id)
        archived_read = _request(
            server,
            "GET",
            root + "/latest",
            content_type=None,
            origin=None,
        )
        archived_write = _request(
            server,
            "POST",
            root,
            payload=body,
        )

    assert before_freeze[:2] == (
        409,
        {"error": "Performance run conflicts with current POC state."},
    )
    assert proof_before_freeze[:2] == (
        409,
        {"error": "Performance proof requires a confirmed frozen agreement."},
    )
    assert wrong_media[:2] == (
        415,
        {"error": "Content-Type must be application/json."},
    )
    assert missing_origin[:2] == (
        403,
        {"error": "Origin is not allowed."},
    )
    invalid = {"error": "Performance run request is invalid."}
    assert header_authority[:2] == duplicate_json[:2] == (400, invalid)
    assert parameterized[:2] == (400, invalid)
    assert too_large[:2] == (
        413,
        {"error": "Performance run request is too large."},
    )
    assert unsupported[:2] == (
        405,
        {"error": "Performance run method is not allowed."},
    )
    assert archived_read[:2] == archived_write[:2] == (
        404,
        {"error": "Performance run was not found."},
    )


@pytest.mark.parametrize(
    "closure_decision",
    ("HANDOFF_COMPLETED", "POC_STOPPED"),
)
def test_closed_dynamic_poc_rejects_every_lifecycle_mutation_atomically(
    tmp_path,
    closure_decision,
):
    with _streaming_endpoint() as (endpoint, _):
        with _running_server(tmp_path) as server:
            poc_id = _create_defined_performance_poc(server)
            _freeze_agreement(server, poc_id, endpoint)
            latest = _start_and_wait_for_terminal_run(
                server,
                poc_id,
                idempotency_key="run-before-terminal-closure",
            )
            assert latest["status"] == "COMPLETED"

            definitions = _request(
                server,
                "GET",
                f"/api/pocs/{poc_id}/definitions",
                content_type=None,
                origin=None,
            )
            assert definitions[0] == 200
            first_proposal_id = definitions[1]["proposals"][0]["proposal_id"]

            closure_root = f"/api/workspace/pocs/{poc_id}/closure"
            eligibility = _request(
                server,
                "GET",
                closure_root,
                content_type=None,
                origin=None,
            )
            assert eligibility[0] == 200
            binding = eligibility[1]["eligible_evidence_binding"]
            assert binding is not None
            closed = _request(
                server,
                "POST",
                closure_root,
                payload={
                    "decision": closure_decision,
                    "decided_by": "field_engineer",
                    "rationale": "Record the terminal POC lifecycle decision.",
                    "evidence_binding": binding,
                    "idempotency_key": (
                        f"close-before-mutation-{closure_decision.lower()}"
                    ),
                },
            )
            assert closed[0] == 201
            before = _dynamic_lifecycle_state(server, poc_id)

            attempts = {
                "source": _request(
                    server,
                    "POST",
                    f"/api/pocs/{poc_id}/sources/document",
                    payload={
                        "document_text": (
                            "Throughput must remain above 20 tokens per second."
                        ),
                        "idempotency_key": "source-after-terminal-closure",
                    },
                ),
                "proposal_review": _request(
                    server,
                    "POST",
                    (
                        f"/api/pocs/{poc_id}/proposals/"
                        f"{first_proposal_id}/decision"
                    ),
                    payload={
                        "decision": "KEEP_FOR_CONTRACT",
                        "reviewer": "Jayesh",
                        "rationale": (
                            "Keep this measurable customer requirement."
                        ),
                        "idempotency_key": "keep-agreement-0",
                    },
                ),
                "definition": _request(
                    server,
                    "POST",
                    f"/api/pocs/{poc_id}/definitions",
                    payload={
                        "proposal_id": first_proposal_id,
                        "metric": "TTFT_P95_MS",
                        "operator": "LT",
                        "threshold": 500,
                        "minimum_samples": 100,
                        "concurrency": 4,
                        "prompt_tokens_min": 512,
                        "prompt_tokens_max": 4096,
                        "output_tokens_min": 64,
                        "output_tokens_max": 512,
                        "reviewer": "Jayesh",
                        "rationale": (
                            "This exact rule defines POC acceptance."
                        ),
                        "idempotency_key": "define-agreement-0",
                    },
                ),
                "agreement_prepare": _request(
                    server,
                    "POST",
                    f"/api/pocs/{poc_id}/agreement",
                    payload=_prepare_payload_for(endpoint),
                ),
                "agreement_confirm": _request(
                    server,
                    "POST",
                    f"/api/pocs/{poc_id}/agreement/confirm",
                    payload={
                        "confirmer": "Customer contact recorded by Jayesh",
                        "agreement_acknowledged": True,
                        "rationale": (
                            "The exact target and both criteria were reviewed."
                        ),
                        "idempotency_key": "confirm-dynamic-run-transport",
                    },
                ),
                "agreement_freeze": _request(
                    server,
                    "POST",
                    f"/api/pocs/{poc_id}/agreement/freeze",
                    payload={
                        "idempotency_key": "freeze-dynamic-run-transport"
                    },
                ),
                "run": _request(
                    server,
                    "POST",
                    f"/api/pocs/{poc_id}/runs",
                    payload={
                        "execution_acknowledged": True,
                        "idempotency_key": "run-before-terminal-closure",
                    },
                ),
            }
            after = _dynamic_lifecycle_state(server, poc_id)

    assert attempts["agreement_confirm"][:2] == (
        400,
        {"error": "Performance agreement request is invalid."},
    )
    mutable_attempts = {
        name: response
        for name, response in attempts.items()
        if name != "agreement_confirm"
    }
    assert {name: response[0] for name, response in mutable_attempts.items()} == {
        name: 409 for name in mutable_attempts
    }
    assert {
        response[1]["code"] for response in mutable_attempts.values()
    } == {"POC_LIFECYCLE_CLOSED"}
    assert after == before


def test_blocked_terminal_run_can_be_stopped_but_cannot_be_handed_off(
    tmp_path,
):
    with _unready_endpoint() as endpoint:
        with _running_server(tmp_path) as server:
            poc_id = _create_defined_performance_poc(server)
            _freeze_agreement(server, poc_id, endpoint)
            latest = _start_and_wait_for_terminal_run(
                server,
                poc_id,
                idempotency_key="run-blocked-before-stop",
            )
            assert latest["status"] == "BLOCKED"
            assert latest["operation_id"] is not None
            assert latest["evidence_pack_url"] is None

            operation_route = (
                f"/api/pocs/{poc_id}/runs/{latest['operation_id']}"
            )
            immutable_receipt = _request(
                server,
                "GET",
                operation_route,
                content_type=None,
                origin=None,
            )
            assert immutable_receipt[:2] == (200, latest)

            closure_root = f"/api/workspace/pocs/{poc_id}/closure"
            eligibility = _request(
                server,
                "GET",
                closure_root,
                content_type=None,
                origin=None,
            )
            assert eligibility[0] == 200
            terminal_run_binding = eligibility[1][
                "eligible_terminal_run_binding"
            ]
            assert eligibility[1]["eligible_evidence_binding"] is None
            assert eligibility[1]["allowed_decisions"] == ["POC_STOPPED"]
            assert terminal_run_binding is not None
            assert (
                terminal_run_binding["operation_id"]
                == latest["operation_id"]
            )
            assert terminal_run_binding["run_status"] == "BLOCKED"
            assert len(terminal_run_binding["run_receipt_sha256"]) == 64
            assert "evidence_pack_url" not in terminal_run_binding
            assert "evidence_pack_sha256" not in terminal_run_binding
            handoff = _request(
                server,
                "POST",
                closure_root,
                payload={
                    "decision": "HANDOFF_COMPLETED",
                    "decided_by": "field_engineer",
                    "rationale": "A blocked run has no Evidence Pack to hand off.",
                    "terminal_run_binding": terminal_run_binding,
                    "idempotency_key": "reject-blocked-handoff",
                },
            )
            after_handoff = _request(
                server,
                "GET",
                closure_root,
                content_type=None,
                origin=None,
            )
            stopped = _request(
                server,
                "POST",
                closure_root,
                payload={
                    "decision": "POC_STOPPED",
                    "decided_by": "field_engineer",
                    "rationale": (
                        "Stop after reviewing the immutable blocked-run receipt."
                    ),
                    "terminal_run_binding": terminal_run_binding,
                    "idempotency_key": "stop-blocked-performance-poc",
                },
            )
            after_stop = _request(
                server,
                "GET",
                closure_root,
                content_type=None,
                origin=None,
            )
            receipt_after_stop = _request(
                server,
                "GET",
                operation_route,
                content_type=None,
                origin=None,
            )

    assert handoff[0] == 409
    assert after_handoff[0] == 200
    assert after_handoff[1]["closure"] is None
    assert stopped[0] == 201
    assert stopped[1]["closure"]["decision"] == "POC_STOPPED"
    assert stopped[1]["closure"]["evidence_binding"] is None
    assert (
        stopped[1]["closure"]["terminal_run_binding"]
        == terminal_run_binding
    )
    assert after_stop[1]["closure"] == stopped[1]["closure"]
    assert receipt_after_stop[:2] == immutable_receipt[:2]
