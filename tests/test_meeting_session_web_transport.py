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
        assert not worker.is_alive()


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
) -> tuple[int, dict]:
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
        connection.request(
            method,
            target,
            body=body,
            headers=request_headers,
        )
        response = connection.getresponse()
        return response.status, json.loads(response.read().decode("utf-8"))
    finally:
        connection.close()


def _create_draft(server: ExitSpecDemoServer) -> str:
    status, payload = _request(
        server,
        "POST",
        "/api/pocs",
        payload={
            "display_name": "Guided meeting POC",
            "customer_label": "Northstar",
            "use_case": "Draft measurable requirements during a call.",
            "owner": "field_engineer",
            "first_source_choice": "MEETING",
            "idempotency_key": "create-meeting-transport",
        },
    )
    assert status == 201
    return payload["poc_id"]


def _create_session(server: ExitSpecDemoServer, poc_id: str):
    return _request(
        server,
        "POST",
        f"/api/pocs/{poc_id}/meeting-sessions",
        payload={"idempotency_key": "meeting-transport-create"},
    )


def test_http_flow_reaches_existing_source_and_human_review_queue(tmp_path):
    with _running_server(tmp_path) as server:
        poc_id = _create_draft(server)
        disclosure = _request(
            server,
            "GET",
            f"/api/pocs/{poc_id}/meeting-sessions/disclosure",
            content_type=None,
            origin=None,
        )
        created = _create_session(server, poc_id)
        session_id = created[1]["session"]["session_id"]
        consented = _request(
            server,
            "POST",
            f"/api/pocs/{poc_id}/meeting-sessions/{session_id}/consent",
            payload={
                "all_participants_consented": True,
                "disclosure_id": "meeting_synthetic_disclosure_v1",
                "idempotency_key": "meeting-transport-consent",
                "recording_notice_acknowledged": True,
                "synthetic_demo_acknowledged": True,
            },
        )
        started = _request(
            server,
            "POST",
            f"/api/pocs/{poc_id}/meeting-sessions/{session_id}/start",
            payload={"idempotency_key": "meeting-transport-start"},
        )
        drafted = _request(
            server,
            "POST",
            f"/api/pocs/{poc_id}/meeting-sessions/{session_id}/draft",
            payload={"idempotency_key": "meeting-transport-draft"},
        )
        sources = _request(
            server,
            "GET",
            f"/api/pocs/{poc_id}/sources",
            content_type=None,
            origin=None,
        )
        proposals = _request(
            server,
            "GET",
            f"/api/pocs/{poc_id}/proposals",
            content_type=None,
            origin=None,
        )

    assert disclosure[0] == 200
    assert disclosure[1]["adapter"]["provider"] == "exitspec.synthetic"
    assert disclosure[1]["adapter"]["provider_connected"] is False
    assert created[0] == consented[0] == started[0] == drafted[0] == 201
    assert drafted[1]["session"]["state"] == "DRAFT_READY"
    assert drafted[1]["session"]["review_state"] == "NEEDS_REVIEW"
    assert drafted[1]["session"]["proposal_count"] == 2
    assert sources[0] == proposals[0] == 200
    assert len(sources[1]["sources"]) == 1
    assert len(proposals[1]["proposals"]) == 2
    assert all(
        item["review_state"] == "NEEDS_REVIEW"
        for item in proposals[1]["proposals"]
    )
    serialized = json.dumps(
        [disclosure[1], created[1], consented[1], started[1], drafted[1]]
    )
    assert "transcript_text" not in serialized
    assert '"meeting_id"' not in serialized
    assert "participant_synthetic_" not in serialized
    assert '"provider_connected": true' not in serialized


@pytest.mark.parametrize(
    ("content_type", "origin", "expected"),
    (
        (
            "text/plain",
            "same",
            (415, {"error": "Content-Type must be application/json."}),
        ),
        (
            None,
            "same",
            (415, {"error": "Content-Type must be application/json."}),
        ),
        (
            "application/json",
            None,
            (403, {"error": "Origin is not allowed."}),
        ),
        (
            "application/json",
            "https://evil.test",
            (403, {"error": "Origin is not allowed."}),
        ),
    ),
)
def test_writes_require_json_and_exact_same_origin(
    tmp_path,
    content_type,
    origin,
    expected,
):
    with _running_server(tmp_path) as server:
        poc_id = _create_draft(server)
        response = _request(
            server,
            "POST",
            f"/api/pocs/{poc_id}/meeting-sessions",
            payload={"idempotency_key": "meeting-transport-gate"},
            content_type=content_type,
            origin=origin,
        )

    assert response == expected


def test_duplicate_json_header_idempotency_and_authority_fail_closed(tmp_path):
    with _running_server(tmp_path) as server:
        poc_id = _create_draft(server)
        target = f"/api/pocs/{poc_id}/meeting-sessions"
        duplicate = _request(
            server,
            "POST",
            target,
            raw_body=(
                b'{"idempotency_key":"meeting-duplicate-one",'
                b'"idempotency_key":"meeting-duplicate-two"}'
            ),
        )
        header = _request(
            server,
            "POST",
            target,
            payload={"idempotency_key": "meeting-header-body"},
            headers={"Idempotency-Key": "meeting-header-control"},
        )
        authority = _request(
            server,
            "POST",
            target,
            payload={
                "idempotency_key": "meeting-authority-field",
                "meeting_id": "browser-controlled",
                "may_freeze_contract": True,
                "transcript": "private marker",
            },
        )
        current = _request(
            server,
            "GET",
            f"{target}/current",
            content_type=None,
            origin=None,
        )

    expected = (400, {"error": "Meeting session request is invalid."})
    assert duplicate == header == authority == expected
    assert current[0] == 404
    assert "private marker" not in json.dumps(authority)


def test_oversize_parameters_and_arbitrary_methods_fail_closed(tmp_path):
    with _running_server(tmp_path) as server:
        poc_id = _create_draft(server)
        target = f"/api/pocs/{poc_id}/meeting-sessions"
        oversized = _request(
            server,
            "POST",
            target,
            raw_body=b"{" + b"x" * MAX_REQUEST_BYTES + b"}",
        )
        query = _request(
            server,
            "POST",
            target + "?provider=zoom",
            payload={"idempotency_key": "meeting-query-route"},
        )
        unsupported = _request(
            server,
            "DELETE",
            target + "/disclosure",
            content_type=None,
            origin=None,
        )
        trailing = _request(
            server,
            "GET",
            target + "/disclosure/",
            content_type=None,
            origin=None,
        )
        doubled = _request(
            server,
            "GET",
            target + "//disclosure",
            content_type=None,
            origin=None,
        )

    assert oversized == (
        413,
        {"error": "Meeting session request is too large."},
    )
    assert query == (
        400,
        {"error": "Meeting session request is invalid."},
    )
    assert unsupported == (
        405,
        {"error": "Meeting session method is not allowed."},
    )
    assert trailing == doubled == (
        400,
        {"error": "Meeting session request is invalid."},
    )


def test_archived_draft_refuses_mutation_but_preserves_safe_read(tmp_path):
    with _running_server(tmp_path) as server:
        poc_id = _create_draft(server)
        created = _create_session(server, poc_id)
        session_id = created[1]["session"]["session_id"]
        server.draft_poc_service.archive(poc_id)
        rejected = _request(
            server,
            "POST",
            f"/api/pocs/{poc_id}/meeting-sessions/{session_id}/consent",
            payload={
                "all_participants_consented": True,
                "disclosure_id": "meeting_synthetic_disclosure_v1",
                "idempotency_key": "meeting-archived-consent",
                "recording_notice_acknowledged": True,
                "synthetic_demo_acknowledged": True,
            },
        )
        recovered = _request(
            server,
            "GET",
            f"/api/pocs/{poc_id}/meeting-sessions/{session_id}",
            content_type=None,
            origin=None,
        )

    assert rejected[0] == 404
    assert rejected[1]["code"] == "MEETING_SESSION_DRAFT_UNAVAILABLE"
    assert recovered[0] == 200
    assert recovered[1]["state"] == "SETUP"
