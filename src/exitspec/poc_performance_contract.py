"""Pure assembly of reviewed definitions into a runner-valid performance bundle.

This module grants no customer-confirmation, freeze, execution, evidence, or
verdict authority.  It only proves that the current reviewed definition pair
can be represented exactly by ExitSpec's existing performance contract and
workload models.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import hashlib
from typing import Sequence
from urllib.parse import urlsplit

from pydantic import Field, field_validator, model_validator

from .canonical import canonical_json_bytes
from .models import (
    ContractStatus,
    ErrorRateRule,
    FrozenExitSpecModel,
    InferencePerformanceCriterionV2,
    MeasurementPopulationPolicyV1,
    POCContract,
    SourceReference,
    TargetSystem,
    TTFTP95Rule,
    WorkloadReference,
)
from .performance_evidence import (
    PerformanceWorkloadV1,
    ValidatedPerformanceContext,
    validate_performance_context_bytes,
)
from .poc_contract_definition import (
    ContractDefinitionOperator,
    ContractDefinitionReceipt,
    InferencePerformanceMetric,
)
from .poc_creation import (
    DraftPOCArchiveState,
    DraftPOCSnapshot,
)
from .poc_proposal_review import (
    ProposalDecision,
    ProposalReviewItem,
    ProposalReviewState,
)


ADAPTER = "vllm_streaming_latency"
ADAPTER_VERSION = "1.0.0"
FIRST_TOKEN_DEFINITION = "first_nonempty_choices_delta_content_v1"
MAX_TARGET_TEXT = 512
MAX_ENDPOINT_LENGTH = 2_048
MAX_PROMPT_BYTES = 4 * 1024 * 1024
MAX_STREAM_BYTES = 1024 * 1024


class PerformanceContractAssemblyError(ValueError):
    """The reviewed inputs cannot become an exact executable contract."""


class PerformanceTargetInput(FrozenExitSpecModel):
    """Explicit target identity; no target field may be inferred after freeze."""

    provider: str = Field(min_length=1, max_length=MAX_TARGET_TEXT)
    endpoint_class: str = Field(min_length=1, max_length=MAX_TARGET_TEXT)
    endpoint: str = Field(min_length=1, max_length=MAX_ENDPOINT_LENGTH)
    model: str = Field(min_length=1, max_length=MAX_TARGET_TEXT)

    @field_validator(
        "provider",
        "endpoint_class",
        "endpoint",
        "model",
        mode="before",
    )
    @classmethod
    def require_exact_single_line_text(cls, value: object) -> str:
        if type(value) is not str or not value or value != value.strip():
            raise ValueError("Target values must be exact non-empty text.")
        if any(ord(character) < 0x20 or ord(character) == 0x7F for character in value):
            raise ValueError("Target values cannot contain control characters.")
        return value

    @model_validator(mode="after")
    def require_exact_http_endpoint(self) -> "PerformanceTargetInput":
        parsed = urlsplit(self.endpoint)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.geturl() != self.endpoint
        ):
            raise ValueError(
                "endpoint must be an exact HTTP(S) URL without credentials, "
                "query, or fragment."
            )
        return self


class PerformanceDefinitionBinding(FrozenExitSpecModel):
    """The immutable authoring receipts represented by the executable bundle."""

    proposal_id: str
    definition_id: str
    definition_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    metric: InferencePerformanceMetric


@dataclass(frozen=True, slots=True)
class PreparedPerformanceBundle:
    """Approved runner-valid inputs that still carry zero execution authority."""

    poc_id: str
    bundle_fingerprint: str
    approved_contract: POCContract
    workload: PerformanceWorkloadV1
    workload_bytes: bytes = field(repr=False)
    prompt_bytes: bytes = field(repr=False)
    context: ValidatedPerformanceContext = field(repr=False)
    definition_bindings: tuple[PerformanceDefinitionBinding, ...]
    planning_limitations: tuple[str, ...]

    def __post_init__(self) -> None:
        if (
            self.approved_contract.status is not ContractStatus.APPROVED
            or self.context.contract != self.approved_contract
            or self.context.workload != self.workload
            or self.context.workload_bytes != self.workload_bytes
            or self.context.prompt_bytes != self.prompt_bytes
            or hashlib.sha256(self.workload_bytes).hexdigest()
            != self.approved_contract.workload.sha256
            or hashlib.sha256(self.prompt_bytes).hexdigest()
            != self.workload.prompt_fixture_sha256
        ):
            raise ValueError("Prepared performance bundle binding is invalid.")
        expected = _bundle_fingerprint(
            contract=self.approved_contract,
            workload_bytes=self.workload_bytes,
            prompt_bytes=self.prompt_bytes,
            definition_bindings=self.definition_bindings,
        )
        if expected != self.bundle_fingerprint:
            raise ValueError("Prepared performance bundle fingerprint is invalid.")


def prepare_performance_bundle(
    *,
    draft: DraftPOCSnapshot,
    proposals: Sequence[ProposalReviewItem],
    definitions: Sequence[ContractDefinitionReceipt],
    target: PerformanceTargetInput,
    prompt_bytes: bytes,
    prepared_at: datetime,
    warmup_count: int = 10,
    timeout_seconds: float = 30.0,
    max_stream_bytes: int = MAX_STREAM_BYTES,
    contract_id: str | None = None,
    contract_version: str = "1",
    parent_version: str | None = None,
) -> PreparedPerformanceBundle:
    """Create one exact approved contract and validate it with the real runner."""

    if type(draft) is not DraftPOCSnapshot:
        raise TypeError("draft must be a DraftPOCSnapshot.")
    if draft.archive_state is not DraftPOCArchiveState.ACTIVE:
        raise PerformanceContractAssemblyError("The POC must be active.")
    if type(target) is not PerformanceTargetInput:
        raise TypeError("target must be a PerformanceTargetInput.")
    if (
        type(prepared_at) is not datetime
        or prepared_at.tzinfo is None
        or prepared_at.utcoffset() is None
    ):
        raise PerformanceContractAssemblyError("prepared_at must be timezone-aware.")
    if type(prompt_bytes) is not bytes or not 0 < len(prompt_bytes) <= MAX_PROMPT_BYTES:
        raise PerformanceContractAssemblyError(
            "prompt_bytes must be non-empty bounded bytes."
        )
    if (
        type(warmup_count) is not int
        or isinstance(warmup_count, bool)
        or not 0 <= warmup_count <= 100
        or type(timeout_seconds) not in (int, float)
        or isinstance(timeout_seconds, bool)
        or not 0 < float(timeout_seconds) <= 60
        or type(max_stream_bytes) is not int
        or isinstance(max_stream_bytes, bool)
        or not 1 <= max_stream_bytes <= MAX_STREAM_BYTES
    ):
        raise PerformanceContractAssemblyError(
            "Server-owned workload configuration is outside runner bounds."
        )
    if (
        type(contract_version) is not str
        or not contract_version.isdigit()
        or int(contract_version) < 1
        or str(int(contract_version)) != contract_version
        or (
            contract_id is None
            and (contract_version != "1" or parent_version is not None)
        )
        or (
            contract_id is not None
            and (
                type(contract_id) is not str
                or not contract_id.startswith("agreement-")
                or not 3 <= len(contract_id) <= 64
            )
        )
        or (contract_version == "1" and parent_version is not None)
        or (
            contract_version != "1"
            and (
                contract_id is None
                or parent_version
                != "{0}@{1}".format(
                    contract_id,
                    int(contract_version) - 1,
                )
            )
        )
    ):
        raise PerformanceContractAssemblyError(
            "Contract revision identity is outside supported bounds."
        )

    kept = _kept_proposals(draft.poc_id, proposals)
    ttft, error_rate, bindings = _definition_pair(
        draft.poc_id,
        kept,
        definitions,
    )
    _require_matching_workload(ttft, error_rate)

    prompt_sha256 = hashlib.sha256(prompt_bytes).hexdigest()
    identity_seed = canonical_json_bytes(
        {
            "poc_id": draft.poc_id,
            "target": target.model_dump(mode="json"),
            "definitions": [binding.model_dump(mode="json") for binding in bindings],
            "prompt_sha256": prompt_sha256,
            "warmup_count": warmup_count,
            "timeout_seconds": float(timeout_seconds),
            "max_stream_bytes": max_stream_bytes,
        }
    )
    identity = hashlib.sha256(
        b"exitspec-performance-bundle-identity-v1\x00" + identity_seed
    ).hexdigest()
    workload_id = "perf-{0}".format(identity[:20])
    resolved_contract_id = (
        "agreement-{0}".format(identity[:20])
        if contract_id is None
        else contract_id
    )
    prompt_path = "generated/{0}/prompts-v1.jsonl".format(draft.poc_id)
    workload_path = "generated/{0}/workload-v1.json".format(draft.poc_id)

    workload_payload = {
        "schema_version": "exitspec.performance-workload.v1",
        "workload_id": workload_id,
        "adapter": ADAPTER,
        "adapter_version": ADAPTER_VERSION,
        "endpoint": target.endpoint,
        "model": target.model,
        "request_count": error_rate.minimum_samples,
        "concurrency": ttft.concurrency,
        "warmup_count": warmup_count,
        "timeout_seconds": float(timeout_seconds),
        "max_tokens": ttft.output_tokens_max,
        "max_stream_bytes": max_stream_bytes,
        "first_token_definition": FIRST_TOKEN_DEFINITION,
        "warmup_included_in_measurement": False,
        "synthetic_prompts": True,
        "prompt_fixture_path": prompt_path,
        "prompt_fixture_sha256": prompt_sha256,
        "retries": 0,
    }
    workload = PerformanceWorkloadV1.model_validate(workload_payload, strict=True)
    workload_bytes = canonical_json_bytes(workload.model_dump(mode="json"))
    workload_sha256 = hashlib.sha256(workload_bytes).hexdigest()

    ttft_proposal = kept[ttft.proposal_id]
    error_proposal = kept[error_rate.proposal_id]
    source = SourceReference(
        speaker="customer_source",
        quote="{0} {1}".format(
            ttft_proposal.source_quote,
            error_proposal.source_quote,
        ),
        location="{0}/{1}; {2}/{3}".format(
            ttft.source_receipt_id,
            ttft.proposal_id,
            error_rate.source_receipt_id,
            error_rate.proposal_id,
        ),
    )
    normalized_claim = (
        "At configured client concurrency {0}, client-observed p95 time to "
        "first non-empty content is {1} {2} milliseconds across at least {3} "
        "successful samples, and attempted-request error rate is strictly "
        "less than {4}% across {5} attempts."
    ).format(
        ttft.concurrency,
        "<" if ttft.operator is ContractDefinitionOperator.LT else "<=",
        ttft.threshold,
        ttft.minimum_samples,
        error_rate.threshold,
        error_rate.minimum_samples,
    )
    measurement_policy = MeasurementPopulationPolicyV1.model_validate(
        {
            "schema_version": "exitspec.measurement-population.v1",
            "calculation_version": "exitspec.performance-verdicts.v2",
            "measured_population": {
                "phases": ("MEASURED",),
                "exact_attempts": error_rate.minimum_samples,
                "warmups_included": False,
                "preflight_included": False,
                "retries": 0,
            },
            "latency_population": {
                "population": "successful_measured_attempts_with_valid_ttft",
                "failed_attempts": (
                    "excluded_from_latency_counted_in_reliability"
                ),
            },
            "reliability": {
                "numerator": "external_error_outcomes",
                "denominator": "all_measured_attempts",
                "outcomes": (
                    "HTTP_ERROR",
                    "TIMEOUT",
                    "PROTOCOL_ERROR",
                    "TRANSPORT_ERROR",
                ),
            },
            "invalid_evidence": {
                "terminal_outcomes": ("CANCELLED", "INTERNAL_ERROR"),
                "record_conditions": (
                    "MISSING_RECORD",
                    "DUPLICATE_RECORD",
                    "EXTRA_RECORD",
                ),
                "integrity_mismatch": "NOT_PROVEN",
                "disposition": "NOT_PROVEN",
            },
        },
        strict=True,
    )
    criterion = InferencePerformanceCriterionV2(
        criterion_type="inference_performance_v2",
        id="INFERENCE-PERF-01",
        title="Inference latency and reliability",
        source=source,
        normalized_claim=normalized_claim,
        ttft_p95=TTFTP95Rule(
            metric="time_to_first_token",
            aggregation="p95",
            unit="milliseconds",
            operator=(
                "lt" if ttft.operator is ContractDefinitionOperator.LT else "lte"
            ),
            threshold=ttft.threshold,
            method="nearest_rank",
            minimum_successful_samples=ttft.minimum_samples,
            must_pass=True,
        ),
        error_rate=ErrorRateRule(
            metric="error_rate",
            aggregation="rate",
            unit="proportion",
            operator="lt",
            threshold=error_rate.threshold / 100.0,
            method="failed_attempts_over_total_attempts",
            minimum_attempts=error_rate.minimum_samples,
            must_pass=True,
        ),
        workload_slice=workload_id,
        adapter=ADAPTER,
        adapter_version=ADAPTER_VERSION,
        owner=draft.owner,
        evidence_policy=(
            "Persist the frozen contract, workload manifest, sanitized terminal "
            "attempt records, calculation inputs, and SHA-256 digests."
        ),
        measurement_policy=measurement_policy,
        approved=True,
    )
    limitations = (
        (
            "NOT_PROVEN — Prompt token range {0}-{1} is planning context "
            "only; the runner binds the exact synthetic prompt fixture and "
            "does not prove a token distribution."
        ).format(ttft.prompt_tokens_min, ttft.prompt_tokens_max),
        (
            "NOT_PROVEN — Output minimum {0} tokens is not measured; runner "
            "only binds max_tokens={1}."
        ).format(ttft.output_tokens_min, ttft.output_tokens_max),
        (
            "NOT_PROVEN — Configured client concurrency does not by itself "
            "prove achieved request overlap, production capacity, GPU "
            "latency, model quality, or long-duration reliability."
        ),
        *_excluded_claim_limitations(proposals),
    )
    contract = POCContract(
        id=resolved_contract_id,
        version=contract_version,
        status=ContractStatus.APPROVED,
        created_at=prepared_at,
        approved_at=prepared_at,
        customer=draft.customer_label,
        use_case=draft.use_case,
        target_system=TargetSystem(
            provider=target.provider,
            endpoint_class=target.endpoint_class,
            model=target.model,
        ),
        workload=WorkloadReference(
            fixture_path=workload_path,
            sha256=workload_sha256,
        ),
        criteria=(criterion,),
        owners=(draft.owner,),
        non_goals=limitations,
        evidence_retention_policy=(
            "Retain the approved synthetic prompt fixture and redacted "
            "measurement artifacts; never persist credentials or response text."
        ),
        parent_version=parent_version,
    )
    context = validate_performance_context_bytes(
        contract,
        workload_bytes,
        prompt_bytes,
    )
    return PreparedPerformanceBundle(
        poc_id=draft.poc_id,
        bundle_fingerprint=_bundle_fingerprint(
            contract=contract,
            workload_bytes=workload_bytes,
            prompt_bytes=prompt_bytes,
            definition_bindings=bindings,
        ),
        approved_contract=contract,
        workload=workload,
        workload_bytes=workload_bytes,
        prompt_bytes=prompt_bytes,
        context=context,
        definition_bindings=bindings,
        planning_limitations=limitations,
    )


def _excluded_claim_limitations(
    proposals: Sequence[ProposalReviewItem],
) -> tuple[str, ...]:
    """Keep human-excluded source claims visible as explicit non-proof."""

    excluded = []
    for proposal in proposals:
        if (
            type(proposal) is ProposalReviewItem
            and proposal.review_state is ProposalReviewState.DISCARD
            and proposal.decision is not None
            and proposal.decision.decision is ProposalDecision.DISCARD
        ):
            excluded.append(
                "NOT_PROVEN — Reviewed source claim excluded from this "
                'executable POC: "{0}"'.format(proposal.normalized_claim)
            )
    return tuple(excluded)


def _kept_proposals(
    poc_id: str,
    proposals: Sequence[ProposalReviewItem],
) -> dict[str, ProposalReviewItem]:
    detached = tuple(proposals)
    if any(type(item) is not ProposalReviewItem for item in detached):
        raise PerformanceContractAssemblyError(
            "Proposal projection contains an invalid item."
        )
    kept = {}
    for proposal in detached:
        if proposal.poc_id != poc_id:
            raise PerformanceContractAssemblyError(
                "Proposal projection crosses POC authority."
            )
        if proposal.review_state is not ProposalReviewState.KEEP_FOR_CONTRACT:
            continue
        if (
            proposal.decision is None
            or proposal.decision.decision is not ProposalDecision.KEEP_FOR_CONTRACT
            or proposal.proposal_id in kept
        ):
            raise PerformanceContractAssemblyError("Kept proposal binding is invalid.")
        kept[proposal.proposal_id] = proposal
    if len(kept) != 2:
        raise PerformanceContractAssemblyError(
            "The first executable vertical requires exactly two kept proposals."
        )
    return kept


def _definition_pair(
    poc_id: str,
    kept: dict[str, ProposalReviewItem],
    definitions: Sequence[ContractDefinitionReceipt],
) -> tuple[
    ContractDefinitionReceipt,
    ContractDefinitionReceipt,
    tuple[PerformanceDefinitionBinding, ...],
]:
    detached = tuple(definitions)
    if len(detached) != 2 or any(
        type(item) is not ContractDefinitionReceipt for item in detached
    ):
        raise PerformanceContractAssemblyError(
            "Exactly two current definition receipts are required."
        )
    by_metric = {}
    by_proposal = {}
    for definition in detached:
        proposal = kept.get(definition.proposal_id)
        if (
            definition.poc_id != poc_id
            or proposal is None
            or definition.proposal_id in by_proposal
            or definition.metric in by_metric
            or definition.source_receipt_id != proposal.source_receipt_id
            or definition.source_kind != proposal.source_kind
            or definition.normalized_claim != proposal.normalized_claim
        ):
            raise PerformanceContractAssemblyError(
                "Definition provenance does not match current kept proposals."
            )
        by_proposal[definition.proposal_id] = definition
        by_metric[definition.metric] = definition
    try:
        ttft = by_metric[InferencePerformanceMetric.TTFT_P95_MS]
        error_rate = by_metric[InferencePerformanceMetric.ERROR_RATE_PERCENT]
    except KeyError as error:
        raise PerformanceContractAssemblyError(
            "One TTFT and one error-rate definition are required."
        ) from error
    if (
        error_rate.operator is not ContractDefinitionOperator.LT
        or not 0 < error_rate.threshold < 100
        or ttft.operator
        not in {ContractDefinitionOperator.LT, ContractDefinitionOperator.LTE}
    ):
        raise PerformanceContractAssemblyError(
            "Definition operators are not exactly runner compatible."
        )
    bindings = tuple(
        PerformanceDefinitionBinding(
            proposal_id=item.proposal_id,
            definition_id=item.definition_id,
            definition_sha256=item.definition_sha256,
            metric=item.metric,
        )
        for item in (ttft, error_rate)
    )
    return ttft, error_rate, bindings


def _require_matching_workload(
    ttft: ContractDefinitionReceipt,
    error_rate: ContractDefinitionReceipt,
) -> None:
    fields = (
        "concurrency",
        "prompt_tokens_min",
        "prompt_tokens_max",
        "output_tokens_min",
        "output_tokens_max",
    )
    if any(
        getattr(ttft, field_name) != getattr(error_rate, field_name)
        for field_name in fields
    ):
        raise PerformanceContractAssemblyError(
            "The two definitions must bind the same workload shape."
        )
    if (
        ttft.minimum_samples > error_rate.minimum_samples
        or ttft.concurrency > error_rate.minimum_samples
        or error_rate.minimum_samples > 1_000
        or ttft.concurrency > 32
        or ttft.output_tokens_max > 2_048
    ):
        raise PerformanceContractAssemblyError(
            "The definition pair exceeds current runner safety bounds."
        )


def _bundle_fingerprint(
    *,
    contract: POCContract,
    workload_bytes: bytes,
    prompt_bytes: bytes,
    definition_bindings: tuple[PerformanceDefinitionBinding, ...],
) -> str:
    return hashlib.sha256(
        b"exitspec-prepared-performance-bundle-v1\x00"
        + canonical_json_bytes(
            {
                "contract": contract.model_dump(mode="json"),
                "workload_sha256": hashlib.sha256(workload_bytes).hexdigest(),
                "prompt_sha256": hashlib.sha256(prompt_bytes).hexdigest(),
                "definition_bindings": [
                    binding.model_dump(mode="json") for binding in definition_bindings
                ],
            }
        )
    ).hexdigest()


__all__ = [
    "ADAPTER",
    "ADAPTER_VERSION",
    "PerformanceContractAssemblyError",
    "PerformanceDefinitionBinding",
    "PerformanceTargetInput",
    "PreparedPerformanceBundle",
    "prepare_performance_bundle",
]
