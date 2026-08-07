import json
from pathlib import Path

from exitspec.meeting_connector import (
    MEETING_CONNECTOR_AUTHORITY,
    MEETING_CONNECTOR_REVIEW_STATE,
    MEETING_CONNECTOR_VERSION,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = (
    PROJECT_ROOT / "examples/meeting/zoom-rtms-capability-spike-v1.json"
)


def _manifest():
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def test_zoom_capability_snapshot_is_official_dated_and_not_a_wire_schema():
    manifest = _manifest()

    assert manifest["status"] == "ARCHITECTURAL_INPUT_FROZEN"
    assert manifest["checked_at"] == "2026-08-06"
    assert manifest["classification"] == "SYNTHETIC_MEETING_DATA_ONLY"
    assert manifest["not_a_wire_schema"] is True
    assert manifest["runtime_network_enabled"] is False
    sources = manifest["provider"]["official_sources"]
    assert len(sources) >= 7
    assert all(
        source["url"].startswith("https://developers.zoom.us/")
        for source in sources
    )


def test_zoom_scope_is_transcript_only_and_capture_is_not_automatic():
    configuration = _manifest()["least_privilege_configuration"]

    assert configuration["required_media_scopes"] == [
        "meeting:read:meeting_transcript"
    ]
    assert configuration["required_rtms_events"] == [
        "meeting.rtms_started",
        "meeting.rtms_stopped",
    ]
    assert configuration["auto_start"] is False
    assert configuration["raw_audio_received_by_exitspec"] is False
    assert set(configuration["explicitly_excluded_media"]) == {
        "audio",
        "video",
        "screen_share",
        "chat",
    }


def test_manifest_matches_the_executable_authority_boundary():
    manifest = _manifest()
    constitution = manifest["exitspec_constitution"]
    contract = manifest["provider_neutral_contract"]

    assert contract["schema_version"] == MEETING_CONNECTOR_VERSION
    assert constitution["transcript_authority"] == MEETING_CONNECTOR_AUTHORITY
    assert constitution["review_state"] == MEETING_CONNECTOR_REVIEW_STATE
    assert constitution["zoom_disclosure_is_not_exitspec_consent"] is True
    assert constitution["participant_set_drift_stops_capture"] is True
    assert constitution["may_confirm_contract"] is False
    assert constitution["may_freeze_contract"] is False
    assert constitution["may_start_measurement"] is False
    assert constitution["may_assign_verdict"] is False


def test_manifest_freezes_every_first_contract_adversarial_case():
    cases = {
        case["case_id"]: case["expected_outcome"]
        for case in _manifest()["frozen_acceptance_cases"]
    }

    assert cases == {
        "authority-attack-in-transcript": "SEALED_REVIEW_ONLY_WINDOW",
        "changed-duplicate-event": "MEETING_EVENT_CONFLICT",
        "incomplete-consent": "MEETING_CONSENT_INCOMPLETE",
        "late-binding": "MEETING_REQUEST_EXPIRED",
        "missing-event-sequence": "MEETING_EVENT_GAP",
        "missing-stop": "MEETING_STREAM_INCOMPLETE",
        "no-consent": "MEETING_CONSENT_REQUIRED",
        "participant-joined-after-consent": "MEETING_PARTICIPANT_SET_CHANGED",
        "reordered-exact-duplicate": "SEALED_REVIEW_ONLY_WINDOW",
        "revoked-consent": "MEETING_CONSENT_REVOKED",
        "wrong-meeting-binding": "MEETING_BINDING_MISMATCH",
    }


def test_first_golden_fixture_blocks_runtime_and_real_customer_claims():
    manifest = _manifest()
    deferred = "\n".join(manifest["deferred_until_first_golden_zoom_fixture"])
    requirements = set(manifest["golden_fixture_requirements"])

    assert "implement OAuth, webhook, REST, or WebSocket runtime code" in deferred
    assert "process real customer meetings or real customer data" in deferred
    assert "sanitized untouched transcript packets from two consenting synthetic participants" in requirements
    assert "one disconnect and reconnect trace" in requirements
    assert "one exact duplicate-delivery trace" in requirements
