"""Bounded human triage for source-derived POC proposals.

This process-local service overlays immutable human triage decisions on a
fresh, trusted lookup of redacted, source-bound proposals.  ``KEEP_FOR_CONTRACT``
means only that a proposal may be considered by a later contract-authoring
boundary.  This module cannot approve, confirm, freeze, execute, create
evidence, or issue a verdict.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
import re
from contextlib import contextmanager
from threading import RLock
from typing import Any, Callable, ClassVar, Literal, Mapping, Sequence, Self, Tuple
import unicodedata

from pydantic import Field, field_validator, model_validator

from .models import FrozenExitSpecModel
from .poc_creation import POC_ID_PATTERN
from .poc_sources import SourceKind
from .redaction import (
    RedactionBoundaryError,
    assert_redaction_egress,
    redact_transcript,
)


PROPOSAL_ID_PATTERN = r"^prop_[a-z0-9][a-z0-9_-]{7,95}$"
SOURCE_RECEIPT_ID_PATTERN = r"^srcpt_[a-z0-9][a-z0-9_-]{7,95}$"

MAX_SOURCE_QUOTE_LENGTH = 4_000
MAX_NORMALIZED_CLAIM_LENGTH = 2_000
MAX_REVIEWER_LENGTH = 160
MAX_RATIONALE_LENGTH = 2_000
MAX_IDEMPOTENCY_KEY_LENGTH = 200

_DEFAULT_MAX_PROPOSALS_PER_POC = 1_024
_DEFAULT_MAX_KNOWN_PROPOSALS = 32_768
_DEFAULT_MAX_DECISIONS = 16_384
_DEFAULT_MAX_IDEMPOTENCY_RECORDS = 32_768
_MAX_CONFIGURABLE_PROPOSALS_PER_POC = 8_192
_MAX_CONFIGURABLE_KNOWN_PROPOSALS = 100_000
_MAX_CONFIGURABLE_DECISIONS = 100_000
_MAX_CONFIGURABLE_IDEMPOTENCY_RECORDS = 100_000

_POC_ID_RE = re.compile(POC_ID_PATTERN)
_PROPOSAL_ID_RE = re.compile(PROPOSAL_ID_PATTERN)


class ProposalReviewState(str, Enum):
    """The only states owned by this human-triage boundary."""

    NEEDS_REVIEW = "NEEDS_REVIEW"
    KEEP_FOR_CONTRACT = "KEEP_FOR_CONTRACT"
    DISCARD = "DISCARD"


class ProposalDecision(str, Enum):
    """A human triage choice, not a contract or lifecycle decision."""

    KEEP_FOR_CONTRACT = "KEEP_FOR_CONTRACT"
    DISCARD = "DISCARD"


class ProposalDecisionDisposition(str, Enum):
    """Whether a decision was created or safely replayed."""

    CREATED = "CREATED"
    IDEMPOTENT_REPLAY = "IDEMPOTENT_REPLAY"
    DECISION_REPLAY = "DECISION_REPLAY"


class _FrozenProposalReviewModel(FrozenExitSpecModel):
    """Frozen model whose copy helpers cannot bypass validation."""

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        if not update:
            return super().model_copy(deep=deep)
        payload = self.model_dump(mode="python")
        payload.update(dict(update))
        return type(self).model_validate(payload)

    def copy(
        self,
        *,
        include: Any = None,
        exclude: Any = None,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        if include is not None or exclude is not None:
            raise ValueError(
                "include/exclude copies are not supported at this boundary."
            )
        return self.model_copy(update=update, deep=deep)


def _normalize_text(
    value: object,
    *,
    field_name: str,
    maximum: int,
    single_line: bool = False,
) -> str:
    if type(value) is not str:
        raise ValueError("{0} must be text.".format(field_name))
    normalized = unicodedata.normalize(
        "NFC",
        value.replace("\r\n", "\n").replace("\r", "\n"),
    ).strip()
    if not normalized:
        raise ValueError("{0} must contain text.".format(field_name))
    if len(normalized) > maximum:
        raise ValueError("{0} exceeds its bounded size.".format(field_name))
    if single_line and "\n" in normalized:
        raise ValueError("{0} must be a single line.".format(field_name))
    for character in normalized:
        if character == "\n":
            continue
        if unicodedata.category(character).startswith("C"):
            raise ValueError(
                "{0} contains a forbidden control character.".format(field_name)
            )
    return normalized


def _require_already_redacted(value: str, *, field_name: str) -> str:
    """Reject supported sensitive values instead of silently rewriting input."""

    try:
        redacted = assert_redaction_egress(redact_transcript(value))
    except (TypeError, ValueError, RedactionBoundaryError):
        raise ValueError(
            "{0} did not pass the redaction boundary.".format(field_name)
        ) from None
    if redacted != value:
        raise ValueError("{0} must contain redacted text only.".format(field_name))
    return value


def _safe_text(
    value: object,
    *,
    field_name: str,
    maximum: int,
    single_line: bool = False,
) -> str:
    return _require_already_redacted(
        _normalize_text(
            value,
            field_name=field_name,
            maximum=maximum,
            single_line=single_line,
        ),
        field_name=field_name,
    )


def _require_aware_timestamp(value: datetime) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("decided_at must be timezone-aware.")
    return value


class SourceBoundProposal(_FrozenProposalReviewModel):
    """Trusted lookup input containing redacted, public-safe source bindings."""

    poc_id: str = Field(pattern=POC_ID_PATTERN)
    proposal_id: str = Field(pattern=PROPOSAL_ID_PATTERN)
    source_receipt_id: str = Field(pattern=SOURCE_RECEIPT_ID_PATTERN)
    source_kind: SourceKind
    source_quote: str = Field(
        min_length=1,
        max_length=MAX_SOURCE_QUOTE_LENGTH,
    )
    normalized_claim: str = Field(
        min_length=1,
        max_length=MAX_NORMALIZED_CLAIM_LENGTH,
    )
    state: Literal[ProposalReviewState.NEEDS_REVIEW] = ProposalReviewState.NEEDS_REVIEW

    @field_validator("source_quote", mode="before")
    @classmethod
    def validate_source_quote(cls, value: object) -> str:
        return _safe_text(
            value,
            field_name="source_quote",
            maximum=MAX_SOURCE_QUOTE_LENGTH,
        )

    @field_validator("normalized_claim", mode="before")
    @classmethod
    def validate_normalized_claim(cls, value: object) -> str:
        return _safe_text(
            value,
            field_name="normalized_claim",
            maximum=MAX_NORMALIZED_CLAIM_LENGTH,
        )


class ProposalDecisionReceipt(_FrozenProposalReviewModel):
    """Safe, immutable projection of one human triage decision."""

    poc_id: str = Field(pattern=POC_ID_PATTERN)
    proposal_id: str = Field(pattern=PROPOSAL_ID_PATTERN)
    source_receipt_id: str = Field(pattern=SOURCE_RECEIPT_ID_PATTERN)
    source_kind: SourceKind
    decision: ProposalDecision
    reviewer: str = Field(min_length=1, max_length=MAX_REVIEWER_LENGTH)
    rationale: str = Field(min_length=1, max_length=MAX_RATIONALE_LENGTH)
    decided_at: datetime

    @field_validator("reviewer", mode="before")
    @classmethod
    def validate_reviewer(cls, value: object) -> str:
        return _safe_text(
            value,
            field_name="reviewer",
            maximum=MAX_REVIEWER_LENGTH,
            single_line=True,
        )

    @field_validator("rationale", mode="before")
    @classmethod
    def validate_rationale(cls, value: object) -> str:
        return _safe_text(
            value,
            field_name="rationale",
            maximum=MAX_RATIONALE_LENGTH,
        )

    @field_validator("decided_at")
    @classmethod
    def validate_decided_at(cls, value: datetime) -> datetime:
        return _require_aware_timestamp(value)


class ProposalReviewItem(_FrozenProposalReviewModel):
    """Current source proposal with any immutable human decision overlaid."""

    poc_id: str = Field(pattern=POC_ID_PATTERN)
    proposal_id: str = Field(pattern=PROPOSAL_ID_PATTERN)
    source_receipt_id: str = Field(pattern=SOURCE_RECEIPT_ID_PATTERN)
    source_kind: SourceKind
    source_quote: str = Field(
        min_length=1,
        max_length=MAX_SOURCE_QUOTE_LENGTH,
    )
    normalized_claim: str = Field(
        min_length=1,
        max_length=MAX_NORMALIZED_CLAIM_LENGTH,
    )
    review_state: ProposalReviewState
    decision: ProposalDecisionReceipt | None = None

    @field_validator("source_quote", mode="before")
    @classmethod
    def validate_source_quote(cls, value: object) -> str:
        return _safe_text(
            value,
            field_name="source_quote",
            maximum=MAX_SOURCE_QUOTE_LENGTH,
        )

    @field_validator("normalized_claim", mode="before")
    @classmethod
    def validate_normalized_claim(cls, value: object) -> str:
        return _safe_text(
            value,
            field_name="normalized_claim",
            maximum=MAX_NORMALIZED_CLAIM_LENGTH,
        )

    @model_validator(mode="after")
    def validate_decision_overlay(self) -> "ProposalReviewItem":
        if self.decision is None:
            if self.review_state != ProposalReviewState.NEEDS_REVIEW:
                raise ValueError("An undecided proposal must remain NEEDS_REVIEW.")
            return self
        if (
            self.review_state.value != self.decision.decision.value
            or self.poc_id != self.decision.poc_id
            or self.proposal_id != self.decision.proposal_id
            or self.source_receipt_id != self.decision.source_receipt_id
            or self.source_kind != self.decision.source_kind
        ):
            raise ValueError("The proposal decision must match its review projection.")
        return self


class ProposalDecisionResult(_FrozenProposalReviewModel):
    """Decision response that does not expose the idempotency key."""

    receipt: ProposalDecisionReceipt
    disposition: ProposalDecisionDisposition

    @property
    def created(self) -> bool:
        return self.disposition == ProposalDecisionDisposition.CREATED

    @property
    def replayed(self) -> bool:
        return not self.created


class ProposalReviewSemantics(_FrozenProposalReviewModel):
    """Machine-readable storage and zero-authority guarantees."""

    storage_scope: Literal["PROCESS_LOCAL"] = "PROCESS_LOCAL"
    survives_process_restart: Literal[False] = False
    shared_across_workers: Literal[False] = False
    keep_is_contract_approval: Literal[False] = False
    can_confirm_contract: Literal[False] = False
    can_freeze_contract: Literal[False] = False
    can_execute_poc: Literal[False] = False
    can_issue_evidence: Literal[False] = False
    can_issue_verdict: Literal[False] = False
    max_proposals_per_poc: int = Field(
        ge=1,
        le=_MAX_CONFIGURABLE_PROPOSALS_PER_POC,
    )
    max_known_proposals: int = Field(
        ge=1,
        le=_MAX_CONFIGURABLE_KNOWN_PROPOSALS,
    )
    max_decisions: int = Field(
        ge=1,
        le=_MAX_CONFIGURABLE_DECISIONS,
    )
    max_idempotency_records: int = Field(
        ge=1,
        le=_MAX_CONFIGURABLE_IDEMPOTENCY_RECORDS,
    )


class ProposalReviewError(RuntimeError):
    """Base class for content-free proposal-review failures."""

    http_status: ClassVar[int] = 500


class ProposalReviewInvalid(ProposalReviewError):
    """A request or trusted lookup result violated the bounded contract."""

    http_status = 400


class ProposalReviewLookupUnavailable(ProposalReviewError):
    """The current source proposal projection could not be trusted."""

    http_status = 503


class ProposalReviewProposalUnavailable(ProposalReviewError, KeyError):
    """The requested proposal is not current beneath this POC."""

    http_status = 404


class ProposalReviewCrossPOC(ProposalReviewProposalUnavailable):
    """A proposal identifier was observed beneath a different POC."""


class ProposalReviewStaleProposal(ProposalReviewError):
    """A known proposal disappeared or changed its immutable source binding."""

    http_status = 409


class ProposalReviewDecisionConflict(ProposalReviewError):
    """An immutable proposal decision was changed after creation."""

    http_status = 409


class ProposalReviewIdempotencyConflict(ProposalReviewError):
    """An idempotency key was reused for a different decision request."""

    http_status = 409


class ProposalReviewCapacityExceeded(ProposalReviewError):
    """A bounded process-local store reached its configured capacity."""

    http_status = 503


@dataclass(frozen=True, slots=True)
class _IdempotencyRecord:
    request_sha256: str
    poc_id: str
    proposal_id: str


class _AuthoringCommitGuard:
    """Review-lock-held commit token for the shared A2/A3 source boundary."""

    __slots__ = ("_proposal_ids", "_service", "_source_key")

    def __init__(
        self,
        service: "ProcessLocalProposalReviewService",
        source_key: tuple[str, str],
    ) -> None:
        self._service = service
        self._source_key = source_key
        self._proposal_ids: frozenset[str] | None = None

    def prepare(self, proposal_ids: Sequence[str]) -> None:
        if self._proposal_ids is not None:
            raise ProposalReviewInvalid("The authoring guard was already prepared.")
        if type(proposal_ids) not in {tuple, list} or not proposal_ids:
            raise ProposalReviewInvalid("The authoring proposal set is invalid.")
        if len(proposal_ids) > _DEFAULT_MAX_PROPOSALS_PER_POC:
            raise ProposalReviewCapacityExceeded(
                "The authoring proposal set exceeds process capacity."
            )
        validated = tuple(_validate_proposal_id(value) for value in proposal_ids)
        if len(set(validated)) != len(validated):
            raise ProposalReviewInvalid("The authoring proposal set is ambiguous.")
        self._proposal_ids = frozenset(validated)

    def commit(self) -> None:
        if self._proposal_ids is None:
            raise ProposalReviewInvalid("The authoring guard was not prepared.")
        self._service._authoring_current_proposals[self._source_key] = self._proposal_ids


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _validate_poc_id(poc_id: object) -> str:
    if type(poc_id) is not str or _POC_ID_RE.fullmatch(poc_id) is None:
        raise ProposalReviewInvalid("The POC identifier is invalid.")
    return poc_id


def _validate_proposal_id(proposal_id: object) -> str:
    if type(proposal_id) is not str or _PROPOSAL_ID_RE.fullmatch(proposal_id) is None:
        raise ProposalReviewInvalid("The proposal identifier is invalid.")
    return proposal_id


def _validate_positive_capacity(
    value: object,
    *,
    field_name: str,
    maximum: int,
) -> int:
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValueError("{0} is outside its supported bounds.".format(field_name))
    return value


def _idempotency_key_digest(idempotency_key: object) -> str:
    if (
        type(idempotency_key) is not str
        or not idempotency_key.strip()
        or len(idempotency_key) > MAX_IDEMPOTENCY_KEY_LENGTH
    ):
        raise ProposalReviewInvalid(
            "The idempotency key is outside its supported bounds."
        )
    return hashlib.sha256(
        b"exitspec-proposal-review-idempotency-key-v1\x00"
        + idempotency_key.encode("utf-8")
    ).hexdigest()


def derive_proposal_id(
    poc_id: str,
    source_receipt_id: str,
    candidate_identity: str,
) -> str:
    """Derive a safe stable proposal ID without exposing a raw candidate ID."""

    validated_poc_id = _validate_poc_id(poc_id)
    if (
        type(source_receipt_id) is not str
        or re.fullmatch(SOURCE_RECEIPT_ID_PATTERN, source_receipt_id) is None
    ):
        raise ProposalReviewInvalid("The source receipt identifier is invalid.")
    if (
        type(candidate_identity) is not str
        or not candidate_identity
        or len(candidate_identity) > 200
    ):
        raise ProposalReviewInvalid("The candidate identity is invalid.")
    digest = hashlib.sha256(
        b"exitspec-safe-proposal-id-v1\x00"
        + validated_poc_id.encode("utf-8")
        + b"\x00"
        + source_receipt_id.encode("utf-8")
        + b"\x00"
        + candidate_identity.encode("utf-8")
    ).hexdigest()
    return "prop_{0}".format(digest[:32])


def _proposal_fingerprint(proposal: SourceBoundProposal) -> str:
    canonical = json.dumps(
        proposal.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(
        b"exitspec-source-bound-proposal-v1\x00" + canonical
    ).hexdigest()


def _decision_request_sha256(
    *,
    poc_id: str,
    proposal_id: str,
    decision: ProposalDecision,
    reviewer: str,
    rationale: str,
) -> str:
    canonical = json.dumps(
        {
            "decision": decision.value,
            "poc_id": poc_id,
            "proposal_id": proposal_id,
            "rationale": rationale,
            "reviewer": reviewer,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(
        b"exitspec-proposal-decision-request-v1\x00" + canonical
    ).hexdigest()


class ProcessLocalProposalReviewService:
    """Thread-safe, bounded human triage over current source proposals."""

    __slots__ = (
        "_authoring_current_proposals",
        "_authoring_guards",
        "_clock",
        "_decisions",
        "_fingerprints",
        "_idempotency",
        "_lock",
        "_max_decisions",
        "_max_idempotency_records",
        "_max_known_proposals",
        "_max_proposals_per_poc",
        "_proposal_lookup",
        "_proposal_owners",
    )

    def __init__(
        self,
        *,
        proposal_lookup: Callable[[str], Sequence[SourceBoundProposal]],
        max_proposals_per_poc: int = _DEFAULT_MAX_PROPOSALS_PER_POC,
        max_known_proposals: int = _DEFAULT_MAX_KNOWN_PROPOSALS,
        max_decisions: int = _DEFAULT_MAX_DECISIONS,
        max_idempotency_records: int = _DEFAULT_MAX_IDEMPOTENCY_RECORDS,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        if not callable(proposal_lookup):
            raise TypeError("proposal_lookup must be callable.")
        self._max_proposals_per_poc = _validate_positive_capacity(
            max_proposals_per_poc,
            field_name="max_proposals_per_poc",
            maximum=_MAX_CONFIGURABLE_PROPOSALS_PER_POC,
        )
        self._max_known_proposals = _validate_positive_capacity(
            max_known_proposals,
            field_name="max_known_proposals",
            maximum=_MAX_CONFIGURABLE_KNOWN_PROPOSALS,
        )
        self._max_decisions = _validate_positive_capacity(
            max_decisions,
            field_name="max_decisions",
            maximum=_MAX_CONFIGURABLE_DECISIONS,
        )
        self._max_idempotency_records = _validate_positive_capacity(
            max_idempotency_records,
            field_name="max_idempotency_records",
            maximum=_MAX_CONFIGURABLE_IDEMPOTENCY_RECORDS,
        )
        if not callable(clock):
            raise TypeError("clock must be callable.")

        self._proposal_lookup = proposal_lookup
        self._clock = clock
        self._fingerprints: dict[tuple[str, str], str] = {}
        self._proposal_owners: dict[str, str] = {}
        self._decisions: dict[
            tuple[str, str],
            ProposalDecisionReceipt,
        ] = {}
        self._idempotency: dict[str, _IdempotencyRecord] = {}
        self._authoring_current_proposals: dict[tuple[str, str], frozenset[str]] = {}
        self._authoring_guards: set[tuple[str, str]] = set()
        self._lock = RLock()

    @property
    def semantics(self) -> ProposalReviewSemantics:
        return ProposalReviewSemantics(
            max_proposals_per_poc=self._max_proposals_per_poc,
            max_known_proposals=self._max_known_proposals,
            max_decisions=self._max_decisions,
            max_idempotency_records=self._max_idempotency_records,
        )

    def __len__(self) -> int:
        with self._lock:
            return len(self._decisions)

    def _lookup(self, poc_id: str) -> Tuple[SourceBoundProposal, ...]:
        try:
            raw_proposals = self._proposal_lookup(poc_id)
        except ProposalReviewError:
            raise
        except Exception as error:
            raise ProposalReviewLookupUnavailable(
                "Current source proposals are unavailable."
            ) from error
        if not isinstance(raw_proposals, (tuple, list)):
            raise ProposalReviewLookupUnavailable(
                "Current source proposals are unavailable."
            )
        if len(raw_proposals) > self._max_proposals_per_poc:
            raise ProposalReviewCapacityExceeded(
                "The POC proposal projection exceeds process capacity."
            )
        proposals: list[SourceBoundProposal] = []
        identifiers: set[str] = set()
        for proposal in raw_proposals:
            if type(proposal) is not SourceBoundProposal:
                raise ProposalReviewLookupUnavailable(
                    "Current source proposals are unavailable."
                )
            if proposal.poc_id != poc_id:
                raise ProposalReviewLookupUnavailable(
                    "Current source proposals are unavailable."
                )
            if proposal.proposal_id in identifiers:
                raise ProposalReviewLookupUnavailable(
                    "Current source proposals are unavailable."
                )
            identifiers.add(proposal.proposal_id)
            proposals.append(proposal)
        return tuple(proposals)

    def _reconcile_locked(
        self,
        poc_id: str,
        proposals: Tuple[SourceBoundProposal, ...],
    ) -> None:
        new_fingerprints = dict(self._fingerprints)
        new_owners = dict(self._proposal_owners)
        additional = 0

        for proposal in proposals:
            owner = new_owners.get(proposal.proposal_id)
            if owner is not None and owner != poc_id:
                raise ProposalReviewCrossPOC(
                    "The proposal is unavailable beneath this POC."
                )
            key = (poc_id, proposal.proposal_id)
            fingerprint = _proposal_fingerprint(proposal)
            known = new_fingerprints.get(key)
            if known is not None and known != fingerprint:
                raise ProposalReviewStaleProposal(
                    "A known proposal changed its immutable source binding."
                )
            if known is None:
                additional += 1
                new_fingerprints[key] = fingerprint
                new_owners[proposal.proposal_id] = poc_id

        if len(self._fingerprints) + additional > self._max_known_proposals:
            raise ProposalReviewCapacityExceeded(
                "The process-local proposal store is at capacity."
            )
        self._fingerprints = new_fingerprints
        self._proposal_owners = new_owners

    def _current_locked(
        self,
        poc_id: str,
        proposals: Tuple[SourceBoundProposal, ...],
        proposal_id: str,
    ) -> SourceBoundProposal:
        for proposal in proposals:
            if proposal.proposal_id == proposal_id:
                current_ids = self._authoring_current_proposals.get(
                    (poc_id, proposal.source_receipt_id)
                )
                if current_ids is not None and proposal_id not in current_ids:
                    raise ProposalReviewStaleProposal(
                        "The proposal is no longer current beneath this POC."
                    )
                return proposal
        owner = self._proposal_owners.get(proposal_id)
        if owner is not None and owner != poc_id:
            raise ProposalReviewCrossPOC(
                "The proposal is unavailable beneath this POC."
            )
        if (poc_id, proposal_id) in self._fingerprints:
            raise ProposalReviewStaleProposal(
                "The proposal is no longer current beneath this POC."
            )
        raise ProposalReviewProposalUnavailable(
            "The proposal is unavailable beneath this POC."
        )

    def list_proposals(self, poc_id: str) -> Tuple[ProposalReviewItem, ...]:
        """Return current proposals with immutable human decisions overlaid."""

        validated_poc_id = _validate_poc_id(poc_id)
        proposals = self._lookup(validated_poc_id)
        with self._lock:
            self._reconcile_locked(validated_poc_id, proposals)
            items = []
            for proposal in proposals:
                receipt = self._decisions.get((validated_poc_id, proposal.proposal_id))
                review_state = (
                    ProposalReviewState.NEEDS_REVIEW
                    if receipt is None
                    else ProposalReviewState(receipt.decision.value)
                )
                items.append(
                    ProposalReviewItem(
                        poc_id=proposal.poc_id,
                        proposal_id=proposal.proposal_id,
                        source_receipt_id=proposal.source_receipt_id,
                        source_kind=proposal.source_kind,
                        source_quote=proposal.source_quote,
                        normalized_claim=proposal.normalized_claim,
                        review_state=review_state,
                        decision=receipt,
                    )
                )
            return tuple(items)

    def source_has_decision(self, poc_id: str, source_receipt_id: str) -> bool:
        """Return whether any current proposal for a source was triaged."""

        if type(source_receipt_id) is not str or not source_receipt_id.strip():
            raise ProposalReviewInvalid("The source receipt identifier is invalid.")
        return any(
            item.source_receipt_id == source_receipt_id and item.decision is not None
            for item in self.list_proposals(poc_id)
        )

    @contextmanager
    def authoring_commit_guard(
        self,
        poc_id: str,
        source_receipt_id: str,
    ):
        """Hold review state while A3 validates and commits a source replacement.

        Proposal lookup occurs before taking the review lock, matching ``decide``
        and avoiding a callback-under-lock cycle.  Once held, this guard blocks
        decisions for the source and records the committed A3 proposal IDs so a
        decision that looked up the old A2 queue cannot commit stale truth after
        the guard releases.
        """

        validated_poc_id = _validate_poc_id(poc_id)
        if (
            type(source_receipt_id) is not str
            or not re.fullmatch(SOURCE_RECEIPT_ID_PATTERN, source_receipt_id)
        ):
            raise ProposalReviewInvalid("The source receipt identifier is invalid.")
        proposals = self._lookup(validated_poc_id)
        source_key = (validated_poc_id, source_receipt_id)
        with self._lock:
            self._reconcile_locked(validated_poc_id, proposals)
            if source_key in self._authoring_guards:
                raise ProposalReviewDecisionConflict(
                    "Authoring is already committing this source."
                )
            if any(
                proposal.source_receipt_id == source_receipt_id
                and (validated_poc_id, proposal.proposal_id) in self._decisions
                for proposal in proposals
            ):
                raise ProposalReviewDecisionConflict(
                    "The source already has an immutable human decision."
                )
            self._authoring_guards.add(source_key)
            guard = _AuthoringCommitGuard(self, source_key)
            try:
                yield guard
            finally:
                self._authoring_guards.discard(source_key)

    def decide(
        self,
        poc_id: str,
        proposal_id: str,
        decision: ProposalDecision,
        reviewer: str,
        rationale: str,
        idempotency_key: str,
    ) -> ProposalDecisionResult:
        """Create or safely replay one immutable human triage decision."""

        validated_poc_id = _validate_poc_id(poc_id)
        validated_proposal_id = _validate_proposal_id(proposal_id)
        if type(decision) is not ProposalDecision:
            raise ProposalReviewInvalid("The proposal decision is invalid.")
        normalized_reviewer = _safe_text(
            reviewer,
            field_name="reviewer",
            maximum=MAX_REVIEWER_LENGTH,
            single_line=True,
        )
        normalized_rationale = _safe_text(
            rationale,
            field_name="rationale",
            maximum=MAX_RATIONALE_LENGTH,
        )
        key_digest = _idempotency_key_digest(idempotency_key)
        request_sha256 = _decision_request_sha256(
            poc_id=validated_poc_id,
            proposal_id=validated_proposal_id,
            decision=decision,
            reviewer=normalized_reviewer,
            rationale=normalized_rationale,
        )
        proposals = self._lookup(validated_poc_id)

        with self._lock:
            self._reconcile_locked(validated_poc_id, proposals)
            if any(
                proposal.proposal_id == validated_proposal_id
                and (validated_poc_id, proposal.source_receipt_id)
                in self._authoring_guards
                for proposal in proposals
            ):
                raise ProposalReviewDecisionConflict(
                    "Authoring is committing this source."
                )
            proposal = self._current_locked(
                validated_poc_id,
                proposals,
                validated_proposal_id,
            )
            prior_key = self._idempotency.get(key_digest)
            if prior_key is not None:
                if (
                    prior_key.request_sha256 != request_sha256
                    or prior_key.poc_id != validated_poc_id
                    or prior_key.proposal_id != validated_proposal_id
                ):
                    raise ProposalReviewIdempotencyConflict(
                        "The idempotency key does not match its original request."
                    )
                return ProposalDecisionResult(
                    receipt=self._decisions[(prior_key.poc_id, prior_key.proposal_id)],
                    disposition=ProposalDecisionDisposition.IDEMPOTENT_REPLAY,
                )

            decision_key = (
                validated_poc_id,
                validated_proposal_id,
            )
            prior_decision = self._decisions.get(decision_key)
            if prior_decision is not None:
                if (
                    prior_decision.decision != decision
                    or prior_decision.reviewer != normalized_reviewer
                    or prior_decision.rationale != normalized_rationale
                ):
                    raise ProposalReviewDecisionConflict(
                        "The proposal already has an immutable decision."
                    )
                if len(self._idempotency) >= self._max_idempotency_records:
                    raise ProposalReviewCapacityExceeded(
                        "The process-local idempotency store is at capacity."
                    )
                new_idempotency = dict(self._idempotency)
                new_idempotency[key_digest] = _IdempotencyRecord(
                    request_sha256=request_sha256,
                    poc_id=validated_poc_id,
                    proposal_id=validated_proposal_id,
                )
                self._idempotency = new_idempotency
                return ProposalDecisionResult(
                    receipt=prior_decision,
                    disposition=ProposalDecisionDisposition.DECISION_REPLAY,
                )

            if len(self._decisions) >= self._max_decisions:
                raise ProposalReviewCapacityExceeded(
                    "The process-local decision store is at capacity."
                )
            if len(self._idempotency) >= self._max_idempotency_records:
                raise ProposalReviewCapacityExceeded(
                    "The process-local idempotency store is at capacity."
                )
            decided_at = self._clock()
            try:
                _require_aware_timestamp(decided_at)
            except (TypeError, ValueError) as error:
                raise ProposalReviewLookupUnavailable(
                    "The decision clock is unavailable."
                ) from error

            receipt = ProposalDecisionReceipt(
                poc_id=validated_poc_id,
                proposal_id=validated_proposal_id,
                source_receipt_id=proposal.source_receipt_id,
                source_kind=proposal.source_kind,
                decision=decision,
                reviewer=normalized_reviewer,
                rationale=normalized_rationale,
                decided_at=decided_at,
            )
            new_decisions = dict(self._decisions)
            new_decisions[decision_key] = receipt
            new_idempotency = dict(self._idempotency)
            new_idempotency[key_digest] = _IdempotencyRecord(
                request_sha256=request_sha256,
                poc_id=validated_poc_id,
                proposal_id=validated_proposal_id,
            )
            self._decisions = new_decisions
            self._idempotency = new_idempotency
            return ProposalDecisionResult(
                receipt=receipt,
                disposition=ProposalDecisionDisposition.CREATED,
            )


__all__ = [
    "MAX_IDEMPOTENCY_KEY_LENGTH",
    "MAX_NORMALIZED_CLAIM_LENGTH",
    "MAX_RATIONALE_LENGTH",
    "MAX_REVIEWER_LENGTH",
    "MAX_SOURCE_QUOTE_LENGTH",
    "PROPOSAL_ID_PATTERN",
    "ProposalDecision",
    "ProposalDecisionDisposition",
    "ProposalDecisionReceipt",
    "ProposalDecisionResult",
    "ProposalReviewCapacityExceeded",
    "ProposalReviewCrossPOC",
    "ProposalReviewDecisionConflict",
    "ProposalReviewError",
    "ProposalReviewIdempotencyConflict",
    "ProposalReviewInvalid",
    "ProposalReviewItem",
    "ProposalReviewLookupUnavailable",
    "ProposalReviewProposalUnavailable",
    "ProposalReviewSemantics",
    "ProposalReviewStaleProposal",
    "ProposalReviewState",
    "ProcessLocalProposalReviewService",
    "SOURCE_RECEIPT_ID_PATTERN",
    "SourceBoundProposal",
    "derive_proposal_id",
]
