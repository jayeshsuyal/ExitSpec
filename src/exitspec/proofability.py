"""Pure, strict PR5 proofability planning for frozen qualification inputs.

This module maps a frozen qualification question to the observations declared
by one package-owned capability descriptor.  It has no execution, evidence,
verdict, receipt, validity, provider, deployment, traffic, or authority path.
Its digest is deterministic planning identity and integrity only.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Mapping
from datetime import datetime
from enum import Enum
from typing import Annotated, Any, Final, Literal

from pydantic import ConfigDict, Field, ValidationError, model_validator
from pydantic_core import PydanticCustomError

from .canonical import CanonicalizationError, canonical_json_bytes
from .contracts import contract_digest
from .models import (
    ContractStatus,
    FrozenExitSpecModel,
    InferenceQualificationCriterionV1,
    MeasuredAttemptReliabilityRequirementV1,
    NativeTTFTP95RequirementV1,
    POCContract,
    SemanticFirstNonemptyTTFTP95RequirementV1,
)
from .producer_capability import (
    ProducerCapabilityDescriptorV1,
    producer_capability_digest,
    verify_producer_capability_descriptor,
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

PROOFABILITY_REPORT_SCHEMA_VERSION: Final = "exitspec.proofability-report.v1"
PROOFABILITY_REPORT_CANONICALIZATION_VERSION: Final = "rfc8785_jcs_v1"
PROOFABILITY_REPORT_HASH_VERSION: Final = "sha256_v1"
PROOFABILITY_REPORT_DIGEST_DOMAIN: Final = b"exitspec-proofability-report-v1\x00"
PROOFABILITY_PROTOCOL_ID: Final = "inference-performance-qualification"
PROOFABILITY_PROTOCOL_VERSION: Final = "1.0.0"

_MAX_REPORT_BYTES: Final = 1024 * 1024
_MAX_JSON_DEPTH: Final = 20
_MAX_JSON_NODES: Final = 16_384
_MAX_JSON_OBJECT_KEYS: Final = 48
_MAX_JSON_ARRAY_ITEMS: Final = 128
_MAX_JSON_STRING_LENGTH: Final = 512
_MAX_JSON_INTEGER: Final = 2_147_483_647
_DIGEST_PATTERN: Final = r"^sha256:[a-f0-9]{64}$"
_IDENTIFIER_PATTERN: Final = r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$"


class ProofabilityValidationCode(str, Enum):
    """Stable, content-safe failures for the PR5 planning boundary."""

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
    SUBJECT_BINDING_MISMATCH = "SUBJECT_BINDING_MISMATCH"
    SCOPE_BINDING_MISMATCH = "SCOPE_BINDING_MISMATCH"
    CONTEXT_BINDING_MISMATCH = "CONTEXT_BINDING_MISMATCH"
    CONTRACT_BINDING_MISMATCH = "CONTRACT_BINDING_MISMATCH"
    CAPABILITY_BINDING_MISMATCH = "CAPABILITY_BINDING_MISMATCH"


class ProofabilityRejected(ValueError):
    """One public proofability boundary failed closed without input disclosure."""

    def __init__(self, code: ProofabilityValidationCode, message: str) -> None:
        self.code = ProofabilityValidationCode(code)
        super().__init__(message)


class _JsonBoundaryError(ValueError):
    def __init__(self, code: ProofabilityValidationCode) -> None:
        self.code = code
        super().__init__(code.value)


def _reject(code: ProofabilityValidationCode, message: str) -> None:
    raise ProofabilityRejected(code, message)


class _StrictFrozenProofabilityModel(FrozenExitSpecModel):
    """Strict immutable base for every material PR5 artifact node."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        revalidate_instances="always",
    )


class CriterionProofabilityDisposition(str, Enum):
    PROVABLE = "PROVABLE"
    CLARIFICATION_REQUIRED = "CLARIFICATION_REQUIRED"
    NOT_PROVABLE = "NOT_PROVABLE"


class OverallProofabilityDisposition(str, Enum):
    PROVABLE = "PROVABLE"
    CLARIFICATION_REQUIRED = "CLARIFICATION_REQUIRED"
    NOT_PROVABLE = "NOT_PROVABLE"
    PARTIALLY_PROVABLE = "PARTIALLY_PROVABLE"


class ProofabilityReasonCode(str, Enum):
    ALL_REQUIRED_OBSERVATIONS_AVAILABLE = "ALL_REQUIRED_OBSERVATIONS_AVAILABLE"
    MISSING_OBSERVATION = "MISSING_OBSERVATION"
    INCOMPATIBLE_METRIC_DEFINITION = "INCOMPATIBLE_METRIC_DEFINITION"
    INCOMPATIBLE_SOURCE_FIELD = "INCOMPATIBLE_SOURCE_FIELD"
    INCOMPATIBLE_UNIT = "INCOMPATIBLE_UNIT"
    INCOMPATIBLE_POPULATION = "INCOMPATIBLE_POPULATION"
    INCOMPATIBLE_REDUCER = "INCOMPATIBLE_REDUCER"
    INCOMPATIBLE_PERCENTILE = "INCOMPATIBLE_PERCENTILE"
    INCOMPATIBLE_RELIABILITY_BINDING = "INCOMPATIBLE_RELIABILITY_BINDING"
    UNMAPPABLE_FROZEN_CRITERION_SCHEMA = "UNMAPPABLE_FROZEN_CRITERION_SCHEMA"


class ProofabilityRemediationCode(str, Enum):
    NO_REMEDIATION_REQUIRED = "NO_REMEDIATION_REQUIRED"
    DECLARE_REQUIRED_OBSERVATION = "DECLARE_REQUIRED_OBSERVATION"
    FREEZE_PROVIDER_NEUTRAL_CRITERION_SCHEMA = (
        "FREEZE_PROVIDER_NEUTRAL_CRITERION_SCHEMA"
    )


class NativeTTFTObservationReferenceV1(_StrictFrozenProofabilityModel):
    observation_kind: Literal["NATIVE_TTFT"]
    observation_id: Literal["native_ttft_sample"]
    metric_definition_id: Literal["vllm_first_choices_event_v0_26"]
    source_field: Literal["request.timing.ttft_ns"]
    unit: Literal["ns"]
    population: Literal["successful_measured_requests_with_observed_ttft"]
    reducer_id: Literal["nearest_rank_v1"]
    percentile: Literal["p95"]


class SemanticFirstNonemptyTTFTObservationReferenceV1(
    _StrictFrozenProofabilityModel
):
    observation_kind: Literal["SEMANTIC_FIRST_NONEMPTY_TTFT"]
    observation_id: Literal["semantic_first_nonempty_ttft_sample"]
    metric_definition_id: Literal["first_nonempty_choices_delta_content_v1"]
    source_field: Literal["response.choices[].delta.content"]
    unit: Literal["ns"]
    population: Literal["successful_measured_requests_with_observed_ttft"]
    reducer_id: Literal["nearest_rank_v1"]
    percentile: Literal["p95"]


class MeasuredAttemptReliabilityObservationReferenceV1(
    _StrictFrozenProofabilityModel
):
    observation_kind: Literal["MEASURED_ATTEMPT_RELIABILITY"]
    observation_id: Literal["native_measured_request_outcome"]
    source_field: Literal["request.outcome.status"]
    latency_population: Literal["successful_measured_requests_with_observed_ttft"]
    reliability_numerator: Literal["failed_or_anomalous_native_measured_requests"]
    reliability_denominator: Literal["all_measured_requests"]


ObservationReferenceV1 = Annotated[
    NativeTTFTObservationReferenceV1 | SemanticFirstNonemptyTTFTObservationReferenceV1 | MeasuredAttemptReliabilityObservationReferenceV1,
    Field(discriminator="observation_kind"),
]


class IncompatibleObservationV1(_StrictFrozenProofabilityModel):
    required_observation: ObservationReferenceV1
    available_observation: ObservationReferenceV1
    reason_code: Literal[
        ProofabilityReasonCode.INCOMPATIBLE_METRIC_DEFINITION,
        ProofabilityReasonCode.INCOMPATIBLE_SOURCE_FIELD,
        ProofabilityReasonCode.INCOMPATIBLE_UNIT,
        ProofabilityReasonCode.INCOMPATIBLE_POPULATION,
        ProofabilityReasonCode.INCOMPATIBLE_REDUCER,
        ProofabilityReasonCode.INCOMPATIBLE_PERCENTILE,
        ProofabilityReasonCode.INCOMPATIBLE_RELIABILITY_BINDING,
    ]


def _observation_sort_key(value: ObservationReferenceV1) -> tuple[str, str]:
    return (value.observation_kind, value.observation_id)


def _incompatible_observation_sort_key(
    value: IncompatibleObservationV1,
) -> tuple[str, str, str, str, str]:
    return (
        *_observation_sort_key(value.required_observation),
        *_observation_sort_key(value.available_observation),
        value.reason_code.value,
    )


def _incompatibility_reason_codes(
    required: ObservationReferenceV1,
    available: ObservationReferenceV1,
) -> frozenset[ProofabilityReasonCode]:
    """Return only closed reason codes supported by differing semantic leaves."""

    required_payload = required.model_dump(mode="json")
    available_payload = available.model_dump(mode="json")

    # A measured-attempt reliability reference and a latency reference do not
    # share latency-only leaves such as percentile or reducer.  Their kind
    # boundary is therefore the only meaningful closed mismatch in PR5; do
    # not invent a mismatch from a field that is absent on one of the types.
    if (
        required_payload["observation_kind"] == "MEASURED_ATTEMPT_RELIABILITY"
        or available_payload["observation_kind"] == "MEASURED_ATTEMPT_RELIABILITY"
    ):
        if required_payload != available_payload:
            return frozenset({ProofabilityReasonCode.INCOMPATIBLE_RELIABILITY_BINDING})
        return frozenset()

    reasons: set[ProofabilityReasonCode] = set()
    if required_payload.get("metric_definition_id") != available_payload.get(
        "metric_definition_id"
    ):
        reasons.add(ProofabilityReasonCode.INCOMPATIBLE_METRIC_DEFINITION)
    if required_payload.get("source_field") != available_payload.get("source_field"):
        reasons.add(ProofabilityReasonCode.INCOMPATIBLE_SOURCE_FIELD)
    if required_payload.get("unit") != available_payload.get("unit"):
        reasons.add(ProofabilityReasonCode.INCOMPATIBLE_UNIT)
    if required_payload.get("population") != available_payload.get("population"):
        reasons.add(ProofabilityReasonCode.INCOMPATIBLE_POPULATION)
    if required_payload.get("reducer_id") != available_payload.get("reducer_id"):
        reasons.add(ProofabilityReasonCode.INCOMPATIBLE_REDUCER)
    if required_payload.get("percentile") != available_payload.get("percentile"):
        reasons.add(ProofabilityReasonCode.INCOMPATIBLE_PERCENTILE)

    return frozenset(reasons)


def _semantic_inconsistency() -> None:
    raise PydanticCustomError(
        "proofability_semantic_inconsistency",
        "criterion proofability is internally inconsistent",
    )


class CriterionProofabilityV1(_StrictFrozenProofabilityModel):
    # POCContract has one bounded uppercase ID family plus three exact routing
    # literals. Keep this report language equal to that existing contract
    # union instead of admitting arbitrary lowercase identifiers.
    criterion_id: str = Field(
        pattern=(
            r"^(?:[A-Z][A-Z0-9-]{2,63}|"
            r"routing_qualification_v1|"
            r"routing_slo_attainment_v1|"
            r"routing_campaign_reduction_v1)$"
        ),
        max_length=64,
    )
    disposition: CriterionProofabilityDisposition
    required_observations: tuple[ObservationReferenceV1, ...] = Field(
        min_length=0, max_length=3
    )
    available_observations: tuple[ObservationReferenceV1, ...] = Field(
        min_length=0, max_length=3
    )
    missing_observations: tuple[ObservationReferenceV1, ...] = Field(
        min_length=0, max_length=3
    )
    incompatible_observations: tuple[IncompatibleObservationV1, ...] = Field(
        min_length=0, max_length=3
    )
    reason_codes: tuple[ProofabilityReasonCode, ...] = Field(
        min_length=1, max_length=3
    )
    remediation_codes: tuple[ProofabilityRemediationCode, ...] = Field(
        min_length=1, max_length=3
    )

    @model_validator(mode="after")
    def require_canonical_tuple_order(self) -> CriterionProofabilityV1:
        for observations in (
            self.required_observations,
            self.available_observations,
            self.missing_observations,
        ):
            keys = tuple(_observation_sort_key(item) for item in observations)
            if len(keys) != len(set(keys)) or keys != tuple(sorted(keys)):
                _semantic_inconsistency()
        incompatible_keys = tuple(
            _incompatible_observation_sort_key(item)
            for item in self.incompatible_observations
        )
        if (
            len(incompatible_keys) != len(set(incompatible_keys))
            or incompatible_keys != tuple(sorted(incompatible_keys))
        ):
            _semantic_inconsistency()
        if (
            len(self.reason_codes) != len(set(self.reason_codes))
            or self.reason_codes != tuple(sorted(self.reason_codes, key=str))
        ):
            _semantic_inconsistency()
        if (
            len(self.remediation_codes) != len(set(self.remediation_codes))
            or self.remediation_codes
            != tuple(sorted(self.remediation_codes, key=str))
        ):
            _semantic_inconsistency()

        if self.disposition is CriterionProofabilityDisposition.PROVABLE:
            if (
                not self.required_observations
                or self.required_observations != self.available_observations
                or self.missing_observations
                or self.incompatible_observations
                or self.reason_codes
                != (ProofabilityReasonCode.ALL_REQUIRED_OBSERVATIONS_AVAILABLE,)
                or self.remediation_codes
                != (ProofabilityRemediationCode.NO_REMEDIATION_REQUIRED,)
            ):
                _semantic_inconsistency()
            return self

        if self.disposition is CriterionProofabilityDisposition.CLARIFICATION_REQUIRED:
            if (
                self.required_observations
                or self.missing_observations
                or self.incompatible_observations
                or self.reason_codes
                != (ProofabilityReasonCode.UNMAPPABLE_FROZEN_CRITERION_SCHEMA,)
                or self.remediation_codes
                != (
                    ProofabilityRemediationCode.FREEZE_PROVIDER_NEUTRAL_CRITERION_SCHEMA,
                )
            ):
                _semantic_inconsistency()
            return self

        if not self.required_observations or not (
            self.missing_observations or self.incompatible_observations
        ):
            _semantic_inconsistency()
        if any(
            item not in self.required_observations
            for item in self.missing_observations
        ):
            _semantic_inconsistency()
        if any(item in self.available_observations for item in self.missing_observations):
            _semantic_inconsistency()

        incompatible_reasons: set[ProofabilityReasonCode] = set()
        pair_reasons: dict[
            tuple[str, str, str, str], set[ProofabilityReasonCode]
        ] = {}
        pair_observations: dict[
            tuple[str, str, str, str],
            tuple[ObservationReferenceV1, ObservationReferenceV1],
        ] = {}
        for incompatible in self.incompatible_observations:
            required_key = _observation_sort_key(incompatible.required_observation)
            available_key = _observation_sort_key(incompatible.available_observation)
            if (
                incompatible.required_observation not in self.required_observations
                or incompatible.available_observation not in self.available_observations
                or incompatible.required_observation == incompatible.available_observation
            ):
                _semantic_inconsistency()
            pair_key = (*required_key, *available_key)
            pair_reasons.setdefault(pair_key, set()).add(incompatible.reason_code)
            pair_observations.setdefault(
                pair_key,
                (
                    incompatible.required_observation,
                    incompatible.available_observation,
                ),
            )
            incompatible_reasons.add(incompatible.reason_code)

        for pair_key, declared_reasons in pair_reasons.items():
            required, available = pair_observations[pair_key]
            actual_reasons = _incompatibility_reason_codes(required, available)
            if not actual_reasons or declared_reasons != actual_reasons:
                _semantic_inconsistency()

        # Full model equality, not a reduced identity key, defines the exact
        # partition. Extra descriptor-available observations remain visible
        # but do not enter this required-observation accounting.
        for required in self.required_observations:
            states = (
                required in self.available_observations,
                required in self.missing_observations,
                any(
                    incompatible.required_observation == required
                    for incompatible in self.incompatible_observations
                ),
            )
            if sum(states) != 1:
                _semantic_inconsistency()

        expected_reasons = set(incompatible_reasons)
        if self.missing_observations:
            expected_reasons.add(ProofabilityReasonCode.MISSING_OBSERVATION)
        if tuple(sorted(expected_reasons, key=str)) != self.reason_codes:
            _semantic_inconsistency()
        if self.remediation_codes != (
            ProofabilityRemediationCode.DECLARE_REQUIRED_OBSERVATION,
        ):
            _semantic_inconsistency()
        if self.required_observations == self.available_observations:
            _semantic_inconsistency()
        return self


class _ProofabilityReportUnsignedV1(_StrictFrozenProofabilityModel):
    schema_version: Literal[PROOFABILITY_REPORT_SCHEMA_VERSION]
    canonicalization_version: Literal[PROOFABILITY_REPORT_CANONICALIZATION_VERSION]
    hash_version: Literal[PROOFABILITY_REPORT_HASH_VERSION]
    subject_digest: str = Field(pattern=_DIGEST_PATTERN)
    scope_digest: str = Field(pattern=_DIGEST_PATTERN)
    qualification_context_digest: str = Field(pattern=_DIGEST_PATTERN)
    protocol_id: Literal[PROOFABILITY_PROTOCOL_ID]
    protocol_version: Literal[PROOFABILITY_PROTOCOL_VERSION]
    contract_id: str = Field(pattern=_IDENTIFIER_PATTERN, max_length=128)
    contract_canonical_digest: str = Field(pattern=_DIGEST_PATTERN)
    capability_digest: str = Field(pattern=_DIGEST_PATTERN)
    profile_id: str = Field(pattern=_IDENTIFIER_PATTERN, max_length=128)
    profile_version: str = Field(pattern=r"^v?[0-9]+(?:\.[0-9]+){0,2}$", max_length=128)
    engine_id: str = Field(pattern=_IDENTIFIER_PATTERN, max_length=128)
    engine_version: str = Field(
        pattern=r"^v?[0-9]+(?:\.[0-9]+){0,2}$", max_length=128
    )
    adapter_id: str = Field(pattern=_IDENTIFIER_PATTERN, max_length=128)
    adapter_version: str = Field(
        pattern=r"^v?[0-9]+(?:\.[0-9]+){0,2}$", max_length=128
    )
    criterion_results: tuple[CriterionProofabilityV1, ...] = Field(
        min_length=1, max_length=64
    )
    overall_disposition: OverallProofabilityDisposition

    @model_validator(mode="after")
    def require_canonical_results_and_summary(self) -> _ProofabilityReportUnsignedV1:
        ids = tuple(result.criterion_id for result in self.criterion_results)
        if len(ids) != len(set(ids)):
            raise ValueError("Criterion result IDs must be unique.")
        dispositions = tuple(result.disposition for result in self.criterion_results)
        if all(item is CriterionProofabilityDisposition.PROVABLE for item in dispositions):
            expected = OverallProofabilityDisposition.PROVABLE
        elif CriterionProofabilityDisposition.PROVABLE in dispositions:
            expected = OverallProofabilityDisposition.PARTIALLY_PROVABLE
        elif CriterionProofabilityDisposition.NOT_PROVABLE in dispositions:
            expected = OverallProofabilityDisposition.NOT_PROVABLE
        else:
            expected = OverallProofabilityDisposition.CLARIFICATION_REQUIRED
        if self.overall_disposition is not expected:
            raise ValueError("Overall proofability disposition is inconsistent.")
        return self


def _report_digest_from_projection(projection: Mapping[str, Any]) -> str:
    try:
        content = canonical_json_bytes(projection)
    except (CanonicalizationError, RecursionError, TypeError, ValueError):
        _reject(
            ProofabilityValidationCode.INVALID_VALUE,
            "Proofability report projection is outside the canonical JSON domain.",
        )
    return "sha256:" + hashlib.sha256(
        PROOFABILITY_REPORT_DIGEST_DOMAIN + content
    ).hexdigest()


class ProofabilityReportV1(_ProofabilityReportUnsignedV1):
    """One immutable, context-bound planning report with no authority."""

    proofability_report_digest: str = Field(pattern=_DIGEST_PATTERN)

    @model_validator(mode="after")
    def require_matching_report_digest(self) -> ProofabilityReportV1:
        projection = self.model_dump(
            mode="json", exclude={"proofability_report_digest"}
        )
        expected = _report_digest_from_projection(projection)
        if not hmac.compare_digest(self.proofability_report_digest, expected):
            raise ValueError(
                "proofability_report_digest does not match the unsigned projection."
            )
        return self


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _JsonBoundaryError(ProofabilityValidationCode.DUPLICATE_FIELD)
        result[key] = value
    return result


def _bounded_integer(raw: str) -> int:
    value = int(raw)
    if abs(value) > _MAX_JSON_INTEGER:
        raise _JsonBoundaryError(ProofabilityValidationCode.OVERSIZED)
    return value


def _reject_float(_: str) -> None:
    raise _JsonBoundaryError(ProofabilityValidationCode.INVALID_VALUE)


def _reject_constant(_: str) -> None:
    raise _JsonBoundaryError(ProofabilityValidationCode.INVALID_VALUE)


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
        raise _JsonBoundaryError(ProofabilityValidationCode.OVERSIZED)
    if type(value) is str:
        if len(value) > _MAX_JSON_STRING_LENGTH:
            raise _JsonBoundaryError(ProofabilityValidationCode.OVERSIZED)
        return
    if value is None or type(value) is bool:
        return
    if type(value) is int:
        if abs(value) > _MAX_JSON_INTEGER:
            raise _JsonBoundaryError(ProofabilityValidationCode.OVERSIZED)
        return
    if type(value) is dict:
        identity = id(value)
        if identity in active_container_ids:
            raise _JsonBoundaryError(ProofabilityValidationCode.INVALID_VALUE)
        if len(value) > _MAX_JSON_OBJECT_KEYS:
            raise _JsonBoundaryError(ProofabilityValidationCode.OVERSIZED)
        active_container_ids.add(identity)
        try:
            for key, child in value.items():
                if type(key) is not str or len(key) > _MAX_JSON_STRING_LENGTH:
                    raise _JsonBoundaryError(ProofabilityValidationCode.OVERSIZED)
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
            raise _JsonBoundaryError(ProofabilityValidationCode.INVALID_VALUE)
        if len(value) > _MAX_JSON_ARRAY_ITEMS:
            raise _JsonBoundaryError(ProofabilityValidationCode.OVERSIZED)
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
    raise _JsonBoundaryError(ProofabilityValidationCode.WRONG_TYPE)


def _decode_object(
    content: bytes, *, require_canonical: bool, label: str
) -> dict[str, Any]:
    if len(content) > _MAX_REPORT_BYTES:
        _reject(ProofabilityValidationCode.OVERSIZED, f"{label} exceeds its byte limit.")
    try:
        payload = json.loads(
            content.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_int=_bounded_integer,
            parse_float=_reject_float,
            parse_constant=_reject_constant,
        )
    except UnicodeDecodeError:
        _reject(ProofabilityValidationCode.WRONG_TYPE, f"{label} is not UTF-8 JSON.")
    except _JsonBoundaryError as error:
        _reject(error.code, f"{label} failed its JSON boundary.")
    except (json.JSONDecodeError, RecursionError, TypeError, ValueError):
        _reject(
            ProofabilityValidationCode.WRONG_TYPE,
            f"{label} is not one valid JSON object.",
        )
    if type(payload) is not dict:
        _reject(ProofabilityValidationCode.WRONG_TYPE, f"{label} must be one JSON object.")
    try:
        _walk_bounded_json(payload, depth=0, node_count=[0])
        canonical = canonical_json_bytes(payload)
    except _JsonBoundaryError as error:
        _reject(error.code, f"{label} failed its JSON boundary.")
    except (CanonicalizationError, RecursionError, TypeError, ValueError):
        _reject(
            ProofabilityValidationCode.INVALID_VALUE,
            f"{label} is outside the canonical JSON domain.",
        )
    if require_canonical and canonical != content:
        _reject(
            ProofabilityValidationCode.NON_CANONICAL,
            f"{label} is not RFC 8785 canonical JSON.",
        )
    return payload


def _load_object(value: bytes | Mapping[str, Any], *, label: str) -> dict[str, Any]:
    if type(value) is bytes:
        return _decode_object(value, require_canonical=True, label=label)
    if type(value) is not dict:
        _reject(
            ProofabilityValidationCode.WRONG_TYPE,
            f"{label} must be bytes or one JSON object.",
        )
    try:
        _walk_bounded_json(value, depth=0, node_count=[0])
        content = canonical_json_bytes(value)
    except _JsonBoundaryError as error:
        _reject(error.code, f"{label} failed its JSON boundary.")
    except (CanonicalizationError, RecursionError, TypeError, ValueError):
        _reject(
            ProofabilityValidationCode.INVALID_VALUE,
            f"{label} is outside the canonical JSON domain.",
        )
    return _decode_object(content, require_canonical=False, label=label)


def _classify_validation_error(error: ValidationError) -> ProofabilityValidationCode:
    for detail in error.errors():
        location = tuple(detail.get("loc", ()))
        error_type = str(detail.get("type", ""))
        if error_type == "extra_forbidden":
            return ProofabilityValidationCode.EXTRA_FIELD
        if error_type == "missing":
            return ProofabilityValidationCode.MISSING_FIELD
        if location and location[0] == "schema_version":
            return ProofabilityValidationCode.WRONG_VERSION
        if location and str(location[-1]).endswith("digest"):
            return ProofabilityValidationCode.INVALID_DIGEST
        if error_type == "proofability_semantic_inconsistency":
            return ProofabilityValidationCode.SEMANTIC_INCONSISTENCY
        if error_type == "string_pattern_mismatch":
            return ProofabilityValidationCode.INVALID_VALUE
        if error_type in {"too_long", "string_too_long", "too_short"}:
            return ProofabilityValidationCode.OVERSIZED
        if error_type.endswith("_type") or error_type in {
            "model_type",
            "tuple_type",
            "list_type",
        }:
            return ProofabilityValidationCode.WRONG_TYPE
    return ProofabilityValidationCode.INVALID_VALUE


def _validate_report_payload(payload: dict[str, Any], *, label: str) -> ProofabilityReportV1:
    try:
        report = ProofabilityReportV1.model_validate_json(
            canonical_json_bytes(payload), strict=True
        )
    except ValidationError as error:
        _reject(_classify_validation_error(error), f"{label} failed strict validation.")
    except (CanonicalizationError, RecursionError, TypeError, ValueError):
        _reject(
            ProofabilityValidationCode.INVALID_VALUE,
            f"{label} failed strict validation.",
        )
    if type(report) is not ProofabilityReportV1:
        _reject(ProofabilityValidationCode.WRONG_TYPE, f"{label} has the wrong type.")
    return report


def _model_raw_state(
    value: FrozenExitSpecModel, *, label: str
) -> tuple[dict[str, object], set[str], dict[str, object] | None, dict[str, object] | None]:
    """Return one inspectable Pydantic node after closing every hidden slot."""

    try:
        raw_state = object.__getattribute__(value, "__dict__")
        fields_set = object.__getattribute__(value, "__pydantic_fields_set__")
        extra_state = object.__getattribute__(value, "__pydantic_extra__")
        private_state = object.__getattribute__(value, "__pydantic_private__")
    except (AttributeError, TypeError):
        _reject(
            ProofabilityValidationCode.WRONG_TYPE,
            f"{label} has no inspectable raw state.",
        )

    model_type = type(value)
    expected_fields = set(model_type.model_fields)
    if type(raw_state) is not dict:
        _reject(
            ProofabilityValidationCode.WRONG_TYPE,
            f"{label} raw state must be one object.",
        )
    if any(type(field_name) is not str for field_name in raw_state):
        _reject(
            ProofabilityValidationCode.WRONG_TYPE,
            f"{label} raw field names have the wrong type.",
        )
    if set(raw_state) - expected_fields:
        _reject(
            ProofabilityValidationCode.EXTRA_FIELD,
            f"{label} contains undocumented raw fields.",
        )
    if expected_fields - set(raw_state):
        _reject(
            ProofabilityValidationCode.MISSING_FIELD,
            f"{label} is missing a raw field.",
        )

    if type(fields_set) is not set or any(
        type(field_name) is not str for field_name in fields_set
    ):
        _reject(
            ProofabilityValidationCode.WRONG_TYPE,
            f"{label} has malformed field-set state.",
        )
    if not fields_set <= expected_fields:
        _reject(
            ProofabilityValidationCode.INVALID_VALUE,
            f"{label} has noncanonical field-set state.",
        )

    if extra_state is not None and (
        type(extra_state) is not dict or bool(extra_state)
    ):
        _reject(
            ProofabilityValidationCode.EXTRA_FIELD,
            f"{label} contains undocumented Pydantic extra state.",
        )
    if private_state is not None and (
        type(private_state) is not dict or bool(private_state)
    ):
        _reject(
            ProofabilityValidationCode.EXTRA_FIELD,
            f"{label} contains undocumented Pydantic private state.",
        )
    return raw_state, fields_set, extra_state, private_state


def _require_safe_enum_state(value: Enum, *, label: str) -> None:
    """Require one real package Enum member with no mutable hidden state."""

    try:
        raw_state = object.__getattribute__(value, "__dict__")
    except (AttributeError, TypeError):
        _reject(
            ProofabilityValidationCode.WRONG_TYPE,
            f"{label} has an uninspectable enum node.",
        )
    expected_keys = ("_value_", "_name_", "__objclass__", "_sort_order_")
    if type(raw_state) is not dict:
        _reject(
            ProofabilityValidationCode.WRONG_TYPE,
            f"{label} has malformed enum state.",
        )
    if set(raw_state) - set(expected_keys):
        _reject(
            ProofabilityValidationCode.EXTRA_FIELD,
            f"{label} contains undocumented enum state.",
        )
    if set(expected_keys) - set(raw_state):
        _reject(
            ProofabilityValidationCode.MISSING_FIELD,
            f"{label} is missing canonical enum state.",
        )
    if tuple(raw_state) != expected_keys:
        _reject(
            ProofabilityValidationCode.INVALID_VALUE,
            f"{label} has noncanonical enum state.",
        )
    enum_type = type(value)
    name = raw_state["_name_"]
    raw_value = raw_state["_value_"]
    if (
        type(name) is not str
        or type(raw_value) not in {str, bool, int, float}
        or raw_state["__objclass__"] is not enum_type
        or type(raw_state["_sort_order_"]) is not int
        or enum_type.__members__.get(name) is not value
    ):
        _reject(
            ProofabilityValidationCode.WRONG_TYPE,
            f"{label} has a type-confused enum node.",
        )


def _require_safe_raw_graph(
    value: object,
    *,
    label: str,
    active_node_ids: set[int] | None = None,
) -> None:
    """Reject cycles, mutable nodes, primitive subclasses, and hidden state."""

    if active_node_ids is None:
        active_node_ids = set()

    if isinstance(value, FrozenExitSpecModel):
        identity = id(value)
        if identity in active_node_ids:
            _reject(
                ProofabilityValidationCode.INVALID_VALUE,
                f"{label} contains a raw model cycle.",
            )
        raw_state, _, _, _ = _model_raw_state(value, label=label)
        active_node_ids.add(identity)
        try:
            for field_name in type(value).model_fields:
                _require_safe_raw_graph(
                    raw_state[field_name],
                    label=label,
                    active_node_ids=active_node_ids,
                )
        finally:
            active_node_ids.remove(identity)
        return

    if type(value) is tuple:
        identity = id(value)
        if identity in active_node_ids:
            _reject(
                ProofabilityValidationCode.INVALID_VALUE,
                f"{label} contains a raw tuple cycle.",
            )
        active_node_ids.add(identity)
        try:
            for child in value:
                _require_safe_raw_graph(
                    child,
                    label=label,
                    active_node_ids=active_node_ids,
                )
        finally:
            active_node_ids.remove(identity)
        return

    if value is None or type(value) in {str, bool, int, float, datetime}:
        return
    if isinstance(value, Enum):
        _require_safe_enum_state(value, label=label)
        return
    _reject(
        ProofabilityValidationCode.WRONG_TYPE,
        f"{label} contains an unsafe or type-confused raw node.",
    )


def _require_exact_roundtrip_graph(
    original: object,
    normalized: object,
    *,
    label: str,
    original_to_normalized: dict[int, int] | None = None,
    normalized_to_original: dict[int, int] | None = None,
) -> None:
    """Compare exact raw node types/state with an independent strict round-trip."""

    if original_to_normalized is None:
        original_to_normalized = {}
    if normalized_to_original is None:
        normalized_to_original = {}
    if type(original) is not type(normalized):
        _reject(
            ProofabilityValidationCode.WRONG_TYPE,
            f"{label} changed raw node type during strict normalization.",
        )

    if isinstance(original, FrozenExitSpecModel) or type(original) is tuple:
        original_id = id(original)
        normalized_id = id(normalized)
        if (
            original_id in original_to_normalized
            and original_to_normalized[original_id] != normalized_id
        ) or (
            normalized_id in normalized_to_original
            and normalized_to_original[normalized_id] != original_id
        ):
            _reject(
                ProofabilityValidationCode.INVALID_VALUE,
                f"{label} changed raw reference topology during strict normalization.",
            )
        original_to_normalized[original_id] = normalized_id
        normalized_to_original[normalized_id] = original_id

    if isinstance(original, FrozenExitSpecModel):
        original_state, original_fields_set, original_extra, original_private = (
            _model_raw_state(original, label=label)
        )
        normalized_state, normalized_fields_set, normalized_extra, normalized_private = (
            _model_raw_state(normalized, label=label)
        )
        if tuple(original_state) != tuple(normalized_state):
            _reject(
                ProofabilityValidationCode.INVALID_VALUE,
                f"{label} has noncanonical raw field order.",
            )
        if original_fields_set != normalized_fields_set:
            _reject(
                ProofabilityValidationCode.INVALID_VALUE,
                f"{label} has noncanonical field-set state.",
            )
        if type(original_extra) is not type(normalized_extra) or (
            original_extra != normalized_extra
        ):
            _reject(
                ProofabilityValidationCode.INVALID_VALUE,
                f"{label} has noncanonical Pydantic extra state.",
            )
        if type(original_private) is not type(normalized_private) or (
            original_private != normalized_private
        ):
            _reject(
                ProofabilityValidationCode.INVALID_VALUE,
                f"{label} has noncanonical Pydantic private state.",
            )
        for field_name in type(original).model_fields:
            _require_exact_roundtrip_graph(
                original_state[field_name],
                normalized_state[field_name],
                label=label,
                original_to_normalized=original_to_normalized,
                normalized_to_original=normalized_to_original,
            )
        return

    if type(original) is tuple:
        if len(original) != len(normalized):
            _reject(
                ProofabilityValidationCode.INVALID_VALUE,
                f"{label} changed tuple length during strict normalization.",
            )
        for original_child, normalized_child in zip(original, normalized, strict=True):
            _require_exact_roundtrip_graph(
                original_child,
                normalized_child,
                label=label,
                original_to_normalized=original_to_normalized,
                normalized_to_original=normalized_to_original,
            )
        return

    if isinstance(original, Enum):
        unchanged = original is normalized
    elif type(original) is datetime:
        unchanged = (
            original.isoformat() == normalized.isoformat()
            and original.fold == normalized.fold
        )
    elif type(original) is float:
        unchanged = original.hex() == normalized.hex()
    else:
        unchanged = original == normalized
    if not unchanged:
        _reject(
            ProofabilityValidationCode.INVALID_VALUE,
            f"{label} changed raw value during strict normalization.",
        )


def _require_exact_model_graph(
    value: object, model_type: type[FrozenExitSpecModel], *, label: str
) -> None:
    """Reject hidden state, subclasses, and unsafe raw nodes before dumping."""

    if type(value) is not model_type:
        _reject(
            ProofabilityValidationCode.WRONG_TYPE,
            f"{label} has the wrong typed model.",
        )
    _require_safe_raw_graph(value, label=label)


def _validated_typed_report(value: object) -> ProofabilityReportV1:
    _require_exact_model_graph(value, ProofabilityReportV1, label="Proofability report")
    try:
        payload = value.model_dump(mode="json", warnings="error")
    except (RecursionError, TypeError, ValueError):
        _reject(
            ProofabilityValidationCode.INVALID_VALUE,
            "Proofability report has an unsafe raw field value.",
        )
    normalized = _validate_report_payload(payload, label="Proofability report")
    _require_safe_raw_graph(normalized, label="Proofability report")
    _require_exact_roundtrip_graph(value, normalized, label="Proofability report")
    return normalized


def _validated_input_model(
    value: object,
    model_type: type[FrozenExitSpecModel],
    *,
    label: str,
    failure_code: ProofabilityValidationCode,
) -> FrozenExitSpecModel:
    if type(value) is not model_type:
        _reject(
            ProofabilityValidationCode.WRONG_TYPE,
            f"{label} has the wrong typed model.",
        )
    try:
        _require_exact_model_graph(value, model_type, label=label)
    except ProofabilityRejected:
        _reject(failure_code, f"{label} failed strict validation.")
    try:
        payload = value.model_dump(mode="json", warnings="error")
        validated = model_type.model_validate_json(canonical_json_bytes(payload), strict=True)
    except (CanonicalizationError, RecursionError, TypeError, ValidationError, ValueError):
        _reject(failure_code, f"{label} failed strict validation.")
    if type(validated) is not model_type:
        _reject(ProofabilityValidationCode.WRONG_TYPE, f"{label} has the wrong typed model.")
    try:
        _require_safe_raw_graph(validated, label=label)
        _require_exact_roundtrip_graph(value, validated, label=label)
    except ProofabilityRejected:
        _reject(failure_code, f"{label} failed strict validation.")
    return validated


def _validated_inputs(
    subject: object,
    scope: object,
    context: object,
    contract: object,
    descriptor: object,
) -> tuple[
    ServingSubjectManifestV1,
    QualificationScopeV1,
    QualificationContextV1,
    POCContract,
    ProducerCapabilityDescriptorV1,
]:
    typed_subject = _validated_input_model(
        subject,
        ServingSubjectManifestV1,
        label="Serving subject",
        failure_code=ProofabilityValidationCode.SUBJECT_BINDING_MISMATCH,
    )
    typed_scope = _validated_input_model(
        scope,
        QualificationScopeV1,
        label="Qualification scope",
        failure_code=ProofabilityValidationCode.SCOPE_BINDING_MISMATCH,
    )
    typed_context = _validated_input_model(
        context,
        QualificationContextV1,
        label="Qualification context",
        failure_code=ProofabilityValidationCode.CONTEXT_BINDING_MISMATCH,
    )
    typed_contract = _validated_input_model(
        contract,
        POCContract,
        label="Frozen contract",
        failure_code=ProofabilityValidationCode.CONTRACT_BINDING_MISMATCH,
    )
    typed_descriptor = _validated_input_model(
        descriptor,
        ProducerCapabilityDescriptorV1,
        label="Producer capability descriptor",
        failure_code=ProofabilityValidationCode.CAPABILITY_BINDING_MISMATCH,
    )
    if not verify_serving_subject_manifest(typed_subject):
        _reject(ProofabilityValidationCode.SUBJECT_BINDING_MISMATCH, "Serving subject is not valid.")
    if not verify_qualification_scope(typed_scope):
        _reject(ProofabilityValidationCode.SCOPE_BINDING_MISMATCH, "Qualification scope is not valid.")
    if not verify_qualification_context(typed_context):
        _reject(ProofabilityValidationCode.CONTEXT_BINDING_MISMATCH, "Qualification context is not valid.")
    if typed_context.protocol_id != PROOFABILITY_PROTOCOL_ID or typed_context.protocol_version != PROOFABILITY_PROTOCOL_VERSION:
        _reject(ProofabilityValidationCode.CONTEXT_BINDING_MISMATCH, "Qualification context protocol is not supported.")
    subject_identity = serving_subject_digest(typed_subject)
    scope_identity = qualification_scope_digest(typed_scope)
    if not hmac.compare_digest(typed_context.subject_digest, subject_identity) or not hmac.compare_digest(typed_context.scope_digest, scope_identity):
        _reject(ProofabilityValidationCode.CONTEXT_BINDING_MISMATCH, "Qualification context links are not exact.")
    if typed_contract.status is not ContractStatus.FROZEN or not typed_contract.canonical_hash:
        _reject(ProofabilityValidationCode.CONTRACT_BINDING_MISMATCH, "Frozen contract state is required.")
    if not hmac.compare_digest(typed_contract.canonical_hash, contract_digest(typed_contract)):
        _reject(ProofabilityValidationCode.CONTRACT_BINDING_MISMATCH, "Frozen contract digest is not valid.")
    expected_contract_digest = "sha256:" + typed_contract.canonical_hash
    if typed_scope.frozen_contract.contract_id != typed_contract.id or not hmac.compare_digest(typed_scope.frozen_contract.contract_canonical_digest, expected_contract_digest):
        _reject(ProofabilityValidationCode.CONTRACT_BINDING_MISMATCH, "Qualification scope contract link is not exact.")
    if not verify_producer_capability_descriptor(typed_descriptor):
        _reject(ProofabilityValidationCode.CAPABILITY_BINDING_MISMATCH, "Producer capability is not package-registered unchanged.")
    if (
        typed_subject.engine.engine_id != typed_descriptor.engine_adapter.engine_id
        or typed_subject.engine.engine_version
        != typed_descriptor.engine_adapter.engine_version
    ):
        _reject(
            ProofabilityValidationCode.CAPABILITY_BINDING_MISMATCH,
            "Producer capability does not apply to the exact subject engine.",
        )
    return (
        typed_subject,
        typed_scope,
        typed_context,
        typed_contract,
        typed_descriptor,
    )


def _native_observation_from_requirement(
    requirement: NativeTTFTP95RequirementV1,
) -> NativeTTFTObservationReferenceV1:
    return NativeTTFTObservationReferenceV1(
        observation_kind="NATIVE_TTFT",
        observation_id=requirement.observation_id,
        metric_definition_id=requirement.metric_definition_id,
        source_field=requirement.source_field,
        unit=requirement.unit,
        population=requirement.population,
        reducer_id=requirement.reducer_id,
        percentile=requirement.percentile,
    )


def _semantic_observation_from_requirement(
    requirement: SemanticFirstNonemptyTTFTP95RequirementV1,
) -> SemanticFirstNonemptyTTFTObservationReferenceV1:
    return SemanticFirstNonemptyTTFTObservationReferenceV1(
        observation_kind="SEMANTIC_FIRST_NONEMPTY_TTFT",
        observation_id=requirement.observation_id,
        metric_definition_id=requirement.metric_definition_id,
        source_field=requirement.source_field,
        unit=requirement.unit,
        population=requirement.population,
        reducer_id=requirement.reducer_id,
        percentile=requirement.percentile,
    )


def _reliability_observation_from_requirement(
    requirement: MeasuredAttemptReliabilityRequirementV1,
) -> MeasuredAttemptReliabilityObservationReferenceV1:
    return MeasuredAttemptReliabilityObservationReferenceV1(
        observation_kind="MEASURED_ATTEMPT_RELIABILITY",
        observation_id=requirement.observation_id,
        source_field=requirement.source_field,
        latency_population=requirement.latency_population,
        reliability_numerator=requirement.reliability_numerator,
        reliability_denominator=requirement.reliability_denominator,
    )


def _available_observations(
    descriptor: ProducerCapabilityDescriptorV1,
) -> tuple[ObservationReferenceV1, ...]:
    native = descriptor.available_observations.native_ttft
    reliability = descriptor.available_observations.measured_attempt_reliability
    observations: tuple[ObservationReferenceV1, ...] = (
        NativeTTFTObservationReferenceV1(
            observation_kind="NATIVE_TTFT",
            observation_id=native.observation_id,
            metric_definition_id=native.metric_definition_id,
            source_field=native.source_field,
            unit=native.unit,
            population=native.population,
            reducer_id=native.reducer_id,
            percentile=native.supported_percentile,
        ),
        MeasuredAttemptReliabilityObservationReferenceV1(
            observation_kind="MEASURED_ATTEMPT_RELIABILITY",
            observation_id=reliability.observation_id,
            source_field=reliability.source_field,
            latency_population=reliability.latency_population,
            reliability_numerator=reliability.reliability_numerator,
            reliability_denominator=reliability.reliability_denominator,
        ),
    )
    return tuple(sorted(observations, key=_observation_sort_key))


def _native_result(
    criterion: InferenceQualificationCriterionV1,
    available: tuple[ObservationReferenceV1, ...],
) -> CriterionProofabilityV1:
    latency = criterion.latency_requirement
    if type(latency) is not NativeTTFTP95RequirementV1:
        _reject(ProofabilityValidationCode.INVALID_VALUE, "Native requirement has the wrong type.")
    required = tuple(
        sorted(
            (
                _native_observation_from_requirement(latency),
                _reliability_observation_from_requirement(
                    criterion.reliability_requirement
                ),
            ),
            key=_observation_sort_key,
        )
    )
    return CriterionProofabilityV1(
        criterion_id=criterion.id,
        disposition=CriterionProofabilityDisposition.PROVABLE,
        required_observations=required,
        available_observations=available,
        missing_observations=(),
        incompatible_observations=(),
        reason_codes=(ProofabilityReasonCode.ALL_REQUIRED_OBSERVATIONS_AVAILABLE,),
        remediation_codes=(ProofabilityRemediationCode.NO_REMEDIATION_REQUIRED,),
    )


def _semantic_result(
    criterion: InferenceQualificationCriterionV1,
    available: tuple[ObservationReferenceV1, ...],
) -> CriterionProofabilityV1:
    latency = criterion.latency_requirement
    if type(latency) is not SemanticFirstNonemptyTTFTP95RequirementV1:
        _reject(ProofabilityValidationCode.INVALID_VALUE, "Semantic requirement has the wrong type.")
    semantic = _semantic_observation_from_requirement(latency)
    required = tuple(
        sorted(
            (semantic, _reliability_observation_from_requirement(criterion.reliability_requirement)),
            key=_observation_sort_key,
        )
    )
    return CriterionProofabilityV1(
        criterion_id=criterion.id,
        disposition=CriterionProofabilityDisposition.NOT_PROVABLE,
        required_observations=required,
        available_observations=available,
        missing_observations=(semantic,),
        incompatible_observations=(),
        reason_codes=(ProofabilityReasonCode.MISSING_OBSERVATION,),
        remediation_codes=(ProofabilityRemediationCode.DECLARE_REQUIRED_OBSERVATION,),
    )


def _legacy_result(
    criterion_id: str, available: tuple[ObservationReferenceV1, ...]
) -> CriterionProofabilityV1:
    return CriterionProofabilityV1(
        criterion_id=criterion_id,
        disposition=CriterionProofabilityDisposition.CLARIFICATION_REQUIRED,
        required_observations=(),
        available_observations=available,
        missing_observations=(),
        incompatible_observations=(),
        reason_codes=(ProofabilityReasonCode.UNMAPPABLE_FROZEN_CRITERION_SCHEMA,),
        remediation_codes=(
            ProofabilityRemediationCode.FREEZE_PROVIDER_NEUTRAL_CRITERION_SCHEMA,
        ),
    )


def _overall_disposition(
    results: tuple[CriterionProofabilityV1, ...],
) -> OverallProofabilityDisposition:
    dispositions = tuple(result.disposition for result in results)
    if all(item is CriterionProofabilityDisposition.PROVABLE for item in dispositions):
        return OverallProofabilityDisposition.PROVABLE
    if CriterionProofabilityDisposition.PROVABLE in dispositions:
        return OverallProofabilityDisposition.PARTIALLY_PROVABLE
    if CriterionProofabilityDisposition.NOT_PROVABLE in dispositions:
        return OverallProofabilityDisposition.NOT_PROVABLE
    return OverallProofabilityDisposition.CLARIFICATION_REQUIRED


def _create_report(
    *,
    subject: ServingSubjectManifestV1,
    scope: QualificationScopeV1,
    context: QualificationContextV1,
    contract: POCContract,
    descriptor: ProducerCapabilityDescriptorV1,
    results: tuple[CriterionProofabilityV1, ...],
) -> ProofabilityReportV1:
    unsigned_payload: dict[str, Any] = {
        "schema_version": PROOFABILITY_REPORT_SCHEMA_VERSION,
        "canonicalization_version": PROOFABILITY_REPORT_CANONICALIZATION_VERSION,
        "hash_version": PROOFABILITY_REPORT_HASH_VERSION,
        "subject_digest": serving_subject_digest(subject),
        "scope_digest": qualification_scope_digest(scope),
        "qualification_context_digest": qualification_context_digest(context),
        "protocol_id": PROOFABILITY_PROTOCOL_ID,
        "protocol_version": PROOFABILITY_PROTOCOL_VERSION,
        "contract_id": contract.id,
        "contract_canonical_digest": "sha256:" + contract.canonical_hash,
        "capability_digest": producer_capability_digest(descriptor),
        "profile_id": descriptor.profile.profile_id,
        "profile_version": descriptor.profile.profile_version,
        "engine_id": descriptor.engine_adapter.engine_id,
        "engine_version": descriptor.engine_adapter.engine_version,
        "adapter_id": descriptor.engine_adapter.adapter_id,
        "adapter_version": descriptor.engine_adapter.adapter_version,
        "criterion_results": [result.model_dump(mode="json") for result in results],
        "overall_disposition": _overall_disposition(results),
    }
    try:
        unsigned = _ProofabilityReportUnsignedV1.model_validate_json(
            canonical_json_bytes(unsigned_payload), strict=True
        )
        projection = unsigned.model_dump(mode="json")
        report = ProofabilityReportV1.model_validate_json(
            canonical_json_bytes(
                {
                    **projection,
                    "proofability_report_digest": _report_digest_from_projection(
                        projection
                    ),
                }
            ),
            strict=True,
        )
    except (CanonicalizationError, RecursionError, TypeError, ValidationError, ValueError):
        _reject(ProofabilityValidationCode.INVALID_VALUE, "Proofability report could not be constructed.")
    if type(report) is not ProofabilityReportV1:
        _reject(ProofabilityValidationCode.WRONG_TYPE, "Proofability report has the wrong type.")
    return report


def evaluate_proofability(
    subject: ServingSubjectManifestV1,
    scope: QualificationScopeV1,
    context: QualificationContextV1,
    contract: POCContract,
    descriptor: ProducerCapabilityDescriptorV1,
) -> ProofabilityReportV1:
    """Map an exact frozen question to declared observations without execution."""

    subject, scope, context, contract, descriptor = _validated_inputs(
        subject, scope, context, contract, descriptor
    )
    available = _available_observations(descriptor)
    results: list[CriterionProofabilityV1] = []
    for criterion in contract.criteria:
        if type(criterion) is InferenceQualificationCriterionV1:
            if type(criterion.latency_requirement) is NativeTTFTP95RequirementV1:
                results.append(_native_result(criterion, available))
            elif type(criterion.latency_requirement) is (
                SemanticFirstNonemptyTTFTP95RequirementV1
            ):
                results.append(_semantic_result(criterion, available))
            else:
                _reject(
                    ProofabilityValidationCode.INVALID_VALUE,
                    "Qualification criterion latency requirement is unsupported.",
                )
        else:
            # Legacy union arms are deliberately opaque: do not inspect or
            # expose their provider-specific fields.
            results.append(_legacy_result(criterion.id, available))
    report = _create_report(
        subject=subject,
        scope=scope,
        context=context,
        contract=contract,
        descriptor=descriptor,
        results=tuple(results),
    )
    return parse_proofability_report(serialize_proofability_report(report))


def parse_proofability_report(value: bytes | Mapping[str, Any]) -> ProofabilityReportV1:
    """Parse one canonical, self-consistent report; parsing is not evaluation."""

    return _validate_report_payload(_load_object(value, label="Proofability report"), label="Proofability report")


def canonical_proofability_report_projection(
    value: ProofabilityReportV1,
) -> dict[str, Any]:
    """Return the one validated unsigned projection for report digesting."""

    return _validated_typed_report(value).model_dump(
        mode="json", exclude={"proofability_report_digest"}
    )


def proofability_report_digest(value: ProofabilityReportV1) -> str:
    """Return the domain-separated planning identity for one report."""

    return _report_digest_from_projection(canonical_proofability_report_projection(value))


def serialize_proofability_report(value: ProofabilityReportV1) -> bytes:
    """Serialize one strict report as byte-exact RFC 8785 JCS."""

    validated = _validated_typed_report(value)
    try:
        content = canonical_json_bytes(validated.model_dump(mode="json"))
    except (CanonicalizationError, RecursionError, TypeError, ValueError):
        _reject(
            ProofabilityValidationCode.INVALID_VALUE,
            "Proofability report is outside the canonical JSON domain.",
        )
    parsed = parse_proofability_report(content)
    if parsed != validated:
        _reject(
            ProofabilityValidationCode.SEMANTIC_INCONSISTENCY,
            "Proofability report changed during serialization.",
        )
    return content


def verify_proofability_report(
    report: object,
    subject: ServingSubjectManifestV1,
    scope: QualificationScopeV1,
    context: QualificationContextV1,
    contract: POCContract,
    descriptor: ProducerCapabilityDescriptorV1,
) -> bool:
    """Re-evaluate exact original inputs; report-only self-consistency is insufficient."""

    try:
        validated = _validated_typed_report(report)
        expected = evaluate_proofability(subject, scope, context, contract, descriptor)
        return hmac.compare_digest(
            serialize_proofability_report(validated),
            serialize_proofability_report(expected),
        )
    except ProofabilityRejected:
        return False


__all__ = [
    "PROOFABILITY_PROTOCOL_ID",
    "PROOFABILITY_PROTOCOL_VERSION",
    "PROOFABILITY_REPORT_CANONICALIZATION_VERSION",
    "PROOFABILITY_REPORT_DIGEST_DOMAIN",
    "PROOFABILITY_REPORT_HASH_VERSION",
    "PROOFABILITY_REPORT_SCHEMA_VERSION",
    "CriterionProofabilityDisposition",
    "CriterionProofabilityV1",
    "IncompatibleObservationV1",
    "MeasuredAttemptReliabilityObservationReferenceV1",
    "NativeTTFTObservationReferenceV1",
    "OverallProofabilityDisposition",
    "ProofabilityRejected",
    "ProofabilityRemediationCode",
    "ProofabilityReportV1",
    "ProofabilityValidationCode",
    "SemanticFirstNonemptyTTFTObservationReferenceV1",
    "canonical_proofability_report_projection",
    "evaluate_proofability",
    "parse_proofability_report",
    "proofability_report_digest",
    "serialize_proofability_report",
    "verify_proofability_report",
]
