"""One private worker slot; preparation never authorizes dispatch.

Owner order ends with this supervisor's lock. Only dispatch_guard/stage belongs
under owner locks. Handoff, collection and startup must run after they release.
Cancel only marks revocation; the already-running watchdog performs process
control outside all owner locks and retains the slot until observed exit.
"""

from __future__ import annotations

import os
import secrets
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

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
