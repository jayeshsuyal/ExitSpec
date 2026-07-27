import json
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from exitspec.assisted_authoring import (
    AssistedAuthoringError,
    ExactToolSelectionPolicy,
    _provider_request,
    build_assisted_discovery_pack,
)
from exitspec.authorized_fireworks import AuthorizedFireworksExecutor
from exitspec.intake import redact_and_parse_pasted_transcript
from exitspec.provider_egress import (
    InMemoryProviderEgressAuthorizer,
    ProviderEgressAcknowledgementError,
    ProviderEgressPolicy,
)
from exitspec.providers import (
    FireworksProvider,
    ProviderError,
    ProviderErrorCode,
    ProviderHTTPResponse,
    ProviderTimeoutError,
    TokenPricing,
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
SYNTHETIC_API_KEY = "fw_test_wave1_matrix_not_a_real_credential"
RESPONSE_MARKER = "synthetic-provider-content-marker"


@dataclass(frozen=True)
class Observation:
    code: str
    retry: bool
    next_action: str
    attempts: int = 0
    terminal_retryable: bool = False


EXPECTED_NEXT_ACTIONS = {
    "missing_configuration": "configure_provider",
    "invalid_credential": "check_provider_credential",
    "suspended_or_unfunded_account": "restore_provider_account",
    "rate_limit": "retry_later",
    "timeout": "retry_later",
    "service_unavailable_503": "retry_later",
    "other_5xx": "contact_provider",
    "malformed_json": "review_provider_output",
    "schema_violation": "review_provider_output",
    "source_link_violation": "review_source_link",
    "retry_exhaustion": "retry_later",
    "preflight_budget_refusal": "reduce_request",
    "postflight_budget_refusal": "reduce_request",
    "redirect_301": "review_provider_destination",
    "redirect_302": "review_provider_destination",
    "redirect_303": "review_provider_destination",
    "redirect_307": "review_provider_destination",
    "redirect_308": "review_provider_destination",
    "missing_egress_acknowledgement": "reauthorize_provider_egress",
    "invalid_egress_acknowledgement": "reauthorize_provider_egress",
}


def _exception_graph(root):
    pending = [root]
    seen = set()
    while pending:
        error = pending.pop()
        identity = id(error)
        if identity in seen:
            continue
        seen.add(identity)
        yield error
        for linked in (error.__cause__, error.__context__):
            if isinstance(linked, BaseException):
                pending.append(linked)


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
    def __init__(self, response):
        self.response = response
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


def _approved_case():
    case_id = MANIFEST["approved_live_smoke_request"]["source_case_id"]
    return next(case for case in FIXTURE["cases"] if case["id"] == case_id)


def _approved_request():
    case = _approved_case()
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


def _authorizer():
    return InMemoryProviderEgressAuthorizer(
        POLICY,
        clock=lambda: FIXED_TIME,
        nonce_factory=lambda: "wave1-matrix-nonce",
        capability_secret_factory=lambda: "wave1-matrix-capability",
    )


def _permit():
    request = _approved_request()
    authorizer = _authorizer()
    _, token = authorizer.issue(request, acknowledged=True)
    return authorizer.authorize(token, request)


def _proposal_payload():
    expected = _approved_case()["expected_proposals"][0]
    return {
        "proposals": [
            {
                "line_number": expected["line_number"],
                "speaker": expected["speaker"],
                "quote": expected["quote"],
                "title": "Exact tool selection",
                "normalized_claim": (
                    "Exact tool-selection accuracy is at least 95 percent "
                    "over at least 200 approved cases."
                ),
                "classification": expected["classification"],
                "threshold": expected["threshold"],
                "minimum_samples": expected["minimum_samples"],
                "open_questions": [],
            }
        ]
    }


def _success_response(*, payload=None, usage=None):
    if payload is None:
        payload = _proposal_payload()
    if usage is None:
        usage = {
            "prompt_tokens": 500,
            "completion_tokens": 100,
            "total_tokens": 600,
        }
    return ProviderHTTPResponse(
        status_code=200,
        headers={"X-Request-ID": RESPONSE_MARKER},
        body=json.dumps(
            {
                "id": RESPONSE_MARKER,
                "choices": [
                    {"message": {"content": json.dumps(payload)}}
                ],
                "usage": usage,
            }
        ),
    )


def _connection(response):
    return FakeHTTPSConnection(FakeHTTPSResponse(response))


def _authorized_error(*actions):
    factory = RecordingConnectionFactory(*actions)
    executor = AuthorizedFireworksExecutor(
        policy=POLICY,
        api_key=SYNTHETIC_API_KEY,
        connection_factory=factory,
        sleeper=lambda _delay: None,
        monotonic=lambda: 10.0,
        wall_clock=lambda: FIXED_TIME.timestamp(),
    )
    with pytest.raises(ProviderError) as captured:
        executor.execute(_permit())
    return captured.value, factory


def _provider_observation(error, *, retry):
    return Observation(
        code=error.code.value,
        retry=retry,
        next_action=error.next_action.value,
        attempts=error.attempts,
        terminal_retryable=error.retryable,
    )


def _configuration_case():
    factory = RecordingConnectionFactory()
    with pytest.raises(ProviderError) as captured:
        AuthorizedFireworksExecutor(
            policy=POLICY,
            api_key=None,
            connection_factory=factory,
        )
    assert factory.calls == []
    return _provider_observation(captured.value, retry=False)


def _single_status_case(status_code):
    error, factory = _authorized_error(
        _connection(
            ProviderHTTPResponse(
                status_code=status_code,
                headers={"X-Request-ID": RESPONSE_MARKER},
                body=RESPONSE_MARKER,
            )
        )
    )
    assert len(factory.calls) == 1
    return _provider_observation(error, retry=False)


def _account_unavailable_case():
    observations = (
        _single_status_case(402),
        _single_status_case(412),
    )
    assert {
        (observation.code, observation.next_action)
        for observation in observations
    } == {("account_unavailable", "restore_provider_account")}
    return observations[0]


def _rate_limit_case():
    error, factory = _authorized_error(
        _connection(ProviderHTTPResponse(status_code=429)),
        _connection(ProviderHTTPResponse(status_code=429)),
    )
    return _provider_observation(error, retry=len(factory.calls) > 1)


def _timeout_case():
    error, factory = _authorized_error(
        ProviderTimeoutError("synthetic timeout"),
        ProviderTimeoutError("synthetic timeout"),
    )
    return _provider_observation(error, retry=len(factory.calls) > 1)


def _service_unavailable_case():
    error, factory = _authorized_error(
        _connection(ProviderHTTPResponse(status_code=503)),
        _connection(ProviderHTTPResponse(status_code=503)),
    )
    return _provider_observation(error, retry=len(factory.calls) > 1)


def _malformed_json_case():
    error, factory = _authorized_error(
        _connection(
            ProviderHTTPResponse(status_code=200, body="{not-json")
        )
    )
    assert len(factory.calls) == 1
    return _provider_observation(error, retry=False)


def _schema_violation_case():
    error, factory = _authorized_error(
        _connection(_success_response(payload={"proposals": []}))
    )
    assert len(factory.calls) == 1
    return _provider_observation(error, retry=False)


def _source_link_violation_case():
    raw_transcript = (
        "Customer: Exact tool-selection accuracy must be at least 95 percent "
        "over 200 samples."
    )
    mismatched = {
        "proposals": [
            {
                "line_number": 1,
                "speaker": "Customer",
                "quote": (
                    "Exact tool-selection accuracy must be at least 95 "
                    "percent over 200 samples. Altered."
                ),
                "title": "Exact tool selection",
                "normalized_claim": (
                    "Exact tool-selection accuracy is at least 95 percent "
                    "over 200 samples."
                ),
                "classification": "measurable",
                "threshold": 0.95,
                "minimum_samples": 200,
                "open_questions": [],
            }
        ]
    }
    transport = ScriptedTransport(_success_response(payload=mismatched))
    provider = FireworksProvider(transport=transport, max_attempts=1)
    policy = ExactToolSelectionPolicy(
        workload_slice="synthetic-wave1-matrix",
        adapter="deterministic_tool_selection",
        adapter_version="1.0",
        owner="synthetic_owner",
        evidence_policy="Persist only synthetic case identifiers and digests.",
    )

    with pytest.raises(AssistedAuthoringError) as captured:
        build_assisted_discovery_pack(
            raw_transcript,
            executor=provider,
            model=POLICY.model,
            policy=policy,
        )

    assert len(transport.requests) == 1
    return Observation(
        code=captured.value.code,
        retry=captured.value.retryable,
        next_action=captured.value.next_action,
        attempts=captured.value.attempts,
        terminal_retryable=captured.value.retryable,
    )


def _terminal_retry_exhaustion_case():
    transport = ScriptedTransport(
        ProviderTimeoutError("synthetic terminal timeout")
    )
    provider = FireworksProvider(transport=transport, max_attempts=1)
    with pytest.raises(ProviderError) as captured:
        provider.execute(_approved_request())

    error = captured.value
    assert len(transport.requests) == 1
    return _provider_observation(error, retry=error.retryable)


def _pricing():
    snapshot = MANIFEST["provider_boundary"]["pricing_snapshot"]
    return TokenPricing(
        input_usd_per_million=Decimal(snapshot["input"]),
        output_usd_per_million=Decimal(snapshot["output"]),
        version="wave1-matrix-pricing",
    )


def _preflight_budget_case():
    transport = ScriptedTransport(_success_response())
    provider = FireworksProvider(
        transport=transport,
        pricing={POLICY.model: _pricing()},
    )
    request = replace(
        _approved_request(),
        budget_usd=Decimal("0.000001"),
    )
    with pytest.raises(ProviderError) as captured:
        provider.execute(request)

    assert transport.requests == []
    return _provider_observation(captured.value, retry=False)


def _postflight_budget_case():
    error, factory = _authorized_error(
        _connection(
            _success_response(
                usage={
                    "prompt_tokens": 1_000_000,
                    "completion_tokens": 1_000_000,
                    "total_tokens": 2_000_000,
                }
            )
        )
    )
    assert len(factory.calls) == 1
    return _provider_observation(error, retry=False)


def _redirect_case(status_code):
    error, factory = _authorized_error(
        _connection(
            ProviderHTTPResponse(
                status_code=status_code,
                headers={"Location": "https://redirect.invalid/synthetic"},
                body=RESPONSE_MARKER,
            )
        ),
        _connection(_success_response()),
    )
    assert len(factory.calls) == 1
    return _provider_observation(error, retry=False)


def _missing_acknowledgement_case():
    authorizer = _authorizer()
    with pytest.raises(ProviderEgressAcknowledgementError) as captured:
        authorizer.issue(_approved_request(), acknowledged=False)
    return Observation(
        code=captured.value.code,
        retry=False,
        next_action=captured.value.next_action,
    )


def _invalid_acknowledgement_case():
    authorizer = _authorizer()
    with pytest.raises(ProviderEgressAcknowledgementError) as captured:
        authorizer.authorize("invalid-synthetic-token", _approved_request())
    return Observation(
        code=captured.value.code,
        retry=False,
        next_action=captured.value.next_action,
    )


CASE_RUNNERS = {
    "missing_configuration": _configuration_case,
    "invalid_credential": lambda: _single_status_case(401),
    "suspended_or_unfunded_account": _account_unavailable_case,
    "rate_limit": _rate_limit_case,
    "timeout": _timeout_case,
    "service_unavailable_503": _service_unavailable_case,
    "other_5xx": lambda: _single_status_case(500),
    "malformed_json": _malformed_json_case,
    "schema_violation": _schema_violation_case,
    "source_link_violation": _source_link_violation_case,
    "retry_exhaustion": _terminal_retry_exhaustion_case,
    "preflight_budget_refusal": _preflight_budget_case,
    "postflight_budget_refusal": _postflight_budget_case,
    "redirect_301": lambda: _redirect_case(301),
    "redirect_302": lambda: _redirect_case(302),
    "redirect_303": lambda: _redirect_case(303),
    "redirect_307": lambda: _redirect_case(307),
    "redirect_308": lambda: _redirect_case(308),
    "missing_egress_acknowledgement": _missing_acknowledgement_case,
    "invalid_egress_acknowledgement": _invalid_acknowledgement_case,
}


@pytest.mark.parametrize(
    "declared",
    MANIFEST["required_failure_matrix"],
    ids=lambda failure: failure["case"],
)
def test_wave1_failure_matrix_matches_executable_public_boundaries(declared):
    assert set(CASE_RUNNERS) == {
        failure["case"]
        for failure in MANIFEST["required_failure_matrix"]
    }

    observed = CASE_RUNNERS[declared["case"]]()

    assert (observed.code, observed.retry) == (
        declared["expected_code"],
        declared["retry"],
    )
    assert observed.next_action == EXPECTED_NEXT_ACTIONS[declared["case"]]
    assert observed.terminal_retryable is False


@pytest.mark.parametrize("status_code", (402, 412))
def test_frozen_wave1_account_failures_are_typed_and_not_retried(status_code):
    observed = _single_status_case(status_code)

    assert observed.code == ProviderErrorCode.ACCOUNT_UNAVAILABLE.value
    assert observed.next_action == "restore_provider_account"
    assert observed.retry is False
    assert observed.attempts == 1
    assert observed.terminal_retryable is False


@pytest.mark.parametrize("status_code", (401, 403))
def test_documented_authentication_statuses_are_both_executed(status_code):
    observed = _single_status_case(status_code)

    assert observed.code == ProviderErrorCode.AUTHENTICATION.value
    assert observed.next_action == "check_provider_credential"
    assert observed.retry is False


@pytest.mark.parametrize(
    "runner",
    (_rate_limit_case, _timeout_case, _service_unavailable_case),
    ids=("rate-limit", "timeout", "service-unavailable"),
)
def test_internal_retry_does_not_make_terminal_failure_retryable(runner):
    observed = runner()

    assert observed.retry is True
    assert observed.attempts == POLICY.request_limits()["max_attempts"]
    assert observed.terminal_retryable is False


def test_terminal_retry_exhaustion_is_a_non_retryable_public_outcome():
    observed = _terminal_retry_exhaustion_case()

    assert observed.code == ProviderErrorCode.RETRIES_EXHAUSTED.value
    assert observed.retry is False
    assert observed.terminal_retryable is False


def test_provider_failures_are_content_free_and_close_fake_https_resources():
    response = FakeHTTPSResponse(
        ProviderHTTPResponse(
            status_code=402,
            headers={"X-Request-ID": RESPONSE_MARKER},
            body=RESPONSE_MARKER,
        )
    )
    connection = FakeHTTPSConnection(response)
    error, factory = _authorized_error(connection)

    assert error.code == ProviderErrorCode.ACCOUNT_UNAVAILABLE
    assert error.provider_request_id is None
    assert len(factory.calls) == 1
    assert len(connection.requests) == 1
    assert response.closed is True
    assert connection.closed is True
    graph = list(_exception_graph(error))
    assert graph == [error]
    for rendered in (str(error), repr(error)):
        assert RESPONSE_MARKER not in rendered
        assert SYNTHETIC_API_KEY not in rendered


def test_malformed_provider_content_is_absent_from_entire_exception_graph():
    provider_body = "{not-json-" + RESPONSE_MARKER
    error, factory = _authorized_error(
        _connection(ProviderHTTPResponse(status_code=200, body=provider_body))
    )

    assert len(factory.calls) == 1
    graph = list(_exception_graph(error))
    assert graph == [error]
    for linked_error in graph:
        rendered = "{0}\n{1}\n{2}".format(
            str(linked_error),
            repr(linked_error),
            getattr(linked_error, "doc", ""),
        )
        assert provider_body not in rendered
        assert RESPONSE_MARKER not in rendered
        assert SYNTHETIC_API_KEY not in rendered


def test_missing_configuration_never_falls_back_to_environment(monkeypatch):
    monkeypatch.setenv("FIREWORKS_API_KEY", "fw_test_environment_only_marker")

    observed = _configuration_case()

    assert observed.code == ProviderErrorCode.CONFIGURATION.value
    assert observed.retry is False
