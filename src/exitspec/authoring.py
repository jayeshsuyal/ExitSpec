"""Source-linked discovery review and contract assembly for the Define step."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Mapping, Optional, Sequence

from .contracts import transition_contract, utc_now
from .models import (
    ContractSeed,
    ContractStatus,
    CriterionDraft,
    CriterionReview,
    DiscoveryPack,
    DiscoveryTranscript,
    DraftStatus,
    POCContract,
    ReviewDecision,
    ReviewPlan,
)
from .reporting import render_define_review


def _load_json_mapping(path: Path) -> Mapping[str, object]:
    parsed = json.loads(path.read_text("utf-8"))
    if not isinstance(parsed, dict):
        raise ValueError("{0} must contain a JSON object at its root.".format(path))
    return parsed


def load_discovery_pack(path: Path) -> DiscoveryPack:
    """Load one transcript and its untrusted candidate criteria."""

    return DiscoveryPack.model_validate(_load_json_mapping(path))


def load_review_plan(path: Path) -> ReviewPlan:
    """Load explicit human decisions for a reproducible local demo."""

    return ReviewPlan.model_validate(_load_json_mapping(path))


def load_contract_seed(path: Path) -> ContractSeed:
    """Load the non-criterion metadata used to assemble a reviewed contract."""

    return ContractSeed.model_validate(_load_json_mapping(path))


def canonical_transcript_bytes(transcript: DiscoveryTranscript) -> bytes:
    """Serialize discovery input deterministically for a source-integrity reference."""

    payload = transcript.model_dump(mode="json")
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def transcript_digest(transcript: DiscoveryTranscript) -> str:
    return hashlib.sha256(canonical_transcript_bytes(transcript)).hexdigest()


def _validated_draft_copy(
    draft: CriterionDraft, updates: Mapping[str, object]
) -> CriterionDraft:
    payload = draft.model_dump(mode="python")
    payload.update(updates)
    return CriterionDraft.model_validate(payload)


def edit_draft(
    draft: CriterionDraft, updates: Mapping[str, object]
) -> CriterionDraft:
    """Return a reviewed candidate to edit without changing its identity or audit state."""

    if draft.status != DraftStatus.NEEDS_REVIEW:
        raise ValueError("Only drafts needing review can be edited; create a revision instead.")

    allowed_fields = {
        "source_span",
        "human_added",
        "human_added_rationale",
        "normalized_claim",
        "proposed_criterion",
        "open_questions",
    }
    unsupported = sorted(set(updates).difference(allowed_fields))
    if unsupported:
        raise ValueError(
            "Draft edits cannot change: {0}".format(", ".join(unsupported))
        )

    resolved_updates = dict(updates)
    if (
        "normalized_claim" in resolved_updates
        and "proposed_criterion" not in resolved_updates
        and draft.proposed_criterion is not None
        and isinstance(resolved_updates["normalized_claim"], str)
    ):
        resolved_updates["proposed_criterion"] = draft.proposed_criterion.model_copy(
            update={"normalized_claim": resolved_updates["normalized_claim"]}
        )
    return _validated_draft_copy(draft, resolved_updates)


def approve_draft(
    draft: CriterionDraft,
    reviewer: str,
    rationale: str,
    reviewed_at: Optional[datetime] = None,
) -> CriterionDraft:
    """Approve a complete, source-linked candidate criterion exactly once."""

    if draft.status != DraftStatus.NEEDS_REVIEW:
        raise ValueError("Only drafts needing review can be approved.")
    if draft.open_questions:
        raise ValueError("Resolve every open question before approving a criterion draft.")
    if draft.proposed_criterion is None:
        raise ValueError("A draft needs a complete proposed criterion before approval.")

    approved_criterion = draft.proposed_criterion.model_copy(
        update={"approved": True}
    )
    review = CriterionReview(
        reviewer=reviewer,
        decision=ReviewDecision.APPROVE,
        rationale=rationale,
        reviewed_at=reviewed_at or utc_now(),
    )
    return _validated_draft_copy(
        draft,
        {
            "status": DraftStatus.APPROVED,
            "proposed_criterion": approved_criterion,
            "review": review,
        },
    )


def reject_draft(
    draft: CriterionDraft,
    reviewer: str,
    rationale: str,
    reviewed_at: Optional[datetime] = None,
) -> CriterionDraft:
    """Record a rejection without pretending the candidate was never proposed."""

    if draft.status != DraftStatus.NEEDS_REVIEW:
        raise ValueError("Only drafts needing review can be rejected.")
    review = CriterionReview(
        reviewer=reviewer,
        decision=ReviewDecision.REJECT,
        rationale=rationale,
        reviewed_at=reviewed_at or utc_now(),
    )
    return _validated_draft_copy(
        draft,
        {"status": DraftStatus.REJECTED, "review": review},
    )


def apply_review_plan(
    drafts: Sequence[CriterionDraft],
    plan: ReviewPlan,
    reviewed_at: Optional[datetime] = None,
) -> List[CriterionDraft]:
    """Apply a deterministic set of human review decisions to a discovery pack."""

    by_id = {draft.id: draft for draft in drafts}
    reviewed = dict(by_id)
    timestamp = reviewed_at or utc_now()
    for action in plan.actions:
        if action.draft_id not in by_id:
            raise ValueError(
                "Review plan references unknown draft {0}.".format(action.draft_id)
            )
        draft = reviewed[action.draft_id]
        if action.decision == ReviewDecision.APPROVE:
            reviewed[action.draft_id] = approve_draft(
                draft, action.reviewer, action.rationale, timestamp
            )
        else:
            reviewed[action.draft_id] = reject_draft(
                draft, action.reviewer, action.rationale, timestamp
            )
    return [reviewed[draft.id] for draft in drafts]


def assemble_approved_contract(
    seed: ContractSeed,
    approved_drafts: Sequence[CriterionDraft],
    approved_at: Optional[datetime] = None,
) -> POCContract:
    """Create a contract only from explicitly approved, complete draft criteria."""

    if not approved_drafts:
        raise ValueError("At least one approved draft is required to assemble a contract.")

    unapproved = [
        draft.id for draft in approved_drafts if draft.status != DraftStatus.APPROVED
    ]
    if unapproved:
        raise ValueError(
            "Only approved drafts can enter a contract: {0}".format(
                ", ".join(unapproved)
            )
        )

    criteria = []
    for draft in approved_drafts:
        if draft.proposed_criterion is None:
            raise ValueError(
                "Approved draft {0} has no complete criterion.".format(draft.id)
            )
        criteria.append(draft.proposed_criterion)

    draft_contract = POCContract(
        id=seed.id,
        version=seed.version,
        status=ContractStatus.DRAFT,
        created_at=seed.created_at,
        customer=seed.customer,
        use_case=seed.use_case,
        target_system=seed.target_system,
        workload=seed.workload,
        criteria=criteria,
        owners=seed.owners,
        non_goals=seed.non_goals,
        evidence_retention_policy=seed.evidence_retention_policy,
    )
    timestamp = approved_at or utc_now()
    in_review = transition_contract(
        draft_contract, ContractStatus.IN_REVIEW, timestamp
    )
    return transition_contract(in_review, ContractStatus.APPROVED, timestamp)


def _write_json(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@dataclass(frozen=True)
class DefineResult:
    output_dir: Path
    discovery_pack: DiscoveryPack
    reviewed_drafts: List[CriterionDraft]
    contract: POCContract


def run_define_demo(
    discovery_path: Path,
    review_plan_path: Path,
    contract_seed_path: Path,
    output_root: Path,
    session_id: Optional[str] = None,
    now: Optional[datetime] = None,
) -> DefineResult:
    """Write a small, inspectable Define-stage review packet for the sample POC."""

    timestamp = now or utc_now()
    discovery_pack = load_discovery_pack(discovery_path)
    if not discovery_pack.transcript.synthetic:
        raise ValueError(
            "Brick 2 only persists synthetic transcripts until a declared redaction "
            "policy is implemented."
        )
    review_plan = load_review_plan(review_plan_path)
    contract_seed = load_contract_seed(contract_seed_path)
    reviewed_drafts = apply_review_plan(
        discovery_pack.drafts, review_plan, reviewed_at=timestamp
    )
    approved_drafts = [
        draft for draft in reviewed_drafts if draft.status == DraftStatus.APPROVED
    ]
    contract = assemble_approved_contract(
        contract_seed, approved_drafts, approved_at=timestamp
    )

    resolved_session_id = session_id or "define-{0}".format(
        timestamp.strftime("%Y%m%dT%H%M%SZ")
    )
    output_dir = output_root / resolved_session_id
    output_dir.mkdir(parents=True, exist_ok=False)

    discovery_output_path = output_dir / "discovery-pack.json"
    reviews_output_path = output_dir / "reviewed-drafts.json"
    contract_output_path = output_dir / "approved-contract.json"
    manifest_output_path = output_dir / "define-manifest.json"
    report_output_path = output_dir / "define-review.html"

    _write_json(discovery_output_path, discovery_pack.model_dump(mode="json"))
    _write_json(
        reviews_output_path,
        {
            "review_plan_id": review_plan.id,
            "drafts": [draft.model_dump(mode="json") for draft in reviewed_drafts],
        },
    )
    _write_json(contract_output_path, contract.model_dump(mode="json"))
    _write_json(
        manifest_output_path,
        {
            "session_id": resolved_session_id,
            "status": "COMPLETED",
            "started_at": timestamp.isoformat(),
            "completed_at": timestamp.isoformat(),
            "transcript_id": discovery_pack.transcript.id,
            "transcript_sha256": transcript_digest(discovery_pack.transcript),
            "review_plan_id": review_plan.id,
            "approved_draft_ids": [draft.id for draft in approved_drafts],
            "rejected_draft_ids": [
                draft.id
                for draft in reviewed_drafts
                if draft.status == DraftStatus.REJECTED
            ],
            "contract_id": contract.id,
            "contract_version": contract.version,
            "contract_status": contract.status.value,
        },
    )
    report_output_path.write_text(
        render_define_review(discovery_pack, reviewed_drafts, contract),
        encoding="utf-8",
    )

    artifact_paths = [
        discovery_output_path,
        reviews_output_path,
        contract_output_path,
        manifest_output_path,
        report_output_path,
    ]
    _write_json(
        output_dir / "artifact-hashes.json",
        {
            "algorithm": "sha256",
            "artifacts": {
                str(path.relative_to(output_dir)): _sha256_file(path)
                for path in sorted(artifact_paths)
            },
        },
    )

    return DefineResult(
        output_dir=output_dir,
        discovery_pack=discovery_pack,
        reviewed_drafts=reviewed_drafts,
        contract=contract,
    )
