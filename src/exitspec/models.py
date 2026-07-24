"""Strict domain models for ExitSpec's trusted decision boundary."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import List, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


SHA256_PATTERN = r"^[a-f0-9]{64}$"


class ExitSpecModel(BaseModel):
    """Base model that rejects undocumented fields at the contract boundary."""

    model_config = ConfigDict(extra="forbid")


class FrozenExitSpecModel(ExitSpecModel):
    """Immutable base for every object participating in a contract digest."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class ContractStatus(str, Enum):
    DRAFT = "DRAFT"
    IN_REVIEW = "IN_REVIEW"
    APPROVED = "APPROVED"
    FROZEN = "FROZEN"
    SUPERSEDED = "SUPERSEDED"


class RunStatus(str, Enum):
    QUEUED = "QUEUED"
    VALIDATING = "VALIDATING"
    RUNNING = "RUNNING"
    AGGREGATING = "AGGREGATING"
    COMPLETED = "COMPLETED"
    BLOCKED = "BLOCKED"
    FAILED_INTERNAL = "FAILED_INTERNAL"
    CANCELLED = "CANCELLED"


class VerdictStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    BLOCKED = "BLOCKED"
    NOT_PROVEN = "NOT_PROVEN"


class DraftStatus(str, Enum):
    """Review state for a candidate criterion, before it becomes contract input."""

    NEEDS_REVIEW = "NEEDS_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class ReviewDecision(str, Enum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"


class ComparisonOperator(str, Enum):
    GTE = "gte"
    LTE = "lte"
    GT = "gt"
    LT = "lt"
    EQ = "eq"


class Metric(str, Enum):
    EXACT_TOOL_SELECTION_RATE = "exact_tool_selection_rate"


class ConfidenceMethod(str, Enum):
    WILSON_TWO_SIDED_LOWER_BOUND = "wilson_two_sided_lower_bound"


class SourceReference(FrozenExitSpecModel):
    speaker: str = Field(min_length=1)
    quote: str = Field(min_length=1)
    location: str = Field(min_length=1)


class TranscriptLine(ExitSpecModel):
    """One normalized line of discovery text retained for source review."""

    line_number: int = Field(gt=0)
    speaker: str = Field(min_length=1)
    text: str = Field(min_length=1)


class DiscoveryTranscript(ExitSpecModel):
    """A bounded discovery source; it is authoring input, not verdict evidence."""

    id: str = Field(pattern=r"^[a-z][a-z0-9-]{2,63}$")
    title: str = Field(min_length=1)
    synthetic: bool
    lines: List[TranscriptLine] = Field(min_length=1)

    @field_validator("lines")
    @classmethod
    def require_contiguous_line_numbers(
        cls, lines: List[TranscriptLine]
    ) -> List[TranscriptLine]:
        expected = list(range(1, len(lines) + 1))
        actual = [line.line_number for line in lines]
        if actual != expected:
            raise ValueError("Transcript line numbers must be contiguous and start at 1.")
        return lines


class TranscriptSpan(ExitSpecModel):
    """A quote anchored to a specific speaker and line range in a transcript."""

    transcript_id: str = Field(pattern=r"^[a-z][a-z0-9-]{2,63}$")
    start_line: int = Field(gt=0)
    end_line: int = Field(gt=0)
    speaker: str = Field(min_length=1)
    quote: str = Field(min_length=1)

    @model_validator(mode="after")
    def require_valid_line_range(self) -> "TranscriptSpan":
        if self.end_line < self.start_line:
            raise ValueError("Transcript span end_line cannot precede start_line.")
        return self

    def to_source_reference(self) -> SourceReference:
        line_range = (
            str(self.start_line)
            if self.start_line == self.end_line
            else "{0}-{1}".format(self.start_line, self.end_line)
        )
        return SourceReference(
            speaker=self.speaker,
            quote=self.quote,
            location="{0}:{1}".format(self.transcript_id, line_range),
        )


class WorkloadReference(FrozenExitSpecModel):
    fixture_path: str = Field(min_length=1)
    sha256: str = Field(pattern=SHA256_PATTERN)


class TargetSystem(FrozenExitSpecModel):
    provider: str = Field(min_length=1)
    endpoint_class: str = Field(min_length=1)
    model: str = Field(min_length=1)


class ProportionRule(FrozenExitSpecModel):
    operator: ComparisonOperator = ComparisonOperator.GTE
    threshold: float = Field(ge=0.0, le=1.0)
    minimum_samples: int = Field(gt=0)
    confidence_level: float = Field(default=0.95, gt=0.0, lt=1.0)
    confidence_method: ConfidenceMethod = (
        ConfidenceMethod.WILSON_TWO_SIDED_LOWER_BOUND
    )

    @model_validator(mode="after")
    def require_supported_comparison(self) -> "ProportionRule":
        if self.operator != ComparisonOperator.GTE:
            raise ValueError(
                "Brick 1 supports only a greater-than-or-equal proportion rule."
            )
        return self


class Criterion(FrozenExitSpecModel):
    id: str = Field(pattern=r"^[A-Z][A-Z0-9-]{2,63}$")
    title: str = Field(min_length=1)
    must_have: bool = True
    source: Optional[SourceReference] = None
    human_added: bool = False
    normalized_claim: str = Field(min_length=1)
    metric: Metric
    unit: str = Field(min_length=1)
    aggregation: str = Field(min_length=1)
    rule: ProportionRule
    workload_slice: str = Field(min_length=1)
    adapter: str = Field(min_length=1)
    adapter_version: str = Field(min_length=1)
    owner: str = Field(min_length=1)
    evidence_policy: str = Field(min_length=1)
    approved: bool = False

    @model_validator(mode="after")
    def require_traceable_origin(self) -> "Criterion":
        if self.source is None and not self.human_added:
            raise ValueError(
                "A criterion needs a source reference or must be explicitly human-added."
            )
        return self


class CriterionReview(ExitSpecModel):
    """An explicit human decision on a proposed criterion."""

    reviewer: str = Field(min_length=1)
    decision: ReviewDecision
    rationale: str = Field(min_length=1)
    reviewed_at: datetime


class CriterionDraft(ExitSpecModel):
    """A source-linked proposal that must be reviewed before contract assembly."""

    id: str = Field(pattern=r"^[A-Z][A-Z0-9-]{2,63}$")
    status: DraftStatus = DraftStatus.NEEDS_REVIEW
    source_span: Optional[TranscriptSpan] = None
    human_added: bool = False
    human_added_rationale: Optional[str] = None
    normalized_claim: str = Field(min_length=1)
    proposed_criterion: Optional[Criterion] = None
    open_questions: List[str] = Field(default_factory=list)
    review: Optional[CriterionReview] = None

    @field_validator("open_questions")
    @classmethod
    def require_nonblank_open_questions(cls, questions: List[str]) -> List[str]:
        if any(not question.strip() for question in questions):
            raise ValueError("Open questions cannot be blank.")
        return questions

    @model_validator(mode="after")
    def validate_authoring_boundary(self) -> "CriterionDraft":
        if self.source_span is None and not self.human_added:
            raise ValueError(
                "A criterion draft needs a transcript span or must be explicitly human-added."
            )
        if self.source_span is not None and self.human_added:
            raise ValueError("A criterion draft cannot be both source-linked and human-added.")
        if self.human_added and not self.human_added_rationale:
            raise ValueError("Human-added drafts require human_added_rationale.")
        if not self.human_added and self.human_added_rationale is not None:
            raise ValueError(
                "human_added_rationale is only valid for explicitly human-added drafts."
            )

        if self.proposed_criterion is not None:
            if self.proposed_criterion.normalized_claim != self.normalized_claim:
                raise ValueError(
                    "A proposed criterion must preserve the draft normalized claim."
                )
            if self.source_span is not None:
                expected_source = self.source_span.to_source_reference()
                if (
                    self.proposed_criterion.source != expected_source
                    or self.proposed_criterion.human_added
                ):
                    raise ValueError(
                        "A proposed criterion must preserve the draft transcript source."
                    )
            elif (
                self.proposed_criterion.source is not None
                or not self.proposed_criterion.human_added
            ):
                raise ValueError(
                    "A human-added draft must produce an explicitly human-added criterion."
                )

        if self.status == DraftStatus.NEEDS_REVIEW and self.review is not None:
            raise ValueError("Drafts needing review cannot already have a review decision.")
        if self.status == DraftStatus.APPROVED:
            if self.review is None or self.review.decision != ReviewDecision.APPROVE:
                raise ValueError("Approved drafts require an explicit approval review.")
            if self.proposed_criterion is None:
                raise ValueError("Approved drafts require a complete proposed criterion.")
            if self.open_questions:
                raise ValueError("Approved drafts cannot retain unresolved open questions.")
            if not self.proposed_criterion.approved:
                raise ValueError("Approved drafts must contain an approved criterion.")
        if self.status == DraftStatus.REJECTED and (
            self.review is None or self.review.decision != ReviewDecision.REJECT
        ):
            raise ValueError("Rejected drafts require an explicit rejection review.")
        return self


class ReviewAction(ExitSpecModel):
    """A repeatable instruction for applying a human review decision in a demo."""

    draft_id: str = Field(pattern=r"^[A-Z][A-Z0-9-]{2,63}$")
    decision: ReviewDecision
    reviewer: str = Field(min_length=1)
    rationale: str = Field(min_length=1)


class ReviewPlan(ExitSpecModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9-]{2,63}$")
    actions: List[ReviewAction] = Field(min_length=1)

    @field_validator("actions")
    @classmethod
    def require_unique_draft_actions(
        cls, actions: List[ReviewAction]
    ) -> List[ReviewAction]:
        draft_ids = [action.draft_id for action in actions]
        if len(draft_ids) != len(set(draft_ids)):
            raise ValueError("A review plan can contain only one action per draft.")
        return actions


class DiscoveryPack(ExitSpecModel):
    """Transcript plus candidate criteria; the authoring input for the Define step."""

    transcript: DiscoveryTranscript
    drafts: List[CriterionDraft] = Field(min_length=1)

    @field_validator("drafts")
    @classmethod
    def require_unique_draft_ids(
        cls, drafts: List[CriterionDraft]
    ) -> List[CriterionDraft]:
        draft_ids = [draft.id for draft in drafts]
        if len(draft_ids) != len(set(draft_ids)):
            raise ValueError("Criterion draft IDs must be unique within a discovery pack.")
        return drafts

    @model_validator(mode="after")
    def verify_draft_sources_against_transcript(self) -> "DiscoveryPack":
        for draft in self.drafts:
            span = draft.source_span
            if span is None:
                continue
            if span.transcript_id != self.transcript.id:
                raise ValueError(
                    "A transcript span must reference the discovery pack transcript."
                )
            if span.end_line > len(self.transcript.lines):
                raise ValueError("A transcript span extends beyond the transcript.")

            selected_lines = self.transcript.lines[
                span.start_line - 1 : span.end_line
            ]
            if any(line.speaker != span.speaker for line in selected_lines):
                raise ValueError(
                    "A transcript span must contain lines from its declared speaker only."
                )
            source_text = " ".join(line.text for line in selected_lines)
            normalized_quote = " ".join(span.quote.split())
            normalized_source = " ".join(source_text.split())
            if normalized_quote not in normalized_source:
                raise ValueError(
                    "A transcript span quote must appear in its declared transcript lines."
                )
        return self


class ContractSeed(ExitSpecModel):
    """Contract metadata supplied before reviewed drafts become criteria."""

    id: str = Field(pattern=r"^[a-z][a-z0-9-]{2,63}$")
    version: str = Field(min_length=1)
    created_at: datetime
    customer: str = Field(min_length=1)
    use_case: str = Field(min_length=1)
    target_system: TargetSystem
    workload: WorkloadReference
    owners: List[str] = Field(min_length=1)
    non_goals: List[str] = Field(default_factory=list)
    evidence_retention_policy: str = Field(min_length=1)


class POCContract(FrozenExitSpecModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9-]{2,63}$")
    version: str = Field(min_length=1)
    status: ContractStatus = ContractStatus.DRAFT
    created_at: datetime
    approved_at: Optional[datetime] = None
    frozen_at: Optional[datetime] = None
    customer: str = Field(min_length=1)
    use_case: str = Field(min_length=1)
    target_system: TargetSystem
    workload: WorkloadReference
    criteria: Tuple[Criterion, ...] = Field(min_length=1)
    owners: Tuple[str, ...] = Field(min_length=1)
    non_goals: Tuple[str, ...] = Field(default_factory=tuple)
    evidence_retention_policy: str = Field(min_length=1)
    parent_version: Optional[str] = None
    confirmation_id: Optional[str] = Field(
        default=None,
        pattern=r"^cnf_[a-f0-9]{64}$",
    )
    canonical_hash: Optional[str] = Field(default=None, pattern=SHA256_PATTERN)

    @field_validator("criteria")
    @classmethod
    def require_unique_criterion_ids(
        cls, criteria: Tuple[Criterion, ...]
    ) -> Tuple[Criterion, ...]:
        ids = [criterion.id for criterion in criteria]
        if len(ids) != len(set(ids)):
            raise ValueError("Criterion IDs must be unique within a contract version.")
        return criteria

    @model_validator(mode="after")
    def validate_lifecycle_requirements(self) -> "POCContract":
        if self.status in (ContractStatus.APPROVED, ContractStatus.FROZEN):
            unapproved = [criterion.id for criterion in self.criteria if not criterion.approved]
            if unapproved:
                raise ValueError(
                    "Approved and frozen contracts require all criteria to be approved: "
                    + ", ".join(unapproved)
                )
        if self.status == ContractStatus.FROZEN and self.frozen_at is None:
            raise ValueError("Frozen contracts require frozen_at.")
        if self.status != ContractStatus.FROZEN and self.confirmation_id is not None:
            raise ValueError(
                "Only a frozen contract may carry customer confirmation provenance."
            )
        return self


class ToolSelectionFixtureCase(ExitSpecModel):
    case_id: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    expected_tool: str = Field(min_length=1)


class ToolSelectionEvidence(ExitSpecModel):
    case_id: str = Field(min_length=1)
    expected_tool: str = Field(min_length=1)
    actual_tool: str = Field(min_length=1)
    is_exact_match: bool


class ProportionMeasurement(ExitSpecModel):
    criterion_id: str = Field(min_length=1)
    sample_count: int = Field(ge=0)
    success_count: int = Field(ge=0)
    evidence_refs: List[str] = Field(default_factory=list)
    external_blocked_reason: Optional[str] = None
    internal_error: Optional[str] = None
    metadata_complete: bool = True
    workload_hash_matches: bool = True
    artifact_integrity_valid: bool = True

    @model_validator(mode="after")
    def validate_counts(self) -> "ProportionMeasurement":
        if self.success_count > self.sample_count:
            raise ValueError("success_count cannot exceed sample_count.")
        return self


class EvidenceArtifact(ExitSpecModel):
    artifact_id: str = Field(min_length=1)
    criterion_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    artifact_type: str = Field(min_length=1)
    storage_path: str = Field(min_length=1)
    media_type: str = Field(min_length=1)
    sha256: str = Field(pattern=SHA256_PATTERN)
    created_at: datetime
    redaction_state: str = Field(min_length=1)
    producer_adapter: str = Field(min_length=1)
    provenance: dict = Field(default_factory=dict)


class CriterionVerdict(ExitSpecModel):
    criterion_id: str = Field(min_length=1)
    verdict: VerdictStatus
    observed_rate: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    threshold: float = Field(ge=0.0, le=1.0)
    sample_count: int = Field(ge=0)
    confidence_lower_bound: Optional[float] = Field(
        default=None, ge=0.0, le=1.0
    )
    evidence_refs: List[str] = Field(default_factory=list)
    calculation_version: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    limitations: List[str] = Field(default_factory=list)


class OverallVerdict(ExitSpecModel):
    verdict: VerdictStatus
    must_have_criterion_ids: List[str] = Field(default_factory=list)
    reason: str = Field(min_length=1)


class RunManifest(ExitSpecModel):
    run_id: str = Field(min_length=1)
    contract_id: str = Field(min_length=1)
    contract_version: str = Field(min_length=1)
    contract_hash: str = Field(pattern=SHA256_PATTERN)
    fixture_hash: str = Field(pattern=SHA256_PATTERN)
    started_at: datetime
    ended_at: datetime
    provider: str = Field(min_length=1)
    endpoint_class: str = Field(min_length=1)
    model: str = Field(min_length=1)
    region: str = Field(min_length=1)
    runtime_configuration: dict = Field(default_factory=dict)
    traffic_shape: str = Field(min_length=1)
    warm_state: str = Field(min_length=1)
    adapter_versions: dict = Field(default_factory=dict)
    retry_policy: str = Field(min_length=1)
    redaction_policy: str = Field(min_length=1)
    environment_metadata: dict = Field(default_factory=dict)
    status: RunStatus
