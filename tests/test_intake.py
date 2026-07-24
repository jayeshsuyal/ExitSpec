import json

import pytest

from exitspec.intake import (
    MAX_INPUT_CHARACTERS,
    MAX_TRANSCRIPT_LINES,
    TranscriptIntakeError,
    intake_pasted_transcript_payload,
    parse_pasted_transcript,
    redact_and_parse_pasted_transcript,
)


RAW_EMAIL = "owner@example.com"
RAW_API_TOKEN = "sk_live_1234567890"
RAW_CUSTOMER_TERM = "Project Phoenix"


def test_parse_pasted_transcript_normalizes_content_and_line_numbers():
    transcript = parse_pasted_transcript(
        "  Field Engineer:  We need 95% tool selection accuracy.  \n"
        "\n"
        "Customer: Yes, and show us failures.  "
    )

    assert transcript.synthetic is True
    assert [(line.line_number, line.speaker, line.text) for line in transcript.lines] == [
        (1, "Field Engineer", "We need 95% tool selection accuracy."),
        (2, "Customer", "Yes, and show us failures."),
    ]


def test_parse_pasted_transcript_keeps_a_redacted_placeholder_as_the_speaker():
    transcript = parse_pasted_transcript(
        "[REDACTED:CUSTOMER_TERM]: message"
    )

    assert transcript.lines[0].speaker == "[REDACTED:CUSTOMER_TERM]"
    assert transcript.lines[0].text == "message"


@pytest.mark.parametrize(
    ("pasted_text", "expected_message"),
    [
        ("", "cannot be blank"),
        ("   \n\t", "cannot be blank"),
        ("Customer says yes", "line 1 must use 'Speaker: message'"),
        (": The target is 95%", "line 1 needs a speaker"),
        ("Customer:   ", "line 1 needs a message"),
    ],
)
def test_parse_pasted_transcript_rejects_blank_and_malformed_input(
    pasted_text, expected_message
):
    with pytest.raises(TranscriptIntakeError, match=expected_message):
        parse_pasted_transcript(pasted_text)


def test_parse_pasted_transcript_rejects_bounded_input():
    with pytest.raises(TranscriptIntakeError, match="character demo limit"):
        parse_pasted_transcript("Customer: " + ("x" * MAX_INPUT_CHARACTERS))
    with pytest.raises(TranscriptIntakeError, match="character demo limit"):
        redact_and_parse_pasted_transcript(
            "Customer: api_key=" + ("x" * MAX_INPUT_CHARACTERS)
        )

    too_many_lines = "\n".join(
        "Customer: line {0}".format(number)
        for number in range(MAX_TRANSCRIPT_LINES + 1)
    )
    with pytest.raises(TranscriptIntakeError, match="more than"):
        parse_pasted_transcript(too_many_lines)


def test_redaction_first_intake_returns_only_redacted_source_and_safe_summary():
    raw = (
        "{0}: Contact {1} with api_key={2}.".format(
            RAW_CUSTOMER_TERM,
            RAW_EMAIL,
            RAW_API_TOKEN,
        )
    )

    intake = redact_and_parse_pasted_transcript(
        raw,
        customer_terms=[RAW_CUSTOMER_TERM],
    )
    payload = intake_pasted_transcript_payload(
        raw,
        customer_terms=[RAW_CUSTOMER_TERM],
    )
    serialized_surfaces = (
        intake.model_dump_json(),
        repr(intake),
        json.dumps(payload),
    )

    for secret in (RAW_EMAIL, RAW_API_TOKEN, RAW_CUSTOMER_TERM):
        assert all(secret not in surface for surface in serialized_surfaces)
    assert intake.transcript.lines[0].speaker == "[REDACTED:CUSTOMER_TERM]"
    assert "[REDACTED:EMAIL]" in intake.transcript.lines[0].text
    assert "[REDACTED:API_TOKEN]" in intake.transcript.lines[0].text
    assert intake.redaction.counts["EMAIL"] == 1
    assert intake.redaction.counts["API_TOKEN"] == 1
    assert intake.redaction.counts["CUSTOMER_TERM"] == 1
    assert set(intake.redaction.model_dump()) == {
        "policy_version",
        "decision",
        "counts",
        "line_numbers",
    }


def test_redaction_does_not_turn_a_placeholder_colon_into_fake_attribution():
    malformed = "{0} contact {1} with api_key={2}".format(
        RAW_CUSTOMER_TERM,
        RAW_EMAIL,
        RAW_API_TOKEN,
    )

    with pytest.raises(
        TranscriptIntakeError,
        match="line 1 must use 'Speaker: message'",
    ) as raised:
        redact_and_parse_pasted_transcript(
            malformed,
            customer_terms=[RAW_CUSTOMER_TERM],
        )

    error = str(raised.value)
    assert RAW_EMAIL not in error
    assert RAW_API_TOKEN not in error
    assert RAW_CUSTOMER_TERM not in error


def test_json_helper_returns_only_source_transcript_material():
    payload = intake_pasted_transcript_payload(
        "Customer: The POC must hit 95% exact tool selection.",
        transcript_id="support-call-01",
        title="Support-agent discovery call",
    )

    assert payload == {
        "id": "support-call-01",
        "title": "Support-agent discovery call",
        "synthetic": True,
        "lines": [
            {
                "line_number": 1,
                "speaker": "Customer",
                "text": "The POC must hit 95% exact tool selection.",
            }
        ],
    }
    assert "criteria" not in payload
    assert "approved" not in payload
