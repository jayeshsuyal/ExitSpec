from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from http.client import HTTPConnection
from pathlib import Path

import pytest

from exitspec.web import (
    MAX_REQUEST_BYTES,
    DemoSession,
    ExitSpecDemoServer,
)


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
            "display_name": "Inference validation",
            "customer_label": "Northstar",
            "use_case": "Validate one bounded performance claim.",
            "owner": "field_engineer",
            "first_source_choice": "DOCUMENT",
            "idempotency_key": "create-source-transport",
        },
    )
    assert status == 201
    return payload["poc_id"]


def test_create_capture_list_is_one_real_process_local_round_trip(tmp_path):
    with _running_server(tmp_path) as server:
        poc_id = _create_draft(server)
        root = f"/api/pocs/{poc_id}/sources"
        created_status, created = _request(
            server,
            "POST",
            root + "/document",
            payload={
                "document_text": "The p95 latency must stay below 500 ms.",
                "idempotency_key": "capture-source-transport",
            },
        )
        list_status, listed = _request(
            server,
            "GET",
            root,
            content_type=None,
            origin=None,
        )

    assert created_status == 201
    assert list_status == 200
    assert created["status"] == "NEEDS_REVIEW"
    assert created["proposal_count"] == 1
    assert listed == {
        "poc_id": poc_id,
        "sources": [{**created, "idempotent_replay": False}],
    }
    serialized = json.dumps((created, listed)).lower()
    assert "p95 latency must" not in serialized
    for forbidden in ("approved", "confirmation", "freeze", "verdict", "pass"):
        assert forbidden not in serialized


def test_transport_replay_does_not_create_a_second_source(tmp_path):
    with _running_server(tmp_path) as server:
        poc_id = _create_draft(server)
        root = f"/api/pocs/{poc_id}/sources"
        payload = {
            "document_text": "Error rate must remain below 1%.",
            "idempotency_key": "capture-source-replay",
        }
        first = _request(server, "POST", root + "/document", payload=payload)
        replay = _request(server, "POST", root + "/document", payload=payload)
        listed = _request(
            server,
            "GET",
            root,
            content_type=None,
            origin=None,
        )

    assert first[0] == 201
    assert replay[0] == 200
    assert replay[1] == {**first[1], "idempotent_replay": True}
    assert len(listed[1]["sources"]) == 1


@pytest.mark.parametrize(
    ("content_type", "origin", "expected_status", "message"),
    (
        (
            "text/plain",
            "same",
            415,
            "Content-Type must be application/json.",
        ),
        (
            None,
            "same",
            415,
            "Content-Type must be application/json.",
        ),
        (
            "application/json",
            None,
            403,
            "Origin is not allowed.",
        ),
        (
            "application/json",
            "https://evil.test",
            403,
            "Origin is not allowed.",
        ),
    ),
)
def test_source_writes_require_json_and_exact_origin(
    tmp_path,
    content_type,
    origin,
    expected_status,
    message,
):
    with _running_server(tmp_path) as server:
        poc_id = _create_draft(server)
        status, response = _request(
            server,
            "POST",
            f"/api/pocs/{poc_id}/sources/document",
            payload={
                "document_text": "Requirement must be reviewed.",
                "idempotency_key": "transport-gate",
            },
            content_type=content_type,
            origin=origin,
        )

    assert status == expected_status
    assert response == {"error": message}


def test_header_idempotency_and_authority_fields_are_rejected(tmp_path):
    with _running_server(tmp_path) as server:
        poc_id = _create_draft(server)
        target = f"/api/pocs/{poc_id}/sources/document"
        header_status, header_body = _request(
            server,
            "POST",
            target,
            payload={
                "document_text": "Requirement must be reviewed.",
                "idempotency_key": "body-key",
            },
            headers={"Idempotency-Key": "header-key"},
        )
        authority_status, authority_body = _request(
            server,
            "POST",
            target,
            payload={
                "document_text": "Requirement must be reviewed.",
                "idempotency_key": "authority-key",
                "approve": True,
            },
        )

    assert header_status == authority_status == 400
    assert header_body == authority_body == {
        "error": "Source intake request is invalid."
    }


@pytest.mark.parametrize(
    "raw_body",
    (
        b'{"document_text":"one","document_text":"two",'
        b'"idempotency_key":"duplicate"}',
        b'{"document_text":"one","idempotency_key":"key",'
        b'"nested":{"approve":false,"approve":true}}',
        b'{"document_text":"one","idempotency_key":"key","number":NaN}',
        b'{"document_text":"raw-secret-marker"',
    ),
)
def test_malformed_duplicate_and_nonfinite_json_fail_without_reflection(
    tmp_path,
    raw_body,
):
    with _running_server(tmp_path) as server:
        poc_id = _create_draft(server)
        status, response = _request(
            server,
            "POST",
            f"/api/pocs/{poc_id}/sources/document",
            raw_body=raw_body,
        )
        listed = _request(
            server,
            "GET",
            f"/api/pocs/{poc_id}/sources",
            content_type=None,
            origin=None,
        )

    assert status == 400
    assert response == {"error": "Source intake request is invalid."}
    assert "raw-secret-marker" not in json.dumps(response)
    assert listed[1]["sources"] == []


def test_oversized_body_is_413_and_does_not_mutate(tmp_path):
    with _running_server(tmp_path) as server:
        poc_id = _create_draft(server)
        status, response = _request(
            server,
            "POST",
            f"/api/pocs/{poc_id}/sources/document",
            raw_body=b"{" + b"x" * MAX_REQUEST_BYTES + b"}",
        )
        listed = _request(
            server,
            "GET",
            f"/api/pocs/{poc_id}/sources",
            content_type=None,
            origin=None,
        )

    assert status == 413
    assert response == {"error": "Source intake request is too large."}
    assert listed[1]["sources"] == []


def test_route_parameters_and_unsupported_method_fail_closed(tmp_path):
    with _running_server(tmp_path) as server:
        poc_id = _create_draft(server)
        root = f"/api/pocs/{poc_id}/sources"
        query = _request(
            server,
            "POST",
            root + "/document?approve=true",
            payload={
                "document_text": "Requirement must be reviewed.",
                "idempotency_key": "query-key",
            },
        )
        unsupported = _request(
            server,
            "DELETE",
            root,
            payload=None,
            content_type=None,
            origin=None,
        )

    assert query == (400, {"error": "Source intake request is invalid."})
    assert unsupported == (
        405,
        {"error": "Source intake method is not allowed."},
    )
