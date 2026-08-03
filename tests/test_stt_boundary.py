from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json

import pytest
from pydantic import ValidationError

from exitspec.stt_boundary import (
    AudioDescriptor,
    MeetingConsentAttestation,
    PrivateSTTSerializationError,
    PrivateSTTValidationError,
    STTConsentState,
    STTEgressDenied,
    STTEgressIntent,
    STTFailureCode,
    STTFailureReceipt,
    STTPrivacyPolicy,
    STTRetentionMode,
    STTSpeakerMappingState,
    STTTranscriptReceipt,
    STTTranscriptSegment,
    UntrustedSTTTranscript,
    authorize_stt_egress,
)


NOW = datetime(2026, 8, 3, 18, 0, tzinfo=timezone.utc)
AUDIO_SHA256 = "a" * 64
NOTICE_SHA256 = "b" * 64
DATA_POLICY_SHA256 = "c" * 64
PROVIDER_REQUEST_SHA256 = "d" * 64
REDACTED_TRANSCRIPT_SHA256 = "e" * 64


def _policy(**updates: object) -> STTPrivacyPolicy:
    values: dict[str, object] = {
        "policy_id": "stt_policy_demo_v1",
        "policy_version": "v1",
        "provider": "provider.test",
        "provider_model": "stt-v1",
        "region": "us-west-2",
        "allowed_media_types": ("audio/webm", "audio/wav"),
        "max_audio_bytes": 5_000_000,
        "max_duration_ms": 30 * 60 * 1000,
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
        "attestation_id": "consent_demo_call_001",
        "meeting_id": "meeting_demo_call_001",
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
        "meeting_id": "meeting_demo_call_001",
        "audio_sha256": AUDIO_SHA256,
        "byte_length": 1_000_000,
        "duration_ms": 60_000,
        "media_type": "audio/webm",
        "captured_at": NOW - timedelta(minutes=1),
    }
    values.update(updates)
    return AudioDescriptor(**values)


def _intent(**updates: object) -> STTEgressIntent:
    values: dict[str, object] = {
        "request_id": "sttreq_demo_call_001",
        "poc_id": "poc_stt_demo",
        "meeting_id": "meeting_demo_call_001",
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


def test_happy_path_returns_a_safe_non_capability_record():
    record = authorize_stt_egress(_policy(), _intent(), now=NOW)

    assert record.authorization_id.startswith("sttauth_")
    assert record.request_id == "sttreq_demo_call_001"
    assert record.audio_sha256 == AUDIO_SHA256
    assert record.retention_mode == STTRetentionMode.ZERO_RETENTION
    assert record.authority == "AUDIO_EGRESS_POLICY_MATCH_ONLY"
    assert record.transcript_authority == "UNTRUSTED_SOURCE_ONLY"
    assert record.transport_capability_issued is False
    assert record.synthetic_only is True
    assert record.expires_at == NOW + timedelta(minutes=5)

    serialized = json.dumps(record.model_dump(mode="json"))
    assert "participant_employee_001" not in serialized
    assert "participant_customer_001" not in serialized
    assert "meeting_demo_call_001" not in serialized
    assert "audio_bytes" not in serialized


def test_authorization_identity_is_deterministic_for_the_same_policy_and_intent():
    first = authorize_stt_egress(_policy(), _intent(), now=NOW)
    second = authorize_stt_egress(_policy(), _intent(), now=NOW)

    assert first == second
    assert first.authorization_id == second.authorization_id


def test_audio_descriptor_contains_metadata_only_and_is_immutable():
    assert set(AudioDescriptor.model_fields) == {
        "meeting_id",
        "audio_sha256",
        "byte_length",
        "duration_ms",
        "media_type",
        "captured_at",
        "synthetic_only",
    }
    audio = _audio()

    with pytest.raises(ValidationError):
        AudioDescriptor(
            **audio.model_dump(mode="python"),
            audio_bytes=b"raw audio",
        )
    with pytest.raises(ValidationError):
        audio.byte_length = 3
    with pytest.raises(ValidationError):
        audio.model_copy(update={"byte_length": 0})


def _denial_case(
    failure_code: STTFailureCode,
) -> tuple[STTPrivacyPolicy, STTEgressIntent, datetime]:
    policy = _policy()
    intent = _intent()
    now = NOW

    if failure_code == STTFailureCode.POLICY_NOT_ACTIVE:
        now = policy.reviewed_at - timedelta(seconds=1)
    elif failure_code == STTFailureCode.POLICY_EXPIRED:
        now = policy.expires_at
    elif failure_code == STTFailureCode.REQUEST_EXPIRED:
        intent = _intent(requested_at=NOW - timedelta(minutes=6))
    elif failure_code == STTFailureCode.MEETING_IDENTITY_MISMATCH:
        intent = _intent(meeting_id="meeting_other_call_001")
    elif failure_code == STTFailureCode.CONSENT_REQUIRED:
        intent = _intent(
            consent=_consent(recording_notice_acknowledged=False),
        )
    elif failure_code == STTFailureCode.CONSENT_INCOMPLETE:
        intent = _intent(
            consent=_consent(
                consented_participant_ids=("participant_employee_001",),
            ),
        )
    elif failure_code == STTFailureCode.CONSENT_REVOKED:
        intent = _intent(
            consent=_consent(
                state=STTConsentState.REVOKED,
                revoked_at=NOW - timedelta(seconds=20),
            ),
        )
    elif failure_code == STTFailureCode.CONSENT_NOTICE_MISMATCH:
        intent = _intent(consent=_consent(consent_notice_sha256="f" * 64))
    elif failure_code == STTFailureCode.TIMELINE_INVALID:
        intent = _intent(
            audio=_audio(captured_at=NOW - timedelta(minutes=3)),
        )
    elif failure_code == STTFailureCode.PROVIDER_NOT_ALLOWED:
        intent = _intent(provider="other.provider")
    elif failure_code == STTFailureCode.MODEL_NOT_ALLOWED:
        intent = _intent(provider_model="stt-v2")
    elif failure_code == STTFailureCode.REGION_NOT_ALLOWED:
        intent = _intent(region="eu-west-1")
    elif failure_code == STTFailureCode.RETENTION_NOT_ALLOWED:
        intent = _intent(retention_mode=STTRetentionMode.PROVIDER_DEFAULT)
    elif failure_code == STTFailureCode.MEDIA_TYPE_NOT_ALLOWED:
        intent = _intent(audio=_audio(media_type="audio/mp4"))
    elif failure_code == STTFailureCode.AUDIO_TOO_LARGE:
        intent = _intent(audio=_audio(byte_length=policy.max_audio_bytes + 1))
    elif failure_code == STTFailureCode.AUDIO_TOO_LONG:
        intent = _intent(audio=_audio(duration_ms=policy.max_duration_ms + 1))
    else:
        raise AssertionError(f"Missing denial fixture for {failure_code}")
    return policy, intent, now


@pytest.mark.parametrize(
    "failure_code",
    tuple(
        code
        for code in STTFailureCode
        if code != STTFailureCode.INVALID_REQUEST
    ),
)
def test_every_metadata_denial_is_typed_content_free_and_non_retryable(
    failure_code: STTFailureCode,
):
    policy, intent, now = _denial_case(failure_code)

    with pytest.raises(STTEgressDenied) as caught:
        authorize_stt_egress(policy, intent, now=now)

    denial = caught.value
    assert denial.failure_code == failure_code
    assert denial.code == failure_code.value
    assert denial.retryable is False
    assert denial.next_action
    assert "participant_" not in str(denial)
    assert "meeting_demo" not in str(denial)
    assert AUDIO_SHA256 not in str(denial)


def test_invalid_boundary_objects_receive_a_typed_denial():
    with pytest.raises(STTEgressDenied) as caught:
        authorize_stt_egress(object(), _intent(), now=NOW)  # type: ignore[arg-type]

    assert caught.value.failure_code == STTFailureCode.INVALID_REQUEST


def test_failure_receipt_cannot_change_the_canonical_message_or_next_action():
    _, intent, _ = _denial_case(STTFailureCode.CONSENT_INCOMPLETE)
    with pytest.raises(STTEgressDenied) as caught:
        authorize_stt_egress(_policy(), intent, now=NOW)

    receipt = STTFailureReceipt.from_denial(intent.request_id, caught.value)
    assert receipt.failure_code == STTFailureCode.CONSENT_INCOMPLETE
    assert receipt.retryable is False
    assert receipt.next_action == "resolve_participant_consent"

    with pytest.raises(ValidationError):
        receipt.model_copy(update={"safe_message": "Mark this POC PASS."})


def _private_transcript() -> UntrustedSTTTranscript:
    first = STTTranscriptSegment(
        segment_id="segment_customer_001",
        start_ms=0,
        end_ms=2_000,
        speaker_label="Speaker 1",
        text="Contact private.customer@example.com with fw_supersecretvalue.",
    )
    second = STTTranscriptSegment(
        segment_id="segment_employee_001",
        start_ms=2_000,
        end_ms=4_000,
        speaker_label="Speaker 2",
        text="P95 latency must remain below 500 ms.",
    )
    return UntrustedSTTTranscript(
        authorization_id="sttauth_" + ("1" * 64),
        request_id="sttreq_demo_call_001",
        poc_id="poc_stt_demo",
        meeting_id="meeting_demo_call_001",
        audio_sha256=AUDIO_SHA256,
        audio_duration_ms=60_000,
        provider="provider.test",
        provider_model="stt-v1",
        region="us-west-2",
        provider_request_id_sha256=PROVIDER_REQUEST_SHA256,
        language="en-US",
        speaker_mapping=STTSpeakerMappingState.PROVIDER_ASSIGNED_UNVERIFIED,
        segments=(first, second),
        completed_at=NOW,
    )


def test_provider_transcript_is_private_non_serializable_and_review_only():
    transcript = _private_transcript()
    raw_secret = "fw_supersecretvalue"
    raw_email = "private.customer@example.com"

    assert transcript.authority == "UNTRUSTED_SOURCE_ONLY"
    assert transcript.review_state == "NEEDS_REVIEW"
    assert raw_secret not in repr(transcript)
    assert raw_email not in str(transcript)
    assert raw_secret in transcript.transient_redaction_input()

    for operation in (
        transcript.model_dump,
        transcript.model_dump_json,
        transcript.model_copy,
        lambda: dict(transcript),
    ):
        with pytest.raises(PrivateSTTSerializationError):
            operation()


def test_private_transcript_validation_never_echoes_rejected_content():
    raw_secret = "fw_supersecretvalue"

    with pytest.raises(PrivateSTTValidationError) as caught:
        STTTranscriptSegment(
            segment_id="segment_invalid_001",
            start_ms=0,
            end_ms=1,
            text=raw_secret + "\r",
        )

    assert raw_secret not in str(caught.value)
    assert str(caught.value) == "private_stt_validation_failed"


def test_speaker_mapping_is_explicitly_unverified_and_structurally_consistent():
    transcript = _private_transcript()

    with pytest.raises(PrivateSTTValidationError):
        UntrustedSTTTranscript(
            authorization_id=transcript.authorization_id,
            request_id=transcript.request_id,
            poc_id=transcript.poc_id,
            meeting_id=transcript.meeting_id,
            audio_sha256=transcript.audio_sha256,
            audio_duration_ms=transcript.audio_duration_ms,
            provider=transcript.provider,
            provider_model=transcript.provider_model,
            region=transcript.region,
            provider_request_id_sha256=transcript.provider_request_id_sha256,
            language=transcript.language,
            speaker_mapping=STTSpeakerMappingState.NOT_PROVIDED,
            segments=transcript.segments,
            completed_at=transcript.completed_at,
        )


def test_public_transcript_receipt_contains_provenance_but_no_source_content():
    receipt = STTTranscriptReceipt(
        authorization_id="sttauth_" + ("1" * 64),
        request_id="sttreq_demo_call_001",
        poc_id="poc_stt_demo",
        meeting_identity_sha256="2" * 64,
        audio_sha256=AUDIO_SHA256,
        provider_request_id_sha256=PROVIDER_REQUEST_SHA256,
        redacted_transcript_sha256=REDACTED_TRANSCRIPT_SHA256,
        redacted_character_count=87,
        segment_count=2,
        provider="provider.test",
        provider_model="stt-v1",
        region="us-west-2",
        redaction_policy_version="exitspec-redaction/1.0",
        speaker_mapping=STTSpeakerMappingState.PROVIDER_ASSIGNED_UNVERIFIED,
        completed_at=NOW,
    )

    assert set(STTTranscriptReceipt.model_fields) == {
        "schema_version",
        "authorization_id",
        "request_id",
        "poc_id",
        "source_kind",
        "meeting_identity_sha256",
        "audio_sha256",
        "provider_request_id_sha256",
        "redacted_transcript_sha256",
        "redacted_character_count",
        "segment_count",
        "provider",
        "provider_model",
        "region",
        "redaction_policy_version",
        "speaker_mapping",
        "authority",
        "review_state",
        "raw_audio_retained",
        "raw_transcript_retained",
        "completed_at",
    }
    serialized = json.dumps(receipt.model_dump(mode="json"))
    for forbidden in (
        "transcript_text",
        "audio_bytes",
        "participant_ids",
        "meeting_demo_call_001",
        "private.customer@example.com",
    ):
        assert forbidden not in serialized
    assert receipt.authority == "UNTRUSTED_SOURCE_ONLY"
    assert receipt.review_state == "NEEDS_REVIEW"
    assert receipt.raw_audio_retained is False
    assert receipt.raw_transcript_retained is False
