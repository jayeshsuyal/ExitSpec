"""Deterministic three-contract demonstration for the retained Inferdrome A10 run."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Final, Literal, NoReturn, Sequence

from pydantic import Field, field_validator, model_validator

from .canonical import canonical_json_bytes
from .confirmations import (
    ConfirmationDecision,
    ContractConfirmation,
    record_confirmation,
    require_affirmative_confirmation,
)
from .contracts import freeze_confirmed_contract, verify_contract_digest
from .inferdrome_archive import extract_pinned_inferdrome_archive
from .inferdrome_bundle import (
    InferdromeBundleErrorCode,
    InferdromeBundleRejected,
    verify_inferdrome_bundle,
)
from .inferdrome_managed_import import (
    InferdromeManagedImportResult,
    import_managed_inferdrome_bundle,
)
from .inferdrome_external_contract import validate_managed_contract_context
from .inferdrome_managed_reporting import (
    render_managed_inferdrome_evidence_pack,
)
from .inferdrome_profile import (
    CAPTURE_PRODUCER_COMMIT,
    LOCAL_GPU_PROOF_SCHEMA_ID,
    LOCAL_GPU_PROOF_SCHEMA_SHA256,
    MANAGED_PROFILE_ID,
    MANAGED_PROFILE_SHA256,
    PINNED_ARCHIVE_SHA256,
    PINNED_BUNDLE_DIGEST,
    PINNED_NATIVE_TTFT_P95_NS,
    PINNED_RUN_ID,
)
from .inferdrome_reporting_v2 import (
    InferdromeManagedReceiptV2,
    ManagedApplicabilityCode,
    managed_receipt_sha256,
    validate_managed_receipt,
)
from .models import POCContract, SHA256_PATTERN, FrozenExitSpecModel


MANAGED_DEMO_SCHEMA_VERSION: Final = "exitspec.inferdrome-a10-demo.v1"
MANAGED_DEMO_RECEIPT_TIME: Final = datetime(
    2026,
    8,
    21,
    12,
    0,
    tzinfo=UTC,
)
MANAGED_DEMO_REQUEST_PLAN_DIGEST: Final = (
    "sha256:0fb852366933598da4139114f416b441c52d2c83cae07b7d8938bd482a12fc8e"
)
MANAGED_DEMO_WORKLOAD_DIGEST: Final = (
    "sha256:22bf3389cc29ee946ae567870d7f8d7b458594224542a796e8990c15b1cfcd63"
)
MANAGED_DEMO_NATIVE_SCHEMA_FINGERPRINT: Final = (
    "sha256:3a4fdee6fe9b45ce5b42c41fd3bfc6614245a36ecfe6f94de92b59717a136abb"
)
MANAGED_DEMO_MODEL: Final = "Qwen/Qwen2.5-0.5B-Instruct"
MANAGED_DEMO_REVISION: Final = "7ae557604adf67be50417f59c2c2f167def9a775"
MANAGED_DEMO_ENDPOINT: Final = "http://127.0.0.1:18080/"
_MAX_DEMO_ARTIFACT_BYTES: Final = 1_048_576
_MAX_DEMO_FILES: Final = 32
_MAX_DEMO_DIRECTORIES: Final = 16


@dataclass(frozen=True, slots=True)
class ManagedDemoCaseDefinition:
    """One deterministic customer question asked of the same retained run."""

    case_id: Literal["pass", "fail", "not-proven"]
    contract_id: str
    threshold_ns: int
    configured_max_concurrency: int
    metric_definition: Literal[
        "vllm_first_choices_event_v0_26",
        "first_nonempty_choices_delta_content_v1",
    ]


MANAGED_DEMO_CASES: Final = (
    ManagedDemoCaseDefinition(
        case_id="pass",
        contract_id="inferdrome-a10-retrospective-pass",
        threshold_ns=20_000_000,
        configured_max_concurrency=4,
        metric_definition="vllm_first_choices_event_v0_26",
    ),
    ManagedDemoCaseDefinition(
        case_id="fail",
        contract_id="inferdrome-a10-retrospective-fail",
        threshold_ns=10_000_000,
        configured_max_concurrency=4,
        metric_definition="vllm_first_choices_event_v0_26",
    ),
    ManagedDemoCaseDefinition(
        case_id="not-proven",
        contract_id="inferdrome-a10-retrospective-content-ttft",
        threshold_ns=20_000_000,
        configured_max_concurrency=4,
        metric_definition="first_nonempty_choices_delta_content_v1",
    ),
)


class ManagedDemoArtifactV1(FrozenExitSpecModel):
    """One path-safe artifact and its exact-byte digest."""

    path: str = Field(pattern=r"^[a-z0-9][a-z0-9._/-]{0,199}$")
    sha256: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")

    @field_validator("path")
    @classmethod
    def require_safe_relative_path(cls, value: str) -> str:
        parsed = PurePosixPath(value)
        if parsed.is_absolute() or ".." in parsed.parts or "." in parsed.parts:
            raise ValueError("Managed demo artifact path is unsafe.")
        return value


class ManagedDemoCaseV1(FrozenExitSpecModel):
    """Portable publication record for one accepted evaluation."""

    case_id: Literal["pass", "fail", "not-proven"]
    acceptance_verdict: Literal["PASS", "FAIL", "NOT_PROVEN"]
    applicability_codes: tuple[ManagedApplicabilityCode, ...]
    threshold_ns: int = Field(gt=0)
    required_configured_max_concurrency: int = Field(gt=0)
    requested_metric_definition: Literal[
        "vllm_first_choices_event_v0_26",
        "first_nonempty_choices_delta_content_v1",
    ]
    recalculated_ttft_p95_ns: int = Field(ge=0)
    contract_hash: str = Field(pattern=SHA256_PATTERN)
    receipt_id: str = Field(pattern=r"^irc2_[a-f0-9]{64}$")
    receipt_sha256: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    contract: ManagedDemoArtifactV1
    confirmation: ManagedDemoArtifactV1
    receipt: ManagedDemoArtifactV1
    evidence_pack: ManagedDemoArtifactV1

    @model_validator(mode="after")
    def require_receipt_artifact_digest(self) -> "ManagedDemoCaseV1":
        if self.receipt.sha256 != self.receipt_sha256:
            raise ValueError("Managed demo receipt digests disagree.")
        return self


class ManagedDemoRejectionV1(FrozenExitSpecModel):
    """Ingestion result only; it deliberately carries no acceptance verdict."""

    fixture_id: Literal["corrupted-bundle-copy", "synthetic-run"]
    ingestion_status: Literal["REJECTED"] = "REJECTED"
    reason_code: Literal["INTEGRITY_MISMATCH", "EVIDENCE_INELIGIBLE"]
    acceptance_verdict: None = None
    receipt_emitted: Literal[False] = False


class InferdromeManagedDemoManifestV1(FrozenExitSpecModel):
    """Portable, hash-addressed index for the complete demonstration output."""

    schema_version: Literal["exitspec.inferdrome-a10-demo.v1"] = (
        MANAGED_DEMO_SCHEMA_VERSION
    )
    purpose: Literal["CONFORMANCE_DEMONSTRATION"]
    generated_at: datetime
    archive_sha256: Literal[
        "sha256:f2408fd0649a7c79f5962872003781ebb9c878b802db27d633cf246f13b6f424"
    ]
    producer_commit: Literal["c08b46d9fbd87477f45d130aa3c63615937c4dc3"]
    run_id: Literal["run-533c9f5f783958fb6077069a6c577144"]
    bundle_digest: Literal[
        "sha256:bae216f2165eb06ae2e0f14d3cd852f8e0ebb381bf1f68c71072769b3c0c1675"
    ]
    recalculated_ttft_p95_ns: Literal[14797213]
    raw_archive_included: Literal[False] = False
    exact_archive_gate: Literal["LOCAL_MANDATORY_PUBLICATION_GATED"]
    readme: ManagedDemoArtifactV1
    cases: tuple[ManagedDemoCaseV1, ...]
    rejections: tuple[ManagedDemoRejectionV1, ...]

    @field_validator("generated_at")
    @classmethod
    def require_utc_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Managed demo time must be timezone-aware.")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def require_canonical_demo_matrix(self) -> "InferdromeManagedDemoManifestV1":
        if tuple(case.case_id for case in self.cases) != (
            "pass",
            "fail",
            "not-proven",
        ) or tuple(case.acceptance_verdict for case in self.cases) != (
            "PASS",
            "FAIL",
            "NOT_PROVEN",
        ):
            raise ValueError("Managed demo cases are incomplete or out of order.")
        if tuple(case.requested_metric_definition for case in self.cases) != (
            "vllm_first_choices_event_v0_26",
            "vllm_first_choices_event_v0_26",
            "first_nonempty_choices_delta_content_v1",
        ):
            raise ValueError("Managed demo metric questions are not canonical.")
        if tuple(item.fixture_id for item in self.rejections) != (
            "corrupted-bundle-copy",
            "synthetic-run",
        ):
            raise ValueError("Managed demo rejections are incomplete or out of order.")
        return self


@dataclass(frozen=True, slots=True)
class VerifiedManagedDemoDirectory:
    """A portable demonstration directory whose every indexed byte was checked."""

    root: Path
    manifest: InferdromeManagedDemoManifestV1
    manifest_sha256: str


def build_managed_demo_contract(
    definition: ManagedDemoCaseDefinition,
) -> tuple[POCContract, ContractConfirmation]:
    """Freeze one exact retrospective customer criterion before evaluation."""

    approved = POCContract.model_validate(
        {
            "id": definition.contract_id,
            "version": "1.0.0",
            "status": "APPROVED",
            "created_at": "2026-08-21T10:00:00Z",
            "approved_at": "2026-08-21T10:30:00Z",
            "customer": "Retrospective GPU Conformance Co.",
            "use_case": "Evaluate one retained managed vLLM performance run.",
            "target_system": {
                "provider": "inferdrome-managed-vllm",
                "endpoint_class": "retained-loopback-vllm-benchmark",
                "model": MANAGED_DEMO_MODEL,
            },
            "workload": {
                "fixture_path": "external://inferdrome/a10/workload",
                "sha256": MANAGED_DEMO_WORKLOAD_DIGEST.removeprefix("sha256:"),
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
                        "100-record workload must have native vLLM p95 TTFT "
                        "below {1} ns and measured-record error rate below 1%."
                    ).format(
                        definition.configured_max_concurrency,
                        definition.threshold_ns,
                    ),
                    "ttft_p95": {
                        "metric": "time_to_first_token",
                        "definition_id": definition.metric_definition,
                        "aggregation": "p95",
                        "unit": "nanoseconds",
                        "operator": "lt",
                        "threshold_ns": definition.threshold_ns,
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
                        "native_schema_fingerprint": (
                            MANAGED_DEMO_NATIVE_SCHEMA_FINGERPRINT
                        ),
                        "managed_profile_id": MANAGED_PROFILE_ID,
                        "managed_profile_sha256": MANAGED_PROFILE_SHA256,
                        "local_gpu_proof_schema_id": LOCAL_GPU_PROOF_SCHEMA_ID,
                        "local_gpu_proof_schema_sha256": (
                            LOCAL_GPU_PROOF_SCHEMA_SHA256
                        ),
                        "request_plan_digest": (MANAGED_DEMO_REQUEST_PLAN_DIGEST),
                        "workload_digest": MANAGED_DEMO_WORKLOAD_DIGEST,
                        "target_model": MANAGED_DEMO_MODEL,
                        "target_model_revision": MANAGED_DEMO_REVISION,
                        "target_tokenizer_revision": MANAGED_DEMO_REVISION,
                        "target_endpoint": MANAGED_DEMO_ENDPOINT,
                        "configured_max_concurrency": (
                            definition.configured_max_concurrency
                        ),
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
        confirmer_identity="retrospective-customer-reviewer",
        decision=ConfirmationDecision.CONFIRM,
        agreement_acknowledged=True,
        rationale="The retrospective criterion is correct for this demonstration.",
        idempotency_key=f"{definition.contract_id}-confirmation",
        decided_at=datetime(2026, 8, 21, 11, 0, tzinfo=UTC),
    )
    frozen = freeze_confirmed_contract(
        approved,
        confirmation,
        datetime(2026, 8, 21, 11, 30, tzinfo=UTC),
    )
    return frozen, confirmation


def generate_managed_a10_demo(
    archive_path: Path,
    output_directory: Path,
) -> VerifiedManagedDemoDirectory:
    """Generate deterministic artifacts without modifying the producer archive."""

    if not isinstance(archive_path, Path) or not isinstance(output_directory, Path):
        raise TypeError("archive_path and output_directory must be Path objects.")
    if output_directory.exists():
        raise ValueError("Managed demo output directory must not already exist.")
    output_directory.parent.mkdir(parents=True, exist_ok=True)

    frozen_cases = tuple(
        (definition, *build_managed_demo_contract(definition))
        for definition in MANAGED_DEMO_CASES
    )
    with tempfile.TemporaryDirectory(prefix="exitspec-inferdrome-a10-") as raw:
        extracted = extract_pinned_inferdrome_archive(
            archive_path,
            Path(raw) / "archive",
        )
        evaluated = tuple(
            (
                definition,
                contract,
                confirmation,
                import_managed_inferdrome_bundle(
                    extracted.bundle_path,
                    contract,
                    confirmation,
                    received_at=MANAGED_DEMO_RECEIPT_TIME,
                ),
            )
            for definition, contract, confirmation in frozen_cases
        )
        rejections = (
            _demonstrate_rejection(
                "corrupted-bundle-copy",
                extracted.corrupted_bundle_path,
                InferdromeBundleErrorCode.INTEGRITY_MISMATCH,
            ),
            _demonstrate_rejection(
                "synthetic-run",
                extracted.synthetic_bundle_path,
                InferdromeBundleErrorCode.EVIDENCE_INELIGIBLE,
            ),
        )

    artifacts: dict[str, bytes] = {}
    case_records: list[ManagedDemoCaseV1] = []
    for definition, contract, confirmation, result in evaluated:
        case_records.append(
            _stage_case_artifacts(
                artifacts,
                definition,
                contract,
                confirmation,
                result,
            )
        )
    readme_path = "readme.md"
    artifacts[readme_path] = _demo_readme()
    manifest = InferdromeManagedDemoManifestV1(
        purpose="CONFORMANCE_DEMONSTRATION",
        generated_at=MANAGED_DEMO_RECEIPT_TIME,
        archive_sha256=PINNED_ARCHIVE_SHA256,
        producer_commit=CAPTURE_PRODUCER_COMMIT,
        run_id=PINNED_RUN_ID,
        bundle_digest=PINNED_BUNDLE_DIGEST,
        recalculated_ttft_p95_ns=PINNED_NATIVE_TTFT_P95_NS,
        exact_archive_gate="LOCAL_MANDATORY_PUBLICATION_GATED",
        readme=_artifact(readme_path, artifacts[readme_path]),
        cases=tuple(case_records),
        rejections=rejections,
    )
    artifacts["manifest.json"] = canonical_json_bytes(manifest.model_dump(mode="json"))
    _write_new_directory(output_directory, artifacts)
    return verify_managed_demo_directory(output_directory)


def verify_managed_demo_directory(
    root: Path,
) -> VerifiedManagedDemoDirectory:
    """Verify checked-in demo artifacts without requiring the raw A10 archive."""

    if not isinstance(root, Path):
        raise TypeError("Managed demo root must be a Path object.")
    try:
        root_stat = root.lstat()
    except OSError as error:
        raise ValueError(
            "Managed demo root must be an existing real directory."
        ) from error
    if not stat.S_ISDIR(root_stat.st_mode):
        raise ValueError("Managed demo root must be an existing real directory.")
    actual_paths = _scan_demo_tree(root)
    manifest_bytes = _read_regular_file(root, "manifest.json")
    manifest_payload = _load_strict_json_object(
        manifest_bytes,
        "managed demo manifest",
    )
    manifest = InferdromeManagedDemoManifestV1.model_validate(manifest_payload)
    if canonical_json_bytes(manifest.model_dump(mode="json")) != manifest_bytes:
        raise ValueError("Managed demo manifest is not canonical JSON.")

    expected_paths = {"manifest.json", manifest.readme.path}
    _verify_artifact(root, manifest.readme)
    for case in manifest.cases:
        refs = (
            case.contract,
            case.confirmation,
            case.receipt,
            case.evidence_pack,
        )
        for artifact in refs:
            if artifact.path in expected_paths:
                raise ValueError("Managed demo artifact path is duplicated.")
            expected_paths.add(artifact.path)
            _verify_artifact(root, artifact)
        _verify_case(root, manifest, case)

    if actual_paths != expected_paths or _scan_demo_tree(root) != actual_paths:
        raise ValueError("Managed demo directory contains unindexed artifacts.")
    return VerifiedManagedDemoDirectory(
        root=root,
        manifest=manifest,
        manifest_sha256=_tagged_sha256(manifest_bytes),
    )


def _verify_case(
    root: Path,
    manifest: InferdromeManagedDemoManifestV1,
    case: ManagedDemoCaseV1,
) -> None:
    contract_bytes = _read_regular_file(root, case.contract.path)
    confirmation_bytes = _read_regular_file(root, case.confirmation.path)
    receipt_bytes = _read_regular_file(root, case.receipt.path)
    contract = POCContract.model_validate(
        _load_strict_json_object(
            contract_bytes,
            "managed demo contract",
        )
    )
    confirmation = ContractConfirmation.model_validate(
        _load_strict_json_object(
            confirmation_bytes,
            "managed demo confirmation",
        )
    )
    receipt = validate_managed_receipt(
        InferdromeManagedReceiptV2.model_validate(
            _load_strict_json_object(
                receipt_bytes,
                "managed demo receipt",
            )
        )
    )
    if (
        canonical_json_bytes(contract.model_dump(mode="json")) != contract_bytes
        or canonical_json_bytes(confirmation.model_dump(mode="json"))
        != confirmation_bytes
        or canonical_json_bytes(receipt.model_dump(mode="json")) != receipt_bytes
    ):
        raise ValueError("Managed demo typed artifact is not canonical JSON.")
    if not verify_contract_digest(contract):
        raise ValueError("Managed demo contract digest is invalid.")
    require_affirmative_confirmation(contract, confirmation)
    _require_receipt_contract_bindings(receipt, contract, confirmation)
    if (
        contract.canonical_hash != case.contract_hash
        or contract.confirmation_id != confirmation.confirmation_id
        or receipt.contract_hash != case.contract_hash
        or receipt.bundle_digest != manifest.bundle_digest
        or receipt.run_id != manifest.run_id
        or receipt.receipt_id != case.receipt_id
        or receipt.acceptance_verdict != case.acceptance_verdict
        or receipt.applicability_codes != case.applicability_codes
        or receipt.metric.threshold_ns != case.threshold_ns
        or receipt.metric.requested_definition_id != case.requested_metric_definition
        or receipt.metric.recalculated_value_ns != case.recalculated_ttft_p95_ns
        or receipt.population.required_configured_max_concurrency
        != case.required_configured_max_concurrency
        or managed_receipt_sha256(receipt) != case.receipt_sha256
    ):
        raise ValueError("Managed demo case bindings are inconsistent.")


def _stage_case_artifacts(
    artifacts: dict[str, bytes],
    definition: ManagedDemoCaseDefinition,
    contract: POCContract,
    confirmation: ContractConfirmation,
    result: InferdromeManagedImportResult,
) -> ManagedDemoCaseV1:
    prefix = definition.case_id
    paths = {
        "contract": f"contracts/{prefix}.frozen.json",
        "confirmation": f"confirmations/{prefix}.confirmation.json",
        "receipt": f"receipts/{prefix}.receipt.json",
        "evidence_pack": f"evidence-packs/{prefix}.html",
    }
    artifacts[paths["contract"]] = canonical_json_bytes(
        contract.model_dump(mode="json")
    )
    artifacts[paths["confirmation"]] = canonical_json_bytes(
        confirmation.model_dump(mode="json")
    )
    artifacts[paths["receipt"]] = canonical_json_bytes(
        result.receipt.model_dump(mode="json")
    )
    artifacts[paths["evidence_pack"]] = render_managed_inferdrome_evidence_pack(
        contract=contract,
        result=result,
    )
    if contract.canonical_hash is None:
        raise ValueError("Managed demo contract was not frozen.")
    receipt_sha256 = managed_receipt_sha256(result.receipt)
    return ManagedDemoCaseV1(
        case_id=definition.case_id,
        acceptance_verdict=result.verdict.value,
        applicability_codes=result.applicability.issues,
        threshold_ns=definition.threshold_ns,
        required_configured_max_concurrency=(definition.configured_max_concurrency),
        requested_metric_definition=definition.metric_definition,
        recalculated_ttft_p95_ns=result.recalculated.p95_ttft_ns,
        contract_hash=contract.canonical_hash,
        receipt_id=result.receipt.receipt_id,
        receipt_sha256=receipt_sha256,
        contract=_artifact(paths["contract"], artifacts[paths["contract"]]),
        confirmation=_artifact(
            paths["confirmation"],
            artifacts[paths["confirmation"]],
        ),
        receipt=_artifact(paths["receipt"], artifacts[paths["receipt"]]),
        evidence_pack=_artifact(
            paths["evidence_pack"],
            artifacts[paths["evidence_pack"]],
        ),
    )


def _demonstrate_rejection(
    fixture_id: Literal["corrupted-bundle-copy", "synthetic-run"],
    bundle_path: Path,
    expected_code: InferdromeBundleErrorCode,
) -> ManagedDemoRejectionV1:
    try:
        verify_inferdrome_bundle(
            bundle_path,
            require_customer_eligible=True,
        )
    except InferdromeBundleRejected as error:
        if error.code is not expected_code:
            raise ValueError(
                "Managed demo fixture produced an unexpected rejection code."
            ) from error
        return ManagedDemoRejectionV1(
            fixture_id=fixture_id,
            reason_code=error.code.value,
        )
    raise ValueError("Managed demo rejection fixture was unexpectedly accepted.")


def _demo_readme() -> bytes:
    return (
        "# Inferdrome A10 retrospective conformance demo\n\n"
        "These portable artifacts prove ExitSpec's deterministic consumer behavior "
        "for one checksum-pinned Inferdrome bundle. The raw archive is not vendored; "
        "its exact local gate remains publication-gated.\n\n"
        "The three customer contracts ask the unchanged run three different "
        "questions and produce PASS, FAIL, and semantic NOT_PROVEN. Corrupt and "
        "synthetic "
        "fixtures are ingestion rejections and receive no acceptance verdict or "
        "receipt.\n\n"
        "Regenerate into a new directory with:\n\n"
        "```bash\n"
        "PYTHONPATH=src python -m exitspec.inferdrome_managed_demo "
        "--archive /absolute/path/to/capture.tar.gz --output /absolute/new/output\n"
        "```\n"
    ).encode("utf-8")


def _artifact(path: str, content: bytes) -> ManagedDemoArtifactV1:
    return ManagedDemoArtifactV1(path=path, sha256=_tagged_sha256(content))


def _write_new_directory(root: Path, artifacts: dict[str, bytes]) -> None:
    staging = Path(
        tempfile.mkdtemp(
            prefix=f".{root.name}-",
            dir=root.parent,
        )
    )
    try:
        for relative, content in sorted(artifacts.items()):
            destination = staging.joinpath(*PurePosixPath(relative).parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
        os.replace(staging, root)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _verify_artifact(root: Path, artifact: ManagedDemoArtifactV1) -> None:
    content = _read_regular_file(root, artifact.path)
    if _tagged_sha256(content) != artifact.sha256:
        raise ValueError("Managed demo artifact digest is invalid.")


def _require_receipt_contract_bindings(
    receipt: InferdromeManagedReceiptV2,
    contract: POCContract,
    confirmation: ContractConfirmation,
) -> None:
    context = validate_managed_contract_context(contract, confirmation)
    criterion = context.criterion
    identity = criterion.evidence_identity
    checks = (
        receipt.criterion_id == criterion.id,
        receipt.evidence_schema_version == identity.evidence_schema_version,
        receipt.producer_name == identity.producer_name,
        receipt.producer_version == identity.producer_version,
        receipt.adapter_id == identity.adapter_id,
        receipt.adapter_version == identity.adapter_version,
        receipt.native_schema_fingerprint == identity.native_schema_fingerprint,
        receipt.managed_profile_id == identity.managed_profile_id,
        receipt.managed_profile_sha256 == identity.managed_profile_sha256,
        receipt.local_gpu_proof_schema_id == identity.local_gpu_proof_schema_id,
        receipt.local_gpu_proof_schema_sha256 == identity.local_gpu_proof_schema_sha256,
        receipt.requested_request_plan_digest == identity.request_plan_digest,
        receipt.requested_workload_digest == identity.workload_digest,
        receipt.binding_mode == identity.binding_mode,
        receipt.producer_contract_link == identity.producer_contract_link,
        receipt.target.requested_model == identity.target_model,
        receipt.target.requested_model_revision == identity.target_model_revision,
        receipt.target.requested_tokenizer_revision
        == identity.target_tokenizer_revision,
        receipt.target.requested_endpoint == identity.target_endpoint,
        receipt.metric.metric == criterion.ttft_p95.metric,
        receipt.metric.aggregation == criterion.ttft_p95.aggregation,
        receipt.metric.unit == criterion.ttft_p95.unit,
        receipt.metric.operator == criterion.ttft_p95.operator,
        receipt.metric.requested_definition_id == criterion.ttft_p95.definition_id,
        receipt.metric.requested_reducer_id == criterion.ttft_p95.reducer_id,
        receipt.metric.requested_population == criterion.ttft_p95.population,
        receipt.metric.threshold_ns == criterion.ttft_p95.threshold_ns,
        receipt.population.required_attempts == criterion.error_rate.exact_attempts,
        receipt.population.required_successful_samples
        == criterion.ttft_p95.minimum_successful_samples,
        receipt.population.required_configured_max_concurrency
        == identity.configured_max_concurrency,
        receipt.population.required_warmup_requests == identity.warmup_requests,
        receipt.population.error_numerator == criterion.error_rate.numerator,
        receipt.population.error_denominator == criterion.error_rate.denominator,
        receipt.population.error_threshold_basis_points
        == criterion.error_rate.threshold_basis_points,
        receipt.assurance.temporal_assurance == identity.chronology,
        receipt.assurance.contract_preceded_measurement is False,
    )
    if not all(checks):
        raise ValueError(
            "Managed demo receipt criterion fields disagree with contract."
        )


def _scan_demo_tree(root: Path) -> set[str]:
    """Reject unsafe entries and bound the tree before reading artifact contents."""

    files: set[str] = set()
    pending = [(root, PurePosixPath())]
    directory_count = 0
    while pending:
        directory, prefix = pending.pop()
        directory_count += 1
        if directory_count > _MAX_DEMO_DIRECTORIES:
            raise ValueError("Managed demo directory exceeds its directory limit.")
        try:
            with os.scandir(directory) as iterator:
                entries = tuple(iterator)
        except OSError as error:
            raise ValueError(
                "Managed demo directory cannot be safely enumerated."
            ) from error
        for entry in entries:
            relative = prefix / entry.name
            if entry.is_symlink():
                raise ValueError("Managed demo directory contains a symbolic link.")
            try:
                entry_stat = entry.stat(follow_symlinks=False)
            except OSError as error:
                raise ValueError(
                    "Managed demo artifact cannot be safely inspected."
                ) from error
            if stat.S_ISDIR(entry_stat.st_mode):
                pending.append((Path(entry.path), relative))
                continue
            if not stat.S_ISREG(entry_stat.st_mode):
                raise ValueError("Managed demo directory contains a special file.")
            if entry_stat.st_size > _MAX_DEMO_ARTIFACT_BYTES:
                raise ValueError("Managed demo artifact exceeds its byte limit.")
            files.add(relative.as_posix())
            if len(files) > _MAX_DEMO_FILES:
                raise ValueError("Managed demo directory exceeds its file limit.")
    return files


def _read_regular_file(root: Path, relative_path: str) -> bytes:
    """Read one bounded artifact through no-follow directory descriptors."""

    parsed = PurePosixPath(relative_path)
    if (
        parsed.is_absolute()
        or not parsed.parts
        or "." in parsed.parts
        or ".." in parsed.parts
    ):
        raise ValueError("Managed demo artifact path is unsafe.")
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        raise ValueError("Managed demo verification requires no-follow file support.")
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    file_flags = os.O_RDONLY | os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        directory_flags |= os.O_CLOEXEC
        file_flags |= os.O_CLOEXEC
    opened: list[int] = []
    try:
        current = os.open(root, directory_flags)
        opened.append(current)
        for part in parsed.parts[:-1]:
            current = os.open(part, directory_flags, dir_fd=current)
            opened.append(current)
        file_descriptor = os.open(parsed.parts[-1], file_flags, dir_fd=current)
        opened.append(file_descriptor)
        before = os.fstat(file_descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("Managed demo artifact is missing or unsafe.")
        if before.st_size > _MAX_DEMO_ARTIFACT_BYTES:
            raise ValueError("Managed demo artifact exceeds its byte limit.")
        with os.fdopen(os.dup(file_descriptor), "rb") as stream:
            content = stream.read(_MAX_DEMO_ARTIFACT_BYTES + 1)
        after = os.fstat(file_descriptor)
        identity_before = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        )
        identity_after = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        if (
            len(content) > _MAX_DEMO_ARTIFACT_BYTES
            or len(content) != before.st_size
            or identity_before != identity_after
        ):
            raise ValueError("Managed demo artifact changed during verification.")
        return content
    except OSError as error:
        raise ValueError("Managed demo artifact is missing or unsafe.") from error
    finally:
        for descriptor in reversed(opened):
            os.close(descriptor)


def _load_strict_json_object(content: bytes, label: str) -> dict[str, Any]:
    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError(f"{label} contains duplicate JSON keys.")
            value[key] = item
        return value

    def reject_constant(value: str) -> NoReturn:
        raise ValueError(f"{label} contains a non-finite number: {value}.")

    def bounded_integer(value: str) -> int:
        if len(value.lstrip("-")) > 20:
            raise ValueError(f"{label} contains an oversized integer.")
        return int(value)

    def reject_float(value: str) -> NoReturn:
        raise ValueError(f"{label} contains an unsupported JSON number: {value}.")

    try:
        parsed = json.loads(
            content,
            object_pairs_hook=unique_object,
            parse_constant=reject_constant,
            parse_int=bounded_integer,
            parse_float=reject_float,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} is not valid JSON.") from error
    if type(parsed) is not dict:
        raise ValueError(f"{label} must be a JSON object.")
    return parsed


def _tagged_sha256(content: bytes) -> str:
    return f"sha256:{hashlib.sha256(content).hexdigest()}"


def main(argv: Sequence[str] | None = None) -> int:
    """Generate the exact local demonstration and print publication identifiers."""

    parser = argparse.ArgumentParser(
        description="Generate ExitSpec's retained Inferdrome A10 conformance demo."
    )
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    verified = generate_managed_a10_demo(
        args.archive.expanduser().resolve(),
        args.output.expanduser().resolve(),
    )
    print(
        json.dumps(
            {
                "manifest_sha256": verified.manifest_sha256,
                "contracts": {
                    case.case_id: case.contract_hash for case in verified.manifest.cases
                },
                "receipts": {
                    case.case_id: case.receipt_sha256
                    for case in verified.manifest.cases
                },
                "rejections": {
                    item.fixture_id: item.reason_code
                    for item in verified.manifest.rejections
                },
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "InferdromeManagedDemoManifestV1",
    "MANAGED_DEMO_CASES",
    "MANAGED_DEMO_ENDPOINT",
    "MANAGED_DEMO_MODEL",
    "MANAGED_DEMO_NATIVE_SCHEMA_FINGERPRINT",
    "MANAGED_DEMO_RECEIPT_TIME",
    "MANAGED_DEMO_REQUEST_PLAN_DIGEST",
    "MANAGED_DEMO_REVISION",
    "MANAGED_DEMO_SCHEMA_VERSION",
    "MANAGED_DEMO_WORKLOAD_DIGEST",
    "ManagedDemoCaseDefinition",
    "ManagedDemoCaseV1",
    "ManagedDemoRejectionV1",
    "VerifiedManagedDemoDirectory",
    "build_managed_demo_contract",
    "generate_managed_a10_demo",
    "verify_managed_demo_directory",
]
