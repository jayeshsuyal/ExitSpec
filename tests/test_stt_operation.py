from __future__ import annotations

import hashlib
import json
import pickle
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from pydantic import ValidationError

from exitspec.stt_boundary import (
    AudioDescriptor,
    MeetingConsentAttestation,
    PrivateSTTSerializationError,
    STTConsentState,
    STTEgressIntent,
    STTPrivacyPolicy,
    STTRetentionMode,
    STTSpeakerMappingState,
)
from exitspec.stt_operation import (
    STTAudioPermit,
    STTAudioPermitIssuer,
    STTOperationError,
    STTOperationExecutor,
    STTOperationFailureCode,
    STTOperationReceipt,
    STTTransportError,
    STTTransportRequest,
    STTTransportResponse,
    STTTransportSegment,
)


NOW = datetime(2026, 8, 3, 19, 0, tzinfo=timezone.utc)
AUDIO_BYTES = b"ExitSpec synthetic audio fixture v1"
AUDIO_SHA256 = hashlib.sha256(AUDIO_BYTES).hexdigest()
NOTICE_SHA256 = "b" * 64
DATA_POLICY_SHA256 = "c" * 64
RAW_PRIVATE_TEXT = "Contact private.customer@example.com with fw_private_value."


class SequenceMonotonic:
    def __init__(self, *values: float) -> None:
        self.values = list(values)

    def __call__(self) -> float:
        return self.values.pop(0)


class RecordingTransport:
    def __init__(self, outcome: object | None = None) -> None:
        self.outcome = _success_response() if outcome is None else outcome
        self.calls: list[dict[str, Any]] = []
        self.last_request: STTTransportRequest | None = None

    def transcribe(self, request: STTTransportRequest) -> STTTransportResponse:
        audio = request.read_audio_bytes()
        self.last_request = request
        self.calls.append(
            {
                "authorization_id": request.authorization.authorization_id,
                "audio_sha256": hashlib.sha256(audio).hexdigest(),
                "audio_length": len(audio),
                "timeout_seconds": request.timeout_seconds,
            }
        )
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome  # type: ignore[return-value]

    def __repr__(self) -> str:
        return "RecordingTransport(<private>)"


class BlockingTransport(RecordingTransport):
    def __init__(self) -> None:
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()

    def transcribe(self, request: STTTransportRequest) -> STTTransportResponse:
        audio = request.read_audio_bytes()
        self.calls.append(
            {
                "authorization_id": request.authorization.authorization_id,
                "audio_sha256": hashlib.sha256(audio).hexdigest(),
                "audio_length": len(audio),
                "timeout_seconds": request.timeout_seconds,
            }
        )
        self.entered.set()
        assert self.release.wait(timeout=5)
        return _success_response()


def _policy(**updates: object) -> STTPrivacyPolicy:
    values: dict[str, object] = {
        "policy_id": "stt_policy_operation_v1",
        "policy_version": "v1",
        "provider": "provider.test",
        "provider_model": "stt-v1",
        "region": "us-west-2",
        "allowed_media_types": ("audio/webm",),
        "max_audio_bytes": 1_000_000,
        "max_duration_ms": 60_000,
        "transport_timeout_seconds": 30.0,
        "provider_data_policy_sha256": DATA_POLICY_SHA256,
        "consent_notice_sha256": NOTICE_SHA256,
        "deletion_policy_ref": "policy://audio-deletion-v1",
        "incident_response_policy_ref": "policy://incident-response-v1",
        "reviewed_at": NOW - timedelta(days=1),
        "expires_at": NOW + timedelta(days=30),
    }
    values.update(updates)
    return STTPrivacyPolicy(**values)


def _consent(**updates: object) -> MeetingConsentAttestation:
    values: dict[str, object] = {
        "attestation_id": "consent_operation_call_001",
        "meeting_id": "meeting_operation_call_001",
        "participant_ids": (
            "participant_employee_001",
            "participant_customer_001",
        ),
        "consented_participant_ids": (
            "participant_employee_001",
            "participant_customer_001",
        ),
        "recording_notice_acknowledged": True,
        "consent_notice_sha256": NOTICE_SHA256,
        "state": STTConsentState.GRANTED,
        "attested_by": "employee:demo",
        "attested_at": NOW - timedelta(minutes=2),
    }
    values.update(updates)
    return MeetingConsentAttestation(**values)


def _audio(**updates: object) -> AudioDescriptor:
    values: dict[str, object] = {
        "meeting_id": "meeting_operation_call_001",
        "audio_sha256": AUDIO_SHA256,
        "byte_length": len(AUDIO_BYTES),
        "duration_ms": 10_000,
        "media_type": "audio/webm",
        "captured_at": NOW - timedelta(minutes=1),
    }
    values.update(updates)
    return AudioDescriptor(**values)


def _intent(**updates: object) -> STTEgressIntent:
    values: dict[str, object] = {
        "request_id": "sttreq_operation_call_001",
        "poc_id": "poc_stt_operation",
        "meeting_id": "meeting_operation_call_001",
        "audio": _audio(),
        "consent": _consent(),
        "provider": "provider.test",
        "provider_model": "stt-v1",
        "region": "us-west-2",
        "retention_mode": STTRetentionMode.ZERO_RETENTION,
        "requested_at": NOW - timedelta(seconds=10),
    }
    values.update(updates)
    return STTEgressIntent(**values)


def _success_response(**updates: object) -> STTTransportResponse:
    values: dict[str, object] = {
        "provider_request_id": "provider-request-001",
        "language": "en-US",
        "speaker_mapping": STTSpeakerMappingState.PROVIDER_ASSIGNED_UNVERIFIED,
        "segments": (
            STTTransportSegment(
                start_ms=0,
                end_ms=2_000,
                speaker_label="Speaker 1",
                text=RAW_PRIVATE_TEXT,
            ),
            STTTransportSegment(
                start_ms=2_000,
                end_ms=4_000,
                speaker_label="Speaker 2",
                text="P95 latency must remain below 500 ms.",
            ),
        ),
    }
    values.update(updates)
    return STTTransportResponse(**values)


def _permit(
    *,
    clock=lambda: NOW,
    intent: STTEgressIntent | None = None,
) -> tuple[STTAudioPermit, STTAudioPermitIssuer]:
    issuer = STTAudioPermitIssuer(_policy(), clock=clock)
    permit = issuer.issue(_intent() if intent is None else intent, AUDIO_BYTES)
    return permit, issuer


def _executor(
    transport: object,
    *,
    enabled: bool = True,
    clock=lambda: NOW,
    monotonic: object | None = None,
) -> STTOperationExecutor:
    return STTOperationExecutor(
        transport,  # type: ignore[arg-type]
        enabled=enabled,
        clock=clock,
        monotonic=(
            SequenceMonotonic(10.0, 10.125)
            if monotonic is None
            else monotonic
        ),
    )


def test_one_exact_permit_produces_private_untrusted_transcript_and_safe_receipt():
    permit, issuer = _permit()
    transport = RecordingTransport()

    result = _executor(transport).execute(permit)

    assert permit.is_taken is True
    assert issuer.was_issued(permit.authorization.authorization_id) is True
    assert len(transport.calls) == 1
    assert transport.calls[0]["audio_sha256"] == AUDIO_SHA256
    assert transport.calls[0]["audio_length"] == len(AUDIO_BYTES)
    assert transport.calls[0]["timeout_seconds"] == 30.0
    assert result.transcript.authority == "UNTRUSTED_SOURCE_ONLY"
    assert result.transcript.review_state == "NEEDS_REVIEW"
    assert RAW_PRIVATE_TEXT in result.transcript.transient_redaction_input()

    receipt = result.receipt
    assert receipt.status == "TRANSCRIBED_UNTRUSTED"
    assert receipt.authority == "UNTRUSTED_SOURCE_ONLY"
    assert receipt.attempts == 1
    assert receipt.automatic_retries == 0
    assert receipt.elapsed_ms == 125
    assert receipt.segment_count == 2
    assert receipt.policy_retention_mode == STTRetentionMode.ZERO_RETENTION
    assert receipt.exitspec_audio_persisted is False
    assert receipt.exitspec_transcript_persisted is False

    rendered = json.dumps(receipt.model_dump(mode="json"))
    for forbidden in (
        RAW_PRIVATE_TEXT,
        "private.customer@example.com",
        "fw_private_value",
        "provider-request-001",
        AUDIO_BYTES.decode(),
        "meeting_operation_call_001",
        "participant_customer_001",
    ):
        assert forbidden not in rendered


def test_transport_request_is_released_after_success():
    permit, _ = _permit()
    transport = RecordingTransport()

    _executor(transport).execute(permit)

    assert transport.last_request is not None
    assert not hasattr(transport.last_request, "meeting_id")
    assert "meeting_operation_call_001" not in repr(transport.last_request)
    with pytest.raises(STTOperationError) as caught:
        transport.last_request.read_audio_bytes()
    assert caught.value.failure_code == STTOperationFailureCode.INVALID_PERMIT


def test_executor_is_disabled_by_default_without_consuming_the_permit():
    permit, _ = _permit()
    transport = RecordingTransport()
    disabled = STTOperationExecutor(
        transport,
        clock=lambda: NOW,
        monotonic=SequenceMonotonic(1.0, 1.1),
    )

    with pytest.raises(STTOperationError) as caught:
        disabled.execute(permit)

    assert caught.value.failure_code == STTOperationFailureCode.DISABLED
    assert caught.value.attempts == 0
    assert permit.is_taken is False
    assert transport.calls == []

    result = _executor(transport).execute(permit)
    assert result.receipt.status == "TRANSCRIBED_UNTRUSTED"
    assert len(transport.calls) == 1


@pytest.mark.parametrize(
    "bad_audio",
    (
        b"different synthetic fixture bytes",
        bytearray(AUDIO_BYTES),
        memoryview(AUDIO_BYTES),
        None,
    ),
)
def test_audio_must_match_exact_immutable_bytes_before_permit_issuance(bad_audio):
    issuer = STTAudioPermitIssuer(_policy(), clock=lambda: NOW)

    with pytest.raises(STTOperationError) as caught:
        issuer.issue(_intent(), bad_audio)

    assert caught.value.failure_code == (
        STTOperationFailureCode.AUDIO_BINDING_MISMATCH
    )
    assert caught.value.attempts == 0
    assert "different synthetic" not in str(caught.value)

    permit = issuer.issue(_intent(), AUDIO_BYTES)
    assert permit.is_taken is False


def test_same_authorization_cannot_issue_two_private_permits():
    issuer = STTAudioPermitIssuer(_policy(), clock=lambda: NOW)
    first = issuer.issue(_intent(), AUDIO_BYTES)

    with pytest.raises(STTOperationError) as caught:
        issuer.issue(_intent(), AUDIO_BYTES)

    assert caught.value.failure_code == STTOperationFailureCode.REPLAYED_PERMIT
    assert first.is_taken is False


def test_process_local_permit_store_is_bounded_and_duplicate_precedes_capacity():
    issuer = STTAudioPermitIssuer(
        _policy(),
        clock=lambda: NOW,
        max_issued=1,
    )
    first_intent = _intent()
    issuer.issue(first_intent, AUDIO_BYTES)

    with pytest.raises(STTOperationError) as duplicate:
        issuer.issue(first_intent, AUDIO_BYTES)
    assert duplicate.value.failure_code == STTOperationFailureCode.REPLAYED_PERMIT

    with pytest.raises(STTOperationError) as capacity:
        issuer.issue(
            _intent(request_id="sttreq_operation_call_002"),
            AUDIO_BYTES,
        )
    assert capacity.value.failure_code == (
        STTOperationFailureCode.CAPACITY_EXCEEDED
    )
    assert capacity.value.attempts == 0


@pytest.mark.parametrize("max_issued", (0, -1, True, 100_001))
def test_permit_store_rejects_ambiguous_capacity(max_issued):
    with pytest.raises(ValueError):
        STTAudioPermitIssuer(
            _policy(),
            clock=lambda: NOW,
            max_issued=max_issued,
        )


def test_forged_permit_and_raw_audio_cannot_enter_the_executor():
    transport = RecordingTransport()
    executor = _executor(transport)

    with pytest.raises(STTOperationError) as raw_audio:
        executor.execute(AUDIO_BYTES)
    assert raw_audio.value.failure_code == STTOperationFailureCode.INVALID_PERMIT

    with pytest.raises(STTOperationError) as forged:
        STTAudioPermit(
            authorization=object(),  # type: ignore[arg-type]
            meeting_id="meeting_operation_call_001",
            audio_bytes=AUDIO_BYTES,
            clock=lambda: NOW,
        )
    assert forged.value.failure_code == STTOperationFailureCode.INVALID_PERMIT
    assert transport.calls == []


def test_expired_permit_is_consumed_without_transport():
    current = {"value": NOW}
    permit, _ = _permit(clock=lambda: current["value"])
    current["value"] = permit.authorization.expires_at
    transport = RecordingTransport()

    with pytest.raises(STTOperationError) as caught:
        _executor(transport, clock=lambda: current["value"]).execute(permit)

    assert caught.value.failure_code == STTOperationFailureCode.EXPIRED_PERMIT
    assert caught.value.attempts == 0
    assert permit.is_taken is True
    assert transport.calls == []


def test_consumed_permit_cannot_make_a_second_provider_call():
    permit, _ = _permit()
    transport = RecordingTransport()
    executor = _executor(
        transport,
        monotonic=SequenceMonotonic(1.0, 1.1, 2.0),
    )

    executor.execute(permit)
    with pytest.raises(STTOperationError) as caught:
        executor.execute(permit)

    assert caught.value.failure_code == STTOperationFailureCode.REPLAYED_PERMIT
    assert caught.value.attempts == 0
    assert len(transport.calls) == 1


def test_concurrent_consumers_make_exactly_one_transport_call():
    permit, _ = _permit()
    transport = BlockingTransport()
    results: list[object] = []
    errors: list[STTOperationError] = []

    def run() -> None:
        try:
            results.append(
                _executor(
                    transport,
                    monotonic=SequenceMonotonic(1.0, 1.1),
                ).execute(permit)
            )
        except STTOperationError as error:
            errors.append(error)

    first = threading.Thread(target=run)
    first.start()
    assert transport.entered.wait(timeout=5)
    second = threading.Thread(target=run)
    second.start()
    second.join(timeout=5)
    transport.release.set()
    first.join(timeout=5)

    assert len(transport.calls) == 1
    assert len(results) == 1
    assert len(errors) == 1
    assert errors[0].failure_code == STTOperationFailureCode.REPLAYED_PERMIT


@pytest.mark.parametrize(
    "failure_code",
    (
        STTOperationFailureCode.TRANSPORT_CONFIGURATION,
        STTOperationFailureCode.AUTHENTICATION,
        STTOperationFailureCode.ACCOUNT_UNAVAILABLE,
        STTOperationFailureCode.RATE_LIMITED,
        STTOperationFailureCode.TIMEOUT,
        STTOperationFailureCode.SERVICE_UNAVAILABLE,
        STTOperationFailureCode.TRANSPORT,
    ),
)
def test_transport_failure_matrix_is_typed_sanitized_and_never_retried(
    failure_code: STTOperationFailureCode,
):
    permit, _ = _permit()
    transport = RecordingTransport(STTTransportError(failure_code))

    with pytest.raises(STTOperationError) as caught:
        _executor(transport).execute(permit)

    error = caught.value
    assert error.failure_code == failure_code
    assert error.attempts == 1
    assert error.retryable is False
    assert error.automatic_retry_allowed is False
    assert error.next_action
    assert len(transport.calls) == 1
    assert permit.is_taken is True
    assert error.__context__ is None
    assert error.__cause__ is None
    for sensitive in (RAW_PRIVATE_TEXT, AUDIO_BYTES.decode(), AUDIO_SHA256):
        assert sensitive not in str(error)
        assert sensitive not in repr(error)


def test_builtin_timeout_is_sanitized_and_not_automatically_retried():
    permit, _ = _permit()
    raw_secret = "fw_timeout_private_value"
    transport = RecordingTransport(TimeoutError(raw_secret))

    with pytest.raises(STTOperationError) as caught:
        _executor(transport).execute(permit)

    assert caught.value.failure_code == STTOperationFailureCode.TIMEOUT
    assert caught.value.attempts == 1
    assert len(transport.calls) == 1
    assert raw_secret not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


def test_unexpected_adapter_bug_is_internal_and_content_free():
    permit, _ = _permit()
    raw_secret = "private-adapter-stack-value"
    transport = RecordingTransport(RuntimeError(raw_secret))

    with pytest.raises(STTOperationError) as caught:
        _executor(transport).execute(permit)

    assert caught.value.failure_code == STTOperationFailureCode.INTERNAL
    assert caught.value.attempts == 1
    assert raw_secret not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


@pytest.mark.parametrize(
    "response",
    (
        object(),
        _success_response(provider_request_id=""),
        _success_response(speaker_mapping="PROVIDER_ASSIGNED_UNVERIFIED"),
        _success_response(segments=(object(),)),
        _success_response(
            segments=(
                STTTransportSegment(start_ms=2, end_ms=1, text="bad timing"),
            ),
            speaker_mapping=STTSpeakerMappingState.NOT_PROVIDED,
        ),
        _success_response(
            segments=(
                STTTransportSegment(
                    start_ms=0,
                    end_ms=1,
                    speaker_label="Speaker 1",
                    text="label conflict",
                ),
            ),
            speaker_mapping=STTSpeakerMappingState.NOT_PROVIDED,
        ),
        _success_response(language="not a language"),
        _success_response(
            segments=(
                STTTransportSegment(
                    start_ms=0,
                    end_ms=20_000,
                    speaker_label="Speaker 1",
                    text="past audio end",
                ),
            ),
        ),
    ),
)
def test_malformed_provider_output_is_not_a_transcript_or_receipt(response):
    permit, _ = _permit()
    transport = RecordingTransport(response)

    with pytest.raises(STTOperationError) as caught:
        _executor(transport).execute(permit)

    assert caught.value.failure_code == STTOperationFailureCode.INVALID_RESPONSE
    assert caught.value.attempts == 1
    assert len(transport.calls) == 1
    assert permit.is_taken is True


def test_private_transport_objects_and_result_refuse_pickle_and_hide_content():
    response = _success_response()
    segment = response.segments[0]
    permit, _ = _permit()
    result = _executor(RecordingTransport(response)).execute(permit)

    for value in (response, segment, result):
        assert RAW_PRIVATE_TEXT not in repr(value)
        with pytest.raises(PrivateSTTSerializationError):
            pickle.dumps(value)

    with pytest.raises(PrivateSTTSerializationError):
        response.language = "fr-FR"
    with pytest.raises(PrivateSTTSerializationError):
        segment.text = "changed"


def test_transport_response_detaches_the_provider_segment_collection():
    source_segments = [
        STTTransportSegment(
            start_ms=0,
            end_ms=1,
            text="Synthetic segment.",
        )
    ]
    response = _success_response(
        segments=source_segments,
        speaker_mapping=STTSpeakerMappingState.NOT_PROVIDED,
    )

    source_segments.append(
        STTTransportSegment(start_ms=1, end_ms=2, text="Late mutation.")
    )

    assert len(response.segments) == 1


def test_safe_receipt_shape_has_no_transcript_audio_or_authority_expansion():
    permit, _ = _permit()
    receipt = _executor(RecordingTransport()).execute(permit).receipt

    assert set(STTOperationReceipt.model_fields) == {
        "schema_version",
        "operation_id",
        "authorization_id",
        "request_id",
        "poc_id",
        "meeting_identity_sha256",
        "audio_sha256",
        "provider_request_id_sha256",
        "provider",
        "provider_model",
        "region",
        "media_type",
        "byte_length",
        "duration_ms",
        "transport_timeout_seconds",
        "segment_count",
        "elapsed_ms",
        "attempts",
        "automatic_retries",
        "policy_retention_mode",
        "status",
        "authority",
        "exitspec_audio_persisted",
        "exitspec_transcript_persisted",
        "completed_at",
    }
    for forbidden in (
        "transcript",
        "audio_bytes",
        "participant_ids",
        "approval",
        "confirmation",
        "freeze",
        "verdict",
    ):
        assert forbidden not in STTOperationReceipt.model_fields

    with pytest.raises(ValidationError):
        receipt.model_copy(update={"automatic_retries": 1})


def test_operation_identity_is_deterministic_for_same_authorization_and_response():
    first_permit, _ = _permit()
    second_permit, _ = _permit()

    first = _executor(RecordingTransport()).execute(first_permit).receipt
    second = _executor(RecordingTransport()).execute(second_permit).receipt

    assert first.operation_id == second.operation_id
    assert first.provider_request_id_sha256 == second.provider_request_id_sha256


@pytest.mark.parametrize(
    ("transport", "enabled", "monotonic"),
    (
        (object(), True, SequenceMonotonic(1.0, 1.1)),
        (RecordingTransport(), "yes", SequenceMonotonic(1.0, 1.1)),
        (RecordingTransport(), True, object()),
    ),
)
def test_executor_rejects_ambiguous_configuration(
    transport,
    enabled,
    monotonic,
):
    with pytest.raises(ValueError):
        STTOperationExecutor(
            transport,
            enabled=enabled,
            clock=lambda: NOW,
            monotonic=monotonic,
        )


def test_executor_representation_is_content_transport_and_credential_free():
    raw_secret = "fw_executor_private_value"
    transport = RecordingTransport(RuntimeError(raw_secret))
    executor = _executor(transport)
    rendered = repr(executor)

    assert rendered == (
        "STTOperationExecutor(enabled=True, transport=<private>, "
        "automatic_retries=0)"
    )
    assert raw_secret not in rendered
    assert RAW_PRIVATE_TEXT not in rendered
    assert "RecordingTransport" not in rendered


def test_clock_failure_is_sanitized_before_and_after_transport():
    raw_secret = "private-clock-stack-value"

    def broken_clock():
        raise RuntimeError(raw_secret)

    issuer = STTAudioPermitIssuer(_policy(), clock=broken_clock)
    with pytest.raises(STTOperationError) as before:
        issuer.issue(_intent(), AUDIO_BYTES)
    assert before.value.failure_code == STTOperationFailureCode.INTERNAL
    assert before.value.attempts == 0
    assert before.value.__context__ is None
    assert raw_secret not in repr(before.value)

    permit, _ = _permit()
    transport = RecordingTransport()
    with pytest.raises(STTOperationError) as after:
        _executor(transport, clock=broken_clock).execute(permit)
    assert after.value.failure_code == STTOperationFailureCode.INTERNAL
    assert after.value.attempts == 1
    assert after.value.__context__ is None
    assert len(transport.calls) == 1
    assert permit.is_taken is True


def test_invalid_monotonic_clock_fails_before_consuming_audio():
    permit, _ = _permit()
    transport = RecordingTransport()

    with pytest.raises(STTOperationError) as caught:
        _executor(transport, monotonic=lambda: float("nan")).execute(permit)

    assert caught.value.failure_code == STTOperationFailureCode.INTERNAL
    assert caught.value.attempts == 0
    assert permit.is_taken is False
    assert transport.calls == []
