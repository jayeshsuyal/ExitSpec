from pathlib import Path


STATIC_ROOT = (
    Path(__file__).resolve().parents[1] / "src" / "exitspec" / "static"
)
HTML_PATH = STATIC_ROOT / "source_intake.html"
CSS_PATH = STATIC_ROOT / "source_intake.css"
JS_PATH = STATIC_ROOT / "source_intake.js"


def _asset(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _function(source: str, name: str, next_name: str) -> str:
    section = source.split(f"function {name}", 1)[1]
    return section.split(f"function {next_name}", 1)[0]


def test_recording_is_one_explicit_submode_not_a_parallel_workflow():
    html = _asset(HTML_PATH)

    assert html.count('class="primary-action"') == 1
    assert 'id="capture-source"' in html
    assert 'id="meeting-mode-paste"' in html
    assert 'id="meeting-mode-record"' in html
    assert 'id="meeting-paste-panel"' in html
    assert 'id="meeting-record-panel"' in html
    assert "Create review proposals" in _asset(JS_PATH)
    assert "Not real STT" in html
    assert "Fixed demo output" in html
    assert "spoken words will not be transcribed" in html
    assert "never persisted" in html
    assert "cleared after the attempt" in html
    assert "No provider, Zoom, or Google Meet connection is implied" in html


def test_every_acknowledgement_is_required_before_microphone_permission():
    javascript = _asset(JS_PATH)
    begin = _function(javascript, "beginRecording", "bytesToBase64")

    consent_at = begin.index("await requestJson(sttConsentsApi")
    consent_validation_at = begin.index("isTrustedSttConsent(consent)")
    microphone_at = begin.index("navigator.mediaDevices.getUserMedia")
    assert consent_at < consent_validation_at < microphone_at
    assert "!allRecordingAcknowledgementsChecked()" in begin
    assert "all_speakers_consented: true" in begin
    assert "recording_notice_acknowledged: true" in begin
    assert "synthetic_demo_acknowledged: true" in begin
    assert "The microphone was not enabled" in begin


def test_disclosure_and_receipts_are_exactly_validated_before_use():
    javascript = _asset(JS_PATH)

    disclosure = _function(
        javascript,
        "isTrustedSttDisclosure",
        "isTrustedSttConsent",
    )
    consent = _function(
        javascript,
        "isTrustedSttConsent",
        "isTrustedSttCaptureResponse",
    )
    capture = _function(
        javascript,
        "isTrustedSttCaptureResponse",
        "requestJson",
    )
    assert "hasExactKeys(payload" in disclosure
    assert 'payload.mode === STT_MODE' in disclosure
    assert "payload.duration_source === STT_DURATION_SOURCE" in disclosure
    assert "payload.webm_signature_required === true" in disclosure
    assert "payload.consent_required_before_microphone === true" in disclosure
    assert "payload.spoken_words_transcribed === false" in disclosure
    assert "payload.provider_connected === false" in disclosure
    assert "hasExactKeys(payload" in consent
    assert "payload.microphone_authority_issued === true" in consent
    assert "payload.audio_egress_authority_issued === false" in consent
    assert "expiresAt > currentTime" in consent
    assert "expiresAt <= currentTime + 120000" in consent
    assert "hasExactKeys(payload" in capture
    assert 'payload.status === "NEEDS_REVIEW"' in capture
    assert "payload.duration_source === STT_DURATION_SOURCE" in capture
    assert "payload.webm_signature_verified === true" in capture
    assert "payload.raw_audio_retained === false" in capture
    assert "payload.raw_transcript_retained === false" in capture


def test_live_fireworks_mode_is_visibly_distinct_and_exactly_validated():
    html = _asset(HTML_PATH)
    javascript = _asset(JS_PATH)
    disclosure = _function(
        javascript,
        "isTrustedSttDisclosure",
        "isTrustedSttConsent",
    )
    consent = _function(
        javascript,
        "isTrustedSttConsent",
        "isTrustedSttCaptureResponse",
    )
    capture = _function(
        javascript,
        "isTrustedSttCaptureResponse",
        "requestJson",
    )
    load = _function(javascript, "loadSttDisclosure", "finishRecording")

    for element_id in (
        "meeting-record-mode-label",
        "meeting-source-option-copy",
        "record-adapter-label",
        "stt-fixed-output-panel",
        "stt-mode-badge",
        "processing-scope-ack-copy",
        "stt-proof-copy",
    ):
        assert f'id="{element_id}"' in html
    assert "payload.mode === STT_LIVE_MODE" in disclosure
    assert 'payload.provider === "fireworks"' in disclosure
    assert 'payload.provider_model === "whisper-v3"' in disclosure
    assert 'payload.provider_region === "us-virginia-1"' in disclosure
    assert 'payload.provider_retention_mode === "ZERO_RETENTION"' in disclosure
    assert "payload.provider_processing_acknowledged === true" in consent
    assert "payload.spoken_words_transcribed === true" in capture
    assert "payload.provider_transport_configured === true" in disclosure
    assert "payload.provider_connected === true" in capture
    assert "Fireworks STT · experimental" in load
    assert "Record with Fireworks STT" in load
    assert "Paste or transcribe one short synthetic clip" in load
    assert "sent once to Fireworks" in load
    assert "NEEDS_REVIEW" in load


def test_provider_failures_use_allowlisted_recovery_copy_not_raw_error_text():
    javascript = _asset(JS_PATH)
    trusted_failure = _function(
        javascript,
        "trustedSttProviderFailure",
        "isTrustedDraft",
    )
    request = _function(javascript, "requestJson", "newIdempotencyKey")
    submit = _function(
        javascript,
        "submitRecordedDemo",
        "safeSttFailureCopy",
    )
    safe_copy = _function(
        javascript,
        "safeSttFailureCopy",
        "safeFailureCopy",
    )

    for failure_code in (
        "STT_PROVIDER_CONFIGURATION",
        "STT_PROVIDER_AUTHENTICATION",
        "STT_PROVIDER_ACCOUNT_UNAVAILABLE",
        "STT_PROVIDER_RATE_LIMITED",
        "STT_PROVIDER_TIMEOUT",
        "STT_PROVIDER_SERVICE_UNAVAILABLE",
        "STT_PROVIDER_TRANSPORT",
        "STT_PROVIDER_INVALID_RESPONSE",
    ):
        assert failure_code in javascript
    assert 'hasExactKeys(payload, ["code", "error", "next_action"])' in trusted_failure
    assert "payload.error" not in trusted_failure
    assert "payload.next_action" not in trusted_failure
    assert "payload.code" in trusted_failure
    assert "trustedSttProviderFailure(failurePayload)" in request
    assert "safeSttFailureCopy(error)" in submit
    assert "STT_PROVIDER_FAILURES[error.failureCode].userMessage" in safe_copy
    assert "restore account balance" in javascript
    assert "errorPanel.textContent = failurePayload" not in javascript


def test_audio_is_memory_only_and_recovery_never_resends_audio():
    javascript = _asset(JS_PATH)
    submit = _function(javascript, "submitRecordedDemo", "safeFailureCopy")
    recovery = _function(
        javascript,
        "recoverSttCapture",
        "submitRecordedDemo",
    )
    cleanup = _function(javascript, "discardRecordedAudio", "recordingElapsedMs")

    for forbidden in (
        "localStorage",
        "sessionStorage",
        "indexedDB",
        "document.cookie",
        "console.",
    ):
        assert forbidden not in javascript
    assert "recordedAudio = null" in cleanup
    assert "captureConsent = null" in cleanup
    assert "audioChunks = []" in cleanup
    assert "discardRecordedAudio();" in submit
    assert "recoverSttCapture(endpoint, captureId)" in submit
    assert "requestJson(endpoint)" in recovery
    assert "audio_base64" not in recovery
    assert "requestBody = null" in submit
    assert "Audio was cleared; record a new clip" in submit
    assert "pendingAttempt" not in submit
    assert 'window.addEventListener("pagehide"' in javascript


def test_recording_endpoint_is_capture_id_bound_and_same_origin_only():
    javascript = _asset(JS_PATH)
    endpoint = _function(javascript, "sttCaptureEndpoint", "isTrustedApiPath")
    trusted = _function(javascript, "isTrustedApiPath", "hasExactKeys")

    assert "STT_CAPTURE_ID_PATTERN.test(captureId)" in endpoint
    assert "parsed.origin === window.location.origin" in trusted
    assert "parsed.pathname === value" in trusted
    assert "parsed.search === \"\"" in trusted
    assert "parsed.hash === \"\"" in trusted
    assert "value === sttDisclosureApi" in trusted
    assert "value === sttConsentsApi" in trusted
    assert "value === sttCaptureEndpoint" in trusted


def test_recording_has_hard_duration_and_size_bounds_with_no_auto_audio_retry():
    javascript = _asset(JS_PATH)
    disclosure = _function(
        javascript,
        "isTrustedSttDisclosure",
        "isTrustedSttConsent",
    )
    finish = _function(javascript, "finishRecording", "stopRecording")
    stop = _function(javascript, "stopRecording", "beginRecording")
    begin = _function(javascript, "beginRecording", "bytesToBase64")

    assert "payload.max_duration_ms <= 8000" in disclosure
    assert "payload.max_audio_bytes <= 65536" in disclosure
    assert "clip.size <= sttDisclosure.max_audio_bytes" in finish
    assert "durationMs >= sttDisclosure.min_duration_ms" in finish
    assert "recordedByteCount + event.data.size" in begin
    assert "recorder.onerror" in begin
    assert "window.setTimeout" in begin
    assert "sttDisclosure.max_duration_ms" in begin
    assert "Retry capture" not in begin
    assert "stopMediaStream();" in stop
    assert "recordingWatchdog = window.setTimeout" in stop
    assert "Browser recording did not finish safely" in stop
    assert "}, 750);" in stop


def test_recording_shows_only_the_action_available_at_each_stage():
    javascript = _asset(JS_PATH)
    selected = _function(
        javascript,
        "renderSelectedSource",
        "clearSensitiveInputs",
    )

    assert "captureButton.hidden = !recordedAudio" in selected
    assert "captureButton.hidden = false" in selected
    assert 'recordedAudio\n          ? "Create review proposals"' in selected


def test_consent_rows_have_accessible_click_targets():
    css = _asset(CSS_PATH)
    consent_label = css.split(".record-consent label", 1)[1].split(
        ".record-consent input",
        1,
    )[0]

    assert "min-height: 24px" in consent_label
