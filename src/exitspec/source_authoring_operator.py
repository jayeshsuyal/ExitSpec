"""Admission-first local SourceNeutral + Zoom operator; installed registry empty."""
from __future__ import annotations

import argparse
import os
import select
import sys
import termios
import threading
import time
from pathlib import Path

from . import source_authoring_launch as launch
from .poc_source_demo import serve_source_neutral_demo
from .source_authoring_live_ipc import validate_credential
from .zoom_live_operator import _pair_zoom_in_server


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise launch.SourceAuthoringLaunchError()

    def exit(self, status=0, message=None):
        raise launch.SourceAuthoringLaunchError()


def _arguments(argv):
    parser = _Parser(add_help=False, allow_abbrev=False)
    parser.add_argument("--approval-id", required=True)
    parser.add_argument("--approval-file", required=True)
    parser.add_argument("--approval-sha256", required=True)
    parser.add_argument("--enroll-metadata", action="store_true")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--output-root", default="/tmp/exitspec-source-authoring")
    args = parser.parse_args(argv)
    path = Path(args.output_root)
    if (not 1 <= args.port <= 65535 or not path.is_absolute() or ".." in path.parts
        or len(args.output_root) > 4096 or any(ord(c) < 32 for c in args.output_root)):
        raise launch.SourceAuthoringLaunchError()
    return args, path


def _read_tty_line(prompt, *, maximum=4096, wait_for_input=False):
    """One unchanged bounded line; no stdin/getpass/environment/file fallback."""
    descriptor, original = None, None
    try:
        descriptor = os.open("/dev/tty", os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        if not os.isatty(descriptor):
            raise launch.SourceAuthoringLaunchError()
        original = termios.tcgetattr(descriptor)
        settings = list(original)
        settings[6] = list(original[6])
        hidden_flags = termios.ECHO | termios.ECHONL | termios.ICANON | termios.IEXTEN
        settings[3] &= ~hidden_flags
        # Preserve input bytes, including CR/high bits/control bytes, for strict
        # validation. Keep ISIG so the operator can still interrupt with Ctrl-C.
        transform_flags = 0
        for name in ("ICRNL", "INLCR", "IGNCR", "ISTRIP", "INPCK", "IGNPAR",
                     "PARMRK", "IXON", "IXOFF", "IXANY", "IUCLC", "IGNBRK"):
            transform_flags |= getattr(termios, name, 0)
        settings[0] &= ~transform_flags
        settings[2] = (settings[2] & ~(termios.CSIZE | termios.PARENB)) | termios.CS8
        settings[6][termios.VMIN], settings[6][termios.VTIME] = 0, 0
        termios.tcsetattr(descriptor, termios.TCSANOW, settings)
        applied = termios.tcgetattr(descriptor)
        if (applied[3] & hidden_flags or applied[0] & transform_flags
            or applied[2] & termios.CSIZE != termios.CS8 or applied[2] & termios.PARENB):
            raise launch.SourceAuthoringLaunchError()
        deadline = time.monotonic() + 30
        wire, offset = prompt.encode("utf-8"), 0
        while offset < len(wire):
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([], [descriptor], [], remaining)[1]:
                raise launch.SourceAuthoringLaunchError()
            count = os.write(descriptor, wire[offset:])
            if count <= 0:
                raise launch.SourceAuthoringLaunchError()
            offset += count
        value = bytearray()
        # Only the deliberate-stop prompt can idle. Each select remains bounded;
        # the ordinary 30-second line deadline starts when stop input arrives.
        # This does not renew the profile, launch, consent or operation budgets.
        if wait_for_input:
            deadline = None
        while len(value) <= maximum:
            remaining = 30 if deadline is None else deadline - time.monotonic()
            if remaining <= 0:
                raise launch.SourceAuthoringLaunchError()
            ready = select.select([descriptor], [], [], remaining)[0]
            if not ready:
                if deadline is None:
                    continue
                raise launch.SourceAuthoringLaunchError()
            if deadline is None:
                deadline = time.monotonic() + 30
            chunk = os.read(descriptor, 1)
            if not chunk or time.monotonic() >= deadline:
                raise launch.SourceAuthoringLaunchError()
            if chunk == b"\n":
                return bytes(value)
            value.extend(chunk)
        raise launch.SourceAuthoringLaunchError()
    except (OSError, ValueError, termios.error, KeyboardInterrupt, EOFError):
        raise launch.SourceAuthoringLaunchError() from None
    finally:
        if descriptor is not None:
            cleanup_failed = False
            try:
                try:
                    # Discard queued secret tails with one kernel operation,
                    # before restoring echo; never drain an unbounded stream.
                    termios.tcflush(descriptor, termios.TCIFLUSH)
                except (OSError, termios.error):
                    cleanup_failed = True
                finally:
                    if original is not None:
                        try:
                            termios.tcsetattr(descriptor, termios.TCSANOW, original)
                        except (OSError, termios.error):
                            cleanup_failed = True
            finally:
                try:
                    os.close(descriptor)
                except OSError:
                    cleanup_failed = True
            if cleanup_failed:
                raise launch.SourceAuthoringLaunchError() from None


def _read_credential_tty():
    credential = _read_tty_line("Fireworks key (input hidden): ")
    try:
        validate_credential(credential)
    except ValueError:
        raise launch.SourceAuthoringLaunchError() from None
    return credential


def _read_text_tty(prompt):
    try:
        value = _read_tty_line(prompt)
        if any(byte < 32 or byte > 126 for byte in value):
            raise ValueError()
        return value.decode("ascii")
    except (ValueError, UnicodeError):
        raise launch.SourceAuthoringLaunchError() from None


def _wait_for_stop_tty():
    _read_tty_line("Submit an empty line with Ctrl-J to revoke this launch, pairing and server: ",
                   maximum=0, wait_for_input=True)


def main(argv=None):
    server = thread = handle = None
    credential = b""
    try:
        # Nothing, including argument-dependent paths or terminal input, can
        # enable an installed launch while this single registry is empty.
        if not launch._QUALIFIED_SERVING_CONTRACTS:
            raise launch.SourceAuthoringLaunchError()
        args, output_root = _arguments(argv)
        admitted = launch._admit_operator_profile(
            args.approval_id, approval_file=args.approval_file,
            expected_sha256=args.approval_sha256,
        )
        profile = launch._admitted_profile(admitted)
        print("Source authoring: one exact-source Fireworks attempt per consent, 30 seconds,")
        print("8192 input / 2000 output tokens, $0.01 per claim, ten claims / $0.10 per launch.")
        print("No regional guarantee. Zoom capture requires separate prerequisites and consent.")
        print("Terminal input is hidden. Submit each response with Ctrl-J (LF); CR is refused.")
        if _read_text_tty("Type APPROVED for this admitted Fireworks launch: ") != "APPROVED":
            raise launch.SourceAuthoringLaunchError()
        launch._verify_code_and_artifacts(profile)
        credential = _read_credential_tty()
        handle = launch._issue_live_launch(admitted, credential)
        credential = b""
        server = serve_source_neutral_demo(
            port=args.port, evidence_artifact_root=output_root, source_authoring_launch=handle,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        print(f"Open http://127.0.0.1:{server.server_port}/app to create the meeting POC.")
        if not _pair_zoom_in_server(server, profile.code_revision, read_text=_read_text_tty,
                                    secret=_read_text_tty, enroll_metadata=args.enroll_metadata):
            raise launch.SourceAuthoringLaunchError()
        print("Capture in this POC's Zoom panel. Then inspect its exact source on the authoring page.")
        print("Fireworks Run requires separate exact-source acknowledgment; Zoom pairing grants none.")
        _wait_for_stop_tty()
        return 0
    except Exception:  # noqa: BLE001 - no argv, source, credential or private traceback
        print("Operator launch unavailable or stopped; private details discarded.")
        return 2
    except KeyboardInterrupt:
        print("Operator launch stopped.")
        return 2
    finally:
        credential = b""
        try:
            if handle is not None:
                handle.revoke()
        finally:
            if server is not None:
                try:
                    if thread is not None and thread.is_alive():
                        server.shutdown()
                        thread.join(timeout=2)
                finally:
                    server.server_close()


if __name__ == "__main__":
    sys.exit(main())
