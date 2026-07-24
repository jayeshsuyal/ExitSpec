"""Fail-closed bootstrap boundary for the durable confirmation ledger."""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime
from typing import Protocol, runtime_checkable

from .confirmation_schema import (
    CONFIRMATION_LEDGER_MIGRATIONS as _CONFIRMATION_LEDGER_MIGRATIONS,
    validate_confirmation_schema as _validate_confirmation_schema,
)
from .confirmation_sqlite import (
    DEFAULT_BUSY_TIMEOUT_MS as _DEFAULT_BUSY_TIMEOUT_MS,
    ConfirmationSQLiteError as _ConfirmationSQLiteError,
    LedgerUnavailable,
    _validate_busy_timeout,
    _validated_utc_datetime,
    apply_migrations as _apply_migrations,
    open_confirmation_database as _open_confirmation_database,
)


__all__ = (
    "GuardedConnection",
    "GuardedCursor",
    "LedgerUnavailable",
    "bootstrap_confirmation_ledger",
)


class GuardedCursor(Protocol):
    """Result cursor exposed by the hardened connection facade."""

    def fetchone(self) -> sqlite3.Row | None:
        """Return the next result row, when present."""

    def fetchall(self) -> list[sqlite3.Row]:
        """Return all remaining result rows."""

    def fetchmany(self, size: int | None = None) -> list[sqlite3.Row]:
        """Return the next result batch."""

    def close(self) -> None:
        """Close this result cursor."""

    def __iter__(self) -> GuardedCursor:
        """Iterate over remaining rows."""

    def __next__(self) -> sqlite3.Row:
        """Return the next result row."""


@runtime_checkable
class GuardedConnection(Protocol):
    """Usable type surface returned by the ledger bootstrap boundary."""

    def execute(
        self,
        sql: str,
        parameters: object = (),
    ) -> GuardedCursor:
        """Execute one guarded statement."""

    def blobopen(
        self,
        table: str,
        column: str,
        row: int,
        /,
        *,
        readonly: bool = False,
        name: str = "main",
    ) -> sqlite3.Blob:
        """Open a blob through the facade's read-only enforcement."""

    def rollback(self) -> None:
        """Roll back the current transaction."""

    def close(self) -> None:
        """Close the guarded connection."""

    @property
    def in_transaction(self) -> bool:
        """Whether a transaction is active."""

    @property
    def isolation_level(self) -> str | None:
        """The configured SQLite isolation level."""

    @property
    def row_factory(self) -> object:
        """The configured SQLite row factory."""


def bootstrap_confirmation_ledger(
    path: str | os.PathLike[str],
    *,
    migration_time: datetime,
    busy_timeout_ms: int = _DEFAULT_BUSY_TIMEOUT_MS,
) -> GuardedConnection:
    """Open, migrate, and verify one durable ledger before returning it.

    ``migration_time`` is validated before the database path is touched. Every
    known bootstrap failure is reduced to the existing non-sensitive
    ``LedgerUnavailable`` error, and an opened connection is never returned or
    left open unless migration and exact-schema validation both succeed. The
    caller owns and must close a returned connection.
    """

    try:
        validated_migration_time = _validated_utc_datetime(migration_time)
        _validate_busy_timeout(busy_timeout_ms)
    except ValueError:
        raise LedgerUnavailable() from None

    connection: GuardedConnection | None = None
    ready = False
    try:
        connection = _open_confirmation_database(
            path,
            busy_timeout_ms=busy_timeout_ms,
        )
        _apply_migrations(
            connection,
            _CONFIRMATION_LEDGER_MIGRATIONS,
            now=validated_migration_time,
        )
        _validate_confirmation_schema(connection)
        ready = True
        return connection
    except _ConfirmationSQLiteError:
        raise LedgerUnavailable() from None
    finally:
        if connection is not None and not ready:
            try:
                connection.close()
            except sqlite3.Error:
                pass
