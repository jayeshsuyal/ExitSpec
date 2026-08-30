"""Small HTTP façade for the generic A6 evidence spine."""

from __future__ import annotations

from dataclasses import dataclass
from http import HTTPStatus
import re
from typing import Any, Mapping
from urllib.parse import urlparse

from pydantic import ValidationError

from .generic_evidence_pack import GenericEvidencePackError
from .poc_evidence_orchestration import (
    ExecutableOrchestrationConflict,
    ExecutableOrchestrationInvalid,
    ProcessLocalEvidenceOrchestrationService,
)


_POC_ID = r"poc_[a-z0-9][a-z0-9_-]{2,63}"
_ATTEMPT_ID = r"eatm_[a-f0-9]{32}"
_ROUTE = re.compile(
    rf"^/api/pocs/({_POC_ID})/evidence(?:/({_ATTEMPT_ID})(?:/(pack|handoff|stop))?)?$"
)
_START_FIELDS = {"acknowledgement", "idempotency_key"}
_START_IMPORT_FIELDS = _START_FIELDS | {"catalog_evidence_ref"}
_DECISION_FIELDS = {"decided_by", "rationale", "idempotency_key"}


@dataclass(frozen=True, slots=True)
class EvidenceWebAPIResponse:
    status: HTTPStatus
    payload: dict[str, Any]


class EvidenceWebAPIRequestError(ValueError):
    """A request violated the exact generic evidence transport contract."""


def is_poc_evidence_web_api_target(target: str) -> bool:
    if type(target) is not str:
        return False
    return bool(_ROUTE.fullmatch(urlparse(target).path))


def handle_poc_evidence_web_api_request(
    *,
    method: str,
    target: str,
    payload: Mapping[str, Any] | None,
    runtime: ProcessLocalEvidenceOrchestrationService | None,
) -> EvidenceWebAPIResponse | None:
    if not is_poc_evidence_web_api_target(target):
        return None
    if runtime is None:
        return _error(HTTPStatus.SERVICE_UNAVAILABLE, "Evidence service is unavailable.")
    if type(runtime) is not ProcessLocalEvidenceOrchestrationService:
        raise TypeError("runtime must be a ProcessLocalEvidenceOrchestrationService.")
    try:
        path = _exact_target(target)
        match = _ROUTE.fullmatch(path)
        if match is None:  # pragma: no cover - guarded by target predicate
            raise EvidenceWebAPIRequestError
        poc_id, attempt_id, action = match.groups()
        if method == "GET":
            if payload is not None:
                raise EvidenceWebAPIRequestError
            if attempt_id is None:
                return _ok(runtime.snapshot_payload(poc_id))
            if action != "pack":
                raise EvidenceWebAPIRequestError
            attempt = runtime.attempt(attempt_id)
            if attempt.poc_id != poc_id:
                raise KeyError("Evidence attempt was not found.")
            runtime.verify_evidence_pack(attempt_id)
            return _ok({"poc_id": poc_id, "attempt": attempt.model_dump(mode="json")})

        if method != "POST":
            return _error(HTTPStatus.METHOD_NOT_ALLOWED, "Evidence method is not allowed.")
        body = _object(payload)
        if attempt_id is None:
            if action is not None:
                raise EvidenceWebAPIRequestError
            if set(body) == _START_IMPORT_FIELDS:
                catalog_ref = body["catalog_evidence_ref"]
            else:
                _only(body, _START_FIELDS)
                catalog_ref = None
            _require_boolean(body, "acknowledgement")
            result = runtime.start(
                poc_id,
                acknowledgement=body["acknowledgement"],
                idempotency_key=_required_text(body, "idempotency_key"),
                catalog_evidence_ref=catalog_ref,
            )
            return _write_start(result, poc_id)
        if action not in {"handoff", "stop"}:
            raise EvidenceWebAPIRequestError
        _only(body, _DECISION_FIELDS)
        attempt = runtime.attempt(attempt_id)
        if attempt.poc_id != poc_id:
            raise KeyError("Evidence attempt was not found.")
        if action == "handoff":
            result = runtime.handoff(
                attempt_id,
                decided_by=_required_text(body, "decided_by"),
                rationale=_required_text(body, "rationale"),
                idempotency_key=_required_text(body, "idempotency_key"),
            )
        else:
            result = runtime.stop(
                attempt_id,
                decided_by=_required_text(body, "decided_by"),
                rationale=_required_text(body, "rationale"),
                idempotency_key=_required_text(body, "idempotency_key"),
            )
        return _ok({"poc_id": poc_id, "closure": result.closure.model_dump(mode="json")})
    except EvidenceWebAPIRequestError:
        return _error(HTTPStatus.BAD_REQUEST, "Evidence request is invalid.")
    except KeyError:
        return _error(HTTPStatus.NOT_FOUND, "Evidence attempt was not found.")
    except (ExecutableOrchestrationConflict, GenericEvidencePackError):
        return _error(HTTPStatus.CONFLICT, "Evidence conflicts with current frozen state.")
    except (ValidationError, ExecutableOrchestrationInvalid, TypeError, ValueError):
        return _error(HTTPStatus.BAD_REQUEST, "Evidence request is invalid.")
    except Exception:
        return _error(HTTPStatus.SERVICE_UNAVAILABLE, "Evidence service is unavailable.")


def _write_start(result, poc_id: str) -> EvidenceWebAPIResponse:
    return EvidenceWebAPIResponse(
        HTTPStatus.OK if result.replayed else HTTPStatus.CREATED,
        {
            "poc_id": poc_id,
            "replayed": result.replayed,
            "attempt": result.attempt.model_dump(mode="json"),
        },
    )


def _exact_target(target: str) -> str:
    parsed = urlparse(target)
    if (
        parsed.scheme
        or parsed.netloc
        or parsed.params
        or parsed.query
        or parsed.fragment
        or parsed.path != target
    ):
        raise EvidenceWebAPIRequestError
    return parsed.path


def _object(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    if type(payload) is not dict:
        raise EvidenceWebAPIRequestError
    return payload


def _only(payload: Mapping[str, Any], fields: set[str]) -> None:
    if set(payload) != fields or any(type(key) is not str for key in payload):
        raise EvidenceWebAPIRequestError


def _required_text(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if type(value) is not str or not value.strip():
        raise EvidenceWebAPIRequestError
    return value.strip()


def _require_boolean(payload: Mapping[str, Any], key: str) -> None:
    if type(payload.get(key)) is not bool:
        raise EvidenceWebAPIRequestError


def _ok(payload: dict[str, Any]) -> EvidenceWebAPIResponse:
    return EvidenceWebAPIResponse(HTTPStatus.OK, payload)


def _error(status: HTTPStatus, message: str) -> EvidenceWebAPIResponse:
    return EvidenceWebAPIResponse(status, {"error": message})


__all__ = [
    "EvidenceWebAPIRequestError",
    "EvidenceWebAPIResponse",
    "handle_poc_evidence_web_api_request",
    "is_poc_evidence_web_api_target",
]
