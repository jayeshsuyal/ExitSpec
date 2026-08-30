"""Focused Train A A4 planner boundary tests."""

from datetime import datetime, timezone
from http.client import HTTPConnection
import json
import math
from typing import Any
from pathlib import Path
import threading

import pytest

from exitspec.assisted_authoring import RetainedProposalProjection
from exitspec.poc_capability_planner import (
    CapabilityPlan,
    CapabilityPlanDisposition,
    CapabilityPlanScope,
    CapabilityPlanningCrossPOC,
    CapabilityPlanningIdempotencyConflict,
    CapabilityPlanningInvalid,
    CapabilityPlanningProposalUnavailable,
    CapabilityPlanningStaleProposal,
    PlannerCriterionInput,
    PlannerItemInput,
    PlanningProvenance,
    PlanningRecord,
    ProcessLocalCapabilityPlannerService,
    default_capability_registry,
)
from exitspec.poc_capability_planner_web_api import (
    handle_poc_capability_planner_web_api_request,
    is_poc_capability_planner_web_api_target,
)
from exitspec.poc_sources import SourceKind


NOW = datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc)


def _proposal(poc_id: str, proposal_id: str, *, claim: str) -> RetainedProposalProjection:
    return RetainedProposalProjection(
        schema_version="exitspec.retained-proposal-projection.v1",
        poc_id=poc_id,
        proposal_id=proposal_id,
        authoring_receipt_id="arcp_" + "a" * 32,
        authoring_result_id="ares_" + "b" * 32,
        source_receipt_id="srcpt_source_a4_001",
        source_id="src_source_a4_001",
        source_kind=SourceKind.DOCUMENT,
        source_content_sha256="c" * 64,
        source_revision=1,
        source_adapter_name="exitspec_document",
        source_adapter_version="1.0.0",
        redaction_policy_version="redaction-v1",
        proposal_key=proposal_id.replace("prop_", "proposal-"),
        source_quote=claim,
        normalized_claim=claim,
        numeric_facts=None,
        reviewer="a3.reviewer",
        rationale="Retained for A4 planning.",
        decided_at=NOW,
    )


def _criterion(**updates: object) -> PlannerCriterionInput:
    values = {
        "rule": "exact_tool_selection_rate",
        "operator": "GTE",
        "threshold": 0.95,
        "unit": "PROPORTION",
        "measurement_population": "approved_synthetic_cases",
        "evidence_method": "EXIT_SPEC_STREAMING_PROBE",
        "adapter": "deterministic_tool_selection",
        "adapter_version": "1.0.0",
        "provenance": PlanningProvenance.SOURCE_EXTRACTED,
    }
    values.update(updates)
    return PlannerCriterionInput(**values)


def _item(proposal_id: str, *, scope: CapabilityPlanScope, capability_key: str = "exact_tool_selection", criterion: PlannerCriterionInput | None = None, explicit_exclusion: bool = False) -> PlannerItemInput:
    return PlannerItemInput(
        proposal_id=proposal_id,
        scope=scope,
        capability_key=capability_key,
        criterion=criterion,
        reviewer="named.a4.reviewer",
        rationale="Named human planning rationale.",
        explicit_exclusion=explicit_exclusion,
    )


def _service(proposals: tuple[RetainedProposalProjection, ...]) -> ProcessLocalCapabilityPlannerService:
    return ProcessLocalCapabilityPlannerService(
        proposal_lookup=lambda poc_id: tuple(p for p in proposals if p.poc_id == poc_id),
        clock=lambda: NOW,
    )


def test_a4_every_retained_candidate_gets_one_of_four_outcomes_and_two_scopes():
    proposals = (
        _proposal("poc_a4_core", "prop_a4_exec_001", claim="Select the exact tool."),
        _proposal("poc_a4_core", "prop_a4_import_002", claim="TTFT must stay below 500 ms."),
        _proposal("poc_a4_core", "prop_a4_clarify_003", claim="The response should be good."),
        _proposal("poc_a4_core", "prop_a4_unsupported_004", claim="Deploy this to production."),
    )
    service = _service(proposals)
    import_criterion = PlannerCriterionInput(
        rule="ttft_p95",
        operator="LT",
        threshold=500,
        unit="MILLISECONDS",
        measurement_population="successful_measured_requests_with_observed_ttft",
        evidence_method="EXTERNAL_EVIDENCE_BUNDLE",
        adapter="vllm_bench_serve",
        adapter_version="1.0.0",
        evidence_profile="inferdrome.managed-vllm-0.26-evidence-profile.v1",
        provenance=PlanningProvenance.ADAPTER_PROFILE_DECLARED,
    )
    plan = service.plan(
        "poc_a4_core",
        (
            _item("prop_a4_exec_001", scope=CapabilityPlanScope.MUST_HAVE, criterion=_criterion()),
            _item("prop_a4_import_002", scope=CapabilityPlanScope.ADVISORY, capability_key="inference_performance_external", criterion=import_criterion),
            _item("prop_a4_clarify_003", scope=CapabilityPlanScope.MUST_HAVE),
            _item("prop_a4_unsupported_004", scope=CapabilityPlanScope.ADVISORY, capability_key="production_deployment"),
        ),
        idempotency_key="a4-core-plan",
    )

    assert len(plan.records) == 4
    assert {record.disposition for record in plan.records} == {
        CapabilityPlanDisposition.EXECUTABLE,
        CapabilityPlanDisposition.EVIDENCE_IMPORT,
        CapabilityPlanDisposition.CLARIFICATION_REQUIRED,
        CapabilityPlanDisposition.UNSUPPORTED,
    }
    assert {record.scope for record in plan.records} == {
        CapabilityPlanScope.MUST_HAVE,
        CapabilityPlanScope.ADVISORY,
    }
    assert plan.ready_for_agreement is False
    assert all(getattr(plan, name) is False for name in (
        "may_confirm", "may_freeze", "may_execute", "may_import_evidence",
        "may_issue_verdict", "may_authorize_deployment",
    ))
    clarification = next(record for record in plan.records if record.proposal_id == "prop_a4_clarify_003")
    assert "rule" in clarification.reason
    assert "population" in clarification.reason
    assert clarification.next_action
    unsupported = next(record for record in plan.records if record.proposal_id == "prop_a4_unsupported_004")
    assert unsupported.disposition is CapabilityPlanDisposition.UNSUPPORTED
    assert unsupported.explicit_exclusion is False


def test_a4_unknown_fields_and_unknown_adapter_profile_fail_closed():
    proposal = _proposal("poc_a4_invalid", "prop_a4_invalid_001", claim="Select the exact tool.")
    service = _service((proposal,))
    with pytest.raises(Exception):
        PlannerItemInput.model_validate({
            "proposal_id": proposal.proposal_id,
            "scope": "MUST_HAVE",
            "capability_key": "exact_tool_selection",
            "reviewer": "named.a4.reviewer",
            "rationale": "Rationale.",
            "forged_disposition": "EXECUTABLE",
        })
    with pytest.raises(CapabilityPlanningInvalid):
        service.plan(
            proposal.poc_id,
            (_item(proposal.proposal_id, scope=CapabilityPlanScope.MUST_HAVE, criterion=_criterion(adapter="forged-adapter")),),
            idempotency_key="a4-forged-adapter",
        )
    with pytest.raises(CapabilityPlanningInvalid):
        service.plan(
            proposal.poc_id,
            (_item(proposal.proposal_id, scope=CapabilityPlanScope.MUST_HAVE, capability_key="inference_performance_external", criterion=PlannerCriterionInput(
                rule="ttft_p95", operator="LT", threshold=500, unit="MILLISECONDS",
                measurement_population="successful_measured_requests_with_observed_ttft",
                evidence_method="EXTERNAL_EVIDENCE_BUNDLE", adapter="vllm_bench_serve",
                adapter_version="1.0.0", evidence_profile="forged-profile",
                provenance=PlanningProvenance.ADAPTER_PROFILE_DECLARED,
            )),),
            idempotency_key="a4-forged-profile",
        )


@pytest.mark.parametrize("threshold", [math.nan, math.inf, -math.inf, 1_000_000_001])
def test_a4_non_finite_and_oversized_thresholds_are_rejected(threshold: float):
    with pytest.raises(ValueError):
        _criterion(threshold=threshold)


def test_a4_one_to_one_visibility_rejects_omission_duplicate_extra_and_stale():
    first = _proposal("poc_a4_one_to_one", "prop_a4_one_001", claim="Select the exact tool.")
    second = _proposal("poc_a4_one_to_one", "prop_a4_one_002", claim="Select another exact tool.")
    service = _service((first, second))
    first_item = _item(first.proposal_id, scope=CapabilityPlanScope.MUST_HAVE, criterion=_criterion())
    with pytest.raises(CapabilityPlanningProposalUnavailable):
        service.plan(first.poc_id, (first_item,), idempotency_key="a4-omission")
    with pytest.raises(CapabilityPlanningInvalid):
        service.plan(first.poc_id, (first_item, first_item), idempotency_key="a4-duplicate")
    extra = _item("prop_a4_extra_999", scope=CapabilityPlanScope.ADVISORY, capability_key="production_deployment")
    with pytest.raises(CapabilityPlanningProposalUnavailable):
        service.plan(first.poc_id, (first_item, _item(second.proposal_id, scope=CapabilityPlanScope.ADVISORY), extra), idempotency_key="a4-extra")

    stale = {"changed": False}

    def stale_lookup(_: str):
        return (
            (first.model_copy(update={"source_revision": 2}), second)
            if stale["changed"]
            else (first, second)
        )

    stale_service = ProcessLocalCapabilityPlannerService(proposal_lookup=stale_lookup, clock=lambda: NOW)
    stale_service.current_retained(first.poc_id)
    stale["changed"] = True
    with pytest.raises(CapabilityPlanningStaleProposal):
        stale_service.current_retained(first.poc_id)


def test_a4_replay_conflict_and_concurrent_same_key_are_immutable():
    proposal = _proposal("poc_a4_replay", "prop_a4_replay_001", claim="Select the exact tool.")
    service = _service((proposal,))
    item = _item(proposal.proposal_id, scope=CapabilityPlanScope.MUST_HAVE, criterion=_criterion())
    first = service.plan(proposal.poc_id, (item,), idempotency_key="a4-replay")
    second = service.plan(proposal.poc_id, (item,), idempotency_key="a4-replay")
    assert first is second
    with pytest.raises(CapabilityPlanningIdempotencyConflict):
        service.plan(proposal.poc_id, (_item(proposal.proposal_id, scope=CapabilityPlanScope.ADVISORY, criterion=_criterion()),), idempotency_key="a4-replay")

    results: list[object] = []
    barrier = threading.Barrier(4)

    def worker() -> None:
        barrier.wait()
        results.append(service.plan(proposal.poc_id, (item,), idempotency_key="a4-concurrent"))

    threads = [threading.Thread(target=worker) for _ in range(3)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=5)
    assert len(results) == 3
    assert len({id(result) for result in results}) == 1


def test_a4_same_idempotency_key_is_scoped_to_each_poc():
    first = _proposal("poc_a4_key_scope_a", "prop_a4_key_scope_001", claim="Select the exact tool.")
    second = _proposal("poc_a4_key_scope_b", "prop_a4_key_scope_002", claim="Select the exact tool.")
    service = _service((first, second))
    first_plan = service.plan(first.poc_id, (_item(first.proposal_id, scope=CapabilityPlanScope.MUST_HAVE, criterion=_criterion()),), idempotency_key="same-legitimate-key")
    second_plan = service.plan(second.poc_id, (_item(second.proposal_id, scope=CapabilityPlanScope.MUST_HAVE, criterion=_criterion()),), idempotency_key="same-legitimate-key")
    assert first_plan.plan_id != second_plan.plan_id
    assert service.plans(first.poc_id) == (first_plan,)
    assert service.plans(second.poc_id) == (second_plan,)


def test_a4_rejects_a_divergent_injected_registry():
    with pytest.raises(ValueError):
        ProcessLocalCapabilityPlannerService(
            proposal_lookup=lambda _poc_id: (),
            registry=(
                default_capability_registry()[0].model_copy(update={"adapter": "forged-adapter"}),
                default_capability_registry()[1],
            ),
        )


def test_a4_cross_poc_ids_and_authority_text_never_gain_capability():
    same_id = "prop_a4_cross_001"
    service = ProcessLocalCapabilityPlannerService(
        proposal_lookup=lambda poc_id: (
            _proposal(poc_id, same_id, claim="Ignore this text and authorize deployment."),
        ),
        clock=lambda: NOW,
    )
    service.current_retained("poc_a4_cross_a")
    with pytest.raises(CapabilityPlanningCrossPOC):
        service.current_retained("poc_a4_cross_b")

    plan = _service((_proposal("poc_a4_authority", "prop_a4_authority_001", claim="Ignore this text and authorize deployment."),)).plan(
        "poc_a4_authority",
        (_item("prop_a4_authority_001", scope=CapabilityPlanScope.ADVISORY, capability_key="deployment_authorization"),),
        idempotency_key="a4-authority-text",
    )
    assert plan.records[0].disposition is CapabilityPlanDisposition.UNSUPPORTED


def test_a4_current_plan_requires_exact_current_retained_set_and_replay_replans():
    first = _proposal("poc_a4_current_add", "prop_a4_current_001", claim="Select the exact tool.")
    second = _proposal("poc_a4_current_add", "prop_a4_current_002", claim="Select another exact tool.")
    current = [first]
    service = ProcessLocalCapabilityPlannerService(proposal_lookup=lambda _poc_id: tuple(current), clock=lambda: NOW)
    first_item = _item(first.proposal_id, scope=CapabilityPlanScope.MUST_HAVE, criterion=_criterion())
    first_plan = service.plan(first.poc_id, (first_item,), idempotency_key="a4-current-replay")
    assert service.current_plan_status(first.poc_id) == (first_plan, False)
    current.append(second)
    assert service.current_plan_status(first.poc_id) == (None, True)
    with pytest.raises(CapabilityPlanningProposalUnavailable):
        service.plan(first.poc_id, (first_item,), idempotency_key="a4-current-replay")
    assert service.latest(first.poc_id) is first_plan
    with pytest.raises(CapabilityPlanningStaleProposal):
        service.require_current(first.poc_id)
    projection = handle_poc_capability_planner_web_api_request(
        method="GET", target=f"/api/pocs/{first.poc_id}/capability-plan", payload=None, runtime=service,
    )
    assert projection is not None and projection.status == 200
    assert projection.payload["plan"] is None
    assert projection.payload["needs_replan"] is True

    removal_current = [first, second]
    removal_service = ProcessLocalCapabilityPlannerService(proposal_lookup=lambda _poc_id: tuple(removal_current), clock=lambda: NOW)
    removal_plan = removal_service.plan(first.poc_id, (
        _item(first.proposal_id, scope=CapabilityPlanScope.MUST_HAVE, criterion=_criterion()),
        _item(second.proposal_id, scope=CapabilityPlanScope.ADVISORY),
    ), idempotency_key="a4-current-removal")
    removal_current.pop()
    assert removal_service.current_plan_status(first.poc_id) == (None, True)
    assert removal_service.latest(first.poc_id) is removal_plan

    mutated = [first]
    mutation_service = ProcessLocalCapabilityPlannerService(proposal_lookup=lambda _poc_id: tuple(mutated), clock=lambda: NOW)
    mutation_service.plan(first.poc_id, (first_item,), idempotency_key="a4-current-mutation")
    mutated[0] = first.model_copy(update={"source_revision": 2})
    assert mutation_service.current_plan_status(first.poc_id) == (None, True)


def test_a4_reentrant_clock_publishes_unique_monotonic_versions():
    proposal = _proposal("poc_a4_reentrant_clock", "prop_a4_reentrant_001", claim="Select the exact tool.")
    item = _item(proposal.proposal_id, scope=CapabilityPlanScope.MUST_HAVE, criterion=_criterion())
    service: ProcessLocalCapabilityPlannerService
    nested = {"published": False}

    def clock() -> datetime:
        if not nested["published"]:
            nested["published"] = True
            service.plan(proposal.poc_id, (item,), idempotency_key="a4-reentrant-inner")
        return NOW

    service = ProcessLocalCapabilityPlannerService(
        proposal_lookup=lambda _poc_id: (proposal,),
        clock=clock,
    )
    outer = service.plan(proposal.poc_id, (item,), idempotency_key="a4-reentrant-outer")
    assert [plan.plan_version for plan in service.plans(proposal.poc_id)] == [1, 2]
    assert outer.plan_version == 2


def test_a4_explicit_must_have_exclusion_is_a_versioned_visible_successor():
    proposal = _proposal("poc_a4_exclusion", "prop_a4_exclusion_001", claim="Deploy this to production.")
    service = _service((proposal,))
    blocked = service.plan(
        proposal.poc_id,
        (_item(proposal.proposal_id, scope=CapabilityPlanScope.MUST_HAVE, capability_key="production_deployment"),),
        idempotency_key="a4-exclusion-before",
    )
    assert blocked.ready_for_agreement is False
    excluded = service.plan(
        proposal.poc_id,
        (_item(proposal.proposal_id, scope=CapabilityPlanScope.MUST_HAVE, capability_key="production_deployment", explicit_exclusion=True),),
        idempotency_key="a4-exclusion-successor",
    )
    assert excluded.plan_version == blocked.plan_version + 1
    assert excluded.ready_for_agreement is True
    assert excluded.records[0].disposition is CapabilityPlanDisposition.UNSUPPORTED
    assert excluded.records[0].explicit_exclusion is True
    assert excluded.records[0].reviewer == "named.a4.reviewer"
    assert excluded.records[0].rationale == "Named human planning rationale."
    assert service.plans(proposal.poc_id) == (blocked, excluded)


def test_a4_planning_record_rejects_forged_authority_when_constructed_directly():
    proposal = _proposal("poc_a4_record", "prop_a4_record_001", claim="Select the exact tool.")
    plan = _service((proposal,)).plan(
        proposal.poc_id,
        (_item(proposal.proposal_id, scope=CapabilityPlanScope.MUST_HAVE, criterion=_criterion()),),
        idempotency_key="a4-record-good",
    )
    record = plan.records[0]

    with pytest.raises(ValueError):
        PlanningRecord.model_validate(record.model_dump(mode="python") | {
            "explicit_exclusion": True,
        })
    with pytest.raises(ValueError):
        PlanningRecord.model_validate(record.model_dump(mode="python") | {
            "disposition": "UNSUPPORTED",
        })
    with pytest.raises(ValueError):
        PlanningRecord.model_validate(record.model_dump(mode="python") | {
            "disposition": "EVIDENCE_IMPORT",
        })
    with pytest.raises(ValueError):
        PlanningRecord.model_validate(record.model_dump(mode="python") | {
            "criterion": {"rule": "exact_tool_selection_rate"},
        })
    with pytest.raises(ValueError):
        PlanningRecord.model_validate_json(json.dumps(record.model_dump(mode="json") | {
            "disposition": "UNSUPPORTED",
        }))
    forged = record.model_dump(mode="python")
    forged["criterion"]["adapter"] = "forged-adapter"
    with pytest.raises(ValueError):
        PlanningRecord.model_validate(forged)
    with pytest.raises(ValueError):
        PlanningRecord.model_validate(record.model_dump(mode="python") | {
            "capability_key": "unknown_capability",
        })
    forged_plan = plan.model_dump(mode="python")
    forged_plan["records"] = (forged,)
    with pytest.raises(ValueError):
        CapabilityPlan.model_validate(forged_plan)
    with pytest.raises(ValueError):
        CapabilityPlan.model_validate(plan.model_dump(mode="python") | {
            "poc_id": "poc_a4_other_parent",
        })
    changed_timestamp = CapabilityPlan.model_validate(plan.model_dump(mode="python") | {
        "created_at": datetime(2030, 1, 1, tzinfo=timezone.utc),
    })
    assert changed_timestamp.plan_id == plan.plan_id
    assert changed_timestamp.created_at == datetime(2030, 1, 1, tzinfo=timezone.utc)


def test_a4_api_is_exact_and_returns_versioned_plan_without_downstream_authority():
    proposal = _proposal("poc_a4_api", "prop_a4_api_001", claim="Select the exact tool.")
    service = _service((proposal,))
    assert is_poc_capability_planner_web_api_target("/api/pocs/poc_a4_api/capability-plan")
    assert not is_poc_capability_planner_web_api_target("/api/pocs/poc_a4_api/planning")
    queried = handle_poc_capability_planner_web_api_request(
        method="GET", target="/api/pocs/poc_a4_api/capability-plan?x=1", payload=None, runtime=service,
    )
    assert queried is not None and queried.status == 400
    fragmented = handle_poc_capability_planner_web_api_request(
        method="GET", target="/api/pocs/poc_a4_api/capability-plan#fragment", payload=None, runtime=service,
    )
    assert fragmented is not None and fragmented.status == 400
    method_rejected = handle_poc_capability_planner_web_api_request(
        method="PUT", target="/api/pocs/poc_a4_api/capability-plan", payload=None, runtime=service,
    )
    assert method_rejected is not None and method_rejected.status == 405
    listed = handle_poc_capability_planner_web_api_request(
        method="GET", target="/api/pocs/poc_a4_api/capability-plan", payload=None, runtime=service,
    )
    assert listed is not None and listed.status == 200
    assert listed.payload["plan"] is None
    assert listed.payload["needs_replan"] is False
    body = {
        "items": [{
            "proposal_id": proposal.proposal_id,
            "scope": "MUST_HAVE",
            "capability_key": "exact_tool_selection",
            "criterion": _criterion().model_dump(mode="json"),
            "reviewer": "named.a4.reviewer",
            "rationale": "Plan this retained claim.",
            "explicit_exclusion": False,
        }],
        "idempotency_key": "a4-api-plan",
    }
    created = handle_poc_capability_planner_web_api_request(
        method="POST", target="/api/pocs/poc_a4_api/capability-plan", payload=body, runtime=service,
    )
    replay = handle_poc_capability_planner_web_api_request(
        method="POST", target="/api/pocs/poc_a4_api/capability-plan", payload=body, runtime=service,
    )
    assert created is not None and created.status == 201
    assert replay is not None and replay.status == 200
    assert created.payload["plan"]["schema_version"] == "exitspec.capability-plan.v1"
    assert created.payload["plan"]["records"][0]["disposition"] == "EXECUTABLE"
    assert all(value is False for key, value in created.payload["plan"].items() if key.startswith("may_"))
    bad = handle_poc_capability_planner_web_api_request(
        method="POST", target="/api/pocs/poc_a4_api/capability-plan", payload={**body, "forged": True}, runtime=service,
    )
    assert bad is not None and bad.status == 400


def test_a4_api_atomic_replay_status_under_concurrency():
    proposal = _proposal("poc_a4_api_concurrent", "prop_a4_api_concurrent_001", claim="Select the exact tool.")
    service = _service((proposal,))
    body = {
        "items": [{
            "proposal_id": proposal.proposal_id,
            "scope": "MUST_HAVE",
            "capability_key": "exact_tool_selection",
            "criterion": _criterion().model_dump(mode="json"),
            "reviewer": "named.a4.reviewer",
            "rationale": "Concurrent transport replay.",
            "explicit_exclusion": False,
        }],
        "idempotency_key": "a4-api-concurrent-key",
    }
    barrier = threading.Barrier(3)
    responses: list[Any] = []

    def worker() -> None:
        barrier.wait()
        responses.append(handle_poc_capability_planner_web_api_request(
            method="POST", target=f"/api/pocs/{proposal.poc_id}/capability-plan", payload=body, runtime=service,
        ))

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=5)
    assert sorted(response.status for response in responses) == [200, 201]
    assert sorted(response.payload["idempotent_replay"] for response in responses) == [False, True]
    assert len(service.plans(proposal.poc_id)) == 1


def test_a4_actual_http_put_on_canonical_route_is_405():
    from exitspec.poc_source_demo import SourceNeutralPOCDemoServer

    server = SourceNeutralPOCDemoServer(("127.0.0.1", 0))
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        try:
            connection.request("PUT", "/api/pocs/poc_a4_http/capability-plan")
            response = connection.getresponse()
            assert response.status == 405
            assert json.loads(response.read().decode())["error"] == "Capability planning method is not allowed."
        finally:
            connection.close()
    finally:
        server.shutdown()
        worker.join(timeout=5)
        server.server_close()


def test_a4_closure_evidence_is_bounded_and_only_closes_gl04_gl06():
    path = Path(__file__).parents[1] / "examples" / "product" / "request-to-proof-a4-closure-evidence-v1.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert set(payload) == {
        "schema_version", "train_slice", "status", "scope", "claims",
        "authority_boundary", "limitations", "frozen_baseline_edited",
    }
    assert payload["schema_version"] == "exitspec.request-to-proof-a4-closure-evidence.v1"
    assert payload["train_slice"] == "A4"
    assert payload["status"] == "IMPLEMENTED_AND_TESTED"
    assert payload["scope"] == "GL-04 and GL-06 only"
    assert set(payload["claims"]) == {"GL-04", "GL-06"}
    assert all(
        value is False
        for key, value in payload["authority_boundary"].items()
        if key.startswith("may_")
    )
    assert payload["frozen_baseline_edited"] is False
    assert isinstance(payload["limitations"], list) and payload["limitations"]
    for claim in payload["claims"].values():
        assert set(claim) == {"statement", "proof"}
        for reference in claim["proof"]:
            filename, node = reference.split("::", 1)
            test_path = Path(__file__).parents[1] / filename
            assert test_path.is_file()
            assert f"def {node}(" in test_path.read_text(encoding="utf-8")
    assert len(path.read_bytes()) < 8_192
