from __future__ import annotations

from datetime import datetime, timedelta, timezone
from http import HTTPStatus

import pytest

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
from exitspec.poc_performance_lifecycle import (
    ProcessLocalPerformanceLifecycleService,
)
from exitspec.poc_performance_lifecycle_web_api import (
    handle_performance_lifecycle_web_api_request,
    is_performance_lifecycle_web_api_target,
)
from exitspec.poc_proposal_review import (
    ProcessLocalProposalReviewService,
    ProposalDecision,
    SourceBoundProposal,
)


NOW = datetime(2026, 7, 29, 13, 0, tzinfo=timezone.utc)
POC_ID = "poc_agreement_api"
ROOT = f"/api/pocs/{POC_ID}/agreement"
PROMPTS = b'{"id":"agreement-api-001","content":"Explain TTFT briefly."}\n'


def _runtime(*, clock=None, source_store=None):
    draft_service = ProcessLocalDraftPOCService(
        clock=lambda: NOW,
        poc_id_factory=lambda: POC_ID,
    )
    draft_service.create(
        DraftPOCCreateRequest(
            display_name="Inference agreement",
            customer_label="Northstar",
            use_case="Validate latency and reliability.",
            owner="field_engineer",
            first_source_choice="MEETING",
        ),
        idempotency_key="create-agreement-api",
    )
    initial_source = (
        SourceBoundProposal(
            poc_id=POC_ID,
            proposal_id="prop_agreement_ttft_001",
            source_receipt_id="srcpt_agreement_api_001",
            source_kind="MEETING",
            source_quote="P95 TTFT must stay below 500 ms.",
            normalized_claim="P95 TTFT must stay below 500 ms.",
        ),
        SourceBoundProposal(
            poc_id=POC_ID,
            proposal_id="prop_agreement_error_002",
            source_receipt_id="srcpt_agreement_api_001",
            source_kind="MEETING",
            source_quote="Error rate must remain below 1 percent.",
            normalized_claim="Error rate must remain below 1 percent.",
        ),
    )
    source = list(initial_source) if source_store is None else source_store
    if source_store is not None:
        source_store.extend(initial_source)
    proposals = ProcessLocalProposalReviewService(
        proposal_lookup=lambda poc_id: tuple(source) if poc_id == POC_ID else (),
        clock=lambda: NOW,
    )
    for index, proposal in enumerate(source):
        proposals.decide(
            POC_ID,
            proposal.proposal_id,
            ProposalDecision.KEEP_FOR_CONTRACT,
            "Jayesh",
            "Keep this executable requirement.",
            f"keep-agreement-api-{index}",
        )
    definitions = ProcessLocalContractDefinitionService(
        proposal_lookup=proposals.list_proposals,
        clock=lambda: NOW,
    )
    common = {
        "minimum_samples": 100,
        "concurrency": 4,
        "prompt_tokens_min": 512,
        "prompt_tokens_max": 4096,
        "output_tokens_min": 64,
        "output_tokens_max": 64,
        "reviewer": "Jayesh",
        "rationale": "This exact definition is ready for agreement.",
    }
    definitions.define(
        POC_ID,
        source[0].proposal_id,
        InferencePerformanceCriterionDefinition(
            metric=InferencePerformanceMetric.TTFT_P95_MS,
            operator=ContractDefinitionOperator.LTE,
            threshold=500,
            **common,
        ),
        idempotency_key="define-agreement-api-ttft",
    )
    definitions.define(
        POC_ID,
        source[1].proposal_id,
        InferencePerformanceCriterionDefinition(
            metric=InferencePerformanceMetric.ERROR_RATE_PERCENT,
            operator=ContractDefinitionOperator.LT,
            threshold=1,
            **common,
        ),
        idempotency_key="define-agreement-api-error",
    )
    lifecycle = ProcessLocalPerformanceLifecycleService(
        draft_lookup=draft_service.get,
        proposal_lookup=proposals.list_proposals,
        definition_lookup=definitions.definitions,
        prompt_bytes=PROMPTS,
        clock=(lambda: NOW) if clock is None else clock,
    )
    return lifecycle, proposals, definitions


def _handle(
    runtime,
    method: str,
    target: str,
    payload=None,
):
    lifecycle, proposals, definitions = runtime
    response = handle_performance_lifecycle_web_api_request(
        method=method,
        target=target,
        payload=payload,
        lifecycle=lifecycle,
        proposals=proposals,
        definitions=definitions,
    )
    assert response is not None
    return response


def _prepare_body(**updates):
    payload = {
        "target_provider": "vllm-local",
        "endpoint_class": "openai-compatible-chat-completions",
        "endpoint": "http://127.0.0.1:8000/v1/chat/completions",
        "model": "Qwen/Qwen2.5-0.5B-Instruct",
        "reviewer": "Jayesh",
        "rationale": "This exact agreement is ready for customer review.",
        "idempotency_key": "prepare-agreement-api",
    }
    payload.update(updates)
    return payload


def test_prepare_customer_confirm_freeze_projects_only_exact_public_state():
    runtime = _runtime()
    lifecycle, _, _ = runtime
    before = _handle(runtime, "GET", ROOT)
    prepared = _handle(runtime, "POST", ROOT, _prepare_body())
    pending = _handle(runtime, "GET", ROOT)
    review_url = pending.payload["customer_review"]["review_url"]
    token = review_url.rsplit("/", 1)[-1]
    customer_view = lifecycle.customer_review_payload(token)
    confirmed = lifecycle.record_customer_review_decision(
        token,
        decision="CONFIRM",
        agreement_acknowledged=True,
        rationale="This exact displayed agreement is correct.",
        idempotency_key="confirm-agreement-api",
    )
    frozen = _handle(
        runtime,
        "POST",
        ROOT + "/freeze",
        {"idempotency_key": "freeze-agreement-api"},
    )
    after = _handle(runtime, "GET", ROOT)

    assert before.status == HTTPStatus.OK
    assert len(before.payload["definitions"]) == 2
    assert before.payload["draft"] is None
    assert before.payload["customer_review"] is None
    assert prepared.status == frozen.status == HTTPStatus.CREATED
    assert confirmed.replayed is False
    assert prepared.payload["draft"]["endpoint"] == (
        "http://127.0.0.1:8000/v1/chat/completions"
    )
    assert pending.payload["customer_review"]["status"] == "PENDING"
    assert review_url.startswith("/review/")
    assert customer_view["review"]["status"] == "PENDING"
    assert customer_view["review"]["contract_id"] == (
        customer_view["review"]["agreement"]["id"]
    )
    assert frozen.payload["frozen_contract"]["canonical_hash"]
    assert after.payload["draft"] == prepared.payload["draft"]
    assert after.payload["customer_review"]["status"] == "CONFIRMED"
    assert after.payload["confirmation"]["confirmation_id"] == (
        confirmed.value.confirmation_id
    )
    assert after.payload["confirmation"]["decision"] == "CONFIRM"
    assert after.payload["confirmation"]["agreement_acknowledged"] is True
    assert after.payload["frozen_contract"] == frozen.payload["frozen_contract"]
    assert set(after.payload) == {
        "poc_id",
        "definitions",
        "counting_policy",
        "not_proven_claims",
        "draft",
        "customer_review",
        "confirmation",
        "frozen_contract",
        "revision",
        "superseded_version_count",
    }
    assert after.payload["revision"] is None
    assert after.payload["superseded_version_count"] == 0
    assert after.payload["not_proven_claims"] == []
    assert after.payload["counting_policy"]["exact_attempts"] == 100
    assert after.payload["counting_policy"]["reliability_denominator"] == (
        "all_measured_attempts"
    )
    assert after.payload["counting_policy"]["external_error_outcomes"] == [
        "HTTP_ERROR",
        "TIMEOUT",
        "PROTOCOL_ERROR",
        "TRANSPORT_ERROR",
    ]


def test_external_evidence_method_is_projected_and_frozen_before_confirmation():
    runtime = _runtime()
    prepared = _handle(
        runtime,
        "POST",
        ROOT,
        _prepare_body(
            evidence_method="INFERDROME_EXTERNAL_BUNDLE",
            idempotency_key="prepare-inferdrome-agreement-api",
        ),
    )

    assert prepared.status == HTTPStatus.CREATED
    assert prepared.payload["draft"]["evidence_method"] == (
        "INFERDROME_EXTERNAL_BUNDLE"
    )
    snapshot = _handle(runtime, "GET", ROOT)
    assert snapshot.payload["draft"]["evidence_method"] == (
        "INFERDROME_EXTERNAL_BUNDLE"
    )
    assert snapshot.payload["frozen_contract"] is None


def test_every_write_exposes_exact_idempotent_replay():
    runtime = _runtime()
    body = _prepare_body()

    first = _handle(runtime, "POST", ROOT, body)
    replay = _handle(runtime, "POST", ROOT, body)

    assert first.status == HTTPStatus.CREATED
    assert replay.status == HTTPStatus.OK
    assert replay.payload["disposition"] == "IDEMPOTENT_REPLAY"
    assert replay.payload["draft"] == first.payload["draft"]


def test_requested_changes_open_a_source_backed_v2_without_mutating_v1():
    source_store = []
    runtime = _runtime(source_store=source_store)
    lifecycle, proposals, definitions = runtime
    _handle(runtime, "POST", ROOT, _prepare_body())
    initial = lifecycle.snapshot(POC_ID, allow_empty=False)
    assert initial.preparation is not None
    first_contract = initial.preparation.approved_contract
    first_draft_sha256 = initial.preparation.draft_sha256
    first_review_url = _handle(runtime, "GET", ROOT).payload[
        "customer_review"
    ]["review_url"]
    first_token = first_review_url.rsplit("/", 1)[-1]
    lifecycle.record_customer_review_decision(
        first_token,
        decision="REQUEST_CHANGES",
        agreement_acknowledged=False,
        rationale="Capture the corrected workload from the customer.",
        idempotency_key="request-agreement-api-revision",
    )

    started = _handle(
        runtime,
        "POST",
        ROOT + "/revision",
        {"idempotency_key": "start-agreement-api-revision"},
    )
    replay = _handle(
        runtime,
        "POST",
        ROOT + "/revision",
        {"idempotency_key": "start-agreement-api-revision"},
    )
    collecting = _handle(runtime, "GET", ROOT)

    assert started.status == HTTPStatus.CREATED
    assert replay.status == HTTPStatus.OK
    assert replay.payload["disposition"] == "IDEMPOTENT_REPLAY"
    assert replay.payload["revision"] == started.payload["revision"]
    assert started.payload["revision"]["contract_version"] == "2"
    assert started.payload["revision"]["parent_contract_id"] == first_contract.id
    assert started.payload["revision"]["parent_contract_version"] == "1"
    assert started.payload["revision"]["parent_draft_sha256"] == (
        first_draft_sha256
    )
    assert collecting.payload["definitions"] == []
    assert collecting.payload["draft"] is None
    assert collecting.payload["customer_review"] is None
    assert collecting.payload["confirmation"] is None
    assert collecting.payload["superseded_version_count"] == 1
    assert lifecycle.customer_review_poc_id(first_token) is None
    history = lifecycle.history(POC_ID)
    assert len(history) == 1
    assert history[0].preparation.approved_contract == first_contract
    assert history[0].confirmation.decision.value == "REQUEST_CHANGES"

    revised_source = (
        SourceBoundProposal(
            poc_id=POC_ID,
            proposal_id="prop_agreement_ttft_revision_003",
            source_receipt_id="srcpt_agreement_api_revision_002",
            source_kind="EMAIL",
            source_quote="Use p95 TTFT at or below 450 ms.",
            normalized_claim="Use p95 TTFT at or below 450 ms.",
        ),
        SourceBoundProposal(
            poc_id=POC_ID,
            proposal_id="prop_agreement_error_revision_004",
            source_receipt_id="srcpt_agreement_api_revision_002",
            source_kind="EMAIL",
            source_quote="Keep attempted-request errors below 1 percent.",
            normalized_claim="Keep attempted-request errors below 1 percent.",
        ),
    )
    source_store.extend(revised_source)
    for index, proposal in enumerate(revised_source):
        proposals.decide(
            POC_ID,
            proposal.proposal_id,
            ProposalDecision.KEEP_FOR_CONTRACT,
            "Jayesh",
            "Keep the customer-requested revision.",
            f"keep-agreement-api-revision-{index}",
        )
    revised_common = {
        "minimum_samples": 100,
        "concurrency": 4,
        "prompt_tokens_min": 512,
        "prompt_tokens_max": 4096,
        "output_tokens_min": 64,
        "output_tokens_max": 64,
        "reviewer": "Jayesh",
        "rationale": "Defined from the customer's revision source.",
    }
    definitions.define(
        POC_ID,
        revised_source[0].proposal_id,
        InferencePerformanceCriterionDefinition(
            metric=InferencePerformanceMetric.TTFT_P95_MS,
            operator=ContractDefinitionOperator.LTE,
            threshold=450,
            **revised_common,
        ),
        idempotency_key="define-agreement-api-revision-ttft",
    )
    definitions.define(
        POC_ID,
        revised_source[1].proposal_id,
        InferencePerformanceCriterionDefinition(
            metric=InferencePerformanceMetric.ERROR_RATE_PERCENT,
            operator=ContractDefinitionOperator.LT,
            threshold=1,
            **revised_common,
        ),
        idempotency_key="define-agreement-api-revision-error",
    )

    ready = _handle(runtime, "GET", ROOT)
    second = _handle(
        runtime,
        "POST",
        ROOT,
        _prepare_body(idempotency_key="prepare-agreement-api-v2"),
    )
    pending_v2 = _handle(runtime, "GET", ROOT)
    second_token = pending_v2.payload["customer_review"]["review_url"].rsplit(
        "/", 1
    )[-1]
    lifecycle.record_customer_review_decision(
        second_token,
        decision="CONFIRM",
        agreement_acknowledged=True,
        rationale="Version 2 matches the corrected customer requirement.",
        idempotency_key="confirm-agreement-api-v2",
    )
    frozen = _handle(
        runtime,
        "POST",
        ROOT + "/freeze",
        {"idempotency_key": "freeze-agreement-api-v2"},
    )

    assert len(ready.payload["definitions"]) == 2
    assert {item["threshold"] for item in ready.payload["definitions"]} == {
        1.0,
        450.0,
    }
    assert second.status == HTTPStatus.CREATED
    assert second.payload["draft"]["contract_id"] == first_contract.id
    assert second.payload["draft"]["contract_version"] == "2"
    assert second.payload["draft"]["parent_version"] == (
        f"{first_contract.id}@1"
    )
    assert frozen.payload["frozen_contract"]["contract_version"] == "2"
    assert frozen.payload["frozen_contract"]["parent_version"] == (
        f"{first_contract.id}@1"
    )
    assert lifecycle.history(POC_ID)[0].preparation.approved_contract == (
        first_contract
    )


def test_expired_customer_review_can_be_reissued_idempotently():
    current = [NOW]
    runtime = _runtime(clock=lambda: current[0])
    _handle(runtime, "POST", ROOT, _prepare_body())
    original = _handle(runtime, "GET", ROOT).payload["customer_review"]
    current[0] = NOW + timedelta(hours=2, microseconds=1)

    expired = _handle(runtime, "GET", ROOT)
    first = _handle(
        runtime,
        "POST",
        ROOT + "/review",
        {"idempotency_key": "reissue-agreement-review"},
    )
    replay = _handle(
        runtime,
        "POST",
        ROOT + "/review",
        {"idempotency_key": "reissue-agreement-review"},
    )

    assert expired.payload["customer_review"]["status"] == "EXPIRED"
    assert first.status == HTTPStatus.CREATED
    assert first.payload["customer_review"]["status"] == "PENDING"
    assert first.payload["customer_review"]["review_url"] != original["review_url"]
    assert replay.status == HTTPStatus.OK
    assert replay.payload["disposition"] == "IDEMPOTENT_REPLAY"
    assert replay.payload["customer_review"] == first.payload["customer_review"]


@pytest.mark.parametrize(
    "body",
    (
        {},
        {**_prepare_body(), "verdict": "PASS"},
        {**_prepare_body(), "endpoint": "https://user:secret@example.com/v1"},
        {**_prepare_body(), "idempotency_key": True},
    ),
)
def test_prepare_body_is_exact_and_cannot_claim_authority(body):
    response = _handle(_runtime(), "POST", ROOT, body)

    assert response.status == HTTPStatus.BAD_REQUEST
    assert response.payload == {"error": "Performance agreement request is invalid."}


def test_direct_confirmation_is_unavailable_and_freeze_fails_closed_out_of_order():
    runtime = _runtime()

    confirmation = _handle(
        runtime,
        "POST",
        ROOT + "/confirm",
        {
            "confirmer": "Customer approver",
            "agreement_acknowledged": True,
            "rationale": "Confirm.",
            "idempotency_key": "confirm-before-prepare",
        },
    )
    freeze = _handle(
        runtime,
        "POST",
        ROOT + "/freeze",
        {"idempotency_key": "freeze-before-prepare"},
    )

    assert confirmation.status == HTTPStatus.BAD_REQUEST
    assert confirmation.payload == {
        "error": "Performance agreement request is invalid."
    }
    assert freeze.status == HTTPStatus.NOT_FOUND


@pytest.mark.parametrize(
    ("method", "target", "payload", "status"),
    (
        ("GET", ROOT + "?next=freeze", None, HTTPStatus.BAD_REQUEST),
        ("GET", ROOT + "/unknown", None, HTTPStatus.BAD_REQUEST),
        ("POST", ROOT + "/confirm", {}, HTTPStatus.BAD_REQUEST),
        ("PATCH", ROOT, None, HTTPStatus.METHOD_NOT_ALLOWED),
    ),
)
def test_namespace_and_methods_fail_closed(method, target, payload, status):
    response = _handle(_runtime(), method, target, payload)
    assert response.status == status


def test_unrelated_routes_are_not_claimed():
    runtime = _runtime()
    lifecycle, proposals, definitions = runtime

    assert not is_performance_lifecycle_web_api_target("/api/pocs")
    assert (
        handle_performance_lifecycle_web_api_request(
            method="GET",
            target="/api/pocs",
            payload=None,
            lifecycle=lifecycle,
            proposals=proposals,
            definitions=definitions,
        )
        is None
    )
