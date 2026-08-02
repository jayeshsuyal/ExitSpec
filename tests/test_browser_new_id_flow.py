from __future__ import annotations

import os
import re
import threading
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urljoin

import pytest

from exitspec.web import DemoSession, ExitSpecDemoServer


DISPLAY_NAME = "Browser inference acceptance POC"
CUSTOMER_LABEL = "Northstar"
EMAIL_TEXT = (
    "From: buyer@example.com\n"
    "The p95 time to first token must stay below 500 ms. "
    "Error rate must remain below 1%."
)


@contextmanager
def _running_server(tmp_path: Path):
    session = DemoSession.synthetic_support_agent(
        output_root=tmp_path / "runs"
    )
    server = ExitSpecDemoServer(("127.0.0.1", 0), session)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield "http://127.0.0.1:{0}".format(server.server_port)
    finally:
        server.shutdown()
        worker.join(timeout=5)
        server.server_close()
        assert not worker.is_alive()


def _expect_suggested_definition(expect, page, normalized_claim: str) -> str:
    claim = normalized_claim.lower()
    if "first token" in claim:
        expect(page.locator("#metric")).to_have_value("TTFT_P95_MS")
        expect(page.locator("#operator")).to_have_value("LT")
        expect(page.locator("#threshold")).to_have_value("500")
        return "P95 time to first token is bounded by the customer email."
    if "error rate" in claim:
        expect(page.locator("#metric")).to_have_value(
            "ERROR_RATE_PERCENT"
        )
        expect(page.locator("#operator")).to_have_value("LT")
        expect(page.locator("#threshold")).to_have_value("1")
        return "Error rate is bounded by the customer email."
    raise AssertionError(
        "The browser flow received an unexpected measurable proposal: "
        f"{normalized_claim!r}"
    )


@pytest.mark.skipif(
    os.environ.get("EXITSPEC_BROWSER_E2E") != "1",
    reason="set EXITSPEC_BROWSER_E2E=1 to run the Chromium lifecycle test",
)
def test_new_id_email_flow_reaches_completed_pass_evidence_pack(tmp_path):
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
            employee_page.set_default_timeout(10_000)
            employee_page.set_default_navigation_timeout(10_000)
            customer_page.set_default_timeout(10_000)
            customer_page.set_default_navigation_timeout(10_000)

            try:
                employee_page.goto(f"{base_url}/app")
                expect(employee_page.locator("#dashboard-main")).to_be_visible()
                employee_page.locator(".new-poc-link").click()
                expect(employee_page).to_have_url(f"{base_url}/app/pocs/new")

                employee_page.locator(
                    'input[name="first_source_choice"][value="EMAIL"]'
                ).check()
                employee_page.locator("#display-name").fill(DISPLAY_NAME)
                employee_page.locator("#customer-label").fill(CUSTOMER_LABEL)
                employee_page.locator("#use-case").fill(
                    "Verify two bounded inference performance requirements."
                )
                employee_page.locator("#owner").fill("field_engineer")
                expect(employee_page.locator("#create-poc")).to_be_enabled()
                employee_page.locator("#create-poc").click()

                source_route = re.compile(
                    rf"^{re.escape(base_url)}/app/pocs/"
                    r"(poc_[a-z0-9][a-z0-9_-]{2,63})/sources/new$"
                )
                expect(employee_page).to_have_url(source_route)
                route_match = source_route.fullmatch(employee_page.url)
                assert route_match is not None
                poc_id = route_match.group(1)

                expect(
                    employee_page.locator("#source-current-task")
                ).to_have_attribute("aria-busy", "false")
                expect(employee_page.locator("#email-entry")).to_be_visible()
                employee_page.locator("#email-text").fill(EMAIL_TEXT)
                expect(
                    employee_page.locator("#capture-source")
                ).to_be_enabled()
                employee_page.locator("#capture-source").click()

                review_route = f"{base_url}/app/pocs/{poc_id}/review"
                expect(employee_page).to_have_url(review_route)
                expect(
                    employee_page.locator("#proposal-current-task")
                ).to_have_attribute("aria-busy", "false")

                reviewed_claims: list[str] = []
                for position in (1, 2):
                    expect(
                        employee_page.locator("#proposal-heading")
                    ).to_have_text(f"Proposal {position}")
                    claim = (
                        employee_page.locator("#normalized-claim")
                        .text_content()
                        .strip()
                    )
                    reviewed_claims.append(claim)
                    employee_page.locator("#reviewer").fill(
                        "field_engineer"
                    )
                    employee_page.locator("#rationale").fill(
                        "Keep this explicit measurable inference requirement."
                    )
                    expect(
                        employee_page.locator("#keep-proposal")
                    ).to_be_enabled()
                    employee_page.locator("#keep-proposal").click()
                    if position == 1:
                        expect(
                            employee_page.locator("#proposal-heading")
                        ).to_have_text("Proposal 2")

                assert {"first token", "error rate"} == {
                    cue
                    for cue in ("first token", "error rate")
                    if any(cue in claim.lower() for claim in reviewed_claims)
                }

                define_route = f"{base_url}/app/pocs/{poc_id}/define"
                expect(employee_page).to_have_url(define_route)
                expect(
                    employee_page.locator("#definition-current-task")
                ).to_have_attribute("aria-busy", "false")

                defined_claims: list[str] = []
                for position in (1, 2):
                    expect(
                        employee_page.locator("#proposal-heading")
                    ).to_have_text(f"Criterion {position}")
                    claim = (
                        employee_page.locator("#normalized-claim")
                        .text_content()
                        .strip()
                    )
                    defined_claims.append(claim)
                    rationale = _expect_suggested_definition(
                        expect, employee_page, claim
                    )
                    employee_page.locator("#reviewer").fill(
                        "field_engineer"
                    )
                    employee_page.locator("#rationale").fill(rationale)
                    expect(
                        employee_page.locator("#save-definition")
                    ).to_be_enabled()
                    employee_page.locator("#save-definition").click()
                    if position == 1:
                        expect(
                            employee_page.locator("#proposal-heading")
                        ).to_have_text("Criterion 2")

                assert set(defined_claims) == set(reviewed_claims)

                agreement_route = f"{base_url}/app/pocs/{poc_id}/agreement"
                expect(employee_page).to_have_url(agreement_route)
                expect(
                    employee_page.locator("#agreement-workbench")
                ).to_have_attribute("aria-busy", "false")
                expect(
                    employee_page.locator("#use-reference-target")
                ).to_be_enabled()
                employee_page.locator("#use-reference-target").click()
                employee_page.locator("#draft-reviewer").fill(
                    "field_engineer"
                )
                employee_page.locator("#draft-rationale").fill(
                    "Use the deterministic local target for this browser proof."
                )
                expect(
                    employee_page.locator("#create-customer-draft")
                ).to_be_enabled()
                employee_page.locator("#create-customer-draft").click()

                expect(
                    employee_page.locator("#confirmation-panel")
                ).to_be_visible()
                customer_review_link = employee_page.locator(
                    "#customer-review-link"
                )
                expect(customer_review_link).to_be_visible()
                expect(customer_review_link).to_have_attribute(
                    "href", re.compile(r"^/review/[A-Za-z0-9_-]+$")
                )
                review_href = customer_review_link.get_attribute("href")
                assert review_href is not None

                customer_page.goto(urljoin(base_url, review_href))
                expect(customer_page.locator("#review-view")).to_be_visible()
                expect(
                    customer_page.locator("#criterion-position")
                ).to_have_text("Requirement 1 of 1")
                expect(customer_page.locator("#criterion-rule")).to_contain_text(
                    "P95 TTFT"
                )
                expect(customer_page.locator("#criterion-rule")).to_contain_text(
                    "error rate"
                )
                customer_page.locator("#agreement-checkbox").check()
                customer_page.locator("#confirm-requirements").click()
                expect(
                    customer_page.locator("#terminal-state")
                ).to_be_visible()
                expect(customer_page.locator("#terminal-title")).to_have_text(
                    "Requirements confirmed"
                )

                employee_page.reload(wait_until="domcontentloaded")
                expect(employee_page).to_have_url(agreement_route)
                expect(employee_page.locator("#freeze-panel")).to_be_visible()
                expect(
                    employee_page.locator("#freeze-contract")
                ).to_be_enabled()
                employee_page.locator("#freeze-contract").click()

                proof_route = f"{base_url}/app/pocs/{poc_id}"
                expect(employee_page).to_have_url(proof_route)
                expect(
                    employee_page.locator("#performance-main")
                ).to_have_attribute("aria-busy", "false")
                expect(
                    employee_page.locator("#execution-acknowledged")
                ).to_be_enabled()
                employee_page.locator("#execution-acknowledged").check()
                expect(employee_page.locator("#run-proof")).to_be_enabled()
                employee_page.locator("#run-proof").click()

                expect(employee_page.locator("#evidence-verdict")).to_have_text(
                    "PASS", timeout=20_000
                )
                evidence_link = employee_page.locator("#evidence-pack-link")
                expect(evidence_link).to_be_visible()
                expect(evidence_link).to_have_attribute(
                    "href",
                    re.compile(
                        r"^/artifacts/run_[a-f0-9]{32}/decision-packet\.html$"
                    ),
                )
                evidence_href = evidence_link.get_attribute("href")
                assert evidence_href is not None

                evidence_page = employee_context.new_page()
                try:
                    evidence_page.goto(urljoin(base_url, evidence_href))
                    expect(evidence_page).to_have_title(
                        re.compile("ExitSpec performance Evidence Pack")
                    )
                    expect(evidence_page.locator("h1")).to_have_text("PASS")
                finally:
                    evidence_page.close()

                expect(
                    employee_page.locator("#closure-panel")
                ).to_be_visible()
                employee_page.locator("#closure-decision").select_option(
                    "HANDOFF_COMPLETED"
                )
                employee_page.locator("#closure-actor").fill(
                    "field_engineer"
                )
                employee_page.locator("#closure-rationale").fill(
                    "Verified Evidence Pack reviewed and handed to the customer."
                )
                expect(
                    employee_page.locator("#record-closure")
                ).to_be_enabled()
                employee_page.locator("#record-closure").click()
                expect(
                    employee_page.locator("#closure-receipt")
                ).to_be_visible()
                expect(
                    employee_page.locator("#closure-receipt-decision")
                ).to_have_text("Handoff completed")

                employee_page.locator(".closure-dashboard-link").click()
                expect(employee_page).to_have_url(
                    f"{base_url}/app?filter=Completed"
                )
                expect(employee_page.locator("#poc-list")).to_have_attribute(
                    "aria-busy", "false"
                )
                completed_row = employee_page.locator(
                    "#poc-list .poc-list-item"
                ).filter(has_text=DISPLAY_NAME)
                expect(completed_row).to_have_count(1)
                expect(completed_row).to_contain_text(CUSTOMER_LABEL)
                expect(completed_row).to_contain_text("Pass")
                expect(completed_row).to_contain_text(
                    "POC closed by field_engineer"
                )
                expect(completed_row).to_contain_text(
                    "Shipping was not authorized."
                )
            finally:
                customer_context.close()
                employee_context.close()
                browser.close()
