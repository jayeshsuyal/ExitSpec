"""Authorization boundary for managed external-evidence contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from .confirmations import (
    ContractConfirmation,
    require_affirmative_confirmation,
)
from .contracts import verify_contract_digest
from .models import (
    ContractStatus,
    InferencePerformanceCriterionV3,
    POCContract,
)


MANAGED_CONTRACT_CONTEXT_VERSION: Final = (
    "exitspec.inferdrome-managed-contract-context.v1"
)
MANAGED_TARGET_PROVIDER: Final = "inferdrome-managed-vllm"
MANAGED_TARGET_ENDPOINT_CLASS: Final = "retained-loopback-vllm-benchmark"


class InferdromeManagedContextError(ValueError):
    """A contract and confirmation cannot authorize managed evidence review."""


@dataclass(frozen=True, slots=True)
class ValidatedManagedContractContext:
    """One frozen customer rule authorized for external receipt binding."""

    contract: POCContract
    confirmation: ContractConfirmation
    criterion: InferencePerformanceCriterionV3
    context_version: str = MANAGED_CONTRACT_CONTEXT_VERSION


def validate_managed_contract_context(
    contract: POCContract,
    confirmation: ContractConfirmation,
) -> ValidatedManagedContractContext:
    """Require a digest-valid, customer-confirmed v3 external criterion."""

    if type(contract) is not POCContract:
        raise InferdromeManagedContextError("A typed POC contract is required.")
    if type(confirmation) is not ContractConfirmation:
        raise InferdromeManagedContextError(
            "A typed customer confirmation is required."
        )
    if contract.status != ContractStatus.FROZEN or not verify_contract_digest(contract):
        raise InferdromeManagedContextError(
            "Managed external evidence requires a digest-valid frozen contract."
        )
    try:
        require_affirmative_confirmation(contract, confirmation)
    except ValueError as error:
        raise InferdromeManagedContextError(
            "Managed external evidence requires the matching affirmative confirmation."
        ) from error
    if contract.confirmation_id != confirmation.confirmation_id:
        raise InferdromeManagedContextError(
            "Frozen contract is bound to a different confirmation record."
        )
    criteria = tuple(
        criterion
        for criterion in contract.criteria
        if isinstance(criterion, InferencePerformanceCriterionV3)
    )
    if len(criteria) != 1 or len(contract.criteria) != 1:
        raise InferdromeManagedContextError(
            "Managed external receipt binding requires exactly one v3 criterion."
        )
    criterion = criteria[0]
    identity = criterion.evidence_identity
    if not criterion.approved:
        raise InferdromeManagedContextError(
            "Managed external criterion must be explicitly approved."
        )
    if (
        contract.target_system.provider != MANAGED_TARGET_PROVIDER
        or contract.target_system.endpoint_class != MANAGED_TARGET_ENDPOINT_CLASS
        or contract.target_system.model != identity.target_model
        or contract.workload.sha256 != identity.workload_digest.removeprefix("sha256:")
    ):
        raise InferdromeManagedContextError(
            "Contract target or workload reference disagrees with its evidence identity."
        )
    if (
        criterion.evidence_identity.exact_measured_attempts != 100
        or criterion.error_rate.exact_attempts != 100
        or criterion.ttft_p95.minimum_successful_samples != 100
    ):
        raise InferdromeManagedContextError(
            "Managed v1 acceptance requires exactly 100 attempts, 100 successful "
            "TTFT samples, and the strict below-threshold rule."
        )
    return ValidatedManagedContractContext(
        contract=contract,
        confirmation=confirmation,
        criterion=criterion,
    )


__all__ = [
    "InferdromeManagedContextError",
    "MANAGED_CONTRACT_CONTEXT_VERSION",
    "MANAGED_TARGET_ENDPOINT_CLASS",
    "MANAGED_TARGET_PROVIDER",
    "ValidatedManagedContractContext",
    "validate_managed_contract_context",
]
