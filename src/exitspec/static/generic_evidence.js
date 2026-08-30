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
    const current = snapshot && snapshot.current;
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
    $("#evidence-authorization").textContent = snapshot?.authorization || "Evidence is proof, not shipping authorization.";
    $("#evidence-technical-binding").textContent = current
      ? `Contract hash ${current.contract_hash} · attempt ${current.attempt_id}`
      : "No current contract or attempt binding.";
    const limitations = visibleLimitations(current);
    $("#evidence-limitation").textContent = limitations[0] || "No admitted evidence limitation is available yet.";
    $("#evidence-next-action").textContent = current?.next_action || "Acknowledge the exact frozen evidence request.";

    const pack = current?.evidence_pack_url;
    const packLink = $("#evidence-pack-link");
    packLink.hidden = !pack;
    if (pack) packLink.href = pack;
    const canStop = Boolean(current && ["COMPLETED", "INGESTION_REJECTED", "FAILED_INTERNAL", "CANCELLED"].includes(current.status));
    const closed = Boolean(snapshot?.closure);
    const start = $("#start-evidence");
    const stop = $("#stop-evidence");
    const handoff = $("#handoff-evidence");
    const handoffFields = $("#handoff-fields");
    stop.hidden = !canStop || closed;
    handoff.hidden = !(pack && current?.is_current && !closed);
    handoffFields.hidden = handoff.hidden;
    start.disabled = busy || closed || !$("#evidence-acknowledged").checked || Boolean(current && ["RESERVED", "RUNNING"].includes(current.status));
    stop.disabled = busy;
    handoff.disabled = busy || !$("#decision-owner").value.trim() || !$("#decision-rationale").value.trim();
    const deciding = !handoff.hidden;
    $("#evidence-task-kicker").textContent = deciding ? "Current task · Decide" : "Current task · Prove";
    $("#evidence-task-heading").textContent = deciding
      ? "Review the current pack and record the human decision"
      : "Run the approved evidence method";

    const history = $("#evidence-history");
    history.replaceChildren();
    for (const [index, attempt] of (snapshot?.history || []).entries()) {
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
          decided_by: $("#decision-owner").value.trim(),
          rationale: decision === "handoff" ? $("#decision-rationale").value.trim() : "Stopped after reviewing the current evidence state.",
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
  $("#decision-owner").addEventListener("input", render);
  $("#decision-rationale").addEventListener("input", render);
  $("#start-evidence").addEventListener("click", start);
  $("#handoff-evidence").addEventListener("click", () => close("handoff"));
  $("#stop-evidence").addEventListener("click", () => close("stop"));
  refresh().catch((error) => setError(error.message));
})();
