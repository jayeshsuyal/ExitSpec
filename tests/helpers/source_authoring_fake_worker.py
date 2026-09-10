"""TEST-ONLY child; never an installed entrypoint or production profile option."""

import dataclasses
import json
import re
import socket
import sys
import time
from pathlib import Path

from exitspec import source_authoring_launch as launch
from exitspec import source_authoring_live_worker as worker

sys.path.insert(0, str(Path(__file__).resolve().parent))
from source_authoring_admission import fake_profile

profile = fake_profile()
launch._PRODUCTION_PROFILES = (profile,)


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
    content = '{"synthetic":true}'
    if scenario == "authoring":
        source = json.loads(json.loads(body)["messages"][1]["content"].split("\n", 1)[1])["text"]
        quotes = [text.strip() for text in re.split(r"(?<=[.!?])\s+", source) if text.strip()]
        content = json.dumps({
            "schema_version": "exitspec.assisted-authoring-output.v1",
            "proposals": [{"proposal_key": f"offline-{index}", "source_quote": quote,
                           "normalized_claim": quote, "numeric_facts": None}
                          for index, quote in enumerate(quotes[:3])],
        })
    return json.dumps(
        {
            "id": "fake-local",
            "object": "chat.completion",
            "created": 0,
            "model": "accounts/fireworks/models/deepseek-v4-flash-0731",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
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
    worker._run_protocol(profile)
except Exception:  # noqa: BLE001 - test child follows the production silent boundary
    sys.exit(2)
