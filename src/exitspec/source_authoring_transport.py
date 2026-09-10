"""Private exact-body HTTPS primitive and strict initial success parser.

No launch, credential acquisition, profile registry or public execution factory
is provided here. The disabled installed worker cannot call this primitive.
"""

from __future__ import annotations

import http.client
import json
import time

from .canonical import canonical_json_bytes
from .source_authoring_ipc import (
    MAX_BODY_BYTES,
    MAX_RESPONSE_BYTES,
    SourceAuthoringWorkerError,
)
from .source_authoring_live_ipc import (
    MAX_SAFE_INTEGER,
    check_deadline,
    validate_credential,
)
from .source_authoring_policy import _profile

_PINNED_MODEL = "accounts/fireworks/models/deepseek-v4-flash-0731"
_HOST = "api.fireworks.ai"
_PATH = "/inference/v1/chat/completions"
_SOURCE_PREFIX = "Untrusted redacted source JSON follows:\n"


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _invalid_constant(_):
    raise ValueError


def _json(raw, maximum):
    if type(raw) is not bytes or not 1 <= len(raw) <= maximum:
        raise ValueError
    return json.loads(
        raw.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_invalid_constant
    )


def _keys(value, required, optional=()):
    if type(value) is not dict or not set(required) <= set(value) <= set(
        required
    ) | set(optional):
        raise ValueError


def _integer(value, maximum=MAX_SAFE_INTEGER):
    if type(value) is not int or not 0 <= value <= maximum:
        raise ValueError


def _metadata_text(value):
    if (
        type(value) is not str
        or not 1 <= len(value) <= 128
        or any(not 32 <= ord(c) <= 126 for c in value)
    ):
        raise ValueError


def decode_response(raw):
    """Return content bytes only; inner source/schema validation still precedes F."""
    try:
        value = _json(raw, MAX_RESPONSE_BYTES)
        _keys(
            value,
            {"id", "object", "created", "model", "choices", "usage"},
            {"system_fingerprint"},
        )
        _metadata_text(value["id"])
        if "system_fingerprint" in value:
            _metadata_text(value["system_fingerprint"])
        _integer(value["created"])
        if value["object"] != "chat.completion" or value["model"] != _PINNED_MODEL:
            raise ValueError
        choices = value["choices"]
        if type(choices) is not list or len(choices) != 1:
            raise ValueError
        choice = choices[0]
        _keys(choice, {"index", "message", "finish_reason"}, {"logprobs"})
        _integer(choice["index"], 0)
        if choice["finish_reason"] != "stop" or choice.get("logprobs") is not None:
            raise ValueError
        message = choice["message"]
        _keys(
            message, {"role", "content"}, {"reasoning_content", "tool_calls", "refusal"}
        )
        if (
            message["role"] != "assistant"
            or type(message["content"]) is not str
            or not message["content"]
            or message.get("reasoning_content") not in (None, "")
            or message.get("tool_calls") not in (None, [])
            or message.get("refusal") is not None
        ):
            raise ValueError
        usage = value["usage"]
        _keys(
            usage,
            {"prompt_tokens", "completion_tokens", "total_tokens"},
            {"prompt_tokens_details", "completion_tokens_details"},
        )
        for key, maximum in (
            ("prompt_tokens", 8192),
            ("completion_tokens", 2000),
            ("total_tokens", 10192),
        ):
            _integer(usage[key], maximum)
        if usage["total_tokens"] != usage["prompt_tokens"] + usage["completion_tokens"]:
            raise ValueError
        for key, counter, maximum in (
            ("prompt_tokens_details", "cached_tokens", usage["prompt_tokens"]),
            ("completion_tokens_details", "reasoning_tokens", 0),
        ):
            if key in usage:
                _keys(usage[key], {counter})
                _integer(usage[key][counter], maximum)
        content = message["content"].encode("utf-8")
        if not 1 <= len(content) <= MAX_RESPONSE_BYTES:
            raise ValueError
        return content
    except (ValueError, TypeError, UnicodeError, RecursionError, KeyError):
        pass
    raise SourceAuthoringWorkerError("worker_response")


def validate_request_body(body):
    """Validate the fixed template and exact canonical bytes, without rewriting."""
    try:
        value = _json(body, MAX_BODY_BYTES)
        template = _profile()["body_template"]
        content = value["messages"][1]["content"]
        if type(content) is not str or not content.startswith(_SOURCE_PREFIX):
            raise ValueError
        source_raw = content[len(_SOURCE_PREFIX) :].encode("utf-8")
        source = _json(source_raw, MAX_BODY_BYTES)
        _keys(source, {"text"})
        if (
            type(source["text"]) is not str
            or not 1 <= len(source["text"].encode("utf-8")) <= 16384
            or canonical_json_bytes(source) != source_raw
        ):
            raise ValueError
        template["messages"][1]["content"] = content
        if (
            canonical_json_bytes(template) != body
            or canonical_json_bytes(value) != body
        ):
            raise ValueError
        return body
    except (ValueError, TypeError, UnicodeError, RecursionError, KeyError, IndexError):
        pass
    raise SourceAuthoringWorkerError("worker_body")


def _remaining(deadline, connection=None):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise SourceAuthoringWorkerError("worker_timeout")
    if connection is not None:
        connection.timeout = remaining
        if connection.sock is not None:
            connection.sock.settimeout(remaining)
    return remaining


def _post_exact(body, credential, *, deadline):
    """Single fixed POST; called only after child has validated both complete pipes."""
    validate_request_body(body)
    validate_credential(credential)
    check_deadline(deadline, admission=True)
    connection = response = None
    result = None
    try:
        connection = http.client.HTTPSConnection(
            _HOST, 443, timeout=_remaining(deadline)
        )
        # http.client uses no proxy environment; no URL, custom headers, retries,
        # redirects, automatic decompression, or streaming protocol is accepted.
        connection.request(
            "POST",
            _PATH,
            body=body,
            headers={
                "Authorization": "Bearer " + credential.decode("ascii"),
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Accept-Encoding": "identity",
                "User-Agent": "ExitSpec source authoring r2",
            },
        )
        _remaining(deadline, connection)
        # getresponse() detaches connection.sock for Connection: close while
        # its response file still owns the socket until the entity is consumed.
        response_socket = connection.sock
        response = connection.getresponse()
        if type(response.status) is not int or response.status != 200:
            raise ValueError
        headers = response.getheaders()
        if (
            len(headers) > 100
            or sum(len(name) + len(value) for name, value in headers) > 16384
        ):
            raise ValueError
        seen = set()
        declared_length = None
        for name, value in headers:
            if (
                type(name) is not str
                or type(value) is not str
                or len(name) > 128
                or len(value) > 8192
                or any(ord(c) < 32 or ord(c) > 126 for c in name + value)
            ):
                raise ValueError
            lower = name.lower()
            if lower in {
                "content-type",
                "content-encoding",
                "content-length",
                "transfer-encoding",
            }:
                if lower in seen:
                    raise ValueError
                seen.add(lower)
                if lower == "content-type" and value.lower() not in {
                    "application/json",
                    "application/json; charset=utf-8",
                }:
                    raise ValueError
                if lower == "content-encoding" and value.lower() != "identity":
                    raise ValueError
                if lower == "content-length" and (
                    not value.isascii()
                    or not value.isdigit()
                    or not 1 <= int(value) <= MAX_RESPONSE_BYTES
                ):
                    raise ValueError
                if lower == "content-length":
                    declared_length = int(value)
                if lower == "transfer-encoding" and value.lower() != "chunked":
                    raise ValueError
        if (
            "content-type" not in seen
            or {"content-length", "transfer-encoding"} <= seen
        ):
            raise ValueError
        chunks = bytearray()
        while True:
            remaining = _remaining(deadline, connection)
            if response_socket is not None:
                response_socket.settimeout(remaining)
            chunk = response.read1(min(8192, MAX_RESPONSE_BYTES + 1 - len(chunks)))
            _remaining(deadline, connection)
            if type(chunk) is not bytes:
                raise ValueError
            if not chunk:
                break
            chunks.extend(chunk)
            if len(chunks) > MAX_RESPONSE_BYTES:
                raise ValueError
            if declared_length is not None and len(chunks) >= declared_length:
                # HTTPResponse closes its file at the declared entity length.
                # Do not touch the detached socket again after that final read.
                break
        raw = bytes(chunks)
        if declared_length is not None and len(raw) != declared_length:
            raise ValueError
        decode_response(raw)
        result = raw
    except Exception:  # noqa: BLE001 - never expose provider, header, TLS or credential diagnostics
        result = None
    finally:
        credential = b""  # Reference clearing is not guaranteed memory zeroization.
        for resource in (response, connection):
            if resource is not None:
                try:
                    resource.close()
                except Exception:  # noqa: BLE001 - cleanup diagnostics remain private
                    result = None
    if result is None:
        raise SourceAuthoringWorkerError("worker_transport")
    return result
