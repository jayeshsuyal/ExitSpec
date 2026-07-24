"""Digest-only persistence boundary for customer confirmation records."""

from __future__ import annotations

import hmac
import re
from dataclasses import dataclass, field
from datetime import datetime
from threading import RLock
from typing import Protocol, runtime_checkable

from .confirmations import ConfirmationDecision


_SHA256_DIGEST = re.compile(r"^[a-f0-9]{64}$")
_CONFIRMATION_ID = re.compile(r"^cnf_[a-f0-9]{64}$")
_ContractVersion = tuple[str, str]
_OperationKey = tuple[str, str, "IdempotencyKeyDigest"]


def _require_non_empty(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("{0} must be non-empty.".format(field_name))


def _require_digest(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not _SHA256_DIGEST.fullmatch(value):
        raise ValueError(
            "{0} must be a lowercase SHA-256 digest.".format(field_name)
        )


def _require_aware(timestamp: datetime, field_name: str) -> None:
    if not isinstance(timestamp, datetime) or timestamp.tzinfo is None:
        raise ValueError("{0} must be timezone-aware.".format(field_name))


def _validate_decision_payload(
    *,
    decision: object,
    agreement_acknowledged: object,
    rationale: object,
) -> None:
    if not isinstance(decision, ConfirmationDecision):
        raise ValueError("decision must be a ConfirmationDecision.")
    if not isinstance(agreement_acknowledged, bool):
        raise ValueError("agreement_acknowledged must be a boolean.")
    if not isinstance(rationale, str):
        raise ValueError("rationale must be a string.")
    if len(rationale) > 2000:
        raise ValueError("rationale must contain at most 2000 characters.")
    if (
        decision == ConfirmationDecision.CONFIRM
        and not agreement_acknowledged
    ):
        raise ValueError("CONFIRM requires explicit agreement acknowledgement.")
    if (
        decision == ConfirmationDecision.REQUEST_CHANGES
        and not rationale.strip()
    ):
        raise ValueError("REQUEST_CHANGES requires a non-empty rationale.")


def _digests_equal(left: TokenDigest, right: TokenDigest) -> bool:
    return hmac.compare_digest(left.value, right.value)


@dataclass(frozen=True, slots=True)
class TokenDigest:
    """A precomputed review-token digest; the raw capability never enters here."""

    value: str = field(repr=False)

    def __post_init__(self) -> None:
        _require_digest(self.value, "token digest")


@dataclass(frozen=True, slots=True)
class OperationDigest:
    """A domain-separated digest identifying one idempotent operation."""

    value: str = field(repr=False)

    def __post_init__(self) -> None:
        _require_digest(self.value, "operation digest")


@dataclass(frozen=True, slots=True)
class IdempotencyKeyDigest:
    """A domain-separated digest of an idempotency key, never the raw key."""

    value: str = field(repr=False)

    def __post_init__(self) -> None:
        _require_digest(self.value, "idempotency-key digest")


@dataclass(frozen=True, slots=True)
class RequestDigest:
    """A canonical digest over every decision-making request input."""

    value: str = field(repr=False)

    def __post_init__(self) -> None:
        _require_digest(self.value, "request digest")


@dataclass(frozen=True, slots=True)
class ContractBinding:
    """The exact contract identity that an invitation and decision bind."""

    contract_id: str
    contract_version: str
    confirmation_fingerprint: str

    def __post_init__(self) -> None:
        _require_non_empty(self.contract_id, "contract_id")
        _require_non_empty(self.contract_version, "contract_version")
        _require_digest(
            self.confirmation_fingerprint,
            "confirmation_fingerprint",
        )


@dataclass(frozen=True, slots=True)
class ReviewInvitationRecord:
    """Immutable, digest-only invitation record stored by an adapter."""

    invitation_id: str
    binding: ContractBinding
    token_digest: TokenDigest
    token_digest_version: str
    intended_organization_id: str
    issued_by_subject: str
    issued_at: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        _require_non_empty(self.invitation_id, "invitation_id")
        if not isinstance(self.binding, ContractBinding):
            raise TypeError("binding must be a ContractBinding.")
        if not isinstance(self.token_digest, TokenDigest):
            raise TypeError("token_digest must be a TokenDigest.")
        _require_non_empty(self.token_digest_version, "token_digest_version")
        _require_non_empty(
            self.intended_organization_id,
            "intended_organization_id",
        )
        _require_non_empty(self.issued_by_subject, "issued_by_subject")
        _require_aware(self.issued_at, "issued_at")
        _require_aware(self.expires_at, "expires_at")
        if self.expires_at <= self.issued_at:
            raise ValueError("expires_at must be later than issued_at.")


@dataclass(frozen=True, slots=True)
class RecordDecision:
    """Digest-only command for one terminal customer decision attempt.

    ``decided_at`` is transaction time supplied by the trusted injected clock,
    not reviewer-controlled request data.
    """

    operation_digest: OperationDigest
    idempotency_key_digest: IdempotencyKeyDigest
    request_digest: RequestDigest
    token_digest: TokenDigest
    confirmation_id: str
    invitation_id: str
    binding: ContractBinding
    reviewer_issuer: str
    reviewer_subject: str
    reviewer_organization_id: str
    reviewer_display_name_snapshot: str
    decision: ConfirmationDecision
    agreement_acknowledged: bool
    rationale: str
    decided_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.operation_digest, OperationDigest):
            raise TypeError("operation_digest must be an OperationDigest.")
        if not isinstance(self.idempotency_key_digest, IdempotencyKeyDigest):
            raise TypeError(
                "idempotency_key_digest must be an IdempotencyKeyDigest."
            )
        if not isinstance(self.request_digest, RequestDigest):
            raise TypeError("request_digest must be a RequestDigest.")
        if not isinstance(self.token_digest, TokenDigest):
            raise TypeError("token_digest must be a TokenDigest.")
        if not isinstance(self.confirmation_id, str) or not _CONFIRMATION_ID.fullmatch(
            self.confirmation_id
        ):
            raise ValueError("confirmation_id must be a valid confirmation digest.")
        _require_non_empty(self.invitation_id, "invitation_id")
        if not isinstance(self.binding, ContractBinding):
            raise TypeError("binding must be a ContractBinding.")
        _require_non_empty(self.reviewer_issuer, "reviewer_issuer")
        _require_non_empty(self.reviewer_subject, "reviewer_subject")
        _require_non_empty(
            self.reviewer_organization_id,
            "reviewer_organization_id",
        )
        _require_non_empty(
            self.reviewer_display_name_snapshot,
            "reviewer_display_name_snapshot",
        )
        _require_aware(self.decided_at, "decided_at")


@dataclass(frozen=True, slots=True)
class ConfirmationDecisionRecord:
    """Immutable terminal decision without a raw idempotency key."""

    confirmation_id: str
    invitation_id: str
    binding: ContractBinding
    reviewer_issuer: str
    reviewer_subject: str
    reviewer_organization_id: str
    reviewer_display_name_snapshot: str
    decision: ConfirmationDecision
    agreement_acknowledged: bool
    rationale: str
    decided_at: datetime
    request_digest: RequestDigest

    def __post_init__(self) -> None:
        if not isinstance(self.confirmation_id, str) or not _CONFIRMATION_ID.fullmatch(
            self.confirmation_id
        ):
            raise ValueError("confirmation_id must be a valid confirmation digest.")
        _require_non_empty(self.invitation_id, "invitation_id")
        if not isinstance(self.binding, ContractBinding):
            raise TypeError("binding must be a ContractBinding.")
        _require_non_empty(self.reviewer_issuer, "reviewer_issuer")
        _require_non_empty(self.reviewer_subject, "reviewer_subject")
        _require_non_empty(
            self.reviewer_organization_id,
            "reviewer_organization_id",
        )
        _require_non_empty(
            self.reviewer_display_name_snapshot,
            "reviewer_display_name_snapshot",
        )
        _validate_decision_payload(
            decision=self.decision,
            agreement_acknowledged=self.agreement_acknowledged,
            rationale=self.rationale,
        )
        _require_aware(self.decided_at, "decided_at")
        if not isinstance(self.request_digest, RequestDigest):
            raise TypeError("request_digest must be a RequestDigest.")


@dataclass(frozen=True, slots=True)
class IdempotencyOperationRecord:
    """Immutable receipt connecting digest-only operation data to a decision."""

    operation_digest: OperationDigest
    contract_id: str
    contract_version: str
    idempotency_key_digest: IdempotencyKeyDigest
    request_digest: RequestDigest
    confirmation_id: str
    created_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.operation_digest, OperationDigest):
            raise TypeError("operation_digest must be an OperationDigest.")
        _require_non_empty(self.contract_id, "contract_id")
        _require_non_empty(self.contract_version, "contract_version")
        if not isinstance(self.idempotency_key_digest, IdempotencyKeyDigest):
            raise TypeError(
                "idempotency_key_digest must be an IdempotencyKeyDigest."
            )
        if not isinstance(self.request_digest, RequestDigest):
            raise TypeError("request_digest must be a RequestDigest.")
        if not isinstance(self.confirmation_id, str) or not _CONFIRMATION_ID.fullmatch(
            self.confirmation_id
        ):
            raise ValueError("confirmation_id must be a valid confirmation digest.")
        _require_aware(self.created_at, "created_at")


@dataclass(frozen=True, slots=True)
class DecisionWriteResult:
    """The original terminal record and whether this write was a replay."""

    decision: ConfirmationDecisionRecord
    replayed: bool


class ConfirmationStoreError(RuntimeError):
    """Base error for a write that violates a store invariant."""


class InvitationIdentityConflict(ConfirmationStoreError):
    """An invitation identity is already bound to different content."""


class TokenDigestConflict(ConfirmationStoreError):
    """A token digest is already bound to another invitation."""


class InvitationNotFound(ConfirmationStoreError):
    """The decision references an invitation that is not stored."""


class InvitationExpired(ConfirmationStoreError):
    """A recognized review invitation is no longer active."""


class InvitationConsumed(ConfirmationStoreError):
    """A recognized review invitation already produced a terminal decision."""


class ContractBindingMismatch(ConfirmationStoreError):
    """A contract ID and version is bound to a different fingerprint."""


class IdempotencyConflict(ConfirmationStoreError):
    """An operation or idempotency digest was reused for another request."""


class DecisionAlreadyRecorded(ConfirmationStoreError):
    """A different operation attempted to replace a terminal decision."""


@runtime_checkable
class ConfirmationStore(Protocol):
    """Minimal storage port for digest-only invitations and decisions."""

    def issue_invitation(
        self,
        invitation: ReviewInvitationRecord,
    ) -> ReviewInvitationRecord:
        """Store an invitation or replay the exact immutable record."""

    def get_invitation(
        self,
        invitation_id: str,
    ) -> ReviewInvitationRecord | None:
        """Return an invitation by identity, when present."""

    def resolve_invitation(
        self,
        token_digest: TokenDigest,
        now: datetime,
    ) -> ReviewInvitationRecord | None:
        """Resolve an active invitation using only a precomputed digest."""

    def record_decision(self, command: RecordDecision) -> DecisionWriteResult:
        """Atomically record, replay, or reject a terminal decision."""

    def get_decision(
        self,
        binding: ContractBinding,
    ) -> ConfirmationDecisionRecord | None:
        """Return the terminal decision for an exact contract binding."""


class InMemoryConfirmationStore:
    """Thread-safe reference adapter with the durable port's write semantics."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._invitations: dict[str, ReviewInvitationRecord] = {}
        self._invitation_ids_by_token_digest: dict[TokenDigest, str] = {}
        self._bindings_by_contract_version: dict[
            _ContractVersion,
            ContractBinding,
        ] = {}
        self._decisions_by_binding: dict[
            ContractBinding,
            ConfirmationDecisionRecord,
        ] = {}
        self._decisions_by_id: dict[str, ConfirmationDecisionRecord] = {}
        self._operations_by_digest: dict[
            OperationDigest,
            IdempotencyOperationRecord,
        ] = {}
        self._operation_digests_by_key: dict[
            _OperationKey,
            OperationDigest,
        ] = {}

    def issue_invitation(
        self,
        invitation: ReviewInvitationRecord,
    ) -> ReviewInvitationRecord:
        if not isinstance(invitation, ReviewInvitationRecord):
            raise TypeError("invitation must be a ReviewInvitationRecord.")
        with self._lock:
            existing = self._invitations.get(invitation.invitation_id)
            if existing is not None:
                if existing == invitation:
                    return existing
                raise InvitationIdentityConflict(
                    "Invitation identity is already bound to different content."
                )

            digest_owner = self._invitation_ids_by_token_digest.get(
                invitation.token_digest
            )
            if digest_owner is not None:
                raise TokenDigestConflict(
                    "Token digest is already bound to another invitation."
                )

            self._require_consistent_binding(invitation.binding)
            self._invitations[invitation.invitation_id] = invitation
            self._invitation_ids_by_token_digest[invitation.token_digest] = (
                invitation.invitation_id
            )
            self._remember_binding(invitation.binding)
            return invitation

    def get_invitation(
        self,
        invitation_id: str,
    ) -> ReviewInvitationRecord | None:
        with self._lock:
            return self._invitations.get(invitation_id)

    def resolve_invitation(
        self,
        token_digest: TokenDigest,
        now: datetime,
    ) -> ReviewInvitationRecord | None:
        if not isinstance(token_digest, TokenDigest):
            raise TypeError("token_digest must be a TokenDigest, never a raw token.")
        _require_aware(now, "now")
        with self._lock:
            invitation = self._invitation_for_digest(token_digest)
            if invitation is None:
                return None
            if invitation.binding in self._decisions_by_binding:
                raise InvitationConsumed("Review capability is no longer active.")
            self._require_not_expired(invitation, now)
            return invitation

    def record_decision(self, command: RecordDecision) -> DecisionWriteResult:
        if not isinstance(command, RecordDecision):
            raise TypeError("command must be a digest-only RecordDecision.")
        operation_key = self._operation_key(command)

        with self._lock:
            existing_operation = self._operations_by_digest.get(
                command.operation_digest
            )
            if existing_operation is not None:
                return self._replay_or_conflict(command, existing_operation)

            operation_owner = self._operation_digests_by_key.get(operation_key)
            if operation_owner is not None:
                raise IdempotencyConflict(
                    "Idempotency digest is already bound to another operation."
                )

            invitation = self._invitation_for_digest(command.token_digest)
            if (
                invitation is None
                or invitation.invitation_id != command.invitation_id
            ):
                raise InvitationNotFound("Review capability is unavailable.")
            self._require_not_expired(invitation, command.decided_at)
            self._require_consistent_binding(command.binding)
            if invitation.binding != command.binding:
                raise ContractBindingMismatch(
                    "Invitation and decision contract bindings do not match."
                )
            if (
                invitation.intended_organization_id
                != command.reviewer_organization_id
            ):
                raise ContractBindingMismatch(
                    "Reviewer organization does not match the invitation."
                )

            if command.binding in self._decisions_by_binding:
                raise DecisionAlreadyRecorded(
                    "A terminal decision already exists for this contract binding."
                )
            if command.confirmation_id in self._decisions_by_id:
                raise DecisionAlreadyRecorded(
                    "Confirmation identity already names a terminal decision."
                )

            self._validate_first_write(command)
            decision = self._decision_from_command(command)
            operation = IdempotencyOperationRecord(
                operation_digest=command.operation_digest,
                contract_id=command.binding.contract_id,
                contract_version=command.binding.contract_version,
                idempotency_key_digest=command.idempotency_key_digest,
                request_digest=command.request_digest,
                confirmation_id=command.confirmation_id,
                created_at=command.decided_at,
            )
            self._decisions_by_binding[command.binding] = decision
            self._decisions_by_id[decision.confirmation_id] = decision
            self._operations_by_digest[command.operation_digest] = operation
            self._operation_digests_by_key[operation_key] = (
                command.operation_digest
            )
            self._remember_binding(command.binding)
            return DecisionWriteResult(decision=decision, replayed=False)

    def get_decision(
        self,
        binding: ContractBinding,
    ) -> ConfirmationDecisionRecord | None:
        if not isinstance(binding, ContractBinding):
            raise TypeError("binding must be a ContractBinding.")
        with self._lock:
            return self._decisions_by_binding.get(binding)

    def _replay_or_conflict(
        self,
        command: RecordDecision,
        operation: IdempotencyOperationRecord,
    ) -> DecisionWriteResult:
        expected_operation = (
            operation.contract_id == command.binding.contract_id
            and operation.contract_version == command.binding.contract_version
            and operation.idempotency_key_digest
            == command.idempotency_key_digest
            and operation.request_digest == command.request_digest
        )
        decision = self._decisions_by_id[operation.confirmation_id]
        invitation = self._invitations[decision.invitation_id]
        if (
            not expected_operation
            or not _digests_equal(invitation.token_digest, command.token_digest)
            or not self._same_request(decision, command)
        ):
            raise IdempotencyConflict(
                "Operation digest is already bound to a different request."
            )
        return DecisionWriteResult(decision=decision, replayed=True)

    def _invitation_for_digest(
        self,
        token_digest: TokenDigest,
    ) -> ReviewInvitationRecord | None:
        matched = None
        for invitation in self._invitations.values():
            if _digests_equal(invitation.token_digest, token_digest):
                matched = invitation
        return matched

    @staticmethod
    def _require_not_expired(
        invitation: ReviewInvitationRecord,
        now: datetime,
    ) -> None:
        if now >= invitation.expires_at:
            raise InvitationExpired("Review capability is no longer active.")

    @staticmethod
    def _validate_first_write(command: RecordDecision) -> None:
        _validate_decision_payload(
            decision=command.decision,
            agreement_acknowledged=command.agreement_acknowledged,
            rationale=command.rationale,
        )

    def _require_consistent_binding(self, binding: ContractBinding) -> None:
        version = (binding.contract_id, binding.contract_version)
        existing = self._bindings_by_contract_version.get(version)
        if existing is not None and existing != binding:
            raise ContractBindingMismatch(
                "Contract ID and version is already bound to another fingerprint."
            )

    def _remember_binding(self, binding: ContractBinding) -> None:
        self._bindings_by_contract_version[
            (binding.contract_id, binding.contract_version)
        ] = binding

    @staticmethod
    def _operation_key(command: RecordDecision) -> _OperationKey:
        return (
            command.binding.contract_id,
            command.binding.contract_version,
            command.idempotency_key_digest,
        )

    @staticmethod
    def _decision_from_command(
        command: RecordDecision,
    ) -> ConfirmationDecisionRecord:
        return ConfirmationDecisionRecord(
            confirmation_id=command.confirmation_id,
            invitation_id=command.invitation_id,
            binding=command.binding,
            reviewer_issuer=command.reviewer_issuer,
            reviewer_subject=command.reviewer_subject,
            reviewer_organization_id=command.reviewer_organization_id,
            reviewer_display_name_snapshot=(
                command.reviewer_display_name_snapshot
            ),
            decision=command.decision,
            agreement_acknowledged=command.agreement_acknowledged,
            rationale=command.rationale,
            decided_at=command.decided_at,
            request_digest=command.request_digest,
        )

    @staticmethod
    def _same_request(
        decision: ConfirmationDecisionRecord,
        command: RecordDecision,
    ) -> bool:
        return (
            decision.invitation_id == command.invitation_id
            and decision.binding == command.binding
            and decision.reviewer_issuer == command.reviewer_issuer
            and decision.reviewer_subject == command.reviewer_subject
            and decision.reviewer_organization_id
            == command.reviewer_organization_id
            and decision.decision == command.decision
            and decision.agreement_acknowledged
            == command.agreement_acknowledged
            and decision.rationale == command.rationale
            and decision.request_digest == command.request_digest
        )
