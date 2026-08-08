from __future__ import annotations

from pathlib import Path

import pytest

from exitspec.inferdrome_catalog import InferdromeBundleCatalog
from exitspec.models import VerdictStatus
from exitspec.poc_inferdrome_import import (
    POCInferdromeImportConflict,
    POCInferdromeImportInvalid,
    POCInferdromeImportStatus,
    ProcessLocalPOCInferdromeImportService,
)
from exitspec.poc_performance_contract import PerformanceEvidenceMethod
from exitspec.poc_performance_run import (
    POCPerformanceRunConflict,
    ProcessLocalPOCPerformanceRunService,
)
from tests.poc_inferdrome_helpers import (
    NOW,
    POC_ID,
    build_external_lifecycle,
    customer_eligible_bundle,
)


def _service(tmp_path: Path, *, launcher=None):
    lifecycle, _ = build_external_lifecycle()
    runs_root, bundle_path = customer_eligible_bundle(tmp_path, lifecycle)
    catalog = InferdromeBundleCatalog(runs_root.resolve())
    service = ProcessLocalPOCInferdromeImportService(
        lifecycle=lifecycle,
        catalog=catalog,
        output_root=(tmp_path / "exitspec-runs").resolve(),
        worker_launcher=(lambda target: target()) if launcher is None else launcher,
        clock=lambda: NOW,
    )
    entry = catalog.refresh().entries[0]
    return service, entry, bundle_path


def test_frozen_external_poc_imports_and_releases_verified_not_proven_pack(
    tmp_path: Path,
):
    service, entry, _ = _service(tmp_path)

    before = service.snapshot(POC_ID)
    started = service.start(
        POC_ID,
        import_acknowledged=True,
        run_id=entry.run_id,
        bundle_digest=entry.bundle_digest,
        idempotency_key="import-inferdrome-proof",
    )
    replay = service.start(
        POC_ID,
        import_acknowledged=True,
        run_id=entry.run_id,
        bundle_digest=entry.bundle_digest,
        idempotency_key="import-inferdrome-proof",
    )
    completed = service.snapshot(POC_ID)

    assert before.status is POCInferdromeImportStatus.NOT_STARTED
    assert started.replayed is False
    assert replay.replayed is True
    assert replay.operation.operation_id == started.operation.operation_id
    assert completed.status is POCInferdromeImportStatus.COMPLETED
    assert completed.verdict is VerdictStatus.NOT_PROVEN
    assert completed.receipt_id is not None
    assert completed.selected_run_id == entry.run_id
    assert completed.producer_run_id == entry.run_id
    assert completed.bundle_digest == entry.bundle_digest
    assert completed.attempted_count == 4
    assert completed.successful_count == 3
    assert completed.error_count == 1
    assert completed.p95_ttft_ms == "14.906291"
    assert set(completed.applicability_codes) == {
        "TRAFFIC_MISMATCH",
        "TTFT_DEFINITION_MISMATCH",
        "RELIABILITY_CLASSIFICATION_UNAVAILABLE",
    }
    assert completed.evidence_pack_url is not None
    assert completed.operation_id is not None
    pack_sha256 = service.verified_evidence_pack_sha256(
        POC_ID,
        completed.operation_id,
    )
    assert len(pack_sha256) == 64
    report = (
        tmp_path
        / "exitspec-runs"
        / completed.operation_id
        / "decision-packet.html"
    ).read_text("utf-8")
    assert "ExitSpec independently verified" in report
    assert "NOT PROVEN" in report
    assert "treated those bytes as untrusted input" in report


def test_import_requires_explicit_authorization(tmp_path: Path):
    service, entry, _ = _service(tmp_path)

    with pytest.raises(POCInferdromeImportInvalid):
        service.start(
            POC_ID,
            import_acknowledged=False,
            run_id=entry.run_id,
            bundle_digest=entry.bundle_digest,
            idempotency_key="import-without-acknowledgement",
        )


def test_evidence_methods_are_mutually_exclusive(tmp_path: Path):
    external_lifecycle, _ = build_external_lifecycle()
    local_runner = ProcessLocalPOCPerformanceRunService(
        lifecycle=external_lifecycle,
        output_root=(tmp_path / "local-output").resolve(),
    )
    with pytest.raises(POCPerformanceRunConflict):
        local_runner.snapshot(POC_ID)

    local_lifecycle, _ = build_external_lifecycle(
        evidence_method=PerformanceEvidenceMethod.EXIT_SPEC_STREAMING_PROBE,
    )
    importer = ProcessLocalPOCInferdromeImportService(
        lifecycle=local_lifecycle,
        catalog=InferdromeBundleCatalog(None),
        output_root=(tmp_path / "import-output").resolve(),
        worker_launcher=lambda target: target(),
        clock=lambda: NOW,
    )
    with pytest.raises(POCInferdromeImportConflict):
        importer.start(
            POC_ID,
            import_acknowledged=True,
            run_id="run-" + "0" * 32,
            bundle_digest="sha256:" + "0" * 64,
            idempotency_key="local-contract-cannot-import",
        )


def test_mutation_after_catalog_resolution_is_ingestion_rejected_not_verdict(
    tmp_path: Path,
):
    pending = []
    service, entry, bundle_path = _service(
        tmp_path,
        launcher=lambda target: pending.append(target),
    )
    started = service.start(
        POC_ID,
        import_acknowledged=True,
        run_id=entry.run_id,
        bundle_digest=entry.bundle_digest,
        idempotency_key="import-mutated-inferdrome-proof",
    )
    assert started.operation.status is POCInferdromeImportStatus.IMPORTING
    (bundle_path / "native" / "stdout.log").write_text(
        "mutated after selection",
        encoding="utf-8",
    )

    pending.pop()()
    rejected = service.snapshot(POC_ID)

    assert rejected.status is POCInferdromeImportStatus.INGESTION_REJECTED
    assert rejected.rejection_code == "INTEGRITY_MISMATCH"
    assert rejected.verdict is None
    assert rejected.receipt_id is None
    assert rejected.evidence_pack_url is None


def test_tampered_import_pack_is_withheld_on_reverification(tmp_path: Path):
    service, entry, _ = _service(tmp_path)
    service.start(
        POC_ID,
        import_acknowledged=True,
        run_id=entry.run_id,
        bundle_digest=entry.bundle_digest,
        idempotency_key="import-pack-tamper-proof",
    )
    completed = service.snapshot(POC_ID)
    assert completed.operation_id is not None
    report = (
        tmp_path
        / "exitspec-runs"
        / completed.operation_id
        / "decision-packet.html"
    )
    report.write_text("tampered", encoding="utf-8")

    with pytest.raises(POCInferdromeImportConflict):
        service.verified_evidence_pack_sha256(
            POC_ID,
            completed.operation_id,
        )
