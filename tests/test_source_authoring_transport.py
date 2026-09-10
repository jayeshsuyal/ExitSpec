"""Strict initial provider envelope; success fixtures are synthetic only."""

import copy
import json
import time

import pytest

from exitspec import source_authoring_transport as transport
from exitspec.canonical import canonical_json_bytes
from exitspec.source_authoring_ipc import SourceAuthoringWorkerError
from exitspec.source_authoring_pins import REQUEST_PROFILE_JSON
from exitspec.source_authoring_transport import decode_response


def envelope():
    return {
        "id": "synthetic-local-response",
        "object": "chat.completion",
        "created": 0,
        "model": "accounts/fireworks/models/deepseek-v4-flash-0731",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": '{"synthetic":true}'},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
    }


def encode(value):
    return json.dumps(value, separators=(",", ":")).encode()


def test_exact_envelope_and_optional_fields():
    value = envelope()
    value["system_fingerprint"] = "synthetic"
    value["choices"][0]["logprobs"] = None
    value["choices"][0]["message"].update(
        reasoning_content="", tool_calls=[], refusal=None
    )
    value["usage"].update(
        prompt_tokens_details={"cached_tokens": 10},
        completion_tokens_details={"reasoning_tokens": 0},
    )
    assert decode_response(encode(value)) == b'{"synthetic":true}'
    value["choices"][0]["message"].update(reasoning_content=None, tool_calls=None)
    assert decode_response(encode(value)) == b'{"synthetic":true}'


@pytest.mark.parametrize(
    "keys,replacement",
    [
        (("model",), "deepseek-v4-flash-0731"),
        (("object",), "chat.completion.chunk"),
        (("created",), True),
        (("created",), -1),
        (("created",), 9007199254740992),
        (("id",), ""),
        (("id",), "a" * 129),
        (("id",), "raw\nprivate"),
        (("system_fingerprint",), None),
        (("system_fingerprint",), "é"),
        (("choices",), []),
        (("choices", 0, "index"), False),
        (("choices", 0, "finish_reason"), "length"),
        (("choices", 0, "logprobs"), {}),
        (("choices", 0, "message", "role"), "tool"),
        (("choices", 0, "message", "content"), ""),
        (("choices", 0, "message", "content"), None),
        (("choices", 0, "message", "reasoning_content"), "private reasoning"),
        (("choices", 0, "message", "tool_calls"), [{}]),
        (("choices", 0, "message", "refusal"), "private refusal"),
        (("usage", "prompt_tokens"), True),
        (("usage", "prompt_tokens"), 8193),
        (("usage", "completion_tokens"), 2001),
        (("usage", "total_tokens"), 11),
        (("usage", "completion_tokens"), 2.0),
        (("usage", "total_tokens"), -1),
        (("usage", "prompt_tokens_details"), {}),
        (("usage", "prompt_tokens_details"), None),
        (("usage", "prompt_tokens_details"), {"cached_tokens": 11}),
        (("usage", "prompt_tokens_details"), {"cached_tokens": True}),
        (("usage", "prompt_tokens_details"), {"cached_tokens": 0, "audio_tokens": 0}),
        (("usage", "completion_tokens_details"), {}),
        (("usage", "completion_tokens_details"), {"reasoning_tokens": 1}),
        (("usage", "completion_tokens_details"), {"reasoning_tokens": 0.0}),
        (
            ("usage", "completion_tokens_details"),
            {"reasoning_tokens": 0, "accepted_prediction_tokens": 0},
        ),
    ],
)
def test_malformed_or_unqualified_field_shapes(keys, replacement):
    value = envelope()
    current = value
    for key in keys[:-1]:
        current = current[key]
    current[keys[-1]] = replacement
    with pytest.raises(SourceAuthoringWorkerError) as error:
        decode_response(encode(value))
    assert "private" not in str(error.value)
    assert error.value.__cause__ is None


@pytest.mark.parametrize(
    "keys", [(), ("choices", 0), ("choices", 0, "message"), ("usage",)]
)
def test_unknown_fields_at_every_depth(keys):
    value = envelope()
    current = value
    for key in keys:
        current = current[key]
    current["unknown_private_field"] = "PRIVATE-MARKER"
    with pytest.raises(SourceAuthoringWorkerError):
        decode_response(encode(value))


@pytest.mark.parametrize("key", list(envelope()))
def test_every_required_root_field(key):
    value = envelope()
    del value[key]
    with pytest.raises(SourceAuthoringWorkerError):
        decode_response(encode(value))


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"[]",
        b"null",
        b"\xff",
        b'{"id":1,"id":2}',
        b'{"x":NaN}',
        b'{"x":Infinity}',
        b"x" * 262145,
    ],
)
def test_strict_bounded_json(raw):
    with pytest.raises(SourceAuthoringWorkerError):
        decode_response(raw)


def test_more_than_one_choice_refused_and_usage_boundaries_pass():
    value = envelope()
    value["choices"].append(copy.deepcopy(value["choices"][0]))
    with pytest.raises(SourceAuthoringWorkerError):
        decode_response(encode(value))
    value = envelope()
    value["usage"] = {
        "prompt_tokens": 8192,
        "completion_tokens": 2000,
        "total_tokens": 10192,
    }
    assert decode_response(encode(value)) == b'{"synthetic":true}'


def request_body():
    value = json.loads(REQUEST_PROFILE_JSON)["body_template"]
    value["messages"][1]["content"] = (
        'Untrusted redacted source JSON follows:\n{"text":"synthetic source"}'
    )
    return canonical_json_bytes(value)


class FakeResponse:
    def __init__(self, *, status=200, raw=None, headers=None, delay=0):
        self.status = status
        self.raw = encode(envelope()) if raw is None else raw
        self.headers = (
            [("Content-Type", "application/json")] if headers is None else headers
        )
        self.offset = 0
        self.closed = False
        self.delay = delay

    def getheaders(self):
        return self.headers

    def read1(self, amount):
        if self.delay:
            time.sleep(self.delay)
            amount = 1
        chunk = self.raw[self.offset : self.offset + amount]
        self.offset += len(chunk)
        return chunk

    def close(self):
        self.closed = True


class FakeConnection:
    def __init__(self, response):
        self.response = response
        self.sock = self
        self.requests = []
        self.timeouts = []
        self.closed = False

    def settimeout(self, timeout):
        self.timeouts.append(timeout)

    def request(self, method, path, *, body, headers):
        self.requests.append((method, path, body, headers))

    def getresponse(self):
        return self.response

    def close(self):
        self.closed = True


def fake_transport(monkeypatch, response):
    connection = FakeConnection(response)
    calls = []

    def create(*args, **kwargs):
        calls.append((args, kwargs))
        return connection

    monkeypatch.setattr(transport.http.client, "HTTPSConnection", create)
    return connection, calls


def test_fixed_exact_bytes_headers_and_one_post_without_proxy(monkeypatch):
    response = FakeResponse()
    connection, calls = fake_transport(monkeypatch, response)
    monkeypatch.setenv("HTTPS_PROXY", "https://forbidden.invalid")
    request = request_body()
    assert (
        transport._post_exact(request, b"SYNTHETIC-KEY", deadline=time.monotonic() + 1)
        == response.raw
    )
    assert len(calls) == len(connection.requests) == 1
    assert calls[0][0] == ("api.fireworks.ai", 443)
    method, path, sent, headers = connection.requests[0]
    assert method == "POST" and path == "/inference/v1/chat/completions"
    assert sent is request
    assert headers == {
        "Authorization": "Bearer SYNTHETIC-KEY",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Accept-Encoding": "identity",
        "User-Agent": "ExitSpec source authoring r2",
    }
    assert response.closed and connection.closed
    assert all(0 < timeout <= 1 for timeout in connection.timeouts)


@pytest.mark.parametrize(
    "mutation",
    [
        "noncanonical",
        "model",
        "stream",
        "n",
        "reasoning",
        "tool",
        "extra_source_key",
        "oversize_source",
        "empty_source",
        "secret_crlf",
        "expired",
    ],
)
def test_invalid_request_refuses_before_connection_or_dns(monkeypatch, mutation):
    value = json.loads(request_body())
    credential, deadline = b"SYNTHETIC-KEY", time.monotonic() + 1
    if mutation == "model":
        value["model"] = "caller-model"
    elif mutation == "stream":
        value["stream"] = True
    elif mutation == "n":
        value["n"] = 2
    elif mutation == "reasoning":
        value["reasoning_effort"] = "high"
    elif mutation == "tool":
        value["tools"] = []
    elif mutation in {"extra_source_key", "oversize_source", "empty_source"}:
        source = {"text": "synthetic source"}
        if mutation == "extra_source_key":
            source["instructions"] = "private"
        else:
            source["text"] = "x" * 16385 if mutation == "oversize_source" else ""
        value["messages"][1]["content"] = (
            "Untrusted redacted source JSON follows:\n"
            + canonical_json_bytes(source).decode()
        )
    elif mutation == "secret_crlf":
        credential = b"bad\r\nsecret"
    elif mutation == "expired":
        deadline = time.monotonic() - 1
    raw = canonical_json_bytes(value)
    if mutation == "noncanonical":
        raw += b" "
    _, calls = fake_transport(monkeypatch, FakeResponse())
    with pytest.raises(SourceAuthoringWorkerError):
        transport._post_exact(raw, credential, deadline=deadline)
    assert calls == []


@pytest.mark.parametrize("status", [201, 301, 307, 400, 401, 429, 500, True])
def test_no_redirect_retry_or_raw_error_response(monkeypatch, status):
    response = FakeResponse(status=status, raw=b"PRIVATE-MARKER")
    connection, calls = fake_transport(monkeypatch, response)
    with pytest.raises(SourceAuthoringWorkerError) as error:
        transport._post_exact(
            request_body(), b"SYNTHETIC-KEY", deadline=time.monotonic() + 1
        )
    assert "PRIVATE-MARKER" not in str(error.value)
    assert response.offset == 0
    assert len(calls) == len(connection.requests) == 1
    assert response.closed and connection.closed


@pytest.mark.parametrize(
    "headers",
    [
        [],
        [("Content-Type", "text/event-stream")],
        [("Content-Type", "application/json"), ("Content-Encoding", "gzip")],
        [("Content-Type", "application/json"), ("Content-Length", "262145")],
        [
            ("Content-Type", "application/json"),
            ("Content-Length", "1"),
            ("Transfer-Encoding", "chunked"),
        ],
        [("Content-Type", "application/json"), ("content-type", "application/json")],
        [("Content-Type", "application/json"), ("X-Private", "PRIVATE\nMARKER")],
    ],
)
def test_unsupported_or_duplicate_headers_refuse_before_body(monkeypatch, headers):
    response = FakeResponse(headers=headers)
    connection, _ = fake_transport(monkeypatch, response)
    with pytest.raises(SourceAuthoringWorkerError):
        transport._post_exact(
            request_body(), b"SYNTHETIC-KEY", deadline=time.monotonic() + 1
        )
    assert response.offset == 0 and connection.closed and response.closed


def test_declared_content_length_mismatch_refuses_even_if_json_is_complete(monkeypatch):
    raw = encode(envelope())
    response = FakeResponse(
        raw=raw,
        headers=[
            ("Content-Type", "application/json"),
            ("Content-Length", str(len(raw) + 1)),
        ],
    )
    fake_transport(monkeypatch, response)
    with pytest.raises(SourceAuthoringWorkerError):
        transport._post_exact(
            request_body(), b"SYNTHETIC-KEY", deadline=time.monotonic() + 1
        )


@pytest.mark.parametrize(
    "response",
    [
        FakeResponse(raw=b"x" * 262145),
        FakeResponse(raw=b'{"x":NaN}'),
        FakeResponse(delay=0.02),
    ],
)
def test_oversize_invalid_or_slow_trickle_response(monkeypatch, response):
    connection, calls = fake_transport(monkeypatch, response)
    started = time.monotonic()
    with pytest.raises(SourceAuthoringWorkerError):
        transport._post_exact(request_body(), b"SYNTHETIC-KEY", deadline=started + 0.1)
    assert time.monotonic() - started < 0.3
    assert connection.closed and response.closed and len(calls) == 1


def test_response_entity_byte_boundary(monkeypatch):
    value = envelope()
    value["choices"][0]["message"]["content"] = ""
    value["choices"][0]["message"]["content"] = "x" * (262144 - len(encode(value)))
    raw = encode(value)
    assert len(raw) == 262144
    response = FakeResponse(
        raw=raw,
        headers=[("Content-Type", "application/json"), ("Content-Length", "262144")],
    )
    connection, calls = fake_transport(monkeypatch, response)
    assert (
        transport._post_exact(
            request_body(), b"SYNTHETIC-KEY", deadline=time.monotonic() + 1
        )
        == raw
    )
    assert response.closed and connection.closed and len(calls) == 1
