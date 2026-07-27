import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from exitspec.assisted_authoring import ProposalBatch, _provider_request
from exitspec.authorized_fireworks import AuthorizedFireworksExecutor
from exitspec.intake import redact_and_parse_pasted_transcript
from exitspec.provider_egress import (
    EgressRejectionReason,
    InMemoryProviderEgressAuthorizer,
    ProviderEgressAcknowledgementError,
    ProviderEgressPolicy,
)
from exitspec.providers import (
    ProviderError,
    ProviderErrorCode,
    ProviderHTTPResponse,
    ProviderNextAction,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MANIFEST = json.loads(
    (
        PROJECT_ROOT
        / "examples/support-agent/fireworks/wave-1-acceptance-v1.json"
    ).read_text(encoding="utf-8")
)
FIXTURE = json.loads(
    (PROJECT_ROOT / MANIFEST["source_fixture"]["path"]).read_text(
        encoding="utf-8"
    )
)
POLICY = ProviderEgressPolicy.from_frozen_manifest(MANIFEST)
FIXED_TIME = datetime(2026, 7, 27, 17, 0, tzinfo=timezone.utc)
API_KEY = "fw_test_AUTHORIZED_EXECUTOR_SECRET_123"


class SequenceClock:
    def __init__(self, *values):
        self.values = iter(values)

    def __call__(self):
        return next(self.values)


class FakeHTTPSResponse:
    def __init__(self, response):
        self.status = response.status_code
        self._headers = list(response.headers.items())
        self._body = response.body.encode("utf-8")
        self.read_calls = 0
        self.closed = False

    def getheaders(self):
        return list(self._headers)

    def read(self, _amount):
        self.read_calls += 1
        return self._body

    def close(self):
        self.closed = True


class FakeHTTPSConnection:
    def __init__(self, response, *, request_error=None):
        self.response = response
        self.request_error = request_error
        self.requests = []
        self.closed = False

    def request(self, method, path, *, body, headers):
        self.requests.append(
            {
                "method": method,
                "path": path,
                "body": body,
                "headers": dict(headers),
            }
        )
        if self.request_error is not None:
            raise self.request_error

    def getresponse(self):
        return self.response

    def close(self):
        self.closed = True


class RecordingConnectionFactory:
    def __init__(self, *actions):
        self.actions = list(actions)
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        action = self.actions.pop(0)
        if isinstance(action, BaseException):
            raise action
        return action


def _case():
    case_id = MANIFEST["approved_live_smoke_request"]["source_case_id"]
    return next(case for case in FIXTURE["cases"] if case["id"] == case_id)


def _request():
    case = _case()
    intake = redact_and_parse_pasted_transcript(
        case["transcript"],
        transcript_id=case["id"],
        title=case["title"],
        customer_terms=case["customer_terms"],
    )
    request, _ = _provider_request(
        intake,
        model=POLICY.model,
        customer_terms=case["customer_terms"],
    )
    return replace(request, budget_usd=POLICY.max_request_cost_usd)


def _success_response():
    expected = _case()["expected_proposals"][0]
    proposal = {
        "line_number": expected["line_number"],
        "speaker": expected["speaker"],
        "quote": expected["quote"],
        "title": "Exact tool selection",
        "normalized_claim": (
            "Exact tool-selection accuracy is at least 95% over "
            "at least 200 approved cases."
        ),
        "classification": expected["classification"],
        "threshold": expected["threshold"],
        "minimum_samples": expected["minimum_samples"],
        "open_questions": [],
    }
    return ProviderHTTPResponse(
        status_code=200,
        headers={"X-Request-ID": "synthetic-request-id"},
        body=json.dumps(
            {
                "id": "synthetic-body-id",
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {"proposals": [proposal]}
                            )
                        }
                    }
                ],
                "usage": {
                    "prompt_tokens": 500,
                    "completion_tokens": 100,
                    "total_tokens": 600,
                },
            }
        ),
    )


def _permit():
    request = _request()
    authorizer = InMemoryProviderEgressAuthorizer(
        POLICY,
        clock=lambda: FIXED_TIME,
        nonce_factory=lambda: "authorized-executor-nonce",
        capability_secret_factory=lambda: "authorized-executor-capability",
    )
    _, token = authorizer.issue(request, acknowledged=True)
    return authorizer.authorize(token, request), request


def _executor(connection_factory, **overrides):
    values = {
        "policy": POLICY,
        "api_key": API_KEY,
        "connection_factory": connection_factory,
        "sleeper": lambda _: None,
        "monotonic": SequenceClock(10.0, 10.05),
        "wall_clock": lambda: FIXED_TIME.timestamp(),
    }
    values.update(overrides)
    return AuthorizedFireworksExecutor(**values)


def test_authorized_executor_uses_frozen_policy_and_one_exact_permit():
    response = FakeHTTPSResponse(_success_response())
    connection = FakeHTTPSConnection(response)
    factory = RecordingConnectionFactory(connection)
    executor = _executor(factory)
    permit, approved_request = _permit()

    result = executor.execute(permit)

    assert isinstance(result.output, ProposalBatch)
    assert result.output.proposals[0].quote == _case()["expected_proposals"][0][
        "quote"
    ]
    assert permit.is_taken
    assert factory.calls == [
        (("api.fireworks.ai", 443), {"timeout": 30.0})
    ]
    assert len(connection.requests) == 1
    sent = connection.requests[0]
    payload = json.loads(sent["body"])
    assert sent["method"] == "POST"
    assert sent["path"] == "/inference/v1/chat/completions"
    assert payload["model"] == POLICY.model
    assert sent["headers"]["Authorization"] == "Bearer " + API_KEY
    assert payload["messages"][1]["content"] == (
        approved_request.messages[1].content
    )
    assert response.closed is True
    assert connection.closed is True
    assert result.receipt.provider == "fireworks"
    assert result.receipt.estimated_cost_usd is not None
    assert result.receipt.estimated_cost_usd < POLICY.max_request_cost_usd
    assert result.receipt.pricing_version == "fireworks-standard-2026-07-27"
    assert result.receipt.provider_request_id is None
    assert executor.max_attempts == POLICY.request_limits()["max_attempts"]


def test_raw_request_or_forged_policy_cannot_enter_executor():
    connection = FakeHTTPSConnection(FakeHTTPSResponse(_success_response()))
    factory = RecordingConnectionFactory(connection)
    executor = _executor(factory)

    with pytest.raises(
        ProviderEgressAcknowledgementError,
        match="authorized request permit",
    ) as error:
        executor.execute(_request())
    assert error.value.code == "egress_not_authorized"
    assert error.value.reason == EgressRejectionReason.INVALID
    assert factory.calls == []

    changed_policy = POLICY.model_copy(
        update={
            "endpoint": "https://provider.example.test/v1/chat/completions"
        }
    )
    with pytest.raises(ValueError, match="frozen Wave-1 policy"):
        AuthorizedFireworksExecutor(
            policy=changed_policy,
            api_key=API_KEY,
            connection_factory=factory,
        )
    assert factory.calls == []


def test_permit_and_provider_retry_limits_are_each_enforced_once():
    factory = RecordingConnectionFactory(
        TimeoutError("first timeout"),
        TimeoutError("second timeout"),
        FakeHTTPSConnection(FakeHTTPSResponse(_success_response())),
    )
    delays = []
    executor = AuthorizedFireworksExecutor(
        policy=POLICY,
        api_key=API_KEY,
        connection_factory=factory,
        sleeper=delays.append,
        monotonic=lambda: 10.0,
        wall_clock=lambda: FIXED_TIME.timestamp(),
    )
    permit, _ = _permit()

    with pytest.raises(ProviderError) as error:
        executor.execute(permit)
    assert error.value.code == ProviderErrorCode.RETRIES_EXHAUSTED
    assert error.value.last_code == ProviderErrorCode.TIMEOUT
    assert error.value.attempts == POLICY.request_limits()["max_attempts"]
    assert error.value.retryable is False
    assert error.value.next_action == ProviderNextAction.RETRY_LATER
    assert len(factory.calls) == POLICY.request_limits()["max_attempts"]
    assert delays == [0.25]

    with pytest.raises(ProviderEgressAcknowledgementError) as replay:
        executor.execute(permit)
    assert replay.value.reason == EgressRejectionReason.REPLAYED
    assert len(factory.calls) == POLICY.request_limits()["max_attempts"]


def test_redirect_is_typed_non_retryable_and_does_not_leak_location_or_key():
    secret_location = "https://redirect.invalid/" + API_KEY
    redirect_response = ProviderHTTPResponse(
        status_code=302,
        headers={"Location": secret_location},
        body=API_KEY,
    )
    response = FakeHTTPSResponse(redirect_response)
    connection = FakeHTTPSConnection(response)
    factory = RecordingConnectionFactory(
        connection,
        FakeHTTPSConnection(FakeHTTPSResponse(_success_response())),
    )
    executor = _executor(factory)
    permit, _ = _permit()

    with pytest.raises(ProviderError) as error:
        executor.execute(permit)
    assert error.value.code == ProviderErrorCode.REDIRECT_REJECTED
    assert error.value.status_code == 302
    assert error.value.attempts == 1
    assert error.value.retryable is False
    assert len(factory.calls) == 1
    assert len(connection.requests) == 1
    assert response.read_calls == 0
    for rendered in (str(error.value), repr(error.value), repr(executor)):
        assert API_KEY not in rendered
        assert secret_location not in rendered


def test_provider_request_id_is_removed_from_typed_error_boundary():
    response = FakeHTTPSResponse(
        ProviderHTTPResponse(
            status_code=401,
            headers={"X-Request-ID": API_KEY},
            body=API_KEY,
        )
    )
    factory = RecordingConnectionFactory(FakeHTTPSConnection(response))
    executor = _executor(factory)
    permit, _ = _permit()

    with pytest.raises(ProviderError) as error:
        executor.execute(permit)

    assert error.value.code == ProviderErrorCode.AUTHENTICATION
    assert error.value.provider_request_id is None
    assert error.value.receipt is None
    assert error.value.__context__ is None
    assert error.value.__cause__ is None
    assert API_KEY not in str(error.value)
    assert API_KEY not in repr(error.value)


@pytest.mark.parametrize("status_code", (402, 412))
def test_frozen_wave1_account_failures_share_one_safe_outcome(status_code):
    response = FakeHTTPSResponse(
        ProviderHTTPResponse(
            status_code=status_code,
            headers={"X-Request-ID": API_KEY},
            body=API_KEY,
        )
    )
    factory = RecordingConnectionFactory(FakeHTTPSConnection(response))
    executor = _executor(factory)
    permit, _ = _permit()

    with pytest.raises(ProviderError) as error:
        executor.execute(permit)

    assert error.value.code == ProviderErrorCode.ACCOUNT_UNAVAILABLE
    assert error.value.status_code == status_code
    assert error.value.next_action == ProviderNextAction.RESTORE_ACCOUNT
    assert error.value.retryable is False
    assert error.value.provider_request_id is None
    assert error.value.__context__ is None
    assert API_KEY not in str(error.value)
    assert API_KEY not in repr(error.value)


@pytest.mark.parametrize(
    "invalid_key",
    (
        None,
        "",
        " ",
        "key with spaces",
        "key\nwith-newline",
        "key\rwith-return",
        "x" * 4097,
    ),
)
def test_missing_or_ambiguous_credential_fails_before_transport(invalid_key):
    connection = FakeHTTPSConnection(FakeHTTPSResponse(_success_response()))
    factory = RecordingConnectionFactory(connection)

    with pytest.raises(ProviderError) as error:
        AuthorizedFireworksExecutor(
            policy=POLICY,
            api_key=invalid_key,
            connection_factory=factory,
        )

    assert error.value.code == ProviderErrorCode.CONFIGURATION
    assert error.value.attempts == 0
    assert factory.calls == []
    if isinstance(invalid_key, str) and len(invalid_key) >= 4:
        assert invalid_key not in str(error.value)
        assert invalid_key not in repr(error.value)


def test_executor_representation_is_content_and_credential_free():
    factory = RecordingConnectionFactory(
        FakeHTTPSConnection(FakeHTTPSResponse(_success_response()))
    )
    executor = _executor(factory)
    rendered = repr(executor)

    assert "AuthorizedFireworksExecutor" in rendered
    assert POLICY.model in rendered
    assert POLICY.endpoint in rendered
    assert API_KEY not in rendered
    assert _case()["transcript"] not in rendered
