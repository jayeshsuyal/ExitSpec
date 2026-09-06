"""Bounded private one-shot IPC; byte limits apply to actual framed wire bytes."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import select
import struct
import time
from dataclasses import asdict, dataclass

from .canonical import canonical_json_bytes

MAX_REQUEST_WIRE = 81_920
MAX_RESULT_WIRE = 278_528
MAX_BODY_BYTES = 65_536
MAX_RESPONSE_BYTES = 262_144
MAX_METADATA_BYTES = 16_376
BODY_DOMAIN = b"exitspec-source-authoring-body-v1\0"


class SourceAuthoringWorkerError(ValueError):
    def __init__(self, code="worker_protocol"):
        self.code = code
        super().__init__("Source authoring worker refused the operation.")


@dataclass(frozen=True, repr=False)
class WorkerBinding:
    epoch: str
    grant: str
    operation: str
    generation: str
    nonce: str
    body_sha256: str
    profile_sha256: str

    def validate(self):
        for value in (self.epoch, self.grant, self.operation):
            if type(value) is not str or not re.fullmatch(
                r"[A-Za-z0-9_-]{8,128}", value
            ):
                raise SourceAuthoringWorkerError()
        for value in (
            self.generation,
            self.nonce,
            self.body_sha256,
            self.profile_sha256,
        ):
            if type(value) is not str or not re.fullmatch(r"[a-f0-9]{64}", value):
                raise SourceAuthoringWorkerError()
        return self

    def fields(self):
        return asdict(self)


def binding_from(value):
    try:
        if type(value) is not dict:
            raise ValueError
        binding = WorkerBinding(**value).validate()
    except (ValueError, TypeError):
        binding = None
    if binding is None:
        raise SourceAuthoringWorkerError()
    return binding


def body_digest(body: bytes) -> str:
    return hashlib.sha256(BODY_DOMAIN + body).hexdigest()


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise SourceAuthoringWorkerError()
        result[key] = value
    return result


def _nonfinite(_):
    raise SourceAuthoringWorkerError()


def encode_frame(metadata: dict, payload: bytes, *, maximum_wire: int) -> bytes:
    failed = False
    try:
        if type(metadata) is not dict or type(payload) is not bytes:
            raise ValueError
        header = canonical_json_bytes(metadata)
        total = 8 + len(header) + len(payload)
        if not 2 <= len(header) <= MAX_METADATA_BYTES or total > maximum_wire:
            raise ValueError
        return struct.pack(">II", total - 4, len(header)) + header + payload
    except (ValueError, TypeError, UnicodeError, RecursionError):
        failed = True
    if failed:
        raise SourceAuthoringWorkerError("worker_frame_limit")


def decode_frame(frame: bytes, *, maximum_wire: int, maximum_payload: int):
    failed = False
    try:
        if type(frame) is not bytes or not 10 <= len(frame) <= maximum_wire:
            raise ValueError
        declared, header_size = struct.unpack(">II", frame[:8])
        if declared != len(frame) - 4 or not 2 <= header_size <= MAX_METADATA_BYTES:
            raise ValueError
        if (
            8 + header_size > len(frame)
            or len(frame) - 8 - header_size > maximum_payload
        ):
            raise ValueError
        metadata = json.loads(
            frame[8 : 8 + header_size].decode("utf-8"),
            object_pairs_hook=_pairs,
            parse_constant=_nonfinite,
        )
        if type(metadata) is not dict:
            raise ValueError
        payload = frame[8 + header_size :]
        # Reject noncanonical metadata; body/response bytes are never reconstructed.
        if encode_frame(metadata, payload, maximum_wire=maximum_wire) != frame:
            raise ValueError
        return metadata, payload
    except (ValueError, TypeError, UnicodeError, RecursionError, struct.error):
        failed = True
    if failed:
        raise SourceAuthoringWorkerError()


def valid_deadline(value):
    if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
        raise SourceAuthoringWorkerError("worker_deadline")
    return float(value)


def _wait(fd, *, write, deadline):
    remaining = valid_deadline(deadline) - time.monotonic()
    if remaining <= 0:
        raise SourceAuthoringWorkerError("worker_timeout")
    readable, writable, _ = select.select(
        [] if write else [fd], [fd] if write else [], [], remaining
    )
    if not (writable if write else readable):
        raise SourceAuthoringWorkerError("worker_timeout")


def write_wire(fd: int, frame: bytes, *, deadline: float):
    offset = 0
    while offset < len(frame):
        _wait(fd, write=True, deadline=deadline)
        try:
            count = os.write(fd, memoryview(frame)[offset : offset + 4096])
        except BlockingIOError:
            continue
        if count <= 0:
            raise SourceAuthoringWorkerError("worker_pipe")
        offset += count


def _read_exact(fd: int, size: int, *, deadline: float):
    body = bytearray()
    while len(body) < size:
        _wait(fd, write=False, deadline=deadline)
        try:
            chunk = os.read(fd, min(size - len(body), 8192))
        except BlockingIOError:
            continue
        if not chunk:
            raise SourceAuthoringWorkerError("worker_partial_frame")
        body.extend(chunk)
    return bytes(body)


def read_wire(fd: int, *, maximum_wire: int, maximum_payload: int, deadline: float):
    header = _read_exact(fd, 4, deadline=deadline)
    declared = struct.unpack(">I", header)[0]
    if not 6 <= declared <= maximum_wire - 4:
        raise SourceAuthoringWorkerError("worker_frame_limit")
    frame = header + _read_exact(fd, declared, deadline=deadline)
    return decode_frame(
        frame, maximum_wire=maximum_wire, maximum_payload=maximum_payload
    )


def require_eof(fd: int, *, deadline: float):
    _wait(fd, write=False, deadline=deadline)
    if os.read(fd, 1):
        raise SourceAuthoringWorkerError("worker_extra_frame")
