"""Observable UI bridge contract; real local owners, synthetic fixtures only."""

from __future__ import annotations

import json
import threading

import pytest

from tests import test_source_authoring_browser as journey
from tests import test_source_authoring_web as web

rig = web.rig
pytestmark = journey.pytestmark
POC = web.POC
HEADERS = {"Content-Type": "application/json", "Cache-Control": "no-store"}
REASONS = [
    "live_worker_and_operator_launcher",
    "owner_launch_approval",
    "model_schema_token_and_billing_proof",
    "account_pricing_and_custody_approval",
]
FALLBACK = "Enter the reviewer and rationale, then choose an available decision."


def source_url(rig):
    return f"http://127.0.0.1:{rig.server.server_port}/app/pocs/{POC}/source-authoring"



def _open_review_editor(page):
    from playwright.sync_api import expect

    if page.locator("#review-editor").is_hidden():
        page.locator("#review-start").click()
    expect(page.locator("#review-editor")).to_be_visible()

def test_bootstrap_copy_is_neutral_until_exact_known_reasons_validate(rig):
    from playwright.sync_api import expect, sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        held = []
        page.route("**/source-authoring/bootstrap", lambda route: held.append(route))
        try:
            page.goto(source_url(rig))
            expect(page.locator("#mode-heading")).to_have_text("Checking authoring mode…")
            expect(page.locator("#source-live-missing")).to_have_text("")
            expect(page.locator("#source-mode-copy")).to_contain_text("until this page validates")
            expect(page.locator("#source-authoring-main")).to_have_attribute("data-authoring-state", "unverified")
            expect(page.locator("#source-run")).to_be_disabled()
            assert held and rig.runtime._browsers == []
            response = held[0].fetch()
            payload = response.json()
            payload["live_missing"] = list(reversed(REASONS))
            held[0].fulfill(status=200, headers=HEADERS, body=json.dumps(payload))
            expect(page.locator("#source-choice")).to_be_enabled()
            expect(page.locator("#mode-heading")).to_contain_text("no provider connection")
            expect(page.locator("#source-mode-copy")).to_contain_text("no external inference call")
            expect(page.locator("#source-live-missing")).to_contain_text("no regional guarantee")
            assert all(reason not in page.locator("#source-live-missing").inner_text() for reason in REASONS)
            expect(page.locator("#source-authoring-main")).to_have_attribute("data-authoring-state", "select")
            assert rig.runtime.operations.ledger[0] == 0
        finally:
            browser.close()


@pytest.mark.parametrize("fault", ["unknown", "duplicate", "missing", "mode", "live", "failure"])
def test_untrusted_bootstrap_mode_or_reasons_stays_unavailable(rig, fault):
    from playwright.sync_api import expect, sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()

        def untrusted(route):
            if fault == "failure":
                route.fulfill(status=403, headers=HEADERS, body='{"code":"CAPABILITY_REFUSED"}')
                return
            payload = route.fetch().json()
            if fault == "unknown":
                payload["live_missing"][3] = "UNTRUSTED_REASON_SENTINEL"
            elif fault == "duplicate":
                payload["live_missing"][3] = payload["live_missing"][0]
            elif fault == "missing":
                payload["live_missing"].pop()
            elif fault == "mode":
                payload["mode"] = "UNTRUSTED_MODE_SENTINEL"
            else:
                payload["live_enabled"] = True
            route.fulfill(status=200, headers=HEADERS, body=json.dumps(payload))

        page.route("**/source-authoring/bootstrap", untrusted)
        try:
            page.goto(source_url(rig))
            expect(page.locator("#source-authoring-error")).to_be_visible()
            expect(page.locator("#mode-heading")).to_have_text("Authoring mode unavailable")
            expect(page.locator("#source-live-missing")).to_have_text("")
            expect(page.locator("#source-mode-copy")).to_contain_text("could not be validated")
            expect(page.locator("#source-authoring-main")).to_have_attribute("data-authoring-state", "unavailable")
            for control in ("source-choice", "source-preview", "source-authorize", "source-run", "source-cancel"):
                expect(page.locator(f"#{control}")).to_be_disabled()
            for description in ("source-selection-reason", "source-ack-reason", "source-run-reason"):
                expect(page.locator(f"#{description}")).to_contain_text("Reload to validate")
            assert "UNTRUSTED_REASON_SENTINEL" not in page.content()
            assert "UNTRUSTED_MODE_SENTINEL" not in page.content()
            assert rig.runtime.operations.ledger[0] == 0 and rig.runtime._thread is None
        finally:
            browser.close()


def test_descriptions_track_consent_without_repeat_announcements_and_reset(rig):
    from playwright.sync_api import expect, sync_playwright

    entered, release = threading.Event(), threading.Event()
    rig.runtime.operations._schedule = lambda phase: (
        (entered.set(), release.wait(5)) if phase == "pre_dispatch" else None
    )
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            journey.open_page(page, rig)
            expect(page.locator("#source-choice")).to_have_attribute("aria-describedby", "source-selection-reason")
            expect(page.locator("#source-selection-reason")).to_contain_text("Inspect the selected source")
            page.locator("#source-preview").click()
            expect(page.locator("#source-disclosure")).to_be_visible()
            expect(page.locator("#source-authoring-main")).to_have_attribute("data-authoring-state", "inspect")
            expect(page.locator("#source-selection-reason")).to_contain_text("locks source selection")
            expect(page.locator("#source-ack-reason")).to_contain_text("Both attestations")
            page.locator("#source-business-text").check()
            expect(page.locator("#source-authorize")).to_be_disabled()
            expect(page.locator("#source-ack-reason")).to_have_text("Acknowledge the purpose, limits, data handling and expiry.")
            page.locator("#source-acknowledged").check()
            expect(page.locator("#source-authorize")).to_be_enabled()
            expect(page.locator("#source-ack-reason")).to_contain_text("Run is separate")
            page.locator("#source-authorize").click()
            expect(page.locator("#source-run")).to_be_enabled()
            expect(page.locator("#source-run")).to_have_attribute("aria-describedby", "source-run-reason")
            expect(page.locator("#source-authoring-main")).to_have_attribute("data-authoring-state", "acknowledged")
            expect(page.locator("#source-run-reason")).to_contain_text("No provider call or spend")
            page.evaluate("""() => {
              window.bridgeAnnouncements = 0;
              const observer = new MutationObserver(records => { window.bridgeAnnouncements += records.length; });
              for (const id of ['source-selection-reason', 'source-ack-reason', 'source-run-reason', 'source-status']) {
                observer.observe(document.getElementById(id), {childList: true, characterData: true, subtree: true});
              }
              window.bridgeObserver = observer;
            }""")
            for _ in range(2):
                with page.expect_response("**/source-authoring/status"):
                    pass
            assert page.evaluate("window.bridgeAnnouncements") == 0
            page.evaluate("window.bridgeObserver.disconnect()")
            page.locator("#source-run").click()
            assert entered.wait(2)
            expect(page.locator("#source-authoring-main")).to_have_attribute("data-authoring-state", "processing")
            expect(page.locator("#source-run-reason")).to_contain_text("cancel remains available")
            expect(page.locator("#source-cancel")).to_be_enabled()
            page.locator("#source-cancel").click()
            expect(page.locator("#source-authoring-main")).to_have_attribute("data-authoring-state", "terminal")
            expect(page.locator("#source-run-reason")).to_contain_text("no longer executable")
            release.set()
            page.route("**/source-authoring/bootstrap", lambda route: route.fulfill(status=403, headers=HEADERS, body='{"code":"CAPABILITY_REFUSED"}'))
            page.evaluate("window.dispatchEvent(new PageTransitionEvent('pagehide'))")
            expect(page.locator("#source-authoring-main")).to_have_attribute("data-authoring-state", "unverified")
            expect(page.locator("#source-live-missing")).to_have_text("")
            expect(page.locator("#source-run-reason")).to_have_text("Checking the page session and source list.")
            page.evaluate("window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted:true}))")
            expect(page.locator("#source-authoring-main")).to_have_attribute("data-authoring-state", "unavailable")
            expect(page.locator("#source-redacted-text")).to_have_text("")
            expect(page.locator("#source-business-text")).not_to_be_checked()
            expect(page.locator("#source-run")).to_be_disabled()
        finally:
            release.set()
            browser.close()


def test_pending_run_keeps_cancellation_and_its_description_available(rig):
    from playwright.sync_api import expect, sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            journey.open_page(page, rig)
            journey.preview_and_acknowledge(page)
            page.evaluate("""() => {
              const fetch = window.fetch;
              window.fetch = (url, options) => url.endsWith('/run') ?
                new Promise((resolve, reject) => { window.releaseRun = () => fetch(url, options).then(resolve, reject); }) :
                fetch(url, options);
            }""")
            page.locator("#source-run").click()
            page.wait_for_function("typeof window.releaseRun === 'function'")
            expect(page.locator("#source-authoring-main")).to_have_attribute("data-authoring-state", "starting")
            expect(page.locator("#source-run")).to_be_disabled()
            expect(page.locator("#source-cancel")).to_be_enabled()
            expect(page.locator("#source-run-reason")).to_contain_text("cancel remains available")
            page.locator("#source-cancel").click()
            expect(page.locator("#source-authoring-main")).to_have_attribute("data-authoring-state", "terminal")
            page.evaluate("window.releaseRun()")
            expect(page.locator("#source-task")).to_have_attribute("aria-busy", "false")
            expect(page.locator("#source-run-reason")).to_contain_text("no longer executable")
            expect(page.locator("#source-cancel")).to_be_disabled()
            assert rig.runtime.operations.ledger[0] == 0
        finally:
            browser.close()


@pytest.mark.parametrize("kind", ["a2", "a3", "mixed"])
def test_review_copy_tracks_current_a2_a3_and_mixed_proposals(rig, kind):
    from playwright.sync_api import expect, sync_playwright

    if kind != "a2":
        capability = web.bootstrap(rig)
        operation = web.prepare(rig, capability)["operation_id"]
        web.authorize(rig, capability, operation)
        web.call(rig, "run", {"operation_id": operation}, capability=capability)
        assert web.wait(rig, capability, operation)["state"] == "SUCCEEDED"
    if kind != "a3":
        rig.server.poc_source_intake.capture_source(
            poc_id=POC,
            source=web.POCSourceInput(source_kind=web.SourceKind.DOCUMENT, content="The budget must stay below 95 dollars."),
            idempotency_key="a2-unsupported",
        )
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        assisted_reads = []
        page.on("request", lambda request: assisted_reads.append((request.method, request.url)) if "/assisted-authoring" in request.url else None)
        try:
            page.goto(f"http://127.0.0.1:{rig.server.server_port}/app/pocs/{POC}/review")
            expect(page.locator("#reviewer")).to_be_enabled()
            expect(page.locator("#decision-status")).to_have_text(FALLBACK)
            expect(page.locator("#proposal-decision-form .review-action-line")).to_contain_text("acceptance drafting")
            assert "TTFT" not in page.locator("#proposal-decision-form .review-action-line").inner_text()
            if kind == "a2":
                expect(page.locator("#proposal-support")).to_contain_text("one TTFT and one error-rate claim")
            else:
                expect(page.locator("#proposal-support")).to_contain_text("Source-bound proposal material")
                assert "evaluator" not in page.locator("#proposal-support").inner_text()
            _open_review_editor(page)
            page.locator("#reviewer").fill("named.reviewer")
            page.locator("#rationale").fill("Retain this synthetic requirement for drafting.")
            expect(page.locator("#keep-proposal")).to_be_enabled()
            expect(page.locator("#keep-proposal")).to_have_text("Keep for contract")
            expect(page.locator("#keep-proposal")).to_have_attribute("value", "KEEP_FOR_CONTRACT")
            if kind != "a2":
                expect(page.locator("#decision-status")).to_contain_text("A3 source-bound proposal only")
            if rig.kind == "main":
                expect(page.locator("#assisted-authoring-link")).to_be_hidden()
            if kind != "a3":
                page.locator("#keep-proposal").click()
                expect(page.locator("#source-quote")).to_contain_text("95 dollars")
                expect(page.locator("#decision-status")).to_have_text(FALLBACK)
                expect(page.locator("#proposal-support")).to_contain_text("one TTFT and one error-rate claim")
                _open_review_editor(page)
                page.locator("#reviewer").fill("named.reviewer")
                page.locator("#rationale").fill("Leave this unsupported synthetic claim unproven.")
                expect(page.locator("#keep-proposal")).to_be_disabled()
                expect(page.locator("#discard-proposal")).to_be_enabled()
                expect(page.locator("#decision-status")).to_contain_text("current evaluator cannot execute")
                assert "A3 source-bound" not in page.locator("#decision-status").inner_text()
                page.locator("#discard-proposal").click()
            else:
                page.locator("#keep-proposal").click()
            expect(page.locator("#review-complete")).to_be_visible()
            page.reload()
            expect(page.locator("#review-complete")).to_be_visible()
            expect(page.locator("#review-complete-summary")).to_contain_text("No contract was created or approved.")
            rows = rig.server.proposal_review_service.list_proposals(POC)
            assert all(row.decision and row.decision.reviewer == "named.reviewer" for row in rows)
            if kind != "a2":
                expect(page.locator("#define-criteria")).to_have_attribute("href", "/app")
            if kind == "a2" or rig.kind == "main":
                expect(page.locator("#plan-capabilities")).to_be_hidden()
            else:
                expect(page.locator("#plan-capabilities")).to_be_visible()
            assert page.url.endswith(f"/app/pocs/{POC}/review")
            if rig.kind == "main" and kind == "a2":
                assert set(assisted_reads) == {
                    ("GET", f"http://127.0.0.1:{rig.server.server_port}/api/pocs/{POC}/assisted-authoring"),
                    ("GET", f"http://127.0.0.1:{rig.server.server_port}/api/pocs/{POC}/assisted-authoring/current-review"),
                }
        finally:
            browser.close()


@pytest.mark.parametrize("rig", ["main"], indirect=True)
@pytest.mark.parametrize("fault", ["missing", "version", "promoted", "extra", "read_disabled"])
def test_main_review_requires_exact_read_capability_before_any_classification(rig, fault):
    from playwright.sync_api import expect, sync_playwright

    web.publish_for_review(rig)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()

        def corrupt(route):
            value = route.fetch().json()
            if fault == "missing":
                value.pop("source_authoring_review")
            elif fault == "version":
                value["source_authoring_review"]["schema_version"] += "-unknown"
            elif fault == "promoted":
                value["source_authoring_review"]["authoring"] = True
            elif fault == "extra":
                value["source_authoring_review"]["may_execute"] = True
            else:
                value["source_authoring_review"]["receipts"] = False
            route.fulfill(status=200, headers=HEADERS, body=json.dumps(value))

        page.route("**/api/state", corrupt)
        try:
            page.goto(f"http://127.0.0.1:{rig.server.server_port}/app/pocs/{POC}/review")
            expect(page.locator("#proposal-review-error")).to_be_visible()
            expect(page.locator("#keep-proposal")).to_be_disabled()
            expect(page.locator("#discard-proposal")).to_be_disabled()
            expect(page.locator("#reviewer")).to_be_disabled()
            expect(page.locator("#assisted-authoring-link")).to_be_hidden()
            assert "Executable candidate" not in page.locator("#proposal-support").inner_text()
            assert all(row.decision is None for row in rig.server.proposal_review_service.list_proposals(POC))
        finally:
            browser.close()


@pytest.mark.parametrize("fault", [
    "missing", "version", "missing_row", "duplicate", "foreign_poc", "origin",
    "contradiction", "extra", "container", "overbound", "stale_id",
])
def test_queue_provenance_manifest_is_exact_bounded_and_agrees_with_a3(rig, fault):
    from playwright.sync_api import expect, sync_playwright

    web.publish_for_review(rig)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()

        def corrupt(route):
            value = route.fetch().json()
            manifest = value["authoring_provenance"]
            if fault == "missing":
                value.pop("authoring_provenance")
            elif fault == "version":
                manifest["schema_version"] += "-unknown"
            elif fault == "missing_row":
                manifest["proposals"] = []
            elif fault == "duplicate":
                manifest["proposals"] *= 2
            elif fault == "foreign_poc":
                value["poc_id"] = "poc_another_customer"
            elif fault == "origin":
                manifest["proposals"][0]["origin"] = "EXECUTABLE"
            elif fault == "contradiction":
                manifest["proposals"][0]["origin"] = "INTAKE_A2"
            elif fault == "extra":
                manifest["may_execute"] = True
            elif fault == "container":
                manifest["proposals"] = {}
            elif fault == "overbound":
                manifest["proposals"] *= 1025
            else:
                manifest["proposals"][0]["proposal_id"] = "prop_" + "b" * 32
            route.fulfill(status=200, headers=HEADERS, body=json.dumps(value))

        page.route(f"**/api/pocs/{POC}/proposals", corrupt)
        try:
            page.goto(f"http://127.0.0.1:{rig.server.server_port}/app/pocs/{POC}/review")
            expect(page.locator("#proposal-review-error")).to_be_visible()
            expect(page.locator("#keep-proposal")).to_be_disabled()
            expect(page.locator("#discard-proposal")).to_be_disabled()
            expect(page.locator("#reviewer")).to_be_disabled()
            assert "Executable candidate" not in page.locator("#proposal-support").inner_text()
            assert all(row.decision is None for row in rig.server.proposal_review_service.list_proposals(POC))
        finally:
            browser.close()


def test_empty_queue_has_valid_empty_manifest_and_no_executable_navigation(rig):
    from playwright.sync_api import expect, sync_playwright

    empty = "poc_empty_provenance"
    rig.server.draft_poc_service.create(web.DraftPOCCreateRequest(
        poc_id=empty, display_name="Empty provenance", customer_label="Synthetic",
        use_case="Review an empty queue", owner="local_operator", first_source_choice=web.FirstSourceChoice.DOCUMENT,
    ), idempotency_key="empty-provenance")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            page.goto(f"http://127.0.0.1:{rig.server.server_port}/app/pocs/{empty}/review")
            expect(page.locator("#review-complete")).to_be_visible()
            expect(page.locator("#review-complete-summary")).to_contain_text("There are no source proposals")
            expect(page.locator("#plan-capabilities")).to_be_hidden()
            expect(page.locator("#define-criteria")).to_have_attribute("href", f"/app/pocs/{empty}/sources/new")
            assert rig.runtime.operations.ledger[0] == 0 and len(rig.server.proposal_review_service) == 0
        finally:
            browser.close()


@pytest.mark.parametrize("rig", ["main"], indirect=True)
@pytest.mark.parametrize("historical_origin", ["INTAKE_A2", "ASSISTED_A3"])
def test_main_current_agreement_scope_excludes_prior_a3_from_slots_and_navigation(rig, monkeypatch, historical_origin):
    from playwright.sync_api import expect, sync_playwright

    if historical_origin == "ASSISTED_A3":
        web.publish_for_review(rig)
    historical, = rig.server.proposal_review_service.list_proposals(POC)
    rig.server.proposal_review_service.decide(
        POC, historical.proposal_id, web.ProposalDecision.KEEP_FOR_CONTRACT,
        "named.previous", "Retain the previous agreement source material.", "previous-agreement-keep",
    )
    rig.server.poc_source_intake.capture_source(
        poc_id=POC, source=web.POCSourceInput(source_kind=web.SourceKind.DOCUMENT, content=(
            "The error rate must remain below 0.5%.\nTTFT must remain below 500 ms."
        )), idempotency_key="current-agreement-a2",
    )
    lifecycle = rig.server.performance_lifecycle_service
    original = type(lifecycle).current_proposals

    def selected_scope(self, poc_id):
        return tuple(item for item in original(self, poc_id) if item.proposal_id != historical.proposal_id)

    monkeypatch.setattr(type(lifecycle), "current_proposals", selected_scope)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            page.goto(f"http://127.0.0.1:{rig.server.server_port}/app/pocs/{POC}/review")
            for index in range(2):
                expect(page.locator("#decision-status")).to_have_text(FALLBACK)
                expect(page.locator("#proposal-support")).to_contain_text("Executable candidate")
                _open_review_editor(page)
                page.locator("#reviewer").fill("named.current")
                page.locator("#rationale").fill("Keep this current agreement requirement.")
                expect(page.locator("#keep-proposal")).to_be_enabled()
                page.locator("#keep-proposal").click()
                if index == 0:
                    expect(page.locator("#source-quote")).to_contain_text("500 ms")
                    page.reload()
            page.wait_for_url(f"**/app/pocs/{POC}/define")
            rows = rig.server.proposal_review_service.list_proposals(POC)
            assert rows[0].decision.reviewer == "named.previous"
            assert all(row.decision.reviewer == "named.current" for row in rows[1:])
        finally:
            browser.close()


@pytest.mark.parametrize("rig", ["main"], indirect=True)
def test_main_a2_page_loads_new_a3_provenance_during_reconciliation(rig):
    from playwright.sync_api import expect, sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            page.goto(f"http://127.0.0.1:{rig.server.server_port}/app/pocs/{POC}/review")
            expect(page.locator("#proposal-support")).to_contain_text("Executable candidate")
            rig.receipt = rig.server.poc_source_intake.capture_source(
                poc_id=POC, source=web.POCSourceInput(source_kind=web.SourceKind.DOCUMENT, content="TTFT must remain below 500 ms."),
                idempotency_key="new-a3-during-review",
            )
            web.publish_for_review(rig)
            _open_review_editor(page)
            page.locator("#reviewer").fill("named.a2-first")
            page.locator("#rationale").fill("Retain the original A2 requirement.")
            page.locator("#keep-proposal").click()
            expect(page.locator("#proposal-support")).to_contain_text("Source-bound proposal material")
            _open_review_editor(page)
            page.locator("#reviewer").fill("named.a3-next")
            page.locator("#rationale").fill("Retain the newly published A3 material.")
            page.locator("#keep-proposal").click()
            expect(page.locator("#review-complete")).to_be_visible()
            expect(page.locator("#define-criteria")).to_have_attribute("href", "/app")
            expect(page.locator("#plan-capabilities")).to_be_hidden()
            assert [row.decision.reviewer for row in rig.server.proposal_review_service.list_proposals(POC)] == ["named.a2-first", "named.a3-next"]
        finally:
            browser.close()


@pytest.mark.parametrize("fault", [
    "receipts_unavailable", "projection_unavailable", "missing_receipts", "missing_projections",
    "malformed_receipt", "receipt_binding", "stale_revision", "quote_binding", "duplicate_projection",
    "queue_binding", "review_overlay", "joint_omission",
])
def test_untrusted_a3_provenance_never_falls_back_to_numeric_a2_support(rig, fault):
    from playwright.sync_api import expect, sync_playwright

    web.publish_for_review(rig)
    before = rig.runtime.operations.ledger
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()

        def corrupt(route):
            if fault in ("receipts_unavailable", "projection_unavailable"):
                route.fulfill(status=503, headers=HEADERS, body='{"error":"Unavailable"}')
                return
            value = route.fetch().json()
            if fault == "joint_omission":
                value["receipts" if "receipts" in value else "proposals"] = []
            elif fault == "missing_receipts":
                value["receipts"] = []
            elif fault == "missing_projections":
                value["proposals"] = []
            elif fault == "malformed_receipt":
                value["receipts"][0]["may_execute"] = True
            elif fault == "receipt_binding":
                value["proposals"][0]["authoring_receipt_id"] = "arcp_" + "a" * 32
            elif fault == "stale_revision":
                value["proposals"][0]["source_revision"] += 1
            elif fault == "quote_binding":
                value["proposals"][0]["normalized_claim"] = "The error rate must be below 99%."
            elif fault == "duplicate_projection":
                value["proposals"].append(value["proposals"][0])
            elif fault == "queue_binding":
                value["proposals"][0]["proposal_id"] = "prop_" + "a" * 32
            else:
                value["proposals"][0]["review_state"] = "KEEP_FOR_CONTRACT"
            route.fulfill(status=200, headers=HEADERS, body=json.dumps(value))

        suffix = "assisted-authoring" if fault in ("receipts_unavailable", "missing_receipts", "malformed_receipt") else (
            "proposals" if fault == "queue_binding" else "assisted-authoring/current-review"
        )
        page.route(f"**/api/pocs/{POC}/{suffix}", corrupt)
        if fault == "joint_omission":
            page.route(f"**/api/pocs/{POC}/assisted-authoring", corrupt)
        try:
            page.goto(f"http://127.0.0.1:{rig.server.server_port}/app/pocs/{POC}/review")
            expect(page.locator("#proposal-review-error")).to_be_visible()
            expect(page.locator("#keep-proposal")).to_be_disabled()
            expect(page.locator("#discard-proposal")).to_be_disabled()
            expect(page.locator("#reviewer")).to_be_disabled()
            expect(page.locator("#plan-capabilities")).to_be_hidden()
            assert "Executable candidate" not in page.locator("#proposal-support").inner_text()
            assert rig.runtime.operations.ledger == before
            assert all(row.decision is None for row in rig.server.proposal_review_service.list_proposals(POC))
        finally:
            browser.close()


def test_mixed_queue_a3_keep_does_not_consume_a2_slots_or_enable_define(rig):
    from playwright.sync_api import expect, sync_playwright

    web.publish_for_review(rig)
    for index, content in enumerate((
        "The error rate must remain below 0.5%.", "TTFT must remain below 500 ms.",
        "The error rate must remain below 0.2%.",
    )):
        rig.server.poc_source_intake.capture_source(
            poc_id=POC, source=web.POCSourceInput(source_kind=web.SourceKind.DOCUMENT, content=content),
            idempotency_key=f"mixed-slot-{index}",
        )
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            page.goto(f"http://127.0.0.1:{rig.server.server_port}/app/pocs/{POC}/review")
            for index in range(3):
                expect(page.locator("#decision-status")).to_have_text(FALLBACK)
                _open_review_editor(page)
                page.locator("#reviewer").fill("named.mixed")
                page.locator("#rationale").fill("Retain this exact synthetic requirement for drafting.")
                expect(page.locator("#keep-proposal")).to_be_enabled()
                page.locator("#keep-proposal").click()
                if index == 0:
                    expect(page.locator("#source-quote")).to_contain_text("0.5%")
                    page.reload()
            expect(page.locator("#source-quote")).to_contain_text("0.2%")
            _open_review_editor(page)
            page.locator("#reviewer").fill("named.mixed")
            page.locator("#rationale").fill("Discard the duplicate A2 metric.")
            expect(page.locator("#keep-proposal")).to_be_disabled()
            expect(page.locator("#decision-status")).to_contain_text("already selected")
            page.locator("#discard-proposal").click()
            expect(page.locator("#review-complete")).to_be_visible()
            expect(page.locator("#review-complete-summary")).to_contain_text("3 retained")
            expect(page.locator("#define-criteria")).to_have_attribute("href", "/app")
            assert page.url.endswith("/review")
            if rig.kind == "main":
                expect(page.locator("#plan-capabilities")).to_be_hidden()
            rows = rig.server.proposal_review_service.list_proposals(POC)
            assert [row.review_state.value for row in rows] == ["KEEP_FOR_CONTRACT"] * 3 + ["DISCARD"]
            assert all(row.decision.reviewer == "named.mixed" for row in rows)
        finally:
            browser.close()


def test_two_retained_a3_proposals_use_only_available_completion_routes(rig):
    from playwright.sync_api import expect, sync_playwright

    rig.receipt = rig.server.poc_source_intake.capture_source(
        poc_id=POC,
        source=web.POCSourceInput(source_kind=web.SourceKind.DOCUMENT, content=(
            "The error rate must remain below 0.5%.\nTTFT must remain below 500 ms."
        )), idempotency_key="two-a3-proposals",
    )
    web.publish_for_review(rig)
    assert len(web.review_read(rig, "/current-review")[1]["proposals"]) == 2
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            page.goto(f"http://127.0.0.1:{rig.server.server_port}/app/pocs/{POC}/review")
            for decision in ("discard-proposal", "keep-proposal", "keep-proposal"):
                expect(page.locator("#decision-status")).to_have_text(FALLBACK)
                _open_review_editor(page)
                page.locator("#reviewer").fill("named.a3")
                page.locator("#rationale").fill("Make this named synthetic triage decision.")
                page.locator(f"#{decision}").click()
            expect(page.locator("#review-complete")).to_be_visible()
            expect(page.locator("#review-complete-summary")).to_contain_text("2 retained")
            expect(page.locator("#define-criteria")).to_have_attribute("href", "/app")
            assert page.url.endswith("/review")
            if rig.kind == "main":
                expect(page.locator("#plan-capabilities")).to_be_hidden()
                expect(page.locator("#assisted-authoring-link")).to_be_hidden()
            else:
                expect(page.locator("#plan-capabilities")).to_be_visible()
                page.locator("#plan-capabilities").click()
                assert page.url.endswith("/capability-plan")
        finally:
            browser.close()


def test_two_a3_keeps_leave_an_a2_slot_available_and_preserve_duplicate_rule(rig):
    from playwright.sync_api import expect, sync_playwright

    rig.receipt = rig.server.poc_source_intake.capture_source(
        poc_id=POC,
        source=web.POCSourceInput(source_kind=web.SourceKind.DOCUMENT, content=(
            "The error rate must remain below 0.5%.\nTTFT must remain below 500 ms."
        )), idempotency_key="two-a3-before-a2",
    )
    web.publish_for_review(rig)
    for index, threshold in enumerate(("0.3", "0.4")):
        rig.server.poc_source_intake.capture_source(
            poc_id=POC, source=web.POCSourceInput(source_kind=web.SourceKind.EMAIL, content=f"The error rate must remain below {threshold}%."),
            idempotency_key=f"after-two-a3-{index}",
        )
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            page.goto(f"http://127.0.0.1:{rig.server.server_port}/app/pocs/{POC}/review")
            for decision in ("discard-proposal", "keep-proposal", "keep-proposal"):
                expect(page.locator("#decision-status")).to_have_text(FALLBACK)
                _open_review_editor(page)
                page.locator("#reviewer").fill("named.two-a3")
                page.locator("#rationale").fill("Review the exact synthetic source material.")
                page.locator(f"#{decision}").click()
            expect(page.locator("#source-quote")).to_contain_text("0.3%")
            page.reload()
            expect(page.locator("#decision-status")).to_have_text(FALLBACK)
            _open_review_editor(page)
            page.locator("#reviewer").fill("named.two-a3")
            page.locator("#rationale").fill("Keep the first A2 error-rate requirement.")
            expect(page.locator("#keep-proposal")).to_be_enabled()
            page.locator("#keep-proposal").click()
            expect(page.locator("#source-quote")).to_contain_text("0.4%")
            _open_review_editor(page)
            page.locator("#reviewer").fill("named.two-a3")
            page.locator("#rationale").fill("Discard the duplicate A2 error-rate requirement.")
            expect(page.locator("#keep-proposal")).to_be_disabled()
            expect(page.locator("#decision-status")).to_contain_text("already selected")
            page.locator("#discard-proposal").click()
            expect(page.locator("#review-complete")).to_be_visible()
            rows = rig.server.proposal_review_service.list_proposals(POC)
            assert [row.review_state.value for row in rows] == ["DISCARD"] + ["KEEP_FOR_CONTRACT"] * 3 + ["DISCARD"]
            assert all(row.decision.reviewer == "named.two-a3" for row in rows)
        finally:
            browser.close()


@pytest.mark.parametrize("failure", ["unavailable", "joint_omission"])
def test_provenance_failure_after_named_decision_blocks_next_proposal(rig, failure):
    from playwright.sync_api import expect, sync_playwright

    web.publish_for_review(rig)
    rig.server.poc_source_intake.capture_source(
        poc_id=POC, source=web.POCSourceInput(source_kind=web.SourceKind.EMAIL, content="TTFT must remain below 500 ms."),
        idempotency_key="after-decision-a2",
    )
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            page.goto(f"http://127.0.0.1:{rig.server.server_port}/app/pocs/{POC}/review")
            expect(page.locator("#decision-status")).to_have_text(FALLBACK)
            if failure == "unavailable":
                page.route("**/assisted-authoring/current-review", lambda route: route.fulfill(status=503, headers=HEADERS, body='{"error":"Unavailable"}'))
            else:
                page.route("**/assisted-authoring/current-review", lambda route: route.fulfill(status=200, headers=HEADERS, body=json.dumps({"poc_id": POC, "proposals": []})))
                page.route("**/assisted-authoring", lambda route: route.fulfill(status=200, headers=HEADERS, body=json.dumps({"poc_id": POC, "receipts": []})))
            _open_review_editor(page)
            page.locator("#reviewer").fill("named.reconcile")
            page.locator("#rationale").fill("Retain this exact synthetic A3 material.")
            page.locator("#keep-proposal").click()
            expect(page.locator("#proposal-review-error")).to_contain_text("decision was recorded")
            expect(page.locator("#keep-proposal")).to_be_disabled()
            expect(page.locator("#discard-proposal")).to_be_disabled()
            expect(page.locator("#reviewer")).to_be_disabled()
            assert "Executable candidate" not in page.locator("#proposal-support").inner_text()
            rows = rig.server.proposal_review_service.list_proposals(POC)
            assert rows[0].decision.reviewer == "named.reconcile"
            assert rows[1].decision is None
        finally:
            browser.close()


def test_mixed_duplicate_a2_stays_disabled_after_reload(rig):
    from playwright.sync_api import expect, sync_playwright
    web.publish_for_review(rig)
    for index, threshold in enumerate(("0.3", "0.4")):
        rig.server.poc_source_intake.capture_source(
            poc_id=web.POC,
            source=web.POCSourceInput(
                source_kind=web.SourceKind.EMAIL,
                content=f"The error rate must remain below {threshold}%.",
            ),
            idempotency_key=f"independent-reload-duplicate-{index}",
        )
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            page.goto(f"http://127.0.0.1:{rig.server.server_port}/app/pocs/{web.POC}/review")
            for _ in range(2):
                expect(page.locator("#reviewer")).to_be_enabled()
                _open_review_editor(page)
                page.locator("#reviewer").fill("independent.reviewer")
                page.locator("#rationale").fill("Retain this synthetic requirement for drafting.")
                expect(page.locator("#keep-proposal")).to_be_enabled()
                page.locator("#keep-proposal").click()
            expect(page.locator("#source-quote")).to_contain_text("0.4%")
            _open_review_editor(page)
            page.locator("#reviewer").fill("independent.reviewer")
            page.locator("#rationale").fill("Inspect whether the duplicate rule survives reload.")
            expect(page.locator("#keep-proposal")).to_be_disabled()
            expect(page.locator("#decision-status")).to_contain_text("already selected")
            page.reload()
            expect(page.locator("#reviewer")).to_be_enabled()
            _open_review_editor(page)
            page.locator("#reviewer").fill("independent.reviewer")
            page.locator("#rationale").fill("Inspect whether the duplicate rule survives reload.")
            print({
                "composition": rig.kind,
                "after_reload_keep_disabled": page.locator("#keep-proposal").is_disabled(),
                "decision_status": page.locator("#decision-status").inner_text(),
                "prior_states": [row.review_state.value for row in rig.server.proposal_review_service.list_proposals(web.POC)],
            })
            expect(page.locator("#keep-proposal")).to_be_disabled(timeout=1000)
        finally:
            browser.close()


@pytest.mark.parametrize("malformation", [
    "array", "nested_array", "duplicate_array_string", "number", "boolean", "null", "newline",
])
def test_completed_a2_manifest_rejects_coerced_ids_before_review(rig, malformation):
    from playwright.sync_api import expect, sync_playwright
    rig.server.poc_source_intake.capture_source(
        poc_id=web.POC,
        source=web.POCSourceInput(
            source_kind=web.SourceKind.DOCUMENT,
            content="TTFT must remain below 500 ms.",
        ),
        idempotency_key="independent-manifest-ttft",
    )
    first, second = rig.server.proposal_review_service.list_proposals(web.POC)
    rig.server.proposal_review_service.decide(
        web.POC, first.proposal_id, web.ProposalDecision.KEEP_FOR_CONTRACT,
        "independent.previous", "Keep the first synthetic A2 requirement.",
        "independent-manifest-first-keep",
    )
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()

        def corrupt(route):
            payload = route.fetch().json()
            row = payload["authoring_provenance"]["proposals"][0]
            row["proposal_id"] = {
                "array": [first.proposal_id],
                "nested_array": [[first.proposal_id]],
                "duplicate_array_string": [second.proposal_id],
                "number": 123,
                "boolean": True,
                "null": None,
                "newline": first.proposal_id + "\n",
            }[malformation]
            route.fulfill(status=200, headers={
                "Content-Type": "application/json", "Cache-Control": "no-store",
            }, body=json.dumps(payload))

        page.route(f"**/api/pocs/{web.POC}/proposals", corrupt)
        try:
            page.goto(f"http://127.0.0.1:{rig.server.server_port}/app/pocs/{web.POC}/review")
            page.wait_for_function("""() => !document.getElementById('reviewer').disabled ||
                !document.getElementById('proposal-review-error').hidden""")
            accepted = page.locator("#reviewer").is_enabled()
            if accepted:
                _open_review_editor(page)
                page.locator("#reviewer").fill("independent.coercion")
                page.locator("#rationale").fill("Check whether malformed provenance permits a named decision.")
                expect(page.locator("#keep-proposal")).to_be_enabled()
                page.locator("#keep-proposal").click()
                page.wait_for_url(f"**/app/pocs/{web.POC}/define")
                rows = rig.server.proposal_review_service.list_proposals(web.POC)
                assert rows[1].decision.reviewer == "independent.coercion"
                print({
                    "composition": rig.kind, "malformation": malformation,
                    "named_keep_recorded": True, "destination": page.url,
                    "manifest_kept_id": first.proposal_id,
                    "manifest_pending_id": second.proposal_id,
                })
            else:
                expect(page.locator("#keep-proposal")).to_be_disabled()
                expect(page.locator("#proposal-review-error")).to_be_visible()
            assert not accepted, f"{malformation}: malformed completed A2 origin ID allowed review, a named Keep, and /define navigation"
        finally:
            browser.close()


@pytest.mark.parametrize("representation", ["array", "string"])
def test_completed_a2_manifest_rejects_duplicate_equivalent_array_ids(rig, representation):
    from playwright.sync_api import expect, sync_playwright
    rig.server.poc_source_intake.capture_source(
        poc_id=web.POC,
        source=web.POCSourceInput(
            source_kind=web.SourceKind.DOCUMENT,
            content="TTFT must remain below 500 ms.",
        ),
        idempotency_key="independent-completed-ttft",
    )
    rows = rig.server.proposal_review_service.list_proposals(web.POC)
    for index, row in enumerate(rows):
        rig.server.proposal_review_service.decide(
            web.POC, row.proposal_id, web.ProposalDecision.KEEP_FOR_CONTRACT,
            "independent.previous", "Keep this synthetic A2 requirement.",
            f"independent-completed-keep-{index}",
        )
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()

        def corrupt(route):
            payload = route.fetch().json()
            for row in payload["authoring_provenance"]["proposals"]:
                row["proposal_id"] = [rows[0].proposal_id] if representation == "array" else rows[0].proposal_id
            route.fulfill(status=200, headers={
                "Content-Type": "application/json", "Cache-Control": "no-store",
            }, body=json.dumps(payload))

        page.route(f"**/api/pocs/{web.POC}/proposals", corrupt)
        try:
            page.goto(f"http://127.0.0.1:{rig.server.server_port}/app/pocs/{web.POC}/review")
            page.wait_for_function("""() => location.pathname.endsWith('/define') ||
                !document.getElementById('proposal-review-error').hidden""")
            assert not page.url.endswith("/define"), "Two distinct JSON arrays containing the same A2 ID bypassed uniqueness and navigated to /define"
            expect(page.locator("#proposal-review-error")).to_be_visible()
        finally:
            browser.close()


@pytest.mark.parametrize("scenario", ["duplicate", "distinct", "discarded"])
def test_a2_reload_rebuilds_actual_kept_metrics_and_slots(rig, scenario):
    from playwright.sync_api import expect, sync_playwright

    if scenario == "discarded":
        rig.server.poc_source_intake.capture_source(
            poc_id=POC, source=web.POCSourceInput(
                source_kind=web.SourceKind.EMAIL, content="Error rate must stay below 0.6%.",
            ), idempotency_key="reload-discarded-second",
        )
    content = "TTFT must remain below 500 ms." if scenario == "distinct" else "Error rate must stay below 0.3%."
    rig.server.poc_source_intake.capture_source(
        poc_id=POC, source=web.POCSourceInput(source_kind=web.SourceKind.DOCUMENT, content=content),
        idempotency_key="reload-current-a2",
    )
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        base_url = f"http://127.0.0.1:{rig.server.server_port}"
        review_url = f"{base_url}/app/pocs/{POC}/review"
        reads = []
        page.on("request", lambda request: reads.append((request.method, request.url))
                if "/assisted-authoring" in request.url else None)
        try:
            page.goto(review_url)
            for _ in range(2 if scenario == "discarded" else 1):
                expect(page.locator("#decision-status")).to_have_text(FALLBACK)
                _open_review_editor(page)
                page.locator("#reviewer").fill("named.reload")
                page.locator("#rationale").fill("Review this exact synthetic requirement before reload.")
                page.locator("#discard-proposal" if scenario == "discarded" else "#keep-proposal").click()
            expect(page.locator("#source-quote")).to_have_text(content)
            reads.clear()
            page.reload()
            expect(page.locator("#reviewer")).to_be_enabled()
            assert set(reads) == {
                ("GET", f"{base_url}/api/pocs/{POC}/assisted-authoring"),
                ("GET", f"{base_url}/api/pocs/{POC}/assisted-authoring/current-review"),
            }
            _open_review_editor(page)
            page.locator("#reviewer").fill("named.reload")
            page.locator("#rationale").fill("Complete this named synthetic decision after reload.")
            if scenario == "duplicate":
                expect(page.locator("#keep-proposal")).to_be_disabled()
                expect(page.locator("#decision-status")).to_contain_text("already selected")
                page.locator("#discard-proposal").click()
            else:
                expect(page.locator("#keep-proposal")).to_be_enabled()
                page.locator("#keep-proposal").click()
            if scenario == "distinct":
                page.wait_for_url(f"**/app/pocs/{POC}/define")
                page.goto(review_url)
                page.wait_for_url(f"**/app/pocs/{POC}/define")
            else:
                expect(page.locator("#review-complete")).to_be_visible()
                page.reload()
                expect(page.locator("#review-complete")).to_be_visible()
                expect(page.locator("#review-complete-summary")).to_contain_text("1 kept for contract authoring")
            rows = rig.server.proposal_review_service.list_proposals(POC)
            expected = {
                "duplicate": ["KEEP_FOR_CONTRACT", "DISCARD"],
                "distinct": ["KEEP_FOR_CONTRACT", "KEEP_FOR_CONTRACT"],
                "discarded": ["DISCARD", "DISCARD", "KEEP_FOR_CONTRACT"],
            }[scenario]
            assert [row.review_state.value for row in rows] == expected
            assert all(row.decision.reviewer == "named.reload" for row in rows)
        finally:
            browser.close()


@pytest.mark.parametrize("fault", [
    "missing_state", "state_array", "state_null", "state_unknown",
    "missing_claim", "claim_array", "claim_null", "claim_number", "claim_boolean",
    "claim_empty", "claim_whitespace", "claim_overbound", "claim_control", "extra_row_key",
    "pending_state", "pending_claim", "kept_count", "discarded_count", "needs_count",
    "a3_state", "a3_claim",
])
def test_manifest_state_claim_and_counts_must_agree_before_review(rig, fault):
    from playwright.sync_api import expect, sync_playwright

    web.publish_for_review(rig)
    for index, content in enumerate(("Error rate must stay below 0.4%.", "TTFT must remain below 500 ms.")):
        rig.server.poc_source_intake.capture_source(
            poc_id=POC, source=web.POCSourceInput(source_kind=web.SourceKind.DOCUMENT, content=content),
            idempotency_key=f"metadata-validation-{index}",
        )
    rows = rig.server.proposal_review_service.list_proposals(POC)
    for index, decision in enumerate((web.ProposalDecision.KEEP_FOR_CONTRACT, web.ProposalDecision.DISCARD)):
        rig.server.proposal_review_service.decide(
            POC, rows[index].proposal_id, decision, "named.metadata", "Review this synthetic source.",
            f"metadata-decision-{index}",
        )
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()

        def corrupt(route):
            payload = route.fetch().json()
            a3, completed, pending = payload["authoring_provenance"]["proposals"]
            summary = payload["review_summary"]
            if fault.startswith("missing_"):
                completed.pop("review_state" if fault == "missing_state" else "normalized_claim")
            elif fault.startswith("state_"):
                completed["review_state"] = {"state_array": ["DISCARD"], "state_null": None, "state_unknown": "APPROVED"}[fault]
            elif fault.startswith("claim_"):
                completed["normalized_claim"] = {
                    "claim_array": [completed["normalized_claim"]], "claim_null": None,
                    "claim_number": 7, "claim_boolean": True, "claim_empty": "", "claim_whitespace": " \n ",
                    "claim_overbound": "a" * 2001, "claim_control": "Error rate\u0001",
                }[fault]
            elif fault == "extra_row_key":
                completed["reviewer"] = "forbidden DTO expansion"
            elif fault == "pending_state":
                # Preserve aggregate counts so pending membership must detect the swap.
                completed["review_state"], pending["review_state"] = pending["review_state"], completed["review_state"]
            elif fault == "pending_claim":
                pending["normalized_claim"] = "Error rate must stay below 0.1%."
            elif fault == "kept_count":
                summary["kept_for_contract"], summary["discarded"] = 2, 0
            elif fault == "discarded_count":
                summary["kept_for_contract"], summary["discarded"] = 0, 2
            elif fault == "needs_count":
                summary["needs_review"], summary["discarded"] = 2, 0
            elif fault == "a3_state":
                # Counts and pending bindings still agree; independent A3 state must reject.
                a3["review_state"], completed["review_state"] = completed["review_state"], a3["review_state"]
            else:
                a3["normalized_claim"] = "TTFT must remain below 100 ms."
            route.fulfill(status=200, headers=HEADERS, body=json.dumps(payload))

        page.route(f"**/api/pocs/{POC}/proposals", corrupt)
        try:
            page.goto(f"http://127.0.0.1:{rig.server.server_port}/app/pocs/{POC}/review")
            expect(page.locator("#proposal-review-error")).to_be_visible()
            expect(page.locator("#reviewer")).to_be_disabled()
            expect(page.locator("#keep-proposal")).to_be_disabled()
            expect(page.locator("#discard-proposal")).to_be_disabled()
            assert rig.server.proposal_review_service.list_proposals(POC)[-1].decision is None
        finally:
            browser.close()


@pytest.mark.parametrize("origin", ["INTAKE_A2", "ASSISTED_A3"])
@pytest.mark.parametrize("fault", ["state", "claim", "count", "missing", "none"])
def test_completed_manifest_metadata_failure_blocks_decision_reconciliation(rig, origin, fault):
    from playwright.sync_api import expect, sync_playwright

    if origin == "ASSISTED_A3":
        web.publish_for_review(rig)
    next_claim = "Error rate must remain below 0.3%." if origin == "INTAKE_A2" else "TTFT must remain below 500 ms."
    rig.server.poc_source_intake.capture_source(
        poc_id=POC, source=web.POCSourceInput(source_kind=web.SourceKind.EMAIL, content=next_claim),
        idempotency_key="metadata-next-proposal",
    )
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            page.goto(f"http://127.0.0.1:{rig.server.server_port}/app/pocs/{POC}/review")
            expect(page.locator("#reviewer")).to_be_enabled()

            def corrupt(route):
                payload = route.fetch().json()
                completed = payload["authoring_provenance"]["proposals"][0]
                assert completed["review_state"] == "KEEP_FOR_CONTRACT"
                if fault == "claim":
                    completed["normalized_claim"] = "TTFT must remain below 100 ms."
                elif fault == "state":
                    completed["review_state"] = "DISCARD"
                    payload["review_summary"].update(kept_for_contract=0, discarded=1)
                elif fault == "count":
                    payload["review_summary"].update(kept_for_contract=0, discarded=1)
                elif fault == "missing":
                    payload["authoring_provenance"]["proposals"].pop(0)
                    payload["review_summary"].update(total=1, kept_for_contract=0)
                route.fulfill(status=200, headers=HEADERS, body=json.dumps(payload))

            page.route(f"**/api/pocs/{POC}/proposals", corrupt)
            _open_review_editor(page)
            page.locator("#reviewer").fill("named.metadata-refresh")
            page.locator("#rationale").fill("Retain this exact source material.")
            page.locator("#keep-proposal").click()
            if fault == "none":
                expect(page.locator("#source-quote")).to_have_text(next_claim)
                expect(page.locator("#reviewer")).to_be_enabled()
                _open_review_editor(page)
                page.locator("#reviewer").fill("named.next")
                page.locator("#rationale").fill("Inspect the next synthetic proposal after a genuine refresh.")
                if origin == "INTAKE_A2":
                    expect(page.locator("#keep-proposal")).to_be_disabled()
                    expect(page.locator("#decision-status")).to_contain_text("already selected")
                else:
                    expect(page.locator("#keep-proposal")).to_be_enabled()
                expect(page.locator("#discard-proposal")).to_be_enabled()
            else:
                expect(page.locator("#proposal-review-error")).to_contain_text("decision was recorded")
                expect(page.locator("#reviewer")).to_be_disabled()
                expect(page.locator("#keep-proposal")).to_be_disabled()
                expect(page.locator("#discard-proposal")).to_be_disabled()
            rows = rig.server.proposal_review_service.list_proposals(POC)
            assert rows[0].decision.reviewer == "named.metadata-refresh"
            assert rows[1].decision is None
        finally:
            browser.close()


@pytest.mark.parametrize("loaded", [False, True])
@pytest.mark.parametrize("prior_decision", ["KEEP_FOR_CONTRACT", "DISCARD"])
@pytest.mark.parametrize("fault", ["claim", "state", "missing", "none"])
def test_earlier_a2_decision_binding_survives_later_reconciliation(rig, loaded, prior_decision, fault):
    from playwright.sync_api import expect, sync_playwright

    next_claim = "Error rate must remain below 0.3%."
    for index, claim in enumerate(("TTFT must remain below 500 ms.", next_claim)):
        rig.server.poc_source_intake.capture_source(
            poc_id=POC, source=web.POCSourceInput(source_kind=web.SourceKind.EMAIL, content=claim),
            idempotency_key=f"earlier-decision-{index}",
        )
    first = rig.server.proposal_review_service.list_proposals(POC)[0]
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            page.goto(f"http://127.0.0.1:{rig.server.server_port}/app/pocs/{POC}/review")
            expect(page.locator("#reviewer")).to_be_enabled()
            _open_review_editor(page)
            page.locator("#reviewer").fill("named.earlier")
            page.locator("#rationale").fill("Review this first synthetic error-rate requirement.")
            page.locator("#keep-proposal" if prior_decision == "KEEP_FOR_CONTRACT" else "#discard-proposal").click()
            expect(page.locator("#source-quote")).to_have_text("TTFT must remain below 500 ms.")
            if loaded:
                page.reload()
            expect(page.locator("#reviewer")).to_be_enabled()

            def corrupt(route):
                payload = route.fetch().json()
                rows = payload["authoring_provenance"]["proposals"]
                prior = next(row for row in rows if row["proposal_id"] == first.proposal_id)
                assert prior["review_state"] == prior_decision
                assert payload["review_summary"]["needs_review"] == 1
                if fault == "claim":
                    prior["normalized_claim"] = "TTFT must remain below 100 ms."
                elif fault in ("state", "missing"):
                    old_count = "kept_for_contract" if prior_decision == "KEEP_FOR_CONTRACT" else "discarded"
                    payload["review_summary"][old_count] -= 1
                    if fault == "state":
                        prior["review_state"] = "DISCARD" if prior_decision == "KEEP_FOR_CONTRACT" else "KEEP_FOR_CONTRACT"
                        new_count = "discarded" if prior_decision == "KEEP_FOR_CONTRACT" else "kept_for_contract"
                        payload["review_summary"][new_count] += 1
                    else:
                        rows.remove(prior)
                        payload["review_summary"]["total"] -= 1
                route.fulfill(status=200, headers=HEADERS, body=json.dumps(payload))

            page.route(f"**/api/pocs/{POC}/proposals", corrupt)
            _open_review_editor(page)
            page.locator("#reviewer").fill("named.later")
            page.locator("#rationale").fill("Discard the intervening synthetic TTFT requirement.")
            page.locator("#discard-proposal").click()
            if fault == "none":
                expect(page.locator("#source-quote")).to_have_text(next_claim)
                expect(page.locator("#reviewer")).to_be_enabled()
                _open_review_editor(page)
                page.locator("#reviewer").fill("named.next")
                page.locator("#rationale").fill("Check current eligibility without forgetting earlier decisions.")
                if prior_decision == "KEEP_FOR_CONTRACT":
                    expect(page.locator("#keep-proposal")).to_be_disabled()
                    expect(page.locator("#decision-status")).to_contain_text("already selected")
                else:
                    expect(page.locator("#keep-proposal")).to_be_enabled()
                expect(page.locator("#discard-proposal")).to_be_enabled()
            else:
                expect(page.locator("#proposal-review-error")).to_contain_text("decision was recorded")
                expect(page.locator("#reviewer")).to_be_disabled()
                expect(page.locator("#keep-proposal")).to_be_disabled()
                expect(page.locator("#discard-proposal")).to_be_disabled()
            rows = rig.server.proposal_review_service.list_proposals(POC)
            assert [row.review_state.value for row in rows] == [prior_decision, "DISCARD", "NEEDS_REVIEW"]
            assert rows[0].decision.reviewer == "named.earlier"
            assert rows[1].decision.reviewer == "named.later"
            assert rows[2].decision is None
        finally:
            browser.close()
