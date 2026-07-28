"""Strict deterministic serialization for performance decision artifacts.

Persisted verdict bytes are display data only.  They become trustworthy only
after :func:`recompute_and_compare_performance_verdict` recreates the verdict
from a validated probe run and the frozen criterion.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Final

from pydantic import ValidationError

from .canonical import CanonicalizationError, canonical_json_bytes
from .confirmations import (
    ContractConfirmation,
    confirmation_operation_id,
)
from .contracts import verify_contract_digest
from .models import (
    InferencePerformanceCriterion,
    POCContract,
    VerdictStatus,
)
from .performance_probe import (
    PROBE_SCHEMA_VERSION,
    ProbeConfig,
    ProbeEvidenceError,
    ProbeManifest,
    ProbeOutcome,
    ProbePhase,
    ProbeRecord,
    ProbeRun,
    PromptDescriptor,
    records_jsonl,
    validate_probe_run,
)
from .performance_receipts import (
    PerformanceExecutionReceipt,
    PerformanceReceiptIntegrityError,
    validate_performance_receipt,
)
from .performance_verdicts import (
    CALCULATION_VERSION,
    ErrorRateRuleResult,
    PerformanceCriterionVerdict,
    TTFTP95RuleResult,
    evaluate_performance_criterion,
)


_MAX_JSON_BYTES: Final = 16 * 1024 * 1024
_MAX_JSONL_BYTES: Final = 64 * 1024 * 1024
_SHA256_LENGTH: Final = 64
_PROMPT_ID: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
PREFLIGHT_ENVELOPE_SCHEMA_VERSION: Final = (
    "exitspec.performance-preflight.v1"
)

_MANIFEST_FIELDS: Final = frozenset(
    {
        "schema_version",
        "manifest_sha256",
        "endpoint",
        "model",
        "request_count",
        "concurrency",
        "warmup_count",
        "timeout_seconds",
        "max_tokens",
        "max_stream_bytes",
        "first_token_definition",
        "warmup_included_in_measurement",
        "prompts",
        "prompt_set_sha256",
    }
)
_PROMPT_DESCRIPTOR_FIELDS: Final = frozenset({"prompt_id", "sha256"})
_PREFLIGHT_ENVELOPE_FIELDS: Final = frozenset(
    {
        "schema_version",
        "execution_id",
        "records_sha256",
        "manifest",
        "records",
    }
)
_RECORD_FIELDS: Final = frozenset(
    {
        "schema_version",
        "execution_id",
        "manifest_sha256",
        "request_id",
        "phase",
        "ordinal",
        "included_in_measurement",
        "prompt_id",
        "prompt_sha256",
        "outcome",
        "http_status",
        "ttft_ns",
        "duration_ns",
    }
)
_CONFIRMATION_PERSISTED_FIELDS: Final = frozenset(
    {
        "confirmation_id",
        "contract_id",
        "contract_version",
        "contract_fingerprint",
        "confirmer_identity",
        "decision",
        "agreement_acknowledged",
        "decided_at",
        "rationale",
    }
)
_VERDICT_FIELDS: Final = frozenset(
    {
        "criterion_id",
        "verdict",
        "attempted_count",
        "successful_count",
        "error_count",
        "ttft_p95",
        "error_rate",
        "calculation_version",
        "reason",
        "limitations",
    }
)
_TTFT_FIELDS: Final = frozenset(
    {
        "verdict",
        "observed_ns",
        "threshold_ns",
        "operator",
        "successful_samples",
        "minimum_successful_samples",
        "reason",
    }
)
_ERROR_RATE_FIELDS: Final = frozenset(
    {
        "verdict",
        "error_count",
        "attempted_count",
        "observed_rate",
        "threshold",
        "operator",
        "minimum_attempts",
        "reason",
    }
)


class PerformanceSerializationError(ValueError):
    """Serialized performance data is malformed, unsafe, or inconsistent."""


@dataclass(frozen=True, slots=True)
class TTFTP95Display:
    """Untrusted persisted display data for the TTFT calculation."""

    verdict: str
    observed_ns: int | None
    threshold_ns: int | None
    operator: str
    successful_samples: int
    minimum_successful_samples: int
    reason: str


@dataclass(frozen=True, slots=True)
class ErrorRateDisplay:
    """Untrusted persisted display data for the error-rate calculation."""

    verdict: str
    error_count: int
    attempted_count: int
    observed_rate: str | None
    threshold: str
    operator: str
    minimum_attempts: int
    reason: str


@dataclass(frozen=True, slots=True)
class PerformanceVerdictDisplay:
    """An explicitly non-authoritative projection loaded from disk."""

    criterion_id: str
    verdict: str
    attempted_count: int
    successful_count: int
    error_count: int
    ttft_p95: TTFTP95Display
    error_rate: ErrorRateDisplay
    calculation_version: str
    reason: str
    limitations: tuple[str, ...]


def serialize_contract(contract: POCContract) -> bytes:
    """Return canonical bytes for a strictly validated contract."""

    if type(contract) is not POCContract:
        raise TypeError("contract must be a POCContract.")
    payload = contract.model_dump(mode="json")
    serialized = canonical_json_bytes(payload)
    parsed = parse_contract(serialized)
    if parsed != contract:
        raise PerformanceSerializationError(
            "Contract did not survive strict serialization."
        )
    return serialized


def parse_contract(data: bytes) -> POCContract:
    """Parse canonical contract bytes without Pydantic coercion."""

    _load_canonical_object(data, artifact_name="contract")
    try:
        contract = POCContract.model_validate_json(data, strict=True)
    except ValidationError as exc:
        raise PerformanceSerializationError(
            "Contract JSON failed strict domain validation."
        ) from exc
    if contract.canonical_hash is not None and not verify_contract_digest(contract):
        raise PerformanceSerializationError("Contract digest is invalid.")
    return contract


def serialize_confirmation(confirmation: ContractConfirmation) -> bytes:
    """Serialize a confirmation without persisting its raw idempotency key."""

    if type(confirmation) is not ContractConfirmation:
        raise TypeError("confirmation must be a ContractConfirmation.")
    if (
        confirmation.decided_at.tzinfo is None
        or confirmation.decided_at.utcoffset() is None
    ):
        raise PerformanceSerializationError(
            "Confirmation timestamp must be timezone-aware."
        )
    expected_id = confirmation_operation_id(
        confirmation.contract_id,
        confirmation.contract_version,
        confirmation.idempotency_key,
    )
    if not hmac.compare_digest(confirmation.confirmation_id, expected_id):
        raise PerformanceSerializationError(
            "Confirmation identity does not bind its idempotency key."
        )
    payload = confirmation.model_dump(mode="json", exclude={"idempotency_key"})
    _require_exact_fields(
        payload,
        _CONFIRMATION_PERSISTED_FIELDS,
        "confirmation",
    )
    return canonical_json_bytes(payload)


def parse_confirmation(
    data: bytes,
    *,
    idempotency_key: str,
) -> ContractConfirmation:
    """Reload a confirmation using an ephemeral key not stored in the artifact."""

    payload = _load_canonical_object(data, artifact_name="confirmation")
    _require_exact_fields(
        payload,
        _CONFIRMATION_PERSISTED_FIELDS,
        "confirmation",
    )
    if type(idempotency_key) is not str or not idempotency_key:
        raise PerformanceSerializationError(
            "An exact ephemeral idempotency key is required."
        )
    hydrated = dict(payload)
    hydrated["idempotency_key"] = idempotency_key
    try:
        confirmation = ContractConfirmation.model_validate_json(
            canonical_json_bytes(hydrated),
            strict=True,
        )
    except (CanonicalizationError, ValidationError) as exc:
        raise PerformanceSerializationError(
            "Confirmation JSON failed strict domain validation."
        ) from exc
    if (
        confirmation.decided_at.tzinfo is None
        or confirmation.decided_at.utcoffset() is None
    ):
        raise PerformanceSerializationError(
            "Confirmation timestamp must be timezone-aware."
        )
    expected_id = confirmation_operation_id(
        confirmation.contract_id,
        confirmation.contract_version,
        idempotency_key,
    )
    if not hmac.compare_digest(confirmation.confirmation_id, expected_id):
        raise PerformanceSerializationError(
            "Confirmation identity does not bind the supplied idempotency key."
        )
    return confirmation


def serialize_probe_manifest(manifest: ProbeManifest) -> bytes:
    """Return canonical bytes for a valid, content-free probe manifest."""

    if type(manifest) is not ProbeManifest:
        raise TypeError("manifest must be a ProbeManifest.")
    serialized = canonical_json_bytes(manifest.to_dict())
    if parse_probe_manifest(serialized) != manifest:
        raise PerformanceSerializationError(
            "Probe manifest did not survive strict serialization."
        )
    return serialized


def parse_probe_manifest(data: bytes) -> ProbeManifest:
    """Parse and independently verify one canonical probe manifest."""

    payload = _load_canonical_object(data, artifact_name="probe manifest")
    _require_exact_fields(payload, _MANIFEST_FIELDS, "probe manifest")
    prompt_values = _require_list(payload["prompts"], "manifest prompts")
    prompts: list[PromptDescriptor] = []
    for index, value in enumerate(prompt_values):
        descriptor = _require_object(
            value,
            "manifest prompt descriptor {0}".format(index),
        )
        _require_exact_fields(
            descriptor,
            _PROMPT_DESCRIPTOR_FIELDS,
            "manifest prompt descriptor",
        )
        prompts.append(
            PromptDescriptor(
                prompt_id=_require_str(
                    descriptor["prompt_id"],
                    "prompt_id",
                ),
                sha256=_require_sha256(
                    descriptor["sha256"],
                    "prompt sha256",
                ),
            )
        )
    timeout_value = payload["timeout_seconds"]
    if (
        isinstance(timeout_value, bool)
        or not isinstance(timeout_value, (int, float))
        or not math.isfinite(float(timeout_value))
    ):
        raise PerformanceSerializationError(
            "timeout_seconds must be a finite JSON number."
        )
    manifest = ProbeManifest(
        schema_version=_require_str(
            payload["schema_version"],
            "schema_version",
        ),
        manifest_sha256=_require_sha256(
            payload["manifest_sha256"],
            "manifest_sha256",
        ),
        endpoint=_require_str(payload["endpoint"], "endpoint"),
        model=_require_str(payload["model"], "model"),
        request_count=_require_int(
            payload["request_count"],
            "request_count",
        ),
        concurrency=_require_int(payload["concurrency"], "concurrency"),
        warmup_count=_require_int(
            payload["warmup_count"],
            "warmup_count",
        ),
        timeout_seconds=float(timeout_value),
        max_tokens=_require_int(payload["max_tokens"], "max_tokens"),
        max_stream_bytes=_require_int(
            payload["max_stream_bytes"],
            "max_stream_bytes",
        ),
        first_token_definition=_require_str(
            payload["first_token_definition"],
            "first_token_definition",
        ),
        warmup_included_in_measurement=_require_bool(
            payload["warmup_included_in_measurement"],
            "warmup_included_in_measurement",
        ),
        prompts=tuple(prompts),
        prompt_set_sha256=_require_sha256(
            payload["prompt_set_sha256"],
            "prompt_set_sha256",
        ),
    )
    _validate_manifest(manifest)
    return manifest


def serialize_probe_records_jsonl(probe_run: ProbeRun) -> bytes:
    """Serialize the validated run's terminal records in canonical order."""

    if type(probe_run) is not ProbeRun:
        raise TypeError("probe_run must be a ProbeRun.")
    try:
        validate_probe_run(probe_run)
    except ProbeEvidenceError as exc:
        raise PerformanceSerializationError("Probe run is invalid.") from exc
    serialized = records_jsonl(probe_run.records).encode("utf-8")
    expected_hash = hashlib.sha256(serialized).hexdigest()
    if not hmac.compare_digest(expected_hash, probe_run.records_sha256):
        raise PerformanceSerializationError("Probe record hash is invalid.")
    return serialized


def serialize_probe_run(probe_run: ProbeRun) -> tuple[bytes, bytes]:
    """Return the separately persistable manifest and record artifacts."""

    return (
        serialize_probe_manifest(probe_run.manifest),
        serialize_probe_records_jsonl(probe_run),
    )


def serialize_probe_run_envelope(probe_run: ProbeRun) -> bytes:
    """Serialize one self-binding probe run for readiness evidence."""

    if type(probe_run) is not ProbeRun:
        raise TypeError("probe_run must be a ProbeRun.")
    manifest_json, records_jsonl_bytes = serialize_probe_run(probe_run)
    records = [
        json.loads(line)
        for line in records_jsonl_bytes.decode("utf-8").splitlines()
    ]
    return canonical_json_bytes(
        {
            "schema_version": PREFLIGHT_ENVELOPE_SCHEMA_VERSION,
            "execution_id": probe_run.execution_id,
            "records_sha256": probe_run.records_sha256,
            "manifest": json.loads(manifest_json),
            "records": records,
        }
    )


def parse_probe_run_envelope(data: bytes) -> ProbeRun:
    """Reconstruct and validate one canonical readiness probe envelope."""

    payload = _load_canonical_object(
        data,
        artifact_name="preflight probe",
    )
    _require_exact_fields(
        payload,
        _PREFLIGHT_ENVELOPE_FIELDS,
        "preflight probe",
    )
    if payload["schema_version"] != PREFLIGHT_ENVELOPE_SCHEMA_VERSION:
        raise PerformanceSerializationError(
            "Preflight probe schema version is unsupported."
        )
    if type(payload["manifest"]) is not dict:
        raise PerformanceSerializationError(
            "Preflight probe manifest must be an object."
        )
    records = payload["records"]
    if type(records) is not list or not records:
        raise PerformanceSerializationError(
            "Preflight probe records must be a non-empty array."
        )
    if any(type(record) is not dict for record in records):
        raise PerformanceSerializationError(
            "Every preflight probe record must be an object."
        )
    manifest_json = canonical_json_bytes(payload["manifest"])
    records_jsonl_bytes = b"\n".join(
        canonical_json_bytes(record) for record in records
    )
    return parse_probe_run(
        manifest_json,
        records_jsonl_bytes,
        expected_execution_id=_require_str(
            payload["execution_id"],
            "preflight execution_id",
        ),
        expected_records_sha256=_require_sha256(
            payload["records_sha256"],
            "preflight records_sha256",
        ),
    )


def parse_probe_run(
    manifest_json: bytes,
    records_jsonl_bytes: bytes,
    *,
    expected_execution_id: str,
    expected_records_sha256: str,
) -> ProbeRun:
    """Reconstruct and validate a run against independently supplied bindings."""

    manifest = parse_probe_manifest(manifest_json)
    _require_sha256(expected_records_sha256, "expected_records_sha256")
    if type(expected_execution_id) is not str:
        raise PerformanceSerializationError("expected_execution_id must be a string.")
    records_payloads = _load_canonical_jsonl(
        records_jsonl_bytes,
        artifact_name="probe records",
    )
    records = tuple(_parse_probe_record(payload) for payload in records_payloads)
    canonical_records = records_jsonl(records).encode("utf-8")
    if not hmac.compare_digest(canonical_records, records_jsonl_bytes):
        raise PerformanceSerializationError(
            "Probe records are not in canonical execution order."
        )
    actual_records_sha256 = hashlib.sha256(records_jsonl_bytes).hexdigest()
    if not hmac.compare_digest(
        actual_records_sha256,
        expected_records_sha256,
    ):
        raise PerformanceSerializationError(
            "Probe record artifact hash does not match the expected hash."
        )
    probe_run = ProbeRun(
        execution_id=expected_execution_id,
        manifest=manifest,
        records_sha256=actual_records_sha256,
        records=records,
    )
    try:
        validate_probe_run(probe_run)
    except ProbeEvidenceError as exc:
        raise PerformanceSerializationError(
            "Persisted probe run failed whole-run validation."
        ) from exc
    return probe_run


def serialize_performance_receipt(
    receipt: PerformanceExecutionReceipt,
) -> bytes:
    """Return canonical bytes for an independently revalidated receipt."""

    if type(receipt) is not PerformanceExecutionReceipt:
        raise TypeError("receipt must be a PerformanceExecutionReceipt.")
    try:
        verified = validate_performance_receipt(receipt)
    except PerformanceReceiptIntegrityError as exc:
        raise PerformanceSerializationError(
            "Performance receipt integrity validation failed."
        ) from exc
    return canonical_json_bytes(verified.model_dump(mode="json"))


def parse_performance_receipt(data: bytes) -> PerformanceExecutionReceipt:
    """Parse a canonical receipt and rerun its derived-identity validation."""

    _load_canonical_object(data, artifact_name="performance receipt")
    try:
        receipt = PerformanceExecutionReceipt.model_validate_json(
            data,
            strict=True,
        )
        return validate_performance_receipt(receipt)
    except (ValidationError, PerformanceReceiptIntegrityError) as exc:
        raise PerformanceSerializationError(
            "Performance receipt failed strict integrity validation."
        ) from exc


def serialize_performance_verdict(
    verdict: PerformanceCriterionVerdict,
) -> bytes:
    """Serialize a calculation projection that carries no verdict authority."""

    if type(verdict) is not PerformanceCriterionVerdict:
        raise TypeError("verdict must be a PerformanceCriterionVerdict.")
    payload = _verdict_payload(verdict)
    serialized = canonical_json_bytes(payload)
    parse_performance_verdict_display(serialized)
    return serialized


def parse_performance_verdict_display(
    data: bytes,
) -> PerformanceVerdictDisplay:
    """Parse persisted verdict bytes into a display-only type."""

    payload = _load_canonical_object(
        data,
        artifact_name="performance verdict projection",
    )
    _require_exact_fields(
        payload,
        _VERDICT_FIELDS,
        "performance verdict projection",
    )
    ttft = _require_object(payload["ttft_p95"], "ttft_p95")
    error_rate = _require_object(payload["error_rate"], "error_rate")
    _require_exact_fields(ttft, _TTFT_FIELDS, "ttft_p95")
    _require_exact_fields(error_rate, _ERROR_RATE_FIELDS, "error_rate")
    limitations = _require_list(payload["limitations"], "limitations")
    return PerformanceVerdictDisplay(
        criterion_id=_require_str(
            payload["criterion_id"],
            "criterion_id",
        ),
        verdict=_require_verdict_text(payload["verdict"], "verdict"),
        attempted_count=_require_nonnegative_int(
            payload["attempted_count"],
            "attempted_count",
        ),
        successful_count=_require_nonnegative_int(
            payload["successful_count"],
            "successful_count",
        ),
        error_count=_require_nonnegative_int(
            payload["error_count"],
            "error_count",
        ),
        ttft_p95=TTFTP95Display(
            verdict=_require_verdict_text(
                ttft["verdict"],
                "ttft_p95.verdict",
            ),
            observed_ns=_require_optional_nonnegative_int(
                ttft["observed_ns"],
                "ttft_p95.observed_ns",
            ),
            threshold_ns=_require_optional_nonnegative_int(
                ttft["threshold_ns"],
                "ttft_p95.threshold_ns",
            ),
            operator=_require_literal(
                ttft["operator"],
                {"lt", "lte"},
                "ttft_p95.operator",
            ),
            successful_samples=_require_nonnegative_int(
                ttft["successful_samples"],
                "ttft_p95.successful_samples",
            ),
            minimum_successful_samples=_require_nonnegative_int(
                ttft["minimum_successful_samples"],
                "ttft_p95.minimum_successful_samples",
            ),
            reason=_require_nonempty_str(
                ttft["reason"],
                "ttft_p95.reason",
            ),
        ),
        error_rate=ErrorRateDisplay(
            verdict=_require_verdict_text(
                error_rate["verdict"],
                "error_rate.verdict",
            ),
            error_count=_require_nonnegative_int(
                error_rate["error_count"],
                "error_rate.error_count",
            ),
            attempted_count=_require_nonnegative_int(
                error_rate["attempted_count"],
                "error_rate.attempted_count",
            ),
            observed_rate=_require_optional_decimal_text(
                error_rate["observed_rate"],
                "error_rate.observed_rate",
            ),
            threshold=_require_decimal_text(
                error_rate["threshold"],
                "error_rate.threshold",
            ),
            operator=_require_literal(
                error_rate["operator"],
                {"lt"},
                "error_rate.operator",
            ),
            minimum_attempts=_require_nonnegative_int(
                error_rate["minimum_attempts"],
                "error_rate.minimum_attempts",
            ),
            reason=_require_nonempty_str(
                error_rate["reason"],
                "error_rate.reason",
            ),
        ),
        calculation_version=_require_literal(
            payload["calculation_version"],
            {CALCULATION_VERSION},
            "calculation_version",
        ),
        reason=_require_nonempty_str(payload["reason"], "reason"),
        limitations=tuple(
            _require_nonempty_str(value, "limitation") for value in limitations
        ),
    )


def recompute_and_compare_performance_verdict(
    persisted: PerformanceVerdictDisplay,
    criterion: InferencePerformanceCriterion,
    probe_run: ProbeRun,
) -> PerformanceCriterionVerdict:
    """Authorize no bytes; recompute, compare, and return the fresh verdict."""

    if type(persisted) is not PerformanceVerdictDisplay:
        raise TypeError("persisted must be a PerformanceVerdictDisplay.")
    if type(criterion) is not InferencePerformanceCriterion:
        raise TypeError("criterion must be an InferencePerformanceCriterion.")
    try:
        validate_probe_run(probe_run)
    except ProbeEvidenceError as exc:
        raise PerformanceSerializationError(
            "Probe run is invalid; verdict cannot be recomputed."
        ) from exc
    recomputed = evaluate_performance_criterion(criterion, probe_run)
    expected = canonical_json_bytes(_verdict_payload(recomputed))
    actual = canonical_json_bytes(_display_payload(persisted))
    if not hmac.compare_digest(expected, actual):
        raise PerformanceSerializationError(
            "Persisted performance verdict does not match independent recomputation."
        )
    return recomputed


def _parse_probe_record(payload: dict[str, Any]) -> ProbeRecord:
    _require_exact_fields(payload, _RECORD_FIELDS, "probe record")
    http_status = _require_optional_int(payload["http_status"], "http_status")
    ttft_ns = _require_optional_nonnegative_int(payload["ttft_ns"], "ttft_ns")
    try:
        phase = ProbePhase(_require_str(payload["phase"], "phase"))
        outcome = ProbeOutcome(_require_str(payload["outcome"], "outcome"))
    except ValueError as exc:
        raise PerformanceSerializationError(
            "Probe record contains a malformed enum."
        ) from exc
    return ProbeRecord(
        schema_version=_require_str(
            payload["schema_version"],
            "schema_version",
        ),
        execution_id=_require_str(
            payload["execution_id"],
            "execution_id",
        ),
        manifest_sha256=_require_sha256(
            payload["manifest_sha256"],
            "manifest_sha256",
        ),
        request_id=_require_str(payload["request_id"], "request_id"),
        phase=phase,
        ordinal=_require_int(payload["ordinal"], "ordinal"),
        included_in_measurement=_require_bool(
            payload["included_in_measurement"],
            "included_in_measurement",
        ),
        prompt_id=_require_str(payload["prompt_id"], "prompt_id"),
        prompt_sha256=_require_sha256(
            payload["prompt_sha256"],
            "prompt_sha256",
        ),
        outcome=outcome,
        http_status=http_status,
        ttft_ns=ttft_ns,
        duration_ns=_require_nonnegative_int(
            payload["duration_ns"],
            "duration_ns",
        ),
    )


def _validate_manifest(manifest: ProbeManifest) -> None:
    if manifest.schema_version != PROBE_SCHEMA_VERSION:
        raise PerformanceSerializationError(
            "Probe manifest schema version is unsupported."
        )
    if (
        manifest.first_token_definition != "first_nonempty_choices_delta_content_v1"
        or manifest.warmup_included_in_measurement is not False
    ):
        raise PerformanceSerializationError(
            "Probe manifest measurement semantics are invalid."
        )
    try:
        ProbeConfig(
            endpoint=manifest.endpoint,
            model=manifest.model,
            request_count=manifest.request_count,
            concurrency=manifest.concurrency,
            warmup_count=manifest.warmup_count,
            timeout_seconds=manifest.timeout_seconds,
            max_tokens=manifest.max_tokens,
            max_stream_bytes=manifest.max_stream_bytes,
        )
    except ValueError as exc:
        raise PerformanceSerializationError(
            "Probe manifest workload bounds are invalid."
        ) from exc
    if not manifest.prompts:
        raise PerformanceSerializationError("Probe manifest prompt set is empty.")
    descriptor_payload = []
    prompt_ids: set[str] = set()
    for descriptor in manifest.prompts:
        if type(descriptor) is not PromptDescriptor:
            raise PerformanceSerializationError(
                "Probe manifest prompt descriptor type is invalid."
            )
        if (
            type(descriptor.prompt_id) is not str
            or not _PROMPT_ID.fullmatch(descriptor.prompt_id)
            or descriptor.prompt_id in prompt_ids
        ):
            raise PerformanceSerializationError(
                "Probe manifest prompt identity is invalid or duplicated."
            )
        prompt_ids.add(descriptor.prompt_id)
        descriptor_payload.append(descriptor.to_dict())
    prompt_set_hash = hashlib.sha256(
        canonical_json_bytes(descriptor_payload)
    ).hexdigest()
    if not hmac.compare_digest(
        prompt_set_hash,
        manifest.prompt_set_sha256,
    ):
        raise PerformanceSerializationError(
            "Probe manifest prompt-set hash is invalid."
        )
    identity = manifest.to_dict()
    identity.pop("manifest_sha256")
    manifest_hash = hashlib.sha256(canonical_json_bytes(identity)).hexdigest()
    if not hmac.compare_digest(manifest_hash, manifest.manifest_sha256):
        raise PerformanceSerializationError("Probe manifest identity hash is invalid.")


def _verdict_payload(
    verdict: PerformanceCriterionVerdict,
) -> dict[str, Any]:
    if type(verdict.ttft_p95) is not TTFTP95RuleResult:
        raise PerformanceSerializationError("TTFT calculation type is invalid.")
    if type(verdict.error_rate) is not ErrorRateRuleResult:
        raise PerformanceSerializationError("Error-rate calculation type is invalid.")
    return {
        "attempted_count": verdict.attempted_count,
        "calculation_version": verdict.calculation_version,
        "criterion_id": verdict.criterion_id,
        "error_count": verdict.error_count,
        "error_rate": {
            "attempted_count": verdict.error_rate.attempted_count,
            "error_count": verdict.error_rate.error_count,
            "minimum_attempts": verdict.error_rate.minimum_attempts,
            "observed_rate": (
                None
                if verdict.error_rate.observed_rate is None
                else str(verdict.error_rate.observed_rate)
            ),
            "operator": verdict.error_rate.operator,
            "reason": verdict.error_rate.reason,
            "threshold": str(verdict.error_rate.threshold),
            "verdict": _enum_text(verdict.error_rate.verdict),
        },
        "limitations": list(verdict.limitations),
        "reason": verdict.reason,
        "successful_count": verdict.successful_count,
        "ttft_p95": {
            "minimum_successful_samples": (verdict.ttft_p95.minimum_successful_samples),
            "observed_ns": verdict.ttft_p95.observed_ns,
            "operator": verdict.ttft_p95.operator,
            "reason": verdict.ttft_p95.reason,
            "successful_samples": verdict.ttft_p95.successful_samples,
            "threshold_ns": verdict.ttft_p95.threshold_ns,
            "verdict": _enum_text(verdict.ttft_p95.verdict),
        },
        "verdict": _enum_text(verdict.verdict),
    }


def _display_payload(
    display: PerformanceVerdictDisplay,
) -> dict[str, Any]:
    return {
        "attempted_count": display.attempted_count,
        "calculation_version": display.calculation_version,
        "criterion_id": display.criterion_id,
        "error_count": display.error_count,
        "error_rate": {
            "attempted_count": display.error_rate.attempted_count,
            "error_count": display.error_rate.error_count,
            "minimum_attempts": display.error_rate.minimum_attempts,
            "observed_rate": display.error_rate.observed_rate,
            "operator": display.error_rate.operator,
            "reason": display.error_rate.reason,
            "threshold": display.error_rate.threshold,
            "verdict": display.error_rate.verdict,
        },
        "limitations": list(display.limitations),
        "reason": display.reason,
        "successful_count": display.successful_count,
        "ttft_p95": {
            "minimum_successful_samples": (display.ttft_p95.minimum_successful_samples),
            "observed_ns": display.ttft_p95.observed_ns,
            "operator": display.ttft_p95.operator,
            "reason": display.ttft_p95.reason,
            "successful_samples": display.ttft_p95.successful_samples,
            "threshold_ns": display.ttft_p95.threshold_ns,
            "verdict": display.ttft_p95.verdict,
        },
        "verdict": display.verdict,
    }


def _load_canonical_object(
    data: bytes,
    *,
    artifact_name: str,
) -> dict[str, Any]:
    value = _load_json(
        data,
        artifact_name=artifact_name,
        maximum_bytes=_MAX_JSON_BYTES,
    )
    if type(value) is not dict:
        raise PerformanceSerializationError(
            "{0} must be a JSON object.".format(artifact_name)
        )
    return value


def _load_canonical_jsonl(
    data: bytes,
    *,
    artifact_name: str,
) -> tuple[dict[str, Any], ...]:
    _require_bytes(data, artifact_name, _MAX_JSONL_BYTES)
    if not data or data.startswith(b"\xef\xbb\xbf") or data.endswith(b"\n"):
        raise PerformanceSerializationError(
            "{0} must be non-empty canonical JSONL with no trailing newline.".format(
                artifact_name
            )
        )
    lines = data.split(b"\n")
    values: list[dict[str, Any]] = []
    for index, line in enumerate(lines, start=1):
        if not line:
            raise PerformanceSerializationError(
                "{0} contains an empty line.".format(artifact_name)
            )
        value = _load_json(
            line,
            artifact_name="{0} line {1}".format(artifact_name, index),
            maximum_bytes=_MAX_JSON_BYTES,
        )
        if type(value) is not dict:
            raise PerformanceSerializationError(
                "{0} line {1} must be a JSON object.".format(
                    artifact_name,
                    index,
                )
            )
        values.append(value)
    return tuple(values)


def _load_json(
    data: bytes,
    *,
    artifact_name: str,
    maximum_bytes: int,
) -> Any:
    _require_bytes(data, artifact_name, maximum_bytes)
    if data.startswith(b"\xef\xbb\xbf"):
        raise PerformanceSerializationError(
            "{0} must not contain a UTF-8 BOM.".format(artifact_name)
        )
    try:
        value = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=_reject_nonfinite_constant,
        )
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        PerformanceSerializationError,
    ) as exc:
        if isinstance(exc, PerformanceSerializationError):
            raise
        raise PerformanceSerializationError(
            "{0} is not valid UTF-8 JSON.".format(artifact_name)
        ) from exc
    try:
        canonical = canonical_json_bytes(value)
    except (CanonicalizationError, TypeError, ValueError) as exc:
        raise PerformanceSerializationError(
            "{0} is outside canonical JSON.".format(artifact_name)
        ) from exc
    if not hmac.compare_digest(canonical, data):
        raise PerformanceSerializationError(
            "{0} is not RFC 8785 canonical JSON.".format(artifact_name)
        )
    return value


def _reject_duplicate_pairs(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PerformanceSerializationError(
                "JSON contains a duplicate object field."
            )
        result[key] = value
    return result


def _reject_nonfinite_constant(value: str) -> Any:
    raise PerformanceSerializationError(
        "JSON contains a non-finite number: {0}.".format(value)
    )


def _require_bytes(data: bytes, name: str, maximum: int) -> None:
    if type(data) is not bytes:
        raise TypeError("{0} must be bytes.".format(name))
    if not data or len(data) > maximum:
        raise PerformanceSerializationError(
            "{0} size is outside the allowed range.".format(name)
        )


def _require_exact_fields(
    value: dict[str, Any],
    expected: frozenset[str],
    name: str,
) -> None:
    actual = frozenset(value)
    if actual != expected:
        raise PerformanceSerializationError(
            "{0} fields do not match the strict schema.".format(name)
        )


def _require_object(value: Any, name: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise PerformanceSerializationError("{0} must be a JSON object.".format(name))
    return value


def _require_list(value: Any, name: str) -> list[Any]:
    if type(value) is not list:
        raise PerformanceSerializationError("{0} must be a JSON array.".format(name))
    return value


def _require_str(value: Any, name: str) -> str:
    if type(value) is not str:
        raise PerformanceSerializationError("{0} must be a string.".format(name))
    return value


def _require_nonempty_str(value: Any, name: str) -> str:
    text = _require_str(value, name)
    if not text:
        raise PerformanceSerializationError("{0} must not be empty.".format(name))
    return text


def _require_bool(value: Any, name: str) -> bool:
    if type(value) is not bool:
        raise PerformanceSerializationError("{0} must be a boolean.".format(name))
    return value


def _require_int(value: Any, name: str) -> int:
    if type(value) is not int:
        raise PerformanceSerializationError("{0} must be an integer.".format(name))
    return value


def _require_nonnegative_int(value: Any, name: str) -> int:
    integer = _require_int(value, name)
    if integer < 0:
        raise PerformanceSerializationError("{0} must be non-negative.".format(name))
    return integer


def _require_optional_int(value: Any, name: str) -> int | None:
    return None if value is None else _require_int(value, name)


def _require_optional_nonnegative_int(
    value: Any,
    name: str,
) -> int | None:
    return None if value is None else _require_nonnegative_int(value, name)


def _require_sha256(value: Any, name: str) -> str:
    text = _require_str(value, name)
    if len(text) != _SHA256_LENGTH or any(
        character not in "0123456789abcdef" for character in text
    ):
        raise PerformanceSerializationError(
            "{0} must be a lowercase SHA-256 digest.".format(name)
        )
    return text


def _require_literal(
    value: Any,
    allowed: set[str],
    name: str,
) -> str:
    text = _require_str(value, name)
    if text not in allowed:
        raise PerformanceSerializationError(
            "{0} contains an unsupported value.".format(name)
        )
    return text


def _require_verdict_text(value: Any, name: str) -> str:
    text = _require_str(value, name)
    try:
        VerdictStatus(text)
    except ValueError as exc:
        raise PerformanceSerializationError(
            "{0} contains a malformed verdict enum.".format(name)
        ) from exc
    return text


def _require_decimal_text(value: Any, name: str) -> str:
    text = _require_str(value, name)
    try:
        decimal_value = Decimal(text)
    except InvalidOperation as exc:
        raise PerformanceSerializationError(
            "{0} must be an exact decimal string.".format(name)
        ) from exc
    if not decimal_value.is_finite() or str(decimal_value) != text:
        raise PerformanceSerializationError(
            "{0} must be a canonical finite decimal string.".format(name)
        )
    return text


def _require_optional_decimal_text(
    value: Any,
    name: str,
) -> str | None:
    return None if value is None else _require_decimal_text(value, name)


def _enum_text(value: Enum) -> str:
    if not isinstance(value, Enum) or type(value.value) is not str:
        raise PerformanceSerializationError("Verdict enum is invalid.")
    return value.value


__all__ = [
    "ErrorRateDisplay",
    "PerformanceSerializationError",
    "PerformanceVerdictDisplay",
    "TTFTP95Display",
    "parse_confirmation",
    "parse_contract",
    "parse_performance_receipt",
    "parse_performance_verdict_display",
    "parse_probe_manifest",
    "parse_probe_run",
    "parse_probe_run_envelope",
    "recompute_and_compare_performance_verdict",
    "serialize_confirmation",
    "serialize_contract",
    "serialize_performance_receipt",
    "serialize_performance_verdict",
    "serialize_probe_manifest",
    "serialize_probe_records_jsonl",
    "serialize_probe_run",
    "serialize_probe_run_envelope",
]
