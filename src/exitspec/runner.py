"""Run the deterministic Brick 1 evidence chain and write inspectable artifacts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

import yaml

from .adapters.deterministic_tool_selection import DeterministicToolSelectionAdapter
from .contracts import freeze_contract, utc_now, verify_contract_digest
from .fixtures import fixture_sha256, load_tool_selection_fixture
from .models import (
    ContractStatus,
    CriterionVerdict,
    EvidenceArtifact,
    OverallVerdict,
    POCContract,
    ProportionMeasurement,
    RunManifest,
    RunStatus,
)
from .reporting import render_decision_packet
from .statistics import CALCULATION_VERSION
from .verdicts import aggregate_overall_verdict, evaluate_proportion_criterion


@dataclass(frozen=True)
class RunResult:
    output_dir: Path
    contract: POCContract
    measurement: ProportionMeasurement
    criterion_verdict: CriterionVerdict
    overall_verdict: OverallVerdict
    manifest: RunManifest


def load_contract(path: Path) -> POCContract:
    parsed = yaml.safe_load(path.read_text("utf-8"))
    if not isinstance(parsed, dict):
        raise ValueError("A contract file must contain a mapping at its root.")
    return POCContract.model_validate(parsed)


def _write_json(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run_status(external_blocked_reason: Optional[str], internal_error: Optional[str]) -> RunStatus:
    if external_blocked_reason:
        return RunStatus.BLOCKED
    if internal_error:
        return RunStatus.FAILED_INTERNAL
    return RunStatus.COMPLETED


def _require_brick_one_contract(contract: POCContract) -> None:
    if len(contract.criteria) != 1:
        raise ValueError("Brick 1 supports exactly one criterion per demo run.")
    if contract.criteria[0].adapter != DeterministicToolSelectionAdapter.name:
        raise ValueError("Brick 1 requires the deterministic_tool_selection adapter.")


def run_demo(
    contract_path: Path,
    fixture_path: Path,
    scenario: str,
    output_root: Path,
    run_id: Optional[str] = None,
    now: Optional[datetime] = None,
) -> RunResult:
    """Execute a deterministic sample and produce a complete evidence packet."""

    started_at = now or utc_now()
    loaded_contract = load_contract(contract_path)
    _require_brick_one_contract(loaded_contract)

    if loaded_contract.status == ContractStatus.APPROVED:
        contract = freeze_contract(loaded_contract, frozen_at=started_at)
    elif loaded_contract.status == ContractStatus.FROZEN and verify_contract_digest(
        loaded_contract
    ):
        contract = loaded_contract
    else:
        raise ValueError(
            "The demo requires an approved contract or a frozen contract with a valid digest."
        )

    fixture_hash = fixture_sha256(fixture_path)
    _, fixture_cases = load_tool_selection_fixture(fixture_path)
    criterion = contract.criteria[0]
    adapter = DeterministicToolSelectionAdapter()

    resolved_run_id = run_id or "demo-{0}-{1}".format(
        scenario, started_at.strftime("%Y%m%dT%H%M%SZ")
    )
    run_dir = output_root / resolved_run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    evidence_dir = run_dir / "evidence"
    evidence_dir.mkdir()

    initial_manifest = RunManifest(
        run_id=resolved_run_id,
        contract_id=contract.id,
        contract_version=contract.version,
        contract_hash=contract.canonical_hash or "",
        fixture_hash=fixture_hash,
        started_at=started_at,
        ended_at=started_at,
        provider=contract.target_system.provider,
        endpoint_class=contract.target_system.endpoint_class,
        model=contract.target_system.model,
        region="local",
        runtime_configuration={"scenario": scenario},
        traffic_shape="deterministic fixture",
        warm_state="not-applicable",
        adapter_versions={adapter.name: adapter.version},
        retry_policy="none for deterministic demo",
        redaction_policy="synthetic fixture; no PII expected",
        environment_metadata={"fixture_case_count": len(fixture_cases)},
        status=RunStatus.RUNNING,
    )
    manifest_path = run_dir / "run-manifest.json"
    _write_json(manifest_path, initial_manifest.model_dump(mode="json"))

    execution = adapter.execute(fixture_cases, scenario)
    artifacts = []
    evidence_refs = []
    if execution.records:
        evidence_path = evidence_dir / "{0}.jsonl".format(criterion.id)
        with evidence_path.open("w", encoding="utf-8") as evidence_file:
            for record in execution.records:
                evidence_file.write(record.model_dump_json() + "\n")
        artifact = EvidenceArtifact(
            artifact_id="evidence-{0}".format(criterion.id),
            criterion_id=criterion.id,
            run_id=resolved_run_id,
            artifact_type="tool_selection_records",
            storage_path=str(evidence_path.relative_to(run_dir)),
            media_type="application/x-ndjson",
            sha256=_sha256_file(evidence_path),
            created_at=started_at,
            redaction_state="synthetic-no-pii",
            producer_adapter="{0}@{1}".format(adapter.name, adapter.version),
            provenance={
                "fixture_sha256": fixture_hash,
                "scenario": scenario,
                "record_count": len(execution.records),
            },
        )
        artifacts.append(artifact)
        evidence_refs.append(artifact.artifact_id)

    measurement = ProportionMeasurement(
        criterion_id=criterion.id,
        sample_count=execution.sample_count,
        success_count=execution.success_count,
        evidence_refs=evidence_refs,
        external_blocked_reason=execution.external_blocked_reason,
        internal_error=execution.internal_error,
        metadata_complete=True,
        workload_hash_matches=contract.workload.sha256 == fixture_hash,
        artifact_integrity_valid=all(
            _sha256_file(run_dir / artifact.storage_path) == artifact.sha256
            for artifact in artifacts
        ),
    )
    criterion_verdict = evaluate_proportion_criterion(criterion, measurement)
    overall_verdict = aggregate_overall_verdict(contract.criteria, [criterion_verdict])

    ended_at = now or utc_now()
    manifest = initial_manifest.model_copy(
        update={
            "ended_at": ended_at,
            "status": _run_status(
                execution.external_blocked_reason, execution.internal_error
            ),
        }
    )
    _write_json(manifest_path, manifest.model_dump(mode="json"))

    contract_output_path = run_dir / "contract.json"
    artifacts_output_path = run_dir / "evidence-artifacts.json"
    calculations_path = run_dir / "calculations.json"
    verdicts_path = run_dir / "verdicts.json"
    report_path = run_dir / "decision-packet.html"

    _write_json(contract_output_path, contract.model_dump(mode="json"))
    _write_json(
        artifacts_output_path,
        {"artifacts": [artifact.model_dump(mode="json") for artifact in artifacts]},
    )
    _write_json(
        calculations_path,
        {
            "calculation_version": CALCULATION_VERSION,
            "criterion_id": criterion.id,
            "approved_rule": criterion.rule.model_dump(mode="json"),
            "measurement": measurement.model_dump(mode="json"),
            "criterion_verdict": criterion_verdict.model_dump(mode="json"),
        },
    )
    _write_json(
        verdicts_path,
        {
            "contract_id": contract.id,
            "contract_version": contract.version,
            "contract_hash": contract.canonical_hash,
            "overall": overall_verdict.model_dump(mode="json"),
            "criteria": [criterion_verdict.model_dump(mode="json")],
        },
    )
    report_path.write_text(
        render_decision_packet(
            contract,
            manifest,
            criterion,
            measurement,
            criterion_verdict,
            overall_verdict,
        ),
        encoding="utf-8",
    )

    artifact_paths = [
        contract_output_path,
        manifest_path,
        artifacts_output_path,
        calculations_path,
        verdicts_path,
        report_path,
    ]
    artifact_paths.extend(run_dir / artifact.storage_path for artifact in artifacts)
    _write_json(
        run_dir / "artifact-hashes.json",
        {
            "algorithm": "sha256",
            "artifacts": {
                str(path.relative_to(run_dir)): _sha256_file(path)
                for path in sorted(artifact_paths)
            },
        },
    )

    return RunResult(
        output_dir=run_dir,
        contract=contract,
        measurement=measurement,
        criterion_verdict=criterion_verdict,
        overall_verdict=overall_verdict,
        manifest=manifest,
    )
