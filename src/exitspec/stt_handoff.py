"""Fail-closed handoff from one private STT result to one MEETING source.

The handoff immediately redacts provider transcript text, reuses the existing
process-local source service, and publishes content-free linked receipts.  It
has no contract, approval, execution, evidence, or verdict authority.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Literal, Mapping, Self

from pydantic import ConfigDict, model_validator

from .intake import redact_and_parse_pasted_transcript
from .models import FrozenExitSpecModel
from .poc_source_intake import (
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
from .stt_boundary import (
    STTTranscriptReceipt,
    UntrustedSTTTranscript,
)
from .stt_operation import (
    STTOperationReceipt,
    STTOperationResult,
)


STT_HANDOFF_VERSION = "exitspec-stt-handoff/1.0"
_IDEMPOTENCY_PREFIX = "stt-handoff:"


class STTTranscriptHandoffFailureCode(str, Enum):
    """Stable content-free failures for transcript-to-source handoff."""

    INVALID_RESULT = "STT_HANDOFF_INVALID_RESULT"
    BINDING_MISMATCH = "STT_HANDOFF_BINDING_MISMATCH"
    REDACTION_FAILED = "STT_HANDOFF_REDACTION_FAILED"
    SOURCE_UNAVAILABLE = "STT_HANDOFF_SOURCE_UNAVAILABLE"
    SOURCE_CONFLICT = "STT_HANDOFF_SOURCE_CONFLICT"
    CAPACITY_EXCEEDED = "STT_HANDOFF_CAPACITY_EXCEEDED"
    INTERNAL = "STT_HANDOFF_INTERNAL"


_FAILURE_DETAILS: dict[
    STTTranscriptHandoffFailureCode,
    tuple[str, str],
] = {
    STTTranscriptHandoffFailureCode.INVALID_RESULT: (
        "The speech-to-text result was not accepted for source handoff.",
        "start_a_new_stt_request",
    ),
    STTTranscriptHandoffFailureCode.BINDING_MISMATCH: (
        "The speech-to-text result does not match its operation receipt.",
        "review_stt_operation_bindings",
    ),
    STTTranscriptHandoffFailureCode.REDACTION_FAILED: (
        "The speech-to-text transcript was blocked before source attachment.",
        "review_transcript_redaction",
    ),
    STTTranscriptHandoffFailureCode.SOURCE_UNAVAILABLE: (
        "The draft POC cannot accept the meeting source.",
        "restore_the_draft_poc",
    ),
    STTTranscriptHandoffFailureCode.SOURCE_CONFLICT: (
        "The meeting source conflicts with the existing STT operation.",
        "review_the_existing_meeting_source",
    ),
    STTTranscriptHandoffFailureCode.CAPACITY_EXCEEDED: (
        "The process-local meeting source store is at capacity.",
        "restart_the_local_runtime_safely",
    ),
    STTTranscriptHandoffFailureCode.INTERNAL: (
        "The speech-to-text source handoff could not complete safely.",
        "review_the_stt_handoff",
    ),
}


class STTTranscriptHandoffError(RuntimeError):
    """Sanitized refusal with no transcript or source content."""

    retryable = False

    def __init__(self, failure_code: STTTranscriptHandoffFailureCode) -> None:
        self.failure_code = STTTranscriptHandoffFailureCode(failure_code)
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


class STTTranscriptHandoffResult(_FrozenHandoffModel):
    """Content-free linked projection of a successful source attachment."""

    schema_version: Literal[STT_HANDOFF_VERSION] = STT_HANDOFF_VERSION
    source_receipt: POCSourceReceipt
    transcript_receipt: STTTranscriptReceipt

    @model_validator(mode="after")
    def require_exact_source_link(self) -> "STTTranscriptHandoffResult":
        source = self.source_receipt
        transcript = self.transcript_receipt
        if (
            source.poc_id != transcript.poc_id
            or source.source_kind != SourceKind.MEETING
            or source.source_receipt_id != transcript.source_receipt_id
            or source.status != transcript.review_state
        ):
            raise ValueError("STT handoff receipts are not linked.")
        return self


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _operation_bindings_match(
    receipt: STTOperationReceipt,
    transcript: UntrustedSTTTranscript,
) -> bool:
    return (
        receipt.authorization_id == transcript.authorization_id
        and receipt.request_id == transcript.request_id
        and receipt.poc_id == transcript.poc_id
        and receipt.audio_sha256 == transcript.audio_sha256
        and receipt.duration_ms == transcript.audio_duration_ms
        and receipt.provider == transcript.provider
        and receipt.provider_model == transcript.provider_model
        and receipt.region == transcript.region
        and receipt.provider_request_id_sha256
        == transcript.provider_request_id_sha256
        and receipt.segment_count == len(transcript.segments)
        and receipt.completed_at == transcript.completed_at
    )


def _redact_transcript(
    transcript: UntrustedSTTTranscript,
) -> tuple[str, str]:
    transient_text: str | None = None
    intake: Any = None
    failed = False
    try:
        transient_text = transcript.transient_redaction_input()
        intake = redact_and_parse_pasted_transcript(
            transient_text,
            transcript_id="source-stt-meeting",
            title="Speech-to-text meeting transcript",
        )
    except Exception:
        failed = True
    finally:
        transient_text = None

    if failed or intake is None:
        raise STTTranscriptHandoffError(
            STTTranscriptHandoffFailureCode.REDACTION_FAILED
        )
    redacted_text = "\n".join(
        "{0}: {1}".format(line.speaker, line.text)
        for line in intake.transcript.lines
    )
    return redacted_text, intake.redaction.policy_version


class STTTranscriptHandoffService:
    """Attach one sealed operation result through the existing source path."""

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
        operation_result: STTOperationResult,
    ) -> STTTranscriptHandoffResult:
        """Redact, attach, and publish linked content-free receipts."""

        if type(operation_result) is not STTOperationResult:
            raise STTTranscriptHandoffError(
                STTTranscriptHandoffFailureCode.INVALID_RESULT
            )
        operation_receipt = operation_result.receipt
        transcript = operation_result.transcript
        if not _operation_bindings_match(operation_receipt, transcript):
            raise STTTranscriptHandoffError(
                STTTranscriptHandoffFailureCode.BINDING_MISMATCH
            )

        completed_at: datetime | None = None
        clock_failed = False
        try:
            completed_at = self._clock()
            clock_failed = (
                not isinstance(completed_at, datetime)
                or completed_at.tzinfo is None
                or completed_at.utcoffset() is None
                or completed_at < operation_receipt.completed_at
            )
        except Exception:
            clock_failed = True
        if clock_failed or completed_at is None:
            raise STTTranscriptHandoffError(
                STTTranscriptHandoffFailureCode.INTERNAL
            )
        completed_at = completed_at.astimezone(timezone.utc)

        redacted_text, policy_version = _redact_transcript(transcript)
        content_sha256 = hashlib.sha256(
            redacted_text.encode("utf-8")
        ).hexdigest()
        source_receipt: POCSourceReceipt | None = None
        source_failure: STTTranscriptHandoffFailureCode | None = None
        try:
            source_receipt = self._source_intake.capture_stt_transcript(
                poc_id=transcript.poc_id,
                redacted_transcript_text=redacted_text,
                expected_content_sha256=content_sha256,
                operation_id=operation_receipt.operation_id,
                idempotency_key=(
                    _IDEMPOTENCY_PREFIX + operation_receipt.operation_id
                ),
            )
        except (POCSourceDraftArchived, POCSourceDraftUnavailable):
            source_failure = (
                STTTranscriptHandoffFailureCode.SOURCE_UNAVAILABLE
            )
        except (
            POCSourceIdempotencyConflict,
            POCSourceIntakeRevisionRequired,
            POCSourceRevisionRequired,
            POCSourceStaleRevision,
        ):
            source_failure = STTTranscriptHandoffFailureCode.SOURCE_CONFLICT
        except (
            DuplicatePOCSourceId,
            POCSourceCapacityExceeded,
            POCSourceIntakeCapacityExceeded,
        ):
            source_failure = STTTranscriptHandoffFailureCode.CAPACITY_EXCEEDED
        except POCSourceIntakeInvalid:
            source_failure = STTTranscriptHandoffFailureCode.REDACTION_FAILED
        except (POCSourceIntakeError, TypeError, ValueError):
            source_failure = STTTranscriptHandoffFailureCode.INTERNAL
        except Exception:
            source_failure = STTTranscriptHandoffFailureCode.INTERNAL

        redacted_character_count = len(redacted_text)
        redacted_text = ""
        if source_failure is not None or source_receipt is None:
            raise STTTranscriptHandoffError(
                source_failure or STTTranscriptHandoffFailureCode.INTERNAL
            )

        projection: STTTranscriptHandoffResult | None = None
        projection_failed = False
        try:
            transcript_receipt = STTTranscriptReceipt(
                operation_id=operation_receipt.operation_id,
                authorization_id=transcript.authorization_id,
                request_id=transcript.request_id,
                poc_id=transcript.poc_id,
                source_receipt_id=source_receipt.source_receipt_id,
                meeting_identity_sha256=(
                    operation_receipt.meeting_identity_sha256
                ),
                audio_sha256=transcript.audio_sha256,
                provider_request_id_sha256=(
                    transcript.provider_request_id_sha256
                ),
                redacted_transcript_sha256=content_sha256,
                redacted_character_count=redacted_character_count,
                segment_count=len(transcript.segments),
                provider=transcript.provider,
                provider_model=transcript.provider_model,
                region=transcript.region,
                redaction_policy_version=policy_version,
                speaker_mapping=transcript.speaker_mapping,
                completed_at=completed_at,
            )
            projection = STTTranscriptHandoffResult(
                source_receipt=source_receipt,
                transcript_receipt=transcript_receipt,
            )
        except Exception:
            projection_failed = True
        if projection_failed or projection is None:
            raise STTTranscriptHandoffError(
                STTTranscriptHandoffFailureCode.INTERNAL
            )
        return projection


__all__ = [
    "STT_HANDOFF_VERSION",
    "STTTranscriptHandoffError",
    "STTTranscriptHandoffFailureCode",
    "STTTranscriptHandoffResult",
    "STTTranscriptHandoffService",
]
