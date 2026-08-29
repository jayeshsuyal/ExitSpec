"""Pure HTTP projection for the explicit source-scoped A3 authoring action."""

from __future__ import annotations

from dataclasses import dataclass
from http import HTTPStatus
import re
from typing import Any, Mapping
from urllib.parse import urlparse

from .assisted_authoring import (
    AssistedAuthoringError,
    ProcessLocalAssistedAuthoringService,
)
from .poc_creation import POC_ID_PATTERN


_POC_ID_RE = re.compile(POC_ID_PATTERN)
_SOURCE_RECEIPT_RE = re.compile(r"^srcpt_[a-z0-9][a-z0-9_-]{7,95}$")
_AUTHORING_ROUTE_RE = re.compile(
    r"^/api/pocs/(poc_[a-z0-9][a-z0-9_-]{2,63})/sources/"
    r"(srcpt_[a-z0-9][a-z0-9_-]{7,95})/assisted-authoring$"
)
_RETAINED_ROUTE_RE = re.compile(
    r"^/api/pocs/(poc_[a-z0-9][a-z0-9_-]{2,63})/retained-proposals$"
)
_COLLECTION_ROUTE_RE = re.compile(
    r"^/api/pocs/(poc_[a-z0-9][a-z0-9_-]{2,63})/assisted-authoring$"
)


@dataclass(frozen=True, slots=True)
class POCAssistedAuthoringWebAPIResponse:
    status: HTTPStatus
    payload: dict[str, Any]


class POCAssistedAuthoringWebAPIRequestError(ValueError):
    """A request targeted A3 but violated its exact transport contract."""


def is_poc_assisted_authoring_web_api_target(target: str) -> bool:
    if type(target) is not str:
        return False
    path = urlparse(target).path
    return bool(
        _AUTHORING_ROUTE_RE.fullmatch(path)
        or _RETAINED_ROUTE_RE.fullmatch(path)
        or _COLLECTION_ROUTE_RE.fullmatch(path)
    )


def handle_poc_assisted_authoring_web_api_request(
    *,
    method: str,
    target: str,
    payload: Mapping[str, Any] | None,
    runtime: ProcessLocalAssistedAuthoringService,
    review_runtime: Any | None = None,
) -> POCAssistedAuthoringWebAPIResponse | None:
    """Handle one exact A3 authoring or A4-handoff projection."""

    if type(runtime) is not ProcessLocalAssistedAuthoringService:
        raise TypeError("runtime must be a ProcessLocalAssistedAuthoringService.")
    if not is_poc_assisted_authoring_web_api_target(target):
        return None
    try:
        path = _require_exact_target(target)
        authoring_match = _AUTHORING_ROUTE_RE.fullmatch(path)
        retained_match = _RETAINED_ROUTE_RE.fullmatch(path)
        collection_match = _COLLECTION_ROUTE_RE.fullmatch(path)
        if authoring_match is not None:
            return _authoring_dispatch(
                method=method,
                poc_id=authoring_match.group(1),
                source_receipt_id=authoring_match.group(2),
                payload=payload,
                runtime=runtime,
            )
        if retained_match is not None:
            return _retained_dispatch(
                method=method,
                poc_id=retained_match.group(1),
                payload=payload,
                runtime=runtime,
                review_runtime=review_runtime,
            )
        if collection_match is not None:
            return _collection_dispatch(
                method=method,
                poc_id=collection_match.group(1),
                payload=payload,
                runtime=runtime,
            )
        raise POCAssistedAuthoringWebAPIRequestError
    except POCAssistedAuthoringWebAPIRequestError:
        return _error(HTTPStatus.BAD_REQUEST, "Assisted authoring request is invalid.")
    except AssistedAuthoringError as error:
        if error.code == "source_unavailable":
            return _error(HTTPStatus.NOT_FOUND, "The source was not found.")
        if error.code in {
            "idempotency_conflict",
            "attempt_conflict",
            "stale_proposal",
            "source_stale",
        }:
            return _error(
                HTTPStatus.CONFLICT,
                "Assisted authoring conflicts with the current source state.",
            )
        if error.code in {"service_unavailable", "capacity_exceeded"}:
            return _error(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "Assisted authoring is temporarily unavailable.",
            )
        return _error(
            HTTPStatus.UNPROCESSABLE_ENTITY,
            "Assisted authoring output was not accepted.",
        )
    except (TypeError, ValueError):
        return _error(HTTPStatus.BAD_REQUEST, "Assisted authoring request is invalid.")


def _authoring_dispatch(
    *,
    method: str,
    poc_id: str,
    source_receipt_id: str,
    payload: Mapping[str, Any] | None,
    runtime: ProcessLocalAssistedAuthoringService,
) -> POCAssistedAuthoringWebAPIResponse:
    if method != "POST":
        return _error(
            HTTPStatus.METHOD_NOT_ALLOWED,
            "Assisted authoring method is not allowed.",
        )
    body = _require_object_payload(payload)
    _require_only_fields(body, {"idempotency_key"})
    idempotency_key = body.get("idempotency_key")
    if type(idempotency_key) is not str or not idempotency_key.strip():
        raise POCAssistedAuthoringWebAPIRequestError
    result = runtime.create_assisted_draft(
        poc_id=poc_id,
        source_receipt_id=source_receipt_id,
        idempotency_key=idempotency_key,
    )
    return POCAssistedAuthoringWebAPIResponse(
        HTTPStatus.OK if result.receipt.idempotent_replay else HTTPStatus.CREATED,
        {
            "authoring_receipt": result.receipt.model_dump(mode="json"),
            "proposals": [
                proposal.model_dump(mode="json") for proposal in result.proposals
            ],
        },
    )


def _retained_dispatch(
    *,
    method: str,
    poc_id: str,
    payload: Mapping[str, Any] | None,
    runtime: ProcessLocalAssistedAuthoringService,
    review_runtime: Any | None,
) -> POCAssistedAuthoringWebAPIResponse:
    if method != "GET":
        return _error(
            HTTPStatus.METHOD_NOT_ALLOWED,
            "Retained proposal method is not allowed.",
        )
    if payload is not None or review_runtime is None:
        raise POCAssistedAuthoringWebAPIRequestError
    retained = runtime.retained_projection(poc_id, review_runtime)
    return POCAssistedAuthoringWebAPIResponse(
        HTTPStatus.OK,
        {
            "poc_id": poc_id,
            "retained_proposals": [
                proposal.model_dump(mode="json") for proposal in retained
            ],
            "retained_count": len(retained),
        },
    )


def _collection_dispatch(
    *,
    method: str,
    poc_id: str,
    payload: Mapping[str, Any] | None,
    runtime: ProcessLocalAssistedAuthoringService,
) -> POCAssistedAuthoringWebAPIResponse:
    if method != "GET" or payload is not None:
        return _error(
            HTTPStatus.METHOD_NOT_ALLOWED if method != "GET" else HTTPStatus.BAD_REQUEST,
            "Assisted authoring method is not allowed."
            if method != "GET"
            else "Assisted authoring request is invalid.",
        )
    return POCAssistedAuthoringWebAPIResponse(
        HTTPStatus.OK,
        {
            "poc_id": poc_id,
            "receipts": [
                receipt.model_dump(mode="json")
                for receipt in runtime.list_receipts(poc_id)
            ],
        },
    )


def _require_exact_target(target: str) -> str:
    parsed = urlparse(target)
    if (
        parsed.scheme
        or parsed.netloc
        or parsed.params
        or parsed.query
        or parsed.fragment
        or parsed.path != target
    ):
        raise POCAssistedAuthoringWebAPIRequestError
    return parsed.path


def _require_object_payload(payload: Mapping[str, Any] | None) -> Mapping[str, Any]:
    if type(payload) is not dict:
        raise POCAssistedAuthoringWebAPIRequestError
    return payload


def _require_only_fields(payload: Mapping[str, Any], allowed: set[str]) -> None:
    if set(payload) != allowed:
        raise POCAssistedAuthoringWebAPIRequestError


def _error(status: HTTPStatus, message: str) -> POCAssistedAuthoringWebAPIResponse:
    return POCAssistedAuthoringWebAPIResponse(status, {"error": message})


__all__ = [
    "POCAssistedAuthoringWebAPIRequestError",
    "POCAssistedAuthoringWebAPIResponse",
    "handle_poc_assisted_authoring_web_api_request",
    "is_poc_assisted_authoring_web_api_target",
]
