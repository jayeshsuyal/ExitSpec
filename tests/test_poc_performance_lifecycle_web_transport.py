from __future__ import annotations

import json
import threading
import time
from contextlib import contextmanager
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

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
    confirmed = _request(
        server,
        "POST",
        root + "/confirm",
        payload={
            "confirmer": "Customer contact recorded by Jayesh",
            "agreement_acknowledged": True,
            "rationale": "The exact target and both criteria were reviewed.",
            "idempotency_key": "confirm-dynamic-run-transport",
        },
    )
    assert confirmed[0] == 201
    frozen = _request(
        server,
        "POST",
        root + "/freeze",
        payload={"idempotency_key": "freeze-dynamic-run-transport"},
    )
    assert frozen[0] == 201
    return str(frozen[1]["frozen_contract"]["canonical_hash"])


def test_agreement_page_and_api_complete_prepare_confirm_freeze_loop(tmp_path):
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

        confirmed = _request(
            server,
            "POST",
            root + "/confirm",
            payload={
                "confirmer": "Customer contact recorded by Jayesh",
                "agreement_acknowledged": True,
                "rationale": "The exact target and both criteria were reviewed.",
                "idempotency_key": "confirm-performance-agreement",
            },
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
    assert prepared_action == "CREATE_CUSTOMER_REVIEW"
    assert confirmed[0] == 201
    assert confirmed[1]["confirmation"]["agreement_acknowledged"] is True
    assert confirmed_action == "FREEZE_CONFIRMED_CONTRACT"
    assert frozen[0] == 201
    assert frozen[1]["frozen_contract"]["canonical_hash"]
    assert frozen_action == "RUN_POC"
    assert after[1]["draft"] == prepared[1]["draft"]
    assert after[1]["confirmation"] == confirmed[1]["confirmation"]
    assert after[1]["frozen_contract"] == frozen[1]["frozen_contract"]
    serialized = json.dumps((prepared, confirmed, frozen, after)).lower()
    for forbidden in ("evidence_pack", '"verdict"', '"pass"', '"fail"'):
        assert forbidden not in serialized


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
    assert projected["next_action_code"] == "REVIEW_EVIDENCE"
    assert projected["latest_evidence_summary"]["status"] == "PASS"
    assert projected["latest_evidence_summary"]["report_url"] == (
        latest[1]["evidence_pack_url"]
    )


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
