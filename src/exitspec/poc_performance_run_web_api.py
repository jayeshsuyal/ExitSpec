"""Pure HTTP projection for dynamic frozen-POC performance runs."""

from __future__ import annotations

from dataclasses import dataclass
from http import HTTPStatus
import re
from typing import Any, Mapping
from urllib.parse import urlparse

from .poc_creation import POC_ID_PATTERN
from .poc_performance_run import (
    POCPerformanceRunCapacityExceeded,
    POCPerformanceRunConflict,
    POCPerformanceRunError,
    POCPerformanceRunInvalid,
    POCPerformanceRunNotFound,
    POCPerformanceRunSnapshot,
    ProcessLocalPOCPerformanceRunService,
)


_POC_ID_RE = re.compile(POC_ID_PATTERN)
_OPERATION_ID_RE = re.compile(r"^prun_[a-f0-9]{32}$")
_START_FIELDS = {"execution_acknowledged", "idempotency_key"}


@dataclass(frozen=True, slots=True)
class POCPerformanceRunWebAPIResponse:
    status: HTTPStatus
    payload: dict[str, Any]


class POCPerformanceRunWebAPIRequestError(ValueError):
    pass


def is_poc_performance_run_web_api_target(target: str) -> bool:
    if type(target) is not str:
        return False
    return bool(
        re.match(
            r"^/api/+pocs/[^/]+/(?:runs|evidence)(?:/|$)",
            urlparse(target).path,
        )
    )


def handle_poc_performance_run_web_api_request(
    *,
    method: str,
    target: str,
    payload: Mapping[str, Any] | None,
    runtime: ProcessLocalPOCPerformanceRunService,
) -> POCPerformanceRunWebAPIResponse | None:
    if type(runtime) is not ProcessLocalPOCPerformanceRunService:
        raise TypeError("performance run runtime is invalid.")
    if not is_poc_performance_run_web_api_target(target):
        return None
    try:
        path = _exact_path(target)
        poc_id, resource, operation_id = _parse_path(path)
        if method == "GET":
            if payload is not None:
                raise POCPerformanceRunWebAPIRequestError
            if resource == "evidence" or operation_id == "latest":
                snapshot = runtime.snapshot(poc_id)
            elif operation_id is not None:
                snapshot = runtime.operation_snapshot(
                    poc_id,
                    operation_id,
                )
            else:
                return _error(
                    HTTPStatus.METHOD_NOT_ALLOWED,
                    "Performance run method is not allowed.",
                )
            return _ok(_snapshot_payload(snapshot))
        if method == "POST":
            if resource != "runs" or operation_id is not None:
                return _error(
                    HTTPStatus.METHOD_NOT_ALLOWED,
                    "Performance run method is not allowed.",
                )
            body = _object(payload)
            if set(body) != _START_FIELDS:
                raise POCPerformanceRunWebAPIRequestError
            started = runtime.start(
                poc_id,
                execution_acknowledged=body["execution_acknowledged"],
                idempotency_key=body["idempotency_key"],
            )
            return POCPerformanceRunWebAPIResponse(
                HTTPStatus.OK if started.replayed else HTTPStatus.ACCEPTED,
                {
                    "operation": _snapshot_payload(started.operation),
                    "replayed": started.replayed,
                },
            )
        return _error(
            HTTPStatus.METHOD_NOT_ALLOWED,
            "Performance run method is not allowed.",
        )
    except POCPerformanceRunWebAPIRequestError:
        return _error(
            HTTPStatus.BAD_REQUEST,
            "Performance run request is invalid.",
        )
    except POCPerformanceRunInvalid:
        return _error(
            HTTPStatus.BAD_REQUEST,
            "Performance run request is invalid.",
        )
    except POCPerformanceRunNotFound:
        return _error(
            HTTPStatus.NOT_FOUND,
            "Performance operation was not found.",
        )
    except POCPerformanceRunConflict:
        return _error(
            HTTPStatus.CONFLICT,
            "Performance run conflicts with current POC state.",
        )
    except POCPerformanceRunCapacityExceeded:
        return _error(
            HTTPStatus.TOO_MANY_REQUESTS,
            "Performance run capacity is exhausted.",
        )
    except POCPerformanceRunError:
        return _error(
            HTTPStatus.SERVICE_UNAVAILABLE,
            "Performance run is unavailable.",
        )


def _exact_path(target: str) -> str:
    parsed = urlparse(target)
    if (
        parsed.scheme
        or parsed.netloc
        or parsed.params
        or parsed.query
        or parsed.fragment
        or parsed.path != target
    ):
        raise POCPerformanceRunWebAPIRequestError
    return parsed.path


def _parse_path(path: str) -> tuple[str, str, str | None]:
    parts = path.strip("/").split("/")
    if (
        len(parts) not in {4, 5}
        or parts[:2] != ["api", "pocs"]
        or _POC_ID_RE.fullmatch(parts[2]) is None
        or parts[3] not in {"runs", "evidence"}
        or path.startswith("//")
        or "//" in path
    ):
        raise POCPerformanceRunWebAPIRequestError
    resource = parts[3]
    operation_id = None if len(parts) == 4 else parts[4]
    if resource == "evidence" and operation_id is not None:
        raise POCPerformanceRunWebAPIRequestError
    if operation_id is not None and (
        operation_id != "latest"
        and _OPERATION_ID_RE.fullmatch(operation_id) is None
    ):
        raise POCPerformanceRunWebAPIRequestError
    return parts[2], resource, operation_id


def _object(payload: Mapping[str, Any] | None) -> Mapping[str, Any]:
    if type(payload) is not dict or any(
        type(key) is not str for key in payload
    ):
        raise POCPerformanceRunWebAPIRequestError
    return payload


def _snapshot_payload(
    snapshot: POCPerformanceRunSnapshot,
) -> dict[str, Any]:
    if type(snapshot) is not POCPerformanceRunSnapshot:
        raise POCPerformanceRunError
    return {
        "poc_id": snapshot.poc_id,
        "contract_hash": snapshot.contract_hash,
        "workload_id": snapshot.workload_id,
        "operation_id": snapshot.operation_id,
        "status": snapshot.status.value,
        "reason_code": snapshot.reason_code,
        "verdict": (
            None if snapshot.verdict is None else snapshot.verdict.value
        ),
        "attempted_count": snapshot.attempted_count,
        "successful_count": snapshot.successful_count,
        "error_count": snapshot.error_count,
        "p95_ttft_ms": snapshot.p95_ttft_ms,
        "error_rate_percent": snapshot.error_rate_percent,
        "evidence_pack_url": snapshot.evidence_pack_url,
        "is_terminal": snapshot.is_terminal,
    }


def _ok(payload: dict[str, Any]) -> POCPerformanceRunWebAPIResponse:
    return POCPerformanceRunWebAPIResponse(HTTPStatus.OK, payload)


def _error(
    status: HTTPStatus,
    message: str,
) -> POCPerformanceRunWebAPIResponse:
    return POCPerformanceRunWebAPIResponse(status, {"error": message})


__all__ = [
    "POCPerformanceRunWebAPIRequestError",
    "POCPerformanceRunWebAPIResponse",
    "handle_poc_performance_run_web_api_request",
    "is_poc_performance_run_web_api_target",
]
