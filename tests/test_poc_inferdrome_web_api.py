from __future__ import annotations

from http import HTTPStatus
from pathlib import Path

from exitspec.inferdrome_catalog import InferdromeBundleCatalog
from exitspec.poc_inferdrome_import import ProcessLocalPOCInferdromeImportService
from exitspec.poc_inferdrome_web_api import (
    handle_poc_inferdrome_web_api_request,
    is_poc_inferdrome_web_api_target,
)
from tests.poc_inferdrome_helpers import (
    NOW,
    POC_ID,
    build_external_lifecycle,
    customer_eligible_bundle,
)


def _runtime(tmp_path: Path) -> ProcessLocalPOCInferdromeImportService:
    lifecycle, _ = build_external_lifecycle()
    runs_root, _ = customer_eligible_bundle(tmp_path, lifecycle)
    return ProcessLocalPOCInferdromeImportService(
        lifecycle=lifecycle,
        catalog=InferdromeBundleCatalog(runs_root.resolve()),
        output_root=(tmp_path / "exitspec-runs").resolve(),
        worker_launcher=lambda target: target(),
        clock=lambda: NOW,
    )


def _handle(runtime, method: str, target: str, payload=None):
    response = handle_poc_inferdrome_web_api_request(
        method=method,
        target=target,
        payload=payload,
        runtime=runtime,
    )
    assert response is not None
    return response


def test_catalog_and_import_api_never_publish_or_accept_paths(tmp_path: Path):
    runtime = _runtime(tmp_path)
    root = f"/api/pocs/{POC_ID}/inferdrome"
    catalog = _handle(runtime, "GET", root + "/runs")

    assert catalog.status == HTTPStatus.OK
    assert set(catalog.payload) == {"configured", "runs", "rejected_count"}
    assert catalog.payload["configured"] is True
    assert catalog.payload["rejected_count"] == 0
    assert len(catalog.payload["runs"]) == 1
    selected = catalog.payload["runs"][0]
    assert set(selected) == {"run_id", "bundle_digest"}
    assert all("path" not in key for key in selected)

    rejected = _handle(
        runtime,
        "POST",
        root + "/imports",
        {
            **selected,
            "import_acknowledged": True,
            "idempotency_key": "web-import-inferdrome",
            "bundle_path": "/tmp/untrusted",
        },
    )
    assert rejected.status == HTTPStatus.BAD_REQUEST


def test_import_api_projects_receipt_and_not_proven_as_success(tmp_path: Path):
    runtime = _runtime(tmp_path)
    root = f"/api/pocs/{POC_ID}/inferdrome"
    selected = _handle(runtime, "GET", root + "/runs").payload["runs"][0]

    started = _handle(
        runtime,
        "POST",
        root + "/imports",
        {
            **selected,
            "import_acknowledged": True,
            "idempotency_key": "web-import-inferdrome",
        },
    )
    latest = _handle(runtime, "GET", root + "/imports/latest")

    assert started.status == HTTPStatus.ACCEPTED
    assert started.payload["replayed"] is False
    assert latest.status == HTTPStatus.OK
    assert latest.payload["status"] == "COMPLETED"
    assert latest.payload["verdict"] == "NOT_PROVEN"
    assert latest.payload["receipt_id"].startswith("irc_")
    assert latest.payload["evidence_pack_url"].endswith(
        "/decision-packet.html"
    )
    assert "RELIABILITY_CLASSIFICATION_UNAVAILABLE" in (
        latest.payload["applicability_codes"]
    )


def test_import_api_requires_exact_explicit_acknowledgement(tmp_path: Path):
    runtime = _runtime(tmp_path)
    root = f"/api/pocs/{POC_ID}/inferdrome"
    selected = _handle(runtime, "GET", root + "/runs").payload["runs"][0]

    for acknowledgement in (None, False):
        body = {
            **selected,
            "idempotency_key": "web-import-without-authorization",
        }
        if acknowledgement is not None:
            body["import_acknowledged"] = acknowledgement
        response = _handle(runtime, "POST", root + "/imports", body)
        assert response.status == HTTPStatus.BAD_REQUEST


def test_routes_and_method_shapes_fail_closed(tmp_path: Path):
    runtime = _runtime(tmp_path)
    root = f"/api/pocs/{POC_ID}/inferdrome"

    assert is_poc_inferdrome_web_api_target(root + "/runs")
    assert not is_poc_inferdrome_web_api_target("/api/pocs")
    for target in (
        root + "/runs?path=/tmp",
        root + "/runs;unsafe",
        root + "/../runs",
        root + "/imports/not-an-operation",
    ):
        assert _handle(runtime, "GET", target).status == HTTPStatus.BAD_REQUEST
    assert _handle(runtime, "GET", root + "/imports").status == (
        HTTPStatus.METHOD_NOT_ALLOWED
    )
