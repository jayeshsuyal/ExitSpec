"""Generic A5 agreement assembly and customer-confirmed lifecycle.

This module is the narrow bridge between A3/A4 and the existing contract
primitives.  It accepts only a current draft POC, the current retained A3
projection, and ``require_current`` from the A4 planner.  It never executes a
POC, imports evidence, creates an Evidence Pack, or issues a verdict.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import re
import secrets
from threading import Condition, RLock, get_ident
from typing import Any, Callable, Mapping, Sequence
import unicodedata

from .canonical import canonical_json_bytes
from .confirmations import (
    ConfirmationDecision,
    ContractConfirmation,
    canonical_confirmation_payload,
    confirmation_matches_contract,
    contract_confirmation_fingerprint,
    record_confirmation,
)
from .contracts import contract_digest, freeze_confirmed_contract, transition_contract
from .models import (
    CapabilityEvidenceBinding,
    CapabilityCriterion,
    ExactToolSelectionEvidencePolicy,
    ManagedTTFTEvidenceIdentityV1,
    ManagedTTFTEvidencePolicy,
    ContractStatus,
    POCContract,
    SourceReference,
    TargetSystem,
    WorkloadReference,
    capability_evidence_policy_digest,
)
from .assisted_authoring import RetainedProposalProjection
from .poc_capability_planner import (
    CapabilityPlan,
    PlanningDisposition,
    PlanningRecord,
    PlanningScope,
    CapabilityPlanningStaleProposal,
)
from .poc_creation import DraftPOCArchiveState, DraftPOCSnapshot, POC_ID_PATTERN
from .review_links import CustomerReviewInvitation, ReviewInvitationError, issue_customer_review_invitation


MAX_REVIEWER_LENGTH = 160
MAX_RATIONALE_LENGTH = 2_000
MAX_IDEMPOTENCY_KEY_LENGTH = 200
MAX_REVISIONS_PER_POC = 32
DEFAULT_MAX_AGREEMENTS = 1_024
A5_EXECUTION_POLICY_ID = "exitspec.a5.freeze-only.v1"
_A5_EXECUTION_POLICY = {
    "schema_version": "exitspec.a5.execution-policy.v1",
    "policy_id": A5_EXECUTION_POLICY_ID,
    "execution_available": False,
    "evidence_import_available": False,
    "evidence_pack_available": False,
    "verdict_available": False,
    "generated_fixture": None,
    "authority": "A6_REQUIRED",
}
A5_EXECUTION_POLICY_SHA256 = hashlib.sha256(
    b"exitspec-a5-execution-policy-v1\x00"
    + canonical_json_bytes(_A5_EXECUTION_POLICY)
).hexdigest()
A5_WORKLOAD_POLICY_URI = "policy://exitspec.a5.freeze-only.v1"


class AgreementError(RuntimeError):
    """Base for content-free A5 lifecycle failures."""


class AgreementInvalid(AgreementError):
    """The request or a trusted input violated the A5 boundary."""


class AgreementNotFound(AgreementError, KeyError):
    """No current agreement or valid review capability exists."""


class AgreementConflict(AgreementError):
    """The operation conflicts with immutable current state."""


class AgreementStale(AgreementConflict):
    """The A3/A4 inputs changed after an agreement was prepared."""


class AgreementCapacityExceeded(AgreementError):
    """The bounded process-local lifecycle reached capacity."""


@dataclass(frozen=True, slots=True)
class AgreementPreparation:
    """Complete immutable agreement input snapshot for one version."""

    poc: DraftPOCSnapshot
    contract: POCContract
    plan: CapabilityPlan
    retained_proposals: tuple[RetainedProposalProjection, ...] = field(repr=False)
    limitations: tuple[str, ...]
    prepared_at: datetime
    input_fingerprint: str = field(repr=False)
    draft_sha256: str = field(repr=False)
    assembly_reviewer: str
    assembly_rationale: str

    @property
    def poc_id(self) -> str:
        return self.poc.poc_id

    @property
    def approved_contract(self) -> POCContract:
        return self.contract

    @property
    def contract_fingerprint(self) -> str:
        return contract_confirmation_fingerprint(self.contract)


@dataclass(frozen=True, slots=True)
class AgreementRevision:
    """Immutable parent/successor relation created after REQUEST_CHANGES."""

    revision_id: str
    revision_number: int
    parent_contract_id: str
    parent_contract_version: str
    parent_draft_sha256: str
    requested_at: datetime
    request_rationale: str
    assembly_reviewer: str
    assembly_rationale: str
    successor_input_fingerprint: str
    successor_draft_sha256: str
    successor_contract_fingerprint: str

    @property
    def contract_version(self) -> str:
        return str(self.revision_number + 1)

    @property
    def parent_version(self) -> str:
        return f"{self.parent_contract_id}@{self.parent_contract_version}"


@dataclass(frozen=True, slots=True)
class AgreementVersionRecord:
    """Historical version record; its nested graph is never mutated."""

    preparation: AgreementPreparation
    review_invitation: CustomerReviewInvitation
    confirmation: ContractConfirmation
    superseded_at: datetime


@dataclass(frozen=True, slots=True)
class AgreementSnapshot:
    preparation: AgreementPreparation | None
    review_invitation: CustomerReviewInvitation | None
    review_expired: bool
    confirmation: ContractConfirmation | None
    frozen_contract: POCContract | None
    revision: AgreementRevision | None
    superseded_version_count: int
    current_inputs_stale: bool = False

    @property
    def poc_id(self) -> str | None:
        return None if self.preparation is None else self.preparation.poc_id


@dataclass(frozen=True, slots=True)
class AgreementWriteResult:
    value: AgreementPreparation | AgreementRevision | CustomerReviewInvitation | ContractConfirmation | POCContract
    replayed: bool


@dataclass(frozen=True, slots=True)
class _IdempotencyRecord:
    request_sha256: str
    poc_id: str


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _safe_text(value: object, field_name: str, maximum: int, *, single_line: bool = False) -> str:
    if type(value) is not str:
        raise AgreementInvalid(f"{field_name} is invalid.")
    normalized = unicodedata.normalize("NFC", value).strip()
    if (
        not normalized
        or len(normalized) > maximum
        or (single_line and ("\n" in normalized or "\r" in normalized))
        or any(ord(char) < 0x20 and char not in {"\n", "\r", "\t"} for char in normalized)
        or any(ord(char) == 0x7F for char in normalized)
    ):
        raise AgreementInvalid(f"{field_name} is invalid.")
    return normalized


def _idempotency_digest(value: object) -> str:
    normalized = _safe_text(value, "idempotency_key", MAX_IDEMPOTENCY_KEY_LENGTH, single_line=True)
    return hashlib.sha256(
        b"exitspec-generic-agreement-idempotency-v1\x00" + normalized.encode("utf-8")
    ).hexdigest()


def _request_digest(payload: Mapping[str, object]) -> str:
    return hashlib.sha256(
        b"exitspec-generic-agreement-request-v1\x00" + canonical_json_bytes(dict(payload))
    ).hexdigest()


def _validate_poc_id(value: object) -> str:
    if type(value) is not str or re.fullmatch(POC_ID_PATTERN, value) is None:
        raise AgreementInvalid("poc_id is invalid.")
    return value


def _snapshot_fingerprint(
    poc: DraftPOCSnapshot,
    retained: Sequence[RetainedProposalProjection],
    plan: CapabilityPlan,
) -> str:
    return hashlib.sha256(
        b"exitspec-generic-agreement-inputs-v1\x00"
        + canonical_json_bytes(
            {
                "poc": poc.model_dump(mode="json"),
                "retained_proposals": [
                    item.model_dump(mode="json")
                    for item in sorted(retained, key=lambda value: value.proposal_id)
                ],
                "capability_plan": plan.model_dump(mode="json"),
            }
        )
    ).hexdigest()


def _plan_digest(plan: CapabilityPlan) -> str:
    return hashlib.sha256(
        b"exitspec-a4-capability-plan-agreement-binding-v1\x00"
        + canonical_json_bytes(plan.model_dump(mode="json"))
    ).hexdigest()


def _criterion_id(proposal_id: str) -> str:
    digest = hashlib.sha256(
        b"exitspec-generic-agreement-criterion-v1\x00" + proposal_id.encode("utf-8")
    ).hexdigest()
    return f"CAP-{digest[:24].upper()}"


def _source(record: PlanningRecord) -> SourceReference:
    return SourceReference(
        speaker="customer_source",
        quote=record.source_quote,
        location=f"{record.source_id}:{record.source_receipt_id}/{record.proposal_id}",
    )


def _safe_contract_text(value: str) -> str:
    """Keep generated contract text bounded and stable."""

    return value[:2_000]


def _evidence_binding(
    record: PlanningRecord,
    criterion: Any,
) -> CapabilityEvidenceBinding | None:
    """Build the linear, server-owned handoff for one supported A4 record."""

    if record.disposition is PlanningDisposition.EXECUTABLE:
        policy = ExactToolSelectionEvidencePolicy(
            policy_id="support-tool-selection-v1",
            capability_key="exact_tool_selection",
            rule=criterion.rule,
            operator=criterion.operator,
            threshold=criterion.threshold,
            unit=criterion.unit,
            measurement_population=criterion.measurement_population,
            evidence_method=criterion.evidence_method,
            workload_path="examples/support-agent/fixtures/tool-selection-200.json",
            workload_sha256=(
                "75ef6f83450de100a920e9489a0b5966464f1dba2e3d339c4b57e64fb95d8271"
            ),
            workload_slice="support-tool-selection-v1",
            minimum_samples=200,
            confidence_level=0.95,
            confidence_method="wilson_two_sided_lower_bound",
            calculator_id="exitspec.statistics.wilson_lower_bound",
            calculator_version="wilson-two-sided-v1",
            adapter=criterion.adapter,
            adapter_version=criterion.adapter_version,
            verifier_id="exitspec.verdicts.evaluate_proportion_criterion",
            reducer_id="exitspec.verdicts.aggregate_overall_verdict",
        )
    elif record.disposition is PlanningDisposition.EVIDENCE_IMPORT:
        policy = ManagedTTFTEvidencePolicy(
            policy_id="inferdrome.managed-vllm-0.26-evidence-profile.v1",
            capability_key="inference_performance_external",
            rule=criterion.rule,
            operator=criterion.operator,
            threshold=criterion.threshold,
            unit=criterion.unit,
            measurement_population=criterion.measurement_population,
            evidence_method=criterion.evidence_method,
            workload_id="inferdrome.qwen2.5-real-gpu-workload.v1",
            workload_digest=(
                "sha256:22bf3389cc29ee946ae567870d7f8d7b458594224542a796e8990c15b1cfcd63"
            ),
            profile_id="inferdrome.managed-vllm-0.26-evidence-profile.v1",
            profile_digest=(
                "sha256:9d03b5d0822ed829ddbfa4c87c75530885b9ad51ee2c0cb7c5e31a075996fe34"
            ),
            native_metric="vllm_first_choices_event_v0_26",
            configured_concurrency=4,
            warmup_requests=10,
            attempts=100,
            minimum_successful_samples=100,
            sampling_seed=42,
            sampling_temperature=0,
            requested_output_tokens=32,
            reducer_id="nearest_rank_v1",
            reducer_version="1.0.0",
            aggregation_policy="independent_single_run_no_pooling",
            adapter=criterion.adapter,
            adapter_version=criterion.adapter_version,
            bundle_verifier_id="exitspec.inferdrome_bundle.verify_inferdrome_bundle",
            bundle_verifier_version="1.0.0",
            invocation_profile_validator_id=(
                "exitspec.inferdrome_managed_profile.validate_managed_invocation_profile"
            ),
            local_gpu_proof_validator_id=(
                "exitspec.inferdrome_managed_profile.validate_managed_local_gpu_proof"
            ),
            recalculation_id="exitspec.inferdrome-recalculation.v1",
            importer_calculation_id="exitspec.inferdrome-managed-importer.v1",
            identity=ManagedTTFTEvidenceIdentityV1(
                evidence_schema_version="inferdrome.evidence.v1",
                sequence_requirement="OPERATOR_MUST_FREEZE_BEFORE_MEASUREMENT",
                chronology_assurance="UNAVAILABLE",
                producer_name="vllm",
                producer_version="0.26.0",
                adapter_id=criterion.adapter,
                adapter_version=criterion.adapter_version,
                native_schema_fingerprint=(
                    "sha256:3a4fdee6fe9b45ce5b42c41fd3bfc6614245a36ecfe6f94de92b59717a136abb"
                ),
                managed_profile_id="inferdrome.managed-vllm-0.26-evidence-profile.v1",
                managed_profile_sha256=(
                    "sha256:9d03b5d0822ed829ddbfa4c87c75530885b9ad51ee2c0cb7c5e31a075996fe34"
                ),
                local_gpu_proof_schema_id="urn:inferdrome:local-gpu-proof:v1",
                local_gpu_proof_schema_sha256=(
                    "sha256:cf83bbdea2bba4c30b8f0e2c5f34f34a4077501207881fdbdab021571d665547"
                ),
                target_engine="vllm",
                target_engine_version="0.26.0",
                target_api="openai_chat_completions",
                target_model="Qwen/Qwen2.5-0.5B-Instruct",
                target_model_revision=(
                    "7ae557604adf67be50417f59c2c2f167def9a775"
                ),
                target_tokenizer_revision=(
                    "7ae557604adf67be50417f59c2c2f167def9a775"
                ),
                target_endpoint="http://127.0.0.1:18080/",
                workload_id="inferdrome.qwen2.5-real-gpu-workload.v1",
                workload_digest=(
                    "sha256:22bf3389cc29ee946ae567870d7f8d7b458594224542a796e8990c15b1cfcd63"
                ),
                source_schema_version="inferdrome.source-experiment.v1",
                traffic={
                    "schema_version": "exitspec.inferdrome-traffic.v1",
                    "policy_id": "inferdrome.concurrent.vllm.v1",
                    "kind": "concurrent",
                    "configured_concurrency": 4,
                    "warmup_requests": 10,
                    "measured_requests": 100,
                },
                sampling={
                    "schema_version": "exitspec.inferdrome-sampling.v1",
                    "policy_id": "inferdrome.qwen2.5-deterministic.v1",
                    "prompt_content_policy": "include",
                    "requested_output_tokens": 32,
                    "temperature": 0,
                    "seed": 42,
                },
                execution_mode="attached_endpoint",
                max_runtime_seconds=900,
                max_measured_requests=100,
                measurement_streaming=True,
                produced_evidence_metric_definition_id="vllm_first_choices_event_v0_26",
                choices_span_definition_id="last_choices_event_span_v1",
                metric_definitions_version="1.0.0",
                reducer_version="1.0.0",
                native_output_sensitivity="RESPONSE_CONTENT",
                canonical_response_content="omit",
                include_request_plan=True,
                expected_execution_fingerprint=(
                    "sha256:76d984ea57a0e7cb00520255a6e362f22885d713a875195a7397771937060edd"
                ),
                requested_criterion_metric_definition_id="vllm_first_choices_event_v0_26",
                run_aggregation_policy="independent_single_run_no_pooling",
                reducer_id="nearest_rank_v1",
                latency_population=(
                    "successful_measured_requests_with_observed_ttft"
                ),
                reliability_population={
                    "schema_version": "exitspec.inferdrome-reliability-population.v1",
                    "population_id": "exitspec.inferdrome-reliability.v1",
                    "operator": "lt",
                    "threshold_basis_points": 100,
                    "numerator": "failed_or_anomalous_native_measured_requests",
                    "denominator": "all_measured_requests",
                    "exact_attempts": 100,
                },
                claims_assurance="INTERNAL_CONSISTENCY_ONLY",
                canonicalization={
                    "canonicalization_scheme_id": "rfc8785_jcs_v1",
                    "canonical_bytes_encoding": "utf-8_rfc8785_jcs",
                    "hash_algorithm_id": "sha256_v1",
                    "hash_encoding_id": "lowercase_hex_without_prefix",
                    "link_derivation_policy_id": (
                        "exitspec.producer_link.sha256_canonical_hash.v1"
                    ),
                    "link_derivation_input": "bare_canonical_hash",
                    "link_derivation_operation": "prefix_sha256_no_second_hash",
                },
            ),
        )
    else:
        return None
    return CapabilityEvidenceBinding(
        binding_type=record.disposition.value,
        policy=policy,
        policy_sha256=capability_evidence_policy_digest(policy),
    )


def _build_criterion(
    record: PlanningRecord,
    poc: DraftPOCSnapshot,
    *,
    proposal: RetainedProposalProjection,
    plan: CapabilityPlan,
    plan_sha256: str,
    assembly_reviewer: str,
    assembly_rationale: str,
):
    criterion = record.criterion
    supported = record.disposition in {
        PlanningDisposition.EXECUTABLE,
        PlanningDisposition.EVIDENCE_IMPORT,
    }
    if supported and criterion is None:
        raise AgreementInvalid("A supported capability record is missing its criterion.")
    common = {
        "schema_version": "exitspec.capability-criterion.v1",
        "id": _criterion_id(record.proposal_id),
        "title": _safe_contract_text(record.normalized_claim),
        "must_have": record.scope is PlanningScope.MUST_HAVE,
        "source": _source(record),
        "human_added": False,
        "normalized_claim": record.normalized_claim,
        "poc_id": poc.poc_id,
        "capability_key": record.capability_key,
        "planning_scope": record.scope.value,
        "planning_disposition": record.disposition.value,
        "provenance": None if criterion is None else criterion.provenance.value,
        "planning_item_id": record.planning_item_id,
        "proposal_id": record.proposal_id,
        "proposal_key": proposal.proposal_key,
        "source_receipt_id": record.source_receipt_id,
        "source_id": record.source_id,
        "source_kind": record.source_kind.value,
        "source_content_sha256": record.source_content_sha256,
        "source_revision": record.source_revision,
        "source_adapter_name": proposal.source_adapter_name,
        "source_adapter_version": proposal.source_adapter_version,
        "redaction_policy_version": proposal.redaction_policy_version,
        "authoring_receipt_id": record.authoring_receipt_id,
        "authoring_result_id": record.authoring_result_id,
        "a4_plan_id": plan.plan_id,
        "a4_plan_version": plan.plan_version,
        "a4_plan_sha256": plan_sha256,
        "planner_reviewer": record.reviewer,
        "planner_rationale": record.rationale,
        "planning_reason": record.reason,
        "planning_next_action": record.next_action,
        "explicit_exclusion": record.explicit_exclusion,
        "assembly_reviewer": assembly_reviewer,
        "assembly_rationale": assembly_rationale,
        "rule": None if criterion is None else criterion.rule,
        "operator": None if criterion is None else criterion.operator,
        "threshold": None if criterion is None else criterion.threshold,
        "unit": None if criterion is None else criterion.unit,
        "measurement_population": None if criterion is None else criterion.measurement_population,
        "evidence_method": None if criterion is None else criterion.evidence_method,
        "adapter": None if criterion is None else criterion.adapter,
        "adapter_version": None if criterion is None else criterion.adapter_version,
        "evidence_profile": None if criterion is None else criterion.evidence_profile,
        "evidence_binding": _evidence_binding(record, criterion) if supported else None,
        "execution_available": False,
        "owner": poc.owner,
        "evidence_policy": (None if criterion is None else
            "A5 freezes the customer agreement only; a later approved A6 "
            "measurement/import boundary must supply evidence."
        ),
        "approved": True,
    }
    return CapabilityCriterion(**common)


def _contract_id(poc_id: str, plan: CapabilityPlan) -> str:
    digest = hashlib.sha256(
        b"exitspec-generic-agreement-contract-id-v1\x00"
        + canonical_json_bytes({"poc_id": poc_id, "plan_id": plan.plan_id})
    ).hexdigest()
    return f"agreement-{digest[:24]}"


def _limitation_text(record: PlanningRecord) -> str:
    status = record.disposition.value
    if record.explicit_exclusion:
        return (
            f"Excluded claim {record.proposal_id} remains visible and non-executable: "
            f"{record.reason}"
        )
    return (
        f"Nonblocking {record.scope.value.lower()} limitation for {record.proposal_id} "
        f"({status}): {record.reason}"
    )


def _assemble_contract(
    *,
    poc: DraftPOCSnapshot,
    retained: tuple[RetainedProposalProjection, ...],
    plan: CapabilityPlan,
    version: str,
    contract_id: str,
    parent_version: str | None,
    created_at: datetime,
    assembly_reviewer: str,
    assembly_rationale: str,
) -> tuple[POCContract, tuple[str, ...]]:
    if poc.archive_state is not DraftPOCArchiveState.ACTIVE:
        raise AgreementInvalid("The POC is not active.")
    if type(created_at) is not datetime or created_at.tzinfo is None or created_at.utcoffset() is None:
        raise AgreementInvalid("The agreement clock is unavailable.")
    criteria = []
    plan_fingerprint = _plan_digest(plan)
    proposal_by_id = {proposal.proposal_id: proposal for proposal in retained}
    limitations = [
        f"Bound A4 capability plan {plan.plan_id}, version {plan.plan_version}, fingerprint sha256:{_plan_digest(plan)}.",
        f"A5 execution policy {A5_EXECUTION_POLICY_ID}, fingerprint sha256:{A5_EXECUTION_POLICY_SHA256}; no generated fixture or executable workload is created by A5.",
        "A5 stops after an immutable customer-confirmed freeze; it does not execute or import evidence, issue an Evidence Pack, or issue a verdict.",
    ]
    for record in plan.records:
        if record.scope is PlanningScope.MUST_HAVE and (
            record.disposition in {PlanningDisposition.CLARIFICATION_REQUIRED, PlanningDisposition.UNSUPPORTED}
            and not record.explicit_exclusion
        ):
            raise AgreementInvalid("An unresolved or unsupported must-have cannot enter an agreement.")
        proposal = proposal_by_id.get(record.proposal_id)
        if proposal is None:
            raise AgreementInvalid("An A4 record is missing its retained proposal.")
        criteria.append(
            _build_criterion(
                record,
                poc,
                proposal=proposal,
                plan=plan,
                plan_sha256=plan_fingerprint,
                assembly_reviewer=assembly_reviewer,
                assembly_rationale=assembly_rationale,
            )
        )
        if record.disposition not in {PlanningDisposition.EXECUTABLE, PlanningDisposition.EVIDENCE_IMPORT}:
            limitations.append(_limitation_text(record))

    contract = POCContract(
        id=contract_id,
        version=version,
        status=ContractStatus.DRAFT,
        created_at=created_at,
        customer=poc.customer_label,
        use_case=poc.use_case,
        target_system=TargetSystem(
            provider="ExitSpec",
            endpoint_class="process-local-capability-agreement",
            model="customer-confirmed-A4-plan",
        ),
        workload=WorkloadReference(
            fixture_path=A5_WORKLOAD_POLICY_URI,
            sha256=A5_EXECUTION_POLICY_SHA256,
        ),
        criteria=tuple(criteria),
        owners=(poc.owner,),
        non_goals=tuple(limitations),
        evidence_retention_policy=(
            "No evidence is created by A5. A later A6 boundary must be explicitly approved."
        ),
        parent_version=parent_version,
    )
    in_review = transition_contract(contract, ContractStatus.IN_REVIEW, at=created_at)
    approved_payload = in_review.model_dump(mode="python")
    approved_payload["criteria"] = tuple(
        criterion.model_copy(update={"approved": True})
        for criterion in in_review.criteria
    )
    approved = transition_contract(
        POCContract.model_validate(approved_payload),
        ContractStatus.APPROVED,
        at=created_at,
    )
    return approved, tuple(limitations)


class ProcessLocalAgreementLifecycleService:
    """Bounded A5 lifecycle over current A3 retention and A4 planning."""

    def __init__(
        self,
        *,
        poc_lookup: Callable[[str], DraftPOCSnapshot],
        retained_lookup: Callable[[str], Sequence[RetainedProposalProjection]],
        planner: Any,
        clock: Callable[[], datetime] = _utc_now,
        max_agreements: int = DEFAULT_MAX_AGREEMENTS,
    ) -> None:
        if not callable(poc_lookup) or not callable(retained_lookup) or not callable(clock):
            raise TypeError("A5 lifecycle dependencies must be callable.")
        if not callable(getattr(planner, "require_current", None)):
            raise TypeError("planner must provide require_current.")
        if type(max_agreements) is not int or not 1 <= max_agreements <= 10_000:
            raise ValueError("max_agreements is outside supported bounds.")
        self._poc_lookup = poc_lookup
        self._retained_lookup = retained_lookup
        self._planner = planner
        self._clock = clock
        self._max_agreements = max_agreements
        self._preparations: dict[str, AgreementPreparation] = {}
        self._invitations: dict[str, CustomerReviewInvitation] = {}
        self._confirmations: dict[str, ContractConfirmation] = {}
        self._frozen: dict[str, POCContract] = {}
        self._history: dict[str, tuple[AgreementVersionRecord, ...]] = {}
        self._revisions: dict[str, AgreementRevision] = {}
        self._prepare_idempotency: dict[tuple[str, str, str], _IdempotencyRecord] = {}
        self._decision_idempotency: dict[tuple[str, str, str], _IdempotencyRecord] = {}
        self._freeze_idempotency: dict[tuple[str, str, str], _IdempotencyRecord] = {}
        self._revision_idempotency: dict[tuple[str, str, str], _IdempotencyRecord] = {}
        self._review_idempotency: dict[tuple[str, str, str], _IdempotencyRecord] = {}
        self._revision_results: dict[tuple[str, str, str], AgreementRevision] = {}
        self._token_secret = secrets.token_bytes(32)
        self._lock = RLock()
        self._condition = Condition(self._lock)
        self._inflight_pocs: dict[str, tuple[int, str]] = {}

    @staticmethod
    def _operation_key(poc_id: str, operation: str, key_digest: str) -> tuple[str, str, str]:
        return poc_id, operation, key_digest

    @contextmanager
    def _mutation_guard(self, poc_id: str, operation: str):
        """Serialize one POC while dependencies are consulted, rejecting reentry.

        The guard deliberately releases the service lock before the caller
        invokes any POC/proposal/planner/clock dependency. Concurrent callers
        wait and then observe the published idempotent result; a same-thread
        callback fails closed instead of recursively publishing another state.
        """

        owner = get_ident()
        with self._condition:
            while poc_id in self._inflight_pocs:
                current_owner, _ = self._inflight_pocs[poc_id]
                if current_owner == owner:
                    raise AgreementConflict("Reentrant agreement operation is not allowed.")
                self._condition.wait()
            self._inflight_pocs[poc_id] = (owner, operation)
        try:
            yield
        finally:
            with self._condition:
                self._inflight_pocs.pop(poc_id, None)
                self._condition.notify_all()

    def _current_inputs(self, poc_id: str) -> tuple[DraftPOCSnapshot, tuple[RetainedProposalProjection, ...], CapabilityPlan]:
        try:
            poc = self._poc_lookup(poc_id)
            retained_raw = self._retained_lookup(poc_id)
            plan_raw = self._planner.require_current(poc_id)
        except AgreementError:
            raise
        except CapabilityPlanningStaleProposal as error:
            raise AgreementStale("The current capability plan is stale for A3 retention.") from error
        except Exception as error:
            raise AgreementConflict("Current POC agreement inputs are unavailable.") from error
        if type(poc) is not DraftPOCSnapshot or poc.poc_id != poc_id:
            raise AgreementConflict("Current POC agreement inputs are unavailable.")
        if poc.archive_state is not DraftPOCArchiveState.ACTIVE:
            raise AgreementNotFound("The POC is not active.")
        if type(plan_raw) is not CapabilityPlan or plan_raw.poc_id != poc_id:
            raise AgreementConflict("The current capability plan is unavailable.")
        try:
            plan = CapabilityPlan.model_validate(plan_raw.model_dump(mode="python"))
        except Exception as error:
            raise AgreementConflict("The current capability plan is invalid.") from error
        if plan != plan_raw or plan.ready_for_agreement is not True:
            raise AgreementConflict("The current capability plan is not ready for agreement.")
        if type(retained_raw) not in {tuple, list}:
            raise AgreementConflict("Current retained proposals are unavailable.")
        retained: list[RetainedProposalProjection] = []
        seen: set[str] = set()
        for item_raw in retained_raw:
            if type(item_raw) is not RetainedProposalProjection or item_raw.poc_id != poc_id:
                raise AgreementConflict("Current retained proposals are unavailable.")
            try:
                item = RetainedProposalProjection.model_validate(item_raw.model_dump(mode="python"))
            except Exception as error:
                raise AgreementConflict("Current retained proposals are invalid.") from error
            if item != item_raw or item.proposal_id in seen:
                raise AgreementConflict("Current retained proposals are ambiguous.")
            seen.add(item.proposal_id)
            retained.append(item)
        if not retained:
            raise AgreementInvalid("At least one current retained proposal is required.")
        by_id = {item.proposal_id: item for item in retained}
        records_by_id: dict[str, PlanningRecord] = {}
        for record in plan.records:
            if record.proposal_id in records_by_id:
                raise AgreementConflict("The current capability plan is ambiguous.")
            records_by_id[record.proposal_id] = record
            if record.poc_id != poc_id or record.proposal_id not in by_id:
                raise AgreementConflict("A4 planning provenance does not match current A3 retention.")
            proposal = by_id[record.proposal_id]
            exact_bindings = (
                record.source_receipt_id == proposal.source_receipt_id,
                record.source_kind == proposal.source_kind,
                record.authoring_receipt_id == proposal.authoring_receipt_id,
                record.authoring_result_id == proposal.authoring_result_id,
                record.source_id == proposal.source_id,
                record.source_content_sha256 == proposal.source_content_sha256,
                record.source_revision == proposal.source_revision,
                record.source_quote == proposal.source_quote,
                record.normalized_claim == proposal.normalized_claim,
            )
            if not all(exact_bindings):
                raise AgreementStale("A4 planning provenance no longer matches current A3 retention.")
        if set(records_by_id) != set(by_id):
            raise AgreementConflict("A4 planning must contain every current retained proposal exactly once.")
        return poc, tuple(sorted(retained, key=lambda item: item.proposal_id)), plan

    def _stable_current_inputs(self, poc_id: str) -> tuple[DraftPOCSnapshot, tuple[RetainedProposalProjection, ...], CapabilityPlan]:
        """Read two dependency snapshots without holding the lifecycle lock."""

        first = self._current_inputs(poc_id)
        second = self._current_inputs(poc_id)
        if _snapshot_fingerprint(*first) != _snapshot_fingerprint(*second):
            raise AgreementStale("Current A3/A4 inputs changed during agreement assembly.")
        return second

    def _prepare_from_inputs(
        self,
        *,
        poc: DraftPOCSnapshot,
        retained: tuple[RetainedProposalProjection, ...],
        plan: CapabilityPlan,
        version: str,
        contract_id: str,
        parent_version: str | None,
        created_at: datetime,
        assembly_reviewer: str,
        assembly_rationale: str,
    ) -> AgreementPreparation:
        contract, limitations = _assemble_contract(
            poc=poc,
            retained=retained,
            plan=plan,
            version=version,
            contract_id=contract_id,
            parent_version=parent_version,
            created_at=created_at,
            assembly_reviewer=assembly_reviewer,
            assembly_rationale=assembly_rationale,
        )
        input_fingerprint = _snapshot_fingerprint(poc, retained, plan)
        receipt_payload = {
            "poc_id": poc.poc_id,
            "contract_id": contract.id,
            "contract_version": contract.version,
            "contract_fingerprint": contract_confirmation_fingerprint(contract),
            "input_fingerprint": input_fingerprint,
            "plan_id": plan.plan_id,
            "plan_version": plan.plan_version,
            "assembly_reviewer": assembly_reviewer,
            "assembly_rationale": assembly_rationale,
            "created_at": created_at.isoformat(),
        }
        draft_sha256 = hashlib.sha256(
            b"exitspec-generic-agreement-draft-v1\x00" + canonical_json_bytes(receipt_payload)
        ).hexdigest()
        return AgreementPreparation(
            poc=poc,
            contract=contract,
            plan=plan,
            retained_proposals=retained,
            limitations=limitations,
            prepared_at=created_at,
            input_fingerprint=input_fingerprint,
            draft_sha256=draft_sha256,
            assembly_reviewer=assembly_reviewer,
            assembly_rationale=assembly_rationale,
        )

    def _require_current_preparation(self, poc_id: str) -> AgreementPreparation:
        with self._lock:
            preparation = self._preparations.get(poc_id)
        if preparation is None:
            raise AgreementNotFound("Agreement preparation was not found.")
        poc, retained, plan = self._stable_current_inputs(poc_id)
        if _snapshot_fingerprint(poc, retained, plan) != preparation.input_fingerprint:
            raise AgreementStale("Agreement inputs changed after preparation.")
        try:
            rebuilt = self._prepare_from_inputs(
                poc=poc,
                retained=retained,
                plan=plan,
                version=preparation.contract.version,
                contract_id=preparation.contract.id,
                parent_version=preparation.contract.parent_version,
                created_at=preparation.prepared_at,
                assembly_reviewer=preparation.assembly_reviewer,
                assembly_rationale=preparation.assembly_rationale,
            )
        except AgreementError:
            raise
        if (
            rebuilt.contract.model_dump(mode="json") != preparation.contract.model_dump(mode="json")
            or rebuilt.draft_sha256 != preparation.draft_sha256
        ):
            # The clock is intentionally excluded from contract content, but
            # the immutable draft receipt must still remain tied to its input.
            raise AgreementStale("Agreement content changed after preparation.")
        return preparation

    def _token_for(self, invitation: CustomerReviewInvitation) -> str:
        payload = canonical_json_bytes(
            {
                "invitation_id": invitation.invitation_id,
                "contract_id": invitation.contract_id,
                "contract_version": invitation.contract_version,
                "confirmation_fingerprint": invitation.confirmation_fingerprint,
            }
        )
        digest = hmac.new(
            self._token_secret,
            b"exitspec-generic-agreement-review-v1\x00" + payload,
            hashlib.sha256,
        ).digest()
        import base64
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")

    def _issue_invitation(self, preparation: AgreementPreparation, created_at: datetime) -> CustomerReviewInvitation:
        invitation_id = f"review-{secrets.token_hex(12)}"
        fingerprint = preparation.contract_fingerprint
        token = self._token_for(
            CustomerReviewInvitation(
                invitation_id=invitation_id,
                contract_id=preparation.contract.id,
                contract_version=preparation.contract.version,
                confirmation_fingerprint=fingerprint,
                token_digest="0" * 64,
                created_at=created_at,
                expires_at=created_at + timedelta(hours=2),
            )
        )
        invitation, _ = issue_customer_review_invitation(
            contract_id=preparation.contract.id,
            contract_version=preparation.contract.version,
            confirmation_fingerprint=fingerprint,
            created_at=created_at,
            token=token,
            invitation_id=invitation_id,
        )
        return invitation

    def prepare(self, poc_id: str, *, reviewer: object, rationale: object, idempotency_key: object) -> AgreementWriteResult:
        poc_id = _validate_poc_id(poc_id)
        reviewer_text = _safe_text(reviewer, "reviewer", MAX_REVIEWER_LENGTH, single_line=True)
        rationale_text = _safe_text(rationale, "rationale", MAX_RATIONALE_LENGTH)
        key_digest = _idempotency_digest(idempotency_key)
        operation_key = self._operation_key(poc_id, "PREPARE", key_digest)
        with self._mutation_guard(poc_id, "PREPARE"):
            poc, retained, plan = self._stable_current_inputs(poc_id)
            contract_id = _contract_id(poc_id, plan)
            input_fingerprint = _snapshot_fingerprint(poc, retained, plan)
            request_sha256 = _request_digest(
                {
                    "operation": "PREPARE",
                    "poc_id": poc_id,
                    "contract_id": contract_id,
                    "plan_id": plan.plan_id,
                    "reviewer": reviewer_text,
                    "rationale": rationale_text,
                    "input_fingerprint": input_fingerprint,
                    "plan_version": plan.plan_version,
                    "plan_sha256": _plan_digest(plan),
                }
            )
            with self._lock:
                prior = self._prepare_idempotency.get(operation_key)
                if prior is not None:
                    if prior.request_sha256 != request_sha256:
                        raise AgreementConflict("Idempotency key conflicts with an earlier preparation.")
                    return AgreementWriteResult(self._preparations[poc_id], True)
                if poc_id in self._preparations:
                    raise AgreementConflict("This POC already has a prepared agreement.")
                if poc_id not in self._history and len(set(self._preparations) | set(self._history)) >= self._max_agreements:
                    raise AgreementCapacityExceeded("Agreement capacity is exhausted.")
            created_at = self._clock()
            preparation = self._prepare_from_inputs(
                poc=poc,
                retained=retained,
                plan=plan,
                version="1",
                contract_id=contract_id,
                parent_version=None,
                created_at=created_at,
                assembly_reviewer=reviewer_text,
                assembly_rationale=rationale_text,
            )
            invitation = self._issue_invitation(preparation, created_at)
            with self._lock:
                self._preparations[poc_id] = preparation
                self._invitations[poc_id] = invitation
                self._prepare_idempotency[operation_key] = _IdempotencyRecord(request_sha256, poc_id)
            return AgreementWriteResult(preparation, False)

    def customer_review_url_for(self, invitation: CustomerReviewInvitation) -> str:
        return f"/review/{self._token_for(invitation)}"

    def customer_review_url(self, poc_id: str) -> str:
        with self._lock:
            try:
                return self.customer_review_url_for(self._invitations[poc_id])
            except KeyError as error:
                raise AgreementNotFound("Customer review link was not found.") from error

    def customer_review_poc_id(self, token: object) -> str | None:
        if type(token) is not str or not token:
            return None
        with self._lock:
            for poc_id, invitation in self._invitations.items():
                candidate = self._token_for(invitation)
                if hmac.compare_digest(candidate, token):
                    return poc_id
        return None

    def _require_invitation(self, token: object) -> tuple[str, AgreementPreparation, CustomerReviewInvitation]:
        poc_id = self.customer_review_poc_id(token)
        if poc_id is None or type(token) is not str:
            raise ReviewInvitationError("Customer review link is invalid.")
        with self._lock:
            preparation = self._preparations.get(poc_id)
            invitation = self._invitations.get(poc_id)
        if preparation is None or invitation is None:
            raise ReviewInvitationError("Customer review link is invalid.")
        preparation = self._require_current_preparation(poc_id)
        now = self._clock()
        invitation.require_valid(token, now=now)
        if (
            invitation.contract_id != preparation.contract.id
            or invitation.contract_version != preparation.contract.version
            or invitation.confirmation_fingerprint != preparation.contract_fingerprint
        ):
            raise ReviewInvitationError("Customer review link no longer matches the current agreement.")
        return poc_id, preparation, invitation

    def customer_review_payload(self, token: object) -> dict[str, Any]:
        poc_id = self.customer_review_poc_id(token)
        if poc_id is None:
            raise ReviewInvitationError("Customer review link is invalid.")
        with self._mutation_guard(poc_id, "CUSTOMER_REVIEW_READ"):
            poc_id, preparation, invitation = self._require_invitation(token)
            with self._lock:
                confirmation = self._confirmations.get(poc_id)
            if confirmation is None:
                status = "PENDING"
            elif confirmation.decision is ConfirmationDecision.CONFIRM:
                status = "CONFIRMED"
            else:
                status = "CHANGES_REQUESTED"
            agreement = canonical_confirmation_payload(preparation.contract)
            return {
                "mode": "local_source_neutral_agreement_review",
                "safety": {
                    "synthetic_only": True,
                    "not_evidence": True,
                    "not_production_authorization": True,
                    "no_downstream_authority": True,
                },
                "review": {
                    "review_id": invitation.invitation_id,
                    "status": status,
                    "contract_id": preparation.contract.id,
                    "contract_version": preparation.contract.version,
                    "confirmation_fingerprint": preparation.contract_fingerprint,
                    "customer": agreement["customer"],
                    "use_case": agreement["use_case"],
                    "agreement": agreement,
                    "criteria": [self._customer_criterion(item) for item in agreement["criteria"]],
                    "limitations": list(agreement["non_goals"]),
                    "non_goals": list(agreement["non_goals"]),
                    "owners": agreement["owners"],
                    "workload": agreement["workload"],
                    "evidence_retention_policy": agreement["evidence_retention_policy"],
                    "expires_at": invitation.expires_at.isoformat(),
                    "acknowledgement_required": True,
                    "identity": {
                        "display_name": "Customer approver · capability review",
                        "notice": "This local review records a decision only; it does not authorize execution or deployment.",
                    },
                    "local_demo": {
                        "return_url": f"/app/pocs/{poc_id}/agreement",
                        "notice": "Local loopback demo only.",
                    },
                    "decision": self._decision_payload(confirmation),
                },
                "confirmation": self._confirmation_payload(confirmation),
            }

    @staticmethod
    def _customer_criterion(criterion: Mapping[str, Any]) -> dict[str, Any]:
        payload = dict(criterion)
        source = payload.get("source")
        operator = str(payload.get("operator", ""))
        threshold = f"{operator} {payload.get('threshold')} {payload.get('unit')}"
        sample = str(payload.get("measurement_population", ""))
        return {
            "id": payload["id"],
            "title": payload["title"],
            "normalized_claim": payload["normalized_claim"],
            "plain_language": payload["normalized_claim"],
            "source": source,
            "source_quote": "Human-added requirement" if source is None else source["quote"],
            "metric": payload.get("rule"),
            "threshold": threshold,
            "sample": sample,
            "workload": payload.get("measurement_population", ""),
            "workload_slice": payload.get("measurement_population", ""),
            "adapter": payload.get("adapter"),
            "adapter_version": payload.get("adapter_version"),
            "owner": payload.get("owner"),
            "evidence_policy": payload.get("evidence_policy"),
            "must_have": payload.get("must_have"),
            "required": payload.get("must_have"),
            "agreement": payload,
            "excluded": [],
        }

    @staticmethod
    def _confirmation_payload(confirmation: ContractConfirmation | None) -> dict[str, Any] | None:
        if confirmation is None:
            return None
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

    @staticmethod
    def _decision_payload(confirmation: ContractConfirmation | None) -> dict[str, Any] | None:
        if confirmation is None:
            return None
        return {
            "decision": confirmation.decision.value,
            "reviewer_display_name": confirmation.confirmer_identity,
            "recorded_at": confirmation.decided_at.isoformat(),
            "rationale": confirmation.rationale,
            "agreement_acknowledged": confirmation.agreement_acknowledged,
            "idempotent_replay": False,
            "synthetic": False,
        }

    def record_customer_review_decision(
        self,
        token: object,
        *,
        decision: object,
        agreement_acknowledged: object,
        rationale: object,
        idempotency_key: object,
        contract_id: object | None = None,
        contract_version: object | None = None,
        confirmation_fingerprint: object | None = None,
    ) -> AgreementWriteResult:
        try:
            requested = ConfirmationDecision(decision)
        except (TypeError, ValueError) as error:
            raise AgreementInvalid("Customer review decision is invalid.") from error
        if type(agreement_acknowledged) is not bool or agreement_acknowledged is not True:
            raise AgreementInvalid("Explicit agreement acknowledgement is required.")
        rationale_text = _safe_text(rationale, "rationale", MAX_RATIONALE_LENGTH)
        key_digest = _idempotency_digest(idempotency_key)
        poc_id = self.customer_review_poc_id(token)
        if poc_id is None:
            raise ReviewInvitationError("Customer review link is invalid.")
        operation_key = self._operation_key(poc_id, "CUSTOMER_REVIEW_DECISION", key_digest)
        with self._mutation_guard(poc_id, "CUSTOMER_REVIEW_DECISION"):
            poc_id, preparation, invitation = self._require_invitation(token)
            if contract_id is not None and contract_id != preparation.contract.id:
                raise AgreementConflict("The decision is bound to a different contract.")
            if contract_version is not None and contract_version != preparation.contract.version:
                raise AgreementConflict("The decision is bound to a different contract version.")
            if confirmation_fingerprint is not None and confirmation_fingerprint != preparation.contract_fingerprint:
                raise AgreementConflict("The decision fingerprint does not match the current agreement.")
            request_sha256 = _request_digest(
                {
                    "operation": "CUSTOMER_REVIEW_DECISION",
                    "poc_id": poc_id,
                    "contract_id": preparation.contract.id,
                    "contract_version": preparation.contract.version,
                    "confirmation_fingerprint": preparation.contract_fingerprint,
                    "decision": requested.value,
                    "agreement_acknowledged": agreement_acknowledged,
                    "rationale": rationale_text,
                }
            )
            with self._lock:
                prior = self._decision_idempotency.get(operation_key)
            if prior is not None:
                if prior.request_sha256 != request_sha256:
                    raise AgreementConflict("Idempotency key conflicts with an earlier decision.")
                with self._lock:
                    return AgreementWriteResult(self._confirmations[poc_id], True)
            with self._lock:
                if poc_id in self._confirmations:
                    raise AgreementConflict("This agreement version already has a terminal decision.")
            decided_at = self._clock()
            confirmation = record_confirmation(
                preparation.contract,
                confirmer_identity="Customer approver · capability review",
                decision=requested,
                agreement_acknowledged=True,
                rationale=rationale_text,
                idempotency_key=key_digest,
                decided_at=decided_at,
            )
            with self._lock:
                self._confirmations[poc_id] = confirmation
                self._decision_idempotency[operation_key] = _IdempotencyRecord(request_sha256, poc_id)
            return AgreementWriteResult(confirmation, False)

    def start_revision(
        self,
        poc_id: str,
        *,
        reviewer: object,
        rationale: object,
        idempotency_key: object,
    ) -> AgreementWriteResult:
        poc_id = _validate_poc_id(poc_id)
        reviewer_text = _safe_text(reviewer, "reviewer", MAX_REVIEWER_LENGTH, single_line=True)
        rationale_text = _safe_text(rationale, "rationale", MAX_RATIONALE_LENGTH)
        key_digest = _idempotency_digest(idempotency_key)
        operation_key = self._operation_key(poc_id, "START_REVISION", key_digest)
        with self._mutation_guard(poc_id, "START_REVISION"):
            with self._lock:
                preparation = self._preparations.get(poc_id)
                confirmation = self._confirmations.get(poc_id)
                invitation = self._invitations.get(poc_id)
                history = self._history.get(poc_id, ())
            if preparation is None:
                raise AgreementNotFound("Agreement preparation was not found.")
            poc, retained, plan = self._stable_current_inputs(poc_id)
            current_input_fingerprint = _snapshot_fingerprint(poc, retained, plan)
            with self._lock:
                prior = self._revision_idempotency.get(operation_key)
                prior_revision = self._revision_results.get(operation_key)
            if prior is not None and prior_revision is not None:
                expected_request_sha256 = _request_digest(
                    {
                        "operation": "START_REVISION",
                        "poc_id": poc_id,
                        "parent_draft_sha256": prior_revision.parent_draft_sha256,
                        "successor_input_fingerprint": current_input_fingerprint,
                        "plan_id": plan.plan_id,
                        "plan_version": plan.plan_version,
                        "plan_sha256": _plan_digest(plan),
                        "reviewer": reviewer_text,
                        "rationale": rationale_text,
                    }
                )
                if prior.request_sha256 != expected_request_sha256:
                    raise AgreementConflict("Idempotency key conflicts with an earlier revision.")
                if current_input_fingerprint != prior_revision.successor_input_fingerprint:
                    raise AgreementConflict("The current A3/A4 snapshot changed after revision publication.")
                return AgreementWriteResult(prior_revision, True)
            if confirmation is None or confirmation.decision is not ConfirmationDecision.REQUEST_CHANGES or invitation is None:
                raise AgreementConflict("A customer REQUEST_CHANGES decision is required before revision.")
            try:
                parent_version_number = int(preparation.contract.version)
            except ValueError as error:
                raise AgreementConflict("The current contract version cannot be revised safely.") from error
            if len(history) >= MAX_REVISIONS_PER_POC:
                raise AgreementCapacityExceeded("Revision capacity is exhausted.")

            # A revision binds a fresh current snapshot and a fresh named
            # human assembly approval. Neither is inherited from the parent.
            if current_input_fingerprint == preparation.input_fingerprint:
                raise AgreementConflict("A revision requires a materially changed current A3/A4 snapshot.")
            request_sha256 = _request_digest(
                {
                    "operation": "START_REVISION",
                    "poc_id": poc_id,
                    "parent_draft_sha256": preparation.draft_sha256,
                    "successor_input_fingerprint": current_input_fingerprint,
                    "plan_id": plan.plan_id,
                    "plan_version": plan.plan_version,
                    "plan_sha256": _plan_digest(plan),
                    "reviewer": reviewer_text,
                    "rationale": rationale_text,
                }
            )
            requested_at = self._clock()
            successor = self._prepare_from_inputs(
                poc=poc,
                retained=retained,
                plan=plan,
                version=str(parent_version_number + 1),
                contract_id=preparation.contract.id,
                parent_version=f"{preparation.contract.id}@{preparation.contract.version}",
                created_at=requested_at,
                assembly_reviewer=reviewer_text,
                assembly_rationale=rationale_text,
            )
            revision_payload = {
                "poc_id": poc_id,
                "parent_contract_id": preparation.contract.id,
                "parent_contract_version": preparation.contract.version,
                "parent_draft_sha256": preparation.draft_sha256,
                "successor_draft_sha256": successor.draft_sha256,
                "successor_contract_fingerprint": successor.contract_fingerprint,
                "successor_input_fingerprint": current_input_fingerprint,
                "request_rationale": confirmation.rationale,
                "assembly_reviewer": reviewer_text,
                "assembly_rationale": rationale_text,
                "requested_at": requested_at.isoformat(),
            }
            revision_sha256 = hashlib.sha256(
                b"exitspec-generic-agreement-revision-v1\x00" + canonical_json_bytes(revision_payload)
            ).hexdigest()
            revision = AgreementRevision(
                revision_id=f"agrrev_{revision_sha256[:32]}",
                revision_number=parent_version_number,
                parent_contract_id=preparation.contract.id,
                parent_contract_version=preparation.contract.version,
                parent_draft_sha256=preparation.draft_sha256,
                requested_at=requested_at,
                request_rationale=confirmation.rationale,
                assembly_reviewer=reviewer_text,
                assembly_rationale=rationale_text,
                successor_input_fingerprint=current_input_fingerprint,
                successor_draft_sha256=successor.draft_sha256,
                successor_contract_fingerprint=successor.contract_fingerprint,
            )
            replacement_invitation = self._issue_invitation(successor, requested_at)
            with self._lock:
                self._history[poc_id] = history + (AgreementVersionRecord(preparation, invitation, confirmation, requested_at),)
                self._revisions[poc_id] = revision
                self._preparations[poc_id] = successor
                self._invitations[poc_id] = replacement_invitation
                self._confirmations.pop(poc_id, None)
                self._frozen.pop(poc_id, None)
                self._revision_idempotency[operation_key] = _IdempotencyRecord(request_sha256, poc_id)
                self._revision_results[operation_key] = revision
            return AgreementWriteResult(revision, False)

    def reissue_customer_review(self, poc_id: str, *, idempotency_key: object) -> AgreementWriteResult:
        poc_id = _validate_poc_id(poc_id)
        key_digest = _idempotency_digest(idempotency_key)
        operation_key = self._operation_key(poc_id, "REISSUE_REVIEW", key_digest)
        with self._mutation_guard(poc_id, "REISSUE_REVIEW"):
            preparation = self._require_current_preparation(poc_id)
            with self._lock:
                invitation = self._invitations[poc_id]
                confirmation = self._confirmations.get(poc_id)
            if confirmation is not None:
                raise AgreementConflict("A decided agreement cannot issue another review link.")
            now = self._clock()
            if now < invitation.expires_at:
                raise AgreementConflict("The current customer review link is still active.")
            request_sha256 = _request_digest({"operation": "REISSUE_REVIEW", "poc_id": poc_id, "contract": preparation.contract_fingerprint})
            with self._lock:
                prior = self._review_idempotency.get(operation_key)
            if prior is not None:
                if prior.request_sha256 != request_sha256:
                    raise AgreementConflict("Idempotency key conflicts with an earlier review link.")
                with self._lock:
                    return AgreementWriteResult(self._invitations[poc_id], True)
            replacement = self._issue_invitation(preparation, now)
            with self._lock:
                self._invitations[poc_id] = replacement
                self._review_idempotency[operation_key] = _IdempotencyRecord(request_sha256, poc_id)
            return AgreementWriteResult(replacement, False)

    def freeze(self, poc_id: str, *, idempotency_key: object) -> AgreementWriteResult:
        poc_id = _validate_poc_id(poc_id)
        key_digest = _idempotency_digest(idempotency_key)
        operation_key = self._operation_key(poc_id, "FREEZE", key_digest)
        with self._mutation_guard(poc_id, "FREEZE"):
            preparation = self._require_current_preparation(poc_id)
            with self._lock:
                confirmation = self._confirmations.get(poc_id)
            if confirmation is None:
                raise AgreementConflict("Customer confirmation is required before freeze.")
            request_sha256 = _request_digest({"operation": "FREEZE", "poc_id": poc_id, "draft_sha256": preparation.draft_sha256, "contract_version": preparation.contract.version})
            with self._lock:
                prior = self._freeze_idempotency.get(operation_key)
            if prior is not None:
                if prior.request_sha256 != request_sha256:
                    raise AgreementConflict("Idempotency key conflicts with an earlier freeze.")
                with self._lock:
                    return AgreementWriteResult(self._frozen[poc_id], True)
            with self._lock:
                already_frozen = poc_id in self._frozen
            if already_frozen:
                raise AgreementConflict("This agreement is already frozen.")
            if not confirmation_matches_contract(preparation.contract, confirmation):
                raise AgreementConflict("Only a matching affirmative confirmation can freeze this agreement.")
            try:
                frozen = freeze_confirmed_contract(preparation.contract, confirmation, frozen_at=self._clock())
            except ValueError as error:
                raise AgreementConflict("The exact customer confirmation could not freeze this agreement.") from error
            if frozen.confirmation_id != confirmation.confirmation_id or not contract_digest(frozen) == frozen.canonical_hash:
                raise AgreementConflict("Frozen agreement integrity verification failed.")
            with self._lock:
                self._frozen[poc_id] = frozen
                self._freeze_idempotency[operation_key] = _IdempotencyRecord(request_sha256, poc_id)
            return AgreementWriteResult(frozen, False)

    def history(self, poc_id: str) -> tuple[AgreementVersionRecord, ...]:
        with self._lock:
            return self._history.get(poc_id, ())

    def snapshot(self, poc_id: str, *, allow_empty: bool = True) -> AgreementSnapshot:
        poc_id = _validate_poc_id(poc_id)
        with self._lock:
            preparation = self._preparations.get(poc_id)
            if preparation is None:
                if not allow_empty:
                    raise AgreementNotFound("Agreement preparation was not found.")
                return AgreementSnapshot(
                    None, None, False, None, None, self._revisions.get(poc_id),
                    len(self._history.get(poc_id, ())), False,
                )
        with self._mutation_guard(poc_id, "AGREEMENT_SNAPSHOT"):
            try:
                current = self._stable_current_inputs(poc_id)
                current_inputs_stale = _snapshot_fingerprint(*current) != preparation.input_fingerprint
            except AgreementError:
                current_inputs_stale = True
            now = self._clock()
            with self._lock:
                invitation = self._invitations.get(poc_id)
                confirmation = self._confirmations.get(poc_id)
                frozen = self._frozen.get(poc_id)
                revision = self._revisions.get(poc_id)
                history_count = len(self._history.get(poc_id, ()))
            return AgreementSnapshot(
                preparation,
                invitation,
                bool(invitation is not None and now >= invitation.expires_at),
                confirmation,
                frozen,
                revision,
                history_count,
                current_inputs_stale,
            )

    def snapshot_payload(self, poc_id: str) -> dict[str, Any]:
        snapshot = self.snapshot(poc_id)
        preparation = snapshot.preparation
        if preparation is None:
            return {"poc_id": poc_id, "agreement": None, "draft": None, "customer_review": None, "confirmation": None, "frozen_contract": None, "revision": None, "superseded_version_count": snapshot.superseded_version_count}
        contract = preparation.contract
        invitation = snapshot.review_invitation
        fingerprint = preparation.contract_fingerprint
        return {
            "poc_id": poc_id,
            "agreement": canonical_confirmation_payload(contract),
            "plan": {
                "plan_id": preparation.plan.plan_id,
                "plan_version": preparation.plan.plan_version,
                "plan_fingerprint": _plan_digest(preparation.plan),
                "ready_for_agreement": preparation.plan.ready_for_agreement,
            },
            "current_inputs_stale": snapshot.current_inputs_stale,
            "limitations": list(preparation.limitations),
            "draft": {
                "draft_id": f"agd_{preparation.draft_sha256[:32]}",
                "draft_sha256": preparation.draft_sha256,
                "contract_id": contract.id,
                "contract_version": contract.version,
                "contract_fingerprint": fingerprint,
                "created_at": contract.created_at.isoformat(),
                "parent_version": contract.parent_version,
            },
            "customer_review": None if invitation is None else {
                "review_id": invitation.invitation_id,
                "status": "STALE" if snapshot.current_inputs_stale else ("EXPIRED" if snapshot.review_expired else ("CONFIRMED" if snapshot.confirmation and snapshot.confirmation.decision is ConfirmationDecision.CONFIRM else "CHANGES_REQUESTED" if snapshot.confirmation else "PENDING")),
                "contract_id": invitation.contract_id,
                "contract_version": invitation.contract_version,
                "confirmation_fingerprint": invitation.confirmation_fingerprint,
                "review_url": self.customer_review_url_for(invitation),
                "created_at": invitation.created_at.isoformat(),
                "expires_at": invitation.expires_at.isoformat(),
            },
            "confirmation": self._confirmation_payload(snapshot.confirmation),
            "frozen_contract": None if snapshot.frozen_contract is None else snapshot.frozen_contract.model_dump(mode="json"),
            "revision": None if snapshot.revision is None else {
                "revision_id": snapshot.revision.revision_id,
                "revision_number": snapshot.revision.revision_number,
                "contract_version": snapshot.revision.contract_version,
                "parent_contract_id": snapshot.revision.parent_contract_id,
                "parent_contract_version": snapshot.revision.parent_contract_version,
                "parent_draft_sha256": snapshot.revision.parent_draft_sha256,
                "assembly_reviewer": snapshot.revision.assembly_reviewer,
                "assembly_rationale": snapshot.revision.assembly_rationale,
                "successor_input_fingerprint": snapshot.revision.successor_input_fingerprint,
                "successor_draft_sha256": snapshot.revision.successor_draft_sha256,
                "successor_contract_fingerprint": snapshot.revision.successor_contract_fingerprint,
                "requested_at": snapshot.revision.requested_at.isoformat(),
                "request_rationale": snapshot.revision.request_rationale,
            },
            "superseded_version_count": snapshot.superseded_version_count,
        }


# Friendly names for callers using the shorter A5 vocabulary.
AgreementLifecycleService = ProcessLocalAgreementLifecycleService
AgreementLifecycle = ProcessLocalAgreementLifecycleService


__all__ = [
    "A5_EXECUTION_POLICY_ID", "A5_EXECUTION_POLICY_SHA256", "A5_WORKLOAD_POLICY_URI",
    "AgreementCapacityExceeded", "AgreementConflict", "AgreementError", "AgreementInvalid",
    "AgreementLifecycle", "AgreementLifecycleService", "AgreementNotFound", "AgreementPreparation",
    "AgreementRevision", "AgreementSnapshot", "AgreementStale", "AgreementVersionRecord",
    "AgreementWriteResult", "ProcessLocalAgreementLifecycleService",
]
