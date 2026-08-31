"""Strict, immutable v0.5 qualification-scope and context artifacts.

These artifacts define a qualification question and bind it to one serving
subject. They do not admit evidence, execute work, issue a verdict, establish
freshness, or authorize deployment or traffic. A self-consistent digest proves
only deterministic identity and integrity, never execution, authorship,
chronology, hardware truth, authenticated identity, or authority.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from collections.abc import Mapping
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
from .models import FrozenExitSpecModel
from .serving_subject import (
    ServingSubjectManifestV1,
    serving_subject_digest,
    verify_serving_subject_manifest,
)

QUALIFICATION_SCOPE_SCHEMA_VERSION: Final = "exitspec.qualification-scope.v1"
QUALIFICATION_CONTEXT_SCHEMA_VERSION: Final = "exitspec.qualification-context.v1"
QUALIFICATION_SCOPE_CANONICALIZATION_VERSION: Final = "rfc8785_jcs_v1"
QUALIFICATION_SCOPE_HASH_VERSION: Final = "sha256_v1"
QUALIFICATION_SCOPE_DIGEST_DOMAIN: Final = b"exitspec-qualification-scope-v1\x00"
QUALIFICATION_CONTEXT_DIGEST_DOMAIN: Final = (
    b"exitspec-qualification-context-v1\x00"
)

_MAX_SCOPE_BYTES: Final = 16 * 1024
_MAX_CONTEXT_BYTES: Final = 4 * 1024
_MAX_JSON_DEPTH: Final = 16
_MAX_JSON_NODES: Final = 256
_MAX_JSON_OBJECT_KEYS: Final = 32
_MAX_JSON_ARRAY_ITEMS: Final = 32
_MAX_JSON_STRING_LENGTH: Final = 512
_MAX_JSON_INTEGER: Final = 2_147_483_647
_MAX_POLICY_SECONDS: Final = 31_536_000

_IDENTIFIER_PATTERN: Final = r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$"
_EXACT_VERSION_PATTERN: Final = (
    r"^v?[0-9]+(?:\.[0-9]+){0,2}(?:-[0-9A-Za-z][0-9A-Za-z.-]{0,63})?"
    r"(?:\+[0-9A-Za-z][0-9A-Za-z.-]{0,63})?$"
)
_DIGEST_PATTERN: Final = r"^sha256:[a-f0-9]{64}$"
_UNPINNED_VERSIONS: Final = frozenset(
    {"latest", "main", "master", "head", "default", "stable"}
)


class QualificationScopeValidationCode(str, Enum):
    """Stable, content-safe failure classes at the PR3 artifact boundary."""

    DUPLICATE_FIELD = "DUPLICATE_FIELD"
    EXTRA_FIELD = "EXTRA_FIELD"
    MISSING_FIELD = "MISSING_FIELD"
    WRONG_VERSION = "WRONG_VERSION"
    WRONG_TYPE = "WRONG_TYPE"
    OVERSIZED = "OVERSIZED"
    INVALID_DIGEST = "INVALID_DIGEST"
    INVALID_VALUE = "INVALID_VALUE"
    NON_CANONICAL = "NON_CANONICAL"
    SEMANTIC_INCONSISTENCY = "SEMANTIC_INCONSISTENCY"


class QualificationScopeRejected(ValueError):
    """A scope or context artifact failed one public fail-closed boundary."""

    def __init__(self, code: QualificationScopeValidationCode, message: str) -> None:
        self.code = QualificationScopeValidationCode(code)
        super().__init__(message)


class _JsonBoundaryError(ValueError):
    def __init__(self, code: QualificationScopeValidationCode) -> None:
        self.code = code
        super().__init__(code.value)


def _reject(code: QualificationScopeValidationCode, message: str) -> None:
    raise QualificationScopeRejected(code, message)


class _StrictFrozenQualificationModel(FrozenExitSpecModel):
    """Frozen strict model base for every material PR3 field."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        revalidate_instances="always",
    )


class FrozenContractIdentityV1(_StrictFrozenQualificationModel):
    """One frozen requirement contract and its RFC 8785 canonical digest."""

    contract_id: str = Field(pattern=_IDENTIFIER_PATTERN, max_length=128)
    contract_canonical_digest: str = Field(pattern=_DIGEST_PATTERN)


class WorkloadIdentityV1(_StrictFrozenQualificationModel):
    """One exact workload identity and its canonical digest."""

    workload_id: str = Field(pattern=_IDENTIFIER_PATTERN, max_length=128)
    workload_digest: str = Field(pattern=_DIGEST_PATTERN)


class MeasurementEnvironmentProfileV1(_StrictFrozenQualificationModel):
    """Declared measurement environment and profile identities, versions, and hashes."""

    environment_id: str = Field(pattern=_IDENTIFIER_PATTERN, max_length=128)
    environment_digest: str = Field(pattern=_DIGEST_PATTERN)
    profile_id: str = Field(pattern=_IDENTIFIER_PATTERN, max_length=128)
    profile_version: str = Field(pattern=_EXACT_VERSION_PATTERN, max_length=128)
    profile_digest: str = Field(pattern=_DIGEST_PATTERN)

    @field_validator("profile_version")
    @classmethod
    def require_pinned_profile_version(cls, value: str) -> str:
        if value.casefold() in _UNPINNED_VERSIONS:
            raise ValueError("Measurement profile version must be pinned.")
        return value


class MaximumUseV1(_StrictFrozenQualificationModel):
    """The narrow, non-authorizing maximum use being evaluated."""

    maximum_traffic_percent: int = Field(ge=1, le=5)


class FreshnessPolicyV1(_StrictFrozenQualificationModel):
    """A prospective evidence-age policy without an issuance or validity clock."""

    age_basis: Literal["EVIDENCE_CAPTURED_AT"]
    maximum_evidence_age_seconds: int = Field(ge=1, le=_MAX_POLICY_SECONDS)


class _QualificationScopeUnsignedV1(_StrictFrozenQualificationModel):
    """Validated scope projection used only to derive ``scope_digest``."""

    schema_version: str = Field(
        pattern=rf"^{re.escape(QUALIFICATION_SCOPE_SCHEMA_VERSION)}$",
        max_length=len(QUALIFICATION_SCOPE_SCHEMA_VERSION),
    )
    frozen_contract: FrozenContractIdentityV1
    workload: WorkloadIdentityV1
    measurement_profile: MeasurementEnvironmentProfileV1
    evaluated_use: Literal["CANARY_CONSIDERATION"]
    maximum_use: MaximumUseV1
    freshness_policy: FreshnessPolicyV1 | None
    reference_subject_requirement: Literal["NOT_REQUIRED", "REQUIRED"]
    reference_subject_digest: str | None = Field(pattern=_DIGEST_PATTERN)

    @model_validator(mode="after")
    def require_reference_subject_presence(self) -> _QualificationScopeUnsignedV1:
        required = self.reference_subject_requirement == "REQUIRED"
        if required != (self.reference_subject_digest is not None):
            raise ValueError("Reference subject requirement and digest must agree.")
        return self


def _scope_digest_from_projection(projection: Mapping[str, Any]) -> str:
    try:
        content = canonical_json_bytes(projection)
    except (CanonicalizationError, TypeError, ValueError):
        _reject(
            QualificationScopeValidationCode.INVALID_VALUE,
            "Qualification scope projection is outside the canonical JSON domain.",
        )
    return "sha256:" + hashlib.sha256(
        QUALIFICATION_SCOPE_DIGEST_DOMAIN + content
    ).hexdigest()


class QualificationScopeV1(_QualificationScopeUnsignedV1):
    """The immutable question being asked about one serving subject."""

    scope_digest: str = Field(pattern=_DIGEST_PATTERN)

    @model_validator(mode="after")
    def require_matching_scope_digest(self) -> QualificationScopeV1:
        projection = self.model_dump(mode="json", exclude={"scope_digest"})
        expected = _scope_digest_from_projection(projection)
        if not hmac.compare_digest(self.scope_digest, expected):
            raise ValueError("scope_digest does not match the unsigned projection.")
        return self


class _QualificationContextUnsignedV1(_StrictFrozenQualificationModel):
    """Validated context projection used only to derive its binding digest."""

    schema_version: str = Field(
        pattern=rf"^{re.escape(QUALIFICATION_CONTEXT_SCHEMA_VERSION)}$",
        max_length=len(QUALIFICATION_CONTEXT_SCHEMA_VERSION),
    )
    subject_digest: str = Field(pattern=_DIGEST_PATTERN)
    scope_digest: str = Field(pattern=_DIGEST_PATTERN)
    protocol_id: str = Field(pattern=_IDENTIFIER_PATTERN, max_length=128)
    protocol_version: str = Field(pattern=_EXACT_VERSION_PATTERN, max_length=128)

    @field_validator("protocol_version")
    @classmethod
    def require_pinned_protocol_version(cls, value: str) -> str:
        if value.casefold() in _UNPINNED_VERSIONS:
            raise ValueError("Protocol version must be pinned.")
        return value


def _context_digest_from_projection(projection: Mapping[str, Any]) -> str:
    try:
        content = canonical_json_bytes(projection)
    except (CanonicalizationError, TypeError, ValueError):
        _reject(
            QualificationScopeValidationCode.INVALID_VALUE,
            "Qualification context projection is outside the canonical JSON domain.",
        )
    return "sha256:" + hashlib.sha256(
        QUALIFICATION_CONTEXT_DIGEST_DOMAIN + content
    ).hexdigest()


class QualificationContextV1(_QualificationContextUnsignedV1):
    """The immutable binding of one subject, scope, and exact protocol version."""

    qualification_context_digest: str = Field(pattern=_DIGEST_PATTERN)

    @model_validator(mode="after")
    def require_matching_context_digest(self) -> QualificationContextV1:
        projection = self.model_dump(
            mode="json", exclude={"qualification_context_digest"}
        )
        expected = _context_digest_from_projection(projection)
        if not hmac.compare_digest(self.qualification_context_digest, expected):
            raise ValueError(
                "qualification_context_digest does not match the unsigned projection."
            )
        return self


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _JsonBoundaryError(QualificationScopeValidationCode.DUPLICATE_FIELD)
        result[key] = value
    return result


def _bounded_integer(raw: str) -> int:
    value = int(raw)
    if abs(value) > _MAX_JSON_INTEGER:
        raise _JsonBoundaryError(QualificationScopeValidationCode.OVERSIZED)
    return value


def _reject_float(_: str) -> None:
    raise _JsonBoundaryError(QualificationScopeValidationCode.INVALID_VALUE)


def _reject_constant(_: str) -> None:
    raise _JsonBoundaryError(QualificationScopeValidationCode.INVALID_VALUE)


def _walk_bounded_json(value: object, *, depth: int, node_count: list[int]) -> None:
    node_count[0] += 1
    if node_count[0] > _MAX_JSON_NODES or depth > _MAX_JSON_DEPTH:
        raise _JsonBoundaryError(QualificationScopeValidationCode.OVERSIZED)
    if type(value) is str:
        if len(value) > _MAX_JSON_STRING_LENGTH:
            raise _JsonBoundaryError(QualificationScopeValidationCode.OVERSIZED)
        return
    if value is None or type(value) in {bool, int}:
        return
    if type(value) is dict:
        if len(value) > _MAX_JSON_OBJECT_KEYS:
            raise _JsonBoundaryError(QualificationScopeValidationCode.OVERSIZED)
        for key, child in value.items():
            if type(key) is not str or len(key) > _MAX_JSON_STRING_LENGTH:
                raise _JsonBoundaryError(QualificationScopeValidationCode.OVERSIZED)
            _walk_bounded_json(child, depth=depth + 1, node_count=node_count)
        return
    if type(value) is list:
        if len(value) > _MAX_JSON_ARRAY_ITEMS:
            raise _JsonBoundaryError(QualificationScopeValidationCode.OVERSIZED)
        for child in value:
            _walk_bounded_json(child, depth=depth + 1, node_count=node_count)
        return
    raise _JsonBoundaryError(QualificationScopeValidationCode.WRONG_TYPE)


def _decode_object(
    content: bytes, *, maximum_bytes: int, require_canonical: bool, label: str
) -> dict[str, Any]:
    if len(content) > maximum_bytes:
        _reject(
            QualificationScopeValidationCode.OVERSIZED,
            f"{label} exceeds its byte limit.",
        )
    try:
        payload = json.loads(
            content.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_int=_bounded_integer,
            parse_float=_reject_float,
            parse_constant=_reject_constant,
        )
    except UnicodeDecodeError:
        _reject(
            QualificationScopeValidationCode.WRONG_TYPE,
            f"{label} is not UTF-8 JSON.",
        )
    except _JsonBoundaryError as error:
        _reject(error.code, f"{label} failed its JSON boundary.")
    except (json.JSONDecodeError, TypeError, ValueError):
        _reject(
            QualificationScopeValidationCode.WRONG_TYPE,
            f"{label} is not one valid JSON object.",
        )
    if type(payload) is not dict:
        _reject(
            QualificationScopeValidationCode.WRONG_TYPE,
            f"{label} must be one JSON object.",
        )
    try:
        _walk_bounded_json(payload, depth=0, node_count=[0])
        canonical = canonical_json_bytes(payload)
    except _JsonBoundaryError as error:
        _reject(error.code, f"{label} failed its JSON boundary.")
    except (CanonicalizationError, TypeError, ValueError):
        _reject(
            QualificationScopeValidationCode.INVALID_VALUE,
            f"{label} is outside the canonical JSON domain.",
        )
    if require_canonical and canonical != content:
        _reject(
            QualificationScopeValidationCode.NON_CANONICAL,
            f"{label} is not RFC 8785 canonical JSON.",
        )
    return payload


def _load_object(
    value: bytes | Mapping[str, Any], *, maximum_bytes: int, label: str
) -> dict[str, Any]:
    if type(value) is bytes:
        return _decode_object(
            value,
            maximum_bytes=maximum_bytes,
            require_canonical=True,
            label=label,
        )
    if type(value) is not dict:
        _reject(
            QualificationScopeValidationCode.WRONG_TYPE,
            f"{label} must be bytes or one JSON object.",
        )
    try:
        content = canonical_json_bytes(value)
    except (CanonicalizationError, TypeError, ValueError):
        _reject(
            QualificationScopeValidationCode.INVALID_VALUE,
            f"{label} is outside the canonical JSON domain.",
        )
    return _decode_object(
        content,
        maximum_bytes=maximum_bytes,
        require_canonical=False,
        label=label,
    )


def _classify_validation_error(error: ValidationError) -> QualificationScopeValidationCode:
    for detail in error.errors():
        location = tuple(detail.get("loc", ()))
        error_type = str(detail.get("type", ""))
        if error_type == "extra_forbidden":
            return QualificationScopeValidationCode.EXTRA_FIELD
        if error_type == "missing":
            return QualificationScopeValidationCode.MISSING_FIELD
        if location and location[0] == "schema_version":
            return QualificationScopeValidationCode.WRONG_VERSION
        if any(str(part).endswith("digest") for part in location):
            return QualificationScopeValidationCode.INVALID_DIGEST
        if error_type in {"too_long", "string_too_long", "too_short"}:
            return QualificationScopeValidationCode.OVERSIZED
        if error_type.endswith("_type") or error_type in {
            "model_type",
            "tuple_type",
            "list_type",
        }:
            return QualificationScopeValidationCode.WRONG_TYPE
        context = detail.get("ctx")
        if isinstance(context, dict) and "error" in context:
            message = str(context["error"])
            if "must agree" in message:
                return QualificationScopeValidationCode.SEMANTIC_INCONSISTENCY
            if "digest" in message:
                return QualificationScopeValidationCode.INVALID_DIGEST
    return QualificationScopeValidationCode.INVALID_VALUE


def _validate_model(model_type: type[Any], payload: dict[str, Any], *, label: str) -> Any:
    try:
        return model_type.model_validate_json(canonical_json_bytes(payload), strict=True)
    except ValidationError as error:
        _reject(_classify_validation_error(error), f"{label} failed strict validation.")
    except (CanonicalizationError, TypeError, ValueError):
        _reject(
            QualificationScopeValidationCode.INVALID_VALUE,
            f"{label} failed strict validation.",
        )


def _validated_typed_model(value: object, model_type: type[Any], *, label: str) -> Any:
    if type(value) is not model_type:
        _reject(
            QualificationScopeValidationCode.WRONG_TYPE,
            f"A typed {model_type.__name__} is required.",
        )
    try:
        raw_state = object.__getattribute__(value, "__dict__")
        extra_state = object.__getattribute__(value, "__pydantic_extra__")
    except AttributeError:
        _reject(
            QualificationScopeValidationCode.WRONG_TYPE,
            f"{label} has no inspectable raw state.",
        )
    expected_fields = set(model_type.model_fields)
    if type(raw_state) is not dict:
        _reject(
            QualificationScopeValidationCode.WRONG_TYPE,
            f"{label} raw state must be one object.",
        )
    if set(raw_state) - expected_fields or extra_state:
        _reject(
            QualificationScopeValidationCode.EXTRA_FIELD,
            f"{label} contains undocumented raw fields.",
        )
    if expected_fields - set(raw_state):
        _reject(
            QualificationScopeValidationCode.MISSING_FIELD,
            f"{label} is missing a raw field.",
        )
    try:
        serialized_state = value.model_dump(mode="json", warnings="error")
    except (TypeError, ValueError):
        _reject(
            QualificationScopeValidationCode.INVALID_VALUE,
            f"{label} has an unsafe raw field value.",
        )
    validated = _validate_model(model_type, serialized_state, label=label)
    if type(validated) is not model_type:
        _reject(
            QualificationScopeValidationCode.WRONG_TYPE,
            f"{label} has the wrong typed model.",
        )
    return validated


def _validated_scope(value: object) -> QualificationScopeV1:
    return _validated_typed_model(
        value, QualificationScopeV1, label="Qualification scope"
    )


def _validated_context(value: object) -> QualificationContextV1:
    return _validated_typed_model(
        value, QualificationContextV1, label="Qualification context"
    )


def create_qualification_scope(value: Mapping[str, Any]) -> QualificationScopeV1:
    """Create one digest-bound scope from a strict unsigned projection."""

    if type(value) is not dict:
        _reject(
            QualificationScopeValidationCode.WRONG_TYPE,
            "Qualification scope input must be one JSON object.",
        )
    if "scope_digest" in value:
        _reject(
            QualificationScopeValidationCode.EXTRA_FIELD,
            "Qualification scope input must not include a derived digest.",
        )
    unsigned = _validate_model(
        _QualificationScopeUnsignedV1,
        _load_object(
            value,
            maximum_bytes=_MAX_SCOPE_BYTES,
            label="Qualification scope input",
        ),
        label="Qualification scope input",
    )
    projection = unsigned.model_dump(mode="json")
    manifest = _validate_model(
        QualificationScopeV1,
        {**projection, "scope_digest": _scope_digest_from_projection(projection)},
        label="Qualification scope",
    )
    if type(manifest) is not QualificationScopeV1:
        _reject(
            QualificationScopeValidationCode.WRONG_TYPE,
            "Qualification scope has the wrong typed model.",
        )
    return manifest


def parse_qualification_scope(
    value: bytes | Mapping[str, Any],
) -> QualificationScopeV1:
    """Strictly parse one canonical digest-bound qualification scope."""

    scope = _validate_model(
        QualificationScopeV1,
        _load_object(
            value,
            maximum_bytes=_MAX_SCOPE_BYTES,
            label="Qualification scope",
        ),
        label="Qualification scope",
    )
    if type(scope) is not QualificationScopeV1:
        _reject(
            QualificationScopeValidationCode.WRONG_TYPE,
            "Qualification scope has the wrong typed model.",
        )
    return scope


def canonical_qualification_scope_projection(
    value: QualificationScopeV1,
) -> dict[str, Any]:
    """Return the validated unsigned scope projection used for digest derivation."""

    return _validated_scope(value).model_dump(mode="json", exclude={"scope_digest"})


def qualification_scope_digest(value: QualificationScopeV1) -> str:
    """Return the domain-separated identity digest for one validated scope."""

    return _scope_digest_from_projection(canonical_qualification_scope_projection(value))


def verify_qualification_scope(value: object) -> bool:
    """Return whether a typed scope is strict, immutable, and digest-valid."""

    try:
        validated = _validated_scope(value)
        return hmac.compare_digest(
            validated.scope_digest, qualification_scope_digest(validated)
        )
    except QualificationScopeRejected:
        return False


def serialize_qualification_scope(value: QualificationScopeV1) -> bytes:
    """Return canonical RFC 8785 bytes for one validated qualification scope."""

    validated = _validated_scope(value)
    try:
        content = canonical_json_bytes(validated.model_dump(mode="json"))
    except (CanonicalizationError, TypeError, ValueError):
        _reject(
            QualificationScopeValidationCode.INVALID_VALUE,
            "Qualification scope is outside the canonical JSON domain.",
        )
    if parse_qualification_scope(content) != validated:
        _reject(
            QualificationScopeValidationCode.SEMANTIC_INCONSISTENCY,
            "Qualification scope changed during serialization.",
        )
    return content


def create_qualification_context(
    subject: ServingSubjectManifestV1,
    scope: QualificationScopeV1,
    *,
    protocol_id: str,
    protocol_version: str,
) -> QualificationContextV1:
    """Bind validated subject and scope identities to one exact protocol version."""

    if not verify_serving_subject_manifest(subject):
        _reject(
            QualificationScopeValidationCode.INVALID_VALUE,
            "Qualification context requires a valid serving subject.",
        )
    if not verify_qualification_scope(scope):
        _reject(
            QualificationScopeValidationCode.INVALID_VALUE,
            "Qualification context requires a valid qualification scope.",
        )
    unsigned = _validate_model(
        _QualificationContextUnsignedV1,
        {
            "schema_version": QUALIFICATION_CONTEXT_SCHEMA_VERSION,
            "subject_digest": serving_subject_digest(subject),
            "scope_digest": qualification_scope_digest(scope),
            "protocol_id": protocol_id,
            "protocol_version": protocol_version,
        },
        label="Qualification context input",
    )
    projection = unsigned.model_dump(mode="json")
    context = _validate_model(
        QualificationContextV1,
        {
            **projection,
            "qualification_context_digest": _context_digest_from_projection(
                projection
            ),
        },
        label="Qualification context",
    )
    if type(context) is not QualificationContextV1:
        _reject(
            QualificationScopeValidationCode.WRONG_TYPE,
            "Qualification context has the wrong typed model.",
        )
    return context


def parse_qualification_context(
    value: bytes | Mapping[str, Any],
) -> QualificationContextV1:
    """Strictly parse one canonical digest-bound qualification context."""

    context = _validate_model(
        QualificationContextV1,
        _load_object(
            value,
            maximum_bytes=_MAX_CONTEXT_BYTES,
            label="Qualification context",
        ),
        label="Qualification context",
    )
    if type(context) is not QualificationContextV1:
        _reject(
            QualificationScopeValidationCode.WRONG_TYPE,
            "Qualification context has the wrong typed model.",
        )
    return context


def canonical_qualification_context_projection(
    value: QualificationContextV1,
) -> dict[str, Any]:
    """Return the validated unsigned context projection used for digest derivation."""

    return _validated_context(value).model_dump(
        mode="json", exclude={"qualification_context_digest"}
    )


def qualification_context_digest(value: QualificationContextV1) -> str:
    """Return the domain-separated digest for one validated qualification context."""

    return _context_digest_from_projection(canonical_qualification_context_projection(value))


def verify_qualification_context(value: object) -> bool:
    """Return whether a typed context is strict, immutable, and digest-valid."""

    try:
        validated = _validated_context(value)
        return hmac.compare_digest(
            validated.qualification_context_digest,
            qualification_context_digest(validated),
        )
    except QualificationScopeRejected:
        return False


def serialize_qualification_context(value: QualificationContextV1) -> bytes:
    """Return canonical RFC 8785 bytes for one validated qualification context."""

    validated = _validated_context(value)
    try:
        content = canonical_json_bytes(validated.model_dump(mode="json"))
    except (CanonicalizationError, TypeError, ValueError):
        _reject(
            QualificationScopeValidationCode.INVALID_VALUE,
            "Qualification context is outside the canonical JSON domain.",
        )
    if parse_qualification_context(content) != validated:
        _reject(
            QualificationScopeValidationCode.SEMANTIC_INCONSISTENCY,
            "Qualification context changed during serialization.",
        )
    return content


__all__ = [
    "QUALIFICATION_CONTEXT_DIGEST_DOMAIN",
    "QUALIFICATION_CONTEXT_SCHEMA_VERSION",
    "QUALIFICATION_SCOPE_CANONICALIZATION_VERSION",
    "QUALIFICATION_SCOPE_DIGEST_DOMAIN",
    "QUALIFICATION_SCOPE_HASH_VERSION",
    "QUALIFICATION_SCOPE_SCHEMA_VERSION",
    "FreshnessPolicyV1",
    "FrozenContractIdentityV1",
    "MaximumUseV1",
    "MeasurementEnvironmentProfileV1",
    "QualificationContextV1",
    "QualificationScopeRejected",
    "QualificationScopeV1",
    "QualificationScopeValidationCode",
    "WorkloadIdentityV1",
    "canonical_qualification_context_projection",
    "canonical_qualification_scope_projection",
    "create_qualification_context",
    "create_qualification_scope",
    "parse_qualification_context",
    "parse_qualification_scope",
    "qualification_context_digest",
    "qualification_scope_digest",
    "serialize_qualification_context",
    "serialize_qualification_scope",
    "verify_qualification_context",
    "verify_qualification_scope",
]
