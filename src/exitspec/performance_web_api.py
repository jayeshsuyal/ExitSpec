"""Pure HTTP projection for the browser-safe performance coordinator.

The request handler in :mod:`exitspec.web` owns transport concerns such as
body limits, JSON decoding, media type, and same-origin enforcement. This
module owns only exact route matching, strict payload shape, safe error
mapping, and immutable public projections.
"""

from __future__ import annotations

from dataclasses import dataclass
from http import HTTPStatus
from typing import Any, Mapping
from urllib.parse import urlparse

from .performance_web_runtime import (
    PerformanceOperationSnapshot,
    PerformanceReadinessSnapshot,
    PerformanceStartSnapshot,
    PerformanceWebCapacityError,
    PerformanceWebConflictError,
    PerformanceWebOperationNotFound,
    PerformanceWebRuntime,
    PerformanceWebRuntimeError,
)
from .performance_workspace import PERFORMANCE_POC_ID


_POC_API_ROOT = "/api/pocs/{0}".format(PERFORMANCE_POC_ID)
_READINESS_PATH = _POC_API_ROOT + "/readiness"
_RUNS_PATH = _POC_API_ROOT + "/runs"
_LATEST_RUN_PATH = _RUNS_PATH + "/latest"
_EVIDENCE_PATH = _POC_API_ROOT + "/evidence"


@dataclass(frozen=True, slots=True)
class PerformanceWebAPIResponse:
    """Transport-neutral status and JSON object."""

    status: HTTPStatus
    payload: dict[str, Any]


class PerformanceWebAPIRequestError(ValueError):
    """A request targeted the API but violated its exact contract."""


def is_performance_web_api_target(target: str) -> bool:
    """Return whether a raw request target belongs to the performance API."""

    if type(target) is not str:
        return False
    parsed = urlparse(target)
    return parsed.path == _POC_API_ROOT or parsed.path.startswith(
        _POC_API_ROOT + "/"
    )


def handle_performance_web_api_request(
    *,
    method: str,
    target: str,
    payload: Mapping[str, Any] | None,
    runtime: PerformanceWebRuntime,
) -> PerformanceWebAPIResponse | None:
    """Handle one exact performance API request.

    ``None`` means the target is unrelated. Targeted malformed requests always
    receive a safe response and never fall through to another route.
    """

    if type(runtime) is not PerformanceWebRuntime:
        raise TypeError("runtime must be a PerformanceWebRuntime.")
    if not is_performance_web_api_target(target):
        return None

    try:
        path = _require_exact_local_target(target)
        return _dispatch(
            method=method,
            path=path,
            payload=payload,
            runtime=runtime,
        )
    except PerformanceWebAPIRequestError:
        return _error(
            HTTPStatus.BAD_REQUEST,
            "Performance API request is invalid.",
        )
    except PerformanceWebOperationNotFound:
        return _error(
            HTTPStatus.NOT_FOUND,
            "Performance operation was not found.",
        )
    except PerformanceWebConflictError:
        return _error(
            HTTPStatus.CONFLICT,
            "Another performance operation is already active.",
        )
    except PerformanceWebCapacityError:
        return _error(
            HTTPStatus.TOO_MANY_REQUESTS,
            "Performance operation capacity is exhausted.",
        )
    except ValueError:
        return _error(
            HTTPStatus.BAD_REQUEST,
            "Performance API request is invalid.",
        )
    except PerformanceWebRuntimeError:
        return _error(
            HTTPStatus.SERVICE_UNAVAILABLE,
            "Performance operation is unavailable.",
        )


def _dispatch(
    *,
    method: str,
    path: str,
    payload: Mapping[str, Any] | None,
    runtime: PerformanceWebRuntime,
) -> PerformanceWebAPIResponse:
    if type(method) is not str:
        raise PerformanceWebAPIRequestError

    if method == "GET":
        _require_no_payload(payload)
        if path == _READINESS_PATH:
            return _ok(_readiness_payload(runtime.readiness_snapshot()))
        if path == _LATEST_RUN_PATH:
            return _ok(
                _operation_payload(runtime.latest_operation_snapshot())
            )
        if path == _EVIDENCE_PATH:
            operation = runtime.latest_operation_snapshot()
            return _ok(
                {
                    "poc_id": operation.poc_id,
                    "operation_id": operation.operation_id,
                    "execution_status": operation.status.value,
                    "evidence_pack_url": operation.evidence_pack_url,
                }
            )
        operation_id = _operation_id_from_path(path)
        if operation_id is not None:
            return _ok(
                _operation_payload(
                    runtime.operation_snapshot(operation_id)
                )
            )
        return _error(HTTPStatus.NOT_FOUND, "Performance API route was not found.")

    if method == "POST":
        body = _require_object_payload(payload)
        if path == _READINESS_PATH:
            _require_only_fields(body, set())
            return _ok(_readiness_payload(runtime.refresh_readiness()))
        if path == _RUNS_PATH:
            _require_only_fields(body, {"idempotency_key"})
            if "idempotency_key" not in body:
                raise PerformanceWebAPIRequestError
            result = runtime.start(
                idempotency_key=body["idempotency_key"],
            )
            status = (
                HTTPStatus.OK
                if result.replayed
                else HTTPStatus.ACCEPTED
            )
            return PerformanceWebAPIResponse(
                status=status,
                payload=_start_payload(result),
            )
        return _error(HTTPStatus.NOT_FOUND, "Performance API route was not found.")

    return _error(
        HTTPStatus.METHOD_NOT_ALLOWED,
        "Performance API method is not allowed.",
    )


def _require_exact_local_target(target: str) -> str:
    parsed = urlparse(target)
    if (
        parsed.scheme
        or parsed.netloc
        or parsed.params
        or parsed.query
        or parsed.fragment
        or parsed.path != target
    ):
        raise PerformanceWebAPIRequestError
    return parsed.path


def _operation_id_from_path(path: str) -> str | None:
    prefix = _RUNS_PATH + "/"
    if not path.startswith(prefix):
        return None
    operation_id = path.removeprefix(prefix)
    if not operation_id or "/" in operation_id or operation_id == "latest":
        return None
    return operation_id


def _require_no_payload(payload: Mapping[str, Any] | None) -> None:
    if payload is not None:
        raise PerformanceWebAPIRequestError


def _require_object_payload(
    payload: Mapping[str, Any] | None,
) -> Mapping[str, Any]:
    if type(payload) is not dict:
        raise PerformanceWebAPIRequestError
    return payload


def _require_only_fields(
    payload: Mapping[str, Any],
    allowed: set[str],
) -> None:
    if set(payload) != allowed:
        raise PerformanceWebAPIRequestError


def _readiness_payload(
    snapshot: PerformanceReadinessSnapshot,
) -> dict[str, Any]:
    return {
        "poc_id": snapshot.poc_id,
        "contract_hash": snapshot.contract_hash,
        "workload_id": snapshot.workload_id,
        "status": snapshot.status.value,
        "reason_code": snapshot.reason_code,
    }


def _operation_payload(
    snapshot: PerformanceOperationSnapshot,
) -> dict[str, Any]:
    return {
        "poc_id": snapshot.poc_id,
        "contract_hash": snapshot.contract_hash,
        "workload_id": snapshot.workload_id,
        "operation_id": snapshot.operation_id,
        "status": snapshot.status.value,
        "reason_code": snapshot.reason_code,
        "evidence_pack_url": snapshot.evidence_pack_url,
        "is_terminal": snapshot.is_terminal,
    }


def _start_payload(snapshot: PerformanceStartSnapshot) -> dict[str, Any]:
    return {
        "operation": _operation_payload(snapshot.operation),
        "replayed": snapshot.replayed,
    }


def _ok(payload: dict[str, Any]) -> PerformanceWebAPIResponse:
    return PerformanceWebAPIResponse(HTTPStatus.OK, payload)


def _error(status: HTTPStatus, message: str) -> PerformanceWebAPIResponse:
    return PerformanceWebAPIResponse(status, {"error": message})


__all__ = [
    "PerformanceWebAPIRequestError",
    "PerformanceWebAPIResponse",
    "handle_performance_web_api_request",
    "is_performance_web_api_target",
]
