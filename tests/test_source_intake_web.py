from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from http.client import HTTPConnection
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen

from exitspec.web import DemoSession, ExitSpecDemoServer


@contextmanager
def _running_server(tmp_path: Path):
    session = DemoSession.synthetic_support_agent(output_root=tmp_path / "runs")
    server = ExitSpecDemoServer(("127.0.0.1", 0), session)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield server, "http://127.0.0.1:{0}".format(server.server_port)
    finally:
        server.shutdown()
        worker.join(timeout=5)
        server.server_close()


def _post(
    server: ExitSpecDemoServer,
    target: str,
    payload: dict,
) -> tuple[int, dict]:
    connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    try:
        connection.request(
            "POST",
            target,
            body=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Origin": "http://127.0.0.1:{0}".format(server.server_port),
            },
        )
        response = connection.getresponse()
        return response.status, json.loads(response.read().decode("utf-8"))
    finally:
        connection.close()


def _create_draft(server: ExitSpecDemoServer) -> dict:
    status, payload = _post(
        server,
        "/api/pocs",
        {
            "display_name": "Support automation POC",
            "customer_label": "Northstar",
            "use_case": "Validate one bounded customer requirement.",
            "owner": "field_engineer",
            "first_source_choice": "MEETING",
            "idempotency_key": "create-source-page",
        },
    )
    assert status == 201
    return payload


def test_created_draft_has_one_real_source_intake_page_and_assets(tmp_path):
    with _running_server(tmp_path) as (server, base_url):
        draft = _create_draft(server)
        source_path = "/app/pocs/{0}/sources/new".format(draft["poc_id"])
        with urlopen(base_url + source_path, timeout=5) as response:
            page = response.read()
        with urlopen(base_url + "/source_intake.css", timeout=5) as response:
            stylesheet = response.read()
        with urlopen(base_url + "/source_intake.js", timeout=5) as response:
            javascript = response.read()

    assert b'id="source-intake-main"' in page
    assert b"Capture one customer source" in page
    assert b"Capture source" in page
    assert b"NEEDS_REVIEW" in page
    assert b"Audio, STT, Zoom, and Google Meet are not" in page
    assert stylesheet
    assert b"/sources/new" in javascript
    assert b"/sources/meeting" not in javascript
    assert b"`${sourcesApi}/meeting`" in javascript


def test_missing_malformed_and_parameterized_source_pages_fail_closed(tmp_path):
    with _running_server(tmp_path) as (server, base_url):
        draft = _create_draft(server)
        targets = (
            "/app/pocs/poc_missing_source_page/sources/new",
            "/app/pocs/poc_BAD/sources/new",
            "/app/pocs/{0}/sources/new?adapter=provider".format(
                draft["poc_id"]
            ),
        )
        results = []
        for target in targets:
            try:
                urlopen(base_url + target, timeout=5)
            except HTTPError as error:
                results.append(
                    (error.code, json.loads(error.read().decode("utf-8")))
                )

    assert results == [
        (
            404,
            {"error": "Draft POC was not found in this local process."},
        ),
        (400, {"error": "Draft POC request is invalid."}),
        (
            400,
            {"error": "Draft POC routes do not accept URL parameters."},
        ),
    ]
