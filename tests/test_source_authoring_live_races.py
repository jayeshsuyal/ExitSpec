"""Actual source/review mutations race the shared live D/F engine."""
from types import SimpleNamespace

import pytest

from exitspec import source_authoring_launch as launch
from exitspec.poc_proposal_review import ProposalDecision
from exitspec.source_authoring_supervisor import _BoundedLiveSupervisor
from tests.test_source_authoring_live_web import bootstrap
from tests.test_source_authoring_live_web import rig as _rig
from tests.test_source_authoring_owners import revise_source
from tests.test_source_authoring_web import POC, authorize, prepare

rig = _rig


def prepared(rig):
    cap = bootstrap(rig)
    disclosure = prepare(rig, cap)
    authorize(rig, cap, disclosure["operation_id"])
    browser = rig.runtime._browsers[0]
    entry = browser.operations[disclosure["operation_id"]]
    return browser.session, entry.permit


@pytest.mark.parametrize("point", ["pre_claim", "pre_dispatch", "pre_commit"])
@pytest.mark.parametrize("change", ["archive", "source", "review"])
def test_current_owners_win_before_claim_dispatch_or_publication(rig, monkeypatch, point, change):
    session, permit = prepared(rig)
    ops, server = rig.runtime.operations, rig.server
    record = ops._records[permit._operation]
    handoffs = []
    original = _BoundedLiveSupervisor.handoff

    def handoff(worker):
        handoffs.append(True)
        return original(worker)

    def schedule(event):
        if event != point:
            return
        if change == "archive":
            server.draft_poc_service.archive(POC)
        elif change == "source":
            revise_source(SimpleNamespace(server=server, poc_id=POC, snapshot=record.source))
        else:
            row = server.proposal_review_service.list_proposals(POC)[0]
            server.proposal_review_service.decide(
                POC, row.proposal_id, ProposalDecision.KEEP_FOR_CONTRACT,
                "named.human", "Retain exact original proposal", "human-decision",
            )

    monkeypatch.setattr(_BoundedLiveSupervisor, "handoff", handoff)
    monkeypatch.setattr(ops, "_schedule", schedule)
    result = ops.execute_live(session, permit)
    assert result.state == "STALE" and result.authoring_receipt_id is None
    assert ops.ledger[0] == (0 if point == "pre_claim" else 1)
    assert len(rig.children) == (0 if point == "pre_claim" else 1)
    assert len(handoffs) == (1 if point == "pre_commit" else 0)
    assert all(child.poll() is not None for child in rig.children)
    assert not server.assisted_authoring_service._results_by_request
    assert ops._active is None and server.poc_closure_service._active_mutations == {}


def test_worker_io_and_token_evaluation_hold_no_owner_or_issuer_lock(rig, monkeypatch):
    ops, server = rig.runtime.operations, rig.server
    observed = []

    def check(name):
        locks = (ops._lock, launch._LOCK, server.draft_poc_service._lock,
                 server.poc_source_intake._source_service._lock,
                 server.assisted_authoring_service._lock, server.proposal_review_service._lock)
        assert not any(lock._is_owned() for lock in locks), name
        assert server.poc_closure_service._active_mutations.get(POC, 0) == 0, name
        observed.append(name)

    original_tokens = launch._evaluate_local_tokens

    def tokens(*args):
        check("tokens")
        return original_tokens(*args)

    monkeypatch.setattr(launch, "_evaluate_local_tokens", tokens)
    for name in ("prepare", "prepare_ticket", "handoff", "collect", "reap"):
        original = getattr(_BoundedLiveSupervisor, name)

        def tracked(self, *args, _original=original, _name=name, **kwargs):
            check(_name)
            return _original(self, *args, **kwargs)

        monkeypatch.setattr(_BoundedLiveSupervisor, name, tracked)
    session, permit = prepared(rig)
    assert ops.execute_live(session, permit).state == "SUCCEEDED"
    assert set(observed) == {"tokens", "prepare", "prepare_ticket", "handoff", "collect", "reap"}
