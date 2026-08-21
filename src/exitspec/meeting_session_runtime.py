"""Provider-neutral application runtime for one guided meeting source.

The browser may create a session, record the fixed disclosure acknowledgement,
start the injected adapter, and ask for a draft.  It cannot provide meeting
identities, participant identities, transport proofs, transcript packets, or
downstream authority.  The default adapter is deterministic and synthetic; it
uses the existing meeting connector, durable inbox, sealer, redaction, and
review-only source handoff without claiming a live provider connection.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
import hashlib
import re
from threading import RLock
from typing import Any, Callable, Literal, Mapping, Protocol, Self

from pydantic import ConfigDict, Field, model_validator

from .canonical import canonical_json_bytes
from .meeting_connector import (
    MeetingCaptureAuthorization,
    MeetingCaptureIntent,
    MeetingConnectorPolicy,
    MeetingEventKind,
    MeetingTranscriptEvent,
    MeetingTransportBinding,
    authorize_meeting_capture,
    meeting_identity_sha256,
    stream_identity_sha256,
)
from .meeting_event_inbox import SQLiteMeetingEventInbox
from .meeting_source_orchestration import (
    MeetingInboxSourceOrchestrationService,
    MeetingSourceOrchestrationError,
)
from .models import FrozenExitSpecModel, SHA256_PATTERN
from .poc_creation import (
    DraftPOCArchiveState,
    DraftPOCNotFound,
    FirstSourceChoice,
    ProcessLocalDraftPOCService,
    POC_ID_PATTERN,
)
from .poc_source_intake import ProcessLocalPOCSourceIntake
from .stt_boundary import MeetingConsentAttestation, STTConsentState


MEETING_SESSION_VERSION = "exitspec-meeting-session/1.0"
MEETING_SESSION_DISCLOSURE_ID = "meeting_synthetic_disclosure_v1"
MEETING_SESSION_MODE = "FIXED_SYNTHETIC_MEETING"
MEETING_SESSION_NOTICE = (
    "This is a synthetic ExitSpec Zoom RTMS test. Transcript only; no "
    "customer data."
)

_NOTICE_SHA256 = hashlib.sha256(
    MEETING_SESSION_NOTICE.encode("utf-8")
).hexdigest()
_SESSION_DOMAIN = b"exitspec-meeting-session-v1\x00"
_OPERATION_DOMAIN = b"exitspec-meeting-session-operation-v1\x00"
_IDEMPOTENCY_DOMAIN = b"exitspec-meeting-session-idempotency-v1\x00"
_SYNTHETIC_BINDING_DOMAIN = b"exitspec-synthetic-meeting-binding-v1\x00"
_SYNTHETIC_EVENT_DOMAIN = b"exitspec-synthetic-meeting-event-v1\x00"

_SESSION_ID = re.compile(r"^meetsess_[a-f0-9]{64}$")
_POC_ID = re.compile(POC_ID_PATTERN)
_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,199}$")
_SOURCE_RECEIPT_ID = re.compile(r"^srcpt_[a-z0-9][a-z0-9_-]{7,95}$")

_SYNTHETIC_PROVIDER = "exitspec.synthetic"
_SYNTHETIC_ADAPTER_ID = "exitspec-synthetic-meeting"
_SYNTHETIC_ADAPTER_VERSION = "v1"
_SYNTHETIC_PARTICIPANTS = (
    "participant_synthetic_employee",
    "participant_synthetic_customer",
)
_SYNTHETIC_SCRIPT = (
    (
        _SYNTHETIC_PARTICIPANTS[0],
        "Synthetic host",
        MEETING_SESSION_NOTICE,
    ),
    (
        _SYNTHETIC_PARTICIPANTS[1],
        "Synthetic guest",
        "I consent to this synthetic transcript-only test.",
    ),
    (
        _SYNTHETIC_PARTICIPANTS[0],
        "Synthetic host",
        "Criterion: p95 time to first token must be at most 500 milliseconds "
        "at concurrency four.",
    ),
    (
        _SYNTHETIC_PARTICIPANTS[1],
        "Synthetic guest",
        "That matches my understanding.",
    ),
    (
        _SYNTHETIC_PARTICIPANTS[0],
        "Synthetic host",
        "Criterion: error rate must stay below 1 percent; timeouts count as "
        "errors over all measured attempts.",
    ),
    (
        _SYNTHETIC_PARTICIPANTS[1],
        "Synthetic guest",
        "Confirmed.",
    ),
    (
        _SYNTHETIC_PARTICIPANTS[0],
        "Synthetic host",
        "End of synthetic test.",
    ),
)


class MeetingSessionState(str, Enum):
    """Small product state machine shown by the guided workbench."""

    SETUP = "SETUP"
    READY = "READY"
    LIVE = "LIVE"
    DRAFT_READY = "DRAFT_READY"


class MeetingSessionNextAction(str, Enum):
    """Exactly one primary human action for each state."""

    RECORD_CONSENT = "RECORD_CONSENT"
    START_CAPTURE = "START_CAPTURE"
    DRAFT_REQUIREMENTS = "DRAFT_REQUIREMENTS"
    REVIEW_REQUIREMENTS = "REVIEW_REQUIREMENTS"


class MeetingSessionFailureCode(str, Enum):
    """Stable, content-free application failure classes."""

    INVALID_REQUEST = "MEETING_SESSION_INVALID_REQUEST"
    DRAFT_UNAVAILABLE = "MEETING_SESSION_DRAFT_UNAVAILABLE"
    WRONG_SOURCE_TYPE = "MEETING_SESSION_WRONG_SOURCE_TYPE"
    CAPACITY_EXCEEDED = "MEETING_SESSION_CAPACITY_EXCEEDED"
    SESSION_NOT_FOUND = "MEETING_SESSION_NOT_FOUND"
    IDEMPOTENCY_CONFLICT = "MEETING_SESSION_IDEMPOTENCY_CONFLICT"
    DISCLOSURE_MISMATCH = "MEETING_SESSION_DISCLOSURE_MISMATCH"
    CONSENT_REQUIRED = "MEETING_SESSION_CONSENT_REQUIRED"
    INVALID_TRANSITION = "MEETING_SESSION_INVALID_TRANSITION"
    ADAPTER_FAILED = "MEETING_SESSION_ADAPTER_FAILED"
    FINALIZATION_FAILED = "MEETING_SESSION_FINALIZATION_FAILED"


_FAILURE_DETAILS: dict[MeetingSessionFailureCode, tuple[str, str]] = {
    MeetingSessionFailureCode.INVALID_REQUEST: (
        "The meeting session request was not accepted.",
        "review_the_meeting_request",
    ),
    MeetingSessionFailureCode.DRAFT_UNAVAILABLE: (
        "The draft POC cannot accept a meeting source.",
        "return_to_the_poc_workspace",
    ),
    MeetingSessionFailureCode.WRONG_SOURCE_TYPE: (
        "This draft POC did not choose meeting intake.",
        "choose_the_meeting_source_type",
    ),
    MeetingSessionFailureCode.CAPACITY_EXCEEDED: (
        "The local meeting session runtime is at capacity.",
        "restart_the_local_demo_safely",
    ),
    MeetingSessionFailureCode.SESSION_NOT_FOUND: (
        "The meeting session was not found in this local process.",
        "start_a_new_meeting_session",
    ),
    MeetingSessionFailureCode.IDEMPOTENCY_CONFLICT: (
        "The idempotency key is bound to a different meeting action.",
        "use_a_new_idempotency_key",
    ),
    MeetingSessionFailureCode.DISCLOSURE_MISMATCH: (
        "The meeting disclosure is no longer current.",
        "review_the_current_disclosure",
    ),
    MeetingSessionFailureCode.CONSENT_REQUIRED: (
        "Every synthetic participant must accept the disclosure before capture.",
        "record_participant_consent",
    ),
    MeetingSessionFailureCode.INVALID_TRANSITION: (
        "The meeting action does not match the current session state.",
        "review_the_current_meeting_step",
    ),
    MeetingSessionFailureCode.ADAPTER_FAILED: (
        "The meeting adapter did not complete the requested step safely.",
        "review_the_meeting_adapter",
    ),
    MeetingSessionFailureCode.FINALIZATION_FAILED: (
        "The meeting transcript could not become a review-only source.",
        "review_meeting_completion_and_consent",
    ),
}


class MeetingSessionError(RuntimeError):
    """Sanitized refusal that never includes transcript or provider identity."""

    retryable = False

    def __init__(self, failure_code: MeetingSessionFailureCode) -> None:
        self.failure_code = MeetingSessionFailureCode(failure_code)
        self.code = self.failure_code.value
        message, next_action = _FAILURE_DETAILS[self.failure_code]
        self.next_action = next_action
        super().__init__(message)


class _FrozenSessionModel(FrozenExitSpecModel):
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


class MeetingSessionAdapterDescriptor(_FrozenSessionModel):
    """Safe facts about the server-owned adapter selected for one session."""

    provider: str = Field(pattern=r"^[a-z][a-z0-9._:-]{1,127}$")
    adapter_id: str = Field(pattern=r"^[a-z][a-z0-9._-]{2,79}$")
    adapter_version: str = Field(pattern=r"^[a-z][a-z0-9._:-]{1,127}$")
    mode: str = Field(pattern=r"^[A-Z][A-Z0-9_]{2,79}$")
    provider_connected: bool
    transcript_only: Literal[True] = True
    synthetic_only: Literal[True] = True


class MeetingSessionDisclosure(_FrozenSessionModel):
    """Exact disclosure and bounded capability surface shown before consent."""

    schema_version: Literal[MEETING_SESSION_VERSION] = MEETING_SESSION_VERSION
    disclosure_id: Literal[MEETING_SESSION_DISCLOSURE_ID] = (
        MEETING_SESSION_DISCLOSURE_ID
    )
    notice: Literal[MEETING_SESSION_NOTICE] = MEETING_SESSION_NOTICE
    adapter: MeetingSessionAdapterDescriptor
    participant_count: Literal[2] = 2
    fixed_script: Literal[True] = True
    consent_required_before_capture: Literal[True] = True
    customer_data_allowed: Literal[False] = False
    raw_audio_requested: Literal[False] = False
    raw_transcript_returned_to_browser: Literal[False] = False
    may_confirm_contract: Literal[False] = False
    may_freeze_contract: Literal[False] = False
    may_start_measurement: Literal[False] = False
    may_assign_verdict: Literal[False] = False
    synthetic_only: Literal[True] = True


class MeetingSessionSnapshot(_FrozenSessionModel):
    """Content-free product projection for one guided meeting session."""

    schema_version: Literal[MEETING_SESSION_VERSION] = MEETING_SESSION_VERSION
    session_id: str = Field(pattern=_SESSION_ID.pattern)
    poc_id: str = Field(pattern=POC_ID_PATTERN)
    state: MeetingSessionState
    next_action: MeetingSessionNextAction
    adapter: MeetingSessionAdapterDescriptor
    disclosure_id: Literal[MEETING_SESSION_DISCLOSURE_ID] = (
        MEETING_SESSION_DISCLOSURE_ID
    )
    participant_count: Literal[2] = 2
    consent_recorded: bool
    transcript_capture_started: bool
    draft_created: bool
    source_receipt_id: str | None = Field(
        default=None,
        pattern=_SOURCE_RECEIPT_ID.pattern,
    )
    proposal_count: int | None = Field(default=None, ge=0, le=64)
    orchestration_sha256: str | None = Field(
        default=None,
        pattern=SHA256_PATTERN,
    )
    review_url: str | None = Field(default=None, max_length=240)
    created_at: datetime
    updated_at: datetime
    consented_at: datetime | None = None
    started_at: datetime | None = None
    drafted_at: datetime | None = None
    review_state: Literal["NEEDS_REVIEW"] | None = None
    raw_audio_received: Literal[False] = False
    raw_transcript_returned_to_browser: Literal[False] = False
    may_confirm_contract: Literal[False] = False
    may_freeze_contract: Literal[False] = False
    may_start_measurement: Literal[False] = False
    may_assign_verdict: Literal[False] = False
    synthetic_only: Literal[True] = True

    @model_validator(mode="after")
    def require_exact_state_projection(self) -> "MeetingSessionSnapshot":
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot precede created_at.")
        expected_review_url = f"/app/pocs/{self.poc_id}/review"
        if self.state is MeetingSessionState.SETUP:
            valid = (
                self.next_action is MeetingSessionNextAction.RECORD_CONSENT
                and not self.consent_recorded
                and not self.transcript_capture_started
                and not self.draft_created
                and self.consented_at is None
                and self.started_at is None
                and self.drafted_at is None
            )
        elif self.state is MeetingSessionState.READY:
            valid = (
                self.next_action is MeetingSessionNextAction.START_CAPTURE
                and self.consent_recorded
                and not self.transcript_capture_started
                and not self.draft_created
                and self.consented_at is not None
                and self.started_at is None
                and self.drafted_at is None
            )
        elif self.state is MeetingSessionState.LIVE:
            valid = (
                self.next_action is MeetingSessionNextAction.DRAFT_REQUIREMENTS
                and self.consent_recorded
                and self.transcript_capture_started
                and not self.draft_created
                and self.consented_at is not None
                and self.started_at is not None
                and self.drafted_at is None
            )
        else:
            valid = (
                self.next_action is MeetingSessionNextAction.REVIEW_REQUIREMENTS
                and self.consent_recorded
                and self.transcript_capture_started
                and self.draft_created
                and self.consented_at is not None
                and self.started_at is not None
                and self.drafted_at is not None
                and self.source_receipt_id is not None
                and self.proposal_count is not None
                and self.orchestration_sha256 is not None
                and self.review_url == expected_review_url
                and self.review_state == "NEEDS_REVIEW"
            )
        if not valid:
            raise ValueError("Meeting session state projection is contradictory.")
        if self.state is not MeetingSessionState.DRAFT_READY and any(
            value is not None
            for value in (
                self.source_receipt_id,
                self.proposal_count,
                self.orchestration_sha256,
                self.review_url,
                self.review_state,
            )
        ):
            raise ValueError("Pre-draft session cannot expose source facts.")
        return self


class MeetingSessionActionResult(_FrozenSessionModel):
    """One mutation result with explicit exact-replay semantics."""

    session: MeetingSessionSnapshot
    idempotent_replay: bool


@dataclass(frozen=True, slots=True, repr=False)
class _PreparedAdapterSession:
    meeting_id: str
    organizer_participant_id: str
    participant_ids: tuple[str, ...]

    def __repr__(self) -> str:
        return "_PreparedAdapterSession(<private>)"


@dataclass(frozen=True, slots=True, repr=False)
class _StartedAdapterSession:
    authorization: MeetingCaptureAuthorization
    binding: MeetingTransportBinding
    initial_events: tuple[MeetingTranscriptEvent, ...]
    stream_id: str

    def __repr__(self) -> str:
        return "_StartedAdapterSession(<private>)"


class MeetingSessionAdapter(Protocol):
    """Internal seam; it grants no public schema or packet compatibility."""

    @property
    def descriptor(self) -> MeetingSessionAdapterDescriptor: ...

    def prepare(
        self,
        *,
        poc_id: str,
        session_id: str,
        now: datetime,
    ) -> _PreparedAdapterSession: ...

    def start(
        self,
        *,
        poc_id: str,
        session_id: str,
        prepared: _PreparedAdapterSession,
        consent: MeetingConsentAttestation,
        now: datetime,
    ) -> _StartedAdapterSession: ...

    def draft_events(
        self,
        *,
        session_id: str,
        prepared: _PreparedAdapterSession,
        started: _StartedAdapterSession,
        now: datetime,
    ) -> tuple[MeetingTranscriptEvent, ...]: ...


class FixedSyntheticMeetingAdapter:
    """Deterministic local adapter with no network or provider connection."""

    __slots__ = ()

    @property
    def descriptor(self) -> MeetingSessionAdapterDescriptor:
        return MeetingSessionAdapterDescriptor(
            provider=_SYNTHETIC_PROVIDER,
            adapter_id=_SYNTHETIC_ADAPTER_ID,
            adapter_version=_SYNTHETIC_ADAPTER_VERSION,
            mode=MEETING_SESSION_MODE,
            provider_connected=False,
        )

    def prepare(
        self,
        *,
        poc_id: str,
        session_id: str,
        now: datetime,
    ) -> _PreparedAdapterSession:
        del poc_id, now
        suffix = session_id.removeprefix("meetsess_")
        return _PreparedAdapterSession(
            meeting_id="meeting_synthetic_" + suffix,
            organizer_participant_id=_SYNTHETIC_PARTICIPANTS[0],
            participant_ids=_SYNTHETIC_PARTICIPANTS,
        )

    def start(
        self,
        *,
        poc_id: str,
        session_id: str,
        prepared: _PreparedAdapterSession,
        consent: MeetingConsentAttestation,
        now: datetime,
    ) -> _StartedAdapterSession:
        suffix = session_id.removeprefix("meetsess_")
        policy = MeetingConnectorPolicy(
            policy_id="meetpolicy_synthetic_session_v1",
            policy_version="v1",
            provider=_SYNTHETIC_PROVIDER,
            adapter_id=_SYNTHETIC_ADAPTER_ID,
            adapter_version=_SYNTHETIC_ADAPTER_VERSION,
            consent_notice_sha256=_NOTICE_SHA256,
            max_event_count=32,
            max_transcript_characters=20_000,
            max_window_seconds=3_600,
            reviewed_at=now - timedelta(days=1),
            expires_at=now + timedelta(days=1),
        )
        intent = MeetingCaptureIntent(
            request_id="meetreq_synthetic_" + suffix,
            poc_id=poc_id,
            provider=_SYNTHETIC_PROVIDER,
            adapter_id=_SYNTHETIC_ADAPTER_ID,
            adapter_version=_SYNTHETIC_ADAPTER_VERSION,
            meeting_id=prepared.meeting_id,
            organizer_participant_id=prepared.organizer_participant_id,
            participant_ids=prepared.participant_ids,
            consent=consent,
            requested_at=now,
        )
        authorization = authorize_meeting_capture(policy, intent, now=now)
        stream_id = "stream_synthetic_" + suffix
        binding_sha256 = _digest(
            _SYNTHETIC_BINDING_DOMAIN,
            {
                "authorization_id": authorization.authorization_id,
                "session_id": session_id,
                "stream_identity_sha256": stream_identity_sha256(stream_id),
            },
        )
        binding = MeetingTransportBinding(
            binding_id="meetbind_" + binding_sha256,
            authorization_id=authorization.authorization_id,
            provider=_SYNTHETIC_PROVIDER,
            adapter_id=_SYNTHETIC_ADAPTER_ID,
            adapter_version=_SYNTHETIC_ADAPTER_VERSION,
            meeting_identity_sha256=meeting_identity_sha256(
                prepared.meeting_id
            ),
            stream_identity_sha256=stream_identity_sha256(stream_id),
            webhook_event_sha256=binding_sha256,
            webhook_signature_verified=True,
            websocket_handshake_authenticated=True,
            protocol_version="synthetic.v1",
            established_at=now,
            expires_at=now + timedelta(hours=1),
        )
        started = MeetingTranscriptEvent(
            event_id=_event_id(session_id, 1),
            adapter_id=_SYNTHETIC_ADAPTER_ID,
            adapter_version=_SYNTHETIC_ADAPTER_VERSION,
            meeting_id=prepared.meeting_id,
            stream_id=stream_id,
            transport_binding_id=binding.binding_id,
            sequence=1,
            kind=MeetingEventKind.STREAM_STARTED,
            received_at=now,
            participant_ids=prepared.participant_ids,
        )
        return _StartedAdapterSession(
            authorization=authorization,
            binding=binding,
            initial_events=(started,),
            stream_id=stream_id,
        )

    def draft_events(
        self,
        *,
        session_id: str,
        prepared: _PreparedAdapterSession,
        started: _StartedAdapterSession,
        now: datetime,
    ) -> tuple[MeetingTranscriptEvent, ...]:
        events: list[MeetingTranscriptEvent] = []
        for offset, (participant_id, label, text) in enumerate(
            _SYNTHETIC_SCRIPT,
            start=2,
        ):
            segment_start_ms = (offset - 2) * 1_000
            events.append(
                MeetingTranscriptEvent(
                    event_id=_event_id(session_id, offset),
                    adapter_id=_SYNTHETIC_ADAPTER_ID,
                    adapter_version=_SYNTHETIC_ADAPTER_VERSION,
                    meeting_id=prepared.meeting_id,
                    stream_id=started.stream_id,
                    transport_binding_id=started.binding.binding_id,
                    sequence=offset,
                    kind=MeetingEventKind.TRANSCRIPT_SEGMENT,
                    received_at=now,
                    participant_id=participant_id,
                    participant_label=label,
                    transcript_text=text,
                    provider_timestamp_ms=segment_start_ms,
                    segment_start_ms=segment_start_ms,
                    segment_end_ms=segment_start_ms + 900,
                )
            )
        stop_sequence = len(_SYNTHETIC_SCRIPT) + 2
        events.append(
            MeetingTranscriptEvent(
                event_id=_event_id(session_id, stop_sequence),
                adapter_id=_SYNTHETIC_ADAPTER_ID,
                adapter_version=_SYNTHETIC_ADAPTER_VERSION,
                meeting_id=prepared.meeting_id,
                stream_id=started.stream_id,
                transport_binding_id=started.binding.binding_id,
                sequence=stop_sequence,
                kind=MeetingEventKind.STREAM_STOPPED,
                received_at=now,
                stop_reason="operator_draft_now",
            )
        )
        return tuple(events)


@dataclass(slots=True, repr=False)
class _MeetingSessionRecord:
    session_id: str
    poc_id: str
    prepared: _PreparedAdapterSession
    created_at: datetime
    updated_at: datetime
    state: MeetingSessionState = MeetingSessionState.SETUP
    consent: MeetingConsentAttestation | None = None
    consented_at: datetime | None = None
    start_attempt: _StartedAdapterSession | None = None
    start_attempt_at: datetime | None = None
    started: _StartedAdapterSession | None = None
    started_at: datetime | None = None
    draft_attempt_at: datetime | None = None
    draft_attempt_events: tuple[MeetingTranscriptEvent, ...] | None = None
    drafted_at: datetime | None = None
    source_receipt_id: str | None = None
    proposal_count: int | None = None
    orchestration_sha256: str | None = None

    def __repr__(self) -> str:
        return (
            "_MeetingSessionRecord(session_id={!r}, state={!r}, "
            "private_material=<redacted>)"
        ).format(self.session_id, self.state.value)


@dataclass(frozen=True, slots=True)
class _OperationRecord:
    action: str
    request_sha256: str
    result: MeetingSessionActionResult


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _read_clock(clock: Callable[[], datetime]) -> datetime:
    try:
        value = clock()
    except Exception:
        raise MeetingSessionError(
            MeetingSessionFailureCode.ADAPTER_FAILED
        ) from None
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise MeetingSessionError(MeetingSessionFailureCode.ADAPTER_FAILED)
    return value.astimezone(timezone.utc)


def _digest(domain: bytes, payload: Any) -> str:
    return hashlib.sha256(domain + canonical_json_bytes(payload)).hexdigest()


def _event_id(session_id: str, sequence: int) -> str:
    suffix = session_id.removeprefix("meetsess_")
    event_sha256 = _digest(
        _SYNTHETIC_EVENT_DOMAIN,
        {"session_id": session_id, "sequence": sequence},
    )
    return "mev_{}_{}".format(suffix[:24], event_sha256[:24])


def _idempotency_sha256(value: object) -> str:
    if type(value) is not str or _IDEMPOTENCY_KEY.fullmatch(value) is None:
        raise MeetingSessionError(MeetingSessionFailureCode.INVALID_REQUEST)
    return _digest(_IDEMPOTENCY_DOMAIN, {"idempotency_key": value})


def _request_sha256(action: str, payload: Mapping[str, Any]) -> str:
    return _digest(
        _OPERATION_DOMAIN,
        {"action": action, "payload": dict(payload)},
    )


class ProcessLocalMeetingSessionRuntime:
    """Thread-safe coordinator for the guided synthetic meeting flow."""

    __slots__ = (
        "_adapter",
        "_by_poc",
        "_clock",
        "_drafts",
        "_inbox",
        "_lock",
        "_max_sessions",
        "_operations",
        "_orchestration",
        "_sessions",
    )

    def __init__(
        self,
        *,
        drafts: ProcessLocalDraftPOCService,
        source_intake: ProcessLocalPOCSourceIntake,
        inbox: SQLiteMeetingEventInbox,
        adapter: MeetingSessionAdapter | None = None,
        clock: Callable[[], datetime] = _utc_now,
        max_sessions: int = 512,
    ) -> None:
        if type(drafts) is not ProcessLocalDraftPOCService:
            raise TypeError("drafts must be a ProcessLocalDraftPOCService.")
        if type(source_intake) is not ProcessLocalPOCSourceIntake:
            raise TypeError("source_intake must be a ProcessLocalPOCSourceIntake.")
        if type(inbox) is not SQLiteMeetingEventInbox:
            raise TypeError("inbox must be a SQLiteMeetingEventInbox.")
        if not callable(clock):
            raise TypeError("clock must be callable.")
        if type(max_sessions) is not int or not 1 <= max_sessions <= 10_000:
            raise ValueError("max_sessions is outside supported bounds.")
        selected_adapter = adapter or FixedSyntheticMeetingAdapter()
        if (
            type(getattr(selected_adapter, "descriptor", None))
            is not MeetingSessionAdapterDescriptor
            or not callable(getattr(selected_adapter, "prepare", None))
            or not callable(getattr(selected_adapter, "start", None))
            or not callable(getattr(selected_adapter, "draft_events", None))
        ):
            raise TypeError("adapter does not implement MeetingSessionAdapter.")
        self._drafts = drafts
        self._inbox = inbox
        self._adapter = selected_adapter
        self._clock = clock
        self._max_sessions = max_sessions
        self._lock = RLock()
        self._sessions: dict[str, _MeetingSessionRecord] = {}
        self._by_poc: dict[str, str] = {}
        self._operations: dict[str, _OperationRecord] = {}
        self._orchestration = MeetingInboxSourceOrchestrationService(
            inbox,
            source_intake,
            clock=clock,
        )

    @property
    def disclosure(self) -> MeetingSessionDisclosure:
        return MeetingSessionDisclosure(adapter=self._adapter.descriptor)

    def __len__(self) -> int:
        with self._lock:
            return len(self._sessions)

    def disclosure_for(self, poc_id: object) -> MeetingSessionDisclosure:
        self._require_active_meeting_draft(poc_id)
        return self.disclosure

    def create(
        self,
        *,
        poc_id: object,
        idempotency_key: object,
    ) -> MeetingSessionActionResult:
        validated_poc_id = self._require_active_meeting_draft(poc_id)
        key_sha256 = _idempotency_sha256(idempotency_key)
        request_sha256 = _request_sha256(
            "CREATE",
            {
                "adapter": self._adapter.descriptor.model_dump(mode="json"),
                "poc_id": validated_poc_id,
            },
        )
        with self._lock:
            replay = self._operation_replay(
                key_sha256,
                action="CREATE",
                request_sha256=request_sha256,
            )
            if replay is not None:
                return replay
            if validated_poc_id in self._by_poc:
                raise MeetingSessionError(
                    MeetingSessionFailureCode.INVALID_TRANSITION
                )
            if len(self._sessions) >= self._max_sessions:
                raise MeetingSessionError(
                    MeetingSessionFailureCode.CAPACITY_EXCEEDED
                )
            session_sha256 = _digest(
                _SESSION_DOMAIN,
                {
                    "idempotency_key_sha256": key_sha256,
                    "poc_id": validated_poc_id,
                    "request_sha256": request_sha256,
                },
            )
            session_id = "meetsess_" + session_sha256
            now = _read_clock(self._clock)
            try:
                prepared = self._adapter.prepare(
                    poc_id=validated_poc_id,
                    session_id=session_id,
                    now=now,
                )
            except Exception:
                raise MeetingSessionError(
                    MeetingSessionFailureCode.ADAPTER_FAILED
                ) from None
            if type(prepared) is not _PreparedAdapterSession:
                raise MeetingSessionError(
                    MeetingSessionFailureCode.ADAPTER_FAILED
                )
            record = _MeetingSessionRecord(
                session_id=session_id,
                poc_id=validated_poc_id,
                prepared=prepared,
                created_at=now,
                updated_at=now,
            )
            self._sessions[session_id] = record
            self._by_poc[validated_poc_id] = session_id
            result = MeetingSessionActionResult(
                session=self._snapshot(record),
                idempotent_replay=False,
            )
            self._operations[key_sha256] = _OperationRecord(
                action="CREATE",
                request_sha256=request_sha256,
                result=result,
            )
            return result

    def record_consent(
        self,
        *,
        poc_id: object,
        session_id: object,
        disclosure_id: object,
        recording_notice_acknowledged: object,
        all_participants_consented: object,
        synthetic_demo_acknowledged: object,
        idempotency_key: object,
    ) -> MeetingSessionActionResult:
        validated_poc_id = self._require_active_meeting_draft(poc_id)
        validated_session_id = self._require_session_id(session_id)
        if (
            type(disclosure_id) is not str
            or type(recording_notice_acknowledged) is not bool
            or type(all_participants_consented) is not bool
            or type(synthetic_demo_acknowledged) is not bool
        ):
            raise MeetingSessionError(
                MeetingSessionFailureCode.INVALID_REQUEST
            )
        payload = {
            "all_participants_consented": all_participants_consented,
            "disclosure_id": disclosure_id,
            "poc_id": validated_poc_id,
            "recording_notice_acknowledged": (
                recording_notice_acknowledged
            ),
            "session_id": validated_session_id,
            "synthetic_demo_acknowledged": synthetic_demo_acknowledged,
        }
        return self._mutate(
            action="CONSENT",
            poc_id=validated_poc_id,
            session_id=validated_session_id,
            idempotency_key=idempotency_key,
            payload=payload,
            mutation=lambda record: self._record_consent(
                record,
                disclosure_id=disclosure_id,
                recording_notice_acknowledged=(
                    recording_notice_acknowledged
                ),
                all_participants_consented=all_participants_consented,
                synthetic_demo_acknowledged=synthetic_demo_acknowledged,
            ),
        )

    def start(
        self,
        *,
        poc_id: object,
        session_id: object,
        idempotency_key: object,
    ) -> MeetingSessionActionResult:
        validated_poc_id = self._require_active_meeting_draft(poc_id)
        validated_session_id = self._require_session_id(session_id)
        return self._mutate(
            action="START",
            poc_id=validated_poc_id,
            session_id=validated_session_id,
            idempotency_key=idempotency_key,
            payload={
                "poc_id": validated_poc_id,
                "session_id": validated_session_id,
            },
            mutation=self._start,
        )

    def draft_now(
        self,
        *,
        poc_id: object,
        session_id: object,
        idempotency_key: object,
    ) -> MeetingSessionActionResult:
        validated_poc_id = self._require_active_meeting_draft(poc_id)
        validated_session_id = self._require_session_id(session_id)
        return self._mutate(
            action="DRAFT",
            poc_id=validated_poc_id,
            session_id=validated_session_id,
            idempotency_key=idempotency_key,
            payload={
                "poc_id": validated_poc_id,
                "session_id": validated_session_id,
            },
            mutation=self._draft_now,
        )

    def current(self, *, poc_id: object) -> MeetingSessionSnapshot:
        validated_poc_id = self._require_poc_id(poc_id)
        with self._lock:
            session_id = self._by_poc.get(validated_poc_id)
            if session_id is None:
                raise MeetingSessionError(
                    MeetingSessionFailureCode.SESSION_NOT_FOUND
                )
            return self._snapshot(self._sessions[session_id])

    def session(
        self,
        *,
        poc_id: object,
        session_id: object,
    ) -> MeetingSessionSnapshot:
        validated_poc_id = self._require_poc_id(poc_id)
        validated_session_id = self._require_session_id(session_id)
        with self._lock:
            return self._snapshot(
                self._session_record(validated_poc_id, validated_session_id)
            )

    def _mutate(
        self,
        *,
        action: str,
        poc_id: str,
        session_id: str,
        idempotency_key: object,
        payload: Mapping[str, Any],
        mutation: Callable[[_MeetingSessionRecord], None],
    ) -> MeetingSessionActionResult:
        key_sha256 = _idempotency_sha256(idempotency_key)
        request_sha256 = _request_sha256(action, payload)
        with self._lock:
            replay = self._operation_replay(
                key_sha256,
                action=action,
                request_sha256=request_sha256,
            )
            if replay is not None:
                return replay
            record = self._session_record(poc_id, session_id)
            mutation(record)
            result = MeetingSessionActionResult(
                session=self._snapshot(record),
                idempotent_replay=False,
            )
            self._operations[key_sha256] = _OperationRecord(
                action=action,
                request_sha256=request_sha256,
                result=result,
            )
            return result

    def _record_consent(
        self,
        record: _MeetingSessionRecord,
        *,
        disclosure_id: str,
        recording_notice_acknowledged: bool,
        all_participants_consented: bool,
        synthetic_demo_acknowledged: bool,
    ) -> None:
        if record.state is not MeetingSessionState.SETUP:
            raise MeetingSessionError(
                MeetingSessionFailureCode.INVALID_TRANSITION
            )
        if disclosure_id != MEETING_SESSION_DISCLOSURE_ID:
            raise MeetingSessionError(
                MeetingSessionFailureCode.DISCLOSURE_MISMATCH
            )
        if (
            recording_notice_acknowledged is not True
            or all_participants_consented is not True
            or synthetic_demo_acknowledged is not True
        ):
            raise MeetingSessionError(
                MeetingSessionFailureCode.CONSENT_REQUIRED
            )
        now = _read_clock(self._clock)
        session_sha256 = record.session_id.removeprefix("meetsess_")
        record.consent = MeetingConsentAttestation(
            attestation_id="consent_" + session_sha256,
            meeting_id=record.prepared.meeting_id,
            participant_ids=record.prepared.participant_ids,
            consented_participant_ids=record.prepared.participant_ids,
            recording_notice_acknowledged=True,
            consent_notice_sha256=_NOTICE_SHA256,
            state=STTConsentState.GRANTED,
            attested_by="employee:synthetic_demo",
            attested_at=now,
        )
        record.consented_at = now
        record.updated_at = now
        record.state = MeetingSessionState.READY

    def _start(self, record: _MeetingSessionRecord) -> None:
        if record.state is MeetingSessionState.SETUP:
            raise MeetingSessionError(
                MeetingSessionFailureCode.CONSENT_REQUIRED
            )
        if record.state is not MeetingSessionState.READY or record.consent is None:
            raise MeetingSessionError(
                MeetingSessionFailureCode.INVALID_TRANSITION
            )
        if record.start_attempt_at is None:
            record.start_attempt_at = _read_clock(self._clock)
        start_at = record.start_attempt_at
        try:
            if record.start_attempt is None:
                started = self._adapter.start(
                    poc_id=record.poc_id,
                    session_id=record.session_id,
                    prepared=record.prepared,
                    consent=record.consent,
                    now=start_at,
                )
                if type(started) is not _StartedAdapterSession:
                    raise TypeError
                record.start_attempt = started
            started = record.start_attempt
            for index, event in enumerate(started.initial_events, start=1):
                self._inbox.append(
                    ingress_idempotency_key=(
                        f"meeting-session:{record.session_id}:start:{index}"
                    ),
                    authorization=started.authorization,
                    binding=started.binding,
                    event=event,
                )
        except Exception:
            raise MeetingSessionError(
                MeetingSessionFailureCode.ADAPTER_FAILED
            ) from None
        record.started = started
        record.started_at = start_at
        record.updated_at = start_at
        record.state = MeetingSessionState.LIVE

    def _draft_now(self, record: _MeetingSessionRecord) -> None:
        if (
            record.state is not MeetingSessionState.LIVE
            or record.consent is None
            or record.started is None
        ):
            raise MeetingSessionError(
                MeetingSessionFailureCode.INVALID_TRANSITION
            )
        if record.draft_attempt_at is None:
            record.draft_attempt_at = _read_clock(self._clock)
        draft_at = record.draft_attempt_at
        try:
            if record.draft_attempt_events is None:
                events = self._adapter.draft_events(
                    session_id=record.session_id,
                    prepared=record.prepared,
                    started=record.started,
                    now=draft_at,
                )
                if (
                    type(events) is not tuple
                    or not events
                    or any(
                        type(event) is not MeetingTranscriptEvent
                        for event in events
                    )
                ):
                    raise TypeError
                record.draft_attempt_events = events
            events = record.draft_attempt_events
            for index, event in enumerate(events, start=1):
                self._inbox.append(
                    ingress_idempotency_key=(
                        f"meeting-session:{record.session_id}:draft:{index}"
                    ),
                    authorization=record.started.authorization,
                    binding=record.started.binding,
                    event=event,
                )
        except Exception:
            raise MeetingSessionError(
                MeetingSessionFailureCode.ADAPTER_FAILED
            ) from None

        try:
            completed = self._orchestration.finalize_source(
                authorization=record.started.authorization,
                binding=record.started.binding,
                consent=record.consent,
            )
        except MeetingSourceOrchestrationError:
            raise MeetingSessionError(
                MeetingSessionFailureCode.FINALIZATION_FAILED
            ) from None
        except Exception:
            raise MeetingSessionError(
                MeetingSessionFailureCode.FINALIZATION_FAILED
            ) from None

        source = completed.handoff_result.source_receipt
        record.source_receipt_id = source.source_receipt_id
        record.proposal_count = source.proposal_count
        record.orchestration_sha256 = completed.orchestration_sha256
        record.drafted_at = draft_at
        record.updated_at = draft_at
        record.state = MeetingSessionState.DRAFT_READY

    def _snapshot(self, record: _MeetingSessionRecord) -> MeetingSessionSnapshot:
        state_actions = {
            MeetingSessionState.SETUP: MeetingSessionNextAction.RECORD_CONSENT,
            MeetingSessionState.READY: MeetingSessionNextAction.START_CAPTURE,
            MeetingSessionState.LIVE: MeetingSessionNextAction.DRAFT_REQUIREMENTS,
            MeetingSessionState.DRAFT_READY: (
                MeetingSessionNextAction.REVIEW_REQUIREMENTS
            ),
        }
        drafted = record.state is MeetingSessionState.DRAFT_READY
        return MeetingSessionSnapshot(
            session_id=record.session_id,
            poc_id=record.poc_id,
            state=record.state,
            next_action=state_actions[record.state],
            adapter=self._adapter.descriptor,
            consent_recorded=record.consent is not None,
            transcript_capture_started=record.started is not None,
            draft_created=drafted,
            source_receipt_id=record.source_receipt_id,
            proposal_count=record.proposal_count,
            orchestration_sha256=record.orchestration_sha256,
            review_url=(
                f"/app/pocs/{record.poc_id}/review" if drafted else None
            ),
            review_state="NEEDS_REVIEW" if drafted else None,
            created_at=record.created_at,
            updated_at=record.updated_at,
            consented_at=record.consented_at,
            started_at=record.started_at,
            drafted_at=record.drafted_at,
        )

    def _operation_replay(
        self,
        key_sha256: str,
        *,
        action: str,
        request_sha256: str,
    ) -> MeetingSessionActionResult | None:
        prior = self._operations.get(key_sha256)
        if prior is None:
            return None
        if prior.action != action or prior.request_sha256 != request_sha256:
            raise MeetingSessionError(
                MeetingSessionFailureCode.IDEMPOTENCY_CONFLICT
            )
        return prior.result.model_copy(update={"idempotent_replay": True})

    def _session_record(
        self,
        poc_id: str,
        session_id: str,
    ) -> _MeetingSessionRecord:
        record = self._sessions.get(session_id)
        if record is None or record.poc_id != poc_id:
            raise MeetingSessionError(
                MeetingSessionFailureCode.SESSION_NOT_FOUND
            )
        return record

    def _require_active_meeting_draft(self, poc_id: object) -> str:
        validated = self._require_poc_id(poc_id)
        try:
            draft = self._drafts.get(validated)
        except (DraftPOCNotFound, TypeError, ValueError):
            raise MeetingSessionError(
                MeetingSessionFailureCode.DRAFT_UNAVAILABLE
            ) from None
        if draft.archive_state is not DraftPOCArchiveState.ACTIVE:
            raise MeetingSessionError(
                MeetingSessionFailureCode.DRAFT_UNAVAILABLE
            )
        if draft.first_source_choice is not FirstSourceChoice.MEETING:
            raise MeetingSessionError(
                MeetingSessionFailureCode.WRONG_SOURCE_TYPE
            )
        return validated

    @staticmethod
    def _require_poc_id(poc_id: object) -> str:
        if type(poc_id) is not str or _POC_ID.fullmatch(poc_id) is None:
            raise MeetingSessionError(
                MeetingSessionFailureCode.INVALID_REQUEST
            )
        return poc_id

    @staticmethod
    def _require_session_id(session_id: object) -> str:
        if (
            type(session_id) is not str
            or _SESSION_ID.fullmatch(session_id) is None
        ):
            raise MeetingSessionError(
                MeetingSessionFailureCode.INVALID_REQUEST
            )
        return session_id


__all__ = [
    "FixedSyntheticMeetingAdapter",
    "MEETING_SESSION_DISCLOSURE_ID",
    "MEETING_SESSION_MODE",
    "MEETING_SESSION_NOTICE",
    "MEETING_SESSION_VERSION",
    "MeetingSessionActionResult",
    "MeetingSessionAdapter",
    "MeetingSessionAdapterDescriptor",
    "MeetingSessionDisclosure",
    "MeetingSessionError",
    "MeetingSessionFailureCode",
    "MeetingSessionNextAction",
    "MeetingSessionSnapshot",
    "MeetingSessionState",
    "ProcessLocalMeetingSessionRuntime",
]
