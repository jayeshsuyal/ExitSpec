"""Process-local lifecycle for one runner-valid performance agreement.

The service composes the pure performance-contract assembler with ExitSpec's
existing customer-confirmation and confirmation-aware freeze primitives.  It
does not execute the POC, generate evidence, or issue a verdict.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import hmac
import secrets
from threading import RLock
from typing import Any, Callable, Sequence
import unicodedata

from .canonical import canonical_json_bytes
from .confirmations import (
    ConfirmationDecision,
    ContractConfirmation,
    contract_confirmation_fingerprint,
    record_confirmation,
)
from .contracts import freeze_confirmed_contract
from .customer_review import build_customer_review_payload
from .models import ContractStatus, POCContract
from .poc_contract_definition import ContractDefinitionReceipt
from .poc_creation import DraftPOCSnapshot
from .poc_performance_contract import (
    PerformanceTargetInput,
    PreparedPerformanceBundle,
    prepare_performance_bundle,
)
from .poc_proposal_review import ProposalReviewItem
from .review_links import (
    CustomerReviewInvitation,
    ReviewInvitationError,
    issue_customer_review_invitation,
)


MAX_REVIEWER_LENGTH = 160
MAX_RATIONALE_LENGTH = 2_000
MAX_IDEMPOTENCY_KEY_LENGTH = 200
DEFAULT_MAX_AGREEMENTS = 1_024


class PerformanceLifecycleError(RuntimeError):
    """Base error with content-free subclasses for API mapping."""


class PerformanceLifecycleInvalid(PerformanceLifecycleError):
    pass


class PerformanceLifecycleNotFound(PerformanceLifecycleError, KeyError):
    pass


class PerformanceLifecycleConflict(PerformanceLifecycleError):
    pass


class PerformanceLifecycleStale(PerformanceLifecycleConflict):
    pass


class PerformanceLifecycleCapacityExceeded(PerformanceLifecycleError):
    pass


@dataclass(frozen=True, slots=True)
class AgreementPreparation:
    """Internal review receipt plus the exact approved runner-valid bundle."""

    draft_id: str
    draft_sha256: str
    reviewer: str
    rationale: str
    prepared_at: datetime
    target: PerformanceTargetInput
    bundle: PreparedPerformanceBundle = field(repr=False)

    @property
    def poc_id(self) -> str:
        return self.bundle.poc_id

    @property
    def approved_contract(self) -> POCContract:
        return self.bundle.approved_contract


@dataclass(frozen=True, slots=True)
class PerformanceLifecycleSnapshot:
    """Exact current state without execution or verdict projection."""

    preparation: AgreementPreparation | None
    review_invitation: CustomerReviewInvitation | None
    review_expired: bool
    confirmation: ContractConfirmation | None
    frozen_contract: POCContract | None

    @property
    def poc_id(self) -> str | None:
        return None if self.preparation is None else self.preparation.poc_id


@dataclass(frozen=True, slots=True)
class LifecycleWriteResult:
    """One immutable write and whether it exactly replayed."""

    value: (
        AgreementPreparation
        | CustomerReviewInvitation
        | ContractConfirmation
        | POCContract
    )
    replayed: bool


@dataclass(frozen=True, slots=True)
class _IdempotencyRecord:
    request_sha256: str
    poc_id: str


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _safe_text(
    value: object,
    *,
    field_name: str,
    maximum: int,
    single_line: bool,
) -> str:
    if type(value) is not str:
        raise PerformanceLifecycleInvalid("{0} must be text.".format(field_name))
    normalized = unicodedata.normalize("NFC", value).strip()
    if (
        not normalized
        or len(normalized) > maximum
        or (single_line and ("\n" in normalized or "\r" in normalized))
        or any(
            ord(character) < 0x20 and character not in {"\n", "\r", "\t"}
            for character in normalized
        )
        or any(ord(character) == 0x7F for character in normalized)
    ):
        raise PerformanceLifecycleInvalid(
            "{0} is outside its supported bounds.".format(field_name)
        )
    return normalized


def _idempotency_digest(value: object) -> str:
    if type(value) is not str or value != value.strip():
        raise PerformanceLifecycleInvalid(
            "idempotency_key is outside its supported bounds."
        )
    normalized = _safe_text(
        value,
        field_name="idempotency_key",
        maximum=MAX_IDEMPOTENCY_KEY_LENGTH,
        single_line=True,
    )
    return hashlib.sha256(
        b"exitspec-performance-lifecycle-idempotency-v1\x00"
        + normalized.encode("utf-8")
    ).hexdigest()


def _request_digest(payload: dict[str, object]) -> str:
    return hashlib.sha256(
        b"exitspec-performance-lifecycle-request-v1\x00" + canonical_json_bytes(payload)
    ).hexdigest()


class ProcessLocalPerformanceLifecycleService:
    """Thread-safe bounded agreement lifecycle for local demo POCs."""

    def __init__(
        self,
        *,
        draft_lookup: Callable[[str], DraftPOCSnapshot],
        proposal_lookup: Callable[[str], Sequence[ProposalReviewItem]],
        definition_lookup: Callable[[], Sequence[ContractDefinitionReceipt]],
        prompt_bytes: bytes,
        clock: Callable[[], datetime] = _utc_now,
        max_agreements: int = DEFAULT_MAX_AGREEMENTS,
    ) -> None:
        for dependency in (
            draft_lookup,
            proposal_lookup,
            definition_lookup,
            clock,
        ):
            if not callable(dependency):
                raise TypeError("Lifecycle dependencies must be callable.")
        if type(prompt_bytes) is not bytes or not prompt_bytes:
            raise ValueError("prompt_bytes must be non-empty exact bytes.")
        if (
            type(max_agreements) is not int
            or isinstance(max_agreements, bool)
            or not 1 <= max_agreements <= 10_000
        ):
            raise ValueError("max_agreements is outside supported bounds.")
        self._draft_lookup = draft_lookup
        self._proposal_lookup = proposal_lookup
        self._definition_lookup = definition_lookup
        self._prompt_bytes = prompt_bytes
        self._clock = clock
        self._max_agreements = max_agreements
        self._preparations: dict[str, AgreementPreparation] = {}
        self._review_invitations: dict[str, CustomerReviewInvitation] = {}
        self._review_token_secret = secrets.token_bytes(32)
        self._confirmations: dict[str, ContractConfirmation] = {}
        self._frozen: dict[str, POCContract] = {}
        self._prepare_idempotency: dict[str, _IdempotencyRecord] = {}
        self._review_idempotency: dict[str, _IdempotencyRecord] = {}
        self._review_idempotency_results: dict[
            str, CustomerReviewInvitation
        ] = {}
        self._confirm_idempotency: dict[str, _IdempotencyRecord] = {}
        self._freeze_idempotency: dict[str, _IdempotencyRecord] = {}
        self._lock = RLock()

    def _assemble(
        self,
        poc_id: str,
        target: PerformanceTargetInput,
        prepared_at: datetime,
    ) -> PreparedPerformanceBundle:
        try:
            draft = self._draft_lookup(poc_id)
            proposals = self._proposal_lookup(poc_id)
            definitions = tuple(
                definition
                for definition in self._definition_lookup()
                if definition.poc_id == poc_id
            )
        except Exception as error:
            raise PerformanceLifecycleConflict(
                "Current POC inputs are unavailable."
            ) from error
        try:
            return prepare_performance_bundle(
                draft=draft,
                proposals=proposals,
                definitions=definitions,
                target=target,
                prompt_bytes=self._prompt_bytes,
                prepared_at=prepared_at,
            )
        except Exception as error:
            raise PerformanceLifecycleConflict(
                "Current POC inputs cannot form an executable agreement."
            ) from error

    def _current_preparation(self, poc_id: str) -> AgreementPreparation:
        try:
            preparation = self._preparations[poc_id]
        except KeyError as error:
            raise PerformanceLifecycleNotFound(
                "Agreement preparation was not found."
            ) from error
        current = self._assemble(
            poc_id,
            preparation.target,
            preparation.prepared_at,
        )
        if current.bundle_fingerprint != preparation.bundle.bundle_fingerprint:
            raise PerformanceLifecycleStale(
                "Agreement inputs changed after preparation."
            )
        return preparation

    def prepare(
        self,
        poc_id: str,
        *,
        target: PerformanceTargetInput,
        reviewer: object,
        rationale: object,
        idempotency_key: object,
    ) -> LifecycleWriteResult:
        if type(poc_id) is not str:
            raise PerformanceLifecycleInvalid("poc_id is invalid.")
        if type(target) is not PerformanceTargetInput:
            raise PerformanceLifecycleInvalid("target is invalid.")
        reviewer_text = _safe_text(
            reviewer,
            field_name="reviewer",
            maximum=MAX_REVIEWER_LENGTH,
            single_line=True,
        )
        rationale_text = _safe_text(
            rationale,
            field_name="rationale",
            maximum=MAX_RATIONALE_LENGTH,
            single_line=False,
        )
        key_digest = _idempotency_digest(idempotency_key)
        request_sha256 = _request_digest(
            {
                "operation": "PREPARE",
                "poc_id": poc_id,
                "target": target.model_dump(mode="json"),
                "reviewer": reviewer_text,
                "rationale": rationale_text,
            }
        )
        with self._lock:
            prior = self._prepare_idempotency.get(key_digest)
            if prior is not None:
                if prior.poc_id != poc_id or prior.request_sha256 != request_sha256:
                    raise PerformanceLifecycleConflict(
                        "Idempotency key conflicts with another preparation."
                    )
                preparation = self._current_preparation(poc_id)
                return LifecycleWriteResult(preparation, True)
            if poc_id in self._preparations:
                raise PerformanceLifecycleConflict(
                    "This POC already has an immutable prepared agreement."
                )
            if len(self._preparations) >= self._max_agreements:
                raise PerformanceLifecycleCapacityExceeded(
                    "Agreement capacity is exhausted."
                )
            prepared_at = self._clock()
            bundle = self._assemble(poc_id, target, prepared_at)
            receipt_payload = {
                "poc_id": poc_id,
                "bundle_fingerprint": bundle.bundle_fingerprint,
                "reviewer": reviewer_text,
                "rationale": rationale_text,
                "prepared_at": prepared_at.isoformat(),
            }
            draft_sha256 = hashlib.sha256(
                b"exitspec-performance-agreement-draft-v1\x00"
                + canonical_json_bytes(receipt_payload)
            ).hexdigest()
            preparation = AgreementPreparation(
                draft_id="agd_{0}".format(draft_sha256[:32]),
                draft_sha256=draft_sha256,
                reviewer=reviewer_text,
                rationale=rationale_text,
                prepared_at=prepared_at,
                target=target,
                bundle=bundle,
            )
            invitation_id = "review-{0}".format(secrets.token_hex(12))
            raw_token = self._review_token(
                invitation_id=invitation_id,
                contract_id=bundle.approved_contract.id,
                contract_version=bundle.approved_contract.version,
                confirmation_fingerprint=contract_confirmation_fingerprint(
                    bundle.approved_contract
                ),
            )
            invitation, _ = issue_customer_review_invitation(
                contract_id=bundle.approved_contract.id,
                contract_version=bundle.approved_contract.version,
                confirmation_fingerprint=contract_confirmation_fingerprint(
                    bundle.approved_contract
                ),
                created_at=prepared_at,
                token=raw_token,
                invitation_id=invitation_id,
            )
            self._preparations[poc_id] = preparation
            self._review_invitations[poc_id] = invitation
            self._prepare_idempotency[key_digest] = _IdempotencyRecord(
                request_sha256,
                poc_id,
            )
            return LifecycleWriteResult(preparation, False)

    def _review_token(
        self,
        *,
        invitation_id: str,
        contract_id: str,
        contract_version: str,
        confirmation_fingerprint: str,
    ) -> str:
        message = canonical_json_bytes(
            {
                "invitation_id": invitation_id,
                "contract_id": contract_id,
                "contract_version": contract_version,
                "confirmation_fingerprint": confirmation_fingerprint,
            }
        )
        digest = hmac.new(
            self._review_token_secret,
            b"exitspec-performance-review-capability-v1\x00" + message,
            hashlib.sha256,
        ).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")

    def _invitation_token(self, invitation: CustomerReviewInvitation) -> str:
        return self._review_token(
            invitation_id=invitation.invitation_id,
            contract_id=invitation.contract_id,
            contract_version=invitation.contract_version,
            confirmation_fingerprint=invitation.confirmation_fingerprint,
        )

    def customer_review_poc_id(self, token: object) -> str | None:
        """Resolve a process-local capability without accepting a POC id."""

        if type(token) is not str or not token or len(token) > 512:
            return None
        with self._lock:
            for poc_id, invitation in self._review_invitations.items():
                if hmac.compare_digest(self._invitation_token(invitation), token):
                    return poc_id
        return None

    def customer_review_url(self, poc_id: str) -> str:
        """Return the one shareable local review URL for a prepared agreement."""

        with self._lock:
            self._current_preparation(poc_id)
            try:
                invitation = self._review_invitations[poc_id]
            except KeyError as error:
                raise PerformanceLifecycleNotFound(
                    "Customer review invitation was not found."
                ) from error
            return self.customer_review_url_for(invitation)

    def customer_review_url_for(
        self,
        invitation: CustomerReviewInvitation,
    ) -> str:
        """Derive the shareable URL without retaining its raw capability."""

        if type(invitation) is not CustomerReviewInvitation:
            raise PerformanceLifecycleInvalid("Review invitation is invalid.")
        return "/review/{0}".format(self._invitation_token(invitation))

    def customer_review_expired(self, poc_id: str) -> bool:
        """Return whether the current prepared review capability has expired."""

        with self._lock:
            self._current_preparation(poc_id)
            try:
                invitation = self._review_invitations[poc_id]
            except KeyError as error:
                raise PerformanceLifecycleNotFound(
                    "Customer review invitation was not found."
                ) from error
            return self._clock() >= invitation.expires_at

    def reissue_customer_review(
        self,
        poc_id: str,
        *,
        idempotency_key: object,
    ) -> LifecycleWriteResult:
        """Replace only an expired, undecided capability for the same agreement."""

        key_digest = _idempotency_digest(idempotency_key)
        request_sha256 = _request_digest(
            {"operation": "REISSUE_CUSTOMER_REVIEW", "poc_id": poc_id}
        )
        with self._lock:
            preparation = self._current_preparation(poc_id)
            checked_at = self._clock()
            prior = self._review_idempotency.get(key_digest)
            if prior is not None:
                if prior.poc_id != poc_id or prior.request_sha256 != request_sha256:
                    raise PerformanceLifecycleConflict(
                        "Idempotency key conflicts with another review reissue."
                    )
                replayed_invitation = self._review_idempotency_results[key_digest]
                current_invitation = self._review_invitations[poc_id]
                if (
                    replayed_invitation.invitation_id
                    != current_invitation.invitation_id
                    or checked_at >= replayed_invitation.expires_at
                ):
                    raise PerformanceLifecycleConflict(
                        "This review reissue operation is no longer current."
                    )
                return LifecycleWriteResult(
                    replayed_invitation,
                    True,
                )
            if poc_id in self._confirmations or poc_id in self._frozen:
                raise PerformanceLifecycleConflict(
                    "A decided agreement cannot issue another review link."
                )
            current = self._review_invitations[poc_id]
            issued_at = checked_at
            if issued_at < current.expires_at:
                raise PerformanceLifecycleConflict(
                    "The current customer review link is still active."
                )
            fingerprint = contract_confirmation_fingerprint(
                preparation.approved_contract
            )
            invitation_id = "review-{0}".format(secrets.token_hex(12))
            raw_token = self._review_token(
                invitation_id=invitation_id,
                contract_id=preparation.approved_contract.id,
                contract_version=preparation.approved_contract.version,
                confirmation_fingerprint=fingerprint,
            )
            invitation, _ = issue_customer_review_invitation(
                contract_id=preparation.approved_contract.id,
                contract_version=preparation.approved_contract.version,
                confirmation_fingerprint=fingerprint,
                created_at=issued_at,
                token=raw_token,
                invitation_id=invitation_id,
            )
            self._review_invitations[poc_id] = invitation
            self._review_idempotency[key_digest] = _IdempotencyRecord(
                request_sha256,
                poc_id,
            )
            self._review_idempotency_results[key_digest] = invitation
            return LifecycleWriteResult(invitation, False)

    def customer_review_payload(self, token: object) -> dict[str, Any]:
        """Return the customer-safe agreement for one valid capability."""

        poc_id = self.customer_review_poc_id(token)
        if poc_id is None or type(token) is not str:
            raise ReviewInvitationError("Customer review link is invalid.")
        with self._lock:
            preparation = self._current_preparation(poc_id)
            invitation = self._review_invitations[poc_id]
            invitation.require_valid(token, now=self._clock())
            return build_customer_review_payload(
                invitation=invitation,
                contract=preparation.approved_contract,
                confirmation=self._confirmations.get(poc_id),
                poc_id=poc_id,
                return_url="/app/pocs/{0}/agreement".format(poc_id),
                execution_endpoint=preparation.target.endpoint,
            )

    def record_customer_review_decision(
        self,
        token: object,
        *,
        decision: object,
        agreement_acknowledged: object,
        rationale: object,
        idempotency_key: object,
    ) -> LifecycleWriteResult:
        """Record one terminal decision reached only through a review capability."""

        poc_id = self.customer_review_poc_id(token)
        if poc_id is None or type(token) is not str:
            raise ReviewInvitationError("Customer review link is invalid.")
        try:
            requested_decision = ConfirmationDecision(decision)
        except (TypeError, ValueError) as error:
            raise PerformanceLifecycleInvalid(
                "Customer review decision is invalid."
            ) from error
        if type(agreement_acknowledged) is not bool:
            raise PerformanceLifecycleInvalid(
                "agreement_acknowledged must be a boolean."
            )
        if (
            requested_decision is ConfirmationDecision.CONFIRM
            and agreement_acknowledged is not True
        ):
            raise PerformanceLifecycleInvalid(
                "Explicit agreement acknowledgement is required."
            )
        if rationale is None and requested_decision is ConfirmationDecision.CONFIRM:
            rationale_text = (
                "Customer confirmed that this exact contract version matches "
                "the intended POC agreement."
            )
        else:
            rationale_text = _safe_text(
                rationale,
                field_name="rationale",
                maximum=MAX_RATIONALE_LENGTH,
                single_line=False,
            )
        key_digest = _idempotency_digest(idempotency_key)
        request_sha256 = _request_digest(
            {
                "operation": "CUSTOMER_REVIEW_DECISION",
                "poc_id": poc_id,
                "decision": requested_decision.value,
                "agreement_acknowledged": agreement_acknowledged,
                "rationale": rationale_text,
            }
        )
        with self._lock:
            preparation = self._current_preparation(poc_id)
            invitation = self._review_invitations[poc_id]
            decided_at = self._clock()
            invitation.require_valid(token, now=decided_at)
            prior = self._confirm_idempotency.get(key_digest)
            if prior is not None:
                if prior.poc_id != poc_id or prior.request_sha256 != request_sha256:
                    raise PerformanceLifecycleConflict(
                        "Idempotency key conflicts with another customer decision."
                    )
                return LifecycleWriteResult(self._confirmations[poc_id], True)
            if poc_id in self._confirmations:
                raise PerformanceLifecycleConflict(
                    "This review already has a terminal customer decision."
                )
            confirmation = record_confirmation(
                preparation.approved_contract,
                confirmer_identity="Customer approver · capability review",
                decision=requested_decision,
                agreement_acknowledged=agreement_acknowledged,
                rationale=rationale_text,
                idempotency_key=idempotency_key,
                decided_at=decided_at,
            )
            self._confirmations[poc_id] = confirmation
            self._confirm_idempotency[key_digest] = _IdempotencyRecord(
                request_sha256,
                poc_id,
            )
            return LifecycleWriteResult(confirmation, False)

    def freeze(
        self,
        poc_id: str,
        *,
        idempotency_key: object,
    ) -> LifecycleWriteResult:
        key_digest = _idempotency_digest(idempotency_key)
        request_sha256 = _request_digest({"operation": "FREEZE", "poc_id": poc_id})
        with self._lock:
            preparation = self._current_preparation(poc_id)
            try:
                confirmation = self._confirmations[poc_id]
            except KeyError as error:
                raise PerformanceLifecycleConflict(
                    "Customer confirmation is required before freeze."
                ) from error
            if confirmation.decision is not ConfirmationDecision.CONFIRM:
                raise PerformanceLifecycleConflict(
                    "Customer requested changes; this agreement cannot be frozen."
                )
            prior = self._freeze_idempotency.get(key_digest)
            if prior is not None:
                if prior.poc_id != poc_id or prior.request_sha256 != request_sha256:
                    raise PerformanceLifecycleConflict(
                        "Idempotency key conflicts with another freeze."
                    )
                return LifecycleWriteResult(self._frozen[poc_id], True)
            if poc_id in self._frozen:
                raise PerformanceLifecycleConflict("This agreement is already frozen.")
            frozen = freeze_confirmed_contract(
                preparation.approved_contract,
                confirmation,
                frozen_at=self._clock(),
            )
            self._frozen[poc_id] = frozen
            self._freeze_idempotency[key_digest] = _IdempotencyRecord(
                request_sha256,
                poc_id,
            )
            return LifecycleWriteResult(frozen, False)

    def snapshot(
        self,
        poc_id: str,
        *,
        allow_empty: bool = True,
    ) -> PerformanceLifecycleSnapshot:
        with self._lock:
            preparation = self._preparations.get(poc_id)
            if preparation is None:
                if not allow_empty:
                    raise PerformanceLifecycleNotFound(
                        "Agreement preparation was not found."
                    )
                return PerformanceLifecycleSnapshot(None, None, False, None, None)
            preparation = self._current_preparation(poc_id)
            invitation = self._review_invitations.get(poc_id)
            return PerformanceLifecycleSnapshot(
                preparation,
                invitation,
                bool(
                    invitation is not None
                    and self._clock() >= invitation.expires_at
                ),
                self._confirmations.get(poc_id),
                self._frozen.get(poc_id),
            )

    def frozen_bundle(
        self,
        poc_id: str,
    ) -> tuple[PreparedPerformanceBundle, ContractConfirmation, POCContract]:
        """Return exact server-owned run inputs only after confirmed freeze."""

        with self._lock:
            preparation = self._current_preparation(poc_id)
            try:
                confirmation = self._confirmations[poc_id]
                frozen = self._frozen[poc_id]
            except KeyError as error:
                raise PerformanceLifecycleConflict(
                    "A confirmed frozen agreement is required."
                ) from error
            if (
                frozen.status is not ContractStatus.FROZEN
                or frozen.confirmation_id != confirmation.confirmation_id
            ):
                raise PerformanceLifecycleConflict(
                    "Frozen agreement binding is invalid."
                )
            return preparation.bundle, confirmation, frozen


__all__ = [
    "AgreementPreparation",
    "LifecycleWriteResult",
    "PerformanceLifecycleCapacityExceeded",
    "PerformanceLifecycleConflict",
    "PerformanceLifecycleError",
    "PerformanceLifecycleInvalid",
    "PerformanceLifecycleNotFound",
    "PerformanceLifecycleSnapshot",
    "PerformanceLifecycleStale",
    "ProcessLocalPerformanceLifecycleService",
]
