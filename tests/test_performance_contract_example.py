from __future__ import annotations

import hashlib
import json
from pathlib import Path

from exitspec.models import InferencePerformanceCriterion
from exitspec.performance_probe import ProbeConfig, build_manifest, load_prompts_jsonl
from exitspec.runner import load_contract


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = (
    PROJECT_ROOT
    / "examples/inference-performance/contracts/vllm-ttft-v1.yaml"
)


def test_example_contract_binds_a_runnable_bounded_workload():
    contract = load_contract(CONTRACT_PATH)
    criterion = contract.criteria[0]
    workload_path = PROJECT_ROOT / contract.workload.fixture_path
    workload_bytes = workload_path.read_bytes()
    workload = json.loads(workload_bytes)
    prompt_path = PROJECT_ROOT / workload["prompt_fixture_path"]

    assert isinstance(criterion, InferencePerformanceCriterion)
    assert hashlib.sha256(workload_bytes).hexdigest() == contract.workload.sha256
    assert (
        hashlib.sha256(prompt_path.read_bytes()).hexdigest()
        == workload["prompt_fixture_sha256"]
    )
    assert workload["request_count"] == criterion.error_rate.minimum_attempts
    assert workload["retries"] == 0
    assert workload["warmup_included_in_measurement"] is False

    config = ProbeConfig(
        endpoint=workload["endpoint"],
        model=workload["model"],
        request_count=workload["request_count"],
        concurrency=workload["concurrency"],
        warmup_count=workload["warmup_count"],
        timeout_seconds=workload["timeout_seconds"],
        max_tokens=workload["max_tokens"],
        max_stream_bytes=workload["max_stream_bytes"],
    )
    manifest = build_manifest(config, load_prompts_jsonl(prompt_path))

    assert manifest.request_count == 100
    assert manifest.concurrency == 4
    assert manifest.first_token_definition == workload["first_token_definition"]
    assert manifest.warmup_included_in_measurement is False
