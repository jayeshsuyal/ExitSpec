"""Strict separate live wire namespace; this module grants no execution authority."""

from __future__ import annotations

import math
import re
import struct
import time
from dataclasses import asdict, dataclass

from .source_authoring_ipc import (
    SourceAuthoringWorkerError,
    _read_exact,
    decode_frame,
    encode_frame,
)

VERSION = "exitspec.source-authoring-live-ipc/1"
MODE = "LIVE_PINNED"
MAX_METADATA = 2048
MAX_CREDENTIAL = 4096
MAX_SAFE_INTEGER = 9007199254740991
# event -> (minimum payload, maximum payload, maximum full wire bytes)
_LIMITS = {
    "PREPARE": (0, 0, 2056),
    "READY_NO_SEND": (0, 0, 2056),
    "SEND_TICKET": (1, 65536, 67592),
    "CREDENTIAL": (1, 4096, 6152),
    "RESULT": (1, 262144, 264200),
}


@dataclass(frozen=True, repr=False)
class LiveBinding:
    epoch: str
    grant: str
    operation: str
    generation: str
    nonce: str
    body_sha256: str
    profile_sha256: str
    launch_profile_sha256: str
    credential_generation: int
    code_revision: str

    def validate(self):
        for name in (
            "epoch",
            "grant",
            "generation",
            "nonce",
            "body_sha256",
            "profile_sha256",
            "launch_profile_sha256",
            "code_revision",
        ):
            value = getattr(self, name)
            size = 40 if name == "code_revision" else 64
            if type(value) is not str or not re.fullmatch(
                r"[a-f0-9]{" + str(size) + "}", value
            ):
                raise SourceAuthoringWorkerError()
        if (
            type(self.operation) is not str
            or not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", self.operation)
            or type(self.credential_generation) is not int
            or not 1 <= self.credential_generation <= MAX_SAFE_INTEGER
        ):
            raise SourceAuthoringWorkerError()
        return self

    def fields(self):
        return asdict(self.validate())


def live_binding_from(value):
    try:
        if type(value) is not dict:
            raise ValueError
        return LiveBinding(**value).validate()
    except (ValueError, TypeError):
        pass
    raise SourceAuthoringWorkerError()


def check_deadline(value, *, admission=False):
    if (
        type(value) not in (int, float)
        or not math.isfinite(value)
        or value <= 0
        or (admission and not 0 < value - time.monotonic() <= 30)
    ):
        raise SourceAuthoringWorkerError("worker_deadline")
    return value  # Retain the exact admitted number; never extend or coerce it.


def validate_credential(value):
    if (
        type(value) is not bytes
        or not 1 <= len(value) <= MAX_CREDENTIAL
        or not re.fullmatch(rb"[A-Za-z0-9._~+/-]+=*", value)
    ):
        raise SourceAuthoringWorkerError("worker_credential")
    return value


def _limits(event):
    if type(event) is not str or event not in _LIMITS:
        raise SourceAuthoringWorkerError()
    return _LIMITS[event]


def _validate_metadata(metadata, event):
    required = {"version", "mode", "event", "binding"}
    if event in {"PREPARE", "SEND_TICKET", "CREDENTIAL"}:
        required.add("deadline")
    if event == "PREPARE":
        required.add("credential_fd")
    if (
        type(metadata) is not dict
        or set(metadata) != required
        or metadata["version"] != VERSION
        or metadata["mode"] != MODE
        or metadata["event"] != event
    ):
        raise SourceAuthoringWorkerError()
    live_binding_from(metadata["binding"])
    if "deadline" in required:
        check_deadline(metadata["deadline"])
    if "credential_fd" in required:
        fd = metadata["credential_fd"]
        if type(fd) is not int or not 3 <= fd <= 65535:
            raise SourceAuthoringWorkerError()


def _sizes(declared, header_size, event):
    minimum, maximum, wire_cap = _limits(event)
    if (
        not 2 <= header_size <= MAX_METADATA
        or declared + 4 > wire_cap
        or not minimum <= declared - 4 - header_size <= maximum
    ):
        raise SourceAuthoringWorkerError("worker_frame_limit")


def encode_live_frame(event, binding, payload, **fields):
    _limits(event)
    if type(binding) is not LiveBinding or type(payload) is not bytes:
        raise SourceAuthoringWorkerError()
    metadata = {
        "version": VERSION,
        "mode": MODE,
        "event": event,
        "binding": binding.fields(),
        **fields,
    }
    _validate_metadata(metadata, event)
    wire = encode_frame(metadata, payload, maximum_wire=_LIMITS[event][2])
    _sizes(*struct.unpack(">II", wire[:8]), event)
    return wire


def decode_live_frame(wire, *, event):
    _minimum, maximum, cap = _limits(event)
    if type(wire) is not bytes or not 10 <= len(wire) <= cap:
        raise SourceAuthoringWorkerError()
    _sizes(*struct.unpack(">II", wire[:8]), event)
    metadata, payload = decode_frame(wire, maximum_wire=cap, maximum_payload=maximum)
    _validate_metadata(metadata, event)
    return metadata, payload


def read_live_frame(fd, *, event, deadline):
    # Unlike the generic legacy reader, validate BOTH length words before
    # reading/allocating metadata or payload. Legacy framing is unchanged.
    _limits(event)
    header = _read_exact(fd, 8, deadline=deadline)
    declared, header_size = struct.unpack(">II", header)
    _sizes(declared, header_size, event)
    wire = header + _read_exact(fd, declared - 4, deadline=deadline)
    return decode_live_frame(wire, event=event)
