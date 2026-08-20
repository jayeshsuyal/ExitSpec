from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from exitspec.inferdrome_managed_import import (
    InferdromeManagedImportErrorCode,
    InferdromeManagedImportRejected,
    ManagedApplicability,
    _evaluate_managed_verdict,
    import_managed_inferdrome_bundle,
)
from exitspec.inferdrome_bundle import RecalculatedInferdromeMeasurements
from exitspec.inferdrome_external_contract import (
    InferdromeManagedContextError,
    validate_managed_contract_context,
)
from exitspec.inferdrome_profile import PINNED_BUNDLE_DIGEST
from exitspec.inferdrome_reporting_v2 import (
    ManagedApplicabilityCode,
    managed_receipt_sha256,
    validate_managed_receipt,
)
from exitspec.models import VerdictStatus
from tests.inferdrome_managed_helpers import (
    FIXED_RECEIPT_TIME,
    build_managed_contract,
    extract_exact_archive_or_skip,
)


def test_v3_contract_freezes_exact_external_identity_without_archive_access():
    contract, confirmation = build_managed_contract(
        contract_id="a10-retrospective-contract-lock",
        threshold_ns=20_000_000,
    )

    context = validate_managed_contract_context(contract, confirmation)

    assert context.contract.canonical_hash == contract.canonical_hash
    assert context.criterion.evidence_identity.configured_max_concurrency == 4
    assert context.criterion.ttft_p95.threshold_ns == 20_000_000
    assert context.criterion.ttft_p95.minimum_successful_samples == 100
    assert context.criterion.ttft_p95.operator == "lt"
    assert context.criterion.error_rate.threshold_basis_points == 100


def test_managed_context_rejects_a_frozen_contract_for_the_wrong_provider():
    contract, confirmation = build_managed_contract(
        contract_id="a10-retrospective-wrong-provider",
        threshold_ns=20_000_000,
        target_provider="unrelated-provider",
    )

    with pytest.raises(InferdromeManagedContextError, match="target or workload"):
        validate_managed_contract_context(contract, confirmation)


def test_one_failure_of_one_hundred_fails_the_strict_one_percent_rule():
    contract, confirmation = build_managed_contract(
        contract_id="a10-retrospective-denominator-lock",
        threshold_ns=20_000_000,
    )
    context = validate_managed_contract_context(contract, confirmation)
    measurement = RecalculatedInferdromeMeasurements(
        attempted_count=100,
        successful_count=99,
        failed_count=1,
        anomalous_count=0,
        error_rate=Decimal("0.01"),
        p95_ttft_ns=14_797_213,
        ttft_definition="vllm_first_choices_event_v0_26",
        records_sha256="1" * 64,
        recalculation_sha256="2" * 64,
    )

    verdict = _evaluate_managed_verdict(
        measurement,
        context.criterion,
        ManagedApplicability(issues=()),
    )

    assert verdict is VerdictStatus.FAIL


def test_internal_verdict_calculation_cannot_bypass_population_checks():
    contract, confirmation = build_managed_contract(
        contract_id="a10-retrospective-population-bypass",
        threshold_ns=20_000_000,
    )
    context = validate_managed_contract_context(contract, confirmation)
    measurement = RecalculatedInferdromeMeasurements(
        attempted_count=99,
        successful_count=99,
        failed_count=0,
        anomalous_count=0,
        error_rate=Decimal("0"),
        p95_ttft_ns=14_797_213,
        ttft_definition="vllm_first_choices_event_v0_26",
        records_sha256="1" * 64,
        recalculation_sha256="2" * 64,
    )

    verdict = _evaluate_managed_verdict(
        measurement,
        context.criterion,
        ManagedApplicability(issues=()),
    )

    assert verdict is VerdictStatus.NOT_PROVEN


def test_proven_reliability_failure_precedes_ttft_sample_shortfall():
    contract, confirmation = build_managed_contract(
        contract_id="a10-retrospective-composite-precedence",
        threshold_ns=20_000_000,
    )
    context = validate_managed_contract_context(contract, confirmation)
    measurement = RecalculatedInferdromeMeasurements(
        attempted_count=100,
        successful_count=90,
        failed_count=10,
        anomalous_count=0,
        error_rate=Decimal("0.1"),
        p95_ttft_ns=14_797_213,
        ttft_definition="vllm_first_choices_event_v0_26",
        records_sha256="1" * 64,
        recalculation_sha256="2" * 64,
    )

    verdict = _evaluate_managed_verdict(
        measurement,
        context.criterion,
        ManagedApplicability(
            issues=(ManagedApplicabilityCode.SUCCESSFUL_SAMPLE_SHORTFALL,)
        ),
    )

    assert verdict is VerdictStatus.FAIL


def test_exact_managed_bundle_produces_pass_and_bound_v2_receipt(tmp_path):
    extracted = extract_exact_archive_or_skip(tmp_path)
    contract, confirmation = build_managed_contract(
        contract_id="a10-retrospective-pass",
        threshold_ns=20_000_000,
    )

    result = import_managed_inferdrome_bundle(
        extracted.bundle_path,
        contract,
        confirmation,
        received_at=FIXED_RECEIPT_TIME,
    )

    assert result.verdict is VerdictStatus.PASS
    assert result.applicability.issues == ()
    assert result.recalculated.p95_ttft_ns == 14_797_213
    assert result.receipt.receipt_id.startswith("irc2_")
    assert result.receipt.bundle_digest == PINNED_BUNDLE_DIGEST
    assert result.receipt.contract_hash == contract.canonical_hash
    assert result.receipt.acceptance_verdict == "PASS"
    assert result.receipt.ingestion_status == "ACCEPTED"
    assert result.receipt.metric.recalculated_value_ns == 14_797_213
    assert result.receipt.population.attempted_count == 100
    assert result.receipt.population.successful_count == 100
    assert result.receipt.population.required_configured_max_concurrency == 4
    assert result.receipt.population.observed_configured_max_concurrency == 4
    assert result.receipt.population.observed_error_rate == "0"
    assert result.receipt.assurance.temporal_assurance == "RETROSPECTIVE"
    assert result.receipt.assurance.contract_preceded_measurement is False
    assert result.receipt.assurance.hardware_attestation == "NOT_AVAILABLE"
    assert result.receipt.assurance.execution_attestation == "NOT_AVAILABLE"
    assert result.receipt.assurance.transport_retry_behavior == "NOT_AVAILABLE"
    assert result.receipt.assurance.production_authorization is False
    assert result.receipt.purpose == "CONFORMANCE_DEMONSTRATION"
    assert validate_managed_receipt(result.receipt) == result.receipt
    assert managed_receipt_sha256(result.receipt).startswith("sha256:")


def test_exact_managed_bundle_threshold_violation_is_fail(tmp_path):
    extracted = extract_exact_archive_or_skip(tmp_path)
    contract, confirmation = build_managed_contract(
        contract_id="a10-retrospective-fail",
        threshold_ns=10_000_000,
    )

    result = import_managed_inferdrome_bundle(
        extracted.bundle_path,
        contract,
        confirmation,
        received_at=FIXED_RECEIPT_TIME,
    )

    assert result.verdict is VerdictStatus.FAIL
    assert result.applicability.issues == ()
    assert result.receipt.acceptance_verdict == "FAIL"


def test_exact_native_threshold_is_strict_at_the_observed_boundary(tmp_path):
    extracted = extract_exact_archive_or_skip(tmp_path)
    pass_contract, pass_confirmation = build_managed_contract(
        contract_id="a10-retrospective-boundary-pass",
        threshold_ns=14_797_214,
    )
    fail_contract, fail_confirmation = build_managed_contract(
        contract_id="a10-retrospective-boundary-fail",
        threshold_ns=14_797_213,
    )

    passing = import_managed_inferdrome_bundle(
        extracted.bundle_path,
        pass_contract,
        pass_confirmation,
        received_at=FIXED_RECEIPT_TIME,
    )
    failing = import_managed_inferdrome_bundle(
        extracted.bundle_path,
        fail_contract,
        fail_confirmation,
        received_at=FIXED_RECEIPT_TIME,
    )

    assert passing.verdict is VerdictStatus.PASS
    assert failing.verdict is VerdictStatus.FAIL
    assert passing.receipt.metric.operator == "lt"
    assert failing.receipt.metric.operator == "lt"


def test_configured_concurrency_mismatch_is_not_proven(tmp_path):
    extracted = extract_exact_archive_or_skip(tmp_path)
    contract, confirmation = build_managed_contract(
        contract_id="a10-retrospective-concurrency-eight",
        threshold_ns=20_000_000,
        configured_max_concurrency=8,
    )

    result = import_managed_inferdrome_bundle(
        extracted.bundle_path,
        contract,
        confirmation,
        received_at=FIXED_RECEIPT_TIME,
    )

    assert result.verdict is VerdictStatus.NOT_PROVEN
    assert result.applicability.issues == (
        ManagedApplicabilityCode.CONFIGURED_CONCURRENCY_MISMATCH,
    )
    assert result.receipt.acceptance_verdict == "NOT_PROVEN"
    assert result.receipt.population.required_configured_max_concurrency == 8
    assert result.receipt.population.observed_configured_max_concurrency == 4
    assert result.receipt.assurance.exact_achieved_concurrency == "NOT_AVAILABLE"


def test_first_nonempty_content_rule_is_not_satisfied_by_native_ttft(tmp_path):
    extracted = extract_exact_archive_or_skip(tmp_path)
    contract, confirmation = build_managed_contract(
        contract_id="a10-retrospective-content-ttft",
        threshold_ns=20_000_000,
        definition_id="first_nonempty_choices_delta_content_v1",
    )

    result = import_managed_inferdrome_bundle(
        extracted.bundle_path,
        contract,
        confirmation,
        received_at=FIXED_RECEIPT_TIME,
    )

    assert result.verdict is VerdictStatus.NOT_PROVEN
    assert result.applicability.issues == (
        ManagedApplicabilityCode.METRIC_DEFINITION_MISMATCH,
    )
    assert result.receipt.metric.requested_definition_id == (
        "first_nonempty_choices_delta_content_v1"
    )
    assert result.receipt.metric.observed_definition_id == (
        "vllm_first_choices_event_v0_26"
    )


def test_generic_null_link_exception_cannot_bind_an_unpinned_digest(tmp_path):
    extracted = extract_exact_archive_or_skip(tmp_path)
    contract, confirmation = build_managed_contract(
        contract_id="a10-retrospective-wrong-digest",
        threshold_ns=20_000_000,
    )

    with pytest.raises(InferdromeManagedImportRejected) as caught:
        import_managed_inferdrome_bundle(
            extracted.bundle_path,
            contract,
            confirmation,
            expected_bundle_digest="sha256:" + "0" * 64,
            received_at=FIXED_RECEIPT_TIME,
        )

    assert caught.value.code is InferdromeManagedImportErrorCode.UNSUPPORTED_BINDING


def test_managed_import_requires_the_matching_customer_confirmation(tmp_path):
    extracted = extract_exact_archive_or_skip(tmp_path)
    contract, confirmation = build_managed_contract(
        contract_id="a10-retrospective-confirmation",
        threshold_ns=20_000_000,
    )
    wrong = confirmation.model_copy(update={"confirmation_id": "cnf_" + "0" * 64})

    with pytest.raises(InferdromeManagedImportRejected) as caught:
        import_managed_inferdrome_bundle(
            extracted.bundle_path,
            contract,
            wrong,
            received_at=FIXED_RECEIPT_TIME,
        )

    assert caught.value.code is (
        InferdromeManagedImportErrorCode.CONTEXT_NOT_AUTHORIZED
    )


def test_managed_receipt_identity_binds_verdict_time_and_applicability(tmp_path):
    extracted = extract_exact_archive_or_skip(tmp_path)
    contract, confirmation = build_managed_contract(
        contract_id="a10-retrospective-receipt-lock",
        threshold_ns=20_000_000,
    )
    result = import_managed_inferdrome_bundle(
        extracted.bundle_path,
        contract,
        confirmation,
        received_at=FIXED_RECEIPT_TIME,
    )

    with pytest.raises(ValidationError, match="receipt_id"):
        validate_managed_receipt(
            result.receipt.model_copy(update={"acceptance_verdict": "FAIL"})
        )
    with pytest.raises(ValidationError, match="receipt_id"):
        validate_managed_receipt(
            result.receipt.model_copy(
                update={"received_at": FIXED_RECEIPT_TIME + timedelta(seconds=1)}
            )
        )
    with pytest.raises(ValidationError, match="applicability_codes"):
        validate_managed_receipt(
            result.receipt.model_copy(
                update={
                    "applicability_codes": (
                        ManagedApplicabilityCode.CONFIGURED_CONCURRENCY_MISMATCH,
                        ManagedApplicabilityCode.CONFIGURED_CONCURRENCY_MISMATCH,
                    )
                }
            )
        )
