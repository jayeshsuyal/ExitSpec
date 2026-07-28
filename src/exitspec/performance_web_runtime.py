"""Thread-safe browser coordination for the bundled performance proof.

This module is deliberately smaller than the authoritative performance runner.
It owns browser-safe readiness and operation state, but it does not accept or
construct execution inputs from request payloads.  A server creates one runtime
from the bundled, validated v2 demo and server-owned configuration, then injects
adapters that call the existing readiness probe, runner, and artifact verifier.

Read methods are side-effect free.  Only ``refresh_readiness`` may probe the
configured target, and only ``start`` may launch execution work.
"""

from __future__ import annotations

import hashlib
import re
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Final, Protocol
from urllib.parse import urlsplit

from .performance_workspace import (
    PERFORMANCE_CONTRACT_HASH,
    PERFORMANCE_POC_ID,
    PerformanceDemoBundle,
    load_performance_demo_bundle,
)


_IDEMPOTENCY_DOMAIN: Final = b"exitspec-performance-web-idempotency-v1\x00"
_MAX_IDEMPOTENCY_KEY_BYTES: Final = 256
_MAX_OPERATIONS_LIMIT: Final = 256
_OPERATION_ID = re.compile(r"pwop_[a-f0-9]{32}\Z")
_CONTROL_CHARACTER = re.compile(r"[\x00-\x1f\x7f]")
_TERMINAL_STATUSES: Final = frozenset(
    {
        "COMPLETED",
        "BLOCKED",
        "NOT_PROVEN",
    }
)


class PerformanceWebStatus(StrEnum):
    """Browser-visible states without conflating execution and verdicts."""

    NOT_STARTED = "NOT_STARTED"
    VALIDATING = "VALIDATING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    BLOCKED = "BLOCKED"
    NOT_PROVEN = "NOT_PROVEN"


class PerformanceWebRuntimeError(RuntimeError):
    """Base error for the in-process browser coordinator."""


class PerformanceWebConflictError(PerformanceWebRuntimeError):
    """Another idempotency key already owns the single execution flight."""


class PerformanceWebCapacityError(PerformanceWebRuntimeError):
    """The bounded in-memory idempotency history is full."""


class PerformanceWebOperationNotFound(PerformanceWebRuntimeError):
    """The requested browser operation does not exist."""


@dataclass(frozen=True, slots=True, repr=False)
class PerformanceWebServerConfig:
    """Server-owned values that browser payloads can never override."""

    output_root: Path
    operation_database_path: Path | None = None
    api_key: str | None = field(default=None, repr=False)
    artifact_url_prefix: str = "/artifacts/"
    max_operations: int = 64

    def __post_init__(self) -> None:
        output_root = _require_absolute_path(
            self.output_root,
            "output_root",
        )
        database_path = self.operation_database_path
        if database_path is not None:
            database_path = _require_absolute_path(
                database_path,
                "operation_database_path",
            )
        _require_optional_secret(self.api_key)
        prefix = _require_artifact_url_prefix(self.artifact_url_prefix)
        if (
            isinstance(self.max_operations, bool)
            or type(self.max_operations) is not int
            or not 1 <= self.max_operations <= _MAX_OPERATIONS_LIMIT
        ):
            raise ValueError(
                "max_operations must be an integer from 1 through {0}.".format(
                    _MAX_OPERATIONS_LIMIT
                )
            )
        object.__setattr__(self, "output_root", output_root)
        object.__setattr__(
            self,
            "operation_database_path",
            database_path,
        )
        object.__setattr__(self, "artifact_url_prefix", prefix)

    def __repr__(self) -> str:
        return (
            "PerformanceWebServerConfig(output_root={0!r}, "
            "operation_database_path={1!r}, api_key=<redacted>, "
            "artifact_url_prefix={2!r}, max_operations={3!r})"
        ).format(
            self.output_root,
            self.operation_database_path,
            self.artifact_url_prefix,
            self.max_operations,
        )


@dataclass(frozen=True, slots=True, repr=False)
class PerformanceWebExecution:
    """Exact authority passed only to trusted server-side dependencies."""

    bundle: PerformanceDemoBundle = field(repr=False)
    output_root: Path
    operation_database_path: Path | None
    api_key: str | None = field(default=None, repr=False)

    @property
    def poc_id(self) -> str:
        return PERFORMANCE_POC_ID

    @property
    def contract_hash(self) -> str:
        contract_hash = self.bundle.context.contract.canonical_hash
        if contract_hash != PERFORMANCE_CONTRACT_HASH:
            raise PerformanceWebRuntimeError(
                "Bundled performance contract identity changed."
            )
        return contract_hash

    @property
    def endpoint(self) -> str:
        return self.bundle.context.workload.endpoint

    @property
    def credential_endpoint(self) -> str | None:
        if self.api_key is None:
            return None
        return self.endpoint

    @property
    def model(self) -> str:
        return self.bundle.context.workload.model

    @property
    def workload_id(self) -> str:
        return self.bundle.context.workload.workload_id

    @property
    def request_count(self) -> int:
        return self.bundle.context.workload.request_count

    @property
    def concurrency(self) -> int:
        return self.bundle.context.workload.concurrency

    @property
    def warmup_count(self) -> int:
        return self.bundle.context.workload.warmup_count

    @property
    def authorized_request_count(self) -> int:
        """Exact preflight + warmup + measured request authorization."""

        return 1 + self.warmup_count + self.request_count

    def __repr__(self) -> str:
        return (
            "PerformanceWebExecution(poc_id={0!r}, "
            "contract_hash={1!r}, workload_id={2!r}, "
            "output_root={3!r}, api_key=<redacted>)"
        ).format(
            self.poc_id,
            self.contract_hash,
            self.workload_id,
            self.output_root,
        )


@dataclass(frozen=True, slots=True)
class PerformanceReadinessResult:
    """Trusted readiness dependency result.

    ``COMPLETED`` means the bounded readiness check succeeded.  It is not a
    performance verdict and does not authorize an Evidence Pack.
    """

    status: PerformanceWebStatus

    def __post_init__(self) -> None:
        if type(self.status) is not PerformanceWebStatus or self.status not in {
            PerformanceWebStatus.COMPLETED,
            PerformanceWebStatus.BLOCKED,
            PerformanceWebStatus.NOT_PROVEN,
        }:
            raise ValueError(
                "Readiness result must be COMPLETED, BLOCKED, or NOT_PROVEN."
            )


@dataclass(frozen=True, slots=True)
class PerformanceWebRunnerResult:
    """Narrow result returned by an adapter over the authoritative runner."""

    status: PerformanceWebStatus
    artifact_subject: object | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if type(self.status) is not PerformanceWebStatus or self.status not in {
            PerformanceWebStatus.COMPLETED,
            PerformanceWebStatus.BLOCKED,
            PerformanceWebStatus.NOT_PROVEN,
        }:
            raise ValueError(
                "Runner result must be COMPLETED, BLOCKED, or NOT_PROVEN."
            )
        if (
            self.status is PerformanceWebStatus.COMPLETED
            and self.artifact_subject is None
        ):
            raise ValueError(
                "A completed runner result requires an artifact subject."
            )
        if (
            self.status is not PerformanceWebStatus.COMPLETED
            and self.artifact_subject is not None
        ):
            raise ValueError(
                "A non-completed runner result cannot expose an artifact."
            )


@dataclass(frozen=True, slots=True)
class PerformanceReadinessSnapshot:
    """Immutable public readiness state."""

    poc_id: str
    contract_hash: str
    workload_id: str
    status: PerformanceWebStatus
    reason_code: str | None


@dataclass(frozen=True, slots=True)
class PerformanceOperationSnapshot:
    """Immutable public operation state with no execution credentials."""

    poc_id: str
    contract_hash: str
    workload_id: str
    operation_id: str | None
    status: PerformanceWebStatus
    reason_code: str | None
    evidence_pack_url: str | None

    @property
    def is_terminal(self) -> bool:
        return self.status.value in _TERMINAL_STATUSES


@dataclass(frozen=True, slots=True)
class PerformanceStartSnapshot:
    """Start response that distinguishes creation from idempotent replay."""

    operation: PerformanceOperationSnapshot
    replayed: bool


class ReadinessProbe(Protocol):
    def __call__(
        self,
        execution: PerformanceWebExecution,
    ) -> PerformanceReadinessResult: ...


class PerformanceRunner(Protocol):
    def __call__(
        self,
        execution: PerformanceWebExecution,
        idempotency_key: str,
    ) -> PerformanceWebRunnerResult: ...


class EvidencePackVerifier(Protocol):
    def __call__(self, artifact_subject: object) -> str: ...


class WorkerLauncher(Protocol):
    def __call__(self, target: Callable[[], None]) -> None: ...


@dataclass(slots=True)
class _OperationRecord:
    operation_id: str
    idempotency_key_digest: str
    status: PerformanceWebStatus
    reason_code: str | None = None
    evidence_pack_url: str | None = None


class PerformanceWebRuntime:
    """Coordinate one bounded, single-flight browser execution boundary."""

    __slots__ = (
        "_active_operation_id",
        "_config",
        "_evidence_pack_verifier",
        "_execution",
        "_idempotency_index",
        "_latest_operation_id",
        "_lock",
        "_operations",
        "_readiness_probe",
        "_readiness_refreshing",
        "_readiness_status",
        "_readiness_reason",
        "_runner",
        "_worker_launcher",
    )

    def __init__(
        self,
        *,
        config: PerformanceWebServerConfig,
        readiness_probe: ReadinessProbe,
        runner: PerformanceRunner,
        evidence_pack_verifier: EvidencePackVerifier,
        worker_launcher: WorkerLauncher | None = None,
    ) -> None:
        if type(config) is not PerformanceWebServerConfig:
            raise TypeError(
                "config must be a PerformanceWebServerConfig."
            )
        for dependency, name in (
            (readiness_probe, "readiness_probe"),
            (runner, "runner"),
            (evidence_pack_verifier, "evidence_pack_verifier"),
        ):
            if not callable(dependency):
                raise TypeError("{0} must be callable.".format(name))
        if worker_launcher is not None and not callable(worker_launcher):
            raise TypeError("worker_launcher must be callable.")

        bundle = load_performance_demo_bundle()
        if (
            bundle.context.contract.canonical_hash
            != PERFORMANCE_CONTRACT_HASH
        ):
            raise PerformanceWebRuntimeError(
                "Bundled performance contract identity changed."
            )
        self._config = config
        self._execution = PerformanceWebExecution(
            bundle=bundle,
            output_root=config.output_root,
            operation_database_path=config.operation_database_path,
            api_key=config.api_key,
        )
        self._readiness_probe = readiness_probe
        self._runner = runner
        self._evidence_pack_verifier = evidence_pack_verifier
        self._worker_launcher = worker_launcher or _launch_daemon_worker
        self._lock = threading.RLock()
        self._operations: dict[str, _OperationRecord] = {}
        self._idempotency_index: dict[str, str] = {}
        self._latest_operation_id: str | None = None
        self._active_operation_id: str | None = None
        self._readiness_status = PerformanceWebStatus.NOT_STARTED
        self._readiness_reason: str | None = None
        self._readiness_refreshing = False

    def readiness_snapshot(self) -> PerformanceReadinessSnapshot:
        """Return current readiness without probing or launching a thread."""

        with self._lock:
            return self._readiness_snapshot_locked()

    def refresh_readiness(self) -> PerformanceReadinessSnapshot:
        """Run one explicit bounded readiness check.

        A concurrent refresh or active operation owns readiness validation, so
        another caller receives the current immutable snapshot without causing
        duplicate network work.
        """

        with self._lock:
            if (
                self._readiness_refreshing
                or self._active_operation_id is not None
            ):
                return self._readiness_snapshot_locked()
            self._readiness_refreshing = True
            self._readiness_status = PerformanceWebStatus.VALIDATING
            self._readiness_reason = None

        try:
            result = self._call_readiness_probe()
            status, reason = _map_readiness_result(result)
        except Exception:
            status = PerformanceWebStatus.NOT_PROVEN
            reason = "READINESS_INTERNAL_FAILURE"

        with self._lock:
            self._readiness_status = status
            self._readiness_reason = reason
            self._readiness_refreshing = False
            return self._readiness_snapshot_locked()

    def start(
        self,
        *,
        idempotency_key: str,
    ) -> PerformanceStartSnapshot:
        """Start or replay the exact bundled operation.

        No contract, workload, endpoint, model, request shape, path, or
        credential parameter exists at this browser-facing boundary.
        """

        key_digest = _idempotency_key_digest(idempotency_key)
        with self._lock:
            existing_id = self._idempotency_index.get(key_digest)
            if existing_id is not None:
                return PerformanceStartSnapshot(
                    operation=self._snapshot_locked(
                        self._operations[existing_id]
                    ),
                    replayed=True,
                )
            if (
                self._active_operation_id is not None
                or self._readiness_refreshing
            ):
                raise PerformanceWebConflictError(
                    "Another performance operation already owns validation "
                    "or execution."
                )
            if len(self._operations) >= self._config.max_operations:
                raise PerformanceWebCapacityError(
                    "The bounded performance operation history is full."
                )

            operation_id = _new_operation_id(self._operations)
            record = _OperationRecord(
                operation_id=operation_id,
                idempotency_key_digest=key_digest,
                status=PerformanceWebStatus.VALIDATING,
            )
            self._operations[operation_id] = record
            self._idempotency_index[key_digest] = operation_id
            self._latest_operation_id = operation_id
            self._active_operation_id = operation_id

        try:
            self._worker_launcher(
                lambda: self._execute(operation_id, idempotency_key)
            )
        except Exception:
            self._mark_terminal(
                operation_id,
                PerformanceWebStatus.NOT_PROVEN,
                "WORKER_START_FAILED",
            )

        with self._lock:
            return PerformanceStartSnapshot(
                operation=self._snapshot_locked(
                    self._operations[operation_id]
                ),
                replayed=False,
            )

    def latest_operation_snapshot(self) -> PerformanceOperationSnapshot:
        """Return the latest state without executing work."""

        with self._lock:
            if self._latest_operation_id is None:
                return self._not_started_snapshot()
            return self._snapshot_locked(
                self._operations[self._latest_operation_id]
            )

    def operation_snapshot(
        self,
        operation_id: str,
    ) -> PerformanceOperationSnapshot:
        """Read one known operation without probing or launching work."""

        if (
            type(operation_id) is not str
            or _OPERATION_ID.fullmatch(operation_id) is None
        ):
            raise PerformanceWebOperationNotFound(
                "Performance operation was not found."
            )
        with self._lock:
            record = self._operations.get(operation_id)
            if record is None:
                raise PerformanceWebOperationNotFound(
                    "Performance operation was not found."
                )
            return self._snapshot_locked(record)

    def _execute(
        self,
        operation_id: str,
        idempotency_key: str,
    ) -> None:
        with self._lock:
            record = self._operations[operation_id]
            if (
                record.status is not PerformanceWebStatus.VALIDATING
                or self._active_operation_id != operation_id
            ):
                return
            record.status = PerformanceWebStatus.RUNNING
            record.reason_code = None

        try:
            runner_result = self._runner(
                self._execution,
                idempotency_key,
            )
            if type(runner_result) is not PerformanceWebRunnerResult:
                raise TypeError(
                    "runner must return PerformanceWebRunnerResult."
                )
        except Exception:
            self._mark_terminal(
                operation_id,
                PerformanceWebStatus.NOT_PROVEN,
                "RUNNER_INTERNAL_FAILURE",
            )
            return

        if runner_result.status is PerformanceWebStatus.BLOCKED:
            self._mark_terminal(
                operation_id,
                PerformanceWebStatus.BLOCKED,
                "RUNNER_BLOCKED",
            )
            return
        if runner_result.status is PerformanceWebStatus.NOT_PROVEN:
            self._mark_terminal(
                operation_id,
                PerformanceWebStatus.NOT_PROVEN,
                "RUNNER_NOT_PROVEN",
            )
            return

        try:
            artifact_url = self._evidence_pack_verifier(
                runner_result.artifact_subject
            )
        except Exception:
            self._mark_terminal(
                operation_id,
                PerformanceWebStatus.NOT_PROVEN,
                "EVIDENCE_VERIFICATION_FAILED",
            )
            return
        try:
            safe_url = _require_safe_artifact_url(
                artifact_url,
                prefix=self._config.artifact_url_prefix,
            )
        except (TypeError, ValueError):
            self._mark_terminal(
                operation_id,
                PerformanceWebStatus.NOT_PROVEN,
                "EVIDENCE_PACK_URL_INVALID",
            )
            return

        self._mark_terminal(
            operation_id,
            PerformanceWebStatus.COMPLETED,
            None,
            evidence_pack_url=safe_url,
        )

    def _call_readiness_probe(self) -> PerformanceReadinessResult:
        result = self._readiness_probe(self._execution)
        if type(result) is not PerformanceReadinessResult:
            raise TypeError(
                "readiness_probe must return PerformanceReadinessResult."
            )
        return result

    def _mark_terminal(
        self,
        operation_id: str,
        status: PerformanceWebStatus,
        reason_code: str | None,
        *,
        evidence_pack_url: str | None = None,
    ) -> None:
        if status.value not in _TERMINAL_STATUSES:
            raise PerformanceWebRuntimeError(
                "Only a terminal status may finish an operation."
            )
        if status is PerformanceWebStatus.COMPLETED:
            if evidence_pack_url is None or reason_code is not None:
                raise PerformanceWebRuntimeError(
                    "Completed state requires only a verified Evidence Pack."
                )
        elif evidence_pack_url is not None or reason_code is None:
            raise PerformanceWebRuntimeError(
                "Non-completed terminal state requires a safe reason and no "
                "Evidence Pack."
            )
        with self._lock:
            record = self._operations[operation_id]
            if record.status.value in _TERMINAL_STATUSES:
                return
            record.status = status
            record.reason_code = reason_code
            record.evidence_pack_url = evidence_pack_url
            if self._active_operation_id == operation_id:
                self._active_operation_id = None

    def _readiness_snapshot_locked(
        self,
    ) -> PerformanceReadinessSnapshot:
        return PerformanceReadinessSnapshot(
            poc_id=self._execution.poc_id,
            contract_hash=self._execution.contract_hash,
            workload_id=self._execution.workload_id,
            status=self._readiness_status,
            reason_code=self._readiness_reason,
        )

    def _not_started_snapshot(self) -> PerformanceOperationSnapshot:
        return PerformanceOperationSnapshot(
            poc_id=self._execution.poc_id,
            contract_hash=self._execution.contract_hash,
            workload_id=self._execution.workload_id,
            operation_id=None,
            status=PerformanceWebStatus.NOT_STARTED,
            reason_code=None,
            evidence_pack_url=None,
        )

    def _snapshot_locked(
        self,
        record: _OperationRecord,
    ) -> PerformanceOperationSnapshot:
        return PerformanceOperationSnapshot(
            poc_id=self._execution.poc_id,
            contract_hash=self._execution.contract_hash,
            workload_id=self._execution.workload_id,
            operation_id=record.operation_id,
            status=record.status,
            reason_code=record.reason_code,
            evidence_pack_url=record.evidence_pack_url,
        )


def _map_readiness_result(
    result: PerformanceReadinessResult,
) -> tuple[PerformanceWebStatus, str | None]:
    if result.status is PerformanceWebStatus.COMPLETED:
        return result.status, None
    if result.status is PerformanceWebStatus.BLOCKED:
        return result.status, "ENDPOINT_PREFLIGHT_FAILED"
    return PerformanceWebStatus.NOT_PROVEN, "READINESS_NOT_PROVEN"


def _idempotency_key_digest(idempotency_key: str) -> str:
    if type(idempotency_key) is not str or not idempotency_key:
        raise ValueError("idempotency_key must be a non-empty string.")
    try:
        encoded = idempotency_key.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ValueError("idempotency_key must be valid UTF-8.") from error
    if (
        len(encoded) > _MAX_IDEMPOTENCY_KEY_BYTES
        or idempotency_key != idempotency_key.strip()
        or _CONTROL_CHARACTER.search(idempotency_key) is not None
    ):
        raise ValueError(
            "idempotency_key must be bounded and contain no surrounding "
            "whitespace or control characters."
        )
    return hashlib.sha256(_IDEMPOTENCY_DOMAIN + encoded).hexdigest()


def _new_operation_id(
    existing: dict[str, _OperationRecord],
) -> str:
    for _ in range(8):
        operation_id = "pwop_{0}".format(uuid.uuid4().hex)
        if operation_id not in existing:
            return operation_id
    raise PerformanceWebRuntimeError(
        "A unique performance operation ID could not be allocated."
    )


def _launch_daemon_worker(target: Callable[[], None]) -> None:
    worker = threading.Thread(
        target=target,
        name="exitspec-performance-web",
        daemon=True,
    )
    worker.start()


def _require_absolute_path(value: Path, label: str) -> Path:
    if not isinstance(value, Path):
        raise TypeError("{0} must be a pathlib.Path.".format(label))
    if not value.is_absolute():
        raise ValueError("{0} must be absolute.".format(label))
    return value


def _require_optional_secret(value: str | None) -> None:
    if value is None:
        return
    if (
        type(value) is not str
        or not value
        or len(value.encode("utf-8")) > 8192
        or value != value.strip()
        or _CONTROL_CHARACTER.search(value) is not None
    ):
        raise ValueError(
            "api_key must be bounded and contain no surrounding whitespace "
            "or control characters."
        )


def _require_artifact_url_prefix(value: str) -> str:
    if (
        type(value) is not str
        or not value.startswith("/artifacts/")
        or not value.endswith("/")
        or "\\" in value
        or "%" in value
        or "//" in value
        or _CONTROL_CHARACTER.search(value) is not None
    ):
        raise ValueError(
            "artifact_url_prefix must be a safe local /artifacts/ path."
        )
    parts = value.split("/")
    if any(part in {".", ".."} for part in parts):
        raise ValueError(
            "artifact_url_prefix cannot contain traversal segments."
        )
    return value


def _require_safe_artifact_url(value: str, *, prefix: str) -> str:
    if (
        type(value) is not str
        or not value
        or len(value) > 500
        or not value.startswith(prefix)
        or "\\" in value
        or "%" in value
        or "//" in value
        or _CONTROL_CHARACTER.search(value) is not None
    ):
        raise ValueError("Evidence Pack URL is not a safe local artifact URL.")
    parsed = urlsplit(value)
    if (
        parsed.scheme
        or parsed.netloc
        or parsed.query
        or parsed.fragment
        or parsed.path != value
    ):
        raise ValueError("Evidence Pack URL is not a safe local artifact URL.")
    raw_parts = value.split("/")
    if any(part in {"", ".", ".."} for part in raw_parts[1:-1]):
        raise ValueError("Evidence Pack URL contains an unsafe path segment.")
    logical = PurePosixPath(value)
    if (
        not logical.is_absolute()
        or logical.name != "decision-packet.html"
        or len(logical.parts) < 4
    ):
        raise ValueError(
            "Evidence Pack URL must identify a local decision packet."
        )
    return value


__all__ = [
    "EvidencePackVerifier",
    "PerformanceOperationSnapshot",
    "PerformanceReadinessResult",
    "PerformanceReadinessSnapshot",
    "PerformanceRunner",
    "PerformanceStartSnapshot",
    "PerformanceWebCapacityError",
    "PerformanceWebConflictError",
    "PerformanceWebExecution",
    "PerformanceWebOperationNotFound",
    "PerformanceWebRunnerResult",
    "PerformanceWebRuntime",
    "PerformanceWebRuntimeError",
    "PerformanceWebServerConfig",
    "PerformanceWebStatus",
    "ReadinessProbe",
    "WorkerLauncher",
]
