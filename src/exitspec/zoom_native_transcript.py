"""Decode the documented native Meetings RTMS transcript envelope.

This is an in-memory parsing boundary, not an authentication or egress API.
The runtime must authenticate the stream and consent before calling it. Native
provider facts remain distinct from the historical ExitSpec fixture envelope.
See docs/ZOOM_NATIVE_TRANSCRIPT_SPEC.md for the pinned subset and limitations.
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from pydantic import ConfigDict, Field

from .canonical import canonical_json_bytes
from .models import SHA256_PATTERN, FrozenExitSpecModel

NATIVE_TRANSCRIPT_VERSION = "exitspec.zoom-native-transcript/1.0"
MAX_NATIVE_PACKET_BYTES = 64 * 1024
MAX_NATIVE_TEXT_CHARACTERS = 8 * 1024
MAX_NATIVE_ARRIVALS = 256
MAX_NATIVE_PARTICIPANTS = 2
# Interoperable integer domain; no time-unit conversion is performed.
MAX_NATIVE_INTEGER = (1 << 53) - 1
_DIGEST_DOMAIN = b"exitspec-zoom-native-transcript-v1\x00"
_CONTENT_KEYS = frozenset(
    {"user_id", "user_name", "start_time", "end_time", "timestamp", "language", "data"}
)


class ZoomNativeTranscriptError(ValueError):
    """A refusal that contains neither provider fields nor source content."""

    def __init__(self) -> None:
        super().__init__("The native Zoom transcript packet was rejected.")


class ZoomNativeStreamBinding(FrozenExitSpecModel):
    """Runtime-supplied binding metadata; possession is NOT authorization.

    Meeting/stream bindings must be keyed digests made by the authenticated
    runtime, not unsalted hashes of low-entropy provider identifiers. No field
    here is an operator capability. These values do not attest a live call.
    """

    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, hide_input_in_errors=True
    )
    schema_version: Literal["exitspec.zoom-native-stream-binding/1.0"] = (
        "exitspec.zoom-native-stream-binding/1.0"
    )
    process_generation_sha256: str = Field(pattern=SHA256_PATTERN)
    session_id: str = Field(pattern=r"^zoomsess_[a-f0-9]{64}$")
    poc_id: str = Field(pattern=r"^poc_[a-z0-9][a-z0-9_-]{2,63}$")
    consent_receipt_sha256: str = Field(pattern=SHA256_PATTERN)
    meeting_binding_sha256: str = Field(pattern=SHA256_PATTERN)
    stream_binding_sha256: str = Field(pattern=SHA256_PATTERN)
    code_revision: str = Field(pattern=r"^[a-f0-9]{40}$")
    source_content_classification: Literal["SYNTHETIC_REQUIREMENTS_ONLY"] = (
        "SYNTHETIC_REQUIREMENTS_ONLY"
    )


@dataclass(frozen=True, slots=True, repr=False)
class ZoomNativeTranscript:
    """Private normalized source; never a browser receipt or measurement.

    Timing and language retain the native integers without guessed units or
    language tags. Arrival order is local, not a provider sequence/finality bit.
    Neither this object nor its digest proves transport authentication.
    """

    binding: ZoomNativeStreamBinding
    packet_sha256: str
    normalized_sha256: str
    arrival_index: int
    provider_start_time: int
    provider_end_time: int
    provider_timestamp: int
    provider_timestamp_unit: Literal["UNSPECIFIED_UNIX"]
    provider_language_code: int
    speaker_pseudonym: Literal["SPEAKER_1", "SPEAKER_2"]
    text: str

    def __repr__(self) -> str:
        return "ZoomNativeTranscript(private_material=<redacted>)"


def _reject_packet() -> None:
    raise ZoomNativeTranscriptError()


def _object_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            _reject_packet()
        result[key] = value
    return result


def _constant(_: str) -> None:
    _reject_packet()


def _integer(value: object) -> int:
    if type(value) is not int or not 0 <= value <= MAX_NATIVE_INTEGER:
        _reject_packet()
    return value


def _decode(
    packet: bytes,
    binding: ZoomNativeStreamBinding,
    speaker_pseudonyms: Mapping[int, str],
    arrival_index: int,
) -> ZoomNativeTranscript:
    if type(packet) is not bytes or not 1 <= len(packet) <= MAX_NATIVE_PACKET_BYTES:
        _reject_packet()
    if type(binding) is not ZoomNativeStreamBinding:
        _reject_packet()
    # Revalidate even a model_construct/model_copy instance supplied by a caller.
    binding = ZoomNativeStreamBinding.model_validate(binding.model_dump(warnings=False))
    if type(arrival_index) is not int or not 1 <= arrival_index <= MAX_NATIVE_ARRIVALS:
        _reject_packet()
    if type(speaker_pseudonyms) is not dict:
        _reject_packet()
    if not 1 <= len(speaker_pseudonyms) <= MAX_NATIVE_PARTICIPANTS:
        _reject_packet()
    speakers = dict(speaker_pseudonyms)
    for user_id, speaker in speakers.items():
        _integer(user_id)
        if user_id > (1 << 32) - 1:
            _reject_packet()
        if type(speaker) is not str or speaker not in {"SPEAKER_1", "SPEAKER_2"}:
            _reject_packet()
    if len(set(speakers.values())) != len(speakers):
        _reject_packet()

    payload = json.loads(
        packet.decode("utf-8"),
        object_pairs_hook=_object_pairs,
        parse_constant=_constant,
    )
    if type(payload) is not dict or set(payload) != {"msg_type", "content"}:
        _reject_packet()
    if type(payload["msg_type"]) is not int or payload["msg_type"] != 17:
        _reject_packet()
    content = payload["content"]
    if type(content) is not dict or set(content) != _CONTENT_KEYS:
        _reject_packet()
    user_id = _integer(content["user_id"])
    if user_id not in speakers:
        _reject_packet()
    name = content["user_name"]
    if type(name) is not str or not 1 <= len(name) <= 256:
        _reject_packet()
    # Validate UTF-8 even for the discarded name, without retaining it.
    name.encode("utf-8")
    start = _integer(content["start_time"])
    end = _integer(content["end_time"])
    timestamp = _integer(content["timestamp"])
    language = _integer(content["language"])
    if end < start:
        _reject_packet()
    text = content["data"]
    if type(text) is not str or not 1 <= len(text) <= MAX_NATIVE_TEXT_CHARACTERS:
        _reject_packet()
    text.encode("utf-8")
    if any(ord(char) < 0x20 and char not in "\t\n\r" for char in text):
        _reject_packet()
    text = " ".join(unicodedata.normalize("NFC", text).split())
    if not text or len(text) > MAX_NATIVE_TEXT_CHARACTERS:
        _reject_packet()
    packet_sha256 = hashlib.sha256(packet).hexdigest()
    # Arrival is intentionally excluded: redelivery of identical bytes within
    # the same authenticated binding has the same content identity.
    normalized = {
        "schema_version": NATIVE_TRANSCRIPT_VERSION,
        "binding": binding.model_dump(mode="json"),
        "packet_sha256": packet_sha256,
        "provider_start_time": start,
        "provider_end_time": end,
        "provider_timestamp": timestamp,
        "provider_timestamp_unit": "UNSPECIFIED_UNIX",
        "provider_language_code": language,
        "speaker_pseudonym": speakers[user_id],
        "text": text,
    }
    digest = hashlib.sha256(
        _DIGEST_DOMAIN + canonical_json_bytes(normalized)
    ).hexdigest()
    return ZoomNativeTranscript(
        binding=binding,
        packet_sha256=packet_sha256,
        normalized_sha256=digest,
        arrival_index=arrival_index,
        provider_start_time=start,
        provider_end_time=end,
        provider_timestamp=timestamp,
        provider_timestamp_unit="UNSPECIFIED_UNIX",
        provider_language_code=language,
        speaker_pseudonym=speakers[user_id],
        text=text,
    )


def decode_zoom_native_transcript(
    packet: bytes,
    *,
    binding: ZoomNativeStreamBinding,
    speaker_pseudonyms: Mapping[int, str],
    arrival_index: int,
) -> ZoomNativeTranscript:
    """Decode one bounded native packet without network, disk or log effects.

    Caller must enforce authenticated participant mapping, aggregate bounds,
    replay/session lifecycle and finalization. Text is untrusted and unredacted;
    attachment must still pass the common source redaction spine.
    """

    try:
        return _decode(packet, binding, speaker_pseudonyms, arrival_index)
    except (ValueError, TypeError, RecursionError):
        # Raise outside the exception handler so __context__ cannot retain a
        # parser/validation exception containing private packet values.
        rejected = True
    if rejected:
        raise ZoomNativeTranscriptError()
