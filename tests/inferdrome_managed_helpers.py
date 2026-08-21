"""Test-only builders for the exact retrospective managed evidence path."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from exitspec.confirmations import (
    ConfirmationDecision,
    ContractConfirmation,
    record_confirmation,
)
from exitspec.contracts import freeze_confirmed_contract
from exitspec.inferdrome_archive import (
    ExtractedInferdromeArchive,
    extract_pinned_inferdrome_archive,
)
from exitspec.inferdrome_profile import (
    LOCAL_GPU_PROOF_SCHEMA_ID,
    LOCAL_GPU_PROOF_SCHEMA_SHA256,
    MANAGED_PROFILE_ID,
    MANAGED_PROFILE_SHA256,
)
from exitspec.models import POCContract


FIXED_RECEIPT_TIME = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)
REQUEST_PLAN_DIGEST = (
    "sha256:0fb852366933598da4139114f416b441c52d2c83cae07b7d8938bd482a12fc8e"
)
WORKLOAD_DIGEST = (
    "sha256:22bf3389cc29ee946ae567870d7f8d7b458594224542a796e8990c15b1cfcd63"
)
NATIVE_SCHEMA_FINGERPRINT = (
    "sha256:3a4fdee6fe9b45ce5b42c41fd3bfc6614245a36ecfe6f94de92b59717a136abb"
)
MODEL = "Qwen/Qwen2.5-0.5B-Instruct"
REVISION = "7ae557604adf67be50417f59c2c2f167def9a775"
ENDPOINT = "http://127.0.0.1:18080/"


def build_managed_contract(
    *,
    contract_id: str,
    threshold_ns: int,
    configured_max_concurrency: int = 4,
    definition_id: str = "vllm_first_choices_event_v0_26",
    target_provider: str = "inferdrome-managed-vllm",
) -> tuple[POCContract, ContractConfirmation]:
    approved = POCContract.model_validate(
        {
            "id": contract_id,
            "version": "1.0.0",
            "status": "APPROVED",
            "created_at": "2026-08-21T10:00:00Z",
            "approved_at": "2026-08-21T10:30:00Z",
            "customer": "Retrospective GPU Conformance Co.",
            "use_case": "Evaluate one retained managed vLLM performance run.",
            "target_system": {
                "provider": target_provider,
                "endpoint_class": "retained-loopback-vllm-benchmark",
                "model": MODEL,
            },
            "workload": {
                "fixture_path": "external://inferdrome/a10/workload",
                "sha256": WORKLOAD_DIGEST.removeprefix("sha256:"),
            },
            "criteria": [
                {
                    "criterion_type": "inference_performance_v3",
                    "id": "INFERENCE-PERF-EXT-01",
                    "title": "Retained native vLLM latency and reliability",
                    "must_have": True,
                    "human_added": True,
                    "normalized_claim": (
                        "At configured maximum concurrency {0}, the retained "
                        "100-request workload must have native vLLM p95 TTFT "
                        "below {1} ns and error rate below 1%."
                    ).format(configured_max_concurrency, threshold_ns),
                    "ttft_p95": {
                        "metric": "time_to_first_token",
                        "definition_id": definition_id,
                        "aggregation": "p95",
                        "unit": "nanoseconds",
                        "operator": "lt",
                        "threshold_ns": threshold_ns,
                        "reducer_id": "nearest_rank_v1",
                        "population": (
                            "successful_measured_requests_with_observed_ttft"
                        ),
                        "minimum_successful_samples": 100,
                        "must_pass": True,
                    },
                    "error_rate": {
                        "metric": "error_rate",
                        "aggregation": "rate",
                        "operator": "lt",
                        "threshold_basis_points": 100,
                        "numerator": ("failed_or_anomalous_native_measured_requests"),
                        "denominator": "all_measured_requests",
                        "exact_attempts": 100,
                        "must_pass": True,
                    },
                    "evidence_identity": {
                        "schema_version": ("exitspec.inferdrome-evidence-identity.v1"),
                        "evidence_schema_version": "inferdrome.evidence.v1",
                        "producer_name": "vllm",
                        "producer_version": "0.26.0",
                        "adapter_id": "vllm_bench_serve",
                        "adapter_version": "1.0.0",
                        "native_schema_fingerprint": NATIVE_SCHEMA_FINGERPRINT,
                        "managed_profile_id": MANAGED_PROFILE_ID,
                        "managed_profile_sha256": MANAGED_PROFILE_SHA256,
                        "local_gpu_proof_schema_id": LOCAL_GPU_PROOF_SCHEMA_ID,
                        "local_gpu_proof_schema_sha256": (
                            LOCAL_GPU_PROOF_SCHEMA_SHA256
                        ),
                        "request_plan_digest": REQUEST_PLAN_DIGEST,
                        "workload_digest": WORKLOAD_DIGEST,
                        "target_model": MODEL,
                        "target_model_revision": REVISION,
                        "target_tokenizer_revision": REVISION,
                        "target_endpoint": ENDPOINT,
                        "configured_max_concurrency": (configured_max_concurrency),
                        "exact_measured_attempts": 100,
                        "warmup_requests": 10,
                        "binding_mode": "EXTERNAL_RECEIPT_BINDING",
                        "chronology": "RETROSPECTIVE",
                        "producer_contract_link": "ABSENT",
                    },
                    "concurrency_semantics": (
                        "configured_maximum_concurrency_not_observed_overlap"
                    ),
                    "owner": "vendor_solutions_engineer",
                    "evidence_policy": (
                        "Retain the unchanged bundle digest and ExitSpec v2 receipt."
                    ),
                    "approved": True,
                }
            ],
            "owners": ["vendor_solutions_engineer"],
            "non_goals": [
                "Do not claim hardware attestation or production authorization."
            ],
            "evidence_retention_policy": (
                "Retain the checksum-pinned external archive and immutable receipt."
            ),
        }
    )
    confirmation = record_confirmation(
        approved,
        confirmer_identity="customer@example.com",
        decision=ConfirmationDecision.CONFIRM,
        agreement_acknowledged=True,
        rationale="The retrospective criterion is correct for this demonstration.",
        idempotency_key=f"{contract_id}-confirmation",
        decided_at=datetime(2026, 8, 21, 11, 0, tzinfo=UTC),
    )
    frozen = freeze_confirmed_contract(
        approved,
        confirmation,
        datetime(2026, 8, 21, 11, 30, tzinfo=UTC),
    )
    return frozen, confirmation


def extract_exact_archive_or_skip(tmp_path: Path) -> ExtractedInferdromeArchive:
    raw_path = os.environ.get("EXITSPEC_INFERDROME_A10_ARCHIVE")
    if raw_path is None:
        pytest.skip("exact external A10 archive is not available")
    return extract_pinned_inferdrome_archive(Path(raw_path), tmp_path / "a10")
