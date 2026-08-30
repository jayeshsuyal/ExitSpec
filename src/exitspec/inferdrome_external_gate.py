"""External-only managed evidence admission for the exact A100/Qwen3 run.

This is an ingestion boundary, not an acceptance evaluator.  It verifies the
retained transport and the complete sealed bundle, matches the versioned
profile registry, and returns only independently recalculated facts.  The
raw archive and the producer profile remain external inputs.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Final, NoReturn

from .inferdrome_archive import (
    InferdromeArchiveErrorCode,
    InferdromeArchiveLimits,
    InferdromeArchiveRejected,
    extract_external_inferdrome_archive,
)
from .inferdrome_bundle import (
    InferdromeBundleErrorCode,
    InferdromeBundleLimits,
    InferdromeBundleRejected,
    RecalculatedInferdromeMeasurements,
    verify_inferdrome_bundle,
)
from .inferdrome_profile import canonical_document_sha256
from .inferdrome_profile_registry import (
    A100_CAMPAIGN_ID,
    A100_HARDWARE_MODEL,
    A100_MANAGED_PROFILE_ID,
    A100_METRIC_DEFINITION_ID,
    ManagedEvidenceProfile,
    ManagedEvidenceProfileRejected,
    get_managed_evidence_profile,
)


A100_ARCHIVE_SHA256: Final = (
    "sha256:92e9456b33d2b8fe6b4df24ce6a487ea1fde09ddbc20b5acdfecdf19abd5efdc"
)
A100_ARCHIVE_SIZE_BYTES: Final = 322_240
A100_BUNDLE_MEMBER_PATH: Final = (
    "capture/runs/run-9a01c9d8b4044e56eb68b2cf0345f5e0/bundle"
)
A100_BUNDLE_DIGEST: Final = (
    "sha256:6fcfa686c106a0fa1de2cf6c338d8de36cb8778d7880bb3e8701043ce5aa353a"
)
A100_CAPTURE_MANIFEST_SHA256: Final = (
    "sha256:3d80c59117c154cdc8157a9898f14cae3b9e17b968650fc8693cf45c8436c0f6"
)
A100_RUN_ID: Final = "run-9a01c9d8b4044e56eb68b2cf0345f5e0"
A100_SOURCE_SPEC_DIGEST: Final = (
    "sha256:1218bbafcded589a5b6fff13f533df8bd4e2a8e176a3995833967cc85334fd3f"
)
A100_REQUEST_PLAN_DIGEST: Final = (
    "sha256:e272bf9c8d82bf3fd0eddd74c5b2b74edd21fa9358165e7d40aa3246f25b4498"
)
A100_EXECUTION_FINGERPRINT: Final = (
    "sha256:256f096f93a14260857d4caba59165459335af9c9c3b57d56eaa9bd369e405c3"
)
A100_METRIC_DEFINITIONS_DIGEST: Final = (
    "sha256:e237ff8613c6eec52a6053b3f6b47563ffc758c957ee298e57fe3c482a389131"
)
A100_EXPECTED_P95_NS: Final = 79_279_716
A100_EXPECTED_MEASURED_REQUESTS: Final = 96
A100_EXPECTED_SUCCESSFUL_REQUESTS: Final = 96
A100_EXPECTED_FAILED_REQUESTS: Final = 0
A100_CAPABILITY_PROFILE_COMMIT: Final = "6cb774d210940073347f9045bb15611aa9e9cf27"
A100_MODEL_SNAPSHOT_SHA256: Final = (
    "sha256:588d19e9e489cccdad793718d8c5efbad0738be717369f9eacb94ce514992d2c"
)
A100_MODEL_SNAPSHOT_FILE_COUNT: Final = 15
A100_MODEL_SNAPSHOT_TOTAL_BYTES: Final = 16_397_461_266
A100_ACCEPTANCE_STATEMENT: Final = (
    "This capture proves one bounded runtime observation. It does not assign PASS, "
    "FAIL, or NOT_PROVEN and is not a cross-GPU result."
)
A100_DELIVERY_LOCATION: Final = (
    "https://github.com/jayeshsuyal/inferdrome/releases/download/"
    "gpu-evidence-2026-08-23/inferdrome-qwen3-8b-a100-sxm4-92e9456b.tar.gz"
)
A100_DELIVERY_STATEMENT: Final = (
    "The raw archive remains ignored. Public CI can validate committed anchors, "
    "but byte-level archive reverification requires these exact reviewed bytes "
    "until owner license and publication approval exists."
)
A100_PUBLICATION_REVIEW_SHA256: Final = (
    "sha256:f2616b08b526cb7346457dec585a98b5918e2c246b03faab9335a8a358e74208"
)
A100_OPERATIONAL_SUMMARY_SHA256: Final = (
    "sha256:167835a2e3c353f5ec06549cc06a5c049566a5d5201a5fae6e6567dea763488e"
)
A100_PROFILE_HOST_DEPENDENCIES_SHA256: Final = (
    "sha256:4ae954afc7b7fec4db21c62d50c9f5262ad1e261270ce433840586c8410239a5"
)
A100_PROFILE_SNAPSHOT_MANIFEST_SHA256: Final = (
    "sha256:ef291a8dd0f21604c8da3025f5112bd6641e891a3401c8550584725eaabe55cc"
)
A100_PROFILE_WORKLOAD_MANIFEST_SHA256: Final = (
    "sha256:297cd52871a00c62914a61f6a4417fe6f97c813b0421e02c6097897af0ed792b"
)
_MAX_METADATA_BYTES: Final = 1_048_576
_MAX_METADATA_INTEGER: Final = (1 << 63) - 1
_MAX_METADATA_DEPTH: Final = 64


class ManagedEvidenceAdmissionErrorCode(str, Enum):
    """Stable fail-closed reasons at the external admission boundary."""

    ARCHIVE_SHA256_MISMATCH = "ARCHIVE_SHA256_MISMATCH"
    ARCHIVE_SIZE_MISMATCH = "ARCHIVE_SIZE_MISMATCH"
    ARCHIVE_UNSAFE = "ARCHIVE_UNSAFE"
    ARCHIVE_MEMBER_MISSING = "ARCHIVE_MEMBER_MISSING"
    BUNDLE_DIGEST_MISMATCH = "BUNDLE_DIGEST_MISMATCH"
    BUNDLE_INTEGRITY_MISMATCH = "BUNDLE_INTEGRITY_MISMATCH"
    BUNDLE_SCHEMA_INVALID = "BUNDLE_SCHEMA_INVALID"
    PROFILE_UNKNOWN = "PROFILE_UNKNOWN"
    PROFILE_ID_MISMATCH = "PROFILE_ID_MISMATCH"
    PROFILE_SHA256_MISMATCH = "PROFILE_SHA256_MISMATCH"
    PROFILE_FACTS_MISSING = "PROFILE_FACTS_MISSING"
    PROFILE_FACTS_EXTRA = "PROFILE_FACTS_EXTRA"
    PROFILE_FACT_MISMATCH = "PROFILE_FACT_MISMATCH"
    PROFILE_SCHEMA_INVALID = "PROFILE_SCHEMA_INVALID"
    MODEL_MISMATCH = "MODEL_MISMATCH"
    REVISION_MISMATCH = "REVISION_MISMATCH"
    ENGINE_MISMATCH = "ENGINE_MISMATCH"
    ADAPTER_MISMATCH = "ADAPTER_MISMATCH"
    WORKLOAD_MISMATCH = "WORKLOAD_MISMATCH"
    METRIC_SEMANTICS_MISMATCH = "METRIC_SEMANTICS_MISMATCH"
    METRIC_REDUCER_MISMATCH = "METRIC_REDUCER_MISMATCH"
    METRIC_POPULATION_MISMATCH = "METRIC_POPULATION_MISMATCH"
    METRIC_UNITS_MISMATCH = "METRIC_UNITS_MISMATCH"
    CONCURRENCY_MISMATCH = "CONCURRENCY_MISMATCH"
    SAMPLE_COUNT_MISMATCH = "SAMPLE_COUNT_MISMATCH"
    ENVIRONMENT_MISMATCH = "ENVIRONMENT_MISMATCH"
    LEGACY_PROFILE_REQUIRES_LEGACY_PATH = "LEGACY_PROFILE_REQUIRES_LEGACY_PATH"
    CHRONOLOGY_MISMATCH = "CHRONOLOGY_MISMATCH"
    CONTRACT_LINK_MISMATCH = "CONTRACT_LINK_MISMATCH"
    PROVENANCE_INSUFFICIENT = "PROVENANCE_INSUFFICIENT"
    EVIDENCE_INELIGIBLE = "EVIDENCE_INELIGIBLE"
    SYNTHETIC_EVIDENCE = "SYNTHETIC_EVIDENCE"
    MALFORMED_JSON = "MALFORMED_JSON"
    MALFORMED_JSONL = "MALFORMED_JSONL"
    TAMPERED_INPUT = "TAMPERED_INPUT"
    RECALCULATION_MISMATCH = "RECALCULATION_MISMATCH"
    CLEANUP_FAILED = "CLEANUP_FAILED"


class ManagedEvidenceAdmissionRejected(ValueError):
    """Untrusted external evidence was rejected before acceptance evaluation."""

    def __init__(
        self,
        code: ManagedEvidenceAdmissionErrorCode,
        message: str,
        *,
        path: str | None = None,
    ) -> None:
        self.code = code
        self.path = path
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class A100RetrospectiveArchiveExpectation:
    """Run-specific transport anchors kept outside the profile registry."""

    archive_sha256: str
    archive_size_bytes: int
    bundle_member_path: str
    bundle_digest: str
    capture_manifest_sha256: str
    run_id: str
    source_spec_digest: str
    request_plan_digest: str
    execution_fingerprint: str
    metric_definitions_digest: str
    expected_p95_ns: int
    measured_requests: int
    successful_requests: int
    failed_requests: int


A100_RETROSPECTIVE_EXPECTATION: Final = A100RetrospectiveArchiveExpectation(
    archive_sha256=A100_ARCHIVE_SHA256,
    archive_size_bytes=A100_ARCHIVE_SIZE_BYTES,
    bundle_member_path=A100_BUNDLE_MEMBER_PATH,
    bundle_digest=A100_BUNDLE_DIGEST,
    capture_manifest_sha256=A100_CAPTURE_MANIFEST_SHA256,
    run_id=A100_RUN_ID,
    source_spec_digest=A100_SOURCE_SPEC_DIGEST,
    request_plan_digest=A100_REQUEST_PLAN_DIGEST,
    execution_fingerprint=A100_EXECUTION_FINGERPRINT,
    metric_definitions_digest=A100_METRIC_DEFINITIONS_DIGEST,
    expected_p95_ns=A100_EXPECTED_P95_NS,
    measured_requests=A100_EXPECTED_MEASURED_REQUESTS,
    successful_requests=A100_EXPECTED_SUCCESSFUL_REQUESTS,
    failed_requests=A100_EXPECTED_FAILED_REQUESTS,
)


@dataclass(frozen=True, slots=True)
class ManagedEvidenceAdmission:
    """Admitted identity and recalculated facts; no acceptance verdict."""

    profile_id: str
    profile_sha256: str
    archive_sha256: str
    bundle_digest: str
    bundle_member_path: str
    run_id: str
    recalculated: RecalculatedInferdromeMeasurements
    archive_member_count: int
    archive_file_count: int
    archive_directory_count: int
    archive_expanded_bytes: int


def admit_a100_qwen3_retrospective(
    archive_path: Path,
    destination: Path,
    *,
    handoff_manifest_path: Path,
    publication_review_path: Path,
    operational_summary_path: Path,
    profile_document_path: Path,
    limits: InferdromeArchiveLimits | None = None,
    bundle_limits: InferdromeBundleLimits | None = None,
) -> ManagedEvidenceAdmission:
    """Admit the exact registry-owned external A100/Qwen3 bundle."""

    return _admit_a100_qwen3_retrospective(
        archive_path,
        destination,
        handoff_manifest_path=handoff_manifest_path,
        publication_review_path=publication_review_path,
        operational_summary_path=operational_summary_path,
        profile_document_path=profile_document_path,
        limits=limits,
        bundle_limits=bundle_limits,
        expectation=A100_RETROSPECTIVE_EXPECTATION,
    )


def _admit_a100_qwen3_retrospective(
    archive_path: Path,
    destination: Path,
    *,
    handoff_manifest_path: Path,
    publication_review_path: Path,
    operational_summary_path: Path,
    profile_document_path: Path,
    limits: InferdromeArchiveLimits | None = None,
    bundle_limits: InferdromeBundleLimits | None = None,
    expectation: A100RetrospectiveArchiveExpectation,
) -> ManagedEvidenceAdmission:
    """Admit the exact external A100/Qwen3 retrospective bundle.

    This function does not consult a producer verdict, contract threshold, or
    acceptance evaluator.  It succeeds only when the archive, reviewed safe
    metadata, profile identity, complete bundle, and independent p95 facts all
    agree.
    """

    profile = get_managed_evidence_profile(A100_MANAGED_PROFILE_ID)
    _validate_profile_document(profile_document_path, profile)
    handoff = _read_metadata(handoff_manifest_path, "handoff manifest")
    publication = _read_metadata(publication_review_path, "publication review")
    operational = _read_metadata(operational_summary_path, "operational summary")
    _validate_handoff(handoff, profile, expectation)
    _validate_publication_review(publication, expectation)
    _validate_operational_summary(operational, expectation)

    try:
        extracted = extract_external_inferdrome_archive(
            archive_path,
            destination,
            expected_member_path=expectation.bundle_member_path,
            expected_sha256=expectation.archive_sha256,
            expected_size_bytes=expectation.archive_size_bytes,
            limits=limits,
        )
    except InferdromeArchiveRejected as error:
        raise _archive_rejection(error) from error

    try:
        try:
            verified = verify_inferdrome_bundle(
                extracted.member,
                expected_bundle_digest=expectation.bundle_digest,
                limits=bundle_limits,
                require_customer_eligible=True,
                managed_evidence_profile=profile,
            )
        except ManagedEvidenceProfileRejected as error:
            raise _profile_rejection(error) from error
        except InferdromeBundleRejected as error:
            raise _bundle_rejection(error) from error

        _validate_admitted_bundle(verified, profile, expectation, handoff)
        recalculated = verified.recalculated
        if (
            recalculated.attempted_count != expectation.measured_requests
            or recalculated.successful_count != expectation.successful_requests
            or recalculated.failed_count != expectation.failed_requests
            or recalculated.anomalous_count != 0
            or recalculated.p95_ttft_ns != expectation.expected_p95_ns
            or recalculated.ttft_definition != A100_METRIC_DEFINITION_ID
        ):
            _reject(
                ManagedEvidenceAdmissionErrorCode.RECALCULATION_MISMATCH,
                "Independent request-record recalculation does not match the exact handoff facts.",
            )
        return ManagedEvidenceAdmission(
            profile_id=profile.profile_id,
            profile_sha256=profile.profile_sha256,
            archive_sha256=extracted.archive_sha256,
            bundle_digest=verified.bundle_digest,
            bundle_member_path=extracted.member_path,
            run_id=str(verified.descriptor["run_id"]),
            recalculated=recalculated,
            archive_member_count=extracted.member_count,
            archive_file_count=extracted.file_count,
            archive_directory_count=extracted.directory_count,
            archive_expanded_bytes=extracted.expanded_bytes,
        )
    except Exception:
        _cleanup_extracted_bundle(extracted.root)
        raise


def admit_managed_evidence_archive(
    archive_path: Path,
    destination: Path,
    *,
    handoff_manifest_path: Path,
    publication_review_path: Path,
    operational_summary_path: Path,
    profile_document_path: Path,
    limits: InferdromeArchiveLimits | None = None,
    bundle_limits: InferdromeBundleLimits | None = None,
) -> ManagedEvidenceAdmission:
    """Stable generic-facing alias for the currently authorized A100 gate."""

    return admit_a100_qwen3_retrospective(
        archive_path,
        destination,
        handoff_manifest_path=handoff_manifest_path,
        publication_review_path=publication_review_path,
        operational_summary_path=operational_summary_path,
        profile_document_path=profile_document_path,
        limits=limits,
        bundle_limits=bundle_limits,
    )


def _validate_profile_document(
    path: Path,
    profile: ManagedEvidenceProfile,
) -> None:
    document = _read_metadata(path, "managed profile document")
    _exact_keys(
        document,
        {
            "benchmark_invocation",
            "campaign_id",
            "claims_boundary",
            "digest_policy",
            "implementation_state",
            "model",
            "producer",
            "profile_id",
            "schema_version",
            "server_invocation",
            "workload_binding",
        },
        "profile",
    )
    try:
        document_digest = canonical_document_sha256(document)
    except ValueError as error:
        raise ManagedEvidenceAdmissionRejected(
            ManagedEvidenceAdmissionErrorCode.MALFORMED_JSON,
            "Managed profile document cannot be canonicalized.",
        ) from error
    if document_digest != profile.profile_sha256:
        _reject(
            ManagedEvidenceAdmissionErrorCode.PROFILE_SHA256_MISMATCH,
            "Managed profile document digest does not match its exact pin.",
        )
    _exact(
        document.get("schema_version"),
        "inferdrome.campaign-managed-vllm-profile.v1",
        ManagedEvidenceAdmissionErrorCode.PROFILE_SCHEMA_INVALID,
        "Managed profile document schema is unsupported.",
        "profile.schema_version",
    )
    _exact(
        document.get("profile_id"),
        profile.profile_id,
        ManagedEvidenceAdmissionErrorCode.PROFILE_ID_MISMATCH,
        "Managed profile document ID does not match the registry.",
        "profile.profile_id",
    )
    _exact(
        _object(document.get("claims_boundary")),
        {
            "acceptance_verdict": "NONE",
            "hardware_attestation": False,
            "runtime_compatibility": "UNPROVEN_REQUIRES_BOUNDED_SPIKE",
        },
        ManagedEvidenceAdmissionErrorCode.PROVENANCE_INSUFFICIENT,
        "Managed profile claims boundary is not external-only.",
        "profile.claims_boundary",
    )
    producer = _object(document.get("producer"))
    _exact_values(
        producer,
        {
            "name": profile.producer_name,
            "version": profile.producer_version,
            "adapter_name": profile.adapter_id,
            "adapter_version": profile.adapter_version,
            "host_dependencies_sha256": A100_PROFILE_HOST_DEPENDENCIES_SHA256,
        },
        "profile.producer",
        ManagedEvidenceAdmissionErrorCode.PROFILE_FACT_MISMATCH,
    )
    model = _object(document.get("model"))
    _exact_values(
        model,
        {
            "model_id": profile.model_id,
            "model_revision": profile.model_revision,
            "tokenizer_revision": profile.tokenizer_revision,
            "checkpoint_precision": profile.checkpoint_precision,
            "activation_dtype": profile.dtype,
            "snapshot_identity_sha256": A100_MODEL_SNAPSHOT_SHA256,
            "snapshot_manifest_sha256": A100_PROFILE_SNAPSHOT_MANIFEST_SHA256,
        },
        "profile.model",
        ManagedEvidenceAdmissionErrorCode.PROFILE_FACT_MISMATCH,
    )
    server = _object(document.get("server_invocation"))
    _exact_values(
        server,
        {
            "device_ids": "selected_single_physical_gpu",
            "dtype": profile.dtype,
            "max_model_len": profile.max_model_len,
            "gpu_memory_utilization": profile.gpu_memory_utilization,
            "tensor_parallel_size": profile.tensor_parallel_size,
            "model_impl": profile.engine,
            "generation_config": "vllm",
            "load_format": "safetensors",
        },
        "profile.server_invocation",
        ManagedEvidenceAdmissionErrorCode.PROFILE_FACT_MISMATCH,
    )
    workload = _object(document.get("workload_binding"))
    _exact_values(
        workload,
        {
            "workload_id": profile.workload_id,
            "workload_sha256": profile.workload_sha256,
            "warmup_requests": profile.warmup_requests,
            "measured_requests": profile.measured_requests,
            "manifest_sha256": A100_PROFILE_WORKLOAD_MANIFEST_SHA256,
        },
        "profile.workload_binding",
        ManagedEvidenceAdmissionErrorCode.WORKLOAD_MISMATCH,
    )


def _validate_handoff(
    handoff: dict[str, Any],
    profile: ManagedEvidenceProfile,
    expectation: A100RetrospectiveArchiveExpectation,
) -> None:
    _exact_keys(
        handoff,
        {
            "acceptance_boundary",
            "archive",
            "capability_profile",
            "contract_binding",
            "fixture_delivery",
            "history_provenance",
            "operational_completion",
            "publication_review",
            "run",
            "runtime_capability",
            "schema_version",
        },
        "handoff",
    )
    _exact(
        handoff.get("schema_version"),
        "inferdrome.qwen3-gpu-evidence-handoff.v1",
        ManagedEvidenceAdmissionErrorCode.MALFORMED_JSON,
        "External handoff manifest schema is unsupported.",
        "handoff.schema_version",
    )
    acceptance = _object(handoff.get("acceptance_boundary"))
    _exact_values(
        acceptance,
        {
            "capture_kind": "BOUNDED_RUNTIME_CAPABILITY_SPIKE",
            "inferdrome_acceptance_verdict": None,
            "publication_state": "OBSERVATION_ONLY_PENDING_REVIEW",
            "statement": A100_ACCEPTANCE_STATEMENT,
        },
        "handoff.acceptance_boundary",
        ManagedEvidenceAdmissionErrorCode.PROVENANCE_INSUFFICIENT,
    )
    archive = _object(handoff.get("archive"))
    _exact_values(
        archive,
        {
            "bundle_member_path": expectation.bundle_member_path,
            "capture_manifest_sha256": expectation.capture_manifest_sha256,
            "compressed_size_bytes": expectation.archive_size_bytes,
            "sha256": expectation.archive_sha256,
        },
        "handoff.archive",
        ManagedEvidenceAdmissionErrorCode.ARCHIVE_SHA256_MISMATCH,
    )
    capability = _object(handoff.get("capability_profile"))
    _exact_keys(
        capability,
        {"campaign_id", "commit", "managed_profile", "model_snapshot", "workload"},
        "handoff.capability_profile",
    )
    _exact(
        capability.get("campaign_id"),
        A100_CAMPAIGN_ID,
        ManagedEvidenceAdmissionErrorCode.PROFILE_FACT_MISMATCH,
        "Handoff campaign identity does not match the selected profile.",
        "handoff.capability_profile.campaign_id",
    )
    _exact(
        capability.get("commit"),
        A100_CAPABILITY_PROFILE_COMMIT,
        ManagedEvidenceAdmissionErrorCode.PROVENANCE_INSUFFICIENT,
        "Handoff capability profile commit does not match the reviewed profile.",
        "handoff.capability_profile.commit",
    )
    managed = _object(capability.get("managed_profile"))
    _exact_values(
        managed,
        {
            "identity": profile.profile_id,
            "path": "campaigns/v1/profiles/managed-vllm-0.26-qwen3-8b-bf16-v1.json",
            "sha256": profile.profile_sha256,
        },
        "handoff.capability_profile.managed_profile",
        ManagedEvidenceAdmissionErrorCode.PROFILE_FACT_MISMATCH,
    )
    model_snapshot = _object(capability.get("model_snapshot"))
    _exact_values(
        model_snapshot,
        {
            "file_count": A100_MODEL_SNAPSHOT_FILE_COUNT,
            "revision": profile.model_revision,
            "sha256": A100_MODEL_SNAPSHOT_SHA256,
            "total_bytes": A100_MODEL_SNAPSHOT_TOTAL_BYTES,
        },
        "handoff.capability_profile.model_snapshot",
        ManagedEvidenceAdmissionErrorCode.PROFILE_FACT_MISMATCH,
    )
    workload = _object(capability.get("workload"))
    _exact_values(
        workload,
        {
            "id": profile.workload_id,
            "path": "campaigns/v1/workloads/qwen-text-mixed-length-v1.jsonl",
            "prompt_count": profile.measured_requests,
            "sha256": profile.workload_sha256,
        },
        "handoff.capability_profile.workload",
        ManagedEvidenceAdmissionErrorCode.WORKLOAD_MISMATCH,
    )
    binding = _object(handoff.get("contract_binding"))
    _exact_values(
        binding,
        {
            "chronology": profile.chronology,
            "chronology_disclosure": (
                "No producer-side ExitSpec contract digest was frozen before this "
                "measurement. A later consumer must use an explicit external "
                "receipt binding without rewriting chronology."
            ),
            "producer_exitspec_contract_digest": None,
            "required_consumer_mode": profile.consumer_mode,
        },
        "handoff.contract_binding",
        ManagedEvidenceAdmissionErrorCode.CONTRACT_LINK_MISMATCH,
    )
    delivery = _object(handoff.get("fixture_delivery"))
    _exact_values(
        delivery,
        {
            "proposed_checksum_pinned_location": A100_DELIVERY_LOCATION,
            "publication_state": "BLOCKED_PENDING_OWNER_APPROVAL",
            "required_sha256": expectation.archive_sha256,
            "statement": A100_DELIVERY_STATEMENT,
        },
        "handoff.fixture_delivery",
        ManagedEvidenceAdmissionErrorCode.PROVENANCE_INSUFFICIENT,
    )
    history = _object(handoff.get("history_provenance"))
    _exact_values(
        history,
        {
            "capability_profile_commit": "6cb774d210940073347f9045bb15611aa9e9cf27",
            "capture_producer_commit": "a02bfd7c3f8bd0f734da0e84d476bcfa905fec4b",
            "eventual_merge_commit": None,
            "merge_requirement": (
                "Use a merge commit that preserves capture_producer_commit ancestry; "
                "do not squash or rebase away the producer commit."
            ),
            "publication_review_commit": None,
        },
        "handoff.history_provenance",
        ManagedEvidenceAdmissionErrorCode.PROVENANCE_INSUFFICIENT,
    )
    _exact_values(
        _object(handoff.get("publication_review")),
        {
            "owner_publication_approval_required": True,
            "path": "evidence/gpu/2026-08-23-qwen3-8b-a100-sxm4/publication-review.json",
            "publication_status": "EXTERNAL_ONLY",
            "sha256": A100_PUBLICATION_REVIEW_SHA256,
        },
        "handoff.publication_review",
        ManagedEvidenceAdmissionErrorCode.PROVENANCE_INSUFFICIENT,
    )
    _exact_values(
        _object(handoff.get("operational_completion")),
        {
            "path": "evidence/gpu/2026-08-23-qwen3-8b-a100-sxm4/operational-summary.json",
            "semantic_verification": "VALID_AFTER_PROVIDER_TERMINATION",
            "sha256": A100_OPERATIONAL_SUMMARY_SHA256,
            "termination_final_status": "absent",
        },
        "handoff.operational_completion",
        ManagedEvidenceAdmissionErrorCode.PROVENANCE_INSUFFICIENT,
    )
    run = _object(handoff.get("run"))
    _exact_keys(
        run,
        {
            "bundle_digest",
            "execution_fingerprint",
            "metric_definitions_digest",
            "model",
            "request_plan_digest",
            "request_population",
            "run_id",
            "source_spec_digest",
            "summary_measurements",
            "workload_sha256",
        },
        "handoff.run",
    )
    for name, expected in {
        "bundle_digest": expectation.bundle_digest,
        "execution_fingerprint": expectation.execution_fingerprint,
        "metric_definitions_digest": expectation.metric_definitions_digest,
        "request_plan_digest": expectation.request_plan_digest,
        "run_id": expectation.run_id,
        "source_spec_digest": expectation.source_spec_digest,
        "workload_sha256": profile.workload_sha256,
    }.items():
        _exact(
            run.get(name),
            expected,
            ManagedEvidenceAdmissionErrorCode.TAMPERED_INPUT,
            f"Handoff run fact {name} does not match the reviewed run.",
            f"handoff.run.{name}",
        )
    request_population = _object(run.get("request_population"))
    _exact_values(
        request_population,
        {
            "failed_requests": expectation.failed_requests,
            "measured_requests": expectation.measured_requests,
            "successful_requests": expectation.successful_requests,
            "ttft_samples": expectation.successful_requests,
        },
        "handoff.run.request_population",
        ManagedEvidenceAdmissionErrorCode.SAMPLE_COUNT_MISMATCH,
    )
    summary_measurements = _object(run.get("summary_measurements"))
    _exact_keys(
        summary_measurements,
        {"output_token_throughput_per_s", "ttft_ns"},
        "handoff.run.summary_measurements",
    )
    _exact(
        summary_measurements.get("output_token_throughput_per_s"),
        "73.377319",
        ManagedEvidenceAdmissionErrorCode.METRIC_SEMANTICS_MISMATCH,
        "Handoff throughput measurement does not match the reviewed run.",
        "handoff.run.summary_measurements.output_token_throughput_per_s",
    )
    target = _object(run.get("model"))
    _exact_keys(target, {"id", "revision"}, "handoff.run.model")
    _exact(
        target.get("id"),
        profile.model_id,
        ManagedEvidenceAdmissionErrorCode.MODEL_MISMATCH,
        "Handoff model identity does not match the selected profile.",
        "handoff.run.model.id",
    )
    _exact(
        target.get("revision"),
        profile.model_revision,
        ManagedEvidenceAdmissionErrorCode.REVISION_MISMATCH,
        "Handoff model revision does not match the selected profile.",
        "handoff.run.model.revision",
    )
    metric = _object(_object(run.get("summary_measurements")).get("ttft_ns"))
    _exact_keys(
        metric,
        {
            "definition_id",
            "p50",
            "p95",
            "p99",
            "population",
            "quantile_method",
        },
        "handoff.run.summary_measurements.ttft_ns",
    )
    _exact(
        metric.get("definition_id"),
        profile.metric_definition_id,
        ManagedEvidenceAdmissionErrorCode.METRIC_SEMANTICS_MISMATCH,
        "Handoff metric identity does not match the selected profile.",
        "handoff.run.summary_measurements.ttft_ns.definition_id",
    )
    _exact(
        metric.get("population"),
        profile.metric_population,
        ManagedEvidenceAdmissionErrorCode.METRIC_POPULATION_MISMATCH,
        "Handoff metric population does not match the selected profile.",
        "handoff.run.summary_measurements.ttft_ns.population",
    )
    _exact(
        metric.get("quantile_method"),
        profile.metric_reducer_id,
        ManagedEvidenceAdmissionErrorCode.METRIC_REDUCER_MISMATCH,
        "Handoff metric reducer does not match the selected profile.",
        "handoff.run.summary_measurements.ttft_ns.quantile_method",
    )
    _exact(
        metric.get("p95"),
        expectation.expected_p95_ns,
        ManagedEvidenceAdmissionErrorCode.METRIC_SEMANTICS_MISMATCH,
        "Handoff metric p95 does not match the exact external expectation.",
        "handoff.run.summary_measurements.ttft_ns.p95",
    )
    _exact(
        metric.get("p50"),
        42_974_685,
        ManagedEvidenceAdmissionErrorCode.METRIC_SEMANTICS_MISMATCH,
        "Handoff metric p50 does not match the reviewed run.",
        "handoff.run.summary_measurements.ttft_ns.p50",
    )
    _exact(
        metric.get("p99"),
        80_570_049,
        ManagedEvidenceAdmissionErrorCode.METRIC_SEMANTICS_MISMATCH,
        "Handoff metric p99 does not match the reviewed run.",
        "handoff.run.summary_measurements.ttft_ns.p99",
    )
    runtime = _object(handoff.get("runtime_capability"))
    _exact_keys(
        runtime,
        {
            "expected_gpu_model",
            "gpu_tier_id",
            "hardware_attestation",
            "hardware_observation",
            "profile_id",
            "spike_outcome",
            "torch_cuda_device_count",
        },
        "handoff.runtime_capability",
    )
    _exact_values(
        runtime,
        {
            "expected_gpu_model": A100_HARDWARE_MODEL,
            "gpu_tier_id": "a100-40gb-sxm4",
            "hardware_attestation": False,
            "hardware_observation": (
                "SELECTED_GPU_REPORTED_NVIDIA_A100-SXM4-40GB_SINGLE_CUDA_DEVICE"
            ),
            "profile_id": profile.profile_id,
            "spike_outcome": "SPIKE_SUCCEEDED",
            "torch_cuda_device_count": 1,
        },
        "handoff.runtime_capability",
        ManagedEvidenceAdmissionErrorCode.PROVENANCE_INSUFFICIENT,
    )


def _validate_publication_review(
    review: dict[str, Any],
    expectation: A100RetrospectiveArchiveExpectation,
) -> None:
    _exact_keys(
        review,
        {
            "archive",
            "archive_integrity_and_safety",
            "content_review",
            "decision_reasons",
            "detector_results",
            "findings",
            "license_review",
            "owner_publication_approval_required",
            "publication_status",
            "raw_archive_modified",
            "review_limits",
            "review_method",
            "schema_version",
            "scope",
        },
        "publication_review",
    )
    _exact(
        review.get("schema_version"),
        "inferdrome.gpu-evidence-publication-review.v1",
        ManagedEvidenceAdmissionErrorCode.MALFORMED_JSON,
        "External publication review schema is unsupported.",
        "publication_review.schema_version",
    )
    _exact(
        review.get("scope"),
        "exact_archive_bytes",
        ManagedEvidenceAdmissionErrorCode.TAMPERED_INPUT,
        "Publication review scope is not exact archive bytes.",
        "publication_review.scope",
    )
    _exact(
        review.get("publication_status"),
        "EXTERNAL_ONLY",
        ManagedEvidenceAdmissionErrorCode.PROVENANCE_INSUFFICIENT,
        "Publication review is not marked external-only.",
        "publication_review.publication_status",
    )
    _exact(
        review.get("raw_archive_modified"),
        False,
        ManagedEvidenceAdmissionErrorCode.TAMPERED_INPUT,
        "Publication review reports a modified raw archive.",
        "publication_review.raw_archive_modified",
    )
    archive = _object(review.get("archive"))
    _exact_values(
        archive,
        {
            "compressed_size_bytes": expectation.archive_size_bytes,
            "sha256": expectation.archive_sha256,
        },
        "publication_review.archive",
        ManagedEvidenceAdmissionErrorCode.ARCHIVE_SHA256_MISMATCH,
    )
    safety = _object(review.get("archive_integrity_and_safety"))
    _exact_values(
        safety,
        {
            "archive_member_rules": [
                "top-level capture directory required",
                "absolute and traversal paths rejected",
                "duplicate members rejected",
                "links, devices, and special files rejected",
                "member, directory, file, expanded-byte, and compressed-byte limits",
            ],
            "capture_manifest_sha256": expectation.capture_manifest_sha256,
            "directory_count": 15,
            "expanded_bytes": 2_103_304,
            "file_count": 52,
            "isolated_verification": True,
            "status": "PASS",
        },
        "publication_review.archive_integrity_and_safety",
        ManagedEvidenceAdmissionErrorCode.ARCHIVE_UNSAFE,
    )
    _exact(
        review.get("owner_publication_approval_required"),
        True,
        ManagedEvidenceAdmissionErrorCode.PROVENANCE_INSUFFICIENT,
        "Owner publication gating is missing from the publication review.",
        "publication_review.owner_publication_approval_required",
    )


def _validate_operational_summary(
    summary: dict[str, Any],
    expectation: A100RetrospectiveArchiveExpectation,
) -> None:
    _exact_keys(
        summary,
        {
            "archive_sha256",
            "bundle_digest",
            "capture_manifest_sha256",
            "cost_observation",
            "evidence_kind",
            "provider",
            "repository_commit",
            "run_id",
            "schema_version",
            "semantic_verification",
            "source_receipts",
        },
        "operational_summary",
    )
    _exact(
        summary.get("schema_version"),
        "inferdrome.qwen3-gpu-operational-summary.v1",
        ManagedEvidenceAdmissionErrorCode.MALFORMED_JSON,
        "External operational summary schema is unsupported.",
        "operational_summary.schema_version",
    )
    for name, expected in {
        "archive_sha256": expectation.archive_sha256,
        "bundle_digest": expectation.bundle_digest,
        "run_id": expectation.run_id,
        "repository_commit": "a02bfd7c3f8bd0f734da0e84d476bcfa905fec4b",
        "semantic_verification": "VALID_AFTER_PROVIDER_TERMINATION",
    }.items():
        _exact(
            summary.get(name),
            expected,
            ManagedEvidenceAdmissionErrorCode.TAMPERED_INPUT,
            f"Operational summary fact {name} does not match the reviewed run.",
            f"operational_summary.{name}",
        )
    _exact_keys(
        _object(summary.get("provider")),
        {
            "gpu_tier_id",
            "instance_type_name",
            "termination_confirmed_at",
            "termination_final_status",
            "termination_trigger",
        },
        "operational_summary.provider",
    )
    _exact(
        _object(summary.get("provider")).get("termination_final_status"),
        "absent",
        ManagedEvidenceAdmissionErrorCode.PROVENANCE_INSUFFICIENT,
        "Operational summary does not record completed provider termination.",
        "operational_summary.provider.termination_final_status",
    )


def _validate_admitted_bundle(
    bundle: Any,
    profile: ManagedEvidenceProfile,
    expectation: A100RetrospectiveArchiveExpectation,
    handoff: dict[str, Any],
) -> None:
    descriptor = bundle.descriptor
    if descriptor.get("run_id") != expectation.run_id:
        _reject(
            ManagedEvidenceAdmissionErrorCode.TAMPERED_INPUT,
            "Verified bundle run identity does not match the exact handoff.",
        )
    digests = _object(descriptor.get("digests"))
    for name, expected in (
        ("source_spec_digest", expectation.source_spec_digest),
        ("request_plan_digest", expectation.request_plan_digest),
        ("execution_fingerprint", expectation.execution_fingerprint),
        ("metric_definitions_digest", expectation.metric_definitions_digest),
    ):
        if digests.get(name) != expected:
            _reject(
                ManagedEvidenceAdmissionErrorCode.TAMPERED_INPUT,
                f"Verified bundle digest {name} does not match the exact handoff.",
            )
    if descriptor.get("evidence_eligibility") != "CUSTOMER_ELIGIBLE":
        _reject(
            ManagedEvidenceAdmissionErrorCode.EVIDENCE_INELIGIBLE,
            "Only CUSTOMER_ELIGIBLE evidence may cross the admission boundary.",
        )
    if descriptor.get("producer", {}).get("name") != profile.producer_name:
        _reject(
            ManagedEvidenceAdmissionErrorCode.PROFILE_FACT_MISMATCH,
            "Verified bundle producer does not match the selected profile.",
        )
    handoff_run = _object(handoff.get("run"))
    if handoff_run.get("bundle_digest") != bundle.bundle_digest:
        _reject(
            ManagedEvidenceAdmissionErrorCode.BUNDLE_DIGEST_MISMATCH,
            "Verified bundle digest does not match the reviewed handoff.",
        )


def _read_metadata(path: Path, label: str) -> dict[str, Any]:
    if not isinstance(path, Path):
        raise TypeError(f"{label} path must be a Path.")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        _reject(
            ManagedEvidenceAdmissionErrorCode.MALFORMED_JSON,
            f"{label} is missing, linked, or inaccessible.",
        )
    try:
        info = os.fstat(descriptor)
        before_identity = _file_identity(info)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_size <= 0
            or info.st_size > _MAX_METADATA_BYTES
        ):
            _reject(
                ManagedEvidenceAdmissionErrorCode.MALFORMED_JSON,
                f"{label} is not a bounded regular JSON file.",
            )
        chunks: list[bytes] = []
        remaining = _MAX_METADATA_BYTES + 1
        while remaining > 0:
            chunk = os.read(descriptor, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        content = b"".join(chunks)
        if _file_identity(os.fstat(descriptor)) != before_identity:
            _reject(
                ManagedEvidenceAdmissionErrorCode.TAMPERED_INPUT,
                f"{label} changed during reading.",
            )
    finally:
        os.close(descriptor)
    if len(content) != info.st_size or len(content) > _MAX_METADATA_BYTES:
        _reject(
            ManagedEvidenceAdmissionErrorCode.MALFORMED_JSON,
            f"{label} changed or exceeds its byte limit.",
        )
    try:
        value = json.loads(
            content,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_json_number,
            parse_float=_reject_json_float,
            parse_int=_bounded_json_int,
        )
        _validate_metadata_depth(value)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise ManagedEvidenceAdmissionRejected(
            ManagedEvidenceAdmissionErrorCode.MALFORMED_JSON,
            f"{label} is invalid JSON.",
        ) from error
    if type(value) is not dict:
        _reject(
            ManagedEvidenceAdmissionErrorCode.MALFORMED_JSON,
            f"{label} must be a JSON object.",
        )
    return value


def _file_identity(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON object key")
        value[key] = item
    return value


def _reject_json_number(value: str) -> NoReturn:
    raise ValueError(f"unsupported JSON number: {value}")


def _reject_json_float(value: str) -> NoReturn:
    raise ValueError(f"unsupported JSON float: {value}")


def _bounded_json_int(value: str) -> int:
    digits = value[1:] if value.startswith("-") else value
    if len(digits) > 19:
        raise ValueError("JSON integer exceeds the signed 64-bit bound")
    parsed = int(value)
    if not -_MAX_METADATA_INTEGER <= parsed <= _MAX_METADATA_INTEGER:
        raise ValueError("JSON integer exceeds the signed 64-bit bound")
    return parsed


def _validate_metadata_depth(value: object, depth: int = 0) -> None:
    if depth > _MAX_METADATA_DEPTH:
        raise ValueError("JSON metadata nesting exceeds its depth bound")
    if isinstance(value, dict):
        for item in value.values():
            _validate_metadata_depth(item, depth + 1)
    elif isinstance(value, list):
        for item in value:
            _validate_metadata_depth(item, depth + 1)


def _cleanup_extracted_bundle(root: Path) -> None:
    try:
        shutil.rmtree(root)
    except FileNotFoundError:
        return
    except OSError as error:
        raise ManagedEvidenceAdmissionRejected(
            ManagedEvidenceAdmissionErrorCode.CLEANUP_FAILED,
            "Extracted evidence cleanup failed after admission rejection.",
        ) from error
    if root.exists():
        _reject(
            ManagedEvidenceAdmissionErrorCode.CLEANUP_FAILED,
            "Extracted evidence cleanup did not remove its destination.",
        )


def _object(value: object) -> dict[str, Any]:
    return value if type(value) is dict else {}


def _exact(
    actual: object,
    expected: object,
    code: ManagedEvidenceAdmissionErrorCode,
    message: str,
    path: str,
) -> None:
    if not _strict_equal(actual, expected):
        _reject(code, message, path=path)


def _exact_keys(
    actual: dict[str, Any],
    expected: set[str],
    path: str,
) -> None:
    missing = expected - set(actual)
    if missing:
        _reject(
            ManagedEvidenceAdmissionErrorCode.PROFILE_FACTS_MISSING,
            f"Required metadata fields are missing at {path}.",
            path=path,
        )
    extra = set(actual) - expected
    if extra:
        _reject(
            ManagedEvidenceAdmissionErrorCode.PROFILE_FACTS_EXTRA,
            f"Unsupported metadata fields are present at {path}.",
            path=path,
        )


def _exact_values(
    actual: dict[str, Any],
    expected: dict[str, Any],
    path: str,
    code: ManagedEvidenceAdmissionErrorCode,
) -> None:
    missing = set(expected) - set(actual)
    if missing:
        _reject(
            ManagedEvidenceAdmissionErrorCode.PROFILE_FACTS_MISSING,
            f"Required facts are missing at {path}.",
            path=path,
        )
    if set(actual) - set(expected):
        _reject(
            ManagedEvidenceAdmissionErrorCode.PROFILE_FACTS_EXTRA,
            f"Unsupported facts are present at {path}.",
            path=path,
        )
    if any(not _strict_equal(actual[name], value) for name, value in expected.items()):
        _reject(code, f"Facts do not match at {path}.", path=path)


def _strict_equal(actual: object, expected: object) -> bool:
    if type(actual) is not type(expected):
        return False
    if isinstance(actual, dict):
        return set(actual) == set(expected) and all(
            _strict_equal(actual[key], expected[key]) for key in actual
        )
    if isinstance(actual, list):
        return len(actual) == len(expected) and all(
            _strict_equal(left, right) for left, right in zip(actual, expected)
        )
    return actual == expected


def _profile_rejection(
    error: ManagedEvidenceProfileRejected,
) -> ManagedEvidenceAdmissionRejected:
    try:
        code = ManagedEvidenceAdmissionErrorCode(error.code.value)
    except ValueError:
        code = ManagedEvidenceAdmissionErrorCode.PROFILE_FACT_MISMATCH
    return ManagedEvidenceAdmissionRejected(code, str(error), path=error.path)


def _archive_rejection(
    error: InferdromeArchiveRejected,
) -> ManagedEvidenceAdmissionRejected:
    if error.code is InferdromeArchiveErrorCode.CLEANUP_FAILED:
        code = ManagedEvidenceAdmissionErrorCode.CLEANUP_FAILED
    elif error.code is InferdromeArchiveErrorCode.ARCHIVE_INTEGRITY_MISMATCH:
        message = str(error)
        if "SHA-256" in message:
            code = ManagedEvidenceAdmissionErrorCode.ARCHIVE_SHA256_MISMATCH
        elif "size or file type" in message:
            code = ManagedEvidenceAdmissionErrorCode.ARCHIVE_SIZE_MISMATCH
        elif "requested bundle member" in message:
            code = ManagedEvidenceAdmissionErrorCode.ARCHIVE_MEMBER_MISSING
        else:
            code = ManagedEvidenceAdmissionErrorCode.BUNDLE_INTEGRITY_MISMATCH
    elif error.code is InferdromeArchiveErrorCode.ARCHIVE_LIMIT_EXCEEDED:
        code = ManagedEvidenceAdmissionErrorCode.ARCHIVE_UNSAFE
    else:
        code = ManagedEvidenceAdmissionErrorCode.ARCHIVE_UNSAFE
    return ManagedEvidenceAdmissionRejected(code, str(error))


def _bundle_rejection(
    error: InferdromeBundleRejected,
) -> ManagedEvidenceAdmissionRejected:
    if error.code is InferdromeBundleErrorCode.EVIDENCE_INELIGIBLE:
        code = (
            ManagedEvidenceAdmissionErrorCode.SYNTHETIC_EVIDENCE
            if "Synthetic" in str(error)
            else ManagedEvidenceAdmissionErrorCode.EVIDENCE_INELIGIBLE
        )
    elif error.code is InferdromeBundleErrorCode.INTEGRITY_MISMATCH:
        code = (
            ManagedEvidenceAdmissionErrorCode.BUNDLE_DIGEST_MISMATCH
            if "digest" in str(error).lower()
            else ManagedEvidenceAdmissionErrorCode.BUNDLE_INTEGRITY_MISMATCH
        )
    elif error.code is InferdromeBundleErrorCode.UNSUPPORTED_SCHEMA:
        code = ManagedEvidenceAdmissionErrorCode.BUNDLE_SCHEMA_INVALID
    elif error.code is InferdromeBundleErrorCode.SCHEMA_INVALID:
        code = (
            ManagedEvidenceAdmissionErrorCode.MALFORMED_JSONL
            if "record" in str(error).lower()
            else ManagedEvidenceAdmissionErrorCode.BUNDLE_SCHEMA_INVALID
        )
    elif error.code is InferdromeBundleErrorCode.UNSAFE_BUNDLE:
        code = ManagedEvidenceAdmissionErrorCode.ARCHIVE_UNSAFE
    else:
        code = ManagedEvidenceAdmissionErrorCode.TAMPERED_INPUT
    return ManagedEvidenceAdmissionRejected(code, str(error))


def _reject(
    code: ManagedEvidenceAdmissionErrorCode,
    message: str,
    *,
    path: str | None = None,
) -> NoReturn:
    raise ManagedEvidenceAdmissionRejected(code, message, path=path)


__all__ = [
    "A100_ARCHIVE_SHA256",
    "A100_ARCHIVE_SIZE_BYTES",
    "A100_BUNDLE_DIGEST",
    "A100_BUNDLE_MEMBER_PATH",
    "A100_CAPTURE_MANIFEST_SHA256",
    "A100_EXPECTED_P95_NS",
    "A100_RETROSPECTIVE_EXPECTATION",
    "A100RetrospectiveArchiveExpectation",
    "ManagedEvidenceAdmission",
    "ManagedEvidenceAdmissionErrorCode",
    "ManagedEvidenceAdmissionRejected",
    "admit_a100_qwen3_retrospective",
    "admit_managed_evidence_archive",
]
