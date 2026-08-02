from __future__ import annotations

import pytest

from exitspec.reference_inference import (
    REFERENCE_MODEL,
    ReferenceInferenceRequestError,
    reference_sse_payload,
    validate_reference_request,
)


def _request() -> dict:
    return {
        "max_tokens": 64,
        "messages": [{"content": "Measure this request.", "role": "user"}],
        "model": REFERENCE_MODEL,
        "stream": True,
        "temperature": 0,
    }


def test_exact_reference_probe_request_is_accepted() -> None:
    validate_reference_request(_request())
    assert reference_sse_payload().endswith(b"data: [DONE]\n\n")
    assert b'"content":"reference-ok"' in reference_sse_payload()


@pytest.mark.parametrize(
    "change",
    (
        {"model": "some-real-model"},
        {"stream": False},
        {"temperature": 0.1},
        {"max_tokens": 0},
        {"unexpected": True},
    ),
)
def test_reference_target_rejects_every_request_outside_its_exact_contract(
    change: dict,
) -> None:
    payload = _request()
    payload.update(change)
    with pytest.raises(ReferenceInferenceRequestError):
        validate_reference_request(payload)


def test_reference_target_rejects_malformed_or_unbounded_messages() -> None:
    for messages in (
        [],
        [{"role": "assistant", "content": "wrong authority"}],
        [{"role": "user", "content": ""}],
        [{"role": "user", "content": "x", "extra": True}],
        [{"role": "user", "content": "x" * 20_001}],
    ):
        payload = _request()
        payload["messages"] = messages
        with pytest.raises(ReferenceInferenceRequestError):
            validate_reference_request(payload)
