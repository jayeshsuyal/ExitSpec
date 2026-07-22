import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from exitspec.authoring import (
    apply_review_plan,
    approve_draft,
    assemble_approved_contract,
    edit_draft,
    load_contract_seed,
    load_discovery_pack,
    load_review_plan,
    run_define_demo,
)
from exitspec.contracts import freeze_contract, verify_contract_digest
from exitspec.models import (
    ContractStatus,
    CriterionDraft,
    DraftStatus,
    ReviewDecision,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
AUTHORING_ROOT = PROJECT_ROOT / "examples/support-agent/authoring"
DISCOVERY_PATH = AUTHORING_ROOT / "discovery-pack-v1.json"
REVIEW_PLAN_PATH = AUTHORING_ROOT / "review-plan-v1.json"
CONTRACT_SEED_PATH = AUTHORING_ROOT / "contract-seed-v1.json"
FIXED_TIME = datetime(2026, 7, 22, 18, 0, tzinfo=timezone.utc)


def test_discovery_pack_rejects_a_quote_not_present_in_the_source():
    payload = json.loads(DISCOVERY_PATH.read_text("utf-8"))
    payload["drafts"][0]["source_span"]["quote"] = "Invented requirement."
    payload["drafts"][0]["proposed_criterion"]["source"][
        "quote"
    ] = "Invented requirement."

    with pytest.raises(ValidationError, match="must appear"):
        load_discovery_pack_payload(payload)


def load_discovery_pack_payload(payload):
    # Keep this small helper local so the test exercises Pydantic's source validation.
    from exitspec.models import DiscoveryPack

    return DiscoveryPack.model_validate(payload)


def test_draft_with_an_open_question_cannot_be_approved():
    draft = load_discovery_pack(DISCOVERY_PATH).drafts[1]

    with pytest.raises(ValueError, match="Resolve every open question"):
        approve_draft(
            draft,
            reviewer="customer_vp_engineering",
            rationale="Trying to approve an ambiguous claim.",
            reviewed_at=FIXED_TIME,
        )


def test_review_plan_preserves_explicit_approval_and_rejection():
    pack = load_discovery_pack(DISCOVERY_PATH)
    reviewed = apply_review_plan(
        pack.drafts, load_review_plan(REVIEW_PLAN_PATH), reviewed_at=FIXED_TIME
    )

    approved, rejected = reviewed
    assert approved.status == DraftStatus.APPROVED
    assert approved.proposed_criterion is not None
    assert approved.proposed_criterion.approved
    assert approved.review is not None
    assert approved.review.decision == ReviewDecision.APPROVE
    assert rejected.status == DraftStatus.REJECTED
    assert rejected.review is not None
    assert rejected.review.decision == ReviewDecision.REJECT


def test_only_approved_drafts_can_enter_the_contract_and_freeze():
    pack = load_discovery_pack(DISCOVERY_PATH)
    reviewed = apply_review_plan(
        pack.drafts, load_review_plan(REVIEW_PLAN_PATH), reviewed_at=FIXED_TIME
    )
    contract = assemble_approved_contract(
        load_contract_seed(CONTRACT_SEED_PATH), [reviewed[0]], approved_at=FIXED_TIME
    )

    assert contract.status == ContractStatus.APPROVED
    assert [criterion.id for criterion in contract.criteria] == ["TOOL-SELECT-01"]
    assert contract.criteria[0].source is not None
    assert contract.criteria[0].source.location == "support-discovery-v1:2"
    frozen = freeze_contract(contract, FIXED_TIME)
    assert verify_contract_digest(frozen)


def test_contract_assembly_rejects_a_nonapproved_candidate():
    pack = load_discovery_pack(DISCOVERY_PATH)

    with pytest.raises(ValueError, match="Only approved drafts"):
        assemble_approved_contract(
            load_contract_seed(CONTRACT_SEED_PATH), [pack.drafts[0]], FIXED_TIME
        )


def test_human_added_drafts_require_a_reason_and_preserve_the_marker():
    source_linked = load_discovery_pack(DISCOVERY_PATH).drafts[0]
    payload = source_linked.model_dump(mode="python")
    proposed = payload["proposed_criterion"]
    assert isinstance(proposed, dict)
    proposed["source"] = None
    proposed["human_added"] = True
    payload.update(
        {
            "source_span": None,
            "human_added": True,
            "human_added_rationale": None,
            "proposed_criterion": proposed,
        }
    )

    with pytest.raises(ValidationError, match="human_added_rationale"):
        CriterionDraft.model_validate(payload)

    payload["human_added_rationale"] = "The customer added this during contract review."
    human_added = CriterionDraft.model_validate(payload)
    assert human_added.human_added
    assert human_added.proposed_criterion is not None
    assert human_added.proposed_criterion.human_added


def test_edit_keeps_a_candidate_in_review_and_blocks_identity_edits():
    original = load_discovery_pack(DISCOVERY_PATH).drafts[0]
    edited = edit_draft(
        original,
        {"normalized_claim": "Edited claim wording without changing its source."},
    )

    assert edited.id == original.id
    assert edited.status == DraftStatus.NEEDS_REVIEW
    assert edited.normalized_claim == "Edited claim wording without changing its source."
    assert edited.proposed_criterion is not None
    assert (
        edited.proposed_criterion.normalized_claim
        == "Edited claim wording without changing its source."
    )
    with pytest.raises(ValueError, match="cannot change"):
        edit_draft(original, {"id": "DRAFT-OTHER-01"})


def test_define_demo_writes_an_inspectable_authoring_packet(tmp_path):
    result = run_define_demo(
        discovery_path=DISCOVERY_PATH,
        review_plan_path=REVIEW_PLAN_PATH,
        contract_seed_path=CONTRACT_SEED_PATH,
        output_root=tmp_path,
        session_id="define-demo",
        now=FIXED_TIME,
    )

    assert result.contract.status == ContractStatus.APPROVED
    assert [draft.status for draft in result.reviewed_drafts] == [
        DraftStatus.APPROVED,
        DraftStatus.REJECTED,
    ]
    for filename in (
        "discovery-pack.json",
        "reviewed-drafts.json",
        "approved-contract.json",
        "define-manifest.json",
        "define-review.html",
        "artifact-hashes.json",
    ):
        assert (result.output_dir / filename).exists()

    report = (result.output_dir / "define-review.html").read_text("utf-8")
    assert "Agree before you test." in report
    assert "status-approved" in report
    assert "status-rejected" in report
    hashes = json.loads((result.output_dir / "artifact-hashes.json").read_text("utf-8"))
    for relative_path, expected_hash in hashes["artifacts"].items():
        actual_hash = hashlib.sha256(
            (result.output_dir / relative_path).read_bytes()
        ).hexdigest()
        assert actual_hash == expected_hash


def test_define_demo_refuses_non_synthetic_transcripts_until_redaction_exists(
    tmp_path,
):
    payload = json.loads(DISCOVERY_PATH.read_text("utf-8"))
    payload["transcript"]["synthetic"] = False
    unsafe_discovery_path = tmp_path / "non-synthetic-discovery.json"
    unsafe_discovery_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="only persists synthetic transcripts"):
        run_define_demo(
            discovery_path=unsafe_discovery_path,
            review_plan_path=REVIEW_PLAN_PATH,
            contract_seed_path=CONTRACT_SEED_PATH,
            output_root=tmp_path,
            session_id="should-not-exist",
            now=FIXED_TIME,
        )
