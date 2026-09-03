"""Executable contract for the bounded v0.5 qualification walkthrough."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src" / "exitspec" / "static"
WEB = ROOT / "src" / "exitspec" / "web.py"


def _read(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


def test_qualification_route_is_exactly_a_mode_of_the_existing_app_shell():
    source = WEB.read_text(encoding="utf-8")
    assert "def _is_qualification_demo_query" in source
    assert 'fields == [("mode", "qualification")]' in source
    assert '"qualification.html"' in source
    assert all((STATIC / name).is_file() for name in (
        "qualification.html",
        "qualification.css",
        "qualification.js",
    ))


def test_walkthrough_has_four_ordered_screens_and_one_action_each():
    html = _read("qualification.html")

    assert html.count('data-screen="1"') == 1
    assert html.count('data-screen="2"') == 1
    assert html.count('data-screen="3"') == 1
    assert html.count('data-screen="4"') == 1
    assert html.index('data-screen="1"') < html.index('data-screen="2"')
    assert html.index('data-screen="2"') < html.index('data-screen="3"')
    assert html.index('data-screen="3"') < html.index('data-screen="4"')
    for phrase in (
        "What is being qualified?",
        "Can the frozen question be proven?",
        "What does the evidence establish?",
        "Is the qualification still current?",
        "PROVABLE",
        "PASS",
        "CURRENT",
        "STALE",
        "Consideration for up to 5% canary",
        "ExitSpec never authorizes deployment or traffic.",
    ):
        assert phrase in html
    assert html.count('class="primary-action"') == 4


def test_walkthrough_is_deterministic_text_only_and_keeps_authority_boundary():
    javascript = _read("qualification.js")
    html = _read("qualification.html")

    assert "fetch(" not in javascript
    assert "XMLHttpRequest" not in javascript
    assert "localStorage" not in javascript
    assert "sessionStorage" not in javascript
    assert "textContent" in javascript
    assert "immutable receipt stays unchanged" in html
    assert "no system was changed" in html
    assert "deployment or traffic" in html
    assert "provider" not in javascript.lower()


def test_walkthrough_has_compact_desktop_shell_and_narrow_layout():
    css = _read("qualification.css")

    assert "min-height: 100dvh" in css
    assert "overflow: hidden" in css
    assert ".qualification-hero" in css
    assert ".qualification-main { min-height: 0; overflow: auto; }" in css
    assert "@media (max-width: 800px)" in css
    assert "@media (max-width: 480px)" in css
    assert "@media (min-width: 801px) and (max-height: 700px)" in css
    assert "allowing zoomed views to scroll" in css
    assert "@media (prefers-reduced-motion: reduce)" in css


@pytest.mark.skipif(
    os.environ.get("EXITSPEC_BROWSER_E2E") != "1",
    reason="set EXITSPEC_BROWSER_E2E=1 to run the Chromium walkthrough",
)
def test_chromium_walkthrough_reaches_stale_without_network_or_scroll(tmp_path):
    from playwright import sync_api

    from tests.test_browser_new_id_flow import _running_server

    expect = sync_api.expect
    with _running_server(tmp_path) as base_url:
        with sync_api.sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1280, "height": 720})
            requests: list[str] = []
            page.on("request", lambda request: requests.append(request.url))
            page.goto(f"{base_url}/app?mode=qualification")
            expect(page.get_by_role("heading", name="What is being qualified?")).to_be_visible()
            assert page.evaluate("document.documentElement.scrollHeight <= window.innerHeight")

            for next_screen, heading in (
                ("2", "Can the frozen question be proven?"),
                ("3", "What does the evidence establish?"),
                ("4", "Is the qualification still current?"),
            ):
                page.locator(f'[data-next="{next_screen}"]').click()
                expect(page.get_by_role("heading", name=heading)).to_be_visible()

            page.locator("#mutate-button").click()
            expect(page.locator("#currency-state")).to_have_text("STALE")
            expect(page.locator("#currency-title")).to_have_text(
                "Requalification is required"
            )
            assert all(url.startswith(base_url) for url in requests)
            browser.close()
