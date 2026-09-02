"""Adversarial tests for the PR9 qualification-receipt boundary."""

from __future__ import annotations

import ast
import hashlib
import json
from decimal import Decimal
from datetime import UTC, datetime
from pathlib import Path

import pytest

import exitspec.qualification_receipts as receipts_module
from exitspec.canonical import canonical_json_bytes
from exitspec.external_evidence_admission import (
    AdmittedExternalEvidenceV1,
    EVIDENCE_CLASS_SYNTHETIC_CI_INFERDROME_V1,
    EXTERNAL_EVIDENCE_PACKAGE_DIGEST_DOMAIN,
    ExternalEvidencePackageV1,
)
from exitspec.producer_capability import get_producer_capability_descriptor
from exitspec.proofability import evaluate_proofability, proofability_report_digest
from exitspec.proofability_workspace_fixture import (
    PRODUCTION_FIXTURE_AUTHORITIES,
    PROFILE_ID,
    PROFILE_VERSION,
)
from exitspec.prospective_handoff import create_prospective_handoff
from exitspec.qualification_receipts import (
    InferencePerformanceQualificationReceiptV1,
    QualificationReceiptCode,
    QualificationReceiptRejected,
    issue_inference_performance_qualification_receipt,
    parse_inference_performance_qualification_receipt,
    serialize_inference_performance_qualification_receipt,
)
from exitspec.inferdrome_bundle import RecalculatedInferdromeMeasurements

FIXED_TIME = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)


def _inputs():
    authority = PRODUCTION_FIXTURE_AUTHORITIES[0]
    descriptor = get_producer_capability_descriptor(
        profile_id=PROFILE_ID,
        profile_version=PROFILE_VERSION,
    )
    report = evaluate_proofability(
        authority.subject,
        authority.scope,
        authority.context,
        authority.contract,
        descriptor,
    )
    handoff = create_prospective_handoff(
        authority.subject,
        authority.scope,
        authority.context,
        authority.contract,
        descriptor,
        report,
    )
    unsigned = {
        "schema_version": "exitspec.external-evidence-package.v1",
        "canonicalization_version": "rfc8785_jcs_v1",
        "hash_version": "sha256_v1",
        "evidence_class": EVIDENCE_CLASS_SYNTHETIC_CI_INFERDROME_V1,
        "profile_id": PROFILE_ID,
        "profile_version": PROFILE_VERSION,
        "capability_digest": descriptor.capability_digest,
        "subject_digest": authority.expected_subject_digest,
        "scope_digest": authority.expected_scope_digest,
        "qualification_context_digest": authority.expected_qualification_context_digest,
        "contract_canonical_digest": authority.expected_contract_canonical_digest,
        "proofability_report_digest": proofability_report_digest(report),
        "evidence_set_id": "synthetic-ci-evidence-01",
        "bundle_digest": "sha256:" + "b" * 64,
    }
    package = ExternalEvidencePackageV1(
        **unsigned,
        package_digest="sha256:" + hashlib.sha256(
            EXTERNAL_EVIDENCE_PACKAGE_DIGEST_DOMAIN + canonical_json_bytes(unsigned)
        ).hexdigest(),
    )
    return authority, descriptor, report, handoff, package


def _admitted(
    package: ExternalEvidencePackageV1,
    *,
    attempted: int = 100,
    successful: int = 100,
    failed: int = 0,
    p95: int | None = 12_000_000,
) -> AdmittedExternalEvidenceV1:
    return AdmittedExternalEvidenceV1(
        package_digest=package.package_digest,
        evidence_set_id=package.evidence_set_id,
        evidence_class=package.evidence_class,
        profile_id=package.profile_id,
        profile_version=package.profile_version,
        bundle_digest=package.bundle_digest,
        verifier_version="1.0.0",
        recalculated=RecalculatedInferdromeMeasurements(
            attempted_count=attempted,
            successful_count=successful,
            failed_count=failed,
            anomalous_count=0,
            error_rate=Decimal(failed) / Decimal(attempted),
            p95_ttft_ns=p95,
            ttft_definition="vllm_first_choices_event_v0_26",
            records_sha256="sha256:" + "c" * 64,
            recalculation_sha256="sha256:" + "d" * 64,
        ),
    )


def _issue(monkeypatch, *, failed: int = 0, attempted: int = 100, p95=12_000_000):
    authority, descriptor, report, handoff, package = _inputs()
    monkeypatch.setattr(
        receipts_module,
        "admit_external_evidence_package",
        lambda *args, **kwargs: _admitted(
            package,
            attempted=attempted,
            successful=attempted - failed,
            failed=failed,
            p95=p95,
        ),
    )
    receipt = issue_inference_performance_qualification_receipt(
        "/synthetic/not-read",
        package,
        handoff,
        authority.subject,
        authority.scope,
        authority.context,
        authority.contract,
        descriptor,
        report,
        evidence_captured_at=FIXED_TIME,
        issued_at=FIXED_TIME,
    )
    return receipt


def test_receipt_recalculates_typed_verdict_and_preserves_authority_boundary(
    monkeypatch,
):
    receipt = _issue(monkeypatch)

    assert type(receipt) is InferencePerformanceQualificationReceiptV1
    assert receipt.verdict == "PASS"
    assert receipt.proofability == "PROVABLE"
    assert receipt.deployment_authorized is False
    assert receipt.production_traffic_authorized is False
    assert receipt.traffic_expansion_authorized is False
    assert receipt.external_authorization_required is True
    raw = serialize_inference_performance_qualification_receipt(receipt)
    assert parse_inference_performance_qualification_receipt(raw) == receipt
    assert raw == canonical_json_bytes(json.loads(raw))


@pytest.mark.parametrize(
    ("failed", "attempted", "p95", "expected"),
    [
        (1, 100, 12_000_000, "FAIL"),
        (0, 99, 12_000_000, "NOT_PROVEN"),
        (0, 100, None, "NOT_PROVEN"),
    ],
)
def test_verdict_states_do_not_collapse_to_pass(
    monkeypatch,
    failed: int,
    attempted: int,
    p95: int | None,
    expected: str,
):
    assert _issue(
        monkeypatch,
        failed=failed,
        attempted=attempted,
        p95=p95,
    ).verdict == expected


def test_receipt_parser_rejects_tampering_and_duplicate_fields():
    _authority, _descriptor, _report, _handoff, _package = _inputs()
    payload = {
        "schema_version": "exitspec.inference-performance-qualification-receipt.v1",
        "canonicalization_version": "rfc8785_jcs_v1",
        "hash_version": "sha256_v1",
    }
    with pytest.raises(QualificationReceiptRejected) as caught:
        parse_inference_performance_qualification_receipt(
            canonical_json_bytes(payload)
        )
    assert caught.value.code is QualificationReceiptCode.INVALID_INPUT

    with pytest.raises(QualificationReceiptRejected):
        parse_inference_performance_qualification_receipt(
            b'{"schema_version":"x","schema_version":"y"}'
        )


def test_invalid_or_unprovable_context_issues_no_receipt(monkeypatch):
    authority, descriptor, report, handoff, package = _inputs()
    monkeypatch.setattr(
        receipts_module,
        "admit_external_evidence_package",
        lambda *args, **kwargs: pytest.fail("admission must not run"),
    )
    with pytest.raises(QualificationReceiptRejected) as caught:
        issue_inference_performance_qualification_receipt(
            "/synthetic/not-read",
            package,
            handoff,
            authority.subject,
            authority.scope,
            authority.context,
            authority.contract,
            descriptor,
            report.model_copy(update={"overall_disposition": "NOT_PROVABLE"}),
            evidence_captured_at=FIXED_TIME,
            issued_at=FIXED_TIME,
        )
    assert caught.value.code is QualificationReceiptCode.CONTEXT_MISMATCH


def test_module_has_no_network_or_execution_import_surface():
    source = Path(receipts_module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_modules = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported_modules.update(
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    )
    assert not imported_modules.intersection(
        {"subprocess", "socket", "requests", "httpx", "urllib", "webbrowser"}
    )
