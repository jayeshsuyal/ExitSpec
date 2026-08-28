"""Targeted browser journey for the source-neutral A2 runtime."""

from __future__ import annotations

from contextlib import contextmanager
import re
import threading

import pytest

from exitspec.poc_source_demo import SourceNeutralPOCDemoServer


playwright_sync = pytest.importorskip("playwright.sync_api")


@contextmanager
def _running_server():
    server = SourceNeutralPOCDemoServer(("127.0.0.1", 0))
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        worker.join(timeout=5)
        server.server_close()


def test_dynamic_browser_create_attach_list_proposal_journey_without_seeded_fallback():
    sync_playwright = playwright_sync.sync_playwright
    with _running_server() as base_url, sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 720})
        try:
            page.goto(f"{base_url}/app/pocs/new")
            page.locator('input[name="first_source_choice"][value="EMAIL"]').check()
            page.locator("#display-name").fill("Dynamic source POC")
            page.locator("#customer-label").fill("Northstar")
            page.locator("#use-case").fill("Validate a source-neutral request spine.")
            page.locator("#owner").fill("field_engineer")
            page.locator("#create-poc").click()
            page.wait_for_url(
                re.compile(rf"^{re.escape(base_url)}/app/pocs/poc_[a-z0-9_-]+/sources/new$")
            )
            assert "/poc_support_agent_demo" not in page.url

            page.locator("#email-text").fill(
                "The p95 latency must stay below 500 ms. Contact owner@example.com."
            )
            page.locator("#capture-source").click()
            page.wait_for_url(
                re.compile(rf"^{re.escape(base_url)}/app/pocs/poc_[a-z0-9_-]+/review$")
            )
            page.wait_for_load_state("networkidle")
            assert page.locator("#proposal-heading").inner_text() == "Proposal 1"
            assert page.locator("#source-kind").inner_text().casefold() == "email"
            assert page.locator("#proposal-support").inner_text().startswith(
                "Not executable in this demo"
            )
            assert "poc_support_agent_demo" not in page.content()
        finally:
            browser.close()
