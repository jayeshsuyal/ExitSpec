from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from exitspec.confirmations import (
    ConfirmationDecision,
    canonical_confirmation_payload,
    contract_confirmation_fingerprint,
    record_confirmation,
)
from exitspec.contracts import freeze_confirmed_contract
from exitspec.customer_review import build_customer_review_payload
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
    INFERDROME_ADAPTER,
    PerformanceContractAssemblyError,
    PerformanceEvidenceMethod,
    PerformanceTargetInput,
    prepare_performance_bundle,
)
from exitspec.poc_performance_lifecycle import (
    PerformanceLifecycleConflict,
    PerformanceLifecycleInvalid,
    ProcessLocalPerformanceLifecycleService,
)
from exitspec.poc_proposal_review import (
    ProcessLocalProposalReviewService,
    ProposalDecision,
    SourceBoundProposal,
)
from exitspec.review_links import ReviewInvitationError


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


def _lifecycle_service(*, clock=None):
    draft, proposals, definitions = _inputs()
    current_proposals = list(proposals)
    current_definitions = list(definitions)
    service = ProcessLocalPerformanceLifecycleService(
        draft_lookup=lambda poc_id: draft
        if poc_id == POC_ID
        else (_ for _ in ()).throw(KeyError(poc_id)),
        proposal_lookup=lambda poc_id: tuple(current_proposals)
        if poc_id == POC_ID
        else (),
        definition_lookup=lambda: tuple(current_definitions),
        prompt_bytes=PROMPTS,
        clock=(lambda: NOW) if clock is None else clock,
    )
    return service, current_proposals, current_definitions


def _review_token(
    service: ProcessLocalPerformanceLifecycleService,
) -> str:
    review_url = service.customer_review_url(POC_ID)
    assert review_url.startswith("/review/")
    return review_url.rsplit("/", 1)[-1]


def _confirm_through_customer_review(
    service: ProcessLocalPerformanceLifecycleService,
    *,
    idempotency_key: str,
    rationale: str = "The exact target and criteria were reviewed.",
):
    return service.record_customer_review_decision(
        _review_token(service),
        decision="CONFIRM",
        agreement_acknowledged=True,
        rationale=rationale,
        idempotency_key=idempotency_key,
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
    assert criterion.criterion_type == "inference_performance_v2"
    assert criterion.measurement_policy.measured_population.exact_attempts == 100
    assert criterion.measurement_policy.reliability.denominator == (
        "all_measured_attempts"
    )
    assert bundle.workload.adapter == ADAPTER
    assert bundle.workload.request_count == 100
    assert bundle.workload.concurrency == 4
    assert bundle.context.contract == bundle.approved_contract
    assert bundle.context.expected_manifest.measurement_policy is not None
    assert len(bundle.definition_bindings) == 2
    assert all("PASS" not in item for item in bundle.planning_limitations)


def test_inferdrome_method_freezes_external_adapter_without_metric_equivalence():
    draft, proposals, definitions = _inputs()
    bundle = prepare_performance_bundle(
        draft=draft,
        proposals=proposals,
        definitions=definitions,
        target=_target(
            evidence_method=(
                PerformanceEvidenceMethod.INFERDROME_EXTERNAL_BUNDLE
            )
        ),
        prompt_bytes=PROMPTS,
        prepared_at=NOW,
    )

    criterion = bundle.approved_contract.criteria[0]
    assert bundle.workload.adapter == INFERDROME_ADAPTER
    assert criterion.adapter == INFERDROME_ADAPTER
    assert bundle.workload.first_token_definition == (
        "first_nonempty_choices_delta_content_v1"
    )
    assert any(
        "first-choices-event TTFT is not equivalent" in limitation
        for limitation in bundle.approved_contract.non_goals
    )


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


def test_prepare_issues_opaque_review_bound_to_exact_contract_fingerprint():
    service, _, _ = _lifecycle_service()
    prepared = service.prepare(
        POC_ID,
        target=_target(),
        reviewer="Jayesh",
        rationale="Prepare the exact agreement for external review.",
        idempotency_key="prepare-bound-review-capability",
    )
    snapshot = service.snapshot(POC_ID)
    invitation = snapshot.review_invitation
    assert invitation is not None
    token = _review_token(service)
    customer_view = service.customer_review_payload(token)
    contract = prepared.value.approved_contract

    assert POC_ID not in token
    assert contract.id not in token
    assert invitation.accepts(token, now=NOW)
    assert invitation.contract_id == contract.id
    assert invitation.contract_version == contract.version
    assert invitation.confirmation_fingerprint == (
        contract_confirmation_fingerprint(contract)
    )
    assert customer_view["review"]["confirmation_fingerprint"] == (
        invitation.confirmation_fingerprint
    )
    assert customer_view["review"]["agreement"] == (
        canonical_confirmation_payload(contract)
    )
    assert customer_view["review"]["evidence_method"] == (
        "EXIT_SPEC_STREAMING_PROBE"
    )
    criterion = customer_view["review"]["criteria"][0]
    assert criterion["metric"] == "P95 time to first token and error rate"
    assert criterion["threshold"] == (
        "P95 TTFT at most 500 ms · error rate below 1%"
    )
    assert criterion["sample"] == (
        "95 successful timing samples · 100 attempted requests"
    )
    assert customer_view["safety"]["not_evidence"] is True
    assert customer_view["safety"]["not_production_authorization"] is True

    with pytest.raises(ReviewInvitationError, match="does not match"):
        build_customer_review_payload(
            invitation=invitation,
            contract=contract,
            confirmation=None,
            evidence_method=(
                PerformanceEvidenceMethod.INFERDROME_EXTERNAL_BUNDLE
            ),
            poc_id=POC_ID,
            return_url=f"/app/pocs/{POC_ID}/agreement",
            execution_endpoint=_target().endpoint,
        )


def test_invalid_and_foreign_review_capabilities_fail_closed():
    service, _, _ = _lifecycle_service()
    service.prepare(
        POC_ID,
        target=_target(),
        reviewer="Jayesh",
        rationale="Prepare the local capability.",
        idempotency_key="prepare-local-review-capability",
    )
    other, _, _ = _lifecycle_service()
    other.prepare(
        POC_ID,
        target=_target(),
        reviewer="Jayesh",
        rationale="Prepare a different process-local capability.",
        idempotency_key="prepare-foreign-review-capability",
    )

    with pytest.raises(ReviewInvitationError, match="invalid"):
        service.customer_review_payload("not-a-review-capability")
    with pytest.raises(ReviewInvitationError, match="invalid"):
        service.customer_review_payload(_review_token(other))


def test_expired_review_can_be_reissued_without_changing_the_agreement():
    current = [NOW]
    service, _, _ = _lifecycle_service(clock=lambda: current[0])
    prepared = service.prepare(
        POC_ID,
        target=_target(),
        reviewer="Jayesh",
        rationale="Prepare one expiring capability.",
        idempotency_key="prepare-expiring-review",
    )
    old_url = service.customer_review_url(POC_ID)
    old_token = old_url.rsplit("/", 1)[-1]
    current[0] = NOW + timedelta(hours=2, microseconds=1)

    assert service.customer_review_expired(POC_ID) is True
    with pytest.raises(ReviewInvitationError, match="expired"):
        service.customer_review_payload(old_token)

    reissued = service.reissue_customer_review(
        POC_ID,
        idempotency_key="reissue-expired-review",
    )
    replay = service.reissue_customer_review(
        POC_ID,
        idempotency_key="reissue-expired-review",
    )
    new_url = service.customer_review_url(POC_ID)

    assert reissued.replayed is False
    assert replay.replayed is True
    assert replay.value is reissued.value
    assert new_url != old_url
    assert service.customer_review_payload(new_url.rsplit("/", 1)[-1])["review"][
        "agreement"
    ] == canonical_confirmation_payload(prepared.value.approved_contract)
    with pytest.raises(ReviewInvitationError, match="invalid"):
        service.customer_review_payload(old_token)

    current[0] += timedelta(hours=2, microseconds=1)
    with pytest.raises(PerformanceLifecycleConflict, match="no longer current"):
        service.reissue_customer_review(
            POC_ID,
            idempotency_key="reissue-expired-review",
        )


def test_customer_decision_uses_one_timestamp_for_expiry_and_receipt():
    before_expiry = NOW + timedelta(hours=2, microseconds=-1)
    calls = 0

    def clock():
        nonlocal calls
        calls += 1
        return NOW if calls == 1 else before_expiry

    service, _, _ = _lifecycle_service(clock=clock)
    service.prepare(
        POC_ID,
        target=_target(),
        reviewer="Jayesh",
        rationale="Prepare the boundary-time review.",
        idempotency_key="prepare-boundary-time-review",
    )
    confirmation = service.record_customer_review_decision(
        _review_token(service),
        decision="CONFIRM",
        agreement_acknowledged=True,
        rationale=None,
        idempotency_key="confirm-boundary-time-review",
    )

    assert confirmation.value.decided_at == before_expiry
    assert calls == 2


def test_process_local_lifecycle_reuses_proven_confirmation_and_freeze():
    service, _, _ = _lifecycle_service()

    prepared = service.prepare(
        POC_ID,
        target=_target(),
        reviewer="Jayesh",
        rationale="This exact agreement is ready for customer review.",
        idempotency_key="prepare-lifecycle-001",
    )
    confirmed = _confirm_through_customer_review(
        service,
        rationale="This exact target and requirement pair are correct.",
        idempotency_key="confirm-lifecycle-001",
    )
    frozen = service.freeze(
        POC_ID,
        idempotency_key="freeze-lifecycle-001",
    )
    bundle, confirmation, frozen_contract = service.frozen_bundle(POC_ID)

    assert prepared.replayed is False
    assert confirmed.replayed is False
    assert frozen.replayed is False
    assert confirmation is confirmed.value
    assert frozen_contract is frozen.value
    assert frozen_contract.status is ContractStatus.FROZEN
    assert frozen_contract.confirmation_id == confirmation.confirmation_id
    assert bundle.bundle_fingerprint == prepared.value.bundle.bundle_fingerprint
    assert service.snapshot(POC_ID).frozen_contract is frozen_contract


def test_every_lifecycle_write_is_exactly_idempotent():
    service, _, _ = _lifecycle_service()
    prepare_arguments = {
        "target": _target(),
        "reviewer": "Jayesh",
        "rationale": "This exact agreement is ready for customer review.",
        "idempotency_key": "prepare-lifecycle-replay",
    }
    first_prepare = service.prepare(POC_ID, **prepare_arguments)
    replay_prepare = service.prepare(POC_ID, **prepare_arguments)
    confirmation_key = "confirm-lifecycle-replay"
    first_confirmation = _confirm_through_customer_review(
        service,
        rationale="Confirmed exactly as displayed.",
        idempotency_key=confirmation_key,
    )
    replay_confirmation = _confirm_through_customer_review(
        service,
        rationale="Confirmed exactly as displayed.",
        idempotency_key=confirmation_key,
    )
    first_freeze = service.freeze(
        POC_ID,
        idempotency_key="freeze-lifecycle-replay",
    )
    replay_freeze = service.freeze(
        POC_ID,
        idempotency_key="freeze-lifecycle-replay",
    )

    assert replay_prepare.replayed is True
    assert replay_prepare.value is first_prepare.value
    assert replay_confirmation.replayed is True
    assert replay_confirmation.value is first_confirmation.value
    assert replay_freeze.replayed is True
    assert replay_freeze.value is first_freeze.value


def test_freeze_requires_exact_affirmative_customer_confirmation():
    service, _, _ = _lifecycle_service()
    service.prepare(
        POC_ID,
        target=_target(),
        reviewer="Jayesh",
        rationale="Prepared for exact customer review.",
        idempotency_key="prepare-before-freeze",
    )

    with pytest.raises(PerformanceLifecycleConflict, match="confirmation"):
        service.freeze(
            POC_ID,
            idempotency_key="freeze-without-confirmation",
        )
    with pytest.raises(PerformanceLifecycleInvalid, match="acknowledgement"):
        service.record_customer_review_decision(
            _review_token(service),
            decision="CONFIRM",
            agreement_acknowledged=False,
            rationale="No acknowledgement.",
            idempotency_key="reject-missing-acknowledgement",
        )


def test_request_changes_is_terminal_and_cannot_be_frozen():
    service, _, _ = _lifecycle_service()
    service.prepare(
        POC_ID,
        target=_target(),
        reviewer="Jayesh",
        rationale="Prepared for customer review.",
        idempotency_key="prepare-before-change-request",
    )
    changed = service.record_customer_review_decision(
        _review_token(service),
        decision="REQUEST_CHANGES",
        agreement_acknowledged=False,
        rationale="The workload does not match the customer call.",
        idempotency_key="request-performance-agreement-changes",
    )

    assert changed.value.decision is ConfirmationDecision.REQUEST_CHANGES
    assert service.snapshot(POC_ID).confirmation is changed.value
    with pytest.raises(PerformanceLifecycleConflict, match="requested changes"):
        service.freeze(
            POC_ID,
            idempotency_key="reject-freeze-after-change-request",
        )


def test_customer_decision_idempotency_cannot_change_payload_or_authority():
    service, _, _ = _lifecycle_service()
    service.prepare(
        POC_ID,
        target=_target(),
        reviewer="Jayesh",
        rationale="Prepared for exact customer review.",
        idempotency_key="prepare-before-review-replay",
    )
    token = _review_token(service)
    first = service.record_customer_review_decision(
        token,
        decision="CONFIRM",
        agreement_acknowledged=True,
        rationale="Confirm this exact agreement.",
        idempotency_key="customer-review-replay",
    )
    replay = service.record_customer_review_decision(
        token,
        decision="CONFIRM",
        agreement_acknowledged=True,
        rationale="Confirm this exact agreement.",
        idempotency_key="customer-review-replay",
    )

    assert first.replayed is False
    assert replay.replayed is True
    assert replay.value is first.value
    with pytest.raises(PerformanceLifecycleConflict, match="Idempotency"):
        service.record_customer_review_decision(
            token,
            decision="REQUEST_CHANGES",
            agreement_acknowledged=False,
            rationale="Attempt to mutate the terminal decision.",
            idempotency_key="customer-review-replay",
        )


def test_lifecycle_refuses_stale_upstream_and_conflicting_replays():
    service, _, definitions = _lifecycle_service()
    service.prepare(
        POC_ID,
        target=_target(),
        reviewer="Jayesh",
        rationale="Prepared before upstream mutation.",
        idempotency_key="prepare-stale-lifecycle",
    )

    definitions.pop()
    with pytest.raises(PerformanceLifecycleConflict, match="cannot form"):
        service.snapshot(POC_ID)

    other, _, _ = _lifecycle_service()
    other.prepare(
        POC_ID,
        target=_target(),
        reviewer="Jayesh",
        rationale="Original request.",
        idempotency_key="prepare-conflict-lifecycle",
    )
    with pytest.raises(PerformanceLifecycleConflict, match="Idempotency"):
        other.prepare(
            POC_ID,
            target=_target(),
            reviewer="Jayesh",
            rationale="Changed request.",
            idempotency_key="prepare-conflict-lifecycle",
        )
