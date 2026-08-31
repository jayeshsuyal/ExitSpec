import json
import re
import threading
from contextlib import contextmanager

import pytest

from exitspec.poc_source_demo import SourceNeutralPOCDemoServer

playwright_sync = pytest.importorskip("playwright.sync_api")


@contextmanager
def _running_source_server():
    server = SourceNeutralPOCDemoServer(("127.0.0.1", 0))
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        worker.join(timeout=5)
        assert not worker.is_alive()
        server.server_close()


def _complete_mixed_plan(page) -> None:
    first = page.locator(".planning-row").nth(0)
    first.locator('[name="scope"]').select_option("ADVISORY")
    first.locator('[name="capability_key"]').select_option("unsupported_capability")
    first.locator('[name="reviewer"]').fill("a6.browser.a4")
    first.locator('[name="rationale"]').fill("Keep this unsupported advisory visible.")

    second = page.locator(".planning-row").nth(1)
    second.locator('[name="scope"]').select_option("MUST_HAVE")
    second.locator('[name="capability_key"]').select_option("exact_tool_selection")
    second.locator('[name="operator"]').select_option("GTE")
    second.locator('[name="threshold"]').fill("0.95")
    assert second.locator('[name="provenance"]').input_value() == "HUMAN_DECLARED"
    assert second.locator('[name="provenance"]').is_disabled()
    second.locator('[name="reviewer"]').fill("a6.browser.a4")
    second.locator('[name="rationale"]').fill("Bind the executable acceptance rule.")

    third = page.locator(".planning-row").nth(2)
    third.locator('[name="scope"]').select_option("MUST_HAVE")
    third.locator('[name="capability_key"]').select_option("exact_tool_selection")
    third.locator('[name="explicit_exclusion"]').check()
    third.locator('[name="reviewer"]').fill("a6.browser.a4")
    third.locator('[name="rationale"]').fill("Keep the excluded boundary visible.")
    page.locator("#planning-submit").click()
    page.locator("#planning-result").wait_for(state="visible")


def test_fresh_source_neutral_a5_to_a6_evidence_loopback_path():
    with _running_source_server() as base_url, playwright_sync.sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 820})
        network_urls: list[str] = []
        evidence_start_bodies: list[dict] = []
        browser_errors: list[str] = []
        failed_responses: list[tuple[int, str]] = []
        page.on("request", lambda request: network_urls.append(request.url))
        page.on(
            "request",
            lambda request: evidence_start_bodies.append(json.loads(request.post_data))
            if request.method == "POST"
            and re.search(r"/api/pocs/poc_[a-z0-9][a-z0-9_-]{2,63}/evidence$", request.url)
            and request.post_data
            else None,
        )
        page.on(
            "console",
            lambda message: browser_errors.append(message.text)
            if message.type == "error"
            else None,
        )
        page.on(
            "response",
            lambda response: failed_responses.append((response.status, response.url))
            if response.status >= 400
            else None,
        )
        page.add_init_script(
            """
            (() => {
                const nativeFetch = window.fetch.bind(window);
                window.__holdEvidenceRefresh = false;
                window.__releaseEvidenceRefresh = null;
                window.fetch = (input, init = {}) => {
                    const url = typeof input === "string" ? input : input?.url;
                    const method = (init.method || input?.method || "GET").toUpperCase();
                    const path = url ? new URL(url, window.location.origin).pathname : "";
                    if (
                        window.__holdEvidenceRefresh &&
                        method === "GET" &&
                        /\\/api\\/pocs\\/poc_[a-z0-9][a-z0-9_-]{2,63}\\/evidence$/.test(path)
                    ) {
                        return new Promise((resolve, reject) => {
                            window.__releaseEvidenceRefresh = () => {
                                window.__holdEvidenceRefresh = false;
                                nativeFetch(input, init).then(resolve, reject);
                            };
                        });
                    }
                    return nativeFetch(input, init);
                };
            })();
            """
        )
        try:
            page.goto(f"{base_url}/app/pocs/new")
            page.locator('input[name="first_source_choice"][value="DOCUMENT"]').check()
            page.locator("#display-name").fill("Fresh A6 loopback POC")
            page.locator("#customer-label").fill("A6 loopback customer")
            page.locator("#use-case").fill("Prove a frozen exact-tool criterion.")
            page.locator("#owner").fill("a6.browser.owner")
            page.locator("#create-poc").click()
            page.wait_for_url(re.compile(rf"^{re.escape(base_url)}/app/pocs/poc_[a-z0-9_-]+/sources/new$"))
            poc_match = re.search(r"/pocs/(poc_[a-z0-9_-]+)/", page.url)
            assert poc_match is not None
            poc_id = poc_match.group(1)
            assert re.fullmatch(r"poc_[a-z0-9][a-z0-9_-]{2,63}", poc_id)
            page.locator("#document-text").fill(
                "The response should be acceptable. The system must select the exact tool. "
                "Latency should be visible to the customer. Production deployment remains excluded."
            )
            page.locator("#capture-source").click()
            page.wait_for_url(re.compile(rf"/app/pocs/{poc_id}/review$"))
            page.locator("#assisted-authoring-link").click()
            page.wait_for_url(re.compile(r"/assisted-authoring$"))
            page.locator('input[name="source_receipt"]').check()
            page.locator("#authoring-submit").click()
            page.locator("#authoring-result").wait_for(state="visible")
            page.locator("#open-proposal-review").click()
            page.wait_for_url(re.compile(r"/review$"))
            page.wait_for_load_state("networkidle")
            for index in range(3):
                page.locator("#reviewer").fill("a6.browser.a3")
                page.locator("#rationale").fill("Retain this fresh source-bound claim.")
                page.locator("#keep-proposal").click()
                page.wait_for_function(
                    "(document.querySelector('#review-complete')?.hidden === false) || "
                    "(document.querySelector('#proposal-heading')?.textContent !== "
                    f"'Proposal {index + 1}')"
                )
            page.locator("#review-complete").wait_for(state="visible")
            page.locator("#plan-capabilities").click()
            page.wait_for_url(re.compile(r"/capability-plan$"))
            _complete_mixed_plan(page)
            page.locator("#open-agreement").click()
            page.wait_for_url(re.compile(r"/agreement$"))
            page.locator("#assembly-reviewer").fill("a6.browser.a5")
            page.locator("#assembly-rationale").fill("Assemble the exact current A4 plan.")
            page.locator("#prepare-agreement").click()
            page.locator("#agreement-summary").wait_for(state="visible")
            page.locator("#open-customer-review").click()
            page.wait_for_url(re.compile(r"/review/[A-Za-z0-9_-]+$"))
            page.locator("#agreement-checkbox").check()
            page.locator("#review-rationale").fill("I confirm this exact evidence agreement.")
            page.locator("#confirm-agreement").click()
            page.locator("#review-result").wait_for(state="visible")
            page.locator("#return-to-agreement").click()
            page.wait_for_url(re.compile(r"/agreement$"))
            page.locator("#freeze-agreement").click()
            page.wait_for_function("document.querySelector('#agreement-status')?.textContent === 'FROZEN'")
            assert page.locator("#freeze-agreement").is_hidden()
            assert page.locator("#open-evidence").is_visible()
            assert page.locator("#open-evidence").inner_text() == "Continue to evidence"
            assert "primary-action" in (page.locator("#open-evidence").get_attribute("class") or "")

            page.locator("#open-evidence").click()
            page.wait_for_url(re.compile(rf"/app/pocs/{poc_id}/evidence$"))
            page.locator("#evidence-acknowledged").check()
            page.locator("#start-evidence").dispatch_event("click")
            page.locator("#start-evidence").dispatch_event("click")
            page.wait_for_function(
                "document.querySelector('#evidence-current-status')?.textContent === 'COMPLETED'"
            )
            assert page.locator("#evidence-result-verdict").inner_text() == "PASS"
            assert page.locator("#evidence-pack-link").is_visible()
            assert page.locator("#evidence-acknowledgement").is_hidden()
            assert page.locator("#evidence-acknowledged").is_disabled()
            assert page.locator("#start-evidence").is_hidden()
            assert page.locator("#start-evidence").is_disabled()
            assert page.locator(".primary-action:visible").count() == 1
            assert page.locator(".primary-action:visible").get_attribute("id") == "handoff-evidence"
            assert evidence_start_bodies == [{
                "acknowledgement": True,
                "idempotency_key": evidence_start_bodies[0]["idempotency_key"],
            }]
            before_retry = page.request.get(f"{base_url}/api/pocs/{poc_id}/evidence").json()
            page.locator("#start-evidence").dispatch_event("click")
            after_retry = page.request.get(f"{base_url}/api/pocs/{poc_id}/evidence").json()
            assert after_retry["current"] == before_retry["current"]
            assert after_retry["history"] == before_retry["history"]
            assert len(evidence_start_bodies) == 1
            pack_url = page.locator("#evidence-pack-link").get_attribute("href")
            assert re.fullmatch(r"/artifacts/eatm_[a-f0-9]{32}/decision-packet\.html", pack_url or "")
            page.goto(f"{base_url}{pack_url}")
            assert "ExitSpec Evidence Pack" in page.title()
            assert "does not authorize deployment" in page.locator("body").inner_text()
            page.goto(f"{base_url}/app/pocs/{poc_id}/evidence")
            assert page.locator("#decision-owner").input_value() == ""
            assert page.locator("#decision-rationale").input_value() == ""
            assert page.locator("#stop-evidence").is_disabled()
            assert page.locator("#handoff-evidence").is_disabled()
            page.locator("#decision-owner").fill("a6.named.human")
            assert page.locator("#stop-evidence").is_enabled()
            assert page.locator("#handoff-evidence").is_disabled()
            page.locator("#decision-rationale").fill(
                "Reviewed the current pack, limitations, and next action."
            )
            assert page.locator("#handoff-evidence").is_enabled()
            page.evaluate("window.__holdEvidenceRefresh = true")
            page.locator("#handoff-evidence").click()
            page.wait_for_function("window.__releaseEvidenceRefresh !== null")
            assert page.locator("#evidence-acknowledgement").is_hidden()
            assert page.locator("#handoff-evidence").is_visible()
            assert page.locator("#handoff-evidence").is_disabled()
            page.evaluate("window.__releaseEvidenceRefresh()")
            page.wait_for_function("document.querySelector('#handoff-evidence')?.hidden === true")
            assert page.locator("#evidence-acknowledgement").is_hidden()
            assert page.locator("#evidence-acknowledged").is_disabled()
            assert page.locator("#start-evidence").is_hidden()
            assert page.locator("#start-evidence").is_disabled()
            assert page.locator("#evidence-task-kicker").inner_text() == "Decision recorded"
            assert page.locator("#evidence-task-heading").inner_text() == "Human decision recorded"
            assert page.locator(".primary-action:visible").count() == 0
            page.locator("#start-evidence").dispatch_event("click")
            snapshot = page.request.get(f"{base_url}/api/pocs/{poc_id}/evidence").json()
            assert snapshot["closure"]["decision"] == "HANDOFF_COMPLETED"
            assert len(snapshot["history"]) == 1
            assert len(evidence_start_bodies) == 1
            assert snapshot["shipping_authorized"] is False
            assert failed_responses == []
            assert browser_errors == []
            assert not any("/api/provider/" in url for url in network_urls)
        finally:
            browser.close()
