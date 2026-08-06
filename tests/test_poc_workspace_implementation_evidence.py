"""Integrity checks for the POC workspace implementation evidence record."""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_PATH = (
    PROJECT_ROOT
    / "examples/product/poc-workspace-implementation-evidence-v1.json"
)
CONTRACT_PATH = PROJECT_ROOT / "examples/product/poc-workspace-acceptance-v1.json"
CURRENT_TEST_PATH = "tests/test_poc_workspace_implementation_evidence.py"
EXPECTED_CONTRACT_HASH = (
    "4327bba2283912ab89ac2f5958bdcc2e8be81101e646697ae0fcc864f531a209"
)
EXPECTED_GATE_IDS = {f"WS-{position:02d}" for position in range(1, 13)}
EXPECTED_SCENARIO_IDS = {
    "clean_start_dynamic_email_pass_and_handoff",
    "synthetic_meeting_consent_and_review_handoff",
    "versioned_customer_change",
    "strict_fail",
    "external_block",
    "invalid_evidence_not_proven",
    "artifact_tamper_rejected",
    "replay_is_idempotent",
    "reset_revokes_stale_review_authority",
    "built_wheel_outside_checkout",
}
EXPECTED_LIMITATION_IDS = {
    "local_process_scope",
    "synthetic_data_only",
    "no_live_connectors",
    "provider_smoke_pending",
    "no_real_inference_result",
    "no_production_authorization",
    "not_production_ready",
}


def _load(path: Path = EVIDENCE_PATH) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _test_functions(path: Path) -> set[str]:
    parsed = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return {
        node.name
        for node in parsed.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("test_")
    }


def _assert_test_reference(reference: str) -> None:
    path_text, separator, function_name = reference.partition("::")
    assert separator == "::", reference
    assert path_text != CURRENT_TEST_PATH, (
        "The evidence integrity test cannot prove implementation behavior."
    )
    path = PROJECT_ROOT / path_text
    assert path.is_file(), f"Missing test reference: {path_text}"
    assert function_name in _test_functions(path), (
        f"Missing referenced test function: {reference}"
    )


def test_workspace_evidence_record_has_exact_release_scope():
    evidence = _load()
    assert set(evidence) == {
        "schema_version",
        "evidence_id",
        "evidence_version",
        "recorded_on",
        "status",
        "release_scope",
        "contract_baseline",
        "release_gate",
        "acceptance_gate_evidence",
        "adversarial_release_scenarios",
        "limitations",
    }
    assert evidence["schema_version"] == "1.0.0"
    assert evidence["evidence_id"] == (
        "exitspec-poc-workspace-implementation-v1"
    )
    assert evidence["evidence_version"] == "1.0.0"
    assert evidence["recorded_on"] == "2026-08-05"
    assert evidence["status"] == "REPRODUCIBLE_IMPLEMENTATION_EVIDENCE"

    scope = evidence["release_scope"]
    assert scope == {
        "classification": "LOCAL_OPEN_SOURCE_DEMO",
        "package_version": "0.1.0",
        "implementation_baseline_commit": (
            "44dcfb95c8dd9704081867d11335b377c2549865"
        ),
        "expected_release_tag": "v0.1.0",
        "release_gate_command": "./scripts/v0_1_release_gate.sh",
        "external_provider_required": False,
        "production_ready": False,
        "frozen_contracts_mutated": False,
    }


def test_frozen_workspace_contract_is_unchanged_and_all_gates_are_covered():
    evidence = _load()
    contract = _load(CONTRACT_PATH)
    baseline = evidence["contract_baseline"]

    assert _sha256(CONTRACT_PATH) == EXPECTED_CONTRACT_HASH
    assert baseline == {
        "contract_id": "poc-workspace-acceptance-v1",
        "path": "examples/product/poc-workspace-acceptance-v1.json",
        "sha256": EXPECTED_CONTRACT_HASH,
        "status": "FROZEN",
        "mutation_policy": "immutable historical contract",
        "historical_implementation_status_preserved": True,
    }
    assert contract["status"] == "FROZEN"
    assert contract["implementation_status"] == {
        "contract_only": True,
        "dashboard_implemented": False,
        "poc_registry_implemented": False,
        "create_flow_implemented": False,
        "multi_source_workspace_implemented": False,
        "synthetic_email_spine_already_implemented": True,
        "synthetic_meeting_transcript_source_implemented": False,
        "graphite_orange_restoration_implemented": False,
        "implementation_must_not_be_inferred_from_this_contract": True,
    }

    contract_gates = {
        item["gate_id"]: item for item in contract["acceptance_gates"]
    }
    evidence_gates = {
        item["gate_id"]: item
        for item in evidence["acceptance_gate_evidence"]
    }
    assert set(contract_gates) == EXPECTED_GATE_IDS
    assert set(evidence_gates) == EXPECTED_GATE_IDS
    assert all(item["required"] is True for item in contract_gates.values())


def test_every_gate_has_resolving_code_positive_and_adversarial_proof():
    evidence = _load()
    for gate in evidence["acceptance_gate_evidence"]:
        assert gate["status"] in {
            "PROVEN_BY_REFERENCED_TESTS",
            "PROVEN_BY_REFERENCED_TESTS_AND_BROWSER_E2E",
        }
        assert gate["claim"]
        assert gate["code_refs"]
        assert gate["positive_test_refs"]
        assert gate["adversarial_test_refs"]

        for path_text in gate["code_refs"]:
            assert (PROJECT_ROOT / path_text).is_file(), path_text
        for reference in (
            gate["positive_test_refs"] + gate["adversarial_test_refs"]
        ):
            _assert_test_reference(reference)


def test_release_scenario_inventory_is_exact_and_executable():
    evidence = _load()
    scenarios = evidence["adversarial_release_scenarios"]
    assert {item["scenario_id"] for item in scenarios} == EXPECTED_SCENARIO_IDS
    for scenario in scenarios:
        assert scenario["expected"]
        assert scenario["test_refs"]
        for reference in scenario["test_refs"]:
            _assert_test_reference(reference)

    gate = evidence["release_gate"]
    _assert_test_reference(gate["clean_process_browser_test"])
    _assert_test_reference(gate["synthetic_meeting_browser_test"])
    _assert_test_reference(gate["built_wheel_test"])
    assert gate["browser_environment_flag"] == "EXITSPEC_BROWSER_E2E=1"
    assert gate["provider_mode"] == "disabled"
    assert gate["input_classification"] == "approved synthetic data only"


def test_release_gate_composes_engineering_and_mandatory_chromium_checks():
    release_script = (
        PROJECT_ROOT / "scripts/v0_1_release_gate.sh"
    ).read_text(encoding="utf-8")
    engineering_script = (
        PROJECT_ROOT / "scripts/engineering_gate.sh"
    ).read_text(encoding="utf-8")

    assert "set -euo pipefail" in release_script
    assert "export EXITSPEC_BROWSER_E2E=1" in release_script
    assert 'exec "${script_directory}/engineering_gate.sh"' in release_script
    assert "tests/test_distribution.py" in engineering_script
    assert 'pytest --ignore=tests/test_distribution.py' in engineering_script
    assert "node --check" in engineering_script


def test_release_limits_are_exact_and_prevent_production_overclaim():
    evidence = _load()
    limitations = evidence["limitations"]
    assert {
        item["limitation_id"] for item in limitations
    } == EXPECTED_LIMITATION_IDS
    assert all(item["statement"] for item in limitations)
    joined = " ".join(item["statement"] for item in limitations).lower()
    for required_boundary in (
        "synthetic",
        "non-durable",
        "fireworks",
        "vllm",
        "production",
        "pass never authorizes",
    ):
        assert required_boundary in joined
