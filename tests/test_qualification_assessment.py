"""Adversarial tests for the PR10 qualification-validity boundary."""

from __future__ import annotations

import ast
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import exitspec.qualification_assessment as assessment_module
from exitspec.canonical import canonical_json_bytes
from exitspec.qualification_assessment import (
    QualificationAssessmentReason,
    QualificationAssessmentRejected,
    QualificationValidity,
    assess_inference_qualification,
    parse_qualification_assessment,
    serialize_qualification_assessment,
)
from exitspec.qualification_scope import create_qualification_context, create_qualification_scope
from exitspec.serving_subject import create_serving_subject_manifest
from test_qualification_receipts import _inputs, _issue

FIXED_TIME = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)


def _changed_subject(authority):
    payload = authority.subject.model_dump(mode="json", exclude={"subject_digest"})
    payload["model"]["revision"] = "fedcba9876543210"
    return create_serving_subject_manifest(payload)


def _changed_scope(authority):
    payload = authority.scope.model_dump(mode="json", exclude={"scope_digest"})
    payload["maximum_use"]["maximum_traffic_percent"] = 4
    return create_qualification_scope(payload)


def test_current_assessment_is_typed_canonical_and_zero_authority(monkeypatch):
    authority, _, _, _, _ = _inputs()
    receipt = _issue(monkeypatch)
    assessment = assess_inference_qualification(
        receipt,
        authority.subject,
        authority.scope,
        authority.context,
        assessed_at=FIXED_TIME + timedelta(hours=1),
    )

    assert assessment.validity == QualificationValidity.CURRENT
    assert assessment.reason == QualificationAssessmentReason.CURRENT
    assert assessment.verdict == "PASS"
    assert assessment.purpose == "CANARY_CONSIDERATION"
    assert assessment.deployment_authorized is False
    assert assessment.production_traffic_authorized is False
    assert assessment.traffic_expansion_authorized is False
    assert assessment.external_authorization_required is True
    raw = serialize_qualification_assessment(assessment)
    assert parse_qualification_assessment(raw) == assessment
    assert raw == canonical_json_bytes(json.loads(raw))


def test_subject_and_scope_drift_are_stale_without_rewriting_receipt(monkeypatch):
    authority, _, _, _, _ = _inputs()
    receipt = _issue(monkeypatch)

    changed_subject = _changed_subject(authority)
    changed_subject_context = create_qualification_context(
        changed_subject,
        authority.scope,
        protocol_id="inference-performance-qualification",
        protocol_version="1.0.0",
    )
    subject_assessment = assess_inference_qualification(
        receipt,
        changed_subject,
        authority.scope,
        changed_subject_context,
        assessed_at=FIXED_TIME,
    )
    assert subject_assessment.validity == QualificationValidity.STALE
    assert subject_assessment.reason == QualificationAssessmentReason.SUBJECT_CHANGED

    changed_scope = _changed_scope(authority)
    changed_scope_context = create_qualification_context(
        authority.subject,
        changed_scope,
        protocol_id="inference-performance-qualification",
        protocol_version="1.0.0",
    )
    scope_assessment = assess_inference_qualification(
        receipt,
        authority.subject,
        changed_scope,
        changed_scope_context,
        assessed_at=FIXED_TIME,
    )
    assert scope_assessment.validity == QualificationValidity.STALE
    assert scope_assessment.reason == QualificationAssessmentReason.SCOPE_CHANGED
    assert receipt.subject_digest == authority.subject.subject_digest
    assert receipt.scope_digest == authority.scope.scope_digest


def test_protocol_drift_is_invalid_and_exact_freshness_boundary_expires(monkeypatch):
    authority, _, _, _, _ = _inputs()
    receipt = _issue(monkeypatch)
    unsupported_context = create_qualification_context(
        authority.subject,
        authority.scope,
        protocol_id="unsupported-qualification-protocol",
        protocol_version="1.0.0",
    )
    unsupported = assess_inference_qualification(
        receipt,
        authority.subject,
        authority.scope,
        unsupported_context,
        assessed_at=FIXED_TIME,
    )
    assert unsupported.validity == QualificationValidity.INVALID
    assert unsupported.reason == QualificationAssessmentReason.UNSUPPORTED_PROTOCOL

    expired_at = FIXED_TIME + timedelta(days=1)
    expired = assess_inference_qualification(
        receipt,
        authority.subject,
        authority.scope,
        authority.context,
        assessed_at=expired_at,
    )
    assert expired.validity == QualificationValidity.EXPIRED
    assert expired.reason == QualificationAssessmentReason.EXPIRED
    assert expired.expires_at == expired_at


def test_malformed_receipt_and_invalid_context_fail_closed_to_invalid(monkeypatch):
    authority, _, _, _, _ = _inputs()
    receipt = _issue(monkeypatch)
    malformed = assess_inference_qualification(
        b'{"schema_version":"not-a-receipt"}',
        authority.subject,
        authority.scope,
        authority.context,
        assessed_at=FIXED_TIME,
    )
    assert malformed.validity == QualificationValidity.INVALID
    assert malformed.reason == QualificationAssessmentReason.INVALID_RECEIPT
    assert malformed.receipt_id is None
    assert malformed.verdict is None

    invalid_context = authority.context.model_copy(
        update={"qualification_context_digest": "sha256:" + "f" * 64}
    )
    invalid = assess_inference_qualification(
        receipt,
        authority.subject,
        authority.scope,
        invalid_context,
        assessed_at=FIXED_TIME,
    )
    assert invalid.validity == QualificationValidity.INVALID
    assert invalid.reason == QualificationAssessmentReason.INVALID_CONTEXT


def test_assessment_parser_rejects_duplicate_fields_and_tampering():
    with pytest.raises(QualificationAssessmentRejected):
        parse_qualification_assessment(
            b'{"assessment_id":"x","assessment_id":"y"}'
        )

    with pytest.raises(QualificationAssessmentRejected):
        parse_qualification_assessment(canonical_json_bytes({"schema_version": "x"}))


def test_module_has_no_network_or_execution_import_surface():
    source = Path(assessment_module.__file__).read_text(encoding="utf-8")
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
