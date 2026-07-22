"""Contract lifecycle, canonicalization, and digest functions."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Dict, Optional

from .models import ContractStatus, POCContract


_ALLOWED_TRANSITIONS = {
    ContractStatus.DRAFT: {ContractStatus.IN_REVIEW},
    ContractStatus.IN_REVIEW: {ContractStatus.APPROVED},
    ContractStatus.APPROVED: set(),
    ContractStatus.FROZEN: {ContractStatus.SUPERSEDED},
    ContractStatus.SUPERSEDED: set(),
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def canonical_contract_payload(contract: POCContract) -> Dict[str, object]:
    """Return the typed contract payload that participates in its digest."""

    return contract.model_dump(mode="json", exclude={"canonical_hash"})


def canonical_contract_bytes(contract: POCContract) -> bytes:
    """Serialize a contract deterministically for hashing and inspection."""

    return json.dumps(
        canonical_contract_payload(contract),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def contract_digest(contract: POCContract) -> str:
    return hashlib.sha256(canonical_contract_bytes(contract)).hexdigest()


def verify_contract_digest(contract: POCContract) -> bool:
    return bool(contract.canonical_hash) and contract.canonical_hash == contract_digest(
        contract
    )


def _validated_copy(contract: POCContract, updates: Dict[str, object]) -> POCContract:
    payload = contract.model_dump(mode="python")
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
    """Freeze an approved contract and attach a canonical SHA-256 digest."""

    if contract.status != ContractStatus.APPROVED:
        raise ValueError("Only an approved contract can be frozen.")

    timestamp = frozen_at or utc_now()
    frozen_without_digest = _validated_copy(
        contract,
        {
            "status": ContractStatus.FROZEN,
            "frozen_at": timestamp,
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
            "criteria": criteria,
            "parent_version": "{0}@{1}".format(contract.id, contract.version),
            "canonical_hash": None,
        }
    )
