"""Immutable v0.5 inference-performance qualification receipts.

PR9 is the protocol-specific layer after PR8 evidence admission.  It reruns
the local admission boundary, recalculates the native facts, and then applies
the frozen criterion.  A receipt never trusts a producer verdict, never
contacts a producer, and never grants deployment or traffic authority.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Final, Literal

from pydantic import (
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from .canonical import CanonicalizationError, canonical_json_bytes
from .external_evidence_admission import (
    AdmittedExternalEvidenceV1,
    ExternalEvidencePackageV1,
    admit_external_evidence_package,
)
from .inferdrome_bundle import InferdromeBundleLimits
from .models import (
    FrozenExitSpecModel,
    InferenceQualificationCriterionV1,
    POCContract,
)
from .producer_capability import ProducerCapabilityDescriptorV1
from .proofability import (
    CriterionProofabilityDisposition,
    OverallProofabilityDisposition,
    ProofabilityReportV1,
)
from .prospective_handoff import ProspectiveHandoffV1
from .qualification_scope import QualificationContextV1, QualificationScopeV1
from .serving_subject import ServingSubjectManifestV1

INFERENCE_PERFORMANCE_QUALIFICATION_RECEIPT_SCHEMA_VERSION: Final = (
    "exitspec.inference-performance-qualification-receipt.v1"
)
INFERENCE_PERFORMANCE_QUALIFICATION_RECEIPT_CANONICALIZATION_VERSION: Final = (
    "rfc8785_jcs_v1"
)
INFERENCE_PERFORMANCE_QUALIFICATION_RECEIPT_HASH_VERSION: Final = "sha256_v1"
INFERENCE_PERFORMANCE_QUALIFICATION_RECEIPT_DIGEST_DOMAIN: Final = (
    b"exitspec-inference-performance-qualification-receipt-v1\x00"
)

_DIGEST_PATTERN: Final = r"^sha256:[a-f0-9]{64}$"
_IDENTIFIER_PATTERN: Final = r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$"
_RECEIPT_ID_PATTERN: Final = r"^qrc_[a-f0-9]{64}$"
_MAX_RECEIPT_BYTES: Final = 64 * 1024
_LIMITATIONS: Final = (
    "A qualification receipt is not deployment or production-traffic authorization.",
    "Evidence integrity is not producer-authorship proof.",
    "Native first-event TTFT does not establish first-nonempty-content TTFT.",
)


class QualificationReceiptCode(str, Enum):
    """Stable content-safe receipt issuance failures."""

    INVALID_INPUT = "INVALID_INPUT"
    CONTEXT_MISMATCH = "CONTEXT_MISMATCH"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    UNSUPPORTED_CRITERION = "UNSUPPORTED_CRITERION"


class QualificationReceiptRejected(ValueError):
    """A receipt cannot be issued from the supplied evidence path."""

    def __init__(self, code: QualificationReceiptCode, message: str) -> None:
        self.code = QualificationReceiptCode(code)
        super().__init__(message)


class _StrictFrozenReceiptModel(FrozenExitSpecModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        revalidate_instances="always",
    )


def _reject(code: QualificationReceiptCode, message: str) -> None:
    raise QualificationReceiptRejected(code, message)


def _timestamp(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(UTC)


def _timestamp_text(value: datetime) -> str:
    normalized = _timestamp(value)
    return normalized.isoformat().replace("+00:00", "Z")


def _receipt_id_from_projection(projection: Mapping[str, Any]) -> str:
    try:
        content = canonical_json_bytes(projection)
    except (CanonicalizationError, RecursionError, TypeError, ValueError):
        _reject(QualificationReceiptCode.INVALID_INPUT, "Receipt is not canonical JSON.")
    return "qrc_" + hashlib.sha256(
        INFERENCE_PERFORMANCE_QUALIFICATION_RECEIPT_DIGEST_DOMAIN + content
    ).hexdigest()


class InferencePerformanceQualificationReceiptV1(_StrictFrozenReceiptModel):
    """A typed verdict over one exact admitted qualification context."""

    schema_version: Literal[
        INFERENCE_PERFORMANCE_QUALIFICATION_RECEIPT_SCHEMA_VERSION
    ]
    canonicalization_version: Literal[
        INFERENCE_PERFORMANCE_QUALIFICATION_RECEIPT_CANONICALIZATION_VERSION
    ]
    hash_version: Literal[INFERENCE_PERFORMANCE_QUALIFICATION_RECEIPT_HASH_VERSION]
    receipt_id: str = Field(pattern=_RECEIPT_ID_PATTERN)
    subject_digest: str = Field(pattern=_DIGEST_PATTERN)
    scope_digest: str = Field(pattern=_DIGEST_PATTERN)
    qualification_context_digest: str = Field(pattern=_DIGEST_PATTERN)
    contract_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    contract_canonical_digest: str = Field(pattern=_DIGEST_PATTERN)
    workload_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    workload_digest: str = Field(pattern=_DIGEST_PATTERN)
    measurement_profile_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    measurement_profile_version: str = Field(pattern=r"^v?[0-9]+(?:\.[0-9]+){0,2}$")
    measurement_profile_digest: str = Field(pattern=_DIGEST_PATTERN)
    capability_profile_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    capability_profile_version: str = Field(pattern=r"^v?[0-9]+$")
    capability_digest: str = Field(pattern=_DIGEST_PATTERN)
    proofability_report_digest: str = Field(pattern=_DIGEST_PATTERN)
    evidence_package_digest: str = Field(pattern=_DIGEST_PATTERN)
    evidence_set_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    evidence_class: Literal[
        "EXTERNAL_INFERDROME_V1",
        "SYNTHETIC_CI_INFERDROME_V1",
    ]
    verifier_version: Literal["1.0.0"]
    recalculation_sha256: str = Field(pattern=_DIGEST_PATTERN)
    proofability: Literal["PROVABLE"]
    verdict: Literal["PASS", "FAIL", "NOT_PROVEN"]
    attempted_count: int = Field(ge=0, le=1_000_000)
    successful_count: int = Field(ge=0, le=1_000_000)
    failed_count: int = Field(ge=0, le=1_000_000)
    p95_ttft_ns: int | None = Field(default=None, ge=0, le=60_000_000_000)
    ttft_definition: Literal["vllm_first_choices_event_v0_26"]
    evidence_captured_at: datetime
    issued_at: datetime
    limitations: tuple[str, ...] = Field(min_length=1, max_length=8)
    deployment_authorized: Literal[False] = False
    production_traffic_authorized: Literal[False] = False
    traffic_expansion_authorized: Literal[False] = False
    external_authorization_required: Literal[True] = True

    @field_validator("evidence_captured_at", "issued_at")
    @classmethod
    def require_utc_timestamps(cls, value: datetime) -> datetime:
        return _timestamp(value)

    @field_validator("limitations")
    @classmethod
    def require_canonical_limitations(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if value != tuple(sorted(set(value))) or value != _LIMITATIONS:
            raise ValueError("Receipt limitations must use the fixed canonical set.")
        return value

    @model_validator(mode="after")
    def require_receipt_id_binding(
        self,
    ) -> InferencePerformanceQualificationReceiptV1:
        projection = self.model_dump(mode="json", exclude={"receipt_id"})
        expected = _receipt_id_from_projection(projection)
        if not hmac.compare_digest(self.receipt_id, expected):
            raise ValueError("receipt_id does not bind the receipt projection.")
        if self.successful_count + self.failed_count > self.attempted_count:
            raise ValueError("Receipt population counts are inconsistent.")
        return self


def _criterion(
    contract: POCContract,
    criterion_id: str,
) -> InferenceQualificationCriterionV1:
    matches = tuple(item for item in contract.criteria if item.id == criterion_id)
    if len(matches) != 1 or type(matches[0]) is not InferenceQualificationCriterionV1:
        _reject(
            QualificationReceiptCode.UNSUPPORTED_CRITERION,
            "Receipt issuance requires one supported inference qualification criterion.",
        )
    return matches[0]


def _calculate_verdict(
    admitted: AdmittedExternalEvidenceV1,
    criterion: InferenceQualificationCriterionV1,
) -> Literal["PASS", "FAIL", "NOT_PROVEN"]:
    facts = admitted.recalculated
    latency = criterion.latency_requirement
    reliability = criterion.reliability_requirement
    if facts.attempted_count != reliability.exact_attempts:
        return "NOT_PROVEN"
    if facts.ttft_definition != "vllm_first_choices_event_v0_26":
        return "NOT_PROVEN"
    latency_proven = facts.successful_count >= latency.minimum_successful_samples
    if latency_proven and facts.p95_ttft_ns is not None:
        latency_failed = not facts.p95_ttft_ns < latency.threshold_ns
    else:
        latency_failed = False
    reliability_failed = (
        facts.failed_count * 10_000
        >= reliability.threshold_basis_points * facts.attempted_count
    )
    if reliability_failed:
        return "FAIL"
    if not latency_proven or facts.p95_ttft_ns is None:
        return "NOT_PROVEN"
    if latency_failed:
        return "FAIL"
    return "PASS"


def _validate_original_inputs(
    handoff: ProspectiveHandoffV1,
    subject: ServingSubjectManifestV1,
    scope: QualificationScopeV1,
    context: QualificationContextV1,
    contract: POCContract,
    descriptor: ProducerCapabilityDescriptorV1,
    report: ProofabilityReportV1,
) -> None:
    from .prospective_handoff import verify_prospective_handoff

    if not verify_prospective_handoff(
        handoff,
        subject,
        scope,
        context,
        contract,
        descriptor,
        report,
    ):
        _reject(
            QualificationReceiptCode.CONTEXT_MISMATCH,
            "Receipt inputs do not reproduce the prospective handoff.",
        )
    if report.overall_disposition is not OverallProofabilityDisposition.PROVABLE:
        _reject(
            QualificationReceiptCode.INSUFFICIENT_EVIDENCE,
            "Receipt issuance requires a fully provable report.",
        )
    if any(
        result.disposition is not CriterionProofabilityDisposition.PROVABLE
        for result in report.criterion_results
    ):
        _reject(
            QualificationReceiptCode.INSUFFICIENT_EVIDENCE,
            "Receipt issuance requires provable criterion requirements.",
        )


def issue_inference_performance_qualification_receipt(
    package_path: str,
    package: ExternalEvidencePackageV1,
    handoff: ProspectiveHandoffV1,
    subject: ServingSubjectManifestV1,
    scope: QualificationScopeV1,
    context: QualificationContextV1,
    contract: POCContract,
    descriptor: ProducerCapabilityDescriptorV1,
    report: ProofabilityReportV1,
    *,
    evidence_captured_at: datetime,
    issued_at: datetime,
    limits: InferdromeBundleLimits | None = None,
) -> InferencePerformanceQualificationReceiptV1:
    """Re-admit the original package, recalculate, and issue one receipt."""

    _validate_original_inputs(
        handoff,
        subject,
        scope,
        context,
        contract,
        descriptor,
        report,
    )
    try:
        admitted = admit_external_evidence_package(
            package_path,
            package,
            handoff,
            subject,
            scope,
            context,
            contract,
            descriptor,
            report,
            limits=limits,
        )
    except ValueError as error:
        if isinstance(error, QualificationReceiptRejected):
            raise
        _reject(
            QualificationReceiptCode.INVALID_INPUT,
            "Original evidence package could not be admitted.",
        )
    criterion_id = report.criterion_results[0].criterion_id
    criterion = _criterion(contract, criterion_id)
    verdict = _calculate_verdict(admitted, criterion)
    if contract.canonical_hash is None:
        _reject(QualificationReceiptCode.CONTEXT_MISMATCH, "Contract digest is unavailable.")
    values: dict[str, Any] = {
        "schema_version": INFERENCE_PERFORMANCE_QUALIFICATION_RECEIPT_SCHEMA_VERSION,
        "canonicalization_version": INFERENCE_PERFORMANCE_QUALIFICATION_RECEIPT_CANONICALIZATION_VERSION,
        "hash_version": INFERENCE_PERFORMANCE_QUALIFICATION_RECEIPT_HASH_VERSION,
        "subject_digest": handoff.subject_digest,
        "scope_digest": handoff.scope_digest,
        "qualification_context_digest": handoff.qualification_context_digest,
        "contract_id": handoff.contract_id,
        "contract_canonical_digest": handoff.contract_canonical_digest,
        "workload_id": handoff.workload_id,
        "workload_digest": handoff.workload_digest,
        "measurement_profile_id": handoff.measurement_profile_id,
        "measurement_profile_version": handoff.measurement_profile_version,
        "measurement_profile_digest": handoff.measurement_profile_digest,
        "capability_profile_id": package.profile_id,
        "capability_profile_version": package.profile_version,
        "capability_digest": package.capability_digest,
        "proofability_report_digest": package.proofability_report_digest,
        "evidence_package_digest": package.package_digest,
        "evidence_set_id": admitted.evidence_set_id,
        "evidence_class": admitted.evidence_class,
        "verifier_version": admitted.verifier_version,
        "recalculation_sha256": admitted.recalculated.recalculation_sha256,
        "proofability": "PROVABLE",
        "verdict": verdict,
        "attempted_count": admitted.recalculated.attempted_count,
        "successful_count": admitted.recalculated.successful_count,
        "failed_count": admitted.recalculated.failed_count,
        "p95_ttft_ns": admitted.recalculated.p95_ttft_ns,
        "ttft_definition": admitted.recalculated.ttft_definition,
        "evidence_captured_at": _timestamp(evidence_captured_at),
        "issued_at": _timestamp(issued_at),
        "limitations": _LIMITATIONS,
        "deployment_authorized": False,
        "production_traffic_authorized": False,
        "traffic_expansion_authorized": False,
        "external_authorization_required": True,
    }
    digest_projection = dict(values)
    digest_projection["evidence_captured_at"] = _timestamp_text(evidence_captured_at)
    digest_projection["issued_at"] = _timestamp_text(issued_at)
    digest_projection["limitations"] = list(_LIMITATIONS)
    values["receipt_id"] = _receipt_id_from_projection(digest_projection)
    try:
        return InferencePerformanceQualificationReceiptV1.model_validate(
            values,
            strict=True,
        )
    except (ValidationError, TypeError, ValueError) as error:
        _reject(
            QualificationReceiptCode.INVALID_INPUT,
            "Receipt could not be created from the admitted facts.",
        )
        raise AssertionError("unreachable") from error


def serialize_inference_performance_qualification_receipt(
    receipt: InferencePerformanceQualificationReceiptV1,
) -> bytes:
    if type(receipt) is not InferencePerformanceQualificationReceiptV1:
        _reject(QualificationReceiptCode.INVALID_INPUT, "A typed receipt is required.")
    try:
        content = canonical_json_bytes(receipt.model_dump(mode="json"))
    except (CanonicalizationError, RecursionError, TypeError, ValueError):
        _reject(QualificationReceiptCode.INVALID_INPUT, "Receipt is not canonical JSON.")
    if len(content) > _MAX_RECEIPT_BYTES:
        _reject(QualificationReceiptCode.INVALID_INPUT, "Receipt is oversized.")
    return content


def parse_inference_performance_qualification_receipt(
    content: bytes | Mapping[str, Any],
) -> InferencePerformanceQualificationReceiptV1:
    parsed_from_json = False
    if type(content) is bytes:
        if len(content) > _MAX_RECEIPT_BYTES:
            _reject(QualificationReceiptCode.INVALID_INPUT, "Receipt is oversized.")
        try:
            payload = json.loads(
                content.decode("utf-8"),
                object_pairs_hook=_unique_object,
                parse_float=_reject_float,
                parse_constant=_reject_constant,
            )
            if type(payload) is not dict or canonical_json_bytes(payload) != content:
                raise ValueError("noncanonical receipt")
            parsed_from_json = True
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
            _reject(QualificationReceiptCode.INVALID_INPUT, "Receipt bytes are invalid.")
    elif type(content) is dict:
        payload = content
    else:
        _reject(QualificationReceiptCode.INVALID_INPUT, "Receipt must be bytes or an object.")
    try:
        if parsed_from_json:
            return InferencePerformanceQualificationReceiptV1.model_validate_json(
                content,
                strict=True,
            )
        return InferencePerformanceQualificationReceiptV1.model_validate(payload, strict=True)
    except (ValidationError, TypeError, ValueError):
        _reject(QualificationReceiptCode.INVALID_INPUT, "Receipt failed strict validation.")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate object key")
        result[key] = value
    return result


def _reject_float(_: str) -> None:
    raise ValueError("floating-point values are not supported")


def _reject_constant(_: str) -> None:
    raise ValueError("non-finite values are not supported")


__all__ = [
    "INFERENCE_PERFORMANCE_QUALIFICATION_RECEIPT_CANONICALIZATION_VERSION",
    "INFERENCE_PERFORMANCE_QUALIFICATION_RECEIPT_DIGEST_DOMAIN",
    "INFERENCE_PERFORMANCE_QUALIFICATION_RECEIPT_HASH_VERSION",
    "INFERENCE_PERFORMANCE_QUALIFICATION_RECEIPT_SCHEMA_VERSION",
    "InferencePerformanceQualificationReceiptV1",
    "QualificationReceiptCode",
    "QualificationReceiptRejected",
    "issue_inference_performance_qualification_receipt",
    "parse_inference_performance_qualification_receipt",
    "serialize_inference_performance_qualification_receipt",
]
