from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import math

import pytest
from pydantic import ValidationError

from exitspec.poc_contract_definition import (
    MAX_IDEMPOTENCY_KEY_LENGTH,
    MAX_OUTPUT_TOKENS,
    MAX_PERFORMANCE_CONCURRENCY,
    MAX_PERFORMANCE_SAMPLES,
    MAX_PROMPT_TOKENS,
    MAX_RATIONALE_LENGTH,
    MAX_REVIEWER_LENGTH,
    MAX_TTFT_P95_MS,
    ContractDefinitionCapacityExceeded,
    ContractDefinitionConflict,
    ContractDefinitionCrossPOC,
    ContractDefinitionDisposition,
    ContractDefinitionIdempotencyConflict,
    ContractDefinitionInvalid,
    ContractDefinitionLookupUnavailable,
    ContractDefinitionOperator,
    ContractDefinitionProposalNotKept,
    ContractDefinitionProposalUnavailable,
    ContractDefinitionReceipt,
    ContractDefinitionStaleProposal,
    ContractDefinitionUnit,
    InferencePerformanceCriterionDefinition,
    InferencePerformanceMetric,
    ProcessLocalContractDefinitionService,
)
from exitspec.poc_proposal_review import (
    ProposalDecision,
    ProposalDecisionReceipt,
    ProposalReviewItem,
    ProposalReviewState,
    SourceBoundProposal,
)
from exitspec.poc_sources import SourceKind


NOW = datetime(2026, 7, 29, 9, 0, tzinfo=timezone.utc)
LATER = NOW + timedelta(hours=3)
POC_ALPHA = "poc_customer_alpha"
POC_BETA = "poc_customer_beta"
PROPOSAL_ID = "prop_latency_001"


def _proposal_decision(
    *,
    poc_id: str = POC_ALPHA,
    proposal_id: str = PROPOSAL_ID,
    source_receipt_id: str = "srcpt_meeting_001",
    source_kind: SourceKind = SourceKind.MEETING,
    decision: ProposalDecision = ProposalDecision.KEEP_FOR_CONTRACT,
) -> ProposalDecisionReceipt:
    return ProposalDecisionReceipt(
        poc_id=poc_id,
        proposal_id=proposal_id,
        source_receipt_id=source_receipt_id,
        source_kind=source_kind,
        decision=decision,
        reviewer="Jayesh Suyal",
        rationale="Keep this source-bound claim for contract definition.",
        decided_at=NOW,
    )


def _review_item(
    *,
    poc_id: str = POC_ALPHA,
    proposal_id: str = PROPOSAL_ID,
    source_receipt_id: str = "srcpt_meeting_001",
    source_kind: SourceKind = SourceKind.MEETING,
    source_quote: str = "P95 TTFT must remain at or below 500 ms.",
    normalized_claim: str = "P95 TTFT must remain at or below 500 ms.",
    state: ProposalReviewState = ProposalReviewState.KEEP_FOR_CONTRACT,
) -> ProposalReviewItem:
    decision = (
        None
        if state == ProposalReviewState.NEEDS_REVIEW
        else _proposal_decision(
            poc_id=poc_id,
            proposal_id=proposal_id,
            source_receipt_id=source_receipt_id,
            source_kind=source_kind,
            decision=ProposalDecision(state.value),
        )
    )
    return ProposalReviewItem(
        poc_id=poc_id,
        proposal_id=proposal_id,
        source_receipt_id=source_receipt_id,
        source_kind=source_kind,
        source_quote=source_quote,
        normalized_claim=normalized_claim,
        review_state=state,
        decision=decision,
    )


def _source_bound(
    *,
    poc_id: str = POC_ALPHA,
    proposal_id: str = PROPOSAL_ID,
) -> SourceBoundProposal:
    return SourceBoundProposal(
        poc_id=poc_id,
        proposal_id=proposal_id,
        source_receipt_id="srcpt_meeting_001",
        source_kind=SourceKind.MEETING,
        source_quote="P95 TTFT must remain at or below 500 ms.",
        normalized_claim="P95 TTFT must remain at or below 500 ms.",
    )


class _Lookup:
    def __init__(self) -> None:
        self.by_poc: dict[
            str,
            tuple[SourceBoundProposal | ProposalReviewItem, ...],
        ] = {}

    def __call__(self, poc_id: str):
        return self.by_poc.get(poc_id, ())


def _criterion(**updates: object) -> InferencePerformanceCriterionDefinition:
    payload: dict[str, object] = {
        "metric": InferencePerformanceMetric.TTFT_P95_MS,
        "operator": ContractDefinitionOperator.LT,
        "threshold": 500.0,
        "minimum_samples": 100,
        "concurrency": 4,
        "prompt_tokens_min": 512,
        "prompt_tokens_max": 4_096,
        "output_tokens_min": 64,
        "output_tokens_max": 512,
        "reviewer": "Jayesh Suyal",
        "rationale": "This is the bounded latency acceptance requirement.",
    }
    payload.update(updates)
    return InferencePerformanceCriterionDefinition(**payload)


def _service(
    lookup: _Lookup,
    **updates: object,
) -> ProcessLocalContractDefinitionService:
    options: dict[str, object] = {
        "proposal_lookup": lookup,
        "clock": lambda: NOW,
    }
    options.update(updates)
    return ProcessLocalContractDefinitionService(**options)


def _define(
    service: ProcessLocalContractDefinitionService,
    *,
    poc_id: str = POC_ALPHA,
    proposal_id: str = PROPOSAL_ID,
    criterion: InferencePerformanceCriterionDefinition | None = None,
    idempotency_key: str = "define-latency-001",
):
    return service.define(
        poc_id,
        proposal_id,
        criterion or _criterion(),
        idempotency_key=idempotency_key,
    )


def test_kept_proposal_creates_one_immutable_ttft_definition_receipt():
    lookup = _Lookup()
    lookup.by_poc[POC_ALPHA] = (_review_item(),)
    service = _service(lookup)

    result = _define(service)
    receipt = result.receipt

    assert result.disposition == ContractDefinitionDisposition.CREATED
    assert result.created is True
    assert result.replayed is False
    assert receipt.definition_id.startswith("cdef_")
    assert len(receipt.definition_sha256) == 64
    assert receipt.criterion_type == "INFERENCE_PERFORMANCE_V1"
    assert receipt.metric == InferencePerformanceMetric.TTFT_P95_MS
    assert receipt.unit == ContractDefinitionUnit.MILLISECONDS
    assert receipt.operator == ContractDefinitionOperator.LT
    assert receipt.threshold == 500.0
    assert receipt.minimum_samples == 100
    assert receipt.concurrency == 4
    assert (receipt.prompt_tokens_min, receipt.prompt_tokens_max) == (512, 4_096)
    assert (receipt.output_tokens_min, receipt.output_tokens_max) == (64, 512)
    assert receipt.defined_at == NOW
    assert service.get(receipt.definition_id) is receipt
    assert service.definitions() == (receipt,)
    with pytest.raises(ValidationError):
        receipt.threshold = 1.0


def test_error_rate_percent_supports_strict_executable_boundary():
    lookup = _Lookup()
    lookup.by_poc[POC_ALPHA] = (_review_item(),)
    receipt = _define(
        _service(lookup),
        criterion=_criterion(
            metric=InferencePerformanceMetric.ERROR_RATE_PERCENT,
            operator=ContractDefinitionOperator.LT,
            threshold=1.0,
        ),
    ).receipt

    assert receipt.metric == InferencePerformanceMetric.ERROR_RATE_PERCENT
    assert receipt.unit == ContractDefinitionUnit.PERCENT
    assert receipt.operator == ContractDefinitionOperator.LT
    assert receipt.threshold == 1.0


def test_strict_zero_percent_error_threshold_is_rejected_as_impossible():
    with pytest.raises(ValidationError, match="greater than 0"):
        _criterion(
            metric=InferencePerformanceMetric.ERROR_RATE_PERCENT,
            operator=ContractDefinitionOperator.LT,
            threshold=0.0,
        )


@pytest.mark.parametrize(
    ("operator", "threshold", "message"),
    (
        (ContractDefinitionOperator.LTE, 1.0, "strict LT"),
        (ContractDefinitionOperator.LT, 100.0, "less than 100"),
    ),
)
def test_error_rate_rejects_non_executable_operator_or_threshold(
    operator: ContractDefinitionOperator,
    threshold: float,
    message: str,
):
    with pytest.raises(ValidationError, match=message):
        _criterion(
            metric=InferencePerformanceMetric.ERROR_RATE_PERCENT,
            operator=operator,
            threshold=threshold,
        )


def test_operator_semantics_participate_in_definition_identity():
    first_lookup = _Lookup()
    second_lookup = _Lookup()
    first_lookup.by_poc[POC_ALPHA] = (_review_item(),)
    second_lookup.by_poc[POC_ALPHA] = (_review_item(),)

    strict = _define(
        _service(first_lookup),
        criterion=_criterion(operator=ContractDefinitionOperator.LT),
    ).receipt
    inclusive = _define(
        _service(second_lookup),
        criterion=_criterion(operator=ContractDefinitionOperator.LTE),
    ).receipt

    assert strict.definition_id != inclusive.definition_id
    assert strict.definition_sha256 != inclusive.definition_sha256


def test_identity_and_digest_are_stable_across_processes_and_clock_values():
    first_lookup = _Lookup()
    second_lookup = _Lookup()
    first_lookup.by_poc[POC_ALPHA] = (_review_item(),)
    second_lookup.by_poc[POC_ALPHA] = (_review_item(),)
    first = _define(_service(first_lookup)).receipt
    second = _define(
        _service(second_lookup, clock=lambda: LATER),
    ).receipt

    assert first.definition_id == second.definition_id
    assert first.definition_sha256 != second.definition_sha256
    assert first.defined_at == NOW
    assert second.defined_at == LATER


def test_exact_idempotent_replay_rechecks_binding_and_returns_original():
    lookup = _Lookup()
    lookup.by_poc[POC_ALPHA] = (_review_item(),)
    service = _service(lookup)

    created = _define(service)
    replay = _define(service)

    assert replay.disposition == ContractDefinitionDisposition.IDEMPOTENT_REPLAY
    assert replay.created is False
    assert replay.replayed is True
    assert replay.receipt is created.receipt
    assert len(service) == 1


@pytest.mark.parametrize(
    "criterion",
    (
        _criterion(threshold=300.0),
        _criterion(minimum_samples=101),
        _criterion(concurrency=8),
        _criterion(prompt_tokens_min=256),
        _criterion(prompt_tokens_max=8_192),
        _criterion(output_tokens_min=32),
        _criterion(output_tokens_max=1_024),
        _criterion(reviewer="Another reviewer"),
        _criterion(rationale="A changed human rationale."),
        _criterion(
            metric=InferencePerformanceMetric.ERROR_RATE_PERCENT,
            threshold=1.0,
        ),
    ),
)
def test_conflicting_idempotency_reuse_fails_without_mutation(
    criterion: InferencePerformanceCriterionDefinition,
):
    lookup = _Lookup()
    lookup.by_poc[POC_ALPHA] = (_review_item(),)
    service = _service(lookup)
    original = _define(service).receipt

    with pytest.raises(ContractDefinitionIdempotencyConflict) as caught:
        _define(service, criterion=criterion)

    assert caught.value.http_status == 409
    assert service.definitions() == (original,)


def test_same_or_changed_definition_with_new_key_cannot_overwrite_proposal():
    lookup = _Lookup()
    lookup.by_poc[POC_ALPHA] = (_review_item(),)
    service = _service(lookup)
    original = _define(service).receipt

    with pytest.raises(ContractDefinitionConflict):
        _define(service, idempotency_key="new-key-same-request")
    with pytest.raises(ContractDefinitionConflict):
        _define(
            service,
            criterion=_criterion(threshold=250.0),
            idempotency_key="new-key-changed-request",
        )

    assert service.definitions() == (original,)


def test_missing_proposal_fails_closed():
    lookup = _Lookup()
    service = _service(lookup)

    with pytest.raises(ContractDefinitionProposalUnavailable) as caught:
        _define(service)

    assert caught.value.http_status == 404
    assert len(service) == 0


@pytest.mark.parametrize(
    "proposal",
    (
        _source_bound(),
        _review_item(state=ProposalReviewState.NEEDS_REVIEW),
        _review_item(state=ProposalReviewState.DISCARD),
    ),
)
def test_source_bound_unreviewed_and_discarded_proposals_are_not_kept(
    proposal: SourceBoundProposal | ProposalReviewItem,
):
    lookup = _Lookup()
    lookup.by_poc[POC_ALPHA] = (proposal,)
    service = _service(lookup)

    with pytest.raises(ContractDefinitionProposalNotKept) as caught:
        _define(service)

    assert caught.value.http_status == 409
    assert len(service) == 0


def test_not_kept_can_later_be_kept_but_a_known_kept_binding_cannot_regress():
    lookup = _Lookup()
    lookup.by_poc[POC_ALPHA] = (_source_bound(),)
    service = _service(lookup)
    with pytest.raises(ContractDefinitionProposalNotKept):
        _define(service)

    lookup.by_poc[POC_ALPHA] = (_review_item(),)
    created = _define(service)
    assert created.created is True

    lookup.by_poc[POC_ALPHA] = (
        _review_item(state=ProposalReviewState.DISCARD),
    )
    with pytest.raises(ContractDefinitionStaleProposal):
        _define(service)


def test_known_kept_proposal_disappearance_and_binding_change_are_stale():
    lookup = _Lookup()
    lookup.by_poc[POC_ALPHA] = (_review_item(),)
    service = _service(lookup)
    created = _define(service).receipt

    lookup.by_poc[POC_ALPHA] = ()
    with pytest.raises(ContractDefinitionStaleProposal):
        _define(service)
    assert service.definitions() == (created,)

    lookup.by_poc[POC_ALPHA] = (
        _review_item(
            source_quote="P95 TTFT must remain at or below 300 ms.",
            normalized_claim="P95 TTFT must remain at or below 300 ms.",
        ),
    )
    with pytest.raises(ContractDefinitionStaleProposal):
        _define(service)
    assert service.definitions() == (created,)


def test_cross_poc_proposal_identifier_is_never_rebound():
    lookup = _Lookup()
    lookup.by_poc[POC_ALPHA] = (_review_item(),)
    service = _service(lookup)
    _define(service)
    lookup.by_poc[POC_BETA] = (
        _review_item(poc_id=POC_BETA),
    )

    with pytest.raises(ContractDefinitionCrossPOC) as caught:
        _define(
            service,
            poc_id=POC_BETA,
            idempotency_key="cross-poc",
        )

    assert caught.value.http_status == 404
    assert len(service) == 1


def test_lookup_failures_wrong_shapes_duplicates_and_cross_bindings_are_sanitized():
    exploding = ProcessLocalContractDefinitionService(
        proposal_lookup=lambda _: (_ for _ in ()).throw(
            RuntimeError("raw secret")
        ),
    )
    with pytest.raises(ContractDefinitionLookupUnavailable) as caught:
        _define(exploding)
    assert "raw secret" not in str(caught.value)

    for lookup_function in (
        lambda _: "not-a-sequence",
        lambda _: ("wrong-type",),
        lambda _: (_review_item(), _review_item()),
        lambda _: (_review_item(poc_id=POC_BETA),),
    ):
        service = ProcessLocalContractDefinitionService(
            proposal_lookup=lookup_function,
        )
        with pytest.raises(ContractDefinitionLookupUnavailable):
            _define(service)
        assert len(service) == 0


@pytest.mark.parametrize(
    ("metric", "threshold"),
    (
        (InferencePerformanceMetric.TTFT_P95_MS, 0.0),
        (InferencePerformanceMetric.TTFT_P95_MS, -1.0),
        (
            InferencePerformanceMetric.TTFT_P95_MS,
            MAX_TTFT_P95_MS + 0.001,
        ),
        (InferencePerformanceMetric.ERROR_RATE_PERCENT, -0.001),
        (InferencePerformanceMetric.ERROR_RATE_PERCENT, 100.001),
    ),
)
def test_metric_specific_threshold_bounds_are_enforced(
    metric: InferencePerformanceMetric,
    threshold: float,
):
    with pytest.raises(ValidationError, match="threshold"):
        _criterion(metric=metric, threshold=threshold)


@pytest.mark.parametrize(
    "threshold",
    (
        True,
        False,
        "500",
        None,
        math.nan,
        math.inf,
        -math.inf,
    ),
)
def test_threshold_rejects_coercion_nonfinite_and_boolean_values(threshold: object):
    with pytest.raises(ValidationError, match="threshold"):
        _criterion(threshold=threshold)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("minimum_samples", 0),
        ("minimum_samples", MAX_PERFORMANCE_SAMPLES + 1),
        ("concurrency", 0),
        ("concurrency", MAX_PERFORMANCE_CONCURRENCY + 1),
        ("prompt_tokens_min", 0),
        ("prompt_tokens_max", MAX_PROMPT_TOKENS + 1),
        ("output_tokens_min", 0),
        ("output_tokens_max", MAX_OUTPUT_TOKENS + 1),
        ("minimum_samples", True),
        ("concurrency", 1.0),
        ("prompt_tokens_min", "512"),
        ("output_tokens_max", None),
    ),
)
def test_integer_fields_are_exact_and_bounded(field: str, value: object):
    with pytest.raises(ValidationError, match=field):
        _criterion(**{field: value})


def test_concurrency_cannot_exceed_the_measured_sample_count():
    with pytest.raises(ValidationError, match="cannot exceed"):
        _criterion(minimum_samples=4, concurrency=5)


@pytest.mark.parametrize(
    "updates",
    (
        {"prompt_tokens_min": 513, "prompt_tokens_max": 512},
        {"output_tokens_min": 513, "output_tokens_max": 512},
    ),
)
def test_token_ranges_must_be_ordered(updates: dict[str, object]):
    with pytest.raises(ValidationError, match="cannot exceed"):
        _criterion(**updates)


@pytest.mark.parametrize(
    "updates",
    (
        {"metric": "TTFT_P95_MS"},
        {"metric": "latency"},
        {"operator": "LT"},
        {"operator": "LTE"},
        {"operator": "GTE"},
    ),
)
def test_metric_and_operator_reject_string_coercion(updates: dict[str, object]):
    with pytest.raises(ValidationError):
        _criterion(**updates)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("reviewer", ""),
        ("reviewer", "r" * (MAX_REVIEWER_LENGTH + 1)),
        ("reviewer", "one\nother"),
        ("reviewer", "raw.person@example.com"),
        ("rationale", ""),
        ("rationale", "r" * (MAX_RATIONALE_LENGTH + 1)),
        ("rationale", "Use secret fw_abcdefghijklmnopqrstuvwxyz."),
        ("rationale", "unsafe\u0000control"),
    ),
)
def test_human_text_is_required_bounded_redacted_and_control_free(
    field: str,
    value: str,
):
    with pytest.raises(ValidationError):
        _criterion(**{field: value})


def test_human_text_is_normalized_before_hashing():
    first = _criterion(
        reviewer="  Jayesh Suyal  ",
        rationale="  Bounded rationale.  ",
    )
    second = _criterion(
        reviewer="Jayesh Suyal",
        rationale="Bounded rationale.",
    )

    assert first == second


@pytest.mark.parametrize(
    "idempotency_key",
    (
        "",
        " ",
        " surrounding ",
        "line\nbreak",
        "unsafe\u0000control",
        "x" * (MAX_IDEMPOTENCY_KEY_LENGTH + 1),
        None,
        123,
    ),
)
def test_idempotency_key_is_exact_bounded_and_not_persisted(
    idempotency_key: object,
):
    lookup = _Lookup()
    lookup.by_poc[POC_ALPHA] = (_review_item(),)
    service = _service(lookup)

    with pytest.raises(ContractDefinitionInvalid):
        _define(service, idempotency_key=idempotency_key)  # type: ignore[arg-type]

    assert len(service) == 0


def test_receipt_result_and_service_never_expose_raw_idempotency_key():
    lookup = _Lookup()
    lookup.by_poc[POC_ALPHA] = (_review_item(),)
    service = _service(lookup)
    secret_key = "do-not-persist-this-key"
    result = _define(service, idempotency_key=secret_key)
    serialized = json.dumps(result.model_dump(mode="json"), sort_keys=True)

    assert secret_key not in serialized
    assert "idempotency_key" not in serialized
    assert secret_key not in repr(service)
    assert secret_key not in repr(result)


def test_models_forbid_extra_authority_fields_and_validated_copy_bypasses():
    criterion = _criterion()
    with pytest.raises(ValidationError, match="Extra inputs"):
        InferencePerformanceCriterionDefinition(
            **criterion.model_dump(),
            approved=True,
        )
    with pytest.raises(ValidationError, match="Extra inputs"):
        criterion.model_copy(update={"confirmed": True})

    lookup = _Lookup()
    lookup.by_poc[POC_ALPHA] = (_review_item(),)
    receipt = _define(_service(lookup)).receipt
    payload = receipt.model_dump(mode="python")
    with pytest.raises(ValidationError, match="Extra inputs"):
        ContractDefinitionReceipt(**payload, verdict="PASS")
    with pytest.raises(ValidationError):
        receipt.model_copy(update={"frozen": True})
    with pytest.raises(ValidationError, match="definition_id"):
        receipt.model_copy(update={"threshold": 1.0})
    with pytest.raises(ValidationError, match="definition_sha256"):
        receipt.model_copy(update={"defined_at": LATER})


@pytest.mark.parametrize(
    "updates",
    (
        {"source_kind": "MEETING"},
        {"metric": "TTFT_P95_MS"},
        {"unit": "MILLISECONDS"},
        {"operator": "LT"},
        {"minimum_samples": 100.0},
        {"concurrency": "4"},
        {"defined_at": NOW.isoformat()},
        {"normalized_claim": "Contact raw.person@example.com."},
    ),
)
def test_receipt_boundary_rejects_coercion_and_unsafe_text(
    updates: dict[str, object],
):
    lookup = _Lookup()
    lookup.by_poc[POC_ALPHA] = (_review_item(),)
    receipt = _define(_service(lookup)).receipt
    payload = receipt.model_dump(mode="python")
    payload.update(updates)

    with pytest.raises(ValidationError):
        ContractDefinitionReceipt.model_validate(payload)


def test_receipt_has_only_definition_fields_and_no_downstream_authority():
    lookup = _Lookup()
    lookup.by_poc[POC_ALPHA] = (_review_item(),)
    receipt = _define(_service(lookup)).receipt
    fields = set(type(receipt).model_fields)
    forbidden = {
        "approved",
        "customer_confirmation",
        "contract_hash",
        "contract_status",
        "evidence",
        "freeze",
        "frozen",
        "run",
        "run_status",
        "verdict",
    }

    assert forbidden.isdisjoint(fields)
    serialized = json.dumps(receipt.model_dump(mode="json"), sort_keys=True)
    assert '"PASS"' not in serialized
    assert '"FROZEN"' not in serialized


def test_machine_readable_semantics_are_explicit_and_immutable():
    lookup = _Lookup()
    service = _service(
        lookup,
        max_proposals_per_poc=7,
        max_known_proposals=8,
        max_definitions=9,
        max_idempotency_records=10,
    )
    semantics = service.semantics

    assert semantics.storage_scope == "PROCESS_LOCAL"
    assert semantics.survives_process_restart is False
    assert semantics.shared_across_workers is False
    assert semantics.definition_is_customer_confirmation is False
    assert semantics.definition_is_frozen_contract is False
    assert semantics.can_confirm_contract is False
    assert semantics.can_freeze_contract is False
    assert semantics.can_execute_poc is False
    assert semantics.can_issue_evidence is False
    assert semantics.can_issue_verdict is False
    assert semantics.max_proposals_per_poc == 7
    assert semantics.max_known_proposals == 8
    assert semantics.max_definitions == 9
    assert semantics.max_idempotency_records == 10
    with pytest.raises(ValidationError):
        semantics.max_definitions = 10


@pytest.mark.parametrize(
    ("field", "maximum"),
    (
        ("max_proposals_per_poc", 8_192),
        ("max_known_proposals", 100_000),
        ("max_definitions", 100_000),
        ("max_idempotency_records", 100_000),
    ),
)
@pytest.mark.parametrize("bad", (0, -1, True, 1.0, "1"))
def test_capacity_configuration_is_exact_and_positive(
    field: str,
    maximum: int,
    bad: object,
):
    lookup = _Lookup()
    value = maximum + 1 if bad == "1" else bad
    with pytest.raises(ValueError, match=field):
        _service(lookup, **{field: value})


def test_proposal_and_definition_capacity_failures_are_atomic():
    lookup = _Lookup()
    second = _review_item(
        proposal_id="prop_error_rate_002",
        source_receipt_id="srcpt_document_002",
        source_kind=SourceKind.DOCUMENT,
        source_quote="Error rate must remain at or below one percent.",
        normalized_claim="Error rate must remain at or below one percent.",
    )
    lookup.by_poc[POC_ALPHA] = (_review_item(), second)
    proposal_limited = _service(lookup, max_proposals_per_poc=1)
    with pytest.raises(ContractDefinitionCapacityExceeded):
        _define(proposal_limited)
    assert len(proposal_limited) == 0

    lookup.by_poc[POC_ALPHA] = (_review_item(),)
    definition_limited = _service(lookup, max_definitions=1)
    _define(definition_limited)
    lookup.by_poc[POC_BETA] = (
        _review_item(
            poc_id=POC_BETA,
            proposal_id="prop_other_002",
            source_receipt_id="srcpt_other_002",
        ),
    )
    with pytest.raises(ContractDefinitionCapacityExceeded):
        _define(
            definition_limited,
            poc_id=POC_BETA,
            proposal_id="prop_other_002",
            idempotency_key="second-definition",
        )
    assert len(definition_limited) == 1


def test_idempotent_replay_still_works_at_capacity():
    lookup = _Lookup()
    lookup.by_poc[POC_ALPHA] = (_review_item(),)
    service = _service(
        lookup,
        max_definitions=1,
        max_idempotency_records=1,
    )
    created = _define(service)
    replay = _define(service)

    assert replay.replayed is True
    assert replay.receipt is created.receipt


def test_known_proposal_capacity_is_atomic():
    lookup = _Lookup()
    lookup.by_poc[POC_ALPHA] = (_review_item(),)
    service = _service(lookup, max_known_proposals=1)
    _define(service)
    lookup.by_poc[POC_BETA] = (
        _review_item(
            poc_id=POC_BETA,
            proposal_id="prop_other_002",
            source_receipt_id="srcpt_other_002",
        ),
    )
    with pytest.raises(ContractDefinitionCapacityExceeded):
        _define(
            service,
            poc_id=POC_BETA,
            proposal_id="prop_other_002",
            idempotency_key="known-capacity",
        )
    assert len(service) == 1


def test_naive_or_failing_clock_never_publishes_a_definition():
    lookup = _Lookup()
    lookup.by_poc[POC_ALPHA] = (_review_item(),)
    naive = _service(
        lookup,
        clock=lambda: datetime(2026, 7, 29, 9, 0),
    )
    with pytest.raises(ContractDefinitionLookupUnavailable):
        _define(naive)
    assert len(naive) == 0

    exploding = _service(
        lookup,
        clock=lambda: (_ for _ in ()).throw(RuntimeError("clock secret")),
    )
    with pytest.raises(ContractDefinitionLookupUnavailable) as caught:
        _define(exploding)
    assert "clock secret" not in str(caught.value)
    assert len(exploding) == 0


def test_concurrent_exact_replays_create_exactly_one_definition():
    lookup = _Lookup()
    lookup.by_poc[POC_ALPHA] = (_review_item(),)
    service = _service(lookup)

    with ThreadPoolExecutor(max_workers=16) as pool:
        results = tuple(pool.map(lambda _: _define(service), range(64)))

    assert sum(result.created for result in results) == 1
    assert sum(result.replayed for result in results) == 63
    assert all(result.receipt is results[0].receipt for result in results)
    assert len(service) == 1


def test_concurrent_different_keys_never_create_two_definitions():
    lookup = _Lookup()
    lookup.by_poc[POC_ALPHA] = (_review_item(),)
    service = _service(lookup)

    def define_once(index: int):
        try:
            return _define(
                service,
                criterion=_criterion(threshold=500.0 - index),
                idempotency_key="concurrent-{0}".format(index),
            )
        except ContractDefinitionConflict as error:
            return error

    with ThreadPoolExecutor(max_workers=16) as pool:
        outcomes = tuple(pool.map(define_once, range(32)))

    created = [
        outcome
        for outcome in outcomes
        if not isinstance(outcome, ContractDefinitionConflict)
    ]
    assert len(created) == 1
    assert created[0].created is True
    assert len(service) == 1


def test_ids_inputs_and_get_are_strictly_validated():
    lookup = _Lookup()
    lookup.by_poc[POC_ALPHA] = (_review_item(),)
    service = _service(lookup)

    for poc_id in ("invalid", "poc_ABCD", None, 123):
        with pytest.raises(ContractDefinitionInvalid):
            _define(service, poc_id=poc_id)  # type: ignore[arg-type]
    for proposal_id in ("invalid", "prop_AAAAAAAA", None, 123):
        with pytest.raises(ContractDefinitionInvalid):
            _define(service, proposal_id=proposal_id)  # type: ignore[arg-type]
    with pytest.raises(ContractDefinitionInvalid):
        service.define(
            POC_ALPHA,
            PROPOSAL_ID,
            {"threshold": 500},  # type: ignore[arg-type]
            idempotency_key="wrong-criterion-type",
        )
    with pytest.raises(ContractDefinitionInvalid):
        service.get("not-a-definition")
    with pytest.raises(ContractDefinitionProposalUnavailable):
        service.get("cdef_" + "0" * 32)
    assert len(service) == 0


def test_service_has_no_customer_confirmation_freeze_execution_or_verdict_api():
    service = ProcessLocalContractDefinitionService(
        proposal_lookup=lambda _: (),
    )
    for authority_method in (
        "approve",
        "confirm",
        "freeze",
        "run",
        "execute",
        "issue_evidence",
        "issue_verdict",
    ):
        assert not hasattr(service, authority_method)
