"""Actual combined bootstrap with synthetic TTY, Zoom child and provider only."""

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from exitspec import poc_source_demo as demo
from exitspec import source_authoring_launch as launch
from exitspec import source_authoring_operator as operator
from exitspec import zoom_live_operator as zoom_operator
from exitspec.source_authoring_web import SourceAuthoringWebError
from exitspec.workspace_closure import POCClosureConflict
from exitspec.zoom_live_runtime import ZoomLiveError
from tests.helpers.source_authoring_admission import (
    fake_transport,
    install_fake_profile,
)
from tests.test_a6_source_neutral_browser import _complete_mixed_plan
from tests.test_meeting_session_web_transport import _create_draft
from tests.test_zoom_live_runtime import FakeChild, packet, settings


@pytest.fixture
def combined(monkeypatch, tmp_path, request):
    demo_mode = getattr(request, "param", False)
    if demo_mode:
        from tests.helpers.source_authoring_demo import install_demo_profile
        profile, approval_path, approval_digest = install_demo_profile(monkeypatch, tmp_path)
    else:
        profile = install_fake_profile(monkeypatch)
    state = SimpleNamespace(servers=[], zooms=[], children=[], handles=[], now=0,
                            mode=None, ending=None, prompts=[], stop=None, checks=[])
    state.demo_mode = demo_mode
    if demo_mode:
        from tests.helpers.source_authoring_demo import fake_demo_transport
        state.providers = fake_demo_transport(monkeypatch, approval_path, scenario="demo_delayed")
    else:
        state.providers = fake_transport(monkeypatch)
    runtime_type, serve = demo.ZoomLiveRuntime, operator.serve_source_neutral_demo

    class Child(FakeChild):
        def send(self, value):
            super().send(value)
            command = value["command"]
            if command == "enrollment_arm":
                runtime = state.zooms[0]
                if state.ending == "timeout":
                    state.now = 30
                    runtime.tick()
                elif state.ending == "failed":
                    self.emit("failed", code="synthetic_failure")
                elif state.ending == "server_closed":
                    state.servers[0].server_close()
                elif state.ending == "poc_archived":
                    state.servers[0].draft_poc_service.archive(state.poc)
                    runtime.tick()
                else:
                    self.emit("enrollment_armed", nonce=value["nonce"])
            elif command == "enrollment_confirm":
                self.emit("enrollment_confirmed", nonce=value["nonce"], user_id=value["user_id"])
            elif command == "enrollment_capture":
                self.emit("listening")

    def factory(*args):
        child = Child(*args)
        state.children.append(child)
        # These two startup events are buffered until the real parent publishes
        # its child handle. No response is injected reentrantly during bind.
        child.emit("offer")
        if "enrollment" in child.init:
            child.emit("enrollment_ready")
        else:
            child.emit("listening")
        return child

    def runtime(**kwargs):
        value = runtime_type(**kwargs, child_factory=factory, fake_network=True,
                             clock=lambda: state.now)
        state.zooms.append(value)
        return value

    def construct(**kwargs):
        state.handles.append(kwargs["source_authoring_launch"])
        kwargs["port"] = 0  # Real loopback server; no fixed port or browser launch.
        try:
            server = serve(**kwargs)
        except BaseException as error:
            state.checks.append(error)
            raise
        state.servers.append(server)
        return server

    def text(prompt):
        state.prompts.append(prompt)
        if prompt == "Exact POC ID: ":
            state.poc = _create_draft(state.servers[0])
            return state.poc
        if prompt.startswith("Observed RTMS ID "):
            if state.ending == "wrong_confirmation":
                return "99"
            return prompt.split()[3].rstrip(".")
        values = {
            "First person's content-free metadata consent receipt: ": "synthetic-person-one",
            "Second person's content-free metadata consent receipt: ": "synthetic-person-two",
            "Zoom client ID: ": settings().client_id,
            "Zoom client secret: ": settings().client_secret,
            "Zoom webhook secret: ": settings().webhook_secret,
            "Exact meeting UUID: ": settings().meeting_uuid,
            "Approved participant numeric IDs (comma separated, max 2): ": "42,43",
            "Approved local webhook port: ": "3456",
            "Approved callback Host header (hostname[:port]): ": "localhost:3456",
            "Approved callback path (/zoom-webhook/<24+ characters>): ": settings().callback_path,
            "Content-free owner consent/rotation receipt ID: ": "synthetic-owner-receipt",
            "Approved spending ceiling USD (e.g. 1.00): ": "0.01",
            "Approved cumulative window in seconds (60–120): ": "120",
            "Approved maximum window in seconds (60–900): ": "120",
        }
        if "APPROVED" in prompt:
            return "DECLINED" if state.ending == "metadata_declined" and "metadata custody" in prompt else "APPROVED"
        return values[prompt]

    def write(*args, **kwargs):
        print(*args, **kwargs)
        # The synthetic participant obeys the actual operator speaking prompt.
        # Emitting both armed and candidate at once would skip the ARMED poll.
        if args and isinstance(args[0], str) and args[0].startswith("Approved speaker "):
            child = state.children[0]
            nonce = child.sent[-1]["nonce"]
            identifier = 42 + sum(item["command"] == "enrollment_confirm" for item in child.sent)
            child.emit("enrollment_candidate", nonce=nonce, user_id=identifier)

    def stop():
        # main deliberately sanitizes exceptions. Retain failures raised by this
        # test callback so a failed assertion cannot masquerade as expected exit.
        try:
            state.stop(state)
        except BaseException as error:
            state.checks.append(error)
            raise

    monkeypatch.setattr(demo, "ZoomLiveRuntime", runtime)
    monkeypatch.setattr(operator, "serve_source_neutral_demo", construct)
    monkeypatch.setattr(operator, "_read_text_tty", text)
    monkeypatch.setattr(operator, "_read_credential_tty", lambda: b"SYNTHETIC-KEY")
    monkeypatch.setattr(operator, "_wait_for_stop_tty", stop)
    monkeypatch.setattr(zoom_operator, "print", write, raising=False)

    def run(*, enroll=True, ending=None, check=lambda _: None):
        state.ending, state.stop = ending, check
        args = ["--approval-id", profile.approval_id, "--approval-file", str(tmp_path / "approval.json"),
                "--approval-sha256", "0" * 64, "--output-root", str(tmp_path)]
        if demo_mode:
            args = ["--demo", "--approval-id", profile.approval_id,
                    "--approval-file", str(approval_path), "--approval-sha256", approval_digest,
                    "--output-root", str(tmp_path)]
        if enroll:
            args.append("--enroll-metadata")
        result = operator.main(args)
        if state.checks:
            raise state.checks[0]
        assert len(state.servers) == len(state.zooms) == len(state.handles) == 1
        assert state.servers[0].source_authoring_web._closed
        assert state.zooms[0]._closed.is_set() and not state.zooms[0]._watcher.is_alive()
        assert all(child.closed for child in state.children)
        assert launch._LAUNCHES[state.handles[0]].state == "REVOKED"
        assert launch._LAUNCHES[state.handles[0]].credential == b""
        assert all(child.poll() is not None for child in state.providers)
        return result

    state.run = run
    yield state
    for server in state.servers:
        server.server_close()


@pytest.mark.parametrize("enroll", [False, True])
def test_explicit_selection_reuses_one_app_and_retains_direct_pairing(combined, enroll):
    def check(state):
        server, runtime = state.servers[0], state.zooms[0]
        assert server.zoom_live_runtime is runtime
        assert runtime._run_if_open.__self__ is server.poc_closure_service
        assert server.source_authoring_web._owners._run_if_open.__self__ is server.poc_closure_service
        assert runtime._record.settings.participant_ids == (42, 43)
        assert runtime.current(state.poc)["state"] == "PAIRED"
        assert len(state.children) == (1 if enroll else 0)
        assert bool(runtime._record.enrollment_digest) is enroll
        assert state.providers == [] and server.source_authoring_web.operations.ledger[0] == 0
        if enroll:
            child = state.children[0]
            assert runtime._record.child is child and runtime._record.expires == 120
            assert [item["command"] for item in child.sent] == [
                "bind", "enrollment_arm", "enrollment_confirm", "enrollment_arm",
                "enrollment_confirm", "enrollment_seal",
            ]
            state.now = 120
            runtime.tick()
            assert child.closed and runtime._record.state == "REVOKED"
    assert combined.run(enroll=enroll, check=check) == 0


@pytest.mark.parametrize("ending", ["metadata_declined", "failed", "timeout", "wrong_confirmation",
                                     "poc_archived", "server_closed"])
def test_enrollment_refusal_or_owner_close_revokes_both_resources(combined, ending, capsys):
    assert combined.run(ending=ending) == 2
    assert combined.providers == []
    assert combined.servers[0].poc_source_intake._source_service.snapshots(combined.poc) == ()
    assert len(combined.children) == (0 if ending == "metadata_declined" else 1)
    if combined.children:
        assert any(item["command"] == "enrollment_arm" for item in combined.children[0].sent)
    output = capsys.readouterr()
    for private in ["SYNTHETIC-KEY", "synthetic-person-one", "synthetic-client-secret", "Observed RTMS ID"]:
        assert private not in output.out + output.err


@pytest.mark.parametrize("args", [[], ["--enroll-metadata"], ["--enroll-metadata", "--approval-file", "/unread"]])
def test_installed_registry_refuses_before_arguments_paths_or_tty(monkeypatch, args):
    assert launch._QUALIFIED_SERVING_CONTRACTS == launch._PRODUCTION_PROFILES == ()
    def forbidden(*_args, **_kwargs):
        pytest.fail("Installed unqualified bootstrap crossed its admission boundary")
    for name in ["_arguments", "_read_text_tty", "_read_credential_tty", "serve_source_neutral_demo"]:
        monkeypatch.setattr(operator, name, forbidden)
    assert operator.main(args) == 2


@pytest.mark.skipif(os.environ.get("EXITSPEC_BROWSER_E2E") != "1", reason="combined synthetic Chromium flow")
@pytest.mark.parametrize("combined", [False, True], indirect=True)
def test_combined_enrollment_exact_source_draft_human_freeze_and_evidence(combined):
    from playwright.sync_api import expect, sync_playwright

    def check(state):
        server, runtime, poc = state.servers[0], state.zooms[0], state.poc
        child, record = state.children[0], runtime._record
        identity = (record, child, record.generation, record.stream_id, record.expires)
        authoring = server.source_authoring_web
        with pytest.raises(ZoomLiveError):
            runtime.action(poc, {"action": "start", "session_id": record.session_id,
                                "idempotency_key": "no-capture-consent", "consent_acknowledged": False})
        assert state.providers == [] and authoring.operations.ledger[0] == 0
        base = f"http://127.0.0.1:{server.server_port}"
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            urls, errors, responses = [], [], []
            page.on("request", lambda request: urls.append(request.url))
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("response", lambda response: responses.append((response.status, response.url)))
            output = os.environ.get("EXITSPEC_COMBINED_EVIDENCE")
            evidence_root = (Path(output) / ("demo" if state.demo_mode else "baseline")) if output else None
            def capture(name):
                if evidence_root:
                    evidence_root.mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=str(evidence_root / (name + ".png")), full_page=True)
            try:
                page.goto(base + f"/app/pocs/{poc}/sources/new")
                expect(page.locator("#zoom-live-start")).to_be_disabled()
                page.locator("#zoom-live-consent").check()
                page.locator("#zoom-live-start").click()
                expect(page.locator("#zoom-live-stop")).to_be_enabled()
                assert (runtime._record, record.child, record.generation, record.stream_id, record.expires) == identity
                assert len(state.children) == 1
                packet(child, data="Response quality must satisfy the customer. The system must select the exact requested tool.")
                packet(child, user_id=43, data="Latency must remain visible to the customer.")
                page.locator("#zoom-live-stop").click()
                expect(page.locator("#zoom-live-status")).to_contain_text("Transport stop requested")
                child.emit("stop_ack")
                child.emit("drained")
                expect(page.locator("#zoom-live-process")).to_be_enabled()
                page.locator("#zoom-live-process").click()
                expect(page.locator("#zoom-live-review")).to_be_visible()
                receipts = server.poc_source_intake.list_receipts(poc)
                assert len(receipts) == len(server.draft_poc_service.ids()) == 1
                source_id = receipts[0].source_receipt_id
                assert runtime.receipt(poc)["participant_association"] == "HUMAN_ATTESTED_NOT_AUTHENTICATED_IDENTITY"
                assert state.providers == [] and authoring.operations.ledger[0] == 0
                cap = authoring.request(poc, "bootstrap", {})["capability"]
                operation = authoring.request(poc, "prepare", {"source_receipt_id": source_id}, cap)["operation_id"]
                with pytest.raises(SourceAuthoringWebError):
                    authoring.request(poc, "run", {"operation_id": operation}, cap)
                assert state.providers == []
                page.goto(base + f"/app/pocs/{poc}/source-authoring")
                page.locator("#source-choice").select_option(source_id)
                page.locator("#source-preview").click()
                expect(page.locator("#source-disclosure")).to_be_visible()
                expect(page.locator("#source-run")).to_be_disabled()
                page.locator("#source-business-text").check()
                page.locator("#source-acknowledged").check()
                page.locator("#source-authorize").click()
                expect(page.locator("#source-run")).to_be_enabled()
                assert state.providers == []
                if state.demo_mode:
                    expect(page.locator("#mode-heading")).to_have_text("Fireworks · one-attempt demo")
                    expect(page.locator("#source-ledger")).to_contain_text("0 of 1")
                    expect(page.locator("#source-purpose")).to_contain_text("ONE")
                capture("01-separate-exact-source-consent")
                page.locator("#source-run").click()
                expect(page.locator("#source-review-result")).to_be_visible(timeout=15000)
                assert len(state.providers) == authoring.operations.ledger[0] == 1
                entry = next(item for item in authoring.operations._records.values() if item.receipt.state == "SUCCEEDED")
                assert entry.disclosure.source_sha256 == runtime.receipt(poc)["redacted_content_sha256"]
                assert all(row.source_receipt_id == source_id for row in server.proposal_review_service.list_proposals(poc))
                rows = server.proposal_review_service.list_proposals(poc)
                assert len(rows) == 3 and all(row.decision is None for row in rows)
                # Replaying the same authorized browser operation cannot draft again.
                active = next(item for item in authoring._browsers if entry.disclosure.operation_id in item.operations)
                authoring.request(poc, "run", {"operation_id": entry.disclosure.operation_id}, active.secret.hex())
                assert len(state.providers) == authoring.operations.ledger[0] == 1
                if state.demo_mode:
                    expect(page.locator("#source-ledger")).to_contain_text("1 of 1")
                    expect(page.locator("#source-mode-copy")).to_contain_text("not a guaranteed invoice ceiling")
                    expect(page.locator("#source-preview")).to_be_disabled()
                    capture("01b-one-attempt-result-readable")
                    assert not authoring.operations._closed
                    from exitspec.source_authoring_demo_run import consumed
                    assert consumed(launch._demo_profile(authoring.operations._live_lease))
                page.locator("#source-review-result").click()
                for index in range(3):
                    page.locator("#review-start").click()
                    page.locator("#reviewer").fill("synthetic_combined_reviewer")
                    page.locator("#rationale").fill("Retain this source claim for separate human planning.")
                    page.locator("#keep-proposal").click()
                    expect(page.locator("#progress-bar")).to_have_attribute("aria-valuenow", str(index + 1))
                page.locator("#plan-capabilities").click()
                _complete_mixed_plan(page)
                page.locator("#open-agreement").click()
                page.locator("#assembly-reviewer").fill("synthetic_combined_assembler")
                page.locator("#assembly-rationale").fill("Assemble exactly the human-declared scope.")
                page.locator("#prepare-agreement").click()
                expect(page.locator("#agreement-summary")).to_be_visible()
                page.locator("#open-customer-review").click()
                page.locator("#agreement-checkbox").check()
                page.locator("#review-rationale").fill("Confirm the exact synthetic agreement.")
                page.locator("#confirm-agreement").click()
                expect(page.locator("#review-result")).to_be_visible()
                page.locator("#return-to-agreement").click()
                page.locator("#freeze-agreement").click()
                expect(page.locator("#agreement-status")).to_have_text("FROZEN")
                capture("02-human-confirmed-frozen")
                page.locator("#open-evidence").click()
                page.locator("#evidence-acknowledged").check()
                page.locator("#start-evidence").click()
                expect(page.locator("#evidence-current-status")).to_have_text("COMPLETED")
                expect(page.locator("#evidence-result-verdict")).to_have_text("PASS")
                page.locator("#decision-owner").fill("synthetic_combined_handoff")
                page.locator("#decision-rationale").fill("Handoff this synthetic evidence without shipping authority.")
                page.locator("#handoff-evidence").click()
                expect(page.locator("#evidence-task-heading")).to_have_text("Human decision recorded")
                closure = server.generic_evidence_service.snapshot_payload(poc)["closure"]
                assert closure["decision"] == "HANDOFF_COMPLETED" and not closure["shipping_authorized"]
                with pytest.raises((POCClosureConflict, SourceAuthoringWebError)):
                    authoring.request(poc, "bootstrap", {})
                assert not errors and all(url.startswith(base + "/") for url in urls)
                assert all(status < 400 for status, _ in responses)
                capture("03-supported-evidence-and-handoff")
                if evidence_root:
                    (evidence_root / "rehearsal.json").write_text(json.dumps({
                        "status": "SYNTHETIC_PASS_NOT_LIVE_QUALIFICATION",
                        "server_count": len(state.servers), "zoom_runtime_count": len(state.zooms),
                        "zoom_child_count": len(state.children), "provider_children": len(state.providers),
                        "source_id": source_id, "zoom_receipt": runtime.receipt(poc),
                        "draft_source_sha256": entry.disclosure.source_sha256,
                        "provider_claims": authoring.operations.ledger[0],
                        "human_decisions": 3, "confirmation_and_freeze": "ACTUAL_EXISTING_PAGES",
                        "closure": closure, "page_errors": errors,
                        "all_browser_requests_loopback": True, "failed_browser_responses": 0,
                        "live_calls": 0, "spend": 0,
                    }, indent=2) + "\n")
            finally:
                browser.close()
    assert combined.run(check=check) == 0
