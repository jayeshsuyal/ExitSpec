"""Real source/draft/review owners; synthetic material only, no provider calls."""

from __future__ import annotations

import hashlib
import threading
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

import exitspec.source_authoring_owners as owner_module
from exitspec.assisted_authoring import (
    ASSISTED_AUTHORING_SCHEMA_VERSION,
    SourceNeutralProposalBatch,
)
from exitspec.poc_creation import DraftPOCCreateRequest, FirstSourceChoice
from exitspec.poc_proposal_review import (
    ProposalDecision,
    ProposalReviewDecisionConflict,
    ProposalReviewState,
)
from exitspec.poc_source_demo import SourceNeutralPOCDemoServer
from exitspec.poc_source_intake import POCSourceInput
from exitspec.poc_sources import (
    PreparedPOCSource,
    PreparedRequirementCandidate,
    SourceKind,
)
from exitspec.source_authoring_owners import (
    SourceAuthoringOwners,
    SourceAuthoringOwnersError,
)
from exitspec.workspace_closure import POCClosureConflict
from tests.test_workspace_closure import _binding, _request, _service

NOW = datetime(2026, 9, 6, tzinfo=UTC)
TEXT = "The error rate must remain below 0.7%."


@pytest.fixture
def rig():
    server = SourceNeutralPOCDemoServer(("127.0.0.1", 0))
    poc_id = "poc_source_owner_test"
    try:
        server.draft_poc_service.create(
            DraftPOCCreateRequest(
                poc_id=poc_id,
                display_name="Synthetic owner test",
                customer_label="Synthetic",
                owner="local_operator",
                use_case="Review synthetic spoken requirements",
                first_source_choice=FirstSourceChoice.DOCUMENT,
            ),
            idempotency_key="owner-create",
        )
        receipt = server.poc_source_intake.capture_source(
            poc_id=poc_id,
            source=POCSourceInput(source_kind=SourceKind.DOCUMENT, content=TEXT),
            idempotency_key="owner-capture",
        )
        closure = _service({poc_id: _binding(poc_id)})
        owners = SourceAuthoringOwners(
            source_intake=server.poc_source_intake,
            drafts=server.draft_poc_service,
            review=server.proposal_review_service,
            assisted=server.assisted_authoring_service,
            run_if_open=closure.run_if_open,
        )
        snapshot = owners.capture(poc_id, receipt.source_receipt_id)
        yield SimpleNamespace(
            server=server,
            poc_id=poc_id,
            receipt=receipt,
            closure=closure,
            owners=owners,
            snapshot=snapshot,
        )
    finally:
        server.server_close()


def batch():
    return SourceNeutralProposalBatch.model_validate(
        {
            "schema_version": ASSISTED_AUTHORING_SCHEMA_VERSION,
            "proposals": [
                {
                    "proposal_key": "spoken_requirement",
                    "source_quote": TEXT,
                    "normalized_claim": TEXT,
                }
            ],
        }
    )


def prepare(guard):
    return guard.prepare(
        batch(),
        request_sha256="a" * 64,
        provider="fireworks",
        model="synthetic-policy-model",
        endpoint="local://fake-worker",
        generated_at=NOW,
    )


def revise_source(rig):
    source = rig.snapshot.source
    text = "The error rate must remain below 0.9%."
    revised = PreparedPOCSource(
        kind=source.kind,
        external_id=source.external_id,
        redacted_text=text,
        content_sha256=hashlib.sha256(text.encode()).hexdigest(),
        candidates=(
            PreparedRequirementCandidate(
                candidate_id="cand_revision_001",
                source_quote=text,
                normalized_claim=text,
            ),
        ),
        adapter_name=source.adapter_name,
        adapter_version=source.adapter_version,
        redaction_policy_version=source.redaction_policy_version,
        observed_at=NOW,
        revises_source_id=source.source_id,
    )
    rig.server.poc_source_intake._source_service.attach(
        rig.poc_id, revised, "revise-owner"
    )


def untouched(rig):
    service = rig.server.assisted_authoring_service
    assert service._results_by_request == {}
    assert service._source_attempts == {}
    assert service._idempotency == {}
    assert rig.server.proposal_review_service._authoring_current_proposals == {}


def test_real_owners_publish_review_only_material_in_prepared_suffix(rig, monkeypatch):
    operation_lock = threading.RLock()
    operation = {"state": "DISPATCH_AUTHORIZED"}

    def transaction(guard):
        nonlocal operation
        with operation_lock:
            token = prepare(guard)
            untouched(rig)
            prepared_operation = {"state": "SUCCEEDED", "receipt": token.result.receipt}

            # A guard commit hook must never be invoked by the publication suffix.
            def forbidden(_guard):
                raise AssertionError("fallible commit callback invoked")

            monkeypatch.setattr(owner_module._ReviewCommitGuard, "commit", forbidden)
            token.publish()
            operation = prepared_operation  # fake operation owner's prepared pointer
            return token.result

    result = rig.owners.run_guarded(rig.snapshot, transaction)
    assert operation["state"] == "SUCCEEDED"
    assert result.receipt.status == "NEEDS_REVIEW"
    assert result.proposals[0].source_quote == TEXT
    assert (
        result.receipt.source_content_sha256
        == hashlib.sha256(TEXT.encode()).hexdigest()
    )
    assert result.receipt.provider == "fireworks"
    reviewed = rig.server.proposal_review_service.list_proposals(rig.poc_id)
    assert [p.proposal_id for p in reviewed] == list(result.receipt.proposal_ids)
    assert all(
        p.review_state == ProposalReviewState.NEEDS_REVIEW and p.decision is None
        for p in reviewed
    )
    assert rig.server.draft_poc_service.get(rig.poc_id) == rig.snapshot.draft
    assert rig.closure.get(rig.poc_id) is None


@pytest.mark.parametrize("kind", ["source", "draft", "archive", "decision", "closure"])
def test_invalidation_before_transaction_blocks_dispatch_and_publication(rig, kind):
    if kind == "source":
        revise_source(rig)
    elif kind == "draft":
        # A changed generation with unchanged public source is independently stale.
        drafts = rig.server.draft_poc_service
        drafts._records[rig.poc_id] = rig.snapshot.draft.model_copy(
            update={"display_name": "Changed draft"}
        )
    elif kind == "archive":
        rig.server.draft_poc_service.archive(rig.poc_id)
    elif kind == "decision":
        review = rig.server.proposal_review_service
        prior = review.list_proposals(rig.poc_id)[0]
        review.decide(
            rig.poc_id,
            prior.proposal_id,
            ProposalDecision.KEEP_FOR_CONTRACT,
            "human_reviewer",
            "Retain synthetic requirement",
            "owner-decision",
        )
    else:
        binding = _binding(rig.poc_id)
        rig.closure.record(rig.poc_id, _request(binding), idempotency_key="owner-close")
    entered = []
    with pytest.raises(SourceAuthoringOwnersError):
        rig.owners.run_guarded(rig.snapshot, lambda guard: entered.append(guard))
    assert entered == []
    untouched(rig)


@pytest.mark.parametrize("fault", ["projection", "review_prepare", "token"])
def test_preparation_failures_publish_no_assisted_or_review_material(
    rig, monkeypatch, fault
):
    def fail(*_args, **_kwargs):
        raise ValueError("sensitive source sentinel should not escape")

    if fault == "projection":
        monkeypatch.setattr(owner_module, "_materialize_source_neutral_attempt", fail)
    elif fault == "review_prepare":
        original = owner_module._ReviewCommitGuard.prepare

        def fail_after_prepare(guard, ids):
            original(guard, ids)
            fail()

        monkeypatch.setattr(
            owner_module._ReviewCommitGuard, "prepare", fail_after_prepare
        )
    else:
        monkeypatch.setattr(owner_module, "PreparedSourceAuthoringPublication", fail)
    with pytest.raises(SourceAuthoringOwnersError) as error:
        rig.owners.run_guarded(rig.snapshot, prepare)
    assert error.value.code == "PREPARATION_FAILED"
    assert "sensitive" not in str(error.value)
    untouched(rig)


def test_typed_but_tampered_batch_is_revalidated_before_preparation(rig):
    malformed = batch()
    malformed.proposals[0].source_quote = "Made-up source quote."
    with pytest.raises(SourceAuthoringOwnersError):
        rig.owners.run_guarded(
            rig.snapshot,
            lambda guard: guard.prepare(
                malformed,
                request_sha256="a" * 64,
                provider="fireworks",
                model="synthetic-policy-model",
                endpoint="local://fake-worker",
                generated_at=NOW,
            ),
        )
    untouched(rig)


def test_unused_or_escaped_publication_and_wrong_thread_are_refused(rig):
    token = rig.owners.run_guarded(rig.snapshot, prepare)
    untouched(rig)
    with pytest.raises(SourceAuthoringOwnersError):
        token.publish()

    def transaction(guard):
        current = prepare(guard)
        errors = []

        def wrong_thread():
            try:
                current.publish()
            except SourceAuthoringOwnersError as error:
                errors.append(error.code)

        worker = threading.Thread(target=wrong_thread)
        worker.start()
        worker.join(timeout=2)
        assert not worker.is_alive()
        assert errors == ["PUBLICATION_UNAVAILABLE"]
        untouched(rig)
        current.publish()
        with pytest.raises(SourceAuthoringOwnersError):
            current.publish()

    rig.owners.run_guarded(rig.snapshot, transaction)


def test_forged_snapshot_and_noop_owner_bindings_are_refused(rig):
    forged = replace(rig.snapshot, source_receipt_id="srcpt_wrong_source")
    with pytest.raises(SourceAuthoringOwnersError):
        rig.owners.run_guarded(forged, prepare)
    with pytest.raises(SourceAuthoringOwnersError):
        SourceAuthoringOwners(
            source_intake=rig.server.poc_source_intake,
            drafts=rig.server.draft_poc_service,
            review=rig.server.proposal_review_service,
            assisted=rig.server.assisted_authoring_service,
            run_if_open=lambda _poc, fn: fn(),
        )
    rig.server.assisted_authoring_service._review_commit_guard = lambda *_args: None
    with pytest.raises(SourceAuthoringOwnersError):
        rig.owners.run_guarded(rig.snapshot, prepare)
    untouched(rig)


def test_published_source_cannot_overwrite_existing_a3_or_human_state(rig):
    rig.owners.run_guarded(rig.snapshot, lambda guard: prepare(guard).publish())
    prior = rig.server.assisted_authoring_service.list_receipts(rig.poc_id)
    with pytest.raises(SourceAuthoringOwnersError):
        rig.owners.run_guarded(rig.snapshot, prepare)
    assert rig.server.assisted_authoring_service.list_receipts(rig.poc_id) == prior


def test_prepared_transaction_preserves_real_closure_and_review_reservations(rig):
    review = rig.server.proposal_review_service
    old = review.list_proposals(rig.poc_id)[0]

    def transaction(guard):
        prepared = prepare(guard)
        with pytest.raises(POCClosureConflict):
            binding = _binding(rig.poc_id)
            rig.closure.record(
                rig.poc_id, _request(binding), idempotency_key="racing-close"
            )
        with pytest.raises(ProposalReviewDecisionConflict):
            review.decide(
                rig.poc_id,
                old.proposal_id,
                ProposalDecision.KEEP_FOR_CONTRACT,
                "human_reviewer",
                "Concurrent synthetic decision",
                "racing-decision",
            )
        untouched(rig)
        prepared.publish()

    rig.owners.run_guarded(rig.snapshot, transaction)
    assert review.list_proposals(rig.poc_id)[0].decision is None


def test_reader_cannot_observe_prepared_proposals_before_operation_pointer_swap(rig):
    operation = {"state": "DISPATCH_AUTHORIZED"}
    ready, finished = threading.Event(), threading.Event()
    observed = []
    workers = []

    def reader():
        ready.set()
        values = rig.server.proposal_review_service.list_proposals(rig.poc_id)
        observed.append((operation["state"], tuple(p.source_quote for p in values)))
        finished.set()

    def transaction(guard):
        nonlocal operation
        token = prepare(guard)
        prepared_operation = {"state": "SUCCEEDED"}
        worker = threading.Thread(target=reader, daemon=True)
        workers.append(worker)
        worker.start()
        assert ready.wait(2)
        assert not finished.wait(0.02)
        token.publish()
        operation = prepared_operation

    rig.owners.run_guarded(rig.snapshot, transaction)
    assert finished.wait(2)
    workers[0].join(timeout=2)
    assert observed == [("SUCCEEDED", (TEXT,))]


def test_a_freely_constructed_token_cannot_publish_even_with_an_active_guard(rig):
    def transaction(guard):
        token = prepare(guard)
        forged = owner_module.PreparedSourceAuthoringPublication(
            guard,
            {},
            {},
            {},
            token.result,
            expected_registration=token._expected_registration,
            expected_results=token._expected_results,
            expected_attempts=token._expected_attempts,
        )
        with pytest.raises(SourceAuthoringOwnersError):
            forged.publish()
        untouched(rig)

    rig.owners.run_guarded(rig.snapshot, transaction)


@pytest.mark.parametrize("boundary", ["D", "F"])
@pytest.mark.parametrize("mutation", ["source", "archive"])
def test_final_check_rejects_reentrant_source_or_draft_change(rig, boundary, mutation):
    sent = []

    def transaction(guard):
        token = prepare(guard) if boundary == "F" else None
        # Same-thread owner mutation models a reentrant final clock callback.
        if mutation == "source":
            revise_source(rig)
        else:
            rig.server.draft_poc_service.archive(rig.poc_id)
        guard.check_current_locked()
        if token is not None:
            token.publish()
        else:
            sent.append("ticket staged")

    with pytest.raises(SourceAuthoringOwnersError) as error:
        rig.owners.run_guarded(rig.snapshot, transaction)
    assert error.value.code == (
        "SOURCE_STALE" if mutation == "source" else "DRAFT_STALE"
    )
    assert sent == []
    untouched(rig)


def test_final_check_detects_mutation_during_fallible_preparation(rig, monkeypatch):
    original = owner_module._ReviewCommitGuard.prepare

    def prepare_then_archive(guard, ids):
        original(guard, ids)
        rig.server.draft_poc_service.archive(rig.poc_id)

    monkeypatch.setattr(
        owner_module._ReviewCommitGuard, "prepare", prepare_then_archive
    )

    def transaction(guard):
        token = prepare(guard)
        guard.check_current_locked()
        token.publish()

    with pytest.raises(SourceAuthoringOwnersError, match="refused"):
        rig.owners.run_guarded(rig.snapshot, transaction)
    untouched(rig)


@pytest.mark.parametrize("human_review", [False, True])
def test_nested_independent_a3_publication_survives_outer_prepared_commit(
    rig, human_review
):
    service = rig.server.assisted_authoring_service
    review = rig.server.proposal_review_service
    second = rig.server.poc_source_intake.capture_source(
        poc_id=rig.poc_id,
        source=POCSourceInput(
            source_kind=SourceKind.DOCUMENT,
            content="The throughput must exceed 175 requests per second.",
        ),
        idempotency_key="independent-source",
    )
    nested = {}

    def transaction(guard):
        token = prepare(guard)
        # This public call models an independent authoring action reentering
        # through the final-clock seam after source A's maps were prepared.
        authored = service.create_assisted_draft(
            poc_id=rig.poc_id,
            source_receipt_id=second.source_receipt_id,
            idempotency_key="independent-authoring",
        )
        if human_review:
            review.decide(
                rig.poc_id,
                authored.proposals[0].proposal_id,
                ProposalDecision.KEEP_FOR_CONTRACT,
                "human_reviewer",
                "Retain the independent synthetic requirement.",
                "independent-decision",
            )
        nested.update(
            {
                "result": authored,
                "registration": review._authoring_current_proposals,
                "results": service._results_by_request,
                "attempts": service._source_attempts,
                "idempotency": service._idempotency,
                "decisions": review._decisions,
            }
        )
        guard.check_current_locked()
        token.publish()

    with pytest.raises(SourceAuthoringOwnersError) as error:
        rig.owners.run_guarded(rig.snapshot, transaction)
    assert error.value.code == "PUBLICATION_CONFLICT"
    assert review._authoring_current_proposals is nested["registration"]
    assert service._results_by_request is nested["results"]
    assert service._source_attempts is nested["attempts"]
    assert service._idempotency is nested["idempotency"]
    assert review._decisions is nested["decisions"]
    assert tuple(service._results_by_request) == (
        nested["result"].receipt.authoring_receipt_id,
    )
    assert (rig.poc_id, rig.snapshot.source.source_id) not in service._source_attempts
    replay = service.create_assisted_draft(
        poc_id=rig.poc_id,
        source_receipt_id=second.source_receipt_id,
        idempotency_key="independent-authoring",
    )
    assert (
        replay.receipt.authoring_receipt_id
        == nested["result"].receipt.authoring_receipt_id
    )
    assert replay.receipt.idempotent_replay is True
    reviewed = [
        p
        for p in review.list_proposals(rig.poc_id)
        if p.source_receipt_id == second.source_receipt_id
    ]
    assert reviewed[0].proposal_id == nested["result"].proposals[0].proposal_id
    assert (reviewed[0].decision is not None) is human_review


@pytest.mark.parametrize("map_name", ["registration", "results", "attempts"])
def test_each_changed_owner_map_refuses_all_prepared_swaps(rig, map_name):
    service = rig.server.assisted_authoring_service
    review = rig.server.proposal_review_service

    def transaction(guard):
        token = prepare(guard)
        if map_name == "registration":
            review._authoring_current_proposals = dict(
                review._authoring_current_proposals
            )
        elif map_name == "results":
            service._results_by_request = dict(service._results_by_request)
        else:
            service._source_attempts = dict(service._source_attempts)
        guard.check_current_locked()
        token.publish()

    with pytest.raises(SourceAuthoringOwnersError) as error:
        rig.owners.run_guarded(rig.snapshot, transaction)
    assert error.value.code == "PUBLICATION_CONFLICT"
    untouched(rig)
