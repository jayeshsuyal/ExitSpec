(() => {
  "use strict";

  const POC_ID_PATTERN = /^poc_[a-z0-9][a-z0-9_-]{2,63}$/;
  const PROPOSAL_ID_PATTERN = /^prop_[a-z0-9][a-z0-9_-]{7,95}$/;
  const DEFINITION_ID_PATTERN = /^cdef_[a-f0-9]{32}$/;
  const DRAFT_ID_PATTERN = /^agd_[a-f0-9]{32,64}$/;
  const CONFIRMATION_ID_PATTERN = /^cnf_[a-f0-9]{64}$/;
  const SHA256_PATTERN = /^[a-f0-9]{64}$/;
  const ROUTE_PATTERN =
    /^\/app\/pocs\/(poc_[a-z0-9][a-z0-9_-]{2,63})\/agreement$/;
  const METRICS = Object.freeze([
    "TTFT_P95_MS",
    "ERROR_RATE_PERCENT",
  ]);
  const METRIC_LABELS = Object.freeze({
    TTFT_P95_MS: "P95 time to first token",
    ERROR_RATE_PERCENT: "Error rate",
  });
  const METRIC_CONFIG = Object.freeze({
    TTFT_P95_MS: Object.freeze({
      unit: "MILLISECONDS",
      shortUnit: "ms",
      minimum: 0.001,
      maximum: 60000,
    }),
    ERROR_RATE_PERCENT: Object.freeze({
      unit: "PERCENT",
      shortUnit: "%",
      minimum: 0,
      maximum: 100,
    }),
  });
  const SOURCE_KINDS = Object.freeze([
    "EMAIL",
    "MEETING",
    "DOCUMENT",
    "EXISTING_CONTRACT",
  ]);
  const SOURCE_LABELS = Object.freeze({
    EMAIL: "Email",
    MEETING: "Meeting",
    DOCUMENT: "Document",
    EXISTING_CONTRACT: "Existing contract",
  });
  const DISPOSITIONS = Object.freeze([
    "CREATED",
    "IDEMPOTENT_REPLAY",
  ]);
  const AGREEMENT_KEYS = Object.freeze([
    "confirmation",
    "definitions",
    "draft",
    "frozen_contract",
    "poc_id",
  ]);
  const DEFINITION_KEYS = Object.freeze([
    "concurrency",
    "defined_at",
    "definition_id",
    "definition_sha256",
    "metric",
    "minimum_samples",
    "normalized_claim",
    "operator",
    "output_tokens_max",
    "output_tokens_min",
    "prompt_tokens_max",
    "prompt_tokens_min",
    "proposal_id",
    "source_kind",
    "source_quote",
    "threshold",
    "unit",
  ]);
  const DRAFT_KEYS = Object.freeze([
    "created_at",
    "draft_id",
    "draft_sha256",
    "endpoint",
    "endpoint_class",
    "model",
    "rationale",
    "reviewer",
    "target_provider",
  ]);
  const CONFIRMATION_KEYS = Object.freeze([
    "agreement_acknowledged",
    "confirmation_id",
    "confirmed_at",
    "confirmer",
    "draft_sha256",
    "rationale",
  ]);
  const FROZEN_CONTRACT_KEYS = Object.freeze([
    "canonical_hash",
    "confirmation_id",
    "contract_id",
    "endpoint",
    "endpoint_class",
    "frozen_at",
    "model",
    "target_provider",
  ]);
  const routeMatch =
    window.location.search === "" && window.location.hash === ""
      ? window.location.pathname.match(ROUTE_PATTERN)
      : null;
  const pocId =
    routeMatch && POC_ID_PATTERN.test(routeMatch[1]) ? routeMatch[1] : null;
  const agreementApi = pocId ? `/api/pocs/${pocId}/agreement` : null;
  const confirmationApi = agreementApi ? `${agreementApi}/confirm` : null;
  const freezeApi = agreementApi ? `${agreementApi}/freeze` : null;

  const workbench = document.querySelector("#agreement-workbench");
  const draftForm = document.querySelector("#create-draft-form");
  const targetProviderInput = document.querySelector("#target-provider");
  const endpointClassInput = document.querySelector("#endpoint-class");
  const endpointInput = document.querySelector("#endpoint");
  const modelInput = document.querySelector("#model");
  const draftReviewerInput = document.querySelector("#draft-reviewer");
  const draftRationaleInput = document.querySelector("#draft-rationale");
  const createDraftButton = document.querySelector("#create-customer-draft");
  const draftStatus = document.querySelector("#draft-status");
  const endpointDetails = document.querySelector(".endpoint-fields");
  const confirmationPanel = document.querySelector("#confirmation-panel");
  const confirmationForm = document.querySelector("#confirmation-form");
  const confirmerInput = document.querySelector("#confirmer");
  const confirmationRationaleInput = document.querySelector(
    "#confirmation-rationale"
  );
  const acknowledgementInput = document.querySelector(
    "#agreement-acknowledged"
  );
  const confirmButton = document.querySelector("#confirm-agreement");
  const confirmationStatus = document.querySelector("#confirmation-status");
  const freezePanel = document.querySelector("#freeze-panel");
  const freezeForm = document.querySelector("#freeze-form");
  const freezeButton = document.querySelector("#freeze-contract");
  const freezeStatus = document.querySelector("#freeze-status");
  const completionPanel = document.querySelector("#agreement-complete");
  const continueToProof = document.querySelector("#continue-to-proof");
  const errorPanel = document.querySelector("#agreement-error");
  const draftControls = Object.freeze([
    targetProviderInput,
    endpointClassInput,
    endpointInput,
    modelInput,
    draftReviewerInput,
    draftRationaleInput,
  ]);
  const confirmationControls = Object.freeze([
    confirmerInput,
    confirmationRationaleInput,
    acknowledgementInput,
  ]);

  let agreementState = null;
  let inFlight = null;
  let pendingDraftAttempt = null;
  let pendingConfirmationAttempt = null;
  let pendingFreezeAttempt = null;

  class SafeRequestError extends Error {
    constructor(statusCode, retrySameAttempt) {
      super("Agreement request failed.");
      this.name = "SafeRequestError";
      this.statusCode = statusCode;
      this.retrySameAttempt = retrySameAttempt;
    }
  }

  function hasExactKeys(payload, keys) {
    if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
      return false;
    }
    const actual = Object.keys(payload).sort();
    const expected = [...keys].sort();
    return (
      actual.length === expected.length &&
      actual.every((key, index) => key === expected[index])
    );
  }

  function isSafeBoundedText(value, maximum) {
    return (
      typeof value === "string" &&
      value.trim().length > 0 &&
      value.length <= maximum &&
      !/[\u0000-\u0008\u000b\u000c\u000e-\u001f]/.test(value)
    );
  }

  function isSingleLineText(value, maximum) {
    return (
      isSafeBoundedText(value, maximum) &&
      value === value.trim() &&
      !value.includes("\n") &&
      !value.includes("\r")
    );
  }

  function isExactInteger(value, minimum, maximum) {
    return (
      typeof value === "number" &&
      Number.isInteger(value) &&
      value >= minimum &&
      value <= maximum
    );
  }

  function isTrustedTimestamp(value) {
    return Boolean(
      typeof value === "string" &&
        /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$/.test(
          value
        ) &&
        Number.isFinite(Date.parse(value))
    );
  }

  function isExactTargetUrl(value) {
    if (
      !isSingleLineText(value, 2048) ||
      value.includes("?") ||
      value.includes("#")
    ) {
      return false;
    }
    try {
      const parsed = new URL(value);
      return Boolean(
        (parsed.protocol === "https:" || parsed.protocol === "http:") &&
          parsed.hostname.length > 0 &&
          parsed.username === "" &&
          parsed.password === "" &&
          parsed.search === "" &&
          parsed.hash === "" &&
          parsed.href === value
      );
    } catch {
      return false;
    }
  }

  function isTrustedApiPath(value) {
    if (
      typeof value !== "string" ||
      !pocId ||
      value.includes("?") ||
      value.includes("#")
    ) {
      return false;
    }
    try {
      const parsed = new URL(value, window.location.origin);
      return Boolean(
        parsed.origin === window.location.origin &&
          parsed.pathname === value &&
          parsed.search === "" &&
          parsed.hash === "" &&
          (value === agreementApi ||
            value === confirmationApi ||
            value === freezeApi)
      );
    } catch {
      return false;
    }
  }

  function isTrustedDefinition(definition) {
    if (
      !hasExactKeys(definition, DEFINITION_KEYS) ||
      !PROPOSAL_ID_PATTERN.test(definition.proposal_id) ||
      !DEFINITION_ID_PATTERN.test(definition.definition_id) ||
      !SHA256_PATTERN.test(definition.definition_sha256) ||
      !METRICS.includes(definition.metric) ||
      !SOURCE_KINDS.includes(definition.source_kind) ||
      !isSafeBoundedText(definition.source_quote, 4000) ||
      !isSafeBoundedText(definition.normalized_claim, 2000)
    ) {
      return false;
    }
    const config = METRIC_CONFIG[definition.metric];
    const operatorValid =
      definition.metric === "TTFT_P95_MS"
        ? ["LT", "LTE"].includes(definition.operator)
        : definition.operator === "LT";
    const thresholdValid =
      definition.metric === "TTFT_P95_MS"
        ? definition.threshold > 0 && definition.threshold <= config.maximum
        : definition.threshold > 0 && definition.threshold < config.maximum;
    return Boolean(
      definition.unit === config.unit &&
        operatorValid &&
        typeof definition.threshold === "number" &&
        Number.isFinite(definition.threshold) &&
        thresholdValid &&
        isExactInteger(definition.minimum_samples, 1, 1000) &&
        isExactInteger(definition.concurrency, 1, 32) &&
        definition.concurrency <= definition.minimum_samples &&
        isExactInteger(definition.prompt_tokens_min, 1, 1000000) &&
        isExactInteger(definition.prompt_tokens_max, 1, 1000000) &&
        definition.prompt_tokens_min <= definition.prompt_tokens_max &&
        isExactInteger(definition.output_tokens_min, 1, 1000000) &&
        isExactInteger(definition.output_tokens_max, 1, 1000000) &&
        definition.output_tokens_min <= definition.output_tokens_max &&
        isTrustedTimestamp(definition.defined_at)
    );
  }

  function hasExecutableDefinitionPair(definitions) {
    return Boolean(
      Array.isArray(definitions) &&
        definitions.length === 2 &&
        METRICS.every(
          (metric) =>
            definitions.filter((definition) => definition.metric === metric)
              .length === 1
        )
    );
  }

  function isTrustedDraft(draft) {
    return Boolean(
      hasExactKeys(draft, DRAFT_KEYS) &&
        DRAFT_ID_PATTERN.test(draft.draft_id) &&
        SHA256_PATTERN.test(draft.draft_sha256) &&
        isTrustedTimestamp(draft.created_at) &&
        isSingleLineText(draft.target_provider, 160) &&
        isSingleLineText(draft.endpoint_class, 160) &&
        isExactTargetUrl(draft.endpoint) &&
        isSingleLineText(draft.model, 300) &&
        isSingleLineText(draft.reviewer, 160) &&
        isSafeBoundedText(draft.rationale, 2000)
    );
  }

  function isTrustedConfirmation(confirmation) {
    return Boolean(
      hasExactKeys(confirmation, CONFIRMATION_KEYS) &&
        CONFIRMATION_ID_PATTERN.test(confirmation.confirmation_id) &&
        SHA256_PATTERN.test(confirmation.draft_sha256) &&
        isTrustedTimestamp(confirmation.confirmed_at) &&
        isSingleLineText(confirmation.confirmer, 160) &&
        confirmation.agreement_acknowledged === true &&
        isSafeBoundedText(confirmation.rationale, 2000)
    );
  }

  function isTrustedFrozenContract(contract) {
    return Boolean(
      hasExactKeys(contract, FROZEN_CONTRACT_KEYS) &&
        isSingleLineText(contract.contract_id, 160) &&
        SHA256_PATTERN.test(contract.canonical_hash) &&
        CONFIRMATION_ID_PATTERN.test(contract.confirmation_id) &&
        isTrustedTimestamp(contract.frozen_at) &&
        isSingleLineText(contract.target_provider, 160) &&
        isSingleLineText(contract.endpoint_class, 160) &&
        isExactTargetUrl(contract.endpoint) &&
        isSingleLineText(contract.model, 300)
    );
  }

  function targetMatches(left, right) {
    return Boolean(
      left.target_provider === right.target_provider &&
        left.endpoint_class === right.endpoint_class &&
        left.endpoint === right.endpoint &&
        left.model === right.model
    );
  }

  function isTrustedAgreementProjection(payload) {
    if (
      !hasExactKeys(payload, AGREEMENT_KEYS) ||
      payload.poc_id !== pocId ||
      !Array.isArray(payload.definitions) ||
      payload.definitions.length > 1024 ||
      !payload.definitions.every(isTrustedDefinition) ||
      (payload.draft !== null && !isTrustedDraft(payload.draft)) ||
      (payload.confirmation !== null &&
        !isTrustedConfirmation(payload.confirmation)) ||
      (payload.frozen_contract !== null &&
        !isTrustedFrozenContract(payload.frozen_contract))
    ) {
      return false;
    }
    const proposalIds = payload.definitions.map(
      (definition) => definition.proposal_id
    );
    const definitionIds = payload.definitions.map(
      (definition) => definition.definition_id
    );
    if (
      new Set(proposalIds).size !== proposalIds.length ||
      new Set(definitionIds).size !== definitionIds.length
    ) {
      return false;
    }
    if (
      payload.draft === null &&
      (payload.confirmation !== null || payload.frozen_contract !== null)
    ) {
      return false;
    }
    if (
      payload.confirmation !== null &&
      (payload.draft === null ||
        payload.confirmation.draft_sha256 !== payload.draft.draft_sha256)
    ) {
      return false;
    }
    if (
      payload.frozen_contract !== null &&
      (payload.draft === null ||
        payload.confirmation === null ||
        payload.frozen_contract.confirmation_id !==
          payload.confirmation.confirmation_id ||
        !targetMatches(payload.frozen_contract, payload.draft))
    ) {
      return false;
    }
    return true;
  }

  function isTrustedDraftActionResponse(payload, attempt) {
    return Boolean(
      hasExactKeys(payload, ["disposition", "draft", "poc_id"]) &&
        payload.poc_id === pocId &&
        DISPOSITIONS.includes(payload.disposition) &&
        isTrustedDraft(payload.draft) &&
        payload.draft.target_provider === attempt.payload.target_provider &&
        payload.draft.endpoint_class === attempt.payload.endpoint_class &&
        payload.draft.endpoint === attempt.payload.endpoint &&
        payload.draft.model === attempt.payload.model &&
        payload.draft.reviewer === attempt.payload.reviewer &&
        payload.draft.rationale === attempt.payload.rationale
    );
  }

  function isTrustedConfirmationActionResponse(payload, attempt) {
    return Boolean(
      hasExactKeys(payload, ["confirmation", "disposition", "poc_id"]) &&
        payload.poc_id === pocId &&
        DISPOSITIONS.includes(payload.disposition) &&
        isTrustedConfirmation(payload.confirmation) &&
        payload.confirmation.confirmer === attempt.payload.confirmer &&
        payload.confirmation.agreement_acknowledged === true &&
        payload.confirmation.rationale === attempt.payload.rationale
    );
  }

  function isTrustedFreezeActionResponse(payload) {
    return Boolean(
      hasExactKeys(payload, ["disposition", "frozen_contract", "poc_id"]) &&
        payload.poc_id === pocId &&
        DISPOSITIONS.includes(payload.disposition) &&
        isTrustedFrozenContract(payload.frozen_contract) &&
        agreementState &&
        agreementState.draft &&
        agreementState.confirmation &&
        payload.frozen_contract.confirmation_id ===
          agreementState.confirmation.confirmation_id &&
        targetMatches(payload.frozen_contract, agreementState.draft)
    );
  }

  async function requestJson(path, options = {}) {
    if (!isTrustedApiPath(path)) {
      throw new SafeRequestError(null, true);
    }

    let response;
    try {
      response = await fetch(path, {
        ...options,
        cache: "no-store",
        credentials: "same-origin",
        redirect: "error",
        referrerPolicy: "same-origin",
        headers: {
          Accept: "application/json",
          ...(options.headers || {}),
        },
      });
    } catch {
      throw new SafeRequestError(null, true);
    }

    let responseUrl;
    try {
      responseUrl = new URL(response.url || path, window.location.origin);
    } catch {
      throw new SafeRequestError(response.status, true);
    }
    if (
      responseUrl.origin !== window.location.origin ||
      responseUrl.pathname !== path ||
      responseUrl.search !== "" ||
      responseUrl.hash !== ""
    ) {
      throw new SafeRequestError(response.status, true);
    }
    if (!response.ok) {
      const retrySameAttempt =
        response.status >= 500 ||
        response.status === 408 ||
        response.status === 429;
      throw new SafeRequestError(response.status, retrySameAttempt);
    }
    const contentType = (
      response.headers.get("content-type") || ""
    ).toLowerCase();
    if (contentType.split(";", 1)[0].trim() !== "application/json") {
      throw new SafeRequestError(response.status, true);
    }
    const payload = await response.json().catch(() => null);
    if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
      throw new SafeRequestError(response.status, true);
    }
    return payload;
  }

  function newOperationKey(prefix) {
    if (
      !window.crypto ||
      typeof window.crypto.randomUUID !== "function"
    ) {
      return null;
    }
    return `${prefix}-${window.crypto.randomUUID()}`;
  }

  function clearError() {
    errorPanel.hidden = true;
    errorPanel.textContent = "";
  }

  function safeFailureCopy(error, action) {
    if (!(error instanceof SafeRequestError)) {
      return `The ${action} action could not be completed safely. Retry the same attempt.`;
    }
    if (error.statusCode === 400 || error.statusCode === 422) {
      return `The ${action} request was not accepted. Review every required value.`;
    }
    if (error.statusCode === 404) {
      return "This POC agreement is unavailable. No lifecycle action was completed.";
    }
    if (
      error.statusCode === 403 ||
      error.statusCode === 409 ||
      error.statusCode === 415
    ) {
      return `The ${action} request conflicts with the current agreement state. Reload before continuing.`;
    }
    return `The ${action} response was interrupted or could not be trusted. Retry uses the same operation key.`;
  }

  function validatedDraftFields() {
    const targetProvider = targetProviderInput.value.trim();
    const endpointClass = endpointClassInput.value.trim();
    const endpoint = endpointInput.value.trim();
    const model = modelInput.value.trim();
    const reviewer = draftReviewerInput.value.trim();
    const rationale = draftRationaleInput.value.trim();
    if (
      !isSingleLineText(targetProvider, 160) ||
      !isSingleLineText(endpointClass, 160) ||
      !isExactTargetUrl(endpoint) ||
      !isSingleLineText(model, 300) ||
      !isSingleLineText(reviewer, 160) ||
      !isSafeBoundedText(rationale, 2000)
    ) {
      return null;
    }
    return {
      target_provider: targetProvider,
      endpoint_class: endpointClass,
      endpoint,
      model,
      reviewer,
      rationale,
    };
  }

  function validatedConfirmationFields() {
    const confirmer = confirmerInput.value.trim();
    const rationale = confirmationRationaleInput.value.trim();
    if (
      !isSingleLineText(confirmer, 160) ||
      !isSafeBoundedText(rationale, 2000) ||
      acknowledgementInput.checked !== true
    ) {
      return null;
    }
    return {
      confirmer,
      agreement_acknowledged: true,
      rationale,
    };
  }

  function setControlsAvailability(controls, enabled) {
    controls.forEach((control) => {
      control.disabled = !enabled;
    });
  }

  function updateDraftControls() {
    const available = Boolean(
      agreementState &&
        hasExecutableDefinitionPair(agreementState.definitions) &&
        agreementState.draft === null
    );
    const fieldsValid = validatedDraftFields() !== null;
    const editable =
      available && inFlight === null && pendingDraftAttempt === null;
    setControlsAvailability(draftControls, editable);
    createDraftButton.disabled = Boolean(
      !available ||
        inFlight !== null ||
        (!pendingDraftAttempt && !fieldsValid)
    );
    createDraftButton.textContent = pendingDraftAttempt
      ? "Retry create customer draft"
      : "Create customer draft";
    draftStatus.textContent =
      inFlight === "draft"
        ? "Creating the bounded customer draft…"
        : pendingDraftAttempt
          ? "The response was interrupted. Retry will reuse the same draft key."
          : fieldsValid
            ? "Verify the target and create the customer draft."
            : "Complete the exact target, reviewer, and rationale.";
  }

  function updateConfirmationControls() {
    const available = Boolean(
      agreementState &&
        agreementState.draft !== null &&
        agreementState.confirmation === null
    );
    const fieldsValid = validatedConfirmationFields() !== null;
    const editable =
      available && inFlight === null && pendingConfirmationAttempt === null;
    setControlsAvailability(confirmationControls, editable);
    confirmButton.disabled = Boolean(
      !available ||
        inFlight !== null ||
        (!pendingConfirmationAttempt && !fieldsValid)
    );
    confirmButton.textContent = pendingConfirmationAttempt
      ? "Retry confirm agreement"
      : "Confirm agreement";
    confirmationStatus.textContent =
      inFlight === "confirmation"
        ? "Recording the customer confirmation…"
        : pendingConfirmationAttempt
          ? "The response was interrupted. Retry will reuse the same confirmation key."
          : fieldsValid
            ? "Ready to record this explicit customer confirmation."
            : "Customer identity, rationale, and acknowledgement are required.";
  }

  function updateFreezeControls() {
    const available = Boolean(
      agreementState &&
        agreementState.draft !== null &&
        agreementState.confirmation !== null &&
        agreementState.frozen_contract === null
    );
    freezeButton.disabled = !available || inFlight !== null;
    freezeButton.textContent = pendingFreezeAttempt
      ? "Retry freeze confirmed contract"
      : "Freeze confirmed contract";
    freezeStatus.textContent =
      inFlight === "freeze"
        ? "Freezing the confirmed contract…"
        : pendingFreezeAttempt
          ? "The response was interrupted. Retry will reuse the same freeze key."
          : "Recorded confirmation matches the exact agreement.";
  }

  function definitionRule(definition) {
    const config = METRIC_CONFIG[definition.metric];
    const operator = definition.operator === "LT" ? "<" : "≤";
    return `${METRIC_LABELS[definition.metric]} ${operator} ${definition.threshold} ${config.shortUnit}`;
  }

  function appendDefinitionCard(definition, position) {
    const card = document.createElement("article");
    card.className = "definition-card";
    const header = document.createElement("header");
    const heading = document.createElement("h4");
    heading.textContent = `Criterion ${position + 1}`;
    const sourceKind = document.createElement("span");
    sourceKind.textContent = SOURCE_LABELS[definition.source_kind];
    header.append(heading, sourceKind);

    const quote = document.createElement("blockquote");
    quote.textContent = definition.source_quote;
    const rule = document.createElement("p");
    rule.className = "definition-rule";
    rule.textContent = definitionRule(definition);
    const workload = document.createElement("p");
    workload.className = "definition-workload";
    workload.textContent =
      `${definition.minimum_samples} samples · concurrency ${definition.concurrency} · ` +
      `prompt ${definition.prompt_tokens_min}–${definition.prompt_tokens_max} · ` +
      `output ${definition.output_tokens_min}–${definition.output_tokens_max}`;
    card.append(header, quote, rule, workload);
    document.querySelector("#definition-list").append(card);
  }

  function renderDefinitions() {
    const list = document.querySelector("#definition-list");
    list.replaceChildren();
    const definitions = agreementState.definitions;
    document.querySelector("#definition-count").textContent =
      `${definitions.length} ${definitions.length === 1 ? "definition" : "definitions"}`;
    if (definitions.length === 0) {
      const empty = document.createElement("p");
      empty.className = "empty-copy";
      empty.textContent =
        "No bounded definitions are available. Agreement drafting remains blocked.";
      list.append(empty);
      return;
    }
    definitions.forEach(appendDefinitionCard);
  }

  function appendCustomerCriterion(definition) {
    const row = document.createElement("p");
    row.className = "customer-criterion";
    const claim = document.createElement("span");
    claim.textContent = definition.normalized_claim;
    const rule = document.createElement("strong");
    rule.textContent = definitionRule(definition);
    row.append(claim, rule);
    document.querySelector("#customer-criteria-list").append(row);
  }

  function renderCustomerAgreement() {
    const draft = agreementState.draft;
    document.querySelector("#review-target-provider").textContent =
      draft.target_provider;
    document.querySelector("#review-model").textContent = draft.model;
    document.querySelector("#review-endpoint-class").textContent =
      draft.endpoint_class;
    document.querySelector("#review-endpoint").textContent = draft.endpoint;
    const criteria = document.querySelector("#customer-criteria-list");
    criteria.replaceChildren();
    agreementState.definitions.forEach(appendCustomerCriterion);
  }

  function setCurrentStep(stepName) {
    const steps = [
      ["draft", document.querySelector("#step-draft")],
      ["confirm", document.querySelector("#step-confirm")],
      ["freeze", document.querySelector("#step-freeze")],
    ];
    const currentIndex = steps.findIndex(([name]) => name === stepName);
    steps.forEach(([, element], index) => {
      element.removeAttribute("aria-current");
      element.removeAttribute("data-complete");
      if (index < currentIndex || stepName === "complete") {
        element.setAttribute("data-complete", "true");
      } else if (index === currentIndex) {
        element.setAttribute("aria-current", "step");
      }
    });
  }

  function showOnly(panel) {
    [draftForm, confirmationPanel, freezePanel, completionPanel].forEach(
      (item) => {
        item.hidden = item !== panel;
      }
    );
  }

  function renderAgreementState() {
    renderDefinitions();
    workbench.setAttribute("aria-busy", "false");

    if (agreementState.frozen_contract !== null) {
      continueToProof.href = `/app/pocs/${pocId}`;
      showOnly(completionPanel);
      setCurrentStep("complete");
      document.querySelector("#current-task-heading").textContent =
        "Agreement lifecycle complete";
      document.querySelector("#current-task-copy").textContent =
        "The confirmed contract is frozen. Execution remains separate.";
      document.querySelector("#frozen-contract-identity").textContent =
        `${agreementState.frozen_contract.contract_id} · ${agreementState.frozen_contract.canonical_hash.slice(0, 12)}…`;
      completionPanel.focus();
      return;
    }

    if (agreementState.confirmation !== null) {
      showOnly(freezePanel);
      setCurrentStep("freeze");
      document.querySelector("#current-task-heading").textContent =
        "Freeze the confirmed contract";
      document.querySelector("#current-task-copy").textContent =
        "Create the immutable identity for this exact confirmed agreement.";
      document.querySelector("#confirmed-by").textContent =
        agreementState.confirmation.confirmer;
      document.querySelector("#confirmed-draft-identity").textContent =
        `${agreementState.draft.draft_id} · ${agreementState.draft.draft_sha256.slice(0, 12)}…`;
      updateFreezeControls();
      freezePanel.focus();
      return;
    }

    if (agreementState.draft !== null) {
      showOnly(confirmationPanel);
      setCurrentStep("confirm");
      document.querySelector("#current-task-heading").textContent =
        "Confirm the customer agreement";
      document.querySelector("#current-task-copy").textContent =
        "Show the exact target and criteria before recording confirmation.";
      renderCustomerAgreement();
      updateConfirmationControls();
      document.querySelector("#customer-agreement").focus?.();
      return;
    }

    showOnly(draftForm);
    setCurrentStep("draft");
    document.querySelector("#current-task-heading").textContent =
      "Prepare the customer agreement";
    document.querySelector("#current-task-copy").textContent =
      "Bind the reviewed definitions to one explicit execution target.";
    updateDraftControls();
    document.querySelector("#definition-summary").focus?.();
  }

  async function reconcileAgreement() {
    const projection = await requestJson(agreementApi);
    if (!isTrustedAgreementProjection(projection)) {
      throw new SafeRequestError(200, true);
    }
    agreementState = projection;
    inFlight = null;
    renderAgreementState();
  }

  function blockAgreement(message) {
    workbench.setAttribute("aria-busy", "false");
    setControlsAvailability(draftControls, false);
    setControlsAvailability(confirmationControls, false);
    createDraftButton.disabled = true;
    confirmButton.disabled = true;
    freezeButton.disabled = true;
    draftStatus.textContent = "Agreement drafting is unavailable.";
    confirmationStatus.textContent = "Agreement confirmation is unavailable.";
    freezeStatus.textContent = "Contract freeze is unavailable.";
    errorPanel.textContent = message;
    errorPanel.hidden = false;
  }

  draftControls.forEach((control) => {
    control.addEventListener("input", () => {
      if (inFlight === null && pendingDraftAttempt === null) {
        clearError();
        updateDraftControls();
      }
    });
  });

  confirmationControls.forEach((control) => {
    control.addEventListener("input", () => {
      if (inFlight === null && pendingConfirmationAttempt === null) {
        clearError();
        updateConfirmationControls();
      }
    });
    control.addEventListener("change", () => {
      if (inFlight === null && pendingConfirmationAttempt === null) {
        clearError();
        updateConfirmationControls();
      }
    });
  });

  draftForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    let actionRecorded = false;
    if (
      inFlight !== null ||
      !agreementState ||
      agreementState.draft !== null ||
      agreementState.definitions.length === 0
    ) {
      return;
    }
    if (!pendingDraftAttempt) {
      const fields = validatedDraftFields();
      const idempotencyKey = newOperationKey("agreement-draft");
      if (!fields || !idempotencyKey || !draftForm.reportValidity()) {
        endpointDetails.open = true;
        draftStatus.textContent =
          "Review the complete execution target and required authoring fields.";
        return;
      }
      pendingDraftAttempt = {
        payload: {
          target_provider: fields.target_provider,
          endpoint_class: fields.endpoint_class,
          endpoint: fields.endpoint,
          model: fields.model,
          reviewer: fields.reviewer,
          rationale: fields.rationale,
          idempotency_key: idempotencyKey,
        },
      };
    }

    inFlight = "draft";
    clearError();
    updateDraftControls();
    try {
      const response = await requestJson(agreementApi, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify(pendingDraftAttempt.payload),
      });
      if (!isTrustedDraftActionResponse(response, pendingDraftAttempt)) {
        throw new SafeRequestError(200, true);
      }
      pendingDraftAttempt = null;
      actionRecorded = true;
      await reconcileAgreement();
      draftControls.forEach((control) => {
        control.value = "";
      });
    } catch (error) {
      if (actionRecorded) {
        agreementState = null;
        blockAgreement(
          "The customer draft was recorded, but the authoritative agreement state could not be refreshed. Reload before continuing."
        );
        return;
      }
      if (error instanceof SafeRequestError && !error.retrySameAttempt) {
        pendingDraftAttempt = null;
      }
      errorPanel.textContent = safeFailureCopy(error, "draft");
      errorPanel.hidden = false;
    } finally {
      inFlight = null;
      if (agreementState && agreementState.draft === null) {
        updateDraftControls();
      }
    }
  });

  confirmationForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    let actionRecorded = false;
    if (
      inFlight !== null ||
      !agreementState ||
      agreementState.draft === null ||
      agreementState.confirmation !== null
    ) {
      return;
    }
    if (!pendingConfirmationAttempt) {
      const fields = validatedConfirmationFields();
      const idempotencyKey = newOperationKey("agreement-confirmation");
      if (
        !fields ||
        !idempotencyKey ||
        !confirmationForm.reportValidity()
      ) {
        confirmationStatus.textContent =
          "Explicit customer identity, rationale, and acknowledgement are required.";
        return;
      }
      pendingConfirmationAttempt = {
        payload: {
          confirmer: fields.confirmer,
          agreement_acknowledged: true,
          rationale: fields.rationale,
          idempotency_key: idempotencyKey,
        },
      };
    }

    inFlight = "confirmation";
    clearError();
    updateConfirmationControls();
    try {
      const response = await requestJson(confirmationApi, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify(pendingConfirmationAttempt.payload),
      });
      if (
        !isTrustedConfirmationActionResponse(
          response,
          pendingConfirmationAttempt
        )
      ) {
        throw new SafeRequestError(200, true);
      }
      pendingConfirmationAttempt = null;
      actionRecorded = true;
      await reconcileAgreement();
      confirmerInput.value = "";
      confirmationRationaleInput.value = "";
      acknowledgementInput.checked = false;
    } catch (error) {
      if (actionRecorded) {
        agreementState = null;
        blockAgreement(
          "The confirmation was recorded, but the authoritative agreement state could not be refreshed. Reload before freezing."
        );
        return;
      }
      if (error instanceof SafeRequestError && !error.retrySameAttempt) {
        pendingConfirmationAttempt = null;
      }
      errorPanel.textContent = safeFailureCopy(error, "confirmation");
      errorPanel.hidden = false;
    } finally {
      inFlight = null;
      if (
        agreementState &&
        agreementState.draft !== null &&
        agreementState.confirmation === null
      ) {
        updateConfirmationControls();
      }
    }
  });

  freezeForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    let actionRecorded = false;
    if (
      inFlight !== null ||
      !agreementState ||
      agreementState.draft === null ||
      agreementState.confirmation === null ||
      agreementState.frozen_contract !== null
    ) {
      return;
    }
    if (!pendingFreezeAttempt) {
      const idempotencyKey = newOperationKey("agreement-freeze");
      if (!idempotencyKey) {
        freezeStatus.textContent =
          "A safe freeze operation key could not be created.";
        return;
      }
      pendingFreezeAttempt = {
        payload: {
          idempotency_key: idempotencyKey,
        },
      };
    }

    inFlight = "freeze";
    clearError();
    updateFreezeControls();
    try {
      const response = await requestJson(freezeApi, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify(pendingFreezeAttempt.payload),
      });
      if (!isTrustedFreezeActionResponse(response)) {
        throw new SafeRequestError(200, true);
      }
      pendingFreezeAttempt = null;
      actionRecorded = true;
      await reconcileAgreement();
    } catch (error) {
      if (actionRecorded) {
        agreementState = null;
        blockAgreement(
          "The contract was frozen, but the authoritative agreement state could not be refreshed. Reload before continuing."
        );
        return;
      }
      if (error instanceof SafeRequestError && !error.retrySameAttempt) {
        pendingFreezeAttempt = null;
      }
      errorPanel.textContent = safeFailureCopy(error, "freeze");
      errorPanel.hidden = false;
    } finally {
      inFlight = null;
      if (
        agreementState &&
        agreementState.confirmation !== null &&
        agreementState.frozen_contract === null
      ) {
        updateFreezeControls();
      }
    }
  });

  async function initialise() {
    if (!pocId || !agreementApi || !confirmationApi || !freezeApi) {
      blockAgreement(
        "This agreement address is invalid. Return to the POC workspace."
      );
      return;
    }
    try {
      const projection = await requestJson(agreementApi);
      if (!isTrustedAgreementProjection(projection)) {
        throw new SafeRequestError(200, true);
      }
      agreementState = projection;
      document.querySelector("#poc-title").textContent =
        "Performance agreement";
      document.querySelector("#poc-context").textContent =
        `${pocId} · ${projection.definitions.length} ${projection.definitions.length === 1 ? "bounded definition" : "bounded definitions"}`;
      renderAgreementState();
    } catch {
      blockAgreement(
        "The agreement state could not be validated. No confirmation or freeze action is available."
      );
    }
  }

  window.addEventListener("pagehide", () => {
    agreementState = null;
    pendingDraftAttempt = null;
    pendingConfirmationAttempt = null;
    pendingFreezeAttempt = null;
    draftControls.forEach((control) => {
      control.value = "";
    });
    confirmerInput.value = "";
    confirmationRationaleInput.value = "";
    acknowledgementInput.checked = false;
    document.querySelector("#definition-list").replaceChildren();
    document.querySelector("#customer-criteria-list").replaceChildren();
    document.querySelector("#review-target-provider").textContent = "";
    document.querySelector("#review-model").textContent = "";
    document.querySelector("#review-endpoint-class").textContent = "";
    document.querySelector("#review-endpoint").textContent = "";
  });

  initialise();
})();
