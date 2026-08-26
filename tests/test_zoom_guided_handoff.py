from __future__ import annotations

from datetime import datetime, timezone
import json

import pytest

from exitspec.poc_creation import (
    DraftPOCCreateRequest,
    FirstSourceChoice,
    ProcessLocalDraftPOCService,
)
from exitspec.poc_source_intake import ProcessLocalPOCSourceIntake
from exitspec.zoom_guided_handoff import (
    ZoomGuidedHandoffError,
    ZoomGuidedHandoffService,
)
from exitspec.zoom_guided_handoff_web_api import (
    handle_zoom_guided_handoff_web_api_request,
    is_zoom_guided_handoff_web_api_target,
    zoom_guided_handoff_web_api_poc_id,
)
from exitspec.zoom_proposal_bridge import ZoomProposalBridge
from exitspec.zoom_proposal_bridge import (
    ZoomProposalBridgeError,
    ZoomProposalBridgeFailureCode,
)


NOW = datetime(2026, 8, 25, 19, 0, tzinfo=timezone.utc)
POC_ID = "poc_guided_zoom"
HANDOFF_ROUTE = f"/api/pocs/{POC_ID}/zoom-handoff"


def _services():
    drafts = ProcessLocalDraftPOCService(clock=lambda: NOW)
    drafts.create(
        DraftPOCCreateRequest(
            poc_id=POC_ID,
            display_name="Guided Zoom POC",
            customer_label="Synthetic customer",
            use_case="Review measurable requirements from a local Zoom handoff.",
            owner="field_engineer",
            first_source_choice=FirstSourceChoice.MEETING,
        ),
        idempotency_key="create-guided-zoom",
    )
    intake = ProcessLocalPOCSourceIntake(
        draft_lookup=drafts.get,
        clock=lambda: NOW,
    )
    bridge = ZoomProposalBridge(drafts=drafts, source_intake=intake)
    return drafts, intake, ZoomGuidedHandoffService(
        bridge=bridge,
        drafts=drafts,
        clock=lambda: NOW,
    )


def _post(runtime, action: str, key: str, **extra: object):
    payload = {"action": action, "idempotency_key": key, **extra}
    return handle_zoom_guided_handoff_web_api_request(
        method="POST",
        target=HANDOFF_ROUTE,
        payload=payload,
        runtime=runtime,
    )


def test_route_and_disclosure_are_exact_and_content_free():
    _, _, runtime = _services()
    assert is_zoom_guided_handoff_web_api_target(HANDOFF_ROUTE)
    assert zoom_guided_handoff_web_api_poc_id(HANDOFF_ROUTE) == POC_ID
    disclosure = handle_zoom_guided_handoff_web_api_request(
        method="GET",
        target=f"/api/pocs/{POC_ID}/zoom-handoff-disclosure",
        payload=None,
        runtime=runtime,
    )
    assert disclosure is not None
    assert disclosure.payload["mode"] == "ZOOM_RTMS_LOCAL_SYNTHETIC"
    assert disclosure.payload["provider_connected"] is False
    assert disclosure.payload["live_network"] is False
    assert disclosure.payload["may_freeze_contract"] is False


def test_guided_handoff_has_listening_processing_draft_ready_sequence():
    drafts, intake, runtime = _services()
    current = runtime.current(POC_ID)
    assert current.state == "IDLE"
    assert current.next_action == "AUTHORIZE_AND_LISTEN"

    started = _post(
        runtime,
        "start",
        "guided-start-123",
        consent_acknowledged=True,
    )
    stopped = _post(runtime, "stop", "guided-stop-123")
    processed = _post(runtime, "process", "guided-process-123")

    assert started.status.value == 201
    assert started.payload["handoff"]["state"] == "LISTENING"
    assert stopped.payload["handoff"]["state"] == "PROCESSING"
    assert processed.payload["handoff"]["state"] == "DRAFT_READY"
    assert processed.payload["handoff"]["source_provider"] == "ZOOM_RTMS"
    assert processed.payload["handoff"]["review_state"] == "NEEDS_REVIEW"
    assert processed.payload["handoff"]["proposal_count"] == 2
    assert processed.payload["handoff"]["review_url"] == (
        f"/app/pocs/{POC_ID}/review"
    )
    assert drafts.ids() == (POC_ID,)
    assert len(intake.list_receipts(POC_ID)) == 1
    serialized = json.dumps(
        [started.payload, stopped.payload, processed.payload]
    )
    assert "guided-customer" not in serialized
    assert "Criterion: p95" not in serialized
    assert "Criterion: error rate" not in serialized


def test_repeated_actions_do_not_create_a_second_source():
    drafts, intake, runtime = _services()
    _post(runtime, "start", "guided-replay-start", consent_acknowledged=True)
    _post(runtime, "stop", "guided-replay-stop")
    first = _post(runtime, "process", "guided-replay-process")
    replay = _post(runtime, "process", "guided-replay-process")
    start_replay = _post(
        runtime,
        "start",
        "guided-replay-start",
        consent_acknowledged=True,
    )

    assert first.payload["idempotent_replay"] is False
    assert replay.payload["idempotent_replay"] is True
    assert start_replay.payload["handoff"]["state"] == "DRAFT_READY"
    assert start_replay.payload["idempotent_replay"] is True
    assert drafts.ids() == (POC_ID,)
    assert len(intake.list_receipts(POC_ID)) == 1


def test_consent_is_required_before_listening():
    _, _, runtime = _services()
    response = _post(
        runtime,
        "start",
        "guided-no-consent",
        consent_acknowledged=False,
    )
    assert response.status.value == 409
    assert response.payload["code"] == (
        "ZOOM_GUIDED_HANDOFF_CONSENT_REQUIRED"
    )
    assert runtime.current(POC_ID).state == "IDLE"


def test_wrong_method_and_extra_fields_fail_closed():
    _, _, runtime = _services()
    get_mutation = handle_zoom_guided_handoff_web_api_request(
        method="GET",
        target=HANDOFF_ROUTE,
        payload={"action": "start"},
        runtime=runtime,
    )
    extra = _post(
        runtime,
        "start",
        "guided-extra-field",
        consent_acknowledged=True,
        transcript="must-not-be-accepted",
    )
    assert get_mutation.status.value == 400
    assert extra.status.value == 400
    assert "must-not-be-accepted" not in json.dumps(extra.payload)


def test_processing_failure_is_not_reported_as_a_draft():
    _, _, runtime = _services()
    _post(runtime, "start", "guided-failure-start", consent_acknowledged=True)
    _post(runtime, "stop", "guided-failure-stop")

    class FailingBridge:
        def bridge_into_existing_poc(self, **kwargs):
            del kwargs
            raise ZoomProposalBridgeError(
                ZoomProposalBridgeFailureCode.PROPOSAL_PROJECTION_FAILED
            )

    runtime._bridge = FailingBridge()  # type: ignore[attr-defined]
    response = _post(runtime, "process", "guided-failure-process")
    assert response.status.value == 503
    assert response.payload["code"] == (
        "ZOOM_PROPOSAL_BRIDGE_PROPOSAL_PROJECTION_FAILED"
    )
    with pytest.raises(ZoomGuidedHandoffError):
        runtime.process(poc_id=POC_ID, idempotency_key="guided-failure-retry")
