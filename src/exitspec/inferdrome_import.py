"""ExitSpec-owned acceptance evaluation for verified Inferdrome evidence."""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from pathlib import Path
from typing import Final, Literal, cast

from pydantic import Field, field_validator, model_validator

from .canonical import canonical_json_bytes
from .confirmations import ContractConfirmation
from .inferdrome_bundle import (
    INFERDROME_VERIFIER_VERSION,
    InferdromeBundleLimits,
    RecalculatedInferdromeMeasurements,
    VerifiedInferdromeBundle,
    verify_inferdrome_bundle,
)
from .models import SHA256_PATTERN, FrozenExitSpecModel, VerdictStatus
from .performance_evidence import (
    PerformanceEvidenceError,
    ValidatedPerformanceContext,
    require_frozen_confirmed,
)
from .performance_verdicts import (
    ErrorRateRuleResult,
    PerformanceCriterionVerdict,
    TTFTP95RuleResult,
)

INFERDROME_RECEIPT_SCHEMA_VERSION: Final = "exitspec.inferdrome-receipt.v1"
INFERDROME_CALCULATION_VERSION: Final = "exitspec.inferdrome-importer.v1"
_RECEIPT_DOMAIN: Final = b"exitspec:inferdrome-ingestion-receipt-v1\x00"
_NANOSECONDS_PER_MILLISECOND: Final = Decimal(1_000_000)


class InferdromeImportErrorCode(str, Enum):
    """Stable failures before an external ingestion receipt can be issued."""

    CONTEXT_NOT_AUTHORIZED = "CONTEXT_NOT_AUTHORIZED"
    INVALID_RECEIPT_TIME = "INVALID_RECEIPT_TIME"


class InferdromeImportRejected(ValueError):
    """ExitSpec cannot authorize evaluation for the supplied context."""

    def __init__(self, code: InferdromeImportErrorCode, message: str) -> None:
        self.code = code
        super().__init__(message)


class InferdromeApplicabilityCode(str, Enum):
    """Valid evidence conditions that prevent or limit contract proof."""

    CONTRACT_LINK_MISMATCH = "CONTRACT_LINK_MISMATCH"
    TARGET_MODEL_MISMATCH = "TARGET_MODEL_MISMATCH"
    ENDPOINT_MISMATCH = "ENDPOINT_MISMATCH"
    ADAPTER_MISMATCH = "ADAPTER_MISMATCH"
    REQUEST_POPULATION_MISMATCH = "REQUEST_POPULATION_MISMATCH"
    TRAFFIC_MISMATCH = "TRAFFIC_MISMATCH"
    SAMPLING_MISMATCH = "SAMPLING_MISMATCH"
    WORKLOAD_PROMPT_MISMATCH = "WORKLOAD_PROMPT_MISMATCH"
    ENVIRONMENT_INCOMPLETE = "ENVIRONMENT_INCOMPLETE"
    ANOMALOUS_RECORD = "ANOMALOUS_RECORD"
    TTFT_DEFINITION_MISMATCH = "TTFT_DEFINITION_MISMATCH"


@dataclass(frozen=True, slots=True)
class InferdromeApplicability:
    """Machine-readable alignment between one bundle and one frozen context."""

    issues: tuple[InferdromeApplicabilityCode, ...]

    @property
    def fully_applicable(self) -> bool:
        return not self.issues


class InferdromeIngestionReceipt(FrozenExitSpecModel):
    """The first ExitSpec-controlled trust anchor for an accepted bundle digest."""

    schema_version: Literal["exitspec.inferdrome-receipt.v1"] = (
        INFERDROME_RECEIPT_SCHEMA_VERSION
    )
    receipt_id: str = Field(pattern=r"^irc_[a-f0-9]{64}$")
    bundle_digest: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    contract_hash: str = Field(pattern=SHA256_PATTERN)
    criterion_id: str = Field(pattern=r"^[A-Z][A-Z0-9-]{2,63}$")
    verifier_version: Literal["1.0.0"] = INFERDROME_VERIFIER_VERSION
    calculation_version: Literal["exitspec.inferdrome-importer.v1"] = (
        INFERDROME_CALCULATION_VERSION
    )
    received_at: datetime
    import_status: Literal["ACCEPTED"] = "ACCEPTED"
    acceptance_verdict: Literal["PASS", "FAIL", "NOT_PROVEN"]
    recalculation_sha256: str = Field(pattern=SHA256_PATTERN)
    applicability_codes: tuple[InferdromeApplicabilityCode, ...]

    @field_validator("received_at")
    @classmethod
    def require_utc_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("received_at must be timezone-aware.")
        return value.astimezone(UTC)

    @field_validator("applicability_codes")
    @classmethod
    def require_canonical_applicability_codes(
        cls,
        value: tuple[InferdromeApplicabilityCode, ...],
    ) -> tuple[InferdromeApplicabilityCode, ...]:
        expected = tuple(code for code in InferdromeApplicabilityCode if code in value)
        if value != expected:
            raise ValueError(
                "applicability_codes must be unique and canonically ordered."
            )
        return value

    @model_validator(mode="after")
    def require_bound_receipt_identity(self) -> InferdromeIngestionReceipt:
        expected = inferdrome_receipt_id(
            schema_version=self.schema_version,
            bundle_digest=self.bundle_digest,
            contract_hash=self.contract_hash,
            criterion_id=self.criterion_id,
            verifier_version=self.verifier_version,
            calculation_version=self.calculation_version,
            received_at=self.received_at,
            import_status=self.import_status,
            acceptance_verdict=self.acceptance_verdict,
            recalculation_sha256=self.recalculation_sha256,
            applicability_codes=self.applicability_codes,
        )
        if not hmac.compare_digest(self.receipt_id, expected):
            raise ValueError("receipt_id does not bind the ingestion receipt.")
        return self


@dataclass(frozen=True, slots=True)
class InferdromeImportResult:
    """Accepted bundle identity, independent calculation, and ExitSpec verdict."""

    run_id: str
    receipt: InferdromeIngestionReceipt
    applicability: InferdromeApplicability
    recalculated: RecalculatedInferdromeMeasurements
    performance_verdict: PerformanceCriterionVerdict


def import_inferdrome_bundle(
    bundle_path: str | Path,
    context: ValidatedPerformanceContext,
    confirmation: ContractConfirmation,
    *,
    expected_bundle_digest: str | None = None,
    received_at: datetime | None = None,
    limits: InferdromeBundleLimits | None = None,
) -> InferdromeImportResult:
    """Verify, align, recalculate, evaluate, and receipt one external bundle.

    Integrity or eligibility failures reject ingestion.  A valid bundle that
    does not match the frozen target, workload, population, or metric semantics
    is accepted as evidence but yields ``NOT_PROVEN`` instead of being coerced
    into an acceptance result.
    """

    _require_authorized_context(context, confirmation)
    timestamp = _receipt_timestamp(received_at)
    verified = verify_inferdrome_bundle(
        Path(bundle_path),
        expected_bundle_digest=expected_bundle_digest,
        limits=limits,
        require_customer_eligible=True,
    )
    applicability = _evaluate_applicability(verified, context)
    verdict = _evaluate_contract(verified, context, applicability)
    contract_hash = context.contract.canonical_hash
    if contract_hash is None:
        raise InferdromeImportRejected(
            InferdromeImportErrorCode.CONTEXT_NOT_AUTHORIZED,
            "Frozen contract digest is unavailable.",
        )
    receipt = _build_receipt(
        bundle=verified,
        contract_hash=contract_hash,
        criterion_id=context.criterion.id,
        verdict=verdict.verdict,
        applicability=applicability,
        received_at=timestamp,
    )
    return InferdromeImportResult(
        run_id=str(verified.descriptor["run_id"]),
        receipt=receipt,
        applicability=applicability,
        recalculated=verified.recalculated,
        performance_verdict=verdict,
    )


def inferdrome_receipt_id(
    *,
    schema_version: str,
    bundle_digest: str,
    contract_hash: str,
    criterion_id: str,
    verifier_version: str,
    calculation_version: str,
    received_at: datetime,
    import_status: str,
    acceptance_verdict: str,
    recalculation_sha256: str,
    applicability_codes: tuple[InferdromeApplicabilityCode, ...],
) -> str:
    """Derive one immutable receipt identity over every persisted field."""

    if schema_version != INFERDROME_RECEIPT_SCHEMA_VERSION:
        raise ValueError("Unsupported Inferdrome receipt schema version.")
    if verifier_version != INFERDROME_VERIFIER_VERSION:
        raise ValueError("Unsupported Inferdrome verifier version.")
    if calculation_version != INFERDROME_CALCULATION_VERSION:
        raise ValueError("Unsupported Inferdrome calculation version.")
    if import_status != "ACCEPTED" or acceptance_verdict not in {
        "PASS",
        "FAIL",
        "NOT_PROVEN",
    }:
        raise ValueError("Inferdrome receipt status or verdict is invalid.")
    canonical_codes = tuple(
        code for code in InferdromeApplicabilityCode if code in applicability_codes
    )
    if applicability_codes != canonical_codes:
        raise ValueError("Inferdrome applicability codes are not canonical.")
    timestamp = _receipt_timestamp(received_at)
    payload = {
        "acceptance_verdict": acceptance_verdict,
        "applicability_codes": [code.value for code in applicability_codes],
        "bundle_digest": bundle_digest,
        "contract_hash": contract_hash,
        "criterion_id": criterion_id,
        "calculation_version": calculation_version,
        "import_status": import_status,
        "received_at": _canonical_timestamp(timestamp),
        "recalculation_sha256": recalculation_sha256,
        "schema_version": schema_version,
        "verifier_version": verifier_version,
    }
    return f"irc_{hashlib.sha256(_RECEIPT_DOMAIN + canonical_json_bytes(payload)).hexdigest()}"


def validate_inferdrome_receipt(
    receipt: InferdromeIngestionReceipt,
) -> InferdromeIngestionReceipt:
    """Reparse a copied receipt and recheck its complete derived identity."""

    if type(receipt) is not InferdromeIngestionReceipt:
        raise TypeError("receipt must be an InferdromeIngestionReceipt.")
    return InferdromeIngestionReceipt.model_validate(receipt.model_dump(mode="python"))


def _require_authorized_context(
    context: ValidatedPerformanceContext,
    confirmation: ContractConfirmation,
) -> None:
    if type(context) is not ValidatedPerformanceContext:
        raise InferdromeImportRejected(
            InferdromeImportErrorCode.CONTEXT_NOT_AUTHORIZED,
            "A validated performance context is required.",
        )
    if type(confirmation) is not ContractConfirmation:
        raise InferdromeImportRejected(
            InferdromeImportErrorCode.CONTEXT_NOT_AUTHORIZED,
            "A matching customer confirmation is required.",
        )
    try:
        require_frozen_confirmed(context, confirmation)
    except (PerformanceEvidenceError, TypeError, ValueError) as error:
        raise InferdromeImportRejected(
            InferdromeImportErrorCode.CONTEXT_NOT_AUTHORIZED,
            "Inferdrome import requires a frozen, customer-confirmed context.",
        ) from error


def _evaluate_applicability(
    bundle: VerifiedInferdromeBundle,
    context: ValidatedPerformanceContext,
) -> InferdromeApplicability:
    issues: list[InferdromeApplicabilityCode] = []
    descriptor = bundle.descriptor
    resolved = bundle.resolved_spec
    plan = bundle.request_plan
    target = _object(resolved.get("target"))
    execution = _object(resolved.get("execution"))
    workload = _object(resolved.get("workload"))
    traffic = _object(resolved.get("traffic"))
    digests = _object(descriptor.get("digests"))
    contract_hash = context.contract.canonical_hash
    expected_contract_digest = (
        f"sha256:{contract_hash}" if contract_hash is not None else None
    )
    if digests.get("exitspec_contract_digest") != expected_contract_digest:
        issues.append(InferdromeApplicabilityCode.CONTRACT_LINK_MISMATCH)
    if target.get("model") != context.contract.target_system.model:
        issues.append(InferdromeApplicabilityCode.TARGET_MODEL_MISMATCH)
    expected_endpoint = (
        str(target.get("endpoint", "")).rstrip("/") + "/v1/chat/completions"
    )
    if expected_endpoint != context.workload.endpoint:
        issues.append(InferdromeApplicabilityCode.ENDPOINT_MISMATCH)
    if (
        execution.get("adapter") != context.criterion.adapter
        or execution.get("adapter_version") != context.criterion.adapter_version
    ):
        issues.append(InferdromeApplicabilityCode.ADAPTER_MISMATCH)
    if (
        traffic.get("measured_requests") != context.workload.request_count
        or bundle.recalculated.attempted_count != context.workload.request_count
    ):
        issues.append(InferdromeApplicabilityCode.REQUEST_POPULATION_MISMATCH)
    if (
        traffic.get("kind") != "concurrent"
        or traffic.get("concurrency") != context.workload.concurrency
        or traffic.get("warmup_requests") != context.workload.warmup_count
        or context.workload.retries != 0
    ):
        issues.append(InferdromeApplicabilityCode.TRAFFIC_MISMATCH)
    if (
        workload.get("requested_output_tokens") != context.workload.max_tokens
        or workload.get("temperature") != "0"
    ):
        issues.append(InferdromeApplicabilityCode.SAMPLING_MISMATCH)
    planned_requests = plan.get("requests")
    expected_prompt_digests = tuple(
        f"sha256:{context.prompts[index % len(context.prompts)].sha256}"
        for index in range(context.workload.request_count)
    )
    if (
        not isinstance(planned_requests, list)
        or tuple(_planned_prompt_digest(item) for item in planned_requests)
        != expected_prompt_digests
    ):
        issues.append(InferdromeApplicabilityCode.WORKLOAD_PROMPT_MISMATCH)
    if descriptor.get("environment_completeness") != "COMPLETE":
        issues.append(InferdromeApplicabilityCode.ENVIRONMENT_INCOMPLETE)
    if bundle.recalculated.anomalous_count:
        issues.append(InferdromeApplicabilityCode.ANOMALOUS_RECORD)
    if bundle.recalculated.ttft_definition != context.workload.first_token_definition:
        issues.append(InferdromeApplicabilityCode.TTFT_DEFINITION_MISMATCH)
    return InferdromeApplicability(issues=tuple(dict.fromkeys(issues)))


def _evaluate_contract(
    bundle: VerifiedInferdromeBundle,
    context: ValidatedPerformanceContext,
    applicability: InferdromeApplicability,
) -> PerformanceCriterionVerdict:
    criterion = context.criterion
    measurement = bundle.recalculated
    general_issues = tuple(
        issue
        for issue in applicability.issues
        if issue
        not in {
            InferdromeApplicabilityCode.ANOMALOUS_RECORD,
            InferdromeApplicabilityCode.TTFT_DEFINITION_MISMATCH,
        }
    )
    threshold_ns = _ttft_threshold_ns(criterion.ttft_p95.threshold)
    ttft_issue = _issue_reason(applicability.issues)
    if (
        general_issues
        or (
            InferdromeApplicabilityCode.TTFT_DEFINITION_MISMATCH in applicability.issues
        )
        or threshold_ns is None
    ):
        ttft_result = TTFTP95RuleResult(
            verdict=VerdictStatus.NOT_PROVEN,
            observed_ns=measurement.p95_ttft_ns,
            threshold_ns=threshold_ns,
            operator=criterion.ttft_p95.operator,
            successful_samples=measurement.successful_count,
            minimum_successful_samples=(criterion.ttft_p95.minimum_successful_samples),
            reason=ttft_issue
            or "The TTFT threshold is not exactly representable in nanoseconds.",
        )
    elif measurement.successful_count < criterion.ttft_p95.minimum_successful_samples:
        ttft_result = TTFTP95RuleResult(
            verdict=VerdictStatus.NOT_PROVEN,
            observed_ns=measurement.p95_ttft_ns,
            threshold_ns=threshold_ns,
            operator=criterion.ttft_p95.operator,
            successful_samples=measurement.successful_count,
            minimum_successful_samples=(criterion.ttft_p95.minimum_successful_samples),
            reason="The successful Inferdrome TTFT population is insufficient.",
        )
    else:
        observed = measurement.p95_ttft_ns
        if observed is None:
            ttft_status = VerdictStatus.NOT_PROVEN
        else:
            passed = (
                observed < threshold_ns
                if criterion.ttft_p95.operator == "lt"
                else observed <= threshold_ns
            )
            ttft_status = VerdictStatus.PASS if passed else VerdictStatus.FAIL
        ttft_result = TTFTP95RuleResult(
            verdict=ttft_status,
            observed_ns=observed,
            threshold_ns=threshold_ns,
            operator=criterion.ttft_p95.operator,
            successful_samples=measurement.successful_count,
            minimum_successful_samples=(criterion.ttft_p95.minimum_successful_samples),
            reason=(
                "ExitSpec independently recalculated the nearest-rank p95 from "
                "applicable Inferdrome request records."
            ),
        )

    error_threshold = Decimal(str(criterion.error_rate.threshold))
    if general_issues:
        error_result = ErrorRateRuleResult(
            verdict=VerdictStatus.NOT_PROVEN,
            error_count=measurement.failed_count,
            attempted_count=measurement.attempted_count,
            observed_rate=measurement.error_rate,
            threshold=error_threshold,
            operator=criterion.error_rate.operator,
            minimum_attempts=criterion.error_rate.minimum_attempts,
            reason=_issue_reason(general_issues),
        )
    elif measurement.attempted_count != criterion.error_rate.minimum_attempts:
        error_result = ErrorRateRuleResult(
            verdict=VerdictStatus.NOT_PROVEN,
            error_count=measurement.failed_count,
            attempted_count=measurement.attempted_count,
            observed_rate=measurement.error_rate,
            threshold=error_threshold,
            operator=criterion.error_rate.operator,
            minimum_attempts=criterion.error_rate.minimum_attempts,
            reason="The measured Inferdrome attempt population is not exact.",
        )
    else:
        passed = Decimal(measurement.failed_count) < (
            error_threshold * Decimal(measurement.attempted_count)
        )
        error_result = ErrorRateRuleResult(
            verdict=VerdictStatus.PASS if passed else VerdictStatus.FAIL,
            error_count=measurement.failed_count,
            attempted_count=measurement.attempted_count,
            observed_rate=measurement.error_rate,
            threshold=error_threshold,
            operator=criterion.error_rate.operator,
            minimum_attempts=criterion.error_rate.minimum_attempts,
            reason=(
                "ExitSpec independently counted non-success terminal records "
                "over all measured Inferdrome attempts."
            ),
        )

    statuses = (ttft_result.verdict, error_result.verdict)
    if VerdictStatus.FAIL in statuses:
        verdict = VerdictStatus.FAIL
        reason = "At least one mandatory imported-evidence requirement failed."
    elif VerdictStatus.NOT_PROVEN in statuses:
        verdict = VerdictStatus.NOT_PROVEN
        reason = (
            "No mandatory requirement was contradicted, but the imported "
            "evidence cannot prove every requirement."
        )
    else:
        verdict = VerdictStatus.PASS
        reason = "Both mandatory requirements passed independent recalculation."
    return PerformanceCriterionVerdict(
        criterion_id=criterion.id,
        verdict=verdict,
        attempted_count=measurement.attempted_count,
        successful_count=measurement.successful_count,
        error_count=measurement.failed_count,
        ttft_p95=ttft_result,
        error_rate=error_result,
        calculation_version=INFERDROME_CALCULATION_VERSION,
        reason=reason,
        limitations=(
            "Inferdrome vLLM 0.26.0 does not expose first-nonempty-content TTFT.",
            "HTTP status and achieved request overlap are unavailable in v1 records.",
            "Integrity verification is not producer-authorship proof.",
        ),
        outcome_counts=None,
    )


def _build_receipt(
    *,
    bundle: VerifiedInferdromeBundle,
    contract_hash: str,
    criterion_id: str,
    verdict: VerdictStatus,
    applicability: InferdromeApplicability,
    received_at: datetime,
) -> InferdromeIngestionReceipt:
    if verdict not in {
        VerdictStatus.PASS,
        VerdictStatus.FAIL,
        VerdictStatus.NOT_PROVEN,
    }:
        raise ValueError("Inferdrome import cannot issue this verdict.")
    verdict_value = cast(
        Literal["PASS", "FAIL", "NOT_PROVEN"],
        verdict.value,
    )
    receipt_id = inferdrome_receipt_id(
        schema_version=INFERDROME_RECEIPT_SCHEMA_VERSION,
        bundle_digest=bundle.bundle_digest,
        contract_hash=contract_hash,
        criterion_id=criterion_id,
        verifier_version=INFERDROME_VERIFIER_VERSION,
        calculation_version=INFERDROME_CALCULATION_VERSION,
        received_at=received_at,
        import_status="ACCEPTED",
        acceptance_verdict=verdict_value,
        recalculation_sha256=bundle.recalculated.recalculation_sha256,
        applicability_codes=applicability.issues,
    )
    return InferdromeIngestionReceipt(
        receipt_id=receipt_id,
        bundle_digest=bundle.bundle_digest,
        contract_hash=contract_hash,
        criterion_id=criterion_id,
        received_at=received_at,
        acceptance_verdict=verdict_value,
        recalculation_sha256=bundle.recalculated.recalculation_sha256,
        applicability_codes=applicability.issues,
    )


def _receipt_timestamp(value: datetime | None) -> datetime:
    resolved = value if value is not None else datetime.now(UTC)
    if not isinstance(resolved, datetime):
        raise InferdromeImportRejected(
            InferdromeImportErrorCode.INVALID_RECEIPT_TIME,
            "Inferdrome receipt time must be a datetime.",
        )
    if resolved.tzinfo is None or resolved.utcoffset() is None:
        raise InferdromeImportRejected(
            InferdromeImportErrorCode.INVALID_RECEIPT_TIME,
            "Inferdrome receipt time must be timezone-aware.",
        )
    return resolved.astimezone(UTC)


def _canonical_timestamp(value: datetime) -> str:
    return (
        value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
    )


def _ttft_threshold_ns(threshold_ms: float) -> int | None:
    try:
        threshold = Decimal(str(threshold_ms)) * _NANOSECONDS_PER_MILLISECOND
        integral = threshold.to_integral_exact()
    except (InvalidOperation, ValueError):
        return None
    if threshold != integral or integral <= 0:
        return None
    return int(integral)


def _issue_reason(
    issues: tuple[InferdromeApplicabilityCode, ...],
) -> str:
    if not issues:
        return ""
    labels = ", ".join(issue.value for issue in issues)
    return f"Inferdrome evidence is not fully applicable: {labels}."


def _object(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _planned_prompt_digest(value: object) -> object:
    prompt = _object(_object(value).get("prompt"))
    return prompt.get("sha256")


__all__ = [
    "INFERDROME_CALCULATION_VERSION",
    "INFERDROME_RECEIPT_SCHEMA_VERSION",
    "InferdromeApplicability",
    "InferdromeApplicabilityCode",
    "InferdromeImportErrorCode",
    "InferdromeImportRejected",
    "InferdromeImportResult",
    "InferdromeIngestionReceipt",
    "import_inferdrome_bundle",
    "inferdrome_receipt_id",
    "validate_inferdrome_receipt",
]
