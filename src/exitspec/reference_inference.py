"""Deterministic local streaming target for the browser demo.

This endpoint is deliberately not an inference engine.  It implements the
smallest exact OpenAI-compatible streaming surface needed to exercise
ExitSpec's real probe, deterministic evaluator, Evidence Pack, and handoff
boundaries without a paid provider or GPU.
"""

from __future__ import annotations

from typing import Any, Final, Mapping


REFERENCE_ENDPOINT_PATH: Final = (
    "/api/reference/inference/v1/chat/completions"
)
REFERENCE_MODEL: Final = "exitspec/reference-stream-v1"
REFERENCE_PROVIDER: Final = "ExitSpec local reference"
REFERENCE_ENDPOINT_CLASS: Final = (
    "OpenAI-compatible deterministic reference"
)
MAX_REFERENCE_PROMPT_LENGTH: Final = 20_000
MAX_REFERENCE_TOKENS: Final = 2_048

_REQUEST_FIELDS: Final = frozenset(
    {"max_tokens", "messages", "model", "stream", "temperature"}
)
_MESSAGE_FIELDS: Final = frozenset({"content", "role"})
_SSE_PAYLOAD: Final = (
    b'data: {"choices":[{"delta":{"content":"reference-ok"}}]}\n\n'
    b"data: [DONE]\n\n"
)


class ReferenceInferenceRequestError(ValueError):
    """The request is outside the exact local reference contract."""


def validate_reference_request(payload: Mapping[str, Any]) -> None:
    """Accept only the exact bounded request emitted by the performance probe."""

    if type(payload) is not dict or set(payload) != _REQUEST_FIELDS:
        raise ReferenceInferenceRequestError
    max_tokens = payload["max_tokens"]
    temperature = payload["temperature"]
    messages = payload["messages"]
    if (
        type(max_tokens) is not int
        or not 1 <= max_tokens <= MAX_REFERENCE_TOKENS
        or payload["model"] != REFERENCE_MODEL
        or payload["stream"] is not True
        or (
            type(temperature) not in {int, float}
            or isinstance(temperature, bool)
            or temperature != 0
        )
        or type(messages) is not list
        or len(messages) != 1
    ):
        raise ReferenceInferenceRequestError

    message = messages[0]
    if type(message) is not dict or set(message) != _MESSAGE_FIELDS:
        raise ReferenceInferenceRequestError
    content = message["content"]
    if (
        message["role"] != "user"
        or type(content) is not str
        or not content
        or len(content) > MAX_REFERENCE_PROMPT_LENGTH
        or any(
            ord(character) < 0x20
            and character not in {"\n", "\r", "\t"}
            for character in content
        )
        or any(ord(character) == 0x7F for character in content)
    ):
        raise ReferenceInferenceRequestError


def reference_sse_payload() -> bytes:
    """Return the byte-stable response used by the local reference target."""

    return _SSE_PAYLOAD


__all__ = [
    "REFERENCE_ENDPOINT_CLASS",
    "REFERENCE_ENDPOINT_PATH",
    "REFERENCE_MODEL",
    "REFERENCE_PROVIDER",
    "ReferenceInferenceRequestError",
    "reference_sse_payload",
    "validate_reference_request",
]
