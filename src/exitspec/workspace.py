"""Read-only POC registry and deterministic dashboard projections.

The workspace layer coordinates navigation. It does not own source review,
customer confirmation, contract freeze, measurement, or verdict authority.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from types import MappingProxyType
from typing import Iterable, Mapping, Optional, Tuple

from pydantic import Field, field_validator, model_validator

from .confirmations import ConfirmationDecision
from .models import (
    ContractStatus,
    FrozenExitSpecModel,
    RunStatus,
    VerdictStatus,
)


class ArchiveState(str, Enum):
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    ARCHIVED = "ARCHIVED"


class WorkspacePhase(str, Enum):
    DEFINE = "DEFINE"
    PROVE = "PROVE"
    DECIDE = "DECIDE"


class WorkspaceSourceType(str, Enum):
    EMAIL = "email"
    MEETING_TRANSCRIPT = "meeting_transcript"
    NOTE = "note"
    DOCUMENT = "document"
    EXISTING_CONTRACT = "existing_contract"


class DashboardFilter(str, Enum):
    ACTIVE = "Active"
    NEEDS_ATTENTION = "Needs attention"
    COMPLETED = "Completed"


class WorkspaceAction(str, Enum):
    ADD_SOURCE = "ADD_SOURCE"
    REVIEW_PROPOSALS = "REVIEW_PROPOSALS"
    DEFINE_CRITERIA = "DEFINE_CRITERIA"
    PREPARE_AGREEMENT = "PREPARE_AGREEMENT"
    CREATE_CUSTOMER_REVIEW = "CREATE_CUSTOMER_REVIEW"
    WAIT_FOR_CUSTOMER = "WAIT_FOR_CUSTOMER"
    START_REVISION = "START_REVISION"
    FREEZE_CONFIRMED_CONTRACT = "FREEZE_CONFIRMED_CONTRACT"
    RUN_POC = "RUN_POC"
    WAIT_FOR_PROOF = "WAIT_FOR_PROOF"
    RERUN_POC = "RERUN_POC"
    REVIEW_EVIDENCE = "REVIEW_EVIDENCE"
    RESOLVE_BLOCKER = "RESOLVE_BLOCKER"
    NONE = "NONE"


class WorkspaceEvidenceState(str, Enum):
    NOT_RUN = "NOT_RUN"
    PASS = "PASS"
    FAIL = "FAIL"
    BLOCKED = "BLOCKED"
    NOT_PROVEN = "NOT_PROVEN"


class POCRegistryEntry(FrozenExitSpecModel):
    """Stable POC metadata with no workflow mutation methods."""

    poc_id: str = Field(pattern=r"^poc_[a-z0-9][a-z0-9_-]{2,63}$")
    display_name: str = Field(min_length=1, max_length=160)
    customer_label: str = Field(min_length=1, max_length=160)
    use_case: str = Field(min_length=1, max_length=500)
    owner: str = Field(min_length=1, max_length=160)
    created_at: datetime
    updated_at: datetime
    archive_state: ArchiveState = ArchiveState.ACTIVE

    @field_validator("created_at", "updated_at")
    @classmethod
    def require_timezone_aware_timestamps(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Workspace timestamps must be timezone-aware.")
        return value

    @model_validator(mode="after")
    def require_monotonic_timestamps(self) -> "POCRegistryEntry":
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot precede created_at.")
        return self


class POCWorkflowFacts(FrozenExitSpecModel):
    """Authority-free facts supplied by the existing domain services."""

    state_available: bool = True
    source_count: int = Field(default=0, ge=0)
    source_types: Tuple[WorkspaceSourceType, ...] = Field(default_factory=tuple)
    pending_draft_count: int = Field(default=0, ge=0)
    kept_proposal_count: int = Field(default=0, ge=0)
    defined_criterion_count: int = Field(default=0, ge=0)
    approved_criterion_count: int = Field(default=0, ge=0)
    active_contract_id: Optional[str] = Field(
        default=None,
        pattern=r"^[a-z][a-z0-9-]{2,63}$",
    )
    active_contract_version: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=100,
    )
    contract_status: Optional[ContractStatus] = None
    customer_review_issued: bool = False
    customer_decision: Optional[ConfirmationDecision] = None
    confirmation_matches_active_contract: Optional[bool] = None
    revision_requested: bool = False
    run_status: Optional[RunStatus] = None
    verdict: Optional[VerdictStatus] = None
    verdict_reason: Optional[str] = None
    evidence_pack_url: Optional[str] = Field(default=None, max_length=500)
    action_since: Optional[datetime] = None

    @field_validator("source_types")
    @classmethod
    def require_unique_source_types(
        cls,
        source_types: Tuple[WorkspaceSourceType, ...],
    ) -> Tuple[WorkspaceSourceType, ...]:
        if len(source_types) != len(set(source_types)):
            raise ValueError("source_types must contain distinct summary values.")
        return source_types

    @field_validator("action_since")
    @classmethod
    def require_timezone_aware_action_since(
        cls,
        value: Optional[datetime],
    ) -> Optional[datetime]:
        if (
            value is not None
            and (value.tzinfo is None or value.utcoffset() is None)
        ):
            raise ValueError("action_since must be timezone-aware.")
        return value

    @model_validator(mode="after")
    def require_definition_counts_to_follow_review(
        self,
    ) -> "POCWorkflowFacts":
        if self.defined_criterion_count > self.kept_proposal_count:
            raise ValueError(
                "Defined criterion count cannot exceed kept proposal count."
            )
        return self


class SourceSummary(FrozenExitSpecModel):
    count: int = Field(ge=0)
    types: Tuple[WorkspaceSourceType, ...] = Field(default_factory=tuple)
    label: str = Field(min_length=1)


class WorkspaceBlocker(FrozenExitSpecModel):
    code: str = Field(pattern=r"^[a-z][a-z0-9_]{2,63}$")
    message: str = Field(min_length=1)


class WorkspaceEvidenceSummary(FrozenExitSpecModel):
    status: WorkspaceEvidenceState
    reason: Optional[str] = None
    report_url: Optional[str] = None


class POCWorkspaceProjection(FrozenExitSpecModel):
    poc_id: str
    display_name: str
    customer_label: str
    use_case: str
    owner: str
    created_at: datetime
    updated_at: datetime
    archive_state: ArchiveState
    active_contract_id: Optional[str]
    active_contract_version: Optional[str]
    source_summary: SourceSummary
    derived_phase: WorkspacePhase
    next_action_code: WorkspaceAction
    next_human_action: str
    blockers: Tuple[WorkspaceBlocker, ...] = Field(default_factory=tuple)
    latest_evidence_summary: WorkspaceEvidenceSummary
    attention_required: bool
    action_since: datetime


class DashboardProjection(FrozenExitSpecModel):
    selected_filter: DashboardFilter
    available_filters: Tuple[DashboardFilter, ...]
    continue_working: Optional[POCWorkspaceProjection]
    pocs: Tuple[POCWorkspaceProjection, ...]


class ReadOnlyPOCRegistry:
    """An immutable registry snapshot used by dashboard projection services."""

    __slots__ = ("_records",)

    def __init__(self, records: Iterable[POCRegistryEntry]) -> None:
        indexed = {}
        for record in records:
            if type(record) is not POCRegistryEntry:
                raise TypeError("Registry records must be POCRegistryEntry values.")
            if record.poc_id in indexed:
                raise ValueError("POC registry IDs must be unique.")
            indexed[record.poc_id] = record
        self._records = MappingProxyType(dict(sorted(indexed.items())))

    def __len__(self) -> int:
        return len(self._records)

    def ids(self) -> Tuple[str, ...]:
        return tuple(self._records)

    def get(self, poc_id: str) -> POCRegistryEntry:
        return self._records[poc_id]

    def records(self) -> Tuple[POCRegistryEntry, ...]:
        return tuple(self._records.values())


_FILTERS = (
    DashboardFilter.ACTIVE,
    DashboardFilter.NEEDS_ATTENTION,
    DashboardFilter.COMPLETED,
)
_IN_PROGRESS_RUN_STATES = {
    RunStatus.QUEUED,
    RunStatus.VALIDATING,
    RunStatus.RUNNING,
    RunStatus.AGGREGATING,
}
_TERMINAL_RUN_STATES = {
    RunStatus.COMPLETED,
    RunStatus.BLOCKED,
    RunStatus.FAILED_INTERNAL,
    RunStatus.CANCELLED,
}
_WAIT_ACTIONS = {
    WorkspaceAction.WAIT_FOR_CUSTOMER,
    WorkspaceAction.WAIT_FOR_PROOF,
    WorkspaceAction.NONE,
}


def project_dashboard(
    registry: ReadOnlyPOCRegistry,
    facts_by_poc_id: Mapping[str, POCWorkflowFacts],
    *,
    current_owner: Optional[str] = None,
    selected_filter: DashboardFilter = DashboardFilter.ACTIVE,
) -> DashboardProjection:
    """Project one bounded dashboard without changing underlying state."""

    normalized_filter = DashboardFilter(selected_filter)
    unknown_ids = sorted(set(facts_by_poc_id).difference(registry.ids()))
    if unknown_ids:
        raise ValueError(
            "Workspace facts reference unknown POCs: {0}".format(
                ", ".join(unknown_ids)
            )
        )

    projected = []
    for record in registry.records():
        facts = facts_by_poc_id.get(
            record.poc_id,
            POCWorkflowFacts(state_available=False),
        )
        if type(facts) is not POCWorkflowFacts:
            raise TypeError("Workspace facts must be POCWorkflowFacts values.")
        projected.append(project_poc(record, facts))

    ordered = tuple(
        sorted(
            projected,
            key=lambda item: (
                0 if current_owner is not None and item.owner == current_owner else 1,
                0 if item.attention_required else 1,
                item.action_since,
                item.poc_id,
            ),
        )
    )
    active = tuple(
        item for item in ordered if item.archive_state == ArchiveState.ACTIVE
    )
    visible = _apply_filter(ordered, normalized_filter)
    return DashboardProjection(
        selected_filter=normalized_filter,
        available_filters=_FILTERS,
        continue_working=active[0] if active else None,
        pocs=visible,
    )


def project_poc(
    record: POCRegistryEntry,
    facts: POCWorkflowFacts,
) -> POCWorkspaceProjection:
    """Derive phase, next action, blockers, and evidence from domain facts."""

    if type(record) is not POCRegistryEntry:
        raise TypeError("record must be a POCRegistryEntry.")
    if type(facts) is not POCWorkflowFacts:
        raise TypeError("facts must be POCWorkflowFacts.")

    blockers = list(_state_blockers(facts))
    if facts.action_since is not None and facts.action_since < record.created_at:
        blockers.append(
            WorkspaceBlocker(
                code="action_timestamp_invalid",
                message="The next-action timestamp predates this POC.",
            )
        )
    if (
        record.archive_state == ArchiveState.COMPLETED
        and facts.verdict is None
    ):
        blockers.append(
            WorkspaceBlocker(
                code="completed_without_verdict",
                message="The completed POC has no terminal evidence verdict.",
            )
        )
    frozen_blockers = tuple(blockers)
    source_summary = _source_summary(facts)
    evidence = _evidence_summary(facts)
    action_since = facts.action_since or record.updated_at

    if frozen_blockers:
        phase = WorkspacePhase.DEFINE
        action = WorkspaceAction.RESOLVE_BLOCKER
        action_text = frozen_blockers[0].message
    else:
        phase, action, action_text = _derive_action(record, facts)

    return POCWorkspaceProjection(
        poc_id=record.poc_id,
        display_name=record.display_name,
        customer_label=record.customer_label,
        use_case=record.use_case,
        owner=record.owner,
        created_at=record.created_at,
        updated_at=record.updated_at,
        archive_state=record.archive_state,
        active_contract_id=facts.active_contract_id,
        active_contract_version=facts.active_contract_version,
        source_summary=source_summary,
        derived_phase=phase,
        next_action_code=action,
        next_human_action=action_text,
        blockers=frozen_blockers,
        latest_evidence_summary=evidence,
        attention_required=bool(frozen_blockers or action not in _WAIT_ACTIONS),
        action_since=action_since,
    )


def _apply_filter(
    projected: Tuple[POCWorkspaceProjection, ...],
    selected_filter: DashboardFilter,
) -> Tuple[POCWorkspaceProjection, ...]:
    if selected_filter == DashboardFilter.ACTIVE:
        return tuple(
            item for item in projected if item.archive_state == ArchiveState.ACTIVE
        )
    if selected_filter == DashboardFilter.NEEDS_ATTENTION:
        return tuple(
            item
            for item in projected
            if item.archive_state == ArchiveState.ACTIVE
            and item.attention_required
        )
    return tuple(
        item for item in projected if item.archive_state == ArchiveState.COMPLETED
    )


def _state_blockers(facts: POCWorkflowFacts) -> Tuple[WorkspaceBlocker, ...]:
    blockers = []

    def add(code: str, message: str) -> None:
        blockers.append(WorkspaceBlocker(code=code, message=message))

    if not facts.state_available:
        add(
            "workspace_state_unavailable",
            "POC state is unavailable. Reload before making a decision.",
        )
        return tuple(blockers)

    if facts.source_count == 0 and facts.source_types:
        add(
            "source_summary_inconsistent",
            "Source summary is inconsistent. Review the POC state.",
        )
    elif facts.source_count < len(facts.source_types):
        add(
            "source_summary_inconsistent",
            "Source summary is inconsistent. Review the POC state.",
        )

    identity_values = (
        facts.active_contract_id,
        facts.active_contract_version,
        facts.contract_status,
    )
    if any(value is not None for value in identity_values) and any(
        value is None for value in identity_values
    ):
        add(
            "contract_identity_incomplete",
            "The active agreement identity is incomplete. Review the POC state.",
        )

    contract_exists = all(value is not None for value in identity_values)
    customer_state_exists = bool(
        facts.customer_review_issued
        or facts.customer_decision is not None
        or facts.confirmation_matches_active_contract is not None
    )
    if customer_state_exists and not contract_exists:
        add(
            "customer_state_without_contract",
            "Customer review state is not bound to an active agreement.",
        )

    if (
        facts.confirmation_matches_active_contract is not None
        and facts.customer_decision is None
    ):
        add(
            "confirmation_match_without_decision",
            "Confirmation binding state has no customer decision.",
        )

    if facts.customer_decision is not None and not facts.customer_review_issued:
        add(
            "decision_without_review",
            "Customer decision has no issued review record.",
        )

    if facts.customer_decision == ConfirmationDecision.CONFIRM and (
        facts.confirmation_matches_active_contract is not True
    ):
        add(
            "confirmation_binding_mismatch",
            "Customer confirmation does not match the active agreement version.",
        )

    if (
        facts.contract_status == ContractStatus.FROZEN
        and facts.customer_decision != ConfirmationDecision.CONFIRM
    ):
        add(
            "frozen_without_confirmation",
            "The frozen agreement has no matching affirmative customer decision.",
        )

    if facts.revision_requested and facts.contract_status == ContractStatus.FROZEN:
        add(
            "revision_conflicts_with_frozen_contract",
            "A requested revision cannot mutate the frozen agreement.",
        )

    if (
        facts.contract_status == ContractStatus.FROZEN
        and facts.pending_draft_count > 0
    ):
        add(
            "pending_drafts_with_frozen_contract",
            "Pending proposals cannot belong to the frozen agreement.",
        )

    if facts.contract_status == ContractStatus.SUPERSEDED:
        add(
            "active_contract_superseded",
            "The active agreement points to a superseded version.",
        )

    if (
        facts.contract_status in {ContractStatus.APPROVED, ContractStatus.FROZEN}
        and facts.approved_criterion_count == 0
    ):
        add(
            "approved_contract_without_criteria",
            "The approved agreement has no approved executable requirement.",
        )

    if facts.run_status is not None and facts.contract_status != ContractStatus.FROZEN:
        add(
            "run_without_frozen_contract",
            "Proof state is not bound to a frozen agreement.",
        )

    if facts.verdict is not None and facts.run_status not in _TERMINAL_RUN_STATES:
        add(
            "verdict_without_terminal_run",
            "Evidence verdict is not bound to a terminal proof run.",
        )

    if facts.evidence_pack_url is not None and facts.verdict is None:
        add(
            "evidence_pack_without_verdict",
            "Evidence Pack is not bound to a terminal verdict.",
        )

    if facts.verdict is not None and facts.evidence_pack_url is None:
        add(
            "verdict_without_evidence_pack",
            "The terminal verdict has no inspectable Evidence Pack.",
        )

    if (
        facts.evidence_pack_url is not None
        and not facts.evidence_pack_url.startswith("/artifacts/")
    ):
        add(
            "evidence_pack_url_invalid",
            "Evidence Pack URL is outside the artifact boundary.",
        )

    if (
        facts.run_status in {
            RunStatus.COMPLETED,
            RunStatus.BLOCKED,
            RunStatus.FAILED_INTERNAL,
        }
        and facts.verdict is None
    ):
        add(
            "terminal_run_without_verdict",
            "The terminal proof run has no deterministic verdict.",
        )

    if facts.verdict is not None and not facts.verdict_reason:
        add(
            "verdict_reason_missing",
            "The evidence verdict has no inspectable reason.",
        )

    if (
        facts.run_status == RunStatus.BLOCKED
        and facts.verdict is not None
        and facts.verdict != VerdictStatus.BLOCKED
    ):
        add(
            "blocked_run_verdict_mismatch",
            "A blocked proof run must retain a BLOCKED verdict.",
        )

    if (
        facts.run_status == RunStatus.FAILED_INTERNAL
        and facts.verdict is not None
        and facts.verdict != VerdictStatus.NOT_PROVEN
    ):
        add(
            "internal_failure_verdict_mismatch",
            "An internal execution failure cannot become a customer FAIL or PASS.",
        )

    return tuple(blockers)


def _source_summary(facts: POCWorkflowFacts) -> SourceSummary:
    ordered_types = tuple(
        sorted(facts.source_types, key=lambda source_type: source_type.value)
    )
    if facts.source_count == 0:
        label = "No source"
    else:
        names = " + ".join(_source_type_label(value) for value in ordered_types)
        suffix = "source" if facts.source_count == 1 else "sources"
        label = "{0} {1}".format(facts.source_count, suffix)
        if names:
            label = "{0} · {1}".format(label, names)
    return SourceSummary(
        count=facts.source_count,
        types=ordered_types,
        label=label,
    )


def _source_type_label(source_type: WorkspaceSourceType) -> str:
    return {
        WorkspaceSourceType.EMAIL: "Email",
        WorkspaceSourceType.MEETING_TRANSCRIPT: "Meeting transcript",
        WorkspaceSourceType.NOTE: "Note",
        WorkspaceSourceType.DOCUMENT: "Document",
        WorkspaceSourceType.EXISTING_CONTRACT: "Existing contract",
    }[source_type]


def _evidence_summary(facts: POCWorkflowFacts) -> WorkspaceEvidenceSummary:
    if facts.verdict is None:
        return WorkspaceEvidenceSummary(status=WorkspaceEvidenceState.NOT_RUN)
    return WorkspaceEvidenceSummary(
        status=WorkspaceEvidenceState(facts.verdict.value),
        reason=facts.verdict_reason,
        report_url=facts.evidence_pack_url,
    )


def _derive_action(
    record: POCRegistryEntry,
    facts: POCWorkflowFacts,
) -> Tuple[WorkspacePhase, WorkspaceAction, str]:
    if record.archive_state == ArchiveState.ARCHIVED:
        return (
            WorkspacePhase.DECIDE,
            WorkspaceAction.NONE,
            "This POC is archived.",
        )

    if facts.verdict is not None:
        return _verdict_action(facts.verdict)

    if facts.contract_status == ContractStatus.FROZEN:
        if facts.run_status in _IN_PROGRESS_RUN_STATES:
            return (
                WorkspacePhase.PROVE,
                WorkspaceAction.WAIT_FOR_PROOF,
                "Wait for the current proof run to finish.",
            )
        if facts.run_status in {RunStatus.CANCELLED, RunStatus.FAILED_INTERNAL}:
            return (
                WorkspacePhase.PROVE,
                WorkspaceAction.RERUN_POC,
                "Resolve the execution issue, then rerun this POC.",
            )
        return (
            WorkspacePhase.PROVE,
            WorkspaceAction.RUN_POC,
            "Run this POC against the frozen agreement.",
        )

    if facts.revision_requested or (
        facts.customer_decision == ConfirmationDecision.REQUEST_CHANGES
    ):
        return (
            WorkspacePhase.DEFINE,
            WorkspaceAction.START_REVISION,
            "Revise the customer-requested agreement.",
        )

    if facts.contract_status == ContractStatus.APPROVED:
        if facts.customer_decision == ConfirmationDecision.CONFIRM:
            return (
                WorkspacePhase.DEFINE,
                WorkspaceAction.FREEZE_CONFIRMED_CONTRACT,
                "Freeze confirmed contract.",
            )
        if facts.customer_review_issued:
            return (
                WorkspacePhase.DEFINE,
                WorkspaceAction.WAIT_FOR_CUSTOMER,
                "Wait for the customer decision on this exact version.",
            )
        return (
            WorkspacePhase.DEFINE,
            WorkspaceAction.CREATE_CUSTOMER_REVIEW,
            "Create the customer review for this agreement.",
        )

    if facts.pending_draft_count > 0:
        return (
            WorkspacePhase.DEFINE,
            WorkspaceAction.REVIEW_PROPOSALS,
            "Review {0} requirement proposal{1}.".format(
                facts.pending_draft_count,
                "" if facts.pending_draft_count == 1 else "s",
            ),
        )

    undefined_kept_count = (
        facts.kept_proposal_count - facts.defined_criterion_count
    )
    if undefined_kept_count > 0:
        return (
            WorkspacePhase.DEFINE,
            WorkspaceAction.DEFINE_CRITERIA,
            "Define acceptance criteria for {0} kept proposal{1}.".format(
                undefined_kept_count,
                "" if undefined_kept_count == 1 else "s",
            ),
        )

    if facts.contract_status in {ContractStatus.DRAFT, ContractStatus.IN_REVIEW}:
        return (
            WorkspacePhase.DEFINE,
            WorkspaceAction.PREPARE_AGREEMENT,
            "Complete internal review and prepare the agreement.",
        )

    if facts.approved_criterion_count > 0:
        return (
            WorkspacePhase.DEFINE,
            WorkspaceAction.PREPARE_AGREEMENT,
            "Prepare the customer-visible agreement.",
        )

    if facts.defined_criterion_count > 0:
        return (
            WorkspacePhase.DEFINE,
            WorkspaceAction.PREPARE_AGREEMENT,
            "Prepare an agreement from {0} defined acceptance {1}.".format(
                facts.defined_criterion_count,
                (
                    "criterion"
                    if facts.defined_criterion_count == 1
                    else "criteria"
                ),
            ),
        )

    if facts.source_count > 0:
        return (
            WorkspacePhase.DEFINE,
            WorkspaceAction.PREPARE_AGREEMENT,
            "Define an executable requirement from the reviewed source.",
        )

    return (
        WorkspacePhase.DEFINE,
        WorkspaceAction.ADD_SOURCE,
        "Add a source to begin defining the POC.",
    )


def _verdict_action(
    verdict: VerdictStatus,
) -> Tuple[WorkspacePhase, WorkspaceAction, str]:
    if verdict == VerdictStatus.PASS:
        return (
            WorkspacePhase.DECIDE,
            WorkspaceAction.REVIEW_EVIDENCE,
            "Review and share the Evidence Pack. PASS is not authorization.",
        )
    if verdict == VerdictStatus.FAIL:
        return (
            WorkspacePhase.DECIDE,
            WorkspaceAction.REVIEW_EVIDENCE,
            "Review the failed criterion and decide whether to revise or stop.",
        )
    if verdict == VerdictStatus.BLOCKED:
        return (
            WorkspacePhase.DECIDE,
            WorkspaceAction.RERUN_POC,
            "Resolve the external blocker, then rerun the frozen agreement.",
        )
    return (
        WorkspacePhase.DECIDE,
        WorkspaceAction.RERUN_POC,
        "Collect sufficient valid evidence, then rerun the frozen agreement.",
    )
