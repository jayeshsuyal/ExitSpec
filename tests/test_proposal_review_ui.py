import re
import subprocess
from html.parser import HTMLParser
from pathlib import Path


STATIC_ROOT = Path(__file__).resolve().parents[1] / "src" / "exitspec" / "static"
HTML_PATH = STATIC_ROOT / "proposal_review.html"
CSS_PATH = STATIC_ROOT / "proposal_review.css"
JS_PATH = STATIC_ROOT / "proposal_review.js"


def _asset(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _function(source: str, name: str, next_name: str) -> str:
    section = source.split(f"function {name}", 1)[1]
    return section.split(f"function {next_name}", 1)[0]


class _MarkupInventory(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: list[str] = []
        self.labels_for: list[str] = []
        self.form_control_ids: list[str] = []
        self.landmarks: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        attributes = dict(attrs)
        if attributes.get("id"):
            element_id = str(attributes["id"])
            self.ids.append(element_id)
            if tag in {"input", "textarea", "select"}:
                self.form_control_ids.append(element_id)
        if tag == "label" and attributes.get("for"):
            self.labels_for.append(str(attributes["for"]))
        if tag in {"header", "main", "nav", "section", "article"}:
            self.landmarks.append(tag)


def test_review_page_is_one_accessible_bounded_task():
    html = _asset(HTML_PATH)
    parser = _MarkupInventory()
    parser.feed(html)

    assert len(parser.ids) == len(set(parser.ids))
    assert set(parser.form_control_ids).issubset(set(parser.labels_for))
    assert '<a class="skip-link" href="#proposal-review-main">' in html
    assert 'id="proposal-current-task"' in html
    assert 'aria-labelledby="current-task-heading"' in html
    assert 'aria-busy="true"' in html
    assert 'role="progressbar"' in html
    assert 'id="decision-status"' in html
    assert 'role="status"' in html
    assert 'aria-live="polite"' in html
    assert 'id="proposal-review-error"' in html
    assert 'role="alert"' in html
    assert {"header", "main", "nav", "section", "article"}.issubset(parser.landmarks)


def test_one_proposal_and_two_explicit_decisions_are_visible():
    html = _asset(HTML_PATH)

    assert 'id="source-kind"' in html
    assert 'id="source-quote"' in html
    assert 'id="normalized-claim"' in html
    assert 'id="proposal-support"' in html
    assert 'id="progress-copy"' in html
    assert html.count('name="decision"') == 2
    assert 'value="KEEP_FOR_CONTRACT"' in html
    assert 'value="DISCARD"' in html
    assert "Keep for contract" in html
    assert re.search(r">\s*Discard\s*</button>", html)
    assert html.count('class="primary-action"') == 1
    assert html.count('class="secondary-action"') == 1


def test_copy_keeps_triage_separate_from_every_authority_boundary():
    html = _asset(HTML_PATH)
    javascript = _asset(JS_PATH)

    assert (
        "This demo executes one TTFT and one error-rate claim. Other\n"
        "                  claims stay NOT_PROVEN. KEEP remains triage only—it does not\n"
        "                  approve, freeze, run, or issue a verdict."
    ) in html
    assert "No contract was created or approved." in html
    assert (
        "Contract authoring, customer confirmation, freeze, execution, and\n"
        "            verdict remain separate steps."
    ) in html
    assert "Triage only · zero execution authority" in html
    assert (
        "approve"
        not in _function(
            javascript,
            "isTrustedDecisionResponse",
            "requestJson",
        ).lower()
    )


def test_exact_route_identity_precedes_all_api_construction():
    javascript = _asset(JS_PATH)

    pattern_at = javascript.index("const ROUTE_PATTERN")
    match_at = javascript.index("const routeMatch =")
    identity_at = javascript.index("const pocId =")
    poc_api_at = javascript.index("const pocApi = pocId ? `/api/pocs/${pocId}` : null;")
    proposals_api_at = javascript.index(
        "const proposalsApi = pocApi ? `${pocApi}/proposals` : null;"
    )

    assert pattern_at < match_at < identity_at < poc_api_at < proposals_api_at
    assert r"^\/app\/pocs\/(poc_[a-z0-9][a-z0-9_-]{2,63})\/review$" in javascript
    assert "encodeURIComponent(pocId)" in javascript
    assert "URLSearchParams" not in javascript
    assert 'window.location.search === ""' in javascript
    assert 'window.location.hash === ""' in javascript
    assert 'value.includes("?")' in javascript
    assert 'value.includes("#")' in javascript
    assert "parsed.origin !== window.location.origin" in javascript
    assert "parsed.pathname !== value" in javascript


def test_trusted_queue_is_exact_bounded_source_anchored_and_unique():
    javascript = _asset(JS_PATH)
    proposal_validator = _function(
        javascript,
        "isTrustedProposal",
        "isTrustedReviewSummary",
    )
    summary_validator = _function(
        javascript,
        "isTrustedReviewSummary",
        "isTrustedProposalList",
    )
    queue_validator = _function(
        javascript,
        "isTrustedProposalList",
        "isTrustedDecisionResponse",
    )

    for exact_key in (
        "normalized_claim",
        "proposal_id",
        "source_receipt_id",
        "source_kind",
        "source_quote",
        "review_state",
    ):
        assert f'"{exact_key}"' in proposal_validator
    assert "PROPOSAL_ID_PATTERN.test(proposal.proposal_id)" in proposal_validator
    assert "proposal.source_receipt_id" in proposal_validator
    assert "SOURCE_KINDS.includes(proposal.source_kind)" in proposal_validator
    assert "isSafeBoundedText(proposal.source_quote, 4000)" in proposal_validator
    assert "isSafeBoundedText(proposal.normalized_claim, 2000)" in proposal_validator
    assert 'proposal.review_state === "NEEDS_REVIEW"' in proposal_validator
    for summary_key in (
        "discarded",
        "kept_for_contract",
        "needs_review",
        "total",
    ):
        assert f'"{summary_key}"' in summary_validator
    assert "Number.isInteger(count)" in summary_validator
    assert "count >= 0 && count <= 1024" in summary_validator
    assert "summary.total ===" in summary_validator
    assert (
        'hasExactKeys(payload, ["poc_id", "proposals", "review_summary"])'
        in queue_validator
    )
    assert "isTrustedReviewSummary(payload.review_summary)" in queue_validator
    assert (
        "payload.review_summary.needs_review !== payload.proposals.length"
        in queue_validator
    )
    assert "payload.proposals.length > 1024" in queue_validator
    assert "new Set(proposalIds).size === proposalIds.length" in queue_validator


def test_draft_and_proposals_load_read_only_before_review_unlocks():
    javascript = _asset(JS_PATH)
    initialise = javascript.split("async function initialise()", 1)[1]
    apply_loaded = _function(
        javascript,
        "applyLoadedData",
        "blockReview",
    )

    assert "Promise.all([" in initialise
    assert "requestJson(pocApi)" in initialise
    assert "requestJson(proposalsApi)" in initialise
    assert "method:" not in initialise
    assert "isTrustedDraft(draft)" in initialise
    assert "isTrustedProposalList(proposalList)" in initialise
    assert "proposals = proposalList.proposals.slice();" in apply_loaded
    assert "initialCount = proposalList.review_summary.total;" in apply_loaded
    assert (
        "keptCount = proposalList.review_summary.kept_for_contract;"
        in apply_loaded
    )
    assert (
        "discardedCount = proposalList.review_summary.discarded;"
        in apply_loaded
    )
    assert 'currentTask.setAttribute("aria-busy", "false");' in apply_loaded
    assert "renderCurrentProposal();" in apply_loaded
    assert 'payload.archive_state === "ACTIVE"' in javascript
    assert 'cache: "no-store"' in javascript


def test_reviewer_and_rationale_are_required_before_either_decision():
    html = _asset(HTML_PATH)
    javascript = _asset(JS_PATH)
    validator = _function(
        javascript,
        "validatedReviewFields",
        "setFieldAvailability",
    )
    controls = _function(
        javascript,
        "updateDecisionControls",
        "renderProgress",
    )

    assert 'id="reviewer"' in html
    assert 'maxlength="160"' in html
    assert 'id="rationale"' in html
    assert 'maxlength="2000"' in html
    assert html.count("required") == 2
    assert "isSafeBoundedText(reviewer, 160)" in validator
    assert "isSafeBoundedText(rationale, 2000)" in validator
    assert "!fieldsValid" in controls
    assert "keepButton.disabled" in controls
    assert "discardButton.disabled" in controls
    assert "form.reportValidity()" in javascript


def test_post_body_has_only_decision_review_fields_and_one_idempotency_key():
    javascript = _asset(JS_PATH)
    submit = javascript.split('form.addEventListener("submit"', 1)[1].split(
        "\n  async function initialise", 1
    )[0]
    payload = submit.split("payload: {", 1)[1].split("},\n      };", 1)[0]

    assert re.search(r"\bdecision,", payload)
    assert "reviewer: fields.reviewer" in payload
    assert "rationale: fields.rationale" in payload
    assert "idempotency_key: newIdempotencyKey()" in payload
    assert payload.count("idempotency_key") == 1
    assert "JSON.stringify(pendingAttempt.payload)" in submit
    assert 'method: "POST"' in submit
    assert '"Content-Type": "application/json"' in submit

    for forbidden in (
        "approval",
        "confirmation",
        "freeze",
        "run",
        "verdict",
        "source_quote",
        "normalized_claim",
        "provider",
        "endpoint",
    ):
        assert re.search(rf"\b{forbidden}\b", payload, re.IGNORECASE) is None


def test_decision_response_is_exact_and_bound_to_attempt():
    javascript = _asset(JS_PATH)
    validator = _function(
        javascript,
        "isTrustedDecisionResponse",
        "requestJson",
    )

    for exact_key in (
        "decision",
        "disposition",
        "poc_id",
        "proposal_id",
        "review_state",
    ):
        assert f'"{exact_key}"' in validator
    assert "payload.poc_id === pocId" in validator
    assert "payload.proposal_id === attempt.proposalId" in validator
    assert "payload.decision === attempt.payload.decision" in validator
    assert "payload.review_state === attempt.payload.decision" in validator
    assert "DECISION_DISPOSITIONS.includes(payload.disposition)" in validator


def test_retry_reuses_the_same_decision_payload_and_key():
    javascript = _asset(JS_PATH)
    submit = javascript.split('form.addEventListener("submit"', 1)[1].split(
        "\n  async function initialise", 1
    )[0]

    assert "if (!pendingAttempt)" in submit
    assert "pendingAttempt = {" in submit
    assert "JSON.stringify(pendingAttempt.payload)" in submit
    assert "retriedDecision !== pendingAttempt.payload.decision" in submit
    assert "!error.retrySameAttempt" in submit
    assert "pendingAttempt = null;" in submit
    assert "response.status >= 500" in javascript
    assert "response.status === 408" in javascript
    assert "response.status === 429" in javascript
    assert "Retry uses the same decision key." in javascript
    assert 'pendingDecision !== "KEEP_FOR_CONTRACT"' in javascript
    assert 'pendingDecision !== "DISCARD"' in javascript


def test_source_content_never_enters_browser_persistence_navigation_or_logs():
    javascript = _asset(JS_PATH)
    renderer = _function(
        javascript,
        "renderCurrentProposal",
        "renderCompletion",
    )

    for forbidden in (
        "localStorage",
        "sessionStorage",
        "console.",
        "history.",
        "location.assign",
        "document.cookie",
        "innerHTML",
        "dataset",
    ):
        assert forbidden not in javascript
    assert 'document.querySelector("#source-quote").textContent =' in renderer
    assert 'document.querySelector("#normalized-claim").textContent =' in renderer
    assert "proposals.shift();" in javascript
    assert 'window.addEventListener("pagehide"' in javascript
    assert (
        javascript.count('document.querySelector("#source-quote").textContent = "";')
        == 2
    )
    assert (
        javascript.count(
            'document.querySelector("#normalized-claim").textContent = "";'
        )
        == 2
    )
    assert "payload.error" not in javascript
    assert "error.message" not in javascript
    assert "response.text" not in javascript
    assert (
        "source_quote"
        not in javascript.split("payload: {", 1)[1].split("},\n      };", 1)[0]
    )
    completion = _function(javascript, "renderCompletion", "applyLoadedData")
    assert (
        "`/app/pocs/${encodeURIComponent(pocId)}/define`" in completion
    )
    assert "window.location.replace(destination);" in completion
    navigation = completion.split("const destination =", 1)[1].split(";", 1)[0]
    assert "pocId" in navigation
    assert "source_quote" not in navigation
    assert "normalized_claim" not in navigation


def test_completion_requires_an_authoritative_queue_refresh():
    javascript = _asset(JS_PATH)
    reconcile = _function(
        javascript,
        "reconcileQueueAfterDecision",
        "blockReview",
    )
    submit = javascript.split('form.addEventListener("submit"', 1)[1].split(
        "\n  async function initialise",
        1,
    )[0]

    assert "requestJson(proposalsApi)" in reconcile
    assert "isTrustedProposalList(proposalList)" in reconcile
    assert "proposals = proposalList.proposals.slice();" in reconcile
    assert "initialCount = proposalList.review_summary.total;" in reconcile
    assert (
        "keptCount = proposalList.review_summary.kept_for_contract;"
        in reconcile
    )
    assert (
        "discardedCount = proposalList.review_summary.discarded;"
        in reconcile
    )
    assert "renderCurrentProposal();" in reconcile
    assert "decisionRecorded = true;" in submit
    assert "await reconcileQueueAfterDecision();" in submit
    assert "if (decisionRecorded)" in submit
    assert (
        "The decision was recorded, but the current proposal queue could not "
        "be refreshed."
        in submit
    )
    assert submit.index("proposals.shift();") < submit.index(
        "await reconcileQueueAfterDecision();"
    )


def test_completion_is_concise_honest_and_links_to_real_definition_step():
    html = _asset(HTML_PATH)
    javascript = _asset(JS_PATH)
    completion_html = html.split('id="review-complete"', 1)[1].split(
        'id="proposal-review-error"', 1
    )[0]
    completion_js = _function(
        javascript,
        "renderCompletion",
        "applyLoadedData",
    )

    assert "No proposals remain in this queue" in completion_html
    assert "No contract was created or approved." in completion_html
    assert "remain separate steps" in completion_html
    assert 'id="define-criteria"' in completion_html
    assert "Define acceptance criteria" in completion_html
    assert "keptCount === 2" in completion_js
    assert "Add the missing executable requirement" in completion_js
    assert "`/app/pocs/${encodeURIComponent(pocId)}/define`" in completion_js
    assert "defineCriteriaLink.hidden = false;" in completion_js
    assert '"Add another source"' in completion_js
    assert "`/app/pocs/${encodeURIComponent(pocId)}/sources/new`" in completion_js
    assert "window.location.replace(destination);" in completion_js
    assert "initialCount === 0" in completion_js
    assert "keptCount" in completion_js
    assert "discardedCount" in completion_js
    assert "completionPanel.focus();" in completion_js


def test_graphite_orange_layout_is_finite_and_accessibly_reflows():
    css = _asset(CSS_PATH)

    assert "grid-template-rows: auto minmax(0, 1fr);" in css
    assert "grid-template-columns: minmax(0, 1.24fr) minmax(330px, 0.76fr);" in css
    assert "overflow: hidden;" in css
    assert "overflow: auto;" in css
    assert "@media (max-width: 760px), (max-height: 680px)" in css
    assert "@media (max-width: 520px)" in css
    assert "body {\n    overflow: auto;" in css
    assert "grid-template-columns: 1fr;" in css
    assert "width: 100%;" in css
    assert "var(--orange)" in css
    assert "var(--canvas)" in css
    assert ":focus-visible" in css
    assert "#000" not in css.lower()
    assert "gradient" not in css.lower()
    assert "backdrop-filter" not in css.lower()


def test_proposal_review_javascript_has_valid_syntax():
    result = subprocess.run(
        ["node", "--check", str(JS_PATH)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
