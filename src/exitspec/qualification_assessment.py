"""Immutable v0.5 qualification-validity assessments.

PR10 evaluates whether one PR9 receipt still applies to the exact requested
subject, scope, protocol context, and declared freshness policy.  It never
rewrites the receipt, re-runs evidence, or grants deployment or traffic
authority.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any, Final, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from .canonical import CanonicalizationError, canonical_json_bytes
from .qualification_receipts import (
    InferencePerformanceQualificationReceiptV1,
    parse_inference_performance_qualification_receipt,
)
from .qualification_scope import (
    QualificationContextV1,
    QualificationScopeV1,
    qualification_context_digest,
    qualification_scope_digest,
    verify_qualification_context,
    verify_qualification_scope,
)
from .serving_subject import (
    ServingSubjectManifestV1,
    serving_subject_digest,
    verify_serving_subject_manifest,
)

QUALIFICATION_ASSESSMENT_SCHEMA_VERSION: Final = (
    "exitspec.qualification-assessment.v1"
)
QUALIFICATION_ASSESSMENT_CANONICALIZATION_VERSION: Final = "rfc8785_jcs_v1"
QUALIFICATION_ASSESSMENT_HASH_VERSION: Final = "sha256_v1"
QUALIFICATION_ASSESSMENT_DIGEST_DOMAIN: Final = (
    b"exitspec-qualification-assessment-v1\x00"
)
QUALIFICATION_PROTOCOL_ID: Final = "inference-performance-qualification"
QUALIFICATION_PROTOCOL_VERSION: Final = "1.0.0"

_DIGEST_PATTERN: Final = r"^sha256:[a-f0-9]{64}$"
_ASSESSMENT_ID_PATTERN: Final = r"^qaa_[a-f0-9]{64}$"
_MAX_ASSESSMENT_BYTES: Final = 32 * 1024
_LIMITATIONS: Final = (
    "A current receipt remains bounded by its evidence, verifier, and authority limitations.",
    "A validity assessment is not deployment or production-traffic authorization.",
    "Receipt timestamps are declared facts; this assessment does not attest chronology.",
)


class QualificationValidity(str, Enum):
    """Applicability state for one validated qualification receipt."""

    CURRENT = "CURRENT"
    STALE = "STALE"
    EXPIRED = "EXPIRED"
    INVALID = "INVALID"


class QualificationAssessmentReason(str, Enum):
    """Stable, content-safe reason for the resulting validity state."""

    CURRENT = "CURRENT"
    SUBJECT_CHANGED = "SUBJECT_CHANGED"
    SCOPE_CHANGED = "SCOPE_CHANGED"
    CONTEXT_CHANGED = "CONTEXT_CHANGED"
    EXPIRED = "EXPIRED"
    INVALID_RECEIPT = "INVALID_RECEIPT"
    INVALID_CONTEXT = "INVALID_CONTEXT"
    INVALID_EXPIRY = "INVALID_EXPIRY"
    UNSUPPORTED_PROTOCOL = "UNSUPPORTED_PROTOCOL"


class QualificationAssessmentRejected(ValueError):
    """The assessment request itself is outside the typed API boundary."""


class _StrictFrozenAssessmentModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        revalidate_instances="always",
    )


class QualificationAssessmentV1(_StrictFrozenAssessmentModel):
    """The immutable applicability result for one exact receipt request."""

    schema_version: Literal[QUALIFICATION_ASSESSMENT_SCHEMA_VERSION]
    canonicalization_version: Literal[QUALIFICATION_ASSESSMENT_CANONICALIZATION_VERSION]
    hash_version: Literal[QUALIFICATION_ASSESSMENT_HASH_VERSION]
    assessment_id: str = Field(pattern=_ASSESSMENT_ID_PATTERN)
    receipt_id: str | None = Field(default=None, pattern=r"^qrc_[a-f0-9]{64}$")
    subject_digest: str | None = Field(default=None, pattern=_DIGEST_PATTERN)
    scope_digest: str | None = Field(default=None, pattern=_DIGEST_PATTERN)
    qualification_context_digest: str | None = Field(
        default=None, pattern=_DIGEST_PATTERN
    )
    purpose: Literal["CANARY_CONSIDERATION"] | None = None
    proofability: Literal["PROVABLE"] | None = None
    verdict: Literal["PASS", "FAIL", "NOT_PROVEN"] | None = None
    validity: Literal["CURRENT", "STALE", "EXPIRED", "INVALID"]
    reason: Literal[
        "CURRENT",
        "SUBJECT_CHANGED",
        "SCOPE_CHANGED",
        "CONTEXT_CHANGED",
        "EXPIRED",
        "INVALID_RECEIPT",
        "INVALID_CONTEXT",
        "INVALID_EXPIRY",
        "UNSUPPORTED_PROTOCOL",
    ]
    evidence_captured_at: datetime | None = None
    expires_at: datetime | None = None
    assessed_at: datetime
    limitations: tuple[str, ...] = Field(min_length=1, max_length=8)
    deployment_authorized: Literal[False] = False
    production_traffic_authorized: Literal[False] = False
    traffic_expansion_authorized: Literal[False] = False
    external_authorization_required: Literal[True] = True

    @field_validator("evidence_captured_at", "expires_at", "assessed_at")
    @classmethod
    def require_utc_timestamps(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must be timezone-aware")
        return value.astimezone(UTC)

    @field_validator("limitations")
    @classmethod
    def require_canonical_limitations(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if value != tuple(sorted(set(value))) or value != _LIMITATIONS:
            raise ValueError("Assessment limitations must use the fixed canonical set.")
        return value

    @model_validator(mode="after")
    def require_assessment_id_binding(self) -> QualificationAssessmentV1:
        projection = self.model_dump(mode="json", exclude={"assessment_id"})
        expected = _assessment_id_from_projection(projection)
        if not hmac.compare_digest(self.assessment_id, expected):
            raise ValueError("assessment_id does not bind the assessment projection.")
        if self.validity == QualificationValidity.INVALID:
            if any(
                value is not None
                for value in (
                    self.receipt_id,
                    self.subject_digest,
                    self.scope_digest,
                    self.qualification_context_digest,
                    self.purpose,
                    self.proofability,
                    self.verdict,
                    self.evidence_captured_at,
                    self.expires_at,
                )
            ):
                raise ValueError("Invalid assessments cannot carry qualification facts.")
        elif self.receipt_id is None or self.subject_digest is None:
            raise ValueError("Applicable assessments require receipt and identity facts.")
        return self


def _assessment_id_from_projection(projection: Mapping[str, Any]) -> str:
    try:
        content = canonical_json_bytes(projection)
    except (CanonicalizationError, RecursionError, TypeError, ValueError) as error:
        raise QualificationAssessmentRejected(
            "Assessment projection is not canonical JSON."
        ) from error
    return "qaa_" + hashlib.sha256(
        QUALIFICATION_ASSESSMENT_DIGEST_DOMAIN + content
    ).hexdigest()


def _timestamp(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise QualificationAssessmentRejected("assessed_at must be timezone-aware")
    return value.astimezone(UTC)


def _timestamp_text(value: datetime | None) -> str | None:
    if value is None:
        return None
    return _timestamp(value).isoformat().replace("+00:00", "Z")


def _invalid_assessment(
    *, assessed_at: datetime, reason: QualificationAssessmentReason
) -> QualificationAssessmentV1:
    values: dict[str, Any] = {
        "schema_version": QUALIFICATION_ASSESSMENT_SCHEMA_VERSION,
        "canonicalization_version": QUALIFICATION_ASSESSMENT_CANONICALIZATION_VERSION,
        "hash_version": QUALIFICATION_ASSESSMENT_HASH_VERSION,
        "receipt_id": None,
        "subject_digest": None,
        "scope_digest": None,
        "qualification_context_digest": None,
        "purpose": None,
        "proofability": None,
        "verdict": None,
        "validity": QualificationValidity.INVALID.value,
        "reason": reason.value,
        "evidence_captured_at": None,
        "expires_at": None,
        "assessed_at": _timestamp(assessed_at),
        "limitations": _LIMITATIONS,
        "deployment_authorized": False,
        "production_traffic_authorized": False,
        "traffic_expansion_authorized": False,
        "external_authorization_required": True,
    }
    digest_values = dict(values)
    digest_values["assessed_at"] = _timestamp_text(assessed_at)
    digest_values["limitations"] = list(_LIMITATIONS)
    values["assessment_id"] = _assessment_id_from_projection(digest_values)
    try:
        return QualificationAssessmentV1.model_validate(values, strict=True)
    except (ValidationError, TypeError, ValueError) as error:
        raise QualificationAssessmentRejected(
            "Invalid assessment could not be created."
        ) from error


def _applicable_assessment(
    *,
    receipt: InferencePerformanceQualificationReceiptV1,
    subject_digest: str,
    scope_digest: str,
    context_digest: str,
    purpose: Literal["CANARY_CONSIDERATION"],
    validity: QualificationValidity,
    reason: QualificationAssessmentReason,
    assessed_at: datetime,
    expires_at: datetime | None,
) -> QualificationAssessmentV1:
    values: dict[str, Any] = {
        "schema_version": QUALIFICATION_ASSESSMENT_SCHEMA_VERSION,
        "canonicalization_version": QUALIFICATION_ASSESSMENT_CANONICALIZATION_VERSION,
        "hash_version": QUALIFICATION_ASSESSMENT_HASH_VERSION,
        "receipt_id": receipt.receipt_id,
        "subject_digest": subject_digest,
        "scope_digest": scope_digest,
        "qualification_context_digest": context_digest,
        "purpose": purpose,
        "proofability": receipt.proofability,
        "verdict": receipt.verdict,
        "validity": validity.value,
        "reason": reason.value,
        "evidence_captured_at": receipt.evidence_captured_at,
        "expires_at": expires_at,
        "assessed_at": _timestamp(assessed_at),
        "limitations": _LIMITATIONS,
        "deployment_authorized": False,
        "production_traffic_authorized": False,
        "traffic_expansion_authorized": False,
        "external_authorization_required": True,
    }
    digest_values = dict(values)
    for key in ("evidence_captured_at", "expires_at", "assessed_at"):
        digest_values[key] = _timestamp_text(values[key])
    digest_values["limitations"] = list(_LIMITATIONS)
    values["assessment_id"] = _assessment_id_from_projection(digest_values)
    try:
        return QualificationAssessmentV1.model_validate(values, strict=True)
    except (ValidationError, TypeError, ValueError) as error:
        raise QualificationAssessmentRejected(
            "Assessment could not be created from the validated receipt."
        ) from error


def assess_inference_qualification(
    receipt: InferencePerformanceQualificationReceiptV1
    | bytes
    | Mapping[str, Any],
    subject: ServingSubjectManifestV1,
    scope: QualificationScopeV1,
    context: QualificationContextV1,
    *,
    assessed_at: datetime,
) -> QualificationAssessmentV1:
    """Assess one receipt against the exact current request without mutation."""

    normalized_assessed_at = _timestamp(assessed_at)
    try:
        if type(receipt) is InferencePerformanceQualificationReceiptV1:
            validated_receipt = InferencePerformanceQualificationReceiptV1.model_validate(
                receipt, strict=True
            )
        else:
            validated_receipt = parse_inference_performance_qualification_receipt(receipt)
    except (TypeError, ValueError, QualificationAssessmentRejected):
        return _invalid_assessment(
            assessed_at=normalized_assessed_at,
            reason=QualificationAssessmentReason.INVALID_RECEIPT,
        )

    if not (
        type(subject) is ServingSubjectManifestV1
        and type(scope) is QualificationScopeV1
        and type(context) is QualificationContextV1
        and verify_serving_subject_manifest(subject)
        and verify_qualification_scope(scope)
        and verify_qualification_context(context)
    ):
        return _invalid_assessment(
            assessed_at=normalized_assessed_at,
            reason=QualificationAssessmentReason.INVALID_CONTEXT,
        )

    current_subject_digest = serving_subject_digest(subject)
    current_scope_digest = qualification_scope_digest(scope)
    current_context_digest = qualification_context_digest(context)
    if (
        context.subject_digest != current_subject_digest
        or context.scope_digest != current_scope_digest
    ):
        return _invalid_assessment(
            assessed_at=normalized_assessed_at,
            reason=QualificationAssessmentReason.INVALID_CONTEXT,
        )
    if (
        context.protocol_id != QUALIFICATION_PROTOCOL_ID
        or context.protocol_version != QUALIFICATION_PROTOCOL_VERSION
    ):
        return _invalid_assessment(
            assessed_at=normalized_assessed_at,
            reason=QualificationAssessmentReason.UNSUPPORTED_PROTOCOL,
        )

    if current_subject_digest != validated_receipt.subject_digest:
        validity = QualificationValidity.STALE
        reason = QualificationAssessmentReason.SUBJECT_CHANGED
    elif current_scope_digest != validated_receipt.scope_digest:
        validity = QualificationValidity.STALE
        reason = QualificationAssessmentReason.SCOPE_CHANGED
    elif current_context_digest != validated_receipt.qualification_context_digest:
        validity = QualificationValidity.STALE
        reason = QualificationAssessmentReason.CONTEXT_CHANGED
    else:
        validity = QualificationValidity.CURRENT
        reason = QualificationAssessmentReason.CURRENT

    expires_at: datetime | None = None
    if validity is QualificationValidity.CURRENT and scope.freshness_policy is not None:
        try:
            expires_at = validated_receipt.evidence_captured_at + timedelta(
                seconds=scope.freshness_policy.maximum_evidence_age_seconds
            )
        except OverflowError:
            return _invalid_assessment(
                assessed_at=normalized_assessed_at,
                reason=QualificationAssessmentReason.INVALID_EXPIRY,
            )
        if normalized_assessed_at >= expires_at:
            validity = QualificationValidity.EXPIRED
            reason = QualificationAssessmentReason.EXPIRED

    return _applicable_assessment(
        receipt=validated_receipt,
        subject_digest=current_subject_digest,
        scope_digest=current_scope_digest,
        context_digest=current_context_digest,
        purpose=scope.evaluated_use,
        validity=validity,
        reason=reason,
        assessed_at=normalized_assessed_at,
        expires_at=expires_at,
    )


def serialize_qualification_assessment(value: QualificationAssessmentV1) -> bytes:
    if type(value) is not QualificationAssessmentV1:
        raise QualificationAssessmentRejected("A typed assessment is required.")
    try:
        content = canonical_json_bytes(value.model_dump(mode="json"))
    except (CanonicalizationError, RecursionError, TypeError, ValueError) as error:
        raise QualificationAssessmentRejected(
            "Assessment is not canonical JSON."
        ) from error
    if len(content) > _MAX_ASSESSMENT_BYTES:
        raise QualificationAssessmentRejected("Assessment is oversized.")
    return content


def parse_qualification_assessment(
    content: bytes | Mapping[str, Any],
) -> QualificationAssessmentV1:
    """Strictly parse canonical assessment bytes or a typed object mapping."""

    if type(content) is bytes:
        if len(content) > _MAX_ASSESSMENT_BYTES:
            raise QualificationAssessmentRejected("Assessment is oversized.")
        try:
            payload = json.loads(
                content.decode("utf-8"), object_pairs_hook=_unique_object
            )
            if type(payload) is not dict or canonical_json_bytes(payload) != content:
                raise ValueError("noncanonical assessment")
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as error:
            raise QualificationAssessmentRejected("Assessment bytes are invalid.") from error
        try:
            return QualificationAssessmentV1.model_validate_json(content, strict=True)
        except (ValidationError, TypeError, ValueError) as error:
            raise QualificationAssessmentRejected(
                "Assessment failed strict validation."
            ) from error
    if type(content) is not dict:
        raise QualificationAssessmentRejected("Assessment must be bytes or an object.")
    try:
        return QualificationAssessmentV1.model_validate_json(
            canonical_json_bytes(content), strict=True
        )
    except (ValidationError, TypeError, ValueError) as error:
        raise QualificationAssessmentRejected(
            "Assessment failed strict validation."
        ) from error


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate object key")
        result[key] = value
    return result


__all__ = [
    "QUALIFICATION_ASSESSMENT_CANONICALIZATION_VERSION",
    "QUALIFICATION_ASSESSMENT_DIGEST_DOMAIN",
    "QUALIFICATION_ASSESSMENT_HASH_VERSION",
    "QUALIFICATION_ASSESSMENT_SCHEMA_VERSION",
    "QUALIFICATION_PROTOCOL_ID",
    "QUALIFICATION_PROTOCOL_VERSION",
    "QualificationAssessmentReason",
    "QualificationAssessmentRejected",
    "QualificationAssessmentV1",
    "QualificationValidity",
    "assess_inference_qualification",
    "parse_qualification_assessment",
    "serialize_qualification_assessment",
]
