"""Authoritative execution boundary for one inference-performance proof loop.

Only this module joins a frozen agreement, durable pre-network reservation,
bounded live probe, receipt, deterministic verdict, atomic artifacts, and a
customer Evidence Pack. Filesystem hash verification alone never authorizes a
verdict; every returned decision is reconstructed and recalculated.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Final

from pydantic import ValidationError

from .canonical import canonical_json_bytes
from .confirmations import ContractConfirmation
from .models import VerdictStatus
from .performance_artifacts import (
    PerformanceArtifactInputs,
    REDACTION_SYNTHETIC_NO_PII,
    VerifiedPerformanceArtifacts,
    persist_performance_artifacts,
    read_and_verify_performance_artifacts,
)
from .performance_decision import (
    AuthorizedPerformanceDecision,
    authorize_performance_decision,
)
from .performance_evidence import (
    ValidatedPerformanceContext,
    require_frozen_confirmed,
    validate_performance_context,
    validate_performance_context_bytes,
)
from .performance_operations import (
    PerformanceOperation,
    PerformanceOperationStatus,
    SQLitePerformanceOperationLedger,
)
from .performance_probe import (
    OpenAIHTTPTransport,
    ProbeConfig,
    ProbeOutcome,
    ProbePhase,
    ProbeRun,
    run_probe,
)
from .performance_receipts import (
    InMemoryPerformanceReceiptStore,
    PerformanceExecutionReceipt,
)
from .performance_reporting import render_performance_evidence_pack
from .performance_serialization import (
    PerformanceSerializationError,
    parse_confirmation,
    parse_contract,
    parse_performance_receipt,
    parse_performance_verdict_display,
    parse_probe_run,
    recompute_and_compare_performance_verdict,
    serialize_confirmation,
    serialize_contract,
    serialize_performance_receipt,
    serialize_performance_verdict,
    serialize_probe_run,
)


PERFORMANCE_RUNNER_VERSION: Final = "exitspec.performance-runner.v1"
_MAX_CONFIRMATION_BYTES: Final = 1024 * 1024
_EXTERNAL_PREFLIGHT_OUTCOMES: Final = frozenset(
    {
        ProbeOutcome.HTTP_ERROR,
        ProbeOutcome.TIMEOUT,
        ProbeOutcome.PROTOCOL_ERROR,
        ProbeOutcome.TRANSPORT_ERROR,
    }
)


class PerformanceRunnerError(RuntimeError):
    """The authoritative performance loop could not complete safely."""


@dataclass(frozen=True, slots=True)
class PerformanceRunResult:
    """A runner result; a verdict exists only after full artifact reconstruction."""

    operation: PerformanceOperation
    replayed: bool
    artifacts: VerifiedPerformanceArtifacts | None = None
    context: ValidatedPerformanceContext | None = None
    probe_run: ProbeRun | None = None
    decision: AuthorizedPerformanceDecision | None = None

    @property
    def verdict(self) -> VerdictStatus | None:
        if self.decision is None:
            return None
        return self.decision.performance_verdict.verdict


def run_performance_proof(
    *,
    contract_path: Path,
    confirmation_path: Path,
    bundle_root: Path,
    output_root: Path,
    idempotency_key: str,
    api_key: str | None = None,
    operation_database_path: Path | None = None,
    clock: Callable[[], datetime] | None = None,
) -> PerformanceRunResult:
    """Run or safely replay one exact frozen inference-performance operation."""

    resolved_clock = clock or _utc_now
    if not callable(resolved_clock):
        raise TypeError("clock must be callable.")
    contract = _load_contract_source(contract_path)
    confirmation = _load_confirmation_source(confirmation_path)
    workload_bytes = _read_bound_workload(
        bundle_root,
        contract.workload.fixture_path,
    )
    context = validate_performance_context(
        contract,
        workload_bytes,
        bundle_root=bundle_root,
    )
    require_frozen_confirmed(context, confirmation)
    contract_hash = contract.canonical_hash
    if contract_hash is None:
        raise PerformanceRunnerError("Frozen contract hash is missing.")

    output = _prepare_output_root(output_root)
    database_path = (
        operation_database_path
        if operation_database_path is not None
        else output / "performance-operations.sqlite3"
    )
    ledger = SQLitePerformanceOperationLedger(
        database_path,
        clock=resolved_clock,
    )
    reservation = ledger.reserve(
        idempotency_key=idempotency_key,
        frozen_contract_hash=contract_hash,
        expected_manifest_hash=(
            context.expected_manifest.manifest_sha256
        ),
        workload_hash=context.workload_sha256,
        adapter=context.workload.adapter,
        adapter_version=context.workload.adapter_version,
    )
    operation = reservation.operation
    if not reservation.should_execute:
        try:
            return _replay_operation(
                operation,
                output=output,
                source_confirmation=confirmation,
            )
        except PerformanceRunnerError:
            raise
        except Exception as error:
            raise PerformanceRunnerError(
                "Persisted performance evidence failed closed during replay."
            ) from error

    transport = OpenAIHTTPTransport(api_key)
    try:
        preflight_status = _run_preflight(context, transport)
        if preflight_status is not None:
            terminal_status, reason = preflight_status
            terminal = ledger.mark_terminal(
                run_id=operation.run_id,
                input_digest=operation.input_digest,
                status=terminal_status,
                terminal_reason=reason,
            )
            return PerformanceRunResult(
                operation=terminal,
                replayed=False,
            )

        probe_run = run_probe(
            context.probe_config,
            context.prompts,
            transport=transport,
        )
        receipt = _issue_receipt(
            context,
            probe_run,
            idempotency_key=idempotency_key,
            created_at=resolved_clock(),
        )
        decision = authorize_performance_decision(
            context,
            confirmation,
            probe_run,
            receipt,
        )
        artifacts = _publish_decision(
            output,
            operation.run_id,
            context,
            confirmation,
            probe_run,
            receipt,
            decision,
        )
        reconstructed = _reconstruct_decision(
            artifacts,
            confirmation_idempotency_key=confirmation.idempotency_key,
        )
        registry_sha256 = hashlib.sha256(
            artifacts.registry_json
        ).hexdigest()
        terminal = ledger.mark_terminal(
            run_id=operation.run_id,
            input_digest=operation.input_digest,
            status=PerformanceOperationStatus.COMPLETED,
            execution_id=reconstructed.probe_run.execution_id,
            receipt_id=reconstructed.decision.receipt.receipt_id,
            artifact_registry_sha256=registry_sha256,
        )
        return _replay_completed(
            terminal,
            output=output,
            source_confirmation=confirmation,
            replayed=False,
        )
    except Exception as error:
        run_dir = output / operation.run_id
        if not run_dir.exists():
            try:
                ledger.mark_terminal(
                    run_id=operation.run_id,
                    input_digest=operation.input_digest,
                    status=PerformanceOperationStatus.FAILED,
                    terminal_reason="RUNNER_INTERNAL_FAILURE",
                )
            except Exception:
                pass
        if isinstance(error, PerformanceRunnerError):
            raise
        raise PerformanceRunnerError(
            "Performance proof failed closed before an authorized result "
            "could be returned."
        ) from error


def _run_preflight(
    context: ValidatedPerformanceContext,
    transport: OpenAIHTTPTransport,
) -> tuple[PerformanceOperationStatus, str] | None:
    """Use one bounded request to distinguish unavailable from measured failure."""

    config = context.probe_config
    preflight_config = ProbeConfig(
        endpoint=config.endpoint,
        model=config.model,
        request_count=1,
        concurrency=1,
        warmup_count=0,
        timeout_seconds=min(float(config.timeout_seconds), 5.0),
        max_tokens=1,
        max_stream_bytes=config.max_stream_bytes,
    )
    preflight = run_probe(
        preflight_config,
        context.prompts[:1],
        transport=transport,
    )
    measured = tuple(
        record
        for record in preflight.records
        if record.phase is ProbePhase.MEASURED
    )
    if len(measured) != 1:
        return (
            PerformanceOperationStatus.NOT_PROVEN,
            "PREFLIGHT_EVIDENCE_INVALID",
        )
    outcome = measured[0].outcome
    if outcome is ProbeOutcome.SUCCESS:
        return None
    if outcome in _EXTERNAL_PREFLIGHT_OUTCOMES:
        return (
            PerformanceOperationStatus.BLOCKED,
            "ENDPOINT_PREFLIGHT_FAILED",
        )
    return (
        PerformanceOperationStatus.NOT_PROVEN,
        "PREFLIGHT_NOT_PROVEN",
    )


def _issue_receipt(
    context: ValidatedPerformanceContext,
    probe_run: ProbeRun,
    *,
    idempotency_key: str,
    created_at: datetime,
) -> PerformanceExecutionReceipt:
    contract_hash = context.contract.canonical_hash
    if contract_hash is None:
        raise PerformanceRunnerError("Frozen contract hash is missing.")
    return InMemoryPerformanceReceiptStore().record_receipt(
        idempotency_key=idempotency_key,
        contract_id=context.contract.id,
        contract_version=context.contract.version,
        frozen_contract_hash=contract_hash,
        criterion_id=context.criterion.id,
        expected_manifest_sha256=(
            context.expected_manifest.manifest_sha256
        ),
        execution_id=probe_run.execution_id,
        records_sha256=probe_run.records_sha256,
        created_at=created_at,
    )


def _publish_decision(
    output: Path,
    run_id: str,
    context: ValidatedPerformanceContext,
    confirmation: ContractConfirmation,
    probe_run: ProbeRun,
    receipt: PerformanceExecutionReceipt,
    decision: AuthorizedPerformanceDecision,
) -> VerifiedPerformanceArtifacts:
    manifest_bytes, records_bytes = serialize_probe_run(probe_run)
    verdict_bytes = serialize_performance_verdict(
        decision.performance_verdict
    )
    report_bytes = render_performance_evidence_pack(
        decision,
        context,
        probe_run,
    )
    redaction_states = {
        path: REDACTION_SYNTHETIC_NO_PII
        for path in (
            "contract.json",
            "confirmation.json",
            "workload.json",
            "prompt-fixture.jsonl",
            "evidence/probe-manifest.json",
            "evidence/probe-records.jsonl",
            "receipt.json",
            "calculations.json",
            "verdicts.json",
            "decision-packet.html",
        )
    }
    return persist_performance_artifacts(
        output,
        run_id,
        PerformanceArtifactInputs(
            contract_json=serialize_contract(context.contract),
            confirmation_json=serialize_confirmation(confirmation),
            workload_json=context.workload_bytes,
            prompt_fixture_jsonl=context.prompt_bytes,
            probe_manifest_json=manifest_bytes,
            records_jsonl=records_bytes,
            receipt_json=serialize_performance_receipt(receipt),
            calculations_json=verdict_bytes,
            verdicts_json=verdict_bytes,
            decision_packet_html=report_bytes,
            redaction_states=redaction_states,
        ),
    )


def _replay_operation(
    operation: PerformanceOperation,
    *,
    output: Path,
    source_confirmation: ContractConfirmation,
) -> PerformanceRunResult:
    if operation.status is PerformanceOperationStatus.COMPLETED:
        return _replay_completed(
            operation,
            output=output,
            source_confirmation=source_confirmation,
            replayed=True,
        )
    # A RUNNING row may mean the process crashed after a paid request. Never
    # execute again and never auto-promote an orphaned directory to PASS.
    return PerformanceRunResult(
        operation=operation,
        replayed=True,
    )


def _replay_completed(
    operation: PerformanceOperation,
    *,
    output: Path,
    source_confirmation: ContractConfirmation,
    replayed: bool,
) -> PerformanceRunResult:
    if operation.status is not PerformanceOperationStatus.COMPLETED:
        raise PerformanceRunnerError(
            "Only a completed operation can expose an Evidence Pack."
        )
    artifacts = read_and_verify_performance_artifacts(
        output / operation.run_id
    )
    reconstructed = _reconstruct_decision(
        artifacts,
        confirmation_idempotency_key=(
            source_confirmation.idempotency_key
        ),
    )
    registry_sha256 = hashlib.sha256(
        artifacts.registry_json
    ).hexdigest()
    expected = (
        (operation.execution_id, reconstructed.probe_run.execution_id),
        (operation.receipt_id, reconstructed.decision.receipt.receipt_id),
        (operation.artifact_registry_sha256, registry_sha256),
    )
    if any(actual != bound for actual, bound in expected):
        raise PerformanceRunnerError(
            "Completed operation identities do not match the verified pack."
        )
    return PerformanceRunResult(
        operation=operation,
        replayed=replayed,
        artifacts=artifacts,
        context=reconstructed.context,
        probe_run=reconstructed.probe_run,
        decision=reconstructed.decision,
    )


@dataclass(frozen=True, slots=True)
class _ReconstructedDecision:
    context: ValidatedPerformanceContext
    probe_run: ProbeRun
    decision: AuthorizedPerformanceDecision


def _reconstruct_decision(
    artifacts: VerifiedPerformanceArtifacts,
    *,
    confirmation_idempotency_key: str,
) -> _ReconstructedDecision:
    contract = parse_contract(artifacts.contract_json)
    confirmation = parse_confirmation(
        artifacts.confirmation_json,
        idempotency_key=confirmation_idempotency_key,
    )
    context = validate_performance_context_bytes(
        contract,
        artifacts.workload_json,
        artifacts.prompt_fixture_jsonl,
    )
    require_frozen_confirmed(context, confirmation)
    receipt = parse_performance_receipt(artifacts.receipt_json)
    probe_run = parse_probe_run(
        artifacts.probe_manifest_json,
        artifacts.records_jsonl,
        expected_execution_id=receipt.execution_id,
        expected_records_sha256=receipt.records_sha256,
    )
    decision = authorize_performance_decision(
        context,
        confirmation,
        probe_run,
        receipt,
    )
    for payload in (
        artifacts.calculations_json,
        artifacts.verdicts_json,
    ):
        display = parse_performance_verdict_display(payload)
        recomputed = recompute_and_compare_performance_verdict(
            display,
            context.criterion,
            probe_run,
        )
        if recomputed != decision.performance_verdict:
            raise PerformanceRunnerError(
                "Persisted calculations do not match the authorized decision."
            )
    expected_report = render_performance_evidence_pack(
        decision,
        context,
        probe_run,
    )
    if artifacts.decision_packet_html != expected_report:
        raise PerformanceRunnerError(
            "Persisted Evidence Pack does not match deterministic rendering."
        )
    return _ReconstructedDecision(
        context=context,
        probe_run=probe_run,
        decision=decision,
    )


def _load_contract_source(path: Path):
    payload = _load_strict_json_object(
        path,
        label="Frozen performance contract",
        maximum_bytes=4 * 1024 * 1024,
    )
    try:
        return parse_contract(canonical_json_bytes(payload))
    except PerformanceSerializationError as error:
        raise PerformanceRunnerError(
            "Frozen performance contract is invalid."
        ) from error


def _load_confirmation_source(path: Path) -> ContractConfirmation:
    payload = _load_strict_json_object(
        path,
        label="Customer confirmation",
        maximum_bytes=_MAX_CONFIRMATION_BYTES,
    )
    try:
        confirmation = ContractConfirmation.model_validate_json(
            canonical_json_bytes(payload),
            strict=True,
        )
        # This also verifies that the ephemeral raw key derives the persisted
        # confirmation identity before any reservation or network access.
        serialize_confirmation(confirmation)
        return confirmation
    except (ValidationError, PerformanceSerializationError) as error:
        raise PerformanceRunnerError(
            "Customer confirmation is invalid."
        ) from error


def _load_strict_json_object(
    path: Path,
    *,
    label: str,
    maximum_bytes: int,
) -> dict[str, Any]:
    source = Path(path)
    try:
        if source.is_symlink() or not source.is_file():
            raise PerformanceRunnerError(
                "{0} must be a regular file.".format(label)
            )
        data = source.read_bytes()
    except OSError as error:
        raise PerformanceRunnerError(
            "{0} could not be read.".format(label)
        ) from error
    if not data or len(data) > maximum_bytes:
        raise PerformanceRunnerError(
            "{0} size is invalid.".format(label)
        )

    def unique_object(
        pairs: list[tuple[str, Any]],
    ) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise PerformanceRunnerError(
                    "{0} contains a duplicate field.".format(label)
                )
            result[key] = value
        return result

    try:
        payload = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=unique_object,
            parse_constant=lambda value: (_ for _ in ()).throw(
                PerformanceRunnerError(
                    "{0} contains a non-finite number.".format(label)
                )
            ),
        )
    except PerformanceRunnerError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PerformanceRunnerError(
            "{0} is not valid JSON.".format(label)
        ) from error
    if type(payload) is not dict:
        raise PerformanceRunnerError(
            "{0} must contain one JSON object.".format(label)
        )
    return payload


def _read_bound_workload(bundle_root: Path, relative_path: str) -> bytes:
    root = Path(bundle_root).resolve(strict=True)
    if not root.is_dir():
        raise PerformanceRunnerError("Bundle root must be a directory.")
    if (
        type(relative_path) is not str
        or not relative_path
        or "\\" in relative_path
    ):
        raise PerformanceRunnerError("Workload path is invalid.")
    logical = PurePosixPath(relative_path)
    if logical.is_absolute() or any(
        part in {"", ".", ".."} for part in logical.parts
    ):
        raise PerformanceRunnerError("Workload path must be safely relative.")
    try:
        path = (root / Path(*logical.parts)).resolve(strict=True)
        path.relative_to(root)
    except (OSError, ValueError) as error:
        raise PerformanceRunnerError(
            "Workload path escapes or is missing from the bundle."
        ) from error
    if path.is_symlink() or not path.is_file():
        raise PerformanceRunnerError("Workload must be a regular file.")
    data = path.read_bytes()
    if not data:
        raise PerformanceRunnerError("Workload file is empty.")
    return data


def _prepare_output_root(output_root: Path) -> Path:
    supplied = Path(output_root)
    if supplied.is_symlink():
        raise PerformanceRunnerError("Output root cannot be a symlink.")
    try:
        supplied.mkdir(parents=True, exist_ok=True, mode=0o700)
        resolved = supplied.resolve(strict=True)
    except OSError as error:
        raise PerformanceRunnerError(
            "Output root could not be prepared."
        ) from error
    if not resolved.is_dir():
        raise PerformanceRunnerError("Output root must be a directory.")
    return resolved


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


__all__ = [
    "PERFORMANCE_RUNNER_VERSION",
    "PerformanceRunResult",
    "PerformanceRunnerError",
    "run_performance_proof",
]
