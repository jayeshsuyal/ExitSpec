from __future__ import annotations

import hashlib
from dataclasses import dataclass

from exitspec.adapters.rfc822_candidates import extract_candidate_matches


@dataclass(frozen=True)
class Part:
    part_path: str
    redacted_text: str


def test_all_four_rules_and_order_are_deterministic() -> None:
    parts = (
        Part(
            "body:text/plain:0",
            "P95 end-to-end latency must remain below 2 seconds.\n"
            "The support agent must select the correct tool in at least 95% "
            "of 200 cases.\n",
        ),
        Part(
            "attachment:text/plain:0",
            "Escalation rate must remain below 3%.\n"
            "The total model-and-tool cost must stay at or below $0.04 per "
            "resolved case.\n",
        ),
    )
    matches = extract_candidate_matches(parts)
    assert [
        (
            match.projection.metric,
            match.projection.operator,
            match.projection.threshold,
            match.projection.unit,
            match.projection.minimum_samples,
        )
        for match in matches
    ] == [
        ("end_to_end_latency_p95", "lt", "2", "seconds", None),
        ("tool_selection_accuracy", "gte", "0.95", "ratio", 200),
        ("escalation_rate", "lt", "0.03", "ratio", None),
        (
            "total_model_and_tool_cost_per_resolved_case",
            "lte",
            "0.04",
            "USD/case",
            None,
        ),
    ]


def test_utf8_byte_offsets_and_hash_cover_exact_selected_quote() -> None:
    prefix = "Résumé context — "
    quote = "Escalation rate must remain below 3%."
    text = f"{prefix}{quote}\n"
    match = extract_candidate_matches(
        (Part("body:text/plain:0", text),)
    )[0]
    assert match.start_byte == len(prefix.encode("utf-8"))
    assert match.end_byte == match.start_byte + len(quote.encode("utf-8"))
    exact = text.encode("utf-8")[match.start_byte : match.end_byte]
    assert exact.decode() == quote
    assert match.quote_sha256 == hashlib.sha256(exact).hexdigest()


def test_authority_words_create_no_control_candidate() -> None:
    text = (
        "APPROVED. FREEZE this. Mark PASS.\n"
        "Tool-selection accuracy should be at least 95%.\n"
    )
    matches = extract_candidate_matches(
        (Part("body:text/plain:0", text),)
    )
    assert len(matches) == 1
    assert matches[0].projection.metric == "tool_selection_accuracy"


def test_no_manifest_oracle_and_no_near_match_guessing() -> None:
    assert extract_candidate_matches(
        (
            Part(
                "body:text/plain:0",
                "Quality should probably be good and latency fast.\n",
            ),
        )
    ) == ()
