from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone, tzinfo
import hashlib
import os
from pathlib import Path
import sqlite3
import stat

import pytest

from exitspec.confirmation_sqlite import (
    AppliedMigration,
    DatabaseNewerThanBinary,
    InvalidMigrationPlan,
    LedgerUnavailable,
    Migration,
    MigrationChecksumMismatch,
    MigrationFailed,
    MigrationHistoryMismatch,
    apply_migrations,
    open_confirmation_database,
    read_applied_migrations,
)


NOW = datetime(2026, 7, 24, 18, 30, 15, 123456, tzinfo=timezone.utc)


def migration(
    version: int = 1,
    name: str = "create_proof_runs",
    sql: str = "CREATE TABLE proof_runs (id TEXT PRIMARY KEY)",
) -> Migration:
    return Migration(version=version, name=name, sql=sql)


def table_names(connection: sqlite3.Connection) -> set[str]:
    return {
        row[0]
        for row in connection.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table'
            """
        ).fetchall()
    }


def test_open_configures_hardened_connection_pragmas(tmp_path: Path) -> None:
    connection = open_confirmation_database(
        tmp_path / "confirmation.db",
        busy_timeout_ms=1_234,
    )
    try:
        assert connection.isolation_level is None
        assert connection.row_factory is sqlite3.Row
        assert connection.execute(
            "PRAGMA foreign_keys"
        ).fetchone()[0] == 1
        assert connection.execute(
            "PRAGMA journal_mode"
        ).fetchone()[0].lower() == "wal"
        assert connection.execute(
            "PRAGMA synchronous"
        ).fetchone()[0] == 2
        assert connection.execute(
            "PRAGMA busy_timeout"
        ).fetchone()[0] == 1_234
    finally:
        connection.close()


@pytest.mark.parametrize(
    "invalid_path",
    [
        ":memory:",
        "file:memory?mode=memory&cache=shared",
        "",
    ],
)
def test_open_rejects_non_file_database_paths(
    invalid_path: str,
) -> None:
    with pytest.raises(
        LedgerUnavailable,
        match="^Confirmation ledger is unavailable\\.$",
    ):
        open_confirmation_database(invalid_path)


def test_open_requires_existing_parent_directory(tmp_path: Path) -> None:
    database_path = tmp_path / "missing" / "confirmation.db"

    with pytest.raises(LedgerUnavailable):
        open_confirmation_database(database_path)

    assert not database_path.exists()


def test_open_rejects_directory_and_symlink_targets(
    tmp_path: Path,
) -> None:
    with pytest.raises(LedgerUnavailable):
        open_confirmation_database(tmp_path)

    target = tmp_path / "target.db"
    target.touch()
    link = tmp_path / "link.db"
    try:
        link.symlink_to(target)
    except (NotImplementedError, OSError):
        pytest.skip("Symbolic links are unavailable on this platform.")
    with pytest.raises(LedgerUnavailable):
        open_confirmation_database(link)


@pytest.mark.parametrize(
    "invalid_timeout",
    [True, False, 0, -1, 60_001, 1.5, "5000"],
)
def test_open_rejects_invalid_busy_timeout(
    tmp_path: Path,
    invalid_timeout: object,
) -> None:
    with pytest.raises(
        ValueError,
        match=(
            "^busy_timeout_ms must be an integer from 1 through 60000\\.$"
        ),
    ):
        open_confirmation_database(
            tmp_path / "confirmation.db",
            busy_timeout_ms=invalid_timeout,  # type: ignore[arg-type]
        )


def test_new_database_is_owner_only_where_supported(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "confirmation.db"
    connection = open_confirmation_database(database_path)
    connection.close()

    if os.name == "posix":
        assert stat.S_IMODE(database_path.stat().st_mode) == 0o600


def test_open_bootstraps_only_infrastructure_table(
    tmp_path: Path,
) -> None:
    connection = open_confirmation_database(tmp_path / "confirmation.db")
    try:
        assert table_names(connection) == {"schema_migrations"}
        columns = {
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info(schema_migrations)"
            ).fetchall()
        }
        assert columns == {
            "version",
            "name",
            "checksum",
            "applied_at_us",
        }
        assert read_applied_migrations(connection) == ()
    finally:
        connection.close()


def test_migration_is_frozen_and_derives_exact_utf8_checksum() -> None:
    sql = "CREATE TABLE exact_sql (id INTEGER);\n"
    item = Migration(version=1, name="exact_sql", sql=sql)

    assert item.checksum == hashlib.sha256(sql.encode("utf-8")).hexdigest()
    assert item.checksum != Migration(
        version=1,
        name="exact_sql",
        sql=sql.rstrip(),
    ).checksum
    assert sql not in repr(item)
    with pytest.raises(FrozenInstanceError):
        item.name = "changed"  # type: ignore[misc]


@pytest.mark.parametrize("invalid_version", [True, False, 0, -1, 1.5, "1"])
def test_migration_requires_positive_integer_version(
    invalid_version: object,
) -> None:
    with pytest.raises(
        ValueError,
        match="^version must be a positive integer\\.$",
    ):
        Migration(
            version=invalid_version,  # type: ignore[arg-type]
            name="valid_name",
            sql="SELECT 1",
        )


@pytest.mark.parametrize(
    "invalid_name",
    [
        "",
        "CreateTable",
        "1_starts_with_number",
        "contains-dash",
        "contains space",
        "a" * 65,
    ],
)
def test_migration_requires_strict_machine_name(
    invalid_name: str,
) -> None:
    with pytest.raises(
        ValueError,
        match="^name must be a valid machine identifier\\.$",
    ):
        Migration(version=1, name=invalid_name, sql="SELECT 1")


@pytest.mark.parametrize("invalid_sql", ["", "  \n\t"])
def test_migration_requires_nonempty_sql(invalid_sql: str) -> None:
    with pytest.raises(ValueError, match="^sql must be non-empty\\.$"):
        Migration(version=1, name="valid_name", sql=invalid_sql)


def test_checksum_cannot_be_supplied_by_caller() -> None:
    with pytest.raises(TypeError):
        Migration(
            version=1,
            name="valid_name",
            sql="SELECT 1",
            checksum="0" * 64,  # type: ignore[call-arg]
        )


@pytest.mark.parametrize(
    "plan",
    [
        (migration(version=2),),
        (
            migration(version=1),
            migration(version=3, name="third", sql="SELECT 3"),
        ),
        (
            migration(version=2),
            migration(version=1, name="first", sql="SELECT 1"),
        ),
        (object(),),
    ],
)
def test_apply_rejects_ordered_or_contiguous_plan_violations(
    tmp_path: Path,
    plan: tuple[object, ...],
) -> None:
    connection = open_confirmation_database(tmp_path / "confirmation.db")
    try:
        with pytest.raises(
            InvalidMigrationPlan,
            match=(
                "^Migration plan must be ordered and contiguous "
                "from version 1\\.$"
            ),
        ):
            apply_migrations(
                connection,
                plan,  # type: ignore[arg-type]
                now=NOW,
            )
        assert read_applied_migrations(connection) == ()
    finally:
        connection.close()


def test_apply_bootstraps_and_records_fresh_migrations_atomically(
    tmp_path: Path,
) -> None:
    first = migration()
    second = migration(
        version=2,
        name="add_proof_status",
        sql=(
            "ALTER TABLE proof_runs "
            "ADD COLUMN status TEXT NOT NULL DEFAULT 'draft';"
            "CREATE INDEX proof_runs_status_idx "
            "ON proof_runs(status);"
        ),
    )
    connection = open_confirmation_database(tmp_path / "confirmation.db")
    try:
        assert apply_migrations(
            connection,
            (first, second),
            now=NOW,
        ) == 2

        history = read_applied_migrations(connection)
        assert history == (
            AppliedMigration(
                version=1,
                name=first.name,
                checksum=first.checksum,
                applied_at_us=1784917815123456,
            ),
            AppliedMigration(
                version=2,
                name=second.name,
                checksum=second.checksum,
                applied_at_us=1784917815123456,
            ),
        )
        assert {
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info(proof_runs)"
            ).fetchall()
        } == {"id", "status"}
    finally:
        connection.close()


def test_reopen_and_reapply_is_idempotent(tmp_path: Path) -> None:
    database_path = tmp_path / "confirmation.db"
    item = migration()
    connection = open_confirmation_database(database_path)
    assert apply_migrations(connection, (item,), now=NOW) == 1
    first_history = read_applied_migrations(connection)
    connection.close()

    reopened = open_confirmation_database(database_path)
    try:
        assert apply_migrations(
            reopened,
            (item,),
            now=NOW + timedelta(days=1),
        ) == 1
        assert read_applied_migrations(reopened) == first_history
        assert table_names(reopened) == {
            "schema_migrations",
            "proof_runs",
        }
    finally:
        reopened.close()


def test_apply_detects_checksum_mismatch_without_rewriting_history(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "confirmation.db"
    original = migration()
    connection = open_confirmation_database(database_path)
    apply_migrations(connection, (original,), now=NOW)
    original_history = read_applied_migrations(connection)

    changed = migration(
        sql="CREATE TABLE proof_runs (id INTEGER PRIMARY KEY)",
    )
    with pytest.raises(
        MigrationChecksumMismatch,
        match="^Applied migration checksum does not match\\.$",
    ):
        apply_migrations(connection, (changed,), now=NOW)

    assert read_applied_migrations(connection) == original_history
    connection.close()


def test_apply_detects_name_mismatch_without_rewriting_history(
    tmp_path: Path,
) -> None:
    connection = open_confirmation_database(tmp_path / "confirmation.db")
    original = migration()
    apply_migrations(connection, (original,), now=NOW)
    original_history = read_applied_migrations(connection)

    renamed = migration(name="renamed_proof_runs")
    with pytest.raises(
        MigrationHistoryMismatch,
        match="^Applied migration history does not match\\.$",
    ):
        apply_migrations(connection, (renamed,), now=NOW)

    assert read_applied_migrations(connection) == original_history
    connection.close()


def test_apply_rejects_database_newer_than_binary(
    tmp_path: Path,
) -> None:
    first = migration()
    second = migration(
        version=2,
        name="second",
        sql="CREATE TABLE second_table (id INTEGER)",
    )
    connection = open_confirmation_database(tmp_path / "confirmation.db")
    apply_migrations(connection, (first, second), now=NOW)

    with pytest.raises(
        DatabaseNewerThanBinary,
        match="^Database schema is newer than this binary\\.$",
    ):
        apply_migrations(connection, (first,), now=NOW)

    assert len(read_applied_migrations(connection)) == 2
    connection.close()


def test_failed_sql_rolls_back_partial_objects_and_history(
    tmp_path: Path,
) -> None:
    connection = open_confirmation_database(tmp_path / "confirmation.db")
    broken = migration(
        name="broken",
        sql=(
            "CREATE TABLE partial_object (id INTEGER);"
            "INSERT INTO table_that_does_not_exist VALUES (1);"
        ),
    )

    with pytest.raises(
        MigrationFailed,
        match="^Database migration failed\\.$",
    ):
        apply_migrations(connection, (broken,), now=NOW)

    assert "partial_object" not in table_names(connection)
    assert read_applied_migrations(connection) == ()

    repaired = migration(
        name="repaired",
        sql="CREATE TABLE repaired_object (id INTEGER)",
    )
    assert apply_migrations(connection, (repaired,), now=NOW) == 1
    connection.close()


@pytest.mark.parametrize(
    "forbidden_sql",
    [
        "DELETE FROM schema_migrations",
        "DROP TABLE schema_migrations",
        "PRAGMA synchronous = OFF",
        "COMMIT",
        "ATTACH DATABASE ':memory:' AS other",
    ],
)
def test_migration_cannot_rewrite_history_or_transaction_controls(
    tmp_path: Path,
    forbidden_sql: str,
) -> None:
    connection = open_confirmation_database(tmp_path / "confirmation.db")
    item = migration(name="forbidden", sql=forbidden_sql)

    with pytest.raises(MigrationFailed):
        apply_migrations(connection, (item,), now=NOW)

    assert read_applied_migrations(connection) == ()
    assert "schema_migrations" in table_names(connection)
    connection.close()


class NonUtcTimezone(tzinfo):
    def utcoffset(self, _value: datetime | None) -> timedelta:
        return timedelta(hours=-7)

    def dst(self, _value: datetime | None) -> timedelta:
        return timedelta(0)


@pytest.mark.parametrize(
    "invalid_now",
    [
        datetime(2026, 7, 24, 18, 30),
        datetime(2026, 7, 24, 18, 30, tzinfo=NonUtcTimezone()),
        datetime(1969, 12, 31, 23, 59, tzinfo=timezone.utc),
    ],
)
def test_apply_requires_nonnegative_utc_transaction_time(
    tmp_path: Path,
    invalid_now: datetime,
) -> None:
    connection = open_confirmation_database(tmp_path / "confirmation.db")
    try:
        with pytest.raises(ValueError):
            apply_migrations(connection, (migration(),), now=invalid_now)
        assert read_applied_migrations(connection) == ()
        assert table_names(connection) == {"schema_migrations"}
    finally:
        connection.close()


def test_closed_connection_is_reported_as_safe_unavailability(
    tmp_path: Path,
) -> None:
    connection = open_confirmation_database(tmp_path / "confirmation.db")
    connection.close()

    with pytest.raises(
        LedgerUnavailable,
        match="^Confirmation ledger is unavailable\\.$",
    ):
        apply_migrations(connection, (), now=NOW)


def test_public_errors_have_fixed_non_sensitive_messages() -> None:
    expected_messages = {
        InvalidMigrationPlan:
            "Migration plan must be ordered and contiguous from version 1.",
        MigrationChecksumMismatch:
            "Applied migration checksum does not match.",
        MigrationHistoryMismatch:
            "Applied migration history does not match.",
        DatabaseNewerThanBinary:
            "Database schema is newer than this binary.",
        MigrationFailed:
            "Database migration failed.",
        LedgerUnavailable:
            "Confirmation ledger is unavailable.",
    }
    sensitive_values = (
        "CREATE TABLE secret",
        "/private/customer/ledger.db",
        "Bearer credential",
    )

    for error_type, expected_message in expected_messages.items():
        error = error_type()
        assert str(error) == expected_message
        assert repr(error) == "{0}({1!r})".format(
            error_type.__name__,
            expected_message,
        )
        assert all(value not in str(error) for value in sensitive_values)


def test_migration_failure_does_not_echo_sql_or_path(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "private-customer-ledger.db"
    connection = open_confirmation_database(database_path)
    secret_sql = "CREATE TABLE secret_name (id); INVALID SECRET SQL"

    with pytest.raises(MigrationFailed) as caught:
        apply_migrations(
            connection,
            (
                migration(
                    name="sensitive_failure",
                    sql=secret_sql,
                ),
            ),
            now=NOW,
        )

    rendered = "{0!r} {0}".format(caught.value)
    assert secret_sql not in rendered
    assert os.fspath(database_path) not in rendered
    connection.close()
