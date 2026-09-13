(() => {
  "use strict";
  const MODE = "SYNTHETIC_NO_NETWORK";
  const MODES = new Set([MODE, "QUALIFIED_FIREWORKS", "OFFLINE_FAKE_FIREWORKS", "DEMO_FIREWORKS"]);
  let runtimeMode = null;
  const isSynthetic = () => runtimeMode === MODE;
  const isOffline = () => runtimeMode === "OFFLINE_FAKE_FIREWORKS";
  const isDemo = () => runtimeMode === "DEMO_FIREWORKS";
  const claimLimit = () => isDemo() ? 1 : 10;
  let demoConsumed = false;
  const HEADER = "X-ExitSpec-Authoring-Capability";
  const HEX = /^[a-f0-9]{64}$/;
  const RECEIPT = /^srcpt_[a-z0-9][a-z0-9_-]{7,95}$/;
  const KINDS = {MEETING: "Meeting text", EMAIL: "Email", DOCUMENT: "Document", EXISTING_CONTRACT: "Existing contract"};
  const STATES = new Set(["PREPARED", "AUTHORIZED", "CLAIMED", "DISPATCH_AUTHORIZED", "SUCCEEDED", "FAILED", "OUTCOME_UNKNOWN", "STALE", "EXPIRED", "REVOKED"]);
  const TERMINAL = new Set(["SUCCEEDED", "FAILED", "OUTCOME_UNKNOWN", "STALE", "EXPIRED", "REVOKED"]);
  const LIVE_MISSING = Object.freeze({
    live_worker_and_operator_launcher: "An admitted live source-authoring installation is unavailable.",
    owner_launch_approval: "Live use requires separate owner launch approval.",
    model_schema_token_and_billing_proof: "Live use requires exact model and schema acceptance, token accounting and billing bounds.",
    account_pricing_and_custody_approval: "Live use requires approved account, pricing and data handling conditions. The candidate global profile has no regional guarantee.",
  });
  const match = !location.search && !location.hash && location.pathname.match(/^\/app\/pocs\/(poc_[a-z0-9][a-z0-9_-]{2,63})\/source-authoring$/);
  const poc = match ? match[1] : null;
  const endpoint = poc ? `/api/pocs/${poc}/source-authoring/` : null;
  const $ = (id) => document.getElementById(id);
  const choice = $("source-choice"), business = $("source-business-text"), acknowledged = $("source-acknowledged");
  let capability = null, operation = null, state = null, processing = false;
  let busy = false, ready = false, serial = 0, timer = null, displayed = null, key = null;
  let unavailable = false;
  const pending = new Set();
  const isCurrent = (epoch, current) => epoch === serial && current === operation;

  function sameKeys(value, keys) {
    return value !== null && typeof value === "object" && !Array.isArray(value) &&
      Object.keys(value).sort().join("|") === keys.slice().sort().join("|");
  }
  const integer = (value, max) => Number.isInteger(value) && value >= 0 && value <= max;
  function setText(id, text) {
    const element = $(id);
    if (element.textContent !== text) element.textContent = text;
  }
  function showMode(mode) {
    setText("mode-heading", mode === "verified" ? "Synthetic only · no provider connection" :
      mode === "unavailable" ? "Authoring mode unavailable" : "Checking authoring mode…");
    setText("source-mode-copy", mode === "verified" ?
      "This local run sends existing source requirements through the bounded validation worker. It makes no external inference call and spends no provider credits." :
      mode === "unavailable" ? "The current page could not be validated. Reload before continuing." :
      "Controls stay unavailable until this page validates its local session.");
    setText("source-live-missing", mode === "verified" && isSynthetic() ? Object.values(LIVE_MISSING).join(" ") : "");
    if (mode === "verified" && !isSynthetic()) {
      setText("mode-heading", isOffline() ? "Offline fake Fireworks transport" : "Fireworks · admitted operator launch");
      setText("source-mode-copy", isOffline() ?
        "This integration rehearsal uses fake credentials and local fake transport. It makes no provider call or spend and does not qualify a live account." :
        "Run sends this exact redacted source to Fireworks under this operator launch. Review the disclosure and acknowledge before each attempt.");
      setText("run-heading", isOffline() ? "Draft proposals with offline fake transport" : "Draft proposals with Fireworks");
      setText("source-run", isOffline() ? "Run offline fake attempt" : "Run Fireworks authoring");
      $("source-business-text").nextElementSibling.textContent = "I have inspected this exact text and attest that it is redacted business requirement or proposal material permitted for this disclosed inference attempt.";
      $("source-disclosure").querySelector("dt:nth-of-type(4) + dd").textContent =
        "One attempt, a 30-second deadline and at most 2,000 output tokens. Source: 16 KiB; request: 64 KiB; response: 256 KiB. The ledger reserves $0.01 per claim, at most ten claims / $0.10 per runtime, with ten seconds between claims. " +
        (isOffline() ? "These are offline test reservations; billing and account prerequisites remain unverified." : "The admitted profile binds the request and launch budgets. No automatic retry is permitted.");
      if (isDemo()) {
        setText("mode-heading", "Fireworks · one-attempt demo");
        setText("source-mode-copy", "This demo permits ONE attempt for this approved run. Local token counts do not prove server parity. Local reservations are bookkeeping, not a guaranteed invoice ceiling. Provider-reported usage is recorded separately. Review, confirmation and handoff remain available after the attempt.");
        $("source-disclosure").querySelector("dt:nth-of-type(4) + dd").textContent =
          "ONE attempt for this approved run; no retry or replacement after failure, cancellation or uncertainty. 30-second local deadline; at most 2,000 requested output tokens. Source: 16 KiB; request: 64 KiB; response: 256 KiB. $0.01 reserved locally, not a guaranteed invoice ceiling.";
      }
    }
  }
  function controls() {
    const terminal = TERMINAL.has(state), active = Boolean(operation && !terminal);
    const selected = RECEIPT.test(choice.value), blocked = !ready || busy;
    const newAttemptClosed = isDemo() && demoConsumed;
    const selectionLocked = blocked || active || newAttemptClosed, consentLocked = blocked || state !== "PREPARED" || newAttemptClosed;
    const authorizeDisabled = consentLocked || !business.checked || !acknowledged.checked || displayed !== operation;
    const runDisabled = blocked || processing || state !== "AUTHORIZED" || newAttemptClosed;
    const cancelDisabled = !capability || !active;
    choice.disabled = selectionLocked;
    $("source-preview").disabled = selectionLocked || !selected;
    $("source-refresh").disabled = selectionLocked;
    business.disabled = acknowledged.disabled = consentLocked;
    $("source-authorize").disabled = authorizeDisabled;
    $("source-run").disabled = runDisabled;
    // A pending Run HTTP response must not disable the separate cancellation.
    $("source-cancel").disabled = cancelDisabled;
    $("source-task").setAttribute("aria-busy", String(busy));
    const waiting = unavailable ? "This page is unavailable. Reload to validate a fresh session." : "Checking the page session and source list.";
    setText("source-selection-reason", !ready ? waiting : newAttemptClosed ? "This demo attempt is consumed. Follow its status, then continue to human review and handoff." : selectionLocked ?
      busy ? "An action is in progress. Wait before selecting or refreshing a source." :
      "This disclosure locks source selection. Revoke consent or wait for a terminal result before inspecting another source." :
      selected ? "Inspect the selected source. This starts no worker." :
      "Select a current eligible source to inspect, or capture a new source.");
    setText("source-ack-reason", !ready ? waiting : newAttemptClosed ? "No new attempt is authorized for this demo run." : consentLocked ?
      busy ? "Wait for the current action before changing acknowledgment." :
      terminal ? "This consent is terminal. Inspect a current source to acknowledge again." :
      processing || ["CLAIMED", "DISPATCH_AUTHORIZED"].includes(state) ? "This attempt is in progress. Acknowledgment cannot be changed." :
      state === "AUTHORIZED" ? "Acknowledgment is complete. Use the separate Run control." :
      active ? "This disclosure is not ready. Revoke consent and inspect a current source." :
      "Inspect a source before attesting or acknowledging." :
      displayed !== operation ? "Wait for the exact disclosure to finish validating." :
      !business.checked && !acknowledged.checked ? "Both attestations are required before acknowledgment." :
      !business.checked ? "Attest that this exact text is permitted redacted business material." :
      !acknowledged.checked ? "Acknowledge the purpose, limits, data handling and expiry." :
      !authorizeDisabled ? "Acknowledge this disclosure. This starts no worker; Run is separate." : waiting);
    setText("source-run-reason", !ready ? waiting : !runDisabled ?
      (isSynthetic() ? "Run one synthetic validation attempt. No provider call or spend." :
       isOffline() ? "Run one offline fake attempt. No provider call or spend." :
       "Send this exact redacted source to Fireworks for one bounded authoring attempt.") :
      terminal ? state === "SUCCEEDED" ? "This attempt is complete. Open proposals for human review." :
      (isDemo() && demoConsumed ? "This demo attempt is consumed. No replacement is authorized; continue to review or handoff." : "This consent is no longer executable. Inspect and acknowledge again.") :
      (busy && state === "AUTHORIZED") || processing || ["CLAIMED", "DISPATCH_AUTHORIZED"].includes(state) ?
      `The current attempt is starting or processing.${cancelDisabled ? "" : " Revoke consent / cancel remains available."}` :
      "Inspect the exact source and acknowledge before using the separate Run control.");
    const presentation = !ready ? unavailable ? "unavailable" : "unverified" : terminal ? "terminal" :
      state === "AUTHORIZED" && (busy || processing) ? "starting" :
      processing || ["CLAIMED", "DISPATCH_AUTHORIZED"].includes(state) ? "processing" :
      state === "PREPARED" ? "inspect" : state === "AUTHORIZED" ? "acknowledged" : "select";
    if ($("source-authoring-main").getAttribute("data-authoring-state") !== presentation) {
      $("source-authoring-main").setAttribute("data-authoring-state", presentation);
    }
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
      budget_exhausted: "This runtime has consumed all ten claims. No further claim is available.",
      grant_closed: "This runtime is closed. No operation can start.",
      demo_consumed: "This demo attempt is consumed. No new authoring attempt is authorized. Current status, human review and handoff remain available.",
      demo_run_unavailable: "This demo run cannot admit another attempt. Its delivery and billing may be unknown. No retry is authorized.",
    };
    if (isDemo() && ["demo_consumed", "demo_run_unavailable"].includes(code)) demoConsumed = true;
    setText("source-authoring-error", messages[code] || "The response could not be trusted or the request was refused. No automatic retry will run. Refresh current state before continuing.");
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
    return sameKeys(value, keys) && value.mode === runtimeMode && value.poc_id === poc && HEX.test(value.operation_id) &&
      STATES.has(value.state) && typeof value.processing === "boolean" && integer(value.attempts, 1) && integer(value.grant_claims, claimLimit()) &&
      ["0.00", "0.01"].includes(value.reserved_usd) && /^0\.(?:0[0-9]|10)$/.test(value.grant_reserved_usd) &&
      integer(value.expires_in_seconds, 300) && (value.code === null || /^[A-Za-z_]{1,60}$/.test(value.code)) &&
      (value.authoring_receipt_id === null || /^arcp_[a-f0-9]{32}$/.test(value.authoring_receipt_id));
  }
  async function showDisclosure(value, epoch) {
    const current = value.operation_id;
    const canDisplay = () => isCurrent(epoch, current) && ready && !TERMINAL.has(state);
    if (!canDisplay()) return false;
    const d = value.disclosure;
    const keys = ["disclosure_sha256", "source_receipt_id", "source_kind", "source_revision", "source_sha256", "redacted_text", "classification", "provider", "model", "purpose", "custody", "limits"];
    const expectedLimits = {source_bytes: 16384, body_bytes: 65536, response_bytes: 262144,
      output_tokens: 2000, deadline_seconds: 30, consent_seconds: 300, attempts: 1,
      claim_interval_seconds: 10, grant_claims: claimLimit(), reservation_usd: "0.01", grant_reservation_usd: isDemo() ? "0.01" : "0.10"};
    if (!sameKeys(d, keys) || !HEX.test(d.disclosure_sha256) || !HEX.test(d.source_sha256) ||
        !RECEIPT.test(d.source_receipt_id) || d.source_receipt_id !== choice.value || !Object.hasOwn(KINDS, d.source_kind) ||
        !integer(d.source_revision, 1000000) || d.source_revision < 1 || typeof d.redacted_text !== "string" ||
        d.classification !== "OWNER_APPROVED_REDACTED_BUSINESS_TEXT" || d.provider !== "fireworks" ||
        d.model !== "accounts/fireworks/models/deepseek-v4-flash-0731" ||
        typeof d.purpose !== "string" || d.purpose.length > 300 || typeof d.custody !== "string" || d.custody.length > 1000 ||
        !sameKeys(d.limits, Object.keys(expectedLimits)) || Object.entries(expectedLimits).some(([k, v]) => d.limits[k] !== v)) throw new Error("UNTRUSTED_RESPONSE");
    const text = new TextEncoder().encode(d.redacted_text);
    if (!text.length || text.length > 16384) throw new Error("RESPONSE_LIMIT");
    let hash;
    try { hash = await crypto.subtle.digest("SHA-256", text); }
    catch (error) { if (!canDisplay()) return false; throw error; }
    if (!canDisplay()) return false;
    const digest = Array.from(new Uint8Array(hash)).map((n) => n.toString(16).padStart(2, "0")).join("");
    if (digest !== d.source_sha256) throw new Error("UNTRUSTED_RESPONSE");
    displayed = value.operation_id;
    $("source-description").textContent = `${KINDS[d.source_kind]} · revision ${d.source_revision} · ${d.source_receipt_id}`;
    $("source-redacted-text").textContent = d.redacted_text;
    $("source-provider").textContent = `Fireworks · ${d.model} ` +
      (isSynthetic() ? "(candidate only; not contacted)" : isOffline() ? "(offline fake transport; provider not contacted)" : "(one admitted provider attempt on Run)");
    $("source-purpose").textContent = d.purpose;
    $("source-custody").textContent = d.custody;
    $("source-disclosure").hidden = false;
    $("source-empty").hidden = true;
    return true;
  }
  function render(value) {
    if (!trustedOperation(value) || value.operation_id !== operation) throw new Error("UNTRUSTED_RESPONSE");
    if (TERMINAL.has(state) && value.state !== state) return;
    state = value.state; processing = value.processing;
    if (isDemo() && value.grant_claims === 1) demoConsumed = true;
    setText("source-expiry", `${value.expires_in_seconds} seconds remaining. Acknowledgment does not extend expiry.`);
    setText("source-ledger", `${value.grant_claims} of ${claimLimit()} ${isSynthetic() ? "synthetic " : isOffline() ? "offline fake " : ""}claims used · $${value.grant_reserved_usd} reserved locally${isSynthetic() || isOffline() ? " · no provider spend" : ""}.`);
    const labels = {PREPARED: "Inspect the exact text, then attest and acknowledge.", AUTHORIZED: "Acknowledged. Choose Run to start one synthetic attempt.", CLAIMED: "Processing locally. One synthetic attempt has been consumed.", DISPATCH_AUTHORIZED: "The synthetic worker is processing. You can still cancel publication.", SUCCEEDED: "Validated proposals are ready for human review. They remain NEEDS_REVIEW.", FAILED: "The attempt failed safely. No proposals were published and its consumed claim is retained.", OUTCOME_UNKNOWN: "The attempt did not finish within its bound. No new proposal is available from this operation.", STALE: "The source, draft, review or closure state changed. Inspect current source state before continuing.", EXPIRED: "This disclosure expired. Inspect the source and acknowledge a new disclosure.", REVOKED: "Consent revoked. This operation cannot run again."};
    if (!isSynthetic()) {
      labels.AUTHORIZED = "Acknowledged. Choose Run to start one " + (isOffline() ? "offline fake" : "Fireworks") + " attempt.";
      labels.CLAIMED = "One attempt has been consumed. Preparing the bounded worker.";
      labels.DISPATCH_AUTHORIZED = "Dispatch authorized. Cancellation can prevent publication but cannot undo a completed delivery.";
      labels.OUTCOME_UNKNOWN = "The attempt's outcome is uncertain. No proposal was published. Its claim remains consumed and it will not be retried.";
    }
    if (isDemo() && demoConsumed && TERMINAL.has(state) && state !== "SUCCEEDED") {
      labels[state] += " This demo attempt is consumed. No replacement is authorized.";
    }
    setText("source-status", value.processing && state === "AUTHORIZED" ?
      (isSynthetic() ? "Starting the bounded synthetic worker…" : "Starting the bounded authoring worker…") : labels[state]);
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
        if (isCurrent(epoch, current)) render(value);
      } catch (error) {
        if (isCurrent(epoch, current) && !TERMINAL.has(state)) {
          failure(error.message); ready = false; unavailable = true; clearSource(); showMode("unavailable");
          setText("source-status", "Current consent could not be verified. Reload before continuing.");
          controls();
        }
      }
      if (isCurrent(epoch, current)) poll();
    }, 750);
  }
  async function sources(epoch) {
    const current = operation;
    let value;
    try { value = await api("sources"); }
    catch (error) { if (!isCurrent(epoch, current)) return false; throw error; }
    if (!isCurrent(epoch, current)) return false;
    if (!sameKeys(value, ["mode", "poc_id", "sources"]) || value.mode !== runtimeMode || value.poc_id !== poc ||
        !Array.isArray(value.sources) || value.sources.length > 256 || value.sources.some((s) =>
          !sameKeys(s, ["source_receipt_id", "source_kind", "eligible"]) || !RECEIPT.test(s.source_receipt_id) ||
          !Object.hasOwn(KINDS, s.source_kind) || typeof s.eligible !== "boolean")) throw new Error("UNTRUSTED_RESPONSE");
    choice.replaceChildren(new Option("Select one current source", ""));
    for (const source of value.sources) {
      const option = new Option(`${KINDS[source.source_kind]} · ${source.source_receipt_id}${source.eligible ? "" : " · unavailable for authoring"}`, source.source_receipt_id);
      option.disabled = !source.eligible; choice.append(option);
    }
    if (!value.sources.some((s) => s.eligible)) setText("source-status", "No current source is eligible. Capture new source text or return to human review.");
    return true;
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
    ready = false; busy = true; unavailable = false; processing = false; clearSource(); showMode("checking"); controls();
    setText("source-status", "Starting a fresh page session…");
    setText("source-ledger", "The runtime ledger has not been read yet.");
    $("source-review-result").hidden = true;
    if (!poc) { unavailable = true; busy = false; showMode("unavailable"); failure("REQUEST_REFUSED"); controls(); return; }
    $("back-to-review").href = $("source-review-result").href = `/app/pocs/${poc}/review`;
    try {
      const value = await api("bootstrap");
      if (epoch !== serial) return;
      if (!sameKeys(value, ["schema_version", "capability", "mode", "poc_id", "display_name", "live_enabled", "live_missing"]) ||
          value.schema_version !== "exitspec.source-authoring-web/1" || !HEX.test(value.capability) || !MODES.has(value.mode) ||
          value.poc_id !== poc || value.live_enabled !== ["QUALIFIED_FIREWORKS", "DEMO_FIREWORKS"].includes(value.mode) || typeof value.display_name !== "string" || value.display_name.length > 200 ||
          !Array.isArray(value.live_missing) || value.live_missing.length !== (value.mode === MODE ? 4 : 0) || new Set(value.live_missing).size !== value.live_missing.length ||
          value.live_missing.some((reason) => typeof reason !== "string" || !Object.hasOwn(LIVE_MISSING, reason))) throw new Error("UNTRUSTED_RESPONSE");
      capability = value.capability;
      runtimeMode = value.mode;
      showMode("verified");
      $("source-poc-title").textContent = value.display_name;
      setText("source-status", "Select and inspect one current source. Nothing runs on page load.");
      const loaded = await sources(epoch);
      if (loaded && isCurrent(epoch, null)) ready = true;
    } catch (error) { if (epoch === serial) { unavailable = true; showMode("unavailable"); failure(error.message); } }
    finally { if (epoch === serial) { busy = false; controls(); } }
  }
  choice.addEventListener("change", controls);
  business.addEventListener("change", controls); acknowledged.addEventListener("change", controls);
  $("source-refresh").addEventListener("click", () => action(async (epoch) => {
    operation = state = key = null; clearSource(); await sources(epoch);
  }));
  $("source-preview").addEventListener("click", () => action(async (epoch) => {
    clearSource(); key = crypto.randomUUID();
    const current = operation;
    const value = await api("prepare", {source_receipt_id: choice.value});
    if (!isCurrent(epoch, current)) return;
    if (!trustedOperation(value) || value.state !== "PREPARED") throw new Error("CONSENT_REFUSED");
    operation = value.operation_id;
    state = null;
    const shown = await showDisclosure(value, epoch);
    if (!shown || !isCurrent(epoch, value.operation_id)) return;
    render(value); $("source-redacted-text").focus();
  }));
  $("source-authorize").addEventListener("click", () => action(async (epoch) => {
    if (!business.checked || !acknowledged.checked || displayed !== operation) return;
    const current = operation;
    const value = await api("authorize", {operation_id: current, business_text: true, acknowledged: true, idempotency_key: key});
    if (isCurrent(epoch, current)) render(value);
  }));
  $("source-run").addEventListener("click", () => action(async (epoch) => {
    const current = operation;
    const value = await api("run", {operation_id: current});
    if (isCurrent(epoch, current)) render(value);
  }));
  $("source-cancel").addEventListener("click", async () => {
    const epoch = serial, current = operation;
    try {
      const value = await api("revoke", {operation_id: current});
      if (isCurrent(epoch, current)) render(value);
    } catch (error) { if (isCurrent(epoch, current) && !TERMINAL.has(state)) failure(error.message); }
  });
  window.addEventListener("pagehide", () => {
    if (capability && operation && !TERMINAL.has(state)) {
      fetch(endpoint + "revoke", {method: "POST", headers: {"Content-Type": "application/json", [HEADER]: capability},
        body: JSON.stringify({operation_id: operation}), keepalive: true, cache: "no-store", credentials: "omit", redirect: "error"}).catch(() => {});
    }
    ++serial; clearTimeout(timer); for (const controller of pending) controller.abort();
    capability = operation = state = key = null; ready = false; busy = false; unavailable = false; clearSource(); showMode("checking"); controls();
    setText("source-status", "Page session cleared. A fresh session is required.");
    setText("source-ledger", "The runtime ledger has not been read yet.");
  });
  window.addEventListener("pageshow", (event) => { if (event.persisted) initialise(); });
  initialise();
})();
