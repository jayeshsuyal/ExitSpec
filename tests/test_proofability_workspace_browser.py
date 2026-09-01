"""Chromium proof for the narrow PR6 planning preflight projection."""

from __future__ import annotations

import json
import re
import threading
from contextlib import contextmanager
from pathlib import Path

import pytest

from exitspec.poc_creation import DraftPOCCreateRequest
from exitspec.poc_source_demo import SourceNeutralPOCDemoServer

playwright_sync = pytest.importorskip("playwright.sync_api")
PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATIC_ROOT = PROJECT_ROOT / "src" / "exitspec" / "static"
HTML_BYTES = (STATIC_ROOT / "proofability_workspace.html").read_bytes()


@contextmanager
def _running_server():
    server = SourceNeutralPOCDemoServer(("127.0.0.1", 0))
    server.draft_poc_service.create(
        DraftPOCCreateRequest(
            display_name="Browser proofability POC",
            customer_label="Synthetic label",
            use_case="Exercise the browser planning preflight.",
            owner="owner",
            first_source_choice="DOCUMENT",
            poc_id="poc_alpha",
        ),
        idempotency_key="draft-alpha",
    )
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield server, f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        worker.join(timeout=5)
        server.server_close()


def _assert_fixed_notices(page):
    text = page.locator(".limitations").inner_text()
    assert "package synthetic fixture" in text
    assert "not this POC or live inputs" in text
    assert "process-local" in text
    assert "lost on restart" in text
    assert "not shared across workers" in text
    assert "No deployment, production traffic, or traffic expansion is authorized" in text
    assert "External authorization remains required" in text


def test_browser_real_no_latest_fresh_applicable_and_text_only_projection():
    with _running_server() as (_server, base_url), playwright_sync.sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 720})
        console_errors: list[str] = []
        page.on(
            "console",
            lambda message: console_errors.append(message.text)
            if message.type == "error"
            else None,
        )
        try:
            page.goto(
                f"{base_url}/app/pocs/poc_alpha/qualification/proofability",
                wait_until="networkidle",
            )
            _assert_fixed_notices(page)
            assert page.locator("#workspace-status").inner_text() == (
                "No preflight exists for this POC."
            )
            button = page.get_by_role("button", name="Create preflight", exact=True)
            assert button.count() == 1
            assert page.locator("select").count() == 0
            button.click()
            page.locator(".criterion").wait_for(state="visible")
            assert page.locator("#workspace-status").inner_text() == (
                "The current package-synthetic preflight is available."
            )
            assert page.get_by_role("button", name="Create preflight").count() == 0
            assert "PROVABLE" in page.locator("#workspace-content").inner_text()
            assert "QUAL-TTFT-01" in page.locator("#workspace-content").inner_text()
            assert "ALL_REQUIRED_OBSERVATIONS_AVAILABLE" in (
                page.locator("#workspace-content").inner_text()
            )
            assert "NO_REMEDIATION_REQUIRED" in (
                page.locator("#workspace-content").inner_text()
            )
            semantic_values = set(
                re.findall(r"\b[A-Z][A-Z_]*\b", page.locator("body").inner_text())
            )
            assert semantic_values.isdisjoint(
                {
                    "PASS",
                    "FAIL",
                    "NOT_PROVEN",
                    "CURRENT",
                    "STALE",
                    "EXPIRED",
                    "INVALID",
                }
            )
            assert page.locator("#workspace-content [style]").count() == 0
            assert console_errors == []

            response = page.request.get(
                f"{base_url}/api/pocs/poc_alpha/qualification/proofability"
            )
            assert response.status == 200
            assert response.json()["report"] is not None
            assert response.json()["authority"] == {
                "deployment_authorized": False,
                "production_traffic_authorized": False,
                "traffic_expansion_authorized": False,
                "external_authorization_required": True,
            }
        finally:
            browser.close()


def test_browser_accepts_closed_replay_and_code_only_error_states():
    with _running_server() as (_, base_url), playwright_sync.sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        api = f"{base_url}/api/pocs/poc_alpha/qualification/proofability"
        empty = page.request.get(api).json()
        accepted = page.request.post(
            api,
            headers={
                "Content-Type": "application/json",
                "Origin": base_url,
            },
            data={
                "profile_id": "exitspec.external-evidence.native-ttft-profile.v1",
                "profile_version": "v1",
                "idempotency_key": "browser-replay",
            },
        ).json()
        accepted["idempotent_replay"] = True

        def replay_route(route):
            payload = accepted if route.request.method == "POST" else empty
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(payload),
            )

        page.route("**/api/pocs/poc_alpha/qualification/proofability", replay_route)
        try:
            page.goto(
                f"{base_url}/app/pocs/poc_alpha/qualification/proofability"
            )
            page.get_by_role("button", name="Create preflight", exact=True).click()
            page.locator(".criterion").wait_for(state="visible")
            assert page.locator("#workspace-status").inner_text() == (
                "The current package-synthetic preflight is available."
            )
            assert page.get_by_role("button", name="Create preflight").count() == 0

            page.unroute(
                "**/api/pocs/poc_alpha/qualification/proofability", replay_route
            )
            page.route(
                "**/api/pocs/poc_alpha/qualification/proofability",
                lambda route: route.fulfill(
                    status=503,
                    content_type="application/json",
                    body=json.dumps({"error_code": "WORKSPACE_UNAVAILABLE"}),
                ),
            )
            page.reload()
            page.wait_for_function(
                "document.querySelector('#workspace-status').textContent.includes('WORKSPACE_UNAVAILABLE')"
            )
            _assert_fixed_notices(page)
            assert page.locator("#workspace-content").inner_text() == ""
            assert page.get_by_role("button", name="Create preflight").count() == 0
        finally:
            browser.close()


def test_browser_transport_retry_reuses_key_and_double_click_is_suppressed():
    with _running_server() as (_, base_url), playwright_sync.sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        post_bodies: list[dict[str, object]] = []

        def route_post(route):
            if route.request.method != "POST":
                route.continue_()
                return
            post_bodies.append(json.loads(route.request.post_data))
            if len(post_bodies) == 1:
                route.abort("connectionfailed")
            else:
                route.continue_()

        page.route("**/api/pocs/poc_alpha/qualification/proofability", route_post)
        try:
            page.goto(
                f"{base_url}/app/pocs/poc_alpha/qualification/proofability",
                wait_until="networkidle",
            )
            button = page.get_by_role("button", name="Create preflight", exact=True)
            button.click()
            page.wait_for_function(
                "document.querySelector('#workspace-status').textContent.includes('Retry uses the same request identity')"
            )
            page.evaluate(
                """
                const button = document.querySelector('#workspace-action button');
                button.dispatchEvent(new MouseEvent('click', {bubbles: true}));
                button.dispatchEvent(new MouseEvent('click', {bubbles: true}));
                """
            )
            page.locator(".criterion").wait_for(state="visible")
            assert len(post_bodies) == 2
            assert post_bodies[0]["idempotency_key"] == post_bodies[1]["idempotency_key"]
            assert re.fullmatch(
                r"preflight-[a-f0-9]{32}", str(post_bodies[0]["idempotency_key"])
            )
        finally:
            browser.close()


@pytest.mark.parametrize(
    "suffix",
    [
        "/app/pocs/poc_%61lpha/qualification/proofability",
        "/app/pocs/poc_alpha/qualification/proofability/",
        "/app/pocs/poc_alpha/qualification/proofability/extra",
        "/app/pocs/poc_alpha/qualification/proofability?",
        "/app/pocs/poc_alpha/qualification/proofability?x=1",
        "/app/pocs/poc_alpha/qualification/proofability#",
        "/app/pocs/poc_alpha/qualification/proofability#x",
    ],
)
def test_browser_exact_serialized_url_gate_runs_before_fetch_or_dynamic_render(suffix):
    with _running_server() as (_, base_url), playwright_sync.sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        api_requests: list[str] = []
        page.on(
            "request",
            lambda request: api_requests.append(request.url)
            if "/api/pocs/" in request.url
            else None,
        )

        def serve_shell(route):
            route.fulfill(
                status=200,
                content_type="text/html; charset=utf-8",
                body=HTML_BYTES,
            )

        page.route("**/app/pocs/**/qualification/proofability**", serve_shell)
        try:
            page.goto(base_url + suffix)
            page.wait_for_timeout(100)
            _assert_fixed_notices(page)
            assert api_requests == []
            assert page.locator("#workspace-content").inner_text() == ""
            assert page.locator("#workspace-action").inner_text() == ""
            assert page.locator("#workspace-status").inner_text() == (
                "Waiting for an exact local workspace route."
            )
        finally:
            browser.close()


def test_browser_rejects_extra_authority_shape_and_hostile_values_before_render():
    with _running_server() as (_, base_url), playwright_sync.sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        hostile = "HOSTILE_CUSTOMER_PROVIDER_CREDENTIAL_PRIVATE_PATH_4A2E"
        payload = {
            "schema_version": "exitspec.proofability-workspace-response.v1",
            "poc_id": "poc_alpha",
            "report": None,
            "needs_replan": False,
            "reported_context_digest": None,
            "resolved_context_digest": "sha256:" + "1" * 64,
            "profile_request": {
                "profile_id": "exitspec.external-evidence.native-ttft-profile.v1",
                "profile_version": "v1",
            },
            "context_source": {
                "kind": "PACKAGE_SYNTHETIC_FIXTURE",
                "fixture_id": "exitspec.synthetic-proofability-preflight.native-v1",
                "fixture_version": "v1",
                "poc_derived": False,
            },
            "storage": {
                "scope": "PROCESS_LOCAL",
                "survives_process_restart": False,
                "shared_across_workers": False,
            },
            "authority": {
                "deployment_authorized": False,
                "production_traffic_authorized": False,
                "traffic_expansion_authorized": False,
                "external_authorization_required": True,
                "customer": hostile,
            },
        }
        page.route(
            "**/api/pocs/poc_alpha/qualification/proofability",
            lambda route: route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(payload),
            ),
        )
        try:
            page.goto(
                f"{base_url}/app/pocs/poc_alpha/qualification/proofability"
            )
            page.wait_for_function(
                "document.querySelector('#workspace-status').textContent.includes('UNUSABLE_RESPONSE')"
            )
            _assert_fixed_notices(page)
            assert hostile not in page.locator("body").inner_text()
            assert page.locator("#workspace-content").inner_text() == ""
            assert page.get_by_role("button", name="Create preflight").count() == 0
        finally:
            browser.close()


def test_browser_renders_drift_and_every_closed_planning_disposition_neutrally():
    with _running_server() as (_, base_url), playwright_sync.sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        api = f"{base_url}/api/pocs/poc_alpha/qualification/proofability"
        empty = page.request.get(api).json()
        drift = dict(empty)
        drift["needs_replan"] = True
        drift["reported_context_digest"] = "sha256:" + "9" * 64
        drift["resolved_context_digest"] = "sha256:" + "8" * 64
        responses = [drift]

        def route_api(route):
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(responses[-1]),
            )

        page.route("**/api/pocs/poc_alpha/qualification/proofability", route_api)
        try:
            page.goto(
                f"{base_url}/app/pocs/poc_alpha/qualification/proofability"
            )
            page.get_by_role("button", name="Create preflight", exact=True).wait_for()
            assert "active binding changed" in page.locator("#workspace-status").inner_text()
            page.unroute("**/api/pocs/poc_alpha/qualification/proofability", route_api)
            real_post = page.request.post(
                api,
                headers={
                    "Content-Type": "application/json",
                    "Origin": base_url,
                },
                data={
                    "profile_id": "exitspec.external-evidence.native-ttft-profile.v1",
                    "profile_version": "v1",
                    "idempotency_key": "browser-dispositions",
                },
            )
            assert real_post.status == 201
            rich = real_post.json()
            rich.pop("idempotent_replay")
            base_criterion = rich["report"]["criterion_results"][0]
            rich["report"]["criterion_results"] = []
            for index, disposition in enumerate(
                ["PROVABLE", "CLARIFICATION_REQUIRED", "NOT_PROVABLE"]
            ):
                criterion = json.loads(json.dumps(base_criterion))
                criterion["criterion_id"] = f"QUAL-BROWSER-{index}"
                criterion["disposition"] = disposition
                rich["report"]["criterion_results"].append(criterion)
            responses.append(rich)
            page.route("**/api/pocs/poc_alpha/qualification/proofability", route_api)
            for report_disposition in (
                "PROVABLE",
                "PARTIALLY_PROVABLE",
                "CLARIFICATION_REQUIRED",
                "NOT_PROVABLE",
            ):
                responses[-1]["report"]["overall_disposition"] = report_disposition
                page.reload()
                page.locator(".criterion").first.wait_for()
                rendered = page.locator("#workspace-content").inner_text()
                assert report_disposition in rendered
                for criterion_disposition in (
                    "PROVABLE",
                    "CLARIFICATION_REQUIRED",
                    "NOT_PROVABLE",
                ):
                    assert criterion_disposition in rendered
            assert page.locator("[class*='success'], [class*='pass'], [class*='fail']").count() == 0
        finally:
            browser.close()


def test_browser_source_uses_closed_text_only_dom_and_no_persistent_state_or_forbidden_actions():
    source = (STATIC_ROOT / "proofability_workspace.js").read_text(encoding="utf-8")
    html = (STATIC_ROOT / "proofability_workspace.html").read_text(encoding="utf-8")
    css = (STATIC_ROOT / "proofability_workspace.css").read_text(encoding="utf-8")
    for forbidden in (
        "innerHTML",
        "outerHTML",
        "insertAdjacentHTML",
        "document.write",
        "localStorage",
        "sessionStorage",
        "indexedDB",
        "caches.open",
        "eval(",
        "new Function",
    ):
        assert forbidden not in source
    assert 'button.textContent = "Create preflight"' in source
    assert "location.href !== location.origin + location.pathname" in source
    assert "createElement" in source
    assert "textContent" in source
    assert "profile selector" not in (source + html).lower()
    for action in ("Execute", "Export", "Approve", "Release", "Deploy"):
        assert f'>{action}<' not in html
        assert f'"{action}"' not in source
    assert "gradient" not in css
    assert "#000" not in css
