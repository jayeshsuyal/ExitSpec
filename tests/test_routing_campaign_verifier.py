import hashlib
import json
import os
from pathlib import Path
import time
from typing import Any

import pytest
from pydantic import ValidationError

from exitspec.canonical import canonical_json_bytes
from exitspec.confirmations import (
    ConfirmationDecision,
    record_confirmation,
    require_affirmative_confirmation,
)
from exitspec.contracts import (
    contract_digest,
    freeze_confirmed_contract,
    freeze_contract,
)
from exitspec.models import (
    ContractStatus,
    RoutingCampaignReductionCriterionV1,
    RoutingQualificationCriterionV1,
    RoutingSLOAttainmentCriterionV1,
)
from exitspec.routing_campaign_verifier import (
    ROUTING_CAMPAIGN_EVIDENCE_BUNDLE_SCHEMA_VERSION,
    ROUTING_CAMPAIGN_EVIDENCE_SCHEMA_VERSION,
    ROUTING_CAMPAIGN_REDUCER_ID,
    ROUTING_CAMPAIGN_REDUCER_VERSION,
    ROUTING_CAMPAIGN_RESULT_SCHEMA_VERSION,
    RoutingCampaignCacheResetEvidenceV1,
    RoutingCampaignEvidenceBundleV1,
    RoutingCampaignIngestionRejected,
    RoutingCampaignProducerV1,
    RoutingCampaignRouteDecisionReceiptV1,
    RoutingCampaignRunEvidenceV1,
    RoutingCampaignServingEvidenceV1,
    RoutingCampaignTelemetryCapsuleV1,
    _sha256_without_field,
    parse_routing_campaign_contract,
    parse_routing_campaign_confirmation,
    parse_routing_campaign_evidence,
    parse_routing_campaign_run_evidence,
    reduce_routing_campaign,
    routing_campaign_contract_digest,
    serialize_routing_campaign_evidence,
    serialize_routing_campaign_confirmation,
    serialize_routing_campaign_reduction_result,
    serialize_routing_campaign_run_evidence,
    validate_routing_campaign_reduction_result,
    validate_routing_campaign_run_evidence,
)
from exitspec.routing_qualification import RoutingQualificationValidationCode
from exitspec.statistics import wilson_lower_bound


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_FIXTURE = PROJECT_ROOT / (
    "examples/routing-qualification/contracts/"
    "routing-campaign-reduction-v1.synthetic.json"
)
EVIDENCE_FIXTURE = PROJECT_ROOT / (
    "examples/routing-qualification/evidence/"
    "routing-campaign-evidence-v1.synthetic.json"
)
CONFIRMATION_FIXTURE = PROJECT_ROOT / (
    "examples/routing-qualification/contracts/"
    "routing-campaign-reduction-v1.synthetic.confirmation.json"
)
EXPECTED_CONTRACT_HASH = (
    "66a6642ab761e8430e0a955e4b43de4779dda12fa08207ad25bb708c858bd260"
)


def _contract():
    return parse_routing_campaign_contract(json.loads(CONTRACT_FIXTURE.read_bytes()))


def _confirmation(contract):
    approved = contract.model_copy(
        update={
            "status": ContractStatus.APPROVED,
            "frozen_at": None,
            "confirmation_id": None,
            "canonical_hash": None,
        }
    )
    return record_confirmation(
        approved,
        confirmer_identity="synthetic-customer",
        decision=ConfirmationDecision.CONFIRM,
        agreement_acknowledged=True,
        rationale="I confirm this exact synthetic routing campaign contract.",
        idempotency_key="routing-campaign-b11-confirmation-v1",
        decided_at=approved.approved_at,
    )


def _reduce(contract, evidence):
    return reduce_routing_campaign(contract, _confirmation(contract), evidence)


def _digest(model: Any, field: str) -> str:
    return _sha256_without_field(model, field)


def _serving():
    return RoutingCampaignServingEvidenceV1(
        schema_version="exitspec.routing-campaign-serving-evidence.v1",
        engine="engine-neutral",
        engine_version="1.0.0",
        model="model-neutral",
        model_revision="revision-123456",
        tokenizer="tokenizer-neutral",
        tokenizer_revision="tokenizer-123456",
        quantization="unquantized",
        tensor_parallel_size=1,
        environment_id="environment-v1",
        environment_sha256="f" * 64,
        target_engine_version="1.0.0",
        target_model_revision="revision-123456",
        target_tokenizer_revision="tokenizer-123456",
        gpu_model="synthetic-gpu",
        gpu_count=1,
        cuda_version="synthetic-cuda",
        driver_version="synthetic-driver",
        execution_environment_id="environment-v1",
    )


def _producer(repetition_index: int):
    return RoutingCampaignProducerV1(
        schema_version="exitspec.routing-campaign-producer.v1",
        producer_id="synthetic-producer-v1",
        producer_version="1.0.0",
        source_digest=format(repetition_index, "x") * 64,
    )


def _telemetry(repetition_index: int, run_id: str):
    producer = _producer(repetition_index)
    telemetry = RoutingCampaignTelemetryCapsuleV1(
        schema_version="exitspec.routing-campaign-telemetry-capsule.v1",
        telemetry_capsule_id=f"synthetic-telemetry-run-{repetition_index}",
        telemetry_capsule_sha256="0" * 64,
        run_id=run_id,
        captured_at="2026-08-30T12:00:00Z",
        producer_id=producer.producer_id,
        producer_version=producer.producer_version,
        source_digest=producer.source_digest,
        environment_id="environment-v1",
    )
    return telemetry.model_copy(
        update={
            "telemetry_capsule_sha256": _digest(telemetry, "telemetry_capsule_sha256")
        }
    )


def _reset(repetition_index: int, trial_index: int, status: str = "RESET_CONFIRMED"):
    producer = _producer(repetition_index)
    reset = RoutingCampaignCacheResetEvidenceV1(
        schema_version="exitspec.routing-campaign-cache-reset-evidence.v1",
        reset_id=f"synthetic-reset-r{repetition_index}-t{trial_index}",
        reset_sha256="0" * 64,
        run_id=f"synthetic-run-{repetition_index}",
        repetition_index=repetition_index,
        trial_index=trial_index,
        status=status,
        reset_scope="ROUTER_AND_SERVING_ENGINE_STATE",
        reset_at="2026-08-30T12:00:00Z",
        producer_id=producer.producer_id,
        producer_version=producer.producer_version,
        source_digest=producer.source_digest,
    )
    return reset.model_copy(update={"reset_sha256": _digest(reset, "reset_sha256")})


def _receipt(
    contract,
    repetition_index: int,
    trial_index: int,
    request_index: int,
    policy_role: str,
    terminal_outcome: str,
    latency_ns: int | None,
):
    producer = _producer(repetition_index)
    campaign = contract.criteria[0]
    if policy_role == "candidate":
        policy_id = campaign.candidate_policy.policy_id
        policy_sha256 = campaign.candidate_policy.policy_sha256
        suffix = "c"
    else:
        policy_id = campaign.baseline_policy.policy_id
        policy_sha256 = campaign.baseline_policy.policy_sha256
        suffix = "b"
    receipt = RoutingCampaignRouteDecisionReceiptV1(
        schema_version="exitspec.routing-campaign-route-decision-receipt.v1",
        route_decision_receipt_id=(
            f"synthetic-receipt-r{repetition_index}-t{trial_index}-"
            f"q{request_index:06d}-{suffix}"
        ),
        route_decision_receipt_sha256="0" * 64,
        campaign_contract_sha256=contract.canonical_hash,
        run_id=f"synthetic-run-{repetition_index}",
        repetition_index=repetition_index,
        request_id=f"request-{request_index:06d}",
        trial_index=trial_index,
        policy_role=policy_role,
        policy_id=policy_id,
        policy_sha256=policy_sha256,
        routing_configuration_id=campaign.routing_configuration.configuration_id,
        routing_configuration_sha256=(
            campaign.routing_configuration.configuration_sha256
        ),
        producer_id=producer.producer_id,
        producer_version=producer.producer_version,
        captured_at="2026-08-30T12:00:00Z",
        source_digest=producer.source_digest,
        terminal_outcome=terminal_outcome,
        latency_ns=latency_ns,
    )
    return receipt.model_copy(
        update={
            "route_decision_receipt_sha256": _digest(
                receipt, "route_decision_receipt_sha256"
            )
        }
    )


def _run(
    contract,
    repetition_index: int,
    *,
    candidate_outcome=None,
    baseline_outcome=None,
    missing: set[tuple[int, int, str]] | None = None,
    reset_status: str = "RESET_CONFIRMED",
    stale: bool = False,
):
    campaign = contract.criteria[0]
    run_id = f"synthetic-run-{repetition_index}"
    producer = _producer(repetition_index)
    captured_at = "2026-08-30T11:00:00Z" if stale else "2026-08-30T12:00:00Z"
    telemetry = _telemetry(repetition_index, run_id)
    if stale:
        telemetry = telemetry.model_copy(update={"captured_at": captured_at})
        telemetry = telemetry.model_copy(
            update={
                "telemetry_capsule_sha256": _digest(
                    telemetry, "telemetry_capsule_sha256"
                )
            }
        )
    if candidate_outcome is None:

        def candidate_outcome(_trial, _request):
            return ("SUCCESS", 1)

    if baseline_outcome is None:

        def baseline_outcome(_trial, _request):
            return ("SUCCESS", 1)

    missing = missing or set()
    assignments = []
    for trial_index in range(campaign.trial_order.trial_count):
        for request_index in range(campaign.trial_order.request_count):
            for role, outcome_fn in (
                ("candidate", candidate_outcome),
                ("baseline", baseline_outcome),
            ):
                if (trial_index, request_index, role) in missing:
                    continue
                outcome, latency = outcome_fn(trial_index, request_index)
                assignments.append(
                    _receipt(
                        contract,
                        repetition_index,
                        trial_index,
                        request_index,
                        role,
                        outcome,
                        latency,
                    )
                )
    return RoutingCampaignRunEvidenceV1(
        schema_version=ROUTING_CAMPAIGN_EVIDENCE_SCHEMA_VERSION,
        protocol_id="routing_campaign_verification_v1",
        evidence_class="SYNTHETIC_FIXTURE",
        contract_sha256=contract.canonical_hash,
        run_id=run_id,
        repetition_index=repetition_index,
        candidate_policy_id=campaign.candidate_policy.policy_id,
        candidate_policy_sha256=campaign.candidate_policy.policy_sha256,
        baseline_policy_id=campaign.baseline_policy.policy_id,
        baseline_policy_sha256=campaign.baseline_policy.policy_sha256,
        routing_configuration_id=campaign.routing_configuration.configuration_id,
        routing_configuration_sha256=campaign.routing_configuration.configuration_sha256,
        request_trace_id=campaign.request_trace.trace_id,
        request_trace_sha256=campaign.request_trace.trace_sha256,
        failure_injection_id=campaign.failure_injection.configuration_id,
        failure_injection_sha256=campaign.failure_injection.configuration_sha256,
        serving=_serving(),
        telemetry=telemetry,
        cache_resets=tuple(
            _reset(repetition_index, trial_index, reset_status)
            for trial_index in range(campaign.trial_order.trial_count)
        ),
        producer=producer,
        observed_at="2026-08-30T12:00:00Z",
        assignments=tuple(assignments),
    )


def _bundle(contract, *runs):
    return RoutingCampaignEvidenceBundleV1(
        schema_version=ROUTING_CAMPAIGN_EVIDENCE_BUNDLE_SCHEMA_VERSION,
        protocol_id="routing_campaign_verification_v1",
        evidence_class="SYNTHETIC_FIXTURE",
        contract_sha256=contract.canonical_hash,
        runs=tuple(runs),
    )


def _expect_rejection(callable_obj, code):
    with pytest.raises(RoutingCampaignIngestionRejected) as caught:
        callable_obj()
    assert caught.value.code is code


def test_b11_contract_fixture_is_new_full_hash_and_preserves_b9_b10_bindings():
    contract = _contract()
    assert contract.canonical_hash == EXPECTED_CONTRACT_HASH
    assert routing_campaign_contract_digest(contract) == EXPECTED_CONTRACT_HASH
    assert [type(item) for item in contract.criteria] == [
        RoutingQualificationCriterionV1,
        RoutingSLOAttainmentCriterionV1,
        RoutingCampaignReductionCriterionV1,
    ]
    campaign, slo, reduction = contract.criteria
    assert reduction.campaign_criterion_id == campaign.id
    assert reduction.slo_criterion_id == slo.id
    assert reduction.required_repetition_indices == (1, 2)
    assert reduction.reduction_policy_id == ROUTING_CAMPAIGN_REDUCER_ID
    assert reduction.reduction_policy_version == ROUTING_CAMPAIGN_REDUCER_VERSION
    assert contract.criteria[0].run_policy.aggregation_policy == "UNDEFINED_IN_B9"
    assert all(
        rule.run_pooling == "INDEPENDENT_B9_RUNS_NOT_POOLED_IN_B10"
        for rule in slo.policy_confidence_rules
    )


def test_b11_confirmation_golden_is_exact_and_binds_confirmed_contract():
    contract = _contract()
    confirmation = parse_routing_campaign_confirmation(
        json.loads(CONFIRMATION_FIXTURE.read_bytes())
    )
    assert serialize_routing_campaign_confirmation(confirmation) == (
        b'{"agreement_acknowledged":true,"confirmation_id":"cnf_e01418bbef5e2a81f63ebcabe5145efe33d5f9a0d8bb3ca9bd8ce861e4d9c3ed","confirmer_identity":"synthetic-customer","contract_fingerprint":"fe966d2459f0d67061f88a0f0942c8a382082aac23992ed0609e496bb065ef81","contract_id":"routing-qualification-campaign","contract_version":"1.0.0","decided_at":"2026-07-22T16:10:00Z","decision":"CONFIRM","idempotency_key":"routing-campaign-b11-confirmation-v1","rationale":"I confirm this exact synthetic routing campaign contract."}'
    )
    assert (
        hashlib.sha256(
            canonical_json_bytes(confirmation.model_dump(mode="json"))
        ).hexdigest()
        == "3a64a55affa7bfc661b311651c55c2120ac8bb9492645c75ccceb3a8e7d8f6d5"
    )
    assert confirmation == _confirmation(contract)
    approved = contract.model_copy(
        update={
            "status": ContractStatus.APPROVED,
            "frozen_at": None,
            "confirmation_id": None,
            "canonical_hash": None,
        }
    )
    assert (
        freeze_confirmed_contract(approved, confirmation, frozen_at=contract.frozen_at)
        == contract
    )
    require_affirmative_confirmation(contract, confirmation)


def test_parsed_canonical_bundle_accepts_two_reset_records_per_run():
    contract = _contract()
    bundle = _bundle(contract, _run(contract, 1), _run(contract, 2))
    parsed = parse_routing_campaign_evidence(
        serialize_routing_campaign_evidence(bundle)
    )
    assert tuple(len(run.cache_resets) for run in parsed.runs) == (2, 2)
    assert _reduce(contract, parsed).campaign_verdict == "PASS"


def test_synthetic_golden_bundle_is_canonicalizable_and_hash_stable():
    payload = json.loads(EVIDENCE_FIXTURE.read_bytes())
    bundle = parse_routing_campaign_evidence(payload)
    canonical = serialize_routing_campaign_evidence(bundle)
    assert parse_routing_campaign_evidence(canonical) == bundle
    assert hashlib.sha256(canonical).hexdigest() == (
        "01bdc0f93b6f9bb40c72be17bae0aab07edba31f456881e3aa2596e863c31f86"
    )


def test_timestamp_age_calculation_is_explicitly_utc(monkeypatch):
    from exitspec.routing_campaign_verifier import _timestamp_seconds

    if not hasattr(time, "tzset"):
        pytest.skip("This platform has no timezone reset primitive.")
    original = os.environ.get("TZ")
    try:
        monkeypatch.setenv("TZ", "Pacific/Honolulu")
        time.tzset()
        honolulu = _timestamp_seconds("2026-08-30T12:00:00Z")
        monkeypatch.setenv("TZ", "Asia/Tokyo")
        time.tzset()
        tokyo = _timestamp_seconds("2026-08-30T12:00:00Z")
    finally:
        if original is None:
            monkeypatch.delenv("TZ", raising=False)
        else:
            monkeypatch.setenv("TZ", original)
        time.tzset()
    assert honolulu == tokyo


def test_latency_integer_boundary_is_admitted_and_above_bound_is_rejected():
    contract = _contract()
    run = _run(contract, 1)
    receipt = run.assignments[0].model_copy(update={"latency_ns": 60_000_000_000})
    receipt = receipt.model_copy(
        update={
            "route_decision_receipt_sha256": _digest(
                receipt, "route_decision_receipt_sha256"
            )
        }
    )
    valid_payload = run.model_dump(mode="json")
    valid_payload["assignments"][0] = receipt.model_dump(mode="json")
    parsed = parse_routing_campaign_run_evidence(valid_payload)
    assert parsed.assignments[0].latency_ns == 60_000_000_000

    oversized_payload = run.model_dump(mode="json")
    oversized_payload["assignments"][0]["latency_ns"] = 60_000_000_001
    _expect_rejection(
        lambda: parse_routing_campaign_run_evidence(oversized_payload),
        RoutingQualificationValidationCode.INVALID_BOUND,
    )


def test_reducer_dispatches_standalone_canonical_run_bytes_and_rejects_ambiguous_schema():
    contract = _contract()
    run = _run(contract, 1)
    encoded = serialize_routing_campaign_run_evidence(run)
    result_from_bytes = _reduce(contract, encoded)
    result_from_mapping = _reduce(contract, json.loads(encoded))
    assert result_from_bytes == result_from_mapping
    assert result_from_bytes.missing_repetition_indices == (2,)

    ambiguous = json.loads(encoded)
    del ambiguous["schema_version"]
    _expect_rejection(
        lambda: _reduce(contract, ambiguous),
        RoutingQualificationValidationCode.WRONG_VERSION,
    )
    wrong_top_level = json.loads(encoded)
    wrong_top_level["schema_version"] = ROUTING_CAMPAIGN_EVIDENCE_BUNDLE_SCHEMA_VERSION
    _expect_rejection(
        lambda: _reduce(contract, wrong_top_level),
        RoutingQualificationValidationCode.EXTRA_FIELD,
    )


def test_cache_resets_cover_unique_trials_and_precede_trial_receipts():
    contract = _contract()
    confirmation = _confirmation(contract)
    run = _run(contract, 1)
    duplicate = run.cache_resets[0].model_copy(
        update={"reset_id": "synthetic-reset-r1-t0-duplicate"}
    )
    duplicate = duplicate.model_copy(
        update={"reset_sha256": _digest(duplicate, "reset_sha256")}
    )
    duplicate_coordinates = run.model_copy(
        update={"cache_resets": (run.cache_resets[0], duplicate)}
    )
    _expect_rejection(
        lambda: validate_routing_campaign_run_evidence(
            contract, confirmation, duplicate_coordinates
        ),
        RoutingQualificationValidationCode.SEMANTIC_INCONSISTENCY,
    )

    late_reset = run.cache_resets[0].model_copy(
        update={"reset_at": "2026-08-30T12:00:01Z"}
    )
    late_reset = late_reset.model_copy(
        update={"reset_sha256": _digest(late_reset, "reset_sha256")}
    )
    late_receipt_order = run.model_copy(
        update={
            "observed_at": "2026-08-30T12:00:02Z",
            "cache_resets": (late_reset, run.cache_resets[1]),
        }
    )
    _expect_rejection(
        lambda: validate_routing_campaign_run_evidence(
            contract, confirmation, late_receipt_order
        ),
        RoutingQualificationValidationCode.SEMANTIC_INCONSISTENCY,
    )


def test_b11_contract_rejects_population_larger_than_its_parser_capacity():
    contract = _contract()
    campaign = contract.criteria[0]
    oversized_trial_order = campaign.trial_order.model_copy(
        update={"trial_count": 6, "total_assignments": 1200}
    )
    oversized_campaign = campaign.model_copy(
        update={"trial_order": oversized_trial_order}
    )
    oversized_payload = contract.model_dump(mode="python")
    oversized_payload["criteria"] = [
        oversized_campaign.model_dump(mode="python"),
        *oversized_payload["criteria"][1:],
    ]
    with pytest.raises(
        ValidationError,
        match="B11 campaign population exceeds the bounded evidence parser capacity",
    ):
        contract.__class__.model_validate(oversized_payload)


def test_unconfirmed_contract_variants_cannot_reach_reduction():
    contract = _contract()
    confirmation = _confirmation(contract)
    evidence = _bundle(contract, _run(contract, 1))

    approved = contract.model_copy(
        update={
            "status": ContractStatus.APPROVED,
            "frozen_at": None,
            "confirmation_id": None,
            "canonical_hash": None,
        }
    )
    legacy_frozen = freeze_contract(approved, frozen_at=contract.frozen_at)
    _expect_rejection(
        lambda: reduce_routing_campaign(legacy_frozen, confirmation, evidence),
        RoutingQualificationValidationCode.CONTRACT_BINDING_MISMATCH,
    )

    copied = contract.model_copy(
        update={"confirmation_id": None, "canonical_hash": None}
    )
    copied = copied.model_copy(update={"canonical_hash": contract_digest(copied)})
    _expect_rejection(
        lambda: reduce_routing_campaign(copied, confirmation, evidence),
        RoutingQualificationValidationCode.CONTRACT_BINDING_MISMATCH,
    )

    constructed = contract.__class__.model_construct(**copied.model_dump(mode="python"))
    _expect_rejection(
        lambda: reduce_routing_campaign(constructed, confirmation, evidence),
        RoutingQualificationValidationCode.WRONG_TYPE,
    )
    _expect_rejection(
        lambda: reduce_routing_campaign(
            copied.model_dump(mode="json"), confirmation, evidence
        ),
        RoutingQualificationValidationCode.WRONG_TYPE,
    )
    _expect_rejection(
        lambda: reduce_routing_campaign(
            contract,
            confirmation.model_copy(
                update={"decision": ConfirmationDecision.REQUEST_CHANGES}
            ),
            evidence,
        ),
        RoutingQualificationValidationCode.CONTRACT_BINDING_MISMATCH,
    )
    _expect_rejection(
        lambda: reduce_routing_campaign(
            contract, confirmation.model_dump(mode="json"), evidence
        ),
        RoutingQualificationValidationCode.WRONG_TYPE,
    )


def test_complete_required_runs_pass_without_pooling_populations():
    contract = _contract()
    result = _reduce(contract, _bundle(contract, _run(contract, 1), _run(contract, 2)))
    assert result.campaign_verdict == "PASS"
    assert result.missing_repetition_indices == ()
    assert tuple(run.repetition_index for run in result.run_results) == (1, 2)
    for run in result.run_results:
        candidate, baseline = run.policy_results
        assert candidate.eligible_assignment_count == 200
        assert candidate.attained_count == 200
        assert candidate.not_attained_count == 0
        assert candidate.not_proven_count == 0
        assert candidate.verdict == "PASS"
        assert baseline.evaluation_role == "REFERENCE_CONTROL"
        assert baseline.eligible_assignment_count == 200


def test_complete_genuine_candidate_failure_fails_campaign():
    contract = _contract()

    def slow(_trial, request):
        return ("SUCCESS", 1 if _trial != 0 or request >= 10 else 250_000_001)

    result = _reduce(
        contract,
        _bundle(contract, _run(contract, 1, candidate_outcome=slow), _run(contract, 2)),
    )
    assert result.campaign_verdict == "FAIL"
    assert result.run_results[0].policy_results[0].attained_count == 190
    assert result.run_results[0].policy_results[0].not_attained_count == 10
    assert result.run_results[0].policy_results[0].verdict == "FAIL"


def test_favorable_197_of_200_is_wilson_inconclusive_not_fail():
    contract = _contract()

    def favorable_point_estimate(trial, request):
        return (
            "SUCCESS",
            250_000_001
            if (trial == 0 and request < 2) or (trial == 1 and request == 0)
            else 1,
        )

    result = _reduce(
        contract,
        _bundle(
            contract,
            _run(contract, 1, candidate_outcome=favorable_point_estimate),
            _run(contract, 2),
        ),
    )
    candidate = result.run_results[0].policy_results[0]
    assert candidate.attained_count == 197
    assert candidate.point_estimate == "0.985"
    assert candidate.wilson_lower_bound == "0.956834271207"
    assert wilson_lower_bound(196, 200) == pytest.approx(0.9497128709)
    assert candidate.verdict == "NOT_PROVEN"
    assert "WILSON_CONFIDENCE_INSUFFICIENT" in candidate.evidence_issues
    assert result.campaign_verdict == "NOT_PROVEN"


def test_external_errors_and_timeouts_remain_in_denominator_and_can_fail():
    contract = _contract()

    def external(_trial, request):
        if _trial == 0 and request < 5:
            return ("EXTERNAL_ERROR", None)
        if _trial == 0 and request < 10:
            return ("TIMEOUT", None)
        return ("SUCCESS", 1)

    result = _reduce(
        contract,
        _bundle(
            contract,
            _run(contract, 1, candidate_outcome=external),
            _run(contract, 2, candidate_outcome=external),
        ),
    )
    candidate = result.run_results[0].policy_results[0]
    assert candidate.eligible_assignment_count == 200
    assert candidate.attained_count == 190
    assert candidate.not_attained_count == 10
    assert candidate.not_proven_count == 0
    assert candidate.verdict == "FAIL"
    assert result.campaign_verdict == "FAIL"


@pytest.mark.parametrize("outcome", ["MISSING", "INVALID", "INTERNAL", "CANCELLED"])
def test_missing_invalid_internal_and_cancellation_are_not_proven_and_counted(outcome):
    contract = _contract()

    def not_proven(_trial, request):
        return (outcome, None) if request == 0 else ("SUCCESS", 1)

    result = _reduce(
        contract,
        _bundle(
            contract, _run(contract, 1, candidate_outcome=not_proven), _run(contract, 2)
        ),
    )
    candidate = result.run_results[0].policy_results[0]
    assert candidate.eligible_assignment_count == 200
    assert candidate.not_proven_count == 2
    assert candidate.verdict == "NOT_PROVEN"
    assert result.campaign_verdict == "NOT_PROVEN"


def test_missing_assignment_is_admitted_but_can_never_pass():
    contract = _contract()
    result = _reduce(
        contract,
        _bundle(
            contract,
            _run(contract, 1, missing={(0, 0, "candidate")}),
            _run(contract, 2),
        ),
    )
    candidate = result.run_results[0].policy_results[0]
    assert candidate.not_proven_count == 1
    assert "MISSING_ASSIGNMENT" in candidate.evidence_issues
    assert result.campaign_verdict == "NOT_PROVEN"


def test_stale_telemetry_and_cache_reset_failure_are_not_proven():
    contract = _contract()
    stale = _reduce(
        contract, _bundle(contract, _run(contract, 1, stale=True), _run(contract, 2))
    )
    assert stale.campaign_verdict == "NOT_PROVEN"
    assert "STALE_TELEMETRY" in stale.run_results[0].evidence_issues

    reset_failed = _reduce(
        contract,
        _bundle(
            contract,
            _run(contract, 1, reset_status="RESET_FAILED"),
            _run(contract, 2),
        ),
    )
    assert reset_failed.campaign_verdict == "NOT_PROVEN"
    assert "CACHE_RESET_NOT_CONFIRMED" in reset_failed.run_results[0].evidence_issues


def test_baseline_is_contextual_and_does_not_control_candidate_pass():
    contract = _contract()

    def slow_baseline(_trial, request):
        return ("SUCCESS", 1 if request < 10 else 250_000_001)

    result = _reduce(
        contract,
        _bundle(
            contract,
            _run(contract, 1, baseline_outcome=slow_baseline),
            _run(contract, 2, baseline_outcome=slow_baseline),
        ),
    )
    assert result.campaign_verdict == "PASS"
    assert all(run.policy_results[0].verdict == "PASS" for run in result.run_results)
    assert all(run.policy_results[1].verdict == "FAIL" for run in result.run_results)

    def baseline_inconclusive(_trial, request):
        return (
            "SUCCESS",
            250_000_001
            if (_trial == 0 and request < 2) or (_trial == 1 and request == 0)
            else 1,
        )

    inconclusive = _reduce(
        contract,
        _bundle(
            contract,
            _run(contract, 1, baseline_outcome=baseline_inconclusive),
            _run(contract, 2, baseline_outcome=baseline_inconclusive),
        ),
    )
    assert inconclusive.campaign_verdict == "PASS"
    assert all(
        run.policy_results[1].verdict == "NOT_PROVEN"
        for run in inconclusive.run_results
    )
    assert all(
        "WILSON_CONFIDENCE_INSUFFICIENT" in run.policy_results[1].evidence_issues
        for run in inconclusive.run_results
    )


def test_missing_required_run_is_not_proven_and_extra_or_duplicate_run_is_rejected():
    contract = _contract()
    missing = _reduce(contract, _bundle(contract, _run(contract, 1)))
    assert missing.campaign_verdict == "NOT_PROVEN"
    assert missing.missing_repetition_indices == (2,)

    _expect_rejection(
        lambda: _reduce(
            contract, _bundle(contract, _run(contract, 1), _run(contract, 3))
        ),
        RoutingQualificationValidationCode.CONTRACT_BINDING_MISMATCH,
    )
    _expect_rejection(
        lambda: _reduce(contract, [_run(contract, 1), _run(contract, 1)]),
        RoutingQualificationValidationCode.SEMANTIC_INCONSISTENCY,
    )


def test_sequence_input_cannot_mix_synthetic_and_external_evidence_classes():
    contract = _contract()
    external = _run(contract, 2).model_copy(
        update={"evidence_class": "EXTERNAL_SEALED_EVIDENCE"}
    )
    _expect_rejection(
        lambda: _reduce(contract, [_run(contract, 1), external]),
        RoutingQualificationValidationCode.SEMANTIC_INCONSISTENCY,
    )


def test_reordered_runs_and_records_are_rejected_as_noncanonical():
    contract = _contract()
    _expect_rejection(
        lambda: _reduce(contract, [_run(contract, 2), _run(contract, 1)]),
        RoutingQualificationValidationCode.NON_CANONICAL,
    )
    run = _run(contract, 1)
    reordered = run.model_copy(update={"assignments": tuple(reversed(run.assignments))})
    _expect_rejection(
        lambda: parse_routing_campaign_run_evidence(reordered.model_dump(mode="json")),
        RoutingQualificationValidationCode.SEMANTIC_INCONSISTENCY,
    )


def test_duplicate_assignment_and_cross_run_identity_reuse_are_rejected():
    contract = _contract()
    run = _run(contract, 1)
    duplicate = run.model_copy(
        update={
            "assignments": (
                *run.assignments,
                run.assignments[-1].model_copy(
                    update={"route_decision_receipt_id": "synthetic-receipt-extra"}
                ),
            )
        }
    )
    _expect_rejection(
        lambda: parse_routing_campaign_run_evidence(duplicate.model_dump(mode="json")),
        RoutingQualificationValidationCode.SEMANTIC_INCONSISTENCY,
    )
    reused_telemetry = _run(contract, 2)
    telemetry = _run(contract, 1).telemetry.model_copy(
        update={
            "run_id": reused_telemetry.run_id,
            "source_digest": reused_telemetry.producer.source_digest,
        }
    )
    reused_telemetry = reused_telemetry.model_copy(
        update={
            "telemetry": telemetry.model_copy(
                update={
                    "telemetry_capsule_sha256": _digest(
                        telemetry, "telemetry_capsule_sha256"
                    )
                }
            )
        }
    )
    with pytest.raises(
        ValidationError, match="Telemetry identities must not be reused"
    ):
        RoutingCampaignEvidenceBundleV1(
            schema_version=ROUTING_CAMPAIGN_EVIDENCE_BUNDLE_SCHEMA_VERSION,
            protocol_id="routing_campaign_verification_v1",
            evidence_class="SYNTHETIC_FIXTURE",
            contract_sha256=contract.canonical_hash,
            runs=(run, reused_telemetry),
        )


def test_extra_assignment_and_digest_tampering_are_ingestion_rejected():
    contract = _contract()
    run = _run(contract, 1)
    extra = run.assignments[-1].model_copy(
        update={
            "request_id": "request-000100",
            "route_decision_receipt_id": "synthetic-receipt-extra",
        }
    )
    extra = extra.model_copy(
        update={
            "route_decision_receipt_sha256": _digest(
                extra, "route_decision_receipt_sha256"
            )
        }
    )
    extra_run = run.model_copy(update={"assignments": (*run.assignments, extra)})
    _expect_rejection(
        lambda: validate_routing_campaign_run_evidence(
            contract, _confirmation(contract), extra_run
        ),
        RoutingQualificationValidationCode.EXTRA_FIELD,
    )
    bad_telemetry = run.model_copy(
        update={
            "telemetry": run.telemetry.model_copy(
                update={"telemetry_capsule_sha256": "0" * 64}
            )
        }
    )
    _expect_rejection(
        lambda: validate_routing_campaign_run_evidence(
            contract, _confirmation(contract), bad_telemetry
        ),
        RoutingQualificationValidationCode.INVALID_DIGEST,
    )
    bad_receipt = run.model_copy(
        update={
            "assignments": (
                run.assignments[0].model_copy(
                    update={"route_decision_receipt_sha256": "0" * 64}
                ),
                *run.assignments[1:],
            )
        }
    )
    _expect_rejection(
        lambda: validate_routing_campaign_run_evidence(
            contract, _confirmation(contract), bad_receipt
        ),
        RoutingQualificationValidationCode.INVALID_DIGEST,
    )


def test_producer_verdict_aliases_and_wrong_wire_shapes_fail_closed():
    contract = _contract()
    payload = _run(contract, 1).model_dump(mode="json")
    payload["telemetry"]["nested"] = {"acceptance_verdict": "PASS"}
    _expect_rejection(
        lambda: parse_routing_campaign_run_evidence(payload),
        RoutingQualificationValidationCode.PRODUCER_VERDICT_FORBIDDEN,
    )
    payload = _run(contract, 1).model_dump(mode="json")
    payload["assignments"][0]["attained"] = True
    _expect_rejection(
        lambda: parse_routing_campaign_run_evidence(payload),
        RoutingQualificationValidationCode.EXTRA_FIELD,
    )
    payload = _run(contract, 1).model_dump(mode="json")
    payload["schema_version"] = "exitspec.routing-campaign-evidence.v2"
    _expect_rejection(
        lambda: parse_routing_campaign_run_evidence(payload),
        RoutingQualificationValidationCode.WRONG_VERSION,
    )
    duplicate = b'{"run_id":"a","run_id":"b"}'
    _expect_rejection(
        lambda: parse_routing_campaign_run_evidence(duplicate),
        RoutingQualificationValidationCode.DUPLICATE_FIELD,
    )


def test_request_and_contract_bindings_are_exact():
    contract = _contract()
    run = _run(contract, 1)
    bad_request = run.model_copy(update={"request_trace_id": "other-request-trace-v1"})
    _expect_rejection(
        lambda: validate_routing_campaign_run_evidence(
            contract, _confirmation(contract), bad_request
        ),
        RoutingQualificationValidationCode.CONTRACT_BINDING_MISMATCH,
    )
    bad_contract = run.model_copy(
        update={"assignments": (), "contract_sha256": "0" * 64}
    )
    _expect_rejection(
        lambda: validate_routing_campaign_run_evidence(
            contract, _confirmation(contract), bad_contract
        ),
        RoutingQualificationValidationCode.CONTRACT_BINDING_MISMATCH,
    )


def test_model_copy_bypass_cannot_reach_digest_or_reduction():
    contract = _contract()
    run = _run(contract, 1)
    malformed = run.model_copy(update={"assignments": None})
    _expect_rejection(
        lambda: serialize_routing_campaign_run_evidence(malformed),
        RoutingQualificationValidationCode.WRONG_TYPE,
    )
    malformed_result = _reduce(contract, _bundle(contract, run, _run(contract, 2)))
    tampered_verdict = malformed_result.model_copy(update={"campaign_verdict": "FAIL"})
    from exitspec.routing_campaign_verifier import (
        validate_routing_campaign_reduction_result,
    )

    _expect_rejection(
        lambda: validate_routing_campaign_reduction_result(
            contract,
            _confirmation(contract),
            _bundle(contract, run, _run(contract, 2)),
            tampered_verdict,
        ),
        RoutingQualificationValidationCode.SEMANTIC_INCONSISTENCY,
    )
    policy = malformed_result.run_results[0].policy_results[0]
    tampered_stats = policy.model_copy(update={"point_estimate": "0.99"})
    tampered_run = malformed_result.run_results[0].model_copy(
        update={
            "policy_results": (
                tampered_stats,
                malformed_result.run_results[0].policy_results[1],
            )
        }
    )
    tampered_result = malformed_result.model_copy(
        update={"run_results": (tampered_run, *malformed_result.run_results[1:])}
    )
    _expect_rejection(
        lambda: serialize_routing_campaign_reduction_result(
            contract,
            _confirmation(contract),
            _bundle(contract, run, _run(contract, 2)),
            tampered_result,
        ),
        RoutingQualificationValidationCode.SEMANTIC_INCONSISTENCY,
    )


def test_context_bound_result_rejects_contract_or_evidence_binding_tampering():
    contract = _contract()
    confirmation = _confirmation(contract)
    evidence = _bundle(contract, _run(contract, 1), _run(contract, 2))
    result = _reduce(contract, evidence)

    original_policy = result.run_results[0].policy_results[0]
    changed_rate = original_policy.model_copy(
        update={"required_attainment_rate": "0.98"}
    )
    changed_rate_run = result.run_results[0].model_copy(
        update={
            "policy_results": (
                changed_rate,
                result.run_results[0].policy_results[1],
            )
        }
    )
    _expect_rejection(
        lambda: validate_routing_campaign_reduction_result(
            contract,
            confirmation,
            evidence,
            result.model_copy(
                update={"run_results": (changed_rate_run, *result.run_results[1:])}
            ),
        ),
        RoutingQualificationValidationCode.SEMANTIC_INCONSISTENCY,
    )

    changed_policy = original_policy.model_copy(
        update={"subject_policy_id": "tampered-policy-v1"}
    )
    changed_policy_run = result.run_results[0].model_copy(
        update={
            "policy_results": (
                changed_policy,
                result.run_results[0].policy_results[1],
            )
        }
    )
    _expect_rejection(
        lambda: validate_routing_campaign_reduction_result(
            contract,
            confirmation,
            evidence,
            result.model_copy(
                update={"run_results": (changed_policy_run, *result.run_results[1:])}
            ),
        ),
        RoutingQualificationValidationCode.SEMANTIC_INCONSISTENCY,
    )

    changed_run = result.run_results[0].model_copy(update={"run_id": "tampered-run-v1"})
    _expect_rejection(
        lambda: validate_routing_campaign_reduction_result(
            contract,
            confirmation,
            evidence,
            result.model_copy(
                update={"run_results": (changed_run, *result.run_results[1:])}
            ),
        ),
        RoutingQualificationValidationCode.SEMANTIC_INCONSISTENCY,
    )

    unknown_issue = original_policy.model_copy(
        update={"evidence_issues": ("UNKNOWN_ISSUE",)}
    )
    unknown_issue_run = result.run_results[0].model_copy(
        update={
            "policy_results": (
                unknown_issue,
                result.run_results[0].policy_results[1],
            )
        }
    )
    _expect_rejection(
        lambda: validate_routing_campaign_reduction_result(
            contract,
            confirmation,
            evidence,
            result.model_copy(
                update={"run_results": (unknown_issue_run, *result.run_results[1:])}
            ),
        ),
        RoutingQualificationValidationCode.SEMANTIC_INCONSISTENCY,
    )


def test_result_constant_versions_are_exact_literals():
    contract = _contract()
    confirmation = _confirmation(contract)
    evidence = _bundle(contract, _run(contract, 1), _run(contract, 2))
    result = _reduce(contract, evidence)
    _expect_rejection(
        lambda: validate_routing_campaign_reduction_result(
            contract,
            confirmation,
            evidence,
            result.model_copy(
                update={
                    "schema_version": "exitspecXrouting-campaign-reduction-resultXv1"
                }
            ),
        ),
        RoutingQualificationValidationCode.WRONG_VERSION,
    )
    _expect_rejection(
        lambda: validate_routing_campaign_reduction_result(
            contract,
            confirmation,
            evidence,
            result.model_copy(update={"reducer_version": "1x0x0"}),
        ),
        RoutingQualificationValidationCode.WRONG_VERSION,
    )


def test_reduction_result_is_immutable_and_canonical():
    contract = _contract()
    result = _reduce(contract, _bundle(contract, _run(contract, 1), _run(contract, 2)))
    evidence = _bundle(contract, _run(contract, 1), _run(contract, 2))
    encoded = serialize_routing_campaign_reduction_result(
        contract, _confirmation(contract), evidence, result
    )
    assert encoded == canonical_json_bytes(result.model_dump(mode="json"))
    assert encoded.startswith(b'{"campaign_verdict":"PASS"')
    assert result.reducer_id == ROUTING_CAMPAIGN_REDUCER_ID
    assert result.reducer_version == ROUTING_CAMPAIGN_REDUCER_VERSION
    assert result.schema_version == ROUTING_CAMPAIGN_RESULT_SCHEMA_VERSION
