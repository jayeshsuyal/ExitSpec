"""One private worker slot; preparation never authorizes dispatch.

Owner order ends with this supervisor's lock. Only dispatch_guard/stage belongs
under owner locks. Handoff, collection and startup must run after they release.
Cancel only marks revocation; the already-running watchdog performs process
control outside all owner locks and retains the slot until observed exit.
"""

from __future__ import annotations

import hmac
import os
import secrets
import select
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from . import source_authoring_launch as _launch
from . import source_authoring_live_worker as _live_worker
from .source_authoring_ipc import (
    MAX_BODY_BYTES,
    MAX_REQUEST_WIRE,
    MAX_RESPONSE_BYTES,
    MAX_RESULT_WIRE,
    SourceAuthoringWorkerError,
    WorkerBinding,
    binding_from,
    body_digest,
    encode_frame,
    read_wire,
    require_eof,
    valid_deadline,
    write_wire,
)
from .source_authoring_live_ipc import (
    LiveBinding,
    check_deadline,
    encode_live_frame,
    live_binding_from,
    read_live_frame,
    validate_credential,
)
from .source_authoring_transport import decode_response, validate_request_body
from .source_authoring_worker import SCENARIOS, SYNTHETIC_MODE


@dataclass(frozen=True, repr=False)
class SendTicket:
    binding: WorkerBinding
    deadline: float
    wire: bytes


class _StageGuard:
    def __init__(self, owner, ticket):
        self._owner, self._ticket, self._active = owner, ticket, True

    def stage(self):
        if not self._active:
            raise SourceAuthoringWorkerError("worker_state")
        # All validation/allocation preceded the caller's final clock check.
        # Owner locks and the supervisor lock exclude cancellation at this D.
        self._owner._staged = self._ticket
        self._owner._state = "DISPATCH_AUTHORIZED"


class SourceAuthoringSupervisor:
    """Real composition remains unavailable at the synthetic-core checkpoint."""

    def __init__(self, *args, **kwargs):
        raise SourceAuthoringWorkerError("live_prerequisites_missing")


class SyntheticSourceAuthoringSupervisor:
    """Distinct non-network-capable implementation using the fixed real child."""

    def __init__(self, *, synthetic_response=b"{}", synthetic_scenario="success"):
        if (
            type(synthetic_response) is not bytes
            or len(synthetic_response) > MAX_BODY_BYTES
            or type(synthetic_scenario) is not str
            or synthetic_scenario not in SCENARIOS
        ):
            raise SourceAuthoringWorkerError("synthetic_profile")
        self._response, self._scenario = synthetic_response, synthetic_scenario
        self._lock = threading.RLock()
        self._state = "NEW"
        self._process = None
        self._binding = None
        self._deadline = None
        self._prepared = None
        self._staged = None
        self._reserved = False
        self._exit_observed = False
        self._cancelled = threading.Event()
        self._exited = threading.Event()
        self._error = None

    @property
    def slot_occupied(self):
        with self._lock:
            return self._reserved and not self._exit_observed

    @property
    def state(self):
        with self._lock:
            return self._state

    def prepare(self, binding, deadline):
        deadline = valid_deadline(deadline)
        if not 0 < deadline - time.monotonic() <= 30:
            raise SourceAuthoringWorkerError("worker_deadline")
        if type(binding) is not dict or set(binding) != {
            "epoch",
            "grant",
            "operation",
            "body_sha256",
            "profile_sha256",
        }:
            raise SourceAuthoringWorkerError()
        bound = binding_from(
            dict(binding, generation=secrets.token_hex(32), nonce=secrets.token_hex(32))
        )
        bootstrap = encode_frame(
            {
                "event": "prepare",
                "mode": SYNTHETIC_MODE,
                "binding": bound.fields(),
                "deadline": deadline,
                "scenario": self._scenario,
            },
            self._response,
            maximum_wire=MAX_REQUEST_WIRE,
        )
        with self._lock:
            if self._state != "NEW":
                raise SourceAuthoringWorkerError("worker_state")
            self._state, self._reserved = "STARTING", True
            self._binding, self._deadline = bound, deadline
            self._response = b""
        try:
            # sys.executable preserves the installed virtualenv (do not resolve
            # its symlink into a different interpreter/site-packages context).
            python = os.path.abspath(sys.executable)
            process = subprocess.Popen(
                [python, "-I", "-m", "exitspec.source_authoring_worker"],
                cwd=Path(__file__).resolve().parent,
                env={},
                close_fds=True,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                bufsize=0,
            )
            with self._lock:
                self._process = process
            os.set_blocking(process.stdin.fileno(), False)
            os.set_blocking(process.stdout.fileno(), False)
            threading.Thread(target=self._watch, daemon=True).start()
            write_wire(
                process.stdin.fileno(),
                bootstrap,
                deadline=min(deadline, time.monotonic() + 1),
            )
            ready, payload = read_wire(
                process.stdout.fileno(),
                maximum_wire=MAX_RESULT_WIRE,
                maximum_payload=0,
                deadline=min(deadline, time.monotonic() + 1),
            )
            if (
                ready
                != {
                    "event": "READY_NO_SEND",
                    "mode": SYNTHETIC_MODE,
                    "binding": bound.fields(),
                }
                or payload
            ):
                raise SourceAuthoringWorkerError()
            with self._lock:
                if self._cancelled.is_set() or time.monotonic() >= deadline:
                    raise SourceAuthoringWorkerError("worker_timeout")
                self._state = "READY_NO_SEND"
            return bound
        except Exception:  # noqa: BLE001 - never retain raw subprocess/private-input errors
            self.cancel()
            with self._lock:
                if self._process is None:
                    self._exit_observed = True
                    self._exited.set()
        raise SourceAuthoringWorkerError("worker_prepare")

    def prepare_ticket(self, binding, body):
        if (
            type(binding) is not WorkerBinding
            or type(body) is not bytes
            or not 1 <= len(body) <= MAX_BODY_BYTES
            or body_digest(body) != binding.body_sha256
        ):
            raise SourceAuthoringWorkerError("worker_ticket")
        with self._lock:
            if (
                self._state != "READY_NO_SEND"
                or self._prepared is not None
                or binding != self._binding
            ):
                raise SourceAuthoringWorkerError("worker_state")
            deadline = self._deadline
        wire = encode_frame(
            {
                "event": "send_ticket",
                "mode": SYNTHETIC_MODE,
                "binding": binding.fields(),
                "deadline": deadline,
            },
            body,
            maximum_wire=MAX_REQUEST_WIRE,
        )
        ticket = SendTicket(binding, deadline, wire)
        with self._lock:
            if (
                self._state != "READY_NO_SEND"
                or self._prepared is not None
                or self._cancelled.is_set()
            ):
                raise SourceAuthoringWorkerError("worker_state")
            self._prepared = ticket
        return ticket

    @contextmanager
    def dispatch_guard(self, ticket):
        with self._lock:
            if (
                self._state != "READY_NO_SEND"
                or ticket is not self._prepared
                or self._cancelled.is_set()
                or self._exit_observed
                or time.monotonic() >= self._deadline
            ):
                raise SourceAuthoringWorkerError("worker_state")
            guard = _StageGuard(self, ticket)
            try:
                yield guard
            finally:
                guard._active = False

    def handoff(self):
        with self._lock:
            if (
                self._state != "DISPATCH_AUTHORIZED"
                or self._staged is None
                or self._cancelled.is_set()
            ):
                raise SourceAuthoringWorkerError("worker_state")
            ticket, process = self._staged, self._process
            self._staged = None
            self._state = "HANDED_OFF"
        failed = False
        try:
            write_wire(
                process.stdin.fileno(),
                ticket.wire,
                deadline=min(ticket.deadline, time.monotonic() + 1),
            )
            process.stdin.close()
        except Exception:  # noqa: BLE001 - pipe failures cannot leak the detached payload
            failed = True
        if failed:
            self.cancel()
            raise SourceAuthoringWorkerError("worker_handoff")

    def collect(self):
        with self._lock:
            if self._state != "HANDED_OFF" or self._cancelled.is_set():
                raise SourceAuthoringWorkerError("worker_state")
            process, binding, deadline = self._process, self._binding, self._deadline
            self._state = "COLLECTING"
        error = None
        result = b""
        try:
            metadata, result = read_wire(
                process.stdout.fileno(),
                maximum_wire=MAX_RESULT_WIRE,
                maximum_payload=MAX_RESPONSE_BYTES,
                deadline=deadline,
            )
            if metadata != {
                "event": "result",
                "mode": SYNTHETIC_MODE,
                "binding": binding.fields(),
            }:
                raise SourceAuthoringWorkerError()
            require_eof(process.stdout.fileno(), deadline=deadline)
            if not self._exited.wait(max(0, deadline - time.monotonic())):
                raise SourceAuthoringWorkerError("worker_timeout")
            with self._lock:
                if (
                    self._cancelled.is_set()
                    or time.monotonic() >= deadline
                    or process.returncode != 0
                ):
                    raise SourceAuthoringWorkerError("worker_timeout")
                self._state = "RESULT_READY"
                self._prepared = None
            return result
        except SourceAuthoringWorkerError as exception:
            error = exception.code
        except Exception:  # noqa: BLE001 - discarded private transport diagnostics
            error = "worker_result"
        finally:
            if self._exit_observed:
                self._close_pipes(process)
        result = b""
        self.cancel()
        raise SourceAuthoringWorkerError(error)

    def cancel(self):
        # Safe under operation -> supervisor locks: this does not kill, wait,
        # write IPC, invoke callbacks or relinquish an unobserved process slot.
        with self._lock:
            if self._state == "RESULT_READY":
                return
            self._cancelled.set()
            self._state = "CANCELLED"
            self._prepared = self._staged = None
            self._response = b""

    def reap(self, timeout=1.0):
        """Wait outside owner locks; false retains the slot and bars another run."""
        if type(timeout) not in (int, float) or not 0 <= timeout <= 2:
            raise SourceAuthoringWorkerError("worker_deadline")
        observed = self._exited.wait(timeout)
        if observed and self._process is not None:
            self._close_pipes(self._process)
        return observed

    @staticmethod
    def _close_pipes(process):
        for stream in (process.stdin, process.stdout):
            try:
                stream.close()
            except (OSError, ValueError):
                pass

    def _watch(self):
        try:
            self._watch_process()
        except Exception:  # noqa: BLE001 - never print private process-control diagnostics
            # No observed exit means the slot stays occupied and future work is barred.
            self.cancel()

    def _watch_process(self):
        process = self._process
        while True:
            if process.poll() is not None:
                with self._lock:
                    self._exit_observed = True
                self._exited.set()
                return
            if (
                self._cancelled.wait(
                    min(0.01, max(0, self._deadline - time.monotonic()))
                )
                or time.monotonic() >= self._deadline
            ):
                self.cancel()
                try:
                    process.kill()
                    process.wait(timeout=1)
                except (OSError, subprocess.TimeoutExpired):
                    # Unobserved cleanup remains occupied permanently/fail-closed.
                    return
                with self._lock:
                    self._exit_observed = True
                self._exited.set()
                return


@dataclass(frozen=True, repr=False)
class _LiveTicket:
    binding: LiveBinding
    deadline: float
    wire: bytes


class _LiveStageGuard:
    def __init__(self, owner, ticket):
        self._owner, self._ticket, self._active = owner, ticket, True

    def stage(self):
        if not self._active or self._owner._state != "READY_NO_SEND":
            raise SourceAuthoringWorkerError("worker_state")
        self._owner._staged = self._ticket
        self._owner._state = "DISPATCH_AUTHORIZED"


class _BoundedLiveSupervisor:
    """Fixed live mechanism admitted only through a sealed issuer lease.

    The only successful admission currently lives in test monkeypatches.
    No public option, environment field or synthetic claim activates this class.
    D/F remain the operation owner's responsibility; this class cannot publish.
    """

    def __init__(self, *, lease=None):
        self._lease = lease
        self._check_launch()
        self._lock = threading.RLock()
        self._io_lock = threading.Lock()
        self._state = "NEW"
        self._process = None
        self._credential_writer = None
        self._binding = self._deadline = self._prepared = self._staged = None
        self._credential_wire = b""
        self._reserved = self._exit_observed = False
        self._reading = False
        self._cancelled = threading.Event()
        self._exited = threading.Event()

    def _check_launch(self, binding=None, credential=None):
        try:
            with _launch._lease_guard(self._lease) as record:
                if binding is not None and (
                    binding.epoch != record.epoch or binding.grant != record.grant
                    or binding.profile_sha256 != _live_worker.PROFILE_SHA256
                    or binding.launch_profile_sha256 != record.profile.launch_profile_sha256
                    or binding.code_revision != record.profile.code_revision
                    or binding.credential_generation != record.generation
                ):
                    raise _launch.SourceAuthoringLaunchError()
                if credential is not None and (type(credential) is not bytes
                    or not hmac.compare_digest(credential, record.credential)):
                    raise _launch.SourceAuthoringLaunchError()
        except _launch.SourceAuthoringLaunchError:
            raise SourceAuthoringWorkerError("live_prerequisites_missing") from None

    @property
    def slot_occupied(self):
        with self._lock:
            return self._reserved and not self._exit_observed

    @property
    def state(self):
        with self._lock:
            return self._state

    def _spawn(self, read_fd):
        return subprocess.Popen(
            [
                os.path.abspath(sys.executable),
                "-I",
                "-m",
                "exitspec.source_authoring_live_worker",
                *_launch._worker_approval_arguments(self._lease),
            ],
            cwd=Path(__file__).resolve().parent,
            env={},
            close_fds=True,
            pass_fds=(read_fd,),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
        )

    def prepare(self, binding, deadline):
        # No reuse/coercion of the synthetic WorkerBinding or synthetic grant.
        deadline = check_deadline(deadline, admission=True)
        expected = {
            "epoch",
            "grant",
            "operation",
            "body_sha256",
            "profile_sha256",
            "launch_profile_sha256",
            "credential_generation",
            "code_revision",
        }
        if type(binding) is not dict or set(binding) != expected:
            raise SourceAuthoringWorkerError()
        bound = live_binding_from(
            dict(binding, generation=secrets.token_hex(32), nonce=secrets.token_hex(32))
        )
        if bound.profile_sha256 != _live_worker.PROFILE_SHA256:
            raise SourceAuthoringWorkerError("worker_profile")
        self._check_launch(bound)
        with self._lock:
            if self._state != "NEW":
                raise SourceAuthoringWorkerError("worker_state")
            self._state, self._reserved = "STARTING", True
            self._binding, self._deadline = bound, deadline
            self._reading = True
        read_fd = write_fd = None
        watcher_started = False
        try:
            read_fd, write_fd = os.pipe()
            bootstrap = encode_live_frame(
                "PREPARE", bound, b"", deadline=deadline, credential_fd=read_fd
            )
            self._credential_writer = os.fdopen(write_fd, "wb", buffering=0)
            write_fd = None
            process = self._spawn(read_fd)
            with self._lock:
                self._process = process
            os.close(read_fd)
            read_fd = None
            for stream in (process.stdin, process.stdout, self._credential_writer):
                os.set_blocking(stream.fileno(), False)
            threading.Thread(target=self._watch_live, daemon=True).start()
            watcher_started = True
            ready_deadline = min(deadline, time.monotonic() + 1)
            self._write_parent(process.stdin, bootstrap, ready_deadline)
            ready, payload = read_live_frame(
                process.stdout.fileno(), event="READY_NO_SEND", deadline=ready_deadline
            )
            if live_binding_from(ready["binding"]) != bound or payload:
                raise SourceAuthoringWorkerError()
            with self._lock:
                if (
                    self._cancelled.is_set()
                    or time.monotonic() >= deadline
                    or self._exit_observed
                ):
                    raise SourceAuthoringWorkerError("worker_state")
                self._state = "READY_NO_SEND"
            return bound
        except Exception:  # noqa: BLE001 - no raw pipe, process or private metadata diagnostics
            self.cancel()
            if self._process is None:
                self._observe_exit()
            elif not watcher_started:
                # Startup failures still have exactly one cleanup owner.
                self._watch_live()
        finally:
            for fd in (read_fd, write_fd):
                if fd is not None:
                    os.close(fd)
            with self._lock:
                self._reading = False
            if self._cancelled.is_set():
                self.reap()
        raise SourceAuthoringWorkerError("worker_prepare")

    def prepare_ticket(self, binding, body, credential, *, credential_generation):
        if (
            type(binding) is not LiveBinding
            or type(body) is not bytes
            or type(credential_generation) is not int
            or credential_generation != binding.credential_generation
            or body_digest(body) != binding.body_sha256
        ):
            raise SourceAuthoringWorkerError("worker_ticket")
        validate_request_body(body)
        validate_credential(credential)
        self._check_launch(binding, credential)
        with self._lock:
            if (
                self._state != "READY_NO_SEND"
                or self._prepared is not None
                or binding != self._binding
                or self._cancelled.is_set()
            ):
                raise SourceAuthoringWorkerError("worker_state")
            deadline = self._deadline
        ticket = _LiveTicket(
            binding,
            deadline,
            encode_live_frame("SEND_TICKET", binding, body, deadline=deadline),
        )
        credential_wire = encode_live_frame(
            "CREDENTIAL", binding, credential, deadline=deadline
        )
        with self._lock:
            if (
                self._state != "READY_NO_SEND"
                or self._prepared is not None
                or self._cancelled.is_set()
                or time.monotonic() >= deadline
            ):
                raise SourceAuthoringWorkerError("worker_state")
            self._prepared, self._credential_wire = ticket, credential_wire
        return ticket

    @contextmanager
    def dispatch_guard(self, ticket):
        self._check_launch(self._binding)
        with self._lock:
            if (
                self._state != "READY_NO_SEND"
                or ticket is not self._prepared
                or self._cancelled.is_set()
                or self._exit_observed
                or time.monotonic() >= self._deadline
            ):
                raise SourceAuthoringWorkerError("worker_state")
            guard = _LiveStageGuard(self, ticket)
            try:
                yield guard
            finally:
                guard._active = False

    def _write_parent(self, stream, wire, deadline):
        offset = 0
        while offset < len(wire):
            if self._cancelled.is_set() or time.monotonic() >= deadline:
                raise SourceAuthoringWorkerError("worker_timeout")
            # Wait in short slices so cancellation bounds a stalled partial write.
            _, writable, _ = select.select(
                [], [stream], [], min(0.01, max(0, deadline - time.monotonic()))
            )
            if not writable:
                continue
            with self._io_lock:
                if (
                    self._cancelled.is_set()
                    or stream.closed
                    or time.monotonic() >= deadline
                ):
                    raise SourceAuthoringWorkerError("worker_state")
                try:
                    count = os.write(
                        stream.fileno(), memoryview(wire)[offset : offset + 4096]
                    )
                except BlockingIOError:
                    continue
            if count <= 0:
                raise SourceAuthoringWorkerError("worker_pipe")
            offset += count

    def _close_writer(self, stream):
        with self._io_lock:
            if stream is not None:
                try:
                    stream.close()
                except (OSError, ValueError):
                    pass

    def handoff(self):
        self._check_launch(self._binding)
        with self._lock:
            if (
                self._state != "DISPATCH_AUTHORIZED"
                or self._staged is None
                or self._cancelled.is_set()
            ):
                raise SourceAuthoringWorkerError("worker_state")
            ticket, credential_wire, process = (
                self._staged,
                self._credential_wire,
                self._process,
            )
            self._staged, self._credential_wire = None, b""
            self._state = "HANDED_OFF"
        failed = False
        try:
            handoff_deadline = min(ticket.deadline, time.monotonic() + 1)
            self._check_launch(ticket.binding)
            self._write_parent(process.stdin, ticket.wire, handoff_deadline)
            self._close_writer(process.stdin)
            # Rotation/revocation use cancel; no replacement generation is accepted.
            if self._cancelled.is_set():
                raise SourceAuthoringWorkerError("worker_state")
            self._check_launch(ticket.binding)
            self._write_parent(
                self._credential_writer, credential_wire, handoff_deadline
            )
        except Exception:  # noqa: BLE001 - detached body/credential must never enter diagnostics
            failed = True
        finally:
            credential_wire = b""
            self._close_writer(process.stdin)
            self._close_writer(self._credential_writer)
        if failed:
            self.cancel()
            raise SourceAuthoringWorkerError("worker_handoff")

    def collect(self):
        self._check_launch(self._binding)
        with self._lock:
            if self._state != "HANDED_OFF" or self._cancelled.is_set():
                raise SourceAuthoringWorkerError("worker_state")
            self._state, self._reading = "COLLECTING", True
            process, binding, deadline = self._process, self._binding, self._deadline
        result = None
        try:
            metadata, raw = read_live_frame(
                process.stdout.fileno(), event="RESULT", deadline=deadline
            )
            if live_binding_from(metadata["binding"]) != binding:
                raise SourceAuthoringWorkerError()
            require_eof(process.stdout.fileno(), deadline=deadline)
            if _launch._display_mode(self._lease) != "DEMO_FIREWORKS":
                decode_response(raw)
            if not self._exited.wait(max(0, deadline - time.monotonic())):
                raise SourceAuthoringWorkerError("worker_timeout")
            with self._lock:
                if (
                    self._cancelled.is_set()
                    or process.returncode != 0
                    or time.monotonic() >= deadline
                ):
                    raise SourceAuthoringWorkerError("worker_state")
                self._state, self._prepared = "RESULT_READY", None
                result = raw
        except Exception:  # noqa: BLE001 - no raw result or transport diagnostics
            self.cancel()
        finally:
            with self._lock:
                self._reading = False
            # Local descriptors belong to the finished reader even when the
            # watchdog cannot observe OS exit. The worker slot stays fenced.
            self._close_handles()
        if result is None:
            raise SourceAuthoringWorkerError("worker_result")
        return result

    def cancel(self):
        # Never wait, invoke callbacks or perform I/O while owner locks may be held.
        # RESULT_READY is also revoked: a later caller must still pass existing F.
        with self._lock:
            self._cancelled.set()
            self._state = "CANCELLED"
            self._prepared = self._staged = None
            self._credential_wire = b""

    def reap(self, timeout=1.0):
        if type(timeout) not in (int, float) or not 0 <= timeout <= 1.0:
            raise SourceAuthoringWorkerError("worker_deadline")
        observed = self._exited.wait(timeout)
        # Cancelled startup must finish deferred cleanup after its reader
        # unwinds; a harmless READY timeout must leave active pipes open.
        if observed or self._cancelled.is_set():
            self._close_handles()
        return observed

    def _close_handles(self):
        process = self._process
        self._close_writer(self._credential_writer)
        if process is not None:
            self._close_writer(process.stdin)
            with self._lock:
                if not self._reading:
                    self._close_writer(process.stdout)

    def _observe_exit(self):
        with self._lock:
            self._exit_observed = True
        self._exited.set()

    def _watch_live(self):
        try:
            self._watch_live_process()
        except Exception:  # noqa: BLE001 - unobserved cleanup retains the slot, diagnostics stay private
            self.cancel()
        finally:
            # Cancelling closes parent pipes even if OS exit is unobserved.
            if self._cancelled.is_set():
                self._close_handles()

    def _watch_live_process(self):
        process = self._process
        while True:
            if process.poll() is not None:
                self._observe_exit()
                return
            if (
                self._cancelled.wait(
                    min(0.01, max(0, self._deadline - time.monotonic()))
                )
                or time.monotonic() >= self._deadline
            ):
                self.cancel()
                cleanup_deadline = time.monotonic() + 1
                try:
                    process.terminate()
                    process.wait(
                        timeout=min(0.25, max(0, cleanup_deadline - time.monotonic()))
                    )
                except (OSError, subprocess.TimeoutExpired):
                    try:
                        process.kill()
                        process.wait(
                            timeout=min(
                                0.75, max(0, cleanup_deadline - time.monotonic())
                            )
                        )
                    except (OSError, subprocess.TimeoutExpired):
                        return
                self._observe_exit()
                return
