(() => {
  "use strict";

  const POC_ID = "poc_inference_latency_demo";
  const DETAIL_API = `/api/workspace/pocs/${POC_ID}`;
  const READINESS_API = `/api/pocs/${POC_ID}/readiness`;
  const RUNS_API = `/api/pocs/${POC_ID}/runs`;
  const ATTEMPT_STORAGE_KEY = "exitspec.performance.attempt.v1";
  const OPERATION_ID_PATTERN = /^pwop_[a-f0-9]{32}$/;
  const EVIDENCE_PACK_PATTERN =
    /^\/artifacts\/[A-Za-z0-9][A-Za-z0-9._-]{0,127}\/decision-packet\.html$/;
  const OPERATION_STATUSES = new Set([
    "NOT_STARTED",
    "VALIDATING",
    "RUNNING",
    "COMPLETED",
    "BLOCKED",
    "NOT_PROVEN",
  ]);
  const TERMINAL_STATUSES = new Set([
    "COMPLETED",
    "BLOCKED",
    "NOT_PROVEN",
  ]);
  const POLL_DELAYS_MS = Object.freeze([750, 1250, 2000, 3000, 5000]);
  const MAX_POLL_REQUESTS = 60;
  const REASON_COPY = Object.freeze({
    ENDPOINT_PREFLIGHT_FAILED:
      "The approved endpoint is not ready. Start it, then check again.",
    READINESS_NOT_PROVEN:
      "Readiness could not be proven. Review the approved endpoint and retry.",
    READINESS_INTERNAL_FAILURE:
      "The readiness check could not finish safely. Try the check again.",
    WORKER_START_FAILED:
      "The proof worker did not start. No result has been inferred.",
    RUNNER_INTERNAL_FAILURE:
      "The proof run could not finish safely. No result has been inferred.",
    RUNNER_BLOCKED:
      "The approved proof run was blocked before evidence was complete.",
    RUNNER_NOT_PROVEN:
      "The run did not produce sufficient verified evidence.",
    EVIDENCE_VERIFICATION_FAILED:
      "Run output exists, but its Evidence Pack could not be verified.",
    EVIDENCE_PACK_URL_INVALID:
      "The Evidence Pack location failed the trusted-link check.",
  });
  const DEFAULT_REASON =
    "This state needs review. Provider details are intentionally hidden.";

  const $ = (selector) => document.querySelector(selector);
  const state = {
    actionMode: "none",
    actionPending: false,
    activeOperationId: null,
    appliedOperationGeneration: 0,
    contractRunnable: false,
    operation: null,
    pollCount: 0,
    pollTimer: null,
    pollingPaused: false,
    readiness: {
      status: "NOT_STARTED",
      reasonCode: null,
    },
    requestGeneration: 0,
  };

  class TrustedRequestError extends Error {
    constructor(status) {
      super("Trusted request failed.");
      this.name = "TrustedRequestError";
      this.status = status;
    }
  }

  function element(tagName, className, text) {
    const node = document.createElement(tagName);
    if (className) {
      node.className = className;
    }
    if (text !== undefined) {
      node.textContent = text;
    }
    return node;
  }

  function compactOwner(owner) {
    return String(owner || "Unassigned")
      .replaceAll("_", " ")
      .replace(/\b\w/g, (character) => character.toUpperCase());
  }

  function spacedStatus(status) {
    return String(status || "UNAVAILABLE").replaceAll("_", " ");
  }

  function safeReason(reasonCode) {
    return REASON_COPY[reasonCode] || DEFAULT_REASON;
  }

  function isOperationId(value) {
    return typeof value === "string" && OPERATION_ID_PATTERN.test(value);
  }

  function safeEvidencePackUrl(value) {
    if (
      typeof value !== "string" ||
      !EVIDENCE_PACK_PATTERN.test(value)
    ) {
      return null;
    }
    try {
      const parsed = new URL(value, window.location.origin);
      if (
        parsed.origin !== window.location.origin ||
        parsed.pathname !== value ||
        parsed.search ||
        parsed.hash
      ) {
        return null;
      }
      return parsed.pathname;
    } catch {
      return null;
    }
  }

  function readAttempt() {
    try {
      const raw = window.sessionStorage.getItem(ATTEMPT_STORAGE_KEY);
      if (!raw) {
        return null;
      }
      const attempt = JSON.parse(raw);
      if (
        !attempt ||
        typeof attempt !== "object" ||
        typeof attempt.idempotencyKey !== "string" ||
        !attempt.idempotencyKey ||
        (attempt.operationId !== null &&
          !isOperationId(attempt.operationId))
      ) {
        window.sessionStorage.removeItem(ATTEMPT_STORAGE_KEY);
        return null;
      }
      return attempt;
    } catch {
      return null;
    }
  }

  function saveAttempt(attempt) {
    try {
      window.sessionStorage.setItem(
        ATTEMPT_STORAGE_KEY,
        JSON.stringify(attempt)
      );
    } catch {
      // The server still enforces idempotency when storage is unavailable.
    }
  }

  function clearAttempt() {
    try {
      window.sessionStorage.removeItem(ATTEMPT_STORAGE_KEY);
    } catch {
      // Storage availability never changes execution authority.
    }
  }

  function newIdempotencyKey() {
    if (
      window.crypto &&
      typeof window.crypto.randomUUID === "function"
    ) {
      return `web_${window.crypto.randomUUID()}`;
    }
    const bytes = new Uint8Array(16);
    window.crypto.getRandomValues(bytes);
    const suffix = Array.from(bytes, (value) =>
      value.toString(16).padStart(2, "0")
    ).join("");
    return `web_${suffix}`;
  }

  async function requestJson(url, options = {}) {
    const response = await fetch(url, {
      ...options,
      headers: {
        Accept: "application/json",
        ...(options.headers || {}),
      },
    });
    const payload = await response.json().catch(() => null);
    if (!response.ok || !payload || typeof payload !== "object") {
      throw new TrustedRequestError(response.status);
    }
    return payload;
  }

  function normalizeSnapshot(payload) {
    const candidate =
      payload && typeof payload.operation === "object"
        ? payload.operation
        : payload;
    if (
      !candidate ||
      typeof candidate !== "object" ||
      !OPERATION_STATUSES.has(candidate.status)
    ) {
      throw new TypeError("Malformed operation snapshot.");
    }
    const operationId = candidate.operation_id ?? null;
    if (operationId !== null && !isOperationId(operationId)) {
      throw new TypeError("Malformed operation identity.");
    }
    return {
      evidencePackUrl: candidate.evidence_pack_url ?? null,
      operationId,
      reasonCode:
        typeof candidate.reason_code === "string"
          ? candidate.reason_code
          : null,
      status: candidate.status,
    };
  }

  function renderRequirements(requirements) {
    const list = $("#requirement-list");
    list.replaceChildren();
    list.setAttribute("aria-busy", "false");

    requirements.forEach((requirement, index) => {
      const item = element("li");
      const identity = element("div", "requirement-identity");
      identity.append(
        element("span", "requirement-number", String(index + 1).padStart(2, "0")),
        element("strong", "", requirement.label),
        element("small", "", requirement.sample_requirement)
      );
      item.append(
        identity,
        element("strong", "requirement-threshold", requirement.threshold)
      );
      list.append(item);
    });
  }

  function renderDetail(detail) {
    $("#performance-title").textContent = detail.display_name;
    $("#performance-customer").textContent = detail.customer_label;
    $("#performance-owner").textContent = compactOwner(detail.owner);
    $("#performance-phase").textContent = spacedStatus(detail.phase);
    $("#performance-use-case").textContent = detail.use_case;
    $("#agreement-status").textContent = spacedStatus(detail.agreement_status);
    $("#customer-status").textContent = spacedStatus(detail.customer_status);
    $(".agreement-state").dataset.state = detail.agreement_status;

    state.contractRunnable =
      detail.agreement_status === "FROZEN" &&
      detail.customer_status === "CONFIRMED";

    renderRequirements(detail.requirements);

    $("#measured-requests").textContent = String(
      detail.run_plan.measured_requests
    );
    $("#configured-concurrency").textContent = String(
      detail.run_plan.configured_concurrency
    );
    $("#warmup-requests").textContent = String(
      detail.run_plan.warmup_requests
    );
    $("#model-name").textContent = detail.run_plan.model;
    $("#endpoint-class").textContent = detail.run_plan.endpoint_class;
    $("#adapter-identity").textContent =
      `${detail.technical.adapter} · v${detail.technical.adapter_version}`;
    $("#contract-identity").textContent =
      `${detail.technical.contract_id} · v${detail.technical.contract_version}`;
    $("#performance-limitation").textContent = detail.limitation;
    renderAction();
  }

  function showDetailUnavailable() {
    state.contractRunnable = false;
    $("#requirement-list").setAttribute("aria-busy", "false");
    $("#requirement-list").replaceChildren(
      element(
        "li",
        "panel-loading",
        "Frozen requirements are unavailable. No result has been inferred."
      )
    );
    $("#agreement-status").textContent = "UNAVAILABLE";
    const error = $("#performance-error");
    error.textContent =
      "The frozen POC could not be loaded. Proof execution remains disabled.";
    error.hidden = false;
    renderAction();
  }

  function setOperationReference(operationId) {
    $("#operation-reference").textContent = operationId || "Not started";
  }

  function hideEvidencePack() {
    const link = $("#evidence-pack-link");
    link.hidden = true;
    link.removeAttribute("href");
  }

  function renderEvidence(operation) {
    const panel = $(".evidence-panel");
    const verdict = $("#evidence-verdict");
    const reason = $("#evidence-reason");
    hideEvidencePack();

    if (!operation || operation.status === "NOT_STARTED") {
      panel.dataset.state = "NOT_STARTED";
      $("#evidence-status").textContent = "NOT RUN";
      verdict.textContent = "NOT RUN";
      reason.textContent =
        "Verified evidence will appear only after the trusted run completes.";
      return;
    }

    if (operation.status === "COMPLETED") {
      const packUrl = safeEvidencePackUrl(operation.evidencePackUrl);
      if (packUrl) {
        const link = $("#evidence-pack-link");
        link.href = packUrl;
        link.hidden = false;
        panel.dataset.state = "COMPLETED";
        $("#evidence-status").textContent = "PACK READY";
        verdict.textContent = "PACK READY";
        reason.textContent =
          "Execution completed and the server verified this Evidence Pack. Open it for the verdict.";
        window.dispatchEvent(
          new CustomEvent("exitspec:evidence-updated")
        );
      } else {
        panel.dataset.state = "NOT_PROVEN";
        $("#evidence-status").textContent = "NOT PROVEN";
        verdict.textContent = "NOT PROVEN";
        reason.textContent =
          "Execution completed, but no trusted Evidence Pack link was accepted.";
      }
      return;
    }

    if (operation.status === "BLOCKED") {
      panel.dataset.state = "BLOCKED";
      $("#evidence-status").textContent = "BLOCKED";
      verdict.textContent = "BLOCKED";
      reason.textContent = safeReason(operation.reasonCode);
      return;
    }

    if (operation.status === "NOT_PROVEN") {
      panel.dataset.state = "NOT_PROVEN";
      $("#evidence-status").textContent = "NOT PROVEN";
      verdict.textContent = "NOT PROVEN";
      reason.textContent = safeReason(operation.reasonCode);
      return;
    }

    panel.dataset.state = operation.status;
    $("#evidence-status").textContent = "PENDING";
    verdict.textContent = "PENDING";
    reason.textContent =
      "The run is active. No verdict has been inferred from execution state.";
  }

  function renderRunState() {
    const operationStatus = state.operation?.status || "NOT_STARTED";
    const displayedStatus =
      operationStatus === "NOT_STARTED"
        ? state.readiness.status === "COMPLETED"
          ? "READY"
          : "NOT STARTED"
        : spacedStatus(operationStatus);

    $("#readiness-status").textContent =
      state.readiness.status === "COMPLETED"
        ? "READY"
        : spacedStatus(state.readiness.status);
    $("#execution-status").textContent = displayedStatus;
    $(".execution-state").dataset.state = operationStatus;
    setOperationReference(state.operation?.operationId || null);
    renderEvidence(state.operation);
  }

  function renderAction() {
    const readinessButton = $("#check-readiness");
    const runButton = $("#run-proof");
    const heading = $("#current-task-heading");
    const guidance = $("#task-guidance");
    const operationStatus = state.operation?.status || "NOT_STARTED";
    const failedOperation = ["BLOCKED", "NOT_PROVEN"].includes(
      operationStatus
    );

    readinessButton.disabled = true;
    readinessButton.hidden = true;
    readinessButton.textContent = "Check readiness";
    runButton.disabled = true;
    runButton.hidden = true;
    state.actionMode = "none";

    if (!state.contractRunnable) {
      heading.textContent = "Review the frozen agreement";
      guidance.textContent =
        "Proof execution stays disabled until the confirmed contract is available.";
      readinessButton.hidden = false;
      readinessButton.textContent = "Proof unavailable";
      renderRunState();
      return;
    }

    if (state.actionPending) {
      heading.textContent =
        state.readiness.status === "VALIDATING"
          ? "Checking endpoint readiness"
          : "Starting the trusted proof";
      guidance.textContent =
        "ExitSpec is waiting for a bounded server response.";
      if (state.readiness.status === "VALIDATING") {
        readinessButton.hidden = false;
        readinessButton.textContent = "Checking readiness…";
      } else {
        runButton.hidden = false;
      }
      renderRunState();
      return;
    }

    if (["VALIDATING", "RUNNING"].includes(operationStatus)) {
      heading.textContent = "Proof run in progress";
      guidance.textContent =
        "The server owns the model, workload, limits, and evidence path.";
      if (state.pollingPaused) {
        readinessButton.disabled = false;
        readinessButton.hidden = false;
        readinessButton.textContent = "Refresh status";
        state.actionMode = "poll";
      }
      renderRunState();
      return;
    }

    if (operationStatus === "COMPLETED") {
      heading.textContent = "Review the verified Evidence Pack";
      guidance.textContent =
        "Completion is not a PASS. The Evidence Pack contains the server-issued verdict.";
      renderRunState();
      return;
    }

    if (
      state.readiness.status !== "COMPLETED" ||
      failedOperation
    ) {
      heading.textContent = failedOperation
        ? "Resolve the run blocker"
        : "Check endpoint readiness";
      guidance.textContent =
        state.readiness.reasonCode || state.operation?.reasonCode
          ? safeReason(
              state.readiness.reasonCode || state.operation?.reasonCode
            )
          : "This bounded check does not start the performance workload.";
      readinessButton.disabled = false;
      readinessButton.hidden = false;
      state.actionMode = "readiness";
      $("#run-reason").textContent = guidance.textContent;
      renderRunState();
      return;
    }

    heading.textContent = "Run the frozen latency check";
    guidance.textContent =
      "One server-controlled run will produce verified evidence or fail closed.";
    runButton.disabled = false;
    runButton.hidden = false;
    state.actionMode = "run";
    $("#run-reason").textContent =
      "Endpoint ready. The frozen 111-call boundary is available to run.";
    renderRunState();
  }

  function applyReadiness(snapshot, { resetFailedOperation = false } = {}) {
    state.readiness = {
      reasonCode: snapshot.reasonCode,
      status: snapshot.status,
    };
    if (
      resetFailedOperation &&
      snapshot.status === "COMPLETED" &&
      ["BLOCKED", "NOT_PROVEN"].includes(state.operation?.status)
    ) {
      stopPolling();
      state.operation = null;
      state.activeOperationId = null;
      clearAttempt();
    }
    $("#run-reason").textContent = snapshot.reasonCode
      ? safeReason(snapshot.reasonCode)
      : snapshot.status === "COMPLETED"
        ? "Endpoint ready. The frozen proof is available to run."
        : "No network check has run. Cached state only.";
    renderAction();
  }

  function applyOperation(snapshot, expectedOperationId, generation) {
    if (
      !isOperationId(expectedOperationId) ||
      snapshot.operationId !== expectedOperationId ||
      state.activeOperationId !== expectedOperationId ||
      generation < state.appliedOperationGeneration
    ) {
      return false;
    }
    state.appliedOperationGeneration = generation;
    state.operation = snapshot;
    state.pollingPaused = false;
    saveAttempt({
      idempotencyKey:
        readAttempt()?.idempotencyKey || "recovered_server_operation",
      operationId: snapshot.operationId,
    });
    renderAction();
    return true;
  }

  function stopPolling() {
    if (state.pollTimer !== null) {
      window.clearTimeout(state.pollTimer);
      state.pollTimer = null;
    }
  }

  function schedulePoll(operationId) {
    stopPolling();
    if (
      !isOperationId(operationId) ||
      TERMINAL_STATUSES.has(state.operation?.status)
    ) {
      return;
    }
    if (state.pollCount >= MAX_POLL_REQUESTS) {
      state.pollingPaused = true;
      renderAction();
      return;
    }
    const delay =
      POLL_DELAYS_MS[
        Math.min(state.pollCount, POLL_DELAYS_MS.length - 1)
      ];
    state.pollTimer = window.setTimeout(() => {
      void pollOperation(operationId);
    }, delay);
  }

  async function pollOperation(operationId, { immediate = false } = {}) {
    if (!isOperationId(operationId)) {
      state.pollingPaused = true;
      renderAction();
      return;
    }
    stopPolling();
    if (!immediate) {
      state.pollCount += 1;
    }
    const generation = ++state.requestGeneration;
    try {
      const payload = await requestJson(`${RUNS_API}/${operationId}`);
      const snapshot = normalizeSnapshot(payload);
      if (applyOperation(snapshot, operationId, generation)) {
        schedulePoll(operationId);
      }
    } catch {
      state.pollingPaused = true;
      $("#run-reason").textContent =
        "Status could not be refreshed safely. The proof was not restarted.";
      renderAction();
    }
  }

  async function refreshReadiness() {
    if (state.actionPending) {
      return;
    }
    state.actionPending = true;
    state.readiness.status = "VALIDATING";
    state.readiness.reasonCode = null;
    renderAction();
    try {
      const payload = await requestJson(READINESS_API, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      const snapshot = normalizeSnapshot(payload);
      applyReadiness(snapshot, { resetFailedOperation: true });
    } catch {
      applyReadiness({
        reasonCode: "READINESS_INTERNAL_FAILURE",
        status: "NOT_PROVEN",
      });
    } finally {
      state.actionPending = false;
      renderAction();
    }
  }

  async function startProof() {
    if (state.actionPending || state.readiness.status !== "COMPLETED") {
      return;
    }
    state.actionPending = true;
    renderAction();

    const existingAttempt = readAttempt();
    const attempt = existingAttempt || {
      idempotencyKey: newIdempotencyKey(),
      operationId: null,
    };
    saveAttempt(attempt);

    if (isOperationId(attempt.operationId)) {
      state.activeOperationId = attempt.operationId;
      state.actionPending = false;
      state.pollCount = 0;
      await pollOperation(attempt.operationId, { immediate: true });
      return;
    }

    const generation = ++state.requestGeneration;
    try {
      const payload = await requestJson(RUNS_API, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          idempotency_key: attempt.idempotencyKey,
        }),
      });
      const snapshot = normalizeSnapshot(payload);
      if (!isOperationId(snapshot.operationId)) {
        throw new TypeError("Missing trusted operation identity.");
      }
      state.activeOperationId = snapshot.operationId;
      saveAttempt({
        idempotencyKey: attempt.idempotencyKey,
        operationId: snapshot.operationId,
      });
      applyOperation(snapshot, snapshot.operationId, generation);
      state.pollCount = 0;
      schedulePoll(snapshot.operationId);
    } catch {
      $("#run-reason").textContent =
        "The start response was not trusted. Retry reuses the same operation key.";
    } finally {
      state.actionPending = false;
      renderAction();
    }
  }

  async function loadDetail() {
    try {
      const payload = await requestJson(DETAIL_API);
      renderDetail(payload);
    } catch {
      showDetailUnavailable();
    }
  }

  async function loadReadiness() {
    try {
      const payload = await requestJson(READINESS_API);
      applyReadiness(normalizeSnapshot(payload));
    } catch {
      applyReadiness({
        reasonCode: "READINESS_INTERNAL_FAILURE",
        status: "NOT_PROVEN",
      });
    }
  }

  async function recoverKnownOperation() {
    const attempt = readAttempt();
    if (!attempt || !isOperationId(attempt.operationId)) {
      return;
    }
    state.activeOperationId = attempt.operationId;
    state.pollCount = 0;
    await pollOperation(attempt.operationId, { immediate: true });
  }

  $("#check-readiness").addEventListener("click", () => {
    if (state.actionMode === "readiness") {
      void refreshReadiness();
    } else if (
      state.actionMode === "poll" &&
      isOperationId(state.activeOperationId)
    ) {
      state.pollingPaused = false;
      state.pollCount = 0;
      renderAction();
      void pollOperation(state.activeOperationId, { immediate: true });
    }
  });

  $("#run-proof").addEventListener("click", () => {
    if (state.actionMode === "run") {
      void startProof();
    }
  });

  renderAction();
  void Promise.allSettled([
    loadDetail(),
    loadReadiness(),
    recoverKnownOperation(),
  ]);
})();
