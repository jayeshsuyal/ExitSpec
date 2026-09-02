"""Adversarial tests for the provider-neutral PR8 evidence boundary."""

from __future__ import annotations

import ast
import hashlib
import json
from decimal import Decimal
from pathlib import Path

import pytest

import exitspec.external_evidence_admission as admission_module
from exitspec.canonical import canonical_json_bytes
from exitspec.external_evidence_admission import (
    EVIDENCE_CLASS_SYNTHETIC_CI_INFERDROME_V1,
    EXTERNAL_EVIDENCE_PACKAGE_DIGEST_DOMAIN,
    ExternalEvidenceAdmissionCode,
    ExternalEvidenceAdmissionRejected,
    ExternalEvidencePackageV1,
    admit_external_evidence_package,
    external_evidence_package_digest,
    parse_external_evidence_package,
    serialize_external_evidence_package,
)
from exitspec.inferdrome_bundle import (
    RecalculatedInferdromeMeasurements,
    VerifiedInferdromeBundle,
)
from exitspec.producer_capability import get_producer_capability_descriptor
from exitspec.proofability import evaluate_proofability, proofability_report_digest
from exitspec.proofability_workspace_fixture import (
    PRODUCTION_FIXTURE_AUTHORITIES,
    PROFILE_ID,
    PROFILE_VERSION,
)
from exitspec.prospective_handoff import create_prospective_handoff


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
    return authority, descriptor, report, handoff


def _package() -> ExternalEvidencePackageV1:
    authority, descriptor, report, _ = _inputs()
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
    package_digest = "sha256:" + hashlib.sha256(
        EXTERNAL_EVIDENCE_PACKAGE_DIGEST_DOMAIN + canonical_json_bytes(unsigned)
    ).hexdigest()
    return ExternalEvidencePackageV1(
        **unsigned,
        package_digest=package_digest,
    )


def _verified_bundle(package: ExternalEvidencePackageV1) -> VerifiedInferdromeBundle:
    return VerifiedInferdromeBundle(
        root=Path("/synthetic/not-read"),
        bundle_digest=package.bundle_digest,
        descriptor={
            "evidence_eligibility": "SYNTHETIC",
            "digests": {
                "exitspec_contract_digest": package.contract_canonical_digest,
            },
        },
        resolved_spec={},
        request_plan={},
        execution={},
        environment={},
        records=(),
        recalculated=RecalculatedInferdromeMeasurements(
            attempted_count=4,
            successful_count=4,
            failed_count=0,
            anomalous_count=0,
            error_rate=Decimal(0),
            p95_ttft_ns=12_000_000,
            ttft_definition="vllm_first_choices_event_v0_26",
            records_sha256="sha256:" + "c" * 64,
            recalculation_sha256="sha256:" + "d" * 64,
        ),
        managed_profile=None,
    )


def test_package_is_canonical_and_digest_bound():
    package = _package()
    raw = serialize_external_evidence_package(package)

    assert raw == canonical_json_bytes(json.loads(raw))
    assert parse_external_evidence_package(raw) == package
    assert external_evidence_package_digest(package) == package.package_digest
    assert not any(
        forbidden in raw.lower()
        for forbidden in (b"producer_verdict", b"deployment_authorized", b"verdict")
    )


def test_duplicate_noncanonical_and_producer_outcome_fields_fail_closed():
    raw = serialize_external_evidence_package(_package())
    with pytest.raises(ExternalEvidenceAdmissionRejected) as duplicate:
        parse_external_evidence_package(raw.replace(
            b'"schema_version":', b'"schema_version":"x","schema_version":', 1
        ))
    assert duplicate.value.code is ExternalEvidenceAdmissionCode.INVALID_PACKAGE

    with pytest.raises(ExternalEvidenceAdmissionRejected) as noncanonical:
        parse_external_evidence_package(b" " + raw)
    assert noncanonical.value.code is ExternalEvidenceAdmissionCode.INVALID_PACKAGE

    payload = json.loads(raw)
    payload["verdict"] = "PASS"
    with pytest.raises(ExternalEvidenceAdmissionRejected) as outcome:
        parse_external_evidence_package(canonical_json_bytes(payload))
    assert outcome.value.code is ExternalEvidenceAdmissionCode.INVALID_PACKAGE


def test_context_mismatch_is_rejected_before_bundle_reader(monkeypatch, tmp_path):
    authority, descriptor, report, handoff = _inputs()
    package = _package().model_copy(
        update={"subject_digest": "sha256:" + "0" * 64}
    )
    unsigned = package.model_dump(mode="json", exclude={"package_digest"})
    package = package.model_copy(
        update={
            "package_digest": "sha256:" + hashlib.sha256(
                EXTERNAL_EVIDENCE_PACKAGE_DIGEST_DOMAIN
                + canonical_json_bytes(unsigned)
            ).hexdigest()
        }
    )
    called = False

    def should_not_read(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("bundle reader must not run after context rejection")

    monkeypatch.setattr(admission_module, "verify_inferdrome_bundle", should_not_read)
    with pytest.raises(ExternalEvidenceAdmissionRejected) as caught:
        admit_external_evidence_package(
            tmp_path,
            package,
            handoff,
            authority.subject,
            authority.scope,
            authority.context,
            authority.contract,
            descriptor,
            report,
        )
    assert caught.value.code is ExternalEvidenceAdmissionCode.CONTEXT_MISMATCH
    assert not called


def test_valid_synthetic_package_returns_recalculated_facts_without_verdict(
    monkeypatch, tmp_path
):
    authority, descriptor, report, handoff = _inputs()
    package = _package()
    monkeypatch.setattr(
        admission_module,
        "verify_inferdrome_bundle",
        lambda *args, **kwargs: _verified_bundle(package),
    )

    admitted = admit_external_evidence_package(
        tmp_path,
        package,
        handoff,
        authority.subject,
        authority.scope,
        authority.context,
        authority.contract,
        descriptor,
        report,
    )
    assert admitted.package_digest == package.package_digest
    assert admitted.recalculated.p95_ttft_ns == 12_000_000
    assert not hasattr(admitted, "verdict")


def test_module_has_no_network_or_execution_import_surface():
    source = Path(admission_module.__file__).read_text(encoding="utf-8")
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
