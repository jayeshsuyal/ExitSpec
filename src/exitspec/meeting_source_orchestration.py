"""Compose the durable synthetic meeting inbox with the source handoff.

This module owns one narrow transition: recover an authenticated provider-
neutral event population, seal it under current consent, and attach its
redacted transcript as review-only source input. It performs no network work,
does not delete inbox payloads, and has no agreement, execution, evidence, or
verdict authority.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
import hashlib
import hmac
from threading import RLock
from typing import Any, Callable, Literal, Mapping, Self

from pydantic import ConfigDict, Field, model_validator

from .canonical import canonical_json_bytes
from .meeting_connector import (
    MEETING_CONNECTOR_AUTHORITY,
    MEETING_CONNECTOR_REVIEW_STATE,
    MeetingCaptureAuthorization,
    MeetingConnectorDenied,
    MeetingConnectorFailureCode,
    MeetingTransportBinding,
    seal_meeting_transcript_window,
)
from .meeting_event_inbox import (
    MeetingEventInboxCapacityError,
    MeetingEventInboxConflict,
    MeetingEventInboxIntegrityError,
    MeetingEventInboxPayloadExpired,
    MeetingEventInboxStorageError,
    MeetingEventInboxStreamReceipt,
    SQLiteMeetingEventInbox,
)
from .meeting_source_handoff import (
    MEETING_SOURCE_HANDOFF_AUTHORITY,
    MeetingSourceHandoffError,
    MeetingSourceHandoffFailureCode,
    MeetingSourceHandoffResult,
    MeetingTranscriptSourceHandoffService,
)
from .models import FrozenExitSpecModel, SHA256_PATTERN
from .poc_source_intake import ProcessLocalPOCSourceIntake
from .stt_boundary import MeetingConsentAttestation


MEETING_SOURCE_ORCHESTRATION_VERSION = (
    "exitspec-meeting-source-orchestration/1.0"
)
MEETING_SOURCE_ORCHESTRATION_SCOPE = "SYNTHETIC_REVIEW_SOURCE_ONLY"
MEETING_SOURCE_ORCHESTRATION_RETENTION = "INBOX_TTL_UNCHANGED"

_ORCHESTRATION_DOMAIN = b"exitspec-meeting-source-orchestration-v1\x00"


class MeetingSourceOrchestrationFailureCode(str, Enum):
    """Stable content-free failures for the composed source transition."""

    INVALID_REQUEST = "MEETING_ORCHESTRATION_INVALID_REQUEST"
    STREAM_NOT_FOUND = "MEETING_ORCHESTRATION_STREAM_NOT_FOUND"
    STREAM_CONFLICT = "MEETING_ORCHESTRATION_STREAM_CONFLICT"
    STREAM_CAPACITY = "MEETING_ORCHESTRATION_STREAM_CAPACITY"
    PAYLOAD_EXPIRED = "MEETING_ORCHESTRATION_PAYLOAD_EXPIRED"
    INBOX_INTEGRITY = "MEETING_ORCHESTRATION_INBOX_INTEGRITY"
    INBOX_STORAGE = "MEETING_ORCHESTRATION_INBOX_STORAGE"
    SEALING_REJECTED = "MEETING_ORCHESTRATION_SEALING_REJECTED"
    SOURCE_HANDOFF_REJECTED = (
        "MEETING_ORCHESTRATION_SOURCE_HANDOFF_REJECTED"
    )
    INTERNAL = "MEETING_ORCHESTRATION_INTERNAL"


_FAILURE_DETAILS: dict[
    MeetingSourceOrchestrationFailureCode,
    tuple[str, str],
] = {
    MeetingSourceOrchestrationFailureCode.INVALID_REQUEST: (
        "The meeting source finalization request was not accepted.",
        "review_meeting_finalization_input",
    ),
    MeetingSourceOrchestrationFailureCode.STREAM_NOT_FOUND: (
        "The authorized meeting stream is not available for finalization.",
        "wait_for_the_meeting_stream",
    ),
    MeetingSourceOrchestrationFailureCode.STREAM_CONFLICT: (
        "The meeting stream is durably blocked by conflicting input.",
        "start_a_new_meeting_capture",
    ),
    MeetingSourceOrchestrationFailureCode.STREAM_CAPACITY: (
        "The meeting stream exceeded its frozen intake bounds.",
        "start_a_new_bounded_capture",
    ),
    MeetingSourceOrchestrationFailureCode.PAYLOAD_EXPIRED: (
        "The private meeting payload expired before source finalization.",
        "start_a_new_meeting_capture",
    ),
    MeetingSourceOrchestrationFailureCode.INBOX_INTEGRITY: (
        "The durable meeting inbox did not pass integrity verification.",
        "review_the_meeting_inbox",
    ),
    MeetingSourceOrchestrationFailureCode.INBOX_STORAGE: (
        "The durable meeting inbox is unavailable.",
        "restore_the_meeting_inbox",
    ),
    MeetingSourceOrchestrationFailureCode.SEALING_REJECTED: (
        "The recovered meeting stream did not satisfy the sealing contract.",
        "review_meeting_completion_and_consent",
    ),
    MeetingSourceOrchestrationFailureCode.SOURCE_HANDOFF_REJECTED: (
        "The sealed meeting transcript could not enter source review.",
        "review_the_meeting_source_handoff",
    ),
    MeetingSourceOrchestrationFailureCode.INTERNAL: (
        "The meeting source finalization could not complete safely.",
        "review_meeting_source_finalization",
    ),
}


class MeetingSourceOrchestrationError(RuntimeError):
    """Sanitized refusal with an optional safe upstream failure code."""

    retryable = False

    def __init__(
        self,
        failure_code: MeetingSourceOrchestrationFailureCode,
        *,
        upstream_failure: (
            MeetingConnectorFailureCode
            | MeetingSourceHandoffFailureCode
            | None
        ) = None,
    ) -> None:
        self.failure_code = MeetingSourceOrchestrationFailureCode(
            failure_code
        )
        self.code = self.failure_code.value
        if upstream_failure is not None and not isinstance(
            upstream_failure,
            (MeetingConnectorFailureCode, MeetingSourceHandoffFailureCode),
        ):
            raise TypeError("upstream_failure must be a supported safe code.")
        self.upstream_failure_code = (
            None if upstream_failure is None else upstream_failure.value
        )
        safe_message, next_action = _FAILURE_DETAILS[self.failure_code]
        self.next_action = next_action
        super().__init__(safe_message)


class _FrozenOrchestrationModel(FrozenExitSpecModel):
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


class MeetingSourceOrchestrationResult(_FrozenOrchestrationModel):
    """Content-free projection linking recovery to one source handoff."""

    schema_version: Literal[MEETING_SOURCE_ORCHESTRATION_VERSION] = (
        MEETING_SOURCE_ORCHESTRATION_VERSION
    )
    inbox_stream_receipt: MeetingEventInboxStreamReceipt
    handoff_result: MeetingSourceHandoffResult
    orchestration_sha256: str = Field(pattern=SHA256_PATTERN)
    completion_scope: Literal[MEETING_SOURCE_ORCHESTRATION_SCOPE] = (
        MEETING_SOURCE_ORCHESTRATION_SCOPE
    )
    inbox_retention: Literal[MEETING_SOURCE_ORCHESTRATION_RETENTION] = (
        MEETING_SOURCE_ORCHESTRATION_RETENTION
    )
    transcript_authority: Literal[MEETING_SOURCE_HANDOFF_AUTHORITY] = (
        MEETING_SOURCE_HANDOFF_AUTHORITY
    )
    review_state: Literal[MEETING_CONNECTOR_REVIEW_STATE] = (
        MEETING_CONNECTOR_REVIEW_STATE
    )
    may_delete_private_inbox_payloads: Literal[False] = False
    may_confirm_contract: Literal[False] = False
    may_freeze_contract: Literal[False] = False
    may_start_measurement: Literal[False] = False
    may_assign_verdict: Literal[False] = False
    synthetic_only: Literal[True] = True

    @model_validator(mode="after")
    def require_exact_population_link(self) -> "MeetingSourceOrchestrationResult":
        inbox = self.inbox_stream_receipt
        window = self.handoff_result.handoff_receipt.transcript_window_receipt
        if (
            not inbox.sequence_contiguous
            or inbox.first_sequence != 1
            or inbox.last_sequence != inbox.unique_event_count
            or inbox.unique_event_count != window.unique_event_count
            or inbox.exact_duplicate_count != window.duplicate_event_count
            or inbox.transcript_authority != MEETING_CONNECTOR_AUTHORITY
            or inbox.transcript_authority != self.transcript_authority
            or inbox.review_state != self.review_state
            or window.review_state != self.review_state
        ):
            raise ValueError("Meeting orchestration receipts are not linked.")
        expected = _orchestration_sha256(
            inbox_stream_receipt=inbox,
            handoff_result=self.handoff_result,
        )
        if not hmac.compare_digest(expected, self.orchestration_sha256):
            raise ValueError("Meeting orchestration digest does not match.")
        return self


def _orchestration_sha256(
    *,
    inbox_stream_receipt: MeetingEventInboxStreamReceipt,
    handoff_result: MeetingSourceHandoffResult,
) -> str:
    payload = {
        "handoff_result": handoff_result.model_dump(mode="json"),
        "inbox_stream_receipt": inbox_stream_receipt.model_dump(mode="json"),
    }
    return hashlib.sha256(
        _ORCHESTRATION_DOMAIN + canonical_json_bytes(payload)
    ).hexdigest()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class MeetingInboxSourceOrchestrationService:
    """Recover, seal, and attach one synthetic meeting source safely."""

    __slots__ = ("_clock", "_inbox", "_lock", "_source_intake")

    def __init__(
        self,
        inbox: SQLiteMeetingEventInbox,
        source_intake: ProcessLocalPOCSourceIntake,
        *,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        if type(inbox) is not SQLiteMeetingEventInbox:
            raise TypeError("inbox must be a SQLiteMeetingEventInbox.")
        if type(source_intake) is not ProcessLocalPOCSourceIntake:
            raise TypeError(
                "source_intake must be a ProcessLocalPOCSourceIntake."
            )
        if not callable(clock):
            raise TypeError("clock must be callable.")
        self._inbox = inbox
        self._source_intake = source_intake
        self._clock = clock
        self._lock = RLock()

    def finalize_source(
        self,
        *,
        authorization: MeetingCaptureAuthorization,
        binding: MeetingTransportBinding,
        consent: MeetingConsentAttestation,
    ) -> MeetingSourceOrchestrationResult:
        """Finalize one complete stream into review-only source input."""

        with self._lock:
            return self._finalize_source(
                authorization=authorization,
                binding=binding,
                consent=consent,
            )

    def _finalize_source(
        self,
        *,
        authorization: MeetingCaptureAuthorization,
        binding: MeetingTransportBinding,
        consent: MeetingConsentAttestation,
    ) -> MeetingSourceOrchestrationResult:
        """Run one process-local serialized finalization attempt."""

        if (
            type(authorization) is not MeetingCaptureAuthorization
            or type(binding) is not MeetingTransportBinding
            or type(consent) is not MeetingConsentAttestation
        ):
            raise MeetingSourceOrchestrationError(
                MeetingSourceOrchestrationFailureCode.INVALID_REQUEST
            )

        completed_at: datetime | None = None
        clock_failed = False
        try:
            completed_at = self._clock()
            clock_failed = (
                not isinstance(completed_at, datetime)
                or completed_at.tzinfo is None
                or completed_at.utcoffset() is None
            )
        except Exception:
            clock_failed = True
        if clock_failed or completed_at is None:
            raise MeetingSourceOrchestrationError(
                MeetingSourceOrchestrationFailureCode.INTERNAL
            )
        completed_at = completed_at.astimezone(timezone.utc)

        inbox_receipt: MeetingEventInboxStreamReceipt | None = None
        sealed_transcript = None
        recovered_stream = None
        private_events = None
        failure: MeetingSourceOrchestrationFailureCode | None = None
        upstream: MeetingConnectorFailureCode | None = None
        try:
            recovered_stream = self._inbox.recover_stream(
                authorization=authorization,
                binding=binding,
            )
            inbox_receipt = recovered_stream.receipt
            private_events = recovered_stream.events_for_sealing()
            sealed_transcript = seal_meeting_transcript_window(
                authorization,
                binding,
                consent,
                private_events,
                now=completed_at,
            )
        except MeetingEventInboxConflict:
            failure = MeetingSourceOrchestrationFailureCode.STREAM_CONFLICT
        except MeetingEventInboxCapacityError:
            failure = MeetingSourceOrchestrationFailureCode.STREAM_CAPACITY
        except MeetingEventInboxPayloadExpired:
            failure = MeetingSourceOrchestrationFailureCode.PAYLOAD_EXPIRED
        except MeetingEventInboxIntegrityError:
            failure = MeetingSourceOrchestrationFailureCode.INBOX_INTEGRITY
        except MeetingEventInboxStorageError:
            failure = MeetingSourceOrchestrationFailureCode.INBOX_STORAGE
        except KeyError:
            failure = MeetingSourceOrchestrationFailureCode.STREAM_NOT_FOUND
        except MeetingConnectorDenied as error:
            failure = MeetingSourceOrchestrationFailureCode.SEALING_REJECTED
            upstream = error.failure_code
        except Exception:
            failure = MeetingSourceOrchestrationFailureCode.INTERNAL
        finally:
            private_events = None
            recovered_stream = None

        if (
            failure is not None
            or inbox_receipt is None
            or sealed_transcript is None
        ):
            raise MeetingSourceOrchestrationError(
                failure or MeetingSourceOrchestrationFailureCode.INTERNAL,
                upstream_failure=upstream,
            )

        handoff_result: MeetingSourceHandoffResult | None = None
        handoff_failure: MeetingSourceHandoffFailureCode | None = None
        try:
            handoff_result = MeetingTranscriptSourceHandoffService(
                self._source_intake,
                clock=lambda: completed_at,
            ).handoff(sealed_transcript)
        except MeetingSourceHandoffError as error:
            handoff_failure = error.failure_code
        except Exception:
            handoff_failure = MeetingSourceHandoffFailureCode.INTERNAL
        finally:
            sealed_transcript = None

        if handoff_failure is not None or handoff_result is None:
            raise MeetingSourceOrchestrationError(
                MeetingSourceOrchestrationFailureCode.SOURCE_HANDOFF_REJECTED,
                upstream_failure=(
                    handoff_failure
                    or MeetingSourceHandoffFailureCode.INTERNAL
                ),
            )

        projection: MeetingSourceOrchestrationResult | None = None
        projection_failed = False
        try:
            projection = MeetingSourceOrchestrationResult(
                inbox_stream_receipt=inbox_receipt,
                handoff_result=handoff_result,
                orchestration_sha256=_orchestration_sha256(
                    inbox_stream_receipt=inbox_receipt,
                    handoff_result=handoff_result,
                ),
            )
        except Exception:
            projection_failed = True
        if projection_failed or projection is None:
            raise MeetingSourceOrchestrationError(
                MeetingSourceOrchestrationFailureCode.INTERNAL
            )
        return projection


__all__ = [
    "MEETING_SOURCE_ORCHESTRATION_RETENTION",
    "MEETING_SOURCE_ORCHESTRATION_SCOPE",
    "MEETING_SOURCE_ORCHESTRATION_VERSION",
    "MeetingInboxSourceOrchestrationService",
    "MeetingSourceOrchestrationError",
    "MeetingSourceOrchestrationFailureCode",
    "MeetingSourceOrchestrationResult",
]
