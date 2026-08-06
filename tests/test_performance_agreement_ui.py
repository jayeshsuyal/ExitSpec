import re
import subprocess
from html.parser import HTMLParser
from pathlib import Path


STATIC_ROOT = Path(__file__).resolve().parents[1] / "src" / "exitspec" / "static"
HTML_PATH = STATIC_ROOT / "agreement.html"
CSS_PATH = STATIC_ROOT / "agreement.css"
JS_PATH = STATIC_ROOT / "agreement.js"


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


def test_page_is_one_accessible_guided_agreement_workbench():
    html = _asset(HTML_PATH)
    parser = _MarkupInventory()
    parser.feed(html)

    assert len(parser.ids) == len(set(parser.ids))
    assert set(parser.form_control_ids).issubset(set(parser.labels_for))
    assert '<a class="skip-link" href="#agreement-main">' in html
    assert 'id="agreement-workbench"' in html
    assert 'aria-labelledby="current-task-heading"' in html
    assert 'aria-busy="true"' in html
    assert 'id="lifecycle-progress"' in html
    assert 'aria-label="Agreement lifecycle"' in html
    assert 'id="draft-status"' in html
    assert 'id="confirmation-status"' in html
    assert 'id="freeze-status"' in html
    assert html.count('role="status"') == 3
    assert 'id="agreement-error"' in html
    assert 'role="alert"' in html
    assert {"header", "main", "nav", "section", "article"}.issubset(
        parser.landmarks
    )


def test_lifecycle_has_one_panel_per_state_and_exact_freeze_action():
    html = _asset(HTML_PATH)
    parser = _MarkupInventory()
    parser.feed(html)

    assert 'id="create-draft-form"' in html
    assert 'id="confirmation-panel"' in html
    assert 'id="review-invitation"' in html
    assert 'id="pending-review-actions"' in html
    assert 'id="customer-review-link"' in html
    assert 'id="refresh-customer-review"' in html
    assert 'id="reissue-customer-review"' in html
    assert 'id="changes-requested-actions"' in html
    assert 'id="start-revision"' in html
    assert 'id="revision-panel"' in html
    assert 'id="continue-revision"' in html
    assert 'id="freeze-panel"' in html
    assert 'id="freeze-form"' in html
    assert 'id="agreement-complete"' in html
    button_ids = {button["id"] for button in parser.buttons}
    assert button_ids == {
        "create-customer-draft",
        "freeze-contract",
        "refresh-customer-review",
        "reissue-customer-review",
        "start-revision",
        "use-reference-target",
    }
    assert re.search(
        r'id="use-reference-target"[\s\S]*?>\s*'
        r"Use local reference target\s*</button>",
        html,
    )
    assert re.search(r">\s*Create customer draft\s*</button>", html)
    assert re.search(
        r'id="customer-review-link"[\s\S]*?>\s*'
        r"Open customer review\s*</a>",
        html,
    )
    assert re.search(
        r'id="reissue-customer-review"[\s\S]*?>\s*'
        r"Issue new review link\s*</button>",
        html,
    )
    assert re.search(
        r'id="start-revision"[\s\S]*?>\s*Start revision\s*</button>',
        html,
    )
    assert re.search(r">\s*Freeze confirmed contract\s*</button>", html)
    assert 'id="freeze-contract"' in html
    assert 'id="confirmation-form"' not in html
    assert 'id="confirm-agreement"' not in html


def test_execution_target_is_required_never_defaulted_and_not_inferred():
    html = _asset(HTML_PATH)
    javascript = _asset(JS_PATH)

    for element_id, name, maximum in (
        ("target-provider", "target_provider", "160"),
        ("endpoint-class", "endpoint_class", "160"),
        ("endpoint", "endpoint", "2048"),
        ("model", "model", "300"),
    ):
        control = html.split(f'id="{element_id}"', 1)[1].split("/>", 1)[0]
        assert f'name="{name}"' in control
        assert f'maxlength="{maximum}"' in control
        assert "required" in control
        assert 'value="' not in control
    assert 'type="url"' in html.split('id="endpoint"', 1)[1].split("/>", 1)[0]
    assert '<details class="endpoint-fields">' in html
    assert "<details open" not in html
    assert "No value is inferred." in html
    assert "defaultTarget" not in javascript
    assert "inferTarget" not in javascript
    assert "autoTarget" not in javascript


def test_local_reference_target_is_explicit_and_never_fakes_human_review():
    html = _asset(HTML_PATH)
    javascript = _asset(JS_PATH)
    reference = _function(
        javascript,
        "useReferenceTarget",
        "updateFreezeControls",
    )

    assert "Use local reference target" in html
    assert "It does not prove production inference" in html
    assert "Not proven by this POC" in html
    assert "Excluded claims remain" in html
    assert 'id="customer-not-proven-list"' in html
    assert "<strong>NOT_PROVEN</strong>" in html
    for exact_value in (
        "ExitSpec local reference",
        "OpenAI-compatible deterministic reference",
        "exitspec/reference-stream-v1",
        "/api/reference/inference/v1/chat/completions",
    ):
        assert exact_value in javascript
    assert "targetProviderInput.value" in reference
    assert "endpointClassInput.value" in reference
    assert "endpointInput.value" in reference
    assert "modelInput.value" in reference
    assert "draftReviewerInput.value" not in reference
    assert "draftRationaleInput.value" not in reference
    assert "draftReviewerInput.focus();" in reference


def test_customer_review_visibly_repeats_all_target_fields_before_confirmation():
    html = _asset(HTML_PATH)

    customer = html.split('id="customer-agreement"', 1)[1].split(
        'id="freeze-panel"', 1
    )[0]
    for label, element_id in (
        ("Provider", "review-target-provider"),
        ("Model", "review-model"),
        ("Endpoint class", "review-endpoint-class"),
        ("Endpoint URL", "review-endpoint"),
    ):
        assert label in customer
        assert f'id="{element_id}"' in customer
    assert customer.index('id="review-target-provider"') < customer.index(
        'id="review-model"'
    )
    assert customer.index('id="review-endpoint-class"') < customer.index(
        'id="review-endpoint"'
    )
    assert "Customer review copy" in customer
    assert "Confirmation required" in customer
    assert 'id="customer-criteria-list"' in customer


def test_exact_route_identity_precedes_exact_agreement_api_construction():
    javascript = _asset(JS_PATH)

    pattern_at = javascript.index("const ROUTE_PATTERN")
    match_at = javascript.index("const routeMatch =")
    identity_at = javascript.index("const pocId =")
    agreement_at = javascript.index(
        "const agreementApi = pocId ? `/api/pocs/${pocId}/agreement` : null;"
    )
    review_at = javascript.index(
        "const reviewApi = agreementApi ? `${agreementApi}/review` : null;"
    )
    freeze_at = javascript.index(
        "const freezeApi = agreementApi ? `${agreementApi}/freeze` : null;"
    )

    assert (
        pattern_at
        < match_at
        < identity_at
        < agreement_at
        < review_at
        < freeze_at
    )
    assert (
        r"^\/app\/pocs\/(poc_[a-z0-9][a-z0-9_-]{2,63})\/agreement$"
        in javascript
    )
    assert "encodeURIComponent(pocId)" in javascript
    assert "URLSearchParams" not in javascript
    assert 'window.location.search === ""' in javascript
    assert 'window.location.hash === ""' in javascript
    assert 'value.includes("?")' in javascript
    assert 'value.includes("#")' in javascript
    assert "confirmationApi" not in javascript
    assert "/agreement/confirm" not in javascript


def test_get_projection_is_exact_bounded_unique_and_lifecycle_consistent():
    javascript = _asset(JS_PATH)
    validator = _function(
        javascript,
        "isTrustedAgreementProjection",
        "isTrustedDraftActionResponse",
    )
    keys = javascript.split("const AGREEMENT_KEYS", 1)[1].split("]);", 1)[0]

    for exact_key in (
        "poc_id",
        "definitions",
        "not_proven_claims",
        "draft",
        "customer_review",
        "confirmation",
        "frozen_contract",
        "revision",
        "superseded_version_count",
    ):
        assert f'"{exact_key}"' in keys
    assert "hasExactKeys(payload, AGREEMENT_KEYS)" in validator
    assert "payload.poc_id !== pocId" in validator
    assert "payload.definitions.length > 1024" in validator
    assert "payload.definitions.every(isTrustedDefinition)" in validator
    assert "payload.not_proven_claims.length > 1024" in validator
    assert "isSafeBoundedText(claim, 2000)" in validator
    assert "new Set(proposalIds).size !== proposalIds.length" in validator
    assert "new Set(definitionIds).size !== definitionIds.length" in validator
    assert "payload.draft === null" in validator
    assert "payload.confirmation !== null" in validator
    assert "payload.frozen_contract !== null" in validator
    assert (
        "payload.confirmation.draft_sha256 !== payload.draft.draft_sha256"
        in validator
    )
    assert (
        "payload.frozen_contract.confirmation_id !==\n"
        "          payload.confirmation.confirmation_id"
    ) in validator
    assert 'payload.confirmation?.decision === "CONFIRM"' in validator
    assert 'payload.customer_review.status !== "CONFIRMED"' in validator
    assert 'payload.confirmation?.decision === "REQUEST_CHANGES"' in validator
    assert 'payload.customer_review.status !== "CHANGES_REQUESTED"' in validator
    assert 'payload.confirmation.decision !== "CONFIRM"' in validator
    assert "!targetMatches(payload.frozen_contract, payload.draft)" in validator
    assert "isTrustedRevision(payload.revision)" in validator
    assert "payload.superseded_version_count" in validator


def test_definition_projection_is_exact_source_anchored_and_metric_bounded():
    javascript = _asset(JS_PATH)
    validator = _function(
        javascript,
        "isTrustedDefinition",
        "isTrustedDraft",
    )
    keys = javascript.split("const DEFINITION_KEYS", 1)[1].split("]);", 1)[0]

    for exact_key in (
        "proposal_id",
        "definition_id",
        "definition_sha256",
        "source_kind",
        "source_quote",
        "normalized_claim",
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
    assert "PROPOSAL_ID_PATTERN.test(definition.proposal_id)" in validator
    assert "DEFINITION_ID_PATTERN.test(definition.definition_id)" in validator
    assert "SHA256_PATTERN.test(definition.definition_sha256)" in validator
    assert "SOURCE_KINDS.includes(definition.source_kind)" in validator
    assert "isSafeBoundedText(definition.source_quote, 4000)" in validator
    assert "isSafeBoundedText(definition.normalized_claim, 2000)" in validator
    assert '["LT", "LTE"].includes(definition.operator)' in validator
    assert 'definition.operator === "LT"' in validator
    assert "definition.threshold > 0" in validator
    assert "definition.threshold < config.maximum" in validator
    assert "isExactInteger(definition.minimum_samples, 1, 1000)" in validator
    assert "isExactInteger(definition.concurrency, 1, 32)" in validator
    assert "definition.concurrency <= definition.minimum_samples" in validator
    assert "Number.isFinite(definition.threshold)" in validator
    assert "isTrustedTimestamp(definition.defined_at)" in validator


def test_all_lifecycle_receipts_have_exact_safe_shapes():
    javascript = _asset(JS_PATH)
    poc_draft_keys = javascript.split(
        "const POC_DRAFT_KEYS = Object.freeze([", 1
    )[1].split("]);", 1)[0]
    agreement_draft_keys = javascript.split(
        "const DRAFT_KEYS = Object.freeze([", 1
    )[1].split("]);", 1)[0]
    draft_validator = _function(
        javascript,
        "isTrustedDraft",
        "isTrustedConfirmation",
    )
    confirmation_validator = _function(
        javascript,
        "isTrustedConfirmation",
        "isTrustedFrozenContract",
    )
    frozen_validator = _function(
        javascript,
        "isTrustedFrozenContract",
        "targetMatches",
    )

    assert '"contract_id"' not in poc_draft_keys
    assert '"contract_version"' not in poc_draft_keys
    assert '"contract_id"' in agreement_draft_keys
    assert '"contract_version"' in agreement_draft_keys
    assert '"parent_version"' in agreement_draft_keys
    assert "hasExactKeys(draft, DRAFT_KEYS)" in draft_validator
    assert "DRAFT_ID_PATTERN.test(draft.draft_id)" in draft_validator
    assert "SHA256_PATTERN.test(draft.draft_sha256)" in draft_validator
    assert "hasValidVersionLineage(draft)" in draft_validator
    for field, maximum in (
        ("target_provider", "160"),
        ("endpoint_class", "160"),
        ("model", "300"),
        ("reviewer", "160"),
    ):
        assert f"isSingleLineText(draft.{field}, {maximum})" in draft_validator
    assert "isExactTargetUrl(draft.endpoint)" in draft_validator
    assert "isSafeBoundedText(draft.rationale, 2000)" in draft_validator

    assert (
        "hasExactKeys(confirmation, CONFIRMATION_KEYS)"
        in confirmation_validator
    )
    assert (
        "CONFIRMATION_ID_PATTERN.test(confirmation.confirmation_id)"
        in confirmation_validator
    )
    assert "confirmation.agreement_acknowledged === true" in confirmation_validator
    assert "isTrustedTimestamp(confirmation.confirmed_at)" in confirmation_validator

    assert (
        "hasExactKeys(contract, FROZEN_CONTRACT_KEYS)"
        in frozen_validator
    )
    assert "SHA256_PATTERN.test(contract.canonical_hash)" in frozen_validator
    assert "hasValidVersionLineage(contract)" in frozen_validator
    assert "isExactTargetUrl(contract.endpoint)" in frozen_validator
    assert "isTrustedTimestamp(contract.frozen_at)" in frozen_validator


def test_client_target_url_check_is_exact_but_server_remains_authority():
    javascript = _asset(JS_PATH)
    html = _asset(HTML_PATH)
    validator = _function(
        javascript,
        "isExactTargetUrl",
        "isTrustedApiPath",
    )

    assert "new URL(value)" in validator
    assert 'parsed.protocol === "https:"' in validator
    assert 'parsed.protocol === "http:"' in validator
    assert 'parsed.username === ""' in validator
    assert 'parsed.password === ""' in validator
    assert 'parsed.search === ""' in validator
    assert 'parsed.hash === ""' in validator
    assert "parsed.href === value" in validator
    assert "server remains authority" not in html.lower()
    assert "credentials, query, and fragment" in html


def test_create_draft_body_has_only_target_review_and_body_idempotency_fields():
    javascript = _asset(JS_PATH)
    submit = javascript.split(
        'draftForm.addEventListener("submit"', 1
    )[1].split(
        '\n  freezeForm.addEventListener("submit"', 1
    )[0]
    payload = submit.split("payload: {", 1)[1].split("},\n      };", 1)[0]

    for exact_field in (
        "target_provider",
        "endpoint_class",
        "endpoint",
        "model",
        "reviewer",
        "rationale",
        "idempotency_key",
    ):
        assert re.search(rf"\b{exact_field}\b", payload)
    assert payload.count("idempotency_key") == 1
    assert "JSON.stringify(pendingDraftAttempt.payload)" in submit
    assert 'method: "POST"' in submit
    assert '"Content-Type": "application/json"' in submit
    assert "Idempotency-Key" not in javascript
    assert "X-Idempotency" not in javascript

    for forbidden in (
        "approved",
        "confirmed",
        "freeze",
        "frozen",
        "run",
        "score",
        "verdict",
        "canonical_hash",
        "source_quote",
        "normalized_claim",
    ):
        assert re.search(rf"\b{forbidden}\b", payload, re.IGNORECASE) is None


def test_create_response_is_exact_and_echoes_every_target_field():
    javascript = _asset(JS_PATH)
    validator = _function(
        javascript,
        "isTrustedDraftActionResponse",
        "isTrustedFreezeActionResponse",
    )

    assert (
        'hasExactKeys(payload, ["disposition", "draft", "poc_id"])'
        in validator
    )
    assert "payload.poc_id === pocId" in validator
    assert "DISPOSITIONS.includes(payload.disposition)" in validator
    assert "isTrustedDraft(payload.draft)" in validator
    for field in (
        "target_provider",
        "endpoint_class",
        "endpoint",
        "model",
        "reviewer",
        "rationale",
    ):
        assert f"payload.draft.{field} === attempt.payload.{field}" in validator


def test_customer_review_is_authoritative_and_employee_self_attestation_is_retired():
    html = _asset(HTML_PATH)
    javascript = _asset(JS_PATH)
    review_validator = _function(
        javascript,
        "isTrustedCustomerReview",
        "isTrustedFrozenContract",
    )
    review_url_validator = _function(
        javascript,
        "isTrustedReviewUrl",
        "isTrustedApiPath",
    )
    keys = javascript.split("const CUSTOMER_REVIEW_KEYS", 1)[1].split(
        "]);", 1
    )[0]

    for exact_key in (
        "created_at",
        "expires_at",
        "review_id",
        "review_url",
        "status",
    ):
        assert f'"{exact_key}"' in keys
    for exact_status in (
        "PENDING",
        "EXPIRED",
        "CONFIRMED",
        "CHANGES_REQUESTED",
    ):
        assert f'"{exact_status}"' in review_validator
    assert "hasExactKeys(customerReview, CUSTOMER_REVIEW_KEYS)" in review_validator
    assert "REVIEW_ID_PATTERN.test(customerReview.review_id)" in review_validator
    assert "isTrustedReviewUrl(customerReview.review_url)" in review_validator
    assert "Date.parse(customerReview.expires_at)" in review_validator
    assert "REVIEW_URL_PATTERN.test(value)" in review_url_validator
    assert "new URL(value, window.location.origin)" in review_url_validator
    assert "parsed.origin === window.location.origin" in review_url_validator
    assert "parsed.pathname === value" in review_url_validator
    assert 'parsed.search === ""' in review_url_validator
    assert 'parsed.hash === ""' in review_url_validator

    assert 'id="confirmer"' not in html
    assert 'id="agreement-acknowledged"' not in html
    assert 'id="confirmation-form"' not in html
    assert 'id="confirm-agreement"' not in html
    assert "confirmationForm" not in javascript
    assert "pendingConfirmationAttempt" not in javascript
    assert "validatedConfirmationFields" not in javascript
    assert "confirmationApi" not in javascript
    assert "/agreement/confirm" not in javascript


def test_freeze_body_is_only_one_retry_stable_operation_key():
    javascript = _asset(JS_PATH)
    submit = javascript.split(
        'freezeForm.addEventListener("submit"', 1
    )[1].split(
        "\n  async function initialise", 1
    )[0]
    payload = submit.split("payload: {", 1)[1].split("},\n      };", 1)[0]

    assert re.fullmatch(
        r"\s*idempotency_key:\s*idempotencyKey,\s*",
        payload,
    )
    assert "JSON.stringify(pendingFreezeAttempt.payload)" in submit
    assert "freezeApi" in submit
    assert 'method: "POST"' in submit
    assert "agreement-freeze" in submit


def test_revision_is_explicit_retry_stable_and_bound_to_the_rejected_version():
    javascript = _asset(JS_PATH)
    revision = _function(
        javascript,
        "startRevision",
        "setCurrentStep",
    )
    validator = _function(
        javascript,
        "isTrustedRevisionActionResponse",
        "requestJson",
    )
    payload = revision.split("payload: {", 1)[1].split("},", 1)[0]

    assert re.fullmatch(
        r"\s*idempotency_key:\s*idempotencyKey\s*",
        payload,
    )
    assert 'agreementState.confirmation?.decision !== "REQUEST_CHANGES"' in (
        revision
    )
    assert 'newOperationKey("agreement-revision")' in revision
    assert "requestJson(revisionApi" in revision
    assert "JSON.stringify(pendingRevisionAttempt.payload)" in revision
    assert "isTrustedRevisionActionResponse(response)" in revision
    assert "window.location.replace(destination);" in revision
    assert 'hasExactKeys(payload, ["disposition", "poc_id", "revision"])' in (
        validator
    )
    assert 'agreementState?.confirmation?.decision === "REQUEST_CHANGES"' in (
        validator
    )
    assert "payload.revision.parent_contract_id" in validator
    assert "agreementState.draft.contract_id" in validator
    assert "payload.revision.parent_draft_sha256" in validator
    assert "agreementState.draft.draft_sha256" in validator


def test_retry_attempts_reuse_exact_in_memory_payloads():
    javascript = _asset(JS_PATH)

    for pending_name, prefix in (
        ("pendingDraftAttempt", "agreement-draft"),
        ("pendingReviewReissueAttempt", "agreement-review-reissue"),
        ("pendingFreezeAttempt", "agreement-freeze"),
        ("pendingRevisionAttempt", "agreement-revision"),
    ):
        assert f"if (!{pending_name})" in javascript
        assert f"{pending_name} = {{" in javascript
        assert f"JSON.stringify({pending_name}.payload)" in javascript
        assert f'newOperationKey("{prefix}")' in javascript
    assert "window.crypto.randomUUID()" in javascript
    assert "Math.random" not in javascript
    assert "response.status >= 500" in javascript
    assert "response.status === 408" in javascript
    assert "response.status === 429" in javascript
    assert "localStorage" not in javascript
    assert "sessionStorage" not in javascript

    reissue = _function(
        javascript,
        "reissueCustomerReview",
        "setCurrentStep",
    )
    payload = reissue.split("payload: {", 1)[1].split("},", 1)[0]
    assert re.fullmatch(
        r"\s*idempotency_key:\s*idempotencyKey\s*",
        payload,
    )
    assert "requestJson(reviewApi" in reissue
    assert 'method: "POST"' in reissue
    assert "JSON.stringify(pendingReviewReissueAttempt.payload)" in reissue
    assert "pendingReviewReissueAttempt = null;" in reissue


def test_customer_review_and_freeze_visibility_require_authoritative_refresh():
    javascript = _asset(JS_PATH)
    reconcile = _function(
        javascript,
        "reconcileAgreement",
        "blockAgreement",
    )
    renderer = _function(
        javascript,
        "renderAgreementState",
        "reconcileAgreement",
    )
    refresh = _function(
        javascript,
        "refreshCustomerReview",
        "reissueCustomerReview",
    )
    freeze_submit = javascript.split(
        'freezeForm.addEventListener("submit"', 1
    )[1].split(
        "\n  async function initialise", 1
    )[0]

    assert "requestJson(agreementApi)" in reconcile
    assert "isTrustedAgreementProjection(projection)" in reconcile
    assert "agreementState = projection;" in reconcile
    assert "renderAgreementState();" in reconcile
    assert "await reconcileAgreement();" in refresh
    assert "await reconcileAgreement();" in freeze_submit
    assert 'agreementState.confirmation.decision !== "CONFIRM"' in freeze_submit
    assert "showOnly(freezePanel);" in renderer
    freeze_branch = renderer.split(
        'if (agreementState.confirmation?.decision === "CONFIRM")', 1
    )[1].split("if (agreementState.draft !== null)", 1)[0]
    assert "showOnly(freezePanel);" in freeze_branch
    assert "updateFreezeControls();" in freeze_branch
    assert "showOnly(completionPanel);" in renderer
    assert "agreementState.frozen_contract !== null" in renderer


def test_authoritative_refresh_focus_and_decision_states_control_the_next_step():
    javascript = _asset(JS_PATH)
    reconcile = _function(
        javascript,
        "reconcileAgreement",
        "blockAgreement",
    )
    renderer = _function(
        javascript,
        "renderAgreementState",
        "reconcileAgreement",
    )
    review_renderer = _function(
        javascript,
        "renderCustomerReviewState",
        "refreshCustomerReview",
    )
    freeze_controls = _function(
        javascript,
        "updateFreezeControls",
        "definitionRule",
    )
    draft_submit = javascript.split(
        'draftForm.addEventListener("submit"', 1
    )[1].split(
        '\n  freezeForm.addEventListener("submit"', 1
    )[0]
    refresh = _function(
        javascript,
        "refreshCustomerReview",
        "reissueCustomerReview",
    )

    assert reconcile.index("agreementState = projection;") < reconcile.index(
        "inFlight = null;"
    ) < reconcile.index("renderAgreementState();")
    assert "await reconcileAgreement();" in draft_submit
    assert "await reconcileAgreement();" in refresh
    assert 'window.addEventListener("focus", () =>' in javascript
    focus_handler = javascript.split(
        'window.addEventListener("focus", () =>', 1
    )[1].split("});", 1)[0]
    assert 'agreementState?.customer_review?.status === "PENDING"' in focus_handler
    assert "refreshCustomerReview();" in focus_handler

    review_branch = renderer.split(
        "if (agreementState.draft !== null)", 1
    )[1].split("showOnly(draftForm);", 1)[0]
    assert "showOnly(confirmationPanel);" in review_branch
    assert "renderCustomerReviewState();" in review_branch
    assert 'agreementState.confirmation?.decision === "REQUEST_CHANGES"' in (
        review_branch
    )
    assert '"Customer requested changes"' in review_branch
    assert "startRevisionButton.focus" in review_branch
    assert 'customerReview.status === "EXPIRED"' in review_renderer
    assert 'customerReviewState.textContent = "Waiting for customer"' in (
        review_renderer
    )
    assert 'customerReviewHeading.textContent = "Customer requested changes"' in (
        review_renderer
    )
    assert "reissueCustomerReviewButton.hidden = !expired;" in review_renderer

    freeze_branch = renderer.split(
        'if (agreementState.confirmation?.decision === "CONFIRM")', 1
    )[1].split("if (agreementState.draft !== null)", 1)[0]
    assert "showOnly(freezePanel);" in freeze_branch
    assert "updateFreezeControls();" in freeze_branch
    assert "freezeButton.disabled = !available || inFlight !== null;" in (
        freeze_controls
    )


def test_completion_is_honest_and_links_to_the_real_proof_route():
    html = _asset(HTML_PATH)
    completion = html.split('id="agreement-complete"', 1)[1].split(
        'id="agreement-error"', 1
    )[0]

    assert "The frozen proof is ready" in completion
    assert (
        "No run, evidence score, pass/fail decision, or verdict was\n"
        "                  created on this screen."
    ) in completion
    assert 'href="' not in completion
    assert 'id="continue-to-proof"' in completion
    assert "Continue to proof" in completion
    assert "<button" not in completion
    assert "Run POC" not in completion
    assert "PASS" not in completion
    assert "FAIL" not in completion
    javascript = _asset(JS_PATH)
    state = _function(
        javascript,
        "renderAgreementState",
        "validatedDraftFields",
    )
    assert "`/app/pocs/${encodeURIComponent(pocId)}`" in state
    assert "continueToProof.href = destination;" in state
    assert "showOnly(completionPanel);" in state
    assert "window.location.replace(destination);" in state


def test_safety_copy_distinguishes_draft_confirmation_freeze_and_execution():
    html = _asset(HTML_PATH)

    assert "Human confirmation required · no execution yet" in html
    assert "This screen cannot\n                edit them, execute a run" in html
    assert "This screen cannot confirm for them." in html
    assert (
        "Freezing still does not run the POC, score evidence, or\n"
        "                    produce a verdict."
    ) in html
    assert "The confirmed contract is frozen. Execution remains separate." in _asset(
        JS_PATH
    )


def test_sensitive_content_uses_safe_text_rendering_and_no_browser_persistence():
    javascript = _asset(JS_PATH)

    for forbidden in (
        "localStorage",
        "sessionStorage",
        "console.",
        "innerHTML",
        "outerHTML",
        "insertAdjacentHTML",
        "document.write",
        "history.",
        "location.assign",
        "document.cookie",
    ):
        assert forbidden not in javascript
    assert ".textContent =" in javascript
    assert "document.createElement(" in javascript
    assert ".replaceChildren(" in javascript
    assert 'window.addEventListener("pagehide"' in javascript
    assert "payload.error" not in javascript
    assert "error.message" not in javascript
    assert "response.text" not in javascript
    state = _function(
        javascript,
        "renderAgreementState",
        "validatedDraftFields",
    )
    assert "window.location.replace(destination);" in state
    navigation = state.split("const destination =", 1)[1].split(";", 1)[0]
    assert "pocId" in navigation
    assert "source_quote" not in navigation
    assert "normalized_claim" not in navigation


def test_transport_is_same_origin_json_only_no_store_and_redirect_closed():
    javascript = _asset(JS_PATH)
    request = _function(
        javascript,
        "requestJson",
        "newOperationKey",
    )
    path_validator = _function(
        javascript,
        "isTrustedApiPath",
        "isTrustedDefinition",
    )

    assert "isTrustedApiPath(path)" in request
    assert 'credentials: "same-origin"' in request
    assert 'cache: "no-store"' in request
    assert 'redirect: "error"' in request
    assert 'referrerPolicy: "same-origin"' in request
    assert 'Accept: "application/json"' in request
    assert (
        'contentType.split(";", 1)[0].trim() !== "application/json"'
        in request
    )
    assert "responseUrl.origin !== window.location.origin" in request
    assert "responseUrl.pathname !== path" in request
    assert "responseUrl.search !==" in request
    assert "responseUrl.hash !==" in request
    assert "parsed.origin === window.location.origin" in path_validator
    assert "value === agreementApi" in path_validator
    assert "value === reviewApi" in path_validator
    assert "value === freezeApi" in path_validator
    assert "value === revisionApi" in path_validator
    assert "confirmationApi" not in javascript
    assert "/agreement/confirm" not in javascript


def test_css_uses_established_theme_finite_panels_reflow_and_focus():
    css = _asset(CSS_PATH)
    dashboard_css = _asset(STATIC_ROOT / "dashboard.css")

    assert "var(--orange)" in css
    assert "var(--canvas)" in css
    assert "var(--panel)" in css
    assert "var(--navigation)" in css
    assert "var(--orange-dark)" in css
    assert "#000" not in css.lower()
    assert "gradient" not in css.lower()
    assert "backdrop-filter" not in css.lower()
    assert "box-shadow" not in css.lower()
    assert ".agreement-layout" in css
    assert ".action-column" in css
    assert "overflow: hidden;" in css
    assert "overflow: auto;" in css
    assert ".definition-list" in css
    assert ".customer-review-scroll" in css
    assert "@media (max-width: 900px), (max-height: 680px)" in css
    assert "@media (max-width: 620px)" in css
    assert ":focus-visible" in css
    assert "@media (prefers-reduced-motion: reduce)" in css
    assert "--canvas: #0e141b;" in dashboard_css
    assert "--orange: #e87849;" in dashboard_css


def test_javascript_parses_with_node():
    completed = subprocess.run(
        ["node", "--check", str(JS_PATH)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
