"""Focused A5 domain invariants before HTTP/UI integration."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
import threading
import warnings

import pytest

from exitspec.assisted_authoring import RetainedProposalProjection
from exitspec.models import (
    CapabilityCriterion,
    POCContract,
    capability_evidence_policy_digest,
)
from exitspec.confirmations import contract_confirmation_fingerprint
from exitspec.poc_agreement import (
    AgreementConflict,
    AgreementStale,
    ProcessLocalAgreementLifecycleService,
)
from exitspec.poc_capability_planner import (
    CapabilityPlanningInvalid,
    PlannerCriterionInput,
    PlannerItemInput,
    PlanningProvenance,
    PlanningScope,
    ProcessLocalCapabilityPlannerService,
)
from exitspec.poc_creation import (
    DraftPOCSnapshot,
    FirstSourceChoice,
    NextIntakeRoute,
)
from exitspec.poc_sources import SourceKind
from exitspec.poc_agreement_web_api import handle_poc_agreement_web_api_request
from exitspec.review_links import ReviewInvitationError


NOW = datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc)


def _proposal(poc_id: str, proposal_id: str, *, digest: str = "c") -> RetainedProposalProjection:
    return RetainedProposalProjection(
        schema_version="exitspec.retained-proposal-projection.v1",
        poc_id=poc_id,
        proposal_id=proposal_id,
        authoring_receipt_id="arcp_" + "a" * 32,
        authoring_result_id="ares_" + "b" * 32,
        source_receipt_id="srcpt_a5_test_001",
        source_id="src_a5_test_001",
        source_kind=SourceKind.DOCUMENT,
        source_content_sha256=digest * 64,
        source_revision=1,
        source_adapter_name="exitspec_document",
        source_adapter_version="1.0.0",
        redaction_policy_version="redaction-v1",
        proposal_key=proposal_id.replace("prop_", "proposal-"),
        source_quote="Select the exact tool.",
        normalized_claim="Select the exact tool.",
        numeric_facts=None,
        retention_state="KEEP_FOR_CONTRACT",
        reviewer="a3.reviewer",
        rationale="Retained for A4 planning.",
        decided_at=NOW,
    )


def _poc(poc_id: str) -> DraftPOCSnapshot:
    return DraftPOCSnapshot(
        poc_id=poc_id,
        display_name="Fresh A5 POC",
        customer_label="A5 customer",
        use_case="Bind one exact capability agreement.",
        owner="field_engineer",
        first_source_choice=FirstSourceChoice.DOCUMENT,
        next_intake_route=NextIntakeRoute.DOCUMENT,
        created_at=NOW,
        updated_at=NOW,
    )


def _plan_item(proposal_id: str, *, threshold: float = 0.95) -> PlannerItemInput:
    return PlannerItemInput(
        proposal_id=proposal_id,
        scope=PlanningScope.MUST_HAVE,
        capability_key="exact_tool_selection",
        criterion=PlannerCriterionInput(
            rule="exact_tool_selection_rate",
            operator="GTE",
            threshold=threshold,
            unit="PROPORTION",
            measurement_population="approved_synthetic_cases",
            evidence_method="EXIT_SPEC_STREAMING_PROBE",
            adapter="deterministic_tool_selection",
            adapter_version="1.0.0",
            provenance=PlanningProvenance.SOURCE_EXTRACTED,
        ),
        reviewer="named.a4.reviewer",
        rationale="The exact server-owned capability is ready.",
    )


def _ttft_plan_item(proposal_id: str, *, threshold: float = 250.0) -> PlannerItemInput:
    return PlannerItemInput(
        proposal_id=proposal_id,
        scope=PlanningScope.MUST_HAVE,
        capability_key="inference_performance_external",
        criterion=PlannerCriterionInput(
            rule="ttft_p95",
            operator="LT",
            threshold=threshold,
            unit="MILLISECONDS",
            measurement_population="successful_measured_requests_with_observed_ttft",
            evidence_method="EXTERNAL_EVIDENCE_BUNDLE",
            adapter="vllm_bench_serve",
            adapter_version="1.0.0",
            evidence_profile="inferdrome.managed-vllm-0.26-evidence-profile.v1",
            provenance=PlanningProvenance.SOURCE_EXTRACTED,
        ),
        reviewer="named.a4.reviewer",
        rationale="The managed TTFT evidence profile is ready.",
    )


def _nested_keys(value: object) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        keys.update(key for key in value if isinstance(key, str))
        for child in value.values():
            keys.update(_nested_keys(child))
    elif isinstance(value, (list, tuple)):
        for child in value:
            keys.update(_nested_keys(child))
    return keys


def _fixture(poc_id: str = "poc_a5_test"):
    proposal = _proposal(poc_id, "prop_a5_test_001")
    state = {"retained": (proposal,)}
    planner = ProcessLocalCapabilityPlannerService(
        proposal_lookup=lambda _: state["retained"],
        clock=lambda: NOW,
    )
    planner.plan(poc_id, (_plan_item(proposal.proposal_id),), idempotency_key="a4-v1")
    service = ProcessLocalAgreementLifecycleService(
        poc_lookup=lambda _: _poc(poc_id),
        retained_lookup=lambda _: state["retained"],
        planner=planner,
        clock=lambda: NOW,
    )
    return service, planner, state, proposal


def _token(service: ProcessLocalAgreementLifecycleService) -> str:
    return service.customer_review_url("poc_a5_test").split("/review/", 1)[1]


def test_a5_uses_generic_criterion_and_preserves_exact_a4_without_sample_default():
    service, _, _, _ = _fixture()
    preparation = service.prepare(
        "poc_a5_test",
        reviewer="named.a5.reviewer",
        rationale="Assemble the exact current A4 plan.",
        idempotency_key="prepare-a5",
    ).value

    criterion = preparation.contract.criteria[0]
    assert isinstance(criterion, CapabilityCriterion)
    payload = criterion.model_dump(mode="json")
    assert "minimum_samples" not in payload
    assert criterion.rule == "exact_tool_selection_rate"
    assert criterion.operator == "GTE"
    assert criterion.threshold == 0.95
    assert criterion.unit == "PROPORTION"
    assert criterion.measurement_population == "approved_synthetic_cases"
    assert criterion.capability_key == "exact_tool_selection"
    assert criterion.planning_disposition == "EXECUTABLE"
    assert criterion.planner_reviewer == "named.a4.reviewer"
    assert criterion.assembly_reviewer == "named.a5.reviewer"
    assert criterion.assembly_rationale == "Assemble the exact current A4 plan."
    assert criterion.evidence_binding is not None
    assert criterion.evidence_binding.binding_type == "EXECUTABLE"
    assert criterion.evidence_binding.policy.workload_path == (
        "examples/support-agent/fixtures/tool-selection-200.json"
    )
    assert criterion.evidence_binding.policy.workload_sha256 == (
        "75ef6f83450de100a920e9489a0b5966464f1dba2e3d339c4b57e64fb95d8271"
    )
    assert criterion.evidence_binding.policy.minimum_samples == 200
    assert criterion.evidence_binding.policy.confidence_level == 0.95
    assert criterion.evidence_binding.policy.evidence_method == "EXIT_SPEC_STREAMING_PROBE"
    assert criterion.evidence_binding.policy.calculator_id == (
        "exitspec.statistics.wilson_lower_bound"
    )
    assert criterion.evidence_binding.policy.calculator_version == "wilson-two-sided-v1"
    assert criterion.evidence_binding.policy.adapter == "deterministic_tool_selection"
    assert criterion.evidence_binding.policy.verifier_id == (
        "exitspec.verdicts.evaluate_proportion_criterion"
    )
    assert criterion.evidence_binding.policy.reducer_id == (
        "exitspec.verdicts.aggregate_overall_verdict"
    )
    assert criterion.evidence_binding.policy_sha256 == capability_evidence_policy_digest(
        criterion.evidence_binding.policy
    )
    assert preparation.contract.workload.fixture_path.startswith("policy://")
    assert "generated/" not in preparation.contract.workload.fixture_path


def test_request_changes_rejects_unchanged_successor_and_changed_snapshot_makes_complete_v2():
    service, planner, state, proposal = _fixture()
    parent = service.prepare(
        "poc_a5_test",
        reviewer="named.a5.reviewer",
        rationale="Assemble the exact current A4 plan.",
        idempotency_key="prepare-a5",
    ).value
    parent_dump = parent.contract.model_dump(mode="json")
    old_token = _token(service)
    service.record_customer_review_decision(
        old_token,
        decision="REQUEST_CHANGES",
        agreement_acknowledged=True,
        rationale="Change the threshold.",
        idempotency_key="request-changes",
    )

    with pytest.raises(AgreementConflict, match="materially changed"):
        service.start_revision(
            "poc_a5_test",
            reviewer="named.a5.successor",
            rationale="Review the changed successor.",
            idempotency_key="revision-unchanged",
        )

    planner.plan(
        "poc_a5_test",
        (_plan_item(proposal.proposal_id, threshold=0.9),),
        idempotency_key="a4-v2",
    )
    revision = service.start_revision(
        "poc_a5_test",
        reviewer="named.a5.successor",
        rationale="Review the changed successor.",
        idempotency_key="revision-changed",
    ).value
    successor = service.snapshot("poc_a5_test").preparation
    assert successor is not None
    assert successor.contract.version == "2"
    assert successor.contract.parent_version == f"{parent.contract.id}@1"
    assert successor.plan.plan_version == 2
    assert successor.contract.criteria[0].threshold == 0.9
    assert successor.contract.criteria[0].assembly_reviewer == "named.a5.successor"
    assert successor.contract.criteria[0].assembly_rationale == "Review the changed successor."
    assert revision.successor_draft_sha256 == successor.draft_sha256
    assert service.history("poc_a5_test")[0].preparation.contract.model_dump(mode="json") == parent_dump
    with pytest.raises(ReviewInvitationError):
        service.customer_review_payload(old_token)


def test_idempotency_is_poc_and_operation_scoped_and_confirmation_keeps_only_digest():
    p1 = _proposal("poc_a5_one", "prop_a5_one_001")
    p2 = _proposal("poc_a5_two", "prop_a5_two_001")
    retained = {"poc_a5_one": (p1,), "poc_a5_two": (p2,)}
    planner = ProcessLocalCapabilityPlannerService(
        proposal_lookup=lambda poc_id: retained[poc_id], clock=lambda: NOW
    )
    for poc_id, proposal in (("poc_a5_one", p1), ("poc_a5_two", p2)):
        planner.plan(poc_id, (_plan_item(proposal.proposal_id),), idempotency_key="same-a4")
    service = ProcessLocalAgreementLifecycleService(
        poc_lookup=lambda poc_id: _poc(poc_id),
        retained_lookup=lambda poc_id: retained[poc_id],
        planner=planner,
        clock=lambda: NOW,
    )
    service.prepare("poc_a5_one", reviewer="a5", rationale="One", idempotency_key="same-key")
    service.prepare("poc_a5_two", reviewer="a5", rationale="Two", idempotency_key="same-key")
    tokens = [
        service.customer_review_url(poc_id).split("/review/", 1)[1]
        for poc_id in ("poc_a5_one", "poc_a5_two")
    ]
    confirmations = [
        service.record_customer_review_decision(
            token,
            decision="CONFIRM",
            agreement_acknowledged=True,
            rationale="Confirmed.",
            idempotency_key="same-key",
        ).value
        for token in tokens
    ]
    assert confirmations[0].idempotency_key != "same-key"
    assert len(confirmations[0].idempotency_key) == 64
    assert confirmations[0].confirmation_id != confirmations[1].confirmation_id


def test_snapshot_payload_is_coherent_during_concurrent_successor_revision():
    service, planner, _, proposal = _fixture()
    service.prepare(
        "poc_a5_test",
        reviewer="a5",
        rationale="Assemble the first agreement.",
        idempotency_key="coherent-prepare",
    )
    service.record_customer_review_decision(
        _token(service),
        decision="REQUEST_CHANGES",
        agreement_acknowledged=True,
        rationale="Change the threshold.",
        idempotency_key="coherent-request-changes",
    )
    planner.plan(
        "poc_a5_test",
        (_plan_item(proposal.proposal_id, threshold=0.9),),
        idempotency_key="coherent-successor-plan",
    )

    stable_inputs_entered = threading.Event()
    release_stable_inputs = threading.Event()
    original_stable_inputs = service._stable_current_inputs

    def blocked_stable_inputs(poc_id: str):
        stable_inputs_entered.set()
        assert release_stable_inputs.wait(timeout=2)
        return original_stable_inputs(poc_id)

    service._stable_current_inputs = blocked_stable_inputs
    payloads: list[dict[str, object]] = []
    errors: list[Exception] = []

    def read_payload() -> None:
        try:
            payloads.append(service.snapshot_payload("poc_a5_test"))
        except Exception as error:  # pragma: no cover - assertion reports races
            errors.append(error)

    def revise() -> None:
        try:
            service.start_revision(
                "poc_a5_test",
                reviewer="a5.successor",
                rationale="Review the changed successor.",
                idempotency_key="coherent-successor",
            )
        except Exception as error:  # pragma: no cover - assertion reports races
            errors.append(error)

    reader = threading.Thread(target=read_payload)
    reader.start()
    assert stable_inputs_entered.wait(timeout=2)
    successor = threading.Thread(target=revise)
    successor.start()
    release_stable_inputs.set()
    reader.join(timeout=2)
    successor.join(timeout=2)
    assert not reader.is_alive()
    assert not successor.is_alive()
    assert errors == []
    assert len(payloads) == 1

    payload = payloads[0]
    agreement = payload["agreement"]
    review = payload["customer_review"]
    assert isinstance(agreement, dict)
    assert isinstance(review, dict)
    assert payload["draft"]["contract_id"] == agreement["id"] == review["contract_id"]
    assert payload["draft"]["contract_version"] == agreement["version"] == review["contract_version"]
    assert payload["draft"]["contract_version"] == "1"
    current = service.snapshot("poc_a5_test").preparation
    assert current is not None
    assert current.contract.version == "2"


def test_stale_inputs_keep_parent_snapshot_readable_but_block_review_and_freeze():
    service, _, state, _ = _fixture()
    service.prepare(
        "poc_a5_test",
        reviewer="a5",
        rationale="Assemble.",
        idempotency_key="prepare",
    )
    state["retained"] = (_proposal("poc_a5_test", "prop_a5_test_001", digest="d"),)
    snapshot = service.snapshot("poc_a5_test")
    assert snapshot.preparation is not None
    assert snapshot.current_inputs_stale is True
    with pytest.raises(AgreementStale):
        service.customer_review_payload(_token(service))


def test_frozen_handoff_structurally_preserves_non_executable_a4_records():
    poc_id = "poc_a5_records"
    supported = _proposal(poc_id, "prop_a5_records_001")
    excluded = _proposal(poc_id, "prop_a5_records_002")
    retained = (supported, excluded)
    planner = ProcessLocalCapabilityPlannerService(
        proposal_lookup=lambda _: retained, clock=lambda: NOW
    )
    planner.plan(
        poc_id,
        (
            _plan_item(supported.proposal_id),
            PlannerItemInput(
                proposal_id=excluded.proposal_id,
                scope=PlanningScope.ADVISORY,
                capability_key="production_deployment",
                reviewer="named.a4.reviewer",
                rationale="Keep this excluded boundary visible.",
                explicit_exclusion=True,
            ),
        ),
        idempotency_key="records-a4",
    )
    service = ProcessLocalAgreementLifecycleService(
        poc_lookup=lambda _: _poc(poc_id),
        retained_lookup=lambda _: retained,
        planner=planner,
        clock=lambda: NOW,
    )
    preparation = service.prepare(
        poc_id,
        reviewer="named.a5.reviewer",
        rationale="Bind every A4 record structurally.",
        idempotency_key="records-a5",
    ).value
    criteria = preparation.contract.criteria
    assert len(criteria) == 2
    assert {record.proposal_id for record in criteria} == {
        supported.proposal_id,
        excluded.proposal_id,
    }
    excluded_criterion = next(
        record for record in criteria if record.proposal_id == excluded.proposal_id
    )
    assert excluded_criterion.planning_disposition == "UNSUPPORTED"
    assert excluded_criterion.explicit_exclusion is True
    assert excluded_criterion.rule is None
    assert excluded_criterion.adapter is None
    assert excluded_criterion.planning_reason
    assert excluded_criterion.planning_next_action
    assert excluded_criterion.planner_reviewer == "named.a4.reviewer"
    assert excluded_criterion.evidence_binding is None
    assert next(
        record for record in criteria if record.proposal_id == supported.proposal_id
    ).evidence_binding is not None


def test_a5_managed_ttft_binding_is_complete_and_run_independent():
    poc_id = "poc_a5_ttft"
    proposal = _proposal(poc_id, "prop_a5_ttft_001")
    retained = (proposal,)
    planner = ProcessLocalCapabilityPlannerService(
        proposal_lookup=lambda _: retained, clock=lambda: NOW
    )
    planner.plan(
        poc_id,
        (_ttft_plan_item(proposal.proposal_id, threshold=375.0),),
        idempotency_key="ttft-a4",
    )
    service = ProcessLocalAgreementLifecycleService(
        poc_lookup=lambda _: _poc(poc_id),
        retained_lookup=lambda _: retained,
        planner=planner,
        clock=lambda: NOW,
    )
    contract = service.prepare(
        poc_id,
        reviewer="named.a5.reviewer",
        rationale="Bind the managed evidence policy.",
        idempotency_key="ttft-a5",
    ).value.contract
    criterion = contract.criteria[0]
    assert isinstance(criterion, CapabilityCriterion)
    assert criterion.planning_disposition == "EVIDENCE_IMPORT"
    binding = criterion.evidence_binding
    assert binding is not None
    assert binding.binding_type == "EVIDENCE_IMPORT"
    policy = binding.policy
    assert policy.workload_id == "inferdrome.qwen2.5-real-gpu-workload.v1"
    assert policy.workload_digest == (
        "sha256:22bf3389cc29ee946ae567870d7f8d7b458594224542a796e8990c15b1cfcd63"
    )
    assert policy.profile_id == "inferdrome.managed-vllm-0.26-evidence-profile.v1"
    assert policy.profile_digest == (
        "sha256:9d03b5d0822ed829ddbfa4c87c75530885b9ad51ee2c0cb7c5e31a075996fe34"
    )
    assert policy.native_metric == "vllm_first_choices_event_v0_26"
    assert policy.identity.target_engine == "vllm"
    assert policy.identity.target_engine_version == "0.26.0"
    assert policy.identity.target_api == "openai_chat_completions"
    assert policy.identity.target_model == "Qwen/Qwen2.5-0.5B-Instruct"
    assert policy.identity.target_model_revision == (
        "7ae557604adf67be50417f59c2c2f167def9a775"
    )
    assert policy.identity.target_tokenizer_revision == (
        "7ae557604adf67be50417f59c2c2f167def9a775"
    )
    assert policy.identity.include_request_plan is True
    assert policy.identity.expected_execution_fingerprint == (
        "sha256:76d984ea57a0e7cb00520255a6e362f22885d713a875195a7397771937060edd"
    )
    assert policy.identity.target_endpoint == "http://127.0.0.1:18080/"
    assert policy.identity.native_schema_fingerprint == (
        "sha256:3a4fdee6fe9b45ce5b42c41fd3bfc6614245a36ecfe6f94de92b59717a136abb"
    )
    assert policy.identity.local_gpu_proof_schema_id == "urn:inferdrome:local-gpu-proof:v1"
    assert policy.identity.measurement_streaming is True
    assert policy.identity.max_runtime_seconds == 900
    assert policy.identity.reliability_population.exact_attempts == 100
    assert (policy.configured_concurrency, policy.warmup_requests, policy.attempts) == (
        4,
        10,
        100,
    )
    assert policy.minimum_successful_samples == 100
    assert (policy.sampling_seed, policy.sampling_temperature, policy.requested_output_tokens) == (
        42,
        0,
        32,
    )
    assert policy.reducer_id == "nearest_rank_v1"
    assert policy.evidence_method == "EXTERNAL_EVIDENCE_BUNDLE"
    assert policy.measurement_population == (
        "successful_measured_requests_with_observed_ttft"
    )
    assert policy.aggregation_policy == "independent_single_run_no_pooling"
    assert policy.adapter == "vllm_bench_serve"
    assert policy.bundle_verifier_id == (
        "exitspec.inferdrome_bundle.verify_inferdrome_bundle"
    )
    assert policy.bundle_verifier_version == "1.0.0"
    assert policy.invocation_profile_validator_id == (
        "exitspec.inferdrome_managed_profile.validate_managed_invocation_profile"
    )
    assert policy.local_gpu_proof_validator_id == (
        "exitspec.inferdrome_managed_profile.validate_managed_local_gpu_proof"
    )
    assert policy.recalculation_id == "exitspec.inferdrome-recalculation.v1"
    assert policy.importer_calculation_id == (
        "exitspec.inferdrome-managed-importer.v1"
    )
    assert binding.policy_sha256 == capability_evidence_policy_digest(policy)
    serialized = policy.model_dump(mode="json")
    observed_keys = _nested_keys(serialized)
    forbidden_runtime_keys = {
        "request_plan_digest",
        "run_id",
        "bundle_id",
        "bundle_digest",
        "measurements",
        "receipts",
        "producer_verdict",
        "producer_link",
    }
    assert observed_keys.isdisjoint(forbidden_runtime_keys)
    assert {
        "bundle_verifier_id",
        "include_request_plan",
        "expected_execution_fingerprint",
    } <= observed_keys


def test_a5_rejects_forged_or_incomplete_evidence_bindings_before_publication():
    service, _, _, _ = _fixture()
    contract = service.prepare(
        "poc_a5_test",
        reviewer="a5",
        rationale="Assemble.",
        idempotency_key="binding-validation",
    ).value.contract
    criterion = contract.criteria[0]

    def validate_with_criterion(updated):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            payload = contract.model_dump(mode="python")
            payload["criteria"] = (updated.model_dump(mode="python"),)
            return POCContract.model_validate(payload)

    forged_digest = criterion.evidence_binding.model_copy(
        update={"policy_sha256": "0" * 64}
    )
    with pytest.raises(ValueError, match="policy digest"):
        validate_with_criterion(criterion.model_copy(update={"evidence_binding": forged_digest}))

    forged_workload = criterion.evidence_binding.policy.model_copy(
        update={"workload_sha256": "0" * 64}
    )
    with pytest.raises(ValueError):
        validate_with_criterion(
            criterion.model_copy(
                update={
                    "evidence_binding": criterion.evidence_binding.model_copy(
                        update={"policy": forged_workload}
                    )
                }
            )
        )

    ttft_poc_id = "poc_a5_ttft_forged"
    ttft_proposal = _proposal(ttft_poc_id, "prop_a5_ttft_002")
    ttft_retained = (ttft_proposal,)
    ttft_planner = ProcessLocalCapabilityPlannerService(
        proposal_lookup=lambda _: ttft_retained, clock=lambda: NOW
    )
    ttft_planner.plan(
        ttft_poc_id,
        (_ttft_plan_item(ttft_proposal.proposal_id),),
        idempotency_key="ttft-forged-a4",
    )
    ttft_service = ProcessLocalAgreementLifecycleService(
        poc_lookup=lambda _: _poc(ttft_poc_id),
        retained_lookup=lambda _: ttft_retained,
        planner=ttft_planner,
        clock=lambda: NOW,
    )
    ttft_contract = ttft_service.prepare(
        ttft_poc_id,
        reviewer="a5",
        rationale="Assemble TTFT.",
        idempotency_key="ttft-forged-a5",
    ).value.contract
    ttft_criterion = ttft_contract.criteria[0]
    ttft_binding = ttft_criterion.evidence_binding
    assert ttft_binding is not None

    def validate_ttft_with_policy(policy):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            payload = ttft_contract.model_dump(mode="python")
            payload["criteria"] = (
                ttft_criterion.model_copy(
                    update={
                        "evidence_binding": ttft_binding.model_copy(
                            update={"policy": policy}
                        )
                    }
                ).model_dump(mode="python"),
            )
            return POCContract.model_validate(payload)

    with pytest.raises(ValueError):
        validate_ttft_with_policy(ttft_binding.policy.model_copy(update={"profile_id": "forged-profile"}))
    with pytest.raises(ValueError):
        validate_ttft_with_policy(ttft_binding.policy.model_copy(update={"workload_id": "forged-workload"}))
    with pytest.raises(ValueError):
        validate_ttft_with_policy(
            ttft_binding.policy.model_copy(update={"minimum_successful_samples": 99})
        )
    omitted_samples = ttft_binding.policy.model_dump(mode="python")
    omitted_samples.pop("minimum_successful_samples")
    with pytest.raises(ValueError):
        validate_ttft_with_policy(omitted_samples)
    with pytest.raises(ValueError):
        validate_ttft_with_policy(
            ttft_binding.policy.model_copy(
                update={
                    "identity": ttft_binding.policy.identity.model_copy(
                        update={"target_model": "forged-model"}
                    )
                }
            )
        )
    with pytest.raises(ValueError):
        validate_ttft_with_policy(
            ttft_binding.policy.model_copy(
                update={
                    "identity": ttft_binding.policy.identity.model_copy(
                        update={"target_endpoint": "http://evil.test/"}
                    )
                }
            )
        )

    incomplete = criterion.model_dump(mode="python")
    incomplete.pop("evidence_binding")
    with pytest.raises(ValueError, match="incomplete or excluded"):
        CapabilityCriterion.model_validate(incomplete)

    invalid_operator = _plan_item("prop_a5_test_001")
    invalid_operator = invalid_operator.model_copy(
        update={
            "criterion": invalid_operator.criterion.model_copy(update={"operator": "GT"})
        }
    )
    with pytest.raises(CapabilityPlanningInvalid, match="server-owned capability policy"):
        ProcessLocalCapabilityPlannerService(
            proposal_lookup=lambda _: (_proposal("poc_a5_test", "prop_a5_test_001"),),
            clock=lambda: NOW,
        ).plan(
            "poc_a5_test",
            (invalid_operator,),
            idempotency_key="invalid-operator",
        )


def test_binding_material_mutations_change_confirmation_fingerprint_and_contract_hash():
    service, _, _, _ = _fixture()
    contract = service.prepare(
        "poc_a5_test",
        reviewer="a5",
        rationale="Assemble.",
        idempotency_key="binding-mutation",
    ).value.contract
    criterion = contract.criteria[0]
    binding = criterion.evidence_binding
    assert binding is not None
    baseline_fingerprint = contract_confirmation_fingerprint(contract)
    from exitspec.contracts import contract_digest

    baseline_hash = contract_digest(contract)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for field_name in type(binding.policy).model_fields:
            value = getattr(binding.policy, field_name)
            if isinstance(value, str):
                changed = value + "-changed"
            elif isinstance(value, int):
                changed = value + 1
            elif isinstance(value, float):
                changed = value + 0.01
            else:
                continue
            changed_policy = binding.policy.model_copy(update={field_name: changed})
            changed_binding = binding.model_copy(update={"policy": changed_policy})
            changed_criterion = criterion.model_copy(update={"evidence_binding": changed_binding})
            changed_contract = contract.model_copy(update={"criteria": (changed_criterion,)})
            assert contract_confirmation_fingerprint(changed_contract) != baseline_fingerprint
            assert contract_digest(changed_contract) != baseline_hash
        changed_binding = binding.model_copy(update={"policy_sha256": "0" * 64})
        changed_contract = contract.model_copy(
            update={"criteria": (criterion.model_copy(update={"evidence_binding": changed_binding}),)}
        )
        assert contract_confirmation_fingerprint(changed_contract) != baseline_fingerprint


def test_managed_minimum_successful_samples_changes_policy_and_contract_authority():
    poc_id = "poc_a5_ttft_mutation"
    proposal = _proposal(poc_id, "prop_a5_ttft_mutation")
    planner = ProcessLocalCapabilityPlannerService(
        proposal_lookup=lambda _: (proposal,), clock=lambda: NOW
    )
    planner.plan(
        poc_id,
        (_ttft_plan_item(proposal.proposal_id),),
        idempotency_key="ttft-mutation-a4",
    )
    service = ProcessLocalAgreementLifecycleService(
        poc_lookup=lambda _: _poc(poc_id),
        retained_lookup=lambda _: (proposal,),
        planner=planner,
        clock=lambda: NOW,
    )
    contract = service.prepare(
        poc_id,
        reviewer="a5",
        rationale="Bind managed minimum successful samples.",
        idempotency_key="ttft-mutation-a5",
    ).value.contract
    criterion = contract.criteria[0]
    binding = criterion.evidence_binding
    assert binding is not None
    assert binding.policy.minimum_successful_samples == 100
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        changed_policy = binding.policy.model_copy(
            update={"minimum_successful_samples": 101}
        )
        changed_contract = contract.model_copy(
            update={
                "criteria": (
                    criterion.model_copy(
                        update={
                            "evidence_binding": binding.model_copy(
                                update={"policy": changed_policy}
                            )
                        }
                    ),
                )
            }
        )
        assert capability_evidence_policy_digest(changed_policy) != binding.policy_sha256
        assert (
            contract_confirmation_fingerprint(changed_contract)
            != contract_confirmation_fingerprint(contract)
        )
        from exitspec.contracts import contract_digest

        assert contract_digest(changed_contract) != contract_digest(contract)


def test_every_customer_visible_material_category_changes_confirmation_fingerprint():
    service, _, _, _ = _fixture()
    contract = service.prepare(
        "poc_a5_test",
        reviewer="a5",
        rationale="Assemble.",
        idempotency_key="prepare",
    ).value.contract
    baseline = contract_confirmation_fingerprint(contract)
    criterion = contract.criteria[0]
    variants = (
        contract.model_copy(update={"customer": "Different customer"}),
        contract.model_copy(update={"use_case": "Different use case"}),
        contract.model_copy(update={
            "target_system": contract.target_system.model_copy(update={"model": "different"})
        }),
        contract.model_copy(update={
            "workload": contract.workload.model_copy(update={"fixture_path": "policy://different"})
        }),
        contract.model_copy(update={
            "criteria": (criterion.model_copy(update={"normalized_claim": "Different claim"}),)
        }),
        contract.model_copy(update={"owners": ("different-owner",)}),
        contract.model_copy(update={"non_goals": ("Different limitation",)}),
        contract.model_copy(update={"evidence_retention_policy": "Different policy"}),
    )
    assert all(contract_confirmation_fingerprint(variant) != baseline for variant in variants)


def test_capability_contract_rejects_duplicate_or_inconsistent_handoff_bindings():
    service, _, _, _ = _fixture()
    contract = service.prepare(
        "poc_a5_test",
        reviewer="a5",
        rationale="Assemble.",
        idempotency_key="prepare",
    ).value.contract
    first = contract.criteria[0].model_dump(mode="python")
    duplicate = contract.model_dump(mode="python")
    duplicate_second = dict(first)
    duplicate_second["id"] = "CAP-DUPLICATE-BINDING"
    duplicate["criteria"] = (first, duplicate_second)
    with pytest.raises(ValueError, match="proposal IDs must be unique"):
        POCContract.model_validate(duplicate)
    inconsistent = contract.model_dump(mode="python")
    altered = dict(first)
    altered["id"] = "CAP-INCONSISTENT-BINDING"
    altered["proposal_id"] = "prop_a5_test_002"
    altered["planning_item_id"] = "cpitem_" + "1" * 32
    altered["a4_plan_version"] = first["a4_plan_version"] + 1
    inconsistent["criteria"] = (first, altered)
    with pytest.raises(ValueError, match="one A4 plan binding"):
        POCContract.model_validate(inconsistent)
    non_executable = dict(first)
    non_executable.update({"planning_disposition": "UNSUPPORTED", "rule": "forged"})
    with pytest.raises(ValueError, match="non-executable capability record"):
        CapabilityCriterion.model_validate(non_executable)


def test_revision_api_requires_and_forwards_fresh_successor_assembly_approval():
    service, planner, _, proposal = _fixture()
    service.prepare(
        "poc_a5_test",
        reviewer="a5",
        rationale="Assemble.",
        idempotency_key="prepare",
    )
    token = _token(service)
    service.record_customer_review_decision(
        token,
        decision="REQUEST_CHANGES",
        agreement_acknowledged=True,
        rationale="Change the threshold.",
        idempotency_key="changes",
    )
    planner.plan(
        "poc_a5_test",
        (_plan_item(proposal.proposal_id, threshold=0.9),),
        idempotency_key="a4-v2",
    )
    missing = handle_poc_agreement_web_api_request(
        method="POST",
        target="/api/pocs/poc_a5_test/agreement/revision",
        payload={"idempotency_key": "revision"},
        runtime=service,
    )
    assert missing is not None and missing.status == 400
    created = handle_poc_agreement_web_api_request(
        method="POST",
        target="/api/pocs/poc_a5_test/agreement/revision",
        payload={
            "reviewer": "fresh.successor.reviewer",
            "rationale": "Approve the changed successor contents.",
            "idempotency_key": "revision",
        },
        runtime=service,
    )
    assert created is not None and created.status == 201
    assert created.payload["revision"]["assembly_reviewer"] == "fresh.successor.reviewer"


def test_reentrant_dependency_cannot_publish_nested_agreement():
    poc_id = "poc_a5_reentrant"
    proposal = _proposal(poc_id, "prop_a5_reentrant_001")
    planner = ProcessLocalCapabilityPlannerService(
        proposal_lookup=lambda _: (proposal,), clock=lambda: NOW
    )
    planner.plan(poc_id, (_plan_item(proposal.proposal_id),), idempotency_key="a4")
    holder: dict[str, object] = {}
    reentry_errors: list[Exception] = []
    called = False

    def lookup(_: str):
        nonlocal called
        if not called:
            called = True
            try:
                holder["service"].prepare(
                    poc_id,
                    reviewer="nested",
                    rationale="Nested.",
                    idempotency_key="nested",
                )
            except Exception as error:  # the assertion below checks the typed refusal
                reentry_errors.append(error)
        return _poc(poc_id)

    service = ProcessLocalAgreementLifecycleService(
        poc_lookup=lookup,
        retained_lookup=lambda _: (proposal,),
        planner=planner,
        clock=lambda: NOW,
    )
    holder["service"] = service
    service.prepare(poc_id, reviewer="outer", rationale="Outer.", idempotency_key="outer")
    assert len(reentry_errors) == 1
    assert isinstance(reentry_errors[0], AgreementConflict)
    assert service.snapshot(poc_id).preparation is not None


def test_concurrent_same_key_publishes_once_then_replays():
    service, _, _, _ = _fixture()
    barrier = threading.Barrier(2)

    def prepare():
        barrier.wait()
        return service.prepare(
            "poc_a5_test",
            reviewer="a5",
            rationale="Concurrent preparation.",
            idempotency_key="concurrent",
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: prepare(), range(2)))
    assert sorted(result.replayed for result in results) == [False, True]


def test_a5_closure_artifact_is_strictly_scoped_to_gl07_through_gl10():
    artifact = json.loads(
        (
            Path(__file__).parents[1]
            / "examples"
            / "product"
            / "request-to-proof-a5-closure-evidence-v1.json"
        ).read_text(encoding="utf-8")
    )
    assert set(artifact) == {
        "schema_version",
        "train_slice",
        "status",
        "scope",
        "claims",
        "authority_boundary",
        "limitations",
        "frozen_baseline_edited",
    }
    assert artifact["schema_version"] == "exitspec.request-to-proof-a5-closure-evidence.v1"
    assert artifact["train_slice"] == "A5"
    assert artifact["scope"] == "GL-07 through GL-10 only"
    assert set(artifact["claims"]) == {"GL-07", "GL-08", "GL-09", "GL-10"}
    assert artifact["frozen_baseline_edited"] is False
    for claim in artifact["claims"].values():
        assert claim["statement"]
        assert claim["proof"]
        assert all(reference.startswith("tests/") for reference in claim["proof"])
