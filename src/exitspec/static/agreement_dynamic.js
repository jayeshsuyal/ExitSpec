(() => {
  "use strict";

  const ROUTE = /^\/app\/pocs\/(poc_[a-z0-9][a-z0-9_-]{2,63})\/agreement$/;
  const match = window.location.search === "" && window.location.hash === "" ? window.location.pathname.match(ROUTE) : null;
  const pocId = match ? match[1] : null;
  const pocApi = pocId ? `/api/pocs/${encodeURIComponent(pocId)}` : null;
  const agreementApi = pocApi ? `${pocApi}/agreement` : null;
  const task = document.querySelector("#agreement-task");
  const prepareForm = document.querySelector("#prepare-form");
  const summary = document.querySelector("#agreement-summary");
  const status = document.querySelector("#agreement-status");
  const errorPanel = document.querySelector("#agreement-error");
  const freezeButton = document.querySelector("#freeze-agreement");
  const reviewLink = document.querySelector("#open-customer-review");
  const revisePlan = document.querySelector("#revise-plan");
  const revisionForm = document.querySelector("#revision-form");
  let snapshot = null;

  function safePath(path) {
    if (typeof path !== "string") return false;
    try {
      const parsed = new URL(path, window.location.origin);
      return parsed.origin === window.location.origin && parsed.pathname === path && !parsed.search && !parsed.hash && (path === pocApi || path === agreementApi || path === `${agreementApi}/freeze` || path === `${agreementApi}/revision`);
    } catch { return false; }
  }
  async function getJson(path) {
    if (!safePath(path)) throw new Error("untrusted path");
    const response = await fetch(path, {cache: "no-store", credentials: "same-origin", headers: {Accept: "application/json"}});
    const payload = await response.json().catch(() => null);
    if (!response.ok || !payload || typeof payload !== "object" || Array.isArray(payload)) throw new Error("request failed");
    return payload;
  }
  async function postJson(path, body) {
    if (!safePath(path)) throw new Error("untrusted path");
    const response = await fetch(path, {method: "POST", cache: "no-store", credentials: "same-origin", headers: {Accept: "application/json", "Content-Type": "application/json", Origin: window.location.origin}, body: JSON.stringify(body)});
    const payload = await response.json().catch(() => null);
    if (!response.ok || !payload || typeof payload !== "object" || Array.isArray(payload)) throw new Error(payload?.error || "request failed");
    return payload;
  }
  function key(prefix) { return `${prefix}-${window.crypto?.randomUUID?.() || Math.random().toString(16).slice(2)}`; }
  function validText(value, max) { return typeof value === "string" && value.trim() && value.length <= max && !/[\u0000-\u001f\u007f]/.test(value); }
  function setError(message) { errorPanel.textContent = message; errorPanel.hidden = !message; }
  function setStatus(value) { status.textContent = value; status.dataset.state = value; }
  function text(value) { return value == null ? "" : String(value); }
  function proofText(criterion) {
    const binding = criterion.evidence_binding?.policy;
    if (criterion.planning_disposition === "EXECUTABLE" && binding) {
      return "Run the server-owned exact-tool selection policy over " + binding.minimum_samples + " approved support-tool cases; require " + criterion.rule + " " + criterion.operator + " " + criterion.threshold + " " + criterion.unit + " using " + binding.confidence_method + ".";
    }
    if (criterion.planning_disposition === "EVIDENCE_IMPORT" && binding) {
      return "Import one managed TTFT evidence result for " + binding.native_metric + "; require p95 " + criterion.operator + " " + criterion.threshold + " " + criterion.unit + " over " + binding.attempts + " attempts at configured concurrency " + binding.configured_concurrency + ", reduced with " + binding.reducer_id + ".";
    }
    return "No executable proof is scheduled in A5. This " + criterion.planning_disposition.toLowerCase() + " item remains customer-bound: " + (criterion.planning_reason || "the A4 limitation is preserved.");
  }
  function renderProof(agreement) {
    const target = document.querySelector("#agreement-proof"); target.textContent = "";
    (agreement.criteria || []).forEach((criterion) => {
      const paragraph = document.createElement("p");
      paragraph.textContent = proofText(criterion);
      target.appendChild(paragraph);
    });
  }

  function currentState(data) {
    if (data.frozen_contract) return "FROZEN";
    if (data.confirmation?.decision === "CONFIRM") return "CONFIRMED";
    if (data.confirmation?.decision === "REQUEST_CHANGES") return "CHANGES_REQUESTED";
    if (data.current_inputs_stale) return "STALE";
    if (data.customer_review?.status === "EXPIRED") return "EXPIRED";
    return data.customer_review ? "PENDING" : "DRAFT";
  }
  function renderCriterion(criterion) {
    const item = document.createElement("article");
    item.className = "agreement-criterion";
    item.dataset.disposition = text(criterion.planning_disposition);
    const head = document.createElement("div"); head.className = "criterion-head";
    const heading = document.createElement("h3"); heading.textContent = criterion.normalized_claim || criterion.title;
    const disposition = document.createElement("span"); disposition.textContent = `${criterion.planning_scope} · ${criterion.planning_disposition}`;
    head.append(heading, disposition); item.appendChild(head);
    const rule = document.createElement("p"); rule.className = "criterion-rule";
    if (criterion.planning_disposition === "EXECUTABLE" || criterion.planning_disposition === "EVIDENCE_IMPORT") {
      rule.textContent = `${criterion.rule} · ${criterion.operator} ${criterion.threshold} ${criterion.unit}`;
    } else {
      rule.textContent = `Not executable in A5 · ${criterion.planning_reason}`;
    }
    item.appendChild(rule);
    const meta = document.createElement("div"); meta.className = "criterion-meta";
    [criterion.measurement_population && `Population: ${criterion.measurement_population}`, criterion.capability_key && `Capability: ${criterion.capability_key}`, criterion.proposal_id, criterion.planning_item_id].filter(Boolean).forEach((value) => { const span = document.createElement("span"); span.textContent = value; meta.appendChild(span); });
    item.appendChild(meta);
    return item;
  }
  function render(data) {
    snapshot = data;
    const state = currentState(data);
    setStatus(state);
    prepareForm.hidden = Boolean(data.agreement);
    summary.hidden = !data.agreement;
    if (!data.agreement) return;
    const agreement = data.agreement;
    document.querySelector("#agreement-customer").textContent = `${agreement.customer} · ${agreement.use_case}`;
    document.querySelector("#agreement-version").textContent = `Version ${data.draft.contract_version}`;
    const criteria = document.querySelector("#agreement-criteria"); criteria.textContent = "";
    (agreement.criteria || []).forEach((criterion) => criteria.appendChild(renderCriterion(criterion)));
    renderProof(agreement);
    const limitations = document.querySelector("#agreement-limitations"); limitations.textContent = "";
    (agreement.non_goals || []).forEach((value) => { const paragraph = document.createElement("p"); paragraph.textContent = value; limitations.appendChild(paragraph); });
    const technical = {
      contract_id: data.draft.contract_id,
      contract_version: data.draft.contract_version,
      contract_fingerprint: data.draft.contract_fingerprint,
      draft_sha256: data.draft.draft_sha256,
      plan: data.plan,
      criteria: (agreement.criteria || []).map((criterion) => ({id: criterion.id, proposal_id: criterion.proposal_id, planning_item_id: criterion.planning_item_id, a4_plan_id: criterion.a4_plan_id, a4_plan_version: criterion.a4_plan_version, a4_plan_sha256: criterion.a4_plan_sha256, source_id: criterion.source_id, source_content_sha256: criterion.source_content_sha256, adapter: criterion.adapter, adapter_version: criterion.adapter_version, evidence_profile: criterion.evidence_profile, evidence_binding: criterion.evidence_binding}))
    };
    document.querySelector("#agreement-technical").textContent = JSON.stringify(technical, null, 2);
    reviewLink.hidden = true; freezeButton.hidden = true; revisePlan.hidden = true; revisionForm.hidden = true;
    if (state === "PENDING" && data.customer_review?.review_url && !data.current_inputs_stale) { reviewLink.href = data.customer_review.review_url; reviewLink.hidden = false; }
    if (state === "CONFIRMED") freezeButton.hidden = false;
    if (state === "CHANGES_REQUESTED" || state === "STALE") {
      revisePlan.href = `/app/pocs/${encodeURIComponent(pocId)}/capability-plan`;
      revisePlan.hidden = false;
      revisionForm.hidden = false;
    }
    if (state === "FROZEN") { freezeButton.textContent = "Version frozen"; freezeButton.disabled = true; freezeButton.hidden = false; }
  }
  async function initialise() {
    if (!pocId) { setError("This agreement address is invalid."); task.setAttribute("aria-busy", "false"); return; }
    try {
      const [draft, data] = await Promise.all([getJson(pocApi), getJson(agreementApi)]);
      if (draft.poc_id !== pocId || draft.archive_state !== "ACTIVE" || data.poc_id !== pocId) throw new Error("untrusted data");
      document.querySelector("#poc-title").textContent = draft.display_name;
      document.querySelector("#poc-context").textContent = `${draft.customer_label} · current A4 capability plan`;
      render(data); task.setAttribute("aria-busy", "false");
    } catch { setError("The current POC or A4 plan could not be verified. No agreement was created."); task.setAttribute("aria-busy", "false"); }
  }
  prepareForm.addEventListener("submit", async (event) => {
    event.preventDefault(); setError("");
    const reviewer = document.querySelector("#assembly-reviewer").value.trim();
    const rationale = document.querySelector("#assembly-rationale").value.trim();
    if (!validText(reviewer, 160) || !validText(rationale, 2000)) { setError("A named reviewer and rationale are required."); return; }
    document.querySelector("#prepare-agreement").disabled = true;
    try { const response = await postJson(agreementApi, {reviewer, rationale, idempotency_key: key("prepare-agreement")}); render((await getJson(agreementApi))); } catch (error) { setError(error.message || "The agreement was refused safely."); document.querySelector("#prepare-agreement").disabled = false; }
  });
  freezeButton.addEventListener("click", async () => {
    setError(""); freezeButton.disabled = true;
    try { await postJson(`${agreementApi}/freeze`, {idempotency_key: key("freeze-agreement")}); render(await getJson(agreementApi)); } catch (error) { setError(error.message || "The exact confirmed version could not be frozen."); freezeButton.disabled = false; }
  });
  revisionForm.addEventListener("submit", async (event) => {
    event.preventDefault(); setError("");
    const reviewer = document.querySelector("#revision-reviewer").value.trim();
    const rationale = document.querySelector("#revision-rationale").value.trim();
    if (!validText(reviewer, 160) || !validText(rationale, 2000)) { setError("A fresh successor reviewer and rationale are required."); return; }
    document.querySelector("#start-revision").disabled = true;
    try { await postJson(`${agreementApi}/revision`, {reviewer, rationale, idempotency_key: key("agreement-revision")}); render(await getJson(agreementApi)); } catch (error) { setError(error.message || "A changed current A3/A4 snapshot is required before successor creation."); document.querySelector("#start-revision").disabled = false; }
  });
  initialise();
})();
