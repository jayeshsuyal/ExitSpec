"""Server-owned execution binding for one frozen local performance POC.

This module joins the process-local agreement lifecycle to ExitSpec's existing
authoritative performance runner. Browser callers may authorize one exact run;
they cannot supply or override contract, workload, prompt, endpoint, model,
credential, output path, or request-count authority.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
import hashlib
import hmac
from pathlib import Path, PurePosixPath
from threading import RLock, Thread
from typing import Callable
import tempfile
import unicodedata
from urllib.parse import urlsplit
import uuid

from .canonical import canonical_json_bytes
from .confirmations import ContractConfirmation
from .models import POCContract, VerdictStatus
from .performance_artifacts import (
    VerifiedPerformanceArtifacts,
    read_and_verify_performance_artifacts,
)
from .performance_evidence import (
    ValidatedPerformanceContext,
    require_frozen_confirmed,
    validate_performance_context_bytes,
)
from .performance_operations import PerformanceOperation, PerformanceOperationStatus
from .performance_reporting import render_performance_evidence_pack
from .performance_runner import PerformanceRunResult, run_performance_proof
from .performance_serialization import serialize_contract
from .performance_verdicts import PerformanceOutcomeCounts
from .poc_performance_contract import (
    PerformanceEvidenceMethod,
    PreparedPerformanceBundle,
)
from .poc_performance_lifecycle import (
    PerformanceLifecycleError,
    ProcessLocalPerformanceLifecycleService,
)


MAX_IDEMPOTENCY_KEY_LENGTH = 200
DEFAULT_MAX_OPERATIONS = 1_024
_FIREWORKS_HOST = "api.fireworks.ai"
_FIREWORKS_ENDPOINT = (
    "https://api.fireworks.ai/inference/v1/chat/completions"
)
_FIREWORKS_PROVIDERS = frozenset({"fireworks", "fireworks ai"})
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


class POCPerformanceRunError(RuntimeError):
    """Base process-local execution error."""


class POCPerformanceRunInvalid(POCPerformanceRunError):
    pass


class POCPerformanceRunConflict(POCPerformanceRunError):
    pass


class POCPerformanceRunNotFound(POCPerformanceRunError, KeyError):
    pass


class POCPerformanceRunCapacityExceeded(POCPerformanceRunError):
    pass


class POCPerformanceRunStatus(StrEnum):
    NOT_STARTED = "NOT_STARTED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    BLOCKED = "BLOCKED"
    NOT_PROVEN = "NOT_PROVEN"


@dataclass(frozen=True, slots=True)
class POCPerformanceRunSnapshot:
    poc_id: str
    contract_id: str
    contract_version: str
    contract_hash: str
    workload_id: str
    target_provider: str
    endpoint_class: str
    endpoint: str
    model: str
    adapter: str
    adapter_version: str
    measured_requests: int
    concurrency: int
    warmup_requests: int
    authorized_request_count: int
    operation_id: str | None
    status: POCPerformanceRunStatus
    reason_code: str | None
    verdict: VerdictStatus | None
    attempted_count: int | None
    successful_count: int | None
    error_count: int | None
    outcome_counts: PerformanceOutcomeCounts | None
    p95_ttft_ms: str | None
    error_rate_percent: str | None
    evidence_pack_url: str | None
    terminal_operation: PerformanceOperation | None = None

    @property
    def is_terminal(self) -> bool:
        return self.status in {
            POCPerformanceRunStatus.COMPLETED,
            POCPerformanceRunStatus.BLOCKED,
            POCPerformanceRunStatus.NOT_PROVEN,
        }


@dataclass(frozen=True, slots=True)
class POCPerformanceStartSnapshot:
    operation: POCPerformanceRunSnapshot
    replayed: bool


@dataclass(slots=True)
class _RunRecord:
    poc_id: str
    execution_fingerprint: str
    operation_id: str
    status: POCPerformanceRunStatus
    bundle: PreparedPerformanceBundle = field(repr=False)
    confirmation: ContractConfirmation = field(repr=False)
    frozen_contract: POCContract = field(repr=False)
    reason_code: str | None = None
    verdict: VerdictStatus | None = None
    attempted_count: int | None = None
    successful_count: int | None = None
    error_count: int | None = None
    outcome_counts: PerformanceOutcomeCounts | None = None
    p95_ttft_ms: str | None = None
    error_rate_percent: str | None = None
    evidence_pack_url: str | None = None
    terminal_operation: PerformanceOperation | None = None


@dataclass(frozen=True, slots=True)
class _MaterializedBundle:
    root: Path
    contract_path: Path
    confirmation_path: Path
    context: ValidatedPerformanceContext


Runner = Callable[..., PerformanceRunResult]
WorkerLauncher = Callable[[Callable[[], None]], None]
ArtifactReader = Callable[[Path], VerifiedPerformanceArtifacts]


class ProcessLocalPOCPerformanceRunService:
    """Bounded single-flight coordinator over the authoritative runner."""

    def __init__(
        self,
        *,
        lifecycle: ProcessLocalPerformanceLifecycleService,
        output_root: Path,
        runner: Runner = run_performance_proof,
        artifact_reader: ArtifactReader = read_and_verify_performance_artifacts,
        worker_launcher: WorkerLauncher | None = None,
        operation_database_path: Path | None = None,
        fireworks_api_key: object = None,
        max_operations: int = DEFAULT_MAX_OPERATIONS,
    ) -> None:
        if type(lifecycle) is not ProcessLocalPerformanceLifecycleService:
            raise TypeError("lifecycle is invalid.")
        if not callable(runner) or not callable(artifact_reader):
            raise TypeError("run dependencies must be callable.")
        if worker_launcher is not None and not callable(worker_launcher):
            raise TypeError("worker_launcher must be callable.")
        root = _absolute_path(output_root, "output_root")
        database_path = (
            None
            if operation_database_path is None
            else _absolute_path(
                operation_database_path,
                "operation_database_path",
            )
        )
        if (
            type(max_operations) is not int
            or isinstance(max_operations, bool)
            or not 1 <= max_operations <= 10_000
        ):
            raise ValueError("max_operations is outside supported bounds.")
        self._lifecycle = lifecycle
        self._output_root = root
        self._operation_database_path = database_path
        self._runner = runner
        self._artifact_reader = artifact_reader
        self._worker_launcher = worker_launcher or _launch_worker
        self._fireworks_api_key = _optional_api_key(fireworks_api_key)
        self._max_operations = max_operations
        self._records: dict[str, _RunRecord] = {}
        self._latest_by_poc: dict[str, str] = {}
        self._idempotency_index: dict[str, str] = {}
        self._active_operation_id: str | None = None
        self._lock = RLock()

    def snapshot(self, poc_id: str) -> POCPerformanceRunSnapshot:
        bundle, confirmation, frozen = self._frozen_bundle(poc_id)
        with self._lock:
            operation_id = self._latest_by_poc.get(poc_id)
            if operation_id is None:
                return _empty_snapshot(
                    poc_id,
                    bundle,
                    frozen,
                )
            record = self._records[operation_id]
            current_fingerprint = _execution_fingerprint(
                poc_id,
                bundle,
                confirmation,
                frozen,
            )
            if not hmac.compare_digest(
                record.execution_fingerprint,
                current_fingerprint,
            ):
                return _empty_snapshot(poc_id, bundle, frozen)
            return self._snapshot_locked(record)

    def operation_snapshot(
        self,
        poc_id: str,
        operation_id: str,
    ) -> POCPerformanceRunSnapshot:
        if type(operation_id) is not str:
            raise POCPerformanceRunNotFound
        with self._lock:
            record = self._records.get(operation_id)
            if record is None or record.poc_id != poc_id:
                raise POCPerformanceRunNotFound
            return self._snapshot_locked(record)

    def completed_snapshots(
        self,
        poc_id: str,
    ) -> tuple[POCPerformanceRunSnapshot, ...]:
        """Return every completed Evidence Pack run without collapsing history."""

        if type(poc_id) is not str:
            raise POCPerformanceRunNotFound
        with self._lock:
            records = tuple(
                record
                for record in self._records.values()
                if record.poc_id == poc_id
                and record.status is POCPerformanceRunStatus.COMPLETED
            )
            return tuple(
                self._snapshot_locked(record)
                for record in records
            )

    def verified_evidence_pack_sha256(
        self,
        poc_id: str,
        operation_id: str,
    ) -> str:
        """Reverify one sealed pack against its runner-owned registry identity."""

        if type(operation_id) is not str:
            raise POCPerformanceRunNotFound
        with self._lock:
            record = self._records.get(operation_id)
            if record is None or record.poc_id != poc_id:
                raise POCPerformanceRunNotFound
            snapshot = self._snapshot_locked(record)
        operation = snapshot.terminal_operation
        if (
            snapshot.status is not POCPerformanceRunStatus.COMPLETED
            or snapshot.evidence_pack_url is None
            or type(operation) is not PerformanceOperation
            or operation.status is not PerformanceOperationStatus.COMPLETED
            or operation.artifact_registry_sha256 is None
            or snapshot.evidence_pack_url
            != "/artifacts/{0}/decision-packet.html".format(operation.run_id)
        ):
            raise POCPerformanceRunConflict
        try:
            verified = self._artifact_reader(
                self._output_root / operation.run_id
            )
        except Exception as error:
            raise POCPerformanceRunConflict from error
        if (
            type(verified) is not VerifiedPerformanceArtifacts
            or verified.run_id != operation.run_id
            or not hmac.compare_digest(
                operation.artifact_registry_sha256,
                hashlib.sha256(verified.registry_json).hexdigest(),
            )
        ):
            raise POCPerformanceRunConflict
        return hashlib.sha256(verified.decision_packet_html).hexdigest()

    def start(
        self,
        poc_id: str,
        *,
        execution_acknowledged: object,
        idempotency_key: object,
    ) -> POCPerformanceStartSnapshot:
        if execution_acknowledged is not True:
            raise POCPerformanceRunInvalid(
                "Explicit execution acknowledgement is required."
            )
        key_digest = _idempotency_digest(idempotency_key)
        bundle, confirmation, frozen = self._frozen_bundle(poc_id)
        _require_supported_execution_target(bundle)
        fingerprint = _execution_fingerprint(
            poc_id,
            bundle,
            confirmation,
            frozen,
        )
        with self._lock:
            existing_id = self._idempotency_index.get(key_digest)
            if existing_id is not None:
                existing = self._records[existing_id]
                if (
                    existing.poc_id != poc_id
                    or not hmac.compare_digest(
                        existing.execution_fingerprint,
                        fingerprint,
                    )
                ):
                    raise POCPerformanceRunConflict
                return POCPerformanceStartSnapshot(
                    self._snapshot_locked(existing),
                    True,
                )
            if self._active_operation_id is not None:
                raise POCPerformanceRunConflict
            if len(self._records) >= self._max_operations:
                raise POCPerformanceRunCapacityExceeded
            operation_id = "prun_{0}".format(uuid.uuid4().hex)
            record = _RunRecord(
                poc_id=poc_id,
                execution_fingerprint=fingerprint,
                operation_id=operation_id,
                status=POCPerformanceRunStatus.RUNNING,
                bundle=bundle,
                confirmation=confirmation,
                frozen_contract=frozen,
            )
            self._records[operation_id] = record
            self._latest_by_poc[poc_id] = operation_id
            self._idempotency_index[key_digest] = operation_id
            self._active_operation_id = operation_id
        try:
            self._worker_launcher(
                lambda: self._execute(
                    record,
                    bundle,
                    confirmation,
                    frozen,
                    str(idempotency_key),
                )
            )
        except Exception:
            with self._lock:
                record.status = POCPerformanceRunStatus.NOT_PROVEN
                record.reason_code = "WORKER_LAUNCH_FAILED"
                self._active_operation_id = None
            raise POCPerformanceRunError from None
        with self._lock:
            return POCPerformanceStartSnapshot(
                self._snapshot_locked(record),
                False,
            )

    def _frozen_bundle(
        self,
        poc_id: str,
    ) -> tuple[
        PreparedPerformanceBundle,
        ContractConfirmation,
        POCContract,
    ]:
        if type(poc_id) is not str:
            raise POCPerformanceRunInvalid
        try:
            bundle, confirmation, frozen = self._lifecycle.frozen_bundle(
                poc_id
            )
            preparation = self._lifecycle.snapshot(
                poc_id,
                allow_empty=False,
            ).preparation
        except PerformanceLifecycleError as error:
            raise POCPerformanceRunConflict from error
        if (
            preparation is None
            or preparation.target.evidence_method
            is not PerformanceEvidenceMethod.EXIT_SPEC_STREAMING_PROBE
        ):
            raise POCPerformanceRunConflict
        return bundle, confirmation, frozen

    def _execute(
        self,
        record: _RunRecord,
        bundle: PreparedPerformanceBundle,
        confirmation: ContractConfirmation,
        frozen: POCContract,
        idempotency_key: str,
    ) -> None:
        try:
            result = self._call_runner(
                bundle,
                confirmation,
                frozen,
                idempotency_key,
            )
            terminal = self._verified_terminal(
                result,
                bundle,
                confirmation,
                frozen,
            )
        except Exception:
            terminal = {
                "status": POCPerformanceRunStatus.NOT_PROVEN,
                "reason_code": "RUNNER_INTERNAL_FAILURE",
            }
        with self._lock:
            record.status = terminal["status"]
            record.reason_code = terminal.get("reason_code")
            record.verdict = terminal.get("verdict")
            record.attempted_count = terminal.get("attempted_count")
            record.successful_count = terminal.get("successful_count")
            record.error_count = terminal.get("error_count")
            record.outcome_counts = terminal.get("outcome_counts")
            record.p95_ttft_ms = terminal.get("p95_ttft_ms")
            record.error_rate_percent = terminal.get(
                "error_rate_percent"
            )
            record.evidence_pack_url = terminal.get("evidence_pack_url")
            record.terminal_operation = terminal.get("terminal_operation")
            if self._active_operation_id == record.operation_id:
                self._active_operation_id = None

    def _call_runner(
        self,
        bundle: PreparedPerformanceBundle,
        confirmation: ContractConfirmation,
        frozen: POCContract,
        idempotency_key: str,
    ) -> PerformanceRunResult:
        with tempfile.TemporaryDirectory(
            prefix="exitspec-dynamic-performance-"
        ) as temporary:
            materialized = _materialize_bundle(
                Path(temporary),
                bundle,
                confirmation,
                frozen,
            )
            api_key, credential_endpoint = _credentials_for(
                bundle,
                self._fireworks_api_key,
            )
            result = self._runner(
                contract_path=materialized.contract_path,
                confirmation_path=materialized.confirmation_path,
                bundle_root=materialized.root,
                output_root=self._output_root,
                idempotency_key=idempotency_key,
                api_key=api_key,
                credential_endpoint=credential_endpoint,
                authorized_request_count=(
                    1
                    + bundle.workload.warmup_count
                    + bundle.workload.request_count
                ),
                operation_database_path=self._operation_database_path,
            )
        if type(result) is not PerformanceRunResult:
            raise TypeError
        return result

    def _verified_terminal(
        self,
        result: PerformanceRunResult,
        bundle: PreparedPerformanceBundle,
        confirmation: ContractConfirmation,
        frozen: POCContract,
    ) -> dict:
        operation = result.operation
        if operation.status is PerformanceOperationStatus.BLOCKED:
            return {
                "status": POCPerformanceRunStatus.BLOCKED,
                "reason_code": (
                    operation.terminal_reason
                    or "ENDPOINT_PREFLIGHT_FAILED"
                ),
                "terminal_operation": operation,
            }
        if operation.status is not PerformanceOperationStatus.COMPLETED:
            return {
                "status": POCPerformanceRunStatus.NOT_PROVEN,
                "reason_code": (
                    operation.terminal_reason or "RUN_NOT_PROVEN"
                ),
                "terminal_operation": operation,
            }
        context = validate_performance_context_bytes(
            frozen,
            bundle.workload_bytes,
            bundle.prompt_bytes,
        )
        require_frozen_confirmed(context, confirmation)
        artifacts = result.artifacts
        decision = result.decision
        probe_run = result.probe_run
        if (
            type(artifacts) is not VerifiedPerformanceArtifacts
            or result.context != context
            or decision is None
            or probe_run is None
            or artifacts.run_id != operation.run_id
            or operation.artifact_registry_sha256 is None
            or not hmac.compare_digest(
                operation.artifact_registry_sha256,
                hashlib.sha256(artifacts.registry_json).hexdigest(),
            )
        ):
            raise TypeError
        verified = self._artifact_reader(
            self._output_root / operation.run_id
        )
        if (
            type(verified) is not VerifiedPerformanceArtifacts
            or verified != artifacts
            or not hmac.compare_digest(
                verified.decision_packet_html,
                render_performance_evidence_pack(
                    decision,
                    context,
                    probe_run,
                ),
            )
        ):
            raise TypeError
        verdict = decision.performance_verdict
        return {
            "status": POCPerformanceRunStatus.COMPLETED,
            "reason_code": None,
            "verdict": verdict.verdict,
            "attempted_count": verdict.attempted_count,
            "successful_count": verdict.successful_count,
            "error_count": verdict.error_count,
            "outcome_counts": verdict.outcome_counts,
            "p95_ttft_ms": _milliseconds(verdict.ttft_p95.observed_ns),
            "error_rate_percent": _percent(
                verdict.error_rate.observed_rate
            ),
            "evidence_pack_url": (
                "/artifacts/{0}/decision-packet.html".format(
                    operation.run_id
                )
            ),
            "terminal_operation": operation,
        }

    def _snapshot_locked(
        self,
        record: _RunRecord,
    ) -> POCPerformanceRunSnapshot:
        bundle = record.bundle
        confirmation = record.confirmation
        frozen = record.frozen_contract
        if not hmac.compare_digest(
            record.execution_fingerprint,
            _execution_fingerprint(
                record.poc_id,
                bundle,
                confirmation,
                frozen,
            ),
        ):
            raise POCPerformanceRunConflict
        return POCPerformanceRunSnapshot(
            poc_id=record.poc_id,
            contract_id=frozen.id,
            contract_version=frozen.version,
            contract_hash=_contract_hash(frozen),
            workload_id=bundle.workload.workload_id,
            target_provider=(
                frozen.target_system.provider
            ),
            endpoint_class=(
                frozen.target_system.endpoint_class
            ),
            endpoint=bundle.workload.endpoint,
            model=bundle.workload.model,
            adapter=bundle.workload.adapter,
            adapter_version=bundle.workload.adapter_version,
            measured_requests=bundle.workload.request_count,
            concurrency=bundle.workload.concurrency,
            warmup_requests=bundle.workload.warmup_count,
            authorized_request_count=(
                1
                + bundle.workload.warmup_count
                + bundle.workload.request_count
            ),
            operation_id=record.operation_id,
            status=record.status,
            reason_code=record.reason_code,
            verdict=record.verdict,
            attempted_count=record.attempted_count,
            successful_count=record.successful_count,
            error_count=record.error_count,
            outcome_counts=record.outcome_counts,
            p95_ttft_ms=record.p95_ttft_ms,
            error_rate_percent=record.error_rate_percent,
            evidence_pack_url=record.evidence_pack_url,
            terminal_operation=record.terminal_operation,
        )


def _launch_worker(target: Callable[[], None]) -> None:
    Thread(target=target, daemon=True).start()


def _absolute_path(value: object, label: str) -> Path:
    if not isinstance(value, Path) or not value.is_absolute():
        raise ValueError("{0} must be an absolute path.".format(label))
    return value


def _optional_api_key(value: object) -> str | None:
    if value is None:
        return None
    if (
        type(value) is not str
        or not value
        or value != value.strip()
        or len(value) > 4_096
        or any(character.isspace() for character in value)
    ):
        return None
    return value


def _safe_text(value: object) -> str:
    if type(value) is not str:
        raise POCPerformanceRunInvalid
    normalized = unicodedata.normalize("NFC", value)
    if (
        not normalized
        or normalized != normalized.strip()
        or len(normalized) > MAX_IDEMPOTENCY_KEY_LENGTH
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in normalized)
    ):
        raise POCPerformanceRunInvalid
    return normalized


def _idempotency_digest(value: object) -> str:
    return hashlib.sha256(
        b"exitspec-dynamic-performance-idempotency-v1\x00"
        + _safe_text(value).encode("utf-8")
    ).hexdigest()


def _contract_hash(contract: POCContract) -> str:
    if type(contract.canonical_hash) is not str:
        raise POCPerformanceRunConflict
    return contract.canonical_hash


def _execution_fingerprint(
    poc_id: str,
    bundle: PreparedPerformanceBundle,
    confirmation: ContractConfirmation,
    frozen: POCContract,
) -> str:
    return hashlib.sha256(
        b"exitspec-dynamic-performance-execution-v1\x00"
        + canonical_json_bytes(
            {
                "poc_id": poc_id,
                "contract_hash": _contract_hash(frozen),
                "confirmation_id": confirmation.confirmation_id,
                "workload_sha256": hashlib.sha256(
                    bundle.workload_bytes
                ).hexdigest(),
                "prompt_sha256": hashlib.sha256(
                    bundle.prompt_bytes
                ).hexdigest(),
            }
        )
    ).hexdigest()


def _safe_relative_path(value: str) -> Path:
    logical = PurePosixPath(value)
    if (
        logical.is_absolute()
        or "\\" in value
        or any(part in {"", ".", ".."} for part in logical.parts)
    ):
        raise ValueError
    return Path(*logical.parts)


def _materialize_bundle(
    temporary: Path,
    bundle: PreparedPerformanceBundle,
    confirmation: ContractConfirmation,
    frozen: POCContract,
) -> _MaterializedBundle:
    root = temporary.resolve(strict=True)
    context = validate_performance_context_bytes(
        frozen,
        bundle.workload_bytes,
        bundle.prompt_bytes,
    )
    require_frozen_confirmed(context, confirmation)
    contract_path = root / "contract.json"
    confirmation_path = root / "confirmation.json"
    workload_path = root / _safe_relative_path(
        frozen.workload.fixture_path
    )
    prompt_path = root / _safe_relative_path(
        bundle.workload.prompt_fixture_path
    )
    for target, payload in (
        (contract_path, serialize_contract(frozen)),
        (
            confirmation_path,
            canonical_json_bytes(
                confirmation.model_dump(mode="json")
            ),
        ),
        (workload_path, bundle.workload_bytes),
        (prompt_path, bundle.prompt_bytes),
    ):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
    return _MaterializedBundle(
        root,
        contract_path,
        confirmation_path,
        context,
    )


def _credentials_for(
    bundle: PreparedPerformanceBundle,
    fireworks_api_key: str | None,
) -> tuple[str | None, str | None]:
    endpoint = bundle.workload.endpoint
    parsed = urlsplit(endpoint)
    provider = bundle.approved_contract.target_system.provider.casefold()
    if (
        fireworks_api_key is not None
        and provider in _FIREWORKS_PROVIDERS
        and parsed.scheme == "https"
        and parsed.hostname == _FIREWORKS_HOST
        and endpoint == _FIREWORKS_ENDPOINT
    ):
        return fireworks_api_key, endpoint
    return None, None


def _require_supported_execution_target(
    bundle: PreparedPerformanceBundle,
) -> None:
    endpoint = bundle.workload.endpoint
    parsed = urlsplit(endpoint)
    provider = bundle.approved_contract.target_system.provider.casefold()
    if parsed.hostname in _LOOPBACK_HOSTS:
        return
    if (
        endpoint == _FIREWORKS_ENDPOINT
        and provider in _FIREWORKS_PROVIDERS
    ):
        return
    raise POCPerformanceRunInvalid(
        "The frozen target is outside the execution allowlist."
    )


def _decimal_text(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _milliseconds(observed_ns: int | None) -> str | None:
    if observed_ns is None:
        return None
    return _decimal_text(Decimal(observed_ns) / Decimal(1_000_000))


def _percent(observed_rate: Decimal | None) -> str | None:
    if observed_rate is None:
        return None
    return _decimal_text(observed_rate * Decimal(100))


def _empty_snapshot(
    poc_id: str,
    bundle: PreparedPerformanceBundle,
    frozen: POCContract,
) -> POCPerformanceRunSnapshot:
    return POCPerformanceRunSnapshot(
        poc_id=poc_id,
        contract_id=frozen.id,
        contract_version=frozen.version,
        contract_hash=_contract_hash(frozen),
        workload_id=bundle.workload.workload_id,
        target_provider=frozen.target_system.provider,
        endpoint_class=frozen.target_system.endpoint_class,
        endpoint=bundle.workload.endpoint,
        model=bundle.workload.model,
        adapter=bundle.workload.adapter,
        adapter_version=bundle.workload.adapter_version,
        measured_requests=bundle.workload.request_count,
        concurrency=bundle.workload.concurrency,
        warmup_requests=bundle.workload.warmup_count,
        authorized_request_count=(
            1
            + bundle.workload.warmup_count
            + bundle.workload.request_count
        ),
        operation_id=None,
        status=POCPerformanceRunStatus.NOT_STARTED,
        reason_code=None,
        verdict=None,
        attempted_count=None,
        successful_count=None,
        error_count=None,
        outcome_counts=None,
        p95_ttft_ms=None,
        error_rate_percent=None,
        evidence_pack_url=None,
    )


__all__ = [
    "POCPerformanceRunCapacityExceeded",
    "POCPerformanceRunConflict",
    "POCPerformanceRunError",
    "POCPerformanceRunInvalid",
    "POCPerformanceRunNotFound",
    "POCPerformanceRunSnapshot",
    "POCPerformanceRunStatus",
    "POCPerformanceStartSnapshot",
    "ProcessLocalPOCPerformanceRunService",
]
