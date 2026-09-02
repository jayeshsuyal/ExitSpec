"""Final v0.5 candidate-state, workflow, and train-closure contracts."""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "docs" / "V0_5_QUALIFICATION_GATE_PLAN.md"
LEDGER = ROOT / "docs" / "V0_5_EXECUTION_LEDGER.md"
CHECKPOINT = ROOT / "docs" / "V0_5_CANDIDATE_CHECKPOINT.md"
WORKFLOW = ROOT / ".github" / "workflows" / "v0_5_qualification_check.yml"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_final_checkpoint_closes_exactly_fourteen_merged_milestones():
    plan = _read(PLAN)
    ledger = _read(LEDGER)
    checkpoint = _read(CHECKPOINT)

    headings = re.findall(r"(?m)^### PR(\d+) — (.+)$", plan)
    assert [number for number, _ in headings] == [str(number) for number in range(1, 15)]
    rows = re.findall(r"(?m)^\| PR(\d+) \| .*? \| (MERGED) \|", ledger)
    assert [number for number, _ in rows] == [str(number) for number in range(1, 15)]
    for marker in (
        "f2030a5e9d7286c5d28e56ccdd6c86fc904d5db4",
        "9c6c63753299fa2769de6e97eb5a090b6e9e6d42",
        "PR #168",
        "PR #169",
        "33627911152",
        "33627911191",
    ):
        assert marker in ledger
    assert "Final checkpoint" in plan
    assert "final engineering candidate only" in checkpoint


def test_candidate_checkpoint_is_explicitly_not_a_release_or_authority_grant():
    combined = "\n".join(_read(path) for path in (PLAN, LEDGER, CHECKPOINT))

    assert "v0.5 is not tagged" in combined
    assert "not a release claim" in combined
    assert "authorized to send production traffic" in combined
    assert "local, synthetic" in combined
    assert "process-local" in combined
    assert "non-durable" in combined
    assert "ExitSpec never authorizes deployment or traffic." in combined


def test_qualification_workflow_remains_least_privilege_and_status_only():
    workflow = _read(WORKFLOW)

    assert "permissions:\n  contents: read" in workflow
    assert "pull_request_target" not in workflow
    lowered = workflow.lower()
    for forbidden in (
        "id-token",
        "secrets.",
        "deploy",
        "provider",
        "production traffic",
        "traffic expansion",
        "write:",
    ):
        assert forbidden not in lowered
    assert "tests/test_qualification_cli.py tests/test_qualification_assessment.py" in workflow
