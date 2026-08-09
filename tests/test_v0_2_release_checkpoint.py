"""Integrity checks for the additive ExitSpec v0.2.0 checkpoint."""

from __future__ import annotations

from pathlib import Path
import stat
import tomllib

import exitspec


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RELEASE_PATH = PROJECT_ROOT / "docs" / "RELEASE_V0_2.md"
DEMO_PATH = PROJECT_ROOT / "docs" / "DEMO_RUNBOOK.md"
GATE_PATH = PROJECT_ROOT / "scripts" / "v0_2_release_gate.sh"


def test_package_version_matches_the_v0_2_checkpoint():
    project = tomllib.loads(
        (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )

    assert project["project"]["version"] == "0.2.0"
    assert exitspec.__version__ == "0.2.0"


def test_v0_2_release_gate_composes_the_existing_engineering_gate():
    script = GATE_PATH.read_text(encoding="utf-8")

    assert GATE_PATH.stat().st_mode & stat.S_IXUSR
    assert "set -euo pipefail" in script
    assert "export EXITSPEC_BROWSER_E2E=1" in script
    assert 'exec "${script_directory}/engineering_gate.sh"' in script
    assert "external-evidence handoff coverage" in script


def test_v0_2_release_notes_preserve_v0_1_and_freeze_the_next_zoom_gate():
    release = RELEASE_PATH.read_text(encoding="utf-8")
    normalized_release = " ".join(release.split())

    assert "immutable `v0.1.0` tag" in release
    assert 'git tag -a v0.2.0 -m "ExitSpec v0.2.0"' in release
    assert "must not be moved or rewritten" in release
    assert (
        "fixture acquisition, not a guessed wire adapter"
        in normalized_release
    )
    assert "does not claim" in release


def test_demo_runbook_exposes_the_optional_pathless_evidence_handoff():
    runbook = DEMO_PATH.read_text(encoding="utf-8")

    assert "Optional sealed Inferdrome evidence handoff" in runbook
    assert "Select sealed evidence" in runbook
    assert "INGESTION_REJECTED" in runbook
    assert "browser receives no filesystem path" in runbook
