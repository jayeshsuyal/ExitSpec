"""Release-truth checks for the ExitSpec v0.3 convergence checkpoint."""

from __future__ import annotations

import stat
import tomllib
from pathlib import Path

import exitspec

ROOT = Path(__file__).resolve().parents[1]


def test_current_package_is_v0_4_while_v0_3_notes_remain_historical():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    release = (ROOT / "docs" / "RELEASE_V0_3.md").read_text(encoding="utf-8")

    assert project["project"]["version"] == "0.4.0"
    assert "ruff==0.16.5" in project["project"]["optional-dependencies"]["dev"]
    assert exitspec.__version__ == "0.4.0"
    assert "no tag or GitHub release has been created" in release
    assert "Capture → Review → Plan → Confirm → Prove → Decide" in release
    assert "Compatibility adapters" in release
    assert "Rollback" in release


def test_v0_3_gate_is_executable_exact_counted_and_zero_skip_enforced():
    gate_path = ROOT / "scripts" / "v0_3_release_gate.sh"
    gate = gate_path.read_text(encoding="utf-8")
    workflow = (ROOT / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")

    assert gate_path.stat().st_mode & stat.S_IXUSR
    assert "tests/test_a7_convergence_browser.py" in gate
    assert "expected=4" in gate
    assert "skipped == 0" in gate
    assert "failed == 0" in gate
    assert "--runxfail" in gate
    assert "export EXITSPEC_BROWSER_E2E=1" in gate
    assert 'exec "${script_directory}/engineering_gate.sh"' in gate
    assert "./scripts/v0_3_release_gate.sh" in workflow


def test_readme_names_canonical_path_compatibility_and_release_gate():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "starts at `/app`" in readme
    assert "Capture → Review → Plan → Confirm → Prove → Decide" in readme
    assert "server-owned A4 registry" in readme
    assert "./scripts/v0_3_release_gate.sh" in readme
    assert "compatibility adapters" in readme


def test_all_a7_browser_assets_remain_in_wheel_package_globs():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    package_data = project["tool"]["setuptools"]["package-data"]["exitspec"]

    assert {"static/*.html", "static/*.css", "static/*.js"}.issubset(package_data)
