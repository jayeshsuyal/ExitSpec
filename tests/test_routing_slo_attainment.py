from copy import deepcopy
import json
from pathlib import Path
from typing import Any

import pytest

from exitspec.canonical import canonical_json_bytes
from exitspec.contracts import contract_digest
from exitspec.models import (
    ContractStatus,
    POCContract,
    RoutingQualificationCriterionV1,
    RoutingSLOAttainmentCriterionV1,
)
from exitspec.routing_qualification import RoutingQualificationValidationCode
from exitspec.routing_slo_attainment import (
    ROUTING_SLO_ATTAINMENT_PROTOCOL_ID,
    RoutingSLOAttainmentRejected,
    parse_routing_slo_attainment_contract,
    routing_slo_attainment_contract_digest,
    serialize_routing_slo_attainment_contract,
    validate_routing_slo_attainment_contract,
)
from exitspec.statistics import wilson_lower_bound


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = PROJECT_ROOT / (
    "examples/routing-qualification/contracts/routing-slo-attainment-v1.synthetic.json"
)
EXPECTED_HASH = "0c2158f64312d95a205a6f9c4409cabef12b86ee2a47357164a2f5982c04ad71"


def _payload() -> dict[str, Any]:
    return json.loads(FIXTURE.read_bytes())


def _contract() -> POCContract:
    return parse_routing_slo_attainment_contract(_payload())


def _rehashed(payload: dict[str, Any]) -> POCContract:
    candidate = deepcopy(payload)
    candidate["canonical_hash"] = None
    without_hash = POCContract.model_validate(candidate)
    candidate["canonical_hash"] = contract_digest(without_hash)
    return POCContract.model_validate(candidate)


def _expect_rejection(callable_obj: Any, code: RoutingQualificationValidationCode):
    with pytest.raises(RoutingSLOAttainmentRejected) as caught:
        callable_obj()
    assert caught.value.code is code


def test_synthetic_full_contract_has_exact_outer_hash_and_no_b10_digest():
    contract = _contract()
    assert contract.status is ContractStatus.FROZEN
    assert contract.canonical_hash == EXPECTED_HASH
    assert routing_slo_attainment_contract_digest(contract) == EXPECTED_HASH
    assert [type(criterion) for criterion in contract.criteria] == [
        RoutingQualificationCriterionV1,
        RoutingSLOAttainmentCriterionV1,
    ]
    assert contract.criteria[1].id == ROUTING_SLO_ATTAINMENT_PROTOCOL_ID
    with pytest.raises(RoutingSLOAttainmentRejected):
        routing_slo_attainment_contract_digest(contract.criteria[1])


def test_standalone_b9_fixture_hash_and_assignment_math_are_unchanged():
    from exitspec.routing_qualification import parse_routing_qualification_contract

    b9_path = PROJECT_ROOT / (
        "examples/routing-qualification/contracts/routing-qualification-v1.json"
    )
    b9 = parse_routing_qualification_contract(json.loads(b9_path.read_bytes()))
    assert b9.canonical_hash == (
        "e3bbcab57ac37987f981e0de1a36e56ae6f649cd2f3c75d8d7bcd637583a0516"
    )
    assert b9.criteria[0].trial_order.total_assignments == 16


def test_full_contract_canonical_round_trip_and_binding_is_complete():
    contract = _contract()
    canonical = serialize_routing_slo_attainment_contract(contract)
    assert canonical == canonical_json_bytes(contract.model_dump(mode="json"))
    assert parse_routing_slo_attainment_contract(canonical) == contract
    assert validate_routing_slo_attainment_contract(contract) == contract

    b9 = contract.criteria[0]
    slo = contract.criteria[1]
    assert slo.campaign_criterion_id == b9.id
    assert slo.candidate_policy_id == b9.candidate_policy.policy_id
    assert slo.baseline_policy_id == b9.baseline_policy.policy_id
    assert tuple(x.subject_policy_role for x in slo.assignment_slo_envelopes) == (
        "candidate",
        "baseline",
    )
    assert tuple(x.subject_policy_role for x in slo.policy_confidence_rules) == (
        "candidate",
        "baseline",
    )


def test_underlying_metric_is_exact_and_inclusive_at_the_threshold():
    slo = _contract().criteria[1]
    for envelope in slo.assignment_slo_envelopes:
        observation = envelope.required_observations[0]
        assert observation.metric_definition_id == (
            "routing_terminal_end_to_end_latency_ns"
        )
        assert observation.metric_definition_version == "1.0.0"
        assert observation.unit == "nanoseconds"
        assert observation.comparison_operator == "lte"
        assert observation.boundary_semantics == (
            "ATTAINED_WHEN_OBSERVED_LATENCY_NS_LE_THRESHOLD_NS"
        )
        assert observation.clock_domain == "MONOTONIC_PER_ASSIGNMENT_CLOCK"
        assert observation.start_event == "ASSIGNMENT_DISPATCH_MONOTONIC_START"
        assert observation.terminal_event == (
            "FINAL_RESPONSE_OR_EXTERNAL_TERMINAL_OUTCOME_MONOTONIC_STOP"
        )
        assert observation.threshold_ns == observation.threshold_ns
        assert observation.threshold_ns + 1 > observation.threshold_ns


def test_assignment_outcome_is_exit_spec_derived_and_external_failures_stay_counted():
    slo = _contract().criteria[1]
    for envelope in slo.assignment_slo_envelopes:
        assert envelope.outcome_derivation == (
            "EXIT_SPEC_DERIVES_ATTAINED_IF_ALL_REQUIRED_OBSERVATIONS_SATISFY"
        )
        assert envelope.producer_outcome_authority == "FORBIDDEN_EXIT_SPEC_ONLY"
        assert envelope.external_error_treatment == "COUNT_AS_NOT_ATTAINED"
        assert envelope.timeout_treatment == "COUNT_AS_NOT_ATTAINED"
        assert envelope.missing_evidence_disposition == "NOT_PROVEN"
        assert envelope.invalid_evidence_disposition == "NOT_PROVEN"
        assert envelope.internal_evidence_disposition == "NOT_PROVEN"
        observation = envelope.required_observations[0]
        assert observation.successful_case.startswith("VALID_")
        assert (
            observation.external_error_case == "NOT_ATTAINED_AND_REMAINS_IN_DENOMINATOR"
        )
        assert observation.timeout_case == "NOT_ATTAINED_AND_REMAINS_IN_DENOMINATOR"
        assert observation.missing_case == "NOT_PROVEN_AND_REMAINS_IN_DENOMINATOR"
        assert observation.invalid_case == "NOT_PROVEN_AND_REMAINS_IN_DENOMINATOR"
        assert observation.internal_case == "NOT_PROVEN_AND_REMAINS_IN_DENOMINATOR"


def test_confidence_rules_are_subject_specific_and_roles_are_explicit():
    slo = _contract().criteria[1]
    assert slo.policy_evaluation_roles == ("QUALIFICATION_GATE", "REFERENCE_CONTROL")
    assert slo.policy_requirement_combination == (
        "QUALIFICATION_GATE_REQUIRED_REFERENCE_CONTROL_CONTEXTUAL"
    )
    assert slo.policy_requirement_rationale == (
        "CANDIDATE_IS_CUSTOMER_QUALIFICATION_TARGET_BASELINE_IS_REFERENCE_CONTROL"
    )
    for rule, role in zip(
        slo.policy_confidence_rules, ("QUALIFICATION_GATE", "REFERENCE_CONTROL")
    ):
        assert rule.evaluation_role == role
        assert rule.eligible_population == (
            "ALL_B9_REQUEST_TRIAL_ASSIGNMENTS_FOR_THIS_SUBJECT_POLICY"
        )
        assert rule.denominator == (
            "ALL_ELIGIBLE_ASSIGNMENTS_FOR_THIS_SUBJECT_POLICY_INCLUDING_EXTERNAL_ERRORS_AND_TIMEOUTS"
        )
        assert (
            rule.population_subject_binding == "THIS_RULE_SUBJECT_POLICY_ID_AND_SHA256"
        )
        assert rule.run_pooling == "INDEPENDENT_B9_RUNS_NOT_POOLED_IN_B10"


def test_corrected_golden_has_200_assignments_per_subject_and_400_total():
    b9 = _contract().criteria[0]
    assert b9.trial_order.trial_count == 2
    assert b9.trial_order.request_count == 100
    assert b9.trial_order.trial_count * b9.trial_order.request_count == 200
    assert b9.trial_order.total_assignments == 400
    assert all(
        rule.confidence.minimum_sample_count == 200
        for rule in _contract().criteria[1].policy_confidence_rules
    )


def test_impossible_sample_plan_is_rejected_against_each_subject_population():
    payload = _payload()
    for rule in payload["criteria"][1]["policy_confidence_rules"]:
        rule["confidence"]["minimum_sample_count"] = 201
    impossible = _rehashed(payload)
    _expect_rejection(
        lambda: validate_routing_slo_attainment_contract(impossible),
        RoutingQualificationValidationCode.INVALID_BOUND,
    )


def test_duplicate_underlying_observation_is_rejected_in_v1():
    payload = _payload()
    observations = payload["criteria"][1]["assignment_slo_envelopes"][0][
        "required_observations"
    ]
    observations.append(deepcopy(observations[0]))
    _expect_rejection(
        lambda: parse_routing_slo_attainment_contract(payload),
        RoutingQualificationValidationCode.OVERSIZED,
    )


def test_b10_does_not_add_observed_run_or_producer_outcome_fields():
    criterion = _contract().criteria[1]
    assert "run_id" not in RoutingSLOAttainmentCriterionV1.model_fields
    assert "measurement" not in RoutingSLOAttainmentCriterionV1.model_fields
    assert "verdict" not in RoutingSLOAttainmentCriterionV1.model_fields
    assert "producer_verdict" not in RoutingSLOAttainmentCriterionV1.model_fields
    assert "attained" not in RoutingSLOAttainmentCriterionV1.model_fields
    assert criterion.verdict_boundary == "NO_VERDICT_IN_B10"


def test_b10_outer_hash_changes_for_valid_metric_and_confidence_changes():
    original = _contract().canonical_hash
    for path, value in (
        (
            ("assignment_slo_envelopes", 0, "required_observations", 0, "threshold_ns"),
            250_000_001,
        ),
        (("policy_confidence_rules", 0, "confidence", "minimum_sample_count"), 201),
        (
            ("policy_confidence_rules", 0, "confidence", "required_attainment_rate"),
            "0.98",
        ),
    ):
        mutated = _payload()
        target: Any = mutated["criteria"][1]
        for component in path[:-1]:
            target = target[component]
        target[path[-1]] = value
        assert _rehashed(mutated).canonical_hash != original


def test_cross_binding_rejects_rebound_slo_policy():
    payload = _payload()
    slo = payload["criteria"][1]
    slo["candidate_policy_id"] = "other-candidate-v1"
    slo["candidate_policy_sha256"] = "9" * 64
    envelope = slo["assignment_slo_envelopes"][0]
    envelope["subject_policy_id"] = "other-candidate-v1"
    envelope["subject_policy_sha256"] = "9" * 64
    confidence = slo["policy_confidence_rules"][0]
    confidence["subject_policy_id"] = "other-candidate-v1"
    confidence["subject_policy_sha256"] = "9" * 64
    _expect_rejection(
        lambda: parse_routing_slo_attainment_contract(payload),
        RoutingQualificationValidationCode.SEMANTIC_INCONSISTENCY,
    )


def test_orphaned_or_reordered_b10_criterion_is_rejected():
    orphan = _payload()
    orphan["criteria"] = [orphan["criteria"][1]]
    with pytest.raises(RoutingSLOAttainmentRejected):
        parse_routing_slo_attainment_contract(orphan)

    reordered = _payload()
    reordered["criteria"] = list(reversed(reordered["criteria"]))
    reordered_contract = _rehashed(reordered)
    _expect_rejection(
        lambda: validate_routing_slo_attainment_contract(reordered_contract),
        RoutingQualificationValidationCode.CONTRACT_BINDING_MISMATCH,
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("threshold_ns", 0),
        ("threshold_ns", 60_000_000_001),
        ("required_attainment_rate", "1.0"),
        ("required_attainment_rate", "1e-1"),
        ("required_attainment_rate", "0.970000"),
        ("confidence_level", "0"),
        ("confidence_level", "1"),
    ],
)
def test_threshold_and_confidence_boundaries_fail_closed(field, value):
    payload = _payload()
    if field == "threshold_ns":
        payload["criteria"][1]["assignment_slo_envelopes"][0]["required_observations"][
            0
        ][field] = value
    else:
        payload["criteria"][1]["policy_confidence_rules"][0]["confidence"][field] = (
            value
        )
    _expect_rejection(
        lambda: parse_routing_slo_attainment_contract(payload),
        RoutingQualificationValidationCode.OVERSIZED,
    )


@pytest.mark.parametrize("value", [1, 60_000_000_000])
def test_underlying_latency_threshold_boundaries_are_admissible(value):
    payload = _payload()
    for envelope in payload["criteria"][1]["assignment_slo_envelopes"]:
        envelope["required_observations"][0]["threshold_ns"] = value
    contract = _rehashed(payload)
    assert validate_routing_slo_attainment_contract(contract) == contract


def test_favorable_197_of_200_point_estimate_is_not_confidence_sufficient():
    point_estimate = 197 / 200
    lower_bound = wilson_lower_bound(197, 200, 0.95)
    confidence = _contract().criteria[1].policy_confidence_rules[0].confidence
    required = float(confidence.required_attainment_rate)

    assert point_estimate == pytest.approx(0.985)
    assert point_estimate > required
    assert lower_bound < required
    assert confidence.point_estimate_sufficiency == "NEVER_SUFFICIENT_ALONE"
    assert confidence.comparison_semantics == (
        "WILSON_TWO_SIDED_LOWER_BOUND_GTE_REQUIRED_ATTAINMENT_RATE"
    )


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        ("extra", RoutingQualificationValidationCode.EXTRA_FIELD),
        ("missing", RoutingQualificationValidationCode.MISSING_FIELD),
        (
            "producer_verdict",
            RoutingQualificationValidationCode.PRODUCER_VERDICT_FORBIDDEN,
        ),
        ("producer_attained", RoutingQualificationValidationCode.EXTRA_FIELD),
        ("wrong_type", RoutingQualificationValidationCode.WRONG_TYPE),
        ("wrong_version", RoutingQualificationValidationCode.OVERSIZED),
        ("bad_digest", RoutingQualificationValidationCode.INVALID_DIGEST),
        ("bad_role_order", RoutingQualificationValidationCode.SEMANTIC_INCONSISTENCY),
        ("bad_disposition", RoutingQualificationValidationCode.OVERSIZED),
    ],
)
def test_malformed_wire_and_semantic_near_misses_fail_closed(mutation, code):
    payload = _payload()
    slo = payload["criteria"][1]
    if mutation == "extra":
        slo["future_field"] = True
    elif mutation == "missing":
        del slo["assignment_slo_envelopes"]
    elif mutation == "producer_verdict":
        slo["producer_verdict"] = "PASS"
    elif mutation == "producer_attained":
        slo["assignment_slo_envelopes"][0]["attained"] = True
    elif mutation == "wrong_type":
        slo["assignment_slo_envelopes"][0]["required_observations"][0][
            "threshold_ns"
        ] = True
    elif mutation == "wrong_version":
        slo["assignment_slo_envelopes"][0]["required_observations"][0][
            "schema_version"
        ] = "exitspec.routing-slo-observation-metric.v2"
    elif mutation == "bad_digest":
        slo["baseline_policy_sha256"] = "A" * 64
    elif mutation == "bad_role_order":
        slo["assignment_slo_envelopes"] = list(
            reversed(slo["assignment_slo_envelopes"])
        )
    else:
        slo["assignment_slo_envelopes"][0]["missing_evidence_disposition"] = "EXCLUDE"
    _expect_rejection(lambda: parse_routing_slo_attainment_contract(payload), code)


def test_duplicate_and_noncanonical_wire_are_rejected_before_model_validation():
    duplicate = (
        b'{"id":"routing-qualification-campaign","id":"routing-qualification-campaign"}'
    )
    _expect_rejection(
        lambda: parse_routing_slo_attainment_contract(duplicate),
        RoutingQualificationValidationCode.DUPLICATE_FIELD,
    )
    noncanonical = b'{ "id": "routing-qualification-campaign" }'
    _expect_rejection(
        lambda: parse_routing_slo_attainment_contract(noncanonical),
        RoutingQualificationValidationCode.NON_CANONICAL,
    )


def test_privacy_and_verdict_aliases_cannot_extend_the_full_contract():
    payload = _payload()
    payload["criteria"][1]["source"] = {
        "speaker": "customer",
        "quote": "secret customer content",
        "location": "synthetic",
    }
    _expect_rejection(
        lambda: parse_routing_slo_attainment_contract(payload),
        RoutingQualificationValidationCode.SEMANTIC_INCONSISTENCY,
    )

    payload = _payload()
    payload["criteria"][1]["credentials"] = "secret"
    _expect_rejection(
        lambda: parse_routing_slo_attainment_contract(payload),
        RoutingQualificationValidationCode.EXTRA_FIELD,
    )

    payload = _payload()
    payload["criteria"][1]["acceptance_verdict"] = "PASS"
    _expect_rejection(
        lambda: parse_routing_slo_attainment_contract(payload),
        RoutingQualificationValidationCode.PRODUCER_VERDICT_FORBIDDEN,
    )


def test_typed_model_copy_and_construct_bypasses_fail_closed_at_all_public_paths():
    contract = _contract()
    copied = contract.model_copy(update={"decision": "PASS"})
    _expect_rejection(
        lambda: routing_slo_attainment_contract_digest(copied),
        RoutingQualificationValidationCode.PRODUCER_VERDICT_FORBIDDEN,
    )

    criterion = contract.criteria[1]
    observation = criterion.assignment_slo_envelopes[0].required_observations[0]
    bad_observation = observation.model_copy(update={"threshold_ns": 0})
    bad_envelope = criterion.assignment_slo_envelopes[0].model_copy(
        update={"required_observations": (bad_observation,)}
    )
    bad_criterion = criterion.model_copy(
        update={
            "assignment_slo_envelopes": (
                bad_envelope,
                criterion.assignment_slo_envelopes[1],
            )
        }
    )
    bad_contract = contract.model_copy(
        update={"criteria": (contract.criteria[0], bad_criterion)}
    )
    for call in (
        lambda: routing_slo_attainment_contract_digest(bad_contract),
        lambda: serialize_routing_slo_attainment_contract(bad_contract),
        lambda: validate_routing_slo_attainment_contract(bad_contract),
    ):
        _expect_rejection(call, RoutingQualificationValidationCode.INVALID_BOUND)

    raw = dict(criterion.__dict__)
    del raw["verdict_boundary"]
    constructed = RoutingSLOAttainmentCriterionV1.model_construct(**raw)
    constructed_contract = contract.model_copy(
        update={"criteria": (contract.criteria[0], constructed)}
    )
    _expect_rejection(
        lambda: routing_slo_attainment_contract_digest(constructed_contract),
        RoutingQualificationValidationCode.MISSING_FIELD,
    )


def test_falsey_malformed_internal_state_and_non_string_keys_fail_closed():
    contract = _contract()
    malformed = contract.model_copy()
    object.__setattr__(malformed, "__pydantic_extra__", [])
    _expect_rejection(
        lambda: serialize_routing_slo_attainment_contract(malformed),
        RoutingQualificationValidationCode.WRONG_TYPE,
    )

    malformed = contract.model_copy()
    state = object.__getattribute__(malformed, "__dict__")
    state[7] = "bad"
    _expect_rejection(
        lambda: routing_slo_attainment_contract_digest(malformed),
        RoutingQualificationValidationCode.WRONG_TYPE,
    )


def test_json_float_is_not_a_permitted_threshold_representation():
    payload = _payload()
    payload["criteria"][1]["assignment_slo_envelopes"][0]["required_observations"][0][
        "threshold_ns"
    ] = 1.0
    _expect_rejection(
        lambda: parse_routing_slo_attainment_contract(payload),
        RoutingQualificationValidationCode.WRONG_TYPE,
    )
