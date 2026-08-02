"""Pure HTTP projection for POC-scoped source intake.

The request handler in :mod:`exitspec.web` owns transport concerns such as
body limits, UTF-8 JSON decoding, media type, and same-origin enforcement.
This module owns exact route matching, payload allowlists, safe error mapping,
and public source-receipt projections.
"""

from __future__ import annotations

from dataclasses import dataclass
from http import HTTPStatus
import re
from typing import Any, Mapping
from urllib.parse import urlparse

from .poc_creation import POC_ID_PATTERN
from .poc_source_intake import (
    POCSourceFixtureUnavailable,
    POCSourceIntakeCapacityExceeded,
    POCSourceIntakeError,
    POCSourceIntakeInvalid,
    POCSourceIntakeRevisionRequired,
    POCSourceReceipt,
    ProcessLocalPOCSourceIntake,
)
from .poc_sources import (
    DuplicatePOCSourceId,
    POCSourceCapacityExceeded,
    POCSourceDraftArchived,
    POCSourceDraftUnavailable,
    POCSourceIdempotencyConflict,
    POCSourceRevisionRequired,
    POCSourceStaleRevision,
)


_POC_ID_RE = re.compile(POC_ID_PATTERN)
_SOURCE_ROOT_PREFIX = "/api/pocs/"
_CAPTURE_ROUTES = {
    "email": ("fixture_case_id", "capture_email"),
    "email-text": ("email_text", "capture_email_text"),
    "meeting": ("transcript_text", "capture_meeting"),
    "document": ("document_text", "capture_document"),
    "contract": ("contract_json", "capture_contract"),
}


@dataclass(frozen=True, slots=True)
class POCSourceWebAPIResponse:
    """Transport-neutral status and JSON object."""

    status: HTTPStatus
    payload: dict[str, Any]


class POCSourceWebAPIRequestError(ValueError):
    """A request targeted source intake but violated its exact contract."""


def is_poc_source_web_api_target(target: str) -> bool:
    """Return whether a raw request target belongs to POC source intake."""

    if type(target) is not str:
        return False
    path = urlparse(target).path
    parts = path.strip("/").split("/")
    return (
        len(parts) >= 4
        and parts[:2] == ["api", "pocs"]
        and parts[3] == "sources"
    )


def handle_poc_source_web_api_request(
    *,
    method: str,
    target: str,
    payload: Mapping[str, Any] | None,
    runtime: ProcessLocalPOCSourceIntake,
) -> POCSourceWebAPIResponse | None:
    """Handle one exact source-intake API request.

    ``None`` means the target is unrelated. A malformed request below the
    source-intake namespace always receives a safe response and never falls
    through to another route.
    """

    if type(runtime) is not ProcessLocalPOCSourceIntake:
        raise TypeError("runtime must be a ProcessLocalPOCSourceIntake.")
    if not is_poc_source_web_api_target(target):
        return None

    try:
        path = _require_exact_local_target(target)
        poc_id, route = _parse_source_path(path)
        return _dispatch(
            method=method,
            poc_id=poc_id,
            route=route,
            payload=payload,
            runtime=runtime,
        )
    except POCSourceWebAPIRequestError:
        return _error(HTTPStatus.BAD_REQUEST, "Source intake request is invalid.")
    except POCSourceFixtureUnavailable:
        return _error(
            HTTPStatus.NOT_FOUND,
            "The approved synthetic source was not found.",
        )
    except POCSourceDraftUnavailable:
        return _error(
            HTTPStatus.NOT_FOUND,
            "The draft POC was not found in this local process.",
        )
    except POCSourceIntakeInvalid:
        status = (
            HTTPStatus.NOT_FOUND
            if method == "GET"
            else HTTPStatus.UNPROCESSABLE_ENTITY
        )
        return _error(status, "The source input was not accepted.")
    except (
        POCSourceDraftArchived,
        POCSourceIdempotencyConflict,
        POCSourceIntakeRevisionRequired,
        POCSourceRevisionRequired,
        POCSourceStaleRevision,
    ):
        return _error(
            HTTPStatus.CONFLICT,
            "The source request conflicts with the current draft state.",
        )
    except (
        POCSourceIntakeCapacityExceeded,
        POCSourceCapacityExceeded,
        DuplicatePOCSourceId,
    ):
        return _error(
            HTTPStatus.SERVICE_UNAVAILABLE,
            "Source intake is temporarily unavailable.",
        )
    except POCSourceIntakeError:
        return _error(
            HTTPStatus.SERVICE_UNAVAILABLE,
            "Source intake is temporarily unavailable.",
        )
    except (TypeError, ValueError):
        return _error(HTTPStatus.BAD_REQUEST, "Source intake request is invalid.")


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
        raise POCSourceWebAPIRequestError
    return parsed.path


def _parse_source_path(path: str) -> tuple[str, str | None]:
    parts = path.strip("/").split("/")
    if (
        len(parts) not in {4, 5}
        or parts[:2] != ["api", "pocs"]
        or parts[3] != "sources"
        or _POC_ID_RE.fullmatch(parts[2]) is None
    ):
        raise POCSourceWebAPIRequestError
    return parts[2], parts[4] if len(parts) == 5 else None


def _dispatch(
    *,
    method: str,
    poc_id: str,
    route: str | None,
    payload: Mapping[str, Any] | None,
    runtime: ProcessLocalPOCSourceIntake,
) -> POCSourceWebAPIResponse:
    if type(method) is not str:
        raise POCSourceWebAPIRequestError

    if method == "GET":
        _require_no_payload(payload)
        if route is not None:
            return _error(HTTPStatus.NOT_FOUND, "Source intake route was not found.")
        receipts = runtime.list_receipts(poc_id)
        return _ok(
            {
                "poc_id": poc_id,
                "sources": [_receipt_payload(receipt) for receipt in receipts],
            }
        )

    if method == "POST":
        body = _require_object_payload(payload)
        route_contract = _CAPTURE_ROUTES.get(route or "")
        if route_contract is None:
            return _error(HTTPStatus.NOT_FOUND, "Source intake route was not found.")
        source_field, method_name = route_contract
        _require_only_fields(body, {source_field, "idempotency_key"})
        capture = getattr(runtime, method_name)
        receipt = capture(
            poc_id=poc_id,
            **{
                source_field: body[source_field],
                "idempotency_key": body["idempotency_key"],
            },
        )
        status = (
            HTTPStatus.OK
            if receipt.idempotent_replay
            else HTTPStatus.CREATED
        )
        return POCSourceWebAPIResponse(status, _receipt_payload(receipt))

    return _error(
        HTTPStatus.METHOD_NOT_ALLOWED,
        "Source intake method is not allowed.",
    )


def _require_no_payload(payload: Mapping[str, Any] | None) -> None:
    if payload is not None:
        raise POCSourceWebAPIRequestError


def _require_object_payload(
    payload: Mapping[str, Any] | None,
) -> Mapping[str, Any]:
    if type(payload) is not dict:
        raise POCSourceWebAPIRequestError
    return payload


def _require_only_fields(
    payload: Mapping[str, Any],
    allowed: set[str],
) -> None:
    if set(payload) != allowed:
        raise POCSourceWebAPIRequestError


def _receipt_payload(receipt: POCSourceReceipt) -> dict[str, Any]:
    if type(receipt) is not POCSourceReceipt:
        raise POCSourceIntakeError(
            "The source receipt could not be projected safely."
        )
    return receipt.model_dump(mode="json")


def _ok(payload: dict[str, Any]) -> POCSourceWebAPIResponse:
    return POCSourceWebAPIResponse(HTTPStatus.OK, payload)


def _error(status: HTTPStatus, message: str) -> POCSourceWebAPIResponse:
    return POCSourceWebAPIResponse(status, {"error": message})


__all__ = [
    "POCSourceWebAPIRequestError",
    "POCSourceWebAPIResponse",
    "handle_poc_source_web_api_request",
    "is_poc_source_web_api_target",
]
