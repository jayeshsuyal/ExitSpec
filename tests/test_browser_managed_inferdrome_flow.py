from __future__ import annotations

import os
import re
import threading
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urljoin

import pytest

from exitspec.inferdrome_bundle import verify_inferdrome_bundle
from exitspec.poc_managed_inferdrome_contract import (
    project_managed_inferdrome_evidence,
)
from exitspec.web import DemoSession, ExitSpecDemoServer
from tests.inferdrome_managed_helpers import extract_exact_archive_or_skip
from tests.test_browser_new_id_flow import (
    _assert_bounded_employee_shell,
    _capture_browser_errors,
)


DISPLAY_NAME = "Managed A10 inference acceptance"
CUSTOMER_LABEL = "Northstar AI"
EMAIL_TEXT = (
    "From: buyer@example.com\n"
    "At concurrency 4, p95 time to first token must stay below 20 ms "
    "across 100 measured requests. Error rate must remain below 1% "
    "across all 100 measured requests."
)


@contextmanager
def _running_exact_a10_server(tmp_path: Path):
    extracted = extract_exact_archive_or_skip(tmp_path)
    projection = project_managed_inferdrome_evidence(
        verify_inferdrome_bundle(
            extracted.bundle_path,
            require_customer_eligible=True,
        )
    )
    session = DemoSession.synthetic_support_agent(
        output_root=tmp_path / "exitspec-runs"
    )
    server = ExitSpecDemoServer(
        ("127.0.0.1", 0),
        session,
        inferdrome_runs_root=extracted.bundle_path,
    )
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield (
            "http://127.0.0.1:{0}".format(server.server_port),
            projection,
        )
    finally:
        server.shutdown()
        worker.join(timeout=5)
        server.server_close()
        assert not worker.is_alive()


def _define_exact_managed_rules(expect, page) -> None:
    for position in (1, 2):
        expect(page.locator("#proposal-heading")).to_have_text(
            f"Criterion {position}"
        )
        claim = page.locator("#normalized-claim").text_content().strip().lower()
        if "first token" in claim:
            expect(page.locator("#metric")).to_have_value("TTFT_P95_MS")
            expect(page.locator("#operator")).to_have_value("LT")
            expect(page.locator("#threshold")).to_have_value("20")
            rationale = "Use the customer's exact 20 ms native TTFT bound."
        elif "error rate" in claim:
            expect(page.locator("#metric")).to_have_value(
                "ERROR_RATE_PERCENT"
            )
            expect(page.locator("#operator")).to_have_value("LT")
            expect(page.locator("#threshold")).to_have_value("1")
            rationale = "Use the customer's exact one-percent reliability bound."
        else:
            raise AssertionError(f"Unexpected managed proposal: {claim!r}")
        expect(page.locator("#minimum-samples")).to_have_value("100")
        expect(page.locator("#concurrency")).to_have_value("4")
        page.locator("#reviewer").fill("field_engineer")
        page.locator("#rationale").fill(rationale)
        expect(page.locator("#save-definition")).to_be_enabled()
        page.locator("#save-definition").click()
        if position == 1:
            expect(page.locator("#proposal-heading")).to_have_text(
                "Criterion 2"
            )


@pytest.mark.skipif(
    os.environ.get("EXITSPEC_BROWSER_E2E") != "1",
    reason="set EXITSPEC_BROWSER_E2E=1 to run the Chromium lifecycle test",
)
def test_exact_a10_email_to_managed_receipt_and_handoff(tmp_path: Path) -> None:
    """Prove the exact retained A10 bundle through the customer UI boundary."""

    from playwright import sync_api

    expect = sync_api.expect
    with _running_exact_a10_server(tmp_path) as (base_url, profile):
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
                employee_page.goto(f"{base_url}/app")
                expect(employee_page.locator("#dashboard-main")).to_be_visible()
                _assert_bounded_employee_shell(employee_page)
                employee_page.locator(".new-poc-link").click()
                expect(employee_page).to_have_url(f"{base_url}/app/pocs/new")

                employee_page.locator(
                    'input[name="first_source_choice"][value="EMAIL"]'
                ).check()
                employee_page.locator("#display-name").fill(DISPLAY_NAME)
                employee_page.locator("#customer-label").fill(CUSTOMER_LABEL)
                employee_page.locator("#use-case").fill(
                    "Prove a frozen native vLLM latency and reliability rule."
                )
                employee_page.locator("#owner").fill("field_engineer")
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
                employee_page.locator("#email-text").fill(EMAIL_TEXT)
                employee_page.locator("#capture-source").click()

                expect(employee_page).to_have_url(
                    f"{base_url}/app/pocs/{poc_id}/review"
                )
                for position in (1, 2):
                    expect(
                        employee_page.locator("#proposal-heading")
                    ).to_have_text(f"Proposal {position}")
                    employee_page.locator("#reviewer").fill("field_engineer")
                    employee_page.locator("#rationale").fill(
                        "Keep this explicit measurable customer requirement."
                    )
                    employee_page.locator("#keep-proposal").click()
                    if position == 1:
                        expect(
                            employee_page.locator("#proposal-heading")
                        ).to_have_text("Proposal 2")

                expect(employee_page).to_have_url(
                    f"{base_url}/app/pocs/{poc_id}/define"
                )
                _define_exact_managed_rules(expect, employee_page)

                agreement_route = f"{base_url}/app/pocs/{poc_id}/agreement"
                expect(employee_page).to_have_url(agreement_route)
                expect(
                    employee_page.locator("#agreement-workbench")
                ).to_have_attribute("aria-busy", "false")
                _assert_bounded_employee_shell(employee_page)
                expect(
                    employee_page.locator("#use-inferdrome-target")
                ).to_be_enabled()
                employee_page.locator("#use-inferdrome-target").click()

                expect(
                    employee_page.locator("#managed-evidence-profile")
                ).to_be_visible()
                expect(
                    employee_page.locator("#managed-evidence-profile-select")
                ).to_have_value(profile.run_id)
                expect(
                    employee_page.locator("#managed-profile-model")
                ).to_have_text("Qwen/Qwen2.5-0.5B-Instruct")
                expect(
                    employee_page.locator("#managed-profile-hardware")
                ).to_have_text("NVIDIA A10")
                expect(
                    employee_page.locator("#managed-profile-workload")
                ).to_have_text("100 requests · concurrency 4")
                expect(
                    employee_page.locator("#managed-profile-semantics")
                ).to_have_text(
                    "Native first choices event · nearest-rank p95"
                )
                visible_workbench = employee_page.locator(
                    "#agreement-workbench"
                ).inner_text()
                assert "/Users/" not in visible_workbench
                assert "/private/" not in visible_workbench

                employee_page.locator("#draft-reviewer").fill(
                    "field_engineer"
                )
                employee_page.locator("#draft-rationale").fill(
                    "Freeze this exact retained A10 run and native metric identity."
                )
                expect(
                    employee_page.locator("#create-customer-draft")
                ).to_be_enabled()
                employee_page.locator("#create-customer-draft").click()

                expect(
                    employee_page.locator("#confirmation-panel")
                ).to_be_visible()
                review_link = employee_page.locator("#customer-review-link")
                expect(review_link).to_have_attribute(
                    "href", re.compile(r"^/review/[A-Za-z0-9_-]+$")
                )
                review_href = review_link.get_attribute("href")
                assert review_href is not None

                customer_page.goto(urljoin(base_url, review_href))
                expect(customer_page.locator("#review-view")).to_be_visible()
                expect(
                    customer_page.locator("#criterion-position")
                ).to_have_text("Requirement 1 of 1")
                expect(
                    customer_page.locator("#criterion-threshold")
                ).to_contain_text("20 ms")
                expect(
                    customer_page.locator("#criterion-threshold")
                ).to_contain_text("1%")
                expect(customer_page.locator("#criterion-adapter")).to_contain_text(
                    "vllm_bench_serve@1.0.0"
                )
                expect(customer_page.locator("#criterion-adapter")).to_contain_text(
                    "native first-choices-event"
                )
                expect(customer_page.locator("#criterion-adapter")).to_contain_text(
                    "retrospective"
                )
                expect(
                    customer_page.locator("#review-counting-population")
                ).to_have_text("100 measured records")
                expect(
                    customer_page.locator("#review-counting-latency")
                ).to_contain_text("Role-only or empty-content")
                expect(
                    customer_page.locator("#review-counting-reliability")
                ).to_contain_text("failed or anomalous records count")
                expect(
                    customer_page.locator("#review-counting-boundary")
                ).to_contain_text("retry behavior NOT_AVAILABLE")
                expect(
                    customer_page.locator("#review-counting-boundary")
                ).to_contain_text("INGESTION REJECTED with no verdict")
                expect(
                    customer_page.locator("#review-counting-boundary")
                ).to_contain_text("insufficient evidence is NOT PROVEN")
                customer_page.locator("#agreement-checkbox").check()
                customer_page.locator("#confirm-requirements").click()
                expect(customer_page.locator("#terminal-title")).to_have_text(
                    "Requirements confirmed"
                )

                employee_page.reload(wait_until="domcontentloaded")
                expect(employee_page.locator("#freeze-panel")).to_be_visible()
                expect(
                    employee_page.locator("#freeze-contract")
                ).to_be_enabled()
                employee_page.locator("#freeze-contract").click()

                expect(employee_page).to_have_url(
                    f"{base_url}/app/pocs/{poc_id}"
                )
                expect(
                    employee_page.locator("#performance-main")
                ).to_have_attribute("aria-busy", "false")
                _assert_bounded_employee_shell(employee_page)
                expect(
                    employee_page.locator("#current-task-heading")
                ).to_have_text("Verify the frozen evidence")
                expect(
                    employee_page.locator("#inferdrome-selection")
                ).to_be_visible()
                expect(
                    employee_page.locator("#inferdrome-bundle")
                ).to_have_value(profile.run_id)
                expect(
                    employee_page.locator("#inferdrome-bundle")
                ).to_be_disabled()
                expect(
                    employee_page.locator("#inferdrome-catalog-status")
                ).to_contain_text("Run and digest are frozen")
                expect(employee_page.locator("#run-proof")).to_have_text(
                    "Verify & import evidence"
                )
                employee_page.locator("#execution-acknowledged").check()
                expect(employee_page.locator("#run-proof")).to_be_enabled()
                employee_page.locator("#run-proof").click()

                expect(employee_page.locator("#evidence-verdict")).to_have_text(
                    "PASS", timeout=20_000
                )
                expect(
                    employee_page.locator("#managed-result-summary")
                ).to_be_visible()
                expect(
                    employee_page.locator("#managed-result-p95-ttft")
                ).to_have_text("14.797213 ms")
                expect(
                    employee_page.locator("#managed-result-error-rate")
                ).to_have_text("0%")
                expect(
                    employee_page.locator("#managed-result-records")
                ).to_have_text("100 / 100")
                expect(
                    employee_page.locator(
                        "#managed-result-required-concurrency"
                    )
                ).to_have_text("4")
                expect(
                    employee_page.locator(
                        "#managed-result-observed-concurrency"
                    )
                ).to_have_text("4")
                expect(
                    employee_page.locator("#outcome-breakdown")
                ).to_have_text(
                    "100 records · 100 successful · 0 failed · 0 anomalous"
                )
                expect(employee_page.locator("#receipt-run")).to_have_text(
                    profile.run_id
                )
                expect(employee_page.locator("#receipt-id")).to_have_text(
                    re.compile(r"^irc2_[a-f0-9]{64}$")
                )
                expect(employee_page.locator("#receipt-digest")).to_have_text(
                    profile.bundle_digest
                )
                expect(
                    employee_page.locator("#receipt-applicability")
                ).to_have_text("Compatible")

                evidence_link = employee_page.locator("#evidence-pack-link")
                expect(evidence_link).to_have_attribute(
                    "href",
                    re.compile(
                        r"^/artifacts/pimp_[a-f0-9]{32}/decision-packet\.html$"
                    ),
                )
                evidence_href = evidence_link.get_attribute("href")
                assert evidence_href is not None
                evidence_page = employee_context.new_page()
                evidence_errors = _capture_browser_errors(evidence_page)
                try:
                    evidence_page.goto(urljoin(base_url, evidence_href))
                    expect(evidence_page).to_have_title(
                        "ExitSpec — Managed inference Evidence Pack"
                    )
                    expect(evidence_page.locator("h1")).to_have_text(
                        "Managed inference Evidence Pack"
                    )
                    expect(evidence_page.locator(".verdict")).to_have_text(
                        "PASS"
                    )
                    expect(evidence_page.locator("body")).to_contain_text(
                        "14.7972 ms"
                    )
                    expect(evidence_page.locator("body")).to_contain_text(
                        "100 total · 100 successful · 0 failed"
                    )
                    expect(evidence_page.locator("body")).to_contain_text(
                        "ExitSpec treated it as untrusted input"
                    )
                    expect(evidence_page.locator("body")).to_contain_text(
                        "retrospective conformance demonstration"
                    )
                finally:
                    evidence_page.close()

                expect(employee_page.locator("#closure-panel")).to_be_visible()
                employee_page.locator("#closure-decision").select_option(
                    "HANDOFF_COMPLETED"
                )
                employee_page.locator("#closure-actor").fill(
                    "field_engineer"
                )
                employee_page.locator("#closure-rationale").fill(
                    "Managed A10 Evidence Pack reviewed and handed off."
                )
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
                _assert_bounded_employee_shell(employee_page)
                assert employee_errors == []
                assert customer_errors == []
                assert evidence_errors == []
            finally:
                customer_context.close()
                employee_context.close()
                browser.close()
