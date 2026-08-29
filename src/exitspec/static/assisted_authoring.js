(() => {
  "use strict";

  const POC_ID_PATTERN = /^poc_[a-z0-9][a-z0-9_-]{2,63}$/;
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
  const ROUTE_PATTERN =
    /^\/app\/pocs\/(poc_[a-z0-9][a-z0-9_-]{2,63})\/assisted-authoring$/;
  const routeMatch =
    window.location.search === "" && window.location.hash === ""
      ? window.location.pathname.match(ROUTE_PATTERN)
      : null;
  const pocId =
    routeMatch && POC_ID_PATTERN.test(routeMatch[1]) ? routeMatch[1] : null;
  const pocApi = pocId ? `/api/pocs/${encodeURIComponent(pocId)}` : null;
  const sourcesApi = pocApi ? `${pocApi}/sources` : null;
  const authoringApi = (receiptId) =>
    sourcesApi && RECEIPT_ID_PATTERN.test(receiptId)
      ? `${sourcesApi}/${receiptId}/assisted-authoring`
      : null;
  const reviewPage = pocId
    ? `/app/pocs/${encodeURIComponent(pocId)}/review`
    : null;

  const task = document.querySelector("#assisted-current-task");
  const list = document.querySelector("#source-receipt-list");
  const form = document.querySelector("#assisted-authoring-form");
  const submit = document.querySelector("#authoring-submit");
  const selection = document.querySelector("#assisted-selection");
  const status = document.querySelector("#authoring-status");
  const errorPanel = document.querySelector("#assisted-authoring-error");
  const resultPanel = document.querySelector("#authoring-result");
  const reviewLink = document.querySelector("#open-proposal-review");

  let sources = [];
  let selectedReceipt = null;
  let pendingAttempt = null;
  let inFlight = false;

  class SafeRequestError extends Error {
    constructor(statusCode, retrySameAttempt) {
      super("Assisted authoring request failed.");
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
    return actual.length === expected.length &&
      actual.every((key, index) => key === expected[index]);
  }

  function isSafeText(value, maximum) {
    return typeof value === "string" && value.trim().length > 0 &&
      value.length <= maximum &&
      !/[\u0000-\u0008\u000b\u000c\u000e-\u001f]/.test(value);
  }

  function isTrustedDraft(payload) {
    return Boolean(
      payload && typeof payload === "object" && !Array.isArray(payload) &&
      payload.poc_id === pocId &&
      isSafeText(payload.display_name, 160) &&
      isSafeText(payload.customer_label, 160) &&
      payload.archive_state === "ACTIVE"
    );
  }

  function isTrustedSource(source) {
    return Boolean(
      hasExactKeys(source, [
        "idempotent_replay",
        "poc_id",
        "proposal_count",
        "source_kind",
        "source_receipt_id",
        "status",
      ]) && source.poc_id === pocId &&
      SOURCE_KINDS.includes(source.source_kind) &&
      RECEIPT_ID_PATTERN.test(source.source_receipt_id) &&
      Number.isSafeInteger(source.proposal_count) &&
      source.proposal_count >= 0 && source.proposal_count <= 64 &&
      source.status === "NEEDS_REVIEW" &&
      typeof source.idempotent_replay === "boolean"
    );
  }

  function isTrustedSourceList(payload) {
    return Boolean(
      hasExactKeys(payload, ["poc_id", "sources"]) &&
      payload.poc_id === pocId && Array.isArray(payload.sources) &&
      payload.sources.length <= 128 && payload.sources.every(isTrustedSource) &&
      new Set(payload.sources.map((source) => source.source_receipt_id)).size ===
        payload.sources.length
    );
  }

  function isTrustedReceipt(payload) {
    return Boolean(
      hasExactKeys(payload, [
        "authoring_adapter_name",
        "authoring_adapter_version",
        "authoring_receipt_id",
        "authoring_result_id",
        "endpoint",
        "idempotent_replay",
        "model",
        "poc_id",
        "proposal_count",
        "proposal_ids",
        "provider",
        "redaction_policy_version",
        "schema_version",
        "source_adapter_name",
        "source_adapter_version",
        "source_content_sha256",
        "source_id",
        "source_kind",
        "source_receipt_id",
        "source_revision",
        "status",
      ]) &&
      payload.schema_version === "exitspec.assisted-authoring-receipt.v1" &&
      RECEIPT_ID_PATTERN.test(payload.source_receipt_id) &&
      payload.poc_id === pocId &&
      /^src_[a-z0-9][a-z0-9_-]{2,63}$/.test(payload.source_id) &&
      /^arcp_[a-f0-9]{32}$/.test(payload.authoring_receipt_id) &&
      /^ares_[a-f0-9]{32}$/.test(payload.authoring_result_id) &&
      SOURCE_KINDS.includes(payload.source_kind) &&
      /^[a-f0-9]{64}$/.test(payload.source_content_sha256) &&
      Number.isSafeInteger(payload.source_revision) && payload.source_revision >= 1 &&
      isSafeText(payload.source_adapter_name, 64) &&
      isSafeText(payload.source_adapter_version, 64) &&
      isSafeText(payload.redaction_policy_version, 64) &&
      isSafeText(payload.authoring_adapter_name, 64) &&
      isSafeText(payload.authoring_adapter_version, 64) &&
      isSafeText(payload.provider, 64) &&
      isSafeText(payload.model, 160) &&
      isSafeText(payload.endpoint, 300) &&
      Number.isSafeInteger(payload.proposal_count) && payload.proposal_count > 0 &&
      Array.isArray(payload.proposal_ids) &&
      payload.proposal_ids.length === payload.proposal_count &&
      payload.proposal_ids.every((proposalId) =>
        /^prop_[a-z0-9][a-z0-9_-]{7,95}$/.test(proposalId)
      ) &&
      new Set(payload.proposal_ids).size === payload.proposal_ids.length &&
      payload.status === "NEEDS_REVIEW" && typeof payload.idempotent_replay === "boolean"
    );
  }

  function isTrustedNumericFacts(value) {
    if (value === null) return true;
    return hasExactKeys(value, ["minimum_samples", "threshold"]) &&
      (value.threshold === null ||
        (typeof value.threshold === "number" && Number.isFinite(value.threshold) &&
          value.threshold >= 0 && value.threshold <= 1)) &&
      (value.minimum_samples === null ||
        (Number.isSafeInteger(value.minimum_samples) && value.minimum_samples > 0)) &&
      (value.threshold !== null || value.minimum_samples !== null);
  }

  function isTrustedProposal(payload, receipt) {
    return Boolean(
      hasExactKeys(payload, [
        "authoring_receipt_id",
        "authoring_result_id",
        "normalized_claim",
        "numeric_facts",
        "poc_id",
        "proposal_id",
        "proposal_key",
        "redaction_policy_version",
        "review_state",
        "schema_version",
        "source_adapter_name",
        "source_adapter_version",
        "source_content_sha256",
        "source_id",
        "source_kind",
        "source_quote",
        "source_receipt_id",
        "source_revision",
      ]) &&
      payload.schema_version === "exitspec.assisted-proposal.v1" &&
      payload.poc_id === pocId &&
      /^prop_[a-z0-9][a-z0-9_-]{7,95}$/.test(payload.proposal_id) &&
      payload.authoring_receipt_id === receipt.authoring_receipt_id &&
      payload.authoring_result_id === receipt.authoring_result_id &&
      payload.source_receipt_id === receipt.source_receipt_id &&
      /^src_[a-z0-9][a-z0-9_-]{2,63}$/.test(payload.source_id) &&
      SOURCE_KINDS.includes(payload.source_kind) &&
      /^[a-f0-9]{64}$/.test(payload.source_content_sha256) &&
      payload.source_content_sha256 === receipt.source_content_sha256 &&
      Number.isSafeInteger(payload.source_revision) &&
      payload.source_revision === receipt.source_revision &&
      isSafeText(payload.source_adapter_name, 64) &&
      isSafeText(payload.source_adapter_version, 64) &&
      isSafeText(payload.redaction_policy_version, 64) &&
      isSafeText(payload.proposal_key, 64) &&
      isSafeText(payload.source_quote, 4000) &&
      isSafeText(payload.normalized_claim, 2000) &&
      isTrustedNumericFacts(payload.numeric_facts) &&
      payload.review_state === "NEEDS_REVIEW"
    );
  }

  function isTrustedAuthoringResponse(payload) {
    if (!hasExactKeys(payload, ["authoring_receipt", "proposals"]) ||
        !isTrustedReceipt(payload.authoring_receipt) ||
        !Array.isArray(payload.proposals) ||
        payload.proposals.length !== payload.authoring_receipt.proposal_count ||
        !payload.proposals.every((proposal) =>
          isTrustedProposal(proposal, payload.authoring_receipt)
        )) {
      return false;
    }
    const ids = payload.proposals.map((proposal) => proposal.proposal_id);
    return new Set(ids).size === ids.length;
  }

  function isTrustedApiPath(value) {
    if (!pocId || typeof value !== "string" || value.includes("?") || value.includes("#")) {
      return false;
    }
    try {
      const parsed = new URL(value, window.location.origin);
      return parsed.origin === window.location.origin && parsed.pathname === value &&
        parsed.search === "" && parsed.hash === "" &&
        (value === pocApi || value === sourcesApi || value === authoringApi(selectedReceipt));
    } catch {
      return false;
    }
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
        headers: { Accept: "application/json", ...(options.headers || {}) },
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
    if (responseUrl.origin !== window.location.origin ||
        responseUrl.pathname !== path || responseUrl.search || responseUrl.hash) {
      throw new SafeRequestError(response.status, true);
    }
    if (!response.ok) {
      throw new SafeRequestError(
        response.status,
        response.status >= 500 || response.status === 408 || response.status === 429
      );
    }
    const payload = await response.json().catch(() => null);
    if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
      throw new SafeRequestError(response.status, true);
    }
    return payload;
  }

  function newIdempotencyKey() {
    if (window.crypto && typeof window.crypto.randomUUID === "function") {
      return `assisted-authoring-${window.crypto.randomUUID()}`;
    }
    const bytes = new Uint8Array(16);
    window.crypto.getRandomValues(bytes);
    return `assisted-authoring-${Array.from(bytes, (value) =>
      value.toString(16).padStart(2, "0")).join("")}`;
  }

  function clearError() {
    errorPanel.hidden = true;
    errorPanel.textContent = "";
  }

  function renderSources() {
    list.textContent = "";
    if (!sources.length) {
      const empty = document.createElement("p");
      empty.className = "assisted-placeholder";
      empty.textContent = "Capture an A2 source before assisted authoring.";
      list.appendChild(empty);
      return;
    }
    sources.forEach((source) => {
      const label = document.createElement("label");
      label.className = "source-receipt-option";
      const radio = document.createElement("input");
      radio.type = "radio";
      radio.name = "source_receipt";
      radio.value = source.source_receipt_id;
      radio.addEventListener("change", () => {
        if (!radio.checked || inFlight || pendingAttempt) return;
        selectedReceipt = radio.value;
        selection.textContent = `${SOURCE_LABELS[source.source_kind]} · ${selectedReceipt}`;
        submit.disabled = false;
        status.textContent = "One source is selected. Run the explicit action when ready.";
      });
      const copy = document.createElement("span");
      const title = document.createElement("strong");
      title.textContent = SOURCE_LABELS[source.source_kind];
      const details = document.createElement("small");
      details.textContent = `${source.source_receipt_id} · ${source.proposal_count} A2 proposal inputs`;
      copy.append(title, details);
      label.append(radio, copy);
      list.appendChild(label);
    });
  }

  function safeFailureCopy(error) {
    if (!(error instanceof SafeRequestError)) {
      return "The assisted action could not be trusted. Retry the same attempt.";
    }
    if (error.statusCode === 404) return "The selected source is unavailable. No proposal was created.";
    if (error.statusCode === 409) return "This source already has a different authoring state. Reload before trying again.";
    if (error.statusCode === 400 || error.statusCode === 422) return "The assisted output was refused safely. No proposal was created.";
    return "The response was interrupted or could not be trusted. Retry uses the same authoring key.";
  }

  function renderResult(payload) {
    const receipt = payload.authoring_receipt;
    resultPanel.hidden = false;
    document.querySelector("#authoring-result-summary").textContent =
      `${receipt.proposal_count} source-bound proposal${receipt.proposal_count === 1 ? "" : "s"} emitted for human review (${receipt.authoring_receipt_id}).`;
    reviewLink.href = reviewPage || "/app";
    reviewLink.hidden = false;
    form.hidden = true;
    task.setAttribute("aria-busy", "false");
    resultPanel.focus();
  }

  async function initialise() {
    if (!pocId || !pocApi || !sourcesApi) {
      errorPanel.textContent = "This assisted-authoring address is invalid. Return to the POC workspace.";
      errorPanel.hidden = false;
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
      document.querySelector("#poc-title").textContent = draft.display_name;
      document.querySelector("#poc-context").textContent = `${draft.customer_label} · local draft`;
      sources = sourceList.sources.slice();
      renderSources();
      task.setAttribute("aria-busy", "false");
      status.textContent = sources.length
        ? "Select one source receipt to continue."
        : "No accepted A2 source is available.";
    } catch {
      errorPanel.textContent = "The draft or source receipts could not be validated. No assisted action is available.";
      errorPanel.hidden = false;
      task.setAttribute("aria-busy", "false");
      submit.disabled = true;
    }
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (inFlight || !selectedReceipt) return;
    if (!pendingAttempt) {
      const endpoint = authoringApi(selectedReceipt);
      if (!endpoint || !isTrustedApiPath(endpoint)) {
        errorPanel.textContent = "The source-scoped authoring route is invalid. No proposal was created.";
        errorPanel.hidden = false;
        return;
      }
      pendingAttempt = {
        endpoint,
        payload: { idempotency_key: newIdempotencyKey() },
      };
    }
    inFlight = true;
    submit.disabled = true;
    clearError();
    status.textContent = "Running the explicit redacted authoring action…";
    try {
      const response = await requestJson(pendingAttempt.endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(pendingAttempt.payload),
      });
      if (!isTrustedAuthoringResponse(response)) throw new SafeRequestError(200, true);
      renderResult(response);
      pendingAttempt = null;
    } catch (error) {
      if (error instanceof SafeRequestError && !error.retrySameAttempt) {
        pendingAttempt = null;
      }
      errorPanel.textContent = safeFailureCopy(error);
      errorPanel.hidden = false;
      status.textContent = pendingAttempt
        ? "The response was interrupted. Retry uses the same authoring key."
        : "Start a new explicit action after reviewing the source.";
    } finally {
      inFlight = false;
      if (!form.hidden) submit.disabled = !selectedReceipt || Boolean(pendingAttempt);
    }
  });

  initialise();
})();
