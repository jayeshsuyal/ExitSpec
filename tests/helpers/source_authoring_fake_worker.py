"""TEST-ONLY child; never an installed entrypoint or production profile option."""

import dataclasses
import json
import socket
import sys
import time

from exitspec import source_authoring_live_worker as worker


def forbidden(*args, **kwargs):
    raise AssertionError("test worker attempted real network access")


socket.socket = socket.create_connection = socket.getaddrinfo = forbidden
scenario = sys.argv[1]
original_encode = worker.encode_live_frame


def encode(event, binding, payload, **fields):
    if event == "RESULT":
        if scenario == "wrong_binding":
            binding = dataclasses.replace(binding, nonce="0" * 64)
        if scenario == "malformed_result":
            payload = b'{"PRIVATE-MARKER":NaN}'
    wire = original_encode(event, binding, payload, **fields)
    if event == "RESULT":
        if scenario == "partial_result":
            wire = wire[:-1]
        elif scenario == "extra_result":
            wire += wire
    return wire


def post(body, credential, *, deadline):
    assert credential == b"SYNTHETIC-KEY"
    if scenario.startswith("stall_") or scenario == "slow_trickle":
        time.sleep(60)
    if scenario == "worker_error":
        raise ValueError("PRIVATE-MARKER")
    return json.dumps(
        {
            "id": "fake-local",
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
    ).encode()


worker.encode_live_frame = encode
worker._post_exact = post
if scenario == "stall_ticket_read":
    original_read = worker.read_live_frame

    def read(fd, *, event, deadline):
        if event == "SEND_TICKET":
            time.sleep(60)
        return original_read(fd, event=event, deadline=deadline)

    worker.read_live_frame = read

try:
    worker._run_protocol()
except Exception:  # noqa: BLE001 - test child follows the production silent boundary
    sys.exit(2)
