from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from http.client import HTTPConnection
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
    return {
        "target_provider": "Local vLLM",
        "endpoint_class": "OpenAI-compatible chat completions",
        "endpoint": "http://127.0.0.1:8000/v1/chat/completions",
        "model": "Qwen/Qwen2.5-0.5B-Instruct",
        "reviewer": "Jayesh",
        "rationale": "Bind the reviewed requirements to this exact target.",
        "idempotency_key": "prepare-performance-agreement",
    }


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
