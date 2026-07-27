"""Fail-closed authorization for one exact provider request.

This module deliberately stops before network transport.  It turns the frozen
acceptance manifest into an immutable policy, binds one exact redacted
``StructuredJSONRequest`` to that policy, and issues a short-lived capability.
Successful authorization returns a one-use permit that privately carries a
detached copy of the exact request.  An authorized transport must take its
request from that permit instead of accepting caller-supplied request bytes.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
import secrets
import threading
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Callable, Dict, Generic, Mapping, Optional, Sequence, Tuple, TypeVar
from urllib.parse import urlsplit

from pydantic import ConfigDict, Field, ValidationError, field_validator, model_validator

from .canonical import CanonicalizationError, canonical_json_bytes
from .models import SHA256_PATTERN, FrozenExitSpecModel
from .providers import ProviderMessage, StructuredJSONRequest
from .redaction import (
    POLICY_VERSION,
    RedactionBoundaryError,
    RedactionConfigurationError,
    assert_redaction_egress,
    redact_transcript,
)


OutputT = TypeVar("OutputT")

DEFAULT_EGRESS_ACKNOWLEDGEMENT_TTL = timedelta(minutes=5)
MAX_EGRESS_CAPABILITY_CHARACTERS = 2048
EGRESS_POLICY_VERSION = "exitspec-provider-egress/1.0"
WAVE1_POLICY_IDENTITY_SHA256 = (
    "5dcd98965bc158ed915f4bf9207a8b43e647e2d069126da970ea36bac008aa71"
)

_PAYLOAD_DOMAIN = b"exitspec-provider-egress-payload-v1\x00"
_DATA_POLICY_DOMAIN = b"exitspec-provider-data-policy-v1\x00"
_PRICING_DOMAIN = b"exitspec-provider-pricing-v1\x00"
_REQUEST_LIMITS_DOMAIN = b"exitspec-provider-request-limits-v1\x00"
_REDACTION_CONFIGURATION_DOMAIN = (
    b"exitspec-provider-redaction-configuration-v1\x00"
)
_BINDING_DOMAIN = b"exitspec-provider-egress-acknowledgement-v1\x00"
_WAVE1_POLICY_DOMAIN = b"exitspec-wave-1-provider-policy-v1\x00"
_AUTHORIZED_PERMIT_SEAL = object()

_BOUND_FIELDS = frozenset(
    {
        "acceptance_manifest_id",
        "acceptance_manifest_version",
        "source_fixture_sha256",
        "source_case_id",
        "redacted_payload_digest",
        "redaction_policy_version",
        "redaction_configuration_digest",
        "provider",
        "model",
        "endpoint",
        "data_policy_snapshot",
        "pricing_snapshot",
        "request_limits",
        "max_request_cost_usd",
        "issued_at",
        "expires_at",
        "nonce",
    }
)

_REQUIRED_REJECTIONS = frozenset(
    {
        "missing",
        "expired",
        "payload_mismatch",
        "policy_mismatch",
        "acceptance_manifest_mismatch",
        "source_fixture_mismatch",
        "source_case_mismatch",
        "redaction_configuration_mismatch",
        "provider_mismatch",
        "model_mismatch",
        "endpoint_mismatch",
        "pricing_mismatch",
        "request_limit_mismatch",
        "budget_mismatch",
        "replayed",
    }
)

_REQUEST_LIMIT_FIELDS = frozenset(
    {
        "estimated_input_tokens_max",
        "output_tokens_max",
        "timeout_seconds",
        "max_attempts",
        "max_retry_after_seconds",
        "max_request_cost_usd",
        "max_live_smoke_total_cost_usd",
    }
)


class ProviderEgressPolicyError(ValueError):
    """The trusted frozen manifest could not produce an egress policy."""


class ProviderEgressIntentError(ValueError):
    """A request could not enter the acknowledgement workflow safely."""


class EgressRejectionReason(str, Enum):
    """Content-free categories for a denied acknowledgement or permit."""

    NOT_ACKNOWLEDGED = "not_acknowledged"
    INVALID = "invalid"
    EXPIRED = "expired"
    INTENT_MISMATCH = "intent_mismatch"
    REPLAYED = "replayed"


class ProviderEgressAcknowledgementError(ValueError):
    """A safe typed refusal that grants no provider-egress authority."""

    code = "egress_not_authorized"
    retryable = False
    next_action = "reauthorize_provider_egress"

    def __init__(self, reason: EgressRejectionReason, safe_message: str) -> None:
        self.reason = EgressRejectionReason(reason)
        super().__init__(safe_message)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_timestamp(value: object, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("{0} must be timezone-aware.".format(field_name))
    return value.astimezone(timezone.utc)


def _canonical_json_text(value: Any) -> str:
    return canonical_json_bytes(value).decode("utf-8")


def _digest(domain: bytes, value: Any) -> str:
    return hashlib.sha256(domain + canonical_json_bytes(value)).hexdigest()


def _token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _decimal_value(value: object, field_name: str) -> Decimal:
    if isinstance(value, bool):
        raise ValueError("{0} must be a finite positive decimal.".format(field_name))
    try:
        decimal_value = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError(
            "{0} must be a finite positive decimal.".format(field_name)
        ) from None
    if not decimal_value.is_finite() or decimal_value <= 0:
        raise ValueError("{0} must be a finite positive decimal.".format(field_name))
    return decimal_value


def _decimal_text(value: Decimal) -> str:
    return format(value, "f")


def _canonical_mapping_json(value: object, field_name: str) -> str:
    if not isinstance(value, Mapping) or not value:
        raise ValueError("{0} must be a non-empty JSON object.".format(field_name))
    try:
        return _canonical_json_text(dict(value))
    except (CanonicalizationError, TypeError, ValueError):
        raise ValueError(
            "{0} must contain only canonical JSON values.".format(field_name)
        ) from None


def _detached_json_object(canonical_json: str) -> Dict[str, Any]:
    value = json.loads(canonical_json)
    if not isinstance(value, dict):
        raise ValueError("Canonical policy snapshot must be a JSON object.")
    return value


def _request_intent_payload(
    request: StructuredJSONRequest[Any],
) -> Dict[str, Any]:
    return {
        "model": request.model,
        "messages": [
            {
                "role": message.role,
                "content": message.content,
            }
            for message in request.messages
        ],
        "schema_name": request.schema_name,
        "response_schema": request.response_schema_payload(),
        "max_output_tokens": request.max_output_tokens,
        "estimated_input_tokens": request.estimated_input_tokens,
        "budget_usd": (
            None
            if request.budget_usd is None
            else _decimal_text(request.budget_usd)
        ),
        "timeout_seconds": request.timeout_seconds,
        "temperature": request.temperature,
    }


def _require_pre_redacted_request(
    request_payload: Mapping[str, Any],
    *,
    customer_terms: Sequence[str],
) -> None:
    try:
        request_json = _canonical_json_text(request_payload)
        redaction = redact_transcript(
            request_json,
            customer_terms=customer_terms,
        )
        checked = assert_redaction_egress(
            redaction,
            customer_terms=customer_terms,
        )
    except (
        CanonicalizationError,
        RedactionBoundaryError,
        RedactionConfigurationError,
        TypeError,
        ValueError,
    ):
        raise ProviderEgressIntentError(
            "Provider egress requires one policy-compliant redacted request."
        ) from None
    if checked != request_json:
        raise ProviderEgressIntentError(
            "Provider egress requires one policy-compliant redacted request."
        )


def _normalized_customer_terms(customer_terms: Sequence[str]) -> Tuple[str, ...]:
    if isinstance(customer_terms, (str, bytes)):
        raise ProviderEgressIntentError(
            "Provider egress redaction configuration is invalid."
        )
    try:
        values = tuple(customer_terms)
    except TypeError:
        raise ProviderEgressIntentError(
            "Provider egress redaction configuration is invalid."
        ) from None

    normalized: Dict[str, str] = {}
    for value in values:
        if not isinstance(value, str):
            raise ProviderEgressIntentError(
                "Provider egress redaction configuration is invalid."
            )
        clean = value.strip()
        if len(clean) < 3 or "\n" in clean or "\r" in clean:
            raise ProviderEgressIntentError(
                "Provider egress redaction configuration is invalid."
            )
        normalized.setdefault(clean.casefold(), clean)
    return tuple(
        sorted(
            normalized.values(),
            key=lambda value: (-len(value), value.casefold()),
        )
    )


def _redaction_configuration_digest(
    *,
    policy_version: str,
    customer_terms: Sequence[str],
) -> str:
    normalized_terms = _normalized_customer_terms(customer_terms)
    try:
        return _digest(
            _REDACTION_CONFIGURATION_DOMAIN,
            {
                "policy_version": policy_version,
                "customer_terms": list(normalized_terms),
            },
        )
    except (CanonicalizationError, TypeError, ValueError):
        raise ProviderEgressIntentError(
            "Provider egress redaction configuration is invalid."
        ) from None


class ProviderEgressPolicy(FrozenExitSpecModel):
    """Immutable policy projection loaded from one frozen acceptance manifest."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
    )

    acceptance_manifest_id: str = Field(min_length=1, max_length=200)
    acceptance_manifest_version: str = Field(min_length=1, max_length=100)
    source_fixture_sha256: str = Field(pattern=SHA256_PATTERN)
    source_case_id: str = Field(min_length=1, max_length=200)
    redacted_payload_digest: str = Field(pattern=SHA256_PATTERN)
    redaction_policy_version: str = Field(min_length=1, max_length=200)
    redaction_configuration_digest: str = Field(pattern=SHA256_PATTERN)
    provider: str = Field(min_length=1, max_length=100)
    model: str = Field(min_length=1, max_length=500)
    endpoint: str = Field(min_length=1, max_length=2000)
    data_policy_snapshot_json: str = Field(min_length=2, repr=False)
    data_policy_digest: str = Field(pattern=SHA256_PATTERN, repr=False)
    pricing_snapshot_json: str = Field(min_length=2, repr=False)
    pricing_digest: str = Field(pattern=SHA256_PATTERN, repr=False)
    request_limits_json: str = Field(min_length=2, repr=False)
    request_limits_digest: str = Field(pattern=SHA256_PATTERN, repr=False)
    max_request_cost_usd: Decimal = Field(gt=0)
    acknowledgement_policy_version: str = Field(min_length=1, max_length=200)
    acknowledgement_ttl_seconds: int = Field(gt=0, le=300)

    @field_validator(
        "acceptance_manifest_id",
        "acceptance_manifest_version",
        "source_case_id",
        "provider",
        "model",
        "redaction_policy_version",
        "acknowledgement_policy_version",
    )
    @classmethod
    def reject_ambiguous_identity(cls, value: str) -> str:
        if value != value.strip() or any(character.isspace() for character in value):
            raise ValueError("Provider egress policy identity is ambiguous.")
        return value

    @field_validator("endpoint")
    @classmethod
    def require_exact_https_destination(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            value != value.strip()
            or any(character.isspace() for character in value)
            or parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(
                "Provider egress policy requires one credential-free HTTPS URL."
            )
        return value

    @field_validator("max_request_cost_usd")
    @classmethod
    def require_finite_cost(cls, value: Decimal) -> Decimal:
        return _decimal_value(value, "max_request_cost_usd")

    @model_validator(mode="after")
    def require_consistent_frozen_policy(self) -> "ProviderEgressPolicy":
        if self.redaction_policy_version != POLICY_VERSION:
            raise ValueError("Provider egress policy uses an unsupported redaction policy.")
        if self.acknowledgement_policy_version != EGRESS_POLICY_VERSION:
            raise ValueError(
                "Provider egress policy uses an unsupported acknowledgement policy."
            )

        snapshots = (
            (
                self.data_policy_snapshot_json,
                self.data_policy_digest,
                _DATA_POLICY_DOMAIN,
            ),
            (
                self.pricing_snapshot_json,
                self.pricing_digest,
                _PRICING_DOMAIN,
            ),
            (
                self.request_limits_json,
                self.request_limits_digest,
                _REQUEST_LIMITS_DOMAIN,
            ),
        )
        for snapshot_json, expected_digest, domain in snapshots:
            try:
                snapshot = _detached_json_object(snapshot_json)
                if _canonical_json_text(snapshot) != snapshot_json:
                    raise ValueError
                if _digest(domain, snapshot) != expected_digest:
                    raise ValueError
            except Exception:
                raise ValueError(
                    "Provider egress policy snapshot is inconsistent."
                ) from None

        limits = self.request_limits()
        if set(limits) != _REQUEST_LIMIT_FIELDS:
            raise ValueError("Provider egress request limits are incomplete.")
        positive_integer_fields = (
            "estimated_input_tokens_max",
            "output_tokens_max",
            "max_attempts",
            "max_retry_after_seconds",
        )
        for field_name in positive_integer_fields:
            value = limits[field_name]
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or value <= 0
            ):
                raise ValueError("Provider egress request limits are invalid.")
        timeout = limits["timeout_seconds"]
        if (
            not isinstance(timeout, (int, float))
            or isinstance(timeout, bool)
            or not math.isfinite(float(timeout))
            or float(timeout) <= 0
        ):
            raise ValueError("Provider egress request limits are invalid.")
        if _decimal_value(
            limits["max_request_cost_usd"],
            "max_request_cost_usd",
        ) != self.max_request_cost_usd:
            raise ValueError("Provider egress request budget is inconsistent.")
        if _decimal_value(
            limits["max_live_smoke_total_cost_usd"],
            "max_live_smoke_total_cost_usd",
        ) < self.max_request_cost_usd:
            raise ValueError("Provider egress live-smoke budget is inconsistent.")
        return self

    @classmethod
    def from_frozen_manifest(
        cls,
        manifest: Mapping[str, Any],
    ) -> "ProviderEgressPolicy":
        """Build a detached policy only from the frozen manifest contract."""

        try:
            if not isinstance(manifest, Mapping) or manifest["status"] != "FROZEN":
                raise ValueError
            source = manifest["source_fixture"]
            approved = manifest["approved_live_smoke_request"]
            boundary = manifest["provider_boundary"]
            acknowledgement = manifest["egress_acknowledgement"]
            if (
                source["synthetic_only"] is not True
                or source["live_smoke_case_id"] != approved["source_case_id"]
                or acknowledgement["required"] is not True
                or acknowledgement["server_validated"] is not True
                or acknowledgement["one_time_use"] is not True
                or acknowledgement["payload_binding"]
                != "sha256_of_canonical_redacted_request_intent"
                or set(acknowledgement["bound_fields"]) != _BOUND_FIELDS
                or set(acknowledgement["required_rejections"])
                != _REQUIRED_REJECTIONS
                or acknowledgement["ttl_seconds"]
                != int(DEFAULT_EGRESS_ACKNOWLEDGEMENT_TTL.total_seconds())
            ):
                raise ValueError

            data_policy_json = _canonical_mapping_json(
                boundary["data_policy_snapshot"],
                "data_policy_snapshot",
            )
            pricing_json = _canonical_mapping_json(
                boundary["pricing_snapshot"],
                "pricing_snapshot",
            )
            request_limits_json = _canonical_mapping_json(
                boundary["request_limits"],
                "request_limits",
            )
            data_policy = _detached_json_object(data_policy_json)
            pricing = _detached_json_object(pricing_json)
            request_limits = _detached_json_object(request_limits_json)

            policy = cls(
                acceptance_manifest_id=manifest["manifest_id"],
                acceptance_manifest_version=manifest["manifest_version"],
                source_fixture_sha256=source["sha256"],
                source_case_id=approved["source_case_id"],
                redacted_payload_digest=approved["redacted_payload_digest"],
                redaction_policy_version=boundary["redaction_policy_version"],
                redaction_configuration_digest=approved[
                    "redaction_configuration_digest"
                ],
                provider=boundary["provider"],
                model=boundary["model"],
                endpoint=boundary["endpoint"],
                data_policy_snapshot_json=data_policy_json,
                data_policy_digest=_digest(_DATA_POLICY_DOMAIN, data_policy),
                pricing_snapshot_json=pricing_json,
                pricing_digest=_digest(_PRICING_DOMAIN, pricing),
                request_limits_json=request_limits_json,
                request_limits_digest=_digest(
                    _REQUEST_LIMITS_DOMAIN,
                    request_limits,
                ),
                max_request_cost_usd=_decimal_value(
                    request_limits["max_request_cost_usd"],
                    "max_request_cost_usd",
                ),
                acknowledgement_policy_version=acknowledgement[
                    "policy_version"
                ],
                acknowledgement_ttl_seconds=acknowledgement["ttl_seconds"],
            )
            if not policy.is_frozen_wave1_policy():
                raise ValueError
            return policy
        except (
            CanonicalizationError,
            KeyError,
            TypeError,
            ValueError,
            ValidationError,
        ):
            raise ProviderEgressPolicyError(
                "Frozen provider egress policy is invalid."
            ) from None

    def data_policy_snapshot(self) -> Dict[str, Any]:
        return _detached_json_object(self.data_policy_snapshot_json)

    def pricing_snapshot(self) -> Dict[str, Any]:
        return _detached_json_object(self.pricing_snapshot_json)

    def request_limits(self) -> Dict[str, Any]:
        return _detached_json_object(self.request_limits_json)

    def identity_payload(self) -> Dict[str, Any]:
        """Return the complete content-free Wave-1 policy identity."""

        return {
            "acceptance_manifest_id": self.acceptance_manifest_id,
            "acceptance_manifest_version": self.acceptance_manifest_version,
            "source_fixture_sha256": self.source_fixture_sha256,
            "source_case_id": self.source_case_id,
            "redacted_payload_digest": self.redacted_payload_digest,
            "redaction_policy_version": self.redaction_policy_version,
            "redaction_configuration_digest": (
                self.redaction_configuration_digest
            ),
            "provider": self.provider,
            "model": self.model,
            "endpoint": self.endpoint,
            "data_policy_snapshot": self.data_policy_snapshot(),
            "pricing_snapshot": self.pricing_snapshot(),
            "request_limits": self.request_limits(),
            "max_request_cost_usd": _decimal_text(
                self.max_request_cost_usd
            ),
            "acknowledgement_policy_version": (
                self.acknowledgement_policy_version
            ),
            "acknowledgement_ttl_seconds": (
                self.acknowledgement_ttl_seconds
            ),
        }

    def is_frozen_wave1_policy(self) -> bool:
        """Return whether this object matches the code-pinned policy anchor."""

        try:
            actual = _digest(_WAVE1_POLICY_DOMAIN, self.identity_payload())
        except Exception:
            return False
        return hmac.compare_digest(actual, WAVE1_POLICY_IDENTITY_SHA256)


class ProviderEgressIntent(FrozenExitSpecModel):
    """Content-free identity for one manifest-approved provider request."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
    )

    acceptance_manifest_id: str
    acceptance_manifest_version: str
    source_fixture_sha256: str = Field(pattern=SHA256_PATTERN)
    source_case_id: str
    redacted_payload_digest: str = Field(pattern=SHA256_PATTERN)
    redaction_policy_version: str
    redaction_configuration_digest: str = Field(pattern=SHA256_PATTERN)
    provider: str
    model: str
    endpoint: str
    data_policy_snapshot_json: str = Field(repr=False)
    pricing_snapshot_json: str = Field(repr=False)
    request_limits_json: str = Field(repr=False)
    max_request_cost_usd: Decimal = Field(gt=0)

    def data_policy_snapshot(self) -> Dict[str, Any]:
        return _detached_json_object(self.data_policy_snapshot_json)

    def pricing_snapshot(self) -> Dict[str, Any]:
        return _detached_json_object(self.pricing_snapshot_json)

    def request_limits(self) -> Dict[str, Any]:
        return _detached_json_object(self.request_limits_json)

    def public_preview(self) -> Dict[str, Any]:
        """Return the safe facts an operator explicitly acknowledges."""

        return {
            "acceptance_manifest_id": self.acceptance_manifest_id,
            "acceptance_manifest_version": self.acceptance_manifest_version,
            "source_fixture_sha256": self.source_fixture_sha256,
            "source_case_id": self.source_case_id,
            "redacted_payload_digest": self.redacted_payload_digest,
            "redaction_policy_version": self.redaction_policy_version,
            "redaction_configuration_digest": (
                self.redaction_configuration_digest
            ),
            "provider": self.provider,
            "model": self.model,
            "endpoint": self.endpoint,
            "data_policy_snapshot": self.data_policy_snapshot(),
            "pricing_snapshot": self.pricing_snapshot(),
            "request_limits": self.request_limits(),
            "max_request_cost_usd": _decimal_text(
                self.max_request_cost_usd
            ),
        }


def _intent_from_policy(policy: ProviderEgressPolicy) -> ProviderEgressIntent:
    return ProviderEgressIntent(
        acceptance_manifest_id=policy.acceptance_manifest_id,
        acceptance_manifest_version=policy.acceptance_manifest_version,
        source_fixture_sha256=policy.source_fixture_sha256,
        source_case_id=policy.source_case_id,
        redacted_payload_digest=policy.redacted_payload_digest,
        redaction_policy_version=policy.redaction_policy_version,
        redaction_configuration_digest=policy.redaction_configuration_digest,
        provider=policy.provider,
        model=policy.model,
        endpoint=policy.endpoint,
        data_policy_snapshot_json=policy.data_policy_snapshot_json,
        pricing_snapshot_json=policy.pricing_snapshot_json,
        request_limits_json=policy.request_limits_json,
        max_request_cost_usd=policy.max_request_cost_usd,
    )


def build_provider_egress_intent(
    request: StructuredJSONRequest[Any],
    *,
    policy: ProviderEgressPolicy,
    customer_terms: Sequence[str] = (),
) -> ProviderEgressIntent:
    """Validate one exact request against immutable trusted policy."""

    if not isinstance(request, StructuredJSONRequest):
        raise ProviderEgressIntentError(
            "Provider egress requires one typed structured request."
        )
    if not isinstance(policy, ProviderEgressPolicy):
        raise ProviderEgressIntentError(
            "Provider egress requires trusted frozen policy."
        )
    if request.model != policy.model:
        raise ProviderEgressIntentError(
            "Provider request does not match frozen provider policy."
        )
    if (
        request.budget_usd is None
        or request.estimated_input_tokens is None
        or request.max_output_tokens is None
    ):
        raise ProviderEgressIntentError(
            "Provider egress requires explicit request and spend bounds."
        )

    limits = policy.request_limits()
    if (
        request.estimated_input_tokens
        > limits["estimated_input_tokens_max"]
        or request.max_output_tokens > limits["output_tokens_max"]
        or float(request.timeout_seconds) > float(limits["timeout_seconds"])
        or request.budget_usd != policy.max_request_cost_usd
    ):
        raise ProviderEgressIntentError(
            "Provider request exceeds or changes frozen request limits."
        )

    request_payload = _request_intent_payload(request)
    _require_pre_redacted_request(
        request_payload,
        customer_terms=customer_terms,
    )
    if _redaction_configuration_digest(
        policy_version=policy.redaction_policy_version,
        customer_terms=customer_terms,
    ) != policy.redaction_configuration_digest:
        raise ProviderEgressIntentError(
            "Provider request redaction configuration is not approved."
        )
    try:
        payload_digest = _digest(_PAYLOAD_DOMAIN, request_payload)
    except (CanonicalizationError, TypeError, ValueError):
        raise ProviderEgressIntentError(
            "Provider request could not be bound safely."
        ) from None
    if not hmac.compare_digest(
        payload_digest,
        policy.redacted_payload_digest,
    ):
        raise ProviderEgressIntentError(
            "Provider request is not the approved synthetic live-smoke payload."
        )
    return _intent_from_policy(policy)


def provider_egress_binding_payload(
    intent: ProviderEgressIntent,
    *,
    issued_at: datetime,
    expires_at: datetime,
    nonce: str,
) -> Dict[str, Any]:
    """Return the complete manifest-declared acknowledgement field set."""

    if not isinstance(intent, ProviderEgressIntent):
        raise ValueError("A typed provider egress intent is required.")
    issued = _utc_timestamp(issued_at, "issued_at")
    expires = _utc_timestamp(expires_at, "expires_at")
    if expires <= issued:
        raise ValueError("Provider egress acknowledgement must expire after issue.")
    if (
        not isinstance(nonce, str)
        or not nonce
        or len(nonce) > 500
        or any(character.isspace() for character in nonce)
    ):
        raise ValueError("Provider egress acknowledgement nonce is invalid.")
    return {
        "acceptance_manifest_id": intent.acceptance_manifest_id,
        "acceptance_manifest_version": intent.acceptance_manifest_version,
        "source_fixture_sha256": intent.source_fixture_sha256,
        "source_case_id": intent.source_case_id,
        "redacted_payload_digest": intent.redacted_payload_digest,
        "redaction_policy_version": intent.redaction_policy_version,
        "redaction_configuration_digest": (
            intent.redaction_configuration_digest
        ),
        "provider": intent.provider,
        "model": intent.model,
        "endpoint": intent.endpoint,
        "data_policy_snapshot": intent.data_policy_snapshot(),
        "pricing_snapshot": intent.pricing_snapshot(),
        "request_limits": intent.request_limits(),
        "max_request_cost_usd": _decimal_text(
            intent.max_request_cost_usd
        ),
        "issued_at": issued.isoformat(),
        "expires_at": expires.isoformat(),
        "nonce": nonce,
    }


class ProviderEgressAcknowledgement(FrozenExitSpecModel):
    """Public content-free record of one explicit operator action."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
    )

    acknowledgement_id: str = Field(pattern=r"^egress_[a-f0-9]{64}$")
    binding_digest: str = Field(pattern=SHA256_PATTERN)
    intent: ProviderEgressIntent
    issued_at: datetime
    expires_at: datetime

    @model_validator(mode="after")
    def require_consistent_public_identity(
        self,
    ) -> "ProviderEgressAcknowledgement":
        issued = _utc_timestamp(self.issued_at, "issued_at")
        expires = _utc_timestamp(self.expires_at, "expires_at")
        if expires <= issued:
            raise ValueError("Provider egress acknowledgement times are invalid.")
        if self.acknowledgement_id != "egress_{0}".format(
            self.binding_digest
        ):
            raise ValueError(
                "Provider egress acknowledgement identity is inconsistent."
            )
        return self

    def __repr__(self) -> str:
        return (
            "ProviderEgressAcknowledgement("
            "acknowledgement_id={0!r}, binding_digest={1!r}, "
            "intent=<content-free>, issued_at={2!r}, expires_at={3!r})"
        ).format(
            self.acknowledgement_id,
            self.binding_digest,
            self.issued_at,
            self.expires_at,
        )


@dataclass(frozen=True, repr=False)
class _StoredAcknowledgement:
    public: ProviderEgressAcknowledgement
    token_digest: str
    nonce: str


def _clone_structured_request(
    request: StructuredJSONRequest[OutputT],
) -> StructuredJSONRequest[OutputT]:
    """Detach every transport-visible mutable value from the caller."""

    try:
        return replace(
            request,
            messages=tuple(
                ProviderMessage(
                    role=message.role,
                    content=message.content,
                )
                for message in request.messages
            ),
            response_schema=request.response_schema_payload(),
        )
    except Exception:
        raise ProviderEgressIntentError(
            "Provider request could not be detached safely."
        ) from None


class AuthorizedProviderRequest(Generic[OutputT]):
    """One-use permit carrying the only request an authorized transport may send."""

    __slots__ = (
        "_acknowledgement",
        "_clock",
        "_lock",
        "_request",
        "_seal",
        "_taken",
    )

    def __init__(
        self,
        acknowledgement: ProviderEgressAcknowledgement,
        request: StructuredJSONRequest[OutputT],
        *,
        _clock: Optional[Callable[[], datetime]] = None,
        _seal: object = None,
    ) -> None:
        if _seal is not _AUTHORIZED_PERMIT_SEAL:
            raise ProviderEgressAcknowledgementError(
                EgressRejectionReason.INVALID,
                "Authorized provider request permit is invalid.",
            )
        self._acknowledgement = acknowledgement
        self._clock = _utc_now if _clock is None else _clock
        self._request: Optional[StructuredJSONRequest[OutputT]] = request
        self._seal = _seal
        self._taken = False
        self._lock = threading.Lock()

    @property
    def acknowledgement(self) -> ProviderEgressAcknowledgement:
        return self._acknowledgement

    @property
    def is_taken(self) -> bool:
        with self._lock:
            return self._taken

    def take_request(self) -> StructuredJSONRequest[OutputT]:
        """Release the detached exact request once to the transport."""

        with self._lock:
            if (
                self._seal is not _AUTHORIZED_PERMIT_SEAL
                or self._taken
                or self._request is None
            ):
                raise ProviderEgressAcknowledgementError(
                    EgressRejectionReason.REPLAYED,
                    "Authorized provider request was already taken.",
                )
            try:
                checked_at = _utc_timestamp(self._clock(), "clock")
            except Exception:
                self._request = None
                self._taken = True
                raise ProviderEgressAcknowledgementError(
                    EgressRejectionReason.INVALID,
                    "Provider egress authorization time could not be validated.",
                ) from None
            if checked_at >= self._acknowledgement.expires_at:
                self._request = None
                self._taken = True
                raise ProviderEgressAcknowledgementError(
                    EgressRejectionReason.EXPIRED,
                    "Provider egress acknowledgement expired before transport.",
                )
            if checked_at < self._acknowledgement.issued_at:
                self._request = None
                self._taken = True
                raise ProviderEgressAcknowledgementError(
                    EgressRejectionReason.INVALID,
                    "Provider egress acknowledgement is not valid yet.",
                )
            request = self._request
            self._request = None
            self._taken = True
            return request

    def __repr__(self) -> str:
        return (
            "AuthorizedProviderRequest("
            "acknowledgement_id={0!r}, request=<redacted>, taken={1!r})"
        ).format(
            self._acknowledgement.acknowledgement_id,
            self.is_taken,
        )


class InMemoryProviderEgressAuthorizer:
    """Thread-safe, server-owned acknowledgement store for the prototype."""

    def __init__(
        self,
        policy: ProviderEgressPolicy,
        *,
        clock: Callable[[], datetime] = _utc_now,
        nonce_factory: Optional[Callable[[], str]] = None,
        capability_secret_factory: Optional[Callable[[], str]] = None,
        ttl: timedelta = DEFAULT_EGRESS_ACKNOWLEDGEMENT_TTL,
    ) -> None:
        if (
            type(policy) is not ProviderEgressPolicy
            or not policy.is_frozen_wave1_policy()
        ):
            raise ValueError(
                "Provider egress authorizer requires trusted frozen policy."
            )
        if not callable(clock):
            raise ValueError("Provider egress clock must be callable.")
        if nonce_factory is not None and not callable(nonce_factory):
            raise ValueError("Provider egress nonce factory must be callable.")
        if capability_secret_factory is not None and not callable(
            capability_secret_factory
        ):
            raise ValueError(
                "Provider egress capability factory must be callable."
            )
        if (
            not isinstance(ttl, timedelta)
            or ttl <= timedelta(0)
            or ttl > DEFAULT_EGRESS_ACKNOWLEDGEMENT_TTL
        ):
            raise ValueError(
                "Provider egress acknowledgement ttl must be positive "
                "and no longer than five minutes."
            )
        self._policy = policy
        self._clock = clock
        self._nonce_factory = nonce_factory or (
            lambda: secrets.token_urlsafe(24)
        )
        self._capability_secret_factory = capability_secret_factory or (
            lambda: secrets.token_urlsafe(32)
        )
        self._ttl = ttl
        self._records: Dict[str, _StoredAcknowledgement] = {}
        self._consumed: set[str] = set()
        self._lock = threading.Lock()

    def _read_clock(self) -> datetime:
        try:
            return _utc_timestamp(self._clock(), "clock")
        except Exception:
            raise ProviderEgressAcknowledgementError(
                EgressRejectionReason.INVALID,
                "Provider egress authorization time could not be validated.",
            ) from None

    @staticmethod
    def _new_capability_value(
        factory: Callable[[], str],
        *,
        maximum_characters: int,
    ) -> str:
        try:
            value = factory()
        except Exception:
            raise ProviderEgressAcknowledgementError(
                EgressRejectionReason.INVALID,
                "Provider egress capability could not be created.",
            ) from None
        if (
            not isinstance(value, str)
            or not value
            or len(value) > maximum_characters
            or any(character.isspace() for character in value)
            or not re.fullmatch(r"[A-Za-z0-9_-]+", value)
        ):
            raise ProviderEgressAcknowledgementError(
                EgressRejectionReason.INVALID,
                "Provider egress capability could not be created.",
            )
        return value

    @staticmethod
    def _validated_intent(
        request: StructuredJSONRequest[Any],
        *,
        policy: ProviderEgressPolicy,
        customer_terms: Sequence[str],
    ) -> ProviderEgressIntent:
        try:
            return build_provider_egress_intent(
                request,
                policy=policy,
                customer_terms=customer_terms,
            )
        except Exception:
            raise ProviderEgressAcknowledgementError(
                EgressRejectionReason.INTENT_MISMATCH,
                "Provider request is not authorized by frozen policy.",
            ) from None

    def issue(
        self,
        request: StructuredJSONRequest[Any],
        *,
        acknowledged: bool,
        customer_terms: Sequence[str] = (),
    ) -> Tuple[ProviderEgressAcknowledgement, str]:
        """Issue one capability after an explicit acknowledgement action."""

        if acknowledged is not True:
            raise ProviderEgressAcknowledgementError(
                EgressRejectionReason.NOT_ACKNOWLEDGED,
                "Provider egress requires explicit acknowledgement.",
            )
        intent = self._validated_intent(
            request,
            policy=self._policy,
            customer_terms=customer_terms,
        )
        issued_at = self._read_clock()
        expires_at = issued_at + self._ttl
        nonce = self._new_capability_value(
            self._nonce_factory,
            maximum_characters=500,
        )
        secret = self._new_capability_value(
            self._capability_secret_factory,
            maximum_characters=MAX_EGRESS_CAPABILITY_CHARACTERS,
        )
        try:
            binding_payload = provider_egress_binding_payload(
                intent,
                issued_at=issued_at,
                expires_at=expires_at,
                nonce=nonce,
            )
            binding_digest = _digest(_BINDING_DOMAIN, binding_payload)
            acknowledgement_id = "egress_{0}".format(binding_digest)
            raw_token = "{0}.{1}".format(acknowledgement_id, secret)
            if len(raw_token) > MAX_EGRESS_CAPABILITY_CHARACTERS:
                raise ValueError
            public = ProviderEgressAcknowledgement(
                acknowledgement_id=acknowledgement_id,
                binding_digest=binding_digest,
                intent=intent,
                issued_at=issued_at,
                expires_at=expires_at,
            )
            stored = _StoredAcknowledgement(
                public=public,
                token_digest=_token_digest(raw_token),
                nonce=nonce,
            )
        except Exception:
            raise ProviderEgressAcknowledgementError(
                EgressRejectionReason.INVALID,
                "Provider egress acknowledgement could not be created.",
            ) from None

        with self._lock:
            if acknowledgement_id in self._records:
                raise ProviderEgressAcknowledgementError(
                    EgressRejectionReason.INVALID,
                    "Provider egress acknowledgement already exists.",
                )
            self._records[acknowledgement_id] = stored
        return public, raw_token

    @staticmethod
    def _acknowledgement_id_from_token(token: object) -> str:
        if (
            not isinstance(token, str)
            or not token
            or len(token) > MAX_EGRESS_CAPABILITY_CHARACTERS
            or token.count(".") != 1
            or any(character.isspace() for character in token)
        ):
            raise ProviderEgressAcknowledgementError(
                EgressRejectionReason.INVALID,
                "Provider egress acknowledgement is invalid.",
            )
        acknowledgement_id, secret = token.split(".", 1)
        if (
            not re.fullmatch(r"egress_[a-f0-9]{64}", acknowledgement_id)
            or not secret
        ):
            raise ProviderEgressAcknowledgementError(
                EgressRejectionReason.INVALID,
                "Provider egress acknowledgement is invalid.",
            )
        return acknowledgement_id

    def authorize(
        self,
        token: object,
        request: StructuredJSONRequest[OutputT],
        *,
        customer_terms: Sequence[str] = (),
    ) -> AuthorizedProviderRequest[OutputT]:
        """Consume a capability and return an exact one-use transport permit."""

        acknowledgement_id = self._acknowledgement_id_from_token(token)
        intent = self._validated_intent(
            request,
            policy=self._policy,
            customer_terms=customer_terms,
        )
        try:
            detached_request = _clone_structured_request(request)
        except Exception:
            raise ProviderEgressAcknowledgementError(
                EgressRejectionReason.INTENT_MISMATCH,
                "Provider request is not authorized by frozen policy.",
            ) from None
        checked_at = self._read_clock()

        with self._lock:
            stored = self._records.get(acknowledgement_id)
            if stored is None or not hmac.compare_digest(
                stored.token_digest,
                _token_digest(token),
            ):
                raise ProviderEgressAcknowledgementError(
                    EgressRejectionReason.INVALID,
                    "Provider egress acknowledgement is invalid.",
                )
            public = stored.public
            if checked_at >= public.expires_at:
                raise ProviderEgressAcknowledgementError(
                    EgressRejectionReason.EXPIRED,
                    "Provider egress acknowledgement expired; review "
                    "and authorize the request again.",
                )
            if checked_at < public.issued_at:
                raise ProviderEgressAcknowledgementError(
                    EgressRejectionReason.INVALID,
                    "Provider egress acknowledgement is not valid yet.",
                )
            try:
                current_binding = _digest(
                    _BINDING_DOMAIN,
                    provider_egress_binding_payload(
                        intent,
                        issued_at=public.issued_at,
                        expires_at=public.expires_at,
                        nonce=stored.nonce,
                    ),
                )
            except Exception:
                raise ProviderEgressAcknowledgementError(
                    EgressRejectionReason.INTENT_MISMATCH,
                    "Provider request changed; review and authorize "
                    "the exact request again.",
                ) from None
            if not hmac.compare_digest(
                public.binding_digest,
                current_binding,
            ):
                raise ProviderEgressAcknowledgementError(
                    EgressRejectionReason.INTENT_MISMATCH,
                    "Provider request changed; review and authorize "
                    "the exact request again.",
                )
            if acknowledgement_id in self._consumed:
                raise ProviderEgressAcknowledgementError(
                    EgressRejectionReason.REPLAYED,
                    "Provider egress acknowledgement was already used; "
                    "review and authorize a new request.",
                )
            self._consumed.add(acknowledgement_id)

        return AuthorizedProviderRequest(
            public,
            detached_request,
            _clock=self._clock,
            _seal=_AUTHORIZED_PERMIT_SEAL,
        )

    def is_consumed(self, acknowledgement_id: str) -> bool:
        """Return one content-free state fact without accepting a token."""

        with self._lock:
            return acknowledgement_id in self._consumed
