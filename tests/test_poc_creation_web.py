import json
import threading
from contextlib import contextmanager
from http.client import HTTPConnection
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest

from exitspec.web import DemoSession, ExitSpecDemoServer


@contextmanager
def _running_server(tmp_path: Path):
    session = DemoSession.synthetic_support_agent(output_root=tmp_path / "runs")
    server = ExitSpecDemoServer(("127.0.0.1", 0), session)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield (
            server,
            "http://127.0.0.1:{0}".format(server.server_port),
        )
    finally:
        server.shutdown()
        worker.join(timeout=5)
        server.server_close()


def _request(
    server: ExitSpecDemoServer,
    method: str,
    target: str,
    *,
    payload: dict | None = None,
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
        response_body = response.read()
        return response.status, json.loads(response_body.decode("utf-8"))
    finally:
        connection.close()


def _valid_payload(
    *,
    key: str = "create-poc-1",
    source: str = "EMAIL",
) -> dict:
    return {
        "display_name": "Support automation POC",
        "customer_label": "Northstar",
        "use_case": "Validate customer support tool selection.",
        "owner": "Jayesh",
        "first_source_choice": source,
        "idempotency_key": key,
    }


def test_new_poc_page_and_dashboard_action_are_real_local_routes(tmp_path):
    with _running_server(tmp_path) as (_, base_url):
        with urlopen(base_url + "/app", timeout=5) as response:
            dashboard = response.read()
        with urlopen(base_url + "/app/pocs/new", timeout=5) as response:
            page = response.read()
        with urlopen(base_url + "/new_poc.css", timeout=5) as response:
            stylesheet = response.read()
        with urlopen(base_url + "/new_poc.js", timeout=5) as response:
            javascript = response.read()

    assert b'href="/app/pocs/new"' in dashboard
    assert b'id="new-poc-main"' in page
    assert b"How are the requirements arriving?" in page
    assert b"Email" in page
    assert b"Meeting" in page
    assert b"Notes or document" in page
    assert b"Existing ExitSpec contract" in page
    assert b"Draft only" in page
    assert stylesheet
    assert b'const CREATE_API = "/api/pocs";' in javascript
    assert b"Create draft POC" in page
    assert b"Zoom" not in page
    assert b"STT" not in page


@pytest.mark.parametrize(
    ("source", "next_route"),
    (
        ("EMAIL", "email"),
        ("MEETING", "meeting"),
        ("DOCUMENT", "document"),
        ("EXISTING_CONTRACT", "existing_contract"),
    ),
)
def test_post_creates_identity_only_and_get_is_read_only(
    tmp_path,
    source,
    next_route,
):
    with _running_server(tmp_path) as (server, _):
        before = _request(server, "GET", "/api/state")[1]
        status, created = _request(
            server,
            "POST",
            "/api/pocs",
            payload=_valid_payload(source=source),
        )
        after_create = _request(server, "GET", "/api/state")[1]
        get_status, fetched = _request(
            server,
            "GET",
            "/api/pocs/{0}".format(created["poc_id"]),
        )
        after_get = _request(server, "GET", "/api/state")[1]

    assert status == 201
    assert get_status == 200
    assert before == after_create == after_get
    assert created == {**fetched, "idempotent_replay": False}
    assert created["first_source_choice"] == source
    assert created["next_intake_route"] == next_route
    assert created["source_ingestion_state"] == "NOT_STARTED"
    assert created["archive_state"] == "ACTIVE"
    assert created["archived_at"] is None
    assert set(created) == {
        "poc_id",
        "display_name",
        "customer_label",
        "use_case",
        "owner",
        "first_source_choice",
        "next_intake_route",
        "source_ingestion_state",
        "created_at",
        "updated_at",
        "archive_state",
        "archived_at",
        "idempotent_replay",
    }
    serialized = json.dumps(created).lower()
    for forbidden in (
        "approved",
        "confirmation",
        "frozen",
        "execution",
        "evidence",
        "verdict",
        "pass",
        "fail",
    ):
        assert forbidden not in serialized


def test_exact_retry_replays_one_process_owned_draft(tmp_path):
    payload = _valid_payload()
    with _running_server(tmp_path) as (server, _):
        first_status, first = _request(
            server,
            "POST",
            "/api/pocs",
            payload=payload,
        )
        second_status, second = _request(
            server,
            "POST",
            "/api/pocs",
            payload=payload,
        )

        assert len(server.draft_poc_service) == 1

    assert first_status == 201
    assert second_status == 200
    assert first["poc_id"] == second["poc_id"]
    assert first["created_at"] == second["created_at"]
    assert first["idempotent_replay"] is False
    assert second["idempotent_replay"] is True


def test_conflicting_idempotency_key_fails_without_duplicate(tmp_path):
    with _running_server(tmp_path) as (server, _):
        _request(
            server,
            "POST",
            "/api/pocs",
            payload=_valid_payload(),
        )
        conflict_payload = _valid_payload()
        conflict_payload["customer_label"] = "Different customer"
        status, body = _request(
            server,
            "POST",
            "/api/pocs",
            payload=conflict_payload,
        )

        assert len(server.draft_poc_service) == 1

    assert status == 409
    assert body == {
        "error": (
            "That idempotency key is already bound to a different draft POC "
            "request."
        )
    }
    assert "Different customer" not in json.dumps(body)


@pytest.mark.parametrize(
    ("mutation", "expected_status"),
    (
        (lambda payload: payload.update({"approve": True}), 400),
        (lambda payload: payload.pop("display_name"), 400),
        (lambda payload: payload.update({"first_source_choice": "ZOOM"}), 400),
        (lambda payload: payload.update({"display_name": " "}), 400),
        (lambda payload: payload.update({"idempotency_key": ""}), 400),
    ),
)
def test_post_rejects_invalid_or_authority_expanding_payloads(
    tmp_path,
    mutation,
    expected_status,
):
    payload = _valid_payload()
    mutation(payload)
    with _running_server(tmp_path) as (server, _):
        status, body = _request(
            server,
            "POST",
            "/api/pocs",
            payload=payload,
        )

        assert len(server.draft_poc_service) == 0

    assert status == expected_status
    assert body == {"error": "Draft POC request is invalid."}


@pytest.mark.parametrize(
    "target",
    (
        "/api/pocs?source=email",
        "/api/pocs;source=email",
    ),
)
def test_post_rejects_route_parameters(tmp_path, target):
    with _running_server(tmp_path) as (server, _):
        status, body = _request(
            server,
            "POST",
            target,
            payload=_valid_payload(),
        )

    assert status == 400
    assert body == {
        "error": "Draft POC routes do not accept URL parameters."
    }


@pytest.mark.parametrize(
    ("content_type", "origin", "expected_status", "expected_error"),
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
            "https://example.com",
            403,
            "Origin is not allowed.",
        ),
    ),
)
def test_post_rejects_wrong_media_type_and_origin(
    tmp_path,
    content_type,
    origin,
    expected_status,
    expected_error,
):
    with _running_server(tmp_path) as (server, _):
        status, body = _request(
            server,
            "POST",
            "/api/pocs",
            payload=_valid_payload(),
            content_type=content_type,
            origin=origin,
        )

    assert status == expected_status
    assert body == {"error": expected_error}


def test_malformed_json_is_safe_and_does_not_reflect_input(tmp_path):
    marker = b'{"display_name":"raw-secret-marker"'
    with _running_server(tmp_path) as (server, _):
        status, body = _request(
            server,
            "POST",
            "/api/pocs",
            raw_body=marker,
        )

    assert status == 400
    assert body == {"error": "Draft POC request is invalid."}
    assert "raw-secret-marker" not in json.dumps(body)


@pytest.mark.parametrize(
    "target",
    (
        "/api/pocs/poc_BAD",
        "/api/pocs/not-a-poc",
        "/api/pocs/poc_ab",
    ),
)
def test_get_rejects_malformed_poc_ids(tmp_path, target):
    with _running_server(tmp_path) as (server, _):
        status, body = _request(server, "GET", target)

    assert status == 400
    assert body == {"error": "Draft POC request is invalid."}


def test_cross_id_lookup_and_get_parameters_fail_closed(tmp_path):
    with _running_server(tmp_path) as (server, _):
        missing_status, missing = _request(
            server,
            "GET",
            "/api/pocs/poc_missing_draft",
        )
        query_status, query = _request(
            server,
            "GET",
            "/api/pocs/poc_missing_draft?include=workflow",
        )

    assert missing_status == 404
    assert missing == {
        "error": "Draft POC was not found in this local process."
    }
    assert query_status == 400
    assert query == {
        "error": "Draft POC routes do not accept URL parameters."
    }


def test_new_poc_page_rejects_url_parameters(tmp_path):
    with _running_server(tmp_path) as (_, base_url):
        with pytest.raises(HTTPError) as invalid:
            urlopen(base_url + "/app/pocs/new?source=email", timeout=5)

        assert invalid.value.code == 400
        assert json.loads(invalid.value.read()) == {
            "error": "Draft POC routes do not accept URL parameters."
        }


def test_creation_ui_prevents_duplicate_submit_and_does_not_fake_intake():
    static_root = (
        Path(__file__).resolve().parents[1] / "src" / "exitspec" / "static"
    )
    html = (static_root / "new_poc.html").read_text("utf-8")
    css = (static_root / "new_poc.css").read_text("utf-8")
    javascript = (static_root / "new_poc.js").read_text("utf-8")

    assert 'id="create-poc"' in html
    assert 'id="created-panel"' in html
    assert "no source is imported or approved" in html
    assert "Nothing has been ingested, approved, confirmed, frozen, executed" in html
    assert "inFlight || !selectedSource" in javascript
    assert "createButton.disabled = !canSubmit" in javascript
    assert "idempotencyKey ||=" in javascript
    assert "pendingPayload ||=" in javascript
    assert "body: JSON.stringify(pendingPayload)" in javascript
    assert "isTrustedDraftResponse(result)" in javascript
    assert "source_ingestion_state === \"NOT_STARTED\"" in javascript
    assert "payload.error" not in javascript
    assert "error.message" not in javascript
    assert "responseStatus >= 400" in javascript
    assert "responseStatus < 500" in javascript
    assert "innerHTML" not in javascript
    assert "window.location" not in javascript
    assert "/app/pocs/" not in javascript
    assert "height: 100dvh" not in css
    assert "overflow: auto" in css
    assert "@media (max-width: 760px)" in css
