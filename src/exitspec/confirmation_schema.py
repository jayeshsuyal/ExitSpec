"""Frozen SQLite schema for the durable confirmation ledger."""

from __future__ import annotations

import re
import sqlite3

from .confirmation_sqlite import (
    ConfirmationSQLiteError,
    LedgerUnavailable,
    Migration,
    read_applied_migrations,
)


CONFIRMATION_LEDGER_SQL = """
CREATE TABLE review_invitations (
    invitation_id TEXT NOT NULL PRIMARY KEY CHECK (
        length(invitation_id) BETWEEN 8 AND 64
        AND substr(invitation_id, 1, 7) = 'review-'
        AND substr(invitation_id, 8) NOT GLOB '*[^a-z0-9-]*'
        AND substr(invitation_id, 8, 1) GLOB '[a-z0-9]'
        AND substr(invitation_id, -1, 1) GLOB '[a-z0-9]'
        AND invitation_id NOT GLOB '*--*'
    ),
    contract_id TEXT NOT NULL CHECK (
        length(trim(contract_id)) > 0
    ),
    contract_version TEXT NOT NULL CHECK (
        length(trim(contract_version)) > 0
    ),
    confirmation_fingerprint TEXT NOT NULL CHECK (
        length(confirmation_fingerprint) = 64
        AND confirmation_fingerprint NOT GLOB '*[^a-f0-9]*'
    ),
    token_digest TEXT NOT NULL UNIQUE CHECK (
        length(token_digest) = 64
        AND token_digest NOT GLOB '*[^a-f0-9]*'
    ),
    token_digest_version TEXT NOT NULL CHECK (
        length(trim(token_digest_version)) > 0
    ),
    intended_organization_id TEXT NOT NULL CHECK (
        length(trim(intended_organization_id)) > 0
    ),
    issued_by_subject TEXT NOT NULL CHECK (
        length(trim(issued_by_subject)) > 0
    ),
    issued_at_us INTEGER NOT NULL CHECK (
        typeof(issued_at_us) = 'integer'
        AND issued_at_us >= 0
    ),
    expires_at_us INTEGER NOT NULL CHECK (
        typeof(expires_at_us) = 'integer'
        AND expires_at_us > issued_at_us
    ),
    UNIQUE (
        invitation_id,
        contract_id,
        contract_version,
        confirmation_fingerprint,
        intended_organization_id
    )
) STRICT;

CREATE INDEX review_invitations_binding_idx
ON review_invitations (
    contract_id,
    contract_version,
    confirmation_fingerprint
);

CREATE INDEX review_invitations_expiry_idx
ON review_invitations (expires_at_us);

CREATE TABLE invitation_revocations (
    invitation_id TEXT NOT NULL PRIMARY KEY,
    revoked_at_us INTEGER NOT NULL CHECK (
        typeof(revoked_at_us) = 'integer'
        AND revoked_at_us >= 0
    ),
    revoked_by_subject TEXT NOT NULL CHECK (
        length(revoked_by_subject) BETWEEN 1 AND 256
        AND length(trim(revoked_by_subject)) > 0
    ),
    reason_code TEXT NOT NULL CHECK (
        reason_code IN (
            'MANUAL',
            'REISSUED',
            'CONTRACT_SUPERSEDED',
            'SECURITY_RESPONSE'
        )
    ),
    FOREIGN KEY (invitation_id)
        REFERENCES review_invitations (invitation_id)
        ON UPDATE RESTRICT
        ON DELETE RESTRICT
) STRICT;

CREATE TABLE confirmation_decisions (
    confirmation_id TEXT NOT NULL PRIMARY KEY CHECK (
        length(confirmation_id) = 68
        AND substr(confirmation_id, 1, 4) = 'cnf_'
        AND substr(confirmation_id, 5) NOT GLOB '*[^a-f0-9]*'
    ),
    invitation_id TEXT NOT NULL UNIQUE,
    contract_id TEXT NOT NULL CHECK (
        length(trim(contract_id)) > 0
    ),
    contract_version TEXT NOT NULL CHECK (
        length(trim(contract_version)) > 0
    ),
    confirmation_fingerprint TEXT NOT NULL CHECK (
        length(confirmation_fingerprint) = 64
        AND confirmation_fingerprint NOT GLOB '*[^a-f0-9]*'
    ),
    reviewer_issuer TEXT NOT NULL CHECK (
        length(trim(reviewer_issuer)) > 0
    ),
    reviewer_subject TEXT NOT NULL CHECK (
        length(trim(reviewer_subject)) > 0
    ),
    reviewer_organization_id TEXT NOT NULL CHECK (
        length(trim(reviewer_organization_id)) > 0
    ),
    reviewer_display_name_snapshot TEXT NOT NULL CHECK (
        length(trim(reviewer_display_name_snapshot)) > 0
    ),
    decision TEXT NOT NULL CHECK (
        decision IN ('CONFIRM', 'REQUEST_CHANGES')
    ),
    agreement_acknowledged INTEGER NOT NULL CHECK (
        typeof(agreement_acknowledged) = 'integer'
        AND agreement_acknowledged IN (0, 1)
    ),
    rationale TEXT NOT NULL CHECK (
        length(rationale) <= 2000
    ),
    decided_at_us INTEGER NOT NULL CHECK (
        typeof(decided_at_us) = 'integer'
        AND decided_at_us >= 0
    ),
    request_digest TEXT NOT NULL CHECK (
        length(request_digest) = 64
        AND request_digest NOT GLOB '*[^a-f0-9]*'
    ),
    CHECK (
        (
            decision = 'CONFIRM'
            AND agreement_acknowledged = 1
        )
        OR (
            decision = 'REQUEST_CHANGES'
            AND length(trim(rationale)) BETWEEN 1 AND 2000
        )
    ),
    UNIQUE (contract_id, contract_version),
    UNIQUE (
        confirmation_id,
        contract_id,
        contract_version,
        request_digest
    ),
    FOREIGN KEY (
        invitation_id,
        contract_id,
        contract_version,
        confirmation_fingerprint,
        reviewer_organization_id
    )
        REFERENCES review_invitations (
            invitation_id,
            contract_id,
            contract_version,
            confirmation_fingerprint,
            intended_organization_id
        )
        ON UPDATE RESTRICT
        ON DELETE RESTRICT
) STRICT;

CREATE TRIGGER confirmation_decisions_active_invitation
BEFORE INSERT ON confirmation_decisions
FOR EACH ROW
WHEN EXISTS (
    SELECT 1
    FROM review_invitations
    WHERE invitation_id = NEW.invitation_id
      AND (
          NEW.decided_at_us >= expires_at_us
          OR EXISTS (
              SELECT 1
              FROM invitation_revocations
              WHERE invitation_id = NEW.invitation_id
          )
      )
)
BEGIN
    SELECT RAISE(ABORT, 'invitation is not active');
END;

CREATE TABLE idempotency_operations (
    operation_digest TEXT NOT NULL PRIMARY KEY CHECK (
        length(operation_digest) = 64
        AND operation_digest NOT GLOB '*[^a-f0-9]*'
    ),
    contract_id TEXT NOT NULL CHECK (
        length(trim(contract_id)) > 0
    ),
    contract_version TEXT NOT NULL CHECK (
        length(trim(contract_version)) > 0
    ),
    idempotency_key_digest TEXT NOT NULL CHECK (
        length(idempotency_key_digest) = 64
        AND idempotency_key_digest NOT GLOB '*[^a-f0-9]*'
    ),
    request_digest TEXT NOT NULL CHECK (
        length(request_digest) = 64
        AND request_digest NOT GLOB '*[^a-f0-9]*'
    ),
    confirmation_id TEXT NOT NULL UNIQUE,
    created_at_us INTEGER NOT NULL CHECK (
        typeof(created_at_us) = 'integer'
        AND created_at_us >= 0
    ),
    UNIQUE (
        contract_id,
        contract_version,
        idempotency_key_digest
    ),
    FOREIGN KEY (
        confirmation_id,
        contract_id,
        contract_version,
        request_digest
    )
        REFERENCES confirmation_decisions (
            confirmation_id,
            contract_id,
            contract_version,
            request_digest
        )
        ON UPDATE RESTRICT
        ON DELETE RESTRICT
) STRICT;

CREATE TABLE confirmation_audit_events (
    event_id TEXT NOT NULL PRIMARY KEY CHECK (
        length(event_id) BETWEEN 7 AND 64
        AND substr(event_id, 1, 6) = 'audit-'
        AND substr(event_id, 7) NOT GLOB '*[^a-z0-9-]*'
        AND substr(event_id, 7, 1) GLOB '[a-z0-9]'
        AND substr(event_id, -1, 1) GLOB '[a-z0-9]'
        AND event_id NOT GLOB '*--*'
    ),
    event_sequence INTEGER NOT NULL UNIQUE CHECK (
        typeof(event_sequence) = 'integer'
        AND event_sequence > 0
    ),
    event_type TEXT NOT NULL CHECK (
        event_type IN (
            'INVITATION_ISSUED',
            'INVITATION_REVOKED',
            'INVITATION_REISSUED',
            'INVITATION_REJECTED',
            'DECISION_RECORDED',
            'DECISION_REPLAYED',
            'DECISION_REJECTED',
            'CONTRACT_SUPERSEDED'
        )
    ),
    occurred_at_us INTEGER NOT NULL CHECK (
        typeof(occurred_at_us) = 'integer'
        AND occurred_at_us >= 0
    ),
    contract_id TEXT NOT NULL CHECK (
        length(trim(contract_id)) > 0
    ),
    contract_version TEXT NOT NULL CHECK (
        length(trim(contract_version)) > 0
    ),
    confirmation_fingerprint TEXT NOT NULL CHECK (
        length(confirmation_fingerprint) = 64
        AND confirmation_fingerprint NOT GLOB '*[^a-f0-9]*'
    ),
    invitation_id TEXT CHECK (
        invitation_id IS NULL
        OR (
            length(invitation_id) BETWEEN 8 AND 64
            AND substr(invitation_id, 1, 7) = 'review-'
            AND substr(invitation_id, 8) NOT GLOB '*[^a-z0-9-]*'
            AND substr(invitation_id, 8, 1) GLOB '[a-z0-9]'
            AND substr(invitation_id, -1, 1) GLOB '[a-z0-9]'
            AND invitation_id NOT GLOB '*--*'
        )
    ),
    confirmation_id TEXT CHECK (
        confirmation_id IS NULL
        OR (
            length(confirmation_id) = 68
            AND substr(confirmation_id, 1, 4) = 'cnf_'
            AND substr(confirmation_id, 5) NOT GLOB '*[^a-f0-9]*'
        )
    ),
    actor_issuer TEXT CHECK (
        actor_issuer IS NULL
        OR (
            length(actor_issuer) BETWEEN 1 AND 256
            AND length(trim(actor_issuer)) > 0
        )
    ),
    actor_subject TEXT CHECK (
        actor_subject IS NULL
        OR (
            length(actor_subject) BETWEEN 1 AND 256
            AND length(trim(actor_subject)) > 0
        )
    ),
    actor_organization_id TEXT CHECK (
        actor_organization_id IS NULL
        OR (
            length(actor_organization_id) BETWEEN 1 AND 256
            AND length(trim(actor_organization_id)) > 0
        )
    ),
    outcome TEXT NOT NULL CHECK (
        outcome IN ('SUCCEEDED', 'REPLAYED', 'REJECTED')
    ),
    reason_code TEXT CHECK (
        reason_code IS NULL
        OR (
            length(reason_code) BETWEEN 1 AND 64
            AND substr(reason_code, 1, 1) GLOB '[A-Z]'
            AND reason_code NOT GLOB '*[^A-Z0-9_]*'
        )
    ),
    trace_id TEXT CHECK (
        trace_id IS NULL
        OR (
            length(trace_id) = 32
            AND trace_id NOT GLOB '*[^a-f0-9]*'
        )
    ),
    metadata_schema_version TEXT NOT NULL CHECK (
        metadata_schema_version = '1'
    ),
    metadata_adapter_name TEXT CHECK (
        metadata_adapter_name IS NULL
        OR metadata_adapter_name IN ('memory', 'sqlite', 'postgresql')
    ),
    metadata_adapter_version TEXT CHECK (
        metadata_adapter_version IS NULL
        OR (
            metadata_adapter_version NOT GLOB '*[^0-9.]*'
            AND substr(metadata_adapter_version, 1, 1) != '.'
            AND substr(metadata_adapter_version, -1, 1) != '.'
            AND metadata_adapter_version NOT GLOB '*..*'
            AND (
                (
                    instr(metadata_adapter_version, '.') = 0
                    AND length(metadata_adapter_version) BETWEEN 1 AND 4
                )
                OR (
                    length(metadata_adapter_version)
                    - length(replace(metadata_adapter_version, '.', '')) = 1
                    AND instr(metadata_adapter_version, '.') BETWEEN 2 AND 5
                    AND length(metadata_adapter_version)
                    - instr(metadata_adapter_version, '.') BETWEEN 1 AND 4
                )
                OR (
                    length(metadata_adapter_version)
                    - length(replace(metadata_adapter_version, '.', '')) = 2
                    AND instr(metadata_adapter_version, '.') BETWEEN 2 AND 5
                    AND instr(
                        substr(
                            metadata_adapter_version,
                            instr(metadata_adapter_version, '.') + 1
                        ),
                        '.'
                    ) BETWEEN 2 AND 5
                    AND length(
                        substr(
                            metadata_adapter_version,
                            instr(metadata_adapter_version, '.') + 1
                        )
                    )
                    - instr(
                        substr(
                            metadata_adapter_version,
                            instr(metadata_adapter_version, '.') + 1
                        ),
                        '.'
                    ) BETWEEN 1 AND 4
                )
            )
        )
    ),
    CHECK (
        (
            actor_issuer IS NULL
            AND actor_subject IS NULL
            AND actor_organization_id IS NULL
        )
        OR (
            actor_issuer IS NOT NULL
            AND actor_subject IS NOT NULL
        )
    ),
    CHECK (
        (
            event_type = 'INVITATION_ISSUED'
            AND outcome = 'SUCCEEDED'
            AND invitation_id IS NOT NULL
            AND confirmation_id IS NULL
            AND reason_code IS NULL
        )
        OR (
            event_type = 'INVITATION_REVOKED'
            AND outcome = 'SUCCEEDED'
            AND invitation_id IS NOT NULL
            AND confirmation_id IS NULL
            AND reason_code IN (
                'MANUAL',
                'REISSUED',
                'CONTRACT_SUPERSEDED',
                'SECURITY_RESPONSE'
            )
        )
        OR (
            event_type = 'INVITATION_REISSUED'
            AND outcome = 'SUCCEEDED'
            AND invitation_id IS NOT NULL
            AND confirmation_id IS NULL
            AND reason_code = 'REISSUED'
        )
        OR (
            event_type = 'INVITATION_REJECTED'
            AND outcome = 'REJECTED'
            AND invitation_id IS NOT NULL
            AND confirmation_id IS NULL
            AND reason_code IS NOT NULL
        )
        OR (
            event_type = 'DECISION_RECORDED'
            AND outcome = 'SUCCEEDED'
            AND invitation_id IS NOT NULL
            AND confirmation_id IS NOT NULL
            AND reason_code IS NULL
        )
        OR (
            event_type = 'DECISION_REPLAYED'
            AND outcome = 'REPLAYED'
            AND invitation_id IS NOT NULL
            AND confirmation_id IS NOT NULL
            AND reason_code IS NULL
        )
        OR (
            event_type = 'DECISION_REJECTED'
            AND outcome = 'REJECTED'
            AND invitation_id IS NOT NULL
            AND confirmation_id IS NULL
            AND reason_code IS NOT NULL
        )
        OR (
            event_type = 'CONTRACT_SUPERSEDED'
            AND outcome = 'SUCCEEDED'
            AND invitation_id IS NULL
            AND confirmation_id IS NULL
            AND reason_code = 'CONTRACT_SUPERSEDED'
        )
    )
) STRICT;

CREATE INDEX confirmation_audit_events_binding_sequence_idx
ON confirmation_audit_events (
    contract_id,
    contract_version,
    event_sequence
);

CREATE INDEX confirmation_audit_events_invitation_sequence_idx
ON confirmation_audit_events (
    invitation_id,
    event_sequence
);

CREATE INDEX confirmation_audit_events_confirmation_sequence_idx
ON confirmation_audit_events (
    confirmation_id,
    event_sequence
);

CREATE TRIGGER review_invitations_consistent_fingerprint
BEFORE INSERT ON review_invitations
FOR EACH ROW
WHEN EXISTS (
    SELECT 1
    FROM review_invitations
    WHERE contract_id = NEW.contract_id
      AND contract_version = NEW.contract_version
      AND confirmation_fingerprint != NEW.confirmation_fingerprint
)
OR EXISTS (
    SELECT 1
    FROM confirmation_audit_events
    WHERE contract_id = NEW.contract_id
      AND contract_version = NEW.contract_version
      AND confirmation_fingerprint != NEW.confirmation_fingerprint
)
BEGIN
    SELECT RAISE(ABORT, 'contract fingerprint conflict');
END;

CREATE TRIGGER confirmation_audit_events_consistent_fingerprint
BEFORE INSERT ON confirmation_audit_events
FOR EACH ROW
WHEN EXISTS (
    SELECT 1
    FROM review_invitations
    WHERE contract_id = NEW.contract_id
      AND contract_version = NEW.contract_version
      AND confirmation_fingerprint != NEW.confirmation_fingerprint
)
OR EXISTS (
    SELECT 1
    FROM confirmation_audit_events
    WHERE contract_id = NEW.contract_id
      AND contract_version = NEW.contract_version
      AND confirmation_fingerprint != NEW.confirmation_fingerprint
)
BEGIN
    SELECT RAISE(ABORT, 'contract fingerprint conflict');
END;

CREATE TRIGGER review_invitations_block_replace
BEFORE INSERT ON review_invitations
FOR EACH ROW
WHEN EXISTS (
    SELECT 1
    FROM review_invitations
    WHERE invitation_id = NEW.invitation_id
       OR token_digest = NEW.token_digest
)
BEGIN
    SELECT RAISE(ABORT, 'confirmation ledger is append-only');
END;

CREATE TRIGGER invitation_revocations_block_replace
BEFORE INSERT ON invitation_revocations
FOR EACH ROW
WHEN EXISTS (
    SELECT 1
    FROM invitation_revocations
    WHERE invitation_id = NEW.invitation_id
)
BEGIN
    SELECT RAISE(ABORT, 'confirmation ledger is append-only');
END;

CREATE TRIGGER confirmation_decisions_block_replace
BEFORE INSERT ON confirmation_decisions
FOR EACH ROW
WHEN EXISTS (
    SELECT 1
    FROM confirmation_decisions
    WHERE confirmation_id = NEW.confirmation_id
       OR invitation_id = NEW.invitation_id
       OR (
           contract_id = NEW.contract_id
           AND contract_version = NEW.contract_version
       )
)
BEGIN
    SELECT RAISE(ABORT, 'confirmation ledger is append-only');
END;

CREATE TRIGGER idempotency_operations_block_replace
BEFORE INSERT ON idempotency_operations
FOR EACH ROW
WHEN EXISTS (
    SELECT 1
    FROM idempotency_operations
    WHERE operation_digest = NEW.operation_digest
       OR confirmation_id = NEW.confirmation_id
       OR (
           contract_id = NEW.contract_id
           AND contract_version = NEW.contract_version
           AND idempotency_key_digest = NEW.idempotency_key_digest
       )
)
BEGIN
    SELECT RAISE(ABORT, 'confirmation ledger is append-only');
END;

CREATE TRIGGER confirmation_audit_events_block_replace
BEFORE INSERT ON confirmation_audit_events
FOR EACH ROW
WHEN EXISTS (
    SELECT 1
    FROM confirmation_audit_events
    WHERE event_id = NEW.event_id
       OR event_sequence = NEW.event_sequence
)
BEGIN
    SELECT RAISE(ABORT, 'confirmation ledger is append-only');
END;

CREATE TRIGGER review_invitations_block_update
BEFORE UPDATE ON review_invitations
FOR EACH ROW
BEGIN
    SELECT RAISE(ABORT, 'confirmation ledger is append-only');
END;

CREATE TRIGGER review_invitations_block_delete
BEFORE DELETE ON review_invitations
FOR EACH ROW
BEGIN
    SELECT RAISE(ABORT, 'confirmation ledger is append-only');
END;

CREATE TRIGGER invitation_revocations_block_update
BEFORE UPDATE ON invitation_revocations
FOR EACH ROW
BEGIN
    SELECT RAISE(ABORT, 'confirmation ledger is append-only');
END;

CREATE TRIGGER invitation_revocations_block_delete
BEFORE DELETE ON invitation_revocations
FOR EACH ROW
BEGIN
    SELECT RAISE(ABORT, 'confirmation ledger is append-only');
END;

CREATE TRIGGER confirmation_decisions_block_update
BEFORE UPDATE ON confirmation_decisions
FOR EACH ROW
BEGIN
    SELECT RAISE(ABORT, 'confirmation ledger is append-only');
END;

CREATE TRIGGER confirmation_decisions_block_delete
BEFORE DELETE ON confirmation_decisions
FOR EACH ROW
BEGIN
    SELECT RAISE(ABORT, 'confirmation ledger is append-only');
END;

CREATE TRIGGER idempotency_operations_block_update
BEFORE UPDATE ON idempotency_operations
FOR EACH ROW
BEGIN
    SELECT RAISE(ABORT, 'confirmation ledger is append-only');
END;

CREATE TRIGGER idempotency_operations_block_delete
BEFORE DELETE ON idempotency_operations
FOR EACH ROW
BEGIN
    SELECT RAISE(ABORT, 'confirmation ledger is append-only');
END;

CREATE TRIGGER confirmation_audit_events_block_update
BEFORE UPDATE ON confirmation_audit_events
FOR EACH ROW
BEGIN
    SELECT RAISE(ABORT, 'confirmation ledger is append-only');
END;

CREATE TRIGGER confirmation_audit_events_block_delete
BEFORE DELETE ON confirmation_audit_events
FOR EACH ROW
BEGIN
    SELECT RAISE(ABORT, 'confirmation ledger is append-only');
END;
""".strip()


CONFIRMATION_LEDGER_MIGRATION = Migration(
    version=1,
    name="confirmation_ledger",
    sql=CONFIRMATION_LEDGER_SQL,
)

CONFIRMATION_LEDGER_MIGRATIONS = (CONFIRMATION_LEDGER_MIGRATION,)

CONFIRMATION_LEDGER_TABLE_NAMES = frozenset(
    {
        "review_invitations",
        "invitation_revocations",
        "confirmation_decisions",
        "idempotency_operations",
        "confirmation_audit_events",
    }
)

_EXPECTED_COLUMNS = {
    "review_invitations": (
        ("invitation_id", "TEXT", 1, None, 1, 0),
        ("contract_id", "TEXT", 1, None, 0, 0),
        ("contract_version", "TEXT", 1, None, 0, 0),
        ("confirmation_fingerprint", "TEXT", 1, None, 0, 0),
        ("token_digest", "TEXT", 1, None, 0, 0),
        ("token_digest_version", "TEXT", 1, None, 0, 0),
        ("intended_organization_id", "TEXT", 1, None, 0, 0),
        ("issued_by_subject", "TEXT", 1, None, 0, 0),
        ("issued_at_us", "INTEGER", 1, None, 0, 0),
        ("expires_at_us", "INTEGER", 1, None, 0, 0),
    ),
    "invitation_revocations": (
        ("invitation_id", "TEXT", 1, None, 1, 0),
        ("revoked_at_us", "INTEGER", 1, None, 0, 0),
        ("revoked_by_subject", "TEXT", 1, None, 0, 0),
        ("reason_code", "TEXT", 1, None, 0, 0),
    ),
    "confirmation_decisions": (
        ("confirmation_id", "TEXT", 1, None, 1, 0),
        ("invitation_id", "TEXT", 1, None, 0, 0),
        ("contract_id", "TEXT", 1, None, 0, 0),
        ("contract_version", "TEXT", 1, None, 0, 0),
        ("confirmation_fingerprint", "TEXT", 1, None, 0, 0),
        ("reviewer_issuer", "TEXT", 1, None, 0, 0),
        ("reviewer_subject", "TEXT", 1, None, 0, 0),
        ("reviewer_organization_id", "TEXT", 1, None, 0, 0),
        ("reviewer_display_name_snapshot", "TEXT", 1, None, 0, 0),
        ("decision", "TEXT", 1, None, 0, 0),
        ("agreement_acknowledged", "INTEGER", 1, None, 0, 0),
        ("rationale", "TEXT", 1, None, 0, 0),
        ("decided_at_us", "INTEGER", 1, None, 0, 0),
        ("request_digest", "TEXT", 1, None, 0, 0),
    ),
    "idempotency_operations": (
        ("operation_digest", "TEXT", 1, None, 1, 0),
        ("contract_id", "TEXT", 1, None, 0, 0),
        ("contract_version", "TEXT", 1, None, 0, 0),
        ("idempotency_key_digest", "TEXT", 1, None, 0, 0),
        ("request_digest", "TEXT", 1, None, 0, 0),
        ("confirmation_id", "TEXT", 1, None, 0, 0),
        ("created_at_us", "INTEGER", 1, None, 0, 0),
    ),
    "confirmation_audit_events": (
        ("event_id", "TEXT", 1, None, 1, 0),
        ("event_sequence", "INTEGER", 1, None, 0, 0),
        ("event_type", "TEXT", 1, None, 0, 0),
        ("occurred_at_us", "INTEGER", 1, None, 0, 0),
        ("contract_id", "TEXT", 1, None, 0, 0),
        ("contract_version", "TEXT", 1, None, 0, 0),
        ("confirmation_fingerprint", "TEXT", 1, None, 0, 0),
        ("invitation_id", "TEXT", 0, None, 0, 0),
        ("confirmation_id", "TEXT", 0, None, 0, 0),
        ("actor_issuer", "TEXT", 0, None, 0, 0),
        ("actor_subject", "TEXT", 0, None, 0, 0),
        ("actor_organization_id", "TEXT", 0, None, 0, 0),
        ("outcome", "TEXT", 1, None, 0, 0),
        ("reason_code", "TEXT", 0, None, 0, 0),
        ("trace_id", "TEXT", 0, None, 0, 0),
        ("metadata_schema_version", "TEXT", 1, None, 0, 0),
        ("metadata_adapter_name", "TEXT", 0, None, 0, 0),
        ("metadata_adapter_version", "TEXT", 0, None, 0, 0),
    ),
}


_CREATE_OBJECT = re.compile(
    r"^CREATE\s+(TABLE|INDEX|TRIGGER)\s+([a-z][a-z0-9_]*)\b",
    flags=re.IGNORECASE,
)


def _split_schema_sql(sql: str) -> tuple[str, ...]:
    statements: list[str] = []
    buffer: list[str] = []
    for character in sql:
        buffer.append(character)
        if (
            character == ";"
            and sqlite3.complete_statement("".join(buffer))
        ):
            statements.append("".join(buffer).strip())
            buffer.clear()
    if "".join(buffer).strip():
        raise ValueError
    return tuple(statements)


def _canonical_object_sql(sql: str) -> str:
    return " ".join(sql.strip().removesuffix(";").split())


def _expected_schema_objects() -> dict[tuple[str, str], str]:
    objects: dict[tuple[str, str], str] = {}
    for statement in _split_schema_sql(CONFIRMATION_LEDGER_SQL):
        match = _CREATE_OBJECT.match(statement)
        if match is None:
            raise ValueError
        object_type, name = match.groups()
        key = (object_type.lower(), name.lower())
        if key in objects:
            raise ValueError
        objects[key] = _canonical_object_sql(statement)
    return objects


_EXPECTED_SCHEMA_OBJECTS = _expected_schema_objects()
CONFIRMATION_LEDGER_INDEX_NAMES = frozenset(
    name
    for (object_type, name) in _EXPECTED_SCHEMA_OBJECTS
    if object_type == "index"
)
CONFIRMATION_LEDGER_TRIGGER_NAMES = frozenset(
    name
    for (object_type, name) in _EXPECTED_SCHEMA_OBJECTS
    if object_type == "trigger"
)
_ALL_EXPECTED_NAMES = frozenset(
    name for _object_type, name in _EXPECTED_SCHEMA_OBJECTS
)


def validate_confirmation_schema(connection: object) -> None:
    """Fail closed unless migration 0001 and every domain object are exact."""

    try:
        history = read_applied_migrations(connection)
        if (
            len(history) != 1
            or history[0].version != CONFIRMATION_LEDGER_MIGRATION.version
            or history[0].name != CONFIRMATION_LEDGER_MIGRATION.name
            or history[0].checksum
            != CONFIRMATION_LEDGER_MIGRATION.checksum
        ):
            raise ValueError

        rows = connection.execute(  # type: ignore[attr-defined]
            """
            SELECT type, name, tbl_name, sql
            FROM sqlite_master
            WHERE sql IS NOT NULL
            """
        ).fetchall()
        relevant: dict[tuple[str, str], str] = {}
        table_pattern = re.compile(
            r"\b(?:{0})\b".format(
                "|".join(
                    re.escape(name)
                    for name in sorted(CONFIRMATION_LEDGER_TABLE_NAMES)
                )
            ),
            flags=re.IGNORECASE,
        )
        for row in rows:
            object_type = str(row["type"]).lower()
            name = str(row["name"]).lower()
            table_name = str(row["tbl_name"]).lower()
            sql = row["sql"]
            if not isinstance(sql, str):
                raise ValueError
            expected_name = name in _ALL_EXPECTED_NAMES
            related_object = (
                object_type in {"index", "trigger"}
                and table_name in CONFIRMATION_LEDGER_TABLE_NAMES
            )
            cross_table_trigger = (
                object_type == "trigger"
                and table_pattern.search(sql) is not None
            )
            if expected_name or related_object or cross_table_trigger:
                key = (object_type, name)
                if key in relevant:
                    raise ValueError
                relevant[key] = _canonical_object_sql(sql)

        if relevant != _EXPECTED_SCHEMA_OBJECTS:
            raise ValueError
        for table_name, expected_columns in _EXPECTED_COLUMNS.items():
            columns = connection.execute(  # type: ignore[attr-defined]
                "PRAGMA table_xinfo({0})".format(table_name)
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
            if actual_columns != expected_columns:
                raise ValueError
    except LedgerUnavailable:
        raise
    except ConfirmationSQLiteError:
        raise LedgerUnavailable() from None
    except (AttributeError, KeyError, sqlite3.Error, TypeError, ValueError):
        raise LedgerUnavailable() from None
