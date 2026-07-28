from __future__ import annotations

from http import HTTPStatus

import pytest

from exitspec.models import VerdictStatus
from exitspec.poc_performance_run import (
    POCPerformanceRunCapacityExceeded,
    POCPerformanceRunConflict,
    POCPerformanceRunError,
    POCPerformanceRunInvalid,
    POCPerformanceRunNotFound,
    POCPerformanceRunSnapshot,
    POCPerformanceRunStatus,
    POCPerformanceStartSnapshot,
    ProcessLocalPOCPerformanceRunService,
)
from exitspec.poc_performance_run_web_api import (
    handle_poc_performance_run_web_api_request,
    is_poc_performance_run_web_api_target,
)


POC_ID = "poc_dynamic_api"
ROOT = f"/api/pocs/{POC_ID}"
OPERATION_ID = "prun_" + "a" * 32


def _snapshot(
    *,
    status: POCPerformanceRunStatus = POCPerformanceRunStatus.COMPLETED,
) -> POCPerformanceRunSnapshot:
    completed = status is POCPerformanceRunStatus.COMPLETED
    return POCPerformanceRunSnapshot(
        poc_id=POC_ID,
        contract_hash="b" * 64,
        workload_id="perf-dynamic-api",
        operation_id=(
            None
            if status is POCPerformanceRunStatus.NOT_STARTED
            else OPERATION_ID
        ),
        status=status,
        reason_code=None,
        verdict=VerdictStatus.PASS if completed else None,
        attempted_count=100 if completed else None,
        successful_count=100 if completed else None,
        error_count=0 if completed else None,
        p95_ttft_ms="12.4" if completed else None,
        error_rate_percent="0" if completed else None,
        evidence_pack_url=(
            "/artifacts/run_{0}/decision-packet.html".format("c" * 32)
            if completed
            else None
        ),
    )


def _runtime() -> ProcessLocalPOCPerformanceRunService:
    return object.__new__(ProcessLocalPOCPerformanceRunService)


def _handle(runtime, method, target, payload=None):
    response = handle_poc_performance_run_web_api_request(
        method=method,
        target=target,
        payload=payload,
        runtime=runtime,
    )
    assert response is not None
    return response


def test_exact_dynamic_run_namespace_detection():
    for target in (
        ROOT + "/runs",
        ROOT + "/runs/latest",
        ROOT + "/runs/" + OPERATION_ID,
        ROOT + "/evidence",
    ):
        assert is_poc_performance_run_web_api_target(target)
    for target in (
        ROOT,
        ROOT + "/agreement",
        "/api/workspace",
        "/app/pocs/" + POC_ID,
    ):
        assert not is_poc_performance_run_web_api_target(target)


def test_get_latest_operation_and_evidence_use_only_read_methods(monkeypatch):
    runtime = _runtime()
    calls = []

    def snapshot(self, poc_id):
        calls.append(("latest", poc_id))
        return _snapshot()

    def operation_snapshot(self, poc_id, operation_id):
        calls.append(("operation", poc_id, operation_id))
        return _snapshot()

    monkeypatch.setattr(
        ProcessLocalPOCPerformanceRunService,
        "snapshot",
        snapshot,
    )
    monkeypatch.setattr(
        ProcessLocalPOCPerformanceRunService,
        "operation_snapshot",
        operation_snapshot,
    )

    latest = _handle(runtime, "GET", ROOT + "/runs/latest")
    evidence = _handle(runtime, "GET", ROOT + "/evidence")
    operation = _handle(
        runtime,
        "GET",
        ROOT + "/runs/" + OPERATION_ID,
    )

    assert latest.status == evidence.status == operation.status == HTTPStatus.OK
    assert latest.payload["status"] == "COMPLETED"
    assert latest.payload["verdict"] == "PASS"
    assert latest.payload["p95_ttft_ms"] == "12.4"
    assert latest.payload["error_rate_percent"] == "0"
    assert latest.payload["is_terminal"] is True
    assert evidence.payload == latest.payload
    assert operation.payload == latest.payload
    assert calls == [
        ("latest", POC_ID),
        ("latest", POC_ID),
        ("operation", POC_ID, OPERATION_ID),
    ]


def test_start_projects_created_and_idempotent_replay(monkeypatch):
    runtime = _runtime()
    replayed = False
    calls = []

    def start(
        self,
        poc_id,
        *,
        execution_acknowledged,
        idempotency_key,
    ):
        calls.append(
            (poc_id, execution_acknowledged, idempotency_key)
        )
        return POCPerformanceStartSnapshot(
            _snapshot(status=POCPerformanceRunStatus.RUNNING),
            replayed,
        )

    monkeypatch.setattr(
        ProcessLocalPOCPerformanceRunService,
        "start",
        start,
    )
    body = {
        "execution_acknowledged": True,
        "idempotency_key": "dynamic-api-start",
    }
    created = _handle(runtime, "POST", ROOT + "/runs", body)
    replayed = True
    replay = _handle(runtime, "POST", ROOT + "/runs", body)

    assert created.status == HTTPStatus.ACCEPTED
    assert created.payload["replayed"] is False
    assert created.payload["operation"]["status"] == "RUNNING"
    assert replay.status == HTTPStatus.OK
    assert replay.payload["replayed"] is True
    assert calls == [
        (POC_ID, True, "dynamic-api-start"),
        (POC_ID, True, "dynamic-api-start"),
    ]


@pytest.mark.parametrize(
    "payload",
    (
        None,
        {},
        {"idempotency_key": "missing-ack"},
        {
            "execution_acknowledged": True,
            "idempotency_key": "extra-authority",
            "endpoint": "https://evil.test",
        },
    ),
)
def test_start_payload_is_exact_and_cannot_override_execution(payload):
    response = _handle(
        _runtime(),
        "POST",
        ROOT + "/runs",
        payload,
    )

    assert response.status == HTTPStatus.BAD_REQUEST
    assert response.payload == {
        "error": "Performance run request is invalid."
    }


@pytest.mark.parametrize(
    ("error", "status", "message"),
    (
        (
            POCPerformanceRunInvalid(),
            HTTPStatus.BAD_REQUEST,
            "Performance run request is invalid.",
        ),
        (
            POCPerformanceRunNotFound(),
            HTTPStatus.NOT_FOUND,
            "Performance operation was not found.",
        ),
        (
            POCPerformanceRunConflict(),
            HTTPStatus.CONFLICT,
            "Performance run conflicts with current POC state.",
        ),
        (
            POCPerformanceRunCapacityExceeded(),
            HTTPStatus.TOO_MANY_REQUESTS,
            "Performance run capacity is exhausted.",
        ),
        (
            POCPerformanceRunError(),
            HTTPStatus.SERVICE_UNAVAILABLE,
            "Performance run is unavailable.",
        ),
    ),
)
def test_runtime_failures_map_to_content_free_errors(
    monkeypatch,
    error,
    status,
    message,
):
    runtime = _runtime()

    def start(self, *args, **kwargs):
        raise error

    monkeypatch.setattr(
        ProcessLocalPOCPerformanceRunService,
        "start",
        start,
    )
    response = _handle(
        runtime,
        "POST",
        ROOT + "/runs",
        {
            "execution_acknowledged": True,
            "idempotency_key": "dynamic-api-error",
        },
    )

    assert response.status == status
    assert response.payload == {"error": message}


@pytest.mark.parametrize(
    "target",
    (
        ROOT + "/runs?endpoint=https://evil.test",
        ROOT + "/runs//latest",
        ROOT + "/runs/not-an-operation",
        ROOT + "/evidence/extra",
        "/api/pocs/not-valid/runs",
    ),
)
def test_malformed_targets_fail_closed(target):
    response = _handle(_runtime(), "GET", target)

    assert response.status == HTTPStatus.BAD_REQUEST
    assert response.payload == {
        "error": "Performance run request is invalid."
    }


@pytest.mark.parametrize(
    ("method", "target"),
    (
        ("GET", ROOT + "/runs"),
        ("POST", ROOT + "/runs/latest"),
        ("DELETE", ROOT + "/evidence"),
        ("PATCH", ROOT + "/runs/" + OPERATION_ID),
    ),
)
def test_unsupported_methods_are_json_405(method, target):
    response = _handle(
        _runtime(),
        method,
        target,
        None if method == "GET" else {},
    )

    assert response.status == HTTPStatus.METHOD_NOT_ALLOWED
    assert response.payload == {
        "error": "Performance run method is not allowed."
    }
