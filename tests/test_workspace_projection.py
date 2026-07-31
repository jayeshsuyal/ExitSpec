from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from exitspec.confirmations import ConfirmationDecision
from exitspec.models import ContractStatus, RunStatus, VerdictStatus
from exitspec.workspace import (
    ArchiveState,
    DashboardFilter,
    POCRegistryEntry,
    POCWorkflowFacts,
    ReadOnlyPOCRegistry,
    WorkspaceAction,
    WorkspaceEvidenceState,
    WorkspacePhase,
    WorkspaceSourceType,
    project_dashboard,
    project_poc,
)


NOW = datetime(2026, 7, 27, 18, 0, tzinfo=timezone.utc)
LATER = datetime(2026, 7, 27, 19, 0, tzinfo=timezone.utc)


def _record(
    poc_id: str = "poc_support_agent",
    *,
    owner: str = "field_engineer",
    archive_state: ArchiveState = ArchiveState.ACTIVE,
    created_at: datetime = NOW,
    updated_at: datetime = LATER,
) -> POCRegistryEntry:
    return POCRegistryEntry(
        poc_id=poc_id,
        display_name="Support-agent POC",
        customer_label="Example customer",
        use_case="Verify exact support-tool selection.",
        owner=owner,
        created_at=created_at,
        updated_at=updated_at,
        archive_state=archive_state,
    )


def _approved_facts(**updates) -> POCWorkflowFacts:
    payload = {
        "source_count": 1,
        "source_types": (WorkspaceSourceType.EMAIL,),
        "approved_criterion_count": 1,
        "active_contract_id": "support-agent-poc",
        "active_contract_version": "1.0.0",
        "contract_status": ContractStatus.APPROVED,
    }
    payload.update(updates)
    return POCWorkflowFacts(**payload)


def _frozen_facts(**updates) -> POCWorkflowFacts:
    payload = {
        "source_count": 1,
        "source_types": (WorkspaceSourceType.EMAIL,),
        "approved_criterion_count": 1,
        "active_contract_id": "support-agent-poc",
        "active_contract_version": "1.0.0",
        "contract_status": ContractStatus.FROZEN,
        "customer_review_issued": True,
        "customer_decision": ConfirmationDecision.CONFIRM,
        "confirmation_matches_active_contract": True,
    }
    payload.update(updates)
    return POCWorkflowFacts(**payload)


def test_registry_is_immutable_sorted_and_rejects_duplicate_ids():
    second = _record("poc_second")
    first = _record("poc_first")
    registry = ReadOnlyPOCRegistry((second, first))

    assert registry.ids() == ("poc_first", "poc_second")
    assert registry.get("poc_first") is first
    assert registry.records() == (first, second)
    assert not hasattr(registry, "register")

    with pytest.raises(ValueError, match="unique"):
        ReadOnlyPOCRegistry((first, first))
    with pytest.raises(TypeError, match="POCRegistryEntry"):
        ReadOnlyPOCRegistry((object(),))


def test_registry_entry_is_frozen_and_timestamps_are_monotonic():
    entry = _record()
    with pytest.raises(ValidationError):
        entry.owner = "another_owner"
    with pytest.raises(ValidationError, match="cannot precede"):
        _record(created_at=LATER, updated_at=NOW)
    with pytest.raises(ValidationError, match="timezone-aware"):
        _record(
            created_at=datetime(2026, 7, 27, 18, 0),
            updated_at=datetime(2026, 7, 27, 19, 0),
        )


def test_empty_poc_projects_one_define_action_without_evidence():
    projection = project_poc(_record(), POCWorkflowFacts())

    assert projection.derived_phase == WorkspacePhase.DEFINE
    assert projection.next_action_code == WorkspaceAction.ADD_SOURCE
    assert projection.next_human_action == "Add a source to begin defining the POC."
    assert projection.source_summary.label == "No source"
    assert projection.latest_evidence_summary.status == (
        WorkspaceEvidenceState.NOT_RUN
    )
    assert projection.blockers == ()
    assert projection.attention_required is True


def test_multiple_source_types_stay_inside_one_poc_summary():
    projection = project_poc(
        _record(),
        POCWorkflowFacts(
            source_count=3,
            source_types=(
                WorkspaceSourceType.MEETING_TRANSCRIPT,
                WorkspaceSourceType.EMAIL,
            ),
            pending_draft_count=2,
        ),
    )

    assert projection.poc_id == "poc_support_agent"
    assert projection.source_summary.count == 3
    assert projection.source_summary.types == (
        WorkspaceSourceType.EMAIL,
        WorkspaceSourceType.MEETING_TRANSCRIPT,
    )
    assert projection.source_summary.label == (
        "3 sources · Email + Meeting transcript"
    )
    assert projection.next_action_code == WorkspaceAction.REVIEW_PROPOSALS
    assert projection.next_human_action == "Review 2 requirement proposals."


def test_duplicate_source_summary_types_are_rejected():
    with pytest.raises(ValidationError, match="distinct"):
        POCWorkflowFacts(
            source_count=2,
            source_types=(
                WorkspaceSourceType.EMAIL,
                WorkspaceSourceType.EMAIL,
            ),
        )

    with pytest.raises(ValidationError, match="timezone-aware"):
        POCWorkflowFacts(action_since=datetime(2026, 7, 27, 18, 0))


@pytest.mark.parametrize(
    ("facts", "expected_action", "expected_text"),
    (
        (
            _approved_facts(),
            WorkspaceAction.CREATE_CUSTOMER_REVIEW,
            "Create the customer review for this agreement.",
        ),
        (
            _approved_facts(customer_review_issued=True),
            WorkspaceAction.WAIT_FOR_CUSTOMER,
            "Wait for the customer decision on this exact version.",
        ),
        (
            _approved_facts(
                customer_review_issued=True,
                customer_decision=ConfirmationDecision.CONFIRM,
                confirmation_matches_active_contract=True,
            ),
            WorkspaceAction.FREEZE_CONFIRMED_CONTRACT,
            "Freeze confirmed contract.",
        ),
        (
            _approved_facts(
                customer_review_issued=True,
                customer_decision=ConfirmationDecision.REQUEST_CHANGES,
                confirmation_matches_active_contract=False,
            ),
            WorkspaceAction.START_REVISION,
            "Revise the customer-requested agreement.",
        ),
    ),
)
def test_approved_agreement_projects_exact_customer_next_action(
    facts,
    expected_action,
    expected_text,
):
    projection = project_poc(_record(), facts)

    assert projection.derived_phase == WorkspacePhase.DEFINE
    assert projection.next_action_code == expected_action
    assert projection.next_human_action == expected_text
    assert projection.blockers == ()


def test_waiting_for_customer_is_not_needs_attention():
    projection = project_poc(
        _record(),
        _approved_facts(customer_review_issued=True),
    )

    assert projection.next_action_code == WorkspaceAction.WAIT_FOR_CUSTOMER
    assert projection.attention_required is False


def test_confirmation_mismatch_fails_closed_to_visible_blocker():
    projection = project_poc(
        _record(),
        _approved_facts(
            customer_review_issued=True,
            customer_decision=ConfirmationDecision.CONFIRM,
            confirmation_matches_active_contract=False,
        ),
    )

    assert projection.derived_phase == WorkspacePhase.DEFINE
    assert projection.next_action_code == WorkspaceAction.RESOLVE_BLOCKER
    assert [blocker.code for blocker in projection.blockers] == [
        "confirmation_binding_mismatch"
    ]
    assert "does not match" in projection.next_human_action


@pytest.mark.parametrize(
    ("facts", "expected_action", "attention_required"),
    (
        (
            _frozen_facts(),
            WorkspaceAction.RUN_POC,
            True,
        ),
        (
            _frozen_facts(run_status=RunStatus.RUNNING),
            WorkspaceAction.WAIT_FOR_PROOF,
            False,
        ),
        (
            _frozen_facts(run_status=RunStatus.CANCELLED),
            WorkspaceAction.RERUN_POC,
            True,
        ),
    ),
)
def test_frozen_agreement_projects_proof_actions(
    facts,
    expected_action,
    attention_required,
):
    projection = project_poc(_record(), facts)

    assert projection.derived_phase == WorkspacePhase.PROVE
    assert projection.next_action_code == expected_action
    assert projection.attention_required is attention_required
    assert projection.blockers == ()


@pytest.mark.parametrize(
    ("verdict", "expected_state", "expected_action"),
    (
        (
            VerdictStatus.PASS,
            WorkspaceEvidenceState.PASS,
            WorkspaceAction.RECORD_DECISION_HANDOFF,
        ),
        (
            VerdictStatus.FAIL,
            WorkspaceEvidenceState.FAIL,
            WorkspaceAction.RECORD_DECISION_HANDOFF,
        ),
        (
            VerdictStatus.BLOCKED,
            WorkspaceEvidenceState.BLOCKED,
            WorkspaceAction.RECORD_DECISION_HANDOFF,
        ),
        (
            VerdictStatus.NOT_PROVEN,
            WorkspaceEvidenceState.NOT_PROVEN,
            WorkspaceAction.RECORD_DECISION_HANDOFF,
        ),
    ),
)
def test_terminal_verdicts_project_decide_without_changing_verdict(
    verdict,
    expected_state,
    expected_action,
):
    facts = _frozen_facts(
        run_status=(
            RunStatus.BLOCKED
            if verdict == VerdictStatus.BLOCKED
            else RunStatus.COMPLETED
        ),
        verdict=verdict,
        verdict_reason="Deterministic verdict reason.",
        evidence_pack_url="/artifacts/demo/decision-packet.html",
    )

    projection = project_poc(_record(), facts)

    assert projection.derived_phase == WorkspacePhase.DECIDE
    assert projection.next_action_code == expected_action
    assert projection.latest_evidence_summary.status == expected_state
    assert projection.latest_evidence_summary.reason == (
        "Deterministic verdict reason."
    )
    assert projection.latest_evidence_summary.report_url == (
        "/artifacts/demo/decision-packet.html"
    )
    assert projection.blockers == ()


@pytest.mark.parametrize(
    ("facts", "expected_code"),
    (
        (
            POCWorkflowFacts(
                source_count=0,
                source_types=(WorkspaceSourceType.EMAIL,),
            ),
            "source_summary_inconsistent",
        ),
        (
            POCWorkflowFacts(
                active_contract_id="support-agent-poc",
            ),
            "contract_identity_incomplete",
        ),
        (
            POCWorkflowFacts(customer_review_issued=True),
            "customer_state_without_contract",
        ),
        (
            _approved_facts(
                confirmation_matches_active_contract=True,
            ),
            "confirmation_match_without_decision",
        ),
        (
            _approved_facts(
                customer_decision=ConfirmationDecision.REQUEST_CHANGES,
                confirmation_matches_active_contract=False,
            ),
            "decision_without_review",
        ),
        (
            _approved_facts(run_status=RunStatus.RUNNING),
            "run_without_frozen_contract",
        ),
        (
            _frozen_facts(
                run_status=RunStatus.RUNNING,
                verdict=VerdictStatus.PASS,
                verdict_reason="Impossible early verdict.",
            ),
            "verdict_without_terminal_run",
        ),
        (
            _frozen_facts(
                run_status=RunStatus.COMPLETED,
            ),
            "terminal_run_without_verdict",
        ),
        (
            _frozen_facts(
                run_status=RunStatus.COMPLETED,
                verdict=VerdictStatus.PASS,
            ),
            "verdict_reason_missing",
        ),
        (
            _frozen_facts(
                run_status=RunStatus.BLOCKED,
                verdict=VerdictStatus.PASS,
                verdict_reason="Impossible pass.",
                evidence_pack_url="/artifacts/demo/decision-packet.html",
            ),
            "blocked_run_verdict_mismatch",
        ),
        (
            _frozen_facts(
                run_status=RunStatus.FAILED_INTERNAL,
                verdict=VerdictStatus.FAIL,
                verdict_reason="Internal failure.",
                evidence_pack_url="/artifacts/demo/decision-packet.html",
            ),
            "internal_failure_verdict_mismatch",
        ),
    ),
)
def test_inconsistent_underlying_truth_never_guesses_advanced_phase(
    facts,
    expected_code,
):
    projection = project_poc(_record(), facts)

    assert projection.derived_phase == WorkspacePhase.DEFINE
    assert projection.next_action_code == WorkspaceAction.RESOLVE_BLOCKER
    assert expected_code in {blocker.code for blocker in projection.blockers}
    assert projection.attention_required is True


def test_terminal_verdict_requires_one_local_evidence_pack_url():
    missing = project_poc(
        _record(),
        _frozen_facts(
            run_status=RunStatus.COMPLETED,
            verdict=VerdictStatus.PASS,
            verdict_reason="Passed.",
        ),
    )
    external = project_poc(
        _record(),
        _frozen_facts(
            run_status=RunStatus.COMPLETED,
            verdict=VerdictStatus.PASS,
            verdict_reason="Passed.",
            evidence_pack_url="https://example.com/evidence",
        ),
    )

    assert "verdict_without_evidence_pack" in {
        blocker.code for blocker in missing.blockers
    }
    assert "evidence_pack_url_invalid" in {
        blocker.code for blocker in external.blockers
    }


def test_completed_archive_state_requires_terminal_verdict():
    projection = project_poc(
        _record(archive_state=ArchiveState.COMPLETED),
        POCWorkflowFacts(),
    )

    assert projection.next_action_code == WorkspaceAction.RESOLVE_BLOCKER
    assert "completed_without_verdict" in {
        blocker.code for blocker in projection.blockers
    }


def test_completed_terminal_poc_has_no_remaining_workflow_action():
    projection = project_poc(
        _record(archive_state=ArchiveState.COMPLETED),
        _frozen_facts(
            run_status=RunStatus.COMPLETED,
            verdict=VerdictStatus.PASS,
            verdict_reason="Passed.",
            evidence_pack_url="/artifacts/pass/decision-packet.html",
        ),
    )

    assert projection.archive_state == ArchiveState.COMPLETED
    assert projection.derived_phase == WorkspacePhase.DECIDE
    assert projection.next_action_code == WorkspaceAction.NONE
    assert projection.attention_required is False
    assert "explicit human decision" in projection.next_human_action


def test_action_timestamp_cannot_precede_poc_creation():
    projection = project_poc(
        _record(created_at=LATER, updated_at=LATER),
        POCWorkflowFacts(action_since=NOW),
    )

    assert projection.next_action_code == WorkspaceAction.RESOLVE_BLOCKER
    assert "action_timestamp_invalid" in {
        blocker.code for blocker in projection.blockers
    }


def test_missing_registry_facts_render_unavailable_instead_of_inventing_state():
    registry = ReadOnlyPOCRegistry((_record(),))
    dashboard = project_dashboard(registry, {})
    projection = dashboard.pocs[0]

    assert projection.next_action_code == WorkspaceAction.RESOLVE_BLOCKER
    assert projection.blockers[0].code == "workspace_state_unavailable"
    assert projection.latest_evidence_summary.status == (
        WorkspaceEvidenceState.NOT_RUN
    )


def test_dashboard_order_and_continue_card_are_deterministic():
    oldest = NOW
    newer = LATER
    registry = ReadOnlyPOCRegistry(
        (
            _record(
                "poc_owned_waiting",
                owner="jayesh",
                created_at=NOW,
                updated_at=newer,
            ),
            _record(
                "poc_unowned_attention",
                owner="other",
                created_at=NOW,
                updated_at=oldest,
            ),
            _record(
                "poc_owned_attention",
                owner="jayesh",
                created_at=NOW,
                updated_at=oldest,
            ),
        )
    )
    facts = {
        "poc_owned_waiting": _approved_facts(customer_review_issued=True),
        "poc_unowned_attention": POCWorkflowFacts(),
        "poc_owned_attention": POCWorkflowFacts(),
    }

    dashboard = project_dashboard(registry, facts, current_owner="jayesh")

    assert [item.poc_id for item in dashboard.pocs] == [
        "poc_owned_attention",
        "poc_owned_waiting",
        "poc_unowned_attention",
    ]
    assert dashboard.continue_working is not None
    assert dashboard.continue_working.poc_id == "poc_owned_attention"
    assert dashboard.available_filters == (
        DashboardFilter.ACTIVE,
        DashboardFilter.NEEDS_ATTENTION,
        DashboardFilter.COMPLETED,
    )


def test_dashboard_filters_are_bounded_and_archive_state_is_not_reclassified():
    active = _record("poc_active")
    completed = _record(
        "poc_completed",
        archive_state=ArchiveState.COMPLETED,
    )
    archived = _record(
        "poc_archived",
        archive_state=ArchiveState.ARCHIVED,
    )
    registry = ReadOnlyPOCRegistry((active, completed, archived))
    facts = {
        "poc_active": POCWorkflowFacts(),
        "poc_completed": _frozen_facts(
            run_status=RunStatus.COMPLETED,
            verdict=VerdictStatus.PASS,
            verdict_reason="Passed.",
            evidence_pack_url="/artifacts/pass/decision-packet.html",
        ),
        "poc_archived": POCWorkflowFacts(),
    }

    active_dashboard = project_dashboard(registry, facts)
    attention_dashboard = project_dashboard(
        registry,
        facts,
        selected_filter=DashboardFilter.NEEDS_ATTENTION,
    )
    completed_dashboard = project_dashboard(
        registry,
        facts,
        selected_filter=DashboardFilter.COMPLETED,
    )

    assert [item.poc_id for item in active_dashboard.pocs] == ["poc_active"]
    assert [item.poc_id for item in attention_dashboard.pocs] == ["poc_active"]
    assert [item.poc_id for item in completed_dashboard.pocs] == [
        "poc_completed"
    ]
    assert all(
        item.poc_id != "poc_archived"
        for dashboard in (
            active_dashboard,
            attention_dashboard,
            completed_dashboard,
        )
        for item in dashboard.pocs
    )


def test_dashboard_rejects_unknown_or_untyped_fact_records():
    registry = ReadOnlyPOCRegistry((_record(),))

    with pytest.raises(ValueError, match="unknown POCs"):
        project_dashboard(
            registry,
            {"poc_unknown": POCWorkflowFacts()},
        )
    with pytest.raises(TypeError, match="POCWorkflowFacts"):
        project_dashboard(
            registry,
            {"poc_support_agent": object()},
        )


def test_projection_serializes_required_workspace_contract_fields():
    projection = project_poc(_record(), POCWorkflowFacts())
    payload = projection.model_dump(mode="json")

    assert set(payload) == {
        "poc_id",
        "display_name",
        "customer_label",
        "use_case",
        "owner",
        "created_at",
        "updated_at",
        "archive_state",
        "active_contract_id",
        "active_contract_version",
        "source_summary",
        "derived_phase",
        "next_action_code",
        "next_human_action",
        "blockers",
        "latest_evidence_summary",
        "attention_required",
        "action_since",
    }
