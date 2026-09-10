"""Disabled installed live entrypoint; private protocol is exercised by fake tests.

Production admission refuses before descriptor access while the central
qualified-profile registry is empty. No command-line or environment admission.
"""

from __future__ import annotations

import os
import stat
import sys
import time

from . import source_authoring_launch as _launch
from .source_authoring_ipc import (
    SourceAuthoringWorkerError,
    body_digest,
    require_eof,
    write_wire,
)
from .source_authoring_live_ipc import (
    check_deadline,
    encode_live_frame,
    live_binding_from,
    read_live_frame,
    validate_credential,
)
from .source_authoring_policy import PROFILE_SHA256
from .source_authoring_transport import _post_exact, validate_request_body


def _require_production_profile():
    try:
        return _launch._require_worker_profile()
    except _launch.SourceAuthoringLaunchError:
        raise SourceAuthoringWorkerError("live_prerequisites_missing") from None


def run():
    profile = _require_production_profile()
    _run_protocol(profile)


def _run_protocol(profile, input_fd=0, output_fd=1):
    """Private mechanism only. Tests replace transport inside a test-only child."""
    _launch._require_profile(profile)
    credential_fd = None
    credential = b""
    try:
        os.set_blocking(input_fd, False)
        os.set_blocking(output_fd, False)
        metadata, _ = read_live_frame(
            input_fd, event="PREPARE", deadline=time.monotonic() + 1
        )
        binding = live_binding_from(metadata["binding"])
        if binding.profile_sha256 != PROFILE_SHA256:
            raise SourceAuthoringWorkerError("worker_profile")
        _launch._check_worker_binding(profile, binding)
        deadline = check_deadline(metadata["deadline"], admission=True)
        candidate_fd = metadata["credential_fd"]
        if candidate_fd in {input_fd, output_fd} or not stat.S_ISFIFO(
            os.fstat(candidate_fd).st_mode
        ):
            raise SourceAuthoringWorkerError()
        credential_fd = candidate_fd
        os.set_blocking(credential_fd, False)
        write_wire(
            output_fd,
            encode_live_frame("READY_NO_SEND", binding, b""),
            deadline=deadline,
        )
        ticket, body = read_live_frame(input_fd, event="SEND_TICKET", deadline=deadline)
        if (
            live_binding_from(ticket["binding"]) != binding
            or ticket["deadline"] != deadline
            or body_digest(body) != binding.body_sha256
        ):
            raise SourceAuthoringWorkerError()
        require_eof(input_fd, deadline=deadline)
        validate_request_body(body)
        secret_metadata, credential = read_live_frame(
            credential_fd, event="CREDENTIAL", deadline=deadline
        )
        if (
            live_binding_from(secret_metadata["binding"]) != binding
            or secret_metadata["deadline"] != deadline
        ):
            raise SourceAuthoringWorkerError()
        validate_credential(credential)
        require_eof(credential_fd, deadline=deadline)
        os.close(credential_fd)
        credential_fd = None
        check_deadline(deadline, admission=True)
        response = _post_exact(body, credential, deadline=deadline)
        credential = b""
        write_wire(
            output_fd, encode_live_frame("RESULT", binding, response), deadline=deadline
        )
    finally:
        credential = b""  # No memory-zeroization claim.
        if credential_fd is not None:
            os.close(credential_fd)


def main():
    try:
        run()
    except Exception:  # noqa: BLE001 - no body, binding, key, provider error or traceback on stderr
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
