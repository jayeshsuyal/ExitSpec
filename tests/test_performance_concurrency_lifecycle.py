"""Real client concurrency excludes a completed server handler's cleanup tail."""

import threading
import traceback
from concurrent.futures import ThreadPoolExecutor

import pytest

from exitspec import performance_probe as probe
from tests import test_performance_runner as runner_tests


@pytest.mark.parametrize("injected_workers", [4, 5])
def test_client_bound_survives_server_cleanup_and_rejects_extra_worker(
    tmp_path, monkeypatch, injected_workers
):
    tail_arrived, tail_release = threading.Event(), threading.Event()
    batch_arrived, batch_release = threading.Event(), threading.Event()
    local = threading.local()
    lock = threading.Lock()
    states, observers, events = [], [], []
    active = {}
    calls = closes = peak = batch_count = 0
    real_begin = runner_tests._EndpointState.begin
    real_end = runner_tests._EndpointState.end
    real_send = probe.OpenAIHTTPTransport.send
    real_executor = probe.ThreadPoolExecutor
    real_observer = runner_tests._track_client_attempts

    def begin(state, authorization):
        nonlocal batch_count
        number = real_begin(state, authorization)
        local.number = number
        if number == 1:
            states.append(state)
        if 2 <= number <= injected_workers + 1:
            with lock:
                batch_count += 1
                if batch_count == injected_workers:
                    batch_arrived.set()
            assert batch_release.wait(5), "Response batch was not released"
        return number

    def end(state):
        if local.number == 1:
            tail_arrived.set()
            assert tail_release.wait(5), "Completed preflight cleanup was not released"
        real_end(state)

    def send(transport, request):
        nonlocal calls, closes, peak
        with lock:
            calls += 1
            number = calls
            active[number] = request.request_id
            peak = max(peak, len(active))
            events.append(("send", number, request.request_id, tuple(active)))
        try:
            response = real_send(transport, request)
        except BaseException:
            with lock:
                del active[number]
            raise
        real_close = response._closer

        def close():
            nonlocal closes
            try:
                real_close()
            finally:
                with lock:
                    del active[number]
                    closes += 1

        response._closer = close
        return response

    def executor(*, max_workers, **kwargs):
        # Deliberately violate the configured bound only in the negative control.
        return real_executor(
            max_workers=injected_workers if max_workers == 4 else max_workers,
            **kwargs,
        )

    def observe(patch):
        activity = real_observer(patch)
        observers.append(activity)
        return activity

    monkeypatch.setattr(runner_tests._EndpointState, "begin", begin)
    monkeypatch.setattr(runner_tests._EndpointState, "end", end)
    monkeypatch.setattr(probe.OpenAIHTTPTransport, "send", send)
    monkeypatch.setattr(probe, "ThreadPoolExecutor", executor)
    monkeypatch.setattr(runner_tests, "_track_client_attempts", observe)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            runner_tests.test_full_live_sse_loop_returns_only_recomputed_verified_pass,
            tmp_path,
            monkeypatch,
        )
        try:
            assert tail_arrived.wait(5)
            assert batch_arrived.wait(5)
            state, activity = states[0], observers[0]
            with lock, state.lock:
                assert state.active == state.peak_active == injected_workers + 1
                assert len(active) == peak == injected_workers
                assert calls == injected_workers + 1
                assert closes == 1
            assert activity == {
                "active": injected_workers,
                "peak_active": injected_workers,
                "started": injected_workers + 1,
                "completed": 1,
            }
            assert not future.done()
        finally:
            batch_release.set()
            tail_release.set()
        if injected_workers == 4:
            future.result(timeout=20)
        else:
            with pytest.raises(AssertionError) as error:
                future.result(timeout=20)
            assert any(
                frame.line == 'assert activity["peak_active"] <= 4'
                for frame in traceback.extract_tb(error.value.__traceback__)
            )
    assert calls == closes == state.request_count == 111
    assert active == {}
    assert activity["active"] == 0
    assert activity["started"] == activity["completed"] == 111
    assert peak == activity["peak_active"] == injected_workers
    assert (peak <= 4) is (injected_workers == 4)
    assert events[0][3] == (1,)
    assert events[1][3] == (2,)
    assert events[11][3] == (12,)
    assert all(event[2].startswith("warmup-") for event in events[1:11])
    assert all(event[2].startswith("measured-") for event in events[11:])
