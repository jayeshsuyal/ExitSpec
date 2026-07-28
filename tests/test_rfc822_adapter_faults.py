from __future__ import annotations

import base64
from datetime import datetime, timezone
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser

import pytest

import exitspec.adapters.rfc822 as rfc822
from exitspec.adapters.rfc822 import (
    _classify_leaves,
    _normalized_valid_message_id,
    _raw_header_budget,
    _render_html_alternative,
    _resolve_identity,
    _strict_parse,
    _validate_fixture_observation,
    _validate_source_link,
    _validate_synthetic_marker,
    prepare_support_agent_email_fixture,
)
from exitspec.adapters.rfc822_policy import (
    ATTACHMENT_BYTES_MAX,
    ATTACHMENT_COUNT_MAX,
    ATTACHMENT_TOTAL_BYTES_MAX,
    HEADER_COUNT_MAX,
    INLINE_BODY_BYTES_MAX,
    MIME_DEPTH_MAX,
    MIME_LEAF_PARTS_MAX,
    RAW_MESSAGE_BYTES_MAX,
    Rfc822PreparationError,
    UNFOLDED_HEADER_BYTES_MAX,
    _decode_transfer_payload,
    _strict_decode_text,
    normalize_text,
    validate_attachment_filename,
    validate_attachment_sizes,
    validate_header_budget,
    validate_mime_shape,
    validate_raw_size,
)
from exitspec.demo_data import support_agent_email_paths


@pytest.mark.parametrize(
    ("function", "args", "expected_code"),
    [
        (validate_raw_size, (RAW_MESSAGE_BYTES_MAX + 1,), "raw_message_too_large"),
        (
            validate_header_budget,
            (HEADER_COUNT_MAX + 1, 1),
            "too_many_headers",
        ),
        (
            validate_header_budget,
            (1, UNFOLDED_HEADER_BYTES_MAX + 1),
            "header_too_large",
        ),
        (validate_mime_shape, (MIME_DEPTH_MAX + 1, 1), "mime_too_deep"),
        (
            validate_mime_shape,
            (1, MIME_LEAF_PARTS_MAX + 1),
            "too_many_mime_parts",
        ),
        (
            validate_attachment_sizes,
            ([ATTACHMENT_BYTES_MAX + 1],),
            "attachment_too_large",
        ),
        (
            validate_attachment_sizes,
            ([ATTACHMENT_BYTES_MAX, ATTACHMENT_BYTES_MAX, 1],),
            "attachment_total_too_large",
        ),
        (
            validate_attachment_sizes,
            ([1] * (ATTACHMENT_COUNT_MAX + 1),),
            "too_many_attachments",
        ),
        (
            validate_attachment_filename,
            ("../requirements.txt",),
            "unsafe_attachment_filename",
        ),
    ],
)
def test_virtual_fault_helpers_return_exact_typed_codes(
    function,
    args,
    expected_code: str,
) -> None:
    with pytest.raises(Rfc822PreparationError) as caught:
        function(*args)
    assert caught.value.code == expected_code
    assert caught.value.retryable is False
    assert caught.value.next_action


@pytest.mark.parametrize(
    ("function", "args"),
    [
        (validate_raw_size, (RAW_MESSAGE_BYTES_MAX - 1,)),
        (validate_raw_size, (RAW_MESSAGE_BYTES_MAX,)),
        (validate_header_budget, (HEADER_COUNT_MAX, UNFOLDED_HEADER_BYTES_MAX)),
        (validate_mime_shape, (MIME_DEPTH_MAX, MIME_LEAF_PARTS_MAX)),
        (validate_attachment_sizes, ([ATTACHMENT_BYTES_MAX],)),
        (
            validate_attachment_sizes,
            ([ATTACHMENT_BYTES_MAX, ATTACHMENT_BYTES_MAX],),
        ),
        (validate_attachment_sizes, ([1] * ATTACHMENT_COUNT_MAX,)),
        (validate_attachment_filename, ("requirements.txt",)),
    ],
)
def test_limit_minus_one_and_limit_are_accepted(function, args) -> None:
    function(*args)


def test_strict_transfer_and_charset_fail_closed() -> None:
    with pytest.raises(Rfc822PreparationError) as malformed:
        _decode_transfer_payload(
            "%%%INVALID-BASE64%%%",
            "base64",
            attachment=False,
        )
    assert malformed.value.code == "malformed_transfer_encoding"

    with pytest.raises(Rfc822PreparationError) as attachment:
        _decode_transfer_payload(
            "%%%INVALID-BASE64%%%",
            "base64",
            attachment=True,
        )
    assert attachment.value.code == "attachment_decode_failed"

    with pytest.raises(Rfc822PreparationError) as charset:
        _strict_decode_text(b"Hi", "iso-8859-1", attachment=False)
    assert charset.value.code == "unsupported_charset"

    encoded = base64.b64encode("héllo".encode()).decode()
    payload = _decode_transfer_payload(encoded, "base64", attachment=False)
    assert _strict_decode_text(payload, "utf-8", attachment=False) == "héllo"


@pytest.mark.parametrize("values", [(), ("false",), ("true", "true")])
def test_synthetic_marker_faults_are_exact(values: tuple[str, ...]) -> None:
    with pytest.raises(Rfc822PreparationError) as caught:
        _validate_synthetic_marker(values)
    assert caught.value.code == "source_not_approved"


def test_fixture_digest_and_source_link_faults_are_exact() -> None:
    with pytest.raises(Rfc822PreparationError) as digest:
        _validate_fixture_observation(
            observed_bytes=1,
            observed_sha256="0" * 64,
            expected_bytes=1,
            expected_sha256="1" * 64,
        )
    assert digest.value.code == "fixture_digest_mismatch"

    quote = "Café requirement.\n"
    with pytest.raises(Rfc822PreparationError) as source_link:
        _validate_source_link(quote, 1, len(quote.encode()), "0" * 64)
    assert source_link.value.code == "source_link_violation"


def test_source_and_attachment_redactor_failures_are_typed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_source(value, customer_terms):
        raise RuntimeError(f"must-not-escape:{value}")

    monkeypatch.setattr(rfc822, "redact_rfc822_value", fail_source)
    with support_agent_email_paths() as resources:
        with pytest.raises(Rfc822PreparationError) as source:
            prepare_support_agent_email_fixture(
                resources,
                "thread-root",
                observed_at=datetime.now(timezone.utc),
            )
    assert source.value.code == "redaction_failed"
    assert "must-not-escape" not in str(source.value)

    monkeypatch.undo()
    original = rfc822.redact_rfc822_value

    def fail_attachment(value, customer_terms):
        if "Escalation rate" in value:
            raise RuntimeError(f"must-not-escape:{value}")
        return original(value, customer_terms)

    monkeypatch.setattr(rfc822, "redact_rfc822_value", fail_attachment)
    with support_agent_email_paths() as resources:
        with pytest.raises(Rfc822PreparationError) as attachment:
            prepare_support_agent_email_fixture(
                resources,
                "allowed-text-attachment",
                observed_at=datetime.now(timezone.utc),
            )
    assert attachment.value.code == "attachment_redaction_failed"
    assert "must-not-escape" not in str(attachment.value)


def test_text_normalization_order_and_body_boundary() -> None:
    value = "\r\nCafe\u0301  \rline\t\r\n\r\n"
    assert normalize_text(value) == "Café\nline\n"
    assert len(("a" * INLINE_BODY_BYTES_MAX).encode()) == INLINE_BODY_BYTES_MAX


@pytest.mark.parametrize(
    ("unfolded_bytes", "expected_code"),
    [
        (8191, None),
        (8192, None),
        (8193, "header_too_large"),
    ],
)
def test_real_unfolded_header_byte_boundaries(
    unfolded_bytes: int,
    expected_code: str | None,
) -> None:
    first_line = b"X-Test: value"
    continuation = b" " * (unfolded_bytes - len(first_line))
    raw = first_line + b"\r\n" + continuation + b"\r\n\r\nbody"
    observed = _raw_header_budget(raw)
    assert observed == (1, unfolded_bytes)
    if expected_code is None:
        validate_header_budget(*observed)
    else:
        with pytest.raises(Rfc822PreparationError) as caught:
            validate_header_budget(*observed)
        assert caught.value.code == expected_code


def test_continuation_whitespace_cannot_collapse_8202_bytes_to_nine() -> None:
    first_line = b"X-Test: a"
    raw = first_line + b"\r\n" + (b" " * 8193) + b"\r\n\r\nbody"
    observed = _raw_header_budget(raw)
    assert observed == (1, 8202)
    with pytest.raises(Rfc822PreparationError) as caught:
        validate_header_budget(*observed)
    assert caught.value.code == "header_too_large"


@pytest.mark.parametrize(
    "value",
    [
        "<a@@b>",
        "<@b>",
        "<a..b@c>",
        "<.a@c>",
        "<a.@c>",
        "<a@[bad]>",
        "<a@-bad.example>",
        "<a@bad-.example>",
        "<a@bad..example>",
        "<a b@c>",
        "<a\x01@c>",
        f"<{'a' * 65}@example.test>",
        f"<a@{'b' * 64}.test>",
    ],
)
def test_message_id_rejects_malformed_or_unbounded_ascii(value: str) -> None:
    assert _normalized_valid_message_id(value) is None
    message = EmailMessage()
    message["Message-ID"] = value
    with pytest.raises(Rfc822PreparationError) as caught:
        _resolve_identity(message)
    assert caught.value.code == "missing_message_id"


def test_message_id_reference_and_reply_resolution_use_same_syntax() -> None:
    message = EmailMessage()
    message["Message-ID"] = "<current.id@customer.example>"
    message["References"] = (
        "<a@@b> <first.valid+tag@customer.example> "
        "<second.valid@customer.example>"
    )
    current, root = _resolve_identity(message)
    assert current == "current.id@customer.example"
    assert root == "first.valid+tag@customer.example"

    reply = EmailMessage()
    reply["Message-ID"] = "<current.id@customer.example>"
    reply["In-Reply-To"] = "<a..b@bad> <one.valid@customer.example>"
    assert _resolve_identity(reply)[1] == "one.valid@customer.example"

    ambiguous = EmailMessage()
    ambiguous["Message-ID"] = "<current.id@customer.example>"
    ambiguous["In-Reply-To"] = (
        "<one.valid@customer.example> <two.valid@customer.example>"
    )
    assert _resolve_identity(ambiguous)[1] == "current.id@customer.example"


@pytest.mark.parametrize(
    ("extra_headers", "expected_code"),
    [
        ("Content-Transfer-Encoding: 8bit", "malformed_transfer_encoding"),
        ("Content-Type: text/plain; charset=UTF-8", "unsupported_attachment"),
        (
            "Content-Disposition: inline\r\n"
            "Content-Disposition: attachment; filename=\"x.txt\"",
            "unsupported_attachment",
        ),
    ],
)
def test_duplicate_content_bearing_leaf_headers_fail_closed(
    extra_headers: str,
    expected_code: str,
) -> None:
    raw = (
        "From: Synthetic <sender@example.test>\r\n"
        "To: Reviewer <reviewer@example.test>\r\n"
        "Message-ID: <duplicate-header@example.test>\r\n"
        "X-ExitSpec-Synthetic: true\r\n"
        "Content-Type: text/plain; charset=UTF-8\r\n"
        "Content-Transfer-Encoding: 8bit\r\n"
        f"{extra_headers}\r\n"
        "\r\n"
        "Hello\r\n"
    ).encode("ascii")
    message = BytesParser(policy=policy.default).parsebytes(raw)
    with pytest.raises(Rfc822PreparationError) as caught:
        _classify_leaves(message)
    assert caught.value.code == expected_code


@pytest.mark.parametrize(
    ("base_header", "duplicate_header", "expected_code"),
    [
        (
            "Content-Type: multipart/mixed; boundary=outer",
            "Content-Type: multipart/alternative; boundary=other",
            "unsupported_attachment",
        ),
        (
            "Content-Transfer-Encoding: 7bit",
            "Content-Transfer-Encoding: 8bit",
            "malformed_transfer_encoding",
        ),
        (
            "Content-Disposition: inline",
            "Content-Disposition: attachment",
            "unsupported_attachment",
        ),
    ],
)
def test_duplicate_content_headers_on_multipart_container_fail_closed(
    base_header: str,
    duplicate_header: str,
    expected_code: str,
) -> None:
    content_type = (
        ""
        if base_header.startswith("Content-Type:")
        else "Content-Type: multipart/mixed; boundary=outer\r\n"
    )
    raw = (
        "From: Synthetic <sender@example.test>\r\n"
        "To: Reviewer <reviewer@example.test>\r\n"
        "Message-ID: <duplicate-container@example.test>\r\n"
        "X-ExitSpec-Synthetic: true\r\n"
        f"{content_type}"
        f"{base_header}\r\n"
        f"{duplicate_header}\r\n"
        "\r\n"
        "--outer\r\n"
        "Content-Type: text/plain; charset=UTF-8\r\n"
        "\r\n"
        "Hello\r\n"
        "--outer--\r\n"
    ).encode("ascii")
    message = BytesParser(policy=policy.default).parsebytes(raw)
    with pytest.raises(Rfc822PreparationError) as caught:
        _classify_leaves(message)
    assert caught.value.code == expected_code


def test_html_processing_instruction_is_never_equivalent_to_plain_text() -> None:
    part = EmailMessage()
    part["Content-Type"] = "text/html; charset=UTF-8"
    part["Content-Transfer-Encoding"] = "8bit"
    part.set_payload("<?ignored?>Hello")
    with pytest.raises(Rfc822PreparationError) as caught:
        _render_html_alternative(part)
    assert caught.value.code == "alternative_disagreement"


def test_error_does_not_chain_or_echo_fault_input() -> None:
    secret = "%%%INVALID-BASE64-api_key=do-not-log%%%"
    try:
        _decode_transfer_payload(secret, "base64", attachment=False)
    except Rfc822PreparationError as error:
        assert error.__cause__ is None
        assert error.__context__ is None
        rendered = " ".join(
            [str(error), repr(error), repr(error.args), error.next_action]
        )
        assert secret not in rendered
        assert "do-not-log" not in rendered
    else:
        raise AssertionError("fault must fail")
