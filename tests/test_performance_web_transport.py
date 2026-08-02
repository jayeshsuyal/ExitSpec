from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from http.client import HTTPConnection
from pathlib import Path

import pytest

from exitspec.performance_web_runtime import (
    PerformanceReadinessResult,
    PerformanceWebRunnerResult,
    PerformanceWebRuntime,
    PerformanceWebServerConfig,
    PerformanceWebStatus,
)
from exitspec.web import DemoSession, ExitSpecDemoServer


BASE = "/api/pocs/poc_inference_latency_demo"
PACK_URL = "/artifacts/run_demo/decision-packet.html"


def _runtime(
    tmp_path: Path,
    *,
    probe=None,
    runner=None,
) -> PerformanceWebRuntime:
    return PerformanceWebRuntime(
        config=PerformanceWebServerConfig(output_root=tmp_path),
        readiness_probe=probe
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
        evidence_pack_verifier=lambda artifact: PACK_URL,
        worker_launcher=lambda target: target(),
    )


@contextmanager
def _running_server(
    tmp_path: Path,
    *,
    runtime: PerformanceWebRuntime | None = None,
):
    session = DemoSession.synthetic_support_agent(output_root=tmp_path / "runs")
    server = ExitSpecDemoServer(
        ("127.0.0.1", 0),
        session,
        performance_runtime=runtime or _runtime(tmp_path / "performance"),
    )
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield server
    finally:
        server.shutdown()
        worker.join(timeout=5)
        server.server_close()


def _request(
    server: ExitSpecDemoServer,
    method: str,
    target: str,
    *,
    payload=None,
    raw_body: bytes | None = None,
    content_type: str | None = "application/json",
    origin: str | None = "same",
) -> tuple[int, dict]:
    body = (
        json.dumps(payload).encode("utf-8")
        if raw_body is None and payload is not None
        else raw_body
    )
    headers = {}
    if content_type is not None:
        headers["Content-Type"] = content_type
    if origin == "same":
        headers["Origin"] = "http://127.0.0.1:{0}".format(
            server.server_port
        )
    elif origin is not None:
        headers["Origin"] = origin
    connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    try:
        connection.request(method, target, body=body, headers=headers)
        response = connection.getresponse()
        return response.status, json.loads(response.read().decode("utf-8"))
    finally:
        connection.close()


def test_get_readiness_and_latest_are_read_only(tmp_path: Path):
    probe_calls = 0
    runner_calls = 0

    def probe(execution):
        nonlocal probe_calls
        probe_calls += 1
        return PerformanceReadinessResult(PerformanceWebStatus.COMPLETED)

    def runner(execution, key):
        nonlocal runner_calls
        runner_calls += 1
        return PerformanceWebRunnerResult(PerformanceWebStatus.NOT_PROVEN)

    runtime = _runtime(tmp_path, probe=probe, runner=runner)
    with _running_server(tmp_path, runtime=runtime) as server:
        readiness = _request(
            server,
            "GET",
            BASE + "/readiness",
            content_type=None,
            origin=None,
        )
        latest = _request(
            server,
            "GET",
            BASE + "/runs/latest",
            content_type=None,
            origin=None,
        )

    assert readiness[0] == latest[0] == 200
    assert readiness[1]["status"] == "NOT_STARTED"
    assert latest[1]["status"] == "NOT_STARTED"
    assert probe_calls == runner_calls == 0


def test_explicit_readiness_then_run_and_verified_pack_round_trip(
    tmp_path: Path,
):
    captured = {}

    def runner(execution, key):
        captured["key"] = key
        captured["endpoint"] = execution.endpoint
        captured["authorized_request_count"] = (
            execution.authorized_request_count
        )
        return PerformanceWebRunnerResult(
            PerformanceWebStatus.COMPLETED,
            artifact_subject=object(),
        )

    runtime = _runtime(tmp_path, runner=runner)
    with _running_server(tmp_path, runtime=runtime) as server:
        readiness_status, readiness = _request(
            server,
            "POST",
            BASE + "/readiness",
            payload={},
        )
        run_status, started = _request(
            server,
            "POST",
            BASE + "/runs",
            payload={"idempotency_key": "browser-round-trip"},
        )
        operation_id = started["operation"]["operation_id"]
        operation_status, operation = _request(
            server,
            "GET",
            BASE + "/runs/" + operation_id,
            content_type=None,
            origin=None,
        )
        evidence_status, evidence = _request(
            server,
            "GET",
            BASE + "/evidence",
            content_type=None,
            origin=None,
        )

    assert readiness_status == 200
    assert readiness["status"] == "COMPLETED"
    assert run_status == 202
    assert operation_status == evidence_status == 200
    assert operation["status"] == "COMPLETED"
    assert operation["evidence_pack_url"] == PACK_URL
    assert evidence["evidence_pack_url"] == PACK_URL
    assert captured == {
        "key": "browser-round-trip",
        "endpoint": "http://127.0.0.1:8000/v1/chat/completions",
        "authorized_request_count": 111,
    }


def test_exact_run_retry_does_not_execute_twice(tmp_path: Path):
    calls = 0

    def runner(execution, key):
        nonlocal calls
        calls += 1
        return PerformanceWebRunnerResult(
            PerformanceWebStatus.NOT_PROVEN
        )

    with _running_server(
        tmp_path,
        runtime=_runtime(tmp_path, runner=runner),
    ) as server:
        first = _request(
            server,
            "POST",
            BASE + "/runs",
            payload={"idempotency_key": "same-browser-key"},
        )
        replay = _request(
            server,
            "POST",
            BASE + "/runs",
            payload={"idempotency_key": "same-browser-key"},
        )

    assert first[0] == 202
    assert replay[0] == 200
    assert first[1]["operation"] == replay[1]["operation"]
    assert replay[1]["replayed"] is True
    assert calls == 1


@pytest.mark.parametrize(
    "payload",
    (
        {"idempotency_key": "key", "endpoint": "https://evil.test"},
        {"idempotency_key": "key", "model": "attacker/model"},
        {"idempotency_key": "key", "request_count": 1},
        {"idempotency_key": "key", "api_key": "browser-secret"},
        {"idempotency_key": "key", "output_root": "/tmp/evil"},
    ),
)
def test_browser_cannot_expand_execution_authority(
    tmp_path: Path,
    payload: dict,
):
    runner_calls = 0

    def runner(execution, key):
        nonlocal runner_calls
        runner_calls += 1
        return PerformanceWebRunnerResult(PerformanceWebStatus.NOT_PROVEN)

    with _running_server(
        tmp_path,
        runtime=_runtime(tmp_path, runner=runner),
    ) as server:
        status, response = _request(
            server,
            "POST",
            BASE + "/runs",
            payload=payload,
        )

    assert status == 400
    assert response == {"error": "Performance API request is invalid."}
    assert "browser-secret" not in json.dumps(response)
    assert runner_calls == 0


@pytest.mark.parametrize(
    ("content_type", "origin", "expected_status", "message"),
    (
        (
            "text/plain",
            "same",
            415,
            "Content-Type must be application/json.",
        ),
        (
            None,
            "same",
            415,
            "Content-Type must be application/json.",
        ),
        (
            "application/json",
            None,
            403,
            "Origin is not allowed.",
        ),
        (
            "application/json",
            "https://evil.test",
            403,
            "Origin is not allowed.",
        ),
    ),
)
def test_performance_writes_require_json_and_exact_origin(
    tmp_path: Path,
    content_type,
    origin,
    expected_status,
    message,
):
    with _running_server(tmp_path) as server:
        status, response = _request(
            server,
            "POST",
            BASE + "/runs",
            payload={"idempotency_key": "safe-key"},
            content_type=content_type,
            origin=origin,
        )

    assert status == expected_status
    assert response == {"error": message}


@pytest.mark.parametrize(
    "target",
    (
        BASE + "/runs?endpoint=https://evil.test",
        BASE + "/readiness;refresh=true",
        BASE + "/evidence#fragment",
    ),
)
def test_route_parameters_fail_before_execution(tmp_path: Path, target: str):
    runner_calls = 0

    def runner(execution, key):
        nonlocal runner_calls
        runner_calls += 1
        return PerformanceWebRunnerResult(PerformanceWebStatus.NOT_PROVEN)

    with _running_server(
        tmp_path,
        runtime=_runtime(tmp_path, runner=runner),
    ) as server:
        status, response = _request(
            server,
            "POST",
            target,
            payload={"idempotency_key": "safe-key"},
        )

    assert status == 400
    assert response == {"error": "Performance API request is invalid."}
    assert runner_calls == 0


def test_malformed_json_and_unsupported_method_are_safe(tmp_path: Path):
    with _running_server(tmp_path) as server:
        malformed = _request(
            server,
            "POST",
            BASE + "/runs",
            raw_body=b'{"idempotency_key":"secret-marker"',
        )
        unsupported = _request(
            server,
            "DELETE",
            BASE + "/readiness",
            content_type=None,
            origin=None,
        )

    assert malformed == (
        400,
        {"error": "Performance API request is invalid."},
    )
    assert "secret-marker" not in json.dumps(malformed[1])
    assert unsupported == (
        405,
        {"error": "Performance API method is not allowed."},
    )
