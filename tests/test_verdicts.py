import pytest

from exitspec.models import (
    CriterionVerdict,
    ProportionMeasurement,
    VerdictStatus,
)
from exitspec.verdicts import aggregate_overall_verdict, evaluate_proportion_criterion


def measurement(criterion_id, samples, successes, **kwargs):
    return ProportionMeasurement(
        criterion_id=criterion_id,
        sample_count=samples,
        success_count=successes,
        evidence_refs=["evidence-" + criterion_id],
        **kwargs,
    )


def test_insufficient_samples_are_not_proven(approved_contract):
    criterion = approved_contract.criteria[0]
    result = evaluate_proportion_criterion(
        criterion, measurement(criterion.id, 100, 100)
    )

    assert result.verdict == VerdictStatus.NOT_PROVEN
    assert "minimum is 200" in result.reason


def test_197_of_200_passes_wilson_rule(approved_contract):
    criterion = approved_contract.criteria[0]
    result = evaluate_proportion_criterion(
        criterion, measurement(criterion.id, 200, 197)
    )

    assert result.verdict == VerdictStatus.PASS
    assert result.confidence_lower_bound == pytest.approx(0.9568342712)


def test_196_of_200_is_statistically_not_proven(approved_contract):
    criterion = approved_contract.criteria[0]
    result = evaluate_proportion_criterion(
        criterion, measurement(criterion.id, 200, 196)
    )

    assert result.verdict == VerdictStatus.NOT_PROVEN
    assert result.observed_rate == pytest.approx(0.98)


def test_rate_below_threshold_fails_with_sufficient_evidence(approved_contract):
    criterion = approved_contract.criteria[0]
    result = evaluate_proportion_criterion(
        criterion, measurement(criterion.id, 200, 189)
    )

    assert result.verdict == VerdictStatus.FAIL


def test_external_block_is_not_a_customer_failure(approved_contract):
    criterion = approved_contract.criteria[0]
    result = evaluate_proportion_criterion(
        criterion,
        measurement(
            criterion.id,
            0,
            0,
            external_blocked_reason="Credentials unavailable.",
        ),
    )

    assert result.verdict == VerdictStatus.BLOCKED


def test_internal_adapter_error_is_not_proven(approved_contract):
    criterion = approved_contract.criteria[0]
    result = evaluate_proportion_criterion(
        criterion,
        measurement(criterion.id, 0, 0, internal_error="Parser crashed."),
    )

    assert result.verdict == VerdictStatus.NOT_PROVEN
    assert "not evidence" in result.limitations[0]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"metadata_complete": False},
        {"workload_hash_matches": False},
        {"artifact_integrity_valid": False},
    ],
)
def test_invalid_evidence_is_not_proven(approved_contract, kwargs):
    criterion = approved_contract.criteria[0]
    result = evaluate_proportion_criterion(
        criterion, measurement(criterion.id, 200, 200, **kwargs)
    )

    assert result.verdict == VerdictStatus.NOT_PROVEN


def make_verdict(criterion_id, status):
    return CriterionVerdict(
        criterion_id=criterion_id,
        verdict=status,
        threshold=0.95,
        sample_count=200,
        calculation_version="test",
        reason="test",
    )


@pytest.mark.parametrize(
    "statuses,expected",
    [
        ([VerdictStatus.PASS, VerdictStatus.FAIL], VerdictStatus.FAIL),
        ([VerdictStatus.FAIL, VerdictStatus.BLOCKED], VerdictStatus.FAIL),
        ([VerdictStatus.PASS, VerdictStatus.BLOCKED], VerdictStatus.BLOCKED),
        ([VerdictStatus.PASS, VerdictStatus.NOT_PROVEN], VerdictStatus.NOT_PROVEN),
    ],
)
def test_overall_verdict_precedence(approved_contract, statuses, expected):
    first = approved_contract.criteria[0]
    second = first.model_copy(update={"id": "TOOL-SELECT-02"})
    overall = aggregate_overall_verdict(
        [first, second],
        [make_verdict(first.id, statuses[0]), make_verdict(second.id, statuses[1])],
    )

    assert overall.verdict == expected


def test_missing_must_have_verdict_is_not_proven(approved_contract):
    result = aggregate_overall_verdict(approved_contract.criteria, [])

    assert result.verdict == VerdictStatus.NOT_PROVEN
