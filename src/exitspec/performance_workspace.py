"""Read-only workspace projection for the bundled inference-performance POC.

This module makes the already frozen synthetic performance agreement visible in
the local product shell. It deliberately owns no execution, persistence, or
verdict authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files
from typing import Final

from pydantic import Field

from .confirmations import ContractConfirmation
from .models import (
    ContractStatus,
    FrozenExitSpecModel,
    InferencePerformanceCriterion,
    POCContract,
)
from .performance_evidence import (
    ValidatedPerformanceContext,
    require_frozen_confirmed,
    validate_performance_context_bytes,
)
from .workspace import (
    ArchiveState,
    POCRegistryEntry,
    POCWorkflowFacts,
    WorkspaceEvidenceState,
    WorkspacePhase,
    WorkspaceSourceType,
)


PERFORMANCE_POC_ID: Final = "poc_inference_latency_demo"
PERFORMANCE_CONTRACT_HASH: Final = (
    "88c4f55dd1a0810efa59fac1bd1041a21c3cbe1179ceb3e101e75000eb7d909f"
)
_RESOURCE_ERROR: Final = (
    "ExitSpec's bundled inference-performance workspace resources are invalid."
)


class PerformanceRuleProjection(FrozenExitSpecModel):
    """One customer-visible rule from the frozen composite criterion."""

    rule_id: str
    label: str
    threshold: str
    sample_requirement: str


class PerformanceRunPlanProjection(FrozenExitSpecModel):
    """A compact display of the exact workload shape, not observed results."""

    measured_requests: int = Field(gt=0)
    configured_concurrency: int = Field(gt=0)
    warmup_requests: int = Field(ge=0)
    model: str = Field(min_length=1)
    endpoint_class: str = Field(min_length=1)


class PerformanceTechnicalProjection(FrozenExitSpecModel):
    """Secondary identities needed to audit the frozen run boundary."""

    contract_id: str
    contract_version: str
    contract_hash: str
    confirmation_id: str
    workload_id: str
    workload_hash: str
    adapter: str
    adapter_version: str
    first_token_definition: str


class PerformancePOCDetailProjection(FrozenExitSpecModel):
    """Read-only product detail for one not-yet-run performance POC."""

    poc_id: str
    display_name: str
    customer_label: str
    use_case: str
    owner: str
    phase: WorkspacePhase
    agreement_status: ContractStatus
    customer_status: str
    execution_status: str
    evidence_status: WorkspaceEvidenceState
    evidence_reason: str
    requirements: tuple[PerformanceRuleProjection, ...]
    run_plan: PerformanceRunPlanProjection
    limitation: str
    technical: PerformanceTechnicalProjection


@dataclass(frozen=True, slots=True)
class PerformanceDemoBundle:
    """Validated immutable resources behind the performance workspace."""

    context: ValidatedPerformanceContext
    confirmation: ContractConfirmation


def _read_resource(*parts: str) -> bytes:
    resource = files("exitspec.demo_data").joinpath("inference_performance")
    for part in parts:
        resource = resource.joinpath(part)
    return resource.read_bytes()


@lru_cache(maxsize=1)
def load_performance_demo_bundle() -> PerformanceDemoBundle:
    """Load and validate the exact contract, confirmation, workload, and prompts."""

    try:
        contract = POCContract.model_validate_json(
            _read_resource("contracts", "vllm-ttft-v2.frozen.json"),
            strict=True,
        )
        if contract.canonical_hash != PERFORMANCE_CONTRACT_HASH:
            raise ValueError("Unexpected performance contract identity.")
        confirmation = ContractConfirmation.model_validate_json(
            _read_resource("contracts", "vllm-ttft-v2.confirmation.json"),
            strict=True,
        )
        context = validate_performance_context_bytes(
            contract,
            _read_resource("workloads", "concurrency-4-v1.json"),
            _read_resource("prompts", "synthetic-latency-v1.jsonl"),
        )
        require_frozen_confirmed(context, confirmation)
        return PerformanceDemoBundle(
            context=context,
            confirmation=confirmation,
        )
    except Exception as error:
        raise RuntimeError(_RESOURCE_ERROR) from error


def performance_workspace_record_and_facts(
) -> tuple[POCRegistryEntry, POCWorkflowFacts]:
    """Return dashboard inputs without creating run or verdict state."""

    bundle = load_performance_demo_bundle()
    contract = bundle.context.contract
    frozen_at = contract.frozen_at
    if frozen_at is None:
        raise RuntimeError(_RESOURCE_ERROR)
    owner = contract.owners[-1]
    record = POCRegistryEntry(
        poc_id=PERFORMANCE_POC_ID,
        display_name="Inference-latency POC",
        customer_label=contract.customer,
        use_case=contract.use_case,
        owner=owner,
        created_at=contract.created_at,
        updated_at=frozen_at,
        archive_state=ArchiveState.ACTIVE,
    )
    facts = POCWorkflowFacts(
        source_count=1,
        source_types=(WorkspaceSourceType.MEETING_TRANSCRIPT,),
        approved_criterion_count=len(contract.criteria),
        active_contract_id=contract.id,
        active_contract_version=contract.version,
        contract_status=contract.status,
        customer_review_issued=True,
        customer_decision=bundle.confirmation.decision,
        confirmation_matches_active_contract=True,
        action_since=frozen_at,
    )
    return record, facts


def performance_poc_detail_payload() -> dict[str, object]:
    """Project the frozen agreement and explicit absence of measured evidence."""

    bundle = load_performance_demo_bundle()
    context = bundle.context
    contract = context.contract
    criterion = context.criterion
    workload = context.workload
    if not isinstance(criterion, InferencePerformanceCriterion):
        raise RuntimeError(_RESOURCE_ERROR)

    ttft_operator = "<" if criterion.ttft_p95.operator == "lt" else "≤"
    requirements = (
        PerformanceRuleProjection(
            rule_id="ttft_p95",
            label="P95 time to first token",
            threshold="{0} {1:g} ms".format(
                ttft_operator,
                criterion.ttft_p95.threshold,
            ),
            sample_requirement="At least {0} successful samples".format(
                criterion.ttft_p95.minimum_successful_samples
            ),
        ),
        PerformanceRuleProjection(
            rule_id="error_rate",
            label="Request error rate",
            threshold="< {0:g}%".format(
                criterion.error_rate.threshold * 100
            ),
            sample_requirement="{0} attempted requests".format(
                criterion.error_rate.minimum_attempts
            ),
        ),
    )
    confirmation_id = contract.confirmation_id
    contract_hash = contract.canonical_hash
    if confirmation_id is None or contract_hash is None:
        raise RuntimeError(_RESOURCE_ERROR)

    projection = PerformancePOCDetailProjection(
        poc_id=PERFORMANCE_POC_ID,
        display_name="Inference-latency POC",
        customer_label=contract.customer,
        use_case=contract.use_case,
        owner=contract.owners[-1],
        phase=WorkspacePhase.PROVE,
        agreement_status=contract.status,
        customer_status="CONFIRMED",
        execution_status="NOT_STARTED",
        evidence_status=WorkspaceEvidenceState.NOT_RUN,
        evidence_reason=(
            "No verified performance run has been completed or projected."
        ),
        requirements=requirements,
        run_plan=PerformanceRunPlanProjection(
            measured_requests=workload.request_count,
            configured_concurrency=workload.concurrency,
            warmup_requests=workload.warmup_count,
            model=workload.model,
            endpoint_class=contract.target_system.endpoint_class,
        ),
        limitation=contract.non_goals[0],
        technical=PerformanceTechnicalProjection(
            contract_id=contract.id,
            contract_version=contract.version,
            contract_hash=contract_hash,
            confirmation_id=confirmation_id,
            workload_id=workload.workload_id,
            workload_hash=context.workload_sha256,
            adapter=criterion.adapter,
            adapter_version=criterion.adapter_version,
            first_token_definition=workload.first_token_definition,
        ),
    )
    return projection.model_dump(mode="json")


__all__ = [
    "PERFORMANCE_CONTRACT_HASH",
    "PERFORMANCE_POC_ID",
    "PerformanceDemoBundle",
    "PerformancePOCDetailProjection",
    "load_performance_demo_bundle",
    "performance_poc_detail_payload",
    "performance_workspace_record_and_facts",
]
