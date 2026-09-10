"""Independent exact-wire contracts for the disabled live worker namespace."""

import dataclasses
import json
import os
import struct
import time

import pytest

from exitspec.canonical import canonical_json_bytes
from exitspec.source_authoring_ipc import SourceAuthoringWorkerError, body_digest
from exitspec.source_authoring_live_ipc import (
    LiveBinding,
    decode_live_frame,
    encode_live_frame,
    read_live_frame,
    validate_credential,
)


def bound(body=b"{}"):
    return LiveBinding(
        epoch="a" * 64,
        grant="b" * 64,
        operation="operation_test",
        generation="c" * 64,
        nonce="d" * 64,
        body_sha256=body_digest(body),
        profile_sha256="e" * 64,
        launch_profile_sha256="f" * 64,
        credential_generation=1,
        code_revision="1" * 40,
    )


def metadata(event, *, binding=None, deadline=None, credential_fd=None):
    value = {
        "version": "exitspec.source-authoring-live-ipc/1",
        "mode": "LIVE_PINNED",
        "event": event,
        "binding": dataclasses.asdict(binding or bound()),
    }
    if deadline is not None:
        value["deadline"] = deadline
    if credential_fd is not None:
        value["credential_fd"] = credential_fd
    return value


def raw_frame(value, payload=b"", *, raw=None):
    header = canonical_json_bytes(value) if raw is None else raw
    return (
        struct.pack(">II", 4 + len(header) + len(payload), len(header))
        + header
        + payload
    )


@pytest.mark.parametrize(
    "event,payload,cap",
    [
        ("PREPARE", b"", 2056),
        ("READY_NO_SEND", b"", 2056),
        ("SEND_TICKET", b"x" * 65536, 67592),
        ("CREDENTIAL", b"x" * 4096, 6152),
        ("RESULT", b"x" * 262144, 264200),
    ],
)
def test_exact_five_frames_boundaries_and_roundtrip(event, payload, cap):
    kwargs = {}
    if event in {"PREPARE", "SEND_TICKET", "CREDENTIAL"}:
        kwargs["deadline"] = time.monotonic() + 20
    if event == "PREPARE":
        kwargs["credential_fd"] = 5
    value = metadata(event, **kwargs)
    wire = encode_live_frame(event, bound(), payload, **kwargs)
    assert wire == raw_frame(value, payload)
    assert len(wire) <= cap
    assert decode_live_frame(wire, event=event) == (value, payload)
    for invalid in (wire + b"x", wire[:-1], raw_frame(value, payload + b"x")):
        with pytest.raises(SourceAuthoringWorkerError):
            decode_live_frame(invalid, event=event)


@pytest.mark.parametrize(
    "field,value",
    [
        ("epoch", "short_id"),
        ("grant", "G" * 64),
        ("operation", "a" * 129),
        ("operation", "unicode_é"),
        ("generation", "0" * 63),
        ("nonce", 1),
        ("body_sha256", "f" * 65),
        ("profile_sha256", True),
        ("launch_profile_sha256", None),
        ("credential_generation", True),
        ("credential_generation", 0),
        ("credential_generation", 1.0),
        ("credential_generation", 9007199254740992),
        ("code_revision", "1" * 64),
    ],
)
def test_binding_rejects_coercion_and_wrong_realm_shape(field, value):
    with pytest.raises(SourceAuthoringWorkerError):
        dataclasses.replace(bound(), **{field: value}).validate()


@pytest.mark.parametrize(
    "change",
    [
        {"mode": "SYNTHETIC_NO_NETWORK"},
        {"version": 1},
        {"event": "result"},
        {"unknown": "PRIVATE-MARKER"},
        {"deadline": 123},
    ],
)
def test_exact_metadata_namespace_keys_and_direction(change):
    value = metadata("RESULT") | change
    with pytest.raises(SourceAuthoringWorkerError) as error:
        decode_live_frame(raw_frame(value, b"{}"), event="RESULT")
    assert "PRIVATE-MARKER" not in str(error.value)


@pytest.mark.parametrize(
    "raw",
    [
        b'{"event":"RESULT","event":"RESULT"}',
        b'{"x":NaN}',
        b'{"x":Infinity}',
        b"[]",
        b"\xff",
    ],
)
def test_duplicate_nonfinite_invalid_utf8_and_nonobject(raw):
    with pytest.raises(SourceAuthoringWorkerError):
        decode_live_frame(raw_frame({}, b"{}", raw=raw), event="RESULT")


def test_noncanonical_metadata_refused():
    raw = json.dumps(metadata("RESULT"), indent=2).encode()
    with pytest.raises(SourceAuthoringWorkerError):
        decode_live_frame(raw_frame({}, b"{}", raw=raw), event="RESULT")


@pytest.mark.parametrize(
    "value",
    [
        b"",
        b"a" * 4097,
        b"a\rZ",
        b"a\nZ",
        b"a\x00Z",
        b" a",
        b"a ",
        b"a:b",
        b'a"b',
        b"\xff",
        b"a=b",
        b"=",
        "safe-looking",
        bytearray(b"safe"),
    ],
)
def test_secret_invalid_bytes_never_repaired(value):
    with pytest.raises(SourceAuthoringWorkerError):
        validate_credential(value)


@pytest.mark.parametrize("value", [b"a", b"Ab0-._~+/==", b"a" * 4096])
def test_secret_valid_exact_bytes(value):
    assert validate_credential(value) is value


@pytest.mark.parametrize(
    "declared,header_size", [(67589, 10), (100, 2049), (100, 1), (6, 5), (2048, 2048)]
)
def test_lengths_refuse_from_eight_bytes_without_waiting_for_payload(
    declared, header_size
):
    read_fd, write_fd = os.pipe()
    try:
        os.set_blocking(read_fd, False)
        os.write(write_fd, struct.pack(">II", declared, header_size))
        started = time.monotonic()
        with pytest.raises(SourceAuthoringWorkerError) as error:
            read_live_frame(read_fd, event="SEND_TICKET", deadline=started + 1)
        assert error.value.code != "worker_timeout"
        assert time.monotonic() - started < 0.2
    finally:
        os.close(read_fd)
        os.close(write_fd)


@pytest.mark.parametrize("deadline", [True, 0, -1, float("inf"), float("nan"), "1"])
def test_deadline_primitive_and_finite(deadline):
    with pytest.raises(SourceAuthoringWorkerError):
        encode_live_frame("SEND_TICKET", bound(), b"{}", deadline=deadline)


@pytest.mark.parametrize("fd", [True, 0, 1, 2, 65536, 3.0, "3"])
def test_descriptor_exact_range(fd):
    with pytest.raises(SourceAuthoringWorkerError):
        encode_live_frame(
            "PREPARE", bound(), b"", deadline=time.monotonic() + 1, credential_fd=fd
        )
