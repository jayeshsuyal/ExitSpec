"""Execution-enabled Chromium proof for the dynamic A3 journey."""

from __future__ import annotations

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


def test_dynamic_browser_a3_assisted_draft_review_named_keep_and_retained_projection():
    sync_playwright = playwright_sync.sync_playwright
    with _running_server() as base_url, sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 720})
        browser_errors: list[str] = []
        failed_responses: list[str] = []
        page.on("console", lambda message: browser_errors.append(message.text) if message.type == "error" else None)
        page.on(
            "response",
            lambda response: failed_responses.append(
                f"{response.status} {response.url}"
            )
            if response.status >= 400
            else None,
        )
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
            assert page.locator(".object-heading .eyebrow").inner_text().casefold() == (
                "review · human triage"
            )
            state = page.request.get(f"{base_url}/api/state")
            assert state.ok
            assert state.json()["mode"] == "local_source_neutral"
            assisted_link = page.locator("#assisted-authoring-link")
            assert assisted_link.is_visible()
            assisted_link.click()
            page.wait_for_url(
                re.compile(
                    rf"^{re.escape(base_url)}/app/pocs/poc_[a-z0-9_-]+/assisted-authoring$"
                )
            )
            page.wait_for_load_state("networkidle")
            assert page.locator(".object-heading .eyebrow").inner_text().casefold() == (
                "review · schema-bound draft"
            )
            assert "poc_support_agent_demo" not in page.content()
            assert page.locator("#source-receipt-list").locator("label").count() == 1
            assert page.locator("#authoring-submit").is_disabled()

            page.locator('input[name="source_receipt"]').check()
            assert page.locator("#authoring-submit").is_enabled()
            page.locator("#authoring-submit").click()
            page.locator("#authoring-result").wait_for(state="visible")
            assert "NEEDS_REVIEW" in page.locator("#authoring-result").inner_text()
            assert "source-bound proposal" in page.locator("#authoring-result").inner_text()
            poc_id = re.search(r"/pocs/(poc_[a-z0-9_-]+)/", page.url).group(1)
            retained_before_review = page.request.get(
                f"{base_url}/api/pocs/{poc_id}/retained-proposals"
            )
            assert retained_before_review.ok
            assert retained_before_review.json()["retained_count"] == 0
            page.locator("#open-proposal-review").click()
            page.wait_for_url(
                re.compile(
                    rf"^{re.escape(base_url)}/app/pocs/poc_[a-z0-9_-]+/review$"
                )
            )
            page.wait_for_load_state("networkidle")
            assert page.locator(".object-heading .eyebrow").inner_text().casefold() == (
                "review · human triage"
            )
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
            assert page.locator("#review-complete-summary").inner_text().startswith(
                "1 proposals reviewed: 1 retained for acceptance drafting"
            )
            visible_destination = page.locator("#define-criteria").get_attribute("href")
            assert visible_destination == "/app"
            assert "/retained-proposals" not in visible_destination
            retained = page.request.get(
                f"{base_url}/api/pocs/{poc_id}/retained-proposals"
            )
            assert retained.ok
            retained_payload = retained.json()
            assert retained_payload["retained_count"] == 1
            retained_json = str(retained_payload["retained_proposals"][0]).lower()
            assert "criterion" not in retained_json
            assert "approved" not in retained_json
            assert "verdict" not in retained_json
            assert failed_responses == []
            assert browser_errors == []
        finally:
            browser.close()


def test_dynamic_browser_mixed_a2_a3_review_keeps_decision_across_reload():
    sync_playwright = playwright_sync.sync_playwright
    with _running_server() as base_url, sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 720})
        browser_errors: list[str] = []
        failed_responses: list[str] = []
        page.on(
            "console",
            lambda message: browser_errors.append(message.text)
            if message.type == "error"
            else None,
        )
        page.on(
            "response",
            lambda response: failed_responses.append(
                f"{response.status} {response.url}"
            )
            if response.status >= 400
            else None,
        )
        try:
            page.goto(f"{base_url}/app/pocs/new")
            page.locator('input[name="first_source_choice"][value="DOCUMENT"]').check()
            page.locator("#display-name").fill("Mixed A2 A3 POC")
            page.locator("#customer-label").fill("Mixed customer")
            page.locator("#use-case").fill("Validate mixed source review.")
            page.locator("#owner").fill("field_engineer")
            page.locator("#create-poc").click()
            page.wait_for_url(re.compile(rf"^{re.escape(base_url)}/app/pocs/poc_[a-z0-9_-]+/sources/new$"))
            page.locator("#document-text").fill("The error rate must remain below 1%.")
            page.locator("#capture-source").click()
            page.wait_for_url(re.compile(rf"^{re.escape(base_url)}/app/pocs/poc_[a-z0-9_-]+/review$"))
            poc_id = re.search(r"/pocs/(poc_[a-z0-9_-]+)/", page.url).group(1)

            page.locator("#assisted-authoring-link").click()
            page.wait_for_url(re.compile(rf"^{re.escape(base_url)}/app/pocs/{poc_id}/assisted-authoring$"))
            page.locator('input[name="source_receipt"]').check()
            page.locator("#authoring-submit").click()
            page.locator("#authoring-result").wait_for(state="visible")
            source_receipt_id = page.locator('input[name="source_receipt"]').input_value()

            capture = page.request.post(
                f"{base_url}/api/pocs/{poc_id}/sources/email-text",
                data=json.dumps(
                    {
                        "email_text": "The error rate must remain below 1%.",
                        "idempotency_key": "mixed-email-source",
                    }
                ),
                headers={
                    "Content-Type": "application/json",
                    "Origin": base_url,
                },
            )
            assert capture.status == 201
            email_receipt_id = capture.json()["source_receipt_id"]
            assert email_receipt_id != source_receipt_id

            page.locator("#open-proposal-review").click()
            page.wait_for_url(re.compile(rf"^{re.escape(base_url)}/app/pocs/{poc_id}/review$"))
            page.wait_for_load_state("networkidle")
            assert page.locator("#source-kind").inner_text().casefold() == "notes or document"
            assert page.locator("#proposal-support").inner_text().startswith(
                "Source-bound proposal material"
            )
            page.locator("#reviewer").fill("named.employee")
            page.locator("#rationale").fill("Keep this exact A3 material for A4 drafting.")
            page.locator("#keep-proposal").click()
            page.wait_for_function(
                "document.querySelector('#source-kind')?.textContent?.toLowerCase() === 'email'"
            )
            assert page.locator("#source-kind").inner_text().casefold() == "email"
            assert page.locator("#proposal-support").inner_text().startswith(
                "Executable candidate"
            )

            receipts = page.request.get(f"{base_url}/api/pocs/{poc_id}/assisted-authoring")
            projection = page.request.get(
                f"{base_url}/api/pocs/{poc_id}/assisted-authoring/current-review"
            )
            assert receipts.ok and projection.ok
            receipt_payload = receipts.json()
            projection_payload = projection.json()
            assert receipt_payload["receipts"][0]["source_receipt_id"] == source_receipt_id
            assert projection_payload["proposals"][0]["source_receipt_id"] == source_receipt_id
            assert projection_payload["proposals"][0]["review_state"] == "KEEP_FOR_CONTRACT"

            page.reload()
            page.wait_for_load_state("networkidle")
            assert page.locator("#source-kind").inner_text().casefold() == "email"
            assert not page.locator("#proposal-support").inner_text().startswith(
                "Source-bound proposal material"
            )
            assert page.locator("#assisted-authoring-link").is_visible()
            assert failed_responses == []
            assert browser_errors == []
        finally:
            browser.close()


@pytest.mark.parametrize(
    "capability_response",
    (
        {"mode": "local_source_neutral", "safety": {}},
        {"status": 503, "body": {"error": "capability unavailable"}},
    ),
)
def test_malformed_or_unavailable_capability_state_hides_a3_without_dead_ui(
    capability_response,
):
    sync_playwright = playwright_sync.sync_playwright
    with _running_server() as base_url, sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 720})
        assisted_api_requests: list[str] = []
        page.on(
            "request",
            lambda request: assisted_api_requests.append(request.url)
            if "/assisted-authoring" in request.url
            else None,
        )
        try:
            def fulfill_capability(route):
                if "status" in capability_response:
                    route.fulfill(
                        status=capability_response["status"],
                        content_type="application/json",
                        body=json.dumps(capability_response["body"]),
                    )
                else:
                    route.fulfill(
                        status=200,
                        content_type="application/json",
                        body=json.dumps(capability_response),
                    )

            page.route("**/api/state", fulfill_capability)
            page.goto(f"{base_url}/app/pocs/new")
            page.locator('input[name="first_source_choice"][value="DOCUMENT"]').check()
            page.locator("#display-name").fill("Capability fallback POC")
            page.locator("#customer-label").fill("A3 customer")
            page.locator("#use-case").fill("Validate capability fail closed.")
            page.locator("#owner").fill("field_engineer")
            page.locator("#create-poc").click()
            page.wait_for_url(
                re.compile(
                    rf"^{re.escape(base_url)}/app/pocs/poc_[a-z0-9_-]+/sources/new$"
                )
            )
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
            assert page.locator("#proposal-current-task").get_attribute(
                "aria-busy"
            ) == "false"
            assert page.locator("#proposal-review-error").is_visible()
            assert page.locator("#proposal-support").inner_text() == (
                "Checking current evaluator support…"
            )
            assert page.locator("#assisted-authoring-link").is_hidden()
            assert assisted_api_requests == []
        finally:
            browser.close()
