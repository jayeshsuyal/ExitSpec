"""Adversarial tests for the pure v0.5 PR5 proofability boundary."""

from __future__ import annotations

import ast
import hashlib
import json
import re
import subprocess
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import get_args

import pytest
from pydantic import ValidationError

import exitspec.proofability as proofability_module
from exitspec.canonical import canonical_json_bytes
from exitspec.contracts import freeze_contract, verify_contract_digest
from exitspec.models import (
    ContractCriterion,
    ContractStatus,
    InferenceQualificationCriterionV1,
    POCContract,
)
from exitspec.producer_capability import (
    DECLARED_EXTERNAL_EVIDENCE_PROFILE_ID,
    DECLARED_EXTERNAL_EVIDENCE_PROFILE_VERSION,
    EngineAdapterIdentityV1,
    NativeTTFTObservationV1,
    get_producer_capability_descriptor,
)
from exitspec.proofability import (
    PROOFABILITY_REPORT_DIGEST_DOMAIN,
    CriterionProofabilityDisposition,
    CriterionProofabilityV1,
    OverallProofabilityDisposition,
    ProofabilityRejected,
    ProofabilityReportV1,
    ProofabilityValidationCode,
    canonical_proofability_report_projection,
    evaluate_proofability,
    parse_proofability_report,
    proofability_report_digest,
    serialize_proofability_report,
    verify_proofability_report,
)
from exitspec.qualification_scope import (
    create_qualification_context,
    create_qualification_scope,
)
from exitspec.serving_subject import (
    create_serving_subject_manifest,
    parse_serving_subject_manifest,
)

ROOT = Path(__file__).resolve().parents[1]
SUBJECT_FIXTURE = ROOT / "tests" / "fixtures" / "serving_subject" / "v1" / "golden.json"
GOLDEN_FIXTURE = ROOT / "tests" / "fixtures" / "proofability" / "v1" / "golden.json"
ROUTING_CONTRACT_FIXTURES = (
    ROOT
    / "examples"
    / "routing-qualification"
    / "contracts"
    / "routing-qualification-v1.json",
    ROOT
    / "examples"
    / "routing-qualification"
    / "contracts"
    / "routing-slo-attainment-v1.synthetic.json",
    ROOT
    / "examples"
    / "routing-qualification"
    / "contracts"
    / "routing-campaign-reduction-v1.synthetic.json",
)
GOLDEN_DIGEST = "sha256:28c49bba2dd3791905a201a74777c9994e6ecc083cc3b9de083095f4c626d81e"
FROZEN_AT = datetime(2026, 8, 31, 9, 0, tzinfo=UTC)


def _criterion_payload(*, criterion_id: str = "QUAL-TTFT-01", semantic: bool = False) -> dict[str, object]:
    return {
        "criterion_type": "inference_qualification_v1",
        "schema_version": "exitspec.inference-qualification-criterion.v1",
        "protocol_id": "inference-performance-qualification",
        "protocol_version": "1.0.0",
        "id": criterion_id,
        "title": "Frozen TTFT qualification question",
        "must_have": True,
        "source": None,
        "human_added": True,
        "normalized_claim": "Bounded prospective native latency and reliability question.",
        "latency_requirement": {
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
        },
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


def _legacy_criterion_payload(*, criterion_id: str = "LEGACY-01") -> dict[str, object]:
    return {
        "id": criterion_id,
        "title": "Legacy opaque question",
        "must_have": True,
        "source": None,
        "human_added": True,
        "normalized_claim": "A legacy frozen criterion remains opaque to PR5.",
        "metric": "exact_tool_selection_rate",
        "unit": "proportion",
        "aggregation": "mean",
        "rule": {
            "operator": "gte",
            "threshold": 0.9,
            "minimum_samples": 10,
            "confidence_level": 0.95,
            "confidence_method": "wilson_two_sided_lower_bound",
        },
        "workload_slice": "legacy-workload",
        "adapter": "legacy-adapter",
        "adapter_version": "1.0.0",
        "owner": "owner",
        "evidence_policy": "legacy policy",
        "approved": True,
    }


def _contract(
    *criteria: dict[str, object],
    contract_id: str = "pr5-proofability-contract",
) -> POCContract:
    approved = POCContract.model_validate(
        {
            "id": contract_id,
            "version": "1.0.0",
            "status": "APPROVED",
            "created_at": FROZEN_AT,
            "approved_at": FROZEN_AT,
            "frozen_at": None,
            "customer": "customer",
            "use_case": "qualification planning",
            "target_system": {
                "provider": "declared-external-system",
                "endpoint_class": "external",
                "model": "model",
            },
            "workload": {
                "fixture_path": "not-read-by-pr5.json",
                "sha256": "1" * 64,
            },
            "criteria": list(criteria or (_criterion_payload(),)),
            "owners": ["owner"],
            "non_goals": ["No authority"],
            "evidence_retention_policy": "future protocol boundary",
            "parent_version": None,
            "confirmation_id": None,
            "canonical_hash": None,
        }
    )
    return freeze_contract(approved, FROZEN_AT)


def _subject():
    return parse_serving_subject_manifest(SUBJECT_FIXTURE.read_bytes())


def _different_valid_subject():
    return _subject_with_engine("vllm", "0.26.1")


def _subject_with_engine(engine_id: str, engine_version: str):
    payload = json.loads(SUBJECT_FIXTURE.read_bytes())
    del payload["subject_digest"]
    payload["engine"]["engine_id"] = engine_id
    payload["engine"]["engine_version"] = engine_version
    return create_serving_subject_manifest(payload)


def _scope(contract: POCContract, *, variant: bool = False):
    return create_qualification_scope(
        {
            "schema_version": "exitspec.qualification-scope.v1",
            "frozen_contract": {
                "contract_id": contract.id,
                "contract_canonical_digest": "sha256:" + contract.canonical_hash,
            },
            # These valid identities are deliberately independent from the
            # contract fixture path and registered capability profile.
            "workload": {
                "workload_id": "separate-workload-v2" if variant else "separate-workload-v1",
                "workload_digest": "sha256:" + ("3" * 64 if variant else "2" * 64),
            },
            "measurement_profile": {
                "environment_id": "separate-environment-v1",
                "environment_digest": "sha256:" + "4" * 64,
                "profile_id": "separate-profile-v2" if variant else "separate-profile-v1",
                "profile_version": "2.0.0" if variant else "1.0.0",
                "profile_digest": "sha256:" + ("6" * 64 if variant else "5" * 64),
            },
            "evaluated_use": "CANARY_CONSIDERATION",
            "maximum_use": {"maximum_traffic_percent": 5},
            "freshness_policy": {
                "age_basis": "EVIDENCE_CAPTURED_AT",
                "maximum_evidence_age_seconds": 86_400,
            },
            "reference_subject_requirement": "NOT_REQUIRED",
            "reference_subject_digest": None,
        }
    )


def _bound_inputs(
    *criteria: dict[str, object], variant_scope: bool = False
) -> tuple[object, object, object, POCContract, object]:
    subject = _subject()
    contract = _contract(*criteria)
    scope = _scope(contract, variant=variant_scope)
    context = create_qualification_context(
        subject,
        scope,
        protocol_id="inference-performance-qualification",
        protocol_version="1.0.0",
    )
    descriptor = get_producer_capability_descriptor(
        profile_id=DECLARED_EXTERNAL_EVIDENCE_PROFILE_ID,
        profile_version=DECLARED_EXTERNAL_EVIDENCE_PROFILE_VERSION,
    )
    return subject, scope, context, contract, descriptor


def _inputs_for_frozen_contract(
    contract: POCContract,
) -> tuple[object, object, object, POCContract, object]:
    subject = _subject()
    scope = _scope(contract)
    context = create_qualification_context(
        subject,
        scope,
        protocol_id="inference-performance-qualification",
        protocol_version="1.0.0",
    )
    descriptor = get_producer_capability_descriptor(
        profile_id=DECLARED_EXTERNAL_EVIDENCE_PROFILE_ID,
        profile_version=DECLARED_EXTERNAL_EVIDENCE_PROFILE_VERSION,
    )
    return subject, scope, context, contract, descriptor


def _report(*criteria: dict[str, object], variant_scope: bool = False):
    inputs = _bound_inputs(*criteria, variant_scope=variant_scope)
    return evaluate_proofability(*inputs), inputs


def _rehash(payload: dict[str, object]) -> dict[str, object]:
    unsigned = {
        key: value for key, value in payload.items() if key != "proofability_report_digest"
    }
    payload["proofability_report_digest"] = "sha256:" + hashlib.sha256(
        b"exitspec-proofability-report-v1\x00" + canonical_json_bytes(unsigned)
    ).hexdigest()
    return payload


def _stdlib_canonical_bytes(payload: dict[str, object]) -> bytes:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _stdlib_rehash_bytes(payload: dict[str, object]) -> bytes:
    unsigned = {
        key: value for key, value in payload.items() if key != "proofability_report_digest"
    }
    payload["proofability_report_digest"] = "sha256:" + hashlib.sha256(
        b"exitspec-proofability-report-v1\x00" + _stdlib_canonical_bytes(unsigned)
    ).hexdigest()
    return _stdlib_canonical_bytes(payload)


def _semantic_observation_payload() -> dict[str, object]:
    return {
        "observation_kind": "SEMANTIC_FIRST_NONEMPTY_TTFT",
        "observation_id": "semantic_first_nonempty_ttft_sample",
        "metric_definition_id": "first_nonempty_choices_delta_content_v1",
        "source_field": "response.choices[].delta.content",
        "unit": "ns",
        "population": "successful_measured_requests_with_observed_ttft",
        "reducer_id": "nearest_rank_v1",
        "percentile": "p95",
    }


def _closure_criteria(kind: str) -> tuple[dict[str, object], ...]:
    criteria: list[dict[str, object]] = []
    for index in range(64):
        arm = kind if kind != "mixed" else ("native", "semantic", "legacy")[index % 3]
        criterion_id = f"Q{index:02d}-{arm[0].upper()}" + "X" * 59
        assert len(criterion_id) == 64
        if arm == "legacy":
            criterion = _legacy_criterion_payload(criterion_id=criterion_id)
        else:
            criterion = _criterion_payload(
                criterion_id=criterion_id,
                semantic=arm == "semantic",
            )
        criteria.append(criterion)
    return tuple(criteria)


def _json_node_count(value: object) -> int:
    if type(value) is dict:
        return 1 + sum(_json_node_count(child) for child in value.values())
    if type(value) is list:
        return 1 + sum(_json_node_count(child) for child in value)
    return 1


def _mts_semantic_report_payload() -> tuple[
    dict[str, object],
    dict[str, object],
    dict[str, object],
    dict[str, object],
    dict[str, object],
]:
    payload = json.loads(GOLDEN_FIXTURE.read_bytes())
    result = payload["criterion_results"][0]
    semantic = _semantic_observation_payload()
    reliability = result["required_observations"][0]
    native = result["required_observations"][1]
    result["disposition"] = "NOT_PROVABLE"
    result["required_observations"][1] = semantic
    result["missing_observations"] = []
    result["incompatible_observations"] = []
    result["reason_codes"] = ["MISSING_OBSERVATION"]
    result["remediation_codes"] = ["DECLARE_REQUIRED_OBSERVATION"]
    payload["overall_disposition"] = "NOT_PROVABLE"
    return payload, result, semantic, reliability, native


def _assert_rejected(action: object, code: ProofabilityValidationCode) -> ProofabilityRejected:
    with pytest.raises(ProofabilityRejected) as raised:
        assert callable(action)
        action()
    assert raised.value.code is code
    return raised.value


def test_native_frozen_criterion_is_provable_and_report_is_context_bound():
    report, inputs = _report()

    assert report.criterion_results[0].disposition is CriterionProofabilityDisposition.PROVABLE
    assert report.overall_disposition is OverallProofabilityDisposition.PROVABLE
    assert report.criterion_results[0].missing_observations == ()
    assert report.criterion_results[0].incompatible_observations == ()
    assert verify_proofability_report(report, *inputs)
    assert "not-read-by-pr5.json" not in serialize_proofability_report(report).decode()


def test_semantic_first_nonempty_is_missing_not_native_substitution():
    report, _ = _report(_criterion_payload(semantic=True))
    result = report.criterion_results[0]

    assert result.disposition is CriterionProofabilityDisposition.NOT_PROVABLE
    assert result.reason_codes[0].value == "MISSING_OBSERVATION"
    assert result.missing_observations[0].metric_definition_id == (
        "first_nonempty_choices_delta_content_v1"
    )
    assert any(
        item.metric_definition_id == "vllm_first_choices_event_v0_26"
        for item in result.available_observations
        if item.observation_kind == "NATIVE_TTFT"
    )


def test_legacy_criteria_are_opaque_clarification_and_order_is_preserved():
    native = _criterion_payload(criterion_id="QUAL-NATIVE-01")
    legacy = _legacy_criterion_payload()
    semantic = _criterion_payload(criterion_id="QUAL-SEMANTIC-01", semantic=True)
    report, _ = _report(native, legacy, semantic)

    assert [item.criterion_id for item in report.criterion_results] == [
        "QUAL-NATIVE-01",
        "LEGACY-01",
        "QUAL-SEMANTIC-01",
    ]
    assert report.criterion_results[1].disposition is (
        CriterionProofabilityDisposition.CLARIFICATION_REQUIRED
    )
    assert report.criterion_results[1].reason_codes[0].value == (
        "UNMAPPABLE_FROZEN_CRITERION_SCHEMA"
    )
    assert report.overall_disposition is OverallProofabilityDisposition.PARTIALLY_PROVABLE


@pytest.mark.parametrize(
    "fixture_path", ROUTING_CONTRACT_FIXTURES, ids=lambda path: path.stem
)
def test_every_lowercase_routing_legacy_arm_round_trips_as_clarification(
    fixture_path: Path,
):
    contract = POCContract.model_validate_json(fixture_path.read_bytes(), strict=True)
    assert contract.status is ContractStatus.FROZEN
    assert verify_contract_digest(contract)
    inputs = _inputs_for_frozen_contract(contract)

    report = evaluate_proofability(*inputs)
    serialized = serialize_proofability_report(report)
    parsed = parse_proofability_report(serialized)

    assert tuple(result.criterion_id for result in parsed.criterion_results) == tuple(
        criterion.id for criterion in contract.criteria
    )
    assert parsed.overall_disposition is (
        OverallProofabilityDisposition.CLARIFICATION_REQUIRED
    )
    for result in parsed.criterion_results:
        assert result.disposition is (
            CriterionProofabilityDisposition.CLARIFICATION_REQUIRED
        )
        assert result.required_observations == ()
        assert tuple(
            (observation.observation_kind, observation.observation_id)
            for observation in result.available_observations
        ) == (
            ("MEASURED_ATTEMPT_RELIABILITY", "native_measured_request_outcome"),
            ("NATIVE_TTFT", "native_ttft_sample"),
        )
        assert result.missing_observations == ()
        assert result.incompatible_observations == ()
        assert tuple(code.value for code in result.reason_codes) == (
            "UNMAPPABLE_FROZEN_CRITERION_SCHEMA",
        )
        assert tuple(code.value for code in result.remediation_codes) == (
            "FREEZE_PROVIDER_NEUTRAL_CRITERION_SCHEMA",
        )
    assert verify_proofability_report(parsed, *inputs)


def test_report_criterion_id_language_covers_the_complete_contract_union():
    report_pattern = CriterionProofabilityV1.model_json_schema()["properties"][
        "criterion_id"
    ]["pattern"]
    uppercase_pattern = r"^[A-Z][A-Z0-9-]{2,63}$"
    lowercase_literals: set[str] = set()

    for criterion_arm in get_args(ContractCriterion):
        id_schema = criterion_arm.model_json_schema()["properties"]["id"]
        if "const" in id_schema:
            lowercase_literals.add(id_schema["const"])
            assert re.fullmatch(report_pattern, id_schema["const"])
        else:
            assert id_schema["pattern"] == uppercase_pattern

    assert lowercase_literals == {
        "routing_qualification_v1",
        "routing_slo_attainment_v1",
        "routing_campaign_reduction_v1",
    }
    assert re.fullmatch(report_pattern, "ABC")
    assert re.fullmatch(report_pattern, "A" * 64)


def test_nonprovable_and_clarification_precedence_is_deterministic():
    report, _ = _report(
        _legacy_criterion_payload(), _criterion_payload(semantic=True)
    )
    assert report.overall_disposition is OverallProofabilityDisposition.NOT_PROVABLE


def test_scope_workload_and_profile_remain_independent_material_identity():
    base, _ = _report()
    changed, _ = _report(variant_scope=True)

    assert base.scope_digest != changed.scope_digest
    assert base.qualification_context_digest != changed.qualification_context_digest
    assert base.proofability_report_digest != changed.proofability_report_digest
    assert base.overall_disposition is changed.overall_disposition


def test_subject_engine_must_exactly_match_registered_capability_engine():
    native_report, native_inputs = _report()
    _, scope, _, contract, descriptor = native_inputs
    subject = _subject_with_engine("tgi", "1.2.3")
    context = create_qualification_context(
        subject,
        scope,
        protocol_id="inference-performance-qualification",
        protocol_version="1.0.0",
    )

    error = _assert_rejected(
        lambda: evaluate_proofability(subject, scope, context, contract, descriptor),
        ProofabilityValidationCode.CAPABILITY_BINDING_MISMATCH,
    )
    assert "tgi" not in str(error)
    assert "1.2.3" not in str(error)
    assert not verify_proofability_report(
        native_report,
        subject,
        scope,
        context,
        contract,
        descriptor,
    )


def test_golden_raw_bytes_are_exact_jcs_and_independently_hashed():
    report, _ = _report()
    raw = GOLDEN_FIXTURE.read_bytes()
    payload = json.loads(raw)
    unsigned = {key: value for key, value in payload.items() if key != "proofability_report_digest"}
    stdlib_unsigned = _stdlib_canonical_bytes(unsigned)
    independently_derived = "sha256:" + hashlib.sha256(
        b"exitspec-proofability-report-v1\x00" + stdlib_unsigned
    ).hexdigest()

    assert raw == serialize_proofability_report(report)
    assert raw == canonical_json_bytes(payload)
    assert raw == _stdlib_canonical_bytes(payload)
    assert not raw.endswith(b"\n")
    assert independently_derived == GOLDEN_DIGEST == report.proofability_report_digest
    assert parse_proofability_report(raw) == report


@pytest.mark.parametrize(
    ("path", "replacement"),
    [
        (("subject_digest",), "sha256:" + "a" * 64),
        (("scope_digest",), "sha256:" + "b" * 64),
        (("qualification_context_digest",), "sha256:" + "c" * 64),
        (("contract_id",), "other-contract"),
        (("contract_canonical_digest",), "sha256:" + "d" * 64),
        (("capability_digest",), "sha256:" + "e" * 64),
        (("profile_id",), "other-profile"),
        (("profile_version",), "v2"),
        (("engine_id",), "other-engine"),
        (("engine_version",), "0.27.0"),
        (("adapter_id",), "other-adapter"),
        (("adapter_version",), "2.0.0"),
        (("criterion_results", 0, "criterion_id"), "QUAL-TTFT-02"),
    ],
)
def test_valid_report_identity_leaf_mutations_change_digest(
    path: tuple[object, ...], replacement: object
):
    report, _ = _report()
    payload = report.model_dump(mode="json")
    unsigned = canonical_proofability_report_projection(report)
    current: object = unsigned
    for key in path[:-1]:
        current = current[key]  # type: ignore[index]
    current[path[-1]] = replacement  # type: ignore[index]
    assert "sha256:" + hashlib.sha256(
        PROOFABILITY_REPORT_DIGEST_DOMAIN + canonical_json_bytes(unsigned)
    ).hexdigest() != report.proofability_report_digest
    assert payload["proofability_report_digest"] == report.proofability_report_digest


def test_self_consistent_hostile_report_parses_but_never_verifies_against_inputs():
    native_report, native_inputs = _report()
    semantic_report, _ = _report(_criterion_payload(semantic=True))
    hostile = json.loads(serialize_proofability_report(semantic_report))
    parsed = parse_proofability_report(canonical_json_bytes(_rehash(hostile)))

    assert parsed.proofability_report_digest != native_report.proofability_report_digest
    assert not verify_proofability_report(parsed, *native_inputs)


def test_criterion_result_semantics_reject_self_consistent_contradictions():
    native_report, _ = _report()
    native_payload = json.loads(serialize_proofability_report(native_report))
    native_result = native_payload["criterion_results"][0]

    provable_with_missing = json.loads(json.dumps(native_payload))
    provable_with_missing["criterion_results"][0]["missing_observations"] = [
        native_result["required_observations"][0]
    ]

    clarification_with_material = json.loads(json.dumps(native_payload))
    clarification_result = clarification_with_material["criterion_results"][0]
    clarification_result["disposition"] = "CLARIFICATION_REQUIRED"
    clarification_result["reason_codes"] = ["UNMAPPABLE_FROZEN_CRITERION_SCHEMA"]
    clarification_result["remediation_codes"] = [
        "FREEZE_PROVIDER_NEUTRAL_CRITERION_SCHEMA"
    ]
    clarification_with_material["overall_disposition"] = "CLARIFICATION_REQUIRED"

    semantic_report, _ = _report(_criterion_payload(semantic=True))
    semantic_payload = json.loads(serialize_proofability_report(semantic_report))
    semantic_result = semantic_payload["criterion_results"][0]
    semantic_result["reason_codes"] = ["ALL_REQUIRED_OBSERVATIONS_AVAILABLE"]
    semantic_result["remediation_codes"] = ["NO_REMEDIATION_REQUIRED"]

    for payload in (
        provable_with_missing,
        clarification_with_material,
        semantic_payload,
    ):
        _assert_rejected(
            lambda payload=payload: parse_proofability_report(
                canonical_json_bytes(_rehash(payload))
            ),
            ProofabilityValidationCode.SEMANTIC_INCONSISTENCY,
        )


def test_mts_p1_a_omitted_required_observation_digest_rejects():
    payload = json.loads(GOLDEN_FIXTURE.read_bytes())
    result = payload["criterion_results"][0]
    result["disposition"] = "NOT_PROVABLE"
    result["available_observations"] = []
    result["missing_observations"] = [result["required_observations"][0]]
    result["incompatible_observations"] = []
    result["reason_codes"] = ["MISSING_OBSERVATION"]
    result["remediation_codes"] = ["DECLARE_REQUIRED_OBSERVATION"]
    payload["overall_disposition"] = "NOT_PROVABLE"
    raw = _stdlib_rehash_bytes(payload)

    assert payload["proofability_report_digest"] == (
        "sha256:c01606ff8770340309ad7a74d06e9018ba344ee920cbe24d910307e1384ec44f"
    )
    _assert_rejected(
        lambda: parse_proofability_report(raw),
        ProofabilityValidationCode.SEMANTIC_INCONSISTENCY,
    )


def test_mts_p1_b_double_classified_semantic_digest_rejects():
    payload, result, semantic, _, native = _mts_semantic_report_payload()
    result["missing_observations"] = [semantic]
    result["incompatible_observations"] = [
        {
            "required_observation": semantic,
            "available_observation": native,
            "reason_code": "INCOMPATIBLE_METRIC_DEFINITION",
        }
    ]
    result["reason_codes"] = [
        "INCOMPATIBLE_METRIC_DEFINITION",
        "MISSING_OBSERVATION",
    ]
    raw = _stdlib_rehash_bytes(payload)

    assert payload["proofability_report_digest"] == (
        "sha256:1116305d43582e265bee40079de622f3892b2b95a4b74109ac9a2b0b1b2b6b03"
    )
    _assert_rejected(
        lambda: parse_proofability_report(raw),
        ProofabilityValidationCode.SEMANTIC_INCONSISTENCY,
    )


def test_complete_reason_set_still_rejects_double_classification():
    payload, result, semantic, _, native = _mts_semantic_report_payload()
    result["missing_observations"] = [semantic]
    result["incompatible_observations"] = [
        {
            "required_observation": semantic,
            "available_observation": native,
            "reason_code": "INCOMPATIBLE_METRIC_DEFINITION",
        },
        {
            "required_observation": semantic,
            "available_observation": native,
            "reason_code": "INCOMPATIBLE_SOURCE_FIELD",
        },
    ]
    result["reason_codes"] = [
        "INCOMPATIBLE_METRIC_DEFINITION",
        "INCOMPATIBLE_SOURCE_FIELD",
        "MISSING_OBSERVATION",
    ]
    raw = _stdlib_rehash_bytes(payload)

    assert payload["proofability_report_digest"] == (
        "sha256:5337d34f3973add09ab8213fe6ab9ed29edf5ffe564418bb857122bba058bd65"
    )
    _assert_rejected(
        lambda: parse_proofability_report(raw),
        ProofabilityValidationCode.SEMANTIC_INCONSISTENCY,
    )


def test_exact_available_required_cannot_also_be_incompatible():
    payload, result, semantic, reliability, native = _mts_semantic_report_payload()
    result["missing_observations"] = [semantic]
    result["incompatible_observations"] = [
        {
            "required_observation": reliability,
            "available_observation": native,
            "reason_code": "INCOMPATIBLE_RELIABILITY_BINDING",
        }
    ]
    result["reason_codes"] = [
        "INCOMPATIBLE_RELIABILITY_BINDING",
        "MISSING_OBSERVATION",
    ]
    raw = _stdlib_rehash_bytes(payload)

    assert payload["proofability_report_digest"] == (
        "sha256:577846e372ff2ade7c05a3472c308c96b7c6157aaae9bae16db0fe39077bb28c"
    )
    _assert_rejected(
        lambda: parse_proofability_report(raw),
        ProofabilityValidationCode.SEMANTIC_INCONSISTENCY,
    )


def test_incompatible_pair_requires_complete_actual_reason_set():
    payload, result, semantic, _, native = _mts_semantic_report_payload()
    result["incompatible_observations"] = [
        {
            "required_observation": semantic,
            "available_observation": native,
            "reason_code": "INCOMPATIBLE_METRIC_DEFINITION",
        }
    ]
    result["reason_codes"] = ["INCOMPATIBLE_METRIC_DEFINITION"]
    raw = _stdlib_rehash_bytes(payload)

    assert payload["proofability_report_digest"] == (
        "sha256:5401528ac536f3cf064f28d69d1e129d79ebd8a48861c45e86aae1772baa751e"
    )
    _assert_rejected(
        lambda: parse_proofability_report(raw),
        ProofabilityValidationCode.SEMANTIC_INCONSISTENCY,
    )


def test_coherent_complete_incompatible_report_parses_but_is_input_bound():
    payload, result, semantic, _, native = _mts_semantic_report_payload()
    result["incompatible_observations"] = [
        {
            "required_observation": semantic,
            "available_observation": native,
            "reason_code": "INCOMPATIBLE_METRIC_DEFINITION",
        },
        {
            "required_observation": semantic,
            "available_observation": native,
            "reason_code": "INCOMPATIBLE_SOURCE_FIELD",
        },
    ]
    result["reason_codes"] = [
        "INCOMPATIBLE_METRIC_DEFINITION",
        "INCOMPATIBLE_SOURCE_FIELD",
    ]
    raw = _stdlib_rehash_bytes(payload)

    assert payload["proofability_report_digest"] == (
        "sha256:afd6ef64a481f78a99c25135470acc2aa0ba5cee5a9055c3b34a20c73876babf"
    )
    parsed = parse_proofability_report(raw)

    semantic_report, semantic_inputs = _report(_criterion_payload(semantic=True))
    same_context = json.loads(serialize_proofability_report(semantic_report))
    same_context["criterion_results"][0] = parsed.criterion_results[0].model_dump(
        mode="json"
    )
    same_context_raw = _stdlib_rehash_bytes(same_context)
    same_context_parsed = parse_proofability_report(same_context_raw)
    assert same_context_parsed.subject_digest == semantic_report.subject_digest
    assert same_context_parsed.scope_digest == semantic_report.scope_digest
    assert (
        same_context_parsed.qualification_context_digest
        == semantic_report.qualification_context_digest
    )
    assert (
        same_context_parsed.contract_canonical_digest
        == semantic_report.contract_canonical_digest
    )
    assert same_context_parsed.criterion_results != semantic_report.criterion_results
    assert not verify_proofability_report(same_context_parsed, *semantic_inputs)

    duplicate = json.loads(raw)
    duplicate_result = duplicate["criterion_results"][0]
    duplicate_result["incompatible_observations"].append(
        duplicate_result["incompatible_observations"][0]
    )
    _assert_rejected(
        lambda: parse_proofability_report(_stdlib_rehash_bytes(duplicate)),
        ProofabilityValidationCode.SEMANTIC_INCONSISTENCY,
    )

    reordered = json.loads(raw)
    reordered["criterion_results"][0]["incompatible_observations"].reverse()
    _assert_rejected(
        lambda: parse_proofability_report(_stdlib_rehash_bytes(reordered)),
        ProofabilityValidationCode.SEMANTIC_INCONSISTENCY,
    )


@pytest.mark.parametrize(
    "content, code",
    [
        (b'{"schema_version":"x","schema_version":"x"}', ProofabilityValidationCode.DUPLICATE_FIELD),
        (b"\xef\xbb\xbf{}", ProofabilityValidationCode.WRONG_TYPE),
        (b"[]", ProofabilityValidationCode.WRONG_TYPE),
        (b'{"proofability_report_digest":1}', ProofabilityValidationCode.MISSING_FIELD),
        (b'{"x":1.0}', ProofabilityValidationCode.INVALID_VALUE),
        (b"{", ProofabilityValidationCode.WRONG_TYPE),
    ],
)
def test_parser_rejects_noncanonical_and_malformed_inputs(
    content: bytes, code: ProofabilityValidationCode
):
    _assert_rejected(lambda: parse_proofability_report(content), code)


def test_parser_rejects_mapping_cycles_and_bounds_without_echoing_attack_content():
    cyclic: dict[str, object] = {}
    cyclic["loop"] = cyclic
    error = _assert_rejected(
        lambda: parse_proofability_report(cyclic), ProofabilityValidationCode.INVALID_VALUE
    )
    assert "loop" not in str(error)
    oversized = {"x": "x" * 513}
    _assert_rejected(
        lambda: parse_proofability_report(oversized), ProofabilityValidationCode.OVERSIZED
    )


def _deep_mapping(depth: int) -> dict[str, object]:
    value: object = 0
    for _ in range(depth):
        value = {"nested": value}
    assert type(value) is dict
    return value


@pytest.mark.parametrize(
    "value",
    [
        _deep_mapping(21),
        {"items": [0] * 129},
        {str(index): 0 for index in range(49)},
        {"integer": 2_147_483_648},
        {"string": "x" * 513},
    ],
)
def test_mapping_input_bounds_reject_before_canonicalization(value: dict[str, object]):
    _assert_rejected(
        lambda: parse_proofability_report(value), ProofabilityValidationCode.OVERSIZED
    )


def test_raw_byte_bounds_and_noncanonical_forms_are_fail_closed():
    report, _ = _report()
    raw = serialize_proofability_report(report)
    for content in (
        b" " + raw,
        json.dumps(report.model_dump(mode="json")).encode("utf-8"),
        raw.replace(b"vllm", b"\\u0076llm", 1),
        b"{" + b"x" * proofability_module._MAX_REPORT_BYTES,
    ):
        with pytest.raises(ProofabilityRejected) as raised:
            parse_proofability_report(content)
        assert raised.value.code in {
            ProofabilityValidationCode.NON_CANONICAL,
            ProofabilityValidationCode.OVERSIZED,
        }


def test_report_byte_and_node_caps_are_exact_finite_and_fail_closed():
    assert proofability_module._MAX_REPORT_BYTES == 1_048_576
    assert proofability_module._MAX_JSON_NODES == 16_384

    exact_byte_limit = b"{" + b"x" * (proofability_module._MAX_REPORT_BYTES - 1)
    assert len(exact_byte_limit) == proofability_module._MAX_REPORT_BYTES
    _assert_rejected(
        lambda: parse_proofability_report(exact_byte_limit),
        ProofabilityValidationCode.WRONG_TYPE,
    )

    one_byte_over = exact_byte_limit + b"x"
    assert len(one_byte_over) == proofability_module._MAX_REPORT_BYTES + 1
    _assert_rejected(
        lambda: parse_proofability_report(one_byte_over),
        ProofabilityValidationCode.OVERSIZED,
    )

    node_overflow = {"items": [[0] * 128 for _ in range(128)]}
    assert _json_node_count(node_overflow) == 16_514
    _assert_rejected(
        lambda: parse_proofability_report(node_overflow),
        ProofabilityValidationCode.OVERSIZED,
    )


@pytest.mark.parametrize(
    ("kind", "expected_nodes", "expected_bytes"),
    [
        ("native", 2_773, 102_194),
        ("semantic", 3_349, 127_286),
        ("legacy", 1_749, 63_936),
        ("mixed", 2_626, 97_878),
    ],
)
def test_all_declared_64_result_evaluator_outputs_are_closed(
    kind: str, expected_nodes: int, expected_bytes: int
):
    inputs = _bound_inputs(*_closure_criteria(kind))
    report = evaluate_proofability(*inputs)
    raw = serialize_proofability_report(report)
    parsed = parse_proofability_report(raw)

    assert len(report.criterion_results) == 64
    assert _json_node_count(report.model_dump(mode="json")) == expected_nodes
    assert expected_nodes <= 3_349 < proofability_module._MAX_JSON_NODES
    assert len(raw) == expected_bytes
    assert len(raw) < proofability_module._MAX_REPORT_BYTES
    assert parsed == report
    assert verify_proofability_report(report, *inputs)


def test_mapping_integer_boundary_is_checked_before_canonicalization():
    report, _ = _report()
    payload = report.model_dump(mode="json")
    payload["bounded_integer"] = 2_147_483_647
    _assert_rejected(
        lambda: parse_proofability_report(payload), ProofabilityValidationCode.EXTRA_FIELD
    )
    payload["bounded_integer"] = 2_147_483_648
    _assert_rejected(
        lambda: parse_proofability_report(payload), ProofabilityValidationCode.OVERSIZED
    )


def test_binding_rejections_are_exact_content_safe_and_never_issue_a_report():
    subject, scope, context, contract, descriptor = _bound_inputs()
    attack = "DO-NOT-ECHO"
    other_subject = _different_valid_subject()
    other_context = create_qualification_context(
        other_subject,
        scope,
        protocol_id="inference-performance-qualification",
        protocol_version="1.0.0",
    )
    wrong_protocol = create_qualification_context(
        subject, scope, protocol_id="different-protocol", protocol_version="1.0.0"
    )
    wrong_version = create_qualification_context(
        subject,
        scope,
        protocol_id="inference-performance-qualification",
        protocol_version="1.0.1",
    )
    bad_context_digest = context.model_construct(
        **{
            **context.model_dump(mode="python"),
            "qualification_context_digest": "sha256:" + "f" * 64,
        }
    )
    unfrozen = contract.model_copy(update={"status": ContractStatus.APPROVED})
    other_contract = _contract(contract_id="other-proofability-contract")
    other_contract_scope = _scope(other_contract)
    other_contract_context = create_qualification_context(
        subject,
        other_contract_scope,
        protocol_id="inference-performance-qualification",
        protocol_version="1.0.0",
    )
    bad_hash = contract.model_construct(
        **{**contract.model_dump(mode="python"), "canonical_hash": "f" * 64}
    )
    changed_adapter = descriptor.model_copy(
        update={
            "engine_adapter": EngineAdapterIdentityV1.model_construct(
                **{
                    **descriptor.engine_adapter.model_dump(mode="python"),
                    "adapter_id": "other-adapter",
                }
            )
        }
    )
    changed_profile = get_producer_capability_descriptor(
        profile_id=DECLARED_EXTERNAL_EVIDENCE_PROFILE_ID,
        profile_version=DECLARED_EXTERNAL_EVIDENCE_PROFILE_VERSION,
    )
    object.__getattribute__(changed_profile.profile, "__dict__")["profile_id"] = (
        "other-profile"
    )
    changed_observation = get_producer_capability_descriptor(
        profile_id=DECLARED_EXTERNAL_EVIDENCE_PROFILE_ID,
        profile_version=DECLARED_EXTERNAL_EVIDENCE_PROFILE_VERSION,
    )
    object.__getattribute__(
        changed_observation.available_observations.native_ttft, "__dict__"
    )["source_field"] = "other.field"

    cases = (
        (("wrong-subject", scope, context, contract, descriptor), ProofabilityValidationCode.WRONG_TYPE),
        ((subject, "wrong-scope", context, contract, descriptor), ProofabilityValidationCode.WRONG_TYPE),
        ((subject, scope, "wrong-context", contract, descriptor), ProofabilityValidationCode.WRONG_TYPE),
        ((subject, scope, context, "wrong-contract", descriptor), ProofabilityValidationCode.WRONG_TYPE),
        ((subject, scope, context, contract, "wrong-descriptor"), ProofabilityValidationCode.WRONG_TYPE),
        ((subject, scope, other_context, contract, descriptor), ProofabilityValidationCode.CONTEXT_BINDING_MISMATCH),
        ((subject, scope, wrong_protocol, contract, descriptor), ProofabilityValidationCode.CONTEXT_BINDING_MISMATCH),
        ((subject, scope, wrong_version, contract, descriptor), ProofabilityValidationCode.CONTEXT_BINDING_MISMATCH),
        ((subject, scope, bad_context_digest, contract, descriptor), ProofabilityValidationCode.CONTEXT_BINDING_MISMATCH),
        ((subject, scope, context, unfrozen, descriptor), ProofabilityValidationCode.CONTRACT_BINDING_MISMATCH),
        ((subject, other_contract_scope, other_contract_context, contract, descriptor), ProofabilityValidationCode.CONTRACT_BINDING_MISMATCH),
        ((subject, scope, context, bad_hash, descriptor), ProofabilityValidationCode.CONTRACT_BINDING_MISMATCH),
        ((subject, scope, context, contract, changed_adapter), ProofabilityValidationCode.CAPABILITY_BINDING_MISMATCH),
        ((subject, scope, context, contract, changed_profile), ProofabilityValidationCode.CAPABILITY_BINDING_MISMATCH),
        ((subject, scope, context, contract, changed_observation), ProofabilityValidationCode.CAPABILITY_BINDING_MISMATCH),
    )
    for arguments, code in cases:
        error = _assert_rejected(
            lambda arguments=arguments: evaluate_proofability(*arguments), code
        )
        assert attack not in str(error)


def test_public_boundaries_reject_hidden_state_subclasses_and_construct_bypasses():
    report, inputs = _report()
    attack = "DO-NOT-ECHO"
    hidden = report.model_copy()
    object.__getattribute__(hidden, "__dict__")["forged"] = attack
    assert not verify_proofability_report(hidden, *inputs)
    error = _assert_rejected(
        lambda: serialize_proofability_report(hidden), ProofabilityValidationCode.EXTRA_FIELD
    )
    assert attack not in str(error)

    class ExactFieldSubclass(ProofabilityReportV1):
        @property
        def forged(self) -> str:
            return attack

    subclass = ExactFieldSubclass.model_validate(report.model_dump(mode="python"))
    assert not verify_proofability_report(subclass, *inputs)
    _assert_rejected(
        lambda: canonical_proofability_report_projection(subclass),
        ProofabilityValidationCode.WRONG_TYPE,
    )
    constructed = ProofabilityReportV1.model_construct(
        **{**report.model_dump(mode="python"), "criterion_results": []}
    )
    assert not verify_proofability_report(constructed, *inputs)
    _assert_rejected(
        lambda: proofability_report_digest(constructed), ProofabilityValidationCode.WRONG_TYPE
    )


def _assert_all_typed_report_boundaries_reject(
    report: ProofabilityReportV1,
    inputs: tuple[object, object, object, POCContract, object],
    code: ProofabilityValidationCode,
) -> None:
    for action in (
        lambda: serialize_proofability_report(report),
        lambda: canonical_proofability_report_projection(report),
        lambda: proofability_report_digest(report),
    ):
        error = _assert_rejected(action, code)
        assert "DO-NOT-ECHO" not in str(error)
    assert not verify_proofability_report(report, *inputs)


@pytest.mark.parametrize(
    ("slot", "state_kind", "code"),
    [
        ("__pydantic_private__", "nonempty", ProofabilityValidationCode.EXTRA_FIELD),
        ("__pydantic_private__", "empty", ProofabilityValidationCode.INVALID_VALUE),
        ("__pydantic_private__", "malformed", ProofabilityValidationCode.EXTRA_FIELD),
        ("__pydantic_fields_set__", "altered", ProofabilityValidationCode.INVALID_VALUE),
        ("__pydantic_fields_set__", "malformed", ProofabilityValidationCode.WRONG_TYPE),
    ],
)
def test_report_hidden_slots_are_closed_at_every_typed_boundary(
    slot: str,
    state_kind: str,
    code: ProofabilityValidationCode,
):
    report, inputs = _report()
    attacked = report.model_copy(deep=True)
    if state_kind == "nonempty":
        state: object = {"forged": "DO-NOT-ECHO"}
    elif state_kind == "empty":
        state = {}
    elif state_kind == "altered":
        state = {"schema_version"}
    else:
        state = ["DO-NOT-ECHO"]
    object.__setattr__(attacked, slot, state)

    _assert_all_typed_report_boundaries_reject(attacked, inputs, code)


def test_nested_report_slots_primitive_subclasses_and_enum_confusion_are_closed():
    report, inputs = _report()

    nested_private = report.model_copy(deep=True)
    object.__setattr__(
        nested_private.criterion_results[0],
        "__pydantic_private__",
        {"forged": "DO-NOT-ECHO"},
    )
    _assert_all_typed_report_boundaries_reject(
        nested_private,
        inputs,
        ProofabilityValidationCode.EXTRA_FIELD,
    )

    nested_fields_set = report.model_copy(deep=True)
    object.__setattr__(
        nested_fields_set.criterion_results[0].available_observations[0],
        "__pydantic_fields_set__",
        {"observation_kind"},
    )
    _assert_all_typed_report_boundaries_reject(
        nested_fields_set,
        inputs,
        ProofabilityValidationCode.INVALID_VALUE,
    )

    class HiddenString(str):
        pass

    for field_path in ("profile_id", "nested_source_field"):
        primitive = report.model_copy(deep=True)
        if field_path == "profile_id":
            node = primitive
            field_name = "profile_id"
        else:
            node = primitive.criterion_results[0].available_observations[0]
            field_name = "source_field"
        hidden = HiddenString(object.__getattribute__(node, "__dict__")[field_name])
        hidden.forged = "DO-NOT-ECHO"
        object.__getattribute__(node, "__dict__")[field_name] = hidden
        _assert_all_typed_report_boundaries_reject(
            primitive,
            inputs,
            ProofabilityValidationCode.WRONG_TYPE,
        )

    class DispositionImpostor(str, Enum):
        PROVABLE = "PROVABLE"

    enum_confused = report.model_copy(deep=True)
    object.__getattribute__(enum_confused, "__dict__")["overall_disposition"] = (
        DispositionImpostor.PROVABLE
    )
    _assert_all_typed_report_boundaries_reject(
        enum_confused,
        inputs,
        ProofabilityValidationCode.INVALID_VALUE,
    )

    forged_member = str.__new__(OverallProofabilityDisposition, "PROVABLE")
    object.__setattr__(forged_member, "_value_", "PROVABLE")
    object.__setattr__(forged_member, "_name_", "PROVABLE")
    object.__setattr__(
        forged_member,
        "__objclass__",
        OverallProofabilityDisposition,
    )
    object.__setattr__(forged_member, "_sort_order_", 0)
    exact_enum_confused = report.model_copy(deep=True)
    object.__getattribute__(exact_enum_confused, "__dict__")[
        "overall_disposition"
    ] = forged_member
    _assert_all_typed_report_boundaries_reject(
        exact_enum_confused,
        inputs,
        ProofabilityValidationCode.WRONG_TYPE,
    )

    enum_state = object.__getattribute__(
        OverallProofabilityDisposition.PROVABLE,
        "__dict__",
    )
    enum_state["forged"] = "DO-NOT-ECHO"
    try:
        _assert_all_typed_report_boundaries_reject(
            report,
            inputs,
            ProofabilityValidationCode.EXTRA_FIELD,
        )
    finally:
        enum_state.pop("forged")


def test_raw_model_cycles_and_mutable_report_containers_fail_closed():
    report, inputs = _report()
    cyclic = report.model_copy(deep=True)
    object.__getattribute__(
        cyclic.criterion_results[0], "__dict__"
    )["available_observations"] = (cyclic,)
    _assert_all_typed_report_boundaries_reject(
        cyclic,
        inputs,
        ProofabilityValidationCode.INVALID_VALUE,
    )

    mutable = report.model_copy(deep=True)
    object.__getattribute__(mutable, "__dict__")["criterion_results"] = list(
        mutable.criterion_results
    )
    _assert_all_typed_report_boundaries_reject(
        mutable,
        inputs,
        ProofabilityValidationCode.WRONG_TYPE,
    )

    aliased = report.model_copy(deep=True)
    result_state = object.__getattribute__(aliased.criterion_results[0], "__dict__")
    result_state["required_observations"] = result_state["available_observations"]
    _assert_all_typed_report_boundaries_reject(
        aliased,
        inputs,
        ProofabilityValidationCode.INVALID_VALUE,
    )


def test_nested_capability_and_contract_bypasses_fail_before_report_issuance():
    _, _, _, contract, descriptor = _bound_inputs()
    subject, scope, context, _, _ = _bound_inputs()
    attack = "DO-NOT-ECHO"
    nested = descriptor.model_copy()
    object.__getattribute__(nested.available_observations.native_ttft, "__dict__")[
        "forged"
    ] = attack
    error = _assert_rejected(
        lambda: evaluate_proofability(subject, scope, context, contract, nested),
        ProofabilityValidationCode.CAPABILITY_BINDING_MISMATCH,
    )
    assert attack not in str(error)
    tampered = contract.model_copy()
    object.__getattribute__(tampered, "__dict__")["criteria"] = [
        object.__getattribute__(tampered, "__dict__")["criteria"][0]
    ]
    _assert_rejected(
        lambda: evaluate_proofability(subject, scope, context, tampered, descriptor),
        ProofabilityValidationCode.CONTRACT_BINDING_MISMATCH,
    )


@pytest.mark.parametrize(
    ("case", "code"),
    [
        ("subject_top_string", ProofabilityValidationCode.SUBJECT_BINDING_MISMATCH),
        ("subject_nested_string", ProofabilityValidationCode.SUBJECT_BINDING_MISMATCH),
        ("scope_nested_integer", ProofabilityValidationCode.SCOPE_BINDING_MISMATCH),
        ("context_top_string", ProofabilityValidationCode.CONTEXT_BINDING_MISMATCH),
        ("contract_top_string", ProofabilityValidationCode.CONTRACT_BINDING_MISMATCH),
        ("contract_nested_integer", ProofabilityValidationCode.CONTRACT_BINDING_MISMATCH),
        ("contract_datetime", ProofabilityValidationCode.CONTRACT_BINDING_MISMATCH),
        ("contract_enum", ProofabilityValidationCode.CONTRACT_BINDING_MISMATCH),
        ("descriptor_top_string", ProofabilityValidationCode.CAPABILITY_BINDING_MISMATCH),
        ("descriptor_nested_string", ProofabilityValidationCode.CAPABILITY_BINDING_MISMATCH),
    ],
)
def test_every_typed_input_rejects_top_and_nested_primitive_type_confusion(
    case: str,
    code: ProofabilityValidationCode,
):
    inputs = _bound_inputs()
    report = evaluate_proofability(*inputs)
    subject, scope, context, contract, descriptor = inputs

    class HiddenString(str):
        pass

    class HiddenInteger(int):
        pass

    class HiddenDateTime(datetime):
        pass

    class ContractStatusImpostor(str, Enum):
        FROZEN = "FROZEN"

    if case == "subject_top_string":
        node, field_name, value_type = subject, "schema_version", HiddenString
    elif case == "subject_nested_string":
        node, field_name, value_type = subject.engine, "engine_id", HiddenString
    elif case == "scope_nested_integer":
        node, field_name, value_type = (
            scope.maximum_use,
            "maximum_traffic_percent",
            HiddenInteger,
        )
    elif case == "context_top_string":
        node, field_name, value_type = context, "protocol_id", HiddenString
    elif case == "contract_top_string":
        node, field_name, value_type = contract, "id", HiddenString
    elif case == "contract_nested_integer":
        node, field_name, value_type = (
            contract.criteria[0].latency_requirement,
            "threshold_ns",
            HiddenInteger,
        )
    elif case == "contract_datetime":
        node, field_name, value_type = contract, "created_at", HiddenDateTime
    elif case == "descriptor_top_string":
        node, field_name, value_type = descriptor, "schema_version", HiddenString
    elif case == "descriptor_nested_string":
        node, field_name, value_type = (
            descriptor.engine_adapter,
            "engine_id",
            HiddenString,
        )
    else:
        object.__getattribute__(contract, "__dict__")["status"] = (
            ContractStatusImpostor.FROZEN
        )
        node = None

    if node is not None:
        original = object.__getattribute__(node, "__dict__")[field_name]
        if value_type is HiddenDateTime:
            hidden: object = HiddenDateTime(
                original.year,
                original.month,
                original.day,
                original.hour,
                original.minute,
                original.second,
                original.microsecond,
                tzinfo=original.tzinfo,
                fold=original.fold,
            )
        else:
            hidden = value_type(original)
        hidden.forged = "DO-NOT-ECHO"
        object.__getattribute__(node, "__dict__")[field_name] = hidden

    error = _assert_rejected(
        lambda: evaluate_proofability(subject, scope, context, contract, descriptor),
        code,
    )
    assert "DO-NOT-ECHO" not in str(error)
    assert not verify_proofability_report(report, *inputs)


@pytest.mark.parametrize(
    ("case", "code"),
    [
        ("subject_private", ProofabilityValidationCode.SUBJECT_BINDING_MISMATCH),
        ("scope_fields_set", ProofabilityValidationCode.SCOPE_BINDING_MISMATCH),
        ("context_private_malformed", ProofabilityValidationCode.CONTEXT_BINDING_MISMATCH),
        ("contract_mutable_container", ProofabilityValidationCode.CONTRACT_BINDING_MISMATCH),
        ("descriptor_nested_private", ProofabilityValidationCode.CAPABILITY_BINDING_MISMATCH),
    ],
)
def test_every_typed_input_closes_slots_fields_set_and_mutable_containers(
    case: str,
    code: ProofabilityValidationCode,
):
    inputs = _bound_inputs()
    report = evaluate_proofability(*inputs)
    subject, scope, context, contract, descriptor = inputs
    if case == "subject_private":
        object.__setattr__(
            subject,
            "__pydantic_private__",
            {"forged": "DO-NOT-ECHO"},
        )
    elif case == "scope_fields_set":
        object.__setattr__(scope, "__pydantic_fields_set__", {"schema_version"})
    elif case == "context_private_malformed":
        object.__setattr__(context, "__pydantic_private__", ["DO-NOT-ECHO"])
    elif case == "contract_mutable_container":
        object.__getattribute__(contract, "__dict__")["owners"] = ["DO-NOT-ECHO"]
    else:
        object.__setattr__(
            descriptor.available_observations.native_ttft,
            "__pydantic_private__",
            {},
        )

    error = _assert_rejected(
        lambda: evaluate_proofability(subject, scope, context, contract, descriptor),
        code,
    )
    assert "DO-NOT-ECHO" not in str(error)
    assert not verify_proofability_report(report, *inputs)


def test_nested_raw_graph_attacks_fail_at_each_report_and_input_boundary():
    attack = "DO-NOT-ECHO"

    report, inputs = _report()
    for node in (
        report.criterion_results[0],
        report.criterion_results[0].available_observations[0],
        report.criterion_results[0].required_observations[1],
    ):
        object.__getattribute__(node, "__dict__")["forged"] = attack
        error = _assert_rejected(
            lambda report=report: serialize_proofability_report(report),
            ProofabilityValidationCode.EXTRA_FIELD,
        )
        assert attack not in str(error)
        object.__getattribute__(node, "__dict__").pop("forged")
        object.__setattr__(node, "__pydantic_extra__", {"forged": attack})
        error = _assert_rejected(
            lambda report=report: serialize_proofability_report(report),
            ProofabilityValidationCode.EXTRA_FIELD,
        )
        assert attack not in str(error)
        object.__setattr__(node, "__pydantic_extra__", None)
        object.__setattr__(node, "__pydantic_extra__", attack)
        _assert_rejected(
            lambda report=report: serialize_proofability_report(report),
            ProofabilityValidationCode.EXTRA_FIELD,
        )
        object.__setattr__(node, "__pydantic_extra__", None)

    subject, scope, context, contract, descriptor = inputs
    cases = (
        (subject.model, ProofabilityValidationCode.SUBJECT_BINDING_MISMATCH),
        (scope.frozen_contract, ProofabilityValidationCode.SCOPE_BINDING_MISMATCH),
        (context, ProofabilityValidationCode.CONTEXT_BINDING_MISMATCH),
        (
            contract.criteria[0].latency_requirement,
            ProofabilityValidationCode.CONTRACT_BINDING_MISMATCH,
        ),
        (
            descriptor.available_observations.measured_attempt_reliability,
            ProofabilityValidationCode.CAPABILITY_BINDING_MISMATCH,
        ),
    )
    for node, code in cases:
        object.__getattribute__(node, "__dict__")["forged"] = attack
        error = _assert_rejected(
            lambda: evaluate_proofability(subject, scope, context, contract, descriptor),
            code,
        )
        assert attack not in str(error)
        object.__getattribute__(node, "__dict__").pop("forged")


def test_exact_field_nested_subclass_and_construct_copy_bypasses_fail_closed():
    subject, scope, context, contract, descriptor = _bound_inputs()

    class ExactNativeObservationSubclass(NativeTTFTObservationV1):
        @property
        def forged(self) -> str:
            return "DO-NOT-ECHO"

    nested = descriptor.available_observations.model_copy(
        update={
            "native_ttft": ExactNativeObservationSubclass.model_validate(
                descriptor.available_observations.native_ttft.model_dump(mode="python")
            )
        }
    )
    subclass_descriptor = descriptor.model_copy(update={"available_observations": nested})
    _assert_rejected(
        lambda: evaluate_proofability(
            subject, scope, context, contract, subclass_descriptor
        ),
        ProofabilityValidationCode.CAPABILITY_BINDING_MISMATCH,
    )
    constructed_scope = scope.model_construct(
        **{**scope.model_dump(mode="python"), "frozen_contract": {}}
    )
    _assert_rejected(
        lambda: evaluate_proofability(
            subject, constructed_scope, context, contract, descriptor
        ),
        ProofabilityValidationCode.SCOPE_BINDING_MISMATCH,
    )
    copied_context = context.model_copy(
        update={"scope_digest": "sha256:" + "a" * 64}
    )
    _assert_rejected(
        lambda: evaluate_proofability(
            subject, scope, copied_context, contract, descriptor
        ),
        ProofabilityValidationCode.CONTEXT_BINDING_MISMATCH,
    )


def _parse_self_consistent_hostile(
    payload: dict[str, object], inputs: tuple[object, object, object, POCContract, object]
) -> None:
    parsed = parse_proofability_report(canonical_json_bytes(_rehash(payload)))
    assert not verify_proofability_report(parsed, *inputs)


def test_self_consistent_report_mutations_never_replace_input_bound_evaluation():
    report, inputs = _report()
    for key, replacement in (
        ("profile_id", "other-profile"),
        ("profile_version", "v2"),
        ("engine_id", "other-engine"),
        ("engine_version", "0.27.0"),
        ("adapter_id", "other-adapter"),
        ("adapter_version", "2.0.0"),
    ):
        hostile = json.loads(serialize_proofability_report(report))
        hostile[key] = replacement
        _parse_self_consistent_hostile(hostile, inputs)

    semantic_report, _ = _report(_criterion_payload(semantic=True))
    hostile = json.loads(serialize_proofability_report(semantic_report))
    hostile["criterion_results"][0]["criterion_id"] = "QUAL-TTFT-02"
    _parse_self_consistent_hostile(hostile, inputs)

    mixed, mixed_inputs = _report(
        _criterion_payload(criterion_id="QUAL-NATIVE-01"),
        _criterion_payload(criterion_id="QUAL-SEMANTIC-01", semantic=True),
    )
    reordered = json.loads(serialize_proofability_report(mixed))
    reordered["criterion_results"].reverse()
    _parse_self_consistent_hostile(reordered, mixed_inputs)


@pytest.mark.parametrize(
    ("invalid_criterion_id", "expected_code"),
    [
        ("AB", ProofabilityValidationCode.INVALID_VALUE),
        ("A" * 65, ProofabilityValidationCode.OVERSIZED),
        ("1AB", ProofabilityValidationCode.INVALID_VALUE),
        ("lowercase", ProofabilityValidationCode.INVALID_VALUE),
        ("AbC", ProofabilityValidationCode.INVALID_VALUE),
        ("ABC_def", ProofabilityValidationCode.INVALID_VALUE),
        ("ABC.DEF", ProofabilityValidationCode.INVALID_VALUE),
        ("routing_qualification_v2", ProofabilityValidationCode.INVALID_VALUE),
        ("routing-slo-attainment-v1", ProofabilityValidationCode.INVALID_VALUE),
        ("routing_Qualification_v1", ProofabilityValidationCode.INVALID_VALUE),
        (
            "routing_campaign_reduction_v1-extra",
            ProofabilityValidationCode.INVALID_VALUE,
        ),
    ],
)
def test_report_criterion_ids_reject_values_outside_the_contract_union_language(
    invalid_criterion_id: str,
    expected_code: ProofabilityValidationCode,
):
    report, _ = _report()
    hostile = report.model_dump(mode="json")
    hostile["criterion_results"][0]["criterion_id"] = invalid_criterion_id
    _rehash(hostile)
    _assert_rejected(
        lambda: parse_proofability_report(canonical_json_bytes(hostile)),
        expected_code,
    )


@pytest.mark.parametrize(
    ("path", "replacement"),
    [
        (("latency_requirement", "threshold_ns"), 10_000_000),
        (("latency_requirement", "minimum_successful_samples"), 99),
        (("reliability_requirement", "threshold_basis_points"), 99),
        (("reliability_requirement", "exact_attempts"), 101),
    ],
)
def test_prospective_requirement_mutations_change_contract_and_report_identity(
    path: tuple[str, str], replacement: int
):
    base_report, _ = _report()
    payload = _criterion_payload()
    payload[path[0]][path[1]] = replacement  # type: ignore[index]
    changed_report, _ = _report(payload)

    assert changed_report.contract_canonical_digest != base_report.contract_canonical_digest
    assert changed_report.proofability_report_digest != base_report.proofability_report_digest


def test_models_are_frozen_and_source_containers_are_not_retained_mutably():
    source = _criterion_payload()
    criterion = InferenceQualificationCriterionV1.model_validate(source)
    source["latency_requirement"]["threshold_ns"] = 10_000_000  # type: ignore[index]

    assert criterion.latency_requirement.threshold_ns == 20_000_000
    with pytest.raises(ValidationError):
        criterion.latency_requirement.threshold_ns = 10_000_000
    report, _ = _report()
    with pytest.raises(ValidationError):
        report.criterion_results[0].disposition = CriterionProofabilityDisposition.NOT_PROVABLE


def test_module_has_no_runtime_side_effect_import_surface():
    source = Path(proofability_module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_modules = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported_modules.update(
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    )

    for forbidden in (
        "subprocess",
        "socket",
        "requests",
        "httpx",
        "webbrowser",
        "selenium",
        "playwright",
    ):
        assert forbidden not in imported_modules
    assert ("infer" + "drome") not in source.casefold()
    assert "def main(" not in source


def test_evaluation_does_not_read_scope_paths_or_use_file_side_effects(monkeypatch):
    inputs = _bound_inputs()

    def forbidden_read(*_args, **_kwargs):
        raise AssertionError("PR5 evaluation must not read a path")

    monkeypatch.setattr(Path, "read_bytes", forbidden_read)
    report = evaluate_proofability(*inputs)
    assert report.overall_disposition is OverallProofabilityDisposition.PROVABLE


def test_pr5_diff_does_not_touch_provider_specific_implementation_or_resources():
    completed = subprocess.run(
        [
            "git",
            "diff",
            "--name-only",
            "1c5fe7960d5464fd40ae21b1a73a841ca0cbf27c",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    changed = tuple(line for line in completed.stdout.splitlines() if line)
    prohibited = "infer" + "drome"
    assert not any(prohibited in path.casefold() for path in changed)
    assert not any(path.startswith("src/exitspec/profiles/") for path in changed)
    assert not any(path.startswith("src/exitspec/schemas/") for path in changed)
