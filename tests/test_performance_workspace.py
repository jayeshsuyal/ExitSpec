import json
from pathlib import Path

from exitspec.models import ContractStatus
from exitspec.performance_workspace import (
    PERFORMANCE_CONTRACT_HASH,
    PERFORMANCE_POC_ID,
    load_performance_demo_bundle,
    performance_poc_detail_payload,
    performance_workspace_record_and_facts,
)
from exitspec.workspace import (
    WorkspaceAction,
    WorkspaceEvidenceState,
    WorkspacePhase,
    project_poc,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
RESOURCE_ROOT = (
    REPOSITORY_ROOT
    / "src/exitspec/demo_data/inference_performance"
)
EXAMPLE_ROOT = REPOSITORY_ROOT / "examples/inference-performance"


def test_bundled_performance_workspace_matches_the_executable_examples():
    pairs = (
        (
            RESOURCE_ROOT / "contracts/vllm-ttft-v2.frozen.json",
            EXAMPLE_ROOT / "contracts/vllm-ttft-v2.frozen.json",
        ),
        (
            RESOURCE_ROOT / "contracts/vllm-ttft-v2.confirmation.json",
            EXAMPLE_ROOT / "contracts/vllm-ttft-v2.confirmation.json",
        ),
        (
            RESOURCE_ROOT / "workloads/concurrency-4-v1.json",
            EXAMPLE_ROOT / "workloads/concurrency-4-v1.json",
        ),
        (
            RESOURCE_ROOT / "prompts/synthetic-latency-v1.jsonl",
            EXAMPLE_ROOT / "prompts/synthetic-latency-v1.jsonl",
        ),
    )

    for bundled, example in pairs:
        assert bundled.read_bytes() == example.read_bytes()


def test_performance_demo_bundle_is_frozen_confirmed_and_byte_bound():
    bundle = load_performance_demo_bundle()

    assert bundle.context.contract.status == ContractStatus.FROZEN
    assert bundle.context.contract.canonical_hash == PERFORMANCE_CONTRACT_HASH
    assert (
        bundle.context.contract.confirmation_id
        == bundle.confirmation.confirmation_id
    )
    assert bundle.context.workload.request_count == 100
    assert bundle.context.workload.concurrency == 4
    assert bundle.context.workload.warmup_count == 10


def test_performance_workspace_stays_in_prove_until_evidence_exists():
    record, facts = performance_workspace_record_and_facts()
    projection = project_poc(record, facts)

    assert record.poc_id == PERFORMANCE_POC_ID
    assert projection.derived_phase == WorkspacePhase.PROVE
    assert projection.next_action_code == WorkspaceAction.RUN_POC
    assert projection.latest_evidence_summary.status == (
        WorkspaceEvidenceState.NOT_RUN
    )
    assert projection.latest_evidence_summary.report_url is None


def test_performance_detail_exposes_requirements_not_invented_results():
    detail = performance_poc_detail_payload()
    serialized = json.dumps(detail, sort_keys=True).lower()

    assert detail["agreement_status"] == "FROZEN"
    assert detail["execution_status"] == "NOT_STARTED"
    assert detail["evidence_status"] == "NOT_RUN"
    assert [rule["threshold"] for rule in detail["requirements"]] == [
        "< 500 ms",
        "< 1%",
    ]
    assert "observed" not in serialized
    assert "pass" not in serialized
    assert "fail" not in serialized
