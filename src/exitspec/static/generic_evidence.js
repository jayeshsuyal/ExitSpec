(() => {
  "use strict";

  const match = window.location.pathname.match(
    /^\/app\/pocs\/(poc_[a-z0-9][a-z0-9_-]{2,63})\/evidence$/,
  );
  const pocId = match ? match[1] : null;
  const api = pocId ? `/api/pocs/${pocId}/evidence` : null;
  const $ = (selector) => document.querySelector(selector);
  let snapshot = null;
  let busy = false;

  function setError(message) {
    const error = $("#evidence-error");
    error.textContent = message;
    error.hidden = !message;
  }

  function render() {
    const current = snapshot && snapshot.current;
    $("#generic-main").setAttribute("aria-busy", busy ? "true" : "false");
    $("#evidence-title").textContent = current
      ? `${current.contract_id} · v${current.contract_version}`
      : "Current evidence";
    $("#evidence-subtitle").textContent = current
      ? `Customer-confirmed contract ${current.contract_hash}`
      : "Evidence begins only after the server confirms a frozen agreement.";
    $("#evidence-current-status").textContent = current ? current.status : "NOT STARTED";
    $("#evidence-current-verdict").textContent = current?.reduction?.verdict || "—";
    $("#evidence-result-verdict").textContent = current?.reduction?.verdict || "NOT RUN";
    $("#evidence-result-reason").textContent = current
      ? `${current.reason} Next: ${current.next_action}`
      : "No admitted terminal evidence yet.";
    $("#evidence-guidance").textContent = current
      ? current.next_action
      : "ExitSpec selects the method from the frozen agreement.";
    $("#evidence-authorization").textContent = snapshot?.authorization || "Evidence is proof, not shipping authorization.";

    const pack = current?.evidence_pack_url;
    const packLink = $("#evidence-pack-link");
    packLink.hidden = !pack;
    if (pack) packLink.href = pack;
    const canStop = Boolean(current && ["COMPLETED", "INGESTION_REJECTED", "FAILED_INTERNAL", "CANCELLED"].includes(current.status));
    const closed = Boolean(snapshot?.closure);
    const start = $("#start-evidence");
    const stop = $("#stop-evidence");
    const handoff = $("#handoff-evidence");
    stop.hidden = !canStop || closed;
    handoff.hidden = !(pack && current?.is_current && !closed);
    start.disabled = busy || closed || !$("#evidence-acknowledged").checked || Boolean(current && ["RESERVED", "RUNNING"].includes(current.status));
    stop.disabled = busy;
    handoff.disabled = busy;

    const history = $("#evidence-history");
    history.replaceChildren();
    for (const attempt of snapshot?.history || []) {
      const item = document.createElement("li");
      const verdict = attempt.reduction?.verdict || "NO VERDICT";
      item.textContent = `${attempt.attempt_id} · ${attempt.status} · ${verdict} · ${attempt.is_current ? "CURRENT" : "HISTORICAL"}`;
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
    render();
    try {
      snapshot = await request(api, { headers: {} });
    } finally {
      busy = false;
      render();
    }
  }

  async function start() {
    if (busy || snapshot?.closure) return;
    setError("");
    busy = true;
    render();
    try {
      const body = await request(api, {
        method: "POST",
        body: JSON.stringify({
          acknowledgement: true,
          idempotency_key: `a6-browser-${crypto.randomUUID()}`,
        }),
      });
      snapshot = { ...snapshot, current: body.attempt, history: [...(snapshot?.history || []).filter((item) => item.attempt_id !== body.attempt.attempt_id), body.attempt] };
    } catch (error) { setError(error.message); }
    finally {
      busy = false;
      render();
    }
  }

  async function close(decision) {
    const current = snapshot?.current;
    if (!current || busy || snapshot?.closure) return;
    busy = true;
    render();
    try {
      await request(`${api}/${current.attempt_id}/${decision}`, {
        method: "POST",
        body: JSON.stringify({
          decided_by: "a6.browser.customer",
          rationale: decision === "handoff" ? "Reviewed the current Evidence Pack." : "Stopped the current POC.",
          idempotency_key: `a6-browser-${decision}-${crypto.randomUUID()}`,
        }),
      });
      await refresh();
    } catch (error) { setError(error.message); }
    finally {
      busy = false;
      render();
    }
  }

  $("#evidence-acknowledged").addEventListener("change", render);
  $("#start-evidence").addEventListener("click", start);
  $("#handoff-evidence").addEventListener("click", () => close("handoff"));
  $("#stop-evidence").addEventListener("click", () => close("stop"));
  refresh().catch((error) => setError(error.message));
})();
