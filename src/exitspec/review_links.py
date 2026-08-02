"""Ephemeral, version-scoped customer review invitations for the local demo."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple


DEFAULT_REVIEW_TTL = timedelta(hours=2)


class ReviewInvitationError(ValueError):
    """A customer review invitation is invalid, stale, or expired."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CustomerReviewInvitation:
    """A one-contract review capability that stores only a token digest."""

    invitation_id: str
    contract_id: str
    contract_version: str
    confirmation_fingerprint: str
    token_digest: str
    created_at: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        if not self.invitation_id.strip():
            raise ValueError("invitation_id must be non-empty.")
        if not self.contract_id.strip() or not self.contract_version.strip():
            raise ValueError("A review invitation must identify one contract version.")
        if len(self.confirmation_fingerprint) != 64:
            raise ValueError("confirmation_fingerprint must be a SHA-256 digest.")
        if len(self.token_digest) != 64:
            raise ValueError("token_digest must be a SHA-256 digest.")
        if self.created_at.tzinfo is None or self.expires_at.tzinfo is None:
            raise ValueError("Review invitation timestamps must be timezone-aware.")
        if self.expires_at <= self.created_at:
            raise ValueError("A review invitation must expire after it is created.")

    def accepts(self, token: str, *, now: Optional[datetime] = None) -> bool:
        """Return whether ``token`` is valid without exposing the stored digest."""

        checked_at = now or _utc_now()
        if checked_at.tzinfo is None:
            raise ValueError("Review validation time must be timezone-aware.")
        if checked_at >= self.expires_at:
            return False
        if not isinstance(token, str) or not token:
            return False
        return hmac.compare_digest(self.token_digest, _token_digest(token))

    def require_valid(self, token: str, *, now: Optional[datetime] = None) -> None:
        """Reject expired and mismatched capabilities with non-secret errors."""

        checked_at = now or _utc_now()
        if checked_at.tzinfo is None:
            raise ValueError("Review validation time must be timezone-aware.")
        if checked_at >= self.expires_at:
            raise ReviewInvitationError("Customer review link has expired.")
        if not isinstance(token, str) or not token:
            raise ReviewInvitationError("Customer review link is invalid.")
        if not hmac.compare_digest(self.token_digest, _token_digest(token)):
            raise ReviewInvitationError("Customer review link is invalid.")


def issue_customer_review_invitation(
    *,
    contract_id: str,
    contract_version: str,
    confirmation_fingerprint: str,
    created_at: Optional[datetime] = None,
    ttl: timedelta = DEFAULT_REVIEW_TTL,
    token: Optional[str] = None,
    invitation_id: Optional[str] = None,
) -> Tuple[CustomerReviewInvitation, str]:
    """Create an invitation and return its raw capability exactly once."""

    timestamp = created_at or _utc_now()
    if timestamp.tzinfo is None:
        raise ValueError("Review invitation creation time must be timezone-aware.")
    if ttl <= timedelta(0):
        raise ValueError("Review invitation ttl must be positive.")

    raw_token = token or secrets.token_urlsafe(32)
    if not raw_token.strip():
        raise ValueError("Review invitation token must be non-empty.")
    invitation = CustomerReviewInvitation(
        invitation_id=(
            invitation_id
            if invitation_id is not None
            else "review-{0}".format(secrets.token_hex(12))
        ),
        contract_id=contract_id,
        contract_version=contract_version,
        confirmation_fingerprint=confirmation_fingerprint,
        token_digest=_token_digest(raw_token),
        created_at=timestamp,
        expires_at=timestamp + ttl,
    )
    return invitation, raw_token
