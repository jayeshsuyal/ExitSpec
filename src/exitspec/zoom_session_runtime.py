"""Bounded Zoom RTMS session lifecycle and idempotency core.

This module coordinates one already-authorized local session around the strict
RTMS decoder. It does not authenticate Zoom, create a source, create a POC,
confirm a contract, freeze a contract, run proof, or issue a verdict. The
normalized transcript stays request-local until the later proposal bridge.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import hashlib
import re
from threading import RLock
from typing import Any, Callable, Literal

from pydantic import ConfigDict, Field, model_validator

from .canonical import canonical_json_bytes
from .models import FrozenExitSpecModel, SHA256_PATTERN
from .zoom_rtms_decoder import ZoomNormalizedTranscriptSegment


ZOOM_SESSION_RUNTIME_VERSION = "exitspec.zoom-session-runtime/1.0"
ZOOM_SESSION_AUTHORITY = "UNTRUSTED_SOURCE_ONLY"
ZOOM_SESSION_REVIEW_STATE = "NEEDS_REVIEW"
MAX_SESSION_SEGMENTS = 256
MAX_DUPLICATE_DELIVERIES = 4096

_SESSION_ID_PATTERN = r"^zoomsess_[a-f0-9]{64}$"
_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,199}$")
_SESSION_DOMAIN = b"exitspec-zoom-session-v1\x00"
_OPERATION_DOMAIN = b"exitspec-zoom-session-operation-v1\x00"
_SEGMENT_DOMAIN = b"exitspec-zoom-session-segment-v1\x00"
_PROCESSING_DOMAIN = b"exitspec-zoom-session-processing-v1\x00"


class ZoomSessionState(str, Enum):
    """States for one bounded RTMS meeting session."""

    STARTING = "STARTING"
    LISTENING = "LISTENING"
    INTERRUPTED = "INTERRUPTED"
    RECONNECTING = "RECONNECTING"
    PROCESSING = "PROCESSING"
    DRAFT_READY = "DRAFT_READY"
    FAILED = "FAILED"


class ZoomSessionNextAction(str, Enum):
    """Content-free next action for a local operator or worker."""

    WAIT_FOR_START = "WAIT_FOR_START"
    WAIT_FOR_STOP = "WAIT_FOR_STOP"
    RECONNECT_STREAM = "RECONNECT_STREAM"
    PROCESS_TRANSCRIPT = "PROCESS_TRANSCRIPT"
    REVIEW_DRAFT = "REVIEW_DRAFT"
    RECOVER_OR_RESTART = "RECOVER_OR_RESTART"


class ZoomSessionFailureCode(str, Enum):
    """Stable, content-free lifecycle refusal classes."""

    INVALID_REQUEST = "ZOOM_SESSION_INVALID_REQUEST"
    INVALID_TRANSITION = "ZOOM_SESSION_INVALID_TRANSITION"
    IDEMPOTENCY_CONFLICT = "ZOOM_SESSION_IDEMPOTENCY_CONFLICT"
    DUPLICATE_CONFLICT = "ZOOM_SESSION_DUPLICATE_CONFLICT"
    NO_TRANSCRIPT = "ZOOM_SESSION_NO_TRANSCRIPT"
    RECONNECT_FAILED = "ZOOM_SESSION_RECONNECT_FAILED"
    TIMEOUT = "ZOOM_SESSION_TIMEOUT"
    PROCESSING_FAILED = "ZOOM_SESSION_PROCESSING_FAILED"
    PROCESSING_RESULT_CONFLICT = "ZOOM_SESSION_PROCESSING_RESULT_CONFLICT"


_FAILURE_DETAILS: dict[ZoomSessionFailureCode, tuple[str, str]] = {
    ZoomSessionFailureCode.INVALID_REQUEST: (
        "The Zoom session request was not accepted.",
        "review_the_zoom_session_request",
    ),
    ZoomSessionFailureCode.INVALID_TRANSITION: (
        "The Zoom session action does not match its current state.",
        "review_the_zoom_session_state",
    ),
    ZoomSessionFailureCode.IDEMPOTENCY_CONFLICT: (
        "The idempotency key is bound to a different Zoom session action.",
        "use_a_new_idempotency_key",
    ),
    ZoomSessionFailureCode.DUPLICATE_CONFLICT: (
        "The Zoom session received conflicting packet identity.",
        "stop_and_review_the_zoom_session",
    ),
    ZoomSessionFailureCode.NO_TRANSCRIPT: (
        "The Zoom session stopped without a transcript segment.",
        "review_the_capture_and_restart_safely",
    ),
    ZoomSessionFailureCode.RECONNECT_FAILED: (
        "The Zoom session could not reconnect safely.",
        "review_the_capture_and_restart_safely",
    ),
    ZoomSessionFailureCode.TIMEOUT: (
        "The Zoom session exceeded its bounded lifecycle window.",
        "review_the_capture_and_restart_safely",
    ),
    ZoomSessionFailureCode.PROCESSING_FAILED: (
        "The normalized Zoom transcript could not be processed.",
        "review_the_transcript_processing_failure",
    ),
    ZoomSessionFailureCode.PROCESSING_RESULT_CONFLICT: (
        "The Zoom session received a conflicting processing result.",
        "review_the_processing_attempt",
    ),
}


class ZoomSessionError(RuntimeError):
    """Sanitized refusal that never includes transcript or provider values."""

    retryable = False

    def __init__(self, failure_code: ZoomSessionFailureCode) -> None:
        self.failure_code = ZoomSessionFailureCode(failure_code)
        self.code = self.failure_code.value
        message, next_action = _FAILURE_DETAILS[self.failure_code]
        self.next_action = next_action
        super().__init__(message)


class _FrozenZoomSessionModel(FrozenExitSpecModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
        validate_default=True,
    )


class ZoomSessionSnapshot(_FrozenZoomSessionModel):
    """Content-free projection of one session state."""

    schema_version: Literal[ZOOM_SESSION_RUNTIME_VERSION] = (
        ZOOM_SESSION_RUNTIME_VERSION
    )
    session_id: str = Field(pattern=_SESSION_ID_PATTERN)
    state: ZoomSessionState
    next_action: ZoomSessionNextAction
    segment_count: int = Field(ge=0, le=MAX_SESSION_SEGMENTS)
    duplicate_delivery_count: int = Field(ge=0, le=MAX_DUPLICATE_DELIVERIES)
    reconnect_count: int = Field(ge=0, le=64)
    stop_received: bool
    finalization_count: Literal[0, 1] = 0
    processing_input_sha256: str | None = Field(
        default=None,
        pattern=SHA256_PATTERN,
    )
    downstream_result_sha256: str | None = Field(
        default=None,
        pattern=SHA256_PATTERN,
    )
    failure_code: ZoomSessionFailureCode | None = None
    created_at: datetime
    updated_at: datetime
    transcript_authority: Literal[ZOOM_SESSION_AUTHORITY] = (
        ZOOM_SESSION_AUTHORITY
    )
    review_state: Literal[ZOOM_SESSION_REVIEW_STATE] = ZOOM_SESSION_REVIEW_STATE
    may_confirm_contract: Literal[False] = False
    may_freeze_contract: Literal[False] = False
    may_start_measurement: Literal[False] = False
    may_assign_verdict: Literal[False] = False

    @model_validator(mode="after")
    def require_state_projection(self) -> "ZoomSessionSnapshot":
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot precede created_at.")
        expected_action = {
            ZoomSessionState.STARTING: ZoomSessionNextAction.WAIT_FOR_START,
            ZoomSessionState.LISTENING: ZoomSessionNextAction.WAIT_FOR_STOP,
            ZoomSessionState.INTERRUPTED: ZoomSessionNextAction.RECONNECT_STREAM,
            ZoomSessionState.RECONNECTING: ZoomSessionNextAction.RECONNECT_STREAM,
            ZoomSessionState.PROCESSING: ZoomSessionNextAction.PROCESS_TRANSCRIPT,
            ZoomSessionState.DRAFT_READY: ZoomSessionNextAction.REVIEW_DRAFT,
            ZoomSessionState.FAILED: ZoomSessionNextAction.RECOVER_OR_RESTART,
        }[self.state]
        if self.next_action is not expected_action:
            raise ValueError("next_action does not match session state.")
        if self.state in {
            ZoomSessionState.PROCESSING,
            ZoomSessionState.DRAFT_READY,
        }:
            if (
                not self.stop_received
                or self.finalization_count != 1
                or self.segment_count < 1
                or self.processing_input_sha256 is None
                or self.failure_code is not None
            ):
                raise ValueError("processing state projection is contradictory.")
        if self.state is ZoomSessionState.DRAFT_READY:
            if self.downstream_result_sha256 is None:
                raise ValueError("draft-ready state requires a result digest.")
        elif self.downstream_result_sha256 is not None:
            raise ValueError("only draft-ready may expose a result digest.")
        if self.state is ZoomSessionState.FAILED and self.failure_code is None:
            raise ValueError("failed state requires a failure code.")
        if self.state is not ZoomSessionState.FAILED and self.failure_code is not None:
            raise ValueError("non-failed state cannot expose a failure code.")
        if self.finalization_count == 1 and not self.stop_received:
            raise ValueError("finalization requires a stop event.")
        return self


class ZoomSessionActionResult(_FrozenZoomSessionModel):
    """One lifecycle mutation result with exact replay semantics."""

    session: ZoomSessionSnapshot
    idempotent_replay: bool
    duplicate_suppressed: bool = False


@dataclass(frozen=True, slots=True, repr=False)
class ZoomSessionProcessingInput:
    """Private, ordered transcript input released only to the bridge."""

    session_id: str
    transcript_sha256: str
    segments: tuple[ZoomNormalizedTranscriptSegment, ...]

    def __repr__(self) -> str:
        return "ZoomSessionProcessingInput(<private>)"

    def segments_for_bridge(self) -> tuple[ZoomNormalizedTranscriptSegment, ...]:
        return self.segments

    def model_dump(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        raise ZoomSessionError(ZoomSessionFailureCode.INVALID_REQUEST)

    model_dump_json = model_dump


@dataclass(frozen=True, slots=True, repr=False)
class _OperationRecord:
    action: str
    request_sha256: str
    result: ZoomSessionActionResult

    def __repr__(self) -> str:
        return "_OperationRecord(<private>)"


@dataclass(frozen=True, slots=True, repr=False)
class ZoomSessionCheckpoint:
    """Private local checkpoint used for bounded crash recovery."""

    session_id: str
    state: ZoomSessionState
    created_at: datetime
    updated_at: datetime
    segments: tuple[ZoomNormalizedTranscriptSegment, ...]
    duplicate_delivery_count: int
    reconnect_count: int
    stop_received: bool
    finalization_count: int
    processing_input_sha256: str | None
    downstream_result_sha256: str | None
    failure_code: ZoomSessionFailureCode | None
    operations: tuple[tuple[str, _OperationRecord], ...]

    def __repr__(self) -> str:
        return "ZoomSessionCheckpoint(<private>)"

    def model_dump(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        raise ZoomSessionError(ZoomSessionFailureCode.INVALID_REQUEST)

    model_dump_json = model_dump


@dataclass(slots=True, repr=False)
class _SessionRecord:
    session_id: str
    created_at: datetime
    updated_at: datetime
    state: ZoomSessionState = ZoomSessionState.STARTING
    segments_by_id: dict[str, ZoomNormalizedTranscriptSegment] | None = None
    packet_to_segment_id: dict[str, str] | None = None
    duplicate_delivery_count: int = 0
    reconnect_count: int = 0
    stop_received: bool = False
    finalization_count: int = 0
    processing_input_sha256: str | None = None
    downstream_result_sha256: str | None = None
    failure_code: ZoomSessionFailureCode | None = None
    operations: dict[str, _OperationRecord] | None = None

    def __post_init__(self) -> None:
        if self.segments_by_id is None:
            self.segments_by_id = {}
        if self.packet_to_segment_id is None:
            self.packet_to_segment_id = {}
        if self.operations is None:
            self.operations = {}

    def __repr__(self) -> str:
        return "_SessionRecord(session_id={!r}, state={!r}, private_material=<redacted>)".format(
            self.session_id,
            self.state.value,
        )


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _read_clock(clock: Callable[[], datetime]) -> datetime:
    try:
        value = clock()
    except Exception:
        raise ZoomSessionError(ZoomSessionFailureCode.INVALID_REQUEST) from None
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ZoomSessionError(ZoomSessionFailureCode.INVALID_REQUEST)
    return value.astimezone(timezone.utc)


def _digest(domain: bytes, payload: Any) -> str:
    return hashlib.sha256(domain + canonical_json_bytes(payload)).hexdigest()


def _require_session_id(value: object) -> str:
    if type(value) is not str or re.fullmatch(_SESSION_ID_PATTERN, value) is None:
        raise ZoomSessionError(ZoomSessionFailureCode.INVALID_REQUEST)
    return value


def _idempotency_sha256(value: object) -> str:
    if type(value) is not str or _IDEMPOTENCY_KEY.fullmatch(value) is None:
        raise ZoomSessionError(ZoomSessionFailureCode.INVALID_REQUEST)
    return _digest(_OPERATION_DOMAIN, {"idempotency_key": value})


def _request_sha256(action: str, payload: Mapping[str, Any]) -> str:
    return _digest(
        _OPERATION_DOMAIN,
        {"action": action, "payload": dict(payload)},
    )


def _segment_fingerprint(segment: ZoomNormalizedTranscriptSegment) -> str:
    return _digest(_SEGMENT_DOMAIN, segment.model_dump(mode="json"))


def _segment_content_fingerprint(segment: ZoomNormalizedTranscriptSegment) -> str:
    payload = segment.model_dump(mode="json")
    payload.pop("segment_id", None)
    payload.pop("arrival_index", None)
    return _digest(_SEGMENT_DOMAIN, payload)


def _ordered_segments(
    record: _SessionRecord,
) -> tuple[ZoomNormalizedTranscriptSegment, ...]:
    assert record.segments_by_id is not None
    return tuple(
        sorted(
            record.segments_by_id.values(),
            key=lambda segment: (
                segment.provider_timestamp_millisecond,
                segment.start_time_millisecond,
                segment.end_time_millisecond,
                segment.arrival_index,
                segment.segment_id,
            ),
        )
    )


def _processing_input(record: _SessionRecord) -> ZoomSessionProcessingInput:
    segments = _ordered_segments(record)
    transcript_sha256 = _digest(
        _PROCESSING_DOMAIN,
        {
            "session_id": record.session_id,
            "segments": [segment.model_dump(mode="json") for segment in segments],
        },
    )
    return ZoomSessionProcessingInput(
        session_id=record.session_id,
        transcript_sha256=transcript_sha256,
        segments=segments,
    )


class ZoomSessionStateMachine:
    """Thread-safe, local state machine for one bounded Zoom session."""

    def __init__(
        self,
        *,
        session_id: object,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        validated_session_id = _require_session_id(session_id)
        if not callable(clock):
            raise TypeError("clock must be callable.")
        now = _read_clock(clock)
        self._clock = clock
        self._lock = RLock()
        self._record = _SessionRecord(
            session_id=validated_session_id,
            created_at=now,
            updated_at=now,
        )

    @classmethod
    def recover(
        cls,
        checkpoint: ZoomSessionCheckpoint,
        *,
        clock: Callable[[], datetime] = _utc_now,
    ) -> "ZoomSessionStateMachine":
        if type(checkpoint) is not ZoomSessionCheckpoint:
            raise ZoomSessionError(ZoomSessionFailureCode.INVALID_REQUEST)
        runtime = cls(session_id=checkpoint.session_id, clock=clock)
        if checkpoint.updated_at < checkpoint.created_at:
            raise ZoomSessionError(ZoomSessionFailureCode.INVALID_REQUEST)
        if len(checkpoint.segments) > MAX_SESSION_SEGMENTS:
            raise ZoomSessionError(ZoomSessionFailureCode.INVALID_REQUEST)
        record = _SessionRecord(
            session_id=checkpoint.session_id,
            created_at=checkpoint.created_at,
            updated_at=checkpoint.updated_at,
            state=checkpoint.state,
            duplicate_delivery_count=checkpoint.duplicate_delivery_count,
            reconnect_count=checkpoint.reconnect_count,
            stop_received=checkpoint.stop_received,
            finalization_count=checkpoint.finalization_count,
            processing_input_sha256=checkpoint.processing_input_sha256,
            downstream_result_sha256=checkpoint.downstream_result_sha256,
            failure_code=checkpoint.failure_code,
            operations=dict(checkpoint.operations),
        )
        assert record.segments_by_id is not None
        assert record.packet_to_segment_id is not None
        for segment in checkpoint.segments:
            if type(segment) is not ZoomNormalizedTranscriptSegment:
                raise ZoomSessionError(ZoomSessionFailureCode.INVALID_REQUEST)
            prior_id = record.packet_to_segment_id.get(segment.packet_sha256)
            if prior_id is not None and prior_id != segment.segment_id:
                raise ZoomSessionError(ZoomSessionFailureCode.DUPLICATE_CONFLICT)
            record.segments_by_id[segment.segment_id] = segment
            record.packet_to_segment_id[segment.packet_sha256] = segment.segment_id
        runtime._record = record
        runtime._validate_recovered_record()
        return runtime

    def snapshot(self) -> ZoomSessionSnapshot:
        with self._lock:
            return self._snapshot_locked()

    def checkpoint(self) -> ZoomSessionCheckpoint:
        with self._lock:
            record = self._record
            assert record.segments_by_id is not None
            assert record.operations is not None
            return ZoomSessionCheckpoint(
                session_id=record.session_id,
                state=record.state,
                created_at=record.created_at,
                updated_at=record.updated_at,
                segments=_ordered_segments(record),
                duplicate_delivery_count=record.duplicate_delivery_count,
                reconnect_count=record.reconnect_count,
                stop_received=record.stop_received,
                finalization_count=record.finalization_count,
                processing_input_sha256=record.processing_input_sha256,
                downstream_result_sha256=record.downstream_result_sha256,
                failure_code=record.failure_code,
                operations=tuple(record.operations.items()),
            )

    def mark_listening(self, *, idempotency_key: object) -> ZoomSessionActionResult:
        return self._mutate(
            "STARTED",
            {"session_id": self._record.session_id},
            idempotency_key,
            self._mark_listening,
        )

    def append_transcript(
        self,
        segment: object,
        *,
        idempotency_key: object,
    ) -> ZoomSessionActionResult:
        if type(segment) is not ZoomNormalizedTranscriptSegment:
            raise ZoomSessionError(ZoomSessionFailureCode.INVALID_REQUEST)
        fingerprint = _segment_fingerprint(segment)
        return self._mutate(
            "TRANSCRIPT",
            {
                "segment_fingerprint_sha256": fingerprint,
                "session_id": self._record.session_id,
            },
            idempotency_key,
            lambda: self._append_transcript(segment),
        )

    def interrupt(self, *, idempotency_key: object) -> ZoomSessionActionResult:
        return self._mutate(
            "INTERRUPTED",
            {"session_id": self._record.session_id},
            idempotency_key,
            self._interrupt,
        )

    def begin_reconnect(
        self,
        *,
        idempotency_key: object,
    ) -> ZoomSessionActionResult:
        return self._mutate(
            "RECONNECTING",
            {"session_id": self._record.session_id},
            idempotency_key,
            self._begin_reconnect,
        )

    def mark_reconnected(
        self,
        *,
        idempotency_key: object,
    ) -> ZoomSessionActionResult:
        return self._mutate(
            "RECONNECTED",
            {"session_id": self._record.session_id},
            idempotency_key,
            self._mark_reconnected,
        )

    def reconnect_failed(
        self,
        *,
        idempotency_key: object,
    ) -> ZoomSessionActionResult:
        return self._mutate(
            "RECONNECT_FAILED",
            {"session_id": self._record.session_id},
            idempotency_key,
            self._reconnect_failed,
        )

    def stop(self, *, idempotency_key: object) -> ZoomSessionActionResult:
        return self._mutate(
            "STOPPED",
            {"session_id": self._record.session_id},
            idempotency_key,
            self._stop,
        )

    def timeout(self, *, idempotency_key: object) -> ZoomSessionActionResult:
        return self._mutate(
            "TIMEOUT",
            {"session_id": self._record.session_id},
            idempotency_key,
            lambda: self._fail(ZoomSessionFailureCode.TIMEOUT),
        )

    def processing_input(self) -> ZoomSessionProcessingInput:
        with self._lock:
            if (
                self._record.state
                not in {ZoomSessionState.PROCESSING, ZoomSessionState.DRAFT_READY}
                or self._record.processing_input_sha256 is None
                or not self._record.stop_received
            ):
                raise ZoomSessionError(ZoomSessionFailureCode.INVALID_TRANSITION)
            result = _processing_input(self._record)
            if result.transcript_sha256 != self._record.processing_input_sha256:
                raise ZoomSessionError(ZoomSessionFailureCode.DUPLICATE_CONFLICT)
            return result

    def processing_succeeded(
        self,
        *,
        result_sha256: object,
        idempotency_key: object,
    ) -> ZoomSessionActionResult:
        if type(result_sha256) is not str or re.fullmatch(SHA256_PATTERN, result_sha256) is None:
            raise ZoomSessionError(ZoomSessionFailureCode.INVALID_REQUEST)
        return self._mutate(
            "PROCESSING_SUCCEEDED",
            {
                "result_sha256": result_sha256,
                "session_id": self._record.session_id,
            },
            idempotency_key,
            lambda: self._processing_succeeded(result_sha256),
        )

    def processing_failed(
        self,
        *,
        idempotency_key: object,
    ) -> ZoomSessionActionResult:
        return self._mutate(
            "PROCESSING_FAILED",
            {"session_id": self._record.session_id},
            idempotency_key,
            self._processing_failed,
        )

    def _mutate(
        self,
        action: str,
        payload: Mapping[str, Any],
        idempotency_key: object,
        mutation: Callable[[], bool],
    ) -> ZoomSessionActionResult:
        key_sha256 = _idempotency_sha256(idempotency_key)
        request_sha256 = _request_sha256(action, payload)
        with self._lock:
            assert self._record.operations is not None
            prior = self._record.operations.get(key_sha256)
            if prior is not None:
                if prior.action != action or prior.request_sha256 != request_sha256:
                    raise ZoomSessionError(ZoomSessionFailureCode.IDEMPOTENCY_CONFLICT)
                return prior.result.model_copy(update={"idempotent_replay": True})
            duplicate_suppressed = mutation()
            result = ZoomSessionActionResult(
                session=self._snapshot_locked(),
                idempotent_replay=False,
                duplicate_suppressed=duplicate_suppressed,
            )
            self._record.operations[key_sha256] = _OperationRecord(
                action=action,
                request_sha256=request_sha256,
                result=result,
            )
            return result

    def _mark_listening(self) -> bool:
        if self._record.state is ZoomSessionState.LISTENING:
            return True
        self._require_state({ZoomSessionState.STARTING})
        self._touch()
        self._record.state = ZoomSessionState.LISTENING
        return False

    def _append_transcript(
        self,
        segment: ZoomNormalizedTranscriptSegment,
    ) -> bool:
        assert self._record.segments_by_id is not None
        assert self._record.packet_to_segment_id is not None
        prior_segment_id = self._record.packet_to_segment_id.get(segment.packet_sha256)
        if prior_segment_id is not None:
            prior_segment = self._record.segments_by_id.get(prior_segment_id)
            if (
                prior_segment is None
                or _segment_content_fingerprint(prior_segment)
                != _segment_content_fingerprint(segment)
            ):
                raise ZoomSessionError(ZoomSessionFailureCode.DUPLICATE_CONFLICT)
            if self._record.stop_received or self._record.state is ZoomSessionState.FAILED:
                return True
            self._require_state(
                {
                    ZoomSessionState.LISTENING,
                    ZoomSessionState.INTERRUPTED,
                    ZoomSessionState.RECONNECTING,
                }
            )
            self._record.duplicate_delivery_count += 1
            self._touch()
            return True
        self._require_state(
            {
                ZoomSessionState.LISTENING,
                ZoomSessionState.INTERRUPTED,
                ZoomSessionState.RECONNECTING,
            }
        )
        if segment.segment_id in self._record.segments_by_id:
            raise ZoomSessionError(ZoomSessionFailureCode.DUPLICATE_CONFLICT)
        if len(self._record.segments_by_id) >= MAX_SESSION_SEGMENTS:
            raise ZoomSessionError(ZoomSessionFailureCode.INVALID_TRANSITION)
        self._record.segments_by_id[segment.segment_id] = segment
        self._record.packet_to_segment_id[segment.packet_sha256] = segment.segment_id
        self._touch()
        return False

    def _interrupt(self) -> bool:
        if self._record.state in {
            ZoomSessionState.INTERRUPTED,
            ZoomSessionState.RECONNECTING,
        }:
            return True
        self._require_state({ZoomSessionState.LISTENING})
        self._touch()
        self._record.state = ZoomSessionState.INTERRUPTED
        return False

    def _begin_reconnect(self) -> bool:
        if self._record.state is ZoomSessionState.RECONNECTING:
            return True
        self._require_state({ZoomSessionState.INTERRUPTED})
        self._touch()
        self._record.state = ZoomSessionState.RECONNECTING
        return False

    def _mark_reconnected(self) -> bool:
        if self._record.state is ZoomSessionState.LISTENING:
            return True
        self._require_state({ZoomSessionState.RECONNECTING})
        self._touch()
        self._record.reconnect_count += 1
        self._record.state = ZoomSessionState.LISTENING
        return False

    def _reconnect_failed(self) -> bool:
        if self._record.state is ZoomSessionState.FAILED:
            return True
        self._require_state(
            {ZoomSessionState.INTERRUPTED, ZoomSessionState.RECONNECTING}
        )
        return self._fail(ZoomSessionFailureCode.RECONNECT_FAILED)

    def _stop(self) -> bool:
        if self._record.stop_received or self._record.state is ZoomSessionState.FAILED:
            return True
        self._require_state(
            {
                ZoomSessionState.LISTENING,
                ZoomSessionState.INTERRUPTED,
                ZoomSessionState.RECONNECTING,
            }
        )
        self._touch()
        self._record.stop_received = True
        self._record.finalization_count = 1
        if not self._record.segments_by_id:
            self._record.state = ZoomSessionState.FAILED
            self._record.failure_code = ZoomSessionFailureCode.NO_TRANSCRIPT
            return False
        self._record.processing_input_sha256 = _processing_input(
            self._record
        ).transcript_sha256
        self._record.state = ZoomSessionState.PROCESSING
        return False

    def _processing_succeeded(self, result_sha256: str) -> bool:
        if self._record.state is ZoomSessionState.DRAFT_READY:
            if self._record.downstream_result_sha256 != result_sha256:
                raise ZoomSessionError(
                    ZoomSessionFailureCode.PROCESSING_RESULT_CONFLICT
                )
            return True
        self._require_state({ZoomSessionState.PROCESSING})
        self.processing_input()
        self._touch()
        self._record.downstream_result_sha256 = result_sha256
        self._record.state = ZoomSessionState.DRAFT_READY
        return False

    def _processing_failed(self) -> bool:
        if self._record.state is ZoomSessionState.DRAFT_READY:
            return True
        self._require_state({ZoomSessionState.PROCESSING})
        return self._fail(ZoomSessionFailureCode.PROCESSING_FAILED)

    def _fail(self, failure_code: ZoomSessionFailureCode) -> bool:
        if self._record.state is ZoomSessionState.FAILED:
            return True
        if self._record.state is ZoomSessionState.DRAFT_READY:
            return True
        self._touch()
        self._record.state = ZoomSessionState.FAILED
        self._record.failure_code = failure_code
        return False

    def _require_state(self, states: set[ZoomSessionState]) -> None:
        if self._record.state not in states:
            raise ZoomSessionError(ZoomSessionFailureCode.INVALID_TRANSITION)

    def _touch(self) -> None:
        now = _read_clock(self._clock)
        if now < self._record.updated_at:
            raise ZoomSessionError(ZoomSessionFailureCode.INVALID_REQUEST)
        self._record.updated_at = now

    def _snapshot_locked(self) -> ZoomSessionSnapshot:
        assert self._record.segments_by_id is not None
        expected_action = {
            ZoomSessionState.STARTING: ZoomSessionNextAction.WAIT_FOR_START,
            ZoomSessionState.LISTENING: ZoomSessionNextAction.WAIT_FOR_STOP,
            ZoomSessionState.INTERRUPTED: ZoomSessionNextAction.RECONNECT_STREAM,
            ZoomSessionState.RECONNECTING: ZoomSessionNextAction.RECONNECT_STREAM,
            ZoomSessionState.PROCESSING: ZoomSessionNextAction.PROCESS_TRANSCRIPT,
            ZoomSessionState.DRAFT_READY: ZoomSessionNextAction.REVIEW_DRAFT,
            ZoomSessionState.FAILED: ZoomSessionNextAction.RECOVER_OR_RESTART,
        }[self._record.state]
        return ZoomSessionSnapshot(
            session_id=self._record.session_id,
            state=self._record.state,
            next_action=expected_action,
            segment_count=len(self._record.segments_by_id),
            duplicate_delivery_count=self._record.duplicate_delivery_count,
            reconnect_count=self._record.reconnect_count,
            stop_received=self._record.stop_received,
            finalization_count=self._record.finalization_count,
            processing_input_sha256=self._record.processing_input_sha256,
            downstream_result_sha256=self._record.downstream_result_sha256,
            failure_code=self._record.failure_code,
            created_at=self._record.created_at,
            updated_at=self._record.updated_at,
        )

    def _validate_recovered_record(self) -> None:
        snapshot = self._snapshot_locked()
        if self._record.processing_input_sha256 is not None:
            expected = _processing_input(self._record).transcript_sha256
            if expected != self._record.processing_input_sha256:
                raise ZoomSessionError(ZoomSessionFailureCode.DUPLICATE_CONFLICT)
        if self._record.operations is None:
            raise ZoomSessionError(ZoomSessionFailureCode.INVALID_REQUEST)
        for key_sha256, operation in self._record.operations.items():
            if (
                not re.fullmatch(SHA256_PATTERN, key_sha256)
                or type(operation) is not _OperationRecord
                or operation.result.session.session_id != snapshot.session_id
            ):
                raise ZoomSessionError(ZoomSessionFailureCode.INVALID_REQUEST)


def new_zoom_session_id(seed: object) -> str:
    """Derive a stable session ID from a server-owned, non-secret seed."""

    if type(seed) is not str or not 1 <= len(seed) <= 256:
        raise ZoomSessionError(ZoomSessionFailureCode.INVALID_REQUEST)
    return "zoomsess_" + _digest(_SESSION_DOMAIN, {"seed": seed})


__all__ = [
    "MAX_SESSION_SEGMENTS",
    "ZOOM_SESSION_AUTHORITY",
    "ZOOM_SESSION_REVIEW_STATE",
    "ZOOM_SESSION_RUNTIME_VERSION",
    "ZoomSessionActionResult",
    "ZoomSessionCheckpoint",
    "ZoomSessionError",
    "ZoomSessionFailureCode",
    "ZoomSessionNextAction",
    "ZoomSessionProcessingInput",
    "ZoomSessionSnapshot",
    "ZoomSessionState",
    "ZoomSessionStateMachine",
    "new_zoom_session_id",
]
