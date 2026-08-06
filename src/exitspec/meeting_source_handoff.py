"""Fail-closed handoff from one sealed meeting window to one MEETING source.

The handoff accepts only an unchanged in-process result minted by the meeting
sealer. It neutralizes provider labels, immediately redacts private transcript
text, and reuses the existing process-local source intake. Every resulting
candidate remains source-linked ``NEEDS_REVIEW`` input with no agreement,
measurement, evidence, or verdict authority.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
import hashlib
from typing import Any, Callable, Literal, Mapping, Self

from pydantic import ConfigDict, Field, field_validator, model_validator

from .intake import redact_and_parse_pasted_transcript
from .meeting_connector import (
    MEETING_CONNECTOR_REVIEW_STATE,
    MeetingEventKind,
    MeetingTranscriptEvent,
    MeetingTranscriptWindowReceipt,
    SealedMeetingTranscript,
    meeting_identity_sha256,
    stream_identity_sha256,
)
from .models import FrozenExitSpecModel, SHA256_PATTERN
from .poc_creation import POC_ID_PATTERN
from .poc_source_intake import (
    MEETING_INPUT_LIMIT,
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
    SourceKind,
)


MEETING_SOURCE_HANDOFF_VERSION = "exitspec-meeting-source-handoff/1.0"
MEETING_SOURCE_HANDOFF_AUTHORITY = "UNTRUSTED_SOURCE_ONLY"
MEETING_SOURCE_HANDOFF_INBOX_AUTHORITY = "UNCHANGED"

_IDEMPOTENCY_PREFIX = "meeting-source-handoff:"
_SOURCE_RECEIPT_ID_PATTERN = r"^srcpt_[a-z0-9][a-z0-9_-]{7,95}$"


class MeetingSourceHandoffFailureCode(str, Enum):
    """Stable content-free failures for sealed transcript handoff."""

    INVALID_TRANSCRIPT = "MEETING_HANDOFF_INVALID_TRANSCRIPT"
    BINDING_MISMATCH = "MEETING_HANDOFF_BINDING_MISMATCH"
    REDACTION_FAILED = "MEETING_HANDOFF_REDACTION_FAILED"
    SOURCE_UNAVAILABLE = "MEETING_HANDOFF_SOURCE_UNAVAILABLE"
    SOURCE_CONFLICT = "MEETING_HANDOFF_SOURCE_CONFLICT"
    CAPACITY_EXCEEDED = "MEETING_HANDOFF_CAPACITY_EXCEEDED"
    INTERNAL = "MEETING_HANDOFF_INTERNAL"


_FAILURE_DETAILS: dict[
    MeetingSourceHandoffFailureCode,
    tuple[str, str],
] = {
    MeetingSourceHandoffFailureCode.INVALID_TRANSCRIPT: (
        "The sealed meeting transcript was not accepted for source handoff.",
        "seal_the_meeting_transcript_again",
    ),
    MeetingSourceHandoffFailureCode.BINDING_MISMATCH: (
        "The sealed meeting transcript does not match its receipt.",
        "review_meeting_handoff_bindings",
    ),
    MeetingSourceHandoffFailureCode.REDACTION_FAILED: (
        "The meeting transcript was blocked before source attachment.",
        "review_meeting_transcript_redaction",
    ),
    MeetingSourceHandoffFailureCode.SOURCE_UNAVAILABLE: (
        "The draft POC cannot accept the meeting source.",
        "restore_the_draft_poc",
    ),
    MeetingSourceHandoffFailureCode.SOURCE_CONFLICT: (
        "The meeting source conflicts with the sealed transcript identity.",
        "review_the_existing_meeting_source",
    ),
    MeetingSourceHandoffFailureCode.CAPACITY_EXCEEDED: (
        "The process-local meeting source store is at capacity.",
        "restart_the_local_runtime_safely",
    ),
    MeetingSourceHandoffFailureCode.INTERNAL: (
        "The meeting source handoff could not complete safely.",
        "review_the_meeting_source_handoff",
    ),
}


class MeetingSourceHandoffError(RuntimeError):
    """Sanitized refusal that reflects no transcript or participant content."""

    retryable = False

    def __init__(self, failure_code: MeetingSourceHandoffFailureCode) -> None:
        self.failure_code = MeetingSourceHandoffFailureCode(failure_code)
        self.code = self.failure_code.value
        safe_message, next_action = _FAILURE_DETAILS[self.failure_code]
        self.next_action = next_action
        super().__init__(safe_message)


class _FrozenHandoffModel(FrozenExitSpecModel):
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


class MeetingSourceHandoffReceipt(_FrozenHandoffModel):
    """Content-free provenance for one review-only source attachment."""

    schema_version: Literal[MEETING_SOURCE_HANDOFF_VERSION] = (
        MEETING_SOURCE_HANDOFF_VERSION
    )
    source_receipt_id: str = Field(pattern=_SOURCE_RECEIPT_ID_PATTERN)
    transcript_window_receipt: MeetingTranscriptWindowReceipt
    redacted_transcript_sha256: str = Field(pattern=SHA256_PATTERN)
    redacted_character_count: int = Field(gt=0, le=MEETING_INPUT_LIMIT)
    segment_count: int = Field(gt=0, le=20_000)
    redaction_policy_version: str = Field(
        pattern=r"^[a-z][a-z0-9._:/-]{1,127}$"
    )
    completed_at: datetime
    source_kind: Literal[SourceKind.MEETING] = SourceKind.MEETING
    transcript_authority: Literal[MEETING_SOURCE_HANDOFF_AUTHORITY] = (
        MEETING_SOURCE_HANDOFF_AUTHORITY
    )
    review_state: Literal[MEETING_CONNECTOR_REVIEW_STATE] = (
        MEETING_CONNECTOR_REVIEW_STATE
    )
    speaker_labels_neutralized: Literal[True] = True
    raw_audio_received: Literal[False] = False
    raw_transcript_retained_by_handoff: Literal[False] = False
    inbox_retention_authority: Literal[
        MEETING_SOURCE_HANDOFF_INBOX_AUTHORITY
    ] = MEETING_SOURCE_HANDOFF_INBOX_AUTHORITY
    may_delete_private_inbox_payloads: Literal[False] = False
    may_confirm_contract: Literal[False] = False
    may_freeze_contract: Literal[False] = False
    may_start_measurement: Literal[False] = False
    may_assign_verdict: Literal[False] = False
    synthetic_only: Literal[True] = True

    @field_validator("completed_at")
    @classmethod
    def normalize_time(cls, value: datetime) -> datetime:
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise ValueError("completed_at must be timezone-aware.")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def require_window_binding(self) -> "MeetingSourceHandoffReceipt":
        window = self.transcript_window_receipt
        if (
            self.segment_count != window.segment_count
            or self.completed_at < window.stopped_at
            or self.transcript_authority != window.transcript_authority
            or self.review_state != window.review_state
        ):
            raise ValueError("Meeting handoff receipt is not window-bound.")
        return self


class MeetingSourceHandoffResult(_FrozenHandoffModel):
    """One source receipt linked to its content-free meeting provenance."""

    schema_version: Literal[MEETING_SOURCE_HANDOFF_VERSION] = (
        MEETING_SOURCE_HANDOFF_VERSION
    )
    source_receipt: POCSourceReceipt
    handoff_receipt: MeetingSourceHandoffReceipt

    @model_validator(mode="after")
    def require_exact_source_link(self) -> "MeetingSourceHandoffResult":
        source = self.source_receipt
        handoff = self.handoff_receipt
        window = handoff.transcript_window_receipt
        if (
            source.poc_id != window.poc_id
            or source.source_kind != SourceKind.MEETING
            or source.source_receipt_id != handoff.source_receipt_id
            or source.status != handoff.review_state
        ):
            raise ValueError("Meeting handoff receipts are not linked.")
        return self


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _sealed_bindings_match(transcript: SealedMeetingTranscript) -> bool:
    try:
        if not transcript._verify_sealer_authority():
            return False
        receipt = transcript.receipt
        segments = transcript.segments
        if (
            receipt.authorization_id != transcript.authorization_id
            or receipt.request_id != transcript.request_id
            or receipt.poc_id != transcript.poc_id
            or receipt.meeting_identity_sha256
            != meeting_identity_sha256(transcript.meeting_id)
            or receipt.stream_identity_sha256
            != stream_identity_sha256(transcript.stream_id)
            or receipt.segment_count != len(segments)
            or receipt.transcript_character_count
            != sum(len(segment.transcript_text or "") for segment in segments)
            or receipt.unique_event_count < len(segments) + 2
        ):
            return False
        sequences = tuple(segment.sequence for segment in segments)
        transport_bindings = {
            segment.transport_binding_id for segment in segments
        }
        return (
            sequences == tuple(sorted(set(sequences)))
            and len(transport_bindings) == 1
            and all(
                type(segment) is MeetingTranscriptEvent
                and segment.kind is MeetingEventKind.TRANSCRIPT_SEGMENT
                and segment.adapter_id == receipt.adapter_id
                and segment.adapter_version == receipt.adapter_version
                and meeting_identity_sha256(segment.meeting_id)
                == receipt.meeting_identity_sha256
                and stream_identity_sha256(segment.stream_id)
                == receipt.stream_identity_sha256
                and receipt.started_at
                <= segment.received_at
                <= receipt.stopped_at
                for segment in segments
            )
        )
    except Exception:
        return False


def _redact_transcript(
    transcript: SealedMeetingTranscript,
) -> tuple[str, str]:
    transient_text: str | None = None
    intake: Any = None
    failed = False
    try:
        transient_text = transcript.transient_redaction_input()
        intake = redact_and_parse_pasted_transcript(
            transient_text,
            transcript_id="source-sealed-meeting",
            title="Sealed meeting transcript",
        )
    except Exception:
        failed = True
    finally:
        transient_text = None

    if failed or intake is None:
        raise MeetingSourceHandoffError(
            MeetingSourceHandoffFailureCode.REDACTION_FAILED
        )
    redacted_text = "\n".join(
        "{0}: {1}".format(line.speaker, line.text)
        for line in intake.transcript.lines
    )
    return redacted_text, intake.redaction.policy_version


class MeetingTranscriptSourceHandoffService:
    """Attach one sealer-minted transcript through the existing source path."""

    __slots__ = ("_clock", "_source_intake")

    def __init__(
        self,
        source_intake: ProcessLocalPOCSourceIntake,
        *,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        if type(source_intake) is not ProcessLocalPOCSourceIntake:
            raise TypeError(
                "source_intake must be a ProcessLocalPOCSourceIntake."
            )
        if not callable(clock):
            raise TypeError("clock must be callable.")
        self._source_intake = source_intake
        self._clock = clock

    def handoff(
        self,
        transcript: SealedMeetingTranscript,
    ) -> MeetingSourceHandoffResult:
        """Neutralize, redact, attach, and publish safe linked receipts."""

        if type(transcript) is not SealedMeetingTranscript:
            raise MeetingSourceHandoffError(
                MeetingSourceHandoffFailureCode.INVALID_TRANSCRIPT
            )
        if not _sealed_bindings_match(transcript):
            raise MeetingSourceHandoffError(
                MeetingSourceHandoffFailureCode.BINDING_MISMATCH
            )

        completed_at: datetime | None = None
        clock_failed = False
        try:
            completed_at = self._clock()
            clock_failed = (
                not isinstance(completed_at, datetime)
                or completed_at.tzinfo is None
                or completed_at.utcoffset() is None
                or completed_at < transcript.receipt.stopped_at
            )
        except Exception:
            clock_failed = True
        if clock_failed or completed_at is None:
            raise MeetingSourceHandoffError(
                MeetingSourceHandoffFailureCode.INTERNAL
            )
        completed_at = completed_at.astimezone(timezone.utc)

        redacted_text, policy_version = _redact_transcript(transcript)
        content_sha256 = hashlib.sha256(
            redacted_text.encode("utf-8")
        ).hexdigest()
        source_receipt: POCSourceReceipt | None = None
        source_failure: MeetingSourceHandoffFailureCode | None = None
        try:
            idempotency_digest = hashlib.sha256(
                (
                    transcript.poc_id
                    + "\x00"
                    + transcript.receipt.stream_identity_sha256
                ).encode("utf-8")
            ).hexdigest()
            source_receipt = (
                self._source_intake.capture_meeting_connector_transcript(
                    poc_id=transcript.poc_id,
                    redacted_transcript_text=redacted_text,
                    expected_content_sha256=content_sha256,
                    stream_identity_sha256=(
                        transcript.receipt.stream_identity_sha256
                    ),
                    idempotency_key=_IDEMPOTENCY_PREFIX + idempotency_digest,
                )
            )
        except (POCSourceDraftArchived, POCSourceDraftUnavailable):
            source_failure = MeetingSourceHandoffFailureCode.SOURCE_UNAVAILABLE
        except (
            POCSourceIdempotencyConflict,
            POCSourceIntakeRevisionRequired,
            POCSourceRevisionRequired,
            POCSourceStaleRevision,
        ):
            source_failure = MeetingSourceHandoffFailureCode.SOURCE_CONFLICT
        except (
            DuplicatePOCSourceId,
            POCSourceCapacityExceeded,
            POCSourceIntakeCapacityExceeded,
        ):
            source_failure = MeetingSourceHandoffFailureCode.CAPACITY_EXCEEDED
        except POCSourceIntakeInvalid:
            source_failure = MeetingSourceHandoffFailureCode.REDACTION_FAILED
        except (POCSourceIntakeError, TypeError, ValueError):
            source_failure = MeetingSourceHandoffFailureCode.INTERNAL
        except Exception:
            source_failure = MeetingSourceHandoffFailureCode.INTERNAL

        redacted_character_count = len(redacted_text)
        redacted_text = ""
        if source_failure is not None or source_receipt is None:
            raise MeetingSourceHandoffError(
                source_failure or MeetingSourceHandoffFailureCode.INTERNAL
            )

        projection: MeetingSourceHandoffResult | None = None
        projection_failed = False
        try:
            handoff_receipt = MeetingSourceHandoffReceipt(
                source_receipt_id=source_receipt.source_receipt_id,
                transcript_window_receipt=transcript.receipt,
                redacted_transcript_sha256=content_sha256,
                redacted_character_count=redacted_character_count,
                segment_count=len(transcript.segments),
                redaction_policy_version=policy_version,
                completed_at=completed_at,
            )
            projection = MeetingSourceHandoffResult(
                source_receipt=source_receipt,
                handoff_receipt=handoff_receipt,
            )
        except Exception:
            projection_failed = True
        if projection_failed or projection is None:
            raise MeetingSourceHandoffError(
                MeetingSourceHandoffFailureCode.INTERNAL
            )
        return projection


__all__ = [
    "MEETING_SOURCE_HANDOFF_AUTHORITY",
    "MEETING_SOURCE_HANDOFF_INBOX_AUTHORITY",
    "MEETING_SOURCE_HANDOFF_VERSION",
    "MeetingSourceHandoffError",
    "MeetingSourceHandoffFailureCode",
    "MeetingSourceHandoffReceipt",
    "MeetingSourceHandoffResult",
    "MeetingTranscriptSourceHandoffService",
]
