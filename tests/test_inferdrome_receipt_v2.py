from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from exitspec.inferdrome_profile import (
    LOCAL_GPU_PROOF_SCHEMA_ID,
    LOCAL_GPU_PROOF_SCHEMA_SHA256,
    MANAGED_PROFILE_ID,
    MANAGED_PROFILE_SHA256,
    PINNED_BUNDLE_DIGEST,
    PINNED_RUN_ID,
)
from exitspec.inferdrome_reporting_v2 import (
    INFERDROME_MANAGED_CALCULATION_VERSION,
    INFERDROME_MANAGED_RECEIPT_SCHEMA_VERSION,
    INFERDROME_MANAGED_VERIFIER_VERSION,
    InferdromeManagedReceiptV2,
    ManagedApplicabilityCode,
    ManagedEvidenceAssuranceV1,
    ManagedMetricReceiptV1,
    ManagedPopulationReceiptV1,
    ManagedTargetReceiptV1,
    managed_receipt_id,
    managed_receipt_sha256,
    validate_managed_receipt,
)
from tests.inferdrome_managed_helpers import (
    FIXED_RECEIPT_TIME,
    REQUEST_PLAN_DIGEST,
    WORKLOAD_DIGEST,
)


def test_portable_managed_receipt_round_trip_and_digest_are_deterministic():
    receipt = _receipt()

    assert validate_managed_receipt(receipt) == receipt
    assert managed_receipt_sha256(receipt) == managed_receipt_sha256(_receipt())
    assert receipt.receipt_id.startswith("irc2_")


def test_portable_managed_receipt_rejects_any_unbound_mutation():
    receipt = _receipt()

    with pytest.raises(ValidationError, match="receipt_id"):
        validate_managed_receipt(
            receipt.model_copy(update={"acceptance_verdict": "FAIL"})
        )
    with pytest.raises(ValidationError, match="receipt_id"):
        validate_managed_receipt(
            receipt.model_copy(
                update={"requested_request_plan_digest": "sha256:" + "0" * 64}
            )
        )
    with pytest.raises(ValidationError, match="receipt_id"):
        validate_managed_receipt(
            receipt.model_copy(
                update={"received_at": datetime(2026, 8, 21, 12, 0, 1, tzinfo=UTC)}
            )
        )


def test_portable_managed_receipt_requires_canonical_applicability_order():
    receipt = _receipt()

    with pytest.raises(ValidationError, match="applicability_codes"):
        validate_managed_receipt(
            receipt.model_copy(
                update={
                    "applicability_codes": (
                        ManagedApplicabilityCode.METRIC_DEFINITION_MISMATCH,
                        ManagedApplicabilityCode.CONFIGURED_CONCURRENCY_MISMATCH,
                    )
                }
            )
        )


def test_portable_managed_receipt_rejects_rehashed_contradictory_verdict():
    receipt = _receipt()
    payload = receipt.model_dump(mode="python", exclude={"receipt_id"})
    payload["acceptance_verdict"] = "FAIL"

    with pytest.raises(ValidationError, match="verdict contradicts"):
        InferdromeManagedReceiptV2(
            receipt_id=managed_receipt_id(payload),
            **payload,
        )


def test_portable_managed_receipt_rejects_rehashed_population_contradiction():
    receipt = _receipt()
    payload = receipt.model_dump(mode="python", exclude={"receipt_id"})
    population = dict(payload["population"])
    population["failed_count"] = 1
    population["successful_count"] = 99
    payload["population"] = population

    with pytest.raises(ValidationError, match="error rate disagrees"):
        InferdromeManagedReceiptV2(
            receipt_id=managed_receipt_id(payload),
            **payload,
        )


def test_portable_managed_receipt_requires_codes_for_visible_mismatches():
    receipt = _receipt()
    payload = receipt.model_dump(mode="python", exclude={"receipt_id"})
    population = dict(payload["population"])
    population["required_configured_max_concurrency"] = 8
    payload["population"] = population

    with pytest.raises(ValidationError, match="applicability codes contradict"):
        InferdromeManagedReceiptV2(
            receipt_id=managed_receipt_id(payload),
            **payload,
        )


def test_portable_receipt_preserves_fail_over_ttft_not_proven_precedence():
    receipt = _receipt()
    payload = receipt.model_dump(mode="python", exclude={"receipt_id"})
    population = dict(payload["population"])
    population.update(
        {
            "successful_count": 90,
            "failed_count": 10,
            "observed_error_rate": "0.1",
        }
    )
    payload["population"] = population
    payload["applicability_codes"] = (
        ManagedApplicabilityCode.SUCCESSFUL_SAMPLE_SHORTFALL,
    )
    payload["acceptance_verdict"] = "FAIL"

    validated = InferdromeManagedReceiptV2(
        receipt_id=managed_receipt_id(payload),
        **payload,
    )

    assert validated.acceptance_verdict == "FAIL"


def _receipt() -> InferdromeManagedReceiptV2:
    payload = {
        "schema_version": INFERDROME_MANAGED_RECEIPT_SCHEMA_VERSION,
        "bundle_digest": PINNED_BUNDLE_DIGEST,
        "contract_hash": "1" * 64,
        "criterion_id": "INFERENCE-PERF-EXT-01",
        "run_id": PINNED_RUN_ID,
        "verifier_version": INFERDROME_MANAGED_VERIFIER_VERSION,
        "calculation_version": INFERDROME_MANAGED_CALCULATION_VERSION,
        "received_at": FIXED_RECEIPT_TIME,
        "ingestion_status": "ACCEPTED",
        "acceptance_verdict": "PASS",
        "applicability_codes": (),
        "evidence_schema_version": "inferdrome.evidence.v1",
        "producer_name": "vllm",
        "producer_version": "0.26.0",
        "adapter_id": "vllm_bench_serve",
        "adapter_version": "1.0.0",
        "native_schema_fingerprint": (
            "sha256:3a4fdee6fe9b45ce5b42c41fd3bfc6614245a36ecfe6f94de92b59717a136abb"
        ),
        "managed_profile_id": MANAGED_PROFILE_ID,
        "managed_profile_sha256": MANAGED_PROFILE_SHA256,
        "local_gpu_proof_schema_id": LOCAL_GPU_PROOF_SCHEMA_ID,
        "local_gpu_proof_schema_sha256": LOCAL_GPU_PROOF_SCHEMA_SHA256,
        "profile_validator_version": "1.0.0",
        "requested_request_plan_digest": REQUEST_PLAN_DIGEST,
        "observed_request_plan_digest": REQUEST_PLAN_DIGEST,
        "requested_workload_digest": WORKLOAD_DIGEST,
        "observed_workload_digest": WORKLOAD_DIGEST,
        "recalculation_sha256": "2" * 64,
        "binding_mode": "EXTERNAL_RECEIPT_BINDING",
        "producer_contract_link": "ABSENT",
        "purpose": "CONFORMANCE_DEMONSTRATION",
        "target": ManagedTargetReceiptV1(
            requested_model="Qwen/Qwen2.5-0.5B-Instruct",
            observed_model="Qwen/Qwen2.5-0.5B-Instruct",
            requested_model_revision=("7ae557604adf67be50417f59c2c2f167def9a775"),
            observed_model_revision=("7ae557604adf67be50417f59c2c2f167def9a775"),
            requested_tokenizer_revision=("7ae557604adf67be50417f59c2c2f167def9a775"),
            observed_tokenizer_revision=("7ae557604adf67be50417f59c2c2f167def9a775"),
            requested_endpoint="http://127.0.0.1:18080/",
            observed_endpoint="http://127.0.0.1:18080/",
        ),
        "metric": ManagedMetricReceiptV1(
            metric="time_to_first_token",
            aggregation="p95",
            unit="nanoseconds",
            operator="lt",
            requested_definition_id="vllm_first_choices_event_v0_26",
            observed_definition_id="vllm_first_choices_event_v0_26",
            requested_reducer_id="nearest_rank_v1",
            observed_reducer_id="nearest_rank_v1",
            requested_population=("successful_measured_requests_with_observed_ttft"),
            observed_population=("successful_measured_requests_with_observed_ttft"),
            threshold_ns=20_000_000,
            recalculated_value_ns=14_797_213,
        ),
        "population": ManagedPopulationReceiptV1(
            attempted_count=100,
            successful_count=100,
            failed_count=0,
            anomalous_count=0,
            required_attempts=100,
            required_successful_samples=100,
            required_configured_max_concurrency=4,
            observed_configured_max_concurrency=4,
            required_warmup_requests=10,
            observed_warmup_requests=10,
            error_numerator=("failed_or_anomalous_native_measured_requests"),
            error_denominator="all_measured_requests",
            error_threshold_basis_points=100,
            observed_error_rate="0",
        ),
        "assurance": ManagedEvidenceAssuranceV1(
            producer_evidence_consistency="VERIFIED",
            hardware_attestation="NOT_AVAILABLE",
            execution_attestation="NOT_AVAILABLE",
            exact_achieved_concurrency="NOT_AVAILABLE",
            transport_retry_behavior="NOT_AVAILABLE",
            temporal_assurance="RETROSPECTIVE",
            contract_preceded_measurement=False,
            production_authorization=False,
        ),
    }
    identity_payload = {
        key: value.model_dump(mode="json")
        if isinstance(
            value,
            (
                ManagedMetricReceiptV1,
                ManagedPopulationReceiptV1,
                ManagedEvidenceAssuranceV1,
                ManagedTargetReceiptV1,
            ),
        )
        else value
        for key, value in payload.items()
    }
    return InferdromeManagedReceiptV2(
        receipt_id=managed_receipt_id(identity_payload),
        **payload,
    )
