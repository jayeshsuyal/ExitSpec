"""Fail-closed bootstrap boundary for the durable confirmation ledger."""

from __future__ import annotations

import hmac
import os
import re
import sqlite3
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Protocol, TypeVar, cast, runtime_checkable

from .confirmation_schema import (
    CONFIRMATION_LEDGER_MIGRATIONS as _CONFIRMATION_LEDGER_MIGRATIONS,
    validate_confirmation_schema as _validate_confirmation_schema,
)
from .confirmation_sqlite import (
    DEFAULT_BUSY_TIMEOUT_MS as _DEFAULT_BUSY_TIMEOUT_MS,
    ConfirmationSQLiteError as _ConfirmationSQLiteError,
    _dispose_guarded_connection,
    _validate_busy_timeout,
    _validated_utc_datetime,
    apply_migrations as _apply_migrations,
    open_confirmation_database as _open_confirmation_database,
    open_existing_confirmation_database as _open_existing_confirmation_database,
    validate_guarded_connection as _validate_guarded_connection,
)
from .confirmation_store import (
    ConfirmationDecisionRecord,
    ContractBinding,
    InvitationConsumed,
    InvitationExpired,
    InvitationRevocationRecord,
    InvitationRevoked,
    LedgerUnavailable,
    RequestDigest,
    ReviewInvitationRecord,
    RevocationReason,
    TokenDigest,
)
from .confirmations import ConfirmationDecision


__all__ = (
    "GuardedConnection",
    "GuardedCursor",
    "LedgerUnavailable",
    "SQLiteConfirmationReader",
    "bootstrap_confirmation_ledger",
    "open_existing_confirmation_ledger",
)

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_SHA256_DIGEST = re.compile(r"[a-f0-9]{64}")
_SUPPORTED_TOKEN_DIGEST_VERSION = "sha256-v1"
_ReadResult = TypeVar("_ReadResult")
_INVITATION_SELECT = """
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
FROM main.review_invitations
""".strip()
_DECISION_SELECT = """
SELECT
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
FROM main.confirmation_decisions
""".strip()
_REVOCATION_SELECT = """
SELECT
    invitation_id,
    revoked_at_us,
    revoked_by_subject,
    reason_code
FROM main.invitation_revocations
""".strip()


class _PersistedLedgerCorruption(RuntimeError):
    """A persisted schema-v1 row or cross-record invariant is invalid."""


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
    """Usable type surface returned by the ledger open boundaries."""

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


class SQLiteConfirmationReader:
    """Ledger-local schema-v1 reads using one fresh guarded connection per call.

    The factory is invoked inside each public read. It must create a fresh
    guarded connection in that calling thread; the reader closes that connection
    before the call returns or raises. Every multi-query read runs in one
    consistent SQLite snapshot. No connection is retained.

    ``resolve_ledger_invitation`` derives only persisted ledger state:
    revocation, terminal consumption, and expiry. It cannot derive ``STALE``
    because this layer has no contract repository. Its result is never an
    authorization decision. A future application service must reload the current
    contract, recompute its canonical binding, and authorize the authenticated
    principal before agreement disclosure or decision recording.

    This deliberately partial adapter does not implement ``ConfirmationStore``
    and exposes no write path.
    """

    __slots__ = ("__connection_factory",)

    def __init__(
        self,
        connection_factory: Callable[[], GuardedConnection],
    ) -> None:
        if not callable(connection_factory):
            raise TypeError("connection_factory must be callable.")
        self.__connection_factory = connection_factory

    def get_invitation(
        self,
        invitation_id: str,
    ) -> ReviewInvitationRecord | None:
        """Hydrate one immutable invitation by its public machine identity."""

        if not isinstance(invitation_id, str):
            raise TypeError("invitation_id must be a string.")
        return self.__read(
            lambda connection: _read_invitation_by_id(
                connection,
                invitation_id,
            )
        )

    def resolve_ledger_invitation(
        self,
        token_digest: TokenDigest,
        now: datetime,
    ) -> ReviewInvitationRecord | None:
        """Resolve ledger-local state without claiming freshness or authorization.

        Returning a record means only that persisted ledger facts classify it as
        active at ``now``. The caller must still reload the current contract,
        recompute and compare its binding, and authorize the principal.
        """

        if not isinstance(token_digest, TokenDigest):
            raise TypeError(
                "token_digest must be a TokenDigest, never a raw token."
            )
        try:
            checked_at = _validated_utc_datetime(now)
        except ValueError:
            raise ValueError("now must be timezone-aware UTC.") from None

        def read(
            connection: GuardedConnection,
        ) -> ReviewInvitationRecord | None:
            rows = _fetchall(
                connection,
                _INVITATION_SELECT + " ORDER BY invitation_id ASC",
            )

            comparison_results: list[bool] = []
            corrupt_digest = False
            for row in rows:
                try:
                    stored_digest = row["token_digest"]
                except (IndexError, KeyError, TypeError):
                    comparison_results.append(False)
                    corrupt_digest = True
                    continue
                if (
                    not isinstance(stored_digest, str)
                    or _SHA256_DIGEST.fullmatch(stored_digest) is None
                ):
                    comparison_results.append(False)
                    corrupt_digest = True
                    continue
                comparison_results.append(
                    hmac.compare_digest(
                        stored_digest,
                        token_digest.value,
                    )
                )

            unsupported_digest_version = False
            for row in rows:
                try:
                    digest_version = row["token_digest_version"]
                except (IndexError, KeyError, TypeError):
                    unsupported_digest_version = True
                    continue
                if digest_version != _SUPPORTED_TOKEN_DIGEST_VERSION:
                    unsupported_digest_version = True

            if corrupt_digest or unsupported_digest_version:
                raise _PersistedLedgerCorruption

            invitations = tuple(_invitation_from_row(row) for row in rows)
            _validate_consistent_invitation_bindings(invitations)
            matched = tuple(
                invitation
                for invitation, is_match in zip(
                    invitations,
                    comparison_results,
                    strict=True,
                )
                if is_match
            )
            if len(matched) > 1:
                raise _PersistedLedgerCorruption
            if not matched:
                return None

            invitation = matched[0]
            revocation = _read_revocation(
                connection,
                invitation.invitation_id,
            )
            if revocation is not None:
                _validate_revocation(invitation, revocation)

            decision = _read_decision_for_version(
                connection,
                invitation.binding.contract_id,
                invitation.binding.contract_version,
            )
            if decision is not None and decision.binding != invitation.binding:
                raise _PersistedLedgerCorruption

            if revocation is not None:
                raise InvitationRevoked()
            if decision is not None:
                raise InvitationConsumed(
                    "Review capability is no longer active."
                )
            if checked_at >= invitation.expires_at:
                raise InvitationExpired(
                    "Review capability is no longer active."
                )
            return invitation

        return self.__read(read)

    def get_decision(
        self,
        binding: ContractBinding,
    ) -> ConfirmationDecisionRecord | None:
        """Hydrate the terminal decision for one exact contract binding."""

        if not isinstance(binding, ContractBinding):
            raise TypeError("binding must be a ContractBinding.")

        def read(
            connection: GuardedConnection,
        ) -> ConfirmationDecisionRecord | None:
            decision = _read_decision_for_version(
                connection,
                binding.contract_id,
                binding.contract_version,
            )
            if decision is None or decision.binding != binding:
                return None
            return decision

        return self.__read(read)

    def __read(
        self,
        operation: Callable[[GuardedConnection], _ReadResult],
    ) -> _ReadResult:
        try:
            candidate = self.__connection_factory()
        except (
            LedgerUnavailable,
            _ConfirmationSQLiteError,
            OSError,
            sqlite3.Error,
        ):
            raise LedgerUnavailable() from None

        validated = False
        try:
            try:
                _validate_guarded_connection(candidate)
            except _ConfirmationSQLiteError:
                raise LedgerUnavailable() from None
            validated = True
        finally:
            if not validated:
                try:
                    _dispose_guarded_connection(candidate)
                except _ConfirmationSQLiteError:
                    pass
        connection = cast(GuardedConnection, candidate)

        snapshot_started = False
        try:
            try:
                if connection.in_transaction:
                    raise LedgerUnavailable()
                begin_cursor = connection.execute("BEGIN")
                snapshot_started = True
                begin_cursor.close()
                return operation(connection)
            except LedgerUnavailable:
                raise LedgerUnavailable() from None
            except _ConfirmationSQLiteError:
                raise LedgerUnavailable() from None
            except sqlite3.Error:
                raise LedgerUnavailable() from None
            except _PersistedLedgerCorruption:
                raise LedgerUnavailable() from None
        finally:
            _finish_reader_connection(
                connection,
                snapshot_started=snapshot_started,
            )


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


def open_existing_confirmation_ledger(
    path: str | os.PathLike[str],
    *,
    busy_timeout_ms: int = _DEFAULT_BUSY_TIMEOUT_MS,
) -> GuardedConnection:
    """Open one already-current ledger without creating or migrating it.

    The database must already exist, be hardened, and contain the exact current
    migration history and schema. Validation runs in one read snapshot. Known
    open or validation failures are reduced to the canonical store-level
    ``LedgerUnavailable``. The caller owns and must close a returned connection.
    """

    try:
        _validate_busy_timeout(busy_timeout_ms)
    except ValueError:
        raise LedgerUnavailable() from None

    connection: GuardedConnection | None = None
    ready = False
    validation_snapshot_started = False
    try:
        connection = _open_existing_confirmation_database(
            path,
            busy_timeout_ms=busy_timeout_ms,
        )
        if connection.in_transaction:
            raise LedgerUnavailable()
        begin_cursor = connection.execute("BEGIN")
        validation_snapshot_started = True
        begin_cursor.close()
        _validate_confirmation_schema(connection)
        connection.rollback()
        validation_snapshot_started = False
        if connection.in_transaction:
            raise LedgerUnavailable()
        ready = True
        return connection
    except (
        LedgerUnavailable,
        _ConfirmationSQLiteError,
        OSError,
        sqlite3.Error,
    ):
        raise LedgerUnavailable() from None
    finally:
        if connection is not None and not ready:
            try:
                if validation_snapshot_started:
                    try:
                        connection.rollback()
                    except (_ConfirmationSQLiteError, sqlite3.Error):
                        pass
            finally:
                try:
                    _dispose_guarded_connection(connection)
                except _ConfirmationSQLiteError:
                    pass


def _finish_reader_connection(
    connection: GuardedConnection,
    *,
    snapshot_started: bool,
) -> None:
    cleanup_failed = False
    try:
        if snapshot_started:
            try:
                connection.rollback()
            except (_ConfirmationSQLiteError, sqlite3.Error):
                cleanup_failed = True
    finally:
        try:
            connection.close()
        except (_ConfirmationSQLiteError, sqlite3.Error):
            cleanup_failed = True

    if cleanup_failed:
        raise LedgerUnavailable() from None


def _fetchall(
    connection: GuardedConnection,
    sql: str,
    parameters: object = (),
) -> list[sqlite3.Row]:
    cursor = connection.execute(sql, parameters)
    try:
        return cursor.fetchall()
    finally:
        cursor.close()


def _read_invitation_by_id(
    connection: GuardedConnection,
    invitation_id: str,
) -> ReviewInvitationRecord | None:
    rows = _fetchall(
        connection,
        _INVITATION_SELECT + " WHERE invitation_id = ?",
        (invitation_id,),
    )
    if len(rows) > 1:
        raise _PersistedLedgerCorruption
    if not rows:
        return None
    invitation = _invitation_from_row(rows[0])
    sibling_rows = _fetchall(
        connection,
        _INVITATION_SELECT
        + " WHERE contract_id = ? AND contract_version = ?"
        + " ORDER BY invitation_id ASC",
        (
            invitation.binding.contract_id,
            invitation.binding.contract_version,
        ),
    )
    siblings = tuple(
        _invitation_from_row(sibling_row)
        for sibling_row in sibling_rows
    )
    _validate_consistent_invitation_bindings(siblings)
    return invitation


def _invitation_from_row(row: sqlite3.Row) -> ReviewInvitationRecord:
    try:
        token_digest_version = row["token_digest_version"]
        if token_digest_version != _SUPPORTED_TOKEN_DIGEST_VERSION:
            raise _PersistedLedgerCorruption
        return ReviewInvitationRecord(
            invitation_id=row["invitation_id"],
            binding=ContractBinding(
                contract_id=row["contract_id"],
                contract_version=row["contract_version"],
                confirmation_fingerprint=row["confirmation_fingerprint"],
            ),
            token_digest=TokenDigest(row["token_digest"]),
            token_digest_version=token_digest_version,
            intended_organization_id=row["intended_organization_id"],
            issued_by_subject=row["issued_by_subject"],
            issued_at=_datetime_from_microseconds(row["issued_at_us"]),
            expires_at=_datetime_from_microseconds(row["expires_at_us"]),
        )
    except _PersistedLedgerCorruption:
        raise
    except (IndexError, KeyError, OverflowError, TypeError, ValueError):
        raise _PersistedLedgerCorruption from None


def _validate_consistent_invitation_bindings(
    invitations: tuple[ReviewInvitationRecord, ...],
) -> None:
    bindings_by_version: dict[tuple[str, str], ContractBinding] = {}
    for invitation in invitations:
        version = (
            invitation.binding.contract_id,
            invitation.binding.contract_version,
        )
        existing = bindings_by_version.setdefault(
            version,
            invitation.binding,
        )
        if existing != invitation.binding:
            raise _PersistedLedgerCorruption


def _read_revocation(
    connection: GuardedConnection,
    invitation_id: str,
) -> InvitationRevocationRecord | None:
    rows = _fetchall(
        connection,
        _REVOCATION_SELECT + " WHERE invitation_id = ?",
        (invitation_id,),
    )
    if len(rows) > 1:
        raise _PersistedLedgerCorruption
    if not rows:
        return None
    row = rows[0]
    try:
        return InvitationRevocationRecord(
            invitation_id=row["invitation_id"],
            revoked_at=_datetime_from_microseconds(row["revoked_at_us"]),
            revoked_by_subject=row["revoked_by_subject"],
            reason_code=RevocationReason(row["reason_code"]),
        )
    except (IndexError, KeyError, OverflowError, TypeError, ValueError):
        raise _PersistedLedgerCorruption from None


def _validate_revocation(
    invitation: ReviewInvitationRecord,
    revocation: InvitationRevocationRecord,
) -> None:
    if (
        revocation.invitation_id != invitation.invitation_id
        or revocation.revoked_at < invitation.issued_at
    ):
        raise _PersistedLedgerCorruption


def _read_decision_for_version(
    connection: GuardedConnection,
    contract_id: str,
    contract_version: str,
) -> ConfirmationDecisionRecord | None:
    rows = _fetchall(
        connection,
        _DECISION_SELECT
        + " WHERE contract_id = ? AND contract_version = ?",
        (contract_id, contract_version),
    )
    if len(rows) > 1:
        raise _PersistedLedgerCorruption
    if not rows:
        return None

    decision = _decision_from_row(rows[0])
    source_invitation = _read_invitation_by_id(
        connection,
        decision.invitation_id,
    )
    if source_invitation is None:
        raise _PersistedLedgerCorruption
    if (
        source_invitation.binding != decision.binding
        or source_invitation.intended_organization_id
        != decision.reviewer_organization_id
        or decision.decided_at < source_invitation.issued_at
        or decision.decided_at >= source_invitation.expires_at
    ):
        raise _PersistedLedgerCorruption

    source_revocation = _read_revocation(
        connection,
        source_invitation.invitation_id,
    )
    if source_revocation is not None:
        _validate_revocation(source_invitation, source_revocation)
        if source_revocation.revoked_at < decision.decided_at:
            raise _PersistedLedgerCorruption
    return decision


def _decision_from_row(row: sqlite3.Row) -> ConfirmationDecisionRecord:
    try:
        return ConfirmationDecisionRecord(
            confirmation_id=row["confirmation_id"],
            invitation_id=row["invitation_id"],
            binding=ContractBinding(
                contract_id=row["contract_id"],
                contract_version=row["contract_version"],
                confirmation_fingerprint=row["confirmation_fingerprint"],
            ),
            reviewer_issuer=row["reviewer_issuer"],
            reviewer_subject=row["reviewer_subject"],
            reviewer_organization_id=row["reviewer_organization_id"],
            reviewer_display_name_snapshot=row[
                "reviewer_display_name_snapshot"
            ],
            decision=ConfirmationDecision(row["decision"]),
            agreement_acknowledged=_boolean_from_integer(
                row["agreement_acknowledged"]
            ),
            rationale=row["rationale"],
            decided_at=_datetime_from_microseconds(row["decided_at_us"]),
            request_digest=RequestDigest(row["request_digest"]),
        )
    except (IndexError, KeyError, OverflowError, TypeError, ValueError):
        raise _PersistedLedgerCorruption from None


def _datetime_from_microseconds(value: object) -> datetime:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < 0
    ):
        raise ValueError
    try:
        return _EPOCH + timedelta(microseconds=value)
    except OverflowError:
        raise ValueError from None


def _boolean_from_integer(value: object) -> bool:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value not in (0, 1)
    ):
        raise ValueError
    return bool(value)
