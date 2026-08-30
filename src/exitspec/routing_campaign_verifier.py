"""B11 routing campaign evidence admission and independent reduction.

This module is the first executable layer for the B9/B10 routing vocabulary.
It admits bounded provider-neutral run evidence, recalculates every assignment
fact and Wilson statistic, and combines only independently evaluated required
runs under the frozen B11 reduction criterion.  It emits no receipt, Evidence
Pack, UI projection, or release decision artifact.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import re
from typing import Any, Literal, Mapping, Sequence

from pydantic import Field, field_validator, model_validator

from .canonical import canonical_json_bytes
from .confirmations import ContractConfirmation, require_affirmative_confirmation
from .contracts import contract_digest, verify_contract_digest
from .models import (
    FrozenExitSpecModel,
    POCContract,
    RoutingCampaignReductionCriterionV1,
    RoutingQualificationCriterionV1,
    RoutingSLOAttainmentCriterionV1,
    SHA256_PATTERN,
)
from .routing_qualification import (
    RoutingQualificationRejected,
    RoutingQualificationValidationCode,
    _load_object,
    _reject_producer_verdict_aliases,
    _revalidate_typed_model,
    _validate_model,
)
from .statistics import CALCULATION_VERSION, wilson_lower_bound


ROUTING_CAMPAIGN_REDUCTION_PROTOCOL_ID = "routing_campaign_reduction_v1"
ROUTING_CAMPAIGN_REDUCTION_SCHEMA_VERSION = "exitspec.routing-campaign-reduction.v1"
ROUTING_CAMPAIGN_REDUCTION_CANONICALIZATION_VERSION = "rfc8785_jcs_v1"
ROUTING_CAMPAIGN_REDUCTION_HASH_VERSION = "sha256_v1"
ROUTING_CAMPAIGN_EVIDENCE_PROTOCOL_ID = "routing_campaign_verification_v1"
ROUTING_CAMPAIGN_EVIDENCE_SCHEMA_VERSION = "exitspec.routing-campaign-evidence.v1"
ROUTING_CAMPAIGN_EVIDENCE_BUNDLE_SCHEMA_VERSION = (
    "exitspec.routing-campaign-evidence-bundle.v1"
)
ROUTING_CAMPAIGN_EVIDENCE_CANONICALIZATION_VERSION = "rfc8785_jcs_v1"
ROUTING_CAMPAIGN_EVIDENCE_HASH_VERSION = "sha256_v1"
ROUTING_CAMPAIGN_REDUCER_ID = "routing_campaign_deterministic_reducer_v1"
ROUTING_CAMPAIGN_REDUCER_VERSION = "1.0.0"
ROUTING_CAMPAIGN_RESULT_SCHEMA_VERSION = "exitspec.routing-campaign-reduction-result.v1"

_MAX_CAMPAIGN_EVIDENCE_BYTES = 2 * 1024 * 1024
_MAX_CAMPAIGN_CONTRACT_BYTES = 256 * 1024
_MAX_CAMPAIGN_JSON_INTEGER = 60_000_000_001
_MAX_CAMPAIGN_RUNS = 100
_MAX_CAMPAIGN_ASSIGNMENTS = 200_000
_MAX_CAMPAIGN_RESETS = 1_000
_ID_PATTERN = r"^[a-z][a-z0-9._-]{2,127}$"
_REQUEST_ID_PATTERN = r"^request-[0-9]{6}$"
_TIMESTAMP_PATTERN = (
    r"^20[0-9]{2}-[0-9]{2}-[0-9]{2}T[0-9]{2}:"
    r"[0-9]{2}:[0-9]{2}Z$"
)
_PROPORTION_PATTERN = r"^(?:0|1|0\.[0-9]*[1-9])$"
_MAX_TIMESTAMP_LENGTH = 20
_TERMINAL_OUTCOMES = (
    "SUCCESS",
    "EXTERNAL_ERROR",
    "TIMEOUT",
    "MISSING",
    "INVALID",
    "INTERNAL",
    "CANCELLED",
)
_EXTERNAL_NOT_ATTAINED = {"EXTERNAL_ERROR", "TIMEOUT"}
_NOT_PROVEN_OUTCOMES = {"MISSING", "INVALID", "INTERNAL", "CANCELLED"}
_RESULT_ISSUE_ORDER = (
    "STALE_TELEMETRY",
    "CACHE_RESET_EVIDENCE_INCOMPLETE",
    "CACHE_RESET_NOT_CONFIRMED",
    "MISSING_ASSIGNMENT",
    "NOT_PROVEN_ASSIGNMENT",
    "INSUFFICIENT_SAMPLE_COUNT",
    "WILSON_CONFIDENCE_INSUFFICIENT",
)
RoutingCampaignResultIssue = Literal[
    "STALE_TELEMETRY",
    "CACHE_RESET_EVIDENCE_INCOMPLETE",
    "CACHE_RESET_NOT_CONFIRMED",
    "MISSING_ASSIGNMENT",
    "NOT_PROVEN_ASSIGNMENT",
    "INSUFFICIENT_SAMPLE_COUNT",
    "WILSON_CONFIDENCE_INSUFFICIENT",
]


RoutingCampaignIngestionRejected = RoutingQualificationRejected
RoutingCampaignValidationCode = RoutingQualificationValidationCode


def _reject(
    code: RoutingQualificationValidationCode,
    message: str,
    path: str | None = None,
) -> None:
    raise RoutingQualificationRejected(code, message, path=path)


def _timestamp(value: str) -> str:
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as error:
        raise ValueError(
            "Timestamp must be a real UTC whole-second timestamp."
        ) from error
    return value


def _timestamp_seconds(value: str) -> int:
    parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    return int(parsed.replace(tzinfo=timezone.utc).timestamp())


def _request_index(request_id: str) -> int:
    if re.fullmatch(_REQUEST_ID_PATTERN, request_id) is None:
        raise ValueError("request_id must use the canonical request index format.")
    return int(request_id[8:])


def _receipt_coordinate(
    receipt: "RoutingCampaignRouteDecisionReceiptV1",
) -> tuple[int, int, int]:
    return (
        receipt.trial_index,
        _request_index(receipt.request_id),
        0 if receipt.policy_role == "candidate" else 1,
    )


def _reset_coordinate(
    reset: "RoutingCampaignCacheResetEvidenceV1",
) -> tuple[int]:
    return (reset.trial_index,)


class RoutingCampaignProducerV1(FrozenExitSpecModel):
    """Bounded producer provenance; it has no acceptance authority."""

    schema_version: str = Field(pattern=r"^exitspec\.routing-campaign-producer\.v1$")
    producer_id: str = Field(pattern=_ID_PATTERN, max_length=128)
    producer_version: str = Field(min_length=1, max_length=128)
    source_digest: str = Field(pattern=SHA256_PATTERN)


class RoutingCampaignServingEvidenceV1(FrozenExitSpecModel):
    """Serving/model/environment identity supplied by the evidence producer."""

    schema_version: str = Field(
        pattern=r"^exitspec\.routing-campaign-serving-evidence\.v1$"
    )
    engine: str = Field(min_length=1, max_length=128)
    engine_version: str = Field(min_length=1, max_length=128)
    model: str = Field(min_length=1, max_length=256)
    model_revision: str = Field(min_length=1, max_length=128)
    tokenizer: str = Field(min_length=1, max_length=256)
    tokenizer_revision: str = Field(min_length=1, max_length=128)
    quantization: str = Field(min_length=1, max_length=128)
    tensor_parallel_size: int = Field(ge=1, le=1_000)
    environment_id: str = Field(pattern=_ID_PATTERN, max_length=128)
    environment_sha256: str = Field(pattern=SHA256_PATTERN)
    target_engine_version: str = Field(min_length=1, max_length=128)
    target_model_revision: str = Field(min_length=1, max_length=128)
    target_tokenizer_revision: str = Field(min_length=1, max_length=128)
    gpu_model: str = Field(min_length=1, max_length=128)
    gpu_count: int = Field(ge=1, le=1_000)
    cuda_version: str = Field(min_length=1, max_length=64)
    driver_version: str = Field(min_length=1, max_length=64)
    execution_environment_id: str = Field(pattern=_ID_PATTERN, max_length=128)

    @model_validator(mode="after")
    def require_environment_identity_consistency(
        self,
    ) -> "RoutingCampaignServingEvidenceV1":
        if (
            self.target_engine_version != self.engine_version
            or self.target_model_revision != self.model_revision
            or self.target_tokenizer_revision != self.tokenizer_revision
            or self.execution_environment_id != self.environment_id
        ):
            raise ValueError(
                "Serving and normalized environment identities must agree."
            )
        return self


class RoutingCampaignTelemetryCapsuleV1(FrozenExitSpecModel):
    """Digest-bound telemetry identity and freshness provenance."""

    schema_version: str = Field(
        pattern=r"^exitspec\.routing-campaign-telemetry-capsule\.v1$"
    )
    telemetry_capsule_id: str = Field(pattern=_ID_PATTERN, max_length=128)
    telemetry_capsule_sha256: str = Field(pattern=SHA256_PATTERN)
    run_id: str = Field(pattern=_ID_PATTERN, max_length=128)
    captured_at: str = Field(
        pattern=_TIMESTAMP_PATTERN, max_length=_MAX_TIMESTAMP_LENGTH
    )
    producer_id: str = Field(pattern=_ID_PATTERN, max_length=128)
    producer_version: str = Field(min_length=1, max_length=128)
    source_digest: str = Field(pattern=SHA256_PATTERN)
    environment_id: str = Field(pattern=_ID_PATTERN, max_length=128)

    _validate_captured_at = field_validator("captured_at")(_timestamp)


class RoutingCampaignCacheResetEvidenceV1(FrozenExitSpecModel):
    """One digest-bound reset observation for one required trial."""

    schema_version: str = Field(
        pattern=r"^exitspec\.routing-campaign-cache-reset-evidence\.v1$"
    )
    reset_id: str = Field(pattern=_ID_PATTERN, max_length=128)
    reset_sha256: str = Field(pattern=SHA256_PATTERN)
    run_id: str = Field(pattern=_ID_PATTERN, max_length=128)
    repetition_index: int = Field(ge=1, le=100)
    trial_index: int = Field(ge=0, le=999)
    status: str = Field(pattern=r"^(?:RESET_CONFIRMED|RESET_FAILED)$")
    reset_scope: str = Field(pattern=r"^ROUTER_AND_SERVING_ENGINE_STATE$")
    reset_at: str = Field(pattern=_TIMESTAMP_PATTERN, max_length=_MAX_TIMESTAMP_LENGTH)
    producer_id: str = Field(pattern=_ID_PATTERN, max_length=128)
    producer_version: str = Field(min_length=1, max_length=128)
    source_digest: str = Field(pattern=SHA256_PATTERN)

    _validate_reset_at = field_validator("reset_at")(_timestamp)


class RoutingCampaignRouteDecisionReceiptV1(FrozenExitSpecModel):
    """One canonical route assignment fact, never a producer verdict."""

    schema_version: str = Field(
        pattern=r"^exitspec\.routing-campaign-route-decision-receipt\.v1$"
    )
    route_decision_receipt_id: str = Field(pattern=_ID_PATTERN, max_length=128)
    route_decision_receipt_sha256: str = Field(pattern=SHA256_PATTERN)
    campaign_contract_sha256: str = Field(pattern=SHA256_PATTERN)
    run_id: str = Field(pattern=_ID_PATTERN, max_length=128)
    repetition_index: int = Field(ge=1, le=100)
    request_id: str = Field(pattern=_REQUEST_ID_PATTERN)
    trial_index: int = Field(ge=0, le=999)
    policy_role: str = Field(pattern=r"^(?:candidate|baseline)$")
    policy_id: str = Field(pattern=_ID_PATTERN, max_length=128)
    policy_sha256: str = Field(pattern=SHA256_PATTERN)
    routing_configuration_id: str = Field(pattern=_ID_PATTERN, max_length=128)
    routing_configuration_sha256: str = Field(pattern=SHA256_PATTERN)
    producer_id: str = Field(pattern=_ID_PATTERN, max_length=128)
    producer_version: str = Field(min_length=1, max_length=128)
    captured_at: str = Field(
        pattern=_TIMESTAMP_PATTERN, max_length=_MAX_TIMESTAMP_LENGTH
    )
    source_digest: str = Field(pattern=SHA256_PATTERN)
    terminal_outcome: str = Field(
        pattern=r"^(?:" + "|".join(_TERMINAL_OUTCOMES) + r")$"
    )
    latency_ns: int | None = Field(default=None, ge=0, le=60_000_000_000)

    _validate_receipt_captured_at = field_validator("captured_at")(_timestamp)

    @model_validator(mode="after")
    def require_b10_latency_shape(
        self,
    ) -> "RoutingCampaignRouteDecisionReceiptV1":
        if self.terminal_outcome == "SUCCESS" and self.latency_ns is None:
            raise ValueError("SUCCESS receipts require the B10 latency fact.")
        if (
            self.terminal_outcome in _NOT_PROVEN_OUTCOMES
            and self.latency_ns is not None
        ):
            raise ValueError("NOT_PROVEN receipt outcomes cannot carry a latency fact.")
        return self


class RoutingCampaignRunEvidenceV1(FrozenExitSpecModel):
    """One independently captured run of the frozen B9/B10 campaign."""

    schema_version: Literal["exitspec.routing-campaign-evidence.v1"]
    protocol_id: Literal["routing_campaign_verification_v1"]
    evidence_class: str = Field(
        pattern=r"^(?:SYNTHETIC_FIXTURE|EXTERNAL_SEALED_EVIDENCE)$"
    )
    contract_sha256: str = Field(pattern=SHA256_PATTERN)
    run_id: str = Field(pattern=_ID_PATTERN, max_length=128)
    repetition_index: int = Field(ge=1, le=100)
    candidate_policy_id: str = Field(pattern=_ID_PATTERN, max_length=128)
    candidate_policy_sha256: str = Field(pattern=SHA256_PATTERN)
    baseline_policy_id: str = Field(pattern=_ID_PATTERN, max_length=128)
    baseline_policy_sha256: str = Field(pattern=SHA256_PATTERN)
    routing_configuration_id: str = Field(pattern=_ID_PATTERN, max_length=128)
    routing_configuration_sha256: str = Field(pattern=SHA256_PATTERN)
    request_trace_id: str = Field(pattern=_ID_PATTERN, max_length=128)
    request_trace_sha256: str = Field(pattern=SHA256_PATTERN)
    failure_injection_id: str = Field(pattern=_ID_PATTERN, max_length=128)
    failure_injection_sha256: str = Field(pattern=SHA256_PATTERN)
    serving: RoutingCampaignServingEvidenceV1
    telemetry: RoutingCampaignTelemetryCapsuleV1
    cache_resets: tuple[RoutingCampaignCacheResetEvidenceV1, ...] = Field(
        min_length=0, max_length=_MAX_CAMPAIGN_RESETS
    )
    producer: RoutingCampaignProducerV1
    observed_at: str = Field(
        pattern=_TIMESTAMP_PATTERN, max_length=_MAX_TIMESTAMP_LENGTH
    )
    assignments: tuple[RoutingCampaignRouteDecisionReceiptV1, ...] = Field(
        min_length=0, max_length=_MAX_CAMPAIGN_ASSIGNMENTS
    )

    _validate_observed_at = field_validator("observed_at")(_timestamp)

    @model_validator(mode="after")
    def require_run_local_canonical_records(self) -> "RoutingCampaignRunEvidenceV1":
        if self.candidate_policy_id == self.baseline_policy_id:
            raise ValueError("Candidate and baseline policy IDs must be distinct.")
        if self.candidate_policy_sha256 == self.baseline_policy_sha256:
            raise ValueError("Candidate and baseline policy digests must be distinct.")
        if self.telemetry.run_id != self.run_id:
            raise ValueError("Telemetry must bind the enclosing run ID.")
        if self.telemetry.producer_id != self.producer.producer_id:
            raise ValueError("Telemetry producer identity must match the run producer.")
        if self.telemetry.producer_version != self.producer.producer_version:
            raise ValueError("Telemetry producer version must match the run producer.")
        if self.telemetry.source_digest != self.producer.source_digest:
            raise ValueError("Telemetry source digest must match the run producer.")
        if _timestamp_seconds(self.telemetry.captured_at) > _timestamp_seconds(
            self.observed_at
        ):
            raise ValueError("Telemetry cannot be captured after evidence observation.")

        reset_ids = tuple(reset.reset_id for reset in self.cache_resets)
        reset_digests = tuple(reset.reset_sha256 for reset in self.cache_resets)
        if len(set(reset_ids)) != len(reset_ids) or len(set(reset_digests)) != len(
            reset_digests
        ):
            raise ValueError("Cache reset identities must be unique within a run.")
        if tuple(map(_reset_coordinate, self.cache_resets)) != tuple(
            sorted(map(_reset_coordinate, self.cache_resets))
        ):
            raise ValueError("Cache reset records must use canonical trial order.")
        for reset in self.cache_resets:
            if (
                reset.run_id != self.run_id
                or reset.repetition_index != self.repetition_index
                or reset.producer_id != self.producer.producer_id
                or reset.producer_version != self.producer.producer_version
                or reset.source_digest != self.producer.source_digest
            ):
                raise ValueError("Cache reset provenance must bind the enclosing run.")

        receipt_ids = tuple(
            receipt.route_decision_receipt_id for receipt in self.assignments
        )
        receipt_digests = tuple(
            receipt.route_decision_receipt_sha256 for receipt in self.assignments
        )
        coordinates = tuple(map(_receipt_coordinate, self.assignments))
        if len(set(receipt_ids)) != len(receipt_ids) or len(
            set(receipt_digests)
        ) != len(receipt_digests):
            raise ValueError("Route receipt identities must be unique within a run.")
        if coordinates != tuple(sorted(coordinates)):
            raise ValueError("Route receipts must use canonical assignment order.")
        seen_coordinates: set[tuple[int, int, int]] = set()
        for receipt in self.assignments:
            coordinate = _receipt_coordinate(receipt)
            if coordinate in seen_coordinates:
                raise ValueError("Route receipt assignment coordinates must be unique.")
            seen_coordinates.add(coordinate)
            if (
                receipt.campaign_contract_sha256 != self.contract_sha256
                or receipt.run_id != self.run_id
                or receipt.repetition_index != self.repetition_index
                or receipt.routing_configuration_id != self.routing_configuration_id
                or receipt.routing_configuration_sha256
                != self.routing_configuration_sha256
                or receipt.producer_id != self.producer.producer_id
                or receipt.producer_version != self.producer.producer_version
                or receipt.source_digest != self.producer.source_digest
            ):
                raise ValueError("Route receipt bindings must bind the enclosing run.")
            expected_policy_id = (
                self.candidate_policy_id
                if receipt.policy_role == "candidate"
                else self.baseline_policy_id
            )
            expected_policy_sha256 = (
                self.candidate_policy_sha256
                if receipt.policy_role == "candidate"
                else self.baseline_policy_sha256
            )
            if (
                receipt.policy_id != expected_policy_id
                or receipt.policy_sha256 != expected_policy_sha256
            ):
                raise ValueError("Route receipt policy binding is inconsistent.")
        return self


class RoutingCampaignEvidenceBundleV1(FrozenExitSpecModel):
    """Portable bounded collection of independently captured run evidence."""

    schema_version: Literal["exitspec.routing-campaign-evidence-bundle.v1"]
    protocol_id: Literal["routing_campaign_verification_v1"]
    evidence_class: str = Field(
        pattern=r"^(?:SYNTHETIC_FIXTURE|EXTERNAL_SEALED_EVIDENCE)$"
    )
    contract_sha256: str = Field(pattern=SHA256_PATTERN)
    runs: tuple[RoutingCampaignRunEvidenceV1, ...] = Field(
        min_length=1, max_length=_MAX_CAMPAIGN_RUNS
    )

    @model_validator(mode="after")
    def require_independent_canonical_runs(
        self,
    ) -> "RoutingCampaignEvidenceBundleV1":
        repetitions = tuple(run.repetition_index for run in self.runs)
        run_ids = tuple(run.run_id for run in self.runs)
        telemetry_ids = tuple(run.telemetry.telemetry_capsule_id for run in self.runs)
        telemetry_digests = tuple(
            run.telemetry.telemetry_capsule_sha256 for run in self.runs
        )
        receipt_ids = tuple(
            receipt.route_decision_receipt_id
            for run in self.runs
            for receipt in run.assignments
        )
        receipt_digests = tuple(
            receipt.route_decision_receipt_sha256
            for run in self.runs
            for receipt in run.assignments
        )
        reset_ids = tuple(
            reset.reset_id for run in self.runs for reset in run.cache_resets
        )
        reset_digests = tuple(
            reset.reset_sha256 for run in self.runs for reset in run.cache_resets
        )
        if repetitions != tuple(sorted(repetitions)):
            raise ValueError("Campaign runs must use canonical repetition order.")
        if len(set(repetitions)) != len(repetitions):
            raise ValueError("Campaign repetition indices must be unique.")
        if len(set(run_ids)) != len(run_ids):
            raise ValueError("Campaign run IDs must be unique.")
        if len(set(telemetry_ids)) != len(telemetry_ids) or len(
            set(telemetry_digests)
        ) != len(telemetry_digests):
            raise ValueError("Telemetry identities must not be reused across runs.")
        if len(set(receipt_ids)) != len(receipt_ids) or len(
            set(receipt_digests)
        ) != len(receipt_digests):
            raise ValueError("Route receipt identities must not be reused across runs.")
        if len(set(reset_ids)) != len(reset_ids) or len(set(reset_digests)) != len(
            reset_digests
        ):
            raise ValueError("Cache reset identities must not be reused across runs.")
        for run in self.runs:
            if run.contract_sha256 != self.contract_sha256:
                raise ValueError("Each run must bind the bundle contract digest.")
            if run.evidence_class != self.evidence_class:
                raise ValueError("Each run must bind the bundle evidence class.")
        return self


class RoutingCampaignPolicyEvaluationResultV1(FrozenExitSpecModel):
    """ExitSpec-recalculated facts for one policy in one run."""

    schema_version: Literal["exitspec.routing-campaign-reduction-result.v1"]
    subject_policy_role: str = Field(pattern=r"^(?:candidate|baseline)$")
    evaluation_role: str = Field(pattern=r"^(?:QUALIFICATION_GATE|REFERENCE_CONTROL)$")
    subject_policy_id: str = Field(pattern=_ID_PATTERN, max_length=128)
    subject_policy_sha256: str = Field(pattern=SHA256_PATTERN)
    eligible_assignment_count: int = Field(ge=0, le=_MAX_CAMPAIGN_ASSIGNMENTS)
    attained_count: int = Field(ge=0, le=_MAX_CAMPAIGN_ASSIGNMENTS)
    not_attained_count: int = Field(ge=0, le=_MAX_CAMPAIGN_ASSIGNMENTS)
    not_proven_count: int = Field(ge=0, le=_MAX_CAMPAIGN_ASSIGNMENTS)
    point_estimate: str = Field(pattern=_PROPORTION_PATTERN, max_length=18)
    wilson_lower_bound: str = Field(pattern=_PROPORTION_PATTERN, max_length=24)
    minimum_sample_count: int = Field(gt=0, le=_MAX_CAMPAIGN_ASSIGNMENTS)
    required_attainment_rate: str = Field(pattern=_PROPORTION_PATTERN, max_length=18)
    confidence_calculator_id: str = Field(
        pattern=r"^exitspec\.statistics\.wilson_lower_bound$"
    )
    confidence_calculator_version: str = Field(pattern=r"^wilson-two-sided-v1$")
    verdict: str = Field(pattern=r"^(?:PASS|FAIL|NOT_PROVEN)$")
    evidence_issues: tuple[RoutingCampaignResultIssue, ...] = Field(
        min_length=0, max_length=len(_RESULT_ISSUE_ORDER)
    )

    @model_validator(mode="after")
    def require_count_conservation(
        self,
    ) -> "RoutingCampaignPolicyEvaluationResultV1":
        if (
            self.attained_count + self.not_attained_count + self.not_proven_count
            != self.eligible_assignment_count
        ):
            raise ValueError("Policy evaluation counts must conserve the population.")
        expected_role = (
            "QUALIFICATION_GATE"
            if self.subject_policy_role == "candidate"
            else "REFERENCE_CONTROL"
        )
        if self.evaluation_role != expected_role:
            raise ValueError("Policy evaluation role must match its policy subject.")
        issue_order = tuple(
            _RESULT_ISSUE_ORDER.index(issue) for issue in self.evidence_issues
        )
        if issue_order != tuple(sorted(issue_order)) or len(set(issue_order)) != len(
            issue_order
        ):
            raise ValueError("Policy evidence issues must use canonical order.")
        return self


class RoutingCampaignRunEvaluationResultV1(FrozenExitSpecModel):
    """ExitSpec-recalculated facts for both policy subjects in one run."""

    schema_version: Literal["exitspec.routing-campaign-reduction-result.v1"]
    run_id: str = Field(pattern=_ID_PATTERN, max_length=128)
    repetition_index: int = Field(ge=1, le=100)
    evidence_issues: tuple[RoutingCampaignResultIssue, ...] = Field(
        min_length=0, max_length=len(_RESULT_ISSUE_ORDER)
    )
    policy_results: tuple[
        RoutingCampaignPolicyEvaluationResultV1,
        RoutingCampaignPolicyEvaluationResultV1,
    ]

    @model_validator(mode="after")
    def require_candidate_then_baseline(
        self,
    ) -> "RoutingCampaignRunEvaluationResultV1":
        if tuple(result.subject_policy_role for result in self.policy_results) != (
            "candidate",
            "baseline",
        ):
            raise ValueError(
                "Run policy results must use candidate then baseline order."
            )
        return self


class RoutingCampaignReductionResultV1(FrozenExitSpecModel):
    """Immutable in-memory B11 facts for a later B12 wrapper."""

    schema_version: Literal["exitspec.routing-campaign-reduction-result.v1"]
    reducer_id: Literal["routing_campaign_deterministic_reducer_v1"]
    reducer_version: Literal["1.0.0"]
    contract_sha256: str = Field(pattern=SHA256_PATTERN)
    required_repetition_indices: tuple[int, ...] = Field(
        min_length=1, max_length=_MAX_CAMPAIGN_RUNS
    )
    missing_repetition_indices: tuple[int, ...] = Field(
        min_length=0, max_length=_MAX_CAMPAIGN_RUNS
    )
    run_results: tuple[RoutingCampaignRunEvaluationResultV1, ...] = Field(
        min_length=0, max_length=_MAX_CAMPAIGN_RUNS
    )
    campaign_verdict: str = Field(pattern=r"^(?:PASS|FAIL|NOT_PROVEN)$")
    verdict_authority: str = Field(pattern=r"^EXIT_SPEC_ONLY$")

    @model_validator(mode="after")
    def require_canonical_result_order(self) -> "RoutingCampaignReductionResultV1":
        actual = tuple(result.repetition_index for result in self.run_results)
        if actual != tuple(sorted(actual)) or len(set(actual)) != len(actual):
            raise ValueError(
                "Reduction run results must use canonical repetition order."
            )
        if self.missing_repetition_indices != tuple(
            index for index in self.required_repetition_indices if index not in actual
        ):
            raise ValueError("Reduction missing repetition facts are inconsistent.")
        return self


def _validated_contract(contract: object) -> POCContract:
    if type(contract) is not POCContract:
        _reject(
            RoutingQualificationValidationCode.WRONG_TYPE,
            "A frozen full B9+B10+B11 POCContract is required.",
        )
    validated = _revalidate_typed_model(
        contract, POCContract, label="routing campaign reduction contract"
    )
    if validated.status.value != "FROZEN":
        _reject(
            RoutingQualificationValidationCode.CONTRACT_BINDING_MISMATCH,
            "Routing campaign reduction requires a FROZEN POCContract.",
            "status",
        )
    if not verify_contract_digest(validated):
        _reject(
            RoutingQualificationValidationCode.CONTRACT_BINDING_MISMATCH,
            "Routing campaign reduction requires a digest-valid frozen contract.",
        )
    if tuple(type(criterion) for criterion in validated.criteria) != (
        RoutingQualificationCriterionV1,
        RoutingSLOAttainmentCriterionV1,
        RoutingCampaignReductionCriterionV1,
    ):
        _reject(
            RoutingQualificationValidationCode.CONTRACT_BINDING_MISMATCH,
            "B11 requires exactly ordered B9, B10, and B11 criteria.",
            "criteria",
        )
    campaign, slo, reduction = validated.criteria
    expected = (
        reduction.campaign_criterion_id,
        reduction.campaign_protocol_id,
        reduction.campaign_schema_version,
        reduction.slo_criterion_id,
        reduction.slo_protocol_id,
        reduction.slo_schema_version,
        reduction.candidate_policy_id,
        reduction.candidate_policy_sha256,
        reduction.baseline_policy_id,
        reduction.baseline_policy_sha256,
        reduction.required_repetition_count,
        reduction.required_repetition_indices,
    )
    actual = (
        campaign.id,
        campaign.protocol_id,
        campaign.schema_version,
        slo.id,
        slo.protocol_id,
        slo.schema_version,
        campaign.candidate_policy.policy_id,
        campaign.candidate_policy.policy_sha256,
        campaign.baseline_policy.policy_id,
        campaign.baseline_policy.policy_sha256,
        campaign.run_policy.default_repetitions,
        tuple(range(1, campaign.run_policy.default_repetitions + 1)),
    )
    if expected != actual:
        _reject(
            RoutingQualificationValidationCode.CONTRACT_BINDING_MISMATCH,
            "B11 reduction bindings do not match the unchanged B9/B10 contract.",
            "criteria[2]",
        )
    return validated


def _validated_confirmation(confirmation: object) -> ContractConfirmation:
    if type(confirmation) is not ContractConfirmation:
        _reject(
            RoutingQualificationValidationCode.WRONG_TYPE,
            "A typed ContractConfirmation is required for routing campaign acceptance.",
            "confirmation",
        )
    return _revalidate_typed_model(
        confirmation,
        ContractConfirmation,
        label="routing campaign customer confirmation",
    )


def _validated_confirmed_contract(
    contract: object,
    confirmation: object,
) -> POCContract:
    """Require the exact affirmative record used to freeze this contract."""

    validated = _validated_contract(contract)
    confirmed = _validated_confirmation(confirmation)
    if validated.confirmation_id != confirmed.confirmation_id:
        _reject(
            RoutingQualificationValidationCode.CONTRACT_BINDING_MISMATCH,
            "Frozen contract and customer confirmation IDs do not match.",
            "confirmation.confirmation_id",
        )
    try:
        require_affirmative_confirmation(validated, confirmed)
    except ValueError as error:
        _reject(
            RoutingQualificationValidationCode.CONTRACT_BINDING_MISMATCH,
            "Routing campaign acceptance requires an affirmative confirmation for the exact frozen contract.",
            "confirmation",
        )
        raise AssertionError("unreachable") from error
    return validated


def parse_routing_campaign_contract(value: bytes | Mapping[str, Any]) -> POCContract:
    """Strictly parse the frozen full B9+B10+B11 contract."""

    payload = _load_object(
        value,
        label="routing campaign reduction contract",
        max_json_integer=_MAX_CAMPAIGN_JSON_INTEGER,
        max_bytes=_MAX_CAMPAIGN_CONTRACT_BYTES,
    )
    _reject_producer_verdict_aliases(payload)
    criteria = payload.get("criteria")
    if type(criteria) is list:
        for index, criterion_payload in enumerate(criteria):
            if type(criterion_payload) is not dict:
                continue
            criterion_type = criterion_payload.get("criterion_type")
            model_type: type[Any] | None = None
            if criterion_type == "routing_qualification_v1":
                model_type = RoutingQualificationCriterionV1
            elif criterion_type == "routing_slo_attainment_v1":
                model_type = RoutingSLOAttainmentCriterionV1
            elif criterion_type == ROUTING_CAMPAIGN_REDUCTION_PROTOCOL_ID:
                model_type = RoutingCampaignReductionCriterionV1
            if model_type is not None:
                _validate_model(
                    model_type,
                    criterion_payload,
                    label=f"routing campaign reduction contract.criteria[{index}]",
                    path_prefix=f"criteria[{index}]",
                )
    parsed = _validate_model(
        POCContract,
        payload,
        label="routing campaign reduction contract",
    )
    return _validated_contract(parsed)


def validate_routing_campaign_contract(value: POCContract) -> POCContract:
    """Revalidate and bind a typed frozen B9+B10+B11 contract."""

    return _validated_contract(value)


def routing_campaign_contract_digest(value: POCContract) -> str:
    """Return the existing full-contract digest; B11 has no side digest."""

    return contract_digest(_validated_contract(value))


def serialize_routing_campaign_contract(value: POCContract) -> bytes:
    """Serialize a B11 contract only after complete strict revalidation."""

    validated = _validated_contract(value)
    content = canonical_json_bytes(validated.model_dump(mode="json"))
    parsed = parse_routing_campaign_contract(content)
    if parsed != validated:
        _reject(
            RoutingQualificationValidationCode.SEMANTIC_INCONSISTENCY,
            "Routing campaign reduction contract changed during serialization.",
        )
    return content


def parse_routing_campaign_confirmation(
    value: bytes | Mapping[str, Any],
) -> ContractConfirmation:
    """Strictly parse a bounded synthetic or persisted confirmation record."""

    payload = _load_object(
        value,
        label="routing campaign customer confirmation",
        max_json_integer=_MAX_CAMPAIGN_JSON_INTEGER,
        max_bytes=_MAX_CAMPAIGN_CONTRACT_BYTES,
    )
    return _validate_model(
        ContractConfirmation,
        payload,
        label="routing campaign customer confirmation",
        reject_producer_verdict_aliases=False,
    )


def serialize_routing_campaign_confirmation(
    value: ContractConfirmation,
) -> bytes:
    """Serialize a confirmation record canonically after strict revalidation."""

    validated = _validated_confirmation(value)
    content = canonical_json_bytes(validated.model_dump(mode="json"))
    if parse_routing_campaign_confirmation(content) != validated:
        _reject(
            RoutingQualificationValidationCode.SEMANTIC_INCONSISTENCY,
            "Routing campaign customer confirmation changed during serialization.",
        )
    return content


def _validated_run(value: object) -> RoutingCampaignRunEvidenceV1:
    if type(value) is not RoutingCampaignRunEvidenceV1:
        _reject(
            RoutingQualificationValidationCode.WRONG_TYPE,
            "A typed routing campaign run evidence object is required.",
        )
    return _revalidate_typed_model(
        value,
        RoutingCampaignRunEvidenceV1,
        label="routing campaign run evidence",
    )


def _validated_bundle(value: object) -> RoutingCampaignEvidenceBundleV1:
    if type(value) is not RoutingCampaignEvidenceBundleV1:
        _reject(
            RoutingQualificationValidationCode.WRONG_TYPE,
            "A typed routing campaign evidence bundle is required.",
        )
    return _revalidate_typed_model(
        value,
        RoutingCampaignEvidenceBundleV1,
        label="routing campaign evidence bundle",
    )


def _validate_internal_digests(
    evidence: RoutingCampaignRunEvidenceV1,
) -> RoutingCampaignRunEvidenceV1:
    """Check every digest whose canonical bytes are present in this envelope."""

    if evidence.telemetry.telemetry_capsule_sha256 != _sha256_without_field(
        evidence.telemetry, "telemetry_capsule_sha256"
    ):
        _reject(
            RoutingQualificationValidationCode.INVALID_DIGEST,
            "Telemetry capsule digest does not match its canonical capsule bytes.",
            "telemetry.telemetry_capsule_sha256",
        )
    for index, reset in enumerate(evidence.cache_resets):
        if reset.reset_sha256 != _sha256_without_field(reset, "reset_sha256"):
            _reject(
                RoutingQualificationValidationCode.INVALID_DIGEST,
                "Cache reset digest does not match its canonical reset evidence.",
                f"cache_resets[{index}].reset_sha256",
            )
    for index, receipt in enumerate(evidence.assignments):
        if receipt.route_decision_receipt_sha256 != _sha256_without_field(
            receipt, "route_decision_receipt_sha256"
        ):
            _reject(
                RoutingQualificationValidationCode.INVALID_DIGEST,
                "Route receipt digest does not match its canonical receipt bytes.",
                f"assignments[{index}].route_decision_receipt_sha256",
            )
    return evidence


def parse_routing_campaign_run_evidence(
    value: bytes | Mapping[str, Any],
) -> RoutingCampaignRunEvidenceV1:
    """Strictly parse one canonical bounded run evidence envelope."""

    payload = _load_object(
        value,
        label="routing campaign evidence",
        max_json_integer=_MAX_CAMPAIGN_JSON_INTEGER,
        max_bytes=_MAX_CAMPAIGN_EVIDENCE_BYTES,
    )
    _reject_producer_verdict_aliases(payload)
    return _validate_internal_digests(
        _validate_model(
            RoutingCampaignRunEvidenceV1,
            payload,
            label="routing campaign evidence",
        )
    )


def serialize_routing_campaign_run_evidence(
    value: RoutingCampaignRunEvidenceV1,
) -> bytes:
    """Serialize one run only after strict raw-state revalidation."""

    validated = _validate_internal_digests(_validated_run(value))
    content = canonical_json_bytes(validated.model_dump(mode="json"))
    parsed = parse_routing_campaign_run_evidence(content)
    if parsed != validated:
        _reject(
            RoutingQualificationValidationCode.SEMANTIC_INCONSISTENCY,
            "Routing campaign evidence changed during serialization.",
        )
    return content


def parse_routing_campaign_evidence(
    value: bytes | Mapping[str, Any],
) -> RoutingCampaignEvidenceBundleV1:
    """Strictly parse one canonical bounded multi-run evidence bundle."""

    payload = _load_object(
        value,
        label="routing campaign evidence bundle",
        max_json_integer=_MAX_CAMPAIGN_JSON_INTEGER,
        max_bytes=_MAX_CAMPAIGN_EVIDENCE_BYTES,
    )
    _reject_producer_verdict_aliases(payload)
    bundle = _validate_model(
        RoutingCampaignEvidenceBundleV1,
        payload,
        label="routing campaign evidence bundle",
    )
    for run in bundle.runs:
        _validate_internal_digests(run)
    return bundle


def serialize_routing_campaign_evidence(
    value: RoutingCampaignEvidenceBundleV1,
) -> bytes:
    """Serialize a bundle only after strict raw-state revalidation."""

    validated = _validated_bundle(value)
    for run in validated.runs:
        _validate_internal_digests(run)
    content = canonical_json_bytes(validated.model_dump(mode="json"))
    parsed = parse_routing_campaign_evidence(content)
    if parsed != validated:
        _reject(
            RoutingQualificationValidationCode.SEMANTIC_INCONSISTENCY,
            "Routing campaign evidence bundle changed during serialization.",
        )
    return content


def _bind_run(
    contract: POCContract,
    evidence: RoutingCampaignRunEvidenceV1,
) -> RoutingCampaignRunEvidenceV1:
    evidence = _validate_internal_digests(_validated_run(evidence))
    campaign, _slo, _reduction = contract.criteria
    expected = {
        "contract_sha256": contract.canonical_hash,
        "candidate_policy_id": campaign.candidate_policy.policy_id,
        "candidate_policy_sha256": campaign.candidate_policy.policy_sha256,
        "baseline_policy_id": campaign.baseline_policy.policy_id,
        "baseline_policy_sha256": campaign.baseline_policy.policy_sha256,
        "routing_configuration_id": campaign.routing_configuration.configuration_id,
        "routing_configuration_sha256": campaign.routing_configuration.configuration_sha256,
        "request_trace_id": campaign.request_trace.trace_id,
        "request_trace_sha256": campaign.request_trace.trace_sha256,
        "failure_injection_id": campaign.failure_injection.configuration_id,
        "failure_injection_sha256": campaign.failure_injection.configuration_sha256,
    }
    for name, expected_value in expected.items():
        if getattr(evidence, name) != expected_value:
            _reject(
                RoutingQualificationValidationCode.CONTRACT_BINDING_MISMATCH,
                f"Run evidence field {name} does not match the frozen contract.",
                name,
            )
    serving = evidence.serving
    requirement = campaign.serving
    environment = requirement.execution_environment
    serving_expected = {
        "engine": requirement.engine,
        "engine_version": requirement.engine_version,
        "model": requirement.model,
        "model_revision": requirement.model_revision,
        "tokenizer": requirement.tokenizer,
        "tokenizer_revision": requirement.tokenizer_revision,
        "quantization": requirement.quantization,
        "tensor_parallel_size": requirement.tensor_parallel_size,
        "environment_id": environment.environment_id,
        "environment_sha256": environment.environment_sha256,
        "target_engine_version": requirement.engine_version,
        "target_model_revision": requirement.model_revision,
        "target_tokenizer_revision": requirement.tokenizer_revision,
        "execution_environment_id": environment.environment_id,
    }
    for name, expected_value in serving_expected.items():
        if getattr(serving, name) != expected_value:
            _reject(
                RoutingQualificationValidationCode.CONTRACT_BINDING_MISMATCH,
                f"Serving evidence field {name} does not match the frozen contract.",
                f"serving.{name}",
            )
    if evidence.telemetry.environment_id != environment.environment_id:
        _reject(
            RoutingQualificationValidationCode.CONTRACT_BINDING_MISMATCH,
            "Telemetry environment identity does not match the frozen contract.",
            "telemetry.environment_id",
        )

    expected_request_count = campaign.trial_order.request_count
    expected_trial_count = campaign.trial_order.trial_count
    expected_assignments = expected_request_count * expected_trial_count * 2
    if len(evidence.cache_resets) > expected_trial_count:
        _reject(
            RoutingQualificationValidationCode.EXTRA_FIELD,
            "Run evidence contains more cache resets than required trials.",
            "cache_resets",
        )
    if len(evidence.assignments) > expected_assignments:
        _reject(
            RoutingQualificationValidationCode.EXTRA_FIELD,
            "Run evidence contains more assignments than the frozen population.",
            "assignments",
        )
    for reset in evidence.cache_resets:
        if reset.trial_index >= expected_trial_count:
            _reject(
                RoutingQualificationValidationCode.CONTRACT_BINDING_MISMATCH,
                "Cache reset trial index is outside the frozen trial population.",
                "cache_resets.trial_index",
            )
    reset_coordinates = tuple(reset.trial_index for reset in evidence.cache_resets)
    if len(set(reset_coordinates)) != len(reset_coordinates):
        _reject(
            RoutingQualificationValidationCode.SEMANTIC_INCONSISTENCY,
            "Cache reset records must have unique trial coordinates.",
            "cache_resets.trial_index",
        )
    if len(
        evidence.cache_resets
    ) == expected_trial_count and reset_coordinates != tuple(
        range(expected_trial_count)
    ):
        _reject(
            RoutingQualificationValidationCode.SEMANTIC_INCONSISTENCY,
            "Complete cache reset evidence must cover each frozen trial exactly once.",
            "cache_resets.trial_index",
        )
    for receipt in evidence.assignments:
        request_index = _request_index(receipt.request_id)
        if receipt.trial_index >= expected_trial_count:
            _reject(
                RoutingQualificationValidationCode.CONTRACT_BINDING_MISMATCH,
                "Route receipt trial index is outside the frozen trial population.",
                "assignments.trial_index",
            )
        if request_index >= expected_request_count:
            _reject(
                RoutingQualificationValidationCode.CONTRACT_BINDING_MISMATCH,
                "Route receipt request index is outside the frozen request population.",
                "assignments.request_id",
            )
        if _timestamp_seconds(receipt.captured_at) > _timestamp_seconds(
            evidence.observed_at
        ):
            _reject(
                RoutingQualificationValidationCode.SEMANTIC_INCONSISTENCY,
                "Route receipt cannot be captured after enclosing evidence observation.",
                "assignments.captured_at",
            )
    for reset in evidence.cache_resets:
        if _timestamp_seconds(reset.reset_at) > _timestamp_seconds(
            evidence.observed_at
        ):
            _reject(
                RoutingQualificationValidationCode.SEMANTIC_INCONSISTENCY,
                "Cache reset cannot be observed after enclosing evidence observation.",
                "cache_resets.reset_at",
            )
        for receipt in evidence.assignments:
            if receipt.trial_index == reset.trial_index and _timestamp_seconds(
                reset.reset_at
            ) > _timestamp_seconds(receipt.captured_at):
                _reject(
                    RoutingQualificationValidationCode.SEMANTIC_INCONSISTENCY,
                    "Cache reset evidence must precede receipts from its trial.",
                    "cache_resets.reset_at",
                )
    return evidence


def validate_routing_campaign_run_evidence(
    contract: POCContract,
    confirmation: ContractConfirmation,
    evidence: RoutingCampaignRunEvidenceV1,
) -> RoutingCampaignRunEvidenceV1:
    """Admit one structurally valid run after exact contract binding checks."""

    return _bind_run(_validated_confirmed_contract(contract, confirmation), evidence)


def _sha256_without_field(model: FrozenExitSpecModel, field: str) -> str:
    payload = model.model_dump(mode="json", exclude={field})
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def _format_probability(value: Decimal | float) -> str:
    decimal = value if isinstance(value, Decimal) else Decimal(str(value))
    quantized = decimal.quantize(Decimal("0.000000000001"), rounding=ROUND_HALF_UP)
    if quantized == 0:
        return "0"
    if quantized == 1:
        return "1"
    text = format(quantized, "f").rstrip("0").rstrip(".")
    return text or "0"


def _reject_result_semantic_mismatch(message: str, path: str) -> None:
    _reject(
        RoutingQualificationValidationCode.SEMANTIC_INCONSISTENCY,
        message,
        path,
    )


def _validate_policy_result_semantics(
    result: RoutingCampaignPolicyEvaluationResultV1,
    *,
    path: str,
) -> None:
    """Recalculate every serialized policy fact from its integer population."""

    eligible = result.eligible_assignment_count
    if eligible <= 0:
        _reject_result_semantic_mismatch(
            "Policy evaluation must contain a non-empty frozen assignment population.",
            f"{path}.eligible_assignment_count",
        )
    if result.attained_count > eligible or result.not_attained_count > eligible:
        _reject_result_semantic_mismatch(
            "Policy evaluation counts exceed the eligible assignment population.",
            path,
        )
    point = Decimal(result.attained_count) / Decimal(eligible)
    expected_point = _format_probability(point)
    if result.point_estimate != expected_point:
        _reject_result_semantic_mismatch(
            "Policy point estimate does not match its integer counts.",
            f"{path}.point_estimate",
        )
    lower_bound = wilson_lower_bound(result.attained_count, eligible, 0.95)
    expected_lower_bound = _format_probability(lower_bound)
    if result.wilson_lower_bound != expected_lower_bound:
        _reject_result_semantic_mismatch(
            "Policy Wilson lower bound does not match its integer counts.",
            f"{path}.wilson_lower_bound",
        )

    issues = tuple(result.evidence_issues)
    if len(set(issues)) != len(issues):
        _reject_result_semantic_mismatch(
            "Policy evidence issues must be unique and canonical.",
            f"{path}.evidence_issues",
        )
    wilson_issue = "WILSON_CONFIDENCE_INSUFFICIENT"
    non_wilson_issues = tuple(issue for issue in issues if issue != wilson_issue)
    if non_wilson_issues:
        if wilson_issue in issues:
            _reject_result_semantic_mismatch(
                "Wilson insufficiency cannot be combined with other evidence issues.",
                f"{path}.evidence_issues",
            )
        expected_verdict = "NOT_PROVEN"
    else:
        lower_bound_decimal = Decimal(expected_lower_bound)
        required_rate = Decimal(result.required_attainment_rate)
        if lower_bound_decimal >= required_rate:
            expected_verdict = "PASS"
        elif point >= required_rate:
            if issues != (wilson_issue,):
                _reject_result_semantic_mismatch(
                    "Favorable point estimate below Wilson confidence requires the canonical issue.",
                    f"{path}.evidence_issues",
                )
            expected_verdict = "NOT_PROVEN"
        else:
            if wilson_issue in issues:
                _reject_result_semantic_mismatch(
                    "Wilson insufficiency is invalid below the required point estimate.",
                    f"{path}.evidence_issues",
                )
            expected_verdict = "FAIL"
    if result.verdict != expected_verdict:
        _reject_result_semantic_mismatch(
            "Policy verdict does not match its recomputed counts and issues.",
            f"{path}.verdict",
        )


def _validate_result_semantics(
    value: RoutingCampaignReductionResultV1,
) -> RoutingCampaignReductionResultV1:
    """Recalculate an in-memory result before any serializer or handoff uses it."""

    required = value.required_repetition_indices
    if required != tuple(range(1, len(required) + 1)):
        _reject_result_semantic_mismatch(
            "Required repetition indices must be the canonical one-based sequence.",
            "required_repetition_indices",
        )
    actual = tuple(result.repetition_index for result in value.run_results)
    if any(index not in required for index in actual):
        _reject_result_semantic_mismatch(
            "Reduction results contain an extra repetition index.",
            "run_results",
        )
    expected_missing = tuple(index for index in required if index not in actual)
    if value.missing_repetition_indices != expected_missing:
        _reject_result_semantic_mismatch(
            "Missing repetition indices do not match the supplied run results.",
            "missing_repetition_indices",
        )
    for run_index, run in enumerate(value.run_results):
        if len(run.policy_results) != 2:
            _reject_result_semantic_mismatch(
                "Each reduction run must contain candidate and baseline results.",
                f"run_results[{run_index}].policy_results",
            )
        if run.policy_results[0].subject_policy_role != "candidate":
            _reject_result_semantic_mismatch(
                "Candidate policy result must precede baseline policy result.",
                f"run_results[{run_index}].policy_results[0]",
            )
        if run.policy_results[1].subject_policy_role != "baseline":
            _reject_result_semantic_mismatch(
                "Baseline policy result must follow candidate policy result.",
                f"run_results[{run_index}].policy_results[1]",
            )
        for policy_index, policy in enumerate(run.policy_results):
            _validate_policy_result_semantics(
                policy,
                path=f"run_results[{run_index}].policy_results[{policy_index}]",
            )

    candidate_results = tuple(run.policy_results[0] for run in value.run_results)
    baseline_results = tuple(run.policy_results[1] for run in value.run_results)
    contextual_incompleteness = {
        "MISSING_ASSIGNMENT",
        "NOT_PROVEN_ASSIGNMENT",
        "STALE_TELEMETRY",
        "CACHE_RESET_EVIDENCE_INCOMPLETE",
        "CACHE_RESET_NOT_CONFIRMED",
        "INSUFFICIENT_SAMPLE_COUNT",
    }
    candidate_not_proven = any(
        policy.verdict == "NOT_PROVEN" for policy in candidate_results
    )
    baseline_evidence_incomplete = any(
        issue in contextual_incompleteness
        for result in baseline_results
        for issue in result.evidence_issues
    )
    run_evidence_incomplete = any(
        issue in contextual_incompleteness
        for result in value.run_results
        for issue in result.evidence_issues
    )
    if (
        expected_missing
        or candidate_not_proven
        or baseline_evidence_incomplete
        or run_evidence_incomplete
    ):
        expected_campaign_verdict = "NOT_PROVEN"
    elif any(policy.verdict == "FAIL" for policy in candidate_results):
        expected_campaign_verdict = "FAIL"
    elif len(candidate_results) == len(required) and all(
        policy.verdict == "PASS" for policy in candidate_results
    ):
        expected_campaign_verdict = "PASS"
    else:
        expected_campaign_verdict = "NOT_PROVEN"
    if value.campaign_verdict != expected_campaign_verdict:
        _reject_result_semantic_mismatch(
            "Campaign verdict does not match candidate results and required evidence completeness.",
            "campaign_verdict",
        )
    return value


def _policy_result(
    run: RoutingCampaignRunEvidenceV1,
    campaign: RoutingQualificationCriterionV1,
    slo: RoutingSLOAttainmentCriterionV1,
    role: str,
    run_issues: tuple[str, ...],
) -> RoutingCampaignPolicyEvaluationResultV1:
    role_index = 0 if role == "candidate" else 1
    policy_id = (
        campaign.candidate_policy.policy_id
        if role == "candidate"
        else campaign.baseline_policy.policy_id
    )
    policy_sha256 = (
        campaign.candidate_policy.policy_sha256
        if role == "candidate"
        else campaign.baseline_policy.policy_sha256
    )
    evaluation_role = (
        "QUALIFICATION_GATE" if role == "candidate" else "REFERENCE_CONTROL"
    )
    confidence_rule = slo.policy_confidence_rules[role_index]
    observation = slo.assignment_slo_envelopes[role_index].required_observations[0]
    expected = campaign.trial_order.trial_count * campaign.trial_order.request_count
    receipts = {
        (receipt.trial_index, _request_index(receipt.request_id)): receipt
        for receipt in run.assignments
        if receipt.policy_role == role
    }
    attained = 0
    not_attained = 0
    not_proven = 0
    for trial_index in range(campaign.trial_order.trial_count):
        for request_index in range(campaign.trial_order.request_count):
            receipt = receipts.get((trial_index, request_index))
            if receipt is None:
                not_proven += 1
                continue
            if receipt.terminal_outcome in _NOT_PROVEN_OUTCOMES:
                not_proven += 1
            elif receipt.terminal_outcome in _EXTERNAL_NOT_ATTAINED:
                not_attained += 1
            elif receipt.terminal_outcome == "SUCCESS":
                if (
                    receipt.latency_ns is not None
                    and receipt.latency_ns <= observation.threshold_ns
                ):
                    attained += 1
                else:
                    not_attained += 1
            else:
                _reject(
                    RoutingQualificationValidationCode.SEMANTIC_INCONSISTENCY,
                    "Unsupported terminal outcome reached reduction.",
                )
    point_estimate = Decimal(attained) / Decimal(expected)
    lower_bound = wilson_lower_bound(attained, expected, 0.95)
    issues = list(run_issues)
    if expected != len(receipts) and "MISSING_ASSIGNMENT" not in issues:
        issues.append("MISSING_ASSIGNMENT")
    if not_proven and "NOT_PROVEN_ASSIGNMENT" not in issues:
        issues.append("NOT_PROVEN_ASSIGNMENT")
    minimum = confidence_rule.confidence.minimum_sample_count
    required_rate = confidence_rule.confidence.required_attainment_rate
    if expected < minimum and "INSUFFICIENT_SAMPLE_COUNT" not in issues:
        issues.append("INSUFFICIENT_SAMPLE_COUNT")
    wilson_text = _format_probability(lower_bound)
    if issues:
        verdict = "NOT_PROVEN"
    elif Decimal(wilson_text) >= Decimal(required_rate):
        verdict = "PASS"
    elif point_estimate >= Decimal(required_rate):
        issues.append("WILSON_CONFIDENCE_INSUFFICIENT")
        verdict = "NOT_PROVEN"
    else:
        verdict = "FAIL"
    return RoutingCampaignPolicyEvaluationResultV1(
        schema_version=ROUTING_CAMPAIGN_RESULT_SCHEMA_VERSION,
        subject_policy_role=role,
        evaluation_role=evaluation_role,
        subject_policy_id=policy_id,
        subject_policy_sha256=policy_sha256,
        eligible_assignment_count=expected,
        attained_count=attained,
        not_attained_count=not_attained,
        not_proven_count=not_proven,
        point_estimate=_format_probability(point_estimate),
        wilson_lower_bound=_format_probability(lower_bound),
        minimum_sample_count=minimum,
        required_attainment_rate=required_rate,
        confidence_calculator_id="exitspec.statistics.wilson_lower_bound",
        confidence_calculator_version=CALCULATION_VERSION,
        verdict=verdict,
        evidence_issues=tuple(issues),
    )


def _run_issues(
    run: RoutingCampaignRunEvidenceV1,
    campaign: RoutingQualificationCriterionV1,
) -> tuple[str, ...]:
    issues: list[str] = []
    age_seconds = _timestamp_seconds(run.observed_at) - _timestamp_seconds(
        run.telemetry.captured_at
    )
    if age_seconds > campaign.telemetry.max_age_seconds:
        issues.append("STALE_TELEMETRY")
    if len(run.cache_resets) != campaign.trial_order.trial_count:
        issues.append("CACHE_RESET_EVIDENCE_INCOMPLETE")
    if any(reset.status != "RESET_CONFIRMED" for reset in run.cache_resets):
        issues.append("CACHE_RESET_NOT_CONFIRMED")
    return tuple(issues)


def reduce_routing_campaign(
    contract: POCContract,
    confirmation: ContractConfirmation,
    evidence: (
        RoutingCampaignEvidenceBundleV1
        | RoutingCampaignRunEvidenceV1
        | bytes
        | Mapping[str, Any]
        | Sequence[RoutingCampaignRunEvidenceV1 | bytes | Mapping[str, Any]]
    ),
) -> RoutingCampaignReductionResultV1:
    """Independently evaluate and deterministically reduce the required runs."""

    frozen = _validated_confirmed_contract(contract, confirmation)
    reduction = frozen.criteria[2]
    if isinstance(evidence, (bytes, Mapping)):
        payload = _load_object(
            evidence,
            label="routing campaign evidence input",
            max_json_integer=_MAX_CAMPAIGN_JSON_INTEGER,
            max_bytes=_MAX_CAMPAIGN_EVIDENCE_BYTES,
        )
        schema_version = payload.get("schema_version")
        if schema_version == ROUTING_CAMPAIGN_EVIDENCE_SCHEMA_VERSION:
            runs = (parse_routing_campaign_run_evidence(evidence),)
        elif schema_version == ROUTING_CAMPAIGN_EVIDENCE_BUNDLE_SCHEMA_VERSION:
            bundle = parse_routing_campaign_evidence(evidence)
            runs = tuple(bundle.runs)
            if bundle.contract_sha256 != frozen.canonical_hash:
                _reject(
                    RoutingQualificationValidationCode.CONTRACT_BINDING_MISMATCH,
                    "Evidence bundle contract digest does not match the frozen contract.",
                    "contract_sha256",
                )
        else:
            _reject(
                RoutingQualificationValidationCode.WRONG_VERSION,
                "Routing campaign evidence input must name exactly one supported top-level schema.",
                "schema_version",
            )
    elif type(evidence) is RoutingCampaignEvidenceBundleV1:
        bundle = _validated_bundle(evidence)
        runs = tuple(bundle.runs)
    elif type(evidence) is RoutingCampaignRunEvidenceV1:
        runs = (evidence,)
    elif type(evidence) in (tuple, list):
        if len(evidence) > _MAX_CAMPAIGN_RUNS:
            _reject(
                RoutingQualificationValidationCode.OVERSIZED,
                "Campaign evidence contains too many runs.",
            )
        parsed_runs: list[RoutingCampaignRunEvidenceV1] = []
        for item in evidence:
            if isinstance(item, (bytes, Mapping)):
                parsed_runs.append(parse_routing_campaign_run_evidence(item))
            else:
                parsed_runs.append(_validated_run(item))
        runs = tuple(parsed_runs)
    else:
        _reject(
            RoutingQualificationValidationCode.WRONG_TYPE,
            "Campaign reduction requires a bundle, run, bytes, mapping, tuple, or list.",
        )
    repetitions = tuple(run.repetition_index for run in runs)
    if repetitions != tuple(sorted(repetitions)):
        _reject(
            RoutingQualificationValidationCode.NON_CANONICAL,
            "Campaign runs must be supplied in canonical repetition order.",
            "runs",
        )
    validated_runs = tuple(_bind_run(frozen, run) for run in runs)
    evidence_classes = {run.evidence_class for run in validated_runs}
    if len(evidence_classes) > 1:
        _reject(
            RoutingQualificationValidationCode.SEMANTIC_INCONSISTENCY,
            "A campaign cannot combine synthetic and external evidence classes.",
            "runs.evidence_class",
        )
    if len(set(run.repetition_index for run in validated_runs)) != len(validated_runs):
        _reject(
            RoutingQualificationValidationCode.SEMANTIC_INCONSISTENCY,
            "Campaign repetition indices must be unique.",
            "runs",
        )
    if len(set(run.run_id for run in validated_runs)) != len(validated_runs):
        _reject(
            RoutingQualificationValidationCode.SEMANTIC_INCONSISTENCY,
            "Campaign run IDs must be unique.",
            "runs",
        )
    required = reduction.required_repetition_indices
    if any(run.repetition_index not in required for run in validated_runs):
        _reject(
            RoutingQualificationValidationCode.CONTRACT_BINDING_MISMATCH,
            "Campaign evidence contains an extra repetition index.",
            "runs.repetition_index",
        )
    telemetry_ids = [run.telemetry.telemetry_capsule_id for run in validated_runs]
    telemetry_digests = [
        run.telemetry.telemetry_capsule_sha256 for run in validated_runs
    ]
    receipt_ids = [
        receipt.route_decision_receipt_id
        for run in validated_runs
        for receipt in run.assignments
    ]
    receipt_digests = [
        receipt.route_decision_receipt_sha256
        for run in validated_runs
        for receipt in run.assignments
    ]
    reset_ids = [reset.reset_id for run in validated_runs for reset in run.cache_resets]
    reset_digests = [
        reset.reset_sha256 for run in validated_runs for reset in run.cache_resets
    ]
    if any(
        len(values) != len(set(values))
        for values in (
            telemetry_ids,
            telemetry_digests,
            receipt_ids,
            receipt_digests,
            reset_ids,
            reset_digests,
        )
    ):
        _reject(
            RoutingQualificationValidationCode.SEMANTIC_INCONSISTENCY,
            "Independent runs must not reuse telemetry, reset, or route receipt identities.",
            "runs",
        )

    campaign, slo, _ = frozen.criteria
    run_results: list[RoutingCampaignRunEvaluationResultV1] = []
    for run in validated_runs:
        issues = _run_issues(run, campaign)
        policy_results = (
            _policy_result(run, campaign, slo, "candidate", issues),
            _policy_result(run, campaign, slo, "baseline", issues),
        )
        run_results.append(
            RoutingCampaignRunEvaluationResultV1(
                schema_version=ROUTING_CAMPAIGN_RESULT_SCHEMA_VERSION,
                run_id=run.run_id,
                repetition_index=run.repetition_index,
                evidence_issues=issues,
                policy_results=policy_results,
            )
        )
    actual_repetitions = tuple(result.repetition_index for result in run_results)
    missing = tuple(index for index in required if index not in actual_repetitions)
    candidate_results = tuple(result.policy_results[0] for result in run_results)
    baseline_results = tuple(result.policy_results[1] for result in run_results)
    contextual_incompleteness = {
        "MISSING_ASSIGNMENT",
        "NOT_PROVEN_ASSIGNMENT",
        "STALE_TELEMETRY",
        "CACHE_RESET_EVIDENCE_INCOMPLETE",
        "CACHE_RESET_NOT_CONFIRMED",
        "INSUFFICIENT_SAMPLE_COUNT",
    }
    candidate_not_proven = any(
        policy.verdict == "NOT_PROVEN" for policy in candidate_results
    )
    baseline_evidence_incomplete = any(
        issue in contextual_incompleteness
        for result in baseline_results
        for issue in result.evidence_issues
    )
    run_evidence_incomplete = any(
        issue in contextual_incompleteness
        for result in run_results
        for issue in result.evidence_issues
    )
    any_not_proven = (
        bool(missing)
        or candidate_not_proven
        or baseline_evidence_incomplete
        or run_evidence_incomplete
    )
    any_candidate_fail = any(policy.verdict == "FAIL" for policy in candidate_results)
    if any_not_proven:
        campaign_verdict = "NOT_PROVEN"
    elif any_candidate_fail:
        campaign_verdict = "FAIL"
    elif len(candidate_results) != len(required) or not all(
        policy.verdict == "PASS" for policy in candidate_results
    ):
        campaign_verdict = "NOT_PROVEN"
    else:
        campaign_verdict = "PASS"
    result = RoutingCampaignReductionResultV1(
        schema_version=ROUTING_CAMPAIGN_RESULT_SCHEMA_VERSION,
        reducer_id=ROUTING_CAMPAIGN_REDUCER_ID,
        reducer_version=ROUTING_CAMPAIGN_REDUCER_VERSION,
        contract_sha256=frozen.canonical_hash or "",
        required_repetition_indices=required,
        missing_repetition_indices=missing,
        run_results=tuple(run_results),
        campaign_verdict=campaign_verdict,
        verdict_authority="EXIT_SPEC_ONLY",
    )
    return _validate_result_semantics(result)


def validate_routing_campaign_reduction_result(
    contract: POCContract,
    confirmation: ContractConfirmation,
    evidence: (
        RoutingCampaignEvidenceBundleV1
        | RoutingCampaignRunEvidenceV1
        | bytes
        | Mapping[str, Any]
        | Sequence[RoutingCampaignRunEvidenceV1 | bytes | Mapping[str, Any]]
    ),
    value: RoutingCampaignReductionResultV1,
) -> RoutingCampaignReductionResultV1:
    """Recompute and context-bind immutable reduction facts before handoff."""

    validated_contract = _validated_confirmed_contract(contract, confirmation)
    if type(value) is not RoutingCampaignReductionResultV1:
        _reject(
            RoutingQualificationValidationCode.WRONG_TYPE,
            "A typed routing campaign reduction result is required.",
            "result",
        )
    revalidated = _validate_result_semantics(
        _revalidate_typed_model(
            value,
            RoutingCampaignReductionResultV1,
            label="routing campaign reduction result",
        )
    )
    expected = reduce_routing_campaign(validated_contract, confirmation, evidence)
    if revalidated != expected:
        _reject(
            RoutingQualificationValidationCode.SEMANTIC_INCONSISTENCY,
            "Routing campaign reduction result does not match its contract-bound evidence recomputation.",
            "result",
        )
    return revalidated


def serialize_routing_campaign_reduction_result(
    contract: POCContract,
    confirmation: ContractConfirmation,
    evidence: (
        RoutingCampaignEvidenceBundleV1
        | RoutingCampaignRunEvidenceV1
        | bytes
        | Mapping[str, Any]
        | Sequence[RoutingCampaignRunEvidenceV1 | bytes | Mapping[str, Any]]
    ),
    value: RoutingCampaignReductionResultV1,
) -> bytes:
    """Return canonical bytes for immutable ExitSpec-owned reduction facts."""

    validated = validate_routing_campaign_reduction_result(
        contract, confirmation, evidence, value
    )
    content = canonical_json_bytes(validated.model_dump(mode="json"))
    parsed = _revalidate_typed_model(
        RoutingCampaignReductionResultV1.model_validate_json(content, strict=True),
        RoutingCampaignReductionResultV1,
        label="routing campaign reduction result",
    )
    _validate_result_semantics(parsed)
    if parsed != validated:
        _reject(
            RoutingQualificationValidationCode.SEMANTIC_INCONSISTENCY,
            "Routing campaign reduction result changed during serialization.",
        )
    return content


__all__ = [
    "ROUTING_CAMPAIGN_EVIDENCE_BUNDLE_SCHEMA_VERSION",
    "ROUTING_CAMPAIGN_EVIDENCE_CANONICALIZATION_VERSION",
    "ROUTING_CAMPAIGN_EVIDENCE_HASH_VERSION",
    "ROUTING_CAMPAIGN_EVIDENCE_PROTOCOL_ID",
    "ROUTING_CAMPAIGN_EVIDENCE_SCHEMA_VERSION",
    "ROUTING_CAMPAIGN_REDUCER_ID",
    "ROUTING_CAMPAIGN_REDUCER_VERSION",
    "ROUTING_CAMPAIGN_REDUCTION_CANONICALIZATION_VERSION",
    "ROUTING_CAMPAIGN_REDUCTION_HASH_VERSION",
    "ROUTING_CAMPAIGN_REDUCTION_PROTOCOL_ID",
    "ROUTING_CAMPAIGN_REDUCTION_SCHEMA_VERSION",
    "ROUTING_CAMPAIGN_RESULT_SCHEMA_VERSION",
    "RoutingCampaignCacheResetEvidenceV1",
    "RoutingCampaignEvidenceBundleV1",
    "RoutingCampaignIngestionRejected",
    "RoutingCampaignPolicyEvaluationResultV1",
    "RoutingCampaignProducerV1",
    "RoutingCampaignReductionResultV1",
    "RoutingCampaignRouteDecisionReceiptV1",
    "RoutingCampaignRunEvidenceV1",
    "RoutingCampaignRunEvaluationResultV1",
    "RoutingCampaignServingEvidenceV1",
    "RoutingCampaignTelemetryCapsuleV1",
    "RoutingCampaignValidationCode",
    "parse_routing_campaign_contract",
    "parse_routing_campaign_confirmation",
    "parse_routing_campaign_evidence",
    "parse_routing_campaign_run_evidence",
    "reduce_routing_campaign",
    "routing_campaign_contract_digest",
    "serialize_routing_campaign_contract",
    "serialize_routing_campaign_confirmation",
    "serialize_routing_campaign_evidence",
    "serialize_routing_campaign_reduction_result",
    "serialize_routing_campaign_run_evidence",
    "validate_routing_campaign_contract",
    "validate_routing_campaign_reduction_result",
    "validate_routing_campaign_run_evidence",
]
