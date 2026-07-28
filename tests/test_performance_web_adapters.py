from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from exitspec.performance_artifacts import (
    PerformanceArtifactInputs,
    REDACTION_REDACTED,
    VerifiedPerformanceArtifacts,
    persist_performance_artifacts,
)
from exitspec.performance_operations import (
    PerformanceOperation,
    PerformanceOperationStatus,
)
from exitspec.performance_probe import ProbeRun
from exitspec.performance_runner import (
    PerformanceRunResult,
    PerformanceRunnerError,
)
from exitspec.performance_web_adapters import (
    PerformanceBundlePaths,
    PerformanceEvidenceSubject,
    PerformanceWebAdapterError,
    TrustedPerformanceEvidenceVerifier,
    TrustedPerformanceReadinessAdapter,
    TrustedPerformanceRunnerAdapter,
    materialized_performance_bundle,
)
from exitspec.performance_web_runtime import (
    PerformanceWebExecution,
    PerformanceWebStatus,
)
from exitspec.performance_workspace import load_performance_demo_bundle


RUN_ID = "run_" + "a" * 32
NOW = datetime(2026, 7, 28, tzinfo=timezone.utc)
SECRET = "server-owned-secret"


def _execution(
    tmp_path: Path,
    *,
    api_key: str | None = SECRET,
) -> PerformanceWebExecution:
    return PerformanceWebExecution(
        bundle=load_performance_demo_bundle(),
        output_root=tmp_path,
        operation_database_path=tmp_path / "operations.sqlite3",
        api_key=api_key,
    )


def _probe_run() -> ProbeRun:
    bundle = load_performance_demo_bundle()
    return ProbeRun(
        execution_id=RUN_ID,
        manifest=bundle.context.expected_manifest,
        records_sha256="b" * 64,
        records=(),
    )


def _operation(
    status: PerformanceOperationStatus,
    *,
    registry_hash: str | None = None,
) -> PerformanceOperation:
    return PerformanceOperation(
        idempotency_key_digest="c" * 64,
        input_digest="d" * 64,
        run_id=RUN_ID,
        status=status,
        created_at=NOW,
        updated_at=NOW,
        execution_id=("run_" + "e" * 32)
        if status is PerformanceOperationStatus.COMPLETED
        else None,
        receipt_id=("prc_" + "f" * 64)
        if status is PerformanceOperationStatus.COMPLETED
        else None,
        artifact_registry_sha256=registry_hash,
        terminal_reason=(
            "ENDPOINT_PREFLIGHT_FAILED"
            if status is PerformanceOperationStatus.BLOCKED
            else None
        ),
    )


def _verified_artifacts(
    run_dir: Path,
    *,
    registry: bytes = b'{"safe":"registry"}',
    packet: bytes = b"verified packet",
) -> VerifiedPerformanceArtifacts:
    return VerifiedPerformanceArtifacts(
        run_id=RUN_ID,
        run_dir=run_dir.resolve(),
        files={
            "evidence-artifacts.json": registry,
            "decision-packet.html": packet,
        },
    )


@contextmanager
def _fake_bundle(execution: PerformanceWebExecution):
    root = execution.output_root / "trusted-bundle"
    yield PerformanceBundlePaths(
        root=root,
        contract_path=(
            root
            / "examples/inference-performance/contracts/"
            "vllm-ttft-v2.frozen.json"
        ),
        confirmation_path=(
            root
            / "examples/inference-performance/contracts/"
            "vllm-ttft-v2.confirmation.json"
        ),
    )


def _json_bytes(**values: object) -> bytes:
    return json.dumps(values, indent=2).encode("utf-8")


def _artifact_inputs() -> PerformanceArtifactInputs:
    return PerformanceArtifactInputs(
        contract_json=_json_bytes(id="demo", version="1"),
        confirmation_json=_json_bytes(confirmation_id="cnf_demo"),
        workload_json=_json_bytes(request_count=100),
        prompt_fixture_jsonl=b'{"prompt_id":"p1","text":"alpha"}',
        preflight_json=_json_bytes(outcome="SUCCESS"),
        probe_manifest_json=_json_bytes(
            schema_version="probe.v1",
            manifest_sha256="a" * 64,
        ),
        records_jsonl=b'{"outcome":"SUCCESS","request_id":"r1"}',
        receipt_json=_json_bytes(receipt_id="prc_" + "b" * 64),
        calculations_json=_json_bytes(p95_ttft_ms=100),
        verdicts_json=_json_bytes(verdict="PASS"),
        decision_packet_html=(
            b"<!doctype html><html><body>PASS</body></html>"
        ),
        redaction_states={
            "prompt-fixture.jsonl": REDACTION_REDACTED,
            "evidence/probe-records.jsonl": REDACTION_REDACTED,
        },
    )


def test_readiness_pins_exact_execution_credential_and_context(
    tmp_path: Path,
):
    captured = {}

    def transport_factory(api_key, *, credential_endpoint):
        captured["api_key"] = api_key
        captured["credential_endpoint"] = credential_endpoint
        return object()

    def preflight(context, transport):
        captured["context"] = context
        captured["transport"] = transport
        return _probe_run(), None

    execution = _execution(tmp_path)
    result = TrustedPerformanceReadinessAdapter(
        preflight=preflight,
        transport_factory=transport_factory,
    )(execution)

    assert result.status is PerformanceWebStatus.COMPLETED
    assert captured["context"] is execution.bundle.context
    assert captured["api_key"] == SECRET
    assert captured["credential_endpoint"] == execution.endpoint
    assert captured["transport"] is not None


@pytest.mark.parametrize(
    ("operation_status", "expected"),
    (
        (
            PerformanceOperationStatus.BLOCKED,
            PerformanceWebStatus.BLOCKED,
        ),
        (
            PerformanceOperationStatus.NOT_PROVEN,
            PerformanceWebStatus.NOT_PROVEN,
        ),
    ),
)
def test_readiness_preserves_external_block_vs_not_proven(
    tmp_path: Path,
    operation_status: PerformanceOperationStatus,
    expected: PerformanceWebStatus,
):
    adapter = TrustedPerformanceReadinessAdapter(
        transport_factory=lambda *args, **kwargs: object(),
        preflight=lambda context, transport: (
            _probe_run(),
            (operation_status, "SAFE_REASON"),
        ),
    )

    assert adapter(_execution(tmp_path)).status is expected


def test_readiness_malformed_or_internal_failure_is_not_proven(
    tmp_path: Path,
):
    malformed = TrustedPerformanceReadinessAdapter(
        transport_factory=lambda *args, **kwargs: object(),
        preflight=lambda context, transport: ("not-a-probe", None),
    )
    secret_failure = TrustedPerformanceReadinessAdapter(
        transport_factory=lambda *args, **kwargs: object(),
        preflight=lambda context, transport: (_ for _ in ()).throw(
            RuntimeError("provider said " + SECRET)
        ),
    )

    assert malformed(_execution(tmp_path)).status is (
        PerformanceWebStatus.NOT_PROVEN
    )
    result = secret_failure(_execution(tmp_path))
    assert result.status is PerformanceWebStatus.NOT_PROVEN
    assert SECRET not in repr(result)


def test_runner_forwards_only_pinned_inputs_exact_111_and_key(
    tmp_path: Path,
):
    captured = {}
    run_dir = tmp_path / RUN_ID
    run_dir.mkdir()
    artifacts = _verified_artifacts(run_dir)
    registry_hash = hashlib.sha256(artifacts.registry_json).hexdigest()

    def runner(**kwargs):
        captured.update(kwargs)
        return PerformanceRunResult(
            operation=_operation(
                PerformanceOperationStatus.COMPLETED,
                registry_hash=registry_hash,
            ),
            replayed=False,
            artifacts=artifacts,
        )

    execution = _execution(tmp_path)
    result = TrustedPerformanceRunnerAdapter(
        runner=runner,
        bundle_factory=_fake_bundle,
    )(execution, "browser-key")

    assert result.status is PerformanceWebStatus.COMPLETED
    assert type(result.artifact_subject) is PerformanceEvidenceSubject
    assert captured == {
        "contract_path": (
            tmp_path
            / "trusted-bundle/examples/inference-performance/contracts/"
            "vllm-ttft-v2.frozen.json"
        ),
        "confirmation_path": (
            tmp_path
            / "trusted-bundle/examples/inference-performance/contracts/"
            "vllm-ttft-v2.confirmation.json"
        ),
        "bundle_root": tmp_path / "trusted-bundle",
        "output_root": tmp_path,
        "idempotency_key": "browser-key",
        "api_key": SECRET,
        "credential_endpoint": execution.endpoint,
        "authorized_request_count": 111,
        "operation_database_path": tmp_path / "operations.sqlite3",
    }
    assert "endpoint" not in captured
    assert "model" not in captured
    assert "request_count" not in captured


@pytest.mark.parametrize(
    ("operation_status", "expected"),
    (
        (
            PerformanceOperationStatus.BLOCKED,
            PerformanceWebStatus.BLOCKED,
        ),
        (
            PerformanceOperationStatus.NOT_PROVEN,
            PerformanceWebStatus.NOT_PROVEN,
        ),
        (
            PerformanceOperationStatus.FAILED,
            PerformanceWebStatus.NOT_PROVEN,
        ),
        (
            PerformanceOperationStatus.RUNNING,
            PerformanceWebStatus.NOT_PROVEN,
        ),
    ),
)
def test_runner_maps_terminal_and_orphan_states_without_artifacts(
    tmp_path: Path,
    operation_status: PerformanceOperationStatus,
    expected: PerformanceWebStatus,
):
    def runner(**kwargs):
        return PerformanceRunResult(
            operation=_operation(operation_status),
            replayed=False,
        )

    result = TrustedPerformanceRunnerAdapter(
        runner=runner,
        bundle_factory=_fake_bundle,
    )(_execution(tmp_path), "key")

    assert result.status is expected
    assert result.artifact_subject is None


def test_runner_exception_and_malformed_result_fail_closed_without_secret(
    tmp_path: Path,
):
    def failing_runner(**kwargs):
        raise PerformanceRunnerError("upstream exposed " + SECRET)

    adapter = TrustedPerformanceRunnerAdapter(
        runner=failing_runner,
        bundle_factory=_fake_bundle,
    )
    result = adapter(_execution(tmp_path), "exact-key")

    assert result.status is PerformanceWebStatus.NOT_PROVEN
    assert result.artifact_subject is None
    assert SECRET not in repr(result)
    assert SECRET not in repr(adapter)

    malformed = TrustedPerformanceRunnerAdapter(
        runner=lambda **kwargs: object(),
        bundle_factory=_fake_bundle,
    )(_execution(tmp_path), "exact-key")
    assert malformed.status is PerformanceWebStatus.NOT_PROVEN


def test_evidence_verifier_reloads_reconstructs_and_rerenders(
    tmp_path: Path,
):
    run_dir = tmp_path / RUN_ID
    run_dir.mkdir()
    verified = _verified_artifacts(run_dir)
    registry_hash = hashlib.sha256(verified.registry_json).hexdigest()
    subject = PerformanceEvidenceSubject(
        run_id=RUN_ID,
        artifact_registry_sha256=registry_hash,
        confirmation_idempotency_key="confirmation-key",
    )
    calls = []

    def reader(path):
        calls.append(("read", path))
        return verified

    reconstructed = SimpleNamespace(
        context=load_performance_demo_bundle().context,
        decision=object(),
        probe_run=object(),
    )

    def reconstructor(artifacts, *, confirmation_idempotency_key):
        calls.append(
            (
                "reconstruct",
                artifacts,
                confirmation_idempotency_key,
            )
        )
        return reconstructed

    def renderer(decision, context, probe_run):
        calls.append(("render", decision, context, probe_run))
        return verified.decision_packet_html

    url = TrustedPerformanceEvidenceVerifier(
        output_root=tmp_path,
        reader=reader,
        reconstructor=reconstructor,
        renderer=renderer,
    )(subject)

    assert url == f"/artifacts/{RUN_ID}/decision-packet.html"
    assert calls[0] == ("read", run_dir)
    assert calls[1][0] == "reconstruct"
    assert calls[1][2] == "confirmation-key"
    assert calls[2][0] == "render"


def test_tampered_artifact_fails_before_url_release(tmp_path: Path):
    verified = persist_performance_artifacts(
        tmp_path,
        RUN_ID,
        _artifact_inputs(),
    )
    registry_hash = hashlib.sha256(verified.registry_json).hexdigest()
    subject = PerformanceEvidenceSubject(
        run_id=RUN_ID,
        artifact_registry_sha256=registry_hash,
        confirmation_idempotency_key="confirmation-key",
    )
    target = verified.run_dir / "verdicts.json"
    target.write_bytes(target.read_bytes() + b" ")

    with pytest.raises(
        PerformanceWebAdapterError,
        match="independent verification",
    ):
        TrustedPerformanceEvidenceVerifier(
            output_root=tmp_path,
        )(subject)


def test_evidence_verifier_rejects_registry_mismatch_and_forged_subject(
    tmp_path: Path,
):
    run_dir = tmp_path / RUN_ID
    run_dir.mkdir()
    verified = _verified_artifacts(run_dir)
    subject = PerformanceEvidenceSubject(
        run_id=RUN_ID,
        artifact_registry_sha256="0" * 64,
        confirmation_idempotency_key="confirmation-key",
    )

    with pytest.raises(PerformanceWebAdapterError):
        TrustedPerformanceEvidenceVerifier(
            output_root=tmp_path,
            reader=lambda path: verified,
        )(subject)
    with pytest.raises(ValueError, match="run identity"):
        PerformanceEvidenceSubject(
            run_id="../../outside",
            artifact_registry_sha256="0" * 64,
            confirmation_idempotency_key="confirmation-key",
        )


def test_evidence_verifier_rejects_symlink_escape_and_outside_root(
    tmp_path: Path,
):
    outside = tmp_path.parent / (tmp_path.name + "-outside")
    outside.mkdir()
    alias = tmp_path / RUN_ID
    alias.symlink_to(outside, target_is_directory=True)
    subject = PerformanceEvidenceSubject(
        run_id=RUN_ID,
        artifact_registry_sha256="0" * 64,
        confirmation_idempotency_key="confirmation-key",
    )

    with pytest.raises(PerformanceWebAdapterError):
        TrustedPerformanceEvidenceVerifier(
            output_root=tmp_path,
            reader=lambda path: object(),
        )(subject)


def test_subject_and_adapter_exceptions_never_reveal_confirmation_or_api_key(
    tmp_path: Path,
):
    subject = PerformanceEvidenceSubject(
        run_id=RUN_ID,
        artifact_registry_sha256="0" * 64,
        confirmation_idempotency_key="confirmation-super-secret",
    )

    assert "confirmation-super-secret" not in repr(subject)
    assert SECRET not in repr(_execution(tmp_path))
    try:
        TrustedPerformanceEvidenceVerifier(output_root=tmp_path)(subject)
    except PerformanceWebAdapterError as error:
        assert "confirmation-super-secret" not in repr(error)
        assert SECRET not in repr(error)


def test_materialized_bundle_contains_exact_frozen_resources(tmp_path: Path):
    execution = _execution(tmp_path, api_key=None)

    with materialized_performance_bundle(execution) as paths:
        assert paths.contract_path.read_bytes() == (
            Path(
                "src/exitspec/demo_data/inference_performance/contracts/"
                "vllm-ttft-v2.frozen.json"
            ).read_bytes()
        )
        assert paths.confirmation_path.read_bytes() == (
            Path(
                "src/exitspec/demo_data/inference_performance/contracts/"
                "vllm-ttft-v2.confirmation.json"
            ).read_bytes()
        )
        assert (
            paths.root
            / "examples/inference-performance/workloads/"
            "concurrency-4-v1.json"
        ).is_file()
        assert (
            paths.root
            / "examples/inference-performance/prompts/"
            "synthetic-latency-v1.jsonl"
        ).is_file()
