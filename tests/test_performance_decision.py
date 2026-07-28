from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from exitspec.confirmations import (
    ConfirmationDecision,
    ContractConfirmation,
    record_confirmation,
)
from exitspec.contracts import freeze_confirmed_contract
from exitspec.models import VerdictStatus
from exitspec.performance_decision import (
    AuthorizedPerformanceDecision,
    PerformanceDecisionErrorCode,
    PerformanceDecisionIntegrityError,
    authorize_performance_decision,
)
from exitspec.performance_evidence import (
    ValidatedPerformanceContext,
    validate_performance_context,
)
from exitspec.performance_probe import (
    PROBE_SCHEMA_VERSION,
    ProbeConfig,
    ProbeManifest,
    ProbeOutcome,
    ProbePhase,
    ProbeRecord,
    ProbeRun,
    SyntheticPrompt,
    build_manifest,
    records_jsonl,
)
from exitspec.performance_receipts import (
    InMemoryPerformanceReceiptStore,
    PerformanceExecutionReceipt,
)
from exitspec.performance_verdicts import PerformanceCriterionVerdict
from exitspec.runner import load_contract


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = (
    PROJECT_ROOT
    / "examples/inference-performance/contracts/vllm-ttft-v1.yaml"
)
WORKLOAD_PATH = (
    PROJECT_ROOT
    / "examples/inference-performance/workloads/concurrency-4-v1.json"
)
FIXED_TIME = datetime(2026, 7, 28, 12, 0, tzinfo=timezone.utc)
EXECUTION_ID = "run_" + "a" * 32


def _confirmation(
    *,
    key: str = "confirm-performance-decision-v1",
) -> ContractConfirmation:
    approved = load_contract(CONTRACT_PATH)
    return record_confirmation(
        approved,
        confirmer_identity="customer@example.com",
        decision=ConfirmationDecision.CONFIRM,
        agreement_acknowledged=True,
        rationale="The frozen performance requirements are correct.",
        idempotency_key=key,
        decided_at=FIXED_TIME,
    )


def _authorized_context() -> tuple[
    ValidatedPerformanceContext,
    ContractConfirmation,
]:
    approved = load_contract(CONTRACT_PATH)
    confirmation = _confirmation()
    frozen = freeze_confirmed_contract(
        approved,
        confirmation,
        FIXED_TIME,
    )
    context = validate_performance_context(
        frozen,
        WORKLOAD_PATH.read_bytes(),
        bundle_root=PROJECT_ROOT,
    )
    return context, confirmation


def _records_hash(records: tuple[ProbeRecord, ...]) -> str:
    return hashlib.sha256(
        records_jsonl(records).encode("utf-8")
    ).hexdigest()


def _probe_run(
    manifest: ProbeManifest,
    *,
    execution_id: str = EXECUTION_ID,
    errors: int = 0,
    ttft_ns: int = 100_000_000,
) -> ProbeRun:
    records: list[ProbeRecord] = []
    for phase, count in (
        (ProbePhase.WARMUP, manifest.warmup_count),
        (ProbePhase.MEASURED, manifest.request_count),
    ):
        for ordinal in range(1, count + 1):
            descriptor = manifest.prompts[
                (ordinal - 1) % len(manifest.prompts)
            ]
            measured_error = (
                phase is ProbePhase.MEASURED
                and ordinal > manifest.request_count - errors
            )
            outcome = (
                ProbeOutcome.HTTP_ERROR
                if measured_error
                else ProbeOutcome.SUCCESS
            )
            records.append(
                ProbeRecord(
                    schema_version=PROBE_SCHEMA_VERSION,
                    execution_id=execution_id,
                    manifest_sha256=manifest.manifest_sha256,
                    request_id=(
                        "warmup"
                        if phase is ProbePhase.WARMUP
                        else "measured"
                    )
                    + f"-{ordinal:05d}",
                    phase=phase,
                    ordinal=ordinal,
                    included_in_measurement=(
                        phase is ProbePhase.MEASURED
                    ),
                    prompt_id=descriptor.prompt_id,
                    prompt_sha256=descriptor.sha256,
                    outcome=outcome,
                    http_status=429 if measured_error else 200,
                    ttft_ns=None if measured_error else ttft_ns,
                    duration_ns=(
                        ttft_ns + 1
                        if not measured_error
                        else 1
                    ),
                )
            )
    ordered = tuple(records)
    return ProbeRun(
        execution_id=execution_id,
        manifest=manifest,
        records_sha256=_records_hash(ordered),
        records=ordered,
    )


def _receipt(
    context: ValidatedPerformanceContext,
    run: ProbeRun,
    **changes: object,
) -> PerformanceExecutionReceipt:
    contract_hash = context.contract.canonical_hash
    assert contract_hash is not None
    fields: dict[str, object] = {
        "idempotency_key": "performance-execution-v1",
        "contract_id": context.contract.id,
        "contract_version": context.contract.version,
        "frozen_contract_hash": contract_hash,
        "criterion_id": context.criterion.id,
        "expected_manifest_sha256": (
            context.expected_manifest.manifest_sha256
        ),
        "execution_id": run.execution_id,
        "records_sha256": run.records_sha256,
        "created_at": FIXED_TIME,
    }
    fields.update(changes)
    return InMemoryPerformanceReceiptStore().record_receipt(**fields)


def _assert_not_proven(
    error: pytest.ExceptionInfo[PerformanceDecisionIntegrityError],
    code: PerformanceDecisionErrorCode,
) -> None:
    assert error.value.code is code
    assert error.value.verdict is VerdictStatus.NOT_PROVEN


def test_valid_zero_of_100_chain_returns_immutable_authorized_pass():
    context, confirmation = _authorized_context()
    run = _probe_run(context.expected_manifest)
    receipt = _receipt(context, run)

    decision = authorize_performance_decision(
        context,
        confirmation,
        run,
        receipt,
    )

    assert isinstance(decision, AuthorizedPerformanceDecision)
    assert isinstance(
        decision.performance_verdict,
        PerformanceCriterionVerdict,
    )
    assert decision.receipt is not receipt
    assert decision.receipt == receipt
    assert decision.performance_verdict.verdict is VerdictStatus.PASS
    assert decision.performance_verdict.error_count == 0
    assert decision.performance_verdict.attempted_count == 100
    with pytest.raises(AttributeError):
        decision.receipt = receipt  # type: ignore[misc]


def test_valid_one_of_100_chain_returns_fail_for_strict_error_limit():
    context, confirmation = _authorized_context()
    run = _probe_run(context.expected_manifest, errors=1)
    receipt = _receipt(context, run)

    decision = authorize_performance_decision(
        context,
        confirmation,
        run,
        receipt,
    )

    assert decision.performance_verdict.error_count == 1
    assert decision.performance_verdict.attempted_count == 100
    assert decision.performance_verdict.error_rate.verdict is VerdictStatus.FAIL
    assert decision.performance_verdict.verdict is VerdictStatus.FAIL


def test_recomputed_forged_workload_never_reaches_evaluator(
    monkeypatch,
):
    context, confirmation = _authorized_context()
    forged_config = ProbeConfig(
        endpoint="http://127.0.0.1:9999/v1/chat/completions",
        model="attacker/different-model",
        request_count=100,
        concurrency=1,
        warmup_count=0,
        timeout_seconds=1,
        max_tokens=context.probe_config.max_tokens,
        max_stream_bytes=context.probe_config.max_stream_bytes,
    )
    forged_manifest = build_manifest(
        forged_config,
        (SyntheticPrompt("attacker-prompt", "different prompt set"),),
    )
    forged_run = _probe_run(forged_manifest)
    receipt = _receipt(context, forged_run)

    def forbidden_evaluator(*_args, **_kwargs):
        raise AssertionError("evaluator must not receive forged evidence")

    monkeypatch.setattr(
        "exitspec.performance_decision.evaluate_performance_criterion",
        forbidden_evaluator,
    )
    with pytest.raises(PerformanceDecisionIntegrityError) as error:
        authorize_performance_decision(
            context,
            confirmation,
            forged_run,
            receipt,
        )

    _assert_not_proven(
        error,
        PerformanceDecisionErrorCode.CROSS_BINDING_MISMATCH,
    )
    assert forged_manifest.manifest_sha256 != (
        context.expected_manifest.manifest_sha256
    )


def test_record_and_self_hash_mutation_conflicts_with_verified_receipt(
    monkeypatch,
):
    context, confirmation = _authorized_context()
    original_run = _probe_run(context.expected_manifest)
    receipt = _receipt(context, original_run)
    measured_index = context.expected_manifest.warmup_count
    measured_record = original_run.records[measured_index]
    mutated_measured_record = replace(measured_record, ttft_ns=0)
    mutated_records = (
        *original_run.records[:measured_index],
        mutated_measured_record,
        *original_run.records[measured_index + 1 :],
    )
    mutated_run = replace(
        original_run,
        records=mutated_records,
        records_sha256=_records_hash(mutated_records),
    )

    def forbidden_evaluator(*_args, **_kwargs):
        raise AssertionError("evaluator must not receive mutated evidence")

    monkeypatch.setattr(
        "exitspec.performance_decision.evaluate_performance_criterion",
        forbidden_evaluator,
    )
    with pytest.raises(PerformanceDecisionIntegrityError) as error:
        authorize_performance_decision(
            context,
            confirmation,
            mutated_run,
            receipt,
        )

    _assert_not_proven(
        error,
        PerformanceDecisionErrorCode.CROSS_BINDING_MISMATCH,
    )
    assert mutated_run.records_sha256 != original_run.records_sha256


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("contract_id", "different-contract"),
        ("contract_version", "9.9.9"),
        ("frozen_contract_hash", "f" * 64),
        ("criterion_id", "DIFFERENT-CRITERION"),
        ("expected_manifest_sha256", "e" * 64),
        ("execution_id", "run_" + "b" * 32),
        ("records_sha256", "d" * 64),
    ],
)
def test_wrong_but_internally_valid_receipt_is_not_proven(
    field,
    replacement,
):
    context, confirmation = _authorized_context()
    run = _probe_run(context.expected_manifest)
    receipt = _receipt(context, run, **{field: replacement})

    with pytest.raises(PerformanceDecisionIntegrityError) as error:
        authorize_performance_decision(
            context,
            confirmation,
            run,
            receipt,
        )

    _assert_not_proven(
        error,
        PerformanceDecisionErrorCode.CROSS_BINDING_MISMATCH,
    )


def test_tampered_receipt_identity_is_not_proven():
    context, confirmation = _authorized_context()
    run = _probe_run(context.expected_manifest)
    receipt = _receipt(context, run)
    tampered = receipt.model_copy(
        update={"records_sha256": "d" * 64}
    )

    with pytest.raises(PerformanceDecisionIntegrityError) as error:
        authorize_performance_decision(
            context,
            confirmation,
            run,
            tampered,
        )

    _assert_not_proven(
        error,
        PerformanceDecisionErrorCode.RECEIPT_INTEGRITY_INVALID,
    )


def test_wrong_confirmation_is_not_proven_before_evaluation(monkeypatch):
    context, _confirmation_record = _authorized_context()
    wrong_confirmation = _confirmation(key="different-confirmation")
    run = _probe_run(context.expected_manifest)
    receipt = _receipt(context, run)

    def forbidden_evaluator(*_args, **_kwargs):
        raise AssertionError("evaluator must not run without confirmation")

    monkeypatch.setattr(
        "exitspec.performance_decision.evaluate_performance_criterion",
        forbidden_evaluator,
    )
    with pytest.raises(PerformanceDecisionIntegrityError) as error:
        authorize_performance_decision(
            context,
            wrong_confirmation,
            run,
            receipt,
        )

    _assert_not_proven(
        error,
        PerformanceDecisionErrorCode.CONTEXT_NOT_AUTHORIZED,
    )


def test_mixed_execution_records_are_not_proven_before_evaluation(
    monkeypatch,
):
    context, confirmation = _authorized_context()
    run = _probe_run(context.expected_manifest)
    mixed_first = replace(
        run.records[0],
        execution_id="run_" + "c" * 32,
    )
    mixed_records = (mixed_first, *run.records[1:])
    mixed_run = replace(
        run,
        records=mixed_records,
        records_sha256=_records_hash(mixed_records),
    )
    receipt = _receipt(context, mixed_run)

    def forbidden_evaluator(*_args, **_kwargs):
        raise AssertionError("evaluator must not receive mixed evidence")

    monkeypatch.setattr(
        "exitspec.performance_decision.evaluate_performance_criterion",
        forbidden_evaluator,
    )
    with pytest.raises(PerformanceDecisionIntegrityError) as error:
        authorize_performance_decision(
            context,
            confirmation,
            mixed_run,
            receipt,
        )

    _assert_not_proven(
        error,
        PerformanceDecisionErrorCode.PROBE_INTEGRITY_INVALID,
    )


def test_missing_probe_or_receipt_is_typed_not_proven():
    context, confirmation = _authorized_context()
    run = _probe_run(context.expected_manifest)
    receipt = _receipt(context, run)

    with pytest.raises(PerformanceDecisionIntegrityError) as probe_error:
        authorize_performance_decision(
            context,
            confirmation,
            None,  # type: ignore[arg-type]
            receipt,
        )
    with pytest.raises(PerformanceDecisionIntegrityError) as receipt_error:
        authorize_performance_decision(
            context,
            confirmation,
            run,
            None,  # type: ignore[arg-type]
        )

    _assert_not_proven(
        probe_error,
        PerformanceDecisionErrorCode.PROBE_INTEGRITY_INVALID,
    )
    _assert_not_proven(
        receipt_error,
        PerformanceDecisionErrorCode.RECEIPT_INTEGRITY_INVALID,
    )
