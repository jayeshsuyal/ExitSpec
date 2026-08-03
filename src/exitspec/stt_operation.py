"""One-use, provider-neutral synthetic audio operation for ExitSpec STT.

The operation is disabled by default and accepts only a private permit issued
from the PR95 policy boundary.  It contains no provider SDK or network
implementation.  Tests supply a fake transport to prove exact byte binding,
single consumption, failure behavior, and private transcript handling.
"""

from __future__ import annotations

import hashlib
import math
import re
import threading
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Literal, Mapping, Never, Protocol, Self

from pydantic import ConfigDict, Field, field_validator, model_validator

from .canonical import canonical_json_bytes
from .models import SHA256_PATTERN, FrozenExitSpecModel
from .poc_creation import POC_ID_PATTERN
from .stt_boundary import (
    PrivateSTTSerializationError,
    PrivateSTTValidationError,
    STTEgressAuthorizationRecord,
    STTEgressIntent,
    STTPrivacyPolicy,
    STTRetentionMode,
    STTSpeakerMappingState,
    STTTranscriptSegment,
    UntrustedSTTTranscript,
    authorize_stt_egress,
)


STT_OPERATION_VERSION = "exitspec-stt-operation/1.0"
STT_OPERATION_STATUS = "TRANSCRIBED_UNTRUSTED"
STT_OPERATION_AUTHORITY = "UNTRUSTED_SOURCE_ONLY"

_PERMIT_SEAL = object()
_TRANSPORT_REQUEST_SEAL = object()
_EXECUTOR_SEAL = object()
_OPERATION_RESULT_SEAL = object()
_PROVIDER_REQUEST_ID_DOMAIN = b"exitspec-stt-provider-request-v1\x00"
_OPERATION_ID_DOMAIN = b"exitspec-stt-operation-id-v1\x00"
_OPERATION_ID_PATTERN = r"^sttop_[a-f0-9]{64}$"
_AUTHORIZATION_ID_PATTERN = r"^sttauth_[a-f0-9]{64}$"
_REQUEST_ID_PATTERN = r"^sttreq_[a-z0-9][a-z0-9_-]{2,95}$"
_IDENTITY_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.:/+-]{1,199}$"
_MEDIA_TYPE_PATTERN = r"^audio/[a-z0-9][a-z0-9.+-]{0,63}$"
_MAX_PROVIDER_REQUEST_ID_CHARACTERS = 512
_MAX_TRANSPORT_SEGMENTS = 10_000


class STTOperationFailureCode(str, Enum):
    """Stable content-free failure classes for one audio operation."""

    DISABLED = "STT_OPERATION_DISABLED"
    INVALID_PERMIT = "STT_PERMIT_INVALID"
    EXPIRED_PERMIT = "STT_PERMIT_EXPIRED"
    REPLAYED_PERMIT = "STT_PERMIT_REPLAYED"
    CAPACITY_EXCEEDED = "STT_PERMIT_CAPACITY_EXCEEDED"
    AUDIO_BINDING_MISMATCH = "STT_AUDIO_BINDING_MISMATCH"
    TRANSPORT_CONFIGURATION = "STT_TRANSPORT_CONFIGURATION"
    AUTHENTICATION = "STT_PROVIDER_AUTHENTICATION"
    ACCOUNT_UNAVAILABLE = "STT_PROVIDER_ACCOUNT_UNAVAILABLE"
    RATE_LIMITED = "STT_PROVIDER_RATE_LIMITED"
    TIMEOUT = "STT_PROVIDER_TIMEOUT"
    SERVICE_UNAVAILABLE = "STT_PROVIDER_SERVICE_UNAVAILABLE"
    TRANSPORT = "STT_PROVIDER_TRANSPORT"
    INVALID_RESPONSE = "STT_PROVIDER_INVALID_RESPONSE"
    INTERNAL = "STT_OPERATION_INTERNAL"


_FAILURE_DETAILS: dict[STTOperationFailureCode, tuple[str, str]] = {
    STTOperationFailureCode.DISABLED: (
        "Speech-to-text execution is disabled.",
        "enable_reviewed_stt_transport",
    ),
    STTOperationFailureCode.INVALID_PERMIT: (
        "The speech-to-text audio permit is invalid.",
        "issue_a_new_audio_permit",
    ),
    STTOperationFailureCode.EXPIRED_PERMIT: (
        "The speech-to-text audio permit expired before use.",
        "issue_a_new_audio_permit",
    ),
    STTOperationFailureCode.REPLAYED_PERMIT: (
        "The speech-to-text audio permit was already used or issued.",
        "start_a_new_stt_request",
    ),
    STTOperationFailureCode.CAPACITY_EXCEEDED: (
        "The process-local speech-to-text permit store is at capacity.",
        "restart_stt_runtime_safely",
    ),
    STTOperationFailureCode.AUDIO_BINDING_MISMATCH: (
        "The supplied audio does not match the approved metadata.",
        "recapture_and_reauthorize_audio",
    ),
    STTOperationFailureCode.TRANSPORT_CONFIGURATION: (
        "The reviewed speech-to-text transport is not configured.",
        "configure_reviewed_stt_transport",
    ),
    STTOperationFailureCode.AUTHENTICATION: (
        "The speech-to-text provider rejected its credential.",
        "check_stt_provider_credential",
    ),
    STTOperationFailureCode.ACCOUNT_UNAVAILABLE: (
        "The speech-to-text provider account is unavailable.",
        "restore_stt_provider_account",
    ),
    STTOperationFailureCode.RATE_LIMITED: (
        "The speech-to-text provider rate-limited the operation.",
        "start_a_new_request_later",
    ),
    STTOperationFailureCode.TIMEOUT: (
        "The speech-to-text provider operation timed out.",
        "review_provider_state_before_retry",
    ),
    STTOperationFailureCode.SERVICE_UNAVAILABLE: (
        "The speech-to-text provider service is unavailable.",
        "start_a_new_request_later",
    ),
    STTOperationFailureCode.TRANSPORT: (
        "The speech-to-text transport failed.",
        "check_stt_provider_connectivity",
    ),
    STTOperationFailureCode.INVALID_RESPONSE: (
        "The speech-to-text provider response was not accepted.",
        "review_stt_provider_output",
    ),
    STTOperationFailureCode.INTERNAL: (
        "The speech-to-text operation could not complete safely.",
        "review_stt_operation",
    ),
}

_TRANSPORT_FAILURE_CODES = frozenset(
    {
        STTOperationFailureCode.TRANSPORT_CONFIGURATION,
        STTOperationFailureCode.AUTHENTICATION,
        STTOperationFailureCode.ACCOUNT_UNAVAILABLE,
        STTOperationFailureCode.RATE_LIMITED,
        STTOperationFailureCode.TIMEOUT,
        STTOperationFailureCode.SERVICE_UNAVAILABLE,
        STTOperationFailureCode.TRANSPORT,
    }
)


class STTOperationError(RuntimeError):
    """Sanitized operation refusal with no audio or transcript content."""

    retryable = False
    automatic_retry_allowed = False

    def __init__(
        self,
        failure_code: STTOperationFailureCode,
        *,
        attempts: int,
    ) -> None:
        if type(attempts) is not int or attempts < 0 or attempts > 1:
            raise ValueError("STT operation attempts must be zero or one.")
        self.failure_code = STTOperationFailureCode(failure_code)
        self.code = self.failure_code.value
        self.attempts = attempts
        safe_message, next_action = _FAILURE_DETAILS[self.failure_code]
        self.next_action = next_action
        super().__init__(safe_message)


class STTTransportError(RuntimeError):
    """Sanitized error that a provider-specific transport may raise."""

    def __init__(self, failure_code: STTOperationFailureCode) -> None:
        normalized = STTOperationFailureCode(failure_code)
        if normalized not in _TRANSPORT_FAILURE_CODES:
            raise ValueError("Unsupported STT transport failure code.")
        self.failure_code = normalized
        self.code = normalized.value
        super().__init__(_FAILURE_DETAILS[normalized][0])


class STTTransportSegment:
    """Private adapter output intentionally validated by the executor later."""

    __slots__ = ("_speaker_label", "_start_ms", "_end_ms", "_text")

    def __init__(
        self,
        *,
        start_ms: Any,
        end_ms: Any,
        text: Any,
        speaker_label: Any = None,
    ) -> None:
        object.__setattr__(self, "_start_ms", start_ms)
        object.__setattr__(self, "_end_ms", end_ms)
        object.__setattr__(self, "_text", text)
        object.__setattr__(self, "_speaker_label", speaker_label)

    @property
    def start_ms(self) -> Any:
        return self._start_ms

    @property
    def end_ms(self) -> Any:
        return self._end_ms

    @property
    def text(self) -> Any:
        return self._text

    @property
    def speaker_label(self) -> Any:
        return self._speaker_label

    def __setattr__(self, name: str, value: Any) -> Never:
        raise PrivateSTTSerializationError()

    def __repr__(self) -> str:
        return "STTTransportSegment(<private>)"

    def __reduce__(self) -> Never:
        raise PrivateSTTSerializationError()

    def __reduce_ex__(self, protocol: int) -> Never:
        raise PrivateSTTSerializationError()


class STTTransportResponse:
    """Private provider transcript projection returned to the executor."""

    __slots__ = (
        "_language",
        "_provider_request_id",
        "_segments",
        "_speaker_mapping",
    )

    def __init__(
        self,
        *,
        provider_request_id: Any,
        language: Any,
        speaker_mapping: Any,
        segments: Any,
    ) -> None:
        object.__setattr__(self, "_provider_request_id", provider_request_id)
        object.__setattr__(self, "_language", language)
        object.__setattr__(self, "_speaker_mapping", speaker_mapping)
        detached_segments = (
            tuple(segments) if isinstance(segments, (tuple, list)) else segments
        )
        object.__setattr__(self, "_segments", detached_segments)

    @property
    def provider_request_id(self) -> Any:
        return self._provider_request_id

    @property
    def language(self) -> Any:
        return self._language

    @property
    def speaker_mapping(self) -> Any:
        return self._speaker_mapping

    @property
    def segments(self) -> Any:
        return self._segments

    def __setattr__(self, name: str, value: Any) -> Never:
        raise PrivateSTTSerializationError()

    def __repr__(self) -> str:
        return "STTTransportResponse(<private>)"

    def __reduce__(self) -> Never:
        raise PrivateSTTSerializationError()

    def __reduce_ex__(self, protocol: int) -> Never:
        raise PrivateSTTSerializationError()


class STTTransportRequest:
    """Request-local exact audio released only to one transport call."""

    __slots__ = (
        "_audio_bytes",
        "_authorization",
        "_meeting_id",
        "_released",
        "_seal",
        "_lock",
    )

    def __init__(
        self,
        *,
        authorization: STTEgressAuthorizationRecord,
        meeting_id: str,
        audio_bytes: bytes,
        _seal: object = None,
    ) -> None:
        if _seal is not _TRANSPORT_REQUEST_SEAL:
            raise STTOperationError(
                STTOperationFailureCode.INVALID_PERMIT,
                attempts=0,
            )
        self._authorization = authorization
        self._meeting_id = meeting_id
        self._audio_bytes: bytes | None = audio_bytes
        self._released = False
        self._seal = _seal
        self._lock = threading.Lock()

    @property
    def authorization(self) -> STTEgressAuthorizationRecord:
        return self._authorization

    @property
    def timeout_seconds(self) -> float:
        """Return the reviewed timeout a real transport must enforce."""

        return self._authorization.transport_timeout_seconds

    def read_audio_bytes(self) -> bytes:
        """Expose immutable bytes only to the selected transport call."""

        with self._lock:
            if self._released or self._audio_bytes is None:
                raise STTOperationError(
                    STTOperationFailureCode.INVALID_PERMIT,
                    attempts=0,
                )
            return self._audio_bytes

    def _meeting_identity(self, *, _seal: object) -> str:
        if _seal is not _EXECUTOR_SEAL:
            raise STTOperationError(
                STTOperationFailureCode.INVALID_PERMIT,
                attempts=0,
            )
        return self._meeting_id

    def _release(self, *, _seal: object) -> None:
        if _seal is not _EXECUTOR_SEAL:
            raise STTOperationError(
                STTOperationFailureCode.INVALID_PERMIT,
                attempts=0,
            )
        with self._lock:
            self._audio_bytes = None
            self._released = True

    def __repr__(self) -> str:
        return (
            "STTTransportRequest(authorization_id={0!r}, audio=<private>, "
            "released={1!r})"
        ).format(
            self._authorization.authorization_id,
            self._released,
        )

    def __reduce__(self) -> Never:
        raise PrivateSTTSerializationError()

    def __reduce_ex__(self, protocol: int) -> Never:
        raise PrivateSTTSerializationError()


class STTTransport(Protocol):
    """Provider-neutral transport seam; real implementations land separately."""

    def transcribe(self, request: STTTransportRequest) -> STTTransportResponse:
        ...


def _aware_utc(value: object, *, attempts: int = 0) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise STTOperationError(
            STTOperationFailureCode.INTERNAL,
            attempts=attempts,
        )
    return value.astimezone(timezone.utc)


def _read_clock(
    clock: Callable[[], datetime],
    *,
    attempts: int,
) -> datetime:
    clock_failed = False
    value: object = None
    try:
        value = clock()
    except Exception:
        clock_failed = True
    if clock_failed:
        raise STTOperationError(
            STTOperationFailureCode.INTERNAL,
            attempts=attempts,
        ) from None
    return _aware_utc(value, attempts=attempts)


def _digest(domain: bytes, payload: Any) -> str:
    return hashlib.sha256(domain + canonical_json_bytes(payload)).hexdigest()


def _audio_digest(audio_bytes: bytes) -> str:
    return hashlib.sha256(audio_bytes).hexdigest()


class STTAudioPermit:
    """One-use private permit carrying exact synthetic audio bytes."""

    __slots__ = (
        "_audio_bytes",
        "_authorization",
        "_clock",
        "_lock",
        "_meeting_id",
        "_seal",
        "_taken",
    )

    def __init__(
        self,
        *,
        authorization: STTEgressAuthorizationRecord,
        meeting_id: str,
        audio_bytes: bytes,
        clock: Callable[[], datetime],
        _seal: object = None,
    ) -> None:
        if _seal is not _PERMIT_SEAL:
            raise STTOperationError(
                STTOperationFailureCode.INVALID_PERMIT,
                attempts=0,
            )
        self._authorization = authorization
        self._meeting_id = meeting_id
        self._audio_bytes: bytes | None = audio_bytes
        self._clock = clock
        self._seal = _seal
        self._taken = False
        self._lock = threading.Lock()

    @property
    def authorization(self) -> STTEgressAuthorizationRecord:
        return self._authorization

    @property
    def is_taken(self) -> bool:
        with self._lock:
            return self._taken

    def _take_for_transport(self, *, _seal: object) -> STTTransportRequest:
        if _seal is not _EXECUTOR_SEAL:
            raise STTOperationError(
                STTOperationFailureCode.INVALID_PERMIT,
                attempts=0,
            )
        with self._lock:
            if (
                self._seal is not _PERMIT_SEAL
                or self._taken
                or self._audio_bytes is None
            ):
                raise STTOperationError(
                    STTOperationFailureCode.REPLAYED_PERMIT,
                    attempts=0,
                )
            try:
                checked_at = _read_clock(self._clock, attempts=0)
            except STTOperationError:
                self._audio_bytes = None
                self._taken = True
                raise
            if checked_at >= self._authorization.expires_at:
                self._audio_bytes = None
                self._taken = True
                raise STTOperationError(
                    STTOperationFailureCode.EXPIRED_PERMIT,
                    attempts=0,
                )
            if checked_at < self._authorization.authorized_at:
                self._audio_bytes = None
                self._taken = True
                raise STTOperationError(
                    STTOperationFailureCode.INVALID_PERMIT,
                    attempts=0,
                )
            request = STTTransportRequest(
                authorization=self._authorization,
                meeting_id=self._meeting_id,
                audio_bytes=self._audio_bytes,
                _seal=_TRANSPORT_REQUEST_SEAL,
            )
            self._audio_bytes = None
            self._taken = True
            return request

    def __repr__(self) -> str:
        return (
            "STTAudioPermit(authorization_id={0!r}, audio=<private>, "
            "taken={1!r})"
        ).format(
            self._authorization.authorization_id,
            self.is_taken,
        )

    def __reduce__(self) -> Never:
        raise PrivateSTTSerializationError()

    def __reduce_ex__(self, protocol: int) -> Never:
        raise PrivateSTTSerializationError()


class STTAudioPermitIssuer:
    """Server-owned issuer that permits one exact authorization only once."""

    __slots__ = ("_clock", "_issued", "_lock", "_max_issued", "_policy")

    def __init__(
        self,
        policy: STTPrivacyPolicy,
        *,
        clock: Callable[[], datetime] | None = None,
        max_issued: int = 4_096,
    ) -> None:
        if type(policy) is not STTPrivacyPolicy:
            raise ValueError("STT audio permit issuer requires an exact policy.")
        selected_clock = (
            (lambda: datetime.now(timezone.utc)) if clock is None else clock
        )
        if not callable(selected_clock):
            raise ValueError("STT audio permit issuer requires a clock.")
        if (
            type(max_issued) is not int
            or max_issued <= 0
            or max_issued > 100_000
        ):
            raise ValueError("STT permit capacity is outside supported bounds.")
        self._policy = policy
        self._clock = selected_clock
        self._max_issued = max_issued
        self._issued: set[str] = set()
        self._lock = threading.Lock()

    def issue(
        self,
        intent: STTEgressIntent,
        audio_bytes: object,
    ) -> STTAudioPermit:
        """Bind exact immutable bytes and issue one private permit."""

        current_time = _read_clock(self._clock, attempts=0)
        authorization = authorize_stt_egress(
            self._policy,
            intent,
            now=current_time,
        )
        if type(audio_bytes) is not bytes:
            raise STTOperationError(
                STTOperationFailureCode.AUDIO_BINDING_MISMATCH,
                attempts=0,
            )
        if (
            len(audio_bytes) != authorization.byte_length
            or _audio_digest(audio_bytes) != authorization.audio_sha256
        ):
            raise STTOperationError(
                STTOperationFailureCode.AUDIO_BINDING_MISMATCH,
                attempts=0,
            )

        with self._lock:
            if authorization.authorization_id in self._issued:
                raise STTOperationError(
                    STTOperationFailureCode.REPLAYED_PERMIT,
                    attempts=0,
                )
            if len(self._issued) >= self._max_issued:
                raise STTOperationError(
                    STTOperationFailureCode.CAPACITY_EXCEEDED,
                    attempts=0,
                )
            self._issued.add(authorization.authorization_id)

        return STTAudioPermit(
            authorization=authorization,
            meeting_id=intent.meeting_id,
            audio_bytes=audio_bytes,
            clock=self._clock,
            _seal=_PERMIT_SEAL,
        )

    def was_issued(self, authorization_id: str) -> bool:
        """Return one content-free issuance fact for tests/coordinators."""

        with self._lock:
            return authorization_id in self._issued

    def __repr__(self) -> str:
        return "STTAudioPermitIssuer(policy=<reviewed>, audio=<never-stored>)"


class _FrozenOperationModel(FrozenExitSpecModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
        validate_default=True,
    )

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        if not update:
            return super().model_copy(deep=deep)
        payload = self.model_dump(mode="python")
        payload.update(dict(update))
        return type(self).model_validate(payload)

    def copy(
        self,
        *,
        include: Any = None,
        exclude: Any = None,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        if include is not None or exclude is not None:
            raise ValueError("include/exclude copies are not supported here.")
        return self.model_copy(update=update, deep=deep)


class STTOperationReceipt(_FrozenOperationModel):
    """Safe content-free success receipt before transcript redaction."""

    schema_version: Literal[STT_OPERATION_VERSION] = STT_OPERATION_VERSION
    operation_id: str = Field(pattern=_OPERATION_ID_PATTERN)
    authorization_id: str = Field(pattern=_AUTHORIZATION_ID_PATTERN)
    request_id: str = Field(pattern=_REQUEST_ID_PATTERN)
    poc_id: str = Field(pattern=POC_ID_PATTERN)
    meeting_identity_sha256: str = Field(pattern=SHA256_PATTERN)
    audio_sha256: str = Field(pattern=SHA256_PATTERN)
    provider_request_id_sha256: str = Field(pattern=SHA256_PATTERN)
    provider: str = Field(pattern=_IDENTITY_PATTERN)
    provider_model: str = Field(pattern=_IDENTITY_PATTERN)
    region: str = Field(pattern=_IDENTITY_PATTERN)
    media_type: str = Field(pattern=_MEDIA_TYPE_PATTERN)
    byte_length: int = Field(gt=0)
    duration_ms: int = Field(gt=0)
    transport_timeout_seconds: float = Field(gt=0, le=300)
    segment_count: int = Field(gt=0, le=_MAX_TRANSPORT_SEGMENTS)
    elapsed_ms: int = Field(ge=0)
    attempts: Literal[1] = 1
    automatic_retries: Literal[0] = 0
    policy_retention_mode: Literal[STTRetentionMode.ZERO_RETENTION] = (
        STTRetentionMode.ZERO_RETENTION
    )
    status: Literal[STT_OPERATION_STATUS] = STT_OPERATION_STATUS
    authority: Literal[STT_OPERATION_AUTHORITY] = STT_OPERATION_AUTHORITY
    exitspec_audio_persisted: Literal[False] = False
    exitspec_transcript_persisted: Literal[False] = False
    completed_at: datetime

    @field_validator("completed_at")
    @classmethod
    def validate_completed_at(cls, value: datetime) -> datetime:
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise ValueError("completed_at must be timezone-aware.")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def require_deterministic_operation_id(self) -> "STTOperationReceipt":
        expected = "sttop_" + _digest(
            _OPERATION_ID_DOMAIN,
            {
                "authorization_id": self.authorization_id,
                "provider_request_id_sha256": (
                    self.provider_request_id_sha256
                ),
                "segment_count": self.segment_count,
            },
        )
        if self.operation_id != expected:
            raise ValueError("operation_id does not match its binding.")
        return self


class STTOperationResult:
    """Private transcript plus its separately serializable safe receipt."""

    __slots__ = ("_receipt", "_transcript")

    def __init__(
        self,
        *,
        receipt: STTOperationReceipt,
        transcript: UntrustedSTTTranscript,
        _seal: object,
    ) -> None:
        if (
            _seal is not _OPERATION_RESULT_SEAL
            or type(receipt) is not STTOperationReceipt
            or type(transcript) is not UntrustedSTTTranscript
        ):
            raise ValueError("STT operation result is invalid.")
        self._receipt = receipt
        self._transcript = transcript

    @property
    def receipt(self) -> STTOperationReceipt:
        return self._receipt

    @property
    def transcript(self) -> UntrustedSTTTranscript:
        return self._transcript

    def __repr__(self) -> str:
        return (
            "STTOperationResult(operation_id={0!r}, transcript=<private>)"
        ).format(self._receipt.operation_id)

    def __reduce__(self) -> Never:
        raise PrivateSTTSerializationError()

    def __reduce_ex__(self, protocol: int) -> Never:
        raise PrivateSTTSerializationError()


def _provider_request_digest(value: object) -> str:
    if (
        type(value) is not str
        or not value
        or len(value) > _MAX_PROVIDER_REQUEST_ID_CHARACTERS
        or value != value.strip()
        or any(character.isspace() for character in value)
    ):
        raise STTOperationError(
            STTOperationFailureCode.INVALID_RESPONSE,
            attempts=1,
        )
    return _digest(_PROVIDER_REQUEST_ID_DOMAIN, {"provider_request_id": value})


def _validated_segments(response: STTTransportResponse) -> tuple[
    STTTranscriptSegment,
    ...,
]:
    if not isinstance(response.segments, (tuple, list)):
        raise STTOperationError(
            STTOperationFailureCode.INVALID_RESPONSE,
            attempts=1,
        )
    segments = tuple(response.segments)
    if not segments or len(segments) > _MAX_TRANSPORT_SEGMENTS:
        raise STTOperationError(
            STTOperationFailureCode.INVALID_RESPONSE,
            attempts=1,
        )
    try:
        return tuple(
            STTTranscriptSegment(
                segment_id=f"segment_{ordinal:05d}",
                start_ms=segment.start_ms,
                end_ms=segment.end_ms,
                speaker_label=segment.speaker_label,
                text=segment.text,
            )
            for ordinal, segment in enumerate(segments, start=1)
            if type(segment) is STTTransportSegment
        )
    except (PrivateSTTValidationError, TypeError, ValueError):
        raise STTOperationError(
            STTOperationFailureCode.INVALID_RESPONSE,
            attempts=1,
        ) from None


class STTOperationExecutor:
    """Disabled-by-default, single-attempt executor for one private permit."""

    __slots__ = ("_clock", "_enabled", "_monotonic", "_transport")

    def __init__(
        self,
        transport: STTTransport,
        *,
        enabled: bool = False,
        clock: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        transcribe = getattr(transport, "transcribe", None)
        if not callable(transcribe):
            raise ValueError("STT executor requires a typed transport.")
        if type(enabled) is not bool:
            raise ValueError("STT executor enabled state must be explicit.")
        selected_clock = (
            (lambda: datetime.now(timezone.utc)) if clock is None else clock
        )
        if monotonic is None:
            import time

            selected_monotonic = time.monotonic
        else:
            selected_monotonic = monotonic
        if not callable(selected_clock) or not callable(selected_monotonic):
            raise ValueError("STT executor clocks must be callable.")
        self._transport = transport
        self._enabled = enabled
        self._clock = selected_clock
        self._monotonic = selected_monotonic

    @property
    def enabled(self) -> bool:
        return self._enabled

    def _monotonic_value(self) -> float:
        try:
            value = self._monotonic()
        except Exception:
            raise STTOperationError(
                STTOperationFailureCode.INTERNAL,
                attempts=0,
            ) from None
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(float(value))
        ):
            raise STTOperationError(
                STTOperationFailureCode.INTERNAL,
                attempts=0,
            )
        return float(value)

    def execute(self, permit: object) -> STTOperationResult:
        """Consume one exact permit and make at most one transport attempt."""

        if not self._enabled:
            raise STTOperationError(
                STTOperationFailureCode.DISABLED,
                attempts=0,
            )
        if type(permit) is not STTAudioPermit:
            raise STTOperationError(
                STTOperationFailureCode.INVALID_PERMIT,
                attempts=0,
            )
        started = self._monotonic_value()
        request = permit._take_for_transport(_seal=_EXECUTOR_SEAL)
        authorization = request.authorization
        meeting_id = request._meeting_identity(_seal=_EXECUTOR_SEAL)
        response: object = None
        transport_failure: STTOperationFailureCode | None = None
        try:
            response = self._transport.transcribe(request)
        except STTTransportError as error:
            transport_failure = error.failure_code
        except TimeoutError:
            transport_failure = STTOperationFailureCode.TIMEOUT
        except Exception:
            transport_failure = STTOperationFailureCode.INTERNAL
        finally:
            request._release(_seal=_EXECUTOR_SEAL)

        if transport_failure is not None:
            raise STTOperationError(
                transport_failure,
                attempts=1,
            ) from None

        if type(response) is not STTTransportResponse:
            raise STTOperationError(
                STTOperationFailureCode.INVALID_RESPONSE,
                attempts=1,
            )
        provider_request_sha256 = _provider_request_digest(
            response.provider_request_id
        )
        if type(response.speaker_mapping) is not STTSpeakerMappingState:
            raise STTOperationError(
                STTOperationFailureCode.INVALID_RESPONSE,
                attempts=1,
            )
        segments = _validated_segments(response)
        if len(segments) != len(response.segments):
            raise STTOperationError(
                STTOperationFailureCode.INVALID_RESPONSE,
                attempts=1,
            )

        finished = self._monotonic_value()
        if finished < started:
            raise STTOperationError(
                STTOperationFailureCode.INTERNAL,
                attempts=1,
            )
        completed_at = _read_clock(self._clock, attempts=1)
        if completed_at < authorization.authorized_at:
            raise STTOperationError(
                STTOperationFailureCode.INTERNAL,
                attempts=1,
            )
        elapsed_ms = int(round((finished - started) * 1000))

        try:
            transcript = UntrustedSTTTranscript(
                authorization_id=authorization.authorization_id,
                request_id=authorization.request_id,
                poc_id=authorization.poc_id,
                meeting_id=meeting_id,
                audio_sha256=authorization.audio_sha256,
                audio_duration_ms=authorization.duration_ms,
                provider=authorization.provider,
                provider_model=authorization.provider_model,
                region=authorization.region,
                provider_request_id_sha256=provider_request_sha256,
                language=response.language,
                speaker_mapping=response.speaker_mapping,
                segments=segments,
                completed_at=completed_at,
            )
        except (PrivateSTTValidationError, TypeError, ValueError):
            raise STTOperationError(
                STTOperationFailureCode.INVALID_RESPONSE,
                attempts=1,
            ) from None

        operation_payload = {
            "authorization_id": authorization.authorization_id,
            "provider_request_id_sha256": provider_request_sha256,
            "segment_count": len(segments),
        }
        operation_id = "sttop_" + _digest(
            _OPERATION_ID_DOMAIN,
            operation_payload,
        )
        receipt = STTOperationReceipt(
            operation_id=operation_id,
            authorization_id=authorization.authorization_id,
            request_id=authorization.request_id,
            poc_id=authorization.poc_id,
            meeting_identity_sha256=authorization.meeting_identity_sha256,
            audio_sha256=authorization.audio_sha256,
            provider_request_id_sha256=provider_request_sha256,
            provider=authorization.provider,
            provider_model=authorization.provider_model,
            region=authorization.region,
            media_type=authorization.media_type,
            byte_length=authorization.byte_length,
            duration_ms=authorization.duration_ms,
            transport_timeout_seconds=authorization.transport_timeout_seconds,
            segment_count=len(segments),
            elapsed_ms=elapsed_ms,
            policy_retention_mode=authorization.retention_mode,
            completed_at=completed_at,
        )
        return STTOperationResult(
            receipt=receipt,
            transcript=transcript,
            _seal=_OPERATION_RESULT_SEAL,
        )

    def __repr__(self) -> str:
        return (
            "STTOperationExecutor(enabled={0!r}, transport=<private>, "
            "automatic_retries=0)"
        ).format(self._enabled)


__all__ = [
    "STTAudioPermit",
    "STTAudioPermitIssuer",
    "STTOperationError",
    "STTOperationExecutor",
    "STTOperationFailureCode",
    "STTOperationReceipt",
    "STTOperationResult",
    "STTTransport",
    "STTTransportError",
    "STTTransportRequest",
    "STTTransportResponse",
    "STTTransportSegment",
]
