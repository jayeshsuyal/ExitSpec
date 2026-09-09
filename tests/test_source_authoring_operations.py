"""Synthetic D/F state-machine proof, using real source/review owners."""

from __future__ import annotations

import copy
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from decimal import Decimal
from threading import Event

import pytest

from exitspec.poc_proposal_review import ProcessLocalProposalReviewService
from exitspec.source_authoring_operations import (
    AuthorizedSourceAuthoringRequest,
    ProcessLocalSourceAuthoringOperations,
    SourceAuthoringOperationError,
    SyntheticBrowserSession,
    create_live_source_authoring_operations,
)
from exitspec.source_authoring_owners import SourceAuthoringOwners
from exitspec.source_authoring_policy import SourceBoundAuthoringIntent
from exitspec.source_authoring_supervisor import SyntheticSourceAuthoringSupervisor
from exitspec.workspace_closure import ProcessLocalPOCClosureService
from tests.test_a3_assisted_review import (
    NOW,
    _attach_document,
    _runtime,
    _valid_payload,
)


class Clock:
    def __init__(self):
        self.value = 100.0

    def __call__(self):
        return self.value


def setup(schedule=None):
    poc = "poc_source_core"
    drafts, intake, assisted = _runtime(poc)
    review = ProcessLocalProposalReviewService(
        proposal_lookup=assisted.proposal_inputs, clock=lambda: NOW
    )
    assisted.bind_decision_lookup(review.source_has_decision)
    assisted.bind_review_commit_guard(review.authoring_commit_guard)
    assisted.bind_source_commit_guard(intake.authoring_commit_guard)
    assisted.bind_draft_commit_guard(drafts.authoring_commit_guard)
    closure = ProcessLocalPOCClosureService(evidence_resolver=lambda _: None)
    owners = SourceAuthoringOwners(
        source_intake=intake,
        drafts=drafts,
        assisted=assisted,
        review=review,
        run_if_open=closure.run_if_open,
    )
    clock = Clock()
    ops = ProcessLocalSourceAuthoringOperations(
        owners=owners, monotonic=clock, schedule=schedule
    )
    session = ops.new_synthetic_session()
    receipt = _attach_document(intake, poc)
    disclosure = ops.prepare(session, poc, receipt)
    permit = ops.authorize(
        session, disclosure, acknowledged=True, idempotency_key="first"
    )
    return (
        ops,
        session,
        permit,
        clock,
        (poc, drafts, intake, assisted, review, closure, disclosure),
    )


def worker(payload=None, **kwargs):
    return SyntheticSourceAuthoringSupervisor(
        synthetic_response=json.dumps(
            _valid_payload() if payload is None else payload
        ).encode(),
        **kwargs,
    )


def test_success_is_review_only_and_replay_never_starts_another_worker():
    ops, session, permit, _, context = setup()
    result = ops.execute_synthetic(session, permit, worker=worker())
    assert result.state == "SUCCEEDED"
    assert result.attempts == 1 and result.reserved_usd == Decimal("0.01")
    rows = context[4].list_proposals(context[0])
    assert len(rows) == 1 and rows[0].review_state.value == "NEEDS_REVIEW"
    assert rows[0].decision is None
    unused = worker()
    assert ops.execute_synthetic(session, permit, worker=unused) == result
    assert unused.state == "NEW"
    assert ops.ledger == (1, Decimal("0.01"))
    assert ops._records[permit._operation].body is None
    assert ops._records[permit._operation].source is None


@pytest.mark.parametrize(
    "handle", [AuthorizedSourceAuthoringRequest, SyntheticBrowserSession]
)
def test_handles_cannot_be_publicly_constructed(handle):
    with pytest.raises(SourceAuthoringOperationError):
        handle()


def test_permit_privacy_cross_session_cross_issuer_and_wave1_type_refusal():
    ops, session, permit, _, _ = setup()
    assert permit._operation not in repr(permit)
    for clone in (copy.copy, copy.deepcopy):
        with pytest.raises(SourceAuthoringOperationError):
            clone(permit)
    for wrong in ("public-session-id", object(), {}):
        with pytest.raises(SourceAuthoringOperationError):
            ops.status(session, wrong)
    with pytest.raises(SourceAuthoringOperationError):
        ops.status(ops.new_synthetic_session(), permit)
    other, other_session, _, _, _ = setup()
    with pytest.raises(SourceAuthoringOperationError):
        other.status(other_session, permit)
    assert ops.ledger[0] == other.ledger[0] == 0


def test_disclosure_acknowledgement_and_aliases_do_not_grant_new_attempts():
    ops, session, permit, _, context = setup()
    disclosure = context[-1]
    assert (
        ops.authorize(session, disclosure, acknowledged=True, idempotency_key="alias")
        is permit
    )
    assert (
        ops.authorize(session, disclosure, acknowledged=True, idempotency_key="first")
        is permit
    )
    for ack in (False, 1, "true", None):
        with pytest.raises(SourceAuthoringOperationError):
            ops.authorize(session, disclosure, acknowledged=ack, idempotency_key="bad")
    with pytest.raises(SourceAuthoringOperationError):
        ops.authorize(
            session,
            replace(disclosure, disclosure_sha256="0" * 64),
            acknowledged=True,
            idempotency_key="bad",
        )
    assert ops.ledger[0] == 0


@pytest.mark.parametrize(
    "pause", ["pre_claim", "claimed", "pre_dispatch", "post_dispatch", "pre_commit"]
)
def test_revocation_at_each_boundary_never_publishes(pause):
    holder = {}

    def schedule(where):
        if where == pause:
            holder["ops"].revoke(holder["session"], holder["permit"])

    ops, session, permit, _, context = setup(schedule)
    holder.update(ops=ops, session=session, permit=permit)
    actual_worker = worker()
    result = ops.execute_synthetic(session, permit, worker=actual_worker)
    assert result.state == "REVOKED"
    assert context[4].list_proposals(context[0]) == ()
    assert ops.ledger[0] == (0 if pause == "pre_claim" else 1)
    if pause in {"pre_claim", "claimed", "pre_dispatch"}:
        assert actual_worker.state in {"NEW", "CANCELLED"}
    repeat = worker()
    assert ops.execute_synthetic(session, permit, worker=repeat) == result
    assert repeat.state == "NEW"


@pytest.mark.parametrize(
    "pause", ["claimed", "pre_dispatch", "post_dispatch", "pre_commit"]
)
def test_expiry_at_boundaries_discards_results_and_retains_reservation(pause):
    holder = {}

    def schedule(where):
        if where == pause:
            holder["clock"].value = 400.0

    ops, session, permit, clock, context = setup(schedule)
    holder["clock"] = clock
    result = ops.execute_synthetic(session, permit, worker=worker())
    assert result.state == "EXPIRED"
    assert ops.ledger == (1, Decimal("0.01"))
    assert context[4].list_proposals(context[0]) == ()


def test_expiry_before_claim_has_zero_attempt():
    ops, session, permit, clock, _ = setup()
    clock.value = 400.0
    result = ops.execute_synthetic(session, permit, worker=worker())
    assert result.state == "EXPIRED" and ops.ledger[0] == 0


def test_concurrent_execute_has_one_claim_and_one_worker():
    started, release = Event(), Event()

    def schedule(where):
        if where == "claimed":
            started.set()
            assert release.wait(5)

    ops, session, permit, _, _ = setup(schedule)
    with ThreadPoolExecutor(2) as pool:
        running = pool.submit(ops.execute_synthetic, session, permit, worker=worker())
        assert started.wait(5)
        unused = worker()
        replay = ops.execute_synthetic(session, permit, worker=unused)
        assert replay.state == "CLAIMED" and unused.state == "NEW"
        release.set()
        assert running.result(5).state == "SUCCEEDED"
    assert ops.ledger[0] == 1


def test_malformed_output_is_terminal_and_not_refunded():
    ops, session, permit, _, context = setup()
    result = ops.execute_synthetic(
        session, permit, worker=worker({"secret": "fw_private_sentinel"})
    )
    assert result.state == "FAILED"
    assert "sentinel" not in repr(result)
    assert context[4].list_proposals(context[0]) == ()
    assert ops.ledger == (1, Decimal("0.01"))
    assert ops.execute_synthetic(session, permit, worker=worker()) == result


def test_final_publication_wins_before_revocation():
    holder = {}

    def schedule(where):
        if where == "committed":
            holder["ops"].revoke(holder["session"], holder["permit"])

    ops, session, permit, _, context = setup(schedule)
    holder.update(ops=ops, session=session, permit=permit)
    assert ops.execute_synthetic(session, permit, worker=worker()).state == "SUCCEEDED"
    assert len(context[4].list_proposals(context[0])) == 1


def test_shutdown_revokes_handles_and_new_runtime_cannot_replay():
    ops, session, permit, _, _ = setup()
    ops.shutdown()
    with pytest.raises(SourceAuthoringOperationError, match="grant_closed"):
        ops.status(session, permit)
    other, other_session, _, _, _ = setup()
    with pytest.raises(SourceAuthoringOperationError):
        other.execute_synthetic(other_session, permit, worker=worker())
    with pytest.raises(
        SourceAuthoringOperationError, match="live_prerequisites_missing"
    ):
        create_live_source_authoring_operations(network_enabled=True)


def test_session_capacity_never_resets_global_ledger():
    ops, _, _, _, _ = setup()
    for _ in range(15):
        ops.new_synthetic_session()
    with pytest.raises(SourceAuthoringOperationError, match="session_capacity"):
        ops.new_synthetic_session()
    assert len(ops._sessions) == 16 and ops.ledger[0] == 0


@pytest.mark.parametrize("field", list(SourceBoundAuthoringIntent.model_fields))
def test_every_intent_binding_tamper_is_refused_before_handoff(field, monkeypatch):
    ops, session, permit, _, context = setup()
    old = ops._records[permit._operation]
    values = old.intent.model_dump()
    value = values[field]
    if type(value) is str:
        values[field] = "changed-binding"
    elif type(value) is bool:
        values[field] = not value
    elif type(value) in (float, int):
        values[field] = value + 1
    else:
        values[field] = {"hostile": "PRIVATE_SENTINEL"}
    changed = SourceBoundAuthoringIntent.model_construct(**values)
    ops._records[permit._operation] = replace(old, intent=changed)
    calls = []
    monkeypatch.setattr(
        SyntheticSourceAuthoringSupervisor, "handoff", lambda self: calls.append(1)
    )
    result = ops.execute_synthetic(session, permit, worker=worker())
    assert result.state == "FAILED" and calls == []
    assert context[4].list_proposals(context[0]) == ()
    assert "PRIVATE_SENTINEL" not in repr(result)
    assert ops.ledger[0] == 1


@pytest.mark.parametrize(
    "scenario",
    [
        "worker_error",
        "partial_result",
        "oversize_result",
        "malformed_result",
        "extra_result",
        "wrong_binding",
    ],
)
def test_ambiguous_worker_failure_is_terminal_and_replay_never_resends(scenario):
    ops, session, permit, _, context = setup()
    result = ops.execute_synthetic(
        session, permit, worker=worker(synthetic_scenario=scenario)
    )
    assert result.state == "OUTCOME_UNKNOWN"
    assert ops.ledger == (1, Decimal("0.01"))
    assert context[4].list_proposals(context[0]) == ()
    again = worker()
    assert ops.execute_synthetic(session, permit, worker=again) == result
    assert again.state == "NEW"


@pytest.mark.parametrize("pause", ["pre_dispatch", "pre_commit"])
def test_archive_winning_owner_guard_prevents_send_or_publication(pause, monkeypatch):
    holder = {}
    sent = []
    original = SyntheticSourceAuthoringSupervisor.handoff

    def handoff(self):
        sent.append(1)
        return original(self)

    monkeypatch.setattr(SyntheticSourceAuthoringSupervisor, "handoff", handoff)

    def schedule(where):
        if where == pause:
            holder["drafts"].archive(holder["poc"])

    ops, session, permit, _, context = setup(schedule)
    holder.update(drafts=context[1], poc=context[0])
    result = ops.execute_synthetic(session, permit, worker=worker())
    assert result.state == "STALE"
    assert len(sent) == (0 if pause == "pre_dispatch" else 1)
    assert context[3]._results_by_request == {}
    assert context[4]._authoring_current_proposals == {}
    assert ops.execute_synthetic(session, permit, worker=worker()) == result


def test_logical_total_deadline_rejects_late_result():
    holder = {}

    def schedule(where):
        if where == "pre_commit":
            holder["clock"].value += 30

    ops, session, permit, clock, context = setup(schedule)
    holder["clock"] = clock
    result = ops.execute_synthetic(session, permit, worker=worker())
    assert result.state == "OUTCOME_UNKNOWN"
    assert context[3]._results_by_request == {}
    assert ops.execute_synthetic(session, permit, worker=worker()) == result


def test_budget_and_rate_are_global_nonrefunded_and_not_reset_by_new_sessions():
    ops, session, permit, clock, context = setup()
    ops.execute_synthetic(session, permit, worker=worker({}))
    source_receipt = ops._records[permit._operation].intent.source_receipt_id
    disclosure = ops.prepare(session, context[0], source_receipt)
    next_permit = ops.authorize(
        session, disclosure, acknowledged=True, idempotency_key="second"
    )
    with pytest.raises(SourceAuthoringOperationError, match="rate_limited"):
        ops.execute_synthetic(session, next_permit, worker=worker({}))
    for index in range(1, 10):
        clock.value += 10
        if index > 1:
            session = ops.new_synthetic_session()
            disclosure = ops.prepare(session, context[0], source_receipt)
            next_permit = ops.authorize(
                session, disclosure, acknowledged=True, idempotency_key=str(index)
            )
        assert (
            ops.execute_synthetic(session, next_permit, worker=worker({})).state
            == "FAILED"
        )
    assert ops.ledger == (10, Decimal("0.10"))
    clock.value += 10
    disclosure = ops.prepare(session, context[0], source_receipt)
    last = ops.authorize(
        session, disclosure, acknowledged=True, idempotency_key="eleventh"
    )
    with pytest.raises(SourceAuthoringOperationError, match="budget_exhausted"):
        ops.execute_synthetic(session, last, worker=worker())
    assert ops.ledger == (10, Decimal("0.10"))


def test_prepared_disclosure_can_be_revoked_without_authorizing():
    ops, session, permit, _, context = setup()
    ops.revoke(session, permit)
    source_receipt = ops._records[permit._operation].intent.source_receipt_id
    disclosure = ops.prepare(session, context[0], source_receipt)
    result = ops.revoke_disclosure(session, disclosure)
    assert result.state == "REVOKED" and result.attempts == 0
    assert ops._records[disclosure.operation_id].body is None
    with pytest.raises(SourceAuthoringOperationError):
        ops.authorize(session, disclosure, acknowledged=True, idempotency_key="revoked")


def test_reentrant_clock_revocation_cannot_publish_stale_record():
    holder = {"armed": False}

    def schedule(where):
        if where == "pre_commit":
            holder["armed"] = True

    ops, session, permit, clock, context = setup(schedule)

    def now():
        if holder["armed"]:
            holder["armed"] = False
            ops.revoke(session, permit)
        return clock.value

    ops._clock = now
    assert ops.execute_synthetic(session, permit, worker=worker()).state == "REVOKED"
    assert context[3]._results_by_request == {}


@pytest.mark.parametrize("invalidation", ["permit", "disclosure", "shutdown", "session"])
def test_claim_clock_invalidation_never_consumes_or_starts_worker(invalidation):
    ops, session, permit, clock, context = setup()
    armed = True

    def now():
        nonlocal armed
        if armed:
            armed = False
            if invalidation == "permit":
                assert ops.revoke(session, permit).state == "REVOKED"
            elif invalidation == "disclosure":
                assert ops.revoke_disclosure(session, context[-1]).state == "REVOKED"
            elif invalidation == "shutdown":
                ops.shutdown()
            else:
                session._secret = b"invalidated-session"
        return clock.value

    ops._clock = now
    unused = worker()
    if invalidation in {"shutdown", "session"}:
        code = "grant_closed" if invalidation == "shutdown" else "session_refused"
        with pytest.raises(SourceAuthoringOperationError, match=code):
            ops.execute_synthetic(session, permit, worker=unused)
    else:
        result = ops.execute_synthetic(session, permit, worker=unused)
        assert result.state == "REVOKED"
        assert ops.execute_synthetic(session, permit, worker=unused) is result
    record = ops._records[permit._operation]
    assert record.receipt.attempts == 0
    assert record.receipt.reserved_usd == Decimal("0.00")
    assert ops.ledger == (0, Decimal("0.00"))
    assert unused.state == "NEW"
    assert ops._active is None and ops._worker is None
    assert context[3]._results_by_request == {}
    assert context[4].list_proposals(context[0]) == ()
    if invalidation != "session":
        assert record.receipt.state == "REVOKED"
        assert record.source is None and record.body is None


@pytest.mark.parametrize("other_session", [False, True])
@pytest.mark.parametrize("revocation", ["permit", "disclosure"])
def test_final_clock_preserves_another_operations_revocation(other_session, revocation):
    countdown = 0

    def schedule(where):
        nonlocal countdown
        if where == "pre_commit":
            # The second commit clock runs after the operation map was copied.
            countdown = 2

    ops, session, permit, clock, context = setup(schedule)
    second_session = ops.new_synthetic_session() if other_session else session
    second_source = _attach_document(
        context[2],
        context[0],
        "The second requirement is an error rate below 1%.",
        "capture-second",
    )
    disclosure = ops.prepare(second_session, context[0], second_source)
    second_permit = ops.authorize(
        second_session, disclosure, acknowledged=True, idempotency_key="second"
    )
    tombstone = None

    def now():
        nonlocal countdown, tombstone
        if countdown:
            countdown -= 1
            if countdown == 0:
                if revocation == "permit":
                    ops.revoke(second_session, second_permit)
                else:
                    ops.revoke_disclosure(second_session, disclosure)
                tombstone = ops._records[second_permit._operation]
        return clock.value

    ops._clock = now
    result = ops.execute_synthetic(session, permit, worker=worker())
    assert tombstone is not None
    assert ops._records[second_permit._operation] is tombstone
    assert tombstone.receipt.state == "REVOKED"
    assert tombstone.source is None and tombstone.body is None
    assert tombstone.receipt.attempts == 0
    # The outer operation fails closed before any of its owner maps publish.
    assert result.state == "FAILED"
    assert result.authoring_receipt_id is None
    assert context[3]._results_by_request == {}
    assert context[3]._source_attempts == {}
    assert context[4].list_proposals(context[0]) == ()
    clock.value += 10
    unused = worker()
    assert (
        ops.execute_synthetic(second_session, second_permit, worker=unused)
        is tombstone.receipt
    )
    assert unused.state == "NEW"
    assert ops.ledger == (1, Decimal("0.01"))


def test_bad_clock_exception_is_content_free():
    ops, session, permit, _, _ = setup()

    def bad_clock():
        raise RuntimeError("PRIVATE_SENTINEL")

    ops._clock = bad_clock
    with pytest.raises(SourceAuthoringOperationError) as failure:
        ops.execute_synthetic(session, permit, worker=worker())
    assert "PRIVATE_SENTINEL" not in str(failure.value)


def test_alias_capacity_fails_closed_without_evicting_replay_records():
    ops, session, permit, _, context = setup()
    for index in range(16383):
        assert (
            ops.authorize(
                session,
                context[-1],
                acknowledged=True,
                idempotency_key=f"alias-{index}",
            )
            is permit
        )
    with pytest.raises(SourceAuthoringOperationError, match="alias_capacity"):
        ops.authorize(
            session, context[-1], acknowledged=True, idempotency_key="overflow"
        )
    assert (
        ops.authorize(session, context[-1], acknowledged=True, idempotency_key="first")
        is permit
    )
    assert ops.ledger[0] == 0


def test_operation_capacity_does_not_evict_revoked_disclosures():
    ops, session, permit, _, context = setup()
    ops.revoke(session, permit)
    source_receipt = ops._records[permit._operation].intent.source_receipt_id
    for _ in range(1023):
        disclosure = ops.prepare(session, context[0], source_receipt)
        ops.revoke_disclosure(session, disclosure)
    with pytest.raises(SourceAuthoringOperationError, match="operation_capacity"):
        ops.prepare(session, context[0], source_receipt)
    assert ops.status(session, permit).state == "REVOKED"
    assert ops.ledger[0] == 0


def test_arbitrary_exception_code_accessor_is_never_read():
    class HostileException(Exception):
        @property
        def code(self):
            raise RuntimeError("PRIVATE_SENTINEL")

    def schedule(where):
        if where == "claimed":
            raise HostileException("PRIVATE_SENTINEL")

    ops, session, permit, _, context = setup(schedule)
    result = ops.execute_synthetic(session, permit, worker=worker())
    assert result.state == "FAILED" and "PRIVATE_SENTINEL" not in repr(result)
    assert context[3]._results_by_request == {}
    assert ops.ledger == (1, Decimal("0.01"))


def test_cleanup_without_observed_exit_retains_slot_and_disables_grant(monkeypatch):
    ops, session, permit, _, _ = setup()
    monkeypatch.setattr(
        SyntheticSourceAuthoringSupervisor, "slot_occupied", property(lambda _: True)
    )
    ops.execute_synthetic(session, permit, worker=worker())
    assert ops._active == permit._operation and ops._closed
    with pytest.raises(SourceAuthoringOperationError):
        ops.new_synthetic_session()


@pytest.mark.parametrize("pause", ["pre_dispatch", "pre_commit"])
def test_reentrant_final_clock_archive_cannot_publish_or_dispatch(pause, monkeypatch):
    holder = {"armed": False}
    handed = []
    original = SyntheticSourceAuthoringSupervisor.handoff

    def handoff(self):
        handed.append(1)
        return original(self)

    monkeypatch.setattr(SyntheticSourceAuthoringSupervisor, "handoff", handoff)

    def schedule(where):
        if where == pause:
            holder["armed"] = True

    ops, session, permit, clock, context = setup(schedule)

    def now():
        if holder["armed"]:
            holder["armed"] = False
            context[1].archive(context[0])
        return clock.value

    ops._clock = now
    assert ops.execute_synthetic(session, permit, worker=worker()).state == "STALE"
    assert len(handed) == (0 if pause == "pre_dispatch" else 1)
    assert context[3]._results_by_request == {}


def test_session_secret_is_bound_to_issuer_record_and_uninitialized_handles_fail_closed():
    ops, session, permit, _, _ = setup()
    session._secret = b"changed-secret"
    with pytest.raises(SourceAuthoringOperationError, match="session_refused"):
        ops.status(session, permit)
    with pytest.raises(SourceAuthoringOperationError, match="session_refused"):
        ops.status(object.__new__(SyntheticBrowserSession), permit)
    good = ops.new_synthetic_session()
    with pytest.raises(SourceAuthoringOperationError, match="permit_refused"):
        ops.status(good, object.__new__(AuthorizedSourceAuthoringRequest))


@pytest.mark.parametrize("clock_call", [1, 2])
@pytest.mark.parametrize("invalidation", ["revoke", "archive", "shutdown"])
def test_local_preview_final_clock_never_returns_invalidated_source(clock_call, invalidation):
    ops, session, permit, clock, context = setup()
    remaining = clock_call

    def now():
        nonlocal remaining
        remaining -= 1
        if remaining == 0:
            if invalidation == "revoke":
                ops.revoke(session, permit)
            elif invalidation == "archive":
                context[1].archive(context[0])
            else:
                ops.shutdown()
        return clock.value

    ops._clock = now
    receipt, source, ttl = ops.inspect_disclosure(session, context[-1])
    assert receipt.state == ("STALE" if invalidation == "archive" else "REVOKED")
    assert source is None and ttl == 0
    assert ops.ledger[0] == 0 and ops._worker is None
    assert ops._records[permit._operation].source is None
    assert ops._records[permit._operation].body is None
