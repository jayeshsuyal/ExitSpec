from pathlib import Path

from exitspec.web import DemoSession, SYNTHETIC_SUPPORT_AGENT_POC_ID


def _workspace_state(session: DemoSession) -> dict:
    return session.state_payload()["workspace"]


def _current_poc(session: DemoSession) -> dict:
    workspace = _workspace_state(session)
    assert len(workspace["pocs"]) == 2
    return next(
        poc
        for poc in workspace["pocs"]
        if poc["poc_id"] == SYNTHETIC_SUPPORT_AGENT_POC_ID
    )


def test_seeded_demo_projects_the_support_agent_poc_without_losing_identity(
    tmp_path: Path,
):
    session = DemoSession.synthetic_support_agent(output_root=tmp_path / "runs")

    poc = _current_poc(session)

    assert poc["poc_id"] == "poc_support_agent_demo"
    assert poc["source_summary"] == {
        "count": 1,
        "types": ["meeting_transcript"],
        "label": "1 source · Meeting transcript",
    }
    assert poc["derived_phase"] == "DEFINE"
    assert poc["next_action_code"] == "REVIEW_PROPOSALS"
    assert poc["latest_evidence_summary"]["status"] == "NOT_RUN"
    assert poc["active_contract_id"] is None
    assert poc["blockers"] == []
    assert set(_workspace_state(session)["available_filters"]) == {
        "Active",
        "Needs attention",
        "Completed",
    }


def test_guided_email_changes_source_summary_not_poc_identity(tmp_path: Path):
    session = DemoSession.synthetic_support_agent(output_root=tmp_path / "runs")
    before = _current_poc(session)

    session.import_guided_source_fixture("thread-root")
    after = _current_poc(session)

    assert before["poc_id"] == after["poc_id"]
    assert after["source_summary"] == {
        "count": 1,
        "types": ["email"],
        "label": "1 source · Email",
    }
    assert after["next_action_code"] == "REVIEW_PROPOSALS"
    assert after["blockers"] == []


def test_existing_review_confirmation_freeze_and_prove_drive_projection(
    tmp_path: Path,
):
    session = DemoSession.synthetic_support_agent(output_root=tmp_path / "runs")
    first, second = session.reviewed_drafts

    session.review(
        first.id,
        "APPROVE",
        "field_engineer",
        "Customer confirmed the measurable requirement.",
    )
    session.review(
        second.id,
        "REJECT",
        "field_engineer",
        "No executable latency adapter was agreed.",
    )
    approved = _current_poc(session)
    assert approved["next_action_code"] == "CREATE_CUSTOMER_REVIEW"
    assert approved["active_contract_version"] == "0.1.0"

    session.create_customer_draft()
    waiting = _current_poc(session)
    assert waiting["next_action_code"] == "WAIT_FOR_CUSTOMER"
    assert waiting["attention_required"] is False

    token = session.customer_review_token
    assert token is not None
    session.record_customer_decision(
        token,
        decision="CONFIRM",
        confirmer="customer_approver",
        agreement_acknowledged=True,
        rationale="The exact POC agreement is confirmed.",
        idempotency_key="workspace-projection-confirm-v1",
    )
    confirmed = _current_poc(session)
    assert confirmed["next_action_code"] == "FREEZE_CONFIRMED_CONTRACT"
    assert confirmed["blockers"] == []

    session.freeze()
    frozen = _current_poc(session)
    assert frozen["derived_phase"] == "PROVE"
    assert frozen["next_action_code"] == "RUN_POC"

    session.prove("pass")
    decided = _current_poc(session)
    assert decided["derived_phase"] == "DECIDE"
    assert decided["next_action_code"] == "REVIEW_EVIDENCE"
    assert decided["latest_evidence_summary"]["status"] == "PASS"
    assert decided["latest_evidence_summary"]["report_url"].startswith(
        "/artifacts/"
    )
    assert decided["blockers"] == []


def test_request_changes_projects_revision_without_false_confirmation(
    tmp_path: Path,
):
    session = DemoSession.synthetic_support_agent(output_root=tmp_path / "runs")
    first, second = session.reviewed_drafts
    session.review(
        first.id,
        "APPROVE",
        "field_engineer",
        "Measurable rule accepted.",
    )
    session.review(
        second.id,
        "REJECT",
        "field_engineer",
        "Context only.",
    )
    session.create_customer_draft()
    token = session.customer_review_token
    assert token is not None
    session.record_customer_decision(
        token,
        decision="REQUEST_CHANGES",
        confirmer="customer_approver",
        agreement_acknowledged=False,
        rationale="Clarify the workload.",
        idempotency_key="workspace-projection-change-v1",
    )

    projected = _current_poc(session)

    assert projected["derived_phase"] == "DEFINE"
    assert projected["next_action_code"] == "START_REVISION"
    assert projected["latest_evidence_summary"]["status"] == "NOT_RUN"
    assert projected["blockers"] == []
