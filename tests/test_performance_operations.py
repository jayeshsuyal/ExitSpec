from __future__ import annotations

import hashlib
import multiprocessing
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from exitspec.canonical import canonical_json_bytes
from exitspec.performance_operations import (
    PERFORMANCE_OPERATION_SCHEMA_NAME,
    PERFORMANCE_OPERATION_SCHEMA_VERSION,
    PerformanceOperationConflict,
    PerformanceOperationIntegrityError,
    PerformanceOperationStatus,
    PerformanceOperationTransitionError,
    SQLitePerformanceOperationLedger,
    performance_operation_idempotency_key_digest,
    performance_operation_input_digest,
)


CREATED_AT = datetime(
    2026,
    7,
    28,
    10,
    30,
    45,
    123456,
    tzinfo=timezone.utc,
)
TERMINAL_AT = CREATED_AT + timedelta(minutes=5)
RECEIPT_ID = "prc_{0}".format("a" * 64)
EXECUTION_ID = "run_{0}".format("b" * 32)
REGISTRY_SHA256 = "c" * 64

DEFAULT_INPUTS = {
    "frozen_contract_hash": "1" * 64,
    "confirmation_hash": "2" * 64,
    "expected_manifest_hash": "3" * 64,
    "workload_hash": "4" * 64,
    "adapter": "vllm_latency",
    "adapter_version": "1.0",
}


def make_ledger(
    tmp_path: Path,
    *,
    database_name: str = "performance-operations.sqlite3",
    clock=None,
    run_id_factory=None,
) -> SQLitePerformanceOperationLedger:
    return SQLitePerformanceOperationLedger(
        tmp_path / database_name,
        clock=clock,
        run_id_factory=run_id_factory,
    )


def reserve(
    ledger: SQLitePerformanceOperationLedger,
    *,
    idempotency_key: str = "latency-demo-001",
    **overrides,
):
    inputs = {**DEFAULT_INPUTS, **overrides}
    return ledger.reserve(
        idempotency_key=idempotency_key,
        created_at=CREATED_AT,
        **inputs,
    )


def _process_reserve(database_path: str, output_queue) -> None:
    try:
        ledger = SQLitePerformanceOperationLedger(database_path)
        result = ledger.reserve(
            idempotency_key="shared-process-key",
            **DEFAULT_INPUTS,
        )
        output_queue.put(
            ("ok", result.created, result.operation.run_id)
        )
    except Exception as exc:  # pragma: no cover - failure is asserted by parent
        output_queue.put(("error", type(exc).__name__, str(exc)))


def test_reservation_is_persisted_before_execution_and_contains_no_raw_key(
    tmp_path,
):
    secret_key = "secret-operation-key-that-must-not-be-stored"
    ledger = make_ledger(tmp_path)

    result = reserve(ledger, idempotency_key=secret_key)

    assert result.created is True
    assert result.should_execute is True
    assert result.operation.status is PerformanceOperationStatus.RUNNING
    assert result.operation.receipt_id is None
    assert result.operation.terminal_reason is None
    assert result.operation.created_at == CREATED_AT
    assert result.operation.updated_at == CREATED_AT
    assert secret_key not in repr(result)
    assert secret_key not in repr(ledger)

    connection = sqlite3.connect(ledger.database_path)
    try:
        row = connection.execute(
            """
            SELECT idempotency_key_digest, input_digest, run_id, status,
                   created_at, updated_at, execution_id, receipt_id,
                   artifact_registry_sha256, terminal_reason
              FROM performance_operations
            """
        ).fetchone()
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        connection.close()

    assert row == (
        result.operation.idempotency_key_digest,
        result.operation.input_digest,
        result.operation.run_id,
        "RUNNING",
        "2026-07-28T10:30:45.123456Z",
        "2026-07-28T10:30:45.123456Z",
        None,
        None,
        None,
        None,
    )
    for candidate in tmp_path.iterdir():
        if candidate.is_file():
            assert secret_key.encode() not in candidate.read_bytes()


def test_digests_are_domain_separated_rfc8785_hashes():
    key_payload = {"idempotency_key": "latency-demo-001"}
    expected_key_digest = hashlib.sha256(
        b"exitspec-performance-operation-idempotency-key-v1\x00"
        + canonical_json_bytes(key_payload)
    ).hexdigest()
    assert (
        performance_operation_idempotency_key_digest("latency-demo-001")
        == expected_key_digest
    )

    input_payload = {
        "adapter": "vllm_latency",
        "adapter_version": "1.0",
        "confirmation_hash": "2" * 64,
        "expected_manifest_hash": "3" * 64,
        "frozen_contract_hash": "1" * 64,
        "schema_version": 1,
        "workload_hash": "4" * 64,
    }
    expected_input_digest = hashlib.sha256(
        b"exitspec-performance-operation-input-v2\x00"
        + canonical_json_bytes(input_payload)
    ).hexdigest()
    assert (
        performance_operation_input_digest(**DEFAULT_INPUTS)
        == expected_input_digest
    )
    assert expected_input_digest != expected_key_digest


def test_same_key_and_same_inputs_replays_running_without_execution(tmp_path):
    ledger = make_ledger(tmp_path)

    first = reserve(ledger)
    replay = reserve(ledger)

    assert first.created is True
    assert replay.created is False
    assert replay.should_execute is False
    assert replay.operation == first.operation


@pytest.mark.parametrize(
    ("field", "different_value"),
    [
        ("frozen_contract_hash", "5" * 64),
        ("confirmation_hash", "6" * 64),
        ("expected_manifest_hash", "7" * 64),
        ("workload_hash", "8" * 64),
        ("adapter", "openai_latency"),
        ("adapter_version", "2.0"),
    ],
)
def test_same_key_with_any_changed_input_conflicts(
    tmp_path,
    field,
    different_value,
):
    ledger = make_ledger(tmp_path)
    reserve(ledger)

    with pytest.raises(
        PerformanceOperationConflict,
        match="different inputs",
    ):
        reserve(ledger, **{field: different_value})


def test_conflict_does_not_echo_raw_key(tmp_path):
    raw_key = "do-not-echo-this-idempotency-key"
    ledger = make_ledger(tmp_path)
    reserve(ledger, idempotency_key=raw_key)

    with pytest.raises(PerformanceOperationConflict) as captured:
        reserve(
            ledger,
            idempotency_key=raw_key,
            workload_hash="9" * 64,
        )

    assert raw_key not in str(captured.value)


def test_different_keys_create_independent_runs(tmp_path):
    ledger = make_ledger(tmp_path)

    first = reserve(ledger, idempotency_key="latency-demo-001")
    second = reserve(ledger, idempotency_key="latency-demo-002")

    assert first.created is True
    assert second.created is True
    assert second.operation.run_id != first.operation.run_id
    assert (
        second.operation.idempotency_key_digest
        != first.operation.idempotency_key_digest
    )
    assert second.operation.input_digest == first.operation.input_digest


@pytest.mark.parametrize(
    "status",
    [
        PerformanceOperationStatus.COMPLETED,
        PerformanceOperationStatus.BLOCKED,
        PerformanceOperationStatus.NOT_PROVEN,
        PerformanceOperationStatus.FAILED,
    ],
)
def test_running_operation_can_transition_once_to_each_terminal_status(
    tmp_path,
    status,
):
    ledger = make_ledger(tmp_path)
    reservation = reserve(ledger, idempotency_key="key-{0}".format(status))
    receipt_id = (
        RECEIPT_ID
        if status is PerformanceOperationStatus.COMPLETED
        else None
    )
    execution_id = (
        EXECUTION_ID
        if status is PerformanceOperationStatus.COMPLETED
        else None
    )
    registry_sha256 = (
        REGISTRY_SHA256
        if status is PerformanceOperationStatus.COMPLETED
        else None
    )
    reason = (
        None
        if status is PerformanceOperationStatus.COMPLETED
        else "SAFE_TERMINAL_REASON"
    )

    terminal = ledger.mark_terminal(
        run_id=reservation.operation.run_id,
        input_digest=reservation.operation.input_digest,
        status=status,
        execution_id=execution_id,
        receipt_id=receipt_id,
        artifact_registry_sha256=registry_sha256,
        terminal_reason=reason,
        updated_at=TERMINAL_AT,
    )

    assert terminal.status is status
    assert terminal.is_terminal is True
    assert terminal.execution_id == execution_id
    assert terminal.receipt_id == receipt_id
    assert terminal.artifact_registry_sha256 == registry_sha256
    assert terminal.terminal_reason == reason
    assert terminal.updated_at == TERMINAL_AT


def test_terminal_operation_cannot_transition_or_reopen(tmp_path):
    ledger = make_ledger(tmp_path)
    reservation = reserve(ledger)
    terminal = ledger.mark_terminal(
        run_id=reservation.operation.run_id,
        input_digest=reservation.operation.input_digest,
        status=PerformanceOperationStatus.COMPLETED,
        execution_id=EXECUTION_ID,
        receipt_id=RECEIPT_ID,
        artifact_registry_sha256=REGISTRY_SHA256,
        updated_at=TERMINAL_AT,
    )

    with pytest.raises(
        PerformanceOperationTransitionError,
        match="cannot be reopened",
    ):
        ledger.mark_terminal(
            run_id=terminal.run_id,
            input_digest=terminal.input_digest,
            status=PerformanceOperationStatus.FAILED,
            terminal_reason="LATE_FAILURE",
            updated_at=TERMINAL_AT + timedelta(minutes=1),
        )

    replay = reserve(ledger)
    assert replay.created is False
    assert replay.operation == terminal
    assert replay.should_execute is False


@pytest.mark.parametrize(
    ("status", "receipt_id", "terminal_reason"),
    [
        ("COMPLETED", None, None),
        ("COMPLETED", RECEIPT_ID, "UNEXPECTED_REASON"),
        ("FAILED", RECEIPT_ID, "INTERNAL_FAILURE"),
        ("FAILED", None, None),
        ("BLOCKED", None, None),
        ("NOT_PROVEN", None, None),
    ],
)
def test_terminal_fields_must_match_the_terminal_state(
    tmp_path,
    status,
    receipt_id,
    terminal_reason,
):
    ledger = make_ledger(tmp_path)
    reservation = reserve(
        ledger,
        idempotency_key="terminal-shape-{0}-{1}".format(
            status,
            terminal_reason or "none",
        ),
    )

    with pytest.raises(ValueError, match="requires|cannot carry"):
        ledger.mark_terminal(
            run_id=reservation.operation.run_id,
            input_digest=reservation.operation.input_digest,
            status=status,
            receipt_id=receipt_id,
            terminal_reason=terminal_reason,
            updated_at=TERMINAL_AT,
        )


def test_reopen_preserves_terminal_replay(tmp_path):
    first_ledger = make_ledger(tmp_path)
    reservation = reserve(first_ledger)
    terminal = first_ledger.mark_terminal(
        run_id=reservation.operation.run_id,
        input_digest=reservation.operation.input_digest,
        status="NOT_PROVEN",
        terminal_reason="PROCESS_INTERRUPTED",
        updated_at=TERMINAL_AT,
    )

    reopened_ledger = make_ledger(tmp_path)
    replay = reserve(reopened_ledger)

    assert replay.created is False
    assert replay.operation == terminal
    assert reopened_ledger.get_by_run_id(terminal.run_id) == terminal


def test_crash_state_remains_running_after_reopen(tmp_path):
    first_ledger = make_ledger(tmp_path)
    reservation = reserve(first_ledger)

    reopened_ledger = make_ledger(tmp_path)
    recovered = reopened_ledger.get_by_run_id(
        reservation.operation.run_id
    )
    replay = reserve(reopened_ledger)

    assert recovered == reservation.operation
    assert recovered.status is PerformanceOperationStatus.RUNNING
    assert replay.created is False
    assert replay.should_execute is False


def test_thread_concurrency_authorizes_exactly_one_execution(tmp_path):
    database_path = tmp_path / "threaded.sqlite3"
    SQLitePerformanceOperationLedger(database_path)

    def concurrent_reserve(_):
        ledger = SQLitePerformanceOperationLedger(database_path)
        return ledger.reserve(
            idempotency_key="shared-thread-key",
            **DEFAULT_INPUTS,
        )

    with ThreadPoolExecutor(max_workers=12) as executor:
        results = list(executor.map(concurrent_reserve, range(24)))

    assert sum(result.created for result in results) == 1
    assert len({result.operation.run_id for result in results}) == 1
    assert {result.operation.status for result in results} == {
        PerformanceOperationStatus.RUNNING
    }


def test_process_concurrency_authorizes_exactly_one_execution(tmp_path):
    database_path = tmp_path / "processes.sqlite3"
    SQLitePerformanceOperationLedger(database_path)
    context = multiprocessing.get_context("spawn")
    queue = context.Queue()
    processes = [
        context.Process(
            target=_process_reserve,
            args=(str(database_path), queue),
        )
        for _ in range(4)
    ]

    for process in processes:
        process.start()
    results = [queue.get(timeout=20) for _ in processes]
    for process in processes:
        process.join(timeout=20)
        assert process.exitcode == 0

    assert not [result for result in results if result[0] == "error"]
    successful = [result for result in results if result[0] == "ok"]
    assert sum(result[1] for result in successful) == 1
    assert len({result[2] for result in successful}) == 1


def test_sqlite_schema_version_wal_and_foreign_keys_are_explicit(tmp_path):
    ledger = make_ledger(tmp_path)
    connection = sqlite3.connect(ledger.database_path)
    try:
        metadata = connection.execute(
            """
            SELECT schema_name, schema_version
              FROM performance_operation_metadata
             WHERE singleton = 1
            """
        ).fetchone()
        user_version = connection.execute("PRAGMA user_version").fetchone()[0]
        journal_mode = connection.execute(
            "PRAGMA journal_mode"
        ).fetchone()[0]
    finally:
        connection.close()

    assert metadata == (
        PERFORMANCE_OPERATION_SCHEMA_NAME,
        PERFORMANCE_OPERATION_SCHEMA_VERSION,
    )
    assert user_version == PERFORMANCE_OPERATION_SCHEMA_VERSION
    assert journal_mode.lower() == "wal"

    managed_connection = ledger._connect()
    try:
        assert (
            managed_connection.execute(
                "PRAGMA foreign_keys"
            ).fetchone()[0]
            == 1
        )
    finally:
        managed_connection.close()


def test_operation_table_has_only_the_approved_secret_free_columns(tmp_path):
    ledger = make_ledger(tmp_path)
    connection = sqlite3.connect(ledger.database_path)
    try:
        columns = tuple(
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(performance_operations)"
            )
        )
    finally:
        connection.close()

    assert columns == (
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


def test_corrupt_unknown_status_is_rejected_on_read(tmp_path):
    ledger = make_ledger(tmp_path)
    reservation = reserve(ledger)
    connection = sqlite3.connect(ledger.database_path)
    try:
        connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute(
            """
            UPDATE performance_operations
               SET status = 'SECRET_SUCCESS'
             WHERE run_id = ?
            """,
            (reservation.operation.run_id,),
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(
        PerformanceOperationIntegrityError,
        match="corrupt",
    ):
        ledger.get_by_run_id(reservation.operation.run_id)


@pytest.mark.parametrize(
    ("column", "bad_timestamp"),
    [
        ("created_at", "2026-07-28T10:30:45.123456"),
        ("updated_at", "2026-07-28 10:30:45"),
        ("updated_at", "2026-07-28T11:30:45.123456+01:00"),
    ],
)
def test_naive_or_noncanonical_persisted_timestamps_are_rejected(
    tmp_path,
    column,
    bad_timestamp,
):
    ledger = make_ledger(tmp_path)
    reservation = reserve(ledger)
    connection = sqlite3.connect(ledger.database_path)
    try:
        connection.execute(
            "UPDATE performance_operations SET {0} = ? WHERE run_id = ?".format(
                column
            ),
            (bad_timestamp, reservation.operation.run_id),
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(
        PerformanceOperationIntegrityError,
        match="corrupt",
    ):
        ledger.get_by_run_id(reservation.operation.run_id)


def test_contradictory_persisted_timestamps_are_rejected(tmp_path):
    ledger = make_ledger(tmp_path)
    reservation = reserve(ledger)
    connection = sqlite3.connect(ledger.database_path)
    try:
        connection.execute(
            """
            UPDATE performance_operations
               SET updated_at = '2026-07-28T10:29:45.123456Z'
             WHERE run_id = ?
            """,
            (reservation.operation.run_id,),
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(
        PerformanceOperationIntegrityError,
        match="contradictory",
    ):
        ledger.get_by_run_id(reservation.operation.run_id)


def test_corrupt_metadata_schema_is_rejected_on_reopen(tmp_path):
    ledger = make_ledger(tmp_path)
    connection = sqlite3.connect(ledger.database_path)
    try:
        connection.execute(
            """
            UPDATE performance_operation_metadata
               SET schema_version = 999
             WHERE singleton = 1
            """
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(
        PerformanceOperationIntegrityError,
        match="Unsupported performance operation schema",
    ):
        make_ledger(tmp_path)


def test_unknown_table_is_rejected_on_reopen(tmp_path):
    ledger = make_ledger(tmp_path)
    connection = sqlite3.connect(ledger.database_path)
    try:
        connection.execute("CREATE TABLE raw_secrets (api_key TEXT)")
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(
        PerformanceOperationIntegrityError,
        match="unknown tables",
    ):
        make_ledger(tmp_path)


def test_extra_operation_column_is_rejected_on_reopen(tmp_path):
    ledger = make_ledger(tmp_path)
    connection = sqlite3.connect(ledger.database_path)
    try:
        connection.execute(
            "ALTER TABLE performance_operations ADD COLUMN raw_key TEXT"
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(
        PerformanceOperationIntegrityError,
        match="schema is corrupt",
    ):
        make_ledger(tmp_path)


def test_unrelated_sqlite_database_is_not_claimed(tmp_path):
    database_path = tmp_path / "unrelated.sqlite3"
    connection = sqlite3.connect(database_path)
    try:
        connection.execute("CREATE TABLE customer_data (value TEXT)")
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(
        PerformanceOperationIntegrityError,
        match="unrelated SQLite database",
    ):
        SQLitePerformanceOperationLedger(database_path)


@pytest.mark.parametrize(
    "database_path",
    [
        "relative.sqlite3",
        ":memory:",
        "file:/tmp/operations.sqlite3",
        "/tmp/../tmp/operations.sqlite3",
    ],
)
def test_unsafe_or_nondurable_database_paths_are_rejected(database_path):
    with pytest.raises(ValueError, match="database_path"):
        SQLitePerformanceOperationLedger(database_path)


def test_symbolic_link_database_path_is_rejected(tmp_path):
    target = tmp_path / "target.sqlite3"
    sqlite3.connect(target).close()
    link = tmp_path / "operations.sqlite3"
    link.symlink_to(target)

    with pytest.raises(ValueError, match="symbolic link"):
        SQLitePerformanceOperationLedger(link)


def test_missing_database_parent_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="parent"):
        SQLitePerformanceOperationLedger(
            tmp_path / "missing" / "operations.sqlite3"
        )


def test_naive_reservation_and_terminal_timestamps_are_rejected(tmp_path):
    ledger = make_ledger(tmp_path)

    with pytest.raises(ValueError, match="timezone-aware"):
        ledger.reserve(
            idempotency_key="naive-created-at",
            created_at=CREATED_AT.replace(tzinfo=None),
            **DEFAULT_INPUTS,
        )

    reservation = reserve(ledger)
    with pytest.raises(ValueError, match="timezone-aware"):
        ledger.mark_terminal(
            run_id=reservation.operation.run_id,
            input_digest=reservation.operation.input_digest,
            status="FAILED",
            terminal_reason="INVALID_TIMESTAMP",
            updated_at=TERMINAL_AT.replace(tzinfo=None),
        )


def test_terminal_timestamp_cannot_move_backwards(tmp_path):
    ledger = make_ledger(tmp_path)
    reservation = reserve(ledger)

    with pytest.raises(ValueError, match="cannot precede"):
        ledger.mark_terminal(
            run_id=reservation.operation.run_id,
            input_digest=reservation.operation.input_digest,
            status="FAILED",
            terminal_reason="CLOCK_REGRESSION",
            updated_at=CREATED_AT - timedelta(microseconds=1),
        )


def test_terminal_transition_requires_exact_input_digest(tmp_path):
    ledger = make_ledger(tmp_path)
    reservation = reserve(ledger)

    with pytest.raises(PerformanceOperationConflict):
        ledger.mark_terminal(
            run_id=reservation.operation.run_id,
            input_digest="f" * 64,
            status="FAILED",
            terminal_reason="INPUT_MISMATCH",
            updated_at=TERMINAL_AT,
        )

    assert (
        ledger.get_by_run_id(reservation.operation.run_id).status
        is PerformanceOperationStatus.RUNNING
    )


@pytest.mark.parametrize(
    "status",
    ["RUNNING", "PASS", "CANCELLED", "", None],
)
def test_unknown_or_nonterminal_transition_status_is_rejected(
    tmp_path,
    status,
):
    ledger = make_ledger(tmp_path)
    reservation = reserve(ledger)

    with pytest.raises(ValueError):
        ledger.mark_terminal(
            run_id=reservation.operation.run_id,
            input_digest=reservation.operation.input_digest,
            status=status,
            updated_at=TERMINAL_AT,
        )


@pytest.mark.parametrize(
    "reason",
    [
        "contains a customer error message",
        "API_KEY=secret",
        "lowercase",
        "A" * 65,
        "MULTI\nLINE",
    ],
)
def test_terminal_reason_accepts_only_non_sensitive_reason_codes(
    tmp_path,
    reason,
):
    ledger = make_ledger(tmp_path)
    reservation = reserve(
        ledger,
        idempotency_key="reason-{0}".format(hash(reason)),
    )

    with pytest.raises(ValueError, match="non-sensitive reason code"):
        ledger.mark_terminal(
            run_id=reservation.operation.run_id,
            input_digest=reservation.operation.input_digest,
            status="FAILED",
            terminal_reason=reason,
            updated_at=TERMINAL_AT,
        )


def test_generated_run_id_is_safe_and_collisions_fail_closed(tmp_path):
    colliding_run_id = "run_{0}".format("b" * 32)
    ledger = make_ledger(
        tmp_path,
        run_id_factory=lambda: colliding_run_id,
    )
    first = reserve(ledger, idempotency_key="first")
    assert first.operation.run_id == colliding_run_id

    with pytest.raises(
        PerformanceOperationIntegrityError,
        match="unique performance run identity",
    ):
        reserve(ledger, idempotency_key="second")


def test_get_unknown_safe_run_id_returns_none(tmp_path):
    ledger = make_ledger(tmp_path)
    assert ledger.get_by_run_id("run_{0}".format("0" * 32)) is None
