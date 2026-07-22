from pathlib import Path

import pytest

from exitspec.models import DraftStatus, VerdictStatus
from exitspec.web import DemoSession, DemoStateError


def _session(tmp_path: Path) -> DemoSession:
    return DemoSession.synthetic_support_agent(output_root=tmp_path / "runs")


def test_demo_starts_with_visible_unreviewed_customer_claims(tmp_path):
    session = _session(tmp_path)
    state = session.state_payload()

    assert state["mode"] == "local_synthetic_demo"
    assert not state["ready_to_prove"]
    assert [draft["status"] for draft in state["drafts"]] == [
        DraftStatus.NEEDS_REVIEW.value,
        DraftStatus.NEEDS_REVIEW.value,
    ]
    assert state["safety"]["provider_calls"] is False


def test_demo_refuses_to_prove_while_a_claim_is_ambiguous(tmp_path):
    session = _session(tmp_path)

    with pytest.raises(DemoStateError, match="Resolve every candidate"):
        session.prove("pass")


def test_human_review_closes_the_contract_then_proves_a_pass(tmp_path):
    session = _session(tmp_path)
    first, second = session.reviewed_drafts

    approved = session.review(
        first.id,
        "APPROVE",
        reviewer="field_engineer",
        rationale="The customer confirmed the 95 percent accuracy target.",
    )
    rejected = session.review(
        second.id,
        "REJECT",
        reviewer="field_engineer",
        rationale="No measurable rule was agreed for this request yet.",
    )

    assert approved.status == DraftStatus.APPROVED
    assert rejected.status == DraftStatus.REJECTED
    assert session.state_payload()["ready_to_prove"]

    result = session.prove("pass")
    proof_pack = session.state_payload()["proof_pack"]

    assert result.overall_verdict.verdict == VerdictStatus.PASS
    assert proof_pack is not None
    assert proof_pack["overall_verdict"] == VerdictStatus.PASS.value
    assert "not an automatic ship" in proof_pack["next_human_action"]
    assert (result.output_dir / "decision-packet.html").exists()


def test_customer_draft_requires_closed_review_and_stays_pre_freeze(tmp_path):
    session = _session(tmp_path)
    with pytest.raises(DemoStateError, match="Resolve every visible candidate"):
        session.create_customer_draft()

    first, second = session.reviewed_drafts
    session.review(first.id, "APPROVE", "field_engineer", "Confirmed exact target.")
    session.review(second.id, "REJECT", "field_engineer", "Measurement is undefined.")
    draft_path = session.create_customer_draft()

    html = draft_path.read_text("utf-8")
    assert "This version is not frozen yet." in html
    assert session.state_payload()["customer_draft_url"] is not None


def test_blocked_scenario_produces_a_blocked_proof_pack(tmp_path):
    session = _session(tmp_path)
    first, second = session.reviewed_drafts
    session.review(first.id, "APPROVE", "field_engineer", "Confirmed exact target.")
    session.review(second.id, "REJECT", "field_engineer", "Measurement is undefined.")

    result = session.prove("blocked")
    proof_pack = session.state_payload()["proof_pack"]

    assert result.overall_verdict.verdict == VerdictStatus.BLOCKED
    assert proof_pack is not None
    assert proof_pack["next_human_action"].startswith("Resolve the stated external blocker")


def test_pasted_meeting_notes_become_unresolved_source_candidates(tmp_path):
    session = _session(tmp_path)

    session.intake(
        "Customer: The POC must reach 95% tool-selection accuracy.\n"
        "Field Engineer: We will confirm the test set and confidence rule."
    )
    state = session.state_payload()

    assert state["transcript"]["id"] == "pasted-transcript"
    assert state["drafts"][0]["status"] == DraftStatus.NEEDS_REVIEW.value
    assert state["drafts"][0]["proposed_criterion"] is None
    assert state["drafts"][0]["open_questions"]
    assert not state["ready_to_prove"]


def test_pasted_notes_reject_malformed_source_text(tmp_path):
    session = _session(tmp_path)

    with pytest.raises(DemoStateError, match="must use 'Speaker: message'"):
        session.intake("This is not attributed meeting text.")


def test_reset_restores_the_reproducible_synthetic_sample(tmp_path):
    session = _session(tmp_path)
    session.intake("Customer: The POC must reach 95% tool selection accuracy.")
    assert session.discovery_pack.transcript.id == "pasted-transcript"

    session.reset_to_synthetic_sample()

    assert session.discovery_pack.transcript.id == "support-discovery-v1"
    assert [draft.status for draft in session.reviewed_drafts] == [
        DraftStatus.NEEDS_REVIEW,
        DraftStatus.NEEDS_REVIEW,
    ]
