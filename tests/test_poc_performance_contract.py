from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from exitspec.confirmations import ConfirmationDecision, record_confirmation
from exitspec.contracts import freeze_confirmed_contract
from exitspec.models import ContractStatus
from exitspec.performance_evidence import (
    require_frozen_confirmed,
    validate_performance_context_bytes,
)
from exitspec.poc_contract_definition import (
    ContractDefinitionOperator,
    InferencePerformanceCriterionDefinition,
    InferencePerformanceMetric,
    ProcessLocalContractDefinitionService,
)
from exitspec.poc_creation import (
    DraftPOCCreateRequest,
    ProcessLocalDraftPOCService,
)
from exitspec.poc_performance_contract import (
    ADAPTER,
    PerformanceContractAssemblyError,
    PerformanceTargetInput,
    prepare_performance_bundle,
)
from exitspec.poc_proposal_review import (
    ProcessLocalProposalReviewService,
    ProposalDecision,
    SourceBoundProposal,
)


NOW = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)
POC_ID = "poc_contract_bundle"
PROMPTS = (
    b'{"id":"latency-001","content":"Explain bounded retries briefly."}\n'
    b'{"id":"latency-002","content":"Return a small health JSON object."}\n'
)


def _inputs(
    *,
    ttft_operator: ContractDefinitionOperator = ContractDefinitionOperator.LTE,
    ttft_samples: int = 95,
    error_samples: int = 100,
    ttft_concurrency: int = 4,
    error_concurrency: int = 4,
    ttft_prompt_max: int = 4096,
    error_prompt_max: int = 4096,
):
    draft_service = ProcessLocalDraftPOCService(
        clock=lambda: NOW,
        poc_id_factory=lambda: POC_ID,
    )
    draft = draft_service.create(
        DraftPOCCreateRequest(
            display_name="Inference proof",
            customer_label="Northstar",
            use_case="Validate bounded streaming latency and reliability.",
            owner="field_engineer",
            first_source_choice="MEETING",
        ),
        idempotency_key="create-contract-bundle",
    ).draft
    source = (
        SourceBoundProposal(
            poc_id=POC_ID,
            proposal_id="prop_ttft_bundle_001",
            source_receipt_id="srcpt_bundle_001",
            source_kind="MEETING",
            source_quote="P95 time to first token must stay below 500 ms.",
            normalized_claim="P95 TTFT must stay below 500 ms.",
        ),
        SourceBoundProposal(
            poc_id=POC_ID,
            proposal_id="prop_error_bundle_002",
            source_receipt_id="srcpt_bundle_001",
            source_kind="MEETING",
            source_quote="Error rate must remain below 1 percent.",
            normalized_claim="Error rate must remain below 1 percent.",
        ),
    )
    review = ProcessLocalProposalReviewService(
        proposal_lookup=lambda poc_id: source if poc_id == POC_ID else (),
        clock=lambda: NOW,
    )
    for index, proposal in enumerate(source):
        review.decide(
            POC_ID,
            proposal.proposal_id,
            ProposalDecision.KEEP_FOR_CONTRACT,
            "Jayesh",
            "Keep this exact executable customer requirement.",
            "keep-contract-bundle-{0}".format(index),
        )
    definitions = ProcessLocalContractDefinitionService(
        proposal_lookup=review.list_proposals,
        clock=lambda: NOW,
    )
    common = {
        "prompt_tokens_min": 512,
        "output_tokens_min": 64,
        "output_tokens_max": 64,
        "reviewer": "Jayesh",
        "rationale": "This exact boundary is ready for agreement assembly.",
    }
    definitions.define(
        POC_ID,
        source[0].proposal_id,
        InferencePerformanceCriterionDefinition(
            metric=InferencePerformanceMetric.TTFT_P95_MS,
            operator=ttft_operator,
            threshold=500,
            minimum_samples=ttft_samples,
            concurrency=ttft_concurrency,
            prompt_tokens_max=ttft_prompt_max,
            **common,
        ),
        idempotency_key="define-contract-ttft",
    )
    definitions.define(
        POC_ID,
        source[1].proposal_id,
        InferencePerformanceCriterionDefinition(
            metric=InferencePerformanceMetric.ERROR_RATE_PERCENT,
            operator=ContractDefinitionOperator.LT,
            threshold=1,
            minimum_samples=error_samples,
            concurrency=error_concurrency,
            prompt_tokens_max=error_prompt_max,
            **common,
        ),
        idempotency_key="define-contract-error",
    )
    return draft, review.list_proposals(POC_ID), definitions.definitions()


def _target(**updates) -> PerformanceTargetInput:
    payload = {
        "provider": "vllm-local",
        "endpoint_class": "openai-compatible-chat-completions",
        "endpoint": "http://127.0.0.1:8000/v1/chat/completions",
        "model": "Qwen/Qwen2.5-0.5B-Instruct",
    }
    payload.update(updates)
    return PerformanceTargetInput(**payload)


def _bundle(**input_updates):
    draft, proposals, definitions = _inputs(**input_updates)
    return prepare_performance_bundle(
        draft=draft,
        proposals=proposals,
        definitions=definitions,
        target=_target(),
        prompt_bytes=PROMPTS,
        prepared_at=NOW,
    )


@pytest.mark.parametrize(
    "operator",
    (ContractDefinitionOperator.LT, ContractDefinitionOperator.LTE),
)
def test_exact_ttft_operator_survives_runner_valid_assembly(operator):
    bundle = _bundle(ttft_operator=operator)
    criterion = bundle.approved_contract.criteria[0]

    assert bundle.approved_contract.status is ContractStatus.APPROVED
    assert criterion.ttft_p95.operator == operator.value.lower()
    assert criterion.error_rate.operator == "lt"
    assert criterion.error_rate.threshold == 0.01
    assert bundle.workload.adapter == ADAPTER
    assert bundle.workload.request_count == 100
    assert bundle.workload.concurrency == 4
    assert bundle.context.contract == bundle.approved_contract
    assert len(bundle.definition_bindings) == 2
    assert all("PASS" not in item for item in bundle.planning_limitations)


def test_bundle_is_deterministic_and_binds_exact_bytes():
    first = _bundle()
    second = _bundle()

    assert first.bundle_fingerprint == second.bundle_fingerprint
    assert first.approved_contract == second.approved_contract
    assert first.workload_bytes == second.workload_bytes
    assert first.prompt_bytes == second.prompt_bytes == PROMPTS
    assert first.approved_contract.workload.sha256 == first.context.workload_sha256


def test_existing_confirmation_and_freeze_primitives_accept_exact_bundle():
    bundle = _bundle()
    confirmation = record_confirmation(
        bundle.approved_contract,
        confirmer_identity="Customer approver",
        decision=ConfirmationDecision.CONFIRM,
        agreement_acknowledged=True,
        rationale="This exact target and acceptance boundary are correct.",
        idempotency_key="confirm-prepared-performance-bundle",
        decided_at=NOW,
    )
    frozen = freeze_confirmed_contract(
        bundle.approved_contract,
        confirmation,
        frozen_at=NOW,
    )
    context = validate_performance_context_bytes(
        frozen,
        bundle.workload_bytes,
        bundle.prompt_bytes,
    )

    assert frozen.status is ContractStatus.FROZEN
    assert frozen.canonical_hash
    assert require_frozen_confirmed(context, confirmation) is context


@pytest.mark.parametrize(
    "updates",
    (
        {"ttft_samples": 101, "error_samples": 100},
        {"ttft_concurrency": 4, "error_concurrency": 8},
        {"ttft_prompt_max": 4096, "error_prompt_max": 2048},
    ),
)
def test_mismatched_definition_pair_fails_closed(updates):
    draft, proposals, definitions = _inputs(**updates)

    with pytest.raises(PerformanceContractAssemblyError):
        prepare_performance_bundle(
            draft=draft,
            proposals=proposals,
            definitions=definitions,
            target=_target(),
            prompt_bytes=PROMPTS,
            prepared_at=NOW,
        )


def test_missing_definition_and_cross_poc_inputs_fail_closed():
    draft, proposals, definitions = _inputs()

    with pytest.raises(PerformanceContractAssemblyError, match="two"):
        prepare_performance_bundle(
            draft=draft,
            proposals=proposals,
            definitions=definitions[:1],
            target=_target(),
            prompt_bytes=PROMPTS,
            prepared_at=NOW,
        )
    foreign_draft = draft.model_copy(update={"poc_id": "poc_foreign_bundle"})
    with pytest.raises(PerformanceContractAssemblyError, match="crosses"):
        prepare_performance_bundle(
            draft=foreign_draft,
            proposals=proposals,
            definitions=definitions,
            target=_target(),
            prompt_bytes=PROMPTS,
            prepared_at=NOW,
        )


def test_prompt_tamper_cannot_validate_against_prepared_contract():
    bundle = _bundle()

    with pytest.raises(ValueError, match="Prompt fixture bytes"):
        validate_performance_context_bytes(
            bundle.approved_contract,
            bundle.workload_bytes,
            PROMPTS + b" ",
        )


@pytest.mark.parametrize(
    "endpoint",
    (
        "ftp://example.com/v1/chat/completions",
        "https://user:secret@example.com/v1/chat/completions",
        "https://example.com/v1/chat/completions?model=other",
        " https://example.com/v1/chat/completions",
    ),
)
def test_target_endpoint_is_exact_and_cannot_carry_credentials(endpoint):
    with pytest.raises(ValidationError):
        _target(endpoint=endpoint)


def test_token_ranges_are_fingerprinted_as_explicit_non_goals():
    bundle = _bundle()
    serialized = bundle.approved_contract.model_dump_json()

    assert "planning context only" in serialized
    assert "does not prove a token distribution" in serialized
    assert "Output minimum 64 tokens is not measured" in serialized
    assert bundle.approved_contract.canonical_hash is None
    assert bundle.approved_contract.confirmation_id is None
