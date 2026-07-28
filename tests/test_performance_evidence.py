from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from exitspec.confirmations import ConfirmationDecision, record_confirmation
from exitspec.contracts import freeze_confirmed_contract
from exitspec.models import POCContract
from exitspec.performance_evidence import (
    PerformanceEvidenceError,
    ValidatedPerformanceContext,
    parse_performance_workload,
    require_frozen_confirmed,
    validate_performance_context,
)
from exitspec.runner import load_contract


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PERFORMANCE_CONTRACT_PATH = (
    PROJECT_ROOT / "examples/inference-performance/contracts/vllm-ttft-v1.yaml"
)
WORKLOAD_PATH = (
    PROJECT_ROOT
    / "examples/inference-performance/workloads/concurrency-4-v1.json"
)
PROMPT_PATH = (
    PROJECT_ROOT
    / "examples/inference-performance/prompts/synthetic-latency-v1.jsonl"
)
FIXED_TIME = datetime(2026, 7, 28, 8, 0, tzinfo=timezone.utc)


def _workload_payload() -> dict:
    return json.loads(WORKLOAD_PATH.read_text(encoding="utf-8"))


def _workload_bytes(payload: dict) -> bytes:
    return (json.dumps(payload, indent=2) + "\n").encode("utf-8")


def _contract_for_workload(
    workload_bytes: bytes,
    *,
    fixture_path: str = "workloads/performance.json",
) -> POCContract:
    contract = load_contract(PERFORMANCE_CONTRACT_PATH)
    payload = contract.model_dump(mode="python")
    payload["workload"] = {
        "fixture_path": fixture_path,
        "sha256": hashlib.sha256(workload_bytes).hexdigest(),
    }
    return POCContract.model_validate(payload)


def _bundle(
    tmp_path: Path,
    *,
    workload_changes: dict | None = None,
    prompt_bytes: bytes | None = None,
) -> tuple[POCContract, bytes]:
    prompt_content = prompt_bytes if prompt_bytes is not None else PROMPT_PATH.read_bytes()
    prompt_target = tmp_path / "prompts" / "synthetic.jsonl"
    prompt_target.parent.mkdir(parents=True)
    prompt_target.write_bytes(prompt_content)

    payload = _workload_payload()
    payload["prompt_fixture_path"] = "prompts/synthetic.jsonl"
    payload["prompt_fixture_sha256"] = hashlib.sha256(prompt_content).hexdigest()
    if workload_changes:
        payload.update(workload_changes)
    exact_bytes = _workload_bytes(payload)
    return _contract_for_workload(exact_bytes), exact_bytes


def _confirmation(contract: POCContract, *, key: str = "confirm-performance-v1"):
    return record_confirmation(
        contract,
        confirmer_identity="customer@example.com",
        decision=ConfirmationDecision.CONFIRM,
        agreement_acknowledged=True,
        rationale="The performance requirements match the agreed POC.",
        idempotency_key=key,
        decided_at=FIXED_TIME,
    )


def test_exact_workload_and_prompt_bytes_build_typed_expected_manifest():
    contract = load_contract(PERFORMANCE_CONTRACT_PATH)
    workload_bytes = WORKLOAD_PATH.read_bytes()

    context = validate_performance_context(
        contract,
        workload_bytes,
        bundle_root=PROJECT_ROOT,
    )

    assert isinstance(context, ValidatedPerformanceContext)
    assert context.workload_sha256 == contract.workload.sha256
    assert context.prompt_sha256 == context.workload.prompt_fixture_sha256
    assert context.criterion.id == "INFERENCE-PERF-01"
    assert context.probe_config.request_count == 100
    assert context.probe_config.concurrency == 4
    assert context.expected_manifest.model == contract.target_system.model
    assert context.expected_manifest.request_count == 100
    assert context.expected_manifest.warmup_included_in_measurement is False
    assert context.expected_manifest.manifest_sha256 == (
        "c93a54db67a73c1f2ab6c8968164bc4838e64230b25bbfc6c6e8dcccda50860a"
    )


def test_approved_contract_can_author_context_but_cannot_authorize_execution():
    contract = load_contract(PERFORMANCE_CONTRACT_PATH)
    context = validate_performance_context(
        contract,
        WORKLOAD_PATH.read_bytes(),
        bundle_root=PROJECT_ROOT,
    )
    confirmation = _confirmation(contract)

    with pytest.raises(PerformanceEvidenceError, match="frozen contract"):
        require_frozen_confirmed(context, confirmation)


def test_exact_frozen_contract_and_confirmation_authorize_execution():
    approved = load_contract(PERFORMANCE_CONTRACT_PATH)
    confirmation = _confirmation(approved)
    frozen = freeze_confirmed_contract(approved, confirmation, FIXED_TIME)
    context = validate_performance_context(
        frozen,
        WORKLOAD_PATH.read_bytes(),
        bundle_root=PROJECT_ROOT,
    )

    assert require_frozen_confirmed(context, confirmation) is context


def test_execution_gate_rejects_invalid_digest_and_wrong_confirmation():
    approved = load_contract(PERFORMANCE_CONTRACT_PATH)
    confirmation = _confirmation(approved)
    frozen = freeze_confirmed_contract(approved, confirmation, FIXED_TIME)
    context = validate_performance_context(
        frozen,
        WORKLOAD_PATH.read_bytes(),
        bundle_root=PROJECT_ROOT,
    )
    corrupted = frozen.model_copy(update={"canonical_hash": "0" * 64})
    corrupted_context = validate_performance_context(
        corrupted,
        WORKLOAD_PATH.read_bytes(),
        bundle_root=PROJECT_ROOT,
    )
    other_confirmation = _confirmation(approved, key="another-confirmation")

    with pytest.raises(PerformanceEvidenceError, match="digest"):
        require_frozen_confirmed(corrupted_context, confirmation)
    with pytest.raises(PerformanceEvidenceError, match="identity"):
        require_frozen_confirmed(context, other_confirmation)


def test_workload_byte_tampering_is_rejected_before_use():
    contract = load_contract(PERFORMANCE_CONTRACT_PATH)
    tampered = WORKLOAD_PATH.read_bytes().replace(b'"concurrency": 4', b'"concurrency": 5')

    with pytest.raises(PerformanceEvidenceError, match="contract SHA-256"):
        validate_performance_context(
            contract,
            tampered,
            bundle_root=PROJECT_ROOT,
        )


def test_strict_workload_parser_rejects_extra_and_duplicate_fields(tmp_path):
    contract, workload_bytes = _bundle(tmp_path)
    payload = json.loads(workload_bytes)
    payload["undocumented"] = True
    extra_bytes = _workload_bytes(payload)
    extra_contract = _contract_for_workload(extra_bytes)
    duplicate_bytes = workload_bytes.replace(
        b'{\n  "schema_version"',
        b'{\n  "adapter": "duplicate",\n  "schema_version"',
        1,
    )
    duplicate_contract = _contract_for_workload(duplicate_bytes)

    with pytest.raises(PerformanceEvidenceError, match="schema"):
        validate_performance_context(
            extra_contract,
            extra_bytes,
            bundle_root=tmp_path,
        )
    with pytest.raises(PerformanceEvidenceError, match="JSON"):
        validate_performance_context(
            duplicate_contract,
            duplicate_bytes,
            bundle_root=tmp_path,
        )
    with pytest.raises(PerformanceEvidenceError):
        parse_performance_workload(b'{"request_count":"100"}')


@pytest.mark.parametrize(
    "unsafe_path",
    [
        "../outside.jsonl",
        "/tmp/outside.jsonl",
        "prompts/../outside.jsonl",
        "prompts\\outside.jsonl",
        "prompts//outside.jsonl",
    ],
)
def test_prompt_path_traversal_and_noncanonical_paths_are_rejected(
    tmp_path,
    unsafe_path,
):
    contract, workload_bytes = _bundle(
        tmp_path,
        workload_changes={"prompt_fixture_path": unsafe_path},
    )

    with pytest.raises(PerformanceEvidenceError, match="path"):
        validate_performance_context(
            contract,
            workload_bytes,
            bundle_root=tmp_path,
        )


def test_prompt_symlink_escape_is_rejected(tmp_path):
    bundle_root = tmp_path / "bundle"
    outside = tmp_path / "outside.jsonl"
    outside.write_bytes(PROMPT_PATH.read_bytes())
    prompt_link = bundle_root / "prompts" / "linked.jsonl"
    prompt_link.parent.mkdir(parents=True)
    prompt_link.symlink_to(outside)
    payload = _workload_payload()
    payload["prompt_fixture_path"] = "prompts/linked.jsonl"
    payload["prompt_fixture_sha256"] = hashlib.sha256(outside.read_bytes()).hexdigest()
    workload_bytes = _workload_bytes(payload)
    contract = _contract_for_workload(workload_bytes)

    with pytest.raises(PerformanceEvidenceError, match="escapes"):
        validate_performance_context(
            contract,
            workload_bytes,
            bundle_root=bundle_root,
        )


def test_contract_workload_path_traversal_is_rejected(tmp_path):
    contract, workload_bytes = _bundle(tmp_path)
    unsafe_contract = _contract_for_workload(
        workload_bytes,
        fixture_path="../workloads/performance.json",
    )

    with pytest.raises(PerformanceEvidenceError, match="workload fixture path"):
        validate_performance_context(
            unsafe_contract,
            workload_bytes,
            bundle_root=tmp_path,
        )


def test_prompt_byte_tampering_is_rejected(tmp_path):
    contract, workload_bytes = _bundle(tmp_path)
    prompt_target = tmp_path / "prompts" / "synthetic.jsonl"
    prompt_target.write_bytes(prompt_target.read_bytes() + b" ")

    with pytest.raises(PerformanceEvidenceError, match="Prompt fixture bytes"):
        validate_performance_context(
            contract,
            workload_bytes,
            bundle_root=tmp_path,
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("workload_id", "another-workload", "Workload identity"),
        ("model", "another/model", "target model"),
        ("adapter", "another_adapter", "adapter does not match"),
        ("adapter_version", "2.0.0", "adapter version"),
    ],
)
def test_model_adapter_and_version_must_align(
    tmp_path,
    field,
    value,
    message,
):
    contract, workload_bytes = _bundle(
        tmp_path,
        workload_changes={field: value},
    )

    with pytest.raises(PerformanceEvidenceError, match=message):
        validate_performance_context(
            contract,
            workload_bytes,
            bundle_root=tmp_path,
        )


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"request_count": 99}, "exactly match"),
        ({"retries": 1}, "retries"),
        ({"warmup_included_in_measurement": True}, "schema"),
        ({"concurrency": 101}, "safety bounds"),
        ({"max_tokens": 999_999}, "safety bounds"),
    ],
)
def test_count_semantics_and_probe_bounds_are_enforced(
    tmp_path,
    changes,
    message,
):
    contract, workload_bytes = _bundle(
        tmp_path,
        workload_changes=changes,
    )

    with pytest.raises(PerformanceEvidenceError, match=message):
        validate_performance_context(
            contract,
            workload_bytes,
            bundle_root=tmp_path,
        )


def test_successful_sample_minimum_cannot_exceed_request_count():
    contract = load_contract(PERFORMANCE_CONTRACT_PATH)
    criterion = contract.criteria[0]
    tampered_criterion = criterion.model_copy(
        update={
            "ttft_p95": criterion.ttft_p95.model_copy(
                update={"minimum_successful_samples": 101}
            )
        }
    )
    tampered_contract = contract.model_copy(
        update={"criteria": (tampered_criterion,)}
    )

    with pytest.raises(
        PerformanceEvidenceError,
        match="successful samples exceed",
    ):
        validate_performance_context(
            tampered_contract,
            WORKLOAD_PATH.read_bytes(),
            bundle_root=PROJECT_ROOT,
        )


def test_contract_without_performance_criterion_is_rejected(approved_contract):
    workload_bytes = WORKLOAD_PATH.read_bytes()
    payload = approved_contract.model_dump(mode="python")
    payload["workload"] = {
        "fixture_path": "examples/inference-performance/workloads/concurrency-4-v1.json",
        "sha256": hashlib.sha256(workload_bytes).hexdigest(),
    }
    contract = POCContract.model_validate(payload)

    with pytest.raises(PerformanceEvidenceError, match="performance criterion"):
        validate_performance_context(
            contract,
            workload_bytes,
            bundle_root=PROJECT_ROOT,
        )
