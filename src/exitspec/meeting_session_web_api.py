"""Pure HTTP projection for guided provider-neutral meeting sessions."""

from __future__ import annotations

from dataclasses import dataclass
from http import HTTPStatus
import re
from typing import Any, Mapping
from urllib.parse import urlparse

from .meeting_session_runtime import (
    MeetingSessionError,
    MeetingSessionFailureCode,
    ProcessLocalMeetingSessionRuntime,
)
from .poc_creation import POC_ID_PATTERN


_POC_ID = re.compile(POC_ID_PATTERN)
_SESSION_ID = re.compile(r"^meetsess_[a-f0-9]{64}$")


@dataclass(frozen=True, slots=True)
class MeetingSessionWebAPIResponse:
    """Transport-neutral status and JSON object."""

    status: HTTPStatus
    payload: dict[str, Any]


class MeetingSessionWebAPIRequestError(ValueError):
    """A request targeted the meeting-session namespace but was malformed."""


def is_meeting_session_web_api_target(target: str) -> bool:
    """Return whether a raw request target belongs to meeting sessions."""

    if type(target) is not str:
        return False
    parts = urlparse(target).path.strip("/").split("/")
    return (
        len(parts) >= 4
        and parts[:2] == ["api", "pocs"]
        and parts[3] == "meeting-sessions"
    )


def meeting_session_web_api_poc_id(target: str) -> str | None:
    """Return the validated POC ID for an exact meeting-session route."""

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
    try:
        poc_id, _, _ = _parse_path(parsed.path)
    except MeetingSessionWebAPIRequestError:
        return None
    return poc_id


def handle_meeting_session_web_api_request(
    *,
    method: str,
    target: str,
    payload: Mapping[str, Any] | None,
    runtime: ProcessLocalMeetingSessionRuntime,
) -> MeetingSessionWebAPIResponse | None:
    """Handle one exact disclosure, session-read, or session mutation."""

    if type(runtime) is not ProcessLocalMeetingSessionRuntime:
        raise TypeError("runtime must be a ProcessLocalMeetingSessionRuntime.")
    if not is_meeting_session_web_api_target(target):
        return None

    try:
        path = _require_exact_local_target(target)
        poc_id, route, session_id = _parse_path(path)
        return _dispatch(
            method=method,
            poc_id=poc_id,
            route=route,
            session_id=session_id,
            payload=payload,
            runtime=runtime,
        )
    except MeetingSessionWebAPIRequestError:
        return _error(
            HTTPStatus.BAD_REQUEST,
            "Meeting session request is invalid.",
        )
    except MeetingSessionError as error:
        return _runtime_error(error)
    except (KeyError, TypeError, ValueError):
        return _error(
            HTTPStatus.BAD_REQUEST,
            "Meeting session request is invalid.",
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
        raise MeetingSessionWebAPIRequestError
    return parsed.path


def _parse_path(path: str) -> tuple[str, str, str | None]:
    if (
        not path.startswith("/")
        or path == "/"
        or path.endswith("/")
        or "//" in path
    ):
        raise MeetingSessionWebAPIRequestError
    parts = path[1:].split("/")
    if (
        len(parts) not in {4, 5, 6}
        or parts[:2] != ["api", "pocs"]
        or parts[3] != "meeting-sessions"
        or _POC_ID.fullmatch(parts[2]) is None
    ):
        raise MeetingSessionWebAPIRequestError
    if len(parts) == 4:
        return parts[2], "collection", None
    if len(parts) == 5 and parts[4] in {"current", "disclosure"}:
        return parts[2], parts[4], None
    if len(parts) == 5 and _SESSION_ID.fullmatch(parts[4]) is not None:
        return parts[2], "session", parts[4]
    if (
        len(parts) == 6
        and _SESSION_ID.fullmatch(parts[4]) is not None
        and parts[5] in {"consent", "draft", "start"}
    ):
        return parts[2], parts[5], parts[4]
    raise MeetingSessionWebAPIRequestError


def _dispatch(
    *,
    method: str,
    poc_id: str,
    route: str,
    session_id: str | None,
    payload: Mapping[str, Any] | None,
    runtime: ProcessLocalMeetingSessionRuntime,
) -> MeetingSessionWebAPIResponse:
    if type(method) is not str:
        raise MeetingSessionWebAPIRequestError

    if method == "GET":
        if payload is not None:
            raise MeetingSessionWebAPIRequestError
        if route == "disclosure":
            result = runtime.disclosure_for(poc_id)
        elif route == "current":
            result = runtime.current(poc_id=poc_id)
        elif route == "session" and session_id is not None:
            result = runtime.session(poc_id=poc_id, session_id=session_id)
        else:
            return _error(
                HTTPStatus.METHOD_NOT_ALLOWED,
                "Meeting session method is not allowed.",
            )
        return MeetingSessionWebAPIResponse(
            HTTPStatus.OK,
            result.model_dump(mode="json"),
        )

    if method != "POST":
        return _error(
            HTTPStatus.METHOD_NOT_ALLOWED,
            "Meeting session method is not allowed.",
        )

    body = _require_object_payload(payload)
    if route == "collection":
        _require_only_fields(body, {"idempotency_key"})
        result = runtime.create(
            poc_id=poc_id,
            idempotency_key=body["idempotency_key"],
        )
    elif route == "consent" and session_id is not None:
        _require_only_fields(
            body,
            {
                "all_participants_consented",
                "disclosure_id",
                "idempotency_key",
                "recording_notice_acknowledged",
                "synthetic_demo_acknowledged",
            },
        )
        result = runtime.record_consent(
            poc_id=poc_id,
            session_id=session_id,
            disclosure_id=body["disclosure_id"],
            recording_notice_acknowledged=(
                body["recording_notice_acknowledged"]
            ),
            all_participants_consented=body[
                "all_participants_consented"
            ],
            synthetic_demo_acknowledged=body[
                "synthetic_demo_acknowledged"
            ],
            idempotency_key=body["idempotency_key"],
        )
    elif route == "start" and session_id is not None:
        _require_only_fields(body, {"idempotency_key"})
        result = runtime.start(
            poc_id=poc_id,
            session_id=session_id,
            idempotency_key=body["idempotency_key"],
        )
    elif route == "draft" and session_id is not None:
        _require_only_fields(body, {"idempotency_key"})
        result = runtime.draft_now(
            poc_id=poc_id,
            session_id=session_id,
            idempotency_key=body["idempotency_key"],
        )
    else:
        return _error(
            HTTPStatus.METHOD_NOT_ALLOWED,
            "Meeting session method is not allowed.",
        )

    status = (
        HTTPStatus.OK
        if result.idempotent_replay
        else HTTPStatus.CREATED
    )
    return MeetingSessionWebAPIResponse(
        status,
        result.model_dump(mode="json"),
    )


def _require_object_payload(
    payload: Mapping[str, Any] | None,
) -> Mapping[str, Any]:
    if type(payload) is not dict:
        raise MeetingSessionWebAPIRequestError
    return payload


def _require_only_fields(payload: Mapping[str, Any], allowed: set[str]) -> None:
    if set(payload) != allowed:
        raise MeetingSessionWebAPIRequestError


def _runtime_error(
    error: MeetingSessionError,
) -> MeetingSessionWebAPIResponse:
    statuses = {
        MeetingSessionFailureCode.INVALID_REQUEST: HTTPStatus.BAD_REQUEST,
        MeetingSessionFailureCode.DRAFT_UNAVAILABLE: HTTPStatus.NOT_FOUND,
        MeetingSessionFailureCode.WRONG_SOURCE_TYPE: HTTPStatus.CONFLICT,
        MeetingSessionFailureCode.CAPACITY_EXCEEDED: (
            HTTPStatus.SERVICE_UNAVAILABLE
        ),
        MeetingSessionFailureCode.SESSION_NOT_FOUND: HTTPStatus.NOT_FOUND,
        MeetingSessionFailureCode.IDEMPOTENCY_CONFLICT: HTTPStatus.CONFLICT,
        MeetingSessionFailureCode.DISCLOSURE_MISMATCH: HTTPStatus.CONFLICT,
        MeetingSessionFailureCode.CONSENT_REQUIRED: HTTPStatus.CONFLICT,
        MeetingSessionFailureCode.INVALID_TRANSITION: HTTPStatus.CONFLICT,
        MeetingSessionFailureCode.ADAPTER_FAILED: (
            HTTPStatus.SERVICE_UNAVAILABLE
        ),
        MeetingSessionFailureCode.FINALIZATION_FAILED: (
            HTTPStatus.SERVICE_UNAVAILABLE
        ),
    }
    return MeetingSessionWebAPIResponse(
        statuses[error.failure_code],
        {
            "code": error.code,
            "error": str(error),
            "next_action": error.next_action,
        },
    )


def _error(
    status: HTTPStatus,
    message: str,
) -> MeetingSessionWebAPIResponse:
    return MeetingSessionWebAPIResponse(status, {"error": message})


__all__ = [
    "MeetingSessionWebAPIRequestError",
    "MeetingSessionWebAPIResponse",
    "handle_meeting_session_web_api_request",
    "is_meeting_session_web_api_target",
    "meeting_session_web_api_poc_id",
]
