from datetime import datetime, timezone
from pathlib import Path

from exitspec.models import SourceReference
from exitspec.reporting import render_customer_draft, render_decision_packet
from exitspec.runner import run_demo


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = PROJECT_ROOT / "examples/support-agent/contracts/tool-selection-v1.yaml"
FIXTURE_PATH = PROJECT_ROOT / "examples/support-agent/fixtures/tool-selection-200.json"
FIXED_TIME = datetime(2026, 7, 22, 19, 0, tzinfo=timezone.utc)


def _run(tmp_path, scenario):
    return run_demo(
        contract_path=CONTRACT_PATH,
        fixture_path=FIXTURE_PATH,
        scenario=scenario,
        output_root=tmp_path,
        run_id="report-" + scenario,
        now=FIXED_TIME,
    )


def _proof_pack(result):
    return render_decision_packet(
        result.contract,
        result.manifest,
        result.contract.criteria[0],
        result.measurement,
        result.criterion_verdict,
        result.overall_verdict,
    )


def test_proof_pack_makes_the_contract_evidence_and_human_action_readable(tmp_path):
    result = _run(tmp_path, "pass")

    html = _proof_pack(result)

    for heading in (
        "Proof Pack",
        "Source quote",
        "Frozen contract",
        "Exact measurement",
        "Evidence sufficiency",
        "Limits of this proof",
        "Explicit next human action",
    ):
        assert heading in html
    assert result.contract.canonical_hash in html
    assert "At least 95%" in html
    assert "200 / 200 collected (minimum met)" in html
    assert "Review this Proof Pack with the customer" in html
    assert "does not authorize deployment, spending, procurement" in html


def test_not_proven_pack_tells_the_human_to_close_the_evidence_gap(tmp_path):
    result = _run(tmp_path, "insufficient")

    html = _proof_pack(result)

    assert "NOT_PROVEN" in html
    assert "100 / 200 collected (minimum not met)" in html
    assert "Close the evidence gaps, then re-run the frozen contract." in html
    assert "Do not treat this result as a pass." in html


def test_customer_draft_is_reviewable_and_does_not_claim_authority(approved_contract):
    html = render_customer_draft(approved_contract)

    assert "customer review draft" in html
    assert "Proposed POC acceptance criteria" in html
    assert "This version is not frozen yet." in html
    assert "Please confirm that the quoted requirement" in html
    assert "does not authorize deployment, spending, procurement" in html
    assert "At least 95%" in html


def test_reporting_escapes_customer_and_result_content(tmp_path):
    result = _run(tmp_path, "pass")
    criterion = result.contract.criteria[0].model_copy(
        update={
            "source": SourceReference(
                speaker="Customer <script>",
                quote="<script>alert('quote')</script>",
                location="call <line>",
            ),
            "normalized_claim": "<img src=x onerror=alert(1)>",
        }
    )
    overall = result.overall_verdict.model_copy(
        update={"reason": "<script>alert('reason')</script>"}
    )

    html = render_decision_packet(
        result.contract,
        result.manifest,
        criterion,
        result.measurement,
        result.criterion_verdict,
        overall,
    )

    assert "<script>alert" not in html
    assert "&lt;script&gt;alert(&#x27;quote&#x27;)&lt;/script&gt;" in html
    assert "&lt;img src=x onerror=alert(1)&gt;" in html
