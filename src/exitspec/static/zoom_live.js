(() => {
  "use strict";

  const route = /^\/app\/pocs\/(poc_[a-z0-9][a-z0-9_-]{2,63})\/(?:capture|sources\/new)$/.exec(window.location.pathname);
  const panel = document.getElementById("zoom-live-panel");
  if (!route || !panel || panel.dataset.zoomLiveEnabled !== "true") return;
  panel.hidden = false;
  const pocId = route[1];
  const endpoint = `/api/pocs/${pocId}/zoom-live`;
  const reviewPath = `/app/pocs/${pocId}/review`;
  const entry = document.getElementById("meeting-entry");
  const element = (name) => document.getElementById(`zoom-live-${name}`);
  const consent = element("consent");
  const status = element("status");
  const mode = element("mode");
  const counts = element("counts");
  const review = element("review");
  const actions = ["start", "stop", "process", "reset"];
  const stopping = new Set(["LISTENING", "INTERRUPTED", "RECONNECTING"]);
  const polling = new Set(["PAIRED", "WAITING", ...stopping, "STOP_REQUESTED", "DRAINING"]);
  const stateCopy = Object.freeze({
    UNPAIRED: "Disconnected. Ask the local operator to pair this POC, then refresh. This page cannot pair an operator.",
    PAIRED: "Operator paired. Review participant consent before starting capture.",
    WAITING: "Start requested. Waiting for Zoom transport; no listening connection verified yet.",
    LISTENING: "Receiving a bounded requirements window. Stop when the synthetic requirements have been spoken.",
    INTERRUPTED: "Connection interrupted. Capture continuity is uncertain.",
    RECONNECTING: "Reconnecting. A restored connection does not prove complete delivery.",
    STOP_REQUESTED: "Transport stop requested. Waiting for acknowledgement; capture is not ready to process.",
    DRAINING: "Stop acknowledged. Draining the local buffer; capture is not ready to process.",
    CAPTURE_READY: "Bounded capture ready for processing. This is not a complete meeting transcript.",
    DRAFT_READY: "Review proposals attached. Human review is required; capture does not validate measurements.",
    FAILED: "Capture failed safely. No successful capture is claimed. Ask the operator to inspect the local status.",
    REVOKED: "Capture session revoked. A fresh operator pairing and participant consent are required.",
  });
  const keys = ["schema_version", "poc_id", "session_id", "state", "transport_mode", "provider_connected", "source_content_classification", "capture_scope", "segment_count", "proposal_count", "source_receipt_id", "review_url", "failure_code"].sort();
  let snapshot = null;
  let busy = false;
  let timer = null;
  let request = null;
  let generation = 0;
  let closed = false;

  function trusted(value) {
    if (!value || typeof value !== "object" || Array.isArray(value) ||
        JSON.stringify(Object.keys(value).sort()) !== JSON.stringify(keys)) return false;
    if (value.schema_version !== "exitspec.zoom-live/1.0" || value.poc_id !== pocId ||
        !Object.hasOwn(stateCopy, value.state) ||
        !["LIVE_ZOOM_RTMS", "FAKE_ZOOM_RTMS", "DISABLED"].includes(value.transport_mode) ||
        typeof value.provider_connected !== "boolean" ||
        value.source_content_classification !== "SYNTHETIC_REQUIREMENTS_ONLY" ||
        value.capture_scope !== "BOUNDED_WINDOW_NOT_COMPLETE_MEETING" ||
        !Number.isSafeInteger(value.segment_count) || value.segment_count < 0 || value.segment_count > 256 ||
        !Number.isSafeInteger(value.proposal_count) || value.proposal_count < 0 || value.proposal_count > 64 ||
        !(value.failure_code === null || (typeof value.failure_code === "string" && /^[A-Z][A-Z0-9_]{0,79}$/.test(value.failure_code)))) return false;
    if (value.state === "UNPAIRED") {
      if (value.session_id !== null || value.transport_mode !== "DISABLED" || value.provider_connected || value.segment_count !== 0) return false;
    } else if (typeof value.session_id !== "string" || !/^zoomsess_[a-f0-9]{64}$/.test(value.session_id) || value.transport_mode === "DISABLED") return false;
    if ((value.state === "LISTENING" && !value.provider_connected) ||
        (value.provider_connected && !["LISTENING", "STOP_REQUESTED", "DRAINING"].includes(value.state)) ||
        (["CAPTURE_READY", "DRAFT_READY"].includes(value.state) && value.segment_count === 0)) return false;
    if (value.source_receipt_id !== null) {
      if (typeof value.source_receipt_id !== "string" || !/^srcpt_[a-z0-9][a-z0-9_-]{7,95}$/.test(value.source_receipt_id) ||
          !["DRAFT_READY", "REVOKED", "FAILED"].includes(value.state) ||
          value.review_url !== reviewPath) return false;
    } else if (value.state === "DRAFT_READY" || value.review_url !== null || value.proposal_count !== 0) return false;
    return true;
  }

  function allowed(action) {
    if (busy || !snapshot || entry.hidden) return false;
    if (action === "start") return snapshot.state === "PAIRED" && consent.checked;
    if (action === "stop") return stopping.has(snapshot.state);
    if (action === "process") return snapshot.state === "CAPTURE_READY";
    return action === "reset" && snapshot.session_id !== null && snapshot.state !== "REVOKED";
  }

  function render() {
    actions.forEach((action) => { element(action).disabled = !allowed(action); });
    element("refresh").disabled = busy;
    consent.disabled = busy || !snapshot || snapshot.state !== "PAIRED";
    review.hidden = !snapshot || snapshot.state !== "DRAFT_READY";
    review.removeAttribute("href");
    if (!snapshot) {
      if (busy) {
        mode.textContent = "Connection unverified";
        counts.textContent = "Checking capture status…";
        status.textContent = "Checking the local operator connection…";
      }
      return;
    }
    status.textContent = stateCopy[snapshot.state];
    mode.textContent = snapshot.transport_mode === "FAKE_ZOOM_RTMS"
      ? "Simulated transport · no live Zoom connection"
      : snapshot.transport_mode === "DISABLED" ? "Disconnected"
        : snapshot.provider_connected ? "Live Zoom transport connected" : "Live Zoom transport disconnected";
    counts.textContent = `${snapshot.segment_count} captured segments · ${snapshot.proposal_count} review proposals · synthetic requirements only`;
    if (!review.hidden) review.setAttribute("href", reviewPath);
  }

  function clearTimer() {
    window.clearTimeout(timer);
    timer = null;
  }

  function schedule() {
    clearTimer();
    if (!closed && !entry.hidden && snapshot && polling.has(snapshot.state)) {
      timer = window.setTimeout(() => load(), 1000);
    }
  }

  function fail() {
    snapshot = null;
    consent.checked = false;
    clearTimer();
    render();
    mode.textContent = "Connection unverified";
    counts.textContent = "Capture status unavailable; no success verified.";
    status.textContent = "The connection status could not be verified. No action will be retried automatically. Refresh to recover the current state.";
  }

  async function load(action = null) {
    if (closed || busy || entry.hidden || (action && !allowed(action))) return;
    clearTimer();
    const expectedSession = action ? snapshot.session_id : null;
    const body = action ? { action, session_id: expectedSession, idempotency_key: crypto.randomUUID() } : null;
    if (action === "start") body.consent_acknowledged = true;
    const ticket = ++generation;
    busy = true;
    request = new AbortController();
    const controller = request;
    const timeout = window.setTimeout(() => controller.abort(), 5000);
    render();
    try {
      const response = await fetch(endpoint, {
        method: action ? "POST" : "GET", credentials: "same-origin", cache: "no-store", redirect: "error",
        headers: { Accept: "application/json", ...(action ? { "Content-Type": "application/json" } : {}) },
        ...(action ? { body: JSON.stringify(body) } : {}), signal: controller.signal,
      });
      if (!response.ok || response.headers.get("Content-Type")?.split(";")[0].trim() !== "application/json") throw new Error("Unavailable");
      const text = await response.text();
      if (text.length > 16384) throw new Error("Oversized status");
      const value = JSON.parse(text);
      if (ticket !== generation || closed || entry.hidden) return;
      if (!trusted(value) || (expectedSession && value.session_id !== expectedSession)) throw new Error("Untrusted status");
      if (snapshot?.session_id !== value.session_id || value.state !== "PAIRED") consent.checked = false;
      snapshot = value;
      busy = false;
      render();
      schedule();
    } catch {
      if (ticket === generation && !closed && !entry.hidden) {
        busy = false;
        fail();
      }
    } finally {
      window.clearTimeout(timeout);
      if (ticket === generation) { busy = false; request = null; }
    }
  }

  actions.forEach((action) => element(action).addEventListener("click", () => load(action)));
  element("refresh").addEventListener("click", () => load());
  consent.addEventListener("change", render);
  new MutationObserver(() => {
    if (entry.hidden) {
      ++generation;
      request?.abort();
      busy = false;
      snapshot = null;
      consent.checked = false;
      clearTimer();
      render();
    } else load();
  }).observe(entry, { attributes: true, attributeFilter: ["hidden"] });
  window.addEventListener("pagehide", () => {
    closed = true;
    ++generation;
    request?.abort();
    clearTimer();
  });
  if (!entry.hidden) load();
})();
