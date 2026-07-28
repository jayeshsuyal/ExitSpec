"""Deterministic candidate matching over normalized, redacted source parts."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol, Sequence

from exitspec.source_models import CandidateProjection


class _RedactedPart(Protocol):
    part_path: str
    redacted_text: str


@dataclass(frozen=True)
class CandidateMatch:
    projection: CandidateProjection
    part_path: str
    start_byte: int
    end_byte: int
    quote_sha256: str


@dataclass(frozen=True)
class _Rule:
    pattern: re.Pattern[str]
    metric: str
    operator: str
    unit: str
    percent: bool = False
    sample_group: str | None = None


_RULES = (
    _Rule(
        re.compile(
            r"(?:The support agent must select the correct tool in at least|"
            r"Tool-selection accuracy (?:should|must) be at least) "
            r"(?P<value>[0-9]+(?:\.[0-9]+)?)%"
            r"(?: of (?P<samples>[1-9][0-9]*) cases)?\.",
            re.IGNORECASE,
        ),
        "tool_selection_accuracy",
        "gte",
        "ratio",
        percent=True,
        sample_group="samples",
    ),
    _Rule(
        re.compile(
            r"P95 end-to-end latency must remain below "
            r"(?P<value>[0-9]+(?:\.[0-9]+)?) seconds?\.",
            re.IGNORECASE,
        ),
        "end_to_end_latency_p95",
        "lt",
        "seconds",
    ),
    _Rule(
        re.compile(
            r"The total model-and-tool cost must stay at or below "
            r"\$(?P<value>[0-9]+(?:\.[0-9]+)?) per resolved case\.",
            re.IGNORECASE,
        ),
        "total_model_and_tool_cost_per_resolved_case",
        "lte",
        "USD/case",
    ),
    _Rule(
        re.compile(
            r"Escalation rate must remain below "
            r"(?P<value>[0-9]+(?:\.[0-9]+)?)%\.",
            re.IGNORECASE,
        ),
        "escalation_rate",
        "lt",
        "ratio",
        percent=True,
    ),
)


def _decimal_string(value: Decimal) -> str:
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered or "0"


def _byte_offset(value: str, character_offset: int) -> int:
    return len(value[:character_offset].encode("utf-8"))


def extract_candidate_matches(
    parts: Sequence[_RedactedPart],
) -> tuple[CandidateMatch, ...]:
    """Return all V1 measurable statements in MIME/match/rule order."""

    ordered: list[tuple[int, int, int, re.Match[str], _Rule, _RedactedPart]] = []
    for part_index, part in enumerate(parts):
        for rule_index, rule in enumerate(_RULES):
            for match in rule.pattern.finditer(part.redacted_text):
                ordered.append(
                    (part_index, match.start(), rule_index, match, rule, part)
                )
    ordered.sort(key=lambda item: item[:3])

    matches: list[CandidateMatch] = []
    for _, _, _, match, rule, part in ordered:
        numeric = Decimal(match.group("value"))
        if rule.percent:
            numeric /= Decimal(100)
        sample_value = (
            match.group(rule.sample_group)
            if rule.sample_group is not None
            else None
        )
        start_byte = _byte_offset(part.redacted_text, match.start())
        end_byte = _byte_offset(part.redacted_text, match.end())
        quote = part.redacted_text.encode("utf-8")[start_byte:end_byte]
        matches.append(
            CandidateMatch(
                projection=CandidateProjection(
                    metric=rule.metric,
                    operator=rule.operator,
                    threshold=_decimal_string(numeric),
                    unit=rule.unit,
                    minimum_samples=(
                        int(sample_value) if sample_value is not None else None
                    ),
                ),
                part_path=part.part_path,
                start_byte=start_byte,
                end_byte=end_byte,
                quote_sha256=hashlib.sha256(quote).hexdigest(),
            )
        )
    return tuple(matches)


__all__ = ["CandidateMatch", "extract_candidate_matches"]
