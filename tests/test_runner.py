import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from exitspec.models import RunStatus, VerdictStatus
from exitspec.runner import run_demo


FIXED_TIME = datetime(2026, 7, 22, 17, 0, tzinfo=timezone.utc)
CONTRACT_PATH = (
    Path(__file__).resolve().parents[1]
    / "examples/support-agent/contracts/tool-selection-v1.yaml"
)


def run_scenario(tmp_path, fixture_path, scenario):
    return run_demo(
        contract_path=CONTRACT_PATH,
        fixture_path=fixture_path,
        scenario=scenario,
        output_root=tmp_path,
        run_id="demo-" + scenario,
        now=FIXED_TIME,
    )


def test_pass_run_writes_complete_evidence_packet(tmp_path, fixture_path):
    result = run_scenario(tmp_path, fixture_path, "pass")

    assert result.criterion_verdict.verdict == VerdictStatus.PASS
    assert result.overall_verdict.verdict == VerdictStatus.PASS
    assert result.manifest.status == RunStatus.COMPLETED
    for filename in (
        "contract.json",
        "run-manifest.json",
        "evidence-artifacts.json",
        "calculations.json",
        "verdicts.json",
        "decision-packet.html",
        "artifact-hashes.json",
        "evidence/TOOL-SELECT-01.jsonl",
    ):
        assert (result.output_dir / filename).exists()


def test_insufficient_run_is_not_proven(tmp_path, fixture_path):
    result = run_scenario(tmp_path, fixture_path, "insufficient")

    assert result.criterion_verdict.verdict == VerdictStatus.NOT_PROVEN
    assert result.measurement.sample_count == 100


def test_fixture_hash_mismatch_is_not_proven(tmp_path, fixture_path):
    changed_fixture = tmp_path / "changed-fixture.json"
    changed_fixture.write_bytes(fixture_path.read_bytes() + b"\n")

    result = run_scenario(tmp_path, changed_fixture, "pass")

    assert result.criterion_verdict.verdict == VerdictStatus.NOT_PROVEN
    assert "fixture hash" in result.criterion_verdict.reason


def test_blocked_run_is_blocked_not_failed(tmp_path, fixture_path):
    result = run_scenario(tmp_path, fixture_path, "blocked")

    assert result.criterion_verdict.verdict == VerdictStatus.BLOCKED
    assert result.overall_verdict.verdict == VerdictStatus.BLOCKED
    assert result.manifest.status == RunStatus.BLOCKED


def test_artifact_hashes_match_written_artifacts(tmp_path, fixture_path):
    result = run_scenario(tmp_path, fixture_path, "pass")
    hashes = json.loads((result.output_dir / "artifact-hashes.json").read_text("utf-8"))

    for relative_path, expected_hash in hashes["artifacts"].items():
        actual = hashlib.sha256(
            (result.output_dir / relative_path).read_bytes()
        ).hexdigest()
        assert actual == expected_hash
