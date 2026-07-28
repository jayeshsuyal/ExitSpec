import re
import subprocess
from html.parser import HTMLParser
from pathlib import Path


STATIC_ROOT = Path(__file__).resolve().parents[1] / "src" / "exitspec" / "static"
HTML_PATH = STATIC_ROOT / "contract_definition.html"
CSS_PATH = STATIC_ROOT / "contract_definition.css"
JS_PATH = STATIC_ROOT / "contract_definition.js"


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
        self.buttons: list[dict[str, str | None]] = []
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
        if tag == "button":
            self.buttons.append(attributes)
        if tag in {"header", "main", "nav", "section", "article"}:
            self.landmarks.append(tag)


def test_page_is_one_accessible_bounded_definition_task():
    html = _asset(HTML_PATH)
    parser = _MarkupInventory()
    parser.feed(html)

    assert len(parser.ids) == len(set(parser.ids))
    assert set(parser.form_control_ids).issubset(set(parser.labels_for))
    assert '<a class="skip-link" href="#contract-definition-main">' in html
    assert 'id="definition-current-task"' in html
    assert 'aria-labelledby="current-task-heading"' in html
    assert 'aria-busy="true"' in html
    assert 'role="progressbar"' in html
    assert 'id="definition-status"' in html
    assert 'role="status"' in html
    assert 'aria-live="polite"' in html
    assert 'id="contract-definition-error"' in html
    assert 'role="alert"' in html
    assert {"header", "main", "nav", "section", "article"}.issubset(
        parser.landmarks
    )


def test_page_has_one_consistent_primary_action_and_no_fake_next_link():
    html = _asset(HTML_PATH)
    parser = _MarkupInventory()
    parser.feed(html)

    assert len(parser.buttons) == 1
    assert parser.buttons[0]["id"] == "save-definition"
    assert parser.buttons[0]["type"] == "submit"
    assert html.count('class="primary-action"') == 1
    assert re.search(r">\s*Save definition\s*</button>", html)
    completion = html.split('id="definition-complete"', 1)[1]
    assert 'href="' not in completion
    assert "Next" not in completion


def test_source_quote_and_claim_are_visibly_separate_from_human_form():
    html = _asset(HTML_PATH)

    evidence_at = html.index('id="definition-evidence"')
    quote_at = html.index('id="source-quote"')
    claim_at = html.index('id="normalized-claim"')
    form_at = html.index('id="contract-definition-form"')

    assert evidence_at < quote_at < claim_at < form_at
    assert "Redacted source quote" in html
    assert "Normalized claim" in html
    assert "Human definition" in html
    assert "not written to browser storage" in html


def test_supported_metrics_and_explicit_operators_are_bounded():
    html = _asset(HTML_PATH)
    javascript = _asset(JS_PATH)

    assert html.count('value="TTFT_P95_MS"') == 1
    assert html.count('value="ERROR_RATE_PERCENT"') == 1
    assert 'id="operator"' in html
    assert 'name="operator"' in html
    assert html.count('value="LT"') == 1
    assert html.count('value="LTE"') == 1
    assert 'const OPERATORS = Object.freeze(["LT", "LTE"]);' in javascript
    assert "operator: operator" not in javascript
    assert "operator," in javascript
    assert 'unit: "MILLISECONDS"' in javascript
    assert 'unit: "PERCENT"' in javascript
    assert "minimum: 0.001" in javascript
    assert "maximum: 60000" in javascript
    assert "minimum: 0" in javascript
    assert "maximum: 100" in javascript
    assert 'thresholdUnit.textContent = config.shortUnit;' in javascript


def test_normal_view_is_simple_and_workload_ranges_are_native_details():
    html = _asset(HTML_PATH)

    assert 'id="metric"' in html
    assert 'id="threshold"' in html
    assert 'id="minimum-samples"' in html
    assert 'id="concurrency"' in html
    assert '<details class="technical-fields">' in html
    assert "Planning context · not measured by runner v1" in html
    assert "does not prove token distributions" in html
    assert 'id="prompt-tokens-min"' in html
    assert 'id="prompt-tokens-max"' in html
    assert 'id="output-tokens-min"' in html
    assert 'id="output-tokens-max"' in html
    assert "<details open" not in html


def test_defaults_are_visible_but_copy_requires_human_verification():
    html = _asset(HTML_PATH)

    assert 'id="threshold"' in html and 'value="500"' in html
    assert 'id="minimum-samples"' in html and 'value="100"' in html
    assert 'id="concurrency"' in html and 'value="4"' in html
    assert 'max="1000"' in html
    assert 'max="32"' in html
    for value in ("512", "4096", "64"):
        assert f'value="{value}"' in html
    assert (
        "Starting values are suggestions only. Verify every value\n"
        "                  against the reviewed source before saving."
    ) in html
    assert "current runner binds a hashed prompt fixture" in html
    assert "does not prove token distributions" in html


def test_reviewer_and_rationale_are_required_and_bounded():
    html = _asset(HTML_PATH)
    javascript = _asset(JS_PATH)
    validator = _function(
        javascript,
        "validatedDefinitionFields",
        "setFieldAvailability",
    )

    assert 'id="reviewer"' in html
    assert 'maxlength="160"' in html
    assert 'autocomplete="off"' in html
    assert 'id="rationale"' in html
    assert 'maxlength="2000"' in html
    assert html.count("required") == 11
    assert "isSafeBoundedText(reviewer, 160)" in validator
    assert 'reviewer.includes("\\n")' in validator
    assert "isSafeBoundedText(rationale, 2000)" in validator
    assert "form.reportValidity()" in javascript


def test_exact_route_identity_precedes_api_construction():
    javascript = _asset(JS_PATH)

    pattern_at = javascript.index("const ROUTE_PATTERN")
    match_at = javascript.index("const routeMatch =")
    identity_at = javascript.index("const pocId =")
    poc_api_at = javascript.index(
        "const pocApi = pocId ? `/api/pocs/${pocId}` : null;"
    )
    definitions_at = javascript.index(
        "const definitionsApi = pocApi ? `${pocApi}/definitions` : null;"
    )

    assert pattern_at < match_at < identity_at < poc_api_at < definitions_at
    assert (
        r"^\/app\/pocs\/(poc_[a-z0-9][a-z0-9_-]{2,63})\/define$"
        in javascript
    )
    assert "encodeURIComponent" not in javascript
    assert "URLSearchParams" not in javascript
    assert 'window.location.search === ""' in javascript
    assert 'window.location.hash === ""' in javascript
    assert 'value.includes("?")' in javascript
    assert 'value.includes("#")' in javascript
    assert "parsed.origin === window.location.origin" in javascript
    assert "parsed.pathname === value" in javascript


def test_get_contract_is_exact_bounded_kept_and_unique():
    javascript = _asset(JS_PATH)
    proposal_validator = _function(
        javascript,
        "isTrustedProposal",
        "isTrustedDefinitionList",
    )
    list_validator = _function(
        javascript,
        "isTrustedDefinitionList",
        "isTrustedDefinitionResponse",
    )

    for exact_key in (
        "definition",
        "normalized_claim",
        "proposal_id",
        "review_state",
        "source_kind",
        "source_quote",
        "source_receipt_id",
    ):
        assert f'"{exact_key}"' in javascript.split(
            "const PROPOSAL_KEYS", 1
        )[1].split("]);", 1)[0]
    assert "hasExactKeys(proposal, PROPOSAL_KEYS)" in proposal_validator
    assert "PROPOSAL_ID_PATTERN.test(proposal.proposal_id)" in proposal_validator
    assert (
        "SOURCE_RECEIPT_ID_PATTERN.test(proposal.source_receipt_id)"
        in proposal_validator
    )
    assert "SOURCE_KINDS.includes(proposal.source_kind)" in proposal_validator
    assert "isSafeBoundedText(proposal.source_quote, 4000)" in proposal_validator
    assert (
        "isSafeBoundedText(proposal.normalized_claim, 2000)"
        in proposal_validator
    )
    assert 'proposal.review_state === "KEEP_FOR_CONTRACT"' in proposal_validator
    assert "proposal.definition === null" in proposal_validator
    assert "isTrustedDefinition(proposal.definition)" in proposal_validator
    assert 'hasExactKeys(payload, ["poc_id", "proposals"])' in list_validator
    assert "payload.proposals.length > 1024" in list_validator
    assert "new Set(proposalIds).size === proposalIds.length" in list_validator
    assert "new Set(definitionIds).size === definitionIds.length" in list_validator


def test_definition_receipt_contract_is_exact_and_metric_consistent():
    javascript = _asset(JS_PATH)
    validator = _function(
        javascript,
        "isTrustedDefinition",
        "isTrustedProposal",
    )
    keys = javascript.split("const DEFINITION_KEYS", 1)[1].split("]);", 1)[0]

    for exact_key in (
        "definition_id",
        "definition_sha256",
        "metric",
        "unit",
        "operator",
        "threshold",
        "minimum_samples",
        "concurrency",
        "prompt_tokens_min",
        "prompt_tokens_max",
        "output_tokens_min",
        "output_tokens_max",
        "defined_at",
    ):
        assert f'"{exact_key}"' in keys
    assert "hasExactKeys(definition, DEFINITION_KEYS)" in validator
    assert "DEFINITION_ID_PATTERN.test(definition.definition_id)" in validator
    assert "SHA256_PATTERN.test(definition.definition_sha256)" in validator
    assert "!OPERATORS.includes(definition.operator)" in validator
    assert "definition.unit !== config.unit" in validator
    assert "Number.isFinite(definition.threshold)" in validator
    assert "definition.prompt_tokens_min > definition.prompt_tokens_max" in validator
    assert "definition.output_tokens_min > definition.output_tokens_max" in validator
    assert 'definition.operator !== "LT"' in validator
    assert "definition.concurrency > definition.minimum_samples" in validator
    assert "Date.parse(definition.defined_at)" in validator


def test_loads_poc_and_definitions_read_only_before_unlocking():
    javascript = _asset(JS_PATH)
    initialise = javascript.split("async function initialise()", 1)[1]
    apply_loaded = _function(
        javascript,
        "applyLoadedData",
        "reconcileDefinitionsAfterSave",
    )

    assert "Promise.all([" in initialise
    assert "requestJson(pocApi)" in initialise
    assert "requestJson(definitionsApi)" in initialise
    assert "method:" not in initialise
    assert "isTrustedDraft(draft)" in initialise
    assert "isTrustedDefinitionList(definitionList)" in initialise
    assert "proposals = definitionList.proposals.slice();" in apply_loaded
    assert 'currentTask.setAttribute("aria-busy", "false");' in apply_loaded
    assert "renderCurrentProposal();" in apply_loaded
    assert 'payload.archive_state === "ACTIVE"' in javascript
    assert 'cache: "no-store"' in javascript


def test_existing_definitions_are_skipped_to_next_undefined_proposal():
    javascript = _asset(JS_PATH)
    current = _function(
        javascript,
        "currentProposal",
        "clearError",
    )
    renderer = _function(
        javascript,
        "renderCurrentProposal",
        "renderCompletion",
    )

    assert (
        "proposals.find((proposal) => proposal.definition === null)"
        in current
    )
    assert "const proposal = currentProposal();" in renderer
    assert "if (!proposal)" in renderer
    assert "renderCompletion();" in renderer


def test_post_body_has_exact_authoring_fields_and_body_only_idempotency():
    javascript = _asset(JS_PATH)
    submit = javascript.split('form.addEventListener("submit"', 1)[1].split(
        "\n  async function initialise", 1
    )[0]
    payload = submit.split("payload: {", 1)[1].split("},\n      };", 1)[0]
    required_fields = (
        "proposal_id",
        "metric",
        "operator",
        "threshold",
        "minimum_samples",
        "concurrency",
        "prompt_tokens_min",
        "prompt_tokens_max",
        "output_tokens_min",
        "output_tokens_max",
        "reviewer",
        "rationale",
        "idempotency_key",
    )

    for field in required_fields:
        assert re.search(rf"\b{field}\b", payload)
    assert payload.count("idempotency_key") == 1
    assert "JSON.stringify(pendingAttempt.payload)" in submit
    assert 'method: "POST"' in submit
    assert '"Content-Type": "application/json"' in submit
    assert "Idempotency-Key" not in javascript
    assert "X-Idempotency" not in javascript

    for forbidden in (
        "approved",
        "confirmed",
        "customer_confirmation",
        "freeze",
        "frozen",
        "run",
        "score",
        "verdict",
        "source_quote",
        "normalized_claim",
        "provider",
        "endpoint",
    ):
        assert re.search(rf"\b{forbidden}\b", payload, re.IGNORECASE) is None


def test_post_response_is_exact_and_bound_to_the_attempt():
    javascript = _asset(JS_PATH)
    validator = _function(
        javascript,
        "isTrustedDefinitionResponse",
        "requestJson",
    )

    for exact_key in (
        "definition",
        "disposition",
        "poc_id",
        "proposal_id",
    ):
        assert f'"{exact_key}"' in validator
    assert "payload.poc_id !== pocId" in validator
    assert "payload.proposal_id !== attempt.proposalId" in validator
    assert "DISPOSITIONS.includes(payload.disposition)" in validator
    assert "isTrustedDefinition(payload.definition)" in validator
    for field in (
        "metric",
        "operator",
        "threshold",
        "minimum_samples",
        "concurrency",
        "prompt_tokens_min",
        "prompt_tokens_max",
        "output_tokens_min",
        "output_tokens_max",
    ):
        assert f"definition.{field} === request.{field}" in validator


def test_retry_reuses_exact_same_body_and_definition_key():
    javascript = _asset(JS_PATH)
    submit = javascript.split('form.addEventListener("submit"', 1)[1].split(
        "\n  async function initialise", 1
    )[0]

    assert "if (!pendingAttempt)" in submit
    assert "pendingAttempt = {" in submit
    assert "JSON.stringify(pendingAttempt.payload)" in submit
    assert "!error.retrySameAttempt" in submit
    assert "pendingAttempt = null;" in submit
    assert "response.status >= 500" in javascript
    assert "response.status === 408" in javascript
    assert "response.status === 429" in javascript
    assert "Retry uses the same definition key." in javascript
    assert "Retry save definition" in javascript
    assert "contract-definition-${window.crypto.randomUUID()}" in javascript
    assert "Math.random" not in javascript


def test_completion_requires_authoritative_queue_refresh():
    javascript = _asset(JS_PATH)
    reconcile = _function(
        javascript,
        "reconcileDefinitionsAfterSave",
        "blockDefinition",
    )
    submit = javascript.split('form.addEventListener("submit"', 1)[1].split(
        "\n  async function initialise", 1
    )[0]

    assert "requestJson(definitionsApi)" in reconcile
    assert "isTrustedDefinitionList(definitionList)" in reconcile
    assert "proposals = definitionList.proposals.slice();" in reconcile
    assert "renderCurrentProposal();" in reconcile
    assert "definitionRecorded = true;" in submit
    assert "await reconcileDefinitionsAfterSave();" in submit
    assert "if (definitionRecorded)" in submit
    assert (
        "The definition was recorded, but the current queue could not be refreshed."
        in submit
    )


def test_completion_copy_preserves_every_downstream_authority_boundary():
    html = _asset(HTML_PATH)

    assert "Definitions are ready for later agreement drafting" in html
    assert (
        "No contract was created, approved, customer-confirmed, frozen, run,\n"
        "            scored, or given a verdict."
    ) in html
    assert "Authoring only · zero execution authority" in html
    assert "No contract was created" in html
    assert "approved" in html
    assert "customer-confirmed" in html
    assert "frozen" in html
    assert "run" in html
    assert "scored" in html
    assert "verdict" in html


def test_sensitive_content_never_enters_persistence_navigation_or_logs():
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
        "location.replace",
        "document.cookie",
        "innerHTML",
        "dataset",
    ):
        assert forbidden not in javascript
    assert 'document.querySelector("#source-quote").textContent =' in renderer
    assert 'document.querySelector("#normalized-claim").textContent =' in renderer
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


def test_transport_is_same_origin_no_store_json_only_and_redirect_closed():
    javascript = _asset(JS_PATH)
    request = _function(
        javascript,
        "requestJson",
        "newIdempotencyKey",
    )

    assert "isTrustedApiPath(path)" in request
    assert 'credentials: "same-origin"' in request
    assert 'cache: "no-store"' in request
    assert 'redirect: "error"' in request
    assert 'referrerPolicy: "same-origin"' in request
    assert 'Accept: "application/json"' in request
    assert 'contentType.split(";", 1)[0].trim() !== "application/json"' in request
    assert "responseUrl.origin !== window.location.origin" in request
    assert "responseUrl.pathname !== path" in request
    assert "responseUrl.search !==" in request
    assert "responseUrl.hash !==" in request


def test_css_is_graphite_orange_finite_responsive_and_focus_visible():
    css = _asset(CSS_PATH)
    dashboard_css = _asset(STATIC_ROOT / "dashboard.css")

    assert "var(--orange)" in css
    assert "var(--canvas)" in css
    assert "var(--panel)" in css
    assert "var(--navigation)" in css
    assert "#000" not in css.lower()
    assert "gradient" not in css.lower()
    assert "backdrop-filter" not in css.lower()
    assert "box-shadow" not in css.lower()
    assert "overflow: hidden;" in css
    assert "overflow: auto;" in css
    assert ".definition-fields" in css
    assert "@media (max-width: 840px), (max-height: 680px)" in css
    assert "@media (max-width: 560px)" in css
    assert ":focus-visible" in css
    assert "@media (prefers-reduced-motion: reduce)" in css
    assert "--canvas: #0b0d0c;" in dashboard_css
    assert "--orange: #ff6b3d;" in dashboard_css


def test_javascript_parses_with_node():
    completed = subprocess.run(
        ["node", "--check", str(JS_PATH)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
