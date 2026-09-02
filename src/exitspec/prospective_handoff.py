"""Canonical, zero-side-effect PR7 prospective qualification handoffs.

This module records the exact local qualification context and the observation
requirements declared by a fully provable proofability report.  A handoff is
not a run request, provider configuration, evidence bundle, verdict, receipt,
credential, dispatch capability, or authorization.  Creating, parsing, and
verifying a handoff perform no I/O or external operation.
"""

from __future__ import annotations

import hashlib
import hmac
import json
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
from .models import FrozenExitSpecModel, POCContract
from .producer_capability import (
    ProducerCapabilityDescriptorV1,
    parse_producer_capability_descriptor,
    producer_capability_digest,
    serialize_producer_capability_descriptor,
)
from .proofability import (
    PROOFABILITY_PROTOCOL_ID,
    PROOFABILITY_PROTOCOL_VERSION,
    CriterionProofabilityDisposition,
    ObservationReferenceV1,
    OverallProofabilityDisposition,
    ProofabilityRejected,
    ProofabilityReportV1,
    evaluate_proofability,
    parse_proofability_report,
    proofability_report_digest,
    serialize_proofability_report,
)
from .qualification_scope import (
    QualificationContextV1,
    QualificationScopeV1,
    parse_qualification_context,
    parse_qualification_scope,
    qualification_context_digest,
    qualification_scope_digest,
    serialize_qualification_context,
    serialize_qualification_scope,
)
from .serving_subject import (
    ServingSubjectManifestV1,
    parse_serving_subject_manifest,
    serialize_serving_subject_manifest,
    serving_subject_digest,
)

PROSPECTIVE_HANDOFF_SCHEMA_VERSION: Final = (
    "exitspec.prospective-qualification-handoff.v1"
)
PROSPECTIVE_HANDOFF_CANONICALIZATION_VERSION: Final = "rfc8785_jcs_v1"
PROSPECTIVE_HANDOFF_HASH_VERSION: Final = "sha256_v1"
PROSPECTIVE_HANDOFF_DIGEST_DOMAIN: Final = (
    b"exitspec-prospective-qualification-handoff-v1\x00"
)

_MAX_HANDOFF_BYTES: Final = 128 * 1024
_MAX_JSON_DEPTH: Final = 16
_MAX_JSON_NODES: Final = 4_096
_MAX_JSON_OBJECT_KEYS: Final = 40
_MAX_JSON_ARRAY_ITEMS: Final = 64
_MAX_JSON_STRING_LENGTH: Final = 512
_MAX_JSON_INTEGER: Final = 2_147_483_647
_DIGEST_PATTERN: Final = r"^sha256:[a-f0-9]{64}$"
_IDENTIFIER_PATTERN: Final = r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$"
_EXACT_VERSION_PATTERN: Final = (
    r"^v?[0-9]+(?:\.[0-9]+){0,2}(?:-[0-9A-Za-z][0-9A-Za-z.-]{0,63})?"
    r"(?:\+[0-9A-Za-z][0-9A-Za-z.-]{0,63})?$"
)
_UNPINNED_VERSIONS: Final = frozenset(
    {"latest", "main", "master", "head", "default", "stable"}
)
_CRITERION_ID_PATTERN: Final = (
    r"^(?:[A-Z][A-Z0-9-]{2,63}|routing_qualification_v1|"
    r"routing_slo_attainment_v1|routing_campaign_reduction_v1)$"
)


class ProspectiveHandoffValidationCode(str, Enum):
    """Stable, content-safe failure classes for the PR7 boundary."""

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
    PROOFABILITY_BINDING_MISMATCH = "PROOFABILITY_BINDING_MISMATCH"
    PROOFABILITY_NOT_PROVABLE = "PROOFABILITY_NOT_PROVABLE"


class ProspectiveHandoffRejected(ValueError):
    """One public prospective-handoff boundary failed closed."""

    def __init__(self, code: ProspectiveHandoffValidationCode, message: str) -> None:
        self.code = ProspectiveHandoffValidationCode(code)
        super().__init__(message)


class _JsonBoundaryError(ValueError):
    def __init__(self, code: ProspectiveHandoffValidationCode) -> None:
        self.code = code
        super().__init__(code.value)


def _reject(code: ProspectiveHandoffValidationCode, message: str) -> None:
    raise ProspectiveHandoffRejected(code, message)


class _StrictFrozenHandoffModel(FrozenExitSpecModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        revalidate_instances="always",
    )


def _observation_key(value: ObservationReferenceV1) -> tuple[str, str]:
    return (str(value.observation_kind), value.observation_id)


class ProspectiveHandoffRequirementV1(_StrictFrozenHandoffModel):
    """One frozen criterion and the exact observations a later producer may supply."""

    criterion_id: str = Field(pattern=_CRITERION_ID_PATTERN, max_length=64)
    required_observations: tuple[ObservationReferenceV1, ...] = Field(
        min_length=1,
        max_length=8,
    )

    @model_validator(mode="after")
    def require_sorted_unique_observations(self) -> ProspectiveHandoffRequirementV1:
        keys = tuple(_observation_key(item) for item in self.required_observations)
        if len(keys) != len(set(keys)):
            raise ValueError("Required observations must be unique.")
        if keys != tuple(sorted(keys)):
            raise ValueError("Required observations must use canonical order.")
        return self


class _ProspectiveHandoffUnsignedV1(_StrictFrozenHandoffModel):
    schema_version: Literal[PROSPECTIVE_HANDOFF_SCHEMA_VERSION]
    canonicalization_version: Literal[PROSPECTIVE_HANDOFF_CANONICALIZATION_VERSION]
    hash_version: Literal[PROSPECTIVE_HANDOFF_HASH_VERSION]
    subject_digest: str = Field(pattern=_DIGEST_PATTERN)
    scope_digest: str = Field(pattern=_DIGEST_PATTERN)
    qualification_context_digest: str = Field(pattern=_DIGEST_PATTERN)
    protocol_id: Literal[PROOFABILITY_PROTOCOL_ID]
    protocol_version: Literal[PROOFABILITY_PROTOCOL_VERSION]
    contract_id: str = Field(pattern=_IDENTIFIER_PATTERN, max_length=128)
    contract_canonical_digest: str = Field(pattern=_DIGEST_PATTERN)
    workload_id: str = Field(pattern=_IDENTIFIER_PATTERN, max_length=128)
    workload_digest: str = Field(pattern=_DIGEST_PATTERN)
    measurement_profile_id: str = Field(pattern=_IDENTIFIER_PATTERN, max_length=128)
    measurement_profile_version: str = Field(
        pattern=_EXACT_VERSION_PATTERN,
        max_length=128,
    )
    measurement_profile_digest: str = Field(pattern=_DIGEST_PATTERN)
    capability_profile_id: str = Field(pattern=_IDENTIFIER_PATTERN, max_length=128)
    capability_profile_version: str = Field(
        pattern=_EXACT_VERSION_PATTERN,
        max_length=128,
    )
    capability_digest: str = Field(pattern=_DIGEST_PATTERN)
    proofability_report_digest: str = Field(pattern=_DIGEST_PATTERN)
    requirements: tuple[ProspectiveHandoffRequirementV1, ...] = Field(
        min_length=1,
        max_length=64,
    )

    @field_validator(
        "measurement_profile_version",
        "capability_profile_version",
    )
    @classmethod
    def require_pinned_profile_versions(cls, value: str) -> str:
        if value.casefold() in _UNPINNED_VERSIONS:
            raise ValueError("Prospective handoff profile versions must be pinned.")
        return value

    @model_validator(mode="after")
    def require_unique_criterion_requirements(self) -> _ProspectiveHandoffUnsignedV1:
        identifiers = tuple(item.criterion_id for item in self.requirements)
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Handoff criterion requirements must be unique.")
        return self


def _handoff_digest_from_projection(projection: Mapping[str, Any]) -> str:
    try:
        content = canonical_json_bytes(projection)
    except (CanonicalizationError, RecursionError, TypeError, ValueError):
        _reject(
            ProspectiveHandoffValidationCode.INVALID_VALUE,
            "Prospective handoff projection is outside the canonical JSON domain.",
        )
    return "sha256:" + hashlib.sha256(
        PROSPECTIVE_HANDOFF_DIGEST_DOMAIN + content
    ).hexdigest()


class ProspectiveHandoffV1(_ProspectiveHandoffUnsignedV1):
    """Immutable local handoff artifact with no execution or authority capability."""

    prospective_handoff_digest: str = Field(pattern=_DIGEST_PATTERN)

    @model_validator(mode="after")
    def require_matching_handoff_digest(self) -> ProspectiveHandoffV1:
        projection = self.model_dump(
            mode="json",
            exclude={"prospective_handoff_digest"},
        )
        expected = _handoff_digest_from_projection(projection)
        if not hmac.compare_digest(self.prospective_handoff_digest, expected):
            raise ValueError(
                "prospective_handoff_digest does not match the unsigned projection."
            )
        return self


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _JsonBoundaryError(ProspectiveHandoffValidationCode.DUPLICATE_FIELD)
        result[key] = value
    return result


def _bounded_integer(raw: str) -> int:
    value = int(raw)
    if abs(value) > _MAX_JSON_INTEGER:
        raise _JsonBoundaryError(ProspectiveHandoffValidationCode.OVERSIZED)
    return value


def _reject_float(_: str) -> None:
    raise _JsonBoundaryError(ProspectiveHandoffValidationCode.INVALID_VALUE)


def _reject_constant(_: str) -> None:
    raise _JsonBoundaryError(ProspectiveHandoffValidationCode.INVALID_VALUE)


def _walk_bounded_json(
    value: object,
    *,
    depth: int,
    node_count: list[int],
    active_container_ids: set[int] | None = None,
) -> None:
    if active_container_ids is None:
        active_container_ids = set()
    node_count[0] += 1
    if node_count[0] > _MAX_JSON_NODES or depth > _MAX_JSON_DEPTH:
        raise _JsonBoundaryError(ProspectiveHandoffValidationCode.OVERSIZED)
    if type(value) is str:
        if len(value) > _MAX_JSON_STRING_LENGTH:
            raise _JsonBoundaryError(ProspectiveHandoffValidationCode.OVERSIZED)
        return
    if value is None or type(value) in {bool, int}:
        return
    if type(value) is dict:
        identity = id(value)
        if identity in active_container_ids:
            raise _JsonBoundaryError(ProspectiveHandoffValidationCode.INVALID_VALUE)
        if len(value) > _MAX_JSON_OBJECT_KEYS:
            raise _JsonBoundaryError(ProspectiveHandoffValidationCode.OVERSIZED)
        active_container_ids.add(identity)
        try:
            for key, child in value.items():
                if type(key) is not str or len(key) > _MAX_JSON_STRING_LENGTH:
                    raise _JsonBoundaryError(ProspectiveHandoffValidationCode.OVERSIZED)
                _walk_bounded_json(
                    child,
                    depth=depth + 1,
                    node_count=node_count,
                    active_container_ids=active_container_ids,
                )
        finally:
            active_container_ids.remove(identity)
        return
    if type(value) is list:
        identity = id(value)
        if identity in active_container_ids:
            raise _JsonBoundaryError(ProspectiveHandoffValidationCode.INVALID_VALUE)
        if len(value) > _MAX_JSON_ARRAY_ITEMS:
            raise _JsonBoundaryError(ProspectiveHandoffValidationCode.OVERSIZED)
        active_container_ids.add(identity)
        try:
            for child in value:
                _walk_bounded_json(
                    child,
                    depth=depth + 1,
                    node_count=node_count,
                    active_container_ids=active_container_ids,
                )
        finally:
            active_container_ids.remove(identity)
        return
    raise _JsonBoundaryError(ProspectiveHandoffValidationCode.WRONG_TYPE)


def _decode_object(content: bytes, *, require_canonical: bool) -> dict[str, Any]:
    if len(content) > _MAX_HANDOFF_BYTES:
        _reject(
            ProspectiveHandoffValidationCode.OVERSIZED,
            "Prospective handoff exceeds its byte limit.",
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
            ProspectiveHandoffValidationCode.WRONG_TYPE,
            "Prospective handoff is not UTF-8 JSON.",
        )
    except _JsonBoundaryError as error:
        _reject(error.code, "Prospective handoff failed its JSON boundary.")
    except (json.JSONDecodeError, RecursionError, TypeError, ValueError):
        _reject(
            ProspectiveHandoffValidationCode.WRONG_TYPE,
            "Prospective handoff is not one valid JSON object.",
        )
    if type(payload) is not dict:
        _reject(
            ProspectiveHandoffValidationCode.WRONG_TYPE,
            "Prospective handoff must be one JSON object.",
        )
    try:
        _walk_bounded_json(payload, depth=0, node_count=[0])
        canonical = canonical_json_bytes(payload)
    except _JsonBoundaryError as error:
        _reject(error.code, "Prospective handoff failed its JSON boundary.")
    except (CanonicalizationError, RecursionError, TypeError, ValueError):
        _reject(
            ProspectiveHandoffValidationCode.INVALID_VALUE,
            "Prospective handoff is outside the canonical JSON domain.",
        )
    if require_canonical and canonical != content:
        _reject(
            ProspectiveHandoffValidationCode.NON_CANONICAL,
            "Prospective handoff is not RFC 8785 canonical JSON.",
        )
    return payload


def _load_object(value: bytes | Mapping[str, Any]) -> dict[str, Any]:
    if type(value) is bytes:
        return _decode_object(value, require_canonical=True)
    if type(value) is not dict:
        _reject(
            ProspectiveHandoffValidationCode.WRONG_TYPE,
            "Prospective handoff must be bytes or one JSON object.",
        )
    try:
        return _decode_object(canonical_json_bytes(value), require_canonical=False)
    except (CanonicalizationError, RecursionError, TypeError, ValueError):
        _reject(
            ProspectiveHandoffValidationCode.INVALID_VALUE,
            "Prospective handoff is outside the canonical JSON domain.",
        )


def _classify_validation_error(
    error: ValidationError,
) -> ProspectiveHandoffValidationCode:
    for detail in error.errors():
        location = tuple(detail.get("loc", ()))
        error_type = str(detail.get("type", ""))
        if error_type == "extra_forbidden":
            return ProspectiveHandoffValidationCode.EXTRA_FIELD
        if error_type == "missing":
            return ProspectiveHandoffValidationCode.MISSING_FIELD
        if location and location[0] in {
            "schema_version",
            "canonicalization_version",
            "hash_version",
            "protocol_id",
            "protocol_version",
        }:
            return ProspectiveHandoffValidationCode.WRONG_VERSION
        if location and location[0] == "prospective_handoff_digest":
            return ProspectiveHandoffValidationCode.INVALID_DIGEST
        if error_type in {"too_long", "string_too_long", "too_short"}:
            return ProspectiveHandoffValidationCode.OVERSIZED
        if error_type.endswith("_type") or error_type in {
            "model_type",
            "tuple_type",
            "list_type",
        }:
            return ProspectiveHandoffValidationCode.WRONG_TYPE
    return ProspectiveHandoffValidationCode.INVALID_VALUE


def _validate_model(
    model_type: type[_ProspectiveHandoffUnsignedV1 | ProspectiveHandoffV1],
    payload: dict[str, Any],
    *,
    label: str,
) -> _ProspectiveHandoffUnsignedV1 | ProspectiveHandoffV1:
    try:
        return model_type.model_validate_json(canonical_json_bytes(payload), strict=True)
    except ValidationError as error:
        _reject(_classify_validation_error(error), f"{label} failed strict validation.")
    except (CanonicalizationError, RecursionError, TypeError, ValueError):
        _reject(
            ProspectiveHandoffValidationCode.INVALID_VALUE,
            f"{label} failed strict validation.",
        )


def _validated_typed_handoff(value: object) -> ProspectiveHandoffV1:
    if type(value) is not ProspectiveHandoffV1:
        _reject(
            ProspectiveHandoffValidationCode.WRONG_TYPE,
            "A typed ProspectiveHandoffV1 is required.",
        )
    try:
        raw_state = object.__getattribute__(value, "__dict__")
        extra_state = object.__getattribute__(value, "__pydantic_extra__")
        private_state = object.__getattribute__(value, "__pydantic_private__")
    except AttributeError:
        _reject(
            ProspectiveHandoffValidationCode.WRONG_TYPE,
            "Prospective handoff has no inspectable raw state.",
        )
    expected_fields = set(ProspectiveHandoffV1.model_fields)
    if type(raw_state) is not dict or set(raw_state) != expected_fields:
        _reject(
            ProspectiveHandoffValidationCode.SEMANTIC_INCONSISTENCY,
            "Prospective handoff raw fields are inconsistent.",
        )
    if extra_state or private_state:
        _reject(
            ProspectiveHandoffValidationCode.EXTRA_FIELD,
            "Prospective handoff contains undocumented state.",
        )
    try:
        serialized = value.model_dump(mode="json", warnings="error")
    except (TypeError, ValueError):
        _reject(
            ProspectiveHandoffValidationCode.INVALID_VALUE,
            "Prospective handoff has an unsafe raw field value.",
        )
    validated = _validate_model(
        ProspectiveHandoffV1,
        serialized,
        label="Prospective handoff",
    )
    if type(validated) is not ProspectiveHandoffV1:
        _reject(
            ProspectiveHandoffValidationCode.WRONG_TYPE,
            "Prospective handoff has the wrong typed model.",
        )
    return validated


def _normalise_inputs(
    subject: ServingSubjectManifestV1,
    scope: QualificationScopeV1,
    context: QualificationContextV1,
    descriptor: ProducerCapabilityDescriptorV1,
) -> tuple[
    ServingSubjectManifestV1,
    QualificationScopeV1,
    QualificationContextV1,
    ProducerCapabilityDescriptorV1,
]:
    try:
        return (
            parse_serving_subject_manifest(serialize_serving_subject_manifest(subject)),
            parse_qualification_scope(serialize_qualification_scope(scope)),
            parse_qualification_context(serialize_qualification_context(context)),
            parse_producer_capability_descriptor(
                serialize_producer_capability_descriptor(descriptor)
            ),
        )
    except Exception as error:
        _reject(
            ProspectiveHandoffValidationCode.PROOFABILITY_BINDING_MISMATCH,
            "Prospective handoff inputs are not valid exact bindings.",
        )
        raise AssertionError("unreachable") from error


def _verified_provable_report(
    report: ProofabilityReportV1,
    subject: ServingSubjectManifestV1,
    scope: QualificationScopeV1,
    context: QualificationContextV1,
    contract: POCContract,
    descriptor: ProducerCapabilityDescriptorV1,
) -> ProofabilityReportV1:
    try:
        supplied = parse_proofability_report(serialize_proofability_report(report))
        expected = evaluate_proofability(subject, scope, context, contract, descriptor)
        if not hmac.compare_digest(
            serialize_proofability_report(supplied),
            serialize_proofability_report(expected),
        ):
            _reject(
                ProspectiveHandoffValidationCode.PROOFABILITY_BINDING_MISMATCH,
                "Proofability report does not bind the supplied qualification inputs.",
            )
    except ProspectiveHandoffRejected:
        raise
    except ProofabilityRejected:
        _reject(
            ProspectiveHandoffValidationCode.PROOFABILITY_BINDING_MISMATCH,
            "Proofability report does not bind the supplied qualification inputs.",
        )
    if expected.overall_disposition is not OverallProofabilityDisposition.PROVABLE:
        _reject(
            ProspectiveHandoffValidationCode.PROOFABILITY_NOT_PROVABLE,
            "Prospective handoff requires a fully provable qualification report.",
        )
    if any(
        item.disposition is not CriterionProofabilityDisposition.PROVABLE
        for item in expected.criterion_results
    ):
        _reject(
            ProspectiveHandoffValidationCode.PROOFABILITY_NOT_PROVABLE,
            "Prospective handoff requires only provable criterion requirements.",
        )
    return expected


def _requirements_from_report(
    report: ProofabilityReportV1,
) -> tuple[ProspectiveHandoffRequirementV1, ...]:
    try:
        return tuple(
            ProspectiveHandoffRequirementV1(
                criterion_id=result.criterion_id,
                required_observations=tuple(result.required_observations),
            )
            for result in report.criterion_results
        )
    except ValidationError:
        _reject(
            ProspectiveHandoffValidationCode.SEMANTIC_INCONSISTENCY,
            "Proofability report requirements cannot form a prospective handoff.",
        )


def create_prospective_handoff(
    subject: ServingSubjectManifestV1,
    scope: QualificationScopeV1,
    context: QualificationContextV1,
    contract: POCContract,
    descriptor: ProducerCapabilityDescriptorV1,
    report: ProofabilityReportV1,
) -> ProspectiveHandoffV1:
    """Create one canonical local handoff from exact, fully provable inputs.

    This re-evaluates the provided proofability report against the original
    bindings.  It neither dispatches work nor contacts a provider.
    """

    subject, scope, context, descriptor = _normalise_inputs(
        subject,
        scope,
        context,
        descriptor,
    )
    report = _verified_provable_report(
        report,
        subject,
        scope,
        context,
        contract,
        descriptor,
    )
    unsigned_payload: dict[str, Any] = {
        "schema_version": PROSPECTIVE_HANDOFF_SCHEMA_VERSION,
        "canonicalization_version": PROSPECTIVE_HANDOFF_CANONICALIZATION_VERSION,
        "hash_version": PROSPECTIVE_HANDOFF_HASH_VERSION,
        "subject_digest": serving_subject_digest(subject),
        "scope_digest": qualification_scope_digest(scope),
        "qualification_context_digest": qualification_context_digest(context),
        "protocol_id": PROOFABILITY_PROTOCOL_ID,
        "protocol_version": PROOFABILITY_PROTOCOL_VERSION,
        "contract_id": report.contract_id,
        "contract_canonical_digest": report.contract_canonical_digest,
        "workload_id": scope.workload.workload_id,
        "workload_digest": scope.workload.workload_digest,
        "measurement_profile_id": scope.measurement_profile.profile_id,
        "measurement_profile_version": scope.measurement_profile.profile_version,
        "measurement_profile_digest": scope.measurement_profile.profile_digest,
        "capability_profile_id": descriptor.profile.profile_id,
        "capability_profile_version": descriptor.profile.profile_version,
        "capability_digest": producer_capability_digest(descriptor),
        "proofability_report_digest": proofability_report_digest(report),
        "requirements": [
            item.model_dump(mode="json") for item in _requirements_from_report(report)
        ],
    }
    unsigned = _validate_model(
        _ProspectiveHandoffUnsignedV1,
        unsigned_payload,
        label="Prospective handoff input",
    )
    projection = unsigned.model_dump(mode="json")
    handoff = _validate_model(
        ProspectiveHandoffV1,
        {
            **projection,
            "prospective_handoff_digest": _handoff_digest_from_projection(projection),
        },
        label="Prospective handoff",
    )
    if type(handoff) is not ProspectiveHandoffV1:
        _reject(
            ProspectiveHandoffValidationCode.WRONG_TYPE,
            "Prospective handoff has the wrong typed model.",
        )
    return handoff


def parse_prospective_handoff(
    value: bytes | Mapping[str, Any],
) -> ProspectiveHandoffV1:
    """Parse one canonical, self-consistent handoff; parsing is not dispatch."""

    handoff = _validate_model(
        ProspectiveHandoffV1,
        _load_object(value),
        label="Prospective handoff",
    )
    if type(handoff) is not ProspectiveHandoffV1:
        _reject(
            ProspectiveHandoffValidationCode.WRONG_TYPE,
            "Prospective handoff has the wrong typed model.",
        )
    return handoff


def canonical_prospective_handoff_projection(
    value: ProspectiveHandoffV1,
) -> dict[str, Any]:
    """Return the one validated unsigned projection used for handoff digesting."""

    return _validated_typed_handoff(value).model_dump(
        mode="json",
        exclude={"prospective_handoff_digest"},
    )


def prospective_handoff_digest(value: ProspectiveHandoffV1) -> str:
    """Return the domain-separated identity digest for one local handoff."""

    return _handoff_digest_from_projection(canonical_prospective_handoff_projection(value))


def serialize_prospective_handoff(value: ProspectiveHandoffV1) -> bytes:
    """Serialize one strict handoff as byte-exact RFC 8785 JSON."""

    validated = _validated_typed_handoff(value)
    try:
        content = canonical_json_bytes(validated.model_dump(mode="json"))
    except (CanonicalizationError, RecursionError, TypeError, ValueError):
        _reject(
            ProspectiveHandoffValidationCode.INVALID_VALUE,
            "Prospective handoff is outside the canonical JSON domain.",
        )
    if parse_prospective_handoff(content) != validated:
        _reject(
            ProspectiveHandoffValidationCode.SEMANTIC_INCONSISTENCY,
            "Prospective handoff changed during serialization.",
        )
    return content


def verify_prospective_handoff(
    handoff: object,
    subject: ServingSubjectManifestV1,
    scope: QualificationScopeV1,
    context: QualificationContextV1,
    contract: POCContract,
    descriptor: ProducerCapabilityDescriptorV1,
    report: ProofabilityReportV1,
) -> bool:
    """Recreate one exact local handoff; self-consistency alone is insufficient."""

    try:
        actual = _validated_typed_handoff(handoff)
        expected = create_prospective_handoff(
            subject,
            scope,
            context,
            contract,
            descriptor,
            report,
        )
        return hmac.compare_digest(
            serialize_prospective_handoff(actual),
            serialize_prospective_handoff(expected),
        )
    except (ProspectiveHandoffRejected, TypeError, ValueError):
        return False


__all__ = [
    "PROSPECTIVE_HANDOFF_CANONICALIZATION_VERSION",
    "PROSPECTIVE_HANDOFF_DIGEST_DOMAIN",
    "PROSPECTIVE_HANDOFF_HASH_VERSION",
    "PROSPECTIVE_HANDOFF_SCHEMA_VERSION",
    "ProspectiveHandoffRejected",
    "ProspectiveHandoffRequirementV1",
    "ProspectiveHandoffV1",
    "ProspectiveHandoffValidationCode",
    "canonical_prospective_handoff_projection",
    "create_prospective_handoff",
    "parse_prospective_handoff",
    "prospective_handoff_digest",
    "serialize_prospective_handoff",
    "verify_prospective_handoff",
]
