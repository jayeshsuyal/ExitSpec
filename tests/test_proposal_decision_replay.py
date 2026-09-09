"""Committed decisions replay through current-scope HTTP and actual browser retry."""

from __future__ import annotations

import json
import os

import pytest

from exitspec.poc_proposal_web_api import handle_poc_proposal_web_api_request
from tests import test_source_authoring_web as source
from tests.test_poc_proposal_web_api import POC_ID, ROOT, _services
from tests.test_poc_proposal_web_transport import _request

rig = source.rig
BROWSER = pytest.mark.skipif(
    os.environ.get("EXITSPEC_BROWSER_E2E") != "1",
    reason="actual response-loss browser controls are mandatory in the v0.4 gate",
)


def _queue(rig, origin="a2"):
    if origin == "a3":
        source.publish_for_review(rig)
    rig.server.poc_source_intake.capture_source(
        poc_id=source.POC,
        source=source.POCSourceInput(
            source_kind=source.SourceKind.DOCUMENT,
            content="TTFT must remain below 500 ms.",
        ),
        idempotency_key="second-replay-source",
    )
    route = f"/api/pocs/{source.POC}/proposals"
    status, listed, _ = _request(rig.server, "GET", route, content_type=None, origin=None)
    assert status == 200 and len(listed["proposals"]) == 2
    return route, listed


def _body(decision="DISCARD"):
    return {
        "decision": decision,
        "reviewer": "Named retry reviewer",
        "rationale": "Triage this exact synthetic source proposal once.",
        "idempotency_key": "committed-decision-retry",
    }


@pytest.mark.parametrize("origin", ["a2", "a3"])
@pytest.mark.parametrize("decision", ["KEEP_FOR_CONTRACT", "DISCARD"])
def test_exact_committed_decision_http_replay_keeps_one_immutable_receipt(
    rig, origin, decision
):
    route, listed = _queue(rig, origin)
    proposal = listed["proposals"][0]["proposal_id"]
    target, body = f"{route}/{proposal}/decision", _body(decision)
    assert _request(rig.server, "POST", target, payload=body)[0] == 201
    owner = rig.server.proposal_review_service
    receipt = owner._decisions[(source.POC, proposal)]
    for _ in range(2):
        status, result, _ = _request(rig.server, "POST", target, payload=body)
        assert status == 200 and result["disposition"] == "IDEMPOTENT_REPLAY"
        assert owner._decisions[(source.POC, proposal)] is receipt and len(owner) == 1
    status, alias, _ = _request(
        rig.server, "POST", target,
        payload=dict(body, idempotency_key="same-decision-new-key"),
    )
    assert status == 200 and alias["disposition"] == "DECISION_REPLAY"
    assert owner._decisions[(source.POC, proposal)] is receipt and len(owner) == 1
    _, after, _ = _request(rig.server, "GET", route, content_type=None, origin=None)
    assert after["review_summary"] == {
        "total": 2, "needs_review": 1,
        "kept_for_contract": int(decision == "KEEP_FOR_CONTRACT"),
        "discarded": int(decision == "DISCARD"),
    }


@pytest.mark.parametrize("conflict", ["decision", "reviewer", "rationale", "proposal", "extra"])
def test_committed_replay_does_not_weaken_request_or_key_conflicts(rig, conflict):
    route, listed = _queue(rig)
    proposal = listed["proposals"][0]["proposal_id"]
    target, body = f"{route}/{proposal}/decision", _body()
    assert _request(rig.server, "POST", target, payload=body)[0] == 201
    owner = rig.server.proposal_review_service
    receipt = owner._decisions[(source.POC, proposal)]
    changed = dict(body)
    if conflict == "proposal":
        target = f"{route}/{listed['proposals'][1]['proposal_id']}/decision"
    elif conflict == "decision":
        changed[conflict] = "KEEP_FOR_CONTRACT"
    elif conflict == "extra":
        changed["scope_override"] = True
    else:
        changed[conflict] = "Different human request"
    status, _, _ = _request(rig.server, "POST", target, payload=changed)
    assert status == (400 if conflict == "extra" else 409)
    assert owner._decisions[(source.POC, proposal)] is receipt and len(owner) == 1
    # A different key cannot replace an immutable decision either.
    if conflict in {"decision", "reviewer", "rationale"}:
        changed["idempotency_key"] = "conflicting-new-key"
        assert _request(rig.server, "POST", target, payload=changed)[0] == 409
        assert owner._decisions[(source.POC, proposal)] is receipt


@pytest.mark.parametrize("scope", ["excluded", "other", "wrong_poc", "duplicate", "unavailable", "replaced"])
def test_committed_replay_still_requires_current_source_poc_and_agreement_scope(scope):
    _, _, owner = _services()
    rows = owner.list_proposals(POC_ID)
    target = f"{ROOT}/{rows[0].proposal_id}/decision"
    body = _body()

    def handle(lookup):
        return handle_poc_proposal_web_api_request(
            method="POST", target=target, payload=body, runtime=owner,
            current_proposal_lookup=lookup,
        )

    assert handle(owner.list_proposals).status == 201
    receipt = owner._decisions[(POC_ID, rows[0].proposal_id)]

    def current(_):
        if scope == "excluded":
            return ()
        if scope == "other":
            return (rows[1],)
        if scope == "wrong_poc":
            return (rows[0].model_copy(update={"poc_id": "poc_other_scope"}),)
        if scope == "duplicate":
            return (rows[0], rows[0])
        if scope == "unavailable":
            raise RuntimeError("private lookup details")
        with owner.authoring_commit_guard(POC_ID, rows[0].source_receipt_id) as guard:
            guard.prepare(["prop_replaced_source_requirement"])
            guard.commit()
        return rows

    result = handle(current)
    assert result.status in {404, 409, 503}
    assert "private lookup details" not in repr(result.payload)
    assert owner._decisions[(POC_ID, rows[0].proposal_id)] is receipt and len(owner) == 1


@BROWSER
@pytest.mark.parametrize("origin", ["a2", "a3"])
@pytest.mark.parametrize("selection", [0, 1])
@pytest.mark.parametrize("decision", ["KEEP_FOR_CONTRACT", "DISCARD"])
def test_browser_commits_then_loses_response_and_retries_exact_attempt(
    rig, origin, selection, decision
):
    from playwright.sync_api import expect, sync_playwright

    route, listed = _queue(rig, origin)
    proposal = listed["proposals"][selection]["proposal_id"]
    base = f"http://127.0.0.1:{rig.server.server_port}"
    requests, results = [], []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        try:
            def commit_then_drop(intercept):
                requests.append(intercept.request.post_data)
                response = intercept.fetch()
                results.append((response.status, response.json()))
                if len(requests) == 1:
                    assert response.status == 201
                    intercept.abort("failed")
                else:
                    intercept.fulfill(response=response)

            page.route(f"**{route}/{proposal}/decision", commit_then_drop)
            page.goto(base + f"/app/pocs/{source.POC}/review")
            page.locator("#proposal-tabs button").nth(selection).click()
            page.locator("#review-start").click()
            page.locator("#reviewer").fill("Named browser retry reviewer")
            page.locator("#rationale").fill("Retain this exact attempt after its response is lost.")
            button = page.locator("#keep-proposal" if decision == "KEEP_FOR_CONTRACT" else "#discard-proposal")
            button.click()
            expect(page.locator("#proposal-review-error")).to_be_visible()
            expect(button).to_be_enabled()
            expect(button).to_contain_text("Retry")
            expect(page.locator("#reviewer")).to_be_disabled()
            owner = rig.server.proposal_review_service
            receipt = owner._decisions[(source.POC, proposal)]
            assert len(owner) == 1
            button.click()
            expect(page.locator("#progress-bar")).to_have_attribute("aria-valuenow", "1")
            expect(page.locator("#proposal-review-error")).to_be_hidden()
            assert len(requests) == 2 and requests[0] == requests[1]
            assert results[1][0] == 200
            assert results[1][1]["disposition"] == "IDEMPOTENT_REPLAY"
            assert owner._decisions[(source.POC, proposal)] is receipt and len(owner) == 1
            assert receipt.decision.value == decision
        finally:
            browser.close()


@BROWSER
@pytest.mark.parametrize("fault", ["claim", "decision", "origin", "missing"])
@pytest.mark.parametrize("origin", ["a2", "a3"])
def test_acknowledged_selected_binding_survives_current_controller_reconciliation(rig, fault, origin):
    from playwright.sync_api import expect, sync_playwright

    route, listed = _queue(rig, origin)
    # Select the non-first A2 row; A3 selection uses the first real A3 row.
    selection = 1 if origin == "a2" else 0
    proposal = listed["proposals"][selection]["proposal_id"]
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        try:
            def contradict_acknowledgement(intercept):
                response = intercept.fetch()
                payload = response.json()
                if len(rig.server.proposal_review_service):
                    rows = payload["authoring_provenance"]["proposals"]
                    row = next(item for item in rows if item["proposal_id"] == proposal)
                    if fault == "claim":
                        row["normalized_claim"] += " changed after acknowledgement"
                    elif fault == "decision":
                        row["review_state"] = "DISCARD"
                        payload["review_summary"]["kept_for_contract"] -= 1
                        payload["review_summary"]["discarded"] += 1
                    elif fault == "origin":
                        row["origin"] = "ASSISTED_A3" if origin == "a2" else "INTAKE_A2"
                    else:
                        rows.remove(row)
                        payload["review_summary"]["total"] -= 1
                        payload["review_summary"]["kept_for_contract"] -= 1
                intercept.fulfill(response=response, body=json.dumps(payload))

            page.route(f"**{route}", contradict_acknowledgement)
            page.goto(f"http://127.0.0.1:{rig.server.server_port}/app/pocs/{source.POC}/review")
            page.locator("#proposal-tabs button").nth(selection).click()
            page.locator("#review-start").click()
            page.locator("#reviewer").fill("Named binding reviewer")
            page.locator("#rationale").fill("Keep the exact selected source proposal.")
            page.locator("#keep-proposal").click()
            expect(page.locator("#proposal-review-error")).to_contain_text("Reload before continuing")
            expect(page.locator("#review-start")).to_be_disabled()
            expect(page.locator("#keep-proposal")).to_be_disabled()
            expect(page.locator("#discard-proposal")).to_be_disabled()
            assert len(rig.server.proposal_review_service) == 1
        finally:
            browser.close()
