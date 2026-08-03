"""Provider-neutral, fail-closed boundary for future speech-to-text work.

This module deliberately stops before raw audio transport.  It validates one
synthetic-audio egress intent against one reviewed policy and returns a safe
authorization record, not a network capability.  Provider transcript content
is request-local, non-serializable, and permanently untrusted; it cannot
approve a proposal, confirm or freeze a contract, run a measurement, or assign
a verdict.
"""

from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Iterator, Literal, Mapping, Never, Self

from pydantic import (
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from .canonical import canonical_json_bytes
from .models import SHA256_PATTERN, FrozenExitSpecModel
from .poc_creation import POC_ID_PATTERN


STT_BOUNDARY_VERSION = "exitspec-stt-boundary/1.0"
STT_CONSENT_SCOPE = "POC_REQUIREMENTS_TRANSCRIPTION"
STT_TRANSCRIPT_AUTHORITY = "UNTRUSTED_SOURCE_ONLY"
STT_REVIEW_STATE = "NEEDS_REVIEW"
STT_EGRESS_AUTHORITY = "AUDIO_EGRESS_POLICY_MATCH_ONLY"

_AUTHORIZATION_DOMAIN = b"exitspec-stt-authorization-v1\x00"
_CONSENT_DOMAIN = b"exitspec-stt-consent-v1\x00"
_MEETING_IDENTITY_DOMAIN = b"exitspec-stt-meeting-identity-v1\x00"
_IDENTITY_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.:/+-]{1,199}$"
_REQUEST_ID_PATTERN = r"^sttreq_[a-z0-9][a-z0-9_-]{2,95}$"
_POLICY_ID_PATTERN = r"^stt_policy_[a-z0-9][a-z0-9_-]{2,95}$"
_ATTESTATION_ID_PATTERN = r"^consent_[a-z0-9][a-z0-9_-]{2,95}$"
_MEETING_ID_PATTERN = r"^meeting_[a-z0-9][a-z0-9_-]{2,95}$"
_PARTICIPANT_ID_PATTERN = r"^participant_[a-z0-9][a-z0-9_-]{2,95}$"
_AUTHORIZATION_ID_PATTERN = r"^sttauth_[a-f0-9]{64}$"
_OPERATION_ID_PATTERN = r"^sttop_[a-f0-9]{64}$"
_SEGMENT_ID_PATTERN = r"^segment_[a-z0-9][a-z0-9_-]{2,95}$"
_SOURCE_RECEIPT_ID_PATTERN = r"^srcpt_[a-z0-9][a-z0-9_-]{7,95}$"
_POLICY_REFERENCE_PATTERN = r"^[a-z][a-z0-9._:/+-]{2,199}$"
_MEDIA_TYPE_PATTERN = re.compile(r"^audio/[a-z0-9][a-z0-9.+-]{0,63}$")
_LANGUAGE_PATTERN = r"^[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*$"
_MAX_PARTICIPANTS = 64
_MAX_MEDIA_TYPES = 16
_MAX_TRANSCRIPT_SEGMENTS = 10_000
_MAX_TRANSCRIPT_SEGMENT_CHARACTERS = 8_000
_MAX_POLICY_AGE = timedelta(days=90)


class STTRetentionMode(str, Enum):
    """The first boundary permits only provider-asserted zero retention."""

    ZERO_RETENTION = "ZERO_RETENTION"
    EPHEMERAL = "EPHEMERAL"
    PROVIDER_DEFAULT = "PROVIDER_DEFAULT"


class STTConsentState(str, Enum):
    """Current state of one exact meeting consent attestation."""

    GRANTED = "GRANTED"
    REVOKED = "REVOKED"


class STTSpeakerMappingState(str, Enum):
    """Speaker labels from STT are never treated as verified identity."""

    NOT_PROVIDED = "NOT_PROVIDED"
    PROVIDER_ASSIGNED_UNVERIFIED = "PROVIDER_ASSIGNED_UNVERIFIED"


class STTFailureCode(str, Enum):
    """Content-free denial categories at the pre-transport boundary."""

    INVALID_REQUEST = "STT_INVALID_REQUEST"
    POLICY_NOT_ACTIVE = "STT_POLICY_NOT_ACTIVE"
    POLICY_EXPIRED = "STT_POLICY_EXPIRED"
    REQUEST_EXPIRED = "STT_REQUEST_EXPIRED"
    MEETING_IDENTITY_MISMATCH = "STT_MEETING_IDENTITY_MISMATCH"
    CONSENT_REQUIRED = "STT_CONSENT_REQUIRED"
    CONSENT_INCOMPLETE = "STT_CONSENT_INCOMPLETE"
    CONSENT_REVOKED = "STT_CONSENT_REVOKED"
    CONSENT_NOTICE_MISMATCH = "STT_CONSENT_NOTICE_MISMATCH"
    TIMELINE_INVALID = "STT_TIMELINE_INVALID"
    PROVIDER_NOT_ALLOWED = "STT_PROVIDER_NOT_ALLOWED"
    MODEL_NOT_ALLOWED = "STT_MODEL_NOT_ALLOWED"
    REGION_NOT_ALLOWED = "STT_REGION_NOT_ALLOWED"
    RETENTION_NOT_ALLOWED = "STT_RETENTION_NOT_ALLOWED"
    MEDIA_TYPE_NOT_ALLOWED = "STT_MEDIA_TYPE_NOT_ALLOWED"
    AUDIO_TOO_LARGE = "STT_AUDIO_TOO_LARGE"
    AUDIO_TOO_LONG = "STT_AUDIO_TOO_LONG"


_FAILURE_DETAILS: dict[STTFailureCode, tuple[str, str]] = {
    STTFailureCode.INVALID_REQUEST: (
        "The speech-to-text request was not accepted.",
        "correct_request_metadata",
    ),
    STTFailureCode.POLICY_NOT_ACTIVE: (
        "The reviewed speech-to-text policy is not active yet.",
        "review_stt_policy",
    ),
    STTFailureCode.POLICY_EXPIRED: (
        "The reviewed speech-to-text policy has expired.",
        "refresh_stt_policy",
    ),
    STTFailureCode.REQUEST_EXPIRED: (
        "The speech-to-text request is no longer current.",
        "start_a_new_stt_request",
    ),
    STTFailureCode.MEETING_IDENTITY_MISMATCH: (
        "The audio, consent, and request do not name the same meeting.",
        "reconcile_meeting_identity",
    ),
    STTFailureCode.CONSENT_REQUIRED: (
        "Recorded consent is required before audio capture or egress.",
        "record_participant_consent",
    ),
    STTFailureCode.CONSENT_INCOMPLETE: (
        "Every listed participant must consent before audio egress.",
        "resolve_participant_consent",
    ),
    STTFailureCode.CONSENT_REVOKED: (
        "Audio egress is blocked because consent was revoked.",
        "stop_audio_processing",
    ),
    STTFailureCode.CONSENT_NOTICE_MISMATCH: (
        "Consent does not bind the reviewed recording notice.",
        "record_consent_for_current_notice",
    ),
    STTFailureCode.TIMELINE_INVALID: (
        "Consent, capture, and request times do not form a valid sequence.",
        "restart_capture_after_consent",
    ),
    STTFailureCode.PROVIDER_NOT_ALLOWED: (
        "The requested speech-to-text provider is not approved.",
        "use_reviewed_stt_configuration",
    ),
    STTFailureCode.MODEL_NOT_ALLOWED: (
        "The requested speech-to-text model is not approved.",
        "use_reviewed_stt_configuration",
    ),
    STTFailureCode.REGION_NOT_ALLOWED: (
        "The requested speech-to-text region is not approved.",
        "use_reviewed_stt_configuration",
    ),
    STTFailureCode.RETENTION_NOT_ALLOWED: (
        "The requested audio-retention behavior is not approved.",
        "use_zero_retention",
    ),
    STTFailureCode.MEDIA_TYPE_NOT_ALLOWED: (
        "The audio format is outside the reviewed policy.",
        "use_a_supported_audio_format",
    ),
    STTFailureCode.AUDIO_TOO_LARGE: (
        "The audio exceeds the reviewed byte limit.",
        "reduce_audio_size",
    ),
    STTFailureCode.AUDIO_TOO_LONG: (
        "The audio exceeds the reviewed duration limit.",
        "shorten_audio_duration",
    ),
}


class STTEgressDenied(ValueError):
    """Safe typed refusal that grants no raw-audio transport authority."""

    retryable = False

    def __init__(self, failure_code: STTFailureCode) -> None:
        self.failure_code = STTFailureCode(failure_code)
        self.code = self.failure_code.value
        safe_message, next_action = _FAILURE_DETAILS[self.failure_code]
        self.next_action = next_action
        super().__init__(safe_message)


class PrivateSTTSerializationError(RuntimeError):
    """Refusal to serialize or copy request-local provider transcript text."""

    code = "private_stt_serialization_forbidden"

    def __init__(self) -> None:
        super().__init__(self.code)


class PrivateSTTValidationError(ValueError):
    """Content-free validation failure for private transcript material."""

    code = "private_stt_validation_failed"

    def __init__(self) -> None:
        super().__init__(self.code)


class _FrozenSTTModel(FrozenExitSpecModel):
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


class _PrivateSTTModel(_FrozenSTTModel):
    """Request-local model whose ordinary inspection cannot reveal content."""

    def __init__(self, **data: Any) -> None:
        try:
            super().__init__(**data)
        except (ValidationError, PrivateSTTValidationError, ValueError, TypeError):
            raise PrivateSTTValidationError() from None

    @classmethod
    def model_validate(cls, obj: Any, *args: Any, **kwargs: Any) -> Self:
        try:
            return super().model_validate(obj, *args, **kwargs)
        except (ValidationError, PrivateSTTValidationError, ValueError, TypeError):
            raise PrivateSTTValidationError() from None

    @classmethod
    def model_validate_json(
        cls,
        json_data: str | bytes | bytearray,
        *args: Any,
        **kwargs: Any,
    ) -> Self:
        try:
            return super().model_validate_json(json_data, *args, **kwargs)
        except (ValidationError, PrivateSTTValidationError, ValueError, TypeError):
            raise PrivateSTTValidationError() from None

    def __repr__(self) -> str:
        return f"{type(self).__name__}(<private>)"

    def __str__(self) -> str:
        return repr(self)

    def __iter__(self) -> Iterator[tuple[str, Any]]:
        raise PrivateSTTSerializationError()

    def __getstate__(self) -> Never:
        raise PrivateSTTSerializationError()

    def __reduce__(self) -> Never:
        raise PrivateSTTSerializationError()

    def __reduce_ex__(self, protocol: int) -> Never:
        raise PrivateSTTSerializationError()

    def __copy__(self) -> Never:
        raise PrivateSTTSerializationError()

    def __deepcopy__(self, memo: dict[int, Any]) -> Never:
        raise PrivateSTTSerializationError()

    def dict(self, *args: Any, **kwargs: Any) -> Never:
        raise PrivateSTTSerializationError()

    def json(self, *args: Any, **kwargs: Any) -> Never:
        raise PrivateSTTSerializationError()

    def model_dump(self, *args: Any, **kwargs: Any) -> Never:
        raise PrivateSTTSerializationError()

    def model_dump_json(self, *args: Any, **kwargs: Any) -> Never:
        raise PrivateSTTSerializationError()

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Never:
        raise PrivateSTTSerializationError()


def _aware_utc(value: datetime, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{field_name} must be timezone-aware.")
    return value.astimezone(timezone.utc)


def _identity(value: str, field_name: str) -> str:
    if type(value) is not str or re.fullmatch(_IDENTITY_PATTERN, value) is None:
        raise ValueError(f"{field_name} must be one exact opaque identity.")
    return value


def _bounded_timeout(value: object) -> float:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(float(value))
        or not 0 < float(value) <= 300
    ):
        raise ValueError("transport_timeout_seconds must be between 0 and 300.")
    return float(value)


def _digest(domain: bytes, payload: Any) -> str:
    return hashlib.sha256(domain + canonical_json_bytes(payload)).hexdigest()


class STTPrivacyPolicy(_FrozenSTTModel):
    """Reviewed server-owned policy for one exact synthetic STT configuration."""

    schema_version: Literal[STT_BOUNDARY_VERSION] = STT_BOUNDARY_VERSION
    policy_id: str = Field(pattern=_POLICY_ID_PATTERN)
    policy_version: str = Field(pattern=_IDENTITY_PATTERN)
    provider: str = Field(pattern=_IDENTITY_PATTERN)
    provider_model: str = Field(pattern=_IDENTITY_PATTERN)
    region: str = Field(pattern=_IDENTITY_PATTERN)
    allowed_media_types: tuple[str, ...] = Field(
        min_length=1,
        max_length=_MAX_MEDIA_TYPES,
    )
    max_audio_bytes: int = Field(gt=0, le=100 * 1024 * 1024)
    max_duration_ms: int = Field(gt=0, le=4 * 60 * 60 * 1000)
    transport_timeout_seconds: float = Field(gt=0, le=300)
    retention_mode: Literal[STTRetentionMode.ZERO_RETENTION] = (
        STTRetentionMode.ZERO_RETENTION
    )
    provider_data_policy_sha256: str = Field(pattern=SHA256_PATTERN)
    consent_notice_sha256: str = Field(pattern=SHA256_PATTERN)
    deletion_policy_ref: str = Field(pattern=_POLICY_REFERENCE_PATTERN)
    incident_response_policy_ref: str = Field(pattern=_POLICY_REFERENCE_PATTERN)
    reviewed_at: datetime
    expires_at: datetime
    authorization_ttl_seconds: int = Field(default=300, gt=0, le=300)
    synthetic_only: Literal[True] = True

    @field_validator("provider", "provider_model", "region", "policy_version")
    @classmethod
    def validate_identity(cls, value: str, info: Any) -> str:
        return _identity(value, info.field_name)

    @field_validator("allowed_media_types", mode="before")
    @classmethod
    def normalize_media_types(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value

    @field_validator("allowed_media_types")
    @classmethod
    def validate_media_types(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("allowed_media_types must be unique.")
        if any(
            type(media_type) is not str
            or _MEDIA_TYPE_PATTERN.fullmatch(media_type) is None
            for media_type in value
        ):
            raise ValueError("allowed_media_types contains an invalid value.")
        return value

    @field_validator("reviewed_at", "expires_at")
    @classmethod
    def validate_policy_time(cls, value: datetime, info: Any) -> datetime:
        return _aware_utc(value, info.field_name)

    _timeout_validator = field_validator(
        "transport_timeout_seconds",
        mode="before",
    )(_bounded_timeout)

    @model_validator(mode="after")
    def require_bounded_review_window(self) -> "STTPrivacyPolicy":
        if self.expires_at <= self.reviewed_at:
            raise ValueError("expires_at must follow reviewed_at.")
        if self.expires_at - self.reviewed_at > _MAX_POLICY_AGE:
            raise ValueError("STT policy review may be valid for at most 90 days.")
        return self


class MeetingConsentAttestation(_FrozenSTTModel):
    """Recorded consent bound to one meeting, participant set, and notice."""

    attestation_id: str = Field(pattern=_ATTESTATION_ID_PATTERN)
    meeting_id: str = Field(pattern=_MEETING_ID_PATTERN)
    participant_ids: tuple[str, ...] = Field(
        min_length=1,
        max_length=_MAX_PARTICIPANTS,
        repr=False,
    )
    consented_participant_ids: tuple[str, ...] = Field(
        max_length=_MAX_PARTICIPANTS,
        repr=False,
    )
    recording_notice_acknowledged: bool
    consent_notice_sha256: str = Field(pattern=SHA256_PATTERN, repr=False)
    scope: Literal[STT_CONSENT_SCOPE] = STT_CONSENT_SCOPE
    state: STTConsentState
    attested_by: str = Field(pattern=_IDENTITY_PATTERN)
    attested_at: datetime
    revoked_at: datetime | None = None
    synthetic_only: Literal[True] = True

    @field_validator("participant_ids", "consented_participant_ids", mode="before")
    @classmethod
    def normalize_participant_collections(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value

    @field_validator("participant_ids", "consented_participant_ids")
    @classmethod
    def validate_participant_ids(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("participant identities must be unique.")
        if any(re.fullmatch(_PARTICIPANT_ID_PATTERN, item) is None for item in value):
            raise ValueError("participant identity is invalid.")
        return value

    @field_validator("attested_by")
    @classmethod
    def validate_attester(cls, value: str) -> str:
        return _identity(value, "attested_by")

    @field_validator("attested_at", "revoked_at")
    @classmethod
    def validate_consent_time(
        cls,
        value: datetime | None,
        info: Any,
    ) -> datetime | None:
        if value is None:
            return None
        return _aware_utc(value, info.field_name)

    @model_validator(mode="after")
    def require_consistent_consent_state(self) -> "MeetingConsentAttestation":
        if not set(self.consented_participant_ids).issubset(self.participant_ids):
            raise ValueError("consented participants must belong to the meeting.")
        if self.state == STTConsentState.GRANTED and self.revoked_at is not None:
            raise ValueError("granted consent cannot include revoked_at.")
        if self.state == STTConsentState.REVOKED:
            if self.revoked_at is None or self.revoked_at < self.attested_at:
                raise ValueError("revoked consent requires a valid revoked_at.")
        return self


class AudioDescriptor(_FrozenSTTModel):
    """Metadata binding for audio bytes; raw bytes are intentionally absent."""

    meeting_id: str = Field(pattern=_MEETING_ID_PATTERN)
    audio_sha256: str = Field(pattern=SHA256_PATTERN)
    byte_length: int = Field(gt=0, le=100 * 1024 * 1024)
    duration_ms: int = Field(gt=0, le=4 * 60 * 60 * 1000)
    media_type: str = Field(min_length=1, max_length=70)
    captured_at: datetime
    synthetic_only: Literal[True] = True

    @field_validator("media_type")
    @classmethod
    def validate_media_type(cls, value: str) -> str:
        if _MEDIA_TYPE_PATTERN.fullmatch(value) is None:
            raise ValueError("media_type must be a canonical audio media type.")
        return value

    @field_validator("captured_at")
    @classmethod
    def validate_capture_time(cls, value: datetime) -> datetime:
        return _aware_utc(value, "captured_at")


class STTEgressIntent(_FrozenSTTModel):
    """Exact metadata intent evaluated before any future raw-audio egress."""

    request_id: str = Field(pattern=_REQUEST_ID_PATTERN)
    poc_id: str = Field(pattern=POC_ID_PATTERN)
    meeting_id: str = Field(pattern=_MEETING_ID_PATTERN)
    audio: AudioDescriptor
    consent: MeetingConsentAttestation = Field(repr=False)
    provider: str = Field(pattern=_IDENTITY_PATTERN)
    provider_model: str = Field(pattern=_IDENTITY_PATTERN)
    region: str = Field(pattern=_IDENTITY_PATTERN)
    retention_mode: STTRetentionMode
    requested_at: datetime
    synthetic_only: Literal[True] = True

    @field_validator("provider", "provider_model", "region")
    @classmethod
    def validate_configuration_identity(cls, value: str, info: Any) -> str:
        return _identity(value, info.field_name)

    @field_validator("requested_at")
    @classmethod
    def validate_request_time(cls, value: datetime) -> datetime:
        return _aware_utc(value, "requested_at")


class STTEgressAuthorizationRecord(_FrozenSTTModel):
    """Safe policy-match receipt; explicitly not a transport capability."""

    schema_version: Literal[STT_BOUNDARY_VERSION] = STT_BOUNDARY_VERSION
    authorization_id: str = Field(pattern=_AUTHORIZATION_ID_PATTERN)
    request_id: str = Field(pattern=_REQUEST_ID_PATTERN)
    poc_id: str = Field(pattern=POC_ID_PATTERN)
    meeting_identity_sha256: str = Field(pattern=SHA256_PATTERN)
    audio_sha256: str = Field(pattern=SHA256_PATTERN)
    consent_attestation_sha256: str = Field(pattern=SHA256_PATTERN)
    policy_id: str = Field(pattern=_POLICY_ID_PATTERN)
    policy_version: str = Field(pattern=_IDENTITY_PATTERN)
    provider: str = Field(pattern=_IDENTITY_PATTERN)
    provider_model: str = Field(pattern=_IDENTITY_PATTERN)
    region: str = Field(pattern=_IDENTITY_PATTERN)
    media_type: str
    byte_length: int = Field(gt=0)
    duration_ms: int = Field(gt=0)
    transport_timeout_seconds: float = Field(gt=0, le=300)
    retention_mode: Literal[STTRetentionMode.ZERO_RETENTION]
    provider_data_policy_sha256: str = Field(pattern=SHA256_PATTERN)
    authority: Literal[STT_EGRESS_AUTHORITY] = STT_EGRESS_AUTHORITY
    transcript_authority: Literal[STT_TRANSCRIPT_AUTHORITY] = (
        STT_TRANSCRIPT_AUTHORITY
    )
    transport_capability_issued: Literal[False] = False
    authorized_at: datetime
    expires_at: datetime
    synthetic_only: Literal[True] = True

    @field_validator("authorized_at", "expires_at")
    @classmethod
    def validate_authorization_time(cls, value: datetime, info: Any) -> datetime:
        return _aware_utc(value, info.field_name)

    _timeout_validator = field_validator(
        "transport_timeout_seconds",
        mode="before",
    )(_bounded_timeout)

    @model_validator(mode="after")
    def require_positive_authorization_window(self) -> "STTEgressAuthorizationRecord":
        if self.expires_at <= self.authorized_at:
            raise ValueError("expires_at must follow authorized_at.")
        return self


class STTTranscriptSegment(_PrivateSTTModel):
    """One request-local provider segment; text and labels are untrusted."""

    segment_id: str = Field(pattern=_SEGMENT_ID_PATTERN)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    speaker_label: str | None = Field(default=None, max_length=160, repr=False)
    text: str = Field(
        min_length=1,
        max_length=_MAX_TRANSCRIPT_SEGMENT_CHARACTERS,
        repr=False,
    )

    @field_validator("speaker_label", "text")
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
    def require_positive_segment(self) -> "STTTranscriptSegment":
        if self.end_ms <= self.start_ms:
            raise ValueError("end_ms must follow start_ms.")
        return self


class UntrustedSTTTranscript(_PrivateSTTModel):
    """Request-local provider output with permanently non-authoritative status."""

    authorization_id: str = Field(pattern=_AUTHORIZATION_ID_PATTERN)
    request_id: str = Field(pattern=_REQUEST_ID_PATTERN)
    poc_id: str = Field(pattern=POC_ID_PATTERN)
    meeting_id: str = Field(pattern=_MEETING_ID_PATTERN, repr=False)
    audio_sha256: str = Field(pattern=SHA256_PATTERN)
    audio_duration_ms: int = Field(gt=0)
    provider: str = Field(pattern=_IDENTITY_PATTERN)
    provider_model: str = Field(pattern=_IDENTITY_PATTERN)
    region: str = Field(pattern=_IDENTITY_PATTERN)
    provider_request_id_sha256: str = Field(pattern=SHA256_PATTERN)
    language: str = Field(pattern=_LANGUAGE_PATTERN)
    speaker_mapping: STTSpeakerMappingState
    segments: tuple[STTTranscriptSegment, ...] = Field(
        min_length=1,
        max_length=_MAX_TRANSCRIPT_SEGMENTS,
        repr=False,
    )
    authority: Literal[STT_TRANSCRIPT_AUTHORITY] = STT_TRANSCRIPT_AUTHORITY
    review_state: Literal[STT_REVIEW_STATE] = STT_REVIEW_STATE
    completed_at: datetime

    @field_validator("segments", mode="before")
    @classmethod
    def normalize_segments(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value

    @field_validator("completed_at")
    @classmethod
    def validate_completion_time(cls, value: datetime) -> datetime:
        return _aware_utc(value, "completed_at")

    @model_validator(mode="after")
    def require_ordered_unverified_segments(self) -> "UntrustedSTTTranscript":
        previous_end = -1
        identifiers: set[str] = set()
        for segment in self.segments:
            if segment.segment_id in identifiers:
                raise ValueError("segment identities must be unique.")
            if segment.start_ms < previous_end:
                raise ValueError("segments must be ordered and non-overlapping.")
            if segment.end_ms > self.audio_duration_ms:
                raise ValueError("segment exceeds the bound audio duration.")
            identifiers.add(segment.segment_id)
            previous_end = segment.end_ms

        labels_present = tuple(
            segment.speaker_label is not None for segment in self.segments
        )
        if self.speaker_mapping == STTSpeakerMappingState.NOT_PROVIDED:
            if any(labels_present):
                raise ValueError("speaker labels conflict with speaker_mapping.")
        elif not all(labels_present):
            raise ValueError("provider-assigned mapping requires every label.")
        return self

    def transient_redaction_input(self) -> str:
        """Expose transcript text only for the immediate redaction handoff."""

        lines = []
        neutral_labels: dict[str, str] = {}
        for segment in self.segments:
            normalized_text = " ".join(segment.text.split())
            if segment.speaker_label is None:
                lines.append(f"Speaker unknown: {normalized_text}")
            else:
                label = neutral_labels.setdefault(
                    segment.speaker_label,
                    f"Speaker {len(neutral_labels) + 1}",
                )
                lines.append(f"{label}: {normalized_text}")
        return "\n".join(lines)


class STTTranscriptReceipt(_FrozenSTTModel):
    """Content-free provenance published only after a redaction handoff."""

    schema_version: Literal[STT_BOUNDARY_VERSION] = STT_BOUNDARY_VERSION
    operation_id: str = Field(pattern=_OPERATION_ID_PATTERN)
    authorization_id: str = Field(pattern=_AUTHORIZATION_ID_PATTERN)
    request_id: str = Field(pattern=_REQUEST_ID_PATTERN)
    poc_id: str = Field(pattern=POC_ID_PATTERN)
    source_kind: Literal["MEETING"] = "MEETING"
    source_receipt_id: str = Field(pattern=_SOURCE_RECEIPT_ID_PATTERN)
    meeting_identity_sha256: str = Field(pattern=SHA256_PATTERN)
    audio_sha256: str = Field(pattern=SHA256_PATTERN)
    provider_request_id_sha256: str = Field(pattern=SHA256_PATTERN)
    redacted_transcript_sha256: str = Field(pattern=SHA256_PATTERN)
    redacted_character_count: int = Field(gt=0)
    segment_count: int = Field(gt=0, le=_MAX_TRANSCRIPT_SEGMENTS)
    provider: str = Field(pattern=_IDENTITY_PATTERN)
    provider_model: str = Field(pattern=_IDENTITY_PATTERN)
    region: str = Field(pattern=_IDENTITY_PATTERN)
    redaction_policy_version: str = Field(pattern=_IDENTITY_PATTERN)
    speaker_mapping: STTSpeakerMappingState
    authority: Literal[STT_TRANSCRIPT_AUTHORITY] = STT_TRANSCRIPT_AUTHORITY
    review_state: Literal[STT_REVIEW_STATE] = STT_REVIEW_STATE
    raw_audio_retained: Literal[False] = False
    raw_transcript_retained: Literal[False] = False
    completed_at: datetime

    @field_validator("completed_at")
    @classmethod
    def validate_receipt_time(cls, value: datetime) -> datetime:
        return _aware_utc(value, "completed_at")


class STTFailureReceipt(_FrozenSTTModel):
    """Stable public projection of one content-free authorization refusal."""

    request_id: str = Field(pattern=_REQUEST_ID_PATTERN)
    failure_code: STTFailureCode
    retryable: Literal[False] = False
    next_action: str = Field(pattern=r"^[a-z][a-z0-9_]{2,79}$")
    safe_message: str = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def require_canonical_failure_projection(self) -> "STTFailureReceipt":
        safe_message, next_action = _FAILURE_DETAILS[self.failure_code]
        if self.safe_message != safe_message or self.next_action != next_action:
            raise ValueError("STT failure projection is inconsistent.")
        return self

    @classmethod
    def from_denial(
        cls,
        request_id: str,
        denial: STTEgressDenied,
    ) -> "STTFailureReceipt":
        safe_message, next_action = _FAILURE_DETAILS[denial.failure_code]
        return cls(
            request_id=request_id,
            failure_code=denial.failure_code,
            next_action=next_action,
            safe_message=safe_message,
        )


def authorize_stt_egress(
    policy: STTPrivacyPolicy,
    intent: STTEgressIntent,
    *,
    now: datetime | None = None,
) -> STTEgressAuthorizationRecord:
    """Evaluate one exact metadata intent without touching raw audio or network."""

    if not isinstance(policy, STTPrivacyPolicy) or not isinstance(
        intent,
        STTEgressIntent,
    ):
        raise STTEgressDenied(STTFailureCode.INVALID_REQUEST)

    current_time = datetime.now(timezone.utc) if now is None else now
    if not isinstance(current_time, datetime) or current_time.tzinfo is None:
        raise STTEgressDenied(STTFailureCode.INVALID_REQUEST)
    current_time = current_time.astimezone(timezone.utc)

    if current_time < policy.reviewed_at:
        raise STTEgressDenied(STTFailureCode.POLICY_NOT_ACTIVE)
    if current_time >= policy.expires_at:
        raise STTEgressDenied(STTFailureCode.POLICY_EXPIRED)
    if intent.requested_at > current_time:
        raise STTEgressDenied(STTFailureCode.TIMELINE_INVALID)
    if (
        current_time - intent.requested_at
        > timedelta(seconds=policy.authorization_ttl_seconds)
    ):
        raise STTEgressDenied(STTFailureCode.REQUEST_EXPIRED)

    if not (
        intent.meeting_id
        == intent.audio.meeting_id
        == intent.consent.meeting_id
    ):
        raise STTEgressDenied(STTFailureCode.MEETING_IDENTITY_MISMATCH)
    if intent.consent.state == STTConsentState.REVOKED:
        raise STTEgressDenied(STTFailureCode.CONSENT_REVOKED)
    if not intent.consent.recording_notice_acknowledged:
        raise STTEgressDenied(STTFailureCode.CONSENT_REQUIRED)
    if set(intent.consent.consented_participant_ids) != set(
        intent.consent.participant_ids
    ):
        raise STTEgressDenied(STTFailureCode.CONSENT_INCOMPLETE)
    if intent.consent.consent_notice_sha256 != policy.consent_notice_sha256:
        raise STTEgressDenied(STTFailureCode.CONSENT_NOTICE_MISMATCH)
    if not (
        policy.reviewed_at
        <= intent.consent.attested_at
        <= intent.audio.captured_at
        <= intent.requested_at
        <= current_time
    ):
        raise STTEgressDenied(STTFailureCode.TIMELINE_INVALID)

    if intent.provider != policy.provider:
        raise STTEgressDenied(STTFailureCode.PROVIDER_NOT_ALLOWED)
    if intent.provider_model != policy.provider_model:
        raise STTEgressDenied(STTFailureCode.MODEL_NOT_ALLOWED)
    if intent.region != policy.region:
        raise STTEgressDenied(STTFailureCode.REGION_NOT_ALLOWED)
    if intent.retention_mode != policy.retention_mode:
        raise STTEgressDenied(STTFailureCode.RETENTION_NOT_ALLOWED)
    if intent.audio.media_type not in policy.allowed_media_types:
        raise STTEgressDenied(STTFailureCode.MEDIA_TYPE_NOT_ALLOWED)
    if intent.audio.byte_length > policy.max_audio_bytes:
        raise STTEgressDenied(STTFailureCode.AUDIO_TOO_LARGE)
    if intent.audio.duration_ms > policy.max_duration_ms:
        raise STTEgressDenied(STTFailureCode.AUDIO_TOO_LONG)

    policy_payload = policy.model_dump(mode="json")
    intent_payload = intent.model_dump(mode="json")
    consent_digest = _digest(
        _CONSENT_DOMAIN,
        intent.consent.model_dump(mode="json"),
    )
    meeting_identity_digest = _digest(
        _MEETING_IDENTITY_DOMAIN,
        {"meeting_id": intent.meeting_id},
    )
    authorization_digest = _digest(
        _AUTHORIZATION_DOMAIN,
        {
            "policy": policy_payload,
            "intent": intent_payload,
        },
    )
    expires_at = min(
        policy.expires_at,
        current_time + timedelta(seconds=policy.authorization_ttl_seconds),
    )

    return STTEgressAuthorizationRecord(
        authorization_id=f"sttauth_{authorization_digest}",
        request_id=intent.request_id,
        poc_id=intent.poc_id,
        meeting_identity_sha256=meeting_identity_digest,
        audio_sha256=intent.audio.audio_sha256,
        consent_attestation_sha256=consent_digest,
        policy_id=policy.policy_id,
        policy_version=policy.policy_version,
        provider=policy.provider,
        provider_model=policy.provider_model,
        region=policy.region,
        media_type=intent.audio.media_type,
        byte_length=intent.audio.byte_length,
        duration_ms=intent.audio.duration_ms,
        transport_timeout_seconds=policy.transport_timeout_seconds,
        retention_mode=policy.retention_mode,
        provider_data_policy_sha256=policy.provider_data_policy_sha256,
        authorized_at=current_time,
        expires_at=expires_at,
    )


__all__ = [
    "AudioDescriptor",
    "MeetingConsentAttestation",
    "PrivateSTTSerializationError",
    "PrivateSTTValidationError",
    "STT_BOUNDARY_VERSION",
    "STTConsentState",
    "STTEgressAuthorizationRecord",
    "STTEgressDenied",
    "STTEgressIntent",
    "STTFailureCode",
    "STTFailureReceipt",
    "STTPrivacyPolicy",
    "STTRetentionMode",
    "STTSpeakerMappingState",
    "STTTranscriptReceipt",
    "STTTranscriptSegment",
    "UntrustedSTTTranscript",
    "authorize_stt_egress",
]
