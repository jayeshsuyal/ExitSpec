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
_HISTORY_TABLE = "schema_migrations"
_HISTORY_INSERT_FUNCTION = "exitspec_migration_insert_allowed"
_SCHEMA_MIGRATIONS_SQL = """
CREATE TABLE schema_migrations (
    version INTEGER NOT NULL PRIMARY KEY CHECK (version > 0),
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
_INSERT_GUARD_TRIGGER = "schema_migrations_guard_insert"
_UPDATE_GUARD_TRIGGER = "schema_migrations_block_update"
_DELETE_GUARD_TRIGGER = "schema_migrations_block_delete"
_SCHEMA_MIGRATIONS_TRIGGER_SQL = {
    _INSERT_GUARD_TRIGGER: """
CREATE TRIGGER schema_migrations_guard_insert
BEFORE INSERT ON schema_migrations
FOR EACH ROW
WHEN exitspec_migration_insert_allowed() != 1
BEGIN
    SELECT RAISE(ABORT, 'schema_migrations is append-only');
END
""".strip(),
    _UPDATE_GUARD_TRIGGER: """
CREATE TRIGGER schema_migrations_block_update
BEFORE UPDATE ON schema_migrations
FOR EACH ROW
BEGIN
    SELECT RAISE(ABORT, 'schema_migrations is append-only');
END
""".strip(),
    _DELETE_GUARD_TRIGGER: """
CREATE TRIGGER schema_migrations_block_delete
BEFORE DELETE ON schema_migrations
FOR EACH ROW
BEGIN
    SELECT RAISE(ABORT, 'schema_migrations is append-only');
END
""".strip(),
}
_EXPECTED_HISTORY_COLUMNS = (
    ("version", "INTEGER", 1, None, 1, 0),
    ("name", "TEXT", 1, None, 0, 0),
    ("checksum", "TEXT", 1, None, 0, 0),
    ("applied_at_us", "INTEGER", 1, None, 0, 0),
)
_READ_ONLY_PRAGMAS = {
    "busy_timeout",
    "foreign_key_check",
    "foreign_key_list",
    "foreign_keys",
    "index_info",
    "index_list",
    "index_xinfo",
    "journal_mode",
    "quick_check",
    "synchronous",
    "table_info",
    "table_list",
    "table_xinfo",
}
_SIDECAR_SUFFIXES = ("-wal", "-shm")
_CONNECTION_GUARD = object()
_INTERNAL_AUTHORITY = object()
_CREATE_TRIGGER_ACTIONS = {
    sqlite3.SQLITE_CREATE_TRIGGER,
    getattr(sqlite3, "SQLITE_CREATE_TEMP_TRIGGER", -1),
}
_PROTECTED_HISTORY_ACTIONS = {
    sqlite3.SQLITE_ALTER_TABLE,
    sqlite3.SQLITE_CREATE_INDEX,
    sqlite3.SQLITE_CREATE_TABLE,
    sqlite3.SQLITE_CREATE_TRIGGER,
    sqlite3.SQLITE_CREATE_VIEW,
    sqlite3.SQLITE_DELETE,
    sqlite3.SQLITE_DROP_INDEX,
    sqlite3.SQLITE_DROP_TABLE,
    sqlite3.SQLITE_DROP_TRIGGER,
    sqlite3.SQLITE_DROP_VIEW,
    sqlite3.SQLITE_UPDATE,
    *(
        getattr(sqlite3, name, -1)
        for name in (
            "SQLITE_CREATE_TEMP_INDEX",
            "SQLITE_CREATE_TEMP_TABLE",
            "SQLITE_CREATE_TEMP_TRIGGER",
            "SQLITE_CREATE_TEMP_VIEW",
            "SQLITE_DROP_TEMP_INDEX",
            "SQLITE_DROP_TEMP_TABLE",
            "SQLITE_DROP_TEMP_TRIGGER",
            "SQLITE_DROP_TEMP_VIEW",
        )
    ),
}


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
class _OpenedDatabaseFile:
    descriptor: int
    device: int
    inode: int


class _ConfirmationConnection(sqlite3.Connection):
    """Private connection with a permanent append-only authorizer."""

    __slots__ = ("__guard_token", "__mode")

    def _initialize_security(self, authority: object) -> None:
        self.__require_authority(authority)
        self.__guard_token = _CONNECTION_GUARD
        self.__mode = "normal"
        sqlite3.Connection.create_function(
            self,
            _HISTORY_INSERT_FUNCTION,
            0,
            self.__history_insert_allowed,
        )
        sqlite3.Connection.set_authorizer(
            self,
            self.__authorize,
        )

    def _set_mode(self, mode: str, authority: object) -> None:
        self.__require_authority(authority)
        if mode not in {
            "bootstrap",
            "configuration",
            "history_insert",
            "migration",
            "normal",
        }:
            raise sqlite3.OperationalError("not authorized")
        self.__mode = mode

    def __require_authority(
        self,
        authority: object,
    ) -> None:
        if authority is not _INTERNAL_AUTHORITY:
            raise sqlite3.OperationalError("not authorized")

    def _is_guarded(self) -> bool:
        try:
            return self.__guard_token is _CONNECTION_GUARD
        except AttributeError:
            return False

    def __history_insert_allowed(self) -> int:
        return int(self.__mode == "history_insert")

    def __authorize(
        self,
        action: int,
        first: str | None,
        second: str | None,
        _database: str | None,
        _trigger: str | None,
    ) -> int:
        if self.__mode == "bootstrap":
            return sqlite3.SQLITE_OK

        if action == sqlite3.SQLITE_PRAGMA:
            pragma_name = (first or "").lower()
            if self.__mode == "configuration":
                return sqlite3.SQLITE_OK
            if (
                self.__mode == "normal"
                and pragma_name in _READ_ONLY_PRAGMAS
            ):
                return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_DENY

        if action in {
            sqlite3.SQLITE_ATTACH,
            sqlite3.SQLITE_DETACH,
        }:
            return sqlite3.SQLITE_DENY

        if self.__mode == "migration" and action in {
            sqlite3.SQLITE_TRANSACTION,
            getattr(sqlite3, "SQLITE_SAVEPOINT", -1),
        }:
            return sqlite3.SQLITE_DENY

        if (
            action in _CREATE_TRIGGER_ACTIONS
            and self.__mode != "migration"
        ):
            return sqlite3.SQLITE_DENY

        targets = {first, second}
        history_targeted = _HISTORY_TABLE in targets
        required_trigger_targeted = bool(
            targets.intersection(_SCHEMA_MIGRATIONS_TRIGGER_SQL)
        )

        if self.__mode == "migration" and history_targeted:
            return sqlite3.SQLITE_DENY

        if (
            action == sqlite3.SQLITE_INSERT
            and first == _HISTORY_TABLE
        ):
            if self.__mode == "history_insert":
                return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_DENY

        if action in _PROTECTED_HISTORY_ACTIONS and (
            history_targeted or required_trigger_targeted
        ):
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    def set_authorizer(self, _callback: object) -> None:
        raise sqlite3.OperationalError("not authorized")

    def create_function(
        self,
        name: str,
        narg: int,
        func: object,
        *,
        deterministic: bool = False,
    ) -> None:
        if name.lower() == _HISTORY_INSERT_FUNCTION:
            raise sqlite3.OperationalError("not authorized")
        sqlite3.Connection.create_function(
            self,
            name,
            narg,
            func,
            deterministic=deterministic,
        )


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

    The parent directory must already exist and is a trusted custody boundary.
    Database and live WAL/SHM files are normalized to owner-only permissions
    on POSIX. Descriptor and path identity are checked before returning.
    """

    _validate_busy_timeout(busy_timeout_ms)
    database_path = _validate_database_path(path)
    opened_file = _open_database_file(database_path)

    connection: _ConfirmationConnection | None = None
    try:
        _secure_existing_sidecars(database_path)
        connection = sqlite3.connect(
            os.fspath(database_path),
            timeout=busy_timeout_ms / 1_000,
            isolation_level=None,
            uri=False,
            factory=_ConfirmationConnection,
            cached_statements=0,
        )
        if not isinstance(connection, _ConfirmationConnection):
            raise sqlite3.OperationalError
        if not _database_identity_matches(database_path, opened_file):
            raise OSError
        connection.row_factory = sqlite3.Row
        connection._initialize_security(_INTERNAL_AUTHORITY)
        connection._set_mode("configuration", _INTERNAL_AUTHORITY)
        try:
            connection.execute(
                "PRAGMA busy_timeout = {0}".format(busy_timeout_ms)
            )
            connection.execute("PRAGMA foreign_keys = ON")
            journal_mode = connection.execute(
                "PRAGMA journal_mode = WAL"
            ).fetchone()[0]
            connection.execute("PRAGMA synchronous = FULL")
        finally:
            connection._set_mode("normal", _INTERNAL_AUTHORITY)

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
        _secure_existing_sidecars(database_path)
        if not _database_identity_matches(database_path, opened_file):
            raise OSError
        return connection
    except LedgerUnavailable:
        if connection is not None:
            try:
                connection.close()
            except sqlite3.Error:
                pass
        raise
    except (OSError, sqlite3.Error, TypeError, ValueError):
        if connection is not None:
            try:
                connection.close()
            except sqlite3.Error:
                pass
        raise LedgerUnavailable() from None
    finally:
        try:
            os.close(opened_file.descriptor)
        except OSError:
            pass


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

    guarded_connection = _require_guarded_connection(connection)
    plan = _validated_plan(migrations)
    applied_at_us = _utc_microseconds(now)

    while True:
        _begin_immediate(guarded_connection)
        try:
            history = _read_history(guarded_connection)
            _validate_history(history, plan)
            if len(history) == len(plan):
                _commit_or_raise(
                    guarded_connection,
                    migration_phase=False,
                )
                return len(history)

            migration = plan[len(history)]
            _execute_migration_sql(
                guarded_connection,
                migration.sql,
            )
            try:
                guarded_connection._set_mode(
                    "history_insert",
                    _INTERNAL_AUTHORITY,
                )
                try:
                    guarded_connection.execute(
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
                finally:
                    guarded_connection._set_mode(
                        "normal",
                        _INTERNAL_AUTHORITY,
                    )
                guarded_connection.execute("COMMIT")
            except sqlite3.Error:
                _rollback_quietly(guarded_connection)
                raise MigrationFailed() from None
        except ConfirmationSQLiteError:
            _rollback_quietly(guarded_connection)
            raise
        except (sqlite3.Error, TypeError, ValueError):
            _rollback_quietly(guarded_connection)
            raise LedgerUnavailable() from None


def read_applied_migrations(
    connection: sqlite3.Connection,
) -> tuple[AppliedMigration, ...]:
    """Return the ordered infrastructure migration history."""

    guarded_connection = _require_guarded_connection(connection)
    try:
        return _read_history(guarded_connection)
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


def _open_database_file(database_path: Path) -> _OpenedDatabaseFile:
    flags = os.O_RDWR
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW

    try:
        descriptor = os.open(
            database_path,
            flags | os.O_CREAT | os.O_EXCL,
            0o600,
        )
    except FileExistsError:
        try:
            descriptor = os.open(database_path, flags)
        except OSError:
            raise LedgerUnavailable() from None
    except OSError:
        raise LedgerUnavailable() from None

    try:
        opened_status = os.fstat(descriptor)
        if not stat.S_ISREG(opened_status.st_mode):
            raise OSError
        _enforce_owner_only(descriptor)
        secured_status = os.fstat(descriptor)
        if (
            not stat.S_ISREG(secured_status.st_mode)
            or (
                os.name == "posix"
                and stat.S_IMODE(secured_status.st_mode) != 0o600
            )
        ):
            raise OSError
        return _OpenedDatabaseFile(
            descriptor=descriptor,
            device=secured_status.st_dev,
            inode=secured_status.st_ino,
        )
    except OSError:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise LedgerUnavailable() from None


def _enforce_owner_only(descriptor: int) -> None:
    if os.name != "posix":
        return
    try:
        os.fchmod(descriptor, 0o600)
    except (AttributeError, OSError):
        raise OSError from None


def _database_identity_matches(
    database_path: Path,
    opened_file: _OpenedDatabaseFile,
) -> bool:
    try:
        descriptor_status = os.fstat(opened_file.descriptor)
        path_status = os.stat(database_path, follow_symlinks=False)
    except OSError:
        return False
    return (
        stat.S_ISREG(descriptor_status.st_mode)
        and stat.S_ISREG(path_status.st_mode)
        and descriptor_status.st_dev == opened_file.device
        and descriptor_status.st_ino == opened_file.inode
        and path_status.st_dev == opened_file.device
        and path_status.st_ino == opened_file.inode
        and (
            os.name != "posix"
            or (
                stat.S_IMODE(descriptor_status.st_mode) == 0o600
                and stat.S_IMODE(path_status.st_mode) == 0o600
            )
        )
    )


def _secure_existing_sidecars(database_path: Path) -> None:
    for suffix in _SIDECAR_SUFFIXES:
        sidecar_path = Path("{0}{1}".format(database_path, suffix))
        _secure_existing_sidecar(sidecar_path)


def _secure_existing_sidecar(sidecar_path: Path) -> None:
    flags = os.O_RDWR
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(sidecar_path, flags)
    except FileNotFoundError:
        return
    except OSError:
        raise LedgerUnavailable() from None

    try:
        initial_status = os.fstat(descriptor)
        if not stat.S_ISREG(initial_status.st_mode):
            raise OSError
        _enforce_owner_only(descriptor)
        descriptor_status = os.fstat(descriptor)
        path_status = os.stat(sidecar_path, follow_symlinks=False)
        if (
            not stat.S_ISREG(path_status.st_mode)
            or descriptor_status.st_dev != path_status.st_dev
            or descriptor_status.st_ino != path_status.st_ino
            or (
                os.name == "posix"
                and (
                    stat.S_IMODE(descriptor_status.st_mode) != 0o600
                    or stat.S_IMODE(path_status.st_mode) != 0o600
                )
            )
        ):
            raise OSError
    except FileNotFoundError:
        return
    except OSError:
        raise LedgerUnavailable() from None
    finally:
        try:
            os.close(descriptor)
        except OSError:
            pass


def _bootstrap_schema_migrations(
    connection: _ConfirmationConnection,
) -> None:
    _begin_immediate(connection)
    connection._set_mode("bootstrap", _INTERNAL_AUTHORITY)
    try:
        object_row = connection.execute(
            """
            SELECT type, name, tbl_name, sql
            FROM sqlite_master
            WHERE name = ?
            """,
            (_HISTORY_TABLE,),
        ).fetchone()
        if object_row is None:
            connection.execute(_SCHEMA_MIGRATIONS_SQL)
            for trigger_sql in _SCHEMA_MIGRATIONS_TRIGGER_SQL.values():
                connection.execute(trigger_sql)
        _validate_bootstrap_shape(connection)
        connection.execute("COMMIT")
    except (sqlite3.Error, TypeError, ValueError):
        _rollback_quietly(connection)
        raise LedgerUnavailable() from None
    finally:
        connection._set_mode("normal", _INTERNAL_AUTHORITY)


def _validate_bootstrap_shape(
    connection: _ConfirmationConnection,
) -> None:
    primary_objects = connection.execute(
        """
        SELECT type, name, tbl_name, sql
        FROM sqlite_master
        WHERE name = ?
        """,
        (_HISTORY_TABLE,),
    ).fetchall()
    if (
        len(primary_objects) != 1
        or primary_objects[0]["type"] != "table"
        or primary_objects[0]["name"] != _HISTORY_TABLE
        or primary_objects[0]["tbl_name"] != _HISTORY_TABLE
        or primary_objects[0]["sql"] != _SCHEMA_MIGRATIONS_SQL
    ):
        raise ValueError

    columns = connection.execute(
        "PRAGMA table_xinfo(schema_migrations)"
    ).fetchall()
    actual_columns = tuple(
        (
            row["name"],
            row["type"],
            row["notnull"],
            row["dflt_value"],
            row["pk"],
            row["hidden"],
        )
        for row in columns
    )
    if actual_columns != _EXPECTED_HISTORY_COLUMNS:
        raise ValueError

    related_objects = connection.execute(
        """
        SELECT type, name, tbl_name, sql
        FROM sqlite_master
        WHERE tbl_name = ?
          AND type IN ('index', 'trigger')
        ORDER BY type, name
        """,
        (_HISTORY_TABLE,),
    ).fetchall()
    indexes = [
        row for row in related_objects if row["type"] == "index"
    ]
    triggers = {
        row["name"]: row["sql"]
        for row in related_objects
        if row["type"] == "trigger"
    }
    if indexes or triggers != _SCHEMA_MIGRATIONS_TRIGGER_SQL:
        raise ValueError

    all_trigger_rows = connection.execute(
        """
        SELECT name, sql
        FROM sqlite_master
        WHERE type = 'trigger'
        """
    ).fetchall()
    for row in all_trigger_rows:
        trigger_sql = row["sql"]
        if (
            row["name"] not in _SCHEMA_MIGRATIONS_TRIGGER_SQL
            and isinstance(trigger_sql, str)
            and re.search(
                r"\bschema_migrations\b",
                trigger_sql,
                flags=re.IGNORECASE,
            )
        ):
            raise ValueError


def _require_guarded_connection(
    connection: sqlite3.Connection,
) -> _ConfirmationConnection:
    if (
        type(connection) is not _ConfirmationConnection
        or not connection._is_guarded()
    ):
        raise LedgerUnavailable()
    return connection


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


def _begin_immediate(connection: _ConfirmationConnection) -> None:
    try:
        connection.execute("BEGIN IMMEDIATE")
    except sqlite3.Error:
        raise LedgerUnavailable() from None


def _read_history(
    connection: _ConfirmationConnection,
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
    connection: _ConfirmationConnection,
    sql: str,
) -> None:
    connection._set_mode("migration", _INTERNAL_AUTHORITY)
    try:
        for statement in _split_statements(sql):
            if re.search(
                r"\bschema_migrations\b",
                statement,
                flags=re.IGNORECASE,
            ):
                raise MigrationFailed()
            connection.execute(statement)
    except (sqlite3.Error, TypeError, ValueError):
        raise MigrationFailed() from None
    finally:
        connection._set_mode("normal", _INTERNAL_AUTHORITY)


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


def _commit_or_raise(
    connection: _ConfirmationConnection,
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


def _rollback_quietly(connection: _ConfirmationConnection) -> None:
    try:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
    except sqlite3.Error:
        pass
