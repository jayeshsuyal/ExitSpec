from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from exitspec.contracts import (
    contract_digest,
    freeze_contract,
    revise_contract,
    transition_contract,
    verify_contract_digest,
)
from exitspec.models import ContractStatus, Criterion


FIXED_TIME = datetime(2026, 7, 22, 16, 30, tzinfo=timezone.utc)


def test_criterion_requires_source_or_human_added(approved_contract):
    payload = approved_contract.criteria[0].model_dump(mode="python")
    payload["source"] = None
    payload["human_added"] = False

    with pytest.raises(ValidationError, match="source reference"):
        Criterion.model_validate(payload)


def test_contract_transition_to_review_then_approval(approved_contract):
    draft = approved_contract.model_copy(
        update={"status": ContractStatus.DRAFT, "approved_at": None}
    )
    review = transition_contract(draft, ContractStatus.IN_REVIEW, FIXED_TIME)
    approved = transition_contract(review, ContractStatus.APPROVED, FIXED_TIME)

    assert review.status == ContractStatus.IN_REVIEW
    assert approved.status == ContractStatus.APPROVED
    assert approved.approved_at == FIXED_TIME


def test_illegal_contract_transition_is_rejected(approved_contract):
    with pytest.raises(ValueError, match="Illegal contract transition"):
        transition_contract(approved_contract, ContractStatus.DRAFT, FIXED_TIME)


def test_freeze_assigns_verifiable_canonical_digest(approved_contract):
    frozen = freeze_contract(approved_contract, FIXED_TIME)

    assert frozen.status == ContractStatus.FROZEN
    assert frozen.frozen_at == FIXED_TIME
    assert frozen.canonical_hash
    assert verify_contract_digest(frozen)


def test_frozen_contract_rejects_top_level_assignment(approved_contract):
    frozen = freeze_contract(approved_contract, FIXED_TIME)

    with pytest.raises(ValidationError, match="Instance is frozen"):
        frozen.customer = "Changed Customer"

    assert frozen.customer == approved_contract.customer
    assert verify_contract_digest(frozen)


def test_frozen_contract_rejects_nested_assignment(approved_contract):
    frozen = freeze_contract(approved_contract, FIXED_TIME)
    criterion = frozen.criteria[0]
    source = criterion.source
    assert source is not None

    with pytest.raises(ValidationError, match="Instance is frozen"):
        criterion.owner = "changed-owner"
    with pytest.raises(ValidationError, match="Instance is frozen"):
        criterion.rule.threshold = 0.5
    with pytest.raises(ValidationError, match="Instance is frozen"):
        source.quote = "Changed source"
    with pytest.raises(ValidationError, match="Instance is frozen"):
        frozen.target_system.model = "changed-model"
    with pytest.raises(ValidationError, match="Instance is frozen"):
        frozen.workload.fixture_path = "changed-fixture.json"

    assert verify_contract_digest(frozen)


def test_frozen_contract_collections_are_immutable_snapshots(approved_contract):
    frozen = freeze_contract(approved_contract, FIXED_TIME)
    original_digest = frozen.canonical_hash

    with pytest.raises(AttributeError):
        frozen.criteria.append(frozen.criteria[0])
    with pytest.raises(TypeError):
        frozen.criteria[0] = frozen.criteria[0]
    with pytest.raises(AttributeError):
        frozen.owners.append("another-owner")
    with pytest.raises(TypeError):
        frozen.owners[0] = "changed-owner"
    with pytest.raises(AttributeError):
        frozen.non_goals.append("Changed non-goal")

    assert frozen.canonical_hash == original_digest
    assert verify_contract_digest(frozen)


def test_contract_collections_keep_json_serialization_shape(approved_contract):
    frozen = freeze_contract(approved_contract, FIXED_TIME)
    payload = frozen.model_dump(mode="json")

    assert isinstance(payload["criteria"], list)
    assert isinstance(payload["owners"], list)
    assert isinstance(payload["non_goals"], list)


def test_frozen_contract_cannot_transition_to_superseded(approved_contract):
    frozen = freeze_contract(approved_contract, FIXED_TIME)

    with pytest.raises(ValueError, match="separate supersession record"):
        transition_contract(frozen, ContractStatus.SUPERSEDED, FIXED_TIME)

    assert frozen.status == ContractStatus.FROZEN
    assert verify_contract_digest(frozen)


def test_changed_frozen_payload_no_longer_matches_digest(approved_contract):
    frozen = freeze_contract(approved_contract, FIXED_TIME)
    changed = frozen.model_copy(update={"customer": "Changed Customer"})

    assert not verify_contract_digest(changed)


def test_semantically_identical_contract_mappings_have_same_digest(
    approved_contract,
):
    payload = approved_contract.model_dump(mode="python")
    reordered = {key: payload[key] for key in reversed(payload)}
    reordered["target_system"] = dict(reversed(payload["target_system"].items()))
    equivalent_contract = type(approved_contract).model_validate(reordered)

    assert contract_digest(equivalent_contract) == contract_digest(approved_contract)


def test_semantic_contract_change_changes_digest(approved_contract):
    changed = approved_contract.model_copy(update={"customer": "Changed Customer"})

    assert contract_digest(changed) != contract_digest(approved_contract)


def test_revision_creates_new_unapproved_draft(approved_contract):
    frozen = freeze_contract(approved_contract, FIXED_TIME)
    revised = revise_contract(
        frozen,
        new_id="support-agent-tool-selection-v2",
        new_version="0.2.0",
        created_at=FIXED_TIME,
    )

    assert revised.status == ContractStatus.DRAFT
    assert revised.parent_version == "support-agent-tool-selection@0.1.0"
    assert revised.canonical_hash is None
    assert all(not criterion.approved for criterion in revised.criteria)
