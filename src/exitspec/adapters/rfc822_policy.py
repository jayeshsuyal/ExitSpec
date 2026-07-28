"""Pure, fail-closed RFC822 policy helpers for synthetic Wave 2 fixtures."""

from __future__ import annotations

import base64
import binascii
import quopri
import re
import unicodedata
from dataclasses import dataclass
from enum import Enum
from pathlib import PurePath
from typing import Never, Sequence


RAW_MESSAGE_BYTES_MAX = 131_072
HEADER_COUNT_MAX = 100
UNFOLDED_HEADER_BYTES_MAX = 8_192
MIME_DEPTH_MAX = 8
MIME_LEAF_PARTS_MAX = 20
INLINE_BODY_BYTES_MAX = 4_096
ATTACHMENT_COUNT_MAX = 3
ATTACHMENT_BYTES_MAX = 32_768
ATTACHMENT_TOTAL_BYTES_MAX = 65_536
ATTACHMENT_FILENAME_BYTES_MAX = 128


class Rfc822PreparationCode(str, Enum):
    SOURCE_NOT_APPROVED = "source_not_approved"
    FIXTURE_DIGEST_MISMATCH = "fixture_digest_mismatch"
    RAW_MESSAGE_TOO_LARGE = "raw_message_too_large"
    TOO_MANY_HEADERS = "too_many_headers"
    HEADER_TOO_LARGE = "header_too_large"
    MIME_TOO_DEEP = "mime_too_deep"
    TOO_MANY_MIME_PARTS = "too_many_mime_parts"
    MALFORMED_TRANSFER_ENCODING = "malformed_transfer_encoding"
    UNSUPPORTED_CHARSET = "unsupported_charset"
    SENDER_AMBIGUOUS = "sender_ambiguous"
    MISSING_MESSAGE_ID = "missing_message_id"
    MISSING_BODY = "missing_body"
    BODY_TOO_LARGE = "body_too_large"
    ALTERNATIVE_DISAGREEMENT = "alternative_disagreement"
    UNSUPPORTED_ATTACHMENT = "unsupported_attachment"
    UNSAFE_ATTACHMENT_FILENAME = "unsafe_attachment_filename"
    ATTACHMENT_TOO_LARGE = "attachment_too_large"
    ATTACHMENT_TOTAL_TOO_LARGE = "attachment_total_too_large"
    TOO_MANY_ATTACHMENTS = "too_many_attachments"
    ATTACHMENT_DECODE_FAILED = "attachment_decode_failed"
    ATTACHMENT_REDACTION_FAILED = "attachment_redaction_failed"
    REDACTION_FAILED = "redaction_failed"
    SOURCE_LINK_VIOLATION = "source_link_violation"


_SAFE_ACTIONS = {
    Rfc822PreparationCode.SOURCE_NOT_APPROVED: (
        "Use a manifest-approved synthetic fixture with the exact required marker."
    ),
    Rfc822PreparationCode.FIXTURE_DIGEST_MISMATCH: (
        "Restore the exact manifest-approved fixture bytes."
    ),
    Rfc822PreparationCode.RAW_MESSAGE_TOO_LARGE: (
        "Reduce the synthetic message below the frozen raw-byte limit."
    ),
    Rfc822PreparationCode.TOO_MANY_HEADERS: (
        "Reduce the synthetic message below the frozen header-count limit."
    ),
    Rfc822PreparationCode.HEADER_TOO_LARGE: (
        "Shorten every unfolded header below the frozen byte limit."
    ),
    Rfc822PreparationCode.MIME_TOO_DEEP: (
        "Flatten the synthetic MIME structure below the frozen depth limit."
    ),
    Rfc822PreparationCode.TOO_MANY_MIME_PARTS: (
        "Reduce the synthetic MIME structure below the frozen part-count limit."
    ),
    Rfc822PreparationCode.MALFORMED_TRANSFER_ENCODING: (
        "Provide a correctly transfer-encoded manifest-approved fixture."
    ),
    Rfc822PreparationCode.UNSUPPORTED_CHARSET: (
        "Use UTF-8 or US-ASCII text in the synthetic fixture."
    ),
    Rfc822PreparationCode.SENDER_AMBIGUOUS: (
        "Correct the synthetic source to contain exactly one From field."
    ),
    Rfc822PreparationCode.MISSING_MESSAGE_ID: (
        "Provide an approved fixture with exactly one syntactically valid Message-ID."
    ),
    Rfc822PreparationCode.MISSING_BODY: (
        "Provide an approved fixture with one non-empty inline text body."
    ),
    Rfc822PreparationCode.BODY_TOO_LARGE: (
        "Shorten the synthetic source under the frozen limit."
    ),
    Rfc822PreparationCode.ALTERNATIVE_DISAGREEMENT: (
        "Provide one authoritative plain-text body with no conflicting alternative."
    ),
    Rfc822PreparationCode.UNSUPPORTED_ATTACHMENT: (
        "Remove the attachment or use a manifest-allowed textual type."
    ),
    Rfc822PreparationCode.UNSAFE_ATTACHMENT_FILENAME: (
        "Use a basename-only synthetic attachment filename."
    ),
    Rfc822PreparationCode.ATTACHMENT_TOO_LARGE: (
        "Reduce every decoded attachment below the frozen per-file limit."
    ),
    Rfc822PreparationCode.ATTACHMENT_TOTAL_TOO_LARGE: (
        "Reduce total decoded attachment bytes below the frozen limit."
    ),
    Rfc822PreparationCode.TOO_MANY_ATTACHMENTS: (
        "Reduce the message to at most three allowed attachments."
    ),
    Rfc822PreparationCode.ATTACHMENT_DECODE_FAILED: (
        "Provide a correctly encoded manifest-approved attachment."
    ),
    Rfc822PreparationCode.ATTACHMENT_REDACTION_FAILED: (
        "Keep the source local and repair attachment redaction."
    ),
    Rfc822PreparationCode.REDACTION_FAILED: (
        "Keep the source local and repair the redaction configuration."
    ),
    Rfc822PreparationCode.SOURCE_LINK_VIOLATION: (
        "Regenerate candidates from the exact current redacted source version."
    ),
}


class Rfc822PreparationError(ValueError):
    """Content-free typed refusal at the synthetic RFC822 boundary."""

    __slots__ = ("code", "safe_message", "retryable", "next_action")

    def __init__(self, code: Rfc822PreparationCode) -> None:
        self.code = code
        self.safe_message = code.value
        self.retryable = False
        self.next_action = _SAFE_ACTIONS[code]
        super().__init__(code.value)

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(code={self.code.value!r}, "
            "retryable=False)"
        )


def _fail(code: Rfc822PreparationCode) -> Never:
    raise Rfc822PreparationError(code) from None


def validate_raw_size(observed_bytes: int) -> None:
    if type(observed_bytes) is not int or observed_bytes < 0:
        _fail(Rfc822PreparationCode.RAW_MESSAGE_TOO_LARGE)
    if observed_bytes > RAW_MESSAGE_BYTES_MAX:
        _fail(Rfc822PreparationCode.RAW_MESSAGE_TOO_LARGE)


def validate_header_budget(
    header_count: int,
    max_unfolded_bytes: int,
) -> None:
    if type(header_count) is not int or header_count < 0:
        _fail(Rfc822PreparationCode.TOO_MANY_HEADERS)
    if header_count > HEADER_COUNT_MAX:
        _fail(Rfc822PreparationCode.TOO_MANY_HEADERS)
    if type(max_unfolded_bytes) is not int or max_unfolded_bytes < 0:
        _fail(Rfc822PreparationCode.HEADER_TOO_LARGE)
    if max_unfolded_bytes > UNFOLDED_HEADER_BYTES_MAX:
        _fail(Rfc822PreparationCode.HEADER_TOO_LARGE)


def validate_mime_shape(depth: int, leaf_part_count: int) -> None:
    if type(depth) is not int or depth < 0 or depth > MIME_DEPTH_MAX:
        _fail(Rfc822PreparationCode.MIME_TOO_DEEP)
    if (
        type(leaf_part_count) is not int
        or leaf_part_count < 0
        or leaf_part_count > MIME_LEAF_PARTS_MAX
    ):
        _fail(Rfc822PreparationCode.TOO_MANY_MIME_PARTS)


def validate_attachment_sizes(decoded_sizes: Sequence[int]) -> None:
    sizes = tuple(decoded_sizes)
    if len(sizes) > ATTACHMENT_COUNT_MAX:
        _fail(Rfc822PreparationCode.TOO_MANY_ATTACHMENTS)
    if any(type(size) is not int or size < 0 for size in sizes):
        _fail(Rfc822PreparationCode.ATTACHMENT_DECODE_FAILED)
    if any(size > ATTACHMENT_BYTES_MAX for size in sizes):
        _fail(Rfc822PreparationCode.ATTACHMENT_TOO_LARGE)
    if sum(sizes) > ATTACHMENT_TOTAL_BYTES_MAX:
        _fail(Rfc822PreparationCode.ATTACHMENT_TOTAL_TOO_LARGE)


def validate_attachment_filename(filename: str) -> None:
    if (
        type(filename) is not str
        or not filename
        or "\x00" in filename
        or PurePath(filename).name != filename
        or "/" in filename
        or "\\" in filename
        or len(filename.encode("utf-8")) > ATTACHMENT_FILENAME_BYTES_MAX
    ):
        _fail(Rfc822PreparationCode.UNSAFE_ATTACHMENT_FILENAME)


def _decode_transfer_payload(
    payload: str,
    transfer_encoding: str | None,
    *,
    attachment: bool,
) -> bytes:
    failure = (
        Rfc822PreparationCode.ATTACHMENT_DECODE_FAILED
        if attachment
        else Rfc822PreparationCode.MALFORMED_TRANSFER_ENCODING
    )
    if type(payload) is not str:
        _fail(failure)
    encoding = (transfer_encoding or "7bit").strip().lower()
    if encoding == "base64":
        compact = re.sub(r"[ \t\r\n]+", "", payload)
        if not compact or not compact.isascii():
            _fail(failure)
        try:
            return base64.b64decode(compact.encode("ascii"), validate=True)
        except (UnicodeError, binascii.Error, ValueError):
            pass
        _fail(failure)
    if encoding == "quoted-printable":
        if re.search(r"=(?![0-9A-Fa-f]{2}|(?:\r\n|\n|\r))", payload):
            _fail(failure)
        try:
            return quopri.decodestring(payload.encode("ascii"))
        except (UnicodeError, binascii.Error, ValueError):
            pass
        _fail(failure)
    if encoding in {"7bit", "8bit", "binary", ""}:
        try:
            return payload.encode("utf-8", errors="surrogateescape")
        except UnicodeError:
            pass
        _fail(failure)
    _fail(failure)


def _strict_decode_text(
    payload: bytes,
    charset: str | None,
    *,
    attachment: bool,
) -> str:
    """Strictly decode already transfer-decoded synthetic text."""

    normalized_charset = (charset or "utf-8").strip().lower()
    normalized_charset = normalized_charset.replace("_", "-")
    if normalized_charset not in {"utf-8", "us-ascii"}:
        _fail(Rfc822PreparationCode.UNSUPPORTED_CHARSET)
    try:
        return payload.decode(normalized_charset, errors="strict")
    except UnicodeError:
        pass
    _fail(
        Rfc822PreparationCode.ATTACHMENT_DECODE_FAILED
        if attachment
        else Rfc822PreparationCode.MALFORMED_TRANSFER_ENCODING
    )


def normalize_text(value: str) -> str:
    if type(value) is not str:
        _fail(Rfc822PreparationCode.REDACTION_FAILED)
    normalized = value.replace("\r\n", "\n").replace("\r", "\n")
    normalized = unicodedata.normalize("NFC", normalized)
    normalized = "\n".join(
        line.rstrip(" \t") for line in normalized.split("\n")
    )
    normalized = normalized.strip("\n")
    return normalized + "\n"


_SECRET_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_])api_key=[A-Za-z0-9._-]+(?![A-Za-z0-9._-])",
    re.ASCII,
)
_EMAIL_PATTERN = re.compile(
    r"(?<![A-Z0-9._%+-])[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}"
    r"(?![A-Z0-9.-])",
    re.ASCII | re.IGNORECASE,
)
_PHONE_PATTERN = re.compile(
    r"(?<![0-9])(?:\+?1[ .-]?)?\(?[2-9][0-9]{2}\)?[ .-]"
    r"[0-9]{3}[ .-][0-9]{4}(?![0-9])",
    re.ASCII,
)


@dataclass(frozen=True)
class RedactionResult:
    value: str
    customer_term: int
    email: int
    phone: int
    secret: int


def _ordered_customer_terms(customer_terms: Sequence[str]) -> tuple[str, ...]:
    unique: dict[str, str] = {}
    for raw_term in customer_terms:
        if type(raw_term) is not str:
            _fail(Rfc822PreparationCode.REDACTION_FAILED)
        term = unicodedata.normalize("NFC", raw_term)
        if not term:
            _fail(Rfc822PreparationCode.REDACTION_FAILED)
        unique.setdefault(term.casefold(), term)
    return tuple(
        sorted(
            unique.values(),
            key=lambda term: (-len(term), term.casefold()),
        )
    )


def redact_rfc822_value(
    value: str,
    customer_terms: Sequence[str],
) -> RedactionResult:
    """Apply the exact ordered Wave 2 redaction rules to NFC text."""

    result: RedactionResult | None = None
    try:
        redacted = unicodedata.normalize("NFC", value)
        redacted, secret = _SECRET_PATTERN.subn("[SECRET]", redacted)
        customer_term = 0
        for term in _ordered_customer_terms(customer_terms):
            redacted, count = re.subn(
                re.escape(term),
                "[CUSTOMER_TERM]",
                redacted,
                flags=re.IGNORECASE,
            )
            customer_term += count
        redacted, email = _EMAIL_PATTERN.subn("[EMAIL]", redacted)
        redacted, phone = _PHONE_PATTERN.subn("[PHONE]", redacted)
        result = RedactionResult(
            value=redacted,
            customer_term=customer_term,
            email=email,
            phone=phone,
            secret=secret,
        )
    except Rfc822PreparationError:
        raise
    except (TypeError, ValueError, UnicodeError, re.error):
        pass
    if result is None:
        _fail(Rfc822PreparationCode.REDACTION_FAILED)
    return result


__all__ = [
    "ATTACHMENT_BYTES_MAX",
    "ATTACHMENT_COUNT_MAX",
    "ATTACHMENT_FILENAME_BYTES_MAX",
    "ATTACHMENT_TOTAL_BYTES_MAX",
    "HEADER_COUNT_MAX",
    "INLINE_BODY_BYTES_MAX",
    "MIME_DEPTH_MAX",
    "MIME_LEAF_PARTS_MAX",
    "RAW_MESSAGE_BYTES_MAX",
    "Rfc822PreparationCode",
    "Rfc822PreparationError",
    "RedactionResult",
    "UNFOLDED_HEADER_BYTES_MAX",
    "normalize_text",
    "redact_rfc822_value",
    "validate_attachment_filename",
    "validate_attachment_sizes",
    "validate_header_budget",
    "validate_mime_shape",
    "validate_raw_size",
]
