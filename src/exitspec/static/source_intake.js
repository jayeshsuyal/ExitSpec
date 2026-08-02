(() => {
  "use strict";

  const POC_ID_PATTERN = /^poc_[a-z0-9][a-z0-9_-]{2,63}$/;
  const ROUTE_PATTERN =
    /^\/app\/pocs\/(poc_[a-z0-9][a-z0-9_-]{2,63})\/sources\/new$/;
  const RECEIPT_ID_PATTERN = /^srcpt_[a-z0-9][a-z0-9_-]{7,95}$/;
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
  const SOURCE_ENTRY_IDS = Object.freeze({
    EMAIL: "email-entry",
    MEETING: "meeting-entry",
    DOCUMENT: "document-entry",
    EXISTING_CONTRACT: "contract-entry",
  });
  const SOURCE_INPUT_IDS = Object.freeze({
    EMAIL: "email-text",
    MEETING: "meeting-transcript",
    DOCUMENT: "document-text",
    EXISTING_CONTRACT: "contract-json",
  });
  const routeMatch = window.location.pathname.match(ROUTE_PATTERN);
  const pocId =
    routeMatch && POC_ID_PATTERN.test(routeMatch[1]) ? routeMatch[1] : null;
  const pocApi = pocId ? `/api/pocs/${pocId}` : null;
  const sourcesApi = pocApi ? `${pocApi}/sources` : null;

  const form = document.querySelector("#source-intake-form");
  const chooser = document.querySelector("#source-chooser");
  const currentTask = document.querySelector("#source-current-task");
  const captureButton = document.querySelector("#capture-source");
  const status = document.querySelector("#source-intake-status");
  const errorPanel = document.querySelector("#intake-error");
  const resultPanel = document.querySelector("#capture-result");
  const sourceEmpty = document.querySelector("#source-empty");
  const sourceRadios = Array.from(
    document.querySelectorAll('input[name="source_kind"]')
  );
  const sourceEntries = Object.fromEntries(
    SOURCE_KINDS.map((sourceKind) => [
      sourceKind,
      document.querySelector(`#${SOURCE_ENTRY_IDS[sourceKind]}`),
    ])
  );
  const sourceInputs = Object.fromEntries(
    SOURCE_KINDS.map((sourceKind) => [
      sourceKind,
      document.querySelector(`#${SOURCE_INPUT_IDS[sourceKind]}`),
    ])
  );

  let selectedSource = null;
  let preferredSource = null;
  let inFlight = false;
  let pendingAttempt = null;

  class SafeRequestError extends Error {
    constructor(statusCode, retrySameAttempt) {
      super("Source request failed.");
      this.name = "SafeRequestError";
      this.statusCode = statusCode;
      this.retrySameAttempt = retrySameAttempt;
    }
  }

  function endpointFor(sourceKind) {
    if (!sourcesApi) {
      return null;
    }
    switch (sourceKind) {
      case "EMAIL":
        return `${sourcesApi}/email-text`;
      case "MEETING":
        return `${sourcesApi}/meeting`;
      case "DOCUMENT":
        return `${sourcesApi}/document`;
      case "EXISTING_CONTRACT":
        return `${sourcesApi}/contract`;
      default:
        return null;
    }
  }

  function isTrustedApiPath(value) {
    if (
      !pocId ||
      typeof value !== "string" ||
      value.includes("?") ||
      value.includes("#")
    ) {
      return false;
    }
    try {
      const parsed = new URL(value, window.location.origin);
      return (
        parsed.origin === window.location.origin &&
        parsed.pathname === value &&
        parsed.search === "" &&
        parsed.hash === "" &&
        (value === pocApi ||
          value === sourcesApi ||
          SOURCE_KINDS.some((sourceKind) => value === endpointFor(sourceKind)))
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

  function isTrustedDraft(payload) {
    return Boolean(
      payload &&
        typeof payload === "object" &&
        !Array.isArray(payload) &&
        payload.poc_id === pocId &&
        isSafeBoundedText(payload.display_name, 160) &&
        isSafeBoundedText(payload.customer_label, 160) &&
        SOURCE_KINDS.includes(payload.first_source_choice) &&
        payload.archive_state === "ACTIVE"
    );
  }

  function isTrustedSourceList(payload) {
    return Boolean(
      hasExactKeys(payload, ["poc_id", "sources"]) &&
        payload.poc_id === pocId &&
        Array.isArray(payload.sources) &&
        payload.sources.length <= 128 &&
        payload.sources.every(
          (source) =>
            hasExactKeys(source, [
              "idempotent_replay",
              "poc_id",
              "proposal_count",
              "source_kind",
              "source_receipt_id",
              "status",
            ]) &&
            source.poc_id === pocId &&
            SOURCE_KINDS.includes(source.source_kind) &&
            RECEIPT_ID_PATTERN.test(source.source_receipt_id) &&
            Number.isSafeInteger(source.proposal_count) &&
            source.proposal_count >= 0 &&
            source.proposal_count <= 64 &&
            source.status === "NEEDS_REVIEW" &&
            source.idempotent_replay === false
        )
    );
  }

  function isTrustedCaptureResponse(payload, sourceKind) {
    return Boolean(
      hasExactKeys(payload, [
        "idempotent_replay",
        "poc_id",
        "proposal_count",
        "source_kind",
        "source_receipt_id",
        "status",
      ]) &&
        payload.poc_id === pocId &&
        payload.source_kind === sourceKind &&
        RECEIPT_ID_PATTERN.test(payload.source_receipt_id) &&
        Number.isSafeInteger(payload.proposal_count) &&
        payload.proposal_count >= 0 &&
        payload.proposal_count <= 64 &&
        payload.status === "NEEDS_REVIEW" &&
        typeof payload.idempotent_replay === "boolean"
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
        credentials: "same-origin",
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
      responseUrl.search ||
      responseUrl.hash
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
    const payload = await response.json().catch(() => null);
    if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
      throw new SafeRequestError(response.status, true);
    }
    return payload;
  }

  function newIdempotencyKey() {
    if (
      window.crypto &&
      typeof window.crypto.randomUUID === "function"
    ) {
      return `source-${window.crypto.randomUUID()}`;
    }
    const bytes = new Uint8Array(16);
    window.crypto.getRandomValues(bytes);
    const suffix = Array.from(bytes, (value) =>
      value.toString(16).padStart(2, "0")
    ).join("");
    return `source-${suffix}`;
  }

  function selectedValue(sourceKind) {
    const input = sourceInputs[sourceKind];
    if (!input || input.disabled) {
      return null;
    }
    if (sourceKind === "EXISTING_CONTRACT") {
      const rawValue = input.value;
      if (!isSafeBoundedText(rawValue, 40000)) {
        return null;
      }
      try {
        const parsed = JSON.parse(rawValue);
        if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
          return null;
        }
      } catch {
        return null;
      }
      return rawValue;
    }
    const maximum = 20000;
    return isSafeBoundedText(input.value, maximum) ? input.value : null;
  }

  function buildSourcePayload(sourceKind, value, idempotencyKey) {
    switch (sourceKind) {
      case "EMAIL":
        return { email_text: value, idempotency_key: idempotencyKey };
      case "MEETING":
        return { transcript_text: value, idempotency_key: idempotencyKey };
      case "DOCUMENT":
        return { document_text: value, idempotency_key: idempotencyKey };
      case "EXISTING_CONTRACT":
        return { contract_json: value, idempotency_key: idempotencyKey };
      default:
        return null;
    }
  }

  function clearError() {
    errorPanel.hidden = true;
    errorPanel.textContent = "";
  }

  function safeFailureCopy(error) {
    if (!(error instanceof SafeRequestError)) {
      return "The source could not be captured safely. Retry the same attempt.";
    }
    if (error.statusCode === 400) {
      if (selectedSource === "MEETING") {
        return "The transcript was not accepted. Use Speaker: message lines, or paste one natural single-speaker text block.";
      }
      return "The source was not accepted. Review the selected input and try again.";
    }
    if (error.statusCode === 404) {
      return "This draft or source route is unavailable. No source was captured.";
    }
    if (
      error.statusCode === 403 ||
      error.statusCode === 409 ||
      error.statusCode === 415
    ) {
      return "The source request was refused safely. Review the draft and try again.";
    }
    if (error.statusCode === 413 || error.statusCode === 422) {
      if (selectedSource === "MEETING") {
        return "The transcript was not accepted. Check the 20,000-character limit and use Speaker: message lines or one natural text block.";
      }
      return "The selected source was not accepted. Review it and start a new capture attempt.";
    }
    return "The response was interrupted or could not be trusted. Retry uses the same source key.";
  }

  function setControlsDisabled(disabled) {
    chooser.disabled = disabled;
    sourceRadios.forEach((radio) => {
      radio.disabled = disabled;
    });
    Object.values(sourceInputs).forEach((input) => {
      input.disabled =
        disabled ||
        !selectedSource ||
        input.id !== SOURCE_INPUT_IDS[selectedSource];
    });
  }

  function renderSelectedSource() {
    Object.entries(sourceEntries).forEach(([sourceKind, entry]) => {
      const active = sourceKind === selectedSource;
      entry.hidden = !active;
      entry.setAttribute("aria-hidden", String(!active));
    });
    sourceEmpty.hidden = Boolean(selectedSource);
    document.querySelector("#source-work-heading").textContent = selectedSource
      ? SOURCE_LABELS[selectedSource]
      : "Choose a source";
    document.querySelector("#preferred-source-copy").textContent =
      selectedSource && selectedSource === preferredSource
        ? "Original starting choice"
        : selectedSource
          ? "Alternate source"
          : "No source selected";
    setControlsDisabled(inFlight || Boolean(pendingAttempt));
    captureButton.disabled =
      !selectedSource ||
      inFlight ||
      (!pendingAttempt && selectedValue(selectedSource) === null);
    captureButton.textContent = pendingAttempt
      ? "Retry capture"
      : inFlight
        ? "Capturing…"
        : "Capture source";
    status.textContent = pendingAttempt
      ? "The response was interrupted. Retry will use the same source key."
      : inFlight
        ? "Capturing the source for human review…"
        : selectedSource
          ? "Check the source, then capture it."
          : "Choose one source to continue.";
  }

  function clearSensitiveInputs() {
    document.querySelector("#email-text").value = "";
    document.querySelector("#meeting-transcript").value = "";
    document.querySelector("#document-text").value = "";
    document.querySelector("#contract-json").value = "";
  }

  function renderSuccess(payload) {
    const destination = `/app/pocs/${encodeURIComponent(pocId)}/review`;
    clearSensitiveInputs();
    pendingAttempt = null;
    form.hidden = true;
    currentTask.hidden = false;
    resultPanel.hidden = false;
    document.querySelector("#current-task-heading").textContent =
      "Review the extracted proposals";
    document.querySelector("#task-guidance").textContent =
      "The source is captured. Human review decides which claims may become acceptance criteria.";
    document.querySelector("#proposal-count").textContent =
      `${payload.proposal_count} ${payload.proposal_count === 1 ? "proposal" : "proposals"}`;
    document.querySelector("#review-state").textContent = "NEEDS_REVIEW";
    const reviewProposals = document.querySelector("#review-proposals");
    reviewProposals.setAttribute("href", destination);
    reviewProposals.hidden = false;
    const addAnotherSource = document.querySelector("#add-another-source");
    addAnotherSource.setAttribute(
      "href",
      `/app/pocs/${pocId}/sources/new`
    );
    addAnotherSource.hidden = false;
    resultPanel.focus();
    try {
      window.location.replace(destination);
    } catch {
      // The verified fallback panel remains available if navigation is blocked.
    }
  }

  function applyDraft(draft, sourceList) {
    preferredSource = draft.first_source_choice;
    selectedSource = preferredSource;
    document.querySelector("#poc-title").textContent = draft.display_name;
    document.querySelector("#poc-context").textContent =
      `${draft.customer_label} · local draft`;
    document.querySelector("#existing-source-count").textContent =
      `${sourceList.sources.length} existing ${sourceList.sources.length === 1 ? "source" : "sources"}`;

    sourceRadios.forEach((radio) => {
      radio.checked = radio.value === preferredSource;
      const option = radio.closest("[data-source-option]");
      const marker = option.querySelector(".starting-choice");
      option.dataset.preferred = String(radio.value === preferredSource);
      marker.hidden = radio.value !== preferredSource;
    });

    currentTask.setAttribute("aria-busy", "false");
    renderSelectedSource();
  }

  function blockIntake(message) {
    setControlsDisabled(true);
    captureButton.disabled = true;
    currentTask.setAttribute("aria-busy", "false");
    status.textContent = "Source intake is unavailable.";
    errorPanel.textContent = message;
    errorPanel.hidden = false;
  }

  sourceRadios.forEach((radio) => {
    radio.addEventListener("change", () => {
      if (!radio.checked || inFlight || pendingAttempt) {
        return;
      }
      selectedSource = radio.value;
      clearError();
      renderSelectedSource();
      sourceInputs[selectedSource].focus();
    });
  });

  Object.values(sourceInputs).forEach((input) => {
    input.addEventListener("input", () => {
      if (!inFlight && !pendingAttempt) {
        clearError();
        renderSelectedSource();
      }
    });
    input.addEventListener("change", () => {
      if (!inFlight && !pendingAttempt) {
        clearError();
        renderSelectedSource();
      }
    });
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (inFlight || !selectedSource) {
      return;
    }

    if (!pendingAttempt) {
      const value = selectedValue(selectedSource);
      if (value === null || !form.reportValidity()) {
        status.textContent =
          selectedSource === "EXISTING_CONTRACT"
            ? "Enter one valid JSON object before capture."
            : "Complete the selected source before capture.";
        return;
      }
      const idempotencyKey = newIdempotencyKey();
      const payload = buildSourcePayload(
        selectedSource,
        value,
        idempotencyKey
      );
      const endpoint = endpointFor(selectedSource);
      if (!payload || !isTrustedApiPath(endpoint)) {
        blockIntake("The source route is invalid. No source was captured.");
        return;
      }
      pendingAttempt = {
        endpoint,
        idempotencyKey,
        payload,
        sourceKind: selectedSource,
      };
    }

    inFlight = true;
    clearError();
    renderSelectedSource();

    try {
      const response = await requestJson(pendingAttempt.endpoint, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify(pendingAttempt.payload),
      });
      if (!isTrustedCaptureResponse(response, pendingAttempt.sourceKind)) {
        throw new SafeRequestError(200, true);
      }
      renderSuccess(response);
    } catch (error) {
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
      if (!form.hidden) {
        renderSelectedSource();
      }
    }
  });

  async function initialise() {
    if (!pocId || !pocApi || !sourcesApi) {
      blockIntake(
        "This source-intake address is invalid. Return to the POC workspace."
      );
      return;
    }
    try {
      const [draft, sourceList] = await Promise.all([
        requestJson(pocApi),
        requestJson(sourcesApi),
      ]);
      if (!isTrustedDraft(draft) || !isTrustedSourceList(sourceList)) {
        throw new SafeRequestError(200, true);
      }
      applyDraft(draft, sourceList);
    } catch {
      blockIntake(
        "The draft could not be validated. No source request is available."
      );
    }
  }

  initialise();
})();
