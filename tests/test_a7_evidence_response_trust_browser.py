"""Adversarial browser checks for A7 generic-evidence response trust."""

from __future__ import annotations

import hashlib
import json
import threading
from contextlib import contextmanager

import pytest

from exitspec.canonical import canonical_json_bytes
from exitspec.poc_creation import DraftPOCCreateRequest
from exitspec.poc_source_demo import SourceNeutralPOCDemoServer

playwright_sync = pytest.importorskip("playwright.sync_api")
POC_ID = "poc_a7_untrusted_evidence"
VALID_ATTEMPT_ID = "eatm_" + "a" * 32
VALID_OPERATION_ID = "prun_" + "1" * 32
VALID_RUN_ID = "run_" + "2" * 32


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
        "operation_id": VALID_OPERATION_ID,
        "run_id": VALID_RUN_ID,
        "poc_id": POC_ID,
        "contract_id": "contract-a7-adversarial",
        "contract_version": "1",
        "contract_hash": "b" * 64,
        "request_digest": "c" * 64,
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
        "reserved_at": "2026-08-30T08:00:00+00:00",
        "terminal_at": "2026-08-30T08:00:01+00:00",
        "is_current": True,
        "shipping_authorized": False,
    }


def _evidence_binding(attempt: dict) -> dict:
    return {
        "poc_id": attempt["poc_id"],
        "contract_id": attempt["contract_id"],
        "contract_version": attempt["contract_version"],
        "contract_hash": attempt["contract_hash"],
        "run_id": attempt["run_id"],
        "verdict": attempt["reduction"]["verdict"],
        "evidence_pack_url": attempt["evidence_pack_url"],
        "evidence_pack_sha256": attempt["evidence_pack_sha256"],
    }


def _terminal_run_binding(attempt: dict) -> dict:
    return {
        "poc_id": attempt["poc_id"],
        "contract_id": attempt["contract_id"],
        "contract_version": attempt["contract_version"],
        "contract_hash": attempt["contract_hash"],
        "operation_id": attempt["operation_id"],
        "runner_run_id": attempt["run_id"],
        "runner_input_digest": attempt["request_digest"],
        "run_status": "BLOCKED",
        "reason_code": attempt["status"],
        "terminal_at": attempt["terminal_at"],
        "run_receipt_sha256": "e" * 64,
    }


def _binding_digest(binding: dict, *, evidence: bool) -> str:
    domain = (
        b"exitspec-terminal-evidence-binding-v1\x00"
        if evidence
        else b"exitspec-terminal-run-binding-v1\x00"
    )
    return hashlib.sha256(domain + canonical_json_bytes(binding)).hexdigest()


def _closure(
    *,
    evidence_binding: dict | None,
    terminal_run_binding: dict | None,
    decision: str = "HANDOFF_COMPLETED",
) -> dict:
    binding = evidence_binding or terminal_run_binding
    return {
        "closure_id": "poccl_" + "3" * 32,
        "poc_id": POC_ID,
        "decision": decision,
        "decided_by": "synthetic.attacker",
        "rationale": "Unbound synthetic closure.",
        "recorded_at": "2026-08-30T08:00:02+00:00",
        "evidence_binding": evidence_binding,
        "terminal_run_binding": terminal_run_binding,
        "evidence_binding_sha256": (
            "f" * 64
            if binding is None
            else _binding_digest(binding, evidence=evidence_binding is not None)
        ),
        "authorization_scope": "POC_LIFECYCLE_ONLY",
        "shipping_authorized": False,
    }


def _snapshot(attempt: dict, *, closure: dict | None) -> dict:
    return {
        "poc_id": POC_ID,
        "current": attempt,
        "history": [dict(attempt)],
        "closure": closure,
        "shipping_authorized": False,
        "authorization": "Evidence never authorizes deployment.",
    }


def _assert_untrusted_snapshot(base_url, playwright, payload, *forbidden_values):
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1280, "height": 720})
    api_url = f"{base_url}/api/pocs/{POC_ID}/evidence"
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
        assert page.locator("#evidence-current-status").inner_text() == "NOT STARTED"
        assert page.locator("#evidence-task-kicker").inner_text() == "Current task · Prove"
        assert page.locator("#evidence-task-heading").inner_text() == "Run the approved evidence method"
        assert "Human decision recorded" not in page.locator("body").inner_text()
        assert page.locator("#evidence-pack-link").is_hidden()
        assert page.locator("#evidence-pack-link").get_attribute("href") is None
        assert page.locator("#start-evidence").is_visible()
        assert page.locator("#start-evidence").is_disabled()
        assert page.locator("#handoff-evidence").is_hidden()
        assert page.locator("#stop-evidence").is_hidden()
        for forbidden in forbidden_values:
            assert forbidden not in page.locator("body").inner_text()
        page.locator("#evidence-acknowledged").check()
        assert page.locator("#start-evidence").is_disabled()
        page.locator("#start-evidence").dispatch_event("click")
        page.locator("#handoff-evidence").dispatch_event("click")
        page.locator("#stop-evidence").dispatch_event("click")
        assert mutations == []
        assert page.url == evidence_url
    finally:
        browser.close()


def _hostile_closure_cases():
    attempt = _attempt(
        attempt_id=VALID_ATTEMPT_ID,
        pack_url=f"/artifacts/{VALID_ATTEMPT_ID}/decision-packet.html",
    )
    binding = _evidence_binding(attempt)
    terminal_binding = _terminal_run_binding(attempt)
    cases = [
        pytest.param(
            {
                "poc_id": POC_ID,
                "decision": "HANDOFF_COMPLETED",
                "decided_by": "synthetic.attacker",
                "rationale": "Unbound synthetic closure.",
                "shipping_authorized": False,
            },
            id="fabricated-five-field-record",
        ),
        pytest.param(
            _closure(evidence_binding=None, terminal_run_binding=None),
            id="missing-terminal-binding",
        ),
        pytest.param(
            _closure(
                evidence_binding=dict(binding),
                terminal_run_binding=dict(terminal_binding),
                decision="POC_STOPPED",
            ),
            id="multiple-terminal-bindings",
        ),
        pytest.param(
            _closure(
                evidence_binding=None,
                terminal_run_binding=dict(terminal_binding),
                decision="POC_STOPPED",
            ),
            id="terminal-run-binding-impossible-for-current-pack",
        ),
    ]
    mismatches = {
        "poc-id": ("poc_id", "poc_other_untrusted_evidence"),
        "contract-id": ("contract_id", "contract-other"),
        "contract-version": ("contract_version", "2"),
        "contract-hash": ("contract_hash", "e" * 64),
        "run-id": ("run_id", "run_" + "4" * 32),
        "verdict": ("verdict", "FAIL"),
        "pack-url": (
            "evidence_pack_url",
            f"/artifacts/eatm_{'5' * 32}/decision-packet.html",
        ),
        "pack-digest": ("evidence_pack_sha256", "e" * 64),
    }
    for case_id, (field, value) in mismatches.items():
        changed_binding = dict(binding)
        changed_binding[field] = value
        cases.append(
            pytest.param(
                _closure(
                    evidence_binding=changed_binding,
                    terminal_run_binding=None,
                ),
                id=f"mismatched-{case_id}",
            )
        )
    extra_binding = dict(binding)
    extra_binding["attempt_id"] = VALID_ATTEMPT_ID
    cases.append(
        pytest.param(
            _closure(evidence_binding=extra_binding, terminal_run_binding=None),
            id="extra-evidence-binding-field",
        )
    )
    mismatched_digest = _closure(
        evidence_binding=dict(binding),
        terminal_run_binding=None,
    )
    mismatched_digest["evidence_binding_sha256"] = "f" * 64
    cases.append(pytest.param(mismatched_digest, id="mismatched-binding-digest"))
    extra_closure_field = _closure(
        evidence_binding=dict(binding),
        terminal_run_binding=None,
    )
    extra_closure_field["unexpected_authority"] = True
    cases.append(pytest.param(extra_closure_field, id="extra-closure-field"))
    return tuple(cases)


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
        attempt = _attempt(attempt_id=attempt_id, pack_url=pack_url)
        history_attempt = dict(attempt)
        if history_contract_hash is not None:
            history_attempt["contract_hash"] = history_contract_hash
        payload = _snapshot(attempt, closure=None)
        payload["history"] = [history_attempt]
        _assert_untrusted_snapshot(
            base_url,
            playwright,
            payload,
            pack_url,
            attempt_id,
        )


@pytest.mark.parametrize("malicious_closure", _hostile_closure_cases())
def test_unbound_or_mismatched_closure_fails_before_terminal_dom_mutation(
    malicious_closure,
):
    with _running_server() as base_url, playwright_sync.sync_playwright() as playwright:
        attempt = _attempt(
            attempt_id=VALID_ATTEMPT_ID,
            pack_url=f"/artifacts/{VALID_ATTEMPT_ID}/decision-packet.html",
        )
        _assert_untrusted_snapshot(
            base_url,
            playwright,
            _snapshot(attempt, closure=malicious_closure),
            "synthetic.attacker",
            "Unbound synthetic closure.",
        )
