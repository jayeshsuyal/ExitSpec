"""Strict domain models for ExitSpec's trusted decision boundary."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
import hashlib
import re
from typing import List, Literal, Optional, Tuple, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .canonical import canonical_json_bytes


SHA256_PATTERN = r"^[a-f0-9]{64}$"


class ExitSpecModel(BaseModel):
    """Base model that rejects undocumented fields at the contract boundary."""

    model_config = ConfigDict(extra="forbid")


class FrozenExitSpecModel(ExitSpecModel):
    """Immutable base for every object participating in a contract digest."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class ContractStatus(str, Enum):
    DRAFT = "DRAFT"
    IN_REVIEW = "IN_REVIEW"
    APPROVED = "APPROVED"
    FROZEN = "FROZEN"
    SUPERSEDED = "SUPERSEDED"


class RunStatus(str, Enum):
    QUEUED = "QUEUED"
    VALIDATING = "VALIDATING"
    RUNNING = "RUNNING"
    AGGREGATING = "AGGREGATING"
    COMPLETED = "COMPLETED"
    BLOCKED = "BLOCKED"
    FAILED_INTERNAL = "FAILED_INTERNAL"
    CANCELLED = "CANCELLED"


class VerdictStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    BLOCKED = "BLOCKED"
    NOT_PROVEN = "NOT_PROVEN"


class DraftStatus(str, Enum):
    """Review state for a candidate criterion, before it becomes contract input."""

    NEEDS_REVIEW = "NEEDS_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class ReviewDecision(str, Enum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"


class ComparisonOperator(str, Enum):
    GTE = "gte"
    LTE = "lte"
    GT = "gt"
    LT = "lt"
    EQ = "eq"


class Metric(str, Enum):
    EXACT_TOOL_SELECTION_RATE = "exact_tool_selection_rate"


class ConfidenceMethod(str, Enum):
    WILSON_TWO_SIDED_LOWER_BOUND = "wilson_two_sided_lower_bound"


class SourceReference(FrozenExitSpecModel):
    speaker: str = Field(min_length=1)
    quote: str = Field(min_length=1)
    location: str = Field(min_length=1)


class TranscriptLine(ExitSpecModel):
    """One normalized line of discovery text retained for source review."""

    line_number: int = Field(gt=0)
    speaker: str = Field(min_length=1)
    text: str = Field(min_length=1)


class DiscoveryTranscript(ExitSpecModel):
    """A bounded discovery source; it is authoring input, not verdict evidence."""

    id: str = Field(pattern=r"^[a-z][a-z0-9-]{2,63}$")
    title: str = Field(min_length=1)
    synthetic: bool
    lines: List[TranscriptLine] = Field(min_length=1)

    @field_validator("lines")
    @classmethod
    def require_contiguous_line_numbers(
        cls, lines: List[TranscriptLine]
    ) -> List[TranscriptLine]:
        expected = list(range(1, len(lines) + 1))
        actual = [line.line_number for line in lines]
        if actual != expected:
            raise ValueError(
                "Transcript line numbers must be contiguous and start at 1."
            )
        return lines


class TranscriptSpan(ExitSpecModel):
    """A quote anchored to a specific speaker and line range in a transcript."""

    transcript_id: str = Field(pattern=r"^[a-z][a-z0-9-]{2,63}$")
    start_line: int = Field(gt=0)
    end_line: int = Field(gt=0)
    speaker: str = Field(min_length=1)
    quote: str = Field(min_length=1)

    @model_validator(mode="after")
    def require_valid_line_range(self) -> "TranscriptSpan":
        if self.end_line < self.start_line:
            raise ValueError("Transcript span end_line cannot precede start_line.")
        return self

    def to_source_reference(self) -> SourceReference:
        line_range = (
            str(self.start_line)
            if self.start_line == self.end_line
            else "{0}-{1}".format(self.start_line, self.end_line)
        )
        return SourceReference(
            speaker=self.speaker,
            quote=self.quote,
            location="{0}:{1}".format(self.transcript_id, line_range),
        )


class WorkloadReference(FrozenExitSpecModel):
    fixture_path: str = Field(min_length=1)
    sha256: str = Field(pattern=SHA256_PATTERN)


class TargetSystem(FrozenExitSpecModel):
    provider: str = Field(min_length=1)
    endpoint_class: str = Field(min_length=1)
    model: str = Field(min_length=1)


class ProportionRule(FrozenExitSpecModel):
    operator: ComparisonOperator = ComparisonOperator.GTE
    threshold: float = Field(ge=0.0, le=1.0)
    minimum_samples: int = Field(gt=0)
    confidence_level: float = Field(default=0.95, gt=0.0, lt=1.0)
    confidence_method: ConfidenceMethod = ConfidenceMethod.WILSON_TWO_SIDED_LOWER_BOUND

    @model_validator(mode="after")
    def require_supported_comparison(self) -> "ProportionRule":
        if self.operator != ComparisonOperator.GTE:
            raise ValueError(
                "Brick 1 supports only a greater-than-or-equal proportion rule."
            )
        return self


class Criterion(FrozenExitSpecModel):
    id: str = Field(pattern=r"^[A-Z][A-Z0-9-]{2,63}$")
    title: str = Field(min_length=1)
    must_have: bool = True
    source: Optional[SourceReference] = None
    human_added: bool = False
    normalized_claim: str = Field(min_length=1)
    metric: Metric
    unit: str = Field(min_length=1)
    aggregation: str = Field(min_length=1)
    rule: ProportionRule
    workload_slice: str = Field(min_length=1)
    adapter: str = Field(min_length=1)
    adapter_version: str = Field(min_length=1)
    owner: str = Field(min_length=1)
    evidence_policy: str = Field(min_length=1)
    approved: bool = False

    @model_validator(mode="after")
    def require_traceable_origin(self) -> "Criterion":
        if self.source is None and not self.human_added:
            raise ValueError(
                "A criterion needs a source reference or must be explicitly human-added."
            )
        return self


class TTFTP95Rule(FrozenExitSpecModel):
    """The required client-observed time-to-first-token acceptance rule."""

    metric: Literal["time_to_first_token"]
    aggregation: Literal["p95"]
    unit: Literal["milliseconds"]
    operator: Literal["lt", "lte"]
    threshold: float = Field(gt=0.0, le=60_000.0, allow_inf_nan=False)
    method: Literal["nearest_rank"]
    minimum_successful_samples: int = Field(gt=0, le=1_000)
    must_pass: Literal[True]


class ErrorRateRule(FrozenExitSpecModel):
    """The required attempted-request error-rate acceptance rule."""

    metric: Literal["error_rate"]
    aggregation: Literal["rate"]
    unit: Literal["proportion"]
    operator: Literal["lt"]
    threshold: float = Field(gt=0.0, lt=1.0, allow_inf_nan=False)
    method: Literal["failed_attempts_over_total_attempts"]
    minimum_attempts: int = Field(gt=0, le=1_000)
    must_pass: Literal[True]


class MeasuredPopulationPolicyV1(FrozenExitSpecModel):
    """The exact request population eligible for one performance decision."""

    phases: Tuple[Literal["MEASURED"], ...] = Field(min_length=1, max_length=1)
    exact_attempts: int = Field(gt=0, le=1_000)
    warmups_included: Literal[False]
    preflight_included: Literal[False]
    retries: Literal[0]


class LatencyPopulationPolicyV1(FrozenExitSpecModel):
    """The frozen subset permitted to contribute values to latency percentiles."""

    population: Literal["successful_measured_attempts_with_valid_ttft"]
    failed_attempts: Literal["excluded_from_latency_counted_in_reliability"]


class ReliabilityPopulationPolicyV1(FrozenExitSpecModel):
    """The frozen numerator and denominator for attempted-request reliability."""

    numerator: Literal["external_error_outcomes"]
    denominator: Literal["all_measured_attempts"]
    outcomes: Tuple[
        Literal[
            "HTTP_ERROR",
            "TIMEOUT",
            "PROTOCOL_ERROR",
            "TRANSPORT_ERROR",
        ],
        ...,
    ] = Field(min_length=4, max_length=4)

    @model_validator(mode="after")
    def require_canonical_external_error_order(
        self,
    ) -> "ReliabilityPopulationPolicyV1":
        if self.outcomes != (
            "HTTP_ERROR",
            "TIMEOUT",
            "PROTOCOL_ERROR",
            "TRANSPORT_ERROR",
        ):
            raise ValueError(
                "Reliability outcomes must contain the canonical external-error set."
            )
        return self


class InvalidEvidencePolicyV1(FrozenExitSpecModel):
    """Run-level conditions that invalidate proof instead of changing a rate."""

    terminal_outcomes: Tuple[
        Literal["CANCELLED", "INTERNAL_ERROR"],
        ...,
    ] = Field(min_length=2, max_length=2)
    record_conditions: Tuple[
        Literal[
            "MISSING_RECORD",
            "DUPLICATE_RECORD",
            "EXTRA_RECORD",
        ],
        ...,
    ] = Field(min_length=3, max_length=3)
    integrity_mismatch: Literal["NOT_PROVEN"]
    disposition: Literal["NOT_PROVEN"]

    @model_validator(mode="after")
    def require_canonical_invalid_evidence_order(
        self,
    ) -> "InvalidEvidencePolicyV1":
        if self.terminal_outcomes != ("CANCELLED", "INTERNAL_ERROR"):
            raise ValueError("Invalid terminal outcomes must use the canonical order.")
        if self.record_conditions != (
            "MISSING_RECORD",
            "DUPLICATE_RECORD",
            "EXTRA_RECORD",
        ):
            raise ValueError("Invalid record conditions must use the canonical order.")
        return self


class MeasurementPopulationPolicyV1(FrozenExitSpecModel):
    """Customer-confirmed counting semantics for inference performance v2."""

    schema_version: Literal["exitspec.measurement-population.v1"]
    calculation_version: Literal["exitspec.performance-verdicts.v2"]
    measured_population: MeasuredPopulationPolicyV1
    latency_population: LatencyPopulationPolicyV1
    reliability: ReliabilityPopulationPolicyV1
    invalid_evidence: InvalidEvidencePolicyV1


class InferencePerformanceCriterion(FrozenExitSpecModel):
    """One must-have criterion composed of non-compensating performance rules."""

    criterion_type: Literal["inference_performance_v1"]
    id: str = Field(pattern=r"^[A-Z][A-Z0-9-]{2,63}$")
    title: str = Field(min_length=1)
    must_have: Literal[True] = True
    source: Optional[SourceReference] = None
    human_added: bool = False
    normalized_claim: str = Field(min_length=1)
    ttft_p95: TTFTP95Rule
    error_rate: ErrorRateRule
    workload_slice: str = Field(min_length=1)
    adapter: str = Field(min_length=1)
    adapter_version: str = Field(min_length=1)
    owner: str = Field(min_length=1)
    evidence_policy: str = Field(min_length=1)
    approved: bool = False

    @model_validator(mode="after")
    def require_traceable_origin(self) -> "InferencePerformanceCriterion":
        if self.source is None and not self.human_added:
            raise ValueError(
                "A criterion needs a source reference or must be explicitly human-added."
            )
        if self.ttft_p95.minimum_successful_samples > self.error_rate.minimum_attempts:
            raise ValueError(
                "TTFT successful samples cannot exceed total attempted samples."
            )
        return self


class InferencePerformanceCriterionV2(InferencePerformanceCriterion):
    """A performance criterion with a hash-bound measurement population."""

    criterion_type: Literal["inference_performance_v2"]
    measurement_policy: MeasurementPopulationPolicyV1

    @model_validator(mode="after")
    def bind_population_to_rules(self) -> "InferencePerformanceCriterionV2":
        if (
            self.measurement_policy.measured_population.exact_attempts
            != self.error_rate.minimum_attempts
        ):
            raise ValueError(
                "Measurement policy attempts must match the reliability rule."
            )
        return self


class InferdromeEvidenceIdentityV1(FrozenExitSpecModel):
    """Complete supported identity for one external managed-vLLM evidence slice."""

    schema_version: Literal["exitspec.inferdrome-evidence-identity.v1"]
    evidence_schema_version: Literal["inferdrome.evidence.v1"]
    producer_name: Literal["vllm"]
    producer_version: Literal["0.26.0"]
    adapter_id: Literal["vllm_bench_serve"]
    adapter_version: Literal["1.0.0"]
    native_schema_fingerprint: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    managed_profile_id: Literal["inferdrome.managed-vllm-0.26-evidence-profile.v1"]
    managed_profile_sha256: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    local_gpu_proof_schema_id: Literal["urn:inferdrome:local-gpu-proof:v1"]
    local_gpu_proof_schema_sha256: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    request_plan_digest: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    workload_digest: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    target_model: str = Field(min_length=1)
    target_model_revision: str = Field(pattern=r"^[a-f0-9]{40}$")
    target_tokenizer_revision: str = Field(pattern=r"^[a-f0-9]{40}$")
    target_endpoint: str = Field(pattern=r"^http://127\.0\.0\.1:[0-9]{1,5}/$")
    configured_max_concurrency: int = Field(gt=0, le=1_000)
    exact_measured_attempts: int = Field(gt=0, le=1_000)
    warmup_requests: int = Field(ge=0, le=1_000)
    binding_mode: Literal["EXTERNAL_RECEIPT_BINDING"]
    chronology: Literal["RETROSPECTIVE"]
    producer_contract_link: Literal["ABSENT"]

    @field_validator("target_endpoint")
    @classmethod
    def require_valid_loopback_port(cls, value: str) -> str:
        port_text = value.removeprefix("http://127.0.0.1:").removesuffix("/")
        if not port_text.isdecimal() or not 1 <= int(port_text) <= 65_535:
            raise ValueError("External target endpoint requires a valid loopback port.")
        return value


class ExternalTTFTP95RuleV1(FrozenExitSpecModel):
    """One exact external TTFT observation identity and integer threshold."""

    metric: Literal["time_to_first_token"]
    definition_id: Literal[
        "vllm_first_choices_event_v0_26",
        "first_nonempty_choices_delta_content_v1",
    ]
    aggregation: Literal["p95"]
    unit: Literal["nanoseconds"]
    operator: Literal["lt"]
    threshold_ns: int = Field(gt=0, le=60_000_000_000)
    reducer_id: Literal["nearest_rank_v1"]
    population: Literal["successful_measured_requests_with_observed_ttft"]
    minimum_successful_samples: int = Field(gt=0, le=1_000)
    must_pass: Literal[True]


class ExternalErrorRateRuleV1(FrozenExitSpecModel):
    """Strict error ratio over the complete measured attempt population."""

    metric: Literal["error_rate"]
    aggregation: Literal["rate"]
    operator: Literal["lt"]
    threshold_basis_points: int = Field(gt=0, lt=10_000)
    numerator: Literal["failed_or_anomalous_native_measured_requests"]
    denominator: Literal["all_measured_requests"]
    exact_attempts: int = Field(gt=0, le=1_000)
    must_pass: Literal[True]


class InferencePerformanceCriterionV3(FrozenExitSpecModel):
    """Hash-bound native metric criterion for retrospective external evidence."""

    criterion_type: Literal["inference_performance_v3"]
    id: str = Field(pattern=r"^[A-Z][A-Z0-9-]{2,63}$")
    title: str = Field(min_length=1)
    must_have: Literal[True] = True
    source: Optional[SourceReference] = None
    human_added: bool = False
    normalized_claim: str = Field(min_length=1)
    ttft_p95: ExternalTTFTP95RuleV1
    error_rate: ExternalErrorRateRuleV1
    evidence_identity: InferdromeEvidenceIdentityV1
    concurrency_semantics: Literal[
        "configured_maximum_concurrency_not_observed_overlap"
    ]
    owner: str = Field(min_length=1)
    evidence_policy: str = Field(min_length=1)
    approved: bool = False

    @model_validator(mode="after")
    def require_traceable_and_exact_population(
        self,
    ) -> "InferencePerformanceCriterionV3":
        if self.source is None and not self.human_added:
            raise ValueError(
                "A criterion needs a source reference or must be explicitly human-added."
            )
        if (
            self.evidence_identity.exact_measured_attempts
            != self.error_rate.exact_attempts
            or self.ttft_p95.minimum_successful_samples > self.error_rate.exact_attempts
        ):
            raise ValueError(
                "External latency and reliability populations must share one exact run."
            )
        return self


class ProspectiveCanonicalizationBindingV1(FrozenExitSpecModel):
    """Pinned serialization, hashing, and producer-link derivation rules."""

    canonicalization_scheme_id: Literal["rfc8785_jcs_v1"]
    canonical_bytes_encoding: Literal["utf-8_rfc8785_jcs"]
    hash_algorithm_id: Literal["sha256_v1"]
    hash_encoding_id: Literal["lowercase_hex_without_prefix"]
    link_derivation_policy_id: Literal[
        "exitspec.producer_link.sha256_canonical_hash.v1"
    ]
    link_derivation_input: Literal["bare_canonical_hash"]
    link_derivation_operation: Literal["prefix_sha256_no_second_hash"]


class ProspectiveTrafficPolicyV1(FrozenExitSpecModel):
    """The fixed run-independent traffic identity for Inferdrome P1."""

    schema_version: Literal["exitspec.inferdrome-traffic.v1"]
    policy_id: Literal["inferdrome.concurrent.vllm.v1"]
    kind: Literal["concurrent"]
    configured_concurrency: Literal[4]
    warmup_requests: Literal[10]
    measured_requests: Literal[100]


class ProspectiveSamplingPolicyV1(FrozenExitSpecModel):
    """The fixed deterministic sampling identity for Inferdrome P1."""

    schema_version: Literal["exitspec.inferdrome-sampling.v1"]
    policy_id: Literal["inferdrome.qwen2.5-deterministic.v1"]
    prompt_content_policy: Literal["include"]
    requested_output_tokens: Literal[32]
    temperature: Literal[0]
    seed: Literal[42]


class ProspectiveReliabilityPopulationV1(FrozenExitSpecModel):
    """The existing strict reliability population, repeated additively."""

    schema_version: Literal["exitspec.inferdrome-reliability-population.v1"]
    population_id: Literal["exitspec.inferdrome-reliability.v1"]
    operator: Literal["lt"]
    threshold_basis_points: Literal[100]
    numerator: Literal["failed_or_anomalous_native_measured_requests"]
    denominator: Literal["all_measured_requests"]
    exact_attempts: Literal[100]


class InferdromeEvidenceIdentityV2(FrozenExitSpecModel):
    """Run-independent managed evidence identity for prospective handoff.

    This identity intentionally has no request-plan, bundle, observed
    measurement, observed run identity, or producer-link value.  The producer link is derived only after the
    customer-confirmed contract has been frozen.
    """

    schema_version: Literal["exitspec.inferdrome-evidence-identity.v2"]
    case_id: Literal[
        "native-p95-under-20ms",
        "native-p95-under-10ms",
        "semantic-first-nonempty-under-20ms",
    ]
    evidence_schema_version: Literal["inferdrome.evidence.v1"]
    sequence_requirement: Literal["OPERATOR_MUST_FREEZE_BEFORE_MEASUREMENT"]
    chronology_assurance: Literal["UNAVAILABLE"]
    producer_name: Literal["vllm"]
    producer_version: Literal["0.26.0"]
    adapter_id: Literal["vllm_bench_serve"]
    adapter_version: Literal["1.0.0"]
    native_schema_fingerprint: Literal[
        "sha256:3a4fdee6fe9b45ce5b42c41fd3bfc6614245a36ecfe6f94de92b59717a136abb"
    ]
    managed_profile_id: Literal["inferdrome.managed-vllm-0.26-evidence-profile.v1"]
    managed_profile_sha256: Literal[
        "sha256:9d03b5d0822ed829ddbfa4c87c75530885b9ad51ee2c0cb7c5e31a075996fe34"
    ]
    local_gpu_proof_schema_id: Literal["urn:inferdrome:local-gpu-proof:v1"]
    local_gpu_proof_schema_sha256: Literal[
        "sha256:cf83bbdea2bba4c30b8f0e2c5f34f34a4077501207881fdbdab021571d665547"
    ]
    target_engine: Literal["vllm"]
    target_engine_version: Literal["0.26.0"]
    target_api: Literal["openai_chat_completions"]
    target_model: Literal["Qwen/Qwen2.5-0.5B-Instruct"]
    target_model_revision: Literal["7ae557604adf67be50417f59c2c2f167def9a775"]
    target_tokenizer_revision: Literal["7ae557604adf67be50417f59c2c2f167def9a775"]
    target_endpoint: Literal["http://127.0.0.1:18080/"]
    workload_id: Literal["inferdrome.qwen2.5-real-gpu-workload.v1"]
    workload_digest: Literal[
        "sha256:22bf3389cc29ee946ae567870d7f8d7b458594224542a796e8990c15b1cfcd63"
    ]
    source_schema_version: Literal["inferdrome.source-experiment.v1"]
    traffic: ProspectiveTrafficPolicyV1
    sampling: ProspectiveSamplingPolicyV1
    execution_mode: Literal["attached_endpoint"]
    max_runtime_seconds: Literal[900]
    max_measured_requests: Literal[100]
    measurement_streaming: Literal[True]
    produced_evidence_metric_definition_id: Literal["vllm_first_choices_event_v0_26"]
    choices_span_definition_id: Literal["last_choices_event_span_v1"]
    metric_definitions_version: Literal["1.0.0"]
    reducer_version: Literal["1.0.0"]
    native_output_sensitivity: Literal["RESPONSE_CONTENT"]
    canonical_response_content: Literal["omit"]
    include_request_plan: Literal[True]
    expected_execution_fingerprint: Literal[
        "sha256:76d984ea57a0e7cb00520255a6e362f22885d713a875195a7397771937060edd"
    ]
    requested_criterion_metric_definition_id: Literal[
        "vllm_first_choices_event_v0_26",
        "first_nonempty_choices_delta_content_v1",
    ]
    run_aggregation_policy: Literal["independent_single_run_no_pooling"]
    reducer_id: Literal["nearest_rank_v1"]
    latency_population: Literal["successful_measured_requests_with_observed_ttft"]
    reliability_population: ProspectiveReliabilityPopulationV1
    claims_assurance: Literal["INTERNAL_CONSISTENCY_ONLY"]
    canonicalization: ProspectiveCanonicalizationBindingV1

    @model_validator(mode="after")
    def require_case_metric_pairing(self) -> "InferdromeEvidenceIdentityV2":
        expected_metric = {
            "native-p95-under-20ms": "vllm_first_choices_event_v0_26",
            "native-p95-under-10ms": "vllm_first_choices_event_v0_26",
            "semantic-first-nonempty-under-20ms": (
                "first_nonempty_choices_delta_content_v1"
            ),
        }[self.case_id]
        if self.requested_criterion_metric_definition_id != expected_metric:
            raise ValueError("Prospective case and metric definition disagree.")
        return self


class ProspectiveTTFTP95RuleV2(FrozenExitSpecModel):
    """Versioned prospective TTFT semantics with strict integer thresholds."""

    schema_version: Literal["exitspec.inferdrome-ttft-p95.v2"]
    metric: Literal["time_to_first_token"]
    definition_id: Literal[
        "vllm_first_choices_event_v0_26",
        "first_nonempty_choices_delta_content_v1",
    ]
    aggregation: Literal["p95"]
    unit: Literal["nanoseconds"]
    operator: Literal["lt"]
    threshold_ns: Literal[10_000_000, 20_000_000]
    reducer_id: Literal["nearest_rank_v1"]
    population: Literal["successful_measured_requests_with_observed_ttft"]
    minimum_successful_samples: Literal[100]
    equality_outcome: Literal["FAIL"]
    must_pass: Literal[True]


class InferencePerformanceCriterionV4(FrozenExitSpecModel):
    """Customer-confirmed, run-independent prospective Inferdrome criterion."""

    criterion_type: Literal["inference_performance_v4"]
    id: str = Field(pattern=r"^[A-Z][A-Z0-9-]{2,63}$")
    title: str = Field(min_length=1)
    must_have: Literal[True] = True
    source: Optional[SourceReference] = None
    human_added: bool = False
    normalized_claim: str = Field(min_length=1)
    case_id: Literal[
        "native-p95-under-20ms",
        "native-p95-under-10ms",
        "semantic-first-nonempty-under-20ms",
    ]
    ttft_p95: ProspectiveTTFTP95RuleV2
    error_rate: ExternalErrorRateRuleV1
    evidence_identity: InferdromeEvidenceIdentityV2
    concurrency_semantics: Literal[
        "configured_maximum_concurrency_not_observed_overlap"
    ]
    owner: str = Field(min_length=1)
    evidence_policy: str = Field(min_length=1)
    approved: bool = False

    @model_validator(mode="after")
    def require_run_independent_exact_case(self) -> "InferencePerformanceCriterionV4":
        if self.source is None and not self.human_added:
            raise ValueError(
                "A criterion needs a source reference or must be explicitly human-added."
            )
        if self.case_id != self.evidence_identity.case_id:
            raise ValueError("Prospective criterion and evidence identity disagree.")
        expected = {
            "native-p95-under-20ms": (
                "vllm_first_choices_event_v0_26",
                20_000_000,
            ),
            "native-p95-under-10ms": (
                "vllm_first_choices_event_v0_26",
                10_000_000,
            ),
            "semantic-first-nonempty-under-20ms": (
                "first_nonempty_choices_delta_content_v1",
                20_000_000,
            ),
        }[self.case_id]
        if (
            self.ttft_p95.definition_id,
            self.ttft_p95.threshold_ns,
        ) != expected:
            raise ValueError("Prospective case metric or threshold is invalid.")
        if (
            self.error_rate.threshold_basis_points != 100
            or self.error_rate.exact_attempts != 100
        ):
            raise ValueError(
                "Prospective reliability must retain the exact strict 1% rule."
            )
        return self


class ManagedTTFTEvidenceIdentityV1(FrozenExitSpecModel):
    """Run-independent managed target identity retained for the A6 handoff."""

    schema_version: Literal["exitspec.managed-ttft-evidence-identity.v1"] = (
        "exitspec.managed-ttft-evidence-identity.v1"
    )
    evidence_schema_version: Literal["inferdrome.evidence.v1"]
    sequence_requirement: Literal["OPERATOR_MUST_FREEZE_BEFORE_MEASUREMENT"]
    chronology_assurance: Literal["UNAVAILABLE"]
    producer_name: Literal["vllm"]
    producer_version: Literal["0.26.0"]
    adapter_id: Literal["vllm_bench_serve"]
    adapter_version: Literal["1.0.0"]
    native_schema_fingerprint: Literal[
        "sha256:3a4fdee6fe9b45ce5b42c41fd3bfc6614245a36ecfe6f94de92b59717a136abb"
    ]
    managed_profile_id: Literal["inferdrome.managed-vllm-0.26-evidence-profile.v1"]
    managed_profile_sha256: Literal[
        "sha256:9d03b5d0822ed829ddbfa4c87c75530885b9ad51ee2c0cb7c5e31a075996fe34"
    ]
    local_gpu_proof_schema_id: Literal["urn:inferdrome:local-gpu-proof:v1"]
    local_gpu_proof_schema_sha256: Literal[
        "sha256:cf83bbdea2bba4c30b8f0e2c5f34f34a4077501207881fdbdab021571d665547"
    ]
    target_engine: Literal["vllm"]
    target_engine_version: Literal["0.26.0"]
    target_api: Literal["openai_chat_completions"]
    target_model: Literal["Qwen/Qwen2.5-0.5B-Instruct"]
    target_model_revision: Literal["7ae557604adf67be50417f59c2c2f167def9a775"]
    target_tokenizer_revision: Literal["7ae557604adf67be50417f59c2c2f167def9a775"]
    target_endpoint: Literal["http://127.0.0.1:18080/"]
    workload_id: Literal["inferdrome.qwen2.5-real-gpu-workload.v1"]
    workload_digest: Literal[
        "sha256:22bf3389cc29ee946ae567870d7f8d7b458594224542a796e8990c15b1cfcd63"
    ]
    source_schema_version: Literal["inferdrome.source-experiment.v1"]
    traffic: ProspectiveTrafficPolicyV1
    sampling: ProspectiveSamplingPolicyV1
    execution_mode: Literal["attached_endpoint"]
    max_runtime_seconds: Literal[900]
    max_measured_requests: Literal[100]
    measurement_streaming: Literal[True]
    produced_evidence_metric_definition_id: Literal["vllm_first_choices_event_v0_26"]
    choices_span_definition_id: Literal["last_choices_event_span_v1"]
    metric_definitions_version: Literal["1.0.0"]
    reducer_version: Literal["1.0.0"]
    native_output_sensitivity: Literal["RESPONSE_CONTENT"]
    canonical_response_content: Literal["omit"]
    include_request_plan: Literal[True]
    expected_execution_fingerprint: Literal[
        "sha256:76d984ea57a0e7cb00520255a6e362f22885d713a875195a7397771937060edd"
    ]
    requested_criterion_metric_definition_id: Literal["vllm_first_choices_event_v0_26"]
    run_aggregation_policy: Literal["independent_single_run_no_pooling"]
    reducer_id: Literal["nearest_rank_v1"]
    latency_population: Literal["successful_measured_requests_with_observed_ttft"]
    reliability_population: ProspectiveReliabilityPopulationV1
    claims_assurance: Literal["INTERNAL_CONSISTENCY_ONLY"]
    canonicalization: ProspectiveCanonicalizationBindingV1


class ExactToolSelectionEvidencePolicy(FrozenExitSpecModel):
    """The immutable A6 handoff policy for the server-owned tool probe."""

    schema_version: Literal["exitspec.capability-evidence-policy.v1"] = (
        "exitspec.capability-evidence-policy.v1"
    )
    policy_id: Literal["support-tool-selection-v1"]
    capability_key: Literal["exact_tool_selection"]
    rule: Literal["exact_tool_selection_rate"]
    operator: Literal["GTE"]
    threshold: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    unit: Literal["PROPORTION"]
    measurement_population: Literal["approved_synthetic_cases"]
    evidence_method: Literal["EXIT_SPEC_STREAMING_PROBE"]
    workload_path: Literal["examples/support-agent/fixtures/tool-selection-200.json"]
    workload_sha256: str = Field(
        pattern="^75ef6f83450de100a920e9489a0b5966464f1dba2e3d339c4b57e64fb95d8271$"
    )
    workload_slice: Literal["support-tool-selection-v1"]
    minimum_samples: Literal[200]
    confidence_level: Literal[0.95]
    confidence_method: Literal["wilson_two_sided_lower_bound"]
    calculator_id: Literal["exitspec.statistics.wilson_lower_bound"]
    calculator_version: Literal["wilson-two-sided-v1"]
    adapter: Literal["deterministic_tool_selection"]
    adapter_version: Literal["1.0.0"]
    verifier_id: Literal["exitspec.verdicts.evaluate_proportion_criterion"]
    reducer_id: Literal["exitspec.verdicts.aggregate_overall_verdict"]


class ManagedTTFTEvidencePolicy(FrozenExitSpecModel):
    """The immutable A6 handoff policy for managed TTFT evidence import."""

    schema_version: Literal["exitspec.capability-evidence-policy.v1"] = (
        "exitspec.capability-evidence-policy.v1"
    )
    policy_id: Literal["inferdrome.managed-vllm-0.26-evidence-profile.v1"]
    capability_key: Literal["inference_performance_external"]
    rule: Literal["ttft_p95"]
    operator: Literal["LT"]
    threshold: float = Field(gt=0.0, allow_inf_nan=False)
    unit: Literal["MILLISECONDS"]
    measurement_population: Literal["successful_measured_requests_with_observed_ttft"]
    evidence_method: Literal["EXTERNAL_EVIDENCE_BUNDLE"]
    workload_id: Literal["inferdrome.qwen2.5-real-gpu-workload.v1"]
    workload_digest: Literal[
        "sha256:22bf3389cc29ee946ae567870d7f8d7b458594224542a796e8990c15b1cfcd63"
    ]
    profile_id: Literal["inferdrome.managed-vllm-0.26-evidence-profile.v1"]
    profile_digest: Literal[
        "sha256:9d03b5d0822ed829ddbfa4c87c75530885b9ad51ee2c0cb7c5e31a075996fe34"
    ]
    native_metric: Literal["vllm_first_choices_event_v0_26"]
    configured_concurrency: Literal[4]
    warmup_requests: Literal[10]
    attempts: Literal[100]
    minimum_successful_samples: Literal[100]
    sampling_seed: Literal[42]
    sampling_temperature: Literal[0]
    requested_output_tokens: Literal[32]
    reducer_id: Literal["nearest_rank_v1"]
    reducer_version: Literal["1.0.0"]
    aggregation_policy: Literal["independent_single_run_no_pooling"]
    adapter: Literal["vllm_bench_serve"]
    adapter_version: Literal["1.0.0"]
    bundle_verifier_id: Literal["exitspec.inferdrome_bundle.verify_inferdrome_bundle"]
    bundle_verifier_version: Literal["1.0.0"]
    invocation_profile_validator_id: Literal[
        "exitspec.inferdrome_managed_profile.validate_managed_invocation_profile"
    ]
    local_gpu_proof_validator_id: Literal[
        "exitspec.inferdrome_managed_profile.validate_managed_local_gpu_proof"
    ]
    recalculation_id: Literal["exitspec.inferdrome-recalculation.v1"]
    importer_calculation_id: Literal["exitspec.inferdrome-managed-importer.v1"]
    identity: ManagedTTFTEvidenceIdentityV1

    @model_validator(mode="after")
    def validate_identity_matches_policy(self) -> "ManagedTTFTEvidencePolicy":
        identity = self.identity
        if (
            identity.adapter_id != self.adapter
            or identity.adapter_version != self.adapter_version
            or identity.managed_profile_id != self.profile_id
            or identity.managed_profile_sha256 != self.profile_digest
            or identity.workload_id != self.workload_id
            or identity.workload_digest != self.workload_digest
            or identity.produced_evidence_metric_definition_id != self.native_metric
            or identity.requested_criterion_metric_definition_id != self.native_metric
            or identity.run_aggregation_policy != self.aggregation_policy
            or identity.reducer_id != self.reducer_id
            or identity.latency_population != self.measurement_population
            or identity.traffic.configured_concurrency != self.configured_concurrency
            or identity.traffic.warmup_requests != self.warmup_requests
            or identity.traffic.measured_requests != self.attempts
            or identity.max_measured_requests != self.minimum_successful_samples
            or identity.reliability_population.exact_attempts
            != self.minimum_successful_samples
            or identity.sampling.seed != self.sampling_seed
            or identity.sampling.temperature != self.sampling_temperature
            or identity.sampling.requested_output_tokens != self.requested_output_tokens
        ):
            raise ValueError(
                "Managed TTFT evidence identity does not match its policy."
            )
        return self


CapabilityEvidencePolicy = ExactToolSelectionEvidencePolicy | ManagedTTFTEvidencePolicy


def capability_evidence_policy_digest(
    policy: CapabilityEvidencePolicy,
) -> str:
    """Digest policy content without including the digest field itself."""

    return hashlib.sha256(
        b"exitspec-capability-evidence-policy-v1\x00"
        + canonical_json_bytes(policy.model_dump(mode="json"))
    ).hexdigest()


class CapabilityEvidenceBinding(FrozenExitSpecModel):
    """Canonical, immutable evidence policy bound to one supported criterion."""

    schema_version: Literal["exitspec.capability-evidence-binding.v1"] = (
        "exitspec.capability-evidence-binding.v1"
    )
    binding_type: Literal["EXECUTABLE", "EVIDENCE_IMPORT"]
    policy: CapabilityEvidencePolicy
    policy_sha256: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_policy_digest_and_disposition(self) -> "CapabilityEvidenceBinding":
        expected_type = (
            "EXECUTABLE"
            if isinstance(self.policy, ExactToolSelectionEvidencePolicy)
            else "EVIDENCE_IMPORT"
        )
        if self.binding_type != expected_type:
            raise ValueError("Evidence binding type does not match its policy.")
        if self.policy_sha256 != capability_evidence_policy_digest(self.policy):
            raise ValueError(
                "Evidence binding policy digest does not match its content."
            )
        return self


class CapabilityCriterion(FrozenExitSpecModel):
    """Generic A4 capability criterion carried by the A5 agreement boundary.

    A4 deliberately plans capabilities without choosing one of the older
    vertical-specific contract schemas.  This small criterion keeps the
    server-owned plan fields exact while allowing the existing POCContract,
    digest, confirmation, and freeze primitives to remain the lifecycle
    authority.
    """

    criterion_type: Literal["capability_v1"] = "capability_v1"
    schema_version: Literal["exitspec.capability-criterion.v1"] = (
        "exitspec.capability-criterion.v1"
    )
    id: str = Field(pattern=r"^[A-Z][A-Z0-9-]{2,63}$")
    title: str = Field(min_length=1)
    must_have: bool
    source: Optional[SourceReference] = None
    human_added: Literal[False] = False
    normalized_claim: str = Field(min_length=1)
    poc_id: str = Field(pattern=r"^poc_[a-z0-9][a-z0-9_-]{2,63}$")
    capability_key: str = Field(min_length=1, max_length=160)
    planning_scope: Literal["MUST_HAVE", "ADVISORY"]
    planning_disposition: Literal[
        "EXECUTABLE", "EVIDENCE_IMPORT", "CLARIFICATION_REQUIRED", "UNSUPPORTED"
    ]
    provenance: Optional[
        Literal["SOURCE_EXTRACTED", "HUMAN_DECLARED", "ADAPTER_PROFILE_DECLARED"]
    ] = None
    planning_item_id: str = Field(pattern=r"^cpitem_[a-f0-9]{32}$")
    proposal_id: str = Field(pattern=r"^prop_[a-z0-9][a-z0-9_-]{7,95}$")
    proposal_key: str = Field(min_length=1, max_length=160)
    source_receipt_id: str = Field(pattern=r"^srcpt_[a-z0-9][a-z0-9_-]{7,95}$")
    source_id: str = Field(pattern=r"^src_[a-z0-9][a-z0-9_-]{2,63}$")
    source_kind: str = Field(min_length=1, max_length=80)
    source_content_sha256: str = Field(pattern=SHA256_PATTERN)
    source_revision: int = Field(ge=1)
    source_adapter_name: str = Field(min_length=1, max_length=64)
    source_adapter_version: str = Field(min_length=1, max_length=64)
    redaction_policy_version: str = Field(min_length=1, max_length=64)
    authoring_receipt_id: str = Field(min_length=1, max_length=160)
    authoring_result_id: str = Field(min_length=1, max_length=160)
    a4_plan_id: str = Field(pattern=r"^cplan_[a-f0-9]{32}$")
    a4_plan_version: int = Field(ge=1)
    a4_plan_sha256: str = Field(pattern=SHA256_PATTERN)
    planner_reviewer: str = Field(min_length=1, max_length=160)
    planner_rationale: str = Field(min_length=1, max_length=2_000)
    planning_reason: str = Field(min_length=1, max_length=2_000)
    planning_next_action: str = Field(min_length=1, max_length=2_000)
    explicit_exclusion: bool = False
    assembly_reviewer: str = Field(min_length=1, max_length=160)
    assembly_rationale: str = Field(min_length=1, max_length=2_000)
    rule: Optional[str] = Field(default=None, min_length=1)
    operator: Optional[str] = Field(default=None, min_length=1)
    threshold: Optional[float] = Field(default=None, allow_inf_nan=False)
    unit: Optional[str] = Field(default=None, min_length=1)
    measurement_population: Optional[str] = Field(default=None, min_length=1)
    evidence_method: Optional[str] = Field(default=None, min_length=1)
    adapter: Optional[str] = Field(default=None, min_length=1)
    adapter_version: Optional[str] = Field(default=None, min_length=1)
    evidence_profile: Optional[str] = None
    evidence_binding: Optional[CapabilityEvidenceBinding] = None
    execution_available: Literal[False] = False
    owner: str = Field(min_length=1)
    evidence_policy: Optional[str] = Field(default=None, min_length=1)
    approved: bool = False

    @model_validator(mode="after")
    def require_traceable_origin(self) -> "CapabilityCriterion":
        if self.source is None and not self.human_added:
            raise ValueError(
                "A capability criterion needs a source reference or must be explicitly human-added."
            )
        supported = self.planning_disposition in {"EXECUTABLE", "EVIDENCE_IMPORT"}
        if supported:
            required = (
                self.provenance,
                self.rule,
                self.operator,
                self.threshold,
                self.unit,
                self.measurement_population,
                self.evidence_method,
                self.adapter,
                self.adapter_version,
                self.evidence_binding,
            )
            if self.explicit_exclusion or any(value is None for value in required):
                raise ValueError(
                    "A supported capability criterion is incomplete or excluded."
                )
            expected_binding_type = self.planning_disposition
            if (
                self.evidence_binding is None
                or self.evidence_binding.binding_type != expected_binding_type
            ):
                raise ValueError(
                    "A supported capability criterion requires its matching evidence binding."
                )
            policy = self.evidence_binding.policy
            if (
                policy.capability_key != self.capability_key
                or policy.rule != self.rule
                or policy.operator != self.operator
                or policy.threshold != self.threshold
                or policy.unit != self.unit
                or policy.measurement_population != self.measurement_population
                or policy.evidence_method != self.evidence_method
                or policy.adapter != self.adapter
                or policy.adapter_version != self.adapter_version
            ):
                raise ValueError(
                    "Evidence binding does not match the capability criterion."
                )
            if (
                self.planning_disposition == "EVIDENCE_IMPORT"
                and self.evidence_profile is None
            ):
                raise ValueError(
                    "An evidence-import capability criterion requires its profile."
                )
            if self.planning_disposition == "EVIDENCE_IMPORT":
                if (
                    not isinstance(policy, ManagedTTFTEvidencePolicy)
                    or self.evidence_profile != policy.profile_id
                ):
                    raise ValueError(
                        "An evidence-import capability criterion requires its matching profile."
                    )
            if (
                self.planning_disposition == "EXECUTABLE"
                and self.evidence_profile is not None
            ):
                raise ValueError(
                    "An executable capability criterion cannot carry an evidence profile."
                )
        else:
            if self.provenance is not None or any(
                value is not None
                for value in (
                    self.rule,
                    self.operator,
                    self.threshold,
                    self.unit,
                    self.measurement_population,
                    self.evidence_method,
                    self.adapter,
                    self.adapter_version,
                    self.evidence_profile,
                    self.evidence_binding,
                )
            ):
                raise ValueError(
                    "A non-executable capability record carries executable fields."
                )
            if self.explicit_exclusion and self.planning_disposition != "UNSUPPORTED":
                raise ValueError(
                    "Only an unsupported capability record may be explicitly excluded."
                )
        return self


class RoutingPolicyIdentityV1(FrozenExitSpecModel):
    """A run-independent, digest-bound candidate or baseline policy."""

    schema_version: Literal["exitspec.routing-policy-identity.v1"]
    policy_id: str = Field(pattern=r"^[a-z][a-z0-9._-]{2,127}$", max_length=128)
    policy_sha256: str = Field(pattern=SHA256_PATTERN)


class RoutingConfigurationIdentityV1(FrozenExitSpecModel):
    """The exact routing configuration required by a campaign."""

    schema_version: Literal["exitspec.routing-configuration-identity.v1"]
    configuration_id: str = Field(pattern=r"^[a-z][a-z0-9._-]{2,127}$", max_length=128)
    configuration_sha256: str = Field(pattern=SHA256_PATTERN)


class RoutingRequestTraceIdentityV1(FrozenExitSpecModel):
    """The exact request-trace/workload identity frozen before capture."""

    schema_version: Literal["exitspec.routing-request-trace-identity.v1"]
    trace_id: str = Field(pattern=r"^[a-z][a-z0-9._-]{2,127}$", max_length=128)
    trace_sha256: str = Field(pattern=SHA256_PATTERN)


class RoutingCanonicalizationBindingV1(FrozenExitSpecModel):
    """The existing ExitSpec canonical bytes and digest vocabulary."""

    canonicalization_scheme_id: Literal["rfc8785_jcs_v1"]
    canonical_bytes_encoding: Literal["utf-8_rfc8785_jcs"]
    hash_algorithm_id: Literal["sha256_v1"]
    hash_encoding_id: Literal["lowercase_hex_without_prefix"]


class RoutingQualificationOwnershipV1(FrozenExitSpecModel):
    """Explicit authority boundaries for routing qualification."""

    acceptance_owner: Literal["EXIT_SPEC"]
    route_decision_emitter: Literal["ROUTER_OR_CASCADE"]
    evidence_sealer: Literal["EVIDENCE_PRODUCER_OR_INFERDROME"]
    producer_acceptance_authority: Literal["FORBIDDEN"]


class RoutingTrialOrderV1(FrozenExitSpecModel):
    """Deterministic request/trial allocation frozen before capture."""

    schema_version: Literal["exitspec.routing-trial-order.v1"]
    candidate_policy_id: str = Field(
        pattern=r"^[a-z][a-z0-9._-]{2,127}$", max_length=128
    )
    baseline_policy_id: str = Field(
        pattern=r"^[a-z][a-z0-9._-]{2,127}$", max_length=128
    )
    request_trace_binding: Literal["REQUEST_TRACE_INDEX"]
    assignment_rule: Literal["ONE_CANDIDATE_AND_ONE_BASELINE_PER_REQUEST_PER_TRIAL"]
    ordering_rule: Literal["TRIAL_INDEX_ASCENDING_REQUEST_INDEX_ASCENDING_POLICY_ORDER"]
    trial_index_base: Literal[0]
    policy_order: Tuple[Literal["candidate"], Literal["baseline"]]
    trial_count: int = Field(ge=1, le=1_000)
    request_count: int = Field(ge=1, le=100_000)
    total_assignments: int = Field(ge=1, le=200_000_000)

    @model_validator(mode="after")
    def require_complete_deterministic_allocation(self) -> "RoutingTrialOrderV1":
        if self.candidate_policy_id == self.baseline_policy_id:
            raise ValueError("Candidate and baseline policy IDs must be distinct.")
        if self.total_assignments != self.trial_count * self.request_count * 2:
            raise ValueError(
                "Trial allocation total_assignments must equal trials times requests times two."
            )
        return self


class RoutingCacheResetProtocolV1(FrozenExitSpecModel):
    """Cache state and reset boundary that prevent policy contamination."""

    schema_version: Literal["exitspec.routing-cache-reset.v1"]
    intended_start_state: Literal["COLD"]
    reset_boundary: Literal["BEFORE_EACH_TRIAL"]
    reset_scope: Literal["ROUTER_AND_SERVING_ENGINE_STATE"]
    reset_required: Literal[True]
    cross_policy_cache_reuse: Literal[False]


class RoutingFailureInjectionProtocolV1(FrozenExitSpecModel):
    """A digest-bound failure-injection configuration, including none."""

    schema_version: Literal["exitspec.routing-failure-injection.v1"]
    configuration_id: str = Field(pattern=r"^[a-z][a-z0-9._-]{2,127}$", max_length=128)
    configuration_sha256: str = Field(pattern=SHA256_PATTERN)
    posture: Literal["NO_INJECTION"]
    injected_failure_classes: Tuple[()]
    maximum_injected_failures: Literal[0]

    @model_validator(mode="after")
    def require_explicit_no_injection(self) -> "RoutingFailureInjectionProtocolV1":
        if self.injected_failure_classes or self.maximum_injected_failures != 0:
            raise ValueError("NO_INJECTION cannot carry failure classes or failures.")
        return self


class RoutingEnvironmentRequirementV1(FrozenExitSpecModel):
    """An environment identity plus normalized fields future evidence must bind."""

    schema_version: Literal["exitspec.routing-environment-requirement.v1"]
    environment_id: str = Field(pattern=r"^[a-z][a-z0-9._-]{2,127}$", max_length=128)
    environment_sha256: str = Field(pattern=SHA256_PATTERN)
    required_evidence_fields: Tuple[
        Literal[
            "target.engine_version",
            "target.model_revision",
            "target.tokenizer_revision",
            "gpu.model",
            "gpu.count",
            "cuda.version",
            "driver.version",
            "execution.environment_id",
        ],
        ...,
    ] = Field(min_length=8, max_length=8)

    @model_validator(mode="after")
    def require_canonical_environment_fields(
        self,
    ) -> "RoutingEnvironmentRequirementV1":
        expected = (
            "target.engine_version",
            "target.model_revision",
            "target.tokenizer_revision",
            "gpu.model",
            "gpu.count",
            "cuda.version",
            "driver.version",
            "execution.environment_id",
        )
        if self.required_evidence_fields != expected:
            raise ValueError(
                "Required environment evidence fields must use the canonical order."
            )
        return self


class RoutingServingRequirementV1(FrozenExitSpecModel):
    """Provider-neutral serving and execution identity requirements."""

    schema_version: Literal["exitspec.routing-serving-requirement.v1"]
    engine: str = Field(min_length=1, max_length=128)
    engine_version: str = Field(min_length=1, max_length=128)
    model: str = Field(min_length=1, max_length=256)
    model_revision: str = Field(
        pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]{6,127}$", max_length=128
    )
    tokenizer: str = Field(min_length=1, max_length=256)
    tokenizer_revision: str = Field(
        pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]{6,127}$", max_length=128
    )
    quantization: str = Field(min_length=1, max_length=128)
    tensor_parallel_size: int = Field(ge=1, le=1_000)
    execution_environment: RoutingEnvironmentRequirementV1


class RoutingTelemetryPolicyV1(FrozenExitSpecModel):
    """Freshness, identity, and provenance rules for telemetry capsules."""

    schema_version: Literal["exitspec.routing-telemetry-policy.v1"]
    capsule_type: Literal["ROUTING_TELEMETRY_CAPSULE_V1"]
    capsule_identity_field: Literal["telemetry_capsule_id"]
    capsule_digest_field: Literal["telemetry_capsule_sha256"]
    capsule_digest_scope: Literal["CANONICAL_CAPSULE_BYTES"]
    required_provenance_fields: Tuple[
        Literal[
            "telemetry_capsule_id",
            "telemetry_capsule_sha256",
            "run_id",
            "captured_at",
            "producer_id",
            "environment_id",
        ],
        ...,
    ] = Field(min_length=6, max_length=6)
    max_age_seconds: int = Field(ge=0, le=2_147_483_647)
    age_comparison: Literal["OBSERVED_AGE_SECONDS_LE_MAX_AGE_SECONDS"]
    stale_when: Literal["OBSERVED_AGE_SECONDS_GT_MAX_AGE_SECONDS"]

    @model_validator(mode="after")
    def require_canonical_telemetry_provenance(
        self,
    ) -> "RoutingTelemetryPolicyV1":
        expected = (
            "telemetry_capsule_id",
            "telemetry_capsule_sha256",
            "run_id",
            "captured_at",
            "producer_id",
            "environment_id",
        )
        if self.required_provenance_fields != expected:
            raise ValueError(
                "Telemetry provenance fields must use the canonical order."
            )
        return self


class RoutingReceiptProtocolV1(FrozenExitSpecModel):
    """Admissibility vocabulary for sealed route-decision receipts."""

    schema_version: Literal["exitspec.routing-decision-receipt-protocol.v1"]
    receipt_type: Literal["ROUTE_DECISION_RECEIPT_V1"]
    receipt_identity_field: Literal["route_decision_receipt_id"]
    receipt_digest_field: Literal["route_decision_receipt_sha256"]
    required_bindings: Tuple[
        Literal[
            "campaign_contract_sha256",
            "request_id",
            "trial_index",
            "policy_id",
            "routing_configuration_id",
            "routing_configuration_sha256",
        ],
        ...,
    ] = Field(min_length=6, max_length=6)
    required_provenance_fields: Tuple[
        Literal[
            "route_decision_receipt_id",
            "route_decision_receipt_sha256",
            "producer_id",
            "producer_version",
            "captured_at",
            "source_digest",
        ],
        ...,
    ] = Field(min_length=6, max_length=6)
    completeness_expectation: Literal[
        "EXACTLY_ONE_RECEIPT_PER_REQUEST_TRIAL_POLICY_ASSIGNMENT"
    ]
    route_decision_source: Literal["ROUTER_OR_CASCADE"]
    verdict_boundary: Literal["EXIT_SPEC_ONLY"]

    @model_validator(mode="after")
    def require_canonical_receipt_bindings(self) -> "RoutingReceiptProtocolV1":
        expected_bindings = (
            "campaign_contract_sha256",
            "request_id",
            "trial_index",
            "policy_id",
            "routing_configuration_id",
            "routing_configuration_sha256",
        )
        expected_provenance = (
            "route_decision_receipt_id",
            "route_decision_receipt_sha256",
            "producer_id",
            "producer_version",
            "captured_at",
            "source_digest",
        )
        if self.required_bindings != expected_bindings:
            raise ValueError("Route receipt bindings must use the canonical order.")
        if self.required_provenance_fields != expected_provenance:
            raise ValueError(
                "Route receipt provenance fields must use the canonical order."
            )
        return self


class RoutingRunPolicyV1(FrozenExitSpecModel):
    """The B9 default for independent repetitions and no pooled reduction."""

    schema_version: Literal["exitspec.routing-run-policy.v1"]
    run_mode: Literal["INDEPENDENT_RUNS"]
    default_repetitions: int = Field(ge=1, le=100)
    independence_requirement: Literal["EACH_RUN_HAS_A_SEPARATE_RUN_ID"]
    pooling_policy: Literal["FORBIDDEN_UNLESS_FUTURE_FROZEN_CONTRACT_DEFINES_IT"]
    aggregation_policy: Literal["UNDEFINED_IN_B9"]


class RoutingPrivacyPolicyV1(FrozenExitSpecModel):
    """The customer-artifact privacy posture for campaign evidence."""

    schema_version: Literal["exitspec.routing-privacy-policy.v1"]
    credentials: Literal["FORBIDDEN"]
    secrets: Literal["FORBIDDEN"]
    raw_sensitive_customer_content: Literal["FORBIDDEN"]
    allowed_representation: Literal["IDENTITIES_DIGESTS_AND_BOUNDED_METADATA_ONLY"]


class RoutingQualificationCriterionV1(FrozenExitSpecModel):
    """Run-independent B9 routing qualification vocabulary.

    This criterion is intentionally one member of the existing ``POCContract``
    criterion union.  It owns no observed run identity and no acceptance
    verdict; those belong to later evidence and ExitSpec-owned reduction.
    """

    criterion_type: Literal["routing_qualification_v1"]
    protocol_id: Literal["routing_qualification_v1"]
    schema_version: Literal["exitspec.routing-qualification.v1"]
    protocol_version: Literal["1.0.0"]
    id: Literal["routing_qualification_v1"]
    title: str = Field(min_length=1, max_length=256)
    must_have: Literal[True]
    source: Optional[SourceReference]
    human_added: bool
    normalized_claim: str = Field(min_length=1, max_length=2_000)
    owner: str = Field(min_length=1, max_length=160)
    evidence_policy: str = Field(min_length=1, max_length=2_000)
    canonicalization: RoutingCanonicalizationBindingV1
    ownership: RoutingQualificationOwnershipV1
    candidate_policy: RoutingPolicyIdentityV1
    baseline_policy: RoutingPolicyIdentityV1
    routing_configuration: RoutingConfigurationIdentityV1
    request_trace: RoutingRequestTraceIdentityV1
    trial_order: RoutingTrialOrderV1
    cache_reset: RoutingCacheResetProtocolV1
    failure_injection: RoutingFailureInjectionProtocolV1
    serving: RoutingServingRequirementV1
    telemetry: RoutingTelemetryPolicyV1
    route_decision_receipts: RoutingReceiptProtocolV1
    run_policy: RoutingRunPolicyV1
    privacy: RoutingPrivacyPolicyV1
    approved: bool

    @model_validator(mode="after")
    def require_traceability_and_consistent_bindings(
        self,
    ) -> "RoutingQualificationCriterionV1":
        if self.source is None and not self.human_added:
            raise ValueError(
                "A routing qualification criterion needs a source reference or must be explicitly human-added."
            )
        if self.source is not None:
            raise ValueError(
                "Routing qualification contracts cannot carry raw source content."
            )
        if self.candidate_policy.policy_id == self.baseline_policy.policy_id:
            raise ValueError("Candidate and baseline policy IDs must be distinct.")
        if self.candidate_policy.policy_sha256 == self.baseline_policy.policy_sha256:
            raise ValueError("Candidate and baseline policy digests must be distinct.")
        if (
            self.trial_order.candidate_policy_id != self.candidate_policy.policy_id
            or self.trial_order.baseline_policy_id != self.baseline_policy.policy_id
        ):
            raise ValueError(
                "Trial order policy assignment does not match policy identities."
            )
        if self.ownership.acceptance_owner != "EXIT_SPEC":
            raise ValueError("ExitSpec must own routing qualification acceptance.")
        if self.ownership.producer_acceptance_authority != "FORBIDDEN":
            raise ValueError("Evidence producers cannot supply an acceptance verdict.")
        return self


_CANONICAL_PROPORTION_PATTERN = r"^(?:0|1|0\.[0-9]*[1-9])$"


def _require_canonical_proportion(value: str) -> str:
    """Require a bounded, non-exponential decimal string in [0, 1]."""

    if re.fullmatch(_CANONICAL_PROPORTION_PATTERN, value) is None:
        raise ValueError(
            "Proportion values must be canonical decimal strings without exponent notation."
        )
    try:
        decimal = Decimal(value)
    except InvalidOperation as error:
        raise ValueError("Proportion value is not a valid decimal.") from error
    if not 0 <= decimal <= 1:
        raise ValueError("Proportion value must be between zero and one.")
    if -decimal.as_tuple().exponent > 6:
        raise ValueError("Proportion values may contain at most six decimal places.")
    if format(decimal, "f") != value:
        raise ValueError("Proportion value is not in canonical decimal form.")
    return value


class RoutingSLOObservationMetricV1(FrozenExitSpecModel):
    """One concrete provider-neutral observation for a B9 assignment."""

    schema_version: Literal["exitspec.routing-slo-observation-metric.v1"]
    metric_definition_id: Literal["routing_terminal_end_to_end_latency_ns"]
    metric_definition_version: Literal["1.0.0"]
    metric_name: Literal["terminal_end_to_end_latency"]
    unit: Literal["nanoseconds"]
    value_type: Literal["NON_NEGATIVE_INTEGER"]
    comparison_operator: Literal["lte"]
    threshold_ns: int = Field(gt=0, le=60_000_000_000)
    threshold_representation: Literal["CANONICAL_JSON_INTEGER_NANOSECONDS"]
    boundary_semantics: Literal["ATTAINED_WHEN_OBSERVED_LATENCY_NS_LE_THRESHOLD_NS"]
    measurement_scope: Literal["ONE_B9_REQUEST_TRIAL_POLICY_ASSIGNMENT"]
    clock_domain: Literal["MONOTONIC_PER_ASSIGNMENT_CLOCK"]
    start_event: Literal["ASSIGNMENT_DISPATCH_MONOTONIC_START"]
    terminal_event: Literal[
        "FINAL_RESPONSE_OR_EXTERNAL_TERMINAL_OUTCOME_MONOTONIC_STOP"
    ]
    per_assignment_aggregation: Literal["NONE_ONE_OBSERVATION_REQUIRED"]
    successful_case: Literal["VALID_NON_NEGATIVE_LATENCY_IS_COMPARED_TO_THRESHOLD"]
    external_error_case: Literal["NOT_ATTAINED_AND_REMAINS_IN_DENOMINATOR"]
    timeout_case: Literal["NOT_ATTAINED_AND_REMAINS_IN_DENOMINATOR"]
    missing_case: Literal["NOT_PROVEN_AND_REMAINS_IN_DENOMINATOR"]
    invalid_case: Literal["NOT_PROVEN_AND_REMAINS_IN_DENOMINATOR"]
    internal_case: Literal["NOT_PROVEN_AND_REMAINS_IN_DENOMINATOR"]


class RoutingSLOAssignmentRuleV1(FrozenExitSpecModel):
    """One exact per-assignment SLO envelope for one B9 policy subject."""

    schema_version: Literal["exitspec.routing-slo-assignment-rule.v1"]
    subject_policy_role: Literal["candidate", "baseline"]
    subject_policy_id: str = Field(pattern=r"^[a-z][a-z0-9._-]{2,127}$", max_length=128)
    subject_policy_sha256: str = Field(pattern=SHA256_PATTERN)
    required_observations: Tuple[RoutingSLOObservationMetricV1, ...] = Field(
        min_length=1, max_length=1
    )
    outcome_derivation: Literal[
        "EXIT_SPEC_DERIVES_ATTAINED_IF_ALL_REQUIRED_OBSERVATIONS_SATISFY"
    ]
    external_error_treatment: Literal["COUNT_AS_NOT_ATTAINED"]
    timeout_treatment: Literal["COUNT_AS_NOT_ATTAINED"]
    missing_evidence_disposition: Literal["NOT_PROVEN"]
    invalid_evidence_disposition: Literal["NOT_PROVEN"]
    internal_evidence_disposition: Literal["NOT_PROVEN"]
    producer_outcome_authority: Literal["FORBIDDEN_EXIT_SPEC_ONLY"]


class RoutingSLOAttainmentConfidenceV1(FrozenExitSpecModel):
    """Confidence requirements over one subject policy's binary population."""

    schema_version: Literal["exitspec.routing-slo-attainment-confidence.v1"]
    binary_observation: Literal["ATTAINED_OR_NOT_ATTAINED"]
    attained_count_field: Literal["attained_count"]
    not_attained_count_field: Literal["not_attained_count"]
    sample_count_field: Literal["eligible_assignment_count"]
    minimum_sample_count: int = Field(gt=0, le=200_000_000)
    required_attainment_rate: str = Field(
        pattern=_CANONICAL_PROPORTION_PATTERN, max_length=18
    )
    confidence_level: Literal["0.95"]
    confidence_method: Literal["wilson_two_sided_lower_bound"]
    calculator_id: Literal["exitspec.statistics.wilson_lower_bound"]
    calculator_version: Literal["wilson-two-sided-v1"]
    comparison_operator: Literal["gte"]
    comparison_semantics: Literal[
        "WILSON_TWO_SIDED_LOWER_BOUND_GTE_REQUIRED_ATTAINMENT_RATE"
    ]
    point_estimate_sufficiency: Literal["NEVER_SUFFICIENT_ALONE"]
    decimal_representation: Literal[
        "CANONICAL_DECIMAL_STRING_NO_EXPONENT_MAX_6_FRACTION_DIGITS"
    ]

    @field_validator("required_attainment_rate")
    @classmethod
    def require_canonical_decimal(cls, value: str) -> str:
        return _require_canonical_proportion(value)


class RoutingSLOPolicyConfidenceRuleV1(FrozenExitSpecModel):
    """A subject-specific aggregate confidence rule, still pre-measurement."""

    schema_version: Literal["exitspec.routing-slo-policy-confidence-rule.v1"]
    subject_policy_role: Literal["candidate", "baseline"]
    subject_policy_id: str = Field(pattern=r"^[a-z][a-z0-9._-]{2,127}$", max_length=128)
    subject_policy_sha256: str = Field(pattern=SHA256_PATTERN)
    evaluation_role: Literal["QUALIFICATION_GATE", "REFERENCE_CONTROL"]
    eligible_population: Literal[
        "ALL_B9_REQUEST_TRIAL_ASSIGNMENTS_FOR_THIS_SUBJECT_POLICY"
    ]
    denominator: Literal[
        "ALL_ELIGIBLE_ASSIGNMENTS_FOR_THIS_SUBJECT_POLICY_INCLUDING_EXTERNAL_ERRORS_AND_TIMEOUTS"
    ]
    population_subject_binding: Literal["THIS_RULE_SUBJECT_POLICY_ID_AND_SHA256"]
    run_pooling: Literal["INDEPENDENT_B9_RUNS_NOT_POOLED_IN_B10"]
    confidence: RoutingSLOAttainmentConfidenceV1


class RoutingSLOAttainmentCriterionV1(FrozenExitSpecModel):
    """Additive B10 confidence-bearing routing SLO requirements.

    This is a pre-measurement companion criterion. It binds to exactly one
    B9 routing campaign criterion and its policy identities, but contains no
    observed run, measurement, producer verdict, or reduction result.
    """

    criterion_type: Literal["routing_slo_attainment_v1"]
    protocol_id: Literal["routing_slo_attainment_v1"]
    schema_version: Literal["exitspec.routing-slo-attainment.v1"]
    protocol_version: Literal["1.0.0"]
    id: Literal["routing_slo_attainment_v1"]
    title: str = Field(min_length=1, max_length=256)
    must_have: Literal[True]
    source: Optional[SourceReference]
    human_added: bool
    normalized_claim: str = Field(min_length=1, max_length=2_000)
    owner: str = Field(min_length=1, max_length=160)
    evidence_policy: str = Field(min_length=1, max_length=2_000)
    campaign_criterion_id: Literal["routing_qualification_v1"]
    campaign_protocol_id: Literal["routing_qualification_v1"]
    campaign_schema_version: Literal["exitspec.routing-qualification.v1"]
    candidate_policy_id: str = Field(
        pattern=r"^[a-z][a-z0-9._-]{2,127}$", max_length=128
    )
    candidate_policy_sha256: str = Field(pattern=SHA256_PATTERN)
    baseline_policy_id: str = Field(
        pattern=r"^[a-z][a-z0-9._-]{2,127}$", max_length=128
    )
    baseline_policy_sha256: str = Field(pattern=SHA256_PATTERN)
    assignment_slo_envelopes: Tuple[RoutingSLOAssignmentRuleV1, ...] = Field(
        min_length=2, max_length=2
    )
    policy_confidence_rules: Tuple[RoutingSLOPolicyConfidenceRuleV1, ...] = Field(
        min_length=2, max_length=2
    )
    policy_evaluation_roles: Tuple[
        Literal["QUALIFICATION_GATE"], Literal["REFERENCE_CONTROL"]
    ]
    policy_requirement_combination: Literal[
        "QUALIFICATION_GATE_REQUIRED_REFERENCE_CONTROL_CONTEXTUAL"
    ]
    policy_requirement_rationale: Literal[
        "CANDIDATE_IS_CUSTOMER_QUALIFICATION_TARGET_BASELINE_IS_REFERENCE_CONTROL"
    ]
    verdict_boundary: Literal["NO_VERDICT_IN_B10"]
    approved: bool

    @model_validator(mode="after")
    def require_traceability_and_bindings(self) -> "RoutingSLOAttainmentCriterionV1":
        if self.source is None and not self.human_added:
            raise ValueError(
                "A routing SLO criterion needs a source reference or must be explicitly human-added."
            )
        if self.source is not None:
            raise ValueError("Routing SLO contracts cannot carry raw source content.")
        if self.candidate_policy_id == self.baseline_policy_id:
            raise ValueError("Candidate and baseline policy IDs must be distinct.")
        if self.candidate_policy_sha256 == self.baseline_policy_sha256:
            raise ValueError("Candidate and baseline policy digests must be distinct.")
        envelope_roles = tuple(
            envelope.subject_policy_role for envelope in self.assignment_slo_envelopes
        )
        confidence_roles = tuple(
            rule.subject_policy_role for rule in self.policy_confidence_rules
        )
        evaluation_roles = tuple(
            rule.evaluation_role for rule in self.policy_confidence_rules
        )
        if envelope_roles != ("candidate", "baseline"):
            raise ValueError(
                "Assignment SLO envelopes must use canonical candidate-then-baseline order."
            )
        if confidence_roles != ("candidate", "baseline"):
            raise ValueError(
                "Policy confidence rules must use canonical candidate-then-baseline order."
            )
        if self.policy_evaluation_roles != (
            "QUALIFICATION_GATE",
            "REFERENCE_CONTROL",
        ):
            raise ValueError(
                "Policy evaluation roles must use candidate gate then baseline control order."
            )
        if evaluation_roles != self.policy_evaluation_roles:
            raise ValueError(
                "Policy confidence rules must carry their explicit evaluation roles."
            )
        expected = (
            (self.candidate_policy_id, self.candidate_policy_sha256),
            (self.baseline_policy_id, self.baseline_policy_sha256),
        )
        envelope_bindings = tuple(
            (envelope.subject_policy_id, envelope.subject_policy_sha256)
            for envelope in self.assignment_slo_envelopes
        )
        confidence_bindings = tuple(
            (rule.subject_policy_id, rule.subject_policy_sha256)
            for rule in self.policy_confidence_rules
        )
        if envelope_bindings != expected or confidence_bindings != expected:
            raise ValueError("SLO policy identities must match the criterion bindings.")
        return self


ContractCriterion = Union[
    Criterion,
    InferencePerformanceCriterion,
    InferencePerformanceCriterionV2,
    InferencePerformanceCriterionV3,
    InferencePerformanceCriterionV4,
    CapabilityCriterion,
    RoutingQualificationCriterionV1,
    RoutingSLOAttainmentCriterionV1,
]


class CriterionReview(ExitSpecModel):
    """An explicit human decision on a proposed criterion."""

    reviewer: str = Field(min_length=1)
    decision: ReviewDecision
    rationale: str = Field(min_length=1)
    reviewed_at: datetime


class CriterionDraft(ExitSpecModel):
    """A source-linked proposal that must be reviewed before contract assembly."""

    id: str = Field(pattern=r"^[A-Z][A-Z0-9-]{2,63}$")
    status: DraftStatus = DraftStatus.NEEDS_REVIEW
    source_span: Optional[TranscriptSpan] = None
    human_added: bool = False
    human_added_rationale: Optional[str] = None
    normalized_claim: str = Field(min_length=1)
    proposed_criterion: Optional[ContractCriterion] = None
    open_questions: List[str] = Field(default_factory=list)
    review: Optional[CriterionReview] = None

    @field_validator("open_questions")
    @classmethod
    def require_nonblank_open_questions(cls, questions: List[str]) -> List[str]:
        if any(not question.strip() for question in questions):
            raise ValueError("Open questions cannot be blank.")
        return questions

    @model_validator(mode="after")
    def validate_authoring_boundary(self) -> "CriterionDraft":
        if self.source_span is None and not self.human_added:
            raise ValueError(
                "A criterion draft needs a transcript span or must be explicitly human-added."
            )
        if self.source_span is not None and self.human_added:
            raise ValueError(
                "A criterion draft cannot be both source-linked and human-added."
            )
        if self.human_added and not self.human_added_rationale:
            raise ValueError("Human-added drafts require human_added_rationale.")
        if not self.human_added and self.human_added_rationale is not None:
            raise ValueError(
                "human_added_rationale is only valid for explicitly human-added drafts."
            )

        if self.proposed_criterion is not None:
            if self.proposed_criterion.normalized_claim != self.normalized_claim:
                raise ValueError(
                    "A proposed criterion must preserve the draft normalized claim."
                )
            if self.source_span is not None:
                expected_source = self.source_span.to_source_reference()
                if (
                    self.proposed_criterion.source != expected_source
                    or self.proposed_criterion.human_added
                ):
                    raise ValueError(
                        "A proposed criterion must preserve the draft transcript source."
                    )
            elif (
                self.proposed_criterion.source is not None
                or not self.proposed_criterion.human_added
            ):
                raise ValueError(
                    "A human-added draft must produce an explicitly human-added criterion."
                )

        if self.status == DraftStatus.NEEDS_REVIEW and self.review is not None:
            raise ValueError(
                "Drafts needing review cannot already have a review decision."
            )
        if self.status == DraftStatus.APPROVED:
            if self.review is None or self.review.decision != ReviewDecision.APPROVE:
                raise ValueError("Approved drafts require an explicit approval review.")
            if self.proposed_criterion is None:
                raise ValueError(
                    "Approved drafts require a complete proposed criterion."
                )
            if self.open_questions:
                raise ValueError(
                    "Approved drafts cannot retain unresolved open questions."
                )
            if not self.proposed_criterion.approved:
                raise ValueError("Approved drafts must contain an approved criterion.")
        if self.status == DraftStatus.REJECTED and (
            self.review is None or self.review.decision != ReviewDecision.REJECT
        ):
            raise ValueError("Rejected drafts require an explicit rejection review.")
        return self


class ReviewAction(ExitSpecModel):
    """A repeatable instruction for applying a human review decision in a demo."""

    draft_id: str = Field(pattern=r"^[A-Z][A-Z0-9-]{2,63}$")
    decision: ReviewDecision
    reviewer: str = Field(min_length=1)
    rationale: str = Field(min_length=1)


class ReviewPlan(ExitSpecModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9-]{2,63}$")
    actions: List[ReviewAction] = Field(min_length=1)

    @field_validator("actions")
    @classmethod
    def require_unique_draft_actions(
        cls, actions: List[ReviewAction]
    ) -> List[ReviewAction]:
        draft_ids = [action.draft_id for action in actions]
        if len(draft_ids) != len(set(draft_ids)):
            raise ValueError("A review plan can contain only one action per draft.")
        return actions


class DiscoveryPack(ExitSpecModel):
    """Transcript plus candidate criteria; the authoring input for the Define step."""

    transcript: DiscoveryTranscript
    drafts: List[CriterionDraft] = Field(min_length=1)

    @field_validator("drafts")
    @classmethod
    def require_unique_draft_ids(
        cls, drafts: List[CriterionDraft]
    ) -> List[CriterionDraft]:
        draft_ids = [draft.id for draft in drafts]
        if len(draft_ids) != len(set(draft_ids)):
            raise ValueError(
                "Criterion draft IDs must be unique within a discovery pack."
            )
        return drafts

    @model_validator(mode="after")
    def verify_draft_sources_against_transcript(self) -> "DiscoveryPack":
        for draft in self.drafts:
            span = draft.source_span
            if span is None:
                continue
            if span.transcript_id != self.transcript.id:
                raise ValueError(
                    "A transcript span must reference the discovery pack transcript."
                )
            if span.end_line > len(self.transcript.lines):
                raise ValueError("A transcript span extends beyond the transcript.")

            selected_lines = self.transcript.lines[span.start_line - 1 : span.end_line]
            if any(line.speaker != span.speaker for line in selected_lines):
                raise ValueError(
                    "A transcript span must contain lines from its declared speaker only."
                )
            source_text = " ".join(line.text for line in selected_lines)
            normalized_quote = " ".join(span.quote.split())
            normalized_source = " ".join(source_text.split())
            if normalized_quote not in normalized_source:
                raise ValueError(
                    "A transcript span quote must appear in its declared transcript lines."
                )
        return self


class ContractSeed(ExitSpecModel):
    """Contract metadata supplied before reviewed drafts become criteria."""

    id: str = Field(pattern=r"^[a-z][a-z0-9-]{2,63}$")
    version: str = Field(min_length=1)
    created_at: datetime
    customer: str = Field(min_length=1)
    use_case: str = Field(min_length=1)
    target_system: TargetSystem
    workload: WorkloadReference
    owners: List[str] = Field(min_length=1)
    non_goals: List[str] = Field(default_factory=list)
    evidence_retention_policy: str = Field(min_length=1)


class POCContract(FrozenExitSpecModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9-]{2,63}$")
    version: str = Field(min_length=1)
    status: ContractStatus = ContractStatus.DRAFT
    created_at: datetime
    approved_at: Optional[datetime] = None
    frozen_at: Optional[datetime] = None
    customer: str = Field(min_length=1)
    use_case: str = Field(min_length=1)
    target_system: TargetSystem
    workload: WorkloadReference
    criteria: Tuple[ContractCriterion, ...] = Field(min_length=1)
    owners: Tuple[str, ...] = Field(min_length=1)
    non_goals: Tuple[str, ...] = Field(default_factory=tuple)
    evidence_retention_policy: str = Field(min_length=1)
    parent_version: Optional[str] = None
    confirmation_id: Optional[str] = Field(
        default=None,
        pattern=r"^cnf_[a-f0-9]{64}$",
    )
    canonical_hash: Optional[str] = Field(default=None, pattern=SHA256_PATTERN)

    @field_validator("criteria")
    @classmethod
    def require_unique_criterion_ids(
        cls, criteria: Tuple[ContractCriterion, ...]
    ) -> Tuple[ContractCriterion, ...]:
        ids = [criterion.id for criterion in criteria]
        if len(ids) != len(set(ids)):
            raise ValueError("Criterion IDs must be unique within a contract version.")
        return criteria

    @model_validator(mode="after")
    def validate_lifecycle_requirements(self) -> "POCContract":
        if self.status in (ContractStatus.APPROVED, ContractStatus.FROZEN):
            unapproved = [
                criterion.id for criterion in self.criteria if not criterion.approved
            ]
            if unapproved:
                raise ValueError(
                    "Approved and frozen contracts require all criteria to be approved: "
                    + ", ".join(unapproved)
                )
        if self.status == ContractStatus.FROZEN and self.frozen_at is None:
            raise ValueError("Frozen contracts require frozen_at.")
        if self.status != ContractStatus.FROZEN and self.confirmation_id is not None:
            raise ValueError(
                "Only a frozen contract may carry customer confirmation provenance."
            )
        return self

    @model_validator(mode="after")
    def validate_capability_criterion_bindings(self) -> "POCContract":
        """Keep one linear, internally consistent A4 handoff per contract."""

        capability_criteria = tuple(
            criterion
            for criterion in self.criteria
            if isinstance(criterion, CapabilityCriterion)
        )
        if not capability_criteria:
            return self
        if len(capability_criteria) != len(self.criteria):
            raise ValueError(
                "A capability agreement cannot mix legacy and generic criteria."
            )
        proposal_ids = [criterion.proposal_id for criterion in capability_criteria]
        planning_item_ids = [
            criterion.planning_item_id for criterion in capability_criteria
        ]
        if len(proposal_ids) != len(set(proposal_ids)):
            raise ValueError("Capability agreement proposal IDs must be unique.")
        if len(planning_item_ids) != len(set(planning_item_ids)):
            raise ValueError("Capability agreement planning item IDs must be unique.")
        common_plan = {
            (criterion.a4_plan_id, criterion.a4_plan_version, criterion.a4_plan_sha256)
            for criterion in capability_criteria
        }
        if len(common_plan) != 1:
            raise ValueError(
                "Capability agreement criteria must share one A4 plan binding."
            )
        poc_ids = {criterion.poc_id for criterion in capability_criteria}
        if len(poc_ids) != 1:
            raise ValueError(
                "Capability agreement criteria must share one POC binding."
            )
        return self

    @model_validator(mode="after")
    def validate_routing_slo_attainment_bindings(self) -> "POCContract":
        """Keep the additive B10 rule attached to one complete B9 campaign."""

        slo_criteria = tuple(
            criterion
            for criterion in self.criteria
            if type(criterion) is RoutingSLOAttainmentCriterionV1
        )
        if not slo_criteria:
            return self
        if len(slo_criteria) != 1:
            raise ValueError("A contract may contain exactly one B10 SLO criterion.")
        campaign_criteria = tuple(
            criterion
            for criterion in self.criteria
            if type(criterion) is RoutingQualificationCriterionV1
        )
        if len(campaign_criteria) != 1:
            raise ValueError(
                "A B10 SLO criterion requires exactly one B9 routing campaign criterion."
            )
        slo = slo_criteria[0]
        campaign = campaign_criteria[0]
        if (
            slo.campaign_criterion_id != campaign.id
            or slo.campaign_protocol_id != campaign.protocol_id
            or slo.campaign_schema_version != campaign.schema_version
            or slo.candidate_policy_id != campaign.candidate_policy.policy_id
            or slo.candidate_policy_sha256 != campaign.candidate_policy.policy_sha256
            or slo.baseline_policy_id != campaign.baseline_policy.policy_id
            or slo.baseline_policy_sha256 != campaign.baseline_policy.policy_sha256
        ):
            raise ValueError(
                "B10 SLO bindings must match the B9 campaign criterion in this contract."
            )
        return self


class ToolSelectionFixtureCase(ExitSpecModel):
    case_id: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    expected_tool: str = Field(min_length=1)


class ToolSelectionEvidence(ExitSpecModel):
    case_id: str = Field(min_length=1)
    expected_tool: str = Field(min_length=1)
    actual_tool: str = Field(min_length=1)
    is_exact_match: bool


class ProportionMeasurement(ExitSpecModel):
    criterion_id: str = Field(min_length=1)
    sample_count: int = Field(ge=0)
    success_count: int = Field(ge=0)
    evidence_refs: List[str] = Field(default_factory=list)
    external_blocked_reason: Optional[str] = None
    internal_error: Optional[str] = None
    metadata_complete: bool = True
    workload_hash_matches: bool = True
    artifact_integrity_valid: bool = True

    @model_validator(mode="after")
    def validate_counts(self) -> "ProportionMeasurement":
        if self.success_count > self.sample_count:
            raise ValueError("success_count cannot exceed sample_count.")
        return self


class EvidenceArtifact(ExitSpecModel):
    artifact_id: str = Field(min_length=1)
    criterion_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    artifact_type: str = Field(min_length=1)
    storage_path: str = Field(min_length=1)
    media_type: str = Field(min_length=1)
    sha256: str = Field(pattern=SHA256_PATTERN)
    created_at: datetime
    redaction_state: str = Field(min_length=1)
    producer_adapter: str = Field(min_length=1)
    provenance: dict = Field(default_factory=dict)


class CriterionVerdict(ExitSpecModel):
    criterion_id: str = Field(min_length=1)
    verdict: VerdictStatus
    observed_rate: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    threshold: float = Field(ge=0.0, le=1.0)
    sample_count: int = Field(ge=0)
    confidence_lower_bound: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    evidence_refs: List[str] = Field(default_factory=list)
    calculation_version: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    limitations: List[str] = Field(default_factory=list)


class OverallVerdict(ExitSpecModel):
    verdict: VerdictStatus
    must_have_criterion_ids: List[str] = Field(default_factory=list)
    reason: str = Field(min_length=1)


class RunManifest(ExitSpecModel):
    run_id: str = Field(min_length=1)
    contract_id: str = Field(min_length=1)
    contract_version: str = Field(min_length=1)
    contract_hash: str = Field(pattern=SHA256_PATTERN)
    fixture_hash: str = Field(pattern=SHA256_PATTERN)
    started_at: datetime
    ended_at: datetime
    provider: str = Field(min_length=1)
    endpoint_class: str = Field(min_length=1)
    model: str = Field(min_length=1)
    region: str = Field(min_length=1)
    runtime_configuration: dict = Field(default_factory=dict)
    traffic_shape: str = Field(min_length=1)
    warm_state: str = Field(min_length=1)
    adapter_versions: dict = Field(default_factory=dict)
    retry_policy: str = Field(min_length=1)
    redaction_policy: str = Field(min_length=1)
    environment_metadata: dict = Field(default_factory=dict)
    status: RunStatus
