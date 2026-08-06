"""Provider-neutral, fail-closed contract for synthetic meeting streams.

This module is the Wave 4 boundary between a future meeting transport and the
existing ExitSpec source pipeline.  It deliberately performs no OAuth, REST,
webhook, WebSocket, persistence, redaction, or source attachment.  A provider
adapter may supply only untrusted transcript events; it receives no agreement,
measurement, evidence, or verdict authority.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Iterator, Literal, Mapping, Never, Self, Sequence

from pydantic import ConfigDict, Field, ValidationError, field_validator, model_validator

from .canonical import canonical_json_bytes
from .models import FrozenExitSpecModel, SHA256_PATTERN
from .poc_creation import POC_ID_PATTERN
from .stt_boundary import MeetingConsentAttestation, STTConsentState


MEETING_CONNECTOR_VERSION = "exitspec-meeting-connector/1.0"
MEETING_CONNECTOR_AUTHORITY = "UNTRUSTED_SOURCE_ONLY"
MEETING_CONNECTOR_REVIEW_STATE = "NEEDS_REVIEW"
MEETING_CAPTURE_AUTHORITY = "SYNTHETIC_SOURCE_CAPTURE_ONLY"

_AUTHORIZATION_DOMAIN = b"exitspec-meeting-capture-authorization-v1\x00"
_CONSENT_DOMAIN = b"exitspec-meeting-consent-binding-v1\x00"
_EVENT_DOMAIN = b"exitspec-meeting-event-v1\x00"
_EVENT_STREAM_DOMAIN = b"exitspec-meeting-event-stream-v1\x00"
_MEETING_DOMAIN = b"exitspec-meeting-identity-v1\x00"
_PARTICIPANT_SET_DOMAIN = b"exitspec-meeting-participant-set-v1\x00"
_STREAM_DOMAIN = b"exitspec-meeting-stream-identity-v1\x00"

_ADAPTER_PATTERN = r"^[a-z][a-z0-9._-]{2,79}$"
_BINDING_ID_PATTERN = r"^meetbind_[a-f0-9]{64}$"
_EVENT_ID_PATTERN = r"^mev_[a-z0-9][a-z0-9_-]{2,95}$"
_IDENTITY_PATTERN = r"^[a-z][a-z0-9._:-]{1,127}$"
_MEETING_ID_PATTERN = r"^meeting_[a-z0-9][a-z0-9_-]{2,95}$"
_PARTICIPANT_ID_PATTERN = r"^participant_[a-z0-9][a-z0-9_-]{2,95}$"
_POLICY_ID_PATTERN = r"^meetpolicy_[a-z0-9][a-z0-9_-]{2,95}$"
_REQUEST_ID_PATTERN = r"^meetreq_[a-z0-9][a-z0-9_-]{2,95}$"
_STREAM_ID_PATTERN = r"^stream_[a-z0-9][a-z0-9_-]{2,95}$"

_MAX_PARTICIPANTS = 64
_MAX_TRANSCRIPT_CHARACTERS = 200_000
_MAX_TRANSCRIPT_SEGMENT_CHARACTERS = 4_000
_START_AUTHORIZATION_TTL = timedelta(minutes=5)


class MeetingEventKind(str, Enum):
    """Provider-neutral event kinds accepted by the first stream contract."""

    STREAM_STARTED = "STREAM_STARTED"
    PARTICIPANT_JOINED = "PARTICIPANT_JOINED"
    PARTICIPANT_LEFT = "PARTICIPANT_LEFT"
    TRANSCRIPT_SEGMENT = "TRANSCRIPT_SEGMENT"
    STREAM_STOPPED = "STREAM_STOPPED"


class MeetingConnectorFailureCode(str, Enum):
    """Stable, content-free refusal codes for capture and stream sealing."""

    POLICY_NOT_ACTIVE = "MEETING_POLICY_NOT_ACTIVE"
    POLICY_EXPIRED = "MEETING_POLICY_EXPIRED"
    REQUEST_EXPIRED = "MEETING_REQUEST_EXPIRED"
    TRANSPORT_UNVERIFIED = "MEETING_TRANSPORT_UNVERIFIED"
    BINDING_MISMATCH = "MEETING_BINDING_MISMATCH"
    CONSENT_REQUIRED = "MEETING_CONSENT_REQUIRED"
    CONSENT_INCOMPLETE = "MEETING_CONSENT_INCOMPLETE"
    CONSENT_REVOKED = "MEETING_CONSENT_REVOKED"
    CONSENT_NOTICE_MISMATCH = "MEETING_CONSENT_NOTICE_MISMATCH"
    PARTICIPANT_SET_CHANGED = "MEETING_PARTICIPANT_SET_CHANGED"
    EVENT_CONFLICT = "MEETING_EVENT_CONFLICT"
    EVENT_GAP = "MEETING_EVENT_GAP"
    STREAM_INCOMPLETE = "MEETING_STREAM_INCOMPLETE"
    LIMIT_EXCEEDED = "MEETING_LIMIT_EXCEEDED"
    TIMELINE_INVALID = "MEETING_TIMELINE_INVALID"


_FAILURE_DETAILS: dict[MeetingConnectorFailureCode, tuple[str, str]] = {
    MeetingConnectorFailureCode.POLICY_NOT_ACTIVE: (
        "The meeting connector policy is not active yet.",
        "review_meeting_connector_policy",
    ),
    MeetingConnectorFailureCode.POLICY_EXPIRED: (
        "The meeting connector policy has expired.",
        "renew_meeting_connector_policy",
    ),
    MeetingConnectorFailureCode.REQUEST_EXPIRED: (
        "The meeting capture request is outside its authorization window.",
        "request_meeting_capture_again",
    ),
    MeetingConnectorFailureCode.TRANSPORT_UNVERIFIED: (
        "The meeting transport has not established the required verification.",
        "verify_meeting_transport",
    ),
    MeetingConnectorFailureCode.BINDING_MISMATCH: (
        "The meeting stream does not match its capture authorization.",
        "review_meeting_stream_bindings",
    ),
    MeetingConnectorFailureCode.CONSENT_REQUIRED: (
        "Recorded participant consent is required before meeting capture.",
        "record_participant_consent",
    ),
    MeetingConnectorFailureCode.CONSENT_INCOMPLETE: (
        "Every participant in the approved meeting set must consent.",
        "resolve_participant_consent",
    ),
    MeetingConnectorFailureCode.CONSENT_REVOKED: (
        "Meeting capture is blocked because consent was revoked.",
        "stop_meeting_capture",
    ),
    MeetingConnectorFailureCode.CONSENT_NOTICE_MISMATCH: (
        "Meeting consent does not bind the current capture disclosure.",
        "record_consent_for_current_disclosure",
    ),
    MeetingConnectorFailureCode.PARTICIPANT_SET_CHANGED: (
        "The participant set changed after consent was recorded.",
        "stop_capture_and_record_consent_again",
    ),
    MeetingConnectorFailureCode.EVENT_CONFLICT: (
        "The meeting stream contains conflicting event identities or order.",
        "review_meeting_event_integrity",
    ),
    MeetingConnectorFailureCode.EVENT_GAP: (
        "The meeting stream is missing one or more canonical events.",
        "recover_missing_meeting_events",
    ),
    MeetingConnectorFailureCode.STREAM_INCOMPLETE: (
        "The meeting stream cannot be sealed without a complete lifecycle.",
        "finalize_the_meeting_stream",
    ),
    MeetingConnectorFailureCode.LIMIT_EXCEEDED: (
        "The meeting stream exceeds its approved processing limits.",
        "reduce_the_meeting_capture_window",
    ),
    MeetingConnectorFailureCode.TIMELINE_INVALID: (
        "The meeting stream timeline is invalid.",
        "restart_meeting_capture_after_consent",
    ),
}


class MeetingConnectorDenied(RuntimeError):
    """Sanitized refusal that grants no connector or source authority."""

    retryable = False

    def __init__(self, failure_code: MeetingConnectorFailureCode) -> None:
        self.failure_code = MeetingConnectorFailureCode(failure_code)
        self.code = self.failure_code.value
        safe_message, next_action = _FAILURE_DETAILS[self.failure_code]
        self.next_action = next_action
        super().__init__(safe_message)


class PrivateMeetingConnectorSerializationError(RuntimeError):
    """Refusal to serialize raw meeting identities or transcript text."""

    code = "private_meeting_connector_serialization_forbidden"

    def __init__(self) -> None:
        super().__init__(self.code)


class PrivateMeetingConnectorValidationError(ValueError):
    """Content-free validation failure for private meeting material."""

    code = "private_meeting_connector_validation_failed"

    def __init__(self) -> None:
        super().__init__(self.code)


class _FrozenMeetingModel(FrozenExitSpecModel):
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


class _PrivateMeetingModel(_FrozenMeetingModel):
    """Request-local meeting data whose ordinary inspection reveals no content."""

    def __init__(self, **data: Any) -> None:
        try:
            super().__init__(**data)
        except (
            ValidationError,
            PrivateMeetingConnectorValidationError,
            ValueError,
            TypeError,
        ):
            raise PrivateMeetingConnectorValidationError() from None

    @classmethod
    def model_validate(cls, obj: Any, *args: Any, **kwargs: Any) -> Self:
        try:
            return super().model_validate(obj, *args, **kwargs)
        except (
            ValidationError,
            PrivateMeetingConnectorValidationError,
            ValueError,
            TypeError,
        ):
            raise PrivateMeetingConnectorValidationError() from None

    @classmethod
    def model_validate_json(
        cls,
        json_data: str | bytes | bytearray,
        *args: Any,
        **kwargs: Any,
    ) -> Self:
        try:
            return super().model_validate_json(json_data, *args, **kwargs)
        except (
            ValidationError,
            PrivateMeetingConnectorValidationError,
            ValueError,
            TypeError,
        ):
            raise PrivateMeetingConnectorValidationError() from None

    def __repr__(self) -> str:
        return f"{type(self).__name__}(<private>)"

    def __str__(self) -> str:
        return repr(self)

    def __iter__(self) -> Iterator[tuple[str, Any]]:
        raise PrivateMeetingConnectorSerializationError()

    def __getstate__(self) -> Never:
        raise PrivateMeetingConnectorSerializationError()

    def __reduce__(self) -> Never:
        raise PrivateMeetingConnectorSerializationError()

    def __reduce_ex__(self, protocol: int) -> Never:
        raise PrivateMeetingConnectorSerializationError()

    def __copy__(self) -> Never:
        raise PrivateMeetingConnectorSerializationError()

    def __deepcopy__(self, memo: dict[int, Any]) -> Never:
        raise PrivateMeetingConnectorSerializationError()

    def dict(self, *args: Any, **kwargs: Any) -> Never:
        raise PrivateMeetingConnectorSerializationError()

    def json(self, *args: Any, **kwargs: Any) -> Never:
        raise PrivateMeetingConnectorSerializationError()

    def model_dump(self, *args: Any, **kwargs: Any) -> Never:
        raise PrivateMeetingConnectorSerializationError()

    def model_dump_json(self, *args: Any, **kwargs: Any) -> Never:
        raise PrivateMeetingConnectorSerializationError()

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Never:
        raise PrivateMeetingConnectorSerializationError()


def _aware_utc(value: datetime, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{field_name} must be timezone-aware.")
    return value.astimezone(timezone.utc)


def _digest(domain: bytes, payload: Any) -> str:
    return hashlib.sha256(domain + canonical_json_bytes(payload)).hexdigest()


def meeting_identity_sha256(meeting_id: str) -> str:
    if type(meeting_id) is not str or re.fullmatch(_MEETING_ID_PATTERN, meeting_id) is None:
        raise ValueError("meeting identity is invalid.")
    return _digest(_MEETING_DOMAIN, {"meeting_id": meeting_id})


def stream_identity_sha256(stream_id: str) -> str:
    if type(stream_id) is not str or re.fullmatch(_STREAM_ID_PATTERN, stream_id) is None:
        raise ValueError("stream identity is invalid.")
    return _digest(_STREAM_DOMAIN, {"stream_id": stream_id})


def participant_set_sha256(participant_ids: Sequence[str]) -> str:
    values = tuple(participant_ids)
    if (
        not values
        or len(values) > _MAX_PARTICIPANTS
        or len(values) != len(set(values))
        or any(re.fullmatch(_PARTICIPANT_ID_PATTERN, value) is None for value in values)
    ):
        raise ValueError("participant set is invalid.")
    return _digest(
        _PARTICIPANT_SET_DOMAIN,
        {"participant_ids": sorted(values)},
    )


def consent_attestation_sha256(consent: MeetingConsentAttestation) -> str:
    if type(consent) is not MeetingConsentAttestation:
        raise ValueError("consent attestation is invalid.")
    return _digest(_CONSENT_DOMAIN, consent.model_dump(mode="json"))


class MeetingConnectorPolicy(_FrozenMeetingModel):
    """Reviewed server policy for one synthetic meeting adapter."""

    schema_version: Literal[MEETING_CONNECTOR_VERSION] = MEETING_CONNECTOR_VERSION
    policy_id: str = Field(pattern=_POLICY_ID_PATTERN)
    policy_version: str = Field(pattern=_IDENTITY_PATTERN)
    provider: str = Field(pattern=_IDENTITY_PATTERN)
    adapter_id: str = Field(pattern=_ADAPTER_PATTERN)
    adapter_version: str = Field(pattern=_IDENTITY_PATTERN)
    consent_notice_sha256: str = Field(pattern=SHA256_PATTERN)
    max_event_count: int = Field(gt=2, le=20_000)
    max_transcript_characters: int = Field(
        gt=0,
        le=_MAX_TRANSCRIPT_CHARACTERS,
    )
    max_window_seconds: int = Field(gt=0, le=4 * 60 * 60)
    reviewed_at: datetime
    expires_at: datetime
    capture_authorization_ttl_seconds: int = Field(default=300, gt=0, le=300)
    synthetic_only: Literal[True] = True

    @field_validator("reviewed_at", "expires_at")
    @classmethod
    def validate_time(cls, value: datetime, info: Any) -> datetime:
        return _aware_utc(value, info.field_name)

    @model_validator(mode="after")
    def require_positive_policy_window(self) -> "MeetingConnectorPolicy":
        if self.expires_at <= self.reviewed_at:
            raise ValueError("expires_at must follow reviewed_at.")
        return self


class MeetingCaptureIntent(_PrivateMeetingModel):
    """Private request to authorize starting one exact synthetic capture."""

    request_id: str = Field(pattern=_REQUEST_ID_PATTERN)
    poc_id: str = Field(pattern=POC_ID_PATTERN)
    provider: str = Field(pattern=_IDENTITY_PATTERN)
    adapter_id: str = Field(pattern=_ADAPTER_PATTERN)
    adapter_version: str = Field(pattern=_IDENTITY_PATTERN)
    meeting_id: str = Field(pattern=_MEETING_ID_PATTERN, repr=False)
    organizer_participant_id: str = Field(
        pattern=_PARTICIPANT_ID_PATTERN,
        repr=False,
    )
    participant_ids: tuple[str, ...] = Field(
        min_length=1,
        max_length=_MAX_PARTICIPANTS,
        repr=False,
    )
    consent: MeetingConsentAttestation = Field(repr=False)
    requested_at: datetime
    synthetic_only: Literal[True] = True

    @field_validator("participant_ids", mode="before")
    @classmethod
    def normalize_participants(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value

    @field_validator("participant_ids")
    @classmethod
    def validate_participants(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        participant_set_sha256(value)
        return value

    @field_validator("requested_at")
    @classmethod
    def validate_request_time(cls, value: datetime) -> datetime:
        return _aware_utc(value, "requested_at")

    @model_validator(mode="after")
    def require_organizer_in_participant_set(self) -> "MeetingCaptureIntent":
        if self.organizer_participant_id not in self.participant_ids:
            raise ValueError("organizer must belong to the participant set.")
        return self


class MeetingCaptureAuthorization(_FrozenMeetingModel):
    """Content-free authority to start capture, never to accept a verdict."""

    schema_version: Literal[MEETING_CONNECTOR_VERSION] = MEETING_CONNECTOR_VERSION
    authorization_id: str = Field(pattern=r"^meetauth_[a-f0-9]{64}$")
    request_id: str = Field(pattern=_REQUEST_ID_PATTERN)
    poc_id: str = Field(pattern=POC_ID_PATTERN)
    provider: str = Field(pattern=_IDENTITY_PATTERN)
    adapter_id: str = Field(pattern=_ADAPTER_PATTERN)
    adapter_version: str = Field(pattern=_IDENTITY_PATTERN)
    meeting_identity_sha256: str = Field(pattern=SHA256_PATTERN)
    participant_set_sha256: str = Field(pattern=SHA256_PATTERN)
    consent_attestation_sha256: str = Field(pattern=SHA256_PATTERN)
    max_event_count: int = Field(gt=2, le=20_000)
    max_transcript_characters: int = Field(gt=0, le=_MAX_TRANSCRIPT_CHARACTERS)
    max_window_seconds: int = Field(gt=0, le=4 * 60 * 60)
    capture_authority: Literal[MEETING_CAPTURE_AUTHORITY] = MEETING_CAPTURE_AUTHORITY
    transcript_authority: Literal[MEETING_CONNECTOR_AUTHORITY] = (
        MEETING_CONNECTOR_AUTHORITY
    )
    review_state: Literal[MEETING_CONNECTOR_REVIEW_STATE] = (
        MEETING_CONNECTOR_REVIEW_STATE
    )
    may_confirm_contract: Literal[False] = False
    may_freeze_contract: Literal[False] = False
    may_start_measurement: Literal[False] = False
    may_assign_verdict: Literal[False] = False
    authorized_at: datetime
    start_by: datetime
    synthetic_only: Literal[True] = True

    @field_validator("authorized_at", "start_by")
    @classmethod
    def validate_time(cls, value: datetime, info: Any) -> datetime:
        return _aware_utc(value, info.field_name)

    @model_validator(mode="after")
    def require_positive_authorization_window(self) -> "MeetingCaptureAuthorization":
        if self.start_by <= self.authorized_at:
            raise ValueError("start_by must follow authorized_at.")
        return self


class MeetingTransportBinding(_FrozenMeetingModel):
    """Server-internal proof that one Zoom-like stream transport was verified."""

    schema_version: Literal[MEETING_CONNECTOR_VERSION] = MEETING_CONNECTOR_VERSION
    binding_id: str = Field(pattern=_BINDING_ID_PATTERN)
    authorization_id: str = Field(pattern=r"^meetauth_[a-f0-9]{64}$")
    provider: str = Field(pattern=_IDENTITY_PATTERN)
    adapter_id: str = Field(pattern=_ADAPTER_PATTERN)
    adapter_version: str = Field(pattern=_IDENTITY_PATTERN)
    meeting_identity_sha256: str = Field(pattern=SHA256_PATTERN)
    stream_identity_sha256: str = Field(pattern=SHA256_PATTERN)
    webhook_event_sha256: str = Field(pattern=SHA256_PATTERN)
    webhook_signature_verified: Literal[True] = True
    websocket_handshake_authenticated: Literal[True] = True
    protocol_version: str = Field(pattern=_IDENTITY_PATTERN)
    established_at: datetime
    expires_at: datetime
    synthetic_only: Literal[True] = True

    @field_validator("established_at", "expires_at")
    @classmethod
    def validate_time(cls, value: datetime, info: Any) -> datetime:
        return _aware_utc(value, info.field_name)

    @model_validator(mode="after")
    def require_positive_binding_window(self) -> "MeetingTransportBinding":
        if self.expires_at <= self.established_at:
            raise ValueError("expires_at must follow established_at.")
        return self


class MeetingTranscriptEvent(_PrivateMeetingModel):
    """One private provider-neutral event from an authenticated stream."""

    schema_version: Literal[MEETING_CONNECTOR_VERSION] = MEETING_CONNECTOR_VERSION
    event_id: str = Field(pattern=_EVENT_ID_PATTERN)
    adapter_id: str = Field(pattern=_ADAPTER_PATTERN)
    adapter_version: str = Field(pattern=_IDENTITY_PATTERN)
    meeting_id: str = Field(pattern=_MEETING_ID_PATTERN, repr=False)
    stream_id: str = Field(pattern=_STREAM_ID_PATTERN, repr=False)
    transport_binding_id: str = Field(pattern=_BINDING_ID_PATTERN)
    sequence: int = Field(gt=0, le=20_000)
    kind: MeetingEventKind
    received_at: datetime
    participant_ids: tuple[str, ...] = Field(default=(), repr=False)
    participant_id: str | None = Field(
        default=None,
        pattern=_PARTICIPANT_ID_PATTERN,
        repr=False,
    )
    participant_label: str | None = Field(default=None, max_length=160, repr=False)
    transcript_text: str | None = Field(
        default=None,
        max_length=_MAX_TRANSCRIPT_SEGMENT_CHARACTERS,
        repr=False,
    )
    provider_timestamp_ms: int | None = Field(default=None, ge=0)
    segment_start_ms: int | None = Field(default=None, ge=0)
    segment_end_ms: int | None = Field(default=None, ge=0)
    stop_reason: str | None = Field(
        default=None,
        pattern=r"^[a-z][a-z0-9._-]{2,79}$",
    )
    authority: Literal[MEETING_CONNECTOR_AUTHORITY] = MEETING_CONNECTOR_AUTHORITY
    review_state: Literal[MEETING_CONNECTOR_REVIEW_STATE] = (
        MEETING_CONNECTOR_REVIEW_STATE
    )
    synthetic_only: Literal[True] = True

    @field_validator("participant_ids", mode="before")
    @classmethod
    def normalize_participants(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value

    @field_validator("received_at")
    @classmethod
    def validate_received_at(cls, value: datetime) -> datetime:
        return _aware_utc(value, "received_at")

    @field_validator("participant_label", "transcript_text")
    @classmethod
    def validate_private_text(cls, value: str | None, info: Any) -> str | None:
        if value is None:
            return None
        if value != unicodedata.normalize("NFC", value) or "\r" in value:
            raise ValueError(f"{info.field_name} must be normalized text.")
        if not value.strip():
            raise ValueError(f"{info.field_name} must contain text.")
        return value

    @model_validator(mode="after")
    def require_exact_fields_for_kind(self) -> "MeetingTranscriptEvent":
        snapshot = bool(self.participant_ids)
        has_participant = self.participant_id is not None
        has_participant_label = self.participant_label is not None
        has_transcript = self.transcript_text is not None
        has_timing = any(
            value is not None
            for value in (
                self.provider_timestamp_ms,
                self.segment_start_ms,
                self.segment_end_ms,
            )
        )
        has_stop_reason = self.stop_reason is not None

        if self.kind == MeetingEventKind.STREAM_STARTED:
            if (
                not snapshot
                or has_participant
                or has_participant_label
                or has_transcript
                or has_timing
                or has_stop_reason
            ):
                raise ValueError("stream start requires only a participant snapshot.")
            participant_set_sha256(self.participant_ids)
        elif self.kind in {
            MeetingEventKind.PARTICIPANT_JOINED,
            MeetingEventKind.PARTICIPANT_LEFT,
        }:
            if (
                snapshot
                or not has_participant
                or has_participant_label
                or has_transcript
                or has_timing
                or has_stop_reason
            ):
                raise ValueError("participant events require only participant_id.")
        elif self.kind == MeetingEventKind.TRANSCRIPT_SEGMENT:
            if snapshot or not has_participant or not has_transcript or has_stop_reason:
                raise ValueError("transcript events require participant and text.")
            if self.segment_start_ms is None or self.segment_end_ms is None:
                raise ValueError("transcript events require exact segment bounds.")
            if self.segment_end_ms <= self.segment_start_ms:
                raise ValueError("segment_end_ms must follow segment_start_ms.")
        elif self.kind == MeetingEventKind.STREAM_STOPPED:
            if (
                snapshot
                or has_participant
                or has_participant_label
                or has_transcript
                or has_timing
                or not has_stop_reason
            ):
                raise ValueError("stream stop requires only a stop reason.")
        return self

    def fingerprint_sha256(self) -> str:
        payload = {
            "adapter_id": self.adapter_id,
            "adapter_version": self.adapter_version,
            "authority": self.authority,
            "event_id": self.event_id,
            "kind": self.kind.value,
            "meeting_id": self.meeting_id,
            "participant_id": self.participant_id,
            "participant_ids": list(self.participant_ids),
            "participant_label": self.participant_label,
            "provider_timestamp_ms": self.provider_timestamp_ms,
            "received_at": self.received_at.isoformat(),
            "review_state": self.review_state,
            "segment_end_ms": self.segment_end_ms,
            "segment_start_ms": self.segment_start_ms,
            "sequence": self.sequence,
            "stop_reason": self.stop_reason,
            "stream_id": self.stream_id,
            "synthetic_only": self.synthetic_only,
            "transcript_text": self.transcript_text,
            "transport_binding_id": self.transport_binding_id,
        }
        return _digest(_EVENT_DOMAIN, payload)


class MeetingTranscriptWindowReceipt(_FrozenMeetingModel):
    """Content-free receipt for one sealed, still-untrusted transcript window."""

    schema_version: Literal[MEETING_CONNECTOR_VERSION] = MEETING_CONNECTOR_VERSION
    authorization_id: str = Field(pattern=r"^meetauth_[a-f0-9]{64}$")
    request_id: str = Field(pattern=_REQUEST_ID_PATTERN)
    poc_id: str = Field(pattern=POC_ID_PATTERN)
    provider: str = Field(pattern=_IDENTITY_PATTERN)
    adapter_id: str = Field(pattern=_ADAPTER_PATTERN)
    adapter_version: str = Field(pattern=_IDENTITY_PATTERN)
    meeting_identity_sha256: str = Field(pattern=SHA256_PATTERN)
    stream_identity_sha256: str = Field(pattern=SHA256_PATTERN)
    participant_set_sha256: str = Field(pattern=SHA256_PATTERN)
    event_stream_sha256: str = Field(pattern=SHA256_PATTERN)
    unique_event_count: int = Field(gt=2, le=20_000)
    duplicate_event_count: int = Field(ge=0, le=20_000)
    segment_count: int = Field(gt=0, le=20_000)
    transcript_character_count: int = Field(gt=0, le=_MAX_TRANSCRIPT_CHARACTERS)
    started_at: datetime
    stopped_at: datetime
    transcript_authority: Literal[MEETING_CONNECTOR_AUTHORITY] = (
        MEETING_CONNECTOR_AUTHORITY
    )
    review_state: Literal[MEETING_CONNECTOR_REVIEW_STATE] = (
        MEETING_CONNECTOR_REVIEW_STATE
    )
    may_confirm_contract: Literal[False] = False
    may_freeze_contract: Literal[False] = False
    may_start_measurement: Literal[False] = False
    may_assign_verdict: Literal[False] = False
    raw_audio_received: Literal[False] = False
    raw_transcript_persisted: Literal[False] = False
    synthetic_only: Literal[True] = True

    @field_validator("started_at", "stopped_at")
    @classmethod
    def validate_time(cls, value: datetime, info: Any) -> datetime:
        return _aware_utc(value, info.field_name)

    @model_validator(mode="after")
    def require_positive_window(self) -> "MeetingTranscriptWindowReceipt":
        if self.stopped_at < self.started_at:
            raise ValueError("stopped_at cannot precede started_at.")
        return self


class SealedMeetingTranscript(_PrivateMeetingModel):
    """Private sealed transcript awaiting immediate redaction and source handoff."""

    authorization_id: str = Field(pattern=r"^meetauth_[a-f0-9]{64}$")
    request_id: str = Field(pattern=_REQUEST_ID_PATTERN)
    poc_id: str = Field(pattern=POC_ID_PATTERN)
    meeting_id: str = Field(pattern=_MEETING_ID_PATTERN, repr=False)
    stream_id: str = Field(pattern=_STREAM_ID_PATTERN, repr=False)
    receipt: MeetingTranscriptWindowReceipt
    segments: tuple[MeetingTranscriptEvent, ...] = Field(
        min_length=1,
        max_length=20_000,
        repr=False,
    )

    @field_validator("segments", mode="before")
    @classmethod
    def normalize_segments(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def require_receipt_binding(self) -> "SealedMeetingTranscript":
        if (
            self.receipt.authorization_id != self.authorization_id
            or self.receipt.request_id != self.request_id
            or self.receipt.poc_id != self.poc_id
            or self.receipt.meeting_identity_sha256
            != meeting_identity_sha256(self.meeting_id)
            or self.receipt.stream_identity_sha256
            != stream_identity_sha256(self.stream_id)
            or self.receipt.segment_count != len(self.segments)
        ):
            raise ValueError("sealed transcript does not match its receipt.")
        return self

    def transient_redaction_input(self) -> str:
        """Return neutral speaker text only for the immediate source handoff."""

        neutral_speakers: dict[str, str] = {}
        lines: list[str] = []
        for segment in self.segments:
            speaker = neutral_speakers.setdefault(
                segment.participant_id or "participant_unknown",
                f"Speaker {len(neutral_speakers) + 1}",
            )
            text = " ".join((segment.transcript_text or "").split())
            lines.append(f"{speaker}: {text}")
        return "\n".join(lines)


def authorize_meeting_capture(
    policy: MeetingConnectorPolicy,
    intent: MeetingCaptureIntent,
    *,
    now: datetime,
) -> MeetingCaptureAuthorization:
    """Authorize only the start of one consent-bound synthetic capture."""

    try:
        current_time = _aware_utc(now, "now")
    except (TypeError, ValueError):
        raise MeetingConnectorDenied(
            MeetingConnectorFailureCode.TIMELINE_INVALID
        ) from None

    if type(policy) is not MeetingConnectorPolicy or type(intent) is not MeetingCaptureIntent:
        raise MeetingConnectorDenied(MeetingConnectorFailureCode.BINDING_MISMATCH)
    if current_time < policy.reviewed_at:
        raise MeetingConnectorDenied(MeetingConnectorFailureCode.POLICY_NOT_ACTIVE)
    if current_time >= policy.expires_at:
        raise MeetingConnectorDenied(MeetingConnectorFailureCode.POLICY_EXPIRED)
    if intent.requested_at > current_time or current_time - intent.requested_at > _START_AUTHORIZATION_TTL:
        raise MeetingConnectorDenied(MeetingConnectorFailureCode.REQUEST_EXPIRED)
    if (
        intent.provider != policy.provider
        or intent.adapter_id != policy.adapter_id
        or intent.adapter_version != policy.adapter_version
    ):
        raise MeetingConnectorDenied(MeetingConnectorFailureCode.BINDING_MISMATCH)

    consent = intent.consent
    if consent.state == STTConsentState.REVOKED:
        raise MeetingConnectorDenied(MeetingConnectorFailureCode.CONSENT_REVOKED)
    if not consent.recording_notice_acknowledged:
        raise MeetingConnectorDenied(MeetingConnectorFailureCode.CONSENT_REQUIRED)
    if consent.consent_notice_sha256 != policy.consent_notice_sha256:
        raise MeetingConnectorDenied(
            MeetingConnectorFailureCode.CONSENT_NOTICE_MISMATCH
        )
    if consent.meeting_id != intent.meeting_id:
        raise MeetingConnectorDenied(MeetingConnectorFailureCode.BINDING_MISMATCH)
    if (
        set(consent.participant_ids) != set(intent.participant_ids)
        or set(consent.consented_participant_ids) != set(intent.participant_ids)
        or consent.state != STTConsentState.GRANTED
    ):
        raise MeetingConnectorDenied(MeetingConnectorFailureCode.CONSENT_INCOMPLETE)
    if consent.attested_at > intent.requested_at:
        raise MeetingConnectorDenied(MeetingConnectorFailureCode.TIMELINE_INVALID)

    projection = {
        "adapter_id": intent.adapter_id,
        "adapter_version": intent.adapter_version,
        "consent_attestation_sha256": consent_attestation_sha256(consent),
        "meeting_identity_sha256": meeting_identity_sha256(intent.meeting_id),
        "participant_set_sha256": participant_set_sha256(intent.participant_ids),
        "poc_id": intent.poc_id,
        "policy_id": policy.policy_id,
        "policy_version": policy.policy_version,
        "provider": intent.provider,
        "request_id": intent.request_id,
        "requested_at": intent.requested_at.isoformat(),
    }
    authorization_id = "meetauth_" + _digest(_AUTHORIZATION_DOMAIN, projection)
    ttl = timedelta(seconds=policy.capture_authorization_ttl_seconds)
    return MeetingCaptureAuthorization(
        authorization_id=authorization_id,
        request_id=intent.request_id,
        poc_id=intent.poc_id,
        provider=intent.provider,
        adapter_id=intent.adapter_id,
        adapter_version=intent.adapter_version,
        meeting_identity_sha256=projection["meeting_identity_sha256"],
        participant_set_sha256=projection["participant_set_sha256"],
        consent_attestation_sha256=projection["consent_attestation_sha256"],
        max_event_count=policy.max_event_count,
        max_transcript_characters=policy.max_transcript_characters,
        max_window_seconds=policy.max_window_seconds,
        authorized_at=current_time,
        start_by=min(current_time + ttl, policy.expires_at),
    )


def _require_binding(
    authorization: MeetingCaptureAuthorization,
    binding: MeetingTransportBinding,
) -> None:
    if not binding.webhook_signature_verified or not binding.websocket_handshake_authenticated:
        raise MeetingConnectorDenied(
            MeetingConnectorFailureCode.TRANSPORT_UNVERIFIED
        )
    if (
        binding.authorization_id != authorization.authorization_id
        or binding.provider != authorization.provider
        or binding.adapter_id != authorization.adapter_id
        or binding.adapter_version != authorization.adapter_version
        or binding.meeting_identity_sha256
        != authorization.meeting_identity_sha256
    ):
        raise MeetingConnectorDenied(MeetingConnectorFailureCode.BINDING_MISMATCH)
    if not (
        authorization.authorized_at
        <= binding.established_at
        <= authorization.start_by
    ):
        raise MeetingConnectorDenied(MeetingConnectorFailureCode.REQUEST_EXPIRED)


def seal_meeting_transcript_window(
    authorization: MeetingCaptureAuthorization,
    binding: MeetingTransportBinding,
    consent: MeetingConsentAttestation,
    events: Sequence[MeetingTranscriptEvent],
    *,
    now: datetime,
) -> SealedMeetingTranscript:
    """Validate, deduplicate, order, and seal one untrusted transcript window."""

    if (
        type(authorization) is not MeetingCaptureAuthorization
        or type(binding) is not MeetingTransportBinding
        or type(consent) is not MeetingConsentAttestation
    ):
        raise MeetingConnectorDenied(MeetingConnectorFailureCode.BINDING_MISMATCH)
    try:
        current_time = _aware_utc(now, "now")
    except (TypeError, ValueError):
        raise MeetingConnectorDenied(
            MeetingConnectorFailureCode.TIMELINE_INVALID
        ) from None

    _require_binding(authorization, binding)
    if consent.state == STTConsentState.REVOKED:
        raise MeetingConnectorDenied(MeetingConnectorFailureCode.CONSENT_REVOKED)
    if consent_attestation_sha256(consent) != authorization.consent_attestation_sha256:
        raise MeetingConnectorDenied(MeetingConnectorFailureCode.BINDING_MISMATCH)
    if participant_set_sha256(consent.participant_ids) != authorization.participant_set_sha256:
        raise MeetingConnectorDenied(
            MeetingConnectorFailureCode.PARTICIPANT_SET_CHANGED
        )

    try:
        supplied_events = tuple(events)
    except (TypeError, ValueError):
        raise MeetingConnectorDenied(MeetingConnectorFailureCode.EVENT_CONFLICT) from None
    if len(supplied_events) > authorization.max_event_count:
        raise MeetingConnectorDenied(MeetingConnectorFailureCode.LIMIT_EXCEEDED)
    if any(type(event) is not MeetingTranscriptEvent for event in supplied_events):
        raise MeetingConnectorDenied(MeetingConnectorFailureCode.EVENT_CONFLICT)

    by_event_id: dict[str, tuple[str, MeetingTranscriptEvent]] = {}
    duplicate_count = 0
    for event in supplied_events:
        fingerprint = event.fingerprint_sha256()
        prior = by_event_id.get(event.event_id)
        if prior is None:
            by_event_id[event.event_id] = (fingerprint, event)
        elif prior[0] == fingerprint:
            duplicate_count += 1
        else:
            raise MeetingConnectorDenied(MeetingConnectorFailureCode.EVENT_CONFLICT)

    unique_events = sorted(
        (entry[1] for entry in by_event_id.values()),
        key=lambda event: event.sequence,
    )
    if len(unique_events) < 3:
        raise MeetingConnectorDenied(MeetingConnectorFailureCode.STREAM_INCOMPLETE)
    if len(unique_events) > authorization.max_event_count:
        raise MeetingConnectorDenied(MeetingConnectorFailureCode.LIMIT_EXCEEDED)
    sequences = [event.sequence for event in unique_events]
    if len(sequences) != len(set(sequences)):
        raise MeetingConnectorDenied(MeetingConnectorFailureCode.EVENT_CONFLICT)
    if sequences != list(range(1, len(unique_events) + 1)):
        raise MeetingConnectorDenied(MeetingConnectorFailureCode.EVENT_GAP)

    start_events = [
        event
        for event in unique_events
        if event.kind == MeetingEventKind.STREAM_STARTED
    ]
    stop_events = [
        event
        for event in unique_events
        if event.kind == MeetingEventKind.STREAM_STOPPED
    ]
    if (
        len(start_events) != 1
        or len(stop_events) != 1
        or unique_events[0] is not start_events[0]
        or unique_events[-1] is not stop_events[0]
    ):
        raise MeetingConnectorDenied(MeetingConnectorFailureCode.STREAM_INCOMPLETE)

    expected_meeting_sha256 = authorization.meeting_identity_sha256
    expected_stream_sha256 = binding.stream_identity_sha256
    previous_received_at = binding.established_at
    for event in unique_events:
        if (
            event.adapter_id != authorization.adapter_id
            or event.adapter_version != authorization.adapter_version
            or event.transport_binding_id != binding.binding_id
            or meeting_identity_sha256(event.meeting_id)
            != expected_meeting_sha256
            or stream_identity_sha256(event.stream_id) != expected_stream_sha256
        ):
            raise MeetingConnectorDenied(MeetingConnectorFailureCode.BINDING_MISMATCH)
        if event.received_at < previous_received_at:
            raise MeetingConnectorDenied(MeetingConnectorFailureCode.TIMELINE_INVALID)
        previous_received_at = event.received_at

    started = start_events[0]
    stopped = stop_events[0]
    if (
        set(started.participant_ids) != set(consent.participant_ids)
        or any(
            event.kind
            in {
                MeetingEventKind.PARTICIPANT_JOINED,
                MeetingEventKind.PARTICIPANT_LEFT,
            }
            for event in unique_events
        )
    ):
        raise MeetingConnectorDenied(
            MeetingConnectorFailureCode.PARTICIPANT_SET_CHANGED
        )
    if stopped.received_at > current_time or stopped.received_at > binding.expires_at:
        raise MeetingConnectorDenied(MeetingConnectorFailureCode.TIMELINE_INVALID)
    if (stopped.received_at - started.received_at).total_seconds() > authorization.max_window_seconds:
        raise MeetingConnectorDenied(MeetingConnectorFailureCode.LIMIT_EXCEEDED)

    transcript_events = tuple(
        event
        for event in unique_events
        if event.kind == MeetingEventKind.TRANSCRIPT_SEGMENT
    )
    if not transcript_events:
        raise MeetingConnectorDenied(MeetingConnectorFailureCode.STREAM_INCOMPLETE)
    if any(
        event.participant_id not in consent.participant_ids
        for event in transcript_events
    ):
        raise MeetingConnectorDenied(
            MeetingConnectorFailureCode.PARTICIPANT_SET_CHANGED
        )
    transcript_character_count = sum(
        len(event.transcript_text or "") for event in transcript_events
    )
    if transcript_character_count > authorization.max_transcript_characters:
        raise MeetingConnectorDenied(MeetingConnectorFailureCode.LIMIT_EXCEEDED)

    fingerprints = [event.fingerprint_sha256() for event in unique_events]
    event_stream_sha256 = _digest(
        _EVENT_STREAM_DOMAIN,
        {"event_fingerprints": fingerprints},
    )
    receipt = MeetingTranscriptWindowReceipt(
        authorization_id=authorization.authorization_id,
        request_id=authorization.request_id,
        poc_id=authorization.poc_id,
        provider=authorization.provider,
        adapter_id=authorization.adapter_id,
        adapter_version=authorization.adapter_version,
        meeting_identity_sha256=expected_meeting_sha256,
        stream_identity_sha256=expected_stream_sha256,
        participant_set_sha256=authorization.participant_set_sha256,
        event_stream_sha256=event_stream_sha256,
        unique_event_count=len(unique_events),
        duplicate_event_count=duplicate_count,
        segment_count=len(transcript_events),
        transcript_character_count=transcript_character_count,
        started_at=started.received_at,
        stopped_at=stopped.received_at,
    )
    return SealedMeetingTranscript(
        authorization_id=authorization.authorization_id,
        request_id=authorization.request_id,
        poc_id=authorization.poc_id,
        meeting_id=consent.meeting_id,
        stream_id=unique_events[0].stream_id,
        receipt=receipt,
        segments=transcript_events,
    )


__all__ = [
    "MEETING_CAPTURE_AUTHORITY",
    "MEETING_CONNECTOR_AUTHORITY",
    "MEETING_CONNECTOR_REVIEW_STATE",
    "MEETING_CONNECTOR_VERSION",
    "MeetingCaptureAuthorization",
    "MeetingCaptureIntent",
    "MeetingConnectorDenied",
    "MeetingConnectorFailureCode",
    "MeetingConnectorPolicy",
    "MeetingEventKind",
    "MeetingTranscriptEvent",
    "MeetingTranscriptWindowReceipt",
    "MeetingTransportBinding",
    "PrivateMeetingConnectorSerializationError",
    "PrivateMeetingConnectorValidationError",
    "SealedMeetingTranscript",
    "authorize_meeting_capture",
    "consent_attestation_sha256",
    "meeting_identity_sha256",
    "participant_set_sha256",
    "seal_meeting_transcript_window",
    "stream_identity_sha256",
]
