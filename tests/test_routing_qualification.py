from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from exitspec.canonical import canonical_json_bytes
from exitspec.contracts import contract_digest, freeze_contract, verify_contract_digest
from exitspec.models import POCContract, RoutingQualificationCriterionV1
from exitspec.routing_qualification import (
    RoutingQualificationRejected,
    RoutingQualificationValidationCode,
    parse_routing_qualification_contract,
    parse_routing_qualification_evidence_fixture,
    routing_qualification_contract_digest,
    serialize_routing_qualification_contract,
    serialize_routing_qualification_evidence_fixture,
    validate_routing_qualification_evidence_fixture,
)
from exitspec.runner import load_contract


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_FIXTURE = PROJECT_ROOT / (
    "examples/routing-qualification/contracts/routing-qualification-v1.json"
)
EVIDENCE_FIXTURE = PROJECT_ROOT / (
    "examples/routing-qualification/evidence/"
    "routing-qualification-evidence.synthetic.json"
)
BASE_CONTRACT = PROJECT_ROOT / "examples/support-agent/contracts/tool-selection-v1.yaml"


def _contract_payload() -> dict[str, object]:
    return json.loads(CONTRACT_FIXTURE.read_bytes())


def _evidence_payload() -> dict[str, object]:
    return json.loads(EVIDENCE_FIXTURE.read_bytes())


def _criterion() -> RoutingQualificationCriterionV1:
    return parse_routing_qualification_contract(_contract_payload())


def _frozen_poc_contract() -> POCContract:
    base = load_contract(BASE_CONTRACT)
    payload = base.model_dump(mode="python")
    payload["criteria"] = (_criterion(),)
    approved = POCContract.model_validate(payload)
    frozen = freeze_contract(approved)
    assert verify_contract_digest(frozen)
    return frozen


def _expect_code(
    payload: dict[str, object], code: RoutingQualificationValidationCode
) -> None:
    with pytest.raises(RoutingQualificationRejected) as caught:
        parse_routing_qualification_contract(payload)
    assert caught.value.code is code


def test_golden_contract_is_strict_run_independent_and_provider_neutral():
    criterion = _criterion()
    assert criterion.criterion_type == "routing_qualification_v1"
    assert criterion.protocol_id == "routing_qualification_v1"
    assert criterion.schema_version == "exitspec.routing-qualification.v1"
    assert criterion.canonicalization.hash_algorithm_id == "sha256_v1"
    assert criterion.ownership.acceptance_owner == "EXIT_SPEC"
    assert criterion.ownership.producer_acceptance_authority == "FORBIDDEN"
    assert criterion.run_policy.pooling_policy.startswith("FORBIDDEN_")
    assert "run_id" not in RoutingQualificationCriterionV1.model_fields
    assert "telemetry_capsule_id" not in criterion.telemetry.__class__.model_fields
    assert (
        "route_decision_receipt_ids"
        not in criterion.route_decision_receipts.__class__.model_fields
    )
    assert "acceptance_verdict" not in RoutingQualificationCriterionV1.model_fields
    assert routing_qualification_contract_digest(criterion) == (
        "a6fcca1a613e9c13cd89ff1feb3f5d8853a2b5f97994446ca6267b8ef01fde0e"
    )


def test_canonical_bytes_round_trip_and_evidence_fixture_is_loudly_synthetic():
    criterion_bytes = canonical_json_bytes(_contract_payload())
    criterion = parse_routing_qualification_contract(criterion_bytes)
    evidence = parse_routing_qualification_evidence_fixture(_evidence_payload())

    assert evidence.fixture_status == "LOUDLY_SYNTHETIC_TEST_ONLY"
    assert evidence.run_id.startswith("synthetic-routing-run-")
    assert evidence.telemetry_capsule_id.startswith("synthetic-routing-capsule-")
    assert all(
        receipt.startswith("synthetic-route-receipt-")
        for receipt in evidence.route_decision_receipt_ids
    )
    assert evidence.contract_sha256 == routing_qualification_contract_digest(criterion)
    assert (
        parse_routing_qualification_contract(
            serialize_routing_qualification_contract(criterion)
        )
        == criterion
    )
    assert (
        parse_routing_qualification_evidence_fixture(
            serialize_routing_qualification_evidence_fixture(evidence)
        )
        == evidence
    )


def test_routing_criterion_uses_existing_poc_contract_freeze_and_hash():
    frozen = _frozen_poc_contract()
    assert type(frozen.criteria[0]) is RoutingQualificationCriterionV1
    assert frozen.criteria[0].id == "routing_qualification_v1"
    assert frozen.canonical_hash == contract_digest(frozen)
    assert verify_contract_digest(frozen)

    evidence_payload = _evidence_payload()
    evidence_payload["contract_sha256"] = frozen.canonical_hash
    evidence = parse_routing_qualification_evidence_fixture(evidence_payload)
    assert validate_routing_qualification_evidence_fixture(frozen, evidence) == evidence

    changed_payload = frozen.model_dump(mode="python")
    changed_payload["canonical_hash"] = None
    changed_payload["criteria"][0]["title"] = "Changed frozen title"
    changed = POCContract.model_validate(changed_payload)
    assert contract_digest(changed) != frozen.canonical_hash


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        ("extra", RoutingQualificationValidationCode.EXTRA_FIELD),
        ("missing", RoutingQualificationValidationCode.MISSING_FIELD),
        ("wrong_version", RoutingQualificationValidationCode.WRONG_VERSION),
        ("wrong_type", RoutingQualificationValidationCode.WRONG_TYPE),
        ("oversized", RoutingQualificationValidationCode.OVERSIZED),
        ("invalid_digest", RoutingQualificationValidationCode.INVALID_DIGEST),
        (
            "equal_policy_digest",
            RoutingQualificationValidationCode.SEMANTIC_INCONSISTENCY,
        ),
        (
            "nondeterministic_order",
            RoutingQualificationValidationCode.SEMANTIC_INCONSISTENCY,
        ),
        ("negative_age", RoutingQualificationValidationCode.INVALID_BOUND),
        (
            "cache_contradiction",
            RoutingQualificationValidationCode.SEMANTIC_INCONSISTENCY,
        ),
        (
            "failure_contradiction",
            RoutingQualificationValidationCode.SEMANTIC_INCONSISTENCY,
        ),
        (
            "producer_verdict",
            RoutingQualificationValidationCode.PRODUCER_VERDICT_FORBIDDEN,
        ),
    ],
)
def test_contract_near_misses_fail_closed_with_stable_codes(
    mutation: str, code: RoutingQualificationValidationCode
):
    payload = deepcopy(_contract_payload())
    if mutation == "extra":
        payload["future_field"] = True
    elif mutation == "missing":
        del payload["telemetry"]
    elif mutation == "wrong_version":
        payload["schema_version"] = "exitspec.routing-qualification.v2"
    elif mutation == "wrong_type":
        payload["trial_order"]["trial_count"] = True
    elif mutation == "oversized":
        payload["title"] = "x" * 4_097
    elif mutation == "invalid_digest":
        payload["candidate_policy"]["policy_sha256"] = "A" * 64
    elif mutation == "equal_policy_digest":
        payload["baseline_policy"]["policy_sha256"] = payload["candidate_policy"][
            "policy_sha256"
        ]
    elif mutation == "nondeterministic_order":
        payload["trial_order"]["ordering_rule"] = "RANDOMIZED"
    elif mutation == "negative_age":
        payload["telemetry"]["max_age_seconds"] = -1
    elif mutation == "cache_contradiction":
        payload["cache_reset"]["cross_policy_cache_reuse"] = True
    elif mutation == "failure_contradiction":
        payload["failure_injection"]["maximum_injected_failures"] = 1
    else:
        payload["acceptance_verdict"] = "PASS"
    _expect_code(payload, code)


def test_duplicate_fields_floats_and_unbounded_integers_are_rejected():
    duplicate = b'{"protocol_id":"routing_qualification_v1","protocol_id":"routing_qualification_v1"}'
    with pytest.raises(RoutingQualificationRejected) as caught:
        parse_routing_qualification_contract(duplicate)
    assert caught.value.code is RoutingQualificationValidationCode.DUPLICATE_FIELD

    float_payload = _contract_payload()
    float_payload["serving"]["tensor_parallel_size"] = 1.0
    _expect_code(float_payload, RoutingQualificationValidationCode.WRONG_TYPE)

    huge_payload = _contract_payload()
    huge_payload["trial_order"]["trial_count"] = 2_147_483_648
    _expect_code(huge_payload, RoutingQualificationValidationCode.OVERSIZED)


def test_all_material_protocol_mutations_change_the_existing_contract_hash():
    criterion = _criterion()
    original = routing_qualification_contract_digest(criterion)
    mutations = (
        {"criterion_type": "routing_qualification_v1_mutated"},
        {"protocol_id": "routing_qualification_v1_mutated"},
        {"schema_version": "exitspec.routing-qualification.v2"},
        {"protocol_version": "1.0.1"},
        {"id": "routing_qualification_v1_mutated"},
        {"title": "Changed title"},
        {"must_have": False},
        {"human_added": False},
        {"normalized_claim": "Changed claim"},
        {"owner": "changed-owner"},
        {"evidence_policy": "Changed evidence policy"},
        {
            "canonicalization": criterion.canonicalization.model_copy(
                update={"hash_algorithm_id": "sha512_v1"}
            )
        },
        {
            "ownership": criterion.ownership.model_copy(
                update={"acceptance_owner": "PRODUCER"}
            )
        },
        {
            "candidate_policy": criterion.candidate_policy.model_copy(
                update={"policy_sha256": "1" * 64}
            )
        },
        {
            "baseline_policy": criterion.baseline_policy.model_copy(
                update={"policy_sha256": "2" * 64}
            )
        },
        {
            "routing_configuration": criterion.routing_configuration.model_copy(
                update={"configuration_sha256": "3" * 64}
            )
        },
        {
            "request_trace": criterion.request_trace.model_copy(
                update={"trace_sha256": "4" * 64}
            )
        },
        {"trial_order": criterion.trial_order.model_copy(update={"trial_count": 3})},
        {
            "cache_reset": criterion.cache_reset.model_copy(
                update={"reset_required": False}
            )
        },
        {
            "failure_injection": criterion.failure_injection.model_copy(
                update={"configuration_sha256": "5" * 64}
            )
        },
        {"serving": criterion.serving.model_copy(update={"engine_version": "2.0.0"})},
        {"telemetry": criterion.telemetry.model_copy(update={"max_age_seconds": 301})},
        {
            "route_decision_receipts": criterion.route_decision_receipts.model_copy(
                update={"verdict_boundary": "PRODUCER"}
            )
        },
        {
            "run_policy": criterion.run_policy.model_copy(
                update={"default_repetitions": 3}
            )
        },
        {"privacy": criterion.privacy.model_copy(update={"credentials": "ALLOWED"})},
        {"approved": False},
    )
    for update in mutations:
        assert (
            routing_qualification_contract_digest(criterion.model_copy(update=update))
            != original
        )


@pytest.mark.parametrize(
    "field",
    [
        "acceptance_verdict",
        "producer_verdict",
        "verdict",
        "inferdrome_acceptance_verdict",
    ],
)
def test_evidence_acceptance_verdict_fields_are_forbidden(field: str):
    payload = _evidence_payload()
    payload[field] = "PASS"
    with pytest.raises(RoutingQualificationRejected) as caught:
        parse_routing_qualification_evidence_fixture(payload)
    assert (
        caught.value.code
        is RoutingQualificationValidationCode.PRODUCER_VERDICT_FORBIDDEN
    )


def test_evidence_binding_rejects_mismatched_observed_identity_without_verdict():
    frozen = _frozen_poc_contract()
    payload = _evidence_payload()
    payload["contract_sha256"] = frozen.canonical_hash
    evidence = parse_routing_qualification_evidence_fixture(payload)
    mutated = evidence.model_copy(update={"routing_configuration_sha256": "0" * 64})
    with pytest.raises(RoutingQualificationRejected) as caught:
        validate_routing_qualification_evidence_fixture(frozen, mutated)
    assert (
        caught.value.code
        is RoutingQualificationValidationCode.CONTRACT_BINDING_MISMATCH
    )


def test_contract_rejects_source_content_at_the_privacy_boundary():
    payload = _contract_payload()
    payload["source"] = {
        "location": "synthetic",
        "quote": "customer secret must not be retained",
        "speaker": "customer",
    }
    _expect_code(payload, RoutingQualificationValidationCode.SEMANTIC_INCONSISTENCY)
