from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

import pytest

from exitspec.contracts import freeze_contract
from exitspec.models import ContractStatus, SourceReference, VerdictStatus
from exitspec.reporting import render_customer_draft, render_decision_packet
from exitspec.runner import run_demo
from exitspec.verdicts import (
    aggregate_overall_verdict,
    evaluate_proportion_criterion,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = (
    PROJECT_ROOT / "examples/support-agent/contracts/tool-selection-v1.frozen.yaml"
)
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


def _packet_inputs(result):
    return {
        "contract": result.contract,
        "manifest": result.manifest,
        "criterion": result.contract.criteria[0],
        "measurement": result.measurement,
        "criterion_verdict": result.criterion_verdict,
        "overall": result.overall_verdict,
    }


def _acceptance_evidence_pack(result):
    return render_decision_packet(**_packet_inputs(result))


class _PackStructureParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.details = []
        self.links = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "details":
            self.details.append(attributes)
        if tag == "a":
            self.links.append(attributes)


def test_acceptance_evidence_pack_makes_the_decision_readable_at_a_glance(tmp_path):
    result = _run(tmp_path, "pass")

    html = _acceptance_evidence_pack(result)

    for heading in (
        "ExitSpec",
        "POC acceptance evidence",
        "Evidence verdict",
        "Why this verdict",
        "Exact evidence equation",
        "Concise limitation",
        "Exact next human action",
        "Source quote",
        "Frozen contract",
        "Exact measurement",
        "Evidence sufficiency",
    ):
        assert heading in html
    assert result.contract.canonical_hash in html
    assert "At least 95%" in html
    assert (
        "Required ≥ 95.00%<span class=\"operator\">·</span>"
        "Observed 197/200 (98.50%)<span class=\"operator\">·</span>"
        "Wilson lower bound 95.68%<span class=\"operator\">·</span>PASS"
    ) in html
    assert "200 / 200 collected (minimum met)" in html
    assert "This establishes the approved fixture criterion" in html
    assert "Review this POC Acceptance Evidence Pack with the customer" in html
    assert "Evidence is not authorization." in html
    assert "does not authorize deployment, spending, procurement" in html
    assert "<h1>Proof Pack:" not in html
    assert 'data-legacy-artifact-name="Proof Pack"' in html
    assert 'class="proof-sheet status-panel-PASS"' in html
    assert "gradient(" not in html
    assert "background: #0e141b" in html
    assert "background: #18222d" in html


def test_pack_links_every_evidence_artifact_with_relative_urls(tmp_path):
    html = _acceptance_evidence_pack(_run(tmp_path, "pass"))
    parser = _PackStructureParser()
    parser.feed(html)

    assert [link["href"] for link in parser.links] == [
        "contract.json",
        "evidence-artifacts.json",
        "calculations.json",
        "verdicts.json",
        "run-manifest.json",
        "artifact-hashes.json",
    ]
    assert all(
        "://" not in link["href"] and not link["href"].startswith("/")
        for link in parser.links
    )
    assert "<script" not in html
    assert "https://" not in html
    assert "http://" not in html


def test_pack_keeps_seven_audit_sections_collapsed_by_default(tmp_path):
    html = _acceptance_evidence_pack(_run(tmp_path, "pass"))
    parser = _PackStructureParser()
    parser.feed(html)

    assert len(parser.details) == 7
    assert all("open" not in details for details in parser.details)
    for number, title in enumerate(
        (
            "What the customer asked us to prove",
            "The frozen agreement",
            "The exact test",
            "Is the evidence sufficient?",
            "What the test observed",
            "What this does not prove",
            "Human follow-up",
        ),
        start=1,
    ):
        assert '<span class="row-number">{0:02d}</span>'.format(number) in html
        assert title in html


def test_not_proven_pack_tells_the_human_to_close_the_evidence_gap(tmp_path):
    result = _run(tmp_path, "insufficient")

    html = _acceptance_evidence_pack(result)

    assert "NOT_PROVEN" in html
    assert "100 / 200 collected (minimum not met)" in html
    assert "Close the evidence gaps, then re-run the frozen contract." in html
    assert "Do not treat this result as a pass." in html
    assert (
        "Required ≥ 95.00%<span class=\"operator\">·</span>"
        "Observed 100/100 (—)<span class=\"operator\">·</span>"
        "Wilson lower bound —<span class=\"operator\">·</span>NOT_PROVEN"
    ) in html
    assert 'class="proof-sheet status-panel-NOT_PROVEN"' in html


@pytest.mark.parametrize(
    ("scenario", "verdict", "equation_result"),
    [
        ("pass", "PASS", "Observed 197/200 (98.50%)"),
        ("fail", "FAIL", "Observed 189/200 (94.50%)"),
        ("insufficient", "NOT_PROVEN", "Observed 100/100 (—)"),
        ("blocked", "BLOCKED", "Observed 0/0 (—)"),
    ],
)
def test_verdict_variants_have_distinct_hooks_and_honest_equations(
    tmp_path, scenario, verdict, equation_result
):
    html = _acceptance_evidence_pack(_run(tmp_path, scenario))

    assert 'class="proof-sheet status-panel-{0}"'.format(verdict) in html
    assert "<h1 id=\"evidence-verdict\">{0}</h1>".format(verdict) in html
    assert equation_result in html
    assert "Wilson lower bound" in html
    assert "Evidence is not authorization." in html


def test_customer_draft_is_reviewable_and_does_not_claim_authority(approved_contract):
    html = render_customer_draft(approved_contract)

    assert "customer confirmation draft" in html
    assert "Draft — customer confirmation required" in html
    assert "Proposed POC acceptance criteria" in html
    assert "This version is not frozen yet." in html
    assert "Please confirm that the quoted requirement" in html
    assert "does not authorize deployment, spending, procurement" in html
    assert "At least 95%" in html


def test_pack_rejects_an_unfrozen_contract(tmp_path):
    packet = _packet_inputs(_run(tmp_path, "pass"))
    packet["contract"] = packet["contract"].model_copy(
        update={
            "status": ContractStatus.APPROVED,
            "frozen_at": None,
            "canonical_hash": None,
        }
    )

    with pytest.raises(ValueError, match="contract is not frozen"):
        render_decision_packet(**packet)


@pytest.mark.parametrize("canonical_hash", [None, "0" * 64])
def test_pack_rejects_a_missing_or_invalid_contract_digest(tmp_path, canonical_hash):
    packet = _packet_inputs(_run(tmp_path, "pass"))
    packet["contract"] = packet["contract"].model_copy(
        update={"canonical_hash": canonical_hash}
    )

    with pytest.raises(ValueError, match="contract digest is missing or invalid"):
        render_decision_packet(**packet)


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("contract_id", "another-contract"),
        ("contract_version", "another-version"),
        ("contract_hash", "f" * 64),
    ],
)
def test_pack_rejects_a_manifest_for_another_contract(
    tmp_path, field, replacement
):
    packet = _packet_inputs(_run(tmp_path, "pass"))
    packet["manifest"] = packet["manifest"].model_copy(
        update={field: replacement}
    )

    with pytest.raises(ValueError, match="run manifest does not match"):
        render_decision_packet(**packet)


def test_pack_rejects_more_than_the_supported_single_criterion(tmp_path):
    packet = _packet_inputs(_run(tmp_path, "pass"))
    second_criterion = packet["criterion"].model_copy(
        update={"id": "TOOL-SELECT-02"}
    )
    approved_contract = packet["contract"].model_copy(
        update={
            "status": ContractStatus.APPROVED,
            "frozen_at": None,
            "canonical_hash": None,
            "criteria": (packet["criterion"], second_criterion),
        }
    )
    packet["contract"] = freeze_contract(approved_contract, frozen_at=FIXED_TIME)
    packet["manifest"] = packet["manifest"].model_copy(
        update={"contract_hash": packet["contract"].canonical_hash}
    )

    with pytest.raises(ValueError, match="exactly one frozen criterion"):
        render_decision_packet(**packet)


def test_pack_rejects_a_criterion_not_frozen_in_the_contract(tmp_path):
    packet = _packet_inputs(_run(tmp_path, "pass"))
    private_title = "different private criterion title"
    packet["criterion"] = packet["criterion"].model_copy(
        update={"title": private_title}
    )

    with pytest.raises(ValueError, match="rendered criterion does not match") as exc:
        render_decision_packet(**packet)

    assert private_title not in str(exc.value)


def test_pack_rejects_a_measurement_for_another_criterion(tmp_path):
    packet = _packet_inputs(_run(tmp_path, "pass"))
    packet["measurement"] = packet["measurement"].model_copy(
        update={"criterion_id": "ANOTHER-CRITERION"}
    )

    with pytest.raises(ValueError, match="measurement does not match"):
        render_decision_packet(**packet)


def test_pack_rejects_a_verdict_for_another_criterion(tmp_path):
    packet = _packet_inputs(_run(tmp_path, "pass"))
    packet["criterion_verdict"] = packet["criterion_verdict"].model_copy(
        update={"criterion_id": "ANOTHER-CRITERION"}
    )

    with pytest.raises(ValueError, match="criterion verdict does not match"):
        render_decision_packet(**packet)


def test_pack_rejects_a_criterion_verdict_that_was_not_recomputed(tmp_path):
    packet = _packet_inputs(_run(tmp_path, "pass"))
    private_reason = "private supplied reason"
    packet["criterion_verdict"] = packet["criterion_verdict"].model_copy(
        update={"verdict": VerdictStatus.FAIL, "reason": private_reason}
    )

    with pytest.raises(
        ValueError, match="criterion verdict does not match deterministic recomputation"
    ) as exc:
        render_decision_packet(**packet)

    assert private_reason not in str(exc.value)


def test_pack_rejects_an_overall_verdict_that_was_not_recomputed(tmp_path):
    packet = _packet_inputs(_run(tmp_path, "pass"))
    private_reason = "private overall reason"
    packet["overall"] = packet["overall"].model_copy(
        update={"verdict": VerdictStatus.FAIL, "reason": private_reason}
    )

    with pytest.raises(
        ValueError, match="overall verdict does not match deterministic recomputation"
    ) as exc:
        render_decision_packet(**packet)

    assert private_reason not in str(exc.value)


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
    approved_contract = result.contract.model_copy(
        update={
            "status": ContractStatus.APPROVED,
            "frozen_at": None,
            "canonical_hash": None,
            "criteria": (criterion,),
        }
    )
    contract = freeze_contract(approved_contract, frozen_at=FIXED_TIME)
    manifest = result.manifest.model_copy(
        update={
            "contract_hash": contract.canonical_hash,
            "provider": "<script>alert('provider')</script>",
        }
    )
    criterion_verdict = evaluate_proportion_criterion(criterion, result.measurement)
    overall = aggregate_overall_verdict(contract.criteria, [criterion_verdict])

    html = render_decision_packet(
        contract,
        manifest,
        criterion,
        result.measurement,
        criterion_verdict,
        overall,
    )

    assert "<script>alert" not in html
    assert "&lt;script&gt;alert(&#x27;quote&#x27;)&lt;/script&gt;" in html
    assert "&lt;img src=x onerror=alert(1)&gt;" in html
    assert "&lt;script&gt;alert(&#x27;provider&#x27;)&lt;/script&gt;" in html
