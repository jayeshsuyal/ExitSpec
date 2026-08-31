"""Strict, package-owned v0.5 producer-capability descriptors.

This registry declares only the observations a declared external-evidence
profile could provide. It does not contact or execute a producer, read a
bundle, admit evidence, calculate proofability, issue a verdict or receipt, or
authorize deployment or traffic. A self-consistent descriptor is planning
identity and integrity only; it is not evidence of execution, hardware truth,
chronology, authorship, authenticated identity, or authority.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Mapping
from enum import Enum
from types import MappingProxyType
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

PRODUCER_CAPABILITY_DESCRIPTOR_SCHEMA_VERSION: Final = (
    "exitspec.producer-capability-descriptor.v1"
)
PRODUCER_CAPABILITY_REQUEST_SCHEMA_VERSION: Final = (
    "exitspec.producer-capability-request.v1"
)
PRODUCER_CAPABILITY_REGISTRY_VERSION: Final = (
    "exitspec.producer-capability-registry.v1"
)
PRODUCER_CAPABILITY_CANONICALIZATION_VERSION: Final = "rfc8785_jcs_v1"
PRODUCER_CAPABILITY_HASH_VERSION: Final = "sha256_v1"
PRODUCER_CAPABILITY_DIGEST_DOMAIN: Final = (
    b"exitspec-producer-capability-descriptor-v1\x00"
)

DECLARED_EXTERNAL_EVIDENCE_PROFILE_ID: Final = (
    "exitspec.external-evidence.native-ttft-profile.v1"
)
DECLARED_EXTERNAL_EVIDENCE_PROFILE_VERSION: Final = "v1"
NATIVE_TTFT_METRIC_DEFINITION_ID: Final = "vllm_first_choices_event_v0_26"
NATIVE_TTFT_SOURCE_FIELD: Final = "request.timing.ttft_ns"
NATIVE_TTFT_POPULATION: Final = (
    "successful_measured_requests_with_observed_ttft"
)
NATIVE_TTFT_REDUCER_ID: Final = "nearest_rank_v1"
MEASURED_ATTEMPT_RELIABILITY_SOURCE_FIELD: Final = "request.outcome.status"

_MAX_DESCRIPTOR_BYTES: Final = 8 * 1024
_MAX_REQUEST_BYTES: Final = 2 * 1024
_MAX_JSON_DEPTH: Final = 12
_MAX_JSON_NODES: Final = 128
_MAX_JSON_OBJECT_KEYS: Final = 24
_MAX_JSON_ARRAY_ITEMS: Final = 16
_MAX_JSON_STRING_LENGTH: Final = 512
_MAX_JSON_INTEGER: Final = 2_147_483_647

_IDENTIFIER_PATTERN: Final = r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$"
_EXACT_VERSION_PATTERN: Final = (
    r"^v?[0-9]+(?:\.[0-9]+){0,2}(?:-[0-9A-Za-z][0-9A-Za-z.-]{0,63})?"
    r"(?:\+[0-9A-Za-z][0-9A-Za-z.-]{0,63})?$"
)
_DIGEST_PATTERN: Final = r"^sha256:[a-f0-9]{64}$"
_UNPINNED_VERSIONS: Final = frozenset(
    {"latest", "main", "master", "head", "default", "stable"}
)


class ProducerCapabilityValidationCode(str, Enum):
    """Stable, content-safe failure classes at the capability boundary."""

    DUPLICATE_FIELD = "DUPLICATE_FIELD"
    EXTRA_FIELD = "EXTRA_FIELD"
    MISSING_FIELD = "MISSING_FIELD"
    WRONG_VERSION = "WRONG_VERSION"
    UNSUPPORTED_PROFILE_VERSION = "UNSUPPORTED_PROFILE_VERSION"
    UNKNOWN_PROFILE = "UNKNOWN_PROFILE"
    UNREGISTERED_DESCRIPTOR = "UNREGISTERED_DESCRIPTOR"
    WRONG_TYPE = "WRONG_TYPE"
    OVERSIZED = "OVERSIZED"
    INVALID_DIGEST = "INVALID_DIGEST"
    INVALID_VALUE = "INVALID_VALUE"
    NON_CANONICAL = "NON_CANONICAL"
    SEMANTIC_INCONSISTENCY = "SEMANTIC_INCONSISTENCY"


class ProducerCapabilityRejected(ValueError):
    """One public producer-capability boundary failed closed."""

    def __init__(self, code: ProducerCapabilityValidationCode, message: str) -> None:
        self.code = ProducerCapabilityValidationCode(code)
        super().__init__(message)


class _JsonBoundaryError(ValueError):
    def __init__(self, code: ProducerCapabilityValidationCode) -> None:
        self.code = code
        super().__init__(code.value)


def _reject(code: ProducerCapabilityValidationCode, message: str) -> None:
    raise ProducerCapabilityRejected(code, message)


class _StrictFrozenCapabilityModel(FrozenExitSpecModel):
    """Frozen strict base for every material capability field."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        revalidate_instances="always",
    )


class DeclaredExternalEvidenceProfileV1(_StrictFrozenCapabilityModel):
    """The exact ExitSpec-owned declared profile identity and revision."""

    profile_id: Literal[DECLARED_EXTERNAL_EVIDENCE_PROFILE_ID]
    profile_version: Literal[DECLARED_EXTERNAL_EVIDENCE_PROFILE_VERSION]


class EngineAdapterIdentityV1(_StrictFrozenCapabilityModel):
    """The exact engine and native adapter semantics declared by the profile."""

    engine_id: Literal["vllm"]
    engine_version: Literal["0.26.0"]
    adapter_id: Literal["vllm_bench_serve"]
    adapter_version: Literal["1.0.0"]


class NativeTTFTObservationV1(_StrictFrozenCapabilityModel):
    """The native first-choices-event TTFT sample available from this profile."""

    observation_id: Literal["native_ttft_sample"]
    metric_definition_id: Literal[NATIVE_TTFT_METRIC_DEFINITION_ID]
    source_field: Literal[NATIVE_TTFT_SOURCE_FIELD]
    unit: Literal["ns"]
    population: Literal[NATIVE_TTFT_POPULATION]
    reducer_id: Literal[NATIVE_TTFT_REDUCER_ID]
    supported_percentile: Literal["p95"]


class MeasuredAttemptReliabilityObservationV1(_StrictFrozenCapabilityModel):
    """Native measured-request outcome facts available for reliability analysis."""

    observation_id: Literal["native_measured_request_outcome"]
    source_field: Literal[MEASURED_ATTEMPT_RELIABILITY_SOURCE_FIELD]
    latency_population: Literal[NATIVE_TTFT_POPULATION]
    reliability_numerator: Literal[
        "failed_or_anomalous_native_measured_requests"
    ]
    reliability_denominator: Literal["all_measured_requests"]


class ProducerCapabilityObservationsV1(_StrictFrozenCapabilityModel):
    """The complete, closed available-observation set for one profile."""

    native_ttft: NativeTTFTObservationV1
    measured_attempt_reliability: MeasuredAttemptReliabilityObservationV1


class _ProducerCapabilityDescriptorUnsignedV1(_StrictFrozenCapabilityModel):
    """Validated unsigned descriptor projection used only by the package registry."""

    schema_version: Literal[PRODUCER_CAPABILITY_DESCRIPTOR_SCHEMA_VERSION]
    registry_version: Literal[PRODUCER_CAPABILITY_REGISTRY_VERSION]
    profile: DeclaredExternalEvidenceProfileV1
    engine_adapter: EngineAdapterIdentityV1
    available_observations: ProducerCapabilityObservationsV1


def _descriptor_digest_from_projection(projection: Mapping[str, Any]) -> str:
    try:
        content = canonical_json_bytes(projection)
    except (CanonicalizationError, RecursionError, TypeError, ValueError):
        _reject(
            ProducerCapabilityValidationCode.INVALID_VALUE,
            "Producer capability projection is outside the canonical JSON domain.",
        )
    return "sha256:" + hashlib.sha256(
        PRODUCER_CAPABILITY_DIGEST_DOMAIN + content
    ).hexdigest()


class ProducerCapabilityDescriptorV1(_ProducerCapabilityDescriptorUnsignedV1):
    """One immutable, registered statement of planning capability only."""

    capability_digest: str = Field(pattern=_DIGEST_PATTERN)

    @model_validator(mode="after")
    def require_matching_capability_digest(self) -> ProducerCapabilityDescriptorV1:
        projection = self.model_dump(mode="json", exclude={"capability_digest"})
        expected = _descriptor_digest_from_projection(projection)
        if not hmac.compare_digest(self.capability_digest, expected):
            raise ValueError(
                "capability_digest does not match the unsigned projection."
            )
        return self


class ProducerCapabilityRequestV1(_StrictFrozenCapabilityModel):
    """The only untrusted input accepted by the package-owned registry."""

    schema_version: Literal[PRODUCER_CAPABILITY_REQUEST_SCHEMA_VERSION]
    profile_id: str = Field(pattern=_IDENTIFIER_PATTERN, max_length=128)
    profile_version: str = Field(
        pattern=_EXACT_VERSION_PATTERN,
        max_length=128,
    )

    @field_validator("profile_version", mode="before")
    @classmethod
    def require_pinned_profile_version(cls, value: object) -> object:
        if type(value) is str and value.casefold() in _UNPINNED_VERSIONS:
            raise ValueError("Profile version must be pinned.")
        return value


_DECLARED_MODEL_CHILDREN: Final = {
    ProducerCapabilityDescriptorV1: (
        ("profile", DeclaredExternalEvidenceProfileV1),
        ("engine_adapter", EngineAdapterIdentityV1),
        ("available_observations", ProducerCapabilityObservationsV1),
    ),
    ProducerCapabilityObservationsV1: (
        ("native_ttft", NativeTTFTObservationV1),
        (
            "measured_attempt_reliability",
            MeasuredAttemptReliabilityObservationV1,
        ),
    ),
}


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _JsonBoundaryError(
                ProducerCapabilityValidationCode.DUPLICATE_FIELD
            )
        result[key] = value
    return result


def _bounded_integer(raw: str) -> int:
    value = int(raw)
    if abs(value) > _MAX_JSON_INTEGER:
        raise _JsonBoundaryError(ProducerCapabilityValidationCode.OVERSIZED)
    return value


def _reject_float(_: str) -> None:
    raise _JsonBoundaryError(ProducerCapabilityValidationCode.INVALID_VALUE)


def _reject_constant(_: str) -> None:
    raise _JsonBoundaryError(ProducerCapabilityValidationCode.INVALID_VALUE)


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
        raise _JsonBoundaryError(ProducerCapabilityValidationCode.OVERSIZED)
    if type(value) is str:
        if len(value) > _MAX_JSON_STRING_LENGTH:
            raise _JsonBoundaryError(ProducerCapabilityValidationCode.OVERSIZED)
        return
    if value is None or type(value) in {bool, int}:
        return
    if type(value) is dict:
        identity = id(value)
        if identity in active_container_ids:
            raise _JsonBoundaryError(ProducerCapabilityValidationCode.INVALID_VALUE)
        if len(value) > _MAX_JSON_OBJECT_KEYS:
            raise _JsonBoundaryError(ProducerCapabilityValidationCode.OVERSIZED)
        active_container_ids.add(identity)
        try:
            for key, child in value.items():
                if type(key) is not str or len(key) > _MAX_JSON_STRING_LENGTH:
                    raise _JsonBoundaryError(ProducerCapabilityValidationCode.OVERSIZED)
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
            raise _JsonBoundaryError(ProducerCapabilityValidationCode.INVALID_VALUE)
        if len(value) > _MAX_JSON_ARRAY_ITEMS:
            raise _JsonBoundaryError(ProducerCapabilityValidationCode.OVERSIZED)
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
    raise _JsonBoundaryError(ProducerCapabilityValidationCode.WRONG_TYPE)


def _decode_object(
    content: bytes,
    *,
    maximum_bytes: int,
    require_canonical: bool,
    label: str,
) -> dict[str, Any]:
    if len(content) > maximum_bytes:
        _reject(
            ProducerCapabilityValidationCode.OVERSIZED,
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
            ProducerCapabilityValidationCode.WRONG_TYPE,
            f"{label} is not UTF-8 JSON.",
        )
    except _JsonBoundaryError as error:
        _reject(error.code, f"{label} failed its JSON boundary.")
    except (json.JSONDecodeError, RecursionError, TypeError, ValueError):
        _reject(
            ProducerCapabilityValidationCode.WRONG_TYPE,
            f"{label} is not one valid JSON object.",
        )
    if type(payload) is not dict:
        _reject(
            ProducerCapabilityValidationCode.WRONG_TYPE,
            f"{label} must be one JSON object.",
        )
    try:
        _walk_bounded_json(payload, depth=0, node_count=[0])
        canonical = canonical_json_bytes(payload)
    except _JsonBoundaryError as error:
        _reject(error.code, f"{label} failed its JSON boundary.")
    except (CanonicalizationError, RecursionError, TypeError, ValueError):
        _reject(
            ProducerCapabilityValidationCode.INVALID_VALUE,
            f"{label} is outside the canonical JSON domain.",
        )
    if require_canonical and canonical != content:
        _reject(
            ProducerCapabilityValidationCode.NON_CANONICAL,
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
            ProducerCapabilityValidationCode.WRONG_TYPE,
            f"{label} must be bytes or one JSON object.",
        )
    try:
        _walk_bounded_json(value, depth=0, node_count=[0])
        content = canonical_json_bytes(value)
    except _JsonBoundaryError as error:
        _reject(error.code, f"{label} failed its JSON boundary.")
    except (CanonicalizationError, RecursionError, TypeError, ValueError):
        _reject(
            ProducerCapabilityValidationCode.INVALID_VALUE,
            f"{label} is outside the canonical JSON domain.",
        )
    return _decode_object(
        content,
        maximum_bytes=maximum_bytes,
        require_canonical=False,
        label=label,
    )


def _classify_validation_error(error: ValidationError) -> ProducerCapabilityValidationCode:
    for detail in error.errors():
        location = tuple(detail.get("loc", ()))
        error_type = str(detail.get("type", ""))
        if error_type == "extra_forbidden":
            return ProducerCapabilityValidationCode.EXTRA_FIELD
        if error_type == "missing":
            return ProducerCapabilityValidationCode.MISSING_FIELD
        if location and location[0] == "schema_version":
            return ProducerCapabilityValidationCode.WRONG_VERSION
        if location and location[-1] == "capability_digest":
            return ProducerCapabilityValidationCode.INVALID_DIGEST
        if error_type in {"too_long", "string_too_long", "too_short"}:
            return ProducerCapabilityValidationCode.OVERSIZED
        if error_type.endswith("_type") or error_type in {
            "model_type",
            "tuple_type",
            "list_type",
        }:
            return ProducerCapabilityValidationCode.WRONG_TYPE
        context = detail.get("ctx")
        if isinstance(context, dict) and "error" in context:
            message = str(context["error"])
            if "capability_digest" in message:
                return ProducerCapabilityValidationCode.INVALID_DIGEST
            if "Profile version" in message:
                return ProducerCapabilityValidationCode.UNSUPPORTED_PROFILE_VERSION
    return ProducerCapabilityValidationCode.INVALID_VALUE


def _validate_model(model_type: type[Any], payload: dict[str, Any], *, label: str) -> Any:
    try:
        return model_type.model_validate_json(canonical_json_bytes(payload), strict=True)
    except ValidationError as error:
        _reject(_classify_validation_error(error), f"{label} failed strict validation.")
    except (CanonicalizationError, RecursionError, TypeError, ValueError):
        _reject(
            ProducerCapabilityValidationCode.INVALID_VALUE,
            f"{label} failed strict validation.",
        )


def _require_exact_model_graph(
    value: object,
    model_type: type[Any],
    *,
    label: str,
) -> None:
    """Reject hidden state and type confusion before any lossy model dump."""

    if type(value) is not model_type:
        _reject(
            ProducerCapabilityValidationCode.WRONG_TYPE,
            f"A typed {model_type.__name__} is required.",
        )
    try:
        raw_state = object.__getattribute__(value, "__dict__")
        extra_state = object.__getattribute__(value, "__pydantic_extra__")
    except AttributeError:
        _reject(
            ProducerCapabilityValidationCode.WRONG_TYPE,
            f"{label} has no inspectable raw state.",
        )
    expected_fields = set(model_type.model_fields)
    if type(raw_state) is not dict:
        _reject(
            ProducerCapabilityValidationCode.WRONG_TYPE,
            f"{label} raw state must be one object.",
        )
    if set(raw_state) - expected_fields:
        _reject(
            ProducerCapabilityValidationCode.EXTRA_FIELD,
            f"{label} contains undocumented raw fields.",
        )
    if expected_fields - set(raw_state):
        _reject(
            ProducerCapabilityValidationCode.MISSING_FIELD,
            f"{label} is missing a raw field.",
        )
    if extra_state is not None and (
        type(extra_state) is not dict or bool(extra_state)
    ):
        _reject(
            ProducerCapabilityValidationCode.EXTRA_FIELD,
            f"{label} contains undocumented raw fields.",
        )
    for field_name, child_type in _DECLARED_MODEL_CHILDREN.get(model_type, ()):
        _require_exact_model_graph(
            raw_state[field_name],
            child_type,
            label="Producer capability descriptor nested model",
        )


def _validated_typed_model(value: object, model_type: type[Any], *, label: str) -> Any:
    _require_exact_model_graph(value, model_type, label=label)
    try:
        serialized_state = value.model_dump(mode="json", warnings="error")
    except (RecursionError, TypeError, ValueError):
        _reject(
            ProducerCapabilityValidationCode.INVALID_VALUE,
            f"{label} has an unsafe raw field value.",
        )
    validated = _validate_model(model_type, serialized_state, label=label)
    if type(validated) is not model_type:
        _reject(
            ProducerCapabilityValidationCode.WRONG_TYPE,
            f"{label} has the wrong typed model.",
        )
    return validated


def _registered_descriptor_payload() -> dict[str, Any]:
    """Return the one package-authored descriptor; it has no caller input."""

    return {
        "schema_version": PRODUCER_CAPABILITY_DESCRIPTOR_SCHEMA_VERSION,
        "registry_version": PRODUCER_CAPABILITY_REGISTRY_VERSION,
        "profile": {
            "profile_id": DECLARED_EXTERNAL_EVIDENCE_PROFILE_ID,
            "profile_version": DECLARED_EXTERNAL_EVIDENCE_PROFILE_VERSION,
        },
        "engine_adapter": {
            "engine_id": "vllm",
            "engine_version": "0.26.0",
            "adapter_id": "vllm_bench_serve",
            "adapter_version": "1.0.0",
        },
        "available_observations": {
            "native_ttft": {
                "observation_id": "native_ttft_sample",
                "metric_definition_id": NATIVE_TTFT_METRIC_DEFINITION_ID,
                "source_field": NATIVE_TTFT_SOURCE_FIELD,
                "unit": "ns",
                "population": NATIVE_TTFT_POPULATION,
                "reducer_id": NATIVE_TTFT_REDUCER_ID,
                "supported_percentile": "p95",
            },
            "measured_attempt_reliability": {
                "observation_id": "native_measured_request_outcome",
                "source_field": MEASURED_ATTEMPT_RELIABILITY_SOURCE_FIELD,
                "latency_population": NATIVE_TTFT_POPULATION,
                "reliability_numerator": (
                    "failed_or_anomalous_native_measured_requests"
                ),
                "reliability_denominator": "all_measured_requests",
            },
        },
    }


def _build_registered_descriptor_bytes() -> bytes:
    unsigned = _validate_model(
        _ProducerCapabilityDescriptorUnsignedV1,
        _registered_descriptor_payload(),
        label="Registered producer capability",
    )
    projection = unsigned.model_dump(mode="json")
    descriptor = _validate_model(
        ProducerCapabilityDescriptorV1,
        {
            **projection,
            "capability_digest": _descriptor_digest_from_projection(projection),
        },
        label="Registered producer capability",
    )
    if type(descriptor) is not ProducerCapabilityDescriptorV1:
        raise RuntimeError("Registered producer capability has an invalid type.")
    try:
        return canonical_json_bytes(descriptor.model_dump(mode="json"))
    except (CanonicalizationError, TypeError, ValueError) as error:
        raise RuntimeError("Registered producer capability cannot be serialized.") from error


_REGISTERED_DESCRIPTOR_BYTES: Final = _build_registered_descriptor_bytes()
_REGISTERED_DESCRIPTORS: Final = MappingProxyType(
    {
        (
            DECLARED_EXTERNAL_EVIDENCE_PROFILE_ID,
            DECLARED_EXTERNAL_EVIDENCE_PROFILE_VERSION,
        ): _REGISTERED_DESCRIPTOR_BYTES,
    }
)


def _lookup_registered_bytes(profile_id: str, profile_version: str) -> bytes:
    if type(profile_id) is not str or type(profile_version) is not str:
        _reject(
            ProducerCapabilityValidationCode.WRONG_TYPE,
            "Producer capability profile identity has the wrong type.",
        )
    content = _REGISTERED_DESCRIPTORS.get((profile_id, profile_version))
    if content is None:
        _reject(
            ProducerCapabilityValidationCode.UNKNOWN_PROFILE,
            "Producer capability profile is not registered.",
        )
    return content


def _validated_registered_descriptor(value: object) -> ProducerCapabilityDescriptorV1:
    descriptor = _validated_typed_model(
        value,
        ProducerCapabilityDescriptorV1,
        label="Producer capability descriptor",
    )
    try:
        content = canonical_json_bytes(descriptor.model_dump(mode="json"))
    except (CanonicalizationError, RecursionError, TypeError, ValueError):
        _reject(
            ProducerCapabilityValidationCode.INVALID_VALUE,
            "Producer capability descriptor is outside the canonical JSON domain.",
        )
    expected = _lookup_registered_bytes(
        descriptor.profile.profile_id,
        descriptor.profile.profile_version,
    )
    if not hmac.compare_digest(content, expected):
        _reject(
            ProducerCapabilityValidationCode.UNREGISTERED_DESCRIPTOR,
            "Producer capability descriptor is not package-registered unchanged.",
        )
    return descriptor


def parse_producer_capability_request(
    value: bytes | Mapping[str, Any],
) -> ProducerCapabilityRequestV1:
    """Strictly parse one canonical request for an exact registered profile."""

    request = _validate_model(
        ProducerCapabilityRequestV1,
        _load_object(
            value,
            maximum_bytes=_MAX_REQUEST_BYTES,
            label="Producer capability request",
        ),
        label="Producer capability request",
    )
    if type(request) is not ProducerCapabilityRequestV1:
        _reject(
            ProducerCapabilityValidationCode.WRONG_TYPE,
            "Producer capability request has the wrong typed model.",
        )
    _lookup_registered_bytes(request.profile_id, request.profile_version)
    return request


def get_producer_capability_descriptor(
    *, profile_id: str, profile_version: str
) -> ProducerCapabilityDescriptorV1:
    """Return a detached immutable descriptor for one exact registered profile."""

    request = _validate_model(
        ProducerCapabilityRequestV1,
        {
            "schema_version": PRODUCER_CAPABILITY_REQUEST_SCHEMA_VERSION,
            "profile_id": profile_id,
            "profile_version": profile_version,
        },
        label="Producer capability request",
    )
    if type(request) is not ProducerCapabilityRequestV1:
        _reject(
            ProducerCapabilityValidationCode.WRONG_TYPE,
            "Producer capability request has the wrong typed model.",
        )
    return parse_producer_capability_descriptor(
        _lookup_registered_bytes(request.profile_id, request.profile_version)
    )


def resolve_producer_capability_request(
    value: bytes | Mapping[str, Any],
) -> ProducerCapabilityDescriptorV1:
    """Resolve one untrusted exact-profile request without accepting overrides."""

    request = parse_producer_capability_request(value)
    return get_producer_capability_descriptor(
        profile_id=request.profile_id,
        profile_version=request.profile_version,
    )


def parse_producer_capability_descriptor(
    value: bytes | Mapping[str, Any],
) -> ProducerCapabilityDescriptorV1:
    """Parse only a canonical descriptor identical to package-owned registry data."""

    descriptor = _validate_model(
        ProducerCapabilityDescriptorV1,
        _load_object(
            value,
            maximum_bytes=_MAX_DESCRIPTOR_BYTES,
            label="Producer capability descriptor",
        ),
        label="Producer capability descriptor",
    )
    if type(descriptor) is not ProducerCapabilityDescriptorV1:
        _reject(
            ProducerCapabilityValidationCode.WRONG_TYPE,
            "Producer capability descriptor has the wrong typed model.",
        )
    return _validated_registered_descriptor(descriptor)


def canonical_producer_capability_projection(
    value: ProducerCapabilityDescriptorV1,
) -> dict[str, Any]:
    """Return the exact unsigned projection of one registered descriptor."""

    return _validated_registered_descriptor(value).model_dump(
        mode="json", exclude={"capability_digest"}
    )


def producer_capability_digest(value: ProducerCapabilityDescriptorV1) -> str:
    """Return the domain-separated digest of one registered descriptor."""

    return _descriptor_digest_from_projection(
        canonical_producer_capability_projection(value)
    )


def verify_producer_capability_descriptor(value: object) -> bool:
    """Return whether a typed descriptor is strict, canonical, and registered."""

    try:
        descriptor = _validated_registered_descriptor(value)
        return hmac.compare_digest(
            descriptor.capability_digest,
            producer_capability_digest(descriptor),
        )
    except ProducerCapabilityRejected:
        return False


def serialize_producer_capability_descriptor(
    value: ProducerCapabilityDescriptorV1,
) -> bytes:
    """Return canonical RFC 8785 bytes for one registered descriptor."""

    descriptor = _validated_registered_descriptor(value)
    try:
        content = canonical_json_bytes(descriptor.model_dump(mode="json"))
    except (CanonicalizationError, RecursionError, TypeError, ValueError):
        _reject(
            ProducerCapabilityValidationCode.INVALID_VALUE,
            "Producer capability descriptor is outside the canonical JSON domain.",
        )
    if parse_producer_capability_descriptor(content) != descriptor:
        _reject(
            ProducerCapabilityValidationCode.SEMANTIC_INCONSISTENCY,
            "Producer capability descriptor changed during serialization.",
        )
    return content


__all__ = [
    "DECLARED_EXTERNAL_EVIDENCE_PROFILE_ID",
    "DECLARED_EXTERNAL_EVIDENCE_PROFILE_VERSION",
    "MEASURED_ATTEMPT_RELIABILITY_SOURCE_FIELD",
    "NATIVE_TTFT_METRIC_DEFINITION_ID",
    "NATIVE_TTFT_POPULATION",
    "NATIVE_TTFT_REDUCER_ID",
    "NATIVE_TTFT_SOURCE_FIELD",
    "PRODUCER_CAPABILITY_CANONICALIZATION_VERSION",
    "PRODUCER_CAPABILITY_DESCRIPTOR_SCHEMA_VERSION",
    "PRODUCER_CAPABILITY_DIGEST_DOMAIN",
    "PRODUCER_CAPABILITY_HASH_VERSION",
    "PRODUCER_CAPABILITY_REGISTRY_VERSION",
    "PRODUCER_CAPABILITY_REQUEST_SCHEMA_VERSION",
    "DeclaredExternalEvidenceProfileV1",
    "EngineAdapterIdentityV1",
    "MeasuredAttemptReliabilityObservationV1",
    "NativeTTFTObservationV1",
    "ProducerCapabilityDescriptorV1",
    "ProducerCapabilityObservationsV1",
    "ProducerCapabilityRejected",
    "ProducerCapabilityRequestV1",
    "ProducerCapabilityValidationCode",
    "canonical_producer_capability_projection",
    "get_producer_capability_descriptor",
    "parse_producer_capability_descriptor",
    "parse_producer_capability_request",
    "producer_capability_digest",
    "resolve_producer_capability_request",
    "serialize_producer_capability_descriptor",
    "verify_producer_capability_descriptor",
]
