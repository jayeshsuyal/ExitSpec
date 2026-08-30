from datetime import datetime, timezone
import json
import os
from threading import Event, Thread

import pytest
from pydantic import ValidationError

from exitspec.confirmations import ConfirmationDecision, record_confirmation
from exitspec.contracts import freeze_confirmed_contract
from exitspec.generic_evidence_pack import GenericEvidencePackError
from exitspec.models import (
    CapabilityCriterion,
    CapabilityEvidenceBinding,
    ContractStatus,
    ExactToolSelectionEvidencePolicy,
    POCContract,
    SourceReference,
    TargetSystem,
    WorkloadReference,
    capability_evidence_policy_digest,
)
from exitspec.poc_evidence_orchestration import (
    CriterionEvidenceResult,
    EXECUTABLE_SYNTHETIC_PROFILE,
    ExecutableOrchestrationConflict,
    ExecutableOrchestrationInvalid,
    EvidenceAttemptStatus,
    ProcessLocalEvidenceOrchestrationService,
    ProcessLocalExecutableEvidenceService,
    reduce_criterion_results,
)
from exitspec.inferdrome_catalog import InferdromeBundleCatalog
from exitspec.poc_agreement import ProcessLocalAgreementLifecycleService
from exitspec.poc_capability_planner import ProcessLocalCapabilityPlannerService
from tests.test_poc_agreement import _poc, _proposal, _ttft_plan_item
from tests.inferdrome_managed_helpers import extract_exact_archive_or_skip


NOW = datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc)


def _frozen_contract() -> tuple[POCContract, object]:
    policy = ExactToolSelectionEvidencePolicy(
        policy_id="support-tool-selection-v1",
        capability_key="exact_tool_selection",
        rule="exact_tool_selection_rate",
        operator="GTE",
        threshold=0.90,
        unit="PROPORTION",
        measurement_population="approved_synthetic_cases",
        evidence_method="EXIT_SPEC_STREAMING_PROBE",
        workload_path="examples/support-agent/fixtures/tool-selection-200.json",
        workload_sha256="75ef6f83450de100a920e9489a0b5966464f1dba2e3d339c4b57e64fb95d8271",
        workload_slice="support-tool-selection-v1",
        minimum_samples=200,
        confidence_level=0.95,
        confidence_method="wilson_two_sided_lower_bound",
        calculator_id="exitspec.statistics.wilson_lower_bound",
        calculator_version="wilson-two-sided-v1",
        adapter="deterministic_tool_selection",
        adapter_version="1.0.0",
        verifier_id="exitspec.verdicts.evaluate_proportion_criterion",
        reducer_id="exitspec.verdicts.aggregate_overall_verdict",
    )
    criterion = CapabilityCriterion(
        id="CAP-TOOL-001",
        title="Exact tool selection",
        must_have=True,
        source=SourceReference(
            speaker="customer",
            quote="Select the exact tool.",
            location="document:1",
        ),
        normalized_claim="Select the exact tool.",
        poc_id="poc_a6_executable_test",
        capability_key="exact_tool_selection",
        planning_scope="MUST_HAVE",
        planning_disposition="EXECUTABLE",
        provenance="SOURCE_EXTRACTED",
        planning_item_id="cpitem_" + "a" * 32,
        proposal_id="prop_a6_test_001",
        proposal_key="proposal-a6-tool",
        source_receipt_id="srcpt_a6_test_001",
        source_id="src_a6_test",
        source_kind="DOCUMENT",
        source_content_sha256="b" * 64,
        source_revision=1,
        source_adapter_name="exitspec_document",
        source_adapter_version="1.0.0",
        redaction_policy_version="redaction-v1",
        authoring_receipt_id="arcp_" + "c" * 32,
        authoring_result_id="ares_" + "d" * 32,
        a4_plan_id="cplan_" + "e" * 32,
        a4_plan_version=1,
        a4_plan_sha256="f" * 64,
        planner_reviewer="a4.reviewer",
        planner_rationale="Use the exact server-owned policy.",
        planning_reason="Supported executable capability.",
        planning_next_action="Run the frozen deterministic probe.",
        assembly_reviewer="a5.reviewer",
        assembly_rationale="Assemble the exact A4 plan.",
        rule=policy.rule,
        operator=policy.operator,
        threshold=policy.threshold,
        unit=policy.unit,
        measurement_population=policy.measurement_population,
        evidence_method=policy.evidence_method,
        adapter=policy.adapter,
        adapter_version=policy.adapter_version,
        evidence_binding=CapabilityEvidenceBinding(
            binding_type="EXECUTABLE",
            policy=policy,
            policy_sha256=capability_evidence_policy_digest(policy),
        ),
        owner="field_engineer",
        evidence_policy=policy.policy_id,
        approved=True,
    )
    approved = POCContract(
        id="a6-executable-poc",
        version="1",
        status="APPROVED",
        created_at=NOW,
        approved_at=NOW,
        customer="A6 customer",
        use_case="Prove one exact server-owned capability.",
        target_system=TargetSystem(
            provider="local-exitspec",
            endpoint_class="deterministic-fixture",
            model="fixture-model",
        ),
        workload=WorkloadReference(
            fixture_path=policy.workload_path,
            sha256=policy.workload_sha256,
        ),
        criteria=(criterion,),
        owners=("field_engineer",),
        evidence_retention_policy="retain immutable evidence",
    )
    confirmation = record_confirmation(
        approved,
        confirmer_identity="A6 customer approver",
        decision=ConfirmationDecision.CONFIRM,
        agreement_acknowledged=True,
        rationale="I confirm the exact executable criterion.",
        idempotency_key="confirm-a6",
        decided_at=NOW,
    )
    return freeze_confirmed_contract(approved, confirmation, frozen_at=NOW), confirmation


def test_executable_method_is_server_selected_and_replays_immutably():
    frozen, confirmation = _frozen_contract()
    service = ProcessLocalExecutableEvidenceService(
        contract_lookup=lambda _: frozen,
        confirmation_lookup=lambda _: confirmation,
        clock=lambda: NOW,
    )

    started = service.start(
        "poc_a6_executable_test",
        acknowledgement=True,
        idempotency_key="run-a6",
    )
    attempt = started.attempt
    assert started.replayed is False
    assert attempt.method == "EXECUTABLE"
    assert attempt.adapter_id == "deterministic_tool_selection"
    assert attempt.execution_profile == EXECUTABLE_SYNTHETIC_PROFILE
    assert "not real endpoint proof" in " ".join(attempt.limitations)
    assert attempt.workload_path == "examples/support-agent/fixtures/tool-selection-200.json"
    assert attempt.sample_count == 200
    assert attempt.success_count == 197
    assert attempt.verdict.value == "PASS"
    assert attempt.contract_hash == frozen.canonical_hash
    assert attempt.confirmation_id == confirmation.confirmation_id

    replay = service.start(
        "poc_a6_executable_test",
        acknowledgement=True,
        idempotency_key="run-a6",
    )
    assert replay.replayed is True
    assert replay.attempt == attempt

    with pytest.raises(TypeError):
        service.start(
            "poc_a6_executable_test",
            acknowledgement=True,
            idempotency_key="run-a6-other",
            method="EXECUTABLE",
        )


def test_executable_method_rejects_cross_poc_generic_criteria():
    frozen, confirmation = _frozen_contract()
    service = ProcessLocalExecutableEvidenceService(
        contract_lookup=lambda _: frozen,
        confirmation_lookup=lambda _: confirmation,
        clock=lambda: NOW,
    )

    with pytest.raises(ExecutableOrchestrationConflict, match="requested POC"):
        service.start(
            "poc_a6_other_test",
            acknowledgement=True,
            idempotency_key="run-cross-poc",
        )


def test_reservation_displaces_old_current_before_late_completion():
    frozen, confirmation = _frozen_contract()
    service = ProcessLocalExecutableEvidenceService(
        contract_lookup=lambda _: frozen,
        confirmation_lookup=lambda _: confirmation,
        clock=lambda: NOW,
    )
    entered = Event()
    release = Event()
    original_execute = service._execute

    def blocked_execute(**kwargs):
        entered.set()
        assert release.wait(timeout=2)
        return original_execute(**kwargs)

    service._execute = blocked_execute
    first_results = []
    worker = Thread(
        target=lambda: first_results.append(
            service.start(
                "poc_a6_executable_test",
                acknowledgement=True,
                idempotency_key="run-late-first",
            )
        )
    )
    worker.start()
    assert entered.wait(timeout=2)
    first_id = service.current("poc_a6_executable_test").attempt_id

    successor = service.reserve(
        "poc_a6_executable_test",
        acknowledgement=True,
        idempotency_key="run-late-successor",
    )
    assert service.current("poc_a6_executable_test").attempt_id == successor.attempt.attempt_id
    assert service.attempt(first_id).status.value == "STALE"
    release.set()
    worker.join(timeout=2)
    assert not worker.is_alive()
    first = first_results[0].attempt
    assert first.status.value == "STALE"
    assert first.is_current is False
    assert first.verdict.value == "PASS"
    assert service.current("poc_a6_executable_test").attempt_id == successor.attempt.attempt_id


def test_cancelled_and_failed_attempts_have_no_acceptance_verdict():
    frozen, confirmation = _frozen_contract()
    service = ProcessLocalExecutableEvidenceService(
        contract_lookup=lambda _: frozen,
        confirmation_lookup=lambda _: confirmation,
        clock=lambda: NOW,
    )
    reserved = service.reserve(
        "poc_a6_executable_test",
        acknowledgement=True,
        idempotency_key="run-cancel",
    )
    cancelled = service.cancel(reserved.attempt.attempt_id)
    assert cancelled.status.value == "CANCELLED"
    assert cancelled.verdict is None
    assert service.execute(cancelled.attempt_id).attempt.status.value == "CANCELLED"

    failing = ProcessLocalExecutableEvidenceService(
        contract_lookup=lambda _: frozen,
        confirmation_lookup=lambda _: confirmation,
        clock=lambda: NOW,
    )
    failing._execute = lambda **_: (_ for _ in ()).throw(RuntimeError("boom"))
    failed = failing.start(
        "poc_a6_executable_test",
        acknowledgement=True,
        idempotency_key="run-fail",
    )
    assert failed.attempt.status.value == "FAILED_INTERNAL"
    assert failed.attempt.verdict is None


def test_generic_orchestration_selects_frozen_executable_binding():
    frozen, confirmation = _frozen_contract()
    service = ProcessLocalEvidenceOrchestrationService(
        contract_lookup=lambda _: frozen,
        confirmation_lookup=lambda _: confirmation,
        clock=lambda: NOW,
    )

    started = service.start(
        "poc_a6_executable_test",
        acknowledgement=True,
        idempotency_key="generic-a6-executable",
    )
    assert started.replayed is False
    assert started.attempt.status is EvidenceAttemptStatus.COMPLETED
    assert started.attempt.reduction is not None
    assert started.attempt.reduction.verdict.value == "PASS"
    assert [item.method.value for item in started.attempt.method_identities] == [
        "EXECUTABLE"
    ]
    assert started.attempt.results[0].verdict.value == "PASS"
    assert "not real endpoint proof" in " ".join(started.attempt.results[0].limitations)

    replay = service.start(
        "poc_a6_executable_test",
        acknowledgement=True,
        idempotency_key="generic-a6-executable",
    )
    assert replay.replayed is True
    assert replay.attempt == started.attempt


def test_generic_rerun_allocates_fresh_operation_and_run_identities():
    frozen, confirmation = _frozen_contract()
    service = ProcessLocalEvidenceOrchestrationService(
        contract_lookup=lambda _: frozen,
        confirmation_lookup=lambda _: confirmation,
        clock=lambda: NOW,
    )
    first = service.start(
        "poc_a6_executable_test",
        acknowledgement=True,
        idempotency_key="generic-a6-rerun-one",
    ).attempt
    second = service.start(
        "poc_a6_executable_test",
        acknowledgement=True,
        idempotency_key="generic-a6-rerun-two",
    ).attempt
    assert first.operation_id != second.operation_id
    assert first.run_id != second.run_id
    replay = service.start(
        "poc_a6_executable_test",
        acknowledgement=True,
        idempotency_key="generic-a6-rerun-one",
    )
    assert replay.attempt.operation_id == first.operation_id
    assert replay.attempt.run_id == first.run_id


def test_generic_successor_stays_current_when_predecessor_finishes_late(tmp_path):
    service = _pack_service(tmp_path)
    entered = Event()
    release = Event()
    original_evaluate = service._evaluate_record

    def blocked_evaluate(record):
        entered.set()
        assert release.wait(timeout=2)
        return original_evaluate(record)

    service._evaluate_record = blocked_evaluate
    first_results = []
    worker = Thread(
        target=lambda: first_results.append(
            service.start(
                "poc_a6_executable_test",
                acknowledgement=True,
                idempotency_key="generic-a6-late-first",
            )
        )
    )
    worker.start()
    assert entered.wait(timeout=2)
    first_id = service.current("poc_a6_executable_test").attempt_id

    successor = service.reserve(
        "poc_a6_executable_test",
        acknowledgement=True,
        idempotency_key="generic-a6-late-successor",
    ).attempt
    assert service.current("poc_a6_executable_test").attempt_id == successor.attempt_id
    assert service.attempt(first_id).status is EvidenceAttemptStatus.STALE

    release.set()
    worker.join(timeout=2)
    assert not worker.is_alive()
    assert len(first_results) == 1
    predecessor = first_results[0].attempt
    assert predecessor.status is EvidenceAttemptStatus.STALE
    assert predecessor.is_current is False
    assert predecessor.results
    assert predecessor.evidence_pack_url is None
    assert predecessor.evidence_pack_sha256 is None
    assert service.current("poc_a6_executable_test").attempt_id == successor.attempt_id
    assert service.attempt(successor.attempt_id).is_current is True


def _frozen_managed_contract() -> tuple[object, object]:
    poc_id = "poc_a6_managed_test"
    proposal = _proposal(poc_id, "prop_a6_managed_001")
    planner = ProcessLocalCapabilityPlannerService(
        proposal_lookup=lambda _: (proposal,),
        clock=lambda: NOW,
    )
    planner.plan(
        poc_id,
        (_ttft_plan_item(proposal.proposal_id, threshold=375.0),),
        idempotency_key="a6-managed-plan",
    )
    agreement = ProcessLocalAgreementLifecycleService(
        poc_lookup=lambda _: _poc(poc_id),
        retained_lookup=lambda _: (proposal,),
        planner=planner,
        clock=lambda: NOW,
    )
    agreement.prepare(
        poc_id,
        reviewer="a6.reviewer",
        rationale="Freeze the managed import criterion.",
        idempotency_key="a6-managed-prepare",
    )
    token = agreement.customer_review_url(poc_id).split("/review/", 1)[1]
    agreement.record_customer_review_decision(
        token,
        decision="CONFIRM",
        agreement_acknowledged=True,
        rationale="I confirm the managed import criterion.",
        idempotency_key="a6-managed-confirm",
    )
    frozen = agreement.freeze(poc_id, idempotency_key="a6-managed-freeze").value
    managed_approved = POCContract.model_validate(
        frozen.model_copy(
            update={
                "status": ContractStatus.APPROVED,
                "frozen_at": None,
                "confirmation_id": None,
                "canonical_hash": None,
                "target_system": TargetSystem(
                    provider="inferdrome-managed-vllm",
                    endpoint_class="retained-loopback-vllm-benchmark",
                    model="Qwen/Qwen2.5-0.5B-Instruct",
                ),
                "workload": WorkloadReference(
                    fixture_path="external://inferdrome/a10/workload",
                    sha256="22bf3389cc29ee946ae567870d7f8d7b458594224542a796e8990c15b1cfcd63",
                ),
            }
        ).model_dump(mode="python")
    )
    confirmation = record_confirmation(
        managed_approved,
        confirmer_identity="A6 managed customer approver",
        decision=ConfirmationDecision.CONFIRM,
        agreement_acknowledged=True,
        rationale="I confirm the managed import criterion.",
        idempotency_key="a6-managed-generic-confirm",
        decided_at=NOW,
    )
    return freeze_confirmed_contract(managed_approved, confirmation, frozen_at=NOW), confirmation


def test_generic_import_unknown_catalog_evidence_is_ingestion_rejected(tmp_path):
    frozen, confirmation = _frozen_managed_contract()
    service = ProcessLocalEvidenceOrchestrationService(
        contract_lookup=lambda _: frozen,
        confirmation_lookup=lambda _: confirmation,
        catalog=InferdromeBundleCatalog(tmp_path.resolve()),
        clock=lambda: NOW,
    )

    started = service.start(
        "poc_a6_managed_test",
        acknowledgement=True,
        idempotency_key="generic-a6-unknown-import",
        catalog_evidence_ref="evref_" + "0" * 64,
    )
    assert started.attempt.status is EvidenceAttemptStatus.INGESTION_REJECTED
    assert started.attempt.reduction is None
    assert started.attempt.results[0].ingestion_status == "INGESTION_REJECTED"
    assert started.attempt.results[0].verdict is None
    assert started.attempt.evidence_pack_url is None


def test_generic_managed_import_uses_catalog_reference_and_retains_independent_facts(
    tmp_path,
):
    extracted = extract_exact_archive_or_skip(tmp_path)
    frozen, confirmation = _frozen_managed_contract()
    catalog = InferdromeBundleCatalog(extracted.bundle_path)
    entry = catalog.refresh().entries[0]
    reference = catalog.evidence_reference(entry.run_id, entry.bundle_digest)
    service = ProcessLocalEvidenceOrchestrationService(
        contract_lookup=lambda _: frozen,
        confirmation_lookup=lambda _: confirmation,
        catalog=catalog,
        clock=lambda: NOW,
    )

    started = service.start(
        "poc_a6_managed_test",
        acknowledgement=True,
        idempotency_key="generic-a6-exact-managed-import",
        catalog_evidence_ref=reference,
    )

    assert started.replayed is False
    assert started.attempt.status is EvidenceAttemptStatus.COMPLETED
    assert started.attempt.reduction is not None
    assert started.attempt.reduction.verdict.value == "PASS"
    result = started.attempt.results[0]
    assert result.ingestion_status == "ADMITTED"
    assert result.verdict.value == "PASS"
    assert result.observed_ttft_p95_ns == 14_797_213
    assert result.observed_latency_population == (
        "successful_measured_requests_with_observed_ttft"
    )
    assert result.sample_count == 100
    assert result.success_count == 100
    assert result.bundle_digest == entry.bundle_digest.removeprefix("sha256:")
    assert result.recalculation_sha256 is not None
    assert result.receipt_id is not None and result.receipt_id.startswith("irc2_")
    assert result.receipt_sha256 is not None
    assert "producer_verdict" not in result.model_dump(mode="json")
    assert "ExitSpec independently calculated" in result.reason


def _frozen_unsupported_contract() -> tuple[POCContract, object]:
    frozen, _ = _frozen_contract()
    base = frozen.criteria[0]
    unsupported = CapabilityCriterion.model_validate(
        base.model_copy(
            update={
                "id": "CAP-UNSUPPORTED-001",
                "planning_disposition": "UNSUPPORTED",
                "provenance": None,
                "rule": None,
                "operator": None,
                "threshold": None,
                "unit": None,
                "measurement_population": None,
                "evidence_method": None,
                "adapter": None,
                "adapter_version": None,
                "evidence_profile": None,
                "evidence_binding": None,
                "planning_reason": "The requested capability is unsupported.",
                "planning_next_action": "Clarify the unsupported claim or stop.",
            }
        ).model_dump(mode="python")
    )
    approved = POCContract.model_validate(
        frozen.model_copy(
            update={
                "status": ContractStatus.APPROVED,
                "frozen_at": None,
                "confirmation_id": None,
                "canonical_hash": None,
                "criteria": (unsupported,),
            }
        ).model_dump(mode="python")
    )
    confirmation = record_confirmation(
        approved,
        confirmer_identity="A6 unsupported customer approver",
        decision=ConfirmationDecision.CONFIRM,
        agreement_acknowledged=True,
        rationale="I confirm the unsupported claim remains visible.",
        idempotency_key="a6-unsupported-confirm",
        decided_at=NOW,
    )
    return freeze_confirmed_contract(approved, confirmation, frozen_at=NOW), confirmation


def test_generic_unsupported_must_have_is_completed_not_proven_without_import():
    frozen, confirmation = _frozen_unsupported_contract()
    service = ProcessLocalEvidenceOrchestrationService(
        contract_lookup=lambda _: frozen,
        confirmation_lookup=lambda _: confirmation,
        clock=lambda: NOW,
    )

    started = service.start(
        "poc_a6_executable_test",
        acknowledgement=True,
        idempotency_key="generic-a6-unsupported-claim",
    )

    assert started.attempt.status is EvidenceAttemptStatus.COMPLETED
    assert started.attempt.reduction is not None
    assert started.attempt.reduction.verdict.value == "NOT_PROVEN"
    assert started.attempt.results[0].ingestion_status == "ADMITTED"
    assert started.attempt.results[0].verdict.value == "NOT_PROVEN"


def test_generic_import_rejects_path_like_catalog_reference_before_reservation(tmp_path):
    frozen, confirmation = _frozen_managed_contract()
    service = ProcessLocalEvidenceOrchestrationService(
        contract_lookup=lambda _: frozen,
        confirmation_lookup=lambda _: confirmation,
        catalog=InferdromeBundleCatalog(tmp_path.resolve()),
        clock=lambda: NOW,
    )

    with pytest.raises(ExecutableOrchestrationInvalid, match="catalog_evidence_ref"):
        service.start(
            "poc_a6_managed_test",
            acknowledgement=True,
            idempotency_key="generic-a6-path-import",
            catalog_evidence_ref="../../private/bundle",
        )


def test_criterion_result_invariants_fail_closed():
    common = {
        "criterion_id": "CAP-TOOL-001",
        "scope": "MUST_HAVE",
        "planning_disposition": "EXECUTABLE",
        "reason": "invalid test result",
    }
    with pytest.raises(ValidationError, match="INGESTION_REJECTED"):
        CriterionEvidenceResult(
            **common,
            ingestion_status="INGESTION_REJECTED",
            verdict="PASS",
        )
    with pytest.raises(ValidationError, match="ADMITTED terminal"):
        CriterionEvidenceResult(
            **common,
            ingestion_status="ADMITTED",
            verdict=None,
        )
    with pytest.raises(ValidationError, match="success_count"):
        CriterionEvidenceResult(
            **common,
            ingestion_status="ADMITTED",
            verdict="PASS",
            sample_count=1,
            success_count=2,
        )


def test_reducer_keeps_advisory_and_excluded_limitations_visible():
    frozen, _ = _frozen_contract()
    base = frozen.criteria[0]
    must_fail = base.model_copy(update={"id": "CAP-TOOL-002"})
    must_block = base.model_copy(update={"id": "CAP-TOOL-003"})
    advisory = base.model_copy(
        update={"id": "CAP-TOOL-004", "must_have": False, "planning_scope": "ADVISORY"}
    )
    excluded = base.model_copy(update={"id": "CAP-TOOL-005", "explicit_exclusion": True})
    criteria = (base, must_fail, must_block, advisory, excluded)
    results = (
        CriterionEvidenceResult(
            criterion_id="CAP-TOOL-001",
            scope="MUST_HAVE",
            planning_disposition="EXECUTABLE",
            ingestion_status="ADMITTED",
            verdict="PASS",
            reason="passed",
        ),
        CriterionEvidenceResult(
            criterion_id="CAP-TOOL-002",
            scope="MUST_HAVE",
            planning_disposition="EXECUTABLE",
            ingestion_status="ADMITTED",
            verdict="FAIL",
            reason="failed",
        ),
        CriterionEvidenceResult(
            criterion_id="CAP-TOOL-003",
            scope="MUST_HAVE",
            planning_disposition="EXECUTABLE",
            ingestion_status="ADMITTED",
            verdict="BLOCKED",
            reason="blocked",
        ),
        CriterionEvidenceResult(
            criterion_id="CAP-TOOL-004",
            scope="ADVISORY",
            planning_disposition="EXECUTABLE",
            ingestion_status="ADMITTED",
            verdict="NOT_PROVEN",
            reason="advisory limitation",
            limitations=("advisory limitation",),
        ),
        CriterionEvidenceResult(
            criterion_id="CAP-TOOL-005",
            scope="MUST_HAVE",
            planning_disposition="EXECUTABLE",
            explicit_exclusion=True,
            ingestion_status="ADMITTED",
            verdict="PASS",
            reason="excluded",
        ),
    )

    reduction = reduce_criterion_results(criteria, results)
    assert reduction.verdict.value == "FAIL"
    assert reduction.advisory_non_pass_criterion_ids == ("CAP-TOOL-004",)
    assert reduction.explicit_exclusion_criterion_ids == ("CAP-TOOL-005",)
    assert "Advisory non-pass" in " ".join(reduction.limitations)

    incomplete = reduce_criterion_results(criteria, results[:-2])
    assert incomplete.verdict.value == "NOT_PROVEN"

    omitted_excluded = reduce_criterion_results(criteria, results[:-1])
    assert omitted_excluded.verdict.value == "FAIL"
    assert omitted_excluded.explicit_exclusion_criterion_ids == ("CAP-TOOL-005",)

    mismatched_scope = list(results)
    mismatched_scope[0] = mismatched_scope[0].model_copy(update={"scope": "ADVISORY"})
    mismatch = reduce_criterion_results(criteria, tuple(mismatched_scope))
    assert mismatch.verdict.value == "NOT_PROVEN"
    assert "scope, disposition, and exclusion" in " ".join(mismatch.limitations)


def test_admitted_insufficient_evidence_is_not_proven_and_not_rejected():
    frozen, _ = _frozen_contract()
    criterion = frozen.criteria[0]
    result = CriterionEvidenceResult(
        criterion_id=criterion.id,
        scope=criterion.planning_scope,
        planning_disposition=criterion.planning_disposition,
        ingestion_status="ADMITTED",
        verdict="NOT_PROVEN",
        reason="The admitted sample is below the frozen minimum.",
        sample_count=99,
        success_count=99,
        limitations=("Successful sample shortfall remains visible.",),
    )

    reduction = reduce_criterion_results((criterion,), (result,))
    assert reduction.verdict.value == "NOT_PROVEN"
    assert result.ingestion_status == "ADMITTED"
    assert result.verdict.value == "NOT_PROVEN"


def _pack_service(tmp_path):
    frozen, confirmation = _frozen_contract()
    return ProcessLocalEvidenceOrchestrationService(
        contract_lookup=lambda _: frozen,
        confirmation_lookup=lambda _: confirmation,
        clock=lambda: NOW,
        output_root=(tmp_path / "packs").resolve(),
    )


_SECRET_CONFIRMATION_IDEMPOTENCY_KEY = (
    "confirm__A6_PRIVATE_TEST__do-not-publish__9f4c1e"
)


def _secret_confirmation_pack_service(tmp_path):
    frozen, confirmation = _frozen_contract()
    public_test_confirmation = confirmation.model_copy(
        update={"idempotency_key": _SECRET_CONFIRMATION_IDEMPOTENCY_KEY}
    )
    return ProcessLocalEvidenceOrchestrationService(
        contract_lookup=lambda _: frozen,
        confirmation_lookup=lambda _: public_test_confirmation,
        clock=lambda: NOW,
        output_root=(tmp_path / "packs").resolve(),
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


def test_generic_pack_is_verified_and_current_handoff_is_bound(tmp_path):
    service = _pack_service(tmp_path)
    started = service.start(
        "poc_a6_executable_test",
        acknowledgement=True,
        idempotency_key="generic-a6-pack-current",
    )
    attempt = started.attempt

    assert attempt.evidence_pack_url == (
        f"/artifacts/{attempt.attempt_id}/decision-packet.html"
    )
    assert attempt.evidence_pack_sha256 == service.verify_evidence_pack(attempt.attempt_id)
    evidence_path = tmp_path / "packs" / attempt.attempt_id / "evidence.json"
    payload = json.loads(evidence_path.read_bytes())
    assert payload["contract_identity"]["sha256"] == attempt.contract_hash
    assert payload["attempt"]["run_id"] == attempt.run_id
    assert payload["attempt"]["operation_id"] == attempt.operation_id
    assert payload["shipping_authorized"] is False
    assert "does not authorize deployment" in payload["non_authorization"]
    assert "CAP-TOOL-001" in {
        item["criterion"]["id"] for item in payload["criterion_evidence"]
    }

    library_item = service.evidence_pack_library_item(attempt.attempt_id)
    assert library_item.handoff_state.value == "READY_FOR_HANDOFF"
    handoff = service.handoff(
        attempt.attempt_id,
        decided_by="a6.customer",
        rationale="I reviewed the current Evidence Pack.",
        idempotency_key="generic-a6-pack-handoff",
    )
    assert handoff.closure.decision.value == "HANDOFF_COMPLETED"
    assert handoff.closure.shipping_authorized is False
    assert (
        service.evidence_pack_library_item(attempt.attempt_id).handoff_state.value
        == "HANDOFF_COMPLETED"
    )
    with pytest.raises(ExecutableOrchestrationConflict, match="lifecycle is closed"):
        service.start(
            "poc_a6_executable_test",
            acknowledgement=True,
            idempotency_key="generic-a6-pack-after-handoff",
        )


def test_public_pack_and_handoff_snapshot_redact_confirmation_idempotency_key(tmp_path):
    service = _secret_confirmation_pack_service(tmp_path)
    attempt = service.start(
        "poc_a6_executable_test",
        acknowledgement=True,
        idempotency_key="generic-a6-public-confirmation",
    ).attempt
    pack_root = tmp_path / "packs" / attempt.attempt_id
    confirmation_payload = json.loads(
        (pack_root / "confirmation.json").read_bytes()
    )
    evidence_payload = json.loads((pack_root / "evidence.json").read_bytes())
    decision_packet = (pack_root / "decision-packet.html").read_bytes()

    for payload in (confirmation_payload, evidence_payload):
        assert "idempotency_key" not in _nested_keys(payload)
        assert _SECRET_CONFIRMATION_IDEMPOTENCY_KEY not in json.dumps(payload)
    assert "idempotency_key" not in decision_packet.decode("utf-8")
    assert _SECRET_CONFIRMATION_IDEMPOTENCY_KEY.encode() not in decision_packet
    assert confirmation_payload == {
        "confirmation_id": attempt.confirmation_id,
        "contract_id": attempt.contract_id,
        "contract_version": attempt.contract_version,
        "contract_fingerprint": attempt.confirmation_fingerprint,
        "confirmer": "A6 customer approver",
        "decision": "CONFIRM",
        "agreement_acknowledged": True,
        "confirmed_at": NOW.isoformat(),
        "rationale": "I confirm the exact executable criterion.",
    }
    assert service.verify_evidence_pack(attempt.attempt_id) == attempt.evidence_pack_sha256

    before_closure = service.snapshot_payload("poc_a6_executable_test")
    assert "idempotency_key" not in _nested_keys(before_closure)
    assert _SECRET_CONFIRMATION_IDEMPOTENCY_KEY not in json.dumps(before_closure)
    service.handoff(
        attempt.attempt_id,
        decided_by="a6.customer",
        rationale="Review the redacted confirmation binding.",
        idempotency_key="generic-a6-public-confirmation-handoff",
    )
    after_closure = service.snapshot_payload("poc_a6_executable_test")
    assert "idempotency_key" not in _nested_keys(after_closure)
    assert _SECRET_CONFIRMATION_IDEMPOTENCY_KEY not in json.dumps(after_closure)


def test_handoff_rejects_tampered_pack_without_recording_closure(tmp_path):
    service = _pack_service(tmp_path)
    attempt = service.start(
        "poc_a6_executable_test",
        acknowledgement=True,
        idempotency_key="generic-a6-handoff-tamper",
    ).attempt
    packet = tmp_path / "packs" / attempt.attempt_id / "decision-packet.html"
    packet.write_bytes(packet.read_bytes() + b"tampered")

    with pytest.raises(GenericEvidencePackError):
        service.handoff(
            attempt.attempt_id,
            decided_by="a6.customer",
            rationale="The packet was tampered with.",
            idempotency_key="generic-a6-handoff-tamper-decision",
        )
    assert service.snapshot_payload("poc_a6_executable_test")["closure"] is None


def test_pack_replay_tamper_bounds_and_no_overwrite(tmp_path):
    service = _pack_service(tmp_path)
    attempt = service.start(
        "poc_a6_executable_test",
        acknowledgement=True,
        idempotency_key="generic-a6-pack-replay",
    ).attempt
    pack_root = tmp_path / "packs" / attempt.attempt_id
    assert service.publish_evidence_pack(attempt.attempt_id) == attempt

    with pytest.raises(GenericEvidencePackError, match="already exists"):
        from exitspec.generic_evidence_pack import publish_generic_evidence_pack

        publish_generic_evidence_pack(
            (tmp_path / "packs").resolve(),
            attempt.attempt_id,
            {"contract": {}, "confirmation": {}},
        )

    (pack_root / "extra.txt").write_text("unexpected", encoding="utf-8")
    with pytest.raises(GenericEvidencePackError, match="directory entries"):
        service.verify_evidence_pack(attempt.attempt_id)
    (pack_root / "extra.txt").unlink()

    packet = pack_root / "decision-packet.html"
    packet.unlink()
    os.symlink("evidence.json", packet)
    with pytest.raises(GenericEvidencePackError, match="unsafe entry|bytes"):
        service.verify_evidence_pack(attempt.attempt_id)
    packet.unlink()
    packet.write_bytes(b"x" * (4 * 1024 * 1024 + 1))
    with pytest.raises(GenericEvidencePackError, match="too large|bytes"):
        service.verify_evidence_pack(attempt.attempt_id)


def test_pack_verifier_rejects_duplicate_manifest_and_missing_root(tmp_path):
    service = _pack_service(tmp_path)
    attempt = service.start(
        "poc_a6_executable_test",
        acknowledgement=True,
        idempotency_key="generic-a6-pack-json",
    ).attempt
    manifest_path = tmp_path / "packs" / attempt.attempt_id / "artifact-hashes.json"
    manifest = manifest_path.read_text(encoding="utf-8")
    manifest_path.write_text(
        manifest[:-1] + ',"attempt_id":"' + attempt.attempt_id + '"}',
        encoding="utf-8",
    )
    with pytest.raises(GenericEvidencePackError, match="duplicate|invalid|bytes"):
        service.verify_evidence_pack(attempt.attempt_id)

    missing_root = (tmp_path / "does-not-exist").resolve()
    with pytest.raises(GenericEvidencePackError, match="unavailable"):
        from exitspec.generic_evidence_pack import verify_generic_evidence_pack

        verify_generic_evidence_pack(missing_root, attempt.attempt_id)
    assert not missing_root.exists()


def test_stale_attempt_is_never_newly_published_and_terminal_history_is_retained(tmp_path):
    service = _pack_service(tmp_path)
    first = service.start(
        "poc_a6_executable_test",
        acknowledgement=True,
        idempotency_key="generic-a6-history-first",
    ).attempt
    pack_path = tmp_path / "packs" / first.attempt_id / "evidence.json"
    published_pack = pack_path.read_bytes()
    published_payload = json.loads(published_pack)
    assert published_payload["current_at_publication"] is True
    assert published_payload["historical_at_publication"] is False
    assert "current" not in published_payload
    assert "historical" not in published_payload
    successor = service.reserve(
        "poc_a6_executable_test",
        acknowledgement=True,
        idempotency_key="generic-a6-history-successor",
    ).attempt
    assert first.status is EvidenceAttemptStatus.COMPLETED
    historical = service.attempt(first.attempt_id)
    assert first.is_current is True
    assert historical.status is EvidenceAttemptStatus.COMPLETED
    assert historical.is_current is False
    assert service.verify_evidence_pack(first.attempt_id) == historical.evidence_pack_sha256
    assert service.evidence_pack_library_item(first.attempt_id).handoff_state.value == "HISTORICAL"
    assert pack_path.read_bytes() == published_pack
    assert service.current("poc_a6_executable_test").attempt_id == successor.attempt_id
    with pytest.raises(GenericEvidencePackError, match="current|terminal"):
        service.publish_evidence_pack(successor.attempt_id)

    uncompleted = _pack_service(tmp_path / "uncompleted")
    reserved = uncompleted.reserve(
        "poc_a6_executable_test",
        acknowledgement=True,
        idempotency_key="generic-a6-history-reserved",
    ).attempt
    uncompleted.reserve(
        "poc_a6_executable_test",
        acknowledgement=True,
        idempotency_key="generic-a6-history-reserved-successor",
    )
    assert uncompleted.attempt(reserved.attempt_id).status is EvidenceAttemptStatus.STALE
    with pytest.raises(GenericEvidencePackError, match="current|terminal"):
        uncompleted.publish_evidence_pack(reserved.attempt_id)


def test_cancelled_current_attempt_can_stop_without_an_evidence_pack(tmp_path):
    service = _pack_service(tmp_path)
    reserved = service.reserve(
        "poc_a6_executable_test",
        acknowledgement=True,
        idempotency_key="generic-a6-pack-stop",
    ).attempt
    cancelled = service.cancel(reserved.attempt_id)
    stopped = service.stop(
        cancelled.attempt_id,
        decided_by="a6.customer",
        rationale="Stop the cancelled POC.",
        idempotency_key="generic-a6-pack-stop-decision",
    )
    assert stopped.closure.decision.value == "POC_STOPPED"
    assert stopped.closure.shipping_authorized is False


def test_closure_and_reservation_race_has_bounded_completion(tmp_path):
    service = _pack_service(tmp_path)
    attempt = service.start(
        "poc_a6_executable_test",
        acknowledgement=True,
        idempotency_key="generic-a6-lock-order-first",
    ).attempt
    barrier = Event()
    errors: list[Exception] = []

    def close_current() -> None:
        barrier.wait(timeout=2)
        try:
            service.handoff(
                attempt.attempt_id,
                decided_by="a6.customer",
                rationale="Close the current evidence attempt.",
                idempotency_key="generic-a6-lock-order-close",
            )
        except Exception as error:  # race outcome is intentionally bounded
            errors.append(error)

    def reserve_successor() -> None:
        barrier.wait(timeout=2)
        try:
            service.reserve(
                "poc_a6_executable_test",
                acknowledgement=True,
                idempotency_key="generic-a6-lock-order-successor",
            )
        except Exception as error:  # race outcome is intentionally bounded
            errors.append(error)

    threads = [Thread(target=close_current), Thread(target=reserve_successor)]
    for thread in threads:
        thread.start()
    barrier.set()
    for thread in threads:
        thread.join(timeout=2)
        assert not thread.is_alive()
    assert len(errors) <= 1
