import re
import subprocess
from html.parser import HTMLParser
from pathlib import Path


STATIC_ROOT = (
    Path(__file__).resolve().parents[1] / "src" / "exitspec" / "static"
)
HTML_PATH = STATIC_ROOT / "source_intake.html"
CSS_PATH = STATIC_ROOT / "source_intake.css"
JS_PATH = STATIC_ROOT / "source_intake.js"


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
        self.implicitly_labeled_input_ids: list[str] = []
        self.input_ids: list[str] = []
        self.landmarks: list[str] = []
        self._label_depth = 0

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        attributes = dict(attrs)
        if tag == "label":
            self._label_depth += 1
        if attributes.get("id"):
            element_id = str(attributes["id"])
            self.ids.append(element_id)
            if tag in {"input", "select", "textarea"}:
                self.input_ids.append(element_id)
                if self._label_depth:
                    self.implicitly_labeled_input_ids.append(element_id)
        if tag == "label" and attributes.get("for"):
            self.labels_for.append(str(attributes["for"]))
        if tag in {"header", "main", "nav", "section"}:
            self.landmarks.append(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag == "label":
            self._label_depth = max(0, self._label_depth - 1)


def test_source_intake_is_one_accessible_bounded_task():
    html = _asset(HTML_PATH)
    parser = _MarkupInventory()
    parser.feed(html)

    assert len(parser.ids) == len(set(parser.ids))
    labeled_control_ids = set(parser.labels_for) | set(
        parser.implicitly_labeled_input_ids
    )
    assert set(parser.input_ids).issubset(labeled_control_ids)
    assert '<a class="skip-link" href="#source-intake-main">' in html
    assert 'id="source-current-task"' in html
    assert 'aria-labelledby="current-task-heading"' in html
    assert html.count('class="primary-action"') == 1
    assert 'id="capture-source"' in html
    assert 'type="submit"' in html
    assert 'id="source-intake-status"' in html
    assert 'role="status"' in html
    assert 'aria-live="polite"' in html
    assert 'id="intake-error"' in html
    assert 'role="alert"' in html
    assert 'id="add-another-source"' in html
    assert "Add another source" in html
    assert 'id="review-proposals"' in html
    assert "Review proposals" in html
    assert {"header", "nav", "main", "section"}.issubset(parser.landmarks)


def test_four_sources_are_clear_and_product_claims_stay_honest():
    html = _asset(HTML_PATH)

    assert html.count('name="source_kind"') == 4
    for source_kind in ("EMAIL", "MEETING", "DOCUMENT", "EXISTING_CONTRACT"):
        assert f'value="{source_kind}"' in html
        assert f'data-source-option="{source_kind}"' in html

    assert "Starting choice" in html
    assert "not a POC type" in html
    assert "Customer email text" in html
    assert "Paste bounded customer email text" in html
    assert "Approved synthetic fixtures only" not in html
    assert 'id="email-text"' in html
    assert 'maxlength="20000"' in html
    assert "API tokens are redacted before the source is stored" in html
    assert "human review" in html
    assert "Speaker: message" in html
    assert "Customer: Error rate must stay below 1%." in html
    assert "single-speaker natural-text paste is also accepted" in html
    assert 'aria-describedby="meeting-format-help meeting-connection-help"' in html
    assert 'id="meeting-mode-paste"' in html
    assert 'id="meeting-mode-record"' in html
    assert "Record synthetic demo" in html
    assert "Not real STT" in html
    assert "spoken words will not be transcribed" in html
    assert "No provider, Zoom, or Google Meet connection is implied" in html
    assert "PDF and DOCX parsing" in html
    assert "not connected" in html
    assert "Paste one JSON object" in html
    assert 'type="file"' not in html
    assert "Upload" not in html
    assert "<canvas" not in html
    assert "<svg" not in html


def test_meeting_failure_copy_repeats_the_accepted_shapes_without_content_echo():
    javascript = _asset(JS_PATH)
    failure_copy = _function(
        javascript,
        "safeFailureCopy",
        "setControlsDisabled",
    )

    assert 'selectedSource === "MEETING"' in failure_copy
    assert "Speaker: message lines" in failure_copy
    assert "natural single-speaker text block" in failure_copy
    assert "error.message" not in failure_copy
    assert "response.text" not in failure_copy


def test_route_identity_is_validated_before_any_api_url_is_constructed():
    javascript = _asset(JS_PATH)

    pattern_at = javascript.index("const ROUTE_PATTERN")
    match_at = javascript.index(
        "const routeMatch = window.location.pathname.match(ROUTE_PATTERN);"
    )
    identity_at = javascript.index("const pocId =")
    poc_api_at = javascript.index(
        "const pocApi = pocId ? `/api/pocs/${pocId}` : null;"
    )
    sources_api_at = javascript.index(
        "const sourcesApi = pocApi ? `${pocApi}/sources` : null;"
    )

    assert pattern_at < match_at < identity_at < poc_api_at < sources_api_at
    assert (
        r"^\/app\/pocs\/(poc_[a-z0-9][a-z0-9_-]{2,63})\/sources\/new$"
        in javascript
    )
    assert "encodeURIComponent(pocId)" in javascript
    assert "URLSearchParams" not in javascript
    assert "window.location.search" not in javascript
    assert "window.location.hash" not in javascript
    assert "value.includes(\"?\")" in javascript
    assert "value.includes(\"#\")" in javascript
    assert "parsed.origin === window.location.origin" in javascript
    assert "parsed.pathname === value" in javascript


def test_endpoint_selection_is_explicit_and_never_uses_a_generic_adapter_route():
    javascript = _asset(JS_PATH)
    endpoint_selector = _function(
        javascript,
        "endpointFor",
        "isTrustedApiPath",
    )

    for endpoint in ("email-text", "meeting", "document", "contract"):
        assert f"return `${{sourcesApi}}/{endpoint}`;" in endpoint_selector
    for source_kind in ("EMAIL", "MEETING", "DOCUMENT", "EXISTING_CONTRACT"):
        assert f'case "{source_kind}"' in endpoint_selector
    for forbidden in (
        "/sources/import",
        "/sources/adapter",
        "/sources/generic",
        "adapterName",
        "providerName",
    ):
        assert forbidden not in javascript


def test_each_post_payload_contains_only_its_allowed_field_and_idempotency_key():
    javascript = _asset(JS_PATH)
    builder = _function(javascript, "buildSourcePayload", "clearError")

    expected_returns = (
        "{ email_text: value, idempotency_key: idempotencyKey }",
        "{ transcript_text: value, idempotency_key: idempotencyKey }",
        "{ document_text: value, idempotency_key: idempotencyKey }",
        "{ contract_json: value, idempotency_key: idempotencyKey }",
    )
    assert all(expected in builder for expected in expected_returns)
    assert builder.count("idempotency_key: idempotencyKey") == 4

    for forbidden_field in (
        "adapter",
        "provider",
        "path",
        "approval",
        "approve",
        "confirmation",
        "freeze",
        "run",
        "verdict",
        "model",
        "endpoint",
    ):
        assert re.search(rf"\b{forbidden_field}\b", builder, re.IGNORECASE) is None


def test_source_content_is_never_persisted_logged_or_added_to_navigation_state():
    javascript = _asset(JS_PATH)
    success = _function(javascript, "renderSuccess", "applyDraft")
    clearer = _function(javascript, "clearSensitiveInputs", "renderSuccess")

    for forbidden in (
        "localStorage",
        "sessionStorage",
        "console.",
        "history.",
        "location.assign",
        "document.cookie",
    ):
        assert forbidden not in javascript
    assert "clearSensitiveInputs();" in success
    assert success.index("clearSensitiveInputs();") < success.index(
        "resultPanel.hidden = false;"
    )
    assert 'document.querySelector("#meeting-transcript").value = "";' in clearer
    assert 'document.querySelector("#email-text").value = "";' in clearer
    assert 'document.querySelector("#document-text").value = "";' in clearer
    assert 'document.querySelector("#contract-json").value = "";' in clearer
    assert "pendingAttempt = null;" in success
    assert "currentTask.hidden = true;" not in success
    assert "`/app/pocs/${pocId}/sources/new`" in success
    assert "addAnotherSource.hidden = false;" in success
    assert "`/app/pocs/${encodeURIComponent(pocId)}/review`" in success
    assert "reviewProposals.hidden = false;" in success
    assert "window.location.replace(destination);" in success
    navigation = success.split("const destination =", 1)[1].split(";", 1)[0]
    assert "pocId" in navigation
    for sensitive_name in (
        "payload",
        "emailText",
        "meetingTranscript",
        "documentText",
        "contractJson",
    ):
        assert sensitive_name not in navigation
    assert "innerHTML" not in javascript


def test_interrupted_or_untrusted_response_reuses_the_same_attempt():
    javascript = _asset(JS_PATH)
    submit = javascript.split(
        'form.addEventListener("submit"', 1
    )[1].split("\n  async function initialise", 1)[0]

    assert "if (!pendingAttempt)" in submit
    assert "const idempotencyKey = newIdempotencyKey();" in submit
    assert "pendingAttempt = {" in submit
    assert "JSON.stringify(pendingAttempt.payload)" in submit
    assert "!error.retrySameAttempt" in submit
    assert "pendingAttempt = null;" in submit
    assert "response.status >= 500" in javascript
    assert "response.status === 408" in javascript
    assert "response.status === 429" in javascript
    assert (
        "!pendingAttempt && selectedValue(selectedSource) === null"
        in javascript
    )
    assert (
        "The response was interrupted. Retry will use the same source key."
        in javascript
    )


def test_accepted_response_is_bound_to_poc_source_receipt_and_review_state():
    javascript = _asset(JS_PATH)
    validator = _function(
        javascript,
        "isTrustedCaptureResponse",
        "requestJson",
    )
    submit = javascript.split(
        'form.addEventListener("submit"', 1
    )[1].split("\n  async function initialise", 1)[0]

    for exact_key in (
        "idempotent_replay",
        "poc_id",
        "proposal_count",
        "source_kind",
        "source_receipt_id",
        "status",
    ):
        assert f'"{exact_key}"' in validator
    assert "payload.poc_id === pocId" in validator
    assert "payload.source_kind === sourceKind" in validator
    assert "RECEIPT_ID_PATTERN.test(payload.source_receipt_id)" in validator
    assert 'payload.status === "NEEDS_REVIEW"' in validator
    assert "Number.isSafeInteger(payload.proposal_count)" in validator
    assert "payload.proposal_count <= 64" in validator
    assert "isTrustedCaptureResponse(response, pendingAttempt.sourceKind)" in submit
    assert 'document.querySelector("#review-state").textContent = "NEEDS_REVIEW";' in (
        javascript
    )
    assert "payload.error" not in javascript
    assert "error.message" not in javascript
    assert "response.text" not in javascript


def test_draft_and_source_list_are_loaded_read_only_before_controls_unlock():
    javascript = _asset(JS_PATH)
    initialise = javascript.split("async function initialise()", 1)[1]
    apply_draft = _function(javascript, "applyDraft", "blockIntake")

    assert "Promise.all([" in initialise
    assert "requestJson(pocApi)" in initialise
    assert "requestJson(sourcesApi)" in initialise
    assert "method:" not in initialise
    assert "isTrustedDraft(draft)" in initialise
    assert "isTrustedSourceList(sourceList)" in initialise
    assert "preferredSource = draft.first_source_choice;" in apply_draft
    assert "selectedSource = preferredSource;" in apply_draft
    assert 'currentTask.setAttribute("aria-busy", "false");' in apply_draft
    assert "chooser.disabled = disabled;" in javascript


def test_success_is_a_real_review_handoff_with_no_authority_claim():
    html = _asset(HTML_PATH)
    result = html.split('id="capture-result"', 1)[1].split(
        'id="intake-error"', 1
    )[0]

    assert "Source captured" in result
    assert "Review the proposals next" in result
    assert 'id="proposal-count"' in result
    assert 'id="review-state"' in result
    assert "NEEDS_REVIEW" in result
    assert "No proposal was approved automatically" in result
    assert 'id="review-proposals"' in result
    assert "Review proposals" in result
    assert "contract definition" in result
    assert "approve" not in result.lower().replace(
        "no proposal was approved automatically",
        "",
    )


def test_graphite_orange_layout_is_finite_at_demo_size_and_reflows_at_320px():
    css = _asset(CSS_PATH)

    assert "grid-template-rows: auto minmax(0, 1fr);" in css
    assert "grid-template-columns: minmax(250px, 0.72fr) minmax(0, 1.55fr);" in css
    assert "overflow: hidden;" in css
    assert "overflow: auto;" in css
    assert "@media (max-width: 760px), (max-height: 680px)" in css
    assert "@media (max-width: 520px)" in css
    assert "body {\n    overflow: auto;" in css
    assert "grid-template-columns: 1fr;" in css
    assert "width: 100%;" in css
    assert "var(--orange)" in css
    assert "var(--canvas)" in css
    assert "#000" not in css.lower()
    assert "gradient" not in css.lower()
    assert "backdrop-filter" not in css.lower()


def test_source_intake_javascript_has_valid_syntax():
    result = subprocess.run(
        ["node", "--check", str(JS_PATH)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
