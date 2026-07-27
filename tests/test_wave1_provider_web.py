import json
import threading
from contextlib import contextmanager
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

import exitspec.web as web_module
from exitspec.web import DemoSession, DemoStateError, ExitSpecDemoServer


ENV_CREDENTIAL_MARKER = "fw_test_environment_must_not_be_read"
UNSUPPORTED_FIELD_MARKER = "provider-secret-shaped-field"


def _session(tmp_path: Path) -> DemoSession:
    return DemoSession.synthetic_support_agent(output_root=tmp_path / "runs")


@contextmanager
def _running_server(tmp_path: Path):
    session = _session(tmp_path)
    server = ExitSpecDemoServer(("127.0.0.1", 0), session)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    base_url = "http://127.0.0.1:{0}".format(server.server_port)
    try:
        yield session, base_url
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


def _get_json(url: str) -> dict:
    with urlopen(url, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def _get_json_error(url: str) -> tuple[int, dict]:
    try:
        _get_json(url)
    except HTTPError as error:
        return error.code, json.loads(error.read().decode("utf-8"))
    raise AssertionError("Expected the request to fail.")


def _post_json(
    url: str,
    payload: dict,
    *,
    idempotency_key: str | None = None,
    origin: str | None = None,
) -> dict:
    headers = {"Content-Type": "application/json"}
    if idempotency_key is not None:
        headers["Idempotency-Key"] = idempotency_key
    if origin is not None:
        headers["Origin"] = origin
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def _post_json_error(
    url: str,
    payload: dict,
    *,
    idempotency_key: str | None = None,
    origin: str | None = None,
) -> tuple[int, dict]:
    try:
        _post_json(
            url,
            payload,
            idempotency_key=idempotency_key,
            origin=origin,
        )
    except HTTPError as error:
        return error.code, json.loads(error.read().decode("utf-8"))
    raise AssertionError("Expected the request to fail.")


def test_disclosure_is_content_free_and_cannot_enable_execution(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("FIREWORKS_API_KEY", ENV_CREDENTIAL_MARKER)
    session = _session(tmp_path)

    disclosure = session.wave1_provider_disclosure_payload()
    rendered = json.dumps(disclosure, sort_keys=True)

    assert disclosure["execution_available"] is False
    assert disclosure["authorization"] is None
    assert disclosure["synthetic_case"]["synthetic_only"] is True
    assert disclosure["acknowledgement_policy"]["required"] is True
    assert disclosure["acknowledgement_policy"]["one_time_use"] is True
    assert disclosure["acknowledgement_policy"]["ttl_seconds"] == 300
    assert disclosure["limits"]["max_attempts"] == 2
    assert disclosure["limits"]["max_request_cost_usd"] == "0.01"
    assert ENV_CREDENTIAL_MARKER not in rendered
    assert "Authorization" not in rendered
    assert "messages" not in disclosure
    assert "response_schema" not in disclosure
    assert session.state_payload()["safety"]["provider_calls"] is False
    assert "AuthorizedFireworksExecutor" not in web_module.__dict__


def test_authorization_keeps_capability_and_request_server_private(tmp_path):
    session = _session(tmp_path)
    disclosure = session.wave1_provider_disclosure_payload()

    response = session.authorize_wave1_provider_egress(
        disclosure_id=disclosure["disclosure_id"],
        acknowledged=True,
        idempotency_key="wave1-authorization-1",
    )

    authorization = response["authorization"]
    assert authorization["status"] == (
        "authorization_recorded_not_executed"
    )
    assert authorization["execution_available"] is False
    assert authorization["idempotent_replay"] is False
    assert authorization["replaced_previous"] is False
    state = session._wave1_provider_authorization
    assert state is not None
    rendered_response = json.dumps(response, sort_keys=True)
    private_values = (
        state.capability_token,
        state.request.messages[0].content,
        state.request.messages[1].content,
    )
    for private_value in private_values:
        assert private_value not in rendered_response
        assert private_value not in repr(state)
        assert private_value not in repr(session.__dict__)
    assert "capability_token" not in rendered_response
    assert "request" not in authorization
    assert session.state_payload()["safety"]["provider_calls"] is False


def test_authorization_is_idempotent_and_new_operation_replaces_private_state(
    tmp_path,
):
    session = _session(tmp_path)
    disclosure_id = session.wave1_provider_disclosure_payload()["disclosure_id"]

    first = session.authorize_wave1_provider_egress(
        disclosure_id=disclosure_id,
        acknowledged=True,
        idempotency_key="provider-auth-operation-1",
    )
    first_state = session._wave1_provider_authorization
    assert first_state is not None

    replay = session.authorize_wave1_provider_egress(
        disclosure_id=disclosure_id,
        acknowledged=True,
        idempotency_key="provider-auth-operation-1",
    )
    assert replay["authorization"]["idempotent_replay"] is True
    assert replay["authorization"]["acknowledgement_id"] == (
        first["authorization"]["acknowledgement_id"]
    )
    assert session._wave1_provider_authorization is first_state

    replacement = session.authorize_wave1_provider_egress(
        disclosure_id=disclosure_id,
        acknowledged=True,
        idempotency_key="provider-auth-operation-2",
    )
    assert replacement["authorization"]["replaced_previous"] is True
    assert replacement["authorization"]["acknowledgement_id"] != (
        first["authorization"]["acknowledgement_id"]
    )
    replacement_state = session._wave1_provider_authorization
    assert replacement_state is not first_state

    delayed_first_retry = session.authorize_wave1_provider_egress(
        disclosure_id=disclosure_id,
        acknowledged=True,
        idempotency_key="provider-auth-operation-1",
    )
    assert delayed_first_retry["authorization"]["idempotent_replay"] is True
    assert delayed_first_retry["authorization"]["acknowledgement_id"] == (
        first["authorization"]["acknowledgement_id"]
    )
    assert session._wave1_provider_authorization is replacement_state


def test_conflicting_idempotency_reuse_is_rejected_without_replacing_state(
    tmp_path,
):
    session = _session(tmp_path)
    disclosure_id = session.wave1_provider_disclosure_payload()["disclosure_id"]
    session.authorize_wave1_provider_egress(
        disclosure_id=disclosure_id,
        acknowledged=True,
        idempotency_key="provider-auth-conflict",
    )
    original = session._wave1_provider_authorization

    with pytest.raises(DemoStateError, match="review the current disclosure"):
        session.authorize_wave1_provider_egress(
            disclosure_id="stale-disclosure",
            acknowledged=True,
            idempotency_key="provider-auth-conflict",
        )

    assert session._wave1_provider_authorization is original


@pytest.mark.parametrize(
    ("disclosure_id", "acknowledged", "match"),
    [
        ("stale-disclosure", True, "review the current disclosure"),
        (None, True, "review the current disclosure"),
        ("CURRENT", False, "explicit acknowledgement"),
    ],
)
def test_authorization_rejects_stale_or_unacknowledged_requests(
    tmp_path,
    disclosure_id,
    acknowledged,
    match,
):
    session = _session(tmp_path)
    current = session.wave1_provider_disclosure_payload()["disclosure_id"]
    supplied = current if disclosure_id == "CURRENT" else disclosure_id

    with pytest.raises(DemoStateError, match=match):
        session.authorize_wave1_provider_egress(
            disclosure_id=supplied,
            acknowledged=acknowledged,
            idempotency_key="provider-auth-rejected",
        )

    assert session._wave1_provider_authorization is None


def test_new_intake_and_reset_clear_unused_provider_authorization(tmp_path):
    session = _session(tmp_path)
    disclosure_id = session.wave1_provider_disclosure_payload()["disclosure_id"]
    session.authorize_wave1_provider_egress(
        disclosure_id=disclosure_id,
        acknowledged=True,
        idempotency_key="provider-auth-before-intake",
    )
    assert session._wave1_provider_authorization is not None

    session.intake(
        "Customer: The POC must reach 95% exact tool-selection accuracy."
    )
    assert session._wave1_provider_authorization is None
    assert session._wave1_provider_authorization_operations == {}

    session.authorize_wave1_provider_egress(
        disclosure_id=disclosure_id,
        acknowledged=True,
        idempotency_key="provider-auth-before-reset",
    )
    session.reset_to_synthetic_sample()
    assert session._wave1_provider_authorization is None
    assert session._wave1_provider_authorization_operations == {}


def test_authorization_operation_history_is_bounded_fail_closed(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(
        web_module,
        "MAX_WAVE1_PROVIDER_AUTHORIZATION_OPERATIONS",
        1,
    )
    session = _session(tmp_path)
    disclosure_id = session.wave1_provider_disclosure_payload()["disclosure_id"]
    session.authorize_wave1_provider_egress(
        disclosure_id=disclosure_id,
        acknowledged=True,
        idempotency_key="provider-auth-ledger-1",
    )
    active = session._wave1_provider_authorization

    with pytest.raises(DemoStateError, match="operation limit"):
        session.authorize_wave1_provider_egress(
            disclosure_id=disclosure_id,
            acknowledged=True,
            idempotency_key="provider-auth-ledger-2",
        )

    assert session._wave1_provider_authorization is active


def test_http_disclosure_and_authorization_never_expose_a_capability(tmp_path):
    with _running_server(tmp_path) as (session, base_url):
        disclosure = _get_json(
            base_url + "/api/provider/fireworks/disclosure"
        )
        authorized = _post_json(
            base_url + "/api/provider/fireworks/authorization",
            {
                "disclosure_id": disclosure["disclosure_id"],
                "acknowledged": True,
            },
            idempotency_key="provider-http-authorization-1",
            origin=base_url,
        )

        serialized = json.dumps(authorized, sort_keys=True)
        assert authorized["authorization"]["status"] == (
            "authorization_recorded_not_executed"
        )
        assert authorized["authorization"]["execution_available"] is False
        assert "capability_token" not in serialized
        assert "Authorization" not in serialized
        assert "messages" not in serialized
        assert "response_schema" not in serialized
        current = _get_json(
            base_url + "/api/provider/fireworks/disclosure"
        )
        assert current["authorization"]["acknowledgement_id"] == (
            authorized["authorization"]["acknowledgement_id"]
        )
        assert _get_json(base_url + "/api/state")["safety"][
            "provider_calls"
        ] is False
        assert session._wave1_provider_authorization is not None


def test_http_authorization_requires_origin_json_ack_and_idempotency(tmp_path):
    with _running_server(tmp_path) as (_session_value, base_url):
        disclosure = _get_json(
            base_url + "/api/provider/fireworks/disclosure"
        )
        endpoint = base_url + "/api/provider/fireworks/authorization"
        base_payload = {
            "disclosure_id": disclosure["disclosure_id"],
            "acknowledged": True,
        }

        status, error = _post_json_error(
            endpoint,
            base_payload,
            origin=base_url,
        )
        assert status == 400
        assert "idempotency key" in error["error"].lower()

        status, error = _post_json_error(
            endpoint,
            base_payload,
            idempotency_key="provider-origin-required",
        )
        assert status == 403
        assert "origin" in error["error"].lower()

        status, error = _post_json_error(
            endpoint,
            base_payload,
            idempotency_key="provider-origin-rejected",
            origin="https://attacker.invalid",
        )
        assert status == 403
        assert "origin" in error["error"].lower()

        status, error = _post_json_error(
            endpoint,
            base_payload,
            idempotency_key="provider-origin-alias-rejected",
            origin=base_url.replace("127.0.0.1", "localhost"),
        )
        assert status == 403
        assert "origin" in error["error"].lower()

        status, error = _post_json_error(
            endpoint,
            {
                "disclosure_id": " {0} ".format(
                    disclosure["disclosure_id"]
                ),
                "acknowledged": True,
            },
            idempotency_key="provider-disclosure-padding-rejected",
            origin=base_url,
        )
        assert status == 400
        assert "match exactly" in error["error"].lower()

        status, error = _post_json_error(
            endpoint,
            {
                **base_payload,
                UNSUPPORTED_FIELD_MARKER: ENV_CREDENTIAL_MARKER,
            },
            idempotency_key="provider-field-rejected",
            origin=base_url,
        )
        assert status == 400
        assert error == {"error": "Request contains unsupported fields."}
        assert UNSUPPORTED_FIELD_MARKER not in json.dumps(error)
        assert ENV_CREDENTIAL_MARKER not in json.dumps(error)

        status, error = _post_json_error(
            endpoint,
            {
                "disclosure_id": disclosure["disclosure_id"],
                "acknowledged": False,
            },
            idempotency_key="provider-ack-rejected",
            origin=base_url,
        )
        assert status == 409
        assert "acknowledgement" in error["error"].lower()


def test_provider_authority_routes_reject_url_parameters_without_state_change(
    tmp_path,
):
    with _running_server(tmp_path) as (session, base_url):
        status, error = _get_json_error(
            base_url + "/api/provider/fireworks/disclosure?ignored=true"
        )
        assert status == 400
        assert "url parameters" in error["error"].lower()

        disclosure = _get_json(
            base_url + "/api/provider/fireworks/disclosure"
        )
        status, error = _post_json_error(
            base_url
            + "/api/provider/fireworks/authorization?ignored=true",
            {
                "disclosure_id": disclosure["disclosure_id"],
                "acknowledged": True,
            },
            idempotency_key="provider-query-rejected",
            origin=base_url,
        )
        assert status == 400
        assert "url parameters" in error["error"].lower()
        assert session._wave1_provider_authorization is None
        assert session._wave1_provider_authorization_operations == {}


def test_provider_execution_route_does_not_exist(tmp_path):
    with _running_server(tmp_path) as (_session_value, base_url):
        status, error = _post_json_error(
            base_url + "/api/provider/fireworks/execution",
            {},
            idempotency_key="provider-execution-must-not-exist",
            origin=base_url,
        )

        assert status == 404
        assert error == {"error": "Unknown API route."}
