from __future__ import annotations

import hashlib
import json

import pytest

from exitspec.zoom_rtms_decoder import (
    MAX_PACKET_BYTES,
    MAX_STREAM_BYTES,
    MAX_STREAM_RECORDS,
    ZOOM_RTMS_PACKET_SCHEMA_VERSION,
    ZOOM_RTMS_TRANSCRIPT_MEDIA_TYPE,
    ZOOM_RTMS_TRANSCRIPT_MESSAGE_TYPE,
    ZoomDecoderFailureCode,
    ZoomDecoderProvenance,
    ZoomRtmsDecodeError,
    decode_zoom_rtms_transcript_packet,
    decode_zoom_rtms_transcript_stream,
)


TIMESTAMP = 1_787_684_400_000


def _payload(**updates: object) -> dict[str, object]:
    value: dict[str, object] = {
        "schema_version": ZOOM_RTMS_PACKET_SCHEMA_VERSION,
        "media_type": ZOOM_RTMS_TRANSCRIPT_MEDIA_TYPE,
        "message_type": ZOOM_RTMS_TRANSCRIPT_MESSAGE_TYPE,
        "user_id": "provider-user-1",
        "start_time": "2026-08-25T19:00:00.000Z",
        "end_time": "2026-08-25T19:00:01.000Z",
        "timestamp": TIMESTAMP,
        "language": "en-US",
        "data": "  synthetic   transcript  ",
    }
    value.update(updates)
    return value


def _packet(**updates: object) -> bytes:
    return json.dumps(
        _payload(**updates),
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _provenance(packet: bytes) -> ZoomDecoderProvenance:
    return ZoomDecoderProvenance(
        source_classification="SYNTHETIC_REVIEWED_FIXTURE",
        fixture_sha256="1" * 64,
        capture_plan_sha256="2" * 64,
        setup_attestation_sha256="3" * 64,
        runtime_plan_sha256="4" * 64,
        packet_sha256=hashlib.sha256(packet).hexdigest(),
    )


def _decode(packet: bytes, *, arrival_index: int = 1):
    return decode_zoom_rtms_transcript_packet(
        packet,
        speaker_pseudonyms={"provider-user-1": "SPEAKER_1"},
        provenance=_provenance(packet),
        arrival_index=arrival_index,
    )


def test_golden_vector_normalizes_text_speaker_timing_and_provenance():
    packet = _packet()
    segment = _decode(packet)

    assert segment.speaker_pseudonym == "SPEAKER_1"
    assert segment.text == "synthetic transcript"
    assert segment.start_time_millisecond < segment.end_time_millisecond
    assert segment.duration_millisecond == 1000
    assert segment.provider_timestamp_millisecond == TIMESTAMP
    assert segment.arrival_index == 1
    assert segment.packet_sha256 == hashlib.sha256(packet).hexdigest()
    assert segment.provenance.source_classification == "SYNTHETIC_REVIEWED_FIXTURE"
    assert segment.text_authority == "UNTRUSTED_SOURCE_ONLY"
    serialized = json.dumps(segment.model_dump(mode="json"))
    assert "provider-user-1" not in serialized
    assert "provider_name" not in serialized
    assert segment.may_confirm_contract is False
    assert segment.may_freeze_contract is False
    assert segment.may_start_measurement is False
    assert segment.may_assign_verdict is False


@pytest.mark.parametrize(
    "packet,code",
    (
        (b"{not-json", ZoomDecoderFailureCode.MALFORMED_JSON),
        (b"\xff", ZoomDecoderFailureCode.MALFORMED_JSON),
        (_packet(schema_version="zoom-rtms-transcript-packet.v9"), ZoomDecoderFailureCode.UNKNOWN_VERSION),
        (_packet(media_type=7), ZoomDecoderFailureCode.UNSUPPORTED_MEDIA),
        (_packet(message_type=16), ZoomDecoderFailureCode.UNSUPPORTED_MESSAGE),
        (_packet(extra_field="reject"), ZoomDecoderFailureCode.UNSUPPORTED_FIELDS),
        (_packet(data={"text": "ambiguous"}), ZoomDecoderFailureCode.INVALID_RECORD),
        (_packet(timestamp=-1), ZoomDecoderFailureCode.INVALID_TIMESTAMP),
        (_packet(timestamp=4_102_444_800_001), ZoomDecoderFailureCode.INVALID_TIMESTAMP),
        (
            _packet(start_time="2026-08-25T19:00:02.000Z"),
            ZoomDecoderFailureCode.INVALID_TIMESTAMP,
        ),
        (_packet(language="not a language"), ZoomDecoderFailureCode.INVALID_RECORD),
        (_packet(data="\u0001"), ZoomDecoderFailureCode.INVALID_TEXT),
    ),
)
def test_malformed_and_unsupported_vectors_fail_closed(packet: bytes, code: str):
    with pytest.raises(ZoomRtmsDecodeError) as exc_info:
        _decode(packet)
    assert exc_info.value.code == code
    assert "provider-user-1" not in str(exc_info.value)


def test_duplicate_keys_are_rejected_before_json_recovery():
    packet = (
        b'{"schema_version":"exitspec.zoom-rtms-transcript-packet.v1",'
        b'"media_type":8,"message_type":17,"user_id":"provider-user-1",'
        b'"start_time":"2026-08-25T19:00:00.000Z",'
        b'"end_time":"2026-08-25T19:00:01.000Z","timestamp":1787684400000,'
        b'"language":"en-US","data":"one","data":"two"}'
    )
    with pytest.raises(ZoomRtmsDecodeError) as exc_info:
        _decode(packet)
    assert exc_info.value.code == ZoomDecoderFailureCode.DUPLICATE_KEY


def test_packet_size_depth_numeric_and_record_limits_are_bounded():
    with pytest.raises(ZoomRtmsDecodeError) as oversized:
        _decode(b"{" + b" " * MAX_PACKET_BYTES)
    assert oversized.value.code == ZoomDecoderFailureCode.INPUT_BOUNDED

    deep = "{}"
    for _ in range(10):
        deep = '{"nested":' + deep + "}"
    with pytest.raises(ZoomRtmsDecodeError) as depth_exc:
        _decode(deep.encode("utf-8"))
    assert depth_exc.value.code == ZoomDecoderFailureCode.DEPTH_EXCEEDED

    packets = [_packet(timestamp=TIMESTAMP + index) for index in range(MAX_STREAM_RECORDS)]
    stream = b"\n".join(packets)
    mapping = {
        hashlib.sha256(packet).hexdigest(): _provenance(packet)
        for packet in packets
    }
    segments = decode_zoom_rtms_transcript_stream(
        stream,
        speaker_pseudonyms={"provider-user-1": "SPEAKER_1"},
        provenance_by_packet_sha256=mapping,
    )
    assert len(segments) == MAX_STREAM_RECORDS
    assert segments[-1].arrival_index == MAX_STREAM_RECORDS

    too_many = b"\n".join(packets + [_packet(timestamp=TIMESTAMP + MAX_STREAM_RECORDS)])
    with pytest.raises(ZoomRtmsDecodeError) as records_exc:
        decode_zoom_rtms_transcript_stream(
            too_many,
            speaker_pseudonyms={"provider-user-1": "SPEAKER_1"},
            provenance_by_packet_sha256=mapping,
        )
    assert records_exc.value.code == ZoomDecoderFailureCode.STREAM_BOUNDED

    with pytest.raises(ZoomRtmsDecodeError) as stream_size_exc:
        decode_zoom_rtms_transcript_stream(
            b"x" * (MAX_STREAM_BYTES + 1),
            speaker_pseudonyms={"provider-user-1": "SPEAKER_1"},
            provenance_by_packet_sha256={},
        )
    assert stream_size_exc.value.code == ZoomDecoderFailureCode.STREAM_BOUNDED


def test_stream_rejects_duplicates_gaps_and_missing_provenance():
    first = _packet()
    second = _packet(timestamp=TIMESTAMP + 1000)
    with pytest.raises(ZoomRtmsDecodeError) as duplicate_exc:
        decode_zoom_rtms_transcript_stream(
            first + b"\n" + first,
            speaker_pseudonyms={"provider-user-1": "SPEAKER_1"},
            provenance_by_packet_sha256={
                hashlib.sha256(first).hexdigest(): _provenance(first),
            },
        )
    assert duplicate_exc.value.code == ZoomDecoderFailureCode.INVALID_RECORD

    with pytest.raises(ZoomRtmsDecodeError) as missing_exc:
        decode_zoom_rtms_transcript_stream(
            first + b"\n" + second,
            speaker_pseudonyms={"provider-user-1": "SPEAKER_1"},
            provenance_by_packet_sha256={
                hashlib.sha256(first).hexdigest(): _provenance(first),
            },
        )
    assert missing_exc.value.code == ZoomDecoderFailureCode.INVALID_RECORD

    with pytest.raises(ZoomRtmsDecodeError) as blank_exc:
        decode_zoom_rtms_transcript_stream(
            first + b"\n\n" + second,
            speaker_pseudonyms={"provider-user-1": "SPEAKER_1"},
            provenance_by_packet_sha256={
                hashlib.sha256(first).hexdigest(): _provenance(first),
                hashlib.sha256(second).hexdigest(): _provenance(second),
            },
        )
    assert blank_exc.value.code == ZoomDecoderFailureCode.STREAM_BOUNDED


def test_speaker_identity_requires_explicit_server_owned_pseudonym():
    packet = _packet(user_id="unmapped-provider-user")
    with pytest.raises(ZoomRtmsDecodeError) as exc_info:
        decode_zoom_rtms_transcript_packet(
            packet,
            speaker_pseudonyms={"provider-user-1": "SPEAKER_1"},
            provenance=_provenance(packet),
        )
    assert exc_info.value.code == ZoomDecoderFailureCode.INVALID_IDENTITY

def test_timestamp_edges_require_utc_rfc3339_and_safe_integer():
    edge_packet = _packet(
        start_time="1970-01-01T00:00:00.000Z",
        end_time="1970-01-01T00:00:00.000Z",
        timestamp=0,
    )
    edge = _decode(edge_packet)
    assert edge.start_time_millisecond == 0
    assert edge.end_time_millisecond == 0

    for update in (
        {"start_time": "2026-08-25T19:00:00.000+00:00"},
        {"start_time": "2026-08-25 19:00:00.000Z"},
        {"timestamp": True},
        {"timestamp": 1.5},
        {"end_time": "2026-08-25T19:10:01.000Z"},
        {"start_time": "2026-08-25T19:00:00.000Z", "end_time": "not-time"},
    ):
        packet = _packet(**update)
        with pytest.raises(ZoomRtmsDecodeError) as exc_info:
            _decode(packet)
        assert exc_info.value.code in {
            ZoomDecoderFailureCode.INVALID_TIMESTAMP,
            ZoomDecoderFailureCode.INVALID_RECORD,
        }


def test_arrival_index_and_provenance_are_required_and_bound():
    packet = _packet()
    with pytest.raises(ZoomRtmsDecodeError) as arrival_exc:
        _decode(packet, arrival_index=0)
    assert arrival_exc.value.code == ZoomDecoderFailureCode.INVALID_RECORD

    wrong_provenance = _provenance(packet).model_copy(
        update={"packet_sha256": "f" * 64}
    )
    with pytest.raises(ZoomRtmsDecodeError) as provenance_exc:
        decode_zoom_rtms_transcript_packet(
            packet,
            speaker_pseudonyms={"provider-user-1": "SPEAKER_1"},
            provenance=wrong_provenance,
        )
    assert provenance_exc.value.code == ZoomDecoderFailureCode.INVALID_RECORD
