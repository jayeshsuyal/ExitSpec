"""Targeted browser journey for the source-neutral A2 runtime."""

from __future__ import annotations

from contextlib import contextmanager
import json
import re
import threading

import pytest

from exitspec.poc_source_demo import SourceNeutralPOCDemoServer
from tests.test_a2_source_spine import CONTRACT


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


@pytest.mark.parametrize(
    ("source_kind", "input_selector", "value"),
    (
        ("EMAIL", "#email-text", "The p95 latency must stay below 500 ms."),
        ("MEETING", "#meeting-transcript", "Customer: The error rate must remain below 1%."),
        ("DOCUMENT", "#document-text", "The throughput must exceed 100 requests per second."),
        ("EXISTING_CONTRACT", "#contract-json", json.dumps(CONTRACT)),
    ),
)
def test_dynamic_browser_create_attach_list_proposal_journey_without_seeded_fallback(
    source_kind, input_selector, value
):
    sync_playwright = playwright_sync.sync_playwright
    with _running_server() as base_url, sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 720})
        try:
            page.goto(f"{base_url}/app/pocs/new")
            page.locator(
                f'input[name="first_source_choice"][value="{source_kind}"]'
            ).check()
            page.locator("#display-name").fill("Dynamic source POC")
            page.locator("#customer-label").fill("Northstar")
            page.locator("#use-case").fill("Validate a source-neutral request spine.")
            page.locator("#owner").fill("field_engineer")
            page.locator("#create-poc").click()
            page.wait_for_url(
                re.compile(rf"^{re.escape(base_url)}/app/pocs/poc_[a-z0-9_-]+/sources/new$")
            )
            assert "/poc_support_agent_demo" not in page.url

            if source_kind == "MEETING":
                page.locator('input[name="meeting_mode"][value="PASTE"]').check()
            page.locator(input_selector).fill(value)
            page.locator("#capture-source").click()
            page.wait_for_url(
                re.compile(rf"^{re.escape(base_url)}/app/pocs/poc_[a-z0-9_-]+/review$")
            )
            page.wait_for_load_state("networkidle")
            assert page.locator("#proposal-heading").inner_text() == "Proposal 1"
            assert page.locator("#source-kind").inner_text().casefold() == {
                "EMAIL": "email",
                "MEETING": "meeting transcript",
                "DOCUMENT": "notes or document",
                "EXISTING_CONTRACT": "existing contract",
            }[source_kind]
            proposal_support = page.locator("#proposal-support").inner_text()
            assert proposal_support.startswith(
                ("Executable candidate", "Not executable in this demo")
            )
            assert "poc_support_agent_demo" not in page.content()
        finally:
            browser.close()
