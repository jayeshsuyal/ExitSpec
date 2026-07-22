from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from exitspec.contracts import (
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


def test_changed_frozen_payload_no_longer_matches_digest(approved_contract):
    frozen = freeze_contract(approved_contract, FIXED_TIME)
    changed = frozen.model_copy(update={"customer": "Changed Customer"})

    assert not verify_contract_digest(changed)


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
