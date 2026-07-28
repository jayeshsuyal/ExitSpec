(() => {
  "use strict";

  const POC_ID_PATTERN = /^poc_[a-z0-9][a-z0-9_-]{2,63}$/;
  const PROPOSAL_ID_PATTERN = /^prop_[a-z0-9][a-z0-9_-]{7,95}$/;
  const SOURCE_RECEIPT_ID_PATTERN = /^srcpt_[a-z0-9][a-z0-9_-]{7,95}$/;
  const DEFINITION_ID_PATTERN = /^cdef_[a-f0-9]{32}$/;
  const SHA256_PATTERN = /^[a-f0-9]{64}$/;
  const ROUTE_PATTERN =
    /^\/app\/pocs\/(poc_[a-z0-9][a-z0-9_-]{2,63})\/define$/;
  const SOURCE_KINDS = Object.freeze([
    "EMAIL",
    "MEETING",
    "DOCUMENT",
    "EXISTING_CONTRACT",
  ]);
  const SOURCE_LABELS = Object.freeze({
    EMAIL: "Email",
    MEETING: "Meeting transcript",
    DOCUMENT: "Notes or document",
    EXISTING_CONTRACT: "Existing contract",
  });
  const METRICS = Object.freeze([
    "TTFT_P95_MS",
    "ERROR_RATE_PERCENT",
  ]);
  const OPERATORS = Object.freeze(["LT", "LTE"]);
  const METRIC_CONFIG = Object.freeze({
    TTFT_P95_MS: Object.freeze({
      unit: "MILLISECONDS",
      shortUnit: "ms",
      minimum: 0.001,
      maximum: 60000,
      defaultThreshold: 500,
    }),
    ERROR_RATE_PERCENT: Object.freeze({
      unit: "PERCENT",
      shortUnit: "%",
      minimum: 0,
      maximum: 100,
      defaultThreshold: 1,
    }),
  });
  const DISPOSITIONS = Object.freeze([
    "CREATED",
    "IDEMPOTENT_REPLAY",
  ]);
  const DEFINITION_KEYS = Object.freeze([
    "concurrency",
    "defined_at",
    "definition_id",
    "definition_sha256",
    "metric",
    "minimum_samples",
    "operator",
    "output_tokens_max",
    "output_tokens_min",
    "prompt_tokens_max",
    "prompt_tokens_min",
    "threshold",
    "unit",
  ]);
  const PROPOSAL_KEYS = Object.freeze([
    "definition",
    "normalized_claim",
    "proposal_id",
    "review_state",
    "source_kind",
    "source_quote",
    "source_receipt_id",
  ]);
  const routeMatch =
    window.location.search === "" && window.location.hash === ""
      ? window.location.pathname.match(ROUTE_PATTERN)
      : null;
  const pocId =
    routeMatch && POC_ID_PATTERN.test(routeMatch[1]) ? routeMatch[1] : null;
  const pocApi = pocId ? `/api/pocs/${pocId}` : null;
  const definitionsApi = pocApi ? `${pocApi}/definitions` : null;

  const currentTask = document.querySelector("#definition-current-task");
  const form = document.querySelector("#contract-definition-form");
  const metricInput = document.querySelector("#metric");
  const operatorInput = document.querySelector("#operator");
  const thresholdInput = document.querySelector("#threshold");
  const thresholdUnit = document.querySelector("#threshold-unit");
  const minimumSamplesInput = document.querySelector("#minimum-samples");
  const concurrencyInput = document.querySelector("#concurrency");
  const promptTokensMinInput = document.querySelector("#prompt-tokens-min");
  const promptTokensMaxInput = document.querySelector("#prompt-tokens-max");
  const outputTokensMinInput = document.querySelector("#output-tokens-min");
  const outputTokensMaxInput = document.querySelector("#output-tokens-max");
  const reviewerInput = document.querySelector("#reviewer");
  const rationaleInput = document.querySelector("#rationale");
  const saveButton = document.querySelector("#save-definition");
  const definitionStatus = document.querySelector("#definition-status");
  const errorPanel = document.querySelector("#contract-definition-error");
  const completionPanel = document.querySelector("#definition-complete");
  const formControls = Object.freeze([
    metricInput,
    operatorInput,
    thresholdInput,
    minimumSamplesInput,
    concurrencyInput,
    promptTokensMinInput,
    promptTokensMaxInput,
    outputTokensMinInput,
    outputTokensMaxInput,
    reviewerInput,
    rationaleInput,
  ]);

  let proposals = [];
  let inFlight = false;
  let pendingAttempt = null;

  class SafeRequestError extends Error {
    constructor(statusCode, retrySameAttempt) {
      super("Contract-definition request failed.");
      this.name = "SafeRequestError";
      this.statusCode = statusCode;
      this.retrySameAttempt = retrySameAttempt;
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
          (value === pocApi || value === definitionsApi)
      );
    } catch {
      return false;
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

  function isExactInteger(value, minimum, maximum) {
    return (
      typeof value === "number" &&
      Number.isInteger(value) &&
      value >= minimum &&
      value <= maximum
    );
  }

  function isTrustedDraft(payload) {
    return Boolean(
      payload &&
        typeof payload === "object" &&
        !Array.isArray(payload) &&
        payload.poc_id === pocId &&
        isSafeBoundedText(payload.display_name, 160) &&
        isSafeBoundedText(payload.customer_label, 160) &&
        payload.archive_state === "ACTIVE"
    );
  }

  function isTrustedDefinition(definition) {
    if (
      !hasExactKeys(definition, DEFINITION_KEYS) ||
      !DEFINITION_ID_PATTERN.test(definition.definition_id) ||
      !SHA256_PATTERN.test(definition.definition_sha256) ||
      !METRICS.includes(definition.metric) ||
      !OPERATORS.includes(definition.operator)
    ) {
      return false;
    }
    const config = METRIC_CONFIG[definition.metric];
    if (
      definition.unit !== config.unit ||
      typeof definition.threshold !== "number" ||
      !Number.isFinite(definition.threshold) ||
      definition.threshold < config.minimum ||
      definition.threshold > config.maximum ||
      (definition.metric === "ERROR_RATE_PERCENT" &&
        definition.threshold >= config.maximum) ||
      (definition.metric === "ERROR_RATE_PERCENT" &&
        definition.operator !== "LT") ||
      (definition.metric === "ERROR_RATE_PERCENT" &&
        definition.operator === "LT" &&
        definition.threshold <= 0) ||
      !isExactInteger(definition.minimum_samples, 1, 1000) ||
      !isExactInteger(definition.concurrency, 1, 32) ||
      definition.concurrency > definition.minimum_samples ||
      !isExactInteger(definition.prompt_tokens_min, 1, 1000000) ||
      !isExactInteger(definition.prompt_tokens_max, 1, 1000000) ||
      !isExactInteger(definition.output_tokens_min, 1, 1000000) ||
      !isExactInteger(definition.output_tokens_max, 1, 1000000) ||
      definition.prompt_tokens_min > definition.prompt_tokens_max ||
      definition.output_tokens_min > definition.output_tokens_max
    ) {
      return false;
    }
    if (
      typeof definition.defined_at !== "string" ||
      !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$/.test(
        definition.defined_at
      ) ||
      !Number.isFinite(Date.parse(definition.defined_at))
    ) {
      return false;
    }
    return true;
  }

  function isTrustedProposal(proposal) {
    return Boolean(
      hasExactKeys(proposal, PROPOSAL_KEYS) &&
        PROPOSAL_ID_PATTERN.test(proposal.proposal_id) &&
        SOURCE_RECEIPT_ID_PATTERN.test(proposal.source_receipt_id) &&
        SOURCE_KINDS.includes(proposal.source_kind) &&
        isSafeBoundedText(proposal.source_quote, 4000) &&
        isSafeBoundedText(proposal.normalized_claim, 2000) &&
        proposal.review_state === "KEEP_FOR_CONTRACT" &&
        (proposal.definition === null ||
          isTrustedDefinition(proposal.definition))
    );
  }

  function isTrustedDefinitionList(payload) {
    if (
      !hasExactKeys(payload, ["poc_id", "proposals"]) ||
      payload.poc_id !== pocId ||
      !Array.isArray(payload.proposals) ||
      payload.proposals.length > 1024 ||
      !payload.proposals.every(isTrustedProposal)
    ) {
      return false;
    }
    const proposalIds = payload.proposals.map(
      (proposal) => proposal.proposal_id
    );
    const definitionIds = payload.proposals
      .filter((proposal) => proposal.definition !== null)
      .map((proposal) => proposal.definition.definition_id);
    return (
      new Set(proposalIds).size === proposalIds.length &&
      new Set(definitionIds).size === definitionIds.length
    );
  }

  function isTrustedDefinitionResponse(payload, attempt) {
    if (
      !hasExactKeys(payload, [
        "definition",
        "disposition",
        "poc_id",
        "proposal_id",
      ]) ||
      payload.poc_id !== pocId ||
      payload.proposal_id !== attempt.proposalId ||
      !DISPOSITIONS.includes(payload.disposition) ||
      !isTrustedDefinition(payload.definition)
    ) {
      return false;
    }
    const definition = payload.definition;
    const request = attempt.payload;
    return Boolean(
      definition.metric === request.metric &&
        definition.operator === request.operator &&
        definition.threshold === request.threshold &&
        definition.minimum_samples === request.minimum_samples &&
        definition.concurrency === request.concurrency &&
        definition.prompt_tokens_min === request.prompt_tokens_min &&
        definition.prompt_tokens_max === request.prompt_tokens_max &&
        definition.output_tokens_min === request.output_tokens_min &&
        definition.output_tokens_max === request.output_tokens_max
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

  function newIdempotencyKey() {
    if (
      !window.crypto ||
      typeof window.crypto.randomUUID !== "function"
    ) {
      return null;
    }
    return `contract-definition-${window.crypto.randomUUID()}`;
  }

  function currentProposal() {
    return (
      proposals.find((proposal) => proposal.definition === null) || null
    );
  }

  function clearError() {
    errorPanel.hidden = true;
    errorPanel.textContent = "";
  }

  function safeFailureCopy(error) {
    if (!(error instanceof SafeRequestError)) {
      return "The definition could not be recorded safely. Retry the same attempt.";
    }
    if (error.statusCode === 400 || error.statusCode === 422) {
      return "The definition was not accepted. Review every measurement value.";
    }
    if (error.statusCode === 404) {
      return "This POC or proposal is unavailable. No definition was recorded.";
    }
    if (
      error.statusCode === 403 ||
      error.statusCode === 409 ||
      error.statusCode === 415
    ) {
      return "The definition request was refused safely. Reload the POC before continuing.";
    }
    return "The response was interrupted or could not be trusted. Retry uses the same definition key.";
  }

  function numberValue(input) {
    if (input.value.trim() === "") {
      return null;
    }
    const value = Number(input.value);
    return Number.isFinite(value) ? value : null;
  }

  function integerValue(input, minimum, maximum) {
    const value = numberValue(input);
    return isExactInteger(value, minimum, maximum) ? value : null;
  }

  function validatedDefinitionFields() {
    const metric = metricInput.value;
    const operator = operatorInput.value;
    const config = METRIC_CONFIG[metric];
    const threshold = numberValue(thresholdInput);
    const minimumSamples = integerValue(minimumSamplesInput, 1, 1000);
    const concurrency = integerValue(concurrencyInput, 1, 32);
    const promptTokensMin = integerValue(
      promptTokensMinInput,
      1,
      1000000
    );
    const promptTokensMax = integerValue(
      promptTokensMaxInput,
      1,
      1000000
    );
    const outputTokensMin = integerValue(
      outputTokensMinInput,
      1,
      1000000
    );
    const outputTokensMax = integerValue(
      outputTokensMaxInput,
      1,
      1000000
    );
    const reviewer = reviewerInput.value.trim();
    const rationale = rationaleInput.value.trim();
    if (
      !config ||
      !OPERATORS.includes(operator) ||
      threshold === null ||
      threshold < config.minimum ||
      threshold > config.maximum ||
      (metric === "ERROR_RATE_PERCENT" && threshold >= config.maximum) ||
      (metric === "ERROR_RATE_PERCENT" && operator !== "LT") ||
      (metric === "ERROR_RATE_PERCENT" &&
        operator === "LT" &&
        threshold <= 0) ||
      minimumSamples === null ||
      concurrency === null ||
      concurrency > minimumSamples ||
      promptTokensMin === null ||
      promptTokensMax === null ||
      outputTokensMin === null ||
      outputTokensMax === null ||
      promptTokensMin > promptTokensMax ||
      outputTokensMin > outputTokensMax ||
      !isSafeBoundedText(reviewer, 160) ||
      reviewer.includes("\n") ||
      !isSafeBoundedText(rationale, 2000)
    ) {
      return null;
    }
    return {
      metric,
      operator,
      threshold,
      minimum_samples: minimumSamples,
      concurrency,
      prompt_tokens_min: promptTokensMin,
      prompt_tokens_max: promptTokensMax,
      output_tokens_min: outputTokensMin,
      output_tokens_max: outputTokensMax,
      reviewer,
      rationale,
    };
  }

  function setFieldAvailability(enabled) {
    formControls.forEach((control) => {
      control.disabled = !enabled;
    });
  }

  function updateMetricBoundary(resetThreshold) {
    const config = METRIC_CONFIG[metricInput.value];
    if (!config) {
      return;
    }
    thresholdInput.min = String(config.minimum);
    thresholdInput.max = String(config.maximum);
    thresholdUnit.textContent = config.shortUnit;
    if (resetThreshold) {
      thresholdInput.value = String(config.defaultThreshold);
    }
    const inclusiveOption = operatorInput.querySelector(
      'option[value="LTE"]'
    );
    if (inclusiveOption) {
      const errorRateSelected =
        metricInput.value === "ERROR_RATE_PERCENT";
      inclusiveOption.disabled = errorRateSelected;
      if (errorRateSelected && operatorInput.value === "LTE") {
        operatorInput.value = "LT";
      }
    }
  }

  function updateDefinitionControls() {
    const hasProposal = currentProposal() !== null;
    const fieldsValid = validatedDefinitionFields() !== null;
    const editable = hasProposal && !inFlight && !pendingAttempt;

    setFieldAvailability(editable);
    saveButton.disabled =
      !hasProposal || inFlight || (!pendingAttempt && !fieldsValid);
    saveButton.textContent = pendingAttempt
      ? "Retry save definition"
      : "Save definition";
    definitionStatus.textContent = inFlight
      ? "Saving this bounded definition…"
      : pendingAttempt
        ? "The response was interrupted. Retry will use the same definition key."
        : fieldsValid
          ? "Verify the source and save this definition."
          : "Complete every required value to unlock Save definition.";
  }

  function renderProgress() {
    const definedCount = proposals.filter(
      (proposal) => proposal.definition !== null
    ).length;
    const totalCount = proposals.length;
    const progressBar = document.querySelector("#progress-bar");
    const progressFill = document.querySelector("#progress-fill");
    const progressCopy = document.querySelector("#progress-copy");

    progressBar.setAttribute("aria-valuemax", String(totalCount || 1));
    progressBar.setAttribute("aria-valuenow", String(definedCount));
    progressCopy.textContent =
      totalCount === 0
        ? "No kept proposals"
        : `${definedCount} of ${totalCount} defined`;
    progressFill.style.width =
      totalCount === 0
        ? "100%"
        : `${Math.round((definedCount / totalCount) * 100)}%`;
  }

  function resetFormForProposal() {
    metricInput.value = "TTFT_P95_MS";
    operatorInput.value = "LT";
    updateMetricBoundary(true);
    minimumSamplesInput.value = "100";
    concurrencyInput.value = "4";
    promptTokensMinInput.value = "512";
    promptTokensMaxInput.value = "4096";
    outputTokensMinInput.value = "64";
    outputTokensMaxInput.value = "512";
    reviewerInput.value = "";
    rationaleInput.value = "";
  }

  function renderCurrentProposal() {
    const proposal = currentProposal();
    if (!proposal) {
      renderCompletion();
      return;
    }

    const definedCount = proposals.filter(
      (item) => item.definition !== null
    ).length;
    document.querySelector("#proposal-heading").textContent =
      `Criterion ${definedCount + 1}`;
    document.querySelector("#source-kind").textContent =
      SOURCE_LABELS[proposal.source_kind];
    document.querySelector("#source-quote").textContent =
      proposal.source_quote;
    document.querySelector("#normalized-claim").textContent =
      proposal.normalized_claim;
    pendingAttempt = null;
    resetFormForProposal();
    clearError();
    renderProgress();
    updateDefinitionControls();
    document.querySelector("#definition-evidence").focus?.();
  }

  function renderCompletion() {
    const totalCount = proposals.length;
    const definedCount = proposals.filter(
      (proposal) => proposal.definition !== null
    ).length;
    document.querySelector("#source-quote").textContent = "";
    document.querySelector("#normalized-claim").textContent = "";
    reviewerInput.value = "";
    rationaleInput.value = "";
    pendingAttempt = null;
    currentTask.hidden = true;
    completionPanel.hidden = false;
    document.querySelector("#definition-complete-summary").textContent =
      totalCount === 0
        ? "There are no kept proposals available for criterion definition."
        : `${definedCount} of ${totalCount} kept proposals have bounded definitions ready for later agreement drafting.`;
    const progressBar = document.querySelector("#progress-bar");
    progressBar.setAttribute("aria-valuenow", String(totalCount));
    document.querySelector("#progress-fill").style.width = "100%";
    completionPanel.focus();
  }

  function applyLoadedData(draft, definitionList) {
    proposals = definitionList.proposals.slice();
    document.querySelector("#poc-title").textContent = draft.display_name;
    document.querySelector("#poc-context").textContent =
      `${draft.customer_label} · ${proposals.length} ${proposals.length === 1 ? "kept proposal" : "kept proposals"}`;
    currentTask.setAttribute("aria-busy", "false");
    renderCurrentProposal();
  }

  async function reconcileDefinitionsAfterSave() {
    const definitionList = await requestJson(definitionsApi);
    if (!isTrustedDefinitionList(definitionList)) {
      throw new SafeRequestError(200, true);
    }
    proposals = definitionList.proposals.slice();
    renderCurrentProposal();
  }

  function blockDefinition(message) {
    currentTask.setAttribute("aria-busy", "false");
    setFieldAvailability(false);
    saveButton.disabled = true;
    definitionStatus.textContent = "Criterion definition is unavailable.";
    errorPanel.textContent = message;
    errorPanel.hidden = false;
  }

  formControls.forEach((control) => {
    control.addEventListener("input", () => {
      if (!inFlight && !pendingAttempt) {
        clearError();
        updateDefinitionControls();
      }
    });
  });

  metricInput.addEventListener("change", () => {
    if (!inFlight && !pendingAttempt) {
      updateMetricBoundary(true);
      clearError();
      updateDefinitionControls();
    }
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const proposal = currentProposal();
    let definitionRecorded = false;
    if (inFlight || !proposal) {
      return;
    }

    if (!pendingAttempt) {
      const fields = validatedDefinitionFields();
      const idempotencyKey = newIdempotencyKey();
      if (!fields || !idempotencyKey || !form.reportValidity()) {
        definitionStatus.textContent =
          "Review every required value before saving this definition.";
        return;
      }
      pendingAttempt = {
        proposalId: proposal.proposal_id,
        payload: {
          proposal_id: proposal.proposal_id,
          metric: fields.metric,
          operator: fields.operator,
          threshold: fields.threshold,
          minimum_samples: fields.minimum_samples,
          concurrency: fields.concurrency,
          prompt_tokens_min: fields.prompt_tokens_min,
          prompt_tokens_max: fields.prompt_tokens_max,
          output_tokens_min: fields.output_tokens_min,
          output_tokens_max: fields.output_tokens_max,
          reviewer: fields.reviewer,
          rationale: fields.rationale,
          idempotency_key: idempotencyKey,
        },
      };
    }

    inFlight = true;
    clearError();
    updateDefinitionControls();

    try {
      const response = await requestJson(definitionsApi, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify(pendingAttempt.payload),
      });
      if (!isTrustedDefinitionResponse(response, pendingAttempt)) {
        throw new SafeRequestError(200, true);
      }
      pendingAttempt = null;
      definitionRecorded = true;
      await reconcileDefinitionsAfterSave();
    } catch (error) {
      if (definitionRecorded) {
        proposals = [];
        pendingAttempt = null;
        blockDefinition(
          "The definition was recorded, but the current queue could not be refreshed. Reload before continuing."
        );
        return;
      }
      if (
        error instanceof SafeRequestError &&
        !error.retrySameAttempt
      ) {
        pendingAttempt = null;
      }
      errorPanel.textContent = safeFailureCopy(error);
      errorPanel.hidden = false;
    } finally {
      inFlight = false;
      if (!completionPanel.hidden) {
        return;
      }
      updateDefinitionControls();
    }
  });

  async function initialise() {
    if (!pocId || !pocApi || !definitionsApi) {
      blockDefinition(
        "This criterion-definition address is invalid. Return to the POC workspace."
      );
      return;
    }
    try {
      const [draft, definitionList] = await Promise.all([
        requestJson(pocApi),
        requestJson(definitionsApi),
      ]);
      if (
        !isTrustedDraft(draft) ||
        !isTrustedDefinitionList(definitionList)
      ) {
        throw new SafeRequestError(200, true);
      }
      applyLoadedData(draft, definitionList);
    } catch {
      blockDefinition(
        "The draft or kept-proposal queue could not be validated. No definition action is available."
      );
    }
  }

  window.addEventListener("pagehide", () => {
    proposals = [];
    pendingAttempt = null;
    reviewerInput.value = "";
    rationaleInput.value = "";
    document.querySelector("#source-quote").textContent = "";
    document.querySelector("#normalized-claim").textContent = "";
  });

  initialise();
})();
