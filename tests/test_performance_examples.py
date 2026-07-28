from __future__ import annotations

import json
from pathlib import Path

from exitspec.confirmations import ContractConfirmation
from exitspec.performance_evidence import (
    require_frozen_confirmed,
    validate_performance_context,
    validate_performance_context_bytes,
)
from exitspec.runner import load_contract


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_ROOT = REPOSITORY_ROOT / "examples" / "inference-performance"


def test_frozen_performance_example_is_exact_and_execution_authorized():
    contract = load_contract(
        EXAMPLE_ROOT / "contracts" / "vllm-ttft-v1.frozen.json"
    )
    confirmation = ContractConfirmation.model_validate(
        json.loads(
            (
                EXAMPLE_ROOT
                / "contracts"
                / "vllm-ttft-v1.confirmation.json"
            ).read_bytes()
        )
    )
    workload_path = REPOSITORY_ROOT / contract.workload.fixture_path
    context = validate_performance_context(
        contract,
        workload_path.read_bytes(),
        bundle_root=REPOSITORY_ROOT,
    )

    assert (
        contract.canonical_hash
        == "27a91b164cf45a589693efbd5adb9cf59e03a08e31420ff7ca8f67adb9abb661"
    )
    assert require_frozen_confirmed(context, confirmation) is context
    assert context.workload.request_count == 100
    assert context.workload.concurrency == 4
    assert context.workload.synthetic_prompts is True
    assert context.expected_manifest.model == (
        "Qwen/Qwen2.5-0.5B-Instruct"
    )

    reconstructed = validate_performance_context_bytes(
        contract,
        context.workload_bytes,
        context.prompt_bytes,
    )
    assert reconstructed.expected_manifest == context.expected_manifest
    assert reconstructed.workload_sha256 == context.workload_sha256
