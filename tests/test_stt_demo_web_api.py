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


def _runtime() -> ProcessLocalSTTDemoRuntime:
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
    return _request(
        runtime,
        "POST",
        f"/api/pocs/{POC_ID}/stt/consents",
        {
            "all_speakers_consented": True,
            "disclosure_id": STT_DEMO_DISCLOSURE_ID,
            "idempotency_key": "web-consent-key",
            "recording_notice_acknowledged": True,
            "synthetic_demo_acknowledged": True,
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
            (404, {"error": "Synthetic recording route was not found."}),
        ),
        (
            "DELETE",
            f"/api/pocs/{POC_ID}/stt/disclosure",
            None,
            (405, {"error": "Synthetic recording method is not allowed."}),
        ),
        (
            "GET",
            f"/api/pocs/{POC_ID}/stt/disclosure?provider=real",
            None,
            (400, {"error": "Synthetic recording request is invalid."}),
        ),
        (
            "GET",
            f"/api/pocs/{POC_ID}/stt/disclosure/",
            None,
            (400, {"error": "Synthetic recording request is invalid."}),
        ),
        (
            "GET",
            f"/api/pocs/{POC_ID}/stt//disclosure",
            None,
            (400, {"error": "Synthetic recording request is invalid."}),
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
            (400, {"error": "Synthetic recording request is invalid."}),
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
