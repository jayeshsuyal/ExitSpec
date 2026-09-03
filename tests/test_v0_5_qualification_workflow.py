"""Security contract tests for the PR12 status-only workflow."""

from __future__ import annotations

from pathlib import Path


WORKFLOW = (
    Path(__file__).resolve().parents[1]
    / ".github"
    / "workflows"
    / "v0_5_qualification_check.yml"
)


def test_qualification_workflow_is_read_only_and_status_only():
    content = WORKFLOW.read_text(encoding="utf-8")
    assert "permissions:\n  contents: read" in content
    assert "pull_request_target" not in content
    assert "secrets" not in content
    assert "id-token" not in content
    assert "contents: write" not in content
    assert "actions: write" not in content
    assert "deploy" not in content.casefold()
    assert "provider" not in content.casefold()
    assert "traffic" not in content.casefold()
    assert "python -m pytest -q tests/test_qualification_cli.py" in content


def test_control_harness_is_bounded_and_runs_candidate_as_data():
    harness = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "run_qualification_control_harness.py"
    ).read_text(encoding="utf-8")
    assert "MAX_SOURCE_ENTRIES" in harness
    assert "MAX_OUTPUT_BYTES" in harness
    assert "start_new_session=True" in harness
    assert '"-I", "-c"' in harness
    assert "candidate did not fail closed" in harness
