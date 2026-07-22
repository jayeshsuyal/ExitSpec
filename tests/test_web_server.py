import json
import threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from exitspec.web import DemoSession, ExitSpecDemoServer


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


def _post_json(url: str, payload: dict):
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


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

        proved = _post_json(base_url + "/api/prove", {"scenario": "pass"})
        proof_pack = proved["proof_pack"]
        assert proof_pack["overall_verdict"] == "PASS"
        with urlopen(base_url + proof_pack["report_url"], timeout=5) as response:
            assert b"Proof Pack" in response.read()
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
                "transcript": "Customer: The POC must reach 95% tool selection accuracy.",
            },
        )
        state = captured["state"]
        assert state["transcript"]["title"] == "Synthetic discovery call"
        assert state["drafts"][0]["status"] == "NEEDS_REVIEW"
        assert state["drafts"][0]["proposed_criterion"] is None
        assert state["ready_to_prove"] is False

        reset = _post_json(base_url + "/api/reset", {})
        assert reset["transcript"]["id"] == "support-discovery-v1"
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
