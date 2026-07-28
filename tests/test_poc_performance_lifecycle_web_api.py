from __future__ import annotations

from datetime import datetime, timezone
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


def _runtime():
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
    source = (
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
    proposals = ProcessLocalProposalReviewService(
        proposal_lookup=lambda poc_id: source if poc_id == POC_ID else (),
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
        clock=lambda: NOW,
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


def test_prepare_confirm_freeze_projects_only_exact_public_state():
    runtime = _runtime()
    before = _handle(runtime, "GET", ROOT)
    prepared = _handle(runtime, "POST", ROOT, _prepare_body())
    confirmed = _handle(
        runtime,
        "POST",
        ROOT + "/confirm",
        {
            "confirmer": "Customer approver",
            "agreement_acknowledged": True,
            "rationale": "This exact displayed agreement is correct.",
            "idempotency_key": "confirm-agreement-api",
        },
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
    assert prepared.status == confirmed.status == frozen.status == HTTPStatus.CREATED
    assert prepared.payload["draft"]["endpoint"] == (
        "http://127.0.0.1:8000/v1/chat/completions"
    )
    assert confirmed.payload["confirmation"]["agreement_acknowledged"] is True
    assert frozen.payload["frozen_contract"]["canonical_hash"]
    assert after.payload["draft"] == prepared.payload["draft"]
    assert after.payload["confirmation"] == confirmed.payload["confirmation"]
    assert after.payload["frozen_contract"] == frozen.payload["frozen_contract"]
    assert set(after.payload) == {
        "poc_id",
        "definitions",
        "draft",
        "confirmation",
        "frozen_contract",
    }


def test_every_write_exposes_exact_idempotent_replay():
    runtime = _runtime()
    body = _prepare_body()

    first = _handle(runtime, "POST", ROOT, body)
    replay = _handle(runtime, "POST", ROOT, body)

    assert first.status == HTTPStatus.CREATED
    assert replay.status == HTTPStatus.OK
    assert replay.payload["disposition"] == "IDEMPOTENT_REPLAY"
    assert replay.payload["draft"] == first.payload["draft"]


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


def test_confirmation_and_freeze_fail_closed_out_of_order():
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

    assert confirmation.status == freeze.status == HTTPStatus.NOT_FOUND


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
