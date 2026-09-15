"""Bounded offline P1 admission. No producer imports, execution, CLI or services."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import fields
from datetime import UTC, datetime
from decimal import Decimal
from itertools import pairwise
from pathlib import Path

import yaml

from .inferdrome_bundle import (
    INFERDROME_VERIFIER_VERSION,
    InferdromeBundleLimits,
    InferdromeBundleRejected,
    VerifiedInferdromeBundle,
    verify_inferdrome_bundle,
)
from .inferdrome_prospective import PROSPECTIVE_CASES
from .inferdrome_prospective_context import (
    ManifestPinMismatch,
    ProspectiveContext,
    capture_prospective_context,
)
from .inferdrome_prospective_receipt import (
    CALCULATION_VERSION,
    SCHEMA_VERSION,
    InferdromeProspectiveReceiptV1,
    ProspectiveEvidenceAssuranceV1,
    _decision,
    _timestamp,
    parse_prospective_receipt,
    prospective_receipt_id,
    serialize_prospective_receipt,
)
from .inferdrome_reporting_v2 import (
    ManagedMetricReceiptV1,
    ManagedPopulationReceiptV1,
    ManagedTargetReceiptV1,
)


class ProspectiveAdmissionRejected(ValueError):
    """Closed refusal: no partial receipt or producer-controlled error payload."""

    def __init__(self, code: str, field_paths: tuple[str, ...] = ()):
        self.code = code
        self.field_paths = field_paths
        super().__init__(code)


def _reject(code, *paths):
    raise ProspectiveAdmissionRejected(code, tuple(sorted(set(paths))))


def _path(value) -> Path:
    if not isinstance(value, (str, Path)):
        _reject("BINDING_MISMATCH", "path")
    try:
        path = Path(value)
        if len(str(path)) > 4096 or not path.is_absolute() or path.resolve() != path:
            _reject("BINDING_MISMATCH", "path")
    except (OSError, ValueError, RuntimeError):
        _reject("BINDING_MISMATCH", "path")
    return path


def _time(value) -> datetime:
    try:
        if type(value) is str:
            value = datetime.fromisoformat(value)
        _timestamp(value)
        return value.astimezone(UTC)
    except (TypeError, ValueError, OverflowError):
        _reject("DECLARED_CHRONOLOGY_CONTRADICTION")


def _get(root, path):
    for key in path.split("."):
        if not isinstance(root, dict) and not hasattr(root, "get"):
            return None
        root = root.get(key)
    return root


def _methodology(context: ProspectiveContext, bundle: VerifiedInferdromeBundle):
    """Check the approved 45-field mapping; consumer policy IDs stay consumer-side."""
    identity = context.case.methodology
    descriptor, resolved = bundle.descriptor, bundle.resolved_spec
    checks = []

    def equal(label, actual, expected):
        if type(actual) is not type(expected) or actual != expected:
            checks.append(label)

    equal(
        "descriptor.evidence_eligibility",
        descriptor.get("evidence_eligibility"),
        "CUSTOMER_ELIGIBLE",
    )
    equal(
        "descriptor.schema_version",
        descriptor.get("schema_version"),
        identity.evidence_schema_version,
    )
    equal(
        "descriptor.execution_mode",
        descriptor.get("execution_mode"),
        identity.execution_mode,
    )
    for key, field in {
        "name": "producer_name",
        "version": "producer_version",
        "adapter": "adapter_id",
        "adapter_version": "adapter_version",
        "native_schema_fingerprint": "native_schema_fingerprint",
    }.items():
        equal(
            "descriptor.producer." + key,
            _get(descriptor, "producer." + key),
            getattr(identity, field),
        )
    for key, expected in {
        "mode": identity.execution_mode,
        "producer_name": identity.producer_name,
        "producer_version": identity.producer_version,
        "adapter": identity.adapter_id,
        "adapter_version": identity.adapter_version,
        "max_runtime_seconds": identity.max_runtime_seconds,
        "max_measured_requests": identity.max_measured_requests,
    }.items():
        equal("resolved.execution." + key, _get(resolved, "execution." + key), expected)
    for key in (
        "engine",
        "engine_version",
        "api",
        "model",
        "model_revision",
        "tokenizer_revision",
        "endpoint",
    ):
        equal(
            "resolved.target." + key,
            _get(resolved, "target." + key),
            getattr(identity, "target_" + key),
        )
    traffic = {
        "kind": identity.traffic.kind,
        "concurrency": identity.traffic.configured_concurrency,
        "measured_requests": identity.traffic.measured_requests,
        "warmup_requests": identity.traffic.warmup_requests,
    }
    for key, expected in traffic.items():
        equal("resolved.traffic." + key, _get(resolved, "traffic." + key), expected)
        equal(
            "plan.traffic." + key, _get(bundle.request_plan, "traffic." + key), expected
        )
        equal(
            "execution.configured_traffic." + key,
            _get(bundle.execution, "configured_traffic." + key),
            expected,
        )
    for key, expected in {
        "sha256": identity.workload_digest,
        "prompt_content_policy": identity.sampling.prompt_content_policy,
        "requested_output_tokens": identity.sampling.requested_output_tokens,
        "temperature": str(identity.sampling.temperature),
        "seed": identity.sampling.seed,
    }.items():
        equal("resolved.workload." + key, _get(resolved, "workload." + key), expected)
    for key, expected in {
        "streaming": identity.measurement_streaming,
        "ttft_definition": identity.produced_evidence_metric_definition_id,
        "choices_span_definition": identity.choices_span_definition_id,
        "metric_definitions_version": identity.metric_definitions_version,
        "reducer_version": identity.reducer_version,
    }.items():
        equal(
            "resolved.measurement." + key,
            _get(resolved, "measurement." + key),
            expected,
        )
    for key in (
        "native_output_sensitivity",
        "canonical_response_content",
        "include_request_plan",
    ):
        equal(
            "resolved.evidence." + key,
            _get(resolved, "evidence." + key),
            getattr(identity, key),
        )
    facts = bundle.managed_profile
    for key, field in {
        "profile_id": "managed_profile_id",
        "profile_sha256": "managed_profile_sha256",
        "local_gpu_proof_schema_id": "local_gpu_proof_schema_id",
        "local_gpu_proof_schema_sha256": "local_gpu_proof_schema_sha256",
        "claims_assurance": "claims_assurance",
    }.items():
        equal(
            "managed_profile." + key,
            getattr(facts, key, None),
            getattr(identity, field),
        )
    equal(
        "managed_profile.validator_version",
        getattr(facts, "validator_version", None),
        "1.0.0",
    )
    equal(
        "descriptor.digests.execution_fingerprint",
        _get(descriptor, "digests.execution_fingerprint"),
        identity.expected_execution_fingerprint,
    )
    source_digest = (
        "sha256:"
        + hashlib.sha256(
            b"inferdrome:source-spec-v1\0" + context.source_bytes
        ).hexdigest()
    )
    equal(
        "descriptor.digests.source_spec_digest",
        _get(descriptor, "digests.source_spec_digest"),
        source_digest,
    )
    source = yaml.safe_load(
        context.source_bytes
    )  # Already exact, bounded P1 YAML bytes.
    equal(
        "resolved.experiment.id",
        _get(resolved, "experiment.id"),
        source["experiment"]["id"],
    )
    equal("records.count", len(bundle.records), identity.traffic.measured_requests)
    equal(
        "recalculated.ttft_definition",
        bundle.recalculated.ttft_definition,
        identity.produced_evidence_metric_definition_id,
    )
    if checks:
        _reject("P1_NOT_APPLICABLE", *checks)


def _ordered_plan(context: ProspectiveContext, bundle: VerifiedInferdromeBundle):
    rows = context.workload_bytes.splitlines()
    plan = bundle.request_plan
    requests = plan.get("requests")
    # Both the existing handoff and verifier bound these structures first.
    if (
        len(rows) != 100
        or not isinstance(requests, (list, tuple))
        or len(requests) != 100
    ):
        _reject("P1_NOT_APPLICABLE", "plan.population")
    run_id = bundle.descriptor["run_id"]
    if plan.get("producer_request_id_prefix") != run_id + "-":
        _reject("P1_NOT_APPLICABLE", "plan.producer_request_id_prefix")
    for index, (raw, request) in enumerate(zip(rows, requests, strict=True)):
        prompt = json.loads(raw)["prompt"]
        expected = {
            "sequence_index": index,
            "request_id": f"req-{index:08d}",
            "producer_request_id": f"{run_id}-{index}",
            "prompt.kind": "inline",
            "prompt.text": prompt,
            "prompt.sha256": "sha256:" + hashlib.sha256(prompt.encode()).hexdigest(),
            "sampling.requested_output_tokens": 32,
            "sampling.temperature": "0",
            "sampling.seed": 42,
        }
        for key, value in expected.items():
            actual = _get(request, key)
            if type(actual) is not type(value) or actual != value:
                _reject("P1_NOT_APPLICABLE", "plan.requests." + key)


def _measurements(context, bundle):
    measurement, identity = bundle.recalculated, context.case.methodology
    for name in (
        "attempted_count",
        "successful_count",
        "failed_count",
        "anomalous_count",
    ):
        value = getattr(measurement, name)
        if type(value) is not int or not 0 <= value <= 100:
            _reject("P1_NOT_APPLICABLE", "recalculated." + name)
    if (
        measurement.attempted_count != 100
        or measurement.successful_count + measurement.failed_count != 100
        or measurement.anomalous_count > measurement.failed_count
        or type(measurement.error_rate) is not Decimal
        or not measurement.error_rate.is_finite()
        or measurement.error_rate != Decimal(measurement.failed_count) / 100
    ):
        _reject("P1_NOT_APPLICABLE", "recalculated.population")
    if measurement.p95_ttft_ns is not None and (
        type(measurement.p95_ttft_ns) is not int
        or not 0 <= measurement.p95_ttft_ns <= 9_007_199_254_740_991
    ):
        _reject("P1_NOT_APPLICABLE", "recalculated.p95_ttft_ns")
    population = ManagedPopulationReceiptV1.model_validate(
        {
            "attempted_count": measurement.attempted_count,
            "successful_count": measurement.successful_count,
            "failed_count": measurement.failed_count,
            "anomalous_count": measurement.anomalous_count,
            "required_attempts": 100,
            "required_successful_samples": 100,
            "required_configured_max_concurrency": 4,
            "observed_configured_max_concurrency": _get(
                bundle.resolved_spec, "traffic.concurrency"
            ),
            "required_warmup_requests": 10,
            "observed_warmup_requests": _get(
                bundle.resolved_spec, "traffic.warmup_requests"
            ),
            "error_numerator": identity.reliability_population.numerator,
            "error_denominator": identity.reliability_population.denominator,
            "error_threshold_basis_points": 100,
            "observed_error_rate": format(measurement.error_rate, "f"),
        },
        strict=True,
    )
    rule = context.contract.criteria[0].ttft_p95
    metric = ManagedMetricReceiptV1.model_validate(
        {
            "metric": rule.metric,
            "aggregation": rule.aggregation,
            "unit": rule.unit,
            "operator": rule.operator,
            "requested_definition_id": rule.definition_id,
            "observed_definition_id": measurement.ttft_definition,
            "requested_reducer_id": rule.reducer_id,
            "observed_reducer_id": "nearest_rank_v1",
            "requested_population": rule.population,
            "observed_population": "successful_measured_requests_with_observed_ttft",
            "threshold_ns": rule.threshold_ns,
            "recalculated_value_ns": measurement.p95_ttft_ns,
        },
        strict=True,
    )
    return metric, population


def _admit_verified(
    context: ProspectiveContext,
    bundle: VerifiedInferdromeBundle,
    expected_run_id: str,
    expected_bundle_digest: str,
    received_at: datetime,
):
    """Internal unit-test seam, never a substitute for public byte verification."""
    link = context.case.producer_contract_link
    if (
        bundle.bundle_digest != expected_bundle_digest
        or bundle.descriptor.get("run_id") != expected_run_id
        or _get(bundle.descriptor, "digests.exitspec_contract_digest") != link
        or _get(bundle.resolved_spec, "links.exitspec_contract_digest") != link
    ):
        _reject("BINDING_MISMATCH")
    _methodology(context, bundle)
    _ordered_plan(context, bundle)
    started, ended = (
        _time(bundle.execution.get(name)) for name in ("started_at", "ended_at")
    )
    times = (
        _time(context.contract.created_at),
        _time(context.contract.approved_at),
        _time(context.confirmation.decided_at),
        _time(context.contract.frozen_at),
        started,
        ended,
        _time(received_at),
    )
    if any(a > b for a, b in pairwise(times)):
        _reject("DECLARED_CHRONOLOGY_CONTRADICTION")
    metric, population = _measurements(context, bundle)
    verdict, codes = _decision(metric, population)
    identity, facts = context.case.methodology, bundle.managed_profile
    target = {}
    for key in ("model", "model_revision", "tokenizer_revision", "endpoint"):
        target["requested_" + key] = getattr(identity, "target_" + key)
        target["observed_" + key] = _get(bundle.resolved_spec, "target." + key)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "bundle_digest": bundle.bundle_digest,
        "contract_hash": context.case.contract_canonical_hash,
        "criterion_id": context.contract.criteria[0].id,
        "run_id": expected_run_id,
        "verifier_version": INFERDROME_VERIFIER_VERSION,
        "calculation_version": CALCULATION_VERSION,
        "received_at": _timestamp(received_at),
        "ingestion_status": "ACCEPTED",
        "acceptance_verdict": verdict,
        "applicability_codes": [c.value for c in codes],
        **{
            key: getattr(identity, key)
            for key in (
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
            )
        },
        "profile_validator_version": facts.validator_version,
        "observed_request_plan_digest": _get(
            bundle.descriptor, "digests.request_plan_digest"
        ),
        "requested_workload_digest": identity.workload_digest,
        "observed_workload_digest": _get(bundle.resolved_spec, "workload.sha256"),
        "recalculation_sha256": bundle.recalculated.recalculation_sha256,
        "binding_mode": "PRODUCER_CONTRACT_LINK",
        "producer_contract_link": link,
        "purpose": "CONFORMANCE_DEMONSTRATION",
        "target": ManagedTargetReceiptV1.model_validate(target, strict=True).model_dump(
            mode="json"
        ),
        "metric": metric.model_dump(mode="json"),
        "population": population.model_dump(mode="json"),
        "assurance": ProspectiveEvidenceAssuranceV1().model_dump(mode="json"),
        "handoff_manifest_sha256": context.manifest_sha256,
        "handoff_case": context.case.model_dump(mode="json"),
        "observed_execution_fingerprint": _get(
            bundle.descriptor, "digests.execution_fingerprint"
        ),
        "observed_source_spec_digest": _get(
            bundle.descriptor, "digests.source_spec_digest"
        ),
        "request_plan_binding": "P1_ORDERED_WORKLOAD_MATCH",
        "contract_frozen_at": _timestamp(context.contract.frozen_at),
        "confirmation_decided_at": _timestamp(context.confirmation.decided_at),
        "producer_execution_started_at": _timestamp(started),
        "producer_execution_ended_at": _timestamp(ended),
    }
    payload["receipt_id"] = prospective_receipt_id(payload)
    # Strict JSON-mode validation preserves enum/datetime decoding, rejects coercions.
    from .canonical import canonical_json_bytes

    return parse_prospective_receipt(canonical_json_bytes(payload))


def admit_prospective_bundle(
    handoff_path,
    case_id,
    bundle_path,
    *,
    expected_handoff_manifest_sha256,
    expected_bundle_digest,
    expected_run_id,
    received_at,
    limits=None,
) -> InferdromeProspectiveReceiptV1:
    for value, pattern in (
        (expected_handoff_manifest_sha256, r"[a-f0-9]{64}"),
        (expected_bundle_digest, r"sha256:[a-f0-9]{64}"),
        (expected_run_id, r"run-[a-f0-9]{32}"),
    ):
        if type(value) is not str or re.fullmatch(pattern, value) is None:
            _reject("BINDING_MISMATCH", "pin")
    if type(case_id) is not str or case_id not in {
        c.case_id for c in PROSPECTIVE_CASES
    }:
        _reject("CONTEXT_NOT_AUTHORIZED", "case_id")
    if not isinstance(received_at, datetime):
        _reject("DECLARED_CHRONOLOGY_CONTRADICTION")
    received_at = _time(received_at)
    defaults = InferdromeBundleLimits()
    if limits is not None and (
        type(limits) is not InferdromeBundleLimits
        or any(
            type(getattr(limits, field.name)) is not int
            or not 0 < getattr(limits, field.name) <= getattr(defaults, field.name)
            for field in fields(defaults)
        )
    ):
        _reject("BINDING_MISMATCH", "limits")
    source, bundle_path = _path(handoff_path), _path(bundle_path)
    try:
        context = capture_prospective_context(
            source, case_id, expected_handoff_manifest_sha256
        )
        contract = context.contract
        if (
            contract.target_system.provider != "inferdrome-managed-vllm"
            or contract.target_system.endpoint_class
            != "retained-loopback-vllm-benchmark"
            or contract.target_system.model != context.case.methodology.target_model
            or contract.workload.sha256 != context.case.methodology.workload_digest[7:]
        ):
            _reject("CONTEXT_NOT_AUTHORIZED")
    except ManifestPinMismatch:
        _reject("BINDING_MISMATCH", "handoff_manifest_sha256")
    except (OSError, ValueError, TypeError, RecursionError, StopIteration):
        _reject("CONTEXT_NOT_AUTHORIZED")
    # Existing verifier refusals deliberately propagate unchanged.
    verified = verify_inferdrome_bundle(
        bundle_path,
        expected_bundle_digest=expected_bundle_digest,
        limits=limits,
        require_customer_eligible=True,
    )
    try:
        receipt = _admit_verified(
            context, verified, expected_run_id, expected_bundle_digest, received_at
        )
        context.reader.assert_unchanged()
        return receipt
    except ProspectiveAdmissionRejected:
        raise
    except InferdromeBundleRejected:
        _reject("CONTEXT_NOT_AUTHORIZED")
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        RecursionError,
        OverflowError,
    ):
        _reject("P1_NOT_APPLICABLE", "validated_facts")


def verify_prospective_receipt(
    receipt_bytes,
    handoff_path,
    case_id,
    bundle_path,
    *,
    expected_handoff_manifest_sha256,
    expected_bundle_digest,
    expected_run_id,
    limits=None,
) -> InferdromeProspectiveReceiptV1:
    try:
        receipt = parse_prospective_receipt(receipt_bytes)
    except (ValueError, TypeError, KeyError, RecursionError, OverflowError):
        _reject("INVALID_RECEIPT")
    regenerated = admit_prospective_bundle(
        handoff_path,
        case_id,
        bundle_path,
        expected_handoff_manifest_sha256=expected_handoff_manifest_sha256,
        expected_bundle_digest=expected_bundle_digest,
        expected_run_id=expected_run_id,
        received_at=receipt.received_at,
        limits=limits,
    )
    if serialize_prospective_receipt(regenerated) != receipt_bytes:
        _reject("RECEIPT_MISMATCH")
    return regenerated


__all__ = [
    "ProspectiveAdmissionRejected",
    "admit_prospective_bundle",
    "verify_prospective_receipt",
]
