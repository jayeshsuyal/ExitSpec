"""Mandatory v0.4 Chromium proof for the seeded Routing Evidence Pack."""

from __future__ import annotations

import os
import threading
from contextlib import contextmanager

import pytest

from exitspec.web import DemoSession, ExitSpecDemoServer

playwright_sync = pytest.importorskip("playwright.sync_api")

if os.environ.get("EXITSPEC_BROWSER_E2E") != "1":
    pytest.skip("B13 browser proof requires EXITSPEC_BROWSER_E2E=1", allow_module_level=True)


@contextmanager
def _running_server(tmp_path):
    session = DemoSession.synthetic_support_agent(tmp_path / "runs")
    server = ExitSpecDemoServer(
        ("127.0.0.1", 0),
        session,
        enable_routing_evidence_pack_demo=True,
    )
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield server, f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        worker.join(timeout=5)
        assert not worker.is_alive()
        server.server_close()


def _assert_no_horizontal_overflow(page):
    metrics = page.evaluate(
        """() => {
          const root = document.scrollingElement || document.documentElement;
          return {scrollWidth: root.scrollWidth, clientWidth: root.clientWidth};
        }"""
    )
    assert metrics["scrollWidth"] <= metrics["clientWidth"]


@pytest.mark.parametrize(
    "case",
    ["hierarchy", "responsive-focus", "tampered", "unverified"],
)
def test_b13_routing_pack_browser_gate_starts_at_app(tmp_path, case):
    with _running_server(tmp_path) as (server, base_url), playwright_sync.sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 720})
        try:
            page.goto(f"{base_url}/app")
            assert page.url == f"{base_url}/app"
            page.get_by_role("link", name="Evidence Packs").click()
            page.wait_for_url(f"{base_url}/app/evidence")
            page.locator("#evidence-pack-list").locator(".open-pack-link").first.wait_for()

            if case == "hierarchy":
                assert page.locator("#evidence-pack-list").get_by_text(
                    "Routing qualification · synthetic demo"
                ).is_visible()
                assert page.locator(".pack-verdict").inner_text() == "NOT PROVEN"
                link = page.locator(".open-pack-link").first
                assert link.get_attribute("href").endswith("/decision-packet.html")
                link.click()
                page.wait_for_url("**/decision-packet.html")
                assert page.locator("#routing-verdict").inner_text() == "NOT_PROVEN"
                assert page.locator("#routing-test-only").inner_text() == "TEST ONLY · Synthetic fixture"
                assert "Required repetition 2 is missing" in page.locator("#routing-reason").inner_text()
                assert "candidate-policy-v1" in page.locator("body").inner_text()
                assert "baseline-policy-v1" in page.locator("body").inner_text()
                assert "no deployment" in page.locator("body").inner_text().casefold()
                assert page.locator("details").count() == 3
            elif case == "responsive-focus":
                link = page.locator(".open-pack-link").first
                link.click()
                page.wait_for_url("**/decision-packet.html")
                page.set_viewport_size({"width": 390, "height": 844})
                _assert_no_horizontal_overflow(page)
                page.locator("summary").first.focus()
                assert page.evaluate("document.activeElement.tagName") == "SUMMARY"
                assert page.locator("#routing-verdict").is_visible()
                page.locator("summary").first.press("Enter")
                assert page.locator("details").first.get_attribute("open") is not None
            elif case == "tampered":
                pack_root = tmp_path / "runs" / server.routing_evidence_pack.pack_id
                (pack_root / "decision-packet.html").write_text("tampered", encoding="utf-8")
                page.reload()
                page.locator("#evidence-library-error").wait_for(state="visible")
                assert page.locator(".open-pack-link").count() == 0
                assert "No artifact link was released" in page.locator("body").inner_text()
            else:
                pack_root = tmp_path / "runs" / server.routing_evidence_pack.pack_id
                (pack_root / ".complete").unlink()
                page.reload()
                page.locator("#evidence-library-error").wait_for(state="visible")
                assert page.locator(".open-pack-link").count() == 0
                assert "No artifact link was released" in page.locator("body").inner_text()
        finally:
            browser.close()
