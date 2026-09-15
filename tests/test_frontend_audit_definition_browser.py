"""Browser regressions for acknowledged definition state and retry access."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tests.test_poc_contract_definition_web_transport import (
    _create_kept_proposals,
    _request,
    _running_server,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("EXITSPEC_BROWSER_E2E") != "1",
    reason="set EXITSPEC_BROWSER_E2E=1 to run the definition recovery tests",
)


def _read(server, path):
    status, payload, _ = _request(
        server, "GET", path, content_type=None, origin=None
    )
    assert status == 200
    return payload


def _fill_definition(page):
    from playwright.sync_api import expect

    expect(page.locator("#reviewer")).to_be_enabled()
    for detail in page.locator("details").all():
        if detail.get_attribute("open") is None:
            detail.locator("summary").click()
    for selector, value in {
        "#minimum-samples": "96",
        "#concurrency": "6",
        "#prompt-tokens-min": "512",
        "#prompt-tokens-max": "4096",
        "#output-tokens-min": "64",
        "#output-tokens-max": "512",
        "#reviewer": "recovery.reviewer",
        "#rationale": "Bind this synthetic definition to the exact reviewed source.",
    }.items():
        page.locator(selector).fill(value)
    expect(page.locator("#save-definition")).to_be_enabled()


def _capture(page, name):
    capture_root = os.environ.get("EXITSPEC_AUDIT_CAPTURE_ROOT")
    if capture_root:
        directory = Path(capture_root)
        directory.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(directory / f"{name}.png"), full_page=True)


@pytest.fixture
def definition_browser(tmp_path):
    from playwright.sync_api import sync_playwright

    with _running_server(tmp_path) as server, sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        try:
            poc_id, _ = _create_kept_proposals(server)
            base = f"http://127.0.0.1:{server.server_port}"
            path = f"/api/pocs/{poc_id}/definitions"
            yield server, page, base, path, f"{base}/app/pocs/{poc_id}/define"
            assert not errors
        finally:
            browser.close()


@pytest.mark.parametrize(
    "projection_fault",
    ["missing_definition", "changed_definition", "changed_source", "missing_proposal"],
)
def test_f07_acknowledged_definition_rejects_stale_or_mismatched_projection(
    definition_browser, projection_fault
):
    from playwright.sync_api import expect

    server, page, base, path, url = definition_browser
    original = _read(server, path)
    acknowledged = []
    injected = []

    def fault(route):
        if route.request.method == "POST":
            response = route.fetch()
            assert response.status == 201
            acknowledged.append(response.json())
            route.fulfill(response=response)
        elif acknowledged and not injected:
            current = route.fetch().json()
            row = next(
                row for row in current["proposals"]
                if row["proposal_id"] == acknowledged[0]["proposal_id"]
            )
            if projection_fault == "missing_definition":
                current = original
            elif projection_fault == "changed_definition":
                row["definition"]["definition_sha256"] = "0" * 64
            elif projection_fault == "changed_source":
                row["source_quote"] += " Changed projection."
            else:
                current["proposals"].remove(row)
            injected.append(current)
            route.fulfill(status=200, content_type="application/json", body=json.dumps(current))
        else:
            route.continue_()

    page.route(base + path, fault)
    page.goto(url)
    _fill_definition(page)
    page.locator("#save-definition").click()
    expect(page.locator("#contract-definition-error")).to_contain_text(
        "definition was recorded", timeout=2000
    )
    expect(page.locator("#save-definition")).to_be_disabled()
    expect(page.locator("#reviewer")).to_be_disabled()
    assert len(acknowledged) == len(injected) == 1
    current = _read(server, path)
    saved = [row for row in current["proposals"] if row["definition"] is not None]
    assert len(saved) == 1
    assert saved[0]["definition"] == acknowledged[0]["definition"]
    _capture(page, f"f07-{projection_fault}-blocked")
    page.reload()
    expect(page.locator("#reviewer")).to_be_enabled()
    expect(page.locator("#progress-copy")).to_have_text("1 of 2 defined")
    expect(page.locator("#source-quote")).to_have_text("Error rate must remain below 1%.")


def test_f07_acknowledged_definition_accepts_reordered_current_projection(definition_browser):
    from playwright.sync_api import expect

    server, page, base, path, url = definition_browser
    acknowledged = []

    def reorder(route):
        response = route.fetch()
        payload = response.json()
        if route.request.method == "POST":
            acknowledged.append(payload)
        elif acknowledged:
            payload["proposals"].reverse()
        route.fulfill(response=response, json=payload)

    page.route(base + path, reorder)
    page.goto(url)
    _fill_definition(page)
    page.locator("#save-definition").click()
    expect(page.locator("#source-quote")).to_have_text("Error rate must remain below 1%.")
    expect(page.locator("#progress-copy")).to_have_text("1 of 2 defined")
    expect(page.locator("#contract-definition-error")).to_be_hidden()
    assert len(acknowledged) == 1
    assert sum(row["definition"] is not None for row in _read(server, path)["proposals"]) == 1


@pytest.mark.parametrize("viewport", [(1440, 1000), (1280, 720), (320, 720)])
def test_f09_definition_retry_remains_visible_and_pointer_reachable(
    definition_browser, viewport
):
    from playwright.sync_api import expect

    server, page, base, path, url = definition_browser
    page.set_viewport_size({"width": viewport[0], "height": viewport[1]})
    posts = []

    def drop_first_reply(route):
        if route.request.method != "POST":
            return route.continue_()
        response = route.fetch()
        posts.append((route.request.post_data, response.status))
        if len(posts) == 1:
            route.abort("connectionfailed")
        else:
            route.fulfill(response=response)

    page.route(base + path, drop_first_reply)
    page.goto(url)
    _fill_definition(page)
    _capture(page, f"f09-{viewport[0]}-before-submit")
    button = page.locator("#save-definition")
    button.click()
    expect(page.locator("#contract-definition-error")).to_be_visible()
    expect(button).to_have_text("Retry save definition")
    button.scroll_into_view_if_needed()
    geometry = button.evaluate("""element => {
      const r = element.getBoundingClientRect();
      const hit = document.elementFromPoint(r.left+r.width/2, r.top+r.height/2);
      const alert = document.querySelector('#contract-definition-error').getBoundingClientRect();
      return {
        hit: hit === element || element.contains(hit),
        visible: r.left >= 0 && r.top >= 0 && r.right <= innerWidth && r.bottom <= innerHeight,
        overlap: Math.min(r.right,alert.right)>Math.max(r.left,alert.left) &&
          Math.min(r.bottom,alert.bottom)>Math.max(r.top,alert.top)
      };
    }""")
    _capture(page, f"f09-{viewport[0]}-retry")
    assert geometry == {"hit": True, "visible": True, "overlap": False}
    button.click(timeout=2000)
    expect(page.locator("#source-quote")).to_have_text("Error rate must remain below 1%.")
    assert len(posts) == 2
    assert posts[0][0] == posts[1][0]
    assert [status for _, status in posts] == [201, 200]
    assert sum(row["definition"] is not None for row in _read(server, path)["proposals"]) == 1
