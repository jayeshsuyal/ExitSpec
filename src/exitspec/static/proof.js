(() => {
  "use strict";

  const POC_ID_PATTERN = /^poc_[a-z0-9][a-z0-9_-]{2,63}$/;
  const OPERATION_ID_PATTERN = /^prun_[a-f0-9]{32}$/;
  const SHA256_PATTERN = /^[a-f0-9]{64}$/;
  const ROUTE_PATTERN =
    /^\/app\/pocs\/(poc_[a-z0-9][a-z0-9_-]{2,63})$/;
  const EVIDENCE_PACK_PATTERN =
    /^\/artifacts\/run_[a-f0-9]{32}\/decision-packet\.html$/;
  const RUN_STATUSES = new Set([
    "NOT_STARTED",
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
  const VERDICTS = new Set(["PASS", "FAIL", "NOT_PROVEN"]);
  const METRICS = new Set(["TTFT_P95_MS", "ERROR_RATE_PERCENT"]);
  const POLL_DELAYS = Object.freeze([500, 900, 1500, 2500, 4000]);
  const MAX_POLLS = 90;
  const REASON_COPY = Object.freeze({
    ENDPOINT_PREFLIGHT_FAILED:
      "The frozen endpoint did not pass the bounded preflight. No performance conclusion was made.",
    RUNNER_INTERNAL_FAILURE:
      "The proof failed closed before verified evidence could be released.",
    RUN_NOT_PROVEN:
      "The run did not produce sufficient verified evidence.",
    WORKER_LAUNCH_FAILED:
      "The local proof worker did not start. No requests were inferred.",
  });

  const routeMatch =
    window.location.search === "" && window.location.hash === ""
      ? window.location.pathname.match(ROUTE_PATTERN)
      : null;
  const pocId =
    routeMatch && POC_ID_PATTERN.test(routeMatch[1]) ? routeMatch[1] : null;
  const pocApi = pocId ? `/api/pocs/${pocId}` : null;
  const agreementApi = pocApi ? `${pocApi}/agreement` : null;
  const runsApi = pocApi ? `${pocApi}/runs` : null;
  const latestRunApi = runsApi ? `${runsApi}/latest` : null;

  const main = document.querySelector("#performance-main");
  const runButton = document.querySelector("#run-proof");
  const acknowledgement = document.querySelector("#execution-acknowledged");
  const acknowledgementLabel = document.querySelector(
    ".run-acknowledgement"
  );
  const errorPanel = document.querySelector("#performance-error");
  let draft = null;
  let agreement = null;
  let run = null;
  let actionPending = false;
  let pollCount = 0;
  let pollTimer = null;

  class TrustedRequestError extends Error {
    constructor(status) {
      super("Trusted proof request failed.");
      this.name = "TrustedRequestError";
      this.status = status;
    }
  }

  function hasExactKeys(value, expectedKeys) {
    if (!value || typeof value !== "object" || Array.isArray(value)) {
      return false;
    }
    const actual = Object.keys(value).sort();
    const expected = [...expectedKeys].sort();
    return (
      actual.length === expected.length &&
      actual.every((key, index) => key === expected[index])
    );
  }

  function safeText(value, maximum) {
    return Boolean(
      typeof value === "string" &&
        value.trim().length > 0 &&
        value.length <= maximum &&
        !/[\u0000-\u0008\u000b\u000c\u000e-\u001f]/.test(value)
    );
  }

  function exactTargetUrl(value) {
    if (typeof value !== "string" || value.length > 2048) {
      return false;
    }
    try {
      const parsed = new URL(value);
      return Boolean(
        ["http:", "https:"].includes(parsed.protocol) &&
          parsed.hostname &&
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

  function safeEvidenceUrl(value) {
    if (
      typeof value !== "string" ||
      !EVIDENCE_PACK_PATTERN.test(value)
    ) {
      return null;
    }
    try {
      const parsed = new URL(value, window.location.origin);
      return parsed.origin === window.location.origin &&
        parsed.pathname === value &&
        !parsed.search &&
        !parsed.hash
        ? parsed.pathname
        : null;
    } catch {
      return null;
    }
  }

  function trustedDraft(value) {
    return Boolean(
      value &&
        value.poc_id === pocId &&
        value.archive_state === "ACTIVE" &&
        safeText(value.display_name, 160) &&
        safeText(value.customer_label, 160) &&
        safeText(value.use_case, 500) &&
        safeText(value.owner, 160)
    );
  }

  function trustedDefinition(value) {
    if (
      !value ||
      typeof value !== "object" ||
      !METRICS.has(value.metric) ||
      !["LT", "LTE"].includes(value.operator) ||
      typeof value.threshold !== "number" ||
      !Number.isFinite(value.threshold) ||
      !Number.isInteger(value.minimum_samples) ||
      !Number.isInteger(value.concurrency) ||
      value.minimum_samples < 1 ||
      value.minimum_samples > 1000 ||
      value.concurrency < 1 ||
      value.concurrency > 32 ||
      value.concurrency > value.minimum_samples ||
      !safeText(value.source_quote, 4000)
    ) {
      return false;
    }
    if (value.metric === "ERROR_RATE_PERCENT") {
      return (
        value.operator === "LT" &&
        value.threshold > 0 &&
        value.threshold < 100
      );
    }
    return value.threshold > 0 && value.threshold <= 60000;
  }

  function trustedAgreement(value) {
    if (
      !value ||
      value.poc_id !== pocId ||
      !Array.isArray(value.definitions) ||
      value.definitions.length !== 2 ||
      !value.definitions.every(trustedDefinition) ||
      !value.draft ||
      !value.confirmation ||
      !value.frozen_contract
    ) {
      return false;
    }
    const metrics = new Set(
      value.definitions.map((definition) => definition.metric)
    );
    return Boolean(
      metrics.size === 2 &&
        [...METRICS].every((metric) => metrics.has(metric)) &&
        SHA256_PATTERN.test(value.frozen_contract.canonical_hash) &&
        safeText(value.frozen_contract.contract_id, 512) &&
        safeText(value.frozen_contract.target_provider, 512) &&
        safeText(value.frozen_contract.endpoint_class, 512) &&
        exactTargetUrl(value.frozen_contract.endpoint) &&
        safeText(value.frozen_contract.model, 512) &&
        value.confirmation.agreement_acknowledged === true
    );
  }

  const RUN_KEYS = Object.freeze([
    "adapter",
    "adapter_version",
    "attempted_count",
    "authorized_request_count",
    "concurrency",
    "contract_hash",
    "contract_id",
    "contract_version",
    "endpoint",
    "endpoint_class",
    "error_count",
    "error_rate_percent",
    "evidence_pack_url",
    "is_terminal",
    "measured_requests",
    "model",
    "operation_id",
    "p95_ttft_ms",
    "poc_id",
    "reason_code",
    "status",
    "successful_count",
    "target_provider",
    "verdict",
    "warmup_requests",
    "workload_id",
  ]);

  function nullableCount(value) {
    return (
      value === null ||
      (Number.isInteger(value) && value >= 0 && value <= 1000)
    );
  }

  function nullableDecimal(value) {
    return (
      value === null ||
      (typeof value === "string" &&
        /^(?:0|[1-9]\d*)(?:\.\d+)?$/.test(value) &&
        Number.isFinite(Number(value)))
    );
  }

  function trustedRun(value) {
    if (
      !hasExactKeys(value, RUN_KEYS) ||
      value.poc_id !== pocId ||
      !SHA256_PATTERN.test(value.contract_hash) ||
      !safeText(value.contract_id, 512) ||
      !safeText(value.contract_version, 80) ||
      !safeText(value.workload_id, 512) ||
      !safeText(value.target_provider, 512) ||
      !safeText(value.endpoint_class, 512) ||
      !exactTargetUrl(value.endpoint) ||
      !safeText(value.model, 512) ||
      !safeText(value.adapter, 160) ||
      !safeText(value.adapter_version, 80) ||
      !Number.isInteger(value.measured_requests) ||
      value.measured_requests < 1 ||
      value.measured_requests > 1000 ||
      !Number.isInteger(value.concurrency) ||
      value.concurrency < 1 ||
      value.concurrency > 32 ||
      value.concurrency > value.measured_requests ||
      !Number.isInteger(value.warmup_requests) ||
      value.warmup_requests < 0 ||
      value.warmup_requests > 100 ||
      value.authorized_request_count !==
        1 + value.warmup_requests + value.measured_requests ||
      !RUN_STATUSES.has(value.status) ||
      !nullableCount(value.attempted_count) ||
      !nullableCount(value.successful_count) ||
      !nullableCount(value.error_count) ||
      !nullableDecimal(value.p95_ttft_ms) ||
      !nullableDecimal(value.error_rate_percent)
    ) {
      return false;
    }
    const operationValid =
      value.operation_id === null ||
      OPERATION_ID_PATTERN.test(value.operation_id);
    const evidenceUrl =
      value.evidence_pack_url === null
        ? null
        : safeEvidenceUrl(value.evidence_pack_url);
    if (!operationValid || (value.evidence_pack_url !== null && !evidenceUrl)) {
      return false;
    }
    if (value.status === "NOT_STARTED") {
      return (
        value.operation_id === null &&
        value.verdict === null &&
        value.evidence_pack_url === null &&
        value.is_terminal === false
      );
    }
    if (value.status === "RUNNING") {
      return (
        OPERATION_ID_PATTERN.test(value.operation_id) &&
        value.verdict === null &&
        value.attempted_count === null &&
        value.successful_count === null &&
        value.error_count === null &&
        value.p95_ttft_ms === null &&
        value.error_rate_percent === null &&
        value.evidence_pack_url === null &&
        value.is_terminal === false
      );
    }
    if (["BLOCKED", "NOT_PROVEN"].includes(value.status)) {
      return (
        OPERATION_ID_PATTERN.test(value.operation_id) &&
        value.verdict === null &&
        value.attempted_count === null &&
        value.successful_count === null &&
        value.error_count === null &&
        value.p95_ttft_ms === null &&
        value.error_rate_percent === null &&
        value.evidence_pack_url === null &&
        value.is_terminal === true
      );
    }
    return Boolean(
      value.status === "COMPLETED" &&
        OPERATION_ID_PATTERN.test(value.operation_id) &&
        VERDICTS.has(value.verdict) &&
        Number.isInteger(value.attempted_count) &&
        Number.isInteger(value.successful_count) &&
        Number.isInteger(value.error_count) &&
        value.attempted_count === value.measured_requests &&
        value.successful_count + value.error_count === value.attempted_count &&
        value.evidence_pack_url !== null &&
        value.is_terminal === true
    );
  }

  function crossBindingsValid() {
    const frozen = agreement.frozen_contract;
    const ttft = agreement.definitions.find(
      (definition) => definition.metric === "TTFT_P95_MS"
    );
    const error = agreement.definitions.find(
      (definition) => definition.metric === "ERROR_RATE_PERCENT"
    );
    return Boolean(
      run.contract_hash === frozen.canonical_hash &&
        run.contract_id === frozen.contract_id &&
        run.target_provider === frozen.target_provider &&
        run.endpoint_class === frozen.endpoint_class &&
        run.endpoint === frozen.endpoint &&
        run.model === frozen.model &&
        run.measured_requests === error.minimum_samples &&
        run.concurrency === ttft.concurrency &&
        ttft.concurrency === error.concurrency
    );
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

  function compactOwner(value) {
    return String(value)
      .replaceAll("_", " ")
      .replace(/\b\w/g, (character) => character.toUpperCase());
  }

  function operatorSymbol(value) {
    return value === "LT" ? "<" : "≤";
  }

  function observedFor(definition) {
    if (!run || run.status !== "COMPLETED") {
      return "Not measured";
    }
    if (definition.metric === "TTFT_P95_MS") {
      return run.p95_ttft_ms === null
        ? "Not proven"
        : `Observed ${run.p95_ttft_ms} ms`;
    }
    return run.error_rate_percent === null
      ? "Not proven"
      : `Observed ${run.error_rate_percent}%`;
  }

  function renderRequirements() {
    const list = document.querySelector("#requirement-list");
    list.replaceChildren();
    agreement.definitions.forEach((definition, index) => {
      const item = document.createElement("li");
      const identity = document.createElement("div");
      identity.className = "requirement-identity";
      const number = document.createElement("span");
      number.className = "requirement-number";
      number.textContent = String(index + 1).padStart(2, "0");
      const label = document.createElement("strong");
      label.textContent =
        definition.metric === "TTFT_P95_MS"
          ? "P95 time to first token"
          : "Attempted-request error rate";
      const samples = document.createElement("small");
      samples.textContent = `${definition.minimum_samples} samples · concurrency ${definition.concurrency}`;
      identity.append(number, label, samples);

      const result = document.createElement("div");
      result.className = "requirement-result";
      result.dataset.state =
        run.status === "COMPLETED" ? run.verdict : "NOT_RUN";
      const threshold = document.createElement("strong");
      threshold.className = "requirement-threshold";
      threshold.textContent =
        `${operatorSymbol(definition.operator)} ${definition.threshold}` +
        (definition.metric === "TTFT_P95_MS" ? " ms" : "%");
      const observed = document.createElement("small");
      observed.textContent = observedFor(definition);
      result.append(threshold, observed);
      item.append(identity, result);
      list.append(item);
    });
    list.setAttribute("aria-busy", "false");
  }

  function reasonCopy() {
    return (
      REASON_COPY[run.reason_code] ||
      "The run stopped without releasing a performance conclusion."
    );
  }

  function setJourney(decide) {
    document.querySelector("#journey-prove").dataset.state = decide
      ? "complete"
      : "current";
    document.querySelector("#journey-decide").dataset.state = decide
      ? "current"
      : "locked";
    document.querySelector("#performance-phase").textContent = decide
      ? "DECIDE"
      : "PROVE";
  }

  function renderEvidence() {
    const panel = document.querySelector(".evidence-panel");
    const status = document.querySelector("#evidence-status");
    const verdict = document.querySelector("#evidence-verdict");
    const reason = document.querySelector("#evidence-reason");
    const link = document.querySelector("#evidence-pack-link");
    link.hidden = true;
    link.removeAttribute("href");

    if (run.status === "COMPLETED") {
      const packUrl = safeEvidenceUrl(run.evidence_pack_url);
      panel.dataset.state = run.verdict;
      status.textContent = run.verdict.replaceAll("_", " ");
      verdict.textContent = run.verdict.replaceAll("_", " ");
      reason.textContent =
        "This verdict was released only after artifact verification and independent recomputation.";
      link.href = packUrl;
      link.hidden = false;
      setJourney(true);
      return;
    }
    if (run.status === "BLOCKED") {
      panel.dataset.state = "BLOCKED";
      status.textContent = "BLOCKED";
      verdict.textContent = "BLOCKED";
      reason.textContent =
        "Execution was blocked. No performance verdict exists.";
      setJourney(false);
      return;
    }
    if (run.status === "NOT_PROVEN") {
      panel.dataset.state = "NOT_PROVEN";
      status.textContent = "NOT PROVEN";
      verdict.textContent = "NOT PROVEN";
      reason.textContent =
        "No verified result was released from this execution attempt.";
      setJourney(false);
      return;
    }
    panel.dataset.state = run.status;
    status.textContent =
      run.status === "RUNNING" ? "PENDING" : "NOT RUN";
    verdict.textContent =
      run.status === "RUNNING" ? "PENDING" : "NOT RUN";
    reason.textContent =
      run.status === "RUNNING"
        ? "Execution is active. RUNNING is not a verdict."
        : "No performance conclusion exists before verified evidence.";
    setJourney(false);
  }

  function renderAction() {
    const heading = document.querySelector("#current-task-heading");
    const kicker = document.querySelector("#task-kicker");
    const guidance = document.querySelector("#task-guidance");
    const runReason = document.querySelector("#run-reason");
    const executionState = document.querySelector(".execution-state");
    executionState.dataset.state = run.status;
    document.querySelector("#execution-status").textContent =
      run.status.replaceAll("_", " ");
    document.querySelector("#operation-reference").textContent =
      run.operation_id || "Not started";

    runButton.hidden = false;
    runButton.disabled = true;
    acknowledgement.disabled = true;
    acknowledgementLabel.hidden = false;

    if (actionPending || run.status === "RUNNING") {
      heading.textContent = "Proof run in progress";
      kicker.textContent = "Current task · Prove";
      guidance.textContent =
        "The server owns the target, workload, limits, credentials, and evidence path.";
      runReason.textContent =
        "Preflight runs first. No progress percentage or ETA is inferred.";
      runButton.textContent = "Run in progress…";
      acknowledgementLabel.hidden = true;
      return;
    }
    if (run.status === "COMPLETED") {
      heading.textContent = "Review the verified decision";
      kicker.textContent = "Current task · Decide";
      guidance.textContent =
        `${run.verdict.replaceAll("_", " ")} is the evidence verdict, not automatic authorization to ship.`;
      runReason.textContent =
        "Execution completed and the Evidence Pack passed independent verification.";
      runButton.hidden = true;
      acknowledgementLabel.hidden = true;
      return;
    }
    if (["BLOCKED", "NOT_PROVEN"].includes(run.status)) {
      heading.textContent = "Retry the frozen proof";
      kicker.textContent = "Current task · Prove";
      guidance.textContent = reasonCopy();
      runReason.textContent = guidance.textContent;
      runButton.textContent = "Retry frozen proof";
    } else {
      heading.textContent = "Run the frozen proof";
      kicker.textContent = "Current task · Prove";
      guidance.textContent =
        "One bounded run will return verified evidence or fail closed.";
      runReason.textContent =
        "The readiness preflight is request 1 and does not itself produce a verdict.";
      runButton.textContent = "Run frozen proof";
    }
    acknowledgement.disabled = false;
    runButton.disabled = !acknowledgement.checked;
  }

  function renderAll() {
    document.querySelector("#performance-title").textContent =
      draft.display_name;
    document.querySelector("#performance-customer").textContent =
      draft.customer_label;
    document.querySelector("#performance-owner").textContent =
      compactOwner(draft.owner);
    document.querySelector("#performance-use-case").textContent =
      draft.use_case;
    document.querySelector("#agreement-status").textContent = "FROZEN";
    document.querySelector("#target-summary").textContent =
      `${run.target_provider} · ${run.model}`;
    document.querySelector("#measured-requests").textContent = String(
      run.measured_requests
    );
    document.querySelector("#configured-concurrency").textContent = String(
      run.concurrency
    );
    document.querySelector("#warmup-requests").textContent = String(
      run.warmup_requests
    );
    document.querySelector("#model-name").textContent = run.model;
    document.querySelector("#endpoint-class").textContent =
      run.endpoint_class;
    document.querySelector("#adapter-identity").textContent =
      `${run.adapter} · v${run.adapter_version}`;
    document.querySelector("#contract-identity").textContent =
      `${run.contract_id} · v${run.contract_version}`;
    document.querySelector("#execution-acknowledgement-copy").textContent =
      `I authorize this exact ${run.authorized_request_count}-request run ` +
      `(${run.measured_requests} measured + ${run.warmup_requests} warmups + 1 preflight) against the frozen target.`;
    renderRequirements();
    renderEvidence();
    renderAction();
    main.setAttribute("aria-busy", "false");
  }

  function blockProof(message) {
    actionPending = false;
    stopPolling();
    runButton.disabled = true;
    acknowledgement.disabled = true;
    errorPanel.textContent = message;
    errorPanel.hidden = false;
    main.setAttribute("aria-busy", "false");
  }

  function attemptStorageKey() {
    return `exitspec.proof.attempt.v1.${pocId}.${run.contract_hash}`;
  }

  function readAttempt() {
    try {
      const value = window.sessionStorage.getItem(attemptStorageKey());
      return safeText(value, 200) ? value : null;
    } catch {
      return null;
    }
  }

  function saveAttempt(value) {
    try {
      window.sessionStorage.setItem(attemptStorageKey(), value);
    } catch {
      // Server idempotency remains authoritative without browser storage.
    }
  }

  function clearAttempt() {
    try {
      window.sessionStorage.removeItem(attemptStorageKey());
    } catch {
      // Storage does not control proof authority.
    }
  }

  function newIdempotencyKey() {
    if (
      window.crypto &&
      typeof window.crypto.randomUUID === "function"
    ) {
      return `proof_${window.crypto.randomUUID()}`;
    }
    const bytes = new Uint8Array(16);
    window.crypto.getRandomValues(bytes);
    return (
      "proof_" +
      Array.from(bytes, (value) =>
        value.toString(16).padStart(2, "0")
      ).join("")
    );
  }

  function stopPolling() {
    if (pollTimer !== null) {
      window.clearTimeout(pollTimer);
      pollTimer = null;
    }
  }

  function schedulePoll() {
    stopPolling();
    if (TERMINAL_STATUSES.has(run.status)) {
      return;
    }
    if (pollCount >= MAX_POLLS) {
      blockProof(
        "Status polling paused safely. Reload to resume; the run was not restarted."
      );
      return;
    }
    const delay =
      POLL_DELAYS[Math.min(pollCount, POLL_DELAYS.length - 1)];
    pollTimer = window.setTimeout(() => {
      void pollLatest();
    }, delay);
  }

  async function pollLatest() {
    pollCount += 1;
    try {
      const payload = await requestJson(latestRunApi);
      if (!trustedRun(payload)) {
        throw new TypeError("Malformed run projection.");
      }
      run = payload;
      renderAll();
      schedulePoll();
    } catch {
      blockProof(
        "The run status could not be validated. Reload to resume without starting another run."
      );
    }
  }

  async function startProof() {
    if (
      actionPending ||
      !acknowledgement.checked ||
      run.status === "RUNNING" ||
      run.status === "COMPLETED"
    ) {
      return;
    }
    actionPending = true;
    errorPanel.hidden = true;
    renderAction();
    const idempotencyKey = readAttempt() || newIdempotencyKey();
    saveAttempt(idempotencyKey);
    try {
      const payload = await requestJson(runsApi, {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
          execution_acknowledged: true,
          idempotency_key: idempotencyKey,
        }),
      });
      if (
        !hasExactKeys(payload, ["operation", "replayed"]) ||
        typeof payload.replayed !== "boolean" ||
        !trustedRun(payload.operation)
      ) {
        throw new TypeError("Malformed start projection.");
      }
      run = payload.operation;
      acknowledgement.checked = false;
      pollCount = 0;
      renderAll();
      schedulePoll();
    } catch {
      blockProof(
        "The run start response was not trusted. Retry reuses the same operation key."
      );
    } finally {
      actionPending = false;
      if (run) {
        renderAction();
      }
    }
  }

  async function loadProof() {
    if (!pocId || !pocApi || !agreementApi || !runsApi || !latestRunApi) {
      blockProof("This proof route is invalid.");
      return;
    }
    try {
      const [draftPayload, agreementPayload, runPayload] =
        await Promise.all([
          requestJson(pocApi),
          requestJson(agreementApi),
          requestJson(latestRunApi),
        ]);
      if (
        !trustedDraft(draftPayload) ||
        !trustedAgreement(agreementPayload) ||
        !trustedRun(runPayload)
      ) {
        throw new TypeError("Malformed proof inputs.");
      }
      draft = draftPayload;
      agreement = agreementPayload;
      run = runPayload;
      if (!crossBindingsValid()) {
        throw new TypeError("Cross-POC proof binding failed.");
      }
      if (TERMINAL_STATUSES.has(run.status)) {
        clearAttempt();
      }
      renderAll();
      if (run.status === "RUNNING") {
        schedulePoll();
      }
    } catch {
      blockProof(
        "The frozen agreement and run state could not be cross-validated. Proof actions remain disabled."
      );
    }
  }

  acknowledgement.addEventListener("change", renderAction);
  runButton.addEventListener("click", () => {
    void startProof();
  });
  window.addEventListener("pagehide", stopPolling);
  void loadProof();
})();
