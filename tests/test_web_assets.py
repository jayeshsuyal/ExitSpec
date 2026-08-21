from pathlib import Path


STATIC_ROOT = Path(__file__).resolve().parents[1] / "src" / "exitspec" / "static"


def _css_rule(css: str, selector: str) -> str:
    marker = selector + " {"
    start = css.index(marker) + len(marker)
    return css[start : css.index("}", start)]


def test_static_demo_assets_exist_and_describe_the_proof_boundary():
    index = STATIC_ROOT / "index.html"
    styles = STATIC_ROOT / "styles.css"
    script = STATIC_ROOT / "app.js"

    assert index.exists()
    assert styles.exists()
    assert script.exists()

    html = index.read_text(encoding="utf-8")
    css = styles.read_text(encoding="utf-8")
    javascript = script.read_text(encoding="utf-8")
    surface_text = f"{html}\n{javascript}"

    for phrase in (
        "Define",
        "Confirm",
        "Prove",
        "Does this rule match the customer’s intent?",
        "Customer asked",
        "Proposed acceptance rule",
        "PASS is not authorization",
        "Capture notes",
        "Define acceptance rule",
        "Create customer review",
        "Freeze confirmed contract",
        "Run another reference set",
        "POC Acceptance Evidence Pack",
        "Not proven",
        "Next human action",
    ):
        assert phrase in surface_text

    for retired_visible_phrase in (
        "Customer-ready Proof Pack",
        "Open full Proof Pack",
        "Give the customer a proof pack",
        "Relevant source",
        "Live state",
        "AI may draft. Humans approve",
        "Customer handoff",
        "Blocked by",
    ):
        assert retired_visible_phrase not in html

    for summary_field in (
        'id="pack-verdict"',
        'id="pack-why"',
        'id="pack-limits"',
        'id="pack-next-step"',
    ):
        assert summary_field in html

    for endpoint in (
        "/api/state",
        "/api/intake",
        "/api/provider/fireworks/disclosure",
        "/api/provider/fireworks/authorization",
        "/api/provider/fireworks/execution",
        "/api/draft/define",
        "/api/review",
        "/api/customer-draft",
        "/api/revision/start",
        "/api/revision/edit",
        "/api/freeze",
        "/api/prove",
        "/api/reset",
    ):
        assert endpoint in javascript

    for workflow_element in (
        'id="current-task-title"',
        'id="agreement-status"',
        'id="blocker-list"',
        'id="next-action-title"',
        'id="start-revision"',
        'id="freeze-contract"',
    ):
        assert workflow_element in html

    assert 'data-stage="define"' in html
    assert 'data-stage="prove"' in html
    assert 'data-stage="decide"' in html
    for label in ("Capture", "Review", "Agree", "Decide"):
        assert f"<strong>{label}</strong>" not in html
    assert "Capture → Review → Agree → Prove → Decide" not in html
    assert "class=\"hero\"" not in html
    assert "Which dataset should test this agreement?" in javascript
    assert "Choose the evidence outcome" not in html
    assert "Passing fixture" not in surface_text
    assert "Borderline fixture" not in surface_text
    assert "Unavailable fixture" not in surface_text
    assert "Reference set A" in html
    assert "Reference set B" in html
    assert "Reference set C" in html
    assert "never fabricates a proof result" in javascript
    assert "state?.proof_pack" in javascript
    assert '"EVIDENCE / RECORDED"' in javascript
    assert '"EVIDENCE / VERIFIED"' not in javascript
    assert 'verdict === "FAIL" || verdict === "BLOCKED"' in javascript
    assert "function renderCustody(model)" in javascript
    assert "renderCustody(model)" in javascript
    for custody_item in (
        "source",
        "agreement",
        "customer",
        "freeze",
        "evidence",
        "decision",
    ):
        assert f'id="custody-{custody_item}"' in html
        assert f'id="custody-{custody_item}-state"' in html
        assert f'custody-${{entry.id}}' in javascript
    for custody_class in (
        "is-pending",
        "is-current",
        "is-recorded",
        "is-warning",
    ):
        assert custody_class in javascript
    assert 'item.setAttribute("aria-current", "step")' in javascript
    assert 'item.removeAttribute("aria-current")' in javascript
    assert "Matches intent" in javascript
    assert "Needs correction" not in javascript
    assert "Edit rule" in javascript
    assert "Define acceptance rule" in javascript
    assert "Keep as context" in javascript
    assert "NOT A TEST" in javascript
    assert "CALL 02:14 · CUSTOMER" in javascript
    assert "Required ≥ ${requiredThreshold}" in javascript
    assert "Observed ${observedCases}/${sampleCount}" in javascript
    assert "Wilson lower bound ${percentage(proof.confidence_lower_bound)}" in javascript
    assert ".verdict-hero .evidence-equation" in css
    assert "Approve rule" not in javascript
    assert "Reject request" not in javascript
    assert "normalized_claim:" not in javascript


def test_fireworks_browser_action_is_explicit_bounded_and_content_free():
    html = (STATIC_ROOT / "index.html").read_text(encoding="utf-8")
    styles = (STATIC_ROOT / "styles.css").read_text(encoding="utf-8")
    javascript = (STATIC_ROOT / "app.js").read_text(encoding="utf-8")
    action = javascript.split("async function runFireworksAssist() {", 1)[1].split(
        "function drafts() {", 1
    )[0]
    acknowledgement = javascript.split(
        "function recordFireworksAcknowledgement() {", 1
    )[1].split("async function runFireworksAssist() {", 1)[0]
    request_helper = javascript.split("async function request(path, options = {}) {", 1)[
        1
    ].split("function applyState(payload) {", 1)[0]

    for control in (
        'id="fireworks-runtime-status"',
        'id="fireworks-provider"',
        'id="fireworks-model"',
        'id="fireworks-destination"',
        'id="fireworks-max-cost"',
        'id="fireworks-data-policy"',
        'id="fireworks-acknowledgement"',
        'id="fireworks-assist-button"',
        'id="fireworks-assist-status"',
    ):
        assert control in html

    assert (
        "Sends the frozen approved synthetic case—not the editable notes above."
        in html
    )
    assert "Local safety cap" in html
    assert "process guardrail left" in javascript
    assert "I authorize one bounded synthetic action under this disclosure." in html
    assert "up to ${limits.max_attempts} provider attempts" in javascript
    assert (
        "fireworksAcknowledgedDisclosureId = fireworksDisclosure.disclosure_id;"
        in acknowledgement
    )
    assert "fireworksAcknowledgedDisclosureId = null;" in acknowledgement
    assert 'headers: { "Idempotency-Key": fireworksAttempt.authorizationKey }' in action
    assert 'headers: { "Idempotency-Key": fireworksAttempt.executionKey }' in action
    assert "disclosure_id: fireworksAttempt.disclosureId" in action
    assert "acknowledged: true" in action
    assert (
        "fireworksAcknowledgedDisclosureId\n"
        "        !== currentDisclosure.disclosure_id"
    ) in action
    assert action.index(
        "const currentDisclosure = await request(API.fireworksDisclosure);"
    ) < action.index("await request(API.fireworksAuthorization")
    assert action.index(
        "fireworksAcknowledgedDisclosureId\n"
        "        !== currentDisclosure.disclosure_id"
    ) < action.index("await request(API.fireworksAuthorization")
    assert (
        'error.payload?.code === "provider_execution_in_progress"'
        in action
    )
    assert "if (error.status && !executionPending)" in action
    assert (
        "Retry to check the same bounded action."
        in action
    )
    assert 'body: "{}"' in action
    assert "meeting-notes" not in action
    assert "transcript" not in action
    assert "model:" not in action
    assert "endpoint:" not in action
    assert "prompt" not in action
    assert "capability" not in action
    assert 'execution.status === "succeeded_needs_review"' in action
    assert "applyState(response)" in action
    assert request_helper.index("...options") < request_helper.index(
        'headers: { "Content-Type": "application/json"'
    )
    assert "body.recording-mode.provider-enabled .source-details" in styles
    assert ".provider-disclosure-grid" in styles
    assert "grid-template-columns: repeat(2, minmax(0, 1fr))" in styles


def test_recording_mode_is_query_driven_and_enters_the_define_workflow():
    html = (STATIC_ROOT / "index.html").read_text(encoding="utf-8")
    styles = (STATIC_ROOT / "styles.css").read_text(encoding="utf-8")
    javascript = (STATIC_ROOT / "app.js").read_text(encoding="utf-8")

    assert "75-second walkthrough" in html
    assert 'id="recording-restart"' in html
    assert 'new URLSearchParams(window.location.search).get("mode")' in javascript
    assert '=== "recording"' in javascript
    assert 'document.body.dataset.mode = recordingMode ? "recording" : "standard"' in javascript
    assert 'await request(API.reset, { method: "POST", body: "{}" })' in javascript
    assert '$("#define").scrollIntoView' in javascript
    assert '$("#decide").scrollIntoView' in javascript
    assert "body.recording-mode .source-details" in styles
    assert "body.recording-mode .proof-workspace" in styles
    assert "body.recording-mode .custody-rail {\n  display: block;" in styles
    assert ".global-nav {\n  display: flex;\n  align-items: center;\n  gap: 28px;" in styles
    assert "position: sticky;\n  z-index: 2;\n  bottom: 0;" in styles
    assert '$("#recording-restart").addEventListener("click", resetDemo)' in javascript
    assert (
        'EXIT_SPEC_DETERMINISTIC_TOOL_SELECTION:\n'
        '        "Evaluate with ExitSpec · deterministic tool-selection fixture"'
        in (STATIC_ROOT / "review.js").read_text(encoding="utf-8")
    )


def test_customer_confirmation_returns_only_to_valid_seeded_or_dynamic_pocs():
    javascript = (STATIC_ROOT / "review.js").read_text(encoding="utf-8")
    validator = javascript.split(
        "function safeLocalReturnPath(value) {", 1
    )[1].split("function showTerminal", 1)[0]
    terminal = javascript.split(
        "function showTerminal(decision) {", 1
    )[1].split("async function loadReview", 1)[0]

    assert 'value === "/app/pocs/poc_support_agent_demo"' in validator
    assert (
        r"^\/app\/pocs\/(poc_[a-z0-9][a-z0-9_-]{2,63})\/agreement$"
        in validator
    )
    assert "match[1] !== review.poc_id" in validator
    assert "parsed.origin === window.location.origin" in validator
    assert "parsed.pathname === value" in validator
    assert 'parsed.search === ""' in validator
    assert 'parsed.hash === ""' in validator
    assert "elements.returnToApp.href = safeLocalReturnUrl;" in javascript
    assert "elements.returnToApp.href = \"/app\";" not in javascript
    assert 'decision.decision === "REQUEST_CHANGES"' in terminal
    assert 'safeLocalReturnUrl?.endsWith("/agreement")' in terminal
    assert '"Changes requested"' in terminal
    assert (
        '"This immutable local POC stops here. The owner must start a new POC '
        'with the requested changes."'
        in terminal
    )


def test_rule_editor_places_progressive_details_before_state_changing_actions():
    """Source contracts supplement, but do not prove, viewport geometry."""
    styles = (STATIC_ROOT / "styles.css").read_text(encoding="utf-8")
    javascript = (STATIC_ROOT / "app.js").read_text(encoding="utf-8")
    editor = javascript.split("function structuredRuleEditor(draft) {", 1)[1].split(
        "function candidateActions(draft) {", 1
    )[0]

    fields_at = editor.index('<div class="rule-editor__fields">')
    actions_at = editor.index('<div class="candidate-actions">')
    details_at = editor.index('<details class="rule-technical-details">')
    details_end_at = editor.index("</details>", details_at)
    panel_at = editor.index('<div class="rule-technical-panel">')
    ledger_at = editor.index(
        '<dl class="supported-rule-ledger" '
        'aria-label="Fixed deterministic measurement fields">'
    )
    note_at = editor.index('<p class="generated-claim-note">')

    assert fields_at < details_at < actions_at
    assert details_at < panel_at < ledger_at < note_at < details_end_at < actions_at
    assert "<summary>Measurement details</summary>" in editor
    assert '<details class="rule-technical-details" open>' not in editor

    detail_rule = _css_rule(styles, ".rule-technical-details")
    summary_rule = _css_rule(styles, ".rule-technical-details > summary")
    panel_rule = _css_rule(styles, ".rule-technical-panel")
    nested_ledger_rule = _css_rule(styles, ".rule-technical-panel .supported-rule-ledger")
    mobile = styles.split("@media (max-width: 760px)", 1)[1].split(
        "@media (max-width: 560px)", 1
    )[0]
    mobile_panel_rule = _css_rule(mobile, ".rule-technical-panel")

    assert "position: relative" in detail_rule
    assert "margin-top: 9px" in detail_rule
    assert "border-top: 1px solid var(--rule)" in detail_rule
    assert "cursor: pointer" in summary_rule
    assert "color: var(--muted)" in summary_rule
    assert "position: absolute" in panel_rule
    assert "bottom: calc(100% + 8px)" in panel_rule
    assert "max-height: 180px" in panel_rule
    assert "overflow: auto" in panel_rule
    assert "background: var(--raised)" in panel_rule
    assert "margin: 0" in nested_ledger_rule
    assert "position: static" in mobile_panel_rule
    assert "max-height: none" in mobile_panel_rule
    assert "overflow: visible" in mobile_panel_rule


def test_rule_details_close_synchronously_when_focus_leaves_the_disclosure():
    javascript = (STATIC_ROOT / "app.js").read_text(encoding="utf-8")
    focus_contract = javascript.split(
        "function bindTechnicalDetailsFocus(details) {", 1
    )[1].split("function candidateActions(draft) {", 1)[0]
    render_contract = javascript.split("function renderCandidates() {", 1)[1].split(
        "function renderRevisionRequest() {", 1
    )[0]

    assert 'details.addEventListener("focusout", (event) =>' in focus_contract
    assert "!event.relatedTarget" in focus_contract
    assert "!details.contains(event.relatedTarget)" in focus_contract
    assert "details.open = false;" in focus_contract
    assert "setTimeout" not in focus_contract
    assert (
        '.querySelectorAll(".rule-technical-details")\n'
        "      .forEach(bindTechnicalDetailsFocus);"
    ) in render_contract


def test_desktop_workbench_prevents_workflow_length_body_scroll():
    styles = (STATIC_ROOT / "styles.css").read_text(encoding="utf-8")

    assert "height: 100dvh" in styles
    assert "overflow: hidden" in styles
    assert "grid-template-rows: 54px minmax(0, 1fr)" in styles
    assert ".proof-workspace" in styles
    assert ".proof-sheet" in styles
    assert ".task-main" in styles
    assert ".task-view" in styles
    assert "overflow: auto" in styles
    assert ".source-drawer" in styles
    assert "position: fixed" in styles
    assert "@media (max-width: 760px)" in styles


def test_workbench_keeps_detail_progressive_and_uses_proof_sheet_palette():
    html = (STATIC_ROOT / "index.html").read_text(encoding="utf-8")
    styles = (STATIC_ROOT / "styles.css").read_text(encoding="utf-8")
    javascript = (STATIC_ROOT / "app.js").read_text(encoding="utf-8")

    assert 'class="proof-sheet"' in html
    assert 'class="custody-disclosure"' in html
    assert 'class="custody-ledger"' in html
    assert 'class="context-rail"' not in html
    assert '<details class="source-details" id="source-details">' in html
    assert '<details class="evidence-details">' in html
    assert 'class="sheet-ledger"' in html
    assert 'class="source-drawer"' in html
    assert "product-nav" not in html
    assert "workspace-label" not in html
    assert "pendingDrafts[0]" in javascript
    assert "Requirement ${reviewed + 1}" in javascript
    assert 'class="candidate decision-card"' in javascript
    assert "Customer review draft" in javascript
    assert 'const journeyOrder = ["define", "confirm", "prove"]' in javascript
    assert html.count("data-journey-step=") == 3
    for stage in ("define", "confirm", "prove"):
        assert f'data-journey-step="{stage}"' in html
    for stage in ("capture", "review", "agree", "decide"):
        assert f'data-journey-step="{stage}"' not in html
    for label in ("Define", "Confirm", "Prove"):
        assert f"<strong>{label}</strong>" in html
    for label in ("Capture", "Review", "Agree", "Decide"):
        assert f"<strong>{label}</strong>" not in html
    assert '"criterion" : "criteria"' in javascript
    assert '"note" : "notes"' in javascript
    assert "--canvas: #0e141b" in styles
    assert "--mast: #121a23" in styles
    assert "--sheet: #18222d" in styles
    assert "--raised: #1d2936" in styles
    assert "--primary: #f1f4f7" in styles
    assert "--secondary: #c4ced9" in styles
    assert "--muted: #96a4b4" in styles
    assert "--rule: #2a3747" in styles
    assert "--strong-rule: #46576b" in styles
    assert "--signal: #e87849" in styles
    assert "--success: #73c99c" in styles


def test_workbench_continuity_contracts_are_explicit_in_the_browser_surface():
    html = (STATIC_ROOT / "index.html").read_text(encoding="utf-8")
    styles = (STATIC_ROOT / "styles.css").read_text(encoding="utf-8")
    javascript = (STATIC_ROOT / "app.js").read_text(encoding="utf-8")

    for control in (
        'id="poc-label"',
        'id="open-source-controls"',
        'id="rerun-proof"',
        'id="recording-restart"',
    ):
        assert control in html

    for polling_contract in (
        "CUSTOMER_POLL_INTERVAL_MS = 1800",
        "stateRefreshPromise",
        "reconcileCustomerPolling",
        "isAwaitingCustomerDecision",
        'document.addEventListener("visibilitychange"',
        'window.addEventListener("pagehide"',
        "stopCustomerPolling();",
    ):
        assert polling_contract in javascript

    for reset_contract in (
        "function resetLocalWorkbench()",
        "editingDraftId = null",
        "rerunMode = false",
        'selectScenario("pass")',
        "closeSourceDrawer();",
    ):
        assert reset_contract in javascript

    for rerun_contract in (
        "function beginRerun()",
        "Run another reference set",
        "Run selected reference set",
        "criterion_reason",
        "await refreshState();",
    ):
        assert rerun_contract in javascript

    for authoring_contract in (
        "supported_rule_template",
        "Exact expected support-tool selection",
        "Human-defined rule",
        "The customer-facing sentence is generated from these fields",
        "One executable rule is already included",
    ):
        assert authoring_contract in javascript

    assert ".rule-editor__fields" in styles
    assert ".supported-rule-ledger" in styles
    assert ".recording-restart" in styles
