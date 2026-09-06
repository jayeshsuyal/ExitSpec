"""Synthetic-only source-authoring permits, consumed ledger and D/F lifecycle.

No HTTP, browser bootstrap or live launcher is wired here.  A synthetic launch
cannot be promoted to a funded launch, even when a caller supplies real-looking
metadata. Owner locks precede this module's operation lock; the supervisor lock
is always last. Network-capable execution is deliberately unavailable.
"""

from __future__ import annotations

import hashlib
import hmac
import math
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from threading import RLock

from .canonical import canonical_json_bytes
from .source_authoring_ipc import SourceAuthoringWorkerError
from .source_authoring_owners import (
    SourceAuthoringOwners,
    SourceAuthoringOwnersError,
    SourceAuthoringSnapshot,
)
from .source_authoring_policy import (
    REDACTION_CONFIGURATION_DIGEST,
    SourceAuthoringPolicyError,
    build_body,
    build_intent,
    synthetic_policy,
    validate_intent,
    validate_output,
)
from .source_authoring_supervisor import SyntheticSourceAuthoringSupervisor


class SourceAuthoringOperationError(ValueError):
    """Content-free local boundary failure."""

    def __init__(self, code: str = "operation_refused") -> None:
        self.code = code
        super().__init__("Source authoring operation refused: " + code)


class _PrivateHandle:
    __slots__ = ()

    def __copy__(self):
        raise SourceAuthoringOperationError("private_handle")

    def __deepcopy__(self, memo):
        raise SourceAuthoringOperationError("private_handle")

    def __reduce__(self):
        raise SourceAuthoringOperationError("private_handle")

    def __repr__(self) -> str:
        return "<source-authoring private handle>"


_ISSUER = object()


class SyntheticBrowserSession(_PrivateHandle):
    """Process-private synthetic session; public session IDs confer no authority."""

    __slots__ = ("_identity", "_secret")

    def __init__(self, issuer: object = None) -> None:
        if issuer is not _ISSUER:
            raise SourceAuthoringOperationError("private_handle")
        self._identity = secrets.token_hex(32)
        self._secret = secrets.token_bytes(32)


class AuthorizedSourceAuthoringRequest(_PrivateHandle):
    """Issuer-owned identity permit. It contains no outbound request text."""

    __slots__ = ("_operation",)

    def __init__(self, issuer: object = None, operation: str = "") -> None:
        if issuer is not _ISSUER:
            raise SourceAuthoringOperationError("private_handle")
        self._operation = operation


@dataclass(frozen=True, slots=True)
class SourceAuthoringDisclosure:
    operation_id: str
    disclosure_sha256: str
    source_sha256: str
    expires_monotonic: float


@dataclass(frozen=True, slots=True)
class SourceAuthoringOperationReceipt:
    operation_id: str
    state: str
    intent_sha256: str | None = None
    attempts: int = 0
    reserved_usd: Decimal = Decimal("0.00")
    code: str | None = None
    authoring_receipt_id: str | None = None


@dataclass(frozen=True, slots=True, repr=False)
class _Record:
    session: str
    disclosure: SourceAuthoringDisclosure
    source: SourceAuthoringSnapshot | None
    body: bytes | None
    issued: float
    issued_at: float
    receipt: SourceAuthoringOperationReceipt
    intent: object = None
    permit: AuthorizedSourceAuthoringRequest | None = None
    deadline: float | None = None


_TERMINAL = frozenset(
    {"SUCCEEDED", "FAILED", "OUTCOME_UNKNOWN", "STALE", "EXPIRED", "REVOKED"}
)


def _digest(domain: bytes, value: object) -> str:
    return hashlib.sha256(domain + canonical_json_bytes(value)).hexdigest()


def _key(value: object) -> str:
    if type(value) is not str or not value.strip() or len(value) > 200:
        raise SourceAuthoringOperationError("invalid_key")
    try:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()
    except UnicodeError:
        raise SourceAuthoringOperationError("invalid_key") from None


class ProcessLocalSourceAuthoringOperations:
    """One synthetic grant across all POCs and sessions, with no reset API."""

    def __init__(
        self,
        *,
        owners: SourceAuthoringOwners,
        monotonic: Callable[[], float] = time.monotonic,
        schedule: Callable[[str], None] | None = None,
    ) -> None:
        if type(owners) is not SourceAuthoringOwners:
            raise SourceAuthoringOperationError("owner_required")
        self._owners = owners
        self._clock = monotonic
        # Scheduling seam belongs exclusively to this synthetic composition.
        self._schedule = schedule or (lambda _: None)
        self._policy = synthetic_policy()
        self._epoch = secrets.token_hex(32)
        self._grant = secrets.token_hex(32)
        self._lock = RLock()
        self._sessions: dict[str, SyntheticBrowserSession] = {}
        self._session_secrets: dict[str, bytes] = {}
        self._records: dict[str, _Record] = {}
        self._records_generation = 0
        self._aliases: dict[tuple[str, str], str] = {}
        self._claims = 0
        self._last_claim: float | None = None
        self._active: str | None = None
        self._worker: SyntheticSourceAuthoringSupervisor | None = None
        self._closed = False
        self._consent_generation = 0
        self._now()

    def __repr__(self) -> str:
        return "<ProcessLocalSourceAuthoringOperations synthetic-only>"

    def _now(self) -> float:
        failed = False
        try:
            value = self._clock()
            if type(value) not in {int, float} or not math.isfinite(value) or value < 0:
                failed = True
        except Exception:  # noqa: BLE001 - a clock seam cannot leak private errors
            failed = True
        if failed:
            raise SourceAuthoringOperationError("invalid_clock") from None
        return float(value)

    def _session(self, session: SyntheticBrowserSession) -> str:
        if self._closed:
            raise SourceAuthoringOperationError("grant_closed")
        if type(session) is not SyntheticBrowserSession:
            raise SourceAuthoringOperationError("session_refused")
        identity = getattr(session, "_identity", None)
        secret = getattr(session, "_secret", None)
        if type(identity) is not str or type(secret) is not bytes:
            raise SourceAuthoringOperationError("session_refused")
        stored = self._sessions.get(identity)
        if stored is not session or not hmac.compare_digest(
            self._session_secrets.get(identity, b""), secret
        ):
            raise SourceAuthoringOperationError("session_refused")
        return session._identity

    def new_synthetic_session(self) -> SyntheticBrowserSession:
        """Test/local core seam, not an HTTP bootstrap or a live grant issuer."""
        with self._lock:
            if self._closed or len(self._sessions) >= 16:
                raise SourceAuthoringOperationError("session_capacity")
            session = SyntheticBrowserSession(_ISSUER)
            self._sessions[session._identity] = session
            self._session_secrets[session._identity] = session._secret
            return session

    @property
    def ledger(self) -> tuple[int, Decimal]:
        with self._lock:
            return self._claims, Decimal(self._claims) * Decimal("0.01")

    def prepare(
        self, session: SyntheticBrowserSession, poc_id: str, source_receipt_id: str
    ) -> SourceAuthoringDisclosure:
        with self._lock:
            identity = self._session(session)
        snapshot = self._owners.capture(poc_id, source_receipt_id)
        body = build_body(snapshot.source, policy=self._policy)
        operation = secrets.token_hex(32)

        def publish_disclosure(guard):
            with self._lock:
                self._session(session)
                now = self._now()
                for record in self._records.values():
                    if (
                        record.session == identity
                        and record.source is not None
                        and record.source.source.poc_id == poc_id
                        and record.source.source.source_id == snapshot.source.source_id
                        and record.receipt.state not in _TERMINAL
                    ):
                        if now < record.disclosure.expires_monotonic:
                            return record.disclosure
                        self._terminal(
                            record.disclosure.operation_id, "EXPIRED", "consent_expired"
                        )
                        break
                if len(self._records) >= 1024:
                    raise SourceAuthoringOperationError("operation_capacity")
                disclosure = SourceAuthoringDisclosure(
                    operation,
                    _digest(
                        b"exitspec-source-authoring-disclosure-v1\0",
                        {
                            "operation": operation,
                            "epoch": self._epoch,
                            "grant": self._grant,
                            "session": identity,
                            "source": snapshot.source.content_sha256,
                            "body": hashlib.sha256(body).hexdigest(),
                            "issued": now,
                            "expires": now + 300,
                            "classification": "OWNER_APPROVED_REDACTED_BUSINESS_TEXT",
                        },
                    ),
                    snapshot.source.content_sha256,
                    now + 300,
                )
                self._records[operation] = _Record(
                    identity,
                    disclosure,
                    snapshot,
                    body,
                    now,
                    time.time(),
                    SourceAuthoringOperationReceipt(operation, "PREPARED"),
                )
                self._records_generation += 1
                return disclosure

        return self._owners.run_guarded(snapshot, publish_disclosure)

    def authorize(
        self,
        session: SyntheticBrowserSession,
        disclosure: SourceAuthoringDisclosure,
        *,
        acknowledged: bool,
        idempotency_key: str,
    ) -> AuthorizedSourceAuthoringRequest:
        key = _key(idempotency_key)
        if (
            acknowledged is not True
            or type(disclosure) is not SourceAuthoringDisclosure
        ):
            raise SourceAuthoringOperationError("acknowledgement_required")
        with self._lock:
            identity = self._session(session)
            record = self._records.get(disclosure.operation_id)
            if (
                record is None
                or record.session != identity
                or record.disclosure != disclosure
            ):
                raise SourceAuthoringOperationError("disclosure_refused")
            previous = self._aliases.get((identity, key))
            if previous is not None and previous != disclosure.operation_id:
                raise SourceAuthoringOperationError("idempotency_conflict")
            if record.permit is not None:
                self._add_alias(identity, key, disclosure.operation_id)
                return record.permit
            if (
                record.receipt.state in _TERMINAL
                or self._now() >= disclosure.expires_monotonic
            ):
                self._terminal(disclosure.operation_id, "EXPIRED", "consent_expired")
                raise SourceAuthoringOperationError("consent_expired")
            snapshot = record.source
        assert snapshot is not None

        def issue(guard):
            with self._lock:
                self._session(session)
                current = self._records[disclosure.operation_id]
                if current.permit is not None:
                    self._add_alias(identity, key, disclosure.operation_id)
                    return current.permit
                if (
                    current.receipt.state != "PREPARED"
                    or self._now() >= disclosure.expires_monotonic
                ):
                    raise SourceAuthoringOperationError("disclosure_refused")
                intent = self._make_intent(current, key)
                self._check_pending(current, "PREPARED")
                guard.check_current_locked()
                permit = AuthorizedSourceAuthoringRequest(
                    _ISSUER, disclosure.operation_id
                )
                self._add_alias(identity, key, disclosure.operation_id)
                self._records[disclosure.operation_id] = replace(
                    current,
                    intent=intent,
                    permit=permit,
                    receipt=replace(
                        current.receipt, state="AUTHORIZED", intent_sha256=intent.digest
                    ),
                )
                self._records_generation += 1
                return permit

        return self._owners.run_guarded(snapshot, issue)

    def _add_alias(self, identity: str, key: str, operation: str) -> None:
        previous = self._aliases.get((identity, key))
        if previous is not None and previous != operation:
            raise SourceAuthoringOperationError("idempotency_conflict")
        if previous is None and len(self._aliases) >= 16384:
            raise SourceAuthoringOperationError("alias_capacity")
        self._aliases[identity, key] = operation

    def _make_intent(self, record: _Record, key: str):
        # The strict policy factory revalidates all metadata, not just a digest.
        assert record.source is not None and record.body is not None
        return build_intent(
            record.source.source,
            body=record.body,
            policy=self._policy,
            server_epoch=self._epoch,
            launch_grant_id=self._grant,
            browser_session_id=record.session,
            consent_generation=self._consent_generation,
            operation_id=record.disclosure.operation_id,
            draft_generation=hashlib.sha256(
                canonical_json_bytes(record.source.draft.model_dump(mode="json"))
            ).hexdigest(),
            source_receipt_id="srcpt_"
            + record.source.source.source_id.removeprefix("src_"),
            issued_monotonic=record.issued,
            expires_monotonic=record.disclosure.expires_monotonic,
            issued_at=record.issued_at,
            expires_at=record.issued_at + 300.0,
            acknowledged_at=record.issued_at + (self._now() - record.issued),
            acknowledged=True,
            disclosure_digest=record.disclosure.disclosure_sha256,
            idempotency_id=key,
            credential_configuration_generation=0,
            synthetic_input_tokens=8192,
            redaction_configuration_digest=REDACTION_CONFIGURATION_DIGEST,
            content_classification="OWNER_APPROVED_REDACTED_BUSINESS_TEXT",
        )

    def _record(
        self, session: SyntheticBrowserSession, permit: AuthorizedSourceAuthoringRequest
    ) -> _Record:
        identity = self._session(session)
        if type(permit) is not AuthorizedSourceAuthoringRequest:
            raise SourceAuthoringOperationError("permit_refused")
        operation = getattr(permit, "_operation", None)
        if type(operation) is not str:
            raise SourceAuthoringOperationError("permit_refused")
        record = self._records.get(operation)
        if record is None or record.permit is not permit or record.session != identity:
            raise SourceAuthoringOperationError("permit_refused")
        return record

    def status(
        self, session: SyntheticBrowserSession, permit: AuthorizedSourceAuthoringRequest
    ) -> SourceAuthoringOperationReceipt:
        with self._lock:
            return self._record(session, permit).receipt

    def _terminal(self, operation: str, state: str, code: str | None) -> None:
        record = self._records[operation]
        if record.receipt.state in _TERMINAL:
            return
        self._records[operation] = replace(
            record,
            source=None,
            body=None,
            receipt=replace(record.receipt, state=state, code=code),
        )
        self._records_generation += 1

    def revoke_disclosure(
        self, session: SyntheticBrowserSession, disclosure: SourceAuthoringDisclosure
    ) -> SourceAuthoringOperationReceipt:
        with self._lock:
            identity = self._session(session)
            if type(disclosure) is not SourceAuthoringDisclosure:
                raise SourceAuthoringOperationError("disclosure_refused")
            record = self._records.get(disclosure.operation_id)
            if (
                record is None
                or record.session != identity
                or record.disclosure != disclosure
            ):
                raise SourceAuthoringOperationError("disclosure_refused")
            self._terminal(disclosure.operation_id, "REVOKED", "consent_revoked")
            worker = self._worker if self._active == disclosure.operation_id else None
            receipt = self._records[disclosure.operation_id].receipt
        if worker is not None:
            worker.cancel()
        return receipt

    def revoke(
        self, session: SyntheticBrowserSession, permit: AuthorizedSourceAuthoringRequest
    ) -> SourceAuthoringOperationReceipt:
        with self._lock:
            self._record(session, permit)
            self._terminal(permit._operation, "REVOKED", "consent_revoked")
            worker = self._worker if self._active == permit._operation else None
            receipt = self._records[permit._operation].receipt
        if worker is not None:
            worker.cancel()
        return receipt

    def shutdown(self) -> None:
        with self._lock:
            self._closed = True
            self._consent_generation += 1
            for operation in self._records:
                self._terminal(operation, "REVOKED", "grant_closed")
            self._sessions.clear()
            self._session_secrets.clear()
            worker = self._worker
        if worker is not None:
            worker.cancel()

    def execute_synthetic(
        self,
        session: SyntheticBrowserSession,
        permit: AuthorizedSourceAuthoringRequest,
        *,
        worker: SyntheticSourceAuthoringSupervisor,
    ) -> SourceAuthoringOperationReceipt:
        if type(worker) is not SyntheticSourceAuthoringSupervisor:
            raise SourceAuthoringOperationError("synthetic_worker_required")
        self._schedule("pre_claim")
        with self._lock:
            record = self._record(session, permit)
            operation = permit._operation
            if record.receipt.state != "AUTHORIZED":
                return record.receipt
            now = self._now()
            # The clock seam may revoke this permit, close the grant, or
            # invalidate the session while the RLock is held. Revalidate before
            # preparing or consuming a claim; terminal tombstones stay intact.
            record = self._record(session, permit)
            if record.receipt.state != "AUTHORIZED":
                return record.receipt
            if now >= record.disclosure.expires_monotonic:
                self._terminal(operation, "EXPIRED", "consent_expired")
                return self._records[operation].receipt
            if self._active is not None:
                raise SourceAuthoringOperationError("worker_busy")
            if self._claims >= 10:
                raise SourceAuthoringOperationError("budget_exhausted")
            if self._last_claim is not None and now - self._last_claim < 10:
                raise SourceAuthoringOperationError("rate_limited")
            claimed = replace(
                record,
                deadline=min(now + 30, record.disclosure.expires_monotonic),
                receipt=replace(
                    record.receipt,
                    state="CLAIMED",
                    attempts=1,
                    reserved_usd=Decimal("0.01"),
                ),
            )
            next_claims = self._claims + 1
            next_generation = self._records_generation + 1
            current = self._record(session, permit)
            if current is not record:
                return current.receipt
            self._records[operation] = claimed
            self._records_generation = next_generation
            self._claims = next_claims
            self._last_claim = now
            self._active, self._worker = operation, worker
        try:
            self._schedule("claimed")
            with self._lock:
                self._check_pending(self._records[operation], "CLAIMED")
            assert (
                claimed.source is not None
                and claimed.body is not None
                and claimed.deadline is not None
            )
            binding = worker.prepare(
                {
                    "epoch": self._epoch,
                    "grant": self._grant,
                    "operation": operation,
                    "body_sha256": claimed.intent.body_sha256,
                    "profile_sha256": claimed.intent.policy.profile_sha256,
                },
                deadline=time.monotonic() + max(0.001, claimed.deadline - self._now()),
            )
            ticket = worker.prepare_ticket(binding, claimed.body)
            self._schedule("pre_dispatch")

            def dispatch(guard):
                with self._lock:
                    current = self._record(session, permit)
                    self._check_pending(current, "CLAIMED")
                    if current.intent.digest != current.receipt.intent_sha256:
                        raise SourceAuthoringPolicyError("intent_mismatch")
                    validate_intent(
                        current.intent,
                        claimed.source.source,
                        claimed.body,
                        self._policy,
                    )
                    dispatched = replace(
                        current,
                        receipt=replace(current.receipt, state="DISPATCH_AUTHORIZED"),
                    )
                    with worker.dispatch_guard(ticket) as handoff:
                        self._check_pending(current, "CLAIMED")
                        next_generation = self._records_generation + 1
                        # D: prepared immutable state/ticket publication, no I/O.
                        guard.check_current_locked()
                        handoff.stage()
                        self._records[operation] = dispatched
                        self._records_generation = next_generation

            self._owners.run_guarded(claimed.source, dispatch)
            self._schedule("post_dispatch")
            worker.handoff()
            response = worker.collect()
            batch = validate_output(response, claimed.source.source)
            self._schedule("pre_commit")

            def commit(guard):
                with self._lock:
                    current = self._record(session, permit)
                    self._check_pending(current, "DISPATCH_AUTHORIZED")
                    if current.intent.digest != current.receipt.intent_sha256:
                        raise SourceAuthoringPolicyError("intent_mismatch")
                    validate_intent(
                        current.intent,
                        claimed.source.source,
                        claimed.body,
                        self._policy,
                    )
                    publication = guard.prepare(
                        batch,
                        request_sha256=current.receipt.intent_sha256,
                        provider="synthetic-source-authoring",
                        model=self._policy.model,
                        endpoint="local://exitspec/source-authoring-synthetic",
                        generated_at=datetime.now(UTC),
                    )
                    completed = replace(
                        current,
                        source=None,
                        body=None,
                        receipt=replace(
                            current.receipt,
                            state="SUCCEEDED",
                            authoring_receipt_id=publication.result.receipt.authoring_receipt_id,
                        ),
                    )
                    records_generation = self._records_generation
                    next_generation = records_generation + 1
                    updated = dict(self._records)
                    updated[operation] = completed
                    self._check_pending(current, "DISPATCH_AUTHORIZED")
                    # A callback may mutate another operation even when this
                    # one remains valid. Reject the stale whole-map copy before
                    # publishing any owner state, preserving every tombstone.
                    if self._records_generation != records_generation:
                        raise SourceAuthoringOperationError("publication_conflict")
                    # F: only concrete owner pointer swaps and our prepared map.
                    guard.check_current_locked()
                    publication.publish()
                    self._records = updated
                    self._records_generation = next_generation

            self._owners.run_guarded(claimed.source, commit)
            self._schedule("committed")
        except Exception as error:  # noqa: BLE001 - sanitize every private execution boundary
            with self._lock:
                state = self._records[operation].receipt.state
                if state not in _TERMINAL:
                    code = (
                        error.code
                        if type(error) is SourceAuthoringOperationError
                        else "execution_failed"
                    )
                    # Never reflect arbitrary adapter text or exception payloads.
                    if isinstance(error, SourceAuthoringOperationError) and code in {
                        "consent_expired",
                        "operation_timeout",
                    }:
                        terminal = (
                            "EXPIRED"
                            if code == "consent_expired"
                            else "OUTCOME_UNKNOWN"
                        )
                    elif isinstance(error, SourceAuthoringPolicyError):
                        terminal, code = "FAILED", "invalid_output_or_binding"
                    elif isinstance(error, SourceAuthoringOwnersError):
                        terminal, code = "STALE", "owner_refused"
                    elif isinstance(error, SourceAuthoringWorkerError):
                        terminal = (
                            "OUTCOME_UNKNOWN"
                            if state == "DISPATCH_AUTHORIZED"
                            else "FAILED"
                        )
                        code = "worker_refused"
                    else:
                        terminal, code = "FAILED", "execution_failed"
                    self._terminal(operation, terminal, code)
        finally:
            worker.cancel()
            worker.reap(timeout=1.0)
            occupied = worker.slot_occupied
            with self._lock:
                if not occupied:
                    self._active, self._worker = None, None
                else:
                    self._closed = True
        with self._lock:
            return self._records[operation].receipt

    def _check_pending(self, record: _Record, expected: str) -> None:
        now = self._now()
        # A synthetic clock can reenter the owner. Re-read after that last
        # callback so revocation/shutdown cannot leave a stale local record.
        if (
            self._closed
            or record.receipt.state != expected
            or self._records.get(record.disclosure.operation_id) is not record
        ):
            raise SourceAuthoringOperationError("operation_invalidated")
        if now >= record.disclosure.expires_monotonic:
            raise SourceAuthoringOperationError("consent_expired")
        if record.deadline is not None and now >= record.deadline:
            raise SourceAuthoringOperationError("operation_timeout")


def create_live_source_authoring_operations(**kwargs):
    """No owner-approved live profile exists in this synthetic checkpoint."""
    raise SourceAuthoringOperationError("live_prerequisites_missing")
