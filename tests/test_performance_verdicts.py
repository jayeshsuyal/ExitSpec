from __future__ import annotations

import hashlib
from dataclasses import replace

import pytest

from exitspec.models import (
    InferencePerformanceCriterion,
    InferencePerformanceCriterionV2,
    VerdictStatus,
)
from exitspec.performance_population import project_measurement_policy
from exitspec.performance_probe import (
    ProbeConfig,
    ProbeOutcome,
    ProbePhase,
    ProbeRecord,
    ProbeRun,
    SyntheticPrompt,
    build_manifest,
    records_jsonl,
)
from exitspec.performance_verdicts import (
    ErrorRateRuleResult,
    PerformanceCriterionVerdict,
    TTFTP95RuleResult,
    evaluate_performance_criterion,
    measure_performance,
    nearest_rank_p95_ns,
)


EXECUTION_ID = "run_" + "a" * 32


def criterion(
    *,
    ttft_threshold_ms: float = 500.0,
    ttft_operator: str = "lte",
    minimum_successful_samples: int = 100,
    error_threshold: float = 0.01,
    minimum_attempts: int = 100,
) -> InferencePerformanceCriterion:
    return InferencePerformanceCriterion.model_validate(
        {
            "criterion_type": "inference_performance_v1",
            "id": "PERF-LATENCY-01",
            "title": "Inference latency and reliability",
            "must_have": True,
            "source": {
                "speaker": "customer",
                "quote": "P95 TTFT and error rate must meet the agreed limits.",
                "location": "synthetic:1",
            },
            "human_added": False,
            "normalized_claim": "The frozen inference workload meets both limits.",
            "ttft_p95": {
                "metric": "time_to_first_token",
                "aggregation": "p95",
                "unit": "milliseconds",
                "operator": ttft_operator,
                "threshold": ttft_threshold_ms,
                "method": "nearest_rank",
                "minimum_successful_samples": minimum_successful_samples,
                "must_pass": True,
            },
            "error_rate": {
                "metric": "error_rate",
                "aggregation": "rate",
                "unit": "proportion",
                "operator": "lt",
                "threshold": error_threshold,
                "method": "failed_attempts_over_total_attempts",
                "minimum_attempts": minimum_attempts,
                "must_pass": True,
            },
            "workload_slice": "performance-v1",
            "adapter": "vllm_latency",
            "adapter_version": "1.0.0",
            "owner": "solutions_engineering",
            "evidence_policy": "Retain complete request records and hashes.",
            "approved": True,
        }
    )


def criterion_v2(**updates: object) -> InferencePerformanceCriterionV2:
    base = criterion(**updates).model_dump(mode="python")
    base["criterion_type"] = "inference_performance_v2"
    base["measurement_policy"] = {
        "schema_version": "exitspec.measurement-population.v1",
        "calculation_version": "exitspec.performance-verdicts.v2",
        "measured_population": {
            "phases": ["MEASURED"],
            "exact_attempts": base["error_rate"]["minimum_attempts"],
            "warmups_included": False,
            "preflight_included": False,
            "retries": 0,
        },
        "latency_population": {
            "population": "successful_measured_attempts_with_valid_ttft",
            "failed_attempts": (
                "excluded_from_latency_counted_in_reliability"
            ),
        },
        "reliability": {
            "numerator": "external_error_outcomes",
            "denominator": "all_measured_attempts",
            "outcomes": [
                "HTTP_ERROR",
                "TIMEOUT",
                "PROTOCOL_ERROR",
                "TRANSPORT_ERROR",
            ],
        },
        "invalid_evidence": {
            "terminal_outcomes": ["CANCELLED", "INTERNAL_ERROR"],
            "record_conditions": [
                "MISSING_RECORD",
                "DUPLICATE_RECORD",
                "EXTRA_RECORD",
            ],
            "integrity_mismatch": "NOT_PROVEN",
            "disposition": "NOT_PROVEN",
        },
    }
    return InferencePerformanceCriterionV2.model_validate(base)


def probe_run(
    measured: list[tuple[object, int | None]],
    *,
    warmups: list[tuple[object, int | None]] | None = None,
    measurement_criterion: InferencePerformanceCriterionV2 | None = None,
) -> ProbeRun:
    warmups = warmups or []
    config = ProbeConfig(
        endpoint="http://127.0.0.1:8000/v1/chat/completions",
        model="synthetic/tiny-model",
        request_count=len(measured),
        concurrency=1,
        warmup_count=len(warmups),
        timeout_seconds=1,
        max_tokens=16,
        measurement_policy=(
            None
            if measurement_criterion is None
            else project_measurement_policy(measurement_criterion)
        ),
    )
    manifest = build_manifest(
        config,
        (SyntheticPrompt("prompt-1", "synthetic prompt"),),
    )
    descriptor = manifest.prompts[0]
    records: list[ProbeRecord] = []
    for phase, items in (
        (ProbePhase.WARMUP, warmups),
        (ProbePhase.MEASURED, measured),
    ):
        for ordinal, (outcome, ttft_ns) in enumerate(items, start=1):
            outcome_value = getattr(outcome, "value", outcome)
            success = outcome_value == ProbeOutcome.SUCCESS.value
            http_status = (
                200
                if success
                else 429
                if outcome_value == ProbeOutcome.HTTP_ERROR.value
                else None
            )
            duration_ns = (ttft_ns + 1) if ttft_ns is not None else 1
            records.append(
                ProbeRecord(
                    schema_version=manifest.schema_version,
                    execution_id=EXECUTION_ID,
                    manifest_sha256=manifest.manifest_sha256,
                    request_id=(
                        "warmup" if phase is ProbePhase.WARMUP else "measured"
                    )
                    + f"-{ordinal:05d}",
                    phase=phase,
                    ordinal=ordinal,
                    included_in_measurement=phase is ProbePhase.MEASURED,
                    prompt_id=descriptor.prompt_id,
                    prompt_sha256=descriptor.sha256,
                    outcome=outcome,  # type: ignore[arg-type]
                    http_status=http_status,
                    ttft_ns=ttft_ns,
                    duration_ns=duration_ns,
                )
            )
    ordered = tuple(records)
    try:
        records_sha256 = hashlib.sha256(
            records_jsonl(ordered).encode("utf-8")
        ).hexdigest()
    except AttributeError:
        records_sha256 = "0" * 64
    return ProbeRun(
        execution_id=EXECUTION_ID,
        manifest=manifest,
        records_sha256=records_sha256,
        records=ordered,
    )


@pytest.mark.parametrize(
    ("size", "expected"),
    [
        (1, 1),
        (20, 19),
        (99, 95),
        (100, 95),
    ],
)
def test_nearest_rank_p95_integer_vectors(size, expected):
    values = list(range(size, 0, -1))

    assert nearest_rank_p95_ns(values) == expected


@pytest.mark.parametrize(
    "values",
    [
        [],
        [True],
        [-1],
        [1.5],
        "123",
        [1, "2"],
    ],
)
def test_nearest_rank_rejects_malformed_inputs(values):
    with pytest.raises(ValueError):
        nearest_rank_p95_ns(values)


def test_complete_valid_measurement_passes_both_immutable_typed_rules():
    run = probe_run([(ProbeOutcome.SUCCESS, 100_000_000)] * 100)

    result = evaluate_performance_criterion(criterion(), run)

    assert isinstance(result, PerformanceCriterionVerdict)
    assert isinstance(result.ttft_p95, TTFTP95RuleResult)
    assert isinstance(result.error_rate, ErrorRateRuleResult)
    assert result.verdict is VerdictStatus.PASS
    assert result.ttft_p95.verdict is VerdictStatus.PASS
    assert result.ttft_p95.observed_ns == 100_000_000
    assert result.ttft_p95.threshold_ns == 500_000_000
    assert result.error_rate.verdict is VerdictStatus.PASS
    assert result.error_rate.error_count == 0
    assert result.attempted_count == 100
    with pytest.raises(AttributeError):
        result.verdict = VerdictStatus.FAIL  # type: ignore[misc]


def test_exactly_one_error_of_100_fails_strict_below_one_percent():
    run = probe_run(
        [(ProbeOutcome.SUCCESS, 100_000_000)] * 99
        + [(ProbeOutcome.HTTP_ERROR, None)]
    )

    result = evaluate_performance_criterion(criterion(), run)

    assert result.error_rate.error_count == 1
    assert result.error_rate.attempted_count == 100
    assert result.error_rate.verdict is VerdictStatus.FAIL
    assert result.ttft_p95.verdict is VerdictStatus.NOT_PROVEN
    assert result.verdict is VerdictStatus.FAIL


def test_v2_policy_counts_each_terminal_outcome_and_uses_all_attempts():
    approved = criterion_v2(
        minimum_successful_samples=95,
        error_threshold=0.05,
    )
    run = probe_run(
        [(ProbeOutcome.SUCCESS, 100_000_000)] * 96
        + [
            (ProbeOutcome.HTTP_ERROR, None),
            (ProbeOutcome.TIMEOUT, None),
            (ProbeOutcome.PROTOCOL_ERROR, None),
            (ProbeOutcome.TRANSPORT_ERROR, None),
        ],
        measurement_criterion=approved,
    )

    result = evaluate_performance_criterion(approved, run)

    assert result.calculation_version == "exitspec.performance-verdicts.v2"
    assert result.attempted_count == 100
    assert result.successful_count == 96
    assert result.error_count == 4
    assert result.error_rate.attempted_count == 100
    assert result.outcome_counts is not None
    assert result.outcome_counts.success == 96
    assert result.outcome_counts.http_error == 1
    assert result.outcome_counts.timeout == 1
    assert result.outcome_counts.protocol_error == 1
    assert result.outcome_counts.transport_error == 1
    assert result.verdict is VerdictStatus.PASS


def test_v2_one_timeout_of_100_fails_strict_below_one_percent():
    approved = criterion_v2(minimum_successful_samples=99)
    run = probe_run(
        [(ProbeOutcome.SUCCESS, 100_000_000)] * 99
        + [(ProbeOutcome.TIMEOUT, None)],
        measurement_criterion=approved,
    )

    result = evaluate_performance_criterion(approved, run)

    assert result.error_rate.error_count == 1
    assert result.error_rate.attempted_count == 100
    assert result.error_rate.verdict is VerdictStatus.FAIL
    assert result.outcome_counts is not None
    assert result.outcome_counts.timeout == 1
    assert result.verdict is VerdictStatus.FAIL


def test_v2_cancelled_or_missing_evidence_is_not_proven():
    approved = criterion_v2(minimum_successful_samples=99)
    cancelled = probe_run(
        [(ProbeOutcome.SUCCESS, 100_000_000)] * 99
        + [(ProbeOutcome.CANCELLED, None)],
        measurement_criterion=approved,
    )
    complete = probe_run(
        [(ProbeOutcome.SUCCESS, 100_000_000)] * 100,
        measurement_criterion=approved,
    )
    missing = replace(complete, records=complete.records[:-1])

    cancelled_result = evaluate_performance_criterion(approved, cancelled)
    missing_result = evaluate_performance_criterion(approved, missing)

    assert cancelled_result.verdict is VerdictStatus.NOT_PROVEN
    assert cancelled_result.outcome_counts is not None
    assert cancelled_result.outcome_counts.cancelled == 1
    assert missing_result.verdict is VerdictStatus.NOT_PROVEN


def test_v2_unbound_probe_manifest_is_not_proven():
    approved = criterion_v2()
    unbound = probe_run([(ProbeOutcome.SUCCESS, 100_000_000)] * 100)

    result = evaluate_performance_criterion(approved, unbound)

    assert result.verdict is VerdictStatus.NOT_PROVEN
    assert "frozen measurement population" in result.reason or (
        "frozen measurement population" in result.ttft_p95.reason
    )


def test_zero_errors_of_100_passes_strict_below_one_percent():
    result = evaluate_performance_criterion(
        criterion(),
        probe_run([(ProbeOutcome.SUCCESS, 100_000_000)] * 100),
    )

    assert result.error_rate.verdict is VerdictStatus.PASS
    assert result.verdict is VerdictStatus.PASS


def test_extra_attempts_cannot_change_the_frozen_sample_population():
    result = evaluate_performance_criterion(
        criterion(),
        probe_run([(ProbeOutcome.SUCCESS, 100_000_000)] * 101),
    )

    assert result.error_rate.verdict is VerdictStatus.NOT_PROVEN
    assert "exactly 100" in result.error_rate.reason
    assert result.verdict is VerdictStatus.NOT_PROVEN


def test_warmups_do_not_affect_counts_p95_or_error_rate():
    measured = [
        (ProbeOutcome.SUCCESS, value * 1_000_000)
        for value in range(1, 21)
    ]
    run = probe_run(
        measured,
        warmups=[
            (ProbeOutcome.SUCCESS, 99_000_000_000),
            (ProbeOutcome.HTTP_ERROR, None),
        ],
    )

    result = evaluate_performance_criterion(
        criterion(
            ttft_threshold_ms=20,
            minimum_successful_samples=20,
            minimum_attempts=20,
        ),
        run,
    )

    assert result.attempted_count == 20
    assert result.successful_count == 20
    assert result.error_count == 0
    assert result.ttft_p95.observed_ns == 19_000_000
    assert result.verdict is VerdictStatus.PASS


@pytest.mark.parametrize(
    "outcome",
    [
        ProbeOutcome.HTTP_ERROR,
        ProbeOutcome.TIMEOUT,
        ProbeOutcome.PROTOCOL_ERROR,
        ProbeOutcome.TRANSPORT_ERROR,
    ],
)
def test_external_measured_errors_count_toward_fail(outcome):
    run = probe_run(
        [(ProbeOutcome.SUCCESS, 100_000_000)] * 99 + [(outcome, None)]
    )

    result = evaluate_performance_criterion(
        criterion(minimum_successful_samples=99),
        run,
    )

    assert result.error_count == 1
    assert result.error_rate.verdict is VerdictStatus.FAIL
    assert result.verdict is VerdictStatus.FAIL


@pytest.mark.parametrize(
    "outcome",
    [
        ProbeOutcome.CANCELLED,
        "INTERNAL_ERROR",
    ],
)
def test_cancelled_and_internal_error_are_not_proven(outcome):
    run = probe_run(
        [(ProbeOutcome.SUCCESS, 100_000_000)] * 99 + [(outcome, None)]
    )

    result = evaluate_performance_criterion(criterion(), run)

    assert result.ttft_p95.verdict is VerdictStatus.NOT_PROVEN
    assert result.error_rate.verdict is VerdictStatus.NOT_PROVEN
    assert result.verdict is VerdictStatus.NOT_PROVEN
    assert result.verdict is not VerdictStatus.BLOCKED


def test_too_few_successes_makes_ttft_not_proven_without_blocking():
    run = probe_run(
        [(ProbeOutcome.SUCCESS, 100_000_000)] * 99
        + [(ProbeOutcome.HTTP_ERROR, None)]
    )

    result = evaluate_performance_criterion(
        criterion(error_threshold=0.02),
        run,
    )

    assert result.ttft_p95.verdict is VerdictStatus.NOT_PROVEN
    assert result.error_rate.verdict is VerdictStatus.PASS
    assert result.verdict is VerdictStatus.NOT_PROVEN
    assert result.verdict is not VerdictStatus.BLOCKED


def test_composite_precedence_is_fail_over_not_proven_over_pass():
    fail_over_not_proven = evaluate_performance_criterion(
        criterion(
            ttft_threshold_ms=50,
            minimum_successful_samples=1,
            minimum_attempts=100,
        ),
        probe_run([(ProbeOutcome.SUCCESS, 100_000_000)]),
    )
    not_proven_over_pass = evaluate_performance_criterion(
        criterion(error_threshold=0.02),
        probe_run(
            [(ProbeOutcome.SUCCESS, 100_000_000)] * 99
            + [(ProbeOutcome.HTTP_ERROR, None)]
        ),
    )

    assert fail_over_not_proven.ttft_p95.verdict is VerdictStatus.FAIL
    assert fail_over_not_proven.error_rate.verdict is VerdictStatus.NOT_PROVEN
    assert fail_over_not_proven.verdict is VerdictStatus.FAIL
    assert not_proven_over_pass.ttft_p95.verdict is VerdictStatus.NOT_PROVEN
    assert not_proven_over_pass.error_rate.verdict is VerdictStatus.PASS
    assert not_proven_over_pass.verdict is VerdictStatus.NOT_PROVEN


def test_ttft_operator_boundaries_use_integer_nanoseconds():
    run = probe_run([(ProbeOutcome.SUCCESS, 500_000_000)])

    inclusive = evaluate_performance_criterion(
        criterion(
            ttft_operator="lte",
            minimum_successful_samples=1,
            minimum_attempts=1,
        ),
        run,
    )
    strict = evaluate_performance_criterion(
        criterion(
            ttft_operator="lt",
            minimum_successful_samples=1,
            minimum_attempts=1,
        ),
        run,
    )

    assert inclusive.ttft_p95.verdict is VerdictStatus.PASS
    assert strict.ttft_p95.verdict is VerdictStatus.FAIL


def test_error_rate_boundary_is_decimal_exact():
    run = probe_run(
        [(ProbeOutcome.SUCCESS, 100_000_000)] * 9
        + [(ProbeOutcome.HTTP_ERROR, None)]
    )

    strict = evaluate_performance_criterion(
        criterion(
            minimum_successful_samples=9,
            error_threshold=0.1,
            minimum_attempts=10,
        ),
        run,
    )

    assert strict.error_rate.verdict is VerdictStatus.FAIL


def test_malformed_success_record_fails_closed_to_not_proven():
    valid = probe_run([(ProbeOutcome.SUCCESS, 100_000_000)])
    malformed_record = replace(valid.records[0], ttft_ns=None)
    malformed = replace(valid, records=(malformed_record,))

    result = evaluate_performance_criterion(
        criterion(
            minimum_successful_samples=1,
            minimum_attempts=1,
        ),
        malformed,
    )

    assert result.verdict is VerdictStatus.NOT_PROVEN
    assert "malformed" in result.ttft_p95.reason


def test_missing_or_duplicate_records_fail_closed_to_not_proven():
    valid = probe_run(
        [
            (ProbeOutcome.SUCCESS, 100_000_000),
            (ProbeOutcome.SUCCESS, 100_000_000),
        ]
    )
    missing = replace(valid, records=(valid.records[0],))
    duplicate = replace(valid, records=(valid.records[0], valid.records[0]))

    for malformed in (missing, duplicate):
        result = evaluate_performance_criterion(
            criterion(
                minimum_successful_samples=1,
                minimum_attempts=1,
            ),
            malformed,
        )
        assert result.verdict is VerdictStatus.NOT_PROVEN


def test_public_functions_reject_wrong_top_level_types():
    with pytest.raises(TypeError, match="ProbeRun"):
        measure_performance(object())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="InferencePerformanceCriterion"):
        evaluate_performance_criterion(  # type: ignore[arg-type]
            object(),
            probe_run([(ProbeOutcome.SUCCESS, 1)]),
        )
