from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone, tzinfo
import hashlib
import os
from pathlib import Path
import sqlite3
import stat
from threading import Barrier

import pytest

import exitspec.confirmation_sqlite as confirmation_sqlite_module
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

        for assignment in (
            "PRAGMA foreign_keys = OFF",
            "PRAGMA synchronous = OFF",
            "PRAGMA journal_mode = DELETE",
            "PRAGMA busy_timeout = 1",
        ):
            with pytest.raises(sqlite3.DatabaseError):
                connection.execute(assignment)

        assert connection.execute(
            "PRAGMA foreign_keys"
        ).fetchone()[0] == 1
        assert connection.execute(
            "PRAGMA synchronous"
        ).fetchone()[0] == 2
        assert connection.execute(
            "PRAGMA journal_mode"
        ).fetchone()[0].lower() == "wal"
        assert connection.execute(
            "PRAGMA busy_timeout"
        ).fetchone()[0] == 1_234
        assert connection.execute(
            "PRAGMA table_info(schema_migrations)"
        ).fetchall()
        assert connection.execute(
            "PRAGMA index_info(missing_index)"
        ).fetchall() == []
        assert connection.execute(
            "PRAGMA foreign_key_check(schema_migrations)"
        ).fetchall() == []
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


def test_existing_posix_database_is_normalized_to_owner_only(
    tmp_path: Path,
) -> None:
    if os.name != "posix":
        pytest.skip("POSIX custody permissions are unavailable.")
    database_path = tmp_path / "confirmation.db"
    database_path.touch(mode=0o644)
    database_path.chmod(0o644)

    connection = open_confirmation_database(database_path)
    try:
        assert stat.S_IMODE(database_path.stat().st_mode) == 0o600
    finally:
        connection.close()


def test_live_wal_and_shm_are_owner_only_on_posix(
    tmp_path: Path,
) -> None:
    if os.name != "posix":
        pytest.skip("POSIX custody permissions are unavailable.")
    database_path = tmp_path / "confirmation.db"
    connection = open_confirmation_database(database_path)
    try:
        sidecars = (
            Path("{0}-wal".format(database_path)),
            Path("{0}-shm".format(database_path)),
        )
        assert all(sidecar.exists() for sidecar in sidecars)
        assert all(
            stat.S_IMODE(sidecar.stat().st_mode) == 0o600
            for sidecar in sidecars
        )
    finally:
        connection.close()


def test_post_connect_identity_mismatch_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        confirmation_sqlite_module,
        "_database_identity_matches",
        lambda _path, _opened: False,
    )

    with pytest.raises(
        LedgerUnavailable,
        match="^Confirmation ledger is unavailable\\.$",
    ):
        open_confirmation_database(tmp_path / "confirmation.db")


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
        triggers = {
            row["name"]
            for row in connection.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'trigger'
                  AND tbl_name = 'schema_migrations'
                """
            ).fetchall()
        }
        assert triggers == {
            "schema_migrations_guard_insert",
            "schema_migrations_block_update",
            "schema_migrations_block_delete",
        }
        assert connection.execute(
            """
            SELECT COUNT(*)
            FROM sqlite_master
            WHERE type = 'index'
              AND tbl_name = 'schema_migrations'
            """
        ).fetchone()[0] == 0
        assert read_applied_migrations(connection) == ()
    finally:
        connection.close()


@pytest.mark.parametrize(
    "preexisting_sql",
    [
        """
        CREATE TABLE schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT,
            checksum TEXT,
            applied_at_us INTEGER
        )
        """,
        """
        CREATE TABLE schema_migrations (
            version INTEGER NOT NULL PRIMARY KEY,
            name TEXT NOT NULL,
            checksum TEXT NOT NULL,
            applied_at_us INTEGER NOT NULL
        )
        """,
        """
        CREATE VIEW schema_migrations AS
        SELECT
            1 AS version,
            'forged' AS name,
            'forged' AS checksum,
            0 AS applied_at_us
        """,
    ],
    ids=("loose-table", "changed-constraints", "view"),
)
def test_bootstrap_rejects_malformed_preexisting_primary_object(
    tmp_path: Path,
    preexisting_sql: str,
) -> None:
    database_path = tmp_path / "sensitive-customer-ledger.db"
    raw = sqlite3.connect(database_path)
    raw.execute(preexisting_sql)
    raw.close()

    with pytest.raises(
        LedgerUnavailable,
        match="^Confirmation ledger is unavailable\\.$",
    ) as caught:
        open_confirmation_database(database_path)

    assert os.fspath(database_path) not in str(caught.value)


@pytest.mark.parametrize(
    "tampering_sql",
    [
        "DROP TRIGGER schema_migrations_block_update",
        """
        DROP TRIGGER schema_migrations_block_update;
        CREATE TRIGGER schema_migrations_block_update
        AFTER UPDATE ON schema_migrations
        BEGIN
            SELECT RAISE(ABORT, 'modified');
        END;
        """,
        """
        CREATE TRIGGER schema_migrations_unexpected
        BEFORE UPDATE ON schema_migrations
        BEGIN
            SELECT RAISE(ABORT, 'unexpected');
        END
        """,
        """
        CREATE INDEX schema_migrations_unexpected_idx
        ON schema_migrations(name)
        """,
        """
        CREATE TABLE unrelated_events (id INTEGER);
        CREATE TRIGGER unrelated_history_mutation
        AFTER INSERT ON unrelated_events
        BEGIN
            DELETE FROM schema_migrations;
        END
        """,
    ],
    ids=(
        "missing-trigger",
        "modified-trigger",
        "extra-trigger",
        "extra-index",
        "cross-table-trigger",
    ),
)
def test_bootstrap_rejects_tampered_protection_objects(
    tmp_path: Path,
    tampering_sql: str,
) -> None:
    database_path = tmp_path / "confirmation.db"
    protected = open_confirmation_database(database_path)
    protected.close()

    raw = sqlite3.connect(database_path)
    raw.executescript(tampering_sql)
    raw.close()

    with pytest.raises(
        LedgerUnavailable,
        match="^Confirmation ledger is unavailable\\.$",
    ):
        open_confirmation_database(database_path)


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


def test_returned_connection_cannot_mutate_migration_history(
    tmp_path: Path,
) -> None:
    connection = open_confirmation_database(tmp_path / "confirmation.db")
    item = migration()
    apply_migrations(connection, (item,), now=NOW)
    original_history = read_applied_migrations(connection)
    forbidden_statements = (
        """
        INSERT INTO schema_migrations (
            version, name, checksum, applied_at_us
        ) VALUES (
            2, 'forged', '00000000000000000000000000000000'
            || '00000000000000000000000000000000', 0
        )
        """,
        "UPDATE schema_migrations SET name = 'forged' WHERE version = 1",
        "DELETE FROM schema_migrations WHERE version = 1",
        "DROP TABLE schema_migrations",
        "ALTER TABLE schema_migrations ADD COLUMN forged TEXT",
        "DROP TRIGGER schema_migrations_block_update",
        """
        CREATE TRIGGER schema_migrations_unexpected
        BEFORE UPDATE ON schema_migrations
        BEGIN
            SELECT 1;
        END
        """,
        """
        CREATE TRIGGER proof_runs_history_mutation
        AFTER INSERT ON proof_runs
        BEGIN
            DELETE FROM schema_migrations;
        END
        """,
    )

    try:
        for statement in forbidden_statements:
            with pytest.raises(sqlite3.DatabaseError):
                connection.execute(statement)
            if connection.in_transaction:
                connection.rollback()
            assert read_applied_migrations(connection) == original_history
            assert "proof_runs" in table_names(connection)

        trigger_names = {
            row["name"]
            for row in connection.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'trigger'
                  AND tbl_name = 'schema_migrations'
                """
            ).fetchall()
        }
        assert trigger_names == {
            "schema_migrations_guard_insert",
            "schema_migrations_block_update",
            "schema_migrations_block_delete",
        }
        with pytest.raises(sqlite3.OperationalError):
            connection.set_authorizer(None)
        with pytest.raises(sqlite3.OperationalError):
            connection.create_function(
                "exitspec_migration_insert_allowed",
                0,
                lambda: 1,
            )
    finally:
        connection.close()


def test_on_disk_triggers_reject_raw_insert_update_and_delete(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "confirmation.db"
    protected = open_confirmation_database(database_path)
    item = migration()
    apply_migrations(protected, (item,), now=NOW)
    protected.close()

    raw = sqlite3.connect(database_path, isolation_level=None)
    try:
        for statement in (
            "UPDATE schema_migrations SET name = 'forged'",
            "DELETE FROM schema_migrations",
            """
            INSERT INTO schema_migrations (
                version, name, checksum, applied_at_us
            ) VALUES (
                2, 'forged',
                '00000000000000000000000000000000'
                || '00000000000000000000000000000000',
                0
            )
            """,
        ):
            with pytest.raises(sqlite3.DatabaseError):
                raw.execute(statement)
    finally:
        raw.close()

    reopened = open_confirmation_database(database_path)
    try:
        assert read_applied_migrations(reopened) == (
            AppliedMigration(
                version=1,
                name=item.name,
                checksum=item.checksum,
                applied_at_us=1784917815123456,
            ),
        )
    finally:
        reopened.close()


def test_helpers_reject_arbitrary_raw_connections(
    tmp_path: Path,
) -> None:
    raw = sqlite3.connect(tmp_path / "raw.db")
    try:
        with pytest.raises(LedgerUnavailable):
            apply_migrations(raw, (), now=NOW)
        with pytest.raises(LedgerUnavailable):
            read_applied_migrations(raw)
    finally:
        raw.close()


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


def test_concurrent_apply_records_one_history_row(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "confirmation.db"
    item = migration()
    barrier = Barrier(2)
    initialized = open_confirmation_database(database_path)
    initialized.close()

    def apply_once() -> tuple[int, tuple[AppliedMigration, ...]]:
        connection = open_confirmation_database(
            database_path,
            busy_timeout_ms=5_000,
        )
        try:
            barrier.wait(timeout=5)
            version = apply_migrations(connection, (item,), now=NOW)
            return version, read_applied_migrations(connection)
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(lambda _index: apply_once(), range(2)))

    assert {result[0] for result in results} == {1}
    assert all(len(result[1]) == 1 for result in results)
    reopened = open_confirmation_database(database_path)
    try:
        assert len(read_applied_migrations(reopened)) == 1
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


def test_temp_schema_migration_rolls_back_without_history(
    tmp_path: Path,
) -> None:
    connection = open_confirmation_database(tmp_path / "confirmation.db")
    transient = migration(
        name="create_transient_state",
        sql="CREATE TEMP TABLE transient_state (id INTEGER)",
    )

    try:
        with pytest.raises(
            MigrationFailed,
            match="^Database migration failed\\.$",
        ):
            apply_migrations(connection, (transient,), now=NOW)

        assert connection.execute(
            """
            SELECT name
            FROM sqlite_temp_master
            WHERE name = 'transient_state'
            """
        ).fetchone() is None
        assert read_applied_migrations(connection) == ()
    finally:
        connection.close()


def test_migration_cannot_write_preexisting_temp_object(
    tmp_path: Path,
) -> None:
    connection = open_confirmation_database(tmp_path / "confirmation.db")
    connection.execute(
        "CREATE TEMP TABLE transient_state (id INTEGER)"
    )
    write_transient = migration(
        name="write_transient_state",
        sql="INSERT INTO temp.transient_state (id) VALUES (1)",
    )

    try:
        with pytest.raises(
            MigrationFailed,
            match="^Database migration failed\\.$",
        ):
            apply_migrations(connection, (write_transient,), now=NOW)

        assert connection.execute(
            "SELECT COUNT(*) FROM temp.transient_state"
        ).fetchone()[0] == 0
        assert read_applied_migrations(connection) == ()
    finally:
        connection.close()


def test_trigger_body_semicolons_are_split_as_one_migration_statement(
    tmp_path: Path,
) -> None:
    connection = open_confirmation_database(tmp_path / "confirmation.db")
    trigger_migration = migration(
        name="create_domain_trigger",
        sql="""
        CREATE TABLE source_events (id INTEGER PRIMARY KEY);
        CREATE TABLE copied_events (
            id INTEGER PRIMARY KEY,
            source_id INTEGER NOT NULL
        );
        CREATE TRIGGER copy_source_event
        AFTER INSERT ON source_events
        FOR EACH ROW
        BEGIN
            INSERT INTO copied_events (source_id) VALUES (NEW.id);
            INSERT INTO copied_events (source_id) VALUES (NEW.id);
        END;
        """,
    )

    try:
        assert apply_migrations(
            connection,
            (trigger_migration,),
            now=NOW,
        ) == 1
        connection.execute("INSERT INTO source_events (id) VALUES (7)")
        assert [
            row[0]
            for row in connection.execute(
                "SELECT source_id FROM copied_events ORDER BY id"
            ).fetchall()
        ] == [7, 7]
    finally:
        connection.close()


@pytest.mark.parametrize(
    "forbidden_sql",
    [
        "SELECT * FROM schema_migrations",
        """
        INSERT INTO schema_migrations (
            version, name, checksum, applied_at_us
        ) VALUES (
            2, 'forged',
            '00000000000000000000000000000000'
            || '00000000000000000000000000000000',
            0
        )
        """,
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
