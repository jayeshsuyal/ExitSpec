"""Authoritative cross-binding boundary for performance decisions.

This module is intentionally offline.  It joins already validated contract
context, customer confirmation, probe evidence, and an execution receipt
before deterministic performance evaluation is allowed to run.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from enum import Enum
from typing import Final

from .confirmations import ContractConfirmation
from .models import VerdictStatus
from .performance_evidence import (
    PerformanceEvidenceError,
    ValidatedPerformanceContext,
    require_frozen_confirmed,
)
from .performance_probe import (
    ProbeConfigurationError,
    ProbeEvidenceError,
    ProbeRun,
    build_manifest,
    validate_probe_run,
)
from .performance_receipts import (
    PerformanceExecutionReceipt,
    PerformanceReceiptIntegrityError,
    validate_performance_receipt,
)
from .performance_verdicts import (
    PerformanceCriterionVerdict,
    evaluate_performance_criterion,
)


class PerformanceDecisionErrorCode(str, Enum):
    """Stable runner-facing classifications for untrusted decision inputs."""

    CONTEXT_NOT_AUTHORIZED = "CONTEXT_NOT_AUTHORIZED"
    PROBE_INTEGRITY_INVALID = "PROBE_INTEGRITY_INVALID"
    RECEIPT_INTEGRITY_INVALID = "RECEIPT_INTEGRITY_INVALID"
    CROSS_BINDING_MISMATCH = "CROSS_BINDING_MISMATCH"


class PerformanceDecisionIntegrityError(ValueError):
    """Performance evidence cannot authorize a customer verdict."""

    verdict: Final[VerdictStatus] = VerdictStatus.NOT_PROVEN

    def __init__(
        self,
        code: PerformanceDecisionErrorCode,
        message: str,
    ) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class AuthorizedPerformanceDecision:
    """An immutable verdict whose complete trust chain was cross-validated."""

    receipt: PerformanceExecutionReceipt
    performance_verdict: PerformanceCriterionVerdict


def authorize_performance_decision(
    context: ValidatedPerformanceContext,
    confirmation: ContractConfirmation,
    probe_run: ProbeRun,
    receipt: PerformanceExecutionReceipt,
) -> AuthorizedPerformanceDecision:
    """Authorize deterministic evaluation only after every identity is joined.

    The returned decision says that the supplied receipt, run, and frozen
    agreement form one internally consistent chain.  It does not turn SHA-256
    self-integrity into external authenticity; a runner must still control
    receipt issuance and durable replay protection.
    """

    _require_authorized_context(context, confirmation)
    _require_valid_probe_run(probe_run)
    verified_receipt = _require_valid_receipt(receipt)
    try:
        _require_context_is_self_consistent(context)
        _require_exact_cross_bindings(
            context,
            probe_run,
            verified_receipt,
        )
    except PerformanceDecisionIntegrityError:
        raise
    except (AttributeError, TypeError, ValueError) as error:
        raise PerformanceDecisionIntegrityError(
            PerformanceDecisionErrorCode.CROSS_BINDING_MISMATCH,
            "Performance decision inputs are incomplete or inconsistent.",
        ) from error

    verdict = evaluate_performance_criterion(
        context.criterion,
        probe_run,
    )
    return AuthorizedPerformanceDecision(
        receipt=verified_receipt,
        performance_verdict=verdict,
    )


def _require_authorized_context(
    context: ValidatedPerformanceContext,
    confirmation: ContractConfirmation,
) -> None:
    if type(context) is not ValidatedPerformanceContext:
        raise PerformanceDecisionIntegrityError(
            PerformanceDecisionErrorCode.CONTEXT_NOT_AUTHORIZED,
            "A validated performance context is required.",
        )
    if type(confirmation) is not ContractConfirmation:
        raise PerformanceDecisionIntegrityError(
            PerformanceDecisionErrorCode.CONTEXT_NOT_AUTHORIZED,
            "A matching customer confirmation is required.",
        )
    try:
        require_frozen_confirmed(context, confirmation)
    except PerformanceEvidenceError as error:
        raise PerformanceDecisionIntegrityError(
            PerformanceDecisionErrorCode.CONTEXT_NOT_AUTHORIZED,
            "The performance agreement is not frozen and customer-confirmed.",
        ) from error


def _require_valid_probe_run(probe_run: ProbeRun) -> None:
    if type(probe_run) is not ProbeRun:
        raise PerformanceDecisionIntegrityError(
            PerformanceDecisionErrorCode.PROBE_INTEGRITY_INVALID,
            "A complete typed probe run is required.",
        )
    try:
        validate_probe_run(probe_run)
    except (ProbeEvidenceError, TypeError, ValueError) as error:
        raise PerformanceDecisionIntegrityError(
            PerformanceDecisionErrorCode.PROBE_INTEGRITY_INVALID,
            "Probe evidence integrity validation failed.",
        ) from error


def _require_valid_receipt(
    receipt: PerformanceExecutionReceipt,
) -> PerformanceExecutionReceipt:
    if type(receipt) is not PerformanceExecutionReceipt:
        raise PerformanceDecisionIntegrityError(
            PerformanceDecisionErrorCode.RECEIPT_INTEGRITY_INVALID,
            "A complete typed performance execution receipt is required.",
        )
    try:
        return validate_performance_receipt(receipt)
    except (
        PerformanceReceiptIntegrityError,
        TypeError,
        ValueError,
    ) as error:
        raise PerformanceDecisionIntegrityError(
            PerformanceDecisionErrorCode.RECEIPT_INTEGRITY_INVALID,
            "Performance execution receipt integrity validation failed.",
        ) from error


def _require_context_is_self_consistent(
    context: ValidatedPerformanceContext,
) -> None:
    contract = context.contract
    criterion = context.criterion
    workload = context.workload
    config = context.probe_config

    if criterion not in contract.criteria:
        _cross_binding_error(
            "The performance criterion is not bound to the frozen contract."
        )
    if not hmac.compare_digest(
        context.workload_sha256,
        hashlib.sha256(context.workload_bytes).hexdigest(),
    ):
        _cross_binding_error(
            "The validated workload bytes no longer match their digest."
        )
    if not hmac.compare_digest(
        context.workload_sha256,
        contract.workload.sha256,
    ):
        _cross_binding_error(
            "The validated workload is not bound to the frozen contract."
        )
    if not hmac.compare_digest(
        context.prompt_sha256,
        hashlib.sha256(context.prompt_bytes).hexdigest(),
    ):
        _cross_binding_error(
            "The validated prompt bytes no longer match their digest."
        )
    if not hmac.compare_digest(
        context.prompt_sha256,
        workload.prompt_fixture_sha256,
    ):
        _cross_binding_error(
            "The validated prompts are not bound to the frozen workload."
        )

    aligned_values = (
        (workload.endpoint, config.endpoint),
        (workload.model, config.model),
        (workload.request_count, config.request_count),
        (workload.concurrency, config.concurrency),
        (workload.warmup_count, config.warmup_count),
        (float(workload.timeout_seconds), float(config.timeout_seconds)),
        (workload.max_tokens, config.max_tokens),
        (workload.max_stream_bytes, config.max_stream_bytes),
        (workload.workload_id, criterion.workload_slice),
        (workload.adapter, criterion.adapter),
        (workload.adapter_version, criterion.adapter_version),
        (workload.model, contract.target_system.model),
    )
    if any(left != right for left, right in aligned_values):
        _cross_binding_error(
            "The validated workload, probe configuration, criterion, and "
            "target system are not aligned."
        )

    try:
        derived_manifest = build_manifest(config, context.prompts)
    except (
        ProbeConfigurationError,
        ProbeEvidenceError,
        TypeError,
        ValueError,
    ) as error:
        raise PerformanceDecisionIntegrityError(
            PerformanceDecisionErrorCode.CROSS_BINDING_MISMATCH,
            "The expected probe manifest cannot be independently derived.",
        ) from error
    if derived_manifest != context.expected_manifest:
        _cross_binding_error(
            "The expected manifest does not match the validated probe inputs."
        )


def _require_exact_cross_bindings(
    context: ValidatedPerformanceContext,
    probe_run: ProbeRun,
    receipt: PerformanceExecutionReceipt,
) -> None:
    expected_manifest = context.expected_manifest
    actual_manifest = probe_run.manifest
    contract = context.contract

    if actual_manifest != expected_manifest:
        _cross_binding_error(
            "The actual probe manifest does not exactly match the authorized "
            "manifest."
        )
    if not hmac.compare_digest(
        actual_manifest.manifest_sha256,
        expected_manifest.manifest_sha256,
    ):
        _cross_binding_error(
            "The actual probe manifest hash does not match the authorized hash."
        )
    if contract.canonical_hash is None:
        _cross_binding_error(
            "The frozen contract hash is missing."
        )

    expected_receipt_fields = {
        "contract_id": contract.id,
        "contract_version": contract.version,
        "frozen_contract_hash": contract.canonical_hash,
        "criterion_id": context.criterion.id,
        "expected_manifest_sha256": expected_manifest.manifest_sha256,
        "execution_id": probe_run.execution_id,
        "records_sha256": probe_run.records_sha256,
    }
    for field_name, expected_value in expected_receipt_fields.items():
        actual_value = getattr(receipt, field_name)
        if not hmac.compare_digest(actual_value, expected_value):
            _cross_binding_error(
                "The execution receipt does not bind the exact contract, "
                "criterion, manifest, execution, and record artifact."
            )


def _cross_binding_error(message: str) -> None:
    raise PerformanceDecisionIntegrityError(
        PerformanceDecisionErrorCode.CROSS_BINDING_MISMATCH,
        message,
    )


__all__ = [
    "AuthorizedPerformanceDecision",
    "PerformanceDecisionErrorCode",
    "PerformanceDecisionIntegrityError",
    "authorize_performance_decision",
]
