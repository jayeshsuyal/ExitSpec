import pytest

from exitspec.intake import (
    MAX_INPUT_CHARACTERS,
    MAX_TRANSCRIPT_LINES,
    TranscriptIntakeError,
    intake_pasted_transcript_payload,
    parse_pasted_transcript,
)


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

    too_many_lines = "\n".join(
        "Customer: line {0}".format(number)
        for number in range(MAX_TRANSCRIPT_LINES + 1)
    )
    with pytest.raises(TranscriptIntakeError, match="more than"):
        parse_pasted_transcript(too_many_lines)


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
