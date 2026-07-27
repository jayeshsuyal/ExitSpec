from __future__ import annotations

from typing import Any

import pytest

from exitspec.canonical import canonical_json_bytes
from exitspec.providers.base import (
    ProviderHTTPRequest,
    ProviderRedirectError,
    ProviderTimeoutError,
    ProviderTransportError,
)
from exitspec.providers.fireworks import FIREWORKS_CHAT_COMPLETIONS_ENDPOINT
from exitspec.providers.fireworks_http import PinnedFireworksHTTPSTransport


API_KEY = "fw_test_transport-secret-never-print"
LOCATION = "https://attacker.invalid/steal"
BODY = {
    "messages": [{"content": "Synthetic customer request.", "role": "user"}],
    "model": "accounts/fireworks/models/deepseek-v4-flash",
    "temperature": 0,
}
HEADERS = {
    "Accept": "application/json",
    "Authorization": "Bearer " + API_KEY,
    "Content-Type": "application/json",
    "User-Agent": "ExitSpec/0.1 provider-boundary",
}


class FakeResponse:
    def __init__(
        self,
        *,
        status: Any = 200,
        headers: Any = None,
        body: Any = b'{"ok":true}',
        getheaders_error: Exception | None = None,
        read_error: Exception | None = None,
        close_error: Exception | None = None,
    ) -> None:
        self.status = status
        self.headers = [] if headers is None else headers
        self.body = body
        self.getheaders_error = getheaders_error
        self.read_error = read_error
        self.close_error = close_error
        self.getheaders_calls = 0
        self.read_calls: list[int] = []
        self.close_calls = 0

    def getheaders(self):
        self.getheaders_calls += 1
        if self.getheaders_error is not None:
            raise self.getheaders_error
        return self.headers

    def read(self, amount: int):
        self.read_calls.append(amount)
        if self.read_error is not None:
            raise self.read_error
        return self.body

    def close(self) -> None:
        self.close_calls += 1
        if self.close_error is not None:
            raise self.close_error


class FakeConnection:
    def __init__(
        self,
        response: FakeResponse | None = None,
        *,
        request_error: Exception | None = None,
        getresponse_error: Exception | None = None,
        close_error: Exception | None = None,
    ) -> None:
        self.response = response or FakeResponse()
        self.request_error = request_error
        self.getresponse_error = getresponse_error
        self.close_error = close_error
        self.requests: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
        self.getresponse_calls = 0
        self.close_calls = 0

    def request(self, *args, **kwargs) -> None:
        self.requests.append((args, kwargs))
        if self.request_error is not None:
            raise self.request_error

    def getresponse(self):
        self.getresponse_calls += 1
        if self.getresponse_error is not None:
            raise self.getresponse_error
        return self.response

    def close(self) -> None:
        self.close_calls += 1
        if self.close_error is not None:
            raise self.close_error


class RecordingConnectionFactory:
    def __init__(
        self,
        *connections: FakeConnection,
        error: Exception | None = None,
    ) -> None:
        self.connections = list(connections)
        self.error = error
        self.calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if self.error is not None:
            raise self.error
        return self.connections.pop(0)


def provider_request(**changes) -> ProviderHTTPRequest:
    values = {
        "method": "POST",
        "url": FIREWORKS_CHAT_COMPLETIONS_ENDPOINT,
        "headers": dict(HEADERS),
        "json_body": dict(BODY),
        "timeout_seconds": 12.5,
    }
    values.update(changes)
    return ProviderHTTPRequest(**values)


def transport_for(
    connection: FakeConnection,
    **changes,
) -> tuple[PinnedFireworksHTTPSTransport, RecordingConnectionFactory]:
    factory = RecordingConnectionFactory(connection)
    return (
        PinnedFireworksHTTPSTransport(
            connection_factory=factory,
            **changes,
        ),
        factory,
    )


def assert_sanitized(error: BaseException) -> None:
    rendered = str(error) + repr(error)
    for sensitive in (
        API_KEY,
        LOCATION,
        "Synthetic customer request.",
        "api.fireworks.ai",
        "Authorization",
    ):
        assert sensitive not in rendered


def test_construction_is_side_effect_free_and_repr_hides_factory():
    factory = RecordingConnectionFactory(error=AssertionError("must not run"))

    transport = PinnedFireworksHTTPSTransport(connection_factory=factory)

    assert factory.calls == []
    assert "RecordingConnectionFactory" not in repr(transport)
    assert API_KEY not in repr(transport)
    assert "connection_factory=<redacted>" in repr(transport)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("connection_factory", object()),
        ("max_request_body_bytes", 0),
        ("max_request_body_bytes", True),
        ("max_response_body_bytes", -1),
        ("max_response_body_bytes", 1.5),
    ],
)
def test_constructor_rejects_invalid_configuration_without_calling_factory(
    name,
    value,
):
    factory = RecordingConnectionFactory(error=AssertionError("must not run"))
    values = {"connection_factory": factory}
    values[name] = value

    with pytest.raises((TypeError, ValueError)):
        PinnedFireworksHTTPSTransport(**values)

    assert factory.calls == []


def test_success_pins_host_port_path_and_sends_one_canonical_post():
    raw_headers = [("Content-Type", "application/json"), ("X-Request-ID", "req-1")]
    response = FakeResponse(headers=raw_headers, body=b'{"answer":"synthetic"}')
    connection = FakeConnection(response)
    transport, factory = transport_for(connection)
    request = provider_request()

    result = transport.send(request)

    assert factory.calls == [
        (("api.fireworks.ai", 443), {"timeout": 12.5}),
    ]
    assert connection.requests == [
        (
            ("POST", "/inference/v1/chat/completions"),
            {
                "body": canonical_json_bytes(BODY),
                "headers": HEADERS,
            },
        )
    ]
    assert connection.getresponse_calls == 1
    assert response.getheaders_calls == 1
    assert response.read_calls == [1024 * 1024 + 1]
    assert response.close_calls == 1
    assert connection.close_calls == 1
    assert result.status_code == 200
    assert result.body == '{"answer":"synthetic"}'
    assert result.headers == {
        "Content-Type": "application/json",
        "X-Request-ID": "req-1",
    }

    raw_headers.append(("X-Late-Mutation", "not-detached"))
    assert "X-Late-Mutation" not in result.headers


def test_request_headers_are_detached_before_the_connection_is_created():
    source_headers = dict(HEADERS)
    connection = FakeConnection()
    transport, _ = transport_for(connection)

    transport.send(provider_request(headers=source_headers))
    sent_headers = connection.requests[0][1]["headers"]
    source_headers["Accept"] = "text/plain"

    assert sent_headers is not source_headers
    assert sent_headers["Accept"] == "application/json"


@pytest.mark.parametrize(
    "candidate",
    [
        {"method": "POST"},
        provider_request(method="post"),
        provider_request(method="GET"),
        provider_request(url="http://api.fireworks.ai/inference/v1/chat/completions"),
        provider_request(
            url="https://credential@api.fireworks.ai/inference/v1/chat/completions"
        ),
        provider_request(
            url=FIREWORKS_CHAT_COMPLETIONS_ENDPOINT + "?redirect=false"
        ),
        provider_request(url=FIREWORKS_CHAT_COMPLETIONS_ENDPOINT + "#fragment"),
        provider_request(
            url="https://api.fireworks.ai:443/inference/v1/chat/completions"
        ),
        provider_request(
            url="https://api.fireworks.ai/inference/v1/chat/completions/"
        ),
    ],
)
def test_method_type_and_exact_url_are_rejected_before_connection(candidate):
    factory = RecordingConnectionFactory(error=AssertionError("must not run"))
    transport = PinnedFireworksHTTPSTransport(connection_factory=factory)

    with pytest.raises(
        ProviderTransportError,
        match="^Provider HTTPS request was rejected[.]$",
    ) as captured:
        transport.send(candidate)

    assert factory.calls == []
    assert_sanitized(captured.value)


@pytest.mark.parametrize(
    "timeout_seconds",
    [True, "1", 0, -0.1, float("inf"), float("-inf"), float("nan"), 30.0001],
)
def test_invalid_timeout_is_rejected_before_connection(timeout_seconds):
    factory = RecordingConnectionFactory(error=AssertionError("must not run"))
    transport = PinnedFireworksHTTPSTransport(connection_factory=factory)

    with pytest.raises(ProviderTransportError) as captured:
        transport.send(provider_request(timeout_seconds=timeout_seconds))

    assert factory.calls == []
    assert_sanitized(captured.value)


@pytest.mark.parametrize(
    "headers",
    [
        {name: value for name, value in HEADERS.items() if name != "Authorization"},
        {**HEADERS, "X-Debug": "true"},
        {**HEADERS, "Accept": "application/json, text/plain"},
        {**HEADERS, "Content-Type": "application/json; charset=utf-8"},
        {**HEADERS, "User-Agent": "another-client"},
        {**HEADERS, "Authorization": "Bearer "},
        {**HEADERS, "Authorization": "bearer token"},
        {**HEADERS, "Authorization": "Bearer token with-space"},
        {**HEADERS, "Authorization": "Bearer token\r\nX-Evil: yes"},
        {
            "Accept": "application/json",
            "Authorization\r\nX-Evil": "Bearer token",
            "Content-Type": "application/json",
            "User-Agent": "ExitSpec/0.1 provider-boundary",
        },
        {**HEADERS, "authorization": "Bearer duplicate"},
    ],
)
def test_unsafe_or_ambiguous_headers_are_rejected_before_connection(headers):
    factory = RecordingConnectionFactory(error=AssertionError("must not run"))
    transport = PinnedFireworksHTTPSTransport(connection_factory=factory)

    with pytest.raises(ProviderTransportError) as captured:
        transport.send(provider_request(headers=headers))

    assert factory.calls == []
    assert_sanitized(captured.value)


@pytest.mark.parametrize(
    "json_body",
    [
        ["not", "an", "object"],
        {"not_json": object()},
        {"not_finite": float("nan")},
    ],
)
def test_noncanonical_json_body_is_rejected_before_connection(json_body):
    factory = RecordingConnectionFactory(error=AssertionError("must not run"))
    transport = PinnedFireworksHTTPSTransport(connection_factory=factory)

    with pytest.raises(ProviderTransportError) as captured:
        transport.send(provider_request(json_body=json_body))

    assert factory.calls == []
    assert_sanitized(captured.value)


def test_oversized_request_body_is_rejected_before_connection():
    factory = RecordingConnectionFactory(error=AssertionError("must not run"))
    transport = PinnedFireworksHTTPSTransport(
        connection_factory=factory,
        max_request_body_bytes=8,
    )

    with pytest.raises(ProviderTransportError) as captured:
        transport.send(provider_request(json_body={"value": "too long"}))

    assert factory.calls == []
    assert_sanitized(captured.value)


@pytest.mark.parametrize("status_code", [300, 301, 302, 303, 307, 308, 399])
def test_redirect_is_refused_without_reading_location_or_opening_again(status_code):
    response = FakeResponse(
        status=status_code,
        headers=[("Location", LOCATION)],
        body=API_KEY.encode("utf-8"),
    )
    connection = FakeConnection(response)
    transport, factory = transport_for(connection)

    with pytest.raises(ProviderRedirectError) as captured:
        transport.send(provider_request())

    assert captured.value.status_code == status_code
    assert_sanitized(captured.value)
    assert len(factory.calls) == 1
    assert len(connection.requests) == 1
    assert connection.getresponse_calls == 1
    assert response.getheaders_calls == 0
    assert response.read_calls == []
    assert response.close_calls == 1
    assert connection.close_calls == 1


@pytest.mark.parametrize(
    "stage",
    ["factory", "request", "getresponse", "read"],
)
def test_timeout_at_every_io_stage_is_sanitized_and_resources_close(stage):
    timeout = TimeoutError(API_KEY + LOCATION)
    response = FakeResponse(read_error=timeout if stage == "read" else None)
    connection = FakeConnection(
        response,
        request_error=timeout if stage == "request" else None,
        getresponse_error=timeout if stage == "getresponse" else None,
    )
    factory = RecordingConnectionFactory(
        connection,
        error=timeout if stage == "factory" else None,
    )
    transport = PinnedFireworksHTTPSTransport(connection_factory=factory)

    with pytest.raises(
        ProviderTimeoutError,
        match="^Provider HTTPS request timed out[.]$",
    ) as captured:
        transport.send(provider_request())

    assert_sanitized(captured.value)
    if stage == "factory":
        assert connection.close_calls == 0
        assert response.close_calls == 0
    else:
        assert connection.close_calls == 1
        assert response.close_calls == (1 if stage == "read" else 0)


@pytest.mark.parametrize(
    "response",
    [
        FakeResponse(status=True),
        FakeResponse(headers=[("X-Bad", "line\r\nbreak")]),
        FakeResponse(headers=[("X-Duplicate", "one"), ("x-duplicate", "two")]),
        FakeResponse(body=b"\xff"),
        FakeResponse(body="not bytes"),
        FakeResponse(getheaders_error=RuntimeError(API_KEY + LOCATION)),
        FakeResponse(read_error=OSError(API_KEY + LOCATION)),
    ],
)
def test_malformed_response_and_io_failures_are_sanitized_and_closed(response):
    connection = FakeConnection(response)
    transport, _ = transport_for(connection)

    with pytest.raises(
        ProviderTransportError,
        match="^Provider HTTPS transport failed[.]$",
    ) as captured:
        transport.send(provider_request())

    assert_sanitized(captured.value)
    assert response.close_calls == 1
    assert connection.close_calls == 1


def test_oversized_response_is_refused_after_one_bounded_read():
    response = FakeResponse(body=b"x" * 9)
    connection = FakeConnection(response)
    transport, _ = transport_for(connection, max_response_body_bytes=8)

    with pytest.raises(ProviderTransportError) as captured:
        transport.send(provider_request())

    assert response.read_calls == [9]
    assert response.close_calls == 1
    assert connection.close_calls == 1
    assert_sanitized(captured.value)


def test_request_connection_failure_does_not_echo_exception_details():
    response = FakeResponse()
    connection = FakeConnection(
        response,
        request_error=OSError(
            API_KEY
            + " "
            + LOCATION
            + " Authorization Synthetic customer request. api.fireworks.ai"
        ),
    )
    transport, _ = transport_for(connection)

    with pytest.raises(ProviderTransportError) as captured:
        transport.send(provider_request())

    assert_sanitized(captured.value)
    assert response.close_calls == 0
    assert connection.close_calls == 1


@pytest.mark.parametrize("resource", ["response", "connection"])
def test_close_failure_is_sanitized_after_the_other_resource_is_closed(resource):
    close_error = OSError(API_KEY + LOCATION)
    response = FakeResponse(close_error=close_error if resource == "response" else None)
    connection = FakeConnection(
        response,
        close_error=close_error if resource == "connection" else None,
    )
    transport, _ = transport_for(connection)

    with pytest.raises(ProviderTransportError) as captured:
        transport.send(provider_request())

    assert_sanitized(captured.value)
    assert response.close_calls == 1
    assert connection.close_calls == 1


def test_provider_request_repr_does_not_expose_credential_or_body():
    request = provider_request()

    assert API_KEY not in repr(request)
    assert "Synthetic customer request." not in repr(request)
    assert "headers=<redacted>" in repr(request)
    assert "json_body=<redacted>" in repr(request)
