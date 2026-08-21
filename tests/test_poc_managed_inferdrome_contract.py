from __future__ import annotations

import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from exitspec.confirmations import (
    canonical_confirmation_payload,
    contract_confirmation_fingerprint,
)
from exitspec.inferdrome_archive import extract_pinned_inferdrome_archive
from exitspec.inferdrome_bundle import verify_inferdrome_bundle
from exitspec.inferdrome_external_contract import validate_managed_contract_context
from exitspec.inferdrome_catalog import InferdromeBundleCatalog
from exitspec.models import (
    ContractStatus,
    InferencePerformanceCriterionV3,
    POCContract,
)
from exitspec.poc_contract_definition import ContractDefinitionOperator
from exitspec.poc_managed_inferdrome_contract import (
    ManagedInferdromeContractAssemblyError,
    ManagedInferdromeEvidenceProjection,
    PreparedManagedInferdromeBundle,
    prepare_managed_inferdrome_bundle,
    project_managed_inferdrome_evidence,
)
from exitspec.poc_inferdrome_import import (
    POCInferdromeImportConflict,
    POCInferdromeImportStatus,
    ProcessLocalPOCInferdromeImportService,
)
from exitspec.poc_performance_contract import (
    PerformanceEvidenceMethod,
    PerformanceTargetInput,
)
from exitspec.poc_performance_lifecycle import (
    ProcessLocalPerformanceLifecycleService,
)
from exitspec.poc_performance_lifecycle_web_api import (
    _managed_evidence_profile,
)
from exitspec.poc_performance_run import (
    POCPerformanceRunConflict,
    ProcessLocalPOCPerformanceRunService,
)
from tests.test_poc_performance_contract import (
    NOW,
    POC_ID,
    PROMPTS,
    _inputs,
)


def _projection(**updates: object) -> ManagedInferdromeEvidenceProjection:
    payload: dict[str, object] = {
        "run_id": "run-533c9f5f783958fb6077069a6c577144",
        "bundle_digest": (
            "sha256:bae216f2165eb06ae2e0f14d3cd852f8e0ebb381bf1f68c71072769b3c0c1675"
        ),
        "evidence_schema_version": "inferdrome.evidence.v1",
        "producer_name": "vllm",
        "producer_version": "0.26.0",
        "adapter_id": "vllm_bench_serve",
        "adapter_version": "1.0.0",
        "native_schema_fingerprint": (
            "sha256:3a4fdee6fe9b45ce5b42c41fd3bfc6614245a36ecfe6f94de92b59717a136abb"
        ),
        "managed_profile_id": (
            "inferdrome.managed-vllm-0.26-evidence-profile.v1"
        ),
        "managed_profile_sha256": (
            "sha256:9d03b5d0822ed829ddbfa4c87c75530885b9ad51ee2c0cb7c5e31a075996fe34"
        ),
        "local_gpu_proof_schema_id": "urn:inferdrome:local-gpu-proof:v1",
        "local_gpu_proof_schema_sha256": (
            "sha256:cf83bbdea2bba4c30b8f0e2c5f34f34a4077501207881fdbdab021571d665547"
        ),
        "request_plan_digest": (
            "sha256:0fb852366933598da4139114f416b441c52d2c83cae07b7d8938bd482a12fc8e"
        ),
        "workload_digest": (
            "sha256:22bf3389cc29ee946ae567870d7f8d7b458594224542a796e8990c15b1cfcd63"
        ),
        "workload_path": "real-gpu/workload.jsonl",
        "target_model": "Qwen/Qwen2.5-0.5B-Instruct",
        "target_model_revision": "7ae557604adf67be50417f59c2c2f167def9a775",
        "target_tokenizer_revision": (
            "7ae557604adf67be50417f59c2c2f167def9a775"
        ),
        "target_endpoint": "http://127.0.0.1:18080/",
        "observed_configured_max_concurrency": 4,
        "exact_measured_attempts": 100,
        "warmup_requests": 10,
        "metric_definition_id": "vllm_first_choices_event_v0_26",
        "gpu_models": ("NVIDIA A10",),
        "claims_assurance": "INTERNAL_CONSISTENCY_ONLY",
        "native_response_content_present": True,
    }
    payload.update(updates)
    return ManagedInferdromeEvidenceProjection.model_validate(payload, strict=True)


def _target(
    projection: ManagedInferdromeEvidenceProjection,
) -> PerformanceTargetInput:
    return PerformanceTargetInput(
        provider="inferdrome-managed-vllm",
        endpoint_class="retained-loopback-vllm-benchmark",
        endpoint=projection.target_endpoint,
        model=projection.target_model,
        evidence_method=PerformanceEvidenceMethod.INFERDROME_EXTERNAL_BUNDLE,
        inferdrome_run_id=projection.run_id,
        inferdrome_bundle_digest=projection.bundle_digest,
    )


def _managed_inputs(*, concurrency: int = 4):
    return _inputs(
        ttft_operator=ContractDefinitionOperator.LT,
        ttft_samples=100,
        error_samples=100,
        ttft_concurrency=concurrency,
        error_concurrency=concurrency,
    )


def _lifecycle(
    projection_ref: list[ManagedInferdromeEvidenceProjection],
    *,
    concurrency: int = 4,
) -> ProcessLocalPerformanceLifecycleService:
    draft, proposals, definitions = _managed_inputs(concurrency=concurrency)
    return ProcessLocalPerformanceLifecycleService(
        draft_lookup=lambda poc_id: draft
        if poc_id == POC_ID
        else (_ for _ in ()).throw(KeyError(poc_id)),
        proposal_lookup=lambda poc_id: proposals if poc_id == POC_ID else (),
        definition_lookup=lambda: definitions,
        prompt_bytes=PROMPTS,
        managed_evidence_lookup=lambda run_id, bundle_digest: projection_ref[0],
        clock=lambda: NOW,
    )


def _freeze_managed(
    service: ProcessLocalPerformanceLifecycleService,
    projection: ManagedInferdromeEvidenceProjection,
) -> PreparedManagedInferdromeBundle:
    service.prepare(
        POC_ID,
        target=_target(projection),
        reviewer="Jayesh",
        rationale="Bind the exact retained managed evidence before confirmation.",
        idempotency_key="prepare-managed-a10",
    )
    token = service.customer_review_url(POC_ID).rsplit("/", 1)[-1]
    service.record_customer_review_decision(
        token,
        decision="CONFIRM",
        agreement_acknowledged=True,
        rationale="This exact retrospective native metric criterion is correct.",
        idempotency_key="confirm-managed-a10",
    )
    service.freeze(POC_ID, idempotency_key="freeze-managed-a10")
    bundle, _, _ = service.frozen_bundle(POC_ID)
    assert type(bundle) is PreparedManagedInferdromeBundle
    return bundle


def test_target_requires_one_pathless_managed_identity_pair():
    projection = _projection()
    with pytest.raises(ValidationError, match="selected together"):
        PerformanceTargetInput(
            provider="inferdrome-managed-vllm",
            endpoint_class="retained-loopback-vllm-benchmark",
            endpoint=projection.target_endpoint,
            model=projection.target_model,
            evidence_method="INFERDROME_EXTERNAL_BUNDLE",
            inferdrome_run_id=projection.run_id,
        )
    with pytest.raises(ValidationError, match="external evidence method"):
        PerformanceTargetInput(
            provider="inferdrome-managed-vllm",
            endpoint_class="retained-loopback-vllm-benchmark",
            endpoint=projection.target_endpoint,
            model=projection.target_model,
            inferdrome_run_id=projection.run_id,
            inferdrome_bundle_digest=projection.bundle_digest,
        )


def test_pre_freeze_profile_is_pathless_and_semantically_explicit():
    profile = _managed_evidence_profile(_projection())

    assert set(profile) == {
        "adapter",
        "bundle_digest",
        "chronology",
        "claims_assurance",
        "display_name",
        "endpoint",
        "endpoint_class",
        "gpu_models",
        "measured_requests",
        "metric_definition_id",
        "model",
        "observed_configured_max_concurrency",
        "privacy",
        "producer",
        "profile_id",
        "reducer_id",
        "run_id",
        "target_provider",
        "warmup_requests",
    }
    assert profile["metric_definition_id"] == (
        "vllm_first_choices_event_v0_26"
    )
    assert profile["chronology"] == "RETROSPECTIVE"
    assert profile["privacy"] == (
        "SYNTHETIC_NATIVE_RESPONSE_CONTENT_RETAINED_SERVER_SIDE"
    )
    serialized = repr(profile).lower()
    assert "bundle_path" not in serialized
    assert "runs_root" not in serialized
    assert "/private/" not in serialized


def test_managed_assembler_binds_native_semantics_and_customer_rules():
    projection = _projection()
    draft, proposals, definitions = _managed_inputs()

    bundle = prepare_managed_inferdrome_bundle(
        draft=draft,
        proposals=proposals,
        definitions=definitions,
        target=_target(projection),
        evidence=projection,
        prepared_at=NOW,
    )

    criterion = bundle.approved_contract.criteria[0]
    assert type(criterion) is InferencePerformanceCriterionV3
    assert criterion.ttft_p95.definition_id == "vllm_first_choices_event_v0_26"
    assert criterion.ttft_p95.threshold_ns == 500_000_000
    assert criterion.ttft_p95.minimum_successful_samples == 100
    assert criterion.error_rate.threshold_basis_points == 100
    assert criterion.error_rate.exact_attempts == 100
    assert criterion.evidence_identity.request_plan_digest == (
        projection.request_plan_digest
    )
    assert criterion.evidence_identity.workload_digest == projection.workload_digest
    assert criterion.evidence_identity.configured_max_concurrency == 4
    assert bundle.approved_contract.workload.sha256 == (
        projection.workload_digest.removeprefix("sha256:")
    )
    assert projection.run_id in bundle.approved_contract.workload.fixture_path
    assert projection.bundle_digest.removeprefix("sha256:") in (
        bundle.approved_contract.workload.fixture_path
    )
    tampered_payload = bundle.approved_contract.model_dump(mode="python")
    tampered_workload = dict(tampered_payload["workload"])
    tampered_workload["fixture_path"] = tampered_workload[
        "fixture_path"
    ].replace("bae216", "0ae216")
    tampered_payload["workload"] = tampered_workload
    tampered = POCContract.model_validate(tampered_payload)
    assert contract_confirmation_fingerprint(tampered) != (
        contract_confirmation_fingerprint(bundle.approved_contract)
    )
    assert "retrospective" in " ".join(bundle.planning_limitations).lower()
    assert "native annex contains synthetic generated response content" in " ".join(
        bundle.planning_limitations
    )


@pytest.mark.parametrize(
    ("ttft_operator", "ttft_samples", "error_samples"),
    [
        (ContractDefinitionOperator.LTE, 100, 100),
        (ContractDefinitionOperator.LT, 95, 100),
        (ContractDefinitionOperator.LT, 100, 99),
    ],
)
def test_managed_assembler_never_silently_rewrites_customer_population(
    ttft_operator: ContractDefinitionOperator,
    ttft_samples: int,
    error_samples: int,
):
    projection = _projection()
    draft, proposals, definitions = _inputs(
        ttft_operator=ttft_operator,
        ttft_samples=ttft_samples,
        error_samples=error_samples,
    )

    with pytest.raises(ManagedInferdromeContractAssemblyError):
        prepare_managed_inferdrome_bundle(
            draft=draft,
            proposals=proposals,
            definitions=definitions,
            target=_target(projection),
            evidence=projection,
            prepared_at=NOW,
        )


def test_customer_reviews_and_freezes_exact_v3_identity_before_import():
    projection = _projection()
    service = _lifecycle([projection])
    prepared = service.prepare(
        POC_ID,
        target=_target(projection),
        reviewer="Jayesh",
        rationale="Bind the exact retained managed evidence before confirmation.",
        idempotency_key="prepare-managed-review",
    )
    token = service.customer_review_url(POC_ID).rsplit("/", 1)[-1]
    customer = service.customer_review_payload(token)["review"]

    visible = customer["contract"]["criteria"][0]
    assert visible["adapter"] == "vllm_bench_serve"
    assert visible["threshold"] == (
        "Native p95 TTFT below 500 ms · error rate below 1%"
    )
    assert visible["sample"] == (
        "100 successful native timing records · 100 measured records"
    )
    assert customer["agreement"]["criteria"][0]["criterion_type"] == (
        "inference_performance_v3"
    )
    service.record_customer_review_decision(
        token,
        decision="CONFIRM",
        agreement_acknowledged=True,
        rationale="The selected bundle and exact native rule are correct.",
        idempotency_key="confirm-managed-review",
    )
    frozen = service.freeze(
        POC_ID,
        idempotency_key="freeze-managed-review",
    ).value
    bundle, confirmation, frozen_again = service.frozen_bundle(POC_ID)

    assert prepared.value.bundle is bundle
    assert frozen.status is ContractStatus.FROZEN
    assert frozen_again is frozen
    assert validate_managed_contract_context(frozen, confirmation).criterion == (
        frozen.criteria[0]
    )


def test_preparation_captures_projection_once_and_import_reverifies_later():
    projection = _projection()
    current = [projection]
    calls = 0
    draft, proposals, definitions = _managed_inputs()

    def lookup(
        run_id: str,
        bundle_digest: str,
    ) -> ManagedInferdromeEvidenceProjection:
        nonlocal calls
        calls += 1
        return current[0]

    service = ProcessLocalPerformanceLifecycleService(
        draft_lookup=lambda poc_id: draft
        if poc_id == POC_ID
        else (_ for _ in ()).throw(KeyError(poc_id)),
        proposal_lookup=lambda poc_id: proposals if poc_id == POC_ID else (),
        definition_lookup=lambda: definitions,
        prompt_bytes=PROMPTS,
        managed_evidence_lookup=lookup,
        clock=lambda: NOW,
    )
    prepared = service.prepare(
        POC_ID,
        target=_target(projection),
        reviewer="Jayesh",
        rationale="Bind one exact selected bundle.",
        idempotency_key="prepare-managed-stale",
    )
    changed = projection.model_dump(mode="python")
    changed["gpu_models"] = ("NVIDIA A10 changed",)
    current[0] = ManagedInferdromeEvidenceProjection.model_validate(
        changed,
        strict=True,
    )

    token = service.customer_review_url(POC_ID).rsplit("/", 1)[-1]
    assert service.customer_review_payload(token)["review"]["agreement"] == (
        canonical_confirmation_payload(prepared.value.approved_contract)
    )
    service.record_customer_review_decision(
        token,
        decision="CONFIRM",
        agreement_acknowledged=True,
        rationale="The captured selection is correct.",
        idempotency_key="confirm-managed-captured",
    )
    frozen = service.freeze(
        POC_ID,
        idempotency_key="freeze-managed-captured",
    ).value

    assert calls == 1
    assert frozen.workload.fixture_path.endswith(
        "/bundles/bae216f2165eb06ae2e0f14d3cd852f8e0ebb381bf1f68c71072769b3c0c1675/workload"
    )


def test_local_probe_runner_refuses_a_managed_external_bundle(tmp_path: Path):
    projection = _projection()
    service = _lifecycle([projection])
    _freeze_managed(service, projection)
    runner = ProcessLocalPOCPerformanceRunService(
        lifecycle=service,
        output_root=tmp_path.resolve(),
    )

    with pytest.raises(POCPerformanceRunConflict):
        runner.snapshot(POC_ID)


def test_import_cannot_switch_the_customer_confirmed_bundle(tmp_path: Path):
    projection = _projection()
    service = _lifecycle([projection])
    _freeze_managed(service, projection)
    runs_root = tmp_path / "empty-catalog"
    runs_root.mkdir()
    worker_calls = 0

    def launch(target) -> None:
        nonlocal worker_calls
        worker_calls += 1

    importer = ProcessLocalPOCInferdromeImportService(
        lifecycle=service,
        catalog=InferdromeBundleCatalog(runs_root.resolve()),
        output_root=(tmp_path / "packs").resolve(),
        worker_launcher=launch,
        clock=lambda: NOW,
    )

    with pytest.raises(POCInferdromeImportConflict):
        importer.start(
            POC_ID,
            import_acknowledged=True,
            run_id=projection.run_id,
            bundle_digest="sha256:" + "0" * 64,
            idempotency_key="switch-managed-selection",
        )

    assert worker_calls == 0


def test_exact_a10_bundle_completes_dynamic_managed_pass_loop(tmp_path: Path):
    archive_value = os.environ.get("EXITSPEC_INFERDROME_A10_ARCHIVE")
    if archive_value is None:
        pytest.skip("exact external A10 archive is not available")
    extracted = extract_pinned_inferdrome_archive(
        Path(archive_value),
        tmp_path / "a10",
    )
    projection = project_managed_inferdrome_evidence(
        verify_inferdrome_bundle(
            extracted.bundle_path,
            require_customer_eligible=True,
        )
    )
    service = _lifecycle([projection])
    bundle = _freeze_managed(service, projection)
    importer = ProcessLocalPOCInferdromeImportService(
        lifecycle=service,
        catalog=InferdromeBundleCatalog(extracted.bundle_path),
        output_root=(tmp_path / "managed-packs").resolve(),
        worker_launcher=lambda target: target(),
        clock=lambda: NOW,
    )

    importer.start(
        POC_ID,
        import_acknowledged=True,
        run_id=projection.run_id,
        bundle_digest=projection.bundle_digest,
        idempotency_key="import-exact-managed-a10",
    )
    snapshot = importer.snapshot(POC_ID)

    assert snapshot.status is POCInferdromeImportStatus.COMPLETED
    assert snapshot.verdict is not None and snapshot.verdict.value == "PASS"
    assert snapshot.p95_ttft_ms == "14.797213"
    assert snapshot.attempted_count == snapshot.successful_count == 100
    assert snapshot.error_count == 0
    assert snapshot.anomalous_count == 0
    assert snapshot.concurrency == 4
    assert snapshot.observed_configured_max_concurrency == 4
    assert snapshot.receipt_id is not None
    assert snapshot.receipt_id.startswith("irc2_")
    assert snapshot.bundle_digest == projection.bundle_digest
    assert bundle.approved_contract.workload.fixture_path.endswith(
        "/bundles/bae216f2165eb06ae2e0f14d3cd852f8e0ebb381bf1f68c71072769b3c0c1675/workload"
    )
    assert snapshot.operation_id is not None
    assert len(
        importer.verified_evidence_pack_sha256(
            POC_ID,
            snapshot.operation_id,
        )
    ) == 64

    mismatch_service = _lifecycle([projection], concurrency=8)
    _freeze_managed(mismatch_service, projection)
    mismatch_importer = ProcessLocalPOCInferdromeImportService(
        lifecycle=mismatch_service,
        catalog=InferdromeBundleCatalog(extracted.bundle_path),
        output_root=(tmp_path / "managed-mismatch-packs").resolve(),
        worker_launcher=lambda target: target(),
        clock=lambda: NOW,
    )
    mismatch_importer.start(
        POC_ID,
        import_acknowledged=True,
        run_id=projection.run_id,
        bundle_digest=projection.bundle_digest,
        idempotency_key="import-managed-a10-concurrency-eight",
    )
    mismatch = mismatch_importer.snapshot(POC_ID)

    assert mismatch.verdict is not None
    assert mismatch.verdict.value == "NOT_PROVEN"
    assert mismatch.concurrency == 8
    assert mismatch.observed_configured_max_concurrency == 4
    assert mismatch.applicability_codes == (
        "CONFIGURED_CONCURRENCY_MISMATCH",
    )
