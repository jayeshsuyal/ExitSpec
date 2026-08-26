"""Local, source-aware Zoom handoff for the existing employee workbench.

This is a bounded conformance path for ``/app``. It uses only server-owned
synthetic packets, the strict RTMS decoder, the PR4 state machine, and the PR5
proposal bridge. It never opens a Zoom network connection and never exposes
transcript text or provider identifiers in its browser projection.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from threading import RLock
from typing import Callable, Literal

from pydantic import ConfigDict, Field

from .models import FrozenExitSpecModel
from .poc_creation import (
    DraftPOCCreationError,
    FirstSourceChoice,
    ProcessLocalDraftPOCService,
)
from .zoom_proposal_bridge import (
    ZoomProposalBridge,
    ZoomProposalBridgeError,
    ZoomProposalBridgeResult,
)
from .zoom_rtms_decoder import (
    ZOOM_RTMS_PACKET_SCHEMA_VERSION,
    ZOOM_RTMS_TRANSCRIPT_MEDIA_TYPE,
    ZOOM_RTMS_TRANSCRIPT_MESSAGE_TYPE,
    ZoomDecoderProvenance,
    decode_zoom_rtms_transcript_packet,
)
from .zoom_session_runtime import (
    ZoomSessionError,
    ZoomSessionFailureCode,
    ZoomSessionState,
    ZoomSessionStateMachine,
    new_zoom_session_id,
)


ZOOM_GUIDED_HANDOFF_VERSION = "exitspec.zoom-guided-handoff/1.0"
ZOOM_GUIDED_DISCLOSURE_ID = "zoom_rtms_local_handoff_v1"
ZOOM_GUIDED_MODE = "ZOOM_RTMS_LOCAL_SYNTHETIC"
ZOOM_GUIDED_SOURCE_PROVIDER = "ZOOM_RTMS"
ZOOM_GUIDED_SOURCE_AUTHORITY = "UNTRUSTED_SOURCE_ONLY"

_POC_ID_PATTERN = r"^poc_[a-z0-9][a-z0-9_-]{2,63}$"
_SESSION_ID_PATTERN = r"^zoomsess_[a-f0-9]{64}$"
_GUIDED_DOMAIN = b"exitspec-zoom-guided-handoff-v1\x00"
_CAPTURE_PLAN_SHA256 = hashlib.sha256(
    b"exitspec-zoom-guided-capture-plan-v1"
).hexdigest()
_SETUP_ATTESTATION_SHA256 = hashlib.sha256(
    b"exitspec-zoom-guided-setup-attestation-v1"
).hexdigest()
_RUNTIME_PLAN_SHA256 = hashlib.sha256(
    b"exitspec-zoom-guided-runtime-plan-v1"
).hexdigest()
_FIXTURE_SHA256 = hashlib.sha256(
    b"exitspec-zoom-guided-synthetic-packet-set-v1"
).hexdigest()


class ZoomGuidedHandoffError(RuntimeError):
    """Sanitized handoff refusal for the local workbench."""

    def __init__(self, code: str, message: str, next_action: str) -> None:
        self.code = code
        self.next_action = next_action
        super().__init__(message)


class _HandoffModel(FrozenExitSpecModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
        validate_default=True,
    )


class ZoomGuidedHandoffDisclosure(_HandoffModel):
    """Content-free disclosure shown before the local handoff starts."""

    schema_version: Literal[ZOOM_GUIDED_HANDOFF_VERSION] = (
        ZOOM_GUIDED_HANDOFF_VERSION
    )
    disclosure_id: Literal[ZOOM_GUIDED_DISCLOSURE_ID] = (
        ZOOM_GUIDED_DISCLOSURE_ID
    )
    mode: Literal[ZOOM_GUIDED_MODE] = ZOOM_GUIDED_MODE
    provider: Literal[ZOOM_GUIDED_SOURCE_PROVIDER] = ZOOM_GUIDED_SOURCE_PROVIDER
    provider_connected: Literal[False] = False
    live_network: Literal[False] = False
    synthetic_only: Literal[True] = True
    transcript_only: Literal[True] = True
    consent_required_before_capture: Literal[True] = True
    raw_transcript_returned_to_browser: Literal[False] = False
    source_authority: Literal[ZOOM_GUIDED_SOURCE_AUTHORITY] = (
        ZOOM_GUIDED_SOURCE_AUTHORITY
    )
    notice: str = Field(min_length=1, max_length=1200)
    may_confirm_contract: Literal[False] = False
    may_freeze_contract: Literal[False] = False
    may_start_measurement: Literal[False] = False
    may_assign_verdict: Literal[False] = False


class ZoomGuidedHandoffSnapshot(_HandoffModel):
    """Safe browser projection of the four-step Zoom handoff."""

    schema_version: Literal[ZOOM_GUIDED_HANDOFF_VERSION] = (
        ZOOM_GUIDED_HANDOFF_VERSION
    )
    poc_id: str = Field(pattern=_POC_ID_PATTERN)
    session_id: str | None = Field(default=None, pattern=_SESSION_ID_PATTERN)
    state: Literal["IDLE", "LISTENING", "PROCESSING", "DRAFT_READY", "FAILED"]
    next_action: Literal[
        "AUTHORIZE_AND_LISTEN",
        "WAIT_FOR_STOP",
        "PROCESS_TRANSCRIPT",
        "OPEN_DRAFT",
        "RESTART_HANDOFF",
    ]
    proposal_count: int = Field(ge=0, le=64)
    review_state: Literal["NOT_STARTED", "NEEDS_REVIEW"]
    review_url: str | None = Field(
        default=None,
        pattern=r"^/app/pocs/[a-z0-9_-]{3,64}/review$",
    )
    source_provider: Literal[ZOOM_GUIDED_SOURCE_PROVIDER] = (
        ZOOM_GUIDED_SOURCE_PROVIDER
    )
    source_authority: Literal[ZOOM_GUIDED_SOURCE_AUTHORITY] = (
        ZOOM_GUIDED_SOURCE_AUTHORITY
    )
    synthetic_only: Literal[True] = True
    live_connection: Literal[False] = False
    raw_transcript_returned_to_browser: Literal[False] = False
    may_confirm_contract: Literal[False] = False
    may_freeze_contract: Literal[False] = False
    may_start_measurement: Literal[False] = False
    may_assign_verdict: Literal[False] = False
    failure_code: str | None = None


@dataclass(frozen=True, slots=True)
class ZoomGuidedHandoffAction:
    """Mutation response with replay metadata kept outside the snapshot."""

    snapshot: ZoomGuidedHandoffSnapshot
    idempotent_replay: bool


@dataclass(slots=True)
class _GuidedRecord:
    session: ZoomSessionStateMachine
    result: ZoomProposalBridgeResult | None = None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _digest(payload: object) -> str:
    return hashlib.sha256(
        _GUIDED_DOMAIN
        + json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def _guided_segment(*, ordinal: int, user_id: str, text: str):
    packet = json.dumps(
        {
            "schema_version": ZOOM_RTMS_PACKET_SCHEMA_VERSION,
            "media_type": ZOOM_RTMS_TRANSCRIPT_MEDIA_TYPE,
            "message_type": ZOOM_RTMS_TRANSCRIPT_MESSAGE_TYPE,
            "user_id": user_id,
            "start_time": "2026-08-25T19:00:00.000Z",
            "end_time": "2026-08-25T19:00:01.000Z",
            "timestamp": 1_787_684_400_000 + (ordinal * 1_000),
            "language": "en-US",
            "data": text,
        },
        separators=(",", ":"),
    ).encode("utf-8")
    packet_sha256 = hashlib.sha256(packet).hexdigest()
    provenance = ZoomDecoderProvenance(
        source_classification="PRIVATE_SYNTHETIC_RUNTIME",
        fixture_sha256=_FIXTURE_SHA256,
        capture_plan_sha256=_CAPTURE_PLAN_SHA256,
        setup_attestation_sha256=_SETUP_ATTESTATION_SHA256,
        runtime_plan_sha256=_RUNTIME_PLAN_SHA256,
        packet_sha256=packet_sha256,
    )
    return decode_zoom_rtms_transcript_packet(
        packet,
        speaker_pseudonyms={
            "guided-customer": "SPEAKER_1",
            "guided-engineer": "SPEAKER_2",
        },
        provenance=provenance,
        arrival_index=ordinal,
    )


def _guided_segments():
    return (
        _guided_segment(
            ordinal=1,
            user_id="guided-customer",
            text=(
                "Criterion: p95 time to first token must stay below 500 "
                "milliseconds at concurrency 4."
            ),
        ),
        _guided_segment(
            ordinal=2,
            user_id="guided-engineer",
            text=(
                "Criterion: error rate must remain below 1 percent over all "
                "measured attempts."
            ),
        ),
    )


class ZoomGuidedHandoffService:
    """Thread-safe process-local Zoom handoff bound to existing draft POCs."""

    __slots__ = ("_bridge", "_clock", "_drafts", "_lock", "_records")

    def __init__(
        self,
        *,
        bridge: ZoomProposalBridge,
        drafts: ProcessLocalDraftPOCService,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        if type(bridge) is not ZoomProposalBridge:
            raise TypeError("bridge must be a ZoomProposalBridge.")
        if type(drafts) is not ProcessLocalDraftPOCService:
            raise TypeError("drafts must be a ProcessLocalDraftPOCService.")
        if not callable(clock):
            raise TypeError("clock must be callable.")
        self._bridge = bridge
        self._drafts = drafts
        self._clock = clock
        self._lock = RLock()
        self._records: dict[str, _GuidedRecord] = {}

    def _require_meeting_draft(self, poc_id: object):
        if type(poc_id) is not str:
            raise ZoomGuidedHandoffError(
                "ZOOM_GUIDED_HANDOFF_INVALID_REQUEST",
                "The Zoom handoff request was not accepted.",
                "review_the_zoom_handoff_request",
            )
        try:
            draft = self._drafts.get(poc_id)
        except (DraftPOCCreationError, TypeError, ValueError):
            raise ZoomGuidedHandoffError(
                "ZOOM_GUIDED_HANDOFF_POC_UNAVAILABLE",
                "The selected draft POC cannot accept a Zoom source.",
                "return_to_the_active_meeting_source",
            ) from None
        if draft.archive_state.value != "ACTIVE":
            raise ZoomGuidedHandoffError(
                "ZOOM_GUIDED_HANDOFF_POC_UNAVAILABLE",
                "The selected draft POC cannot accept a Zoom source.",
                "return_to_the_active_meeting_source",
            )
        if draft.first_source_choice is not FirstSourceChoice.MEETING:
            raise ZoomGuidedHandoffError(
                "ZOOM_GUIDED_HANDOFF_WRONG_SOURCE",
                "The selected draft POC cannot accept a Zoom source.",
                "return_to_the_active_meeting_source",
            )
        return draft

    def disclosure_for(self, poc_id: object) -> ZoomGuidedHandoffDisclosure:
        self._require_meeting_draft(poc_id)
        return ZoomGuidedHandoffDisclosure(
            notice=(
                "Local Zoom RTMS handoff only. No Zoom network connection or "
                "customer data is used; a fixed synthetic transcript is "
                "decoded, bounded, and sent to human review."
            )
        )

    def current(self, poc_id: object) -> ZoomGuidedHandoffSnapshot:
        self._require_meeting_draft(poc_id)
        with self._lock:
            record = self._records.get(poc_id)
            return self._snapshot(poc_id, record)

    def start(
        self,
        *,
        poc_id: object,
        consent_acknowledged: object,
        idempotency_key: object,
    ) -> ZoomGuidedHandoffAction:
        self._require_meeting_draft(poc_id)
        if consent_acknowledged is not True:
            raise ZoomGuidedHandoffError(
                "ZOOM_GUIDED_HANDOFF_CONSENT_REQUIRED",
                "Authorize the local Zoom handoff before listening starts.",
                "review_the_zoom_handoff_notice",
            )
        if type(poc_id) is not str:
            raise ZoomGuidedHandoffError(
                "ZOOM_GUIDED_HANDOFF_INVALID_REQUEST",
                "The Zoom handoff request was not accepted.",
                "review_the_zoom_handoff_request",
            )
        with self._lock:
            record = self._records.get(poc_id)
            if record is not None:
                state = record.session.snapshot().state
                if state is not ZoomSessionState.STARTING:
                    return ZoomGuidedHandoffAction(
                        snapshot=self._snapshot(poc_id, record),
                        idempotent_replay=True,
                    )
            else:
                session = ZoomSessionStateMachine(
                    session_id=new_zoom_session_id("zoom-guided:" + poc_id),
                    clock=self._clock,
                )
                record = _GuidedRecord(session=session)
                self._records[poc_id] = record
            try:
                started = record.session.mark_listening(
                    idempotency_key=idempotency_key
                )
                for ordinal, segment in enumerate(_guided_segments(), start=1):
                    record.session.append_transcript(
                        segment,
                        idempotency_key=(
                            "zoom-guided-segment-"
                            + str(ordinal)
                            + "-"
                            + record.session.snapshot().session_id[:16]
                        ),
                    )
            except ZoomSessionError as error:
                raise ZoomGuidedHandoffError(
                    error.code,
                    str(error),
                    error.next_action,
                ) from None
            return ZoomGuidedHandoffAction(
                snapshot=self._snapshot(poc_id, record),
                idempotent_replay=started.idempotent_replay,
            )

    def stop(
        self,
        *,
        poc_id: object,
        idempotency_key: object,
    ) -> ZoomGuidedHandoffAction:
        self._require_meeting_draft(poc_id)
        if type(poc_id) is not str:
            raise ZoomGuidedHandoffError(
                "ZOOM_GUIDED_HANDOFF_INVALID_REQUEST",
                "The Zoom handoff request was not accepted.",
                "review_the_zoom_handoff_request",
            )
        with self._lock:
            record = self._records.get(poc_id)
            if record is None:
                raise ZoomGuidedHandoffError(
                    "ZOOM_GUIDED_HANDOFF_NOT_STARTED",
                    "The Zoom handoff has not started.",
                    "authorize_and_start_the_handoff",
                )
            if record.session.snapshot().state is not ZoomSessionState.LISTENING:
                return ZoomGuidedHandoffAction(
                    snapshot=self._snapshot(poc_id, record),
                    idempotent_replay=True,
                )
            try:
                stopped = record.session.stop(idempotency_key=idempotency_key)
            except ZoomSessionError as error:
                raise ZoomGuidedHandoffError(
                    error.code,
                    str(error),
                    error.next_action,
                ) from None
            return ZoomGuidedHandoffAction(
                snapshot=self._snapshot(poc_id, record),
                idempotent_replay=stopped.idempotent_replay,
            )

    def process(
        self,
        *,
        poc_id: object,
        idempotency_key: object,
    ) -> ZoomGuidedHandoffAction:
        self._require_meeting_draft(poc_id)
        if type(poc_id) is not str:
            raise ZoomGuidedHandoffError(
                "ZOOM_GUIDED_HANDOFF_INVALID_REQUEST",
                "The Zoom handoff request was not accepted.",
                "review_the_zoom_handoff_request",
            )
        with self._lock:
            record = self._records.get(poc_id)
            if record is None:
                raise ZoomGuidedHandoffError(
                    "ZOOM_GUIDED_HANDOFF_NOT_STARTED",
                    "The Zoom handoff has not started.",
                    "authorize_and_start_the_handoff",
                )
            if record.session.snapshot().state is ZoomSessionState.DRAFT_READY:
                return ZoomGuidedHandoffAction(
                    snapshot=self._snapshot(poc_id, record),
                    idempotent_replay=True,
                )
            try:
                if record.session.snapshot().state is not ZoomSessionState.PROCESSING:
                    raise ZoomSessionError(ZoomSessionFailureCode.INVALID_TRANSITION)
                result = self._bridge.bridge_into_existing_poc(
                    session=record.session,
                    poc_id=poc_id,
                )
                record.result = result
            except ZoomProposalBridgeError as error:
                try:
                    record.session.processing_failed(
                        idempotency_key="zoom-guided-processing-failure-"
                        + record.session.snapshot().session_id[:32]
                    )
                except ZoomSessionError:
                    pass
                raise ZoomGuidedHandoffError(
                    error.code,
                    str(error),
                    error.next_action,
                ) from None
            except ZoomSessionError as error:
                raise ZoomGuidedHandoffError(
                    error.code,
                    str(error),
                    error.next_action,
                ) from None
            return ZoomGuidedHandoffAction(
                snapshot=self._snapshot(poc_id, record),
                idempotent_replay=result.idempotent_replay,
            )

    def _snapshot(
        self,
        poc_id: str,
        record: _GuidedRecord | None,
    ) -> ZoomGuidedHandoffSnapshot:
        if record is None:
            return ZoomGuidedHandoffSnapshot(
                poc_id=poc_id,
                state="IDLE",
                next_action="AUTHORIZE_AND_LISTEN",
                proposal_count=0,
                review_state="NOT_STARTED",
            )
        session = record.session.snapshot()
        state_map = {
            ZoomSessionState.STARTING: "IDLE",
            ZoomSessionState.LISTENING: "LISTENING",
            ZoomSessionState.INTERRUPTED: "LISTENING",
            ZoomSessionState.RECONNECTING: "LISTENING",
            ZoomSessionState.PROCESSING: "PROCESSING",
            ZoomSessionState.DRAFT_READY: "DRAFT_READY",
            ZoomSessionState.FAILED: "FAILED",
        }
        state = state_map[session.state]
        result = record.result
        return ZoomGuidedHandoffSnapshot(
            poc_id=poc_id,
            session_id=session.session_id,
            state=state,
            next_action={
                "LISTENING": "WAIT_FOR_STOP",
                "PROCESSING": "PROCESS_TRANSCRIPT",
                "DRAFT_READY": "OPEN_DRAFT",
                "FAILED": "RESTART_HANDOFF",
            }.get(state, "AUTHORIZE_AND_LISTEN"),
            proposal_count=result.proposal_count if result is not None else 0,
            review_state=("NEEDS_REVIEW" if result is not None else "NOT_STARTED"),
            review_url=result.review_url if result is not None else None,
            failure_code=(
                session.failure_code.value if session.failure_code is not None else None
            ),
        )


__all__ = [
    "ZOOM_GUIDED_DISCLOSURE_ID",
    "ZOOM_GUIDED_HANDOFF_VERSION",
    "ZOOM_GUIDED_MODE",
    "ZOOM_GUIDED_SOURCE_PROVIDER",
    "ZoomGuidedHandoffAction",
    "ZoomGuidedHandoffDisclosure",
    "ZoomGuidedHandoffError",
    "ZoomGuidedHandoffService",
    "ZoomGuidedHandoffSnapshot",
]
