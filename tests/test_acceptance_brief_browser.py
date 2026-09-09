"""Observable acceptance-brief contracts against the actual main-server UI.

Only the text-boundary cases substitute a proposal GET payload. Decision cases
use real source intake and immutable review receipts. Lifecycle cases dispatch
the persisted page events explicitly; they do not claim native BFCache coverage.
"""

from __future__ import annotations

import json
import os
import re
from copy import deepcopy
from pathlib import Path
from time import monotonic
from types import SimpleNamespace

import pytest

from exitspec import web
from tests.test_browser_new_id_flow import (
    EMAIL_TEXT,
    _assert_review_control_reachable,
    _assert_review_document_flow,
)
from tests.test_poc_proposal_web_transport import (
    _create_draft,
    _request,
    _running_server,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("EXITSPEC_BROWSER_E2E") != "1",
    reason="set EXITSPEC_BROWSER_E2E=1 to run the Chromium acceptance-brief tests",
)


@pytest.fixture
def brief(tmp_path):
    from playwright.sync_api import sync_playwright

    source_root = Path(__file__).resolve().parents[1] / "src"
    assert Path(web.__file__).resolve().is_relative_to(source_root.resolve())
    with _running_server(tmp_path) as server, sync_playwright() as playwright:
        poc_id = _create_draft(server)
        code, captured, _ = _request(
            server,
            "POST",
            f"/api/pocs/{poc_id}/sources/email-text",
            payload={"email_text": EMAIL_TEXT, "idempotency_key": "brief-email"},
        )
        assert code == 201 and captured["proposal_count"] == 3
        api_path = f"/api/pocs/{poc_id}/proposals"
        code, initial, _ = _request(server, "GET", api_path, content_type=None, origin=None)
        assert code == 200 and len(initial["proposals"]) == 3
        base = f"http://127.0.0.1:{server.server_port}"
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        page = context.new_page()
        page.set_default_timeout(10_000)
        errors = []
        requests = []
        posts = []
        page.on("pageerror", lambda error: errors.append(str(error)))

        def record(request):
            requests.append((request.method, request.url))
            if request.method == "POST" and request.url.endswith("/decision"):
                posts.append((request.url, request.post_data))

        page.on("request", record)
        rig = SimpleNamespace(
            server=server, page=page, base=base, poc_id=poc_id,
            api_path=api_path, api=base + api_path,
            url=f"{base}/app/pocs/{poc_id}/review", initial=initial,
            posts=posts, requests=requests,
        )
        try:
            yield rig
            assert errors == []
            # Browsing, editing and source inspection must not execute any
            # downstream agreement, provider, freeze or performance operation.
            assert all(
                method in {"GET", "HEAD"} or url.endswith("/decision")
                for method, url in requests
            )
        finally:
            context.close()
            browser.close()


def _open(rig):
    from playwright.sync_api import expect

    rig.page.goto(rig.url)
    expect(rig.page.locator("#proposal-tabs .proposal-tab")).to_have_count(3)
    expect(rig.page.locator("#proposal-reference")).to_have_text(
        rig.initial["proposals"][0]["proposal_id"]
    )


def _select(rig, index):
    from playwright.sync_api import expect

    rig.page.locator("#proposal-tabs .proposal-tab").nth(index).click()
    expect(rig.page.locator("#proposal-tabs .proposal-tab").nth(index)).to_have_attribute(
        "aria-pressed", "true"
    )


def _edit(rig, reviewer, rationale):
    from playwright.sync_api import expect

    editor = rig.page.locator("#review-editor")
    if not editor.is_visible():
        rig.page.locator("#review-start").click()
    expect(editor).to_be_visible()
    rig.page.locator("#reviewer").fill(reviewer)
    rig.page.locator("#rationale").fill(rationale)


def _listed(rig):
    code, payload, _ = _request(
        rig.server, "GET", rig.api_path, content_type=None, origin=None
    )
    assert code == 200
    return payload


def _wait_held(page, held, count=1):
    deadline = monotonic() + 10
    while len(held) < count and monotonic() < deadline:
        page.wait_for_timeout(10)
    assert len(held) >= count, "Expected intercepted request did not arrive"


def _fulfill(route, payload, status=200):
    route.fulfill(status=status, content_type="application/json", body=json.dumps(payload))


def _assert_no_selection_change(rig, selected_id):
    from playwright.sync_api import expect

    first_id = rig.initial["proposals"][0]["proposal_id"]
    rig.page.locator("#proposal-tabs .proposal-tab").first.dispatch_event("click")
    rig.page.locator("#proposal-picker").evaluate(
        "(element, value) => { element.value = value; element.dispatchEvent(new Event('change', {bubbles: true})); }",
        first_id,
    )
    expect(rig.page.locator("#proposal-reference")).to_have_text(selected_id)


def test_nonfirst_selection_preserves_per_proposal_drafts_and_removes_acknowledged_id(brief):
    from playwright.sync_api import expect

    _open(brief)
    first, second, third = brief.initial["proposals"]
    _edit(brief, "reviewer.one", "Rationale belonging only to the first proposal.")
    _select(brief, 1)
    expect(brief.page.locator("#proposal-reference")).to_have_text(second["proposal_id"])
    expect(brief.page.locator("#source-receipt-id")).to_have_text(second["source_receipt_id"])
    assert brief.page.locator("#source-quote").text_content() == second["source_quote"]
    _edit(brief, "reviewer.two", "Rationale belonging only to the second proposal.")
    _select(brief, 0)
    if not brief.page.locator("#review-editor").is_visible():
        brief.page.locator("#review-start").click()
    expect(brief.page.locator("#reviewer")).to_have_value("reviewer.one")
    expect(brief.page.locator("#rationale")).to_have_value(
        "Rationale belonging only to the first proposal."
    )
    _select(brief, 1)
    if not brief.page.locator("#review-editor").is_visible():
        brief.page.locator("#review-start").click()
    expect(brief.page.locator("#reviewer")).to_have_value("reviewer.two")
    expect(brief.page.locator("#rationale")).to_have_value(
        "Rationale belonging only to the second proposal."
    )
    brief.page.locator("#review-cancel").click()
    expect(brief.page.locator("#review-editor")).to_be_hidden()
    assert brief.posts == [] and len(brief.server.proposal_review_service) == 0
    _edit(brief, "reviewer.two", "Updated rationale for the second proposal only.")
    brief.page.locator("#rationale").fill(" ")
    expect(brief.page.locator("#keep-proposal")).to_be_disabled()
    expect(brief.page.locator("#discard-proposal")).to_be_disabled()
    brief.page.locator("#rationale").fill("Updated rationale for the second proposal only.")
    brief.page.locator("#discard-proposal").click()
    expect(brief.page.locator("#proposal-tabs .proposal-tab")).to_have_count(2)
    remaining = _listed(brief)
    assert [row["proposal_id"] for row in remaining["proposals"]] == [
        first["proposal_id"], third["proposal_id"]
    ]
    assert remaining["review_summary"]["discarded"] == 1
    assert len(brief.posts) == 1
    endpoint, raw = brief.posts[0]
    assert endpoint.endswith(f"/{second['proposal_id']}/decision")
    payload = json.loads(raw)
    assert set(payload) == {"decision", "reviewer", "rationale", "idempotency_key"}
    assert payload["decision"] == "DISCARD" and payload["reviewer"] == "reviewer.two"
    assert payload["rationale"] == "Updated rationale for the second proposal only."
    receipt = next(
        item.decision for item in brief.server.proposal_review_service.list_proposals(brief.poc_id)
        if item.proposal_id == second["proposal_id"]
    )
    assert receipt.reviewer == "reviewer.two" and receipt.rationale == payload["rationale"]
    expect(brief.page.locator("#proposal-reference")).not_to_have_text(second["proposal_id"])
    brief.page.reload()
    expect(brief.page.locator("#proposal-tabs .proposal-tab")).to_have_count(2)
    expect(brief.page.locator("#reviewer")).to_have_value("")
    expect(brief.page.locator("#rationale")).to_have_value("")
    assert brief.page.evaluate("() => [localStorage.length, sessionStorage.length]") == [0, 0]


def test_pending_selection_and_edits_cannot_retarget_identical_retry(brief):
    from playwright.sync_api import expect

    held = []

    def intercept(route):
        if not held:
            held.append(route)
        else:
            route.continue_()

    brief.page.route("**/proposals/*/decision", intercept)
    _open(brief)
    _select(brief, 1)
    selected = brief.initial["proposals"][1]["proposal_id"]
    _edit(brief, "reviewer.pending", "Immutable request rationale.")
    brief.page.locator("#discard-proposal").click()
    _wait_held(brief.page, held)
    expect(brief.page.locator("#proposal-picker")).to_be_disabled()
    for button in brief.page.locator("#proposal-tabs .proposal-tab").all():
        expect(button).to_be_disabled()
    for selector in ("#reviewer", "#rationale", "#review-cancel"):
        expect(brief.page.locator(selector)).to_be_disabled()
    _assert_no_selection_change(brief, selected)
    brief.page.locator("#reviewer").evaluate(
        "element => { element.value = 'wrong.proposal'; element.dispatchEvent(new Event('input', {bubbles: true})); }"
    )
    brief.page.locator("#keep-proposal").dispatch_event("click")
    assert len(brief.posts) == 1
    _fulfill(held[0], {"error": "Synthetic interrupted response"}, status=503)
    expect(brief.page.locator("#discard-proposal")).to_be_enabled()
    expect(brief.page.locator("#keep-proposal")).to_be_disabled()
    expect(brief.page.locator("#proposal-picker")).to_be_disabled()
    _assert_no_selection_change(brief, selected)
    brief.page.locator("#discard-proposal").click()
    expect(brief.page.locator("#proposal-tabs .proposal-tab")).to_have_count(2)
    assert len(brief.posts) == 2 and brief.posts[0] == brief.posts[1]
    assert json.loads(brief.posts[1][1])["reviewer"] == "reviewer.pending"
    assert len(brief.server.proposal_review_service) == 1


@pytest.mark.parametrize("failure", ["wrong_acknowledged_id", "reconciliation"])
def test_untrusted_decision_or_refresh_never_advances_to_another_proposal(brief, failure):
    from playwright.sync_api import expect

    enabled = {"fault": True, "committed": False}

    def decision(route):
        response = route.fetch()
        assert response.status == 201
        enabled["committed"] = True
        payload = response.json()
        if failure == "wrong_acknowledged_id":
            payload["proposal_id"] = brief.initial["proposals"][0]["proposal_id"]
        _fulfill(route, payload, response.status)

    def proposals(route):
        if enabled["fault"] and enabled["committed"] and failure == "reconciliation":
            _fulfill(route, {"error": "Synthetic unavailable queue"}, 503)
        else:
            route.continue_()

    brief.page.route("**/proposals/*/decision", decision)
    brief.page.route(brief.api, proposals)
    _open(brief)
    _select(brief, 1)
    selected = brief.initial["proposals"][1]
    _edit(brief, "reviewer.fault", "Record only this source-bound review.")
    brief.page.locator("#discard-proposal").click()
    expect(brief.page.locator("#proposal-review-error")).to_be_visible()
    expect(brief.page.locator("#review-complete")).to_be_hidden()
    expect(brief.page.locator("#proposal-picker")).to_be_disabled()
    _assert_no_selection_change(brief, selected["proposal_id"])
    if failure == "reconciliation":
        expect(brief.page.locator("#proposal-review-error")).to_contain_text("could not be refreshed")
        expect(brief.page.locator("#keep-proposal")).to_be_disabled()
        expect(brief.page.locator("#discard-proposal")).to_be_disabled()
    else:
        assert brief.page.locator("#source-quote").text_content() == selected["source_quote"]
        expect(brief.page.locator("#keep-proposal")).to_be_disabled()
    assert len(brief.posts) == 1 and len(brief.server.proposal_review_service) == 1
    enabled["fault"] = False
    brief.page.reload()
    expect(brief.page.locator("#proposal-tabs .proposal-tab")).to_have_count(2)
    assert selected["proposal_id"] not in [row["proposal_id"] for row in _listed(brief)["proposals"]]


def test_lost_committed_response_retry_does_not_claim_nothing_was_recorded(brief):
    from playwright.sync_api import expect

    attempts = []

    def decision(route):
        attempts.append(route.request.post_data)
        if len(attempts) == 1:
            response = route.fetch()
            assert response.status == 201
            route.abort("connectionfailed")
        else:
            route.continue_()

    brief.page.route("**/proposals/*/decision", decision)
    _open(brief)
    _select(brief, 1)
    _edit(brief, "reviewer.uncertain", "This decision may already be recorded.")
    brief.page.locator("#discard-proposal").click()
    expect(brief.page.locator("#proposal-review-error")).to_be_visible()
    expect(brief.page.locator("#discard-proposal")).to_be_enabled()
    brief.page.locator("#discard-proposal").click()
    expect(brief.page.locator("#proposal-review-error")).to_contain_text(
        re.compile(r"reload", re.IGNORECASE)
    )
    assert "no decision was recorded" not in brief.page.locator("#proposal-review-error").inner_text().lower()
    assert len(attempts) == 2 and attempts[0] == attempts[1]
    assert len(brief.server.proposal_review_service) == 1
    brief.page.reload()
    expect(brief.page.locator("#proposal-tabs .proposal-tab")).to_have_count(2)
    assert _listed(brief)["review_summary"]["discarded"] == 1


def test_full_quote_and_claim_bounds_all_source_kinds_render_only_as_text(brief):
    from playwright.sync_api import expect

    payload = deepcopy(brief.initial)
    rows = []
    for index, kind in enumerate(("EMAIL", "MEETING", "DOCUMENT", "EXISTING_CONTRACT")):
        claim_prefix = f"p95 time to first token {kind} <img src=x onerror=window.briefInjected=1> "
        claim = (claim_prefix + "bounded criterion wording " * 100)[:2000]
        prefix = f"Synthetic {kind} quote begins.\n"
        quote = prefix + claim + "\n" + "Q" * (4000 - len(prefix) - len(claim) - 1)
        rows.append({
            **deepcopy(payload["proposals"][0]),
            "proposal_id": f"prop_brief_bounds_{index}",
            "source_receipt_id": f"srcpt_brief_bounds_{index}",
            "source_kind": kind, "source_quote": quote, "normalized_claim": claim,
        })
    payload["proposals"] = rows
    payload["review_summary"] = {"total": 4, "needs_review": 4, "kept_for_contract": 0, "discarded": 0}
    brief.page.route(brief.api, lambda route: _fulfill(route, payload))
    brief.page.goto(brief.url)
    expect(brief.page.locator("#proposal-tabs .proposal-tab")).to_have_count(4)
    for index, row in enumerate(rows):
        brief.page.set_viewport_size({"width": 1280, "height": 900})
        _select(brief, index)
        expect(brief.page.locator("#proposal-reference")).to_have_text(row["proposal_id"])
        expect(brief.page.locator("#source-receipt-id")).to_have_text(row["source_receipt_id"])
        assert brief.page.locator("#source-quote").text_content() == row["source_quote"]
        assert brief.page.locator("#normalized-claim").text_content() == row["normalized_claim"]
        terms = brief.page.locator("button.source-term")
        assert terms.count() > 0
        for candidate in terms.all():
            text = candidate.text_content()
            assert 8 <= len(text) <= 64
            assert text in row["normalized_claim"] and text in row["source_quote"]
        term = terms.first
        other = next(candidate for candidate in terms.all() if candidate.text_content() != term.text_content())
        term.click()
        expect(term).to_have_attribute("aria-pressed", "true")
        other.click()
        expect(term).to_have_attribute("aria-pressed", "false")
        expect(other).to_have_attribute("aria-pressed", "true")
        selected_text = other.text_content()
        assert brief.page.locator("#source-excerpt mark").count() > 0
        for mark in brief.page.locator("#source-excerpt mark").all():
            assert mark.text_content() == selected_text
            assert mark.text_content() in row["source_quote"]
        term.click()
        expect(term).to_have_attribute("aria-pressed", "true")
        expect(other).to_have_attribute("aria-pressed", "false")
        assert brief.page.locator("#source-quote").text_content() == row["source_quote"]
        assert brief.page.locator("#source-quote img, #normalized-claim img, #source-quote script").count() == 0
        assert brief.page.evaluate("() => window.briefInjected === undefined")
        disclosure = brief.page.locator("details.source-full-quote")
        if disclosure.get_attribute("open") is None:
            disclosure.locator("summary").click()
        expect(brief.page.locator("#source-quote")).to_be_visible()
        brief.page.set_viewport_size({"width": 320, "height": 900})
        expect(brief.page.locator("#proposal-picker")).to_be_visible()
        assert brief.page.evaluate("() => document.documentElement.scrollWidth <= document.documentElement.clientWidth")
    assert brief.posts == []


def test_nonverbatim_claim_has_no_numeric_or_keyword_highlight_fallback(brief):
    from playwright.sync_api import expect

    payload = deepcopy(brief.initial)
    row = payload["proposals"][0]
    row["normalized_claim"] = "Independent ordering invariant requires deterministic behavior."
    assert row["normalized_claim"] not in row["source_quote"]
    brief.page.route(brief.api, lambda route: _fulfill(route, payload))
    _open(brief)
    assert brief.page.locator("#normalized-claim").text_content() == row["normalized_claim"]
    assert brief.page.locator("#source-quote").text_content() == row["source_quote"]
    expect(brief.page.locator("button.source-term")).to_have_count(0)
    expect(brief.page.locator("#source-quote mark")).to_have_count(0)
    expect(brief.page.locator("#source-excerpt mark")).to_have_count(0)
    expect(brief.page.locator("#source-match-note")).to_contain_text(
        re.compile(r"not.*(?:exact|verbatim)|no exact", re.IGNORECASE)
    )
    assert brief.posts == []


def test_pagehide_ignores_delayed_load_and_persisted_pageshow_revalidates(brief):
    from playwright.sync_api import expect

    held = []
    brief.page.route(brief.api, lambda route: held.append(route))
    brief.page.goto(brief.url, wait_until="domcontentloaded")
    _wait_held(brief.page, held)
    brief.page.evaluate("() => window.dispatchEvent(new PageTransitionEvent('pagehide', {persisted: true}))")
    _fulfill(held[0], brief.initial)
    brief.page.wait_for_load_state("networkidle")
    assert brief.initial["proposals"][0]["source_quote"] not in brief.page.locator("#source-quote").text_content()
    expect(brief.page.locator("#review-start")).to_be_disabled()
    brief.page.evaluate("() => window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted: true}))")
    _wait_held(brief.page, held, 2)
    expect(brief.page.locator("#review-start")).to_be_disabled()
    _fulfill(held[1], brief.initial)
    expect(brief.page.locator("#proposal-tabs .proposal-tab")).to_have_count(3)
    expect(brief.page.locator("#review-start")).to_be_enabled()
    expect(brief.page.locator("#reviewer")).to_have_value("")
    expect(brief.page.locator("#rationale")).to_have_value("")
    assert brief.page.locator("#source-quote").text_content() == brief.initial["proposals"][0]["source_quote"]
    assert brief.posts == []


def test_pagehide_ignores_late_decision_ack_and_restores_from_server_counts(brief):
    from playwright.sync_api import expect

    held = []
    brief.page.route("**/proposals/*/decision", lambda route: held.append(route))
    _open(brief)
    _select(brief, 1)
    selected = brief.initial["proposals"][1]
    _edit(brief, "reviewer.lifecycle", "This response belongs to the old page epoch.")
    brief.page.locator("#discard-proposal").click()
    _wait_held(brief.page, held)
    response = held[0].fetch()
    assert response.status == 201
    brief.page.evaluate("() => window.dispatchEvent(new PageTransitionEvent('pagehide', {persisted: true}))")
    _fulfill(held[0], response.json(), response.status)
    brief.page.wait_for_load_state("networkidle")
    assert selected["source_quote"] not in brief.page.locator("#source-quote").text_content()
    expect(brief.page.locator("#review-start")).to_be_disabled()
    brief.page.evaluate("() => window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted: true}))")
    expect(brief.page.locator("#proposal-tabs .proposal-tab")).to_have_count(2)
    expect(brief.page.locator("#review-start")).to_be_enabled()
    expect(brief.page.locator("#reviewer")).to_have_value("")
    expect(brief.page.locator("#rationale")).to_have_value("")
    assert _listed(brief)["review_summary"]["discarded"] == 1
    assert len(brief.posts) == 1 and len(brief.server.proposal_review_service) == 1


def test_reordered_queue_keeps_unique_numbers_and_invalidates_only_changed_binding(brief):
    from playwright.sync_api import expect

    first, second, third = brief.initial["proposals"]
    added = {
        **deepcopy(first),
        "proposal_id": "prop_brief_added_001",
        "source_receipt_id": "srcpt_brief_added_001",
        "source_kind": "DOCUMENT",
        "source_quote": "Operator documentation must describe benchmark conditions.",
        "normalized_claim": "Operator documentation must describe benchmark conditions.",
    }
    changed_quote = "Changed synthetic source context. " + first["source_quote"]

    def queue(route):
        if len(brief.server.proposal_review_service) == 0:
            route.continue_()
            return
        response = route.fetch()
        payload = response.json()
        rows = {row["proposal_id"]: row for row in payload["proposals"]}
        if len(brief.server.proposal_review_service) == 2:
            rows[first["proposal_id"]]["source_quote"] = changed_quote
        payload["proposals"] = [added] + [
            rows[proposal["proposal_id"]]
            for proposal in (first, second) if proposal["proposal_id"] in rows
        ]
        payload["review_summary"]["total"] += 1
        payload["review_summary"]["needs_review"] += 1
        _fulfill(route, payload)

    brief.page.route(brief.api, queue)
    _open(brief)
    _edit(brief, "reviewer.a", "Draft A follows its exact source binding.")
    _select(brief, 1)
    _edit(brief, "reviewer.b", "Draft B stays with proposal B.")
    _select(brief, 2)
    _edit(brief, "reviewer.c", "Discard the third proposal in this fixture.")
    brief.page.locator("#discard-proposal").click()
    expect(brief.page.locator("#proposal-reference")).to_have_text(added["proposal_id"])
    tabs = brief.page.locator("#proposal-tabs .proposal-tab")
    expect(tabs).to_have_count(3)
    for index, number in enumerate((4, 1, 2)):
        expect(tabs.nth(index)).to_contain_text(f"Proposal {number}")
    expect(brief.page.locator("#progress-copy")).to_have_text("1 reviewed · 3 awaiting triage")
    _select(brief, 1)
    expect(brief.page.locator("#proposal-reference")).to_have_text(first["proposal_id"])
    expect(brief.page.locator("#reviewer")).to_have_value("reviewer.a")
    expect(brief.page.locator("#rationale")).to_have_value("Draft A follows its exact source binding.")
    _select(brief, 2)
    expect(brief.page.locator("#proposal-reference")).to_have_text(second["proposal_id"])
    expect(brief.page.locator("#reviewer")).to_have_value("reviewer.b")
    expect(brief.page.locator("#rationale")).to_have_value("Draft B stays with proposal B.")
    brief.page.locator("#discard-proposal").click()
    expect(tabs).to_have_count(2)
    expect(brief.page.locator("#progress-copy")).to_have_text("2 reviewed · 2 awaiting triage")
    _select(brief, 1)
    expect(brief.page.locator("#proposal-reference")).to_have_text(first["proposal_id"])
    assert brief.page.locator("#source-quote").text_content() == changed_quote
    if not brief.page.locator("#review-editor").is_visible():
        brief.page.locator("#review-start").click()
    expect(brief.page.locator("#reviewer")).to_have_value("")
    expect(brief.page.locator("#rationale")).to_have_value("")
    expect(brief.page.locator("#keep-proposal")).to_be_disabled()
    expect(brief.page.locator("#discard-proposal")).to_be_disabled()
    assert [endpoint.rsplit("/", 2)[1] for endpoint, _ in brief.posts] == [
        third["proposal_id"], second["proposal_id"]
    ]


def test_review_reachability_contract_rejects_clipping_and_obscuring_overlay(brief):
    _open(brief)
    _edit(brief, "reviewer.geometry", "Inspect the real decision controls without activating them.")
    _assert_review_document_flow(brief.page)
    target = brief.page.locator("#keep-proposal")
    original = target.evaluate("element => element.parentElement.getAttribute('style')")
    try:
        target.evaluate("""element => {
          const style = element.parentElement.style;
          style.setProperty('height', '2px', 'important');
          style.setProperty('min-height', '0', 'important');
          style.setProperty('overflow', 'hidden', 'important');
        }""")
        with pytest.raises(AssertionError):
            _assert_review_control_reachable(target)
    finally:
        target.evaluate("""(element, original) => {
          if (original === null) element.parentElement.removeAttribute('style');
          else element.parentElement.setAttribute('style', original);
        }""", original)
    _assert_review_control_reachable(target)
    brief.page.evaluate("""() => {
      const overlay = document.createElement('div');
      overlay.id = 'brief-test-obscuring-overlay';
      overlay.style.cssText = 'position:fixed;inset:0;z-index:2147483647;pointer-events:auto';
      document.body.append(overlay);
    }""")
    try:
        with pytest.raises(AssertionError):
            _assert_review_control_reachable(target)
    finally:
        brief.page.locator("#brief-test-obscuring-overlay").evaluate("element => element.remove()")
    _assert_review_control_reachable(target)
    assert brief.posts == [] and len(brief.server.proposal_review_service) == 0
