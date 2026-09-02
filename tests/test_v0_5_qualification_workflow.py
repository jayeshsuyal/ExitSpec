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
