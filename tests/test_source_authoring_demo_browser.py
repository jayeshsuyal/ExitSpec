"""Rehearse the actual synthetic source-authoring to named handoff product path."""

from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path

import pytest

from exitspec.poc_source_demo import SourceNeutralPOCDemoServer
from exitspec.source_authoring_web import SourceAuthoringWebError
from exitspec.workspace_closure import POCClosureConflict
from tests.test_a6_source_neutral_browser import _complete_mixed_plan

pytestmark = pytest.mark.skipif(
    os.environ.get("EXITSPEC_BROWSER_E2E") != "1",
    reason="the actual synthetic demo rehearsal is mandatory in the v0.4 gate",
)


def test_actual_synthetic_source_authoring_to_human_declared_proof_and_handoff():
    from playwright.sync_api import expect, sync_playwright

    server = SourceNeutralPOCDemoServer(("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    output = os.environ.get("EXITSPEC_SYNTHETIC_DEMO_EVIDENCE")
    output_root = Path(output) if output else None
    if output_root:
        output_root.mkdir(parents=True, exist_ok=True)
    requests, failures, errors = [], [], []

    def capture(page, name):
        if output_root:
            page.screenshot(path=str(output_root / (name + ".png")), full_page=True)

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page.on("request", lambda item: requests.append((item.method, item.url)))
            page.on("response", lambda item: failures.append((item.status, item.url)) if item.status >= 400 else None)
            page.on("pageerror", lambda error: errors.append(str(error)))
            try:
                page.goto(base + "/app/pocs/new")
                page.locator('input[name="first_source_choice"][value="DOCUMENT"]').check()
                page.locator("#display-name").fill("Synthetic source to proof demo")
                page.locator("#customer-label").fill("Synthetic demonstration customer")
                page.locator("#use-case").fill("Review human requirements and prove only the declared supported scope.")
                page.locator("#owner").fill("synthetic.demo.owner")
                page.locator("#create-poc").click()
                page.wait_for_url(re.compile(r"/app/pocs/poc_[a-z0-9_-]+/sources/new$"))
                poc = re.search(r"/pocs/(poc_[a-z0-9_-]+)/", page.url).group(1)
                page.locator("#document-text").fill(
                    "Response quality must satisfy the customer. "
                    "The system must select the exact requested tool. "
                    "Latency must remain visible to the customer."
                )
                page.locator("#capture-source").click()
                page.wait_for_url(base + f"/app/pocs/{poc}/review")
                source_receipt_id = server.proposal_review_service.list_proposals(poc)[0].source_receipt_id
                page.locator("#source-authoring-link").click()
                expect(page.locator("#source-mode-copy")).to_contain_text("no external inference call")
                expect(page.locator("#source-choice")).to_be_enabled()
                page.locator("#source-choice").select_option(source_receipt_id)
                page.locator("#source-preview").click()
                expect(page.locator("#source-disclosure")).to_be_visible()
                page.locator("#source-business-text").check()
                page.locator("#source-acknowledged").check()
                page.locator("#source-authorize").click()
                expect(page.locator("#source-run")).to_be_enabled()
                assert server.source_authoring_web.operations.ledger[0] == 0
                assert server.source_authoring_web._thread is None
                capture(page, "01-explicit-synthetic-disclosure")
                page.locator("#source-run").click()
                expect(page.locator("#source-review-result")).to_be_visible()
                expect(page.locator("#source-status")).to_contain_text("NEEDS_REVIEW")
                assert server.source_authoring_web.operations.ledger[0] == 1
                rows = server.proposal_review_service.list_proposals(poc)
                assert len(rows) == 3 and all(row.decision is None for row in rows)
                receipts = server.assisted_authoring_service.list_receipts(poc)
                assert len(receipts) == 1 and receipts[0].provider == "synthetic-source-authoring"
                page.locator("#source-review-result").click()
                for index in range(3):
                    page.locator("#review-start").click()
                    page.locator("#reviewer").fill("synthetic.demo.reviewer")
                    page.locator("#rationale").fill("Retain this exact source proposal for separate human planning.")
                    if index == 0:
                        capture(page, "02-named-human-review")
                    page.locator("#keep-proposal").click()
                    expect(page.locator("#progress-bar")).to_have_attribute("aria-valuenow", str(index + 1))
                expect(page.locator("#review-complete")).to_be_visible()
                reviewed = server.proposal_review_service.list_proposals(poc)
                assert all(row.decision.reviewer == "synthetic.demo.reviewer" for row in reviewed)
                page.locator("#plan-capabilities").click()
                _complete_mixed_plan(page)
                capture(page, "03-human-declared-planning")
                page.locator("#open-agreement").click()
                page.locator("#assembly-reviewer").fill("synthetic.demo.assembler")
                page.locator("#assembly-rationale").fill("Assemble exactly the human-declared supported and excluded scope.")
                page.locator("#prepare-agreement").click()
                expect(page.locator("#agreement-summary")).to_be_visible()
                page.locator("#open-customer-review").click()
                page.locator("#agreement-checkbox").check()
                page.locator("#review-rationale").fill("Confirm this exact synthetic demonstration agreement.")
                page.locator("#confirm-agreement").click()
                expect(page.locator("#review-result")).to_be_visible()
                page.locator("#return-to-agreement").click()
                page.locator("#freeze-agreement").click()
                expect(page.locator("#agreement-status")).to_have_text("FROZEN")
                capture(page, "04-confirmed-frozen-agreement")
                page.locator("#open-evidence").click()
                page.locator("#evidence-acknowledged").check()
                page.locator("#start-evidence").click()
                expect(page.locator("#evidence-current-status")).to_have_text("COMPLETED")
                expect(page.locator("#evidence-result-verdict")).to_have_text("PASS")
                evidence = server.generic_evidence_service.snapshot_payload(poc)
                assert evidence["current"]["evidence_pack_url"] and evidence["closure"] is None
                capture(page, "05-supported-deterministic-proof")
                page.locator("#decision-owner").fill("synthetic.demo.handoff")
                page.locator("#decision-rationale").fill("Handoff the exact completed synthetic evidence; no shipping authority.")
                page.locator("#handoff-evidence").click()
                expect(page.locator("#evidence-task-heading")).to_have_text("Human decision recorded")
                closure = server.generic_evidence_service.snapshot_payload(poc)["closure"]
                assert closure["decision"] == "HANDOFF_COMPLETED" and not closure["shipping_authorized"]
                with pytest.raises((POCClosureConflict, SourceAuthoringWebError)):
                    server.source_authoring_web.request(poc, "bootstrap", {})
                capture(page, "06-named-terminal-handoff")
                assert errors == [] and failures == []
                assert all(url.startswith(base + "/") for _, url in requests)
                if output_root:
                    (output_root / "rehearsal.json").write_text(json.dumps({
                        "status": "PASS", "poc_id": poc,
                        "server": "SourceNeutralPOCDemoServer", "source": "literal synthetic document",
                        "authoring": "synthetic-source-authoring subprocess; explicit separate consent",
                        "authoring_claims": 1, "named_human_decisions": 3,
                        "planning": "HUMAN_DECLARED: unsupported advisory, exact tool selection threshold, explicit exclusion",
                        "confirmation_and_freeze": "actual product pages",
                        "supported_proof_verdict": "PASS", "closure": closure,
                        "requests": requests, "page_errors": errors, "failed_responses": failures,
                        "limitations": ["Synthetic fixtures only; no provider call or spend", "Native Zoom is not composed into this server", "Unsupported/advisory/excluded criteria do not become proven", "No deployment, shipping or release authority"],
                    }, indent=2) + "\n")
            finally:
                browser.close()
    finally:
        server.shutdown()
        thread.join(timeout=5)
        assert not thread.is_alive()
        server.server_close()
