from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import re
import sqlite3
import threading
from pathlib import Path
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
from exitspec.confirmation_store import (
    ContractBinding,
    ContractBindingMismatch,
    InvitationIdentityConflict,
    InvitationRevoked,
    LedgerUnavailable as StoreLedgerUnavailable,
    ReviewInvitationRecord,
    TokenDigest,
    TokenDigestConflict,
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
ISSUED_AT = datetime(
    2026,
    7,
    25,
    10,
    15,
    30,
    654321,
    tzinfo=timezone.utc,
)
BINDING = ContractBinding(
    contract_id="support-agent",
    contract_version="0.1.0",
    confirmation_fingerprint="a" * 64,
)
AUDIT_COLUMNS = (
    "event_id",
    "event_sequence",
    "event_type",
    "occurred_at_us",
    "contract_id",
    "contract_version",
    "confirmation_fingerprint",
    "invitation_id",
    "confirmation_id",
    "actor_issuer",
    "actor_subject",
    "actor_organization_id",
    "outcome",
    "reason_code",
    "trace_id",
    "metadata_schema_version",
    "metadata_adapter_name",
    "metadata_adapter_version",
)
AUDIT_EVENT_ID = re.compile(r"^audit-[a-z0-9]+(?:-[a-z0-9]+)*$")
CANONICAL_LEDGER_ERROR = "^Confirmation ledger is unavailable\\.$"


class StructuralConnectionFake:
    """A protocol-shaped object that must not cross the nominal guard."""

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
    """A structural proxy around a real facade is still not a valid facade."""

    def __init__(self, target: GuardedConnection) -> None:
        self._target = target

    def __getattr__(self, name: str) -> object:
        return getattr(self._target, name)


class MasqueradingStr(str):
    """A string whose real SQLite payload lies about one equality."""

    def __new__(
        cls,
        actual_value: str,
        masquerades_as: str,
    ) -> "MasqueradingStr":
        instance = super().__new__(cls, actual_value)
        instance.masquerades_as = masquerades_as
        return instance

    def __eq__(self, other: object) -> bool:
        if type(other) is str and str.__eq__(
            other,
            self.masquerades_as,
        ):
            return True
        return bool(str.__eq__(self, other))

    def __ne__(self, other: object) -> bool:
        return not self.__eq__(other)

    __hash__ = str.__hash__


class RehydratingDatetime(datetime):
    """A real 2026 instant that lies when converted to epoch microseconds."""

    def __sub__(self, other: object) -> timedelta:
        if (
            type(other) is datetime
            and other == datetime(1970, 1, 1, tzinfo=timezone.utc)
        ):
            return timedelta(0)
        return super().__sub__(other)  # type: ignore[arg-type]


class ReviewInvitationRecordSubclass(ReviewInvitationRecord):
    """A nominal-looking model subclass that the writer must reject."""


class ContractBindingSubclass(ContractBinding):
    """A nested binding subclass that the writer must reject."""


class TokenDigestSubclass(TokenDigest):
    """A nested digest subclass that the writer must reject."""


@pytest.fixture
def writer_class() -> type[Any]:
    writer = getattr(
        confirmation_ledger_module,
        "SQLiteInvitationWriter",
        None,
    )
    if writer is None:
        pytest.skip("SQLiteInvitationWriter has not landed yet.")
    return writer


def _epoch_microseconds(value: datetime) -> int:
    delta = value - datetime(1970, 1, 1, tzinfo=timezone.utc)
    return (
        delta.days * 86_400_000_000
        + delta.seconds * 1_000_000
        + delta.microseconds
    )


def _make_invitation(
    *,
    invitation_id: str = "review-primary",
    token_digest: str = "b" * 64,
    binding: ContractBinding = BINDING,
    token_digest_version: str = "sha256-v1",
    intended_organization_id: str = "customer-org",
    issued_by_subject: str = "seller-subject",
    issued_at: datetime = ISSUED_AT,
    expires_at: datetime | None = None,
) -> ReviewInvitationRecord:
    isolated_binding = ContractBinding(
        contract_id=binding.contract_id,
        contract_version=binding.contract_version,
        confirmation_fingerprint=binding.confirmation_fingerprint,
    )
    return ReviewInvitationRecord(
        invitation_id=invitation_id,
        binding=isolated_binding,
        token_digest=TokenDigest(token_digest),
        token_digest_version=token_digest_version,
        intended_organization_id=intended_organization_id,
        issued_by_subject=issued_by_subject,
        issued_at=issued_at,
        expires_at=expires_at or issued_at + timedelta(hours=2),
    )


def _bootstrap_path(tmp_path: Path) -> Path:
    database_path = tmp_path / "confirmation.db"
    connection = bootstrap_confirmation_ledger(
        database_path,
        migration_time=MIGRATION_TIME,
    )
    connection.close()
    return database_path


def _connection_factory(
    database_path: Path,
    *,
    opened: list[GuardedConnection] | None = None,
    creation_threads: list[int] | None = None,
    busy_timeout_ms: int = 5_000,
) -> Callable[[], GuardedConnection]:
    def create() -> GuardedConnection:
        if creation_threads is not None:
            creation_threads.append(threading.get_ident())
        connection = open_existing_confirmation_ledger(
            database_path,
            busy_timeout_ms=busy_timeout_ms,
        )
        if opened is not None:
            opened.append(connection)
        return connection

    return create


def _writer(
    writer_class: type[Any],
    database_path: Path,
    *,
    opened: list[GuardedConnection] | None = None,
    creation_threads: list[int] | None = None,
    busy_timeout_ms: int = 5_000,
) -> Any:
    return writer_class(
        _connection_factory(
            database_path,
            opened=opened,
            creation_threads=creation_threads,
            busy_timeout_ms=busy_timeout_ms,
        )
    )


def _assert_connection_closed(connection: GuardedConnection) -> None:
    with pytest.raises(sqlite3.ProgrammingError):
        connection.execute("SELECT 1")


def _raw_rows(
    database_path: Path,
    sql: str,
    parameters: object = (),
) -> list[sqlite3.Row]:
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    try:
        return connection.execute(sql, parameters).fetchall()
    finally:
        connection.close()


def _table_counts(database_path: Path) -> tuple[int, int]:
    row = _raw_rows(
        database_path,
        """
        SELECT
            (SELECT COUNT(*) FROM review_invitations) AS invitations,
            (
                SELECT COUNT(*)
                FROM confirmation_audit_events
            ) AS audits
        """,
    )[0]
    return row["invitations"], row["audits"]


def _invitation_rows(database_path: Path) -> list[sqlite3.Row]:
    return _raw_rows(
        database_path,
        """
        SELECT
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
        FROM review_invitations
        ORDER BY invitation_id ASC
        """,
    )


def _audit_rows(database_path: Path) -> list[sqlite3.Row]:
    return _raw_rows(
        database_path,
        """
        SELECT
            event_id,
            event_sequence,
            event_type,
            occurred_at_us,
            contract_id,
            contract_version,
            confirmation_fingerprint,
            invitation_id,
            confirmation_id,
            actor_issuer,
            actor_subject,
            actor_organization_id,
            outcome,
            reason_code,
            trace_id,
            metadata_schema_version,
            metadata_adapter_name,
            metadata_adapter_version
        FROM confirmation_audit_events
        ORDER BY event_sequence ASC
        """,
    )


def _assert_canonical_failure(action: Callable[[], object]) -> None:
    with pytest.raises(
        LedgerUnavailable,
        match=CANONICAL_LEDGER_ERROR,
    ) as error:
        action()
    assert type(error.value) is StoreLedgerUnavailable
    assert error.value.__cause__ is None


def _with_trigger_temporarily_removed(
    database_path: Path,
    trigger_name: str,
    operation: Callable[[sqlite3.Connection], None],
    *,
    ignore_check_constraints: bool = False,
) -> None:
    connection = sqlite3.connect(database_path)
    try:
        row = connection.execute(
            """
            SELECT sql
            FROM sqlite_master
            WHERE type = 'trigger' AND name = ?
            """,
            (trigger_name,),
        ).fetchone()
        assert row is not None and isinstance(row[0], str)
        trigger_sql = row[0]
        connection.execute(
            "DROP TRIGGER {0}".format(trigger_name)
        )
        if ignore_check_constraints:
            connection.execute("PRAGMA ignore_check_constraints = ON")
        operation(connection)
        connection.execute(trigger_sql)
        connection.commit()
    finally:
        connection.close()


def _delete_issuance_audit(
    database_path: Path,
    invitation_id: str,
) -> None:
    def delete(connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            DELETE FROM confirmation_audit_events
            WHERE invitation_id = ? AND event_type = 'INVITATION_ISSUED'
            """,
            (invitation_id,),
        )

    _with_trigger_temporarily_removed(
        database_path,
        "confirmation_audit_events_block_delete",
        delete,
    )


def _mismatch_issuance_audit(
    database_path: Path,
    invitation_id: str,
) -> None:
    def update(connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            UPDATE confirmation_audit_events
            SET occurred_at_us = occurred_at_us + 1
            WHERE invitation_id = ? AND event_type = 'INVITATION_ISSUED'
            """,
            (invitation_id,),
        )

    _with_trigger_temporarily_removed(
        database_path,
        "confirmation_audit_events_block_update",
        update,
    )


def _clone_issuance_audit(
    database_path: Path,
    invitation_id: str,
    *,
    cloned_invitation_id: str | None = None,
    event_id: str = "audit-test-clone",
) -> None:
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    try:
        source = connection.execute(
            """
            SELECT *
            FROM confirmation_audit_events
            WHERE invitation_id = ? AND event_type = 'INVITATION_ISSUED'
            ORDER BY event_sequence ASC
            LIMIT 1
            """,
            (invitation_id,),
        ).fetchone()
        assert source is not None
        next_sequence = connection.execute(
            """
            SELECT COALESCE(MAX(event_sequence), 0) + 1
            FROM confirmation_audit_events
            """
        ).fetchone()[0]
        values = {
            column: source[column]
            for column in AUDIT_COLUMNS
        }
        values.update(
            {
                "event_id": event_id,
                "event_sequence": next_sequence,
                "invitation_id": (
                    cloned_invitation_id or invitation_id
                ),
            }
        )
        connection.execute(
            """
            INSERT INTO confirmation_audit_events (
                event_id,
                event_sequence,
                event_type,
                occurred_at_us,
                contract_id,
                contract_version,
                confirmation_fingerprint,
                invitation_id,
                confirmation_id,
                actor_issuer,
                actor_subject,
                actor_organization_id,
                outcome,
                reason_code,
                trace_id,
                metadata_schema_version,
                metadata_adapter_name,
                metadata_adapter_version
            )
            VALUES (
                :event_id,
                :event_sequence,
                :event_type,
                :occurred_at_us,
                :contract_id,
                :contract_version,
                :confirmation_fingerprint,
                :invitation_id,
                :confirmation_id,
                :actor_issuer,
                :actor_subject,
                :actor_organization_id,
                :outcome,
                :reason_code,
                :trace_id,
                :metadata_schema_version,
                :metadata_adapter_name,
                :metadata_adapter_version
            )
            """,
            values,
        )
        connection.commit()
    finally:
        connection.close()


def _corrupt_issuance_audit(
    database_path: Path,
    invitation_id: str,
    corruption: str,
) -> None:
    if corruption == "missing":
        _delete_issuance_audit(database_path, invitation_id)
    elif corruption == "mismatched":
        _mismatch_issuance_audit(database_path, invitation_id)
    elif corruption == "duplicate":
        _clone_issuance_audit(
            database_path,
            invitation_id,
            event_id="audit-test-duplicate",
        )
    elif corruption == "orphan":
        _clone_issuance_audit(
            database_path,
            invitation_id,
            cloned_invitation_id="review-orphan",
            event_id="audit-test-orphan",
        )
    else:
        raise AssertionError("unknown test corruption")


def _tamper_invitation(
    database_path: Path,
    invitation_id: str,
    assignment: str,
    parameters: object,
    *,
    ignore_check_constraints: bool = False,
) -> None:
    def update(connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            UPDATE review_invitations
            SET {0}
            WHERE invitation_id = ?
            """.format(assignment),
            (*parameters, invitation_id),  # type: ignore[arg-type]
        )

    _with_trigger_temporarily_removed(
        database_path,
        "review_invitations_block_update",
        update,
        ignore_check_constraints=ignore_check_constraints,
    )


def _change_audit_sequence(
    database_path: Path,
    event_sequence: int,
    replacement: int,
) -> None:
    def update(connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            UPDATE confirmation_audit_events
            SET
                event_sequence = ?,
                event_id = ?
            WHERE event_sequence = ?
            """,
            (
                replacement,
                "audit-sqlite-{0:020d}".format(replacement),
                event_sequence,
            ),
        )

    _with_trigger_temporarily_removed(
        database_path,
        "confirmation_audit_events_block_update",
        update,
    )


def _insert_invitation_fixture(
    connection: sqlite3.Connection,
    invitation: ReviewInvitationRecord,
) -> None:
    connection.execute(
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
            invitation.invitation_id,
            invitation.binding.contract_id,
            invitation.binding.contract_version,
            invitation.binding.confirmation_fingerprint,
            invitation.token_digest.value,
            invitation.token_digest_version,
            invitation.intended_organization_id,
            invitation.issued_by_subject,
            _epoch_microseconds(invitation.issued_at),
            _epoch_microseconds(invitation.expires_at),
        ),
    )


def _insert_audit_fixture(
    connection: sqlite3.Connection,
    *,
    sequence: int,
    event_type: str,
    occurred_at: datetime,
    invitation_id: str,
    reason_code: str | None,
    binding: ContractBinding = BINDING,
    event_id: str | None = None,
    outcome: str = "SUCCEEDED",
    confirmation_id: str | None = None,
    metadata_adapter_version: str | None = None,
) -> None:
    connection.execute(
        """
        INSERT INTO confirmation_audit_events (
            event_id,
            event_sequence,
            event_type,
            occurred_at_us,
            contract_id,
            contract_version,
            confirmation_fingerprint,
            invitation_id,
            confirmation_id,
            actor_issuer,
            actor_subject,
            actor_organization_id,
            outcome,
            reason_code,
            trace_id,
            metadata_schema_version,
            metadata_adapter_name,
            metadata_adapter_version
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL,
                ?, ?, NULL, '1', 'sqlite', ?)
        """,
        (
            event_id or "audit-sqlite-{0:020d}".format(sequence),
            sequence,
            event_type,
            _epoch_microseconds(occurred_at),
            binding.contract_id,
            binding.contract_version,
            binding.confirmation_fingerprint,
            invitation_id,
            confirmation_id,
            outcome,
            reason_code,
            metadata_adapter_version,
        ),
    )


def _install_reissue_fixture(
    database_path: Path,
    writer_class: type[Any],
    *,
    provenance: str = "valid",
) -> tuple[ReviewInvitationRecord, ReviewInvitationRecord]:
    old_invitation = _make_invitation(
        invitation_id="review-old",
        token_digest="1" * 64,
    )
    replacement_time = ISSUED_AT + timedelta(minutes=30)
    replacement = _make_invitation(
        invitation_id="review-replacement",
        token_digest="2" * 64,
        issued_at=replacement_time,
        expires_at=replacement_time + timedelta(hours=2),
    )
    _writer(
        writer_class,
        database_path,
    ).issue_invitation(old_invitation)

    connection = sqlite3.connect(database_path)
    try:
        _insert_invitation_fixture(connection, replacement)
        connection.execute(
            """
            INSERT INTO invitation_revocations (
                invitation_id,
                revoked_at_us,
                revoked_by_subject,
                reason_code
            )
            VALUES (?, ?, ?, 'REISSUED')
            """,
            (
                old_invitation.invitation_id,
                _epoch_microseconds(replacement_time),
                "seller-subject",
            ),
        )
        _insert_audit_fixture(
            connection,
            sequence=2,
            event_type="INVITATION_REVOKED",
            occurred_at=replacement_time,
            invitation_id=old_invitation.invitation_id,
            reason_code="REISSUED",
        )
        reissue_time = replacement_time
        if provenance == "malformed":
            reissue_time += timedelta(microseconds=1)
        _insert_audit_fixture(
            connection,
            sequence=3,
            event_type="INVITATION_REISSUED",
            occurred_at=reissue_time,
            invitation_id=replacement.invitation_id,
            reason_code="REISSUED",
        )
        if provenance == "duplicate":
            _insert_audit_fixture(
                connection,
                sequence=4,
                event_type="INVITATION_REISSUED",
                occurred_at=replacement_time,
                invitation_id=replacement.invitation_id,
                reason_code="REISSUED",
            )
        elif provenance == "both":
            _insert_audit_fixture(
                connection,
                sequence=4,
                event_type="INVITATION_ISSUED",
                occurred_at=replacement_time,
                invitation_id=replacement.invitation_id,
                reason_code=None,
            )
        elif provenance not in {"valid", "malformed"}:
            raise AssertionError("unknown reissue provenance")
        connection.commit()
    finally:
        connection.close()
    return old_invitation, replacement


def _writer_state_snapshot(
    database_path: Path,
) -> tuple[
    list[tuple[object, ...]],
    list[tuple[object, ...]],
    list[tuple[object, ...]],
]:
    invitations = [
        tuple(row)
        for row in _raw_rows(
            database_path,
            "SELECT * FROM review_invitations ORDER BY invitation_id",
        )
    ]
    revocations = [
        tuple(row)
        for row in _raw_rows(
            database_path,
            "SELECT * FROM invitation_revocations ORDER BY invitation_id",
        )
    ]
    audits = [
        tuple(row)
        for row in _raw_rows(
            database_path,
            """
            SELECT *
            FROM confirmation_audit_events
            ORDER BY event_sequence, event_id
            """,
        )
    ]
    return invitations, revocations, audits


def _delete_reissue_counterpart(
    database_path: Path,
    old_invitation_id: str,
    counterpart: str,
) -> None:
    if counterpart == "revocation-row":
        table = "invitation_revocations"
        trigger = "invitation_revocations_block_delete"
        predicate = "invitation_id = ?"
    elif counterpart == "revoked-audit":
        table = "confirmation_audit_events"
        trigger = "confirmation_audit_events_block_delete"
        predicate = (
            "invitation_id = ? "
            "AND event_type = 'INVITATION_REVOKED'"
        )
    else:
        raise AssertionError("unknown reissue counterpart")

    def delete(connection: sqlite3.Connection) -> None:
        connection.execute(
            "DELETE FROM {0} WHERE {1}".format(table, predicate),
            (old_invitation_id,),
        )

    _with_trigger_temporarily_removed(
        database_path,
        trigger,
        delete,
    )


def _tamper_reissue_counterpart(
    database_path: Path,
    old_invitation_id: str,
    corruption: str,
) -> None:
    if corruption == "time":
        table = "invitation_revocations"
        trigger = "invitation_revocations_block_update"
        assignment = "revoked_at_us = revoked_at_us + 1"
        predicate = "invitation_id = ?"
        parameters: tuple[object, ...] = (old_invitation_id,)
    elif corruption == "reason":
        table = "invitation_revocations"
        trigger = "invitation_revocations_block_update"
        assignment = "reason_code = 'MANUAL'"
        predicate = "invitation_id = ?"
        parameters = (old_invitation_id,)
    elif corruption == "binding":
        table = "confirmation_audit_events"
        trigger = "confirmation_audit_events_block_update"
        assignment = "confirmation_fingerprint = ?"
        predicate = (
            "invitation_id = ? "
            "AND event_type = 'INVITATION_REVOKED'"
        )
        parameters = ("f" * 64, old_invitation_id)
    else:
        raise AssertionError("unknown reissue corruption")

    def update(connection: sqlite3.Connection) -> None:
        connection.execute(
            "UPDATE {0} SET {1} WHERE {2}".format(
                table,
                assignment,
                predicate,
            ),
            parameters,
        )

    _with_trigger_temporarily_removed(
        database_path,
        trigger,
        update,
    )


def _rewrite_audit_representation(
    database_path: Path,
    replacements: dict[str, str],
    *,
    adapter_version: str,
) -> None:
    def update(connection: sqlite3.Connection) -> None:
        for event_type, event_id in replacements.items():
            connection.execute(
                """
                UPDATE confirmation_audit_events
                SET event_id = ?, metadata_adapter_version = ?
                WHERE event_type = ?
                """,
                (event_id, adapter_version, event_type),
            )

    _with_trigger_temporarily_removed(
        database_path,
        "confirmation_audit_events_block_update",
        update,
    )


def _insert_revocation_fixture(
    connection: sqlite3.Connection,
    invitation: ReviewInvitationRecord,
    *,
    sequence: int,
    revoked_at: datetime,
    reason_code: str,
    include_row: bool = True,
    include_audit: bool = True,
    audit_time: datetime | None = None,
    audit_binding: ContractBinding | None = None,
    audit_reason: str | None = None,
    event_id: str | None = None,
) -> None:
    if include_row:
        connection.execute(
            """
            INSERT INTO invitation_revocations (
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
                reason_code,
            ),
        )
    if include_audit:
        _insert_audit_fixture(
            connection,
            sequence=sequence,
            event_type="INVITATION_REVOKED",
            occurred_at=audit_time or revoked_at,
            invitation_id=invitation.invitation_id,
            reason_code=audit_reason or reason_code,
            binding=audit_binding or invitation.binding,
            event_id=event_id,
        )


def _normalized_sql(sql: str) -> str:
    return " ".join(sql.upper().split())


def _tampered_invitation(case: str) -> ReviewInvitationRecord:
    invitation = _make_invitation()
    if case == "record":
        object.__setattr__(
            invitation,
            "intended_organization_id",
            object(),
        )
    elif case == "binding":
        object.__setattr__(
            invitation.binding,
            "confirmation_fingerprint",
            "A" * 64,
        )
    elif case == "token":
        object.__setattr__(
            invitation.token_digest,
            "value",
            "raw-review-token",
        )
    elif case == "issued-before-epoch":
        object.__setattr__(
            invitation,
            "issued_at",
            datetime(1969, 12, 31, 23, 59, tzinfo=timezone.utc),
        )
    elif case == "expires-before-epoch":
        object.__setattr__(
            invitation,
            "expires_at",
            datetime(1969, 12, 31, 23, 59, tzinfo=timezone.utc),
        )
    else:
        raise AssertionError("unknown tamper case")
    return invitation


def test_public_writer_surface_and_constructor_contract() -> None:
    writer = getattr(
        confirmation_ledger_module,
        "SQLiteInvitationWriter",
        None,
    )

    assert writer is not None
    assert "SQLiteInvitationWriter" in confirmation_ledger_module.__all__
    with pytest.raises(TypeError, match="connection_factory"):
        writer(None)


@pytest.mark.parametrize(
    ("case", "expected_error"),
    (
        ("record", ValueError),
        ("binding", ValueError),
        ("token", ValueError),
        ("issued-before-epoch", ValueError),
        ("expires-before-epoch", ValueError),
    ),
)
def test_tampered_domain_objects_and_pre_epoch_times_fail_before_factory(
    writer_class: type[Any],
    case: str,
    expected_error: type[Exception],
) -> None:
    factory_calls = 0

    def factory() -> GuardedConnection:
        nonlocal factory_calls
        factory_calls += 1
        raise AssertionError("invalid input must not call the factory")

    writer = writer_class(factory)
    with pytest.raises(expected_error):
        writer.issue_invitation(_tampered_invitation(case))

    assert factory_calls == 0


def test_non_record_input_fails_before_factory(
    writer_class: type[Any],
) -> None:
    factory_calls = 0

    def factory() -> GuardedConnection:
        nonlocal factory_calls
        factory_calls += 1
        raise AssertionError("invalid input must not call the factory")

    writer = writer_class(factory)
    with pytest.raises(TypeError, match="ReviewInvitationRecord"):
        writer.issue_invitation(object())

    assert factory_calls == 0


@pytest.mark.parametrize(
    "digest_version",
    ("SHA256-v1", "sha256-v1 ", "sha256-v2"),
)
def test_only_exact_lowercase_sha256_v1_reaches_factory(
    writer_class: type[Any],
    digest_version: str,
) -> None:
    factory_calls = 0

    def factory() -> GuardedConnection:
        nonlocal factory_calls
        factory_calls += 1
        raise AssertionError("invalid digest version must be preflighted")

    writer = writer_class(factory)
    with pytest.raises(ValueError, match="sha256-v1"):
        writer.issue_invitation(
            _make_invitation(token_digest_version=digest_version)
        )

    assert factory_calls == 0


@pytest.mark.parametrize(
    "model_kind",
    ("invitation", "binding", "token-digest"),
)
def test_model_subclasses_are_rejected_before_factory(
    tmp_path: Path,
    writer_class: type[Any],
    model_kind: str,
) -> None:
    database_path = _bootstrap_path(tmp_path)
    base = _make_invitation()
    if model_kind == "invitation":
        candidate = ReviewInvitationRecordSubclass(
            invitation_id=base.invitation_id,
            binding=base.binding,
            token_digest=base.token_digest,
            token_digest_version=base.token_digest_version,
            intended_organization_id=base.intended_organization_id,
            issued_by_subject=base.issued_by_subject,
            issued_at=base.issued_at,
            expires_at=base.expires_at,
        )
    elif model_kind == "binding":
        candidate = replace(
            base,
            binding=ContractBindingSubclass(
                contract_id=base.binding.contract_id,
                contract_version=base.binding.contract_version,
                confirmation_fingerprint=(
                    base.binding.confirmation_fingerprint
                ),
            ),
        )
    else:
        candidate = replace(
            base,
            token_digest=TokenDigestSubclass(base.token_digest.value),
        )
    factory_calls = 0

    def factory() -> GuardedConnection:
        nonlocal factory_calls
        factory_calls += 1
        return open_existing_confirmation_ledger(database_path)

    with pytest.raises(TypeError):
        writer_class(factory).issue_invitation(candidate)

    assert factory_calls == 0
    assert _table_counts(database_path) == (0, 0)


def test_digest_version_str_subclass_cannot_masquerade_as_sha256_v1(
    tmp_path: Path,
    writer_class: type[Any],
) -> None:
    database_path = _bootstrap_path(tmp_path)
    lying_version = MasqueradingStr("sha256-v2", "sha256-v1")
    assert str(lying_version) == "sha256-v2"
    assert lying_version == "sha256-v1"
    candidate = _make_invitation(
        token_digest_version=lying_version,
    )
    factory_calls = 0

    def factory() -> GuardedConnection:
        nonlocal factory_calls
        factory_calls += 1
        return open_existing_confirmation_ledger(database_path)

    with pytest.raises(ValueError, match="exact string"):
        writer_class(factory).issue_invitation(candidate)

    assert factory_calls == 0
    assert _table_counts(database_path) == (0, 0)


def test_invitation_id_str_subclass_cannot_fake_an_existing_replay(
    tmp_path: Path,
    writer_class: type[Any],
) -> None:
    database_path = _bootstrap_path(tmp_path)
    existing = _make_invitation(
        invitation_id="review-alpha",
        token_digest="1" * 64,
    )
    _writer(
        writer_class,
        database_path,
    ).issue_invitation(existing)
    before_invitations = [
        tuple(row) for row in _invitation_rows(database_path)
    ]
    before_audits = [tuple(row) for row in _audit_rows(database_path)]
    lying_id = MasqueradingStr("review-bravo", "review-alpha")
    assert str(lying_id) == "review-bravo"
    assert "review-alpha" == lying_id
    candidate = _make_invitation(
        invitation_id=lying_id,
        token_digest=existing.token_digest.value,
    )
    factory_calls = 0

    def factory() -> GuardedConnection:
        nonlocal factory_calls
        factory_calls += 1
        return open_existing_confirmation_ledger(database_path)

    with pytest.raises(ValueError, match="exact string"):
        writer_class(factory).issue_invitation(candidate)

    assert factory_calls == 0
    assert [
        tuple(row) for row in _invitation_rows(database_path)
    ] == before_invitations
    assert [
        tuple(row) for row in _audit_rows(database_path)
    ] == before_audits
    assert [
        row["invitation_id"] for row in _invitation_rows(database_path)
    ] == ["review-alpha"]


def test_datetime_subclass_cannot_persist_a_different_restart_instant(
    tmp_path: Path,
    writer_class: type[Any],
) -> None:
    database_path = _bootstrap_path(tmp_path)
    lying_issued_at = RehydratingDatetime(
        ISSUED_AT.year,
        ISSUED_AT.month,
        ISSUED_AT.day,
        ISSUED_AT.hour,
        ISSUED_AT.minute,
        ISSUED_AT.second,
        ISSUED_AT.microsecond,
        tzinfo=timezone.utc,
    )
    assert lying_issued_at == ISSUED_AT
    assert (
        lying_issued_at
        - datetime(1970, 1, 1, tzinfo=timezone.utc)
    ) == timedelta(0)
    candidate = _make_invitation(
        issued_at=lying_issued_at,
        expires_at=ISSUED_AT + timedelta(hours=2),
    )
    factory_calls = 0

    def factory() -> GuardedConnection:
        nonlocal factory_calls
        factory_calls += 1
        return open_existing_confirmation_ledger(database_path)

    with pytest.raises(ValueError, match="exact datetime"):
        writer_class(factory).issue_invitation(candidate)

    assert factory_calls == 0
    assert _table_counts(database_path) == (0, 0)


def test_fresh_issue_is_durable_and_rehydrates_exactly_after_restart(
    tmp_path: Path,
    writer_class: type[Any],
) -> None:
    database_path = _bootstrap_path(tmp_path)
    invitation = _make_invitation()
    opened: list[GuardedConnection] = []

    stored = _writer(
        writer_class,
        database_path,
        opened=opened,
    ).issue_invitation(invitation)

    assert stored == invitation
    assert len(opened) == 1
    _assert_connection_closed(opened[0])
    assert _table_counts(database_path) == (1, 1)

    reader = SQLiteConfirmationReader(
        _connection_factory(database_path)
    )
    restarted = reader.get_invitation(invitation.invitation_id)
    assert restarted == invitation
    assert restarted is not None
    assert restarted.issued_at.tzinfo is timezone.utc
    assert restarted.issued_at.microsecond == 654321
    assert restarted.expires_at.tzinfo is timezone.utc
    assert restarted.expires_at.microsecond == 654321


def test_issue_writes_one_exact_canonical_issuance_audit(
    tmp_path: Path,
    writer_class: type[Any],
) -> None:
    database_path = _bootstrap_path(tmp_path)
    invitation = _make_invitation()

    _writer(writer_class, database_path).issue_invitation(invitation)

    rows = _audit_rows(database_path)
    assert len(rows) == 1
    event = rows[0]
    assert AUDIT_EVENT_ID.fullmatch(event["event_id"])
    assert 7 <= len(event["event_id"]) <= 64
    assert event["event_sequence"] == 1
    assert event["event_type"] == "INVITATION_ISSUED"
    assert event["occurred_at_us"] == _epoch_microseconds(
        invitation.issued_at
    )
    assert event["contract_id"] == invitation.binding.contract_id
    assert (
        event["contract_version"]
        == invitation.binding.contract_version
    )
    assert (
        event["confirmation_fingerprint"]
        == invitation.binding.confirmation_fingerprint
    )
    assert event["invitation_id"] == invitation.invitation_id
    assert event["confirmation_id"] is None
    assert event["actor_issuer"] is None
    assert event["actor_subject"] is None
    assert event["actor_organization_id"] is None
    assert event["outcome"] == "SUCCEEDED"
    assert event["reason_code"] is None
    assert event["trace_id"] is None
    assert event["metadata_schema_version"] == "1"
    assert event["metadata_adapter_name"] == "sqlite"
    assert event["metadata_adapter_version"] is None


def test_raw_token_never_reaches_rows_audits_or_database_files(
    tmp_path: Path,
    writer_class: type[Any],
) -> None:
    database_path = _bootstrap_path(tmp_path)
    raw_token = "review-secret-never-persist-" + "z" * 48
    digest = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    invitation = _make_invitation(token_digest=digest)

    _writer(writer_class, database_path).issue_invitation(invitation)

    all_values = [
        value
        for row in _invitation_rows(database_path) + _audit_rows(database_path)
        for value in row
        if isinstance(value, str)
    ]
    assert raw_token not in all_values
    assert digest in all_values

    checkpoint = sqlite3.connect(database_path)
    try:
        checkpoint.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        checkpoint.close()
    for candidate in (
        database_path,
        Path(str(database_path) + "-wal"),
        Path(str(database_path) + "-shm"),
    ):
        if candidate.exists():
            assert raw_token.encode("utf-8") not in candidate.read_bytes()


def test_sql_looking_literal_text_is_bound_data_not_executable_sql(
    tmp_path: Path,
    writer_class: type[Any],
) -> None:
    database_path = _bootstrap_path(tmp_path)
    sql_text = "support'); DROP TABLE review_invitations; --"
    invitation = _make_invitation(
        binding=ContractBinding(
            contract_id=sql_text,
            contract_version="v1'; SELECT * FROM sqlite_master; --",
            confirmation_fingerprint="c" * 64,
        ),
        intended_organization_id="org'); DELETE FROM confirmation_audit_events; --",
        issued_by_subject="seller'); VACUUM; --",
    )

    assert (
        _writer(writer_class, database_path).issue_invitation(invitation)
        == invitation
    )
    assert _table_counts(database_path) == (1, 1)
    assert _raw_rows(
        database_path,
        """
        SELECT COUNT(*) AS table_count
        FROM sqlite_master
        WHERE type = 'table'
          AND name IN (
              'review_invitations',
              'confirmation_audit_events'
          )
        """,
    )[0]["table_count"] == 2
    assert _invitation_rows(database_path)[0]["contract_id"] == sql_text


def test_utc_offset_zero_is_normalized_without_losing_microseconds(
    tmp_path: Path,
    writer_class: type[Any],
) -> None:
    database_path = _bootstrap_path(tmp_path)
    offset_zero = timezone(timedelta(0), "CUSTOM-UTC")
    issued_at = datetime(
        2026,
        7,
        25,
        1,
        2,
        3,
        987654,
        tzinfo=offset_zero,
    )
    invitation = _make_invitation(
        issued_at=issued_at,
        expires_at=issued_at + timedelta(minutes=17, microseconds=5),
    )

    stored = _writer(
        writer_class,
        database_path,
    ).issue_invitation(invitation)
    restarted = SQLiteConfirmationReader(
        _connection_factory(database_path)
    ).get_invitation(invitation.invitation_id)

    assert stored == invitation
    assert restarted is not None
    assert restarted.issued_at.tzinfo is timezone.utc
    assert restarted.issued_at.microsecond == 987654
    assert restarted.expires_at.tzinfo is timezone.utc
    assert restarted.expires_at.microsecond == 987659


def test_exact_replay_returns_stored_record_and_appends_nothing(
    tmp_path: Path,
    writer_class: type[Any],
) -> None:
    database_path = _bootstrap_path(tmp_path)
    invitation = _make_invitation()
    writer = _writer(writer_class, database_path)

    first = writer.issue_invitation(invitation)
    before_rows = (
        [tuple(row) for row in _invitation_rows(database_path)],
        [tuple(row) for row in _audit_rows(database_path)],
    )
    replayed = writer.issue_invitation(invitation)

    assert first == invitation
    assert replayed == first
    assert _table_counts(database_path) == (1, 1)
    assert (
        [tuple(row) for row in _invitation_rows(database_path)],
        [tuple(row) for row in _audit_rows(database_path)],
    ) == before_rows


def test_exact_replay_survives_process_style_restart(
    tmp_path: Path,
    writer_class: type[Any],
) -> None:
    database_path = _bootstrap_path(tmp_path)
    invitation = _make_invitation()
    _writer(writer_class, database_path).issue_invitation(invitation)

    restarted_writer = _writer(writer_class, database_path)
    assert restarted_writer.issue_invitation(invitation) == invitation
    assert _table_counts(database_path) == (1, 1)


@pytest.mark.parametrize(
    ("conflict", "expected_error"),
    (
        ("identity", InvitationIdentityConflict),
        ("token", TokenDigestConflict),
        ("binding", ContractBindingMismatch),
    ),
)
def test_conflict_precedence_is_identity_then_token_then_binding(
    tmp_path: Path,
    writer_class: type[Any],
    conflict: str,
    expected_error: type[Exception],
) -> None:
    database_path = _bootstrap_path(tmp_path)
    writer = _writer(writer_class, database_path)
    primary = _make_invitation(
        invitation_id="review-alpha",
        token_digest="1" * 64,
    )
    digest_owner = _make_invitation(
        invitation_id="review-bravo",
        token_digest="2" * 64,
    )
    writer.issue_invitation(primary)
    writer.issue_invitation(digest_owner)
    conflicting_binding = ContractBinding(
        contract_id=BINDING.contract_id,
        contract_version=BINDING.contract_version,
        confirmation_fingerprint="f" * 64,
    )
    if conflict == "identity":
        candidate = _make_invitation(
            invitation_id=primary.invitation_id,
            token_digest=digest_owner.token_digest.value,
            binding=conflicting_binding,
        )
    elif conflict == "token":
        candidate = _make_invitation(
            invitation_id="review-charlie",
            token_digest=digest_owner.token_digest.value,
            binding=conflicting_binding,
        )
    else:
        candidate = _make_invitation(
            invitation_id="review-delta",
            token_digest="4" * 64,
            binding=conflicting_binding,
        )

    before = _table_counts(database_path)
    opened: list[GuardedConnection] = []
    with pytest.raises(expected_error):
        _writer(
            writer_class,
            database_path,
            opened=opened,
        ).issue_invitation(candidate)

    assert _table_counts(database_path) == before
    assert len(opened) == 1
    _assert_connection_closed(opened[0])


@pytest.mark.parametrize(
    "operation",
    ("match-first", "match-middle", "miss"),
)
def test_every_persisted_digest_is_compared_without_early_exit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    writer_class: type[Any],
    operation: str,
) -> None:
    database_path = _bootstrap_path(tmp_path)
    writer = _writer(writer_class, database_path)
    invitations = (
        _make_invitation(
            invitation_id="review-alpha",
            token_digest="1" * 64,
        ),
        _make_invitation(
            invitation_id="review-bravo",
            token_digest="2" * 64,
        ),
        _make_invitation(
            invitation_id="review-charlie",
            token_digest="3" * 64,
        ),
    )
    for invitation in invitations:
        writer.issue_invitation(invitation)

    calls: list[tuple[str, str]] = []
    original_compare = confirmation_ledger_module.hmac.compare_digest

    def recording_compare(left: str, right: str) -> bool:
        calls.append((left, right))
        return original_compare(left, right)

    monkeypatch.setattr(
        confirmation_ledger_module.hmac,
        "compare_digest",
        recording_compare,
    )
    if operation == "match-first":
        candidate = invitations[0]
    elif operation == "match-middle":
        candidate = invitations[1]
    else:
        candidate = _make_invitation(
            invitation_id="review-delta",
            token_digest="4" * 64,
        )

    assert writer.issue_invitation(candidate) == candidate
    assert calls == [
        ("1" * 64, candidate.token_digest.value),
        ("2" * 64, candidate.token_digest.value),
        ("3" * 64, candidate.token_digest.value),
    ]


@pytest.mark.parametrize(
    "candidate_digest",
    ("1" * 64, "4" * 64),
    ids=("match", "miss"),
)
def test_corrupt_unicode_digest_fails_only_after_all_valid_rows_are_scanned(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    writer_class: type[Any],
    candidate_digest: str,
) -> None:
    database_path = _bootstrap_path(tmp_path)
    writer = _writer(writer_class, database_path)
    for invitation_id, digest in (
        ("review-alpha", "1" * 64),
        ("review-bravo", "2" * 64),
        ("review-charlie", "3" * 64),
    ):
        writer.issue_invitation(
            _make_invitation(
                invitation_id=invitation_id,
                token_digest=digest,
            )
        )
    corrupt_digest = "\N{LATIN SMALL LETTER E WITH ACUTE}" * 64
    _tamper_invitation(
        database_path,
        "review-bravo",
        "token_digest = ?",
        (corrupt_digest,),
        ignore_check_constraints=True,
    )

    calls: list[tuple[str, str]] = []
    original_compare = confirmation_ledger_module.hmac.compare_digest

    def recording_compare(left: str, right: str) -> bool:
        calls.append((left, right))
        return original_compare(left, right)

    monkeypatch.setattr(
        confirmation_ledger_module.hmac,
        "compare_digest",
        recording_compare,
    )
    candidate = (
        _make_invitation(
            invitation_id="review-alpha",
            token_digest=candidate_digest,
        )
        if candidate_digest == "1" * 64
        else _make_invitation(
            invitation_id="review-delta",
            token_digest=candidate_digest,
        )
    )

    _assert_canonical_failure(
        lambda: writer.issue_invitation(candidate)
    )
    assert calls == [
        ("1" * 64, candidate_digest),
        ("0" * 64, candidate_digest),
        ("3" * 64, candidate_digest),
    ]
    assert corrupt_digest not in CANONICAL_LEDGER_ERROR


@pytest.mark.parametrize(
    "corruption",
    ("unsupported-version", "malformed-digest", "invalid-expiry"),
)
def test_any_corrupt_persisted_invitation_blocks_a_new_issue(
    tmp_path: Path,
    writer_class: type[Any],
    corruption: str,
) -> None:
    database_path = _bootstrap_path(tmp_path)
    writer = _writer(writer_class, database_path)
    existing = _make_invitation(
        invitation_id="review-existing",
        token_digest="1" * 64,
    )
    writer.issue_invitation(existing)
    if corruption == "unsupported-version":
        _tamper_invitation(
            database_path,
            existing.invitation_id,
            "token_digest_version = ?",
            ("sha256-v2",),
        )
    elif corruption == "malformed-digest":
        _tamper_invitation(
            database_path,
            existing.invitation_id,
            "token_digest = ?",
            ("A" * 64,),
            ignore_check_constraints=True,
        )
    else:
        _tamper_invitation(
            database_path,
            existing.invitation_id,
            "expires_at_us = issued_at_us",
            (),
            ignore_check_constraints=True,
        )
    before = _table_counts(database_path)

    _assert_canonical_failure(
        lambda: writer.issue_invitation(
            _make_invitation(
                invitation_id="review-new",
                token_digest="2" * 64,
            )
        )
    )
    assert _table_counts(database_path) == before


@pytest.mark.parametrize(
    "corruption",
    ("missing", "mismatched", "duplicate"),
)
def test_target_replay_requires_one_exact_matching_issuance_audit(
    tmp_path: Path,
    writer_class: type[Any],
    corruption: str,
) -> None:
    database_path = _bootstrap_path(tmp_path)
    invitation = _make_invitation()
    writer = _writer(writer_class, database_path)
    writer.issue_invitation(invitation)
    _corrupt_issuance_audit(
        database_path,
        invitation.invitation_id,
        corruption,
    )
    before = _table_counts(database_path)

    _assert_canonical_failure(
        lambda: writer.issue_invitation(invitation)
    )
    assert _table_counts(database_path) == before


@pytest.mark.parametrize(
    "corruption",
    ("missing", "mismatched", "duplicate", "orphan"),
)
@pytest.mark.parametrize("operation", ("new-issue", "replay"))
def test_unrelated_global_issuance_corruption_blocks_issue_and_replay(
    tmp_path: Path,
    writer_class: type[Any],
    corruption: str,
    operation: str,
) -> None:
    database_path = _bootstrap_path(tmp_path)
    writer = _writer(writer_class, database_path)
    target = _make_invitation(
        invitation_id="review-target",
        token_digest="1" * 64,
    )
    unrelated = _make_invitation(
        invitation_id="review-unrelated",
        token_digest="2" * 64,
    )
    writer.issue_invitation(target)
    writer.issue_invitation(unrelated)
    _corrupt_issuance_audit(
        database_path,
        unrelated.invitation_id,
        corruption,
    )
    candidate = (
        target
        if operation == "replay"
        else _make_invitation(
            invitation_id="review-new",
            token_digest="3" * 64,
        )
    )
    before_rows = (
        [tuple(row) for row in _invitation_rows(database_path)],
        [tuple(row) for row in _audit_rows(database_path)],
    )

    _assert_canonical_failure(
        lambda: writer.issue_invitation(candidate)
    )
    assert (
        [tuple(row) for row in _invitation_rows(database_path)],
        [tuple(row) for row in _audit_rows(database_path)],
    ) == before_rows


def test_valid_reissue_provenance_allows_later_issue_and_replay(
    tmp_path: Path,
    writer_class: type[Any],
) -> None:
    database_path = _bootstrap_path(tmp_path)
    old_invitation, replacement = _install_reissue_fixture(
        database_path,
        writer_class,
    )
    writer = _writer(writer_class, database_path)

    replacement_events = [
        row
        for row in _audit_rows(database_path)
        if row["invitation_id"] == replacement.invitation_id
    ]
    assert [
        row["event_type"] for row in replacement_events
    ] == ["INVITATION_REISSUED"]
    assert _raw_rows(
        database_path,
        """
        SELECT invitation_id, reason_code
        FROM invitation_revocations
        """,
    )[0]["invitation_id"] == old_invitation.invitation_id

    assert writer.issue_invitation(replacement) == replacement
    assert _table_counts(database_path) == (2, 3)
    reader = SQLiteConfirmationReader(
        _connection_factory(database_path)
    )
    with pytest.raises(InvitationRevoked):
        reader.resolve_ledger_invitation(
            old_invitation.token_digest,
            replacement.issued_at,
        )

    fresh = _make_invitation(
        invitation_id="review-fresh",
        token_digest="3" * 64,
    )
    assert writer.issue_invitation(fresh) == fresh
    assert writer.issue_invitation(fresh) == fresh
    assert _table_counts(database_path) == (3, 4)
    assert [
        row["event_sequence"] for row in _audit_rows(database_path)
    ] == [1, 2, 3, 4]


def test_equal_time_reissue_is_accepted(
    tmp_path: Path,
    writer_class: type[Any],
) -> None:
    database_path = _bootstrap_path(tmp_path)
    writer = _writer(writer_class, database_path)
    source = _make_invitation(
        invitation_id="review-equal-time-source",
        token_digest="1" * 64,
    )
    replacement = _make_invitation(
        invitation_id="review-equal-time-replacement",
        token_digest="2" * 64,
        issued_at=source.issued_at,
        expires_at=source.expires_at,
    )
    writer.issue_invitation(source)
    connection = sqlite3.connect(database_path)
    try:
        _insert_invitation_fixture(connection, replacement)
        _insert_revocation_fixture(
            connection,
            source,
            sequence=2,
            revoked_at=replacement.issued_at,
            reason_code="REISSUED",
        )
        _insert_audit_fixture(
            connection,
            sequence=3,
            event_type="INVITATION_REISSUED",
            occurred_at=replacement.issued_at,
            invitation_id=replacement.invitation_id,
            reason_code="REISSUED",
        )
        connection.commit()
    finally:
        connection.close()

    before = _writer_state_snapshot(database_path)
    assert writer.issue_invitation(replacement) == replacement
    assert _writer_state_snapshot(database_path) == before
    reader = SQLiteConfirmationReader(
        _connection_factory(database_path)
    )
    assert reader.resolve_ledger_invitation(
        replacement.token_digest,
        replacement.issued_at,
    ) == replacement
    with pytest.raises(InvitationRevoked):
        reader.resolve_ledger_invitation(
            source.token_digest,
            source.issued_at,
        )


def test_expired_pending_source_can_be_reissued_to_active_replacement(
    tmp_path: Path,
    writer_class: type[Any],
) -> None:
    database_path = _bootstrap_path(tmp_path)
    writer = _writer(writer_class, database_path)
    source = _make_invitation(
        invitation_id="review-expired-source",
        token_digest="1" * 64,
        expires_at=ISSUED_AT + timedelta(minutes=5),
    )
    replacement_time = source.expires_at + timedelta(minutes=1)
    replacement = _make_invitation(
        invitation_id="review-after-expiry",
        token_digest="2" * 64,
        issued_at=replacement_time,
        expires_at=replacement_time + timedelta(hours=2),
    )
    writer.issue_invitation(source)
    connection = sqlite3.connect(database_path)
    try:
        _insert_invitation_fixture(connection, replacement)
        _insert_revocation_fixture(
            connection,
            source,
            sequence=2,
            revoked_at=replacement_time,
            reason_code="REISSUED",
        )
        _insert_audit_fixture(
            connection,
            sequence=3,
            event_type="INVITATION_REISSUED",
            occurred_at=replacement_time,
            invitation_id=replacement.invitation_id,
            reason_code="REISSUED",
        )
        connection.commit()
    finally:
        connection.close()

    assert writer.issue_invitation(replacement) == replacement
    reader = SQLiteConfirmationReader(
        _connection_factory(database_path)
    )
    assert reader.resolve_ledger_invitation(
        replacement.token_digest,
        replacement_time,
    ) == replacement
    with pytest.raises(InvitationRevoked):
        reader.resolve_ledger_invitation(
            source.token_digest,
            replacement_time,
        )


def test_valid_chained_reissues_allow_replay_and_later_issue(
    tmp_path: Path,
    writer_class: type[Any],
) -> None:
    database_path = _bootstrap_path(tmp_path)
    old_invitation, first_replacement = _install_reissue_fixture(
        database_path,
        writer_class,
    )
    second_reissue_time = first_replacement.issued_at + timedelta(
        minutes=30
    )
    second_replacement = _make_invitation(
        invitation_id="review-second-replacement",
        token_digest="3" * 64,
        issued_at=second_reissue_time,
        expires_at=second_reissue_time + timedelta(hours=2),
    )
    connection = sqlite3.connect(database_path)
    try:
        _insert_invitation_fixture(connection, second_replacement)
        _insert_revocation_fixture(
            connection,
            first_replacement,
            sequence=4,
            revoked_at=second_reissue_time,
            reason_code="REISSUED",
        )
        _insert_audit_fixture(
            connection,
            sequence=5,
            event_type="INVITATION_REISSUED",
            occurred_at=second_reissue_time,
            invitation_id=second_replacement.invitation_id,
            reason_code="REISSUED",
        )
        connection.commit()
    finally:
        connection.close()
    writer = _writer(writer_class, database_path)

    assert writer.issue_invitation(second_replacement) == second_replacement
    reader = SQLiteConfirmationReader(
        _connection_factory(database_path)
    )
    for revoked in (old_invitation, first_replacement):
        with pytest.raises(InvitationRevoked):
            reader.resolve_ledger_invitation(
                revoked.token_digest,
                second_reissue_time,
            )
    fresh = _make_invitation(
        invitation_id="review-after-chain",
        token_digest="4" * 64,
    )
    assert writer.issue_invitation(fresh) == fresh
    assert _table_counts(database_path) == (4, 6)
    assert len(
        _raw_rows(
            database_path,
            "SELECT * FROM invitation_revocations",
        )
    ) == 2


@pytest.mark.parametrize(
    "provenance",
    ("malformed", "duplicate", "both"),
)
@pytest.mark.parametrize("operation", ("new-issue", "replay"))
def test_invalid_reissue_creation_provenance_fails_closed(
    tmp_path: Path,
    writer_class: type[Any],
    provenance: str,
    operation: str,
) -> None:
    database_path = _bootstrap_path(tmp_path)
    _, replacement = _install_reissue_fixture(
        database_path,
        writer_class,
        provenance=provenance,
    )
    candidate = (
        replacement
        if operation == "replay"
        else _make_invitation(
            invitation_id="review-fresh",
            token_digest="3" * 64,
        )
    )
    before_rows = (
        [tuple(row) for row in _invitation_rows(database_path)],
        [tuple(row) for row in _audit_rows(database_path)],
    )

    _assert_canonical_failure(
        lambda: _writer(
            writer_class,
            database_path,
        ).issue_invitation(candidate)
    )

    assert (
        [tuple(row) for row in _invitation_rows(database_path)],
        [tuple(row) for row in _audit_rows(database_path)],
    ) == before_rows


@pytest.mark.parametrize(
    ("event_type", "reason_code", "confirmation_id"),
    (
        ("INVITATION_REVOKED", "MANUAL", None),
        ("DECISION_RECORDED", None, "cnf_{0}".format("d" * 64)),
    ),
)
def test_orphan_success_audit_fails_closed_without_mutation(
    tmp_path: Path,
    writer_class: type[Any],
    event_type: str,
    reason_code: str | None,
    confirmation_id: str | None,
) -> None:
    database_path = _bootstrap_path(tmp_path)
    candidate = _make_invitation(
        invitation_id="review-orphan-success",
    )
    connection = sqlite3.connect(database_path)
    try:
        _insert_audit_fixture(
            connection,
            sequence=1,
            event_type=event_type,
            occurred_at=ISSUED_AT,
            invitation_id=candidate.invitation_id,
            reason_code=reason_code,
            confirmation_id=confirmation_id,
            event_id="audit-orphan-success",
        )
        connection.commit()
    finally:
        connection.close()
    before = _writer_state_snapshot(database_path)

    _assert_canonical_failure(
        lambda: _writer(
            writer_class,
            database_path,
        ).issue_invitation(candidate)
    )

    assert _writer_state_snapshot(database_path) == before


@pytest.mark.parametrize(
    "event_type",
    ("INVITATION_REJECTED", "DECISION_REJECTED"),
)
def test_orphan_rejected_audit_allows_later_same_binding_issue(
    tmp_path: Path,
    writer_class: type[Any],
    event_type: str,
) -> None:
    database_path = _bootstrap_path(tmp_path)
    candidate = _make_invitation(
        invitation_id="review-rejected-attempt",
    )
    connection = sqlite3.connect(database_path)
    try:
        _insert_audit_fixture(
            connection,
            sequence=1,
            event_type=event_type,
            occurred_at=ISSUED_AT - timedelta(minutes=1),
            invitation_id=candidate.invitation_id,
            reason_code="INVALID_REQUEST",
            outcome="REJECTED",
            event_id="audit-rejected-attempt",
        )
        connection.commit()
    finally:
        connection.close()

    assert (
        _writer(
            writer_class,
            database_path,
        ).issue_invitation(candidate)
        == candidate
    )
    assert _table_counts(database_path) == (1, 2)
    assert [
        row["event_type"] for row in _audit_rows(database_path)
    ] == [event_type, "INVITATION_ISSUED"]


@pytest.mark.parametrize(
    ("event_type", "outcome"),
    (
        ("DECISION_RECORDED", "SUCCEEDED"),
        ("DECISION_REPLAYED", "REPLAYED"),
    ),
)
def test_stored_invitation_allows_future_decision_audit_without_table_pairing(
    tmp_path: Path,
    writer_class: type[Any],
    event_type: str,
    outcome: str,
) -> None:
    database_path = _bootstrap_path(tmp_path)
    writer = _writer(writer_class, database_path)
    invitation = _make_invitation()
    writer.issue_invitation(invitation)
    connection = sqlite3.connect(database_path)
    try:
        _insert_audit_fixture(
            connection,
            sequence=2,
            event_type=event_type,
            occurred_at=ISSUED_AT + timedelta(minutes=1),
            invitation_id=invitation.invitation_id,
            reason_code=None,
            confirmation_id="cnf_{0}".format("d" * 64),
            outcome=outcome,
            event_id="audit-future-decision",
        )
        connection.commit()
    finally:
        connection.close()

    fresh = _make_invitation(
        invitation_id="review-after-future-audit",
        token_digest="2" * 64,
    )
    assert writer.issue_invitation(fresh) == fresh
    assert _table_counts(database_path) == (2, 3)


def test_complete_manual_revocation_pair_allows_later_issue(
    tmp_path: Path,
    writer_class: type[Any],
) -> None:
    database_path = _bootstrap_path(tmp_path)
    writer = _writer(writer_class, database_path)
    invitation = _make_invitation()
    writer.issue_invitation(invitation)
    revoked_at = ISSUED_AT + timedelta(minutes=15)
    connection = sqlite3.connect(database_path)
    try:
        _insert_revocation_fixture(
            connection,
            invitation,
            sequence=2,
            revoked_at=revoked_at,
            reason_code="MANUAL",
        )
        connection.commit()
    finally:
        connection.close()

    fresh = _make_invitation(
        invitation_id="review-after-revocation",
        token_digest="2" * 64,
    )
    assert writer.issue_invitation(fresh) == fresh
    with pytest.raises(InvitationRevoked):
        SQLiteConfirmationReader(
            _connection_factory(database_path)
        ).resolve_ledger_invitation(
            invitation.token_digest,
            revoked_at,
        )
    assert _table_counts(database_path) == (2, 3)


@pytest.mark.parametrize(
    "corruption",
    (
        "audit-without-row",
        "row-without-audit",
        "mismatched-time",
        "mismatched-binding",
        "mismatched-reason",
        "duplicate-audit",
    ),
)
def test_revocation_row_and_semantic_audit_are_one_to_one(
    tmp_path: Path,
    writer_class: type[Any],
    corruption: str,
) -> None:
    database_path = _bootstrap_path(tmp_path)
    writer = _writer(writer_class, database_path)
    invitation = _make_invitation()
    writer.issue_invitation(invitation)
    revoked_at = ISSUED_AT + timedelta(minutes=15)
    connection = sqlite3.connect(database_path)
    try:
        _insert_revocation_fixture(
            connection,
            invitation,
            sequence=2,
            revoked_at=revoked_at,
            reason_code="MANUAL",
            include_row=corruption != "audit-without-row",
            include_audit=corruption != "row-without-audit",
            audit_time=(
                revoked_at + timedelta(microseconds=1)
                if corruption == "mismatched-time"
                else revoked_at
            ),
            audit_binding=invitation.binding,
            audit_reason=(
                "SECURITY_RESPONSE"
                if corruption == "mismatched-reason"
                else "MANUAL"
            ),
        )
        if corruption == "duplicate-audit":
            _insert_audit_fixture(
                connection,
                sequence=3,
                event_type="INVITATION_REVOKED",
                occurred_at=revoked_at,
                invitation_id=invitation.invitation_id,
                reason_code="MANUAL",
                event_id="audit-duplicate-revocation",
            )
        connection.commit()
    finally:
        connection.close()
    if corruption == "mismatched-binding":
        _tamper_reissue_counterpart(
            database_path,
            invitation.invitation_id,
            "binding",
        )
    before = _writer_state_snapshot(database_path)

    _assert_canonical_failure(
        lambda: writer.issue_invitation(
            _make_invitation(
                invitation_id="review-after-corruption",
                token_digest="2" * 64,
            )
        )
    )

    assert _writer_state_snapshot(database_path) == before


def test_reissued_revocation_without_replacement_creation_fails_closed(
    tmp_path: Path,
    writer_class: type[Any],
) -> None:
    database_path = _bootstrap_path(tmp_path)
    writer = _writer(writer_class, database_path)
    invitation = _make_invitation()
    writer.issue_invitation(invitation)
    connection = sqlite3.connect(database_path)
    try:
        _insert_revocation_fixture(
            connection,
            invitation,
            sequence=2,
            revoked_at=ISSUED_AT + timedelta(minutes=15),
            reason_code="REISSUED",
        )
        connection.commit()
    finally:
        connection.close()
    before = _writer_state_snapshot(database_path)

    _assert_canonical_failure(
        lambda: writer.issue_invitation(
            _make_invitation(
                invitation_id="review-after-incomplete-reissue",
                token_digest="2" * 64,
            )
        )
    )

    assert _writer_state_snapshot(database_path) == before


@pytest.mark.parametrize(
    "corruption",
    (
        "missing-revocation-row",
        "missing-revoked-audit",
        "time",
        "binding",
        "reason",
        "duplicate-revoked-audit",
    ),
)
def test_reissue_requires_one_exact_atomic_revocation_counterpart(
    tmp_path: Path,
    writer_class: type[Any],
    corruption: str,
) -> None:
    database_path = _bootstrap_path(tmp_path)
    old_invitation, _ = _install_reissue_fixture(
        database_path,
        writer_class,
    )
    if corruption == "missing-revocation-row":
        _delete_reissue_counterpart(
            database_path,
            old_invitation.invitation_id,
            "revocation-row",
        )
    elif corruption == "missing-revoked-audit":
        _delete_reissue_counterpart(
            database_path,
            old_invitation.invitation_id,
            "revoked-audit",
        )
    elif corruption in {"time", "binding", "reason"}:
        _tamper_reissue_counterpart(
            database_path,
            old_invitation.invitation_id,
            corruption,
        )
    elif corruption == "duplicate-revoked-audit":
        connection = sqlite3.connect(database_path)
        try:
            _insert_audit_fixture(
                connection,
                sequence=4,
                event_type="INVITATION_REVOKED",
                occurred_at=ISSUED_AT + timedelta(minutes=30),
                invitation_id=old_invitation.invitation_id,
                reason_code="REISSUED",
                event_id="audit-duplicate-revoked",
            )
            connection.commit()
        finally:
            connection.close()
    else:
        raise AssertionError("unknown reissue corruption")
    before = _writer_state_snapshot(database_path)

    _assert_canonical_failure(
        lambda: _writer(
            writer_class,
            database_path,
        ).issue_invitation(
            _make_invitation(
                invitation_id="review-after-corrupt-reissue",
                token_digest="3" * 64,
            )
        )
    )

    assert _writer_state_snapshot(database_path) == before


def test_reissue_rejects_ambiguous_atomic_revocation_counterpart(
    tmp_path: Path,
    writer_class: type[Any],
) -> None:
    database_path = _bootstrap_path(tmp_path)
    writer = _writer(writer_class, database_path)
    old_invitations = (
        _make_invitation(
            invitation_id="review-old-alpha",
            token_digest="1" * 64,
        ),
        _make_invitation(
            invitation_id="review-old-bravo",
            token_digest="2" * 64,
        ),
    )
    for invitation in old_invitations:
        writer.issue_invitation(invitation)
    replacement_time = ISSUED_AT + timedelta(minutes=30)
    replacement = _make_invitation(
        invitation_id="review-replacement-ambiguous",
        token_digest="3" * 64,
        issued_at=replacement_time,
        expires_at=replacement_time + timedelta(hours=2),
    )
    connection = sqlite3.connect(database_path)
    try:
        _insert_invitation_fixture(connection, replacement)
        for sequence, old_invitation in enumerate(
            old_invitations,
            start=3,
        ):
            connection.execute(
                """
                INSERT INTO invitation_revocations (
                    invitation_id,
                    revoked_at_us,
                    revoked_by_subject,
                    reason_code
                )
                VALUES (?, ?, ?, 'REISSUED')
                """,
                (
                    old_invitation.invitation_id,
                    _epoch_microseconds(replacement_time),
                    "seller-subject",
                ),
            )
            _insert_audit_fixture(
                connection,
                sequence=sequence,
                event_type="INVITATION_REVOKED",
                occurred_at=replacement_time,
                invitation_id=old_invitation.invitation_id,
                reason_code="REISSUED",
            )
        _insert_audit_fixture(
            connection,
            sequence=5,
            event_type="INVITATION_REISSUED",
            occurred_at=replacement_time,
            invitation_id=replacement.invitation_id,
            reason_code="REISSUED",
        )
        connection.commit()
    finally:
        connection.close()
    before = _writer_state_snapshot(database_path)

    _assert_canonical_failure(
        lambda: _writer(
            writer_class,
            database_path,
        ).issue_invitation(replacement)
    )

    assert _writer_state_snapshot(database_path) == before


def test_schema_v1_initial_issuance_representation_is_accepted(
    tmp_path: Path,
    writer_class: type[Any],
) -> None:
    database_path = _bootstrap_path(tmp_path)
    writer = _writer(writer_class, database_path)
    invitation = _make_invitation()
    writer.issue_invitation(invitation)
    _rewrite_audit_representation(
        database_path,
        {"INVITATION_ISSUED": "audit-imported-initial"},
        adapter_version="1.2.3",
    )

    assert writer.issue_invitation(invitation) == invitation
    fresh = _make_invitation(
        invitation_id="review-after-import",
        token_digest="2" * 64,
    )
    assert writer.issue_invitation(fresh) == fresh
    events = _audit_rows(database_path)
    assert events[0]["event_id"] == "audit-imported-initial"
    assert events[0]["metadata_adapter_version"] == "1.2.3"
    assert events[-1]["event_id"] == "audit-sqlite-00000000000000000002"
    assert events[-1]["metadata_adapter_version"] is None


def test_schema_v1_complete_reissue_representation_is_accepted(
    tmp_path: Path,
    writer_class: type[Any],
) -> None:
    database_path = _bootstrap_path(tmp_path)
    old_invitation, replacement = _install_reissue_fixture(
        database_path,
        writer_class,
    )
    _rewrite_audit_representation(
        database_path,
        {
            "INVITATION_ISSUED": "audit-imported-issued",
            "INVITATION_REVOKED": "audit-imported-revoked",
            "INVITATION_REISSUED": "audit-imported-reissued",
        },
        adapter_version="2026.7",
    )
    writer = _writer(writer_class, database_path)

    assert writer.issue_invitation(replacement) == replacement
    with pytest.raises(InvitationRevoked):
        SQLiteConfirmationReader(
            _connection_factory(database_path)
        ).resolve_ledger_invitation(
            old_invitation.token_digest,
            replacement.issued_at,
        )
    fresh = _make_invitation(
        invitation_id="review-after-reissue-import",
        token_digest="3" * 64,
    )
    assert writer.issue_invitation(fresh) == fresh
    events = _audit_rows(database_path)
    assert {
        row["event_id"] for row in events[:3]
    } == {
        "audit-imported-issued",
        "audit-imported-revoked",
        "audit-imported-reissued",
    }
    assert all(
        row["metadata_adapter_version"] == "2026.7"
        for row in events[:3]
    )
    assert events[-1]["event_id"] == "audit-sqlite-00000000000000000004"
    assert events[-1]["metadata_adapter_version"] is None


def test_schema_v1_event_id_collision_uses_stable_unique_fallback(
    tmp_path: Path,
    writer_class: type[Any],
) -> None:
    database_path = _bootstrap_path(tmp_path)
    writer = _writer(writer_class, database_path)
    original = _make_invitation(
        invitation_id="review-imported",
        token_digest="1" * 64,
    )
    writer.issue_invitation(original)
    next_sequence = 4
    canonical_id = "audit-sqlite-{0:020d}".format(next_sequence)
    occupied_fallbacks = (
        "{0}-1".format(canonical_id),
        "{0}-2".format(canonical_id),
    )
    _rewrite_audit_representation(
        database_path,
        {"INVITATION_ISSUED": canonical_id},
        adapter_version="1.4.0",
    )
    connection = sqlite3.connect(database_path)
    try:
        for sequence, event_id in enumerate(
            occupied_fallbacks,
            start=2,
        ):
            _insert_audit_fixture(
                connection,
                sequence=sequence,
                event_type="INVITATION_REJECTED",
                occurred_at=ISSUED_AT + timedelta(
                    microseconds=sequence
                ),
                invitation_id="review-rejected-{0}".format(sequence),
                reason_code="INVALID_REQUEST",
                outcome="REJECTED",
                event_id=event_id,
                metadata_adapter_version="1.4.0",
            )
        connection.commit()
    finally:
        connection.close()

    candidate = _make_invitation(
        invitation_id="review-collision-fallback",
        token_digest="2" * 64,
    )
    assert writer.issue_invitation(candidate) == candidate
    after_issue = _writer_state_snapshot(database_path)
    assert writer.issue_invitation(candidate) == candidate
    assert _writer_state_snapshot(database_path) == after_issue
    events = _audit_rows(database_path)
    assert events[-1]["event_sequence"] == next_sequence
    assert events[-1]["event_id"] == "{0}-3".format(canonical_id)

    no_collision = _make_invitation(
        invitation_id="review-canonical-after-fallback",
        token_digest="3" * 64,
    )
    assert writer.issue_invitation(no_collision) == no_collision
    events = _audit_rows(database_path)
    assert events[-1]["event_sequence"] == 5
    assert events[-1]["event_id"] == (
        "audit-sqlite-00000000000000000005"
    )


def test_issue_uses_exactly_one_begin_immediate_transaction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    writer_class: type[Any],
) -> None:
    database_path = _bootstrap_path(tmp_path)
    sample = open_existing_confirmation_ledger(database_path)
    guarded_type = type(sample)
    original_execute = guarded_type.execute
    sample.close()
    statements: list[str] = []

    def recording_execute(
        connection: GuardedConnection,
        sql: str,
        parameters: object = (),
    ) -> object:
        statements.append(_normalized_sql(sql))
        return original_execute(connection, sql, parameters)

    monkeypatch.setattr(
        guarded_type,
        "execute",
        recording_execute,
    )

    _writer(writer_class, database_path).issue_invitation(
        _make_invitation()
    )

    assert statements.count("BEGIN IMMEDIATE") == 1
    assert statements.count("COMMIT") == 1
    assert not any(
        statement.startswith("SAVEPOINT")
        for statement in statements
    )


@pytest.mark.parametrize(
    "fault_point",
    ("after-invitation", "after-audit", "before-commit"),
)
def test_faults_leave_both_invitation_and_audit_or_neither(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    writer_class: type[Any],
    fault_point: str,
) -> None:
    database_path = _bootstrap_path(tmp_path)
    sample = open_existing_confirmation_ledger(database_path)
    guarded_type = type(sample)
    original_execute = guarded_type.execute
    sample.close()
    fault_fired = False

    def faulting_execute(
        connection: GuardedConnection,
        sql: str,
        parameters: object = (),
    ) -> object:
        nonlocal fault_fired
        normalized = _normalized_sql(sql)
        if fault_point == "before-commit" and normalized == "COMMIT":
            fault_fired = True
            raise sqlite3.OperationalError("injected pre-commit fault")
        cursor = original_execute(connection, sql, parameters)
        should_fail = (
            fault_point == "after-invitation"
            and normalized.startswith("INSERT INTO")
            and "REVIEW_INVITATIONS" in normalized
        ) or (
            fault_point == "after-audit"
            and normalized.startswith("INSERT INTO")
            and "CONFIRMATION_AUDIT_EVENTS" in normalized
        )
        if should_fail:
            cursor.close()
            fault_fired = True
            raise sqlite3.OperationalError("injected post-insert fault")
        return cursor

    monkeypatch.setattr(
        guarded_type,
        "execute",
        faulting_execute,
    )

    _assert_canonical_failure(
        lambda: _writer(
            writer_class,
            database_path,
        ).issue_invitation(_make_invitation())
    )
    assert fault_fired is True
    assert _table_counts(database_path) == (0, 0)


def test_audit_sequence_collision_rolls_back_invitation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    writer_class: type[Any],
) -> None:
    database_path = _bootstrap_path(tmp_path)
    sample = open_existing_confirmation_ledger(database_path)
    guarded_type = type(sample)
    original_execute = guarded_type.execute
    sample.close()

    def collide_on_audit(
        connection: GuardedConnection,
        sql: str,
        parameters: object = (),
    ) -> object:
        normalized = _normalized_sql(sql)
        if (
            normalized.startswith("INSERT INTO")
            and "CONFIRMATION_AUDIT_EVENTS" in normalized
        ):
            raise sqlite3.IntegrityError(
                "UNIQUE constraint failed: "
                "confirmation_audit_events.event_sequence"
            )
        return original_execute(connection, sql, parameters)

    monkeypatch.setattr(
        guarded_type,
        "execute",
        collide_on_audit,
    )

    _assert_canonical_failure(
        lambda: _writer(
            writer_class,
            database_path,
        ).issue_invitation(_make_invitation())
    )
    assert _table_counts(database_path) == (0, 0)


def test_commit_success_close_failure_is_ambiguous_then_retry_replays(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    writer_class: type[Any],
) -> None:
    database_path = _bootstrap_path(tmp_path)
    sample = open_existing_confirmation_ledger(database_path)
    guarded_type = type(sample)
    original_close = guarded_type.close
    sample.close()
    close_calls = 0
    invitation = _make_invitation()

    def fail_first_close(connection: GuardedConnection) -> None:
        nonlocal close_calls
        close_calls += 1
        original_close(connection)
        if close_calls == 1:
            raise sqlite3.OperationalError(
                "close failed after durable commit: hidden-secret"
            )

    monkeypatch.setattr(
        guarded_type,
        "close",
        fail_first_close,
    )
    _assert_canonical_failure(
        lambda: _writer(
            writer_class,
            database_path,
        ).issue_invitation(invitation)
    )
    assert _table_counts(database_path) == (1, 1)

    monkeypatch.setattr(guarded_type, "close", original_close)
    assert (
        _writer(
            writer_class,
            database_path,
        ).issue_invitation(invitation)
        == invitation
    )
    assert _table_counts(database_path) == (1, 1)


def test_oserror_close_after_durable_commit_is_canonical_and_sanitized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    writer_class: type[Any],
) -> None:
    database_path = _bootstrap_path(tmp_path)
    sample = open_existing_confirmation_ledger(database_path)
    guarded_type = type(sample)
    original_close = guarded_type.close
    sample.close()
    secret = "close-path-secret-" + "f" * 64

    def close_then_fail(connection: GuardedConnection) -> None:
        original_close(connection)
        raise OSError("{0}:{1}".format(database_path, secret))

    monkeypatch.setattr(
        guarded_type,
        "close",
        close_then_fail,
    )
    with pytest.raises(
        LedgerUnavailable,
        match=CANONICAL_LEDGER_ERROR,
    ) as error:
        _writer(
            writer_class,
            database_path,
        ).issue_invitation(_make_invitation())

    assert type(error.value) is StoreLedgerUnavailable
    assert error.value.__cause__ is None
    assert str(database_path) not in repr(error.value)
    assert secret not in repr(error.value)
    assert _table_counts(database_path) == (1, 1)


def test_existing_only_writer_never_creates_a_missing_database(
    tmp_path: Path,
    writer_class: type[Any],
) -> None:
    database_path = tmp_path / "never-created.db"
    assert not database_path.exists()
    writer = _writer(writer_class, database_path)

    _assert_canonical_failure(
        lambda: writer.issue_invitation(_make_invitation())
    )
    assert not database_path.exists()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "factory_error",
    (
        sqlite3.OperationalError("sqlite-path-and-secret"),
        OSError("filesystem-path-and-secret"),
        LedgerUnavailable(),
    ),
)
def test_known_factory_failures_are_canonical_and_sanitized(
    writer_class: type[Any],
    factory_error: Exception,
) -> None:
    def factory() -> GuardedConnection:
        raise factory_error

    _assert_canonical_failure(
        lambda: writer_class(factory).issue_invitation(
            _make_invitation()
        )
    )


def test_factory_programming_error_propagates(
    writer_class: type[Any],
) -> None:
    def factory() -> GuardedConnection:
        raise RuntimeError("factory programming defect")

    with pytest.raises(
        RuntimeError,
        match="^factory programming defect$",
    ):
        writer_class(factory).issue_invitation(_make_invitation())


def test_raw_structural_and_proxy_factory_results_fail_nominally(
    tmp_path: Path,
    writer_class: type[Any],
) -> None:
    database_path = _bootstrap_path(tmp_path)
    guarded = open_existing_confirmation_ledger(database_path)
    raw = sqlite3.connect(":memory:")
    structural = StructuralConnectionFake()
    proxy = ConnectionProxy(guarded)
    candidates = (raw, structural, proxy)

    for candidate in candidates:
        _assert_canonical_failure(
            lambda value=candidate: writer_class(
                lambda: value
            ).issue_invitation(_make_invitation())
        )

    assert raw.execute("SELECT 1").fetchone() == (1,)
    assert guarded.execute("SELECT 1").fetchone()[0] == 1
    raw.close()
    guarded.close()


def test_tampered_nominal_factory_result_is_disposed(
    tmp_path: Path,
    writer_class: type[Any],
) -> None:
    database_path = _bootstrap_path(tmp_path)
    guarded = open_existing_confirmation_ledger(database_path)
    object.__setattr__(
        guarded,
        "_GuardedConnection__guard_token",
        object(),
    )

    _assert_canonical_failure(
        lambda: writer_class(
            lambda: guarded
        ).issue_invitation(_make_invitation())
    )
    _assert_connection_closed(guarded)


def test_invalid_nominal_facade_disposal_oserror_preserves_canonical_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    writer_class: type[Any],
) -> None:
    database_path = _bootstrap_path(tmp_path)
    guarded = open_existing_confirmation_ledger(database_path)
    guarded_type = type(guarded)
    secret = "dispose-path-secret-" + "d" * 64
    object.__setattr__(
        guarded,
        "_GuardedConnection__guard_token",
        object(),
    )

    def fail_dispose(
        _connection: GuardedConnection,
        _authority: object,
    ) -> None:
        raise OSError("{0}:{1}".format(database_path, secret))

    monkeypatch.setattr(
        guarded_type,
        "_dispose",
        fail_dispose,
    )
    try:
        with pytest.raises(
            LedgerUnavailable,
            match=CANONICAL_LEDGER_ERROR,
        ) as error:
            writer_class(
                lambda: guarded
            ).issue_invitation(_make_invitation())
    finally:
        guarded.close()

    assert type(error.value) is StoreLedgerUnavailable
    assert error.value.__cause__ is None
    assert str(database_path) not in repr(error.value)
    assert secret not in repr(error.value)
    assert _table_counts(database_path) == (0, 0)


@pytest.mark.parametrize(
    "cleanup_error",
    (
        AssertionError("dispose assertion secret"),
        RuntimeError("dispose runtime secret"),
    ),
)
def test_invalid_nominal_facade_disposal_programmer_error_preserves_canonical(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    writer_class: type[Any],
    cleanup_error: Exception,
) -> None:
    database_path = _bootstrap_path(tmp_path)
    guarded = open_existing_confirmation_ledger(database_path)
    guarded_type = type(guarded)
    object.__setattr__(
        guarded,
        "_GuardedConnection__guard_token",
        object(),
    )

    def fail_dispose(
        _connection: GuardedConnection,
        _authority: object,
    ) -> None:
        raise cleanup_error

    monkeypatch.setattr(
        guarded_type,
        "_dispose",
        fail_dispose,
    )
    try:
        with pytest.raises(
            LedgerUnavailable,
            match=CANONICAL_LEDGER_ERROR,
        ) as error:
            writer_class(
                lambda: guarded
            ).issue_invitation(_make_invitation())
    finally:
        guarded.close()

    assert type(error.value) is StoreLedgerUnavailable
    assert error.value.__cause__ is None
    assert "secret" not in str(error.value)
    assert _table_counts(database_path) == (0, 0)


def test_active_factory_transaction_is_rolled_back_and_closed(
    tmp_path: Path,
    writer_class: type[Any],
) -> None:
    database_path = _bootstrap_path(tmp_path)
    created: list[GuardedConnection] = []

    def transactional_factory() -> GuardedConnection:
        connection = open_existing_confirmation_ledger(database_path)
        cursor = connection.execute("BEGIN")
        cursor.close()
        cursor = connection.execute(
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
                "review-uncommitted",
                BINDING.contract_id,
                BINDING.contract_version,
                BINDING.confirmation_fingerprint,
                "9" * 64,
                "sha256-v1",
                "customer-org",
                "seller-subject",
                _epoch_microseconds(ISSUED_AT),
                _epoch_microseconds(
                    ISSUED_AT + timedelta(hours=1)
                ),
            ),
        )
        cursor.close()
        assert connection.in_transaction is True
        created.append(connection)
        return connection

    writer = writer_class(transactional_factory)
    _assert_canonical_failure(
        lambda: writer.issue_invitation(_make_invitation())
    )

    assert len(created) == 1
    _assert_connection_closed(created[0])
    assert _table_counts(database_path) == (0, 0)


def test_busy_database_fails_fast_without_partial_write(
    tmp_path: Path,
    writer_class: type[Any],
) -> None:
    database_path = _bootstrap_path(tmp_path)
    opened: list[GuardedConnection] = []
    locker = sqlite3.connect(
        database_path,
        isolation_level=None,
        timeout=0,
    )
    try:
        locker.execute("BEGIN IMMEDIATE")
        writer = _writer(
            writer_class,
            database_path,
            opened=opened,
            busy_timeout_ms=1,
        )
        _assert_canonical_failure(
            lambda: writer.issue_invitation(_make_invitation())
        )
        assert len(opened) == 1
        _assert_connection_closed(opened[0])
        assert _table_counts(database_path) == (0, 0)
    finally:
        locker.execute("ROLLBACK")
        locker.close()


def test_rollback_oserror_does_not_replace_identity_conflict(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    writer_class: type[Any],
) -> None:
    database_path = _bootstrap_path(tmp_path)
    existing = _make_invitation(
        invitation_id="review-existing",
        token_digest="1" * 64,
    )
    _writer(
        writer_class,
        database_path,
    ).issue_invitation(existing)
    connection = open_existing_confirmation_ledger(database_path)
    guarded_type = type(connection)
    secret = "rollback-path-secret-" + "e" * 64

    def fail_rollback(_connection: GuardedConnection) -> None:
        raise OSError("{0}:{1}".format(database_path, secret))

    monkeypatch.setattr(
        guarded_type,
        "rollback",
        fail_rollback,
    )
    conflicting = replace(
        existing,
        token_digest=TokenDigest("2" * 64),
    )

    with pytest.raises(InvitationIdentityConflict) as error:
        writer_class(
            lambda: connection
        ).issue_invitation(conflicting)

    assert type(error.value) is InvitationIdentityConflict
    assert str(database_path) not in repr(error.value)
    assert secret not in repr(error.value)
    _assert_connection_closed(connection)
    assert _table_counts(database_path) == (1, 1)


def test_rollback_programmer_error_does_not_replace_identity_conflict(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    writer_class: type[Any],
) -> None:
    database_path = _bootstrap_path(tmp_path)
    existing = _make_invitation(
        invitation_id="review-existing",
        token_digest="1" * 64,
    )
    _writer(writer_class, database_path).issue_invitation(existing)
    connection = open_existing_confirmation_ledger(database_path)
    guarded_type = type(connection)
    original_rollback = guarded_type.rollback

    def rollback_then_fail(target: GuardedConnection) -> None:
        original_rollback(target)
        raise AssertionError("rollback cleanup programming defect")

    monkeypatch.setattr(
        guarded_type,
        "rollback",
        rollback_then_fail,
    )
    conflicting = replace(
        existing,
        token_digest=TokenDigest("2" * 64),
    )

    with pytest.raises(InvitationIdentityConflict) as error:
        writer_class(
            lambda: connection
        ).issue_invitation(conflicting)

    assert type(error.value) is InvitationIdentityConflict
    _assert_connection_closed(connection)
    assert _table_counts(database_path) == (1, 1)


def test_close_programmer_error_does_not_replace_identity_conflict(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    writer_class: type[Any],
) -> None:
    database_path = _bootstrap_path(tmp_path)
    existing = _make_invitation(
        invitation_id="review-existing",
        token_digest="1" * 64,
    )
    _writer(writer_class, database_path).issue_invitation(existing)
    connection = open_existing_confirmation_ledger(database_path)
    guarded_type = type(connection)
    original_close = guarded_type.close

    def close_then_fail(target: GuardedConnection) -> None:
        original_close(target)
        raise RuntimeError("close cleanup programming defect")

    monkeypatch.setattr(
        guarded_type,
        "close",
        close_then_fail,
    )
    conflicting = replace(
        existing,
        token_digest=TokenDigest("2" * 64),
    )

    with pytest.raises(InvitationIdentityConflict) as error:
        writer_class(
            lambda: connection
        ).issue_invitation(conflicting)

    assert type(error.value) is InvitationIdentityConflict
    _assert_connection_closed(connection)
    assert _table_counts(database_path) == (1, 1)


def test_cursor_close_programmer_error_does_not_replace_typed_primary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    writer_class: type[Any],
) -> None:
    database_path = _bootstrap_path(tmp_path)
    sample = open_existing_confirmation_ledger(database_path)
    guarded_type = type(sample)
    original_execute = guarded_type.execute
    sample.close()

    class FailingCursor:
        def __init__(self, target: object) -> None:
            self.target = target

        def fetchall(self) -> list[sqlite3.Row]:
            raise InvitationIdentityConflict()

        def close(self) -> None:
            self.target.close()  # type: ignore[attr-defined]
            raise RuntimeError("cursor cleanup programming defect")

    def inject_faulty_cursor(
        connection: GuardedConnection,
        sql: str,
        parameters: object = (),
    ) -> object:
        cursor = original_execute(connection, sql, parameters)
        if "FROM MAIN.REVIEW_INVITATIONS" in _normalized_sql(sql):
            return FailingCursor(cursor)
        return cursor

    monkeypatch.setattr(
        guarded_type,
        "execute",
        inject_faulty_cursor,
    )

    with pytest.raises(InvitationIdentityConflict) as error:
        _writer(
            writer_class,
            database_path,
        ).issue_invitation(_make_invitation())

    assert type(error.value) is InvitationIdentityConflict
    assert _table_counts(database_path) == (0, 0)


def test_cursor_close_programmer_error_propagates_without_primary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    writer_class: type[Any],
) -> None:
    database_path = _bootstrap_path(tmp_path)
    sample = open_existing_confirmation_ledger(database_path)
    guarded_type = type(sample)
    original_execute = guarded_type.execute
    sample.close()

    class FailingCursor:
        def __init__(self, target: object) -> None:
            self.target = target

        def fetchall(self) -> list[sqlite3.Row]:
            return self.target.fetchall()  # type: ignore[attr-defined]

        def close(self) -> None:
            self.target.close()  # type: ignore[attr-defined]
            raise AssertionError("cursor cleanup programming defect")

    def inject_faulty_cursor(
        connection: GuardedConnection,
        sql: str,
        parameters: object = (),
    ) -> object:
        cursor = original_execute(connection, sql, parameters)
        if "FROM MAIN.REVIEW_INVITATIONS" in _normalized_sql(sql):
            return FailingCursor(cursor)
        return cursor

    monkeypatch.setattr(
        guarded_type,
        "execute",
        inject_faulty_cursor,
    )

    with pytest.raises(
        AssertionError,
        match="^cursor cleanup programming defect$",
    ):
        _writer(
            writer_class,
            database_path,
        ).issue_invitation(_make_invitation())

    assert _table_counts(database_path) == (0, 0)


def test_rollback_failure_is_canonical_and_close_still_removes_partial_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    writer_class: type[Any],
) -> None:
    database_path = _bootstrap_path(tmp_path)
    sample = open_existing_confirmation_ledger(database_path)
    guarded_type = type(sample)
    original_execute = guarded_type.execute
    original_rollback = guarded_type.rollback
    sample.close()
    rollback_calls = 0

    def fail_after_invitation(
        connection: GuardedConnection,
        sql: str,
        parameters: object = (),
    ) -> object:
        cursor = original_execute(connection, sql, parameters)
        normalized = _normalized_sql(sql)
        if (
            normalized.startswith("INSERT INTO")
            and "REVIEW_INVITATIONS" in normalized
        ):
            cursor.close()
            raise sqlite3.OperationalError("write fault")
        return cursor

    def fail_rollback(_connection: GuardedConnection) -> None:
        nonlocal rollback_calls
        rollback_calls += 1
        raise sqlite3.OperationalError("rollback fault")

    monkeypatch.setattr(
        guarded_type,
        "execute",
        fail_after_invitation,
    )
    monkeypatch.setattr(
        guarded_type,
        "rollback",
        fail_rollback,
    )

    _assert_canonical_failure(
        lambda: _writer(
            writer_class,
            database_path,
        ).issue_invitation(_make_invitation())
    )
    assert rollback_calls >= 1
    assert _table_counts(database_path) == (0, 0)
    monkeypatch.setattr(guarded_type, "rollback", original_rollback)


def test_close_failure_is_canonical_without_leaking_details(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    writer_class: type[Any],
) -> None:
    database_path = _bootstrap_path(tmp_path)
    sample = open_existing_confirmation_ledger(database_path)
    guarded_type = type(sample)
    original_close = guarded_type.close
    sample.close()
    opened: list[GuardedConnection] = []
    secret = "close-secret-" + "f" * 64

    def close_then_fail(connection: GuardedConnection) -> None:
        original_close(connection)
        raise sqlite3.OperationalError(
            "{0}:{1}".format(database_path, secret)
        )

    monkeypatch.setattr(
        guarded_type,
        "close",
        close_then_fail,
    )
    with pytest.raises(
        LedgerUnavailable,
        match=CANONICAL_LEDGER_ERROR,
    ) as error:
        _writer(
            writer_class,
            database_path,
            opened=opened,
        ).issue_invitation(_make_invitation())

    assert type(error.value) is StoreLedgerUnavailable
    assert str(database_path) not in repr(error.value)
    assert secret not in repr(error.value)
    assert len(opened) == 1
    _assert_connection_closed(opened[0])


def test_cursor_close_oserror_preserves_active_programmer_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    writer_class: type[Any],
) -> None:
    database_path = _bootstrap_path(tmp_path)
    sample = open_existing_confirmation_ledger(database_path)
    guarded_type = type(sample)
    original_execute = guarded_type.execute
    sample.close()
    cleanup_secret = "cursor-close-path-secret-" + "c" * 64

    class FailingCursor:
        def __init__(self, target: object) -> None:
            self.target = target

        def fetchall(self) -> list[sqlite3.Row]:
            raise RuntimeError("primary cursor programming defect")

        def close(self) -> None:
            self.target.close()  # type: ignore[attr-defined]
            raise OSError(
                "{0}:{1}".format(database_path, cleanup_secret)
            )

    def inject_faulty_cursor(
        connection: GuardedConnection,
        sql: str,
        parameters: object = (),
    ) -> object:
        cursor = original_execute(connection, sql, parameters)
        if "FROM MAIN.REVIEW_INVITATIONS" in _normalized_sql(sql):
            return FailingCursor(cursor)
        return cursor

    monkeypatch.setattr(
        guarded_type,
        "execute",
        inject_faulty_cursor,
    )

    with pytest.raises(
        RuntimeError,
        match="^primary cursor programming defect$",
    ) as error:
        _writer(
            writer_class,
            database_path,
        ).issue_invitation(_make_invitation())

    assert str(database_path) not in repr(error.value)
    assert cleanup_secret not in repr(error.value)
    assert _table_counts(database_path) == (0, 0)


def test_programmer_error_propagates_after_rollback_and_close(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    writer_class: type[Any],
) -> None:
    database_path = _bootstrap_path(tmp_path)
    writer = _writer(writer_class, database_path)
    writer.issue_invitation(
        _make_invitation(
            invitation_id="review-existing",
            token_digest="1" * 64,
        )
    )
    sample = open_existing_confirmation_ledger(database_path)
    guarded_type = type(sample)
    original_rollback = guarded_type.rollback
    original_close = guarded_type.close
    sample.close()
    rollbacks: list[GuardedConnection] = []
    closes: list[GuardedConnection] = []

    def recording_rollback(connection: GuardedConnection) -> None:
        rollbacks.append(connection)
        original_rollback(connection)

    def recording_close(connection: GuardedConnection) -> None:
        closes.append(connection)
        original_close(connection)

    def programmer_defect(_left: str, _right: str) -> bool:
        raise RuntimeError("digest comparator programming defect")

    monkeypatch.setattr(
        guarded_type,
        "rollback",
        recording_rollback,
    )
    monkeypatch.setattr(
        guarded_type,
        "close",
        recording_close,
    )
    monkeypatch.setattr(
        confirmation_ledger_module.hmac,
        "compare_digest",
        programmer_defect,
    )
    opened: list[GuardedConnection] = []

    with pytest.raises(
        RuntimeError,
        match="^digest comparator programming defect$",
    ):
        _writer(
            writer_class,
            database_path,
            opened=opened,
        ).issue_invitation(
            _make_invitation(
                invitation_id="review-new",
                token_digest="2" * 64,
            )
        )

    assert len(opened) == 1
    assert opened[0] in rollbacks
    assert opened[0] in closes
    _assert_connection_closed(opened[0])
    assert _table_counts(database_path) == (1, 1)


def test_close_programming_error_propagates_after_durable_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    writer_class: type[Any],
) -> None:
    database_path = _bootstrap_path(tmp_path)
    sample = open_existing_confirmation_ledger(database_path)
    guarded_type = type(sample)
    original_close = guarded_type.close
    sample.close()

    def close_then_defect(connection: GuardedConnection) -> None:
        original_close(connection)
        raise AssertionError("close programming defect")

    monkeypatch.setattr(
        guarded_type,
        "close",
        close_then_defect,
    )

    with pytest.raises(
        AssertionError,
        match="^close programming defect$",
    ):
        _writer(
            writer_class,
            database_path,
        ).issue_invitation(_make_invitation())

    assert _table_counts(database_path) == (1, 1)


def test_successful_issues_allocate_unique_contiguous_audit_sequences(
    tmp_path: Path,
    writer_class: type[Any],
) -> None:
    database_path = _bootstrap_path(tmp_path)
    writer = _writer(writer_class, database_path)
    for invitation_id, digest in (
        ("review-alpha", "1" * 64),
        ("review-bravo", "2" * 64),
        ("review-charlie", "3" * 64),
    ):
        writer.issue_invitation(
            _make_invitation(
                invitation_id=invitation_id,
                token_digest=digest,
            )
        )

    events = _audit_rows(database_path)
    assert [row["event_sequence"] for row in events] == [1, 2, 3]
    assert len({row["event_id"] for row in events}) == 3
    assert all(
        AUDIT_EVENT_ID.fullmatch(row["event_id"])
        for row in events
    )


def test_existing_event_sequence_gap_advances_from_highest_sequence(
    tmp_path: Path,
    writer_class: type[Any],
) -> None:
    database_path = _bootstrap_path(tmp_path)
    writer = _writer(writer_class, database_path)
    writer.issue_invitation(
        _make_invitation(
            invitation_id="review-alpha",
            token_digest="1" * 64,
        )
    )
    writer.issue_invitation(
        _make_invitation(
            invitation_id="review-bravo",
            token_digest="2" * 64,
        )
    )
    _change_audit_sequence(database_path, 2, 3)
    candidate = _make_invitation(
        invitation_id="review-new",
        token_digest="3" * 64,
    )

    assert writer.issue_invitation(candidate) == candidate
    assert _table_counts(database_path) == (3, 3)
    events = _audit_rows(database_path)
    assert [event["event_sequence"] for event in events] == [1, 3, 4]
    assert events[-1]["event_id"] == "audit-sqlite-00000000000000000004"
    assert events[-1]["invitation_id"] == candidate.invitation_id


def test_event_sequence_overflow_fails_closed(
    tmp_path: Path,
    writer_class: type[Any],
) -> None:
    database_path = _bootstrap_path(tmp_path)
    writer = _writer(writer_class, database_path)
    writer.issue_invitation(
        _make_invitation(
            invitation_id="review-alpha",
            token_digest="1" * 64,
        )
    )
    _change_audit_sequence(
        database_path,
        1,
        9_223_372_036_854_775_807,
    )
    before = _table_counts(database_path)

    _assert_canonical_failure(
        lambda: writer.issue_invitation(
            _make_invitation(
                invitation_id="review-new",
                token_digest="2" * 64,
            )
        )
    )
    assert _table_counts(database_path) == before


def test_concurrent_identical_issue_has_one_durable_effect(
    tmp_path: Path,
    writer_class: type[Any],
) -> None:
    database_path = _bootstrap_path(tmp_path)
    invitation = _make_invitation()
    barrier = threading.Barrier(2)

    def issue() -> ReviewInvitationRecord:
        barrier.wait()
        return _writer(
            writer_class,
            database_path,
        ).issue_invitation(invitation)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(issue) for _ in range(2)]
        results = [future.result(timeout=10) for future in futures]

    assert results == [invitation, invitation]
    assert _table_counts(database_path) == (1, 1)


@pytest.mark.parametrize(
    ("race", "expected_error"),
    (
        ("identity", InvitationIdentityConflict),
        ("token", TokenDigestConflict),
        ("binding", ContractBindingMismatch),
    ),
)
def test_concurrent_conflict_race_has_one_winner_and_typed_loser(
    tmp_path: Path,
    writer_class: type[Any],
    race: str,
    expected_error: type[Exception],
) -> None:
    database_path = _bootstrap_path(tmp_path)
    first = _make_invitation(
        invitation_id="review-alpha",
        token_digest="1" * 64,
    )
    if race == "identity":
        second = replace(
            first,
            token_digest=TokenDigest("2" * 64),
        )
    elif race == "token":
        second = replace(
            first,
            invitation_id="review-bravo",
        )
    else:
        second = _make_invitation(
            invitation_id="review-bravo",
            token_digest="2" * 64,
            binding=ContractBinding(
                contract_id=BINDING.contract_id,
                contract_version=BINDING.contract_version,
                confirmation_fingerprint="f" * 64,
            ),
        )
    barrier = threading.Barrier(2)

    def attempt(
        invitation: ReviewInvitationRecord,
    ) -> ReviewInvitationRecord | Exception:
        barrier.wait()
        try:
            return _writer(
                writer_class,
                database_path,
            ).issue_invitation(invitation)
        except Exception as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(attempt, invitation)
            for invitation in (first, second)
        ]
        outcomes = [future.result(timeout=10) for future in futures]

    records = [
        outcome
        for outcome in outcomes
        if isinstance(outcome, ReviewInvitationRecord)
    ]
    failures = [
        outcome
        for outcome in outcomes
        if isinstance(outcome, Exception)
    ]
    assert len(records) == 1
    assert len(failures) == 1
    assert type(failures[0]) is expected_error
    assert _table_counts(database_path) == (1, 1)


def test_factory_is_invoked_and_connection_closed_in_calling_worker(
    tmp_path: Path,
    writer_class: type[Any],
) -> None:
    database_path = _bootstrap_path(tmp_path)
    opened: list[GuardedConnection] = []
    creation_threads: list[int] = []
    writer = _writer(
        writer_class,
        database_path,
        opened=opened,
        creation_threads=creation_threads,
    )

    def issue_in_worker() -> tuple[int, ReviewInvitationRecord, bool]:
        worker_thread = threading.get_ident()
        stored = writer.issue_invitation(_make_invitation())
        try:
            opened[-1].execute("SELECT 1")
        except sqlite3.ProgrammingError:
            closed_in_worker = True
        else:
            closed_in_worker = False
        return worker_thread, stored, closed_in_worker

    with ThreadPoolExecutor(max_workers=1) as pool:
        worker_thread, stored, closed_in_worker = pool.submit(
            issue_in_worker
        ).result(timeout=10)

    assert stored == _make_invitation()
    assert creation_threads == [worker_thread]
    assert closed_in_worker is True
