"""SourceNeutral projects native capture availability without optional adapters."""
import os
import threading

import pytest

from exitspec.poc_source_demo import SourceNeutralPOCDemoServer
from tests.test_meeting_session_web_transport import _create_draft
from tests.test_zoom_live_browser import install_fake
from tests.test_zoom_live_runtime import settings

pytestmark = pytest.mark.skipif(
    os.environ.get("EXITSPEC_BROWSER_E2E") != "1", reason="mandatory unified capture Chromium controls",
)


@pytest.mark.parametrize("route", ["sources/new", "capture"])
@pytest.mark.parametrize("paired", [False, True])
def test_source_neutral_capture_hints_and_no_unsupported_probes(route, paired):
    from playwright.sync_api import expect, sync_playwright

    server = SourceNeutralPOCDemoServer(("127.0.0.1", 0))
    children, _ = install_fake(server)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    urls, errors = [], []
    try:
        poc = _create_draft(server)
        if paired:
            server.zoom_live_runtime.pair(poc, settings())
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            page.on("request", lambda request: urls.append(request.url))
            page.on("pageerror", lambda error: errors.append(str(error)))
            try:
                response = page.goto(f"{base}/app/pocs/{poc}/{route}")
                raw = response.body()
                assert int(response.headers["content-length"]) == len(raw)
                assert raw.count(b'data-source-neutral="true"') == raw.count(b'data-zoom-live-enabled="true"') == 1
                expect(page.locator("#zoom-live-panel")).to_be_visible()
                expect(page.locator("#zoom-live-mode")).to_contain_text("Simulated transport" if paired else "Disconnected")
                expect(page.locator("#meeting-mode-paste")).to_be_checked()
                expect(page.locator("#zoom-live-start")).to_be_disabled()
                assert children == [] and server.source_authoring_web.operations.ledger[0] == 0
                document_radio = page.locator('input[name="source_kind"][value="DOCUMENT"]')
                page.locator("label").filter(has=document_radio).click()
                expect(document_radio).to_be_checked()
                expect(page.locator("#meeting-entry")).to_be_hidden()
                # Wait past native polling's one-second interval to prove that
                # leaving Meeting stops further native and recording requests.
                before = sum(url.endswith("/zoom-live") for url in urls)
                page.wait_for_timeout(1100)
                assert sum(url.endswith("/zoom-live") for url in urls) == before
                assert not any(any(part in url for part in ("/meeting-session", "/zoom-guided", "/stt")) for url in urls)
                assert children == [] and errors == [] and all(url.startswith(base + "/") for url in urls)
            finally:
                browser.close()
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()
