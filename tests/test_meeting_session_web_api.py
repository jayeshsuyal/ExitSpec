from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from exitspec.meeting_event_inbox import SQLiteMeetingEventInbox
from exitspec.meeting_session_runtime import (
    MEETING_SESSION_DISCLOSURE_ID,
    ProcessLocalMeetingSessionRuntime,
)
from exitspec.meeting_session_web_api import (
    handle_meeting_session_web_api_request,
    is_meeting_session_web_api_target,
    meeting_session_web_api_poc_id,
)
from exitspec.poc_creation import (
    DraftPOCCreateRequest,
    FirstSourceChoice,
    ProcessLocalDraftPOCService,
)
from exitspec.poc_source_intake import ProcessLocalPOCSourceIntake


NOW = datetime(2026, 8, 18, 18, 0, tzinfo=timezone.utc)
POC_ID = "poc_meeting_session_web_api"


def _runtime(tmp_path):
    drafts = ProcessLocalDraftPOCService(clock=lambda: NOW)
    drafts.create(
        DraftPOCCreateRequest(
            poc_id=POC_ID,
            display_name="Meeting API POC",
            customer_label="Northstar",
            use_case="Create a reviewable contract draft during a call.",
            owner="field_engineer",
            first_source_choice=FirstSourceChoice.MEETING,
        ),
        idempotency_key="create-meeting-api-poc",
    )
    intake = ProcessLocalPOCSourceIntake(
        draft_lookup=drafts.get,
        clock=lambda: NOW,
    )
    return ProcessLocalMeetingSessionRuntime(
        drafts=drafts,
        source_intake=intake,
        inbox=SQLiteMeetingEventInbox(
            tmp_path / "meeting-api.sqlite3",
            clock=lambda: NOW,
        ),
        clock=lambda: NOW,
    )


def _request(runtime, method, target, payload=None):
    response = handle_meeting_session_web_api_request(
        method=method,
        target=target,
        payload=payload,
        runtime=runtime,
    )
    assert response is not None
    return int(response.status), response.payload


def _create(runtime, key="meeting-web-create"):
    return _request(
        runtime,
        "POST",
        f"/api/pocs/{POC_ID}/meeting-sessions",
        {"idempotency_key": key},
    )


def test_target_detection_and_poc_identity_are_exact():
    exact = f"/api/pocs/{POC_ID}/meeting-sessions/disclosure"

    assert is_meeting_session_web_api_target(exact) is True
    assert meeting_session_web_api_poc_id(exact) == POC_ID
    assert is_meeting_session_web_api_target("/api/pocs") is False
    assert meeting_session_web_api_poc_id(exact + "?provider=zoom") is None
    assert meeting_session_web_api_poc_id(exact + "/") is None
    assert meeting_session_web_api_poc_id(
        f"/api/pocs/{POC_ID}/meeting-sessions/not-a-session"
    ) is None


def test_complete_api_flow_exposes_one_safe_next_action_at_each_step(tmp_path):
    runtime = _runtime(tmp_path)
    disclosure = _request(
        runtime,
        "GET",
        f"/api/pocs/{POC_ID}/meeting-sessions/disclosure",
    )
    created = _create(runtime)
    session_id = created[1]["session"]["session_id"]
    consented = _request(
        runtime,
        "POST",
        f"/api/pocs/{POC_ID}/meeting-sessions/{session_id}/consent",
        {
            "all_participants_consented": True,
            "disclosure_id": MEETING_SESSION_DISCLOSURE_ID,
            "idempotency_key": "meeting-web-consent",
            "recording_notice_acknowledged": True,
            "synthetic_demo_acknowledged": True,
        },
    )
    started = _request(
        runtime,
        "POST",
        f"/api/pocs/{POC_ID}/meeting-sessions/{session_id}/start",
        {"idempotency_key": "meeting-web-start"},
    )
    drafted = _request(
        runtime,
        "POST",
        f"/api/pocs/{POC_ID}/meeting-sessions/{session_id}/draft",
        {"idempotency_key": "meeting-web-draft"},
    )
    current = _request(
        runtime,
        "GET",
        f"/api/pocs/{POC_ID}/meeting-sessions/current",
    )
    recovered = _request(
        runtime,
        "GET",
        f"/api/pocs/{POC_ID}/meeting-sessions/{session_id}",
    )

    assert disclosure[0] == 200
    assert disclosure[1]["adapter"]["provider_connected"] is False
    assert disclosure[1]["synthetic_only"] is True
    assert created[0] == consented[0] == started[0] == drafted[0] == 201
    assert created[1]["session"]["next_action"] == "RECORD_CONSENT"
    assert created[1]["session"]["review_state"] is None
    assert consented[1]["session"]["next_action"] == "START_CAPTURE"
    assert started[1]["session"]["next_action"] == "DRAFT_REQUIREMENTS"
    assert drafted[1]["session"]["next_action"] == "REVIEW_REQUIREMENTS"
    assert drafted[1]["session"]["review_state"] == "NEEDS_REVIEW"
    assert drafted[1]["session"]["proposal_count"] == 2
    assert current == (200, drafted[1]["session"])
    assert recovered == current

    serialized = json.dumps(
        [disclosure[1], created[1], consented[1], started[1], drafted[1]]
    )
    assert "transcript_text" not in serialized
    assert "participant_synthetic_" not in serialized
    assert '"meeting_id"' not in serialized
    assert '"provider_connected": true' not in serialized
    assert '"may_freeze_contract": true' not in serialized


def test_replay_is_200_and_changed_or_cross_action_keys_conflict(tmp_path):
    runtime = _runtime(tmp_path)
    first = _create(runtime)
    replay = _create(runtime)
    session_id = first[1]["session"]["session_id"]
    cross_action = _request(
        runtime,
        "POST",
        f"/api/pocs/{POC_ID}/meeting-sessions/{session_id}/start",
        {"idempotency_key": "meeting-web-create"},
    )

    assert first[0] == 201
    assert replay[0] == 200
    assert replay[1]["idempotent_replay"] is True
    assert cross_action[0] == 409
    assert cross_action[1]["code"] == "MEETING_SESSION_IDEMPOTENCY_CONFLICT"


@pytest.mark.parametrize(
    ("method", "target", "payload", "status"),
    (
        (
            "GET",
            f"/api/pocs/{POC_ID}/meeting-sessions?provider=zoom",
            None,
            400,
        ),
        (
            "POST",
            f"/api/pocs/{POC_ID}/meeting-sessions/",
            {"idempotency_key": "meeting-web-trailing"},
            400,
        ),
        (
            "POST",
            f"/api/pocs/{POC_ID}/meeting-sessions",
            {
                "idempotency_key": "meeting-web-extra",
                "meeting_id": "browser-controlled",
            },
            400,
        ),
        (
            "PATCH",
            f"/api/pocs/{POC_ID}/meeting-sessions/disclosure",
            None,
            405,
        ),
        (
            "GET",
            f"/api/pocs/{POC_ID}/meeting-sessions",
            None,
            405,
        ),
    ),
)
def test_route_and_payload_shapes_fail_closed(
    tmp_path,
    method,
    target,
    payload,
    status,
):
    response = _request(_runtime(tmp_path), method, target, payload)
    assert response[0] == status


def test_no_current_session_and_start_before_consent_are_typed(tmp_path):
    runtime = _runtime(tmp_path)
    missing = _request(
        runtime,
        "GET",
        f"/api/pocs/{POC_ID}/meeting-sessions/current",
    )
    created = _create(runtime)
    session_id = created[1]["session"]["session_id"]
    premature = _request(
        runtime,
        "POST",
        f"/api/pocs/{POC_ID}/meeting-sessions/{session_id}/start",
        {"idempotency_key": "meeting-web-premature"},
    )

    assert missing[0] == 404
    assert missing[1]["code"] == "MEETING_SESSION_NOT_FOUND"
    assert premature[0] == 409
    assert premature[1]["code"] == "MEETING_SESSION_CONSENT_REQUIRED"
