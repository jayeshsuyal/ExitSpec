"""Process-local definition of one bounded inference-performance criterion.

This domain consumes a fresh proposal-review projection and accepts only a
proposal explicitly kept for later contract work.  A definition receipt records
human authoring intent.  It is not customer confirmation, a frozen contract,
permission to execute, evidence, or a verdict.

The service is deliberately process-local, bounded, and fail-closed.  Every
create attempt, including an idempotent replay, rechecks the proposal's current
source and review binding before returning a receipt.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import hashlib
import hmac
import math
import re
from threading import RLock
from typing import Any, Callable, ClassVar, Literal, Mapping, Sequence, Self, Tuple
import unicodedata

from pydantic import Field, field_validator, model_validator

from .canonical import canonical_json_bytes
from .models import FrozenExitSpecModel, SHA256_PATTERN
from .poc_creation import POC_ID_PATTERN
from .poc_proposal_review import (
    MAX_NORMALIZED_CLAIM_LENGTH,
    PROPOSAL_ID_PATTERN,
    SOURCE_RECEIPT_ID_PATTERN,
    ProposalDecision,
    ProposalReviewItem,
    ProposalReviewState,
    SourceBoundProposal,
)
from .poc_sources import SourceKind
from .redaction import (
    RedactionBoundaryError,
    assert_redaction_egress,
    redact_transcript,
)


CONTRACT_DEFINITION_ID_PATTERN = r"^cdef_[a-f0-9]{32}$"
MAX_REVIEWER_LENGTH = 160
MAX_RATIONALE_LENGTH = 2_000
MAX_IDEMPOTENCY_KEY_LENGTH = 200
MAX_PROMPT_TOKENS = 1_000_000
MAX_OUTPUT_TOKENS = 1_000_000
MAX_TTFT_P95_MS = 60_000.0
MAX_PERFORMANCE_SAMPLES = 1_000
MAX_PERFORMANCE_CONCURRENCY = 32

_DEFAULT_MAX_PROPOSALS_PER_POC = 1_024
_DEFAULT_MAX_KNOWN_PROPOSALS = 32_768
_DEFAULT_MAX_DEFINITIONS = 16_384
_DEFAULT_MAX_IDEMPOTENCY_RECORDS = 32_768
_MAX_CONFIGURABLE_PROPOSALS_PER_POC = 8_192
_MAX_CONFIGURABLE_KNOWN_PROPOSALS = 100_000
_MAX_CONFIGURABLE_DEFINITIONS = 100_000
_MAX_CONFIGURABLE_IDEMPOTENCY_RECORDS = 100_000

_POC_ID_RE = re.compile(POC_ID_PATTERN)
_PROPOSAL_ID_RE = re.compile(PROPOSAL_ID_PATTERN)
_DEFINITION_ID_RE = re.compile(CONTRACT_DEFINITION_ID_PATTERN)


class InferencePerformanceMetric(str, Enum):
    """The only metrics supported by the first contract-definition vertical."""

    TTFT_P95_MS = "TTFT_P95_MS"
    ERROR_RATE_PERCENT = "ERROR_RATE_PERCENT"


class ContractDefinitionOperator(str, Enum):
    """Explicit upper-bound comparisons supported by this vertical."""

    LT = "LT"
    LTE = "LTE"


class ContractDefinitionUnit(str, Enum):
    """Canonical unit derived from the selected metric."""

    MILLISECONDS = "MILLISECONDS"
    PERCENT = "PERCENT"


class ContractDefinitionDisposition(str, Enum):
    """Whether this exact idempotent operation created or replayed a receipt."""

    CREATED = "CREATED"
    IDEMPOTENT_REPLAY = "IDEMPOTENT_REPLAY"


class _FrozenContractDefinitionModel(FrozenExitSpecModel):
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


def _normalize_safe_text(
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
    try:
        normalized.encode("utf-8")
    except UnicodeError:
        raise ValueError("{0} must be valid UTF-8.".format(field_name)) from None
    try:
        redacted = assert_redaction_egress(redact_transcript(normalized))
    except (TypeError, ValueError, RedactionBoundaryError):
        raise ValueError(
            "{0} did not pass the redaction boundary.".format(field_name)
        ) from None
    if redacted != normalized:
        raise ValueError("{0} must contain redacted text only.".format(field_name))
    return normalized


def _require_exact_integer(
    value: object,
    *,
    field_name: str,
    minimum: int,
    maximum: int,
) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(
            "{0} must be an integer from {1} to {2}.".format(
                field_name,
                minimum,
                maximum,
            )
        )
    return value


def _require_finite_number(value: object) -> float:
    if type(value) not in (int, float):
        raise ValueError("threshold must be a finite number.")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError("threshold must be a finite number.")
    return numeric


class InferencePerformanceCriterionDefinition(_FrozenContractDefinitionModel):
    """Human-authored input for exactly one bounded performance criterion."""

    metric: InferencePerformanceMetric
    operator: ContractDefinitionOperator
    threshold: float = Field(allow_inf_nan=False)
    minimum_samples: int = Field(ge=1, le=MAX_PERFORMANCE_SAMPLES)
    concurrency: int = Field(ge=1, le=MAX_PERFORMANCE_CONCURRENCY)
    prompt_tokens_min: int = Field(ge=1, le=MAX_PROMPT_TOKENS)
    prompt_tokens_max: int = Field(ge=1, le=MAX_PROMPT_TOKENS)
    output_tokens_min: int = Field(ge=1, le=MAX_OUTPUT_TOKENS)
    output_tokens_max: int = Field(ge=1, le=MAX_OUTPUT_TOKENS)
    reviewer: str = Field(min_length=1, max_length=MAX_REVIEWER_LENGTH)
    rationale: str = Field(min_length=1, max_length=MAX_RATIONALE_LENGTH)

    @field_validator("metric", mode="before")
    @classmethod
    def require_exact_metric(cls, value: object) -> InferencePerformanceMetric:
        if type(value) is not InferencePerformanceMetric:
            raise ValueError("metric must be a supported metric enum.")
        return value

    @field_validator("operator", mode="before")
    @classmethod
    def require_exact_operator(cls, value: object) -> ContractDefinitionOperator:
        if type(value) is not ContractDefinitionOperator:
            raise ValueError("operator must be a supported operator enum.")
        return value

    @field_validator("threshold", mode="before")
    @classmethod
    def require_finite_threshold(cls, value: object) -> float:
        return _require_finite_number(value)

    @field_validator(
        "minimum_samples",
        "concurrency",
        "prompt_tokens_min",
        "prompt_tokens_max",
        "output_tokens_min",
        "output_tokens_max",
        mode="before",
    )
    @classmethod
    def require_exact_bounded_integers(
        cls,
        value: object,
        info: Any,
    ) -> int:
        bounds = {
            "minimum_samples": (1, MAX_PERFORMANCE_SAMPLES),
            "concurrency": (1, MAX_PERFORMANCE_CONCURRENCY),
            "prompt_tokens_min": (1, MAX_PROMPT_TOKENS),
            "prompt_tokens_max": (1, MAX_PROMPT_TOKENS),
            "output_tokens_min": (1, MAX_OUTPUT_TOKENS),
            "output_tokens_max": (1, MAX_OUTPUT_TOKENS),
        }
        minimum, maximum = bounds[info.field_name]
        return _require_exact_integer(
            value,
            field_name=info.field_name,
            minimum=minimum,
            maximum=maximum,
        )

    @field_validator("reviewer", mode="before")
    @classmethod
    def validate_reviewer(cls, value: object) -> str:
        return _normalize_safe_text(
            value,
            field_name="reviewer",
            maximum=MAX_REVIEWER_LENGTH,
            single_line=True,
        )

    @field_validator("rationale", mode="before")
    @classmethod
    def validate_rationale(cls, value: object) -> str:
        return _normalize_safe_text(
            value,
            field_name="rationale",
            maximum=MAX_RATIONALE_LENGTH,
        )

    @model_validator(mode="after")
    def enforce_metric_and_workload_bounds(
        self,
    ) -> "InferencePerformanceCriterionDefinition":
        if self.metric == InferencePerformanceMetric.TTFT_P95_MS:
            if not 0.0 < self.threshold <= MAX_TTFT_P95_MS:
                raise ValueError(
                    "TTFT_P95_MS threshold must be greater than 0 and at "
                    "most {0}.".format(int(MAX_TTFT_P95_MS))
                )
        elif self.operator == ContractDefinitionOperator.LT:
            if not 0.0 < self.threshold < 100.0:
                raise ValueError(
                    "Strict ERROR_RATE_PERCENT threshold must be greater "
                    "than 0 and less than 100."
                )
        else:
            raise ValueError(
                "ERROR_RATE_PERCENT supports only the strict LT operator."
            )
        if self.concurrency > self.minimum_samples:
            raise ValueError(
                "concurrency cannot exceed minimum_samples."
            )
        if self.prompt_tokens_min > self.prompt_tokens_max:
            raise ValueError(
                "prompt_tokens_min cannot exceed prompt_tokens_max."
            )
        if self.output_tokens_min > self.output_tokens_max:
            raise ValueError(
                "output_tokens_min cannot exceed output_tokens_max."
            )
        return self


def _unit_for(metric: InferencePerformanceMetric) -> ContractDefinitionUnit:
    if metric == InferencePerformanceMetric.TTFT_P95_MS:
        return ContractDefinitionUnit.MILLISECONDS
    return ContractDefinitionUnit.PERCENT


def _proposal_fingerprint(
    proposal: SourceBoundProposal | ProposalReviewItem,
) -> str:
    return hashlib.sha256(
        b"exitspec-contract-definition-proposal-v1\x00"
        + canonical_json_bytes(proposal.model_dump(mode="json"))
    ).hexdigest()


def _definition_payload(
    *,
    poc_id: str,
    proposal: ProposalReviewItem,
    proposal_sha256: str,
    criterion: InferencePerformanceCriterionDefinition,
) -> dict[str, object]:
    return {
        "criterion_type": "INFERENCE_PERFORMANCE_V1",
        "poc_id": poc_id,
        "proposal_id": proposal.proposal_id,
        "source_receipt_id": proposal.source_receipt_id,
        "source_kind": proposal.source_kind.value,
        "proposal_sha256": proposal_sha256,
        "normalized_claim": proposal.normalized_claim,
        "metric": criterion.metric.value,
        "unit": _unit_for(criterion.metric).value,
        "operator": criterion.operator.value,
        "threshold": criterion.threshold,
        "minimum_samples": criterion.minimum_samples,
        "concurrency": criterion.concurrency,
        "prompt_tokens_min": criterion.prompt_tokens_min,
        "prompt_tokens_max": criterion.prompt_tokens_max,
        "output_tokens_min": criterion.output_tokens_min,
        "output_tokens_max": criterion.output_tokens_max,
        "reviewer": criterion.reviewer,
        "rationale": criterion.rationale,
    }


def _definition_content_sha256(payload: Mapping[str, object]) -> str:
    return hashlib.sha256(
        b"exitspec-contract-definition-content-v1\x00"
        + canonical_json_bytes(dict(payload))
    ).hexdigest()


def _definition_id(content_sha256: str) -> str:
    return "cdef_{0}".format(content_sha256[:32])


def _receipt_sha256(
    *,
    definition_id: str,
    defined_at: datetime,
    payload: Mapping[str, object],
) -> str:
    return hashlib.sha256(
        b"exitspec-contract-definition-receipt-v1\x00"
        + canonical_json_bytes(
            {
                "definition": dict(payload),
                "definition_id": definition_id,
                "defined_at": defined_at.isoformat(),
            }
        )
    ).hexdigest()


class ContractDefinitionReceipt(_FrozenContractDefinitionModel):
    """Immutable, canonical record of human criterion definition only."""

    definition_id: str = Field(pattern=CONTRACT_DEFINITION_ID_PATTERN)
    definition_sha256: str = Field(pattern=SHA256_PATTERN)
    criterion_type: Literal["INFERENCE_PERFORMANCE_V1"] = (
        "INFERENCE_PERFORMANCE_V1"
    )
    poc_id: str = Field(pattern=POC_ID_PATTERN)
    proposal_id: str = Field(pattern=PROPOSAL_ID_PATTERN)
    source_receipt_id: str = Field(pattern=SOURCE_RECEIPT_ID_PATTERN)
    source_kind: SourceKind
    proposal_sha256: str = Field(pattern=SHA256_PATTERN)
    normalized_claim: str = Field(
        min_length=1,
        max_length=MAX_NORMALIZED_CLAIM_LENGTH,
    )
    metric: InferencePerformanceMetric
    unit: ContractDefinitionUnit
    operator: ContractDefinitionOperator
    threshold: float = Field(allow_inf_nan=False)
    minimum_samples: int = Field(ge=1, le=MAX_PERFORMANCE_SAMPLES)
    concurrency: int = Field(ge=1, le=MAX_PERFORMANCE_CONCURRENCY)
    prompt_tokens_min: int = Field(ge=1, le=MAX_PROMPT_TOKENS)
    prompt_tokens_max: int = Field(ge=1, le=MAX_PROMPT_TOKENS)
    output_tokens_min: int = Field(ge=1, le=MAX_OUTPUT_TOKENS)
    output_tokens_max: int = Field(ge=1, le=MAX_OUTPUT_TOKENS)
    reviewer: str = Field(min_length=1, max_length=MAX_REVIEWER_LENGTH)
    rationale: str = Field(min_length=1, max_length=MAX_RATIONALE_LENGTH)
    defined_at: datetime

    @field_validator("source_kind", mode="before")
    @classmethod
    def require_exact_source_kind(cls, value: object) -> SourceKind:
        if type(value) is not SourceKind:
            raise ValueError("source_kind must be a SourceKind enum.")
        return value

    @field_validator("metric", mode="before")
    @classmethod
    def require_exact_metric(cls, value: object) -> InferencePerformanceMetric:
        if type(value) is not InferencePerformanceMetric:
            raise ValueError("metric must be a supported metric enum.")
        return value

    @field_validator("unit", mode="before")
    @classmethod
    def require_exact_unit(cls, value: object) -> ContractDefinitionUnit:
        if type(value) is not ContractDefinitionUnit:
            raise ValueError("unit must be a supported unit enum.")
        return value

    @field_validator("operator", mode="before")
    @classmethod
    def require_exact_operator(cls, value: object) -> ContractDefinitionOperator:
        if type(value) is not ContractDefinitionOperator:
            raise ValueError("operator must be a supported operator enum.")
        return value

    @field_validator("threshold", mode="before")
    @classmethod
    def require_finite_threshold(cls, value: object) -> float:
        return _require_finite_number(value)

    @field_validator(
        "minimum_samples",
        "concurrency",
        "prompt_tokens_min",
        "prompt_tokens_max",
        "output_tokens_min",
        "output_tokens_max",
        mode="before",
    )
    @classmethod
    def require_exact_bounded_integers(
        cls,
        value: object,
        info: Any,
    ) -> int:
        bounds = {
            "minimum_samples": (1, MAX_PERFORMANCE_SAMPLES),
            "concurrency": (1, MAX_PERFORMANCE_CONCURRENCY),
            "prompt_tokens_min": (1, MAX_PROMPT_TOKENS),
            "prompt_tokens_max": (1, MAX_PROMPT_TOKENS),
            "output_tokens_min": (1, MAX_OUTPUT_TOKENS),
            "output_tokens_max": (1, MAX_OUTPUT_TOKENS),
        }
        minimum, maximum = bounds[info.field_name]
        return _require_exact_integer(
            value,
            field_name=info.field_name,
            minimum=minimum,
            maximum=maximum,
        )

    @field_validator("normalized_claim", mode="before")
    @classmethod
    def validate_normalized_claim(cls, value: object) -> str:
        return _normalize_safe_text(
            value,
            field_name="normalized_claim",
            maximum=MAX_NORMALIZED_CLAIM_LENGTH,
        )

    @field_validator("reviewer", mode="before")
    @classmethod
    def validate_reviewer(cls, value: object) -> str:
        return _normalize_safe_text(
            value,
            field_name="reviewer",
            maximum=MAX_REVIEWER_LENGTH,
            single_line=True,
        )

    @field_validator("rationale", mode="before")
    @classmethod
    def validate_rationale(cls, value: object) -> str:
        return _normalize_safe_text(
            value,
            field_name="rationale",
            maximum=MAX_RATIONALE_LENGTH,
        )

    @field_validator("defined_at", mode="before")
    @classmethod
    def require_aware_timestamp(cls, value: object) -> datetime:
        if (
            type(value) is not datetime
            or value.tzinfo is None
            or value.utcoffset() is None
        ):
            raise ValueError("defined_at must be timezone-aware.")
        return value

    @model_validator(mode="after")
    def verify_identity_and_bounds(self) -> "ContractDefinitionReceipt":
        criterion = InferencePerformanceCriterionDefinition(
            metric=self.metric,
            operator=self.operator,
            threshold=self.threshold,
            minimum_samples=self.minimum_samples,
            concurrency=self.concurrency,
            prompt_tokens_min=self.prompt_tokens_min,
            prompt_tokens_max=self.prompt_tokens_max,
            output_tokens_min=self.output_tokens_min,
            output_tokens_max=self.output_tokens_max,
            reviewer=self.reviewer,
            rationale=self.rationale,
        )
        if self.unit != _unit_for(self.metric):
            raise ValueError("unit does not match metric.")
        payload = {
            "criterion_type": self.criterion_type,
            "poc_id": self.poc_id,
            "proposal_id": self.proposal_id,
            "source_receipt_id": self.source_receipt_id,
            "source_kind": self.source_kind.value,
            "proposal_sha256": self.proposal_sha256,
            "normalized_claim": self.normalized_claim,
            "metric": criterion.metric.value,
            "unit": self.unit.value,
            "operator": criterion.operator.value,
            "threshold": criterion.threshold,
            "minimum_samples": criterion.minimum_samples,
            "concurrency": criterion.concurrency,
            "prompt_tokens_min": criterion.prompt_tokens_min,
            "prompt_tokens_max": criterion.prompt_tokens_max,
            "output_tokens_min": criterion.output_tokens_min,
            "output_tokens_max": criterion.output_tokens_max,
            "reviewer": criterion.reviewer,
            "rationale": criterion.rationale,
        }
        content_sha256 = _definition_content_sha256(payload)
        if not hmac.compare_digest(
            self.definition_id,
            _definition_id(content_sha256),
        ):
            raise ValueError("definition_id does not match the receipt.")
        expected_receipt_sha256 = _receipt_sha256(
            definition_id=self.definition_id,
            defined_at=self.defined_at,
            payload=payload,
        )
        if not hmac.compare_digest(
            self.definition_sha256,
            expected_receipt_sha256,
        ):
            raise ValueError("definition_sha256 does not match the receipt.")
        return self


class ContractDefinitionResult(_FrozenContractDefinitionModel):
    """Definition response that never exposes the raw idempotency key."""

    receipt: ContractDefinitionReceipt
    disposition: ContractDefinitionDisposition

    @property
    def created(self) -> bool:
        return self.disposition == ContractDefinitionDisposition.CREATED

    @property
    def replayed(self) -> bool:
        return self.disposition == ContractDefinitionDisposition.IDEMPOTENT_REPLAY


class ContractDefinitionSemantics(_FrozenContractDefinitionModel):
    """Machine-readable process scope and zero-authority guarantees."""

    storage_scope: Literal["PROCESS_LOCAL"] = "PROCESS_LOCAL"
    survives_process_restart: Literal[False] = False
    shared_across_workers: Literal[False] = False
    definition_is_customer_confirmation: Literal[False] = False
    definition_is_frozen_contract: Literal[False] = False
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
    max_definitions: int = Field(
        ge=1,
        le=_MAX_CONFIGURABLE_DEFINITIONS,
    )
    max_idempotency_records: int = Field(
        ge=1,
        le=_MAX_CONFIGURABLE_IDEMPOTENCY_RECORDS,
    )


class ContractDefinitionError(RuntimeError):
    """Base class for content-free contract-definition failures."""

    http_status: ClassVar[int] = 500


class ContractDefinitionInvalid(ContractDefinitionError):
    """The caller supplied an invalid bounded domain request."""

    http_status = 400


class ContractDefinitionLookupUnavailable(ContractDefinitionError):
    """The current proposal-review projection could not be trusted."""

    http_status = 503


class ContractDefinitionProposalUnavailable(ContractDefinitionError, KeyError):
    """The proposal is not current beneath the requested POC."""

    http_status = 404


class ContractDefinitionCrossPOC(ContractDefinitionProposalUnavailable):
    """A proposal identifier is owned by a different POC."""


class ContractDefinitionProposalNotKept(ContractDefinitionError):
    """The current proposal lacks KEEP_FOR_CONTRACT human triage."""

    http_status = 409


class ContractDefinitionStaleProposal(ContractDefinitionError):
    """A known kept proposal disappeared or changed its immutable binding."""

    http_status = 409


class ContractDefinitionConflict(ContractDefinitionError):
    """The proposal already owns an immutable criterion definition."""

    http_status = 409


class ContractDefinitionIdempotencyConflict(ContractDefinitionError):
    """An idempotency key was reused for a different definition request."""

    http_status = 409


class ContractDefinitionCapacityExceeded(ContractDefinitionError):
    """A bounded process-local store reached its configured capacity."""

    http_status = 503


@dataclass(frozen=True, slots=True)
class _IdempotencyRecord:
    request_sha256: str
    poc_id: str
    proposal_id: str
    definition_id: str


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _validate_poc_id(value: object) -> str:
    if type(value) is not str or _POC_ID_RE.fullmatch(value) is None:
        raise ContractDefinitionInvalid("The POC identifier is invalid.")
    return value


def _validate_proposal_id(value: object) -> str:
    if type(value) is not str or _PROPOSAL_ID_RE.fullmatch(value) is None:
        raise ContractDefinitionInvalid("The proposal identifier is invalid.")
    return value


def _validate_definition_id(value: object) -> str:
    if type(value) is not str or _DEFINITION_ID_RE.fullmatch(value) is None:
        raise ContractDefinitionInvalid("The definition identifier is invalid.")
    return value


def _validate_capacity(
    value: object,
    *,
    field_name: str,
    maximum: int,
) -> int:
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValueError("{0} is outside its supported bounds.".format(field_name))
    return value


def _idempotency_key_digest(value: object) -> str:
    if type(value) is not str:
        raise ContractDefinitionInvalid("The idempotency key is invalid.")
    if (
        not value
        or value != value.strip()
        or len(value) > MAX_IDEMPOTENCY_KEY_LENGTH
    ):
        raise ContractDefinitionInvalid(
            "The idempotency key is outside its supported bounds."
        )
    for character in value:
        if unicodedata.category(character).startswith("C"):
            raise ContractDefinitionInvalid(
                "The idempotency key is outside its supported bounds."
            )
    try:
        encoded = value.encode("utf-8")
    except UnicodeError:
        raise ContractDefinitionInvalid(
            "The idempotency key is outside its supported bounds."
        ) from None
    return hashlib.sha256(
        b"exitspec-contract-definition-idempotency-v1\x00" + encoded
    ).hexdigest()


def _request_sha256(
    *,
    poc_id: str,
    proposal_id: str,
    criterion: InferencePerformanceCriterionDefinition,
) -> str:
    return hashlib.sha256(
        b"exitspec-contract-definition-request-v1\x00"
        + canonical_json_bytes(
            {
                "poc_id": poc_id,
                "proposal_id": proposal_id,
                "criterion": criterion.model_dump(mode="json"),
            }
        )
    ).hexdigest()


class ProcessLocalContractDefinitionService:
    """Thread-safe, bounded authoring over current kept proposal-review items."""

    __slots__ = (
        "_clock",
        "_definitions",
        "_definitions_by_proposal",
        "_fingerprints",
        "_idempotency",
        "_lock",
        "_max_definitions",
        "_max_idempotency_records",
        "_max_known_proposals",
        "_max_proposals_per_poc",
        "_proposal_lookup",
        "_proposal_owners",
    )

    def __init__(
        self,
        *,
        proposal_lookup: Callable[
            [str],
            Sequence[SourceBoundProposal | ProposalReviewItem],
        ],
        max_proposals_per_poc: int = _DEFAULT_MAX_PROPOSALS_PER_POC,
        max_known_proposals: int = _DEFAULT_MAX_KNOWN_PROPOSALS,
        max_definitions: int = _DEFAULT_MAX_DEFINITIONS,
        max_idempotency_records: int = _DEFAULT_MAX_IDEMPOTENCY_RECORDS,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        if not callable(proposal_lookup):
            raise TypeError("proposal_lookup must be callable.")
        if not callable(clock):
            raise TypeError("clock must be callable.")
        self._max_proposals_per_poc = _validate_capacity(
            max_proposals_per_poc,
            field_name="max_proposals_per_poc",
            maximum=_MAX_CONFIGURABLE_PROPOSALS_PER_POC,
        )
        self._max_known_proposals = _validate_capacity(
            max_known_proposals,
            field_name="max_known_proposals",
            maximum=_MAX_CONFIGURABLE_KNOWN_PROPOSALS,
        )
        self._max_definitions = _validate_capacity(
            max_definitions,
            field_name="max_definitions",
            maximum=_MAX_CONFIGURABLE_DEFINITIONS,
        )
        self._max_idempotency_records = _validate_capacity(
            max_idempotency_records,
            field_name="max_idempotency_records",
            maximum=_MAX_CONFIGURABLE_IDEMPOTENCY_RECORDS,
        )
        self._proposal_lookup = proposal_lookup
        self._clock = clock
        self._definitions: dict[str, ContractDefinitionReceipt] = {}
        self._definitions_by_proposal: dict[tuple[str, str], str] = {}
        self._fingerprints: dict[tuple[str, str], str] = {}
        self._proposal_owners: dict[str, str] = {}
        self._idempotency: dict[str, _IdempotencyRecord] = {}
        self._lock = RLock()

    @property
    def semantics(self) -> ContractDefinitionSemantics:
        return ContractDefinitionSemantics(
            max_proposals_per_poc=self._max_proposals_per_poc,
            max_known_proposals=self._max_known_proposals,
            max_definitions=self._max_definitions,
            max_idempotency_records=self._max_idempotency_records,
        )

    def __len__(self) -> int:
        with self._lock:
            return len(self._definitions)

    def _lookup(
        self,
        poc_id: str,
    ) -> Tuple[SourceBoundProposal | ProposalReviewItem, ...]:
        try:
            raw = self._proposal_lookup(poc_id)
        except Exception as error:
            raise ContractDefinitionLookupUnavailable(
                "Current proposal review is unavailable."
            ) from error
        if not isinstance(raw, (tuple, list)):
            raise ContractDefinitionLookupUnavailable(
                "Current proposal review is unavailable."
            )
        if len(raw) > self._max_proposals_per_poc:
            raise ContractDefinitionCapacityExceeded(
                "The POC proposal projection exceeds process capacity."
            )
        proposals: list[SourceBoundProposal | ProposalReviewItem] = []
        identifiers: set[str] = set()
        for proposal in raw:
            if type(proposal) not in (SourceBoundProposal, ProposalReviewItem):
                raise ContractDefinitionLookupUnavailable(
                    "Current proposal review is unavailable."
                )
            if proposal.poc_id != poc_id or proposal.proposal_id in identifiers:
                raise ContractDefinitionLookupUnavailable(
                    "Current proposal review is unavailable."
                )
            identifiers.add(proposal.proposal_id)
            proposals.append(proposal)
        return tuple(proposals)

    @staticmethod
    def _is_kept(
        proposal: SourceBoundProposal | ProposalReviewItem,
    ) -> bool:
        return (
            type(proposal) is ProposalReviewItem
            and proposal.review_state == ProposalReviewState.KEEP_FOR_CONTRACT
            and proposal.decision is not None
            and proposal.decision.decision
            == ProposalDecision.KEEP_FOR_CONTRACT
        )

    def _reconcile_locked(
        self,
        poc_id: str,
        proposals: Tuple[SourceBoundProposal | ProposalReviewItem, ...],
    ) -> None:
        new_fingerprints = dict(self._fingerprints)
        new_owners = dict(self._proposal_owners)
        additional_owners = 0
        for proposal in proposals:
            owner = new_owners.get(proposal.proposal_id)
            if owner is not None and owner != poc_id:
                raise ContractDefinitionCrossPOC(
                    "The proposal is unavailable beneath this POC."
                )
            if owner is None:
                additional_owners += 1
                new_owners[proposal.proposal_id] = poc_id
            key = (poc_id, proposal.proposal_id)
            if not self._is_kept(proposal):
                continue
            fingerprint = _proposal_fingerprint(proposal)
            known = new_fingerprints.get(key)
            if known is not None and not hmac.compare_digest(known, fingerprint):
                raise ContractDefinitionStaleProposal(
                    "A known kept proposal changed its immutable binding."
                )
            if known is None:
                new_fingerprints[key] = fingerprint
        if len(self._proposal_owners) + additional_owners > (
            self._max_known_proposals
        ):
            raise ContractDefinitionCapacityExceeded(
                "The process-local proposal store is at capacity."
            )
        self._fingerprints = new_fingerprints
        self._proposal_owners = new_owners

    def _current_kept_locked(
        self,
        poc_id: str,
        proposal_id: str,
        proposals: Tuple[SourceBoundProposal | ProposalReviewItem, ...],
    ) -> ProposalReviewItem:
        current = next(
            (
                proposal
                for proposal in proposals
                if proposal.proposal_id == proposal_id
            ),
            None,
        )
        if current is None:
            owner = self._proposal_owners.get(proposal_id)
            if owner is not None and owner != poc_id:
                raise ContractDefinitionCrossPOC(
                    "The proposal is unavailable beneath this POC."
                )
            if (poc_id, proposal_id) in self._fingerprints:
                raise ContractDefinitionStaleProposal(
                    "The known kept proposal is no longer current."
                )
            raise ContractDefinitionProposalUnavailable(
                "The proposal is unavailable beneath this POC."
            )
        if not self._is_kept(current):
            if (poc_id, proposal_id) in self._fingerprints:
                raise ContractDefinitionStaleProposal(
                    "The known kept proposal no longer has its original binding."
                )
            raise ContractDefinitionProposalNotKept(
                "The proposal must be kept for contract work first."
            )
        if type(current) is not ProposalReviewItem:
            raise ContractDefinitionProposalNotKept(
                "The proposal must be kept for contract work first."
            )
        return current

    def define(
        self,
        poc_id: str,
        proposal_id: str,
        criterion: InferencePerformanceCriterionDefinition,
        *,
        idempotency_key: str,
    ) -> ContractDefinitionResult:
        """Create or exactly replay one immutable human definition receipt."""

        validated_poc_id = _validate_poc_id(poc_id)
        validated_proposal_id = _validate_proposal_id(proposal_id)
        if type(criterion) is not InferencePerformanceCriterionDefinition:
            raise ContractDefinitionInvalid(
                "criterion must be an inference-performance definition."
            )
        key_digest = _idempotency_key_digest(idempotency_key)
        request_sha256 = _request_sha256(
            poc_id=validated_poc_id,
            proposal_id=validated_proposal_id,
            criterion=criterion,
        )
        proposals = self._lookup(validated_poc_id)

        with self._lock:
            self._reconcile_locked(validated_poc_id, proposals)
            proposal = self._current_kept_locked(
                validated_poc_id,
                validated_proposal_id,
                proposals,
            )
            prior_key = self._idempotency.get(key_digest)
            if prior_key is not None:
                if (
                    not hmac.compare_digest(
                        prior_key.request_sha256,
                        request_sha256,
                    )
                    or prior_key.poc_id != validated_poc_id
                    or prior_key.proposal_id != validated_proposal_id
                ):
                    raise ContractDefinitionIdempotencyConflict(
                        "The idempotency key does not match its original request."
                    )
                return ContractDefinitionResult(
                    receipt=self._definitions[prior_key.definition_id],
                    disposition=ContractDefinitionDisposition.IDEMPOTENT_REPLAY,
                )

            proposal_key = (validated_poc_id, validated_proposal_id)
            if proposal_key in self._definitions_by_proposal:
                raise ContractDefinitionConflict(
                    "The proposal already has an immutable criterion definition."
                )
            if len(self._definitions) >= self._max_definitions:
                raise ContractDefinitionCapacityExceeded(
                    "The process-local definition store is at capacity."
                )
            if len(self._idempotency) >= self._max_idempotency_records:
                raise ContractDefinitionCapacityExceeded(
                    "The process-local idempotency store is at capacity."
                )

            try:
                defined_at = self._clock()
            except Exception as error:
                raise ContractDefinitionLookupUnavailable(
                    "The definition clock is unavailable."
                ) from error
            if (
                type(defined_at) is not datetime
                or defined_at.tzinfo is None
                or defined_at.utcoffset() is None
            ):
                raise ContractDefinitionLookupUnavailable(
                    "The definition clock is unavailable."
                )
            proposal_sha256 = _proposal_fingerprint(proposal)
            payload = _definition_payload(
                poc_id=validated_poc_id,
                proposal=proposal,
                proposal_sha256=proposal_sha256,
                criterion=criterion,
            )
            content_sha256 = _definition_content_sha256(payload)
            definition_id = _definition_id(content_sha256)
            if definition_id in self._definitions:
                raise ContractDefinitionConflict(
                    "The derived definition identity is already in use."
                )
            receipt_sha256 = _receipt_sha256(
                definition_id=definition_id,
                defined_at=defined_at,
                payload=payload,
            )
            receipt_fields = dict(payload)
            receipt_fields.update(
                {
                    "source_kind": proposal.source_kind,
                    "metric": criterion.metric,
                    "unit": _unit_for(criterion.metric),
                    "operator": criterion.operator,
                }
            )
            receipt = ContractDefinitionReceipt(
                definition_id=definition_id,
                definition_sha256=receipt_sha256,
                defined_at=defined_at,
                **receipt_fields,
            )
            new_definitions = dict(self._definitions)
            new_definitions[definition_id] = receipt
            new_by_proposal = dict(self._definitions_by_proposal)
            new_by_proposal[proposal_key] = definition_id
            new_idempotency = dict(self._idempotency)
            new_idempotency[key_digest] = _IdempotencyRecord(
                request_sha256=request_sha256,
                poc_id=validated_poc_id,
                proposal_id=validated_proposal_id,
                definition_id=definition_id,
            )
            self._definitions = new_definitions
            self._definitions_by_proposal = new_by_proposal
            self._idempotency = new_idempotency
            return ContractDefinitionResult(
                receipt=receipt,
                disposition=ContractDefinitionDisposition.CREATED,
            )

    def get(self, definition_id: str) -> ContractDefinitionReceipt:
        """Return one immutable process-local definition receipt."""

        validated_id = _validate_definition_id(definition_id)
        with self._lock:
            try:
                return self._definitions[validated_id]
            except KeyError as error:
                raise ContractDefinitionProposalUnavailable(
                    "The definition is unavailable in this process."
                ) from error

    def definitions(self) -> Tuple[ContractDefinitionReceipt, ...]:
        """Return immutable receipts in stable identifier order."""

        with self._lock:
            return tuple(
                self._definitions[key] for key in sorted(self._definitions)
            )


__all__ = [
    "CONTRACT_DEFINITION_ID_PATTERN",
    "MAX_IDEMPOTENCY_KEY_LENGTH",
    "MAX_OUTPUT_TOKENS",
    "MAX_PERFORMANCE_CONCURRENCY",
    "MAX_PERFORMANCE_SAMPLES",
    "MAX_PROMPT_TOKENS",
    "MAX_RATIONALE_LENGTH",
    "MAX_REVIEWER_LENGTH",
    "MAX_TTFT_P95_MS",
    "ContractDefinitionCapacityExceeded",
    "ContractDefinitionConflict",
    "ContractDefinitionCrossPOC",
    "ContractDefinitionDisposition",
    "ContractDefinitionError",
    "ContractDefinitionIdempotencyConflict",
    "ContractDefinitionInvalid",
    "ContractDefinitionLookupUnavailable",
    "ContractDefinitionOperator",
    "ContractDefinitionProposalNotKept",
    "ContractDefinitionProposalUnavailable",
    "ContractDefinitionReceipt",
    "ContractDefinitionResult",
    "ContractDefinitionSemantics",
    "ContractDefinitionStaleProposal",
    "ContractDefinitionUnit",
    "InferencePerformanceCriterionDefinition",
    "InferencePerformanceMetric",
    "ProcessLocalContractDefinitionService",
]
