"""Fixed installed one-shot worker. This checkpoint has no network implementation.

Only the explicitly distinct SYNTHETIC_NO_NETWORK protocol is implemented.
Scenarios simulate transport phases locally; no DNS/socket/HTTP module is used.
"""

from __future__ import annotations

import os
import struct
import sys
import time

from .source_authoring_ipc import (
    MAX_BODY_BYTES,
    MAX_REQUEST_WIRE,
    MAX_RESPONSE_BYTES,
    MAX_RESULT_WIRE,
    SourceAuthoringWorkerError,
    binding_from,
    body_digest,
    encode_frame,
    read_wire,
    require_eof,
    valid_deadline,
    write_wire,
)

SYNTHETIC_MODE = "SYNTHETIC_NO_NETWORK"
SCENARIOS = frozenset(
    {
        "success",
        "stall_dns",
        "stall_connect",
        "stall_headers",
        "stall_body",
        "slow_trickle",
        "partial_result",
        "oversize_result",
        "malformed_result",
        "extra_result",
        "wrong_binding",
        "worker_error",
        "result_boundary",
        "stall_ticket_read",
    }
)


def run(input_fd=0, output_fd=1):
    os.set_blocking(input_fd, False)
    os.set_blocking(output_fd, False)
    metadata, response = read_wire(
        input_fd,
        maximum_wire=MAX_REQUEST_WIRE,
        maximum_payload=MAX_BODY_BYTES,
        deadline=time.monotonic() + 5,
    )
    if set(metadata) != {"event", "mode", "binding", "deadline", "scenario"}:
        raise SourceAuthoringWorkerError()
    if metadata["mode"] != SYNTHETIC_MODE:
        raise SourceAuthoringWorkerError("live_prerequisites_missing")
    if metadata["event"] != "prepare" or metadata["scenario"] not in SCENARIOS:
        raise SourceAuthoringWorkerError()
    binding = binding_from(metadata["binding"])
    deadline = valid_deadline(metadata["deadline"])
    if not 0 < deadline - time.monotonic() <= 30:
        raise SourceAuthoringWorkerError("worker_deadline")
    ready = {
        "event": "READY_NO_SEND",
        "mode": SYNTHETIC_MODE,
        "binding": binding.fields(),
    }
    write_wire(
        output_fd,
        encode_frame(ready, b"", maximum_wire=MAX_RESULT_WIRE),
        deadline=deadline,
    )
    if metadata["scenario"] == "stall_ticket_read":
        time.sleep(60)
        return
    ticket, body = read_wire(
        input_fd,
        maximum_wire=MAX_REQUEST_WIRE,
        maximum_payload=MAX_BODY_BYTES,
        deadline=deadline,
    )
    if (
        set(ticket) != {"event", "mode", "binding", "deadline"}
        or ticket["event"] != "send_ticket"
        or ticket["mode"] != SYNTHETIC_MODE
        or binding_from(ticket["binding"]) != binding
        or ticket["deadline"] != deadline
        or body_digest(body) != binding.body_sha256
    ):
        raise SourceAuthoringWorkerError()
    # Parent closes its write pipe after exactly one ticket. Extra or partial
    # trailing frames fail before even a synthetic attempt can begin.
    require_eof(input_fd, deadline=deadline)
    if time.monotonic() >= deadline:
        raise SourceAuthoringWorkerError("worker_timeout")
    scenario = metadata["scenario"]
    if scenario.startswith("stall_"):
        time.sleep(60)  # Supervisor's total deadline must terminate this process.
        return
    if scenario == "worker_error":
        raise SourceAuthoringWorkerError()
    if scenario == "result_boundary":
        response = b"x" * MAX_RESPONSE_BYTES
    fields = binding.fields()
    if scenario == "wrong_binding":
        fields["nonce"] = "0" * 64
    result = encode_frame(
        {"event": "result", "mode": SYNTHETIC_MODE, "binding": fields},
        response,
        maximum_wire=MAX_RESULT_WIRE,
    )
    if scenario == "oversize_result":
        result = struct.pack(">I", MAX_RESULT_WIRE)
    elif scenario == "malformed_result":
        result = struct.pack(">II", 7, 3) + b"!!!"
    elif scenario == "partial_result":
        result = result[:-1]
    elif scenario == "extra_result":
        result += result
    if scenario == "slow_trickle":
        for byte in result:
            write_wire(output_fd, bytes([byte]), deadline=deadline)
            time.sleep(0.1)
    else:
        write_wire(output_fd, result, deadline=deadline)


def main():
    # No exception string, body, metadata or traceback is written to diagnostics.
    try:
        run()
    except Exception:  # noqa: BLE001 - subprocess boundary must never disclose private input
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
