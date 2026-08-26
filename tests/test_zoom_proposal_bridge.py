from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json

import pytest

from exitspec.poc_creation import FirstSourceChoice, ProcessLocalDraftPOCService
from exitspec.poc_proposal_review import ProposalReviewState
from exitspec.poc_source_intake import ProcessLocalPOCSourceIntake
from exitspec.poc_sources import SourceKind
from exitspec.zoom_proposal_bridge import (
    ZOOM_PROPOSAL_ADAPTER_VERSION,
    ZoomProposalBridge,
    ZoomProposalBridgeError,
    ZoomProposalBridgeFailureCode,
    ZoomProposalBridgeRequest,
)
from exitspec.zoom_rtms_decoder import (
    ZOOM_RTMS_PACKET_SCHEMA_VERSION,
    ZOOM_RTMS_TRANSCRIPT_MEDIA_TYPE,
    ZOOM_RTMS_TRANSCRIPT_MESSAGE_TYPE,
    ZoomDecoderProvenance,
    decode_zoom_rtms_transcript_packet,
)
from exitspec.zoom_session_runtime import (
    ZoomSessionState,
    ZoomSessionStateMachine,
    new_zoom_session_id,
)


NOW = datetime(2026, 8, 25, 19, 0, tzinfo=timezone.utc)
TIMESTAMP = 1_787_684_400_000


def _packet(*, timestamp: int, text: str) -> bytes:
    return json.dumps(
        {
            "schema_version": ZOOM_RTMS_PACKET_SCHEMA_VERSION,
            "media_type": ZOOM_RTMS_TRANSCRIPT_MEDIA_TYPE,
            "message_type": ZOOM_RTMS_TRANSCRIPT_MESSAGE_TYPE,
            "user_id": "provider-user-1",
            "start_time": "2026-08-25T19:00:00.000Z",
            "end_time": "2026-08-25T19:00:01.000Z",
            "timestamp": timestamp,
            "language": "en-US",
            "data": text,
        },
        separators=(",", ":"),
    ).encode("utf-8")


def _segment(
    *,
    timestamp: int,
    text: str,
    fixture_sha256: str = "1" * 64,
    arrival_index: int = 1,
):
    packet = _packet(timestamp=timestamp, text=text)
    provenance = ZoomDecoderProvenance(
        source_classification="SYNTHETIC_REVIEWED_FIXTURE",
        fixture_sha256=fixture_sha256,
        capture_plan_sha256="2" * 64,
        setup_attestation_sha256="3" * 64,
        runtime_plan_sha256="4" * 64,
        packet_sha256=hashlib.sha256(packet).hexdigest(),
    )
    return decode_zoom_rtms_transcript_packet(
        packet,
        speaker_pseudonyms={"provider-user-1": "SPEAKER_1"},
        provenance=provenance,
        arrival_index=arrival_index,
    )


def _request(session_id: str, **updates: object) -> ZoomProposalBridgeRequest:
    payload: dict[str, object] = {
        "session_id": session_id,
        "display_name": "Synthetic Zoom POC",
        "customer_label": "Synthetic customer",
        "use_case": "Review measurable requirements from one meeting.",
        "owner": "field_engineer",
    }
    payload.update(updates)
    return ZoomProposalBridgeRequest(**payload)


def _services():
    drafts = ProcessLocalDraftPOCService(clock=lambda: NOW)
    intake = ProcessLocalPOCSourceIntake(
        draft_lookup=drafts.get,
        clock=lambda: NOW,
    )
    return drafts, intake, ZoomProposalBridge(drafts=drafts, source_intake=intake)


def _session(*, mixed_provenance: bool = False):
    session = ZoomSessionStateMachine(
        session_id=new_zoom_session_id("zoom-proposal-bridge"),
        clock=lambda: NOW,
    )
    session.mark_listening(idempotency_key="bridge-start")
    session.append_transcript(
        _segment(
            timestamp=TIMESTAMP,
            text=(
                "Criterion: exact tool selection must be at least 95 percent "
                "over 200 attempts."
            ),
        ),
        idempotency_key="bridge-first-transcript",
    )
    session.append_transcript(
        _segment(
            timestamp=TIMESTAMP + 1_000,
            text="We need quality.",
            fixture_sha256="5" * 64 if mixed_provenance else "1" * 64,
            arrival_index=2,
        ),
        idempotency_key="bridge-second-transcript",
    )
    session.stop(idempotency_key="bridge-stop")
    return session


def test_bridge_creates_one_zoom_sourced_review_poc_through_existing_intake():
    drafts, intake, bridge = _services()
    session = _session()
    result = bridge.bridge(
        session=session,
        request=_request(session.snapshot().session_id),
    )

    assert result.source_provider == "ZOOM_RTMS"
    assert result.source_kind is SourceKind.MEETING
    assert result.source_provenance.segment_count == 2
    assert result.source_provenance.decoder_version == "exitspec.zoom-rtms-decoder/1.0"
    assert result.source_provenance.packet_schema_version == (
        "exitspec.zoom-rtms-transcript-packet.v1"
    )
    assert result.proposal_count == 2
    assert len(result.assessments) == 2
    assert result.assessments[0].catalog_metric.value == "exact_tool_selection_rate"
    assert result.assessments[1].catalog_metric is None
    assert all(
        proposal.state is ProposalReviewState.NEEDS_REVIEW
        for proposal in result.proposals
    )
    assert all(item.evaluation_state == "NOT_RUN" for item in result.assessments)
    assert result.review_state == "NEEDS_REVIEW"
    assert session.snapshot().state is ZoomSessionState.DRAFT_READY
    assert drafts.ids() == (result.poc_id,)
    assert drafts.get(result.poc_id).first_source_choice is FirstSourceChoice.MEETING
    receipts = intake.list_receipts(result.poc_id)
    assert len(receipts) == 1
    assert receipts[0].source_kind is SourceKind.MEETING
    assert receipts[0].source_receipt_id == result.source_receipt_id
    assert result.may_confirm_contract is False
    assert result.may_freeze_contract is False
    assert result.may_start_measurement is False
    assert result.may_assign_verdict is False
    serialized = json.dumps(result.model_dump(mode="json"))
    assert "provider-user-1" not in serialized
    assert ZOOM_PROPOSAL_ADAPTER_VERSION in intake._source_service.snapshots(result.poc_id)[0].adapter_version


def test_bridge_replay_creates_no_second_poc_or_source():
    drafts, intake, bridge = _services()
    session = _session()
    request = _request(session.snapshot().session_id)
    first = bridge.bridge(session=session, request=request)
    replay = bridge.bridge(session=session, request=request)

    assert replay.idempotent_replay is True
    assert replay.poc_id == first.poc_id
    assert replay.source_receipt_id == first.source_receipt_id
    assert replay.source_provenance == first.source_provenance
    assert drafts.ids() == (first.poc_id,)
    assert len(intake.list_receipts(first.poc_id)) == 1
    assert session.snapshot().finalization_count == 1


def test_bridge_rejects_not_ready_or_mixed_provenance_sessions():
    _, _, bridge = _services()
    not_ready = ZoomSessionStateMachine(
        session_id=new_zoom_session_id("not-ready"),
        clock=lambda: NOW,
    )
    with pytest.raises(ZoomProposalBridgeError) as not_ready_error:
        bridge.bridge(
            session=not_ready,
            request=_request(not_ready.snapshot().session_id),
        )
    assert not_ready_error.value.failure_code is (
        ZoomProposalBridgeFailureCode.SESSION_NOT_READY
    )

    mixed = _session(mixed_provenance=True)
    with pytest.raises(ZoomProposalBridgeError) as provenance_error:
        bridge.bridge(
            session=mixed,
            request=_request(mixed.snapshot().session_id),
        )
    assert provenance_error.value.failure_code is (
        ZoomProposalBridgeFailureCode.PROVENANCE_MISMATCH
    )


def test_bridge_binds_request_metadata_and_rejects_changed_replay():
    _, _, bridge = _services()
    session = _session()
    request = _request(session.snapshot().session_id)
    bridge.bridge(session=session, request=request)
    with pytest.raises(ZoomProposalBridgeError) as changed:
        bridge.bridge(
            session=session,
            request=_request(
                session.snapshot().session_id,
                owner="different_owner",
            ),
        )
    assert changed.value.failure_code is ZoomProposalBridgeFailureCode.POC_CREATE_FAILED
