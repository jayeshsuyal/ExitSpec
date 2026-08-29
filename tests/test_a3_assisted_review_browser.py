"""Execution-enabled Chromium proof for the dynamic A3 journey."""

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


def test_dynamic_browser_a3_assisted_draft_review_named_keep_and_retained_projection():
    sync_playwright = playwright_sync.sync_playwright
    with _running_server() as base_url, sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 720})
        try:
            page.goto(f"{base_url}/app/pocs/new")
            page.locator('input[name="first_source_choice"][value="DOCUMENT"]').check()
            page.locator("#display-name").fill("Fresh A3 POC")
            page.locator("#customer-label").fill("A3 customer")
            page.locator("#use-case").fill("Validate assisted source authoring.")
            page.locator("#owner").fill("field_engineer")
            page.locator("#create-poc").click()
            page.wait_for_url(
                re.compile(
                    rf"^{re.escape(base_url)}/app/pocs/poc_[a-z0-9_-]+/sources/new$"
                )
            )
            assert "poc_support_agent_demo" not in page.url
            page.locator("#document-text").fill(
                "The error rate must remain below 1%."
            )
            page.locator("#capture-source").click()
            page.wait_for_url(
                re.compile(
                    rf"^{re.escape(base_url)}/app/pocs/poc_[a-z0-9_-]+/review$"
                )
            )
            page.wait_for_load_state("networkidle")
            assisted_link = page.locator("#assisted-authoring-link")
            assert assisted_link.is_visible()
            assisted_link.click()
            page.wait_for_url(
                re.compile(
                    rf"^{re.escape(base_url)}/app/pocs/poc_[a-z0-9_-]+/assisted-authoring$"
                )
            )
            page.wait_for_load_state("networkidle")
            assert "poc_support_agent_demo" not in page.content()
            assert page.locator("#source-receipt-list").locator("label").count() == 1
            assert page.locator("#authoring-submit").is_disabled()

            page.locator('input[name="source_receipt"]').check()
            assert page.locator("#authoring-submit").is_enabled()
            page.locator("#authoring-submit").click()
            page.locator("#authoring-result").wait_for(state="visible")
            assert "NEEDS_REVIEW" in page.locator("#authoring-result").inner_text()
            assert "source-bound proposal" in page.locator("#authoring-result").inner_text()
            page.locator("#open-proposal-review").click()
            page.wait_for_url(
                re.compile(
                    rf"^{re.escape(base_url)}/app/pocs/poc_[a-z0-9_-]+/review$"
                )
            )
            page.wait_for_load_state("networkidle")
            assert page.locator("#proposal-support").inner_text().startswith(
                "Source-bound proposal material"
            )
            assert page.locator("#keep-proposal").is_disabled()
            assert page.locator("#discard-proposal").is_disabled()
            page.locator("#reviewer").fill("named.employee")
            page.locator("#rationale").fill(
                "Keep this exact source-bound material for A4 authoring."
            )
            assert page.locator("#keep-proposal").is_enabled()
            page.locator("#keep-proposal").click()
            page.locator("#review-complete").wait_for(state="visible")
            assert "kept for contract authoring" in page.locator(
                "#review-complete-summary"
            ).inner_text()
            retained_href = page.locator("#define-criteria").get_attribute("href")
            assert retained_href is not None
            retained = page.request.get(f"{base_url}{retained_href}")
            assert retained.ok
            retained_payload = retained.json()
            assert retained_payload["retained_count"] == 1
            retained_json = str(retained_payload["retained_proposals"][0]).lower()
            assert "criterion" not in retained_json
            assert "approved" not in retained_json
            assert "verdict" not in retained_json
        finally:
            browser.close()
