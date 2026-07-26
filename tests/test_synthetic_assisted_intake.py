import json
import threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from exitspec.assisted_authoring import (
    AssistedAuthoringError,
    build_assisted_discovery_pack,
)
from exitspec.providers import ProviderError, ProviderErrorCode
from exitspec.synthetic_assisted_authoring import (
    SYNTHETIC_ASSISTED_MODEL,
    SYNTHETIC_ASSISTED_POLICY,
    SyntheticAssistedAuthoringExecutor,
)
from exitspec.models import DraftStatus
from exitspec.web import DemoSession, ExitSpecDemoServer


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WAVE1_FIXTURE_PATH = (
    PROJECT_ROOT
    / "examples/support-agent/fireworks/assisted-authoring-cases-v1.json"
)
RAW_SECRET = "sk_live_assisted_1234567890"


def _session(tmp_path: Path) -> DemoSession:
    return DemoSession.synthetic_support_agent(output_root=tmp_path / "runs")


def _post_json(url: str, payload: dict) -> dict:
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def test_assisted_intake_happy_path_is_synthetic_and_review_only(tmp_path):
    session = _session(tmp_path)

    session.assisted_intake(
        "Customer: The agent must reach 95% exact tool-selection accuracy "
        "across 200 approved cases.\n"
        "Field Engineer: We will review the proposed acceptance rule."
    )

    state = session.state_payload()
    assert state["authoring"]["mode"] == "synthetic_assisted"
    assert state["authoring"]["provider_calls"] is False
    assert state["authoring"]["adapter"] == "synthetic_assisted_authoring"
    assert state["authoring"]["receipt"]["provider"] == "synthetic"
    assert state["authoring"]["receipt"]["provider_request_id"] is None
    assert state["transcript_redaction"]["decision"] == "ALLOW_REDACTED_ONLY"
    assert state["drafts"]
    assert all(
        draft["status"] == DraftStatus.NEEDS_REVIEW.value
        for draft in state["drafts"]
    )
    assert state["drafts"][0]["proposed_criterion"] is not None
    assert state["approved_criterion_count"] == 0
    assert state["contract"] is None
    assert state["ready_to_prove"] is False


def test_assisted_intake_keeps_vague_proposal_unresolved(tmp_path):
    session = _session(tmp_path)

    session.assisted_intake(
        "Customer: We want the experience to be much better and fast."
    )

    draft = session.pending_drafts[0]
    assert draft.status == DraftStatus.NEEDS_REVIEW
    assert draft.proposed_criterion is None
    assert draft.open_questions
    assert session.state_payload()["ready_to_prepare_customer_review"] is False


def test_assisted_intake_redacts_secrets_from_state_and_receipt(tmp_path):
    session = _session(tmp_path)

    session.assisted_intake(
        "Customer: Project Phoenix needs 95% exact tool-selection accuracy "
        "across 200 approved cases. Contact owner@example.com and use "
        f"{RAW_SECRET}.",
        customer_terms=("Project Phoenix",),
    )

    serialized_state = json.dumps(session.state_payload(), sort_keys=True)
    assert RAW_SECRET not in serialized_state
    assert "Project Phoenix" not in serialized_state
    assert "owner@example.com" not in serialized_state
    assert "[REDACTED" in serialized_state


def test_synthetic_assist_matches_every_frozen_wave1_case():
    fixture = json.loads(WAVE1_FIXTURE_PATH.read_text(encoding="utf-8"))

    for case in fixture["cases"]:
        authored = build_assisted_discovery_pack(
            case["transcript"],
            executor=SyntheticAssistedAuthoringExecutor(),
            model=SYNTHETIC_ASSISTED_MODEL,
            policy=SYNTHETIC_ASSISTED_POLICY,
            customer_terms=case["customer_terms"],
            transcript_id=case["id"],
            title=case["title"],
        )
        drafts = authored.discovery_pack.drafts

        assert len(drafts) == len(case["expected_proposals"]), case["id"]
        assert authored.receipt.provider == "synthetic"
        for draft, expected in zip(drafts, case["expected_proposals"]):
            assert draft.status == DraftStatus.NEEDS_REVIEW, case["id"]
            assert draft.source_span.start_line == expected["line_number"], case["id"]
            assert draft.source_span.end_line == expected["line_number"], case["id"]
            assert draft.source_span.speaker == expected["speaker"], case["id"]
            assert draft.source_span.quote == expected["quote"], case["id"]
            if expected["classification"] == "measurable":
                criterion = draft.proposed_criterion
                assert criterion is not None, case["id"]
                assert criterion.approved is False, case["id"]
                assert criterion.rule.threshold == expected["threshold"], case["id"]
                assert (
                    criterion.rule.minimum_samples
                    == expected["minimum_samples"]
                ), case["id"]
                assert draft.open_questions == [], case["id"]
            else:
                assert draft.proposed_criterion is None, case["id"]
                assert draft.open_questions, case["id"]


class _FailingExecutor:
    def execute(self, request):
        raise ProviderError(
            ProviderErrorCode.SERVICE_ERROR,
            "upstream failure containing " + RAW_SECRET,
        )


def test_assisted_authoring_provider_failure_is_sanitized():
    raw_transcript = f"Customer: Please use {RAW_SECRET} for the POC."

    with pytest.raises(
        AssistedAuthoringError,
        match="Provider-assisted discovery could not be completed",
    ) as error:
        build_assisted_discovery_pack(
            raw_transcript,
            executor=_FailingExecutor(),
            model="synthetic-test-model",
            policy=SYNTHETIC_ASSISTED_POLICY,
        )

    assert RAW_SECRET not in str(error.value)
    assert RAW_SECRET not in repr(error.value)


def test_assisted_intake_endpoint_replaces_downstream_state(tmp_path):
    session = _session(tmp_path)
    first, second = session.reviewed_drafts
    session.review(first.id, "APPROVE", "reviewer", "The rule is complete.")
    session.review(second.id, "REJECT", "reviewer", "Keep this as context.")
    session.create_customer_draft()
    assert session.customer_review_token is not None

    server = ExitSpecDemoServer(("127.0.0.1", 0), session)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        base_url = "http://127.0.0.1:{0}".format(server.server_port)
        response = _post_json(
            base_url + "/api/assisted-intake",
            {
                "title": "Customer assisted draft",
                "transcript": (
                    "Customer: The target is 95% exact tool selection across "
                    "200 approved cases."
                ),
            },
        )
    finally:
        server.shutdown()
        worker.join(timeout=5)
        server.server_close()

    state = response["state"]
    assert response["notice"].startswith("Synthetic assisted authoring")
    assert state["authoring"]["mode"] == "synthetic_assisted"
    assert state["customer_review_url"] is None
    assert state["confirmation"] is None
    assert state["contract"] is None
    assert state["ready_to_prove"] is False
    assert all(
        draft["status"] == DraftStatus.NEEDS_REVIEW.value
        for draft in state["drafts"]
    )


def test_assisted_intake_endpoint_reports_safe_provider_error(tmp_path, monkeypatch):
    """The HTTP boundary must not expose provider-controlled exception content."""

    session = _session(tmp_path)

    def fail(*args, **kwargs):
        raise ValueError("provider payload leaked " + RAW_SECRET)

    monkeypatch.setattr("exitspec.web.build_assisted_discovery_pack", fail)
    server = ExitSpecDemoServer(("127.0.0.1", 0), session)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        base_url = "http://127.0.0.1:{0}".format(server.server_port)
        request = Request(
            base_url + "/api/assisted-intake",
            data=json.dumps({"transcript": f"Customer: {RAW_SECRET}"}).encode(
                "utf-8"
            ),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with pytest.raises(HTTPError) as error:
            urlopen(request, timeout=5)
        payload = json.loads(error.value.read().decode("utf-8"))
    finally:
        server.shutdown()
        worker.join(timeout=5)
        server.server_close()

    assert error.value.code == 409
    assert RAW_SECRET not in payload["error"]
