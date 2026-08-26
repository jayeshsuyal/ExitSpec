from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json

import pytest

from exitspec.zoom_rtms_decoder import (
    ZOOM_RTMS_PACKET_SCHEMA_VERSION,
    ZOOM_RTMS_TRANSCRIPT_MEDIA_TYPE,
    ZOOM_RTMS_TRANSCRIPT_MESSAGE_TYPE,
    ZoomDecoderProvenance,
    decode_zoom_rtms_transcript_packet,
)
from exitspec.zoom_session_runtime import (
    ZoomSessionError,
    ZoomSessionFailureCode,
    ZoomSessionState,
    ZoomSessionStateMachine,
    new_zoom_session_id,
)


NOW = datetime(2026, 8, 25, 19, 0, tzinfo=timezone.utc)
TIMESTAMP = 1_787_684_400_000


class MutableClock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now


def _packet(*, timestamp: int, text: str = "synthetic transcript") -> bytes:
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


def _segment(*, timestamp: int, arrival_index: int = 1, text: str = "synthetic transcript"):
    packet = _packet(timestamp=timestamp, text=text)
    provenance = ZoomDecoderProvenance(
        source_classification="SYNTHETIC_REVIEWED_FIXTURE",
        fixture_sha256="1" * 64,
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


def _runtime(clock: MutableClock | None = None) -> ZoomSessionStateMachine:
    return ZoomSessionStateMachine(
        session_id=new_zoom_session_id("synthetic-zoom-session"),
        clock=clock or (lambda: NOW),
    )


def test_lifecycle_orders_out_of_order_packets_and_finalizes_once():
    clock = MutableClock()
    runtime = _runtime(clock)
    assert runtime.snapshot().state is ZoomSessionState.STARTING

    started = runtime.mark_listening(idempotency_key="started-one")
    assert started.session.state is ZoomSessionState.LISTENING
    duplicate_start = runtime.mark_listening(idempotency_key="started-two")
    assert duplicate_start.duplicate_suppressed is True

    second = _segment(timestamp=TIMESTAMP + 2_000, arrival_index=2, text="second")
    first = _segment(timestamp=TIMESTAMP + 1_000, arrival_index=1, text="first")
    runtime.append_transcript(first, idempotency_key="transcript-first")
    runtime.append_transcript(second, idempotency_key="transcript-second")
    same_packet_late = _segment(
        timestamp=TIMESTAMP + 1_000,
        arrival_index=99,
        text="first",
    )
    duplicate = runtime.append_transcript(
        same_packet_late,
        idempotency_key="transcript-first-retry",
    )
    assert duplicate.duplicate_suppressed is True
    assert duplicate.session.segment_count == 2
    assert duplicate.session.duplicate_delivery_count == 1

    runtime.interrupt(idempotency_key="interrupt-one")
    runtime.begin_reconnect(idempotency_key="reconnect-one")
    reconnected = runtime.mark_reconnected(idempotency_key="reconnected-one")
    assert reconnected.session.state is ZoomSessionState.LISTENING
    assert reconnected.session.reconnect_count == 1

    processing = runtime.stop(idempotency_key="stopped-one")
    assert processing.session.state is ZoomSessionState.PROCESSING
    assert processing.session.finalization_count == 1
    processing_input = runtime.processing_input()
    assert [segment.text for segment in processing_input.segments_for_bridge()] == [
        "first",
        "second",
    ]
    assert processing_input.transcript_sha256 == processing.session.processing_input_sha256

    repeated_stop = runtime.stop(idempotency_key="stopped-replayed")
    assert repeated_stop.duplicate_suppressed is True
    assert repeated_stop.session.finalization_count == 1
    late_duplicate = runtime.append_transcript(
        first,
        idempotency_key="transcript-after-stop",
    )
    assert late_duplicate.duplicate_suppressed is True
    assert late_duplicate.session.segment_count == 2

    ready = runtime.processing_succeeded(
        result_sha256="a" * 64,
        idempotency_key="processing-complete",
    )
    assert ready.session.state is ZoomSessionState.DRAFT_READY
    assert ready.session.downstream_result_sha256 == "a" * 64
    ready_replay = runtime.processing_succeeded(
        result_sha256="a" * 64,
        idempotency_key="processing-complete-replay",
    )
    assert ready_replay.duplicate_suppressed is True
    assert ready_replay.session.finalization_count == 1


def test_idempotency_keys_bind_action_and_segment_input():
    runtime = _runtime()
    runtime.mark_listening(idempotency_key="shared-operation")
    with pytest.raises(ZoomSessionError) as action_conflict:
        runtime.stop(idempotency_key="shared-operation")
    assert action_conflict.value.failure_code is (
        ZoomSessionFailureCode.IDEMPOTENCY_CONFLICT
    )

    segment = _segment(timestamp=TIMESTAMP)
    runtime.append_transcript(segment, idempotency_key="transcript-shared")
    changed_segment = _segment(timestamp=TIMESTAMP + 1_000, text="changed")
    with pytest.raises(ZoomSessionError) as segment_conflict:
        runtime.append_transcript(
            changed_segment,
            idempotency_key="transcript-shared",
        )
    assert segment_conflict.value.failure_code is (
        ZoomSessionFailureCode.IDEMPOTENCY_CONFLICT
    )


def test_no_transcript_stop_is_failed_and_cannot_create_processing_input():
    runtime = _runtime()
    runtime.mark_listening(idempotency_key="start-no-transcript")
    failed = runtime.stop(idempotency_key="stop-no-transcript")
    assert failed.session.state is ZoomSessionState.FAILED
    assert failed.session.failure_code is ZoomSessionFailureCode.NO_TRANSCRIPT
    assert failed.session.finalization_count == 1
    repeated = runtime.stop(idempotency_key="stop-no-transcript-replay")
    assert repeated.duplicate_suppressed is True
    with pytest.raises(ZoomSessionError) as processing:
        runtime.processing_input()
    assert processing.value.failure_code is ZoomSessionFailureCode.INVALID_TRANSITION


def test_reconnect_failure_timeout_and_processing_failure_are_terminal():
    reconnecting = _runtime()
    reconnecting.mark_listening(idempotency_key="reconnect-start")
    reconnecting.interrupt(idempotency_key="reconnect-interrupt")
    reconnecting.begin_reconnect(idempotency_key="reconnect-begin")
    failed_reconnect = reconnecting.reconnect_failed(
        idempotency_key="reconnect-failed"
    )
    assert failed_reconnect.session.state is ZoomSessionState.FAILED
    assert failed_reconnect.session.failure_code is ZoomSessionFailureCode.RECONNECT_FAILED

    timed_out = _runtime()
    timeout = timed_out.timeout(idempotency_key="starting-timeout")
    assert timeout.session.state is ZoomSessionState.FAILED
    assert timeout.session.failure_code is ZoomSessionFailureCode.TIMEOUT
    with pytest.raises(ZoomSessionError) as stale_start:
        timed_out.mark_listening(idempotency_key="stale-start")
    assert stale_start.value.failure_code is ZoomSessionFailureCode.INVALID_TRANSITION

    processing = _runtime()
    processing.mark_listening(idempotency_key="processing-start")
    processing.append_transcript(
        _segment(timestamp=TIMESTAMP),
        idempotency_key="processing-transcript",
    )
    processing.stop(idempotency_key="processing-stop")
    failed_processing = processing.processing_failed(
        idempotency_key="processing-failed"
    )
    assert failed_processing.session.state is ZoomSessionState.FAILED
    assert failed_processing.session.failure_code is ZoomSessionFailureCode.PROCESSING_FAILED
    with pytest.raises(ZoomSessionError) as after_failure:
        processing.processing_succeeded(
            result_sha256="b" * 64,
            idempotency_key="late-success",
        )
    assert after_failure.value.failure_code is ZoomSessionFailureCode.INVALID_TRANSITION


def test_checkpoint_recovers_processing_session_without_duplicate_finalization():
    clock = MutableClock()
    runtime = _runtime(clock)
    runtime.mark_listening(idempotency_key="checkpoint-start")
    runtime.append_transcript(
        _segment(timestamp=TIMESTAMP),
        idempotency_key="checkpoint-transcript",
    )
    runtime.stop(idempotency_key="checkpoint-stop")
    checkpoint = runtime.checkpoint()
    assert repr(checkpoint) == "ZoomSessionCheckpoint(<private>)"
    assert "synthetic transcript" not in repr(checkpoint)

    clock.now += timedelta(seconds=2)
    recovered = ZoomSessionStateMachine.recover(checkpoint, clock=clock)
    repeated_stop = recovered.stop(idempotency_key="checkpoint-stop-replay")
    assert repeated_stop.duplicate_suppressed is True
    assert repeated_stop.session.state is ZoomSessionState.PROCESSING
    assert recovered.processing_input().transcript_sha256 == checkpoint.processing_input_sha256

    ready = recovered.processing_succeeded(
        result_sha256="c" * 64,
        idempotency_key="checkpoint-complete",
    )
    assert ready.session.state is ZoomSessionState.DRAFT_READY
    assert ready.session.may_confirm_contract is False
    assert ready.session.may_freeze_contract is False


def test_public_projection_contains_no_transcript_content_or_provider_identity():
    runtime = _runtime()
    runtime.mark_listening(idempotency_key="projection-start")
    runtime.append_transcript(
        _segment(timestamp=TIMESTAMP, text="provider-user-1 should stay private"),
        idempotency_key="projection-transcript",
    )
    serialized = json.dumps(runtime.snapshot().model_dump(mode="json"))
    assert "provider-user-1" not in serialized
    assert "should stay private" not in serialized
    assert "provider-user-1 should stay private" not in serialized


def test_session_id_is_stable_but_does_not_expose_seed():
    first = new_zoom_session_id("private-seed")
    second = new_zoom_session_id("private-seed")
    assert first == second
    assert "private-seed" not in first
