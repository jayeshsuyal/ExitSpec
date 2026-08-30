"""B9 routing qualification contract and campaign-evidence boundary.

This module freezes the vocabulary needed to plan a routing qualification
campaign.  It deliberately stops before router execution, multi-run
reduction, confidence calculations, acceptance receipts, or a verdict.

The frozen criterion is a member of the existing :class:`POCContract` union;
the evidence fixture model is separate and may carry observed IDs only for
synthetic protocol tests.  A producer can seal facts, but neither a producer
nor a router can assign ExitSpec's acceptance verdict.
"""

from __future__ import annotations

from enum import Enum
import hashlib
import json
import re
from typing import Any, Final, Mapping

from pydantic import Field, ValidationError, field_validator

from .canonical import CanonicalizationError, canonical_json_bytes
from .contracts import contract_digest, verify_contract_digest
from .models import (
    ExitSpecModel,
    POCContract,
    RoutingQualificationCriterionV1,
    SHA256_PATTERN,
)


ROUTING_QUALIFICATION_PROTOCOL_ID: Final = "routing_qualification_v1"
ROUTING_QUALIFICATION_SCHEMA_VERSION: Final = "exitspec.routing-qualification.v1"
ROUTING_QUALIFICATION_CANONICALIZATION_VERSION: Final = "rfc8785_jcs_v1"
ROUTING_QUALIFICATION_HASH_VERSION: Final = "sha256_v1"
ROUTING_QUALIFICATION_EVIDENCE_FIXTURE_SCHEMA_VERSION: Final = (
    "exitspec.routing-qualification-evidence-fixture.v1"
)

_MAX_CONTRACT_BYTES: Final = 256 * 1024
_MAX_EVIDENCE_BYTES: Final = 128 * 1024
_MAX_JSON_DEPTH: Final = 16
_MAX_JSON_OBJECT_KEYS: Final = 64
_MAX_JSON_ARRAY_ITEMS: Final = 1_000
_MAX_JSON_STRING_LENGTH: Final = 4_096
_MAX_JSON_INTEGER: Final = 2_147_483_647
_SYNTHETIC_RUN_ID = re.compile(r"^synthetic-routing-run-[a-z0-9-]{1,48}$")
_SYNTHETIC_CAPSULE_ID = re.compile(r"^synthetic-routing-capsule-[a-z0-9-]{1,48}$")
_SYNTHETIC_RECEIPT_ID = re.compile(r"^synthetic-route-receipt-[a-z0-9-]{1,48}$")

# The domain model remains named as a criterion because it is inserted into
# the existing POCContract criterion union.  This alias makes the protocol
# vocabulary discoverable to callers without creating another contract type.
RoutingQualificationContractV1 = RoutingQualificationCriterionV1


class RoutingQualificationValidationCode(str, Enum):
    """Stable fail-closed reasons for B9 contract/evidence parsing."""

    DUPLICATE_FIELD = "DUPLICATE_FIELD"
    EXTRA_FIELD = "EXTRA_FIELD"
    MISSING_FIELD = "MISSING_FIELD"
    WRONG_VERSION = "WRONG_VERSION"
    WRONG_TYPE = "WRONG_TYPE"
    OVERSIZED = "OVERSIZED"
    INVALID_DIGEST = "INVALID_DIGEST"
    INVALID_BOUND = "INVALID_BOUND"
    INVALID_VALUE = "INVALID_VALUE"
    SEMANTIC_INCONSISTENCY = "SEMANTIC_INCONSISTENCY"
    NON_CANONICAL = "NON_CANONICAL"
    PRODUCER_VERDICT_FORBIDDEN = "PRODUCER_VERDICT_FORBIDDEN"
    CONTRACT_BINDING_MISMATCH = "CONTRACT_BINDING_MISMATCH"


class RoutingQualificationRejected(ValueError):
    """A B9 object failed strict parsing or semantic admission."""

    def __init__(
        self,
        code: RoutingQualificationValidationCode,
        message: str,
        *,
        path: str | None = None,
    ) -> None:
        self.code = RoutingQualificationValidationCode(code)
        self.path = path
        super().__init__(message)


class _JsonBoundaryError(ValueError):
    def __init__(self, code: RoutingQualificationValidationCode, message: str) -> None:
        self.code = code
        super().__init__(message)


def routing_qualification_contract_digest(
    value: POCContract | RoutingQualificationCriterionV1,
) -> str:
    """Return the existing bare lowercase SHA-256 contract digest.

    A full ``POCContract`` uses the established ExitSpec contract hash.  The
    criterion-only form is useful for portable protocol fixtures and uses the
    same RFC 8785/JCS + SHA-256 convention without introducing a second
    persisted contract hash field.
    """

    if type(value) is POCContract:
        return contract_digest(value)
    if type(value) is RoutingQualificationCriterionV1:
        return hashlib.sha256(
            canonical_json_bytes(value.model_dump(mode="json"))
        ).hexdigest()
    raise TypeError("value must be a POCContract or RoutingQualificationCriterionV1.")


def parse_routing_qualification_contract(
    value: bytes | Mapping[str, Any],
) -> RoutingQualificationCriterionV1:
    """Strictly parse one run-independent B9 criterion."""

    payload = _load_object(value, label="routing qualification contract")
    return _validate_model(
        RoutingQualificationCriterionV1,
        payload,
        label="routing qualification contract",
    )


def serialize_routing_qualification_contract(
    value: RoutingQualificationCriterionV1,
) -> bytes:
    """Return canonical bytes after rerunning strict B9 validation."""

    if type(value) is not RoutingQualificationCriterionV1:
        _reject(
            RoutingQualificationValidationCode.WRONG_TYPE,
            "A typed routing qualification criterion is required.",
        )
    content = canonical_json_bytes(value.model_dump(mode="json"))
    parsed = parse_routing_qualification_contract(content)
    if parsed != value:
        _reject(
            RoutingQualificationValidationCode.SEMANTIC_INCONSISTENCY,
            "Routing qualification contract changed during serialization.",
        )
    return content


def parse_routing_qualification_evidence_fixture(
    value: bytes | Mapping[str, Any],
) -> "RoutingQualificationEvidenceFixtureV1":
    """Strictly parse the synthetic evidence-side protocol fixture."""

    payload = _load_object(value, label="routing qualification evidence fixture")
    return _validate_model(
        RoutingQualificationEvidenceFixtureV1,
        payload,
        label="routing qualification evidence fixture",
    )


def serialize_routing_qualification_evidence_fixture(
    value: "RoutingQualificationEvidenceFixtureV1",
) -> bytes:
    """Return canonical bytes after rerunning strict fixture validation."""

    if type(value) is not RoutingQualificationEvidenceFixtureV1:
        _reject(
            RoutingQualificationValidationCode.WRONG_TYPE,
            "A typed routing qualification evidence fixture is required.",
        )
    content = canonical_json_bytes(value.model_dump(mode="json"))
    parsed = parse_routing_qualification_evidence_fixture(content)
    if parsed != value:
        _reject(
            RoutingQualificationValidationCode.SEMANTIC_INCONSISTENCY,
            "Routing qualification evidence changed during serialization.",
        )
    return content


def validate_routing_qualification_evidence_fixture(
    contract: POCContract | RoutingQualificationCriterionV1,
    evidence: "RoutingQualificationEvidenceFixtureV1",
) -> "RoutingQualificationEvidenceFixtureV1":
    """Bind synthetic observed references to a frozen routing contract.

    This checks identity and provenance bindings only.  It does not check
    route quality, combine repetitions, calculate statistics, or emit PASS,
    FAIL, BLOCKED, NOT_PROVEN, or any other acceptance verdict.
    """

    if type(evidence) is not RoutingQualificationEvidenceFixtureV1:
        raise RoutingQualificationRejected(
            RoutingQualificationValidationCode.WRONG_TYPE,
            "A typed routing qualification evidence fixture is required.",
        )
    criterion = _routing_criterion(contract)
    expected_digest = routing_qualification_contract_digest(contract)
    if evidence.contract_sha256 != expected_digest:
        _reject(
            RoutingQualificationValidationCode.CONTRACT_BINDING_MISMATCH,
            "Evidence fixture contract digest does not match the frozen contract.",
            "contract_sha256",
        )
    expected = {
        "candidate_policy_id": criterion.candidate_policy.policy_id,
        "candidate_policy_sha256": criterion.candidate_policy.policy_sha256,
        "baseline_policy_id": criterion.baseline_policy.policy_id,
        "baseline_policy_sha256": criterion.baseline_policy.policy_sha256,
        "routing_configuration_id": criterion.routing_configuration.configuration_id,
        "routing_configuration_sha256": criterion.routing_configuration.configuration_sha256,
        "request_trace_sha256": criterion.request_trace.trace_sha256,
        "failure_injection_id": criterion.failure_injection.configuration_id,
        "failure_injection_sha256": criterion.failure_injection.configuration_sha256,
        "environment_id": criterion.serving.execution_environment.environment_id,
        "environment_sha256": criterion.serving.execution_environment.environment_sha256,
        "telemetry_capsule_type": criterion.telemetry.capsule_type,
        "route_decision_receipt_type": criterion.route_decision_receipts.receipt_type,
    }
    for name, expected_value in expected.items():
        if getattr(evidence, name) != expected_value:
            _reject(
                RoutingQualificationValidationCode.CONTRACT_BINDING_MISMATCH,
                f"Evidence fixture field {name} does not match the frozen contract.",
                name,
            )
    return evidence


def _routing_criterion(
    contract: POCContract | RoutingQualificationCriterionV1,
) -> RoutingQualificationCriterionV1:
    if type(contract) is RoutingQualificationCriterionV1:
        return contract
    if type(contract) is not POCContract:
        _reject(
            RoutingQualificationValidationCode.WRONG_TYPE,
            "A POCContract or routing qualification criterion is required.",
        )
    if not verify_contract_digest(contract):
        _reject(
            RoutingQualificationValidationCode.CONTRACT_BINDING_MISMATCH,
            "Routing evidence requires a digest-valid frozen contract.",
        )
    criteria = tuple(
        criterion
        for criterion in contract.criteria
        if type(criterion) is RoutingQualificationCriterionV1
    )
    if (
        contract.status.value != "FROZEN"
        or len(criteria) != 1
        or len(contract.criteria) != 1
    ):
        _reject(
            RoutingQualificationValidationCode.CONTRACT_BINDING_MISMATCH,
            "Routing evidence requires exactly one frozen routing qualification criterion.",
        )
    return criteria[0]


class RoutingEvidenceProvenanceV1(ExitSpecModel):
    """Producer/sealer provenance; it is not an acceptance authority."""

    schema_version: str = Field(pattern=r"^exitspec\.routing-evidence-provenance\.v1$")
    producer_role: str = Field(pattern=r"^EVIDENCE_PRODUCER_OR_INFERDROME$")
    producer_id: str = Field(pattern=r"^[a-z][a-z0-9._-]{2,127}$", max_length=128)
    producer_version: str = Field(min_length=1, max_length=128)
    captured_at: str = Field(
        pattern=r"^20[0-9]{2}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$",
        max_length=20,
    )
    source_digest: str = Field(pattern=SHA256_PATTERN)


class RoutingQualificationEvidenceFixtureV1(ExitSpecModel):
    """Loudly synthetic observed IDs used only to test the B9 boundary."""

    schema_version: str = Field(
        pattern=r"^exitspec\.routing-qualification-evidence-fixture\.v1$"
    )
    fixture_status: str = Field(pattern=r"^LOUDLY_SYNTHETIC_TEST_ONLY$")
    protocol_id: str = Field(pattern=r"^routing_qualification_v1$")
    contract_sha256: str = Field(pattern=SHA256_PATTERN)
    run_id: str = Field(pattern=_SYNTHETIC_RUN_ID)
    repetition_index: int = Field(ge=1, le=100)
    candidate_policy_id: str = Field(
        pattern=r"^[a-z][a-z0-9._-]{2,127}$", max_length=128
    )
    candidate_policy_sha256: str = Field(pattern=SHA256_PATTERN)
    baseline_policy_id: str = Field(
        pattern=r"^[a-z][a-z0-9._-]{2,127}$", max_length=128
    )
    baseline_policy_sha256: str = Field(pattern=SHA256_PATTERN)
    routing_configuration_id: str = Field(
        pattern=r"^[a-z][a-z0-9._-]{2,127}$", max_length=128
    )
    routing_configuration_sha256: str = Field(pattern=SHA256_PATTERN)
    request_trace_sha256: str = Field(pattern=SHA256_PATTERN)
    failure_injection_id: str = Field(
        pattern=r"^[a-z][a-z0-9._-]{2,127}$", max_length=128
    )
    failure_injection_sha256: str = Field(pattern=SHA256_PATTERN)
    environment_id: str = Field(pattern=r"^[a-z][a-z0-9._-]{2,127}$", max_length=128)
    environment_sha256: str = Field(pattern=SHA256_PATTERN)
    telemetry_capsule_id: str = Field(pattern=_SYNTHETIC_CAPSULE_ID)
    telemetry_capsule_type: str = Field(pattern=r"^ROUTING_TELEMETRY_CAPSULE_V1$")
    telemetry_capsule_sha256: str = Field(pattern=SHA256_PATTERN)
    route_decision_receipt_type: str = Field(pattern=r"^ROUTE_DECISION_RECEIPT_V1$")
    route_decision_receipt_ids: tuple[str, ...] = Field(min_length=1, max_length=1_000)
    provenance: RoutingEvidenceProvenanceV1

    @field_validator("route_decision_receipt_ids")
    @classmethod
    def require_unique_synthetic_receipts(
        cls, value: tuple[str, ...]
    ) -> tuple[str, ...]:
        if len(set(value)) != len(value) or any(
            _SYNTHETIC_RECEIPT_ID.fullmatch(item) is None for item in value
        ):
            raise ValueError(
                "Evidence fixture receipt IDs must be unique synthetic references."
            )
        return value


def _load_object(
    value: bytes | Mapping[str, Any],
    *,
    label: str,
) -> dict[str, Any]:
    if isinstance(value, Mapping):
        if type(value) is not dict:
            _reject(
                RoutingQualificationValidationCode.WRONG_TYPE,
                f"{label} must be one JSON object.",
            )
        _walk_bounded(value, depth=0)
        try:
            content = canonical_json_bytes(value)
        except (CanonicalizationError, TypeError, ValueError) as error:
            _reject(
                RoutingQualificationValidationCode.WRONG_TYPE,
                f"{label} is outside the canonical JSON domain.",
            )
            raise AssertionError from error
        return _decode_json(content, label=label, require_canonical=False)
    if type(value) is not bytes:
        _reject(
            RoutingQualificationValidationCode.WRONG_TYPE,
            f"{label} must be bytes or one JSON object.",
        )
    limit = _MAX_EVIDENCE_BYTES if "evidence" in label else _MAX_CONTRACT_BYTES
    if len(value) > limit:
        _reject(
            RoutingQualificationValidationCode.OVERSIZED,
            f"{label} exceeds the bounded JSON size.",
        )
    return _decode_json(value, label=label, require_canonical=True)


def _decode_json(
    content: bytes,
    *,
    label: str,
    require_canonical: bool,
) -> dict[str, Any]:
    try:
        text = content.decode("utf-8")
        payload = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_int=_bounded_integer,
            parse_float=_reject_float,
            parse_constant=_reject_constant,
        )
    except UnicodeDecodeError as error:
        _reject(
            RoutingQualificationValidationCode.WRONG_TYPE,
            f"{label} is not UTF-8 JSON.",
        )
        raise AssertionError from error
    except _JsonBoundaryError as error:
        _reject(error.code, f"{label} failed its bounded JSON boundary.")
    except (json.JSONDecodeError, TypeError, ValueError):
        _reject(
            RoutingQualificationValidationCode.WRONG_TYPE,
            f"{label} is not one valid JSON object.",
        )
    if type(payload) is not dict:
        _reject(
            RoutingQualificationValidationCode.WRONG_TYPE,
            f"{label} must be one JSON object.",
        )
    _walk_bounded(payload, depth=0)
    try:
        canonical = canonical_json_bytes(payload)
    except (CanonicalizationError, TypeError, ValueError):
        _reject(
            RoutingQualificationValidationCode.WRONG_TYPE,
            f"{label} is outside the canonical JSON domain.",
        )
    if require_canonical and canonical != content:
        _reject(
            RoutingQualificationValidationCode.NON_CANONICAL,
            f"{label} is not RFC 8785 canonical JSON.",
        )
    return payload


def _validate_model(
    model_type: type[Any], payload: dict[str, Any], *, label: str
) -> Any:
    try:
        # JSON arrays are the wire representation of immutable tuple fields.
        # Pydantic's strict Python validator intentionally rejects a list for a
        # tuple, while its strict JSON validator accepts the JSON array without
        # coercing scalar values.
        model = model_type.model_validate_json(
            canonical_json_bytes(payload), strict=True
        )
    except ValidationError as error:
        code, path = _classify_validation_error(error)
        _reject(code, f"{label} failed strict validation.", path)
    except (TypeError, ValueError):
        _reject(
            RoutingQualificationValidationCode.WRONG_TYPE,
            f"{label} failed strict validation.",
        )
    try:
        if canonical_json_bytes(model.model_dump(mode="json")) != canonical_json_bytes(
            payload
        ):
            _reject(
                RoutingQualificationValidationCode.SEMANTIC_INCONSISTENCY,
                f"{label} changed during strict validation.",
            )
    except (CanonicalizationError, TypeError, ValueError):
        _reject(
            RoutingQualificationValidationCode.WRONG_TYPE,
            f"{label} is outside the canonical JSON domain.",
        )
    return model


def _classify_validation_error(
    error: ValidationError,
) -> tuple[RoutingQualificationValidationCode, str | None]:
    first = error.errors()[0]
    location = tuple(str(item) for item in first.get("loc", ()))
    path = ".".join(location) or None
    error_type = str(first.get("type", ""))
    field = location[-1] if location else ""
    if error_type == "extra_forbidden":
        if "verdict" in field.lower() or field.lower() in {"acceptance", "decision"}:
            return RoutingQualificationValidationCode.PRODUCER_VERDICT_FORBIDDEN, path
        return RoutingQualificationValidationCode.EXTRA_FIELD, path
    if "missing" in error_type:
        return RoutingQualificationValidationCode.MISSING_FIELD, path
    if error_type in {
        "int_type",
        "bool_type",
        "string_type",
        "tuple_type",
        "list_type",
        "dict_type",
        "model_type",
    }:
        return RoutingQualificationValidationCode.WRONG_TYPE, path
    if "too_long" in error_type or "too_short" in error_type:
        return RoutingQualificationValidationCode.OVERSIZED, path
    if error_type == "string_pattern_mismatch":
        if "sha" in field or "digest" in field or "hash" in field:
            return RoutingQualificationValidationCode.INVALID_DIGEST, path
        return RoutingQualificationValidationCode.INVALID_VALUE, path
    if error_type in {
        "greater_than",
        "greater_than_equal",
        "less_than",
        "less_than_equal",
    }:
        return RoutingQualificationValidationCode.INVALID_BOUND, path
    if error_type == "literal_error":
        if (
            "version" in field
            or "schema" in field
            or "canonical" in field
            or "hash" in field
        ):
            return RoutingQualificationValidationCode.WRONG_VERSION, path
        return RoutingQualificationValidationCode.SEMANTIC_INCONSISTENCY, path
    return RoutingQualificationValidationCode.SEMANTIC_INCONSISTENCY, path


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _JsonBoundaryError(
                RoutingQualificationValidationCode.DUPLICATE_FIELD,
                "duplicate JSON object field",
            )
        result[key] = value
    if len(result) > _MAX_JSON_OBJECT_KEYS:
        raise _JsonBoundaryError(
            RoutingQualificationValidationCode.OVERSIZED,
            "too many JSON object fields",
        )
    return result


def _bounded_integer(value: str) -> int:
    parsed = int(value)
    if abs(parsed) > _MAX_JSON_INTEGER:
        raise _JsonBoundaryError(
            RoutingQualificationValidationCode.OVERSIZED,
            "JSON integer is outside the bounded domain",
        )
    return parsed


def _reject_float(value: str) -> None:
    raise _JsonBoundaryError(
        RoutingQualificationValidationCode.WRONG_TYPE,
        "floating point values are not permitted in the B9 protocol",
    )


def _reject_constant(value: str) -> None:
    raise _JsonBoundaryError(
        RoutingQualificationValidationCode.WRONG_TYPE,
        f"non-finite JSON constant {value} is not permitted",
    )


def _walk_bounded(value: object, *, depth: int) -> None:
    if depth > _MAX_JSON_DEPTH:
        _reject(
            RoutingQualificationValidationCode.OVERSIZED,
            "JSON nesting exceeds the B9 bound.",
        )
    if type(value) is str and len(value) > _MAX_JSON_STRING_LENGTH:
        _reject(
            RoutingQualificationValidationCode.OVERSIZED,
            "JSON string exceeds the B9 bound.",
        )
    elif type(value) is dict:
        if len(value) > _MAX_JSON_OBJECT_KEYS:
            _reject(
                RoutingQualificationValidationCode.OVERSIZED,
                "JSON object exceeds the B9 field bound.",
            )
        for key, child in value.items():
            if type(key) is not str:
                _reject(
                    RoutingQualificationValidationCode.WRONG_TYPE,
                    "JSON object keys must be strings.",
                )
            _walk_bounded(child, depth=depth + 1)
    elif type(value) is list:
        if len(value) > _MAX_JSON_ARRAY_ITEMS:
            _reject(
                RoutingQualificationValidationCode.OVERSIZED,
                "JSON array exceeds the B9 item bound.",
            )
        for child in value:
            _walk_bounded(child, depth=depth + 1)
    elif type(value) is int and abs(value) > _MAX_JSON_INTEGER:
        _reject(
            RoutingQualificationValidationCode.OVERSIZED,
            "JSON integer exceeds the B9 bound.",
        )
    elif type(value) is float:
        _reject(
            RoutingQualificationValidationCode.WRONG_TYPE,
            "floating point values are not permitted in the B9 protocol.",
        )


def _reject(
    code: RoutingQualificationValidationCode,
    message: str,
    path: str | None = None,
) -> None:
    raise RoutingQualificationRejected(code, message, path=path)


__all__ = [
    "ROUTING_QUALIFICATION_CANONICALIZATION_VERSION",
    "ROUTING_QUALIFICATION_EVIDENCE_FIXTURE_SCHEMA_VERSION",
    "ROUTING_QUALIFICATION_HASH_VERSION",
    "ROUTING_QUALIFICATION_PROTOCOL_ID",
    "ROUTING_QUALIFICATION_SCHEMA_VERSION",
    "RoutingEvidenceProvenanceV1",
    "RoutingQualificationContractV1",
    "RoutingQualificationEvidenceFixtureV1",
    "RoutingQualificationRejected",
    "RoutingQualificationValidationCode",
    "parse_routing_qualification_contract",
    "parse_routing_qualification_evidence_fixture",
    "routing_qualification_contract_digest",
    "serialize_routing_qualification_contract",
    "serialize_routing_qualification_evidence_fixture",
    "validate_routing_qualification_evidence_fixture",
]
