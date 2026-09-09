"""Completion counters agree with real, reconciled proposal-review totals."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from exitspec import web
from tests.test_browser_new_id_flow import EMAIL_TEXT
from tests.test_poc_proposal_web_transport import (
    _create_draft,
    _request,
    _running_server,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("EXITSPEC_BROWSER_E2E") != "1",
    reason="set EXITSPEC_BROWSER_E2E=1 for proposal completion browser checks",
)


def _listed(queue):
    status, payload, _ = _request(
        queue.server, "GET", queue.api_path, content_type=None, origin=None
    )
    assert status == 200
    return payload


@pytest.fixture
def queue(tmp_path, request):
    from playwright.sync_api import sync_playwright

    source_root = Path(__file__).resolve().parents[1] / "src"
    assert Path(web.__file__).resolve().is_relative_to(source_root.resolve())
    with _running_server(tmp_path) as server, sync_playwright() as runtime:
        poc_id = _create_draft(server)
        if getattr(request, "param", "populated") != "empty":
            status, capture, _ = _request(
                server,
                "POST",
                f"/api/pocs/{poc_id}/sources/email-text",
                payload={
                    "email_text": EMAIL_TEXT,
                    "idempotency_key": "completion-source",
                },
            )
            assert status == 201 and capture["proposal_count"] == 3
        base = f"http://127.0.0.1:{server.server_port}"
        browser = runtime.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        context.route(
            "**/*",
            lambda route: (
                route.continue_()
                if route.request.url.startswith(base + "/")
                else route.abort()
            ),
        )
        page = context.new_page()
        page.set_default_timeout(10_000)
        errors, posts = [], []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on(
            "request",
            lambda item: (
                posts.append((item.url, item.post_data))
                if item.method == "POST"
                else None
            ),
        )
        rig = SimpleNamespace(
            server=server,
            page=page,
            poc_id=poc_id,
            posts=posts,
            api_path=f"/api/pocs/{poc_id}/proposals",
            api=f"{base}/api/pocs/{poc_id}/proposals",
            url=f"{base}/app/pocs/{poc_id}/review",
        )
        destination = None
        if artifacts := os.environ.get("EXITSPEC_COMPLETION_ARTIFACTS"):
            destination = Path(artifacts) / re.sub(
                r"[^a-zA-Z0-9_-]", "_", request.node.name
            )
            destination.mkdir(parents=True, exist_ok=True)
            context.tracing.start(screenshots=True, snapshots=True, sources=True)
        try:
            yield rig
            assert errors == []
            assert all(url.endswith("/decision") for url, _ in posts)
        finally:
            if destination:
                page.screenshot(path=str(destination / "final.png"), full_page=True)
                (destination / "server.json").write_text(
                    json.dumps(_listed(rig), indent=2) + "\n"
                )
                context.tracing.stop(path=str(destination / "trace.zip"))
            context.close()
            browser.close()


def _assert_progress(queue, payload):
    from playwright.sync_api import expect

    summary = payload["review_summary"]
    total = summary["total"]
    reviewed = summary["kept_for_contract"] + summary["discarded"]
    remaining = summary["needs_review"]
    assert reviewed + remaining == total
    assert len(payload["proposals"]) == remaining
    copy = (
        "No proposals to review"
        if total == 0
        else f"{reviewed} reviewed · {remaining} awaiting triage"
    )
    expect(queue.page.locator("#progress-copy")).to_have_text(copy)
    bar = queue.page.locator("#progress-bar")
    expect(bar).to_have_attribute("aria-valuemin", "0")
    expect(bar).to_have_attribute("aria-valuemax", str(total or 1))
    expect(bar).to_have_attribute("aria-valuenow", str(reviewed))
    percent = 100 if total == 0 else int(100 * reviewed / total + 0.5)
    assert (
        queue.page.locator("#progress-fill").evaluate("node => node.style.width")
        == f"{percent}%"
    )
    queue.page.wait_for_function(
        "percent => { const track = document.querySelector('#progress-bar').getBoundingClientRect(); "
        "const fill = document.querySelector('#progress-fill').getBoundingClientRect(); "
        "return track.width > 0 && Math.abs(100 * fill.width / track.width - percent) < 1; }",
        arg=percent,
    )


def _decide(queue, decision):
    page = queue.page
    page.locator("#review-start").click()
    page.locator("#reviewer").fill("Completion reviewer")
    page.locator("#rationale").fill(
        "Triage this exact source proposal for the counter regression."
    )
    page.locator(
        "#keep-proposal" if decision == "KEEP_FOR_CONTRACT" else "#discard-proposal"
    ).click()


def _assert_completed(queue, payload):
    from playwright.sync_api import expect

    summary = payload["review_summary"]
    expect(queue.page.locator("#review-complete")).to_be_visible()
    assert summary["needs_review"] == 0
    _assert_progress(queue, payload)
    expect(queue.page.locator("#proposal-current-task")).to_be_hidden()
    if summary["total"]:
        expect(queue.page.locator("#review-complete-summary")).to_have_text(
            f"{summary['total']} proposals reviewed: {summary['kept_for_contract']} "
            f"kept for contract authoring and {summary['discarded']} discarded. "
            "No contract was created or approved."
        )
    else:
        expect(queue.page.locator("#review-complete-summary")).to_have_text(
            "There are no source proposals awaiting review. No contract was created or approved."
        )


@pytest.mark.parametrize("first_decision", ["DISCARD", "KEEP_FOR_CONTRACT"])
def test_final_discard_updates_all_counters_from_reconciled_totals(
    queue, first_decision
):
    from playwright.sync_api import expect

    queue.page.goto(queue.url)
    _assert_progress(queue, _listed(queue))
    for index, decision in enumerate((first_decision, "DISCARD", "DISCARD")):
        _decide(queue, decision)
        if index < 2:
            expect(queue.page.locator("#proposal-tabs .proposal-tab")).to_have_count(
                2 - index
            )
            _assert_progress(queue, _listed(queue))
        else:
            expect(queue.page.locator("#review-complete")).to_be_visible()
    recorded = _listed(queue)
    assert recorded["review_summary"] == {
        "total": 3,
        "needs_review": 0,
        "kept_for_contract": int(first_decision == "KEEP_FOR_CONTRACT"),
        "discarded": 3 - int(first_decision == "KEEP_FOR_CONTRACT"),
    }
    _assert_completed(queue, recorded)
    assert len(queue.posts) == len(queue.server.proposal_review_service) == 3
    queue.page.reload()
    _assert_completed(queue, recorded)
    assert _listed(queue) == recorded


@pytest.mark.parametrize("queue", ["empty"], indirect=True)
def test_initial_empty_queue_has_complete_counter_projection(queue):
    recorded = _listed(queue)
    assert recorded["review_summary"] == {
        "total": 0,
        "needs_review": 0,
        "kept_for_contract": 0,
        "discarded": 0,
    }
    queue.page.goto(queue.url)
    _assert_completed(queue, recorded)
    queue.page.reload()
    _assert_completed(queue, recorded)
    assert queue.posts == []


def test_previously_completed_queue_loads_and_reloads_authoritative_totals(queue):
    for proposal in _listed(queue)["proposals"]:
        status, _, _ = _request(
            queue.server,
            "POST",
            f"{queue.api_path}/{proposal['proposal_id']}/decision",
            payload={
                "decision": "DISCARD",
                "reviewer": "Completion reviewer",
                "rationale": "Discard before opening the review page.",
                "idempotency_key": f"complete-before-open-{proposal['proposal_id']}",
            },
        )
        assert status == 201
    recorded = _listed(queue)
    assert recorded["review_summary"]["discarded"] == 3
    queue.page.goto(queue.url)
    _assert_completed(queue, recorded)
    queue.page.reload()
    _assert_completed(queue, recorded)
    assert _listed(queue) == recorded
    assert queue.posts == []


def test_failed_final_reconciliation_preserves_old_counts_until_reload(queue):
    from playwright.sync_api import expect

    queue.page.goto(queue.url)
    for remaining in (2, 1):
        _decide(queue, "DISCARD")
        expect(queue.page.locator("#proposal-tabs .proposal-tab")).to_have_count(
            remaining
        )
    last_validated = _listed(queue)
    _assert_progress(queue, last_validated)
    queue.page.route(queue.api, lambda route: route.abort("connectionfailed"))
    _decide(queue, "DISCARD")
    expect(queue.page.locator("#proposal-review-error")).to_contain_text(
        "decision was recorded"
    )
    expect(queue.page.locator("#review-complete")).to_be_hidden()
    expect(queue.page.locator("#discard-proposal")).to_be_disabled()
    _assert_progress(queue, last_validated)
    recorded = _listed(queue)
    assert recorded["review_summary"]["discarded"] == 3
    assert recorded["review_summary"]["needs_review"] == 0
    queue.page.unroute(queue.api)
    queue.page.reload()
    _assert_completed(queue, recorded)
    assert _listed(queue) == recorded
    assert len(queue.posts) == len(queue.server.proposal_review_service) == 3
