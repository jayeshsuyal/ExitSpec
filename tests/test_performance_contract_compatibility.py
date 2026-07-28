from __future__ import annotations

import hashlib
from pathlib import Path

from exitspec.confirmations import contract_confirmation_fingerprint
from exitspec.contracts import contract_digest, verify_contract_digest
from exitspec.models import Criterion
from exitspec.runner import load_contract


PROJECT_ROOT = Path(__file__).resolve().parents[1]
APPROVED_CONTRACT_PATH = (
    PROJECT_ROOT / "examples/support-agent/contracts/tool-selection-v1.yaml"
)
FROZEN_CONTRACT_PATH = (
    PROJECT_ROOT / "examples/support-agent/contracts/tool-selection-v1.frozen.yaml"
)
EXPECTED_CONTRACT_HASH = (
    "05d6e360411a2b0c65d003e8ec63bd0429592d72034b98c7327095f66e54bed1"
)
EXPECTED_FROZEN_YAML_SHA256 = (
    "e15073ce7e94f793e83343432b15c3a1dc141e250a8a5c107253597cc814edcf"
)
EXPECTED_CONFIRMATION_FINGERPRINT = (
    "c070ece7ce34275131d3b4bf6a02e3c8eb85d41240254dd7ded08fc938eef53e"
)
EXPECTED_CONFIRMATION_ID = (
    "cnf_f71b669eb70d58452d425198475435c56029d74abbdc97d84aed7610910bc53c"
)


def test_legacy_criterion_serialization_is_byte_shape_compatible():
    contract = load_contract(APPROVED_CONTRACT_PATH)
    criterion_payload = contract.model_dump(mode="json")["criteria"][0]

    assert isinstance(contract.criteria[0], Criterion)
    assert "criterion_type" not in criterion_payload
    assert Criterion.model_validate(criterion_payload).model_dump(
        mode="json"
    ) == criterion_payload


def test_existing_frozen_contract_keeps_its_exact_canonical_hash():
    contract = load_contract(FROZEN_CONTRACT_PATH)

    assert contract.canonical_hash == EXPECTED_CONTRACT_HASH
    assert contract_digest(contract) == EXPECTED_CONTRACT_HASH
    assert verify_contract_digest(contract)
    assert isinstance(contract.criteria[0], Criterion)
    assert "criterion_type" not in contract.model_dump(mode="json")["criteria"][0]


def test_existing_frozen_yaml_keeps_its_exact_file_digest():
    assert (
        hashlib.sha256(FROZEN_CONTRACT_PATH.read_bytes()).hexdigest()
        == EXPECTED_FROZEN_YAML_SHA256
    )


def test_existing_confirmation_projection_keeps_its_exact_fingerprint():
    approved = load_contract(APPROVED_CONTRACT_PATH)
    frozen = load_contract(FROZEN_CONTRACT_PATH)

    assert (
        contract_confirmation_fingerprint(approved)
        == EXPECTED_CONFIRMATION_FINGERPRINT
    )
    assert (
        contract_confirmation_fingerprint(frozen)
        == EXPECTED_CONFIRMATION_FINGERPRINT
    )


def test_existing_frozen_contract_keeps_its_exact_confirmation_id():
    contract = load_contract(FROZEN_CONTRACT_PATH)

    assert contract.confirmation_id == EXPECTED_CONFIRMATION_ID
