from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from exitspec.poc_creation import (
    DraftPOCCreateRequest,
    FirstSourceChoice,
    ProcessLocalDraftPOCService,
)
from exitspec.poc_source_intake import ProcessLocalPOCSourceIntake
from exitspec.stt_demo_runtime import (
    ProcessLocalSTTDemoRuntime,
    STT_DEMO_DISCLOSURE_ID,
    STT_DEMO_MEDIA_TYPE,
    STTDemoError,
    STTDemoFailureCode,
)


NOW = datetime(2026, 8, 3, 20, 0, tzinfo=timezone.utc)
WEBM_SIGNATURE = b"\x1a\x45\xdf\xa3"
AUDIO = WEBM_SIGNATURE + b"browser synthetic audio bytes"


class MutableClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value


def _runtime(*, clock=None, poc_id: str = "poc_stt_browser_demo"):
    selected_clock = clock or (lambda: NOW)
    drafts = ProcessLocalDraftPOCService(clock=selected_clock)
    drafts.create(
        DraftPOCCreateRequest(
            poc_id=poc_id,
            display_name="Browser STT demo",
            customer_label="Synthetic customer",
            use_case="Prove a review-only browser recording handoff.",
            owner="field_engineer",
            first_source_choice=FirstSourceChoice.MEETING,
        ),
        idempotency_key="create-stt-browser-demo",
    )
    intake = ProcessLocalPOCSourceIntake(
        draft_lookup=drafts.get,
        clock=selected_clock,
    )
    return (
        ProcessLocalSTTDemoRuntime(
            drafts=drafts,
            source_intake=intake,
            clock=selected_clock,
        ),
        drafts,
        intake,
    )


def _consent(runtime, *, poc_id="poc_stt_browser_demo", key="consent-key-001"):
    return runtime.record_consent(
        poc_id=poc_id,
        disclosure_id=STT_DEMO_DISCLOSURE_ID,
        recording_notice_acknowledged=True,
        all_speakers_consented=True,
        synthetic_demo_acknowledged=True,
        idempotency_key=key,
    )


def _capture(
    runtime,
    capture_id: str,
    *,
    audio=AUDIO,
    poc_id="poc_stt_browser_demo",
    key="capture-key-001",
):
    return runtime.capture(
        poc_id=poc_id,
        capture_id=capture_id,
        audio_bytes=audio,
        byte_length=len(audio),
        duration_ms=1_000,
        media_type=STT_DEMO_MEDIA_TYPE,
        audio_sha256=hashlib.sha256(audio).hexdigest(),
        idempotency_key=key,
    )


def test_disclosure_truthfully_freezes_the_local_demo_boundary():
    runtime, _, _ = _runtime()

    disclosure = runtime.disclosure

    assert disclosure.consent_required_before_microphone is True
    assert disclosure.one_local_operator_only is True
    assert disclosure.duration_source == "BROWSER_MONOTONIC_CLOCK_DECLARED"
    assert disclosure.webm_signature_required is True
    assert disclosure.spoken_words_transcribed is False
    assert disclosure.provider_connected is False
    assert disclosure.raw_audio_retained is False
    assert disclosure.raw_transcript_retained is False
    assert disclosure.fixed_output == (
        "P95 time to first token must stay below 500 ms.",
        "Error rate must remain below 1%.",
    )
    with pytest.raises(ValidationError):
        type(disclosure)(
            fixed_output=("Caller selected output.", "Unsafe output."),
        )


def test_consented_capture_reuses_pr95_to_pr97_and_creates_review_only_source():
    runtime, _, intake = _runtime()
    consent = _consent(runtime)

    result = _capture(runtime, consent.capture_id)

    assert result.poc_id == "poc_stt_browser_demo"
    assert result.source_kind == "MEETING"
    assert result.status == "NEEDS_REVIEW"
    assert result.proposal_count == 2
    assert result.idempotent_replay is False
    assert result.duration_source == "BROWSER_MONOTONIC_CLOCK_DECLARED"
    assert result.webm_signature_verified is True
    assert result.spoken_words_transcribed is False
    assert result.provider_connected is False
    assert result.raw_audio_retained is False
    assert result.raw_transcript_retained is False
    assert len(intake.list_receipts("poc_stt_browser_demo")) == 1
    proposals = intake.proposal_inputs("poc_stt_browser_demo")
    assert len(proposals) == 2
    assert all(proposal.state == "NEEDS_REVIEW" for proposal in proposals)

    serialized = json.dumps(result.model_dump(mode="json"))
    assert AUDIO.hex() not in serialized
    for authority_claim in ("approved", "confirmed", "frozen", "verdict"):
        assert authority_claim not in serialized.lower()


def test_spoken_audio_content_cannot_change_the_fixed_synthetic_output():
    first, _, first_intake = _runtime(poc_id="poc_first_audio_demo")
    second, _, second_intake = _runtime(poc_id="poc_second_audio_demo")

    first_consent = _consent(
        first,
        poc_id="poc_first_audio_demo",
        key="consent-first-audio",
    )
    second_consent = _consent(
        second,
        poc_id="poc_second_audio_demo",
        key="consent-second-audio",
    )
    _capture(
        first,
        first_consent.capture_id,
        poc_id="poc_first_audio_demo",
        audio=WEBM_SIGNATURE + b"one spoken phrase",
        key="capture-first-audio",
    )
    _capture(
        second,
        second_consent.capture_id,
        poc_id="poc_second_audio_demo",
        audio=WEBM_SIGNATURE + b"completely different spoken phrase",
        key="capture-second-audio",
    )

    first_claims = tuple(
        proposal.normalized_claim
        for proposal in first_intake.proposal_inputs("poc_first_audio_demo")
    )
    second_claims = tuple(
        proposal.normalized_claim
        for proposal in second_intake.proposal_inputs("poc_second_audio_demo")
    )
    assert first_claims == second_claims


def test_consent_is_idempotent_and_must_precede_audio_capture():
    runtime, _, _ = _runtime()
    first = _consent(runtime)
    replay = _consent(runtime)

    assert replay == first
    with pytest.raises(STTDemoError) as missing:
        _capture(runtime, "sttcap_" + ("f" * 64))
    assert missing.value.failure_code == STTDemoFailureCode.CONSENT_REQUIRED


@pytest.mark.parametrize(
    "update",
    (
        {"recording_notice_acknowledged": False},
        {"all_speakers_consented": False},
        {"synthetic_demo_acknowledged": False},
    ),
)
def test_every_consent_acknowledgement_is_required(update):
    runtime, _, _ = _runtime()
    values = {
        "poc_id": "poc_stt_browser_demo",
        "disclosure_id": STT_DEMO_DISCLOSURE_ID,
        "recording_notice_acknowledged": True,
        "all_speakers_consented": True,
        "synthetic_demo_acknowledged": True,
        "idempotency_key": "consent-required-key",
    }
    values.update(update)

    with pytest.raises(STTDemoError) as caught:
        runtime.record_consent(**values)

    assert caught.value.failure_code == STTDemoFailureCode.CONSENT_REQUIRED


def test_expired_consent_requires_a_new_recording():
    clock = MutableClock(NOW)
    runtime, _, _ = _runtime(clock=clock)
    consent = _consent(runtime)
    clock.value = consent.expires_at

    with pytest.raises(STTDemoError) as caught:
        _capture(runtime, consent.capture_id)

    assert caught.value.failure_code == STTDemoFailureCode.CONSENT_EXPIRED


def test_exact_capture_replay_is_safe_but_changed_audio_conflicts():
    runtime, _, intake = _runtime()
    consent = _consent(runtime)
    first = _capture(runtime, consent.capture_id)
    replay = _capture(runtime, consent.capture_id)

    assert replay == first.model_copy(update={"idempotent_replay": True})
    with pytest.raises(STTDemoError) as changed:
        _capture(
            runtime,
            consent.capture_id,
            audio=WEBM_SIGNATURE + b"changed audio bytes",
        )
    assert changed.value.failure_code == STTDemoFailureCode.CAPTURE_CONFLICT
    assert len(intake.list_receipts("poc_stt_browser_demo")) == 1


def test_completed_capture_replay_returns_only_its_receipt_after_consent_expiry():
    clock = MutableClock(NOW)
    runtime, _, intake = _runtime(clock=clock)
    consent = _consent(runtime)
    first = _capture(runtime, consent.capture_id)
    clock.value = consent.expires_at + timedelta(seconds=1)

    replay = _capture(runtime, consent.capture_id)
    recovered = runtime.capture_receipt(
        poc_id="poc_stt_browser_demo",
        capture_id=consent.capture_id,
    )

    assert replay == first.model_copy(update={"idempotent_replay": True})
    assert recovered == first.model_copy(update={"idempotent_replay": True})
    assert len(intake.list_receipts("poc_stt_browser_demo")) == 1


def test_capture_receipt_never_grants_authority_before_completion():
    runtime, _, _ = _runtime()
    consent = _consent(runtime)

    with pytest.raises(STTDemoError) as caught:
        runtime.capture_receipt(
            poc_id="poc_stt_browser_demo",
            capture_id=consent.capture_id,
        )

    assert caught.value.failure_code == STTDemoFailureCode.CONSENT_REQUIRED


def test_concurrent_exact_capture_never_creates_duplicate_sources():
    runtime, _, intake = _runtime()
    consent = _consent(runtime)

    def run_once():
        try:
            return _capture(runtime, consent.capture_id)
        except STTDemoError as error:
            return error

    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = tuple(pool.map(lambda _: run_once(), range(8)))

    successes = [
        outcome
        for outcome in outcomes
        if not isinstance(outcome, STTDemoError)
    ]
    failures = [
        outcome
        for outcome in outcomes
        if isinstance(outcome, STTDemoError)
    ]
    assert successes
    assert all(
        error.failure_code == STTDemoFailureCode.CAPTURE_IN_PROGRESS
        for error in failures
    )
    assert len(intake.list_receipts("poc_stt_browser_demo")) == 1


@pytest.mark.parametrize(
    ("field", "value", "failure"),
    (
        ("audio_bytes", b"different", STTDemoFailureCode.AUDIO_BINDING_MISMATCH),
        ("byte_length", 99, STTDemoFailureCode.AUDIO_BINDING_MISMATCH),
        ("duration_ms", 249, STTDemoFailureCode.AUDIO_TOO_LONG),
        ("duration_ms", 8_001, STTDemoFailureCode.AUDIO_TOO_LONG),
        ("media_type", "audio/mp4", STTDemoFailureCode.UNSUPPORTED_MEDIA),
        ("audio_sha256", "f" * 64, STTDemoFailureCode.AUDIO_BINDING_MISMATCH),
    ),
)
def test_audio_metadata_is_fail_closed(field, value, failure):
    runtime, _, _ = _runtime()
    consent = _consent(runtime)
    values = {
        "poc_id": "poc_stt_browser_demo",
        "capture_id": consent.capture_id,
        "audio_bytes": AUDIO,
        "byte_length": len(AUDIO),
        "duration_ms": 1_000,
        "media_type": STT_DEMO_MEDIA_TYPE,
        "audio_sha256": hashlib.sha256(AUDIO).hexdigest(),
        "idempotency_key": "capture-metadata-key",
    }
    values[field] = value

    with pytest.raises(STTDemoError) as caught:
        runtime.capture(**values)

    assert caught.value.failure_code == failure
    assert AUDIO.hex() not in str(caught.value)


def test_declared_webm_without_the_ebml_signature_is_rejected():
    runtime, _, _ = _runtime()
    consent = _consent(runtime)
    audio = b"not a webm container"

    with pytest.raises(STTDemoError) as caught:
        _capture(runtime, consent.capture_id, audio=audio)

    assert caught.value.failure_code == STTDemoFailureCode.UNSUPPORTED_MEDIA


def test_archived_or_unknown_draft_never_issues_microphone_authority():
    runtime, drafts, _ = _runtime()
    drafts.archive("poc_stt_browser_demo")

    with pytest.raises(STTDemoError) as archived:
        _consent(runtime)
    with pytest.raises(STTDemoError) as missing:
        _consent(runtime, poc_id="poc_missing_browser_demo")

    assert archived.value.failure_code == STTDemoFailureCode.DRAFT_UNAVAILABLE
    assert missing.value.failure_code == STTDemoFailureCode.DRAFT_UNAVAILABLE
