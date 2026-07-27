"""Server-owned composition for one authorized Wave-1 assisted-authoring call.

This module does not read the environment and does not accept provider request
content from an HTTP client.  The web server supplies an explicitly enabled
credential configuration, then binds the private acknowledgement state created
by the disclosure flow to the permit-only Fireworks executor.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Optional

from .assisted_authoring import ProposalBatch
from .authorized_fireworks import AuthorizedFireworksExecutor
from .provider_egress import (
    InMemoryProviderEgressAuthorizer,
    ProviderEgressPolicy,
)
from .providers import (
    ProviderError,
    ProviderReceipt,
    StructuredJSONRequest,
    StructuredJSONResult,
)


WAVE1_FIREWORKS_ADAPTER = "fireworks_assisted_authoring"
WAVE1_FIREWORKS_ADAPTER_VERSION = "1"


def _valid_server_credential(value: object) -> bool:
    return bool(
        isinstance(value, str)
        and value
        and len(value) <= 4096
        and value == value.strip()
        and not any(character.isspace() for character in value)
    )


def wave1_terminal_receipt(
    *,
    policy: ProviderEgressPolicy,
    outcome_code: str,
    attempts: int,
    provider_receipt: Optional[ProviderReceipt] = None,
) -> dict[str, Any]:
    """Build the frozen content-free receipt for every terminal outcome."""

    if not isinstance(outcome_code, str) or not outcome_code:
        raise ValueError("Wave-1 receipt outcome code is invalid.")
    if type(attempts) is not int or attempts < 0:
        raise ValueError("Wave-1 receipt attempt count is invalid.")
    pricing_snapshot = policy.pricing_snapshot()
    pricing_version = "fireworks-standard-{0}".format(
        pricing_snapshot["effective_checked_at"]
    )
    return {
        "provider": policy.provider,
        "model": policy.model,
        "endpoint": policy.endpoint,
        "attempts": attempts,
        "latency_ms": (
            None if provider_receipt is None else provider_receipt.latency_ms
        ),
        "input_tokens": (
            None if provider_receipt is None else provider_receipt.input_tokens
        ),
        "output_tokens": (
            None if provider_receipt is None else provider_receipt.output_tokens
        ),
        "total_tokens": (
            None if provider_receipt is None else provider_receipt.total_tokens
        ),
        "estimated_cost_usd": (
            None
            if provider_receipt is None
            or provider_receipt.estimated_cost_usd is None
            else str(provider_receipt.estimated_cost_usd)
        ),
        "pricing_version": (
            pricing_version
            if provider_receipt is None
            else provider_receipt.pricing_version
        ),
        "outcome_code": outcome_code,
    }


class Wave1ProviderExecutionConfiguration:
    """Private server configuration with a content-free representation."""

    __slots__ = (
        "_api_key",
        "_connection_factory",
        "_monotonic",
        "_sleeper",
        "_wall_clock",
        "enabled",
    )

    def __init__(
        self,
        *,
        enabled: bool = False,
        api_key: object = None,
        connection_factory: Optional[Callable[..., Any]] = None,
        sleeper: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        if type(enabled) is not bool:
            raise ValueError("Wave-1 provider enablement must be a boolean.")
        if connection_factory is not None and not callable(connection_factory):
            raise ValueError("Wave-1 provider connection factory must be callable.")
        if not callable(sleeper) or not callable(monotonic) or not callable(
            wall_clock
        ):
            raise ValueError("Wave-1 provider execution clocks must be callable.")

        self.enabled = enabled
        self._api_key = (
            api_key if enabled and _valid_server_credential(api_key) else None
        )
        self._connection_factory = connection_factory
        self._sleeper = sleeper
        self._monotonic = monotonic
        self._wall_clock = wall_clock

    @property
    def configured(self) -> bool:
        return self.enabled and self._api_key is not None

    def bind(
        self,
        *,
        policy: ProviderEgressPolicy,
        authorizer: InMemoryProviderEgressAuthorizer,
        capability_token: str,
        request: StructuredJSONRequest[ProposalBatch],
    ) -> "Wave1AuthorizedAssistedExecutor":
        """Bind server-private authorization state to one assisted executor."""

        return Wave1AuthorizedAssistedExecutor(
            policy=policy,
            authorizer=authorizer,
            capability_token=capability_token,
            request=request,
            api_key=self._api_key if self.enabled else None,
            connection_factory=self._connection_factory,
            sleeper=self._sleeper,
            monotonic=self._monotonic,
            wall_clock=self._wall_clock,
        )

    def public_status(self) -> dict[str, bool]:
        return {
            "enabled": self.enabled,
            "configured": self.configured,
        }

    def __repr__(self) -> str:
        return (
            "Wave1ProviderExecutionConfiguration("
            "enabled={0!r}, configured={1!r}, api_key=<redacted>)"
        ).format(self.enabled, self.configured)


class Wave1AuthorizedAssistedExecutor:
    """Adapt the local authoring boundary to one private one-use permit."""

    __slots__ = (
        "_api_key",
        "_authorizer",
        "_capability_token",
        "_connection_factory",
        "_monotonic",
        "_policy",
        "_request",
        "_sleeper",
        "_started",
        "_wall_clock",
        "last_receipt",
        "permit_consumed",
        "provider_call_attempted",
    )

    def __init__(
        self,
        *,
        policy: ProviderEgressPolicy,
        authorizer: InMemoryProviderEgressAuthorizer,
        capability_token: str,
        request: StructuredJSONRequest[ProposalBatch],
        api_key: object,
        connection_factory: Optional[Callable[..., Any]],
        sleeper: Callable[[float], None],
        monotonic: Callable[[], float],
        wall_clock: Callable[[], float],
    ) -> None:
        self._policy = policy
        self._authorizer = authorizer
        self._capability_token = capability_token
        self._request = request
        self._api_key = api_key
        self._connection_factory = connection_factory
        self._sleeper = sleeper
        self._monotonic = monotonic
        self._wall_clock = wall_clock
        self._started = False
        self.permit_consumed = False
        self.provider_call_attempted = False
        self.last_receipt: Optional[ProviderReceipt] = None

    def execute(self) -> StructuredJSONResult[ProposalBatch]:
        """Authorize and execute only the exact request retained by the server."""

        if self._started:
            raise RuntimeError("Wave-1 provider execution was already started.")
        self._started = True

        # Constructing the executor validates configuration before the private
        # acknowledgement is consumed. Missing configuration therefore cannot
        # burn an otherwise valid five-minute authorization.
        executor = AuthorizedFireworksExecutor(
            policy=self._policy,
            api_key=self._api_key,
            connection_factory=self._connection_factory,
            sleeper=self._sleeper,
            monotonic=self._monotonic,
            wall_clock=self._wall_clock,
        )
        permit = self._authorizer.authorize(
            self._capability_token,
            self._request,
            customer_terms=(),
        )
        self.permit_consumed = True

        try:
            result = executor.execute(permit)
        except ProviderError as error:
            self.provider_call_attempted = error.attempts > 0
            self.last_receipt = error.receipt
            raise
        self.provider_call_attempted = True
        self.last_receipt = result.receipt
        return result

    def __repr__(self) -> str:
        return (
            "Wave1AuthorizedAssistedExecutor("
            "authorization=<redacted>, request=<server-owned>, "
            "api_key=<redacted>, started={0!r})"
        ).format(self._started)
