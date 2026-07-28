"""Durable idempotency reservations for performance executions.

The ledger is deliberately smaller than the performance runner. It reserves an
operation before network work starts and persists only secret-free identities
and terminal state. It does not execute probes, calculate verdicts, render
reports, or persist raw idempotency/API keys.
"""

from __future__ import annotations

import fcntl
import hashlib
import hmac
import os
import re
import secrets
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Callable, Iterator

from .canonical import canonical_json_bytes


PERFORMANCE_OPERATION_SCHEMA_NAME = "exitspec.performance-operations"
PERFORMANCE_OPERATION_SCHEMA_VERSION = 1

# v2 adds the exact key-redacted customer-confirmation digest. Existing v1
# reservations therefore fail closed as input conflicts instead of silently
# replaying under broader binding semantics.
_INPUT_DIGEST_DOMAIN = b"exitspec-performance-operation-input-v2\x00"
_IDEMPOTENCY_KEY_DOMAIN = (
    b"exitspec-performance-operation-idempotency-key-v1\x00"
)

_SHA256 = re.compile(r"[a-f0-9]{64}\Z")
_RUN_ID = re.compile(r"run_[a-f0-9]{32}\Z")
_RECEIPT_ID = re.compile(r"prc_[a-f0-9]{64}\Z")
_ADAPTER = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z")
_ADAPTER_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
_TERMINAL_REASON = re.compile(r"[A-Z][A-Z0-9_]{0,63}\Z")
_CANONICAL_TIMESTAMP = re.compile(
    r"\d{4}-\d{2}-\d{2}T"
    r"\d{2}:\d{2}:\d{2}\.\d{6}Z\Z"
)

_MAX_RUN_ID_ATTEMPTS = 8
_DEFAULT_BUSY_TIMEOUT_SECONDS = 10.0
_ALLOWED_USER_TABLES = frozenset(
    {
        "performance_operation_metadata",
        "performance_operations",
    }
)
_EXPECTED_METADATA_COLUMNS = (
    "singleton",
    "schema_name",
    "schema_version",
)
_EXPECTED_OPERATION_COLUMNS = (
    "idempotency_key_digest",
    "input_digest",
    "run_id",
    "status",
    "created_at",
    "updated_at",
    "execution_id",
    "receipt_id",
    "artifact_registry_sha256",
    "terminal_reason",
)


class PerformanceOperationStatus(StrEnum):
    """Allowed durable states for a performance operation."""

    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    BLOCKED = "BLOCKED"
    NOT_PROVEN = "NOT_PROVEN"
    FAILED = "FAILED"


TERMINAL_OPERATION_STATUSES = frozenset(
    {
        PerformanceOperationStatus.COMPLETED,
        PerformanceOperationStatus.BLOCKED,
        PerformanceOperationStatus.NOT_PROVEN,
        PerformanceOperationStatus.FAILED,
    }
)


class PerformanceOperationError(RuntimeError):
    """Base error for the durable performance operation boundary."""


class PerformanceOperationConflict(PerformanceOperationError):
    """An idempotency key is already bound to different frozen inputs."""

    def __init__(self) -> None:
        super().__init__(
            "Performance idempotency key is already bound to different inputs."
        )


class PerformanceOperationIntegrityError(PerformanceOperationError):
    """Persisted operation state is malformed, corrupt, or contradictory."""


class PerformanceOperationTransitionError(PerformanceOperationError):
    """A requested state transition violates the terminal state machine."""


class PerformanceOperationStorageError(PerformanceOperationError):
    """The durable SQLite store could not be opened or updated safely."""


@dataclass(frozen=True, slots=True)
class PerformanceOperation:
    """One validated, secret-free operation row."""

    idempotency_key_digest: str
    input_digest: str
    run_id: str
    status: PerformanceOperationStatus
    created_at: datetime
    updated_at: datetime
    execution_id: str | None = None
    receipt_id: str | None = None
    artifact_registry_sha256: str | None = None
    terminal_reason: str | None = None

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_OPERATION_STATUSES


@dataclass(frozen=True, slots=True)
class PerformanceOperationReservation:
    """Result of atomically reserving an idempotency key."""

    operation: PerformanceOperation
    created: bool

    @property
    def should_execute(self) -> bool:
        """Only a newly created reservation may start network work."""

        return self.created


def performance_operation_idempotency_key_digest(
    idempotency_key: str,
) -> str:
    """Hash one ephemeral idempotency key without retaining the raw value."""

    _require_idempotency_key(idempotency_key)
    return _domain_sha256(
        _IDEMPOTENCY_KEY_DOMAIN,
        {"idempotency_key": idempotency_key},
    )


def performance_operation_input_digest(
    *,
    frozen_contract_hash: str,
    confirmation_hash: str,
    expected_manifest_hash: str,
    workload_hash: str,
    adapter: str,
    adapter_version: str,
) -> str:
    """Bind every frozen input that authorizes one performance execution."""

    _require_sha256(frozen_contract_hash, "frozen_contract_hash")
    _require_sha256(confirmation_hash, "confirmation_hash")
    _require_sha256(expected_manifest_hash, "expected_manifest_hash")
    _require_sha256(workload_hash, "workload_hash")
    _require_adapter(adapter)
    _require_adapter_version(adapter_version)
    return _domain_sha256(
        _INPUT_DIGEST_DOMAIN,
        {
            "adapter": adapter,
            "adapter_version": adapter_version,
            "confirmation_hash": confirmation_hash,
            "expected_manifest_hash": expected_manifest_hash,
            "frozen_contract_hash": frozen_contract_hash,
            "schema_version": PERFORMANCE_OPERATION_SCHEMA_VERSION,
            "workload_hash": workload_hash,
        },
    )


class SQLitePerformanceOperationLedger:
    """Process-safe SQLite idempotency ledger for performance executions.

    A separate SQLite connection is used for each operation. Reservations and
    terminal transitions take a ``BEGIN IMMEDIATE`` write lock so multiple
    threads or processes cannot authorize duplicate execution for one key.
    """

    __slots__ = (
        "_busy_timeout_seconds",
        "_clock",
        "_database_path",
        "_run_id_factory",
    )

    def __init__(
        self,
        database_path: str | os.PathLike[str],
        *,
        clock: Callable[[], datetime] | None = None,
        run_id_factory: Callable[[], str] | None = None,
        busy_timeout_seconds: float = _DEFAULT_BUSY_TIMEOUT_SECONDS,
    ) -> None:
        self._database_path = _require_safe_database_path(database_path)
        self._clock = clock or _utc_now
        self._run_id_factory = run_id_factory or _new_run_id
        if not callable(self._clock):
            raise TypeError("clock must be callable.")
        if not callable(self._run_id_factory):
            raise TypeError("run_id_factory must be callable.")
        if (
            isinstance(busy_timeout_seconds, bool)
            or not isinstance(busy_timeout_seconds, (int, float))
            or busy_timeout_seconds <= 0
            or busy_timeout_seconds > 60
        ):
            raise ValueError(
                "busy_timeout_seconds must be greater than 0 and at most 60."
            )
        self._busy_timeout_seconds = float(busy_timeout_seconds)
        self._initialize()

    def __repr__(self) -> str:
        return (
            "SQLitePerformanceOperationLedger(database_path={0!r})".format(
                str(self._database_path)
            )
        )

    @property
    def database_path(self) -> Path:
        return self._database_path

    def reserve(
        self,
        *,
        idempotency_key: str,
        frozen_contract_hash: str,
        confirmation_hash: str,
        expected_manifest_hash: str,
        workload_hash: str,
        adapter: str,
        adapter_version: str,
        created_at: datetime | None = None,
    ) -> PerformanceOperationReservation:
        """Reserve before network execution, replay, or reject a conflict."""

        key_digest = performance_operation_idempotency_key_digest(
            idempotency_key
        )
        input_digest = performance_operation_input_digest(
            frozen_contract_hash=frozen_contract_hash,
            confirmation_hash=confirmation_hash,
            expected_manifest_hash=expected_manifest_hash,
            workload_hash=workload_hash,
            adapter=adapter,
            adapter_version=adapter_version,
        )

        connection = self._connect()
        try:
            with _immediate_transaction(connection):
                existing_row = connection.execute(
                    """
                    SELECT idempotency_key_digest, input_digest, run_id,
                           status, created_at, updated_at, execution_id,
                           receipt_id, artifact_registry_sha256,
                           terminal_reason
                      FROM performance_operations
                     WHERE idempotency_key_digest = ?
                    """,
                    (key_digest,),
                ).fetchone()
                if existing_row is not None:
                    existing = _operation_from_row(existing_row)
                    if not hmac.compare_digest(
                        existing.input_digest,
                        input_digest,
                    ):
                        raise PerformanceOperationConflict()
                    return PerformanceOperationReservation(
                        operation=existing,
                        created=False,
                    )

                resolved_created_at = _require_aware_datetime(
                    created_at if created_at is not None else self._clock(),
                    "created_at",
                )
                canonical_created_at = _canonical_timestamp(
                    resolved_created_at
                )
                run_id = self._unused_run_id(connection)
                connection.execute(
                    """
                    INSERT INTO performance_operations (
                        idempotency_key_digest,
                        input_digest,
                        run_id,
                        status,
                        created_at,
                        updated_at,
                        execution_id,
                        receipt_id,
                        artifact_registry_sha256,
                        terminal_reason
                    ) VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL)
                    """,
                    (
                        key_digest,
                        input_digest,
                        run_id,
                        PerformanceOperationStatus.RUNNING.value,
                        canonical_created_at,
                        canonical_created_at,
                    ),
                )
                operation = PerformanceOperation(
                    idempotency_key_digest=key_digest,
                    input_digest=input_digest,
                    run_id=run_id,
                    status=PerformanceOperationStatus.RUNNING,
                    created_at=resolved_created_at,
                    updated_at=resolved_created_at,
                )
                return PerformanceOperationReservation(
                    operation=operation,
                    created=True,
                )
        except sqlite3.DatabaseError as exc:
            raise PerformanceOperationStorageError(
                "Could not reserve a durable performance operation."
            ) from exc
        finally:
            connection.close()

    def mark_terminal(
        self,
        *,
        run_id: str,
        input_digest: str,
        status: PerformanceOperationStatus | str,
        execution_id: str | None = None,
        receipt_id: str | None = None,
        artifact_registry_sha256: str | None = None,
        terminal_reason: str | None = None,
        updated_at: datetime | None = None,
    ) -> PerformanceOperation:
        """Apply the only transition: RUNNING to one terminal status."""

        _require_run_id(run_id)
        _require_sha256(input_digest, "input_digest")
        resolved_status = _require_terminal_status(status)
        _require_execution_id_or_none(execution_id)
        _require_receipt_id_or_none(receipt_id)
        _require_sha256_or_none(
            artifact_registry_sha256,
            "artifact_registry_sha256",
        )
        _require_terminal_reason_or_none(terminal_reason)
        if resolved_status is PerformanceOperationStatus.COMPLETED:
            if (
                execution_id is None
                or receipt_id is None
                or artifact_registry_sha256 is None
                or terminal_reason is not None
            ):
                raise ValueError(
                    "COMPLETED requires execution, receipt, and artifact "
                    "registry identities with no terminal_reason."
                )
        elif (
            execution_id is not None
            or receipt_id is not None
            or artifact_registry_sha256 is not None
            or terminal_reason is None
        ):
            raise ValueError(
                "Non-completed terminal states require one reason code and "
                "cannot carry completed evidence identities."
            )
        resolved_updated_at = _require_aware_datetime(
            updated_at if updated_at is not None else self._clock(),
            "updated_at",
        )
        canonical_updated_at = _canonical_timestamp(resolved_updated_at)

        connection = self._connect()
        try:
            with _immediate_transaction(connection):
                row = connection.execute(
                    """
                    SELECT idempotency_key_digest, input_digest, run_id,
                           status, created_at, updated_at, execution_id,
                           receipt_id, artifact_registry_sha256,
                           terminal_reason
                      FROM performance_operations
                     WHERE run_id = ?
                    """,
                    (run_id,),
                ).fetchone()
                if row is None:
                    raise KeyError("Performance operation was not found.")
                existing = _operation_from_row(row)
                if not hmac.compare_digest(
                    existing.input_digest,
                    input_digest,
                ):
                    raise PerformanceOperationConflict()
                if existing.status is not PerformanceOperationStatus.RUNNING:
                    raise PerformanceOperationTransitionError(
                        "Terminal performance operations cannot be reopened "
                        "or transitioned again."
                    )
                if resolved_updated_at < existing.updated_at:
                    raise ValueError(
                        "updated_at cannot precede the persisted timestamp."
                    )

                cursor = connection.execute(
                    """
                    UPDATE performance_operations
                       SET status = ?,
                           updated_at = ?,
                           execution_id = ?,
                           receipt_id = ?,
                           artifact_registry_sha256 = ?,
                           terminal_reason = ?
                     WHERE run_id = ?
                       AND input_digest = ?
                       AND status = ?
                    """,
                    (
                        resolved_status.value,
                        canonical_updated_at,
                        execution_id,
                        receipt_id,
                        artifact_registry_sha256,
                        terminal_reason,
                        run_id,
                        input_digest,
                        PerformanceOperationStatus.RUNNING.value,
                    ),
                )
                if cursor.rowcount != 1:
                    raise PerformanceOperationIntegrityError(
                        "Performance operation transition lost atomicity."
                    )
                updated_row = connection.execute(
                    """
                    SELECT idempotency_key_digest, input_digest, run_id,
                           status, created_at, updated_at, execution_id,
                           receipt_id, artifact_registry_sha256,
                           terminal_reason
                      FROM performance_operations
                     WHERE run_id = ?
                    """,
                    (run_id,),
                ).fetchone()
                if updated_row is None:
                    raise PerformanceOperationIntegrityError(
                        "Terminal performance operation disappeared."
                    )
                return _operation_from_row(updated_row)
        except sqlite3.DatabaseError as exc:
            raise PerformanceOperationStorageError(
                "Could not update the durable performance operation."
            ) from exc
        finally:
            connection.close()

    def get_by_run_id(self, run_id: str) -> PerformanceOperation | None:
        """Load and validate one operation without changing its state."""

        _require_run_id(run_id)
        connection = self._connect()
        try:
            row = connection.execute(
                """
                SELECT idempotency_key_digest, input_digest, run_id,
                       status, created_at, updated_at, execution_id,
                       receipt_id, artifact_registry_sha256, terminal_reason
                  FROM performance_operations
                 WHERE run_id = ?
                """,
                (run_id,),
            ).fetchone()
            return None if row is None else _operation_from_row(row)
        except sqlite3.DatabaseError as exc:
            raise PerformanceOperationStorageError(
                "Could not read the durable performance operation."
            ) from exc
        finally:
            connection.close()

    def _initialize(self) -> None:
        with _database_initialization_lock(self._database_path):
            self._initialize_locked()

    def _initialize_locked(self) -> None:
        connection = self._connect()
        try:
            journal_mode = connection.execute(
                "PRAGMA journal_mode = WAL"
            ).fetchone()
            if (
                journal_mode is None
                or str(journal_mode[0]).lower() != "wal"
            ):
                raise PerformanceOperationStorageError(
                    "SQLite WAL mode is required for the operation ledger."
                )
            connection.execute("PRAGMA synchronous = FULL")
            quick_check = connection.execute("PRAGMA quick_check").fetchone()
            if quick_check is None or quick_check[0] != "ok":
                raise PerformanceOperationIntegrityError(
                    "SQLite integrity check failed."
                )

            with _immediate_transaction(connection):
                user_tables = {
                    str(row[0])
                    for row in connection.execute(
                        """
                        SELECT name
                          FROM sqlite_master
                         WHERE type = 'table'
                           AND name NOT LIKE 'sqlite_%'
                        """
                    )
                }
                if (
                    "performance_operation_metadata" not in user_tables
                    and user_tables
                ):
                    raise PerformanceOperationIntegrityError(
                        "Refusing to initialize an unrelated SQLite database."
                    )
                if not user_tables.issubset(_ALLOWED_USER_TABLES):
                    raise PerformanceOperationIntegrityError(
                        "Performance operation database has unknown tables."
                    )

                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS
                    performance_operation_metadata (
                        singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                        schema_name TEXT NOT NULL,
                        schema_version INTEGER NOT NULL
                    ) STRICT
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS performance_operations (
                        idempotency_key_digest TEXT PRIMARY KEY
                            CHECK (
                                length(idempotency_key_digest) = 64
                                AND idempotency_key_digest
                                    NOT GLOB '*[^0-9a-f]*'
                            ),
                        input_digest TEXT NOT NULL
                            CHECK (
                                length(input_digest) = 64
                                AND input_digest NOT GLOB '*[^0-9a-f]*'
                            ),
                        run_id TEXT NOT NULL UNIQUE
                            CHECK (
                                length(run_id) = 36
                                AND substr(run_id, 1, 4) = 'run_'
                                AND substr(run_id, 5)
                                    NOT GLOB '*[^0-9a-f]*'
                            ),
                        status TEXT NOT NULL
                            CHECK (
                                status IN (
                                    'RUNNING',
                                    'COMPLETED',
                                    'BLOCKED',
                                    'NOT_PROVEN',
                                    'FAILED'
                                )
                            ),
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        execution_id TEXT
                            CHECK (
                                execution_id IS NULL
                                OR (
                                    length(execution_id) = 36
                                    AND substr(execution_id, 1, 4) = 'run_'
                                    AND substr(execution_id, 5)
                                        NOT GLOB '*[^0-9a-f]*'
                                )
                            ),
                        receipt_id TEXT
                            CHECK (
                                receipt_id IS NULL
                                OR (
                                    length(receipt_id) = 68
                                    AND substr(receipt_id, 1, 4) = 'prc_'
                                    AND substr(receipt_id, 5)
                                        NOT GLOB '*[^0-9a-f]*'
                                )
                            ),
                        artifact_registry_sha256 TEXT
                            CHECK (
                                artifact_registry_sha256 IS NULL
                                OR (
                                    length(artifact_registry_sha256) = 64
                                    AND artifact_registry_sha256
                                        NOT GLOB '*[^0-9a-f]*'
                                )
                            ),
                        terminal_reason TEXT,
                        CHECK (
                            (
                                status = 'RUNNING'
                                AND execution_id IS NULL
                                AND receipt_id IS NULL
                                AND artifact_registry_sha256 IS NULL
                                AND terminal_reason IS NULL
                            )
                            OR (
                                status = 'COMPLETED'
                                AND execution_id IS NOT NULL
                                AND receipt_id IS NOT NULL
                                AND artifact_registry_sha256 IS NOT NULL
                                AND terminal_reason IS NULL
                            )
                            OR (
                                status IN (
                                    'BLOCKED',
                                    'NOT_PROVEN',
                                    'FAILED'
                                )
                                AND execution_id IS NULL
                                AND receipt_id IS NULL
                                AND artifact_registry_sha256 IS NULL
                                AND terminal_reason IS NOT NULL
                            )
                        )
                    ) STRICT
                    """
                )
                _require_exact_table_columns(
                    connection,
                    "performance_operation_metadata",
                    _EXPECTED_METADATA_COLUMNS,
                )
                _require_exact_table_columns(
                    connection,
                    "performance_operations",
                    _EXPECTED_OPERATION_COLUMNS,
                )

                metadata = connection.execute(
                    """
                    SELECT schema_name, schema_version
                      FROM performance_operation_metadata
                     WHERE singleton = 1
                    """
                ).fetchone()
                if metadata is None:
                    connection.execute(
                        """
                        INSERT INTO performance_operation_metadata (
                            singleton,
                            schema_name,
                            schema_version
                        ) VALUES (1, ?, ?)
                        """,
                        (
                            PERFORMANCE_OPERATION_SCHEMA_NAME,
                            PERFORMANCE_OPERATION_SCHEMA_VERSION,
                        ),
                    )
                elif (
                    metadata["schema_name"]
                    != PERFORMANCE_OPERATION_SCHEMA_NAME
                    or metadata["schema_version"]
                    != PERFORMANCE_OPERATION_SCHEMA_VERSION
                ):
                    raise PerformanceOperationIntegrityError(
                        "Unsupported performance operation schema."
                    )

                user_version = int(
                    connection.execute("PRAGMA user_version").fetchone()[0]
                )
                if user_version not in (
                    0,
                    PERFORMANCE_OPERATION_SCHEMA_VERSION,
                ):
                    raise PerformanceOperationIntegrityError(
                        "Unsupported SQLite operation schema version."
                    )
                connection.execute(
                    "PRAGMA user_version = {0}".format(
                        PERFORMANCE_OPERATION_SCHEMA_VERSION
                    )
                )
        except sqlite3.DatabaseError as exc:
            raise PerformanceOperationStorageError(
                "Could not initialize the durable performance operation "
                "ledger."
            ) from exc
        finally:
            connection.close()

    def _connect(self) -> sqlite3.Connection:
        try:
            connection = sqlite3.connect(
                self._database_path,
                timeout=self._busy_timeout_seconds,
                isolation_level=None,
            )
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute(
                "PRAGMA busy_timeout = {0}".format(
                    int(self._busy_timeout_seconds * 1000)
                )
            )
            connection.execute("PRAGMA trusted_schema = OFF")
            return connection
        except sqlite3.DatabaseError as exc:
            raise PerformanceOperationStorageError(
                "Could not open the durable performance operation ledger."
            ) from exc

    def _unused_run_id(self, connection: sqlite3.Connection) -> str:
        for _ in range(_MAX_RUN_ID_ATTEMPTS):
            run_id = self._run_id_factory()
            _require_run_id(run_id)
            collision = connection.execute(
                """
                SELECT 1
                  FROM performance_operations
                 WHERE run_id = ?
                """,
                (run_id,),
            ).fetchone()
            if collision is None:
                return run_id
        raise PerformanceOperationIntegrityError(
            "Could not allocate a unique performance run identity."
        )


@contextmanager
def _database_initialization_lock(database_path: Path) -> Iterator[None]:
    lock_path = database_path.parent / (
        ".{0}.initialize.lock".format(database_path.name)
    )
    if lock_path.is_symlink():
        raise PerformanceOperationIntegrityError(
            "Operation database initialization lock cannot be a symlink."
        )
    flags = os.O_CREAT | os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(lock_path, flags, 0o600)
    except OSError as exc:
        raise PerformanceOperationStorageError(
            "Could not acquire the operation initialization lock."
        ) from exc
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


@contextmanager
def _immediate_transaction(
    connection: sqlite3.Connection,
) -> Iterator[None]:
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        connection.rollback()
        raise
    else:
        connection.commit()


def _operation_from_row(row: sqlite3.Row) -> PerformanceOperation:
    try:
        key_digest = row["idempotency_key_digest"]
        input_digest = row["input_digest"]
        run_id = row["run_id"]
        status_value = row["status"]
        created_at_value = row["created_at"]
        updated_at_value = row["updated_at"]
        execution_id = row["execution_id"]
        receipt_id = row["receipt_id"]
        artifact_registry_sha256 = row["artifact_registry_sha256"]
        terminal_reason = row["terminal_reason"]

        _require_sha256(key_digest, "idempotency_key_digest")
        _require_sha256(input_digest, "input_digest")
        _require_run_id(run_id)
        status = PerformanceOperationStatus(status_value)
        created_at = _parse_canonical_timestamp(
            created_at_value,
            "created_at",
        )
        updated_at = _parse_canonical_timestamp(
            updated_at_value,
            "updated_at",
        )
        _require_execution_id_or_none(execution_id)
        _require_receipt_id_or_none(receipt_id)
        _require_sha256_or_none(
            artifact_registry_sha256,
            "artifact_registry_sha256",
        )
        _require_terminal_reason_or_none(terminal_reason)
    except (KeyError, TypeError, ValueError) as exc:
        raise PerformanceOperationIntegrityError(
            "Persisted performance operation is corrupt."
        ) from exc

    if updated_at < created_at:
        raise PerformanceOperationIntegrityError(
            "Persisted operation timestamps are contradictory."
        )
    if status is PerformanceOperationStatus.RUNNING and (
        execution_id is not None
        or receipt_id is not None
        or artifact_registry_sha256 is not None
        or terminal_reason is not None
    ):
        raise PerformanceOperationIntegrityError(
            "A running operation cannot contain terminal fields."
        )
    if status is PerformanceOperationStatus.COMPLETED and (
        execution_id is None
        or receipt_id is None
        or artifact_registry_sha256 is None
        or terminal_reason is not None
    ):
        raise PerformanceOperationIntegrityError(
            "A completed operation must contain exactly one receipt."
        )
    if status in TERMINAL_OPERATION_STATUSES - {
        PerformanceOperationStatus.COMPLETED
    } and (
        execution_id is not None
        or receipt_id is not None
        or artifact_registry_sha256 is not None
        or terminal_reason is None
    ):
        raise PerformanceOperationIntegrityError(
            "A non-completed terminal operation must contain only a reason."
        )
    return PerformanceOperation(
        idempotency_key_digest=key_digest,
        input_digest=input_digest,
        run_id=run_id,
        status=status,
        created_at=created_at,
        updated_at=updated_at,
        execution_id=execution_id,
        receipt_id=receipt_id,
        artifact_registry_sha256=artifact_registry_sha256,
        terminal_reason=terminal_reason,
    )


def _require_exact_table_columns(
    connection: sqlite3.Connection,
    table_name: str,
    expected_columns: tuple[str, ...],
) -> None:
    actual_columns = tuple(
        str(row["name"])
        for row in connection.execute(
            "PRAGMA table_info({0})".format(table_name)
        )
    )
    if actual_columns != expected_columns:
        raise PerformanceOperationIntegrityError(
            "Performance operation database schema is corrupt."
        )


def _domain_sha256(domain: bytes, payload: object) -> str:
    return hashlib.sha256(domain + canonical_json_bytes(payload)).hexdigest()


def _new_run_id() -> str:
    return "run_{0}".format(secrets.token_hex(16))


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _canonical_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(
        timespec="microseconds"
    ).replace("+00:00", "Z")


def _parse_canonical_timestamp(value: object, name: str) -> datetime:
    if (
        not isinstance(value, str)
        or not _CANONICAL_TIMESTAMP.fullmatch(value)
    ):
        raise ValueError(
            "{0} must be a canonical UTC timestamp.".format(name)
        )
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if (
        parsed.tzinfo is None
        or parsed.utcoffset() is None
        or parsed.utcoffset().total_seconds() != 0
        or _canonical_timestamp(parsed) != value
    ):
        raise ValueError(
            "{0} must be a canonical UTC timestamp.".format(name)
        )
    return parsed.astimezone(timezone.utc)


def _require_aware_datetime(value: object, name: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError("{0} must be a datetime.".format(name))
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("{0} must be timezone-aware.".format(name))
    return value.astimezone(timezone.utc)


def _require_safe_database_path(
    database_path: str | os.PathLike[str],
) -> Path:
    try:
        raw_path = os.fspath(database_path)
    except TypeError as exc:
        raise TypeError("database_path must be path-like.") from exc
    if not isinstance(raw_path, str) or not raw_path or "\x00" in raw_path:
        raise ValueError("database_path must be a non-empty filesystem path.")
    if raw_path == ":memory:" or raw_path.startswith("file:"):
        raise ValueError("database_path must identify a durable local file.")

    unexpanded = Path(raw_path)
    if not unexpanded.is_absolute():
        raise ValueError("database_path must be absolute.")
    if any(part in (".", "..") for part in unexpanded.parts):
        raise ValueError("database_path cannot contain traversal components.")
    if unexpanded.name in ("", ".", ".."):
        raise ValueError("database_path must identify a file.")

    parent = unexpanded.parent
    if not parent.exists() or not parent.is_dir():
        raise ValueError("database_path parent must be an existing directory.")
    if unexpanded.exists():
        if unexpanded.is_symlink():
            raise ValueError("database_path cannot be a symbolic link.")
        if not unexpanded.is_file():
            raise ValueError("database_path must identify a regular file.")

    resolved_parent = parent.resolve(strict=True)
    resolved_path = resolved_parent / unexpanded.name
    if resolved_path.parent != resolved_parent:
        raise ValueError("database_path escaped its parent directory.")
    return resolved_path


def _require_idempotency_key(value: object) -> None:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 200
        or value != value.strip()
        or any(
            ord(character) < 32 or ord(character) == 127
            for character in value
        )
    ):
        raise ValueError(
            "idempotency_key must be 1-200 printable characters with no "
            "surrounding whitespace."
        )


def _require_sha256(value: object, name: str) -> None:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ValueError(
            "{0} must be 64 lowercase hexadecimal characters.".format(name)
        )


def _require_run_id(value: object) -> None:
    if not isinstance(value, str) or not _RUN_ID.fullmatch(value):
        raise ValueError("run_id has an invalid shape.")


def _require_receipt_id_or_none(value: object) -> None:
    if value is not None and (
        not isinstance(value, str) or not _RECEIPT_ID.fullmatch(value)
    ):
        raise ValueError("receipt_id has an invalid shape.")


def _require_execution_id_or_none(value: object) -> None:
    if value is not None and (
        not isinstance(value, str) or not _RUN_ID.fullmatch(value)
    ):
        raise ValueError("execution_id has an invalid shape.")


def _require_sha256_or_none(value: object, name: str) -> None:
    if value is not None:
        _require_sha256(value, name)


def _require_terminal_reason_or_none(value: object) -> None:
    if value is not None and (
        not isinstance(value, str) or not _TERMINAL_REASON.fullmatch(value)
    ):
        raise ValueError(
            "terminal_reason must be a short non-sensitive reason code."
        )


def _require_adapter(value: object) -> None:
    if not isinstance(value, str) or not _ADAPTER.fullmatch(value):
        raise ValueError("adapter has an invalid shape.")


def _require_adapter_version(value: object) -> None:
    if (
        not isinstance(value, str)
        or not _ADAPTER_VERSION.fullmatch(value)
    ):
        raise ValueError("adapter_version has an invalid shape.")


def _require_terminal_status(
    value: PerformanceOperationStatus | str,
) -> PerformanceOperationStatus:
    try:
        status = PerformanceOperationStatus(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Unknown performance operation status.") from exc
    if status not in TERMINAL_OPERATION_STATUSES:
        raise ValueError("mark_terminal requires a terminal status.")
    return status
