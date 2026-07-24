"""Deterministic, best-effort transcript redaction before external handling.

Only :attr:`RedactionResult.redacted_text` may cross the provider or persistence
boundary.  The original transcript is used transiently while this function
runs and is never retained in the returned model or its finding metadata.

This module intentionally implements a narrow, auditable policy.  It is not a
general PII classifier and it cannot establish that a transcript is free of
personal, confidential, or regulated information.
"""

from __future__ import annotations

import bisect
import re
from dataclasses import dataclass
from enum import Enum
from typing import Dict, Iterable, List, Pattern, Sequence, Tuple

from pydantic import ConfigDict, Field, computed_field, model_validator

from .models import ExitSpecModel


POLICY_VERSION = "exitspec-transcript-redaction/1.0"


class RedactionKind(str, Enum):
    BEARER_TOKEN = "BEARER_TOKEN"
    API_TOKEN = "API_TOKEN"
    JWT = "JWT"
    PAYMENT_CARD = "PAYMENT_CARD"
    EMAIL = "EMAIL"
    PHONE = "PHONE"
    CUSTOMER_TERM = "CUSTOMER_TERM"


class RedactionDecision(str, Enum):
    """Whether the returned text passed this policy's mechanical boundary."""

    ALLOW_REDACTED_ONLY = "ALLOW_REDACTED_ONLY"
    BLOCK = "BLOCK"


PLACEHOLDERS: Dict[RedactionKind, str] = {
    kind: "[REDACTED:{0}]".format(kind.value) for kind in RedactionKind
}

_PLACEHOLDER_PATTERN = re.compile(
    r"\[REDACTED:(?P<kind>"
    + "|".join(re.escape(kind.value) for kind in RedactionKind)
    + r")\]"
)

_LIMITATIONS = (
    "Best-effort patterns do not detect every form of personal, confidential, "
    "or regulated data.",
    "Unknown customer names, project names, and code words are not inferred; "
    "the caller must configure them explicitly.",
    "Contextual or unusually formatted sensitive data can require human review "
    "before a real transcript is shared or retained.",
)


class RedactionFinding(ExitSpecModel):
    """Aggregate, non-secret audit metadata for one redaction category."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
    )

    kind: RedactionKind
    count: int = Field(gt=0)
    line_numbers: Tuple[int, ...] = Field(min_length=1)
    placeholder: str = Field(min_length=1)

    @model_validator(mode="after")
    def require_category_placeholder(self) -> "RedactionFinding":
        if self.placeholder != PLACEHOLDERS[self.kind]:
            raise ValueError("Finding placeholder must match its redaction category.")
        if self.line_numbers != tuple(sorted(set(self.line_numbers))):
            raise ValueError("Finding line numbers must be sorted and unique.")
        return self


class RedactionResult(ExitSpecModel):
    """The only transcript representation allowed beyond the intake boundary.

    ``safe_to_send`` and ``safe_to_persist`` mean that the returned text passed
    the detectors in this exact policy version.  They do not assert that the
    text contains no PII or confidential information.
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
    )

    policy_version: str
    redacted_text: str = Field(repr=False)
    findings: Tuple[RedactionFinding, ...]
    decision: RedactionDecision
    safe_to_send: bool
    safe_to_persist: bool
    limitations: Tuple[str, ...] = Field(min_length=1)

    @computed_field(return_type=Dict[str, int])
    @property
    def counts(self) -> Dict[str, int]:
        """Return isolated count metadata while preserving serialized output."""

        counts = {kind.value: 0 for kind in RedactionKind}
        for finding in self.findings:
            counts[finding.kind.value] = finding.count
        return counts

    @model_validator(mode="after")
    def require_consistent_decision(self) -> "RedactionResult":
        if self.policy_version != POLICY_VERSION:
            raise ValueError("Redaction result must use the current policy.")
        if not _findings_match_redacted_text(self.redacted_text, self.findings):
            raise ValueError(
                "Redaction findings must match the redacted placeholders."
            )

        allowed = self.decision == RedactionDecision.ALLOW_REDACTED_ONLY
        if self.safe_to_send != allowed or self.safe_to_persist != allowed:
            raise ValueError("Safe-handling flags must match the redaction decision.")
        if allowed and _has_unredacted_supported_value(self.redacted_text, ()):
            raise ValueError(
                "Allowed redaction result contains a supported sensitive pattern."
            )
        return self


class RedactionConfigurationError(ValueError):
    """Raised without echoing a potentially sensitive configured term."""


class RedactionBoundaryError(RuntimeError):
    """Sanitized fail-closed denial at a provider or persistence boundary."""


@dataclass(frozen=True)
class _Candidate:
    start: int
    end: int
    kind: RedactionKind
    priority: int
    existing_placeholder: bool = False


@dataclass(frozen=True)
class _Detector:
    kind: RedactionKind
    pattern: Pattern[str]
    priority: int
    group: str = "value"


_DETECTORS: Tuple[_Detector, ...] = (
    _Detector(
        RedactionKind.BEARER_TOKEN,
        re.compile(
            r"(?i)\bbearer[ \t]+(?P<value>[A-Za-z0-9._~+/=-]{8,})"
        ),
        10,
    ),
    _Detector(
        RedactionKind.JWT,
        re.compile(
            r"(?<![A-Za-z0-9_-])(?P<value>"
            r"[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"
            r")(?![A-Za-z0-9_-])"
        ),
        20,
    ),
    _Detector(
        RedactionKind.API_TOKEN,
        re.compile(
            r"(?i)\b(?:(?:[A-Za-z0-9]+_)*(?:api_key|access_token|secret_key|"
            r"client_secret)|api[ -]key|access[ -]token|secret(?:[ -]key)?|"
            r"client[ -]secret)\b[ \t]*(?::|=)[ \t]*[\"']?"
            r"(?P<value>[A-Za-z0-9][A-Za-z0-9_./+=-]{7,})"
        ),
        30,
    ),
    _Detector(
        RedactionKind.API_TOKEN,
        re.compile(
            r"(?<![A-Za-z0-9_-])(?P<value>"
            r"(?:sk|rk|pk|fw|api)[_-][A-Za-z0-9][A-Za-z0-9_-]{11,}|"
            r"gh[pousr]_[A-Za-z0-9]{20,}"
            r")(?![A-Za-z0-9_-])",
            re.IGNORECASE,
        ),
        31,
    ),
    _Detector(
        RedactionKind.EMAIL,
        re.compile(
            r"(?<![A-Za-z0-9.!#$%&'*+/=?^_`{|}~-])"
            r"(?P<value>[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
            r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
            r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+)"
            r"(?![A-Za-z0-9-])"
        ),
        50,
    ),
    _Detector(
        RedactionKind.PHONE,
        re.compile(
            r"(?<![\w])(?P<value>"
            r"(?:\+\d{1,3}[ .-])?"
            r"(?:\(\d{2,4}\)[ .-]?|\d{2,4}[ .-])"
            r"\d{3,4}[ .-]\d{4}"
            r")(?![\w])"
        ),
        60,
    ),
    _Detector(
        RedactionKind.PHONE,
        re.compile(r"(?<![\w])(?P<value>\+\d{8,15})(?![\w])"),
        61,
    ),
)

_PAYMENT_CARD_PATTERN = re.compile(
    r"(?<!\d)(?P<value>(?:\d[ -]?){12,18}\d)(?!\d)"
)


def _normalize_customer_terms(customer_terms: Sequence[str]) -> Tuple[str, ...]:
    if isinstance(customer_terms, (str, bytes)):
        raise RedactionConfigurationError(
            "Customer terms must be a sequence of individual strings."
        )

    normalized: Dict[str, str] = {}
    try:
        terms: Iterable[object] = iter(customer_terms)
    except TypeError as error:
        raise RedactionConfigurationError(
            "Customer terms must be a sequence of individual strings."
        ) from error

    for term in terms:
        if not isinstance(term, str):
            raise RedactionConfigurationError("Every customer term must be a string.")
        clean = term.strip()
        if len(clean) < 3:
            raise RedactionConfigurationError(
                "Every customer term must contain at least three characters."
            )
        if "\n" in clean or "\r" in clean:
            raise RedactionConfigurationError(
                "Customer terms cannot contain line breaks."
            )
        if _PLACEHOLDER_PATTERN.search(clean):
            raise RedactionConfigurationError(
                "Customer terms cannot contain reserved redaction placeholders."
            )
        normalized.setdefault(clean.casefold(), clean)

    return tuple(
        sorted(normalized.values(), key=lambda value: (-len(value), value.casefold()))
    )


def _line_starts(text: str) -> List[int]:
    """Return offsets for LF, CRLF, and lone-CR lines without changing text."""

    starts = [0]
    index = 0
    while index < len(text):
        if text[index] == "\r":
            if index + 1 < len(text) and text[index + 1] == "\n":
                index += 1
            starts.append(index + 1)
        elif text[index] == "\n":
            starts.append(index + 1)
        index += 1
    return starts


def _line_number(starts: Sequence[int], offset: int) -> int:
    return bisect.bisect_right(starts, offset)


def _overlaps(candidate: _Candidate, other: _Candidate) -> bool:
    return candidate.start < other.end and other.start < candidate.end


def _luhn_valid(value: str) -> bool:
    digits = [int(character) for character in value if character.isdigit()]
    if not 13 <= len(digits) <= 19:
        return False
    if len(set(digits)) == 1:
        return False

    checksum = 0
    parity = len(digits) % 2
    for index, digit in enumerate(digits):
        if index % 2 == parity:
            digit *= 2
            if digit > 9:
                digit -= 9
        checksum += digit
    return checksum % 10 == 0


def _existing_placeholders(text: str) -> List[_Candidate]:
    return [
        _Candidate(
            start=match.start(),
            end=match.end(),
            kind=RedactionKind(match.group("kind")),
            priority=0,
            existing_placeholder=True,
        )
        for match in _PLACEHOLDER_PATTERN.finditer(text)
    ]


def _raw_candidates(text: str, customer_terms: Sequence[str]) -> List[_Candidate]:
    candidates: List[_Candidate] = []
    for detector in _DETECTORS:
        for match in detector.pattern.finditer(text):
            candidates.append(
                _Candidate(
                    start=match.start(detector.group),
                    end=match.end(detector.group),
                    kind=detector.kind,
                    priority=detector.priority,
                )
            )

    for match in _PAYMENT_CARD_PATTERN.finditer(text):
        value = match.group("value")
        if _luhn_valid(value):
            candidates.append(
                _Candidate(
                    start=match.start("value"),
                    end=match.end("value"),
                    kind=RedactionKind.PAYMENT_CARD,
                    priority=40,
                )
            )

    for term in customer_terms:
        prefix = r"(?<!\w)" if term[0].isalnum() or term[0] == "_" else ""
        suffix = r"(?!\w)" if term[-1].isalnum() or term[-1] == "_" else ""
        pattern = re.compile(prefix + re.escape(term) + suffix, re.IGNORECASE)
        for match in pattern.finditer(text):
            candidates.append(
                _Candidate(
                    start=match.start(),
                    end=match.end(),
                    kind=RedactionKind.CUSTOMER_TERM,
                    priority=70,
                )
            )
    return candidates


def _select_candidates(
    text: str, customer_terms: Sequence[str]
) -> List[_Candidate]:
    selected = _existing_placeholders(text)
    raw = sorted(
        _raw_candidates(text, customer_terms),
        key=lambda item: (
            item.priority,
            item.start,
            -(item.end - item.start),
            item.kind.value,
        ),
    )
    for candidate in raw:
        if not any(_overlaps(candidate, existing) for existing in selected):
            selected.append(candidate)
    return sorted(selected, key=lambda item: item.start)


def _replace(text: str, selected: Sequence[_Candidate]) -> str:
    output: List[str] = []
    cursor = 0
    for candidate in selected:
        output.append(text[cursor : candidate.start])
        output.append(PLACEHOLDERS[candidate.kind])
        cursor = candidate.end
    output.append(text[cursor:])
    return "".join(output)


def _has_unredacted_supported_value(
    text: str, customer_terms: Sequence[str]
) -> bool:
    placeholders = _existing_placeholders(text)
    return any(
        not any(_overlaps(candidate, placeholder) for placeholder in placeholders)
        for candidate in _raw_candidates(text, customer_terms)
    )


def _findings_match_redacted_text(
    text: str, findings: Sequence[RedactionFinding]
) -> bool:
    """Check aggregate metadata against placeholders without retaining raw data."""

    if not isinstance(text, str):
        return False

    starts = _line_starts(text)
    counts = {kind: 0 for kind in RedactionKind}
    lines: Dict[RedactionKind, set[int]] = {
        kind: set() for kind in RedactionKind
    }
    for candidate in _existing_placeholders(text):
        counts[candidate.kind] += 1
        lines[candidate.kind].add(_line_number(starts, candidate.start))

    seen: set[RedactionKind] = set()
    for finding in findings:
        if not isinstance(finding, RedactionFinding):
            return False
        if finding.kind in seen:
            return False
        seen.add(finding.kind)
        if finding.placeholder != PLACEHOLDERS[finding.kind]:
            return False
        if finding.count != counts[finding.kind]:
            return False
        if tuple(finding.line_numbers) != tuple(sorted(lines[finding.kind])):
            return False

    expected_kinds = {kind for kind, count in counts.items() if count}
    return seen == expected_kinds


def _has_current_allowed_state(
    result: RedactionResult, customer_terms: Sequence[str]
) -> bool:
    """Recheck all trusted state without relying on construction validation."""

    if type(result) is not RedactionResult:
        return False
    if result.policy_version != POLICY_VERSION:
        return False
    if result.decision is not RedactionDecision.ALLOW_REDACTED_ONLY:
        return False
    if result.safe_to_send is not True or result.safe_to_persist is not True:
        return False
    if not isinstance(result.redacted_text, str):
        return False
    if type(result.findings) is not tuple:
        return False
    if (
        type(result.limitations) is not tuple
        or result.limitations != _LIMITATIONS
    ):
        return False
    for finding in result.findings:
        if type(finding) is not RedactionFinding:
            return False
        if type(finding.line_numbers) is not tuple:
            return False
    if not _findings_match_redacted_text(result.redacted_text, result.findings):
        return False
    return not _has_unredacted_supported_value(
        result.redacted_text, customer_terms
    )


def assert_redaction_egress(
    result: RedactionResult, *, customer_terms: Sequence[str] = ()
) -> str:
    """Return redacted text only when it passes a fresh, current-policy scan.

    Call this immediately before sending text to a provider or persisting it.
    The check deliberately does not trust prior validation because Pydantic's
    low-level construction and ``model_copy(update=...)`` APIs can bypass it.
    All denials use one sanitized message that never includes transcript text,
    configured terms, or matched values.
    """

    try:
        terms = _normalize_customer_terms(customer_terms)
        if not _has_current_allowed_state(result, terms):
            raise RedactionBoundaryError
    except Exception:
        raise RedactionBoundaryError(
            "Redaction egress denied by the current policy."
        ) from None
    return result.redacted_text


def redact_transcript(
    transcript_text: str, *, customer_terms: Sequence[str] = ()
) -> RedactionResult:
    """Return the redacted-only transcript allowed past the intake boundary.

    The function never mutates or stores ``transcript_text``.  Supported
    patterns are replaced with category-only placeholders, and findings retain
    line numbers rather than matched values.  Existing policy placeholders are
    recognized, which makes repeated application idempotent.
    """

    if not isinstance(transcript_text, str):
        raise TypeError("Transcript text must be a string.")
    terms = _normalize_customer_terms(customer_terms)
    selected = _select_candidates(transcript_text, terms)
    redacted_text = _replace(transcript_text, selected)

    starts = _line_starts(transcript_text)
    count_by_kind = {kind: 0 for kind in RedactionKind}
    lines_by_kind: Dict[RedactionKind, set[int]] = {
        kind: set() for kind in RedactionKind
    }
    for candidate in selected:
        count_by_kind[candidate.kind] += 1
        lines_by_kind[candidate.kind].add(
            _line_number(starts, candidate.start)
        )

    findings = [
        RedactionFinding(
            kind=kind,
            count=count_by_kind[kind],
            line_numbers=sorted(lines_by_kind[kind]),
            placeholder=PLACEHOLDERS[kind],
        )
        for kind in RedactionKind
        if count_by_kind[kind]
    ]
    blocked = _has_unredacted_supported_value(redacted_text, terms)
    decision = (
        RedactionDecision.BLOCK
        if blocked
        else RedactionDecision.ALLOW_REDACTED_ONLY
    )
    allowed = decision == RedactionDecision.ALLOW_REDACTED_ONLY
    return RedactionResult(
        policy_version=POLICY_VERSION,
        redacted_text=redacted_text,
        findings=findings,
        decision=decision,
        safe_to_send=allowed,
        safe_to_persist=allowed,
        limitations=_LIMITATIONS,
    )
