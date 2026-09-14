"""External deterministic controls: real startup queue, fake child, bounded barriers."""
import threading
from contextlib import contextmanager

import pytest

from tests import test_zoom_live_runtime as runtime_tests
from tests.test_zoom_live_runtime import POC_ID, action, start


@pytest.fixture
def rig():
    yield from runtime_tests.rig.__wrapped__()


def pause_launch(runtime, phase, monkeypatch):
    paused, release = threading.Event(), threading.Event()
    factory, transaction = runtime._factory, runtime._transaction
    stopped = False

    def hold():
        nonlocal stopped
        if not stopped:
            stopped = True
            paused.set()
            assert release.wait(2), "Control did not release the launch thread"

    def delayed_factory(*args):
        child = factory(*args)
        if phase == "factory_return":
            hold()
        return child

    @contextmanager
    def delayed_publication():
        with transaction():
            yield
        if (
            phase == "child_published"
            and getattr(runtime._effects, "launching", False)
            and runtime._record.child is not None
        ):
            hold()

    monkeypatch.setattr(runtime, "_factory", delayed_factory)
    monkeypatch.setattr(runtime, "_transaction", delayed_publication)
    return paused, release


@pytest.mark.parametrize("phase", ["factory_return", "child_published"])
def test_legitimate_early_events_survive_startup_queue(rig, monkeypatch, phase):
    runtime = rig[0]
    paused, release = pause_launch(runtime, phase, monkeypatch)
    try:
        action(runtime, "start")
        assert paused.wait(2)
        child = rig[2][-1]
        assert not runtime._launch_idle.is_set()
        assert (runtime._record.child is child) == (phase == "child_published")
        child.emit("offer")
        child.emit("listening")
        assert runtime.current(POC_ID)["state"] == "WAITING"
    finally:
        release.set()
        assert runtime._launch_idle.wait(2)
    assert runtime.current(POC_ID)["state"] == "LISTENING"
    assert [item["command"] for item in child.sent] == ["bind"]


@pytest.mark.parametrize("phase", ["factory_return", "child_published"])
def test_start_helper_waits_for_callback_readiness(rig, monkeypatch, phase):
    runtime = rig[0]
    paused, release = pause_launch(runtime, phase, monkeypatch)
    waiting, progressed, done = threading.Event(), threading.Event(), threading.Event()
    idle_wait = runtime._launch_idle.wait
    results, errors = [], []

    def observed_wait(timeout=None):
        if threading.current_thread() is worker:
            waiting.set()
            progressed.set()
        return idle_wait(timeout)

    def run_helper():
        try:
            results.append(start(rig))
        except Exception as exc:  # noqa: BLE001 - relay helper-thread failures to test assertions
            errors.append(exc)
        finally:
            done.set()
            progressed.set()

    monkeypatch.setattr(runtime._launch_idle, "wait", observed_wait)
    worker = threading.Thread(target=run_helper, daemon=True)
    try:
        worker.start()
        assert paused.wait(2)
        assert progressed.wait(2)
        assert waiting.is_set(), "Helper emitted before actual launch completion"
        assert not done.is_set()
        assert rig[2][-1].seq == 0, "Fake events must wait for callback readiness"
    finally:
        release.set()
        worker.join(2)
        assert not worker.is_alive()
        assert idle_wait(2)
    assert not errors
    assert results == [rig[2][-1]]
    assert runtime.current(POC_ID)["state"] == "LISTENING"
