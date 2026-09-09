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


@pytest.mark.parametrize("origin", ["INTAKE_A2", "ASSISTED_A3"])
def test_review_provenance_manifest_accounts_for_pending_kept_and_discarded(origin):
    drafts, _, runtime = _services()
    items = runtime.list_proposals(POC_ID)
    if origin == "ASSISTED_A3":
        with runtime.authoring_commit_guard(POC_ID, items[0].source_receipt_id) as guard:
            guard.prepare([item.proposal_id for item in items])
            guard.commit()
    expected = {
        "schema_version": "exitspec.review-authoring-provenance/1",
        "proposals": [{
            "proposal_id": item.proposal_id, "origin": origin,
            "review_state": "NEEDS_REVIEW", "normalized_claim": item.normalized_claim,
        } for item in items],
    }
    before_draft = drafts.get(POC_ID)
    for index, decision in enumerate(("KEEP_FOR_CONTRACT", "DISCARD")):
        listed = _handle(runtime, "GET", ROOT)
        assert listed.payload["authoring_provenance"] == expected
        assert len(runtime) == index
        response = _handle(runtime, "POST", f"{ROOT}/{items[index].proposal_id}/decision", {
            "decision": decision, "reviewer": "named.provenance", "rationale": "Review this exact source material.",
            "idempotency_key": f"provenance-{index}",
        })
        assert response.status == 201
        expected["proposals"][index]["review_state"] = decision
    completed = _handle(runtime, "GET", ROOT)
    assert completed.payload["authoring_provenance"] == expected
    assert completed.payload["proposals"] == []
    assert completed.payload["review_summary"] == {"total": 2, "needs_review": 0, "kept_for_contract": 1, "discarded": 1}
    assert len(runtime) == 2 and drafts.get(POC_ID) == before_draft


def test_review_provenance_filtered_empty_scope_does_not_reintroduce_history():
    _, _, runtime = _services()
    result = handle_poc_proposal_web_api_request(
        method="GET", target=ROOT, payload=None, runtime=runtime,
        current_proposal_lookup=lambda _: (),
    )
    assert result.status == 200
    assert result.payload == {
        "poc_id": POC_ID, "proposals": [],
        "review_summary": {"total": 0, "needs_review": 0, "kept_for_contract": 0, "discarded": 0},
        "authoring_provenance": {"schema_version": "exitspec.review-authoring-provenance/1", "proposals": []},
    }
    assert len(runtime.list_proposals(POC_ID)) == 2 and len(runtime) == 0


def test_review_provenance_counts_use_actual_overlay_after_scope_callback():
    _, _, runtime = _services()
    selected = runtime.list_proposals(POC_ID)[:1]

    def scope(_):
        assert _handle(runtime, "POST", f"{ROOT}/{selected[0].proposal_id}/decision", {
            "decision": "KEEP_FOR_CONTRACT", "reviewer": "named.snapshot", "rationale": "Keep this exact source material.",
            "idempotency_key": "snapshot-decision",
        }).status == 201
        return selected

    result = handle_poc_proposal_web_api_request(
        method="GET", target=ROOT, payload=None, runtime=runtime, current_proposal_lookup=scope,
    )
    assert result.status == 200 and result.payload["proposals"] == []
    assert result.payload["review_summary"] == {"total": 1, "needs_review": 0, "kept_for_contract": 1, "discarded": 0}
    assert result.payload["authoring_provenance"]["proposals"] == [{
        "proposal_id": selected[0].proposal_id, "origin": "INTAKE_A2",
        "review_state": "KEEP_FOR_CONTRACT", "normalized_claim": selected[0].normalized_claim,
    }]


def test_review_provenance_stale_scope_cannot_label_committed_replacement_a2():
    _, _, runtime = _services()
    selected = runtime.list_proposals(POC_ID)
    replacements = tuple(item.model_copy(update={"proposal_id": f"prop_replacement_{index:03d}"}) for index, item in enumerate(runtime._proposal_lookup(POC_ID)))

    def scope(_):
        with runtime.authoring_commit_guard(POC_ID, selected[0].source_receipt_id) as guard:
            guard.prepare([item.proposal_id for item in replacements])
            runtime._proposal_lookup = lambda _: replacements
            guard.commit()
        return selected

    result = handle_poc_proposal_web_api_request(
        method="GET", target=ROOT, payload=None, runtime=runtime, current_proposal_lookup=scope,
    )
    assert result.status == 409 and set(result.payload) == {"error"}
    current = _handle(runtime, "GET", ROOT)
    assert all(item["origin"] == "ASSISTED_A3" for item in current.payload["authoring_provenance"]["proposals"])
    assert len(runtime) == 0


def test_review_provenance_agreement_scope_excludes_historical_a3_origins():
    _, intake, runtime = _services()
    historical = runtime.list_proposals(POC_ID)
    with runtime.authoring_commit_guard(POC_ID, historical[0].source_receipt_id) as guard:
        guard.prepare([item.proposal_id for item in historical])
        guard.commit()
    intake.capture_document(
        poc_id=POC_ID, document_text="TTFT must remain below 500 ms.",
        idempotency_key="new-agreement-source",
    )
    historical_ids = {item.proposal_id for item in historical}
    result = handle_poc_proposal_web_api_request(
        method="GET", target=ROOT, payload=None, runtime=runtime,
        current_proposal_lookup=lambda poc_id: tuple(item for item in runtime.list_proposals(poc_id) if item.proposal_id not in historical_ids),
    )
    assert result.status == 200 and result.payload["review_summary"]["total"] == 1
    row, = result.payload["authoring_provenance"]["proposals"]
    assert row["origin"] == "INTAKE_A2" and row["proposal_id"] not in historical_ids
    assert len(runtime.list_proposals(POC_ID)) == 3 and len(runtime) == 0


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
    assert response.payload["review_summary"] == {
        "total": 2,
        "needs_review": 2,
        "kept_for_contract": 0,
        "discarded": 0,
    }
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


def test_active_agreement_scope_excludes_prior_version_counts_and_writes():
    _, _, runtime = _services()
    all_items = runtime.list_proposals(POC_ID)
    current_id = all_items[1].proposal_id

    def current_scope(poc_id: str):
        assert poc_id == POC_ID
        return tuple(
            item
            for item in runtime.list_proposals(poc_id)
            if item.proposal_id == current_id
        )

    listed = handle_poc_proposal_web_api_request(
        method="GET",
        target=ROOT,
        payload=None,
        runtime=runtime,
        current_proposal_lookup=current_scope,
    )
    assert listed is not None
    assert listed.payload["review_summary"] == {
        "total": 1,
        "needs_review": 1,
        "kept_for_contract": 0,
        "discarded": 0,
    }
    assert [item["proposal_id"] for item in listed.payload["proposals"]] == [
        current_id
    ]
    assert listed.payload["authoring_provenance"] == {
        "schema_version": "exitspec.review-authoring-provenance/1",
        "proposals": [{
            "proposal_id": current_id, "origin": "INTAKE_A2",
            "review_state": "NEEDS_REVIEW", "normalized_claim": all_items[1].normalized_claim,
        }],
    }

    stale = handle_poc_proposal_web_api_request(
        method="POST",
        target=f"{ROOT}/{all_items[0].proposal_id}/decision",
        payload={
            "decision": "KEEP_FOR_CONTRACT",
            "reviewer": "Jayesh",
            "rationale": "A prior version cannot re-enter the current queue.",
            "idempotency_key": "stale-version-proposal-write",
        },
        runtime=runtime,
        current_proposal_lookup=current_scope,
    )
    assert stale is not None
    assert stale.status == HTTPStatus.NOT_FOUND
    assert all(
        item.review_state.value == "NEEDS_REVIEW"
        for item in runtime.list_proposals(POC_ID)
    )


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
    assert remaining.payload["review_summary"] == {
        "total": 2,
        "needs_review": 1,
        "kept_for_contract": 1,
        "discarded": 0,
    }
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
