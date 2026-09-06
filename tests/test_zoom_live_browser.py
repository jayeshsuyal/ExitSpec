"""Native-mode fake-network acceptance; mandatory no-skip collection in release gate."""

from __future__ import annotations

import os
import threading
import time

import pytest

from exitspec.zoom_live_runtime import ZoomLiveRuntime
from tests.test_meeting_session_web_transport import _create_draft, _running_server
from tests.test_poc_performance_lifecycle_web_transport import _request
from tests.test_zoom_live_runtime import FakeChild, packet, settings
from tests.test_zoom_pr7_adversarial_e2e import _confirm_and_freeze

pytestmark = pytest.mark.skipif(
    os.environ.get("EXITSPEC_BROWSER_E2E") != "1",
    reason="native Chromium acceptance is mandatory under v0.4 release gate",
)


def install_fake(server):
    server.zoom_live_runtime.close()
    children = []
    launched = threading.Event()

    def factory(*args):
        child = FakeChild(*args)
        children.append(child)
        launched.set()
        return child

    server.zoom_live_runtime = ZoomLiveRuntime(
        drafts=server.draft_poc_service,
        intake=server.poc_source_intake,
        run_if_open=server.poc_closure_service.run_if_open,
        child_factory=factory,
        fake_network=True,
    )
    return children, launched


def test_native_capture_browser_to_human_confirmed_reference_evidence_pack(tmp_path):
    from playwright.sync_api import expect, sync_playwright

    with _running_server(tmp_path) as server, sync_playwright() as p:
        children, launched = install_fake(server)
        poc_id = _create_draft(server)
        server.zoom_live_runtime.pair(poc_id, settings())
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        base = f"http://127.0.0.1:{server.server_port}"
        page.goto(f"{base}/app/pocs/{poc_id}/sources/new")
        expect(page.locator("#zoom-live-mode")).to_contain_text("Simulated transport")
        expect(page.locator("#zoom-live-start")).to_be_disabled()
        page.locator("#zoom-live-consent").check()
        page.locator("#zoom-live-start").click()
        assert launched.wait(2)
        expect(page.locator("#zoom-live-status")).to_contain_text("Waiting")
        child = children[-1]
        child.emit("offer")
        child.emit("listening")
        packet(
            child,
            data="Criterion: p95 time to first token must stay below 730 milliseconds at concurrency 4. Contact alice@example.com.",
        )
        child.emit("interrupted")
        child.emit("reconnecting")
        child.emit("listening")
        packet(
            child,
            data="Criterion: p95 time to first token must stay below 730 milliseconds at concurrency 4. Contact alice@example.com.",
        )
        expect(page.locator("#zoom-live-stop")).to_be_enabled()
        page.locator("#zoom-live-stop").click()
        expect(page.locator("#zoom-live-process")).to_be_disabled()
        child.emit("stop_ack")
        packet(
            child,
            user_id=43,
            data="Criterion: error rate must remain below 0.7 percent.",
        )
        child.emit("drained")
        expect(page.locator("#zoom-live-process")).to_be_enabled()
        page.locator("#zoom-live-process").click()
        expect(page.locator("#zoom-live-review")).to_be_visible()
        assert len(server.poc_source_intake.list_receipts(poc_id)) == 1
        assert len(server.draft_poc_service.ids()) == 1
        assert not server.zoom_live_runtime._record.packets
        receipt = _request(
            server,
            "GET",
            f"/api/pocs/{poc_id}/zoom-live-receipt",
            content_type=None,
            origin=None,
        )
        assert receipt[0] == 200
        assert receipt[1]["transport_mode"] == "FAKE_ZOOM_RTMS"
        assert receipt[1]["measurement_validity"] == "NOT_ASSERTED"
        assert receipt[1]["code_revision"] == "a" * 40
        page.locator("#zoom-live-review").click()
        expect(page.locator("body")).to_contain_text("730")
        expect(page.locator("body")).not_to_contain_text("alice@example.com")
        proposals = _request(
            server,
            "GET",
            f"/api/pocs/{poc_id}/proposals",
            content_type=None,
            origin=None,
        )[1]["proposals"]
        assert len(proposals) == 2
        for index, proposal in enumerate(proposals):
            root = f"/api/pocs/{poc_id}"
            assert (
                _request(
                    server,
                    "POST",
                    f"{root}/proposals/{proposal['proposal_id']}/decision",
                    payload={
                        "decision": "KEEP_FOR_CONTRACT",
                        "reviewer": "human_test_reviewer",
                        "rationale": "Keep spoken requirement.",
                        "idempotency_key": f"native_keep_{index}",
                    },
                )[0]
                == 201
            )
            ttft = "first token" in proposal["normalized_claim"]
            assert (
                _request(
                    server,
                    "POST",
                    f"{root}/definitions",
                    payload={
                        "proposal_id": proposal["proposal_id"],
                        "metric": "TTFT_P95_MS" if ttft else "ERROR_RATE_PERCENT",
                        "operator": "LT",
                        "threshold": 730 if ttft else 0.7,
                        "minimum_samples": 100,
                        "concurrency": 4,
                        "prompt_tokens_min": 512,
                        "prompt_tokens_max": 4096,
                        "output_tokens_min": 64,
                        "output_tokens_max": 512,
                        "reviewer": "human_test_reviewer",
                        "rationale": "Bind the actual spoken threshold.",
                        "idempotency_key": f"native_define_{index}",
                    },
                )[0]
                == 201
            )
        _confirm_and_freeze(server, poc_id)
        assert (
            _request(
                server,
                "POST",
                f"/api/pocs/{poc_id}/runs",
                payload={
                    "execution_acknowledged": True,
                    "idempotency_key": "native_reference_run",
                },
            )[0]
            == 202
        )
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            latest = _request(
                server,
                "GET",
                f"/api/pocs/{poc_id}/runs/latest",
                content_type=None,
                origin=None,
            )[1]
            if latest["is_terminal"]:
                break
            time.sleep(0.05)
        assert latest["status"] == "COMPLETED"
        # Valid deterministic reference measurements only; no live/provider proof.
        assert latest["verdict"] == "PASS"
        page.goto(base + latest["evidence_pack_url"])
        expect(page.locator("body")).to_contain_text("PASS")
        assert not errors
        browser.close()


def test_native_browser_stop_failure_and_reset_never_create_proposals(tmp_path):
    from playwright.sync_api import expect, sync_playwright

    with _running_server(tmp_path) as server, sync_playwright() as p:
        children, launched = install_fake(server)
        poc_id = _create_draft(server)
        server.zoom_live_runtime.pair(poc_id, settings())
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(
            f"http://127.0.0.1:{server.server_port}/app/pocs/{poc_id}/sources/new"
        )
        page.locator("#zoom-live-consent").check()
        page.locator("#zoom-live-start").click()
        assert launched.wait(2)
        expect(page.locator("#zoom-live-status")).to_contain_text("Waiting")
        child = children[-1]
        child.emit("offer")
        child.emit("listening")
        packet(child)
        expect(page.locator("#zoom-live-stop")).to_be_enabled()
        page.locator("#zoom-live-stop").click()
        child.emit("drained")  # No authenticated stop acknowledgement.
        expect(page.locator("#zoom-live-status")).to_contain_text("failed")
        expect(page.locator("#zoom-live-process")).to_be_disabled()
        assert not server.poc_source_intake.list_receipts(poc_id)
        page.locator("#zoom-live-reset").click()
        expect(page.locator("#zoom-live-status")).to_contain_text("revoked")
        child.emit("listening")
        assert server.zoom_live_runtime.current(poc_id)["state"] == "REVOKED"
        browser.close()


def test_native_browser_persisted_lifecycle_revalidates_operator_session(tmp_path):
    """Native browser event dispatch proves restoration logic, not BFCache admission."""
    from playwright.sync_api import expect, sync_playwright

    with _running_server(tmp_path) as server, sync_playwright() as p:
        children, launched = install_fake(server)
        poc_id = _create_draft(server)
        original = server.zoom_live_runtime.pair(poc_id, settings())["session_id"]
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        base = f"http://127.0.0.1:{server.server_port}"
        endpoint = f"{base}/api/pocs/{poc_id}/zoom-live"
        page.goto(f"{base}/app/pocs/{poc_id}/sources/new")
        page.locator("#zoom-live-consent").check()
        expect(page.locator("#zoom-live-start")).to_be_enabled()

        def leave():
            page.evaluate(
                "window.dispatchEvent(new PageTransitionEvent('pagehide', {persisted:true}))"
            )
            expect(page.locator("#zoom-live-consent")).not_to_be_checked()
            expect(page.locator("#zoom-live-start")).to_be_disabled()
            expect(page.locator("#zoom-live-stop")).to_be_disabled()
            expect(page.locator("#zoom-live-mode")).to_have_text("Connection unverified")

        def restore():
            with page.expect_response(
                lambda response: response.url == endpoint
                and response.request.method == "GET"
            ) as response:
                page.evaluate(
                    "window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted:true}))"
                )
            assert response.value.status == 200
            return response.value.json()

        leave()
        replacement = server.zoom_live_runtime.pair(poc_id, settings())["session_id"]
        assert replacement != original
        assert restore()["session_id"] == replacement
        expect(page.locator("#zoom-live-status")).to_contain_text("Operator paired")
        expect(page.locator("#zoom-live-consent")).not_to_be_checked()
        expect(page.locator("#zoom-live-start")).to_be_disabled()

        page.locator("#zoom-live-consent").check()
        with page.expect_request(
            lambda request: request.url == endpoint and request.method == "POST"
        ) as start:
            page.locator("#zoom-live-start").click()
        assert start.value.post_data_json["session_id"] == replacement
        assert launched.wait(2)
        child = children[-1]
        child.emit("offer")
        child.emit("listening")
        expect(page.locator("#zoom-live-stop")).to_be_enabled()

        leave()
        assert restore()["state"] == "LISTENING"
        expect(page.locator("#zoom-live-stop")).to_be_enabled()
        # Confirm restored polling continues, independent of the immediate GET.
        child.emit("interrupted")
        expect(page.locator("#zoom-live-status")).to_contain_text("interrupted")
        page.locator("#zoom-live-stop").click()
        expect(page.locator("#zoom-live-status")).to_contain_text("Transport stop requested")
        assert server.zoom_live_runtime.current(poc_id)["state"] == "STOP_REQUESTED"

        leave()
        expires = server.zoom_live_runtime._record.expires
        server.zoom_live_runtime._clock = lambda: expires + 1
        server.zoom_live_runtime.tick()
        assert restore()["state"] == "REVOKED"
        expect(page.locator("#zoom-live-status")).to_contain_text("revoked")
        expect(page.locator("#zoom-live-consent")).not_to_be_checked()
        for action in ("start", "stop", "process"):
            expect(page.locator(f"#zoom-live-{action}")).to_be_disabled()
        expect(page.locator("#zoom-live-review")).to_be_hidden()
        with page.expect_response(
            lambda response: response.url == endpoint and response.request.method == "GET"
        ) as refreshed:
            page.locator("#zoom-live-refresh").click()
        assert refreshed.value.json()["failure_code"] == "TIMEOUT"
        assert not server.poc_source_intake.list_receipts(poc_id)
        assert not errors
        browser.close()
