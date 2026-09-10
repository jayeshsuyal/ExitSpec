"""Sealed offline live-realm composition through the one actual operation engine."""
from dataclasses import replace
from decimal import Decimal

import pytest

from exitspec import source_authoring_launch as launch
from exitspec.source_authoring_operations import (
    LiveAuthorizedSourceAuthoringRequest,
    LiveBrowserSession,
    SourceAuthoringOperationError,
    create_live_source_authoring_operations,
)
from exitspec.source_authoring_policy import ENDPOINT, MODEL
from exitspec.source_authoring_supervisor import _BoundedLiveSupervisor
from tests.helpers.source_authoring_admission import fake_transport, make_launch
from tests.test_source_authoring_operations import setup, worker


def live_setup(monkeypatch):
    synthetic, synthetic_session, synthetic_permit, _, context = setup()
    handle = make_launch(monkeypatch)
    install = launch._reserve_runtime_install(handle)
    ops = create_live_source_authoring_operations(owners=synthetic._owners, installation=install)
    session = ops.new_live_session()
    disclosure = ops.prepare(session, context[0], synthetic._records[synthetic_permit._operation].source.source_receipt_id)
    permit = ops.authorize(session, disclosure, acknowledged=True, idempotency_key="live-first")
    return ops, session, permit, handle, context, (synthetic, synthetic_session, synthetic_permit)


def test_one_live_realm_attempt_publishes_fixed_provenance_and_no_human_decision(monkeypatch):
    ops, session, permit, handle, context, _ = live_setup(monkeypatch)
    children = fake_transport(monkeypatch)
    result = ops.execute_live(session, permit)
    assert result.state == "SUCCEEDED", result
    assert ops.ledger == (1, Decimal("0.01")) and len(children) == 1
    assert children[0].returncode == 0 and children[0].stdin.closed and children[0].stdout.closed
    rows = context[4].list_proposals(context[0])
    assert rows and all(row.review_state.value == "NEEDS_REVIEW" and row.decision is None for row in rows)
    stored = context[3]._results_by_request
    receipt = next(iter(stored.values())).receipt
    assert receipt.provider == "fireworks" and receipt.model == MODEL and receipt.endpoint == ENDPOINT
    assert ops.execute_live(session, permit) == result and len(children) == 1
    record = ops._records[permit._operation]
    assert record.body is None and record.source is None
    handle.revoke()


@pytest.mark.parametrize("wrong", ["live_session", "live_execute", "synthetic_session", "synthetic_execute", "cross_session", "cross_permit"])
def test_realms_refuse_before_claim_or_child(monkeypatch, wrong):
    ops, session, permit, handle, context, old = live_setup(monkeypatch)
    synthetic, old_session, old_permit = old
    children = fake_transport(monkeypatch)
    calls = {
        "live_session": synthetic.new_live_session,
        "live_execute": lambda: synthetic.execute_live(old_session, old_permit),
        "synthetic_session": ops.new_synthetic_session,
        "synthetic_execute": lambda: ops.execute_synthetic(session, permit, worker=worker()),
        "cross_session": lambda: ops.execute_live(old_session, permit),
        "cross_permit": lambda: ops.execute_live(session, old_permit),
    }
    with pytest.raises(SourceAuthoringOperationError):
        calls[wrong]()
    assert children == [] and ops.ledger[0] == synthetic.ledger[0] == 0
    assert not context[3]._results_by_request
    handle.revoke()


@pytest.mark.parametrize("handle_type", [LiveBrowserSession, LiveAuthorizedSourceAuthoringRequest])
def test_live_handles_are_private(handle_type):
    with pytest.raises(SourceAuthoringOperationError):
        handle_type()


@pytest.mark.parametrize("point", ["before_claim", "after_ready", "after_ticket", "after_dispatch", "after_result"])
def test_launch_revocation_never_replays_or_publishes(monkeypatch, point):
    ops, session, permit, handle, context, _ = live_setup(monkeypatch)
    children = fake_transport(monkeypatch)
    if point == "before_claim":
        handle.revoke()
        with pytest.raises(SourceAuthoringOperationError):
            ops.execute_live(session, permit)
        assert children == [] and ops.ledger[0] == 0
    else:
        method = {"after_ready": "prepare", "after_ticket": "prepare_ticket",
                  "after_dispatch": "handoff", "after_result": "collect"}[point]
        original = getattr(_BoundedLiveSupervisor, method)

        def revoke(self, *args, **kwargs):
            if point == "after_dispatch":
                handle.revoke()
            value = original(self, *args, **kwargs)
            if point != "after_dispatch":
                handle.revoke()
            return value

        monkeypatch.setattr(_BoundedLiveSupervisor, method, revoke)
        result = ops.execute_live(session, permit)
        assert result.state == ("REVOKED" if point in {"after_ready", "after_ticket"} else "OUTCOME_UNKNOWN")
        assert result.attempts == 1 and ops.ledger == (1, Decimal("0.01"))
        assert len(children) == 1 and children[0].poll() is not None
    assert not context[3]._results_by_request
    assert ops._active is None


@pytest.mark.parametrize("field,value", [("server_epoch", "wrong"), ("credential_configuration_generation", 2),
                                          ("body_sha256", "a" * 64), ("verified_input_tokens", 1)])
def test_altered_live_intent_refuses_before_claim(monkeypatch, field, value):
    ops, session, permit, handle, context, _ = live_setup(monkeypatch)
    children = fake_transport(monkeypatch)
    record = ops._records[permit._operation]
    intent = record.intent.model_copy(update={field: value})
    ops._records[permit._operation] = replace(record, intent=intent)
    with pytest.raises(ValueError):
        ops.execute_live(session, permit)
    assert children == [] and ops.ledger[0] == 0 and not context[3]._results_by_request
    handle.revoke()
