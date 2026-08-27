"""Strict domain models for ExitSpec's trusted decision boundary."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import List, Literal, Optional, Tuple, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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


ContractCriterion = Union[
    Criterion,
    InferencePerformanceCriterion,
    InferencePerformanceCriterionV2,
    InferencePerformanceCriterionV3,
    InferencePerformanceCriterionV4,
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
