"""Bounded, provider-free transcript intake for the local synthetic demo.

This module only structures pasted source text.  It never calls a model,
generates a criterion, or records an approval.  Those remain separate human
authoring and review steps.
"""

from __future__ import annotations

from typing import Any, Dict, List

from pydantic import ValidationError

from .models import DiscoveryTranscript, TranscriptLine


DEFAULT_TRANSCRIPT_ID = "pasted-transcript"
DEFAULT_TRANSCRIPT_TITLE = "Pasted discovery transcript"
MAX_INPUT_CHARACTERS = 50_000
MAX_TRANSCRIPT_LINES = 500


class TranscriptIntakeError(ValueError):
    """A clear, safe-to-display error for invalid pasted transcript text."""


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
        if ":" not in stripped_line:
            raise _line_error(source_line_number, "must use 'Speaker: message'.")

        raw_speaker, raw_message = stripped_line.split(":", 1)
        speaker = raw_speaker.strip()
        message = raw_message.strip()
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


def intake_pasted_transcript_payload(
    pasted_text: str,
    *,
    transcript_id: str = DEFAULT_TRANSCRIPT_ID,
    title: str = DEFAULT_TRANSCRIPT_TITLE,
) -> Dict[str, Any]:
    """Return a JSON-ready transcript payload for a future local web route.

    The payload contains only transcript source material and its synthetic-demo
    marker. It does not contain AI-generated criteria, review decisions, or
    approval claims.
    """

    transcript = parse_pasted_transcript(
        pasted_text,
        transcript_id=transcript_id,
        title=title,
    )
    return transcript.model_dump(mode="json")
