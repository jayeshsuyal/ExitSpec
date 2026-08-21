"""ExitSpec-owned acceptance for the exact managed Inferdrome A10 handoff."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Final, NoReturn

from .confirmations import ContractConfirmation
from .inferdrome_bundle import (
    InferdromeBundleLimits,
    RecalculatedInferdromeMeasurements,
    VerifiedInferdromeBundle,
    verify_inferdrome_bundle,
)
from .inferdrome_external_contract import (
    InferdromeManagedContextError,
    ValidatedManagedContractContext,
    validate_managed_contract_context,
)
from .inferdrome_profile import (
    LOCAL_GPU_PROOF_SCHEMA_SHA256,
    MANAGED_PROFILE_ID,
    MANAGED_PROFILE_SHA256,
    PINNED_BUNDLE_DIGEST,
    PINNED_RUN_ID,
)
from .inferdrome_reporting_v2 import (
    INFERDROME_MANAGED_CALCULATION_VERSION,
    INFERDROME_MANAGED_RECEIPT_SCHEMA_VERSION,
    INFERDROME_MANAGED_VERIFIER_VERSION,
    InferdromeManagedReceiptV2,
    MANAGED_TTFT_ONLY_APPLICABILITY_CODES,
    ManagedApplicabilityCode,
    ManagedEvidenceAssuranceV1,
    ManagedMetricReceiptV1,
    ManagedPopulationReceiptV1,
    ManagedTargetReceiptV1,
    managed_receipt_id,
)
from .models import (
    InferencePerformanceCriterionV3,
    POCContract,
    VerdictStatus,
)


_OBSERVED_TTFT_DEFINITION: Final = "vllm_first_choices_event_v0_26"
_OBSERVED_REDUCER: Final = "nearest_rank_v1"
_OBSERVED_LATENCY_POPULATION: Final = "successful_measured_requests_with_observed_ttft"


class InferdromeManagedImportErrorCode(str, Enum):
    """Failures before a managed v2 receipt can be released."""

    CONTEXT_NOT_AUTHORIZED = "CONTEXT_NOT_AUTHORIZED"
    UNSUPPORTED_BINDING = "UNSUPPORTED_BINDING"
    INVALID_RECEIPT_TIME = "INVALID_RECEIPT_TIME"


class InferdromeManagedImportRejected(ValueError):
    """The managed bundle cannot enter this retrospective acceptance path."""

    def __init__(
        self,
        code: InferdromeManagedImportErrorCode,
        message: str,
    ) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class ManagedApplicability:
    """Supported evidence conditions that prevent one criterion from being proven."""

    issues: tuple[ManagedApplicabilityCode, ...]

    @property
    def fully_applicable(self) -> bool:
        return not self.issues


@dataclass(frozen=True, slots=True)
class InferdromeManagedImportResult:
    """Accepted evidence, ExitSpec verdict, and immutable external receipt."""

    run_id: str
    verdict: VerdictStatus
    applicability: ManagedApplicability
    recalculated: RecalculatedInferdromeMeasurements
    receipt: InferdromeManagedReceiptV2


def import_managed_inferdrome_bundle(
    bundle_path: str | Path,
    contract: POCContract,
    confirmation: ContractConfirmation,
    *,
    expected_bundle_digest: str = PINNED_BUNDLE_DIGEST,
    received_at: datetime | None = None,
    limits: InferdromeBundleLimits | None = None,
) -> InferdromeManagedImportResult:
    """Verify, evaluate, and receipt the exact retrospective managed handoff."""

    context = _authorized_context(contract, confirmation)
    if expected_bundle_digest != PINNED_BUNDLE_DIGEST:
        _reject(
            InferdromeManagedImportErrorCode.UNSUPPORTED_BINDING,
            "Managed retrospective import requires the exact retained bundle digest.",
        )
    timestamp = _receipt_timestamp(received_at)
    verified = verify_inferdrome_bundle(
        Path(bundle_path),
        expected_bundle_digest=expected_bundle_digest,
        limits=limits,
        require_customer_eligible=True,
    )
    _require_exact_retrospective_binding(verified, context)
    applicability = _evaluate_applicability(verified, context.criterion)
    verdict = _evaluate_managed_verdict(
        verified.recalculated,
        context.criterion,
        applicability,
    )
    receipt = _build_receipt(
        verified,
        context,
        applicability,
        verdict,
        timestamp,
    )
    return InferdromeManagedImportResult(
        run_id=str(verified.descriptor["run_id"]),
        verdict=verdict,
        applicability=applicability,
        recalculated=verified.recalculated,
        receipt=receipt,
    )


def _authorized_context(
    contract: POCContract,
    confirmation: ContractConfirmation,
) -> ValidatedManagedContractContext:
    try:
        return validate_managed_contract_context(contract, confirmation)
    except (InferdromeManagedContextError, TypeError, ValueError) as error:
        raise InferdromeManagedImportRejected(
            InferdromeManagedImportErrorCode.CONTEXT_NOT_AUTHORIZED,
            "Managed import requires one frozen, customer-confirmed v3 contract.",
        ) from error


def _require_exact_retrospective_binding(
    bundle: VerifiedInferdromeBundle,
    context: ValidatedManagedContractContext,
) -> None:
    facts = bundle.managed_profile
    descriptor = bundle.descriptor
    resolved = bundle.resolved_spec
    digests = _object(descriptor.get("digests"))
    links = _object(resolved.get("links"))
    criterion = context.criterion
    identity = criterion.evidence_identity
    if (
        bundle.bundle_digest != PINNED_BUNDLE_DIGEST
        or descriptor.get("run_id") != PINNED_RUN_ID
        or facts is None
        or facts.profile_id != MANAGED_PROFILE_ID
        or facts.profile_sha256 != MANAGED_PROFILE_SHA256
        or facts.local_gpu_proof_schema_sha256 != LOCAL_GPU_PROOF_SCHEMA_SHA256
        or identity.binding_mode != "EXTERNAL_RECEIPT_BINDING"
        or identity.chronology != "RETROSPECTIVE"
        or identity.producer_contract_link != "ABSENT"
        or digests.get("exitspec_contract_digest") is not None
        or links.get("exitspec_contract_digest") is not None
    ):
        _reject(
            InferdromeManagedImportErrorCode.UNSUPPORTED_BINDING,
            "Bundle cannot use the exact retrospective external-receipt exception.",
        )


def _evaluate_applicability(
    bundle: VerifiedInferdromeBundle,
    criterion: InferencePerformanceCriterionV3,
) -> ManagedApplicability:
    identity = criterion.evidence_identity
    facts = bundle.managed_profile
    descriptor = bundle.descriptor
    resolved = bundle.resolved_spec
    target = _object(resolved.get("target"))
    execution = _object(resolved.get("execution"))
    workload = _object(resolved.get("workload"))
    traffic = _object(resolved.get("traffic"))
    digests = _object(descriptor.get("digests"))
    issues: set[ManagedApplicabilityCode] = set()

    producer = _object(descriptor.get("producer"))
    if (
        descriptor.get("schema_version") != identity.evidence_schema_version
        or producer.get("name") != identity.producer_name
        or producer.get("version") != identity.producer_version
        or producer.get("adapter") != identity.adapter_id
        or producer.get("adapter_version") != identity.adapter_version
        or producer.get("native_schema_fingerprint")
        != identity.native_schema_fingerprint
        or facts is None
        or facts.profile_id != identity.managed_profile_id
        or facts.profile_sha256 != identity.managed_profile_sha256
        or facts.local_gpu_proof_schema_id != identity.local_gpu_proof_schema_id
        or facts.local_gpu_proof_schema_sha256 != identity.local_gpu_proof_schema_sha256
    ):
        issues.add(ManagedApplicabilityCode.EVIDENCE_PROFILE_MISMATCH)
    if target.get("model") != identity.target_model:
        issues.add(ManagedApplicabilityCode.TARGET_MODEL_MISMATCH)
    if target.get("model_revision") != identity.target_model_revision:
        issues.add(ManagedApplicabilityCode.MODEL_REVISION_MISMATCH)
    if target.get("tokenizer_revision") != identity.target_tokenizer_revision:
        issues.add(ManagedApplicabilityCode.TOKENIZER_REVISION_MISMATCH)
    if target.get("endpoint") != identity.target_endpoint:
        issues.add(ManagedApplicabilityCode.ENDPOINT_MISMATCH)
    if digests.get("request_plan_digest") != identity.request_plan_digest:
        issues.add(ManagedApplicabilityCode.REQUEST_PLAN_MISMATCH)
    if workload.get("sha256") != identity.workload_digest:
        issues.add(ManagedApplicabilityCode.WORKLOAD_MISMATCH)
    if (
        traffic.get("kind") != "concurrent"
        or traffic.get("concurrency") != identity.configured_max_concurrency
    ):
        issues.add(ManagedApplicabilityCode.CONFIGURED_CONCURRENCY_MISMATCH)
    if (
        traffic.get("measured_requests") != identity.exact_measured_attempts
        or bundle.recalculated.attempted_count != identity.exact_measured_attempts
    ):
        issues.add(ManagedApplicabilityCode.REQUEST_POPULATION_MISMATCH)
    if traffic.get("warmup_requests") != identity.warmup_requests:
        issues.add(ManagedApplicabilityCode.WARMUP_POPULATION_MISMATCH)
    if bundle.recalculated.ttft_definition != criterion.ttft_p95.definition_id:
        issues.add(ManagedApplicabilityCode.METRIC_DEFINITION_MISMATCH)
    if criterion.ttft_p95.reducer_id != _OBSERVED_REDUCER:
        issues.add(ManagedApplicabilityCode.REDUCER_MISMATCH)
    if criterion.ttft_p95.population != _OBSERVED_LATENCY_POPULATION:
        issues.add(ManagedApplicabilityCode.LATENCY_POPULATION_MISMATCH)
    if (
        bundle.recalculated.successful_count
        < criterion.ttft_p95.minimum_successful_samples
    ):
        issues.add(ManagedApplicabilityCode.SUCCESSFUL_SAMPLE_SHORTFALL)
    if (
        execution.get("adapter") != identity.adapter_id
        or execution.get("adapter_version") != identity.adapter_version
    ):
        issues.add(ManagedApplicabilityCode.EVIDENCE_PROFILE_MISMATCH)
    canonical = tuple(code for code in ManagedApplicabilityCode if code in issues)
    return ManagedApplicability(issues=canonical)


def _evaluate_managed_verdict(
    measurement: RecalculatedInferdromeMeasurements,
    criterion: InferencePerformanceCriterionV3,
    applicability: ManagedApplicability,
) -> VerdictStatus:
    expected_error_rate = (
        Decimal(measurement.failed_count) / Decimal(measurement.attempted_count)
        if measurement.attempted_count > 0
        else None
    )
    reliability_population_is_valid = (
        measurement.attempted_count
        == criterion.error_rate.exact_attempts
        == criterion.evidence_identity.exact_measured_attempts
        and measurement.successful_count + measurement.failed_count
        == measurement.attempted_count
        and 0 <= measurement.anomalous_count <= measurement.failed_count
        and expected_error_rate is not None
        and measurement.error_rate == expected_error_rate
    )
    global_issues = set(applicability.issues) - set(
        MANAGED_TTFT_ONLY_APPLICABILITY_CODES
    )
    if global_issues or not reliability_population_is_valid:
        return VerdictStatus.NOT_PROVEN

    reliability_passed = measurement.failed_count * 10_000 < (
        criterion.error_rate.threshold_basis_points * measurement.attempted_count
    )
    if not reliability_passed:
        return VerdictStatus.FAIL

    latency_is_valid = (
        not applicability.issues
        and measurement.successful_count
        >= criterion.ttft_p95.minimum_successful_samples
        and measurement.p95_ttft_ns is not None
        and measurement.p95_ttft_ns >= 0
        and measurement.ttft_definition == criterion.ttft_p95.definition_id
    )
    if not latency_is_valid:
        return VerdictStatus.NOT_PROVEN
    assert measurement.p95_ttft_ns is not None
    latency_passed = measurement.p95_ttft_ns < criterion.ttft_p95.threshold_ns
    return (
        VerdictStatus.PASS
        if latency_passed and reliability_passed
        else VerdictStatus.FAIL
    )


def _build_receipt(
    bundle: VerifiedInferdromeBundle,
    context: ValidatedManagedContractContext,
    applicability: ManagedApplicability,
    verdict: VerdictStatus,
    received_at: datetime,
) -> InferdromeManagedReceiptV2:
    facts = bundle.managed_profile
    contract_hash = context.contract.canonical_hash
    if facts is None or contract_hash is None:
        _reject(
            InferdromeManagedImportErrorCode.UNSUPPORTED_BINDING,
            "Verified managed profile or frozen contract digest is unavailable.",
        )
    criterion = context.criterion
    identity = criterion.evidence_identity
    measurement = bundle.recalculated
    descriptor = bundle.descriptor
    resolved = bundle.resolved_spec
    producer = _object(descriptor.get("producer"))
    digests = _object(descriptor.get("digests"))
    observed_target = _object(resolved.get("target"))
    observed_workload = _object(resolved.get("workload"))
    observed_traffic = _object(resolved.get("traffic"))
    error_rate = Decimal(measurement.failed_count) / Decimal(
        measurement.attempted_count
    )
    target = ManagedTargetReceiptV1(
        requested_model=identity.target_model,
        observed_model=str(observed_target.get("model")),
        requested_model_revision=identity.target_model_revision,
        observed_model_revision=str(observed_target.get("model_revision")),
        requested_tokenizer_revision=identity.target_tokenizer_revision,
        observed_tokenizer_revision=str(observed_target.get("tokenizer_revision")),
        requested_endpoint=identity.target_endpoint,
        observed_endpoint=str(observed_target.get("endpoint")),
    )
    metric = ManagedMetricReceiptV1(
        metric="time_to_first_token",
        aggregation="p95",
        unit="nanoseconds",
        operator=criterion.ttft_p95.operator,
        requested_definition_id=criterion.ttft_p95.definition_id,
        observed_definition_id=_OBSERVED_TTFT_DEFINITION,
        requested_reducer_id=criterion.ttft_p95.reducer_id,
        observed_reducer_id=_OBSERVED_REDUCER,
        requested_population=criterion.ttft_p95.population,
        observed_population=_OBSERVED_LATENCY_POPULATION,
        threshold_ns=criterion.ttft_p95.threshold_ns,
        recalculated_value_ns=measurement.p95_ttft_ns,
    )
    population = ManagedPopulationReceiptV1(
        attempted_count=measurement.attempted_count,
        successful_count=measurement.successful_count,
        failed_count=measurement.failed_count,
        anomalous_count=measurement.anomalous_count,
        required_attempts=criterion.error_rate.exact_attempts,
        required_successful_samples=(criterion.ttft_p95.minimum_successful_samples),
        required_configured_max_concurrency=(identity.configured_max_concurrency),
        observed_configured_max_concurrency=int(observed_traffic["concurrency"]),
        required_warmup_requests=identity.warmup_requests,
        observed_warmup_requests=int(observed_traffic["warmup_requests"]),
        error_numerator=criterion.error_rate.numerator,
        error_denominator=criterion.error_rate.denominator,
        error_threshold_basis_points=(criterion.error_rate.threshold_basis_points),
        observed_error_rate=format(error_rate, "f"),
    )
    assurance = ManagedEvidenceAssuranceV1(
        producer_evidence_consistency="VERIFIED",
        hardware_attestation="NOT_AVAILABLE",
        execution_attestation="NOT_AVAILABLE",
        exact_achieved_concurrency="NOT_AVAILABLE",
        transport_retry_behavior="NOT_AVAILABLE",
        temporal_assurance="RETROSPECTIVE",
        contract_preceded_measurement=False,
        production_authorization=False,
    )
    payload = {
        "schema_version": INFERDROME_MANAGED_RECEIPT_SCHEMA_VERSION,
        "bundle_digest": bundle.bundle_digest,
        "contract_hash": contract_hash,
        "criterion_id": criterion.id,
        "run_id": str(bundle.descriptor["run_id"]),
        "verifier_version": INFERDROME_MANAGED_VERIFIER_VERSION,
        "calculation_version": INFERDROME_MANAGED_CALCULATION_VERSION,
        "received_at": received_at,
        "ingestion_status": "ACCEPTED",
        "acceptance_verdict": verdict.value,
        "applicability_codes": applicability.issues,
        "evidence_schema_version": str(descriptor["schema_version"]),
        "producer_name": str(producer["name"]),
        "producer_version": str(producer["version"]),
        "adapter_id": str(producer["adapter"]),
        "adapter_version": str(producer["adapter_version"]),
        "native_schema_fingerprint": str(producer["native_schema_fingerprint"]),
        "managed_profile_id": facts.profile_id,
        "managed_profile_sha256": facts.profile_sha256,
        "local_gpu_proof_schema_id": facts.local_gpu_proof_schema_id,
        "local_gpu_proof_schema_sha256": facts.local_gpu_proof_schema_sha256,
        "profile_validator_version": facts.validator_version,
        "requested_request_plan_digest": identity.request_plan_digest,
        "observed_request_plan_digest": str(digests["request_plan_digest"]),
        "requested_workload_digest": identity.workload_digest,
        "observed_workload_digest": str(observed_workload["sha256"]),
        "recalculation_sha256": measurement.recalculation_sha256,
        "binding_mode": "EXTERNAL_RECEIPT_BINDING",
        "producer_contract_link": "ABSENT",
        "purpose": "CONFORMANCE_DEMONSTRATION",
        "target": target,
        "metric": metric,
        "population": population,
        "assurance": assurance,
    }
    identity_payload = {
        key: value.model_dump(mode="json")
        if isinstance(
            value,
            (
                ManagedTargetReceiptV1,
                ManagedMetricReceiptV1,
                ManagedPopulationReceiptV1,
                ManagedEvidenceAssuranceV1,
            ),
        )
        else [item.value for item in value]
        if key == "applicability_codes"
        else value
        for key, value in payload.items()
    }
    receipt_id = managed_receipt_id(identity_payload)
    return InferdromeManagedReceiptV2(
        receipt_id=receipt_id,
        **payload,
    )


def _receipt_timestamp(value: datetime | None) -> datetime:
    timestamp = value or datetime.now(UTC)
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        _reject(
            InferdromeManagedImportErrorCode.INVALID_RECEIPT_TIME,
            "Managed receipt time must be timezone-aware.",
        )
    return timestamp.astimezone(UTC)


def _object(value: object) -> dict[str, object]:
    return value if type(value) is dict else {}


def _reject(
    code: InferdromeManagedImportErrorCode,
    message: str,
) -> NoReturn:
    raise InferdromeManagedImportRejected(code, message)


__all__ = [
    "InferdromeManagedImportErrorCode",
    "InferdromeManagedImportRejected",
    "InferdromeManagedImportResult",
    "ManagedApplicability",
    "import_managed_inferdrome_bundle",
]
