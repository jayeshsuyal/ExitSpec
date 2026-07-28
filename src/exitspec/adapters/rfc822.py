"""Synthetic-only RFC822 preparation facade for the frozen Wave 2 contract."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from email import policy
from email.message import EmailMessage, Message
from email.parser import BytesParser
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Sequence

from exitspec.adapters.rfc822_candidates import extract_candidate_matches
from exitspec.adapters.rfc822_policy import (
    INLINE_BODY_BYTES_MAX,
    RAW_MESSAGE_BYTES_MAX,
    Rfc822PreparationCode,
    Rfc822PreparationError,
    _decode_transfer_payload,
    _fail,
    normalize_text,
    redact_rfc822_value,
    _strict_decode_text,
    validate_attachment_filename,
    validate_attachment_sizes,
    validate_header_budget,
    validate_mime_shape,
    validate_raw_size,
)
from exitspec.demo_data import SupportAgentEmailPaths
from exitspec.source_models import (
    MANIFEST_ID,
    MANIFEST_VERSION,
    ApprovedSyntheticFixture,
    PartKind,
    PreparedCandidateDraft,
    PreparedSourceEnvelope,
    PreparedSourceImport,
    RedactedHeaders,
    RedactionCounts,
    RedactionSummary,
    SourceMessage,
    SourcePart,
    compute_message_key,
    compute_redacted_header_sha256,
    compute_source_id,
    normalize_message_id,
)


_PINNED_MANIFEST_FILENAME = "wave-2-acceptance-v1.json"
_PINNED_MANIFEST_SHA256 = (
    "aa514787eb6b14a93216682d702fc29a32d630eb1a91a16dae6ce0873a268ae2"
)
_PINNED_MANIFEST_BYTES = 72_428
_CASE_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_MESSAGE_ID_TOKEN = re.compile(r"<[^<>]*>")
_ATEXT = re.compile(r"^[A-Za-z0-9!#$%&'*+\-/=?^_`{|}~]+$")
_DOMAIN_LABEL = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$"
)
_ALLOWED_HTML_TAGS = {"html", "body", "p", "br"}


def _read_bounded(path: Path, maximum_bytes: int) -> bytes:
    """Read at most one byte beyond a trusted boundary."""

    with path.open("rb") as handle:
        return handle.read(maximum_bytes + 1)


class _ConservativeHtmlText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.fragments: list[str] = []
        self.uncertain = False

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        if tag.lower() not in _ALLOWED_HTML_TAGS or attrs:
            self.uncertain = True
        if tag.lower() in {"p", "br"}:
            self.fragments.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() not in _ALLOWED_HTML_TAGS:
            self.uncertain = True
        if tag.lower() == "p":
            self.fragments.append("\n")

    def handle_data(self, data: str) -> None:
        self.fragments.append(data)

    def handle_comment(self, data: str) -> None:
        self.uncertain = True

    def handle_pi(self, data: str) -> None:
        self.uncertain = True

    def handle_decl(self, decl: str) -> None:
        self.uncertain = True

    def unknown_decl(self, data: str) -> None:
        self.uncertain = True


def _approved_fixture_snapshot(
    resources: SupportAgentEmailPaths,
    fixture_case_id: str,
) -> tuple[bytes, dict[str, Any]]:
    """Read one pinned authorization snapshot and its selected fixture once."""

    if (
        not isinstance(resources, SupportAgentEmailPaths)
        or type(fixture_case_id) is not str
        or _CASE_ID.fullmatch(fixture_case_id) is None
    ):
        _fail(Rfc822PreparationCode.SOURCE_NOT_APPROVED)

    try:
        root_path = resources.root
        manifest_path = resources.manifest
        fixture_path = resources.fixture_for(fixture_case_id)
        if (
            root_path.is_symlink()
            or not root_path.is_dir()
            or manifest_path.is_symlink()
            or not manifest_path.is_file()
            or fixture_path.is_symlink()
            or not fixture_path.is_file()
        ):
            _fail(Rfc822PreparationCode.SOURCE_NOT_APPROVED)
        root = root_path.resolve(strict=True)
        expected_manifest = root / _PINNED_MANIFEST_FILENAME
        expected_fixture = root / f"{fixture_case_id}.eml"
        if (
            manifest_path != expected_manifest
            or manifest_path.resolve(strict=True) != expected_manifest
            or fixture_path != expected_fixture
            or fixture_path.resolve(strict=True) != expected_fixture
            or fixture_path.parent != root
        ):
            _fail(Rfc822PreparationCode.SOURCE_NOT_APPROVED)

        manifest_bytes = _read_bounded(
            manifest_path,
            _PINNED_MANIFEST_BYTES,
        )
        if (
            len(manifest_bytes) != _PINNED_MANIFEST_BYTES
            or hashlib.sha256(manifest_bytes).hexdigest()
            != _PINNED_MANIFEST_SHA256
        ):
            _fail(Rfc822PreparationCode.SOURCE_NOT_APPROVED)
        manifest = json.loads(manifest_bytes)
        if (
            not isinstance(manifest, dict)
            or manifest.get("manifest_id") != MANIFEST_ID
            or manifest.get("manifest_version") != MANIFEST_VERSION
            or manifest.get("status") != "FROZEN"
        ):
            _fail(Rfc822PreparationCode.SOURCE_NOT_APPROVED)
        records = manifest["fixture_set"]["fixtures"]
        record = next(
            item
            for item in records
            if item.get("case_id") == fixture_case_id
        )
        if (
            not isinstance(record, dict)
            or record.get("path")
            != f"examples/support-agent/email/{fixture_case_id}.eml"
            or type(record.get("raw_bytes")) is not int
            or not isinstance(record.get("sha256"), str)
            or not isinstance(record.get("customer_terms"), list)
        ):
            _fail(Rfc822PreparationCode.SOURCE_NOT_APPROVED)

        raw_message = _read_bounded(fixture_path, RAW_MESSAGE_BYTES_MAX)
        validate_raw_size(len(raw_message))
        _validate_fixture_observation(
            observed_bytes=len(raw_message),
            observed_sha256=hashlib.sha256(raw_message).hexdigest(),
            expected_bytes=record["raw_bytes"],
            expected_sha256=record["sha256"],
        )
        return raw_message, record
    except Rfc822PreparationError:
        raise
    except (KeyError, StopIteration, OSError, TypeError, ValueError):
        _fail(Rfc822PreparationCode.SOURCE_NOT_APPROVED)


def _raw_header_budget(raw_message: bytes) -> tuple[int, int]:
    lines: list[bytes] = []
    line_start = 0
    index = 0
    while index < len(raw_message):
        byte = raw_message[index]
        if byte == 13:
            line_end = index + (
                2
                if index + 1 < len(raw_message)
                and raw_message[index + 1] == 10
                else 1
            )
            lines.append(raw_message[line_start:line_end])
            line_start = line_end
            index = line_end
            continue
        if byte == 10:
            line_end = index + 1
            lines.append(raw_message[line_start:line_end])
            line_start = line_end
        index += 1
    if line_start < len(raw_message):
        lines.append(raw_message[line_start:])

    logical: list[bytearray] = []
    for physical_line in lines:
        if physical_line.endswith(b"\r\n"):
            line = physical_line[:-2]
        elif physical_line.endswith((b"\r", b"\n")):
            line = physical_line[:-1]
        else:
            line = physical_line
        if not line:
            break
        if line.startswith((b" ", b"\t")):
            if not logical:
                _fail(Rfc822PreparationCode.HEADER_TOO_LARGE)
            logical[-1].extend(line)
        else:
            if b":" not in line:
                _fail(Rfc822PreparationCode.HEADER_TOO_LARGE)
            logical.append(bytearray(line))
    return len(logical), max((len(line) for line in logical), default=0)


def _strict_parse(raw_message: bytes) -> EmailMessage:
    try:
        parsed = BytesParser(policy=policy.default).parsebytes(raw_message)
    except Exception:
        _fail(Rfc822PreparationCode.MALFORMED_TRANSFER_ENCODING)
    for part in parsed.walk():
        if part.defects:
            _fail(Rfc822PreparationCode.MALFORMED_TRANSFER_ENCODING)
    if not isinstance(parsed, EmailMessage):
        _fail(Rfc822PreparationCode.MALFORMED_TRANSFER_ENCODING)
    return parsed


def _single_exact_header(
    message: Message,
    name: str,
    failure: Rfc822PreparationCode,
) -> str:
    values = message.get_all(name, [])
    if len(values) != 1:
        _fail(failure)
    return str(values[0])


def _validate_synthetic_marker(values: Sequence[object]) -> None:
    if len(values) != 1 or str(values[0]) != "true":
        _fail(Rfc822PreparationCode.SOURCE_NOT_APPROVED)


def _validate_fixture_observation(
    *,
    observed_bytes: int,
    observed_sha256: str,
    expected_bytes: object,
    expected_sha256: object,
) -> None:
    if (
        observed_bytes != expected_bytes
        or observed_sha256 != expected_sha256
    ):
        _fail(Rfc822PreparationCode.FIXTURE_DIGEST_MISMATCH)


def _validate_source_link(
    redacted_text: str,
    start_byte: int,
    end_byte: int,
    quote_sha256: str,
) -> None:
    encoded = redacted_text.encode("utf-8")
    if (
        type(start_byte) is not int
        or type(end_byte) is not int
        or start_byte < 0
        or end_byte <= start_byte
        or end_byte > len(encoded)
    ):
        _fail(Rfc822PreparationCode.SOURCE_LINK_VIOLATION)
    quote = encoded[start_byte:end_byte]
    if (
        not quote
        or hashlib.sha256(quote).hexdigest() != quote_sha256
    ):
        _fail(Rfc822PreparationCode.SOURCE_LINK_VIOLATION)


def _normalized_valid_message_id(value: str) -> str | None:
    if type(value) is not str or not value.isascii():
        return None
    match = _MESSAGE_ID_TOKEN.fullmatch(value.strip())
    if match is None:
        return None
    inner = match.group(0)[1:-1]
    if (
        not inner
        or len(inner.encode("ascii")) > 254
        or inner.count("@") != 1
    ):
        return None
    local, domain = inner.split("@", 1)
    if (
        not local
        or not domain
        or len(local.encode("ascii")) > 64
        or len(domain.encode("ascii")) > 253
    ):
        return None
    local_parts = local.split(".")
    if (
        any(not part for part in local_parts)
        or any(_ATEXT.fullmatch(part) is None for part in local_parts)
    ):
        return None
    domain_labels = domain.split(".")
    if (
        any(not label or len(label) > 63 for label in domain_labels)
        or any(_DOMAIN_LABEL.fullmatch(label) is None for label in domain_labels)
    ):
        return None
    try:
        return normalize_message_id(value)
    except (TypeError, ValueError, UnicodeError):
        return None


def _resolve_identity(message: Message) -> tuple[str, str]:
    current = _normalized_valid_message_id(
        _single_exact_header(
            message,
            "Message-ID",
            Rfc822PreparationCode.MISSING_MESSAGE_ID,
        )
    )
    if current is None:
        _fail(Rfc822PreparationCode.MISSING_MESSAGE_ID)

    root: str | None = None
    for value in message.get_all("References", []):
        for match in _MESSAGE_ID_TOKEN.finditer(str(value)):
            candidate = _normalized_valid_message_id(match.group(0))
            if candidate is not None:
                root = candidate
                break
        if root is not None:
            break
    if root is None:
        replies: list[str] = []
        for value in message.get_all("In-Reply-To", []):
            replies.extend(
                match.group(0)
                for match in _MESSAGE_ID_TOKEN.finditer(str(value))
            )
        valid_replies = [
            candidate
            for value in replies
            if (candidate := _normalized_valid_message_id(value)) is not None
        ]
        if len(valid_replies) == 1:
            root = valid_replies[0]
    return current, root or current


def _mime_shape(message: Message) -> tuple[int, int]:
    maximum_depth = 0
    leaf_count = 0

    def visit(part: Message, depth: int) -> None:
        nonlocal maximum_depth, leaf_count
        maximum_depth = max(maximum_depth, depth)
        if part.is_multipart():
            payload = part.get_payload()
            if not isinstance(payload, list):
                _fail(Rfc822PreparationCode.MALFORMED_TRANSFER_ENCODING)
            for child in payload:
                visit(child, depth + 1)
        else:
            leaf_count += 1

    visit(message, 0)
    return maximum_depth, leaf_count


def _classify_leaves(
    message: Message,
) -> tuple[list[Message], list[Message], list[Message]]:
    bodies: list[Message] = []
    html_alternatives: list[Message] = []
    attachments: list[Message] = []
    for part in message.walk():
        if len(part.get_all("Content-Transfer-Encoding", [])) > 1:
            _fail(Rfc822PreparationCode.MALFORMED_TRANSFER_ENCODING)
        if (
            len(part.get_all("Content-Type", [])) > 1
            or len(part.get_all("Content-Disposition", [])) > 1
        ):
            _fail(Rfc822PreparationCode.UNSUPPORTED_ATTACHMENT)
        if part.is_multipart():
            continue
        disposition = part.get_content_disposition()
        filename = part.get_filename()
        is_attachment = disposition == "attachment" or filename is not None
        media_type = part.get_content_type().lower()
        if is_attachment:
            attachments.append(part)
        elif media_type == "text/plain":
            bodies.append(part)
        elif media_type == "text/html":
            html_alternatives.append(part)
        else:
            _fail(Rfc822PreparationCode.UNSUPPORTED_ATTACHMENT)
    return bodies, html_alternatives, attachments


def _decode_part(part: Message, *, attachment: bool) -> str:
    try:
        payload = part.get_payload(decode=False)
        decoded = _decode_transfer_payload(
            payload,
            part.get("Content-Transfer-Encoding"),
            attachment=attachment,
        )
        return _strict_decode_text(
            decoded,
            part.get_content_charset(),
            attachment=attachment,
        )
    except Rfc822PreparationError:
        raise
    except Exception:
        _fail(
            Rfc822PreparationCode.ATTACHMENT_DECODE_FAILED
            if attachment
            else Rfc822PreparationCode.MALFORMED_TRANSFER_ENCODING
        )


def _render_html_alternative(part: Message) -> str:
    decoded = _decode_part(part, attachment=False)
    parser = _ConservativeHtmlText()
    try:
        parser.feed(decoded)
        parser.close()
    except Exception:
        _fail(Rfc822PreparationCode.ALTERNATIVE_DISAGREEMENT)
    if parser.uncertain:
        _fail(Rfc822PreparationCode.ALTERNATIVE_DISAGREEMENT)
    return normalize_text("".join(parser.fragments))


def _authored_at(message: Message) -> str | None:
    values = message.get_all("Date", [])
    if len(values) != 1:
        return None
    try:
        parsed = parsedate_to_datetime(str(values[0]))
        if parsed is None or parsed.tzinfo is None:
            return None
        return (
            parsed.astimezone(timezone.utc)
            .replace(microsecond=0)
            .strftime("%Y-%m-%dT%H:%M:%SZ")
        )
    except (TypeError, ValueError, OverflowError):
        return None


def _redacted_header_value(message: Message, name: str) -> str:
    value = str(message.get(name, ""))
    return value


def _observed_at_string(observed_at: datetime) -> str:
    if (
        not isinstance(observed_at, datetime)
        or observed_at.tzinfo is None
        or observed_at.utcoffset() is None
    ):
        raise TypeError("observed_at must be a timezone-aware datetime")
    return (
        observed_at.astimezone(timezone.utc)
        .replace(microsecond=0)
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    )


def _merge_counts(total: dict[str, int], result: Any) -> None:
    for name in ("customer_term", "email", "phone", "secret"):
        total[name] += getattr(result, name)


def _prepare_sensitive_impl(
    resources: SupportAgentEmailPaths,
    fixture_case_id: str,
    *,
    observed_at: datetime,
) -> PreparedSourceImport:
    observed = _observed_at_string(observed_at)
    raw_message, record = _approved_fixture_snapshot(
        resources,
        fixture_case_id,
    )
    try:
        validate_header_budget(*_raw_header_budget(raw_message))
        message = _strict_parse(raw_message)

        _validate_synthetic_marker(
            message.get_all("X-ExitSpec-Synthetic", [])
        )

        current_id, root_id = _resolve_identity(message)
        _single_exact_header(
            message,
            "From",
            Rfc822PreparationCode.SENDER_AMBIGUOUS,
        )

        validate_mime_shape(*_mime_shape(message))
        bodies, html_alternatives, attachments = _classify_leaves(message)

        if len(attachments) > 3:
            _fail(Rfc822PreparationCode.TOO_MANY_ATTACHMENTS)
        attachment_names: list[str] = []
        for part in attachments:
            filename = part.get_filename()
            if (
                part.get_content_type().lower() != "text/plain"
                or filename is None
                or Path(filename).suffix.lower() != ".txt"
            ):
                _fail(Rfc822PreparationCode.UNSUPPORTED_ATTACHMENT)
            validate_attachment_filename(filename)
            attachment_names.append(filename)

        decoded_attachments: list[tuple[Message, str, bytes]] = []
        decoded_sizes: list[int] = []
        for part in attachments:
            try:
                payload = _decode_transfer_payload(
                    part.get_payload(decode=False),
                    part.get("Content-Transfer-Encoding"),
                    attachment=True,
                )
                text = _strict_decode_text(
                    payload,
                    part.get_content_charset(),
                    attachment=True,
                )
            except Rfc822PreparationError:
                raise
            decoded_attachments.append((part, text, payload))
            decoded_sizes.append(len(payload))
        validate_attachment_sizes(decoded_sizes)

        if len(bodies) != 1:
            _fail(Rfc822PreparationCode.MISSING_BODY)
        normalized_body = normalize_text(_decode_part(bodies[0], attachment=False))
        if not normalized_body.strip():
            _fail(Rfc822PreparationCode.MISSING_BODY)
        if len(normalized_body.encode("utf-8")) > INLINE_BODY_BYTES_MAX:
            _fail(Rfc822PreparationCode.BODY_TOO_LARGE)
        for html_part in html_alternatives:
            if _render_html_alternative(html_part) != normalized_body:
                _fail(Rfc822PreparationCode.ALTERNATIVE_DISAGREEMENT)

        customer_terms = record.get("customer_terms")
        if not isinstance(customer_terms, list):
            _fail(Rfc822PreparationCode.REDACTION_FAILED)
        counts = {
            "customer_term": 0,
            "email": 0,
            "phone": 0,
            "secret": 0,
        }
        try:
            header_results = {
                name: redact_rfc822_value(
                    _redacted_header_value(message, name),
                    customer_terms,
                )
                for name in ("From", "Subject", "To")
            }
            for result in header_results.values():
                _merge_counts(counts, result)
            body_result = redact_rfc822_value(normalized_body, customer_terms)
            _merge_counts(counts, body_result)
        except Rfc822PreparationError:
            raise
        except Exception:
            _fail(Rfc822PreparationCode.REDACTION_FAILED)
        source_parts: list[SourcePart] = [
            SourcePart(
                part_path="body:text/plain:0",
                kind=PartKind.BODY,
                redacted_text=body_result.value,
                redacted_text_sha256=hashlib.sha256(
                    body_result.value.encode("utf-8")
                ).hexdigest(),
                redacted_filename_sha256=None,
            )
        ]

        for index, ((_, text, _), filename) in enumerate(
            zip(decoded_attachments, attachment_names, strict=True)
        ):
            try:
                normalized = normalize_text(text)
                text_result = redact_rfc822_value(normalized, customer_terms)
                filename_result = redact_rfc822_value(filename, customer_terms)
                _merge_counts(counts, text_result)
                _merge_counts(counts, filename_result)
                source_parts.append(
                    SourcePart(
                        part_path=f"attachment:text/plain:{index}",
                        kind=PartKind.ATTACHMENT,
                        redacted_text=text_result.value,
                        redacted_text_sha256=hashlib.sha256(
                            text_result.value.encode("utf-8")
                        ).hexdigest(),
                        redacted_filename_sha256=hashlib.sha256(
                            filename_result.value.encode("utf-8")
                        ).hexdigest(),
                    )
                )
            except Rfc822PreparationError as error:
                if error.code is Rfc822PreparationCode.REDACTION_FAILED:
                    _fail(Rfc822PreparationCode.ATTACHMENT_REDACTION_FAILED)
                raise
            except Exception:
                _fail(Rfc822PreparationCode.ATTACHMENT_REDACTION_FAILED)

        headers = RedactedHeaders(
            authored_at=_authored_at(message),
            **{"from": header_results["From"].value},
            subject=header_results["Subject"].value,
            to=header_results["To"].value,
        )
        source_message = SourceMessage(
            message_key=compute_message_key(current_id),
            redacted_headers=headers,
            redacted_header_sha256=compute_redacted_header_sha256(headers),
            parts=tuple(source_parts),
        )
        try:
            matches = extract_candidate_matches(source_message.parts)
        except Exception:
            _fail(Rfc822PreparationCode.SOURCE_LINK_VIOLATION)
        drafts: list[PreparedCandidateDraft] = []
        for match in matches:
            try:
                part = next(
                    item
                    for item in source_message.parts
                    if item.part_path == match.part_path
                )
                _validate_source_link(
                    part.redacted_text,
                    match.start_byte,
                    match.end_byte,
                    match.quote_sha256,
                )
                drafts.append(
                    PreparedCandidateDraft(
                        projection=match.projection,
                        message_key=source_message.message_key,
                        part_path=match.part_path,
                        start_byte=match.start_byte,
                        end_byte=match.end_byte,
                        quote_sha256=match.quote_sha256,
                    )
                )
            except Rfc822PreparationError:
                raise
            except Exception:
                _fail(Rfc822PreparationCode.SOURCE_LINK_VIOLATION)

        envelope = PreparedSourceEnvelope(
            source_id=compute_source_id(root_id),
            observed_at=observed,
            redaction=RedactionSummary(
                counts=RedactionCounts(**counts),
            ),
            message=source_message,
            candidate_drafts=tuple(drafts),
        )
        return PreparedSourceImport(
            approved_synthetic_fixture=ApprovedSyntheticFixture(
                fixture_case_id=fixture_case_id,
                synthetic_fixture_sha256=record["sha256"],
            ),
            normalized_thread_root_message_id=root_id,
            thread_root_message_key=compute_message_key(root_id),
            prepared_envelope=envelope,
        )
    except Rfc822PreparationError:
        raise
    except (OSError, TypeError, ValueError, UnicodeError):
        _fail(Rfc822PreparationCode.REDACTION_FAILED)
    finally:
        raw_message = None


@dataclass(frozen=True, slots=True)
class _SafeFailure:
    code: Rfc822PreparationCode


def _discard_exception_state(error: BaseException) -> None:
    """Sever every internal traceback/context reference before returning."""

    pending = [error]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if current.__context__ is not None:
            pending.append(current.__context__)
        if current.__cause__ is not None:
            pending.append(current.__cause__)
        current.__traceback__ = None
        current.__context__ = None
        current.__cause__ = None


def _prepare_sensitive(
    resources: SupportAgentEmailPaths,
    fixture_case_id: str,
    observed_at: datetime,
) -> PreparedSourceImport | _SafeFailure:
    try:
        return _prepare_sensitive_impl(
            resources,
            fixture_case_id,
            observed_at=observed_at,
        )
    except Rfc822PreparationError as internal_error:
        code = internal_error.code
        _discard_exception_state(internal_error)
        return _SafeFailure(code)
    except Exception as internal_error:
        _discard_exception_state(internal_error)
        return _SafeFailure(Rfc822PreparationCode.REDACTION_FAILED)


def prepare_support_agent_email_fixture(
    resources: SupportAgentEmailPaths,
    fixture_case_id: str,
    *,
    observed_at: datetime,
) -> PreparedSourceImport:
    """Prepare one employee-selected, registry-approved synthetic fixture."""

    result = _prepare_sensitive(resources, fixture_case_id, observed_at)
    if isinstance(result, _SafeFailure):
        public_error = Rfc822PreparationError(result.code)
        public_error.__traceback__ = None
        public_error.__context__ = None
        raise public_error
    return result


__all__ = [
    "Rfc822PreparationCode",
    "Rfc822PreparationError",
    "prepare_support_agent_email_fixture",
]
