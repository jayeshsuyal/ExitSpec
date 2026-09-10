"""Explicit local operator bootstrap; never invoked by an HTTP request."""

from __future__ import annotations

import argparse
import getpass
import subprocess
import threading
from pathlib import Path

from .web import serve_demo
from .zoom_live_runtime import ZoomLiveSettings


def _pair_zoom_in_server(server, revision, *, read_text=None, secret=None, write=None):
    """Pair this exact server/runtime; Zoom prerequisites and consent stay separate."""
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
    settings = ZoomLiveSettings(
        client_id=secret("Zoom client ID: "),
        client_secret=secret("Zoom client secret: "),
        webhook_secret=secret("Zoom webhook secret: "),
        meeting_uuid=secret("Exact meeting UUID: "),
        participant_ids=tuple(
            int(i)
            for i in secret(
                "Approved participant numeric IDs (comma separated, max 2): "
            ).split(",")
        ),
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
            read_text("Approved maximum window in seconds (60–900): ")
        ),
        code_revision=revision,
        live_network_authorized=True,
        credential_rotation_confirmed=True,
        synthetic_requirements_consent=True,
        credits_and_budget_confirmed=True,
    )
    server.zoom_live_runtime.pair(poc_id, settings)
    del settings
    write(
        "Paired. Consent and start in the Live Zoom panel, then start RTMS in the approved Zoom app."
    )
    return True


def main():
    parser = argparse.ArgumentParser(
        description="Local operator-paired Zoom capture (explicit live opt-in)."
    )
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
    server = serve_demo(port=args.port, output_root=args.output_root, open_browser=True)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        if not _pair_zoom_in_server(server, revision):
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
