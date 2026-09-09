"""Real-browser regressions for audit findings F01, F03, F04, and F05."""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

import exitspec.web
from exitspec.poc_creation import DraftPOCCreateRequest
from tests.test_a6_executable_orchestration import _pack_service
from tests.test_a6_source_neutral_evidence_http import _running_source_server
from tests.test_poc_performance_lifecycle_web_transport import (
    _create_defined_performance_poc,
    _freeze_agreement,
    _running_server,
)

playwright = pytest.importorskip("playwright.sync_api")
ROOT = Path(__file__).resolve().parents[1]
assert Path(exitspec.web.__file__).resolve().is_relative_to(ROOT / "src")
POC_ID = "poc_a6_executable_test"


@pytest.fixture
def browser_page(request):
    """Keep test traffic local; optionally capture traces or serve baseline JS."""
    with playwright.sync_playwright() as runtime:
        browser = runtime.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1280, "height": 736})
        context.route(
            "**/*",
            lambda route: (
                route.continue_()
                if route.request.url.startswith("http://127.0.0.1:")
                else route.abort()
            ),
        )
        baseline = os.environ.get("EXITSPEC_AUDIT_BASELINE_REF")
        if baseline:
            for name in ("app.js", "proof.js", "generic_evidence.js"):
                body = subprocess.run(
                    ["git", "show", f"{baseline}:src/exitspec/static/{name}"],
                    cwd=ROOT,
                    check=True,
                    capture_output=True,
                ).stdout
                context.route(
                    f"**/{name}",
                    lambda route, _request, body=body: route.fulfill(
                        status=200,
                        content_type="application/javascript",
                        body=body,
                    ),
                )
        artifacts = os.environ.get("EXITSPEC_AUDIT_ARTIFACTS")
        destination = None
        if artifacts:
            destination = Path(artifacts) / re.sub(
                r"[^a-zA-Z0-9_-]", "_", request.node.name
            )
            destination.mkdir(parents=True, exist_ok=True)
            context.tracing.start(screenshots=True, snapshots=True, sources=True)
        page = context.new_page()
        page.set_default_timeout(10_000)
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        try:
            yield page, context
            assert errors == []
        finally:
            if destination:
                page.screenshot(path=str(destination / "final.png"), full_page=True)
                context.tracing.stop(path=str(destination / "trace.zip"))
            context.close()
            browser.close()


def _freeze_seeded_browser(page, context, base):
    page.goto(base + "/app?mode=recording", wait_until="networkidle")
    page.get_by_role("button", name="Matches intent").click()
    page.get_by_role("button", name="Keep as context").click()
    page.locator("#create-customer-draft").click()
    link = page.locator("#customer-draft-link")
    playwright.expect(link).to_be_visible()
    customer = context.new_page()
    try:
        customer.goto(base + link.get_attribute("href"))
        customer.locator("#agreement-checkbox").check()
        customer.locator("#confirm-requirements").click()
        playwright.expect(page.locator("#freeze-contract")).to_be_enabled()
        page.locator("#freeze-contract").click()
        playwright.expect(page.locator("#run-proof")).to_be_enabled()
    finally:
        customer.close()


@pytest.mark.parametrize("delivery", ["success", "failure"])
def test_f01_reset_rejects_late_proof_success_or_error(
    tmp_path, browser_page, delivery
):
    page, context = browser_page
    with _running_server(tmp_path) as server:
        base = f"http://127.0.0.1:{server.server_port}"
        _freeze_seeded_browser(page, context, base)
        held = []

        def hold_completed_proof(route):
            response = route.fetch()
            held.append((route, response))
            page.evaluate("window.__auditProofResponseHeld = true")

        page.route(base + "/api/prove", hold_completed_proof)
        page.locator("#run-proof").click()
        # The server response is complete, while the browser response is held.
        page.wait_for_function("window.__auditProofResponseHeld === true")
        assert len(held) == 1
        assert page.request.get(base + "/api/state").json()["proof_pack"] is not None
        page.locator("#recording-restart").click()
        playwright.expect(
            page.get_by_role("button", name="Matches intent")
        ).to_be_visible()
        assert page.request.get(base + "/api/state").json()["proof_pack"] is None
        route, response = held[0]
        if delivery == "success":
            route.fulfill(response=response)
        else:
            route.fulfill(
                status=503,
                content_type="application/json",
                body=json.dumps({"error": "Late proof failure after reset"}),
            )
        page.wait_for_load_state("networkidle")
        playwright.expect(page.locator("#pack-verdict")).to_have_text("AWAITING")
        playwright.expect(page.locator("#proof-pack-link")).to_be_hidden()
        playwright.expect(page.locator("#prove-status")).to_have_text("")
        playwright.expect(
            page.get_by_role("button", name="Matches intent")
        ).to_be_visible()
        assert page.request.get(base + "/api/state").json()["proof_pack"] is None


@contextmanager
def _recovering_endpoint(*, ready=False, held=False):
    state = {"ready": ready, "requests": 0}
    lock = threading.Lock()
    release = threading.Event()
    if not held:
        release.set()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            with lock:
                state["requests"] += 1
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            release.wait(timeout=10)
            payload = (
                b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\ndata: [DONE]\n\n'
                if state["ready"]
                else b'{"error":"local test endpoint is not ready"}'
            )
            self.send_response(200 if state["ready"] else 503)
            self.send_header(
                "Content-Type",
                "text/event-stream" if state["ready"] else "application/json",
            )
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            return

    endpoint = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=endpoint.serve_forever, daemon=True)
    worker.start()
    try:
        yield (
            f"http://127.0.0.1:{endpoint.server_port}/v1/chat/completions",
            state,
            release,
        )
    finally:
        release.set()
        endpoint.shutdown()
        worker.join(timeout=5)
        endpoint.server_close()
        assert not worker.is_alive()


@pytest.mark.parametrize("terminal_delivery", ["poll", "start-response"])
def test_f03_terminal_failure_allows_fresh_retry_without_reload(
    tmp_path, browser_page, terminal_delivery
):
    page, _ = browser_page
    with (
        _recovering_endpoint(held=terminal_delivery == "poll") as (
            endpoint,
            state,
            release,
        ),
        _running_server(tmp_path) as server,
    ):
        base = f"http://127.0.0.1:{server.server_port}"
        poc = _create_defined_performance_poc(server)
        _freeze_agreement(server, poc, endpoint)
        if terminal_delivery == "start-response":
            server.poc_performance_run_service._worker_launcher = lambda task: task()
        api = base + f"/api/pocs/{poc}/runs"
        bodies = []
        page.on(
            "request",
            lambda request: (
                bodies.append(json.loads(request.post_data))
                if request.url == api and request.method == "POST"
                else None
            ),
        )
        page.goto(base + f"/app/pocs/{poc}", wait_until="networkidle")
        page.locator("#execution-acknowledged").check()
        page.locator("#run-proof").click()
        if terminal_delivery == "poll":
            playwright.expect(page.locator("#execution-status")).to_have_text("RUNNING")
            release.set()
        playwright.expect(page.locator("#execution-status")).to_have_text("BLOCKED")
        first = page.request.get(api + "/latest").json()
        assert first["is_terminal"] is True
        assert first["status"] == "BLOCKED"
        assert state["requests"] == 1
        state["ready"] = True
        page.locator("#execution-acknowledged").check()
        page.locator("#run-proof").click()
        playwright.expect(page.locator("#execution-status")).to_have_text(
            "COMPLETED", timeout=20_000
        )
        current = page.request.get(api + "/latest").json()
        assert len(bodies) == 2
        assert bodies[0]["idempotency_key"] != bodies[1]["idempotency_key"]
        assert current["operation_id"] != first["operation_id"]
        assert state["requests"] == 1 + current["authorized_request_count"]
        playwright.expect(page.locator("#operation-reference")).to_have_text(
            current["operation_id"]
        )
        playwright.expect(page.locator("#evidence-pack-link")).to_have_attribute(
            "href", current["evidence_pack_url"]
        )


@pytest.mark.parametrize("fault", ["lost", "malformed"])
def test_f03_uncertain_start_retries_the_same_operation(tmp_path, browser_page, fault):
    page, _ = browser_page
    with (
        _recovering_endpoint(ready=True) as (endpoint, state, _),
        _running_server(tmp_path) as server,
    ):
        base = f"http://127.0.0.1:{server.server_port}"
        poc = _create_defined_performance_poc(server)
        _freeze_agreement(server, poc, endpoint)
        server.poc_performance_run_service._worker_launcher = lambda task: task()
        api = base + f"/api/pocs/{poc}/runs"
        bodies = []
        replies = []

        def uncertain_response(route):
            bodies.append(json.loads(route.request.post_data))
            response = route.fetch()
            replies.append(response.json())
            if len(bodies) == 1 and fault == "lost":
                route.abort("connectionfailed")
            elif len(bodies) == 1:
                malformed = response.json()
                malformed["operation"]["is_terminal"] = False
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps(malformed),
                )
            else:
                route.fulfill(response=response)

        page.route(api, uncertain_response)
        page.goto(base + f"/app/pocs/{poc}", wait_until="networkidle")
        page.locator("#execution-acknowledged").check()
        page.locator("#run-proof").click()
        playwright.expect(page.locator("#performance-error")).to_be_visible()
        first = page.request.get(api + "/latest").json()
        page.locator("#run-proof").click()
        playwright.expect(page.locator("#execution-status")).to_have_text("COMPLETED")
        current = page.request.get(api + "/latest").json()
        assert len(bodies) == 2
        assert bodies[0] == bodies[1]
        assert replies[1]["replayed"] is True
        assert current["operation_id"] == first["operation_id"]
        assert state["requests"] == current["authorized_request_count"]
        playwright.expect(page.locator("#operation-reference")).to_have_text(
            current["operation_id"]
        )


@contextmanager
def _generic_server(tmp_path):
    with _running_source_server(tmp_path) as (server, base):
        service = _pack_service(tmp_path / "service")
        server.generic_evidence_service = service
        server.evidence_artifact_root = service._output_root
        server.draft_poc_service.create(
            DraftPOCCreateRequest(
                poc_id=POC_ID,
                display_name="Proof recovery regression",
                customer_label="Synthetic local customer",
                use_case="Verify the audit recovery fixes.",
                owner="audit.owner",
                first_source_choice="DOCUMENT",
            ),
            idempotency_key="proof-audit-draft",
        )
        yield service, base, base + f"/api/pocs/{POC_ID}/evidence"


@pytest.mark.parametrize("fault", ["post-loss", "refresh-loss", "stale-refresh"])
def test_f04_uncertain_evidence_start_cannot_duplicate_the_attempt(
    tmp_path, browser_page, fault
):
    page, _ = browser_page
    with _generic_server(tmp_path) as (service, base, api):
        before = service.snapshot_payload(POC_ID)
        bodies = []
        refresh_fault_sent = False

        def inject_fault(route):
            nonlocal refresh_fault_sent
            if route.request.method == "POST":
                bodies.append(json.loads(route.request.post_data))
                response = route.fetch()
                if len(bodies) == 1 and fault == "post-loss":
                    route.abort("connectionfailed")
                else:
                    route.fulfill(response=response)
            elif len(bodies) == 1 and fault != "post-loss" and not refresh_fault_sent:
                refresh_fault_sent = True
                if fault == "refresh-loss":
                    route.abort("connectionfailed")
                else:
                    route.fulfill(
                        status=200,
                        content_type="application/json",
                        body=json.dumps(before),
                    )
            else:
                route.continue_()

        page.route(api, inject_fault)
        page.goto(base + f"/app/pocs/{POC_ID}/evidence", wait_until="networkidle")
        page.locator("#evidence-acknowledged").check()
        page.locator("#start-evidence").click()
        playwright.expect(page.locator("#start-evidence")).to_be_enabled()
        page.wait_for_load_state("networkidle")
        first = service.snapshot_payload(POC_ID)
        assert len(first["history"]) == 1
        assert first["current"]["status"] == "COMPLETED"
        page.locator("#start-evidence").click()
        playwright.expect(page.locator("#evidence-pack-link")).to_be_visible()
        current = service.snapshot_payload(POC_ID)
        assert len(bodies) == 2
        assert bodies[0] == bodies[1]
        assert len(current["history"]) == 1
        assert current["current"]["attempt_id"] == first["current"]["attempt_id"]
        playwright.expect(page.locator("#evidence-pack-link")).to_have_attribute(
            "href", current["current"]["evidence_pack_url"]
        )
        playwright.expect(page.locator("#evidence-history li")).to_have_count(1)


@pytest.mark.parametrize("fault", ["lost", "malformed", "stale"])
def test_f05_recorded_handoff_survives_unusable_refresh(tmp_path, browser_page, fault):
    page, _ = browser_page
    with _generic_server(tmp_path) as (service, base, api):
        service.start(POC_ID, acknowledgement=True, idempotency_key="handoff-fixture")
        before = service.snapshot_payload(POC_ID)
        committed = False
        receipts = []

        def record_handoff(route):
            nonlocal committed
            response = route.fetch()
            receipts.append(response.json()["closure"])
            committed = True
            route.fulfill(response=response)

        def break_refresh(route):
            if not committed:
                route.continue_()
            elif fault == "lost":
                route.abort("connectionfailed")
            else:
                payload = before if fault == "stale" else {"poc_id": "poc_wrong"}
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps(payload),
                )

        page.route(api, break_refresh)
        page.route(api + "/*/handoff", record_handoff)
        page.goto(base + f"/app/pocs/{POC_ID}/evidence", wait_until="networkidle")
        page.locator("#decision-owner").fill("audit.human.reviewer")
        page.locator("#decision-rationale").fill(
            "Reviewed the exact current synthetic evidence pack."
        )
        page.locator("#handoff-evidence").click()
        playwright.expect(page.locator("#evidence-error")).to_contain_text(
            "human decision was recorded"
        )
        authoritative = service.snapshot_payload(POC_ID)
        assert receipts == [authoritative["closure"]]
        assert authoritative["closure"]["decision"] == "HANDOFF_COMPLETED"
        assert (
            authoritative["closure"]["evidence_binding"]["run_id"]
            == before["current"]["run_id"]
        )
        playwright.expect(page.locator("#evidence-task-heading")).to_have_text(
            "Human decision recorded"
        )
        playwright.expect(page.locator("#handoff-evidence")).to_be_hidden()
        playwright.expect(page.locator("#stop-evidence")).to_be_hidden()
        playwright.expect(page.locator("#start-evidence")).to_be_hidden()
        playwright.expect(page.locator("#evidence-pack-link")).to_have_attribute(
            "href", authoritative["current"]["evidence_pack_url"]
        )


@pytest.mark.parametrize("fault", ["null-receipt", "wrong-run"])
def test_f05_does_not_record_an_unbound_handoff_response(tmp_path, browser_page, fault):
    page, _ = browser_page
    with _generic_server(tmp_path) as (service, base, api):
        service.start(
            POC_ID, acknowledgement=True, idempotency_key="untrusted-handoff-fixture"
        )

        def corrupt_receipt(route):
            response = route.fetch()
            body = response.json()
            if fault == "null-receipt":
                body["closure"] = None
            else:
                body["closure"]["evidence_binding"]["run_id"] = "run_" + "f" * 32
            route.fulfill(
                status=200, content_type="application/json", body=json.dumps(body)
            )

        page.route(api + "/*/handoff", corrupt_receipt)
        page.goto(base + f"/app/pocs/{POC_ID}/evidence", wait_until="networkidle")
        page.locator("#decision-owner").fill("audit.human.reviewer")
        page.locator("#decision-rationale").fill(
            "Only the exact bound receipt may establish the decision."
        )
        page.locator("#handoff-evidence").click()
        playwright.expect(page.locator("#evidence-error")).to_contain_text(
            "could not be trusted"
        )
        playwright.expect(page.locator("#evidence-task-heading")).not_to_have_text(
            "Human decision recorded"
        )
        assert (
            service.snapshot_payload(POC_ID)["closure"]["decision"]
            == "HANDOFF_COMPLETED"
        )
