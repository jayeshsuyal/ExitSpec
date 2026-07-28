import json
import re
import subprocess
from html.parser import HTMLParser
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATIC_ROOT = PROJECT_ROOT / "src" / "exitspec" / "static"
INDEX_PATH = STATIC_ROOT / "index.html"
APP_PATH = STATIC_ROOT / "app.js"
CONTRACT_PATH = (
    PROJECT_ROOT
    / "examples"
    / "support-agent"
    / "email"
    / "wave-2-source-web-v1.json"
)


class _IdInventory(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if attributes.get("id"):
            self.ids.append(str(attributes["id"]))


def _sources() -> tuple[str, str, dict]:
    return (
        INDEX_PATH.read_text(encoding="utf-8"),
        APP_PATH.read_text(encoding="utf-8"),
        json.loads(CONTRACT_PATH.read_text(encoding="utf-8")),
    )


def _function(source: str, name: str, next_name: str) -> str:
    section = source.split(f"function {name}", 1)[1]
    return section if not next_name else section.split(f"function {next_name}", 1)[0]


def test_exact_six_source_ids_are_added_before_the_candidate_list():
    html, _, contract = _sources()
    parser = _IdInventory()
    parser.feed(html)
    existing = set(contract["ui_contract"]["existing_dom_hooks"])
    future = contract["ui_contract"]["future_dom_ids"]

    assert future == [
        "source-intake-panel",
        "source-fixture-select",
        "import-source-fixture",
        "source-intake-status",
        "source-summary",
        "source-summary-details",
    ]
    assert set(parser.ids) == existing | set(future)
    assert len(parser.ids) == len(set(parser.ids))
    assert all(html.count(f'id="{dom_id}"') == 1 for dom_id in future)

    define_at = html.index('id="define"')
    panel_at = html.index('id="source-intake-panel"')
    candidate_at = html.index('id="candidate-list"')
    assert define_at < panel_at < candidate_at
    assert all(
        panel_at <= html.index(f'id="{dom_id}"') < candidate_at
        for dom_id in future
    )


def test_pre_import_surface_uses_the_frozen_copy_and_no_forbidden_source_feature():
    html, _, contract = _sources()
    panel = html.split('id="source-intake-panel"', 1)[1].split(
        'id="candidate-list"', 1
    )[0]
    copy = contract["ui_contract"]["pre_import_copy"]

    for phrase in copy.values():
        assert phrase in panel
    assert '<label for="source-fixture-select">' in panel
    assert 'id="source-fixture-select" disabled' in panel
    assert 'id="import-source-fixture" type="button" disabled' in panel
    assert 'id="source-intake-status"' in panel
    assert 'role="status"' in panel
    assert 'aria-live="polite"' in panel
    assert '<details id="source-summary-details">' in panel

    lowered = panel.lower()
    for forbidden in (
        'type="file"',
        "upload",
        "paste",
        "oauth",
        "mailbox",
        "imap",
        "smtp",
        "provider",
        "google meet",
        "zoom",
        "raw email",
    ):
        assert forbidden not in lowered


def test_email_mode_is_query_gated_and_ordinary_app_remains_the_default():
    html, javascript, _ = _sources()

    assert 'new URLSearchParams(window.location.search).get("intake") === "email"' in (
        javascript
    )
    assert 'document.body.classList.toggle("email-intake-mode", emailIntakeMode)' in (
        javascript
    )
    assert 'id="source-intake-panel"' in html
    assert 'aria-label="Synthetic source intake"' in html
    assert re.search(r'id="source-intake-panel"[\s\S]{0,100}\bhidden\b', html)
    assert "if (!emailIntakeMode)" in javascript
    assert 'new URLSearchParams(window.location.search).get("mode")' in javascript


def test_catalog_and_import_use_only_the_frozen_endpoints_and_request_shape():
    _, javascript, _ = _sources()
    importer = _function(javascript, "importSourceFixture", "percentage")
    loader = _function(javascript, "loadSourceCatalog", "importSourceFixture")
    initialise = javascript.split("async function initialise", 1)[1].split(
        "\n  applyPresentationMode();", 1
    )[0]

    assert 'sourceFixtures: "/api/source/fixtures"' in javascript
    assert 'sourceImport: "/api/source/import"' in javascript
    assert "await request(API.sourceFixtures)" in loader
    assert "importRequest = request(API.sourceImport" in importer
    assert 'method: "POST"' in importer
    assert (
        "body: JSON.stringify({ fixture_case_id: selectedFixture })" in importer
    )
    for forbidden_field in (
        "raw_rfc822",
        "source_id",
        "version_id",
        "content_sha256",
        "message_id",
        "customer_terms",
        "provider_request",
        "metadata:",
    ):
        assert forbidden_field not in importer
    assert "applyState(response)" not in importer
    assert "sourceLastReceipt = preserveSourceReceipt(response?.receipt)" in importer
    assert "const completeWorkflow = await request(API.state)" in importer
    assert "applyState(requireCompleteWorkflowState(completeWorkflow))" in importer
    assert not re.search(r"state\.source_intake\s*=(?!=)", javascript)
    assert "importSourceFixture(" not in initialise


def test_import_is_click_only_and_double_submission_is_blocked():
    _, javascript, _ = _sources()
    importer = _function(javascript, "importSourceFixture", "percentage")

    assert "if (!emailIntakeMode || sourceImportRunning || resetRunning)" in importer
    assert "sourceImportRunning = true;" in importer
    assert "sourceImportRunning = false;" in importer
    assert "const operationVersion = ++sourceOperationVersion" in importer
    assert "const workflowVersion = ++stateRefreshVersion" in importer
    assert (
        '$("#import-source-fixture").addEventListener("click", '
        "() => importSourceFixture())" in javascript
    )
    assert (
        '$("#source-summary-details .source-replay-action")'
        '.addEventListener("click"' in javascript
    )
    select_uses = [
        line
        for line in javascript.splitlines()
        if "source-fixture-select" in line and "addEventListener" in line
    ]
    assert select_uses == []
    assert "autoImport" not in javascript


def test_delayed_catalog_import_and_state_responses_cannot_undo_reset():
    _, javascript, _ = _sources()
    catalog = _function(javascript, "loadSourceCatalog", "importSourceFixture")
    importer = _function(javascript, "importSourceFixture", "percentage")
    local_reset = _function(javascript, "resetLocalWorkbench", "loadIntake")
    reset = _function(javascript, "resetDemo", "createCustomerDraft")
    refresh = _function(javascript, "refreshState", "runProof")

    assert "const catalogVersion = ++sourceCatalogVersion" in catalog
    assert "catalogVersion !== sourceCatalogVersion || !pageActive" in catalog
    assert "sourceCatalogVersion += 1;" in local_reset
    assert "sourceCatalogRunning = false;" in local_reset
    assert local_reset.index("sourceCatalogVersion += 1;") < local_reset.index(
        'setSourceStatus("")'
    )
    assert local_reset.index("sourceCatalogRunning = false;") < local_reset.index(
        'setSourceStatus("")'
    )
    assert importer.count("operationVersion !== sourceOperationVersion") >= 3
    assert importer.count("workflowVersion !== stateRefreshVersion") >= 3
    assert "const pendingSourceImport = sourceImportRequestPromise" in reset
    assert "await pendingSourceImport.catch(() => null)" in reset
    assert "const resetVersion = stateRefreshVersion" in reset
    assert "resetVersion !== stateRefreshVersion || !pageActive" in reset
    assert "sourceOperationVersion += 1;" in javascript
    assert "sourceLastReceipt = null;" in javascript
    assert "sourceTimingEvidence = null;" in javascript
    assert "sourceImportRunning || resetRunning" in refresh


def test_email_actions_fail_closed_against_server_review_controls():
    _, javascript, _ = _sources()
    actions = _function(javascript, "emailCandidateActions", "renderCandidates")

    assert "sourceReviewControl(draft.id)" in actions
    assert 'allowedActions.includes("APPROVE")' in actions
    assert 'allowedActions.includes("REJECT")' in actions
    assert "control.can_edit_rule === true" in actions
    assert "if (allowApprove && complete)" in actions
    assert "if (canEditRule)" in actions
    assert "if (allowReject)" in actions
    assert "Matches intent" in actions
    assert "Define acceptance rule" in actions
    assert "Keep as context" in actions
    assert actions.index("if (allowApprove && complete)") < actions.index(
        "Matches intent"
    )
    assert "No review action is allowed by the current server state." in actions
    assert actions.index('allowedActions.includes("APPROVE")') < actions.index(
        "Matches intent"
    )
    assert actions.index('allowedActions.includes("REJECT")') < actions.index(
        "Keep as context"
    )


def test_summary_replay_reset_and_safe_typed_failures_are_explicit():
    html, javascript, contract = _sources()
    renderer = _function(javascript, "renderSourceIntake", "loadSourceCatalog")
    failure = _function(javascript, "sourceFailureMessage", "sourceReviewControl")

    summary = contract["ui_contract"]["post_import_compact_summary"]
    assert "intake.label" in renderer
    assert (
        "`${intake.proposal_count} proposals · sensitive fields removed`" in renderer
    )
    assert summary["boundary"] in html
    assert "Sensitive fields were removed before review." in renderer
    assert "reset this workflow first" in renderer
    assert "Existing reviews were preserved. (duplicate_replay)" in javascript
    assert (
        '$("#source-summary-details .source-reset-action")'
        '.addEventListener("click", resetDemo)' in javascript
    )
    assert "applyState(await request(API.reset" in javascript

    for refusal in contract["refusal_contract"]:
        assert refusal["code"] in failure
    assert "Object.hasOwn(pinnedMessages, code)" in failure
    assert "`${pinnedMessages[code]} (${code})`" in failure
    assert "/^[a-z][a-z0-9_]{0,47}$/.test(code)" in failure
    assert "`${safeRefusal} (${safeCode})`" in failure
    assert "error.message" not in failure
    assert "payload?.error?.message" not in failure
    assert "Local source service unavailable." in failure
    assert "raw_error" not in failure


def test_source_failures_never_render_malicious_server_copy_or_identifiers():
    _, javascript, _ = _sources()
    failure = (
        "function sourceFailureMessage"
        + _function(javascript, "sourceFailureMessage", "sourceReviewControl")
    )
    probes = [
        {
            "status": 403,
            "code": "forbidden_origin",
            "message": "From: attacker@example.com\r\nMessage-ID: <known@evil.test>",
            "payload": {"raw_details": "X-Injected: known-secret"},
        },
        {
            "status": 400,
            "payload": {
                "error": {
                    "code": "future_safe_refusal",
                    "message": "To: customer@example.com\r\nX-Injected: future-secret",
                },
                "raw_details": {"message_id": "<future@evil.test>"},
            },
        },
        {
            "status": 400,
            "payload": {
                "error": {
                    "code": "bad\r\nX-Injected: header-secret",
                    "message": "header-message-secret",
                }
            },
        },
        {
            "status": 400,
            "payload": {
                "error": {
                    "code": "attacker@example.com",
                    "message": "address-message-secret",
                }
            },
        },
        {
            "status": 400,
            "payload": {
                "error": {
                    "code": "<message-id@evil.test>",
                    "message": "message-id-secret",
                }
            },
        },
    ]
    script = "\n".join(
        (
            '"use strict";',
            failure,
            f"const probes = {json.dumps(probes)};",
            "process.stdout.write(JSON.stringify(probes.map(sourceFailureMessage)));",
        )
    )
    completed = subprocess.run(
        ["node", "--input-type=commonjs", "-e", script],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    rendered = json.loads(completed.stdout)

    assert rendered == [
        "Open this action from the same local ExitSpec application. (forbidden_origin)",
        "The source action failed safely. (future_safe_refusal)",
        "The source action failed safely.",
        "The source action failed safely.",
        "The source action failed safely.",
    ]
    joined = " ".join(rendered)
    for secret in (
        "attacker@example.com",
        "customer@example.com",
        "X-Injected",
        "known-secret",
        "future-secret",
        "header-secret",
        "message-id",
        "evil.test",
    ):
        assert secret not in joined


def test_email_import_cannot_make_proof_action_available_by_itself():
    _, javascript, _ = _sources()
    actions = _function(javascript, "renderActions", "renderProof")

    assert 'state?.confirmation?.decision === "CONFIRM"' in actions
    assert 'state?.contract?.status === "FROZEN"' in actions
    assert "state?.ready_to_prove && sourceProofEligible" in actions
    assert "sourceReviewIncomplete" in actions
    assert "runButton.hidden = true;" in actions


def test_catalog_labels_are_text_only_and_browser_timing_never_leaves_the_page():
    _, javascript, _ = _sources()
    renderer = _function(javascript, "renderSourceIntake", "loadSourceCatalog")
    importer = _function(javascript, "importSourceFixture", "percentage")
    receipt = _function(javascript, "preserveSourceReceipt", "requireCompleteWorkflowState")

    assert "option.textContent = fixture.label" in renderer
    assert "option.innerHTML" not in renderer
    assert "summary.querySelector" in renderer
    assert "sourceTimingEvidence = Object.freeze({" in importer
    timing = importer.split("sourceTimingEvidence = Object.freeze({", 1)[1].split(
        "});", 1
    )[0]
    assert set(re.findall(r"^\s*([a-z_]+):", timing, flags=re.MULTILINE)) == {
        "fixture_case_id",
        "outcome_code",
        "elapsed_ms",
    }
    assert "window.performance.now()" in importer
    assert "window.requestAnimationFrame" in importer
    request_bodies = re.findall(r"body:\s*JSON\.stringify\(\{([^}]*)\}\)", javascript)
    assert all("elapsed_ms" not in body for body in request_bodies)
    assert all("outcome_code" not in body for body in request_bodies)
    assert "localStorage" not in javascript
    assert "sessionStorage" not in javascript
    assert "indexedDB" not in javascript
    for field in (
        "source_type",
        "manifest_id",
        "manifest_version",
        "fixture_case_id",
        "outcome_code",
        "source_version",
        "candidate_count",
    ):
        assert f'"{field}"' in receipt
