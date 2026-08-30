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

from datetime import datetime
from enum import Enum
import json
import re
from types import UnionType
from typing import Any, Final, Mapping, Union, get_args, get_origin

from pydantic import BaseModel, Field, ValidationError, field_validator

from .canonical import CanonicalizationError, canonical_json_bytes
from .contracts import contract_digest, verify_contract_digest
from .models import (
    FrozenExitSpecModel,
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

# Public parsing and binding always use this existing lifecycle authority.
# The routing criterion is only a typed member of POCContract.criteria.
_MODEL_FIELD_PRECEDENCE: Final = (
    "producer_verdict",
    "extra",
    "missing",
    "wrong_type",
    "oversized",
    "invalid_digest",
    "invalid_bound",
    "wrong_version",
    "invalid_value",
    "semantic",
)
_MODEL_ERROR_PRIORITY: Final = {
    name: index for index, name in enumerate(_MODEL_FIELD_PRECEDENCE)
}


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


def routing_qualification_contract_digest(value: POCContract) -> str:
    """Return the existing frozen ``POCContract`` canonical hash.

    The routing criterion has no independent contract authority or persisted
    contract digest.  It contributes to the outer ExitSpec contract hash only
    after the complete frozen POC contract has been revalidated.
    """

    validated = _validated_frozen_routing_contract(value)
    return contract_digest(validated)


def parse_routing_qualification_contract(
    value: bytes | Mapping[str, Any],
) -> POCContract:
    """Strictly parse one frozen POCContract containing one routing criterion."""

    payload = _load_object(value, label="routing qualification contract")
    _reject_producer_verdict_aliases(payload)
    criteria = payload.get("criteria")
    if type(criteria) is list and len(criteria) == 1 and type(criteria[0]) is dict:
        criterion_payload = criteria[0]
        if criterion_payload.get("criterion_type") == "routing_qualification_v1":
            _validate_model(
                RoutingQualificationCriterionV1,
                criterion_payload,
                label="routing qualification contract.criteria[0]",
                path_prefix="criteria[0]",
            )
    parsed = _validate_model(
        POCContract, payload, label="routing qualification contract"
    )
    return _validated_frozen_routing_contract(parsed)


def serialize_routing_qualification_contract(
    value: POCContract,
) -> bytes:
    """Return canonical bytes after rerunning strict B9 validation."""

    validated = _validated_frozen_routing_contract(value)
    content = canonical_json_bytes(validated.model_dump(mode="json"))
    parsed = parse_routing_qualification_contract(content)
    if parsed != validated:
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

    validated = _validated_evidence_fixture(value)
    content = canonical_json_bytes(validated.model_dump(mode="json"))
    parsed = parse_routing_qualification_evidence_fixture(content)
    if parsed != validated:
        _reject(
            RoutingQualificationValidationCode.SEMANTIC_INCONSISTENCY,
            "Routing qualification evidence changed during serialization.",
        )
    return content


def validate_routing_qualification_evidence_fixture(
    contract: POCContract,
    evidence: "RoutingQualificationEvidenceFixtureV1",
) -> "RoutingQualificationEvidenceFixtureV1":
    """Bind synthetic observed references to a frozen routing contract.

    This checks identity and provenance bindings only.  It does not check
    route quality, combine repetitions, calculate statistics, or emit PASS,
    FAIL, BLOCKED, NOT_PROVEN, or any other acceptance verdict.
    """

    frozen_contract = _validated_frozen_routing_contract(contract)
    validated_evidence = _validated_evidence_fixture(evidence)
    criterion = frozen_contract.criteria[0]
    expected_digest = frozen_contract.canonical_hash
    if validated_evidence.contract_sha256 != expected_digest:
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
        "request_trace_id": criterion.request_trace.trace_id,
        "request_trace_sha256": criterion.request_trace.trace_sha256,
        "failure_injection_id": criterion.failure_injection.configuration_id,
        "failure_injection_sha256": criterion.failure_injection.configuration_sha256,
        "environment_id": criterion.serving.execution_environment.environment_id,
        "environment_sha256": criterion.serving.execution_environment.environment_sha256,
        "telemetry_capsule_type": criterion.telemetry.capsule_type,
        "route_decision_receipt_type": criterion.route_decision_receipts.receipt_type,
    }
    for name, expected_value in expected.items():
        if getattr(validated_evidence, name) != expected_value:
            _reject(
                RoutingQualificationValidationCode.CONTRACT_BINDING_MISMATCH,
                f"Evidence fixture field {name} does not match the frozen contract.",
                name,
            )
    if validated_evidence.repetition_index > criterion.run_policy.default_repetitions:
        _reject(
            RoutingQualificationValidationCode.INVALID_BOUND,
            "Evidence repetition_index exceeds the frozen default repetition bound.",
            "repetition_index",
        )
    if (
        len(validated_evidence.route_decision_receipt_ids)
        != criterion.trial_order.total_assignments
    ):
        _reject(
            RoutingQualificationValidationCode.SEMANTIC_INCONSISTENCY,
            "Evidence receipt count must equal frozen total_assignments.",
            "route_decision_receipt_ids",
        )
    return validated_evidence


def _validated_frozen_routing_contract(contract: object) -> POCContract:
    if type(contract) is not POCContract:
        _reject(
            RoutingQualificationValidationCode.WRONG_TYPE,
            "A frozen POCContract is required; a routing criterion is not a contract.",
        )
    validated = _revalidate_typed_model(
        contract, POCContract, label="routing qualification contract"
    )
    if validated.status.value != "FROZEN":
        _reject(
            RoutingQualificationValidationCode.CONTRACT_BINDING_MISMATCH,
            "Routing qualification requires a FROZEN POCContract.",
            "status",
        )
    if not verify_contract_digest(validated):
        _reject(
            RoutingQualificationValidationCode.CONTRACT_BINDING_MISMATCH,
            "Routing evidence requires a digest-valid frozen contract.",
        )
    criteria = tuple(
        criterion
        for criterion in validated.criteria
        if type(criterion) is RoutingQualificationCriterionV1
    )
    if len(criteria) != 1 or len(validated.criteria) != 1:
        _reject(
            RoutingQualificationValidationCode.CONTRACT_BINDING_MISMATCH,
            "Routing evidence requires exactly one frozen routing qualification criterion.",
        )
    return validated


class RoutingEvidenceProvenanceV1(FrozenExitSpecModel):
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

    @field_validator("captured_at")
    @classmethod
    def require_real_utc_second_timestamp(cls, value: str) -> str:
        try:
            datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
        except ValueError as error:
            raise ValueError(
                "captured_at must be a real UTC timestamp at whole-second precision."
            ) from error
        return value


class RoutingQualificationEvidenceFixtureV1(FrozenExitSpecModel):
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
    request_trace_id: str = Field(pattern=r"^[a-z][a-z0-9._-]{2,127}$", max_length=128)
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


def _validated_evidence_fixture(value: object) -> RoutingQualificationEvidenceFixtureV1:
    if type(value) is not RoutingQualificationEvidenceFixtureV1:
        _reject(
            RoutingQualificationValidationCode.WRONG_TYPE,
            "A typed routing qualification evidence fixture is required.",
        )
    return _revalidate_typed_model(
        value,
        RoutingQualificationEvidenceFixtureV1,
        label="routing qualification evidence fixture",
    )


def _revalidate_typed_model(
    value: object,
    model_type: type[Any],
    *,
    label: str,
) -> Any:
    """Revalidate an object's complete raw Pydantic state before any dump.

    ``model_copy`` and ``model_construct`` intentionally bypass normal
    Pydantic validation.  This walk checks the raw ``__dict__`` (including
    nested models) for missing/unknown fields before strict validation, so a
    serializer or digest path cannot sanitize an invalid typed object through
    ``model_dump``.
    """

    if type(value) is not model_type:
        _reject(
            RoutingQualificationValidationCode.WRONG_TYPE,
            f"{label} has the wrong typed model.",
        )
    raw = _raw_model_state(value, path=None)
    try:
        return model_type.model_validate(raw, strict=True)
    except ValidationError as error:
        code, path = _classify_validation_error(error)
        _reject(code, f"{label} failed strict revalidation.", path)
    except (TypeError, ValueError):
        _reject(
            RoutingQualificationValidationCode.WRONG_TYPE,
            f"{label} failed strict revalidation.",
        )


def _raw_model_state(value: BaseModel, *, path: str | None) -> dict[str, Any]:
    model_type = type(value)
    field_names = set(model_type.model_fields)
    try:
        raw_state = object.__getattribute__(value, "__dict__")
    except AttributeError:
        _reject(
            RoutingQualificationValidationCode.WRONG_TYPE,
            "Typed model has no inspectable raw state.",
            path,
        )
    if type(raw_state) is not dict:
        _reject(
            RoutingQualificationValidationCode.WRONG_TYPE,
            "Typed model raw state must be one object.",
            path,
        )
    _require_string_keys(raw_state, _join_path(path, "__dict__"))
    unexpected = sorted(set(raw_state) - field_names)
    if unexpected:
        _reject_producer_aliases_in_names(unexpected, path)
        _reject(
            RoutingQualificationValidationCode.EXTRA_FIELD,
            "Typed model contains an undocumented raw field.",
            _join_path(path, unexpected[0]),
        )
    try:
        extra_state = object.__getattribute__(value, "__pydantic_extra__")
    except AttributeError:
        extra_state = None
    extra_path = _join_path(path, "__pydantic_extra__")
    if extra_state is not None:
        if type(extra_state) is not dict:
            _reject(
                RoutingQualificationValidationCode.WRONG_TYPE,
                "Typed model extra state must be one object.",
                extra_path,
            )
        _require_string_keys(extra_state, extra_path)
        extra_names = sorted(extra_state)
        if extra_names:
            _reject_producer_aliases_in_names(extra_names, extra_path)
            _reject(
                RoutingQualificationValidationCode.EXTRA_FIELD,
                "Typed model contains undocumented extra state.",
                _join_path(extra_path, extra_names[0]),
            )
    missing = sorted(field_names - set(raw_state))
    if missing:
        _reject(
            RoutingQualificationValidationCode.MISSING_FIELD,
            "Typed model is missing a raw field.",
            _join_path(path, missing[0]),
        )
    try:
        private_state = object.__getattribute__(value, "__pydantic_private__")
    except AttributeError:
        private_state = None
    private_path = _join_path(path, "__pydantic_private__")
    if private_state is not None:
        if type(private_state) is not dict:
            _reject(
                RoutingQualificationValidationCode.WRONG_TYPE,
                "Typed model private state must be one object.",
                private_path,
            )
        _require_string_keys(private_state, private_path)
        private_names = sorted(private_state)
        if private_names:
            _reject_producer_aliases_in_names(private_names, private_path)
            _reject(
                RoutingQualificationValidationCode.EXTRA_FIELD,
                "Typed model contains undocumented private state.",
                _join_path(private_path, private_names[0]),
            )

    state: dict[str, Any] = {}
    for field_name in sorted(field_names):
        annotation = model_type.model_fields[field_name].annotation
        state[field_name] = _raw_value(
            raw_state[field_name],
            annotation,
            path=_join_path(path, field_name),
        )
    return state


def _raw_value(value: object, annotation: object, *, path: str | None) -> object:
    if isinstance(value, BaseModel):
        allowed_types = _model_types(annotation)
        if not allowed_types or type(value) not in allowed_types:
            _reject(
                RoutingQualificationValidationCode.WRONG_TYPE,
                "Nested typed model has the wrong model type.",
                path,
            )
        raw_state = _raw_model_state(value, path=path)
        try:
            type(value).model_validate(raw_state, strict=True)
        except ValidationError as error:
            code, error_path = _classify_validation_error(error)
            _reject(
                code,
                "Nested typed model failed strict revalidation.",
                _prefix_path(path, error_path),
            )
        except (TypeError, ValueError):
            _reject(
                RoutingQualificationValidationCode.WRONG_TYPE,
                "Nested typed model failed strict revalidation.",
                path,
            )
        return raw_state
    if _model_types(annotation) and value is not None:
        _reject(
            RoutingQualificationValidationCode.WRONG_TYPE,
            "A nested model field cannot be replaced by a raw mapping.",
            path,
        )
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin is tuple and type(value) is not tuple:
        _reject(
            RoutingQualificationValidationCode.WRONG_TYPE,
            "A tuple field cannot be replaced by another collection type.",
            path,
        )
    if origin is list and type(value) is not list:
        _reject(
            RoutingQualificationValidationCode.WRONG_TYPE,
            "A list field cannot be replaced by another collection type.",
            path,
        )
    if type(value) is tuple:
        item_annotation = args[0] if args and args[-1] is Ellipsis else None
        if item_annotation is not None:
            return tuple(
                _raw_value(item, item_annotation, path=_index_path(path, index))
                for index, item in enumerate(value)
            )
        return value
    if type(value) is list:
        item_annotation = args[0] if args else None
        if origin is list and item_annotation is not None:
            return [
                _raw_value(item, item_annotation, path=_index_path(path, index))
                for index, item in enumerate(value)
            ]
        return value
    if type(value) is dict:
        return {
            key: _raw_value(child, None, path=_join_path(path, str(key)))
            for key, child in value.items()
        }
    return value


def _model_types(annotation: object) -> tuple[type[BaseModel], ...]:
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return (annotation,)
    origin = get_origin(annotation)
    if origin in (Union, UnionType):
        types: list[type[BaseModel]] = []
        for child in get_args(annotation):
            types.extend(_model_types(child))
        return tuple(types)
    return ()


def _join_path(path: str | None, name: str) -> str:
    return f"{path}.{name}" if path else name


def _prefix_path(prefix: str | None, path: str | None) -> str | None:
    if prefix is None:
        return path
    if path is None:
        return prefix
    if path.startswith("["):
        return f"{prefix}{path}"
    return f"{prefix}.{path}"


def _require_string_keys(value: dict[object, Any], path: str) -> None:
    if any(type(key) is not str for key in value):
        _reject(
            RoutingQualificationValidationCode.WRONG_TYPE,
            "Typed model internal-state keys must be strings.",
            path,
        )


def _index_path(path: str | None, index: int) -> str:
    return f"{path}[{index}]" if path else f"[{index}]"


def _is_producer_verdict_alias(name: str) -> bool:
    normalized = name.lower()
    return (
        ("verdict" in normalized and normalized != "verdict_boundary")
        or normalized in {"acceptance", "decision"}
        or normalized.endswith("_decision")
    )


def _reject_producer_aliases_in_names(names: list[str], path: str | None) -> None:
    for name in sorted(names):
        if _is_producer_verdict_alias(name):
            _reject(
                RoutingQualificationValidationCode.PRODUCER_VERDICT_FORBIDDEN,
                "Producer acceptance verdict/decision fields are forbidden.",
                _join_path(path, name),
            )


def _reject_producer_verdict_aliases(payload: Mapping[str, Any]) -> None:
    matches: list[tuple[str, ...]] = []

    def walk(value: object, path: tuple[str, ...]) -> None:
        if type(value) is dict:
            for key in sorted(value):
                key_path = path + (str(key),)
                if _is_producer_verdict_alias(str(key)):
                    matches.append(key_path)
                walk(value[key], key_path)
        elif type(value) is list:
            for index, child in enumerate(value):
                walk(child, path + (f"[{index}]",))

    walk(payload, ())
    if matches:
        first = min(matches)
        _reject(
            RoutingQualificationValidationCode.PRODUCER_VERDICT_FORBIDDEN,
            "Producer acceptance verdict/decision fields are forbidden.",
            _components_path(first),
        )


def _components_path(components: tuple[str, ...]) -> str:
    path: str | None = None
    for component in components:
        if component.startswith("["):
            path = f"{path or ''}{component}"
        else:
            path = _join_path(path, component)
    return path or "<root>"


def _load_object(
    value: bytes | Mapping[str, Any],
    *,
    label: str,
    max_json_integer: int = _MAX_JSON_INTEGER,
    max_bytes: int | None = None,
) -> dict[str, Any]:
    if isinstance(value, Mapping):
        if type(value) is not dict:
            _reject(
                RoutingQualificationValidationCode.WRONG_TYPE,
                f"{label} must be one JSON object.",
            )
        try:
            content = canonical_json_bytes(value)
        except (CanonicalizationError, TypeError, ValueError) as error:
            _reject(
                RoutingQualificationValidationCode.WRONG_TYPE,
                f"{label} is outside the canonical JSON domain.",
            )
            raise AssertionError from error
        limit = max_bytes if max_bytes is not None else _size_limit(label)
        if len(content) > limit:
            _reject(
                RoutingQualificationValidationCode.OVERSIZED,
                f"{label} exceeds the bounded JSON size.",
            )
        _walk_bounded(value, depth=0, max_json_integer=max_json_integer)
        return _decode_json(
            content,
            label=label,
            require_canonical=False,
            max_json_integer=max_json_integer,
            max_bytes=max_bytes,
        )
    if type(value) is not bytes:
        _reject(
            RoutingQualificationValidationCode.WRONG_TYPE,
            f"{label} must be bytes or one JSON object.",
        )
    limit = max_bytes if max_bytes is not None else _size_limit(label)
    if len(value) > limit:
        _reject(
            RoutingQualificationValidationCode.OVERSIZED,
            f"{label} exceeds the bounded JSON size.",
        )
    return _decode_json(
        value,
        label=label,
        require_canonical=True,
        max_json_integer=max_json_integer,
        max_bytes=max_bytes,
    )


def _size_limit(label: str) -> int:
    return _MAX_EVIDENCE_BYTES if "evidence" in label else _MAX_CONTRACT_BYTES


def _decode_json(
    content: bytes,
    *,
    label: str,
    require_canonical: bool,
    max_json_integer: int = _MAX_JSON_INTEGER,
    max_bytes: int | None = None,
) -> dict[str, Any]:
    limit = max_bytes if max_bytes is not None else _size_limit(label)
    if len(content) > limit:
        _reject(
            RoutingQualificationValidationCode.OVERSIZED,
            f"{label} exceeds the bounded JSON size.",
        )
    try:
        text = content.decode("utf-8")
        payload = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_int=lambda raw: _bounded_integer(
                raw, max_json_integer=max_json_integer
            ),
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
    _walk_bounded(payload, depth=0, max_json_integer=max_json_integer)
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
    model_type: type[Any],
    payload: dict[str, Any],
    *,
    label: str,
    path_prefix: str | None = None,
    reject_producer_verdict_aliases: bool = True,
) -> Any:
    if reject_producer_verdict_aliases:
        _reject_producer_verdict_aliases(payload)
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
        _reject(
            code,
            f"{label} failed strict validation.",
            _prefix_path(path_prefix, path),
        )
    except (TypeError, ValueError):
        _reject(
            RoutingQualificationValidationCode.WRONG_TYPE,
            f"{label} failed strict validation.",
        )
    model = _revalidate_typed_model(model, model_type, label=label)
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
    details = error.errors()
    locations = [tuple(detail.get("loc", ())) for detail in details]

    def has_descendant(location: tuple[object, ...]) -> bool:
        return any(
            len(location) < len(other) and other[: len(location)] == location
            for other in locations
        )

    details = [
        detail
        for detail in details
        if not (
            detail.get("type") in {"too_short", "too_long"}
            and has_descendant(tuple(detail.get("loc", ())))
        )
    ] or details

    candidates = []
    for detail in details:
        location = tuple(detail.get("loc", ()))
        path = _location_path(location)
        error_type = str(detail.get("type", ""))
        field = str(location[-1]) if location else ""
        code = _classify_error_type(error_type, field)
        candidates.append(
            (
                _MODEL_ERROR_PRIORITY[_error_category(code)],
                _location_sort_key(location),
                error_type,
                code.value,
                code,
                path,
            )
        )
    _, _, _, _, code, path = min(candidates)
    return code, path


def _classify_error_type(
    error_type: str, field: str
) -> RoutingQualificationValidationCode:
    if error_type == "extra_forbidden":
        if _is_producer_verdict_alias(field):
            return RoutingQualificationValidationCode.PRODUCER_VERDICT_FORBIDDEN
        return RoutingQualificationValidationCode.EXTRA_FIELD
    if "missing" in error_type:
        return RoutingQualificationValidationCode.MISSING_FIELD
    if error_type in {
        "int_type",
        "bool_type",
        "string_type",
        "tuple_type",
        "list_type",
        "dict_type",
        "model_type",
    }:
        return RoutingQualificationValidationCode.WRONG_TYPE
    if "too_long" in error_type or "too_short" in error_type:
        return RoutingQualificationValidationCode.OVERSIZED
    if error_type == "string_pattern_mismatch":
        if "sha" in field or "digest" in field or "hash" in field:
            return RoutingQualificationValidationCode.INVALID_DIGEST
        return RoutingQualificationValidationCode.INVALID_VALUE
    if error_type in {
        "greater_than",
        "greater_than_equal",
        "less_than",
        "less_than_equal",
    }:
        return RoutingQualificationValidationCode.INVALID_BOUND
    if field == "captured_at" and error_type == "value_error":
        return RoutingQualificationValidationCode.INVALID_VALUE
    if error_type == "literal_error":
        if (
            "version" in field
            or "schema" in field
            or "canonical" in field
            or "hash" in field
        ):
            return RoutingQualificationValidationCode.WRONG_VERSION
        return RoutingQualificationValidationCode.SEMANTIC_INCONSISTENCY
    return RoutingQualificationValidationCode.SEMANTIC_INCONSISTENCY


def _error_category(code: RoutingQualificationValidationCode) -> str:
    if code is RoutingQualificationValidationCode.PRODUCER_VERDICT_FORBIDDEN:
        return "producer_verdict"
    if code is RoutingQualificationValidationCode.EXTRA_FIELD:
        return "extra"
    if code is RoutingQualificationValidationCode.MISSING_FIELD:
        return "missing"
    if code is RoutingQualificationValidationCode.WRONG_TYPE:
        return "wrong_type"
    if code is RoutingQualificationValidationCode.OVERSIZED:
        return "oversized"
    if code is RoutingQualificationValidationCode.INVALID_DIGEST:
        return "invalid_digest"
    if code is RoutingQualificationValidationCode.INVALID_BOUND:
        return "invalid_bound"
    if code is RoutingQualificationValidationCode.WRONG_VERSION:
        return "wrong_version"
    if code is RoutingQualificationValidationCode.INVALID_VALUE:
        return "invalid_value"
    return "semantic"


def _location_sort_key(location: tuple[object, ...]) -> tuple[str, ...]:
    return tuple(
        f"0:{item}" if isinstance(item, int) else f"1:{item}" for item in location
    )


def _location_path(location: tuple[object, ...]) -> str | None:
    path: str | None = None
    for item in location:
        path = (
            _index_path(path, item)
            if isinstance(item, int)
            else _join_path(path, str(item))
        )
    return path


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


def _bounded_integer(value: str, *, max_json_integer: int = _MAX_JSON_INTEGER) -> int:
    parsed = int(value)
    if abs(parsed) > max_json_integer:
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


def _walk_bounded(
    value: object,
    *,
    depth: int,
    max_json_integer: int = _MAX_JSON_INTEGER,
) -> None:
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
            _walk_bounded(
                child,
                depth=depth + 1,
                max_json_integer=max_json_integer,
            )
    elif type(value) is list:
        if len(value) > _MAX_JSON_ARRAY_ITEMS:
            _reject(
                RoutingQualificationValidationCode.OVERSIZED,
                "JSON array exceeds the B9 item bound.",
            )
        for child in value:
            _walk_bounded(
                child,
                depth=depth + 1,
                max_json_integer=max_json_integer,
            )
    elif type(value) is int and abs(value) > max_json_integer:
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
