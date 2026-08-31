(() => {
  "use strict";

  const match = window.location.pathname.match(
    /^\/app\/pocs\/(poc_[a-z0-9][a-z0-9_-]{2,63})\/evidence$/,
  );
  const pocId = match ? match[1] : null;
  const api = pocId ? `/api/pocs/${pocId}/evidence` : null;
  const $ = (selector) => document.querySelector(selector);
  const POC_ID_PATTERN = /^poc_[a-z0-9][a-z0-9_-]{2,63}$/;
  const ATTEMPT_ID_PATTERN = /^eatm_[a-f0-9]{32}$/;
  const OPERATION_ID_PATTERN = /^prun_[a-f0-9]{32}$/;
  const RUN_ID_PATTERN = /^run_[a-f0-9]{32}$/;
  const CLOSURE_ID_PATTERN = /^poccl_[a-f0-9]{32}$/;
  const HASH_PATTERN = /^[a-f0-9]{64}$/;
  const STATUSES = new Set([
    "RESERVED",
    "RUNNING",
    "COMPLETED",
    "INGESTION_REJECTED",
    "FAILED_INTERNAL",
    "CANCELLED",
    "STALE",
  ]);
  const VERDICTS = new Set(["PASS", "FAIL", "BLOCKED", "NOT_PROVEN"]);
  let snapshot = null;
  let busy = false;
  let trustedSnapshot = false;

  function plainObject(value) {
    return Boolean(value && typeof value === "object" && !Array.isArray(value));
  }

  function exactKeys(value, keys) {
    return plainObject(value) &&
      Object.keys(value).sort().join("|") === [...keys].sort().join("|");
  }

  function safeText(value, maximum = 4000) {
    return typeof value === "string" && value.trim().length > 0 && value.length <= maximum;
  }

  function timestamp(value) {
    return typeof value === "string" &&
      value.length <= 64 &&
      /(?:Z|[+-]\d{2}:\d{2})$/.test(value) &&
      Number.isFinite(Date.parse(value));
  }

  function canonicalJson(value) {
    if (value === null || typeof value === "boolean" || typeof value === "string") {
      return JSON.stringify(value);
    }
    if (typeof value === "number" && Number.isFinite(value)) return JSON.stringify(value);
    if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
    if (plainObject(value)) {
      return `{${Object.keys(value).sort().map((key) =>
        `${JSON.stringify(key)}:${canonicalJson(value[key])}`
      ).join(",")}}`;
    }
    throw new Error("Evidence response could not be trusted.");
  }

  async function sha256Hex(domain, value) {
    const canonical = canonicalJson(value);
    const bytes = new TextEncoder().encode(
      domain === null ? canonical : `${domain}\u0000${canonical}`,
    );
    const digest = await window.crypto.subtle.digest("SHA-256", bytes);
    return [...new Uint8Array(digest)]
      .map((part) => part.toString(16).padStart(2, "0"))
      .join("");
  }

  function deepEqual(left, right) {
    if (Object.is(left, right)) return true;
    if (Array.isArray(left) || Array.isArray(right)) {
      return Array.isArray(left) &&
        Array.isArray(right) &&
        left.length === right.length &&
        left.every((item, index) => deepEqual(item, right[index]));
    }
    if (!plainObject(left) || !plainObject(right)) return false;
    const leftKeys = Object.keys(left).sort();
    const rightKeys = Object.keys(right).sort();
    return leftKeys.length === rightKeys.length &&
      leftKeys.every((key, index) =>
        key === rightKeys[index] && deepEqual(left[key], right[key])
      );
  }

  function trustedPackPath(value, attemptId) {
    if (value === null) return null;
    if (typeof value !== "string") throw new Error("Evidence response could not be trusted.");
    const match = value.match(/^\/artifacts\/(eatm_[a-f0-9]{32})\/decision-packet\.html$/);
    let parsed;
    try {
      parsed = new URL(value, window.location.origin);
    } catch {
      throw new Error("Evidence response could not be trusted.");
    }
    if (
      !match ||
      match[1] !== attemptId ||
      parsed.origin !== window.location.origin ||
      parsed.pathname !== value ||
      parsed.search ||
      parsed.hash
    ) {
      throw new Error("Evidence response could not be trusted.");
    }
    return value;
  }

  function validateAttempt(value, expectedPocId) {
    if (
      !plainObject(value) ||
      !ATTEMPT_ID_PATTERN.test(value.attempt_id) ||
      !OPERATION_ID_PATTERN.test(value.operation_id) ||
      !RUN_ID_PATTERN.test(value.run_id) ||
      value.poc_id !== expectedPocId ||
      !STATUSES.has(value.status) ||
      typeof value.is_current !== "boolean" ||
      !safeText(value.contract_id, 160) ||
      !safeText(value.contract_version, 100) ||
      !HASH_PATTERN.test(value.contract_hash) ||
      !HASH_PATTERN.test(value.request_digest) ||
      !timestamp(value.reserved_at) ||
      !(value.terminal_at === null || timestamp(value.terminal_at)) ||
      !safeText(value.reason) ||
      !safeText(value.next_action, 2000) ||
      value.shipping_authorized !== false ||
      !(
        value.evidence_pack_sha256 === null ||
        (typeof value.evidence_pack_sha256 === "string" &&
          HASH_PATTERN.test(value.evidence_pack_sha256))
      ) ||
      (value.evidence_pack_url === null) !== (value.evidence_pack_sha256 === null) ||
      !Array.isArray(value.results)
    ) {
      throw new Error("Evidence response could not be trusted.");
    }
    for (const result of value.results) {
      if (
        !plainObject(result) ||
        !["ADMITTED", "INGESTION_REJECTED"].includes(result.ingestion_status) ||
        !(result.verdict === null || VERDICTS.has(result.verdict)) ||
        !Array.isArray(result.limitations) ||
        !result.limitations.every((item) => safeText(item, 2000)) ||
        (result.ingestion_status === "INGESTION_REJECTED" && result.verdict !== null)
      ) {
        throw new Error("Evidence response could not be trusted.");
      }
    }
    if (value.reduction !== null) {
      if (
        !plainObject(value.reduction) ||
        !VERDICTS.has(value.reduction.verdict) ||
        !Array.isArray(value.reduction.limitations) ||
        !value.reduction.limitations.every((item) => safeText(item, 2000))
      ) {
        throw new Error("Evidence response could not be trusted.");
      }
    }
    if (value.status === "INGESTION_REJECTED" && value.reduction !== null) {
      throw new Error("Evidence response could not be trusted.");
    }
    trustedPackPath(value.evidence_pack_url, value.attempt_id);
    return value;
  }

  function validateEvidenceBinding(value, expectedPocId, current) {
    if (
      !exactKeys(value, [
        "poc_id",
        "contract_id",
        "contract_version",
        "contract_hash",
        "run_id",
        "verdict",
        "evidence_pack_url",
        "evidence_pack_sha256",
      ]) ||
      !current ||
      current.status !== "COMPLETED" ||
      current.poc_id !== expectedPocId ||
      value.poc_id !== expectedPocId ||
      value.contract_id !== current.contract_id ||
      value.contract_version !== current.contract_version ||
      value.contract_hash !== current.contract_hash ||
      value.run_id !== current.run_id ||
      value.verdict !== current.reduction?.verdict ||
      value.evidence_pack_url !== current.evidence_pack_url ||
      value.evidence_pack_sha256 !== current.evidence_pack_sha256 ||
      !HASH_PATTERN.test(value.evidence_pack_sha256)
    ) {
      throw new Error("Evidence response could not be trusted.");
    }
    trustedPackPath(value.evidence_pack_url, current.attempt_id);
    return value;
  }

  async function validateTerminalRunBinding(value, expectedPocId, current) {
    if (
      !exactKeys(value, [
        "poc_id",
        "contract_id",
        "contract_version",
        "contract_hash",
        "operation_id",
        "runner_run_id",
        "runner_input_digest",
        "run_status",
        "reason_code",
        "terminal_at",
        "run_receipt_sha256",
      ]) ||
      !current ||
      !["INGESTION_REJECTED", "FAILED_INTERNAL", "CANCELLED"].includes(current.status) ||
      current.evidence_pack_url !== null ||
      current.evidence_pack_sha256 !== null ||
      value.poc_id !== expectedPocId ||
      value.contract_id !== current.contract_id ||
      value.contract_version !== current.contract_version ||
      value.contract_hash !== current.contract_hash ||
      value.operation_id !== current.operation_id ||
      value.runner_run_id !== current.run_id ||
      value.runner_input_digest !== current.request_digest ||
      value.run_status !== "BLOCKED" ||
      value.reason_code !== current.status ||
      value.terminal_at !== (current.terminal_at || current.reserved_at) ||
      !timestamp(value.terminal_at) ||
      !HASH_PATTERN.test(value.run_receipt_sha256)
    ) {
      throw new Error("Evidence response could not be trusted.");
    }
    const receiptDigest = await sha256Hex(null, current);
    if (value.run_receipt_sha256 !== receiptDigest) {
      throw new Error("Evidence response could not be trusted.");
    }
    return value;
  }

  async function validateClosure(value, expectedPocId, current) {
    if (value === null) return null;
    if (
      !exactKeys(value, [
        "closure_id",
        "poc_id",
        "decision",
        "decided_by",
        "rationale",
        "recorded_at",
        "evidence_binding",
        "terminal_run_binding",
        "evidence_binding_sha256",
        "authorization_scope",
        "shipping_authorized",
      ]) ||
      !CLOSURE_ID_PATTERN.test(value.closure_id) ||
      value.poc_id !== expectedPocId ||
      !["HANDOFF_COMPLETED", "POC_STOPPED"].includes(value.decision) ||
      !safeText(value.decided_by, 160) ||
      !safeText(value.rationale, 2000) ||
      !timestamp(value.recorded_at) ||
      !HASH_PATTERN.test(value.evidence_binding_sha256) ||
      value.authorization_scope !== "POC_LIFECYCLE_ONLY" ||
      value.shipping_authorized !== false
    ) {
      throw new Error("Evidence response could not be trusted.");
    }
    const hasEvidenceBinding = value.evidence_binding !== null;
    const hasTerminalRunBinding = value.terminal_run_binding !== null;
    if (hasEvidenceBinding === hasTerminalRunBinding || !current?.is_current) {
      throw new Error("Evidence response could not be trusted.");
    }
    let binding;
    let domain;
    if (hasEvidenceBinding) {
      binding = validateEvidenceBinding(value.evidence_binding, expectedPocId, current);
      domain = "exitspec-terminal-evidence-binding-v1";
    } else {
      if (value.decision === "HANDOFF_COMPLETED") {
        throw new Error("Evidence response could not be trusted.");
      }
      binding = await validateTerminalRunBinding(
        value.terminal_run_binding,
        expectedPocId,
        current,
      );
      domain = "exitspec-terminal-run-binding-v1";
    }
    if (value.evidence_binding_sha256 !== await sha256Hex(domain, binding)) {
      throw new Error("Evidence response could not be trusted.");
    }
    return value;
  }

  async function validateSnapshot(value) {
    if (
      !exactKeys(value, [
        "poc_id",
        "current",
        "history",
        "closure",
        "shipping_authorized",
        "authorization",
      ]) ||
      value.poc_id !== pocId ||
      !POC_ID_PATTERN.test(value.poc_id) ||
      value.shipping_authorized !== false ||
      !safeText(value.authorization, 2000) ||
      !Array.isArray(value.history) ||
      value.history.length > 2048
    ) {
      throw new Error("Evidence response could not be trusted.");
    }
    const history = value.history.map((attempt) => validateAttempt(attempt, pocId));
    const identities = history.map((attempt) => attempt.attempt_id);
    if (new Set(identities).size !== identities.length) {
      throw new Error("Evidence response could not be trusted.");
    }
    const markedCurrent = history.filter((attempt) => attempt.is_current);
    let current = null;
    if (value.current === null) {
      if (markedCurrent.length !== 0) throw new Error("Evidence response could not be trusted.");
    } else {
      current = validateAttempt(value.current, pocId);
      const historicalCurrent = history.find(
        (attempt) => attempt.attempt_id === current.attempt_id
      );
      if (
        markedCurrent.length !== 1 ||
        !current.is_current ||
        !historicalCurrent ||
        !historicalCurrent.is_current ||
        !deepEqual(historicalCurrent, current)
      ) {
        throw new Error("Evidence response could not be trusted.");
      }
    }
    await validateClosure(value.closure, pocId, current);
    return value;
  }

  function validateStartResponse(value) {
    if (
      !exactKeys(value, ["poc_id", "replayed", "attempt"]) ||
      value.poc_id !== pocId ||
      typeof value.replayed !== "boolean"
    ) {
      throw new Error("Evidence response could not be trusted.");
    }
    validateAttempt(value.attempt, pocId);
    return value;
  }

  async function validateDecisionResponse(value, current) {
    if (!exactKeys(value, ["poc_id", "closure"]) || value.poc_id !== pocId) {
      throw new Error("Evidence response could not be trusted.");
    }
    await validateClosure(value.closure, pocId, current);
    return value;
  }

  function visibleLimitations(current) {
    const values = [];
    for (const result of current?.results || []) {
      for (const limitation of result.limitations || []) values.push(limitation);
    }
    for (const limitation of current?.reduction?.limitations || []) values.push(limitation);
    return [...new Set(values)];
  }

  function setError(message) {
    const error = $("#evidence-error");
    error.textContent = message;
    error.hidden = !message;
  }

  function render() {
    const current = trustedSnapshot && snapshot ? snapshot.current : null;
    $("#generic-main").setAttribute("aria-busy", busy ? "true" : "false");
    $("#evidence-title").textContent = current
      ? `${current.contract_id} · v${current.contract_version}`
      : "Current evidence";
    $("#evidence-subtitle").textContent = current
      ? "The exact customer-confirmed contract is frozen."
      : "Evidence begins only after the server confirms a frozen agreement.";
    $("#evidence-current-status").textContent = current ? current.status : "NOT STARTED";
    $("#evidence-current-verdict").textContent = current?.reduction?.verdict || "—";
    $("#evidence-result-verdict").textContent = current?.reduction?.verdict || "NOT RUN";
    $("#evidence-result-reason").textContent = current
      ? current.reason
      : "No admitted terminal evidence yet.";
    $("#evidence-guidance").textContent = current
      ? current.next_action
      : "ExitSpec selects the method from the frozen agreement.";
    $("#evidence-authorization").textContent = trustedSnapshot && snapshot
      ? snapshot.authorization
      : "Evidence is proof, not shipping authorization.";
    $("#evidence-technical-binding").textContent = current
      ? `Contract hash ${current.contract_hash} · attempt ${current.attempt_id}`
      : "No current contract or attempt binding.";
    const limitations = visibleLimitations(current);
    $("#evidence-limitation").textContent = limitations[0] || "No admitted evidence limitation is available yet.";
    $("#evidence-next-action").textContent = current?.next_action || "Acknowledge the exact frozen evidence request.";

    const pack = current?.evidence_pack_url;
    const packLink = $("#evidence-pack-link");
    packLink.hidden = !pack;
    if (pack) packLink.setAttribute("href", pack);
    else packLink.removeAttribute("href");
    const canStop = Boolean(current && ["COMPLETED", "INGESTION_REJECTED", "FAILED_INTERNAL", "CANCELLED"].includes(current.status));
    const closed = Boolean(trustedSnapshot && snapshot?.closure);
    const start = $("#start-evidence");
    const stop = $("#stop-evidence");
    const handoff = $("#handoff-evidence");
    const handoffFields = $("#handoff-fields");
    const acknowledgement = $("#evidence-acknowledgement");
    const acknowledgementInput = $("#evidence-acknowledged");
    const owner = $("#decision-owner").value.trim();
    const rationale = $("#decision-rationale").value.trim();
    const hasCurrentPack = Boolean(pack && current?.is_current);
    const deciding = hasCurrentPack && !closed;
    stop.hidden = !canStop || closed;
    handoff.hidden = !deciding;
    handoffFields.hidden = closed || (!canStop && handoff.hidden);
    acknowledgement.hidden = hasCurrentPack;
    acknowledgementInput.disabled = hasCurrentPack;
    start.hidden = hasCurrentPack;
    start.disabled = busy || !trustedSnapshot || closed || deciding || !acknowledgementInput.checked || Boolean(current && ["RESERVED", "RUNNING"].includes(current.status));
    stop.disabled = busy || !owner;
    handoff.disabled = busy || !owner || !rationale;
    $("#evidence-task-kicker").textContent = closed && hasCurrentPack
      ? "Decision recorded"
      : deciding ? "Current task · Decide" : "Current task · Prove";
    $("#evidence-task-heading").textContent = closed && hasCurrentPack
      ? "Human decision recorded"
      : deciding ? "Review the current pack and record the human decision" : "Run the approved evidence method";

    const history = $("#evidence-history");
    history.replaceChildren();
    for (const [index, attempt] of (trustedSnapshot ? snapshot?.history || [] : []).entries()) {
      const item = document.createElement("li");
      const verdict = attempt.reduction?.verdict || "NO VERDICT";
      item.textContent = `Attempt ${index + 1} · ${attempt.status} · ${verdict} · ${attempt.is_current ? "CURRENT" : "HISTORICAL"}`;
      history.append(item);
    }
    if (!history.children.length) {
      const item = document.createElement("li");
      item.textContent = "No attempts yet.";
      history.append(item);
    }
  }

  async function request(path, options = {}) {
    const response = await fetch(path, {
      ...options,
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    });
    const body = await response.json();
    if (!response.ok) throw new Error(body.error || "Evidence request failed.");
    return body;
  }

  async function refresh() {
    if (!api) throw new Error("Evidence route is invalid.");
    busy = true;
    // Keep the last trusted projection visible while the replacement is
    // fetched and validated. Controls are busy-disabled until the swap, so a
    // failed refresh cannot turn a transient untrusted state into UI state.
    render();
    try {
      const candidate = await request(api, { headers: {} });
      snapshot = await validateSnapshot(candidate);
      trustedSnapshot = true;
    } finally {
      busy = false;
      render();
    }
  }

  async function start() {
    const current = snapshot?.current;
    if (busy || !trustedSnapshot || snapshot?.closure || (current?.evidence_pack_url && current.is_current)) return;
    setError("");
    busy = true;
    render();
    try {
      const body = validateStartResponse(await request(api, {
        method: "POST",
        body: JSON.stringify({
          acknowledgement: true,
          idempotency_key: `a6-browser-${crypto.randomUUID()}`,
        }),
      }));
      if (!body.attempt.is_current) throw new Error("Evidence response could not be trusted.");
      await refresh();
    } catch (error) { setError(error.message); }
    finally {
      busy = false;
      render();
    }
  }

  async function close(decision) {
    const current = snapshot?.current;
    if (!current || busy || !trustedSnapshot || snapshot?.closure) return;
    const owner = $("#decision-owner").value.trim();
    const rationale = $("#decision-rationale").value.trim();
    if (!owner || (decision === "handoff" && !rationale)) {
      setError(decision === "handoff"
        ? "Name the decision owner and record the handoff rationale."
        : "Name the decision owner before stopping the POC.");
      render();
      return;
    }
    busy = true;
    render();
    try {
      await validateDecisionResponse(await request(`${api}/${current.attempt_id}/${decision}`, {
        method: "POST",
        body: JSON.stringify({
          decided_by: owner,
          rationale: decision === "handoff" ? rationale : "Stopped after reviewing the current evidence state.",
          idempotency_key: `a6-browser-${decision}-${crypto.randomUUID()}`,
        }),
      }), current);
      await refresh();
    } catch (error) { setError(error.message); }
    finally {
      busy = false;
      render();
    }
  }

  $("#evidence-acknowledged").addEventListener("change", render);
  $("#decision-owner").addEventListener("input", render);
  $("#decision-rationale").addEventListener("input", render);
  $("#start-evidence").addEventListener("click", start);
  $("#handoff-evidence").addEventListener("click", () => close("handoff"));
  $("#stop-evidence").addEventListener("click", () => close("stop"));
  refresh().catch((error) => setError(error.message));
})();
