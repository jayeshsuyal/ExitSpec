from __future__ import annotations

import json
import os
import re
import threading
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urljoin

import pytest

from exitspec.poc_inferdrome_import import (
    ProcessLocalPOCInferdromeImportService,
)
from exitspec.poc_performance_run import (
    ProcessLocalPOCPerformanceRunService,
)
from exitspec.poc_source_intake import ProcessLocalPOCSourceIntake
from exitspec.web import DemoSession, ExitSpecDemoServer
from tests.poc_inferdrome_helpers import (
    NOW,
    POC_ID,
    build_external_web_services,
    customer_eligible_bundle,
)


DISPLAY_NAME = "Browser inference acceptance POC"
CUSTOMER_LABEL = "Northstar"
MEASURED_REQUESTS = 96
CONCURRENCY = 6
EMAIL_TEXT = (
    "From: buyer@example.com\n"
    "The p95 time to first token must stay below 650 ms at concurrency 6 "
    "across 96 measured requests. "
    "Error rate must remain below 2% over all 96 attempts. "
    "Monthly infrastructure cost must stay below $100."
)
REVISION_EMAIL_TEXT = (
    "From: buyer@example.com\n"
    "Revised acceptance plan: p95 time to first token must stay below 700 ms "
    "at concurrency 8 across 96 measured requests. "
    "Error rate must remain below 3% over all 96 attempts."
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


@contextmanager
def _running_external_evidence_server(tmp_path: Path):
    lifecycle, drafts, proposals, definitions = build_external_web_services()
    runs_root, _ = customer_eligible_bundle(tmp_path, lifecycle)
    session = DemoSession.synthetic_support_agent(
        output_root=tmp_path / "exitspec-runs"
    )
    server = ExitSpecDemoServer(
        ("127.0.0.1", 0),
        session,
        inferdrome_runs_root=runs_root.resolve(),
    )
    server.draft_poc_service = drafts
    server.proposal_review_service = proposals
    server.contract_definition_service = definitions
    server.poc_source_intake = ProcessLocalPOCSourceIntake(
        draft_lookup=drafts.get,
        clock=lambda: NOW,
    )
    server.poc_source_intake.capture_meeting(
        poc_id=POC_ID,
        transcript_text=(
            "Customer: P95 TTFT must stay below 500 ms.\n"
            "Customer: Error rate must remain below 50 percent."
        ),
        idempotency_key="browser-inferdrome-source",
    )
    server.performance_lifecycle_service = lifecycle
    server.poc_performance_run_service = ProcessLocalPOCPerformanceRunService(
        lifecycle=lifecycle,
        output_root=session.output_root.resolve(),
    )
    server.poc_inferdrome_import_service = (
        ProcessLocalPOCInferdromeImportService(
            lifecycle=lifecycle,
            catalog=server.inferdrome_catalog,
            output_root=session.output_root.resolve(),
            worker_launcher=lambda target: target(),
            clock=lambda: NOW,
        )
    )
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
        expect(page.locator("#threshold")).to_have_value("650")
        expect(page.locator("#minimum-samples")).to_have_value(
            str(MEASURED_REQUESTS)
        )
        expect(page.locator("#concurrency")).to_have_value(str(CONCURRENCY))
        return "P95 time to first token is bounded by the customer email."
    if "error rate" in claim:
        expect(page.locator("#metric")).to_have_value(
            "ERROR_RATE_PERCENT"
        )
        expect(page.locator("#operator")).to_have_value("LT")
        expect(page.locator("#threshold")).to_have_value("2")
        expect(page.locator("#minimum-samples")).to_have_value(
            str(MEASURED_REQUESTS)
        )
        expect(page.locator("#concurrency")).to_have_value(str(CONCURRENCY))
        return "Error rate is bounded by the customer email."
    raise AssertionError(
        "The browser flow received an unexpected measurable proposal: "
        f"{normalized_claim!r}"
    )


def _capture_browser_errors(page) -> list[str]:
    errors: list[str] = []

    def capture_console(message) -> None:
        if message.type == "error":
            errors.append(f"console: {message.text}")

    page.on("console", capture_console)
    page.on("pageerror", lambda error: errors.append(f"page: {error}"))
    return errors


def _layout_metrics(page) -> dict[str, int]:
    return page.evaluate(
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


def _assert_bounded_employee_shell(page) -> None:
    metrics = _layout_metrics(page)
    assert metrics["innerWidth"] == 1280
    assert metrics["innerHeight"] == 720
    assert metrics["scrollWidth"] <= metrics["clientWidth"]
    assert metrics["scrollHeight"] <= metrics["clientHeight"]


def _assert_narrow_keyboard_contract(expect, page) -> None:
    page.set_viewport_size({"width": 320, "height": 900})
    metrics = _layout_metrics(page)
    assert metrics["innerWidth"] == 320
    assert metrics["scrollWidth"] <= metrics["clientWidth"]

    create_link = page.locator(".new-poc-link")
    create_link.focus()
    expect(create_link).to_be_focused()
    focus_style = create_link.evaluate(
        """element => {
          const style = getComputedStyle(element);
          return {
            outlineStyle: style.outlineStyle,
            outlineWidth: style.outlineWidth,
          };
        }"""
    )
    assert focus_style["outlineStyle"] != "none"
    assert focus_style["outlineWidth"] != "0px"


def _create_browser_meeting_poc(expect, page, base_url: str, name: str) -> str:
    page.goto(f"{base_url}/app/pocs/new")
    page.locator(
        'input[name="first_source_choice"][value="MEETING"]'
    ).check()
    page.locator("#display-name").fill(name)
    page.locator("#customer-label").fill("Northstar")
    page.locator("#use-case").fill(
        "Turn a consented synthetic meeting into review-only requirements."
    )
    page.locator("#owner").fill("field_engineer")
    page.locator("#create-poc").click()

    source_route = re.compile(
        rf"^{re.escape(base_url)}/app/pocs/"
        r"(poc_[a-z0-9][a-z0-9_-]{2,63})/sources/new$"
    )
    expect(page).to_have_url(source_route)
    route_match = source_route.fullmatch(page.url)
    assert route_match is not None
    return route_match.group(1)


def _capture_define_supported_email(
    expect,
    page,
    base_url: str,
    poc_id: str,
    *,
    email_text: str,
    ttft_threshold: str,
    error_threshold: str,
    proposal_count: int,
    concurrency: str,
) -> None:
    """Drive one bounded email through review and immutable definition."""

    source_route = f"{base_url}/app/pocs/{poc_id}/sources/new"
    if page.url != source_route:
        page.goto(source_route)
    expect(page.locator("#source-current-task")).to_have_attribute(
        "aria-busy", "false"
    )
    page.locator('input[name="source_kind"][value="EMAIL"]').check()
    page.locator("#email-text").fill(email_text)
    page.locator("#capture-source").click()

    expect(page).to_have_url(f"{base_url}/app/pocs/{poc_id}/review")
    kept_claims: list[str] = []
    for position in range(1, proposal_count + 1):
        expect(page.locator("#proposal-heading")).to_have_text(
            f"Proposal {position}"
        )
        claim = page.locator("#normalized-claim").text_content().strip()
        page.locator("#reviewer").fill("field_engineer")
        if "first token" in claim.lower() or "error rate" in claim.lower():
            expect(page.locator("#proposal-support")).to_contain_text(
                "Executable candidate"
            )
            page.locator("#rationale").fill(
                "Keep this source-backed executable requirement."
            )
            kept_claims.append(claim)
            page.locator("#keep-proposal").click()
        else:
            expect(page.locator("#proposal-support")).to_contain_text(
                "Not executable in this demo"
            )
            page.locator("#rationale").fill(
                "Retain this unsupported claim as NOT_PROVEN context."
            )
            expect(page.locator("#keep-proposal")).to_be_disabled()
            page.locator("#discard-proposal").click()

    assert len(kept_claims) == 2
    expect(page).to_have_url(f"{base_url}/app/pocs/{poc_id}/define")
    for position in (1, 2):
        expect(page.locator("#proposal-heading")).to_have_text(
            f"Criterion {position}"
        )
        claim = page.locator("#normalized-claim").text_content().strip()
        if "first token" in claim.lower():
            expect(page.locator("#metric")).to_have_value("TTFT_P95_MS")
            expect(page.locator("#threshold")).to_have_value(ttft_threshold)
        elif "error rate" in claim.lower():
            expect(page.locator("#metric")).to_have_value(
                "ERROR_RATE_PERCENT"
            )
            expect(page.locator("#threshold")).to_have_value(error_threshold)
        else:
            raise AssertionError(f"Unsupported claim reached definition: {claim!r}")
        expect(page.locator("#minimum-samples")).to_have_value(
            str(MEASURED_REQUESTS)
        )
        expect(page.locator("#concurrency")).to_have_value(concurrency)
        page.locator("#reviewer").fill("field_engineer")
        page.locator("#rationale").fill(
            "Verified against the complete customer source."
        )
        expect(page.locator("#save-definition")).to_be_enabled()
        page.locator("#save-definition").click()

    expect(page).to_have_url(f"{base_url}/app/pocs/{poc_id}/agreement")


def _prepare_local_review(expect, page, poc_id: str) -> str:
    """Prepare one local target and return its separate customer-review path."""

    expect(page.locator("#agreement-workbench")).to_have_attribute(
        "aria-busy", "false"
    )
    page.locator("#use-reference-target").click()
    page.locator("#draft-reviewer").fill("field_engineer")
    page.locator("#draft-rationale").fill(
        "Use the deterministic local target for this browser proof."
    )
    expect(page.locator("#create-customer-draft")).to_be_enabled()
    page.locator("#create-customer-draft").click()
    link = page.locator("#customer-review-link")
    expect(link).to_be_visible()
    expect(link).to_have_attribute("href", re.compile(r"^/review/[A-Za-z0-9_-]+$"))
    href = link.get_attribute("href")
    assert href is not None
    return href


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
                _assert_bounded_employee_shell(employee_page)
                source_copy_size = employee_page.locator(
                    ".source-option small"
                ).first.evaluate(
                    "element => Number.parseFloat("
                    "getComputedStyle(element).fontSize)"
                )
                assert source_copy_size >= 12

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
                _assert_bounded_employee_shell(employee_page)
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
                _assert_bounded_employee_shell(employee_page)

                kept_claims: list[str] = []
                for position in (1, 2, 3):
                    expect(
                        employee_page.locator("#proposal-heading")
                    ).to_have_text(f"Proposal {position}")
                    claim = (
                        employee_page.locator("#normalized-claim")
                        .text_content()
                        .strip()
                    )
                    employee_page.locator("#reviewer").fill(
                        "field_engineer"
                    )
                    if "cost" in claim.lower():
                        expect(
                            employee_page.locator("#proposal-support")
                        ).to_contain_text("Not executable in this demo")
                        employee_page.locator("#rationale").fill(
                            "Keep this customer claim visible as NOT_PROVEN."
                        )
                        expect(
                            employee_page.locator("#keep-proposal")
                        ).to_be_disabled()
                        employee_page.locator("#discard-proposal").click()
                    else:
                        expect(
                            employee_page.locator("#proposal-support")
                        ).to_contain_text("Executable candidate")
                        employee_page.locator("#rationale").fill(
                            "Keep this explicit measurable inference requirement."
                        )
                        expect(
                            employee_page.locator("#keep-proposal")
                        ).to_be_enabled()
                        kept_claims.append(claim)
                        employee_page.locator("#keep-proposal").click()
                    if position < 3:
                        expect(
                            employee_page.locator("#proposal-heading")
                        ).to_have_text(f"Proposal {position + 1}")
                        _assert_bounded_employee_shell(employee_page)

                assert {"first token", "error rate"} == {
                    cue
                    for cue in ("first token", "error rate")
                    if any(cue in claim.lower() for claim in kept_claims)
                }

                define_route = f"{base_url}/app/pocs/{poc_id}/define"
                expect(employee_page).to_have_url(define_route)
                expect(
                    employee_page.locator("#definition-current-task")
                ).to_have_attribute("aria-busy", "false")
                _assert_bounded_employee_shell(employee_page)

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
                        _assert_bounded_employee_shell(employee_page)

                assert set(defined_claims) == set(kept_claims)

                agreement_route = f"{base_url}/app/pocs/{poc_id}/agreement"
                expect(employee_page).to_have_url(agreement_route)
                expect(
                    employee_page.locator("#agreement-workbench")
                ).to_have_attribute("aria-busy", "false")
                _assert_bounded_employee_shell(employee_page)
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
                _assert_bounded_employee_shell(employee_page)
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
                expect(
                    customer_page.locator("#review-counting-policy")
                ).to_be_visible()
                expect(
                    customer_page.locator("#review-counting-population")
                ).to_have_text(f"{MEASURED_REQUESTS} measured attempts")
                expect(
                    customer_page.locator("#review-counting-reliability")
                ).to_contain_text(f"all {MEASURED_REQUESTS} attempts")
                expect(
                    customer_page.locator("#review-counting-boundary")
                ).to_contain_text("NOT PROVEN")
                expect(customer_page.locator("#excluded-list")).to_contain_text(
                    "Monthly infrastructure cost"
                )
                customer_page.locator("#agreement-checkbox").check()
                customer_page.locator("#confirm-requirements").click()
                expect(
                    customer_page.locator("#terminal-state")
                ).to_be_visible()
                expect(customer_page.locator("#terminal-title")).to_have_text(
                    "POC agreement confirmed"
                )

                employee_page.reload(wait_until="domcontentloaded")
                expect(employee_page).to_have_url(agreement_route)
                expect(employee_page.locator("#freeze-panel")).to_be_visible()
                _assert_bounded_employee_shell(employee_page)
                expect(
                    employee_page.locator("#freeze-contract")
                ).to_be_enabled()
                employee_page.locator("#freeze-contract").click()

                proof_route = f"{base_url}/app/pocs/{poc_id}"
                expect(employee_page).to_have_url(proof_route)
                expect(
                    employee_page.locator("#performance-main")
                ).to_have_attribute("aria-busy", "false")
                _assert_bounded_employee_shell(employee_page)
                expect(
                    employee_page.locator("#execution-acknowledged")
                ).to_be_enabled()
                employee_page.locator("#execution-acknowledged").check()
                expect(employee_page.locator("#run-proof")).to_be_enabled()
                employee_page.locator("#run-proof").click()

                expect(employee_page.locator("#evidence-verdict")).to_have_text(
                    "PASS", timeout=20_000
                )
                expect(
                    employee_page.locator("#outcome-breakdown")
                ).to_be_visible()
                expect(
                    employee_page.locator("#outcome-breakdown")
                ).to_contain_text(
                    f"{MEASURED_REQUESTS} attempts · "
                    f"{MEASURED_REQUESTS} successful"
                )
                _assert_bounded_employee_shell(employee_page)
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
                evidence_errors = _capture_browser_errors(evidence_page)
                try:
                    evidence_page.goto(urljoin(base_url, evidence_href))
                    expect(evidence_page).to_have_title(
                        re.compile("ExitSpec performance Evidence Pack")
                    )
                    expect(evidence_page.locator("h1")).to_have_text("PASS")
                    expect(
                        evidence_page.locator("#counting-title")
                    ).to_have_text("How results were counted")
                    expect(
                        evidence_page.locator(".counting-copy")
                    ).to_contain_text(
                        f"{MEASURED_REQUESTS} attempts · "
                        f"{MEASURED_REQUESTS} successful"
                    )
                    expect(
                        evidence_page.locator(".counting-copy")
                    ).to_contain_text(
                        f"all {MEASURED_REQUESTS} measured attempts"
                    )
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
                _assert_bounded_employee_shell(employee_page)
                _assert_narrow_keyboard_contract(expect, employee_page)
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
def test_customer_changes_create_version_two_before_freeze_and_proof(tmp_path):
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
            employee = employee_context.new_page()
            customer = customer_context.new_page()
            employee_errors = _capture_browser_errors(employee)
            customer_errors = _capture_browser_errors(customer)
            employee.set_default_timeout(10_000)
            customer.set_default_timeout(10_000)
            try:
                employee.goto(f"{base_url}/app/pocs/new")
                employee.locator(
                    'input[name="first_source_choice"][value="EMAIL"]'
                ).check()
                employee.locator("#display-name").fill(
                    "Revision-safe inference POC"
                )
                employee.locator("#customer-label").fill("Northstar")
                employee.locator("#use-case").fill(
                    "Preserve customer-requested agreement versions."
                )
                employee.locator("#owner").fill("field_engineer")
                employee.locator("#create-poc").click()
                source_route = re.compile(
                    rf"^{re.escape(base_url)}/app/pocs/"
                    r"(poc_[a-z0-9][a-z0-9_-]{2,63})/sources/new$"
                )
                expect(employee).to_have_url(source_route)
                match = source_route.fullmatch(employee.url)
                assert match is not None
                poc_id = match.group(1)

                _capture_define_supported_email(
                    expect,
                    employee,
                    base_url,
                    poc_id,
                    email_text=EMAIL_TEXT,
                    ttft_threshold="650",
                    error_threshold="2",
                    proposal_count=3,
                    concurrency=str(CONCURRENCY),
                )
                first_review = _prepare_local_review(expect, employee, poc_id)

                customer.goto(urljoin(base_url, first_review))
                expect(customer.locator("#contract-version")).to_have_text("1")
                customer.locator("#request-changes").click()
                expect(customer.locator("#change-details")).to_be_visible()
                customer.locator("#change-rationale").fill(
                    "Use 700 ms, 3% error, and concurrency 8 for the full plan."
                )
                customer.locator("#request-changes").click()
                expect(customer.locator("#terminal-title")).to_have_text(
                    "Changes requested"
                )
                expect(customer.locator("#terminal-next-title")).to_have_text(
                    "Next: revise the test plan and issue a new version."
                )
                expect(customer.locator("#return-to-app")).to_have_attribute(
                    "href", f"/app/pocs/{poc_id}/agreement"
                )

                employee.reload(wait_until="domcontentloaded")
                expect(employee.locator("#changes-requested-actions")).to_be_visible()
                expect(employee.locator("#start-revision")).to_be_enabled()
                employee.locator("#start-revision").click()
                expect(employee).to_have_url(
                    f"{base_url}/app/pocs/{poc_id}/sources/new"
                )
                expect(employee.locator("#current-task-heading")).to_have_text(
                    "Add one customer source"
                )
                expect(employee.locator("#task-guidance")).to_contain_text(
                    "complete replacement TTFT + error-rate plan"
                )

                retired_review = customer_context.request.get(
                    urljoin(
                        base_url,
                        first_review.replace("/review/", "/api/review/", 1),
                    )
                )
                assert retired_review.status == 404

                _capture_define_supported_email(
                    expect,
                    employee,
                    base_url,
                    poc_id,
                    email_text=REVISION_EMAIL_TEXT,
                    ttft_threshold="700",
                    error_threshold="3",
                    proposal_count=2,
                    concurrency="8",
                )
                second_review = _prepare_local_review(expect, employee, poc_id)
                assert second_review != first_review

                customer.goto(urljoin(base_url, second_review))
                expect(customer.locator("#review-view")).to_be_visible()
                expect(customer.locator("#contract-version")).to_have_text("2")
                expect(customer.locator("#criterion-rule")).to_contain_text(
                    "700 ms"
                )
                expect(customer.locator("#criterion-rule")).to_contain_text(
                    "3%"
                )
                customer.locator("#agreement-checkbox").check()
                customer.locator("#confirm-requirements").click()
                expect(customer.locator("#terminal-title")).to_have_text(
                    "POC agreement confirmed"
                )
                expect(customer.locator("#terminal-next-title")).to_have_text(
                    "Next: freeze the confirmed contract."
                )

                employee.reload(wait_until="domcontentloaded")
                expect(employee.locator("#freeze-panel")).to_be_visible()
                employee.locator("#freeze-contract").click()
                expect(employee).to_have_url(f"{base_url}/app/pocs/{poc_id}")
                employee.locator("#execution-acknowledged").check()
                employee.locator("#run-proof").click()
                expect(employee.locator("#evidence-verdict")).to_have_text(
                    "PASS", timeout=20_000
                )
                expect(employee.locator("#requirement-list")).to_contain_text(
                    "< 700 ms"
                )
                expect(employee.locator("#requirement-list")).to_contain_text(
                    "< 3%"
                )
                expect(employee.locator("#configured-concurrency")).to_have_text(
                    "8"
                )
                assert employee_errors == []
                assert customer_errors == []
            finally:
                customer_context.close()
                employee_context.close()
                browser.close()


@pytest.mark.skipif(
    os.environ.get("EXITSPEC_BROWSER_E2E") != "1",
    reason="set EXITSPEC_BROWSER_E2E=1 to run the Chromium lifecycle test",
)
def test_external_evidence_flow_recalculates_pack_and_completes_handoff(
    tmp_path,
):
    from playwright import sync_api

    expect = sync_api.expect

    with _running_external_evidence_server(tmp_path) as base_url:
        with sync_api.sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(
                viewport={"width": 1280, "height": 720}
            )
            page = context.new_page()
            evidence_page = context.new_page()
            page_errors = _capture_browser_errors(page)
            evidence_errors = _capture_browser_errors(evidence_page)
            page.set_default_timeout(10_000)
            page.set_default_navigation_timeout(10_000)
            evidence_page.set_default_timeout(10_000)

            try:
                page.goto(f"{base_url}/app")
                expect(page.locator("#poc-list")).to_have_attribute(
                    "aria-busy", "false"
                )
                external_action = page.get_by_role(
                    "link",
                    name="Select sealed evidence for Imported inference proof",
                )
                expect(external_action).to_be_visible()
                expect(page.locator("#continue-card")).to_contain_text(
                    "Select sealed Inferdrome evidence"
                )
                external_action.click()
                expect(page).to_have_url(f"{base_url}/app/pocs/{POC_ID}")
                expect(page.locator("#performance-main")).to_have_attribute(
                    "aria-busy", "false"
                )
                _assert_bounded_employee_shell(page)

                expect(page.locator(".primary-action")).to_have_count(1)
                expect(page.locator("#current-task-heading")).to_have_text(
                    "Import sealed evidence"
                )
                expect(page.locator("#inferdrome-selection")).to_be_visible()
                expect(page.locator("#inferdrome-bundle")).to_be_enabled()
                expect(page.locator("#inferdrome-bundle option")).to_have_count(
                    1
                )
                expect(
                    page.locator("#inferdrome-catalog-status")
                ).to_contain_text("1 eligible sealed bundle")
                expect(page.locator("#run-proof")).to_have_text(
                    "Import sealed evidence"
                )
                expect(page.locator("#run-proof")).to_be_disabled()
                expect(
                    page.locator("#execution-acknowledgement-copy")
                ).to_contain_text(
                    "authorize ExitSpec to import and evaluate"
                )

                page.locator("#execution-acknowledged").check()
                expect(page.locator("#run-proof")).to_be_enabled()
                page.locator("#run-proof").click()

                expect(page.locator("#evidence-verdict")).to_have_text(
                    "NOT PROVEN", timeout=20_000
                )
                expect(page.locator("#outcome-breakdown")).to_contain_text(
                    "producer-reported failed"
                )
                expect(page.locator("#import-receipt")).to_be_visible()
                expect(page.locator("#receipt-run")).to_have_text(
                    re.compile(r"^run-")
                )
                expect(page.locator("#receipt-id")).to_have_text(
                    re.compile(r"^irc_")
                )
                expect(page.locator("#receipt-digest")).to_have_text(
                    re.compile(r"^sha256:[a-f0-9]{64}$")
                )
                expect(page.locator("#receipt-applicability")).to_contain_text(
                    "RELIABILITY_CLASSIFICATION_UNAVAILABLE"
                )
                _assert_bounded_employee_shell(page)

                evidence_link = page.locator("#evidence-pack-link")
                expect(evidence_link).to_be_visible()
                expect(evidence_link).to_have_attribute(
                    "href",
                    re.compile(
                        r"^/artifacts/pimp_[a-f0-9]{32}/decision-packet\.html$"
                    ),
                )
                evidence_href = evidence_link.get_attribute("href")
                assert evidence_href is not None
                evidence_page.goto(urljoin(base_url, evidence_href))
                expect(evidence_page).to_have_title(
                    re.compile("ExitSpec .* Inferdrome Evidence Pack")
                )
                expect(evidence_page.locator(".verdict")).to_have_text(
                    "NOT PROVEN"
                )
                expect(evidence_page.locator("body")).to_contain_text(
                    "ExitSpec independently verified"
                )
                expect(evidence_page.locator("body")).to_contain_text(
                    "Producer verdicts were ignored"
                )

                expect(page.locator("#closure-panel")).to_be_visible()
                page.set_viewport_size({"width": 390, "height": 844})
                mobile_metrics = _layout_metrics(page)
                assert mobile_metrics["innerWidth"] == 390
                assert (
                    mobile_metrics["scrollWidth"]
                    <= mobile_metrics["clientWidth"]
                )
                expect(page.locator("#record-closure")).to_be_visible()
                page.set_viewport_size({"width": 1280, "height": 720})
                _assert_bounded_employee_shell(page)
                page.locator("#closure-decision").select_option(
                    "HANDOFF_COMPLETED"
                )
                page.locator("#closure-actor").fill("field_engineer")
                page.locator("#closure-rationale").fill(
                    "Independently recalculated imported evidence handed off."
                )
                expect(page.locator("#record-closure")).to_be_enabled()
                page.locator("#record-closure").click()
                expect(page.locator("#closure-receipt")).to_be_visible()
                expect(
                    page.locator("#closure-receipt-decision")
                ).to_have_text("Handoff completed")
                assert page_errors == []
                assert evidence_errors == []
            finally:
                evidence_page.close()
                context.close()
                browser.close()


@pytest.mark.skipif(
    os.environ.get("EXITSPEC_BROWSER_E2E") != "1",
    reason="set EXITSPEC_BROWSER_E2E=1 to run the Chromium lifecycle test",
)
def test_meeting_microphone_demo_records_consent_before_review_handoff(tmp_path):
    from playwright import sync_api

    expect = sync_api.expect
    browser_fixture = """
      window.__sttDemoEvents = [];
      window.__microphoneRequests = 0;
      window.__trackStops = 0;
      window.__failConsent = false;
      window.__denyMicrophone = false;
      window.__failCapture = false;
      window.__suppressNextStopEvent = true;

      const realFetch = window.fetch.bind(window);
      window.fetch = async (...args) => {
        const path = typeof args[0] === "string" ? args[0] : args[0].url;
        const method = (args[1] && args[1].method) ||
          (typeof args[0] === "string" ? "GET" : args[0].method);
        if (window.__failConsent && path.endsWith("/stt/consents")) {
          return new Response('{"error":"synthetic consent failure"}', {
            status: 503,
            headers: { "Content-Type": "application/json" },
          });
        }
        if (
          window.__failCapture &&
          path.includes("/stt/captures/") &&
          method === "POST"
        ) {
          return new Response('{"error":"synthetic upload failure"}', {
            status: 503,
            headers: { "Content-Type": "application/json" },
          });
        }
        const response = await realFetch(...args);
        if (path.endsWith("/stt/consents") && response.ok) {
          window.__sttDemoEvents.push("consent-recorded");
        }
        return response;
      };

      const track = {
        stop() {
          window.__trackStops += 1;
        },
      };
      Object.defineProperty(navigator, "mediaDevices", {
        configurable: true,
        value: {
          async getUserMedia() {
            window.__microphoneRequests += 1;
            window.__sttDemoEvents.push("microphone-requested");
            if (window.__denyMicrophone) {
              throw new DOMException("Synthetic denial", "NotAllowedError");
            }
            return { getTracks: () => [track] };
          },
        },
      });

      class FakeMediaRecorder {
        static isTypeSupported(type) {
          return type === "audio/webm";
        }

        constructor(stream, options) {
          this.stream = stream;
          this.mimeType = options.mimeType;
          this.state = "inactive";
          this.ondataavailable = null;
          this.onstop = null;
        }

        start() {
          this.state = "recording";
          window.__sttDemoEvents.push("recording-started");
        }

        stop() {
          if (this.state !== "recording") return;
          this.state = "inactive";
          const data = new Blob(
            [new Uint8Array([26, 69, 223, 163, 69, 120, 105, 116])],
            { type: "audio/webm" }
          );
          if (this.ondataavailable) this.ondataavailable({ data });
          if (window.__suppressNextStopEvent) {
            window.__suppressNextStopEvent = false;
            return;
          }
          window.setTimeout(() => {
            if (this.onstop) this.onstop();
          }, 0);
        }
      }
      window.MediaRecorder = FakeMediaRecorder;
    """

    with _running_server(tmp_path) as base_url:
        with sync_api.sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(
                viewport={"width": 1280, "height": 720}
            )
            page = context.new_page()
            page.add_init_script(browser_fixture)
            page.set_default_timeout(10_000)
            page.set_default_navigation_timeout(10_000)

            try:
                page.goto(f"{base_url}/app/pocs/new")
                page.locator(
                    'input[name="first_source_choice"][value="MEETING"]'
                ).check()
                page.locator("#display-name").fill(
                    "Browser microphone acceptance POC"
                )
                page.locator("#customer-label").fill("Northstar")
                page.locator("#use-case").fill(
                    "Prove consented synthetic meeting intake."
                )
                page.locator("#owner").fill("field_engineer")
                page.locator("#create-poc").click()

                source_route = re.compile(
                    rf"^{re.escape(base_url)}/app/pocs/"
                    r"(poc_[a-z0-9][a-z0-9_-]{2,63})/sources/new$"
                )
                expect(page).to_have_url(source_route)
                route_match = source_route.fullmatch(page.url)
                assert route_match is not None
                poc_id = route_match.group(1)

                record_mode = page.locator("#meeting-mode-record")
                expect(record_mode).to_be_enabled()
                record_mode.check()
                expect(page.locator("#meeting-record-panel")).to_be_visible()
                expect(page.locator(".synthetic-badge")).to_have_text(
                    "Not real STT"
                )
                expect(page.locator("#stt-disclosure")).to_contain_text(
                    "does not transcribe spoken words"
                )
                expect(page.locator("#capture-source")).to_have_text(
                    "Record first"
                )

                page.locator("#recording-notice-ack").check()
                page.locator("#all-speakers-consent").check()
                page.locator("#synthetic-output-ack").check()
                expect(page.locator("#start-recording")).to_be_enabled()

                page.evaluate("window.__failConsent = true")
                page.locator("#start-recording").click()
                expect(page.locator("#intake-error")).to_contain_text(
                    "microphone was not enabled"
                )
                assert page.evaluate("window.__microphoneRequests") == 0

                page.evaluate(
                    "window.__failConsent = false; window.__denyMicrophone = true"
                )
                page.locator("#start-recording").click()
                expect(page.locator("#intake-error")).to_contain_text(
                    "Microphone access failed"
                )
                assert page.evaluate("window.__microphoneRequests") == 1
                page.locator("#meeting-mode-paste").check()
                expect(page.locator("#meeting-transcript")).to_be_enabled()
                page.locator("#meeting-transcript").fill(
                    "Customer: Paste mode remains available."
                )
                page.locator("#meeting-mode-record").check()
                page.evaluate("window.__denyMicrophone = false")

                expect(page.locator("#start-recording")).to_be_enabled()
                page.locator("#start-recording").click()

                expect(page.locator("#recording-status")).to_contain_text(
                    "Recording locally"
                )
                page.wait_for_timeout(350)
                expect(page.locator("#stop-recording")).to_be_enabled()
                page.locator("#stop-recording").click()
                expect(page.locator("#intake-error")).to_contain_text(
                    "Browser recording did not finish safely"
                )
                expect(page.locator("#start-recording")).to_be_enabled()

                page.locator("#start-recording").click()
                page.wait_for_timeout(350)
                expect(page.locator("#stop-recording")).to_be_enabled()
                page.locator("#stop-recording").click()
                expect(page.locator("#recording-status")).to_contain_text(
                    "Clip ready"
                )
                expect(page.locator("#capture-source")).to_have_text(
                    "Create review proposals"
                )
                expect(page.locator("#capture-source")).to_be_enabled()

                assert page.evaluate("window.__microphoneRequests") == 3
                assert page.evaluate("window.__sttDemoEvents")[:2] == [
                    "consent-recorded",
                    "microphone-requested",
                ]
                assert page.evaluate("window.__trackStops") >= 2

                page.evaluate("window.__failCapture = true")
                page.locator("#capture-source").click()
                expect(page.locator("#intake-error")).to_contain_text(
                    "Audio was cleared; record a new clip"
                )
                expect(page.locator("#capture-source")).to_have_text(
                    "Record first"
                )
                page.evaluate("window.__failCapture = false")

                page.locator("#start-recording").click()
                page.wait_for_timeout(350)
                page.locator("#stop-recording").click()
                expect(page.locator("#capture-source")).to_be_enabled()
                assert page.evaluate("window.__microphoneRequests") == 4
                assert page.evaluate("window.__trackStops") >= 3
                page.locator("#capture-source").click()
                expect(page).to_have_url(
                    f"{base_url}/app/pocs/{poc_id}/review"
                )
                expect(page.locator("#proposal-heading")).to_have_text(
                    "Proposal 1"
                )
                first_claim = page.locator("#normalized-claim").text_content()
                assert first_claim is not None
                assert "first token" in first_claim.lower()
            finally:
                context.close()
                browser.close()


@pytest.mark.skipif(
    os.environ.get("EXITSPEC_BROWSER_E2E") != "1",
    reason="set EXITSPEC_BROWSER_E2E=1 to run the Chromium lifecycle test",
)
def test_guided_meeting_session_recovers_and_reaches_human_review(tmp_path):
    from playwright import sync_api

    expect = sync_api.expect

    with _running_server(tmp_path) as base_url:
        with sync_api.sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(
                viewport={"width": 1280, "height": 720}
            )
            page = context.new_page()
            page_errors = _capture_browser_errors(page)
            page.set_default_timeout(10_000)
            page.set_default_navigation_timeout(10_000)
            meeting_posts: list[tuple[str, dict[str, object]]] = []

            def capture_meeting_post(request) -> None:
                if (
                    request.method == "POST"
                    and "/meeting-sessions" in request.url
                ):
                    payload = request.post_data_json
                    assert isinstance(payload, dict)
                    meeting_posts.append((request.url, payload))

            page.on("request", capture_meeting_post)

            try:
                poc_id = _create_browser_meeting_poc(
                    expect,
                    page,
                    base_url,
                    "Browser guided meeting POC",
                )
                review_route = f"{base_url}/app/pocs/{poc_id}/review"

                expect(page.locator("#meeting-mode-session")).to_be_checked()
                expect(page.locator("#meeting-session-panel")).to_be_visible()
                expect(
                    page.locator("#meeting-session-disclosure")
                ).to_have_text(
                    "This is a synthetic ExitSpec Zoom RTMS test. "
                    "Transcript only; no customer data."
                )
                expect(
                    page.locator("#meeting-session-state-badge")
                ).to_have_text("Not connected")
                expect(page.locator("#capture-source")).to_have_text(
                    "Record consent"
                )
                expect(page.locator("#capture-source")).to_be_disabled()
                _assert_bounded_employee_shell(page)

                page.locator("#meeting-session-notice-ack").check()
                page.locator("#meeting-session-participants-consent").check()
                page.locator("#meeting-session-synthetic-ack").check()
                expect(page.locator("#capture-source")).to_be_enabled()
                page.locator("#capture-source").click()

                expect(
                    page.locator("#meeting-session-state-badge")
                ).to_have_text("Consent recorded")
                expect(page.locator("#capture-source")).to_have_text(
                    "Start synthetic capture"
                )
                expect(page.locator("#meeting-mode-session")).to_be_disabled()

                page.reload()
                expect(
                    page.locator("#meeting-session-state-badge")
                ).to_have_text("Consent recorded")
                expect(page.locator("#capture-source")).to_have_text(
                    "Start synthetic capture"
                )
                _assert_bounded_employee_shell(page)

                start_route = re.compile(
                    rf"^{re.escape(base_url)}/api/pocs/{re.escape(poc_id)}/"
                    r"meeting-sessions/meetsess_[a-f0-9]{64}/start$"
                )
                raw_marker = "raw-provider-secret-must-never-render"

                def fail_first_start(route, request) -> None:
                    del request
                    route.fulfill(
                        status=503,
                        content_type="application/json",
                        body=json.dumps({"error": raw_marker}),
                    )

                page.route(start_route, fail_first_start)
                page.locator("#capture-source").click()
                expect(page.locator("#intake-error")).to_be_visible()
                expect(page.locator("#intake-error")).to_contain_text(
                    "did not return a trusted result"
                )
                expect(page.locator("body")).not_to_contain_text(raw_marker)
                expect(
                    page.locator("#meeting-session-state-badge")
                ).to_have_text("Consent recorded")
                expect(page.locator("#capture-source")).to_have_text(
                    "Start synthetic capture"
                )
                page.unroute(start_route, fail_first_start)

                page.locator("#capture-source").click()
                expect(
                    page.locator("#meeting-session-state-badge")
                ).to_have_text("Synthetic running")
                expect(page.locator("#capture-source")).to_have_text(
                    "Draft requirements now"
                )

                draft_route = re.compile(
                    rf"^{re.escape(base_url)}/api/pocs/{re.escape(poc_id)}/"
                    r"meeting-sessions/meetsess_[a-f0-9]{64}/draft$"
                )

                def corrupt_completed_draft(route, request) -> None:
                    del request
                    response = route.fetch()
                    payload = response.json()
                    payload["producer_verdict"] = "PASS"
                    route.fulfill(
                        status=response.status,
                        content_type="application/json",
                        body=json.dumps(payload),
                    )

                page.route(draft_route, corrupt_completed_draft)
                page.locator("#capture-source").click()
                expect(page).to_have_url(review_route)
                expect(page.locator("#proposal-heading")).to_have_text(
                    "Proposal 1"
                )
                expect(page.locator("#progress-copy")).to_have_text(
                    "Proposal 1 of 2"
                )
                expect(page.locator("#normalized-claim")).to_contain_text(
                    "p95 time to first token"
                )
                expect(page.locator("#review-state")).to_have_count(0)
                _assert_bounded_employee_shell(page)

                start_posts = [
                    payload
                    for url, payload in meeting_posts
                    if url.endswith("/start")
                ]
                assert len(start_posts) == 2
                assert start_posts[0] == start_posts[1]

                serialized_posts = json.dumps(meeting_posts, sort_keys=True)
                for forbidden in (
                    "meeting_id",
                    "participant_id",
                    "provider_connected",
                    "transcript_text",
                    "may_confirm_contract",
                    "may_freeze_contract",
                    "may_start_measurement",
                    "may_assign_verdict",
                ):
                    assert forbidden not in serialized_posts
                unexpected_errors = [
                    error
                    for error in page_errors
                    if not error.startswith(
                        "console: Failed to load resource:"
                    )
                ]
                assert unexpected_errors == []
            finally:
                context.close()
                browser.close()


@pytest.mark.skipif(
    os.environ.get("EXITSPEC_BROWSER_E2E") != "1",
    reason="set EXITSPEC_BROWSER_E2E=1 to run the Chromium lifecycle test",
)
def test_guided_meeting_session_fails_closed_to_paste_on_bad_disclosure(
    tmp_path,
):
    from playwright import sync_api

    expect = sync_api.expect

    with _running_server(tmp_path) as base_url:
        with sync_api.sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(
                viewport={"width": 1280, "height": 720}
            )
            page = context.new_page()
            page_errors = _capture_browser_errors(page)
            page.set_default_timeout(10_000)
            page.set_default_navigation_timeout(10_000)
            raw_marker = "unsafe-disclosure-marker-must-never-render"
            disclosure_route = re.compile(
                rf"^{re.escape(base_url)}/api/pocs/"
                r"poc_[a-z0-9][a-z0-9_-]{2,63}/"
                r"meeting-sessions/disclosure$"
            )

            def corrupt_disclosure(route, request) -> None:
                del request
                response = route.fetch()
                payload = response.json()
                payload["provider_connected"] = True
                payload["raw_provider_message"] = raw_marker
                route.fulfill(
                    status=response.status,
                    content_type="application/json",
                    body=json.dumps(payload),
                )

            page.route(disclosure_route, corrupt_disclosure)

            try:
                _create_browser_meeting_poc(
                    expect,
                    page,
                    base_url,
                    "Browser fail-closed meeting POC",
                )
                expect(
                    page.locator("#meeting-session-state-badge")
                ).to_have_text("Unavailable")
                expect(
                    page.locator("#meeting-session-disclosure")
                ).to_contain_text("could not be validated")
                expect(page.locator("#meeting-mode-session")).to_be_disabled()
                expect(page.locator("#capture-source")).to_be_disabled()
                expect(page.locator("body")).not_to_contain_text(raw_marker)

                expect(page.locator("#meeting-mode-paste")).to_be_enabled()
                page.locator("#meeting-mode-paste").check()
                expect(page.locator("#meeting-paste-panel")).to_be_visible()
                expect(page.locator("#meeting-transcript")).to_be_enabled()
                _assert_bounded_employee_shell(page)
                assert page_errors == []
            finally:
                context.close()
                browser.close()
