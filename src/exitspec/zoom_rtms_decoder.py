"""Small, strict decoder for the pinned Zoom RTMS transcript subset.

The decoder accepts only the explicitly pinned transcript envelope documented by
the decoder specification. It is a provider-adapter boundary, not a general JSON
parser: unknown fields, versions, media/message types, identities, timestamps,
and payload shapes fail closed. Provider identifiers never enter the normalized
public model.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
import hashlib
import hmac
import json
import re
from enum import Enum
from typing import Any, Final, Literal
import unicodedata

from pydantic import ConfigDict, Field, StrictInt, field_validator, model_validator

from .canonical import canonical_json_bytes
from .models import FrozenExitSpecModel, SHA256_PATTERN


ZOOM_RTMS_DECODER_VERSION = "exitspec.zoom-rtms-decoder/1.0"
ZOOM_RTMS_PACKET_SCHEMA_VERSION = "exitspec.zoom-rtms-transcript-packet.v1"
ZOOM_RTMS_TRANSCRIPT_MEDIA_TYPE = 8
ZOOM_RTMS_TRANSCRIPT_MESSAGE_TYPE = 17
ZOOM_DECODER_TEXT_AUTHORITY = "UNTRUSTED_SOURCE_ONLY"
ZOOM_DECODER_REVIEW_STATE = "NEEDS_REVIEW"

MAX_PACKET_BYTES = 64 * 1024
MAX_STREAM_BYTES = 1024 * 1024
MAX_STREAM_RECORDS = 256
MAX_JSON_DEPTH = 8
MAX_TEXT_CHARACTERS = 8 * 1024
MAX_LANGUAGE_CHARACTERS = 35
MAX_TIMESTAMP_MILLISECONDS = 4_102_444_800_000
MAX_SEGMENT_MILLISECONDS = 10 * 60 * 1000

_SEGMENT_DOMAIN = b"exitspec-zoom-rtms-segment-v1\x00"
_UTC_TIMESTAMP_PATTERN = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z$"
)
_LANGUAGE_TAG_PATTERN = re.compile(r"^[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*$")
_ALLOWED_PACKET_KEYS: Final = frozenset(
    {
        "schema_version",
        "media_type",
        "message_type",
        "user_id",
        "start_time",
        "end_time",
        "timestamp",
        "language",
        "data",
    }
)


class ZoomDecoderFailureCode(str, Enum):
    """Stable content-free decoder refusal codes."""

    INPUT_BOUNDED = "ZOOM_DECODER_INPUT_BOUNDED"
    MALFORMED_JSON = "ZOOM_DECODER_MALFORMED_JSON"
    DUPLICATE_KEY = "ZOOM_DECODER_DUPLICATE_KEY"
    DEPTH_EXCEEDED = "ZOOM_DECODER_DEPTH_EXCEEDED"
    UNKNOWN_VERSION = "ZOOM_DECODER_UNKNOWN_VERSION"
    UNSUPPORTED_FIELDS = "ZOOM_DECODER_UNSUPPORTED_FIELDS"
    UNSUPPORTED_MEDIA = "ZOOM_DECODER_UNSUPPORTED_MEDIA"
    UNSUPPORTED_MESSAGE = "ZOOM_DECODER_UNSUPPORTED_MESSAGE"
    INVALID_RECORD = "ZOOM_DECODER_INVALID_RECORD"
    INVALID_IDENTITY = "ZOOM_DECODER_INVALID_IDENTITY"
    INVALID_TIMESTAMP = "ZOOM_DECODER_INVALID_TIMESTAMP"
    INVALID_TEXT = "ZOOM_DECODER_INVALID_TEXT"
    STREAM_BOUNDED = "ZOOM_DECODER_STREAM_BOUNDED"


class ZoomRtmsDecodeError(RuntimeError):
    """Sanitized decoder refusal that never echoes packet values."""

    def __init__(self, code: str | ZoomDecoderFailureCode) -> None:
        self.code = ZoomDecoderFailureCode(code).value
        super().__init__("The Zoom RTMS transcript packet was rejected.")


class _DecoderModel(FrozenExitSpecModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
        validate_default=True,
    )


class ZoomDecoderProvenance(_DecoderModel):
    """Digest-only binding for one normalized packet."""

    source_classification: Literal[
        "SYNTHETIC_REVIEWED_FIXTURE",
        "PRIVATE_SYNTHETIC_RUNTIME",
    ]
    fixture_sha256: str = Field(pattern=SHA256_PATTERN)
    capture_plan_sha256: str = Field(pattern=SHA256_PATTERN)
    setup_attestation_sha256: str = Field(pattern=SHA256_PATTERN)
    runtime_plan_sha256: str = Field(pattern=SHA256_PATTERN)
    packet_sha256: str = Field(pattern=SHA256_PATTERN)


class ZoomNormalizedTranscriptSegment(_DecoderModel):
    """Provider-neutral transcript segment with untrusted source authority."""

    schema_version: Literal[ZOOM_RTMS_DECODER_VERSION] = ZOOM_RTMS_DECODER_VERSION
    segment_id: str = Field(pattern=r"^zoomsegment_[a-f0-9]{64}$")
    packet_sha256: str = Field(pattern=SHA256_PATTERN)
    arrival_index: StrictInt = Field(gt=0, le=MAX_STREAM_RECORDS)
    provider_timestamp_millisecond: StrictInt = Field(
        ge=0,
        le=MAX_TIMESTAMP_MILLISECONDS,
    )
    start_time_millisecond: StrictInt = Field(
        ge=0,
        le=MAX_TIMESTAMP_MILLISECONDS,
    )
    end_time_millisecond: StrictInt = Field(
        ge=0,
        le=MAX_TIMESTAMP_MILLISECONDS,
    )
    duration_millisecond: StrictInt = Field(
        ge=0,
        le=MAX_SEGMENT_MILLISECONDS,
    )
    language: str = Field(min_length=2, max_length=MAX_LANGUAGE_CHARACTERS)
    speaker_pseudonym: Literal["SPEAKER_1", "SPEAKER_2", "SPEAKER_UNKNOWN"]
    text: str = Field(min_length=1, max_length=MAX_TEXT_CHARACTERS)
    ordering_metadata: tuple[str, ...] = (
        "arrival_index",
        "provider_timestamp_millisecond",
        "start_time_millisecond",
        "end_time_millisecond",
    )
    provenance: ZoomDecoderProvenance
    text_authority: Literal[ZOOM_DECODER_TEXT_AUTHORITY] = (
        ZOOM_DECODER_TEXT_AUTHORITY
    )
    review_state: Literal[ZOOM_DECODER_REVIEW_STATE] = ZOOM_DECODER_REVIEW_STATE
    may_confirm_contract: Literal[False] = False
    may_freeze_contract: Literal[False] = False
    may_start_measurement: Literal[False] = False
    may_assign_verdict: Literal[False] = False

    @field_validator("language")
    @classmethod
    def validate_language(cls, value: str) -> str:
        if not _is_language_tag(value):
            raise ValueError("language is not a supported BCP-47 subset.")
        return value

    @field_validator("text")
    @classmethod
    def validate_text(cls, value: str) -> str:
        normalized = unicodedata.normalize("NFC", value)
        if normalized != value or any(
            ord(character) < 0x20 and character not in "\t\n\r"
            for character in normalized
        ):
            raise ValueError("text contains unsupported control or normalization data.")
        compact = " ".join(normalized.split())
        if not compact:
            raise ValueError("text is empty after normalization.")
        return compact

    @model_validator(mode="after")
    def validate_segment(self) -> "ZoomNormalizedTranscriptSegment":
        if self.end_time_millisecond < self.start_time_millisecond:
            raise ValueError("segment end precedes start.")
        if self.duration_millisecond != (
            self.end_time_millisecond - self.start_time_millisecond
        ):
            raise ValueError("segment duration is inconsistent.")
        expected = _digest_identifier(
            "zoomsegment_",
            _SEGMENT_DOMAIN,
            _model_payload_without(self, "segment_id"),
        )
        if not hmac.compare_digest(self.segment_id, expected):
            raise ValueError("segment identity is invalid.")
        if self.provenance.packet_sha256 != self.packet_sha256:
            raise ValueError("packet provenance is inconsistent.")
        return self


def decode_zoom_rtms_transcript_packet(
    packet: bytes,
    *,
    speaker_pseudonyms: Mapping[str, str],
    provenance: ZoomDecoderProvenance,
    arrival_index: int = 1,
) -> ZoomNormalizedTranscriptSegment:
    """Decode exactly one pinned transcript packet."""

    if not isinstance(provenance, ZoomDecoderProvenance):
        raise ZoomRtmsDecodeError(ZoomDecoderFailureCode.INVALID_RECORD)
    raw = _bounded_packet(packet)
    payload = _parse_json_object(raw)
    _validate_packet_shape(payload)
    packet_sha256 = hashlib.sha256(raw).hexdigest()
    parsed = _parse_packet_fields(payload)
    provider_user_id = parsed["user_id"]
    speaker = speaker_pseudonyms.get(provider_user_id)
    if speaker not in {"SPEAKER_1", "SPEAKER_2", "SPEAKER_UNKNOWN"}:
        raise ZoomRtmsDecodeError(ZoomDecoderFailureCode.INVALID_IDENTITY)
    if not isinstance(arrival_index, int) or isinstance(arrival_index, bool):
        raise ZoomRtmsDecodeError(ZoomDecoderFailureCode.INVALID_RECORD)
    if not 1 <= arrival_index <= MAX_STREAM_RECORDS:
        raise ZoomRtmsDecodeError(ZoomDecoderFailureCode.INVALID_RECORD)
    if provenance.packet_sha256 != packet_sha256:
        raise ZoomRtmsDecodeError(ZoomDecoderFailureCode.INVALID_RECORD)

    payload_for_segment: dict[str, object] = {
        "schema_version": ZOOM_RTMS_DECODER_VERSION,
        "segment_id": "zoomsegment_" + "0" * 64,
        "packet_sha256": packet_sha256,
        "arrival_index": arrival_index,
        "provider_timestamp_millisecond": parsed["timestamp"],
        "start_time_millisecond": parsed["start_time_millisecond"],
        "end_time_millisecond": parsed["end_time_millisecond"],
        "duration_millisecond": parsed["end_time_millisecond"]
        - parsed["start_time_millisecond"],
        "language": parsed["language"],
        "speaker_pseudonym": speaker,
        "text": parsed["text"],
        "ordering_metadata": (
            "arrival_index",
            "provider_timestamp_millisecond",
            "start_time_millisecond",
            "end_time_millisecond",
        ),
        "provenance": provenance.model_dump(mode="json"),
        "text_authority": ZOOM_DECODER_TEXT_AUTHORITY,
        "review_state": ZOOM_DECODER_REVIEW_STATE,
        "may_confirm_contract": False,
        "may_freeze_contract": False,
        "may_start_measurement": False,
        "may_assign_verdict": False,
    }
    payload_for_segment["segment_id"] = _digest_identifier(
        "zoomsegment_",
        _SEGMENT_DOMAIN,
        {key: value for key, value in payload_for_segment.items() if key != "segment_id"},
    )
    try:
        return ZoomNormalizedTranscriptSegment.model_validate(payload_for_segment)
    except Exception as exc:
        raise ZoomRtmsDecodeError(ZoomDecoderFailureCode.INVALID_RECORD) from exc


def decode_zoom_rtms_transcript_stream(
    stream: bytes,
    *,
    speaker_pseudonyms: Mapping[str, str],
    provenance_by_packet_sha256: Mapping[str, ZoomDecoderProvenance],
) -> tuple[ZoomNormalizedTranscriptSegment, ...]:
    """Decode a bounded newline-delimited packet stream without skipping records."""

    if not isinstance(stream, bytes) or len(stream) < 1 or len(stream) > MAX_STREAM_BYTES:
        raise ZoomRtmsDecodeError(ZoomDecoderFailureCode.STREAM_BOUNDED)
    lines = stream.split(b"\n")
    if lines and lines[-1] == b"":
        lines.pop()
    if not 1 <= len(lines) <= MAX_STREAM_RECORDS or any(not line for line in lines):
        raise ZoomRtmsDecodeError(ZoomDecoderFailureCode.STREAM_BOUNDED)
    segments: list[ZoomNormalizedTranscriptSegment] = []
    seen_packet_digests: set[str] = set()
    for index, line in enumerate(lines, start=1):
        raw = _bounded_packet(line)
        packet_sha256 = hashlib.sha256(raw).hexdigest()
        if packet_sha256 in seen_packet_digests:
            raise ZoomRtmsDecodeError(ZoomDecoderFailureCode.INVALID_RECORD)
        seen_packet_digests.add(packet_sha256)
        provenance = provenance_by_packet_sha256.get(packet_sha256)
        if not isinstance(provenance, ZoomDecoderProvenance):
            raise ZoomRtmsDecodeError(ZoomDecoderFailureCode.INVALID_RECORD)
        segments.append(
            decode_zoom_rtms_transcript_packet(
                raw,
                speaker_pseudonyms=speaker_pseudonyms,
                provenance=provenance,
                arrival_index=index,
            )
        )
    return tuple(segments)


def _bounded_packet(packet: bytes) -> bytes:
    if not isinstance(packet, bytes) or not 1 <= len(packet) <= MAX_PACKET_BYTES:
        raise ZoomRtmsDecodeError(ZoomDecoderFailureCode.INPUT_BOUNDED)
    return packet


def _parse_json_object(raw: bytes) -> dict[str, object]:
    try:
        payload = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_json_constant,
        )
    except DuplicateKeyError as exc:
        raise ZoomRtmsDecodeError(ZoomDecoderFailureCode.DUPLICATE_KEY) from exc
    except Exception as exc:
        raise ZoomRtmsDecodeError(ZoomDecoderFailureCode.MALFORMED_JSON) from exc
    try:
        depth = _json_depth(payload)
    except RecursionError as exc:
        raise ZoomRtmsDecodeError(ZoomDecoderFailureCode.DEPTH_EXCEEDED) from exc
    if depth > MAX_JSON_DEPTH:
        raise ZoomRtmsDecodeError(ZoomDecoderFailureCode.DEPTH_EXCEEDED)
    if not isinstance(payload, dict):
        raise ZoomRtmsDecodeError(ZoomDecoderFailureCode.INVALID_RECORD)
    return payload


def _validate_packet_shape(payload: Mapping[str, object]) -> None:
    keys = set(payload)
    if keys != _ALLOWED_PACKET_KEYS:
        raise ZoomRtmsDecodeError(ZoomDecoderFailureCode.UNSUPPORTED_FIELDS)
    if payload.get("schema_version") != ZOOM_RTMS_PACKET_SCHEMA_VERSION:
        raise ZoomRtmsDecodeError(ZoomDecoderFailureCode.UNKNOWN_VERSION)
    if payload.get("media_type") != ZOOM_RTMS_TRANSCRIPT_MEDIA_TYPE:
        raise ZoomRtmsDecodeError(ZoomDecoderFailureCode.UNSUPPORTED_MEDIA)
    if payload.get("message_type") != ZOOM_RTMS_TRANSCRIPT_MESSAGE_TYPE:
        raise ZoomRtmsDecodeError(ZoomDecoderFailureCode.UNSUPPORTED_MESSAGE)


def _parse_packet_fields(
    payload: Mapping[str, object],
) -> dict[str, Any]:
    user_id = payload.get("user_id")
    language = payload.get("language")
    text = payload.get("data")
    timestamp = payload.get("timestamp")
    start_time = payload.get("start_time")
    end_time = payload.get("end_time")
    if (
        not isinstance(user_id, str)
        or not 1 <= len(user_id) <= 256
        or not isinstance(language, str)
        or not 2 <= len(language) <= MAX_LANGUAGE_CHARACTERS
        or not isinstance(text, str)
        or not 1 <= len(text) <= MAX_TEXT_CHARACTERS
        or not isinstance(timestamp, int)
        or isinstance(timestamp, bool)
        or not isinstance(start_time, str)
        or not isinstance(end_time, str)
    ):
        raise ZoomRtmsDecodeError(ZoomDecoderFailureCode.INVALID_RECORD)
    if not _is_language_tag(language):
        raise ZoomRtmsDecodeError(ZoomDecoderFailureCode.INVALID_RECORD)
    if any(ord(character) < 0x20 and character not in "\t\n\r" for character in text):
        raise ZoomRtmsDecodeError(ZoomDecoderFailureCode.INVALID_TEXT)
    try:
        start_millisecond = _timestamp_string_to_milliseconds(start_time)
        end_millisecond = _timestamp_string_to_milliseconds(end_time)
    except Exception as exc:
        raise ZoomRtmsDecodeError(ZoomDecoderFailureCode.INVALID_TIMESTAMP) from exc
    if not 0 <= timestamp <= MAX_TIMESTAMP_MILLISECONDS:
        raise ZoomRtmsDecodeError(ZoomDecoderFailureCode.INVALID_TIMESTAMP)
    if end_millisecond < start_millisecond:
        raise ZoomRtmsDecodeError(ZoomDecoderFailureCode.INVALID_TIMESTAMP)
    duration = end_millisecond - start_millisecond
    if duration > MAX_SEGMENT_MILLISECONDS:
        raise ZoomRtmsDecodeError(ZoomDecoderFailureCode.INVALID_TIMESTAMP)
    normalized_text = " ".join(unicodedata.normalize("NFC", text).split())
    if not normalized_text:
        raise ZoomRtmsDecodeError(ZoomDecoderFailureCode.INVALID_TEXT)
    return {
        "user_id": user_id,
        "language": language,
        "text": normalized_text,
        "timestamp": timestamp,
        "start_time_millisecond": start_millisecond,
        "end_time_millisecond": end_millisecond,
    }


def _timestamp_string_to_milliseconds(value: str) -> int:
    if not 1 <= len(value) <= 64 or not _UTC_TIMESTAMP_PATTERN.fullmatch(value):
        raise ValueError
    parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError
    milliseconds = int(parsed.timestamp() * 1000)
    if not 0 <= milliseconds <= MAX_TIMESTAMP_MILLISECONDS:
        raise ValueError
    return milliseconds


def _is_language_tag(value: str) -> bool:
    return len(value) <= MAX_LANGUAGE_CHARACTERS and bool(
        _LANGUAGE_TAG_PATTERN.fullmatch(value)
    )


class DuplicateKeyError(ValueError):
    pass


def _reject_duplicate_keys(pairs: Sequence[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateKeyError
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    del value
    raise ValueError


def _json_depth(value: object) -> int:
    if isinstance(value, Mapping):
        return 1 + max((_json_depth(child) for child in value.values()), default=0)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return 1 + max((_json_depth(child) for child in value), default=0)
    return 0


def _model_payload_without(model: _DecoderModel, field: str) -> dict[str, Any]:
    payload = model.model_dump(mode="json")
    payload.pop(field, None)
    return payload


def _digest_identifier(prefix: str, domain: bytes, payload: Mapping[str, object]) -> str:
    return prefix + hashlib.sha256(domain + canonical_json_bytes(payload)).hexdigest()


__all__ = [
    "MAX_JSON_DEPTH",
    "MAX_PACKET_BYTES",
    "MAX_STREAM_BYTES",
    "MAX_STREAM_RECORDS",
    "ZOOM_DECODER_REVIEW_STATE",
    "ZOOM_DECODER_TEXT_AUTHORITY",
    "ZOOM_RTMS_DECODER_VERSION",
    "ZOOM_RTMS_PACKET_SCHEMA_VERSION",
    "ZOOM_RTMS_TRANSCRIPT_MEDIA_TYPE",
    "ZOOM_RTMS_TRANSCRIPT_MESSAGE_TYPE",
    "ZoomDecoderFailureCode",
    "ZoomDecoderProvenance",
    "ZoomNormalizedTranscriptSegment",
    "ZoomRtmsDecodeError",
    "decode_zoom_rtms_transcript_packet",
    "decode_zoom_rtms_transcript_stream",
]
