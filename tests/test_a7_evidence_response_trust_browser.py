"""Adversarial browser checks for A7 generic-evidence response trust."""

from __future__ import annotations

import json
import threading
from contextlib import contextmanager

import pytest

from exitspec.poc_creation import DraftPOCCreateRequest
from exitspec.poc_source_demo import SourceNeutralPOCDemoServer

playwright_sync = pytest.importorskip("playwright.sync_api")
POC_ID = "poc_a7_untrusted_evidence"
VALID_ATTEMPT_ID = "eatm_" + "a" * 32


@contextmanager
def _running_server():
    server = SourceNeutralPOCDemoServer(("127.0.0.1", 0))
    server.draft_poc_service.create(
        DraftPOCCreateRequest(
            poc_id=POC_ID,
            display_name="Untrusted evidence response",
            customer_label="A7 adversarial customer",
            use_case="Reject untrusted evidence projections.",
            owner="a7.security.owner",
            first_source_choice="EMAIL",
        ),
        idempotency_key="a7-untrusted-evidence-poc",
    )
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        worker.join(timeout=5)
        assert not worker.is_alive()
        server.server_close()


def _attempt(*, attempt_id: str, pack_url: str) -> dict:
    return {
        "attempt_id": attempt_id,
        "poc_id": POC_ID,
        "contract_id": "contract-a7-adversarial",
        "contract_version": "1",
        "contract_hash": "b" * 64,
        "status": "COMPLETED",
        "results": [
            {
                "ingestion_status": "ADMITTED",
                "verdict": "PASS",
                "limitations": [],
            }
        ],
        "reduction": {"verdict": "PASS", "limitations": []},
        "evidence_pack_url": pack_url,
        "evidence_pack_sha256": "d" * 64,
        "reason": "Synthetic untrusted response must not reach the DOM.",
        "next_action": "Do not act on this response.",
        "is_current": True,
        "shipping_authorized": False,
    }


@pytest.mark.parametrize(
    ("attempt_id", "pack_url", "history_contract_hash"),
    (
        pytest.param(
            VALID_ATTEMPT_ID,
            "https://evil.example/artifacts/eatm_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/decision-packet.html",
            None,
            id="cross-origin-pack",
        ),
        pytest.param(
            f"{VALID_ATTEMPT_ID}/../eatm_{'c' * 32}",
            f"/artifacts/{VALID_ATTEMPT_ID}/../eatm_{'c' * 32}/decision-packet.html",
            None,
            id="traversal-attempt",
        ),
        pytest.param(
            VALID_ATTEMPT_ID,
            f"/artifacts/{VALID_ATTEMPT_ID}/decision-packet.html",
            "e" * 64,
            id="current-history-identity-mismatch",
        ),
    ),
)
def test_untrusted_evidence_response_cannot_navigate_or_trigger_mutation(
    attempt_id,
    pack_url,
    history_contract_hash,
):
    with _running_server() as base_url, playwright_sync.sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 720})
        api_url = f"{base_url}/api/pocs/{POC_ID}/evidence"
        attempt = _attempt(attempt_id=attempt_id, pack_url=pack_url)
        history_attempt = dict(attempt)
        if history_contract_hash is not None:
            history_attempt["contract_hash"] = history_contract_hash
        payload = {
            "poc_id": POC_ID,
            "current": attempt,
            "history": [history_attempt],
            "closure": None,
            "shipping_authorized": False,
            "authorization": "Evidence never authorizes deployment.",
        }
        mutations: list[str] = []
        page.on(
            "request",
            lambda request: mutations.append(request.url)
            if request.method != "GET"
            else None,
        )
        page.route(
            api_url,
            lambda route: route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(payload),
            ),
        )
        try:
            evidence_url = f"{base_url}/app/pocs/{POC_ID}/evidence"
            page.goto(evidence_url)
            page.locator("#evidence-error").wait_for(state="visible")
            assert page.url == evidence_url
            assert "could not be trusted" in page.locator("#evidence-error").inner_text()
            assert page.locator("#evidence-pack-link").is_hidden()
            assert page.locator("#evidence-pack-link").get_attribute("href") is None
            assert pack_url not in page.locator("body").inner_text()
            assert attempt_id not in page.locator("body").inner_text()
            page.locator("#evidence-acknowledged").check()
            assert page.locator("#start-evidence").is_disabled()
            page.locator("#start-evidence").dispatch_event("click")
            assert mutations == []
            assert page.url == evidence_url
        finally:
            browser.close()
