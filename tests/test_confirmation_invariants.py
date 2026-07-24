import hashlib
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from exitspec.confirmations import (
    ConfirmationDecision,
    canonical_confirmation_payload,
    confirmation_matches_contract,
    contract_confirmation_fingerprint,
    record_confirmation,
    require_affirmative_confirmation,
)
from exitspec.models import POCContract
from exitspec.review_links import (
    ReviewInvitationError,
    issue_customer_review_invitation,
)
from exitspec.web import DemoSession


FIXED_TIME = datetime(2026, 7, 24, 12, 0, tzinfo=timezone.utc)
VISIBLE_AGREEMENT_FIELDS = {
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
}


def _validated_contract_copy(contract, **updates):
    payload = contract.model_dump(mode="python")
    payload.update(updates)
    return POCContract.model_validate(payload)


def _record_affirmative(contract, *, key="customer-confirmation"):
    return record_confirmation(
        contract,
        confirmer_identity="customer@example.com",
        decision=ConfirmationDecision.CONFIRM,
        agreement_acknowledged=True,
        rationale="The visible agreement matches the intended POC.",
        idempotency_key=key,
        decided_at=FIXED_TIME,
    )


def test_fingerprint_tracks_the_visible_agreement_not_valid_lifecycle_metadata(
    approved_contract,
):
    agreement = canonical_confirmation_payload(approved_contract)
    fingerprint = contract_confirmation_fingerprint(approved_contract)
    lifecycle_variant = _validated_contract_copy(
        approved_contract,
        created_at=approved_contract.created_at - timedelta(days=1),
        approved_at=FIXED_TIME + timedelta(minutes=1),
        parent_version="support-agent-tool-selection@0.0.9",
    )

    assert set(agreement) == VISIBLE_AGREEMENT_FIELDS
    assert {
        "status",
        "created_at",
        "approved_at",
        "frozen_at",
        "parent_version",
        "confirmation_id",
        "canonical_hash",
    }.isdisjoint(agreement)
    assert canonical_confirmation_payload(lifecycle_variant) == agreement
    assert contract_confirmation_fingerprint(lifecycle_variant) == fingerprint

    changed_visible_agreement = _validated_contract_copy(
        approved_contract,
        use_case="A materially different customer use case",
    )
    assert canonical_confirmation_payload(changed_visible_agreement) != agreement
    assert (
        contract_confirmation_fingerprint(changed_visible_agreement)
        != fingerprint
    )


def test_confirmation_is_explicit_immutable_and_idempotent_only_for_exact_replay(
    approved_contract,
):
    with pytest.raises(ValueError, match="explicit agreement acknowledgement"):
        record_confirmation(
            approved_contract,
            confirmer_identity="customer@example.com",
            decision=ConfirmationDecision.CONFIRM,
            agreement_acknowledged=False,
            rationale="An unchecked box cannot confirm an agreement.",
            idempotency_key="customer-confirmation",
            decided_at=FIXED_TIME,
        )

    original = _record_affirmative(approved_contract)
    replay = record_confirmation(
        approved_contract,
        confirmer_identity=original.confirmer_identity,
        decision=original.decision,
        agreement_acknowledged=original.agreement_acknowledged,
        rationale=original.rationale,
        idempotency_key=original.idempotency_key,
        decided_at=FIXED_TIME + timedelta(hours=1),
        existing=original,
    )

    assert replay is original
    assert replay.decided_at == FIXED_TIME
    with pytest.raises(ValueError, match="different confirmation"):
        record_confirmation(
            approved_contract,
            confirmer_identity=original.confirmer_identity,
            decision=ConfirmationDecision.REQUEST_CHANGES,
            agreement_acknowledged=False,
            rationale="A conflicting decision reused the operation key.",
            idempotency_key=original.idempotency_key,
            existing=original,
        )
    with pytest.raises(ValidationError, match="Instance is frozen"):
        original.rationale = "Mutated after recording"


def test_confirmation_authority_is_bound_to_exact_version_and_fingerprint(
    approved_contract,
):
    confirmation = _record_affirmative(approved_contract)
    require_affirmative_confirmation(approved_contract, confirmation)
    assert confirmation_matches_contract(approved_contract, confirmation)

    next_version = _validated_contract_copy(
        approved_contract,
        version="0.1.1",
    )
    assert not confirmation_matches_contract(next_version, confirmation)
    with pytest.raises(ValueError, match="different contract id or version"):
        require_affirmative_confirmation(next_version, confirmation)

    same_version_changed_content = _validated_contract_copy(
        approved_contract,
        evidence_retention_policy="Retain approved evidence for 90 days.",
    )
    assert not confirmation_matches_contract(
        same_version_changed_content,
        confirmation,
    )
    with pytest.raises(ValueError, match="fingerprint does not match"):
        require_affirmative_confirmation(
            same_version_changed_content,
            confirmation,
        )


def test_review_capability_is_secret_immutable_and_has_constant_invalid_outcomes():
    invitation, raw_token = issue_customer_review_invitation(
        contract_id="support-agent-tool-selection",
        contract_version="0.1.0",
        confirmation_fingerprint="a" * 64,
        created_at=FIXED_TIME,
        ttl=timedelta(minutes=15),
        token="raw-customer-review-capability",
    )

    assert invitation.token_digest == hashlib.sha256(
        raw_token.encode("utf-8")
    ).hexdigest()
    assert raw_token not in repr(invitation)
    assert raw_token not in invitation.__dict__.values()
    assert invitation.accepts(
        raw_token,
        now=invitation.expires_at - timedelta(microseconds=1),
    )

    outcomes = []
    for invalid_token in ("wrong-token", "", None):
        assert not invitation.accepts(invalid_token, now=FIXED_TIME)
        with pytest.raises(ReviewInvitationError) as error:
            invitation.require_valid(invalid_token, now=FIXED_TIME)
        outcomes.append((type(error.value), str(error.value)))
        if invalid_token:
            assert str(invalid_token) not in str(error.value)
        assert raw_token not in str(error.value)
    assert outcomes == [
        (ReviewInvitationError, "Customer review link is invalid."),
    ] * 3

    assert not invitation.accepts(raw_token, now=invitation.expires_at)
    with pytest.raises(ReviewInvitationError, match="expired"):
        invitation.require_valid(raw_token, now=invitation.expires_at)
    with pytest.raises(FrozenInstanceError):
        invitation.contract_version = "0.1.1"


def test_version_revision_rejects_an_otherwise_valid_stale_review_capability(
    tmp_path,
):
    session = DemoSession.synthetic_support_agent(output_root=tmp_path / "runs")
    measurable, vague = session.reviewed_drafts
    session.review(
        measurable.id,
        "APPROVE",
        "field_engineer",
        "The measurable rule matches the call.",
    )
    session.review(
        vague.id,
        "REJECT",
        "field_engineer",
        "The vague request remains context only.",
    )
    session.create_customer_draft()
    stale_token = session.customer_review_token
    stale_invitation = session.customer_review_invitation
    assert stale_token is not None
    assert stale_invitation is not None

    pending = session.customer_review_payload(stale_token)["review"]
    assert pending["contract_version"] == stale_invitation.contract_version
    assert (
        pending["confirmation_fingerprint"]
        == stale_invitation.confirmation_fingerprint
    )
    session.record_customer_decision(
        stale_token,
        decision="REQUEST_CHANGES",
        confirmer="customer@example.com",
        agreement_acknowledged=False,
        rationale="Change the title before I confirm.",
        idempotency_key="request-title-revision",
    )
    session.start_revision()

    assert session.contract_seed.version != stale_invitation.contract_version
    assert stale_invitation.accepts(
        stale_token,
        now=stale_invitation.created_at + timedelta(microseconds=1),
    )
    with pytest.raises(ReviewInvitationError, match="invalid"):
        session.customer_review_payload(stale_token)
