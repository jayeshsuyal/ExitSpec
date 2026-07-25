"""Fail-closed bootstrap boundary for the durable confirmation ledger."""

from __future__ import annotations

import hmac
import os
import re
import sqlite3
import sys
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
    AuditEventType,
    AuditOutcome,
    ConfirmationAuditEvent,
    ConfirmationDecisionRecord,
    ContractBinding,
    ContractBindingMismatch,
    InvitationConsumed,
    InvitationExpired,
    InvitationIdentityConflict,
    InvitationRevocationRecord,
    InvitationRevoked,
    LedgerUnavailable,
    RequestDigest,
    ReviewInvitationRecord,
    RevocationReason,
    TokenDigest,
    TokenDigestConflict,
)
from .confirmations import ConfirmationDecision


__all__ = (
    "GuardedConnection",
    "GuardedCursor",
    "LedgerUnavailable",
    "SQLiteConfirmationReader",
    "SQLiteInvitationWriter",
    "bootstrap_confirmation_ledger",
    "open_existing_confirmation_ledger",
)

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_MAX_SQLITE_INTEGER = (1 << 63) - 1
_SHA256_DIGEST = re.compile(r"[a-f0-9]{64}")
_SUPPORTED_TOKEN_DIGEST_VERSION = "sha256-v1"
_SQLITE_ISSUANCE_METADATA = (
    ("adapter_name", "sqlite"),
    ("schema_version", "1"),
)
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
_AUDIT_SELECT = """
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
FROM main.confirmation_audit_events
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


class SQLiteInvitationWriter:
    """Atomic schema-v1 invitation issuance using a fresh connection per call.

    This deliberately narrow adapter owns only invitation issuance. It does not
    claim full ``ConfirmationStore`` conformance and retains no connection.
    The supplied factory must return a fresh guarded connection to an already
    validated ledger in the calling thread.
    """

    __slots__ = ("__connection_factory",)

    def __init__(
        self,
        connection_factory: Callable[[], GuardedConnection],
    ) -> None:
        if not callable(connection_factory):
            raise TypeError("connection_factory must be callable.")
        self.__connection_factory = connection_factory

    def issue_invitation(
        self,
        invitation: ReviewInvitationRecord,
    ) -> ReviewInvitationRecord:
        """Insert one invitation and its issuance audit, or replay it exactly."""

        (
            validated_invitation,
            issued_at_us,
            expires_at_us,
        ) = _validated_invitation_for_write(invitation)
        connection = _open_writer_connection(self.__connection_factory)

        committed = False
        try:
            try:
                if connection.in_transaction:
                    raise LedgerUnavailable()
                begin_cursor = connection.execute("BEGIN IMMEDIATE")
                begin_cursor.close()

                result = _issue_invitation_in_transaction(
                    connection,
                    validated_invitation,
                    issued_at_us=issued_at_us,
                    expires_at_us=expires_at_us,
                )

                commit_cursor = connection.execute("COMMIT")
                commit_cursor.close()
                if connection.in_transaction:
                    raise _PersistedLedgerCorruption
                committed = True
            except (
                LedgerUnavailable,
                _ConfirmationSQLiteError,
                OSError,
                sqlite3.Error,
                _PersistedLedgerCorruption,
            ):
                raise LedgerUnavailable() from None
        finally:
            _finish_writer_connection(
                connection,
                committed=committed,
                error_in_flight=sys.exception() is not None,
            )
        return result


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


def _validated_invitation_for_write(
    invitation: object,
) -> tuple[ReviewInvitationRecord, int, int]:
    if type(invitation) is not ReviewInvitationRecord:
        raise TypeError("invitation must be a ReviewInvitationRecord.")

    try:
        source_binding = invitation.binding
        source_token_digest = invitation.token_digest
        invitation_id = invitation.invitation_id
        token_digest_version = invitation.token_digest_version
        intended_organization_id = invitation.intended_organization_id
        issued_by_subject = invitation.issued_by_subject
        source_issued_at = invitation.issued_at
        source_expires_at = invitation.expires_at
    except AttributeError:
        raise TypeError(
            "invitation must be a complete ReviewInvitationRecord."
        ) from None

    if type(source_binding) is not ContractBinding:
        raise TypeError("invitation binding must be a ContractBinding.")
    try:
        contract_id = source_binding.contract_id
        contract_version = source_binding.contract_version
        confirmation_fingerprint = (
            source_binding.confirmation_fingerprint
        )
    except AttributeError:
        raise TypeError(
            "invitation binding must be a complete ContractBinding."
        ) from None

    if type(source_token_digest) is not TokenDigest:
        raise TypeError("invitation token_digest must be a TokenDigest.")
    try:
        token_digest_value = source_token_digest.value
    except AttributeError:
        raise TypeError(
            "invitation token_digest must be a complete TokenDigest."
        ) from None

    exact_string_fields = (
        ("invitation_id", invitation_id),
        ("contract_id", contract_id),
        ("contract_version", contract_version),
        ("confirmation_fingerprint", confirmation_fingerprint),
        ("token_digest", token_digest_value),
        ("token_digest_version", token_digest_version),
        ("intended_organization_id", intended_organization_id),
        ("issued_by_subject", issued_by_subject),
    )
    for field_name, value in exact_string_fields:
        if type(value) is not str:
            raise ValueError(
                "{0} must be an exact string.".format(field_name)
            )

    if type(source_issued_at) is not datetime:
        raise ValueError("issued_at must be an exact datetime.")
    if type(source_expires_at) is not datetime:
        raise ValueError("expires_at must be an exact datetime.")

    if token_digest_version != _SUPPORTED_TOKEN_DIGEST_VERSION:
        raise ValueError("token_digest_version must be sha256-v1.")

    try:
        normalized_issued_at = _validated_utc_datetime(source_issued_at)
    except ValueError:
        raise ValueError(
            "issued_at must be timezone-aware UTC and not precede "
            "the Unix epoch."
        ) from None
    try:
        normalized_expires_at = _validated_utc_datetime(source_expires_at)
    except ValueError:
        raise ValueError(
            "expires_at must be timezone-aware UTC and not precede "
            "the Unix epoch."
        ) from None

    issued_at = datetime(
        normalized_issued_at.year,
        normalized_issued_at.month,
        normalized_issued_at.day,
        normalized_issued_at.hour,
        normalized_issued_at.minute,
        normalized_issued_at.second,
        normalized_issued_at.microsecond,
        tzinfo=timezone.utc,
        fold=normalized_issued_at.fold,
    )
    expires_at = datetime(
        normalized_expires_at.year,
        normalized_expires_at.month,
        normalized_expires_at.day,
        normalized_expires_at.hour,
        normalized_expires_at.minute,
        normalized_expires_at.second,
        normalized_expires_at.microsecond,
        tzinfo=timezone.utc,
        fold=normalized_expires_at.fold,
    )
    binding = ContractBinding(
        contract_id=contract_id,
        contract_version=contract_version,
        confirmation_fingerprint=confirmation_fingerprint,
    )
    token_digest = TokenDigest(token_digest_value)
    validated = ReviewInvitationRecord(
        invitation_id=invitation_id,
        binding=binding,
        token_digest=token_digest,
        token_digest_version=token_digest_version,
        intended_organization_id=intended_organization_id,
        issued_by_subject=issued_by_subject,
        issued_at=issued_at,
        expires_at=expires_at,
    )
    issued_at_us = _microseconds_from_utc(issued_at)
    expires_at_us = _microseconds_from_utc(expires_at)
    return validated, issued_at_us, expires_at_us


def _microseconds_from_utc(value: datetime) -> int:
    delta = value - _EPOCH
    microseconds = (
        delta.days * 86_400_000_000
        + delta.seconds * 1_000_000
        + delta.microseconds
    )
    if microseconds < 0:
        raise ValueError("timestamp must not precede the Unix epoch.")
    return microseconds


def _open_writer_connection(
    connection_factory: Callable[[], GuardedConnection],
) -> GuardedConnection:
    try:
        candidate = connection_factory()
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
            except Exception:
                # Disposal must not replace the validation failure already
                # in flight. BaseException subclasses still propagate.
                pass
    return cast(GuardedConnection, candidate)


def _finish_writer_connection(
    connection: GuardedConnection,
    *,
    committed: bool,
    error_in_flight: bool,
) -> None:
    cleanup_infrastructure_failed = False
    cleanup_programmer_error: Exception | None = None
    try:
        if not committed:
            # This also neutralizes a transaction supplied by a bad factory
            # and a transaction whose COMMIT outcome was ambiguous.
            try:
                connection.rollback()
            except Exception as error:
                if isinstance(
                    error,
                    (_ConfirmationSQLiteError, OSError, sqlite3.Error),
                ):
                    cleanup_infrastructure_failed = True
                else:
                    cleanup_programmer_error = error
    finally:
        try:
            connection.close()
        except Exception as error:
            if isinstance(
                error,
                (_ConfirmationSQLiteError, OSError, sqlite3.Error),
            ):
                cleanup_infrastructure_failed = True
            elif cleanup_programmer_error is None:
                cleanup_programmer_error = error

    if error_in_flight:
        return
    if cleanup_programmer_error is not None:
        raise cleanup_programmer_error
    if cleanup_infrastructure_failed:
        raise LedgerUnavailable() from None


def _issue_invitation_in_transaction(
    connection: GuardedConnection,
    invitation: ReviewInvitationRecord,
    *,
    issued_at_us: int,
    expires_at_us: int,
) -> ReviewInvitationRecord:
    (
        stored_invitations,
        audit_events,
        digest_matches,
        bindings_by_version,
    ) = _read_validated_invitation_write_state(
        connection,
        invitation.token_digest,
    )

    existing_index = next(
        (
            index
            for index, stored in enumerate(stored_invitations)
            if stored.invitation_id == invitation.invitation_id
        ),
        None,
    )
    if existing_index is not None:
        existing = stored_invitations[existing_index]
        if (
            digest_matches[existing_index]
            and _same_non_secret_invitation_fields(
                existing,
                invitation,
            )
        ):
            return existing
        raise InvitationIdentityConflict(
            "Invitation identity is already bound to different content."
        )

    referenced_binding = next(
        (
            event.binding
            for event in audit_events
            if event.invitation_id == invitation.invitation_id
        ),
        None,
    )
    if (
        referenced_binding is not None
        and referenced_binding != invitation.binding
    ):
        raise InvitationIdentityConflict(
            "Invitation identity is already bound to different content."
        )

    digest_owners = tuple(
        stored
        for stored, is_match in zip(
            stored_invitations,
            digest_matches,
            strict=True,
        )
        if is_match
    )
    if len(digest_owners) > 1:
        raise _PersistedLedgerCorruption
    if digest_owners:
        raise TokenDigestConflict(
            "Token digest is already bound to another invitation."
        )

    contract_version = (
        invitation.binding.contract_id,
        invitation.binding.contract_version,
    )
    existing_binding = bindings_by_version.get(contract_version)
    if (
        existing_binding is not None
        and existing_binding != invitation.binding
    ):
        raise ContractBindingMismatch(
            "Contract ID and version is already bound to another fingerprint."
        )

    event_sequence = _next_audit_sequence(audit_events)
    event_id = _next_audit_event_id(audit_events, event_sequence)
    issuance_event = ConfirmationAuditEvent(
        event_id=event_id,
        event_sequence=event_sequence,
        event_type=AuditEventType.INVITATION_ISSUED,
        occurred_at=invitation.issued_at,
        binding=invitation.binding,
        outcome=AuditOutcome.SUCCEEDED,
        invitation_id=invitation.invitation_id,
        confirmation_id=None,
        actor_issuer=None,
        actor_subject=None,
        actor_organization_id=None,
        reason_code=None,
        trace_id=None,
        safe_metadata=_SQLITE_ISSUANCE_METADATA,
    )

    _insert_invitation(
        connection,
        invitation,
        issued_at_us=issued_at_us,
        expires_at_us=expires_at_us,
    )
    _insert_audit_event(connection, issuance_event)
    return invitation


def _same_non_secret_invitation_fields(
    left: ReviewInvitationRecord,
    right: ReviewInvitationRecord,
) -> bool:
    return (
        left.invitation_id == right.invitation_id
        and left.binding == right.binding
        and left.token_digest_version == right.token_digest_version
        and left.intended_organization_id
        == right.intended_organization_id
        and left.issued_by_subject == right.issued_by_subject
        and left.issued_at == right.issued_at
        and left.expires_at == right.expires_at
    )


def _read_validated_invitation_write_state(
    connection: GuardedConnection,
    token_digest: TokenDigest,
) -> tuple[
    tuple[ReviewInvitationRecord, ...],
    tuple[ConfirmationAuditEvent, ...],
    tuple[bool, ...],
    dict[tuple[str, str], ContractBinding],
]:
    invitation_rows = _fetchall(
        connection,
        _INVITATION_SELECT + " ORDER BY invitation_id ASC",
    )

    digest_matches: list[bool] = []
    persisted_corruption = False
    invalid_comparison_value = "0" * 64
    for row in invitation_rows:
        try:
            stored_digest = row["token_digest"]
        except (IndexError, KeyError, TypeError):
            stored_digest = None
        valid_digest = (
            isinstance(stored_digest, str)
            and _SHA256_DIGEST.fullmatch(stored_digest) is not None
        )
        comparison_value = (
            stored_digest
            if valid_digest
            else invalid_comparison_value
        )
        digest_matches.append(
            hmac.compare_digest(
                comparison_value,
                token_digest.value,
            )
        )
        if not valid_digest:
            persisted_corruption = True

    audit_rows = _fetchall(
        connection,
        _AUDIT_SELECT + " ORDER BY event_sequence ASC, event_id ASC",
    )
    revocation_rows = _fetchall(
        connection,
        _REVOCATION_SELECT + " ORDER BY invitation_id ASC",
    )

    invitations: list[ReviewInvitationRecord] = []
    for row in invitation_rows:
        try:
            invitations.append(_invitation_from_row(row))
        except _PersistedLedgerCorruption:
            persisted_corruption = True

    audit_events: list[ConfirmationAuditEvent] = []
    for row in audit_rows:
        try:
            audit_events.append(_audit_event_from_row(row))
        except _PersistedLedgerCorruption:
            persisted_corruption = True

    revocations: list[InvitationRevocationRecord] = []
    for row in revocation_rows:
        try:
            revocations.append(_revocation_from_row(row))
        except _PersistedLedgerCorruption:
            persisted_corruption = True

    if persisted_corruption:
        raise _PersistedLedgerCorruption

    invitation_tuple = tuple(invitations)
    audit_tuple = tuple(audit_events)
    bindings_by_version = _validate_invitation_write_state(
        invitation_tuple,
        audit_tuple,
        tuple(revocations),
    )
    return (
        invitation_tuple,
        audit_tuple,
        tuple(digest_matches),
        bindings_by_version,
    )


def _validate_invitation_write_state(
    invitations: tuple[ReviewInvitationRecord, ...],
    audit_events: tuple[ConfirmationAuditEvent, ...],
    revocations: tuple[InvitationRevocationRecord, ...],
) -> dict[tuple[str, str], ContractBinding]:
    invitation_ids: set[str] = set()
    token_digests: set[str] = set()
    bindings_by_version: dict[tuple[str, str], ContractBinding] = {}
    invitations_by_id: dict[str, ReviewInvitationRecord] = {}
    for invitation in invitations:
        if (
            invitation.invitation_id in invitation_ids
            or invitation.token_digest.value in token_digests
        ):
            raise _PersistedLedgerCorruption
        invitation_ids.add(invitation.invitation_id)
        token_digests.add(invitation.token_digest.value)
        invitations_by_id[invitation.invitation_id] = invitation
        _remember_persisted_binding(
            bindings_by_version,
            invitation.binding,
        )

    revocations_by_invitation: dict[str, InvitationRevocationRecord] = {}
    for revocation in revocations:
        if revocation.invitation_id in revocations_by_invitation:
            raise _PersistedLedgerCorruption
        invitation = invitations_by_id.get(revocation.invitation_id)
        if invitation is None:
            raise _PersistedLedgerCorruption
        _validate_revocation(invitation, revocation)
        revocations_by_invitation[revocation.invitation_id] = revocation

    event_ids: set[str] = set()
    event_sequences: set[int] = set()
    audit_bindings_by_invitation: dict[str, ContractBinding] = {}
    creation_events_by_invitation: dict[
        str,
        list[ConfirmationAuditEvent],
    ] = {}
    revocation_events_by_invitation: dict[
        str,
        list[ConfirmationAuditEvent],
    ] = {}
    for event in audit_events:
        if (
            event.event_id in event_ids
            or event.event_sequence in event_sequences
        ):
            raise _PersistedLedgerCorruption
        event_ids.add(event.event_id)
        event_sequences.add(event.event_sequence)
        _remember_persisted_binding(
            bindings_by_version,
            event.binding,
        )
        if event.invitation_id is not None:
            referenced_binding = audit_bindings_by_invitation.setdefault(
                event.invitation_id,
                event.binding,
            )
            if referenced_binding != event.binding:
                raise _PersistedLedgerCorruption
            invitation = invitations_by_id.get(event.invitation_id)
            # Rejection audits may legitimately name an invitation that was
            # never stored. Successful and replayed facts must reference a
            # durable invitation, and stored identities have an exact binding.
            if invitation is None:
                if event.outcome != AuditOutcome.REJECTED:
                    raise _PersistedLedgerCorruption
            elif event.binding != invitation.binding:
                raise _PersistedLedgerCorruption
        if event.event_type in {
            AuditEventType.INVITATION_ISSUED,
            AuditEventType.INVITATION_REISSUED,
        }:
            if event.invitation_id is None:
                raise _PersistedLedgerCorruption
            creation_events_by_invitation.setdefault(
                event.invitation_id,
                [],
            ).append(event)
        elif event.event_type == AuditEventType.INVITATION_REVOKED:
            if event.invitation_id is None:
                raise _PersistedLedgerCorruption
            revocation_events_by_invitation.setdefault(
                event.invitation_id,
                [],
            ).append(event)

    for invitation_id, events in revocation_events_by_invitation.items():
        invitation = invitations_by_id.get(invitation_id)
        revocation = revocations_by_invitation.get(invitation_id)
        if (
            invitation is None
            or revocation is None
            or len(events) != 1
            or not _is_valid_revocation_audit_event(
                events[0],
                invitation,
                revocation,
            )
        ):
            raise _PersistedLedgerCorruption

    for invitation_id in revocations_by_invitation:
        if len(revocation_events_by_invitation.get(invitation_id, [])) != 1:
            raise _PersistedLedgerCorruption

    claimed_reissue_sources: set[str] = set()
    for invitation_id, events in creation_events_by_invitation.items():
        invitation = invitations_by_id.get(invitation_id)
        if invitation is None or len(events) != 1:
            raise _PersistedLedgerCorruption
        creation_event = events[0]
        if creation_event.event_type == AuditEventType.INVITATION_ISSUED:
            valid_provenance = _is_valid_issuance_provenance_event(
                creation_event,
                invitation,
            )
        else:
            valid_provenance = _is_valid_reissuance_provenance_event(
                creation_event,
                invitation,
            )
            if valid_provenance:
                source_invitation_id = _reissue_source_invitation_id(
                    replacement=invitation,
                    invitations=invitations,
                    revocations_by_invitation=revocations_by_invitation,
                    audit_events=audit_events,
                )
                if source_invitation_id in claimed_reissue_sources:
                    raise _PersistedLedgerCorruption
                claimed_reissue_sources.add(source_invitation_id)
        if not valid_provenance:
            raise _PersistedLedgerCorruption

    for revocation in revocations:
        if (
            revocation.reason_code == RevocationReason.REISSUED
            and revocation.invitation_id not in claimed_reissue_sources
        ):
            raise _PersistedLedgerCorruption

    for invitation in invitations:
        events = creation_events_by_invitation.get(
            invitation.invitation_id,
            [],
        )
        if len(events) != 1:
            raise _PersistedLedgerCorruption

    return bindings_by_version


def _remember_persisted_binding(
    bindings_by_version: dict[tuple[str, str], ContractBinding],
    binding: ContractBinding,
) -> None:
    contract_version = (
        binding.contract_id,
        binding.contract_version,
    )
    existing = bindings_by_version.setdefault(contract_version, binding)
    if existing != binding:
        raise _PersistedLedgerCorruption


def _is_valid_issuance_provenance_event(
    event: ConfirmationAuditEvent,
    invitation: ReviewInvitationRecord,
) -> bool:
    return (
        event.event_type == AuditEventType.INVITATION_ISSUED
        and event.occurred_at == invitation.issued_at
        and event.binding == invitation.binding
        and event.outcome == AuditOutcome.SUCCEEDED
        and event.invitation_id == invitation.invitation_id
        and event.confirmation_id is None
        and event.reason_code is None
    )


def _is_valid_reissuance_provenance_event(
    event: ConfirmationAuditEvent,
    invitation: ReviewInvitationRecord,
) -> bool:
    return (
        event.event_type == AuditEventType.INVITATION_REISSUED
        and event.occurred_at == invitation.issued_at
        and event.binding == invitation.binding
        and event.outcome == AuditOutcome.SUCCEEDED
        and event.invitation_id == invitation.invitation_id
        and event.confirmation_id is None
        and event.reason_code == RevocationReason.REISSUED.value
    )


def _is_valid_revocation_audit_event(
    event: ConfirmationAuditEvent,
    invitation: ReviewInvitationRecord,
    revocation: InvitationRevocationRecord,
) -> bool:
    return (
        event.event_type == AuditEventType.INVITATION_REVOKED
        and event.occurred_at == revocation.revoked_at
        and event.binding == invitation.binding
        and event.outcome == AuditOutcome.SUCCEEDED
        and event.invitation_id == invitation.invitation_id
        and event.confirmation_id is None
        and event.reason_code == revocation.reason_code.value
    )


def _reissue_source_invitation_id(
    *,
    replacement: ReviewInvitationRecord,
    invitations: tuple[ReviewInvitationRecord, ...],
    revocations_by_invitation: dict[str, InvitationRevocationRecord],
    audit_events: tuple[ConfirmationAuditEvent, ...],
) -> str:
    candidates: list[
        tuple[ReviewInvitationRecord, InvitationRevocationRecord]
    ] = []
    for source in invitations:
        if (
            source.invitation_id == replacement.invitation_id
            or source.binding != replacement.binding
            or source.issued_at > replacement.issued_at
        ):
            continue
        revocation = revocations_by_invitation.get(source.invitation_id)
        if (
            revocation is not None
            and revocation.revoked_at == replacement.issued_at
            and revocation.reason_code == RevocationReason.REISSUED
        ):
            candidates.append((source, revocation))

    if len(candidates) != 1:
        raise _PersistedLedgerCorruption

    source, revocation = candidates[0]
    matching_revocation_audits = tuple(
        event
        for event in audit_events
        if (
            event.event_type == AuditEventType.INVITATION_REVOKED
            and event.occurred_at == revocation.revoked_at
            and event.binding == source.binding
            and event.outcome == AuditOutcome.SUCCEEDED
            and event.invitation_id == source.invitation_id
            and event.confirmation_id is None
            and event.reason_code == RevocationReason.REISSUED.value
        )
    )
    if len(matching_revocation_audits) != 1:
        raise _PersistedLedgerCorruption
    return source.invitation_id


def _next_audit_sequence(
    audit_events: tuple[ConfirmationAuditEvent, ...],
) -> int:
    highest_sequence = max(
        (event.event_sequence for event in audit_events),
        default=0,
    )
    if highest_sequence >= _MAX_SQLITE_INTEGER:
        raise _PersistedLedgerCorruption
    return highest_sequence + 1


def _next_audit_event_id(
    audit_events: tuple[ConfirmationAuditEvent, ...],
    event_sequence: int,
) -> str:
    canonical = "audit-sqlite-{0:020d}".format(event_sequence)
    existing_ids = {event.event_id for event in audit_events}
    if canonical not in existing_ids:
        return canonical

    # Schema v1 allows imported IDs to be independent of sequence. There are
    # at most N occupied IDs, so canonical plus N deterministic candidates
    # guarantees a free ID. A SQLite ledger cannot contain enough rows for the
    # decimal suffix to exceed the schema's 64-character limit.
    for suffix in range(1, len(existing_ids) + 1):
        candidate = f"{canonical}-{suffix}"
        if candidate not in existing_ids:
            return candidate
    raise _PersistedLedgerCorruption


def _insert_invitation(
    connection: GuardedConnection,
    invitation: ReviewInvitationRecord,
    *,
    issued_at_us: int,
    expires_at_us: int,
) -> None:
    _execute_write(
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
            invitation.invitation_id,
            invitation.binding.contract_id,
            invitation.binding.contract_version,
            invitation.binding.confirmation_fingerprint,
            invitation.token_digest.value,
            invitation.token_digest_version,
            invitation.intended_organization_id,
            invitation.issued_by_subject,
            issued_at_us,
            expires_at_us,
        ),
    )


def _insert_audit_event(
    connection: GuardedConnection,
    event: ConfirmationAuditEvent,
) -> None:
    metadata = dict(event.safe_metadata)
    _execute_write(
        connection,
        """
        INSERT INTO main.confirmation_audit_events (
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
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event.event_id,
            event.event_sequence,
            event.event_type.value,
            _microseconds_from_utc(event.occurred_at),
            event.binding.contract_id,
            event.binding.contract_version,
            event.binding.confirmation_fingerprint,
            event.invitation_id,
            event.confirmation_id,
            event.actor_issuer,
            event.actor_subject,
            event.actor_organization_id,
            event.outcome.value,
            event.reason_code,
            event.trace_id,
            metadata["schema_version"],
            metadata.get("adapter_name"),
            metadata.get("adapter_version"),
        ),
    )


def _execute_write(
    connection: GuardedConnection,
    sql: str,
    parameters: object,
) -> None:
    cursor = connection.execute(sql, parameters)
    cursor.close()


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
        error_in_flight = sys.exception() is not None
        try:
            cursor.close()
        except Exception as error:
            # Cursor cleanup must not replace the operation's primary error.
            if not error_in_flight:
                if isinstance(
                    error,
                    (_ConfirmationSQLiteError, OSError, sqlite3.Error),
                ):
                    raise LedgerUnavailable() from None
                raise


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


def _audit_event_from_row(row: sqlite3.Row) -> ConfirmationAuditEvent:
    try:
        safe_metadata: list[tuple[str, str]] = [
            ("schema_version", row["metadata_schema_version"]),
        ]
        if row["metadata_adapter_name"] is not None:
            safe_metadata.append(
                ("adapter_name", row["metadata_adapter_name"])
            )
        if row["metadata_adapter_version"] is not None:
            safe_metadata.append(
                ("adapter_version", row["metadata_adapter_version"])
            )
        return ConfirmationAuditEvent(
            event_id=row["event_id"],
            event_sequence=row["event_sequence"],
            event_type=AuditEventType(row["event_type"]),
            occurred_at=_datetime_from_microseconds(
                row["occurred_at_us"]
            ),
            binding=ContractBinding(
                contract_id=row["contract_id"],
                contract_version=row["contract_version"],
                confirmation_fingerprint=row[
                    "confirmation_fingerprint"
                ],
            ),
            outcome=AuditOutcome(row["outcome"]),
            invitation_id=row["invitation_id"],
            confirmation_id=row["confirmation_id"],
            actor_issuer=row["actor_issuer"],
            actor_subject=row["actor_subject"],
            actor_organization_id=row["actor_organization_id"],
            reason_code=row["reason_code"],
            trace_id=row["trace_id"],
            safe_metadata=tuple(safe_metadata),
        )
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
    return _revocation_from_row(rows[0])


def _revocation_from_row(
    row: sqlite3.Row,
) -> InvitationRevocationRecord:
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
