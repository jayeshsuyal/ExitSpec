from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime, timezone

import pytest

from exitspec.poc_creation import (
    DraftPOCCreateRequest,
    FirstSourceChoice,
    ProcessLocalDraftPOCService,
)
from exitspec.poc_source_intake import ProcessLocalPOCSourceIntake
from exitspec.stt_demo_runtime import (
    ProcessLocalSTTDemoRuntime,
    STT_DEMO_DISCLOSURE_ID,
    STT_LIVE_DISCLOSURE_ID,
    STT_LIVE_MODE,
)
from exitspec.stt_boundary import STTSpeakerMappingState
from exitspec.stt_operation import (
    STTOperationFailureCode,
    STTTransportError,
    STTTransportResponse,
    STTTransportSegment,
)
from exitspec.stt_demo_web_api import (
    handle_stt_demo_web_api_request,
    is_stt_demo_web_api_target,
    stt_demo_web_api_poc_id,
)


NOW = datetime(2026, 8, 3, 21, 0, tzinfo=timezone.utc)
POC_ID = "poc_stt_web_api"
WEBM_SIGNATURE = b"\x1a\x45\xdf\xa3"
AUDIO = WEBM_SIGNATURE + b"safe browser audio"


class LiveTransport:
    def __init__(self, failure=None):
        self.calls = 0
        self.failure = failure

    def transcribe(self, request):
        request.read_audio_bytes()
        self.calls += 1
        if self.failure is not None:
            raise self.failure
        return STTTransportResponse(
            provider_request_id="fireworks-web-request-001",
            language="en",
            speaker_mapping=STTSpeakerMappingState.NOT_PROVIDED,
            segments=(
                STTTransportSegment(
                    start_ms=0,
                    end_ms=900,
                    text="P95 latency must stay below 650 ms.",
                ),
            ),
        )


def _runtime(fireworks_transport=None) -> ProcessLocalSTTDemoRuntime:
    drafts = ProcessLocalDraftPOCService(clock=lambda: NOW)
    drafts.create(
        DraftPOCCreateRequest(
            poc_id=POC_ID,
            display_name="STT API demo",
            customer_label="Northstar",
            use_case="Prove browser STT handoff controls.",
            owner="field_engineer",
            first_source_choice=FirstSourceChoice.MEETING,
        ),
        idempotency_key="create-stt-api-demo",
    )
    intake = ProcessLocalPOCSourceIntake(
        draft_lookup=drafts.get,
        clock=lambda: NOW,
    )
    return ProcessLocalSTTDemoRuntime(
        drafts=drafts,
        source_intake=intake,
        clock=lambda: NOW,
        fireworks_transport=fireworks_transport,
    )


def _request(runtime, method, target, payload=None):
    response = handle_stt_demo_web_api_request(
        method=method,
        target=target,
        payload=payload,
        runtime=runtime,
    )
    assert response is not None
    return int(response.status), response.payload


def _consent(runtime):
    live = runtime.live_provider_enabled
    return _request(
        runtime,
        "POST",
        f"/api/pocs/{POC_ID}/stt/consents",
        {
            "all_speakers_consented": True,
            "disclosure_id": (
                STT_LIVE_DISCLOSURE_ID if live else STT_DEMO_DISCLOSURE_ID
            ),
            "idempotency_key": "web-consent-key",
            "recording_notice_acknowledged": True,
            **(
                {"provider_processing_acknowledged": True}
                if live
                else {"synthetic_demo_acknowledged": True}
            ),
        },
    )


def _capture_payload():
    return {
        "audio_base64": base64.b64encode(AUDIO).decode("ascii"),
        "audio_sha256": hashlib.sha256(AUDIO).hexdigest(),
        "byte_length": len(AUDIO),
        "duration_ms": 1_000,
        "idempotency_key": "web-capture-key",
        "media_type": "audio/webm",
    }


def test_target_detection_and_identity_are_exact():
    exact = f"/api/pocs/{POC_ID}/stt/disclosure"
    assert is_stt_demo_web_api_target(exact) is True
    assert stt_demo_web_api_poc_id(exact) == POC_ID
    assert is_stt_demo_web_api_target("/api/pocs") is False
    assert stt_demo_web_api_poc_id(exact + "?mode=provider") is None
    assert stt_demo_web_api_poc_id(exact + "/") is None
    assert stt_demo_web_api_poc_id(
        f"/api/pocs/{POC_ID}/stt/captures/not-a-capture"
    ) is None


def test_disclosure_is_explicitly_synthetic_and_non_retaining():
    status, payload = _request(
        _runtime(),
        "GET",
        f"/api/pocs/{POC_ID}/stt/disclosure",
    )

    assert status == 200
    assert payload["mode"] == "FIXED_SYNTHETIC_TRANSCRIPT"
    assert payload["consent_required_before_microphone"] is True
    assert payload["one_local_operator_only"] is True
    assert payload["duration_source"] == "BROWSER_MONOTONIC_CLOCK_DECLARED"
    assert payload["webm_signature_required"] is True
    assert payload["spoken_words_transcribed"] is False
    assert payload["provider_connected"] is False
    assert payload["raw_audio_retained"] is False
    assert payload["raw_transcript_retained"] is False
    assert payload["fixed_output"] == [
        "P95 time to first token must stay below 500 ms.",
        "Error rate must remain below 1%.",
    ]


def test_live_disclosure_and_receipts_are_exact_provider_safe_projections():
    transport = LiveTransport()
    runtime = _runtime(transport)

    disclosure_status, disclosure = _request(
        runtime,
        "GET",
        f"/api/pocs/{POC_ID}/stt/disclosure",
    )
    consent_status, consent = _consent(runtime)
    capture_status, captured = _request(
        runtime,
        "POST",
        f"/api/pocs/{POC_ID}/stt/captures/{consent['capture_id']}",
        _capture_payload(),
    )

    assert disclosure_status == 200
    assert disclosure["mode"] == STT_LIVE_MODE
    assert disclosure["provider"] == "fireworks"
    assert disclosure["provider_model"] == "whisper-v3"
    assert disclosure["provider_region"] == "us-virginia-1"
    assert disclosure["spoken_words_transcribed"] is True
    assert disclosure["provider_transport_configured"] is True
    assert disclosure["raw_audio_retained"] is False
    assert "fixed_output" not in disclosure
    assert consent_status == capture_status == 201
    assert consent["provider_processing_acknowledged"] is True
    assert consent["audio_egress_authority_issued"] is False
    assert captured["mode"] == STT_LIVE_MODE
    assert captured["status"] == "NEEDS_REVIEW"
    assert captured["proposal_count"] == 1
    assert captured["provider_connected"] is True
    assert captured["provider_retention_mode"] == "ZERO_RETENTION"
    assert captured["raw_audio_retained"] is False
    assert captured["raw_transcript_retained"] is False
    assert transport.calls == 1
    serialized = json.dumps((disclosure, consent, captured))
    assert AUDIO.hex() not in serialized
    assert "650 ms" not in serialized


def test_live_consent_rejects_the_synthetic_acknowledgement_shape():
    runtime = _runtime(LiveTransport())

    response = _request(
        runtime,
        "POST",
        f"/api/pocs/{POC_ID}/stt/consents",
        {
            "all_speakers_consented": True,
            "disclosure_id": STT_LIVE_DISCLOSURE_ID,
            "idempotency_key": "wrong-live-consent-key",
            "recording_notice_acknowledged": True,
            "synthetic_demo_acknowledged": True,
        },
    )

    assert response == (400, {"error": "Recording request is invalid."})


@pytest.mark.parametrize(
    ("failure", "status", "code"),
    (
        (
            STTOperationFailureCode.AUTHENTICATION,
            502,
            "STT_PROVIDER_AUTHENTICATION",
        ),
        (
            STTOperationFailureCode.ACCOUNT_UNAVAILABLE,
            424,
            "STT_PROVIDER_ACCOUNT_UNAVAILABLE",
        ),
        (
            STTOperationFailureCode.RATE_LIMITED,
            429,
            "STT_PROVIDER_RATE_LIMITED",
        ),
        (
            STTOperationFailureCode.TIMEOUT,
            504,
            "STT_PROVIDER_TIMEOUT",
        ),
        (
            STTOperationFailureCode.SERVICE_UNAVAILABLE,
            503,
            "STT_PROVIDER_SERVICE_UNAVAILABLE",
        ),
        (
            STTOperationFailureCode.INVALID_RESPONSE,
            502,
            "STT_PROVIDER_INVALID_RESPONSE",
        ),
    ),
)
def test_live_provider_failures_are_typed_and_content_free(
    failure,
    status,
    code,
):
    transport = LiveTransport(STTTransportError(failure))
    runtime = _runtime(transport)
    _, consent = _consent(runtime)

    actual_status, payload = _request(
        runtime,
        "POST",
        f"/api/pocs/{POC_ID}/stt/captures/{consent['capture_id']}",
        _capture_payload(),
    )

    assert actual_status == status
    assert payload["code"] == code
    assert set(payload) == {"code", "error", "next_action"}
    assert transport.calls == 1
    serialized = json.dumps(payload)
    assert AUDIO.hex() not in serialized
    assert "650 ms" not in serialized


def test_consent_then_capture_returns_only_safe_review_receipts():
    runtime = _runtime()
    consent_status, consent = _consent(runtime)
    capture_id = consent["capture_id"]
    capture_status, captured = _request(
        runtime,
        "POST",
        f"/api/pocs/{POC_ID}/stt/captures/{capture_id}",
        _capture_payload(),
    )

    assert consent_status == capture_status == 201
    assert consent["microphone_authority_issued"] is True
    assert consent["audio_egress_authority_issued"] is False
    assert captured["source_kind"] == "MEETING"
    assert captured["status"] == "NEEDS_REVIEW"
    assert captured["proposal_count"] == 2
    assert captured["duration_source"] == "BROWSER_MONOTONIC_CLOCK_DECLARED"
    assert captured["webm_signature_verified"] is True
    assert captured["spoken_words_transcribed"] is False
    assert captured["raw_audio_retained"] is False
    serialized = json.dumps((consent, captured))
    assert AUDIO.hex() not in serialized
    assert "P95 time to first token" not in serialized


def test_exact_capture_replay_is_200_and_does_not_add_authority():
    runtime = _runtime()
    _, consent = _consent(runtime)
    target = f"/api/pocs/{POC_ID}/stt/captures/{consent['capture_id']}"
    first = _request(runtime, "POST", target, _capture_payload())
    replay = _request(runtime, "POST", target, _capture_payload())
    recovered = _request(runtime, "GET", target)

    assert first[0] == 201
    assert replay[0] == 200
    assert replay[1] == {**first[1], "idempotent_replay": True}
    assert recovered == replay


@pytest.mark.parametrize(
    ("method", "target", "payload", "expected"),
    (
        (
            "GET",
            f"/api/pocs/{POC_ID}/stt/consents",
            None,
            (404, {"error": "Recording route was not found."}),
        ),
        (
            "DELETE",
            f"/api/pocs/{POC_ID}/stt/disclosure",
            None,
            (405, {"error": "Recording method is not allowed."}),
        ),
        (
            "GET",
            f"/api/pocs/{POC_ID}/stt/disclosure?provider=real",
            None,
            (400, {"error": "Recording request is invalid."}),
        ),
        (
            "GET",
            f"/api/pocs/{POC_ID}/stt/disclosure/",
            None,
            (400, {"error": "Recording request is invalid."}),
        ),
        (
            "GET",
            f"/api/pocs/{POC_ID}/stt//disclosure",
            None,
            (400, {"error": "Recording request is invalid."}),
        ),
        (
            "POST",
            f"/api/pocs/{POC_ID}/stt/consents",
            {
                "all_speakers_consented": True,
                "approve": True,
                "disclosure_id": STT_DEMO_DISCLOSURE_ID,
                "idempotency_key": "authority-field-key",
                "recording_notice_acknowledged": True,
                "synthetic_demo_acknowledged": True,
            },
            (400, {"error": "Recording request is invalid."}),
        ),
    ),
)
def test_routes_methods_parameters_and_authority_fields_fail_closed(
    method,
    target,
    payload,
    expected,
):
    assert _request(_runtime(), method, target, payload) == expected


@pytest.mark.parametrize("audio_base64", ("%%%", "AA=A", ""))
def test_malformed_audio_is_content_free_and_never_processed(audio_base64):
    runtime = _runtime()
    _, consent = _consent(runtime)
    payload = _capture_payload()
    payload["audio_base64"] = audio_base64

    status, response = _request(
        runtime,
        "POST",
        f"/api/pocs/{POC_ID}/stt/captures/{consent['capture_id']}",
        payload,
    )

    assert status == 422
    assert response["code"] == "STT_DEMO_AUDIO_BINDING_MISMATCH"
    if audio_base64:
        assert audio_base64 not in json.dumps(response)


def test_unrelated_routes_fall_through_without_a_response():
    assert handle_stt_demo_web_api_request(
        method="GET",
        target=f"/api/pocs/{POC_ID}/sources",
        payload=None,
        runtime=_runtime(),
    ) is None
