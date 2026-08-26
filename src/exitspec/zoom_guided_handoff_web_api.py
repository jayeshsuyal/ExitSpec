"""Pure HTTP projection for the local source-aware Zoom handoff."""

from __future__ import annotations

from dataclasses import dataclass
from http import HTTPStatus
import re
from typing import Any, Mapping
from urllib.parse import urlparse

from .poc_creation import POC_ID_PATTERN
from .zoom_guided_handoff import (
    ZoomGuidedHandoffError,
    ZoomGuidedHandoffService,
)


_POC_ID = re.compile(POC_ID_PATTERN)


@dataclass(frozen=True, slots=True)
class ZoomGuidedHandoffWebAPIResponse:
    status: HTTPStatus
    payload: dict[str, Any]


class ZoomGuidedHandoffWebAPIRequestError(ValueError):
    """A request targeted the Zoom handoff namespace but was malformed."""


def is_zoom_guided_handoff_web_api_target(target: str) -> bool:
    if type(target) is not str:
        return False
    parts = urlparse(target).path.strip("/").split("/")
    return (
        len(parts) == 4
        and parts[:2] == ["api", "pocs"]
        and parts[3] in {"zoom-handoff", "zoom-handoff-disclosure"}
    )


def zoom_guided_handoff_web_api_poc_id(target: str) -> str | None:
    if type(target) is not str:
        return None
    parsed = urlparse(target)
    if (
        parsed.scheme
        or parsed.netloc
        or parsed.params
        or parsed.query
        or parsed.fragment
        or parsed.path != target
    ):
        return None
    parts = parsed.path.strip("/").split("/")
    if (
        len(parts) != 4
        or parts[:2] != ["api", "pocs"]
        or _POC_ID.fullmatch(parts[2]) is None
        or parts[3] != "zoom-handoff"
    ):
        return None
    return parts[2]


def handle_zoom_guided_handoff_web_api_request(
    *,
    method: str,
    target: str,
    payload: Mapping[str, Any] | None,
    runtime: ZoomGuidedHandoffService,
) -> ZoomGuidedHandoffWebAPIResponse | None:
    if type(runtime) is not ZoomGuidedHandoffService:
        raise TypeError("runtime must be a ZoomGuidedHandoffService.")
    if not is_zoom_guided_handoff_web_api_target(target):
        return None
    try:
        path = _require_exact_local_target(target)
        parts = path[1:].split("/")
        poc_id = parts[2]
        route = parts[3]
        if method == "GET":
            if payload is not None:
                raise ZoomGuidedHandoffWebAPIRequestError
            if route == "zoom-handoff-disclosure":
                result = runtime.disclosure_for(poc_id)
                return ZoomGuidedHandoffWebAPIResponse(
                    HTTPStatus.OK,
                    result.model_dump(mode="json"),
                )
            result = runtime.current(poc_id)
            return ZoomGuidedHandoffWebAPIResponse(
                HTTPStatus.OK,
                result.model_dump(mode="json"),
            )
        if method != "POST" or route != "zoom-handoff":
            return _error(
                HTTPStatus.METHOD_NOT_ALLOWED,
                "Zoom handoff method is not allowed.",
            )
        body = _require_object_payload(payload)
        action = body.get("action")
        if action == "start":
            _require_only_fields(
                body,
                {"action", "consent_acknowledged", "idempotency_key"},
            )
            result = runtime.start(
                poc_id=poc_id,
                consent_acknowledged=body["consent_acknowledged"],
                idempotency_key=body["idempotency_key"],
            )
        elif action == "stop":
            _require_only_fields(body, {"action", "idempotency_key"})
            result = runtime.stop(
                poc_id=poc_id,
                idempotency_key=body["idempotency_key"],
            )
        elif action == "process":
            _require_only_fields(body, {"action", "idempotency_key"})
            result = runtime.process(
                poc_id=poc_id,
                idempotency_key=body["idempotency_key"],
            )
        else:
            raise ZoomGuidedHandoffWebAPIRequestError
        status = HTTPStatus.OK if result.idempotent_replay else HTTPStatus.CREATED
        return ZoomGuidedHandoffWebAPIResponse(
            status,
            {
                "idempotent_replay": result.idempotent_replay,
                "handoff": result.snapshot.model_dump(mode="json"),
            },
        )
    except ZoomGuidedHandoffWebAPIRequestError:
        return _error(HTTPStatus.BAD_REQUEST, "Zoom handoff request is invalid.")
    except ZoomGuidedHandoffError as error:
        statuses = {
            "ZOOM_GUIDED_HANDOFF_POC_UNAVAILABLE": HTTPStatus.NOT_FOUND,
            "ZOOM_GUIDED_HANDOFF_WRONG_SOURCE": HTTPStatus.CONFLICT,
            "ZOOM_GUIDED_HANDOFF_CONSENT_REQUIRED": HTTPStatus.CONFLICT,
            "ZOOM_GUIDED_HANDOFF_NOT_STARTED": HTTPStatus.CONFLICT,
            "ZOOM_SESSION_INVALID_REQUEST": HTTPStatus.BAD_REQUEST,
            "ZOOM_SESSION_INVALID_TRANSITION": HTTPStatus.CONFLICT,
            "ZOOM_SESSION_IDEMPOTENCY_CONFLICT": HTTPStatus.CONFLICT,
            "ZOOM_SESSION_PROCESSING_FAILED": HTTPStatus.SERVICE_UNAVAILABLE,
        }
        return ZoomGuidedHandoffWebAPIResponse(
            statuses.get(error.code, HTTPStatus.SERVICE_UNAVAILABLE),
            {
                "code": error.code,
                "error": str(error),
                "next_action": error.next_action,
            },
        )
    except (KeyError, TypeError, ValueError):
        return _error(HTTPStatus.BAD_REQUEST, "Zoom handoff request is invalid.")


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
        raise ZoomGuidedHandoffWebAPIRequestError
    return parsed.path


def _require_object_payload(
    payload: Mapping[str, Any] | None,
) -> Mapping[str, Any]:
    if type(payload) is not dict:
        raise ZoomGuidedHandoffWebAPIRequestError
    return payload


def _require_only_fields(payload: Mapping[str, Any], allowed: set[str]) -> None:
    if set(payload) != allowed:
        raise ZoomGuidedHandoffWebAPIRequestError


def _error(
    status: HTTPStatus,
    message: str,
) -> ZoomGuidedHandoffWebAPIResponse:
    return ZoomGuidedHandoffWebAPIResponse(status, {"error": message})


__all__ = [
    "ZoomGuidedHandoffWebAPIRequestError",
    "ZoomGuidedHandoffWebAPIResponse",
    "handle_zoom_guided_handoff_web_api_request",
    "is_zoom_guided_handoff_web_api_target",
    "zoom_guided_handoff_web_api_poc_id",
]
