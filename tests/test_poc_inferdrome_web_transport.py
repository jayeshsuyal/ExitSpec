from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from http.client import HTTPConnection
from pathlib import Path

from exitspec.poc_inferdrome_import import (
    ProcessLocalPOCInferdromeImportService,
)
from exitspec.poc_performance_run import (
    ProcessLocalPOCPerformanceRunService,
)
from exitspec.web import (
    EVIDENCE_LIBRARY_API_PATH,
    DemoSession,
    ExitSpecDemoServer,
)
from tests.poc_inferdrome_helpers import (
    NOW,
    POC_ID,
    build_external_lifecycle,
    customer_eligible_bundle,
)


@contextmanager
def _running_server(tmp_path: Path):
    lifecycle, drafts = build_external_lifecycle()
    runs_root, _ = customer_eligible_bundle(tmp_path, lifecycle)
    session = DemoSession.synthetic_support_agent(
        output_root=tmp_path / "exitspec-runs"
    )
    server = ExitSpecDemoServer(
        ("127.0.0.1", 0),
        session,
        inferdrome_runs_root=runs_root.resolve(),
    )
    server.draft_poc_service = drafts
    server.performance_lifecycle_service = lifecycle
    server.poc_performance_run_service = ProcessLocalPOCPerformanceRunService(
        lifecycle=lifecycle,
        output_root=session.output_root.resolve(),
    )
    server.poc_inferdrome_import_service = (
        ProcessLocalPOCInferdromeImportService(
            lifecycle=lifecycle,
            catalog=server.inferdrome_catalog,
            output_root=session.output_root.resolve(),
            worker_launcher=lambda target: target(),
            clock=lambda: NOW,
        )
    )
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
    payload: dict | None = None,
    content_type: str | None = "application/json",
    origin: str | None = "same",
    headers: dict[str, str] | None = None,
) -> tuple[int, dict | str, str]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request_headers = {"Accept": "application/json", **(headers or {})}
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
        data = response.read()
        media_type = response.getheader("Content-Type") or ""
        decoded: dict | str = (
            json.loads(data.decode("utf-8"))
            if media_type.startswith("application/json")
            else data.decode("utf-8")
        )
        return response.status, decoded, media_type
    finally:
        connection.close()


def test_mounted_import_route_recalculates_pack_and_completes_handoff(
    tmp_path: Path,
):
    with _running_server(tmp_path) as server:
        root = f"/api/pocs/{POC_ID}/inferdrome"
        catalog_status, catalog, _ = _request(
            server,
            "GET",
            root + "/runs",
            content_type=None,
            origin=None,
        )
        assert catalog_status == 200
        assert isinstance(catalog, dict)
        selected = catalog["runs"][0]
        assert set(selected) == {"run_id", "bundle_digest"}

        no_origin, _, _ = _request(
            server,
            "POST",
            root + "/imports",
            payload={
                **selected,
                "import_acknowledged": True,
                "idempotency_key": "transport-import-without-origin",
            },
            origin=None,
        )
        assert no_origin == 403

        path_injection, _, _ = _request(
            server,
            "POST",
            root + "/imports",
            payload={
                **selected,
                "bundle_path": "/tmp/untrusted",
                "import_acknowledged": True,
                "idempotency_key": "transport-import-with-path",
            },
        )
        assert path_injection == 400

        started_status, started, _ = _request(
            server,
            "POST",
            root + "/imports",
            payload={
                **selected,
                "import_acknowledged": True,
                "idempotency_key": "transport-import-valid",
            },
        )
        assert started_status == 202
        assert isinstance(started, dict)
        assert started["operation"]["status"] == "COMPLETED"

        latest_status, latest, _ = _request(
            server,
            "GET",
            root + "/imports/latest",
            content_type=None,
            origin=None,
        )
        assert latest_status == 200
        assert isinstance(latest, dict)
        assert latest["verdict"] == "NOT_PROVEN"
        assert latest["selected_run_id"] == selected["run_id"]
        assert latest["producer_run_id"] == selected["run_id"]
        assert latest["receipt_id"].startswith("irc_")

        pack_status, pack, pack_type = _request(
            server,
            "GET",
            latest["evidence_pack_url"],
            content_type=None,
            origin=None,
        )
        assert pack_status == 200
        assert "text/html" in pack_type
        assert isinstance(pack, str)
        assert "ExitSpec independently verified" in pack

        library_status, library, _ = _request(
            server,
            "GET",
            EVIDENCE_LIBRARY_API_PATH,
            content_type=None,
            origin=None,
        )
        assert library_status == 200
        assert isinstance(library, dict)
        imported = next(
            item for item in library["packs"] if item["poc_id"] == POC_ID
        )
        assert imported["run_id"] == latest["operation_id"]
        assert imported["verdict"] == "NOT_PROVEN"
        assert imported["handoff_state"] == "READY_FOR_HANDOFF"

        closure_route = f"/api/workspace/pocs/{POC_ID}/closure"
        eligibility_status, eligibility, _ = _request(
            server,
            "GET",
            closure_route,
            content_type=None,
            origin=None,
        )
        assert eligibility_status == 200
        assert isinstance(eligibility, dict)
        assert eligibility["closeable"] is True
        binding = eligibility["eligible_evidence_binding"]
        assert binding["run_id"] == latest["operation_id"]

        closed_status, closed, _ = _request(
            server,
            "POST",
            closure_route,
            payload={
                "decision": "HANDOFF_COMPLETED",
                "decided_by": "field_engineer",
                "rationale": "Verified imported pack handed to the customer.",
                "evidence_binding": binding,
                "terminal_run_binding": None,
                "idempotency_key": "transport-import-handoff",
            },
        )
        assert closed_status == 201
        assert isinstance(closed, dict)
        assert closed["closure"]["decision"] == "HANDOFF_COMPLETED"


def test_import_transport_rejects_wrong_media_query_and_header_authority(
    tmp_path: Path,
):
    with _running_server(tmp_path) as server:
        root = f"/api/pocs/{POC_ID}/inferdrome"
        _, catalog, _ = _request(
            server,
            "GET",
            root + "/runs",
            content_type=None,
            origin=None,
        )
        assert isinstance(catalog, dict)
        payload = {
            **catalog["runs"][0],
            "import_acknowledged": True,
            "idempotency_key": "transport-gate",
        }
        wrong_media, _, _ = _request(
            server,
            "POST",
            root + "/imports",
            payload=payload,
            content_type="text/plain",
        )
        query, _, _ = _request(
            server,
            "POST",
            root + "/imports?path=/tmp",
            payload=payload,
        )
        header_key, _, _ = _request(
            server,
            "POST",
            root + "/imports",
            payload=payload,
            headers={"Idempotency-Key": "browser-must-not-control-this"},
        )
        assert wrong_media == 415
        assert query == 400
        assert header_key == 400
