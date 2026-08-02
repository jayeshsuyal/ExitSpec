from datetime import datetime, timezone
from http import HTTPStatus

import pytest

from exitspec.poc_creation import (
    DraftPOCCreateRequest,
    ProcessLocalDraftPOCService,
)
from exitspec.poc_proposal_review import (
    ProcessLocalProposalReviewService,
)
from exitspec.poc_proposal_web_api import (
    handle_poc_proposal_web_api_request,
    is_poc_proposal_web_api_target,
)
from exitspec.poc_source_intake import ProcessLocalPOCSourceIntake


NOW = datetime(2026, 7, 28, 23, 0, tzinfo=timezone.utc)
POC_ID = "poc_proposal_web_alpha"
ROOT = f"/api/pocs/{POC_ID}/proposals"


def _services():
    drafts = ProcessLocalDraftPOCService(
        clock=lambda: NOW,
        poc_id_factory=lambda: POC_ID,
    )
    drafts.create(
        DraftPOCCreateRequest(
            display_name="Inference validation",
            customer_label="Northstar",
            use_case="Validate customer requirements.",
            owner="field_engineer",
            first_source_choice="DOCUMENT",
        ),
        idempotency_key="create-proposal-web",
    )
    intake = ProcessLocalPOCSourceIntake(
        draft_lookup=drafts.get,
        clock=lambda: NOW,
    )
    intake.capture_document(
        poc_id=POC_ID,
        document_text=(
            "The p95 latency must stay below 500 ms. "
            "Error rate must remain below 1%."
        ),
        idempotency_key="capture-proposal-web",
    )
    review = ProcessLocalProposalReviewService(
        proposal_lookup=intake.proposal_inputs,
        clock=lambda: NOW,
    )
    return drafts, intake, review


def _handle(runtime, method, target, payload=None):
    response = handle_poc_proposal_web_api_request(
        method=method,
        target=target,
        payload=payload,
        runtime=runtime,
    )
    assert response is not None
    return response


def test_unrelated_routes_are_not_claimed():
    _, _, runtime = _services()

    assert is_poc_proposal_web_api_target("/api/pocs") is False
    assert (
        handle_poc_proposal_web_api_request(
            method="GET",
            target=f"/api/pocs/{POC_ID}",
            payload=None,
            runtime=runtime,
        )
        is None
    )


def test_get_returns_only_current_redacted_needs_review_proposals():
    _, intake, runtime = _services()

    response = _handle(runtime, "GET", ROOT)

    assert response.status == HTTPStatus.OK
    assert response.payload["poc_id"] == POC_ID
    assert len(response.payload["proposals"]) == 2
    for proposal in response.payload["proposals"]:
        assert set(proposal) == {
            "normalized_claim",
            "proposal_id",
            "source_receipt_id",
            "source_kind",
            "source_quote",
            "review_state",
        }
        assert proposal["proposal_id"].startswith("prop_")
        assert proposal["source_receipt_id"].startswith("srcpt_")
        assert proposal["source_kind"] == "DOCUMENT"
        assert proposal["review_state"] == "NEEDS_REVIEW"
    serialized = repr(response.payload).lower()
    for forbidden in (
        "source_id",
        "candidate_id",
        "content_sha",
        "adapter_name",
        "external_id",
        "idempotency",
        "approved",
        "freeze",
        "verdict",
    ):
        assert forbidden not in serialized
    assert len(intake.proposal_inputs(POC_ID)) == 2


def test_keep_decision_is_triage_only_and_leaves_one_pending_proposal():
    _, _, runtime = _services()
    proposal = _handle(runtime, "GET", ROOT).payload["proposals"][0]
    target = f"{ROOT}/{proposal['proposal_id']}/decision"
    payload = {
        "decision": "KEEP_FOR_CONTRACT",
        "reviewer": "Jayesh",
        "rationale": "This is a measurable customer requirement.",
        "idempotency_key": "keep-proposal-web",
    }

    created = _handle(runtime, "POST", target, payload)
    replay = _handle(runtime, "POST", target, payload)
    remaining = _handle(runtime, "GET", ROOT)

    assert created.status == HTTPStatus.CREATED
    assert created.payload == {
        "decision": "KEEP_FOR_CONTRACT",
        "disposition": "CREATED",
        "poc_id": POC_ID,
        "proposal_id": proposal["proposal_id"],
        "review_state": "KEEP_FOR_CONTRACT",
    }
    assert replay.status == HTTPStatus.OK
    assert replay.payload == {
        **created.payload,
        "disposition": "IDEMPOTENT_REPLAY",
    }
    assert len(remaining.payload["proposals"]) == 1
    serialized = repr((created.payload, replay.payload)).lower()
    for forbidden in (
        "approved",
        "confirmation",
        "freeze",
        "execution",
        "evidence",
        "verdict",
        "pass",
    ):
        assert forbidden not in serialized


@pytest.mark.parametrize(
    "payload",
    (
        {},
        {
            "decision": "APPROVE",
            "reviewer": "Jayesh",
            "rationale": "No.",
            "idempotency_key": "bad-decision",
        },
        {
            "decision": "KEEP_FOR_CONTRACT",
            "reviewer": "Jayesh",
            "rationale": "No.",
            "idempotency_key": "extra-authority",
            "freeze": True,
        },
    ),
)
def test_payload_cannot_expand_triage_authority(payload):
    _, _, runtime = _services()
    proposal = _handle(runtime, "GET", ROOT).payload["proposals"][0]

    response = _handle(
        runtime,
        "POST",
        f"{ROOT}/{proposal['proposal_id']}/decision",
        payload,
    )

    assert response.status == HTTPStatus.BAD_REQUEST
    assert response.payload == {"error": "Proposal review request is invalid."}
    assert len(_handle(runtime, "GET", ROOT).payload["proposals"]) == 2


@pytest.mark.parametrize(
    "target",
    (
        f"{ROOT}?include=raw",
        f"{ROOT};provider=remote",
        "/api/pocs/poc_BAD/proposals",
        f"{ROOT}/prop_invalid/decision",
        f"{ROOT}/prop_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/approve",
    ),
)
def test_malformed_paths_and_parameters_fail_closed(target):
    _, _, runtime = _services()

    response = _handle(runtime, "GET", target)

    assert response.status == HTTPStatus.BAD_REQUEST
    assert response.payload == {"error": "Proposal review request is invalid."}


def test_unknown_cross_poc_proposal_and_conflict_are_safe():
    _, _, runtime = _services()
    proposals = _handle(runtime, "GET", ROOT).payload["proposals"]
    first = proposals[0]
    target = f"{ROOT}/{first['proposal_id']}/decision"
    _handle(
        runtime,
        "POST",
        target,
        {
            "decision": "DISCARD",
            "reviewer": "Jayesh",
            "rationale": "Outside the intended scope.",
            "idempotency_key": "discard-first",
        },
    )

    conflict = _handle(
        runtime,
        "POST",
        target,
        {
            "decision": "KEEP_FOR_CONTRACT",
            "reviewer": "Jayesh",
            "rationale": "Try to overwrite.",
            "idempotency_key": "overwrite-first",
        },
    )
    forged = _handle(
        runtime,
        "POST",
        f"{ROOT}/prop_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/decision",
        {
            "decision": "DISCARD",
            "reviewer": "Jayesh",
            "rationale": "Unknown proposal.",
            "idempotency_key": "unknown-proposal",
        },
    )

    assert conflict.status == HTTPStatus.CONFLICT
    assert conflict.payload == {
        "error": "Proposal review conflicts with the current POC state."
    }
    assert forged.status == HTTPStatus.NOT_FOUND
    assert forged.payload == {"error": "Proposal was not found."}


def test_method_contract_is_explicit():
    _, _, runtime = _services()

    response = _handle(runtime, "DELETE", ROOT)

    assert response.status == HTTPStatus.METHOD_NOT_ALLOWED
    assert response.payload == {"error": "Proposal review method is not allowed."}
