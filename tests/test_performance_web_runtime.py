from __future__ import annotations

import inspect
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from exitspec.performance_web_runtime import (
    PerformanceReadinessResult,
    PerformanceWebCapacityError,
    PerformanceWebConflictError,
    PerformanceWebOperationNotFound,
    PerformanceWebRunnerResult,
    PerformanceWebRuntime,
    PerformanceWebServerConfig,
    PerformanceWebStatus,
)
from exitspec.performance_workspace import (
    PERFORMANCE_CONTRACT_HASH,
    PERFORMANCE_POC_ID,
)


VALID_PACK_URL = "/artifacts/run_demo/decision-packet.html"


def _inline(target) -> None:
    target()


def _runtime(
    tmp_path: Path,
    *,
    readiness_probe=None,
    runner=None,
    verifier=None,
    worker_launcher=_inline,
    max_operations: int = 64,
    api_key: str | None = None,
) -> PerformanceWebRuntime:
    return PerformanceWebRuntime(
        config=PerformanceWebServerConfig(
            output_root=tmp_path,
            api_key=api_key,
            max_operations=max_operations,
        ),
        readiness_probe=readiness_probe
        or (
            lambda execution: PerformanceReadinessResult(
                PerformanceWebStatus.COMPLETED
            )
        ),
        runner=runner
        or (
            lambda execution, key: PerformanceWebRunnerResult(
                PerformanceWebStatus.COMPLETED,
                artifact_subject=object(),
            )
        ),
        evidence_pack_verifier=verifier
        or (lambda artifact: VALID_PACK_URL),
        worker_launcher=worker_launcher,
    )


def test_construction_and_reads_start_no_probe_runner_or_worker(
    tmp_path: Path,
):
    calls = {"probe": 0, "runner": 0, "verifier": 0, "worker": 0}

    def probe(execution):
        calls["probe"] += 1
        return PerformanceReadinessResult(
            PerformanceWebStatus.COMPLETED
        )

    def runner(execution, key):
        calls["runner"] += 1
        return PerformanceWebRunnerResult(
            PerformanceWebStatus.COMPLETED,
            artifact_subject=object(),
        )

    def verifier(artifact):
        calls["verifier"] += 1
        return VALID_PACK_URL

    def launcher(target):
        calls["worker"] += 1
        target()

    runtime = _runtime(
        tmp_path,
        readiness_probe=probe,
        runner=runner,
        verifier=verifier,
        worker_launcher=launcher,
    )

    readiness = runtime.readiness_snapshot()
    latest = runtime.latest_operation_snapshot()
    runtime.readiness_snapshot()
    runtime.latest_operation_snapshot()

    assert calls == {
        "probe": 0,
        "runner": 0,
        "verifier": 0,
        "worker": 0,
    }
    assert readiness.status is PerformanceWebStatus.NOT_STARTED
    assert latest.status is PerformanceWebStatus.NOT_STARTED
    assert latest.operation_id is None
    assert latest.evidence_pack_url is None


def test_browser_start_has_no_execution_payload_and_uses_exact_bundle(
    tmp_path: Path,
):
    captured = {}
    api_key = "server-owned-secret"

    def runner(execution, key):
        captured["execution"] = execution
        captured["key"] = key
        return PerformanceWebRunnerResult(
            PerformanceWebStatus.COMPLETED,
            artifact_subject={"verified": "by dependency"},
        )

    runtime = _runtime(tmp_path, runner=runner, api_key=api_key)
    signature = inspect.signature(runtime.start)

    assert tuple(signature.parameters) == ("idempotency_key",)
    with pytest.raises(TypeError):
        runtime.start(  # type: ignore[call-arg]
            idempotency_key="browser-key",
            endpoint="https://evil.example.test",
            model="attacker/model",
            request_count=1,
            output_root=Path("/tmp/attacker"),
            api_key="browser-secret",
        )

    started = runtime.start(idempotency_key="browser-key")
    execution = captured["execution"]

    assert started.operation.status is PerformanceWebStatus.COMPLETED
    assert execution.poc_id == PERFORMANCE_POC_ID
    assert execution.contract_hash == PERFORMANCE_CONTRACT_HASH
    assert execution.endpoint == (
        "http://127.0.0.1:8000/v1/chat/completions"
    )
    assert execution.model == "Qwen/Qwen2.5-0.5B-Instruct"
    assert execution.request_count == 100
    assert execution.concurrency == 4
    assert execution.warmup_count == 10
    assert execution.authorized_request_count == 111
    assert execution.output_root == tmp_path
    assert execution.api_key == api_key
    assert execution.credential_endpoint == execution.endpoint
    assert captured["key"] == "browser-key"
    assert api_key not in repr(execution)
    assert api_key not in repr(runtime._config)  # type: ignore[attr-defined]


def test_explicit_readiness_refresh_is_bounded_and_read_is_cached(
    tmp_path: Path,
):
    calls = 0

    def probe(execution):
        nonlocal calls
        calls += 1
        return PerformanceReadinessResult(
            PerformanceWebStatus.COMPLETED
        )

    runtime = _runtime(tmp_path, readiness_probe=probe)

    refreshed = runtime.refresh_readiness()
    first_read = runtime.readiness_snapshot()
    second_read = runtime.readiness_snapshot()

    assert calls == 1
    assert refreshed.status is PerformanceWebStatus.COMPLETED
    assert first_read == second_read == refreshed
    assert refreshed.reason_code is None


@pytest.mark.parametrize(
    ("readiness_status", "expected_reason"),
    (
        (
            PerformanceWebStatus.BLOCKED,
            "ENDPOINT_PREFLIGHT_FAILED",
        ),
        (
            PerformanceWebStatus.NOT_PROVEN,
            "READINESS_NOT_PROVEN",
        ),
    ),
)
def test_failed_readiness_never_calls_runner_or_invents_pack(
    tmp_path: Path,
    readiness_status: PerformanceWebStatus,
    expected_reason: str,
):
    runner_calls = 0
    verifier_calls = 0

    def runner(execution, key):
        nonlocal runner_calls
        runner_calls += 1
        raise AssertionError("runner must not execute")

    def verifier(artifact):
        nonlocal verifier_calls
        verifier_calls += 1
        raise AssertionError("verifier must not execute")

    runtime = _runtime(
        tmp_path,
        readiness_probe=lambda execution: PerformanceReadinessResult(
            readiness_status
        ),
        runner=runner,
        verifier=verifier,
    )

    result = runtime.refresh_readiness()

    assert result.status is readiness_status
    assert result.reason_code == expected_reason
    assert runtime.latest_operation_snapshot().status is (
        PerformanceWebStatus.NOT_STARTED
    )
    assert runtime.latest_operation_snapshot().evidence_pack_url is None
    assert runner_calls == verifier_calls == 0


def test_start_leaves_optional_readiness_probe_to_cli_runner_authority(
    tmp_path: Path,
):
    probe_calls = 0
    runner_calls = 0

    def probe(execution):
        nonlocal probe_calls
        probe_calls += 1
        return PerformanceReadinessResult(
            PerformanceWebStatus.BLOCKED
        )

    def runner(execution, key):
        nonlocal runner_calls
        runner_calls += 1
        return PerformanceWebRunnerResult(
            PerformanceWebStatus.COMPLETED,
            artifact_subject=object(),
        )

    runtime = _runtime(
        tmp_path,
        readiness_probe=probe,
        runner=runner,
    )
    started = runtime.start(idempotency_key="runner-owned-preflight")

    assert started.operation.status is PerformanceWebStatus.COMPLETED
    assert started.operation.evidence_pack_url == VALID_PACK_URL
    assert probe_calls == 0
    assert runner_calls == 1


def test_concurrent_same_key_is_one_flight_and_every_retry_replays(
    tmp_path: Path,
):
    runner_entered = threading.Event()
    release_runner = threading.Event()
    runner_calls = 0
    runner_lock = threading.Lock()
    workers: list[threading.Thread] = []
    workers_lock = threading.Lock()

    def runner(execution, key):
        nonlocal runner_calls
        with runner_lock:
            runner_calls += 1
        runner_entered.set()
        assert release_runner.wait(timeout=5)
        return PerformanceWebRunnerResult(
            PerformanceWebStatus.COMPLETED,
            artifact_subject=object(),
        )

    def launcher(target):
        worker = threading.Thread(target=target, daemon=True)
        with workers_lock:
            workers.append(worker)
        worker.start()

    runtime = _runtime(
        tmp_path,
        runner=runner,
        worker_launcher=launcher,
    )

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [
            executor.submit(
                runtime.start,
                idempotency_key="same-key",
            )
            for _ in range(8)
        ]
        assert runner_entered.wait(timeout=5)
        starts = [future.result(timeout=5) for future in futures]

    assert len({item.operation.operation_id for item in starts}) == 1
    assert sum(not item.replayed for item in starts) == 1
    assert runner_calls == 1
    assert runtime.latest_operation_snapshot().status is (
        PerformanceWebStatus.RUNNING
    )

    release_runner.set()
    for worker in workers:
        worker.join(timeout=5)

    terminal = runtime.latest_operation_snapshot()
    replay = runtime.start(idempotency_key="same-key")
    assert terminal.status is PerformanceWebStatus.COMPLETED
    assert terminal.evidence_pack_url == VALID_PACK_URL
    assert replay.replayed is True
    assert replay.operation == terminal
    assert runner_calls == 1


def test_different_key_conflicts_while_single_flight_is_active(
    tmp_path: Path,
):
    runner_entered = threading.Event()
    release_runner = threading.Event()
    workers = []

    def runner(execution, key):
        runner_entered.set()
        assert release_runner.wait(timeout=5)
        return PerformanceWebRunnerResult(
            PerformanceWebStatus.COMPLETED,
            artifact_subject=object(),
        )

    def launcher(target):
        worker = threading.Thread(target=target, daemon=True)
        workers.append(worker)
        worker.start()

    runtime = _runtime(
        tmp_path,
        runner=runner,
        worker_launcher=launcher,
    )
    runtime.start(idempotency_key="first-key")
    assert runner_entered.wait(timeout=5)

    with pytest.raises(
        PerformanceWebConflictError,
        match="already owns",
    ):
        runtime.start(idempotency_key="different-key")

    release_runner.set()
    for worker in workers:
        worker.join(timeout=5)


def test_terminal_replay_is_idempotent_and_does_not_rerun(
    tmp_path: Path,
):
    calls = {"runner": 0, "verifier": 0}

    def runner(execution, key):
        calls["runner"] += 1
        return PerformanceWebRunnerResult(
            PerformanceWebStatus.COMPLETED,
            artifact_subject=object(),
        )

    def verifier(artifact):
        calls["verifier"] += 1
        return VALID_PACK_URL

    runtime = _runtime(
        tmp_path,
        runner=runner,
        verifier=verifier,
    )
    first = runtime.start(idempotency_key="replay-key")
    replay = runtime.start(idempotency_key="replay-key")

    assert first.replayed is False
    assert replay.replayed is True
    assert replay.operation == first.operation
    assert calls == {"runner": 1, "verifier": 1}


def test_conflicting_key_can_start_after_prior_flight_is_terminal(
    tmp_path: Path,
):
    calls = 0

    def runner(execution, key):
        nonlocal calls
        calls += 1
        return PerformanceWebRunnerResult(
            PerformanceWebStatus.COMPLETED,
            artifact_subject=object(),
        )

    runtime = _runtime(tmp_path, runner=runner)
    first = runtime.start(idempotency_key="first")
    second = runtime.start(idempotency_key="second")

    assert first.operation.operation_id != second.operation.operation_id
    assert second.replayed is False
    assert calls == 2


def test_runner_exception_is_not_proven_without_pack(
    tmp_path: Path,
):
    verifier_calls = 0

    def runner(execution, key):
        raise RuntimeError("secret provider failure")

    def verifier(artifact):
        nonlocal verifier_calls
        verifier_calls += 1
        return VALID_PACK_URL

    runtime = _runtime(
        tmp_path,
        runner=runner,
        verifier=verifier,
    )
    result = runtime.start(idempotency_key="runner-failure")

    assert result.operation.status is PerformanceWebStatus.NOT_PROVEN
    assert result.operation.reason_code == "RUNNER_INTERNAL_FAILURE"
    assert result.operation.evidence_pack_url is None
    assert "secret provider failure" not in repr(result)
    assert verifier_calls == 0


@pytest.mark.parametrize(
    ("runner_status", "reason"),
    (
        (PerformanceWebStatus.BLOCKED, "RUNNER_BLOCKED"),
        (PerformanceWebStatus.NOT_PROVEN, "RUNNER_NOT_PROVEN"),
    ),
)
def test_noncompleted_runner_result_has_no_pack(
    tmp_path: Path,
    runner_status: PerformanceWebStatus,
    reason: str,
):
    verifier_calls = 0

    def verifier(artifact):
        nonlocal verifier_calls
        verifier_calls += 1
        return VALID_PACK_URL

    runtime = _runtime(
        tmp_path,
        runner=lambda execution, key: PerformanceWebRunnerResult(
            runner_status
        ),
        verifier=verifier,
    )
    result = runtime.start(idempotency_key="terminal-runner")

    assert result.operation.status is runner_status
    assert result.operation.reason_code == reason
    assert result.operation.evidence_pack_url is None
    assert verifier_calls == 0


@pytest.mark.parametrize(
    "unsafe_url",
    (
        "https://evil.example.test/decision-packet.html",
        "//evil.example.test/decision-packet.html",
        "/artifacts/../decision-packet.html",
        "/artifacts/%2e%2e/decision-packet.html",
        "/artifacts/run_demo/decision-packet.html?download=1",
        "/artifacts/run_demo/not-the-pack.html",
        "/outside/run_demo/decision-packet.html",
        "",
    ),
)
def test_invalid_artifact_url_fails_closed(
    tmp_path: Path,
    unsafe_url: str,
):
    runtime = _runtime(
        tmp_path,
        verifier=lambda artifact: unsafe_url,
    )
    result = runtime.start(idempotency_key="invalid-url")

    assert result.operation.status is PerformanceWebStatus.NOT_PROVEN
    assert result.operation.reason_code == "EVIDENCE_PACK_URL_INVALID"
    assert result.operation.evidence_pack_url is None


def test_verifier_failure_fails_closed_without_pack(tmp_path: Path):
    def verifier(artifact):
        raise ValueError("artifact bytes do not verify")

    runtime = _runtime(tmp_path, verifier=verifier)
    result = runtime.start(idempotency_key="verifier-failure")

    assert result.operation.status is PerformanceWebStatus.NOT_PROVEN
    assert result.operation.reason_code == "EVIDENCE_VERIFICATION_FAILED"
    assert result.operation.evidence_pack_url is None


def test_malformed_dependency_results_fail_closed(tmp_path: Path):
    with pytest.raises(ValueError, match="Readiness result"):
        PerformanceReadinessResult("COMPLETED")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="Runner result"):
        PerformanceWebRunnerResult(  # type: ignore[arg-type]
            "BLOCKED",
        )

    runtime = _runtime(
        tmp_path,
        readiness_probe=lambda execution: object(),
    )
    result = runtime.refresh_readiness()

    assert result.status is PerformanceWebStatus.NOT_PROVEN
    assert result.reason_code == "READINESS_INTERNAL_FAILURE"
    assert runtime.latest_operation_snapshot().evidence_pack_url is None


def test_worker_start_failure_is_not_proven_without_execution(
    tmp_path: Path,
):
    calls = {"probe": 0, "runner": 0}

    def probe(execution):
        calls["probe"] += 1
        return PerformanceReadinessResult(
            PerformanceWebStatus.COMPLETED
        )

    def runner(execution, key):
        calls["runner"] += 1
        return PerformanceWebRunnerResult(
            PerformanceWebStatus.COMPLETED,
            artifact_subject=object(),
        )

    def launcher(target):
        raise RuntimeError("thread unavailable")

    runtime = _runtime(
        tmp_path,
        readiness_probe=probe,
        runner=runner,
        worker_launcher=launcher,
    )
    result = runtime.start(idempotency_key="worker-start-failure")

    assert result.operation.status is PerformanceWebStatus.NOT_PROVEN
    assert result.operation.reason_code == "WORKER_START_FAILED"
    assert result.operation.evidence_pack_url is None
    assert calls == {"probe": 0, "runner": 0}


def test_public_snapshots_are_read_only_copies(tmp_path: Path):
    runtime = _runtime(tmp_path)
    readiness = runtime.readiness_snapshot()
    latest = runtime.latest_operation_snapshot()

    with pytest.raises(FrozenInstanceError):
        readiness.status = PerformanceWebStatus.RUNNING  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        latest.evidence_pack_url = VALID_PACK_URL  # type: ignore[misc]

    completed = runtime.start(idempotency_key="immutable")
    observed = runtime.operation_snapshot(
        completed.operation.operation_id
    )
    with pytest.raises(FrozenInstanceError):
        observed.status = PerformanceWebStatus.BLOCKED  # type: ignore[misc]
    assert runtime.operation_snapshot(
        completed.operation.operation_id
    ).status is PerformanceWebStatus.COMPLETED


def test_operation_history_and_idempotency_key_are_bounded(
    tmp_path: Path,
):
    runtime = _runtime(tmp_path, max_operations=1)
    runtime.start(idempotency_key="only-operation")

    with pytest.raises(PerformanceWebCapacityError, match="history is full"):
        runtime.start(idempotency_key="one-too-many")
    with pytest.raises(ValueError, match="bounded"):
        runtime.start(idempotency_key="x" * 257)


def test_unknown_operation_reads_fail_without_side_effects(
    tmp_path: Path,
):
    calls = 0

    def probe(execution):
        nonlocal calls
        calls += 1
        return PerformanceReadinessResult(
            PerformanceWebStatus.COMPLETED
        )

    runtime = _runtime(tmp_path, readiness_probe=probe)

    with pytest.raises(PerformanceWebOperationNotFound, match="not found"):
        runtime.operation_snapshot("pwop_" + "0" * 32)
    with pytest.raises(PerformanceWebOperationNotFound, match="not found"):
        runtime.operation_snapshot("../../etc/passwd")
    assert calls == 0
