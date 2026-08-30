import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import time

import pytest

from exitspec.canonical import canonical_json_bytes
from exitspec.routing_campaign_verifier import (
    ROUTING_CAMPAIGN_EVIDENCE_BUNDLE_SCHEMA_VERSION,
    RoutingCampaignCacheResetEvidenceV1,
    RoutingCampaignEvidenceBundleV1,
    RoutingCampaignRouteDecisionReceiptV1,
    RoutingCampaignRunEvidenceV1,
    _sha256_without_field,
    parse_routing_campaign_confirmation,
    parse_routing_campaign_contract,
    parse_routing_campaign_evidence,
    reduce_routing_campaign,
)
from exitspec.routing_qualification import RoutingQualificationValidationCode
from exitspec.routing_qualification_receipts import (
    ROUTING_QUALIFICATION_RECEIPT_SCHEMA_VERSION,
    RoutingQualificationReceiptRejected,
    issue_routing_qualification_receipt,
    parse_routing_qualification_receipt,
    serialize_routing_qualification_receipt,
    validate_routing_qualification_receipt,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_FIXTURE = PROJECT_ROOT / (
    "examples/routing-qualification/contracts/"
    "routing-campaign-reduction-v1.synthetic.json"
)
CONFIRMATION_FIXTURE = PROJECT_ROOT / (
    "examples/routing-qualification/contracts/"
    "routing-campaign-reduction-v1.synthetic.confirmation.json"
)
EVIDENCE_FIXTURE = PROJECT_ROOT / (
    "examples/routing-qualification/evidence/"
    "routing-campaign-evidence-v1.synthetic.json"
)
RECEIPT_FIXTURE = PROJECT_ROOT / (
    "examples/routing-qualification/receipts/"
    "routing-qualification-receipt-v1.synthetic.json"
)
EXPECTED_B9_HASH = (
    "e3bbcab57ac37987f981e0de1a36e56ae6f649cd2f3c75d8d7bcd637583a0516"
)
EXPECTED_B10_HASH = (
    "097a49c40646e8a58c31a375160cc0e453ed88dfa5e30cd78120f9d89a460f07"
)
EXPECTED_B11_HASH = (
    "66a6642ab761e8430e0a955e4b43de4779dda12fa08207ad25bb708c858bd260"
)
EXPECTED_CONFIRMATION_SHA256 = (
    "3a64a55affa7bfc661b311651c55c2120ac8bb9492645c75ccceb3a8e7d8f6d5"
)
EXPECTED_EVIDENCE_SHA256 = (
    "01bdc0f93b6f9bb40c72be17bae0aab07edba31f456881e3aa2596e863c31f86"
)


def _json(path: Path):
    return json.loads(path.read_bytes())


def _context():
    contract = parse_routing_campaign_contract(_json(CONTRACT_FIXTURE))
    confirmation = parse_routing_campaign_confirmation(_json(CONFIRMATION_FIXTURE))
    evidence = parse_routing_campaign_evidence(_json(EVIDENCE_FIXTURE))
    return contract, confirmation, evidence


def _digest(model, field: str) -> str:
    return _sha256_without_field(model, field)


def _complete_run(contract, template, repetition_index: int, *, failures: int = 0):
    campaign = contract.criteria[0]
    run_id = f"synthetic-run-{repetition_index}"
    source_digest = format(repetition_index, "x") * 64
    producer = template.producer.model_copy(update={"source_digest": source_digest})
    telemetry = template.telemetry.model_copy(
        update={
            "telemetry_capsule_id": f"synthetic-telemetry-run-{repetition_index}",
            "telemetry_capsule_sha256": "0" * 64,
            "run_id": run_id,
            "source_digest": source_digest,
        }
    )
    telemetry = telemetry.model_copy(
        update={
            "telemetry_capsule_sha256": _digest(
                telemetry, "telemetry_capsule_sha256"
            )
        }
    )
    resets = []
    for trial_index in range(campaign.trial_order.trial_count):
        reset = RoutingCampaignCacheResetEvidenceV1(
            schema_version="exitspec.routing-campaign-cache-reset-evidence.v1",
            reset_id=f"synthetic-reset-r{repetition_index}-t{trial_index}",
            reset_sha256="0" * 64,
            run_id=run_id,
            repetition_index=repetition_index,
            trial_index=trial_index,
            status="RESET_CONFIRMED",
            reset_scope="ROUTER_AND_SERVING_ENGINE_STATE",
            reset_at="2026-08-30T12:00:00Z",
            producer_id=producer.producer_id,
            producer_version=producer.producer_version,
            source_digest=source_digest,
        )
        resets.append(
            reset.model_copy(update={"reset_sha256": _digest(reset, "reset_sha256")})
        )
    assignments = []
    for trial_index in range(campaign.trial_order.trial_count):
        for request_index in range(campaign.trial_order.request_count):
            for role in ("candidate", "baseline"):
                policy = (
                    campaign.candidate_policy
                    if role == "candidate"
                    else campaign.baseline_policy
                )
                should_fail = (
                    role == "candidate"
                    and trial_index == 0
                    and request_index < failures
                )
                receipt = RoutingCampaignRouteDecisionReceiptV1(
                    schema_version=(
                        "exitspec.routing-campaign-route-decision-receipt.v1"
                    ),
                    route_decision_receipt_id=(
                        f"synthetic-receipt-r{repetition_index}-t{trial_index}-"
                        f"q{request_index:06d}-{role[0]}"
                    ),
                    route_decision_receipt_sha256="0" * 64,
                    campaign_contract_sha256=contract.canonical_hash,
                    run_id=run_id,
                    repetition_index=repetition_index,
                    request_id=f"request-{request_index:06d}",
                    trial_index=trial_index,
                    policy_role=role,
                    policy_id=policy.policy_id,
                    policy_sha256=policy.policy_sha256,
                    routing_configuration_id=(
                        campaign.routing_configuration.configuration_id
                    ),
                    routing_configuration_sha256=(
                        campaign.routing_configuration.configuration_sha256
                    ),
                    producer_id=producer.producer_id,
                    producer_version=producer.producer_version,
                    captured_at="2026-08-30T12:00:00Z",
                    source_digest=source_digest,
                    terminal_outcome="SUCCESS",
                    latency_ns=250_000_001 if should_fail else 1,
                )
                assignments.append(
                    receipt.model_copy(
                        update={
                            "route_decision_receipt_sha256": _digest(
                                receipt, "route_decision_receipt_sha256"
                            )
                        }
                    )
                )
    return RoutingCampaignRunEvidenceV1(
        **template.model_dump(
            mode="python",
            exclude={
                "run_id",
                "repetition_index",
                "telemetry",
                "cache_resets",
                "producer",
                "assignments",
            },
        ),
        run_id=run_id,
        repetition_index=repetition_index,
        telemetry=telemetry,
        cache_resets=tuple(resets),
        producer=producer,
        assignments=tuple(assignments),
    )


def _complete_bundle(contract, template, *, failures: int = 0, external=False):
    runs = (
        _complete_run(contract, template, 1, failures=failures),
        _complete_run(contract, template, 2),
    )
    evidence_class = "EXTERNAL_SEALED_EVIDENCE" if external else "SYNTHETIC_FIXTURE"
    if external:
        runs = tuple(run.model_copy(update={"evidence_class": evidence_class}) for run in runs)
    return RoutingCampaignEvidenceBundleV1(
        schema_version=ROUTING_CAMPAIGN_EVIDENCE_BUNDLE_SCHEMA_VERSION,
        protocol_id="routing_campaign_verification_v1",
        evidence_class=evidence_class,
        contract_sha256=contract.canonical_hash,
        runs=runs,
    )


def _issue(contract, confirmation, evidence):
    result = reduce_routing_campaign(contract, confirmation, evidence)
    receipt = issue_routing_qualification_receipt(
        contract,
        confirmation,
        evidence,
        result,
        issued_at="2026-08-30T12:30:00Z",
    )
    return result, receipt


def _expect_rejection(callable_obj, code):
    with pytest.raises(RoutingQualificationReceiptRejected) as caught:
        callable_obj()
    assert caught.value.code is code


def _forge_receipt_id(payload):
    identity = {key: value for key, value in payload.items() if key != "receipt_id"}
    payload["receipt_id"] = "rqr_" + hashlib.sha256(
        b"exitspec-routing-policy-qualification-receipt-v1\x00"
        + canonical_json_bytes(identity)
    ).hexdigest()
    return payload


def test_synthetic_golden_is_canonical_test_only_and_frozen():
    contract, confirmation, evidence = _context()
    result = reduce_routing_campaign(contract, confirmation, evidence)
    raw_fixture = RECEIPT_FIXTURE.read_bytes()
    receipt = parse_routing_qualification_receipt(raw_fixture)
    canonical = serialize_routing_qualification_receipt(
        contract, confirmation, evidence, result, receipt
    )
    assert raw_fixture == canonical
    assert receipt == issue_routing_qualification_receipt(
        contract,
        confirmation,
        evidence,
        result,
        issued_at="2026-08-30T12:30:00Z",
    )
    assert receipt.verdict == "NOT_PROVEN"
    assert receipt.evidence_use == "TEST_ONLY"
    assert receipt.authorization.deployment_authorized is False
    assert receipt.authorization.production_traffic_authorized is False
    assert receipt.authorization.release_authorized is False
    assert receipt.authorization.human_product_decision_required is True
    assert receipt.receipt_id == (
        "rqr_ab83f702d765ce428c88c7deea0a7aa4f46293c098d25117f59633c6f37b5c34"
    )
    assert hashlib.sha256(raw_fixture).hexdigest() == (
        "c502a1e3bae757015b90ecca96839b5c792a1d3c2fab9a048a40d00829cfaa87"
    )


@pytest.mark.parametrize(
    ("failures", "expected_verdict"),
    [(0, "PASS"), (10, "FAIL")],
)
def test_issues_pass_and_fail_without_granting_authority(failures, expected_verdict):
    contract, confirmation, golden = _context()
    evidence = _complete_bundle(contract, golden.runs[0], failures=failures)
    result, receipt = _issue(contract, confirmation, evidence)
    assert result.campaign_verdict == expected_verdict
    assert receipt.verdict == expected_verdict
    assert receipt.evidence_use == "TEST_ONLY"
    assert set(receipt.missing_repetition_indices) == set()
    assert receipt.authorization == receipt.authorization.__class__()


def test_external_evidence_verdict_still_has_zero_deployment_authority():
    contract, confirmation, golden = _context()
    evidence = _complete_bundle(contract, golden.runs[0], external=True)
    result, receipt = _issue(contract, confirmation, evidence)
    assert result.campaign_verdict == "PASS"
    assert receipt.evidence_class == "EXTERNAL_SEALED_EVIDENCE"
    assert receipt.evidence_use == "EXTERNAL_EVIDENCE"
    assert receipt.authorization.deployment_authorized is False
    assert receipt.authorization.shipping_authorized is False
    assert receipt.authorization.traffic_expansion_authorized is False
    assert receipt.authorization.contract_mutation_authorized is False


def test_ingestion_rejection_produces_no_receipt():
    contract, confirmation, evidence = _context()
    result = reduce_routing_campaign(contract, confirmation, evidence)
    bad_run = evidence.runs[0].model_copy(
        update={
            "telemetry": evidence.runs[0].telemetry.model_copy(
                update={"telemetry_capsule_sha256": "0" * 64}
            )
        }
    )
    malformed = evidence.model_copy(update={"runs": (bad_run,)})
    issued = []
    _expect_rejection(
        lambda: issued.append(
            issue_routing_qualification_receipt(
                contract,
                confirmation,
                malformed,
                result,
                issued_at="2026-08-30T12:30:00Z",
            )
        ),
        RoutingQualificationValidationCode.INVALID_DIGEST,
    )
    assert issued == []


def test_result_only_tamper_and_context_substitution_cannot_issue_or_validate():
    contract, confirmation, evidence = _context()
    result, receipt = _issue(contract, confirmation, evidence)
    tampered_result = result.model_copy(update={"campaign_verdict": "PASS"})
    _expect_rejection(
        lambda: issue_routing_qualification_receipt(
            contract,
            confirmation,
            evidence,
            tampered_result,
            issued_at=receipt.issued_at,
        ),
        RoutingQualificationValidationCode.SEMANTIC_INCONSISTENCY,
    )
    wrong_confirmation = confirmation.model_copy(
        update={"contract_fingerprint": "0" * 64}
    )
    _expect_rejection(
        lambda: validate_routing_qualification_receipt(
            contract, wrong_confirmation, evidence, result, receipt
        ),
        RoutingQualificationValidationCode.CONTRACT_BINDING_MISMATCH,
    )
    _expect_rejection(
        lambda: issue_routing_qualification_receipt(
            contract,
            confirmation,
            evidence.runs[0],
            result,
            issued_at=receipt.issued_at,
        ),
        RoutingQualificationValidationCode.WRONG_TYPE,
    )


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("contract_sha256", "0" * 64),
        ("confirmation_canonical_sha256", "1" * 64),
        ("candidate_policy_sha256", "2" * 64),
        ("baseline_policy_id", "substituted-baseline"),
        ("result_canonical_sha256", "3" * 64),
        ("verdict", "PASS"),
        ("issued_at", "2026-08-30T12:30:01Z"),
    ],
)
def test_raw_state_tamper_and_model_copy_bypass_are_rejected(field, replacement):
    contract, confirmation, evidence = _context()
    result, receipt = _issue(contract, confirmation, evidence)
    tampered = receipt.model_copy(update={field: replacement})
    _expect_rejection(
        lambda: validate_routing_qualification_receipt(
            contract, confirmation, evidence, result, tampered
        ),
        RoutingQualificationValidationCode.SEMANTIC_INCONSISTENCY,
    )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload["evidence_runs"][0].update(
            {"repetition_index": 3}
        ),
        lambda payload: payload.update(
            {
                "baseline_policy_id": payload["candidate_policy_id"],
                "baseline_policy_sha256": payload["candidate_policy_sha256"],
            }
        ),
        lambda payload: payload.update(
            {
                "verdict": "PASS",
                "missing_repetition_indices": [2],
                "evidence_runs": payload["evidence_runs"][:1],
            }
        ),
    ],
)
def test_forged_payload_with_recomputed_receipt_id_still_fails_invariants(mutate):
    contract, confirmation, evidence = _context()
    _result, receipt = _issue(contract, confirmation, evidence)
    payload = receipt.model_dump(mode="json")
    mutate(payload)
    _forge_receipt_id(payload)
    _expect_rejection(
        lambda: parse_routing_qualification_receipt(payload),
        RoutingQualificationValidationCode.SEMANTIC_INCONSISTENCY,
    )


def test_self_consistent_forged_result_digest_still_fails_context_validation():
    contract, confirmation, evidence = _context()
    result, receipt = _issue(contract, confirmation, evidence)
    payload = receipt.model_dump(mode="json")
    payload["result_canonical_sha256"] = "9" * 64
    forged = parse_routing_qualification_receipt(_forge_receipt_id(payload))
    assert forged.result_canonical_sha256 == "9" * 64
    _expect_rejection(
        lambda: validate_routing_qualification_receipt(
            contract, confirmation, evidence, result, forged
        ),
        RoutingQualificationValidationCode.CONTRACT_BINDING_MISMATCH,
    )


def test_typed_model_construct_and_falsey_raw_state_cannot_bypass_revalidation():
    contract, confirmation, evidence = _context()
    result, receipt = _issue(contract, confirmation, evidence)
    raw = receipt.model_dump(mode="python")
    del raw["purpose"]
    constructed = receipt.__class__.model_construct(**raw)
    _expect_rejection(
        lambda: validate_routing_qualification_receipt(
            contract, confirmation, evidence, result, constructed
        ),
        RoutingQualificationValidationCode.MISSING_FIELD,
    )
    falsey = receipt.model_copy(update={"evidence_runs": ()})
    _expect_rejection(
        lambda: serialize_routing_qualification_receipt(
            contract, confirmation, evidence, result, falsey
        ),
        RoutingQualificationValidationCode.OVERSIZED,
    )


def test_reordered_evidence_identity_and_mixed_evidence_class_are_rejected():
    contract, confirmation, golden = _context()
    evidence = _complete_bundle(contract, golden.runs[0])
    result, receipt = _issue(contract, confirmation, evidence)
    reordered = receipt.model_copy(
        update={"evidence_runs": tuple(reversed(receipt.evidence_runs))}
    )
    _expect_rejection(
        lambda: validate_routing_qualification_receipt(
            contract, confirmation, evidence, result, reordered
        ),
        RoutingQualificationValidationCode.SEMANTIC_INCONSISTENCY,
    )
    mixed_run = evidence.runs[1].model_copy(
        update={"evidence_class": "EXTERNAL_SEALED_EVIDENCE"}
    )
    mixed = evidence.model_construct(
        **{**evidence.model_dump(mode="python"), "runs": (evidence.runs[0], mixed_run)}
    )
    _expect_rejection(
        lambda: issue_routing_qualification_receipt(
            contract,
            confirmation,
            mixed,
            result,
            issued_at=receipt.issued_at,
        ),
        RoutingQualificationValidationCode.SEMANTIC_INCONSISTENCY,
    )


def test_strict_parse_rejects_duplicate_extra_missing_wrong_version_and_noncanonical():
    contract, confirmation, evidence = _context()
    _result, receipt = _issue(contract, confirmation, evidence)
    payload = receipt.model_dump(mode="json")
    duplicate = b'{"schema_version":"a","schema_version":"b"}'
    _expect_rejection(
        lambda: parse_routing_qualification_receipt(duplicate),
        RoutingQualificationValidationCode.DUPLICATE_FIELD,
    )
    extra = {**payload, "undocumented": True}
    _expect_rejection(
        lambda: parse_routing_qualification_receipt(extra),
        RoutingQualificationValidationCode.EXTRA_FIELD,
    )
    missing = dict(payload)
    del missing["purpose"]
    _expect_rejection(
        lambda: parse_routing_qualification_receipt(missing),
        RoutingQualificationValidationCode.MISSING_FIELD,
    )
    wrong = {**payload, "schema_version": "exitspec.routing-policy-qualification-receipt.v2"}
    _expect_rejection(
        lambda: parse_routing_qualification_receipt(wrong),
        RoutingQualificationValidationCode.WRONG_VERSION,
    )
    pretty = json.dumps(payload, indent=2).encode()
    _expect_rejection(
        lambda: parse_routing_qualification_receipt(pretty),
        RoutingQualificationValidationCode.NON_CANONICAL,
    )


def test_receipt_boundary_rejects_oversized_integer_and_wrong_scalar_type():
    contract, confirmation, evidence = _context()
    _result, receipt = _issue(contract, confirmation, evidence)
    payload = receipt.model_dump(mode="json")
    _expect_rejection(
        lambda: parse_routing_qualification_receipt(b" " * (128 * 1024 + 1)),
        RoutingQualificationValidationCode.OVERSIZED,
    )
    oversized_integer = {
        **payload,
        "required_repetition_indices": [2_147_483_648],
    }
    _expect_rejection(
        lambda: parse_routing_qualification_receipt(oversized_integer),
        RoutingQualificationValidationCode.OVERSIZED,
    )
    wrong_scalar = {**payload, "contract_version": False}
    _expect_rejection(
        lambda: parse_routing_qualification_receipt(wrong_scalar),
        RoutingQualificationValidationCode.WRONG_TYPE,
    )


def test_utc_whole_second_timestamp_is_timezone_portable(monkeypatch):
    if not hasattr(time, "tzset"):
        pytest.skip("This platform has no timezone reset primitive.")
    contract, confirmation, evidence = _context()
    result = reduce_routing_campaign(contract, confirmation, evidence)
    explicit = datetime(
        2026, 8, 30, 5, 30, tzinfo=timezone(timedelta(hours=-7))
    )
    original = os.environ.get("TZ")
    receipts = []
    try:
        for zone in ("Pacific/Honolulu", "Asia/Tokyo"):
            monkeypatch.setenv("TZ", zone)
            time.tzset()
            receipts.append(
                issue_routing_qualification_receipt(
                    contract,
                    confirmation,
                    evidence,
                    result,
                    issued_at=explicit,
                )
            )
    finally:
        if original is None:
            monkeypatch.delenv("TZ", raising=False)
        else:
            monkeypatch.setenv("TZ", original)
        time.tzset()
    assert receipts[0] == receipts[1]
    assert receipts[0].issued_at == "2026-08-30T12:30:00Z"
    with pytest.raises(ValueError, match="whole-second"):
        issue_routing_qualification_receipt(
            contract,
            confirmation,
            evidence,
            result,
            issued_at=explicit.replace(microsecond=1),
        )


def test_b9_b10_b11_confirmation_and_evidence_hashes_do_not_regress():
    b9 = PROJECT_ROOT / (
        "examples/routing-qualification/contracts/routing-qualification-v1.json"
    )
    b10 = PROJECT_ROOT / (
        "examples/routing-qualification/contracts/"
        "routing-slo-attainment-v1.synthetic.json"
    )
    assert _json(b9)["canonical_hash"] == EXPECTED_B9_HASH
    assert _json(b10)["canonical_hash"] == EXPECTED_B10_HASH
    assert _json(CONTRACT_FIXTURE)["canonical_hash"] == EXPECTED_B11_HASH
    assert hashlib.sha256(canonical_json_bytes(_json(CONFIRMATION_FIXTURE))).hexdigest() == (
        EXPECTED_CONFIRMATION_SHA256
    )
    assert hashlib.sha256(canonical_json_bytes(_json(EVIDENCE_FIXTURE))).hexdigest() == (
        EXPECTED_EVIDENCE_SHA256
    )
    assert _json(RECEIPT_FIXTURE)["schema_version"] == (
        ROUTING_QUALIFICATION_RECEIPT_SCHEMA_VERSION
    )
