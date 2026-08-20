"""Immutable v2 receipts for managed retrospective Inferdrome evidence."""

from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Final, Literal, Mapping

from pydantic import Field, field_validator, model_validator

from .canonical import canonical_json_bytes
from .models import SHA256_PATTERN, FrozenExitSpecModel


INFERDROME_MANAGED_RECEIPT_SCHEMA_VERSION: Final = (
    "exitspec.inferdrome-managed-receipt.v2"
)
INFERDROME_MANAGED_VERIFIER_VERSION: Final = "2.0.0"
INFERDROME_MANAGED_CALCULATION_VERSION: Final = (
    "exitspec.inferdrome-managed-importer.v1"
)
_RECEIPT_DOMAIN: Final = b"exitspec:inferdrome-managed-receipt-v2\x00"


class ManagedApplicabilityCode(str, Enum):
    """Canonical reasons accepted managed evidence cannot prove a v3 rule."""

    EVIDENCE_PROFILE_MISMATCH = "EVIDENCE_PROFILE_MISMATCH"
    TARGET_MODEL_MISMATCH = "TARGET_MODEL_MISMATCH"
    MODEL_REVISION_MISMATCH = "MODEL_REVISION_MISMATCH"
    TOKENIZER_REVISION_MISMATCH = "TOKENIZER_REVISION_MISMATCH"
    ENDPOINT_MISMATCH = "ENDPOINT_MISMATCH"
    REQUEST_PLAN_MISMATCH = "REQUEST_PLAN_MISMATCH"
    WORKLOAD_MISMATCH = "WORKLOAD_MISMATCH"
    CONFIGURED_CONCURRENCY_MISMATCH = "CONFIGURED_CONCURRENCY_MISMATCH"
    REQUEST_POPULATION_MISMATCH = "REQUEST_POPULATION_MISMATCH"
    WARMUP_POPULATION_MISMATCH = "WARMUP_POPULATION_MISMATCH"
    METRIC_DEFINITION_MISMATCH = "METRIC_DEFINITION_MISMATCH"
    REDUCER_MISMATCH = "REDUCER_MISMATCH"
    LATENCY_POPULATION_MISMATCH = "LATENCY_POPULATION_MISMATCH"
    SUCCESSFUL_SAMPLE_SHORTFALL = "SUCCESSFUL_SAMPLE_SHORTFALL"


MANAGED_TTFT_ONLY_APPLICABILITY_CODES: Final = frozenset(
    {
        ManagedApplicabilityCode.METRIC_DEFINITION_MISMATCH,
        ManagedApplicabilityCode.REDUCER_MISMATCH,
        ManagedApplicabilityCode.LATENCY_POPULATION_MISMATCH,
        ManagedApplicabilityCode.SUCCESSFUL_SAMPLE_SHORTFALL,
    }
)


class ManagedMetricReceiptV1(FrozenExitSpecModel):
    """Requested and observed metric identities without semantic substitution."""

    metric: Literal["time_to_first_token"]
    aggregation: Literal["p95"]
    unit: Literal["nanoseconds"]
    operator: Literal["lt"]
    requested_definition_id: Literal[
        "vllm_first_choices_event_v0_26",
        "first_nonempty_choices_delta_content_v1",
    ]
    observed_definition_id: Literal["vllm_first_choices_event_v0_26"]
    requested_reducer_id: Literal["nearest_rank_v1"]
    observed_reducer_id: Literal["nearest_rank_v1"]
    requested_population: Literal["successful_measured_requests_with_observed_ttft"]
    observed_population: Literal["successful_measured_requests_with_observed_ttft"]
    threshold_ns: int = Field(gt=0, le=60_000_000_000)
    recalculated_value_ns: int | None = Field(default=None, ge=0)


class ManagedPopulationReceiptV1(FrozenExitSpecModel):
    """Exact counted populations and strict reliability denominator."""

    attempted_count: int = Field(gt=0, le=1_000)
    successful_count: int = Field(ge=0, le=1_000)
    failed_count: int = Field(ge=0, le=1_000)
    anomalous_count: int = Field(ge=0, le=1_000)
    required_attempts: int = Field(gt=0, le=1_000)
    required_successful_samples: int = Field(gt=0, le=1_000)
    required_configured_max_concurrency: int = Field(gt=0, le=1_000)
    observed_configured_max_concurrency: int = Field(gt=0, le=1_000)
    required_warmup_requests: int = Field(ge=0, le=1_000)
    observed_warmup_requests: int = Field(ge=0, le=1_000)
    error_numerator: Literal["failed_or_anomalous_native_measured_requests"]
    error_denominator: Literal["all_measured_requests"]
    error_threshold_basis_points: int = Field(gt=0, lt=10_000)
    observed_error_rate: str = Field(pattern=r"^(?:0|1|0\.[0-9]+)$")

    @model_validator(mode="after")
    def require_consistent_counts(self) -> "ManagedPopulationReceiptV1":
        if (
            self.successful_count + self.failed_count != self.attempted_count
            or self.anomalous_count > self.failed_count
            or self.required_successful_samples > self.required_attempts
        ):
            raise ValueError("Managed receipt populations are internally inconsistent.")
        expected_error_rate = format(
            Decimal(self.failed_count) / Decimal(self.attempted_count),
            "f",
        )
        if self.observed_error_rate != expected_error_rate:
            raise ValueError(
                "Managed receipt error rate disagrees with its counted population."
            )
        return self


class ManagedTargetReceiptV1(FrozenExitSpecModel):
    """Requested and observed target identities for applicability review."""

    requested_model: str = Field(min_length=1)
    observed_model: str = Field(min_length=1)
    requested_model_revision: str = Field(pattern=r"^[a-f0-9]{40}$")
    observed_model_revision: str = Field(pattern=r"^[a-f0-9]{40}$")
    requested_tokenizer_revision: str = Field(pattern=r"^[a-f0-9]{40}$")
    observed_tokenizer_revision: str = Field(pattern=r"^[a-f0-9]{40}$")
    requested_endpoint: str = Field(min_length=1)
    observed_endpoint: str = Field(min_length=1)


class ManagedEvidenceAssuranceV1(FrozenExitSpecModel):
    """Explicit limits on what the retained bytes can establish."""

    producer_evidence_consistency: Literal["VERIFIED"]
    hardware_attestation: Literal["NOT_AVAILABLE"]
    execution_attestation: Literal["NOT_AVAILABLE"]
    exact_achieved_concurrency: Literal["NOT_AVAILABLE"]
    transport_retry_behavior: Literal["NOT_AVAILABLE"]
    temporal_assurance: Literal["RETROSPECTIVE"]
    contract_preceded_measurement: Literal[False]
    production_authorization: Literal[False]


class InferdromeManagedReceiptV2(FrozenExitSpecModel):
    """ExitSpec-owned binding of one frozen contract to one unchanged bundle."""

    schema_version: Literal["exitspec.inferdrome-managed-receipt.v2"] = (
        INFERDROME_MANAGED_RECEIPT_SCHEMA_VERSION
    )
    receipt_id: str = Field(pattern=r"^irc2_[a-f0-9]{64}$")
    bundle_digest: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    contract_hash: str = Field(pattern=SHA256_PATTERN)
    criterion_id: str = Field(pattern=r"^[A-Z][A-Z0-9-]{2,63}$")
    run_id: str = Field(pattern=r"^run-[a-f0-9]{32}$")
    verifier_version: Literal["2.0.0"] = INFERDROME_MANAGED_VERIFIER_VERSION
    calculation_version: Literal["exitspec.inferdrome-managed-importer.v1"] = (
        INFERDROME_MANAGED_CALCULATION_VERSION
    )
    received_at: datetime
    ingestion_status: Literal["ACCEPTED"] = "ACCEPTED"
    acceptance_verdict: Literal["PASS", "FAIL", "NOT_PROVEN"]
    applicability_codes: tuple[ManagedApplicabilityCode, ...]
    evidence_schema_version: Literal["inferdrome.evidence.v1"]
    producer_name: Literal["vllm"]
    producer_version: Literal["0.26.0"]
    adapter_id: Literal["vllm_bench_serve"]
    adapter_version: Literal["1.0.0"]
    native_schema_fingerprint: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    managed_profile_id: Literal["inferdrome.managed-vllm-0.26-evidence-profile.v1"]
    managed_profile_sha256: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    local_gpu_proof_schema_id: Literal["urn:inferdrome:local-gpu-proof:v1"]
    local_gpu_proof_schema_sha256: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    profile_validator_version: Literal["1.0.0"]
    requested_request_plan_digest: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    observed_request_plan_digest: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    requested_workload_digest: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    observed_workload_digest: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    recalculation_sha256: str = Field(pattern=SHA256_PATTERN)
    binding_mode: Literal["EXTERNAL_RECEIPT_BINDING"]
    producer_contract_link: Literal["ABSENT"]
    purpose: Literal["CONFORMANCE_DEMONSTRATION"]
    target: ManagedTargetReceiptV1
    metric: ManagedMetricReceiptV1
    population: ManagedPopulationReceiptV1
    assurance: ManagedEvidenceAssuranceV1

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
        value: tuple[ManagedApplicabilityCode, ...],
    ) -> tuple[ManagedApplicabilityCode, ...]:
        expected = tuple(code for code in ManagedApplicabilityCode if code in value)
        if value != expected:
            raise ValueError(
                "applicability_codes must be unique and canonically ordered."
            )
        return value

    @model_validator(mode="after")
    def require_semantic_consistency(self) -> "InferdromeManagedReceiptV2":
        """Reject a re-hashed receipt whose visible facts contradict its verdict."""

        expected_identity = managed_receipt_id(
            self.model_dump(mode="json", exclude={"receipt_id"})
        )
        if not hmac.compare_digest(self.receipt_id, expected_identity):
            raise ValueError("receipt_id does not bind the managed evidence receipt.")
        expected_codes: set[ManagedApplicabilityCode] = set()
        target = self.target
        metric = self.metric
        population = self.population
        if target.requested_model != target.observed_model:
            expected_codes.add(ManagedApplicabilityCode.TARGET_MODEL_MISMATCH)
        if target.requested_model_revision != target.observed_model_revision:
            expected_codes.add(ManagedApplicabilityCode.MODEL_REVISION_MISMATCH)
        if target.requested_tokenizer_revision != target.observed_tokenizer_revision:
            expected_codes.add(ManagedApplicabilityCode.TOKENIZER_REVISION_MISMATCH)
        if target.requested_endpoint != target.observed_endpoint:
            expected_codes.add(ManagedApplicabilityCode.ENDPOINT_MISMATCH)
        if self.requested_request_plan_digest != self.observed_request_plan_digest:
            expected_codes.add(ManagedApplicabilityCode.REQUEST_PLAN_MISMATCH)
        if self.requested_workload_digest != self.observed_workload_digest:
            expected_codes.add(ManagedApplicabilityCode.WORKLOAD_MISMATCH)
        if (
            population.required_configured_max_concurrency
            != population.observed_configured_max_concurrency
        ):
            expected_codes.add(ManagedApplicabilityCode.CONFIGURED_CONCURRENCY_MISMATCH)
        if population.required_attempts != population.attempted_count:
            expected_codes.add(ManagedApplicabilityCode.REQUEST_POPULATION_MISMATCH)
        if population.required_warmup_requests != population.observed_warmup_requests:
            expected_codes.add(ManagedApplicabilityCode.WARMUP_POPULATION_MISMATCH)
        if metric.requested_definition_id != metric.observed_definition_id:
            expected_codes.add(ManagedApplicabilityCode.METRIC_DEFINITION_MISMATCH)
        if metric.requested_reducer_id != metric.observed_reducer_id:
            expected_codes.add(ManagedApplicabilityCode.REDUCER_MISMATCH)
        if metric.requested_population != metric.observed_population:
            expected_codes.add(ManagedApplicabilityCode.LATENCY_POPULATION_MISMATCH)
        if population.successful_count < population.required_successful_samples:
            expected_codes.add(ManagedApplicabilityCode.SUCCESSFUL_SAMPLE_SHORTFALL)

        actual_codes = set(self.applicability_codes)
        observable_actual = actual_codes - {
            ManagedApplicabilityCode.EVIDENCE_PROFILE_MISMATCH
        }
        if observable_actual != expected_codes:
            raise ValueError(
                "Managed receipt applicability codes contradict its visible facts."
            )
        global_codes = actual_codes - MANAGED_TTFT_ONLY_APPLICABILITY_CODES
        if global_codes:
            if self.acceptance_verdict != "NOT_PROVEN":
                raise ValueError(
                    "Globally inapplicable managed evidence must produce NOT_PROVEN."
                )
            return self

        reliability_passed = population.failed_count * 10_000 < (
            population.error_threshold_basis_points * population.attempted_count
        )
        if not reliability_passed:
            if self.acceptance_verdict != "FAIL":
                raise ValueError(
                    "A proven managed reliability failure must produce FAIL."
                )
            return self
        if actual_codes:
            if self.acceptance_verdict != "NOT_PROVEN":
                raise ValueError(
                    "Unproven managed latency with passing reliability must produce "
                    "NOT_PROVEN."
                )
            return self

        value = metric.recalculated_value_ns
        if value is None:
            raise ValueError(
                "Applicable managed evidence requires a recalculated TTFT value."
            )
        latency_passed = value < metric.threshold_ns
        expected_verdict = "PASS" if latency_passed and reliability_passed else "FAIL"
        if self.acceptance_verdict != expected_verdict:
            raise ValueError(
                "Managed receipt verdict contradicts its recalculated measurements."
            )
        return self

    @model_validator(mode="after")
    def require_bound_receipt_identity(self) -> "InferdromeManagedReceiptV2":
        expected = managed_receipt_id(
            self.model_dump(mode="json", exclude={"receipt_id"})
        )
        if not hmac.compare_digest(self.receipt_id, expected):
            raise ValueError("receipt_id does not bind the managed evidence receipt.")
        return self


def managed_receipt_id(payload: Mapping[str, Any]) -> str:
    """Derive an irc2 identity over every persisted field except itself."""

    normalized = dict(payload)
    if "receipt_id" in normalized:
        raise ValueError("Managed receipt identity payload must exclude receipt_id.")
    if (
        normalized.get("schema_version") != INFERDROME_MANAGED_RECEIPT_SCHEMA_VERSION
        or normalized.get("verifier_version") != INFERDROME_MANAGED_VERIFIER_VERSION
        or normalized.get("calculation_version")
        != INFERDROME_MANAGED_CALCULATION_VERSION
    ):
        raise ValueError("Managed receipt version identity is unsupported.")
    received_at = normalized.get("received_at")
    if isinstance(received_at, datetime):
        normalized["received_at"] = _canonical_timestamp(received_at)
    elif type(received_at) is str:
        try:
            parsed = datetime.fromisoformat(received_at.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError("Managed receipt timestamp is invalid.") from error
        normalized["received_at"] = _canonical_timestamp(parsed)
    else:
        raise ValueError("Managed receipt timestamp is invalid.")
    digest = hashlib.sha256(
        _RECEIPT_DOMAIN + canonical_json_bytes(normalized)
    ).hexdigest()
    return f"irc2_{digest}"


def managed_receipt_sha256(receipt: InferdromeManagedReceiptV2) -> str:
    """Return ordinary SHA-256 over the final canonical receipt JSON."""

    validated = validate_managed_receipt(receipt)
    payload = canonical_json_bytes(validated.model_dump(mode="json"))
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def validate_managed_receipt(
    receipt: InferdromeManagedReceiptV2,
) -> InferdromeManagedReceiptV2:
    """Reparse a copied receipt and recheck its complete derived identity."""

    if type(receipt) is not InferdromeManagedReceiptV2:
        raise TypeError("receipt must be an InferdromeManagedReceiptV2.")
    return InferdromeManagedReceiptV2.model_validate(receipt.model_dump(mode="python"))


def _canonical_timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Managed receipt timestamp must be timezone-aware.")
    return (
        value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
    )


__all__ = [
    "INFERDROME_MANAGED_CALCULATION_VERSION",
    "INFERDROME_MANAGED_RECEIPT_SCHEMA_VERSION",
    "INFERDROME_MANAGED_VERIFIER_VERSION",
    "MANAGED_TTFT_ONLY_APPLICABILITY_CODES",
    "InferdromeManagedReceiptV2",
    "ManagedApplicabilityCode",
    "ManagedEvidenceAssuranceV1",
    "ManagedMetricReceiptV1",
    "ManagedPopulationReceiptV1",
    "ManagedTargetReceiptV1",
    "managed_receipt_id",
    "managed_receipt_sha256",
    "validate_managed_receipt",
]
