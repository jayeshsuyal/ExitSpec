from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import sqlite3
import stat
from typing import Any

import pytest

import exitspec.confirmation_ledger as confirmation_ledger_module
from exitspec.confirmation_ledger import (
    GuardedConnection,
    LedgerUnavailable,
    bootstrap_confirmation_ledger,
    open_existing_confirmation_ledger,
)
from exitspec.confirmation_schema import (
    CONFIRMATION_LEDGER_MIGRATION,
    validate_confirmation_schema,
)
from exitspec.confirmation_sqlite import (
    AppliedMigration,
    MigrationFailed,
    open_confirmation_database,
    read_applied_migrations,
)


MIGRATION_TIME = datetime(
    2026,
    7,
    24,
    22,
    15,
    30,
    123456,
    tzinfo=timezone.utc,
)


def _epoch_microseconds(value: datetime) -> int:
    delta = value - datetime(1970, 1, 1, tzinfo=timezone.utc)
    return (
        delta.days * 86_400_000_000
        + delta.seconds * 1_000_000
        + delta.microseconds
    )


def _assert_connection_closed(connection: Any) -> None:
    with pytest.raises(sqlite3.ProgrammingError):
        connection.execute("SELECT 1")


def test_fresh_bootstrap_returns_only_an_exact_migrated_ledger(
    tmp_path: Path,
) -> None:
    connection = bootstrap_confirmation_ledger(
        tmp_path / "confirmation.db",
        migration_time=MIGRATION_TIME,
    )
    try:
        assert isinstance(connection, GuardedConnection)
        assert read_applied_migrations(connection) == (
            AppliedMigration(
                version=CONFIRMATION_LEDGER_MIGRATION.version,
                name=CONFIRMATION_LEDGER_MIGRATION.name,
                checksum=CONFIRMATION_LEDGER_MIGRATION.checksum,
                applied_at_us=_epoch_microseconds(MIGRATION_TIME),
            ),
        )
        validate_confirmation_schema(connection)
    finally:
        connection.close()


def test_bootstrap_reopen_is_idempotent_and_preserves_original_history(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "confirmation.db"
    first = bootstrap_confirmation_ledger(
        database_path,
        migration_time=MIGRATION_TIME,
    )
    expected_history = read_applied_migrations(first)
    first.close()

    reopened = bootstrap_confirmation_ledger(
        database_path,
        migration_time=datetime(
            2026,
            7,
            25,
            tzinfo=timezone.utc,
        ),
    )
    try:
        assert read_applied_migrations(reopened) == expected_history
        validate_confirmation_schema(reopened)
    finally:
        reopened.close()


def test_existing_only_open_accepts_exact_current_ledger(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "confirmation.db"
    initialized = bootstrap_confirmation_ledger(
        database_path,
        migration_time=MIGRATION_TIME,
    )
    expected_history = read_applied_migrations(initialized)
    initialized.close()

    reopened = open_existing_confirmation_ledger(database_path)
    try:
        assert reopened.in_transaction is False
        assert read_applied_migrations(reopened) == expected_history
        validate_confirmation_schema(reopened)
    finally:
        reopened.close()


def test_existing_only_open_rejects_unmigrated_database_without_migrating(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "confirmation.db"
    infrastructure_only = open_confirmation_database(database_path)
    assert read_applied_migrations(infrastructure_only) == ()
    infrastructure_only.close()

    with pytest.raises(
        LedgerUnavailable,
        match="^Confirmation ledger is unavailable\\.$",
    ) as error:
        open_existing_confirmation_ledger(database_path)

    assert type(error.value) is LedgerUnavailable
    assert error.value.__cause__ is None
    raw = sqlite3.connect(database_path)
    try:
        assert raw.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table'
            ORDER BY name
            """
        ).fetchall() == [("schema_migrations",)]
        assert raw.execute(
            "SELECT version FROM schema_migrations"
        ).fetchall() == []
    finally:
        raw.close()


@pytest.mark.parametrize(
    "invalid_migration_time",
    (
        datetime(2026, 7, 24, 22, 15),
        datetime(
            2026,
            7,
            24,
            22,
            15,
            tzinfo=timezone(timedelta(hours=1)),
        ),
        "2026-07-24T22:15:00Z",
        datetime(1969, 12, 31, 23, 59, tzinfo=timezone.utc),
    ),
    ids=("naive", "non-utc", "not-datetime", "before-epoch"),
)
def test_bootstrap_rejects_invalid_migration_time_with_safe_error(
    tmp_path: Path,
    invalid_migration_time: object,
) -> None:
    database_path = tmp_path / "confirmation.db"
    assert not database_path.exists()

    with pytest.raises(
        LedgerUnavailable,
        match="^Confirmation ledger is unavailable\\.$",
    ) as error:
        bootstrap_confirmation_ledger(
            database_path,
            migration_time=invalid_migration_time,  # type: ignore[arg-type]
        )

    assert type(error.value) is LedgerUnavailable
    assert error.value.__cause__ is None
    assert not database_path.exists()
    assert list(tmp_path.iterdir()) == []


def test_invalid_migration_time_does_not_touch_existing_database_state(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "confirmation.db"
    raw = sqlite3.connect(database_path)
    try:
        raw.execute("CREATE TABLE sentinel (value TEXT NOT NULL)")
        raw.execute("INSERT INTO sentinel (value) VALUES ('unchanged')")
        raw.commit()
    finally:
        raw.close()

    if os.name == "posix":
        database_path.chmod(0o640)
    before_bytes = database_path.read_bytes()
    before_status = database_path.stat()
    before_metadata = (
        stat.S_IMODE(before_status.st_mode),
        before_status.st_size,
        before_status.st_mtime_ns,
        before_status.st_ctime_ns,
    )
    before_children = tuple(
        sorted(path.name for path in tmp_path.iterdir())
    )

    with pytest.raises(LedgerUnavailable):
        bootstrap_confirmation_ledger(
            database_path,
            migration_time=datetime(2026, 7, 24, 22, 15),
        )

    after_status = database_path.stat()
    assert database_path.read_bytes() == before_bytes
    assert (
        stat.S_IMODE(after_status.st_mode),
        after_status.st_size,
        after_status.st_mtime_ns,
        after_status.st_ctime_ns,
    ) == before_metadata
    assert tuple(
        sorted(path.name for path in tmp_path.iterdir())
    ) == before_children

    raw = sqlite3.connect(database_path)
    try:
        assert raw.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall() == [("sentinel",)]
        assert raw.execute("SELECT value FROM sentinel").fetchall() == [
            ("unchanged",),
        ]
    finally:
        raw.close()


def test_bootstrap_rejects_tampered_schema_and_closes_connection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_path = tmp_path / "confirmation.db"
    created = bootstrap_confirmation_ledger(
        database_path,
        migration_time=MIGRATION_TIME,
    )
    created.close()

    raw = sqlite3.connect(database_path)
    try:
        raw.execute("DROP INDEX review_invitations_expiry_idx")
        raw.commit()
    finally:
        raw.close()

    opened: list[Any] = []
    original_open = confirmation_ledger_module._open_confirmation_database

    def tracking_open(*args: object, **kwargs: object) -> Any:
        connection = original_open(*args, **kwargs)
        opened.append(connection)
        return connection

    monkeypatch.setattr(
        confirmation_ledger_module,
        "_open_confirmation_database",
        tracking_open,
    )

    with pytest.raises(
        LedgerUnavailable,
        match="^Confirmation ledger is unavailable\\.$",
    ):
        bootstrap_confirmation_ledger(
            database_path,
            migration_time=MIGRATION_TIME,
        )

    assert len(opened) == 1
    _assert_connection_closed(opened[0])


def test_bootstrap_closes_connection_and_sanitizes_migration_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened: list[Any] = []
    original_open = confirmation_ledger_module._open_confirmation_database

    def tracking_open(*args: object, **kwargs: object) -> Any:
        connection = original_open(*args, **kwargs)
        opened.append(connection)
        return connection

    def fail_migration(*_args: object, **_kwargs: object) -> None:
        raise MigrationFailed()

    monkeypatch.setattr(
        confirmation_ledger_module,
        "_open_confirmation_database",
        tracking_open,
    )
    monkeypatch.setattr(
        confirmation_ledger_module,
        "_apply_migrations",
        fail_migration,
    )

    with pytest.raises(
        LedgerUnavailable,
        match="^Confirmation ledger is unavailable\\.$",
    ) as error:
        bootstrap_confirmation_ledger(
            tmp_path / "confirmation.db",
            migration_time=MIGRATION_TIME,
        )

    assert type(error.value) is LedgerUnavailable
    assert error.value.__cause__ is None
    assert len(opened) == 1
    _assert_connection_closed(opened[0])


def test_unexpected_programming_error_propagates_after_connection_closes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened: list[Any] = []
    original_open = confirmation_ledger_module._open_confirmation_database

    def tracking_open(*args: object, **kwargs: object) -> Any:
        connection = original_open(*args, **kwargs)
        opened.append(connection)
        return connection

    def fail_validation(_connection: object) -> None:
        raise RuntimeError("validator programming defect")

    monkeypatch.setattr(
        confirmation_ledger_module,
        "_open_confirmation_database",
        tracking_open,
    )
    monkeypatch.setattr(
        confirmation_ledger_module,
        "_validate_confirmation_schema",
        fail_validation,
    )

    with pytest.raises(
        RuntimeError,
        match="^validator programming defect$",
    ):
        bootstrap_confirmation_ledger(
            tmp_path / "confirmation.db",
            migration_time=MIGRATION_TIME,
        )

    assert len(opened) == 1
    _assert_connection_closed(opened[0])


def test_existing_open_disposes_connection_when_cleanup_rollback_defects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_path = tmp_path / "confirmation.db"
    initialized = bootstrap_confirmation_ledger(
        database_path,
        migration_time=MIGRATION_TIME,
    )
    guarded_type = type(initialized)
    initialized.close()

    opened: list[Any] = []
    original_open = (
        confirmation_ledger_module._open_existing_confirmation_database
    )

    def tracking_open(*args: object, **kwargs: object) -> Any:
        connection = original_open(*args, **kwargs)
        opened.append(connection)
        return connection

    def fail_validation(_connection: object) -> None:
        raise LedgerUnavailable()

    def fail_rollback(_connection: object) -> None:
        raise RuntimeError("cleanup rollback programming defect")

    monkeypatch.setattr(
        confirmation_ledger_module,
        "_open_existing_confirmation_database",
        tracking_open,
    )
    monkeypatch.setattr(
        confirmation_ledger_module,
        "_validate_confirmation_schema",
        fail_validation,
    )
    monkeypatch.setattr(
        guarded_type,
        "rollback",
        fail_rollback,
    )

    with pytest.raises(
        RuntimeError,
        match="^cleanup rollback programming defect$",
    ):
        open_existing_confirmation_ledger(database_path)

    assert len(opened) == 1
    _assert_connection_closed(opened[0])
