"""Real navigation and current-request fault regressions for audit F11/F12."""

from __future__ import annotations

import os
import threading
from contextlib import contextmanager

import pytest

from exitspec.poc_creation import DraftPOCCreateRequest
from exitspec.poc_source_demo import SourceNeutralPOCDemoServer
from exitspec.web import DemoSession, ExitSpecDemoServer
from tests.test_a6_executable_orchestration import _pack_service

playwright_sync = pytest.importorskip("playwright.sync_api")
expect = playwright_sync.expect
pytestmark = pytest.mark.skipif(
    os.environ.get("EXITSPEC_BROWSER_E2E") != "1",
    reason="set EXITSPEC_BROWSER_E2E=1 to run navigation regressions",
)


@contextmanager
def _server(tmp_path, mode):
    if mode == "source-neutral":
        server = SourceNeutralPOCDemoServer(
            ("127.0.0.1", 0),
            evidence_artifact_root=(tmp_path / "artifacts").resolve(),
        )
    else:
        server = ExitSpecDemoServer(
            ("127.0.0.1", 0),
            DemoSession.synthetic_support_agent(output_root=tmp_path / "runs"),
        )
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield server, f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        worker.join(timeout=5)
        server.server_close()
        assert not worker.is_alive()


@pytest.fixture
def page():
    with playwright_sync.sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1280, "height": 720})
        page = context.new_page()
        try:
            yield page
        finally:
            context.close()
            browser.close()


@pytest.mark.parametrize("mode", ["source-neutral", "compatibility"])
def test_evidence_navigation_opens_real_empty_library(tmp_path, page, mode):
    with _server(tmp_path, mode) as (_, base):
        methods = []
        page.on("request", lambda request: methods.append(request.method))
        page.goto(base + "/app")
        with page.expect_response(base + "/app/evidence") as response:
            page.locator('.global-nav a[href="/app/evidence"]').click()
        assert response.value.status == 200
        expect(page.locator("#evidence-pack-list")).to_have_attribute("aria-busy", "false")
        expect(page.locator("#evidence-empty")).to_be_visible()
        expect(page.locator("#pack-count")).to_have_text("0")
        expect(page.locator("#evidence-library-error")).to_be_hidden()
        assert page.request.get(base + "/api/evidence-packs").json() == {
            "schema_version": "exitspec.evidence-pack-library.v1",
            "packs": [],
            "authorization": "Evidence is proof, not shipping authorization.",
        }
        for path in ("/app/evidence", "/api/evidence-packs"):
            assert page.request.get(base + path + "?filter=Active").status == 400
        assert methods and set(methods) == {"GET"}


def test_source_neutral_library_lists_verified_history_and_rejects_tamper(tmp_path, page):
    with _server(tmp_path, "source-neutral") as (server, base):
        server.draft_poc_service.create(
            DraftPOCCreateRequest(
                poc_id="poc_a6_executable_test",
                display_name="Synthetic navigation proof",
                customer_label="Synthetic customer",
                use_case="Verify real library navigation and publication integrity.",
                owner="synthetic.reviewer",
                first_source_choice="DOCUMENT",
            ),
            idempotency_key="navigation-draft",
        )
        service = _pack_service(tmp_path)
        server.generic_evidence_service = service
        server.evidence_artifact_root = service._output_root
        first = service.start(
            "poc_a6_executable_test", acknowledgement=True, idempotency_key="navigation-first"
        ).attempt
        current = service.start(
            "poc_a6_executable_test", acknowledgement=True, idempotency_key="navigation-current"
        ).attempt
        assert page.goto(base + "/app/evidence").status == 200
        expect(page.locator("#pack-count")).to_have_text("2")
        expect(page.locator(".open-pack-link")).to_have_count(2)
        payload = page.request.get(base + "/api/evidence-packs").json()
        by_run = {item["run_id"]: item for item in payload["packs"]}
        assert by_run[first.run_id]["handoff_state"] == "HISTORICAL"
        assert by_run[current.run_id]["handoff_state"] == "READY_FOR_HANDOFF"
        assert {item["display_name"] for item in payload["packs"]} == {"Synthetic navigation proof"}
        assert by_run[current.run_id]["evidence_pack_sha256"] == service.verify_evidence_pack(current.attempt_id)
        with page.expect_response(base + current.evidence_pack_url) as packet:
            page.locator(f'a.open-pack-link[href="{current.evidence_pack_url}"]').click()
        assert packet.value.status == 200
        expect(page).to_have_title("ExitSpec Evidence Pack")

        packet_path = service._output_root / first.attempt_id / "decision-packet.html"
        packet_path.write_bytes(b"tampered synthetic packet")
        with page.expect_response(base + "/api/evidence-packs") as rejected:
            page.goto(base + "/app/evidence")
        assert rejected.value.status == 503
        expect(page.locator("#evidence-library-error")).to_be_visible()
        expect(page.locator("#evidence-pack-list")).to_have_attribute("aria-busy", "false")
        expect(page.locator("#evidence-empty")).to_be_hidden()
        expect(page.locator(".open-pack-link")).to_have_count(0)


def test_current_dashboard_wrong_filter_fails_visibly_and_recovers(tmp_path, page):
    with _server(tmp_path, "compatibility") as (_, base):
        def mismatch(route):
            response = route.fetch()
            payload = response.json()
            payload["selected_filter"] = "Completed"
            route.fulfill(response=response, json=payload)

        page.route("**/api/workspace?filter=Active", mismatch)
        page.goto(base + "/app")
        expect(page.locator("#workspace-error")).to_be_visible()
        expect(page.locator("#workspace-error")).to_contain_text("selected filter")
        expect(page.locator("#poc-list")).to_have_attribute("aria-busy", "false")
        expect(page.locator("#continue-card")).to_have_attribute("aria-busy", "false")
        expect(page.locator("#empty-state")).to_be_hidden()
        page.locator('[data-filter="Completed"]').click()
        expect(page.locator("#poc-list")).to_have_attribute("aria-busy", "false")
        expect(page.locator("#workspace-error")).to_be_hidden()
        expect(page.locator('[data-filter="Completed"]')).to_have_attribute("aria-pressed", "true")


def test_obsolete_dashboard_wrong_filter_does_not_replace_current_view(tmp_path, page):
    with _server(tmp_path, "compatibility") as (_, base):
        held = []
        page.route("**/api/workspace?filter=Active", lambda route: held.append(route))
        page.goto(base + "/app")
        page.locator('[data-filter="Completed"]').click()
        expect(page.locator("#poc-list")).to_have_attribute("aria-busy", "false")
        expect(page.locator("#empty-title")).to_have_text("No POCs match this view.")
        assert len(held) == 1
        with page.expect_response(base + "/api/workspace?filter=Active"):
            held[0].fulfill(json={
                "selected_filter": "Archived", "pocs": [], "continue_working": None,
            })
        page.wait_for_load_state("networkidle")
        expect(page.locator("#workspace-error")).to_be_hidden()
        expect(page.locator("#poc-list")).to_have_attribute("aria-busy", "false")
        expect(page.locator("#empty-title")).to_have_text("No POCs match this view.")
        expect(page.locator('[data-filter="Completed"]')).to_have_attribute("aria-pressed", "true")
