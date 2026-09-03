from __future__ import annotations

import os
import re
from urllib.parse import urljoin

import pytest

from tests.test_browser_new_id_flow import (
    _assert_bounded_employee_shell,
    _capture_browser_errors,
    _running_server,
)


@pytest.mark.skipif(
    os.environ.get("EXITSPEC_BROWSER_E2E") != "1",
    reason="set EXITSPEC_BROWSER_E2E=1 to run the Chromium lifecycle test",
)
def test_guided_demo_confirms_freezes_and_proves_seeded_contract(tmp_path):
    from playwright import sync_api

    expect = sync_api.expect
    with _running_server(tmp_path) as base_url:
        with sync_api.sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            employee_context = browser.new_context(
                viewport={"width": 1280, "height": 720}
            )
            customer_context = browser.new_context(
                viewport={"width": 1280, "height": 720}
            )
            employee_page = employee_context.new_page()
            customer_page = customer_context.new_page()
            employee_errors = _capture_browser_errors(employee_page)
            customer_errors = _capture_browser_errors(customer_page)
            evidence_errors: list[str] = []
            employee_page.set_default_timeout(10_000)
            employee_page.set_default_navigation_timeout(10_000)
            customer_page.set_default_timeout(10_000)
            customer_page.set_default_navigation_timeout(10_000)

            try:
                employee_page.goto(f"{base_url}/app?mode=recording")
                expect(employee_page.locator("#current-task")).to_be_visible()
                _assert_bounded_employee_shell(employee_page)

                nav_links = employee_page.locator(".global-nav a")
                first_nav = nav_links.nth(0).bounding_box()
                second_nav = nav_links.nth(1).bounding_box()
                assert first_nav is not None
                assert second_nav is not None
                assert second_nav["x"] - (
                    first_nav["x"] + first_nav["width"]
                ) >= 20

                matches_intent = employee_page.get_by_role(
                    "button", name="Matches intent"
                )
                action_box = matches_intent.bounding_box()
                panel_box = employee_page.locator("#define").bounding_box()
                assert action_box is not None
                assert panel_box is not None
                assert action_box["y"] >= panel_box["y"]
                assert action_box["y"] + action_box["height"] <= (
                    panel_box["y"] + panel_box["height"]
                )

                decision_bar_box = employee_page.locator(
                    ".candidate-actions"
                ).bounding_box()
                rule_value_boxes = employee_page.locator(
                    ".rule-rows dd"
                ).evaluate_all(
                    "elements => elements.map(element => { "
                    "const box = element.getBoundingClientRect(); "
                    "return { y: box.y, height: box.height }; })"
                )
                assert decision_bar_box is not None
                assert rule_value_boxes
                assert max(
                    box["y"] + box["height"] for box in rule_value_boxes
                ) <= decision_bar_box["y"]

                matches_intent.click()
                employee_page.get_by_role(
                    "button", name="Keep as context"
                ).click()
                expect(
                    employee_page.get_by_role(
                        "heading", name="Ready to create the customer review?"
                    )
                ).to_be_visible()
                employee_page.locator("#create-customer-draft").click()

                review_link = employee_page.locator("#customer-draft-link")
                expect(review_link).to_be_visible()
                review_href = review_link.get_attribute("href")
                assert review_href is not None
                customer_page.goto(urljoin(base_url, review_href))
                expect(customer_page.locator("#review-view")).to_be_visible()
                expect(
                    customer_page.get_by_role(
                        "heading", name="Confirm the POC test plan"
                    )
                ).to_be_visible()
                expect(
                    customer_page.locator("#criteria-summary-list > li")
                ).to_have_count(1)
                expect(customer_page.locator(".review-detail-group")).to_have_count(2)
                assert customer_page.evaluate(
                    "document.documentElement.scrollHeight <= window.innerHeight"
                )
                expect(customer_page.locator("#change-details")).to_be_hidden()
                expect(customer_page.locator("#evidence-method")).to_have_text(
                    "Evaluate with ExitSpec · deterministic tool-selection fixture"
                )
                expect(customer_page.locator("#criterion-adapter")).to_contain_text(
                    "deterministic_tool_selection@1.0.0"
                )
                customer_page.locator("#request-changes").click()
                expect(customer_page.locator("#change-details")).to_be_visible()
                expect(customer_page.locator("#change-rationale")).to_be_focused()
                customer_page.locator("#agreement-checkbox").check()
                customer_page.locator("#confirm-requirements").click()
                expect(customer_page.locator("#terminal-title")).to_have_text(
                    "POC agreement confirmed"
                )
                expect(
                    customer_page.locator("#terminal-next-title")
                ).to_have_text("Next: freeze the confirmed contract.")
                expect(
                    customer_page.locator("#terminal-boundary")
                ).not_to_have_class(re.compile("terminal-boundary--changes"))

                expect(employee_page.locator("#freeze-contract")).to_be_visible()
                expect(employee_page.locator("#freeze-contract")).to_be_enabled()
                employee_page.locator("#freeze-contract").click()
                expect(employee_page.locator("#run-proof")).to_be_visible()
                expect(employee_page.locator("#run-proof")).to_be_enabled()
                employee_page.locator("#run-proof").click()

                expect(employee_page.locator("#pack-verdict")).to_have_text("PASS")
                expect(employee_page.locator("#proof-pack-link")).to_be_visible()
                evidence_href = employee_page.locator(
                    "#proof-pack-link"
                ).get_attribute("href")
                assert evidence_href is not None
                evidence_page = employee_context.new_page()
                evidence_errors = _capture_browser_errors(evidence_page)
                try:
                    evidence_page.goto(urljoin(base_url, evidence_href))
                    assert "POC Acceptance Evidence Pack" in evidence_page.title()
                    expect(evidence_page.locator("#evidence-verdict")).to_have_text(
                        "PASS"
                    )
                finally:
                    evidence_page.close()

                expect(employee_page.locator("#closure-panel")).to_be_visible()
                expect(employee_page.locator("#rerun-proof")).to_be_visible()
                employee_page.locator("#closure-decision").select_option(
                    "HANDOFF_COMPLETED"
                )
                employee_page.locator("#closure-rationale").fill(
                    "Reviewed the exact Evidence Pack with the support-agent POC owner."
                )
                employee_page.locator("#record-closure").click()
                expect(employee_page.locator("#closure-receipt")).to_be_visible()
                closure_destination = employee_page.locator(
                    ".closure-dashboard-link"
                )
                expect(closure_destination).to_have_text("Evidence Packs")
                expect(closure_destination).to_have_attribute(
                    "href", "/app/evidence"
                )
                expect(employee_page.locator("#rerun-proof")).to_be_hidden()
                closure_destination.click()
                expect(employee_page).to_have_url(f"{base_url}/app/evidence")
                expect(employee_page.locator("#evidence-pack-list")).to_contain_text(
                    "Support-agent POC"
                )

                _assert_bounded_employee_shell(employee_page)
                assert employee_errors == []
                assert customer_errors == []
                assert evidence_errors == []
            finally:
                customer_context.close()
                employee_context.close()
                browser.close()


@pytest.mark.skipif(
    os.environ.get("EXITSPEC_BROWSER_E2E") != "1",
    reason="set EXITSPEC_BROWSER_E2E=1 to run the Chromium lifecycle test",
)
def test_customer_review_request_changes_stops_before_freeze(tmp_path):
    from playwright import sync_api

    expect = sync_api.expect
    with _running_server(tmp_path) as base_url:
        with sync_api.sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(
                viewport={"width": 1280, "height": 720}
            )
            page = context.new_page()
            errors = _capture_browser_errors(page)
            try:
                page.goto(
                    f"{base_url}/review/local-synthetic-preview"
                    "?mock=local-synthetic"
                )
                expect(page.locator("#review-view")).to_be_visible()
                page.locator("#request-changes").click()
                expect(page.locator("#change-details")).to_be_visible()
                page.locator("#change-rationale").fill(
                    "Use a stricter customer-approved threshold."
                )
                page.locator("#request-changes").click()

                expect(page.locator("#terminal-title")).to_have_text(
                    "Changes requested"
                )
                expect(page.locator("#terminal-next-title")).to_have_text(
                    "Preview only: a real request would return for revision."
                )
                expect(page.locator("#terminal-next-detail")).to_have_text(
                    "No agreement, evidence, or lifecycle state changed."
                )
                expect(page.locator("#terminal-boundary")).to_have_class(
                    re.compile("terminal-boundary--changes")
                )
                assert errors == []
            finally:
                context.close()
                browser.close()
