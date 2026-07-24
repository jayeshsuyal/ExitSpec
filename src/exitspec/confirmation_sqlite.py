"""Hardened SQLite connection and forward-only migration primitives.

This module intentionally owns only database infrastructure. Domain ledger
tables and the ``ConfirmationStore`` adapter belong to later slices.
"""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import stat
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable


DEFAULT_BUSY_TIMEOUT_MS = 5_000
MAX_BUSY_TIMEOUT_MS = 60_000

_MACHINE_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_SHA256_DIGEST = re.compile(r"^[a-f0-9]{64}$")
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_SCHEMA_MIGRATIONS_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY CHECK (version > 0),
    name TEXT NOT NULL CHECK (
        length(name) BETWEEN 1 AND 64
        AND substr(name, 1, 1) GLOB '[a-z]'
        AND name NOT GLOB '*[^a-z0-9_]*'
    ),
    checksum TEXT NOT NULL CHECK (
        length(checksum) = 64
        AND checksum NOT GLOB '*[^a-f0-9]*'
    ),
    applied_at_us INTEGER NOT NULL CHECK (applied_at_us >= 0)
)
""".strip()


class ConfirmationSQLiteError(RuntimeError):
    """Base class for safe SQLite infrastructure failures."""


class InvalidMigrationPlan(ConfirmationSQLiteError):
    """The binary's migration plan is not an ordered contiguous sequence."""

    def __init__(self) -> None:
        super().__init__(
            "Migration plan must be ordered and contiguous from version 1."
        )


class MigrationChecksumMismatch(ConfirmationSQLiteError):
    """An applied migration's SQL differs from the binary's history."""

    def __init__(self) -> None:
        super().__init__("Applied migration checksum does not match.")


class MigrationHistoryMismatch(ConfirmationSQLiteError):
    """Applied migration metadata conflicts with the binary's history."""

    def __init__(self) -> None:
        super().__init__("Applied migration history does not match.")


class DatabaseNewerThanBinary(ConfirmationSQLiteError):
    """The database contains a migration unknown to this binary."""

    def __init__(self) -> None:
        super().__init__("Database schema is newer than this binary.")


class MigrationFailed(ConfirmationSQLiteError):
    """A pending migration did not commit atomically."""

    def __init__(self) -> None:
        super().__init__("Database migration failed.")


class LedgerUnavailable(ConfirmationSQLiteError):
    """The file-backed ledger cannot be opened or inspected safely."""

    def __init__(self) -> None:
        super().__init__("Confirmation ledger is unavailable.")


@dataclass(frozen=True, slots=True)
class Migration:
    """One immutable forward migration with a derived SQL checksum."""

    version: int
    name: str
    sql: str = field(repr=False)
    checksum: str = field(init=False)

    def __post_init__(self) -> None:
        if (
            isinstance(self.version, bool)
            or not isinstance(self.version, int)
            or self.version < 1
        ):
            raise ValueError("version must be a positive integer.")
        if (
            not isinstance(self.name, str)
            or not _MACHINE_IDENTIFIER.fullmatch(self.name)
        ):
            raise ValueError("name must be a valid machine identifier.")
        if not isinstance(self.sql, str) or not self.sql.strip():
            raise ValueError("sql must be non-empty.")
        try:
            encoded_sql = self.sql.encode("utf-8")
        except UnicodeEncodeError:
            raise ValueError("sql must be valid UTF-8 text.") from None
        object.__setattr__(
            self,
            "checksum",
            hashlib.sha256(encoded_sql).hexdigest(),
        )


@dataclass(frozen=True, slots=True)
class AppliedMigration:
    """A validated row from the infrastructure migration history."""

    version: int
    name: str
    checksum: str
    applied_at_us: int

    def __post_init__(self) -> None:
        if (
            isinstance(self.version, bool)
            or not isinstance(self.version, int)
            or self.version < 1
        ):
            raise ValueError("version must be a positive integer.")
        if (
            not isinstance(self.name, str)
            or not _MACHINE_IDENTIFIER.fullmatch(self.name)
        ):
            raise ValueError("name must be a valid machine identifier.")
        if (
            not isinstance(self.checksum, str)
            or not _SHA256_DIGEST.fullmatch(self.checksum)
        ):
            raise ValueError("checksum must be a lowercase SHA-256 digest.")
        if (
            isinstance(self.applied_at_us, bool)
            or not isinstance(self.applied_at_us, int)
            or self.applied_at_us < 0
        ):
            raise ValueError("applied_at_us must be a non-negative integer.")


def open_confirmation_database(
    path: str | os.PathLike[str],
    *,
    busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
) -> sqlite3.Connection:
    """Open and harden one file-backed confirmation database.

    The parent directory must already exist. A newly created database file is
    opened with owner-only permissions where the platform supports them.
    """

    _validate_busy_timeout(busy_timeout_ms)
    database_path = _validate_database_path(path)
    _ensure_database_file(database_path)

    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(
            os.fspath(database_path),
            timeout=busy_timeout_ms / 1_000,
            isolation_level=None,
            uri=False,
        )
        connection.row_factory = sqlite3.Row
        connection.execute(
            "PRAGMA busy_timeout = {0}".format(busy_timeout_ms)
        )
        connection.execute("PRAGMA foreign_keys = ON")
        journal_mode = connection.execute(
            "PRAGMA journal_mode = WAL"
        ).fetchone()[0]
        connection.execute("PRAGMA synchronous = FULL")

        if (
            str(journal_mode).lower() != "wal"
            or connection.execute(
                "PRAGMA foreign_keys"
            ).fetchone()[0]
            != 1
            or connection.execute(
                "PRAGMA synchronous"
            ).fetchone()[0]
            != 2
            or connection.execute(
                "PRAGMA busy_timeout"
            ).fetchone()[0]
            != busy_timeout_ms
        ):
            raise sqlite3.OperationalError

        _bootstrap_schema_migrations(connection)
        return connection
    except (OSError, sqlite3.Error, TypeError, ValueError):
        if connection is not None:
            try:
                connection.close()
            except sqlite3.Error:
                pass
        raise LedgerUnavailable() from None


def apply_migrations(
    connection: sqlite3.Connection,
    migrations: Iterable[Migration],
    *,
    now: datetime,
) -> int:
    """Validate history and atomically apply each pending migration.

    The returned integer is the latest applied version, or zero when the plan
    and database are both empty. Existing history is never updated or deleted.
    """

    if not isinstance(connection, sqlite3.Connection):
        raise LedgerUnavailable()
    plan = _validated_plan(migrations)
    applied_at_us = _utc_microseconds(now)

    while True:
        _begin_immediate(connection)
        try:
            history = _read_history(connection)
            _validate_history(history, plan)
            if len(history) == len(plan):
                _commit_or_raise(connection, migration_phase=False)
                return len(history)

            migration = plan[len(history)]
            _execute_migration_sql(connection, migration.sql)
            try:
                connection.execute(
                    """
                    INSERT INTO schema_migrations (
                        version,
                        name,
                        checksum,
                        applied_at_us
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        migration.version,
                        migration.name,
                        migration.checksum,
                        applied_at_us,
                    ),
                )
                connection.execute("COMMIT")
            except sqlite3.Error:
                _rollback_quietly(connection)
                raise MigrationFailed() from None
        except ConfirmationSQLiteError:
            _rollback_quietly(connection)
            raise
        except (sqlite3.Error, TypeError, ValueError):
            _rollback_quietly(connection)
            raise LedgerUnavailable() from None


def read_applied_migrations(
    connection: sqlite3.Connection,
) -> tuple[AppliedMigration, ...]:
    """Return the ordered infrastructure migration history."""

    if not isinstance(connection, sqlite3.Connection):
        raise LedgerUnavailable()
    try:
        return _read_history(connection)
    except (sqlite3.Error, TypeError, ValueError):
        raise LedgerUnavailable() from None


def _validate_busy_timeout(value: object) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 1 <= value <= MAX_BUSY_TIMEOUT_MS
    ):
        raise ValueError(
            "busy_timeout_ms must be an integer from 1 through 60000."
        )


def _validate_database_path(
    value: str | os.PathLike[str],
) -> Path:
    try:
        raw_path = os.fspath(value)
        if not isinstance(raw_path, str):
            raise TypeError
        if (
            not raw_path
            or raw_path == ":memory:"
            or raw_path.startswith("file:")
        ):
            raise ValueError
        database_path = Path(raw_path)
        parent_status = database_path.parent.stat()
        if not stat.S_ISDIR(parent_status.st_mode):
            raise OSError
        return database_path
    except (OSError, TypeError, ValueError):
        raise LedgerUnavailable() from None


def _ensure_database_file(database_path: Path) -> None:
    try:
        existing_status = database_path.lstat()
    except FileNotFoundError:
        flags = os.O_CREAT | os.O_EXCL | os.O_RDWR
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        try:
            descriptor = os.open(database_path, flags, 0o600)
        except FileExistsError:
            _require_regular_database_file(database_path)
            return
        except OSError:
            raise LedgerUnavailable() from None
        try:
            if hasattr(os, "fchmod"):
                os.fchmod(descriptor, 0o600)
        except OSError:
            raise LedgerUnavailable() from None
        finally:
            try:
                os.close(descriptor)
            except OSError:
                pass
        return
    except OSError:
        raise LedgerUnavailable() from None

    if not stat.S_ISREG(existing_status.st_mode):
        raise LedgerUnavailable()


def _require_regular_database_file(database_path: Path) -> None:
    try:
        existing_status = database_path.lstat()
    except OSError:
        raise LedgerUnavailable() from None
    if not stat.S_ISREG(existing_status.st_mode):
        raise LedgerUnavailable()


def _bootstrap_schema_migrations(
    connection: sqlite3.Connection,
) -> None:
    _begin_immediate(connection)
    try:
        connection.execute(_SCHEMA_MIGRATIONS_SQL)
        connection.execute("COMMIT")
    except sqlite3.Error:
        _rollback_quietly(connection)
        raise


def _validated_plan(
    migrations: Iterable[Migration],
) -> tuple[Migration, ...]:
    try:
        plan = tuple(migrations)
    except (TypeError, ValueError):
        raise InvalidMigrationPlan() from None
    expected_versions = tuple(range(1, len(plan) + 1))
    if (
        any(not isinstance(migration, Migration) for migration in plan)
        or tuple(migration.version for migration in plan)
        != expected_versions
    ):
        raise InvalidMigrationPlan()
    return plan


def _utc_microseconds(value: datetime) -> int:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("now must be timezone-aware UTC.")
    try:
        offset = value.utcoffset()
    except Exception:
        raise ValueError("now must be timezone-aware UTC.") from None
    if offset is None or offset != timedelta(0):
        raise ValueError("now must be timezone-aware UTC.")
    normalized = value.astimezone(timezone.utc)
    delta = normalized - _EPOCH
    if delta.days < 0:
        raise ValueError("now must not precede the Unix epoch.")
    return (
        delta.days * 86_400_000_000
        + delta.seconds * 1_000_000
        + delta.microseconds
    )


def _begin_immediate(connection: sqlite3.Connection) -> None:
    try:
        connection.execute("BEGIN IMMEDIATE")
    except sqlite3.Error:
        raise LedgerUnavailable() from None


def _read_history(
    connection: sqlite3.Connection,
) -> tuple[AppliedMigration, ...]:
    rows = connection.execute(
        """
        SELECT version, name, checksum, applied_at_us
        FROM schema_migrations
        ORDER BY version ASC
        """
    ).fetchall()
    try:
        return tuple(
            AppliedMigration(
                version=row["version"],
                name=row["name"],
                checksum=row["checksum"],
                applied_at_us=row["applied_at_us"],
            )
            for row in rows
        )
    except (KeyError, TypeError, ValueError):
        raise MigrationHistoryMismatch() from None


def _validate_history(
    history: tuple[AppliedMigration, ...],
    plan: tuple[Migration, ...],
) -> None:
    if history and history[-1].version > len(plan):
        raise DatabaseNewerThanBinary()
    for expected_version, applied in enumerate(history, start=1):
        if applied.version != expected_version:
            raise MigrationHistoryMismatch()
        expected = plan[expected_version - 1]
        if applied.name != expected.name:
            raise MigrationHistoryMismatch()
        if applied.checksum != expected.checksum:
            raise MigrationChecksumMismatch()


def _execute_migration_sql(
    connection: sqlite3.Connection,
    sql: str,
) -> None:
    try:
        connection.set_authorizer(_migration_authorizer)
        for statement in _split_statements(sql):
            connection.execute(statement)
    except (sqlite3.Error, TypeError, ValueError):
        raise MigrationFailed() from None
    finally:
        try:
            connection.set_authorizer(None)
        except sqlite3.Error:
            pass


def _split_statements(sql: str) -> tuple[str, ...]:
    statements: list[str] = []
    buffer: list[str] = []
    for character in sql:
        buffer.append(character)
        if (
            character == ";"
            and sqlite3.complete_statement("".join(buffer))
        ):
            statement = "".join(buffer).strip()
            if statement:
                statements.append(statement)
            buffer.clear()
    trailing = "".join(buffer).strip()
    if trailing:
        statements.append(trailing)
    return tuple(statements)


def _migration_authorizer(
    action: int,
    first: str | None,
    second: str | None,
    _database: str | None,
    _trigger: str | None,
) -> int:
    denied_actions = {
        sqlite3.SQLITE_ATTACH,
        sqlite3.SQLITE_DETACH,
        sqlite3.SQLITE_PRAGMA,
        sqlite3.SQLITE_TRANSACTION,
    }
    if hasattr(sqlite3, "SQLITE_SAVEPOINT"):
        denied_actions.add(sqlite3.SQLITE_SAVEPOINT)
    if action in denied_actions:
        return sqlite3.SQLITE_DENY

    protected_actions = {
        sqlite3.SQLITE_ALTER_TABLE,
        sqlite3.SQLITE_DELETE,
        sqlite3.SQLITE_DROP_TABLE,
        sqlite3.SQLITE_INSERT,
        sqlite3.SQLITE_UPDATE,
    }
    if action in protected_actions and (
        first == "schema_migrations"
        or second == "schema_migrations"
    ):
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


def _commit_or_raise(
    connection: sqlite3.Connection,
    *,
    migration_phase: bool,
) -> None:
    try:
        connection.execute("COMMIT")
    except sqlite3.Error:
        _rollback_quietly(connection)
        if migration_phase:
            raise MigrationFailed() from None
        raise LedgerUnavailable() from None


def _rollback_quietly(connection: sqlite3.Connection) -> None:
    try:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
    except sqlite3.Error:
        pass
