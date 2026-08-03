from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
import json
from typing import Any

import pytest

from exitspec.canonical import canonical_json_bytes
from exitspec.intake import TranscriptIntakeError
from exitspec.poc_creation import (
    DraftPOCCreateRequest,
    FirstSourceChoice,
    ProcessLocalDraftPOCService,
)
from exitspec.poc_source_intake import (
    POCSourceIntakeCapacityExceeded,
    POCSourceIntakeInvalid,
    ProcessLocalPOCSourceIntake,
)
from exitspec.poc_sources import (
    CandidateState,
    POCSourceDraftUnavailable,
    POCSourceIdempotencyConflict,
    SourceKind,
)
from exitspec.stt_boundary import (
    AudioDescriptor,
    MeetingConsentAttestation,
    STTConsentState,
    STTEgressIntent,
    STTPrivacyPolicy,
    STTRetentionMode,
    STTSpeakerMappingState,
)
from exitspec.stt_handoff import (
    STT_HANDOFF_VERSION,
    STTTranscriptHandoffError,
    STTTranscriptHandoffFailureCode,
    STTTranscriptHandoffResult,
    STTTranscriptHandoffService,
)
from exitspec.stt_operation import (
    STTAudioPermitIssuer,
    STTOperationExecutor,
    STTOperationReceipt,
    STTOperationResult,
    STTTransportRequest,
    STTTransportResponse,
    STTTransportSegment,
)


NOW = datetime(2026, 8, 3, 20, 0, tzinfo=timezone.utc)
HANDOFF_AT = NOW + timedelta(seconds=1)
AUDIO_BYTES = b"ExitSpec synthetic handoff audio fixture v1"
AUDIO_SHA256 = hashlib.sha256(AUDIO_BYTES).hexdigest()
NOTICE_SHA256 = "b" * 64
DATA_POLICY_SHA256 = "c" * 64
RAW_EMAIL = "private.customer@example.com"
RAW_TOKEN = "fw_abcdefghijklmnopqrstuvwxyz"
RAW_SPEAKER_LABEL = "customer.owner@example.com"


class SyntheticTransport:
    def __init__(self, response: STTTransportResponse) -> None:
        self.response = response
        self.calls = 0

    def transcribe(self, request: STTTransportRequest) -> STTTransportResponse:
        assert hashlib.sha256(request.read_audio_bytes()).hexdigest() == (
            AUDIO_SHA256
        )
        self.calls += 1
        return self.response


class SequenceMonotonic:
    def __init__(self, *values: float) -> None:
        self.values = list(values)

    def __call__(self) -> float:
        return self.values.pop(0)


def _policy() -> STTPrivacyPolicy:
    return STTPrivacyPolicy(
        policy_id="stt_policy_handoff_v1",
        policy_version="v1",
        provider="provider.test",
        provider_model="stt-v1",
        region="us-west-2",
        allowed_media_types=("audio/webm",),
        max_audio_bytes=1_000_000,
        max_duration_ms=60_000,
        transport_timeout_seconds=30.0,
        provider_data_policy_sha256=DATA_POLICY_SHA256,
        consent_notice_sha256=NOTICE_SHA256,
        deletion_policy_ref="policy://audio-deletion-v1",
        incident_response_policy_ref="policy://incident-response-v1",
        reviewed_at=NOW - timedelta(days=1),
        expires_at=NOW + timedelta(days=30),
    )


def _consent() -> MeetingConsentAttestation:
    return MeetingConsentAttestation(
        attestation_id="consent_handoff_call_001",
        meeting_id="meeting_handoff_call_001",
        participant_ids=(
            "participant_employee_001",
            "participant_customer_001",
        ),
        consented_participant_ids=(
            "participant_employee_001",
            "participant_customer_001",
        ),
        recording_notice_acknowledged=True,
        consent_notice_sha256=NOTICE_SHA256,
        state=STTConsentState.GRANTED,
        attested_by="employee:demo",
        attested_at=NOW - timedelta(minutes=2),
    )


def _intent() -> STTEgressIntent:
    return STTEgressIntent(
        request_id="sttreq_handoff_call_001",
        poc_id="poc_stt_handoff",
        meeting_id="meeting_handoff_call_001",
        audio=AudioDescriptor(
            meeting_id="meeting_handoff_call_001",
            audio_sha256=AUDIO_SHA256,
            byte_length=len(AUDIO_BYTES),
            duration_ms=10_000,
            media_type="audio/webm",
            captured_at=NOW - timedelta(minutes=1),
        ),
        consent=_consent(),
        provider="provider.test",
        provider_model="stt-v1",
        region="us-west-2",
        retention_mode=STTRetentionMode.ZERO_RETENTION,
        requested_at=NOW - timedelta(seconds=10),
    )


def _response(
    *,
    latency_claim: str = "P95 latency must remain below 500 ms.",
    speaker_mapping: STTSpeakerMappingState = (
        STTSpeakerMappingState.PROVIDER_ASSIGNED_UNVERIFIED
    ),
) -> STTTransportResponse:
    labels: tuple[str | None, ...]
    if speaker_mapping == STTSpeakerMappingState.NOT_PROVIDED:
        labels = (None, None, None)
    else:
        labels = (
            RAW_SPEAKER_LABEL,
            RAW_SPEAKER_LABEL,
            "provider-speaker-employee",
        )
    return STTTransportResponse(
        provider_request_id="provider-handoff-request-001",
        language="en-US",
        speaker_mapping=speaker_mapping,
        segments=(
            STTTransportSegment(
                start_ms=0,
                end_ms=2_000,
                speaker_label=labels[0],
                text=(
                    f"Contact {RAW_EMAIL} with token {RAW_TOKEN}."
                ),
            ),
            STTTransportSegment(
                start_ms=2_000,
                end_ms=4_000,
                speaker_label=labels[1],
                text=latency_claim,
            ),
            STTTransportSegment(
                start_ms=4_000,
                end_ms=6_000,
                speaker_label=labels[2],
                text="Error rate must remain below 1%.",
            ),
        ),
    )


def _operation(
    response: STTTransportResponse | None = None,
) -> STTOperationResult:
    permit = STTAudioPermitIssuer(
        _policy(),
        clock=lambda: NOW,
    ).issue(_intent(), AUDIO_BYTES)
    return STTOperationExecutor(
        SyntheticTransport(_response() if response is None else response),
        enabled=True,
        clock=lambda: NOW,
        monotonic=SequenceMonotonic(1.0, 1.125),
    ).execute(permit)


def _runtime() -> tuple[
    ProcessLocalPOCSourceIntake,
    ProcessLocalDraftPOCService,
]:
    drafts = ProcessLocalDraftPOCService(clock=lambda: HANDOFF_AT)
    drafts.create(
        DraftPOCCreateRequest(
            poc_id="poc_stt_handoff",
            display_name="STT requirement proof",
            customer_label="Synthetic customer",
            use_case="Capture review-only meeting requirements.",
            owner="field_engineer",
            first_source_choice=FirstSourceChoice.MEETING,
        ),
        idempotency_key="create-stt-handoff",
    )
    return (
        ProcessLocalPOCSourceIntake(
            draft_lookup=drafts.get,
            clock=lambda: HANDOFF_AT,
        ),
        drafts,
    )


def _service(
    runtime: ProcessLocalPOCSourceIntake,
    *,
    clock=lambda: HANDOFF_AT,
) -> STTTranscriptHandoffService:
    return STTTranscriptHandoffService(runtime, clock=clock)


def _snapshots(runtime: ProcessLocalPOCSourceIntake):
    return runtime._source_service.snapshots("poc_stt_handoff")


def test_handoff_redacts_and_attaches_one_review_only_meeting_source():
    runtime, _ = _runtime()

    result = _service(runtime).handoff(_operation())

    assert result.schema_version == STT_HANDOFF_VERSION
    assert result.source_receipt.source_kind == SourceKind.MEETING
    assert result.source_receipt.status == "NEEDS_REVIEW"
    assert result.source_receipt.proposal_count == 2
    transcript_receipt = result.transcript_receipt
    assert transcript_receipt.source_receipt_id == (
        result.source_receipt.source_receipt_id
    )
    assert transcript_receipt.operation_id.startswith("sttop_")
    assert transcript_receipt.review_state == "NEEDS_REVIEW"
    assert transcript_receipt.authority == "UNTRUSTED_SOURCE_ONLY"
    assert transcript_receipt.raw_audio_retained is False
    assert transcript_receipt.raw_transcript_retained is False

    source = _snapshots(runtime)[0]
    assert source.adapter_name == "synthetic_stt"
    assert source.kind == SourceKind.MEETING
    assert source.content_sha256 == (
        transcript_receipt.redacted_transcript_sha256
    )
    assert len(source.redacted_text) == (
        transcript_receipt.redacted_character_count
    )
    assert "Speaker 1:" in source.redacted_text
    assert "Speaker 2:" in source.redacted_text
    for private_value in (
        RAW_EMAIL,
        RAW_TOKEN,
        RAW_SPEAKER_LABEL,
        "provider-speaker-employee",
    ):
        assert private_value not in source.redacted_text
        assert private_value not in repr(runtime._source_service._sources_by_poc)
        assert private_value not in json.dumps(result.model_dump(mode="json"))

    proposals = runtime.proposal_inputs("poc_stt_handoff")
    assert len(proposals) == 2
    assert all(
        proposal.source_kind == SourceKind.MEETING
        and proposal.source_receipt_id
        == result.source_receipt.source_receipt_id
        and proposal.state == "NEEDS_REVIEW"
        for proposal in proposals
    )
    assert all(
        candidate.state == CandidateState.NEEDS_REVIEW
        for candidate in source.candidates
    )


def test_missing_provider_speaker_mapping_remains_explicitly_unknown():
    runtime, _ = _runtime()
    operation = _operation(
        _response(speaker_mapping=STTSpeakerMappingState.NOT_PROVIDED)
    )

    result = _service(runtime).handoff(operation)

    source = _snapshots(runtime)[0]
    assert source.redacted_text.count("Speaker unknown:") == 3
    assert result.transcript_receipt.speaker_mapping == (
        STTSpeakerMappingState.NOT_PROVIDED
    )


def test_exact_replay_returns_the_same_source_without_duplicate_proposals():
    runtime, _ = _runtime()
    service = _service(runtime)
    operation = _operation()

    first = service.handoff(operation)
    replay = service.handoff(operation)

    assert first.source_receipt.idempotent_replay is False
    assert replay.source_receipt.idempotent_replay is True
    assert replay.source_receipt.source_receipt_id == (
        first.source_receipt.source_receipt_id
    )
    assert replay.transcript_receipt == first.transcript_receipt
    assert len(_snapshots(runtime)) == 1
    assert len(runtime.proposal_inputs("poc_stt_handoff")) == 2


def test_concurrent_exact_replay_writes_one_source():
    runtime, _ = _runtime()
    service = _service(runtime)
    operation = _operation()

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = tuple(pool.map(lambda _: service.handoff(operation), range(8)))

    receipt_ids = {
        result.source_receipt.source_receipt_id for result in results
    }
    assert len(receipt_ids) == 1
    assert sum(
        not result.source_receipt.idempotent_replay for result in results
    ) == 1
    assert len(_snapshots(runtime)) == 1


def _operation_id(payload: dict[str, Any]) -> str:
    binding = {
        "authorization_id": payload["authorization_id"],
        "provider_request_id_sha256": payload[
            "provider_request_id_sha256"
        ],
        "segment_count": payload["segment_count"],
    }
    digest = hashlib.sha256(
        b"exitspec-stt-operation-id-v1\x00"
        + canonical_json_bytes(binding)
    ).hexdigest()
    return "sttop_" + digest


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("authorization_id", "sttauth_" + ("1" * 64)),
        ("request_id", "sttreq_different_handoff"),
        ("poc_id", "poc_different_handoff"),
        ("audio_sha256", "2" * 64),
        ("duration_ms", 9_000),
        ("provider", "provider.other"),
        ("provider_model", "stt-v2"),
        ("region", "us-east-1"),
        ("provider_request_id_sha256", "3" * 64),
        ("segment_count", 2),
        ("completed_at", NOW + timedelta(milliseconds=1)),
    ),
)
def test_operation_receipt_mismatch_fails_before_redaction_or_source_write(
    field: str,
    value: object,
):
    runtime, _ = _runtime()
    operation = _operation()
    payload = operation.receipt.model_dump(mode="python")
    payload[field] = value
    payload["operation_id"] = _operation_id(payload)
    mismatched = STTOperationReceipt.model_validate(payload)
    object.__setattr__(operation, "_receipt", mismatched)

    with pytest.raises(STTTranscriptHandoffError) as caught:
        _service(runtime).handoff(operation)

    assert caught.value.failure_code == (
        STTTranscriptHandoffFailureCode.BINDING_MISMATCH
    )
    assert _snapshots(runtime) == ()


def test_same_operation_identity_with_changed_content_fails_closed():
    runtime, _ = _runtime()
    service = _service(runtime)
    first = _operation()
    changed = _operation(
        _response(latency_claim="P95 latency must remain below 900 ms.")
    )
    assert changed.receipt.operation_id == first.receipt.operation_id
    service.handoff(first)

    with pytest.raises(STTTranscriptHandoffError) as caught:
        service.handoff(changed)

    assert caught.value.failure_code == (
        STTTranscriptHandoffFailureCode.SOURCE_CONFLICT
    )
    assert len(_snapshots(runtime)) == 1
    assert "900 ms" not in _snapshots(runtime)[0].redacted_text


def test_redaction_exception_is_sanitized_and_writes_nothing(
    monkeypatch: pytest.MonkeyPatch,
):
    runtime, _ = _runtime()

    def fail_redaction(*args: object, **kwargs: object) -> None:
        raise TranscriptIntakeError(RAW_TOKEN)

    monkeypatch.setattr(
        "exitspec.stt_handoff.redact_and_parse_pasted_transcript",
        fail_redaction,
    )

    with pytest.raises(STTTranscriptHandoffError) as caught:
        _service(runtime).handoff(_operation())

    assert caught.value.failure_code == (
        STTTranscriptHandoffFailureCode.REDACTION_FAILED
    )
    assert RAW_TOKEN not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert _snapshots(runtime) == ()


def test_archived_draft_blocks_handoff_without_a_source_write():
    runtime, drafts = _runtime()
    drafts.archive("poc_stt_handoff")

    with pytest.raises(STTTranscriptHandoffError) as caught:
        _service(runtime).handoff(_operation())

    assert caught.value.failure_code == (
        STTTranscriptHandoffFailureCode.SOURCE_UNAVAILABLE
    )
    assert _snapshots(runtime) == ()


@pytest.mark.parametrize(
    ("source_error", "expected_code"),
    (
        (
            POCSourceDraftUnavailable(RAW_TOKEN),
            STTTranscriptHandoffFailureCode.SOURCE_UNAVAILABLE,
        ),
        (
            POCSourceIdempotencyConflict(RAW_TOKEN),
            STTTranscriptHandoffFailureCode.SOURCE_CONFLICT,
        ),
        (
            POCSourceIntakeCapacityExceeded(RAW_TOKEN),
            STTTranscriptHandoffFailureCode.CAPACITY_EXCEEDED,
        ),
        (
            POCSourceIntakeInvalid(RAW_TOKEN),
            STTTranscriptHandoffFailureCode.REDACTION_FAILED,
        ),
    ),
)
def test_source_failures_are_typed_and_never_echo_private_content(
    monkeypatch: pytest.MonkeyPatch,
    source_error: Exception,
    expected_code: STTTranscriptHandoffFailureCode,
):
    runtime, _ = _runtime()

    def fail_source(*args: object, **kwargs: object) -> None:
        raise source_error

    monkeypatch.setattr(
        ProcessLocalPOCSourceIntake,
        "capture_stt_transcript",
        fail_source,
    )

    with pytest.raises(STTTranscriptHandoffError) as caught:
        _service(runtime).handoff(_operation())

    assert caught.value.failure_code == expected_code
    assert RAW_TOKEN not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


@pytest.mark.parametrize(
    "clock",
    (
        lambda: datetime(2026, 8, 3, 20, 0),
        lambda: NOW - timedelta(seconds=1),
        lambda: "not-a-time",
        lambda: (_ for _ in ()).throw(RuntimeError(RAW_TOKEN)),
    ),
)
def test_invalid_handoff_clock_fails_before_redaction_or_source_write(clock):
    runtime, _ = _runtime()

    with pytest.raises(STTTranscriptHandoffError) as caught:
        _service(runtime, clock=clock).handoff(_operation())

    assert caught.value.failure_code == (
        STTTranscriptHandoffFailureCode.INTERNAL
    )
    assert RAW_TOKEN not in str(caught.value)
    assert _snapshots(runtime) == ()


def test_invalid_result_and_forged_source_bindings_fail_closed():
    runtime, _ = _runtime()
    valid_operation = _operation()

    with pytest.raises(STTTranscriptHandoffError) as caught:
        _service(runtime).handoff(object())  # type: ignore[arg-type]
    assert caught.value.failure_code == (
        STTTranscriptHandoffFailureCode.INVALID_RESULT
    )
    with pytest.raises(ValueError):
        STTOperationResult(
            receipt=valid_operation.receipt,
            transcript=valid_operation.transcript,
            _seal=object(),
        )

    with pytest.raises(POCSourceIntakeInvalid):
        runtime.capture_stt_transcript(
            poc_id="poc_stt_handoff",
            redacted_transcript_text="Speaker 1: Latency must be below 1 ms.",
            expected_content_sha256="0" * 64,
            operation_id="sttop_" + ("0" * 64),
            idempotency_key="forged-stt-source",
        )
    assert _snapshots(runtime) == ()


def test_handoff_result_is_content_free_frozen_and_exactly_linked():
    runtime, _ = _runtime()
    result = _service(runtime).handoff(_operation())
    rendered = json.dumps(result.model_dump(mode="json"))

    assert set(STTTranscriptHandoffResult.model_fields) == {
        "schema_version",
        "source_receipt",
        "transcript_receipt",
    }
    for forbidden in (
        RAW_EMAIL,
        RAW_TOKEN,
        RAW_SPEAKER_LABEL,
        "meeting_handoff_call_001",
        "participant_customer_001",
        AUDIO_BYTES.decode(),
    ):
        assert forbidden not in rendered
    with pytest.raises(Exception):
        result.source_receipt = result.source_receipt
