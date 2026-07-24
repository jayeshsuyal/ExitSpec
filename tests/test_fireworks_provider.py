import json
from decimal import Decimal

import pytest

from exitspec.providers import (
    FIREWORKS_CHAT_COMPLETIONS_ENDPOINT,
    FireworksProvider,
    ProviderError,
    ProviderErrorCode,
    ProviderHTTPResponse,
    ProviderMessage,
    ProviderTimeoutError,
    StructuredJSONRequest,
    TokenPricing,
)


MODEL = "accounts/fireworks/models/test-model-v1"
SCHEMA = {
    "type": "object",
    "properties": {
        "draft": {"type": "string"},
        "confidence": {"type": "number"},
    },
    "required": ["draft", "confidence"],
    "additionalProperties": False,
}
API_KEY = "fw_live_never-print-this"
SENSITIVE_OUTPUT = "customer-secret-never-print-this"


class ScriptedTransport:
    def __init__(self, *actions):
        self.actions = list(actions)
        self.requests = []

    def send(self, request):
        self.requests.append(request)
        action = self.actions.pop(0)
        if isinstance(action, BaseException):
            raise action
        return action


class SequenceClock:
    def __init__(self, *values):
        self.values = iter(values)

    def __call__(self):
        return next(self.values)


def validate_draft(value):
    if set(value) != {"draft", "confidence"}:
        raise ValueError("wrong keys")
    if not isinstance(value["draft"], str):
        raise ValueError("draft must be text")
    if not isinstance(value["confidence"], (int, float)):
        raise ValueError("confidence must be numeric")
    return {"draft": value["draft"], "confidence": float(value["confidence"])}


def identity_output(value):
    return value


def request(**overrides):
    values = {
        "model": MODEL,
        "messages": (
            ProviderMessage(role="system", content="Return a source-linked draft."),
            ProviderMessage(role="user", content="Synthetic transcript line 1."),
        ),
        "schema_name": "exit_spec_draft",
        "response_schema": SCHEMA,
        "validate_output": validate_draft,
        "max_output_tokens": 100,
        "estimated_input_tokens": 200,
    }
    values.update(overrides)
    return StructuredJSONRequest(**values)


def success_response(
    *,
    content=None,
    status=200,
    headers=None,
    usage=None,
    request_id="chatcmpl-body-fallback",
):
    if content is None:
        content = json.dumps({"draft": "Use exact tool selection.", "confidence": 0.9})
    if usage is None:
        usage = {
            "prompt_tokens": 200,
            "completion_tokens": 50,
            "total_tokens": 250,
        }
    return ProviderHTTPResponse(
        status_code=status,
        headers=headers or {},
        body=json.dumps(
            {
                "id": request_id,
                "choices": [{"message": {"content": content}}],
                "usage": usage,
            }
        ),
    )


def pricing():
    return TokenPricing(
        input_usd_per_million=Decimal("2"),
        output_usd_per_million=Decimal("4"),
        version="synthetic-pricing-2026-07-22",
    )


def test_success_builds_documented_chat_request_and_records_receipt():
    transport = ScriptedTransport(
        success_response(headers={"X-Request-ID": "fw-request-123"})
    )
    provider = FireworksProvider(
        transport=transport,
        api_key=API_KEY,
        pricing={MODEL: pricing()},
        monotonic=SequenceClock(10.0, 10.125),
    )

    result = provider.execute(request())

    assert result.output == {
        "draft": "Use exact tool selection.",
        "confidence": 0.9,
    }
    assert len(transport.requests) == 1
    sent = transport.requests[0]
    assert sent.method == "POST"
    assert sent.url.endswith("/inference/v1/chat/completions")
    assert sent.headers["Authorization"] == "Bearer " + API_KEY
    assert set(sent.json_body) == {
        "model",
        "messages",
        "response_format",
        "temperature",
        "max_tokens",
    }
    assert sent.json_body["model"] == MODEL
    assert sent.json_body["response_format"] == {
        "type": "json_schema",
        "json_schema": {
            "name": "exit_spec_draft",
            "schema": SCHEMA,
        },
    }
    assert "store" not in sent.json_body
    assert "strict" not in sent.json_body["response_format"]["json_schema"]
    assert sent.json_body["max_tokens"] == 100

    receipt = result.receipt
    assert receipt.provider == "fireworks"
    assert receipt.model == MODEL
    assert receipt.attempts == 1
    assert receipt.latency_ms == 125.0
    assert receipt.input_tokens == 200
    assert receipt.output_tokens == 50
    assert receipt.total_tokens == 250
    assert receipt.provider_request_id == "fw-request-123"
    assert receipt.estimated_cost_usd == Decimal("0.0006")
    assert receipt.pricing_version == "synthetic-pricing-2026-07-22"


def test_outbound_schema_is_the_snapshot_taken_at_request_construction():
    mutable_schema = {
        "type": "object",
        "properties": {"draft": {"type": "string"}},
        "required": ["draft"],
    }
    provider_request = request(response_schema=mutable_schema)
    mutable_schema["required"] = ["attacker_changed_the_schema"]
    provider_request.response_schema["required"] = ["also_changed_after_construction"]
    transport = ScriptedTransport(success_response())

    FireworksProvider(transport=transport).execute(provider_request)

    sent_schema = transport.requests[0].json_body["response_format"]["json_schema"][
        "schema"
    ]
    assert sent_schema["required"] == ["draft"]


def test_local_json_schema_reference_is_enforced_without_remote_retrieval():
    local_reference_schema = {
        "$defs": {
            "draft": {
                "type": "object",
                "properties": {"draft": {"type": "string"}},
                "required": ["draft"],
                "additionalProperties": False,
            }
        },
        "type": "object",
        "properties": {"draft": {"$ref": "#/$defs/draft/properties/draft"}},
        "required": ["draft"],
        "additionalProperties": False,
    }
    transport = ScriptedTransport(
        success_response(content=json.dumps({"draft": "Locally resolved."}))
    )
    provider = FireworksProvider(transport=transport)

    result = provider.execute(
        request(
            response_schema=local_reference_schema,
            validate_output=identity_output,
        )
    )

    assert result.output == {"draft": "Locally resolved."}


@pytest.mark.parametrize(
    "invalid_output",
    [
        pytest.param({"draft": "Missing confidence."}, id="required"),
        pytest.param(
            {"draft": "Wrong type.", "confidence": "0.9"},
            id="type",
        ),
        pytest.param(
            {"draft": "Extra field.", "confidence": 0.9, "approved": True},
            id="additional-properties",
        ),
        pytest.param(
            {"verdict": "PASS", "approved": True},
            id="authority-shaped-identity-callback",
        ),
    ],
)
def test_json_schema_rejects_invalid_output_before_permissive_callback(
    invalid_output,
):
    callback_inputs = []

    def permissive_callback(value):
        callback_inputs.append(value)
        return value

    transport = ScriptedTransport(
        success_response(content=json.dumps(invalid_output)),
        success_response(),
    )
    provider = FireworksProvider(transport=transport)

    with pytest.raises(ProviderError) as raised:
        provider.execute(request(validate_output=permissive_callback))

    assert raised.value.code == ProviderErrorCode.INVALID_OUTPUT
    assert raised.value.attempts == 1
    assert callback_inputs == []
    assert len(transport.requests) == 1


def test_invalid_schema_is_rejected_at_request_construction_without_leaking_it():
    invalid_schema = {
        "type": "object",
        "properties": {"draft": {"type": "string"}},
        "required": SENSITIVE_OUTPUT,
    }

    with pytest.raises(ValueError) as raised:
        request(
            response_schema=invalid_schema,
            validate_output=identity_output,
        )

    assert "JSON Schema Draft 2020-12" in str(raised.value)
    assert SENSITIVE_OUTPUT not in str(raised.value)
    assert SENSITIVE_OUTPUT not in repr(raised.value)


@pytest.mark.parametrize("reference_keyword", ["$ref", "$dynamicRef"])
def test_external_schema_reference_is_rejected_without_fetch_or_secret_leak(
    reference_keyword,
):
    external_reference = "https://attacker.invalid/{0}.json".format(SENSITIVE_OUTPUT)
    external_schema = {
        "type": "object",
        "properties": {
            "draft": {
                reference_keyword: external_reference,
            }
        },
    }

    with pytest.raises(ValueError) as raised:
        request(
            response_schema=external_schema,
            validate_output=identity_output,
        )

    assert "only local fragment references" in str(raised.value)
    assert external_reference not in str(raised.value)
    assert external_reference not in repr(raised.value)
    assert SENSITIVE_OUTPUT not in str(raised.value)
    assert SENSITIVE_OUTPUT not in repr(raised.value)


def test_schema_instance_error_does_not_leak_provider_content():
    secret_instance_value = "{0}-{1}".format(SENSITIVE_OUTPUT, API_KEY)
    transport = ScriptedTransport(
        success_response(
            content=json.dumps(
                {
                    "draft": "Secret-bearing type mismatch.",
                    "confidence": secret_instance_value,
                }
            )
        )
    )
    provider = FireworksProvider(transport=transport, api_key=API_KEY)

    with pytest.raises(ProviderError) as raised:
        provider.execute(request(validate_output=identity_output))

    assert raised.value.code == ProviderErrorCode.INVALID_OUTPUT
    for rendered_error in (str(raised.value), repr(raised.value)):
        assert SENSITIVE_OUTPUT not in rendered_error
        assert API_KEY not in rendered_error
        assert secret_instance_value not in rendered_error


@pytest.mark.parametrize(
    ("response", "expected_code"),
    [
        (success_response(content="not-json"), ProviderErrorCode.INVALID_OUTPUT),
        (
            success_response(content=json.dumps({"draft": "missing confidence"})),
            ProviderErrorCode.INVALID_OUTPUT,
        ),
        (
            ProviderHTTPResponse(status_code=200, body=json.dumps({"choices": []})),
            ProviderErrorCode.MALFORMED_RESPONSE,
        ),
    ],
)
def test_malformed_or_invalid_output_is_not_retried(response, expected_code):
    transport = ScriptedTransport(response, success_response())
    provider = FireworksProvider(transport=transport)

    with pytest.raises(ProviderError) as raised:
        provider.execute(request())

    assert raised.value.code == expected_code
    assert raised.value.attempts == 1
    assert len(transport.requests) == 1


def test_429_and_503_retry_with_capped_retry_after_then_succeed():
    transport = ScriptedTransport(
        ProviderHTTPResponse(
            status_code=429,
            headers={"Retry-After": "99", "X-Request-ID": "rate-limit-1"},
            body="sensitive body is intentionally ignored",
        ),
        ProviderHTTPResponse(status_code=503, body="also ignored"),
        success_response(),
    )
    delays = []
    provider = FireworksProvider(
        transport=transport,
        max_attempts=3,
        base_backoff_seconds=0.1,
        max_backoff_seconds=1.0,
        max_retry_after_seconds=0.5,
        sleeper=delays.append,
        monotonic=SequenceClock(1.0, 1.0),
    )

    result = provider.execute(request())

    assert result.receipt.attempts == 3
    assert delays == [0.5, 0.2]
    assert len(transport.requests) == 3


def test_timeouts_stop_at_bound_and_report_exhaustion():
    transport = ScriptedTransport(
        ProviderTimeoutError("first {0}".format(API_KEY)),
        ProviderTimeoutError("second {0}".format(API_KEY)),
        ProviderTimeoutError("third {0}".format(API_KEY)),
    )
    delays = []
    provider = FireworksProvider(
        transport=transport,
        max_attempts=3,
        base_backoff_seconds=0.25,
        max_backoff_seconds=1.0,
        sleeper=delays.append,
    )

    with pytest.raises(ProviderError) as raised:
        provider.execute(request())

    error = raised.value
    assert error.code == ProviderErrorCode.RETRIES_EXHAUSTED
    assert error.last_code == ProviderErrorCode.TIMEOUT
    assert error.attempts == 3
    assert error.retryable is True
    assert delays == [0.25, 0.5]
    assert API_KEY not in str(error)
    assert API_KEY not in repr(error)


def test_ordinary_4xx_is_not_retried():
    transport = ScriptedTransport(
        ProviderHTTPResponse(status_code=400, body="do not echo me"),
        success_response(),
    )
    provider = FireworksProvider(transport=transport)

    with pytest.raises(ProviderError) as raised:
        provider.execute(request())

    assert raised.value.code == ProviderErrorCode.CLIENT_REQUEST
    assert raised.value.status_code == 400
    assert len(transport.requests) == 1


def test_preflight_budget_blocks_before_transport_when_ceiling_is_known():
    transport = ScriptedTransport(success_response())
    provider = FireworksProvider(transport=transport, pricing={MODEL: pricing()})

    with pytest.raises(ProviderError) as raised:
        provider.execute(request(budget_usd=Decimal("0.0001")))

    assert raised.value.code == ProviderErrorCode.BUDGET_EXCEEDED
    assert raised.value.attempts == 0
    assert transport.requests == []


def test_postflight_budget_uses_actual_usage_and_keeps_safe_receipt():
    transport = ScriptedTransport(
        success_response(
            content=json.dumps({"draft": SENSITIVE_OUTPUT, "confidence": 0.8})
        )
    )
    provider = FireworksProvider(
        transport=transport,
        pricing={MODEL: pricing()},
        monotonic=SequenceClock(4.0, 4.01),
    )

    with pytest.raises(ProviderError) as raised:
        provider.execute(
            request(
                budget_usd=Decimal("0.0003"),
                estimated_input_tokens=None,
                max_output_tokens=None,
            )
        )

    error = raised.value
    assert error.code == ProviderErrorCode.BUDGET_EXCEEDED
    assert error.receipt is not None
    assert error.receipt.estimated_cost_usd == Decimal("0.0006")
    assert SENSITIVE_OUTPUT not in str(error)
    assert SENSITIVE_OUTPUT not in repr(error)


def test_representations_and_errors_do_not_leak_key_or_response_content():
    response = ProviderHTTPResponse(
        status_code=401,
        headers={"X-Debug": API_KEY},
        body="{0} {1}".format(API_KEY, SENSITIVE_OUTPUT),
    )
    transport = ScriptedTransport(response)
    provider = FireworksProvider(transport=transport, api_key=API_KEY)
    provider_request = request(
        messages=(ProviderMessage(role="user", content=SENSITIVE_OUTPUT),)
    )

    with pytest.raises(ProviderError) as raised:
        provider.execute(provider_request)

    values = (
        repr(provider),
        repr(provider_request),
        repr(transport.requests[0]),
        repr(response),
        str(raised.value),
        repr(raised.value),
    )
    for value in values:
        assert API_KEY not in value
        assert SENSITIVE_OUTPUT not in value


@pytest.mark.parametrize(
    "untrusted_endpoint",
    [
        "https://api.fireworks.ai.attacker.example/inference/v1/chat/completions",
        "https://fireworks.ai/inference/v1/chat/completions",
        "https://api.fireworks.ai:444/inference/v1/chat/completions",
        "https://api.fireworks.ai:not-a-port/inference/v1/chat/completions",
        "https://api.fireworks.ai/inference/v1/responses",
        "https://api.fireworks.ai/inference/v1/chat/completions?key=redirect",
        "\n" + FIREWORKS_CHAT_COMPLETIONS_ENDPOINT,
        FIREWORKS_CHAT_COMPLETIONS_ENDPOINT + "\n",
        "\r" + FIREWORKS_CHAT_COMPLETIONS_ENDPOINT,
        FIREWORKS_CHAT_COMPLETIONS_ENDPOINT + "\r",
        "\t" + FIREWORKS_CHAT_COMPLETIONS_ENDPOINT,
        FIREWORKS_CHAT_COMPLETIONS_ENDPOINT + "\t",
        " " + FIREWORKS_CHAT_COMPLETIONS_ENDPOINT,
        FIREWORKS_CHAT_COMPLETIONS_ENDPOINT + " ",
    ],
)
def test_untrusted_endpoint_is_rejected_before_transport_can_receive_key(
    untrusted_endpoint,
):
    transport = ScriptedTransport(success_response())

    with pytest.raises(ValueError, match="official chat-completions"):
        FireworksProvider(
            transport=transport,
            api_key=API_KEY,
            endpoint=untrusted_endpoint,
        )

    assert transport.requests == []


def test_unexpected_transport_exception_is_sanitized_and_not_retried():
    transport = ScriptedTransport(
        RuntimeError("transport accidentally included {0}".format(API_KEY)),
        success_response(),
    )
    provider = FireworksProvider(transport=transport, api_key=API_KEY)

    with pytest.raises(ProviderError) as raised:
        provider.execute(request())

    assert raised.value.code == ProviderErrorCode.TRANSPORT
    assert len(transport.requests) == 1
    assert API_KEY not in str(raised.value)
    assert API_KEY not in repr(raised.value)


def test_transport_must_return_the_typed_response_boundary():
    transport = ScriptedTransport({"status_code": 200, "body": SENSITIVE_OUTPUT})
    provider = FireworksProvider(transport=transport)

    with pytest.raises(ProviderError) as raised:
        provider.execute(request())

    assert raised.value.code == ProviderErrorCode.TRANSPORT
    assert SENSITIVE_OUTPUT not in str(raised.value)
    assert SENSITIVE_OUTPUT not in repr(raised.value)
