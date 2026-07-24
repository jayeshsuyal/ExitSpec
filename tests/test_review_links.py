from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone

import pytest

from exitspec.review_links import (
    CustomerReviewInvitation,
    ReviewInvitationError,
    issue_customer_review_invitation,
)


FIXED_TIME = datetime(2026, 7, 23, 12, 0, tzinfo=timezone.utc)
FINGERPRINT = "a" * 64


def test_review_invitation_is_scoped_and_stores_only_token_digest():
    invitation, token = issue_customer_review_invitation(
        contract_id="support-agent-poc",
        contract_version="1.0.0",
        confirmation_fingerprint=FINGERPRINT,
        created_at=FIXED_TIME,
        token="customer-review-secret",
    )

    assert token == "customer-review-secret"
    assert invitation.contract_id == "support-agent-poc"
    assert invitation.contract_version == "1.0.0"
    assert invitation.confirmation_fingerprint == FINGERPRINT
    assert invitation.token_digest != token
    assert "customer-review-secret" not in repr(invitation)
    assert invitation.accepts(token, now=FIXED_TIME + timedelta(minutes=1))
    assert not invitation.accepts("wrong-token", now=FIXED_TIME)


def test_review_invitation_expires_and_uses_non_secret_errors():
    invitation, token = issue_customer_review_invitation(
        contract_id="support-agent-poc",
        contract_version="1.0.0",
        confirmation_fingerprint=FINGERPRINT,
        created_at=FIXED_TIME,
        ttl=timedelta(minutes=15),
        token="customer-review-secret",
    )

    with pytest.raises(ReviewInvitationError, match="expired") as error:
        invitation.require_valid(token, now=FIXED_TIME + timedelta(minutes=15))
    assert token not in str(error.value)

    with pytest.raises(ReviewInvitationError, match="invalid") as error:
        invitation.require_valid("wrong-token", now=FIXED_TIME)
    assert "wrong-token" not in str(error.value)


def test_review_invitation_is_immutable():
    invitation, _ = issue_customer_review_invitation(
        contract_id="support-agent-poc",
        contract_version="1.0.0",
        confirmation_fingerprint=FINGERPRINT,
        created_at=FIXED_TIME,
        token="customer-review-secret",
    )

    with pytest.raises(FrozenInstanceError):
        invitation.contract_version = "2.0.0"


@pytest.mark.parametrize(
    "kwargs",
    (
        {"ttl": timedelta(0)},
        {"ttl": timedelta(seconds=-1)},
        {"token": " "},
    ),
)
def test_review_invitation_rejects_invalid_issue_parameters(kwargs):
    with pytest.raises(ValueError):
        issue_customer_review_invitation(
            contract_id="support-agent-poc",
            contract_version="1.0.0",
            confirmation_fingerprint=FINGERPRINT,
            created_at=FIXED_TIME,
            **kwargs,
        )


def test_review_invitation_rejects_invalid_digest_shapes():
    with pytest.raises(ValueError, match="confirmation_fingerprint"):
        CustomerReviewInvitation(
            invitation_id="review-test",
            contract_id="support-agent-poc",
            contract_version="1.0.0",
            confirmation_fingerprint="not-a-digest",
            token_digest="b" * 64,
            created_at=FIXED_TIME,
            expires_at=FIXED_TIME + timedelta(hours=1),
        )
