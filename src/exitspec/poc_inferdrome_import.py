"""POC-bound orchestration for untrusted Inferdrome evidence imports."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import shutil
import tempfile
import unicodedata
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from pathlib import Path, PurePosixPath
from threading import RLock, Thread
from typing import Callable

from .canonical import canonical_json_bytes
from .confirmations import ContractConfirmation
from .inferdrome_bundle import InferdromeBundleRejected
from .inferdrome_catalog import (
    InferdromeBundleCatalog,
    InferdromeCatalogNotFound,
    ResolvedInferdromeBundle,
)
from .inferdrome_import import (
    InferdromeImportRejected,
    InferdromeImportResult,
    import_inferdrome_bundle,
)
from .inferdrome_managed_import import (
    InferdromeManagedImportRejected,
    InferdromeManagedImportResult,
    import_managed_inferdrome_bundle,
)
from .inferdrome_managed_reporting import (
    render_managed_inferdrome_evidence_pack,
)
from .inferdrome_reporting import render_inferdrome_evidence_pack
from .models import InferencePerformanceCriterionV3, POCContract, VerdictStatus
from .performance_evidence import validate_performance_context_bytes
from .performance_serialization import serialize_contract
from .poc_performance_contract import (
    PerformanceEvidenceMethod,
    PreparedPerformanceBundle,
)
from .poc_managed_inferdrome_contract import PreparedManagedInferdromeBundle
from .poc_performance_lifecycle import (
    PerformanceLifecycleError,
    ProcessLocalPerformanceLifecycleService,
)

MAX_IDEMPOTENCY_KEY_LENGTH = 200
DEFAULT_MAX_IMPORTS = 1_024
_OPERATION_ID_PREFIX = "pimp_"
_PACK_SCHEMA_VERSION = "exitspec.inferdrome-artifacts.v1"


class POCInferdromeImportError(RuntimeError):
    pass


class POCInferdromeImportInvalid(POCInferdromeImportError):
    pass


class POCInferdromeImportConflict(POCInferdromeImportError):
    pass


class POCInferdromeImportNotFound(POCInferdromeImportError, KeyError):
    pass


class POCInferdromeImportCapacityExceeded(POCInferdromeImportError):
    pass


class POCInferdromeImportStatus(StrEnum):
    NOT_STARTED = "NOT_STARTED"
    IMPORTING = "IMPORTING"
    COMPLETED = "COMPLETED"
    INGESTION_REJECTED = "INGESTION_REJECTED"
    FAILED_CLOSED = "FAILED_CLOSED"


@dataclass(frozen=True, slots=True)
class POCInferdromeImportSnapshot:
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
    observed_configured_max_concurrency: int | None
    warmup_requests: int
    operation_id: str | None
    status: POCInferdromeImportStatus
    rejection_code: str | None
    verdict: VerdictStatus | None
    attempted_count: int | None
    successful_count: int | None
    error_count: int | None
    anomalous_count: int | None
    p95_ttft_ms: str | None
    error_rate_percent: str | None
    selected_run_id: str | None
    producer_run_id: str | None
    bundle_digest: str | None
    receipt_id: str | None
    applicability_codes: tuple[str, ...]
    evidence_pack_url: str | None
    completed_at: datetime | None

    @property
    def is_terminal(self) -> bool:
        return self.status in {
            POCInferdromeImportStatus.COMPLETED,
            POCInferdromeImportStatus.INGESTION_REJECTED,
            POCInferdromeImportStatus.FAILED_CLOSED,
        }


@dataclass(frozen=True, slots=True)
class POCInferdromeImportStartSnapshot:
    operation: POCInferdromeImportSnapshot
    replayed: bool


@dataclass(slots=True)
class _ImportRecord:
    poc_id: str
    operation_id: str
    import_fingerprint: str
    selected_run_id: str
    selected_bundle_digest: str
    status: POCInferdromeImportStatus
    bundle: PreparedPerformanceBundle | PreparedManagedInferdromeBundle = field(
        repr=False
    )
    confirmation: ContractConfirmation = field(repr=False)
    frozen_contract: POCContract = field(repr=False)
    rejection_code: str | None = None
    result: InferdromeImportResult | InferdromeManagedImportResult | None = field(
        default=None,
        repr=False,
    )
    evidence_pack_url: str | None = None
    artifact_manifest_sha256: str | None = None
    completed_at: datetime | None = None


WorkerLauncher = Callable[[Callable[[], None]], None]
Clock = Callable[[], datetime]


class ProcessLocalPOCInferdromeImportService:
    """Single-flight coordinator over catalog resolution and independent import."""

    def __init__(
        self,
        *,
        lifecycle: ProcessLocalPerformanceLifecycleService,
        catalog: InferdromeBundleCatalog,
        output_root: Path,
        worker_launcher: WorkerLauncher | None = None,
        clock: Clock | None = None,
        max_imports: int = DEFAULT_MAX_IMPORTS,
    ) -> None:
        if type(lifecycle) is not ProcessLocalPerformanceLifecycleService:
            raise TypeError("lifecycle is invalid.")
        if type(catalog) is not InferdromeBundleCatalog:
            raise TypeError("catalog is invalid.")
        if not isinstance(output_root, Path) or not output_root.is_absolute():
            raise ValueError("output_root must be an absolute path.")
        if worker_launcher is not None and not callable(worker_launcher):
            raise TypeError("worker_launcher must be callable.")
        if clock is not None and not callable(clock):
            raise TypeError("clock must be callable.")
        if (
            type(max_imports) is not int
            or isinstance(max_imports, bool)
            or not 1 <= max_imports <= 10_000
        ):
            raise ValueError("max_imports is outside supported bounds.")
        output_root.mkdir(parents=True, exist_ok=True)
        self._lifecycle = lifecycle
        self._catalog = catalog
        self._output_root = output_root
        self._worker_launcher = worker_launcher or _launch_worker
        self._clock = clock or (lambda: datetime.now(UTC))
        self._max_imports = max_imports
        self._records: dict[str, _ImportRecord] = {}
        self._latest_by_poc: dict[str, str] = {}
        self._idempotency_index: dict[str, str] = {}
        self._active_pocs: set[str] = set()
        self._lock = RLock()

    @property
    def catalog(self) -> InferdromeBundleCatalog:
        return self._catalog

    def snapshot(self, poc_id: str) -> POCInferdromeImportSnapshot:
        bundle, _, frozen = self._frozen_external_bundle(poc_id)
        with self._lock:
            operation_id = self._latest_by_poc.get(poc_id)
            if operation_id is None:
                return _empty_snapshot(poc_id, bundle, frozen)
            record = self._records[operation_id]
            if not hmac.compare_digest(
                record.import_fingerprint,
                _import_fingerprint(
                    poc_id,
                    bundle,
                    record.confirmation,
                    frozen,
                    record.selected_run_id,
                    record.selected_bundle_digest,
                ),
            ):
                return _empty_snapshot(poc_id, bundle, frozen)
            return self._snapshot_locked(record)

    def operation_snapshot(
        self,
        poc_id: str,
        operation_id: str,
    ) -> POCInferdromeImportSnapshot:
        with self._lock:
            record = self._records.get(operation_id)
            if record is None or record.poc_id != poc_id:
                raise POCInferdromeImportNotFound
            return self._snapshot_locked(record)

    def completed_snapshots(
        self,
        poc_id: str,
    ) -> tuple[POCInferdromeImportSnapshot, ...]:
        with self._lock:
            return tuple(
                self._snapshot_locked(record)
                for record in self._records.values()
                if record.poc_id == poc_id
                and record.status is POCInferdromeImportStatus.COMPLETED
            )

    def start(
        self,
        poc_id: str,
        *,
        import_acknowledged: object,
        run_id: object,
        bundle_digest: object,
        idempotency_key: object,
    ) -> POCInferdromeImportStartSnapshot:
        if import_acknowledged is not True:
            raise POCInferdromeImportInvalid
        selected_run_id = _safe_single_line(run_id, 128)
        selected_digest = _safe_single_line(bundle_digest, 80)
        key_digest = _idempotency_digest(idempotency_key)
        bundle, confirmation, frozen = self._frozen_external_bundle(poc_id)
        if type(bundle) is PreparedManagedInferdromeBundle and (
            selected_run_id != bundle.evidence.run_id
            or selected_digest != bundle.evidence.bundle_digest
        ):
            raise POCInferdromeImportConflict
        fingerprint = _import_fingerprint(
            poc_id,
            bundle,
            confirmation,
            frozen,
            selected_run_id,
            selected_digest,
        )
        with self._lock:
            existing_id = self._idempotency_index.get(key_digest)
            if existing_id is not None:
                existing = self._records[existing_id]
                if (
                    existing.poc_id != poc_id
                    or not hmac.compare_digest(
                        existing.import_fingerprint,
                        fingerprint,
                    )
                ):
                    raise POCInferdromeImportConflict
                return POCInferdromeImportStartSnapshot(
                    self._snapshot_locked(existing),
                    True,
                )
        try:
            resolved = self._catalog.resolve(selected_run_id, selected_digest)
        except InferdromeCatalogNotFound as error:
            raise POCInferdromeImportNotFound from error
        with self._lock:
            if poc_id in self._active_pocs:
                raise POCInferdromeImportConflict
            if len(self._records) >= self._max_imports:
                raise POCInferdromeImportCapacityExceeded
            operation_id = _OPERATION_ID_PREFIX + uuid.uuid4().hex
            record = _ImportRecord(
                poc_id=poc_id,
                operation_id=operation_id,
                import_fingerprint=fingerprint,
                selected_run_id=selected_run_id,
                selected_bundle_digest=selected_digest,
                status=POCInferdromeImportStatus.IMPORTING,
                bundle=bundle,
                confirmation=confirmation,
                frozen_contract=frozen,
            )
            self._records[operation_id] = record
            self._latest_by_poc[poc_id] = operation_id
            self._idempotency_index[key_digest] = operation_id
            self._active_pocs.add(poc_id)
        try:
            self._worker_launcher(lambda: self._import(record, resolved))
        except Exception:
            with self._lock:
                record.status = POCInferdromeImportStatus.FAILED_CLOSED
                record.rejection_code = "WORKER_LAUNCH_FAILED"
                record.completed_at = _utc_time(self._clock())
                self._active_pocs.discard(poc_id)
            raise POCInferdromeImportError from None
        with self._lock:
            return POCInferdromeImportStartSnapshot(
                self._snapshot_locked(record),
                False,
            )

    def verified_evidence_pack_sha256(
        self,
        poc_id: str,
        operation_id: str,
    ) -> str:
        with self._lock:
            record = self._records.get(operation_id)
            if record is None or record.poc_id != poc_id:
                raise POCInferdromeImportNotFound
            snapshot = self._snapshot_locked(record)
            expected_manifest_sha256 = record.artifact_manifest_sha256
        if (
            snapshot.status is not POCInferdromeImportStatus.COMPLETED
            or snapshot.evidence_pack_url is None
            or expected_manifest_sha256 is None
            or snapshot.evidence_pack_url
            != f"/artifacts/{operation_id}/decision-packet.html"
        ):
            raise POCInferdromeImportConflict
        run_dir = self._output_root / operation_id
        try:
            manifest_bytes = (run_dir / "artifact-hashes.json").read_bytes()
            if not hmac.compare_digest(
                expected_manifest_sha256,
                hashlib.sha256(manifest_bytes).hexdigest(),
            ):
                raise ValueError
            manifest = json.loads(manifest_bytes)
            expected_files = {
                "contract.json",
                "inferdrome-receipt.json",
                "recalculation.json",
                "verdict.json",
                "decision-packet.html",
            }
            if (
                type(manifest) is not dict
                or set(manifest) != {"schema_version", "operation_id", "artifacts"}
                or manifest["schema_version"] != _PACK_SCHEMA_VERSION
                or manifest["operation_id"] != operation_id
                or type(manifest["artifacts"]) is not dict
                or set(manifest["artifacts"]) != expected_files
            ):
                raise ValueError
            for relative, expected_sha256 in manifest["artifacts"].items():
                target = _safe_pack_child(run_dir, relative)
                if (
                    target is None
                    or type(expected_sha256) is not str
                    or len(expected_sha256) != 64
                    or hashlib.sha256(target.read_bytes()).hexdigest()
                    != expected_sha256
                ):
                    raise ValueError
            return hashlib.sha256(
                (run_dir / "decision-packet.html").read_bytes()
            ).hexdigest()
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise POCInferdromeImportConflict from error

    def _frozen_external_bundle(
        self,
        poc_id: str,
    ) -> tuple[
        PreparedPerformanceBundle | PreparedManagedInferdromeBundle,
        ContractConfirmation,
        POCContract,
    ]:
        if type(poc_id) is not str:
            raise POCInferdromeImportInvalid
        try:
            bundle, confirmation, frozen = self._lifecycle.frozen_bundle(poc_id)
        except PerformanceLifecycleError as error:
            raise POCInferdromeImportConflict from error
        preparation = self._lifecycle.snapshot(poc_id, allow_empty=False).preparation
        if (
            preparation is None
            or preparation.target.evidence_method
            is not PerformanceEvidenceMethod.INFERDROME_EXTERNAL_BUNDLE
            or type(bundle)
            not in {PreparedPerformanceBundle, PreparedManagedInferdromeBundle}
        ):
            raise POCInferdromeImportConflict
        return bundle, confirmation, frozen

    def _import(
        self,
        record: _ImportRecord,
        resolved: ResolvedInferdromeBundle,
    ) -> None:
        completed_at = _utc_time(self._clock())
        try:
            if type(record.bundle) is PreparedManagedInferdromeBundle:
                result: InferdromeImportResult | InferdromeManagedImportResult = (
                    import_managed_inferdrome_bundle(
                        resolved.path,
                        record.frozen_contract,
                        record.confirmation,
                        expected_bundle_digest=record.selected_bundle_digest,
                        received_at=completed_at,
                    )
                )
            elif type(record.bundle) is PreparedPerformanceBundle:
                context = validate_performance_context_bytes(
                    record.frozen_contract,
                    record.bundle.workload_bytes,
                    record.bundle.prompt_bytes,
                )
                result = import_inferdrome_bundle(
                    resolved.path,
                    context,
                    record.confirmation,
                    expected_bundle_digest=record.selected_bundle_digest,
                    received_at=completed_at,
                )
            else:
                raise POCInferdromeImportConflict
            manifest_sha256 = _write_evidence_pack(
                output_root=self._output_root,
                operation_id=record.operation_id,
                contract=record.frozen_contract,
                result=result,
            )
        except InferdromeBundleRejected as error:
            self._finish_rejected(record, error.code.value, completed_at)
            return
        except InferdromeImportRejected as error:
            self._finish_rejected(record, error.code.value, completed_at)
            return
        except InferdromeManagedImportRejected as error:
            self._finish_rejected(record, error.code.value, completed_at)
            return
        except Exception:
            with self._lock:
                record.status = POCInferdromeImportStatus.FAILED_CLOSED
                record.rejection_code = "IMPORT_INTERNAL_FAILURE"
                record.completed_at = completed_at
                self._active_pocs.discard(record.poc_id)
            return
        with self._lock:
            record.status = POCInferdromeImportStatus.COMPLETED
            record.result = result
            record.evidence_pack_url = (
                f"/artifacts/{record.operation_id}/decision-packet.html"
            )
            record.artifact_manifest_sha256 = manifest_sha256
            record.completed_at = completed_at
            self._active_pocs.discard(record.poc_id)

    def _finish_rejected(
        self,
        record: _ImportRecord,
        code: str,
        completed_at: datetime,
    ) -> None:
        with self._lock:
            record.status = POCInferdromeImportStatus.INGESTION_REJECTED
            record.rejection_code = code
            record.completed_at = completed_at
            self._active_pocs.discard(record.poc_id)

    def _snapshot_locked(
        self,
        record: _ImportRecord,
    ) -> POCInferdromeImportSnapshot:
        bundle = record.bundle
        frozen = record.frozen_contract
        result = record.result
        recalculated = None if result is None else result.recalculated
        verdict = (
            None
            if result is None
            else result.verdict
            if type(result) is InferdromeManagedImportResult
            else result.performance_verdict.verdict
        )
        return POCInferdromeImportSnapshot(
            poc_id=record.poc_id,
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
            concurrency=_required_concurrency(bundle),
            observed_configured_max_concurrency=(
                bundle.workload.concurrency
                if type(bundle) is PreparedManagedInferdromeBundle
                else None
            ),
            warmup_requests=bundle.workload.warmup_count,
            operation_id=record.operation_id,
            status=record.status,
            rejection_code=record.rejection_code,
            verdict=verdict,
            attempted_count=(
                None if recalculated is None else recalculated.attempted_count
            ),
            successful_count=(
                None if recalculated is None else recalculated.successful_count
            ),
            error_count=(
                None if recalculated is None else recalculated.failed_count
            ),
            anomalous_count=(
                recalculated.anomalous_count
                if recalculated is not None
                and type(result) is InferdromeManagedImportResult
                else None
            ),
            p95_ttft_ms=(
                None
                if recalculated is None or recalculated.p95_ttft_ns is None
                else _decimal_text(
                    Decimal(recalculated.p95_ttft_ns) / Decimal(1_000_000)
                )
            ),
            error_rate_percent=(
                None
                if recalculated is None
                else _decimal_text(recalculated.error_rate * Decimal(100))
            ),
            selected_run_id=record.selected_run_id,
            producer_run_id=None if result is None else result.run_id,
            bundle_digest=(
                record.selected_bundle_digest
                if result is None
                else result.receipt.bundle_digest
            ),
            receipt_id=None if result is None else result.receipt.receipt_id,
            applicability_codes=(
                ()
                if result is None
                else tuple(issue.value for issue in result.applicability.issues)
            ),
            evidence_pack_url=record.evidence_pack_url,
            completed_at=record.completed_at,
        )


def _required_concurrency(
    bundle: PreparedPerformanceBundle | PreparedManagedInferdromeBundle,
) -> int:
    if type(bundle) is PreparedPerformanceBundle:
        return bundle.workload.concurrency
    if type(bundle) is PreparedManagedInferdromeBundle:
        criteria = tuple(
            criterion
            for criterion in bundle.approved_contract.criteria
            if type(criterion) is InferencePerformanceCriterionV3
        )
        if len(criteria) == 1 and len(bundle.approved_contract.criteria) == 1:
            return criteria[0].evidence_identity.configured_max_concurrency
    raise POCInferdromeImportConflict


def _empty_snapshot(
    poc_id: str,
    bundle: PreparedPerformanceBundle | PreparedManagedInferdromeBundle,
    frozen: POCContract,
) -> POCInferdromeImportSnapshot:
    return POCInferdromeImportSnapshot(
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
        concurrency=_required_concurrency(bundle),
        observed_configured_max_concurrency=(
            bundle.workload.concurrency
            if type(bundle) is PreparedManagedInferdromeBundle
            else None
        ),
        warmup_requests=bundle.workload.warmup_count,
        operation_id=None,
        status=POCInferdromeImportStatus.NOT_STARTED,
        rejection_code=None,
        verdict=None,
        attempted_count=None,
        successful_count=None,
        error_count=None,
        anomalous_count=None,
        p95_ttft_ms=None,
        error_rate_percent=None,
        selected_run_id=None,
        producer_run_id=None,
        bundle_digest=None,
        receipt_id=None,
        applicability_codes=(),
        evidence_pack_url=None,
        completed_at=None,
    )


def _write_evidence_pack(
    *,
    output_root: Path,
    operation_id: str,
    contract: POCContract,
    result: InferdromeImportResult | InferdromeManagedImportResult,
) -> str:
    recalculated = result.recalculated
    if type(result) is InferdromeManagedImportResult:
        verdict_payload = _managed_verdict_payload(contract, result)
        decision_packet = render_managed_inferdrome_evidence_pack(
            contract=contract,
            result=result,
        )
    else:
        verdict_payload = _verdict_payload(result.performance_verdict)
        decision_packet = render_inferdrome_evidence_pack(
            contract=contract,
            result=result,
        )
    artifacts = {
        "contract.json": serialize_contract(contract),
        "inferdrome-receipt.json": canonical_json_bytes(
            result.receipt.model_dump(mode="json")
        ),
        "recalculation.json": canonical_json_bytes(
            {
                "attempted_count": recalculated.attempted_count,
                "successful_count": recalculated.successful_count,
                "failed_count": recalculated.failed_count,
                "anomalous_count": recalculated.anomalous_count,
                "error_rate": str(recalculated.error_rate),
                "p95_ttft_ns": recalculated.p95_ttft_ns,
                "ttft_definition": recalculated.ttft_definition,
                "records_sha256": recalculated.records_sha256,
                "recalculation_sha256": recalculated.recalculation_sha256,
            }
        ),
        "verdict.json": canonical_json_bytes(
            verdict_payload
        ),
        "decision-packet.html": decision_packet,
    }
    staging = Path(
        tempfile.mkdtemp(
            prefix=f".{operation_id}.",
            dir=output_root,
        )
    )
    destination = output_root / operation_id
    try:
        os.chmod(staging, 0o700)
        for relative, content in artifacts.items():
            target = staging / relative
            with target.open("xb") as handle:
                handle.write(content)
            target.chmod(0o600)
        manifest_bytes = canonical_json_bytes(
            {
                "schema_version": _PACK_SCHEMA_VERSION,
                "operation_id": operation_id,
                "artifacts": {
                    relative: hashlib.sha256(content).hexdigest()
                    for relative, content in sorted(artifacts.items())
                },
            }
        )
        manifest_path = staging / "artifact-hashes.json"
        with manifest_path.open("xb") as handle:
            handle.write(manifest_bytes)
        manifest_path.chmod(0o600)
        staging.rename(destination)
        return hashlib.sha256(manifest_bytes).hexdigest()
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _safe_pack_child(root: Path, relative: object) -> Path | None:
    if type(relative) is not str:
        return None
    logical = PurePosixPath(relative)
    if (
        logical.is_absolute()
        or "\\" in relative
        or any(part in {"", ".", ".."} for part in logical.parts)
    ):
        return None
    target = root.joinpath(*logical.parts)
    try:
        resolved_root = root.resolve(strict=True)
        resolved = target.resolve(strict=True)
    except OSError:
        return None
    if resolved.parent != resolved_root or not resolved.is_file():
        return None
    return resolved


def _verdict_payload(verdict: object) -> dict[str, object]:
    from .performance_verdicts import PerformanceCriterionVerdict

    if type(verdict) is not PerformanceCriterionVerdict:
        raise TypeError("Inferdrome verdict is invalid.")
    return {
        "criterion_id": verdict.criterion_id,
        "verdict": verdict.verdict.value,
        "attempted_count": verdict.attempted_count,
        "successful_count": verdict.successful_count,
        "error_count": verdict.error_count,
        "ttft_p95": {
            "verdict": verdict.ttft_p95.verdict.value,
            "observed_ns": verdict.ttft_p95.observed_ns,
            "threshold_ns": verdict.ttft_p95.threshold_ns,
            "operator": verdict.ttft_p95.operator,
            "successful_samples": verdict.ttft_p95.successful_samples,
            "minimum_successful_samples": (
                verdict.ttft_p95.minimum_successful_samples
            ),
            "reason": verdict.ttft_p95.reason,
        },
        "error_rate": {
            "verdict": verdict.error_rate.verdict.value,
            "error_count": verdict.error_rate.error_count,
            "attempted_count": verdict.error_rate.attempted_count,
            "observed_rate": (
                None
                if verdict.error_rate.observed_rate is None
                else str(verdict.error_rate.observed_rate)
            ),
            "threshold": str(verdict.error_rate.threshold),
            "operator": verdict.error_rate.operator,
            "minimum_attempts": verdict.error_rate.minimum_attempts,
            "reason": verdict.error_rate.reason,
        },
        "calculation_version": verdict.calculation_version,
        "reason": verdict.reason,
        "limitations": list(verdict.limitations),
        "outcome_counts": None,
    }


def _managed_verdict_payload(
    contract: POCContract,
    result: InferdromeManagedImportResult,
) -> dict[str, object]:
    criteria = tuple(
        criterion
        for criterion in contract.criteria
        if type(criterion) is InferencePerformanceCriterionV3
    )
    if len(criteria) != 1 or len(contract.criteria) != 1:
        raise TypeError("Managed Inferdrome verdict contract is invalid.")
    criterion = criteria[0]
    recalculated = result.recalculated
    return {
        "criterion_id": criterion.id,
        "verdict": result.verdict.value,
        "attempted_count": recalculated.attempted_count,
        "successful_count": recalculated.successful_count,
        "error_count": recalculated.failed_count,
        "ttft_p95": {
            "observed_ns": recalculated.p95_ttft_ns,
            "threshold_ns": criterion.ttft_p95.threshold_ns,
            "operator": criterion.ttft_p95.operator,
            "definition_id": criterion.ttft_p95.definition_id,
            "reducer_id": criterion.ttft_p95.reducer_id,
            "population": criterion.ttft_p95.population,
            "minimum_successful_samples": (
                criterion.ttft_p95.minimum_successful_samples
            ),
        },
        "error_rate": {
            "observed_rate": str(recalculated.error_rate),
            "threshold_basis_points": (
                criterion.error_rate.threshold_basis_points
            ),
            "operator": criterion.error_rate.operator,
            "numerator": criterion.error_rate.numerator,
            "denominator": criterion.error_rate.denominator,
            "exact_attempts": criterion.error_rate.exact_attempts,
        },
        "applicability_codes": [
            issue.value for issue in result.applicability.issues
        ],
        "calculation_version": result.receipt.calculation_version,
        "receipt_id": result.receipt.receipt_id,
        "bundle_digest": result.receipt.bundle_digest,
        "reason": (
            "All applicable frozen requirements passed."
            if result.verdict is VerdictStatus.PASS
            else "An applicable frozen requirement failed."
            if result.verdict is VerdictStatus.FAIL
            else "The accepted evidence cannot prove this frozen slice."
        ),
        "limitations": list(contract.non_goals),
        "outcome_counts": None,
    }


def _launch_worker(target: Callable[[], None]) -> None:
    Thread(target=target, daemon=True).start()


def _safe_single_line(value: object, maximum: int) -> str:
    if type(value) is not str:
        raise POCInferdromeImportInvalid
    normalized = unicodedata.normalize("NFC", value)
    if (
        not normalized
        or normalized != normalized.strip()
        or len(normalized) > maximum
        or any(
            ord(character) < 0x20 or ord(character) == 0x7F
            for character in normalized
        )
    ):
        raise POCInferdromeImportInvalid
    return normalized


def _idempotency_digest(value: object) -> str:
    return hashlib.sha256(
        b"exitspec-poc-inferdrome-idempotency-v1\x00"
        + _safe_single_line(value, MAX_IDEMPOTENCY_KEY_LENGTH).encode("utf-8")
    ).hexdigest()


def _contract_hash(contract: POCContract) -> str:
    if type(contract.canonical_hash) is not str:
        raise POCInferdromeImportConflict
    return contract.canonical_hash


def _import_fingerprint(
    poc_id: str,
    bundle: PreparedPerformanceBundle | PreparedManagedInferdromeBundle,
    confirmation: ContractConfirmation,
    frozen: POCContract,
    run_id: str,
    bundle_digest: str,
) -> str:
    if type(bundle) is PreparedManagedInferdromeBundle:
        return hashlib.sha256(
            b"exitspec-poc-managed-inferdrome-import-v1\x00"
            + canonical_json_bytes(
                {
                    "poc_id": poc_id,
                    "contract_hash": _contract_hash(frozen),
                    "confirmation_id": confirmation.confirmation_id,
                    "prepared_bundle_fingerprint": bundle.bundle_fingerprint,
                    "selected_run_id": run_id,
                    "selected_bundle_digest": bundle_digest,
                }
            )
        ).hexdigest()
    if type(bundle) is not PreparedPerformanceBundle:
        raise POCInferdromeImportConflict
    return hashlib.sha256(
        b"exitspec-poc-inferdrome-import-v1\x00"
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
                "run_id": run_id,
                "bundle_digest": bundle_digest,
            }
        )
    ).hexdigest()


def _utc_time(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise POCInferdromeImportError("clock must return an aware datetime.")
    return value.astimezone(UTC)


def _decimal_text(value: Decimal) -> str:
    return format(value.normalize(), "f")


__all__ = [
    "POCInferdromeImportCapacityExceeded",
    "POCInferdromeImportConflict",
    "POCInferdromeImportError",
    "POCInferdromeImportInvalid",
    "POCInferdromeImportNotFound",
    "POCInferdromeImportSnapshot",
    "POCInferdromeImportStartSnapshot",
    "POCInferdromeImportStatus",
    "ProcessLocalPOCInferdromeImportService",
]
