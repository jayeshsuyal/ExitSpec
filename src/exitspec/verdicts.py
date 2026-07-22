"""Deterministic verdict rules for measured contract criteria."""

from __future__ import annotations

from typing import Dict, Iterable, List

from .models import (
    Criterion,
    CriterionVerdict,
    OverallVerdict,
    ProportionMeasurement,
    VerdictStatus,
)
from .statistics import CALCULATION_VERSION, wilson_lower_bound


def evaluate_proportion_criterion(
    criterion: Criterion, measurement: ProportionMeasurement
) -> CriterionVerdict:
    """Apply the Brick 1 proportion rule without adapter-specific judgment."""

    if measurement.criterion_id != criterion.id:
        raise ValueError("Measurement criterion_id does not match the approved criterion.")

    rule = criterion.rule
    common = {
        "criterion_id": criterion.id,
        "threshold": rule.threshold,
        "sample_count": measurement.sample_count,
        "evidence_refs": measurement.evidence_refs,
        "calculation_version": CALCULATION_VERSION,
    }

    if measurement.external_blocked_reason:
        return CriterionVerdict(
            **common,
            verdict=VerdictStatus.BLOCKED,
            reason=measurement.external_blocked_reason,
            limitations=["Execution did not complete because of an external block."],
        )

    if measurement.internal_error:
        return CriterionVerdict(
            **common,
            verdict=VerdictStatus.NOT_PROVEN,
            reason="Measurement adapter failed internally: {0}".format(
                measurement.internal_error
            ),
            limitations=[
                "An ExitSpec software failure is not evidence that the target system failed."
            ],
        )

    invalid_reasons: List[str] = []
    if not measurement.metadata_complete:
        invalid_reasons.append("required run metadata is missing")
    if not measurement.workload_hash_matches:
        invalid_reasons.append("fixture hash does not match the approved workload")
    if not measurement.artifact_integrity_valid:
        invalid_reasons.append("evidence artifact integrity check failed")
    if invalid_reasons:
        return CriterionVerdict(
            **common,
            verdict=VerdictStatus.NOT_PROVEN,
            reason="Evidence cannot establish the claim because " + "; ".join(invalid_reasons) + ".",
            limitations=["Correct the evidence issue and execute the approved plan again."],
        )

    if measurement.sample_count < rule.minimum_samples:
        return CriterionVerdict(
            **common,
            verdict=VerdictStatus.NOT_PROVEN,
            reason=(
                "Only {0} valid samples were collected; the approved minimum is {1}."
            ).format(measurement.sample_count, rule.minimum_samples),
            limitations=["Missing evidence never passes."],
        )

    observed_rate = measurement.success_count / measurement.sample_count
    lower_bound = wilson_lower_bound(
        measurement.success_count,
        measurement.sample_count,
        rule.confidence_level,
    )
    enriched = {
        **common,
        "observed_rate": observed_rate,
        "confidence_lower_bound": lower_bound,
    }

    if observed_rate < rule.threshold:
        return CriterionVerdict(
            **enriched,
            verdict=VerdictStatus.FAIL,
            reason=(
                "Observed exact-tool-selection rate {0:.2%} is below the approved threshold {1:.2%}."
            ).format(observed_rate, rule.threshold),
            limitations=[],
        )

    if lower_bound >= rule.threshold:
        return CriterionVerdict(
            **enriched,
            verdict=VerdictStatus.PASS,
            reason=(
                "Observed rate {0:.2%}; two-sided {1:.0%} Wilson lower bound {2:.2%} meets the approved threshold {3:.2%}."
            ).format(
                observed_rate,
                rule.confidence_level,
                lower_bound,
                rule.threshold,
            ),
            limitations=[
                "This establishes the approved fixture criterion, not universal production quality."
            ],
        )

    return CriterionVerdict(
        **enriched,
        verdict=VerdictStatus.NOT_PROVEN,
        reason=(
            "Observed rate {0:.2%} meets the point threshold, but the two-sided {1:.0%} Wilson lower bound {2:.2%} does not establish {3:.2%}."
        ).format(observed_rate, rule.confidence_level, lower_bound, rule.threshold),
        limitations=["The result is statistically inconclusive under the approved rule."],
    )


def aggregate_overall_verdict(
    criteria: Iterable[Criterion], verdicts: Iterable[CriterionVerdict]
) -> OverallVerdict:
    """Aggregate must-have criteria using explicit non-compensatory precedence."""

    verdict_by_criterion: Dict[str, CriterionVerdict] = {
        verdict.criterion_id: verdict for verdict in verdicts
    }
    must_have_ids = [criterion.id for criterion in criteria if criterion.must_have]

    missing = [criterion_id for criterion_id in must_have_ids if criterion_id not in verdict_by_criterion]
    if missing:
        return OverallVerdict(
            verdict=VerdictStatus.NOT_PROVEN,
            must_have_criterion_ids=must_have_ids,
            reason="Missing terminal verdicts for must-have criteria: " + ", ".join(missing),
        )

    statuses = [verdict_by_criterion[criterion_id].verdict for criterion_id in must_have_ids]
    if VerdictStatus.FAIL in statuses:
        return OverallVerdict(
            verdict=VerdictStatus.FAIL,
            must_have_criterion_ids=must_have_ids,
            reason="At least one must-have criterion failed with sufficient evidence.",
        )
    if VerdictStatus.BLOCKED in statuses:
        return OverallVerdict(
            verdict=VerdictStatus.BLOCKED,
            must_have_criterion_ids=must_have_ids,
            reason="No must-have criterion failed, but an external condition blocked execution.",
        )
    if VerdictStatus.NOT_PROVEN in statuses:
        return OverallVerdict(
            verdict=VerdictStatus.NOT_PROVEN,
            must_have_criterion_ids=must_have_ids,
            reason="No must-have criterion failed, but at least one cannot be validly established.",
        )
    return OverallVerdict(
        verdict=VerdictStatus.PASS,
        must_have_criterion_ids=must_have_ids,
        reason="Every must-have criterion passed under its approved rule.",
    )
