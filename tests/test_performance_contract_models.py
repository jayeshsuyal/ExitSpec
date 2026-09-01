from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from exitspec.confirmations import (
    ConfirmationDecision,
    confirmation_matches_contract,
    record_confirmation,
)
from exitspec.contracts import contract_digest, freeze_contract
from exitspec.models import (
    Criterion,
    InferencePerformanceCriterion,
    InferencePerformanceCriterionV2,
    InferenceQualificationCriterionV1,
    MeasurementPopulationPolicyV1,
    POCContract,
)


def inference_qualification_criterion_payload(
    *, semantic: bool = False
) -> dict:
    latency: dict[str, object] = {
        "requirement_kind": (
            "SEMANTIC_FIRST_NONEMPTY_TTFT_P95" if semantic else "NATIVE_TTFT_P95"
        ),
        "observation_id": (
            "semantic_first_nonempty_ttft_sample" if semantic else "native_ttft_sample"
        ),
        "metric_definition_id": (
            "first_nonempty_choices_delta_content_v1"
            if semantic
            else "vllm_first_choices_event_v0_26"
        ),
        "source_field": (
            "response.choices[].delta.content"
            if semantic
            else "request.timing.ttft_ns"
        ),
        "unit": "ns",
        "population": "successful_measured_requests_with_observed_ttft",
        "reducer_id": "nearest_rank_v1",
        "percentile": "p95",
        "operator": "lt",
        "threshold_ns": 20_000_000,
        "minimum_successful_samples": 100,
        "equality_outcome": "FAIL",
        "must_pass": True,
    }
    return {
        "criterion_type": "inference_qualification_v1",
        "schema_version": "exitspec.inference-qualification-criterion.v1",
        "protocol_id": "inference-performance-qualification",
        "protocol_version": "1.0.0",
        "id": "QUAL-TTFT-01",
        "title": "Native TTFT qualification question",
        "must_have": True,
        "source": None,
        "human_added": True,
        "normalized_claim": "The frozen question requires bounded native TTFT and reliability.",
        "latency_requirement": latency,
        "reliability_requirement": {
            "observation_id": "native_measured_request_outcome",
            "source_field": "request.outcome.status",
            "latency_population": "successful_measured_requests_with_observed_ttft",
            "reliability_numerator": "failed_or_anomalous_native_measured_requests",
            "reliability_denominator": "all_measured_requests",
            "operator": "lt",
            "threshold_basis_points": 100,
            "exact_attempts": 100,
            "must_pass": True,
        },
        "approved": True,
    }


def performance_criterion_payload() -> dict:
    return {
        "criterion_type": "inference_performance_v1",
        "id": "PERF-LATENCY-01",
        "title": "Inference latency and reliability",
        "must_have": True,
        "source": {
            "speaker": "customer_vp_engineering",
            "quote": (
                "P95 time to first token must stay at or below 500 milliseconds "
                "and error rate must remain below one percent."
            ),
            "location": "Synthetic discovery transcript, line 21",
        },
        "human_added": False,
        "normalized_claim": (
            "At the frozen workload, client-observed p95 time to first token is "
            "at most 500 milliseconds and attempted-request error rate is below 1%."
        ),
        "ttft_p95": {
            "metric": "time_to_first_token",
            "aggregation": "p95",
            "unit": "milliseconds",
            "operator": "lte",
            "threshold": 500.0,
            "method": "nearest_rank",
            "minimum_successful_samples": 100,
            "must_pass": True,
        },
        "error_rate": {
            "metric": "error_rate",
            "aggregation": "rate",
            "unit": "proportion",
            "operator": "lt",
            "threshold": 0.01,
            "method": "failed_attempts_over_total_attempts",
            "minimum_attempts": 100,
            "must_pass": True,
        },
        "workload_slice": "inference-latency-demo-v1",
        "adapter": "vllm_latency",
        "adapter_version": "1.0.0",
        "owner": "vendor_solutions_engineer",
        "evidence_policy": (
            "Persist the frozen probe manifest, complete request records, "
            "calculation inputs, and SHA-256 digests."
        ),
        "approved": True,
    }


def measurement_population_policy_payload() -> dict:
    return {
        "schema_version": "exitspec.measurement-population.v1",
        "calculation_version": "exitspec.performance-verdicts.v2",
        "measured_population": {
            "phases": ["MEASURED"],
            "exact_attempts": 100,
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


def performance_criterion_v2_payload() -> dict:
    payload = performance_criterion_payload()
    payload["criterion_type"] = "inference_performance_v2"
    payload["measurement_policy"] = measurement_population_policy_payload()
    return payload


def test_inference_performance_criterion_is_a_strict_tagged_composite():
    payload = performance_criterion_payload()

    criterion = InferencePerformanceCriterion.model_validate(payload)

    assert criterion.criterion_type == "inference_performance_v1"
    assert criterion.ttft_p95.must_pass is True
    assert criterion.error_rate.must_pass is True
    assert criterion.model_dump(mode="json") == payload


def test_inference_qualification_criterion_uses_a_discriminated_latency_union():
    native = InferenceQualificationCriterionV1.model_validate(
        inference_qualification_criterion_payload()
    )
    semantic = InferenceQualificationCriterionV1.model_validate(
        inference_qualification_criterion_payload(semantic=True)
    )

    assert native.latency_requirement.requirement_kind == "NATIVE_TTFT_P95"
    assert (
        semantic.latency_requirement.requirement_kind
        == "SEMANTIC_FIRST_NONEMPTY_TTFT_P95"
    )
    malformed = inference_qualification_criterion_payload()
    malformed["latency_requirement"] = {
        "observation_id": "native_ttft_sample",
        "metric_definition_id": "vllm_first_choices_event_v0_26",
    }
    with pytest.raises(ValidationError):
        InferenceQualificationCriterionV1.model_validate(malformed)


def test_contract_accepts_an_unambiguous_mix_of_legacy_and_performance_criteria(
    approved_contract,
):
    payload = approved_contract.model_dump(mode="python")
    payload["criteria"] = [
        payload["criteria"][0],
        performance_criterion_payload(),
    ]

    contract = POCContract.model_validate(payload)

    assert isinstance(contract.criteria[0], Criterion)
    assert isinstance(contract.criteria[1], InferencePerformanceCriterion)
    assert "criterion_type" not in contract.criteria[0].model_dump(mode="json")
    assert (
        contract.criteria[1].criterion_type
        == "inference_performance_v1"
    )


def test_performance_criterion_requires_its_tag_inside_contract(approved_contract):
    criterion = performance_criterion_payload()
    del criterion["criterion_type"]
    payload = approved_contract.model_dump(mode="python")
    payload["criteria"] = [criterion]

    with pytest.raises(ValidationError, match="criterion_type"):
        POCContract.model_validate(payload)


def test_unknown_performance_criterion_tag_is_rejected(approved_contract):
    criterion = performance_criterion_payload()
    criterion["criterion_type"] = "inference_performance_v3"
    payload = approved_contract.model_dump(mode="python")
    payload["criteria"] = [criterion]

    with pytest.raises(ValidationError, match="inference_performance_v2"):
        POCContract.model_validate(payload)


def test_v2_performance_criterion_requires_the_complete_population_policy():
    payload = performance_criterion_v2_payload()

    criterion = InferencePerformanceCriterionV2.model_validate(payload)

    assert criterion.criterion_type == "inference_performance_v2"
    assert criterion.measurement_policy.measured_population.exact_attempts == 100
    assert criterion.measurement_policy.reliability.denominator == (
        "all_measured_attempts"
    )
    assert criterion.model_dump(mode="json") == payload

    del payload["measurement_policy"]
    with pytest.raises(ValidationError, match="measurement_policy"):
        InferencePerformanceCriterionV2.model_validate(payload)


def test_contract_union_preserves_v1_and_selects_v2_by_exact_tag(
    approved_contract,
):
    payload = approved_contract.model_dump(mode="python")
    payload["criteria"] = [
        performance_criterion_payload(),
        {
            **performance_criterion_v2_payload(),
            "id": "PERF-LATENCY-02",
        },
    ]

    contract = POCContract.model_validate(payload)

    assert type(contract.criteria[0]) is InferencePerformanceCriterion
    assert type(contract.criteria[1]) is InferencePerformanceCriterionV2
    serialized = contract.model_dump(mode="json")
    assert serialized["criteria"][1]["measurement_policy"] == (
        measurement_population_policy_payload()
    )


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("schema_version",), "exitspec.measurement-population.v2"),
        (("calculation_version",), "exitspec.performance-verdicts.v1"),
        (("measured_population", "phases"), ["WARMUP"]),
        (("measured_population", "warmups_included"), True),
        (("measured_population", "preflight_included"), True),
        (("measured_population", "retries"), 1),
        (
            ("reliability", "outcomes"),
            ["TIMEOUT", "HTTP_ERROR", "PROTOCOL_ERROR", "TRANSPORT_ERROR"],
        ),
        (
            ("invalid_evidence", "record_conditions"),
            ["MISSING_RECORD", "EXTRA_RECORD", "DUPLICATE_RECORD"],
        ),
        (("invalid_evidence", "disposition"), "FAIL"),
    ],
)
def test_population_policy_rejects_semantic_drift(path, value):
    payload = measurement_population_policy_payload()
    target = payload
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value

    with pytest.raises(ValidationError):
        MeasurementPopulationPolicyV1.model_validate(payload)


def test_v2_attempt_population_must_match_the_reliability_rule():
    payload = performance_criterion_v2_payload()
    payload["measurement_policy"]["measured_population"]["exact_attempts"] = 99

    with pytest.raises(ValidationError, match="attempts must match"):
        InferencePerformanceCriterionV2.model_validate(payload)


def test_population_policy_is_immutable():
    policy = MeasurementPopulationPolicyV1.model_validate(
        measurement_population_policy_payload()
    )

    with pytest.raises(ValidationError, match="Instance is frozen"):
        policy.measured_population.exact_attempts = 200
    with pytest.raises(ValidationError, match="Instance is frozen"):
        policy.reliability.denominator = "successful_attempts"


def test_population_policy_changes_contract_hash_and_customer_confirmation(
    approved_contract,
):
    original_payload = approved_contract.model_dump(mode="python")
    original_payload["criteria"] = [performance_criterion_v2_payload()]
    original = POCContract.model_validate(original_payload)
    confirmation = record_confirmation(
        original,
        confirmer_identity="customer_vp_engineering",
        decision=ConfirmationDecision.CONFIRM,
        agreement_acknowledged=True,
        rationale="The counting policy and acceptance rules are confirmed.",
        idempotency_key="population-policy-confirmation-v1",
    )

    changed_payload = deepcopy(original_payload)
    changed_payload["criteria"][0]["error_rate"]["minimum_attempts"] = 101
    changed_payload["criteria"][0]["measurement_policy"][
        "measured_population"
    ]["exact_attempts"] = 101
    changed = POCContract.model_validate(changed_payload)

    assert contract_digest(original) != contract_digest(changed)
    assert confirmation_matches_contract(original, confirmation)
    assert not confirmation_matches_contract(changed, confirmation)

    frozen = freeze_contract(original)
    assert frozen.canonical_hash is not None
    assert frozen.canonical_hash != contract_digest(changed)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("ttft_p95", "operator"), "gte"),
        (("ttft_p95", "unit"), "seconds"),
        (("ttft_p95", "method"), "linear_interpolation"),
        (("error_rate", "operator"), "gte"),
        (("error_rate", "unit"), "percent"),
        (("error_rate", "method"), "successful_attempts_over_total_attempts"),
    ],
)
def test_performance_rules_reject_unsupported_operators_units_and_methods(
    path,
    value,
):
    payload = performance_criterion_payload()
    payload[path[0]][path[1]] = value

    with pytest.raises(ValidationError):
        InferencePerformanceCriterion.model_validate(payload)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("ttft_p95", "must_pass"), False),
        (("error_rate", "must_pass"), False),
        (("must_have",), False),
    ],
)
def test_performance_composite_cannot_make_either_rule_optional(path, value):
    payload = performance_criterion_payload()
    if len(path) == 1:
        payload[path[0]] = value
    else:
        payload[path[0]][path[1]] = value

    with pytest.raises(ValidationError):
        InferencePerformanceCriterion.model_validate(payload)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("ttft_p95", "threshold"), 0),
        (("ttft_p95", "threshold"), float("inf")),
        (("ttft_p95", "threshold"), 60_001),
        (("ttft_p95", "minimum_successful_samples"), 0),
        (("ttft_p95", "minimum_successful_samples"), 1_001),
        (("error_rate", "threshold"), -0.01),
        (("error_rate", "threshold"), 1.01),
        (("error_rate", "threshold"), float("nan")),
        (("error_rate", "minimum_attempts"), 0),
        (("error_rate", "minimum_attempts"), 1_001),
    ],
)
def test_performance_rules_reject_invalid_numeric_bounds(path, value):
    payload = performance_criterion_payload()
    payload[path[0]][path[1]] = value

    with pytest.raises(ValidationError):
        InferencePerformanceCriterion.model_validate(payload)


def test_performance_rules_reject_impossible_sample_relationships():
    payload = performance_criterion_payload()
    payload["ttft_p95"]["minimum_successful_samples"] = 101
    payload["error_rate"]["minimum_attempts"] = 100

    with pytest.raises(ValidationError, match="cannot exceed"):
        InferencePerformanceCriterion.model_validate(payload)

    payload = performance_criterion_payload()
    payload["error_rate"]["operator"] = "lt"
    payload["error_rate"]["threshold"] = 0.0

    with pytest.raises(ValidationError, match="greater than 0"):
        InferencePerformanceCriterion.model_validate(payload)


@pytest.mark.parametrize(
    "path",
    [
        (),
        ("ttft_p95",),
        ("error_rate",),
    ],
)
def test_performance_models_reject_unknown_fields(path):
    payload = performance_criterion_payload()
    target = payload
    for part in path:
        target = target[part]
    target["undocumented"] = "must be rejected"

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        InferencePerformanceCriterion.model_validate(payload)


def test_performance_criterion_requires_a_traceable_origin():
    payload = performance_criterion_payload()
    payload["source"] = None
    payload["human_added"] = False

    with pytest.raises(ValidationError, match="source reference"):
        InferencePerformanceCriterion.model_validate(payload)


def test_explicitly_human_added_performance_criterion_is_allowed():
    payload = performance_criterion_payload()
    payload["source"] = None
    payload["human_added"] = True

    criterion = InferencePerformanceCriterion.model_validate(payload)

    assert criterion.source is None
    assert criterion.human_added is True


def test_performance_criterion_and_rules_are_immutable():
    criterion = InferencePerformanceCriterion.model_validate(
        performance_criterion_payload()
    )

    with pytest.raises(ValidationError, match="Instance is frozen"):
        criterion.owner = "changed-owner"
    with pytest.raises(ValidationError, match="Instance is frozen"):
        criterion.ttft_p95.threshold = 900.0
    with pytest.raises(ValidationError, match="Instance is frozen"):
        criterion.error_rate.threshold = 0.25


def test_criterion_ids_must_be_unique_across_both_contract_criterion_types(
    approved_contract,
):
    performance = performance_criterion_payload()
    performance["id"] = approved_contract.criteria[0].id
    payload = approved_contract.model_dump(mode="python")
    payload["criteria"] = [
        payload["criteria"][0],
        performance,
    ]

    with pytest.raises(ValidationError, match="Criterion IDs must be unique"):
        POCContract.model_validate(payload)


def test_approved_contract_rejects_unapproved_performance_criterion(
    approved_contract,
):
    performance = performance_criterion_payload()
    performance["approved"] = False
    payload = deepcopy(approved_contract.model_dump(mode="python"))
    payload["criteria"] = [performance]

    with pytest.raises(ValidationError, match="all criteria to be approved"):
        POCContract.model_validate(payload)
