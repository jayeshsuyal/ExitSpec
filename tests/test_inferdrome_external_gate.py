from __future__ import annotations

import json
import inspect
import os
import copy
from dataclasses import asdict
from pathlib import Path

import pytest

import exitspec.inferdrome_external_gate as external_gate_module
import exitspec.inferdrome_profile_registry as profile_registry_module
from exitspec.inferdrome_archive import ExtractedInferdromeArchiveMember
from exitspec.inferdrome_bundle import (
    InferdromeBundleErrorCode,
    InferdromeBundleRejected,
)
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
    A100_SERVER_ARGV_TEMPLATE,
    LEGACY_A10_PROFILE,
    MANAGED_EVIDENCE_REGISTRY_VERSION,
    ManagedEvidenceProfileErrorCode,
    ManagedEvidenceProfileRejected,
    get_managed_evidence_profile,
    managed_evidence_profiles,
    validate_managed_evidence_profile,
    validate_managed_profile_binding,
)
from exitspec.inferdrome_managed_profile import (
    ManagedInferdromeProfileError,
    validate_managed_invocation_profile,
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


def test_public_gate_has_no_expectation_override_authority(tmp_path):
    assert (
        "expectation"
        not in inspect.signature(admit_a100_qwen3_retrospective).parameters
    )
    with pytest.raises(TypeError):
        admit_a100_qwen3_retrospective(
            tmp_path / "archive.tar.gz",
            tmp_path / "extract",
            handoff_manifest_path=tmp_path / "handoff.json",
            publication_review_path=tmp_path / "review.json",
            operational_summary_path=tmp_path / "summary.json",
            profile_document_path=tmp_path / "profile.json",
            expectation=A100_RETROSPECTIVE_EXPECTATION,
        )


def test_legacy_profile_cannot_enter_the_new_managed_profile_api():
    with pytest.raises(ManagedEvidenceProfileRejected) as rejected:
        validate_managed_evidence_profile(
            LEGACY_A10_PROFILE,
            descriptor={},
            resolved={},
            plan={},
            environment={},
            execution={},
            invocation={},
        )
    assert (
        rejected.value.code
        is ManagedEvidenceProfileErrorCode.LEGACY_PROFILE_REQUIRES_LEGACY_PATH
    )


@pytest.mark.parametrize("mutation", ["selected_gpu", "run_id", "chronology"])
def test_a100_uses_mature_gpu_proof_semantics_for_adversarial_mutations(mutation):
    """Synthetic structure-only coverage; never represents real evidence."""

    invocation = _synthetic_a100_invocation()
    proof = invocation["local_gpu_proof"]
    if mutation == "selected_gpu":
        proof["selected_gpu_indices"] = [1]
    elif mutation == "run_id":
        proof["run_id"] = "run-" + "b" * 32
    else:
        proof["server"]["ready_at"] = "2026-08-19T23:59:00Z"

    with pytest.raises(ManagedInferdromeProfileError):
        validate_managed_invocation_profile(
            invocation,
            profile_document=profile_registry_module._managed_validation_profile_document(
                A100_QWEN3_PROFILE
            ),
        )


def test_a100_mature_gpu_proof_specialization_accepts_only_exact_synthetic_shape():
    """Synthetic structure-only coverage; never represents real evidence."""

    validate_managed_invocation_profile(
        _synthetic_a100_invocation(),
        profile_document=profile_registry_module._managed_validation_profile_document(
            A100_QWEN3_PROFILE
        ),
    )


def test_post_extraction_rejection_removes_owned_destination(monkeypatch, tmp_path):
    destination = tmp_path / "extract"

    def fake_extract(archive_path, root, **kwargs):
        member = root / "capture" / "runs" / "synthetic" / "bundle"
        member.mkdir(parents=True)
        return ExtractedInferdromeArchiveMember(
            root=root,
            archive_sha256=A100_ARCHIVE_SHA256,
            member_path="capture/runs/synthetic/bundle",
            member=member,
            member_count=1,
            file_count=0,
            directory_count=1,
            expanded_bytes=0,
        )

    monkeypatch.setattr(
        external_gate_module,
        "_validate_profile_document",
        lambda *args: None,
    )
    monkeypatch.setattr(
        external_gate_module,
        "_read_metadata",
        lambda *args: {},
    )
    monkeypatch.setattr(
        external_gate_module,
        "_validate_handoff",
        lambda *args: None,
    )
    monkeypatch.setattr(
        external_gate_module,
        "_validate_publication_review",
        lambda *args: None,
    )
    monkeypatch.setattr(
        external_gate_module,
        "_validate_operational_summary",
        lambda *args: None,
    )
    monkeypatch.setattr(
        external_gate_module,
        "extract_external_inferdrome_archive",
        fake_extract,
    )

    def reject_bundle(*args, **kwargs):
        raise InferdromeBundleRejected(
            InferdromeBundleErrorCode.INTEGRITY_MISMATCH,
            "synthetic bundle rejection",
        )

    monkeypatch.setattr(
        external_gate_module,
        "verify_inferdrome_bundle",
        reject_bundle,
    )

    with pytest.raises(ManagedEvidenceAdmissionRejected) as rejected:
        admit_a100_qwen3_retrospective(
            tmp_path / "archive.tar.gz",
            destination,
            handoff_manifest_path=tmp_path / "handoff.json",
            publication_review_path=tmp_path / "review.json",
            operational_summary_path=tmp_path / "summary.json",
            profile_document_path=tmp_path / "profile.json",
        )
    assert (
        rejected.value.code
        is ManagedEvidenceAdmissionErrorCode.BUNDLE_INTEGRITY_MISMATCH
    )
    assert not destination.exists()


def test_cleanup_failure_is_a_stable_admission_rejection(monkeypatch, tmp_path):
    destination = tmp_path / "extract"

    def fake_extract(archive_path, root, **kwargs):
        root.mkdir()
        member = root / "bundle"
        member.mkdir()
        return ExtractedInferdromeArchiveMember(
            root=root,
            archive_sha256=A100_ARCHIVE_SHA256,
            member_path="bundle",
            member=member,
            member_count=1,
            file_count=0,
            directory_count=1,
            expanded_bytes=0,
        )

    monkeypatch.setattr(
        external_gate_module, "_validate_profile_document", lambda *args: None
    )
    monkeypatch.setattr(external_gate_module, "_read_metadata", lambda *args: {})
    monkeypatch.setattr(external_gate_module, "_validate_handoff", lambda *args: None)
    monkeypatch.setattr(
        external_gate_module, "_validate_publication_review", lambda *args: None
    )
    monkeypatch.setattr(
        external_gate_module, "_validate_operational_summary", lambda *args: None
    )
    monkeypatch.setattr(
        external_gate_module, "extract_external_inferdrome_archive", fake_extract
    )
    monkeypatch.setattr(
        external_gate_module,
        "verify_inferdrome_bundle",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            InferdromeBundleRejected(
                InferdromeBundleErrorCode.INTEGRITY_MISMATCH,
                "synthetic bundle rejection",
            )
        ),
    )
    monkeypatch.setattr(
        external_gate_module.shutil,
        "rmtree",
        lambda path: (_ for _ in ()).throw(OSError("synthetic cleanup failure")),
    )

    with pytest.raises(ManagedEvidenceAdmissionRejected) as rejected:
        admit_a100_qwen3_retrospective(
            tmp_path / "archive.tar.gz",
            destination,
            handoff_manifest_path=tmp_path / "handoff.json",
            publication_review_path=tmp_path / "review.json",
            operational_summary_path=tmp_path / "summary.json",
            profile_document_path=tmp_path / "profile.json",
        )
    assert rejected.value.code is ManagedEvidenceAdmissionErrorCode.CLEANUP_FAILED


def test_external_metadata_rejects_duplicate_oversized_and_deep_json(tmp_path):
    nested = {}
    for _ in range(65):
        nested = {"nested": nested}
    payloads = [
        '{"field": 1, "field": 2}',
        '{"field": ' + "9" * 20 + "}",
        json.dumps(nested),
    ]
    for index, payload in enumerate(payloads):
        path = tmp_path / f"metadata-{index}.json"
        path.write_text(payload, encoding="utf-8")
        with pytest.raises(ManagedEvidenceAdmissionRejected) as rejected:
            external_gate_module._read_metadata(path, "synthetic metadata")
        assert rejected.value.code is ManagedEvidenceAdmissionErrorCode.MALFORMED_JSON


def test_frozen_metadata_shape_rejects_extra_fields_and_bool_integer_aliases():
    summary = _synthetic_operational_summary()
    summary["extra"] = "must reject"
    with pytest.raises(ManagedEvidenceAdmissionRejected) as extra:
        external_gate_module._validate_operational_summary(
            summary,
            A100_RETROSPECTIVE_EXPECTATION,
        )
    assert extra.value.code is ManagedEvidenceAdmissionErrorCode.PROFILE_FACTS_EXTRA

    review = _synthetic_publication_review()
    review["owner_publication_approval_required"] = 1
    with pytest.raises(ManagedEvidenceAdmissionRejected) as alias:
        external_gate_module._validate_publication_review(
            review,
            A100_RETROSPECTIVE_EXPECTATION,
        )
    assert alias.value.code is ManagedEvidenceAdmissionErrorCode.PROVENANCE_INSUFFICIENT


def test_frozen_nested_metadata_shape_rejects_extra_archive_fields():
    review = _synthetic_publication_review()
    review["archive"]["unexpected"] = "must reject"
    with pytest.raises(ManagedEvidenceAdmissionRejected) as rejected:
        external_gate_module._validate_publication_review(
            review,
            A100_RETROSPECTIVE_EXPECTATION,
        )
    assert rejected.value.code is ManagedEvidenceAdmissionErrorCode.PROFILE_FACTS_EXTRA


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        ("model", ManagedEvidenceProfileErrorCode.MODEL_MISMATCH),
        ("revision", ManagedEvidenceProfileErrorCode.REVISION_MISMATCH),
        ("engine", ManagedEvidenceProfileErrorCode.ENGINE_MISMATCH),
        ("adapter", ManagedEvidenceProfileErrorCode.ADAPTER_MISMATCH),
        ("workload", ManagedEvidenceProfileErrorCode.WORKLOAD_MISMATCH),
        ("concurrency", ManagedEvidenceProfileErrorCode.CONCURRENCY_MISMATCH),
        ("sample", ManagedEvidenceProfileErrorCode.SAMPLE_COUNT_MISMATCH),
        (
            "metric_semantics",
            ManagedEvidenceProfileErrorCode.METRIC_SEMANTICS_MISMATCH,
        ),
        ("metric_reducer", ManagedEvidenceProfileErrorCode.METRIC_REDUCER_MISMATCH),
        (
            "metric_population",
            ManagedEvidenceProfileErrorCode.METRIC_POPULATION_MISMATCH,
        ),
        ("metric_unit", ManagedEvidenceProfileErrorCode.METRIC_UNITS_MISMATCH),
        ("provenance", ManagedEvidenceProfileErrorCode.ENVIRONMENT_MISMATCH),
    ],
)
def test_a100_profile_near_misses_have_stable_reason_codes(mutation, expected_code):
    context = _synthetic_a100_context()
    if mutation == "model":
        context["resolved"]["target"]["model"] = "Qwen/other"
    elif mutation == "revision":
        context["resolved"]["target"]["model_revision"] = "other-revision"
    elif mutation == "engine":
        context["resolved"]["target"]["engine"] = "other-engine"
    elif mutation == "adapter":
        context["resolved"]["execution"]["adapter"] = "other-adapter"
    elif mutation == "workload":
        context["resolved"]["workload"]["sha256"] = "sha256:" + "0" * 64
    elif mutation == "concurrency":
        context["resolved"]["traffic"]["concurrency"] = 2
        context["plan"]["traffic"]["concurrency"] = 2
        context["execution"]["configured_traffic"]["concurrency"] = 2
    elif mutation == "sample":
        context["resolved"]["traffic"]["measured_requests"] = 95
        context["plan"]["traffic"]["measured_requests"] = 95
        context["execution"]["configured_traffic"]["measured_requests"] = 95
    elif mutation == "metric_semantics":
        context["resolved"]["measurement"]["ttft_definition"] = "other-metric"
    elif mutation == "metric_reducer":
        context["resolved"]["measurement"]["reducer_version"] = "other-reducer"
    elif mutation == "metric_population":
        context["definitions"]["definitions"][0]["population"] = "other-population"
    elif mutation == "metric_unit":
        context["definitions"]["definitions"][0]["unit"] = "ms"
    else:
        for field in context["environment"]["fields"]:
            if field["name"] == "gpu.model":
                field["provenance"] = "UNKNOWN"
                break

    with pytest.raises(ManagedEvidenceProfileRejected) as rejected:
        validate_managed_evidence_profile(
            A100_QWEN3_PROFILE,
            descriptor=context["descriptor"],
            resolved=context["resolved"],
            plan=context["plan"],
            environment=context["environment"],
            execution=context["execution"],
            invocation=context["invocation"],
            definitions=context["definitions"],
        )
    assert rejected.value.code is expected_code


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        ("model", ManagedEvidenceAdmissionErrorCode.MODEL_MISMATCH),
        ("revision", ManagedEvidenceAdmissionErrorCode.REVISION_MISMATCH),
        (
            "metric_population",
            ManagedEvidenceAdmissionErrorCode.METRIC_POPULATION_MISMATCH,
        ),
        ("metric_reducer", ManagedEvidenceAdmissionErrorCode.METRIC_REDUCER_MISMATCH),
        ("chronology", ManagedEvidenceAdmissionErrorCode.CONTRACT_LINK_MISMATCH),
        ("contract_link", ManagedEvidenceAdmissionErrorCode.CONTRACT_LINK_MISMATCH),
        ("provenance", ManagedEvidenceAdmissionErrorCode.PROVENANCE_INSUFFICIENT),
    ],
)
def test_handoff_metadata_near_misses_have_stable_reason_codes(mutation, expected_code):
    handoff = _synthetic_handoff()
    if mutation == "model":
        handoff["run"]["model"]["id"] = "Qwen/other"
    elif mutation == "revision":
        handoff["run"]["model"]["revision"] = "other-revision"
    elif mutation == "metric_population":
        handoff["run"]["summary_measurements"]["ttft_ns"]["population"] = "other"
    elif mutation == "metric_reducer":
        handoff["run"]["summary_measurements"]["ttft_ns"]["quantile_method"] = "other"
    elif mutation == "chronology":
        handoff["contract_binding"]["chronology"] = "PREMEASUREMENT"
    elif mutation == "contract_link":
        handoff["contract_binding"]["producer_exitspec_contract_digest"] = (
            "sha256:" + "0" * 64
        )
    else:
        handoff["fixture_delivery"]["publication_state"] = "PUBLISHED"

    with pytest.raises(ManagedEvidenceAdmissionRejected) as rejected:
        external_gate_module._validate_handoff(
            handoff,
            A100_QWEN3_PROFILE,
            A100_RETROSPECTIVE_EXPECTATION,
        )
    assert rejected.value.code is expected_code


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        ("population", ManagedEvidenceProfileErrorCode.METRIC_POPULATION_MISMATCH),
        ("reducer", ManagedEvidenceProfileErrorCode.METRIC_REDUCER_MISMATCH),
        ("unit", ManagedEvidenceProfileErrorCode.METRIC_UNITS_MISMATCH),
    ],
)
def test_metric_definition_near_misses_have_stable_reason_codes(
    mutation, expected_code
):
    definition = {
        "definitions": [
            {
                "definition_id": A100_QWEN3_PROFILE.metric_definition_id,
                "metric": "ttft_ns",
                "population": A100_QWEN3_PROFILE.metric_population,
                "quantile_method": A100_QWEN3_PROFILE.metric_reducer_id,
                "unit": A100_QWEN3_PROFILE.metric_unit,
                "allowed_aggregations": ["mean", "p50", "p95", "p99"],
            }
        ]
    }
    if mutation == "population":
        definition["definitions"][0]["population"] = "other"
    elif mutation == "reducer":
        definition["definitions"][0]["quantile_method"] = "other"
    else:
        definition["definitions"][0]["unit"] = "ms"

    with pytest.raises(ManagedEvidenceProfileRejected) as rejected:
        profile_registry_module._require_metric_definition(
            definition,
            A100_QWEN3_PROFILE,
        )
    assert rejected.value.code is expected_code


def _synthetic_a100_context():
    invocation = _synthetic_a100_invocation()
    proof = invocation["local_gpu_proof"]
    traffic = {
        "kind": "concurrent",
        "concurrency": 1,
        "warmup_requests": 12,
        "measured_requests": 96,
    }
    descriptor = {
        "schema_version": "inferdrome.evidence.v1",
        "run_id": proof["run_id"],
        "producer": {
            "name": "vllm",
            "version": "0.26.0",
            "adapter": "vllm_bench_serve",
            "adapter_version": "1.0.0",
            "native_schema_fingerprint": A100_QWEN3_PROFILE.native_schema_fingerprint,
        },
        "digests": {
            "execution_fingerprint": invocation["metadata"][
                "inferdrome_execution_fingerprint"
            ]
        },
    }
    resolved = {
        "target": {
            "engine": "vllm",
            "engine_version": "0.26.0",
            "model": "Qwen/Qwen3-8B",
            "model_revision": A100_QWEN3_PROFILE.model_revision,
            "tokenizer_revision": A100_QWEN3_PROFILE.tokenizer_revision,
            "endpoint": "http://127.0.0.1:18080/",
        },
        "workload": {
            "sha256": A100_QWEN3_PROFILE.workload_sha256,
            "requested_output_tokens": 128,
            "temperature": "0.7",
            "seed": 42,
            "prompt_content_policy": "include",
        },
        "traffic": copy.deepcopy(traffic),
        "execution": {
            "mode": "attached_endpoint",
            "producer_name": "vllm",
            "producer_version": "0.26.0",
            "adapter": "vllm_bench_serve",
            "adapter_version": "1.0.0",
        },
        "measurement": {
            "ttft_definition": "vllm_first_choices_event_v0_26",
            "metric_definitions_version": "1.0.0",
            "reducer_version": "1.0.0",
            "choices_span_definition": "last_choices_event_span_v1",
            "streaming": True,
        },
    }
    plan = {
        "traffic": copy.deepcopy(traffic),
        "producer_request_id_prefix": f"{proof['run_id']}-",
    }
    proof_fields = {
        "client.os": (proof["client_os"], "CLIENT_OBSERVED"),
        "client.arch": (proof["client_arch"], "CLIENT_OBSERVED"),
        "client.python_version": (proof["client_python_version"], "CLIENT_OBSERVED"),
        "producer.version": ("0.26.0", "LOCALLY_VERIFIED"),
        "producer.distribution_sha256": (
            proof["producer_distribution"]["sha256"],
            "LOCALLY_VERIFIED",
        ),
        "target.engine_version": ("0.26.0", "LOCALLY_VERIFIED"),
        "target.model_revision": (A100_QWEN3_PROFILE.model_revision, "CONFIGURED"),
        "target.tokenizer_revision": (
            A100_QWEN3_PROFILE.tokenizer_revision,
            "CONFIGURED",
        ),
        "server.model_id": ("Qwen/Qwen3-8B", "SERVER_REPORTED"),
        "gpu.model": (A100_QWEN3_PROFILE.hardware_model, "LOCALLY_VERIFIED"),
        "gpu.count": (1, "LOCALLY_VERIFIED"),
        "cuda.version": (proof["cuda_runtime_version"], "LOCALLY_VERIFIED"),
        "driver.version": (
            proof["gpus"][0]["driver_version"],
            "LOCALLY_VERIFIED",
        ),
    }
    environment = {
        "captured_at": "2026-08-20T00:04:00Z",
        "completeness": "COMPLETE",
        "fields": [
            {
                "name": name,
                "value": value,
                "provenance": provenance,
                "evidence_path": "native/invocation.json",
            }
            for name, (value, provenance) in proof_fields.items()
        ],
    }
    execution = {
        "started_at": "2026-08-20T00:02:30Z",
        "ended_at": "2026-08-20T00:03:00Z",
        "configured_traffic": copy.deepcopy(traffic),
    }
    definitions = {
        "definitions": [
            {
                "definition_id": A100_QWEN3_PROFILE.metric_definition_id,
                "metric": "ttft_ns",
                "population": A100_QWEN3_PROFILE.metric_population,
                "quantile_method": A100_QWEN3_PROFILE.metric_reducer_id,
                "unit": A100_QWEN3_PROFILE.metric_unit,
                "allowed_aggregations": ["mean", "p50", "p95", "p99"],
            }
        ]
    }
    return {
        "descriptor": descriptor,
        "resolved": resolved,
        "plan": plan,
        "environment": environment,
        "execution": execution,
        "invocation": invocation,
        "definitions": definitions,
    }


def _synthetic_handoff():
    return {
        "acceptance_boundary": {
            "capture_kind": "BOUNDED_RUNTIME_CAPABILITY_SPIKE",
            "inferdrome_acceptance_verdict": None,
            "publication_state": "OBSERVATION_ONLY_PENDING_REVIEW",
        },
        "archive": {
            "bundle_member_path": A100_RETROSPECTIVE_EXPECTATION.bundle_member_path,
            "capture_manifest_sha256": A100_RETROSPECTIVE_EXPECTATION.capture_manifest_sha256,
            "compressed_size_bytes": A100_RETROSPECTIVE_EXPECTATION.archive_size_bytes,
            "sha256": A100_RETROSPECTIVE_EXPECTATION.archive_sha256,
        },
        "capability_profile": {
            "campaign_id": "qwen-gpu-capability-campaign-v1",
            "commit": "6cb774d210940073347f9045bb15611aa9e9cf27",
            "managed_profile": {
                "identity": A100_QWEN3_PROFILE.profile_id,
                "path": "campaigns/v1/profiles/managed-vllm-0.26-qwen3-8b-bf16-v1.json",
                "sha256": A100_QWEN3_PROFILE.profile_sha256,
            },
            "model_snapshot": {
                "file_count": 15,
                "revision": A100_QWEN3_PROFILE.model_revision,
                "sha256": "sha256:" + "1" * 64,
                "total_bytes": 16_397_461_266,
            },
            "workload": {
                "id": A100_QWEN3_PROFILE.workload_id,
                "path": "campaigns/v1/workloads/qwen-text-mixed-length-v1.jsonl",
                "prompt_count": 96,
                "sha256": A100_QWEN3_PROFILE.workload_sha256,
            },
        },
        "contract_binding": {
            "chronology": "RETROSPECTIVE",
            "chronology_disclosure": (
                "No producer-side ExitSpec contract digest was frozen before this "
                "measurement. A later consumer must use an explicit external "
                "receipt binding without rewriting chronology."
            ),
            "producer_exitspec_contract_digest": None,
            "required_consumer_mode": "EXTERNAL_RECEIPT_BINDING",
        },
        "fixture_delivery": {
            "proposed_checksum_pinned_location": "https://synthetic.invalid/archive",
            "publication_state": "BLOCKED_PENDING_OWNER_APPROVAL",
            "required_sha256": A100_RETROSPECTIVE_EXPECTATION.archive_sha256,
            "statement": "synthetic structure-only fixture",
        },
        "history_provenance": {
            "capability_profile_commit": "6cb774d210940073347f9045bb15611aa9e9cf27",
            "capture_producer_commit": "a02bfd7c3f8bd0f734da0e84d476bcfa905fec4b",
            "eventual_merge_commit": None,
            "merge_requirement": (
                "Use a merge commit that preserves capture_producer_commit ancestry; "
                "do not squash or rebase away the producer commit."
            ),
            "publication_review_commit": None,
        },
        "operational_completion": {},
        "publication_review": {},
        "run": {
            "bundle_digest": A100_RETROSPECTIVE_EXPECTATION.bundle_digest,
            "execution_fingerprint": A100_RETROSPECTIVE_EXPECTATION.execution_fingerprint,
            "metric_definitions_digest": A100_RETROSPECTIVE_EXPECTATION.metric_definitions_digest,
            "model": {
                "id": A100_QWEN3_PROFILE.model_id,
                "revision": A100_QWEN3_PROFILE.model_revision,
            },
            "request_plan_digest": A100_RETROSPECTIVE_EXPECTATION.request_plan_digest,
            "request_population": {
                "failed_requests": 0,
                "measured_requests": 96,
                "successful_requests": 96,
                "ttft_samples": 96,
            },
            "run_id": A100_RETROSPECTIVE_EXPECTATION.run_id,
            "source_spec_digest": A100_RETROSPECTIVE_EXPECTATION.source_spec_digest,
            "summary_measurements": {
                "output_token_throughput_per_s": "synthetic",
                "ttft_ns": {
                    "definition_id": A100_QWEN3_PROFILE.metric_definition_id,
                    "p50": 1,
                    "p95": A100_RETROSPECTIVE_EXPECTATION.expected_p95_ns,
                    "p99": 2,
                    "population": A100_QWEN3_PROFILE.metric_population,
                    "quantile_method": A100_QWEN3_PROFILE.metric_reducer_id,
                },
            },
            "workload_sha256": A100_QWEN3_PROFILE.workload_sha256,
        },
        "runtime_capability": {
            "expected_gpu_model": A100_QWEN3_PROFILE.hardware_model,
            "gpu_tier_id": "a100-40gb-sxm4",
            "hardware_attestation": False,
            "hardware_observation": "synthetic",
            "profile_id": A100_QWEN3_PROFILE.profile_id,
            "spike_outcome": "SPIKE_SUCCEEDED",
            "torch_cuda_device_count": 1,
        },
        "schema_version": "inferdrome.qwen3-gpu-evidence-handoff.v1",
    }


def _synthetic_a100_invocation():
    """Build a clearly synthetic A100-shaped invocation for validator tests."""

    value = json.loads(
        (
            Path(__file__).parent
            / "fixtures"
            / "inferdrome"
            / "profiles"
            / "v1"
            / "valid"
            / "managed-vllm-invocation.json"
        ).read_bytes()
    )
    proof = value["local_gpu_proof"]
    synthetic_run_id = "run-" + "a" * 32
    model_root = "/synthetic/a100-qwen3-model"
    executable = "/synthetic/vllm"
    proof["run_id"] = synthetic_run_id
    proof["gpus"][0]["model"] = "NVIDIA A100-SXM4-40GB"
    proof["gpu_query_stdout"] = proof["gpu_query_stdout"].replace(
        "NVIDIA A10", "NVIDIA A100-SXM4-40GB"
    )
    for snapshot_name in ("model_snapshot", "tokenizer_snapshot"):
        proof[snapshot_name]["revision"] = "b968826d9c46dd6066d109eabc6255188de91218"
        proof[snapshot_name]["root"] = model_root
    proof["producer_distribution"]["executable_path"] = executable
    proof["producer_distribution"][
        "source_wheel_path"
    ] = "/synthetic/vllm-0.26.0-cp38-abi3-manylinux_2_28_x86_64.whl"
    substitutions = {
        "{producer_distribution.executable_path}": executable,
        "{model_snapshot.root}": model_root,
        "{resolved_target_port}": "18080",
        "{resolved_target_model}": "Qwen/Qwen3-8B",
        "{tokenizer_snapshot.root}": model_root,
        "{resolved_workload_seed}": "42",
        "{selected_gpu_index}": "0",
    }
    proof["server"]["argv"] = [
        substitutions.get(item, item) for item in A100_SERVER_ARGV_TEMPLATE
    ]
    argv = value["argv"]
    argv[0] = executable
    _replace_option(argv, "--model", "Qwen/Qwen3-8B")
    _replace_option(argv, "--tokenizer", model_root)
    for index, item in enumerate(argv):
        if item.startswith("inferdrome_run_id="):
            argv[index] = f"inferdrome_run_id={synthetic_run_id}"
    value["metadata"]["inferdrome_run_id"] = synthetic_run_id
    value["local_gpu_proof"] = proof
    value["campaign_profile"] = copy.deepcopy(A100_QWEN3_PROFILE.profile_binding)
    value["endpoint_preflight"]["result"]["server_reported_models"] = ["Qwen/Qwen3-8B"]
    value["endpoint_preflight"]["result"]["target_model"] = "Qwen/Qwen3-8B"
    return value


def _replace_option(argv, option, value):
    index = argv.index(option)
    argv[index + 1] = value


def _synthetic_operational_summary():
    return {
        "archive_sha256": A100_RETROSPECTIVE_EXPECTATION.archive_sha256,
        "bundle_digest": A100_RETROSPECTIVE_EXPECTATION.bundle_digest,
        "capture_manifest_sha256": "sha256:" + "1" * 64,
        "cost_observation": {},
        "evidence_kind": "OPERATIONAL_RECORD_NOT_PROVIDER_ATTESTATION",
        "provider": {
            "gpu_tier_id": "a100-40gb-sxm4",
            "instance_type_name": "synthetic",
            "termination_confirmed_at": "2026-01-01T00:00:00Z",
            "termination_final_status": "absent",
            "termination_trigger": "controller-finally",
        },
        "repository_commit": "a02bfd7c3f8bd0f734da0e84d476bcfa905fec4b",
        "run_id": A100_RETROSPECTIVE_EXPECTATION.run_id,
        "schema_version": "inferdrome.qwen3-gpu-operational-summary.v1",
        "semantic_verification": "VALID_AFTER_PROVIDER_TERMINATION",
        "source_receipts": {},
    }


def _synthetic_publication_review():
    return {
        "archive": {
            "compressed_size_bytes": A100_RETROSPECTIVE_EXPECTATION.archive_size_bytes,
            "sha256": A100_RETROSPECTIVE_EXPECTATION.archive_sha256,
        },
        "archive_integrity_and_safety": {"status": "PASS"},
        "content_review": {},
        "decision_reasons": [],
        "detector_results": {},
        "findings": [],
        "license_review": {},
        "owner_publication_approval_required": True,
        "publication_status": "EXTERNAL_ONLY",
        "raw_archive_modified": False,
        "review_limits": {},
        "review_method": "synthetic",
        "schema_version": "inferdrome.gpu-evidence-publication-review.v1",
        "scope": "exact_archive_bytes",
    }


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
