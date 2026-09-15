"""Offline proof only; no assertion of pre-media provider event availability."""

from __future__ import annotations

import dataclasses
import json
import pickle
import shutil
import subprocess
import threading
from pathlib import Path

import pytest

from exitspec.zoom_live_operator import _enroll_and_pair, _pair_zoom_in_server
from exitspec.zoom_live_runtime import ZoomEnrollmentSettings, ZoomLiveError
from tests.test_zoom_guided_handoff import POC_ID
from tests.test_zoom_live_runtime import action, packet, settings
from tests.test_zoom_live_runtime import (
    rig as rig,  # noqa: PLC0414 - register the existing pytest fixture
)


def enrollment_settings(**changes):
    values = dataclasses.asdict(settings())
    values.pop("participant_ids")
    values.update(maximum_capture_seconds=120, metadata_custody_confirmed=True,
                  metadata_consent_receipts=("synthetic-person-one", "synthetic-person-two"))
    return ZoomEnrollmentSettings(**(values | changes))


def begin(rig):
    runtime, _, children, launched, *_ = rig
    launched.clear()
    handle = runtime.begin_enrollment(POC_ID, enrollment_settings())
    assert launched.wait(2)
    for _ in range(100):
        with runtime._lock:
            if runtime._record.child is not None:
                break
        threading.Event().wait(0.001)
    child = children[-1]
    child.emit("offer")
    child.emit("enrollment_ready")
    assert runtime.enrollment_status(handle)["state"] == "READY"
    return handle, child


def confirm(runtime, handle, child, identifier):
    runtime.arm_enrollment(handle)
    nonce = child.sent[-1]["nonce"]
    child.emit("enrollment_armed", nonce=nonce)
    child.emit("enrollment_candidate", nonce=nonce, user_id=identifier)
    runtime.confirm_enrollment(handle, identifier)
    child.emit("enrollment_confirmed", nonce=nonce, user_id=identifier)
    return nonce


def adopted(rig):
    handle, child = begin(rig)
    runtime = rig[0]
    confirm(runtime, handle, child, 42)
    confirm(runtime, handle, child, 43)
    runtime.adopt_enrollment(handle)
    return handle, child


@pytest.mark.parametrize("changes", [
    {"metadata_custody_confirmed": False},
    {"metadata_custody_confirmed": 1},
    {"metadata_consent_receipts": ("same-receipt", "same-receipt")},
    {"metadata_consent_receipts": ("only-one",)},
    {"metadata_consent_receipts": ["first-person", "second-person"]},
    {"metadata_consent_receipts": ("first-person", "bad\nreceipt")},
    {"maximum_capture_seconds": 121},
    {"credits_and_budget_confirmed": False},
    {"synthetic_requirements_consent": False},
    {"live_network_authorized": False},
    {"credential_rotation_confirmed": False},
])
def test_metadata_and_account_approvals_precede_child_or_callback(rig, changes):
    runtime, _, children, *_ = rig
    with pytest.raises(ZoomLiveError):
        runtime.begin_enrollment(POC_ID, enrollment_settings(**changes))
    assert children == []
    assert runtime.current(POC_ID)["state"] == "PAIRED"


def test_direct_exact_pairing_rejects_enrollment_type_and_empty_roster(rig):
    runtime = rig[0]
    for value in [enrollment_settings(), settings(participant_ids=()), settings(participant_ids=("42", "43"))]:
        with pytest.raises(ZoomLiveError):
            runtime.pair(POC_ID, value)
    assert runtime.current(POC_ID)["state"] == "PAIRED"


def test_private_handle_hidden_nonserializable_one_use_and_same_stream_adoption(rig):
    runtime, _, children, _, _, now, _ = rig
    handle, child = begin(rig)
    r = runtime._record
    identity = (r, child, r.generation, r.stream_id, r.expires)
    assert runtime.current(POC_ID)["state"] == "UNPAIRED"
    assert runtime.current(POC_ID)["session_id"] is None
    with pytest.raises(TypeError):
        pickle.dumps(handle)
    with pytest.raises(TypeError):
        json.dumps(handle)
    with pytest.raises(ZoomLiveError):
        runtime.enrollment_status(object())
    confirm(runtime, handle, child, 42)
    confirm(runtime, handle, child, 43)
    now[0] = 29
    snapshot = runtime.adopt_enrollment(handle)
    assert snapshot["state"] == "PAIRED"
    assert (r, r.child, r.generation, r.stream_id, r.expires) == identity
    assert r.settings.participant_ids == (42, 43)
    assert len(children) == 1
    assert child.sent[-1]["command"] == "enrollment_seal"
    assert r.binding is None and not r.packets
    with pytest.raises(ZoomLiveError):
        runtime.adopt_enrollment(handle)
    assert runtime.current(POC_ID)["state"] == "PAIRED"
    now[0] = 119
    action(runtime, "start")
    assert child.sent[-1]["command"] == "enrollment_capture"
    assert r.expires == 120
    assert len(children) == 1 and r.child is child
    now[0] = 120
    runtime.tick()
    assert child.closed
    assert runtime.current(POC_ID)["state"] == "REVOKED"


@pytest.mark.parametrize("kind,extra", [
    ("transcript", {"packet_base64": "UFJJVkFURQ=="}),
    ("listening", {}),
    ("participant_left", {"user_id": 42}),
    ("reconnecting", {}),
    ("interrupted", {}),
    ("enrollment_armed", {"nonce": "f" * 64}),
    ("enrollment_candidate", {"nonce": "f" * 64, "user_id": 42}),
])
def test_pending_events_cannot_admit_media_or_unarmed_metadata(rig, kind, extra):
    handle, child = begin(rig)
    child.emit(kind, **extra)
    assert child.closed
    assert rig[0]._record.packets == {}
    with pytest.raises(ZoomLiveError):
        rig[0].enrollment_status(handle)


@pytest.mark.parametrize("value", ["42", True, 0, 2**32])
def test_numeric_metadata_requires_actual_uint32(rig, value):
    _, child = begin(rig)
    child.emit("participant", user_id=value)
    assert child.closed


def test_third_id_and_wrong_stream_revoke_without_source_text(rig):
    handle, child = begin(rig)
    for identifier in (42, 43, 44):
        child.emit("participant", user_id=identifier)
    assert child.closed and not rig[0]._record.packets
    with pytest.raises(ZoomLiveError):
        rig[0].adopt_enrollment(handle)


def test_old_slot_nonce_cannot_satisfy_new_confirmation(rig):
    handle, child = begin(rig)
    old = confirm(rig[0], handle, child, 42)
    rig[0].arm_enrollment(handle)
    child.emit("enrollment_armed", nonce=old)
    assert child.closed


def test_wrong_human_confirmation_revokes_pending_handle(rig):
    handle, child = begin(rig)
    runtime = rig[0]
    runtime.arm_enrollment(handle)
    nonce = child.sent[-1]["nonce"]
    child.emit("enrollment_armed", nonce=nonce)
    child.emit("enrollment_candidate", nonce=nonce, user_id=42)
    with pytest.raises(ZoomLiveError):
        runtime.confirm_enrollment(handle, 43)
    assert child.closed


@pytest.mark.parametrize("after_adoption", [False, True])
@pytest.mark.parametrize("operation", ["close", "revoke", "reset", "replace", "owner_closed", "timeout"])
def test_pending_and_adopted_lifecycle_revokes_and_reaps(rig, after_adoption, operation):
    runtime, _, _, _, closed, now, _ = rig
    handle, child = adopted(rig) if after_adoption else begin(rig)
    if operation == "close":
        runtime.close()
    elif operation == "revoke":
        runtime.revoke(POC_ID)
    elif operation == "reset":
        runtime.action(POC_ID, {"action": "reset", "session_id": runtime._record.session_id, "idempotency_key": "owner-reset"})
    elif operation == "replace":
        runtime.pair(POC_ID, settings())
    elif operation == "owner_closed":
        closed.add(POC_ID)
        runtime.tick()
    else:
        now[0] = 120 if after_adoption else 30
        runtime.tick()
    assert child.closed
    child.emit("enrollment_ready")
    with pytest.raises(ZoomLiveError):
        runtime.enrollment_status(handle)


def test_adoption_requires_two_completed_confirmations(rig):
    handle, child = begin(rig)
    confirm(rig[0], handle, child, 42)
    with pytest.raises(ZoomLiveError):
        rig[0].adopt_enrollment(handle)
    assert child.closed


def test_browser_cannot_start_pending_enrollment_or_construct_handle(rig):
    handle, child = begin(rig)
    with pytest.raises(ZoomLiveError):
        action(rig[0], "start")
    with pytest.raises(ZoomLiveError):
        rig[0].action(POC_ID, {"action": "enroll", "handle": handle})
    assert child.sent[-1]["command"] == "bind"
    assert not child.closed


def test_media_before_separate_browser_consent_is_rejected(rig):
    _, child = adopted(rig)
    packet(child, user_id=42)
    assert child.closed and not rig[0]._record.packets


def test_unknown_participant_after_adoption_still_refuses(rig):
    _, child = adopted(rig)
    child.emit("participant", user_id=99)
    assert child.closed


def test_confirmed_pair_flows_through_current_capture_and_content_free_receipt(rig):
    runtime = rig[0]
    _, child = adopted(rig)
    action(runtime, "start")
    child.emit("listening")
    packet(child, user_id=42)
    action(runtime, "stop")
    child.emit("stop_ack")
    child.emit("drained")
    action(runtime, "process")
    receipt = runtime.receipt(POC_ID)
    assert receipt["metadata_enrollment_sha256"] == runtime._record.enrollment_digest
    assert receipt["participant_association"] == "HUMAN_ATTESTED_NOT_AUTHENTICATED_IDENTITY"
    assert receipt["provider_stop_acknowledged"] and receipt["normal_media_close_observed"]
    assert "participant_ids" not in receipt and "metadata_consent_receipts" not in receipt
    assert receipt["capture_scope"] == "BOUNDED_WINDOW_NOT_COMPLETE_MEETING"


def test_operator_declined_metadata_consent_reads_no_credentials_or_starts_callback():
    class Server:
        zoom_live_runtime = None
    answers = iter(["APPROVED", POC_ID, "NO"])
    assert not _pair_zoom_in_server(Server(), "a" * 40, enroll_metadata=True,
                                   read_text=lambda _: next(answers), write=lambda _: None,
                                   secret=lambda _: pytest.fail("credential read"))


def test_operator_wait_timeout_cancels_without_raw_error_or_retries(rig):
    runtime = rig[0]
    now = rig[5]
    with pytest.raises(ZoomLiveError, match="refused"):
        _enroll_and_pair(runtime, POC_ID, enrollment_settings(), secret=lambda _: pytest.fail("no observation"),
                         write=lambda _: None, pause=lambda _: now.__setitem__(0, 30))
    runtime._launch_idle.wait(2)
    assert all(child.closed for child in rig[2])
    assert not runtime._record.packets


def test_old_operator_cancellation_cannot_revoke_a_new_pairing(rig):
    handle, child = begin(rig)
    runtime = rig[0]
    replacement = runtime.pair(POC_ID, settings())
    runtime.cancel_enrollment(handle)
    assert child.closed
    assert runtime.current(POC_ID) == replacement


def test_owner_close_during_seal_cannot_publish_adopted_pairing(rig):
    handle, child = begin(rig)
    runtime = rig[0]
    confirm(runtime, handle, child, 42)
    confirm(runtime, handle, child, 43)
    send = child.send
    def close_during_send(value):
        send(value)
        if value["command"] == "enrollment_seal":
            runtime.close()
    child.send = close_during_send
    with pytest.raises(ZoomLiveError):
        runtime.adopt_enrollment(handle)
    assert runtime._record.state == "REVOKED"
    assert child.closed and runtime._record.settings is None


def test_wrong_stream_during_pending_enrollment_reaps_child(rig):
    handle, child = begin(rig)
    child.seq += 1
    child.receive({"event": "participant", "generation": child.init["generation"],
                   "seq": child.seq, "stream_id": "other-stream", "user_id": 42})
    assert child.closed
    with pytest.raises(ZoomLiveError):
        rig[0].enrollment_status(handle)


def test_new_metadata_phase_cannot_renew_the_original_deadline(rig):
    handle, child = begin(rig)
    runtime = rig[0]
    original = child.init["enrollment"].copy()
    rig[5][0] = 29
    runtime.arm_enrollment(handle)
    rig[5][0] = 30
    runtime.tick()
    assert child.closed and child.init["enrollment"] == original
    assert runtime._record.expires == 120


def test_actual_node_child_transport_enrollment_regressions():
    node = shutil.which("node")
    assert node is not None, "Node is mandatory for native enrollment regressions."
    root = Path(__file__).resolve().parents[1] / "tools/zoom_fixture_operator"
    result = subprocess.run([node, "--test", "live-child.test.mjs", "rtms-transport.test.mjs",
                             "roster-enrollment.test.mjs", "operator-capture-lib.test.mjs"],
                            cwd=root, env={}, capture_output=True, text=True, timeout=20, check=False)
    assert result.returncode == 0, result.stdout + result.stderr


def test_opt_in_cli_uses_the_existing_source_app_without_a_fireworks_handle(monkeypatch):
    from exitspec import poc_source_demo, zoom_live_operator

    calls = []
    class Server:
        def serve_forever(self):
            pass
        def shutdown(self):
            calls.append("shutdown")
        def server_close(self):
            calls.append("closed")
    server = Server()
    def factory(**kwargs):
        calls.append(kwargs)
        assert "source_authoring_launch" not in kwargs
        return server
    def pair(value, revision, **kwargs):
        assert value is server and revision == "a" * 40
        assert kwargs == {"enroll_metadata": True}
        calls.append("pair")
        return True
    monkeypatch.setattr("sys.argv", ["zoom-live", "--enroll-metadata"])
    monkeypatch.setattr(poc_source_demo, "serve_source_neutral_demo", factory)
    monkeypatch.setattr(zoom_live_operator, "serve_demo", lambda **_: pytest.fail("wrong app"))
    monkeypatch.setattr(zoom_live_operator, "_pair_zoom_in_server", pair)
    monkeypatch.setattr(zoom_live_operator.subprocess, "check_output",
                        lambda args, **_: "a" * 40 if "rev-parse" in args else b"")
    monkeypatch.setattr("builtins.input", lambda _: "")
    zoom_live_operator.main()
    assert calls[-3:] == ["pair", "shutdown", "closed"]
