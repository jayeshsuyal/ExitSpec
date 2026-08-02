"""Process-local lifecycle for one runner-valid performance agreement.

The service composes the pure performance-contract assembler with ExitSpec's
existing customer-confirmation and confirmation-aware freeze primitives.  It
does not execute the POC, generate evidence, or issue a verdict.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
from threading import RLock
from typing import Callable, Sequence
import unicodedata

from .canonical import canonical_json_bytes
from .confirmations import (
    ConfirmationDecision,
    ContractConfirmation,
    record_confirmation,
)
from .contracts import freeze_confirmed_contract
from .models import ContractStatus, POCContract
from .poc_contract_definition import ContractDefinitionReceipt
from .poc_creation import DraftPOCSnapshot
from .poc_performance_contract import (
    PerformanceTargetInput,
    PreparedPerformanceBundle,
    prepare_performance_bundle,
)
from .poc_proposal_review import ProposalReviewItem


MAX_REVIEWER_LENGTH = 160
MAX_IDENTITY_LENGTH = 320
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
    confirmation: ContractConfirmation | None
    frozen_contract: POCContract | None

    @property
    def poc_id(self) -> str | None:
        return None if self.preparation is None else self.preparation.poc_id


@dataclass(frozen=True, slots=True)
class LifecycleWriteResult:
    """One immutable write and whether it exactly replayed."""

    value: AgreementPreparation | ContractConfirmation | POCContract
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
        self._confirmations: dict[str, ContractConfirmation] = {}
        self._frozen: dict[str, POCContract] = {}
        self._prepare_idempotency: dict[str, _IdempotencyRecord] = {}
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
            self._preparations[poc_id] = preparation
            self._prepare_idempotency[key_digest] = _IdempotencyRecord(
                request_sha256,
                poc_id,
            )
            return LifecycleWriteResult(preparation, False)

    def confirm(
        self,
        poc_id: str,
        *,
        confirmer_identity: object,
        agreement_acknowledged: object,
        rationale: object,
        idempotency_key: object,
    ) -> LifecycleWriteResult:
        identity = _safe_text(
            confirmer_identity,
            field_name="confirmer_identity",
            maximum=MAX_IDENTITY_LENGTH,
            single_line=True,
        )
        rationale_text = _safe_text(
            rationale,
            field_name="rationale",
            maximum=MAX_RATIONALE_LENGTH,
            single_line=False,
        )
        if agreement_acknowledged is not True:
            raise PerformanceLifecycleInvalid(
                "Explicit agreement acknowledgement is required."
            )
        key_digest = _idempotency_digest(idempotency_key)
        key_value = idempotency_key
        request_sha256 = _request_digest(
            {
                "operation": "CONFIRM",
                "poc_id": poc_id,
                "confirmer_identity": identity,
                "agreement_acknowledged": True,
                "rationale": rationale_text,
            }
        )
        with self._lock:
            preparation = self._current_preparation(poc_id)
            prior = self._confirm_idempotency.get(key_digest)
            if prior is not None:
                if prior.poc_id != poc_id or prior.request_sha256 != request_sha256:
                    raise PerformanceLifecycleConflict(
                        "Idempotency key conflicts with another confirmation."
                    )
                return LifecycleWriteResult(self._confirmations[poc_id], True)
            if poc_id in self._confirmations:
                raise PerformanceLifecycleConflict(
                    "This agreement already has a customer confirmation."
                )
            confirmation = record_confirmation(
                preparation.approved_contract,
                confirmer_identity=identity,
                decision=ConfirmationDecision.CONFIRM,
                agreement_acknowledged=True,
                rationale=rationale_text,
                idempotency_key=key_value,
                decided_at=self._clock(),
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
                return PerformanceLifecycleSnapshot(None, None, None)
            preparation = self._current_preparation(poc_id)
            return PerformanceLifecycleSnapshot(
                preparation,
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
