import json
import threading
from contextlib import contextmanager
from http.client import HTTPConnection
from pathlib import Path
from urllib.parse import quote

from exitspec.poc_creation import (
    DraftPOCCreateRequest,
    ProcessLocalDraftPOCService,
)
from exitspec.poc_proposal_review import (
    ProcessLocalProposalReviewService,
    ProposalDecision,
)
from exitspec.poc_source_intake import ProcessLocalPOCSourceIntake
from exitspec.web import (
    DemoSession,
    ExitSpecDemoServer,
    SYNTHETIC_SUPPORT_AGENT_POC_ID,
)


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


@contextmanager
def _running_workspace_server(tmp_path: Path):
    session = DemoSession.synthetic_support_agent(output_root=tmp_path / "runs")
    server = ExitSpecDemoServer(("127.0.0.1", 0), session)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield server
    finally:
        server.shutdown()
        worker.join(timeout=5)
        server.server_close()


def _workspace_request(
    server: ExitSpecDemoServer,
    selected_filter: str = "Active",
) -> dict:
    connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    try:
        connection.request(
            "GET",
            "/api/workspace?filter={0}".format(quote(selected_filter)),
            headers={"Accept": "application/json"},
        )
        response = connection.getresponse()
        assert response.status == 200
        return json.loads(response.read().decode("utf-8"))
    finally:
        connection.close()


def _create_local_workspace_draft(
    service: ProcessLocalDraftPOCService,
    *,
    key: str = "workspace-local-draft",
):
    return service.create(
        DraftPOCCreateRequest(
            display_name="Northstar latency POC",
            customer_label="Northstar",
            use_case="Validate one bounded inference latency requirement.",
            owner="field_engineer",
            first_source_choice="DOCUMENT",
        ),
        idempotency_key=key,
    ).draft


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


def test_workspace_get_merges_new_draft_first_and_preserves_seeded_rows(
    tmp_path: Path,
):
    with _running_workspace_server(tmp_path) as server:
        draft = _create_local_workspace_draft(server.draft_poc_service)

        active = _workspace_request(server)
        attention = _workspace_request(server, "Needs attention")
        completed = _workspace_request(server, "Completed")

    assert active["continue_working"]["poc_id"] == draft.poc_id
    assert active["pocs"][0]["poc_id"] == draft.poc_id
    assert active["pocs"][0]["source_summary"]["count"] == 0
    assert active["pocs"][0]["next_action_code"] == "ADD_SOURCE"
    assert {
        poc["poc_id"] for poc in active["pocs"][1:]
    } == {
        "poc_support_agent_demo",
        "poc_inference_latency_demo",
    }
    assert draft.poc_id in {
        poc["poc_id"] for poc in attention["pocs"]
    }
    assert draft.poc_id not in {
        poc["poc_id"] for poc in completed["pocs"]
    }


def test_workspace_get_updates_source_count_and_action_after_capture(
    tmp_path: Path,
):
    with _running_workspace_server(tmp_path) as server:
        draft = _create_local_workspace_draft(server.draft_poc_service)
        before = _workspace_request(server)

        receipt = server.poc_source_intake.capture_document(
            poc_id=draft.poc_id,
            document_text="The p95 latency must stay below 500 ms.",
            idempotency_key="workspace-source-capture",
        )
        after = _workspace_request(server)

    projected_before = next(
        poc for poc in before["pocs"] if poc["poc_id"] == draft.poc_id
    )
    projected_after = next(
        poc for poc in after["pocs"] if poc["poc_id"] == draft.poc_id
    )
    assert projected_before["next_action_code"] == "ADD_SOURCE"
    assert projected_after["source_summary"]["count"] == 1
    assert projected_after["source_summary"]["types"] == ["document"]
    assert projected_after["next_action_code"] == "REVIEW_PROPOSALS"
    assert projected_after["source_summary"]["count"] == 1
    assert receipt.proposal_count == 1


def test_workspace_advances_after_all_source_proposals_are_triaged(
    tmp_path: Path,
):
    with _running_workspace_server(tmp_path) as server:
        draft = _create_local_workspace_draft(server.draft_poc_service)
        server.poc_source_intake.capture_document(
            poc_id=draft.poc_id,
            document_text=(
                "The p95 latency must stay below 500 ms. "
                "Error rate must remain below 1%."
            ),
            idempotency_key="workspace-review-aware-source",
        )
        proposals = server.proposal_review_service.list_proposals(draft.poc_id)
        before = _workspace_request(server)
        for index, proposal in enumerate(proposals, start=1):
            server.proposal_review_service.decide(
                draft.poc_id,
                proposal.proposal_id,
                (
                    ProposalDecision.KEEP_FOR_CONTRACT
                    if index == 1
                    else ProposalDecision.DISCARD
                ),
                "Jayesh",
                "Human triage decision for dashboard continuity.",
                f"workspace-review-aware-{index}",
            )
        after = _workspace_request(server)

    projected_before = next(
        poc for poc in before["pocs"] if poc["poc_id"] == draft.poc_id
    )
    projected_after = next(
        poc for poc in after["pocs"] if poc["poc_id"] == draft.poc_id
    )
    assert projected_before["next_action_code"] == "REVIEW_PROPOSALS"
    assert projected_after["next_action_code"] == "DEFINE_CRITERIA"
    assert projected_after["next_human_action"] == (
        "Define acceptance criteria for 1 kept proposal."
    )
    assert projected_after["active_contract_id"] is None


def test_workspace_get_is_read_only_for_seeded_drafts_and_sources(
    tmp_path: Path,
):
    with _running_workspace_server(tmp_path) as server:
        draft = _create_local_workspace_draft(server.draft_poc_service)
        server.poc_source_intake.capture_document(
            poc_id=draft.poc_id,
            document_text="Error rate must remain below 1%.",
            idempotency_key="workspace-read-only-source",
        )
        seeded_before = server.session.state_payload()
        drafts_before = server.draft_poc_service.snapshots()
        receipts_before = server.poc_source_intake.list_receipts(draft.poc_id)

        _workspace_request(server)
        _workspace_request(server, "Needs attention")
        _workspace_request(server, "Completed")

        assert server.session.state_payload() == seeded_before
        assert server.draft_poc_service.snapshots() == drafts_before
        assert (
            server.poc_source_intake.list_receipts(draft.poc_id)
            == receipts_before
        )


def test_workspace_get_projects_receipt_failure_as_safe_blocker(
    tmp_path: Path,
    monkeypatch,
):
    with _running_workspace_server(tmp_path) as server:
        draft = _create_local_workspace_draft(server.draft_poc_service)

        def refuse_receipts(
            self: ProcessLocalPOCSourceIntake,
            poc_id: str,
        ):
            raise RuntimeError("sensitive adapter failure")

        monkeypatch.setattr(
            ProcessLocalPOCSourceIntake,
            "list_receipts",
            refuse_receipts,
        )
        workspace = _workspace_request(server)

    projected = next(
        poc for poc in workspace["pocs"] if poc["poc_id"] == draft.poc_id
    )
    assert projected["next_action_code"] == "RESOLVE_BLOCKER"
    assert projected["source_summary"] == {
        "count": 0,
        "types": [],
        "label": "Source status unavailable",
    }
    assert projected["blockers"] == [
        {
            "code": "draft_source_summary_unavailable",
            "message": "Source status is unavailable. Reload before continuing.",
        }
    ]
    assert "sensitive" not in json.dumps(projected).lower()


def test_workspace_get_projects_review_failure_as_safe_blocker(
    tmp_path: Path,
    monkeypatch,
):
    with _running_workspace_server(tmp_path) as server:
        draft = _create_local_workspace_draft(server.draft_poc_service)
        server.poc_source_intake.capture_document(
            poc_id=draft.poc_id,
            document_text="Error rate must remain below 1%.",
            idempotency_key="workspace-review-failure-source",
        )

        def refuse_review(
            self: ProcessLocalProposalReviewService,
            poc_id: str,
        ):
            raise RuntimeError("sensitive review failure")

        monkeypatch.setattr(
            ProcessLocalProposalReviewService,
            "list_proposals",
            refuse_review,
        )
        workspace = _workspace_request(server)

    projected = next(
        poc for poc in workspace["pocs"] if poc["poc_id"] == draft.poc_id
    )
    assert projected["next_action_code"] == "RESOLVE_BLOCKER"
    assert projected["source_summary"]["count"] == 1
    assert projected["blockers"] == [
        {
            "code": "draft_proposal_review_unavailable",
            "message": (
                "Proposal review status is unavailable. Reload before continuing."
            ),
        }
    ]
    assert "sensitive" not in json.dumps(projected).lower()
