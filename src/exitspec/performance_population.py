"""Canonical projection of frozen population policy into probe execution."""

from __future__ import annotations

import hashlib

from .canonical import canonical_json_bytes
from .models import InferencePerformanceCriterionV2
from .performance_probe import ProbeMeasurementPolicy


def measurement_policy_sha256(
    criterion: InferencePerformanceCriterionV2,
) -> str:
    """Hash the exact customer-confirmed policy using canonical JSON."""

    if type(criterion) is not InferencePerformanceCriterionV2:
        raise TypeError("criterion must be an InferencePerformanceCriterionV2.")
    payload = criterion.measurement_policy.model_dump(mode="json")
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def project_measurement_policy(
    criterion: InferencePerformanceCriterionV2,
) -> ProbeMeasurementPolicy:
    """Create the only supported execution projection from a v2 criterion."""

    if type(criterion) is not InferencePerformanceCriterionV2:
        raise TypeError("criterion must be an InferencePerformanceCriterionV2.")
    policy = criterion.measurement_policy
    measured = policy.measured_population
    return ProbeMeasurementPolicy(
        schema_version=policy.schema_version,
        policy_sha256=measurement_policy_sha256(criterion),
        calculation_version=policy.calculation_version,
        measured_phases=tuple(measured.phases),
        exact_attempts=measured.exact_attempts,
        warmups_included=measured.warmups_included,
        preflight_included=measured.preflight_included,
        retries=measured.retries,
        latency_population=policy.latency_population.population,
        latency_failed_attempts=policy.latency_population.failed_attempts,
        reliability_numerator=policy.reliability.numerator,
        reliability_denominator=policy.reliability.denominator,
        external_error_outcomes=tuple(policy.reliability.outcomes),
        invalid_terminal_outcomes=tuple(
            policy.invalid_evidence.terminal_outcomes
        ),
        invalid_record_conditions=tuple(
            policy.invalid_evidence.record_conditions
        ),
        integrity_mismatch_disposition=(
            policy.invalid_evidence.integrity_mismatch
        ),
        invalid_evidence_disposition=policy.invalid_evidence.disposition,
    )


__all__ = ["measurement_policy_sha256", "project_measurement_policy"]
