import json
import threading
from contextlib import contextmanager
from http.client import HTTPConnection
from pathlib import Path

from exitspec.web import (
    DemoSession,
    ExitSpecDemoServer,
    SYNTHETIC_SUPPORT_AGENT_POC_ID,
)


@contextmanager
def _running_server(tmp_path: Path):
    session = DemoSession.synthetic_support_agent(output_root=tmp_path / "runs")
    server = ExitSpecDemoServer(("127.0.0.1", 0), session)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield server
    finally:
        server.shutdown()
        worker.join(timeout=5)
        server.server_close()


def _request(
    server: ExitSpecDemoServer,
    method: str,
    target: str,
    payload: dict | None = None,
    *,
    same_origin: bool = True,
) -> tuple[int, dict]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Accept": "application/json"}
    if payload is not None:
        headers["Content-Type"] = "application/json"
        if same_origin:
            headers["Origin"] = "http://127.0.0.1:{0}".format(
                server.server_port
            )
    connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    try:
        connection.request(method, target, body=body, headers=headers)
        response = connection.getresponse()
        return response.status, json.loads(response.read().decode("utf-8"))
    finally:
        connection.close()


def _complete_support_proof(server: ExitSpecDemoServer) -> None:
    session = server.session
    first, second = session.reviewed_drafts
    session.review(
        first.id,
        "APPROVE",
        "field_engineer",
        "Customer confirmed the executable requirement.",
    )
    session.review(
        second.id,
        "REJECT",
        "field_engineer",
        "No executable latency requirement was agreed.",
    )
    session.create_customer_draft()
    assert session.customer_review_token is not None
    session.record_customer_decision(
        session.customer_review_token,
        decision="CONFIRM",
        confirmer="customer_approver",
        agreement_acknowledged=True,
        rationale="The exact POC agreement is confirmed.",
        idempotency_key="closure-web-customer-confirmation",
    )
    session.freeze()
    session.prove("pass")


def test_support_poc_closure_moves_active_to_completed_and_replays(tmp_path):
    with _running_server(tmp_path) as server:
        _complete_support_proof(server)
        state_before_closure = server.session.state_payload()
        route = (
            "/api/workspace/pocs/{0}/closure".format(
                SYNTHETIC_SUPPORT_AGENT_POC_ID
            )
        )

        status, eligibility = _request(server, "GET", route)
        assert status == 200
        binding = eligibility["eligible_evidence_binding"]
        assert eligibility["closeable"] is True
        assert eligibility["eligible_terminal_run_binding"] is None
        assert eligibility["allowed_decisions"] == [
            "HANDOFF_COMPLETED",
            "POC_STOPPED",
        ]
        assert binding["verdict"] == "PASS"
        assert binding["evidence_pack_sha256"]

        request = {
            "decision": "HANDOFF_COMPLETED",
            "decided_by": "field_engineer",
            "rationale": "Evidence Pack sent to the customer POC owner.",
            "evidence_binding": binding,
            "idempotency_key": "close-support-agent-web-v1",
        }
        created_status, created = _request(server, "POST", route, request)
        replay_status, replay = _request(server, "POST", route, request)
        active_status, active = _request(
            server,
            "GET",
            "/api/workspace?filter=Active",
        )
        completed_status, completed = _request(
            server,
            "GET",
            "/api/workspace?filter=Completed",
        )
        reset_status, reset_refusal = _request(
            server,
            "POST",
            "/api/reset",
            {},
        )

        assert created_status == 201
        assert replay_status == 200
        assert replay["idempotent_replay"] is True
        assert replay["closure"] == created["closure"]
        assert created["closure"]["shipping_authorized"] is False
        assert created["closure"]["authorization_scope"] == "POC_LIFECYCLE_ONLY"
        assert active_status == completed_status == 200
        assert reset_status == 409
        assert reset_refusal["code"] == "POC_LIFECYCLE_CLOSED"
        assert SYNTHETIC_SUPPORT_AGENT_POC_ID not in {
            poc["poc_id"] for poc in active["pocs"]
        }
        projected = next(
            poc
            for poc in completed["pocs"]
            if poc["poc_id"] == SYNTHETIC_SUPPORT_AGENT_POC_ID
        )
        assert projected["archive_state"] == "COMPLETED"
        assert projected["next_action_code"] == "NONE"
        assert projected["attention_required"] is False
        assert projected["latest_evidence_summary"]["status"] == "PASS"
        assert "Shipping was not authorized" in projected["next_human_action"]
        assert server.session.state_payload() == state_before_closure


def test_closure_api_rejects_stale_binding_and_keeps_poc_active(tmp_path):
    with _running_server(tmp_path) as server:
        _complete_support_proof(server)
        route = (
            "/api/workspace/pocs/{0}/closure".format(
                SYNTHETIC_SUPPORT_AGENT_POC_ID
            )
        )
        _, eligibility = _request(server, "GET", route)
        stale = dict(eligibility["eligible_evidence_binding"])
        stale["evidence_pack_sha256"] = "0" * 64

        status, refused = _request(
            server,
            "POST",
            route,
            {
                "decision": "HANDOFF_COMPLETED",
                "decided_by": "field_engineer",
                "rationale": "Attempted stale evidence handoff.",
                "evidence_binding": stale,
                "idempotency_key": "stale-close-support-agent",
            },
        )
        _, active = _request(server, "GET", "/api/workspace?filter=Active")
        _, completed = _request(
            server,
            "GET",
            "/api/workspace?filter=Completed",
        )

        assert status == 409
        assert "does not match" in refused["error"]
        assert SYNTHETIC_SUPPORT_AGENT_POC_ID in {
            poc["poc_id"] for poc in active["pocs"]
        }
        assert completed["pocs"] == []


def test_closure_api_requires_terminal_evidence_and_same_origin(tmp_path):
    with _running_server(tmp_path) as server:
        route = (
            "/api/workspace/pocs/{0}/closure".format(
                SYNTHETIC_SUPPORT_AGENT_POC_ID
            )
        )
        status, eligibility = _request(server, "GET", route)

        assert status == 200
        assert eligibility["closeable"] is False
        assert eligibility["eligible_evidence_binding"] is None

        fabricated = {
            "poc_id": SYNTHETIC_SUPPORT_AGENT_POC_ID,
            "contract_id": "support-agent-poc",
            "contract_version": "1.0.0",
            "contract_hash": "a" * 64,
            "run_id": "run_not_present",
            "verdict": "PASS",
            "evidence_pack_url": (
                "/artifacts/run_not_present/decision-packet.html"
            ),
            "evidence_pack_sha256": "b" * 64,
        }
        payload = {
            "decision": "HANDOFF_COMPLETED",
            "decided_by": "field_engineer",
            "rationale": "No terminal evidence exists.",
            "evidence_binding": fabricated,
            "idempotency_key": "close-without-evidence",
        }
        missing_origin_status, _ = _request(
            server,
            "POST",
            route,
            payload,
            same_origin=False,
        )
        unavailable_status, unavailable = _request(
            server,
            "POST",
            route,
            payload,
        )

        assert missing_origin_status == 403
        assert unavailable_status == 409
        assert "required" in unavailable["error"]
