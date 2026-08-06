"""Opaque, synthetic-only authentication boundary for Zoom webhook bytes.

This module verifies the documented Zoom ``v0`` webhook signature over the
exact received body.  It deliberately does not parse a Zoom event, expose an
HTTP route, create a meeting transport binding, append to the meeting inbox, or
retain the raw body.  A real sanitized golden fixture must ground those later
steps.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from threading import RLock
from typing import Any, Callable, Literal, Never

from pydantic import Field, field_validator, model_validator

from .canonical import canonical_json_bytes
from .models import FrozenExitSpecModel, SHA256_PATTERN


ZOOM_WEBHOOK_AUTH_VERSION = "exitspec-zoom-webhook-auth/1.0"
ZOOM_WEBHOOK_SIGNATURE_VERSION = "v0"
ZOOM_WEBHOOK_AUTHORITY = "OPAQUE_SYNTHETIC_WEBHOOK_AUTHENTICATION_ONLY"

_DELIVERY_DOMAIN = b"exitspec-zoom-webhook-delivery-v1\x00"
_POLICY_DOMAIN = b"exitspec-zoom-webhook-auth-policy-v1\x00"
_RECEIPT_DOMAIN = b"exitspec-zoom-webhook-auth-receipt-v1\x00"
_SIGNATURE_PATTERN = re.compile(r"^v0=[a-f0-9]{64}$")
_TIMESTAMP_PATTERN = re.compile(r"^(0|[1-9][0-9]{0,11})$")

_MAX_BODY_BYTES = 1024 * 1024
_MAX_REPLAY_RECORDS = 100_000
_MAX_SECRET_BYTES = 512
_MIN_SECRET_BYTES = 16


class ZoomWebhookAuthenticationFailureCode(str, Enum):
    """Stable request and policy refusal codes with content-free messages."""

    POLICY_NOT_ACTIVE = "ZOOM_WEBHOOK_POLICY_NOT_ACTIVE"
    POLICY_EXPIRED = "ZOOM_WEBHOOK_POLICY_EXPIRED"
    REQUEST_MALFORMED = "ZOOM_WEBHOOK_REQUEST_MALFORMED"
    BODY_LIMIT_EXCEEDED = "ZOOM_WEBHOOK_BODY_LIMIT_EXCEEDED"
    AUTHENTICATION_FAILED = "ZOOM_WEBHOOK_AUTHENTICATION_FAILED"
    TIMESTAMP_OUTSIDE_WINDOW = "ZOOM_WEBHOOK_TIMESTAMP_OUTSIDE_WINDOW"
    REPLAY_CAPACITY_EXCEEDED = "ZOOM_WEBHOOK_REPLAY_CAPACITY_EXCEEDED"


_FAILURE_DETAILS: dict[
    ZoomWebhookAuthenticationFailureCode,
    tuple[str, str],
] = {
    ZoomWebhookAuthenticationFailureCode.POLICY_NOT_ACTIVE: (
        "The reviewed Zoom webhook policy is not active yet.",
        "review_zoom_webhook_policy",
    ),
    ZoomWebhookAuthenticationFailureCode.POLICY_EXPIRED: (
        "The reviewed Zoom webhook policy has expired.",
        "renew_zoom_webhook_policy",
    ),
    ZoomWebhookAuthenticationFailureCode.REQUEST_MALFORMED: (
        "The Zoom webhook request is malformed.",
        "reject_zoom_webhook_request",
    ),
    ZoomWebhookAuthenticationFailureCode.BODY_LIMIT_EXCEEDED: (
        "The Zoom webhook body exceeds the reviewed limit.",
        "reject_oversized_zoom_webhook",
    ),
    ZoomWebhookAuthenticationFailureCode.AUTHENTICATION_FAILED: (
        "The Zoom webhook request could not be authenticated.",
        "reject_unauthenticated_zoom_webhook",
    ),
    ZoomWebhookAuthenticationFailureCode.TIMESTAMP_OUTSIDE_WINDOW: (
        "The Zoom webhook timestamp is outside the reviewed freshness window.",
        "reject_stale_zoom_webhook",
    ),
    ZoomWebhookAuthenticationFailureCode.REPLAY_CAPACITY_EXCEEDED: (
        "The Zoom webhook replay guard is at capacity.",
        "pause_zoom_webhook_ingress",
    ),
}


class ZoomWebhookAuthenticationDenied(RuntimeError):
    """Sanitized request refusal that reflects no body, header, or secret."""

    retryable = False

    def __init__(self, failure_code: ZoomWebhookAuthenticationFailureCode) -> None:
        self.failure_code = ZoomWebhookAuthenticationFailureCode(failure_code)
        self.code = self.failure_code.value
        safe_message, next_action = _FAILURE_DETAILS[self.failure_code]
        self.next_action = next_action
        super().__init__(safe_message)


class ZoomWebhookAuthenticationStateError(RuntimeError):
    """Sanitized local-state failure, distinct from an untrusted request."""

    code = "ZOOM_WEBHOOK_AUTHENTICATOR_STATE_INVALID"

    def __init__(self) -> None:
        super().__init__("The Zoom webhook authenticator state is invalid.")


class ZoomWebhookSecretBoundaryError(RuntimeError):
    """Refuse operations that could copy or serialize the server secret."""

    code = "ZOOM_WEBHOOK_SECRET_BOUNDARY_VIOLATION"

    def __init__(self) -> None:
        super().__init__("The Zoom webhook secret boundary cannot be serialized.")


class ZoomWebhookAuthenticationPolicy(FrozenExitSpecModel):
    """Reviewed server-owned limits for one synthetic authentication seam."""

    schema_version: Literal[ZOOM_WEBHOOK_AUTH_VERSION] = ZOOM_WEBHOOK_AUTH_VERSION
    policy_id: str = Field(pattern=r"^zoomwhpolicy_[a-z0-9][a-z0-9_-]{2,95}$")
    policy_version: str = Field(pattern=r"^[a-z][a-z0-9._:-]{1,127}$")
    signature_version: Literal[ZOOM_WEBHOOK_SIGNATURE_VERSION] = (
        ZOOM_WEBHOOK_SIGNATURE_VERSION
    )
    max_body_bytes: int = Field(gt=0, le=_MAX_BODY_BYTES)
    max_past_age_seconds: int = Field(gt=0, le=15 * 60)
    max_future_skew_seconds: int = Field(ge=0, le=5 * 60)
    max_replay_records: int = Field(gt=0, le=_MAX_REPLAY_RECORDS)
    reviewed_at: datetime
    expires_at: datetime
    synthetic_only: Literal[True] = True

    @field_validator("reviewed_at", "expires_at")
    @classmethod
    def normalize_time(cls, value: datetime, info: Any) -> datetime:
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise ValueError(f"{info.field_name} must be timezone-aware.")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def require_positive_policy_window(self) -> "ZoomWebhookAuthenticationPolicy":
        if self.expires_at <= self.reviewed_at:
            raise ValueError("expires_at must follow reviewed_at.")
        return self


class ZoomWebhookAuthenticationReceipt(FrozenExitSpecModel):
    """Content-free proof that one exact opaque delivery passed the seam."""

    schema_version: Literal[ZOOM_WEBHOOK_AUTH_VERSION] = ZOOM_WEBHOOK_AUTH_VERSION
    receipt_id: str = Field(pattern=r"^zoomwh_[a-f0-9]{64}$")
    delivery_id: str = Field(pattern=r"^zoomdelivery_[a-f0-9]{64}$")
    policy_id: str = Field(pattern=r"^zoomwhpolicy_[a-z0-9][a-z0-9_-]{2,95}$")
    policy_version: str = Field(pattern=r"^[a-z][a-z0-9._:-]{1,127}$")
    policy_sha256: str = Field(pattern=SHA256_PATTERN)
    webhook_event_sha256: str = Field(pattern=SHA256_PATTERN)
    body_size_bytes: int = Field(gt=0, le=_MAX_BODY_BYTES)
    signature_version: Literal[ZOOM_WEBHOOK_SIGNATURE_VERSION] = (
        ZOOM_WEBHOOK_SIGNATURE_VERSION
    )
    request_timestamp: datetime
    authenticated_at: datetime
    replay_expires_at: datetime
    authentication_authority: Literal[ZOOM_WEBHOOK_AUTHORITY] = (
        ZOOM_WEBHOOK_AUTHORITY
    )
    webhook_signature_verified: Literal[True] = True
    raw_body_retained: Literal[False] = False
    may_parse_zoom_payload: Literal[False] = False
    may_create_transport_binding: Literal[False] = False
    may_append_meeting_inbox: Literal[False] = False
    may_confirm_contract: Literal[False] = False
    may_freeze_contract: Literal[False] = False
    may_start_measurement: Literal[False] = False
    may_assign_verdict: Literal[False] = False
    synthetic_only: Literal[True] = True

    @field_validator("request_timestamp", "authenticated_at", "replay_expires_at")
    @classmethod
    def normalize_time(cls, value: datetime, info: Any) -> datetime:
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise ValueError(f"{info.field_name} must be timezone-aware.")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def validate_identity_and_window(self) -> "ZoomWebhookAuthenticationReceipt":
        if self.replay_expires_at < self.authenticated_at:
            raise ValueError("replay_expires_at cannot precede authenticated_at.")
        expected = _receipt_id(
            delivery_id=self.delivery_id,
            policy_sha256=self.policy_sha256,
            webhook_event_sha256=self.webhook_event_sha256,
            body_size_bytes=self.body_size_bytes,
            request_timestamp=self.request_timestamp,
            authenticated_at=self.authenticated_at,
            replay_expires_at=self.replay_expires_at,
        )
        if not hmac.compare_digest(self.receipt_id, expected):
            raise ValueError("receipt_id does not bind the receipt fields.")
        return self


class ZoomWebhookAuthenticationResult(FrozenExitSpecModel):
    """One first observation or exact process-local replay of its receipt."""

    receipt: ZoomWebhookAuthenticationReceipt
    exact_replay: bool
    first_observation: bool
    downstream_effect_permitted: Literal[False] = False

    @model_validator(mode="after")
    def require_complementary_disposition(self) -> "ZoomWebhookAuthenticationResult":
        if self.exact_replay == self.first_observation:
            raise ValueError(
                "exact_replay and first_observation must be complementary."
            )
        return self


@dataclass(frozen=True)
class _ReplayEntry:
    receipt: ZoomWebhookAuthenticationReceipt


class ZoomWebhookAuthenticator:
    """Thread-safe exact-body verifier with bounded process-local replay state."""

    __slots__ = (
        "_clock",
        "_closed",
        "_last_now",
        "_lock",
        "_policy",
        "_policy_sha256",
        "_replay",
        "_secret",
    )

    def __init__(
        self,
        policy: ZoomWebhookAuthenticationPolicy,
        secret: bytes,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if type(policy) is not ZoomWebhookAuthenticationPolicy:
            raise ValueError("A reviewed Zoom webhook policy is required.")
        if (
            type(secret) is not bytes
            or not _MIN_SECRET_BYTES <= len(secret) <= _MAX_SECRET_BYTES
        ):
            raise ValueError("The Zoom webhook secret is outside supported bounds.")
        if clock is not None and not callable(clock):
            raise ValueError("clock must be callable.")
        self._policy = policy
        self._policy_sha256 = zoom_webhook_policy_sha256(policy)
        self._secret = bytes(secret)
        self._clock = _utc_now if clock is None else clock
        self._lock = RLock()
        self._replay: dict[str, _ReplayEntry] = {}
        self._last_now: datetime | None = None
        self._closed = False

    def __repr__(self) -> str:
        return "ZoomWebhookAuthenticator(<server-secret>)"

    __str__ = __repr__

    def __copy__(self) -> Never:
        raise ZoomWebhookSecretBoundaryError()

    def __deepcopy__(self, memo: dict[int, object]) -> Never:
        raise ZoomWebhookSecretBoundaryError()

    def __getstate__(self) -> Never:
        raise ZoomWebhookSecretBoundaryError()

    def __reduce__(self) -> Never:
        raise ZoomWebhookSecretBoundaryError()

    def __reduce_ex__(self, protocol: int) -> Never:
        raise ZoomWebhookSecretBoundaryError()

    def close(self) -> None:
        """Release process-local references without claiming forensic erasure."""

        with self._lock:
            self._secret = b""
            self._replay.clear()
            self._last_now = None
            self._closed = True

    def authenticate(
        self,
        *,
        raw_body: bytes,
        request_timestamp: str,
        signature: str,
    ) -> ZoomWebhookAuthenticationResult:
        """Authenticate exact opaque bytes and record one bounded observation."""

        now = _require_clock_value(self._clock())
        secret = self._secret_snapshot_and_require_active(now)
        if type(raw_body) is not bytes or not raw_body:
            raise ZoomWebhookAuthenticationDenied(
                ZoomWebhookAuthenticationFailureCode.REQUEST_MALFORMED
            )
        if len(raw_body) > self._policy.max_body_bytes:
            raise ZoomWebhookAuthenticationDenied(
                ZoomWebhookAuthenticationFailureCode.BODY_LIMIT_EXCEEDED
            )

        timestamp_valid = (
            type(request_timestamp) is str
            and _TIMESTAMP_PATTERN.fullmatch(request_timestamp) is not None
        )
        signature_valid = (
            type(signature) is str
            and _SIGNATURE_PATTERN.fullmatch(signature) is not None
        )
        timestamp_bytes = (
            request_timestamp.encode("ascii") if timestamp_valid else b"invalid"
        )
        expected_signature = "v0=" + hmac.new(
            secret,
            b"v0:" + timestamp_bytes + b":" + raw_body,
            hashlib.sha256,
        ).hexdigest()
        candidate_signature = signature if type(signature) is str else ""
        signature_matches = hmac.compare_digest(
            expected_signature,
            candidate_signature,
        )
        if not timestamp_valid or not signature_valid or not signature_matches:
            raise ZoomWebhookAuthenticationDenied(
                ZoomWebhookAuthenticationFailureCode.AUTHENTICATION_FAILED
            )

        request_time = _parse_timestamp(request_timestamp)
        replay_expires_at = request_time + timedelta(
            seconds=self._policy.max_past_age_seconds
        )
        if (
            replay_expires_at < now
            or request_time - now
            > timedelta(seconds=self._policy.max_future_skew_seconds)
        ):
            raise ZoomWebhookAuthenticationDenied(
                ZoomWebhookAuthenticationFailureCode.TIMESTAMP_OUTSIDE_WINDOW
            )

        event_sha256 = hashlib.sha256(raw_body).hexdigest()
        delivery_id = _delivery_id(
            request_timestamp=request_timestamp,
            signature=signature,
            webhook_event_sha256=event_sha256,
            body_size_bytes=len(raw_body),
        )

        with self._lock:
            if self._closed or not self._secret:
                raise ZoomWebhookAuthenticationStateError()
            if self._last_now is not None and now < self._last_now:
                raise ZoomWebhookAuthenticationStateError()
            self._last_now = now
            self._prune(now)
            known = self._replay.get(delivery_id)
            if known is not None:
                return ZoomWebhookAuthenticationResult(
                    receipt=known.receipt,
                    exact_replay=True,
                    first_observation=False,
                )
            if len(self._replay) >= self._policy.max_replay_records:
                raise ZoomWebhookAuthenticationDenied(
                    ZoomWebhookAuthenticationFailureCode.REPLAY_CAPACITY_EXCEEDED
                )

            receipt_id = _receipt_id(
                delivery_id=delivery_id,
                policy_sha256=self._policy_sha256,
                webhook_event_sha256=event_sha256,
                body_size_bytes=len(raw_body),
                request_timestamp=request_time,
                authenticated_at=now,
                replay_expires_at=replay_expires_at,
            )
            receipt = ZoomWebhookAuthenticationReceipt(
                receipt_id=receipt_id,
                delivery_id=delivery_id,
                policy_id=self._policy.policy_id,
                policy_version=self._policy.policy_version,
                policy_sha256=self._policy_sha256,
                webhook_event_sha256=event_sha256,
                body_size_bytes=len(raw_body),
                request_timestamp=request_time,
                authenticated_at=now,
                replay_expires_at=replay_expires_at,
            )
            self._replay[delivery_id] = _ReplayEntry(receipt=receipt)
            return ZoomWebhookAuthenticationResult(
                receipt=receipt,
                exact_replay=False,
                first_observation=True,
            )

    def _secret_snapshot_and_require_active(self, now: datetime) -> bytes:
        with self._lock:
            if self._closed or not self._secret:
                raise ZoomWebhookAuthenticationStateError()
            secret = self._secret
        if now < self._policy.reviewed_at:
            raise ZoomWebhookAuthenticationDenied(
                ZoomWebhookAuthenticationFailureCode.POLICY_NOT_ACTIVE
            )
        if now >= self._policy.expires_at:
            raise ZoomWebhookAuthenticationDenied(
                ZoomWebhookAuthenticationFailureCode.POLICY_EXPIRED
            )
        return secret

    def _prune(self, now: datetime) -> None:
        expired = [
            delivery_id
            for delivery_id, entry in self._replay.items()
            if entry.receipt.replay_expires_at < now
        ]
        for delivery_id in expired:
            del self._replay[delivery_id]


def zoom_webhook_policy_sha256(policy: ZoomWebhookAuthenticationPolicy) -> str:
    """Bind every reviewed authentication limit into one stable digest."""

    if type(policy) is not ZoomWebhookAuthenticationPolicy:
        raise ValueError("A reviewed Zoom webhook policy is required.")
    return hashlib.sha256(
        _POLICY_DOMAIN + canonical_json_bytes(policy.model_dump(mode="json"))
    ).hexdigest()


def _delivery_id(
    *,
    request_timestamp: str,
    signature: str,
    webhook_event_sha256: str,
    body_size_bytes: int,
) -> str:
    digest = hashlib.sha256(
        _DELIVERY_DOMAIN
        + canonical_json_bytes(
            {
                "body_size_bytes": body_size_bytes,
                "request_timestamp": request_timestamp,
                "signature": signature,
                "webhook_event_sha256": webhook_event_sha256,
            }
        )
    ).hexdigest()
    return "zoomdelivery_" + digest


def _receipt_id(
    *,
    delivery_id: str,
    policy_sha256: str,
    webhook_event_sha256: str,
    body_size_bytes: int,
    request_timestamp: datetime,
    authenticated_at: datetime,
    replay_expires_at: datetime,
) -> str:
    digest = hashlib.sha256(
        _RECEIPT_DOMAIN
        + canonical_json_bytes(
            {
                "authenticated_at": _canonical_datetime(authenticated_at),
                "body_size_bytes": body_size_bytes,
                "delivery_id": delivery_id,
                "policy_sha256": policy_sha256,
                "replay_expires_at": _canonical_datetime(replay_expires_at),
                "request_timestamp": _canonical_datetime(request_timestamp),
                "webhook_event_sha256": webhook_event_sha256,
            }
        )
    ).hexdigest()
    return "zoomwh_" + digest


def _parse_timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromtimestamp(int(value), tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        raise ZoomWebhookAuthenticationDenied(
            ZoomWebhookAuthenticationFailureCode.AUTHENTICATION_FAILED
        ) from None
    return parsed


def _canonical_datetime(value: datetime) -> str:
    """Return one stable UTC representation for receipt identity inputs."""

    return (
        value.astimezone(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def _require_clock_value(value: object) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ZoomWebhookAuthenticationStateError()
    return value.astimezone(timezone.utc)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


__all__ = [
    "ZOOM_WEBHOOK_AUTHORITY",
    "ZOOM_WEBHOOK_AUTH_VERSION",
    "ZOOM_WEBHOOK_SIGNATURE_VERSION",
    "ZoomWebhookAuthenticationDenied",
    "ZoomWebhookAuthenticationFailureCode",
    "ZoomWebhookAuthenticationPolicy",
    "ZoomWebhookAuthenticationReceipt",
    "ZoomWebhookAuthenticationResult",
    "ZoomWebhookAuthenticationStateError",
    "ZoomWebhookAuthenticator",
    "ZoomWebhookSecretBoundaryError",
    "zoom_webhook_policy_sha256",
]
