"""Synthetic native packets: these tests do not demonstrate a live Zoom call."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace

import pytest

from exitspec.zoom_native_transcript import (
    MAX_NATIVE_INTEGER,
    MAX_NATIVE_PACKET_BYTES,
    ZoomNativeStreamBinding,
    ZoomNativeTranscriptError,
    decode_zoom_native_transcript,
)


def binding(**updates):
    fields = {
        "process_generation_sha256": "1" * 64,
        "session_id": "zoomsess_" + "2" * 64,
        "poc_id": "poc_native_test",
        "consent_receipt_sha256": "3" * 64,
        "meeting_binding_sha256": "4" * 64,
        "stream_binding_sha256": "5" * 64,
        "code_revision": "6" * 40,
    }
    return ZoomNativeStreamBinding(**(fields | updates))


def payload(**updates):
    return {
        "msg_type": 17,
        "content": {
            "user_id": 42,
            "user_name": "PRIVATE PERSON",
            "start_time": 1_800_000_000_000,
            "end_time": 1_800_000_000_400,
            "timestamp": 1_800_000_000_500,
            "language": 9,
            "data": "Criterion: p95 latency must stay below 730 milliseconds at concurrency 7.",
        }
        | updates,
    }


def raw(value=None):
    return json.dumps(value if value is not None else payload()).encode()


def decode(packet=None, **updates):
    args = {
        "binding": binding(),
        "speaker_pseudonyms": {42: "SPEAKER_1"},
        "arrival_index": 1,
    }
    return decode_zoom_native_transcript(
        raw() if packet is None else packet, **(args | updates)
    )


def test_actual_packet_content_and_native_facts_are_preserved_without_fixture_metadata():
    segment = decode()
    assert "730 milliseconds at concurrency 7" in segment.text
    assert "500" not in segment.text
    assert segment.provider_start_time == payload()["content"]["start_time"]
    assert segment.provider_end_time == payload()["content"]["end_time"]
    assert segment.provider_timestamp == payload()["content"]["timestamp"]
    assert segment.provider_timestamp_unit == "UNSPECIFIED_UNIX"
    assert segment.provider_language_code == 9
    assert segment.speaker_pseudonym == "SPEAKER_1"
    assert segment.packet_sha256 == hashlib.sha256(raw()).hexdigest()
    assert not hasattr(segment, "fixture_sha256")
    assert not hasattr(segment, "provider_connected")
    assert not hasattr(segment, "is_final")
    assert "PRIVATE PERSON" not in repr(segment)
    assert "730" not in repr(segment)


def test_normalization_preserves_raw_digest_and_replay_identity():
    packet = raw(payload(data="  Cafe\u0301  requirement:\n latency under 730 ms. "))
    segment = decode(packet)
    assert segment.text == "Café requirement: latency under 730 ms."
    replay = decode(packet, arrival_index=2)
    assert replay.normalized_sha256 == segment.normalized_sha256
    assert replay.arrival_index == 2
    assert replace(replay, arrival_index=1) == segment
    assert (
        decode(raw(payload(data=segment.text))).normalized_sha256
        != segment.normalized_sha256
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("process_generation_sha256", "a" * 64),
        ("session_id", "zoomsess_" + "a" * 64),
        ("poc_id", "poc_another"),
        ("consent_receipt_sha256", "a" * 64),
        ("meeting_binding_sha256", "a" * 64),
        ("stream_binding_sha256", "a" * 64),
        ("code_revision", "a" * 40),
    ],
)
def test_identical_packet_has_distinct_identity_for_each_security_binding(field, value):
    assert (
        decode(binding=binding(**{field: value})).normalized_sha256
        != decode().normalized_sha256
    )


def test_speaker_mapping_changes_normalized_identity():
    assert (
        decode(speaker_pseudonyms={42: "SPEAKER_2"}).normalized_sha256
        != decode().normalized_sha256
    )


@pytest.mark.parametrize(
    "times",
    [
        (1_800_000_000, 1_800_000_001, 1_800_000_002),
        (1_800_000_000_000, 1_800_000_001_000, 1_800_000_002_000),
        (1_800_000_000_000_000, 1_800_000_001_000_000, 1_800_000_002_000_000),
    ],
)
def test_no_magnitude_based_time_conversion(times):
    segment = decode(
        raw(payload(start_time=times[0], end_time=times[1], timestamp=times[2]))
    )
    assert (
        segment.provider_start_time,
        segment.provider_end_time,
        segment.provider_timestamp,
    ) == times


@pytest.mark.parametrize(
    "updates",
    [
        {"user_id": True},
        {"user_id": "42"},
        {"user_id": 43},
        {"start_time": -1},
        {"end_time": 0},
        {"timestamp": 1.5},
        {"timestamp": True},
        {"timestamp": MAX_NATIVE_INTEGER + 1},
        {"language": "en-US"},
        {"language": -1},
        {"data": " "},
        {"data": "secret\x00value"},
        {"data": "x" * 8193},
        {"data": "\ud800"},
        {"user_name": "\ud800"},
        {"user_name": 12},
        {"user_name": "x" * 257},
        {"channel_id": "contact-center"},
        {"is_final": True},
    ],
)
def test_unsupported_native_facts_fail_without_private_exception_chain(updates):
    with pytest.raises(ZoomNativeTranscriptError) as error:
        decode(raw(payload(**updates)))
    assert error.value.__cause__ is None
    assert error.value.__context__ is None
    assert str(error.value) == "The native Zoom transcript packet was rejected."


@pytest.mark.parametrize(
    "packet",
    [
        b"",
        b"\xff",
        b"[]",
        b"null",
        b"{",
        b'{"msg_type":17,"msg_type":17}',
        b'{"msg_type":17,"content":{"data":"private","data":"private"}}',
        b'{"msg_type":17,"content":{"timestamp":NaN}}',
        b"[" * 2000 + b"]" * 2000,
    ],
)
def test_malformed_json_is_sanitized(packet):
    with pytest.raises(ZoomNativeTranscriptError) as error:
        decode(packet)
    assert error.value.__context__ is None


@pytest.mark.parametrize("field", list(payload()["content"]))
def test_missing_fields_are_rejected_not_invented(field):
    value = payload()
    del value["content"][field]
    with pytest.raises(ZoomNativeTranscriptError):
        decode(raw(value))


@pytest.mark.parametrize("message_type", [True, "17", 12, 14, 18, 22])
def test_other_messages_cannot_be_misclassified_as_transcripts(message_type):
    with pytest.raises(ZoomNativeTranscriptError):
        decode(raw(payload() | {"msg_type": message_type}))


@pytest.mark.parametrize(
    "speakers",
    [
        {},
        {True: "SPEAKER_1"},
        {42: "PRIVATE PERSON"},
        {42: "SPEAKER_UNKNOWN"},
        {42: "SPEAKER_1", 43: "SPEAKER_1"},
        {42: "SPEAKER_1", 43: "SPEAKER_2", 44: "SPEAKER_3"},
        {(1 << 32): "SPEAKER_1"},
    ],
)
def test_participant_mapping_is_bounded_and_unambiguous(speakers):
    with pytest.raises(ZoomNativeTranscriptError):
        decode(speaker_pseudonyms=speakers)


@pytest.mark.parametrize("arrival", [0, 257, True, "1"])
def test_arrival_bounds(arrival):
    with pytest.raises(ZoomNativeTranscriptError):
        decode(arrival_index=arrival)


def test_oversized_bytes_rejected_before_json_processing(monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("oversized packet reached JSON decoder")

    monkeypatch.setattr("exitspec.zoom_native_transcript.json.loads", unexpected)
    with pytest.raises(ZoomNativeTranscriptError):
        decode(b"x" * (MAX_NATIVE_PACKET_BYTES + 1))


def test_forged_binding_is_revalidated_without_leaking_private_value():
    forged = binding().model_copy(update={"poc_id": "PRIVATE INVALID VALUE"})
    with pytest.raises(ZoomNativeTranscriptError) as error:
        decode(binding=forged)
    assert error.value.__context__ is None
    assert "PRIVATE" not in str(error.value)


def test_historical_fixture_envelope_is_not_accepted_as_native():
    value = payload()["content"] | {
        "schema_version": "exitspec.zoom-rtms-transcript-packet.v1",
        "media_type": 8,
        "message_type": 17,
    }
    with pytest.raises(ZoomNativeTranscriptError):
        decode(raw(value))
