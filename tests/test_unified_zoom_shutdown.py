"""Serialize close against pairing, queued actions and admitted child launch."""

from __future__ import annotations

import threading

import pytest

from exitspec.zoom_live_runtime import ZoomLiveError, ZoomLiveSettings
from tests.test_meeting_session_web_transport import _create_draft
from tests.test_unified_zoom_web import composition as _composition
from tests.test_zoom_live_runtime import FakeChild, settings

composition = _composition


def start_payload(session, key="shutdown-start-001"):
    return {"action": "start", "session_id": session, "idempotency_key": key, "consent_acknowledged": True}


@pytest.mark.parametrize("pause_at", ["settings", "owner-entry"])
def test_close_wins_before_pair_commit(composition, monkeypatch, pause_at):
    server = composition
    poc = _create_draft(server)
    runtime = server.zoom_live_runtime
    entered, resume = threading.Event(), threading.Event()
    result = {}
    original_validate, original_owner = ZoomLiveSettings.validate, runtime._run_if_open

    def pause():
        entered.set()
        assert resume.wait(2)

    def validate(value):
        original_validate(value)
        pause()

    def owner(poc_id, callback):
        if threading.current_thread().name == "pair-probe":
            pause()
        return original_owner(poc_id, callback)

    if pause_at == "settings":
        monkeypatch.setattr(ZoomLiveSettings, "validate", validate)
    else:
        monkeypatch.setattr(runtime, "_run_if_open", owner)

    def pair():
        try:
            result["value"] = runtime.pair(poc, settings())
        except ZoomLiveError:
            result["refused"] = True

    thread = threading.Thread(target=pair, name="pair-probe", daemon=True)
    thread.start()
    try:
        assert entered.wait(1)
        runtime.close()
        resume.set()
        thread.join(timeout=2)
        assert result == {"refused": True}
        assert runtime._record is None and not runtime._watcher.is_alive()
        assert server.poc_source_intake.list_receipts(poc) == ()
    finally:
        resume.set()
        thread.join(timeout=2)


@pytest.mark.parametrize("action", ["start", "stop", "process", "reset"])
def test_pair_wins_then_close_refuses_all_pending_actions(composition, action):
    server = composition
    poc = _create_draft(server)
    runtime = server.zoom_live_runtime
    paired = runtime.pair(poc, settings())
    runtime.close()
    payload = {"action": action, "session_id": paired["session_id"], "idempotency_key": "closed-action-001"}
    if action == "start":
        payload["consent_acknowledged"] = True
    with pytest.raises(ZoomLiveError):
        runtime.action(poc, payload)
    runtime.close()
    assert runtime._record.state == "REVOKED"
    assert runtime._record.settings is None and runtime._record.operations == {}
    assert runtime._launch_idle.is_set() and not runtime._watcher.is_alive()


def test_action_waiting_before_mutation_refuses_after_close(composition, monkeypatch):
    server = composition
    poc = _create_draft(server)
    runtime = server.zoom_live_runtime
    paired = runtime.pair(poc, settings())
    entered, resume = threading.Event(), threading.Event()
    original = runtime._run_if_open
    outcomes = []

    def owner(poc_id, callback):
        if threading.current_thread().name == "action-probe":
            entered.set()
            assert resume.wait(2)
        return original(poc_id, callback)

    monkeypatch.setattr(runtime, "_run_if_open", owner)

    def action():
        try:
            runtime.action(poc, start_payload(paired["session_id"]))
        except ZoomLiveError:
            outcomes.append("refused")

    thread = threading.Thread(target=action, name="action-probe", daemon=True)
    thread.start()
    try:
        assert entered.wait(1)
        runtime.close()
        resume.set()
        thread.join(timeout=2)
        assert outcomes == ["refused"] and runtime._record.operations == {}
    finally:
        resume.set()
        thread.join(timeout=2)


def test_close_wins_before_queued_launch_admission(composition, monkeypatch):
    server = composition
    poc = _create_draft(server)
    runtime = server.zoom_live_runtime
    paired = runtime.pair(poc, settings())
    entered, resume, finished = threading.Event(), threading.Event(), threading.Event()
    original = runtime._launch
    children = []

    def launch(record):
        entered.set()
        assert resume.wait(2)
        try:
            original(record)
        finally:
            finished.set()

    monkeypatch.setattr(runtime, "_launch", launch)
    monkeypatch.setattr(runtime, "_factory", lambda *args: children.append(args))
    runtime.action(poc, start_payload(paired["session_id"]))
    try:
        assert entered.wait(1)
        runtime.close()
        resume.set()
        assert finished.wait(1)
        assert children == [] and runtime._record.state == "REVOKED"
    finally:
        resume.set()


def test_launch_admitted_before_close_is_cleaned_without_owner_locks(composition, monkeypatch):
    server = composition
    poc = _create_draft(server)
    runtime = server.zoom_live_runtime
    paired = runtime.pair(poc, settings())
    entered, resume, closed = threading.Event(), threading.Event(), threading.Event()
    children, close_calls = [], []

    class ObservedChild(FakeChild):
        def close(self):
            assert not runtime._lock._is_owned()
            assert not server.poc_closure_service._lock._is_owned()
            close_calls.append(self)
            super().close()

    def factory(*args):
        child = ObservedChild(*args)
        children.append(child)
        entered.set()
        assert resume.wait(2)
        return child

    monkeypatch.setattr(runtime, "_factory", factory)
    runtime.action(poc, start_payload(paired["session_id"]))
    assert entered.wait(1)

    def close():
        runtime.close()
        closed.set()

    thread = threading.Thread(target=close, daemon=True)
    thread.start()
    try:
        assert runtime._closed.wait(1)
        assert not closed.is_set()
        resume.set()
        assert closed.wait(2)
        thread.join(timeout=1)
        assert len(children) == 1 and close_calls == children and children[0].closed
        assert runtime._record.state == "REVOKED" and runtime._record.child is None
        assert runtime._active_launches == 0 and runtime._launch_idle.is_set()
        runtime.close()
        assert close_calls == children
    finally:
        resume.set()
        thread.join(timeout=2)


@pytest.mark.parametrize("callback", ["pair", "action", "tick"])
def test_reentrant_close_waits_for_outer_transaction_and_reservation(composition, monkeypatch, callback):
    server = composition
    poc = _create_draft(server)
    runtime = server.zoom_live_runtime
    paired = runtime.pair(poc, settings())
    launched = threading.Event()
    observations, waits = [], []

    def observation():
        return (runtime._lock._is_owned(), server.poc_closure_service._active_mutations.get(poc, 0))

    class ObservedChild(FakeChild):
        def close(self):
            observations.append(observation())
            super().close()

    def factory(*args):
        child = ObservedChild(*args)
        launched.set()
        return child

    monkeypatch.setattr(runtime, "_factory", factory)
    runtime.action(poc, start_payload(paired["session_id"]))
    assert launched.wait(1) and runtime._launch_idle.wait(1)
    original_clock, original_wait = runtime._clock, runtime._launch_idle.wait
    caller = threading.current_thread()

    def close_in_clock():
        if threading.current_thread() is caller:
            runtime.close()
            assert observations == [] and waits == []
        return original_clock()

    def wait(timeout=None):
        waits.append(observation())
        return original_wait(timeout)

    monkeypatch.setattr(runtime, "_clock", close_in_clock)
    monkeypatch.setattr(runtime._launch_idle, "wait", wait)
    if callback == "pair":
        with pytest.raises(ZoomLiveError):
            runtime.pair(poc, settings())
    elif callback == "action":
        with pytest.raises(ZoomLiveError):
            runtime.action(poc, {"action": "reset", "session_id": paired["session_id"], "idempotency_key": "nested-reset-001"})
    else:
        runtime.tick()
    assert observations == [(False, 0)]
    assert waits and all(item == (False, 0) for item in waits)
    assert runtime._record.state == "REVOKED" and runtime._record.child is None
