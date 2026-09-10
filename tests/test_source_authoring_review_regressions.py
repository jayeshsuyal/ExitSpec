"""Deterministic independent-review regressions; no real network or credentials."""

import http.client
import io
import json
import subprocess
import time

import pytest

from exitspec import source_authoring_live_worker as worker
from exitspec import source_authoring_supervisor as supervisors
from exitspec import source_authoring_transport as transport
from exitspec.canonical import canonical_json_bytes
from exitspec.source_authoring_ipc import SourceAuthoringWorkerError
from exitspec.source_authoring_pins import REQUEST_PROFILE_JSON


class FakeReadPipe(io.BytesIO):
    def fileno(self):
        # The reader below is an in-memory fake and never reads this number.
        return 123


@pytest.mark.parametrize("exit_observed", [False, True])
def test_active_reader_closes_pipe_even_when_child_exit_is_unobserved(
    monkeypatch, exit_observed
):
    monkeypatch.setattr(worker, "_require_production_profile", lambda: None)
    instance = supervisors._BoundedLiveSupervisor()

    class FakeProcess:
        def __init__(self):
            self.stdin = io.BytesIO()
            self.stdout = FakeReadPipe()
            self.returncode = None
            self.actions = []

        def poll(self):
            return self.returncode

        def terminate(self):
            self.actions.append("TERM")

        def kill(self):
            self.actions.append("KILL")

        def wait(self, *, timeout):
            assert 0 <= timeout <= 0.75
            if exit_observed and "KILL" in self.actions:
                self.returncode = -9
                return -9
            raise subprocess.TimeoutExpired("fixed-test-child", timeout)

    process = FakeProcess()
    instance._process = process
    instance._credential_writer = io.BytesIO()
    instance._state = "HANDED_OFF"
    instance._reserved = True
    instance._deadline = time.monotonic() + 1

    def cancelled_read(fd, *, event, deadline):
        assert fd == 123 and event == "RESULT" and instance._reading
        # Deterministically model watchdog cleanup while collect owns stdout.
        instance.cancel()
        instance._watch_live()
        assert process.actions == ["TERM", "KILL"]
        # The active reader keeps its FD until it unwinds; that is legitimate.
        assert not process.stdout.closed
        raise SourceAuthoringWorkerError("worker_timeout")

    monkeypatch.setattr(supervisors, "read_live_frame", cancelled_read)
    try:
        with pytest.raises(SourceAuthoringWorkerError):
            instance.collect()
        assert not instance._reading
        assert instance.slot_occupied is (not exit_observed)
        assert instance.reap(timeout=0) is exit_observed
        assert process.stdin.closed and instance._credential_writer.closed
        # Local descriptor cleanup must not depend on observed OS child exit.
        assert process.stdout.closed
    finally:
        # Probe does not leak its own in-memory fixture or change candidate code.
        process.stdin.close()
        process.stdout.close()
        instance._credential_writer.close()


@pytest.mark.parametrize("exit_observed", [False, True])
def test_prepare_startup_failure_closes_stdout_after_reader_unwinds(
    monkeypatch, exit_observed
):
    monkeypatch.setattr(worker, "_require_production_profile", lambda: None)
    instance = supervisors._BoundedLiveSupervisor()

    class StartupFaultProcess:
        # stdin.fileno() raises, modeling failure after spawn and before watcher
        # startup. All process actions remain fake; only the parent pipe is real.
        stdin = io.BytesIO()
        stdout = io.BytesIO()
        returncode = None

        def poll(self):
            return None

        def terminate(self):
            pass

        def kill(self):
            pass

        def wait(self, *, timeout):
            if exit_observed:
                self.returncode = -15
                return -15
            raise subprocess.TimeoutExpired("fixed-test-child", timeout)

    process = StartupFaultProcess()
    monkeypatch.setattr(instance, "_spawn", lambda _fd: process)
    admission = {
        "epoch": "a" * 64,
        "grant": "b" * 64,
        "operation": "operation_test",
        "body_sha256": "c" * 64,
        "profile_sha256": worker.PROFILE_SHA256,
        "launch_profile_sha256": "d" * 64,
        "credential_generation": 1,
        "code_revision": "e" * 40,
    }
    try:
        with pytest.raises(SourceAuthoringWorkerError):
            instance.prepare(admission, time.monotonic() + 1.5)
        assert not instance._reading
        assert instance.slot_occupied is (not exit_observed)
        assert instance.reap(timeout=0) is exit_observed
        assert instance._credential_writer.closed and process.stdin.closed
        assert process.stdout.closed
    finally:
        process.stdin.close()
        process.stdout.close()
        if instance._credential_writer is not None:
            instance._credential_writer.close()


def envelope():
    return {
        "id": "sidecar-synthetic-only",
        "object": "chat.completion",
        "created": 0,
        "model": "accounts/fireworks/models/deepseek-v4-flash-0731",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": '{"synthetic":true}'},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
    }


def encode(value):
    return json.dumps(value, separators=(",", ":")).encode()


def request_value(text="synthetic source"):
    value = json.loads(REQUEST_PROFILE_JSON)["body_template"]
    value["messages"][1]["content"] = (
        "Untrusted redacted source JSON follows:\n"
        + canonical_json_bytes({"text": text}).decode()
    )
    return value


class Clock:
    def __init__(self):
        self.now = 100.0

    def monotonic(self):
        return self.now


class MemoryFile(io.BytesIO):
    def __init__(self, raw, sock, clock, advance):
        super().__init__(raw)
        self.sock = sock
        self.clock = clock
        self.advance = advance
        self.read_observations = []
        self.wire_read = 0

    def read1(self, size=-1):
        self.read_observations.append((self.clock.now, self.sock.timeout, size))
        result = super().read1(min(size, 64))
        self.wire_read += len(result)
        if result:
            self.clock.now += self.advance
        return result

    def read(self, size=-1):
        result = super().read(size)
        self.wire_read += len(result)
        return result

    def readline(self, size=-1):
        result = super().readline(size)
        self.wire_read += len(result)
        return result


class MemorySocket:
    """Only bytes and bookkeeping; does not inherit from or create a socket."""

    def __init__(self, raw, timeout, clock, advance):
        self.timeout = timeout
        self.sent = []
        self.timeout_updates = []
        self.closed = False
        self.file = MemoryFile(raw, self, clock, advance)

    def makefile(self, mode):
        assert mode == "rb"
        return self.file

    def settimeout(self, timeout):
        self.timeout = timeout
        self.timeout_updates.append(timeout)

    def sendall(self, data):
        self.sent.append(bytes(data))

    def close(self):
        self.closed = True


def install_wire(monkeypatch, wire, *, advance=0.0):
    """Keep stdlib request serialization/getresponse/framing; replace only IO."""
    clock = Clock()
    monkeypatch.setattr(transport.time, "monotonic", clock.monotonic)
    record = {}

    class OfflineHTTPSConnection(http.client.HTTPConnection):
        # Retain HTTPS Host-header semantics without constructing a TLS socket.
        default_port = 443

    def factory(host, port, *, timeout):
        assert (host, port) == ("api.fireworks.ai", 443)
        sock = MemorySocket(wire, timeout, clock, advance)
        connection = OfflineHTTPSConnection(host, port, timeout=timeout)
        connection.sock = sock
        record.update(socket=sock, connection=connection, clock=clock)
        return connection

    monkeypatch.setattr(transport.http.client, "HTTPSConnection", factory)
    return record


def http_wire(raw, *, close=False, chunked=False, trailer=b"\r\n"):
    headers = b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
    if close:
        headers += b"Connection: close\r\n"
    if chunked:
        return (
            headers
            + b"Transfer-Encoding: chunked\r\n\r\n"
            + f"{len(raw):x}\r\n".encode()
            + raw
            + b"\r\n0\r\n"
            + trailer
        )
    return headers + f"Content-Length: {len(raw)}\r\n\r\n".encode() + raw


def invoke():
    return transport._post_exact(
        canonical_json_bytes(request_value()), b"SYNTHETIC-KEY", deadline=101.0
    )


@pytest.mark.parametrize(
    "close,chunked", [(False, False), (True, False), (False, True)]
)
def test_real_http_parser_controls_and_exact_request_bytes(monkeypatch, close, chunked):
    raw = encode(envelope())
    record = install_wire(monkeypatch, http_wire(raw, close=close, chunked=chunked))
    monkeypatch.setenv("HTTPS_PROXY", "http://forbidden.invalid")
    monkeypatch.setenv("ALL_PROXY", "http://forbidden.invalid")
    assert invoke() == raw
    sent = b"".join(record["socket"].sent)
    head, body = sent.split(b"\r\n\r\n", 1)
    lines = head.split(b"\r\n")
    assert lines[0] == b"POST /inference/v1/chat/completions HTTP/1.1"
    assert body == canonical_json_bytes(request_value())
    assert dict(line.split(b": ", 1) for line in lines[1:]) == {
        b"Host": b"api.fireworks.ai",
        b"Content-Length": str(len(body)).encode(),
        b"Authorization": b"Bearer SYNTHETIC-KEY",
        b"Accept": b"application/json",
        b"Content-Type": b"application/json",
        b"Accept-Encoding": b"identity",
        b"User-Agent": b"ExitSpec source authoring r2",
    }
    assert record["socket"].closed and record["socket"].file.closed


@pytest.mark.parametrize(
    "close", [False, True], ids=["persistent-control", "connection-close"]
)
def test_regression_body_reads_receive_remaining_socket_timeout(monkeypatch, close):
    raw = encode(envelope())
    record = install_wire(monkeypatch, http_wire(raw, close=close), advance=0.05)
    assert invoke() == raw
    observations = record["socket"].file.read_observations
    assert len(observations) > 1
    print(f"close={close}; (read time, socket timeout, requested bytes)={observations}")
    for started, actual_timeout, _ in observations:
        assert actual_timeout <= 101.0 - started + 1e-9, (
            f"read at {started}: socket timeout {actual_timeout}, "
            f"remaining deadline {101.0 - started}"
        )


def test_harmless_ready_reap_timeout_keeps_preparation_pipes_open(monkeypatch):
    monkeypatch.setattr(worker, "_require_production_profile", lambda: None)
    instance = supervisors._BoundedLiveSupervisor()

    class PreparedProcess:
        stdin = io.BytesIO()
        stdout = io.BytesIO()

    instance._process = PreparedProcess()
    instance._credential_writer = io.BytesIO()
    instance._state = "READY_NO_SEND"
    instance._reserved = True
    streams = (
        instance._process.stdin,
        instance._process.stdout,
        instance._credential_writer,
    )
    try:
        assert instance.reap(timeout=0) is False
        assert instance.slot_occupied and instance.state == "READY_NO_SEND"
        assert all(not stream.closed for stream in streams)
    finally:
        for stream in streams:
            stream.close()


def test_completed_closing_response_does_not_reuse_closed_body_socket(monkeypatch):
    raw = encode(envelope())
    record = install_wire(monkeypatch, http_wire(raw, close=True), advance=0.05)
    original = MemorySocket.settimeout

    def timeout(sock, value):
        if sock.closed and sock.file.closed:
            raise OSError("closed synthetic descriptor")
        original(sock, value)

    monkeypatch.setattr(MemorySocket, "settimeout", timeout)
    assert invoke() == raw
    sock = record["socket"]
    assert sock.closed and sock.file.closed
