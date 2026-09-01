"""Single package-owned synthetic authority for the PR6 planning workspace.

The fixture is immutable input material for a local proofability preflight.  It
is deliberately unrelated to a draft POC's customer, source, proposal, or live
system data.  Importing this module performs no network, provider, execution,
evidence, verdict, deployment, or traffic action.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from .contracts import contract_digest, verify_contract_digest
from .models import POCContract
from .qualification_scope import (
    QualificationContextV1,
    QualificationScopeV1,
    create_qualification_context,
    create_qualification_scope,
)
from .serving_subject import (
    ServingSubjectManifestV1,
    parse_serving_subject_manifest,
)

FIXTURE_ID: Final = "exitspec.synthetic-proofability-preflight.native-v1"
FIXTURE_VERSION: Final = "v1"
PROFILE_ID: Final = "exitspec.external-evidence.native-ttft-profile.v1"
PROFILE_VERSION: Final = "v1"
FROZEN_AT: Final = datetime(2026, 8, 31, 9, 0, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class ProofabilityFixtureAuthority:
    """Immutable trusted roots for one package-synthetic PR5 evaluation."""

    fixture_id: str
    fixture_version: str
    profile_id: str
    profile_version: str
    subject: ServingSubjectManifestV1
    scope: QualificationScopeV1
    context: QualificationContextV1
    contract: POCContract
    expected_subject_digest: str
    expected_scope_digest: str
    expected_qualification_context_digest: str
    expected_contract_id: str
    expected_contract_canonical_digest: str
    expected_profile_id: str
    expected_profile_version: str
    expected_capability_digest: str
    expected_protocol_id: str
    expected_protocol_version: str
    expected_engine_id: str
    expected_engine_version: str
    expected_adapter_id: str
    expected_adapter_version: str
    expected_proofability_report_digest: str
    expected_canonical_report_byte_count: int


def _subject() -> ServingSubjectManifestV1:
    return parse_serving_subject_manifest(
        {
            "schema_version": "exitspec.serving-subject-manifest.v1",
            "engine": {"engine_id": "vllm", "engine_version": "0.26.0"},
            "hardware": {
                "hardware_class": "NVIDIA-H100-SXM5-80GB",
                "topology": "1x8",
            },
            "launch_arguments_digest": "sha256:" + "d" * 64,
            "model": {
                "component_id": "acme/model-x",
                "revision": "0123456789abcdef",
            },
            "profile": {
                "adapter_id": "bench-adapter",
                "adapter_version": "1.0.0+pin",
                "profile_id": "serving-profile",
                "profile_version": "1.0.0+pin",
            },
            "routing_policy_digest": "sha256:" + "b" * 64,
            "routing_policy_id": "route-policy",
            "runtime_artifact_digest": "sha256:" + "a" * 64,
            "runtime_configuration_json": (
                '{"gpu_memory_utilization":90,"scheduler":'
                '{"max_num_seqs":8},"seed":42}'
            ),
            "tokenizer": {
                "component_id": "acme/tokenizer-x",
                "revision": "abcdef0123456789",
            },
            "subject_digest": (
                "sha256:2921dd76c90a5dd4a6131ef8bb7a369f7b4b1a3a829744751e6b38e81dfb988a"
            ),
        }
    )


def _criterion_payload() -> dict[str, object]:
    return {
        "criterion_type": "inference_qualification_v1",
        "schema_version": "exitspec.inference-qualification-criterion.v1",
        "protocol_id": "inference-performance-qualification",
        "protocol_version": "1.0.0",
        "id": "QUAL-TTFT-01",
        "title": "Frozen TTFT qualification question",
        "must_have": True,
        "source": None,
        "human_added": True,
        "normalized_claim": (
            "Bounded prospective native latency and reliability question."
        ),
        "latency_requirement": {
            "requirement_kind": "NATIVE_TTFT_P95",
            "observation_id": "native_ttft_sample",
            "metric_definition_id": "vllm_first_choices_event_v0_26",
            "source_field": "request.timing.ttft_ns",
            "unit": "ns",
            "population": "successful_measured_requests_with_observed_ttft",
            "reducer_id": "nearest_rank_v1",
            "percentile": "p95",
            "operator": "lt",
            "threshold_ns": 20_000_000,
            "minimum_successful_samples": 100,
            "equality_outcome": "FAIL",
            "must_pass": True,
        },
        "reliability_requirement": {
            "observation_id": "native_measured_request_outcome",
            "source_field": "request.outcome.status",
            "latency_population": (
                "successful_measured_requests_with_observed_ttft"
            ),
            "reliability_numerator": (
                "failed_or_anomalous_native_measured_requests"
            ),
            "reliability_denominator": "all_measured_requests",
            "operator": "lt",
            "threshold_basis_points": 100,
            "exact_attempts": 100,
            "must_pass": True,
        },
        "approved": True,
    }


def _contract() -> POCContract:
    contract = POCContract.model_validate(
        {
            "id": "pr5-proofability-contract",
            "version": "1.0.0",
            "status": "FROZEN",
            "created_at": FROZEN_AT,
            "approved_at": FROZEN_AT,
            "frozen_at": FROZEN_AT,
            "customer": "customer",
            "use_case": "qualification planning",
            "target_system": {
                "provider": "declared-external-system",
                "endpoint_class": "external",
                "model": "model",
            },
            "workload": {
                "fixture_path": "not-read-by-pr5.json",
                "sha256": "1" * 64,
            },
            "criteria": [_criterion_payload()],
            "owners": ["owner"],
            "non_goals": ["No authority"],
            "evidence_retention_policy": "future protocol boundary",
            "parent_version": None,
            "confirmation_id": None,
            "canonical_hash": (
                "83d8047b37446e0ec3596e8dd9cb8c76ccff49d68ac4f189268bb3f5ad80f0ee"
            ),
        }
    )
    if not verify_contract_digest(contract):
        raise RuntimeError("Synthetic proofability contract identity drifted.")
    if not hmac.compare_digest(contract.canonical_hash or "", contract_digest(contract)):
        raise RuntimeError("Synthetic proofability contract digest drifted.")
    return contract


def _scope(contract: POCContract) -> QualificationScopeV1:
    return create_qualification_scope(
        {
            "schema_version": "exitspec.qualification-scope.v1",
            "frozen_contract": {
                "contract_id": contract.id,
                "contract_canonical_digest": "sha256:" + contract.canonical_hash,
            },
            "workload": {
                "workload_id": "separate-workload-v1",
                "workload_digest": "sha256:" + "2" * 64,
            },
            "measurement_profile": {
                "environment_id": "separate-environment-v1",
                "environment_digest": "sha256:" + "4" * 64,
                "profile_id": "separate-profile-v1",
                "profile_version": "1.0.0",
                "profile_digest": "sha256:" + "5" * 64,
            },
            "evaluated_use": "CANARY_CONSIDERATION",
            "maximum_use": {"maximum_traffic_percent": 5},
            "freshness_policy": {
                "age_basis": "EVIDENCE_CAPTURED_AT",
                "maximum_evidence_age_seconds": 86_400,
            },
            "reference_subject_requirement": "NOT_REQUIRED",
            "reference_subject_digest": None,
        }
    )


def _build_authority() -> ProofabilityFixtureAuthority:
    subject = _subject()
    contract = _contract()
    scope = _scope(contract)
    context = create_qualification_context(
        subject,
        scope,
        protocol_id="inference-performance-qualification",
        protocol_version="1.0.0",
    )
    authority = ProofabilityFixtureAuthority(
        fixture_id=FIXTURE_ID,
        fixture_version=FIXTURE_VERSION,
        profile_id=PROFILE_ID,
        profile_version=PROFILE_VERSION,
        subject=subject,
        scope=scope,
        context=context,
        contract=contract,
        expected_subject_digest=(
            "sha256:2921dd76c90a5dd4a6131ef8bb7a369f7b4b1a3a829744751e6b38e81dfb988a"
        ),
        expected_scope_digest=(
            "sha256:d4a8896708a5849933b89b7f966cda1950715665667cc631bd6be63431f8c057"
        ),
        expected_qualification_context_digest=(
            "sha256:e5a6f155a33c4b0293a5e8bca523c4796bb5bad5cb9e5d7df4fd58443f7ffc3f"
        ),
        expected_contract_id="pr5-proofability-contract",
        expected_contract_canonical_digest=(
            "sha256:83d8047b37446e0ec3596e8dd9cb8c76ccff49d68ac4f189268bb3f5ad80f0ee"
        ),
        expected_profile_id=PROFILE_ID,
        expected_profile_version=PROFILE_VERSION,
        expected_capability_digest=(
            "sha256:1b8732d26a94dadfab984b43a4c67c1fc858ddf39f95ec496f5914f1c08e066b"
        ),
        expected_protocol_id="inference-performance-qualification",
        expected_protocol_version="1.0.0",
        expected_engine_id="vllm",
        expected_engine_version="0.26.0",
        expected_adapter_id="vllm_bench_serve",
        expected_adapter_version="1.0.0",
        expected_proofability_report_digest=(
            "sha256:28c49bba2dd3791905a201a74777c9994e6ecc083cc3b9de083095f4c626d81e"
        ),
        expected_canonical_report_byte_count=2_602,
    )
    actual_roots = (
        authority.subject.subject_digest,
        authority.scope.scope_digest,
        authority.context.qualification_context_digest,
        authority.contract.id,
        "sha256:" + (authority.contract.canonical_hash or ""),
    )
    expected_roots = (
        authority.expected_subject_digest,
        authority.expected_scope_digest,
        authority.expected_qualification_context_digest,
        authority.expected_contract_id,
        authority.expected_contract_canonical_digest,
    )
    if not all(
        hmac.compare_digest(actual, expected)
        for actual, expected in zip(actual_roots, expected_roots, strict=True)
    ):
        raise RuntimeError("Synthetic proofability fixture identity drifted.")
    return authority


PRODUCTION_FIXTURE_AUTHORITIES: Final = (_build_authority(),)
if len(PRODUCTION_FIXTURE_AUTHORITIES) != 1:  # pragma: no cover - fixed source
    raise RuntimeError("Production requires exactly one proofability fixture.")


def production_fixture_authority() -> ProofabilityFixtureAuthority:
    """Return the sole immutable package fixture without caller selection."""

    if len(PRODUCTION_FIXTURE_AUTHORITIES) != 1:
        raise RuntimeError("Production proofability fixture cardinality drifted.")
    return PRODUCTION_FIXTURE_AUTHORITIES[0]


__all__ = [
    "FIXTURE_ID",
    "FIXTURE_VERSION",
    "PRODUCTION_FIXTURE_AUTHORITIES",
    "PROFILE_ID",
    "PROFILE_VERSION",
    "ProofabilityFixtureAuthority",
    "production_fixture_authority",
]
