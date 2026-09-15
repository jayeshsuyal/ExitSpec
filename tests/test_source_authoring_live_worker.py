"""Disabled production controls and actual private-pipe fake-child proofs."""

import dataclasses
import io
import json
import os
import select
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from exitspec import source_authoring_launch as launch
from exitspec import source_authoring_live_worker as worker
from exitspec import source_authoring_supervisor as supervisors
from exitspec.canonical import canonical_json_bytes
from exitspec.source_authoring_ipc import (
    SourceAuthoringWorkerError,
    body_digest,
    require_eof,
    write_wire,
)
from exitspec.source_authoring_live_ipc import encode_live_frame, read_live_frame
from exitspec.source_authoring_pins import REQUEST_PROFILE_JSON
from tests.helpers.source_authoring_admission import (
    bound_lease,
    fake_profile,
    install_fake_profile,
)


def body(text="Literal synthetic source only."):
    value = json.loads(REQUEST_PROFILE_JSON)["body_template"]
    value["messages"][1]["content"] = (
        "Untrusted redacted source JSON follows:\n"
        + canonical_json_bytes({"text": text}).decode()
    )
    return canonical_json_bytes(value)


def admission(request):
    return {
        "epoch": "a" * 64,
        "grant": "b" * 64,
        "operation": "operation_test",
        "body_sha256": body_digest(request),
        "profile_sha256": worker.PROFILE_SHA256,
        "launch_profile_sha256": fake_profile().launch_profile_sha256,
        "credential_generation": 1,
        "code_revision": "e" * 40,
    }


def ready(monkeypatch, scenario="success", seconds=3, request=None):
    launches = []

    def spawn(self, fd):
        args = [
            os.path.abspath(sys.executable),
            "-I",
            str(Path(__file__).parent / "helpers/source_authoring_fake_worker.py"),
            scenario,
        ]
        kwargs = {
            "env": {},
            "close_fds": True,
            "pass_fds": (fd,),
            "stdin": subprocess.PIPE,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.DEVNULL,
            "bufsize": 0,
        }
        launches.append((args, kwargs))
        return subprocess.Popen(args, **kwargs)

    monkeypatch.setattr(supervisors._BoundedLiveSupervisor, "_spawn", spawn)
    instance = supervisors._BoundedLiveSupervisor(lease=bound_lease(monkeypatch))
    request = body() if request is None else request
    binding = instance.prepare(admission(request), time.monotonic() + seconds)
    return instance, binding, request, launches


def stage(instance, binding, request):
    ticket = instance.prepare_ticket(
        binding, request, b"SYNTHETIC-KEY", credential_generation=1
    )
    with instance.dispatch_guard(ticket) as guard:
        guard.stage()
    return ticket


def test_installed_default_refuses_before_any_side_effect(monkeypatch):
    calls = []

    def forbidden(*args, **kwargs):
        calls.append("forbidden")
        raise AssertionError

    for module, name in (
        (os, "pipe"),
        (os, "read"),
        (os, "set_blocking"),
        (socket, "socket"),
        (socket, "getaddrinfo"),
        (subprocess, "Popen"),
        (worker, "_post_exact"),
    ):
        monkeypatch.setattr(module, name, forbidden)
    monkeypatch.setenv("EXITSPEC_LIVE_PROFILE", "fake")
    monkeypatch.setenv("FIREWORKS_API_KEY", "SYNTHETIC-KEY")
    monkeypatch.setattr(sys, "argv", ["worker", "--fake", "--profile=fake"])
    assert launch._PRODUCTION_PROFILES == ()
    assert worker.main() == 2
    with pytest.raises(SourceAuthoringWorkerError):
        supervisors._BoundedLiveSupervisor()
    with pytest.raises(SourceAuthoringWorkerError):
        supervisors.SourceAuthoringSupervisor()
    assert calls == []


def test_real_installed_child_refuses_forged_input_without_diagnostics():
    result = subprocess.run(
        [sys.executable, "-I", "-m", "exitspec.source_authoring_live_worker", "--fake"],
        input=b"PRIVATE-MARKER",
        capture_output=True,
        timeout=2,
        env={},
        check=False,
    )
    assert result.returncode == 2
    assert result.stdout == result.stderr == b""


def test_successful_fake_control_empty_pipe_before_d_and_one_shot(monkeypatch):
    instance, binding, request, launches = ready(monkeypatch)
    try:
        assert instance.state == "READY_NO_SEND" and instance.slot_occupied
        assert select.select([instance._process.stdout], [], [], 0.02)[0] == []
        writes = []
        original = instance._write_parent

        def record(stream, wire, deadline):
            writes.append((stream, bytes(wire)))
            return original(stream, wire, deadline)

        monkeypatch.setattr(instance, "_write_parent", record)
        ticket = stage(instance, binding, request)
        assert writes == []  # Both pipes remain empty through D staging.
        assert b"SYNTHETIC-KEY" not in ticket.wire
        assert "SYNTHETIC-KEY" not in repr(ticket)
        instance.handoff()
        assert len(writes) == 2
        assert writes[0][0] is instance._process.stdin
        assert writes[1][0] is instance._credential_writer
        assert writes[0][1] == ticket.wire
        assert writes[1][1].endswith(b"SYNTHETIC-KEY")
        result = instance.collect()
        assert json.loads(result)["id"] == "fake-local"
        assert instance.state == "RESULT_READY" and instance.reap()
        assert not instance.slot_occupied
        assert all(
            stream.closed
            for stream in (
                instance._process.stdin,
                instance._process.stdout,
                instance._credential_writer,
            )
        )
        assert len(launches) == 1
        for action in (
            instance.handoff,
            instance.collect,
            lambda: instance.prepare(admission(request), time.monotonic() + 1),
        ):
            with pytest.raises(SourceAuthoringWorkerError):
                action()
    finally:
        instance.cancel()
        assert instance.reap()


@pytest.mark.parametrize(
    "scenario",
    [
        "wrong_binding",
        "malformed_result",
        "partial_result",
        "extra_result",
        "worker_error",
    ],
)
def test_bad_child_results_never_return_private_diagnostics_or_retry(
    monkeypatch, scenario
):
    instance, binding, request, launches = ready(monkeypatch, scenario)
    try:
        stage(instance, binding, request)
        instance.handoff()
        with pytest.raises(SourceAuthoringWorkerError) as error:
            instance.collect()
        assert "PRIVATE-MARKER" not in str(error.value)
        assert instance.reap() and len(launches) == 1
        with pytest.raises(SourceAuthoringWorkerError):
            instance.handoff()
    finally:
        instance.cancel()
        assert instance.reap()


@pytest.mark.parametrize(
    "scenario",
    ["stall_dns", "stall_connect", "stall_headers", "stall_body", "slow_trickle"],
)
def test_total_deadline_kills_blocked_fake_transport(monkeypatch, scenario):
    started = time.monotonic()
    instance, binding, request, _ = ready(monkeypatch, scenario, seconds=0.6)
    stage(instance, binding, request)
    instance.handoff()
    with pytest.raises(SourceAuthoringWorkerError):
        instance.collect()
    assert instance.reap()
    assert time.monotonic() - started < 1.8


@pytest.mark.parametrize(
    "point", ["before_stage", "after_stage", "after_ticket", "after_result"]
)
def test_cancellation_rotation_fences_handoff_and_ready_result(monkeypatch, point):
    instance, binding, request, _ = ready(monkeypatch)
    try:
        ticket = instance.prepare_ticket(
            binding, request, b"SYNTHETIC-KEY", credential_generation=1
        )
        if point == "before_stage":
            instance.cancel()
            with (
                pytest.raises(SourceAuthoringWorkerError),
                instance.dispatch_guard(ticket),
            ):
                pytest.fail("revoked ticket staged")
        else:
            with instance.dispatch_guard(ticket) as guard:
                guard.stage()
            if point == "after_ticket":
                original = instance._close_writer

                def close(stream):
                    original(stream)
                    if stream is instance._process.stdin:
                        instance.cancel()

                monkeypatch.setattr(instance, "_close_writer", close)
                with pytest.raises(SourceAuthoringWorkerError):
                    instance.handoff()
                assert instance._credential_wire == b""
            elif point == "after_result":
                instance.handoff()
                assert instance.collect()
                instance.cancel()
                assert instance.state == "CANCELLED"
            else:
                instance.cancel()
        with pytest.raises(SourceAuthoringWorkerError):
            instance.handoff()
    finally:
        instance.cancel()
        assert instance.reap()


def test_copied_ticket_wrong_secret_generation_and_retained_guard(monkeypatch):
    instance, binding, request, _ = ready(monkeypatch)
    try:
        for generation in (0, 2, True, 1.0):
            with pytest.raises(SourceAuthoringWorkerError):
                instance.prepare_ticket(
                    binding, request, b"SYNTHETIC-KEY", credential_generation=generation
                )
        ticket = instance.prepare_ticket(
            binding, request, b"SYNTHETIC-KEY", credential_generation=1
        )
        with (
            pytest.raises(SourceAuthoringWorkerError),
            instance.dispatch_guard(dataclasses.replace(ticket)),
        ):
            pytest.fail("copied ticket staged")
        with instance.dispatch_guard(ticket) as guard:
            pass
        with pytest.raises(SourceAuthoringWorkerError):
            guard.stage()
    finally:
        instance.cancel()
        assert instance.reap()


def test_stalled_ticket_reader_handoff_stops_within_one_second(monkeypatch):
    # A valid maximum source expands canonical JSON enough to fill the pipe.
    text = "\x01" * 8000
    request = body(text + "x" * (65536 - len(body(text))))
    assert len(request) == 65536
    instance, binding, request, _ = ready(
        monkeypatch, "stall_ticket_read", request=request
    )
    try:
        stage(instance, binding, request)
        started = time.monotonic()
        with pytest.raises(SourceAuthoringWorkerError):
            instance.handoff()
        assert time.monotonic() - started < 1.3
        assert instance.reap()
    finally:
        instance.cancel()
        assert instance.reap()


@pytest.mark.parametrize(
    "fault",
    [
        "ticket_swap",
        "secret_swap",
        "ticket_trailing",
        "secret_trailing",
        "ticket_partial",
        "secret_partial",
        "deadline_swap",
        "ticket_eof_missing",
        "secret_eof_missing",
    ],
)
def test_private_protocol_rejects_both_pipes_before_transport(monkeypatch, fault):
    from exitspec.source_authoring_live_ipc import LiveBinding

    profile = install_fake_profile(monkeypatch)
    request = body()
    binding = LiveBinding(**admission(request), generation="1" * 64, nonce="2" * 64)
    deadline = time.monotonic() + 1.5
    input_read, input_write = os.pipe()
    output_read, output_write = os.pipe()
    secret_read, secret_write = os.pipe()
    called, failed = [], []
    monkeypatch.setattr(
        worker, "_post_exact", lambda *args, **kwargs: called.append(True)
    )

    def child():
        try:
            worker._run_protocol(profile, input_read, output_write)
        except (SourceAuthoringWorkerError, OSError):
            failed.append(True)
        finally:
            os.close(input_read)
            os.close(output_write)

    thread = threading.Thread(target=child)
    thread.start()
    try:
        for fd in (input_write, output_read, secret_write):
            os.set_blocking(fd, False)
        write_wire(
            input_write,
            encode_live_frame(
                "PREPARE", binding, b"", deadline=deadline, credential_fd=secret_read
            ),
            deadline=deadline,
        )
        read_live_frame(output_read, event="READY_NO_SEND", deadline=deadline)
        assert called == []
        wrong = dataclasses.replace(binding, credential_generation=2)
        ticket = encode_live_frame(
            "SEND_TICKET",
            wrong if fault == "ticket_swap" else binding,
            request,
            deadline=deadline,
        )
        secret = encode_live_frame(
            "CREDENTIAL",
            wrong if fault == "secret_swap" else binding,
            b"SYNTHETIC-KEY",
            deadline=deadline + 0.01 if fault == "deadline_swap" else deadline,
        )
        if fault == "ticket_trailing":
            ticket += b"x"
        if fault == "secret_trailing":
            secret += b"x"
        if fault == "ticket_partial":
            ticket = ticket[:-1]
        if fault == "secret_partial":
            secret = secret[:-1]
        write_wire(input_write, ticket, deadline=deadline)
        if fault != "ticket_eof_missing":
            os.close(input_write)
            input_write = None
        try:
            write_wire(secret_write, secret, deadline=deadline)
        except BrokenPipeError:
            pass
        if fault != "secret_eof_missing":
            os.close(secret_write)
            secret_write = None
        thread.join(2)
        assert not thread.is_alive() and failed == [True] and called == []
        require_eof(output_read, deadline=time.monotonic() + 0.1)
    finally:
        for fd in (input_write, secret_write, output_read):
            if fd is not None:
                os.close(fd)
        thread.join(2)


@pytest.mark.parametrize("observed", [False, True])
def test_single_cleanup_owner_bounded_term_kill_and_unknown_slot(monkeypatch, observed):
    instance = supervisors._BoundedLiveSupervisor(lease=bound_lease(monkeypatch))
    actions = []

    class Process:
        stdin = io.BytesIO()
        stdout = io.BytesIO()
        returncode = None

        def poll(self):
            return self.returncode

        def terminate(self):
            actions.append(("term", threading.get_ident()))

        def kill(self):
            actions.append(("kill", threading.get_ident()))

        def wait(self, *, timeout):
            actions.append(("wait", threading.get_ident(), timeout))
            if observed and any(action[0] == "kill" for action in actions):
                self.returncode = -9
                return -9
            raise subprocess.TimeoutExpired("fixed-worker", timeout)

    instance._process = Process()
    instance._reserved = True
    instance._state = "READY_NO_SEND"
    instance._deadline = time.monotonic() + 1
    instance.cancel()
    watcher = threading.Thread(target=instance._watch_live)
    watcher.start()
    for _ in range(5):
        instance.cancel()  # Concurrent callers request cancellation only.
    watcher.join(1)
    assert not watcher.is_alive()
    assert [action[0] for action in actions] == ["term", "wait", "kill", "wait"]
    assert len({action[1] for action in actions}) == 1
    assert 0 <= actions[1][2] <= 0.25 and 0 <= actions[3][2] <= 0.75
    assert instance.reap(timeout=0) is observed
    assert instance.slot_occupied is (not observed)
    assert instance._process.stdin.closed and instance._process.stdout.closed
    with pytest.raises(SourceAuthoringWorkerError):
        instance.prepare(admission(body()), time.monotonic() + 1)


def test_startup_failure_closes_both_inherited_pipe_ends(monkeypatch):
    instance = supervisors._BoundedLiveSupervisor(lease=bound_lease(monkeypatch))
    inherited = []

    def fail(self, fd):
        inherited.append(fd)
        raise OSError("PRIVATE-MARKER")

    monkeypatch.setattr(supervisors._BoundedLiveSupervisor, "_spawn", fail)
    with pytest.raises(SourceAuthoringWorkerError) as error:
        instance.prepare(admission(body()), time.monotonic() + 1)
    assert "PRIVATE-MARKER" not in str(error.value)
    assert instance.reap(timeout=0) and not instance.slot_occupied
    assert instance._credential_writer.closed
    with pytest.raises(OSError):
        os.fstat(inherited[0])


def test_partial_nonblocking_writes_preserve_exact_wire_and_chunk_cap(monkeypatch):
    instance = supervisors._BoundedLiveSupervisor(lease=bound_lease(monkeypatch))
    read_fd, write_fd = os.pipe()
    stream = os.fdopen(write_fd, "wb", buffering=0)
    sizes, captured = [], bytearray()
    call_count = 0

    def write(fd, chunk):
        nonlocal call_count
        call_count += 1
        sizes.append(len(chunk))
        if call_count % 3 == 0:
            raise BlockingIOError
        amount = min(37, len(chunk))
        captured.extend(chunk[:amount])
        return amount

    monkeypatch.setattr(supervisors.os, "write", write)
    wire = b"synthetic-wire-" * 500
    try:
        instance._write_parent(stream, wire, time.monotonic() + 1)
        assert bytes(captured) == wire
        assert max(sizes) <= 4096 and call_count > 100
    finally:
        stream.close()
        os.close(read_fd)


def test_prepared_live_profile_mismatch_fails_before_pipe(monkeypatch):
    instance = supervisors._BoundedLiveSupervisor(lease=bound_lease(monkeypatch))
    calls = []
    monkeypatch.setattr(os, "pipe", lambda: calls.append(True))
    fields = admission(body()) | {"profile_sha256": "0" * 64}
    with pytest.raises(SourceAuthoringWorkerError):
        instance.prepare(fields, time.monotonic() + 1)
    assert calls == [] and instance.state == "NEW"


@pytest.mark.parametrize(
    "channel,offset",
    [
        ("ticket", 1),
        ("ticket", 8),
        ("ticket", 4096),
        ("credential", 1),
        ("credential", 8),
        ("credential", 800),
    ],
)
def test_cancel_during_partial_handoff_never_completes_or_resends(
    monkeypatch, channel, offset
):
    instance, binding, request, launches = ready(monkeypatch)
    original = instance._write_parent
    partial = []

    def write(stream, wire, deadline):
        target = (
            instance._process.stdin
            if channel == "ticket"
            else instance._credential_writer
        )
        if stream is target:
            assert offset < len(wire)
            original(stream, wire[:offset], deadline)
            partial.append(offset)
            instance.cancel()
            raise SourceAuthoringWorkerError("worker_state")
        original(stream, wire, deadline)

    monkeypatch.setattr(instance, "_write_parent", write)
    try:
        stage(instance, binding, request)
        with pytest.raises(SourceAuthoringWorkerError):
            instance.handoff()
        assert partial == [offset] and len(launches) == 1
        assert instance.reap()
        assert instance._process.returncode != 0
        with pytest.raises(SourceAuthoringWorkerError):
            instance.collect()
    finally:
        instance.cancel()
        assert instance.reap()
