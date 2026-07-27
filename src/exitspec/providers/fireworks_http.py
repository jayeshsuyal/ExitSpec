"""Pinned, redirect-refusing HTTPS transport for Fireworks chat completions."""

from __future__ import annotations

import http.client
import math
from collections.abc import Callable, Mapping
from typing import Any, Final

from exitspec.canonical import canonical_json_bytes

from .base import (
    ProviderHTTPRequest,
    ProviderHTTPResponse,
    ProviderRedirectError,
    ProviderTimeoutError,
    ProviderTransportError,
)
from .fireworks import FIREWORKS_CHAT_COMPLETIONS_ENDPOINT


_FIREWORKS_HOST: Final = "api.fireworks.ai"
_FIREWORKS_PORT: Final = 443
_FIREWORKS_PATH: Final = "/inference/v1/chat/completions"
_MAX_TIMEOUT_SECONDS: Final = 30.0
_DEFAULT_BODY_LIMIT_BYTES: Final = 1024 * 1024
_MAX_HEADER_VALUE_BYTES: Final = 8192

_REQUIRED_HEADERS: Final = {
    "Accept",
    "Authorization",
    "Content-Type",
    "User-Agent",
}
_EXPECTED_USER_AGENT: Final = "ExitSpec/0.1 provider-boundary"

_REQUEST_REJECTED = "Provider HTTPS request was rejected."
_REQUEST_TIMED_OUT = "Provider HTTPS request timed out."
_TRANSPORT_FAILED = "Provider HTTPS transport failed."


class PinnedFireworksHTTPSTransport:
    """Send one bounded POST to the official Fireworks HTTPS endpoint.

    Construction is side-effect free. A connection is created only after the
    complete request has been validated and detached from caller-owned
    mappings.
    """

    __slots__ = (
        "__connection_factory",
        "__max_request_body_bytes",
        "__max_response_body_bytes",
    )

    def __init__(
        self,
        *,
        connection_factory: Callable[..., Any] | None = None,
        max_request_body_bytes: int = _DEFAULT_BODY_LIMIT_BYTES,
        max_response_body_bytes: int = _DEFAULT_BODY_LIMIT_BYTES,
    ) -> None:
        if connection_factory is not None and not callable(connection_factory):
            raise TypeError("connection_factory must be callable.")
        _validate_body_limit(max_request_body_bytes)
        _validate_body_limit(max_response_body_bytes)

        self.__connection_factory = (
            http.client.HTTPSConnection
            if connection_factory is None
            else connection_factory
        )
        self.__max_request_body_bytes = max_request_body_bytes
        self.__max_response_body_bytes = max_response_body_bytes

    def __repr__(self) -> str:
        return (
            "PinnedFireworksHTTPSTransport("
            "connection_factory=<redacted>, "
            "max_request_body_bytes={0}, "
            "max_response_body_bytes={1})"
        ).format(
            self.__max_request_body_bytes,
            self.__max_response_body_bytes,
        )

    def send(self, request: ProviderHTTPRequest) -> ProviderHTTPResponse:
        """Validate, send exactly once, and return a detached bounded response."""

        try:
            body, headers, timeout_seconds = self.__prepare_request(request)
        except Exception:
            raise ProviderTransportError(_REQUEST_REJECTED) from None

        connection: Any | None = None
        response: Any | None = None
        result: ProviderHTTPResponse | None = None
        pending_error: Exception | None = None

        try:
            connection = self.__connection_factory(
                _FIREWORKS_HOST,
                _FIREWORKS_PORT,
                timeout=timeout_seconds,
            )
            connection.request(
                "POST",
                _FIREWORKS_PATH,
                body=body,
                headers=headers,
            )
            response = connection.getresponse()
            status_code = _response_status(response)

            if 300 <= status_code <= 399:
                pending_error = ProviderRedirectError(status_code)
            else:
                detached_headers = _detach_response_headers(response)
                response_body = _read_bounded_response(
                    response,
                    self.__max_response_body_bytes,
                )
                result = ProviderHTTPResponse(
                    status_code=status_code,
                    headers=detached_headers,
                    body=response_body,
                )
        except TimeoutError:
            pending_error = ProviderTimeoutError(_REQUEST_TIMED_OUT)
        except Exception:
            pending_error = ProviderTransportError(_TRANSPORT_FAILED)
        finally:
            close_failed = _close_response_and_connection(response, connection)

        if pending_error is not None:
            raise pending_error from None
        if close_failed:
            raise ProviderTransportError(_TRANSPORT_FAILED) from None
        if result is None:
            raise ProviderTransportError(_TRANSPORT_FAILED) from None
        return result

    def __prepare_request(
        self,
        request: ProviderHTTPRequest,
    ) -> tuple[bytes, dict[str, str], float]:
        if type(request) is not ProviderHTTPRequest:
            raise TypeError
        if request.method != "POST":
            raise ValueError
        if request.url != FIREWORKS_CHAT_COMPLETIONS_ENDPOINT:
            raise ValueError

        timeout_seconds = request.timeout_seconds
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(float(timeout_seconds))
            or not 0 < float(timeout_seconds) <= _MAX_TIMEOUT_SECONDS
        ):
            raise ValueError

        headers = _detach_and_validate_request_headers(request.headers)
        if not isinstance(request.json_body, Mapping):
            raise TypeError
        detached_body = dict(request.json_body)
        body = canonical_json_bytes(detached_body)
        if len(body) > self.__max_request_body_bytes:
            raise ValueError

        return body, headers, float(timeout_seconds)


def _validate_body_limit(value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError("Body limits must be positive integers.")


def _has_unsafe_header_character(value: str) -> bool:
    return any(ord(character) < 0x20 or ord(character) == 0x7F for character in value)


def _detach_and_validate_request_headers(
    source: object,
) -> dict[str, str]:
    if not isinstance(source, Mapping):
        raise TypeError
    items = list(source.items())
    if len(items) != len(_REQUIRED_HEADERS):
        raise ValueError

    detached: dict[str, str] = {}
    lower_names: set[str] = set()
    for name, value in items:
        if type(name) is not str or type(value) is not str:
            raise TypeError
        if (
            name not in _REQUIRED_HEADERS
            or _has_unsafe_header_character(name)
            or _has_unsafe_header_character(value)
            or len(value.encode("utf-8")) > _MAX_HEADER_VALUE_BYTES
        ):
            raise ValueError
        lower_name = name.lower()
        if lower_name in lower_names:
            raise ValueError
        lower_names.add(lower_name)
        detached[name] = value

    if set(detached) != _REQUIRED_HEADERS:
        raise ValueError
    if detached["Accept"] != "application/json":
        raise ValueError
    if detached["Content-Type"] != "application/json":
        raise ValueError
    if detached["User-Agent"] != _EXPECTED_USER_AGENT:
        raise ValueError

    authorization = detached["Authorization"]
    if not authorization.startswith("Bearer "):
        raise ValueError
    credential = authorization[len("Bearer ") :]
    if (
        not credential
        or credential != credential.strip()
        or any(character.isspace() for character in credential)
        or any(ord(character) < 0x21 or ord(character) > 0x7E for character in credential)
    ):
        raise ValueError

    return detached


def _response_status(response: object) -> int:
    status_code = getattr(response, "status")
    if (
        isinstance(status_code, bool)
        or not isinstance(status_code, int)
        or not 100 <= status_code <= 599
    ):
        raise ValueError
    return status_code


def _detach_response_headers(response: object) -> dict[str, str]:
    raw_headers = response.getheaders()
    detached: dict[str, str] = {}
    lower_names: set[str] = set()
    total_bytes = 0

    for item in raw_headers:
        if not isinstance(item, tuple) or len(item) != 2:
            raise ValueError
        name, value = item
        if type(name) is not str or type(value) is not str:
            raise TypeError
        if _has_unsafe_header_character(name) or _has_unsafe_header_character(value):
            raise ValueError
        lower_name = name.lower()
        if lower_name in lower_names:
            raise ValueError
        lower_names.add(lower_name)
        total_bytes += len(name.encode("utf-8")) + len(value.encode("utf-8"))
        if total_bytes > _MAX_HEADER_VALUE_BYTES:
            raise ValueError
        detached[name] = value

    return detached


def _read_bounded_response(response: object, body_limit: int) -> str:
    raw_body = response.read(body_limit + 1)
    if not isinstance(raw_body, bytes) or len(raw_body) > body_limit:
        raise ValueError
    return raw_body.decode("utf-8", errors="strict")


def _close_response_and_connection(
    response: object | None,
    connection: object | None,
) -> bool:
    close_failed = False
    for resource in (response, connection):
        if resource is None:
            continue
        try:
            resource.close()
        except Exception:
            close_failed = True
    return close_failed
