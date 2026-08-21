"""Pure v3 agreement assembly for one selected managed Inferdrome bundle.

The selected bundle is verified before this module receives it.  This module
binds its complete supported identity into a customer-reviewable contract, but
grants no import, verdict, or execution authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
import hashlib
from typing import Any, Literal, Sequence

from pydantic import Field

from .canonical import canonical_json_bytes
from .inferdrome_bundle import VerifiedInferdromeBundle
from .inferdrome_external_contract import (
    MANAGED_TARGET_ENDPOINT_CLASS,
    MANAGED_TARGET_PROVIDER,
)
from .inferdrome_profile import (
    LOCAL_GPU_PROOF_SCHEMA_ID,
    LOCAL_GPU_PROOF_SCHEMA_SHA256,
    MANAGED_PROFILE_ID,
    MANAGED_PROFILE_SHA256,
    PINNED_BUNDLE_DIGEST,
    PINNED_RUN_ID,
)
from .models import (
    ContractStatus,
    ExternalErrorRateRuleV1,
    ExternalTTFTP95RuleV1,
    FrozenExitSpecModel,
    InferdromeEvidenceIdentityV1,
    InferencePerformanceCriterionV3,
    POCContract,
    SourceReference,
    TargetSystem,
    WorkloadReference,
)
from .poc_contract_definition import (
    ContractDefinitionOperator,
    ContractDefinitionReceipt,
)
from .poc_creation import DraftPOCArchiveState, DraftPOCSnapshot
from .poc_performance_contract import (
    PerformanceContractAssemblyError,
    PerformanceDefinitionBinding,
    PerformanceEvidenceMethod,
    PerformanceTargetInput,
    _definition_pair,
    _excluded_claim_limitations,
    _kept_proposals,
    _require_matching_workload,
)
from .poc_proposal_review import ProposalReviewItem


MANAGED_NATIVE_TTFT_DEFINITION = "vllm_first_choices_event_v0_26"
MANAGED_NATIVE_REDUCER = "nearest_rank_v1"
MANAGED_NATIVE_LATENCY_POPULATION = (
    "successful_measured_requests_with_observed_ttft"
)
MANAGED_EXACT_ATTEMPTS = 100


class ManagedInferdromeContractAssemblyError(PerformanceContractAssemblyError):
    """A selected managed bundle cannot form the requested v3 agreement."""


class ManagedInferdromeEvidenceProjection(FrozenExitSpecModel):
    """Pathless, verified facts safe to bind into one customer contract."""

    schema_version: Literal["exitspec.managed-inferdrome-projection.v1"] = (
        "exitspec.managed-inferdrome-projection.v1"
    )
    run_id: Literal["run-533c9f5f783958fb6077069a6c577144"]
    bundle_digest: Literal[
        "sha256:bae216f2165eb06ae2e0f14d3cd852f8e0ebb381bf1f68c71072769b3c0c1675"
    ]
    evidence_schema_version: Literal["inferdrome.evidence.v1"]
    producer_name: Literal["vllm"]
    producer_version: Literal["0.26.0"]
    adapter_id: Literal["vllm_bench_serve"]
    adapter_version: Literal["1.0.0"]
    native_schema_fingerprint: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    managed_profile_id: Literal[
        "inferdrome.managed-vllm-0.26-evidence-profile.v1"
    ]
    managed_profile_sha256: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    local_gpu_proof_schema_id: Literal["urn:inferdrome:local-gpu-proof:v1"]
    local_gpu_proof_schema_sha256: str = Field(
        pattern=r"^sha256:[a-f0-9]{64}$"
    )
    request_plan_digest: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    workload_digest: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    workload_path: str = Field(min_length=1, max_length=512)
    target_model: str = Field(min_length=1, max_length=512)
    target_model_revision: str = Field(pattern=r"^[a-f0-9]{40}$")
    target_tokenizer_revision: str = Field(pattern=r"^[a-f0-9]{40}$")
    target_endpoint: str = Field(pattern=r"^http://127\.0\.0\.1:[0-9]{1,5}/$")
    observed_configured_max_concurrency: int = Field(gt=0, le=1_000)
    exact_measured_attempts: Literal[100]
    warmup_requests: int = Field(ge=0, le=1_000)
    metric_definition_id: Literal["vllm_first_choices_event_v0_26"]
    gpu_models: tuple[str, ...] = Field(min_length=1, max_length=8)
    claims_assurance: Literal["INTERNAL_CONSISTENCY_ONLY"]
    native_response_content_present: bool


class ManagedInferdromeWorkloadProjection(FrozenExitSpecModel):
    """Small common projection used by the existing proof-state API."""

    workload_id: str = Field(pattern=r"^inferdrome-[a-f0-9]{20}$")
    endpoint: str = Field(min_length=1)
    model: str = Field(min_length=1)
    adapter: Literal["vllm_bench_serve"]
    adapter_version: Literal["1.0.0"]
    request_count: Literal[100]
    concurrency: int = Field(gt=0, le=1_000)
    warmup_count: int = Field(ge=0, le=1_000)


@dataclass(frozen=True, slots=True)
class PreparedManagedInferdromeBundle:
    """Approved v3 contract plus the selected pathless evidence identity."""

    poc_id: str
    bundle_fingerprint: str
    approved_contract: POCContract
    evidence: ManagedInferdromeEvidenceProjection
    workload: ManagedInferdromeWorkloadProjection
    definition_bindings: tuple[PerformanceDefinitionBinding, ...]
    planning_limitations: tuple[str, ...]

    def __post_init__(self) -> None:
        criteria = tuple(
            criterion
            for criterion in self.approved_contract.criteria
            if type(criterion) is InferencePerformanceCriterionV3
        )
        if len(criteria) != 1 or len(self.approved_contract.criteria) != 1:
            raise ValueError("Prepared managed agreement must contain one v3 rule.")
        criterion = criteria[0]
        identity = criterion.evidence_identity
        if (
            self.approved_contract.status is not ContractStatus.APPROVED
            or self.approved_contract.target_system.provider
            != MANAGED_TARGET_PROVIDER
            or self.approved_contract.target_system.endpoint_class
            != MANAGED_TARGET_ENDPOINT_CLASS
            or self.approved_contract.target_system.model
            != self.evidence.target_model
            or self.approved_contract.workload.sha256
            != self.evidence.workload_digest.removeprefix("sha256:")
            or self.approved_contract.workload.fixture_path
            != _managed_fixture_path(self.evidence)
            or identity.request_plan_digest != self.evidence.request_plan_digest
            or identity.workload_digest != self.evidence.workload_digest
            or identity.target_model != self.evidence.target_model
            or identity.target_model_revision
            != self.evidence.target_model_revision
            or identity.target_tokenizer_revision
            != self.evidence.target_tokenizer_revision
            or identity.target_endpoint != self.evidence.target_endpoint
            or identity.exact_measured_attempts
            != self.evidence.exact_measured_attempts
            or identity.warmup_requests != self.evidence.warmup_requests
            or self.workload.request_count
            != self.evidence.exact_measured_attempts
            or self.workload.endpoint != self.evidence.target_endpoint
            or self.workload.model != self.evidence.target_model
            or self.workload.adapter != self.evidence.adapter_id
            or self.workload.adapter_version != self.evidence.adapter_version
            or self.workload.warmup_count != self.evidence.warmup_requests
        ):
            raise ValueError("Prepared managed agreement binding is invalid.")
        expected = _managed_bundle_fingerprint(
            contract=self.approved_contract,
            evidence=self.evidence,
            definition_bindings=self.definition_bindings,
        )
        if expected != self.bundle_fingerprint:
            raise ValueError("Prepared managed agreement fingerprint is invalid.")


def project_managed_inferdrome_evidence(
    verified: VerifiedInferdromeBundle,
) -> ManagedInferdromeEvidenceProjection:
    """Create an exact pathless projection from the pinned verified handoff."""

    if type(verified) is not VerifiedInferdromeBundle:
        raise ManagedInferdromeContractAssemblyError(
            "Managed evidence must be independently verified first."
        )
    descriptor = _object(verified.descriptor, "bundle descriptor")
    resolved = _object(verified.resolved_spec, "resolved experiment")
    producer = _object(descriptor.get("producer"), "producer identity")
    digests = _object(descriptor.get("digests"), "bundle digests")
    sensitivity = _object(descriptor.get("sensitivity"), "bundle sensitivity")
    target = _object(resolved.get("target"), "target identity")
    execution = _object(resolved.get("execution"), "execution identity")
    measurement = _object(resolved.get("measurement"), "measurement identity")
    traffic = _object(resolved.get("traffic"), "traffic identity")
    workload = _object(resolved.get("workload"), "workload identity")
    facts = verified.managed_profile
    if (
        verified.bundle_digest != PINNED_BUNDLE_DIGEST
        or descriptor.get("run_id") != PINNED_RUN_ID
        or facts is None
        or facts.profile_id != MANAGED_PROFILE_ID
        or facts.profile_sha256 != MANAGED_PROFILE_SHA256
        or facts.local_gpu_proof_schema_id != LOCAL_GPU_PROOF_SCHEMA_ID
        or facts.local_gpu_proof_schema_sha256
        != LOCAL_GPU_PROOF_SCHEMA_SHA256
        or execution.get("adapter") != producer.get("adapter")
        or execution.get("adapter_version") != producer.get("adapter_version")
        or execution.get("producer_name") != producer.get("name")
        or execution.get("producer_version") != producer.get("version")
        or execution.get("max_measured_requests") != MANAGED_EXACT_ATTEMPTS
        or measurement.get("reducer_version") != "1.0.0"
    ):
        raise ManagedInferdromeContractAssemblyError(
            "Only the exact pinned managed Inferdrome handoff is supported."
        )
    try:
        return ManagedInferdromeEvidenceProjection.model_validate(
            {
                "run_id": descriptor["run_id"],
                "bundle_digest": verified.bundle_digest,
                "evidence_schema_version": descriptor["schema_version"],
                "producer_name": producer["name"],
                "producer_version": producer["version"],
                "adapter_id": producer["adapter"],
                "adapter_version": producer["adapter_version"],
                "native_schema_fingerprint": producer[
                    "native_schema_fingerprint"
                ],
                "managed_profile_id": facts.profile_id,
                "managed_profile_sha256": facts.profile_sha256,
                "local_gpu_proof_schema_id": facts.local_gpu_proof_schema_id,
                "local_gpu_proof_schema_sha256": (
                    facts.local_gpu_proof_schema_sha256
                ),
                "request_plan_digest": digests["request_plan_digest"],
                "workload_digest": workload["sha256"],
                "workload_path": workload["path"],
                "target_model": target["model"],
                "target_model_revision": target["model_revision"],
                "target_tokenizer_revision": target["tokenizer_revision"],
                "target_endpoint": target["endpoint"],
                "observed_configured_max_concurrency": traffic["concurrency"],
                "exact_measured_attempts": traffic["measured_requests"],
                "warmup_requests": traffic["warmup_requests"],
                "metric_definition_id": measurement["ttft_definition"],
                "gpu_models": facts.gpu_models,
                "claims_assurance": facts.claims_assurance,
                "native_response_content_present": sensitivity[
                    "native_response_content_present"
                ],
            },
            strict=True,
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ManagedInferdromeContractAssemblyError(
            "Verified managed evidence has an unsupported identity projection."
        ) from error


def prepare_managed_inferdrome_bundle(
    *,
    draft: DraftPOCSnapshot,
    proposals: Sequence[ProposalReviewItem],
    definitions: Sequence[ContractDefinitionReceipt],
    target: PerformanceTargetInput,
    evidence: ManagedInferdromeEvidenceProjection,
    prepared_at: datetime,
    contract_id: str | None = None,
    contract_version: str = "1",
    parent_version: str | None = None,
) -> PreparedManagedInferdromeBundle:
    """Bind one selected native-vLLM evidence identity into a v3 agreement."""

    if type(draft) is not DraftPOCSnapshot:
        raise TypeError("draft must be a DraftPOCSnapshot.")
    if draft.archive_state is not DraftPOCArchiveState.ACTIVE:
        raise ManagedInferdromeContractAssemblyError("The POC must be active.")
    if type(target) is not PerformanceTargetInput:
        raise TypeError("target must be a PerformanceTargetInput.")
    if type(evidence) is not ManagedInferdromeEvidenceProjection:
        raise TypeError("evidence must be a managed Inferdrome projection.")
    _require_contract_identity(contract_id, contract_version, parent_version)
    if (
        type(prepared_at) is not datetime
        or prepared_at.tzinfo is None
        or prepared_at.utcoffset() is None
    ):
        raise ManagedInferdromeContractAssemblyError(
            "prepared_at must be timezone-aware."
        )
    if (
        target.evidence_method
        is not PerformanceEvidenceMethod.INFERDROME_EXTERNAL_BUNDLE
        or target.inferdrome_run_id != evidence.run_id
        or target.inferdrome_bundle_digest != evidence.bundle_digest
        or target.provider != MANAGED_TARGET_PROVIDER
        or target.endpoint_class != MANAGED_TARGET_ENDPOINT_CLASS
        or target.endpoint != evidence.target_endpoint
        or target.model != evidence.target_model
    ):
        raise ManagedInferdromeContractAssemblyError(
            "The selected target does not match the verified managed evidence."
        )

    try:
        kept = _kept_proposals(draft.poc_id, proposals)
        ttft, error_rate, bindings = _definition_pair(
            draft.poc_id,
            kept,
            definitions,
        )
        _require_matching_workload(ttft, error_rate)
    except PerformanceContractAssemblyError as error:
        raise ManagedInferdromeContractAssemblyError(str(error)) from error
    if (
        ttft.operator is not ContractDefinitionOperator.LT
        or ttft.minimum_samples != MANAGED_EXACT_ATTEMPTS
        or error_rate.minimum_samples != MANAGED_EXACT_ATTEMPTS
    ):
        raise ManagedInferdromeContractAssemblyError(
            "Managed v1 requires strict-below rules and exactly 100 samples."
        )
    threshold_ns = _scaled_integer(
        ttft.threshold,
        Decimal(1_000_000),
        "TTFT threshold",
    )
    threshold_basis_points = _scaled_integer(
        error_rate.threshold,
        Decimal(100),
        "error-rate threshold",
    )
    if not 0 < threshold_ns <= 60_000_000_000:
        raise ManagedInferdromeContractAssemblyError(
            "TTFT threshold is outside managed bounds."
        )
    if not 0 < threshold_basis_points < 10_000:
        raise ManagedInferdromeContractAssemblyError(
            "Error-rate threshold is outside managed bounds."
        )

    identity_seed = canonical_json_bytes(
        {
            "poc_id": draft.poc_id,
            "target": target.model_dump(mode="json"),
            "definitions": [
                binding.model_dump(mode="json") for binding in bindings
            ],
            "evidence": evidence.model_dump(mode="json"),
            "contract_version": contract_version,
            "parent_version": parent_version,
        }
    )
    identity = hashlib.sha256(
        b"exitspec-managed-inferdrome-bundle-identity-v1\x00" + identity_seed
    ).hexdigest()
    resolved_contract_id = (
        "agreement-{0}".format(identity[:20])
        if contract_id is None
        else contract_id
    )
    workload_id = "inferdrome-{0}".format(
        evidence.workload_digest.removeprefix("sha256:")[:20]
    )
    workload = ManagedInferdromeWorkloadProjection(
        workload_id=workload_id,
        endpoint=evidence.target_endpoint,
        model=evidence.target_model,
        adapter=evidence.adapter_id,
        adapter_version=evidence.adapter_version,
        request_count=evidence.exact_measured_attempts,
        concurrency=evidence.observed_configured_max_concurrency,
        warmup_count=evidence.warmup_requests,
    )
    ttft_proposal = kept[ttft.proposal_id]
    error_proposal = kept[error_rate.proposal_id]
    source = SourceReference(
        speaker="customer_source",
        quote="{0} {1}".format(
            ttft_proposal.source_quote,
            error_proposal.source_quote,
        ),
        location="{0}/{1}; {2}/{3}".format(
            ttft.source_receipt_id,
            ttft.proposal_id,
            error_rate.source_receipt_id,
            error_rate.proposal_id,
        ),
    )
    normalized_claim = (
        "For the selected retained Inferdrome run, at required configured "
        "maximum concurrency {0}, independently recalculated native vLLM "
        "p95 TTFT must be strictly below {1:g} milliseconds across exactly "
        "100 successful measured requests, and failed-or-anomalous request "
        "rate must be strictly below {2:g}% across exactly 100 measured "
        "requests."
    ).format(
        ttft.concurrency,
        ttft.threshold,
        error_rate.threshold,
    )
    criterion = InferencePerformanceCriterionV3(
        criterion_type="inference_performance_v3",
        id="INFERENCE-PERF-EXT-01",
        title="Retained native vLLM latency and reliability",
        must_have=True,
        source=source,
        normalized_claim=normalized_claim,
        ttft_p95=ExternalTTFTP95RuleV1(
            metric="time_to_first_token",
            definition_id=MANAGED_NATIVE_TTFT_DEFINITION,
            aggregation="p95",
            unit="nanoseconds",
            operator="lt",
            threshold_ns=threshold_ns,
            reducer_id=MANAGED_NATIVE_REDUCER,
            population=MANAGED_NATIVE_LATENCY_POPULATION,
            minimum_successful_samples=MANAGED_EXACT_ATTEMPTS,
            must_pass=True,
        ),
        error_rate=ExternalErrorRateRuleV1(
            metric="error_rate",
            aggregation="rate",
            operator="lt",
            threshold_basis_points=threshold_basis_points,
            numerator="failed_or_anomalous_native_measured_requests",
            denominator="all_measured_requests",
            exact_attempts=MANAGED_EXACT_ATTEMPTS,
            must_pass=True,
        ),
        evidence_identity=InferdromeEvidenceIdentityV1(
            schema_version="exitspec.inferdrome-evidence-identity.v1",
            evidence_schema_version=evidence.evidence_schema_version,
            producer_name=evidence.producer_name,
            producer_version=evidence.producer_version,
            adapter_id=evidence.adapter_id,
            adapter_version=evidence.adapter_version,
            native_schema_fingerprint=evidence.native_schema_fingerprint,
            managed_profile_id=evidence.managed_profile_id,
            managed_profile_sha256=evidence.managed_profile_sha256,
            local_gpu_proof_schema_id=evidence.local_gpu_proof_schema_id,
            local_gpu_proof_schema_sha256=(
                evidence.local_gpu_proof_schema_sha256
            ),
            request_plan_digest=evidence.request_plan_digest,
            workload_digest=evidence.workload_digest,
            target_model=evidence.target_model,
            target_model_revision=evidence.target_model_revision,
            target_tokenizer_revision=evidence.target_tokenizer_revision,
            target_endpoint=evidence.target_endpoint,
            configured_max_concurrency=ttft.concurrency,
            exact_measured_attempts=MANAGED_EXACT_ATTEMPTS,
            warmup_requests=evidence.warmup_requests,
            binding_mode="EXTERNAL_RECEIPT_BINDING",
            chronology="RETROSPECTIVE",
            producer_contract_link="ABSENT",
        ),
        concurrency_semantics=(
            "configured_maximum_concurrency_not_observed_overlap"
        ),
        owner=draft.owner,
        evidence_policy=(
            "Retain producer run {0}, unchanged bundle {1}, independent "
            "recalculation, managed ExitSpec receipt, and immutable Evidence "
            "Pack."
        ).format(
            evidence.run_id,
            evidence.bundle_digest,
        ),
        approved=True,
    )
    limitations = (
        "NOT_PROVEN — This is retrospective conformance evidence; the retained "
        "measurement predates this customer contract.",
        "NOT_PROVEN — Configured maximum concurrency does not prove achieved "
        "request overlap or production capacity.",
        "NOT_PROVEN — Hashes prove internal consistency and mutation detection, "
        "not authorship, truthful execution, hardware identity, or production "
        "authorization.",
        "NOT_PROVEN — Prompt and output-token ranges from planning do not prove "
        "a broader distribution beyond the exact workload digest.",
        "PRIVACY — The retained native annex contains synthetic generated "
        "response content; ExitSpec does not copy response text into its receipt "
        "or Evidence Pack.",
        *_excluded_claim_limitations(proposals),
    )
    contract = POCContract(
        id=resolved_contract_id,
        version=contract_version,
        status=ContractStatus.APPROVED,
        created_at=prepared_at,
        approved_at=prepared_at,
        customer=draft.customer_label,
        use_case=draft.use_case,
        target_system=TargetSystem(
            provider=MANAGED_TARGET_PROVIDER,
            endpoint_class=MANAGED_TARGET_ENDPOINT_CLASS,
            model=evidence.target_model,
        ),
        workload=WorkloadReference(
            fixture_path=_managed_fixture_path(evidence),
            sha256=evidence.workload_digest.removeprefix("sha256:"),
        ),
        criteria=(criterion,),
        owners=(draft.owner,),
        non_goals=limitations,
        evidence_retention_policy=(
            "Retain the checksum-pinned external bundle separately and the "
            "immutable ExitSpec receipt/Evidence Pack; never publish raw "
            "native response content through this workflow."
        ),
        parent_version=parent_version,
    )
    return PreparedManagedInferdromeBundle(
        poc_id=draft.poc_id,
        bundle_fingerprint=_managed_bundle_fingerprint(
            contract=contract,
            evidence=evidence,
            definition_bindings=bindings,
        ),
        approved_contract=contract,
        evidence=evidence,
        workload=workload,
        definition_bindings=bindings,
        planning_limitations=limitations,
    )


def _scaled_integer(value: float, scale: Decimal, label: str) -> int:
    try:
        scaled = Decimal(str(value)) * scale
    except (InvalidOperation, ValueError) as error:
        raise ManagedInferdromeContractAssemblyError(
            "{0} cannot be represented exactly.".format(label)
        ) from error
    integral = scaled.to_integral_value()
    if scaled != integral:
        raise ManagedInferdromeContractAssemblyError(
            "{0} cannot be represented exactly.".format(label)
        )
    return int(integral)


def _managed_fixture_path(
    evidence: ManagedInferdromeEvidenceProjection,
) -> str:
    """Hash-bind the selected run and complete bundle without a local path."""

    return "external://inferdrome/{0}/bundles/{1}/workload".format(
        evidence.run_id,
        evidence.bundle_digest.removeprefix("sha256:"),
    )


def _require_contract_identity(
    contract_id: str | None,
    contract_version: str,
    parent_version: str | None,
) -> None:
    if (
        type(contract_version) is not str
        or not contract_version.isdigit()
        or int(contract_version) < 1
        or str(int(contract_version)) != contract_version
        or (
            contract_id is None
            and (contract_version != "1" or parent_version is not None)
        )
        or (
            contract_id is not None
            and (
                type(contract_id) is not str
                or not contract_id.startswith("agreement-")
                or not 3 <= len(contract_id) <= 64
            )
        )
        or (contract_version == "1" and parent_version is not None)
        or (
            contract_version != "1"
            and (
                contract_id is None
                or parent_version
                != "{0}@{1}".format(contract_id, int(contract_version) - 1)
            )
        )
    ):
        raise ManagedInferdromeContractAssemblyError(
            "Contract revision identity is outside supported bounds."
        )


def _managed_bundle_fingerprint(
    *,
    contract: POCContract,
    evidence: ManagedInferdromeEvidenceProjection,
    definition_bindings: tuple[PerformanceDefinitionBinding, ...],
) -> str:
    return hashlib.sha256(
        b"exitspec-prepared-managed-inferdrome-bundle-v1\x00"
        + canonical_json_bytes(
            {
                "contract": contract.model_dump(mode="json"),
                "evidence": evidence.model_dump(mode="json"),
                "definition_bindings": [
                    binding.model_dump(mode="json")
                    for binding in definition_bindings
                ],
            }
        )
    ).hexdigest()


def _object(value: object, label: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise ManagedInferdromeContractAssemblyError(
            "Verified {0} is unavailable.".format(label)
        )
    return value


__all__ = [
    "MANAGED_EXACT_ATTEMPTS",
    "MANAGED_NATIVE_LATENCY_POPULATION",
    "MANAGED_NATIVE_REDUCER",
    "MANAGED_NATIVE_TTFT_DEFINITION",
    "ManagedInferdromeContractAssemblyError",
    "ManagedInferdromeEvidenceProjection",
    "ManagedInferdromeWorkloadProjection",
    "PreparedManagedInferdromeBundle",
    "prepare_managed_inferdrome_bundle",
    "project_managed_inferdrome_evidence",
]
