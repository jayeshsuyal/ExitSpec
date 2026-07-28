from datetime import datetime, timezone

import pytest

from exitspec.draft_workspace import (
    draft_workspace_record_and_facts,
    project_draft_dashboard,
)
from exitspec.poc_creation import (
    DraftPOCCreateRequest,
    ProcessLocalDraftPOCService,
)
from exitspec.poc_source_intake import POCSourceReceipt
from exitspec.workspace import (
    ArchiveState,
    DashboardFilter,
    WorkspaceAction,
    WorkspaceSourceType,
)


NOW = datetime(2026, 7, 28, 22, 0, tzinfo=timezone.utc)


def _drafts(*poc_ids: str) -> ProcessLocalDraftPOCService:
    ids = iter(poc_ids)
    service = ProcessLocalDraftPOCService(
        clock=lambda: NOW,
        poc_id_factory=lambda: next(ids),
    )
    for number, _ in enumerate(poc_ids, start=1):
        service.create(
            DraftPOCCreateRequest(
                display_name=f"Customer POC {number}",
                customer_label=f"Customer {number}",
                use_case="Validate one bounded customer requirement.",
                owner="field_engineer",
                first_source_choice="MEETING",
            ),
            idempotency_key=f"create-draft-{number}",
        )
    return service


def _receipt(
    poc_id: str,
    *,
    receipt_id: str = "srcpt_workspace_001",
    source_kind: str = "MEETING",
    proposal_count: int = 2,
) -> POCSourceReceipt:
    return POCSourceReceipt(
        poc_id=poc_id,
        source_kind=source_kind,
        source_receipt_id=receipt_id,
        proposal_count=proposal_count,
        status="NEEDS_REVIEW",
        idempotent_replay=False,
    )


def test_new_draft_projects_add_source_without_fake_workflow_state():
    service = _drafts("poc_workspace_alpha")

    record, facts = draft_workspace_record_and_facts(
        service.get("poc_workspace_alpha"),
        (),
    )
    dashboard = project_draft_dashboard(service.snapshots(), {})
    projected = dashboard.pocs[0]

    assert record.archive_state == ArchiveState.ACTIVE
    assert facts.source_count == 0
    assert facts.pending_draft_count == 0
    assert projected.next_action_code == WorkspaceAction.ADD_SOURCE
    assert projected.next_human_action == "Add a source to begin defining the POC."
    assert projected.active_contract_id is None
    assert projected.latest_evidence_summary.status == "NOT_RUN"


@pytest.mark.parametrize(
    ("source_kind", "workspace_type", "label"),
    (
        ("EMAIL", WorkspaceSourceType.EMAIL, "Email"),
        (
            "MEETING",
            WorkspaceSourceType.MEETING_TRANSCRIPT,
            "Meeting transcript",
        ),
        ("DOCUMENT", WorkspaceSourceType.DOCUMENT, "Document"),
        (
            "EXISTING_CONTRACT",
            WorkspaceSourceType.EXISTING_CONTRACT,
            "Existing contract",
        ),
    ),
)
def test_every_source_kind_has_an_honest_dashboard_projection(
    source_kind,
    workspace_type,
    label,
):
    service = _drafts("poc_workspace_alpha")
    receipt = _receipt("poc_workspace_alpha", source_kind=source_kind)

    dashboard = project_draft_dashboard(
        service.snapshots(),
        {"poc_workspace_alpha": (receipt,)},
    )
    projected = dashboard.pocs[0]

    assert projected.source_summary.count == 1
    assert projected.source_summary.types == (workspace_type,)
    assert projected.source_summary.label == f"1 source · {label}"
    assert projected.next_action_code == WorkspaceAction.REVIEW_PROPOSALS
    assert projected.next_human_action == "Review 2 requirement proposals."
    assert projected.active_contract_id is None


def test_zero_candidate_source_still_requires_human_definition():
    service = _drafts("poc_workspace_alpha")
    receipt = _receipt("poc_workspace_alpha", proposal_count=0)

    projected = project_draft_dashboard(
        service.snapshots(),
        {"poc_workspace_alpha": (receipt,)},
    ).pocs[0]

    assert projected.next_action_code == WorkspaceAction.REVIEW_PROPOSALS
    assert projected.next_human_action == (
        "Define an executable requirement from the reviewed source."
    )


def test_archived_draft_is_not_present_in_active_filter():
    service = _drafts("poc_workspace_alpha")
    service.archive("poc_workspace_alpha")

    active = project_draft_dashboard(service.snapshots(), {})
    completed = project_draft_dashboard(
        service.snapshots(),
        {},
        selected_filter=DashboardFilter.COMPLETED,
    )

    assert active.pocs == ()
    assert active.continue_working is None
    assert completed.pocs == ()


def test_multiple_sources_are_counted_but_types_are_deduplicated():
    service = _drafts("poc_workspace_alpha")
    receipts = (
        _receipt(
            "poc_workspace_alpha",
            receipt_id="srcpt_workspace_001",
            proposal_count=1,
        ),
        _receipt(
            "poc_workspace_alpha",
            receipt_id="srcpt_workspace_002",
            proposal_count=3,
        ),
    )

    projected = project_draft_dashboard(
        service.snapshots(),
        {"poc_workspace_alpha": receipts},
    ).pocs[0]

    assert projected.source_summary.count == 2
    assert projected.source_summary.types == (
        WorkspaceSourceType.MEETING_TRANSCRIPT,
    )
    assert projected.next_human_action == "Review 4 requirement proposals."


def test_cross_poc_receipts_and_unknown_receipt_maps_fail_closed():
    service = _drafts("poc_workspace_alpha", "poc_workspace_beta")

    with pytest.raises(ValueError, match="belong"):
        draft_workspace_record_and_facts(
            service.get("poc_workspace_alpha"),
            (_receipt("poc_workspace_beta"),),
        )
    with pytest.raises(ValueError, match="unknown"):
        project_draft_dashboard(
            service.snapshots(),
            {"poc_workspace_unknown": ()},
        )
