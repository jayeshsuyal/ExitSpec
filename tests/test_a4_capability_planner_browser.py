"""Execution-enabled Chromium proof for the dynamic A4 planning journey."""

from contextlib import contextmanager
import json
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


def test_dynamic_browser_a4_plans_every_retained_claim_and_exposes_readiness():
    with _running_server() as base_url, playwright_sync.sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 720})
        failed_responses: list[str] = []
        browser_errors: list[str] = []
        page.on("console", lambda message: browser_errors.append(message.text) if message.type == "error" else None)
        page.on("response", lambda response: failed_responses.append(f"{response.status} {response.url}") if response.status >= 400 else None)
        try:
            page.goto(f"{base_url}/app/pocs/new")
            page.locator('input[name="first_source_choice"][value="DOCUMENT"]').check()
            page.locator("#display-name").fill("Fresh A4 POC")
            page.locator("#customer-label").fill("A4 customer")
            page.locator("#use-case").fill("Plan retained capability claims.")
            page.locator("#owner").fill("field_engineer")
            page.locator("#create-poc").click()
            page.wait_for_url(re.compile(rf"^{re.escape(base_url)}/app/pocs/poc_[a-z0-9_-]+/sources/new$"))
            page.locator("#document-text").fill(
                "The response should be acceptable. The system must select the exact tool. "
                "The deployment must be approved before production."
            )
            page.locator("#capture-source").click()
            page.wait_for_url(re.compile(rf"^{re.escape(base_url)}/app/pocs/poc_[a-z0-9_-]+/review$"))
            page.locator("#assisted-authoring-link").click()
            page.wait_for_url(re.compile(r"/assisted-authoring$"))
            page.locator('input[name="source_receipt"]').check()
            page.locator("#authoring-submit").click()
            page.locator("#authoring-result").wait_for(state="visible")
            page.locator("#open-proposal-review").click()
            page.wait_for_url(re.compile(r"/review$"))

            for index in range(3):
                page.locator("#reviewer").fill("named.a4.reviewer")
                page.locator("#rationale").fill("Retain this exact source-bound claim for A4 planning.")
                with page.expect_response(
                    lambda response: response.request.method == "POST"
                    and "/decision" in response.url
                ):
                    page.locator("#keep-proposal").click()
                page.wait_for_function(
                    "(document.querySelector('#review-complete')?.hidden === false) || "
                    "(document.querySelector('#proposal-heading')?.textContent !== "
                    f"'Proposal {index + 1}')"
                )
            page.locator("#review-complete").wait_for(state="visible")
            page.locator("#plan-capabilities").click()
            page.wait_for_url(re.compile(r"/capability-plan$"))
            poc_id = re.search(r"/pocs/(poc_[a-z0-9_-]+)/capability-plan$", page.url).group(1)
            first = page.locator(".planning-row").nth(0)
            assert first.locator('[name="capability_key"]').input_value() == ""
            assert first.locator('[name="threshold"]').input_value() == ""
            assert first.locator('[name="provenance"]').input_value() == ""
            first.locator('[name="scope"]').select_option("ADVISORY")
            first.locator('[name="capability_key"]').select_option("unsupported_capability")
            first.locator('[name="reviewer"]').fill("named.a4.reviewer")
            first.locator('[name="rationale"]').fill("Keep the unrelated boundary visible.")
            second = page.locator(".planning-row").nth(1)
            second.locator('[name="scope"]').select_option("MUST_HAVE")
            second.locator('[name="capability_key"]').select_option("exact_tool_selection")
            assert second.locator('[name="operator"]').input_value() == ""
            second.locator('[name="operator"]').select_option("GTE")
            second.locator('[name="threshold"]').fill("0.95")
            assert second.locator('[name="provenance"]').input_value() == "SOURCE_EXTRACTED"
            assert second.locator('[name="provenance"]').is_disabled()
            second.locator('[name="reviewer"]').fill("named.a4.reviewer")
            second.locator('[name="rationale"]').fill("Complete executable policy.")
            third = page.locator(".planning-row").nth(2)
            third.locator('[name="scope"]').select_option("MUST_HAVE")
            third.locator('[name="capability_key"]').select_option("exact_tool_selection")
            assert third.locator('[name="operator"]').input_value() == ""
            third.locator('[name="explicit_exclusion"]').check()
            assert third.locator('[name="rule"]').input_value() == ""
            assert third.locator('[name="rule"]').is_disabled()
            third.locator('[name="explicit_exclusion"]').uncheck()
            assert third.locator('[name="rule"]').input_value() == "exact_tool_selection_rate"
            assert not third.locator('[name="rule"]').is_disabled()
            third.locator('[name="operator"]').select_option("GTE")
            third.locator('[name="threshold"]').fill("")
            assert third.locator('[name="provenance"]').input_value() == "SOURCE_EXTRACTED"
            assert third.locator('[name="provenance"]').is_disabled()
            third.locator('[name="reviewer"]').fill("named.a4.reviewer")
            third.locator('[name="rationale"]').fill("Clarify the missing threshold before agreement.")
            page.locator("#planning-submit").click()
            page.locator("#planning-result").wait_for(state="visible")
            summary = page.locator("#planning-result-records").inner_text()
            assert "EXECUTABLE" in summary
            assert "CLARIFICATION_REQUIRED" in summary
            assert "UNSUPPORTED" in summary
            assert page.locator("#planning-result-records .result-record").nth(0).get_attribute("data-disposition") == "UNSUPPORTED"
            assert page.locator("#ready-for-agreement").inner_text() == "NOT READY"
            assert page.locator("#planning-result-records .result-record").count() == 3
            assert failed_responses == []
            assert browser_errors == []

            retained_response = page.request.get(f"{base_url}/api/pocs/{poc_id}/retained-proposals")
            malformed_payload = retained_response.json()
            malformed_payload["retained_proposals"].append({"proposal_id": "prop_malformed"})
            page.route(
                f"{base_url}/api/pocs/{poc_id}/retained-proposals",
                lambda route: route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps(malformed_payload),
                ),
            )
            page.goto(page.url)
            page.locator("#capability-plan-error").wait_for(state="visible")
            assert page.locator(".planning-row").count() == 0
        finally:
            browser.close()
