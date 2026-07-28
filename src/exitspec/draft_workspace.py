"""Read-only dashboard projection for process-local draft POCs."""

from __future__ import annotations

from typing import Mapping, Sequence, Tuple

from .poc_creation import (
    DraftPOCArchiveState,
    DraftPOCSnapshot,
)
from .poc_source_intake import POCSourceReceipt
from .poc_sources import SourceKind
from .workspace import (
    ArchiveState,
    DashboardFilter,
    DashboardProjection,
    POCRegistryEntry,
    POCWorkflowFacts,
    ReadOnlyPOCRegistry,
    WorkspaceSourceType,
    project_dashboard,
)


_WORKSPACE_SOURCE_BY_KIND = {
    SourceKind.EMAIL: WorkspaceSourceType.EMAIL,
    SourceKind.MEETING: WorkspaceSourceType.MEETING_TRANSCRIPT,
    SourceKind.DOCUMENT: WorkspaceSourceType.DOCUMENT,
    SourceKind.EXISTING_CONTRACT: WorkspaceSourceType.EXISTING_CONTRACT,
}


def draft_workspace_record_and_facts(
    draft: DraftPOCSnapshot,
    receipts: Sequence[POCSourceReceipt],
    *,
    pending_proposal_count: int | None = None,
) -> tuple[POCRegistryEntry, POCWorkflowFacts]:
    """Project one draft and safe source receipts without mutating either."""

    if type(draft) is not DraftPOCSnapshot:
        raise TypeError("draft must be a DraftPOCSnapshot.")
    validated_receipts = tuple(receipts)
    if any(type(receipt) is not POCSourceReceipt for receipt in validated_receipts):
        raise TypeError("receipts must contain POCSourceReceipt values.")
    if any(receipt.poc_id != draft.poc_id for receipt in validated_receipts):
        raise ValueError("Source receipts must belong to the projected draft POC.")
    total_proposals = sum(
        receipt.proposal_count for receipt in validated_receipts
    )
    resolved_pending_count = (
        total_proposals
        if pending_proposal_count is None
        else pending_proposal_count
    )
    if (
        type(resolved_pending_count) is not int
        or not 0 <= resolved_pending_count <= total_proposals
    ):
        raise ValueError(
            "Pending proposal count must be between zero and the source total."
        )

    archive_state = (
        ArchiveState.ACTIVE
        if draft.archive_state == DraftPOCArchiveState.ACTIVE
        else ArchiveState.ARCHIVED
    )
    record = POCRegistryEntry(
        poc_id=draft.poc_id,
        display_name=draft.display_name,
        customer_label=draft.customer_label,
        use_case=draft.use_case,
        owner=draft.owner,
        created_at=draft.created_at,
        updated_at=draft.updated_at,
        archive_state=archive_state,
    )
    source_types = tuple(
        sorted(
            {
                _WORKSPACE_SOURCE_BY_KIND[receipt.source_kind]
                for receipt in validated_receipts
            },
            key=lambda source_type: source_type.value,
        )
    )
    facts = POCWorkflowFacts(
        source_count=len(validated_receipts),
        source_types=source_types,
        pending_draft_count=resolved_pending_count,
        action_since=draft.updated_at,
    )
    return record, facts


def project_draft_dashboard(
    drafts: Sequence[DraftPOCSnapshot],
    receipts_by_poc_id: Mapping[str, Sequence[POCSourceReceipt]],
    *,
    pending_proposal_counts_by_poc_id: Mapping[str, int] | None = None,
    selected_filter: DashboardFilter = DashboardFilter.ACTIVE,
) -> DashboardProjection:
    """Project all current process-local drafts into the standard dashboard."""

    validated_drafts = tuple(drafts)
    if any(type(draft) is not DraftPOCSnapshot for draft in validated_drafts):
        raise TypeError("drafts must contain DraftPOCSnapshot values.")
    draft_ids = tuple(draft.poc_id for draft in validated_drafts)
    if len(draft_ids) != len(set(draft_ids)):
        raise ValueError("Draft workspace identities must be unique.")
    unknown_receipt_ids = sorted(set(receipts_by_poc_id).difference(draft_ids))
    if unknown_receipt_ids:
        raise ValueError(
            "Source receipts reference unknown draft POCs: {0}".format(
                ", ".join(unknown_receipt_ids)
            )
        )
    resolved_pending_counts = (
        {}
        if pending_proposal_counts_by_poc_id is None
        else dict(pending_proposal_counts_by_poc_id)
    )
    unknown_pending_ids = sorted(
        set(resolved_pending_counts).difference(draft_ids)
    )
    if unknown_pending_ids:
        raise ValueError(
            "Pending proposal counts reference unknown draft POCs: {0}".format(
                ", ".join(unknown_pending_ids)
            )
        )

    records = []
    facts_by_poc_id = {}
    for draft in validated_drafts:
        record, facts = draft_workspace_record_and_facts(
            draft,
            receipts_by_poc_id.get(draft.poc_id, ()),
            pending_proposal_count=resolved_pending_counts.get(draft.poc_id),
        )
        records.append(record)
        facts_by_poc_id[draft.poc_id] = facts
    return project_dashboard(
        ReadOnlyPOCRegistry(records),
        facts_by_poc_id,
        selected_filter=selected_filter,
    )


__all__ = [
    "draft_workspace_record_and_facts",
    "project_draft_dashboard",
]
