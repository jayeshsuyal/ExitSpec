import json
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from exitspec.intake import redact_and_parse_pasted_transcript
from exitspec.providers import ProviderHTTPResponse
from exitspec.wave1_execution import Wave1ProviderExecutionConfiguration
from exitspec.wave1_runtime import frozen_wave1_source
from exitspec.web import DemoSession, ExitSpecDemoServer


API_KEY = "fw_test_wave1_web_execution_secret"
PROVIDER_MARKER = "provider-controlled-response-marker"
WAVE1_RECEIPT_FIELDS = {
    "provider",
    "model",
    "endpoint",
    "attempts",
    "latency_ms",
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "estimated_cost_usd",
    "pricing_version",
    "outcome_code",
}


class FakeHTTPSResponse:
    def __init__(self, response: ProviderHTTPResponse):
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
    def __init__(self, response: FakeHTTPSResponse):
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


class BlockingFakeHTTPSConnection(FakeHTTPSConnection):
    def __init__(self, response: FakeHTTPSResponse):
        super().__init__(response)
        self.request_started = threading.Event()
        self.release_response = threading.Event()

    def request(self, method, path, *, body, headers):
        super().request(method, path, body=body, headers=headers)
        self.request_started.set()

    def getresponse(self):
        if not self.release_response.wait(5):
            raise TimeoutError("Test did not release the fake response.")
        return super().getresponse()


class RecordingConnectionFactory:
    def __init__(self, *actions):
        self.actions = list(actions)
        self.calls = []
        self.connections = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if not self.actions:
            raise AssertionError("Unexpected additional provider connection.")
        action = self.actions.pop(0)
        if isinstance(action, BaseException):
            raise action
        self.connections.append(action)
        return action


def _session(tmp_path: Path) -> DemoSession:
    return DemoSession.synthetic_support_agent(output_root=tmp_path / "runs")


def _configuration(
    factory: RecordingConnectionFactory,
    *,
    enabled: bool = True,
    api_key: object = API_KEY,
) -> Wave1ProviderExecutionConfiguration:
    return Wave1ProviderExecutionConfiguration(
        enabled=enabled,
        api_key=api_key,
        connection_factory=factory,
        sleeper=lambda _delay: None,
        monotonic=lambda: 10.0,
        wall_clock=lambda: 10.0,
    )


@contextmanager
def _running_server(
    tmp_path: Path,
    configuration: Wave1ProviderExecutionConfiguration | None = None,
):
    session = _session(tmp_path)
    server = ExitSpecDemoServer(
        ("127.0.0.1", 0),
        session,
        wave1_provider_execution=configuration,
    )
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    base_url = "http://127.0.0.1:{0}".format(server.server_port)
    try:
        yield server, base_url
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


def _get_json(url: str) -> dict:
    with urlopen(url, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def _post_json(
    url: str,
    payload: dict,
    *,
    operation_key: str,
    origin: str,
) -> dict:
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Idempotency-Key": operation_key,
            "Origin": origin,
        },
        method="POST",
    )
    with urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def _post_error(
    url: str,
    payload: dict,
    *,
    operation_key: str,
    origin: str,
) -> tuple[int, dict]:
    try:
        _post_json(
            url,
            payload,
            operation_key=operation_key,
            origin=origin,
        )
    except HTTPError as error:
        return error.code, json.loads(error.read().decode("utf-8"))
    raise AssertionError("Expected provider action to fail.")


def _authorize(base_url: str, *, suffix: str = "1") -> dict:
    disclosure = _get_json(
        base_url + "/api/provider/fireworks/disclosure"
    )
    return _post_json(
        base_url + "/api/provider/fireworks/authorization",
        {
            "disclosure_id": disclosure["disclosure_id"],
            "acknowledged": True,
        },
        operation_key="provider-authorization-" + suffix,
        origin=base_url,
    )


def _proposal(*, quote: str | None = None) -> dict:
    source = frozen_wave1_source()
    intake = redact_and_parse_pasted_transcript(
        source["transcript"],
        transcript_id=source["transcript_id"],
        title=source["title"],
        customer_terms=source["customer_terms"],
    )
    line = intake.transcript.lines[0]
    return {
        "line_number": line.line_number,
        "speaker": line.speaker,
        "quote": line.text if quote is None else quote,
        "title": "Exact tool selection",
        "normalized_claim": (
            "Exact tool-selection accuracy is at least 95% over at least "
            "200 approved cases."
        ),
        "classification": "measurable",
        "threshold": 0.95,
        "minimum_samples": 200,
        "open_questions": [],
    }


def _response(
    *,
    status: int = 200,
    payload: dict | None = None,
    usage: dict | None = None,
    body: str | None = None,
    headers: dict | None = None,
) -> ProviderHTTPResponse:
    if body is None:
        if payload is None:
            payload = {"proposals": [_proposal()]}
        if usage is None:
            usage = {
                "prompt_tokens": 500,
                "completion_tokens": 100,
                "total_tokens": 600,
            }
        body = json.dumps(
            {
                "id": PROVIDER_MARKER,
                "choices": [
                    {"message": {"content": json.dumps(payload)}}
                ],
                "usage": usage,
            }
        )
    return ProviderHTTPResponse(
        status_code=status,
        headers=headers or {"X-Request-ID": PROVIDER_MARKER},
        body=body,
    )


def _connection(response: ProviderHTTPResponse) -> FakeHTTPSConnection:
    return FakeHTTPSConnection(FakeHTTPSResponse(response))


def _assert_content_free_terminal_receipt(operation: dict) -> None:
    receipt = operation["receipt"]
    assert set(receipt) == WAVE1_RECEIPT_FIELDS
    assert receipt["provider"] == "fireworks"
    assert receipt["model"] == "accounts/fireworks/models/deepseek-v4-flash"
    assert receipt["endpoint"] == (
        "https://api.fireworks.ai/inference/v1/chat/completions"
    )
    assert receipt["attempts"] == operation["attempts"]
    assert receipt["outcome_code"] == operation["outcome_code"]
    assert "provider_request_id" not in receipt


def test_provider_execution_is_disabled_by_default_and_env_does_not_enable_it(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("FIREWORKS_API_KEY", API_KEY)
    factory = RecordingConnectionFactory(_connection(_response()))

    with _running_server(tmp_path) as (server, base_url):
        disclosure = _get_json(
            base_url + "/api/provider/fireworks/disclosure"
        )
        assert disclosure["runtime"] == {
            "enabled": False,
            "configured": False,
            "authorization_active": False,
            "execution_available": False,
            "provider_calls": False,
            "reserved_spend_usd": "0.00",
            "remaining_spend_usd": "0.10",
            "max_live_smoke_total_cost_usd": "0.10",
            "last_execution": None,
        }
        _authorize(base_url)
        executed = _post_json(
            base_url + "/api/provider/fireworks/execution",
            {},
            operation_key="provider-execution-disabled",
            origin=base_url,
        )

        assert executed["execution"]["outcome_code"] == (
            "configuration_error"
        )
        _assert_content_free_terminal_receipt(executed["execution"])
        assert executed["execution"]["provider_call_attempted"] is False
        assert executed["state"]["safety"]["provider_calls"] is False
        assert executed["state"]["provider_execution"][
            "authorization_active"
        ] is True
        assert server.session._wave1_provider_authorization is not None
        assert factory.calls == []


def test_one_authorized_success_creates_review_only_draft_and_replays_safely(
    tmp_path,
):
    connection = _connection(_response())
    factory = RecordingConnectionFactory(connection)
    configuration = _configuration(factory)

    with _running_server(tmp_path, configuration) as (server, base_url):
        initial = _get_json(
            base_url + "/api/provider/fireworks/disclosure"
        )
        assert initial["runtime"]["enabled"] is True
        assert initial["runtime"]["configured"] is True
        assert initial["runtime"]["execution_available"] is False

        authorized = _authorize(base_url)
        assert authorized["authorization"]["status"] == (
            "authorization_recorded"
        )
        assert authorized["disclosure"]["runtime"][
            "execution_available"
        ] is True

        executed = _post_json(
            base_url + "/api/provider/fireworks/execution",
            {},
            operation_key="provider-execution-success",
            origin=base_url,
        )
        operation = executed["execution"]
        state = executed["state"]

        assert operation["status"] == "succeeded_needs_review"
        assert operation["outcome_code"] == "success"
        assert operation["proposals_created"] == 1
        assert operation["provider_call_attempted"] is True
        _assert_content_free_terminal_receipt(operation)
        assert state["authoring"]["mode"] == "fireworks_assisted"
        assert state["authoring"]["provider_calls"] is True
        assert state["drafts"][0]["status"] == "NEEDS_REVIEW"
        assert state["contract"] is None
        assert state["confirmation"] is None
        assert state["proof_pack"] is None
        assert state["provider_execution"]["authorization_active"] is False
        assert server.session._wave1_provider_authorization is None

        assert factory.calls == [
            (("api.fireworks.ai", 443), {"timeout": 30.0})
        ]
        assert len(connection.requests) == 1
        sent = connection.requests[0]
        assert sent["path"] == "/inference/v1/chat/completions"
        assert sent["headers"]["Authorization"] == "Bearer " + API_KEY

        replay = _post_json(
            base_url + "/api/provider/fireworks/execution",
            {},
            operation_key="provider-execution-success",
            origin=base_url,
        )
        assert replay["execution"]["idempotent_replay"] is True
        assert replay["execution"]["execution_id"] == operation["execution_id"]
        assert len(factory.calls) == 1

        status, error = _post_error(
            base_url + "/api/provider/fireworks/execution",
            {},
            operation_key="provider-execution-second-send",
            origin=base_url,
        )
        assert status == 409
        assert "authorize" in error["error"].lower()
        assert len(factory.calls) == 1

        rendered = json.dumps(executed, sort_keys=True)
        for forbidden in (
            API_KEY,
            PROVIDER_MARKER,
            "Authorization",
            "capability_token",
            "provider_request_id",
            "request_body",
            "response_body",
        ):
            assert forbidden not in rendered


@pytest.mark.parametrize(
    ("case", "expected_code", "expected_next_action", "expected_attempts"),
    [
        (
            "authentication",
            "authentication_error",
            "check_provider_credential",
            1,
        ),
        (
            "account",
            "account_unavailable",
            "restore_provider_account",
            1,
        ),
        ("rate_limit", "rate_limited", "retry_later", 2),
        ("timeout", "retries_exhausted", "retry_later", 2),
        ("service_unavailable", "retries_exhausted", "retry_later", 2),
        (
            "malformed",
            "malformed_response",
            "review_provider_output",
            1,
        ),
        ("schema", "invalid_output", "review_provider_output", 1),
        ("budget", "budget_exceeded", "reduce_request", 1),
        (
            "redirect",
            "redirect_rejected",
            "review_provider_destination",
            1,
        ),
    ],
)
def test_provider_failures_are_typed_sanitized_and_single_use(
    tmp_path,
    case,
    expected_code,
    expected_next_action,
    expected_attempts,
):
    if case == "authentication":
        actions = [_connection(_response(status=401, body=PROVIDER_MARKER))]
    elif case == "account":
        actions = [_connection(_response(status=402, body=PROVIDER_MARKER))]
    elif case == "rate_limit":
        actions = [
            _connection(_response(status=429, body=PROVIDER_MARKER)),
            _connection(_response(status=429, body=PROVIDER_MARKER)),
        ]
    elif case == "timeout":
        actions = [
            TimeoutError(PROVIDER_MARKER),
            TimeoutError(PROVIDER_MARKER),
        ]
    elif case == "service_unavailable":
        actions = [
            _connection(_response(status=503, body=PROVIDER_MARKER)),
            _connection(_response(status=503, body=PROVIDER_MARKER)),
        ]
    elif case == "malformed":
        actions = [
            _connection(_response(body="{not-json-" + PROVIDER_MARKER))
        ]
    elif case == "schema":
        actions = [
            _connection(_response(payload={"proposals": []}))
        ]
    elif case == "redirect":
        actions = [
            _connection(
                _response(
                    status=302,
                    body=PROVIDER_MARKER,
                    headers={
                        "Location": (
                            "https://redirect.invalid/" + PROVIDER_MARKER
                        )
                    },
                )
            )
        ]
    else:
        actions = [
            _connection(
                _response(
                    usage={
                        "prompt_tokens": 100_000_000,
                        "completion_tokens": 100_000_000,
                        "total_tokens": 200_000_000,
                    }
                )
            )
        ]
    factory = RecordingConnectionFactory(*actions)
    configuration = _configuration(factory)

    with _running_server(tmp_path, configuration) as (server, base_url):
        drafts_before = _get_json(base_url + "/api/state")["drafts"]
        _authorize(base_url, suffix=case)
        executed = _post_json(
            base_url + "/api/provider/fireworks/execution",
            {},
            operation_key="provider-execution-" + case,
            origin=base_url,
        )

        operation = executed["execution"]
        assert operation["status"] == "failed"
        assert operation["outcome_code"] == expected_code
        assert operation["next_action"] == expected_next_action
        assert operation["attempts"] == expected_attempts
        _assert_content_free_terminal_receipt(operation)
        assert operation["provider_call_attempted"] is True
        assert operation["proposals_created"] == 0
        assert executed["state"]["drafts"] == drafts_before
        assert executed["state"]["safety"]["provider_calls"] is True
        assert executed["state"]["provider_execution"][
            "authorization_active"
        ] is False
        assert server.session._wave1_provider_authorization is None

        rendered = json.dumps(executed, sort_keys=True)
        assert API_KEY not in rendered
        assert PROVIDER_MARKER not in rendered

        calls_before_replay = len(factory.calls)
        replay = _post_json(
            base_url + "/api/provider/fireworks/execution",
            {},
            operation_key="provider-execution-" + case,
            origin=base_url,
        )
        assert replay["execution"]["idempotent_replay"] is True
        assert len(factory.calls) == calls_before_replay


def test_source_link_failure_cannot_mutate_authority_state(tmp_path):
    factory = RecordingConnectionFactory(
        _connection(
            _response(
                payload={
                    "proposals": [
                        _proposal(quote="Provider invented this source.")
                    ]
                }
            )
        )
    )
    configuration = _configuration(factory)

    with _running_server(tmp_path, configuration) as (server, base_url):
        state_before = _get_json(base_url + "/api/state")
        _authorize(base_url)
        executed = _post_json(
            base_url + "/api/provider/fireworks/execution",
            {},
            operation_key="provider-source-link-rejected",
            origin=base_url,
        )

        operation = executed["execution"]
        assert operation["outcome_code"] == "source_link_violation"
        assert operation["next_action"] == "review_source_link"
        assert operation["provider_call_attempted"] is True
        assert operation["attempts"] == 1
        _assert_content_free_terminal_receipt(operation)
        assert executed["state"]["drafts"] == state_before["drafts"]
        assert executed["state"]["contract"] == state_before["contract"]
        assert server.session._wave1_provider_authorization is None
        assert "Provider invented" not in json.dumps(executed)


def test_execution_route_rejects_client_authority_fields_query_and_alias_origin(
    tmp_path,
):
    factory = RecordingConnectionFactory(_connection(_response()))
    configuration = _configuration(factory)

    with _running_server(tmp_path, configuration) as (server, base_url):
        _authorize(base_url)
        active = server.session._wave1_provider_authorization
        endpoint = base_url + "/api/provider/fireworks/execution"

        status, error = _post_error(
            endpoint,
            {
                "capability_token": API_KEY,
                "model": "attacker-model",
                "prompt": "attacker-prompt",
            },
            operation_key="provider-client-fields-rejected",
            origin=base_url,
        )
        assert status == 400
        assert error == {"error": "Request contains unsupported fields."}

        status, error = _post_error(
            endpoint,
            {"idempotency_key": "body-keys-are-forbidden"},
            operation_key="provider-body-key-rejected",
            origin=base_url,
        )
        assert status == 400
        assert error == {"error": "Request contains unsupported fields."}

        status, error = _post_error(
            endpoint + "?model=attacker",
            {},
            operation_key="provider-query-rejected",
            origin=base_url,
        )
        assert status == 400
        assert "url parameters" in error["error"].lower()

        status, error = _post_error(
            endpoint,
            {},
            operation_key="provider-origin-alias-rejected",
            origin=base_url.replace("127.0.0.1", "localhost"),
        )
        assert status == 403
        assert "origin" in error["error"].lower()

        assert server.session._wave1_provider_authorization is active
        assert server.session._wave1_provider_execution_operations == {}
        assert factory.calls == []
        rendered = json.dumps(error)
        assert API_KEY not in rendered
        assert "attacker-prompt" not in rendered


def test_concurrent_identical_execution_actions_send_exactly_once(tmp_path):
    factory = RecordingConnectionFactory(_connection(_response()))
    configuration = _configuration(factory)
    session = _session(tmp_path)
    session.configure_wave1_provider_execution(configuration)
    disclosure = session.wave1_provider_disclosure_payload()
    session.authorize_wave1_provider_egress(
        disclosure_id=disclosure["disclosure_id"],
        acknowledged=True,
        idempotency_key="provider-concurrent-authorization",
    )

    def execute_once(_index):
        return session.execute_wave1_provider_assist(
            configuration=configuration,
            idempotency_key="provider-concurrent-execution",
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(execute_once, range(16)))

    operations = [result["execution"] for result in results]
    for operation in operations:
        _assert_content_free_terminal_receipt(operation)
    assert len({operation["execution_id"] for operation in operations}) == 1
    assert sum(
        not operation["idempotent_replay"] for operation in operations
    ) == 1
    assert len(factory.calls) == 1
    assert session.reviewed_drafts[0].status.value == "NEEDS_REVIEW"


def test_pending_same_key_replay_is_typed_and_completes_exactly_once(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(
        "exitspec.web.WAVE1_PROVIDER_REPLAY_WAIT_SECONDS",
        0.01,
    )
    connection = BlockingFakeHTTPSConnection(
        FakeHTTPSResponse(_response())
    )
    factory = RecordingConnectionFactory(connection)
    configuration = _configuration(factory)

    with _running_server(tmp_path, configuration) as (_server, base_url):
        _authorize(base_url, suffix="pending-replay")
        endpoint = base_url + "/api/provider/fireworks/execution"
        operation_key = "provider-pending-same-key"
        completed = {}

        def execute():
            try:
                completed["result"] = _post_json(
                    endpoint,
                    {},
                    operation_key=operation_key,
                    origin=base_url,
                )
            except BaseException as error:
                completed["error"] = error

        worker = threading.Thread(target=execute)
        worker.start()
        assert connection.request_started.wait(5)

        try:
            status, pending = _post_error(
                endpoint,
                {},
                operation_key=operation_key,
                origin=base_url,
            )
            assert status == 409
            assert pending == {
                "error": (
                    "Provider execution is still in progress; retry the same "
                    "idempotency key."
                ),
                "code": "provider_execution_in_progress",
            }
            assert len(factory.calls) == 1
        finally:
            connection.release_response.set()
            worker.join(timeout=5)

        assert not worker.is_alive()
        assert "error" not in completed
        original = completed["result"]["execution"]
        assert original["outcome_code"] == "success"

        replay = _post_json(
            endpoint,
            {},
            operation_key=operation_key,
            origin=base_url,
        )
        assert replay["execution"]["idempotent_replay"] is True
        assert replay["execution"]["execution_id"] == original["execution_id"]
        assert len(factory.calls) == 1


def test_reset_during_execution_discards_stale_result_and_preserves_new_authority(
    tmp_path,
):
    connection = BlockingFakeHTTPSConnection(
        FakeHTTPSResponse(_response())
    )
    factory = RecordingConnectionFactory(connection)
    configuration = _configuration(factory)
    session = _session(tmp_path)
    session.configure_wave1_provider_execution(configuration)
    disclosure = session.wave1_provider_disclosure_payload()
    session.authorize_wave1_provider_egress(
        disclosure_id=disclosure["disclosure_id"],
        acknowledged=True,
        idempotency_key="provider-stale-authorization",
    )

    completed = {}

    def execute():
        completed["result"] = session.execute_wave1_provider_assist(
            configuration=configuration,
            idempotency_key="provider-stale-execution",
        )

    worker = threading.Thread(target=execute)
    worker.start()
    assert connection.request_started.wait(5)

    session.reset_to_synthetic_sample()
    reset_drafts = session.state_payload()["drafts"]
    session.authorize_wave1_provider_egress(
        disclosure_id=disclosure["disclosure_id"],
        acknowledged=True,
        idempotency_key="provider-new-authorization",
    )
    new_authorization = session._wave1_provider_authorization
    assert new_authorization is not None

    connection.release_response.set()
    worker.join(timeout=5)
    assert not worker.is_alive()

    result = completed["result"]
    assert result["execution"]["outcome_code"] == "stale_workflow"
    assert result["execution"]["proposals_created"] == 0
    _assert_content_free_terminal_receipt(result["execution"])
    assert session.state_payload()["drafts"] == reset_drafts
    assert session._wave1_provider_authorization is new_authorization

    delayed_replay = session.execute_wave1_provider_assist(
        configuration=configuration,
        idempotency_key="provider-stale-execution",
    )
    assert delayed_replay["execution"]["idempotent_replay"] is True
    assert delayed_replay["execution"]["execution_id"] == (
        result["execution"]["execution_id"]
    )
    assert session._wave1_provider_authorization is new_authorization
    assert len(factory.calls) == 1


def test_process_live_smoke_budget_is_atomic_and_survives_reset(tmp_path):
    factory = RecordingConnectionFactory(
        *[_connection(_response()) for _index in range(10)]
    )
    configuration = _configuration(factory)
    session = _session(tmp_path)
    session.configure_wave1_provider_execution(configuration)
    disclosure_id = session.wave1_provider_disclosure_payload()[
        "disclosure_id"
    ]

    for index in range(10):
        session.authorize_wave1_provider_egress(
            disclosure_id=disclosure_id,
            acknowledged=True,
            idempotency_key="provider-budget-auth-{0}".format(index),
        )
        result = session.execute_wave1_provider_assist(
            configuration=configuration,
            idempotency_key="provider-budget-exec-{0}".format(index),
        )
        assert result["execution"]["outcome_code"] == "success"

    state = session.state_payload()
    assert state["provider_execution"]["reserved_spend_usd"] == "0.10"
    assert state["provider_execution"]["remaining_spend_usd"] == "0.00"
    assert len(factory.calls) == 10

    session.authorize_wave1_provider_egress(
        disclosure_id=disclosure_id,
        acknowledged=True,
        idempotency_key="provider-budget-auth-blocked",
    )
    blocked = session.execute_wave1_provider_assist(
        configuration=configuration,
        idempotency_key="provider-budget-exec-blocked",
    )
    assert blocked["execution"]["outcome_code"] == "budget_exceeded"
    assert blocked["execution"]["provider_call_attempted"] is False
    assert blocked["execution"]["reserved_cost_usd"] == "0.00"
    _assert_content_free_terminal_receipt(blocked["execution"])
    assert len(factory.calls) == 10

    session.reset_to_synthetic_sample()
    after_reset = session.state_payload()["provider_execution"]
    assert after_reset["reserved_spend_usd"] == "0.10"
    assert after_reset["remaining_spend_usd"] == "0.00"

    replay = session.execute_wave1_provider_assist(
        configuration=configuration,
        idempotency_key="provider-budget-exec-blocked",
    )
    assert replay["execution"]["idempotent_replay"] is True
    assert len(factory.calls) == 10


def test_reset_clears_active_authority_but_preserves_replay_and_spend_history(
    tmp_path,
):
    factory = RecordingConnectionFactory(_connection(_response()))
    configuration = _configuration(factory)
    session = _session(tmp_path)
    session.configure_wave1_provider_execution(configuration)
    disclosure = session.wave1_provider_disclosure_payload()
    session.authorize_wave1_provider_egress(
        disclosure_id=disclosure["disclosure_id"],
        acknowledged=True,
        idempotency_key="provider-reset-authorization",
    )
    session.execute_wave1_provider_assist(
        configuration=configuration,
        idempotency_key="provider-reset-execution",
    )

    session.reset_to_synthetic_sample()
    state = session.state_payload()

    assert state["safety"]["provider_calls"] is True
    assert state["provider_execution"]["last_execution"] is not None
    assert state["provider_execution"]["authorization_active"] is False
    assert state["provider_execution"]["enabled"] is True
    assert state["provider_execution"]["configured"] is True
    assert len(session._wave1_provider_authorization_operations) == 1
    assert len(session._wave1_provider_execution_operations) == 1
    assert state["provider_execution"]["reserved_spend_usd"] == "0.01"
