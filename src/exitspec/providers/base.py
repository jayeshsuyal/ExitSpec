"""Typed, provider-neutral execution boundary for structured JSON generation.

The types in this module intentionally stop at provider execution. They do not
import ExitSpec contract or verdict models, which keeps providers incapable of
approving requirements or making acceptance decisions.
"""

from __future__ import annotations

import copy
import json
import math
import re
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Any, Callable, Generic, Mapping, Optional, Protocol, Tuple, TypeVar

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError
from referencing import Registry


OutputT = TypeVar("OutputT")


class ProviderErrorCode(str, Enum):
    """Stable machine-readable provider failure categories."""

    CONFIGURATION = "configuration_error"
    AUTHENTICATION = "authentication_error"
    ACCOUNT_UNAVAILABLE = "account_unavailable"
    PRECONDITION_FAILED = "precondition_failed"
    CLIENT_REQUEST = "client_request_error"
    RATE_LIMITED = "rate_limited"
    SERVICE_UNAVAILABLE = "service_unavailable"
    SERVICE_ERROR = "service_error"
    TIMEOUT = "timeout"
    TRANSPORT = "transport_error"
    MALFORMED_RESPONSE = "malformed_response"
    INVALID_OUTPUT = "invalid_output"
    BUDGET_EXCEEDED = "budget_exceeded"
    RETRIES_EXHAUSTED = "retries_exhausted"
    REDIRECT_REJECTED = "redirect_rejected"


class ProviderNextAction(str, Enum):
    """Content-free operator actions for provider failures."""

    CONFIGURE_PROVIDER = "configure_provider"
    CHECK_CREDENTIAL = "check_provider_credential"
    RESTORE_ACCOUNT = "restore_provider_account"
    REVIEW_PRECONDITION = "review_provider_precondition"
    REVIEW_REQUEST = "review_request"
    RETRY_LATER = "retry_later"
    CONTACT_PROVIDER = "contact_provider"
    CHECK_CONNECTIVITY = "check_provider_connectivity"
    REVIEW_OUTPUT = "review_provider_output"
    REDUCE_REQUEST = "reduce_request"
    REVIEW_DESTINATION = "review_provider_destination"


_PROVIDER_NEXT_ACTIONS = {
    ProviderErrorCode.CONFIGURATION: ProviderNextAction.CONFIGURE_PROVIDER,
    ProviderErrorCode.AUTHENTICATION: ProviderNextAction.CHECK_CREDENTIAL,
    ProviderErrorCode.ACCOUNT_UNAVAILABLE: ProviderNextAction.RESTORE_ACCOUNT,
    ProviderErrorCode.PRECONDITION_FAILED: ProviderNextAction.REVIEW_PRECONDITION,
    ProviderErrorCode.CLIENT_REQUEST: ProviderNextAction.REVIEW_REQUEST,
    ProviderErrorCode.RATE_LIMITED: ProviderNextAction.RETRY_LATER,
    ProviderErrorCode.SERVICE_UNAVAILABLE: ProviderNextAction.RETRY_LATER,
    ProviderErrorCode.SERVICE_ERROR: ProviderNextAction.CONTACT_PROVIDER,
    ProviderErrorCode.TIMEOUT: ProviderNextAction.RETRY_LATER,
    ProviderErrorCode.TRANSPORT: ProviderNextAction.CHECK_CONNECTIVITY,
    ProviderErrorCode.MALFORMED_RESPONSE: ProviderNextAction.REVIEW_OUTPUT,
    ProviderErrorCode.INVALID_OUTPUT: ProviderNextAction.REVIEW_OUTPUT,
    ProviderErrorCode.BUDGET_EXCEEDED: ProviderNextAction.REDUCE_REQUEST,
    ProviderErrorCode.RETRIES_EXHAUSTED: ProviderNextAction.RETRY_LATER,
    ProviderErrorCode.REDIRECT_REJECTED: ProviderNextAction.REVIEW_DESTINATION,
}


@dataclass(frozen=True)
class ProviderMessage:
    """One OpenAI-compatible chat message."""

    role: str
    content: str = field(repr=False)

    def __post_init__(self) -> None:
        if self.role not in {"system", "user", "assistant"}:
            raise ValueError(
                "Provider message role must be system, user, or assistant."
            )
        if not isinstance(self.content, str) or not self.content.strip():
            raise ValueError("Provider message content cannot be blank.")


@dataclass(frozen=True, repr=False)
class StructuredJSONRequest(Generic[OutputT]):
    """A pinned structured-output request with mandatory local validation.

    ``response_schema`` expresses the strict JSON-schema intent sent to the
    provider. ``validate_output`` is the local trust boundary: the provider's
    claim that output is structured is never accepted without this callback.
    """

    model: str
    messages: Tuple[ProviderMessage, ...] = field(repr=False)
    schema_name: str
    response_schema: Mapping[str, Any] = field(repr=False)
    validate_output: Callable[[Mapping[str, Any]], OutputT] = field(
        repr=False, compare=False
    )
    max_output_tokens: Optional[int] = None
    estimated_input_tokens: Optional[int] = None
    budget_usd: Optional[Decimal] = None
    timeout_seconds: float = 30.0
    temperature: float = 0.0
    _response_schema_json: str = field(init=False, repr=False)
    _response_validator: Draft202012Validator = field(
        init=False, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        if not isinstance(self.model, str) or not self.model.strip():
            raise ValueError("A pinned provider model is required.")
        normalized_messages = tuple(self.messages)
        if not normalized_messages or any(
            not isinstance(message, ProviderMessage) for message in normalized_messages
        ):
            raise ValueError("At least one typed provider message is required.")
        object.__setattr__(self, "messages", normalized_messages)

        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", self.schema_name):
            raise ValueError("schema_name must be a portable identifier.")
        if not isinstance(self.response_schema, Mapping):
            raise ValueError("response_schema must be a JSON object.")
        try:
            schema_copy = copy.deepcopy(dict(self.response_schema))
            schema_json = json.dumps(
                schema_copy,
                allow_nan=False,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            validation_schema = json.loads(schema_json)
        except Exception:
            raise ValueError(
                "response_schema must contain only finite JSON values."
            ) from None
        if validation_schema.get("type") != "object":
            raise ValueError("The structured response schema must have type 'object'.")
        _require_local_schema_references(validation_schema)
        try:
            Draft202012Validator.check_schema(validation_schema)
            response_validator = Draft202012Validator(
                validation_schema,
                # An empty Registry has a fail-to-retrieve policy. Combined with
                # the local-reference precheck, schema evaluation cannot fetch.
                registry=Registry(),
            )
        except SchemaError:
            raise ValueError(
                "response_schema must be valid JSON Schema Draft 2020-12."
            ) from None
        except Exception:
            raise ValueError(
                "response_schema could not be prepared for local validation."
            ) from None

        object.__setattr__(self, "response_schema", json.loads(schema_json))
        object.__setattr__(self, "_response_schema_json", schema_json)
        object.__setattr__(self, "_response_validator", response_validator)

        if not callable(self.validate_output):
            raise ValueError("A local output validator is required.")
        _require_optional_positive_int(self.max_output_tokens, "max_output_tokens")
        _require_optional_nonnegative_int(
            self.estimated_input_tokens, "estimated_input_tokens"
        )
        if (
            not isinstance(self.timeout_seconds, (int, float))
            or isinstance(self.timeout_seconds, bool)
            or not math.isfinite(float(self.timeout_seconds))
            or self.timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be a finite positive number.")
        if (
            not isinstance(self.temperature, (int, float))
            or isinstance(self.temperature, bool)
            or not math.isfinite(float(self.temperature))
            or not 0.0 <= float(self.temperature) <= 2.0
        ):
            raise ValueError("temperature must be between 0 and 2.")
        if self.budget_usd is not None:
            budget = _to_nonnegative_decimal(self.budget_usd, "budget_usd")
            object.__setattr__(self, "budget_usd", budget)

    def __repr__(self) -> str:
        return (
            "StructuredJSONRequest("
            "model={0!r}, schema_name={1!r}, messages=<{2} redacted>, "
            "response_schema=<redacted>, max_output_tokens={3!r}, "
            "estimated_input_tokens={4!r}, budget_usd={5!r}, "
            "timeout_seconds={6!r}, temperature={7!r})"
        ).format(
            self.model,
            self.schema_name,
            len(self.messages),
            self.max_output_tokens,
            self.estimated_input_tokens,
            self.budget_usd,
            self.timeout_seconds,
            self.temperature,
        )

    def response_schema_payload(self) -> Mapping[str, Any]:
        """Return the immutable-at-construction schema snapshot for transport."""

        return json.loads(self._response_schema_json)

    def validate_response_instance(self, instance: Mapping[str, Any]) -> None:
        """Validate provider JSON without exposing schema or instance details."""

        try:
            self._response_validator.validate(instance)
        except ValidationError:
            raise ValueError(
                "Provider JSON did not conform to the declared response schema."
            ) from None
        except Exception:
            raise ValueError(
                "Provider JSON schema evaluation could not be completed safely."
            ) from None


@dataclass(frozen=True)
class TokenPricing:
    """Injected USD token pricing; never fetched implicitly by the adapter."""

    input_usd_per_million: Decimal
    output_usd_per_million: Decimal
    version: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "input_usd_per_million",
            _to_nonnegative_decimal(
                self.input_usd_per_million, "input_usd_per_million"
            ),
        )
        object.__setattr__(
            self,
            "output_usd_per_million",
            _to_nonnegative_decimal(
                self.output_usd_per_million, "output_usd_per_million"
            ),
        )
        if not isinstance(self.version, str) or not self.version.strip():
            raise ValueError("Pricing version cannot be blank.")

    def estimate(self, input_tokens: int, output_tokens: int) -> Decimal:
        _require_nonnegative_int(input_tokens, "input_tokens")
        _require_nonnegative_int(output_tokens, "output_tokens")
        million = Decimal("1000000")
        return (
            Decimal(input_tokens) * self.input_usd_per_million
            + Decimal(output_tokens) * self.output_usd_per_million
        ) / million


@dataclass(frozen=True, repr=False)
class ProviderReceipt:
    """Provider execution facts suitable for attaching to later evidence."""

    provider: str
    model: str
    endpoint: str
    attempts: int
    latency_ms: float
    input_tokens: Optional[int]
    output_tokens: Optional[int]
    total_tokens: Optional[int]
    provider_request_id: Optional[str] = field(repr=False)
    estimated_cost_usd: Optional[Decimal]
    pricing_version: Optional[str]

    def __repr__(self) -> str:
        return (
            "ProviderReceipt(provider={0!r}, model={1!r}, endpoint={2!r}, "
            "attempts={3!r}, latency_ms={4!r}, input_tokens={5!r}, "
            "output_tokens={6!r}, total_tokens={7!r}, "
            "provider_request_id=<redacted>, estimated_cost_usd={8!r}, "
            "pricing_version={9!r})"
        ).format(
            self.provider,
            self.model,
            self.endpoint,
            self.attempts,
            self.latency_ms,
            self.input_tokens,
            self.output_tokens,
            self.total_tokens,
            self.estimated_cost_usd,
            self.pricing_version,
        )


@dataclass(frozen=True, repr=False)
class StructuredJSONResult(Generic[OutputT]):
    """Locally validated provider output plus a non-content execution receipt."""

    output: OutputT = field(repr=False)
    receipt: ProviderReceipt

    def __repr__(self) -> str:
        return "StructuredJSONResult(output=<redacted>, receipt={0!r})".format(
            self.receipt
        )


@dataclass(frozen=True, repr=False)
class ProviderHTTPRequest:
    """Secret-safe request passed to an injected HTTP transport."""

    method: str
    url: str
    headers: Mapping[str, str] = field(repr=False)
    json_body: Mapping[str, Any] = field(repr=False)
    timeout_seconds: float

    def __repr__(self) -> str:
        return (
            "ProviderHTTPRequest(method={0!r}, url={1!r}, "
            "headers=<redacted>, json_body=<redacted>, timeout_seconds={2!r})"
        ).format(self.method, self.url, self.timeout_seconds)


@dataclass(frozen=True, repr=False)
class ProviderHTTPResponse:
    """HTTP response whose body and headers are omitted from representations."""

    status_code: int
    headers: Mapping[str, str] = field(default_factory=dict, repr=False)
    body: str = field(default="", repr=False)

    def __post_init__(self) -> None:
        if (
            isinstance(self.status_code, bool)
            or not isinstance(self.status_code, int)
            or not 100 <= self.status_code <= 599
        ):
            raise ValueError("Provider HTTP status must be an integer from 100 to 599.")
        if not isinstance(self.headers, Mapping) or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in self.headers.items()
        ):
            raise ValueError("Provider HTTP headers must map strings to strings.")
        if not isinstance(self.body, str):
            raise ValueError("Provider HTTP body must be decoded text.")
        object.__setattr__(self, "headers", dict(self.headers))

    def __repr__(self) -> str:
        return "ProviderHTTPResponse(status_code={0}, headers=<redacted>, body=<redacted>)".format(
            self.status_code
        )


class ProviderTransport(Protocol):
    """Minimal injectable transport; implementations own actual network I/O."""

    def send(self, request: ProviderHTTPRequest) -> ProviderHTTPResponse: ...


class ProviderTimeoutError(TimeoutError):
    """A transport timed out before a provider response was available."""


class ProviderTransportError(RuntimeError):
    """A non-timeout transport failure."""


class ProviderRedirectError(ProviderTransportError):
    """A first-hop redirect refused before any follow-up request."""

    def __init__(self, status_code: int) -> None:
        if (
            isinstance(status_code, bool)
            or not isinstance(status_code, int)
            or not 300 <= status_code <= 399
        ):
            raise ValueError("Redirect status must be an integer from 300 to 399.")
        self.status_code = status_code
        super().__init__("Provider redirect was rejected.")

    def __repr__(self) -> str:
        return "ProviderRedirectError(status_code={0})".format(self.status_code)


class ProviderError(RuntimeError):
    """Sanitized execution error with stable machine-readable metadata."""

    def __init__(
        self,
        code: ProviderErrorCode,
        safe_message: str,
        *,
        retryable: bool = False,
        status_code: Optional[int] = None,
        attempts: int = 0,
        last_code: Optional[ProviderErrorCode] = None,
        provider_request_id: Optional[str] = None,
        receipt: Optional[ProviderReceipt] = None,
    ) -> None:
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message
        self.retryable = retryable
        self.status_code = status_code
        self.attempts = attempts
        self.last_code = last_code
        self.provider_request_id = provider_request_id
        self.receipt = receipt

    @property
    def next_action(self) -> ProviderNextAction:
        """Return the fixed content-free action for this failure category."""

        return _PROVIDER_NEXT_ACTIONS[self.code]

    def __str__(self) -> str:
        return "{0}: {1}".format(self.code.value, self.safe_message)

    def __repr__(self) -> str:
        return (
            "ProviderError(code={0!r}, safe_message={1!r}, retryable={2!r}, "
            "status_code={3!r}, attempts={4!r}, last_code={5!r}, "
            "provider_request_id=<redacted>, receipt={6!r})"
        ).format(
            self.code,
            self.safe_message,
            self.retryable,
            self.status_code,
            self.attempts,
            self.last_code,
            self.receipt,
        )


def _to_nonnegative_decimal(value: Any, field_name: str) -> Decimal:
    try:
        decimal_value = Decimal(str(value))
    except Exception:
        raise ValueError("{0} must be a decimal number.".format(field_name)) from None
    if not decimal_value.is_finite() or decimal_value < 0:
        raise ValueError("{0} must be finite and nonnegative.".format(field_name))
    return decimal_value


def _require_nonnegative_int(value: int, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("{0} must be a nonnegative integer.".format(field_name))


def _require_optional_nonnegative_int(value: Optional[int], field_name: str) -> None:
    if value is not None:
        _require_nonnegative_int(value, field_name)


def _require_optional_positive_int(value: Optional[int], field_name: str) -> None:
    if value is not None:
        _require_nonnegative_int(value, field_name)
        if value == 0:
            raise ValueError("{0} must be positive when provided.".format(field_name))


def _require_local_schema_references(value: Any) -> None:
    """Reject every reference that could resolve outside the supplied schema."""

    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"$ref", "$dynamicRef"} and (
                not isinstance(child, str) or not child.startswith("#")
            ):
                raise ValueError(
                    "response_schema must use only local fragment references."
                )
            _require_local_schema_references(child)
    elif isinstance(value, list):
        for child in value:
            _require_local_schema_references(child)
