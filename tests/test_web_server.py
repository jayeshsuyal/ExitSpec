import json
import threading
from contextlib import contextmanager
from http.client import HTTPConnection
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

import pytest

import exitspec.web as web_module
from exitspec.web import DemoSession, ExitSpecDemoServer


RAW_EMAIL = "owner@example.com"
RAW_API_TOKEN = "sk_live_1234567890"
RAW_CUSTOMER_TERM = "Project Phoenix"
BODY_MARKER = "body-secret-marker"
PATH_MARKER = "path-secret-marker"


def _running_server(tmp_path: Path):
    session = DemoSession.synthetic_support_agent(output_root=tmp_path / "runs")
    server = ExitSpecDemoServer(("127.0.0.1", 0), session)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    base_url = "http://127.0.0.1:{0}".format(server.server_port)
    return server, worker, base_url


def _get_json(url: str):
    with urlopen(url, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def _get_bytes(url: str) -> bytes:
    with urlopen(url, timeout=5) as response:
        return response.read()


def _post_json(url: str, payload: dict):
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def _post_raw(
    url: str,
    body: bytes,
    *,
    content_type: str | None,
    origin: str | None = None,
):
    parsed = urlsplit(url)
    assert parsed.hostname is not None
    assert parsed.port is not None
    headers = {}
    if content_type is not None:
        headers["Content-Type"] = content_type
    if origin is not None:
        headers["Origin"] = origin

    target = parsed.path or "/"
    if parsed.query:
        target = "{0}?{1}".format(target, parsed.query)
    connection = HTTPConnection(parsed.hostname, parsed.port, timeout=5)
    try:
        connection.request("POST", target, body=body, headers=headers)
        response = connection.getresponse()
        response_body = response.read()
        response_headers = {
            name.lower(): value for name, value in response.getheaders()
        }
        return response.status, response_body, response_headers
    finally:
        connection.close()


def _valid_review_body(draft_id: str) -> bytes:
    return json.dumps(
        {
            "draft_id": draft_id,
            "decision": "APPROVE",
            "reviewer": "field_engineer",
            "rationale": BODY_MARKER,
        }
    ).encode("utf-8")


def test_local_api_runs_define_to_prove_to_proof_pack(tmp_path):
    server, worker, base_url = _running_server(tmp_path)
    try:
        initial = _get_json(base_url + "/api/state")
        first, second = initial["drafts"]

        approved = _post_json(
            base_url + "/api/review",
            {
                "draft_id": first["id"],
                "decision": "APPROVE",
                "reviewer": "field_engineer",
                "rationale": "Customer confirmed the measurable requirement.",
            },
        )
        assert approved["reviewed_draft"]["status"] == "APPROVED"
        _post_json(
            base_url + "/api/review",
            {
                "draft_id": second["id"],
                "decision": "REJECT",
                "reviewer": "field_engineer",
                "rationale": "No acceptance measurement was agreed for this request.",
            },
        )

        customer_draft = _post_json(base_url + "/api/customer-draft", {})
        customer_draft_url = customer_draft["customer_draft_url"]
        assert customer_draft_url is not None
        with urlopen(base_url + customer_draft_url, timeout=5) as response:
            assert b"Proposed POC acceptance criteria" in response.read()

        customer_review_url = customer_draft["customer_review_url"]
        assert customer_review_url is not None
        review_token = customer_review_url.rstrip("/").split("/")[-1]
        _post_json(
            base_url + "/api/review/{0}/decision".format(review_token),
            {
                "decision": "CONFIRM",
                "agreement_acknowledged": True,
                "confirmer": "customer_approver",
                "rationale": "The exact POC requirements are confirmed.",
                "idempotency_key": "confirm-support-agent-web-v1",
            },
        )
        frozen = _post_json(base_url + "/api/freeze", {})
        assert frozen["contract"]["status"] == "FROZEN"

        proved = _post_json(base_url + "/api/prove", {"scenario": "pass"})
        proof_pack = proved["proof_pack"]
        assert proof_pack["overall_verdict"] == "PASS"
        assert "POC Acceptance Evidence Pack" in proof_pack["next_human_action"]
        with urlopen(base_url + proof_pack["report_url"], timeout=5) as response:
            assert b"POC Acceptance Evidence Pack" in response.read()
    finally:
        server.shutdown()
        worker.join(timeout=5)
        server.server_close()


def test_local_api_captures_pasted_source_without_claiming_approval(tmp_path):
    server, worker, base_url = _running_server(tmp_path)
    try:
        captured = _post_json(
            base_url + "/api/intake",
            {
                "title": "Synthetic discovery call",
                "transcript": (
                    "{0}: Contact {1}; api_key={2}; the POC must reach 95% "
                    "tool selection accuracy."
                ).format(
                    RAW_CUSTOMER_TERM,
                    RAW_EMAIL,
                    RAW_API_TOKEN,
                ),
                "customer_terms": [RAW_CUSTOMER_TERM],
            },
        )
        state = captured["state"]
        serialized_response = json.dumps(captured)
        serialized_session = json.dumps(server.session.state_payload())
        for secret in (RAW_EMAIL, RAW_API_TOKEN, RAW_CUSTOMER_TERM):
            assert secret not in serialized_response
            assert secret not in serialized_session
            assert secret not in captured["notice"]

        assert state["transcript"]["title"] == "Synthetic discovery call"
        assert state["transcript"]["lines"][0]["speaker"] == (
            "[REDACTED:CUSTOMER_TERM]"
        )
        assert "[REDACTED:EMAIL]" in state["transcript"]["lines"][0]["text"]
        assert "[REDACTED:API_TOKEN]" in state["transcript"]["lines"][0]["text"]
        assert set(state["transcript_redaction"]) == {
            "policy_version",
            "decision",
            "counts",
            "line_numbers",
        }
        assert state["transcript_redaction"]["counts"]["EMAIL"] == 1
        assert state["transcript_redaction"]["counts"]["API_TOKEN"] == 1
        assert state["transcript_redaction"]["counts"]["CUSTOMER_TERM"] == 1
        assert state["drafts"][0]["status"] == "NEEDS_REVIEW"
        assert state["drafts"][0]["proposed_criterion"] is None
        assert state["ready_to_prove"] is False
        assert state["safety"]["provider_calls"] is False

        try:
            _post_json(
                base_url + "/api/draft/define",
                {
                    "draft_id": state["drafts"][0]["id"],
                    "title": "Exact tool selection",
                    "threshold_percent": 95,
                    "minimum_samples": 200,
                    "workload_slice": "support-tool-selection-v2",
                    "normalized_claim": "A contradictory client-authored claim.",
                },
            )
        except HTTPError as error:
            assert error.code == 400
            error_payload = json.loads(error.read().decode("utf-8"))
        else:
            raise AssertionError("Client-authored normalized claim was accepted.")
        assert "generated from the structured rule" in error_payload["error"]

        defined = _post_json(
            base_url + "/api/draft/define",
            {
                "draft_id": state["drafts"][0]["id"],
                "title": "Exact tool selection",
                "threshold_percent": 95,
                "minimum_samples": 200,
                "workload_slice": "support-tool-selection-v2",
            },
        )
        defined_draft = defined["defined_draft"]
        generated_claim = defined_draft["normalized_claim"]
        assert defined_draft["status"] == "NEEDS_REVIEW"
        assert defined_draft["open_questions"] == []
        assert "95%" in generated_claim
        assert "200 fixed cases" in generated_claim
        assert "support-tool-selection-v2" in generated_claim
        assert (
            defined_draft["proposed_criterion"]["normalized_claim"]
            == generated_claim
        )
        assert defined["state"]["ready_to_prepare_customer_review"] is False

        reset = _post_json(base_url + "/api/reset", {})
        assert reset["transcript"]["id"] == "support-discovery-v1"
        assert reset["transcript_redaction"] is None
        assert reset["safety"]["provider_calls"] is False
    finally:
        server.shutdown()
        worker.join(timeout=5)
        server.server_close()


def test_local_api_errors_do_not_echo_raw_sensitive_values(tmp_path):
    server, worker, base_url = _running_server(tmp_path)
    try:
        malformed = "{0} contact {1} with api_key={2}".format(
            RAW_CUSTOMER_TERM,
            RAW_EMAIL,
            RAW_API_TOKEN,
        )
        try:
            _post_json(
                base_url + "/api/intake",
                {
                    "transcript": malformed,
                    "customer_terms": [RAW_CUSTOMER_TERM],
                },
            )
        except HTTPError as error:
            assert error.code == 409
            error_payload = json.loads(error.read().decode("utf-8"))
        else:
            raise AssertionError("Malformed transcript unexpectedly passed intake.")

        serialized_error = json.dumps(error_payload)
        assert "line 1 must use 'Speaker: message'" in error_payload["error"]
        assert RAW_EMAIL not in serialized_error
        assert RAW_API_TOKEN not in serialized_error
        assert RAW_CUSTOMER_TERM not in serialized_error
        assert server.session.state_payload()["safety"]["provider_calls"] is False
    finally:
        server.shutdown()
        worker.join(timeout=5)
        server.server_close()


def test_local_api_rejects_non_json_media_types_without_mutation(tmp_path):
    server, worker, base_url = _running_server(tmp_path)
    try:
        initial = _get_json(base_url + "/api/state")
        body = _valid_review_body(initial["drafts"][0]["id"])
        state_before = _get_bytes(base_url + "/api/state")
        request_url = base_url + "/api/review?source=" + PATH_MARKER

        for content_type in (
            None,
            "text/plain",
            "application/x-www-form-urlencoded",
        ):
            status, error_body, headers = _post_raw(
                request_url,
                body,
                content_type=content_type,
            )

            assert status == 415
            assert error_body == (
                b'{"error":"Content-Type must be application/json."}'
            )
            assert BODY_MARKER.encode("utf-8") not in error_body
            assert PATH_MARKER.encode("utf-8") not in error_body
            assert "access-control-allow-origin" not in headers
            assert _get_bytes(base_url + "/api/state") == state_before
    finally:
        server.shutdown()
        worker.join(timeout=5)
        server.server_close()


def test_local_api_rejects_untrusted_origins_without_mutation(tmp_path):
    server, worker, base_url = _running_server(tmp_path)
    try:
        initial = _get_json(base_url + "/api/state")
        body = _valid_review_body(initial["drafts"][0]["id"])
        state_before = _get_bytes(base_url + "/api/state")
        request_url = base_url + "/api/review?source=" + PATH_MARKER
        untrusted_origins = (
            "http://localhost.attacker.invalid:{0}".format(server.server_port),
            "null",
            "http://[::1",
            "http://127.0.0.1:{0}".format(server.server_port + 1),
            "https://127.0.0.1:{0}".format(server.server_port),
        )

        for origin in untrusted_origins:
            status, error_body, headers = _post_raw(
                request_url,
                body,
                content_type="application/json",
                origin=origin,
            )

            assert status == 403
            assert error_body == b'{"error":"Origin is not allowed."}'
            assert BODY_MARKER.encode("utf-8") not in error_body
            assert PATH_MARKER.encode("utf-8") not in error_body
            assert "access-control-allow-origin" not in headers
            assert _get_bytes(base_url + "/api/state") == state_before
    finally:
        server.shutdown()
        worker.join(timeout=5)
        server.server_close()


@pytest.mark.parametrize(
    "content_type",
    ("application/json", "application/json; charset=utf-8"),
)
def test_local_api_accepts_same_origin_json(
    tmp_path,
    content_type,
):
    server, worker, base_url = _running_server(tmp_path)
    try:
        initial = _get_json(base_url + "/api/state")
        state_before = _get_bytes(base_url + "/api/state")
        status, response_body, headers = _post_raw(
            base_url + "/api/review",
            _valid_review_body(initial["drafts"][0]["id"]),
            content_type=content_type,
            origin=base_url,
        )

        assert status == 200
        response_payload = json.loads(response_body.decode("utf-8"))
        assert response_payload["reviewed_draft"]["status"] == "APPROVED"
        assert _get_bytes(base_url + "/api/state") != state_before
        assert "access-control-allow-origin" not in headers
    finally:
        server.shutdown()
        worker.join(timeout=5)
        server.server_close()


def test_local_api_refuses_path_traversal_outside_demo_artifacts(tmp_path):
    server, worker, base_url = _running_server(tmp_path)
    try:
        try:
            urlopen(base_url + "/artifacts/../../README.md", timeout=5)
        except HTTPError as error:
            assert error.code == 404
        else:
            raise AssertionError("Traversal request unexpectedly returned a response.")
    finally:
        server.shutdown()
        worker.join(timeout=5)
        server.server_close()


def test_serve_demo_keeps_bundled_resources_alive_through_reset_and_close(
    tmp_path,
    monkeypatch,
):
    lifecycle = {"open": False, "closed": False}
    original_resource_context = web_module.support_agent_demo_paths

    @contextmanager
    def tracked_resources():
        with original_resource_context() as paths:
            lifecycle["open"] = True
            try:
                yield paths
            finally:
                lifecycle["open"] = False
                lifecycle["closed"] = True

    monkeypatch.setattr(web_module, "support_agent_demo_paths", tracked_resources)
    server = web_module.serve_demo(
        host="127.0.0.1",
        port=0,
        output_root=tmp_path / "runs",
    )
    try:
        assert lifecycle == {"open": True, "closed": False}
        assert server.session.fixture_path.is_file()
        server.session.intake("Customer: The agent must reach 95% exact tool selection.")
        server.session.reset_to_synthetic_sample()
        assert server.session.state_payload()["transcript"]["id"] == (
            "support-discovery-v1"
        )
        assert server.session.fixture_path.is_file()
        assert lifecycle == {"open": True, "closed": False}
    finally:
        server.server_close()

    assert lifecycle == {"open": False, "closed": True}
