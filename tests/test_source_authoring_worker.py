"""Real bounded subprocess proofs; every worker is incapable of network I/O."""

from __future__ import annotations

import dataclasses
import os
import select
import struct
import subprocess
import sys
import threading
import time

import pytest

from exitspec.source_authoring_ipc import (
    MAX_BODY_BYTES,
    MAX_REQUEST_WIRE,
    MAX_RESPONSE_BYTES,
    MAX_RESULT_WIRE,
    SourceAuthoringWorkerError,
    body_digest,
    decode_frame,
    encode_frame,
    read_wire,
)
from exitspec.source_authoring_supervisor import (
    SourceAuthoringSupervisor,
    SyntheticSourceAuthoringSupervisor,
)

BODY = b'{"text":"PRIVATE-SYNTHETIC-SOURCE"}'
RESPONSE = b'{"synthetic":"private-output"}'


def binding(body=BODY):
    return {
        "epoch": "a" * 64,
        "grant": "b" * 64,
        "operation": "operation_test",
        "body_sha256": body_digest(body),
        "profile_sha256": "c" * 64,
    }


def ready(*, scenario="success", seconds=3, response=RESPONSE, body=BODY):
    supervisor = SyntheticSourceAuthoringSupervisor(
        synthetic_response=response, synthetic_scenario=scenario
    )
    bound = supervisor.prepare(binding(body), time.monotonic() + seconds)
    return supervisor, bound


def dispatch(supervisor, bound, body=BODY):
    ticket = supervisor.prepare_ticket(bound, body)
    with supervisor.dispatch_guard(ticket) as guard:
        guard.stage()
    supervisor.handoff()
    return ticket


def test_real_mode_and_credentials_cannot_activate_a_network_worker():
    with pytest.raises(SourceAuthoringWorkerError, match="refused") as error:
        SourceAuthoringSupervisor(network_enabled=True, credential="DO-NOT-LOG")
    assert error.value.code == "live_prerequisites_missing"
    assert "DO-NOT-LOG" not in str(error.value)
    with pytest.raises(TypeError):
        SyntheticSourceAuthoringSupervisor(credential="not-supported")


def test_ready_stage_handoff_and_one_result_are_distinct_no_replay(monkeypatch):
    original = subprocess.Popen
    launches = []

    def capture(*args, **kwargs):
        launches.append((args, kwargs))
        return original(*args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", capture)
    supervisor, bound = ready()
    try:
        assert supervisor.state == "READY_NO_SEND" and supervisor.slot_occupied
        assert select.select([supervisor._process.stdout], [], [], 0.03)[0] == []
        ticket = supervisor.prepare_ticket(bound, BODY)
        with supervisor.dispatch_guard(ticket) as guard:
            guard.stage()
            assert supervisor.state == "DISPATCH_AUTHORIZED"
            assert select.select([supervisor._process.stdout], [], [], 0)[0] == []
        supervisor.handoff()
        assert supervisor.collect() == RESPONSE
        assert supervisor.state == "RESULT_READY" and not supervisor.slot_occupied
        for operation in (
            supervisor.handoff,
            supervisor.collect,
            lambda: supervisor.prepare(binding(), time.monotonic() + 3),
        ):
            with pytest.raises(SourceAuthoringWorkerError):
                operation()
        args, kwargs = launches[0]
        assert args[0] == [
            os.path.abspath(sys.executable),
            "-I",
            "-m",
            "exitspec.source_authoring_worker",
        ]
        assert kwargs["env"] == {} and kwargs["close_fds"] is True
        assert kwargs["stderr"] is subprocess.DEVNULL
        assert len(launches) == 1
        assert BODY.decode() not in repr(args) + repr(kwargs)
    finally:
        supervisor.cancel()
        assert supervisor.reap()


def test_forged_ticket_and_wrong_body_fail_before_handoff():
    supervisor, bound = ready()
    try:
        with pytest.raises(SourceAuthoringWorkerError):
            supervisor.prepare_ticket(bound, BODY + b" ")
        ticket = supervisor.prepare_ticket(bound, BODY)
        with (
            pytest.raises(SourceAuthoringWorkerError),
            supervisor.dispatch_guard(dataclasses.replace(ticket)),
        ):
            pytest.fail("copied ticket was authorized")
        assert supervisor.state == "READY_NO_SEND"
        assert select.select([supervisor._process.stdout], [], [], 0)[0] == []
    finally:
        supervisor.cancel()
        assert supervisor.reap()


@pytest.mark.parametrize("staged", [False, True])
def test_cancel_before_handoff_discards_ticket_without_execution(staged):
    supervisor, bound = ready()
    if staged:
        ticket = supervisor.prepare_ticket(bound, BODY)
        with supervisor.dispatch_guard(ticket) as guard:
            guard.stage()
    supervisor.cancel()
    with pytest.raises(SourceAuthoringWorkerError):
        supervisor.handoff()
    assert supervisor._staged is None and supervisor._prepared is None
    assert supervisor.reap() and not supervisor.slot_occupied


@pytest.mark.parametrize(
    "scenario",
    ["stall_dns", "stall_connect", "stall_headers", "stall_body", "slow_trickle"],
)
def test_total_deadline_kills_each_synthetic_phase_and_trickle(scenario):
    started = time.monotonic()
    supervisor, bound = ready(scenario=scenario, seconds=0.4)
    dispatch(supervisor, bound)
    with pytest.raises(SourceAuthoringWorkerError) as error:
        supervisor.collect()
    assert error.value.code in {
        "worker_timeout",
        "worker_partial_frame",
        "worker_state",
    }
    assert supervisor.reap()
    assert not supervisor.slot_occupied
    assert time.monotonic() - started < 2


@pytest.mark.parametrize(
    "scenario",
    [
        "partial_result",
        "oversize_result",
        "malformed_result",
        "extra_result",
        "wrong_binding",
        "worker_error",
    ],
)
def test_invalid_or_incomplete_result_is_terminal_and_not_retransmitted(scenario):
    supervisor, bound = ready(scenario=scenario)
    dispatch(supervisor, bound)
    with pytest.raises(SourceAuthoringWorkerError) as error:
        supervisor.collect()
    assert "private-output" not in str(error.value)
    assert supervisor.reap()
    with pytest.raises(SourceAuthoringWorkerError):
        supervisor.handoff()


def test_real_subprocess_actual_body_and_result_byte_boundaries():
    body = b"x" * MAX_BODY_BYTES
    supervisor, bound = ready(scenario="result_boundary", body=body)
    ticket = dispatch(supervisor, bound, body)
    assert len(ticket.wire) <= MAX_REQUEST_WIRE
    assert len(supervisor.collect()) == MAX_RESPONSE_BYTES
    assert supervisor.reap()


def test_wire_cap_counts_binary_payload_and_multibyte_metadata_exactly():
    for size, cap in (
        (MAX_BODY_BYTES, MAX_REQUEST_WIRE),
        (MAX_RESPONSE_BYTES, MAX_RESULT_WIRE),
    ):
        payload = b"z" * size
        frame = encode_frame({"meta": "é" * 4000}, payload, maximum_wire=cap)
        assert len(frame) <= cap and len(frame) > size + 8000
        assert decode_frame(frame, maximum_wire=cap, maximum_payload=size)[1] == payload
        with pytest.raises(SourceAuthoringWorkerError):
            decode_frame(frame, maximum_wire=len(frame) - 1, maximum_payload=size)
        with pytest.raises(SourceAuthoringWorkerError):
            decode_frame(frame, maximum_wire=cap, maximum_payload=size - 1)
    with pytest.raises(SourceAuthoringWorkerError):
        encode_frame({"meta": "é" * 9000}, b"", maximum_wire=MAX_REQUEST_WIRE)


@pytest.mark.parametrize(
    "bad",
    [
        b"",
        b"\0\0",
        struct.pack(">I", MAX_RESULT_WIRE),
        struct.pack(">II", 13, 9) + b'{"x":NaN}',
        struct.pack(">II", 17, 13) + b'{"x":1,"x":2}',
    ],
)
def test_partial_oversize_malformed_wire_rejected_without_unbounded_read(bad):
    incoming, outgoing = os.pipe()
    try:
        os.write(outgoing, bad)
        os.close(outgoing)
        with pytest.raises(SourceAuthoringWorkerError):
            read_wire(
                incoming,
                maximum_wire=MAX_RESULT_WIRE,
                maximum_payload=MAX_RESPONSE_BYTES,
                deadline=time.monotonic() + 0.1,
            )
    finally:
        os.close(incoming)


def test_slot_retained_until_actual_exit_observed(monkeypatch):
    supervisor, bound = ready(scenario="stall_body")
    dispatch(supervisor, bound)
    original_wait = supervisor._process.wait
    entered, release = threading.Event(), threading.Event()

    def blocked_wait(*args, **kwargs):
        entered.set()
        assert release.wait(1)
        return original_wait(*args, **kwargs)

    monkeypatch.setattr(supervisor._process, "wait", blocked_wait)
    supervisor.cancel()
    assert entered.wait(1)
    assert supervisor.slot_occupied
    assert not supervisor.reap(timeout=0)
    release.set()
    assert supervisor.reap()
    assert not supervisor.slot_occupied


@pytest.mark.parametrize(
    "tamper",
    [
        "epoch",
        "grant",
        "operation",
        "generation",
        "nonce",
        "body_sha256",
        "profile_sha256",
        "extra_ticket",
        "body",
        "real_mode",
    ],
)
def test_actual_worker_rejects_tampered_or_replayed_ticket_without_result(tamper):
    from exitspec.source_authoring_worker import SYNTHETIC_MODE

    fields = binding() | {"generation": "d" * 64, "nonce": "e" * 64}
    deadline = time.monotonic() + 3
    bootstrap = encode_frame(
        {
            "event": "prepare",
            "mode": SYNTHETIC_MODE,
            "binding": fields,
            "deadline": deadline,
            "scenario": "success",
        },
        RESPONSE,
        maximum_wire=MAX_REQUEST_WIRE,
    )
    altered = dict(fields)
    if tamper in fields:
        altered[tamper] = "f" * 64
    ticket = encode_frame(
        {
            "event": "send_ticket",
            "mode": "LIVE" if tamper == "real_mode" else SYNTHETIC_MODE,
            "binding": altered,
            "deadline": deadline,
        },
        BODY + b" " if tamper == "body" else BODY,
        maximum_wire=MAX_REQUEST_WIRE,
    )
    child = subprocess.Popen(
        [
            os.path.abspath(sys.executable),
            "-I",
            "-m",
            "exitspec.source_authoring_worker",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={},
        close_fds=True,
    )
    output, diagnostic = child.communicate(
        bootstrap + ticket + (ticket if tamper == "extra_ticket" else b""), timeout=3
    )
    assert child.returncode == 2 and diagnostic == b""
    ready_size = struct.unpack(">I", output[:4])[0] + 4
    assert len(output) == ready_size
    metadata, payload = decode_frame(
        output, maximum_wire=MAX_RESULT_WIRE, maximum_payload=0
    )
    assert metadata["event"] == "READY_NO_SEND" and payload == b""


def test_partial_ticket_cannot_produce_a_result_or_diagnostics():
    from exitspec.source_authoring_worker import SYNTHETIC_MODE

    fields = binding() | {"generation": "d" * 64, "nonce": "e" * 64}
    deadline = time.monotonic() + 3
    bootstrap = encode_frame(
        {
            "event": "prepare",
            "mode": SYNTHETIC_MODE,
            "binding": fields,
            "deadline": deadline,
            "scenario": "success",
        },
        RESPONSE,
        maximum_wire=MAX_REQUEST_WIRE,
    )
    child = subprocess.Popen(
        [
            os.path.abspath(sys.executable),
            "-I",
            "-m",
            "exitspec.source_authoring_worker",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={},
        close_fds=True,
    )
    output, diagnostic = child.communicate(
        bootstrap + struct.pack(">I", 100) + b"PRIVATE", timeout=3
    )
    assert child.returncode == 2 and diagnostic == b""
    assert len(output) == struct.unpack(">I", output[:4])[0] + 4


def test_blocked_ipc_write_obeys_deadline_without_waiting_for_a_reader():
    from exitspec.source_authoring_ipc import write_wire

    incoming, outgoing = os.pipe()
    os.set_blocking(outgoing, False)
    start = time.monotonic()
    try:
        with pytest.raises(SourceAuthoringWorkerError) as error:
            write_wire(outgoing, b"x" * MAX_RESULT_WIRE, deadline=start + 0.1)
        assert error.value.code == "worker_timeout"
        assert time.monotonic() - start < 0.5
    finally:
        os.close(incoming)
        os.close(outgoing)


def test_actual_worker_handoff_stall_has_one_second_delivery_bound():
    body = b"x" * MAX_BODY_BYTES
    supervisor, bound = ready(scenario="stall_ticket_read", body=body)
    ticket = supervisor.prepare_ticket(bound, body)
    with supervisor.dispatch_guard(ticket) as guard:
        guard.stage()
    started = time.monotonic()
    with pytest.raises(SourceAuthoringWorkerError) as error:
        supervisor.handoff()
    assert error.value.code == "worker_handoff"
    assert time.monotonic() - started < 1.5
    assert supervisor.reap() and not supervisor.slot_occupied
