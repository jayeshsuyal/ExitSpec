from __future__ import annotations

import threading
from pathlib import Path

import pytest

from exitspec.performance_web_api import (
    handle_performance_web_api_request,
    is_performance_web_api_target,
)
from exitspec.performance_web_runtime import (
    PerformanceReadinessResult,
    PerformanceWebRunnerResult,
    PerformanceWebRuntime,
    PerformanceWebServerConfig,
    PerformanceWebStatus,
)


BASE = "/api/pocs/poc_inference_latency_demo"
PACK_URL = "/artifacts/run_demo/decision-packet.html"


def _runtime(
    tmp_path: Path,
    *,
    runner=None,
    launcher=lambda target: target(),
    max_operations: int = 64,
) -> PerformanceWebRuntime:
    return PerformanceWebRuntime(
        config=PerformanceWebServerConfig(
            output_root=tmp_path,
            max_operations=max_operations,
        ),
        readiness_probe=lambda execution: PerformanceReadinessResult(
            PerformanceWebStatus.COMPLETED
        ),
        runner=runner
        or (
            lambda execution, key: PerformanceWebRunnerResult(
                PerformanceWebStatus.COMPLETED,
                artifact_subject=object(),
            )
        ),
        evidence_pack_verifier=lambda artifact: PACK_URL,
        worker_launcher=launcher,
    )


def _request(
    runtime: PerformanceWebRuntime,
    *,
    method: str,
    target: str,
    payload=None,
):
    response = handle_performance_web_api_request(
        method=method,
        target=target,
        payload=payload,
        runtime=runtime,
    )
    assert response is not None
    return response


def test_unrelated_targets_are_not_claimed(tmp_path: Path):
    runtime = _runtime(tmp_path)
    assert not is_performance_web_api_target("/api/workspace")
    assert handle_performance_web_api_request(
        method="GET",
        target="/api/workspace",
        payload=None,
        runtime=runtime,
    ) is None


def test_readiness_get_is_side_effect_free_and_refresh_is_explicit(
    tmp_path: Path,
):
    calls = 0

    def probe(execution):
        nonlocal calls
        calls += 1
        return PerformanceReadinessResult(PerformanceWebStatus.COMPLETED)

    runtime = PerformanceWebRuntime(
        config=PerformanceWebServerConfig(output_root=tmp_path),
        readiness_probe=probe,
        runner=lambda execution, key: PerformanceWebRunnerResult(
            PerformanceWebStatus.NOT_PROVEN
        ),
        evidence_pack_verifier=lambda artifact: PACK_URL,
        worker_launcher=lambda target: target(),
    )

    first = _request(
        runtime,
        method="GET",
        target=BASE + "/readiness",
    )
    refreshed = _request(
        runtime,
        method="POST",
        target=BASE + "/readiness",
        payload={},
    )
    second = _request(
        runtime,
        method="GET",
        target=BASE + "/readiness",
    )

    assert calls == 1
    assert first.payload["status"] == "NOT_STARTED"
    assert refreshed.payload["status"] == "COMPLETED"
    assert second.payload == refreshed.payload


def test_start_accepts_only_idempotency_key_and_projects_verified_pack(
    tmp_path: Path,
):
    captured = {}

    def runner(execution, key):
        captured["key"] = key
        captured["endpoint"] = execution.endpoint
        captured["request_count"] = execution.authorized_request_count
        return PerformanceWebRunnerResult(
            PerformanceWebStatus.COMPLETED,
            artifact_subject=object(),
        )

    runtime = _runtime(tmp_path, runner=runner)
    response = _request(
        runtime,
        method="POST",
        target=BASE + "/runs",
        payload={"idempotency_key": "browser-run-1"},
    )

    assert response.status == 202
    assert response.payload["replayed"] is False
    assert response.payload["operation"]["status"] == "COMPLETED"
    assert response.payload["operation"]["evidence_pack_url"] == PACK_URL
    assert captured == {
        "key": "browser-run-1",
        "endpoint": "http://127.0.0.1:8000/v1/chat/completions",
        "request_count": 111,
    }

    replay = _request(
        runtime,
        method="POST",
        target=BASE + "/runs",
        payload={"idempotency_key": "browser-run-1"},
    )
    assert replay.status == 200
    assert replay.payload["replayed"] is True
    assert replay.payload["operation"] == response.payload["operation"]


@pytest.mark.parametrize(
    "payload",
    (
        {},
        {"idempotency_key": "key", "endpoint": "https://evil.test"},
        {"idempotency_key": "key", "model": "attacker/model"},
        {"idempotency_key": "key", "request_count": 1},
        {"idempotency_key": "key", "api_key": "secret"},
        {"idempotency_key": "key", "output_root": "/tmp/evil"},
        ["not", "an", "object"],
        None,
    ),
)
def test_start_rejects_missing_extra_or_nonobject_payloads(
    tmp_path: Path,
    payload,
):
    response = _request(
        _runtime(tmp_path),
        method="POST",
        target=BASE + "/runs",
        payload=payload,
    )
    assert response.status == 400
    assert response.payload == {
        "error": "Performance API request is invalid."
    }


@pytest.mark.parametrize(
    "target",
    (
        BASE + "/readiness?refresh=true",
        BASE + "/runs?endpoint=https://evil.test",
        BASE + "/runs;adapter=evil",
        BASE + "/evidence#fragment",
        "http://evil.test" + BASE + "/readiness",
    ),
)
def test_parameters_and_absolute_targets_fail_closed(
    tmp_path: Path,
    target: str,
):
    response = _request(
        _runtime(tmp_path),
        method="GET",
        target=target,
    )
    assert response.status == 400
    assert response.payload == {
        "error": "Performance API request is invalid."
    }


def test_latest_specific_and_evidence_reads_never_execute(tmp_path: Path):
    runner_calls = 0

    def runner(execution, key):
        nonlocal runner_calls
        runner_calls += 1
        return PerformanceWebRunnerResult(
            PerformanceWebStatus.COMPLETED,
            artifact_subject=object(),
        )

    runtime = _runtime(tmp_path, runner=runner)
    before = _request(
        runtime,
        method="GET",
        target=BASE + "/runs/latest",
    )
    assert before.payload["status"] == "NOT_STARTED"
    assert runner_calls == 0

    started = _request(
        runtime,
        method="POST",
        target=BASE + "/runs",
        payload={"idempotency_key": "read-projection"},
    )
    operation_id = started.payload["operation"]["operation_id"]
    specific = _request(
        runtime,
        method="GET",
        target=BASE + "/runs/" + operation_id,
    )
    evidence = _request(
        runtime,
        method="GET",
        target=BASE + "/evidence",
    )

    assert runner_calls == 1
    assert specific.payload["operation_id"] == operation_id
    assert specific.payload["is_terminal"] is True
    assert evidence.payload == {
        "poc_id": "poc_inference_latency_demo",
        "operation_id": operation_id,
        "execution_status": "COMPLETED",
        "evidence_pack_url": PACK_URL,
    }
    assert runner_calls == 1


def test_unknown_operation_and_method_are_safe(tmp_path: Path):
    runtime = _runtime(tmp_path)
    missing = _request(
        runtime,
        method="GET",
        target=BASE + "/runs/pwop_" + "0" * 32,
    )
    invalid = _request(
        runtime,
        method="GET",
        target=BASE + "/runs/../../etc/passwd",
    )
    method = _request(
        runtime,
        method="DELETE",
        target=BASE + "/readiness",
    )

    assert missing.status == 404
    assert invalid.status == 404
    assert method.status == 405
    assert "0" * 32 not in str(missing.payload)
    assert "passwd" not in str(invalid.payload)


def test_different_key_conflict_and_capacity_map_without_details(
    tmp_path: Path,
):
    entered = threading.Event()
    release = threading.Event()
    workers = []

    def runner(execution, key):
        entered.set()
        assert release.wait(timeout=5)
        return PerformanceWebRunnerResult(
            PerformanceWebStatus.NOT_PROVEN
        )

    def launch(target):
        worker = threading.Thread(target=target, daemon=True)
        workers.append(worker)
        worker.start()

    runtime = _runtime(tmp_path, runner=runner, launcher=launch)
    _request(
        runtime,
        method="POST",
        target=BASE + "/runs",
        payload={"idempotency_key": "first"},
    )
    assert entered.wait(timeout=5)
    conflict = _request(
        runtime,
        method="POST",
        target=BASE + "/runs",
        payload={"idempotency_key": "second"},
    )
    assert conflict.status == 409
    assert conflict.payload == {
        "error": "Another performance operation is already active."
    }

    release.set()
    for worker in workers:
        worker.join(timeout=5)

    bounded = _runtime(tmp_path / "bounded", max_operations=1)
    _request(
        bounded,
        method="POST",
        target=BASE + "/runs",
        payload={"idempotency_key": "only"},
    )
    capacity = _request(
        bounded,
        method="POST",
        target=BASE + "/runs",
        payload={"idempotency_key": "second"},
    )
    assert capacity.status == 429
    assert capacity.payload == {
        "error": "Performance operation capacity is exhausted."
    }
