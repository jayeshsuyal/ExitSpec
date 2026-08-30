"""Execution-enabled browser proof for the source-neutral A5 journey."""

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


def _complete_a4_plan(
    page, *, threshold: str, supported_capability: str = "exact_tool_selection"
):
    first = page.locator(".planning-row").nth(0)
    first.locator('[name="scope"]').select_option("ADVISORY")
    first.locator('[name="capability_key"]').select_option("unsupported_capability")
    first.locator('[name="reviewer"]').fill("named.a4.reviewer")
    first.locator('[name="rationale"]').fill("Keep this unrelated boundary visible.")
    second = page.locator(".planning-row").nth(1)
    second.locator('[name="scope"]').select_option("MUST_HAVE")
    second.locator('[name="capability_key"]').select_option(supported_capability)
    second.locator('[name="operator"]').select_option(
        "LT" if supported_capability == "inference_performance_external" else "GTE"
    )
    second.locator('[name="threshold"]').fill(threshold)
    second.locator('[name="provenance"]').select_option("SOURCE_EXTRACTED")
    second.locator('[name="reviewer"]').fill("named.a4.reviewer")
    second.locator('[name="rationale"]').fill("Complete executable policy.")
    third = page.locator(".planning-row").nth(2)
    third.locator('[name="scope"]').select_option("MUST_HAVE")
    third.locator('[name="capability_key"]').select_option("exact_tool_selection")
    third.locator('[name="explicit_exclusion"]').check()
    third.locator('[name="reviewer"]').fill("named.a4.reviewer")
    third.locator('[name="rationale"]').fill("Exclude production deployment from this agreement.")
    page.locator("#planning-submit").click()
    page.locator("#planning-result").wait_for(state="visible")


def test_fresh_dynamic_a5_agreement_review_revision_and_freeze_journey():
    with _running_server() as base_url, playwright_sync.sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 820})
        network_urls: list[str] = []
        browser_errors: list[str] = []
        failed_responses: list[tuple[int, str]] = []
        page.on("request", lambda request: network_urls.append(request.url))
        page.on("console", lambda message: browser_errors.append(message.text) if message.type == "error" else None)
        page.on(
            "response",
            lambda response: failed_responses.append((response.status, response.url))
            if response.status >= 400
            else None,
        )
        try:
            page.goto(f"{base_url}/app/pocs/new")
            page.locator('input[name="first_source_choice"][value="DOCUMENT"]').check()
            page.locator("#display-name").fill("Fresh dynamic agreement")
            page.locator("#customer-label").fill("Generated customer")
            page.locator("#use-case").fill("Bind a current capability plan.")
            page.locator("#owner").fill("field_engineer")
            page.locator("#create-poc").click()
            page.wait_for_url(re.compile(rf"^{re.escape(base_url)}/app/pocs/poc_[a-z0-9_-]+/sources/new$"))
            poc_id = re.search(r"/pocs/(poc_[a-z0-9_-]+)/", page.url).group(1)
            page.locator("#document-text").fill(
                "The response should be acceptable. The system must select the exact tool. "
                "Latency should be visible to the customer. Production deployment is excluded "
                "from this agreement."
            )
            page.locator("#capture-source").click()
            page.wait_for_url(re.compile(rf"^{re.escape(base_url)}/app/pocs/{poc_id}/review$"))
            page.locator("#assisted-authoring-link").click()
            page.wait_for_url(re.compile(r"/assisted-authoring$"))
            page.locator('input[name="source_receipt"]').check()
            page.locator("#authoring-submit").click()
            page.locator("#authoring-result").wait_for(state="visible")
            page.locator("#open-proposal-review").click()
            page.wait_for_url(re.compile(r"/review$"))
            page.wait_for_load_state("networkidle")
            assert not page.locator("#proposal-review-error").is_visible(), (
                page.locator("#proposal-review-error").inner_text()
            )
            for index in range(3):
                page.locator("#reviewer").fill("named.a3.reviewer")
                page.locator("#rationale").fill("Retain this source-bound claim for A4 planning.")
                page.locator("#keep-proposal").click()
                page.wait_for_function(
                    "(document.querySelector('#review-complete')?.hidden === false) || "
                    "(document.querySelector('#proposal-heading')?.textContent !== "
                    f"'Proposal {index + 1}')"
                )
            page.locator("#review-complete").wait_for(state="visible")
            page.locator("#plan-capabilities").click()
            page.wait_for_url(re.compile(r"/capability-plan$"))
            _complete_a4_plan(page, threshold="0.95")
            assert page.locator("#ready-for-agreement").inner_text() == "READY FOR NEXT REVIEW"
            page.locator("#open-agreement").click()
            page.wait_for_url(re.compile(r"/agreement$"))

            page.locator("#assembly-reviewer").fill("named.a5.reviewer")
            page.locator("#assembly-rationale").fill("Assemble the exact current reviewed plan.")
            page.locator("#prepare-agreement").click()
            page.locator("#agreement-summary").wait_for(state="visible")
            assert page.locator("#agreement-version").inner_text().casefold() == "version 1"
            assert page.locator(".proof-summary h3").inner_text() == "How this will be proven"
            assert "200 approved support-tool cases" in page.locator("#agreement-proof").inner_text()
            old_review_url = page.locator("#open-customer-review").get_attribute("href")
            page.locator("#open-customer-review").click()
            page.wait_for_url(re.compile(r"/review/[A-Za-z0-9_-]+$"))
            old_token_url = page.url
            assert page.locator(".review-proof h3").inner_text() == "How this will be proven"
            assert "200 approved support-tool cases" in page.locator("#review-proof").inner_text()
            page.locator("#agreement-checkbox").check()
            page.locator("#review-rationale").fill("Please change the measurable threshold.")
            page.locator("#request-changes").click()
            page.locator("#review-result").wait_for(state="visible")
            page.locator("#return-to-agreement").click()
            page.wait_for_url(re.compile(r"/agreement$"))
            page.locator("#revise-plan").click()
            page.wait_for_url(re.compile(r"/capability-plan$"))
            _complete_a4_plan(page, threshold="0.90")
            page.locator("#open-agreement").click()
            page.wait_for_url(re.compile(r"/agreement$"))
            page.locator("#revision-reviewer").fill("named.a5.successor")
            page.locator("#revision-rationale").fill("Approve the changed successor agreement.")
            page.locator("#start-revision").click()
            revision_state = page.wait_for_function(
                """() => {
                    const error = document.querySelector('#agreement-error');
                    if (error && !error.hidden) {
                        return `error: ${error.textContent || 'unknown agreement error'}`;
                    }
                    const version = document.querySelector('#agreement-version');
                    return version?.textContent?.trim() === 'Version 2'
                        ? 'version-2'
                        : false;
                }"""
            )
            assert revision_state.json_value() == "version-2", revision_state
            assert page.locator("#agreement-version").inner_text().casefold() == "version 2"
            assert page.locator("#agreement-status").inner_text() == "PENDING"
            new_review_url = page.locator("#open-customer-review").get_attribute("href")
            assert old_review_url != new_review_url
            assert new_review_url
            page.goto(f"{base_url}{old_review_url}")
            page.locator("#customer-review-error").wait_for(state="visible")
            assert failed_responses == [(404, f"{base_url}/api/review/{old_token_url.rsplit('/', 1)[-1]}")]
            page.goto(f"{base_url}{new_review_url}")
            page.locator("#agreement-checkbox").check()
            page.locator("#review-rationale").fill("I confirm the exact changed agreement.")
            page.locator("#confirm-agreement").click()
            page.locator("#review-result").wait_for(state="visible")
            page.locator("#return-to-agreement").click()
            page.wait_for_url(re.compile(r"/agreement$"))
            page.locator("#freeze-agreement").click()
            page.wait_for_function("document.querySelector('#agreement-status')?.textContent === 'FROZEN'")
            frozen = page.request.get(f"{base_url}/api/pocs/{poc_id}/agreement").json()
            assert frozen["frozen_contract"]["status"] == "FROZEN"
            assert frozen["frozen_contract"]["version"] == "2"
            assert re.fullmatch(r"[a-f0-9]{64}", frozen["frozen_contract"]["canonical_hash"])
            assert old_token_url != page.url
            assert browser_errors in ([], ["Failed to load resource: the server responded with a status of 404 (Not Found)"])
            assert failed_responses == [(404, f"{base_url}/api/review/{old_token_url.rsplit('/', 1)[-1]}")]
            assert not any(re.search(r"/(run|import|evidence|verdict)(?:/|$)", url) for url in network_urls)
        finally:
            browser.close()


def test_managed_ttft_proof_projection_shows_attempt_and_success_requirements():
    with _running_server() as base_url, playwright_sync.sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 820})
        network_urls: list[str] = []
        page.on("request", lambda request: network_urls.append(request.url))
        try:
            page.goto(f"{base_url}/app/pocs/new")
            page.locator('input[name="first_source_choice"][value="DOCUMENT"]').check()
            page.locator("#display-name").fill("Managed TTFT agreement")
            page.locator("#customer-label").fill("Managed customer")
            page.locator("#use-case").fill("Bind managed TTFT evidence requirements.")
            page.locator("#owner").fill("field_engineer")
            page.locator("#create-poc").click()
            page.wait_for_url(re.compile(rf"^{re.escape(base_url)}/app/pocs/poc_[a-z0-9_-]+/sources/new$"))
            poc_id = re.search(r"/pocs/(poc_[a-z0-9_-]+)/", page.url).group(1)
            page.locator("#document-text").fill(
                "The response should be acceptable. The system must select the exact tool. "
                "Latency should be visible to the customer. Production deployment is excluded "
                "from this agreement."
            )
            page.locator("#capture-source").click()
            page.wait_for_url(re.compile(rf"^{re.escape(base_url)}/app/pocs/{poc_id}/review$"))
            page.locator("#assisted-authoring-link").click()
            page.wait_for_url(re.compile(r"/assisted-authoring$"))
            page.locator('input[name="source_receipt"]').check()
            page.locator("#authoring-submit").click()
            page.locator("#authoring-result").wait_for(state="visible")
            page.locator("#open-proposal-review").click()
            page.wait_for_url(re.compile(r"/review$"))
            page.wait_for_load_state("networkidle")
            for index in range(3):
                page.locator("#reviewer").fill("named.a3.reviewer")
                page.locator("#rationale").fill("Retain this source-bound claim for A4 planning.")
                page.locator("#keep-proposal").click()
                page.wait_for_function(
                    "(document.querySelector('#review-complete')?.hidden === false) || "
                    "(document.querySelector('#proposal-heading')?.textContent !== "
                    f"'Proposal {index + 1}')"
                )
            page.locator("#review-complete").wait_for(state="visible")
            page.locator("#plan-capabilities").click()
            page.wait_for_url(re.compile(r"/capability-plan$"))
            _complete_a4_plan(
                page,
                threshold="250",
                supported_capability="inference_performance_external",
            )
            assert page.locator("#ready-for-agreement").inner_text() == "READY FOR NEXT REVIEW"
            page.locator("#open-agreement").click()
            page.wait_for_url(re.compile(r"/agreement$"))
            page.locator("#assembly-reviewer").fill("named.a5.reviewer")
            page.locator("#assembly-rationale").fill("Assemble the managed TTFT requirements.")
            page.locator("#prepare-agreement").click()
            page.locator("#agreement-summary").wait_for(state="visible")
            assert "100 attempts" in page.locator("#agreement-proof").inner_text()
            assert "100 required successful TTFT samples" in page.locator("#agreement-proof").inner_text()
            page.locator("#open-customer-review").click()
            page.wait_for_url(re.compile(r"/review/[A-Za-z0-9_-]+$"))
            assert "100 attempts" in page.locator("#review-proof").inner_text()
            assert "100 required successful TTFT samples" in page.locator("#review-proof").inner_text()
            assert not any(re.search(r"/(run|import|evidence|verdict)(?:/|$)", url) for url in network_urls)
        finally:
            browser.close()
