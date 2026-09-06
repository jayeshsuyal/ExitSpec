(() => {
  "use strict";
  const MODE = "SYNTHETIC_NO_NETWORK";
  const HEADER = "X-ExitSpec-Authoring-Capability";
  const HEX = /^[a-f0-9]{64}$/;
  const RECEIPT = /^srcpt_[a-z0-9][a-z0-9_-]{7,95}$/;
  const KINDS = {MEETING: "Meeting text", EMAIL: "Email", DOCUMENT: "Document", EXISTING_CONTRACT: "Existing contract"};
  const STATES = new Set(["PREPARED", "AUTHORIZED", "CLAIMED", "DISPATCH_AUTHORIZED", "SUCCEEDED", "FAILED", "OUTCOME_UNKNOWN", "STALE", "EXPIRED", "REVOKED"]);
  const TERMINAL = new Set(["SUCCEEDED", "FAILED", "OUTCOME_UNKNOWN", "STALE", "EXPIRED", "REVOKED"]);
  const match = !location.search && !location.hash && location.pathname.match(/^\/app\/pocs\/(poc_[a-z0-9][a-z0-9_-]{2,63})\/source-authoring$/);
  const poc = match ? match[1] : null;
  const endpoint = poc ? `/api/pocs/${poc}/source-authoring/` : null;
  const $ = (id) => document.getElementById(id);
  const choice = $("source-choice"), business = $("source-business-text"), acknowledged = $("source-acknowledged");
  let capability = null, operation = null, state = null, processing = false;
  let busy = false, ready = false, serial = 0, timer = null, displayed = null, key = null;
  const pending = new Set();

  function sameKeys(value, keys) {
    return value !== null && typeof value === "object" && !Array.isArray(value) &&
      Object.keys(value).sort().join("|") === keys.slice().sort().join("|");
  }
  const integer = (value, max) => Number.isInteger(value) && value >= 0 && value <= max;
  function controls() {
    const active = operation && !TERMINAL.has(state);
    choice.disabled = !ready || busy || active;
    $("source-preview").disabled = !ready || busy || active || !RECEIPT.test(choice.value);
    $("source-refresh").disabled = !ready || busy || active;
    business.disabled = !ready || busy || state !== "PREPARED";
    acknowledged.disabled = !ready || busy || state !== "PREPARED";
    $("source-authorize").disabled = !ready || busy || state !== "PREPARED" || !business.checked || !acknowledged.checked || displayed !== operation;
    $("source-run").disabled = !ready || busy || processing || state !== "AUTHORIZED";
    // A pending Run HTTP response must not disable the separate cancellation.
    $("source-cancel").disabled = !capability || !active;
    $("source-task").setAttribute("aria-busy", String(busy));
  }
  function clearSource() {
    displayed = null;
    business.checked = acknowledged.checked = false;
    $("source-redacted-text").textContent = "";
    $("source-disclosure").hidden = true;
    $("source-empty").hidden = false;
  }
  function failure(code) {
    const messages = {
      session_capacity: "This runtime has reached its 16-page session limit. No new session was issued.",
      CAPABILITY_REFUSED: "This page session is unavailable. Reload to inspect the current source and acknowledge again.",
      ORIGIN_REFUSED: "The local request origin was refused. No action was authorized.",
      SOURCE_UNAVAILABLE: "The source, draft, review or closure state no longer permits this action.",
      CONSENT_REFUSED: "Consent is no longer current. Inspect the source again before a new explicit action.",
      WORKER_BUSY: "Another operation occupies this runtime's only worker. Wait for it to finish.",
      rate_limited: "The ten-second claim interval has not elapsed. Run again explicitly when ready.",
      budget_exhausted: "This runtime has consumed all ten synthetic claims. No further claim is available.",
      grant_closed: "This runtime is closed. No operation can start.",
    };
    $("source-authoring-error").textContent = messages[code] || "The response could not be trusted or the request was refused. No automatic retry will run. Refresh current state before continuing.";
    $("source-authoring-error").hidden = false;
  }
  async function api(action, payload = {}) {
    if (!endpoint || (action !== "bootstrap" && !capability)) throw new Error("CAPABILITY_REFUSED");
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 6000);
    pending.add(controller);
    try {
      const headers = {"Content-Type": "application/json"};
      if (action !== "bootstrap") headers[HEADER] = capability;
      const response = await fetch(endpoint + action, {method: "POST", headers,
        body: JSON.stringify(payload), cache: "no-store", credentials: "omit",
        redirect: "error", signal: controller.signal});
      if (!(response.headers.get("Content-Type") || "").startsWith("application/json") ||
          response.headers.get("Cache-Control") !== "no-store" || !response.body) throw new Error("UNTRUSTED_RESPONSE");
      const reader = response.body.getReader(), chunks = [];
      let size = 0;
      while (true) {
        const next = await reader.read();
        if (next.done) break;
        size += next.value.byteLength;
        if (size > 262144) { await reader.cancel(); throw new Error("RESPONSE_LIMIT"); }
        chunks.push(next.value);
      }
      const bytes = new Uint8Array(size);
      let offset = 0;
      for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length; }
      const value = JSON.parse(new TextDecoder("utf-8", {fatal: true}).decode(bytes));
      if (!response.ok) throw new Error(typeof value.code === "string" ? value.code : "REQUEST_REFUSED");
      return value;
    } finally { clearTimeout(timeout); pending.delete(controller); }
  }
  function trustedOperation(value) {
    const keys = ["mode", "poc_id", "operation_id", "state", "processing", "attempts", "reserved_usd", "grant_claims", "grant_reserved_usd", "expires_in_seconds", "code", "authoring_receipt_id"];
    if (value && Object.hasOwn(value, "disclosure")) keys.push("disclosure");
    return sameKeys(value, keys) && value.mode === MODE && value.poc_id === poc && HEX.test(value.operation_id) &&
      STATES.has(value.state) && typeof value.processing === "boolean" && integer(value.attempts, 1) && integer(value.grant_claims, 10) &&
      ["0.00", "0.01"].includes(value.reserved_usd) && /^0\.(?:0[0-9]|10)$/.test(value.grant_reserved_usd) &&
      integer(value.expires_in_seconds, 300) && (value.code === null || /^[A-Za-z_]{1,60}$/.test(value.code)) &&
      (value.authoring_receipt_id === null || /^arcp_[a-f0-9]{32}$/.test(value.authoring_receipt_id));
  }
  async function showDisclosure(value) {
    const d = value.disclosure;
    const keys = ["disclosure_sha256", "source_receipt_id", "source_kind", "source_revision", "source_sha256", "redacted_text", "classification", "provider", "model", "purpose", "custody", "limits"];
    const expectedLimits = {source_bytes: 16384, body_bytes: 65536, response_bytes: 262144,
      output_tokens: 2000, deadline_seconds: 30, consent_seconds: 300, attempts: 1,
      claim_interval_seconds: 10, grant_claims: 10, reservation_usd: "0.01", grant_reservation_usd: "0.10"};
    if (!sameKeys(d, keys) || !HEX.test(d.disclosure_sha256) || !HEX.test(d.source_sha256) ||
        !RECEIPT.test(d.source_receipt_id) || d.source_receipt_id !== choice.value || !Object.hasOwn(KINDS, d.source_kind) ||
        !integer(d.source_revision, 1000000) || d.source_revision < 1 || typeof d.redacted_text !== "string" ||
        d.classification !== "OWNER_APPROVED_REDACTED_BUSINESS_TEXT" || d.provider !== "fireworks" ||
        d.model !== "accounts/fireworks/models/deepseek-v4-flash-0731" ||
        typeof d.purpose !== "string" || d.purpose.length > 300 || typeof d.custody !== "string" || d.custody.length > 1000 ||
        !sameKeys(d.limits, Object.keys(expectedLimits)) || Object.entries(expectedLimits).some(([k, v]) => d.limits[k] !== v)) throw new Error("UNTRUSTED_RESPONSE");
    const text = new TextEncoder().encode(d.redacted_text);
    if (!text.length || text.length > 16384) throw new Error("RESPONSE_LIMIT");
    const digest = Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", text))).map((n) => n.toString(16).padStart(2, "0")).join("");
    if (digest !== d.source_sha256) throw new Error("UNTRUSTED_RESPONSE");
    displayed = value.operation_id;
    $("source-description").textContent = `${KINDS[d.source_kind]} · revision ${d.source_revision} · ${d.source_receipt_id}`;
    $("source-redacted-text").textContent = d.redacted_text;
    $("source-provider").textContent = `Fireworks · ${d.model} (candidate only; not contacted)`;
    $("source-purpose").textContent = d.purpose;
    $("source-custody").textContent = d.custody;
    $("source-disclosure").hidden = false;
    $("source-empty").hidden = true;
  }
  function render(value) {
    if (!trustedOperation(value) || value.operation_id !== operation) throw new Error("UNTRUSTED_RESPONSE");
    if (TERMINAL.has(state) && value.state !== state) return;
    state = value.state; processing = value.processing;
    $("source-expiry").textContent = `${value.expires_in_seconds} seconds remaining. Acknowledgment does not extend expiry.`;
    $("source-ledger").textContent = `${value.grant_claims} of 10 synthetic claims used · $${value.grant_reserved_usd} reserved locally · no provider spend.`;
    const labels = {PREPARED: "Inspect the exact text, then attest and acknowledge.", AUTHORIZED: "Acknowledged. Choose Run to start one synthetic attempt.", CLAIMED: "Processing locally. One synthetic attempt has been consumed.", DISPATCH_AUTHORIZED: "The synthetic worker is processing. You can still cancel publication.", SUCCEEDED: "Validated proposals are ready for human review. They remain NEEDS_REVIEW.", FAILED: "The attempt failed safely. No proposals were published and its consumed claim is retained.", OUTCOME_UNKNOWN: "The attempt did not finish within its bound. No new proposal is available from this operation.", STALE: "The source, draft, review or closure state changed. Inspect current source state before continuing.", EXPIRED: "This disclosure expired. Inspect the source and acknowledge a new disclosure.", REVOKED: "Consent revoked. This operation cannot run again."};
    $("source-status").textContent = value.processing && state === "AUTHORIZED" ? "Starting the bounded synthetic worker…" : labels[state];
    if (value.code && state === "AUTHORIZED") failure(value.code);
    $("source-review-result").hidden = state !== "SUCCEEDED";
    if (TERMINAL.has(state)) { clearSource(); clearTimeout(timer); }
    controls();
  }
  function poll() {
    clearTimeout(timer);
    if (!operation || TERMINAL.has(state) || !ready) return;
    const current = operation, epoch = serial;
    timer = setTimeout(async () => {
      try {
        const value = await api("status", {operation_id: current});
        if (epoch === serial && operation === current) render(value);
      } catch (error) {
        if (epoch === serial) {
          failure(error.message); ready = false; clearSource();
          $("source-status").textContent = "Current consent could not be verified. Reload before continuing.";
          controls();
        }
      }
      if (epoch === serial) poll();
    }, 750);
  }
  async function sources() {
    const value = await api("sources");
    if (!sameKeys(value, ["mode", "poc_id", "sources"]) || value.mode !== MODE || value.poc_id !== poc ||
        !Array.isArray(value.sources) || value.sources.length > 256 || value.sources.some((s) =>
          !sameKeys(s, ["source_receipt_id", "source_kind", "eligible"]) || !RECEIPT.test(s.source_receipt_id) ||
          !Object.hasOwn(KINDS, s.source_kind) || typeof s.eligible !== "boolean")) throw new Error("UNTRUSTED_RESPONSE");
    choice.replaceChildren(new Option("Select one current source", ""));
    for (const source of value.sources) {
      const option = new Option(`${KINDS[source.source_kind]} · ${source.source_receipt_id}${source.eligible ? "" : " · unavailable for authoring"}`, source.source_receipt_id);
      option.disabled = !source.eligible; choice.append(option);
    }
    if (!value.sources.some((s) => s.eligible)) $("source-status").textContent = "No current source is eligible. Capture new source text or return to human review.";
  }
  async function action(task) {
    if (busy || !ready) return;
    busy = true; $("source-authoring-error").hidden = true; controls();
    const epoch = serial;
    try { await task(epoch); }
    catch (error) { if (epoch === serial) failure(error.message); }
    finally { if (epoch === serial) { busy = false; controls(); poll(); } }
  }
  async function initialise() {
    const epoch = ++serial;
    clearTimeout(timer); capability = operation = state = key = null;
    ready = false; busy = true; processing = false; clearSource(); controls();
    $("source-review-result").hidden = true;
    if (!poc) { failure("REQUEST_REFUSED"); return; }
    $("back-to-review").href = $("source-review-result").href = `/app/pocs/${poc}/review`;
    try {
      const value = await api("bootstrap");
      if (epoch !== serial) return;
      if (!sameKeys(value, ["schema_version", "capability", "mode", "poc_id", "display_name", "live_enabled", "live_missing"]) ||
          value.schema_version !== "exitspec.source-authoring-web/1" || !HEX.test(value.capability) || value.mode !== MODE ||
          value.poc_id !== poc || value.live_enabled !== false || typeof value.display_name !== "string" || value.display_name.length > 200 ||
          !Array.isArray(value.live_missing) || value.live_missing.length !== 4) throw new Error("UNTRUSTED_RESPONSE");
      capability = value.capability;
      $("source-poc-title").textContent = value.display_name;
      $("source-status").textContent = "Select and inspect one current source. Nothing runs on page load.";
      await sources();
      if (epoch === serial) ready = true;
    } catch (error) { if (epoch === serial) failure(error.message); }
    finally { if (epoch === serial) { busy = false; controls(); } }
  }
  choice.addEventListener("change", controls);
  business.addEventListener("change", controls); acknowledged.addEventListener("change", controls);
  $("source-refresh").addEventListener("click", () => action(async () => {
    operation = state = key = null; clearSource(); await sources();
  }));
  $("source-preview").addEventListener("click", () => action(async (epoch) => {
    clearSource(); key = crypto.randomUUID();
    const value = await api("prepare", {source_receipt_id: choice.value});
    if (epoch !== serial) return;
    if (!trustedOperation(value) || value.state !== "PREPARED") throw new Error("CONSENT_REFUSED");
    operation = value.operation_id;
    state = null;
    await showDisclosure(value);
    if (epoch !== serial) return;
    render(value); $("source-redacted-text").focus();
  }));
  $("source-authorize").addEventListener("click", () => action(async (epoch) => {
    if (!business.checked || !acknowledged.checked || displayed !== operation) return;
    const value = await api("authorize", {operation_id: operation, business_text: true, acknowledged: true, idempotency_key: key});
    if (epoch === serial) render(value);
  }));
  $("source-run").addEventListener("click", () => action(async (epoch) => {
    const value = await api("run", {operation_id: operation});
    if (epoch === serial) render(value);
  }));
  $("source-cancel").addEventListener("click", async () => {
    const epoch = serial, current = operation;
    try {
      const value = await api("revoke", {operation_id: current});
      if (epoch === serial && current === operation) render(value);
    } catch (error) { if (epoch === serial) failure(error.message); }
  });
  window.addEventListener("pagehide", () => {
    if (capability && operation && !TERMINAL.has(state)) {
      fetch(endpoint + "revoke", {method: "POST", headers: {"Content-Type": "application/json", [HEADER]: capability},
        body: JSON.stringify({operation_id: operation}), keepalive: true, cache: "no-store", credentials: "omit", redirect: "error"}).catch(() => {});
    }
    ++serial; clearTimeout(timer); for (const controller of pending) controller.abort();
    capability = operation = state = key = null; ready = false; busy = false; clearSource(); controls();
  });
  window.addEventListener("pageshow", (event) => { if (event.persisted) initialise(); });
  initialise();
})();
