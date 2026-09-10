"""Explicit local operator bootstrap; never invoked by an HTTP request."""

from __future__ import annotations

import argparse
import getpass
import subprocess
import threading
import time
from pathlib import Path

from .web import serve_demo
from .zoom_live_runtime import ZoomEnrollmentSettings, ZoomLiveError, ZoomLiveSettings


def _pair_zoom_in_server(server, revision, *, read_text=None, secret=None, write=None, enroll_metadata=False):
    """Pair this exact server/runtime; Zoom prerequisites and consent stay separate."""
    if enroll_metadata:
        from .source_authoring_operator import _read_text_tty
        read_text = _read_text_tty if read_text is None else read_text
        secret = _read_text_tty if secret is None else secret
    read_text = input if read_text is None else read_text
    secret = getpass.getpass if secret is None else secret
    write = print if write is None else write
    write(
        "Create a meeting POC in /app, then pair its exact ID here. No capture has started."
    )
    write(
        "Requires prior account-owner approval, credential rotation, privacy/custody consent,"
    )
    write(
        "two-person synthetic requirements only, and approved RTMS credits/rates and spending ceiling."
    )
    write(
        "This runner bounds time; it cannot meter provider billing. Approve a window within your ceiling."
    )
    write(
        "Zoom pairing grants no Fireworks egress consent. Configure the approved Zoom app callback separately."
    )
    if (
        read_text("Type APPROVED to attest all prerequisites for this fresh run: ")
        != "APPROVED"
    ):
        return False
    poc_id = read_text("Exact POC ID: ").strip()
    receipts = None
    if enroll_metadata:
        write("Metadata enrollment opens callback/signaling before browser capture consent; no media is opened.")
        write("Two known consenting people only. Association is human-attested, not authenticated Zoom identity.")
        if read_text("Type APPROVED for both people's separate metadata custody/consent: ") != "APPROVED":
            return False
        receipts = (read_text("First person's content-free metadata consent receipt: ").strip(),
                    read_text("Second person's content-free metadata consent receipt: ").strip())
    settings = (ZoomEnrollmentSettings if enroll_metadata else ZoomLiveSettings)(
        client_id=secret("Zoom client ID: "),
        client_secret=secret("Zoom client secret: "),
        webhook_secret=secret("Zoom webhook secret: "),
        meeting_uuid=secret("Exact meeting UUID: "),
        **({"metadata_consent_receipts": receipts, "metadata_custody_confirmed": True} if enroll_metadata else {
            "participant_ids": tuple(int(i) for i in secret(
                "Approved participant numeric IDs (comma separated, max 2): "
            ).split(","))
        }),
        callback_port=int(read_text("Approved local webhook port: ")),
        callback_host=read_text(
            "Approved callback Host header (hostname[:port]): "
        ).strip(),
        callback_path=read_text(
            "Approved callback path (/zoom-webhook/<24+ characters>): "
        ).strip(),
        owner_receipt_id=read_text(
            "Content-free owner consent/rotation receipt ID: "
        ).strip(),
        budget_ceiling_usd=read_text(
            "Approved spending ceiling USD (e.g. 1.00): "
        ).strip(),
        maximum_capture_seconds=int(
            read_text("Approved cumulative window in seconds (60–120): " if enroll_metadata else
                      "Approved maximum window in seconds (60–900): ")
        ),
        code_revision=revision,
        live_network_authorized=True,
        credential_rotation_confirmed=True,
        synthetic_requirements_consent=True,
        credits_and_budget_confirmed=True,
    )
    if enroll_metadata:
        _enroll_and_pair(server.zoom_live_runtime, poc_id, settings, secret=secret, write=write)
    else:
        server.zoom_live_runtime.pair(poc_id, settings)
    del settings
    write(
        "Paired. Consent and start in the Live Zoom panel." if enroll_metadata else
        "Paired. Consent and start in the Live Zoom panel, then start RTMS in the approved Zoom app."
    )
    return True



def _enroll_and_pair(runtime, poc_id, settings, *, secret, write, pause=time.sleep):
    adopted = False
    handle = runtime.begin_enrollment(poc_id, settings)
    try:
        def wait(state, count):
            while True:
                status = runtime.enrollment_status(handle)
                if status["state"] == state and status["confirmed"] == count:
                    return status
                pause(0.05)
        write("Start RTMS in the approved Zoom app now. Metadata enrollment expires after 30 seconds.")
        wait("READY", 0)
        for slot in range(2):
            runtime.arm_enrollment(handle)
            write("Waiting for a fresh provider keep-alive before the speaking challenge.")
            wait("ARMED", slot)
            write(f"Approved speaker {slot + 1}: speak briefly now while the other person stays silent.")
            status = wait("OBSERVED", slot)
            identifier = status["candidate_id"]
            # Production secret() is the existing private TTY reader. Never log this ID.
            answer = secret(f"Observed RTMS ID {identifier}. Confirm this speaker by typing that exact number: ")
            if answer != str(identifier):
                raise ZoomLiveError()
            runtime.confirm_enrollment(handle, identifier)
            wait("READY", slot + 1)
        result = runtime.adopt_enrollment(handle)
        adopted = True
        return result
    except Exception:  # noqa: BLE001 - no private operator or metadata text in errors
        raise ZoomLiveError() from None
    finally:
        if not adopted:
            runtime.cancel_enrollment(handle)


def main():
    parser = argparse.ArgumentParser(
        description="Local operator-paired Zoom capture (explicit live opt-in)."
    )
    parser.add_argument("--enroll-metadata", action="store_true", help="Explicit two-person signaling-only numeric roster enrollment")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--output-root", type=Path, default=Path("runs"))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    # A live receipt may only name a committed clean implementation revision.
    revision = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=root, text=True
    ).strip()
    if subprocess.check_output(["git", "status", "--porcelain=v1"], cwd=root):
        raise SystemExit("Commit the reviewed candidate before a live operator run.")
    if args.enroll_metadata:
        from .poc_source_demo import serve_source_neutral_demo
        server = serve_source_neutral_demo(port=args.port, evidence_artifact_root=args.output_root, open_browser=True)
    else:
        server = serve_demo(port=args.port, output_root=args.output_root, open_browser=True)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        if not _pair_zoom_in_server(server, revision, enroll_metadata=args.enroll_metadata):
            return
        input("Press Enter here to revoke pairing and shut down: ")
    except (ValueError, OSError, EOFError, KeyboardInterrupt):
        print(
            "Operator run stopped. Review setup locally; private error details were discarded."
        )
    finally:
        server.shutdown()
        worker.join(timeout=5)
        server.server_close()


if __name__ == "__main__":
    main()
