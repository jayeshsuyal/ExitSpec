"""Process-local capability and evidence-method planning for Train A A4.

The planner consumes the existing A3 retained-proposal projection.  It creates
no contract and has no confirmation, freeze, execution, evidence, verdict, or
deployment authority.  Capability and adapter decisions come only from the
small server-owned registry below plus explicit named-human inputs; source and
proposal text is display material, never a capability declaration.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
import hashlib
import hmac
import json
import math
import re
from threading import RLock
from typing import Any, Callable, ClassVar, Literal, Mapping, Sequence
import unicodedata

from pydantic import ConfigDict, Field, field_validator, model_validator

from .assisted_authoring import RetainedProposalProjection
from .canonical import canonical_json_bytes
from .models import FrozenExitSpecModel, SHA256_PATTERN
from .poc_creation import POC_ID_PATTERN
from .poc_proposal_review import PROPOSAL_ID_PATTERN, SOURCE_RECEIPT_ID_PATTERN
from .poc_sources import SourceKind
from .redaction import RedactionBoundaryError, assert_redaction_egress, redact_transcript


CAPABILITY_PLAN_SCHEMA_VERSION = "exitspec.capability-plan.v1"
CAPABILITY_PLAN_ID_PATTERN = r"^cplan_[a-f0-9]{32}$"
PLANNING_ITEM_ID_PATTERN = r"^cpitem_[a-f0-9]{32}$"
MAX_PLAN_VERSION = 10_000
MAX_ITEMS_PER_PLAN = 1_024
MAX_TEXT_LENGTH = 2_000
MAX_SINGLE_LINE_LENGTH = 160
MAX_IDEMPOTENCY_KEY_LENGTH = 200
MAX_THRESHOLD = 1_000_000_000.0

_POC_ID_RE = re.compile(POC_ID_PATTERN)
_PROPOSAL_ID_RE = re.compile(PROPOSAL_ID_PATTERN)
_SOURCE_RECEIPT_RE = re.compile(SOURCE_RECEIPT_ID_PATTERN)
_PLAN_ID_RE = re.compile(CAPABILITY_PLAN_ID_PATTERN)
_ITEM_ID_RE = re.compile(PLANNING_ITEM_ID_PATTERN)


class PlanningScope(StrEnum):
    MUST_HAVE = "MUST_HAVE"
    ADVISORY = "ADVISORY"


class PlanningDisposition(StrEnum):
    EXECUTABLE = "EXECUTABLE"
    EVIDENCE_IMPORT = "EVIDENCE_IMPORT"
    CLARIFICATION_REQUIRED = "CLARIFICATION_REQUIRED"
    UNSUPPORTED = "UNSUPPORTED"


class PlanningProvenance(StrEnum):
    SOURCE_EXTRACTED = "SOURCE_EXTRACTED"
    HUMAN_DECLARED = "HUMAN_DECLARED"
    ADAPTER_PROFILE_DECLARED = "ADAPTER_PROFILE_DECLARED"


class CapabilityPlanningError(RuntimeError):
    """Base class for fail-closed planner errors."""

    http_status: ClassVar[int] = 500


class CapabilityPlanningInvalid(CapabilityPlanningError):
    http_status = 400


class CapabilityPlanningLookupUnavailable(CapabilityPlanningError):
    http_status = 503


class CapabilityPlanningProposalUnavailable(CapabilityPlanningError):
    http_status = 404


class CapabilityPlanningCrossPOC(CapabilityPlanningProposalUnavailable):
    pass


class CapabilityPlanningStaleProposal(CapabilityPlanningError):
    http_status = 409


class CapabilityPlanningConflict(CapabilityPlanningError):
    http_status = 409


class CapabilityPlanningIdempotencyConflict(CapabilityPlanningError):
    http_status = 409


class CapabilityPlanningCapacityExceeded(CapabilityPlanningError):
    http_status = 503


def _safe_text(value: object, field_name: str, maximum: int, *, single_line: bool = False) -> str:
    if type(value) is not str:
        raise ValueError(f"{field_name} must be text.")
    normalized = unicodedata.normalize("NFC", value.replace("\r\n", "\n").replace("\r", "\n")).strip()
    if not normalized or len(normalized) > maximum:
        raise ValueError(f"{field_name} is outside its supported bounds.")
    if single_line and "\n" in normalized:
        raise ValueError(f"{field_name} must be a single line.")
    if any(unicodedata.category(c).startswith("C") for c in normalized if c != "\n"):
        raise ValueError(f"{field_name} contains a control character.")
    try:
        redacted = assert_redaction_egress(redact_transcript(normalized))
    except (TypeError, ValueError, RedactionBoundaryError):
        raise ValueError(f"{field_name} did not pass the redaction boundary.") from None
    if redacted != normalized:
        raise ValueError(f"{field_name} must contain redacted text only.")
    return normalized


def _safe_identifier(value: object, field_name: str, maximum: int) -> str:
    """Validate a bounded server/human identifier without treating it as source prose."""

    if type(value) is not str:
        raise ValueError(f"{field_name} must be text.")
    normalized = unicodedata.normalize("NFC", value).strip()
    if not normalized or len(normalized) > maximum or any(
        unicodedata.category(c).startswith("C") for c in normalized
    ):
        raise ValueError(f"{field_name} is outside its supported bounds.")
    return normalized


def _finite_threshold(value: object) -> float:
    if isinstance(value, bool) or type(value) not in {int, float}:
        raise ValueError("threshold must be a finite number.")
    number = float(value)
    if not math.isfinite(number) or abs(number) > MAX_THRESHOLD:
        raise ValueError("threshold is outside its supported bounds.")
    return number


class PlannerCriterionInput(FrozenExitSpecModel):
    """Optional human-completed criterion fields; missing fields stay visible."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    rule: str | None = Field(default=None, max_length=160)
    operator: str | None = Field(default=None, max_length=32)
    threshold: float | None = Field(default=None, allow_inf_nan=False)
    unit: str | None = Field(default=None, max_length=64)
    measurement_population: str | None = Field(default=None, max_length=300)
    evidence_method: str | None = Field(default=None, max_length=100)
    adapter: str | None = Field(default=None, max_length=160)
    adapter_version: str | None = Field(default=None, max_length=64)
    evidence_profile: str | None = Field(default=None, max_length=200)
    provenance: PlanningProvenance | None = None

    @field_validator(
        "rule", "operator", "unit", "measurement_population", "evidence_method",
        "adapter", "adapter_version", "evidence_profile", mode="before",
    )
    @classmethod
    def normalize_optional_text(cls, value: object, info: Any) -> str | None:
        if value is None:
            return None
        return _safe_identifier(value, info.field_name, 300)

    @field_validator("threshold", mode="before")
    @classmethod
    def validate_threshold(cls, value: object) -> float | None:
        return None if value is None else _finite_threshold(value)


class PlannerItemInput(FrozenExitSpecModel):
    """One retained proposal's named-human planning input."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    proposal_id: str = Field(pattern=PROPOSAL_ID_PATTERN)
    scope: PlanningScope
    capability_key: str
    criterion: PlannerCriterionInput | None = None
    reviewer: str
    rationale: str
    explicit_exclusion: bool = False

    @field_validator("capability_key", mode="before")
    @classmethod
    def validate_capability_key(cls, value: object) -> str:
        return _safe_text(value, "capability_key", 160, single_line=True)

    @field_validator("reviewer", mode="before")
    @classmethod
    def validate_reviewer(cls, value: object) -> str:
        return _safe_text(value, "reviewer", MAX_SINGLE_LINE_LENGTH, single_line=True)

    @field_validator("rationale", mode="before")
    @classmethod
    def validate_rationale(cls, value: object) -> str:
        return _safe_text(value, "rationale", MAX_TEXT_LENGTH)

    @model_validator(mode="after")
    def require_exclusion_rationale(self) -> "PlannerItemInput":
        if self.explicit_exclusion and not self.rationale.strip():
            raise ValueError("An explicit exclusion requires rationale.")
        if self.explicit_exclusion and self.criterion is not None and any(
            value is not None
            for value in self.criterion.model_dump(mode="python").values()
        ):
            raise ValueError("An explicit exclusion cannot carry criterion fields.")
        return self


class CapabilityRegistryEntry(FrozenExitSpecModel):
    """A server-owned, immutable adapter/profile capability declaration."""

    capability_key: str
    label: str
    supported_disposition: PlanningDisposition
    rule: str
    allowed_operators: tuple[str, ...]
    unit: str
    measurement_population: str
    evidence_method: str
    adapter: str
    adapter_version: str
    evidence_profile: str | None = None

    model_config = ConfigDict(extra="forbid", frozen=True)

    @field_validator("capability_key", "label", "rule", "unit", "measurement_population", "evidence_method", "adapter", "adapter_version", mode="before")
    @classmethod
    def validate_registry_text(cls, value: object, info: Any) -> str:
        return _safe_identifier(value, info.field_name, 300)

    @field_validator("allowed_operators", mode="before")
    @classmethod
    def validate_operators(cls, value: object) -> tuple[str, ...]:
        if type(value) not in {tuple, list} or not value:
            raise ValueError("allowed_operators is invalid.")
        result = tuple(_safe_identifier(item, "operator", 32) for item in value)
        if len(set(result)) != len(result):
            raise ValueError("allowed_operators must be unique.")
        return result

    @field_validator("evidence_profile", mode="before")
    @classmethod
    def validate_profile(cls, value: object) -> str | None:
        return None if value is None else _safe_identifier(value, "evidence_profile", 200)


def default_capability_registry() -> tuple[CapabilityRegistryEntry, ...]:
    """Return the narrow known capability/profile identities for A4."""

    return (
        CapabilityRegistryEntry(
            capability_key="exact_tool_selection",
            label="Exact tool selection",
            supported_disposition=PlanningDisposition.EXECUTABLE,
            rule="exact_tool_selection_rate",
            allowed_operators=("GTE", "GT"),
            unit="PROPORTION",
            measurement_population="approved_synthetic_cases",
            evidence_method="EXIT_SPEC_STREAMING_PROBE",
            adapter="deterministic_tool_selection",
            adapter_version="1.0.0",
        ),
        CapabilityRegistryEntry(
            capability_key="inference_performance_external",
            label="External inference performance profile",
            supported_disposition=PlanningDisposition.EVIDENCE_IMPORT,
            rule="ttft_p95",
            allowed_operators=("LT", "LTE"),
            unit="MILLISECONDS",
            measurement_population="successful_measured_attempts_with_valid_ttft",
            evidence_method="EXTERNAL_EVIDENCE_BUNDLE",
            adapter="vllm_bench_serve",
            adapter_version="1.0.0",
            evidence_profile="inferdrome.managed-vllm-0.26-evidence-profile.v1",
        ),
    )


class PlanningRecord(FrozenExitSpecModel):
    """Exactly one versioned, source-bound planning record per retained claim."""

    schema_version: Literal[CAPABILITY_PLAN_SCHEMA_VERSION] = CAPABILITY_PLAN_SCHEMA_VERSION
    planning_item_id: str = Field(pattern=PLANNING_ITEM_ID_PATTERN)
    poc_id: str = Field(pattern=POC_ID_PATTERN)
    proposal_id: str = Field(pattern=PROPOSAL_ID_PATTERN)
    source_receipt_id: str = Field(pattern=SOURCE_RECEIPT_ID_PATTERN)
    source_kind: SourceKind
    authoring_receipt_id: str = Field(min_length=1, max_length=160)
    authoring_result_id: str = Field(min_length=1, max_length=160)
    source_id: str = Field(min_length=1, max_length=160)
    source_content_sha256: str = Field(pattern=SHA256_PATTERN)
    source_revision: int = Field(ge=1)
    source_quote: str = Field(min_length=1, max_length=4_000)
    normalized_claim: str = Field(min_length=1, max_length=2_000)
    scope: PlanningScope
    capability_key: str
    disposition: PlanningDisposition
    criterion: PlannerCriterionInput | None = None
    reason: str
    next_action: str
    reviewer: str
    rationale: str
    explicit_exclusion: bool = False

    @field_validator("source_quote", "normalized_claim", "reason", "next_action", "rationale", mode="before")
    @classmethod
    def validate_redacted_text(cls, value: object, info: Any) -> str:
        return _safe_text(value, info.field_name, 4_000 if info.field_name == "source_quote" else MAX_TEXT_LENGTH)

    @field_validator("capability_key", "reviewer", mode="before")
    @classmethod
    def validate_short_text(cls, value: object, info: Any) -> str:
        return _safe_text(value, info.field_name, MAX_SINGLE_LINE_LENGTH, single_line=True)

    @model_validator(mode="after")
    def validate_record_disposition(self) -> "PlanningRecord":
        if self.explicit_exclusion and (
            self.disposition is not PlanningDisposition.UNSUPPORTED
            or self.criterion is not None
        ):
            raise ValueError(
                "An explicit exclusion must remain an unsupported record without a criterion."
            )
        if self.disposition in {
            PlanningDisposition.EXECUTABLE,
            PlanningDisposition.EVIDENCE_IMPORT,
        }:
            entry = next(
                (candidate for candidate in default_capability_registry() if candidate.capability_key == self.capability_key),
                None,
            )
            if entry is None or entry.supported_disposition is not self.disposition:
                raise ValueError("The supported disposition does not match the server-owned capability registry.")
            required = (
                "rule",
                "operator",
                "threshold",
                "unit",
                "measurement_population",
                "evidence_method",
                "adapter",
                "adapter_version",
                "provenance",
            )
            if self.criterion is None or any(
                getattr(self.criterion, field) is None for field in required
            ):
                raise ValueError("A supported planning record requires a complete criterion.")
            if self.disposition is PlanningDisposition.EVIDENCE_IMPORT:
                if self.criterion.evidence_profile != entry.evidence_profile:
                    raise ValueError("An evidence-import record requires an evidence profile.")
            elif self.criterion.evidence_profile is not None:
                raise ValueError("An executable record cannot carry an evidence profile.")
            if (
                self.criterion.rule != entry.rule
                or self.criterion.operator not in entry.allowed_operators
                or self.criterion.unit != entry.unit
                or self.criterion.measurement_population != entry.measurement_population
                or self.criterion.evidence_method != entry.evidence_method
                or self.criterion.adapter != entry.adapter
                or self.criterion.adapter_version != entry.adapter_version
            ):
                raise ValueError("The supported record does not match the server-owned capability policy.")
        if self.disposition is PlanningDisposition.UNSUPPORTED and self.criterion is not None:
            raise ValueError("An unsupported record cannot carry a criterion.")
        if self.disposition in {PlanningDisposition.CLARIFICATION_REQUIRED, PlanningDisposition.UNSUPPORTED} and not self.reason.strip():
            raise ValueError("An unresolved planning record requires a safe reason.")
        return self


class CapabilityPlan(FrozenExitSpecModel):
    """Immutable process-local planner version.

    ``created_at`` is deliberately not part of the deterministic plan or item
    identities; the material records, POC, and plan version are.
    """

    schema_version: Literal[CAPABILITY_PLAN_SCHEMA_VERSION] = CAPABILITY_PLAN_SCHEMA_VERSION
    plan_id: str = Field(pattern=CAPABILITY_PLAN_ID_PATTERN)
    poc_id: str = Field(pattern=POC_ID_PATTERN)
    plan_version: int = Field(ge=1, le=MAX_PLAN_VERSION)
    created_at: datetime
    records: tuple[PlanningRecord, ...] = Field(min_length=1, max_length=MAX_ITEMS_PER_PLAN)
    ready_for_agreement: bool
    authority_scope: Literal["PLANNING_ONLY"] = "PLANNING_ONLY"
    may_confirm: Literal[False] = False
    may_freeze: Literal[False] = False
    may_execute: Literal[False] = False
    may_import_evidence: Literal[False] = False
    may_issue_verdict: Literal[False] = False
    may_authorize_deployment: Literal[False] = False

    @field_validator("created_at")
    @classmethod
    def require_aware_timestamp(cls, value: datetime) -> datetime:
        if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("created_at must be timezone-aware.")
        return value

    @model_validator(mode="after")
    def require_unique_records(self) -> "CapabilityPlan":
        ids = [record.proposal_id for record in self.records]
        if len(set(ids)) != len(ids):
            raise ValueError("A plan cannot contain duplicate proposal records.")
        for record in self.records:
            if record.poc_id != self.poc_id:
                raise ValueError("Every planning record must belong to its parent POC.")
            record_payload = {
                "poc_id": self.poc_id,
                "proposal_id": record.proposal_id,
                "scope": record.scope.value,
                "capability_key": record.capability_key,
                "disposition": record.disposition.value,
                "criterion": None if record.criterion is None else record.criterion.model_dump(mode="json"),
                "reason": record.reason,
                "next_action": record.next_action,
                "reviewer": record.reviewer,
                "rationale": record.rationale,
                "explicit_exclusion": record.explicit_exclusion,
            }
            if _item_id(self.poc_id, record.proposal_id, self.plan_version, record_payload) != record.planning_item_id:
                raise ValueError("A planning item identity does not match its material record.")
        if _plan_id(self.poc_id, self.plan_version, self.records) != self.plan_id:
            raise ValueError("The plan identity does not match its material records.")
        if self.ready_for_agreement != all(
            record.disposition not in {PlanningDisposition.CLARIFICATION_REQUIRED, PlanningDisposition.UNSUPPORTED}
            or record.scope is PlanningScope.ADVISORY
            or record.explicit_exclusion
            for record in self.records
        ):
            raise ValueError("ready_for_agreement does not match planner records.")
        return self


class CapabilityPlanSemantics(FrozenExitSpecModel):
    storage_scope: Literal["PROCESS_LOCAL"] = "PROCESS_LOCAL"
    survives_process_restart: Literal[False] = False
    shared_across_workers: Literal[False] = False
    planning_is_agreement: Literal[False] = False
    planning_is_contract: Literal[False] = False
    can_confirm: Literal[False] = False
    can_freeze: Literal[False] = False
    can_execute: Literal[False] = False
    can_import_evidence: Literal[False] = False
    can_issue_verdict: Literal[False] = False
    can_authorize_deployment: Literal[False] = False


@dataclass(frozen=True, slots=True)
class _IdempotencyRecord:
    request_sha256: str
    plan_id: str


@dataclass(frozen=True, slots=True)
class CapabilityPlanResult:
    plan: CapabilityPlan
    idempotent_replay: bool


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _idempotency_digest(value: object) -> str:
    if type(value) is not str or not value.strip() or len(value) > MAX_IDEMPOTENCY_KEY_LENGTH:
        raise CapabilityPlanningInvalid("The idempotency key is invalid.")
    return hashlib.sha256(b"exitspec-capability-plan-idempotency-v1\x00" + value.encode()).hexdigest()


def _request_digest(poc_id: str, items: tuple[PlannerItemInput, ...]) -> str:
    return hashlib.sha256(
        b"exitspec-capability-plan-request-v1\x00"
        + canonical_json_bytes({"poc_id": poc_id, "items": [item.model_dump(mode="json") for item in items]})
    ).hexdigest()


def _item_id(poc_id: str, proposal_id: str, plan_version: int, record: Mapping[str, Any]) -> str:
    digest = hashlib.sha256(
        b"exitspec-capability-planning-item-v1\x00"
        + canonical_json_bytes({"poc_id": poc_id, "plan_version": plan_version, "proposal_id": proposal_id, "record": dict(record)})
    ).hexdigest()
    return f"cpitem_{digest[:32]}"


def _plan_id(poc_id: str, plan_version: int, records: Sequence[PlanningRecord]) -> str:
    digest = hashlib.sha256(
        b"exitspec-capability-plan-v1\x00"
        + canonical_json_bytes({"poc_id": poc_id, "plan_version": plan_version, "records": [record.model_dump(mode="json") for record in records]})
    ).hexdigest()
    return f"cplan_{digest[:32]}"


def _retained_fingerprint(proposal: RetainedProposalProjection) -> str:
    return hashlib.sha256(
        b"exitspec-retained-proposal-for-planning-v1\x00"
        + canonical_json_bytes(proposal.model_dump(mode="json"))
    ).hexdigest()


class ProcessLocalCapabilityPlannerService:
    """Thread-safe, bounded, non-durable planner over current A3 retention."""

    __slots__ = (
        "_clock", "_fingerprints", "_idempotency", "_lock", "_max_plans",
        "_max_proposals_per_poc", "_plan_fingerprints", "_plans", "_proposal_lookup",
        "_proposal_owners", "_registry",
    )

    def __init__(
        self,
        *,
        proposal_lookup: Callable[[str], Sequence[RetainedProposalProjection]],
        registry: Sequence[CapabilityRegistryEntry] | None = None,
        max_plans: int = 10_000,
        max_proposals_per_poc: int = MAX_ITEMS_PER_PLAN,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        if not callable(proposal_lookup) or not callable(clock):
            raise TypeError("proposal_lookup and clock must be callable.")
        if type(max_plans) is not int or not 1 <= max_plans <= 100_000:
            raise ValueError("max_plans is outside its supported bounds.")
        if type(max_proposals_per_poc) is not int or not 1 <= max_proposals_per_poc <= MAX_ITEMS_PER_PLAN:
            raise ValueError("max_proposals_per_poc is outside its supported bounds.")
        selected = tuple(default_capability_registry())
        if registry is not None and tuple(registry) != selected:
            raise ValueError("A4 accepts only the frozen server-owned capability registry.")
        self._proposal_lookup = proposal_lookup
        self._registry = selected
        self._clock = clock
        self._max_plans = max_plans
        self._max_proposals_per_poc = max_proposals_per_poc
        self._plans: dict[str, CapabilityPlan] = {}
        self._idempotency: dict[tuple[str, str], _IdempotencyRecord] = {}
        self._fingerprints: dict[tuple[str, str], str] = {}
        self._plan_fingerprints: dict[str, tuple[tuple[str, str], ...]] = {}
        self._proposal_owners: dict[str, str] = {}
        self._lock = RLock()

    @property
    def registry(self) -> tuple[CapabilityRegistryEntry, ...]:
        return self._registry

    @property
    def semantics(self) -> CapabilityPlanSemantics:
        return CapabilityPlanSemantics()

    def _lookup(self, poc_id: str) -> tuple[RetainedProposalProjection, ...]:
        try:
            raw = self._proposal_lookup(poc_id)
        except CapabilityPlanningError:
            raise
        except Exception as error:
            raise CapabilityPlanningLookupUnavailable("Retained proposal projection is unavailable.") from error
        if not isinstance(raw, (tuple, list)) or len(raw) > self._max_proposals_per_poc:
            raise CapabilityPlanningLookupUnavailable("Retained proposal projection is unavailable.")
        result: list[RetainedProposalProjection] = []
        ids: set[str] = set()
        for proposal in raw:
            if type(proposal) is not RetainedProposalProjection or proposal.poc_id != poc_id:
                raise CapabilityPlanningLookupUnavailable("Retained proposal projection is unavailable.")
            if proposal.proposal_id in ids:
                raise CapabilityPlanningLookupUnavailable("Retained proposal projection is ambiguous.")
            ids.add(proposal.proposal_id)
            result.append(proposal)
        return tuple(result)

    def _reconcile_locked(self, poc_id: str, proposals: tuple[RetainedProposalProjection, ...]) -> None:
        owners = dict(self._proposal_owners)
        fingerprints = dict(self._fingerprints)
        new_owners = 0
        for proposal in proposals:
            owner = owners.get(proposal.proposal_id)
            if owner is not None and owner != poc_id:
                raise CapabilityPlanningCrossPOC("The proposal is unavailable beneath this POC.")
            if owner is None:
                owners[proposal.proposal_id] = poc_id
                new_owners += 1
            key = (poc_id, proposal.proposal_id)
            fingerprint = _retained_fingerprint(proposal)
            known = fingerprints.get(key)
            if known is not None and not hmac.compare_digest(known, fingerprint):
                raise CapabilityPlanningStaleProposal("A retained proposal changed its immutable source binding.")
            if known is None:
                fingerprints[key] = fingerprint
        if len(self._proposal_owners) + new_owners > self._max_plans * self._max_proposals_per_poc:
            raise CapabilityPlanningCapacityExceeded("Planner proposal capacity has been reached.")
        self._proposal_owners = owners
        self._fingerprints = fingerprints

    def current_retained(self, poc_id: str) -> tuple[RetainedProposalProjection, ...]:
        if type(poc_id) is not str or _POC_ID_RE.fullmatch(poc_id) is None:
            raise CapabilityPlanningInvalid("The POC identifier is invalid.")
        proposals = self._lookup(poc_id)
        with self._lock:
            self._reconcile_locked(poc_id, proposals)
        return proposals

    def _current_plan_status(self, poc_id: str) -> tuple[CapabilityPlan | None, bool]:
        proposals = self._lookup(poc_id)
        with self._lock:
            try:
                self._reconcile_locked(poc_id, proposals)
            except CapabilityPlanningStaleProposal:
                return None, True
            latest = max(
                (plan for plan in self._plans.values() if plan.poc_id == poc_id),
                key=lambda plan: plan.plan_version,
                default=None,
            )
            if latest is None:
                return None, False
            current = tuple(sorted(
                ((proposal.proposal_id, _retained_fingerprint(proposal)) for proposal in proposals),
                key=lambda item: item[0],
            ))
            planned = self._plan_fingerprints.get(latest.plan_id, ())
            applicable = len(current) == len(planned) and all(
                left[0] == right[0] and hmac.compare_digest(left[1], right[1])
                for left, right in zip(current, planned, strict=True)
            )
            return (latest if applicable else None), not applicable

    def current_plan_status(self, poc_id: str) -> tuple[CapabilityPlan | None, bool]:
        """Return the applicable current plan and whether a replan is required."""

        if type(poc_id) is not str or _POC_ID_RE.fullmatch(poc_id) is None:
            raise CapabilityPlanningInvalid("The POC identifier is invalid.")
        return self._current_plan_status(poc_id)

    def require_current(self, poc_id: str) -> CapabilityPlan:
        """Return only a plan bound to the exact current retained proposal set."""

        current, needs_replan = self.current_plan_status(poc_id)
        if current is not None:
            return current
        if needs_replan:
            raise CapabilityPlanningStaleProposal("The current capability plan no longer matches retained proposals.")
        raise CapabilityPlanningProposalUnavailable("No current capability plan exists.")

    def _classify(self, proposal: RetainedProposalProjection, item: PlannerItemInput) -> tuple[PlanningDisposition, PlannerCriterionInput | None, str, str]:
        entry = next((candidate for candidate in self._registry if candidate.capability_key == item.capability_key), None)
        if entry is None:
            if item.criterion is not None and any(value is not None for value in item.criterion.model_dump(mode="python").values()):
                raise CapabilityPlanningInvalid("An unknown capability cannot carry adapter or profile claims.")
            return (PlanningDisposition.UNSUPPORTED, None, "Unsupported capability boundary: no server-owned registry entry exists for this capability.", "Name a supported capability or explicitly exclude this claim in a new reviewed plan version.")
        if item.explicit_exclusion:
            return (PlanningDisposition.UNSUPPORTED, None, "Explicitly excluded by the named human; this claim remains visible and cannot enter an executable contract.", "Resolve the exclusion in a new reviewed plan version if the requirement changes.")
        if item.criterion is not None:
            supplied = item.criterion
            if supplied.adapter is not None and supplied.adapter != entry.adapter:
                raise CapabilityPlanningInvalid("The adapter is unknown, forged, or incompatible with the registry policy.")
            if supplied.adapter_version is not None and supplied.adapter_version != entry.adapter_version:
                raise CapabilityPlanningInvalid("The adapter version is unknown, forged, or incompatible with the registry policy.")
            if supplied.evidence_profile is not None and supplied.evidence_profile != entry.evidence_profile:
                raise CapabilityPlanningInvalid("The evidence profile is unknown, forged, or incompatible with the registry policy.")
        criterion = item.criterion
        required = (
            "rule", "operator", "threshold", "unit", "measurement_population",
            "evidence_method", "adapter", "adapter_version", "provenance",
        )
        missing = [field for field in required if criterion is None or getattr(criterion, field) is None]
        if missing:
            return (PlanningDisposition.CLARIFICATION_REQUIRED, criterion, "Missing named-human planning input: " + ", ".join(missing) + ".", "Provide each named field from the server-owned capability registry; do not infer it from source text.")
        assert criterion is not None
        if criterion.rule != entry.rule or criterion.operator not in entry.allowed_operators or criterion.unit != entry.unit or criterion.measurement_population != entry.measurement_population or criterion.evidence_method != entry.evidence_method or criterion.adapter != entry.adapter or criterion.adapter_version != entry.adapter_version:
            raise CapabilityPlanningInvalid("The criterion does not match the selected server-owned capability policy.")
        if entry.supported_disposition is PlanningDisposition.EVIDENCE_IMPORT and criterion.evidence_profile != entry.evidence_profile:
            raise CapabilityPlanningInvalid("The evidence profile is unknown, forged, or incompatible with the registry policy.")
        if entry.supported_disposition is PlanningDisposition.EXECUTABLE and criterion.evidence_profile is not None:
            raise CapabilityPlanningInvalid("An executable policy cannot declare an external evidence profile.")
        if criterion.provenance is PlanningProvenance.SOURCE_EXTRACTED and not proposal.source_quote:
            raise CapabilityPlanningInvalid("Source-extracted provenance requires a source-bound quote.")
        return (entry.supported_disposition, criterion, "Complete criterion matches the server-owned capability and evidence policy.", "A later reviewed lifecycle step may decide whether to use this plan; planning does not authorize that step.")

    def plan_with_status(self, poc_id: str, items: Sequence[PlannerItemInput], *, idempotency_key: str) -> CapabilityPlanResult:
        if type(poc_id) is not str or _POC_ID_RE.fullmatch(poc_id) is None:
            raise CapabilityPlanningInvalid("The POC identifier is invalid.")
        if type(items) not in {tuple, list} or not items or len(items) > self._max_proposals_per_poc or any(type(item) is not PlannerItemInput for item in items):
            raise CapabilityPlanningInvalid("The planning item set is invalid.")
        normalized = tuple(items)
        if len({item.proposal_id for item in normalized}) != len(normalized):
            raise CapabilityPlanningInvalid("Planning items must contain unique proposal IDs.")
        key_digest = _idempotency_digest(idempotency_key)
        request_sha256 = _request_digest(poc_id, normalized)
        created_at = self._clock()
        try:
            if type(created_at) is not datetime or created_at.tzinfo is None or created_at.utcoffset() is None:
                raise ValueError
        except (TypeError, ValueError):
            raise CapabilityPlanningLookupUnavailable("The planner clock is unavailable.") from None
        proposals = self._lookup(poc_id)
        current_by_id = {proposal.proposal_id: proposal for proposal in proposals}
        with self._lock:
            self._reconcile_locked(poc_id, proposals)
            expected = set(current_by_id)
            actual = {item.proposal_id for item in normalized}
            if actual != expected:
                if actual - expected:
                    for proposal_id in actual - expected:
                        owner = self._proposal_owners.get(proposal_id)
                        if owner is not None and owner != poc_id:
                            raise CapabilityPlanningCrossPOC("The proposal is unavailable beneath this POC.")
                raise CapabilityPlanningProposalUnavailable("Every current retained proposal must appear exactly once.")
            prior = self._idempotency.get((poc_id, key_digest))
            if prior is not None:
                if prior.request_sha256 != request_sha256 or prior.plan_id not in self._plans:
                    raise CapabilityPlanningIdempotencyConflict("The idempotency key does not match its original planning request.")
                return CapabilityPlanResult(self._plans[prior.plan_id], True)
            plan_version = 1 + max((plan.plan_version for plan in self._plans.values() if plan.poc_id == poc_id), default=0)
            if plan_version > MAX_PLAN_VERSION or len(self._plans) >= self._max_plans:
                raise CapabilityPlanningCapacityExceeded("Planner plan capacity has been reached.")
            records: list[PlanningRecord] = []
            for item in normalized:
                proposal = current_by_id[item.proposal_id]
                disposition, criterion, reason, next_action = self._classify(proposal, item)
                record_payload = {
                    "poc_id": poc_id, "proposal_id": proposal.proposal_id,
                    "scope": item.scope.value, "capability_key": item.capability_key,
                    "disposition": disposition.value, "criterion": None if criterion is None else criterion.model_dump(mode="json"),
                    "reason": reason, "next_action": next_action, "reviewer": item.reviewer,
                    "rationale": item.rationale, "explicit_exclusion": item.explicit_exclusion,
                }
                records.append(PlanningRecord(
                    planning_item_id=_item_id(poc_id, proposal.proposal_id, plan_version, record_payload),
                    poc_id=poc_id, proposal_id=proposal.proposal_id, source_receipt_id=proposal.source_receipt_id,
                    source_kind=proposal.source_kind, authoring_receipt_id=proposal.authoring_receipt_id,
                    authoring_result_id=proposal.authoring_result_id, source_id=proposal.source_id,
                    source_content_sha256=proposal.source_content_sha256, source_revision=proposal.source_revision,
                    source_quote=proposal.source_quote, normalized_claim=proposal.normalized_claim,
                    scope=item.scope, capability_key=item.capability_key, disposition=disposition,
                    criterion=criterion, reason=reason, next_action=next_action, reviewer=item.reviewer,
                    rationale=item.rationale, explicit_exclusion=item.explicit_exclusion,
                ))
            ready = all(record.scope is PlanningScope.ADVISORY or record.disposition not in {PlanningDisposition.CLARIFICATION_REQUIRED, PlanningDisposition.UNSUPPORTED} or record.explicit_exclusion for record in records)
            plan = CapabilityPlan(plan_id=_plan_id(poc_id, plan_version, records), poc_id=poc_id, plan_version=plan_version, created_at=created_at, records=tuple(records), ready_for_agreement=ready)
            self._plans[plan.plan_id] = plan
            self._plan_fingerprints[plan.plan_id] = tuple(sorted(
                ((proposal.proposal_id, _retained_fingerprint(proposal)) for proposal in proposals),
                key=lambda item: item[0],
            ))
            self._idempotency[(poc_id, key_digest)] = _IdempotencyRecord(request_sha256=request_sha256, plan_id=plan.plan_id)
            return CapabilityPlanResult(plan, False)

    def plan(self, poc_id: str, items: Sequence[PlannerItemInput], *, idempotency_key: str) -> CapabilityPlan:
        return self.plan_with_status(poc_id, items, idempotency_key=idempotency_key).plan

    def latest(self, poc_id: str) -> CapabilityPlan | None:
        plans = tuple(plan for plan in self._plans.values() if plan.poc_id == poc_id)
        return max(plans, key=lambda plan: plan.plan_version, default=None)

    def plans(self, poc_id: str) -> tuple[CapabilityPlan, ...]:
        return tuple(sorted((plan for plan in self._plans.values() if plan.poc_id == poc_id), key=lambda plan: plan.plan_version))


# Friendly aliases for callers that use the shorter A4 vocabulary.
CapabilityPlanScope = PlanningScope
CapabilityPlanDisposition = PlanningDisposition
CapabilityPlanItemInput = PlannerItemInput
CapabilityPlannerService = ProcessLocalCapabilityPlannerService


__all__ = [
    "CAPABILITY_PLAN_SCHEMA_VERSION", "CapabilityPlan", "CapabilityPlanDisposition",
    "CapabilityPlanResult",
    "CapabilityPlanItemInput", "CapabilityPlanScope", "CapabilityPlanSemantics",
    "CapabilityPlannerService", "CapabilityPlanningCapacityExceeded", "CapabilityPlanningConflict",
    "CapabilityPlanningCrossPOC", "CapabilityPlanningError", "CapabilityPlanningIdempotencyConflict",
    "CapabilityPlanningInvalid", "CapabilityPlanningLookupUnavailable", "CapabilityPlanningProposalUnavailable",
    "CapabilityPlanningStaleProposal", "CapabilityRegistryEntry", "PlannerCriterionInput",
    "PlannerItemInput", "PlanningDisposition", "PlanningProvenance", "PlanningRecord",
    "PlanningScope", "ProcessLocalCapabilityPlannerService", "default_capability_registry",
]
