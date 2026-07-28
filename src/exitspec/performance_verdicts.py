"""Pure deterministic measurement and verdict rules for inference performance."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Final, Iterable, Literal

from .models import InferencePerformanceCriterion, VerdictStatus
from .performance_probe import (
    ProbeEvidenceError,
    ProbeOutcome,
    ProbePhase,
    ProbeRecord,
    ProbeRun,
    validate_probe_run,
)


CALCULATION_VERSION: Final = "exitspec.performance-verdicts.v1"
_NANOSECONDS_PER_MILLISECOND: Final = Decimal(1_000_000)
_EXTERNAL_ERROR_OUTCOMES: Final = frozenset(
    {
        ProbeOutcome.HTTP_ERROR.value,
        ProbeOutcome.TIMEOUT.value,
        ProbeOutcome.PROTOCOL_ERROR.value,
        ProbeOutcome.TRANSPORT_ERROR.value,
    }
)
_TERMINAL_NOT_PROVEN_OUTCOMES: Final = frozenset(
    {
        ProbeOutcome.CANCELLED.value,
        "INTERNAL_ERROR",
    }
)


@dataclass(frozen=True, slots=True)
class PerformanceMeasurement:
    """The deterministic facts extracted from measured requests only."""

    attempted_count: int
    successful_count: int
    error_count: int
    p95_ttft_ns: int | None
    evidence_issue: str | None


@dataclass(frozen=True, slots=True)
class TTFTP95RuleResult:
    """Result of the client-observed p95 TTFT acceptance rule."""

    verdict: VerdictStatus
    observed_ns: int | None
    threshold_ns: int | None
    operator: Literal["lt", "lte"]
    successful_samples: int
    minimum_successful_samples: int
    reason: str


@dataclass(frozen=True, slots=True)
class ErrorRateRuleResult:
    """Result of the attempted-request error-rate acceptance rule."""

    verdict: VerdictStatus
    error_count: int
    attempted_count: int
    observed_rate: Decimal | None
    threshold: Decimal
    operator: Literal["lt"]
    minimum_attempts: int
    reason: str


@dataclass(frozen=True, slots=True)
class PerformanceCriterionVerdict:
    """Composite non-compensating verdict for one performance criterion."""

    criterion_id: str
    verdict: VerdictStatus
    attempted_count: int
    successful_count: int
    error_count: int
    ttft_p95: TTFTP95RuleResult
    error_rate: ErrorRateRuleResult
    calculation_version: str
    reason: str
    limitations: tuple[str, ...]


def nearest_rank_p95_ns(values: Iterable[int]) -> int:
    """Return the nearest-rank p95 using integer rank `(95*n+99)//100`."""

    if isinstance(values, (str, bytes)):
        raise ValueError("TTFT values must be an iterable of integer nanoseconds.")
    try:
        ordered = sorted(values)
    except TypeError:
        raise ValueError(
            "TTFT values must be an iterable of integer nanoseconds."
        ) from None
    if not ordered:
        raise ValueError("At least one successful TTFT value is required.")
    if any(
        isinstance(value, bool) or not isinstance(value, int) or value < 0
        for value in ordered
    ):
        raise ValueError("Every TTFT value must be a non-negative integer.")
    rank = (95 * len(ordered) + 99) // 100
    return ordered[rank - 1]


def measure_performance(probe_run: ProbeRun) -> PerformanceMeasurement:
    """Validate a probe run and extract deterministic measured-request facts."""

    if type(probe_run) is not ProbeRun:
        raise TypeError("probe_run must be a ProbeRun.")

    records, selection_issue = _measured_records(probe_run)
    if selection_issue is not None:
        return PerformanceMeasurement(
            attempted_count=len(records),
            successful_count=0,
            error_count=0,
            p95_ttft_ns=None,
            evidence_issue=selection_issue,
        )

    outcome_values = tuple(_outcome_value(record.outcome) for record in records)
    if any(value is None for value in outcome_values):
        return _issue_measurement(records, "A measured request outcome is invalid.")
    if ProbeOutcome.CANCELLED.value in outcome_values:
        return _issue_measurement(
            records,
            "The measured execution was cancelled; incomplete evidence cannot prove the claim.",
        )
    if "INTERNAL_ERROR" in outcome_values:
        return _issue_measurement(
            records,
            "The measurement adapter failed internally; target performance is not proven.",
        )

    try:
        validate_probe_run(probe_run)
    except ProbeEvidenceError:
        return _issue_measurement(
            records,
            "The probe evidence envelope is incomplete, malformed, or inconsistent.",
        )

    successes = tuple(
        record
        for record, outcome in zip(records, outcome_values, strict=True)
        if outcome == ProbeOutcome.SUCCESS.value
    )
    errors = tuple(
        record
        for record, outcome in zip(records, outcome_values, strict=True)
        if outcome in _EXTERNAL_ERROR_OUTCOMES
    )
    if len(successes) + len(errors) != len(records):
        return _issue_measurement(records, "A measured request outcome is unsupported.")

    ttft_values = tuple(record.ttft_ns for record in successes)
    if any(value is None for value in ttft_values):
        return _issue_measurement(
            records,
            "A successful measured request is missing its TTFT value.",
        )
    typed_ttft_values = tuple(value for value in ttft_values if value is not None)
    p95_ttft_ns = (
        nearest_rank_p95_ns(typed_ttft_values) if typed_ttft_values else None
    )
    return PerformanceMeasurement(
        attempted_count=len(records),
        successful_count=len(successes),
        error_count=len(errors),
        p95_ttft_ns=p95_ttft_ns,
        evidence_issue=None,
    )


def evaluate_performance_criterion(
    criterion: InferencePerformanceCriterion,
    probe_run: ProbeRun,
) -> PerformanceCriterionVerdict:
    """Evaluate both required rules with `FAIL > NOT_PROVEN > PASS` precedence."""

    if type(criterion) is not InferencePerformanceCriterion:
        raise TypeError("criterion must be an InferencePerformanceCriterion.")

    measurement = measure_performance(probe_run)
    threshold = Decimal(str(criterion.error_rate.threshold))
    threshold_ns = _ttft_threshold_ns(criterion.ttft_p95.threshold)

    if measurement.evidence_issue is not None or threshold_ns is None:
        issue = measurement.evidence_issue or (
            "The TTFT threshold cannot be represented as an integer number "
            "of nanoseconds."
        )
        ttft_result = TTFTP95RuleResult(
            verdict=VerdictStatus.NOT_PROVEN,
            observed_ns=measurement.p95_ttft_ns,
            threshold_ns=threshold_ns,
            operator=criterion.ttft_p95.operator,
            successful_samples=measurement.successful_count,
            minimum_successful_samples=(
                criterion.ttft_p95.minimum_successful_samples
            ),
            reason=issue,
        )
        error_result = ErrorRateRuleResult(
            verdict=VerdictStatus.NOT_PROVEN,
            error_count=measurement.error_count,
            attempted_count=measurement.attempted_count,
            observed_rate=_observed_error_rate(measurement),
            threshold=threshold,
            operator=criterion.error_rate.operator,
            minimum_attempts=criterion.error_rate.minimum_attempts,
            reason=issue,
        )
        return _composite_verdict(
            criterion,
            measurement,
            ttft_result,
            error_result,
            limitations=("Invalid or incomplete evidence never passes.",),
        )

    ttft_result = _evaluate_ttft_rule(
        criterion,
        measurement,
        threshold_ns=threshold_ns,
    )
    error_result = _evaluate_error_rate_rule(
        criterion,
        measurement,
        threshold=threshold,
    )
    return _composite_verdict(
        criterion,
        measurement,
        ttft_result,
        error_result,
        limitations=(
            "TTFT is client-observed and includes network, proxy, queueing, "
            "and inference time.",
        ),
    )


def _measured_records(
    probe_run: ProbeRun,
) -> tuple[tuple[ProbeRecord, ...], str | None]:
    records: list[ProbeRecord] = []
    try:
        source_records = tuple(probe_run.records)
    except TypeError:
        return (), "Probe records are not a finite sequence."
    for record in source_records:
        if type(record) is not ProbeRecord:
            return tuple(records), "A probe record has an invalid type."
        phase = _enum_value(record.phase)
        if phase == ProbePhase.WARMUP.value:
            if record.included_in_measurement is not False:
                return tuple(records), "A warmup record was marked as measured."
            continue
        if phase == ProbePhase.MEASURED.value:
            if record.included_in_measurement is not True:
                return tuple(records), "A measured record was marked as excluded."
            records.append(record)
            continue
        return tuple(records), "A probe record has an invalid phase."
    return tuple(records), None


def _issue_measurement(
    records: tuple[ProbeRecord, ...],
    issue: str,
) -> PerformanceMeasurement:
    outcomes = tuple(_outcome_value(record.outcome) for record in records)
    successful_count = sum(
        outcome == ProbeOutcome.SUCCESS.value for outcome in outcomes
    )
    error_count = sum(outcome in _EXTERNAL_ERROR_OUTCOMES for outcome in outcomes)
    return PerformanceMeasurement(
        attempted_count=len(records),
        successful_count=successful_count,
        error_count=error_count,
        p95_ttft_ns=None,
        evidence_issue=issue,
    )


def _evaluate_ttft_rule(
    criterion: InferencePerformanceCriterion,
    measurement: PerformanceMeasurement,
    *,
    threshold_ns: int,
) -> TTFTP95RuleResult:
    rule = criterion.ttft_p95
    if measurement.successful_count < rule.minimum_successful_samples:
        return TTFTP95RuleResult(
            verdict=VerdictStatus.NOT_PROVEN,
            observed_ns=measurement.p95_ttft_ns,
            threshold_ns=threshold_ns,
            operator=rule.operator,
            successful_samples=measurement.successful_count,
            minimum_successful_samples=rule.minimum_successful_samples,
            reason=(
                f"Only {measurement.successful_count} successful measured "
                f"requests were available; {rule.minimum_successful_samples} "
                "are required."
            ),
        )
    observed_ns = measurement.p95_ttft_ns
    if observed_ns is None:
        return TTFTP95RuleResult(
            verdict=VerdictStatus.NOT_PROVEN,
            observed_ns=None,
            threshold_ns=threshold_ns,
            operator=rule.operator,
            successful_samples=measurement.successful_count,
            minimum_successful_samples=rule.minimum_successful_samples,
            reason="No successful TTFT measurement is available.",
        )
    passed = (
        observed_ns < threshold_ns
        if rule.operator == "lt"
        else observed_ns <= threshold_ns
    )
    return TTFTP95RuleResult(
        verdict=VerdictStatus.PASS if passed else VerdictStatus.FAIL,
        observed_ns=observed_ns,
        threshold_ns=threshold_ns,
        operator=rule.operator,
        successful_samples=measurement.successful_count,
        minimum_successful_samples=rule.minimum_successful_samples,
        reason=(
            f"Client-observed p95 TTFT is {observed_ns} ns; the approved rule "
            f"is {rule.operator} {threshold_ns} ns."
        ),
    )


def _evaluate_error_rate_rule(
    criterion: InferencePerformanceCriterion,
    measurement: PerformanceMeasurement,
    *,
    threshold: Decimal,
) -> ErrorRateRuleResult:
    rule = criterion.error_rate
    observed_rate = _observed_error_rate(measurement)
    if measurement.attempted_count != rule.minimum_attempts:
        return ErrorRateRuleResult(
            verdict=VerdictStatus.NOT_PROVEN,
            error_count=measurement.error_count,
            attempted_count=measurement.attempted_count,
            observed_rate=observed_rate,
            threshold=threshold,
            operator=rule.operator,
            minimum_attempts=rule.minimum_attempts,
            reason=(
                f"The evidence contains {measurement.attempted_count} measured "
                f"attempts; the frozen v1 workload requires exactly "
                f"{rule.minimum_attempts}."
            ),
        )
    scaled_errors = Decimal(measurement.error_count)
    scaled_threshold = threshold * Decimal(measurement.attempted_count)
    passed = scaled_errors < scaled_threshold
    return ErrorRateRuleResult(
        verdict=VerdictStatus.PASS if passed else VerdictStatus.FAIL,
        error_count=measurement.error_count,
        attempted_count=measurement.attempted_count,
        observed_rate=observed_rate,
        threshold=threshold,
        operator=rule.operator,
        minimum_attempts=rule.minimum_attempts,
        reason=(
            f"Measured errors are {measurement.error_count} of "
            f"{measurement.attempted_count}; the approved rate rule is "
            f"{rule.operator} {threshold}."
        ),
    )


def _composite_verdict(
    criterion: InferencePerformanceCriterion,
    measurement: PerformanceMeasurement,
    ttft_result: TTFTP95RuleResult,
    error_result: ErrorRateRuleResult,
    *,
    limitations: tuple[str, ...],
) -> PerformanceCriterionVerdict:
    statuses = (ttft_result.verdict, error_result.verdict)
    if VerdictStatus.FAIL in statuses:
        verdict = VerdictStatus.FAIL
        reason = "At least one mandatory performance requirement failed."
    elif VerdictStatus.NOT_PROVEN in statuses:
        verdict = VerdictStatus.NOT_PROVEN
        reason = (
            "No mandatory performance requirement failed, but at least one "
            "could not be proven."
        )
    else:
        verdict = VerdictStatus.PASS
        reason = "Both mandatory performance requirements passed."
    return PerformanceCriterionVerdict(
        criterion_id=criterion.id,
        verdict=verdict,
        attempted_count=measurement.attempted_count,
        successful_count=measurement.successful_count,
        error_count=measurement.error_count,
        ttft_p95=ttft_result,
        error_rate=error_result,
        calculation_version=CALCULATION_VERSION,
        reason=reason,
        limitations=limitations,
    )


def _ttft_threshold_ns(threshold_ms: float) -> int | None:
    try:
        threshold = Decimal(str(threshold_ms)) * _NANOSECONDS_PER_MILLISECOND
        integral = threshold.to_integral_exact()
    except (InvalidOperation, ValueError):
        return None
    if threshold != integral or integral <= 0:
        return None
    return int(integral)


def _observed_error_rate(
    measurement: PerformanceMeasurement,
) -> Decimal | None:
    if measurement.attempted_count == 0:
        return None
    return Decimal(measurement.error_count) / Decimal(
        measurement.attempted_count
    )


def _outcome_value(outcome: object) -> str | None:
    value = _enum_value(outcome)
    return value if isinstance(value, str) else None


def _enum_value(value: object) -> object:
    return getattr(value, "value", value)


__all__ = [
    "CALCULATION_VERSION",
    "ErrorRateRuleResult",
    "PerformanceCriterionVerdict",
    "PerformanceMeasurement",
    "TTFTP95RuleResult",
    "evaluate_performance_criterion",
    "measure_performance",
    "nearest_rank_p95_ns",
]
