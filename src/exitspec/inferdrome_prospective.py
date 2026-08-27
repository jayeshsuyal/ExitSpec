"""Prospective, run-independent Inferdrome contract and handoff artifacts.

This module stops at the contract-to-producer handshake.  It does not import
Inferdrome, execute a provider, read a bundle, admit evidence, or issue a
verdict.  The existing retrospective Inferdrome V1/V3 path is deliberately not
used or modified here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import tempfile
from typing import Any, Final, Literal, Sequence

import yaml
from pydantic import Field, ValidationError, model_validator

from .canonical import canonical_json_bytes
from .confirmations import (
    ConfirmationDecision,
    ContractConfirmation,
    contract_confirmation_fingerprint,
    confirmation_matches_contract,
    record_confirmation,
    require_affirmative_confirmation,
)
from .contracts import freeze_confirmed_contract, verify_contract_digest
from .inferdrome_profile import MANAGED_PROFILE_ID, MANAGED_PROFILE_SHA256
from .models import (
    ContractStatus,
    ExternalErrorRateRuleV1,
    FrozenExitSpecModel,
    InferdromeEvidenceIdentityV2,
    InferencePerformanceCriterionV4,
    POCContract,
    ProspectiveCanonicalizationBindingV1,
    ProspectiveReliabilityPopulationV1,
    ProspectiveSamplingPolicyV1,
    ProspectiveTTFTP95RuleV2,
    ProspectiveTrafficPolicyV1,
    TargetSystem,
    WorkloadReference,
)
from .performance_serialization import (
    parse_confirmation,
    parse_contract,
    serialize_confirmation,
    serialize_contract,
)

PROSPECTIVE_CANONICALIZATION_SCHEME_ID: Final = "rfc8785_jcs_v1"
PROSPECTIVE_CANONICAL_BYTES_ENCODING: Final = "utf-8_rfc8785_jcs"
PROSPECTIVE_HASH_ALGORITHM_ID: Final = "sha256_v1"
PROSPECTIVE_HASH_ENCODING_ID: Final = "lowercase_hex_without_prefix"
PROSPECTIVE_LINK_DERIVATION_POLICY_ID: Final = (
    "exitspec.producer_link.sha256_canonical_hash.v1"
)
PROSPECTIVE_LINK_DERIVATION_INPUT: Final = "bare_canonical_hash"
PROSPECTIVE_LINK_DERIVATION_OPERATION: Final = "prefix_sha256_no_second_hash"
PROSPECTIVE_HANDOFF_SCHEMA_VERSION: Final = "exitspec.inferdrome-prospective-handoff.v1"
PROSPECTIVE_CONFIRMATION_IDENTITY_ASSURANCE: Final = (
    "PROCESS_LOCAL_DECLARED_IDENTITY_NOT_AUTHENTICATED"
)
PROSPECTIVE_WORKLOAD_ID: Final = "inferdrome.qwen2.5-real-gpu-workload.v1"
PROSPECTIVE_WORKLOAD_DIGEST: Final = (
    "sha256:22bf3389cc29ee946ae567870d7f8d7b458594224542a796e8990c15b1cfcd63"
)
PROSPECTIVE_TARGET_MODEL: Final = "Qwen/Qwen2.5-0.5B-Instruct"
PROSPECTIVE_TARGET_REVISION: Final = "7ae557604adf67be50417f59c2c2f167def9a775"
PROSPECTIVE_TARGET_ENDPOINT: Final = "http://127.0.0.1:18080/"
PROSPECTIVE_SOURCE_TARGET_ENDPOINT: Final = "http://127.0.0.1:18080"
PROSPECTIVE_TARGET_ENGINE: Final = "vllm"
PROSPECTIVE_TARGET_ENGINE_VERSION: Final = "0.26.0"
PROSPECTIVE_TARGET_API: Final = "openai_chat_completions"
PROSPECTIVE_PRODUCER_NAME: Final = "vllm"
PROSPECTIVE_PRODUCER_VERSION: Final = "0.26.0"
PROSPECTIVE_ADAPTER_ID: Final = "vllm_bench_serve"
PROSPECTIVE_ADAPTER_VERSION: Final = "1.0.0"
PROSPECTIVE_NATIVE_SCHEMA_FINGERPRINT: Final = (
    "sha256:3a4fdee6fe9b45ce5b42c41fd3bfc6614245a36ecfe6f94de92b59717a136abb"
)
PROSPECTIVE_LOCAL_GPU_PROOF_SCHEMA_ID: Final = "urn:inferdrome:local-gpu-proof:v1"
PROSPECTIVE_LOCAL_GPU_PROOF_SCHEMA_SHA256: Final = (
    "sha256:cf83bbdea2bba4c30b8f0e2c5f34f34a4077501207881fdbdab021571d665547"
)
PROSPECTIVE_EXPECTED_EXECUTION_FINGERPRINT: Final = (
    "sha256:76d984ea57a0e7cb00520255a6e362f22885d713a875195a7397771937060edd"
)
PROSPECTIVE_EXECUTION_MODE: Final = "attached_endpoint"
PROSPECTIVE_MAX_RUNTIME_SECONDS: Final = 900
PROSPECTIVE_MAX_MEASURED_REQUESTS: Final = 100
PROSPECTIVE_PRODUCED_EVIDENCE_METRIC_DEFINITION_ID: Final = (
    "vllm_first_choices_event_v0_26"
)
PROSPECTIVE_CHOICES_SPAN_DEFINITION_ID: Final = "last_choices_event_span_v1"
PROSPECTIVE_METRIC_DEFINITIONS_VERSION: Final = "1.0.0"
PROSPECTIVE_REDUCER_VERSION: Final = "1.0.0"
PROSPECTIVE_NATIVE_OUTPUT_SENSITIVITY: Final = "RESPONSE_CONTENT"
PROSPECTIVE_CANONICAL_RESPONSE_CONTENT: Final = "omit"
PROSPECTIVE_INCLUDE_REQUEST_PLAN: Final = True
PROSPECTIVE_WORKLOAD_PATH: Final = "real-gpu/workload.jsonl"
PROSPECTIVE_WORKLOAD_ARTIFACT_PATH: Final = "sources/real-gpu/workload.jsonl"
PROSPECTIVE_ARTIFACT_MAX_BYTES: Final = 8 * 1024 * 1024
PROSPECTIVE_PATH_MAX_LENGTH: Final = 512
PROSPECTIVE_TREE_MAX_FILES: Final = 32
PROSPECTIVE_TREE_MAX_BYTES: Final = 16 * 1024 * 1024
PROSPECTIVE_TREE_MAX_DEPTH: Final = 4
PROSPECTIVE_MAX_JSON_NODES: Final = 2_048
PROSPECTIVE_MAX_JSON_DEPTH: Final = 24
PROSPECTIVE_MAX_JSON_STRING_LENGTH: Final = 8_192
PROSPECTIVE_MAX_JSON_INTEGER: Final = 2_147_483_647
PROSPECTIVE_COMPLETION_MARKER: Final = ".complete"
_COMPLETION_MARKER_BYTES: Final = (
    b"exitspec.inferdrome-prospective-handoff.complete.v1\n"
)


@dataclass(frozen=True, slots=True)
class ProspectiveCaseSpec:
    """One of the three customer questions frozen before any capture."""

    case_id: str
    criterion_id: str
    title: str
    normalized_claim: str
    requested_criterion_metric_definition_id: str
    threshold_ns: int


PROSPECTIVE_CASES: Final[tuple[ProspectiveCaseSpec, ...]] = (
    ProspectiveCaseSpec(
        case_id="native-p95-under-20ms",
        criterion_id="INFERDROME-P1-NATIVE-P95-20MS",
        title="Native vLLM p95 TTFT below 20 ms",
        normalized_claim=(
            "For the pinned Qwen2.5 managed-vLLM workload, native vLLM p95 "
            "TTFT must be strictly below 20 ms at configured concurrency 4."
        ),
        requested_criterion_metric_definition_id="vllm_first_choices_event_v0_26",
        threshold_ns=20_000_000,
    ),
    ProspectiveCaseSpec(
        case_id="native-p95-under-10ms",
        criterion_id="INFERDROME-P1-NATIVE-P95-10MS",
        title="Native vLLM p95 TTFT below 10 ms",
        normalized_claim=(
            "For the pinned Qwen2.5 managed-vLLM workload, native vLLM p95 "
            "TTFT must be strictly below 10 ms at configured concurrency 4."
        ),
        requested_criterion_metric_definition_id="vllm_first_choices_event_v0_26",
        threshold_ns=10_000_000,
    ),
    ProspectiveCaseSpec(
        case_id="semantic-first-nonempty-under-20ms",
        criterion_id="INFERDROME-P1-SEMANTIC-FIRST-NONEMPTY-20MS",
        title="First non-empty content p95 below 20 ms",
        normalized_claim=(
            "For the pinned Qwen2.5 managed-vLLM workload, p95 time to first "
            "non-empty content must be strictly below 20 ms at configured "
            "concurrency 4."
        ),
        requested_criterion_metric_definition_id="first_nonempty_choices_delta_content_v1",
        threshold_ns=20_000_000,
    ),
)
_CASE_BY_ID: Final = {case.case_id: case for case in PROSPECTIVE_CASES}


class ProspectiveHandoffError(ValueError):
    """Prospective contract or handoff input is malformed or unsafe."""


@dataclass(frozen=True, slots=True)
class FrozenProspectiveCase:
    """One exact customer-confirmed frozen case, before producer capture."""

    case: ProspectiveCaseSpec
    contract: POCContract
    confirmation: ContractConfirmation

    @property
    def contract_canonical_hash(self) -> str:
        value = self.contract.canonical_hash
        if value is None:
            raise ProspectiveHandoffError("Prospective contract is not frozen.")
        return value

    @property
    def producer_contract_link(self) -> str:
        return derive_producer_contract_link(self.contract_canonical_hash)

    @property
    def contract_bytes(self) -> bytes:
        return serialize_contract(self.contract)

    @property
    def confirmation_bytes(self) -> bytes:
        return serialize_confirmation(self.confirmation)


@dataclass(frozen=True, slots=True)
class ProspectiveHandoffValidation:
    """Validated manifest and its exact byte digest."""

    manifest: "ProspectiveHandoffManifest"
    manifest_sha256: str


class ProspectiveHandoffCaseModel(FrozenExitSpecModel):
    """Strict artifact bindings for one prospective case."""

    case_id: Literal[
        "native-p95-under-20ms",
        "native-p95-under-10ms",
        "semantic-first-nonempty-under-20ms",
    ]
    contract_id: str = Field(pattern=r"^[a-z][a-z0-9-]{2,63}$")
    contract_version: str = Field(min_length=1, max_length=128)
    contract_artifact_path: str = Field(
        min_length=1, max_length=PROSPECTIVE_PATH_MAX_LENGTH
    )
    contract_artifact_sha256: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    contract_canonical_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    producer_contract_link: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    contract_confirmation_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    confirmation_artifact_path: str = Field(
        min_length=1, max_length=PROSPECTIVE_PATH_MAX_LENGTH
    )
    confirmation_id: str = Field(pattern=r"^cnf_[a-f0-9]{64}$")
    confirmation_record_sha256: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    source_yaml_artifact_path: str = Field(
        min_length=1, max_length=PROSPECTIVE_PATH_MAX_LENGTH
    )
    source_yaml_artifact_sha256: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    methodology: InferdromeEvidenceIdentityV2


class ProspectiveHandoffManifest(FrozenExitSpecModel):
    """Versioned, exact, post-freeze source-handoff manifest."""

    schema_version: Literal["exitspec.inferdrome-prospective-handoff.v1"]
    authority_boundary: Literal["EXIT_SPEC_CUSTOMER_CONFIRMED_HANDOFF_ONLY"]
    confirmation_identity_assurance: Literal[
        "PROCESS_LOCAL_DECLARED_IDENTITY_NOT_AUTHENTICATED"
    ]
    acceptance_verdict: Literal[None] = None
    canonicalization_scheme_id: Literal["rfc8785_jcs_v1"]
    hash_algorithm_id: Literal["sha256_v1"]
    link_derivation_policy_id: Literal[
        "exitspec.producer_link.sha256_canonical_hash.v1"
    ]
    workload_artifact_path: Literal["sources/real-gpu/workload.jsonl"]
    workload_artifact_sha256: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    completion_marker: Literal[".complete"]
    cases: tuple[ProspectiveHandoffCaseModel, ...] = Field(
        min_length=len(PROSPECTIVE_CASES), max_length=len(PROSPECTIVE_CASES)
    )

    @model_validator(mode="after")
    def require_canonical_case_order(self) -> "ProspectiveHandoffManifest":
        value = self
        if tuple(item.case_id for item in value.cases) != tuple(
            case.case_id for case in PROSPECTIVE_CASES
        ):
            raise ValueError("Prospective handoff cases must use canonical order.")
        if len({item.case_id for item in value.cases}) != len(PROSPECTIVE_CASES):
            raise ValueError("Prospective handoff cases must be unique.")
        return value


def _case_identity(case: ProspectiveCaseSpec) -> InferdromeEvidenceIdentityV2:
    return InferdromeEvidenceIdentityV2(
        schema_version="exitspec.inferdrome-evidence-identity.v2",
        case_id=case.case_id,
        evidence_schema_version="inferdrome.evidence.v1",
        sequence_requirement="OPERATOR_MUST_FREEZE_BEFORE_MEASUREMENT",
        chronology_assurance="UNAVAILABLE",
        producer_name=PROSPECTIVE_PRODUCER_NAME,
        producer_version=PROSPECTIVE_PRODUCER_VERSION,
        adapter_id=PROSPECTIVE_ADAPTER_ID,
        adapter_version=PROSPECTIVE_ADAPTER_VERSION,
        native_schema_fingerprint=PROSPECTIVE_NATIVE_SCHEMA_FINGERPRINT,
        managed_profile_id=MANAGED_PROFILE_ID,
        managed_profile_sha256=MANAGED_PROFILE_SHA256,
        local_gpu_proof_schema_id=PROSPECTIVE_LOCAL_GPU_PROOF_SCHEMA_ID,
        local_gpu_proof_schema_sha256=PROSPECTIVE_LOCAL_GPU_PROOF_SCHEMA_SHA256,
        target_engine=PROSPECTIVE_TARGET_ENGINE,
        target_engine_version=PROSPECTIVE_TARGET_ENGINE_VERSION,
        target_api=PROSPECTIVE_TARGET_API,
        target_model=PROSPECTIVE_TARGET_MODEL,
        target_model_revision=PROSPECTIVE_TARGET_REVISION,
        target_tokenizer_revision=PROSPECTIVE_TARGET_REVISION,
        target_endpoint=PROSPECTIVE_TARGET_ENDPOINT,
        workload_id=PROSPECTIVE_WORKLOAD_ID,
        workload_digest=PROSPECTIVE_WORKLOAD_DIGEST,
        source_schema_version="inferdrome.source-experiment.v1",
        traffic=ProspectiveTrafficPolicyV1(
            schema_version="exitspec.inferdrome-traffic.v1",
            policy_id="inferdrome.concurrent.vllm.v1",
            kind="concurrent",
            configured_concurrency=4,
            warmup_requests=10,
            measured_requests=100,
        ),
        sampling=ProspectiveSamplingPolicyV1(
            schema_version="exitspec.inferdrome-sampling.v1",
            policy_id="inferdrome.qwen2.5-deterministic.v1",
            prompt_content_policy="include",
            requested_output_tokens=32,
            temperature=0,
            seed=42,
        ),
        execution_mode=PROSPECTIVE_EXECUTION_MODE,
        max_runtime_seconds=PROSPECTIVE_MAX_RUNTIME_SECONDS,
        max_measured_requests=PROSPECTIVE_MAX_MEASURED_REQUESTS,
        measurement_streaming=True,
        produced_evidence_metric_definition_id=(
            PROSPECTIVE_PRODUCED_EVIDENCE_METRIC_DEFINITION_ID
        ),
        choices_span_definition_id=PROSPECTIVE_CHOICES_SPAN_DEFINITION_ID,
        metric_definitions_version=PROSPECTIVE_METRIC_DEFINITIONS_VERSION,
        reducer_version=PROSPECTIVE_REDUCER_VERSION,
        native_output_sensitivity=PROSPECTIVE_NATIVE_OUTPUT_SENSITIVITY,
        canonical_response_content=PROSPECTIVE_CANONICAL_RESPONSE_CONTENT,
        include_request_plan=PROSPECTIVE_INCLUDE_REQUEST_PLAN,
        expected_execution_fingerprint=PROSPECTIVE_EXPECTED_EXECUTION_FINGERPRINT,
        requested_criterion_metric_definition_id=(
            case.requested_criterion_metric_definition_id
        ),
        run_aggregation_policy="independent_single_run_no_pooling",
        reducer_id="nearest_rank_v1",
        latency_population="successful_measured_requests_with_observed_ttft",
        reliability_population=ProspectiveReliabilityPopulationV1(
            schema_version="exitspec.inferdrome-reliability-population.v1",
            population_id="exitspec.inferdrome-reliability.v1",
            operator="lt",
            threshold_basis_points=100,
            numerator="failed_or_anomalous_native_measured_requests",
            denominator="all_measured_requests",
            exact_attempts=100,
        ),
        claims_assurance="INTERNAL_CONSISTENCY_ONLY",
        canonicalization=ProspectiveCanonicalizationBindingV1(
            canonicalization_scheme_id=PROSPECTIVE_CANONICALIZATION_SCHEME_ID,
            canonical_bytes_encoding=PROSPECTIVE_CANONICAL_BYTES_ENCODING,
            hash_algorithm_id=PROSPECTIVE_HASH_ALGORITHM_ID,
            hash_encoding_id=PROSPECTIVE_HASH_ENCODING_ID,
            link_derivation_policy_id=PROSPECTIVE_LINK_DERIVATION_POLICY_ID,
            link_derivation_input=PROSPECTIVE_LINK_DERIVATION_INPUT,
            link_derivation_operation=PROSPECTIVE_LINK_DERIVATION_OPERATION,
        ),
    )


def _criterion(case: ProspectiveCaseSpec) -> InferencePerformanceCriterionV4:
    identity = _case_identity(case)
    return InferencePerformanceCriterionV4(
        criterion_type="inference_performance_v4",
        id=case.criterion_id,
        title=case.title,
        must_have=True,
        human_added=True,
        normalized_claim=case.normalized_claim,
        case_id=case.case_id,
        ttft_p95=ProspectiveTTFTP95RuleV2(
            schema_version="exitspec.inferdrome-ttft-p95.v2",
            metric="time_to_first_token",
            definition_id=case.requested_criterion_metric_definition_id,
            aggregation="p95",
            unit="nanoseconds",
            operator="lt",
            threshold_ns=case.threshold_ns,
            reducer_id="nearest_rank_v1",
            population="successful_measured_requests_with_observed_ttft",
            minimum_successful_samples=100,
            equality_outcome="FAIL",
            must_pass=True,
        ),
        error_rate=ExternalErrorRateRuleV1(
            metric="error_rate",
            aggregation="rate",
            operator="lt",
            threshold_basis_points=100,
            numerator="failed_or_anomalous_native_measured_requests",
            denominator="all_measured_requests",
            exact_attempts=100,
            must_pass=True,
        ),
        evidence_identity=identity,
        concurrency_semantics="configured_maximum_concurrency_not_observed_overlap",
        owner="exitspec-p1-reviewer",
        evidence_policy=(
            "A later producer capture may be linked only after this exact "
            "customer-confirmed contract is frozen; ExitSpec independently "
            "validates any later evidence."
        ),
        approved=True,
    )


def build_prospective_approved_contract(
    case_id: str,
    *,
    created_at: datetime,
) -> POCContract:
    """Build one approved, run-independent V4 contract before confirmation."""

    case = _CASE_BY_ID.get(case_id)
    if case is None:
        raise ProspectiveHandoffError("Unknown prospective case.")
    if (
        type(created_at) is not datetime
        or created_at.tzinfo is None
        or created_at.utcoffset() is None
    ):
        raise ProspectiveHandoffError("created_at must be timezone-aware.")
    return POCContract(
        id=f"inferdrome-p1-{case.case_id}",
        version="1.0.0",
        status=ContractStatus.APPROVED,
        created_at=created_at,
        approved_at=created_at,
        customer="prospective-inferdrome-customer",
        use_case="Prospectively qualify one exact managed Inferdrome case.",
        target_system=TargetSystem(
            provider="inferdrome-managed-vllm",
            endpoint_class="retained-loopback-vllm-benchmark",
            model=PROSPECTIVE_TARGET_MODEL,
        ),
        workload=WorkloadReference(
            fixture_path=PROSPECTIVE_WORKLOAD_PATH,
            sha256=PROSPECTIVE_WORKLOAD_DIGEST.removeprefix("sha256:"),
        ),
        criteria=(_criterion(case),),
        owners=("exitspec-p1-reviewer",),
        non_goals=(
            "No GPU or provider execution is authorized by this contract.",
            "No run, request plan, bundle, observed measurement, receipt, or verdict is present before capture.",
            "A later evidence evaluation is a separate purpose-bound operation.",
        ),
        evidence_retention_policy=(
            "Retain the exact frozen contract, confirmation artifact, synthetic "
            "workload bytes, and post-freeze source handoff metadata until a "
            "separately authorized capture path exists."
        ),
    )


def confirmation_idempotency_key(case_id: str) -> str:
    """Return the fixed non-secret operation key used by checked-in artifacts."""

    if case_id not in _CASE_BY_ID:
        raise ProspectiveHandoffError("Unknown prospective case.")
    return f"inferdrome-p1-{case_id}-confirmation-v1"


def _require_case_spec(case: ProspectiveCaseSpec) -> ProspectiveCaseSpec:
    if type(case) is not ProspectiveCaseSpec:
        raise ProspectiveHandoffError("Unknown prospective case specification.")
    expected = _CASE_BY_ID.get(case.case_id)
    if expected is None or case != expected:
        raise ProspectiveHandoffError("Prospective case specification is not pinned.")
    return expected


def _require_ordered_timestamps(
    created_at: datetime,
    decided_at: datetime,
    frozen_at: datetime,
) -> None:
    values = (created_at, decided_at, frozen_at)
    if any(
        type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None
        for value in values
    ):
        raise ProspectiveHandoffError(
            "Prospective lifecycle timestamps must be timezone-aware."
        )
    if not created_at <= decided_at <= frozen_at:
        raise ProspectiveHandoffError(
            "Prospective lifecycle timestamps must satisfy created_at <= decided_at <= frozen_at."
        )


def freeze_prospective_case(
    case_id: str,
    *,
    created_at: datetime,
    confirmer_identity: str,
    decided_at: datetime,
    frozen_at: datetime,
) -> FrozenProspectiveCase:
    """Require affirmative customer confirmation before freezing one case."""

    _require_ordered_timestamps(created_at, decided_at, frozen_at)
    approved = build_prospective_approved_contract(case_id, created_at=created_at)
    confirmation = record_confirmation(
        approved,
        confirmer_identity=confirmer_identity,
        decision=ConfirmationDecision.CONFIRM,
        agreement_acknowledged=True,
        rationale=(
            "I confirm this exact prospective managed-Inferdrome case, target, "
            "workload, and methodology."
        ),
        idempotency_key=confirmation_idempotency_key(case_id),
        decided_at=decided_at,
    )
    frozen = freeze_confirmed_contract(approved, confirmation, frozen_at)
    result = FrozenProspectiveCase(
        case=_CASE_BY_ID[case_id],
        contract=frozen,
        confirmation=confirmation,
    )
    _assert_frozen_case(result)
    return result


def derive_producer_contract_link(
    canonical_hash: str,
    *,
    link_derivation_policy_id: str = PROSPECTIVE_LINK_DERIVATION_POLICY_ID,
) -> str:
    """Derive the producer's prefixed link from a bare frozen ExitSpec hash."""

    if link_derivation_policy_id != PROSPECTIVE_LINK_DERIVATION_POLICY_ID:
        raise ProspectiveHandoffError("Unsupported producer-link policy.")
    if (
        type(canonical_hash) is not str
        or len(canonical_hash) != 64
        or any(character not in "0123456789abcdef" for character in canonical_hash)
    ):
        raise ProspectiveHandoffError(
            "Producer-link derivation requires a bare lowercase SHA-256 hash."
        )
    return f"sha256:{canonical_hash}"


def _assert_frozen_case(value: FrozenProspectiveCase) -> None:
    _require_case_spec(value.case)
    if (
        type(value.contract) is not POCContract
        or value.contract.status is not ContractStatus.FROZEN
        or not verify_contract_digest(value.contract)
        or not confirmation_matches_contract(value.contract, value.confirmation)
    ):
        raise ProspectiveHandoffError(
            "Handoff emission requires a digest-valid affirmative frozen contract."
        )
    if (
        len(value.contract.criteria) != 1
        or type(value.contract.criteria[0]) is not InferencePerformanceCriterionV4
        or value.contract.criteria[0].case_id != value.case.case_id
        or value.contract.criteria[0] != _criterion(value.case)
    ):
        raise ProspectiveHandoffError("Handoff emission requires one exact V4 case.")
    if value.contract.confirmation_id != value.confirmation.confirmation_id:
        raise ProspectiveHandoffError(
            "Frozen contract confirmation binding is invalid."
        )
    if value.contract.frozen_at is None or not (
        value.contract.created_at
        <= value.confirmation.decided_at
        <= value.contract.frozen_at
    ):
        raise ProspectiveHandoffError("Prospective lifecycle chronology is invalid.")
    require_affirmative_confirmation(value.contract, value.confirmation)


def _source_document(case: ProspectiveCaseSpec, producer_link: str) -> dict[str, Any]:
    """Return the exact Inferdrome #36 source document for one case."""

    return {
        "schema_version": "inferdrome.source-experiment.v1",
        "experiment": {
            "id": f"inferdrome-p1-{case.case_id}",
            "title": "Pinned Qwen2.5 0.5B managed-vLLM real-GPU proof",
            "hypothesis": "A clean NVIDIA host can reproduce one sealed Inferdrome bundle.",
        },
        "execution": {
            "mode": PROSPECTIVE_EXECUTION_MODE,
            "max_runtime_seconds": PROSPECTIVE_MAX_RUNTIME_SECONDS,
            "max_measured_requests": PROSPECTIVE_MAX_MEASURED_REQUESTS,
        },
        "target": {
            "engine": PROSPECTIVE_TARGET_ENGINE,
            "endpoint": PROSPECTIVE_SOURCE_TARGET_ENDPOINT,
            "model": PROSPECTIVE_TARGET_MODEL,
            "model_revision": PROSPECTIVE_TARGET_REVISION,
            "tokenizer_revision": PROSPECTIVE_TARGET_REVISION,
            "engine_version": PROSPECTIVE_TARGET_ENGINE_VERSION,
        },
        "workload": {
            "path": PROSPECTIVE_WORKLOAD_PATH,
            "sha256": PROSPECTIVE_WORKLOAD_DIGEST,
            "prompt_content_policy": "include",
            "requested_output_tokens": 32,
            "temperature": 0,
            "seed": 42,
        },
        "traffic": {
            "kind": "concurrent",
            "concurrency": 4,
            "warmup_requests": 10,
            "measured_requests": 100,
        },
        "evidence": {
            "canonical_response_content": PROSPECTIVE_CANONICAL_RESPONSE_CONTENT,
        },
        "links": {"exitspec_contract_digest": producer_link},
    }


def source_yaml_bytes(case: ProspectiveCaseSpec, producer_link: str) -> bytes:
    """Serialize one source handoff without any future capture identity."""

    _require_case_spec(case)
    if (
        type(producer_link) is not str
        or len(producer_link) != 71
        or not producer_link.startswith("sha256:")
    ):
        raise ProspectiveHandoffError(
            "Source handoff link is not a derived producer link."
        )
    try:
        expected_link = derive_producer_contract_link(producer_link[7:])
    except ProspectiveHandoffError:
        raise ProspectiveHandoffError(
            "Source handoff link is not a derived producer link."
        ) from None
    if producer_link != expected_link:
        raise ProspectiveHandoffError(
            "Source handoff link is not a derived producer link."
        )
    return yaml.safe_dump(
        _source_document(case, producer_link),
        allow_unicode=True,
        sort_keys=False,
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_tagged(value: bytes) -> str:
    return f"sha256:{_sha256(value)}"


def _strict_json_bytes(content: bytes, *, label: str) -> dict[str, Any]:
    if (
        type(content) is not bytes
        or not 0 < len(content) <= PROSPECTIVE_ARTIFACT_MAX_BYTES
    ):
        raise ProspectiveHandoffError(f"{label} is outside its byte limit.")

    def unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ProspectiveHandoffError(f"{label} contains a duplicate key.")
            result[key] = value
        return result

    def reject_constant(value: str) -> None:
        raise ProspectiveHandoffError(f"{label} contains a non-finite number: {value}.")

    def bounded_integer(value: str) -> int:
        parsed = int(value)
        if abs(parsed) > PROSPECTIVE_MAX_JSON_INTEGER:
            raise ProspectiveHandoffError(f"{label} contains an unbounded integer.")
        return parsed

    def reject_float(value: str) -> None:
        raise ProspectiveHandoffError(f"{label} contains a floating-point number.")

    try:
        parsed = json.loads(
            content.decode("utf-8"),
            object_pairs_hook=unique_pairs,
            parse_constant=reject_constant,
            parse_int=bounded_integer,
            parse_float=reject_float,
        )
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ProspectiveHandoffError(f"{label} is not strict JSON.") from None
    if not isinstance(parsed, dict):
        raise ProspectiveHandoffError(f"{label} must be a JSON object.")

    nodes = 0

    def walk(value: Any, depth: int) -> None:
        nonlocal nodes
        nodes += 1
        if nodes > PROSPECTIVE_MAX_JSON_NODES:
            raise ProspectiveHandoffError(f"{label} exceeds its node limit.")
        if depth > PROSPECTIVE_MAX_JSON_DEPTH:
            raise ProspectiveHandoffError(f"{label} exceeds its nesting limit.")
        if isinstance(value, str) and len(value) > PROSPECTIVE_MAX_JSON_STRING_LENGTH:
            raise ProspectiveHandoffError(f"{label} contains an oversized string.")
        if isinstance(value, dict):
            for key, child in value.items():
                if len(key) > PROSPECTIVE_MAX_JSON_STRING_LENGTH:
                    raise ProspectiveHandoffError(f"{label} contains an oversized key.")
                walk(child, depth + 1)
        elif isinstance(value, list):
            for child in value:
                walk(child, depth + 1)

    walk(parsed, 0)
    return parsed


def _file_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mode,
        metadata.st_nlink,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _read_regular(path: Path, *, label: str) -> bytes:
    descriptor: int | None = None
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        metadata = os.fstat(descriptor)
        identity = _file_identity(metadata)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_size > PROSPECTIVE_ARTIFACT_MAX_BYTES
        ):
            raise OSError
        chunks: list[bytes] = []
        total = 0
        while total <= PROSPECTIVE_ARTIFACT_MAX_BYTES:
            chunk = os.read(
                descriptor,
                min(65_536, PROSPECTIVE_ARTIFACT_MAX_BYTES + 1 - total),
            )
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        content = b"".join(chunks)
        final_metadata = os.fstat(descriptor)
        if (
            _file_identity(final_metadata) != identity
            or len(content) > PROSPECTIVE_ARTIFACT_MAX_BYTES
        ):
            raise OSError
        path_metadata = os.lstat(path)
        if _file_identity(path_metadata) != identity:
            raise OSError
        return content
    except (OSError, ValueError):
        raise ProspectiveHandoffError(f"{label} is unavailable or unsafe.") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _safe_relative_path(root: Path, relative: str, *, label: str) -> Path:
    if (
        type(relative) is not str
        or not relative
        or len(relative) > PROSPECTIVE_PATH_MAX_LENGTH
        or "\x00" in relative
        or "\\" in relative
    ):
        raise ProspectiveHandoffError(f"{label} is not a safe relative path.")
    parsed = PurePosixPath(relative)
    if (
        parsed.is_absolute()
        or not parsed.parts
        or parsed.as_posix() != relative
        or any(part in {"", ".", ".."} for part in parsed.parts)
    ):
        raise ProspectiveHandoffError(f"{label} is not a safe relative path.")
    candidate = root.joinpath(*parsed.parts)
    current = root
    for part in parsed.parts:
        current = current / part
        try:
            if current.is_symlink():
                raise ProspectiveHandoffError(f"{label} contains a symlink.")
        except OSError:
            raise ProspectiveHandoffError(
                f"{label} is unavailable or unsafe."
            ) from None
    try:
        candidate.resolve(strict=True).relative_to(root.resolve(strict=True))
    except (OSError, ValueError):
        raise ProspectiveHandoffError(f"{label} escapes the handoff root.") from None
    return candidate


def _read_json(path: Path, *, label: str) -> tuple[dict[str, Any], bytes]:
    content = _read_regular(path, label=label)
    return _strict_json_bytes(content, label=label), content


def _manifest_case(case: FrozenProspectiveCase) -> ProspectiveHandoffCaseModel:
    contract_bytes = case.contract_bytes
    confirmation_bytes = case.confirmation_bytes
    source_bytes = source_yaml_bytes(case.case, case.producer_contract_link)
    return ProspectiveHandoffCaseModel(
        case_id=case.case.case_id,
        contract_id=case.contract.id,
        contract_version=case.contract.version,
        contract_artifact_path=f"contracts/{case.case.case_id}.frozen.json",
        contract_artifact_sha256=_sha256_tagged(contract_bytes),
        contract_canonical_hash=case.contract_canonical_hash,
        producer_contract_link=case.producer_contract_link,
        contract_confirmation_fingerprint=contract_confirmation_fingerprint(
            case.contract
        ),
        confirmation_artifact_path=(
            f"confirmations/{case.case.case_id}.confirmation.json"
        ),
        confirmation_id=case.confirmation.confirmation_id,
        confirmation_record_sha256=_sha256_tagged(confirmation_bytes),
        source_yaml_artifact_path=(f"sources/{case.case.case_id}.yaml"),
        source_yaml_artifact_sha256=_sha256_tagged(source_bytes),
        methodology=case.contract.criteria[0].evidence_identity,  # type: ignore[union-attr]
    )


def _manifest(cases: Sequence[FrozenProspectiveCase]) -> ProspectiveHandoffManifest:
    if tuple(item.case.case_id for item in cases) != tuple(
        case.case_id for case in PROSPECTIVE_CASES
    ):
        raise ProspectiveHandoffError(
            "Handoff requires the exact three cases in order."
        )
    if len({item.contract_canonical_hash for item in cases}) != len(PROSPECTIVE_CASES):
        raise ProspectiveHandoffError("Prospective contract hashes must be distinct.")
    producer_links = [item.producer_contract_link for item in cases]
    if len(set(producer_links)) != len(PROSPECTIVE_CASES):
        raise ProspectiveHandoffError("Prospective producer links must be distinct.")
    confirmation_ids = [item.confirmation.confirmation_id for item in cases]
    if len(set(confirmation_ids)) != len(PROSPECTIVE_CASES):
        raise ProspectiveHandoffError("Prospective confirmation IDs must be distinct.")
    confirmation_digests = [_sha256_tagged(item.confirmation_bytes) for item in cases]
    if len(set(confirmation_digests)) != len(PROSPECTIVE_CASES):
        raise ProspectiveHandoffError(
            "Prospective confirmation-record digests must be distinct."
        )
    return ProspectiveHandoffManifest(
        schema_version=PROSPECTIVE_HANDOFF_SCHEMA_VERSION,
        authority_boundary="EXIT_SPEC_CUSTOMER_CONFIRMED_HANDOFF_ONLY",
        confirmation_identity_assurance=PROSPECTIVE_CONFIRMATION_IDENTITY_ASSURANCE,
        acceptance_verdict=None,
        canonicalization_scheme_id=PROSPECTIVE_CANONICALIZATION_SCHEME_ID,
        hash_algorithm_id=PROSPECTIVE_HASH_ALGORITHM_ID,
        link_derivation_policy_id=PROSPECTIVE_LINK_DERIVATION_POLICY_ID,
        workload_artifact_path=PROSPECTIVE_WORKLOAD_ARTIFACT_PATH,
        workload_artifact_sha256=PROSPECTIVE_WORKLOAD_DIGEST,
        completion_marker=PROSPECTIVE_COMPLETION_MARKER,
        cases=tuple(_manifest_case(item) for item in cases),
    )


def _write_new(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
            0o400,
        )
    except OSError:
        raise ProspectiveHandoffError(
            f"Refusing to overwrite handoff artifact: {path.name}."
        ) from None
    try:
        view = memoryview(content)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError
            view = view[written:]
        os.fsync(descriptor)
    except OSError:
        raise ProspectiveHandoffError("Handoff artifact write failed.") from None
    finally:
        os.close(descriptor)


def _validate_closed_tree(
    root: Path,
    manifest: ProspectiveHandoffManifest,
) -> dict[str, tuple[int, ...]]:
    """Reject anything outside the manifest's fixed, complete artifact tree."""

    expected_files = {
        "handoff-manifest.json",
        PROSPECTIVE_COMPLETION_MARKER,
        manifest.workload_artifact_path,
    }
    expected_dirs = {"contracts", "confirmations", "sources", "sources/real-gpu"}
    expected_files.update(item.contract_artifact_path for item in manifest.cases)
    expected_files.update(item.confirmation_artifact_path for item in manifest.cases)
    expected_files.update(item.source_yaml_artifact_path for item in manifest.cases)

    try:
        root_metadata = root.lstat()
        if not stat.S_ISDIR(root_metadata.st_mode) or root.is_symlink():
            raise OSError
    except OSError:
        raise ProspectiveHandoffError(
            "Handoff root is unavailable or unsafe."
        ) from None

    seen_files: set[str] = set()
    seen_dirs: set[str] = set()
    file_identities: dict[str, tuple[int, ...]] = {}
    total_bytes = 0

    def walk(directory: Path, relative: str, depth: int) -> None:
        nonlocal total_bytes
        if depth > PROSPECTIVE_TREE_MAX_DEPTH:
            raise ProspectiveHandoffError("Handoff tree exceeds its depth limit.")
        try:
            entries = os.scandir(directory)
        except OSError:
            raise ProspectiveHandoffError(
                "Handoff tree is unavailable or unsafe."
            ) from None
        try:
            for entry in entries:
                child_relative = f"{relative}/{entry.name}" if relative else entry.name
                if (
                    len(PurePosixPath(child_relative).parts)
                    > PROSPECTIVE_TREE_MAX_DEPTH
                ):
                    raise ProspectiveHandoffError(
                        "Handoff tree exceeds its depth limit."
                    )
                try:
                    if entry.is_symlink():
                        raise OSError
                    if entry.is_dir(follow_symlinks=False):
                        if child_relative not in expected_dirs:
                            raise OSError
                        seen_dirs.add(child_relative)
                        walk(Path(entry.path), child_relative, depth + 1)
                        continue
                    metadata = entry.stat(follow_symlinks=False)
                    if (
                        not stat.S_ISREG(metadata.st_mode)
                        or metadata.st_nlink != 1
                        or metadata.st_size > PROSPECTIVE_ARTIFACT_MAX_BYTES
                        or child_relative not in expected_files
                    ):
                        raise OSError
                    seen_files.add(child_relative)
                    file_identities[child_relative] = _file_identity(metadata)
                    if len(seen_files) > PROSPECTIVE_TREE_MAX_FILES:
                        raise OSError
                    total_bytes += metadata.st_size
                    if total_bytes > PROSPECTIVE_TREE_MAX_BYTES:
                        raise OSError
                except OSError:
                    raise ProspectiveHandoffError(
                        f"Handoff tree contains an undeclared or unsafe entry: {child_relative}."
                    ) from None
        finally:
            entries.close()

    walk(root, "", 0)
    if seen_files != expected_files or seen_dirs != expected_dirs:
        raise ProspectiveHandoffError("Handoff tree inventory is incomplete.")
    marker = _read_regular(
        root / PROSPECTIVE_COMPLETION_MARKER,
        label="handoff completion marker",
    )
    if marker != _COMPLETION_MARKER_BYTES:
        raise ProspectiveHandoffError("Handoff completion marker is invalid.")
    return file_identities


def _move_new_file(source: Path, destination: Path) -> None:
    """Move one staged regular file without replacing a destination."""

    try:
        os.link(source, destination, follow_symlinks=False)
        os.unlink(source)
    except OSError:
        raise ProspectiveHandoffError(
            "Refusing to replace or publish an existing handoff artifact."
        ) from None


def _acquire_publish_lock(root: Path) -> tuple[Path, int]:
    lock_path = root.parent / f".{root.name}.publish.lock"
    try:
        descriptor = os.open(
            lock_path,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
    except OSError:
        raise ProspectiveHandoffError(
            "Another handoff publication is active or its lock is stale."
        ) from None
    return lock_path, descriptor


def _publish_staged(staging: Path, root: Path) -> None:
    """Publish with mkdir-excl and a marker moved last.

    A portable atomic directory no-replace operation is not available on every
    supported host.  This protocol reserves the destination with mkdir
    ``exist_ok=False``, moves files using no-replace hard links, and moves the
    completion marker last.  Readers reject any tree without that marker.
    """

    lock_path, lock_descriptor = _acquire_publish_lock(root)
    published = False
    try:
        if root.exists() or root.is_symlink():
            raise ProspectiveHandoffError(
                "Refusing to overwrite or replace an existing handoff root."
            )
        try:
            root.mkdir(mode=0o700, exist_ok=False)
            for directory in (
                "contracts",
                "confirmations",
                "sources",
                "sources/real-gpu",
            ):
                (root / directory).mkdir(mode=0o700, exist_ok=False)
            files = [
                "handoff-manifest.json",
                *[
                    f"contracts/{case.case_id}.frozen.json"
                    for case in PROSPECTIVE_CASES
                ],
                *[
                    f"confirmations/{case.case_id}.confirmation.json"
                    for case in PROSPECTIVE_CASES
                ],
                *[f"sources/{case.case_id}.yaml" for case in PROSPECTIVE_CASES],
                PROSPECTIVE_WORKLOAD_ARTIFACT_PATH,
            ]
            for relative in files:
                _move_new_file(staging / relative, root / relative)
            _move_new_file(
                staging / PROSPECTIVE_COMPLETION_MARKER,
                root / PROSPECTIVE_COMPLETION_MARKER,
            )
        except OSError:
            raise ProspectiveHandoffError(
                "Handoff publication failed without replacing its destination."
            ) from None
        try:
            validate_prospective_handoff(root)
        except ProspectiveHandoffError:
            try:
                os.unlink(root / PROSPECTIVE_COMPLETION_MARKER)
            except OSError:
                pass
            raise
        published = True
    finally:
        os.close(lock_descriptor)
        try:
            os.unlink(lock_path)
        except OSError:
            pass
    if not published:
        raise ProspectiveHandoffError("Handoff publication did not complete.")


def materialize_prospective_handoff(
    root: Path,
    cases: Sequence[FrozenProspectiveCase],
    *,
    workload_bytes: bytes,
) -> ProspectiveHandoffValidation:
    """Emit contracts, confirmations, source YAMLs, workload, and manifest.

    Every contract and confirmation is validated before any artifact is
    written.  The workload is copied as a fixture for the read-only Inferdrome
    checker; it is not evidence and does not authorize execution.
    """

    if not isinstance(root, Path) or root.is_symlink() or root.exists():
        raise ProspectiveHandoffError("Handoff root is unavailable or unsafe.")
    if not root.parent.is_dir() or root.parent.is_symlink():
        raise ProspectiveHandoffError("Handoff parent is unavailable or unsafe.")
    if type(workload_bytes) is not bytes or not workload_bytes:
        raise ProspectiveHandoffError("Handoff workload bytes must be non-empty.")
    if _sha256(workload_bytes) != PROSPECTIVE_WORKLOAD_DIGEST.removeprefix("sha256:"):
        raise ProspectiveHandoffError("Handoff workload bytes do not match the pin.")
    normalized = tuple(cases)
    for case in normalized:
        _assert_frozen_case(case)
    staging: Path | None = Path(
        tempfile.mkdtemp(prefix=f".{root.name}.staging-", dir=str(root.parent))
    )
    try:
        manifest = _manifest(normalized)
        for case in normalized:
            _write_new(
                staging / "contracts" / f"{case.case.case_id}.frozen.json",
                case.contract_bytes,
            )
            _write_new(
                staging / "confirmations" / f"{case.case.case_id}.confirmation.json",
                case.confirmation_bytes,
            )
            _write_new(
                staging / "sources" / f"{case.case.case_id}.yaml",
                source_yaml_bytes(case.case, case.producer_contract_link),
            )
        _write_new(staging / "sources" / "real-gpu" / "workload.jsonl", workload_bytes)
        manifest_bytes = canonical_json_bytes(manifest.model_dump(mode="json"))
        manifest_path = staging / "handoff-manifest.json"
        _write_new(manifest_path, manifest_bytes)
        _write_new(staging / PROSPECTIVE_COMPLETION_MARKER, _COMPLETION_MARKER_BYTES)
        validate_prospective_handoff(manifest_path)
        _publish_staged(staging, root)
        shutil.rmtree(staging)
        staging = None
        return validate_prospective_handoff(root)
    finally:
        if staging is not None and staging.exists():
            shutil.rmtree(staging)


def _require_manifest_fields(manifest: ProspectiveHandoffManifest) -> None:
    if (
        manifest.schema_version != PROSPECTIVE_HANDOFF_SCHEMA_VERSION
        or manifest.authority_boundary != "EXIT_SPEC_CUSTOMER_CONFIRMED_HANDOFF_ONLY"
        or manifest.confirmation_identity_assurance
        != PROSPECTIVE_CONFIRMATION_IDENTITY_ASSURANCE
        or manifest.acceptance_verdict is not None
        or manifest.canonicalization_scheme_id != PROSPECTIVE_CANONICALIZATION_SCHEME_ID
        or manifest.hash_algorithm_id != PROSPECTIVE_HASH_ALGORITHM_ID
        or manifest.link_derivation_policy_id != PROSPECTIVE_LINK_DERIVATION_POLICY_ID
        or manifest.workload_artifact_path != PROSPECTIVE_WORKLOAD_ARTIFACT_PATH
        or manifest.workload_artifact_sha256 != PROSPECTIVE_WORKLOAD_DIGEST
        or manifest.completion_marker != PROSPECTIVE_COMPLETION_MARKER
    ):
        raise ProspectiveHandoffError(
            "Prospective handoff manifest has an unsupported shape."
        )
    if tuple(item.case_id for item in manifest.cases) != tuple(
        case.case_id for case in PROSPECTIVE_CASES
    ):
        raise ProspectiveHandoffError("Prospective handoff case order is invalid.")
    if (
        manifest.workload_artifact_path != PROSPECTIVE_WORKLOAD_ARTIFACT_PATH
        or manifest.completion_marker != PROSPECTIVE_COMPLETION_MARKER
    ):
        raise ProspectiveHandoffError(
            "Prospective handoff inventory paths are invalid."
        )
    for item, case in zip(manifest.cases, PROSPECTIVE_CASES, strict=True):
        expected_paths = (
            f"contracts/{case.case_id}.frozen.json",
            f"confirmations/{case.case_id}.confirmation.json",
            f"sources/{case.case_id}.yaml",
        )
        actual_paths = (
            item.contract_artifact_path,
            item.confirmation_artifact_path,
            item.source_yaml_artifact_path,
        )
        if actual_paths != expected_paths:
            raise ProspectiveHandoffError(
                f"{case.case_id} handoff inventory paths are invalid."
            )
    for label, values in (
        ("contract hashes", [item.contract_canonical_hash for item in manifest.cases]),
        ("producer links", [item.producer_contract_link for item in manifest.cases]),
        ("confirmation IDs", [item.confirmation_id for item in manifest.cases]),
        (
            "confirmation-record digests",
            [item.confirmation_record_sha256 for item in manifest.cases],
        ),
    ):
        if len(set(values)) != len(PROSPECTIVE_CASES):
            raise ProspectiveHandoffError(
                f"Prospective {label} must be pairwise distinct."
            )


def _load_manifest(
    path: Path,
) -> tuple[ProspectiveHandoffManifest, bytes, dict[str, tuple[int, ...]]]:
    payload, content = _read_json(path, label="prospective handoff manifest")
    # ``model_validate(..., strict=True)`` intentionally does not coerce a
    # Python list into a tuple, while JSON arrays are the wire representation
    # of the frozen tuple.  Convert only this already-parsed top-level array;
    # all scalar types and nested fields remain strict.
    if isinstance(payload.get("cases"), list):
        payload["cases"] = tuple(payload["cases"])
    try:
        manifest = ProspectiveHandoffManifest.model_validate(payload, strict=True)
    except ValidationError as error:
        raise ProspectiveHandoffError(
            "Prospective handoff manifest failed strict validation."
        ) from error
    _require_manifest_fields(manifest)
    initial_file_identities = _validate_closed_tree(path.parent, manifest)
    if canonical_json_bytes(manifest.model_dump(mode="json")) != content:
        raise ProspectiveHandoffError(
            "Prospective handoff manifest is not canonical JSON."
        )
    return manifest, content, initial_file_identities


def _validate_source(path: Path, case: ProspectiveCaseSpec, producer_link: str) -> None:
    content = _read_regular(path, label=f"{case.case_id} source YAML")
    try:
        document = yaml.safe_load(content)
    except yaml.YAMLError:
        raise ProspectiveHandoffError(
            f"{case.case_id} source YAML is invalid."
        ) from None
    if document != _source_document(case, producer_link):
        raise ProspectiveHandoffError(
            f"{case.case_id} source YAML has methodology, link, or field drift."
        )
    workload_path = _safe_relative_path(
        path.parent,
        PROSPECTIVE_WORKLOAD_PATH,
        label=f"{case.case_id} workload path",
    )
    workload = _read_regular(workload_path, label=f"{case.case_id} workload")
    if _sha256(workload) != PROSPECTIVE_WORKLOAD_DIGEST.removeprefix("sha256:"):
        raise ProspectiveHandoffError(f"{case.case_id} workload digest is invalid.")


def validate_prospective_handoff(path: Path) -> ProspectiveHandoffValidation:
    """Strictly validate the complete post-freeze handoff artifact set."""

    manifest_path = path / "handoff-manifest.json" if path.is_dir() else path
    root = manifest_path.parent
    manifest, manifest_bytes, initial_file_identities = _load_manifest(manifest_path)
    workload_path = _safe_relative_path(
        root,
        manifest.workload_artifact_path,
        label="manifest workload artifact",
    )
    workload_bytes = _read_regular(workload_path, label="manifest workload artifact")
    if _sha256_tagged(workload_bytes) != manifest.workload_artifact_sha256:
        raise ProspectiveHandoffError("Manifest workload artifact digest mismatches.")
    for item in manifest.cases:
        case = _CASE_BY_ID[item.case_id]
        expected_identity = _case_identity(case)
        if item.methodology != expected_identity:
            raise ProspectiveHandoffError(
                f"{case.case_id} methodology identity drifted."
            )
        if item.producer_contract_link != derive_producer_contract_link(
            item.contract_canonical_hash,
            link_derivation_policy_id=manifest.link_derivation_policy_id,
        ):
            raise ProspectiveHandoffError(f"{case.case_id} producer link is invalid.")
        contract_path = _safe_relative_path(
            root,
            item.contract_artifact_path,
            label=f"{case.case_id} contract artifact",
        )
        contract_bytes = _read_regular(
            contract_path,
            label=f"{case.case_id} contract artifact",
        )
        if _sha256_tagged(contract_bytes) != item.contract_artifact_sha256:
            raise ProspectiveHandoffError(
                f"{case.case_id} contract artifact digest mismatches."
            )
        try:
            _strict_json_bytes(
                contract_bytes, label=f"{case.case_id} contract artifact"
            )
            contract = parse_contract(contract_bytes)
        except (ValidationError, ValueError) as error:
            raise ProspectiveHandoffError(
                f"{case.case_id} contract artifact failed strict validation."
            ) from error
        if (
            serialize_contract(contract) != contract_bytes
            or contract.status is not ContractStatus.FROZEN
            or not verify_contract_digest(contract)
            or contract.canonical_hash != item.contract_canonical_hash
            or len(contract.criteria) != 1
            or type(contract.criteria[0]) is not InferencePerformanceCriterionV4
            or contract.criteria[0].case_id != case.case_id
            or contract.criteria[0] != _criterion(case)
            or contract.criteria[0].evidence_identity != expected_identity
        ):
            raise ProspectiveHandoffError(
                f"{case.case_id} frozen contract binding is invalid."
            )
        confirmation_path = _safe_relative_path(
            root,
            item.confirmation_artifact_path,
            label=f"{case.case_id} confirmation artifact",
        )
        confirmation_bytes = _read_regular(
            confirmation_path,
            label=f"{case.case_id} confirmation artifact",
        )
        if _sha256_tagged(confirmation_bytes) != item.confirmation_record_sha256:
            raise ProspectiveHandoffError(
                f"{case.case_id} confirmation artifact digest mismatches."
            )
        try:
            _strict_json_bytes(
                confirmation_bytes,
                label=f"{case.case_id} confirmation artifact",
            )
            confirmation = parse_confirmation(
                confirmation_bytes,
                idempotency_key=confirmation_idempotency_key(case.case_id),
            )
        except (ValidationError, ValueError) as error:
            raise ProspectiveHandoffError(
                f"{case.case_id} confirmation artifact failed strict validation."
            ) from error
        if (
            contract.id != item.contract_id
            or contract.version != item.contract_version
            or confirmation.confirmation_id != item.confirmation_id
            or contract_confirmation_fingerprint(contract)
            != item.contract_confirmation_fingerprint
            or confirmation.contract_fingerprint
            != item.contract_confirmation_fingerprint
            or confirmation.decision is not ConfirmationDecision.CONFIRM
            or not confirmation.agreement_acknowledged
            or not confirmation_matches_contract(contract, confirmation)
        ):
            raise ProspectiveHandoffError(
                f"{case.case_id} confirmation binding is invalid."
            )
        source_path = _safe_relative_path(
            root,
            item.source_yaml_artifact_path,
            label=f"{case.case_id} source artifact",
        )
        source_bytes = _read_regular(source_path, label=f"{case.case_id} source YAML")
        if _sha256_tagged(source_bytes) != item.source_yaml_artifact_sha256:
            raise ProspectiveHandoffError(
                f"{case.case_id} source YAML digest mismatches."
            )
        if source_bytes != source_yaml_bytes(case, item.producer_contract_link):
            raise ProspectiveHandoffError(f"{case.case_id} source YAML bytes drifted.")
        _validate_source(source_path, case, item.producer_contract_link)
    final_file_identities = _validate_closed_tree(root, manifest)
    if final_file_identities != initial_file_identities:
        raise ProspectiveHandoffError(
            "Handoff artifact identities changed during validation."
        )
    final_manifest_bytes = _read_regular(
        manifest_path,
        label="prospective handoff manifest final read",
    )
    if final_manifest_bytes != manifest_bytes:
        raise ProspectiveHandoffError(
            "Prospective handoff manifest changed during validation."
        )
    return ProspectiveHandoffValidation(
        manifest=manifest,
        manifest_sha256=_sha256(manifest_bytes),
    )


__all__ = [
    "FrozenProspectiveCase",
    "PROSPECTIVE_CASES",
    "PROSPECTIVE_EXPECTED_EXECUTION_FINGERPRINT",
    "PROSPECTIVE_HANDOFF_SCHEMA_VERSION",
    "PROSPECTIVE_LINK_DERIVATION_POLICY_ID",
    "PROSPECTIVE_WORKLOAD_DIGEST",
    "ProspectiveHandoffCaseModel",
    "ProspectiveHandoffError",
    "ProspectiveHandoffManifest",
    "ProspectiveHandoffValidation",
    "build_prospective_approved_contract",
    "confirmation_idempotency_key",
    "derive_producer_contract_link",
    "freeze_prospective_case",
    "materialize_prospective_handoff",
    "source_yaml_bytes",
    "validate_prospective_handoff",
]
