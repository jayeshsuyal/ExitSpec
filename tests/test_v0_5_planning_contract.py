"""Regression checks for the v0.5 architecture-only entry contract."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "docs" / "V0_5_QUALIFICATION_GATE_PLAN.md"
RUNBOOK = ROOT / "docs" / "V0_5_EXECUTION_RUNBOOK.md"
LEDGER = ROOT / "docs" / "V0_5_EXECUTION_LEDGER.md"


def _read(path: Path) -> str:
    assert path.is_file(), f"Required v0.5 planning artifact is missing: {path}"
    return path.read_text(encoding="utf-8")


def test_v0_5_plan_keeps_exactly_fourteen_exitspec_pr_milestones():
    plan = _read(PLAN)
    milestones = re.findall(r"(?m)^### PR(\d+) — (.+)$", plan)

    assert [number for number, _ in milestones] == [str(number) for number in range(1, 15)]
    assert milestones[0][1] == "Architecture, vocabulary, and threat contract"
    assert "Provider-neutral prospective handoff boundary" in milestones[6][1]
    assert "Provider-neutral external-evidence admission boundary" in milestones[7][1]


def test_v0_5_contract_is_provider_neutral_and_zero_authority():
    plan = _read(PLAN)
    runbook = _read(RUNBOOK)
    ledger = _read(LEDGER)
    combined = f"{plan}\n{runbook}\n{ledger}"

    assert "Inferdrome" not in combined
    assert combined.count("ExitSpec never authorizes deployment or traffic.") >= 3
    assert "provider-neutral" in combined
    assert "cross-repository" in combined
    assert "CLI JSON + local policy-consumer result" in plan


def test_v0_5_contract_preserves_proofability_verdict_and_validity_axes():
    plan = _read(PLAN)
    runbook = _read(RUNBOOK)

    for marker in (
        "`PROVABLE`, `CLARIFICATION_REQUIRED`, `NOT_PROVABLE`",
        "`PASS`, `FAIL`, `NOT_PROVEN`",
        "`CURRENT`, `STALE`, `EXPIRED`, `INVALID`",
    ):
        assert marker in plan
        assert marker in runbook

    assert "pre-admission capability" in _read(LEDGER)
    assert "ExitSpec's result from admitted evidence" in _read(LEDGER)
    assert "present applicability of a validated receipt" in _read(LEDGER)


def test_v0_5_ledger_captures_pr1_state_and_all_follow_on_milestones():
    ledger = _read(LEDGER)

    assert "Base revision:" in ledger
    assert "Candidate selector:" in ledger
    assert "PR1 | Architecture, vocabulary, and threat contract" in ledger
    assert "PR14 | Adversarial closure and candidate checkpoint" in ledger
    assert "Last updated: PR1 candidate prepared for local commit." in ledger


def test_v0_5_planning_documents_have_resolvable_local_links():
    for document in (PLAN, RUNBOOK, LEDGER):
        markdown = _read(document)
        for target in re.findall(r"\]\(([^)#]+)(?:#[^)]*)?\)", markdown):
            assert (document.parent / target).is_file(), (
                f"Broken local link in {document}: {target}"
            )
