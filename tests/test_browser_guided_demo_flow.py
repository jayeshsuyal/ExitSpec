from __future__ import annotations

import os
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

                _assert_bounded_employee_shell(employee_page)
                assert employee_errors == []
                assert customer_errors == []
                assert evidence_errors == []
            finally:
                customer_context.close()
                employee_context.close()
                browser.close()
