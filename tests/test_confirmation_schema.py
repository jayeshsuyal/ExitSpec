from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
import hashlib
from itertools import product
from pathlib import Path
import sqlite3
from typing import Any

import pytest

import exitspec.confirmation_schema as confirmation_schema_module
import exitspec.confirmation_sqlite as confirmation_sqlite_module
from exitspec.confirmation_schema import (
    CONFIRMATION_LEDGER_INDEX_NAMES,
    CONFIRMATION_LEDGER_MIGRATION,
    CONFIRMATION_LEDGER_MIGRATIONS,
    CONFIRMATION_LEDGER_SQL,
    CONFIRMATION_LEDGER_TRIGGER_NAMES,
    MINIMUM_CONFIRMATION_SQLITE_VERSION,
    require_confirmation_schema_runtime,
    validate_confirmation_schema,
)
from exitspec.confirmation_sqlite import (
    AppliedMigration,
    LedgerUnavailable,
    Migration,
    MigrationFailed,
    apply_migrations,
    open_confirmation_database,
    read_applied_migrations,
)


MIGRATED_AT = datetime(2026, 7, 24, 20, 15, tzinfo=timezone.utc)
FINGERPRINT = "a" * 64
OTHER_FINGERPRINT = "b" * 64
TOKEN_DIGEST = "c" * 64
REQUEST_DIGEST = "d" * 64
CONFIRMATION_ID = "cnf_" + "e" * 64
ISSUED_AT_US = 1_000_000
DECIDED_AT_US = 1_500_000
EXPIRES_AT_US = 2_000_000
PYTHON_STRIP_CODEPOINTS = (
    9,
    10,
    11,
    12,
    13,
    28,
    29,
    30,
    31,
    32,
    133,
    160,
    5760,
    8192,
    8193,
    8194,
    8195,
    8196,
    8197,
    8198,
    8199,
    8200,
    8201,
    8202,
    8232,
    8233,
    8239,
    8287,
    12288,
)
PR6_SCHEMA_MIGRATIONS_SQL = """
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

DOMAIN_TABLES = {
    "review_invitations",
    "invitation_revocations",
    "confirmation_decisions",
    "idempotency_operations",
    "confirmation_audit_events",
}
DOMAIN_INDEXES = {
    "review_invitations_binding_idx",
    "review_invitations_expiry_idx",
    "confirmation_audit_events_binding_sequence_idx",
    "confirmation_audit_events_invitation_sequence_idx",
    "confirmation_audit_events_confirmation_sequence_idx",
}
DOMAIN_TRIGGERS = {
    "review_invitations_consistent_fingerprint",
    "confirmation_audit_events_consistent_fingerprint",
    "confirmation_decisions_active_invitation",
    "review_invitations_block_replace",
    "invitation_revocations_block_replace",
    "confirmation_decisions_block_replace",
    "idempotency_operations_block_replace",
    "confirmation_audit_events_block_replace",
    "review_invitations_block_update",
    "review_invitations_block_delete",
    "invitation_revocations_block_update",
    "invitation_revocations_block_delete",
    "confirmation_decisions_block_update",
    "confirmation_decisions_block_delete",
    "idempotency_operations_block_update",
    "idempotency_operations_block_delete",
    "confirmation_audit_events_block_update",
    "confirmation_audit_events_block_delete",
}


@pytest.fixture
def ledger(tmp_path: Path) -> Any:
    connection = open_confirmation_database(tmp_path / "confirmation.db")
    apply_migrations(
        connection,
        CONFIRMATION_LEDGER_MIGRATIONS,
        now=MIGRATED_AT,
    )
    try:
        yield connection
    finally:
        connection.close()


def _insert_invitation(connection: Any, **changes: object) -> None:
    values: dict[str, object] = {
        "invitation_id": "review-first",
        "contract_id": "contract-1",
        "contract_version": "v1",
        "confirmation_fingerprint": FINGERPRINT,
        "token_digest": TOKEN_DIGEST,
        "token_digest_version": "sha256-v1",
        "intended_organization_id": "org-1",
        "issued_by_subject": "employee-1",
        "issued_at_us": ISSUED_AT_US,
        "expires_at_us": EXPIRES_AT_US,
    }
    values.update(changes)
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
        ) VALUES (
            :invitation_id,
            :contract_id,
            :contract_version,
            :confirmation_fingerprint,
            :token_digest,
            :token_digest_version,
            :intended_organization_id,
            :issued_by_subject,
            :issued_at_us,
            :expires_at_us
        )
        """,
        values,
    )


def _insert_revocation(connection: Any, **changes: object) -> None:
    values: dict[str, object] = {
        "invitation_id": "review-first",
        "revoked_at_us": DECIDED_AT_US,
        "revoked_by_subject": "employee-1",
        "reason_code": "MANUAL",
    }
    values.update(changes)
    connection.execute(
        """
        INSERT INTO invitation_revocations (
            invitation_id,
            revoked_at_us,
            revoked_by_subject,
            reason_code
        ) VALUES (
            :invitation_id,
            :revoked_at_us,
            :revoked_by_subject,
            :reason_code
        )
        """,
        values,
    )


def _insert_decision(connection: Any, **changes: object) -> None:
    values: dict[str, object] = {
        "confirmation_id": CONFIRMATION_ID,
        "invitation_id": "review-first",
        "contract_id": "contract-1",
        "contract_version": "v1",
        "confirmation_fingerprint": FINGERPRINT,
        "reviewer_issuer": "https://identity.example",
        "reviewer_subject": "customer-1",
        "reviewer_organization_id": "org-1",
        "reviewer_display_name_snapshot": "Customer Reviewer",
        "decision": "CONFIRM",
        "agreement_acknowledged": 1,
        "rationale": "",
        "decided_at_us": DECIDED_AT_US,
        "request_digest": REQUEST_DIGEST,
    }
    values.update(changes)
    connection.execute(
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
        ) VALUES (
            :confirmation_id,
            :invitation_id,
            :contract_id,
            :contract_version,
            :confirmation_fingerprint,
            :reviewer_issuer,
            :reviewer_subject,
            :reviewer_organization_id,
            :reviewer_display_name_snapshot,
            :decision,
            :agreement_acknowledged,
            :rationale,
            :decided_at_us,
            :request_digest
        )
        """,
        values,
    )


def _insert_operation(connection: Any, **changes: object) -> None:
    values: dict[str, object] = {
        "operation_digest": "1" * 64,
        "contract_id": "contract-1",
        "contract_version": "v1",
        "idempotency_key_digest": "2" * 64,
        "request_digest": REQUEST_DIGEST,
        "confirmation_id": CONFIRMATION_ID,
        "created_at_us": DECIDED_AT_US,
    }
    values.update(changes)
    connection.execute(
        """
        INSERT INTO idempotency_operations (
            operation_digest,
            contract_id,
            contract_version,
            idempotency_key_digest,
            request_digest,
            confirmation_id,
            created_at_us
        ) VALUES (
            :operation_digest,
            :contract_id,
            :contract_version,
            :idempotency_key_digest,
            :request_digest,
            :confirmation_id,
            :created_at_us
        )
        """,
        values,
    )


def _insert_audit_event(connection: Any, **changes: object) -> None:
    values: dict[str, object] = {
        "event_id": "audit-first",
        "event_sequence": 1,
        "event_type": "INVITATION_ISSUED",
        "occurred_at_us": ISSUED_AT_US,
        "contract_id": "contract-1",
        "contract_version": "v1",
        "confirmation_fingerprint": FINGERPRINT,
        "invitation_id": "review-first",
        "confirmation_id": None,
        "actor_issuer": "https://identity.example",
        "actor_subject": "employee-1",
        "actor_organization_id": "org-1",
        "outcome": "SUCCEEDED",
        "reason_code": None,
        "trace_id": "3" * 32,
        "metadata_schema_version": "1",
        "metadata_adapter_name": None,
        "metadata_adapter_version": None,
    }
    values.update(changes)
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
        ) VALUES (
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


def _object_names(connection: Any, object_type: str) -> set[str]:
    return {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = ?",
            (object_type,),
        ).fetchall()
    }


def test_migration_is_one_frozen_exact_version() -> None:
    migration = CONFIRMATION_LEDGER_MIGRATION

    assert CONFIRMATION_LEDGER_MIGRATIONS == (migration,)
    assert migration.version == 1
    assert migration.name == "confirmation_ledger"
    assert migration.sql == CONFIRMATION_LEDGER_SQL
    assert migration.checksum == hashlib.sha256(
        CONFIRMATION_LEDGER_SQL.encode("utf-8")
    ).hexdigest()
    assert "schema_migrations" not in CONFIRMATION_LEDGER_SQL
    with pytest.raises(FrozenInstanceError):
        migration.name = "changed"  # type: ignore[misc]


def test_confirmation_schema_requires_sqlite_3_37_with_safe_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert MINIMUM_CONFIRMATION_SQLITE_VERSION == (3, 37, 0)
    monkeypatch.setattr(
        confirmation_schema_module.sqlite3,
        "sqlite_version_info",
        (3, 36, 99),
    )

    with pytest.raises(
        LedgerUnavailable,
        match="^Confirmation ledger is unavailable\\.$",
    ) as error:
        require_confirmation_schema_runtime()
    assert error.value.args == ("Confirmation ledger is unavailable.",)

    monkeypatch.setattr(
        confirmation_schema_module.sqlite3,
        "sqlite_version_info",
        MINIMUM_CONFIRMATION_SQLITE_VERSION,
    )
    require_confirmation_schema_runtime()


def test_domain_schema_preserves_and_reopens_pr6_bootstrap_shape(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "confirmation.db"
    created = open_confirmation_database(database_path)
    try:
        assert created.execute(
            """
            SELECT sql
            FROM sqlite_master
            WHERE type = 'table'
              AND name = 'schema_migrations'
            """
        ).fetchone()[0] == PR6_SCHEMA_MIGRATIONS_SQL
    finally:
        created.close()

    reopened = open_confirmation_database(database_path)
    try:
        assert apply_migrations(
            reopened,
            CONFIRMATION_LEDGER_MIGRATIONS,
            now=MIGRATED_AT,
        ) == 1
    finally:
        reopened.close()


def test_clean_apply_has_exact_domain_shape_and_links(ledger: Any) -> None:
    assert _object_names(ledger, "table") == DOMAIN_TABLES | {
        "schema_migrations"
    }
    assert DOMAIN_INDEXES <= _object_names(ledger, "index")
    assert DOMAIN_TRIGGERS <= _object_names(ledger, "trigger")
    assert CONFIRMATION_LEDGER_INDEX_NAMES == DOMAIN_INDEXES
    assert CONFIRMATION_LEDGER_TRIGGER_NAMES == DOMAIN_TRIGGERS
    strict_tables = {
        row["name"]: row["strict"]
        for row in ledger.execute("PRAGMA table_list").fetchall()
        if row["name"] in DOMAIN_TABLES
    }
    assert strict_tables == {name: 1 for name in DOMAIN_TABLES}
    assert read_applied_migrations(ledger) == (
        AppliedMigration(
            version=1,
            name="confirmation_ledger",
            checksum=CONFIRMATION_LEDGER_MIGRATION.checksum,
            applied_at_us=1784924100000000,
        ),
    )

    expected_columns = {
        "review_invitations": {
            "invitation_id",
            "contract_id",
            "contract_version",
            "confirmation_fingerprint",
            "token_digest",
            "token_digest_version",
            "intended_organization_id",
            "issued_by_subject",
            "issued_at_us",
            "expires_at_us",
        },
        "invitation_revocations": {
            "invitation_id",
            "revoked_at_us",
            "revoked_by_subject",
            "reason_code",
        },
        "confirmation_decisions": {
            "confirmation_id",
            "invitation_id",
            "contract_id",
            "contract_version",
            "confirmation_fingerprint",
            "reviewer_issuer",
            "reviewer_subject",
            "reviewer_organization_id",
            "reviewer_display_name_snapshot",
            "decision",
            "agreement_acknowledged",
            "rationale",
            "decided_at_us",
            "request_digest",
        },
        "idempotency_operations": {
            "operation_digest",
            "contract_id",
            "contract_version",
            "idempotency_key_digest",
            "request_digest",
            "confirmation_id",
            "created_at_us",
        },
        "confirmation_audit_events": {
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
        },
    }
    for table_name, columns in expected_columns.items():
        assert {
            row["name"]
            for row in ledger.execute(
                "PRAGMA table_info({0})".format(table_name)
            ).fetchall()
        } == columns

    foreign_key_targets = {
        table_name: {
            row["table"]
            for row in ledger.execute(
                "PRAGMA foreign_key_list({0})".format(table_name)
            ).fetchall()
        }
        for table_name in DOMAIN_TABLES
    }
    assert foreign_key_targets == {
        "review_invitations": set(),
        "invitation_revocations": {"review_invitations"},
        "confirmation_decisions": {"review_invitations"},
        "idempotency_operations": {"confirmation_decisions"},
        "confirmation_audit_events": set(),
    }
    assert ledger.execute("PRAGMA foreign_key_check").fetchall() == []
    validate_confirmation_schema(ledger)


def test_reopen_and_reapply_preserves_exact_schema(tmp_path: Path) -> None:
    database_path = tmp_path / "confirmation.db"
    first = open_confirmation_database(database_path)
    assert apply_migrations(
        first,
        CONFIRMATION_LEDGER_MIGRATIONS,
        now=MIGRATED_AT,
    ) == 1
    first_objects = {
        object_type: _object_names(first, object_type)
        for object_type in ("table", "index", "trigger")
    }
    first.close()

    reopened = open_confirmation_database(database_path)
    try:
        assert apply_migrations(
            reopened,
            CONFIRMATION_LEDGER_MIGRATIONS,
            now=datetime(2026, 7, 25, tzinfo=timezone.utc),
        ) == 1
        assert {
            object_type: _object_names(reopened, object_type)
            for object_type in ("table", "index", "trigger")
        } == first_objects
        validate_confirmation_schema(reopened)
    finally:
        reopened.close()


@pytest.mark.parametrize(
    "tampering_sql",
    (
        "DROP TABLE invitation_revocations",
        "DROP INDEX review_invitations_expiry_idx",
        "DROP TRIGGER review_invitations_block_update",
        "ALTER TABLE review_invitations ADD COLUMN forged TEXT",
        """
        DROP TRIGGER confirmation_decisions_block_delete;
        CREATE TRIGGER confirmation_decisions_block_delete
        AFTER DELETE ON confirmation_decisions
        BEGIN
            SELECT 1;
        END;
        """,
        """
        CREATE INDEX review_invitations_unexpected_idx
        ON review_invitations (issued_at_us)
        """,
        """
        CREATE TABLE unrelated_events (id INTEGER);
        CREATE TRIGGER unrelated_domain_mutation
        AFTER INSERT ON unrelated_events
        BEGIN
            DELETE FROM confirmation_audit_events;
        END;
        """,
    ),
    ids=(
        "missing-table",
        "missing-index",
        "missing-trigger",
        "changed-table",
        "changed-trigger",
        "extra-index",
        "cross-table-trigger",
    ),
)
def test_exact_validator_detects_raw_same_user_schema_drift(
    tmp_path: Path,
    tampering_sql: str,
) -> None:
    database_path = tmp_path / "confirmation.db"
    guarded = open_confirmation_database(database_path)
    apply_migrations(
        guarded,
        CONFIRMATION_LEDGER_MIGRATIONS,
        now=MIGRATED_AT,
    )
    guarded.close()

    raw = sqlite3.connect(database_path)
    raw.executescript(tampering_sql)
    raw.close()

    reopened = open_confirmation_database(database_path)
    try:
        with pytest.raises(
            LedgerUnavailable,
            match="^Confirmation ledger is unavailable\\.$",
        ):
            validate_confirmation_schema(reopened)
    finally:
        reopened.close()


def test_exact_validator_rejects_raw_connection(tmp_path: Path) -> None:
    raw = sqlite3.connect(tmp_path / "raw.db")
    try:
        with pytest.raises(LedgerUnavailable):
            validate_confirmation_schema(raw)
    finally:
        raw.close()


@pytest.mark.parametrize(
    "changes",
    (
        {"invitation_id": "review-Bad"},
        {"contract_id": " "},
        {"contract_id": sqlite3.Binary(b"contract-1")},
        {"confirmation_fingerprint": "A" * 64},
        {"confirmation_fingerprint": sqlite3.Binary(b"a" * 64)},
        {"token_digest": "not-a-digest"},
        {"token_digest": sqlite3.Binary(b"c" * 64)},
        {"token_digest_version": " "},
        {"issued_by_subject": ""},
        {"issued_at_us": -1},
        {"expires_at_us": ISSUED_AT_US},
    ),
)
def test_invitation_rejects_invalid_identifiers_digests_text_and_time(
    ledger: Any,
    changes: dict[str, object],
) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        _insert_invitation(ledger, **changes)
    assert ledger.execute(
        "SELECT COUNT(*) FROM review_invitations"
    ).fetchone()[0] == 0


def test_schema_rejects_nul_in_all_text_authority_categories(
    ledger: Any,
) -> None:
    invitation_changes = (
        {"invitation_id": "review-first\x00hidden"},
        {"contract_id": "contract-1\x00hidden"},
        {"contract_version": "v1\x00hidden"},
        {"confirmation_fingerprint": FINGERPRINT + "\x00hidden"},
        {"token_digest": TOKEN_DIGEST + "\x00first"},
        {"token_digest": TOKEN_DIGEST + "\x00second"},
        {"token_digest_version": "sha256-v1\x00hidden"},
        {"intended_organization_id": "org-1\x00hidden"},
        {"issued_by_subject": "employee-1\x00hidden"},
    )
    for changes in invitation_changes:
        with pytest.raises(sqlite3.IntegrityError):
            _insert_invitation(ledger, **changes)
    assert ledger.execute(
        "SELECT COUNT(*) FROM review_invitations"
    ).fetchone()[0] == 0
    _insert_invitation(ledger)

    for changes in (
        {"invitation_id": "review-first\x00hidden"},
        {"revoked_by_subject": "s" * 256 + "\x00hidden"},
        {"reason_code": "MANUAL\x00hidden"},
    ):
        with pytest.raises(sqlite3.IntegrityError):
            _insert_revocation(ledger, **changes)
    assert ledger.execute(
        "SELECT COUNT(*) FROM invitation_revocations"
    ).fetchone()[0] == 0

    decision_changes = (
        {"confirmation_id": CONFIRMATION_ID + "\x00hidden"},
        {"invitation_id": "review-first\x00hidden"},
        {"contract_id": "contract-1\x00hidden"},
        {"contract_version": "v1\x00hidden"},
        {"confirmation_fingerprint": FINGERPRINT + "\x00hidden"},
        {"reviewer_issuer": "issuer\x00hidden"},
        {"reviewer_subject": "subject\x00hidden"},
        {"reviewer_organization_id": "org-1\x00hidden"},
        {"reviewer_display_name_snapshot": "Reviewer\x00hidden"},
        {"decision": "CONFIRM\x00hidden"},
        {"rationale": "r" * 2000 + "\x00hidden"},
        {"request_digest": REQUEST_DIGEST + "\x00hidden"},
    )
    for changes in decision_changes:
        with pytest.raises(sqlite3.IntegrityError):
            _insert_decision(ledger, **changes)
    assert ledger.execute(
        "SELECT COUNT(*) FROM confirmation_decisions"
    ).fetchone()[0] == 0
    _insert_decision(ledger)

    operation_changes = (
        {"operation_digest": "1" * 64 + "\x00hidden"},
        {"contract_id": "contract-1\x00hidden"},
        {"contract_version": "v1\x00hidden"},
        {"idempotency_key_digest": "2" * 64 + "\x00hidden"},
        {"request_digest": REQUEST_DIGEST + "\x00hidden"},
        {"confirmation_id": CONFIRMATION_ID + "\x00hidden"},
    )
    for changes in operation_changes:
        with pytest.raises(sqlite3.IntegrityError):
            _insert_operation(ledger, **changes)
    assert ledger.execute(
        "SELECT COUNT(*) FROM idempotency_operations"
    ).fetchone()[0] == 0

    audit_changes = (
        {"event_id": "audit-first\x00hidden"},
        {"event_type": "INVITATION_ISSUED\x00hidden"},
        {"contract_id": "contract-1\x00hidden"},
        {"contract_version": "v1\x00hidden"},
        {"confirmation_fingerprint": FINGERPRINT + "\x00hidden"},
        {"invitation_id": "review-first\x00hidden"},
        {
            "event_type": "DECISION_RECORDED",
            "confirmation_id": CONFIRMATION_ID + "\x00hidden",
        },
        {"actor_issuer": "issuer\x00hidden"},
        {"actor_subject": "s" * 256 + "\x00hidden"},
        {"actor_organization_id": "org-1\x00hidden"},
        {"outcome": "SUCCEEDED\x00hidden"},
        {
            "event_type": "INVITATION_REVOKED",
            "reason_code": "MANUAL\x00hidden",
        },
        {"trace_id": "3" * 32 + "\x00hidden"},
        {"metadata_schema_version": "1\x00hidden"},
        {"metadata_adapter_name": "sqlite\x00hidden"},
        {"metadata_adapter_version": "1.2\x00hidden"},
    )
    for changes in audit_changes:
        with pytest.raises(sqlite3.IntegrityError):
            _insert_audit_event(ledger, **changes)
    assert ledger.execute(
        "SELECT COUNT(*) FROM confirmation_audit_events"
    ).fetchone()[0] == 0


@pytest.mark.parametrize(
    "codepoint",
    PYTHON_STRIP_CODEPOINTS,
    ids=lambda codepoint: "U+{0:04X}".format(codepoint),
)
def test_non_empty_sql_text_matches_python_strip_for_all_whitespace(
    ledger: Any,
    codepoint: int,
) -> None:
    whitespace = chr(codepoint)
    assert whitespace.strip() == ""

    for changes in (
        {"contract_id": whitespace},
        {"contract_version": whitespace},
        {"token_digest_version": whitespace},
        {"intended_organization_id": whitespace},
        {"issued_by_subject": whitespace},
    ):
        with pytest.raises(sqlite3.IntegrityError):
            _insert_invitation(ledger, **changes)
    assert ledger.execute(
        "SELECT COUNT(*) FROM review_invitations"
    ).fetchone()[0] == 0
    _insert_invitation(ledger)

    with pytest.raises(sqlite3.IntegrityError):
        _insert_revocation(ledger, revoked_by_subject=whitespace)
    assert ledger.execute(
        "SELECT COUNT(*) FROM invitation_revocations"
    ).fetchone()[0] == 0

    for changes in (
        {"contract_id": whitespace},
        {"contract_version": whitespace},
        {"reviewer_issuer": whitespace},
        {"reviewer_subject": whitespace},
        {"reviewer_organization_id": whitespace},
        {"reviewer_display_name_snapshot": whitespace},
        {
            "decision": "REQUEST_CHANGES",
            "agreement_acknowledged": 0,
            "rationale": whitespace,
        },
    ):
        with pytest.raises(sqlite3.IntegrityError):
            _insert_decision(ledger, **changes)
    assert ledger.execute(
        "SELECT COUNT(*) FROM confirmation_decisions"
    ).fetchone()[0] == 0
    _insert_decision(ledger)

    for changes in (
        {"contract_id": whitespace},
        {"contract_version": whitespace},
    ):
        with pytest.raises(sqlite3.IntegrityError):
            _insert_operation(ledger, **changes)
    assert ledger.execute(
        "SELECT COUNT(*) FROM idempotency_operations"
    ).fetchone()[0] == 0

    for changes in (
        {"contract_id": whitespace},
        {"contract_version": whitespace},
        {"actor_issuer": whitespace},
        {"actor_subject": whitespace},
        {"actor_organization_id": whitespace},
    ):
        with pytest.raises(sqlite3.IntegrityError):
            _insert_audit_event(ledger, **changes)
    assert ledger.execute(
        "SELECT COUNT(*) FROM confirmation_audit_events"
    ).fetchone()[0] == 0


def test_non_empty_sql_text_does_not_overstrip_zero_width_space(
    ledger: Any,
) -> None:
    value = "\u200b"
    assert value.strip() == value
    _insert_invitation(ledger, contract_id=value)
    assert ledger.execute(
        "SELECT contract_id FROM review_invitations"
    ).fetchone()[0] == value


def test_schema_does_not_invent_limits_absent_from_domain_models(
    ledger: Any,
) -> None:
    long_value = "x" * 1_024
    _insert_invitation(
        ledger,
        contract_id=long_value,
        contract_version=long_value,
        token_digest_version=long_value,
        intended_organization_id=long_value,
        issued_by_subject=long_value,
    )
    _insert_decision(
        ledger,
        contract_id=long_value,
        contract_version=long_value,
        reviewer_issuer=long_value,
        reviewer_subject=long_value,
        reviewer_organization_id=long_value,
        reviewer_display_name_snapshot=long_value,
    )

    assert ledger.execute(
        "SELECT COUNT(*) FROM confirmation_decisions"
    ).fetchone()[0] == 1


def test_reissues_require_one_binding_fingerprint_and_unique_token(
    ledger: Any,
) -> None:
    _insert_invitation(ledger)
    _insert_invitation(
        ledger,
        invitation_id="review-reissued",
        token_digest="4" * 64,
    )
    _insert_invitation(
        ledger,
        invitation_id="review-new-version",
        contract_version="v2",
        confirmation_fingerprint=OTHER_FINGERPRINT,
        token_digest="5" * 64,
    )

    with pytest.raises(sqlite3.IntegrityError):
        _insert_invitation(
            ledger,
            invitation_id="review-conflict",
            confirmation_fingerprint=OTHER_FINGERPRINT,
            token_digest="6" * 64,
        )
    with pytest.raises(sqlite3.IntegrityError):
        _insert_invitation(
            ledger,
            invitation_id="review-token-reuse",
            contract_version="v3",
            token_digest=TOKEN_DIGEST,
        )

    assert ledger.execute(
        "SELECT COUNT(*) FROM review_invitations"
    ).fetchone()[0] == 3


def test_binding_only_audit_fact_also_freezes_the_fingerprint(
    ledger: Any,
) -> None:
    _insert_audit_event(
        ledger,
        event_type="CONTRACT_SUPERSEDED",
        invitation_id=None,
        confirmation_id=None,
        outcome="SUCCEEDED",
        reason_code="CONTRACT_SUPERSEDED",
    )

    with pytest.raises(
        sqlite3.IntegrityError,
        match="contract fingerprint conflict",
    ):
        _insert_invitation(
            ledger,
            confirmation_fingerprint=OTHER_FINGERPRINT,
        )


def test_revocation_requires_parent_reason_time_and_single_fact(
    ledger: Any,
) -> None:
    _insert_invitation(ledger)

    with pytest.raises(sqlite3.IntegrityError):
        _insert_revocation(ledger, invitation_id="review-missing")
    with pytest.raises(sqlite3.IntegrityError):
        _insert_revocation(ledger, reason_code="CUSTOMER_TEXT")
    with pytest.raises(sqlite3.IntegrityError):
        _insert_revocation(ledger, revoked_at_us=-1)
    with pytest.raises(sqlite3.IntegrityError):
        _insert_revocation(ledger, revoked_by_subject="s" * 257)

    _insert_revocation(ledger, revoked_at_us=ISSUED_AT_US - 1)
    with pytest.raises(sqlite3.IntegrityError):
        _insert_revocation(
            ledger,
            revoked_at_us=DECIDED_AT_US + 1,
            reason_code="SECURITY_RESPONSE",
        )
    assert ledger.execute(
        "SELECT COUNT(*) FROM invitation_revocations"
    ).fetchone()[0] == 1


@pytest.mark.parametrize(
    "changes",
    (
        {"confirmation_id": "cnf_" + "E" * 64},
        {"contract_version": "v2"},
        {"confirmation_fingerprint": OTHER_FINGERPRINT},
        {"reviewer_issuer": " "},
        {"reviewer_organization_id": "wrong-org"},
        {"reviewer_display_name_snapshot": ""},
        {"decision": "APPROVE"},
        {"agreement_acknowledged": 2},
        {"agreement_acknowledged": 0},
        {
            "decision": "REQUEST_CHANGES",
            "agreement_acknowledged": 0,
            "rationale": " ",
        },
        {"rationale": "r" * 2001},
        {"decided_at_us": -1},
        {"decided_at_us": EXPIRES_AT_US},
        {"request_digest": "D" * 64},
    ),
)
def test_decision_rejects_invalid_binding_identity_payload_and_time(
    ledger: Any,
    changes: dict[str, object],
) -> None:
    _insert_invitation(ledger)
    with pytest.raises(sqlite3.IntegrityError):
        _insert_decision(ledger, **changes)
    assert ledger.execute(
        "SELECT COUNT(*) FROM confirmation_decisions"
    ).fetchone()[0] == 0


def test_revoked_invitation_cannot_receive_a_decision(ledger: Any) -> None:
    _insert_invitation(ledger)
    _insert_revocation(ledger)

    with pytest.raises(
        sqlite3.IntegrityError,
        match="invitation is not active",
    ):
        _insert_decision(ledger)


def test_one_decision_per_invitation_and_contract_version(
    ledger: Any,
) -> None:
    _insert_invitation(ledger)
    _insert_invitation(
        ledger,
        invitation_id="review-second",
        token_digest="4" * 64,
    )
    _insert_decision(ledger)

    with pytest.raises(sqlite3.IntegrityError):
        _insert_decision(
            ledger,
            confirmation_id="cnf_" + "5" * 64,
        )
    with pytest.raises(sqlite3.IntegrityError):
        _insert_decision(
            ledger,
            confirmation_id="cnf_" + "6" * 64,
            invitation_id="review-second",
        )

    _insert_invitation(
        ledger,
        invitation_id="review-v2",
        contract_version="v2",
        token_digest="7" * 64,
    )
    _insert_decision(
        ledger,
        confirmation_id="cnf_" + "8" * 64,
        invitation_id="review-v2",
        contract_version="v2",
        request_digest="9" * 64,
    )
    assert ledger.execute(
        "SELECT COUNT(*) FROM confirmation_decisions"
    ).fetchone()[0] == 2


@pytest.mark.parametrize(
    "changes",
    (
        {"operation_digest": "A" * 64},
        {"contract_id": "wrong-contract"},
        {"contract_version": "v2"},
        {"idempotency_key_digest": "bad"},
        {"request_digest": "f" * 64},
        {"confirmation_id": "cnf_" + "0" * 64},
        {"created_at_us": -1},
    ),
)
def test_idempotency_operation_requires_exact_decision_linkage(
    ledger: Any,
    changes: dict[str, object],
) -> None:
    _insert_invitation(ledger)
    _insert_decision(ledger)
    with pytest.raises(sqlite3.IntegrityError):
        _insert_operation(ledger, **changes)
    assert ledger.execute(
        "SELECT COUNT(*) FROM idempotency_operations"
    ).fetchone()[0] == 0


def test_idempotency_receipt_is_unique_by_operation_key_and_decision(
    ledger: Any,
) -> None:
    _insert_invitation(ledger)
    _insert_decision(ledger)
    _insert_operation(ledger, created_at_us=0)

    for changes in (
        {"operation_digest": "3" * 64},
        {
            "operation_digest": "4" * 64,
            "idempotency_key_digest": "5" * 64,
        },
    ):
        with pytest.raises(sqlite3.IntegrityError):
            _insert_operation(ledger, **changes)
    assert ledger.execute(
        "SELECT COUNT(*) FROM idempotency_operations"
    ).fetchone()[0] == 1


@pytest.mark.parametrize(
    "changes",
    (
        {"event_id": "audit-Bad"},
        {"event_sequence": 0},
        {"event_type": "INVITATION_ISSUED", "outcome": "REJECTED"},
        {"event_type": "INVITATION_ISSUED", "invitation_id": None},
        {"event_type": "INVITATION_ISSUED", "reason_code": "MANUAL"},
        {
            "event_type": "INVITATION_REVOKED",
            "reason_code": "UNBOUNDED_REASON",
        },
        {
            "event_type": "INVITATION_REISSUED",
            "reason_code": "MANUAL",
        },
        {
            "event_type": "DECISION_RECORDED",
            "confirmation_id": None,
        },
        {
            "event_type": "DECISION_REPLAYED",
            "confirmation_id": CONFIRMATION_ID,
            "outcome": "SUCCEEDED",
        },
        {
            "event_type": "DECISION_REJECTED",
            "confirmation_id": CONFIRMATION_ID,
            "outcome": "REJECTED",
            "reason_code": "CONFLICT",
        },
        {
            "event_type": "CONTRACT_SUPERSEDED",
            "invitation_id": "review-first",
            "reason_code": "CONTRACT_SUPERSEDED",
        },
        {"actor_issuer": None, "actor_subject": "employee-1"},
        {
            "actor_issuer": None,
            "actor_subject": None,
            "actor_organization_id": "org-1",
        },
        {"actor_subject": "s" * 257},
        {"reason_code": "not_machine"},
        {"trace_id": "A" * 32},
        {"metadata_schema_version": "2"},
        {"metadata_adapter_name": "custom"},
        {"metadata_adapter_version": "1.23456"},
        {"metadata_adapter_version": "1..2"},
        {"confirmation_fingerprint": OTHER_FINGERPRINT},
    ),
)
def test_audit_rejects_invalid_event_actor_id_and_binding_combinations(
    ledger: Any,
    changes: dict[str, object],
) -> None:
    _insert_invitation(ledger)
    _insert_decision(ledger)
    with pytest.raises(sqlite3.IntegrityError):
        _insert_audit_event(ledger, **changes)
    assert ledger.execute(
        "SELECT COUNT(*) FROM confirmation_audit_events"
    ).fetchone()[0] == 0


@pytest.mark.parametrize(
    ("event_type", "invitation_id"),
    (
        ("INVITATION_REVOKED", "review-first"),
        ("INVITATION_REISSUED", "review-first"),
        ("CONTRACT_SUPERSEDED", None),
    ),
)
def test_audit_events_with_required_reason_reject_null(
    ledger: Any,
    event_type: str,
    invitation_id: str | None,
) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        _insert_audit_event(
            ledger,
            event_type=event_type,
            invitation_id=invitation_id,
            outcome="SUCCEEDED",
            reason_code=None,
        )
    assert ledger.execute(
        "SELECT COUNT(*) FROM confirmation_audit_events"
    ).fetchone()[0] == 0


def test_audit_event_matrix_rejects_every_unlisted_cross_product(
    ledger: Any,
) -> None:
    event_types = (
        "INVITATION_ISSUED",
        "INVITATION_REVOKED",
        "INVITATION_REISSUED",
        "INVITATION_REJECTED",
        "DECISION_RECORDED",
        "DECISION_REPLAYED",
        "DECISION_REJECTED",
        "CONTRACT_SUPERSEDED",
    )
    outcomes = ("SUCCEEDED", "REPLAYED", "REJECTED")
    reference_shapes = {
        "invitation": ("review-first", None),
        "decision": ("review-first", CONFIRMATION_ID),
        "none": (None, None),
        "confirmation_only": (None, CONFIRMATION_ID),
    }
    reasons = (
        None,
        "MANUAL",
        "REISSUED",
        "CONTRACT_SUPERSEDED",
        "SECURITY_RESPONSE",
        "OTHER_REASON",
    )
    non_null_reasons = reasons[1:]
    revocation_reasons = reasons[1:5]
    allowed = {
        ("INVITATION_ISSUED", "SUCCEEDED", "invitation", None),
        ("INVITATION_REISSUED", "SUCCEEDED", "invitation", "REISSUED"),
        ("DECISION_RECORDED", "SUCCEEDED", "decision", None),
        ("DECISION_REPLAYED", "REPLAYED", "decision", None),
        (
            "CONTRACT_SUPERSEDED",
            "SUCCEEDED",
            "none",
            "CONTRACT_SUPERSEDED",
        ),
    }
    allowed.update(
        (
            "INVITATION_REVOKED",
            "SUCCEEDED",
            "invitation",
            reason,
        )
        for reason in revocation_reasons
    )
    allowed.update(
        (
            "INVITATION_REJECTED",
            "REJECTED",
            "invitation",
            reason,
        )
        for reason in non_null_reasons
    )
    allowed.update(
        (
            "DECISION_REJECTED",
            "REJECTED",
            "invitation",
            reason,
        )
        for reason in non_null_reasons
    )

    accepted = 0
    combinations = product(
        event_types,
        outcomes,
        reference_shapes,
        reasons,
    )
    for sequence, (
        event_type,
        outcome,
        reference_shape,
        reason,
    ) in enumerate(combinations, start=1):
        invitation_id, confirmation_id = reference_shapes[reference_shape]
        changes = {
            "event_id": "audit-cross-{0}".format(sequence),
            "event_sequence": sequence,
            "event_type": event_type,
            "outcome": outcome,
            "invitation_id": invitation_id,
            "confirmation_id": confirmation_id,
            "reason_code": reason,
        }
        if (
            event_type,
            outcome,
            reference_shape,
            reason,
        ) in allowed:
            _insert_audit_event(ledger, **changes)
            accepted += 1
        else:
            with pytest.raises(sqlite3.IntegrityError):
                _insert_audit_event(ledger, **changes)

    assert accepted == len(allowed)
    assert ledger.execute(
        "SELECT COUNT(*) FROM confirmation_audit_events"
    ).fetchone()[0] == len(allowed)


@pytest.mark.parametrize(
    ("adapter_name", "adapter_version"),
    (
        (None, None),
        ("memory", "1"),
        ("sqlite", "12.3456"),
        ("postgresql", "1234.5678.9012"),
    ),
)
def test_audit_metadata_is_separate_and_allowlisted(
    ledger: Any,
    adapter_name: str | None,
    adapter_version: str | None,
) -> None:
    _insert_audit_event(
        ledger,
        event_type="INVITATION_REJECTED",
        invitation_id="review-unknown",
        outcome="REJECTED",
        reason_code="NOT_FOUND",
        metadata_adapter_name=adapter_name,
        metadata_adapter_version=adapter_version,
    )

    row = ledger.execute(
        """
        SELECT
            metadata_schema_version,
            metadata_adapter_name,
            metadata_adapter_version
        FROM confirmation_audit_events
        """
    ).fetchone()
    assert tuple(row) == ("1", adapter_name, adapter_version)


def test_audit_accepts_only_the_closed_event_outcome_reason_matrix(
    ledger: Any,
) -> None:
    _insert_invitation(ledger)
    _insert_decision(ledger)
    rows = (
        ("INVITATION_ISSUED", "SUCCEEDED", "review-first", None, None),
        (
            "INVITATION_REVOKED",
            "SUCCEEDED",
            "review-first",
            None,
            "MANUAL",
        ),
        (
            "INVITATION_REISSUED",
            "SUCCEEDED",
            "review-first",
            None,
            "REISSUED",
        ),
        (
            "INVITATION_REJECTED",
            "REJECTED",
            "review-first",
            None,
            "EXPIRED",
        ),
        (
            "DECISION_RECORDED",
            "SUCCEEDED",
            "review-first",
            CONFIRMATION_ID,
            None,
        ),
        (
            "DECISION_REPLAYED",
            "REPLAYED",
            "review-first",
            CONFIRMATION_ID,
            None,
        ),
        (
            "DECISION_REJECTED",
            "REJECTED",
            "review-first",
            None,
            "CONFLICT",
        ),
        (
            "CONTRACT_SUPERSEDED",
            "SUCCEEDED",
            None,
            None,
            "CONTRACT_SUPERSEDED",
        ),
    )
    for sequence, (
        event_type,
        outcome,
        invitation_id,
        confirmation_id,
        reason_code,
    ) in enumerate(rows, start=1):
        _insert_audit_event(
            ledger,
            event_id="audit-event-{0}".format(sequence),
            event_sequence=sequence,
            event_type=event_type,
            occurred_at_us=DECIDED_AT_US,
            invitation_id=invitation_id,
            confirmation_id=confirmation_id,
            outcome=outcome,
            reason_code=reason_code,
        )
    assert ledger.execute(
        "SELECT COUNT(*) FROM confirmation_audit_events"
    ).fetchone()[0] == len(rows)


def test_audit_sequence_is_global_positive_and_unique_without_extra_ordering(
    ledger: Any,
) -> None:
    _insert_invitation(ledger)
    _insert_audit_event(
        ledger,
        event_sequence=10,
        occurred_at_us=ISSUED_AT_US,
    )

    for changes in (
        {
            "event_id": "audit-sequence-duplicate",
            "event_sequence": 10,
            "occurred_at_us": ISSUED_AT_US + 1,
        },
        {
            "event_id": "audit-negative-time",
            "event_sequence": 11,
            "occurred_at_us": -1,
        },
    ):
        with pytest.raises(sqlite3.IntegrityError):
            _insert_audit_event(ledger, **changes)

    _insert_audit_event(
        ledger,
        event_id="audit-earlier-sequence-and-time",
        event_sequence=1,
        occurred_at_us=0,
    )
    assert [
        row[0]
        for row in ledger.execute(
            """
            SELECT event_sequence
            FROM confirmation_audit_events
            ORDER BY event_sequence
            """
        ).fetchall()
    ] == [1, 10]


def _seed_every_domain_table(connection: Any) -> None:
    _insert_invitation(connection)
    _insert_decision(connection)
    _insert_operation(connection)
    _insert_audit_event(
        connection,
        event_type="DECISION_RECORDED",
        occurred_at_us=DECIDED_AT_US,
        confirmation_id=CONFIRMATION_ID,
    )
    _insert_invitation(
        connection,
        invitation_id="review-revoked",
        token_digest="4" * 64,
    )
    _insert_revocation(
        connection,
        invitation_id="review-revoked",
    )


@pytest.mark.parametrize(
    "statement",
    (
        """
        INSERT OR REPLACE INTO review_invitations
        SELECT *
        FROM review_invitations
        WHERE invitation_id = 'review-first'
        """,
        """
        INSERT OR REPLACE INTO invitation_revocations
        SELECT *
        FROM invitation_revocations
        WHERE invitation_id = 'review-revoked'
        """,
        """
        INSERT OR REPLACE INTO confirmation_decisions
        SELECT *
        FROM confirmation_decisions
        WHERE confirmation_id = 'cnf_eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee'
        """,
        """
        INSERT OR REPLACE INTO idempotency_operations
        SELECT *
        FROM idempotency_operations
        WHERE operation_digest = '1111111111111111111111111111111111111111111111111111111111111111'
        """,
        """
        INSERT OR REPLACE INTO confirmation_audit_events
        SELECT *
        FROM confirmation_audit_events
        WHERE event_id = 'audit-first'
        """,
        """
        INSERT OR REPLACE INTO review_invitations
        SELECT
            'review-forged',
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
        WHERE invitation_id = 'review-first'
        """,
        """
        INSERT OR REPLACE INTO confirmation_decisions
        SELECT
            'cnf_ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff',
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
        FROM confirmation_decisions
        WHERE confirmation_id = 'cnf_eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee'
        """,
        """
        INSERT OR REPLACE INTO idempotency_operations
        SELECT
            '3333333333333333333333333333333333333333333333333333333333333333',
            contract_id,
            contract_version,
            idempotency_key_digest,
            request_digest,
            confirmation_id,
            created_at_us
        FROM idempotency_operations
        WHERE operation_digest = '1111111111111111111111111111111111111111111111111111111111111111'
        """,
        """
        INSERT OR REPLACE INTO confirmation_audit_events
        SELECT
            'audit-forged',
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
        WHERE event_id = 'audit-first'
        """,
    ),
)
def test_conflict_guards_block_replace_for_primary_and_alternate_keys(
    tmp_path: Path,
    statement: str,
) -> None:
    database_path = tmp_path / "confirmation.db"
    guarded = open_confirmation_database(database_path)
    apply_migrations(
        guarded,
        CONFIRMATION_LEDGER_MIGRATIONS,
        now=MIGRATED_AT,
    )
    _seed_every_domain_table(guarded)
    guarded.close()

    raw = sqlite3.connect(database_path, isolation_level=None)
    try:
        raw.execute("PRAGMA recursive_triggers = OFF")
        with pytest.raises(
            sqlite3.IntegrityError,
            match="confirmation ledger is append-only",
        ):
            raw.execute(statement)
    finally:
        raw.close()


@pytest.mark.parametrize(
    "statement",
    (
        """
        INSERT INTO review_invitations
        SELECT *
        FROM review_invitations
        WHERE invitation_id = 'review-first'
        ON CONFLICT(invitation_id) DO UPDATE
        SET issued_by_subject = excluded.issued_by_subject
        """,
        """
        INSERT INTO invitation_revocations
        SELECT *
        FROM invitation_revocations
        WHERE invitation_id = 'review-revoked'
        ON CONFLICT(invitation_id) DO UPDATE
        SET reason_code = excluded.reason_code
        """,
        """
        INSERT INTO confirmation_decisions
        SELECT *
        FROM confirmation_decisions
        WHERE confirmation_id = 'cnf_eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee'
        ON CONFLICT(confirmation_id) DO UPDATE
        SET rationale = excluded.rationale
        """,
        """
        INSERT INTO idempotency_operations
        SELECT *
        FROM idempotency_operations
        WHERE operation_digest = '1111111111111111111111111111111111111111111111111111111111111111'
        ON CONFLICT(operation_digest) DO UPDATE
        SET created_at_us = excluded.created_at_us
        """,
        """
        INSERT INTO confirmation_audit_events
        SELECT *
        FROM confirmation_audit_events
        WHERE event_id = 'audit-first'
        ON CONFLICT(event_id) DO UPDATE
        SET metadata_adapter_name = excluded.metadata_adapter_name
        """,
    ),
)
def test_conflict_guards_block_upsert_even_with_recursive_triggers_off(
    tmp_path: Path,
    statement: str,
) -> None:
    database_path = tmp_path / "confirmation.db"
    guarded = open_confirmation_database(database_path)
    apply_migrations(
        guarded,
        CONFIRMATION_LEDGER_MIGRATIONS,
        now=MIGRATED_AT,
    )
    _seed_every_domain_table(guarded)
    guarded.close()

    raw = sqlite3.connect(database_path, isolation_level=None)
    try:
        raw.execute("PRAGMA recursive_triggers = OFF")
        with pytest.raises(
            sqlite3.IntegrityError,
            match="confirmation ledger is append-only",
        ):
            raw.execute(statement)
    finally:
        raw.close()


@pytest.mark.parametrize(
    "statement",
    (
        """
        UPDATE review_invitations
        SET issued_by_subject = 'changed'
        WHERE invitation_id = 'review-first'
        """,
        "DELETE FROM review_invitations WHERE invitation_id = 'review-first'",
        """
        UPDATE invitation_revocations
        SET reason_code = 'SECURITY_RESPONSE'
        WHERE invitation_id = 'review-revoked'
        """,
        """
        DELETE FROM invitation_revocations
        WHERE invitation_id = 'review-revoked'
        """,
        """
        UPDATE confirmation_decisions
        SET rationale = 'changed'
        WHERE confirmation_id = 'cnf_eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee'
        """,
        """
        DELETE FROM confirmation_decisions
        WHERE confirmation_id = 'cnf_eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee'
        """,
        """
        UPDATE idempotency_operations
        SET created_at_us = 1600000
        WHERE operation_digest = '1111111111111111111111111111111111111111111111111111111111111111'
        """,
        """
        DELETE FROM idempotency_operations
        WHERE operation_digest = '1111111111111111111111111111111111111111111111111111111111111111'
        """,
        """
        UPDATE confirmation_audit_events
        SET metadata_adapter_name = 'sqlite'
        WHERE event_id = 'audit-first'
        """,
        "DELETE FROM confirmation_audit_events WHERE event_id = 'audit-first'",
    ),
)
def test_on_disk_triggers_reject_updates_and_deletes(
    tmp_path: Path,
    statement: str,
) -> None:
    database_path = tmp_path / "confirmation.db"
    guarded = open_confirmation_database(database_path)
    apply_migrations(
        guarded,
        CONFIRMATION_LEDGER_MIGRATIONS,
        now=MIGRATED_AT,
    )
    _seed_every_domain_table(guarded)
    guarded.close()

    raw = sqlite3.connect(database_path, isolation_level=None)
    try:
        raw.execute("PRAGMA foreign_keys = ON")
        with pytest.raises(
            sqlite3.IntegrityError,
            match="confirmation ledger is append-only",
        ):
            raw.execute(statement)
    finally:
        raw.close()


def test_normal_facade_cannot_change_domain_schema_or_triggers(
    ledger: Any,
) -> None:
    original_objects = {
        object_type: _object_names(ledger, object_type)
        for object_type in ("table", "index", "trigger")
    }
    forbidden_statements = [
        *("DROP TABLE {0}".format(name) for name in DOMAIN_TABLES),
        *(
            "ALTER TABLE {0} ADD COLUMN forged TEXT".format(name)
            for name in DOMAIN_TABLES
        ),
        *("DROP INDEX {0}".format(name) for name in DOMAIN_INDEXES),
        *("DROP TRIGGER {0}".format(name) for name in DOMAIN_TRIGGERS),
        *(
            "CREATE INDEX forged_{0} ON {0} (rowid)".format(name)
            for name in DOMAIN_TABLES
        ),
    ]

    for statement in forbidden_statements:
        with pytest.raises(sqlite3.DatabaseError):
            ledger.execute(statement)
        assert not ledger.in_transaction

    assert {
        object_type: _object_names(ledger, object_type)
        for object_type in ("table", "index", "trigger")
    } == original_objects


def test_temp_scratch_cannot_be_renamed_to_protected_tables(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_path = tmp_path / "confirmation.db"
    prepared = open_confirmation_database(database_path)
    apply_migrations(
        prepared,
        CONFIRMATION_LEDGER_MIGRATIONS,
        now=MIGRATED_AT,
    )
    _insert_invitation(prepared)
    prepared.close()

    original_connect = confirmation_sqlite_module.sqlite3.connect

    def connect_with_temp_scratch(
        *args: object,
        **kwargs: object,
    ) -> sqlite3.Connection:
        raw_connection = original_connect(*args, **kwargs)
        raw_connection.execute(
            """
            CREATE TEMP TABLE domain_scratch AS
            SELECT * FROM main.review_invitations
            """
        )
        raw_connection.execute(
            """
            CREATE TEMP TABLE history_scratch AS
            SELECT * FROM main.schema_migrations
            """
        )
        return raw_connection

    monkeypatch.setattr(
        confirmation_sqlite_module.sqlite3,
        "connect",
        connect_with_temp_scratch,
    )
    connection = open_confirmation_database(database_path)
    try:
        validate_confirmation_schema(connection)
        assert read_applied_migrations(connection)[0].name == (
            "confirmation_ledger"
        )

        connection.execute("DELETE FROM temp.domain_scratch")
        connection.execute(
            """
            INSERT INTO temp.domain_scratch
            SELECT * FROM main.review_invitations
            """
        )
        assert connection.execute(
            "SELECT COUNT(*) FROM temp.domain_scratch"
        ).fetchone()[0] == 1
        connection.execute(
            """
            UPDATE temp.history_scratch
            SET name = 'scratch_only'
            """
        )
        assert connection.execute(
            "SELECT name FROM temp.history_scratch"
        ).fetchone()[0] == "scratch_only"

        for scratch_name, protected_name in (
            ("domain_scratch", "review_invitations"),
            ("history_scratch", "schema_migrations"),
        ):
            with pytest.raises(
                sqlite3.DatabaseError,
                match="not authorized",
            ):
                connection.execute(
                    "ALTER TABLE temp.{0} RENAME TO {1}".format(
                        scratch_name,
                        protected_name,
                    )
                )
            with pytest.raises(
                sqlite3.DatabaseError,
                match="not authorized",
            ):
                connection.execute(
                    "DROP TABLE temp.{0}".format(scratch_name)
                )

        for statement in (
            """
            CREATE TEMP TABLE review_invitations AS
            SELECT * FROM main.review_invitations
            """,
            """
            CREATE TEMP TABLE schema_migrations AS
            SELECT * FROM main.schema_migrations
            """,
        ):
            with pytest.raises(
                sqlite3.DatabaseError,
                match="not authorized",
            ):
                connection.execute(statement)

        assert connection.execute(
            "SELECT contract_id FROM review_invitations"
        ).fetchone()[0] == connection.execute(
            "SELECT contract_id FROM main.review_invitations"
        ).fetchone()[0]
        assert connection.execute(
            "SELECT name FROM schema_migrations"
        ).fetchone()[0] == connection.execute(
            "SELECT name FROM main.schema_migrations"
        ).fetchone()[0]
    finally:
        connection.close()


def test_preexisting_temp_shadows_cannot_redirect_authority_or_reads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_path = tmp_path / "confirmation.db"
    prepared = open_confirmation_database(database_path)
    apply_migrations(
        prepared,
        CONFIRMATION_LEDGER_MIGRATIONS,
        now=MIGRATED_AT,
    )
    _insert_invitation(prepared)
    prepared.close()

    original_connect = confirmation_sqlite_module.sqlite3.connect

    def connect_with_temp_shadows(
        *args: object,
        **kwargs: object,
    ) -> sqlite3.Connection:
        raw_connection = original_connect(*args, **kwargs)
        raw_connection.execute(
            """
            CREATE TEMP TABLE review_invitations (
                contract_id TEXT
            )
            """
        )
        raw_connection.execute(
            """
            INSERT INTO temp.review_invitations (contract_id)
            VALUES ('forged-contract')
            """
        )
        raw_connection.execute(
            "CREATE TEMP TABLE schema_migrations (name TEXT)"
        )
        raw_connection.execute(
            """
            INSERT INTO temp.schema_migrations (name)
            VALUES ('forged_history')
            """
        )
        return raw_connection

    monkeypatch.setattr(
        confirmation_sqlite_module.sqlite3,
        "connect",
        connect_with_temp_shadows,
    )
    connection = open_confirmation_database(database_path)
    try:
        validate_confirmation_schema(connection)
        assert read_applied_migrations(connection)[0].name == (
            "confirmation_ledger"
        )
        assert connection.execute(
            "SELECT contract_id FROM main.review_invitations"
        ).fetchone()[0] == "contract-1"
        assert connection.execute(
            "SELECT name FROM main.schema_migrations"
        ).fetchone()[0] == "confirmation_ledger"

        for statement in (
            "SELECT contract_id FROM review_invitations",
            "SELECT contract_id FROM temp.review_invitations",
            "SELECT name FROM schema_migrations",
            "SELECT name FROM temp.schema_migrations",
        ):
            with pytest.raises(sqlite3.DatabaseError):
                connection.execute(statement)
    finally:
        connection.close()


def test_normal_facade_denies_virtual_tables_using_any_protected_name(
    tmp_path: Path,
) -> None:
    connection = open_confirmation_database(tmp_path / "confirmation.db")
    protected_names = DOMAIN_TABLES | DOMAIN_INDEXES | DOMAIN_TRIGGERS
    try:
        for name in sorted(protected_names):
            with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
                connection.execute(
                    "CREATE VIRTUAL TABLE {0} USING fts5(content)".format(name)
                )
            assert not connection.in_transaction
        assert not (
            protected_names
            & {
                row["name"]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master"
                ).fetchall()
            }
        )
    finally:
        connection.close()


def test_pre_migration_virtual_table_name_occupation_is_fail_closed(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "confirmation.db"
    raw = sqlite3.connect(database_path, isolation_level=None)
    raw.execute(
        "CREATE VIRTUAL TABLE review_invitations USING fts5(content)"
    )
    raw.close()

    guarded = open_confirmation_database(database_path)
    try:
        with pytest.raises(sqlite3.DatabaseError):
            guarded.execute("DROP TABLE review_invitations")
        with pytest.raises(
            MigrationFailed,
            match="^Database migration failed\\.$",
        ):
            apply_migrations(
                guarded,
                CONFIRMATION_LEDGER_MIGRATIONS,
                now=MIGRATED_AT,
            )
        assert read_applied_migrations(guarded) == ()
        virtual_object = guarded.execute(
            """
            SELECT type, sql
            FROM main.sqlite_master
            WHERE name = 'review_invitations'
            """
        ).fetchone()
        assert virtual_object["type"] == "table"
        assert virtual_object["sql"].startswith(
            "CREATE VIRTUAL TABLE review_invitations"
        )
        assert DOMAIN_TABLES & _object_names(guarded, "table") == {
            "review_invitations"
        }
    finally:
        guarded.close()


def test_post_migration_virtual_replacement_keeps_protected_name_guarded(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "confirmation.db"
    guarded = open_confirmation_database(database_path)
    apply_migrations(
        guarded,
        CONFIRMATION_LEDGER_MIGRATIONS,
        now=MIGRATED_AT,
    )
    expected_history = read_applied_migrations(guarded)
    guarded.close()

    raw = sqlite3.connect(database_path, isolation_level=None)
    raw.execute("DROP INDEX review_invitations_expiry_idx")
    raw.execute(
        """
        CREATE VIRTUAL TABLE review_invitations_expiry_idx
        USING fts5(content)
        """
    )
    raw.close()

    reopened = open_confirmation_database(database_path)
    try:
        with pytest.raises(sqlite3.DatabaseError):
            reopened.execute("DROP TABLE review_invitations_expiry_idx")
        with pytest.raises(LedgerUnavailable):
            validate_confirmation_schema(reopened)
        assert read_applied_migrations(reopened) == expected_history
        virtual_object = reopened.execute(
            """
            SELECT type, sql
            FROM main.sqlite_master
            WHERE name = 'review_invitations_expiry_idx'
            """
        ).fetchone()
        assert virtual_object["type"] == "table"
        assert virtual_object["sql"].startswith(
            "CREATE VIRTUAL TABLE review_invitations_expiry_idx"
        )
        assert DOMAIN_TABLES <= _object_names(reopened, "table")
    finally:
        reopened.close()


def test_migration_cannot_create_temp_virtual_table(tmp_path: Path) -> None:
    connection = open_confirmation_database(tmp_path / "confirmation.db")
    migration = Migration(
        version=1,
        name="temp_virtual",
        sql=(
            "CREATE VIRTUAL TABLE temp.ephemeral "
            "USING fts5(content);"
        ),
    )
    try:
        with pytest.raises(
            MigrationFailed,
            match="^Database migration failed\\.$",
        ):
            apply_migrations(
                connection,
                (migration,),
                now=MIGRATED_AT,
            )
        assert connection.execute(
            """
            SELECT name
            FROM sqlite_temp_master
            WHERE name = 'ephemeral'
            """
        ).fetchall() == []
        assert read_applied_migrations(connection) == ()
    finally:
        connection.close()
