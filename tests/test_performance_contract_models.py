from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from exitspec.models import (
    Criterion,
    InferencePerformanceCriterion,
    POCContract,
)


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


def test_inference_performance_criterion_is_a_strict_tagged_composite():
    payload = performance_criterion_payload()

    criterion = InferencePerformanceCriterion.model_validate(payload)

    assert criterion.criterion_type == "inference_performance_v1"
    assert criterion.ttft_p95.must_pass is True
    assert criterion.error_rate.must_pass is True
    assert criterion.model_dump(mode="json") == payload


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
    criterion["criterion_type"] = "inference_performance_v2"
    payload = approved_contract.model_dump(mode="python")
    payload["criteria"] = [criterion]

    with pytest.raises(ValidationError, match="inference_performance_v1"):
        POCContract.model_validate(payload)


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
