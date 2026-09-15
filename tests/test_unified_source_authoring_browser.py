"""One real browser/server/POC: fake native Zoom to fake Fireworks to human handoff."""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
from dataclasses import replace
from pathlib import Path

import pytest

from exitspec.poc_source_demo import SourceNeutralPOCDemoServer
from exitspec.source_authoring_web import SourceAuthoringWebError
from exitspec.workspace_closure import POCClosureConflict
from tests.helpers.source_authoring_admission import fake_transport, make_launch
from tests.test_a6_source_neutral_browser import _complete_mixed_plan
from tests.test_zoom_live_browser import install_fake
from tests.test_zoom_live_runtime import packet, settings

pytestmark = pytest.mark.skipif(
    os.environ.get("EXITSPEC_BROWSER_E2E") != "1",
    reason="the actual synthetic demo rehearsal is mandatory in the v0.4 gate",
)


def test_unified_fake_zoom_fireworks_to_human_declared_proof_and_handoff(monkeypatch):
    from playwright.sync_api import expect, sync_playwright

    handle = make_launch(monkeypatch)
    provider_children = fake_transport(monkeypatch)
    server = SourceNeutralPOCDemoServer(("127.0.0.1", 0), source_authoring_launch=handle)
    zoom_children, launched = install_fake(server)
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    output = os.environ.get("EXITSPEC_UNIFIED_DEMO_EVIDENCE")
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
                page.locator('input[name="first_source_choice"][value="MEETING"]').check()
                page.locator("#display-name").fill("Unified offline Zoom to proof demo")
                page.locator("#customer-label").fill("Synthetic demonstration customer")
                page.locator("#use-case").fill("Review human requirements and prove only the declared supported scope.")
                page.locator("#owner").fill("synthetic.demo.owner")
                page.locator("#create-poc").click()
                page.wait_for_url(re.compile(r"/app/pocs/poc_[a-z0-9_-]+/sources/new$"))
                poc = re.search(r"/pocs/(poc_[a-z0-9_-]+)/", page.url).group(1)
                server.zoom_live_runtime.pair(poc, replace(settings(), code_revision=revision))
                page.reload()
                expect(page.locator("#zoom-live-mode")).to_contain_text("Simulated transport")
                expect(page.locator("#zoom-live-start")).to_be_disabled()
                page.locator("#zoom-live-consent").check()
                page.locator("#zoom-live-start").click()
                assert launched.wait(2)
                child = zoom_children[-1]
                child.emit("offer")
                child.emit("listening")
                packet(child, data=("Response quality must satisfy the customer. "
                                    "The system must select the exact requested tool. "
                                    "Latency must remain visible to the customer."))
                expect(page.locator("#zoom-live-stop")).to_be_enabled()
                capture(page, "00-native-zoom-capture")
                page.locator("#zoom-live-stop").click()
                expect(page.locator("#zoom-live-status")).to_contain_text("Transport stop requested")
                child.emit("stop_ack")
                child.emit("drained")
                expect(page.locator("#zoom-live-process")).to_be_enabled()
                page.locator("#zoom-live-process").click()
                expect(page.locator("#zoom-live-review")).to_be_visible()
                assert len(server.draft_poc_service.ids()) == 1
                assert len(server.poc_source_intake.list_receipts(poc)) == 1
                zoom_receipt = server.zoom_live_runtime.receipt(poc)
                assert zoom_receipt["transport_mode"] == "FAKE_ZOOM_RTMS"
                assert zoom_receipt["measurement_validity"] == "NOT_ASSERTED"
                assert server.source_authoring_web.operations.ledger[0] == 0 and provider_children == []
                page.locator("#zoom-live-review").click()
                page.wait_for_url(base + f"/app/pocs/{poc}/review")
                source_receipt_id = server.proposal_review_service.list_proposals(poc)[0].source_receipt_id
                page.locator("#source-authoring-link").click()
                expect(page.locator("#source-mode-copy")).to_contain_text("fake credentials and local fake transport")
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
                capture(page, "01-explicit-offline-fireworks-disclosure")
                page.locator("#source-run").click()
                expect(page.locator("#source-review-result")).to_be_visible()
                expect(page.locator("#source-status")).to_contain_text("NEEDS_REVIEW")
                assert server.source_authoring_web.operations.ledger[0] == 1
                rows = server.proposal_review_service.list_proposals(poc)
                assert len(rows) == 3 and all(row.decision is None for row in rows)
                receipts = server.assisted_authoring_service.list_receipts(poc)
                assert len(receipts) == 1 and receipts[0].provider == "fireworks"
                assert len(provider_children) == 1 and provider_children[0].returncode == 0
                assert server.zoom_live_runtime._run_if_open.__self__ is server.generic_evidence_service.closure_service
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
                        "server": "SourceNeutralPOCDemoServer", "source": "native Zoom fake-transport transcript",
                        "candidate_revision": revision, "zoom_receipt": zoom_receipt,
                        "authoring": "Fireworks-shaped network-disabled fake subprocess; separate exact-source consent",
                        "provider_children": len(provider_children), "zoom_children": len(zoom_children),
                        "authoring_claims": 1, "named_human_decisions": 3,
                        "planning": "HUMAN_DECLARED: unsupported advisory, exact tool selection threshold, explicit exclusion",
                        "confirmation_and_freeze": "actual product pages",
                        "supported_proof_verdict": "PASS", "closure": closure,
                        "requests": requests, "page_errors": errors, "failed_responses": failures,
                        "limitations": ["Synthetic fixtures only; no provider call or spend", "Real Zoom/account/tokenizer/provider/billing/custody/region qualification remains unverified", "Unsupported/advisory/excluded criteria do not become proven", "No deployment, shipping or release authority"],
                    }, indent=2) + "\n")
            finally:
                browser.close()
    finally:
        server.shutdown()
        thread.join(timeout=5)
        assert not thread.is_alive()
        server.server_close()
