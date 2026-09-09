"""Customer-review fault regressions using real, disposable localhost services."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

import pytest

from exitspec import web
from tests.test_a5_agreement_browser import _complete_a4_plan
from tests.test_a5_agreement_browser import _running_server as _source_server
from tests.test_poc_performance_lifecycle_web_transport import (
    _create_defined_performance_poc,
    _prepare_payload,
    _request,
    _running_server,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("EXITSPEC_BROWSER_E2E") != "1",
    reason="set EXITSPEC_BROWSER_E2E=1 for localhost customer-review regressions",
)


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright

    expected = Path(__file__).resolve().parents[1] / "src"
    assert Path(web.__file__).resolve().is_relative_to(expected.resolve())
    with sync_playwright() as playwright:
        instance = playwright.chromium.launch(headless=True)
        yield instance
        instance.close()


@pytest.fixture
def page(browser):
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    current = context.new_page()
    current.set_default_timeout(5000)
    errors, requests = [], []
    current.on("pageerror", lambda error: errors.append(str(error)))
    current.on("request", lambda request: requests.append((request.method, request.url)))
    try:
        yield current
        assert errors == []
        assert all(urlparse(url).hostname in {"127.0.0.1", "localhost"} for _, url in requests)
        assert not any(
            method == "POST" and re.search(r"/(freeze|run|runs|execute|import|evidence|verdict)(/|$)", urlparse(url).path)
            for method, url in requests
        )
    finally:
        context.close()


def _get(server, path):
    status, payload, _ = _request(server, "GET", path, content_type=None, origin=None)
    assert status == 200
    return payload


@pytest.fixture
def performance(tmp_path, page):
    from playwright.sync_api import expect

    with _running_server(tmp_path) as server:
        poc = _create_defined_performance_poc(server)
        path = f"/api/pocs/{poc}/agreement"
        status, _, _ = _request(server, "POST", path, payload=_prepare_payload())
        assert status == 201
        before = _get(server, path)
        base = f"http://127.0.0.1:{server.server_port}"
        page.goto(base + before["customer_review"]["review_url"])
        expect(page.locator("#agreement-checkbox")).to_be_visible()
        yield SimpleNamespace(page=page, server=server, path=path, before=before)


def _performance_choice(page, decision):
    page.locator("#agreement-checkbox").check()
    if decision == "REQUEST_CHANGES":
        page.locator("#request-changes").click()
        page.locator("#change-rationale").fill("Change the exact threshold for this synthetic agreement.")
    return page.locator("#confirm-requirements" if decision == "CONFIRM" else "#request-changes")


@pytest.mark.parametrize("fault", ["incomplete", "review_id", "version", "fingerprint", "decision"])
def test_f02_untrusted_success_never_displays_recorded_confirmation(performance, fault):
    from playwright.sync_api import expect

    page = performance.page
    posts = []

    def intercept(route):
        posts.append((route.request.post_data, route.request.headers["idempotency-key"]))
        if len(posts) > 1:
            return route.fulfill(response=route.fetch())
        if fault == "incomplete":
            payload = {"unexpected": "incomplete response"}
        else:
            response = route.fetch()
            assert response.status == 200
            payload = response.json()
            if fault == "review_id":
                payload["review"]["review_id"] += "-different"
            elif fault == "version":
                payload["confirmation"]["contract_version"] = "999"
            elif fault == "fingerprint":
                payload["confirmation"]["contract_fingerprint"] = "0" * 64
            else:
                payload["decision"]["decision"] = "REQUEST_CHANGES"
        route.fulfill(status=200, content_type="application/json", body=json.dumps(payload))

    page.route("**/api/review/*/decision", intercept)
    button = _performance_choice(page, "CONFIRM")
    button.click()
    expect(page.locator("#form-message")).to_contain_text("could not be verified")
    expect(page.locator("#terminal-state")).to_be_hidden()
    expect(page.locator("#agreement-checkbox")).to_be_disabled()
    expect(page.locator("#request-changes")).to_be_disabled()
    current = _get(performance.server, performance.path)
    assert current["customer_review"]["status"] == ("PENDING" if fault == "incomplete" else "CONFIRMED")
    assert current["frozen_contract"] is None
    button.click()
    expect(page.locator("#terminal-title")).to_have_text("POC agreement confirmed")
    expect(page.locator("#terminal-state")).to_be_visible()
    assert len(posts) == 2 and posts[0] == posts[1]


@pytest.mark.parametrize("decision", ["CONFIRM", "REQUEST_CHANGES"])
def test_f06_lost_committed_reply_preserves_immutable_retry(performance, decision):
    from playwright.sync_api import expect

    page = performance.page
    posts, replies = [], []

    def intercept(route):
        posts.append((route.request.post_data, route.request.headers["idempotency-key"]))
        reply = route.fetch()
        replies.append(reply.json())
        if len(posts) == 1:
            route.abort("connectionfailed")
        else:
            route.fulfill(response=reply)

    page.route("**/api/review/*/decision", intercept)
    button = _performance_choice(page, decision)
    button.click()
    expect(page.locator("#form-message")).to_contain_text("could not be verified")
    assert "Nothing changed" not in page.locator("#form-message").inner_text()
    expect(page.locator("#agreement-checkbox")).to_be_disabled()
    expect(page.locator("#change-rationale")).to_be_disabled()
    # Script-dispatched changes cannot alter a pending attempt's body or action.
    page.locator("#change-rationale").evaluate("el => { el.value='Different rationale'; el.dispatchEvent(new Event('input', {bubbles:true})); }")
    opposite = "CONFIRM" if decision == "REQUEST_CHANGES" else "REQUEST_CHANGES"
    page.locator("#decision-form").evaluate("(form, decision) => form.dispatchEvent(new SubmitEvent('submit', {bubbles:true,cancelable:true,submitter:form.querySelector('[value=\"'+decision+'\"]')}))", opposite)
    button.click()
    expect(page.locator("#terminal-state")).to_be_visible()
    assert len(posts) == 2 and posts[0] == posts[1]
    assert replies[-1]["idempotent_replay"] is True
    assert _get(performance.server, performance.path)["frozen_contract"] is None


def test_f10_confirmation_copy_preserves_separate_owner_freeze(performance):
    from playwright.sync_api import expect

    page = performance.page
    expect(page.locator(".boundary-note")).to_contain_text("The POC owner freezes it separately")
    assert "Confirmation freezes" not in page.locator(".boundary-note").inner_text()
    _performance_choice(page, "CONFIRM").click()
    expect(page.locator("#terminal-state")).to_be_visible()
    expect(page.locator("#terminal-next-title")).to_have_text("Next: freeze the confirmed contract.")
    current = _get(performance.server, performance.path)
    assert current["customer_review"]["status"] == "CONFIRMED"
    assert current["frozen_contract"] is None


def test_f02_reload_requires_the_recorded_confirmation_receipt(performance):
    from playwright.sync_api import expect

    page = performance.page
    _performance_choice(page, "CONFIRM").click()
    expect(page.locator("#terminal-state")).to_be_visible()
    api = page.url.replace("/review/", "/api/review/")

    def incomplete_read(route):
        response = route.fetch()
        payload = response.json()
        payload["confirmation"] = None
        route.fulfill(status=200, content_type="application/json", body=json.dumps(payload))

    page.route(api, incomplete_read)
    page.reload()
    expect(page.locator("#error-state")).to_be_visible()
    expect(page.locator("#terminal-state")).to_be_hidden()
    assert "No decision has been recorded" not in page.locator("#error-message").inner_text()
    assert _get(performance.server, performance.path)["customer_review"]["status"] == "CONFIRMED"


def _open_dynamic_review(page, base):
    page.goto(base + "/app/pocs/new")
    page.locator('input[name="first_source_choice"][value="DOCUMENT"]').check()
    for selector, value in {"#display-name": "Synthetic retry audit", "#customer-label": "Generated customer", "#use-case": "Bind a current capability plan.", "#owner": "synthetic.reviewer"}.items():
        page.locator(selector).fill(value)
    page.locator("#create-poc").click()
    page.wait_for_url(re.compile(r"/sources/new$"))
    page.locator("#document-text").fill("The response should be acceptable. The system must select the exact tool. Latency should be visible to the customer. Production deployment is excluded from this agreement.")
    page.locator("#capture-source").click()
    page.wait_for_url(re.compile(r"/review$"))
    page.locator("#assisted-authoring-link").click()
    page.locator('input[name="source_receipt"]').check()
    page.locator("#authoring-submit").click()
    page.locator("#authoring-result").wait_for(state="visible")
    page.locator("#open-proposal-review").click()
    page.wait_for_url(re.compile(r"/review$"))
    for index in range(3):
        page.locator("#review-start").click()
        page.locator("#reviewer").fill("synthetic.a3.reviewer")
        page.locator("#rationale").fill("Retain this source-bound claim for local planning.")
        page.locator("#keep-proposal").click()
        page.wait_for_function("(document.querySelector('#review-complete')?.hidden === false) || (document.querySelector('#proposal-heading')?.textContent !== " + repr(f"Proposal {index + 1}") + ")")
    page.locator("#plan-capabilities").click()
    _complete_a4_plan(page, threshold="0.95")
    page.locator("#open-agreement").click()
    page.locator("#assembly-reviewer").fill("synthetic.a5.reviewer")
    page.locator("#assembly-rationale").fill("Assemble this exact current synthetic plan.")
    page.locator("#prepare-agreement").click()
    page.locator("#agreement-summary").wait_for(state="visible")
    page.locator("#open-customer-review").click()
    page.wait_for_url(re.compile(r"/review/[A-Za-z0-9_-]+$"))
    return base + "/api" + urlparse(page.url).path


@pytest.mark.parametrize("decision", ["CONFIRM", "REQUEST_CHANGES"])
def test_f08_dynamic_review_retries_same_body_and_key_after_commit(page, decision):
    from playwright.sync_api import expect

    with _source_server() as base:
        api = _open_dynamic_review(page, base)
        posts, replies = [], []

        def intercept(route):
            if route.request.method != "POST":
                return route.continue_()
            posts.append(route.request.post_data)
            response = route.fetch()
            replies.append(response.json())
            if len(posts) == 1:
                route.abort("connectionfailed")
            else:
                route.fulfill(response=response)

        page.route(api, intercept)
        page.locator("#agreement-checkbox").check()
        page.locator("#review-rationale").fill("Record this exact synthetic customer choice.")
        selected = page.locator("#confirm-agreement" if decision == "CONFIRM" else "#request-changes")
        selected.click()
        expect(page.locator("#customer-review-error")).to_contain_text("could not be verified")
        expect(page.locator("#review-rationale")).to_be_disabled()
        expect(page.locator("#agreement-checkbox")).to_be_disabled()
        page.locator("#review-rationale").evaluate("el => { el.value='Retargeted rationale'; el.dispatchEvent(new Event('input', {bubbles:true})); }")
        selected.click()
        expect(page.locator("#review-result")).to_be_visible()
        assert len(posts) == 2 and posts[0] == posts[1]
        assert replies[-1]["idempotent_replay"] is True
        expected = "CONFIRMED" if decision == "CONFIRM" else "CHANGES_REQUESTED"
        assert page.request.get(api).json()["review"]["status"] == expected
        expect(page.locator("#customer-decision-form")).to_be_hidden()
