"""Permit-only composition for the frozen Wave-1 Fireworks request.

The generic ``FireworksProvider`` remains independently testable with injected
transports.  This composition is the only boundary intended for future live
server wiring: it accepts an ``AuthorizedProviderRequest`` rather than a raw
structured request, takes the detached request once, revalidates it against the
code-pinned Wave-1 policy, and applies the manifest's retry and pricing limits.
"""

from __future__ import annotations

import time
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Optional, TypeVar

from .provider_egress import (
    AuthorizedProviderRequest,
    EgressRejectionReason,
    ProviderEgressAcknowledgementError,
    ProviderEgressPolicy,
    build_provider_egress_intent,
)
from .providers import (
    FIREWORKS_CHAT_COMPLETIONS_ENDPOINT,
    FireworksProvider,
    ProviderError,
    ProviderErrorCode,
    ProviderReceipt,
    PinnedFireworksHTTPSTransport,
    StructuredJSONResult,
    TokenPricing,
)


OutputT = TypeVar("OutputT")


def _credential_error() -> ProviderError:
    return ProviderError(
        ProviderErrorCode.CONFIGURATION,
        "Fireworks credential is missing or invalid.",
        attempts=0,
    )


def _require_server_credential(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 4096
        or value != value.strip()
        or any(character.isspace() for character in value)
    ):
        raise _credential_error() from None
    return value


def _pricing_decimal(value: object) -> Decimal:
    if isinstance(value, bool):
        raise ValueError
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError from None
    if not parsed.is_finite() or parsed < 0:
        raise ValueError
    return parsed


def _wave1_pricing(policy: ProviderEgressPolicy) -> TokenPricing:
    try:
        snapshot = policy.pricing_snapshot()
        if (
            snapshot["currency"] != "USD"
            or snapshot["unit"] != "per_1_million_tokens"
        ):
            raise ValueError
        version = "fireworks-standard-{0}".format(
            snapshot["effective_checked_at"]
        )
        return TokenPricing(
            input_usd_per_million=_pricing_decimal(snapshot["input"]),
            output_usd_per_million=_pricing_decimal(snapshot["output"]),
            version=version,
        )
    except (KeyError, TypeError, ValueError):
        raise ValueError("Frozen Fireworks pricing policy is invalid.") from None


def _content_free_receipt(receipt: ProviderReceipt) -> ProviderReceipt:
    return ProviderReceipt(
        provider=receipt.provider,
        model=receipt.model,
        endpoint=receipt.endpoint,
        attempts=receipt.attempts,
        latency_ms=receipt.latency_ms,
        input_tokens=receipt.input_tokens,
        output_tokens=receipt.output_tokens,
        total_tokens=receipt.total_tokens,
        provider_request_id=None,
        estimated_cost_usd=receipt.estimated_cost_usd,
        pricing_version=receipt.pricing_version,
    )


def _content_free_wave1_error(error: ProviderError) -> ProviderError:
    code = error.code
    safe_message = error.safe_message
    if (
        error.code == ProviderErrorCode.PRECONDITION_FAILED
        and error.status_code == 412
    ):
        # This executor accepts only the frozen provider-owned base model, so
        # Fireworks' alternate 412 meaning for a failed LoRA load is excluded.
        code = ProviderErrorCode.ACCOUNT_UNAVAILABLE
        safe_message = "Frozen Wave-1 provider account is unavailable."
    return ProviderError(
        code,
        safe_message,
        retryable=error.retryable,
        status_code=error.status_code,
        attempts=error.attempts,
        last_code=error.last_code,
        provider_request_id=None,
        receipt=(
            None
            if error.receipt is None
            else _content_free_receipt(error.receipt)
        ),
    )


class AuthorizedFireworksExecutor:
    """Execute only a sealed, unexpired request permit under Wave-1 limits."""

    provider_name = "fireworks"

    def __init__(
        self,
        *,
        policy: ProviderEgressPolicy,
        api_key: object,
        connection_factory: Optional[Callable[..., Any]] = None,
        sleeper: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        if (
            type(policy) is not ProviderEgressPolicy
            or not policy.is_frozen_wave1_policy()
            or policy.provider != self.provider_name
            or policy.endpoint != FIREWORKS_CHAT_COMPLETIONS_ENDPOINT
        ):
            raise ValueError(
                "Authorized Fireworks execution requires frozen Wave-1 policy."
            )
        if not callable(sleeper) or not callable(monotonic) or not callable(
            wall_clock
        ):
            raise ValueError("Fireworks execution clocks must be callable.")

        credential = _require_server_credential(api_key)
        limits = policy.request_limits()
        pricing = _wave1_pricing(policy)
        transport = PinnedFireworksHTTPSTransport(
            connection_factory=connection_factory
        )
        self._policy = policy
        self._provider = FireworksProvider(
            transport=transport,
            api_key=credential,
            endpoint=policy.endpoint,
            pricing={policy.model: pricing},
            max_attempts=limits["max_attempts"],
            max_retry_after_seconds=limits["max_retry_after_seconds"],
            sleeper=sleeper,
            monotonic=monotonic,
            wall_clock=wall_clock,
        )
        self.model = policy.model
        self.endpoint = policy.endpoint
        self.max_attempts = limits["max_attempts"]
        self.max_request_cost_usd = policy.max_request_cost_usd

    def execute(
        self,
        permit: AuthorizedProviderRequest[OutputT],
    ) -> StructuredJSONResult[OutputT]:
        """Take, revalidate, and execute the permit's exact request once."""

        if type(permit) is not AuthorizedProviderRequest:
            raise ProviderEgressAcknowledgementError(
                EgressRejectionReason.INVALID,
                "Provider execution requires one authorized request permit.",
            )
        request = permit.take_request()
        intent_rejected = False
        try:
            build_provider_egress_intent(
                request,
                policy=self._policy,
                customer_terms=(),
            )
        except Exception:
            intent_rejected = True
        if intent_rejected:
            raise ProviderEgressAcknowledgementError(
                EgressRejectionReason.INTENT_MISMATCH,
                "Authorized provider request no longer matches frozen policy.",
            ) from None

        provider_failure: Optional[ProviderError] = None
        try:
            result = self._provider.execute(request)
        except ProviderError as error:
            provider_failure = _content_free_wave1_error(error)
        if provider_failure is not None:
            # Raising after the handler prevents the original provider exception
            # graph from remaining reachable through ``__context__``.
            raise provider_failure from None
        return StructuredJSONResult(
            output=result.output,
            receipt=_content_free_receipt(result.receipt),
        )

    def __repr__(self) -> str:
        return (
            "AuthorizedFireworksExecutor("
            "provider='fireworks', model={0!r}, endpoint={1!r}, "
            "max_attempts={2!r}, api_key=<redacted>)"
        ).format(
            self.model,
            self.endpoint,
            self.max_attempts,
        )
