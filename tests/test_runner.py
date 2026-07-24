import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from exitspec.contracts import freeze_contract
from exitspec.fixtures import fixture_sha256
from exitspec.models import RunStatus, VerdictStatus
from exitspec.runner import load_contract, run_demo


FIXED_TIME = datetime(2026, 7, 22, 17, 0, tzinfo=timezone.utc)
CONTRACT_PATH = (
    Path(__file__).resolve().parents[1]
    / "examples/support-agent/contracts/tool-selection-v1.frozen.yaml"
)
APPROVED_CONTRACT_PATH = (
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


def test_example_contract_declares_the_committed_fixture_hash(fixture_path):
    contract = load_contract(CONTRACT_PATH)

    assert contract.workload.sha256 == fixture_sha256(fixture_path)
    assert contract.confirmation_id


def test_runner_rejects_internally_approved_but_unconfirmed_contract(
    tmp_path,
    fixture_path,
):
    with pytest.raises(ValueError, match="customer-confirmed frozen contract"):
        run_demo(
            contract_path=APPROVED_CONTRACT_PATH,
            fixture_path=fixture_path,
            scenario="pass",
            output_root=tmp_path,
            run_id="must-not-run",
            now=FIXED_TIME,
        )


def test_runner_rejects_legacy_frozen_contract_without_confirmation_provenance(
    tmp_path,
    fixture_path,
):
    legacy_frozen = freeze_contract(load_contract(APPROVED_CONTRACT_PATH), FIXED_TIME)
    contract_path = tmp_path / "legacy-frozen.yaml"
    contract_path.write_text(
        yaml.safe_dump(legacy_frozen.model_dump(mode="json"), sort_keys=False),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="customer-confirmed frozen contract"):
        run_demo(
            contract_path=contract_path,
            fixture_path=fixture_path,
            scenario="pass",
            output_root=tmp_path,
            run_id="must-not-run-legacy-freeze",
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
