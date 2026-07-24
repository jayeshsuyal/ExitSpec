"""Immutable customer confirmations bound to an exact contract agreement."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, Optional

from pydantic import Field

from .canonical import canonical_json_bytes
from .models import (
    SHA256_PATTERN,
    ContractStatus,
    FrozenExitSpecModel,
    POCContract,
)


_CONFIRMATION_PAYLOAD_FIELDS = (
    "id",
    "version",
    "customer",
    "use_case",
    "target_system",
    "workload",
    "criteria",
    "owners",
    "non_goals",
    "evidence_retention_policy",
)
_FINGERPRINT_DOMAIN = b"exitspec-contract-confirmation-v1\x00"
_OPERATION_DOMAIN = b"exitspec-confirmation-operation-v1\x00"


class ConfirmationDecision(str, Enum):
    """A customer's decision about the exact proposed POC agreement."""

    CONFIRM = "CONFIRM"
    REQUEST_CHANGES = "REQUEST_CHANGES"


class ContractConfirmation(FrozenExitSpecModel):
    """An immutable decision record; it carries no execution or verdict authority."""

    confirmation_id: str = Field(pattern=r"^cnf_[a-f0-9]{64}$")
    idempotency_key: str = Field(min_length=1, max_length=200)
    contract_id: str = Field(pattern=r"^[a-z][a-z0-9-]{2,63}$")
    contract_version: str = Field(min_length=1)
    contract_fingerprint: str = Field(pattern=SHA256_PATTERN)
    confirmer_identity: str = Field(min_length=1)
    decision: ConfirmationDecision
    decided_at: datetime
    rationale: str = Field(min_length=1)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def canonical_confirmation_payload(contract: POCContract) -> Dict[str, object]:
    """Return only the agreement content a customer confirmation binds."""

    contract_payload = contract.model_dump(mode="json")
    return {
        field_name: contract_payload[field_name]
        for field_name in _CONFIRMATION_PAYLOAD_FIELDS
    }


def contract_confirmation_fingerprint(contract: POCContract) -> str:
    """Fingerprint confirmation-relevant content independently of lifecycle state."""

    payload = canonical_json_bytes(canonical_confirmation_payload(contract))
    return hashlib.sha256(_FINGERPRINT_DOMAIN + payload).hexdigest()


def confirmation_operation_id(
    contract_id: str,
    contract_version: str,
    idempotency_key: str,
) -> str:
    """Create the stable identity used to deduplicate one logical decision."""

    payload = canonical_json_bytes(
        {
            "contract_id": contract_id,
            "contract_version": contract_version,
            "idempotency_key": idempotency_key,
        }
    )
    digest = hashlib.sha256(_OPERATION_DOMAIN + payload).hexdigest()
    return "cnf_{0}".format(digest)


def record_confirmation(
    contract: POCContract,
    *,
    confirmer_identity: str,
    decision: ConfirmationDecision,
    rationale: str,
    idempotency_key: str,
    decided_at: Optional[datetime] = None,
    existing: Optional[ContractConfirmation] = None,
) -> ContractConfirmation:
    """Record or safely replay one customer decision for an approved contract.

    A persistence adapter can look up ``confirmation_id`` and pass the existing
    record on retry. An identical retry returns the original immutable record;
    conflicting reuse of the same idempotency key is rejected.
    """

    if contract.status != ContractStatus.APPROVED:
        raise ValueError("Only an approved contract can receive customer confirmation.")

    normalized_decision = ConfirmationDecision(decision)
    operation_id = confirmation_operation_id(
        contract.id,
        contract.version,
        idempotency_key,
    )
    expected_fields = {
        "confirmation_id": operation_id,
        "idempotency_key": idempotency_key,
        "contract_id": contract.id,
        "contract_version": contract.version,
        "contract_fingerprint": contract_confirmation_fingerprint(contract),
        "confirmer_identity": confirmer_identity,
        "decision": normalized_decision,
        "rationale": rationale,
    }

    if existing is not None:
        existing_fields = existing.model_dump(
            mode="python",
            exclude={"decided_at"},
        )
        if existing_fields != expected_fields:
            raise ValueError(
                "Idempotency key is already bound to a different confirmation."
            )
        return existing

    return ContractConfirmation(
        **expected_fields,
        decided_at=decided_at or utc_now(),
    )


def confirmation_matches_contract(
    contract: POCContract,
    confirmation: ContractConfirmation,
) -> bool:
    """Return whether a record affirms this exact contract agreement."""

    return (
        confirmation.decision == ConfirmationDecision.CONFIRM
        and confirmation.contract_id == contract.id
        and confirmation.contract_version == contract.version
        and confirmation.contract_fingerprint
        == contract_confirmation_fingerprint(contract)
    )


def require_affirmative_confirmation(
    contract: POCContract,
    confirmation: ContractConfirmation,
) -> None:
    """Reject a decision that cannot authorize freezing this exact agreement."""

    if confirmation.decision != ConfirmationDecision.CONFIRM:
        raise ValueError("Customer requested changes; the contract cannot be frozen.")
    if (
        confirmation.contract_id != contract.id
        or confirmation.contract_version != contract.version
    ):
        raise ValueError("Confirmation is bound to a different contract id or version.")
    if confirmation.contract_fingerprint != contract_confirmation_fingerprint(contract):
        raise ValueError(
            "Confirmation fingerprint does not match the current contract content."
        )
