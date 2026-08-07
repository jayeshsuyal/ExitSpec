"""Test-only builders for the independently maintained Inferdrome importer."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path

from exitspec.canonical import canonical_json_bytes
from exitspec.confirmations import (
    ConfirmationDecision,
    ContractConfirmation,
    record_confirmation,
)
from exitspec.contracts import freeze_confirmed_contract
from exitspec.models import POCContract
from exitspec.performance_evidence import (
    ValidatedPerformanceContext,
    validate_performance_context,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
INFERDROME_FIXTURES = PROJECT_ROOT / "tests" / "fixtures" / "inferdrome"
FIXED_TIME = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)
PROMPTS = (
    "Inferdrome capability request zero.",
    "Inferdrome capability request one.",
    (
        "Inferdrome capability request two; the mock endpoint will reject "
        "this measured request."
    ),
    "Inferdrome capability request three.",
)


def mutable_bundle_copy(tmp_path: Path, template: str = "vllm-template") -> Path:
    target = tmp_path / template
    shutil.copytree(INFERDROME_FIXTURES / template, target)
    for directory, directory_names, filenames in os.walk(target):
        current = Path(directory)
        current.chmod(0o700)
        for directory_name in directory_names:
            (current / directory_name).chmod(0o700)
        for filename in filenames:
            (current / filename).chmod(0o600)
    return target


def build_context(
    tmp_path: Path,
    *,
    adapter: str = "vllm_bench_serve",
    error_threshold: float = 0.5,
    prompts: tuple[str, ...] = PROMPTS,
    model: str = "inferdrome/mock-model",
    request_count: int = 4,
) -> tuple[ValidatedPerformanceContext, ContractConfirmation]:
    prompt_bytes = b"".join(
        canonical_json_bytes({"id": f"prompt-{index + 1}", "content": content}) + b"\n"
        for index, content in enumerate(prompts)
    )
    prompt_path = tmp_path / "prompts.jsonl"
    prompt_path.write_bytes(prompt_bytes)
    workload_payload = {
        "schema_version": "exitspec.performance-workload.v1",
        "workload_id": "inferdrome-import-v1",
        "adapter": adapter,
        "adapter_version": "1.0.0",
        "endpoint": "http://127.0.0.1:18083/v1/chat/completions",
        "model": model,
        "request_count": request_count,
        "concurrency": 2,
        "warmup_count": 2,
        "timeout_seconds": 30,
        "max_tokens": 2,
        "max_stream_bytes": 1_048_576,
        "first_token_definition": "first_nonempty_choices_delta_content_v1",
        "warmup_included_in_measurement": False,
        "synthetic_prompts": True,
        "prompt_fixture_path": "prompts.jsonl",
        "prompt_fixture_sha256": hashlib.sha256(prompt_bytes).hexdigest(),
        "retries": 0,
    }
    workload_bytes = (json.dumps(workload_payload, indent=2) + "\n").encode()
    approved = POCContract.model_validate(
        {
            "id": "inferdrome-import-demo",
            "version": "1.0.0",
            "status": "APPROVED",
            "created_at": "2026-08-06T11:00:00Z",
            "approved_at": "2026-08-06T11:30:00Z",
            "customer": "Synthetic Import Test Co.",
            "use_case": "Verify independent Inferdrome evidence ingestion.",
            "target_system": {
                "provider": "vllm-local",
                "endpoint_class": "openai-compatible-chat-completions",
                "model": model,
            },
            "workload": {
                "fixture_path": "workload.json",
                "sha256": hashlib.sha256(workload_bytes).hexdigest(),
            },
            "criteria": [
                {
                    "criterion_type": "inference_performance_v1",
                    "id": "INFERENCE-PERF-01",
                    "title": "Imported latency and reliability",
                    "must_have": True,
                    "human_added": True,
                    "normalized_claim": (
                        "The exact imported workload meets the approved latency "
                        "and reliability requirements."
                    ),
                    "ttft_p95": {
                        "metric": "time_to_first_token",
                        "aggregation": "p95",
                        "unit": "milliseconds",
                        "operator": "lt",
                        "threshold": 500.0,
                        "method": "nearest_rank",
                        "minimum_successful_samples": min(3, request_count),
                        "must_pass": True,
                    },
                    "error_rate": {
                        "metric": "error_rate",
                        "aggregation": "rate",
                        "unit": "proportion",
                        "operator": "lt",
                        "threshold": error_threshold,
                        "method": "failed_attempts_over_total_attempts",
                        "minimum_attempts": request_count,
                        "must_pass": True,
                    },
                    "workload_slice": "inferdrome-import-v1",
                    "adapter": adapter,
                    "adapter_version": "1.0.0",
                    "owner": "vendor_solutions_engineer",
                    "evidence_policy": (
                        "Retain the external bundle digest and independent "
                        "recalculation receipt."
                    ),
                    "approved": True,
                }
            ],
            "owners": ["vendor_solutions_engineer"],
            "non_goals": [],
            "evidence_retention_policy": "Retain exact test evidence.",
        }
    )
    confirmation = record_confirmation(
        approved,
        confirmer_identity="customer@example.com",
        decision=ConfirmationDecision.CONFIRM,
        agreement_acknowledged=True,
        rationale="This synthetic contract is correct.",
        idempotency_key=(
            f"inferdrome-{adapter}-{error_threshold}-{model}-{request_count}"
        ),
        decided_at=FIXED_TIME,
    )
    frozen = freeze_confirmed_contract(approved, confirmation, FIXED_TIME)
    context = validate_performance_context(
        frozen,
        workload_bytes,
        bundle_root=tmp_path,
    )
    return context, confirmation


def bind_customer_bundle(bundle: Path, contract_hash: str) -> None:
    descriptor_path = bundle / "bundle.json"
    resolved_path = bundle / "experiment.resolved.json"
    descriptor = json.loads(descriptor_path.read_bytes())
    resolved = json.loads(resolved_path.read_bytes())
    tagged = f"sha256:{contract_hash}"
    descriptor["evidence_eligibility"] = "CUSTOMER_ELIGIBLE"
    descriptor["digests"]["exitspec_contract_digest"] = tagged
    resolved["links"]["exitspec_contract_digest"] = tagged
    descriptor_path.write_bytes(canonical_json_bytes(descriptor))
    resolved_path.write_bytes(canonical_json_bytes(resolved))
    rehash_manifest(bundle, {"bundle.json", "experiment.resolved.json"})


def rehash_manifest(bundle: Path, relative_paths: set[str]) -> None:
    manifest_path = bundle / "integrity" / "artifact-hashes.json"
    manifest = json.loads(manifest_path.read_bytes())
    remaining = set(relative_paths)
    for entry in manifest["entries"]:
        relative_path = entry["path"]
        if relative_path not in remaining:
            continue
        content = (bundle / relative_path).read_bytes()
        entry["size_bytes"] = len(content)
        entry["sha256"] = f"sha256:{hashlib.sha256(content).hexdigest()}"
        remaining.remove(relative_path)
    if remaining:
        raise AssertionError(f"Manifest entry not found: {remaining}")
    manifest_path.write_bytes(canonical_json_bytes(manifest))
