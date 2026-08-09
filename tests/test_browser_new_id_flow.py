from __future__ import annotations

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
                        _assert_bounded_employee_shell(employee_page)

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

                assert set(defined_claims) == set(reviewed_claims)

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
                ).to_have_text("100 measured attempts")
                expect(
                    customer_page.locator("#review-counting-reliability")
                ).to_contain_text("all 100 attempts")
                expect(
                    customer_page.locator("#review-counting-boundary")
                ).to_contain_text("NOT PROVEN")
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
                ).to_contain_text("100 attempts · 100 successful")
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
                    ).to_contain_text("100 attempts · 100 successful")
                    expect(
                        evidence_page.locator(".counting-copy")
                    ).to_contain_text("all 100 measured attempts")
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
