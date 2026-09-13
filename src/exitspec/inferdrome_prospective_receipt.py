"""Prospective P1 receipt representation; parsing alone is not evidence admission."""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import ConfigDict, Field, field_validator, model_validator

from .canonical import canonical_json_bytes
from .inferdrome_prospective import (
    PROSPECTIVE_CASES,
    ProspectiveHandoffCaseModel,
    _case_identity,
    _criterion,
    source_yaml_bytes,
)
from .inferdrome_reporting_v2 import (
    ManagedApplicabilityCode,
    ManagedMetricReceiptV1,
    ManagedPopulationReceiptV1,
    ManagedTargetReceiptV1,
)
from .models import FrozenExitSpecModel

SCHEMA_VERSION = "exitspec.inferdrome-prospective-receipt.v1"
CALCULATION_VERSION = "exitspec.inferdrome-prospective-importer.v1"
MAX_RECEIPT_BYTES = 65_536
_DOMAIN = b"exitspec:inferdrome-prospective-receipt-v1\0"
_TIMES = (
    "received_at",
    "contract_frozen_at",
    "confirmation_decided_at",
    "producer_execution_started_at",
    "producer_execution_ended_at",
)
_TAG = r"^sha256:[a-f0-9]{64}$"
_BARE = r"^[a-f0-9]{64}$"


def _timestamp(value: datetime) -> str:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError("Receipt time must be timezone-aware.")
    return (
        value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
    )


def _decision(metric: ManagedMetricReceiptV1, population: ManagedPopulationReceiptV1):
    """Only independently valid exact reliability can establish a FAIL."""
    if (
        population.attempted_count != population.required_attempts
        or population.required_attempts != 100
    ):
        raise ValueError("P1 reliability population is not exact.")
    issues = set()
    for requested, observed, code in (
        (
            metric.requested_definition_id,
            metric.observed_definition_id,
            ManagedApplicabilityCode.METRIC_DEFINITION_MISMATCH,
        ),
        (
            metric.requested_reducer_id,
            metric.observed_reducer_id,
            ManagedApplicabilityCode.REDUCER_MISMATCH,
        ),
        (
            metric.requested_population,
            metric.observed_population,
            ManagedApplicabilityCode.LATENCY_POPULATION_MISMATCH,
        ),
    ):
        if requested != observed:
            issues.add(code)
    if population.successful_count < population.required_successful_samples:
        issues.add(ManagedApplicabilityCode.SUCCESSFUL_SAMPLE_SHORTFALL)
    codes = tuple(c for c in ManagedApplicabilityCode if c in issues)
    if (
        population.failed_count * 10_000
        >= population.error_threshold_basis_points * population.attempted_count
    ):
        return "FAIL", codes
    if codes or metric.recalculated_value_ns is None:
        return "NOT_PROVEN", codes
    return (
        "PASS" if metric.recalculated_value_ns < metric.threshold_ns else "FAIL"
    ), codes


class ProspectiveEvidenceAssuranceV1(FrozenExitSpecModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    producer_evidence_consistency: Literal["VERIFIED"] = "VERIFIED"
    hardware_attestation: Literal["NOT_AVAILABLE"] = "NOT_AVAILABLE"
    execution_attestation: Literal["NOT_AVAILABLE"] = "NOT_AVAILABLE"
    exact_achieved_concurrency: Literal["NOT_AVAILABLE"] = "NOT_AVAILABLE"
    transport_retry_behavior: Literal["NOT_AVAILABLE"] = "NOT_AVAILABLE"
    temporal_assurance: Literal["UNAVAILABLE"] = "UNAVAILABLE"
    contract_preceded_measurement: None = None
    production_authorization: Literal[False] = False
    sequence_requirement: Literal["OPERATOR_MUST_FREEZE_BEFORE_MEASUREMENT"] = (
        "OPERATOR_MUST_FREEZE_BEFORE_MEASUREMENT"
    )
    declared_sequence_consistency: Literal["CONSISTENT"] = "CONSISTENT"
    confirmation_identity_assurance: Literal[
        "PROCESS_LOCAL_DECLARED_IDENTITY_NOT_AUTHENTICATED"
    ] = "PROCESS_LOCAL_DECLARED_IDENTITY_NOT_AUTHENTICATED"
    claims_assurance: Literal["INTERNAL_CONSISTENCY_ONLY"] = "INTERNAL_CONSISTENCY_ONLY"


class InferdromeProspectiveReceiptV1(FrozenExitSpecModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    schema_version: Literal["exitspec.inferdrome-prospective-receipt.v1"] = (
        SCHEMA_VERSION
    )
    receipt_id: str = Field(pattern=r"^ipr1_[a-f0-9]{64}$")
    bundle_digest: str = Field(pattern=_TAG)
    contract_hash: str = Field(pattern=_BARE)
    criterion_id: str = Field(pattern=r"^[A-Z][A-Z0-9-]{2,63}$")
    run_id: str = Field(pattern=r"^run-[a-f0-9]{32}$")
    verifier_version: Literal["1.0.0"] = "1.0.0"
    calculation_version: Literal["exitspec.inferdrome-prospective-importer.v1"] = (
        CALCULATION_VERSION
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
    native_schema_fingerprint: str = Field(pattern=_TAG)
    managed_profile_id: Literal["inferdrome.managed-vllm-0.26-evidence-profile.v1"]
    managed_profile_sha256: str = Field(pattern=_TAG)
    local_gpu_proof_schema_id: Literal["urn:inferdrome:local-gpu-proof:v1"]
    local_gpu_proof_schema_sha256: str = Field(pattern=_TAG)
    profile_validator_version: Literal["1.0.0"]
    observed_request_plan_digest: str = Field(pattern=_TAG)
    requested_workload_digest: str = Field(pattern=_TAG)
    observed_workload_digest: str = Field(pattern=_TAG)
    recalculation_sha256: str = Field(pattern=_BARE)
    binding_mode: Literal["PRODUCER_CONTRACT_LINK"] = "PRODUCER_CONTRACT_LINK"
    producer_contract_link: str = Field(pattern=_TAG)
    purpose: Literal["CONFORMANCE_DEMONSTRATION"] = "CONFORMANCE_DEMONSTRATION"
    target: ManagedTargetReceiptV1
    metric: ManagedMetricReceiptV1
    population: ManagedPopulationReceiptV1
    assurance: ProspectiveEvidenceAssuranceV1
    handoff_manifest_sha256: str = Field(pattern=_BARE)
    handoff_case: ProspectiveHandoffCaseModel
    observed_execution_fingerprint: str = Field(pattern=_TAG)
    observed_source_spec_digest: str = Field(pattern=_TAG)
    request_plan_binding: Literal["P1_ORDERED_WORKLOAD_MATCH"] = (
        "P1_ORDERED_WORKLOAD_MATCH"
    )
    contract_frozen_at: datetime
    confirmation_decided_at: datetime
    producer_execution_started_at: datetime
    producer_execution_ended_at: datetime

    @field_validator(*_TIMES)
    @classmethod
    def aware_utc(cls, value: datetime) -> datetime:
        _timestamp(value)
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def exact_visible_binding(self):
        case = next(
            c for c in PROSPECTIVE_CASES if c.case_id == self.handoff_case.case_id
        )
        identity = _case_identity(case)
        criterion = _criterion(case)
        if (
            self.handoff_case.methodology != identity
            or self.criterion_id != criterion.id
        ):
            raise ValueError("Receipt is not the exact P1 case.")
        link = "sha256:" + self.contract_hash
        source = source_yaml_bytes(case, link)
        if (
            self.contract_hash != self.handoff_case.contract_canonical_hash
            or self.producer_contract_link != link
            or self.handoff_case.producer_contract_link != link
            or self.handoff_case.source_yaml_artifact_sha256
            != "sha256:" + hashlib.sha256(source).hexdigest()
            or self.observed_source_spec_digest
            != "sha256:"
            + hashlib.sha256(b"inferdrome:source-spec-v1\0" + source).hexdigest()
            or self.observed_execution_fingerprint
            != identity.expected_execution_fingerprint
            or self.requested_workload_digest != identity.workload_digest
            or self.observed_workload_digest != identity.workload_digest
        ):
            raise ValueError("Receipt input binding is inconsistent.")
        for name in (
            "evidence_schema_version",
            "producer_name",
            "producer_version",
            "adapter_id",
            "adapter_version",
            "native_schema_fingerprint",
            "managed_profile_id",
            "managed_profile_sha256",
            "local_gpu_proof_schema_id",
            "local_gpu_proof_schema_sha256",
        ):
            if getattr(self, name) != getattr(identity, name):
                raise ValueError("Receipt profile is not exact P1.")
        for name in ("model", "model_revision", "tokenizer_revision", "endpoint"):
            if any(
                getattr(self.target, prefix + name)
                != getattr(identity, "target_" + name)
                for prefix in ("requested_", "observed_")
            ):
                raise ValueError("Receipt target is not exact P1.")
        p, m = self.population, self.metric
        expected_population = {
            "required_attempts": 100,
            "attempted_count": 100,
            "required_successful_samples": 100,
            "required_configured_max_concurrency": 4,
            "observed_configured_max_concurrency": 4,
            "required_warmup_requests": 10,
            "observed_warmup_requests": 10,
            "error_threshold_basis_points": 100,
        }
        if any(getattr(p, k) != v for k, v in expected_population.items()):
            raise ValueError("Receipt population policy is not exact P1.")
        if (
            m.requested_definition_id != criterion.ttft_p95.definition_id
            or m.threshold_ns != criterion.ttft_p95.threshold_ns
            or m.requested_reducer_id != criterion.ttft_p95.reducer_id
            or m.requested_population != criterion.ttft_p95.population
        ):
            raise ValueError("Receipt requested metric is not exact P1.")
        verdict, codes = _decision(m, p)
        if (self.acceptance_verdict, self.applicability_codes) != (verdict, codes):
            raise ValueError("Receipt decision contradicts its visible facts.")
        if (
            not self.confirmation_decided_at
            <= self.contract_frozen_at
            <= self.producer_execution_started_at
            <= self.producer_execution_ended_at
            <= self.received_at
        ):
            raise ValueError("Receipt declared chronology is contradictory.")
        expected = prospective_receipt_id(
            self.model_dump(mode="json", exclude={"receipt_id"})
        )
        if not hmac.compare_digest(self.receipt_id, expected):
            raise ValueError("Receipt identity does not bind all fields.")
        return self


def prospective_receipt_id(payload: Mapping[str, Any]) -> str:
    value = dict(payload)
    if (
        "receipt_id" in value
        or value.get("schema_version") != SCHEMA_VERSION
        or value.get("verifier_version") != "1.0.0"
        or value.get("calculation_version") != CALCULATION_VERSION
    ):
        raise ValueError("Unsupported prospective receipt identity.")
    for name in _TIMES:
        timestamp = value[name]
        if isinstance(timestamp, str):
            timestamp = datetime.fromisoformat(timestamp)
        value[name] = _timestamp(timestamp)
    raw = canonical_json_bytes(value)
    if len(raw) > MAX_RECEIPT_BYTES:
        raise ValueError("Receipt exceeds its byte bound.")
    return "ipr1_" + hashlib.sha256(_DOMAIN + raw).hexdigest()


def serialize_prospective_receipt(receipt: InferdromeProspectiveReceiptV1) -> bytes:
    # Reparse copied/mutated model instances instead of trusting model_copy.
    receipt = InferdromeProspectiveReceiptV1.model_validate(
        receipt.model_dump(mode="python"), strict=True
    )
    value = receipt.model_dump(mode="json")
    for name in _TIMES:
        value[name] = _timestamp(getattr(receipt, name))
    raw = canonical_json_bytes(value)
    if len(raw) > MAX_RECEIPT_BYTES:
        raise ValueError("Receipt exceeds its byte bound.")
    return raw


def _strict_receipt_object(raw: bytes) -> dict:
    if type(raw) is not bytes or not 0 < len(raw) <= MAX_RECEIPT_BYTES:
        raise ValueError("Receipt bytes are outside their bound.")

    def pairs(items):
        out = {}
        for key, value in items:
            if key in out:
                raise ValueError("Duplicate receipt key.")
            out[key] = value
        return out

    def integer(value):
        result = int(value)
        if abs(result) > 9_007_199_254_740_991:
            raise ValueError("Receipt integer exceeds its bound.")
        return result

    def forbidden(_):
        raise ValueError("Noninteger receipt number.")

    try:
        value = json.loads(
            raw,
            object_pairs_hook=pairs,
            parse_int=integer,
            parse_float=forbidden,
            parse_constant=forbidden,
        )
        pending = [(value, 0)]
        nodes = 0
        while pending:
            item, depth = pending.pop()
            nodes += 1
            if nodes > 8192 or depth > 24:
                raise ValueError("Receipt structure exceeds its bound.")
            if isinstance(item, str) and len(item) > 8192:
                raise ValueError("Receipt string exceeds its bound.")
            if isinstance(item, dict):
                pending.extend((x, depth + 1) for pair in item.items() for x in pair)
            elif isinstance(item, list):
                pending.extend((x, depth + 1) for x in item)
    except (RecursionError, UnicodeError, OverflowError):
        raise ValueError("Invalid receipt structure.") from None
    if type(value) is not dict:
        raise ValueError("Receipt must be an object.")
    return value


def parse_prospective_receipt(raw: bytes) -> InferdromeProspectiveReceiptV1:
    _strict_receipt_object(raw)
    receipt = InferdromeProspectiveReceiptV1.model_validate_json(raw, strict=True)
    if serialize_prospective_receipt(receipt) != raw:
        raise ValueError("Receipt must use exact canonical bytes.")
    return receipt
