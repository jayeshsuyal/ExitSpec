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


def _create_kept_proposals(
    server: ExitSpecDemoServer,
) -> tuple[str, list[dict]]:
    create_status, draft, _ = _request(
        server,
        "POST",
        "/api/pocs",
        payload={
            "display_name": "Inference validation",
            "customer_label": "Northstar",
            "use_case": "Validate two bounded performance claims.",
            "owner": "field_engineer",
            "first_source_choice": "DOCUMENT",
            "idempotency_key": "create-definition-transport",
        },
    )
    assert create_status == 201
    assert isinstance(draft, dict)
    poc_id = str(draft["poc_id"])

    capture_status, captured, _ = _request(
        server,
        "POST",
        f"/api/pocs/{poc_id}/sources/document",
        payload={
            "document_text": (
                "The p95 time to first token must stay below 500 ms. "
                "Error rate must remain below 1%."
            ),
            "idempotency_key": "capture-definition-transport",
        },
    )
    assert capture_status == 201
    assert isinstance(captured, dict)
    assert captured["proposal_count"] == 2

    list_status, proposal_list, _ = _request(
        server,
        "GET",
        f"/api/pocs/{poc_id}/proposals",
        content_type=None,
        origin=None,
    )
    assert list_status == 200
    assert isinstance(proposal_list, dict)
    proposals = proposal_list["proposals"]
    assert len(proposals) == 2
    for index, proposal in enumerate(proposals):
        decision_status, _, _ = _request(
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
                "idempotency_key": f"keep-definition-{index}",
            },
        )
        assert decision_status == 201
    return poc_id, proposals


def _definition_payload(
    proposal_id: str,
    *,
    metric: str = "TTFT_P95_MS",
    threshold: float = 500,
    idempotency_key: str = "define-performance-criterion",
) -> dict:
    return {
        "proposal_id": proposal_id,
        "metric": metric,
        "operator": "LTE",
        "threshold": threshold,
        "minimum_samples": 100,
        "concurrency": 4,
        "prompt_tokens_min": 512,
        "prompt_tokens_max": 4096,
        "output_tokens_min": 64,
        "output_tokens_max": 512,
        "reviewer": "Jayesh",
        "rationale": "This is the bounded acceptance test agreed for the POC.",
        "idempotency_key": idempotency_key,
    }


def test_kept_proposals_become_two_real_immutable_definitions(tmp_path):
    with _running_server(tmp_path) as server:
        poc_id, proposals = _create_kept_proposals(server)
        root = f"/api/pocs/{poc_id}/definitions"
        before = _request(
            server,
            "GET",
            root,
            content_type=None,
            origin=None,
        )
        workspace_before = _request(
            server,
            "GET",
            "/api/workspace",
            content_type=None,
            origin=None,
        )
        first_payload = _definition_payload(proposals[0]["proposal_id"])
        first = _request(server, "POST", root, payload=first_payload)
        replay = _request(server, "POST", root, payload=first_payload)
        second = _request(
            server,
            "POST",
            root,
            payload=_definition_payload(
                proposals[1]["proposal_id"],
                metric="ERROR_RATE_PERCENT",
                threshold=1,
                idempotency_key="define-error-rate-criterion",
            ),
        )
        after = _request(
            server,
            "GET",
            root,
            content_type=None,
            origin=None,
        )
        workspace_after = _request(
            server,
            "GET",
            "/api/workspace",
            content_type=None,
            origin=None,
        )

    assert before[0] == 200
    assert all(item["definition"] is None for item in before[1]["proposals"])
    assert workspace_before[0] == 200
    assert (
        workspace_before[1]["continue_working"]["next_action_code"]
        == "DEFINE_CRITERIA"
    )
    assert first[0] == 201
    assert first[1]["disposition"] == "CREATED"
    assert replay[0] == 200
    assert replay[1]["disposition"] == "IDEMPOTENT_REPLAY"
    assert replay[1]["definition"] == first[1]["definition"]
    assert second[0] == 201
    assert after[0] == 200
    definitions = [item["definition"] for item in after[1]["proposals"]]
    assert [definition["metric"] for definition in definitions] == [
        "TTFT_P95_MS",
        "ERROR_RATE_PERCENT",
    ]
    assert all(definition["operator"] == "LTE" for definition in definitions)
    assert workspace_after[0] == 200
    assert (
        workspace_after[1]["continue_working"]["next_action_code"]
        == "PREPARE_AGREEMENT"
    )
    assert workspace_after[1]["continue_working"]["next_human_action"] == (
        "Prepare an agreement from 2 defined acceptance criteria."
    )
    serialized = json.dumps((first, second, after)).lower()
    for forbidden in ("approved", "confirmation", "freeze", "verdict", "pass"):
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
def test_definition_writes_require_json_and_exact_same_origin(
    tmp_path,
    content_type,
    origin,
    expected_status,
    message,
):
    with _running_server(tmp_path) as server:
        poc_id, proposals = _create_kept_proposals(server)
        response = _request(
            server,
            "POST",
            f"/api/pocs/{poc_id}/definitions",
            payload=_definition_payload(proposals[0]["proposal_id"]),
            content_type=content_type,
            origin=origin,
        )

    assert response[:2] == (expected_status, {"error": message})


def test_definition_transport_rejects_header_authority_and_unsafe_json(
    tmp_path,
):
    with _running_server(tmp_path) as server:
        poc_id, proposals = _create_kept_proposals(server)
        root = f"/api/pocs/{poc_id}/definitions"
        valid = _definition_payload(proposals[0]["proposal_id"])
        header_authority = _request(
            server,
            "POST",
            root,
            payload=valid,
            headers={"Idempotency-Key": "must-not-control-definition"},
        )
        duplicated = json.dumps(valid).replace(
            '"metric": "TTFT_P95_MS"',
            '"metric": "TTFT_P95_MS", "metric": "ERROR_RATE_PERCENT"',
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

    assert header_authority[:2] == duplicate_json[:2] == (
        400,
        {"error": "Contract definition request is invalid."},
    )
    assert too_large[:2] == (
        413,
        {"error": "Contract definition request is too large."},
    )


def test_definition_page_assets_and_routes_are_exact(tmp_path):
    with _running_server(tmp_path) as server:
        poc_id, _ = _create_kept_proposals(server)
        page = _request(
            server,
            "GET",
            f"/app/pocs/{poc_id}/define",
            content_type=None,
            origin=None,
        )
        css = _request(
            server,
            "GET",
            "/contract_definition.css",
            content_type=None,
            origin=None,
        )
        javascript = _request(
            server,
            "GET",
            "/contract_definition.js",
            content_type=None,
            origin=None,
        )
        parameterized = _request(
            server,
            "GET",
            f"/app/pocs/{poc_id}/define?next=freeze",
            content_type=None,
            origin=None,
        )

    assert page[0] == css[0] == javascript[0] == 200
    assert page[2].startswith("text/html")
    assert css[2].startswith("text/css")
    assert "javascript" in javascript[2]
    assert parameterized[:2] == (
        400,
        {"error": "Draft POC routes do not accept URL parameters."},
    )


def test_archived_poc_refuses_definition_page_reads_and_writes(tmp_path):
    with _running_server(tmp_path) as server:
        poc_id, proposals = _create_kept_proposals(server)
        server.draft_poc_service.archive(poc_id)
        root = f"/api/pocs/{poc_id}/definitions"
        page = _request(
            server,
            "GET",
            f"/app/pocs/{poc_id}/define",
            content_type=None,
            origin=None,
        )
        listed = _request(
            server,
            "GET",
            root,
            content_type=None,
            origin=None,
        )
        written = _request(
            server,
            "POST",
            root,
            payload=_definition_payload(proposals[0]["proposal_id"]),
        )

    assert page[:2] == (
        404,
        {"error": "Draft POC was not found in this local process."},
    )
    assert listed[:2] == written[:2] == (
        404,
        {"error": "Contract definition was not found."},
    )


def test_definition_namespace_returns_json_for_unsupported_methods(tmp_path):
    with _running_server(tmp_path) as server:
        poc_id, _ = _create_kept_proposals(server)
        response = _request(
            server,
            "PATCH",
            f"/api/pocs/{poc_id}/definitions",
            content_type=None,
            origin=None,
        )

    assert response[:2] == (
        405,
        {"error": "Contract definition method is not allowed."},
    )
