"""Strict, immutable v0.5 serving-subject identity artifacts.

This module identifies an inference serving subject only.  It does not bind a
workload, qualification scope or context, evidence, execution, verdict, or any
deployment or traffic authority.  A valid digest establishes deterministic
identity and integrity only; it is not a signature, attestation, execution
record, hardware proof, chronology proof, or authorization.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from collections.abc import Mapping
from enum import Enum
from typing import Any, Final

from pydantic import (
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from .canonical import CanonicalizationError, canonical_json_bytes
from .models import FrozenExitSpecModel

SERVING_SUBJECT_SCHEMA_VERSION: Final = "exitspec.serving-subject-manifest.v1"
SERVING_SUBJECT_CANONICALIZATION_VERSION: Final = "rfc8785_jcs_v1"
SERVING_SUBJECT_HASH_VERSION: Final = "sha256_v1"
SERVING_SUBJECT_DIGEST_DOMAIN: Final = b"exitspec-serving-subject-manifest-v1\x00"

_MAX_MANIFEST_BYTES: Final = 16 * 1024
_MAX_JSON_DEPTH: Final = 16
_MAX_JSON_NODES: Final = 512
_MAX_JSON_OBJECT_KEYS: Final = 32
_MAX_JSON_ARRAY_ITEMS: Final = 64
_MAX_JSON_STRING_LENGTH: Final = 8 * 1024
_MAX_JSON_INTEGER: Final = 2_147_483_647
_MAX_RUNTIME_CONFIGURATION_BYTES: Final = 8 * 1024
_MAX_RUNTIME_CONFIGURATION_DEPTH: Final = 8
_MAX_RUNTIME_CONFIGURATION_NODES: Final = 256
_MAX_RUNTIME_CONFIGURATION_OBJECT_KEYS: Final = 32
_MAX_RUNTIME_CONFIGURATION_ARRAY_ITEMS: Final = 32
_MAX_RUNTIME_CONFIGURATION_STRING_LENGTH: Final = 512
_MAX_RUNTIME_CONFIGURATION_INTEGER: Final = 2_147_483_647

_IDENTIFIER_PATTERN: Final = r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$"
_COMPONENT_REVISION_PATTERN: Final = r"^[A-Za-z0-9][A-Za-z0-9._:+-]{6,127}$"
_EXACT_VERSION_PATTERN: Final = (
    r"^v?[0-9]+(?:\.[0-9]+){0,2}(?:-[0-9A-Za-z][0-9A-Za-z.-]{0,63})?"
    r"(?:\+[0-9A-Za-z][0-9A-Za-z.-]{0,63})?$"
)
_HARDWARE_PATTERN: Final = r"^[A-Za-z0-9][A-Za-z0-9._:+/-]{1,127}$"
_TOPOLOGY_PATTERN: Final = r"^[A-Za-z0-9][A-Za-z0-9._:+x/-]{1,127}$"
_RUNTIME_KEY_PATTERN: Final = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
_RUNTIME_KEY_SEGMENT_SEPARATOR: Final = re.compile(r"[_.-]+")
_DIGEST_PATTERN: Final = r"^sha256:[a-f0-9]{64}$"
_UNPINNED_REVISIONS: Final = frozenset(
    {"latest", "main", "master", "head", "default", "stable"}
)
_DENIED_RUNTIME_KEY_SEGMENTS: Final = frozenset(
    {
        "authorization",
        "authorisation",
        "credential",
        "credentials",
        "deploy",
        "deployment",
        "execution",
        "executions",
        "password",
        "passwords",
        "provider",
        "providers",
        "run",
        "runs",
        "secret",
        "secrets",
        "token",
        "tokens",
        "traffic",
    }
)
_DENIED_RUNTIME_KEY_PAIRS: Final = frozenset(
    {("api", "key"), ("gpu", "reservation"), ("private", "key")}
)


class ServingSubjectValidationCode(str, Enum):
    """Stable, content-safe failure classes for the serving-subject boundary."""

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


class ServingSubjectRejected(ValueError):
    """A subject artifact failed the public, fail-closed identity boundary."""

    def __init__(self, code: ServingSubjectValidationCode, message: str) -> None:
        self.code = ServingSubjectValidationCode(code)
        super().__init__(message)


class _JsonBoundaryError(ValueError):
    def __init__(self, code: ServingSubjectValidationCode) -> None:
        self.code = code
        super().__init__(code.value)


def _reject(code: ServingSubjectValidationCode, message: str) -> None:
    raise ServingSubjectRejected(code, message)


class _StrictFrozenSubjectModel(FrozenExitSpecModel):
    """Frozen strict model base for all material serving-subject fields."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        revalidate_instances="always",
    )


class PinnedComponentIdentityV1(_StrictFrozenSubjectModel):
    """One exact model or tokenizer identity, including a non-floating revision."""

    component_id: str = Field(pattern=_IDENTIFIER_PATTERN, max_length=128)
    revision: str = Field(pattern=_COMPONENT_REVISION_PATTERN, max_length=128)

    @field_validator("revision")
    @classmethod
    def require_pinned_revision(cls, value: str) -> str:
        if value.casefold() in _UNPINNED_REVISIONS:
            raise ValueError("Revision must be pinned.")
        return value


class ServingEngineIdentityV1(_StrictFrozenSubjectModel):
    """Exact serving engine identity and version."""

    engine_id: str = Field(pattern=_IDENTIFIER_PATTERN, max_length=128)
    engine_version: str = Field(pattern=_EXACT_VERSION_PATTERN, max_length=128)

    @field_validator("engine_version")
    @classmethod
    def require_pinned_version(cls, value: str) -> str:
        if value.casefold() in _UNPINNED_REVISIONS:
            raise ValueError("Engine version must be pinned.")
        return value


class HardwareIdentityV1(_StrictFrozenSubjectModel):
    """Required hardware class and topology labels for the selected profile."""

    hardware_class: str = Field(pattern=_HARDWARE_PATTERN, max_length=128)
    topology: str = Field(pattern=_TOPOLOGY_PATTERN, max_length=128)


class ProfileAdapterIdentityV1(_StrictFrozenSubjectModel):
    """The profile and adapter that define this subject's material fields."""

    profile_id: str = Field(pattern=_IDENTIFIER_PATTERN, max_length=128)
    profile_version: str = Field(pattern=_EXACT_VERSION_PATTERN, max_length=128)
    adapter_id: str = Field(pattern=_IDENTIFIER_PATTERN, max_length=128)
    adapter_version: str = Field(pattern=_EXACT_VERSION_PATTERN, max_length=128)

    @field_validator("profile_version", "adapter_version")
    @classmethod
    def require_pinned_versions(cls, value: str) -> str:
        if value.casefold() in _UNPINNED_REVISIONS:
            raise ValueError("Profile and adapter versions must be pinned.")
        return value


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _JsonBoundaryError(ServingSubjectValidationCode.DUPLICATE_FIELD)
        result[key] = value
    return result


def _bounded_integer(raw: str, *, maximum: int) -> int:
    value = int(raw)
    if abs(value) > maximum:
        raise _JsonBoundaryError(ServingSubjectValidationCode.OVERSIZED)
    return value


def _reject_float(_: str) -> None:
    raise _JsonBoundaryError(ServingSubjectValidationCode.INVALID_VALUE)


def _reject_constant(_: str) -> None:
    raise _JsonBoundaryError(ServingSubjectValidationCode.INVALID_VALUE)


def _runtime_key_segments(key: str) -> tuple[str, ...]:
    """Return case-insensitive dotted/dashed/camel-case key path segments."""

    separated = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", ".", key)
    separated = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", ".", separated)
    return tuple(
        segment.casefold()
        for segment in _RUNTIME_KEY_SEGMENT_SEPARATOR.split(separated)
        if segment
    )


def _is_prohibited_runtime_key(key: str) -> bool:
    segments = _runtime_key_segments(key)
    if any(segment in _DENIED_RUNTIME_KEY_SEGMENTS for segment in segments):
        return True
    if any(
        pair == segments[index : index + 2]
        for pair in _DENIED_RUNTIME_KEY_PAIRS
        for index in range(len(segments) - 1)
    ):
        return True
    return "".join(segments) in {"apikey", "gpureservation", "privatekey"}


def _parse_runtime_configuration(value: str) -> dict[str, Any]:
    """Accept one bounded, canonical JSON-object runtime configuration string.

    The accepted generic domain is explicit: objects with identifier keys,
    arrays, strings, booleans, null, and bounded integers. Floating-point
    values, non-finite values, duplicate keys, prohibited key semantics, and
    all values outside the stated depth/node/string/collection limits are
    rejected. Keys are compared case-insensitively by dotted, dashed,
    underscored, or camel-case path segment; the rejected vocabulary is fixed
    in ``_DENIED_RUNTIME_KEY_SEGMENTS`` and ``_DENIED_RUNTIME_KEY_PAIRS``.
    """

    if type(value) is not str:
        raise ValueError("Runtime configuration must be a string.")
    try:
        content = value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ValueError("Runtime configuration is not valid Unicode.") from error
    if not 0 < len(content) <= _MAX_RUNTIME_CONFIGURATION_BYTES:
        raise ValueError("Runtime configuration exceeds its byte limit.")
    try:
        payload = json.loads(
            value,
            object_pairs_hook=_unique_object,
            parse_int=lambda raw: _bounded_integer(
                raw, maximum=_MAX_RUNTIME_CONFIGURATION_INTEGER
            ),
            parse_float=_reject_float,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, _JsonBoundaryError) as error:
        raise ValueError(
            "Runtime configuration is outside its JSON boundary."
        ) from error
    if type(payload) is not dict:
        raise ValueError("Runtime configuration must be one JSON object.")

    nodes = 0

    def walk(current: object, depth: int) -> None:
        nonlocal nodes
        nodes += 1
        if nodes > _MAX_RUNTIME_CONFIGURATION_NODES:
            raise ValueError("Runtime configuration exceeds its node limit.")
        if depth > _MAX_RUNTIME_CONFIGURATION_DEPTH:
            raise ValueError("Runtime configuration exceeds its nesting limit.")
        if type(current) is str:
            if len(current) > _MAX_RUNTIME_CONFIGURATION_STRING_LENGTH:
                raise ValueError("Runtime configuration contains an oversized string.")
            return
        if current is None or type(current) is bool:
            return
        if type(current) is int:
            if abs(current) > _MAX_RUNTIME_CONFIGURATION_INTEGER:
                raise ValueError("Runtime configuration contains an unbounded integer.")
            return
        if type(current) is dict:
            if len(current) > _MAX_RUNTIME_CONFIGURATION_OBJECT_KEYS:
                raise ValueError("Runtime configuration contains too many object keys.")
            for key, child in current.items():
                if type(key) is not str or _RUNTIME_KEY_PATTERN.fullmatch(key) is None:
                    raise ValueError(
                        "Runtime configuration contains an unsupported key."
                    )
                if _is_prohibited_runtime_key(key):
                    raise ValueError("Runtime configuration contains a prohibited key.")
                walk(child, depth + 1)
            return
        if type(current) is list:
            if len(current) > _MAX_RUNTIME_CONFIGURATION_ARRAY_ITEMS:
                raise ValueError("Runtime configuration contains too many array items.")
            for child in current:
                walk(child, depth + 1)
            return
        raise ValueError("Runtime configuration contains an unsupported value.")

    walk(payload, 0)
    try:
        canonical = canonical_json_bytes(payload)
    except CanonicalizationError as error:
        raise ValueError(
            "Runtime configuration is outside the canonical domain."
        ) from error
    if canonical != content:
        raise ValueError("Runtime configuration is not canonical JSON.")
    return payload


class _ServingSubjectUnsignedV1(_StrictFrozenSubjectModel):
    """Validated unsigned projection used only to derive the public manifest."""

    schema_version: str = Field(
        pattern=rf"^{re.escape(SERVING_SUBJECT_SCHEMA_VERSION)}$",
        max_length=len(SERVING_SUBJECT_SCHEMA_VERSION),
    )
    model: PinnedComponentIdentityV1
    tokenizer: PinnedComponentIdentityV1
    engine: ServingEngineIdentityV1
    runtime_artifact_digest: str | None = Field(pattern=_DIGEST_PATTERN)
    runtime_configuration_json: str = Field(
        min_length=2, max_length=_MAX_RUNTIME_CONFIGURATION_BYTES
    )
    launch_arguments_digest: str = Field(pattern=_DIGEST_PATTERN)
    hardware: HardwareIdentityV1
    profile: ProfileAdapterIdentityV1
    routing_policy_id: str | None = Field(pattern=_IDENTIFIER_PATTERN, max_length=128)
    routing_policy_digest: str | None = Field(pattern=_DIGEST_PATTERN)

    @field_validator("runtime_configuration_json")
    @classmethod
    def require_canonical_runtime_configuration(cls, value: str) -> str:
        _parse_runtime_configuration(value)
        return value

    @model_validator(mode="after")
    def require_routing_pair(self) -> _ServingSubjectUnsignedV1:
        if (self.routing_policy_id is None) != (self.routing_policy_digest is None):
            raise ValueError("Routing policy identity and digest must be all-or-none.")
        return self


def _subject_digest_from_projection(projection: Mapping[str, Any]) -> str:
    try:
        content = canonical_json_bytes(projection)
    except (CanonicalizationError, TypeError, ValueError):
        _reject(
            ServingSubjectValidationCode.INVALID_VALUE,
            "Serving subject projection is outside the canonical JSON domain.",
        )
    return (
        "sha256:" + hashlib.sha256(SERVING_SUBJECT_DIGEST_DOMAIN + content).hexdigest()
    )


class ServingSubjectManifestV1(_ServingSubjectUnsignedV1):
    """The immutable, digest-bound identity of one serving subject only."""

    subject_digest: str = Field(pattern=_DIGEST_PATTERN)

    @model_validator(mode="after")
    def require_matching_subject_digest(self) -> ServingSubjectManifestV1:
        projection = self.model_dump(mode="json", exclude={"subject_digest"})
        expected = _subject_digest_from_projection(projection)
        if not hmac.compare_digest(self.subject_digest, expected):
            raise ValueError("subject_digest does not match the unsigned projection.")
        return self


def _walk_bounded_json(value: object, *, depth: int, node_count: list[int]) -> None:
    node_count[0] += 1
    if node_count[0] > _MAX_JSON_NODES:
        raise _JsonBoundaryError(ServingSubjectValidationCode.OVERSIZED)
    if depth > _MAX_JSON_DEPTH:
        raise _JsonBoundaryError(ServingSubjectValidationCode.OVERSIZED)
    if type(value) is str:
        if len(value) > _MAX_JSON_STRING_LENGTH:
            raise _JsonBoundaryError(ServingSubjectValidationCode.OVERSIZED)
        return
    if value is None or type(value) in {bool, int}:
        return
    if type(value) is dict:
        if len(value) > _MAX_JSON_OBJECT_KEYS:
            raise _JsonBoundaryError(ServingSubjectValidationCode.OVERSIZED)
        for key, child in value.items():
            if type(key) is not str or len(key) > _MAX_JSON_STRING_LENGTH:
                raise _JsonBoundaryError(ServingSubjectValidationCode.OVERSIZED)
            _walk_bounded_json(child, depth=depth + 1, node_count=node_count)
        return
    if type(value) is list:
        if len(value) > _MAX_JSON_ARRAY_ITEMS:
            raise _JsonBoundaryError(ServingSubjectValidationCode.OVERSIZED)
        for child in value:
            _walk_bounded_json(child, depth=depth + 1, node_count=node_count)
        return
    raise _JsonBoundaryError(ServingSubjectValidationCode.WRONG_TYPE)


def _decode_object(content: bytes, *, require_canonical: bool) -> dict[str, Any]:
    if len(content) > _MAX_MANIFEST_BYTES:
        _reject(
            ServingSubjectValidationCode.OVERSIZED,
            "Serving subject manifest exceeds its byte limit.",
        )
    try:
        payload = json.loads(
            content.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_int=lambda raw: _bounded_integer(raw, maximum=_MAX_JSON_INTEGER),
            parse_float=_reject_float,
            parse_constant=_reject_constant,
        )
    except UnicodeDecodeError:
        _reject(
            ServingSubjectValidationCode.WRONG_TYPE,
            "Serving subject manifest is not UTF-8 JSON.",
        )
    except _JsonBoundaryError as error:
        _reject(error.code, "Serving subject manifest failed its JSON boundary.")
    except (json.JSONDecodeError, TypeError, ValueError):
        _reject(
            ServingSubjectValidationCode.WRONG_TYPE,
            "Serving subject manifest is not one valid JSON object.",
        )
    if type(payload) is not dict:
        _reject(
            ServingSubjectValidationCode.WRONG_TYPE,
            "Serving subject manifest must be one JSON object.",
        )
    try:
        _walk_bounded_json(payload, depth=0, node_count=[0])
        canonical = canonical_json_bytes(payload)
    except _JsonBoundaryError as error:
        _reject(error.code, "Serving subject manifest failed its JSON boundary.")
    except (CanonicalizationError, TypeError, ValueError):
        _reject(
            ServingSubjectValidationCode.INVALID_VALUE,
            "Serving subject manifest is outside the canonical JSON domain.",
        )
    if require_canonical and canonical != content:
        _reject(
            ServingSubjectValidationCode.NON_CANONICAL,
            "Serving subject manifest is not RFC 8785 canonical JSON.",
        )
    return payload


def _load_object(value: bytes | Mapping[str, Any]) -> dict[str, Any]:
    if type(value) is bytes:
        return _decode_object(value, require_canonical=True)
    if type(value) is not dict:
        _reject(
            ServingSubjectValidationCode.WRONG_TYPE,
            "Serving subject manifest must be bytes or one JSON object.",
        )
    try:
        content = canonical_json_bytes(value)
    except (CanonicalizationError, TypeError, ValueError):
        _reject(
            ServingSubjectValidationCode.INVALID_VALUE,
            "Serving subject manifest is outside the canonical JSON domain.",
        )
    return _decode_object(content, require_canonical=False)


def _classify_validation_error(error: ValidationError) -> ServingSubjectValidationCode:
    details = error.errors()
    for detail in details:
        location = tuple(detail.get("loc", ()))
        error_type = str(detail.get("type", ""))
        if error_type == "extra_forbidden":
            return ServingSubjectValidationCode.EXTRA_FIELD
        if error_type == "missing":
            return ServingSubjectValidationCode.MISSING_FIELD
        if location and location[0] == "schema_version":
            return ServingSubjectValidationCode.WRONG_VERSION
        if location and location[0] == "subject_digest":
            return ServingSubjectValidationCode.INVALID_DIGEST
        if error_type in {"too_long", "string_too_long", "too_short"}:
            return ServingSubjectValidationCode.OVERSIZED
        if error_type.endswith("_type") or error_type in {
            "model_type",
            "tuple_type",
            "list_type",
        }:
            return ServingSubjectValidationCode.WRONG_TYPE
        context = detail.get("ctx")
        if isinstance(context, dict) and "error" in context:
            message = str(context["error"])
            if "subject_digest" in message:
                return ServingSubjectValidationCode.INVALID_DIGEST
            if "all-or-none" in message:
                return ServingSubjectValidationCode.SEMANTIC_INCONSISTENCY
    return ServingSubjectValidationCode.INVALID_VALUE


def _validate_model(
    model_type: type[_ServingSubjectUnsignedV1 | ServingSubjectManifestV1],
    payload: dict[str, Any],
    *,
    label: str,
) -> _ServingSubjectUnsignedV1 | ServingSubjectManifestV1:
    try:
        model = model_type.model_validate_json(
            canonical_json_bytes(payload), strict=True
        )
    except ValidationError as error:
        _reject(
            _classify_validation_error(error),
            f"{label} failed strict validation.",
        )
    except (CanonicalizationError, TypeError, ValueError):
        _reject(
            ServingSubjectValidationCode.INVALID_VALUE,
            f"{label} failed strict validation.",
        )
    return model


def _validated_manifest(value: object) -> ServingSubjectManifestV1:
    if type(value) is not ServingSubjectManifestV1:
        _reject(
            ServingSubjectValidationCode.WRONG_TYPE,
            "A typed ServingSubjectManifestV1 is required.",
        )
    try:
        raw_state = object.__getattribute__(value, "__dict__")
        extra_state = object.__getattribute__(value, "__pydantic_extra__")
    except AttributeError:
        _reject(
            ServingSubjectValidationCode.WRONG_TYPE,
            "Serving subject manifest has no inspectable raw state.",
        )
    expected_fields = set(ServingSubjectManifestV1.model_fields)
    if type(raw_state) is not dict:
        _reject(
            ServingSubjectValidationCode.WRONG_TYPE,
            "Serving subject manifest raw state must be one object.",
        )
    if set(raw_state) - expected_fields or extra_state:
        _reject(
            ServingSubjectValidationCode.EXTRA_FIELD,
            "Serving subject manifest contains undocumented raw fields.",
        )
    if expected_fields - set(raw_state):
        _reject(
            ServingSubjectValidationCode.MISSING_FIELD,
            "Serving subject manifest is missing a raw field.",
        )
    try:
        serialized_state = value.model_dump(mode="json", warnings="error")
    except (TypeError, ValueError):
        _reject(
            ServingSubjectValidationCode.INVALID_VALUE,
            "Serving subject manifest has an unsafe raw field value.",
        )
    validated = _validate_model(
        ServingSubjectManifestV1,
        serialized_state,
        label="Serving subject manifest",
    )
    if type(validated) is not ServingSubjectManifestV1:
        _reject(
            ServingSubjectValidationCode.WRONG_TYPE,
            "Serving subject manifest has the wrong typed model.",
        )
    return validated


def create_serving_subject_manifest(
    value: Mapping[str, Any],
) -> ServingSubjectManifestV1:
    """Create one digest-bound manifest from its validated unsigned projection."""

    if type(value) is not dict:
        _reject(
            ServingSubjectValidationCode.WRONG_TYPE,
            "Serving subject input must be one JSON object.",
        )
    if "subject_digest" in value:
        _reject(
            ServingSubjectValidationCode.EXTRA_FIELD,
            "Serving subject input must not include a derived digest.",
        )
    unsigned = _validate_model(
        _ServingSubjectUnsignedV1,
        _load_object(value),
        label="Serving subject input",
    )
    projection = unsigned.model_dump(mode="json")
    digest = _subject_digest_from_projection(projection)
    manifest = _validate_model(
        ServingSubjectManifestV1,
        {**projection, "subject_digest": digest},
        label="Serving subject manifest",
    )
    if type(manifest) is not ServingSubjectManifestV1:
        _reject(
            ServingSubjectValidationCode.WRONG_TYPE,
            "Serving subject manifest has the wrong typed model.",
        )
    return manifest


def parse_serving_subject_manifest(
    value: bytes | Mapping[str, Any],
) -> ServingSubjectManifestV1:
    """Strictly parse one canonical digest-bound serving-subject manifest."""

    manifest = _validate_model(
        ServingSubjectManifestV1,
        _load_object(value),
        label="Serving subject manifest",
    )
    if type(manifest) is not ServingSubjectManifestV1:
        _reject(
            ServingSubjectValidationCode.WRONG_TYPE,
            "Serving subject manifest has the wrong typed model.",
        )
    return manifest


def canonical_serving_subject_projection(
    value: ServingSubjectManifestV1,
) -> dict[str, Any]:
    """Return the validated unsigned projection used for identity derivation."""

    validated = _validated_manifest(value)
    return validated.model_dump(mode="json", exclude={"subject_digest"})


def serving_subject_digest(value: ServingSubjectManifestV1) -> str:
    """Return the domain-separated identity digest for one validated manifest."""

    return _subject_digest_from_projection(canonical_serving_subject_projection(value))


def verify_serving_subject_manifest(value: object) -> bool:
    """Return whether a typed manifest is strict, immutable, and digest-valid."""

    try:
        validated = _validated_manifest(value)
        return hmac.compare_digest(
            validated.subject_digest, serving_subject_digest(validated)
        )
    except ServingSubjectRejected:
        return False


def serialize_serving_subject_manifest(value: ServingSubjectManifestV1) -> bytes:
    """Return canonical RFC 8785 bytes for one validated subject manifest."""

    validated = _validated_manifest(value)
    try:
        content = canonical_json_bytes(validated.model_dump(mode="json"))
    except (CanonicalizationError, TypeError, ValueError):
        _reject(
            ServingSubjectValidationCode.INVALID_VALUE,
            "Serving subject manifest is outside the canonical JSON domain.",
        )
    parsed = parse_serving_subject_manifest(content)
    if parsed != validated:
        _reject(
            ServingSubjectValidationCode.SEMANTIC_INCONSISTENCY,
            "Serving subject manifest changed during serialization.",
        )
    return content


__all__ = [
    "SERVING_SUBJECT_CANONICALIZATION_VERSION",
    "SERVING_SUBJECT_DIGEST_DOMAIN",
    "SERVING_SUBJECT_HASH_VERSION",
    "SERVING_SUBJECT_SCHEMA_VERSION",
    "HardwareIdentityV1",
    "PinnedComponentIdentityV1",
    "ProfileAdapterIdentityV1",
    "ServingEngineIdentityV1",
    "ServingSubjectManifestV1",
    "ServingSubjectRejected",
    "ServingSubjectValidationCode",
    "canonical_serving_subject_projection",
    "create_serving_subject_manifest",
    "parse_serving_subject_manifest",
    "serialize_serving_subject_manifest",
    "serving_subject_digest",
    "verify_serving_subject_manifest",
]
