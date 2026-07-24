"""Bounded, redaction-first transcript intake.

Raw transcript text is redacted before it is parsed or allowed into returned
state. This module never calls a model, generates a criterion, or records an
approval. Those remain separate authoring and human-review steps.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Sequence

from pydantic import ConfigDict, Field, ValidationError

from .models import DiscoveryTranscript, ExitSpecModel, TranscriptLine
from .redaction import (
    RedactionBoundaryError,
    RedactionDecision,
    RedactionResult,
    assert_redaction_egress,
    redact_transcript,
)


DEFAULT_TRANSCRIPT_ID = "pasted-transcript"
DEFAULT_TRANSCRIPT_TITLE = "Pasted discovery transcript"
MAX_INPUT_CHARACTERS = 50_000
MAX_TRANSCRIPT_LINES = 500
_SPEAKER_LINE = re.compile(
    r"^(?P<speaker>\[REDACTED:[A-Z_]+\]|(?!\[REDACTED:)[^:]+)"
    r":(?P<message>.*)$"
)


class TranscriptIntakeError(ValueError):
    """A clear, safe-to-display error for invalid pasted transcript text."""


class TranscriptRedactionSummary(ExitSpecModel):
    """Non-secret metadata retained after raw transcript disposal."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_version: str = Field(min_length=1)
    decision: RedactionDecision
    counts: Dict[str, int]
    line_numbers: Dict[str, List[int]]


class RedactedTranscriptIntake(ExitSpecModel):
    """The redacted transcript and metadata allowed beyond intake."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    transcript: DiscoveryTranscript
    redaction: TranscriptRedactionSummary


def _line_error(source_line_number: int, message: str) -> TranscriptIntakeError:
    return TranscriptIntakeError(
        "Transcript line {0} {1}".format(source_line_number, message)
    )


def _validated_metadata(transcript_id: str, title: str) -> tuple[str, str]:
    if not isinstance(transcript_id, str) or not transcript_id.strip():
        raise TranscriptIntakeError("Transcript id cannot be blank.")
    if not isinstance(title, str) or not title.strip():
        raise TranscriptIntakeError("Transcript title cannot be blank.")
    return transcript_id.strip(), title.strip()


def _format_validation_error(error: ValidationError) -> str:
    details = error.errors(include_url=False)
    first = details[0] if details else {"msg": "Unknown validation error."}
    location = ".".join(str(part) for part in first.get("loc", ()))
    if location:
        return "Transcript metadata is invalid at {0}: {1}".format(
            location, first["msg"]
        )
    return "Transcript metadata is invalid: {0}".format(first["msg"])


def parse_pasted_transcript(
    pasted_text: str,
    *,
    transcript_id: str = DEFAULT_TRANSCRIPT_ID,
    title: str = DEFAULT_TRANSCRIPT_TITLE,
) -> DiscoveryTranscript:
    """Parse bounded ``Speaker: message`` text into a synthetic transcript.

    Empty separator lines are ignored. Every non-empty line must provide both
    a speaker and a message. Output line numbers are always normalized to a
    contiguous one-based sequence, regardless of separator lines in the paste.
    """

    if not isinstance(pasted_text, str):
        raise TranscriptIntakeError("Transcript text must be a string.")
    if not pasted_text.strip():
        raise TranscriptIntakeError("Transcript text cannot be blank.")
    if len(pasted_text) > MAX_INPUT_CHARACTERS:
        raise TranscriptIntakeError(
            "Transcript text exceeds the {0} character demo limit.".format(
                MAX_INPUT_CHARACTERS
            )
        )

    normalized_id, normalized_title = _validated_metadata(transcript_id, title)
    lines: List[TranscriptLine] = []
    for source_line_number, raw_line in enumerate(pasted_text.splitlines(), start=1):
        stripped_line = raw_line.strip()
        if not stripped_line:
            continue
        if stripped_line.startswith(":"):
            raise _line_error(source_line_number, "needs a speaker before ':'.")
        matched_line = _SPEAKER_LINE.fullmatch(stripped_line)
        if matched_line is None:
            raise _line_error(source_line_number, "must use 'Speaker: message'.")

        speaker = matched_line.group("speaker").strip()
        message = matched_line.group("message").strip()
        if not speaker:
            raise _line_error(source_line_number, "needs a speaker before ':'.")
        if not message:
            raise _line_error(source_line_number, "needs a message after ':'.")
        if len(lines) >= MAX_TRANSCRIPT_LINES:
            raise TranscriptIntakeError(
                "Transcript contains more than {0} non-blank lines; "
                "split the demo input into smaller excerpts.".format(
                    MAX_TRANSCRIPT_LINES
                )
            )

        lines.append(
            TranscriptLine(
                line_number=len(lines) + 1,
                speaker=speaker,
                text=message,
            )
        )

    if not lines:
        raise TranscriptIntakeError("Transcript text cannot be blank.")

    try:
        return DiscoveryTranscript(
            id=normalized_id,
            title=normalized_title,
            synthetic=True,
            lines=lines,
        )
    except ValidationError as error:
        raise TranscriptIntakeError(_format_validation_error(error)) from error


def _redaction_summary(result: RedactionResult) -> TranscriptRedactionSummary:
    return TranscriptRedactionSummary(
        policy_version=result.policy_version,
        decision=result.decision,
        counts=dict(result.counts),
        line_numbers={
            finding.kind.value: list(finding.line_numbers)
            for finding in result.findings
        },
    )


def redact_and_parse_pasted_transcript(
    pasted_text: str,
    *,
    transcript_id: str = DEFAULT_TRANSCRIPT_ID,
    title: str = DEFAULT_TRANSCRIPT_TITLE,
    customer_terms: Sequence[str] = (),
) -> RedactedTranscriptIntake:
    """Redact raw notes, fail closed at egress, then parse only redacted text."""

    if not isinstance(pasted_text, str):
        raise TranscriptIntakeError("Transcript text must be a string.")
    if len(pasted_text) > MAX_INPUT_CHARACTERS:
        raise TranscriptIntakeError(
            "Transcript text exceeds the {0} character demo limit.".format(
                MAX_INPUT_CHARACTERS
            )
        )

    try:
        redaction = redact_transcript(pasted_text, customer_terms=customer_terms)
    finally:
        del pasted_text
    try:
        redacted_text = assert_redaction_egress(
            redaction, customer_terms=customer_terms
        )
    except RedactionBoundaryError as error:
        raise TranscriptIntakeError(
            "Transcript intake was blocked by the current redaction policy."
        ) from error

    transcript = parse_pasted_transcript(
        redacted_text,
        transcript_id=transcript_id,
        title=title,
    )
    return RedactedTranscriptIntake(
        transcript=transcript,
        redaction=_redaction_summary(redaction),
    )


def intake_pasted_transcript_payload(
    pasted_text: str,
    *,
    transcript_id: str = DEFAULT_TRANSCRIPT_ID,
    title: str = DEFAULT_TRANSCRIPT_TITLE,
    customer_terms: Sequence[str] = (),
) -> Dict[str, Any]:
    """Return a JSON-ready redacted transcript payload.

    Compatibility is preserved by returning the transcript shape rather than
    the wrapper metadata. The transcript content has still crossed the
    redaction-first intake boundary.
    """

    intake = redact_and_parse_pasted_transcript(
        pasted_text,
        transcript_id=transcript_id,
        title=title,
        customer_terms=customer_terms,
    )
    return intake.transcript.model_dump(mode="json")
