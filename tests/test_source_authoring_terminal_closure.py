"""Actual generic terminal owners fence source authoring; no resolver is forged."""

import os
import re
import threading
from types import SimpleNamespace

import pytest

from exitspec.poc_source_demo import SourceNeutralPOCDemoServer
from exitspec.poc_source_intake import POCSourceInput
from exitspec.poc_sources import SourceKind
from exitspec.source_authoring_operations import SourceAuthoringOperationError
from exitspec.source_authoring_owners import SourceAuthoringOwnersError
from exitspec.source_authoring_supervisor import SyntheticSourceAuthoringSupervisor
from exitspec.source_authoring_web import SourceAuthoringWebError
from exitspec.workspace_closure import POCClosureConflict, ProcessLocalPOCClosureService
from tests.test_a6_source_neutral_browser import _complete_mixed_plan

pytestmark = pytest.mark.skipif(
    os.environ.get("EXITSPEC_BROWSER_E2E") != "1",
    reason="real terminal-closure Chromium controls are mandatory in the v0.4 gate",
)
DECISIONS = ("HANDOFF_COMPLETED", "POC_STOPPED")
REFUSALS = (
    POCClosureConflict,
    SourceAuthoringWebError,
    SourceAuthoringOperationError,
    SourceAuthoringOwnersError,
)


@pytest.fixture
def completed():
    """Build real source/review/plan/confirmation/freeze/evidence on one server."""
    from playwright.sync_api import sync_playwright

    server = SourceNeutralPOCDemoServer(("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            try:
                page.goto(base + "/app/pocs/new")
                page.locator(
                    'input[name="first_source_choice"][value="DOCUMENT"]'
                ).check()
                page.locator("#display-name").fill("Actual terminal owner control")
                page.locator("#customer-label").fill("Synthetic customer")
                page.locator("#use-case").fill(
                    "Fence authoring against actual human closure."
                )
                page.locator("#owner").fill("closure.fixture.owner")
                page.locator("#create-poc").click()
                page.wait_for_url(re.compile(r"/app/pocs/poc_[a-z0-9_-]+/sources/new$"))
                poc = re.search(r"/pocs/(poc_[a-z0-9_-]+)/", page.url).group(1)
                page.locator("#document-text").fill(
                    "The response should be acceptable. The system must select the exact tool. "
                    "Latency should be visible to the customer. Production deployment remains excluded."
                )
                page.locator("#capture-source").click()
                page.wait_for_url(base + f"/app/pocs/{poc}/review")
                page.locator("#assisted-authoring-link").click()
                page.locator('input[name="source_receipt"]').check()
                page.locator("#authoring-submit").click()
                page.locator("#authoring-result").wait_for(state="visible")
                page.locator("#open-proposal-review").click()
                for index in range(3):
                    page.locator("#review-start").click()
                    page.locator("#reviewer").fill("closure.fixture.reviewer")
                    page.locator("#rationale").fill(
                        "Retain the current synthetic source claim."
                    )
                    page.locator("#keep-proposal").click()
                    page.wait_for_function(
                        "document.querySelector('#review-complete')?.hidden === false || "
                        f"document.querySelector('#proposal-heading')?.textContent !== 'Proposal {index + 1}'"
                    )
                page.locator("#review-complete").wait_for(state="visible")
                page.locator("#plan-capabilities").click()
                _complete_mixed_plan(page)
                page.locator("#open-agreement").click()
                page.locator("#assembly-reviewer").fill("closure.fixture.assembler")
                page.locator("#assembly-rationale").fill(
                    "Assemble the exact human-declared plan."
                )
                page.locator("#prepare-agreement").click()
                page.locator("#agreement-summary").wait_for(state="visible")
                page.locator("#open-customer-review").click()
                page.locator("#agreement-checkbox").check()
                page.locator("#review-rationale").fill(
                    "Confirm this exact synthetic agreement."
                )
                page.locator("#confirm-agreement").click()
                page.locator("#review-result").wait_for(state="visible")
                page.locator("#return-to-agreement").click()
                page.locator("#freeze-agreement").click()
                page.wait_for_function(
                    "document.querySelector('#agreement-status')?.textContent === 'FROZEN'"
                )
                page.locator("#open-evidence").click()
                page.locator("#evidence-acknowledged").check()
                page.locator("#start-evidence").click()
                page.wait_for_function(
                    "document.querySelector('#evidence-current-status')?.textContent === 'COMPLETED'"
                )
                snapshot = server.generic_evidence_service.snapshot_payload(poc)
                assert (
                    snapshot["closure"] is None
                    and snapshot["current"]["status"] == "COMPLETED"
                )
                assert page.locator("#evidence-result-verdict").inner_text() == "PASS"
                assert snapshot["current"]["evidence_pack_url"]
                yield SimpleNamespace(
                    server=server,
                    poc=poc,
                    page=page,
                    attempt=snapshot["current"]["attempt_id"],
                )
            finally:
                browser.close()
    finally:
        server.shutdown()
        thread.join(timeout=5)
        assert not thread.is_alive()
        server.server_close()


def capture(rig):
    return rig.server.poc_source_intake.capture_source(
        poc_id=rig.poc,
        source=POCSourceInput(
            source_kind=SourceKind.DOCUMENT,
            content="Criterion: p95 latency must stay below 650 milliseconds.",
        ),
        idempotency_key="fresh-authoring-source",
    ).source_receipt_id


def close_actual(rig, decision):
    service = rig.server.generic_evidence_service
    action = service.handoff if decision == "HANDOFF_COMPLETED" else service.stop
    result = action(
        rig.attempt,
        decided_by="closure.named.human",
        rationale="Review the actual current terminal evidence.",
        idempotency_key="real-closure-" + decision,
    )
    closure = service.snapshot_payload(rig.poc)["closure"]
    assert closure["decision"] == decision
    assert (
        closure["evidence_binding"]["run_id"]
        == service.snapshot_payload(rig.poc)["current"]["run_id"]
    )
    assert not closure["shipping_authorized"]
    return result


def prepared(rig, *, authorize=False):
    runtime = rig.server.source_authoring_web
    receipt = capture(rig)
    capability = runtime.request(rig.poc, "bootstrap", {})["capability"]
    preview = runtime.request(
        rig.poc, "prepare", {"source_receipt_id": receipt}, capability
    )
    operation = preview["operation_id"]
    assert preview["state"] == "PREPARED"
    if authorize:
        result = runtime.request(
            rig.poc, "authorize", authorization(operation), capability
        )
        assert result["state"] == "AUTHORIZED"
    return capability, operation


def authorization(operation):
    return {
        "operation_id": operation,
        "business_text": True,
        "acknowledged": True,
        "idempotency_key": "explicit-source-consent",
    }


def test_readonly_accessor_shares_exact_terminal_owner_and_bound_guard():
    server = SourceNeutralPOCDemoServer(("127.0.0.1", 0))
    try:
        owner = server.generic_evidence_service.closure_service
        assert type(owner) is ProcessLocalPOCClosureService
        assert server.poc_closure_service is owner
        assert server.source_authoring_web._closure is owner
        guard = server.source_authoring_web._owners._run_if_open
        assert (
            guard.__self__ is owner
            and guard.__func__ is ProcessLocalPOCClosureService.run_if_open
        )
        with pytest.raises(AttributeError):
            server.generic_evidence_service.closure_service = owner
    finally:
        server.server_close()


@pytest.mark.parametrize("decision", DECISIONS)
def test_real_terminal_browser_buttons_refuse_new_authoring(completed, decision):
    rig = completed
    rig.page.locator("#decision-owner").fill("closure.browser.human")
    rig.page.locator("#decision-rationale").fill(
        "Review the current immutable evidence pack."
    )
    rig.page.locator(
        "#handoff-evidence" if decision == "HANDOFF_COMPLETED" else "#stop-evidence"
    ).click()
    rig.page.wait_for_function(
        "document.querySelector('#evidence-task-heading')?.textContent === 'Human decision recorded'"
    )
    assert (
        rig.server.generic_evidence_service.snapshot_payload(rig.poc)["closure"][
            "decision"
        ]
        == decision
    )
    capture(rig)
    runtime = rig.server.source_authoring_web
    with pytest.raises(REFUSALS):
        runtime.request(rig.poc, "bootstrap", {})
    assert runtime._browsers == [] and runtime.operations.ledger[0] == 0


@pytest.mark.parametrize("decision", DECISIONS)
@pytest.mark.parametrize("phase", ["bootstrap", "capability", "prepared", "authorized"])
def test_actual_terminal_state_fences_each_authoring_entry(completed, decision, phase):
    rig = completed
    runtime = rig.server.source_authoring_web
    if phase == "capability":
        receipt = capture(rig)
        capability = runtime.request(rig.poc, "bootstrap", {})["capability"]
    elif phase in {"prepared", "authorized"}:
        capability, operation = prepared(rig, authorize=phase == "authorized")
    old_receipts = rig.server.assisted_authoring_service.list_receipts(rig.poc)
    close_actual(rig, decision)
    if phase == "bootstrap":
        capture(rig)
        with pytest.raises(REFUSALS):
            runtime.request(rig.poc, "bootstrap", {})
        assert runtime._browsers == []
    elif phase == "capability":
        with pytest.raises(REFUSALS):
            runtime.request(rig.poc, "sources", {}, capability)
        with pytest.raises(REFUSALS):
            runtime.request(
                rig.poc, "prepare", {"source_receipt_id": receipt}, capability
            )
    elif phase == "prepared":
        with pytest.raises(REFUSALS):
            runtime.request(rig.poc, "authorize", authorization(operation), capability)
    else:
        # Run can return its safe terminal tombstone; it cannot start execution.
        result = runtime.request(
            rig.poc, "run", {"operation_id": operation}, capability
        )
        assert result["state"] == "STALE" and result["attempts"] == 0
        assert result["authoring_receipt_id"] is None and not result["processing"]
    assert runtime.operations.ledger[0] == 0 and runtime._thread is None
    assert rig.server.assisted_authoring_service.list_receipts(rig.poc) == old_receipts


def test_open_actual_terminal_owner_allows_explicit_synthetic_run(completed):
    rig = completed
    runtime = rig.server.source_authoring_web
    capability, operation = prepared(rig, authorize=True)
    runtime.request(rig.poc, "run", {"operation_id": operation}, capability)
    runtime._thread.join(timeout=5)
    assert not runtime._thread.is_alive()
    result = runtime.request(rig.poc, "status", {"operation_id": operation}, capability)
    assert result["state"] == "SUCCEEDED" and result["attempts"] == 1
    assert result["authoring_receipt_id"]
    assert (
        rig.server.generic_evidence_service.snapshot_payload(rig.poc)["closure"] is None
    )


@pytest.mark.parametrize("decision", DECISIONS)
@pytest.mark.parametrize("point", ["pre_dispatch", "pre_commit"])
def test_actual_closure_wins_before_dispatch_or_late_publication(
    completed, monkeypatch, decision, point
):
    rig = completed
    runtime = rig.server.source_authoring_web
    capability, operation = prepared(rig, authorize=True)
    prior = rig.server.assisted_authoring_service.list_receipts(rig.poc)
    events, handoffs = [], []
    original_handoff = SyntheticSourceAuthoringSupervisor.handoff

    def handoff(worker):
        handoffs.append(True)
        return original_handoff(worker)

    def schedule(event):
        events.append(event)
        if event == point:
            close_actual(rig, decision)

    monkeypatch.setattr(SyntheticSourceAuthoringSupervisor, "handoff", handoff)
    monkeypatch.setattr(runtime.operations, "_schedule", schedule)
    runtime.request(rig.poc, "run", {"operation_id": operation}, capability)
    runtime._thread.join(timeout=5)
    assert not runtime._thread.is_alive()
    assert point in events and "committed" not in events
    assert len(handoffs) == (0 if point == "pre_dispatch" else 1)
    record = runtime.operations._records[operation]
    assert record.receipt.state == "STALE" and record.receipt.attempts == 1
    assert record.receipt.authoring_receipt_id is None
    assert record.source is None and record.body is None
    assert runtime.operations.ledger[0] == 1  # Claimed attempts are not refunded.
    assert rig.server.assisted_authoring_service.list_receipts(rig.poc) == prior


@pytest.mark.parametrize("decision", DECISIONS)
@pytest.mark.parametrize("point", ["bootstrap", "prepare"])
def test_real_owner_reservation_wins_race_until_mutation_finishes(
    completed, monkeypatch, decision, point
):
    rig = completed
    runtime = rig.server.source_authoring_web
    observed = []

    def try_closure():
        with pytest.raises(POCClosureConflict):
            close_actual(rig, decision)
        observed.append(True)
        assert (
            rig.server.generic_evidence_service.snapshot_payload(rig.poc)["closure"]
            is None
        )

    if point == "bootstrap":
        original = runtime.operations.new_synthetic_session

        def create():
            try_closure()
            return original()

        monkeypatch.setattr(runtime.operations, "new_synthetic_session", create)
        runtime.request(rig.poc, "bootstrap", {})
    else:
        receipt = capture(rig)
        capability = runtime.request(rig.poc, "bootstrap", {})["capability"]
        original = runtime._owners.run_guarded

        def guarded(snapshot, mutation):
            def invoke(guard):
                try_closure()
                return mutation(guard)

            return original(snapshot, invoke)

        monkeypatch.setattr(runtime._owners, "run_guarded", guarded)
        runtime.request(rig.poc, "prepare", {"source_receipt_id": receipt}, capability)
    assert observed
    assert rig.server.poc_closure_service._active_mutations == {}
    close_actual(rig, decision)


@pytest.mark.parametrize("point", ["bootstrap", "prepare"])
def test_exception_releases_real_owner_reservation(completed, monkeypatch, point):
    rig = completed
    runtime = rig.server.source_authoring_web

    def fail():
        raise SourceAuthoringOperationError("synthetic_test_failure")

    if point == "bootstrap":
        monkeypatch.setattr(runtime.operations, "new_synthetic_session", fail)
        action = lambda: runtime.request(rig.poc, "bootstrap", {})
    else:
        receipt = capture(rig)
        capability = runtime.request(rig.poc, "bootstrap", {})["capability"]
        original = runtime._owners.run_guarded

        def guarded(snapshot, _mutation):
            return original(snapshot, lambda _guard: fail())

        monkeypatch.setattr(runtime._owners, "run_guarded", guarded)
        action = lambda: runtime.request(
            rig.poc, "prepare", {"source_receipt_id": receipt}, capability
        )
    with pytest.raises(SourceAuthoringOperationError):
        action()
    assert rig.server.poc_closure_service._active_mutations == {}
    assert runtime.operations.ledger[0] == 0
    close_actual(rig, "HANDOFF_COMPLETED")
