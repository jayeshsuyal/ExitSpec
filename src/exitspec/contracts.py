"""Contract lifecycle, canonicalization, and digest functions."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Dict, Optional

from .canonical import canonical_json_bytes
from .confirmations import ContractConfirmation, require_affirmative_confirmation
from .models import (
    ContractStatus,
    POCContract,
    RoutingSLOAttainmentCriterionV1,
)


_ALLOWED_TRANSITIONS = {
    ContractStatus.DRAFT: {ContractStatus.IN_REVIEW},
    ContractStatus.IN_REVIEW: {ContractStatus.APPROVED},
    ContractStatus.APPROVED: set(),
    ContractStatus.FROZEN: set(),
    ContractStatus.SUPERSEDED: set(),
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def canonical_contract_payload(contract: POCContract) -> Dict[str, object]:
    """Return the typed contract payload that participates in its digest."""

    validated = _validated_contract_for_full_contract_path(contract)
    return validated.model_dump(mode="json", exclude={"canonical_hash"})


def _validated_contract_for_full_contract_path(contract: POCContract) -> POCContract:
    """Revalidate B10 raw state before a full-contract projection can dump it."""

    raw_state = object.__getattribute__(contract, "__dict__")
    if type(raw_state) is not dict:
        raise ValueError("POCContract raw state must be one object.")
    criteria = raw_state.get("criteria")
    if type(criteria) not in (tuple, list):
        return contract
    has_b10_criterion = False
    for criterion in criteria:
        if type(criterion) is RoutingSLOAttainmentCriterionV1:
            has_b10_criterion = True
            break
        if type(criterion) is dict:
            if criterion.get("criterion_type") == "routing_slo_attainment_v1":
                has_b10_criterion = True
                break
            continue
        try:
            criterion_state = object.__getattribute__(criterion, "__dict__")
        except (AttributeError, TypeError):
            continue
        if (
            type(criterion_state) is dict
            and criterion_state.get("criterion_type") == "routing_slo_attainment_v1"
        ):
            has_b10_criterion = True
            break
    if not has_b10_criterion:
        return contract
    from .routing_qualification import _revalidate_typed_model

    return _revalidate_typed_model(
        contract,
        POCContract,
        label="full POC contract",
    )


def canonical_contract_bytes(contract: POCContract) -> bytes:
    """Serialize a contract with RFC 8785 JCS for hashing and inspection."""

    return canonical_json_bytes(canonical_contract_payload(contract))


def contract_digest(contract: POCContract) -> str:
    return hashlib.sha256(canonical_contract_bytes(contract)).hexdigest()


def verify_contract_digest(contract: POCContract) -> bool:
    return bool(contract.canonical_hash) and contract.canonical_hash == contract_digest(
        contract
    )


def _validated_copy(contract: POCContract, updates: Dict[str, object]) -> POCContract:
    validated = _validated_contract_for_full_contract_path(contract)
    payload = validated.model_dump(mode="python")
    payload.update(updates)
    return POCContract.model_validate(payload)


def transition_contract(
    contract: POCContract,
    target_status: ContractStatus,
    at: Optional[datetime] = None,
) -> POCContract:
    """Advance a draft/review contract through legal non-freeze transitions."""

    if target_status == ContractStatus.FROZEN:
        raise ValueError("Use freeze_contract to create a frozen contract digest.")
    if (
        contract.status == ContractStatus.FROZEN
        and target_status == ContractStatus.SUPERSEDED
    ):
        raise ValueError(
            "Frozen contracts cannot be mutated into SUPERSEDED; "
            "a separate supersession record is required."
        )
    if target_status not in _ALLOWED_TRANSITIONS[contract.status]:
        raise ValueError(
            "Illegal contract transition: {0} -> {1}".format(
                contract.status.value, target_status.value
            )
        )

    timestamp = at or utc_now()
    updates: Dict[str, object] = {"status": target_status}
    if target_status == ContractStatus.APPROVED:
        updates["approved_at"] = timestamp
    return _validated_copy(contract, updates)


def freeze_contract(
    contract: POCContract, frozen_at: Optional[datetime] = None
) -> POCContract:
    """Freeze an approved contract and attach a canonical SHA-256 digest.

    This compatibility primitive predates customer confirmation. New
    user-facing paths must call ``freeze_confirmed_contract`` instead.
    """

    if contract.status != ContractStatus.APPROVED:
        raise ValueError("Only an approved contract can be frozen.")

    timestamp = frozen_at or utc_now()
    frozen_without_digest = _validated_copy(
        contract,
        {
            "status": ContractStatus.FROZEN,
            "frozen_at": timestamp,
            "confirmation_id": None,
            "canonical_hash": None,
        },
    )
    return _validated_copy(
        frozen_without_digest,
        {"canonical_hash": contract_digest(frozen_without_digest)},
    )


def freeze_confirmed_contract(
    contract: POCContract,
    confirmation: Optional[ContractConfirmation] = None,
    frozen_at: Optional[datetime] = None,
) -> POCContract:
    """Freeze only when a customer affirmed this exact contract agreement."""

    if contract.status != ContractStatus.APPROVED:
        raise ValueError("Only an approved contract can be frozen.")
    if confirmation is None:
        raise ValueError("A matching affirmative customer confirmation is required.")
    require_affirmative_confirmation(contract, confirmation)
    timestamp = frozen_at or utc_now()
    frozen_without_digest = _validated_copy(
        contract,
        {
            "status": ContractStatus.FROZEN,
            "frozen_at": timestamp,
            "confirmation_id": confirmation.confirmation_id,
            "canonical_hash": None,
        },
    )
    return _validated_copy(
        frozen_without_digest,
        {"canonical_hash": contract_digest(frozen_without_digest)},
    )


def revise_contract(
    contract: POCContract,
    new_id: str,
    new_version: str,
    created_at: Optional[datetime] = None,
) -> POCContract:
    """Create a new draft from an existing version without mutating history."""

    criteria = []
    for criterion in contract.criteria:
        criteria.append(
            criterion.model_copy(update={"approved": False}).model_dump(mode="python")
        )
    return POCContract.model_validate(
        {
            **contract.model_dump(mode="python"),
            "id": new_id,
            "version": new_version,
            "status": ContractStatus.DRAFT,
            "created_at": created_at or utc_now(),
            "approved_at": None,
            "frozen_at": None,
            "confirmation_id": None,
            "criteria": criteria,
            "parent_version": "{0}@{1}".format(contract.id, contract.version),
            "canonical_hash": None,
        }
    )
