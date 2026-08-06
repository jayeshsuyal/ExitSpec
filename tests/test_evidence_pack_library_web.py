import json
import threading
from contextlib import contextmanager
from http.client import HTTPConnection
from pathlib import Path

from exitspec.web import (
    DemoSession,
    EVIDENCE_LIBRARY_API_PATH,
    EVIDENCE_LIBRARY_PAGE_PATH,
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
) -> tuple[int, bytes, str]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Accept": "application/json"}
    if payload is not None:
        headers.update(
            {
                "Content-Type": "application/json",
                "Origin": "http://127.0.0.1:{0}".format(server.server_port),
            }
        )
    connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    try:
        connection.request(method, target, body=body, headers=headers)
        response = connection.getresponse()
        return (
            response.status,
            response.read(),
            response.getheader("Content-Type", ""),
        )
    finally:
        connection.close()


def _json_request(
    server: ExitSpecDemoServer,
    method: str,
    target: str,
    payload: dict | None = None,
) -> tuple[int, dict]:
    status, body, _ = _request(server, method, target, payload)
    return status, json.loads(body.decode("utf-8"))


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
        idempotency_key="library-customer-confirmation",
    )
    session.freeze()
    session.prove("pass")


def test_library_page_assets_and_empty_projection_are_real_routes(tmp_path):
    with _running_server(tmp_path) as server:
        page_status, page, page_type = _request(
            server,
            "GET",
            EVIDENCE_LIBRARY_PAGE_PATH,
        )
        css_status, css, css_type = _request(
            server,
            "GET",
            "/evidence_library.css",
        )
        js_status, javascript, js_type = _request(
            server,
            "GET",
            "/evidence_library.js",
        )
        api_status, payload = _json_request(
            server,
            "GET",
            EVIDENCE_LIBRARY_API_PATH,
        )

    assert page_status == css_status == js_status == api_status == 200
    assert "text/html" in page_type
    assert "text/css" in css_type
    assert "javascript" in js_type
    assert b"Evidence Packs" in page
    assert b"evidence-library-main" in css
    assert b"isTrustedProjection" in javascript
    assert payload == {
        "schema_version": "exitspec.evidence-pack-library.v1",
        "packs": [],
        "authorization": "Evidence is proof, not shipping authorization.",
    }


def test_terminal_pack_is_listed_openable_and_updates_after_handoff(tmp_path):
    with _running_server(tmp_path) as server:
        _complete_support_proof(server)
        state_before_reads = server.session.state_payload()
        status, before = _json_request(
            server,
            "GET",
            EVIDENCE_LIBRARY_API_PATH,
        )
        assert server.session.state_payload() == state_before_reads
        assert status == 200
        assert len(before["packs"]) == 1
        pack = before["packs"][0]
        assert pack["poc_id"] == SYNTHETIC_SUPPORT_AGENT_POC_ID
        assert pack["verdict"] == "PASS"
        assert pack["handoff_state"] == "READY_FOR_HANDOFF"
        assert len(pack["evidence_pack_sha256"]) == 64
        assert pack["evidence_pack_url"].endswith("/decision-packet.html")

        pack_status, pack_body, pack_type = _request(
            server,
            "GET",
            pack["evidence_pack_url"],
        )
        assert pack_status == 200
        assert "text/html" in pack_type
        assert b"POC Acceptance Evidence Pack" in pack_body

        closure_route = "/api/workspace/pocs/{0}/closure".format(
            SYNTHETIC_SUPPORT_AGENT_POC_ID
        )
        _, eligibility = _json_request(server, "GET", closure_route)
        close_status, _ = _json_request(
            server,
            "POST",
            closure_route,
            {
                "decision": "HANDOFF_COMPLETED",
                "decided_by": "field_engineer",
                "rationale": "Verified pack handed to the customer owner.",
                "evidence_binding": eligibility["eligible_evidence_binding"],
                "idempotency_key": "library-handoff-complete",
            },
        )
        after_status, after = _json_request(
            server,
            "GET",
            EVIDENCE_LIBRARY_API_PATH,
        )

    assert close_status == 201
    assert after_status == 200
    assert after["packs"][0]["handoff_state"] == "HANDOFF_COMPLETED"
    assert after["packs"][0]["evidence_pack_url"] == pack["evidence_pack_url"]
    assert after["packs"][0]["evidence_pack_sha256"] == (
        pack["evidence_pack_sha256"]
    )


def test_rerun_preserves_history_and_mutation_fails_closed(tmp_path):
    with _running_server(tmp_path) as server:
        _complete_support_proof(server)
        first_run = server.session.last_run
        assert first_run is not None
        second_run = server.session.prove("fail")

        history_status, history = _json_request(
            server,
            "GET",
            EVIDENCE_LIBRARY_API_PATH,
        )
        assert history_status == 200
        assert [pack["run_id"] for pack in history["packs"]] == [
            second_run.manifest.run_id,
            first_run.manifest.run_id,
        ]
        assert [pack["verdict"] for pack in history["packs"]] == [
            "FAIL",
            "PASS",
        ]
        assert [pack["handoff_state"] for pack in history["packs"]] == [
            "READY_FOR_HANDOFF",
            "HISTORICAL",
        ]

        (first_run.output_dir / "decision-packet.html").write_text(
            "tampered",
            encoding="utf-8",
        )
        rejected_status, rejected = _json_request(
            server,
            "GET",
            EVIDENCE_LIBRARY_API_PATH,
        )

    assert rejected_status == 503
    assert rejected == {"error": "Evidence Pack library is unavailable."}


def test_library_is_exact_read_only_and_rejects_route_parameters(tmp_path):
    with _running_server(tmp_path) as server:
        before = server.session.state_payload()
        query_status, query = _json_request(
            server,
            "GET",
            EVIDENCE_LIBRARY_API_PATH + "?all=true",
        )
        page_query_status, _ = _json_request(
            server,
            "GET",
            EVIDENCE_LIBRARY_PAGE_PATH + "?view=all",
        )
        post_status, post = _json_request(
            server,
            "POST",
            EVIDENCE_LIBRARY_API_PATH,
            {},
        )
        arbitrary_status, arbitrary = _json_request(
            server,
            "BREW",
            EVIDENCE_LIBRARY_API_PATH,
        )
        after = server.session.state_payload()

    assert query_status == page_query_status == 400
    assert "do not accept parameters" in query["error"]
    assert post_status == arbitrary_status == 405
    assert post == arbitrary == {
        "error": "Evidence Pack library method is not allowed."
    }
    assert before == after
