"""The smallest A6 executable-evidence seam.

This first slice intentionally handles one server-selected executable
capability.  Import, publication, closure, and transport layers build on this
immutable contract-bound result in later A6 patches.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import hmac
from pathlib import Path
import re
from threading import RLock
from enum import StrEnum
from typing import Callable, Literal, Sequence
from uuid import uuid4

from pydantic import Field, model_validator

from .adapters.deterministic_tool_selection import DeterministicToolSelectionAdapter
from .canonical import canonical_json_bytes
from .confirmations import (
    ContractConfirmation,
    contract_confirmation_fingerprint,
    require_affirmative_confirmation,
)
from .contracts import contract_digest, verify_contract_digest
from .fixtures import fixture_sha256, load_tool_selection_fixture
from .generic_evidence_pack import (
    GenericEvidencePackError,
    GenericEvidencePackPublication,
    publish_generic_evidence_pack,
    verify_generic_evidence_pack,
)
from .inferdrome_bundle import InferdromeBundleRejected, verify_inferdrome_bundle
from .inferdrome_catalog import InferdromeBundleCatalog, InferdromeCatalogNotFound
from .inferdrome_external_contract import ValidatedManagedContractContext
from .inferdrome_external_contract import (
    MANAGED_TARGET_ENDPOINT_CLASS,
    MANAGED_TARGET_PROVIDER,
)
from .inferdrome_managed_demo import MANAGED_DEMO_REQUEST_PLAN_DIGEST
from .inferdrome_managed_import import (
    InferdromeManagedImportRejected,
    _build_receipt,
    _evaluate_applicability,
    _evaluate_managed_verdict,
    _require_exact_retrospective_binding,
)
from .inferdrome_reporting_v2 import managed_receipt_sha256
from .models import (
    CapabilityCriterion,
    ExternalErrorRateRuleV1,
    ExternalTTFTP95RuleV1,
    ContractStatus,
    Criterion,
    ExactToolSelectionEvidencePolicy,
    FrozenExitSpecModel,
    InferdromeEvidenceIdentityV1,
    InferencePerformanceCriterionV3,
    ManagedTTFTEvidencePolicy,
    Metric,
    POCContract,
    ProportionMeasurement,
    ProportionRule,
    VerdictStatus,
    capability_evidence_policy_digest,
)
from .verdicts import evaluate_proportion_criterion
from .workspace_closure import (
    HumanClosureDecision,
    HumanPOCClosureRequest,
    POCClosureConflict,
    ProcessLocalPOCClosureService,
    TerminalEvidenceBinding,
    TerminalRunReceiptBinding,
)


EXECUTABLE_ORCHESTRATION_SCHEMA_VERSION = "exitspec.executable-orchestration.v1"
EXECUTABLE_WORKLOAD_PATH = "examples/support-agent/fixtures/tool-selection-200.json"
EXECUTABLE_WORKLOAD_SHA256 = (
    "75ef6f83450de100a920e9489a0b5966464f1dba2e3d339c4b57e64fb95d8271"
)
EXECUTABLE_SYNTHETIC_PROFILE = "exitspec.synthetic-tool-selection.pass.v1"
_MAX_IDEMPOTENCY_KEY_LENGTH = 200


class EvidenceMethod(StrEnum):
    EXECUTABLE = "EXECUTABLE"
    EVIDENCE_IMPORT = "EVIDENCE_IMPORT"


class EvidenceAttemptStatus(StrEnum):
    RESERVED = "RESERVED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    INGESTION_REJECTED = "INGESTION_REJECTED"
    FAILED_INTERNAL = "FAILED_INTERNAL"
    CANCELLED = "CANCELLED"
    STALE = "STALE"


class EvidenceMethodIdentity(FrozenExitSpecModel):
    method: EvidenceMethod
    policy_id: str = Field(min_length=1, max_length=200)
    policy_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    adapter_id: str = Field(min_length=1, max_length=200)
    adapter_version: str = Field(min_length=1, max_length=100)
    profile_id: str | None = None


class CriterionEvidenceResult(FrozenExitSpecModel):
    """One independently evaluated result before overall reduction."""

    criterion_id: str = Field(pattern=r"^[A-Z][A-Z0-9-]{2,63}$")
    scope: Literal["MUST_HAVE", "ADVISORY"]
    planning_disposition: Literal[
        "EXECUTABLE", "EVIDENCE_IMPORT", "CLARIFICATION_REQUIRED", "UNSUPPORTED"
    ]
    explicit_exclusion: bool = False
    ingestion_status: Literal["ADMITTED", "INGESTION_REJECTED"]
    verdict: VerdictStatus | None = None
    reason: str = Field(min_length=1, max_length=4_000)
    limitations: tuple[str, ...] = ()
    sample_count: int | None = Field(default=None, ge=0)
    success_count: int | None = Field(default=None, ge=0)
    evidence_ref: str | None = None
    evidence_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    calculation_id: str | None = None
    calculation_version: str | None = None
    applicability_codes: tuple[str, ...] = ()
    observed_ttft_p95_ns: int | None = Field(default=None, ge=0)
    observed_latency_population: str | None = None
    recalculation_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    receipt_id: str | None = None
    receipt_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    bundle_digest: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def validate_terminal_result(self) -> "CriterionEvidenceResult":
        if self.ingestion_status == "INGESTION_REJECTED":
            if self.verdict is not None:
                raise ValueError("INGESTION_REJECTED results cannot carry an acceptance verdict.")
        elif self.verdict is None:
            raise ValueError("ADMITTED terminal results require an acceptance verdict.")
        if (
            self.sample_count is not None
            and self.success_count is not None
            and self.success_count > self.sample_count
        ):
            raise ValueError("success_count cannot exceed sample_count.")
        return self


class EvidenceReduction(FrozenExitSpecModel):
    """Exact non-compensating MUST_HAVE reduction with visible limitations."""

    verdict: VerdictStatus
    must_have_criterion_ids: tuple[str, ...]
    advisory_non_pass_criterion_ids: tuple[str, ...] = ()
    explicit_exclusion_criterion_ids: tuple[str, ...] = ()
    unsupported_criterion_ids: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    reason: str = Field(min_length=1, max_length=4_000)


def reduce_criterion_results(
    criteria: Sequence[CapabilityCriterion],
    results: Sequence[CriterionEvidenceResult],
) -> EvidenceReduction:
    """Return NOT_PROVEN for identity ambiguity and apply fixed precedence."""

    if any(type(item) is not CapabilityCriterion for item in criteria):
        raise ExecutableOrchestrationInvalid("Reduction criteria are invalid.")
    if any(type(item) is not CriterionEvidenceResult for item in results):
        raise ExecutableOrchestrationInvalid("Criterion results are invalid.")
    expected = tuple(item for item in criteria if not item.explicit_exclusion)
    expected_ids = tuple(item.id for item in expected)
    all_ids = tuple(item.id for item in criteria)
    actual_ids = tuple(item.criterion_id for item in results)
    duplicate_ids = tuple(sorted({item for item in actual_ids if actual_ids.count(item) > 1}))
    extra_ids = tuple(sorted(set(actual_ids) - set(all_ids)))
    missing_ids = tuple(sorted(set(expected_ids) - set(actual_ids)))
    exclusions = tuple(item.id for item in criteria if item.explicit_exclusion)
    unsupported = tuple(
        item.id
        for item in expected
        if item.planning_disposition in {"UNSUPPORTED", "CLARIFICATION_REQUIRED"}
    )
    must_have = tuple(item.id for item in expected if item.must_have)
    by_id = {item.criterion_id: item for item in results}
    advisory_non_pass = tuple(
        item.id
        for item in expected
        if not item.must_have
        and by_id.get(item.id) is not None
        and by_id[item.id].verdict is not VerdictStatus.PASS
    )
    limitations = list(
        dict.fromkeys(
            limitation
            for item in results
            for limitation in item.limitations
        )
    )
    if duplicate_ids or extra_ids or missing_ids:
        limitations.append(
            "Every retained non-excluded claim requires exactly one terminal result; "
            "missing, duplicate, and extra identities cannot pass."
        )
        return EvidenceReduction(
            verdict=VerdictStatus.NOT_PROVEN,
            must_have_criterion_ids=must_have,
            advisory_non_pass_criterion_ids=advisory_non_pass,
            explicit_exclusion_criterion_ids=exclusions,
            unsupported_criterion_ids=unsupported,
            limitations=tuple(limitations),
            reason="Criterion result identity coverage is incomplete or ambiguous.",
        )
    mismatched_ids = tuple(
        item.id
        for item in criteria
        if item.id in by_id
        if (
            by_id[item.id].scope != item.planning_scope
            or by_id[item.id].planning_disposition != item.planning_disposition
            or by_id[item.id].explicit_exclusion != item.explicit_exclusion
        )
    )
    if mismatched_ids:
        limitations.append(
            "Result scope, disposition, and exclusion must match the frozen criterion."
        )
        return EvidenceReduction(
            verdict=VerdictStatus.NOT_PROVEN,
            must_have_criterion_ids=must_have,
            advisory_non_pass_criterion_ids=advisory_non_pass,
            explicit_exclusion_criterion_ids=exclusions,
            unsupported_criterion_ids=unsupported,
            limitations=tuple(dict.fromkeys(limitations)),
            reason="One or more criterion results disagree with frozen planning metadata.",
        )
    statuses = [by_id[item].verdict or VerdictStatus.NOT_PROVEN for item in must_have]
    if not must_have:
        verdict = VerdictStatus.NOT_PROVEN
        reason = "No non-excluded MUST_HAVE criterion is available to establish PASS."
    elif VerdictStatus.FAIL in statuses:
        verdict = VerdictStatus.FAIL
        reason = "At least one non-excluded MUST_HAVE criterion failed."
    elif VerdictStatus.BLOCKED in statuses:
        verdict = VerdictStatus.BLOCKED
        reason = "No MUST_HAVE criterion failed, but an external condition blocked evidence."
    elif VerdictStatus.NOT_PROVEN in statuses:
        verdict = VerdictStatus.NOT_PROVEN
        reason = "At least one non-excluded MUST_HAVE criterion is not proven."
    else:
        verdict = VerdictStatus.PASS
        reason = "Every non-excluded MUST_HAVE criterion passed under its frozen policy."
    if advisory_non_pass:
        limitations.append("Advisory non-pass results remain visible and do not gate PASS.")
    if exclusions:
        limitations.append("Explicit exclusions remain visible and do not gate PASS.")
    if unsupported:
        limitations.append("Unsupported or clarification-required claims remain visible and are not proven.")
    return EvidenceReduction(
        verdict=verdict,
        must_have_criterion_ids=must_have,
        advisory_non_pass_criterion_ids=advisory_non_pass,
        explicit_exclusion_criterion_ids=exclusions,
        unsupported_criterion_ids=unsupported,
        limitations=tuple(dict.fromkeys(limitations)),
        reason=reason,
    )


class ExecutableOrchestrationError(RuntimeError):
    """Base class for the executable A6 boundary."""


class ExecutableOrchestrationInvalid(ExecutableOrchestrationError):
    pass


class ExecutableOrchestrationConflict(ExecutableOrchestrationError):
    pass


class ExecutableEvidenceAttempt(FrozenExitSpecModel):
    """Immutable result of one server-owned executable attempt."""

    schema_version: str = EXECUTABLE_ORCHESTRATION_SCHEMA_VERSION
    attempt_id: str = Field(pattern=r"^eatm_[a-f0-9]{32}$")
    poc_id: str = Field(pattern=r"^poc_[a-z0-9][a-z0-9_-]{2,63}$")
    contract_id: str = Field(min_length=1, max_length=160)
    contract_version: str = Field(min_length=1, max_length=100)
    contract_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    confirmation_id: str = Field(pattern=r"^cnf_[a-f0-9]{64}$")
    confirmation_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    plan_id: str = Field(pattern=r"^cplan_[a-f0-9]{32}$")
    plan_version: int = Field(ge=1)
    plan_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    criterion_id: str = Field(pattern=r"^[A-Z][A-Z0-9-]{2,63}$")
    method: str = "EXECUTABLE"
    policy_id: str = Field(min_length=1)
    policy_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    adapter_id: str = Field(min_length=1)
    adapter_version: str = Field(min_length=1)
    execution_profile: str = EXECUTABLE_SYNTHETIC_PROFILE
    workload_path: str = EXECUTABLE_WORKLOAD_PATH
    workload_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    sample_count: int = Field(ge=0)
    success_count: int = Field(ge=0)
    observed_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    confidence_lower_bound: float | None = Field(default=None, ge=0.0, le=1.0)
    status: EvidenceAttemptStatus = EvidenceAttemptStatus.COMPLETED
    verdict: VerdictStatus | None = None
    reason: str = Field(min_length=1, max_length=4_000)
    limitations: tuple[str, ...] = ()
    evidence_ref: str = Field(min_length=1)
    calculation_id: str = "exitspec.statistics.wilson_lower_bound"
    calculation_version: str = "wilson-two-sided-v1"
    measured_at: datetime
    is_current: bool = True

    @model_validator(mode="after")
    def validate_measurement_counts(self) -> "ExecutableEvidenceAttempt":
        if self.success_count > self.sample_count:
            raise ValueError("success_count cannot exceed sample_count.")
        return self


@dataclass(frozen=True, slots=True)
class ExecutableStartResult:
    attempt: ExecutableEvidenceAttempt
    replayed: bool


@dataclass(slots=True)
class _Record:
    request_digest: str
    attempt: ExecutableEvidenceAttempt
    contract: POCContract
    confirmation: ContractConfirmation
    criterion: CapabilityCriterion


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _key_digest(value: object) -> str:
    if (
        type(value) is not str
        or not value
        or value != value.strip()
        or len(value) > _MAX_IDEMPOTENCY_KEY_LENGTH
    ):
        raise ExecutableOrchestrationInvalid("idempotency_key is invalid.")
    return hashlib.sha256(
        b"exitspec-executable-orchestration-idempotency-v1\x00"
        + value.encode("utf-8")
    ).hexdigest()


def _request_digest(
    poc_id: str,
    contract: POCContract,
    confirmation: ContractConfirmation,
    criterion: CapabilityCriterion,
) -> str:
    return hashlib.sha256(
        b"exitspec-executable-orchestration-request-v1\x00"
        + canonical_json_bytes(
            {
                "poc_id": poc_id,
                "contract": contract.model_dump(mode="json"),
                "confirmation": confirmation.model_dump(mode="json"),
                "criterion": criterion.model_dump(mode="json"),
                "selected_method": {
                    "method": EvidenceMethod.EXECUTABLE.value,
                    "adapter_id": criterion.evidence_binding.policy.adapter
                    if criterion.evidence_binding is not None
                    else None,
                    "adapter_version": criterion.evidence_binding.policy.adapter_version
                    if criterion.evidence_binding is not None
                    else None,
                    "profile_id": EXECUTABLE_SYNTHETIC_PROFILE,
                },
            }
        )
    ).hexdigest()


class ProcessLocalExecutableEvidenceService:
    """Process-local, idempotent execution over one frozen generic criterion."""

    def __init__(
        self,
        *,
        contract_lookup: Callable[[str], POCContract],
        confirmation_lookup: Callable[[str], ContractConfirmation],
        clock: Callable[[], datetime] = _now,
    ) -> None:
        if not callable(contract_lookup) or not callable(confirmation_lookup):
            raise TypeError("contract and confirmation lookups must be callable.")
        if not callable(clock):
            raise TypeError("clock must be callable.")
        self._contract_lookup = contract_lookup
        self._confirmation_lookup = confirmation_lookup
        self._clock = clock
        self._records: dict[str, _Record] = {}
        self._idempotency: dict[str, str] = {}
        self._current_by_poc: dict[str, str] = {}
        self._lock = RLock()

    def start(
        self,
        poc_id: object,
        *,
        acknowledgement: object,
        idempotency_key: object,
    ) -> ExecutableStartResult:
        if acknowledgement is not True:
            raise ExecutableOrchestrationInvalid(
                "Explicit evidence acknowledgement is required."
            )
        if type(poc_id) is not str:
            raise ExecutableOrchestrationInvalid("poc_id is invalid.")
        reserved = self.reserve(
            poc_id,
            acknowledgement=acknowledgement,
            idempotency_key=idempotency_key,
        )
        if reserved.replayed:
            return reserved
        return self.execute(reserved.attempt.attempt_id)

    def reserve(
        self,
        poc_id: object,
        *,
        acknowledgement: object,
        idempotency_key: object,
    ) -> ExecutableStartResult:
        if acknowledgement is not True:
            raise ExecutableOrchestrationInvalid(
                "Explicit evidence acknowledgement is required."
            )
        if type(poc_id) is not str:
            raise ExecutableOrchestrationInvalid("poc_id is invalid.")
        key_digest = _key_digest(idempotency_key)
        contract, confirmation, criterion = self._validated_inputs(poc_id)
        request_sha256 = _request_digest(poc_id, contract, confirmation, criterion)
        with self._lock:
            prior_id = self._idempotency.get(key_digest)
            if prior_id is not None:
                prior = self._records[prior_id]
                if prior.request_digest != request_sha256:
                    raise ExecutableOrchestrationConflict(
                        "Idempotency key conflicts with a different frozen input."
                    )
                return ExecutableStartResult(prior.attempt, True)

            current_id = self._current_by_poc.get(poc_id)
            if current_id is not None:
                current = self._records[current_id]
                current.attempt = current.attempt.model_copy(
                    update={"is_current": False, "status": EvidenceAttemptStatus.STALE}
                )
            attempt_id = "eatm_" + uuid4().hex
            evidence_ref = "evidence:" + uuid4().hex
            attempt = self._reserved_attempt(
                attempt_id=attempt_id,
                evidence_ref=evidence_ref,
                poc_id=poc_id,
                contract=contract,
                confirmation=confirmation,
                criterion=criterion,
            )
            self._records[attempt_id] = _Record(
                request_digest=request_sha256,
                attempt=attempt,
                contract=contract,
                confirmation=confirmation,
                criterion=criterion,
            )
            self._idempotency[key_digest] = attempt_id
            self._current_by_poc[poc_id] = attempt_id
            return ExecutableStartResult(attempt, False)

    def execute(self, attempt_id: object) -> ExecutableStartResult:
        if type(attempt_id) is not str:
            raise KeyError("Executable attempt was not found.")
        with self._lock:
            try:
                record = self._records[attempt_id]
            except KeyError:
                raise KeyError("Executable attempt was not found.") from None
            if record.attempt.status is not EvidenceAttemptStatus.RESERVED:
                return ExecutableStartResult(record.attempt, True)
            record.attempt = record.attempt.model_copy(
                update={"status": EvidenceAttemptStatus.RUNNING}
            )
        try:
            completed = self._execute(
                attempt_id=attempt_id,
                evidence_ref=record.attempt.evidence_ref,
                poc_id=record.attempt.poc_id,
                contract=record.contract,
                confirmation=record.confirmation,
                criterion=record.criterion,
            )
        except Exception:
            return ExecutableStartResult(self.fail(attempt_id), False)
        with self._lock:
            if record.attempt.status in {
                EvidenceAttemptStatus.CANCELLED,
                EvidenceAttemptStatus.FAILED_INTERNAL,
            }:
                return ExecutableStartResult(record.attempt, False)
            current = self._current_by_poc.get(record.attempt.poc_id) == attempt_id
            record.attempt = completed.model_copy(
                update={
                    "status": (
                        EvidenceAttemptStatus.COMPLETED
                        if current
                        else EvidenceAttemptStatus.STALE
                    ),
                    "is_current": current,
                }
            )
            return ExecutableStartResult(record.attempt, False)

    def cancel(self, attempt_id: object) -> ExecutableEvidenceAttempt:
        return self._terminalize(
            attempt_id,
            status=EvidenceAttemptStatus.CANCELLED,
            reason="Evidence execution was cancelled before an acceptance verdict.",
        )

    def fail(self, attempt_id: object) -> ExecutableEvidenceAttempt:
        return self._terminalize(
            attempt_id,
            status=EvidenceAttemptStatus.FAILED_INTERNAL,
            reason="Evidence execution failed internally before an acceptance verdict.",
        )

    def current(self, poc_id: object) -> ExecutableEvidenceAttempt | None:
        if type(poc_id) is not str:
            raise KeyError("Executable attempt was not found.")
        with self._lock:
            attempt_id = self._current_by_poc.get(poc_id)
            return None if attempt_id is None else self._records[attempt_id].attempt

    def history(self, poc_id: object) -> tuple[ExecutableEvidenceAttempt, ...]:
        if type(poc_id) is not str:
            raise KeyError("Executable attempt was not found.")
        with self._lock:
            return tuple(
                record.attempt
                for record in self._records.values()
                if record.attempt.poc_id == poc_id
            )

    def attempt(self, attempt_id: object) -> ExecutableEvidenceAttempt:
        if type(attempt_id) is not str:
            raise KeyError("Executable attempt was not found.")
        with self._lock:
            try:
                return self._records[attempt_id].attempt
            except KeyError:
                raise KeyError("Executable attempt was not found.") from None

    def _reserved_attempt(
        self,
        *,
        attempt_id: str,
        evidence_ref: str,
        poc_id: str,
        contract: POCContract,
        confirmation: ContractConfirmation,
        criterion: CapabilityCriterion,
    ) -> ExecutableEvidenceAttempt:
        binding = criterion.evidence_binding
        assert binding is not None
        policy = binding.policy
        assert isinstance(policy, ExactToolSelectionEvidencePolicy)
        return ExecutableEvidenceAttempt(
            attempt_id=attempt_id,
            poc_id=poc_id,
            contract_id=contract.id,
            contract_version=contract.version,
            contract_hash=contract.canonical_hash or contract_digest(contract),
            confirmation_id=confirmation.confirmation_id,
            confirmation_fingerprint=contract_confirmation_fingerprint(contract),
            plan_id=criterion.a4_plan_id,
            plan_version=criterion.a4_plan_version,
            plan_hash=criterion.a4_plan_sha256,
            criterion_id=criterion.id,
            policy_id=policy.policy_id,
            policy_sha256=binding.policy_sha256,
            adapter_id=policy.adapter,
            adapter_version=policy.adapter_version,
            execution_profile=EXECUTABLE_SYNTHETIC_PROFILE,
            workload_sha256=policy.workload_sha256,
            sample_count=0,
            success_count=0,
            status=EvidenceAttemptStatus.RESERVED,
            reason="Evidence attempt reserved before server-owned execution.",
            limitations=(
                "Server-owned synthetic demo profile "
                f"{EXECUTABLE_SYNTHETIC_PROFILE} is not real endpoint proof.",
            ),
            evidence_ref=evidence_ref,
            measured_at=self._clock(),
        )


    def _terminalize(
        self,
        attempt_id: object,
        *,
        status: EvidenceAttemptStatus,
        reason: str,
    ) -> ExecutableEvidenceAttempt:
        if type(attempt_id) is not str:
            raise KeyError("Executable attempt was not found.")
        with self._lock:
            try:
                record = self._records[attempt_id]
            except KeyError:
                raise KeyError("Executable attempt was not found.") from None
            if record.attempt.status not in {
                EvidenceAttemptStatus.RESERVED,
                EvidenceAttemptStatus.RUNNING,
            }:
                return record.attempt
            record.attempt = record.attempt.model_copy(
                update={"status": status, "verdict": None, "reason": reason}
            )
            return record.attempt

    def _validated_inputs(
        self, poc_id: str
    ) -> tuple[POCContract, ContractConfirmation, CapabilityCriterion]:
        try:
            contract = self._contract_lookup(poc_id)
            confirmation = self._confirmation_lookup(poc_id)
        except Exception as error:
            raise ExecutableOrchestrationConflict(
                "Frozen customer agreement is unavailable."
            ) from error
        if type(contract) is not POCContract or type(confirmation) is not ContractConfirmation:
            raise ExecutableOrchestrationConflict(
                "A typed frozen customer agreement is required."
            )
        if contract.status is not ContractStatus.FROZEN or not verify_contract_digest(contract):
            raise ExecutableOrchestrationConflict(
                "Evidence requires the exact digest-valid frozen contract."
            )
        try:
            require_affirmative_confirmation(contract, confirmation)
        except ValueError as error:
            raise ExecutableOrchestrationConflict(
                "Evidence requires matching affirmative customer confirmation."
            ) from error
        if contract.confirmation_id != confirmation.confirmation_id:
            raise ExecutableOrchestrationConflict(
                "Frozen contract and confirmation provenance disagree."
            )
        criteria = tuple(
            item for item in contract.criteria if type(item) is CapabilityCriterion
        )
        if len(criteria) != len(contract.criteria):
            raise ExecutableOrchestrationInvalid(
                "The executable A6 slice accepts only generic A5 criteria."
            )
        if any(item.poc_id != poc_id for item in criteria):
            raise ExecutableOrchestrationConflict(
                "Every generic criterion must belong to the requested POC."
            )
        executable = tuple(
            item for item in criteria if item.planning_disposition == "EXECUTABLE"
        )
        if len(executable) != 1:
            raise ExecutableOrchestrationInvalid(
                "The executable A6 slice requires exactly one executable criterion."
            )
        criterion = executable[0]
        binding = criterion.evidence_binding
        if binding is None or not isinstance(binding.policy, ExactToolSelectionEvidencePolicy):
            raise ExecutableOrchestrationConflict(
                "The executable criterion has no valid frozen evidence binding."
            )
        if binding.binding_type != "EXECUTABLE":
            raise ExecutableOrchestrationConflict(
                "The server-selected evidence method does not match the binding."
            )
        return contract, confirmation, criterion

    def _execute(
        self,
        *,
        attempt_id: str,
        evidence_ref: str,
        poc_id: str,
        contract: POCContract,
        confirmation: ContractConfirmation,
        criterion: CapabilityCriterion,
    ) -> ExecutableEvidenceAttempt:
        binding = criterion.evidence_binding
        assert binding is not None
        policy = binding.policy
        assert isinstance(policy, ExactToolSelectionEvidencePolicy)
        root = Path(__file__).resolve().parents[2]
        workload = root / policy.workload_path
        if policy.workload_path != EXECUTABLE_WORKLOAD_PATH:
            raise ExecutableOrchestrationConflict(
                "The executable workload is not the exact frozen support fixture."
            )
        if fixture_sha256(workload) != EXECUTABLE_WORKLOAD_SHA256:
            raise ExecutableOrchestrationConflict(
                "The executable workload hash does not match the frozen policy."
            )
        _, fixture = load_tool_selection_fixture(workload)
        execution = DeterministicToolSelectionAdapter().execute(fixture, "pass")
        legacy = Criterion(
            id=criterion.id,
            title=criterion.title,
            must_have=criterion.must_have,
            source=criterion.source,
            human_added=False,
            normalized_claim=criterion.normalized_claim,
            metric=Metric.EXACT_TOOL_SELECTION_RATE,
            unit=policy.unit,
            aggregation="rate",
            rule=ProportionRule(
                operator="gte",
                threshold=policy.threshold,
                minimum_samples=policy.minimum_samples,
                confidence_level=policy.confidence_level,
                confidence_method="wilson_two_sided_lower_bound",
            ),
            workload_slice=policy.workload_slice,
            adapter=policy.adapter,
            adapter_version=policy.adapter_version,
            owner=criterion.owner,
            evidence_policy=policy.policy_id,
            approved=True,
        )
        measurement = ProportionMeasurement(
            criterion_id=criterion.id,
            sample_count=execution.sample_count,
            success_count=execution.success_count,
            evidence_refs=[evidence_ref],
        )
        verdict = evaluate_proportion_criterion(legacy, measurement)
        return ExecutableEvidenceAttempt(
            attempt_id=attempt_id,
            poc_id=poc_id,
            contract_id=contract.id,
            contract_version=contract.version,
            contract_hash=contract.canonical_hash or contract_digest(contract),
            confirmation_id=confirmation.confirmation_id,
            confirmation_fingerprint=contract_confirmation_fingerprint(contract),
            plan_id=criterion.a4_plan_id,
            plan_version=criterion.a4_plan_version,
            plan_hash=criterion.a4_plan_sha256,
            criterion_id=criterion.id,
            policy_id=policy.policy_id,
            policy_sha256=binding.policy_sha256,
            adapter_id=policy.adapter,
            adapter_version=policy.adapter_version,
            execution_profile=EXECUTABLE_SYNTHETIC_PROFILE,
            workload_sha256=policy.workload_sha256,
            sample_count=execution.sample_count,
            success_count=execution.success_count,
            observed_rate=verdict.observed_rate,
            confidence_lower_bound=verdict.confidence_lower_bound,
            verdict=verdict.verdict,
            status=EvidenceAttemptStatus.COMPLETED,
            reason=verdict.reason,
            limitations=tuple(
                dict.fromkeys(
                    (
                        *verdict.limitations,
                        "Server-owned synthetic demo profile "
                        f"{EXECUTABLE_SYNTHETIC_PROFILE} uses the hidden pass scenario; "
                        "this is not real endpoint proof and does not authorize deployment "
                        "or other external action.",
                    )
                )
            ),
            evidence_ref=evidence_ref,
            measured_at=self._clock(),
        )


GENERIC_EVIDENCE_ORCHESTRATION_SCHEMA_VERSION = (
    "exitspec.generic-evidence-orchestration.v1"
)
_CATALOG_REF_RE = re.compile(
    r"^evref_[a-f0-9]{64}$"
)


class EvidenceAttemptSnapshot(FrozenExitSpecModel):
    """Immutable generic attempt projection shared by both supported methods."""

    schema_version: str = GENERIC_EVIDENCE_ORCHESTRATION_SCHEMA_VERSION
    attempt_id: str = Field(pattern=r"^eatm_[a-f0-9]{32}$")
    operation_id: str = Field(pattern=r"^prun_[a-f0-9]{32}$")
    run_id: str = Field(pattern=r"^run_[a-f0-9]{32}$")
    poc_id: str = Field(pattern=r"^poc_[a-z0-9][a-z0-9_-]{2,63}$")
    contract_id: str = Field(min_length=1, max_length=160)
    contract_version: str = Field(min_length=1, max_length=100)
    contract_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    confirmation_id: str = Field(pattern=r"^cnf_[a-f0-9]{64}$")
    confirmation_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    plan_ids: tuple[str, ...]
    plan_versions: tuple[int, ...]
    plan_hashes: tuple[str, ...]
    criterion_ids: tuple[str, ...]
    method_identities: tuple[EvidenceMethodIdentity, ...]
    selected_catalog_ref: str | None = None
    request_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    status: EvidenceAttemptStatus
    results: tuple[CriterionEvidenceResult, ...] = ()
    reduction: EvidenceReduction | None = None
    evidence_pack_url: str | None = None
    evidence_pack_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    reason: str = Field(min_length=1, max_length=4_000)
    next_action: str = Field(min_length=1, max_length=2_000)
    reserved_at: datetime
    terminal_at: datetime | None = None
    is_current: bool = True
    shipping_authorized: Literal[False] = False


@dataclass(frozen=True, slots=True)
class EvidenceOrchestrationStartResult:
    attempt: EvidenceAttemptSnapshot
    replayed: bool


@dataclass(slots=True)
class _GenericRecord:
    request_digest: str
    attempt: EvidenceAttemptSnapshot
    contract: POCContract
    confirmation: ContractConfirmation
    criteria: tuple[CapabilityCriterion, ...]


def _catalog_reference(value: object) -> str:
    if type(value) is not str or _CATALOG_REF_RE.fullmatch(value) is None:
        raise ExecutableOrchestrationInvalid("catalog_evidence_ref is invalid.")
    return value


def _validate_managed_contract_binding(
    contract: POCContract,
    criterion: CapabilityCriterion,
    policy: ManagedTTFTEvidencePolicy,
) -> None:
    """Reject a generic contract that cannot authorize this managed identity."""

    identity = getattr(policy, "identity", None)
    if identity is None:
        raise ValueError("Managed criterion policy identity is unavailable.")
    contract_target = contract.target_system
    contract_workload = contract.workload
    policy_identity_pairs = (
        (getattr(policy, "adapter", None), getattr(identity, "adapter_id", None)),
        (
            getattr(policy, "adapter_version", None),
            getattr(identity, "adapter_version", None),
        ),
        (getattr(policy, "profile_id", None), getattr(identity, "managed_profile_id", None)),
        (
            getattr(policy, "profile_digest", None),
            getattr(identity, "managed_profile_sha256", None),
        ),
        (getattr(policy, "workload_id", None), getattr(identity, "workload_id", None)),
        (
            getattr(policy, "workload_digest", None),
            getattr(identity, "workload_digest", None),
        ),
        (
            getattr(policy, "native_metric", None),
            getattr(identity, "produced_evidence_metric_definition_id", None),
        ),
        (
            getattr(policy, "native_metric", None),
            getattr(identity, "requested_criterion_metric_definition_id", None),
        ),
        (
            getattr(policy, "aggregation_policy", None),
            getattr(identity, "run_aggregation_policy", None),
        ),
        (getattr(policy, "reducer_id", None), getattr(identity, "reducer_id", None)),
        (
            getattr(policy, "measurement_population", None),
            getattr(identity, "latency_population", None),
        ),
        (
            getattr(policy, "configured_concurrency", None),
            getattr(getattr(identity, "traffic", None), "configured_concurrency", None),
        ),
        (
            getattr(policy, "warmup_requests", None),
            getattr(getattr(identity, "traffic", None), "warmup_requests", None),
        ),
        (
            getattr(policy, "attempts", None),
            getattr(getattr(identity, "traffic", None), "measured_requests", None),
        ),
        (
            getattr(policy, "minimum_successful_samples", None),
            getattr(identity, "max_measured_requests", None),
        ),
        (
            getattr(policy, "minimum_successful_samples", None),
            getattr(
                getattr(identity, "reliability_population", None),
                "exact_attempts",
                None,
            ),
        ),
        (getattr(policy, "sampling_seed", None), getattr(getattr(identity, "sampling", None), "seed", None)),
        (
            getattr(policy, "sampling_temperature", None),
            getattr(getattr(identity, "sampling", None), "temperature", None),
        ),
        (
            getattr(policy, "requested_output_tokens", None),
            getattr(getattr(identity, "sampling", None), "requested_output_tokens", None),
        ),
    )
    if (
        contract_target.provider != MANAGED_TARGET_PROVIDER
        or contract_target.endpoint_class != MANAGED_TARGET_ENDPOINT_CLASS
        or contract_target.model != getattr(identity, "target_model", None)
        or contract_workload.sha256
        != str(getattr(identity, "workload_digest", "")).removeprefix("sha256:")
        or any(left != right for left, right in policy_identity_pairs)
        or criterion.capability_key != getattr(policy, "capability_key", None)
        or criterion.rule != getattr(policy, "rule", None)
        or criterion.operator != getattr(policy, "operator", None)
        or criterion.threshold != getattr(policy, "threshold", None)
        or criterion.unit != getattr(policy, "unit", None)
        or criterion.measurement_population
        != getattr(policy, "measurement_population", None)
        or criterion.evidence_method != getattr(policy, "evidence_method", None)
        or criterion.adapter != getattr(policy, "adapter", None)
        or criterion.adapter_version != getattr(policy, "adapter_version", None)
        or criterion.evidence_profile != getattr(policy, "profile_id", None)
    ):
        raise ValueError(
            "Managed contract target, workload, or criterion policy is incompatible "
            "with the server-managed evidence identity."
        )


def _generic_request_digest(
    poc_id: str,
    contract: POCContract,
    confirmation: ContractConfirmation,
    criteria: Sequence[CapabilityCriterion],
    methods: Sequence[EvidenceMethodIdentity],
    catalog_ref: str | None,
) -> str:
    return hashlib.sha256(
        b"exitspec-generic-evidence-request-v1\x00"
        + canonical_json_bytes(
            {
                "poc_id": poc_id,
                "contract": contract.model_dump(mode="json"),
                "confirmation": confirmation.model_dump(mode="json"),
                "criteria": [item.model_dump(mode="json") for item in criteria],
                "methods": [item.model_dump(mode="json") for item in methods],
                "catalog_evidence_ref": catalog_ref,
            }
        )
    ).hexdigest()


class ProcessLocalEvidenceOrchestrationService:
    """Generic A6 coordinator over frozen capability bindings.

    The service reserves an immutable attempt and updates the current pointer
    before dispatching either server-owned deterministic execution or a safe
    catalog import.  It deliberately has no caller-selected method/profile
    argument.
    """

    def __init__(
        self,
        *,
        contract_lookup: Callable[[str], POCContract],
        confirmation_lookup: Callable[[str], ContractConfirmation],
        catalog: InferdromeBundleCatalog | None = None,
        clock: Callable[[], datetime] = _now,
        output_root: Path | None = None,
        closure: ProcessLocalPOCClosureService | None = None,
    ) -> None:
        if not callable(contract_lookup) or not callable(confirmation_lookup):
            raise TypeError("contract and confirmation lookups must be callable.")
        if catalog is not None and type(catalog) is not InferdromeBundleCatalog:
            raise TypeError("catalog is invalid.")
        if not callable(clock):
            raise TypeError("clock must be callable.")
        if output_root is not None and (
            not isinstance(output_root, Path) or not output_root.is_absolute()
        ):
            raise ValueError("output_root must be an absolute path.")
        if closure is not None and type(closure) is not ProcessLocalPOCClosureService:
            raise TypeError("closure is invalid.")
        self._contract_lookup = contract_lookup
        self._confirmation_lookup = confirmation_lookup
        self._catalog = catalog
        self._clock = clock
        self._output_root = output_root
        self._records: dict[str, _GenericRecord] = {}
        self._idempotency: dict[str, str] = {}
        self._current_by_poc: dict[str, str] = {}
        self._lock = RLock()
        self._closure = closure or ProcessLocalPOCClosureService(
            evidence_resolver=self._closure_binding_for_poc,
            clock=self._clock,
        )

    def start(
        self,
        poc_id: object,
        *,
        acknowledgement: object,
        idempotency_key: object,
        catalog_evidence_ref: object = None,
    ) -> EvidenceOrchestrationStartResult:
        reserved = self.reserve(
            poc_id,
            acknowledgement=acknowledgement,
            idempotency_key=idempotency_key,
            catalog_evidence_ref=catalog_evidence_ref,
        )
        if reserved.replayed:
            return reserved
        return self.execute(reserved.attempt.attempt_id)

    def reserve(
        self,
        poc_id: object,
        *,
        acknowledgement: object,
        idempotency_key: object,
        catalog_evidence_ref: object = None,
    ) -> EvidenceOrchestrationStartResult:
        if acknowledgement is not True:
            raise ExecutableOrchestrationInvalid(
                "Explicit evidence acknowledgement is required."
            )
        if type(poc_id) is not str:
            raise ExecutableOrchestrationInvalid("poc_id is invalid.")
        key_digest = _key_digest(idempotency_key)
        contract, confirmation, criteria, methods, catalog_ref = self._validated_inputs(
            poc_id,
            catalog_evidence_ref,
        )
        request_digest = _generic_request_digest(
            poc_id,
            contract,
            confirmation,
            criteria,
            methods,
            catalog_ref,
        )
        def reserve_open_mutation() -> EvidenceOrchestrationStartResult:
            with self._lock:
                prior_id = self._idempotency.get(key_digest)
                if prior_id is not None:
                    prior = self._records[prior_id]
                    if prior.request_digest != request_digest:
                        raise ExecutableOrchestrationConflict(
                            "Idempotency key conflicts with a different frozen input."
                        )
                    return EvidenceOrchestrationStartResult(prior.attempt, True)
                current_id = self._current_by_poc.get(poc_id)
                if current_id is not None:
                    current = self._records[current_id]
                    update = {"is_current": False}
                    if current.attempt.status in {
                        EvidenceAttemptStatus.RESERVED,
                        EvidenceAttemptStatus.RUNNING,
                    }:
                        update["status"] = EvidenceAttemptStatus.STALE
                    current.attempt = current.attempt.model_copy(update=update)
                now = self._clock()
                attempt_id = "eatm_" + uuid4().hex
                attempt = EvidenceAttemptSnapshot(
                    attempt_id=attempt_id,
                    operation_id="prun_" + uuid4().hex,
                    run_id="run_" + uuid4().hex,
                    poc_id=poc_id,
                    contract_id=contract.id,
                    contract_version=contract.version,
                    contract_hash=contract.canonical_hash or contract_digest(contract),
                    confirmation_id=confirmation.confirmation_id,
                    confirmation_fingerprint=contract_confirmation_fingerprint(contract),
                    plan_ids=tuple(item.a4_plan_id for item in criteria),
                    plan_versions=tuple(item.a4_plan_version for item in criteria),
                    plan_hashes=tuple(item.a4_plan_sha256 for item in criteria),
                    criterion_ids=tuple(item.id for item in criteria),
                    method_identities=methods,
                    selected_catalog_ref=catalog_ref,
                    request_digest=request_digest,
                    status=EvidenceAttemptStatus.RESERVED,
                    reason="Evidence attempt reserved before any execution or import.",
                    next_action="ExitSpec will execute or import using the frozen bindings.",
                    reserved_at=now,
                )
                self._records[attempt_id] = _GenericRecord(
                    request_digest=request_digest,
                    attempt=attempt,
                    contract=contract,
                    confirmation=confirmation,
                    criteria=criteria,
                )
                self._idempotency[key_digest] = attempt_id
                self._current_by_poc[poc_id] = attempt_id
                return EvidenceOrchestrationStartResult(attempt, False)

        with self._lock:
            prior_id = self._idempotency.get(key_digest)
            if prior_id is not None:
                prior = self._records[prior_id]
                if prior.request_digest != request_digest:
                    raise ExecutableOrchestrationConflict(
                        "Idempotency key conflicts with a different frozen input."
                    )
                return EvidenceOrchestrationStartResult(prior.attempt, True)
        try:
            return self._closure.run_if_open(poc_id, reserve_open_mutation)
        except POCClosureConflict as error:
            raise ExecutableOrchestrationConflict(
                "The POC lifecycle is closed; start a new POC for new evidence."
            ) from error

    def execute(self, attempt_id: object) -> EvidenceOrchestrationStartResult:
        if type(attempt_id) is not str:
            raise KeyError("Evidence attempt was not found.")
        with self._lock:
            record = self._records.get(attempt_id)
            if record is None:
                raise KeyError("Evidence attempt was not found.")
            if record.attempt.status is not EvidenceAttemptStatus.RESERVED:
                return EvidenceOrchestrationStartResult(record.attempt, True)
            record.attempt = record.attempt.model_copy(
                update={"status": EvidenceAttemptStatus.RUNNING}
            )
        try:
            results = tuple(self._evaluate_record(record))
            rejected = any(
                item.ingestion_status == "INGESTION_REJECTED" for item in results
            )
            reduction = None if rejected else reduce_criterion_results(
                record.criteria,
                results,
            )
            status = (
                EvidenceAttemptStatus.INGESTION_REJECTED
                if rejected
                else EvidenceAttemptStatus.COMPLETED
            )
            reason = (
                "At least one evidence input was rejected during ingestion."
                if status is EvidenceAttemptStatus.INGESTION_REJECTED
                else reduction.reason
            )
            next_action = (
                "Correct the rejected evidence and start a new attempt."
                if status is EvidenceAttemptStatus.INGESTION_REJECTED
                else _next_action(reduction.verdict)
            )
        except Exception:
            return EvidenceOrchestrationStartResult(self.fail(attempt_id), False)
        with self._lock:
            if record.attempt.status in {
                EvidenceAttemptStatus.CANCELLED,
                EvidenceAttemptStatus.FAILED_INTERNAL,
            }:
                return EvidenceOrchestrationStartResult(record.attempt, False)
            current = self._current_by_poc.get(record.attempt.poc_id) == attempt_id
            record.attempt = record.attempt.model_copy(
                update={
                    "status": status if current else EvidenceAttemptStatus.STALE,
                    "results": results,
                    "reduction": reduction,
                    "reason": reason,
                    "next_action": next_action,
                    "terminal_at": self._clock(),
                    "is_current": current,
                }
            )
            if (
                self._output_root is not None
                and status is EvidenceAttemptStatus.COMPLETED
                and reduction is not None
                and record.attempt.status is EvidenceAttemptStatus.COMPLETED
                and record.attempt.is_current
                and self._current_by_poc.get(record.attempt.poc_id) == attempt_id
            ):
                try:
                    publication = self._publish_evidence_pack_locked(record)
                except GenericEvidencePackError:
                    record.attempt = record.attempt.model_copy(
                        update={
                            "status": EvidenceAttemptStatus.FAILED_INTERNAL,
                            "reduction": None,
                            "evidence_pack_url": None,
                            "evidence_pack_sha256": None,
                            "reason": "Evidence Pack publication failed before handoff.",
                            "next_action": "Review the artifact error and start a new evidence attempt.",
                        }
                    )
                else:
                    record.attempt = record.attempt.model_copy(
                        update={
                            "evidence_pack_url": publication.evidence_pack_url,
                            "evidence_pack_sha256": publication.evidence_pack_sha256,
                        }
                    )
            return EvidenceOrchestrationStartResult(record.attempt, False)

    def cancel(self, attempt_id: object) -> EvidenceAttemptSnapshot:
        return self._terminalize(
            attempt_id,
            status=EvidenceAttemptStatus.CANCELLED,
            reason="Evidence attempt was cancelled before an acceptance verdict.",
            next_action="Start a new evidence attempt if the customer still requires proof.",
        )

    def fail(self, attempt_id: object) -> EvidenceAttemptSnapshot:
        return self._terminalize(
            attempt_id,
            status=EvidenceAttemptStatus.FAILED_INTERNAL,
            reason="Evidence attempt failed internally before an acceptance verdict.",
            next_action="Review the server error and start a new evidence attempt.",
        )

    def attempt(self, attempt_id: object) -> EvidenceAttemptSnapshot:
        if type(attempt_id) is not str:
            raise KeyError("Evidence attempt was not found.")
        with self._lock:
            record = self._records.get(attempt_id)
            if record is None:
                raise KeyError("Evidence attempt was not found.")
            return record.attempt

    def current(self, poc_id: object) -> EvidenceAttemptSnapshot | None:
        if type(poc_id) is not str:
            raise KeyError("Evidence attempt was not found.")
        with self._lock:
            attempt_id = self._current_by_poc.get(poc_id)
            return None if attempt_id is None else self._records[attempt_id].attempt

    def history(self, poc_id: object) -> tuple[EvidenceAttemptSnapshot, ...]:
        if type(poc_id) is not str:
            raise KeyError("Evidence attempt was not found.")
        with self._lock:
            return tuple(
                record.attempt
                for record in self._records.values()
                if record.attempt.poc_id == poc_id
            )

    def snapshot_payload(self, poc_id: object) -> dict[str, object]:
        """Return current and historical evidence without exposing authority inputs."""

        if type(poc_id) is not str:
            raise KeyError("Evidence attempt was not found.")
        with self._lock:
            history_payload = [
                record.attempt.model_dump(mode="json")
                for record in self._records.values()
                if record.attempt.poc_id == poc_id
            ]
            current_id = self._current_by_poc.get(poc_id)
            current = None if current_id is None else self._records[current_id].attempt
            current_payload = None if current is None else current.model_dump(mode="json")
        closure = self._closure.get(poc_id)
        return {
            "poc_id": poc_id,
            "current": current_payload,
            "history": history_payload,
            "closure": None if closure is None else closure.model_dump(mode="json"),
            "shipping_authorized": False,
            "authorization": (
                "Evidence is proof about the approved criterion only; no result "
                "authorizes deployment, spending, procurement, production traffic, "
                "shipping, or any other external action."
            ),
        }

    def publish_evidence_pack(self, attempt_id: object) -> EvidenceAttemptSnapshot:
        """Publish or replay the pack for one admitted terminal attempt."""

        if type(attempt_id) is not str:
            raise KeyError("Evidence attempt was not found.")
        with self._lock:
            record = self._records.get(attempt_id)
            if record is None:
                raise KeyError("Evidence attempt was not found.")
            if record.attempt.evidence_pack_url is not None:
                if self._output_root is None:
                    raise GenericEvidencePackError("Evidence Pack output is unavailable.")
                self._verify_record_pack_locked(record)
                return record.attempt
            if (
                record.attempt.status is not EvidenceAttemptStatus.COMPLETED
                or not record.attempt.is_current
                or self._current_by_poc.get(record.attempt.poc_id) != attempt_id
                or record.attempt.reduction is None
                or any(
                    item.ingestion_status == "INGESTION_REJECTED"
                    for item in record.attempt.results
                )
            ):
                raise GenericEvidencePackError(
                    "Only admitted terminal evidence can produce an Evidence Pack."
                )
            if self._output_root is None:
                raise GenericEvidencePackError("Evidence Pack output is unavailable.")
            publication = self._publish_evidence_pack_locked(record)
            record.attempt = record.attempt.model_copy(
                update={
                    "evidence_pack_url": publication.evidence_pack_url,
                    "evidence_pack_sha256": publication.evidence_pack_sha256,
                }
            )
            return record.attempt

    def verify_evidence_pack(self, attempt_id: object) -> str:
        return self.verify_evidence_pack_publication(attempt_id).evidence_pack_sha256

    def verify_evidence_pack_publication(
        self,
        attempt_id: object,
    ) -> GenericEvidencePackPublication:
        if type(attempt_id) is not str:
            raise KeyError("Evidence attempt was not found.")
        with self._lock:
            record = self._records.get(attempt_id)
            if record is None:
                raise KeyError("Evidence attempt was not found.")
            return self._verify_record_pack_locked(record)

    def evidence_pack_library_item(self, attempt_id: object):
        from .evidence_pack_library import EvidencePackHandoffState, EvidencePackLibraryItem

        if type(attempt_id) is not str:
            raise KeyError("Evidence attempt was not found.")
        with self._lock:
            record = self._records.get(attempt_id)
            if record is None:
                raise KeyError("Evidence attempt was not found.")
            attempt = record.attempt
            if (
                attempt.evidence_pack_url is None
                or attempt.evidence_pack_sha256 is None
                or attempt.reduction is None
            ):
                raise GenericEvidencePackError("Evidence Pack is unavailable.")
            poc_id = attempt.poc_id
            is_current = attempt.is_current
            run_id = attempt.run_id
            contract_id = attempt.contract_id
            contract_version = attempt.contract_version
            contract_hash = attempt.contract_hash
            verdict = attempt.reduction.verdict
            evidence_pack_url = attempt.evidence_pack_url
            evidence_pack_sha256 = attempt.evidence_pack_sha256
            updated_at = attempt.terminal_at or attempt.reserved_at
            display_name = record.contract.id
            customer_label = record.contract.customer
        closure = self._closure.get(poc_id)
        handoff_state = EvidencePackHandoffState.HISTORICAL
        if is_current:
            handoff_state = EvidencePackHandoffState.READY_FOR_HANDOFF
            if (
                closure is not None
                and closure.evidence_binding is not None
                and closure.evidence_binding.run_id == run_id
            ):
                handoff_state = (
                    EvidencePackHandoffState.HANDOFF_COMPLETED
                    if closure.decision is HumanClosureDecision.HANDOFF_COMPLETED
                    else EvidencePackHandoffState.POC_STOPPED
                )
        return EvidencePackLibraryItem(
            poc_id=poc_id,
            display_name=display_name,
            customer_label=customer_label,
            contract_id=contract_id,
            contract_version=contract_version,
            contract_hash=contract_hash,
            run_id=run_id,
            verdict=verdict,
            evidence_pack_url=evidence_pack_url,
            evidence_pack_sha256=evidence_pack_sha256,
            handoff_state=handoff_state,
            updated_at=updated_at,
        )

    def handoff(
        self,
        attempt_id: object,
        *,
        decided_by: str,
        rationale: str,
        idempotency_key: str,
    ):
        binding = self._evidence_binding_for_attempt(attempt_id)
        request = HumanPOCClosureRequest(
            decision=HumanClosureDecision.HANDOFF_COMPLETED,
            decided_by=decided_by,
            rationale=rationale,
            evidence_binding=binding,
        )
        return self._closure.record(
            binding.poc_id,
            request,
            idempotency_key=idempotency_key,
        )

    def stop(
        self,
        attempt_id: object,
        *,
        decided_by: str,
        rationale: str,
        idempotency_key: str,
    ):
        binding = self._closure_binding_for_attempt(attempt_id)
        request = HumanPOCClosureRequest(
            decision=HumanClosureDecision.POC_STOPPED,
            decided_by=decided_by,
            rationale=rationale,
            evidence_binding=binding
            if type(binding) is TerminalEvidenceBinding
            else None,
            terminal_run_binding=binding
            if type(binding) is TerminalRunReceiptBinding
            else None,
        )
        return self._closure.record(
            binding.poc_id,
            request,
            idempotency_key=idempotency_key,
        )

    def _publish_evidence_pack_locked(
        self,
        record: _GenericRecord,
    ) -> GenericEvidencePackPublication:
        if self._output_root is None or record.attempt.reduction is None:
            raise GenericEvidencePackError("Evidence Pack inputs are unavailable.")
        payload = _generic_pack_payload(record)
        return publish_generic_evidence_pack(
            self._output_root,
            record.attempt.attempt_id,
            payload,
        )

    def _verify_record_pack_locked(
        self,
        record: _GenericRecord,
    ) -> GenericEvidencePackPublication:
        if self._output_root is None or record.attempt.evidence_pack_url is None:
            raise GenericEvidencePackError("Evidence Pack is unavailable.")
        publication = verify_generic_evidence_pack(
            self._output_root,
            record.attempt.attempt_id,
        )
        if (
            record.attempt.evidence_pack_url != publication.evidence_pack_url
            or record.attempt.evidence_pack_sha256 != publication.evidence_pack_sha256
        ):
            raise GenericEvidencePackError("Evidence Pack identity does not match the attempt.")
        return publication

    def _evidence_binding_for_attempt(self, attempt_id: object) -> TerminalEvidenceBinding:
        if type(attempt_id) is not str:
            raise KeyError("Evidence attempt was not found.")
        with self._lock:
            record = self._records.get(attempt_id)
            if record is None:
                raise KeyError("Evidence attempt was not found.")
            if self._current_by_poc.get(record.attempt.poc_id) != attempt_id:
                raise GenericEvidencePackError("Only the exact current attempt can be closed.")
            binding = self._binding_for_record_locked(record)
            if type(binding) is not TerminalEvidenceBinding:
                raise GenericEvidencePackError(
                    "A current verified Evidence Pack is required."
                )
            self._verify_record_pack_locked(record)
            return binding

    def _closure_binding_for_attempt(
        self,
        attempt_id: object,
    ) -> TerminalEvidenceBinding | TerminalRunReceiptBinding:
        if type(attempt_id) is not str:
            raise KeyError("Evidence attempt was not found.")
        with self._lock:
            record = self._records.get(attempt_id)
            if record is None:
                raise KeyError("Evidence attempt was not found.")
            if self._current_by_poc.get(record.attempt.poc_id) != attempt_id:
                raise GenericEvidencePackError("Only the exact current attempt can be closed.")
            binding = self._binding_for_record_locked(record)
            if binding is None:
                raise GenericEvidencePackError(
                    "Only a terminal current attempt can be closed."
                )
            return binding

    def _closure_binding_for_poc(
        self,
        poc_id: str,
    ) -> TerminalEvidenceBinding | TerminalRunReceiptBinding | None:
        with self._lock:
            attempt_id = self._current_by_poc.get(poc_id)
            if attempt_id is None:
                return None
            record = self._records.get(attempt_id)
            return None if record is None else self._binding_for_record_locked(record)

    def _binding_for_record_locked(
        self,
        record: _GenericRecord,
    ) -> TerminalEvidenceBinding | TerminalRunReceiptBinding | None:
        attempt = record.attempt
        if (
            attempt.reduction is not None
            and attempt.evidence_pack_url is not None
            and attempt.evidence_pack_sha256 is not None
            and attempt.status is EvidenceAttemptStatus.COMPLETED
        ):
            return TerminalEvidenceBinding(
                poc_id=attempt.poc_id,
                contract_id=attempt.contract_id,
                contract_version=attempt.contract_version,
                contract_hash=attempt.contract_hash,
                run_id=attempt.run_id,
                verdict=attempt.reduction.verdict,
                evidence_pack_url=attempt.evidence_pack_url,
                evidence_pack_sha256=attempt.evidence_pack_sha256,
            )
        if attempt.status in {
            EvidenceAttemptStatus.CANCELLED,
            EvidenceAttemptStatus.FAILED_INTERNAL,
            EvidenceAttemptStatus.INGESTION_REJECTED,
        }:
            receipt_sha256 = hashlib.sha256(
                canonical_json_bytes(attempt.model_dump(mode="json"))
            ).hexdigest()
            return TerminalRunReceiptBinding(
                poc_id=attempt.poc_id,
                contract_id=attempt.contract_id,
                contract_version=attempt.contract_version,
                contract_hash=attempt.contract_hash,
                operation_id=attempt.operation_id,
                runner_run_id=attempt.run_id,
                runner_input_digest=attempt.request_digest,
                run_status="BLOCKED",
                reason_code=attempt.status.value,
                terminal_at=attempt.terminal_at or attempt.reserved_at,
                run_receipt_sha256=receipt_sha256,
            )
        return None

    def _validated_inputs(
        self,
        poc_id: str,
        catalog_evidence_ref: object,
    ) -> tuple[
        POCContract,
        ContractConfirmation,
        tuple[CapabilityCriterion, ...],
        tuple[EvidenceMethodIdentity, ...],
        str | None,
    ]:
        try:
            contract = self._contract_lookup(poc_id)
            confirmation = self._confirmation_lookup(poc_id)
        except Exception as error:
            raise ExecutableOrchestrationConflict(
                "Frozen customer agreement is unavailable."
            ) from error
        if type(contract) is not POCContract or type(confirmation) is not ContractConfirmation:
            raise ExecutableOrchestrationConflict(
                "A typed frozen customer agreement is required."
            )
        if (
            contract.status is not ContractStatus.FROZEN
            or not verify_contract_digest(contract)
            or contract.canonical_hash != contract_digest(contract)
        ):
            raise ExecutableOrchestrationConflict(
                "Evidence requires the exact digest-valid frozen contract."
            )
        try:
            require_affirmative_confirmation(contract, confirmation)
        except ValueError as error:
            raise ExecutableOrchestrationConflict(
                "Evidence requires matching affirmative customer confirmation."
            ) from error
        if (
            contract.confirmation_id != confirmation.confirmation_id
            or confirmation.contract_fingerprint
            != contract_confirmation_fingerprint(contract)
        ):
            raise ExecutableOrchestrationConflict(
                "Frozen contract and confirmation provenance disagree."
            )
        criteria = tuple(
            item for item in contract.criteria if type(item) is CapabilityCriterion
        )
        if len(criteria) != len(contract.criteria) or not criteria:
            raise ExecutableOrchestrationInvalid(
                "Generic A6 evidence requires at least one A5 capability criterion."
            )
        if any(item.poc_id != poc_id for item in criteria):
            raise ExecutableOrchestrationConflict(
                "Every generic criterion must belong to the requested POC."
            )
        methods: list[EvidenceMethodIdentity] = []
        import_required = False
        for criterion in criteria:
            if criterion.explicit_exclusion or criterion.planning_disposition in {
                "CLARIFICATION_REQUIRED",
                "UNSUPPORTED",
            }:
                continue
            binding = criterion.evidence_binding
            if binding is None or binding.binding_type != criterion.planning_disposition:
                raise ExecutableOrchestrationConflict(
                    "Every supported criterion requires its matching frozen binding."
                )
            try:
                expected_digest = capability_evidence_policy_digest(binding.policy)
            except (TypeError, ValueError) as error:
                raise ExecutableOrchestrationConflict(
                    "Evidence binding policy digest cannot be verified."
                ) from error
            if not hmac.compare_digest(binding.policy_sha256, expected_digest):
                raise ExecutableOrchestrationConflict(
                    "Evidence binding policy digest does not match its content."
                )
            if criterion.planning_disposition == "EXECUTABLE":
                if not isinstance(binding.policy, ExactToolSelectionEvidencePolicy):
                    raise ExecutableOrchestrationConflict(
                        "Executable binding policy is incompatible with its method."
                    )
                methods.append(
                    EvidenceMethodIdentity(
                        method=EvidenceMethod.EXECUTABLE,
                        policy_id=binding.policy.policy_id,
                        policy_sha256=binding.policy_sha256,
                        adapter_id=binding.policy.adapter,
                        adapter_version=binding.policy.adapter_version,
                        profile_id=EXECUTABLE_SYNTHETIC_PROFILE,
                    )
                )
            elif criterion.planning_disposition == "EVIDENCE_IMPORT":
                if not isinstance(binding.policy, ManagedTTFTEvidencePolicy):
                    raise ExecutableOrchestrationConflict(
                        "Import binding policy is incompatible with its method."
                    )
                import_required = True
                methods.append(
                    EvidenceMethodIdentity(
                        method=EvidenceMethod.EVIDENCE_IMPORT,
                        policy_id=binding.policy.policy_id,
                        policy_sha256=binding.policy_sha256,
                        adapter_id=binding.policy.adapter,
                        adapter_version=binding.policy.adapter_version,
                        profile_id=binding.policy.profile_id,
                    )
                )
        catalog_ref = None
        if import_required:
            catalog_ref = _catalog_reference(catalog_evidence_ref)
        elif catalog_evidence_ref is not None:
            raise ExecutableOrchestrationInvalid(
                "catalog_evidence_ref is only accepted for a frozen import binding."
            )
        return contract, confirmation, criteria, tuple(methods), catalog_ref

    def _evaluate_record(
        self,
        record: _GenericRecord,
    ) -> tuple[CriterionEvidenceResult, ...]:
        catalog_ref = record.attempt.selected_catalog_ref
        results: list[CriterionEvidenceResult] = []
        for criterion in record.criteria:
            scope = criterion.planning_scope
            disposition = criterion.planning_disposition
            if criterion.explicit_exclusion:
                results.append(
                    CriterionEvidenceResult(
                        criterion_id=criterion.id,
                        scope=scope,
                        planning_disposition=disposition,
                        explicit_exclusion=True,
                        ingestion_status="ADMITTED",
                        verdict=VerdictStatus.NOT_PROVEN,
                        reason="Criterion was explicitly excluded by the frozen plan.",
                        limitations=("Explicit exclusion remains visible and non-gating.",),
                    )
                )
                continue
            if disposition in {"CLARIFICATION_REQUIRED", "UNSUPPORTED"}:
                results.append(
                    CriterionEvidenceResult(
                        criterion_id=criterion.id,
                        scope=scope,
                        planning_disposition=disposition,
                        ingestion_status="ADMITTED",
                        verdict=VerdictStatus.NOT_PROVEN,
                        reason="The frozen plan does not grant evidence execution authority.",
                        limitations=(
                            "Unsupported or clarification-required claims remain visible and are not proven.",
                        ),
                    )
                )
                continue
            if disposition == "EXECUTABLE":
                results.append(
                    _evaluate_executable_criterion(
                        record.contract,
                        record.confirmation,
                        criterion,
                        clock=self._clock,
                    )
                )
                continue
            assert disposition == "EVIDENCE_IMPORT"
            assert catalog_ref is not None
            results.append(
                self._evaluate_import_criterion(
                    record.contract,
                    record.confirmation,
                    criterion,
                    catalog_ref,
                )
            )
        return tuple(results)

    def _evaluate_import_criterion(
        self,
        contract: POCContract,
        confirmation: ContractConfirmation,
        criterion: CapabilityCriterion,
        catalog_ref: str,
    ) -> CriterionEvidenceResult:
        if self._catalog is None:
            return _ingestion_rejection(
                criterion,
                "The server evidence catalog is unavailable.",
            )
        try:
            binding = criterion.evidence_binding
            if binding is None or not isinstance(
                binding.policy,
                ManagedTTFTEvidencePolicy,
            ):
                raise ValueError("Managed import binding is invalid.")
            _validate_managed_contract_binding(contract, criterion, binding.policy)
            resolved = self._catalog.resolve_reference(catalog_ref)
            bundle_digest = resolved.entry.bundle_digest
            verified = verify_inferdrome_bundle(
                resolved.path,
                expected_bundle_digest=bundle_digest,
                require_customer_eligible=True,
            )
            result = _import_managed_criterion(
                contract,
                confirmation,
                criterion,
                verified,
                bundle_digest,
                self._clock(),
            )
            return result
        except (
            InferdromeCatalogNotFound,
            InferdromeBundleRejected,
            InferdromeManagedImportRejected,
            OSError,
            TypeError,
            ValueError,
        ) as error:
            return _ingestion_rejection(criterion, _safe_rejection_reason(error))

    def _terminalize(
        self,
        attempt_id: object,
        *,
        status: EvidenceAttemptStatus,
        reason: str,
        next_action: str,
    ) -> EvidenceAttemptSnapshot:
        if type(attempt_id) is not str:
            raise KeyError("Evidence attempt was not found.")
        with self._lock:
            record = self._records.get(attempt_id)
            if record is None:
                raise KeyError("Evidence attempt was not found.")
            if record.attempt.status not in {
                EvidenceAttemptStatus.RESERVED,
                EvidenceAttemptStatus.RUNNING,
            }:
                return record.attempt
            record.attempt = record.attempt.model_copy(
                update={
                    "status": status,
                    "reduction": None,
                    "reason": reason,
                    "next_action": next_action,
                    "terminal_at": self._clock(),
                }
            )
            return record.attempt


def _public_confirmation_payload(
    confirmation: ContractConfirmation,
) -> dict[str, object]:
    """Project confirmation binding without the authoritative idempotency key."""

    return {
        "confirmation_id": confirmation.confirmation_id,
        "contract_id": confirmation.contract_id,
        "contract_version": confirmation.contract_version,
        "contract_fingerprint": confirmation.contract_fingerprint,
        "confirmer": confirmation.confirmer_identity,
        "decision": confirmation.decision.value,
        "agreement_acknowledged": confirmation.agreement_acknowledged,
        "confirmed_at": confirmation.decided_at.isoformat(),
        "rationale": confirmation.rationale,
    }


def _generic_pack_payload(record: _GenericRecord) -> dict[str, object]:
    """Project one immutable, contract-bound attempt into pack artifacts."""

    attempt = record.attempt
    result_by_id = {result.criterion_id: result for result in attempt.results}
    criterion_payloads: list[dict[str, object]] = []
    for criterion in record.criteria:
        binding = criterion.evidence_binding
        result = result_by_id.get(criterion.id)
        criterion_payloads.append(
            {
                "criterion": criterion.model_dump(mode="json"),
                "frozen_policy": (
                    None
                    if binding is None
                    else binding.policy.model_dump(mode="json")
                ),
                "result": None if result is None else result.model_dump(mode="json"),
            }
        )

    plans: dict[tuple[str, int, str], dict[str, object]] = {}
    for criterion in record.criteria:
        plans[(criterion.a4_plan_id, criterion.a4_plan_version, criterion.a4_plan_sha256)] = {
            "plan_id": criterion.a4_plan_id,
            "version": criterion.a4_plan_version,
            "sha256": criterion.a4_plan_sha256,
        }
    limitations = tuple(
        dict.fromkeys(
            (
                *record.contract.non_goals,
                *(limitation for result in attempt.results for limitation in result.limitations),
                *((attempt.reduction.limitations) if attempt.reduction is not None else ()),
            )
        )
    )
    exclusions = [
        criterion.id for criterion in record.criteria if criterion.explicit_exclusion
    ]
    advisories = [
        criterion.id
        for criterion in record.criteria
        if criterion.planning_scope == "ADVISORY"
    ]
    unsupported = [
        criterion.id
        for criterion in record.criteria
        if criterion.planning_disposition in {"UNSUPPORTED", "CLARIFICATION_REQUIRED"}
    ]
    result_facts = [
        {
            "criterion_id": result.criterion_id,
            "observed": {
                "sample_count": result.sample_count,
                "success_count": result.success_count,
                "ttft_p95_ns": result.observed_ttft_p95_ns,
                "latency_population": result.observed_latency_population,
                "applicability_codes": result.applicability_codes,
            },
            "independent_calculation": {
                "calculation_id": result.calculation_id,
                "calculation_version": result.calculation_version,
                "recalculation_sha256": result.recalculation_sha256,
            },
            "evidence": {
                "evidence_ref": result.evidence_ref,
                "evidence_sha256": result.evidence_sha256,
                "bundle_digest": result.bundle_digest,
                "receipt_id": result.receipt_id,
                "receipt_sha256": result.receipt_sha256,
            },
        }
        for result in attempt.results
    ]
    return {
        "schema_version": "exitspec.generic-evidence-pack-payload.v1",
        "poc_id": attempt.poc_id,
        "customer": record.contract.customer,
        "use_case": record.contract.use_case,
        "contract": record.contract.model_dump(mode="json"),
        "contract_identity": {
            "id": attempt.contract_id,
            "version": attempt.contract_version,
            "sha256": attempt.contract_hash,
        },
        "confirmation": _public_confirmation_payload(record.confirmation),
        "confirmation_identity": {
            "id": attempt.confirmation_id,
            "fingerprint": attempt.confirmation_fingerprint,
        },
        "a4_plans": tuple(plans.values()),
        "attempt": {
            "attempt_id": attempt.attempt_id,
            "run_id": attempt.run_id,
            "operation_id": attempt.operation_id,
            "request_digest": attempt.request_digest,
            "reserved_at": attempt.reserved_at.isoformat(),
            "terminal_at": (
                None if attempt.terminal_at is None else attempt.terminal_at.isoformat()
            ),
        },
        "status": attempt.status.value,
        "current_at_publication": attempt.is_current,
        "historical_at_publication": not attempt.is_current,
        "criterion_evidence": tuple(criterion_payloads),
        "result_facts": tuple(result_facts),
        "method_identities": tuple(
            method.model_dump(mode="json") for method in attempt.method_identities
        ),
        "identity_summary": {
            "adapters": tuple(
                dict.fromkeys(method.adapter_id for method in attempt.method_identities)
            ),
            "profiles": tuple(
                dict.fromkeys(
                    method.profile_id
                    for method in attempt.method_identities
                    if method.profile_id is not None
                )
            ),
            "workloads": tuple(
                {
                    key: getattr(binding.policy, key, None)
                    for key in (
                        "workload_id",
                        "workload_path",
                        "workload_sha256",
                        "workload_digest",
                    )
                    if getattr(binding.policy, key, None) is not None
                }
                for criterion in record.criteria
                if (binding := criterion.evidence_binding) is not None
                and any(
                    getattr(binding.policy, key, None) is not None
                    for key in (
                        "workload_id",
                        "workload_path",
                        "workload_sha256",
                        "workload_digest",
                    )
                )
            ),
            "criterion_populations": tuple(
                dict.fromkeys(
                    criterion.measurement_population
                    for criterion in record.criteria
                    if criterion.measurement_population is not None
                )
            ),
            "populations": tuple(
                dict.fromkeys(
                    result.observed_latency_population
                    for result in attempt.results
                    if result.observed_latency_population is not None
                )
            ),
            "reducers": tuple(
                dict.fromkeys(
                    binding.policy.reducer_id
                    for criterion in record.criteria
                    if (binding := criterion.evidence_binding) is not None
                    and hasattr(binding.policy, "reducer_id")
                )
            ),
            "calculations": tuple(
                dict.fromkeys(
                    result.calculation_id
                    for result in attempt.results
                    if result.calculation_id is not None
                )
            ),
        },
        "reduction": (
            None if attempt.reduction is None else attempt.reduction.model_dump(mode="json")
        ),
        "overall_verdict": (
            None if attempt.reduction is None else attempt.reduction.verdict.value
        ),
        "explicit_exclusions": tuple(exclusions),
        "advisory_criteria": tuple(advisories),
        "unsupported_or_clarification_criteria": tuple(unsupported),
        "limitations": limitations,
        "reason": attempt.reason,
        "next_action": attempt.next_action,
        "shipping_authorized": False,
        "non_authorization": (
            "PASS is evidence about the approved criterion only and does not authorize "
            "deployment, spending, procurement, production traffic, shipping, or any "
            "other external action."
        ),
    }


def _next_action(verdict: VerdictStatus) -> str:
    if verdict is VerdictStatus.PASS:
        return "Review the immutable Evidence Pack with the customer before closure."
    if verdict is VerdictStatus.FAIL:
        return "Review the failed criterion and revise the approved scope before rerunning."
    if verdict is VerdictStatus.BLOCKED:
        return "Resolve the blocking condition or stop the POC with a human decision."
    return "Review visible limitations and decide whether to revise scope or stop the POC."


def _ingestion_rejection(
    criterion: CapabilityCriterion,
    reason: str,
) -> CriterionEvidenceResult:
    return CriterionEvidenceResult(
        criterion_id=criterion.id,
        scope=criterion.planning_scope,
        planning_disposition=criterion.planning_disposition,
        explicit_exclusion=criterion.explicit_exclusion,
        ingestion_status="INGESTION_REJECTED",
        verdict=None,
        reason=reason,
        limitations=(
            "Rejected evidence is not an acceptance verdict and cannot produce an Evidence Pack.",
        ),
    )


def _safe_rejection_reason(error: Exception) -> str:
    from .inferdrome_bundle import InferdromeBundleRejected
    from .inferdrome_managed_import import InferdromeManagedImportRejected

    if isinstance(error, InferdromeBundleRejected):
        return f"Evidence ingestion rejected: {error.code.value}."
    if isinstance(error, InferdromeManagedImportRejected):
        return f"Evidence ingestion rejected: {error.code.value}."
    if isinstance(error, InferdromeCatalogNotFound):
        return "Evidence ingestion rejected: catalog evidence was not found."
    return "Evidence ingestion rejected during independent verification."


def _evaluate_executable_criterion(
    contract: POCContract,
    confirmation: ContractConfirmation,
    criterion: CapabilityCriterion,
    *,
    clock: Callable[[], datetime],
) -> CriterionEvidenceResult:
    binding = criterion.evidence_binding
    if binding is None or not isinstance(binding.policy, ExactToolSelectionEvidencePolicy):
        raise ExecutableOrchestrationConflict(
            "The executable criterion has no valid frozen evidence binding."
        )
    policy = binding.policy
    root = Path(__file__).resolve().parents[2]
    workload = root / policy.workload_path
    if policy.workload_path != EXECUTABLE_WORKLOAD_PATH:
        raise ExecutableOrchestrationConflict(
            "The executable workload is not the exact frozen support fixture."
        )
    if fixture_sha256(workload) != EXECUTABLE_WORKLOAD_SHA256:
        raise ExecutableOrchestrationConflict(
            "The executable workload hash does not match the frozen policy."
        )
    _, fixture = load_tool_selection_fixture(workload)
    execution = DeterministicToolSelectionAdapter().execute(fixture, "pass")
    evidence_sha256 = hashlib.sha256(
        b"exitspec-synthetic-execution-evidence-v1\x00"
        + canonical_json_bytes(
            {
                "contract_hash": contract.canonical_hash or contract_digest(contract),
                "confirmation_id": confirmation.confirmation_id,
                "confirmation_fingerprint": contract_confirmation_fingerprint(contract),
                "criterion_id": criterion.id,
                "policy": policy.model_dump(mode="json"),
                "execution": {
                    "sample_count": execution.sample_count,
                    "success_count": execution.success_count,
                    "records": [
                        item.model_dump(mode="json") for item in execution.records
                    ],
                },
            }
        )
    ).hexdigest()
    evidence_ref = "evidence:" + evidence_sha256
    legacy = Criterion(
        id=criterion.id,
        title=criterion.title,
        must_have=criterion.must_have,
        source=criterion.source,
        human_added=False,
        normalized_claim=criterion.normalized_claim,
        metric=Metric.EXACT_TOOL_SELECTION_RATE,
        unit=policy.unit,
        aggregation="rate",
        rule=ProportionRule(
            operator="gte",
            threshold=policy.threshold,
            minimum_samples=policy.minimum_samples,
            confidence_level=policy.confidence_level,
            confidence_method="wilson_two_sided_lower_bound",
        ),
        workload_slice=policy.workload_slice,
        adapter=policy.adapter,
        adapter_version=policy.adapter_version,
        owner=criterion.owner,
        evidence_policy=policy.policy_id,
        approved=True,
    )
    measurement = ProportionMeasurement(
        criterion_id=criterion.id,
        sample_count=execution.sample_count,
        success_count=execution.success_count,
        evidence_refs=[evidence_ref],
    )
    verdict = evaluate_proportion_criterion(legacy, measurement)
    limitations = tuple(
        dict.fromkeys(
            (
                "Server-owned synthetic demo profile "
                f"{EXECUTABLE_SYNTHETIC_PROFILE} uses the hidden pass scenario; "
                "this is not real endpoint proof and does not authorize deployment "
                "or other external action.",
            )
        )
    )
    return CriterionEvidenceResult(
        criterion_id=criterion.id,
        scope=criterion.planning_scope,
        planning_disposition=criterion.planning_disposition,
        explicit_exclusion=criterion.explicit_exclusion,
        ingestion_status="ADMITTED",
        verdict=verdict.verdict,
        reason=verdict.reason,
        limitations=limitations,
        sample_count=execution.sample_count,
        success_count=execution.success_count,
        evidence_ref=evidence_ref,
        calculation_id=policy.calculator_id,
        calculation_version=policy.calculator_version,
        evidence_sha256=evidence_sha256,
    )


def _legacy_managed_criterion(
    criterion: CapabilityCriterion,
    policy: ManagedTTFTEvidencePolicy,
) -> InferencePerformanceCriterionV3:
    identity = policy.identity
    threshold_ns_decimal = Decimal(str(policy.threshold)) * Decimal(1_000_000)
    if threshold_ns_decimal != threshold_ns_decimal.to_integral_value():
        raise ValueError("Managed TTFT threshold is not an exact nanosecond value.")
    legacy_identity = InferdromeEvidenceIdentityV1(
        schema_version="exitspec.inferdrome-evidence-identity.v1",
        evidence_schema_version=identity.evidence_schema_version,
        producer_name=identity.producer_name,
        producer_version=identity.producer_version,
        adapter_id=identity.adapter_id,
        adapter_version=identity.adapter_version,
        native_schema_fingerprint=identity.native_schema_fingerprint,
        managed_profile_id=identity.managed_profile_id,
        managed_profile_sha256=identity.managed_profile_sha256,
        local_gpu_proof_schema_id=identity.local_gpu_proof_schema_id,
        local_gpu_proof_schema_sha256=identity.local_gpu_proof_schema_sha256,
        request_plan_digest=MANAGED_DEMO_REQUEST_PLAN_DIGEST,
        workload_digest=identity.workload_digest,
        target_model=identity.target_model,
        target_model_revision=identity.target_model_revision,
        target_tokenizer_revision=identity.target_tokenizer_revision,
        target_endpoint=identity.target_endpoint,
        configured_max_concurrency=identity.traffic.configured_concurrency,
        exact_measured_attempts=identity.traffic.measured_requests,
        warmup_requests=identity.traffic.warmup_requests,
        binding_mode="EXTERNAL_RECEIPT_BINDING",
        chronology="RETROSPECTIVE",
        producer_contract_link="ABSENT",
    )
    return InferencePerformanceCriterionV3(
        criterion_type="inference_performance_v3",
        id=criterion.id,
        title=criterion.title,
        must_have=criterion.must_have,
        source=criterion.source,
        human_added=False,
        normalized_claim=criterion.normalized_claim,
        ttft_p95=ExternalTTFTP95RuleV1(
            metric="time_to_first_token",
            definition_id=policy.native_metric,
            aggregation="p95",
            unit="nanoseconds",
            operator="lt",
            threshold_ns=int(threshold_ns_decimal),
            reducer_id=policy.reducer_id,
            population=policy.measurement_population,
            minimum_successful_samples=policy.minimum_successful_samples,
            must_pass=True,
        ),
        error_rate=ExternalErrorRateRuleV1(
            metric="error_rate",
            aggregation="rate",
            operator="lt",
            threshold_basis_points=100,
            numerator="failed_or_anomalous_native_measured_requests",
            denominator="all_measured_requests",
            exact_attempts=policy.attempts,
            must_pass=True,
        ),
        evidence_identity=legacy_identity,
        concurrency_semantics="configured_maximum_concurrency_not_observed_overlap",
        owner=criterion.owner,
        evidence_policy=policy.policy_id,
        approved=True,
    )


def _import_managed_criterion(
    contract: POCContract,
    confirmation: ContractConfirmation,
    criterion: CapabilityCriterion,
    verified: object,
    bundle_digest: str,
    received_at: datetime,
) -> CriterionEvidenceResult:
    from .inferdrome_bundle import VerifiedInferdromeBundle

    binding = criterion.evidence_binding
    if binding is None or not isinstance(binding.policy, ManagedTTFTEvidencePolicy):
        raise ValueError("Managed import binding is invalid.")
    if type(verified) is not VerifiedInferdromeBundle:
        raise TypeError("Managed evidence was not independently verified.")
    policy = binding.policy
    _validate_managed_contract_binding(contract, criterion, policy)
    legacy = _legacy_managed_criterion(criterion, policy)
    _require_exact_retrospective_binding(verified, ValidatedManagedContractContext(
        contract=contract,
        confirmation=confirmation,
        criterion=legacy,
    ))
    applicability = _evaluate_applicability(verified, legacy)
    verdict = _evaluate_managed_verdict(
        verified.recalculated,
        legacy,
        applicability,
    )
    context = ValidatedManagedContractContext(
        contract=contract,
        confirmation=confirmation,
        criterion=legacy,
    )
    receipt = _build_receipt(
        verified,
        context,
        applicability,
        verdict,
        received_at,
    )
    limitations = tuple(
        dict.fromkeys(
            (
                "ExitSpec independently verified the managed bundle and recalculated native TTFT facts.",
                *(
                    f"Applicability limitation: {code.value}."
                    for code in applicability.issues
                ),
                "The managed evidence is retrospective and does not attest hardware or production authorization.",
            )
        )
    )
    return CriterionEvidenceResult(
        criterion_id=criterion.id,
        scope=criterion.planning_scope,
        planning_disposition=criterion.planning_disposition,
        explicit_exclusion=criterion.explicit_exclusion,
        ingestion_status="ADMITTED",
        verdict=verdict,
        reason=(
            "ExitSpec independently calculated the managed evidence verdict."
            if verdict is not VerdictStatus.PASS
            else "ExitSpec independently calculated a PASS for the frozen criterion."
        ),
        limitations=limitations,
        sample_count=verified.recalculated.attempted_count,
        success_count=verified.recalculated.successful_count,
        evidence_ref=f"catalog:{verified.descriptor['run_id']}",
        evidence_sha256=bundle_digest.removeprefix("sha256:"),
        calculation_id=policy.importer_calculation_id,
        calculation_version=receipt.calculation_version,
        applicability_codes=tuple(code.value for code in applicability.issues),
        observed_ttft_p95_ns=verified.recalculated.p95_ttft_ns,
        observed_latency_population=legacy.ttft_p95.population,
        recalculation_sha256=verified.recalculated.recalculation_sha256,
        receipt_id=receipt.receipt_id,
        receipt_sha256=managed_receipt_sha256(receipt).removeprefix("sha256:"),
        bundle_digest=bundle_digest.removeprefix("sha256:"),
    )

__all__ = [
    "EXECUTABLE_ORCHESTRATION_SCHEMA_VERSION",
    "EXECUTABLE_WORKLOAD_PATH",
    "EXECUTABLE_WORKLOAD_SHA256",
    "EXECUTABLE_SYNTHETIC_PROFILE",
    "GENERIC_EVIDENCE_ORCHESTRATION_SCHEMA_VERSION",
    "EvidenceAttemptStatus",
    "EvidenceMethod",
    "EvidenceMethodIdentity",
    "ExecutableEvidenceAttempt",
    "CriterionEvidenceResult",
    "EvidenceReduction",
    "ExecutableOrchestrationConflict",
    "ExecutableOrchestrationError",
    "ExecutableOrchestrationInvalid",
    "ExecutableStartResult",
    "ProcessLocalExecutableEvidenceService",
    "EvidenceAttemptSnapshot",
    "EvidenceOrchestrationStartResult",
    "ProcessLocalEvidenceOrchestrationService",
    "reduce_criterion_results",
]
