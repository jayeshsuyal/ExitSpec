"""Deterministic private-child simulation; no provider traffic or live proof."""

from __future__ import annotations

import base64
import io
import json
import re
import struct
import threading
from contextlib import contextmanager

import pytest

from exitspec.zoom_live_ipc import MAX_IPC_FRAME, encode_frame, read_frame
from exitspec.zoom_live_runtime import ZoomLiveError, ZoomLiveRuntime, ZoomLiveSettings
from tests.test_zoom_guided_handoff import POC_ID, _services
from tests.test_zoom_native_transcript import payload as native_payload


def settings(**updates):
    values = {
        "client_id": "synthetic-client-id",
        "client_secret": "synthetic-client-secret",
        "webhook_secret": "synthetic-webhook-secret",
        "meeting_uuid": "synthetic-meeting",
        "participant_ids": (42, 43),
        "callback_port": 3456,
        "callback_host": "localhost:3456",
        "callback_path": "/zoom-webhook/" + "x" * 24,
        "owner_receipt_id": "owner-receipt-test",
        "code_revision": "a" * 40,
        "budget_ceiling_usd": "1.00",
        "maximum_capture_seconds": 900,
        "live_network_authorized": True,
        "credential_rotation_confirmed": True,
        "synthetic_requirements_consent": True,
        "credits_and_budget_confirmed": True,
    }
    return ZoomLiveSettings(**(values | updates))


class FakeChild:
    def __init__(self, init, receive, failed):
        self.init, self.receive, self.failed = init, receive, failed
        self.sent, self.closed, self.seq = [], False, 0

    def send(self, value):
        self.sent.append(value)

    def close(self):
        self.closed = True

    def emit(self, kind, **extra):
        self.seq += 1
        self.receive(
            {
                "event": kind,
                "seq": self.seq,
                "generation": self.init["generation"],
                "stream_id": "stream-one",
            }
            | extra
        )


@pytest.fixture
def rig():
    drafts, intake, _ = _services()
    children = []
    launched = threading.Event()
    closed_pocs = set()
    now = [0.0]

    def guard(poc, fn):
        if poc in closed_pocs:
            raise ValueError("closed")
        return fn()

    def factory(*args):
        child = FakeChild(*args)
        children.append(child)
        launched.set()
        return child

    runtime = ZoomLiveRuntime(
        drafts=drafts,
        intake=intake,
        run_if_open=guard,
        child_factory=factory,
        clock=lambda: now[0],
        fake_network=True,
    )
    runtime.pair(POC_ID, settings())
    yield runtime, intake, children, launched, closed_pocs, now, drafts
    runtime.close()


def action(runtime, kind, **updates):
    value = {
        "action": kind,
        "session_id": runtime.current(POC_ID)["session_id"],
        "idempotency_key": "request_" + kind,
    }
    if kind == "start":
        value["consent_acknowledged"] = True
    return runtime.action(POC_ID, value | updates)


def start(rig):
    runtime, _, children, launched, *_ = rig
    action(runtime, "start")
    assert launched.wait(2)
    # Serialize on the parent lock until _launch publishes the child handle.
    for _ in range(100):
        with runtime._lock:
            if runtime._record.child is not None:
                break
        threading.Event().wait(0.001)
    child = children[-1]
    child.emit("offer")
    child.emit("listening")
    assert runtime.current(POC_ID)["state"] == "LISTENING"
    return child


def packet(child, **updates):
    raw = json.dumps(native_payload(**updates)).encode()
    child.emit("transcript", packet_base64=base64.b64encode(raw).decode())


def finish(rig):
    runtime = rig[0]
    action(runtime, "stop")
    child = rig[2][-1]
    child.emit("stop_ack")
    child.emit("drained")
    assert runtime.current(POC_ID)["state"] == "CAPTURE_READY"


@pytest.mark.parametrize("id_contains_metric", [False, True])
def test_actual_native_text_redacts_and_attaches_once_with_lineage(rig, id_contains_metric):
    runtime, intake, *_ = rig
    if id_contains_metric:
        runtime._record.session_id = "zoomsess_" + "a" * 30 + "730" + "b" * 31
    session_id = runtime.current(POC_ID)["session_id"]
    child = start(rig)
    packet(
        child,
        data="Criterion: p95 latency must be below 730 milliseconds. Contact alice@example.com.",
    )
    packet(
        child,
        user_id=43,
        data="Criterion: error rate must stay below 0.7 percent at concurrency 7.",
    )
    finish(rig)
    snapshot = action(runtime, "process")
    assert snapshot["state"] == "DRAFT_READY"
    source = intake.source_snapshot(POC_ID, snapshot["source_receipt_id"])
    assert "730" in source.redacted_text and "0.7" in source.redacted_text
    assert "alice@example.com" not in source.redacted_text
    assert source.adapter_name == "zoom_rtms"
    assert action(runtime, "process") == snapshot
    assert len(intake.list_receipts(POC_ID)) == 1
    assert len(rig[6].ids()) == 1
    proposals = intake.proposal_inputs(POC_ID)
    assert proposals and all(
        p.source_receipt_id == snapshot["source_receipt_id"] for p in proposals
    )
    assert all(p.state == "NEEDS_REVIEW" for p in proposals)
    # Receipt metadata has an exact allowlist. Random hexadecimal identities may
    # contain a numeric requirement by coincidence; that is not transcript text.
    assert re.fullmatch(r"zoomsess_[a-f0-9]{64}", session_id)
    assert re.fullmatch(r"srcpt_[a-f0-9]{32}", snapshot["source_receipt_id"])
    assert snapshot == {
        "schema_version": "exitspec.zoom-live/1.0",
        "poc_id": POC_ID,
        "session_id": session_id,
        "state": "DRAFT_READY",
        "transport_mode": "FAKE_ZOOM_RTMS",
        "provider_connected": False,
        "source_content_classification": "SYNTHETIC_REQUIREMENTS_ONLY",
        "capture_scope": "BOUNDED_WINDOW_NOT_COMPLETE_MEETING",
        "segment_count": 2,
        "proposal_count": 2,
        "source_receipt_id": snapshot["source_receipt_id"],
        "review_url": f"/app/pocs/{POC_ID}/review",
        "failure_code": None,
    }
    assert (
        runtime._record.provenance["redacted_content_sha256"] == source.content_sha256
    )
    assert runtime._record.provenance["code_revision"] == "a" * 40


def test_start_is_consent_gated_and_browser_cannot_pair_or_choose_stream(rig):
    runtime = rig[0]
    for update in (
        {"consent_acknowledged": False},
        {"meeting_uuid": "another"},
        {"action": "pair"},
    ):
        with pytest.raises(ZoomLiveError):
            action(runtime, "start", **update)
    assert runtime.current(POC_ID)["state"] == "PAIRED"
    assert not rig[2]


def test_waiting_is_not_connected_and_wrong_poc_does_not_start(rig):
    runtime = rig[0]
    with pytest.raises(ZoomLiveError):
        runtime.action(
            "poc_wrong",
            {
                "action": "start",
                "session_id": "wrong",
                "idempotency_key": "request_start",
                "consent_acknowledged": True,
            },
        )
    snapshot = action(runtime, "start")
    assert snapshot["state"] == "WAITING" and not snapshot["provider_connected"]


def test_duplicate_native_bytes_reconnect_and_pending_stop_segments(rig):
    runtime = rig[0]
    child = start(rig)
    packet(child)
    child.emit("interrupted")
    child.emit("reconnecting")
    assert not runtime.current(POC_ID)["provider_connected"]
    child.emit("listening")
    packet(child)
    assert runtime.current(POC_ID)["segment_count"] == 1
    action(runtime, "stop")
    with pytest.raises(ZoomLiveError):
        action(runtime, "process")
    packet(child, data="Throughput must exceed 222 tokens per second.")
    child.emit("stop_ack")
    assert runtime.current(POC_ID)["state"] == "DRAINING"
    packet(child, data="Concurrency must reach 7.")
    child.emit("drained")
    assert runtime.current(POC_ID)["segment_count"] == 3
    assert action(runtime, "process")["state"] == "DRAFT_READY"


@pytest.mark.parametrize(
    "case",
    [
        "wrong_stream",
        "wrong_generation",
        "replay",
        "unknown_participant",
        "forged_segment",
        "early_drain",
        "early_ack",
        "extra_field",
    ],
)
def test_untrusted_or_out_of_order_events_fail_without_attachment(rig, case):
    runtime, intake, *_ = rig
    child = start(rig)
    packet(child)
    if case == "wrong_stream":
        child.emit("listening", stream_id="other")
    elif case == "wrong_generation":
        child.emit("listening", generation="b" * 64)
    elif case == "replay":
        child.emit("listening", seq=child.seq)
    elif case == "unknown_participant":
        packet(child, user_id=99)
    elif case == "forged_segment":
        child.emit("transcript", packet_base64="", normalized_sha256="a" * 64)
    elif case == "early_drain":
        child.emit("drained")
    elif case == "early_ack":
        child.emit("stop_ack")
    else:
        child.emit("participant", user_id=42, unexpected=True)
    assert runtime.current(POC_ID)["state"] == "FAILED"
    assert child.closed and not intake.list_receipts(POC_ID)


def test_reset_replacement_and_old_callbacks_cannot_touch_new_session(rig):
    runtime = rig[0]
    child = start(rig)
    old_session = runtime.current(POC_ID)["session_id"]
    action(runtime, "reset")
    assert child.closed
    new = runtime.pair(POC_ID, settings())
    assert new["session_id"] != old_session
    child.emit("listening")
    assert runtime.current(POC_ID)["state"] == "PAIRED"
    with pytest.raises(ZoomLiveError):
        action(runtime, "start", session_id=old_session)


@pytest.mark.parametrize(
    "mode", ["closed", "archived", "expired", "stop_timeout", "late_packet"]
)
def test_capture_revoked_or_failed_before_any_unsafe_attachment(rig, mode):
    runtime, _intake, _, _, closed, now, drafts = rig
    child = start(rig)
    packet(child)
    if mode == "closed":
        closed.add(POC_ID)
    elif mode == "archived":
        drafts.archive(POC_ID)
    elif mode == "expired":
        now[0] = 901
    elif mode == "stop_timeout":
        action(runtime, "stop")
        now[0] = 21
    else:
        finish(rig)
        packet(child, data="Late unmet requirement must not be dropped.")
    runtime.tick()
    assert runtime._record.state in {"FAILED", "REVOKED"}
    assert child.closed
    with pytest.raises(ZoomLiveError):
        runtime.action(
            POC_ID,
            {
                "action": "process",
                "session_id": runtime._record.session_id,
                "idempotency_key": "process_closed",
            },
        )


def test_empty_capture_and_aggregate_text_overflow_fail(rig):
    runtime = rig[0]
    child = start(rig)
    for i in range(3):
        packet(child, data=("x" * 7990) + str(i))
    assert runtime.current(POC_ID)["state"] == "FAILED"


def test_ipc_checks_declared_size_before_reading_body_and_rejects_duplicate_keys():
    class HeaderOnly(io.BytesIO):
        def read(self, size=-1):
            assert self.tell() == 0, "body was read before validating size"
            return super().read(size)

    with pytest.raises(ValueError):
        read_frame(HeaderOnly(struct.pack(">I", MAX_IPC_FRAME + 1)))
    value = {"event": "listening", "seq": 1}
    assert read_frame(io.BytesIO(encode_frame(value))) == value
    duplicate = b'{"event":"a","event":"b"}'
    with pytest.raises(ValueError):
        read_frame(io.BytesIO(struct.pack(">I", len(duplicate)) + duplicate))


def test_native_publication_follows_source_then_draft_lock_order(rig, monkeypatch):
    runtime, intake, _, _, _, _, drafts = rig
    child = start(rig)
    packet(child)
    finish(rig)
    source_held, zoom_waiting, source_done, zoom_done = (
        threading.Event() for _ in range(4)
    )
    original = type(intake).native_attachment_guard
    errors = []

    @contextmanager
    def guarded(self, poc_id):
        zoom_waiting.set()
        with original(self, poc_id):
            yield

    monkeypatch.setattr(type(intake), "native_attachment_guard", guarded)

    def source_writer():
        try:
            with intake._source_service.attachment_guard(POC_ID):
                source_held.set()
                assert zoom_waiting.wait(2)
                # Existing source-owner operations read the draft while holding source.
                drafts.get(POC_ID)
            source_done.set()
        except Exception as error:  # noqa: BLE001 - collect worker failures for the main test assertion
            errors.append(error)

    def zoom_writer():
        try:
            action(runtime, "process")
            zoom_done.set()
        except Exception as error:  # noqa: BLE001 - collect worker failures for the main test assertion
            errors.append(error)

    first = threading.Thread(target=source_writer, daemon=True)
    second = threading.Thread(target=zoom_writer, daemon=True)
    first.start()
    assert source_held.wait(2)
    second.start()
    assert source_done.wait(2), (
        "draft -> source inversion deadlocked the existing source writer"
    )
    assert zoom_done.wait(2)
    first.join(1)
    second.join(1)
    assert not errors


def test_empty_capture_cannot_finalize(rig):
    runtime = rig[0]
    child = start(rig)
    action(runtime, "stop")
    child.emit("stop_ack")
    child.emit("drained")
    assert runtime.current(POC_ID)["state"] == "FAILED"
