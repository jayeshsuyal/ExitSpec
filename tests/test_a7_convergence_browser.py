"""Mandatory Chromium proof for the v0.3 source-neutral convergence path."""

from __future__ import annotations

import json
import re
import threading
from contextlib import contextmanager

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
        assert not worker.is_alive()
        server.server_close()


SOURCE_CASES = (
    pytest.param(
        "EMAIL",
        "#email-text",
        "The response should be acceptable. The system must select the exact tool. "
        "Latency should be visible to the customer. Production deployment remains excluded.",
        "EMAIL",
        id="email",
    ),
    pytest.param(
        "DOCUMENT",
        "#document-text",
        "The response should be acceptable. The system must select the exact tool. "
        "Latency should be visible to the customer. Production deployment remains excluded.",
        "DOCUMENT",
        id="notes-document",
    ),
    pytest.param(
        "MEETING",
        "#meeting-transcript",
        "Customer: The response should be acceptable.\n"
        "Customer: The system must select the exact tool.\n"
        "Customer: Latency should be visible. Production deployment remains excluded.",
        "MEETING",
        id="meeting-text",
    ),
)


def _assert_no_page_overflow(page, *, bounded_height: bool) -> None:
    metrics = page.evaluate(
        """() => {
          const root = document.scrollingElement || document.documentElement;
          return {
            innerWidth: window.innerWidth,
            innerHeight: window.innerHeight,
            clientWidth: root.clientWidth,
            clientHeight: root.clientHeight,
            scrollWidth: root.scrollWidth,
            scrollHeight: root.scrollHeight,
          };
        }"""
    )
    assert metrics["scrollWidth"] <= metrics["clientWidth"]
    if bounded_height:
        assert metrics["scrollHeight"] <= metrics["clientHeight"]


def _complete_plan(page) -> None:
    assert page.locator(".planning-row").count() == 3
    first = page.locator(".planning-row").nth(0)
    first.locator('[name="scope"]').select_option("ADVISORY")
    first.locator('[name="capability_key"]').select_option("unsupported_capability")
    first.locator('[name="reviewer"]').fill("a7.employee.reviewer")
    first.locator('[name="rationale"]').fill("Keep the unsupported advisory visible.")

    second = page.locator(".planning-row").nth(1)
    second.locator('[name="scope"]').select_option("MUST_HAVE")
    second.locator('[name="capability_key"]').select_option("exact_tool_selection")
    second.locator('[name="operator"]').select_option("GTE")
    second.locator('[name="threshold"]').fill("0.95")
    assert second.locator('[name="provenance"]').input_value() == "HUMAN_DECLARED"
    assert second.locator('[name="provenance"]').is_disabled()
    second.locator('[name="reviewer"]').fill("a7.employee.reviewer")
    second.locator('[name="rationale"]').fill("Bind the server-selected executable method.")

    third = page.locator(".planning-row").nth(2)
    third.locator('[name="scope"]').select_option("MUST_HAVE")
    third.locator('[name="capability_key"]').select_option("exact_tool_selection")
    third.locator('[name="explicit_exclusion"]').check()
    third.locator('[name="reviewer"]').fill("a7.employee.reviewer")
    third.locator('[name="rationale"]').fill("Keep production deployment excluded.")
    page.locator("#planning-submit").click()
    page.locator("#planning-result").wait_for(state="visible")


@pytest.mark.parametrize(
    ("source_choice", "input_selector", "source_text", "expected_kind"),
    SOURCE_CASES,
)
def test_fresh_supported_source_completes_canonical_request_to_proof_spine(
    source_choice,
    input_selector,
    source_text,
    expected_kind,
):
    with _running_server() as base_url, playwright_sync.sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 720})
        browser_errors: list[str] = []
        unexpected_http: list[tuple[int, str]] = []
        evidence_requests: list[dict] = []
        page.on(
            "console",
            lambda message: browser_errors.append(message.text)
            if message.type == "error"
            else None,
        )
        page.on("pageerror", lambda error: browser_errors.append(str(error)))
        page.on(
            "response",
            lambda response: unexpected_http.append((response.status, response.url))
            if response.status >= 400
            else None,
        )
        page.on(
            "request",
            lambda request: evidence_requests.append(json.loads(request.post_data))
            if request.method == "POST"
            and re.search(r"/api/pocs/poc_[a-z0-9_-]+/evidence$", request.url)
            and request.post_data
            else None,
        )
        try:
            page.goto(f"{base_url}/app")
            assert page.url == f"{base_url}/app"
            assert "Capture → Review → Plan → Confirm → Prove → Decide" in page.locator("body").inner_text()
            assert page.locator("#source-heading").inner_text() == "How are the requirements arriving?"
            assert page.locator('input[name="first_source_choice"]').evaluate_all(
                "nodes => nodes.map(node => node.value)"
            ) == ["EMAIL", "MEETING", "DOCUMENT"]
            _assert_no_page_overflow(page, bounded_height=True)

            page.locator(
                f'input[name="first_source_choice"][value="{source_choice}"]'
            ).check()
            page.locator("#display-name").fill(f"Fresh {source_choice.casefold()} convergence")
            page.locator("#customer-label").fill("A7 customer")
            page.locator("#use-case").fill("Complete the exact request-to-proof spine.")
            page.locator("#owner").fill("a7.employee.owner")
            page.locator("#create-poc").click()
            page.wait_for_url(
                re.compile(
                    rf"^{re.escape(base_url)}/app/pocs/(poc_[a-z0-9_-]+)/capture$"
                )
            )
            poc_id = re.search(r"/pocs/(poc_[a-z0-9_-]+)/", page.url).group(1)
            assert poc_id not in {"poc_support_agent_demo", "poc_inference_latency_demo"}

            page.locator(input_selector).fill(source_text)
            page.locator("#capture-source").click()
            page.wait_for_url(f"{base_url}/app/pocs/{poc_id}/review")
            source_snapshot = page.request.get(f"{base_url}/api/pocs/{poc_id}/sources").json()
            assert len(source_snapshot["sources"]) == 1
            assert source_snapshot["sources"][0]["source_kind"] == expected_kind
            assert source_snapshot["sources"][0]["status"] == "NEEDS_REVIEW"
            assert re.fullmatch(r"srcpt_[a-z0-9][a-z0-9_-]{7,95}", source_snapshot["sources"][0]["source_receipt_id"])

            page.locator("#assisted-authoring-link").click()
            page.wait_for_url(f"{base_url}/app/pocs/{poc_id}/assisted-authoring")
            page.locator('input[name="source_receipt"]').check()
            page.locator("#authoring-submit").click()
            page.locator("#authoring-result").wait_for(state="visible")
            page.locator("#open-proposal-review").click()
            page.wait_for_url(f"{base_url}/app/pocs/{poc_id}/review")

            proposal_payload = page.request.get(f"{base_url}/api/pocs/{poc_id}/proposals").json()
            proposal_count = len(proposal_payload["proposals"])
            assert proposal_count == 3
            for position in range(proposal_count):
                page.locator("#reviewer").fill("a7.employee.reviewer")
                page.locator("#rationale").fill("Retain this source-bound request for planning.")
                old_heading = page.locator("#proposal-heading").inner_text()
                page.locator("#keep-proposal").click()
                page.wait_for_function(
                    "([heading, last]) => document.querySelector('#review-complete')?.hidden === false || "
                    "document.querySelector('#proposal-heading')?.textContent !== heading || last",
                    arg=[old_heading, position == proposal_count - 1],
                )
            page.locator("#review-complete").wait_for(state="visible")
            page.locator("#plan-capabilities").click()
            page.wait_for_url(f"{base_url}/app/pocs/{poc_id}/capability-plan")
            _complete_plan(page)

            page.locator("#open-agreement").click()
            page.wait_for_url(f"{base_url}/app/pocs/{poc_id}/agreement")
            page.locator("#assembly-reviewer").fill("a7.employee.reviewer")
            page.locator("#assembly-rationale").fill("Assemble the exact current plan.")
            page.locator("#prepare-agreement").click()
            page.locator("#agreement-summary").wait_for(state="visible")
            page.locator("#open-customer-review").click()
            page.wait_for_url(re.compile(rf"^{re.escape(base_url)}/review/[A-Za-z0-9_-]+$"))
            assert "customer surface · confirm" in page.locator("body").inner_text().casefold()
            page.locator("#agreement-checkbox").check()
            page.locator("#review-rationale").fill("I confirm this exact visible agreement.")
            page.locator("#confirm-agreement").click()
            page.locator("#review-result").wait_for(state="visible")
            page.locator("#return-to-agreement").click()
            page.wait_for_url(f"{base_url}/app/pocs/{poc_id}/agreement")
            page.locator("#freeze-agreement").click()
            page.wait_for_function("document.querySelector('#agreement-status')?.textContent === 'FROZEN'")
            agreement = page.request.get(f"{base_url}/api/pocs/{poc_id}/agreement").json()
            assert agreement["frozen_contract"]["status"] == "FROZEN"
            assert agreement["confirmation"]["decision"] == "CONFIRM"

            page.locator("#open-evidence").click()
            page.wait_for_url(f"{base_url}/app/pocs/{poc_id}/evidence")
            assert page.locator("#generic-main").count() == 1
            page.locator("#evidence-acknowledged").check()
            page.locator("#start-evidence").click()
            page.wait_for_function("document.querySelector('#evidence-current-status')?.textContent === 'COMPLETED'")
            assert page.locator("#evidence-result-verdict").inner_text() == "PASS"
            assert page.locator("#evidence-limitation").inner_text()
            assert page.locator("#evidence-next-action").inner_text()
            assert page.locator("#evidence-acknowledgement").is_hidden()
            assert page.locator("#evidence-acknowledged").is_disabled()
            assert page.locator("#start-evidence").is_hidden()
            assert page.locator("#start-evidence").is_disabled()
            assert page.locator(".primary-action:visible").count() == 1
            assert page.locator(".primary-action:visible").get_attribute("id") == "handoff-evidence"
            assert evidence_requests and set(evidence_requests[0]) == {"acknowledgement", "idempotency_key"}
            assert len(evidence_requests) == 1
            snapshot = page.request.get(f"{base_url}/api/pocs/{poc_id}/evidence").json()
            assert snapshot["current"]["is_current"] is True
            assert len(snapshot["history"]) == 1
            assert snapshot["shipping_authorized"] is False
            assert "no result authorizes deployment" in snapshot["authorization"]
            page.locator("#start-evidence").dispatch_event("click")
            after_retry = page.request.get(f"{base_url}/api/pocs/{poc_id}/evidence").json()
            assert after_retry["current"] == snapshot["current"]
            assert after_retry["history"] == snapshot["history"]
            assert len(evidence_requests) == 1

            pack_url = page.locator("#evidence-pack-link").get_attribute("href")
            assert re.fullmatch(r"/artifacts/eatm_[a-f0-9]{32}/decision-packet\.html", pack_url or "")
            pack_page = browser.new_page(viewport={"width": 1280, "height": 720})
            pack_page.goto(f"{base_url}{pack_url}")
            pack_text = pack_page.locator("body").inner_text()
            assert "ExitSpec Evidence Pack" in pack_page.title()
            assert "does not authorize deployment" in pack_text
            assert "/Users/" not in pack_text and "/home/" not in pack_text
            pack_page.close()

            assert page.locator("#handoff-fields").is_visible()
            assert page.locator("#decision-owner").input_value() == ""
            assert page.locator("#decision-rationale").input_value() == ""
            assert page.locator("#stop-evidence").is_disabled()
            assert page.locator("#handoff-evidence").is_disabled()
            page.locator("#stop-evidence").dispatch_event("click")
            assert page.request.get(f"{base_url}/api/pocs/{poc_id}/evidence").json()["closure"] is None
            page.locator("#decision-owner").fill("a7.named.human")
            assert page.locator("#stop-evidence").is_enabled()
            assert page.locator("#handoff-evidence").is_disabled()
            decision_rationale = "Reviewed the current pack, limitations, and next action."
            page.locator("#decision-rationale").fill(decision_rationale)
            assert page.locator("#handoff-evidence").is_enabled()
            page.locator("#handoff-evidence").click()
            page.wait_for_function("document.querySelector('#handoff-evidence')?.hidden === true")
            assert page.locator("#evidence-acknowledgement").is_hidden()
            assert page.locator("#evidence-acknowledged").is_disabled()
            assert page.locator("#start-evidence").is_hidden()
            assert page.locator("#start-evidence").is_disabled()
            assert page.locator("#evidence-task-kicker").inner_text() == "Decision recorded"
            assert page.locator("#evidence-task-heading").inner_text() == "Human decision recorded"
            assert page.locator(".primary-action:visible").count() == 0
            closed = page.request.get(f"{base_url}/api/pocs/{poc_id}/evidence").json()
            assert closed["closure"]["decision"] == "HANDOFF_COMPLETED"
            assert closed["closure"]["decided_by"] == "a7.named.human"
            assert closed["closure"]["rationale"] == decision_rationale
            assert closed["closure"]["shipping_authorized"] is False
            page_text = page.locator("body").inner_text()
            for forbidden in ("poc_support_agent_demo", "/Users/", "/home/", "idempotency_key", "api_key"):
                assert forbidden not in page_text
            assert unexpected_http == []
            assert browser_errors == []
        finally:
            browser.close()


def test_canonical_app_reflows_without_horizontal_overflow_and_has_focus_visibility():
    with _running_server() as base_url, playwright_sync.sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 390, "height": 844})
        try:
            page.goto(f"{base_url}/app")
            _assert_no_page_overflow(page, bounded_height=False)
            first_source = page.locator('input[name="first_source_choice"]').first
            first_source.focus()
            assert first_source.evaluate("element => document.activeElement === element")
            outline = first_source.locator("xpath=following-sibling::span").evaluate(
                "element => getComputedStyle(element).outlineStyle"
            )
            assert outline != "none"
            assert page.locator(".skip-link").count() == 1
        finally:
            browser.close()
