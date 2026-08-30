from __future__ import annotations

import json
import os
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from exitspec.inferdrome_external_gate import (
    A100_ARCHIVE_SHA256,
    A100_EXPECTED_P95_NS,
    A100_RETROSPECTIVE_EXPECTATION,
    ManagedEvidenceAdmissionErrorCode,
    ManagedEvidenceAdmissionRejected,
    admit_a100_qwen3_retrospective,
)
from exitspec.inferdrome_profile_registry import (
    A100_MANAGED_PROFILE_ID,
    A100_MANAGED_PROFILE_SHA256,
    A100_QWEN3_PROFILE,
    LEGACY_A10_PROFILE,
    MANAGED_EVIDENCE_REGISTRY_VERSION,
    ManagedEvidenceProfileErrorCode,
    ManagedEvidenceProfileRejected,
    get_managed_evidence_profile,
    managed_evidence_profiles,
    validate_managed_profile_binding,
)


def test_registry_is_versioned_and_retains_legacy_a10_entry():
    profiles = managed_evidence_profiles()

    assert [profile.registry_version for profile in profiles] == [
        MANAGED_EVIDENCE_REGISTRY_VERSION,
        MANAGED_EVIDENCE_REGISTRY_VERSION,
    ]
    assert get_managed_evidence_profile(LEGACY_A10_PROFILE.profile_id).profile_id == (
        LEGACY_A10_PROFILE.profile_id
    )
    assert get_managed_evidence_profile(A100_MANAGED_PROFILE_ID).profile_sha256 == (
        A100_MANAGED_PROFILE_SHA256
    )
    assert {profile.profile_id for profile in profiles} == {
        LEGACY_A10_PROFILE.profile_id,
        A100_MANAGED_PROFILE_ID,
    }


def test_a100_profile_contains_compatibility_facts_not_private_run_data():
    serialized = json.dumps(asdict(A100_QWEN3_PROFILE), sort_keys=True)

    assert A100_QWEN3_PROFILE.model_id == "Qwen/Qwen3-8B"
    assert A100_QWEN3_PROFILE.configured_concurrency == 1
    assert A100_QWEN3_PROFILE.measured_requests == 96
    assert A100_QWEN3_PROFILE.benchmark_profile_arguments
    for private_value in ("GPU-", "/tmp/", "run-", "pid", "prompt", "response"):
        assert private_value not in serialized


def test_unknown_or_aliased_profile_ids_fail_closed():
    with pytest.raises(ManagedEvidenceProfileRejected) as unknown:
        get_managed_evidence_profile("managed-vllm-0.26-qwen3-8b-bf16-v1.json")
    assert unknown.value.code is ManagedEvidenceProfileErrorCode.PROFILE_UNKNOWN


def test_a100_profile_binding_rejects_an_extra_fact():
    binding = dict(A100_QWEN3_PROFILE.profile_binding or {})
    binding["profileId"] = binding["profile_id"]
    with pytest.raises(ManagedEvidenceProfileRejected) as rejected:
        validate_managed_profile_binding(A100_QWEN3_PROFILE, binding)
    assert rejected.value.code is ManagedEvidenceProfileErrorCode.PROFILE_FACTS_EXTRA


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        ("profile_id", ManagedEvidenceProfileErrorCode.PROFILE_ID_MISMATCH),
        ("remove_workload_id", ManagedEvidenceProfileErrorCode.PROFILE_FACTS_MISSING),
    ],
)
def test_a100_profile_binding_rejects_wrong_or_missing_facts(mutation, expected_code):
    binding = dict(A100_QWEN3_PROFILE.profile_binding or {})
    if mutation == "profile_id":
        binding["profile_id"] = "managed-vllm-0.26-qwen3-8b-bf16-v2"
    else:
        del binding["workload_id"]

    with pytest.raises(ManagedEvidenceProfileRejected) as rejected:
        validate_managed_profile_binding(A100_QWEN3_PROFILE, binding)
    assert rejected.value.code is expected_code


def test_exact_a100_archive_is_admitted_without_an_acceptance_verdict(tmp_path):
    inputs = _external_inputs_or_skip()

    admitted = admit_a100_qwen3_retrospective(
        inputs.archive,
        tmp_path / "extract",
        handoff_manifest_path=inputs.root / "handoff-manifest.json",
        publication_review_path=inputs.root / "publication-review.json",
        operational_summary_path=inputs.root / "operational-summary.json",
        profile_document_path=inputs.profile,
    )

    assert admitted.profile_id == A100_MANAGED_PROFILE_ID
    assert admitted.archive_sha256 == A100_ARCHIVE_SHA256
    assert admitted.bundle_digest == A100_RETROSPECTIVE_EXPECTATION.bundle_digest
    assert admitted.run_id == A100_RETROSPECTIVE_EXPECTATION.run_id
    assert admitted.recalculated.p95_ttft_ns == A100_EXPECTED_P95_NS
    assert admitted.recalculated.attempted_count == 96
    assert admitted.recalculated.successful_count == 96
    assert admitted.recalculated.failed_count == 0
    assert not hasattr(admitted, "verdict")
    assert not hasattr(admitted, "acceptance_verdict")


def test_archive_pin_mismatch_is_stable_and_cannot_be_overridden(tmp_path):
    inputs = _external_inputs_or_skip()
    wrong = replace(
        A100_RETROSPECTIVE_EXPECTATION,
        archive_sha256="sha256:" + "0" * 64,
    )

    with pytest.raises(ManagedEvidenceAdmissionRejected) as rejected:
        admit_a100_qwen3_retrospective(
            inputs.archive,
            tmp_path / "extract",
            handoff_manifest_path=inputs.root / "handoff-manifest.json",
            publication_review_path=inputs.root / "publication-review.json",
            operational_summary_path=inputs.root / "operational-summary.json",
            profile_document_path=inputs.profile,
            expectation=wrong,
        )
    assert (
        rejected.value.code is ManagedEvidenceAdmissionErrorCode.ARCHIVE_SHA256_MISMATCH
    )


class _ExternalInputs:
    def __init__(self, root: Path, archive: Path, profile: Path) -> None:
        self.root = root
        self.archive = archive
        self.profile = profile


def _external_inputs_or_skip() -> _ExternalInputs:
    archive = Path(
        os.environ.get(
            "EXITSPEC_INFERDROME_A100_ARCHIVE",
            "/Users/jayeshsuyal/Documents/Inferdrome/gpu-proof-retrieved/"
            "20260823T192609Z-a02bfd7c3f8b-dcb7a227/capture.tar.gz",
        )
    )
    root = Path(
        os.environ.get(
            "EXITSPEC_INFERDROME_A100_METADATA",
            "/Users/jayeshsuyal/Documents/Inferdrome/evidence/gpu/"
            "2026-08-23-qwen3-8b-a100-sxm4",
        )
    )
    profile = Path(
        os.environ.get(
            "EXITSPEC_INFERDROME_A100_PROFILE",
            "/Users/jayeshsuyal/Documents/Inferdrome/campaigns/v1/profiles/"
            "managed-vllm-0.26-qwen3-8b-bf16-v1.json",
        )
    )
    if not archive.is_file() or not root.is_dir() or not profile.is_file():
        pytest.skip("exact external A100 evidence inputs are not available")
    return _ExternalInputs(root, archive, profile)
