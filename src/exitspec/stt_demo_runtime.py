"""Process-local browser demo adapter for the synthetic STT spine.

This module joins the reviewed policy boundary, one-use audio operation, and
review-only transcript handoff without pretending to perform speech
recognition. Browser audio is used only to prove consent, byte binding, and
zero-retention control flow. A code-pinned synthetic transcript is always the
output and is permanently untrusted.
"""

from __future__ import annotations

import hashlib
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Callable, Literal, Mapping, Self

from pydantic import ConfigDict, Field, model_validator

from .canonical import canonical_json_bytes
from .models import SHA256_PATTERN, FrozenExitSpecModel
from .poc_creation import (
    DraftPOCArchiveState,
    DraftPOCNotFound,
    ProcessLocalDraftPOCService,
    POC_ID_PATTERN,
)
from .poc_source_intake import ProcessLocalPOCSourceIntake
from .stt_boundary import (
    AudioDescriptor,
    MeetingConsentAttestation,
    STTConsentState,
    STTEgressDenied,
    STTEgressIntent,
    STTPrivacyPolicy,
    STTRetentionMode,
    STTSpeakerMappingState,
)
from .stt_handoff import (
    STTTranscriptHandoffError,
    STTTranscriptHandoffService,
)
from .stt_operation import (
    STTAudioPermitIssuer,
    STTOperationError,
    STTOperationExecutor,
    STTTransportRequest,
    STTTransportResponse,
    STTTransportSegment,
)


STT_DEMO_VERSION = "exitspec-stt-browser-demo/1.0"
STT_DEMO_DISCLOSURE_ID = "stt_demo_disclosure_v1"
STT_DEMO_MODE = "FIXED_SYNTHETIC_TRANSCRIPT"
STT_DEMO_MEDIA_TYPE = "audio/webm"
STT_DEMO_MAX_AUDIO_BYTES = 64 * 1024
STT_DEMO_MIN_DURATION_MS = 250
STT_DEMO_MAX_DURATION_MS = 8_000
STT_DEMO_CONSENT_TTL_SECONDS = 120
STT_DEMO_DURATION_SOURCE = "BROWSER_MONOTONIC_CLOCK_DECLARED"

_NOTICE = (
    "This local demo records one consenting operator for at most eight "
    "browser-measured seconds. ExitSpec checks the WebM signature and exact "
    "byte binding, persists no audio, clears request-local audio after the "
    "attempt, and does not transcribe spoken words. It always emits the "
    "disclosed fixed requirements for human review."
)
_FIXED_OUTPUT = (
    "P95 time to first token must stay below 500 ms.",
    "Error rate must remain below 1%.",
)
_NOTICE_SHA256 = hashlib.sha256(_NOTICE.encode("utf-8")).hexdigest()
_DATA_POLICY_SHA256 = hashlib.sha256(
    b"exitspec-local-synthetic-zero-retention-v1"
).hexdigest()
_CONSENT_DOMAIN = b"exitspec-stt-demo-consent-v1\x00"
_CAPTURE_DOMAIN = b"exitspec-stt-demo-capture-v1\x00"
_REQUEST_DOMAIN = b"exitspec-stt-demo-request-v1\x00"
_IDEMPOTENCY_DOMAIN = b"exitspec-stt-demo-idempotency-v1\x00"
_POC_ID = re.compile(POC_ID_PATTERN)
_CAPTURE_ID = re.compile(r"^sttcap_[a-f0-9]{64}$")
_OPERATION_ID = re.compile(r"^sttop_[a-f0-9]{64}$")
_SOURCE_RECEIPT_ID = re.compile(r"^srcpt_[a-z0-9][a-z0-9_-]{7,95}$")
_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,199}$")
_WEBM_EBML_SIGNATURE = b"\x1a\x45\xdf\xa3"


class STTDemoFailureCode(str, Enum):
    """Stable content-free refusal classes for the local browser adapter."""

    INVALID_REQUEST = "STT_DEMO_INVALID_REQUEST"
    DRAFT_UNAVAILABLE = "STT_DEMO_DRAFT_UNAVAILABLE"
    CAPACITY_EXCEEDED = "STT_DEMO_CAPACITY_EXCEEDED"
    DISCLOSURE_MISMATCH = "STT_DEMO_DISCLOSURE_MISMATCH"
    CONSENT_REQUIRED = "STT_DEMO_CONSENT_REQUIRED"
    CONSENT_EXPIRED = "STT_DEMO_CONSENT_EXPIRED"
    CAPTURE_CONFLICT = "STT_DEMO_CAPTURE_CONFLICT"
    CAPTURE_IN_PROGRESS = "STT_DEMO_CAPTURE_IN_PROGRESS"
    CAPTURE_CONSUMED = "STT_DEMO_CAPTURE_CONSUMED"
    AUDIO_BINDING_MISMATCH = "STT_DEMO_AUDIO_BINDING_MISMATCH"
    AUDIO_TOO_LARGE = "STT_DEMO_AUDIO_TOO_LARGE"
    AUDIO_TOO_LONG = "STT_DEMO_AUDIO_TOO_LONG"
    UNSUPPORTED_MEDIA = "STT_DEMO_UNSUPPORTED_MEDIA"
    OPERATION_FAILED = "STT_DEMO_OPERATION_FAILED"
    HANDOFF_FAILED = "STT_DEMO_HANDOFF_FAILED"


_FAILURE_DETAILS: dict[STTDemoFailureCode, tuple[str, str]] = {
    STTDemoFailureCode.INVALID_REQUEST: (
        "The synthetic recording request was not accepted.",
        "review_the_recording_request",
    ),
    STTDemoFailureCode.DRAFT_UNAVAILABLE: (
        "The draft POC cannot accept a recording source.",
        "return_to_the_poc_workspace",
    ),
    STTDemoFailureCode.CAPACITY_EXCEEDED: (
        "The local synthetic recording runtime is at capacity.",
        "restart_the_local_demo_safely",
    ),
    STTDemoFailureCode.DISCLOSURE_MISMATCH: (
        "The recording disclosure is no longer current.",
        "review_the_current_disclosure",
    ),
    STTDemoFailureCode.CONSENT_REQUIRED: (
        "Recorded consent is required before microphone capture.",
        "record_consent_before_capture",
    ),
    STTDemoFailureCode.CONSENT_EXPIRED: (
        "The recording consent window expired.",
        "record_consent_again",
    ),
    STTDemoFailureCode.CAPTURE_CONFLICT: (
        "The capture request conflicts with the recorded consent.",
        "start_a_new_recording",
    ),
    STTDemoFailureCode.CAPTURE_IN_PROGRESS: (
        "The synthetic capture is already being processed.",
        "wait_before_starting_a_new_recording",
    ),
    STTDemoFailureCode.CAPTURE_CONSUMED: (
        "The recording attempt was consumed without a trusted result.",
        "record_new_consent_and_audio",
    ),
    STTDemoFailureCode.AUDIO_BINDING_MISMATCH: (
        "The audio does not match its declared byte binding.",
        "start_a_new_recording",
    ),
    STTDemoFailureCode.AUDIO_TOO_LARGE: (
        "The audio exceeds the local synthetic byte limit.",
        "record_a_shorter_clip",
    ),
    STTDemoFailureCode.AUDIO_TOO_LONG: (
        "The recording duration is outside the local synthetic limit.",
        "record_a_clip_between_one_and_eight_seconds",
    ),
    STTDemoFailureCode.UNSUPPORTED_MEDIA: (
        "The browser audio format is not supported by this local demo.",
        "use_a_chromium_webm_recording",
    ),
    STTDemoFailureCode.OPERATION_FAILED: (
        "The synthetic speech-to-text operation did not complete safely.",
        "start_a_new_recording",
    ),
    STTDemoFailureCode.HANDOFF_FAILED: (
        "The synthetic transcript could not be attached for review.",
        "review_the_draft_and_start_again",
    ),
}


class STTDemoError(RuntimeError):
    """Sanitized browser-adapter refusal with no audio or transcript text."""

    retryable = False

    def __init__(self, failure_code: STTDemoFailureCode) -> None:
        self.failure_code = STTDemoFailureCode(failure_code)
        self.code = self.failure_code.value
        message, next_action = _FAILURE_DETAILS[self.failure_code]
        self.next_action = next_action
        super().__init__(message)


class _FrozenDemoModel(FrozenExitSpecModel):
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


class STTDemoDisclosure(_FrozenDemoModel):
    """Exact browser copy and limits that must be shown before consent."""

    schema_version: Literal[STT_DEMO_VERSION] = STT_DEMO_VERSION
    disclosure_id: Literal[STT_DEMO_DISCLOSURE_ID] = STT_DEMO_DISCLOSURE_ID
    mode: Literal[STT_DEMO_MODE] = STT_DEMO_MODE
    notice: Literal[_NOTICE] = _NOTICE
    fixed_output: tuple[str, str] = _FIXED_OUTPUT
    media_type: Literal[STT_DEMO_MEDIA_TYPE] = STT_DEMO_MEDIA_TYPE
    min_duration_ms: Literal[STT_DEMO_MIN_DURATION_MS] = (
        STT_DEMO_MIN_DURATION_MS
    )
    max_duration_ms: Literal[STT_DEMO_MAX_DURATION_MS] = (
        STT_DEMO_MAX_DURATION_MS
    )
    max_audio_bytes: Literal[STT_DEMO_MAX_AUDIO_BYTES] = (
        STT_DEMO_MAX_AUDIO_BYTES
    )
    duration_source: Literal[STT_DEMO_DURATION_SOURCE] = (
        STT_DEMO_DURATION_SOURCE
    )
    webm_signature_required: Literal[True] = True
    consent_required_before_microphone: Literal[True] = True
    one_local_operator_only: Literal[True] = True
    spoken_words_transcribed: Literal[False] = False
    provider_connected: Literal[False] = False
    raw_audio_retained: Literal[False] = False
    raw_transcript_retained: Literal[False] = False

    @model_validator(mode="after")
    def require_fixed_output(self) -> "STTDemoDisclosure":
        if self.fixed_output != _FIXED_OUTPUT:
            raise ValueError("Synthetic demo output must remain code-pinned.")
        return self


class STTDemoConsentReceipt(_FrozenDemoModel):
    """Content-free authority to request one short browser capture."""

    schema_version: Literal[STT_DEMO_VERSION] = STT_DEMO_VERSION
    capture_id: str = Field(pattern=_CAPTURE_ID.pattern)
    poc_id: str = Field(pattern=POC_ID_PATTERN)
    disclosure_id: Literal[STT_DEMO_DISCLOSURE_ID] = STT_DEMO_DISCLOSURE_ID
    state: Literal["READY"] = "READY"
    expires_at: datetime
    recording_notice_acknowledged: Literal[True] = True
    all_speakers_consented: Literal[True] = True
    synthetic_demo_acknowledged: Literal[True] = True
    microphone_authority_issued: Literal[True] = True
    audio_egress_authority_issued: Literal[False] = False
    synthetic_only: Literal[True] = True


class STTDemoCaptureReceipt(_FrozenDemoModel):
    """Safe review-only projection after the synthetic STT handoff."""

    schema_version: Literal[STT_DEMO_VERSION] = STT_DEMO_VERSION
    capture_id: str = Field(pattern=_CAPTURE_ID.pattern)
    operation_id: str = Field(pattern=_OPERATION_ID.pattern)
    idempotent_replay: bool
    poc_id: str = Field(pattern=POC_ID_PATTERN)
    source_kind: Literal["MEETING"] = "MEETING"
    source_receipt_id: str = Field(pattern=_SOURCE_RECEIPT_ID.pattern)
    proposal_count: int = Field(ge=0, le=64)
    status: Literal["NEEDS_REVIEW"] = "NEEDS_REVIEW"
    mode: Literal[STT_DEMO_MODE] = STT_DEMO_MODE
    duration_source: Literal[STT_DEMO_DURATION_SOURCE] = (
        STT_DEMO_DURATION_SOURCE
    )
    webm_signature_verified: Literal[True] = True
    spoken_words_transcribed: Literal[False] = False
    provider_connected: Literal[False] = False
    raw_audio_retained: Literal[False] = False
    raw_transcript_retained: Literal[False] = False


@dataclass(slots=True)
class _CaptureSession:
    consent: MeetingConsentAttestation
    receipt: STTDemoConsentReceipt
    request_sha256: str | None = None
    state: str = "READY"
    result: STTDemoCaptureReceipt | None = None


class _FixedSyntheticTransport:
    """Consumes the bytes once and emits only the disclosed fixed fixture."""

    def transcribe(self, request: STTTransportRequest) -> STTTransportResponse:
        audio_bytes: bytes | None = request.read_audio_bytes()
        try:
            duration_ms = request.authorization.duration_ms
            midpoint = max(1, duration_ms // 2)
            return STTTransportResponse(
                provider_request_id=(
                    "synthetic-" + request.authorization.authorization_id[-32:]
                ),
                language="en-US",
                speaker_mapping=(
                    STTSpeakerMappingState.PROVIDER_ASSIGNED_UNVERIFIED
                ),
                segments=(
                    STTTransportSegment(
                        start_ms=0,
                        end_ms=midpoint,
                        speaker_label="synthetic-speaker-a",
                        text=_FIXED_OUTPUT[0],
                    ),
                    STTTransportSegment(
                        start_ms=midpoint,
                        end_ms=duration_ms,
                        speaker_label="synthetic-speaker-b",
                        text=_FIXED_OUTPUT[1],
                    ),
                ),
            )
        finally:
            audio_bytes = None

    def __repr__(self) -> str:
        return "_FixedSyntheticTransport(audio=<never-stored>)"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _read_clock(clock: Callable[[], datetime]) -> datetime:
    try:
        value = clock()
    except Exception:
        raise STTDemoError(STTDemoFailureCode.OPERATION_FAILED) from None
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise STTDemoError(STTDemoFailureCode.OPERATION_FAILED)
    return value.astimezone(timezone.utc)


def _digest(domain: bytes, payload: Any) -> str:
    return hashlib.sha256(domain + canonical_json_bytes(payload)).hexdigest()


def _idempotency_digest(value: object) -> str:
    if type(value) is not str or _IDEMPOTENCY_KEY.fullmatch(value) is None:
        raise STTDemoError(STTDemoFailureCode.INVALID_REQUEST)
    return _digest(_IDEMPOTENCY_DOMAIN, {"idempotency_key": value})


class ProcessLocalSTTDemoRuntime:
    """Thread-safe, bounded, non-durable coordinator for the browser demo."""

    __slots__ = (
        "_clock",
        "_consent_idempotency",
        "_drafts",
        "_executor",
        "_handoff",
        "_lock",
        "_max_sessions",
        "_permit_issuer",
        "_policy",
        "_sessions",
    )

    def __init__(
        self,
        *,
        drafts: ProcessLocalDraftPOCService,
        source_intake: ProcessLocalPOCSourceIntake,
        clock: Callable[[], datetime] = _utc_now,
        max_sessions: int = 512,
    ) -> None:
        if type(drafts) is not ProcessLocalDraftPOCService:
            raise TypeError("drafts must be a ProcessLocalDraftPOCService.")
        if type(source_intake) is not ProcessLocalPOCSourceIntake:
            raise TypeError(
                "source_intake must be a ProcessLocalPOCSourceIntake."
            )
        if not callable(clock):
            raise TypeError("clock must be callable.")
        if type(max_sessions) is not int or not 1 <= max_sessions <= 10_000:
            raise ValueError("max_sessions is outside supported bounds.")

        policy_time = _read_clock(clock)
        self._clock = clock
        self._drafts = drafts
        self._max_sessions = max_sessions
        self._lock = threading.RLock()
        self._sessions: dict[str, _CaptureSession] = {}
        self._consent_idempotency: dict[str, tuple[str, str]] = {}
        self._policy = STTPrivacyPolicy(
            policy_id="stt_policy_local_browser_demo_v1",
            policy_version="v1",
            provider="exitspec.synthetic",
            provider_model="fixture-transcript-v1",
            region="loopback-local",
            allowed_media_types=(STT_DEMO_MEDIA_TYPE,),
            max_audio_bytes=STT_DEMO_MAX_AUDIO_BYTES,
            max_duration_ms=STT_DEMO_MAX_DURATION_MS,
            transport_timeout_seconds=5.0,
            provider_data_policy_sha256=_DATA_POLICY_SHA256,
            consent_notice_sha256=_NOTICE_SHA256,
            deletion_policy_ref="policy://local-zero-retention-v1",
            incident_response_policy_ref="policy://local-demo-incident-v1",
            reviewed_at=policy_time - timedelta(days=1),
            expires_at=policy_time + timedelta(days=30),
        )
        self._permit_issuer = STTAudioPermitIssuer(
            self._policy,
            clock=self._clock,
            max_issued=max_sessions,
        )
        self._executor = STTOperationExecutor(
            _FixedSyntheticTransport(),
            enabled=True,
            clock=self._clock,
        )
        self._handoff = STTTranscriptHandoffService(
            source_intake,
            clock=self._clock,
        )

    @property
    def disclosure(self) -> STTDemoDisclosure:
        return STTDemoDisclosure()

    def disclosure_for(self, poc_id: object) -> STTDemoDisclosure:
        """Return the fixed disclosure only for one active draft POC."""

        self._require_active_draft(poc_id)
        return self.disclosure

    def _require_active_draft(self, poc_id: object) -> str:
        if type(poc_id) is not str or _POC_ID.fullmatch(poc_id) is None:
            raise STTDemoError(STTDemoFailureCode.INVALID_REQUEST)
        try:
            draft = self._drafts.get(poc_id)
        except (DraftPOCNotFound, TypeError, ValueError):
            raise STTDemoError(
                STTDemoFailureCode.DRAFT_UNAVAILABLE
            ) from None
        if draft.archive_state is not DraftPOCArchiveState.ACTIVE:
            raise STTDemoError(STTDemoFailureCode.DRAFT_UNAVAILABLE)
        return poc_id

    def record_consent(
        self,
        *,
        poc_id: object,
        disclosure_id: object,
        recording_notice_acknowledged: object,
        all_speakers_consented: object,
        synthetic_demo_acknowledged: object,
        idempotency_key: object,
    ) -> STTDemoConsentReceipt:
        """Record exact consent before the browser may request a microphone."""

        validated_poc_id = self._require_active_draft(poc_id)
        if disclosure_id != STT_DEMO_DISCLOSURE_ID:
            raise STTDemoError(STTDemoFailureCode.DISCLOSURE_MISMATCH)
        if (
            recording_notice_acknowledged is not True
            or all_speakers_consented is not True
            or synthetic_demo_acknowledged is not True
        ):
            raise STTDemoError(STTDemoFailureCode.CONSENT_REQUIRED)

        key_sha256 = _idempotency_digest(idempotency_key)
        request_payload = {
            "poc_id": validated_poc_id,
            "disclosure_id": STT_DEMO_DISCLOSURE_ID,
            "recording_notice_acknowledged": True,
            "all_speakers_consented": True,
            "synthetic_demo_acknowledged": True,
        }
        request_sha256 = _digest(_CONSENT_DOMAIN, request_payload)
        capture_sha256 = _digest(
            _CAPTURE_DOMAIN,
            {
                "request_sha256": request_sha256,
                "idempotency_key_sha256": key_sha256,
            },
        )
        capture_id = "sttcap_" + capture_sha256

        with self._lock:
            prior = self._consent_idempotency.get(key_sha256)
            if prior is not None:
                if prior != (request_sha256, capture_id):
                    raise STTDemoError(
                        STTDemoFailureCode.CAPTURE_CONFLICT
                    )
                return self._sessions[capture_id].receipt
            if len(self._sessions) >= self._max_sessions:
                raise STTDemoError(STTDemoFailureCode.CAPACITY_EXCEEDED)

            now = _read_clock(self._clock)
            meeting_id = "meeting_" + capture_sha256
            consent = MeetingConsentAttestation(
                attestation_id="consent_" + capture_sha256,
                meeting_id=meeting_id,
                participant_ids=("participant_local_operator",),
                consented_participant_ids=("participant_local_operator",),
                recording_notice_acknowledged=True,
                consent_notice_sha256=_NOTICE_SHA256,
                state=STTConsentState.GRANTED,
                attested_by="synthetic:self_attested_operator",
                attested_at=now,
            )
            receipt = STTDemoConsentReceipt(
                capture_id=capture_id,
                poc_id=validated_poc_id,
                expires_at=(
                    now + timedelta(seconds=STT_DEMO_CONSENT_TTL_SECONDS)
                ),
            )
            self._sessions[capture_id] = _CaptureSession(
                consent=consent,
                receipt=receipt,
            )
            self._consent_idempotency[key_sha256] = (
                request_sha256,
                capture_id,
            )
            return receipt

    def capture(
        self,
        *,
        poc_id: object,
        capture_id: object,
        audio_bytes: object,
        byte_length: object,
        duration_ms: object,
        media_type: object,
        audio_sha256: object,
        idempotency_key: object,
    ) -> STTDemoCaptureReceipt:
        """Consume one consented clip and attach the fixed review-only source."""

        validated_poc_id = self._require_active_draft(poc_id)
        if type(capture_id) is not str or _CAPTURE_ID.fullmatch(capture_id) is None:
            raise STTDemoError(STTDemoFailureCode.INVALID_REQUEST)
        if type(audio_bytes) is not bytes or not audio_bytes:
            raise STTDemoError(STTDemoFailureCode.AUDIO_BINDING_MISMATCH)
        if type(byte_length) is not int or isinstance(byte_length, bool):
            raise STTDemoError(STTDemoFailureCode.INVALID_REQUEST)
        if byte_length > STT_DEMO_MAX_AUDIO_BYTES:
            raise STTDemoError(STTDemoFailureCode.AUDIO_TOO_LARGE)
        if byte_length <= 0 or byte_length != len(audio_bytes):
            raise STTDemoError(STTDemoFailureCode.AUDIO_BINDING_MISMATCH)
        if not audio_bytes.startswith(_WEBM_EBML_SIGNATURE):
            raise STTDemoError(STTDemoFailureCode.UNSUPPORTED_MEDIA)
        if type(duration_ms) is not int or isinstance(duration_ms, bool):
            raise STTDemoError(STTDemoFailureCode.INVALID_REQUEST)
        if not STT_DEMO_MIN_DURATION_MS <= duration_ms <= STT_DEMO_MAX_DURATION_MS:
            raise STTDemoError(STTDemoFailureCode.AUDIO_TOO_LONG)
        if media_type != STT_DEMO_MEDIA_TYPE:
            raise STTDemoError(STTDemoFailureCode.UNSUPPORTED_MEDIA)
        if (
            type(audio_sha256) is not str
            or re.fullmatch(SHA256_PATTERN, audio_sha256) is None
            or hashlib.sha256(audio_bytes).hexdigest() != audio_sha256
        ):
            raise STTDemoError(STTDemoFailureCode.AUDIO_BINDING_MISMATCH)

        key_sha256 = _idempotency_digest(idempotency_key)
        request_sha256 = _digest(
            _REQUEST_DOMAIN,
            {
                "poc_id": validated_poc_id,
                "capture_id": capture_id,
                "audio_sha256": audio_sha256,
                "byte_length": byte_length,
                "duration_ms": duration_ms,
                "media_type": media_type,
                "idempotency_key_sha256": key_sha256,
            },
        )

        with self._lock:
            session = self._sessions.get(capture_id)
            if session is None or session.receipt.poc_id != validated_poc_id:
                raise STTDemoError(STTDemoFailureCode.CONSENT_REQUIRED)
            if session.request_sha256 is not None:
                if session.request_sha256 != request_sha256:
                    raise STTDemoError(STTDemoFailureCode.CAPTURE_CONFLICT)
                if session.state == "COMPLETE" and session.result is not None:
                    return session.result.model_copy(
                        update={"idempotent_replay": True}
                    )
                if session.state == "PROCESSING":
                    raise STTDemoError(
                        STTDemoFailureCode.CAPTURE_IN_PROGRESS
                    )
                raise STTDemoError(STTDemoFailureCode.CAPTURE_CONSUMED)
            now = _read_clock(self._clock)
            if now >= session.receipt.expires_at:
                raise STTDemoError(STTDemoFailureCode.CONSENT_EXPIRED)
            session.request_sha256 = request_sha256
            session.state = "PROCESSING"
            consent = session.consent

        result: STTDemoCaptureReceipt | None = None
        failure: STTDemoFailureCode | None = None
        try:
            captured_at = _read_clock(self._clock)
            intent = STTEgressIntent(
                request_id="sttreq_" + capture_id.removeprefix("sttcap_"),
                poc_id=validated_poc_id,
                meeting_id=consent.meeting_id,
                audio=AudioDescriptor(
                    meeting_id=consent.meeting_id,
                    audio_sha256=audio_sha256,
                    byte_length=byte_length,
                    duration_ms=duration_ms,
                    media_type=STT_DEMO_MEDIA_TYPE,
                    captured_at=captured_at,
                ),
                consent=consent,
                provider=self._policy.provider,
                provider_model=self._policy.provider_model,
                region=self._policy.region,
                retention_mode=STTRetentionMode.ZERO_RETENTION,
                requested_at=captured_at,
            )
            permit = self._permit_issuer.issue(intent, audio_bytes)
            operation = self._executor.execute(permit)
            handoff = self._handoff.handoff(operation)
            source = handoff.source_receipt
            result = STTDemoCaptureReceipt(
                capture_id=capture_id,
                operation_id=operation.receipt.operation_id,
                idempotent_replay=source.idempotent_replay,
                poc_id=source.poc_id,
                source_receipt_id=source.source_receipt_id,
                proposal_count=source.proposal_count,
            )
        except (STTEgressDenied, STTOperationError):
            failure = STTDemoFailureCode.OPERATION_FAILED
        except STTTranscriptHandoffError:
            failure = STTDemoFailureCode.HANDOFF_FAILED
        except Exception:
            failure = STTDemoFailureCode.OPERATION_FAILED
        finally:
            audio_bytes = b""

        with self._lock:
            session = self._sessions[capture_id]
            if failure is not None or result is None:
                session.state = "FAILED"
            else:
                session.state = "COMPLETE"
                session.result = result
        if failure is not None or result is None:
            raise STTDemoError(
                failure or STTDemoFailureCode.OPERATION_FAILED
            )
        return result

    def capture_receipt(
        self,
        *,
        poc_id: object,
        capture_id: object,
    ) -> STTDemoCaptureReceipt:
        """Return one content-free completed receipt without receiving audio."""

        validated_poc_id = self._require_active_draft(poc_id)
        if type(capture_id) is not str or _CAPTURE_ID.fullmatch(capture_id) is None:
            raise STTDemoError(STTDemoFailureCode.INVALID_REQUEST)
        with self._lock:
            session = self._sessions.get(capture_id)
            if session is None or session.receipt.poc_id != validated_poc_id:
                raise STTDemoError(STTDemoFailureCode.CONSENT_REQUIRED)
            if session.state == "COMPLETE" and session.result is not None:
                return session.result.model_copy(
                    update={"idempotent_replay": True}
                )
            if session.state == "PROCESSING":
                raise STTDemoError(STTDemoFailureCode.CAPTURE_IN_PROGRESS)
            if session.state == "FAILED":
                raise STTDemoError(STTDemoFailureCode.CAPTURE_CONSUMED)
            raise STTDemoError(STTDemoFailureCode.CONSENT_REQUIRED)


__all__ = [
    "ProcessLocalSTTDemoRuntime",
    "STT_DEMO_CONSENT_TTL_SECONDS",
    "STT_DEMO_DISCLOSURE_ID",
    "STT_DEMO_DURATION_SOURCE",
    "STT_DEMO_MAX_AUDIO_BYTES",
    "STT_DEMO_MAX_DURATION_MS",
    "STT_DEMO_MEDIA_TYPE",
    "STT_DEMO_MIN_DURATION_MS",
    "STT_DEMO_MODE",
    "STT_DEMO_VERSION",
    "STTDemoCaptureReceipt",
    "STTDemoConsentReceipt",
    "STTDemoDisclosure",
    "STTDemoError",
    "STTDemoFailureCode",
]
