# ADR 0001: Durable confirmation ledger

- Status: Accepted
- Date: 2026-07-24
- Decision owners: ExitSpec maintainers
- Scope: Customer review invitations, terminal decisions, idempotency, and their audit history

## Context

ExitSpec already has the correct domain boundary for a deterministic local demo:

- `canonical_confirmation_payload` is both the customer-visible agreement and
  the source of the confirmation fingerprint;
- review capabilities are random, expiring, and bound to one contract ID,
  version, and fingerprint;
- invitation records contain a SHA-256 token digest rather than the raw token;
- `CONFIRM` requires explicit acknowledgement;
- `REQUEST_CHANGES` is terminal for the reviewed version but does not authorize
  a freeze;
- identical idempotent retries return the original decision, while conflicting
  reuse is rejected; and
- a confirmation can freeze only the exact contract version and fingerprint it
  affirms.

The current browser loop does not persist those facts. `DemoSession` owns one
invitation, its raw token, one confirmation, and an in-memory idempotency map.
Restarting the process loses every link and decision. Reviewer identity is typed
text, capability possession is not contract-scoped authorization, and there is
no durable revocation or audit service. These are explicit demo limitations, not
production guarantees.

The next implementation must preserve the existing canonical fingerprint and
domain checks while making the confirmation history durable. It must not turn
confirmation into evidence or production authorization.

## Decision

Introduce a `ConfirmationLedgerService` application service backed by a
`ConfirmationStore` storage port. A SQLite adapter is the default local
implementation. The store will persist immutable invitation, revocation,
decision, idempotency, and audit records. A future PostgreSQL adapter must
implement the same port and pass the same conformance suite.

The ledger is the source of record for whether a review capability was issued,
revoked, expired, consumed, or used to record a terminal decision. The contract
repository remains the source of record for contract content and versioning.
Every ledger operation is bound to:

```text
contract_id + contract_version + confirmation_fingerprint
```

Callers must recompute the fingerprint from
`canonical_confirmation_payload(contract)`. A stored decision never authorizes a
different ID, version, or fingerprint.

Normal application code may append and read records. It may not update or delete
them. Expiry and effective status are derived from immutable facts rather than
stored as mutable status flags.

## Non-goals

This decision does not:

- persist the complete contract, transcript, evidence pack, or POC run;
- make customer confirmation a production deployment authorization;
- define a hosted identity provider or organization membership system;
- provide multi-region replication or SQLite high availability;
- add digital signatures or non-repudiation claims;
- backfill process-local demo invitations or confirmations; or
- make audit retention and legal deletion policy decisions.

## Domain records

The logical model contains five append-only record types.

### Contract binding

`ContractBinding` is a value object, not an independently mutable ledger row:

```text
contract_id
contract_version
confirmation_fingerprint
```

Contract IDs and versions are treated as immutable. Reusing one
`contract_id + contract_version` with a different fingerprint is a consistency
error, not a revision. A revision must create a new version.

### Review invitation

`ReviewInvitationRecord` contains:

```text
invitation_id
contract_id
contract_version
confirmation_fingerprint
token_digest
token_digest_version
intended_organization_id
issued_by_subject
issued_at
expires_at
```

The application generates at least 256 bits of cryptographically secure random
token material. It returns the raw token exactly once to the delivery boundary
and passes only a domain-separated SHA-256 digest to the ledger. The raw token
must never be written to a database, file, trace, metric, exception, or access
log.

Because review tokens are high-entropy random values, a digest is sufficient to
prevent practical offline recovery. `token_digest_version` permits a future
digest migration. Token comparisons remain constant-time.

### Invitation revocation

`InvitationRevocationRecord` contains:

```text
invitation_id
revoked_at
revoked_by_subject
reason_code
```

There can be at most one revocation per invitation. Allowed reason codes are a
closed enum initially containing:

```text
MANUAL
REISSUED
CONTRACT_SUPERSEDED
SECURITY_RESPONSE
```

Free-form customer or operator content is not stored as a revocation reason.

### Confirmation decision

`ConfirmationDecisionRecord` contains:

```text
confirmation_id
invitation_id
contract_id
contract_version
confirmation_fingerprint
reviewer_issuer
reviewer_subject
reviewer_organization_id
reviewer_display_name_snapshot
decision
agreement_acknowledged
rationale
decided_at
request_digest
```

`decision` is exactly `CONFIRM` or `REQUEST_CHANGES`. `CONFIRM` requires
`agreement_acknowledged = true`. `REQUEST_CHANGES` requires a non-empty,
length-bounded rationale. The rationale is protected customer data: it belongs
in the decision record, not operational telemetry.

There is at most one terminal decision for a contract ID and version. The
stored fingerprint must match both the invitation and the current canonical
contract. A second idempotency key cannot replace an existing decision.

### Idempotency operation

`IdempotencyOperationRecord` contains:

```text
operation_id
contract_id
contract_version
idempotency_key_digest
request_digest
confirmation_id
created_at
```

The raw idempotency key is not persisted or logged. `operation_id` and
`idempotency_key_digest` are domain-separated hashes. `request_digest` is a
canonical digest over all decision-making inputs:

```text
contract binding
invitation_id
reviewer issuer, subject, and organization
decision
agreement acknowledgement
rationale
```

This makes an identical retry distinguishable from conflicting reuse without
retaining the raw idempotency key.

### Audit event

`ConfirmationAuditEvent` contains:

```text
event_id
event_sequence
event_type
occurred_at
contract_id
contract_version
confirmation_fingerprint
invitation_id (optional)
confirmation_id (optional)
actor_issuer (optional)
actor_subject (optional)
actor_organization_id (optional)
outcome
reason_code (optional)
trace_id (optional)
safe_metadata
```

Initial event types are:

```text
INVITATION_ISSUED
INVITATION_REVOKED
INVITATION_REISSUED
INVITATION_REJECTED
DECISION_RECORDED
DECISION_REPLAYED
DECISION_REJECTED
CONTRACT_SUPERSEDED
```

`safe_metadata` is an allowlisted, versioned object. It may contain adapter
version and non-secret reason codes. It may not contain raw tokens,
idempotency keys, request or response bodies, agreement content, rationale,
email addresses, or authentication credentials.

## Effective invitation state

Invitation state is derived at read or decision time:

```text
REVOKED   if a revocation record exists
DECIDED   if a terminal decision exists
EXPIRED   if now >= expires_at
STALE     if the current contract binding differs
ACTIVE    otherwise
```

The evaluation order above is deterministic. `now` is supplied by an injected
UTC clock so boundary behavior is testable. An invitation is usable only when
it is `ACTIVE`, its token digest matches, and the authenticated principal is
authorized for the exact contract.

Reissuing a link atomically appends a revocation for the previous active
invitation, appends the replacement invitation, and appends corresponding audit
events. Revocation and decision facts never reverse. Expiry depends on a trusted
UTC wall clock; production deployments must synchronize and monitor host time.
Multi-node clock-skew hardening is deferred until a multi-host store is added.

## Storage port

Domain and HTTP code call `ConfirmationLedgerService`. That service depends on
the storage port, the canonical contract repository, an injected UTC clock, and
a separate reviewer authorizer. SQLite details do not enter domain or transport
code:

```python
class ConfirmationStore(Protocol):
    def issue_invitation(self, command: IssueInvitation) -> ReviewInvitationRecord: ...
    def reissue_invitation(self, command: ReissueInvitation) -> ReviewInvitationRecord: ...
    def resolve_invitation(self, token_digest: str, now: datetime) -> InvitationView: ...
    def revoke_invitation(self, command: RevokeInvitation) -> InvitationView: ...
    def record_decision(self, command: RecordDecision) -> DecisionWriteResult: ...
    def get_decision(self, binding: ContractBinding) -> ConfirmationDecisionRecord | None: ...
    def list_audit_events(self, query: AuditQuery) -> Sequence[ConfirmationAuditEvent]: ...
```

The concrete command and result types are frozen domain models. They carry
digests and authenticated principal snapshots supplied by the application
service, never raw review tokens or raw idempotency keys.
`DecisionWriteResult` contains the immutable decision and `replayed: bool`.
The store enforces organization and binding equality; it does not validate
identity-provider claims or decide organization membership.

All adapters must expose the same typed failures:

```text
InvitationNotFound
InvitationExpired
InvitationRevoked
InvitationConsumed
ContractBindingMismatch
DecisionAlreadyRecorded
IdempotencyConflict
LedgerUnavailable
```

HTTP translation is performed outside the ledger:

- unauthenticated requests return `401`;
- authenticated but unauthorized requests return `403`;
- unknown or binding-mismatched capabilities return a non-secret `404`;
- a recognized expired or revoked capability returns `410`;
- conflicting idempotency or terminal-decision attempts return `409`;
- an identical replay returns `200` with the original decision and
  `replayed = true`.

The customer UI may explain a recognized expiry or revocation only after
authentication. It must not expose whether an arbitrary guessed token ever
existed.

## Transaction and idempotency semantics

### Issue

Invitation issuance is one transaction:

1. verify that no different fingerprint has been registered for the same
   contract ID and version;
2. insert the immutable invitation;
3. append `INVITATION_ISSUED`; and
4. commit before the raw token is handed to delivery.

If delivery fails, the token is not reconstructed. The invitation is revoked
and a new token is issued.

### Revoke and reissue

Revocation inserts the revocation and audit event in one transaction. A repeated
identical revocation returns the existing effective state. A conflicting second
revocation reason is rejected.

Reissue performs old-invitation revocation, new-invitation insertion, and both
audit events in one transaction. At no committed point are both invitations
active.

### Decide

Decision recording is one serializable logical transaction:

1. resolve the invitation by token digest;
2. verify it is active at the injected transaction time;
3. verify invitation, current contract, and command bindings are identical;
4. verify the trusted principal snapshot matches the invitation's intended
   organization; the application service has already authorized that principal
   for this exact contract;
5. derive the operation ID, idempotency-key digest, and request digest;
6. if the operation exists with the same request digest, return its original
   decision as a replay;
7. if the operation exists with a different request digest, reject with
   `IdempotencyConflict`;
8. if another terminal decision exists for the version, reject with
   `DecisionAlreadyRecorded`;
9. validate decision, acknowledgement, and rationale invariants;
10. insert the decision and idempotency operation;
11. append `DECISION_RECORDED`; and
12. commit.

Database uniqueness constraints, not a process mutex, are the final concurrency
authority. Two simultaneous first decisions can produce only one committed
terminal record. The loser re-reads state and returns either an identical replay
or a conflict.

Failed transactions leave no decision, idempotency receipt, or success audit
event. Rejection telemetry is best-effort and must never make an invalid
decision appear committed.

## SQLite adapter

SQLite is the default because ExitSpec currently ships as a one-command local
application. The first adapter uses:

- one configurable database file outside packaged resources;
- owner-only file permissions where the platform supports them;
- foreign keys enabled;
- write-ahead logging;
- `synchronous = FULL`;
- a bounded busy timeout; and
- `BEGIN IMMEDIATE` for write transactions.

Application tables are:

```text
review_invitations
invitation_revocations
confirmation_decisions
idempotency_operations
confirmation_audit_events
schema_migrations
```

The five domain tables have triggers that reject `UPDATE` and `DELETE` from the
application connection. Only the migration and explicit retention tooling may
replace those protections, and normal request handling never receives that
authority.

Required constraints include:

- primary keys on every record ID;
- a unique token digest;
- one binding fingerprint per contract ID and version;
- one revocation per invitation;
- one decision per invitation;
- one terminal decision per contract ID and version;
- one idempotency operation per contract ID, version, and key digest;
- foreign keys from revocations and decisions to invitations;
- foreign keys from idempotency operations to decisions; and
- checks for digest shape, decision enum, acknowledgement, timestamp ordering,
  and bounded text.

Timestamps are stored as UTC epoch microseconds at the adapter boundary. Boolean
values use constrained integers. Adapter code returns timezone-aware UTC
`datetime` values and domain booleans.

SQLite is supported for a single application instance on a local filesystem. It
is not supported on a shared network filesystem or as a multi-host write store.
Those deployments require the PostgreSQL adapter.

## Future PostgreSQL adapter

The PostgreSQL adapter will preserve port behavior, uniqueness constraints,
transaction boundaries, typed errors, and audit ordering. It may use native
`timestamptz`, row-level locks, and database roles, but those physical choices
must not leak into domain code.

Both adapters run the same black-box conformance suite. Adding PostgreSQL does
not permit dual writes between adapters; one ledger is authoritative for a
deployment.

## Crash recovery

No in-memory map is authoritative after this change. On startup, the application
opens the ledger, applies compatible migrations, verifies required settings, and
serves only after a read/write health check succeeds.

Committed invitations and decisions are reconstructed from ledger records after
restart. An interrupted transaction is either fully committed or absent.
Temporary `database is locked` and serialization failures may be retried within
a bounded deadline using the same idempotency operation. Unknown commit outcome
must be resolved by reading the operation before retrying.

Raw link tokens cannot be recovered from the ledger by design. If the process
crashes after invitation commit but before delivery completes, the operator or
delivery worker revokes and reissues the invitation.

SQLite backup and restore copy the database through SQLite's online backup API,
not by copying an open file. Recovery tests must kill the process at each write
boundary and prove that no partial authority survives.

## Authentication and authorization boundary

A review capability identifies an invitation; it does not identify or authorize
a person.

Hosted mode requires an authentication adapter to create a trusted
`ReviewerPrincipal` from validated identity-provider claims:

```text
issuer
subject
organization_id
display_name
authentication_time
```

The server never accepts these authority fields from request JSON. A
contract-authorization policy must grant that principal permission to review
the exact contract for the intended organization. Authorization is checked
before agreement disclosure and checked again immediately before the decision
transaction. The ledger records the trusted principal snapshot.

The local synthetic demo may use its existing typed identity adapter only when
explicitly running in synthetic mode. Its UI and API must continue to state that
the identity is unauthenticated and cannot represent hosted authorization.

A production review link should be exchanged for a short-lived `Secure`,
`HttpOnly`, `SameSite=Strict` session and redirected to a token-free URL.
Review responses set `Referrer-Policy: no-referrer`, load no third-party assets,
and template access-log paths so raw capability material cannot be recorded.

## Observability and privacy

Operational telemetry records:

- operation name and outcome;
- stable reason code;
- adapter name and schema version;
- latency and retry count;
- contention or transaction-conflict count;
- trace ID; and
- pseudonymous record IDs when needed for correlation.

Operational telemetry never records:

- raw or digested review tokens;
- raw or digested idempotency keys;
- authorization headers, cookies, or identity-provider claims;
- contract or agreement content;
- customer names, email addresses, rationale, or request bodies; or
- generated customer review URLs.

The durable audit table is access-controlled customer metadata, not a general
log sink. Display names and rationale receive the same retention and access
controls as contract records. Audit reads are themselves authorized and
audited. Metrics use bounded outcome labels to avoid customer-controlled
cardinality.

## Migration and rollout

Migration `0001_confirmation_ledger` creates the six SQLite tables, constraints,
and append-only protections. Migrations are:

- ordered and recorded with version plus checksum;
- applied under an exclusive migration lock and transaction;
- forward-only in normal operation;
- refused when the database schema is newer than the binary; and
- preceded by a verified backup when upgrading a non-empty database.

Rollout occurs in safe slices:

1. add frozen port types and an adapter conformance suite;
2. add the SQLite schema and adapter behind a disabled feature flag;
3. route invitation issuance and lookup through the ledger;
4. route decisions and idempotent replay through the ledger;
5. add revocation, reissue, and audit-history reads;
6. remove the in-memory confirmation map as an authority; and
7. enable the durable adapter by default after restart and concurrency tests
   pass.

There is no in-memory data migration. Upgrading from the current demo
invalidates process-local links and starts a clean durable ledger, or the
operator explicitly reissues them.

Application rollback must retain read and write compatibility with schema
version 1. Automatic destructive down-migrations are forbidden. If a release
must be rolled back before a compatible binary is available, stop decision
writes, preserve the database, deploy a forward repair, and revoke/reissue any
links whose delivery status is uncertain.

## Rejected alternatives

### Keep confirmation state in `DemoSession`

Rejected because restart loses authority, multiple workers disagree, and
process locks cannot provide durable idempotency or audit history.

### Call SQLite directly from HTTP handlers

Rejected because it couples transport, policy, and persistence; makes a future
PostgreSQL adapter invasive; and encourages authorization checks outside the
transactional application service.

### Maintain one mutable "current status" row

Rejected because updates erase history and make crash, revocation, and
idempotency disputes harder to explain. Effective state is derived from
append-only facts.

### Persist raw review tokens

Rejected because a database or backup disclosure would immediately disclose
live customer capabilities. Raw tokens are one-time delivery material.

### Store confirmation in browser storage

Rejected because browser state is user-controlled, non-authoritative, difficult
to revoke, and unavailable to other server instances.

### Use Redis as the source of truth

Rejected because the project needs durable relational constraints, transaction
history, and portable local operation. Redis may later support caches or rate
limits, never confirmation authority.

### Require PostgreSQL immediately

Rejected because it breaks the one-command local product before multi-host
operation is required. The port and conformance suite preserve a safe upgrade
path.

### Event-source the entire ExitSpec domain

Rejected as an unnecessary expansion. This ADR appends security-relevant
confirmation facts without redesigning contracts, evidence runs, or proof
packs.

### Dual-write to in-memory and durable stores

Rejected because partial success creates two competing truths. The feature flag
selects exactly one authoritative ledger.

## Acceptance gates

The durable ledger may become the default only when all of the following are
automated and passing:

1. **Restart durability:** issue and confirm, terminate the process, restart,
   and retrieve the exact same immutable decision and audit history.
2. **Identical replay:** the same idempotency key and request returns the
   original confirmation ID and timestamp with `replayed = true`.
3. **Conflicting replay:** the same idempotency key with any changed
   decision-making input returns `IdempotencyConflict` and does not append a
   second decision.
4. **Concurrent first write:** at least two simultaneous valid decisions for
   one version produce exactly one terminal decision; every loser is a replay
   or conflict.
5. **Terminal immutability:** a different idempotency key cannot overwrite
   either `CONFIRM` or `REQUEST_CHANGES`.
6. **Acknowledgement:** `CONFIRM` with missing or false acknowledgement is
   rejected before persistence.
7. **Token custody:** database files, backups, logs, traces, exceptions, API
   payloads, and object representations contain no raw review token.
8. **Expiry:** a token is accepted immediately before expiry and rejected at
   the exact expiry instant and afterward, including after restart.
9. **Revocation and reissue:** a revoked token never succeeds; reissue leaves
   only the replacement active; both actions are auditable.
10. **Version invalidation:** revising the contract creates a new version, and
    the prior invitation or decision cannot authorize the new fingerprint.
11. **Authorization:** unauthenticated and wrong-organization principals cannot
    read the agreement or decide; an authorized principal can.
12. **Crash atomicity:** fault injection at every transaction boundary leaves
    either all decision, idempotency, and audit records committed or none.
13. **Audit completeness:** issue, revoke/reissue, decision, replay, rejection,
    and supersession events are ordered, attributable, and contain no forbidden
    content.
14. **Migration safety:** a fresh database, an existing version-1 database, a
    checksum mismatch, and a database newer than the binary all produce the
    specified safe behavior.
15. **Adapter conformance:** SQLite passes the full ledger contract suite; any
    future PostgreSQL adapter must pass the same suite before use.
16. **Regression:** the existing deterministic demo, confirmation fingerprint,
    freeze gate, BLOCKED/PASS rerun, Evidence Pack, packaged-wheel, and browser
    tests remain green.

Until these gates pass, the existing UI must continue to describe customer
identity and confirmation storage as synthetic and non-production.

## Consequences

Positive consequences:

- confirmation and idempotent replay survive process restart;
- invitation revocation and expiry become explicit, testable facts;
- concurrent requests cannot create competing decisions;
- raw capability material is excluded from durable storage;
- authorization and storage boundaries become independently testable; and
- PostgreSQL can be added without rewriting domain or HTTP code.

Costs and constraints:

- SQLite supports only the documented single-instance deployment shape;
- delivery must handle committed-but-undelivered invitations by reissuing;
- identity, authorization, migrations, retention, backup, and audit access add
  operational responsibility; and
- append-only records require deliberate retention and deletion design rather
  than ad hoc row mutation.
