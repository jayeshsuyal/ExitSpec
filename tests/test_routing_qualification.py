from copy import deepcopy
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from exitspec.contracts import contract_digest
from exitspec.models import (
    ContractStatus,
    POCContract,
    RoutingQualificationCriterionV1,
)
from exitspec.canonical import canonical_json_bytes
from exitspec.routing_qualification import (
    RoutingEvidenceProvenanceV1,
    RoutingQualificationEvidenceFixtureV1,
    RoutingQualificationRejected,
    RoutingQualificationValidationCode,
    parse_routing_qualification_contract,
    parse_routing_qualification_evidence_fixture,
    routing_qualification_contract_digest,
    serialize_routing_qualification_contract,
    serialize_routing_qualification_evidence_fixture,
    validate_routing_qualification_evidence_fixture,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_FIXTURE = PROJECT_ROOT / (
    "examples/routing-qualification/contracts/routing-qualification-v1.json"
)
EVIDENCE_FIXTURE = PROJECT_ROOT / (
    "examples/routing-qualification/evidence/"
    "routing-qualification-evidence.synthetic.json"
)


def _contract_payload() -> dict[str, Any]:
    return json.loads(CONTRACT_FIXTURE.read_bytes())


def _evidence_payload() -> dict[str, Any]:
    return json.loads(EVIDENCE_FIXTURE.read_bytes())


def _frozen_poc_contract() -> POCContract:
    return parse_routing_qualification_contract(_contract_payload())


def _criterion() -> RoutingQualificationCriterionV1:
    contract = _frozen_poc_contract()
    criterion = contract.criteria[0]
    assert type(criterion) is RoutingQualificationCriterionV1
    return criterion


def _rehashed_contract(payload: dict[str, Any]) -> POCContract:
    candidate = deepcopy(payload)
    candidate["canonical_hash"] = None
    without_hash = POCContract.model_validate(candidate)
    candidate["canonical_hash"] = contract_digest(without_hash)
    return POCContract.model_validate(candidate)


def _rehashed_criterion_mutation(update: dict[str, Any]) -> POCContract:
    payload = _contract_payload()
    payload["criteria"][0].update(update)
    return _rehashed_contract(payload)


def _raw_contract_digest(payload: dict[str, Any]) -> str:
    material = deepcopy(payload)
    del material["canonical_hash"]
    return hashlib.sha256(canonical_json_bytes(material)).hexdigest()


def _expect_contract_code(
    payload: dict[str, Any], code: RoutingQualificationValidationCode
) -> None:
    with pytest.raises(RoutingQualificationRejected) as caught:
        parse_routing_qualification_contract(payload)
    assert caught.value.code is code


def _expect_rejection(
    callable_obj: Any, code: RoutingQualificationValidationCode
) -> None:
    with pytest.raises(RoutingQualificationRejected) as caught:
        callable_obj()
    assert caught.value.code is code


def test_golden_fixture_is_a_frozen_poc_and_outer_hash_is_the_only_contract_digest():
    frozen = _frozen_poc_contract()
    criterion = frozen.criteria[0]

    assert frozen.status is ContractStatus.FROZEN
    assert frozen.canonical_hash == (
        "e3bbcab57ac37987f981e0de1a36e56ae6f649cd2f3c75d8d7bcd637583a0516"
    )
    assert routing_qualification_contract_digest(frozen) == frozen.canonical_hash
    assert type(criterion) is RoutingQualificationCriterionV1
    assert criterion.criterion_type == "routing_qualification_v1"
    assert criterion.protocol_id == "routing_qualification_v1"
    assert criterion.schema_version == "exitspec.routing-qualification.v1"
    assert criterion.canonicalization.hash_algorithm_id == "sha256_v1"
    assert criterion.ownership.acceptance_owner == "EXIT_SPEC"
    assert criterion.ownership.producer_acceptance_authority == "FORBIDDEN"
    assert criterion.run_policy.pooling_policy.startswith("FORBIDDEN_")
    assert criterion.route_decision_receipts.required_provenance_fields[:2] == (
        "route_decision_receipt_id",
        "route_decision_receipt_sha256",
    )
    assert (
        "receipt_id" not in criterion.route_decision_receipts.required_provenance_fields
    )
    assert (
        "receipt_sha256"
        not in criterion.route_decision_receipts.required_provenance_fields
    )
    assert "run_id" not in RoutingQualificationCriterionV1.model_fields
    assert "telemetry_capsule_id" not in criterion.telemetry.__class__.model_fields
    assert (
        "route_decision_receipt_ids"
        not in criterion.route_decision_receipts.__class__.model_fields
    )


def test_criterion_only_payload_is_not_accepted_as_a_contract():
    criterion_payload = _contract_payload()["criteria"][0]
    _expect_contract_code(
        criterion_payload, RoutingQualificationValidationCode.EXTRA_FIELD
    )


def test_canonical_bytes_round_trip_and_complete_synthetic_fixture_binding():
    contract = parse_routing_qualification_contract(
        canonical_json_bytes(_contract_payload())
    )
    evidence = parse_routing_qualification_evidence_fixture(_evidence_payload())

    assert evidence.fixture_status == "LOUDLY_SYNTHETIC_TEST_ONLY"
    assert evidence.run_id.startswith("synthetic-routing-run-")
    assert evidence.telemetry_capsule_id.startswith("synthetic-routing-capsule-")
    assert len(evidence.route_decision_receipt_ids) == 16
    assert len(set(evidence.route_decision_receipt_ids)) == 16
    assert all(
        receipt.startswith("synthetic-route-receipt-")
        for receipt in evidence.route_decision_receipt_ids
    )
    assert evidence.contract_sha256 == contract.canonical_hash
    assert (
        validate_routing_qualification_evidence_fixture(contract, evidence) == evidence
    )
    assert (
        parse_routing_qualification_contract(
            serialize_routing_qualification_contract(contract)
        )
        == contract
    )
    assert (
        parse_routing_qualification_evidence_fixture(
            serialize_routing_qualification_evidence_fixture(evidence)
        )
        == evidence
    )


def test_outer_hash_changes_when_routing_vocabulary_changes():
    frozen = _frozen_poc_contract()
    changed = _rehashed_criterion_mutation({"title": "Changed frozen title"})

    assert changed.canonical_hash == contract_digest(changed)
    assert changed.canonical_hash != frozen.canonical_hash


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
        del payload["criteria"]
    elif mutation == "wrong_version":
        payload["criteria"][0]["schema_version"] = "exitspec.routing-qualification.v2"
    elif mutation == "wrong_type":
        payload["criteria"][0]["trial_order"]["trial_count"] = True
    elif mutation == "oversized":
        payload["criteria"][0]["title"] = "x" * 4_097
    elif mutation == "invalid_digest":
        payload["criteria"][0]["candidate_policy"]["policy_sha256"] = "A" * 64
    elif mutation == "equal_policy_digest":
        payload["criteria"][0]["baseline_policy"]["policy_sha256"] = payload[
            "criteria"
        ][0]["candidate_policy"]["policy_sha256"]
    elif mutation == "nondeterministic_order":
        payload["criteria"][0]["trial_order"]["ordering_rule"] = "RANDOMIZED"
    elif mutation == "negative_age":
        payload["criteria"][0]["telemetry"]["max_age_seconds"] = -1
    elif mutation == "cache_contradiction":
        payload["criteria"][0]["cache_reset"]["cross_policy_cache_reuse"] = True
    elif mutation == "failure_contradiction":
        payload["criteria"][0]["failure_injection"]["maximum_injected_failures"] = 1
    else:
        payload["acceptance_verdict"] = "PASS"
    _expect_contract_code(payload, code)


def test_duplicate_fields_floats_and_unbounded_integers_are_rejected():
    duplicate = (
        b'{"id":"routing-qualification-campaign","id":"routing-qualification-campaign"}'
    )
    with pytest.raises(RoutingQualificationRejected) as caught:
        parse_routing_qualification_contract(duplicate)
    assert caught.value.code is RoutingQualificationValidationCode.DUPLICATE_FIELD

    float_payload = _contract_payload()
    float_payload["criteria"][0]["serving"]["tensor_parallel_size"] = 1.0
    _expect_contract_code(float_payload, RoutingQualificationValidationCode.WRONG_TYPE)

    huge_payload = _contract_payload()
    huge_payload["criteria"][0]["trial_order"]["trial_count"] = 2_147_483_648
    _expect_contract_code(huge_payload, RoutingQualificationValidationCode.OVERSIZED)


@pytest.mark.parametrize(
    "captured_at",
    ["2026-99-99T99:99:99Z", "2026-02-30T12:00:00Z", "2026-02-29T12:00:00Z"],
)
def test_captured_at_requires_a_real_utc_second_timestamp(captured_at: str):
    payload = _evidence_payload()
    payload["provenance"]["captured_at"] = captured_at
    with pytest.raises(RoutingQualificationRejected) as caught:
        parse_routing_qualification_evidence_fixture(payload)
    assert caught.value.code is RoutingQualificationValidationCode.INVALID_VALUE
    assert caught.value.path == "provenance.captured_at"


def test_mapping_input_applies_total_canonical_byte_limits_before_field_validation():
    contract_payload = _contract_payload()
    contract_payload["non_goals"] = ["x" * 300_000]
    _expect_contract_code(
        contract_payload, RoutingQualificationValidationCode.OVERSIZED
    )

    evidence_payload = _evidence_payload()
    evidence_payload["provenance"]["producer_version"] = "x" * 130_000
    with pytest.raises(RoutingQualificationRejected) as caught:
        parse_routing_qualification_evidence_fixture(evidence_payload)
    assert caught.value.code is RoutingQualificationValidationCode.OVERSIZED


@pytest.mark.parametrize(
    "field",
    [
        "acceptance_verdict",
        "producer_verdict",
        "verdict",
        "inferdrome_acceptance_verdict",
    ],
)
def test_producer_acceptance_verdict_aliases_are_forbidden(field: str):
    payload = _evidence_payload()
    payload[field] = "PASS"
    with pytest.raises(RoutingQualificationRejected) as caught:
        parse_routing_qualification_evidence_fixture(payload)
    assert (
        caught.value.code
        is RoutingQualificationValidationCode.PRODUCER_VERDICT_FORBIDDEN
    )


def test_nested_verdict_alias_precedes_other_errors_with_stable_path_order():
    first = _contract_payload()
    first["criteria"][0]["decision"] = "PASS"
    first["future_field"] = True
    second = _contract_payload()
    second["future_field"] = True
    second["criteria"][0]["decision"] = "PASS"

    for payload in (first, second):
        with pytest.raises(RoutingQualificationRejected) as caught:
            parse_routing_qualification_contract(payload)
        assert (
            caught.value.code
            is RoutingQualificationValidationCode.PRODUCER_VERDICT_FORBIDDEN
        )
        assert caught.value.path == "criteria[0].decision"


def test_generic_multi_error_precedence_and_path_are_insertion_order_independent():
    first = _contract_payload()
    first["criteria"][0]["schema_version"] = "exitspec.routing-qualification.v2"
    first["criteria"][0]["telemetry"]["max_age_seconds"] = -1
    second = _contract_payload()
    second["criteria"][0]["telemetry"]["max_age_seconds"] = -1
    second["criteria"][0]["schema_version"] = "exitspec.routing-qualification.v2"

    outcomes = []
    for payload in (first, second):
        with pytest.raises(RoutingQualificationRejected) as caught:
            parse_routing_qualification_contract(payload)
        outcomes.append((caught.value.code, caught.value.path))
    assert outcomes == [
        (
            RoutingQualificationValidationCode.INVALID_BOUND,
            "telemetry.max_age_seconds",
        ),
        (
            RoutingQualificationValidationCode.INVALID_BOUND,
            "telemetry.max_age_seconds",
        ),
    ]


def test_all_material_routing_fields_change_the_existing_outer_contract_hash():
    frozen = _frozen_poc_contract()
    original = frozen.canonical_hash
    mutations = []

    customer = _contract_payload()
    customer["customer"] = "changed"
    mutations.append(("customer", customer))

    criterion_type = _contract_payload()
    criterion_type["criteria"][0]["criterion_type"] = "changed"
    mutations.append(("criterion_type", criterion_type))

    policy_digest = _contract_payload()
    policy_digest["criteria"][0]["candidate_policy"]["policy_sha256"] = "1" * 64
    mutations.append(("policy_digest", policy_digest))

    trial_count = _contract_payload()
    trial_count["criteria"][0]["trial_order"]["trial_count"] = 3
    trial_count["criteria"][0]["trial_order"]["total_assignments"] = 24
    mutations.append(("trial_count", trial_count))

    cache_boundary = _contract_payload()
    cache_boundary["criteria"][0]["cache_reset"]["reset_boundary"] = "AFTER_EACH_TRIAL"
    mutations.append(("cache_boundary", cache_boundary))

    privacy = _contract_payload()
    privacy["criteria"][0]["privacy"]["secrets"] = "ALLOWED"
    mutations.append(("privacy", privacy))

    for name, mutated in mutations:
        assert _raw_contract_digest(mutated) != original, name


def test_typed_contract_bypasses_fail_before_digest_or_serialization():
    frozen = _frozen_poc_contract()
    stale = frozen.model_copy(update={"customer": "changed"})
    _expect_rejection(
        lambda: routing_qualification_contract_digest(stale),
        RoutingQualificationValidationCode.CONTRACT_BINDING_MISMATCH,
    )
    _expect_rejection(
        lambda: serialize_routing_qualification_contract(stale),
        RoutingQualificationValidationCode.CONTRACT_BINDING_MISMATCH,
    )

    with_extra = frozen.model_copy(update={"decision": "PASS"})
    _expect_rejection(
        lambda: routing_qualification_contract_digest(with_extra),
        RoutingQualificationValidationCode.PRODUCER_VERDICT_FORBIDDEN,
    )

    invalid_nested = frozen.model_copy(
        update={
            "criteria": (
                frozen.criteria[0].model_copy(
                    update={
                        "telemetry": frozen.criteria[0].telemetry.model_copy(
                            update={"max_age_seconds": -1}
                        )
                    }
                ),
            )
        }
    )
    _expect_rejection(
        lambda: routing_qualification_contract_digest(invalid_nested),
        RoutingQualificationValidationCode.INVALID_BOUND,
    )


def test_typed_evidence_bypasses_and_nested_invalid_copies_fail_closed():
    contract = _frozen_poc_contract()
    evidence = parse_routing_qualification_evidence_fixture(_evidence_payload())

    with_extra = evidence.model_copy(update={"decision": "PASS"})
    _expect_rejection(
        lambda: serialize_routing_qualification_evidence_fixture(with_extra),
        RoutingQualificationValidationCode.PRODUCER_VERDICT_FORBIDDEN,
    )
    real_status = evidence.model_copy(update={"fixture_status": "REAL"})
    _expect_rejection(
        lambda: serialize_routing_qualification_evidence_fixture(real_status),
        RoutingQualificationValidationCode.INVALID_VALUE,
    )
    invalid_provenance = evidence.model_copy(
        update={
            "provenance": evidence.provenance.model_copy(
                update={"captured_at": "2026-02-30T12:00:00Z"}
            )
        }
    )
    _expect_rejection(
        lambda: validate_routing_qualification_evidence_fixture(
            contract, invalid_provenance
        ),
        RoutingQualificationValidationCode.INVALID_VALUE,
    )
    incomplete = evidence.model_copy(
        update={"route_decision_receipt_ids": evidence.route_decision_receipt_ids[:1]}
    )
    _expect_rejection(
        lambda: validate_routing_qualification_evidence_fixture(contract, incomplete),
        RoutingQualificationValidationCode.SEMANTIC_INCONSISTENCY,
    )


def test_model_construct_missing_extra_and_invalid_fields_are_not_sanitized():
    contract = _frozen_poc_contract()
    evidence = parse_routing_qualification_evidence_fixture(_evidence_payload())

    raw = dict(evidence.__dict__)
    raw["fixture_status"] = "REAL"
    constructed = RoutingQualificationEvidenceFixtureV1.model_construct(**raw)
    _expect_rejection(
        lambda: serialize_routing_qualification_evidence_fixture(constructed),
        RoutingQualificationValidationCode.INVALID_VALUE,
    )

    missing = dict(evidence.__dict__)
    del missing["run_id"]
    constructed_missing = RoutingQualificationEvidenceFixtureV1.model_construct(
        **missing
    )
    _expect_rejection(
        lambda: serialize_routing_qualification_evidence_fixture(constructed_missing),
        RoutingQualificationValidationCode.MISSING_FIELD,
    )

    raw_contract = dict(contract.__dict__)
    constructed_contract = POCContract.model_construct(**raw_contract)
    constructed_contract.__dict__["acceptance_verdict"] = "PASS"
    _expect_rejection(
        lambda: routing_qualification_contract_digest(constructed_contract),
        RoutingQualificationValidationCode.PRODUCER_VERDICT_FORBIDDEN,
    )

    telemetry_type = type(contract.criteria[0].telemetry)
    raw_telemetry = dict(contract.criteria[0].telemetry.__dict__)
    raw_telemetry["max_age_seconds"] = -1
    constructed_telemetry = telemetry_type.model_construct(**raw_telemetry)
    bad_criterion = contract.criteria[0].model_copy(
        update={"telemetry": constructed_telemetry}
    )
    bad_contract = contract.model_copy(update={"criteria": (bad_criterion,)})
    _expect_rejection(
        lambda: routing_qualification_contract_digest(bad_contract),
        RoutingQualificationValidationCode.INVALID_BOUND,
    )


def test_recomputed_outer_hash_is_valid_only_when_revalidated_and_rebound():
    original = _frozen_poc_contract()
    changed_payload = _contract_payload()
    changed_payload["customer"] = "changed frozen customer"
    changed = _rehashed_contract(changed_payload)
    assert routing_qualification_contract_digest(changed) == changed.canonical_hash
    assert changed.canonical_hash != original.canonical_hash

    evidence = parse_routing_qualification_evidence_fixture(_evidence_payload())
    _expect_rejection(
        lambda: validate_routing_qualification_evidence_fixture(changed, evidence),
        RoutingQualificationValidationCode.CONTRACT_BINDING_MISMATCH,
    )


def test_unfrozen_or_unapproved_contracts_are_not_admissible():
    frozen = _frozen_poc_contract()
    approved = frozen.model_copy(
        update={
            "status": ContractStatus.APPROVED,
            "frozen_at": None,
            "canonical_hash": None,
        }
    )
    _expect_rejection(
        lambda: routing_qualification_contract_digest(approved),
        RoutingQualificationValidationCode.CONTRACT_BINDING_MISMATCH,
    )

    unapproved_payload = _contract_payload()
    unapproved_payload["criteria"][0]["approved"] = False
    unapproved_payload["canonical_hash"] = None
    unapproved = POCContract.model_construct(**unapproved_payload)
    _expect_rejection(
        lambda: routing_qualification_contract_digest(unapproved),
        RoutingQualificationValidationCode.WRONG_TYPE,
    )


def test_candidate_and_baseline_identity_digests_remain_distinct():
    frozen = _frozen_poc_contract()
    criterion = frozen.criteria[0]
    equal = frozen.model_copy(
        update={
            "criteria": (
                criterion.model_copy(
                    update={
                        "baseline_policy": criterion.candidate_policy,
                    }
                ),
            )
        }
    )
    _expect_rejection(
        lambda: routing_qualification_contract_digest(equal),
        RoutingQualificationValidationCode.SEMANTIC_INCONSISTENCY,
    )


def test_evidence_binding_rejects_criterion_only_and_repetition_overflow():
    criterion = _criterion()
    evidence = parse_routing_qualification_evidence_fixture(_evidence_payload())
    _expect_rejection(
        lambda: validate_routing_qualification_evidence_fixture(criterion, evidence),
        RoutingQualificationValidationCode.WRONG_TYPE,
    )

    frozen = _frozen_poc_contract()
    overflow = evidence.model_copy(update={"repetition_index": 3})
    _expect_rejection(
        lambda: validate_routing_qualification_evidence_fixture(frozen, overflow),
        RoutingQualificationValidationCode.INVALID_BOUND,
    )


def test_contract_rejects_source_content_at_the_privacy_boundary():
    frozen = _frozen_poc_contract()
    bad_criterion = frozen.criteria[0].model_copy(
        update={
            "source": {
                "location": "synthetic",
                "quote": "customer secret must not be retained",
                "speaker": "customer",
            }
        }
    )
    bad_contract = frozen.model_copy(update={"criteria": (bad_criterion,)})
    _expect_rejection(
        lambda: routing_qualification_contract_digest(bad_contract),
        RoutingQualificationValidationCode.WRONG_TYPE,
    )


def test_evidence_and_provenance_models_are_immutable():
    evidence = parse_routing_qualification_evidence_fixture(_evidence_payload())
    with pytest.raises(ValidationError, match="Instance is frozen"):
        evidence.run_id = "synthetic-routing-run-0002"
    with pytest.raises(ValidationError, match="Instance is frozen"):
        evidence.provenance.producer_id = "changed-producer"
    assert isinstance(evidence.provenance, RoutingEvidenceProvenanceV1)
