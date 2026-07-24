from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from exitspec.confirmations import (
    ConfirmationDecision,
    ContractConfirmation,
    confirmation_matches_contract,
    contract_confirmation_fingerprint,
    record_confirmation,
)
from exitspec.contracts import (
    freeze_confirmed_contract,
    revise_contract,
    verify_contract_digest,
)
from exitspec.models import VerdictStatus


FIXED_TIME = datetime(2026, 7, 23, 16, 30, tzinfo=timezone.utc)


def make_confirmation(
    contract,
    *,
    decision=ConfirmationDecision.CONFIRM,
    idempotency_key="confirm-support-agent-v1",
):
    return record_confirmation(
        contract,
        confirmer_identity="customer@example.com",
        decision=decision,
        rationale="These requirements match the customer agreement.",
        idempotency_key=idempotency_key,
        decided_at=FIXED_TIME,
    )


def test_affirmative_confirmation_allows_exact_contract_to_freeze(
    approved_contract,
):
    confirmation = make_confirmation(approved_contract)

    frozen = freeze_confirmed_contract(
        approved_contract,
        confirmation,
        FIXED_TIME,
    )

    assert frozen.frozen_at == FIXED_TIME
    assert frozen.confirmation_id == confirmation.confirmation_id
    assert frozen.canonical_hash
    assert verify_contract_digest(frozen)
    assert confirmation_matches_contract(approved_contract, confirmation)


def test_unconfirmed_contract_cannot_freeze(approved_contract):
    with pytest.raises(ValueError, match="affirmative customer confirmation"):
        freeze_confirmed_contract(approved_contract)


def test_confirmation_for_wrong_contract_version_cannot_freeze(
    approved_contract,
):
    confirmation = make_confirmation(approved_contract)
    wrong_version = approved_contract.model_copy(update={"version": "0.2.0"})

    with pytest.raises(ValueError, match="different contract id or version"):
        freeze_confirmed_contract(
            wrong_version,
            confirmation,
            FIXED_TIME,
        )


def test_confirmation_with_wrong_content_fingerprint_cannot_freeze(
    approved_contract,
):
    confirmation = make_confirmation(approved_contract)
    changed_contract = approved_contract.model_copy(
        update={"use_case": "A meaningfully changed use case"}
    )

    assert (
        contract_confirmation_fingerprint(changed_contract)
        != confirmation.contract_fingerprint
    )
    with pytest.raises(ValueError, match="fingerprint does not match"):
        freeze_confirmed_contract(
            changed_contract,
            confirmation,
            FIXED_TIME,
        )


def test_request_changes_decision_cannot_freeze(approved_contract):
    confirmation = make_confirmation(
        approved_contract,
        decision=ConfirmationDecision.REQUEST_CHANGES,
    )

    with pytest.raises(ValueError, match="requested changes"):
        freeze_confirmed_contract(
            approved_contract,
            confirmation,
            FIXED_TIME,
        )


def test_revision_invalidates_prior_confirmation_without_mutating_history(
    approved_contract,
):
    confirmation = make_confirmation(approved_contract)
    frozen = freeze_confirmed_contract(
        approved_contract,
        confirmation,
        FIXED_TIME,
    )

    revised = revise_contract(
        frozen,
        new_id="support-agent-tool-selection-v2",
        new_version="0.2.0",
        created_at=FIXED_TIME + timedelta(minutes=1),
    )

    assert not confirmation_matches_contract(revised, confirmation)
    assert revised.confirmation_id is None
    assert frozen.canonical_hash
    assert verify_contract_digest(frozen)


def test_confirmation_record_is_immutable(approved_contract):
    confirmation = make_confirmation(approved_contract)

    with pytest.raises(ValidationError, match="Instance is frozen"):
        confirmation.rationale = "Changed after the decision"


def test_identical_retry_returns_original_confirmation(approved_contract):
    original = make_confirmation(approved_contract)

    replayed = record_confirmation(
        approved_contract,
        confirmer_identity=original.confirmer_identity,
        decision=original.decision,
        rationale=original.rationale,
        idempotency_key=original.idempotency_key,
        decided_at=FIXED_TIME + timedelta(minutes=5),
        existing=original,
    )

    assert replayed is original
    assert replayed.decided_at == FIXED_TIME


def test_conflicting_idempotency_key_reuse_is_rejected(approved_contract):
    original = make_confirmation(approved_contract)

    with pytest.raises(ValueError, match="different confirmation"):
        record_confirmation(
            approved_contract,
            confirmer_identity=original.confirmer_identity,
            decision=ConfirmationDecision.REQUEST_CHANGES,
            rationale=original.rationale,
            idempotency_key=original.idempotency_key,
            existing=original,
        )


@pytest.mark.parametrize(
    ("field_name", "field_value"),
    [
        ("verdict", VerdictStatus.PASS),
        ("provider_policy", "prefer-provider-output"),
        ("authority", "production-launch"),
    ],
)
def test_confirmation_rejects_verdict_provider_and_authority_fields(
    approved_contract,
    field_name,
    field_value,
):
    confirmation = make_confirmation(approved_contract)
    payload = confirmation.model_dump(mode="python")
    payload[field_name] = field_value

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ContractConfirmation.model_validate(payload)
