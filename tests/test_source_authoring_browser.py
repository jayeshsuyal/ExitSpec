"""Mandatory Chromium source-consent journey, exclusively synthetic execution."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import pytest

from tests import test_source_authoring_web as web_tests

POC = web_tests.POC
rig = web_tests.rig

pytestmark = pytest.mark.skipif(
    os.environ.get("EXITSPEC_BROWSER_E2E") != "1",
    reason="source authoring Chromium coverage is mandatory under the v0.4 release gate",
)


def open_page(page, rig):
    from playwright.sync_api import expect

    page.goto(f"http://127.0.0.1:{rig.server.server_port}/app/pocs/{POC}/review")
    page.locator("#source-authoring-link").click()
    expect(page.locator("#source-choice")).to_be_enabled()
    page.locator("#source-choice").select_option(rig.receipt.source_receipt_id)
    expect(page.locator("#source-run")).to_be_disabled()


def preview_and_acknowledge(page):
    from playwright.sync_api import expect

    page.locator("#source-preview").click()
    expect(page.locator("#source-disclosure")).to_be_visible()
    expect(page.locator("#source-authorize")).to_be_disabled()
    page.locator("#source-business-text").check()
    expect(page.locator("#source-authorize")).to_be_disabled()
    page.locator("#source-acknowledged").check()
    page.locator("#source-authorize").click()
    expect(page.locator("#source-run")).to_be_enabled()


def assert_controls_do_not_overlap(page):
    boxes = []
    for button in page.locator("button").all():
        if button.is_visible():
            box = button.bounding_box()
            assert box["height"] >= 44
            assert (
                box["x"] >= 0
                and box["x"] + box["width"] <= page.viewport_size["width"] + 1
            )
            boxes.append(box)
    for index, first in enumerate(boxes):
        for second in boxes[index + 1 :]:
            assert (
                first["x"] + first["width"] <= second["x"]
                or second["x"] + second["width"] <= first["x"]
                or first["y"] + first["height"] <= second["y"]
                or second["y"] + second["height"] <= first["y"]
            )


def test_browser_explicit_source_consent_to_review_only_proposals(rig):
    from playwright.sync_api import expect, sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        errors, requests, capabilities = [], [], []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on(
            "request", lambda request: requests.append((request.url, request.headers))
        )
        page.on(
            "response",
            lambda response: (
                capabilities.append(response.json()["capability"])
                if response.url.endswith("/source-authoring/bootstrap")
                and response.status == 200
                else None
            ),
        )
        try:
            open_page(page, rig)
            expect(page.locator("#mode-heading")).to_contain_text(
                "no provider connection"
            )
            preview_and_acknowledge(page)
            expect(page.locator("#source-redacted-text")).not_to_contain_text(
                "alice@example.com"
            )
            assert rig.runtime.operations.ledger[0] == 0
            assert rig.runtime._thread is None
            assert_controls_do_not_overlap(page)
            screenshot_root = os.environ.get("EXITSPEC_SOURCE_AUTHORING_SCREENSHOTS")
            if screenshot_root and rig.kind == "main":
                Path(screenshot_root).mkdir(parents=True, exist_ok=True)
                page.evaluate("window.scrollTo(0, 0)")
                page.screenshot(
                    path=str(Path(screenshot_root) / "desktop-disclosure.png"),
                    full_page=True,
                )
            page.set_viewport_size({"width": 390, "height": 844})
            assert_controls_do_not_overlap(page)
            if screenshot_root and rig.kind == "main":
                page.evaluate("window.scrollTo(0, 0)")
                page.screenshot(
                    path=str(Path(screenshot_root) / "mobile-disclosure.png"),
                    full_page=True,
                )
            page.locator("#source-run").click()
            expect(page.locator("#source-review-result")).to_be_visible(timeout=6000)
            expect(page.locator("#source-status")).to_contain_text("NEEDS_REVIEW")
            assert rig.runtime.operations.ledger[0] == 1
            (capability,) = capabilities
            assert capability not in page.content()
            assert capability not in json.dumps(page.context.storage_state())
            assert capability not in page.evaluate("JSON.stringify(sessionStorage)")
            assert all(capability not in url for url, _ in requests)
            authoring = [
                (url, headers)
                for url, headers in requests
                if "/source-authoring/" in url
            ]
            assert all(
                headers.get("x-exitspec-authoring-capability") == capability
                for url, headers in authoring
                if not url.endswith("/bootstrap")
            )
            assert all(
                url.startswith(f"http://127.0.0.1:{rig.server.server_port}/")
                for url, _ in requests
            )
            page.locator("#source-review-result").click()
            expect(page.locator("#keep-proposal")).to_be_disabled()
            proposals = rig.server.proposal_review_service.list_proposals(POC)
            assert len(proposals) == 1 and proposals[0].decision is None
            assert proposals[0].review_state.value == "NEEDS_REVIEW"
            page.locator("#reviewer").fill("named.reviewer")
            page.locator("#rationale").fill(
                "Retain this exact synthetic source requirement."
            )
            page.locator("#keep-proposal").click()
            expect(page.locator("#review-complete")).to_be_visible()
            assert (
                rig.server.proposal_review_service.list_proposals(POC)[0].decision
                is not None
            )
            assert errors == []
        finally:
            browser.close()


def test_browser_refresh_and_back_restore_require_fresh_inspection_and_ack(rig):
    from playwright.sync_api import expect, sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            open_page(page, rig)
            preview_and_acknowledge(page)
            first = rig.runtime._browsers[0].secret
            page.reload()
            expect(page.locator("#source-choice")).to_be_enabled()
            assert rig.runtime._browsers[-1].secret != first
            expect(page.locator("#source-run")).to_be_disabled()
            expect(page.locator("#source-disclosure")).to_be_hidden()
            page.locator("#source-choice").select_option(rig.receipt.source_receipt_id)
            preview_and_acknowledge(page)
            second = rig.runtime._browsers[-1].secret
            page.locator("#back-to-review").click()
            page.go_back()
            expect(page.locator("#source-choice")).to_be_enabled()
            expect(page.locator("#source-run")).to_be_disabled()
            expect(page.locator("#source-disclosure")).to_be_hidden()
            assert rig.runtime._browsers[-1].secret != second
            # Also exercise the persisted-page restoration event deterministically.
            page.evaluate(
                "window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted:true}))"
            )
            expect(page.locator("#source-choice")).to_be_enabled()
            expect(page.locator("#source-run")).to_be_disabled()
            assert rig.runtime.operations.ledger[0] == 0 and rig.runtime._thread is None
        finally:
            browser.close()


@pytest.mark.parametrize("restoration", ["hidden", "fresh-page", "new-disclosure"])
def test_browser_deferred_digest_never_restores_superseded_source(rig, restoration):
    from playwright.sync_api import expect, sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            open_page(page, rig)
            page.evaluate("""() => {
              const digest = crypto.subtle.digest.bind(crypto.subtle);
              crypto.subtle.digest = async (...args) => {
                const result = await digest(...args);
                crypto.subtle.digest = digest;
                await new Promise(resolve => { window.releaseDigest = resolve; });
                return result;
              };
            }""")
            page.locator("#source-preview").click()
            page.wait_for_function("typeof window.releaseDigest === 'function'")
            page.evaluate("window.dispatchEvent(new PageTransitionEvent('pagehide'))")
            if restoration != "hidden":
                page.evaluate(
                    "window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted:true}))"
                )
                expect(page.locator("#source-choice")).to_be_enabled()
            expected = ""
            if restoration == "new-disclosure":
                receipt = rig.server.poc_source_intake.capture_source(
                    poc_id=POC,
                    source=web_tests.POCSourceInput(
                        source_kind=web_tests.SourceKind.DOCUMENT,
                        content="The budget must stay below 95 dollars.",
                    ),
                    idempotency_key="new-disclosure",
                )
                page.locator("#source-refresh").click()
                expect(page.locator("#source-choice")).to_be_enabled()
                page.locator("#source-choice").select_option(receipt.source_receipt_id)
                preview_and_acknowledge(page)
                expected = page.locator("#source-redacted-text").inner_text()
                assert "95 dollars" in expected
            page.evaluate("window.releaseDigest()")
            # Drain the resolved digest and its caller through the next task.
            page.evaluate("() => new Promise(resolve => setTimeout(resolve, 0))")
            expect(page.locator("#source-redacted-text")).to_have_text(expected)
            if restoration == "new-disclosure":
                expect(page.locator("#source-run")).to_be_enabled()
                expect(page.locator("#source-business-text")).to_be_checked()
                expect(page.locator("#source-acknowledged")).to_be_checked()
            else:
                expect(page.locator("#source-disclosure")).to_be_hidden()
                expect(page.locator("#source-run")).to_be_disabled()
                expect(page.locator("#source-authorize")).to_be_disabled()
                expect(page.locator("#source-business-text")).not_to_be_checked()
                expect(page.locator("#source-acknowledged")).not_to_be_checked()
            assert rig.runtime.operations.ledger[0] == 0 and rig.runtime._thread is None
        finally:
            browser.close()


def test_browser_delayed_status_failure_cannot_clear_new_operation(rig):
    from playwright.sync_api import expect, sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            open_page(page, rig)
            page.evaluate("""() => {
              const fetch = window.fetch;
              let held = false;
              window.fetch = (url, options) => {
                if (!held && url.endsWith('/status')) {
                  held = true;
                  return new Promise((resolve, reject) => { window.rejectStatus = reject; });
                }
                return fetch(url, options);
              };
            }""")
            preview_and_acknowledge(page)
            page.wait_for_function("typeof window.rejectStatus === 'function'")
            page.locator("#source-cancel").click()
            expect(page.locator("#source-status")).to_contain_text("Consent revoked")
            preview_and_acknowledge(page)
            expected = page.locator("#source-redacted-text").inner_text()
            page.evaluate("window.rejectStatus(new Error('superseded status'))")
            page.evaluate("() => new Promise(resolve => setTimeout(resolve, 0))")
            expect(page.locator("#source-redacted-text")).to_have_text(expected)
            expect(page.locator("#source-disclosure")).to_be_visible()
            expect(page.locator("#source-run")).to_be_enabled()
            expect(page.locator("#source-business-text")).to_be_checked()
            expect(page.locator("#source-acknowledged")).to_be_checked()
            expect(page.locator("#source-authoring-error")).to_be_hidden()
            assert rig.runtime.operations.ledger[0] == 0 and rig.runtime._thread is None
        finally:
            browser.close()


def test_browser_delayed_source_list_cannot_replace_restored_page_choices(rig):
    from playwright.sync_api import expect, sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.add_init_script("""(() => {
          const fetch = window.fetch;
          let held = false;
          window.fetch = async (url, options) => {
            const response = await fetch(url, options);
            if (!held && url.endsWith('/sources')) {
              held = true;
              const body = await response.text();
              const detached = new Response(body, {status: response.status, headers: response.headers});
              await new Promise(resolve => { window.releaseSources = resolve; });
              return detached;
            }
            return response;
          };
        })();""")
        try:
            page.goto(
                f"http://127.0.0.1:{rig.server.server_port}/app/pocs/{POC}/source-authoring"
            )
            page.wait_for_function("typeof window.releaseSources === 'function'")
            receipt = rig.server.poc_source_intake.capture_source(
                poc_id=POC,
                source=web_tests.POCSourceInput(
                    source_kind=web_tests.SourceKind.DOCUMENT,
                    content="The budget must stay below 95 dollars.",
                ),
                idempotency_key="new-list-source",
            )
            page.evaluate("""() => {
              window.dispatchEvent(new PageTransitionEvent('pagehide'));
              window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted:true}));
            }""")
            expect(page.locator("#source-choice")).to_be_enabled()
            page.locator("#source-choice").select_option(receipt.source_receipt_id)
            page.evaluate("window.releaseSources()")
            page.evaluate("() => new Promise(resolve => setTimeout(resolve, 0))")
            expect(page.locator("#source-choice")).to_have_value(receipt.source_receipt_id)
            expect(page.locator("#source-preview")).to_be_enabled()
            expect(page.locator("#source-run")).to_be_disabled()
            preview_and_acknowledge(page)
            expect(page.locator("#source-redacted-text")).to_contain_text("95 dollars")
            assert rig.runtime.operations.ledger[0] == 0 and rig.runtime._thread is None
        finally:
            browser.close()


def test_browser_midflight_cancel_and_changed_draft_clear_authority(rig):
    from playwright.sync_api import expect, sync_playwright

    entered, release = threading.Event(), threading.Event()
    rig.runtime.operations._schedule = lambda phase: (
        (entered.set(), release.wait(5)) if phase == "pre_dispatch" else None
    )
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            open_page(page, rig)
            preview_and_acknowledge(page)
            page.locator("#source-run").click()
            assert entered.wait(2)
            expect(page.locator("#source-cancel")).to_be_enabled()
            page.locator("#source-cancel").click()
            expect(page.locator("#source-status")).to_contain_text("Consent revoked")
            release.set()
            expect(page.locator("#source-run")).to_be_disabled()
            expect(page.locator("#source-disclosure")).to_be_hidden()
            assert rig.server.assisted_authoring_service._results_by_request == {}
            assert rig.runtime.operations.ledger[0] == 1
            page.locator("#source-preview").click()
            expect(page.locator("#source-disclosure")).to_be_visible()
            rig.server.draft_poc_service.archive(POC)
            expect(page.locator("#source-status")).to_contain_text(
                "state changed", timeout=5000
            )
            expect(page.locator("#source-redacted-text")).to_have_text("")
            expect(page.locator("#source-run")).to_be_disabled()
        finally:
            release.set()
            browser.close()


def test_browser_untrusted_preview_and_failed_status_never_enable_run(rig):
    from playwright.sync_api import expect, sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            open_page(page, rig)
            page.route(
                "**/source-authoring/prepare",
                lambda route: route.fulfill(
                    status=200,
                    headers={
                        "Content-Type": "application/json",
                        "Cache-Control": "no-store",
                    },
                    body=json.dumps(
                        {"redacted_text": "PRIVATE_RESPONSE_SENTINEL" + "x" * 262144}
                    ),
                ),
            )
            page.locator("#source-preview").click()
            expect(page.locator("#source-authoring-error")).to_be_visible()
            expect(page.locator("#source-run")).to_be_disabled()
            expect(page.locator("#source-disclosure")).to_be_hidden()
            assert "PRIVATE_RESPONSE_SENTINEL" not in page.content()
            assert rig.runtime.operations.ledger[0] == 0 and rig.runtime._thread is None
            page.unroute("**/source-authoring/prepare")
            preview_and_acknowledge(page)
            page.route(
                "**/source-authoring/status",
                lambda route: route.fulfill(
                    status=403,
                    headers={
                        "Content-Type": "application/json",
                        "Cache-Control": "no-store",
                    },
                    body='{"code":"CAPABILITY_REFUSED"}',
                ),
            )
            expect(page.locator("#source-status")).to_contain_text(
                "could not be verified"
            )
            expect(page.locator("#source-run")).to_be_disabled()
            expect(page.locator("#source-authorize")).to_be_disabled()
            expect(page.locator("#source-disclosure")).to_be_hidden()
            expect(page.locator("#source-redacted-text")).to_have_text("")
            assert rig.runtime.operations.ledger[0] == 0 and rig.runtime._thread is None
        finally:
            browser.close()


@pytest.mark.parametrize("rig", ["main"], indirect=True)
def test_browser_native_zoom_text_requires_separate_authoring_consent(rig):
    from playwright.sync_api import expect, sync_playwright

    from tests.test_meeting_session_web_transport import _create_draft
    from tests.test_zoom_live_browser import install_fake
    from tests.test_zoom_live_runtime import packet, settings

    children, launched = install_fake(rig.server)
    poc = _create_draft(rig.server)
    rig.server.zoom_live_runtime.pair(poc, settings())
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            page.goto(
                f"http://127.0.0.1:{rig.server.server_port}/app/pocs/{poc}/sources/new"
            )
            page.locator("#zoom-live-consent").check()
            page.locator("#zoom-live-start").click()
            assert launched.wait(2)
            child = children[-1]
            child.emit("offer")
            child.emit("listening")
            packet(
                child, data="Criterion: p95 latency must stay below 730 milliseconds."
            )
            expect(page.locator("#zoom-live-stop")).to_be_enabled()
            page.locator("#zoom-live-stop").click()
            child.emit("stop_ack")
            child.emit("drained")
            expect(page.locator("#zoom-live-process")).to_be_enabled()
            page.locator("#zoom-live-process").click()
            expect(page.locator("#zoom-live-review")).to_be_visible()
            page.locator("#zoom-live-review").click()
            page.locator("#source-authoring-link").click()
            expect(page.locator("#source-choice")).to_be_enabled()
            (receipt,) = rig.server.poc_source_intake.list_current_receipts(poc)
            page.locator("#source-choice").select_option(receipt.source_receipt_id)
            assert rig.runtime.operations.ledger[0] == 0
            expect(page.locator("#source-run")).to_be_disabled()
            preview_and_acknowledge(page)
            expect(page.locator("#source-redacted-text")).to_contain_text("730")
            assert rig.runtime.operations.ledger[0] == 0
            page.locator("#source-run").click()
            expect(page.locator("#source-review-result")).to_be_visible(timeout=6000)
            proposals = rig.server.proposal_review_service.list_proposals(poc)
            assert proposals and all(
                p.decision is None and p.review_state.value == "NEEDS_REVIEW"
                for p in proposals
            )
            assert rig.runtime.operations.ledger[0] == 1
        finally:
            browser.close()
