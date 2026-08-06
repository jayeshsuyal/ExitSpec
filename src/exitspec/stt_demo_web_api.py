"""Pure HTTP projection for the local ExitSpec STT browser adapter."""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from http import HTTPStatus
import re
from typing import Any, Mapping
from urllib.parse import urlparse

from .poc_creation import POC_ID_PATTERN
from .stt_demo_runtime import (
    ProcessLocalSTTDemoRuntime,
    STT_DEMO_MAX_AUDIO_BYTES,
    STTDemoError,
    STTDemoFailureCode,
)


_POC_ID = re.compile(POC_ID_PATTERN)
_CAPTURE_ID = re.compile(r"^sttcap_[a-f0-9]{64}$")
_MAX_ENCODED_AUDIO_CHARACTERS = 4 * (
    (STT_DEMO_MAX_AUDIO_BYTES + 2) // 3
)


@dataclass(frozen=True, slots=True)
class STTDemoWebAPIResponse:
    """Transport-neutral status and JSON object."""

    status: HTTPStatus
    payload: dict[str, Any]


class STTDemoWebAPIRequestError(ValueError):
    """A request targeted the STT demo namespace but was malformed."""


def is_stt_demo_web_api_target(target: str) -> bool:
    """Return whether a raw request target belongs to the STT demo."""

    if type(target) is not str:
        return False
    path = urlparse(target).path
    parts = path.strip("/").split("/")
    return (
        len(parts) >= 4
        and parts[:2] == ["api", "pocs"]
        and parts[3] == "stt"
    )


def stt_demo_web_api_poc_id(target: str) -> str | None:
    """Return the validated POC ID for an exact STT demo route."""

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
        poc_id, _ = _parse_path(parsed.path)
    except STTDemoWebAPIRequestError:
        return None
    return poc_id


def handle_stt_demo_web_api_request(
    *,
    method: str,
    target: str,
    payload: Mapping[str, Any] | None,
    runtime: ProcessLocalSTTDemoRuntime,
) -> STTDemoWebAPIResponse | None:
    """Handle one exact disclosure, consent, or capture request."""

    if type(runtime) is not ProcessLocalSTTDemoRuntime:
        raise TypeError("runtime must be a ProcessLocalSTTDemoRuntime.")
    if not is_stt_demo_web_api_target(target):
        return None

    try:
        path = _require_exact_local_target(target)
        poc_id, route = _parse_path(path)
        return _dispatch(
            method=method,
            poc_id=poc_id,
            route=route,
            payload=payload,
            runtime=runtime,
        )
    except STTDemoWebAPIRequestError:
        return _error(
            HTTPStatus.BAD_REQUEST,
            "Recording request is invalid.",
        )
    except STTDemoError as error:
        return _runtime_error(error)
    except (TypeError, ValueError):
        return _error(
            HTTPStatus.BAD_REQUEST,
            "Recording request is invalid.",
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
        raise STTDemoWebAPIRequestError
    return parsed.path


def _parse_path(path: str) -> tuple[str, str]:
    if (
        not path.startswith("/")
        or path == "/"
        or path.endswith("/")
        or "//" in path
    ):
        raise STTDemoWebAPIRequestError
    parts = path[1:].split("/")
    if (
        len(parts) not in {5, 6}
        or parts[:2] != ["api", "pocs"]
        or parts[3] != "stt"
        or _POC_ID.fullmatch(parts[2]) is None
    ):
        raise STTDemoWebAPIRequestError
    if len(parts) == 5 and parts[4] in {"disclosure", "consents"}:
        return parts[2], parts[4]
    if (
        len(parts) == 6
        and parts[4] == "captures"
        and _CAPTURE_ID.fullmatch(parts[5]) is not None
    ):
        return parts[2], "captures/" + parts[5]
    raise STTDemoWebAPIRequestError


def _dispatch(
    *,
    method: str,
    poc_id: str,
    route: str,
    payload: Mapping[str, Any] | None,
    runtime: ProcessLocalSTTDemoRuntime,
) -> STTDemoWebAPIResponse:
    if type(method) is not str:
        raise STTDemoWebAPIRequestError

    if method == "GET":
        if payload is not None:
            raise STTDemoWebAPIRequestError
        if route == "disclosure":
            disclosure = runtime.disclosure_for(poc_id)
            return STTDemoWebAPIResponse(
                HTTPStatus.OK,
                disclosure.model_dump(mode="json"),
            )
        if route.startswith("captures/"):
            receipt = runtime.capture_receipt(
                poc_id=poc_id,
                capture_id=route.removeprefix("captures/"),
            )
            return STTDemoWebAPIResponse(
                HTTPStatus.OK,
                receipt.model_dump(mode="json"),
            )
        return _error(
            HTTPStatus.NOT_FOUND,
            "Recording route was not found.",
        )

    if method != "POST":
        return _error(
            HTTPStatus.METHOD_NOT_ALLOWED,
            "Recording method is not allowed.",
        )

    body = _require_object_payload(payload)
    if route == "consents":
        scope_field = (
            "provider_processing_acknowledged"
            if runtime.live_provider_enabled
            else "synthetic_demo_acknowledged"
        )
        _require_only_fields(
            body,
            {
                "all_speakers_consented",
                "disclosure_id",
                "idempotency_key",
                "recording_notice_acknowledged",
                scope_field,
            },
        )
        receipt = runtime.record_consent(
            poc_id=poc_id,
            disclosure_id=body["disclosure_id"],
            recording_notice_acknowledged=(
                body["recording_notice_acknowledged"]
            ),
            all_speakers_consented=body["all_speakers_consented"],
            synthetic_demo_acknowledged=(
                body.get("synthetic_demo_acknowledged")
            ),
            provider_processing_acknowledged=(
                body.get("provider_processing_acknowledged")
            ),
            idempotency_key=body["idempotency_key"],
        )
        return STTDemoWebAPIResponse(
            HTTPStatus.CREATED,
            receipt.model_dump(mode="json"),
        )

    if route.startswith("captures/"):
        _require_only_fields(
            body,
            {
                "audio_base64",
                "audio_sha256",
                "byte_length",
                "duration_ms",
                "idempotency_key",
                "media_type",
            },
        )
        audio_bytes = _decode_audio(body["audio_base64"])
        try:
            receipt = runtime.capture(
                poc_id=poc_id,
                capture_id=route.removeprefix("captures/"),
                audio_bytes=audio_bytes,
                audio_sha256=body["audio_sha256"],
                byte_length=body["byte_length"],
                duration_ms=body["duration_ms"],
                media_type=body["media_type"],
                idempotency_key=body["idempotency_key"],
            )
        finally:
            audio_bytes = b""
        status = (
            HTTPStatus.OK
            if receipt.idempotent_replay
            else HTTPStatus.CREATED
        )
        return STTDemoWebAPIResponse(
            status,
            receipt.model_dump(mode="json"),
        )

    return _error(
        HTTPStatus.NOT_FOUND,
        "Recording route was not found.",
    )


def _require_object_payload(
    payload: Mapping[str, Any] | None,
) -> Mapping[str, Any]:
    if type(payload) is not dict:
        raise STTDemoWebAPIRequestError
    return payload


def _require_only_fields(payload: Mapping[str, Any], allowed: set[str]) -> None:
    if set(payload) != allowed:
        raise STTDemoWebAPIRequestError


def _decode_audio(value: Any) -> bytes:
    if (
        type(value) is not str
        or not value
        or len(value) > _MAX_ENCODED_AUDIO_CHARACTERS
        or len(value) % 4 != 0
    ):
        if type(value) is str and len(value) > _MAX_ENCODED_AUDIO_CHARACTERS:
            raise STTDemoError(STTDemoFailureCode.AUDIO_TOO_LARGE)
        raise STTDemoError(STTDemoFailureCode.AUDIO_BINDING_MISMATCH)
    try:
        decoded = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        raise STTDemoError(
            STTDemoFailureCode.AUDIO_BINDING_MISMATCH
        ) from None
    if len(decoded) > STT_DEMO_MAX_AUDIO_BYTES:
        raise STTDemoError(STTDemoFailureCode.AUDIO_TOO_LARGE)
    return decoded


def _runtime_error(error: STTDemoError) -> STTDemoWebAPIResponse:
    statuses = {
        STTDemoFailureCode.INVALID_REQUEST: HTTPStatus.BAD_REQUEST,
        STTDemoFailureCode.DRAFT_UNAVAILABLE: HTTPStatus.NOT_FOUND,
        STTDemoFailureCode.CAPACITY_EXCEEDED: (
            HTTPStatus.SERVICE_UNAVAILABLE
        ),
        STTDemoFailureCode.DISCLOSURE_MISMATCH: HTTPStatus.CONFLICT,
        STTDemoFailureCode.CONSENT_REQUIRED: HTTPStatus.CONFLICT,
        STTDemoFailureCode.CONSENT_EXPIRED: HTTPStatus.CONFLICT,
        STTDemoFailureCode.CAPTURE_CONFLICT: HTTPStatus.CONFLICT,
        STTDemoFailureCode.CAPTURE_IN_PROGRESS: HTTPStatus.CONFLICT,
        STTDemoFailureCode.CAPTURE_CONSUMED: HTTPStatus.CONFLICT,
        STTDemoFailureCode.AUDIO_BINDING_MISMATCH: (
            HTTPStatus.UNPROCESSABLE_ENTITY
        ),
        STTDemoFailureCode.AUDIO_TOO_LARGE: (
            HTTPStatus.REQUEST_ENTITY_TOO_LARGE
        ),
        STTDemoFailureCode.AUDIO_TOO_LONG: (
            HTTPStatus.UNPROCESSABLE_ENTITY
        ),
        STTDemoFailureCode.UNSUPPORTED_MEDIA: (
            HTTPStatus.UNSUPPORTED_MEDIA_TYPE
        ),
        STTDemoFailureCode.OPERATION_FAILED: (
            HTTPStatus.SERVICE_UNAVAILABLE
        ),
        STTDemoFailureCode.HANDOFF_FAILED: (
            HTTPStatus.SERVICE_UNAVAILABLE
        ),
        STTDemoFailureCode.PROVIDER_CONFIGURATION: (
            HTTPStatus.SERVICE_UNAVAILABLE
        ),
        STTDemoFailureCode.PROVIDER_AUTHENTICATION: HTTPStatus.BAD_GATEWAY,
        STTDemoFailureCode.PROVIDER_ACCOUNT_UNAVAILABLE: (
            HTTPStatus.FAILED_DEPENDENCY
        ),
        STTDemoFailureCode.PROVIDER_RATE_LIMITED: HTTPStatus.TOO_MANY_REQUESTS,
        STTDemoFailureCode.PROVIDER_TIMEOUT: HTTPStatus.GATEWAY_TIMEOUT,
        STTDemoFailureCode.PROVIDER_SERVICE_UNAVAILABLE: (
            HTTPStatus.SERVICE_UNAVAILABLE
        ),
        STTDemoFailureCode.PROVIDER_TRANSPORT: HTTPStatus.BAD_GATEWAY,
        STTDemoFailureCode.PROVIDER_INVALID_RESPONSE: HTTPStatus.BAD_GATEWAY,
    }
    return STTDemoWebAPIResponse(
        statuses[error.failure_code],
        {
            "code": error.code,
            "error": str(error),
            "next_action": error.next_action,
        },
    )


def _error(status: HTTPStatus, message: str) -> STTDemoWebAPIResponse:
    return STTDemoWebAPIResponse(status, {"error": message})


__all__ = [
    "STTDemoWebAPIRequestError",
    "STTDemoWebAPIResponse",
    "handle_stt_demo_web_api_request",
    "is_stt_demo_web_api_target",
    "stt_demo_web_api_poc_id",
]
