"""Pinned single-attempt Fireworks prerecorded speech-to-text transport.

This adapter is deliberately narrower than a general HTTP client.  It accepts
only the private :class:`~exitspec.stt_operation.STTTransportRequest` issued by
ExitSpec's consent and egress boundary, sends one code-pinned multipart request
to the reviewed Fireworks origin, validates a bounded verbose-JSON response,
and returns permanently untrusted transcript segments.

Fireworks' prerecorded STT endpoint is documented in its official archived
cookbook.  The adapter is therefore opt-in and experimental until a live smoke
receipt is recorded; callers cannot override the host, model, region, request
shape, retry count, or retention policy.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import math
import re
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Final

from ..stt_boundary import STTRetentionMode, STTSpeakerMappingState
from ..stt_operation import (
    STTOperationFailureCode,
    STTTransportError,
    STTTransportRequest,
    STTTransportResponse,
    STTTransportSegment,
)


FIREWORKS_STT_ENDPOINT: Final = (
    "https://audio-prod.us-virginia-1.direct.fireworks.ai"
    "/v1/audio/transcriptions"
)
FIREWORKS_STT_PROVIDER: Final = "fireworks"
FIREWORKS_STT_MODEL: Final = "whisper-v3"
FIREWORKS_STT_REGION: Final = "us-virginia-1"
FIREWORKS_STT_POLICY_CHECKED_AT: Final = "2026-08-05"
FIREWORKS_STT_DATA_POLICY_URL: Final = (
    "https://docs.fireworks.ai/guides/security_compliance/data_handling"
)
FIREWORKS_STT_DATA_POLICY_SHA256: Final = hashlib.sha256(
    (
        "Fireworks Zero Data Retention documentation checked 2026-08-05: "
        "open-model prompt and generation data is not logged or persisted "
        "without explicit opt-in; non-Responses services follow that policy."
    ).encode("utf-8")
).hexdigest()

_HOST: Final = "audio-prod.us-virginia-1.direct.fireworks.ai"
_PORT: Final = 443
_PATH: Final = "/v1/audio/transcriptions"
_USER_AGENT: Final = "ExitSpec/0.1 stt-boundary"
_MAX_TIMEOUT_SECONDS: Final = 60.0
_DEFAULT_RESPONSE_LIMIT_BYTES: Final = 2 * 1024 * 1024
_MAX_AUDIO_BYTES: Final = 25 * 1024 * 1024
_MAX_SEGMENTS: Final = 10_000
_MAX_SEGMENT_TEXT_CHARACTERS: Final = 8_000
_MAX_TOTAL_TEXT_CHARACTERS: Final = 200_000
_MAX_PROVIDER_REQUEST_ID_CHARACTERS: Final = 512
_BOUNDARY_PREFIX: Final = "exitspec-stt-"
_LANGUAGE_PATTERN: Final = re.compile(r"^[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*$")


def _configuration_error() -> STTTransportError:
    return STTTransportError(STTOperationFailureCode.TRANSPORT_CONFIGURATION)


def _require_api_key(value: object) -> str:
    if (
        type(value) is not str
        or not value
        or len(value) > 4096
        or value != value.strip()
        or any(character.isspace() for character in value)
        or any(ord(character) < 0x21 or ord(character) > 0x7E for character in value)
    ):
        raise _configuration_error() from None
    return value


class FireworksSTTTransport:
    """Execute one pinned Fireworks Whisper v3 transcription request."""

    __slots__ = (
        "__api_key",
        "__connection_factory",
        "__max_response_body_bytes",
    )

    def __init__(
        self,
        *,
        api_key: object,
        connection_factory: Callable[..., Any] | None = None,
        max_response_body_bytes: int = _DEFAULT_RESPONSE_LIMIT_BYTES,
    ) -> None:
        if connection_factory is not None and not callable(connection_factory):
            raise TypeError("connection_factory must be callable.")
        if (
            type(max_response_body_bytes) is not int
            or not 1 <= max_response_body_bytes <= 16 * 1024 * 1024
        ):
            raise ValueError("Fireworks STT response limit is invalid.")
        self.__api_key = _require_api_key(api_key)
        self.__connection_factory = (
            http.client.HTTPSConnection
            if connection_factory is None
            else connection_factory
        )
        self.__max_response_body_bytes = max_response_body_bytes

    def __repr__(self) -> str:
        return (
            "FireworksSTTTransport(endpoint=<pinned>, model='whisper-v3', "
            "api_key=<redacted>, connection_factory=<redacted>, "
            "automatic_retries=0)"
        )

    def transcribe(self, request: STTTransportRequest) -> STTTransportResponse:
        """Send exactly one request and return detached untrusted segments."""

        if type(request) is not STTTransportRequest:
            raise STTTransportError(STTOperationFailureCode.TRANSPORT_CONFIGURATION)
        authorization = request.authorization
        if (
            authorization.provider != FIREWORKS_STT_PROVIDER
            or authorization.provider_model != FIREWORKS_STT_MODEL
            or authorization.region != FIREWORKS_STT_REGION
            or authorization.retention_mode is not STTRetentionMode.ZERO_RETENTION
            or authorization.media_type != "audio/webm"
            or authorization.byte_length > _MAX_AUDIO_BYTES
            or not 0 < request.timeout_seconds <= _MAX_TIMEOUT_SECONDS
        ):
            raise STTTransportError(STTOperationFailureCode.TRANSPORT_CONFIGURATION)

        audio_bytes: bytes | None = request.read_audio_bytes()
        connection: Any | None = None
        response: Any | None = None
        try:
            boundary = _multipart_boundary(audio_bytes)
            body = _multipart_body(audio_bytes, boundary)
            headers = {
                "Accept": "application/json",
                "Authorization": "Bearer " + self.__api_key,
                "Content-Length": str(len(body)),
                "Content-Type": "multipart/form-data; boundary=" + boundary,
                "User-Agent": _USER_AGENT,
            }
            connection = self.__connection_factory(
                _HOST,
                _PORT,
                timeout=float(request.timeout_seconds),
            )
            connection.request("POST", _PATH, body=body, headers=headers)
            response = connection.getresponse()
            status = _response_status(response)
            response_headers = _response_headers(response)
            response_body = _read_bounded(
                response,
                self.__max_response_body_bytes,
            )
            if status != 200:
                raise _status_error(status) from None
            return _parse_response(
                response_body,
                response_headers,
                authorization.duration_ms,
            )
        except STTTransportError:
            raise
        except TimeoutError:
            raise STTTransportError(STTOperationFailureCode.TIMEOUT) from None
        except (OSError, http.client.HTTPException):
            raise STTTransportError(STTOperationFailureCode.TRANSPORT) from None
        except Exception:
            raise STTTransportError(STTOperationFailureCode.INVALID_RESPONSE) from None
        finally:
            audio_bytes = None
            _close(response)
            _close(connection)


def _multipart_boundary(audio_bytes: bytes) -> str:
    digest = hashlib.sha256(audio_bytes).hexdigest()
    for offset in range(0, 32):
        candidate = _BOUNDARY_PREFIX + hashlib.sha256(
            (digest + f":{offset}").encode("ascii")
        ).hexdigest()[:40]
        if candidate.encode("ascii") not in audio_bytes:
            return candidate
    raise STTTransportError(STTOperationFailureCode.TRANSPORT_CONFIGURATION)


def _multipart_body(audio_bytes: bytes, boundary: str) -> bytes:
    line = ("--" + boundary).encode("ascii")
    fields = (
        ("model", FIREWORKS_STT_MODEL),
        ("language", "en"),
        ("response_format", "verbose_json"),
        ("timestamp_granularities[]", "word,segment"),
        ("diarize", "true"),
    )
    chunks: list[bytes] = []
    for name, value in fields:
        chunks.extend(
            (
                line,
                (
                    'Content-Disposition: form-data; name="{0}"'.format(name)
                ).encode("ascii"),
                b"",
                value.encode("utf-8"),
            )
        )
    chunks.extend(
        (
            line,
            (
                'Content-Disposition: form-data; name="file"; '
                'filename="capture.webm"'
            ).encode("ascii"),
            b"Content-Type: audio/webm",
            b"",
            audio_bytes,
            line + b"--",
            b"",
        )
    )
    return b"\r\n".join(chunks)


def _response_status(response: object) -> int:
    value = getattr(response, "status")
    if type(value) is not int or not 100 <= value <= 599:
        raise STTTransportError(STTOperationFailureCode.INVALID_RESPONSE)
    return value


def _response_headers(response: object) -> dict[str, str]:
    raw_headers = response.getheaders()
    if not isinstance(raw_headers, Sequence) or len(raw_headers) > 128:
        raise STTTransportError(STTOperationFailureCode.INVALID_RESPONSE)
    headers: dict[str, str] = {}
    for item in raw_headers:
        if not isinstance(item, tuple) or len(item) != 2:
            raise STTTransportError(STTOperationFailureCode.INVALID_RESPONSE)
        name, value = item
        if (
            type(name) is not str
            or type(value) is not str
            or any(ord(character) < 0x20 or ord(character) == 0x7F for character in name + value)
        ):
            raise STTTransportError(STTOperationFailureCode.INVALID_RESPONSE)
        lowered = name.lower()
        if lowered in headers:
            raise STTTransportError(STTOperationFailureCode.INVALID_RESPONSE)
        headers[lowered] = value
    return headers


def _read_bounded(response: object, maximum: int) -> bytes:
    body = response.read(maximum + 1)
    if type(body) is not bytes or len(body) > maximum:
        raise STTTransportError(STTOperationFailureCode.INVALID_RESPONSE)
    return body


def _status_error(status: int) -> STTTransportError:
    if status in {401, 403}:
        code = STTOperationFailureCode.AUTHENTICATION
    elif status in {402, 412}:
        code = STTOperationFailureCode.ACCOUNT_UNAVAILABLE
    elif status == 429:
        code = STTOperationFailureCode.RATE_LIMITED
    elif status in {408, 504}:
        code = STTOperationFailureCode.TIMEOUT
    elif status in {500, 502, 503}:
        code = STTOperationFailureCode.SERVICE_UNAVAILABLE
    elif status in {400, 404, 405, 409, 413, 415, 422}:
        code = STTOperationFailureCode.TRANSPORT_CONFIGURATION
    else:
        code = STTOperationFailureCode.TRANSPORT
    return STTTransportError(code)


def _json_object_no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate provider response key")
        result[key] = value
    return result


def _parse_response(
    body: bytes,
    headers: Mapping[str, str],
    audio_duration_ms: int,
) -> STTTransportResponse:
    content_type = headers.get("content-type", "application/json")
    if content_type.split(";", 1)[0].strip().lower() != "application/json":
        raise STTTransportError(STTOperationFailureCode.INVALID_RESPONSE)
    try:
        payload = json.loads(
            body.decode("utf-8"),
            object_pairs_hook=_json_object_no_duplicates,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        raise STTTransportError(STTOperationFailureCode.INVALID_RESPONSE) from None
    if not isinstance(payload, Mapping):
        raise STTTransportError(STTOperationFailureCode.INVALID_RESPONSE)

    language = payload.get("language", "en")
    if type(language) is not str or _LANGUAGE_PATTERN.fullmatch(language) is None:
        raise STTTransportError(STTOperationFailureCode.INVALID_RESPONSE)
    segments = _parse_segments(payload, audio_duration_ms)
    labels_present = tuple(segment.speaker_label is not None for segment in segments)
    if any(labels_present) and not all(labels_present):
        segments = tuple(
            STTTransportSegment(
                start_ms=segment.start_ms,
                end_ms=segment.end_ms,
                text=segment.text,
                speaker_label=segment.speaker_label or "speaker-unknown",
            )
            for segment in segments
        )
        labels_present = tuple(True for _ in segments)
    speaker_mapping = (
        STTSpeakerMappingState.PROVIDER_ASSIGNED_UNVERIFIED
        if all(labels_present)
        else STTSpeakerMappingState.NOT_PROVIDED
    )
    request_id = _provider_request_id(headers, payload, body)
    return STTTransportResponse(
        provider_request_id=request_id,
        language=language,
        speaker_mapping=speaker_mapping,
        segments=segments,
    )


def _parse_segments(
    payload: Mapping[str, Any],
    audio_duration_ms: int,
) -> tuple[STTTransportSegment, ...]:
    raw_segments = payload.get("segments")
    if not isinstance(raw_segments, list) or not 1 <= len(raw_segments) <= _MAX_SEGMENTS:
        text = payload.get("text")
        if type(text) is not str or not text.strip():
            raise STTTransportError(STTOperationFailureCode.INVALID_RESPONSE)
        raw_segments = [
            {
                "start": 0,
                "end": audio_duration_ms / 1000,
                "text": text,
            }
        ]

    parsed: list[STTTransportSegment] = []
    total_text = 0
    previous_end = 0
    for raw in raw_segments:
        if not isinstance(raw, Mapping):
            raise STTTransportError(STTOperationFailureCode.INVALID_RESPONSE)
        text = raw.get("text")
        start_ms = _seconds_to_ms(raw.get("start"), allow_zero=True)
        end_ms = _seconds_to_ms(raw.get("end"), allow_zero=False)
        if (
            type(text) is not str
            or not text.strip()
            or "\r" in text
            or len(text) > _MAX_SEGMENT_TEXT_CHARACTERS
            or start_ms < previous_end
            or end_ms <= start_ms
            or end_ms > audio_duration_ms
        ):
            raise STTTransportError(STTOperationFailureCode.INVALID_RESPONSE)
        normalized_text = " ".join(text.split())
        total_text += len(normalized_text)
        if total_text > _MAX_TOTAL_TEXT_CHARACTERS:
            raise STTTransportError(STTOperationFailureCode.INVALID_RESPONSE)
        speaker = raw.get("speaker_id", raw.get("speaker"))
        if speaker is not None and (
            type(speaker) is not str
            or not speaker.strip()
            or len(speaker) > 160
            or "\r" in speaker
        ):
            raise STTTransportError(STTOperationFailureCode.INVALID_RESPONSE)
        parsed.append(
            STTTransportSegment(
                start_ms=start_ms,
                end_ms=end_ms,
                text=normalized_text,
                speaker_label=None if speaker is None else speaker.strip(),
            )
        )
        previous_end = end_ms
    return tuple(parsed)


def _seconds_to_ms(value: object, *, allow_zero: bool) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or float(value) < 0
    ):
        raise STTTransportError(STTOperationFailureCode.INVALID_RESPONSE)
    milliseconds = int(round(float(value) * 1000))
    if not allow_zero and milliseconds <= 0:
        raise STTTransportError(STTOperationFailureCode.INVALID_RESPONSE)
    return milliseconds


def _provider_request_id(
    headers: Mapping[str, str],
    payload: Mapping[str, Any],
    body: bytes,
) -> str:
    candidates = (
        headers.get("x-request-id"),
        headers.get("request-id"),
        payload.get("id"),
    )
    for value in candidates:
        if (
            type(value) is str
            and value == value.strip()
            and 0 < len(value) <= _MAX_PROVIDER_REQUEST_ID_CHARACTERS
            and not any(character.isspace() for character in value)
        ):
            return value
    return "fireworks-response-" + hashlib.sha256(body).hexdigest()


def _close(value: object | None) -> None:
    if value is None:
        return
    try:
        value.close()  # type: ignore[attr-defined]
    except Exception:
        return


__all__ = [
    "FIREWORKS_STT_DATA_POLICY_SHA256",
    "FIREWORKS_STT_DATA_POLICY_URL",
    "FIREWORKS_STT_ENDPOINT",
    "FIREWORKS_STT_MODEL",
    "FIREWORKS_STT_POLICY_CHECKED_AT",
    "FIREWORKS_STT_PROVIDER",
    "FIREWORKS_STT_REGION",
    "FireworksSTTTransport",
]
