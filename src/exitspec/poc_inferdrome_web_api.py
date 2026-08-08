"""Pure HTTP projection for local, pathless Inferdrome evidence imports."""

from __future__ import annotations

from dataclasses import dataclass
from http import HTTPStatus
import re
from typing import Any, Mapping
from urllib.parse import urlparse

from .inferdrome_catalog import InferdromeCatalogError
from .poc_creation import POC_ID_PATTERN
from .poc_inferdrome_import import (
    POCInferdromeImportCapacityExceeded,
    POCInferdromeImportConflict,
    POCInferdromeImportError,
    POCInferdromeImportInvalid,
    POCInferdromeImportNotFound,
    POCInferdromeImportSnapshot,
    ProcessLocalPOCInferdromeImportService,
)

_POC_ID_RE = re.compile(POC_ID_PATTERN)
_OPERATION_ID_RE = re.compile(r"^pimp_[a-f0-9]{32}$")
_START_FIELDS = {
    "run_id",
    "bundle_digest",
    "import_acknowledged",
    "idempotency_key",
}


@dataclass(frozen=True, slots=True)
class POCInferdromeWebAPIResponse:
    status: HTTPStatus
    payload: dict[str, Any]


class POCInferdromeWebAPIRequestError(ValueError):
    pass


def is_poc_inferdrome_web_api_target(target: str) -> bool:
    if type(target) is not str:
        return False
    return bool(
        re.match(
            r"^/api/+pocs/[^/]+/inferdrome(?:/|$)",
            urlparse(target).path,
        )
    )


def handle_poc_inferdrome_web_api_request(
    *,
    method: str,
    target: str,
    payload: Mapping[str, Any] | None,
    runtime: ProcessLocalPOCInferdromeImportService,
) -> POCInferdromeWebAPIResponse | None:
    if type(runtime) is not ProcessLocalPOCInferdromeImportService:
        raise TypeError("Inferdrome import runtime is invalid.")
    if not is_poc_inferdrome_web_api_target(target):
        return None
    try:
        path = _exact_path(target)
        poc_id, resource, operation_id = _parse_path(path)
        if method == "GET":
            if payload is not None:
                raise POCInferdromeWebAPIRequestError
            if resource == "runs":
                if operation_id is not None:
                    raise POCInferdromeWebAPIRequestError
                runtime.snapshot(poc_id)
                catalog = runtime.catalog.refresh()
                return _ok(
                    {
                        "configured": catalog.configured,
                        "runs": [
                            {
                                "run_id": entry.run_id,
                                "bundle_digest": entry.bundle_digest,
                            }
                            for entry in catalog.entries
                        ],
                        "rejected_count": len(catalog.rejected),
                    }
                )
            if operation_id is None:
                return _error(
                    HTTPStatus.METHOD_NOT_ALLOWED,
                    "Inferdrome import method is not allowed.",
                )
            snapshot = (
                runtime.snapshot(poc_id)
                if operation_id == "latest"
                else runtime.operation_snapshot(poc_id, str(operation_id))
            )
            return _ok(_snapshot_payload(snapshot))
        if method == "POST":
            if resource != "imports" or operation_id is not None:
                return _error(
                    HTTPStatus.METHOD_NOT_ALLOWED,
                    "Inferdrome import method is not allowed.",
                )
            body = _object(payload)
            if set(body) != _START_FIELDS:
                raise POCInferdromeWebAPIRequestError
            started = runtime.start(
                poc_id,
                import_acknowledged=body["import_acknowledged"],
                run_id=body["run_id"],
                bundle_digest=body["bundle_digest"],
                idempotency_key=body["idempotency_key"],
            )
            return POCInferdromeWebAPIResponse(
                HTTPStatus.OK if started.replayed else HTTPStatus.ACCEPTED,
                {
                    "operation": _snapshot_payload(started.operation),
                    "replayed": started.replayed,
                },
            )
        return _error(
            HTTPStatus.METHOD_NOT_ALLOWED,
            "Inferdrome import method is not allowed.",
        )
    except (POCInferdromeWebAPIRequestError, POCInferdromeImportInvalid):
        return _error(
            HTTPStatus.BAD_REQUEST,
            "Inferdrome import request is invalid.",
        )
    except POCInferdromeImportNotFound:
        return _error(
            HTTPStatus.NOT_FOUND,
            "Inferdrome evidence was not found.",
        )
    except POCInferdromeImportConflict:
        return _error(
            HTTPStatus.CONFLICT,
            "Inferdrome import conflicts with current POC state.",
        )
    except POCInferdromeImportCapacityExceeded:
        return _error(
            HTTPStatus.TOO_MANY_REQUESTS,
            "Inferdrome import capacity is exhausted.",
        )
    except (InferdromeCatalogError, POCInferdromeImportError):
        return _error(
            HTTPStatus.SERVICE_UNAVAILABLE,
            "Inferdrome import is unavailable.",
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
        raise POCInferdromeWebAPIRequestError
    return parsed.path


def _parse_path(path: str) -> tuple[str, str, str | None]:
    parts = path.strip("/").split("/")
    if (
        len(parts) not in {5, 6}
        or parts[:2] != ["api", "pocs"]
        or _POC_ID_RE.fullmatch(parts[2]) is None
        or parts[3] != "inferdrome"
        or parts[4] not in {"runs", "imports"}
        or path.startswith("//")
        or "//" in path
    ):
        raise POCInferdromeWebAPIRequestError
    resource = parts[4]
    operation_id = None if len(parts) == 5 else parts[5]
    if resource == "runs" and operation_id is not None:
        raise POCInferdromeWebAPIRequestError
    if operation_id is not None and (
        operation_id != "latest"
        and _OPERATION_ID_RE.fullmatch(operation_id) is None
    ):
        raise POCInferdromeWebAPIRequestError
    return parts[2], resource, operation_id


def _object(payload: Mapping[str, Any] | None) -> Mapping[str, Any]:
    if type(payload) is not dict or any(type(key) is not str for key in payload):
        raise POCInferdromeWebAPIRequestError
    return payload


def _snapshot_payload(
    snapshot: POCInferdromeImportSnapshot,
) -> dict[str, Any]:
    if type(snapshot) is not POCInferdromeImportSnapshot:
        raise POCInferdromeImportError
    return {
        "poc_id": snapshot.poc_id,
        "contract_id": snapshot.contract_id,
        "contract_version": snapshot.contract_version,
        "contract_hash": snapshot.contract_hash,
        "workload_id": snapshot.workload_id,
        "target_provider": snapshot.target_provider,
        "endpoint_class": snapshot.endpoint_class,
        "endpoint": snapshot.endpoint,
        "model": snapshot.model,
        "adapter": snapshot.adapter,
        "adapter_version": snapshot.adapter_version,
        "measured_requests": snapshot.measured_requests,
        "concurrency": snapshot.concurrency,
        "warmup_requests": snapshot.warmup_requests,
        "operation_id": snapshot.operation_id,
        "status": snapshot.status.value,
        "rejection_code": snapshot.rejection_code,
        "verdict": None if snapshot.verdict is None else snapshot.verdict.value,
        "attempted_count": snapshot.attempted_count,
        "successful_count": snapshot.successful_count,
        "error_count": snapshot.error_count,
        "p95_ttft_ms": snapshot.p95_ttft_ms,
        "error_rate_percent": snapshot.error_rate_percent,
        "selected_run_id": snapshot.selected_run_id,
        "producer_run_id": snapshot.producer_run_id,
        "bundle_digest": snapshot.bundle_digest,
        "receipt_id": snapshot.receipt_id,
        "applicability_codes": list(snapshot.applicability_codes),
        "evidence_pack_url": snapshot.evidence_pack_url,
        "completed_at": (
            None
            if snapshot.completed_at is None
            else snapshot.completed_at.isoformat()
        ),
        "is_terminal": snapshot.is_terminal,
    }


def _ok(payload: dict[str, Any]) -> POCInferdromeWebAPIResponse:
    return POCInferdromeWebAPIResponse(HTTPStatus.OK, payload)


def _error(
    status: HTTPStatus,
    message: str,
) -> POCInferdromeWebAPIResponse:
    return POCInferdromeWebAPIResponse(status, {"error": message})


__all__ = [
    "POCInferdromeWebAPIRequestError",
    "POCInferdromeWebAPIResponse",
    "handle_poc_inferdrome_web_api_request",
    "is_poc_inferdrome_web_api_target",
]
