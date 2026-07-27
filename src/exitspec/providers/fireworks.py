"""Fireworks structured-output adapter with bounded, observable execution."""

from __future__ import annotations

import json
import math
import time
from datetime import timezone
from decimal import Decimal
from email.utils import parsedate_to_datetime
from typing import Any, Callable, Dict, Mapping, Optional, Tuple, TypeVar

from .base import (
    ProviderError,
    ProviderErrorCode,
    ProviderHTTPRequest,
    ProviderHTTPResponse,
    ProviderReceipt,
    ProviderRedirectError,
    ProviderTimeoutError,
    ProviderTransport,
    ProviderTransportError,
    StructuredJSONRequest,
    StructuredJSONResult,
    TokenPricing,
)


OutputT = TypeVar("OutputT")

FIREWORKS_CHAT_COMPLETIONS_ENDPOINT = (
    "https://api.fireworks.ai/inference/v1/chat/completions"
)


class FireworksProvider:
    """Execute strict JSON requests without making acceptance decisions.

    Network behavior is supplied by ``transport``. This class deliberately has
    no built-in network client, so importing or constructing it cannot make an
    external request.
    """

    provider_name = "fireworks"

    def __init__(
        self,
        *,
        transport: ProviderTransport,
        api_key: Optional[str] = None,
        endpoint: str = FIREWORKS_CHAT_COMPLETIONS_ENDPOINT,
        pricing: Optional[Mapping[str, TokenPricing]] = None,
        max_attempts: int = 3,
        base_backoff_seconds: float = 0.25,
        max_backoff_seconds: float = 4.0,
        max_retry_after_seconds: float = 10.0,
        sleeper: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        if not hasattr(transport, "send"):
            raise ValueError("transport must provide a send method.")
        if type(endpoint) is not str or endpoint != FIREWORKS_CHAT_COMPLETIONS_ENDPOINT:
            raise ValueError(
                "Fireworks endpoint must be the official chat-completions HTTPS endpoint."
            )
        if api_key is not None and (
            not isinstance(api_key, str)
            or not api_key
            or len(api_key) > 4096
            or api_key != api_key.strip()
            or any(character.isspace() for character in api_key)
        ):
            raise ValueError("api_key must be a nonblank string when provided.")
        if isinstance(max_attempts, bool) or not isinstance(max_attempts, int):
            raise ValueError("max_attempts must be an integer.")
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least one.")
        for name, value in (
            ("base_backoff_seconds", base_backoff_seconds),
            ("max_backoff_seconds", max_backoff_seconds),
            ("max_retry_after_seconds", max_retry_after_seconds),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or value < 0
            ):
                raise ValueError("{0} must be finite and nonnegative.".format(name))
        if max_backoff_seconds < base_backoff_seconds:
            raise ValueError("max_backoff_seconds cannot be below base backoff.")

        self._transport = transport
        self._api_key = api_key
        self.endpoint = FIREWORKS_CHAT_COMPLETIONS_ENDPOINT
        self._pricing = dict(pricing or {})
        self.max_attempts = max_attempts
        self.base_backoff_seconds = float(base_backoff_seconds)
        self.max_backoff_seconds = float(max_backoff_seconds)
        self.max_retry_after_seconds = float(max_retry_after_seconds)
        self._sleep = sleeper
        self._monotonic = monotonic
        self._wall_clock = wall_clock

    def __repr__(self) -> str:
        return (
            "FireworksProvider(endpoint={0!r}, max_attempts={1}, " "api_key=<redacted>)"
        ).format(self.endpoint, self.max_attempts)

    def execute(
        self, request: StructuredJSONRequest[OutputT]
    ) -> StructuredJSONResult[OutputT]:
        """Execute and locally validate one structured JSON request."""

        pricing = self._pricing.get(request.model)
        self._check_preflight_budget(request, pricing)
        http_request = self._build_http_request(request)
        started_at = self._monotonic()

        for attempt in range(1, self.max_attempts + 1):
            try:
                response = self._transport.send(http_request)
            except (ProviderTimeoutError, TimeoutError):
                if attempt < self.max_attempts:
                    self._sleep(self._backoff_delay(attempt, None))
                    continue
                raise self._exhausted_error(
                    attempts=attempt,
                    last_code=ProviderErrorCode.TIMEOUT,
                    safe_message="Provider timed out after the configured retry limit.",
                ) from None
            except ProviderRedirectError as error:
                raise ProviderError(
                    ProviderErrorCode.REDIRECT_REJECTED,
                    "Provider redirect was rejected before a follow-up request.",
                    status_code=error.status_code,
                    attempts=attempt,
                ) from None
            except ProviderTransportError:
                raise ProviderError(
                    ProviderErrorCode.TRANSPORT,
                    "Provider transport failed before a usable response was received.",
                    attempts=attempt,
                ) from None
            except Exception:
                raise ProviderError(
                    ProviderErrorCode.TRANSPORT,
                    "Provider transport failed before a usable response was received.",
                    attempts=attempt,
                ) from None

            if not isinstance(response, ProviderHTTPResponse):
                raise ProviderError(
                    ProviderErrorCode.TRANSPORT,
                    "Provider transport returned an unsupported response object.",
                    attempts=attempt,
                )

            retry_code = self._retryable_status_code(response.status_code)
            if retry_code is not None:
                request_id = _provider_request_id(response)
                if attempt < self.max_attempts:
                    retry_after = _header(response.headers, "retry-after")
                    self._sleep(self._backoff_delay(attempt, retry_after))
                    continue
                raise self._exhausted_error(
                    attempts=attempt,
                    last_code=retry_code,
                    safe_message="Provider remained unavailable after the configured retry limit.",
                    status_code=response.status_code,
                    provider_request_id=request_id,
                )

            self._raise_for_non_success(response, attempt)
            output, usage, request_id = self._parse_success(response, request, attempt)
            latency_ms = max(0.0, (self._monotonic() - started_at) * 1000.0)
            input_tokens, output_tokens, total_tokens = usage
            estimated_cost = _estimate_cost(pricing, input_tokens, output_tokens)
            receipt = ProviderReceipt(
                provider=self.provider_name,
                model=request.model,
                endpoint=self.endpoint,
                attempts=attempt,
                latency_ms=round(latency_ms, 3),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
                provider_request_id=request_id,
                estimated_cost_usd=estimated_cost,
                pricing_version=pricing.version if pricing is not None else None,
            )
            if (
                request.budget_usd is not None
                and estimated_cost is not None
                and estimated_cost > request.budget_usd
            ):
                raise ProviderError(
                    ProviderErrorCode.BUDGET_EXCEEDED,
                    "Actual token usage exceeded the configured budget ceiling.",
                    attempts=attempt,
                    provider_request_id=request_id,
                    receipt=receipt,
                )
            return StructuredJSONResult(output=output, receipt=receipt)

        raise AssertionError("Provider execution loop ended unexpectedly.")

    def _build_http_request(
        self, request: StructuredJSONRequest[OutputT]
    ) -> ProviderHTTPRequest:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "ExitSpec/0.1 provider-boundary",
        }
        if self._api_key:
            headers["Authorization"] = "Bearer {0}".format(self._api_key)

        body: Dict[str, Any] = {
            "model": request.model,
            "messages": [
                {"role": message.role, "content": message.content}
                for message in request.messages
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": request.schema_name,
                    "schema": request.response_schema_payload(),
                },
            },
            "temperature": request.temperature,
        }
        if request.max_output_tokens is not None:
            body["max_tokens"] = request.max_output_tokens
        return ProviderHTTPRequest(
            method="POST",
            url=self.endpoint,
            headers=headers,
            json_body=body,
            timeout_seconds=request.timeout_seconds,
        )

    def _check_preflight_budget(
        self,
        request: StructuredJSONRequest[OutputT],
        pricing: Optional[TokenPricing],
    ) -> None:
        if request.budget_usd is None or pricing is None:
            return
        if request.estimated_input_tokens is None and request.max_output_tokens is None:
            return
        known_input = request.estimated_input_tokens or 0
        known_output = request.max_output_tokens or 0
        estimated_ceiling = pricing.estimate(known_input, known_output)
        if estimated_ceiling > request.budget_usd:
            raise ProviderError(
                ProviderErrorCode.BUDGET_EXCEEDED,
                "Estimated request cost exceeds the configured budget ceiling.",
                attempts=0,
            )

    def _parse_success(
        self,
        response: ProviderHTTPResponse,
        request: StructuredJSONRequest[OutputT],
        attempt: int,
    ) -> Tuple[
        OutputT, Tuple[Optional[int], Optional[int], Optional[int]], Optional[str]
    ]:
        try:
            envelope = json.loads(response.body)
        except (TypeError, json.JSONDecodeError):
            raise ProviderError(
                ProviderErrorCode.MALFORMED_RESPONSE,
                "Provider returned a non-JSON response envelope.",
                attempts=attempt,
                provider_request_id=_provider_request_id(response),
            ) from None
        if not isinstance(envelope, dict):
            raise ProviderError(
                ProviderErrorCode.MALFORMED_RESPONSE,
                "Provider response envelope was not a JSON object.",
                attempts=attempt,
                provider_request_id=_provider_request_id(response),
            )

        request_id = _provider_request_id(response, envelope)
        try:
            choices = envelope["choices"]
            content = choices[0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise ProviderError(
                ProviderErrorCode.MALFORMED_RESPONSE,
                "Provider response did not contain structured message content.",
                attempts=attempt,
                provider_request_id=request_id,
            ) from None
        if not isinstance(content, str) or not content.strip():
            raise ProviderError(
                ProviderErrorCode.MALFORMED_RESPONSE,
                "Provider response did not contain structured message content.",
                attempts=attempt,
                provider_request_id=request_id,
            )
        try:
            parsed_output = json.loads(content)
        except json.JSONDecodeError:
            raise ProviderError(
                ProviderErrorCode.INVALID_OUTPUT,
                "Provider message content was not valid JSON.",
                attempts=attempt,
                provider_request_id=request_id,
            ) from None
        if not isinstance(parsed_output, dict):
            raise ProviderError(
                ProviderErrorCode.INVALID_OUTPUT,
                "Provider message content was not a JSON object.",
                attempts=attempt,
                provider_request_id=request_id,
            )
        try:
            request.validate_response_instance(parsed_output)
        except Exception:
            raise ProviderError(
                ProviderErrorCode.INVALID_OUTPUT,
                "Provider JSON did not conform to the declared response schema.",
                attempts=attempt,
                provider_request_id=request_id,
            ) from None
        try:
            output = request.validate_output(parsed_output)
        except Exception:
            raise ProviderError(
                ProviderErrorCode.INVALID_OUTPUT,
                "Provider JSON failed local typed conversion.",
                attempts=attempt,
                provider_request_id=request_id,
            ) from None

        usage = _parse_usage(envelope.get("usage"), attempt, request_id)
        return output, usage, request_id

    def _raise_for_non_success(
        self, response: ProviderHTTPResponse, attempt: int
    ) -> None:
        status = response.status_code
        if 200 <= status < 300:
            return
        request_id = _provider_request_id(response)
        if status in {401, 403}:
            code = ProviderErrorCode.AUTHENTICATION
            message = "Provider rejected authentication or authorization."
        elif 400 <= status < 500:
            code = ProviderErrorCode.CLIENT_REQUEST
            message = "Provider rejected the request without a retryable status."
        elif status >= 500:
            code = ProviderErrorCode.SERVICE_ERROR
            message = "Provider returned a non-retryable server error."
        else:
            code = ProviderErrorCode.MALFORMED_RESPONSE
            message = "Provider returned an unexpected HTTP status."
        raise ProviderError(
            code,
            message,
            status_code=status,
            attempts=attempt,
            provider_request_id=request_id,
        )

    def _retryable_status_code(self, status_code: int) -> Optional[ProviderErrorCode]:
        if status_code == 429:
            return ProviderErrorCode.RATE_LIMITED
        if status_code == 503:
            return ProviderErrorCode.SERVICE_UNAVAILABLE
        return None

    def _backoff_delay(self, attempt: int, retry_after_header: Optional[str]) -> float:
        retry_after = self._parse_retry_after(retry_after_header)
        if retry_after is not None:
            return min(retry_after, self.max_retry_after_seconds)
        exponential = self.base_backoff_seconds * (2 ** (attempt - 1))
        return min(exponential, self.max_backoff_seconds)

    def _parse_retry_after(self, value: Optional[str]) -> Optional[float]:
        if value is None:
            return None
        try:
            seconds = float(value.strip())
            if math.isfinite(seconds) and seconds >= 0:
                return seconds
        except (TypeError, ValueError):
            pass
        try:
            retry_at = parsedate_to_datetime(value)
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=timezone.utc)
            return max(0.0, retry_at.timestamp() - self._wall_clock())
        except Exception:
            return None

    def _exhausted_error(
        self,
        *,
        attempts: int,
        last_code: ProviderErrorCode,
        safe_message: str,
        status_code: Optional[int] = None,
        provider_request_id: Optional[str] = None,
    ) -> ProviderError:
        return ProviderError(
            ProviderErrorCode.RETRIES_EXHAUSTED,
            safe_message,
            retryable=True,
            status_code=status_code,
            attempts=attempts,
            last_code=last_code,
            provider_request_id=provider_request_id,
        )


def _parse_usage(
    usage: Any, attempt: int, request_id: Optional[str]
) -> Tuple[Optional[int], Optional[int], Optional[int]]:
    if usage is None:
        return None, None, None
    if not isinstance(usage, dict):
        raise ProviderError(
            ProviderErrorCode.MALFORMED_RESPONSE,
            "Provider usage metadata was malformed.",
            attempts=attempt,
            provider_request_id=request_id,
        )
    input_tokens = _optional_token_count(usage.get("prompt_tokens"))
    output_tokens = _optional_token_count(usage.get("completion_tokens"))
    total_tokens = _optional_token_count(usage.get("total_tokens"))
    if any(
        value is _INVALID_TOKEN_COUNT
        for value in (input_tokens, output_tokens, total_tokens)
    ):
        raise ProviderError(
            ProviderErrorCode.MALFORMED_RESPONSE,
            "Provider usage metadata was malformed.",
            attempts=attempt,
            provider_request_id=request_id,
        )
    return input_tokens, output_tokens, total_tokens


_INVALID_TOKEN_COUNT = object()


def _optional_token_count(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return _INVALID_TOKEN_COUNT
    return value


def _estimate_cost(
    pricing: Optional[TokenPricing],
    input_tokens: Optional[int],
    output_tokens: Optional[int],
) -> Optional[Decimal]:
    if pricing is None or input_tokens is None or output_tokens is None:
        return None
    return pricing.estimate(input_tokens, output_tokens)


def _header(headers: Mapping[str, str], name: str) -> Optional[str]:
    lowered = name.lower()
    for key, value in headers.items():
        if key.lower() == lowered:
            return value
    return None


def _provider_request_id(
    response: ProviderHTTPResponse, envelope: Optional[Mapping[str, Any]] = None
) -> Optional[str]:
    for header_name in ("x-request-id", "request-id"):
        value = _header(response.headers, header_name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    if envelope is not None:
        value = envelope.get("id")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None
