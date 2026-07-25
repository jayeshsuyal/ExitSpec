from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import threading
from typing import Any

import pytest

import exitspec.confirmation_ledger as confirmation_ledger_module
from exitspec.confirmation_ledger import (
    GuardedConnection,
    LedgerUnavailable,
    SQLiteConfirmationReader,
    bootstrap_confirmation_ledger,
    open_existing_confirmation_ledger,
)
from exitspec.confirmation_sqlite import (
    LedgerUnavailable as SQLiteLedgerUnavailable,
    validate_guarded_connection,
)
from exitspec.confirmation_store import (
    ConfirmationDecisionRecord,
    ConfirmationStore,
    ContractBinding,
    InvitationConsumed,
    InvitationExpired,
    InvitationRevoked,
    LedgerUnavailable as StoreLedgerUnavailable,
    RequestDigest,
    ReviewInvitationRecord,
    TokenDigest,
)
from exitspec.confirmations import ConfirmationDecision


MIGRATION_TIME = datetime(2026, 7, 24, 22, tzinfo=timezone.utc)
ISSUED_AT = datetime(
    2026,
    7,
    25,
    10,
    15,
    30,
    123456,
    tzinfo=timezone.utc,
)
BINDING = ContractBinding(
    contract_id="support-agent",
    contract_version="0.1.0",
    confirmation_fingerprint="a" * 64,
)


class StructuralConnectionFake:
    """A protocol-shaped object that must never pass the nominal guard."""

    in_transaction = False
    isolation_level = None
    row_factory = sqlite3.Row

    def execute(
        self,
        _sql: str,
        _parameters: object = (),
    ) -> object:
        raise AssertionError("structural fake must never execute")

    def blobopen(self, *_args: object, **_kwargs: object) -> object:
        raise AssertionError("structural fake must never open a blob")

    def rollback(self) -> None:
        raise AssertionError("structural fake must never roll back")

    def close(self) -> None:
        raise AssertionError("structural fake must never close")


class ConnectionProxy:
    """A structural proxy around a real facade; exact type must still win."""

    def __init__(self, target: GuardedConnection) -> None:
        self._target = target

    def __getattr__(self, name: str) -> object:
        return getattr(self._target, name)


def _epoch_microseconds(value: datetime) -> int:
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    delta = value - epoch
    return (
        delta.days * 86_400_000_000
        + delta.seconds * 1_000_000
        + delta.microseconds
    )


def _execute(
    connection: GuardedConnection,
    sql: str,
    parameters: object = (),
) -> None:
    cursor = connection.execute(sql, parameters)
    cursor.close()


def _insert_invitation(
    connection: GuardedConnection,
    *,
    invitation_id: str = "review-primary",
    token_digest: str = "b" * 64,
    token_digest_version: str = "sha256-v1",
    binding: ContractBinding = BINDING,
    intended_organization_id: str = "customer-org",
    issued_at: datetime = ISSUED_AT,
    expires_at: datetime | None = None,
) -> ReviewInvitationRecord:
    expiry = expires_at or issued_at + timedelta(hours=2)
    _execute(
        connection,
        """
        INSERT INTO main.review_invitations (
            invitation_id,
            contract_id,
            contract_version,
            confirmation_fingerprint,
            token_digest,
            token_digest_version,
            intended_organization_id,
            issued_by_subject,
            issued_at_us,
            expires_at_us
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            invitation_id,
            binding.contract_id,
            binding.contract_version,
            binding.confirmation_fingerprint,
            token_digest,
            token_digest_version,
            intended_organization_id,
            "seller-subject",
            _epoch_microseconds(issued_at),
            _epoch_microseconds(expiry),
        ),
    )
    return ReviewInvitationRecord(
        invitation_id=invitation_id,
        binding=binding,
        token_digest=TokenDigest(token_digest),
        token_digest_version=token_digest_version,
        intended_organization_id=intended_organization_id,
        issued_by_subject="seller-subject",
        issued_at=issued_at,
        expires_at=expiry,
    )


def _insert_decision(
    connection: GuardedConnection,
    invitation: ReviewInvitationRecord,
    *,
    confirmation_id: str = "cnf_" + "c" * 64,
    decided_at: datetime | None = None,
) -> ConfirmationDecisionRecord:
    decision_time = decided_at or invitation.issued_at + timedelta(hours=1)
    _execute(
        connection,
        """
        INSERT INTO main.confirmation_decisions (
            confirmation_id,
            invitation_id,
            contract_id,
            contract_version,
            confirmation_fingerprint,
            reviewer_issuer,
            reviewer_subject,
            reviewer_organization_id,
            reviewer_display_name_snapshot,
            decision,
            agreement_acknowledged,
            rationale,
            decided_at_us,
            request_digest
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            confirmation_id,
            invitation.invitation_id,
            invitation.binding.contract_id,
            invitation.binding.contract_version,
            invitation.binding.confirmation_fingerprint,
            "https://identity.example",
            "reviewer-subject",
            invitation.intended_organization_id,
            "Customer approver",
            "CONFIRM",
            1,
            "",
            _epoch_microseconds(decision_time),
            "d" * 64,
        ),
    )
    return ConfirmationDecisionRecord(
        confirmation_id=confirmation_id,
        invitation_id=invitation.invitation_id,
        binding=invitation.binding,
        reviewer_issuer="https://identity.example",
        reviewer_subject="reviewer-subject",
        reviewer_organization_id=invitation.intended_organization_id,
        reviewer_display_name_snapshot="Customer approver",
        decision=ConfirmationDecision.CONFIRM,
        agreement_acknowledged=True,
        rationale="",
        decided_at=decision_time,
        request_digest=RequestDigest("d" * 64),
    )


def _insert_revocation(
    connection: GuardedConnection,
    invitation: ReviewInvitationRecord,
    *,
    revoked_at: datetime,
) -> None:
    _execute(
        connection,
        """
        INSERT INTO main.invitation_revocations (
            invitation_id,
            revoked_at_us,
            revoked_by_subject,
            reason_code
        )
        VALUES (?, ?, ?, ?)
        """,
        (
            invitation.invitation_id,
            _epoch_microseconds(revoked_at),
            "seller-subject",
            "MANUAL",
        ),
    )


def _insert_unchecked_invitation_digest(
    database_path: Path,
    *,
    invitation_id: str,
    token_digest: str,
) -> None:
    raw = sqlite3.connect(database_path)
    try:
        raw.execute("PRAGMA ignore_check_constraints = ON")
        raw.execute(
            """
            INSERT INTO review_invitations (
                invitation_id,
                contract_id,
                contract_version,
                confirmation_fingerprint,
                token_digest,
                token_digest_version,
                intended_organization_id,
                issued_by_subject,
                issued_at_us,
                expires_at_us
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                invitation_id,
                BINDING.contract_id,
                BINDING.contract_version,
                BINDING.confirmation_fingerprint,
                token_digest,
                "sha256-v1",
                "customer-org",
                "seller-subject",
                _epoch_microseconds(ISSUED_AT),
                _epoch_microseconds(ISSUED_AT + timedelta(hours=2)),
            ),
        )
        raw.commit()
    finally:
        raw.close()


def _bootstrap_path(tmp_path: Path) -> tuple[Path, GuardedConnection]:
    database_path = tmp_path / "confirmation.db"
    return (
        database_path,
        bootstrap_confirmation_ledger(
            database_path,
            migration_time=MIGRATION_TIME,
        ),
    )


def _connection_factory(
    database_path: Path,
    *,
    opened: list[GuardedConnection] | None = None,
) -> Callable[[], GuardedConnection]:
    def create() -> GuardedConnection:
        connection = open_existing_confirmation_ledger(
            database_path,
        )
        if opened is not None:
            opened.append(connection)
        return connection

    return create


def _assert_connection_closed(connection: GuardedConnection) -> None:
    with pytest.raises(sqlite3.ProgrammingError):
        connection.execute("SELECT 1")


def _row_count(database_path: Path, table: str) -> int:
    connection = sqlite3.connect(database_path)
    try:
        return connection.execute(
            "SELECT COUNT(*) FROM {0}".format(table)
        ).fetchone()[0]
    finally:
        connection.close()


def test_public_ledger_error_is_the_canonical_store_error() -> None:
    assert LedgerUnavailable is StoreLedgerUnavailable
    assert LedgerUnavailable is not SQLiteLedgerUnavailable
    assert not hasattr(
        confirmation_ledger_module,
        "open_existing_confirmation_database",
    )


def test_nominal_guard_rejects_raw_proxy_structural_and_tampered_facades(
    tmp_path: Path,
) -> None:
    _, guarded = _bootstrap_path(tmp_path)
    raw = sqlite3.connect(":memory:")
    structural = StructuralConnectionFake()
    proxy = ConnectionProxy(guarded)

    assert isinstance(raw, GuardedConnection)
    assert isinstance(structural, GuardedConnection)
    assert validate_guarded_connection(guarded) is None
    for rejected in (raw, structural, proxy):
        with pytest.raises(SQLiteLedgerUnavailable):
            validate_guarded_connection(rejected)

    object.__setattr__(
        guarded,
        "_GuardedConnection__guard_token",
        object(),
    )
    with pytest.raises(SQLiteLedgerUnavailable):
        validate_guarded_connection(guarded)

    raw.close()
    guarded.close()


def test_reader_translates_non_nominal_factory_results_to_canonical_error(
    tmp_path: Path,
) -> None:
    _, guarded = _bootstrap_path(tmp_path)
    raw = sqlite3.connect(":memory:")
    candidates = (
        raw,
        StructuralConnectionFake(),
        ConnectionProxy(guarded),
    )

    for candidate in candidates:
        reader = SQLiteConfirmationReader(lambda value=candidate: value)
        with pytest.raises(
            LedgerUnavailable,
            match="^Confirmation ledger is unavailable\\.$",
        ) as error:
            reader.get_invitation("review-primary")
        assert type(error.value) is StoreLedgerUnavailable
        assert error.value.__cause__ is None

    raw.close()
    guarded.close()


def test_reader_disposes_rejected_nominal_facade_with_tampered_guard(
    tmp_path: Path,
) -> None:
    _, guarded = _bootstrap_path(tmp_path)
    object.__setattr__(
        guarded,
        "_GuardedConnection__guard_token",
        object(),
    )
    reader = SQLiteConfirmationReader(lambda: guarded)

    with pytest.raises(
        LedgerUnavailable,
        match="^Confirmation ledger is unavailable\\.$",
    ) as error:
        reader.get_invitation("review-primary")

    assert type(error.value) is StoreLedgerUnavailable
    assert error.value.__cause__ is None
    _assert_connection_closed(guarded)


def test_reader_disposes_nominal_candidate_on_validator_programming_fault(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, guarded = _bootstrap_path(tmp_path)

    def fail_validation(_candidate: object) -> None:
        raise RuntimeError("reader validator programming defect")

    monkeypatch.setattr(
        confirmation_ledger_module,
        "_validate_guarded_connection",
        fail_validation,
    )
    reader = SQLiteConfirmationReader(lambda: guarded)

    with pytest.raises(
        RuntimeError,
        match="^reader validator programming defect$",
    ):
        reader.get_invitation("review-primary")

    _assert_connection_closed(guarded)


@pytest.mark.parametrize(
    "delete_initialized_database",
    (False, True),
    ids=("never-created", "deleted-after-startup"),
)
def test_reader_existing_only_factory_never_recreates_missing_database(
    tmp_path: Path,
    delete_initialized_database: bool,
) -> None:
    database_path = tmp_path / "confirmation.db"
    if delete_initialized_database:
        initialized = bootstrap_confirmation_ledger(
            database_path,
            migration_time=MIGRATION_TIME,
        )
        initialized.close()
        database_path.unlink()
    assert not database_path.exists()

    reader = SQLiteConfirmationReader(
        _connection_factory(database_path)
    )
    with pytest.raises(
        LedgerUnavailable,
        match="^Confirmation ledger is unavailable\\.$",
    ) as error:
        reader.get_invitation("review-primary")

    assert type(error.value) is StoreLedgerUnavailable
    assert error.value.__cause__ is None
    assert not database_path.exists()


def test_reader_rehydrates_exact_records_after_restart_and_closes_each_call(
    tmp_path: Path,
) -> None:
    database_path, first = _bootstrap_path(tmp_path)
    invitation = _insert_invitation(first)
    decision = _insert_decision(first, invitation)
    first.close()

    opened: list[GuardedConnection] = []
    reader = SQLiteConfirmationReader(
        _connection_factory(database_path, opened=opened)
    )
    hydrated_invitation = reader.get_invitation(
        invitation.invitation_id
    )
    hydrated_decision = reader.get_decision(invitation.binding)

    assert hydrated_invitation == invitation
    assert hydrated_invitation is not None
    assert hydrated_invitation.issued_at.tzinfo is timezone.utc
    assert hydrated_invitation.issued_at.microsecond == 123456
    assert hydrated_invitation.expires_at.tzinfo is timezone.utc
    assert hydrated_invitation.expires_at.microsecond == 123456

    assert hydrated_decision == decision
    assert hydrated_decision is not None
    assert hydrated_decision.decided_at.tzinfo is timezone.utc
    assert hydrated_decision.decided_at.microsecond == 123456

    assert reader.get_decision(
        ContractBinding(
            contract_id=invitation.binding.contract_id,
            contract_version=invitation.binding.contract_version,
            confirmation_fingerprint="f" * 64,
        )
    ) is None
    assert reader.get_invitation("review-unknown") is None

    assert len(opened) == 4
    for connection in opened:
        _assert_connection_closed(connection)


def test_fresh_per_call_factory_succeeds_from_a_worker_thread(
    tmp_path: Path,
) -> None:
    database_path, seed = _bootstrap_path(tmp_path)
    invitation = _insert_invitation(seed)
    seed.close()

    creation_threads: list[int] = []
    opened: list[GuardedConnection] = []

    def factory() -> GuardedConnection:
        creation_threads.append(threading.get_ident())
        connection = open_existing_confirmation_ledger(database_path)
        opened.append(connection)
        return connection

    reader = SQLiteConfirmationReader(factory)

    def read_in_worker() -> tuple[int, ReviewInvitationRecord | None, bool]:
        worker_thread = threading.get_ident()
        hydrated = reader.get_invitation(invitation.invitation_id)
        try:
            opened[-1].execute("SELECT 1")
        except sqlite3.ProgrammingError:
            closed_in_worker = True
        else:
            closed_in_worker = False
        return worker_thread, hydrated, closed_in_worker

    with ThreadPoolExecutor(max_workers=1) as pool:
        worker_thread, hydrated, closed_in_worker = pool.submit(
            read_in_worker
        ).result()

    assert hydrated == invitation
    assert creation_threads == [worker_thread]
    assert closed_in_worker is True


def test_active_factory_transaction_is_rejected_before_any_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_path, migrated = _bootstrap_path(tmp_path)
    migrated.close()
    created: list[GuardedConnection] = []
    operation_called = False

    def fail_if_read(
        _connection: GuardedConnection,
        _invitation_id: str,
    ) -> None:
        nonlocal operation_called
        operation_called = True
        raise AssertionError("an active transaction must block before reading")

    monkeypatch.setattr(
        confirmation_ledger_module,
        "_read_invitation_by_id",
        fail_if_read,
    )

    def transactional_factory() -> GuardedConnection:
        connection = open_existing_confirmation_ledger(database_path)
        _execute(connection, "BEGIN")
        _insert_invitation(connection)
        assert connection.in_transaction is True
        created.append(connection)
        return connection

    reader = SQLiteConfirmationReader(transactional_factory)
    with pytest.raises(
        LedgerUnavailable,
        match="^Confirmation ledger is unavailable\\.$",
    ) as error:
        reader.get_invitation("review-primary")

    assert type(error.value) is StoreLedgerUnavailable
    assert error.value.__cause__ is None
    assert operation_called is False
    assert len(created) == 1
    _assert_connection_closed(created[0])
    assert _row_count(database_path, "review_invitations") == 0


def test_close_failure_becomes_canonical_error_without_leaking_data(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_path, sample = _bootstrap_path(tmp_path)
    guarded_type = type(sample)
    original_close = guarded_type.close
    sample.close()

    opened: list[GuardedConnection] = []
    secret = "close-secret-" + "e" * 64

    def factory() -> GuardedConnection:
        connection = open_existing_confirmation_ledger(database_path)
        opened.append(connection)
        return connection

    def fail_close(_connection: object) -> None:
        raise sqlite3.OperationalError(
            "{0}:{1}".format(database_path, secret)
        )

    monkeypatch.setattr(guarded_type, "close", fail_close)
    reader = SQLiteConfirmationReader(factory)
    with pytest.raises(
        LedgerUnavailable,
        match="^Confirmation ledger is unavailable\\.$",
    ) as error:
        reader.get_invitation("review-unknown")

    assert type(error.value) is StoreLedgerUnavailable
    assert error.value.__cause__ is None
    assert str(database_path) not in repr(error.value)
    assert secret not in repr(error.value)
    for connection in opened:
        original_close(connection)


def test_close_programming_defect_propagates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_path, sample = _bootstrap_path(tmp_path)
    guarded_type = type(sample)
    original_close = guarded_type.close
    sample.close()

    opened: list[GuardedConnection] = []

    def factory() -> GuardedConnection:
        connection = open_existing_confirmation_ledger(database_path)
        opened.append(connection)
        return connection

    def fail_close(_connection: object) -> None:
        raise AssertionError("close programming defect")

    monkeypatch.setattr(guarded_type, "close", fail_close)
    reader = SQLiteConfirmationReader(factory)
    with pytest.raises(
        AssertionError,
        match="^close programming defect$",
    ):
        reader.get_invitation("review-unknown")

    for connection in opened:
        original_close(connection)


@pytest.mark.parametrize(
    "programming_error_type",
    (IndexError, KeyError, OverflowError, TypeError, ValueError),
)
def test_reader_programming_errors_propagate_after_rollback_and_close(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    programming_error_type: type[Exception],
) -> None:
    database_path, sample = _bootstrap_path(tmp_path)
    guarded_type = type(sample)
    original_rollback = guarded_type.rollback
    sample.close()

    rollback_calls: list[GuardedConnection] = []
    opened: list[GuardedConnection] = []

    def recording_rollback(connection: GuardedConnection) -> None:
        rollback_calls.append(connection)
        original_rollback(connection)

    def factory() -> GuardedConnection:
        connection = open_existing_confirmation_ledger(database_path)
        opened.append(connection)
        return connection

    def fail_operation(
        connection: GuardedConnection,
        _invitation_id: str,
    ) -> None:
        assert connection.in_transaction is True
        raise programming_error_type("reader programming defect")

    monkeypatch.setattr(
        guarded_type,
        "rollback",
        recording_rollback,
    )
    monkeypatch.setattr(
        confirmation_ledger_module,
        "_read_invitation_by_id",
        fail_operation,
    )
    reader = SQLiteConfirmationReader(factory)

    with pytest.raises(programming_error_type) as error:
        reader.get_invitation("review-primary")

    assert "reader programming defect" in str(error.value)
    assert len(opened) == 1
    assert rollback_calls.count(opened[0]) == 2
    _assert_connection_closed(opened[0])


@pytest.mark.parametrize(
    "malformed_digest",
    (
        "\N{LATIN SMALL LETTER E WITH ACUTE}" * 64,
        "a" * 63,
        "A" * 64,
    ),
    ids=("non-ascii", "wrong-length", "uppercase"),
)
def test_malformed_persisted_digest_fails_after_later_rows_are_processed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    malformed_digest: str,
) -> None:
    database_path, connection = _bootstrap_path(tmp_path)
    _insert_invitation(
        connection,
        invitation_id="review-zulu",
        token_digest="b" * 64,
    )
    connection.close()
    _insert_unchecked_invitation_digest(
        database_path,
        invitation_id="review-alpha",
        token_digest=malformed_digest,
    )

    calls: list[tuple[str, str]] = []
    original_compare_digest = (
        confirmation_ledger_module.hmac.compare_digest
    )

    def recording_compare_digest(left: str, right: str) -> bool:
        calls.append((left, right))
        return original_compare_digest(left, right)

    monkeypatch.setattr(
        confirmation_ledger_module.hmac,
        "compare_digest",
        recording_compare_digest,
    )
    reader = SQLiteConfirmationReader(
        _connection_factory(database_path)
    )

    with pytest.raises(
        LedgerUnavailable,
        match="^Confirmation ledger is unavailable\\.$",
    ) as error:
        reader.resolve_ledger_invitation(
            TokenDigest("f" * 64),
            ISSUED_AT,
        )

    assert type(error.value) is StoreLedgerUnavailable
    assert error.value.__cause__ is None
    assert calls == [("b" * 64, "f" * 64)]
    rendered_error = repr(error.value)
    assert malformed_digest not in rendered_error
    assert str(database_path) not in rendered_error


def test_ledger_lookup_compares_every_digest_without_early_exit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_path, connection = _bootstrap_path(tmp_path)
    expected = _insert_invitation(
        connection,
        invitation_id="review-alpha",
        token_digest="a" * 64,
    )
    _insert_invitation(
        connection,
        invitation_id="review-bravo",
        token_digest="b" * 64,
    )
    _insert_invitation(
        connection,
        invitation_id="review-charlie",
        token_digest="c" * 64,
    )
    connection.close()

    calls: list[tuple[str, str]] = []
    original_compare_digest = (
        confirmation_ledger_module.hmac.compare_digest
    )

    def recording_compare_digest(left: str, right: str) -> bool:
        calls.append((left, right))
        return original_compare_digest(left, right)

    monkeypatch.setattr(
        confirmation_ledger_module.hmac,
        "compare_digest",
        recording_compare_digest,
    )
    reader = SQLiteConfirmationReader(
        _connection_factory(database_path)
    )

    assert reader.resolve_ledger_invitation(
        TokenDigest("a" * 64),
        ISSUED_AT,
    ) == expected
    assert calls == [
        ("a" * 64, "a" * 64),
        ("b" * 64, "a" * 64),
        ("c" * 64, "a" * 64),
    ]


def test_unsupported_digest_version_fails_after_all_digest_comparisons(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_path, connection = _bootstrap_path(tmp_path)
    _insert_invitation(
        connection,
        invitation_id="review-alpha",
        token_digest="a" * 64,
    )
    _insert_invitation(
        connection,
        invitation_id="review-bravo",
        token_digest="b" * 64,
        token_digest_version="v99",
    )
    _insert_invitation(
        connection,
        invitation_id="review-charlie",
        token_digest="c" * 64,
    )
    connection.close()

    calls: list[tuple[str, str]] = []
    original_compare_digest = (
        confirmation_ledger_module.hmac.compare_digest
    )

    def recording_compare_digest(left: str, right: str) -> bool:
        calls.append((left, right))
        return original_compare_digest(left, right)

    monkeypatch.setattr(
        confirmation_ledger_module.hmac,
        "compare_digest",
        recording_compare_digest,
    )
    reader = SQLiteConfirmationReader(
        _connection_factory(database_path)
    )

    with pytest.raises(LedgerUnavailable) as error:
        reader.resolve_ledger_invitation(
            TokenDigest("a" * 64),
            ISSUED_AT,
        )

    assert type(error.value) is StoreLedgerUnavailable
    assert error.value.__cause__ is None
    assert calls == [
        ("a" * 64, "a" * 64),
        ("b" * 64, "a" * 64),
        ("c" * 64, "a" * 64),
    ]
    with pytest.raises(LedgerUnavailable):
        reader.get_invitation("review-bravo")
    assert len(calls) == 3


def test_expiry_is_active_before_and_expired_at_exact_boundary(
    tmp_path: Path,
) -> None:
    database_path, connection = _bootstrap_path(tmp_path)
    invitation = _insert_invitation(connection)
    connection.close()
    reader = SQLiteConfirmationReader(
        _connection_factory(database_path)
    )

    assert reader.resolve_ledger_invitation(
        invitation.token_digest,
        invitation.expires_at - timedelta(microseconds=1),
    ) == invitation
    with pytest.raises(
        InvitationExpired,
        match="^Review capability is no longer active\\.$",
    ):
        reader.resolve_ledger_invitation(
            invitation.token_digest,
            invitation.expires_at,
        )


def test_state_precedence_is_revoked_then_consumed_then_expired(
    tmp_path: Path,
) -> None:
    database_path, connection = _bootstrap_path(tmp_path)
    source = _insert_invitation(
        connection,
        invitation_id="review-source",
        token_digest="b" * 64,
    )
    target = _insert_invitation(
        connection,
        invitation_id="review-target",
        token_digest="c" * 64,
    )
    _insert_decision(connection, source)
    connection.close()
    reader = SQLiteConfirmationReader(
        _connection_factory(database_path)
    )

    with pytest.raises(InvitationConsumed):
        reader.resolve_ledger_invitation(
            target.token_digest,
            target.expires_at + timedelta(hours=1),
        )

    revocation_connection = open_existing_confirmation_ledger(database_path)
    _insert_revocation(
        revocation_connection,
        target,
        revoked_at=target.issued_at + timedelta(minutes=90),
    )
    revocation_connection.close()

    with pytest.raises(InvitationRevoked):
        reader.resolve_ledger_invitation(
            target.token_digest,
            target.expires_at + timedelta(hours=1),
        )


def test_ledger_lookup_is_not_stale_detection_or_authorization(
    tmp_path: Path,
) -> None:
    database_path, connection = _bootstrap_path(tmp_path)
    invitation = _insert_invitation(connection)
    connection.close()
    reader = SQLiteConfirmationReader(
        _connection_factory(database_path)
    )
    current_binding_from_contract_repository = ContractBinding(
        contract_id=invitation.binding.contract_id,
        contract_version=invitation.binding.contract_version,
        confirmation_fingerprint="f" * 64,
    )

    ledger_record = reader.resolve_ledger_invitation(
        invitation.token_digest,
        invitation.issued_at,
    )

    assert ledger_record == invitation
    assert ledger_record is not None
    assert (
        ledger_record.binding
        != current_binding_from_contract_repository
    )
    assert not hasattr(reader, "resolve_invitation")
    documentation = " ".join(
        (SQLiteConfirmationReader.__doc__ or "").split()
    )
    assert "cannot derive ``STALE``" in documentation
    assert "never an authorization decision" in documentation
    assert "recompute its canonical binding" in documentation


def test_unknown_digest_returns_none_and_raw_token_is_rejected(
    tmp_path: Path,
) -> None:
    database_path, connection = _bootstrap_path(tmp_path)
    invitation = _insert_invitation(connection)
    connection.close()
    factory_calls = 0

    def factory() -> GuardedConnection:
        nonlocal factory_calls
        factory_calls += 1
        return open_existing_confirmation_ledger(database_path)

    reader = SQLiteConfirmationReader(factory)
    assert reader.resolve_ledger_invitation(
        TokenDigest("9" * 64),
        ISSUED_AT,
    ) is None
    with pytest.raises(
        TypeError,
        match="must be a TokenDigest, never a raw token",
    ) as error:
        reader.resolve_ledger_invitation(  # type: ignore[arg-type]
            "customer-review-secret",
            ISSUED_AT,
        )

    assert factory_calls == 1
    assert "customer-review-secret" not in str(error.value)
    assert invitation.token_digest.value not in str(error.value)


def test_multi_query_lookup_uses_one_consistent_read_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_path, seed = _bootstrap_path(tmp_path)
    invitation = _insert_invitation(seed)
    seed.close()

    reached_revocation_query = threading.Event()
    writer_finished = threading.Event()
    original_read_revocation = confirmation_ledger_module._read_revocation

    def pause_before_revocation_query(
        connection: GuardedConnection,
        invitation_id: str,
    ) -> object:
        if not reached_revocation_query.is_set():
            assert connection.in_transaction is True
            reached_revocation_query.set()
            assert writer_finished.wait(timeout=5)
        return original_read_revocation(connection, invitation_id)

    monkeypatch.setattr(
        confirmation_ledger_module,
        "_read_revocation",
        pause_before_revocation_query,
    )
    reader = SQLiteConfirmationReader(
        _connection_factory(database_path)
    )

    with ThreadPoolExecutor(max_workers=1) as pool:
        in_flight_read = pool.submit(
            reader.resolve_ledger_invitation,
            invitation.token_digest,
            invitation.issued_at,
        )
        assert reached_revocation_query.wait(timeout=5)
        writer = open_existing_confirmation_ledger(database_path)
        try:
            _insert_revocation(
                writer,
                invitation,
                revoked_at=invitation.issued_at + timedelta(minutes=1),
            )
        finally:
            writer.close()
            writer_finished.set()
        assert in_flight_read.result(timeout=5) == invitation

    with pytest.raises(InvitationRevoked):
        reader.resolve_ledger_invitation(
            invitation.token_digest,
            invitation.issued_at,
        )


def test_corrupt_timestamp_fails_closed_without_leaking_row_or_path(
    tmp_path: Path,
) -> None:
    database_path, connection = _bootstrap_path(tmp_path)
    connection.close()
    corrupt_digest = "e" * 64

    raw = sqlite3.connect(database_path)
    try:
        raw.execute("PRAGMA ignore_check_constraints = ON")
        raw.execute(
            """
            INSERT INTO review_invitations (
                invitation_id,
                contract_id,
                contract_version,
                confirmation_fingerprint,
                token_digest,
                token_digest_version,
                intended_organization_id,
                issued_by_subject,
                issued_at_us,
                expires_at_us
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "review-corrupt",
                BINDING.contract_id,
                BINDING.contract_version,
                BINDING.confirmation_fingerprint,
                corrupt_digest,
                "sha256-v1",
                "customer-org",
                "seller-subject",
                -1,
                1,
            ),
        )
        raw.commit()
    finally:
        raw.close()

    reader = SQLiteConfirmationReader(
        _connection_factory(database_path)
    )
    with pytest.raises(
        LedgerUnavailable,
        match="^Confirmation ledger is unavailable\\.$",
    ) as error:
        reader.get_invitation("review-corrupt")

    assert type(error.value) is StoreLedgerUnavailable
    assert error.value.__cause__ is None
    rendered_error = repr(error.value)
    assert corrupt_digest not in rendered_error
    assert str(database_path) not in rendered_error


def test_cross_row_binding_mismatch_fails_closed(
    tmp_path: Path,
) -> None:
    database_path, connection = _bootstrap_path(tmp_path)
    invitation = _insert_invitation(connection)
    connection.close()
    mismatched_fingerprint = "f" * 64

    raw = sqlite3.connect(database_path)
    try:
        raw.execute(
            """
            INSERT INTO confirmation_decisions (
                confirmation_id,
                invitation_id,
                contract_id,
                contract_version,
                confirmation_fingerprint,
                reviewer_issuer,
                reviewer_subject,
                reviewer_organization_id,
                reviewer_display_name_snapshot,
                decision,
                agreement_acknowledged,
                rationale,
                decided_at_us,
                request_digest
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "cnf_" + "e" * 64,
                invitation.invitation_id,
                invitation.binding.contract_id,
                invitation.binding.contract_version,
                mismatched_fingerprint,
                "https://identity.example",
                "reviewer-subject",
                invitation.intended_organization_id,
                "Customer approver",
                "CONFIRM",
                1,
                "",
                _epoch_microseconds(
                    invitation.issued_at + timedelta(hours=1)
                ),
                "d" * 64,
            ),
        )
        raw.commit()
    finally:
        raw.close()

    reader = SQLiteConfirmationReader(
        _connection_factory(database_path)
    )
    with pytest.raises(LedgerUnavailable) as error:
        reader.resolve_ledger_invitation(
            invitation.token_digest,
            invitation.issued_at,
        )

    rendered_error = repr(error.value)
    assert error.value.__cause__ is None
    assert mismatched_fingerprint not in rendered_error
    assert invitation.token_digest.value not in rendered_error
    assert str(database_path) not in rendered_error


def test_contradictory_sibling_invitation_bindings_fail_closed(
    tmp_path: Path,
) -> None:
    database_path, connection = _bootstrap_path(tmp_path)
    invitation = _insert_invitation(connection)
    connection.close()
    mismatched_fingerprint = "f" * 64

    raw = sqlite3.connect(database_path)
    try:
        raw.execute(
            "DROP TRIGGER review_invitations_consistent_fingerprint"
        )
        raw.execute(
            """
            INSERT INTO review_invitations (
                invitation_id,
                contract_id,
                contract_version,
                confirmation_fingerprint,
                token_digest,
                token_digest_version,
                intended_organization_id,
                issued_by_subject,
                issued_at_us,
                expires_at_us
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "review-conflict",
                invitation.binding.contract_id,
                invitation.binding.contract_version,
                mismatched_fingerprint,
                "8" * 64,
                "sha256-v1",
                invitation.intended_organization_id,
                "seller-subject",
                _epoch_microseconds(invitation.issued_at),
                _epoch_microseconds(invitation.expires_at),
            ),
        )
        raw.commit()
    finally:
        raw.close()

    reader = SQLiteConfirmationReader(
        _connection_factory(database_path)
    )
    with pytest.raises(LedgerUnavailable) as error:
        reader.resolve_ledger_invitation(
            invitation.token_digest,
            invitation.issued_at,
        )

    rendered_error = repr(error.value)
    assert error.value.__cause__ is None
    assert mismatched_fingerprint not in rendered_error
    assert invitation.token_digest.value not in rendered_error
    assert str(database_path) not in rendered_error


def test_reader_is_partial_read_only_and_retains_no_connection(
    tmp_path: Path,
) -> None:
    database_path, connection = _bootstrap_path(tmp_path)
    invitation = _insert_invitation(connection)
    connection.close()
    before = _row_count(database_path, "review_invitations")
    opened: list[GuardedConnection] = []
    reader = SQLiteConfirmationReader(
        _connection_factory(database_path, opened=opened)
    )

    assert reader.get_invitation(invitation.invitation_id) == invitation
    assert _row_count(database_path, "review_invitations") == before
    assert len(opened) == 1
    _assert_connection_closed(opened[0])
    assert not isinstance(reader, ConfirmationStore)
    for prohibited_name in (
        "close",
        "closed",
        "owns_connection",
        "execute",
        "issue_invitation",
        "resolve_invitation",
        "record_decision",
        "reissue_invitation",
        "revoke_invitation",
        "list_audit_events",
        "__enter__",
        "__exit__",
    ):
        assert not hasattr(reader, prohibited_name)
    with pytest.raises(TypeError, match="connection_factory"):
        SQLiteConfirmationReader(object())  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "invalid_now",
    (
        datetime(2026, 7, 25, 10, 15),
        datetime(
            2026,
            7,
            25,
            10,
            15,
            tzinfo=timezone(timedelta(hours=1)),
        ),
        "2026-07-25T10:15:00Z",
    ),
    ids=("naive", "non-utc", "not-datetime"),
)
def test_lookup_rejects_untrusted_transaction_time_before_factory_call(
    invalid_now: Any,
) -> None:
    factory_calls = 0

    def factory() -> GuardedConnection:
        nonlocal factory_calls
        factory_calls += 1
        raise AssertionError("invalid now must fail before opening SQLite")

    reader = SQLiteConfirmationReader(factory)
    with pytest.raises(
        ValueError,
        match="^now must be timezone-aware UTC\\.$",
    ):
        reader.resolve_ledger_invitation(
            TokenDigest("b" * 64),
            invalid_now,
        )

    assert factory_calls == 0
