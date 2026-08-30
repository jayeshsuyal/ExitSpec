(() => {
  "use strict";

  const TOKEN = /^[A-Za-z0-9_-]{32,512}$/;
  const token = window.location.search === "" && window.location.hash === "" ? window.location.pathname.match(/^\/review\/([A-Za-z0-9_-]{32,512})$/)?.[1] : null;
  const reviewApi = token ? `/api/review/${token}` : null;
  const task = document.querySelector("#customer-review-task");
  const form = document.querySelector("#customer-decision-form");
  const result = document.querySelector("#review-result");
  const errorPanel = document.querySelector("#customer-review-error");
  let review = null;

  function safePath(path) {
    if (typeof path !== "string") return false;
    try { const parsed = new URL(path, window.location.origin); return parsed.origin === window.location.origin && parsed.pathname === path && !parsed.search && !parsed.hash && path === reviewApi; } catch { return false; }
  }
  async function getJson() {
    if (!safePath(reviewApi)) throw new Error("untrusted path");
    const response = await fetch(reviewApi, {cache: "no-store", credentials: "same-origin", headers: {Accept: "application/json"}});
    const payload = await response.json().catch(() => null);
    if (!response.ok || !payload || typeof payload !== "object" || Array.isArray(payload)) throw new Error("request failed");
    return payload;
  }
  async function postJson(body) {
    if (!safePath(reviewApi)) throw new Error("untrusted path");
    const response = await fetch(reviewApi, {method: "POST", cache: "no-store", credentials: "same-origin", headers: {Accept: "application/json", "Content-Type": "application/json", Origin: window.location.origin}, body: JSON.stringify(body)});
    const payload = await response.json().catch(() => null);
    if (!response.ok || !payload || typeof payload !== "object" || Array.isArray(payload)) throw new Error(payload?.error || "request failed");
    return payload;
  }
  function key() { return `customer-review-${window.crypto?.randomUUID?.() || Math.random().toString(16).slice(2)}`; }
  function setError(message) { errorPanel.textContent = message; errorPanel.hidden = !message; }
  function addText(parent, tag, className, value) { const child = document.createElement(tag); if (className) child.className = className; child.textContent = value == null ? "" : String(value); parent.appendChild(child); return child; }
  function renderCriterion(criterion) {
    const item = document.createElement("article"); item.className = "review-criterion"; item.dataset.disposition = criterion.planning_disposition || "";
    addText(item, "h3", "", criterion.normalized_claim || criterion.title);
    const supported = criterion.planning_disposition === "EXECUTABLE" || criterion.planning_disposition === "EVIDENCE_IMPORT";
    addText(item, "p", "", supported ? `${criterion.rule} · ${criterion.operator} ${criterion.threshold} ${criterion.unit} · ${criterion.measurement_population}` : `Not executable in A5 · ${criterion.planning_reason}`);
    addText(item, "small", "", `${criterion.planning_scope} · ${criterion.planning_disposition} · ${criterion.proposal_id}`);
    return item;
  }
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
  function render(data) {
    review = data.review;
    const agreement = review.agreement;
    document.querySelector("#review-customer").textContent = `${agreement.customer} · ${agreement.use_case}`;
    document.querySelector("#review-version").textContent = `Version ${review.contract_version}`;
    document.querySelector("#review-fingerprint").textContent = `Fingerprint ${review.confirmation_fingerprint}`;
    const criteria = document.querySelector("#review-criteria"); criteria.textContent = "";
    (agreement.criteria || []).forEach((criterion) => criteria.appendChild(renderCriterion(criterion)));
    const proof = document.querySelector("#review-proof"); proof.textContent = "";
    (agreement.criteria || []).forEach((criterion) => addText(proof, "p", "", proofText(criterion)));
    const limitations = document.querySelector("#review-limitations"); limitations.textContent = "";
    (review.non_goals || []).forEach((value) => addText(limitations, "p", "", value));
    const technical = {
      contract_id: review.contract_id,
      contract_version: review.contract_version,
      confirmation_fingerprint: review.confirmation_fingerprint,
      criteria: (agreement.criteria || []).map((criterion) => ({id: criterion.id, proposal_id: criterion.proposal_id, planning_item_id: criterion.planning_item_id, a4_plan_id: criterion.a4_plan_id, a4_plan_version: criterion.a4_plan_version, a4_plan_sha256: criterion.a4_plan_sha256, source_id: criterion.source_id, source_content_sha256: criterion.source_content_sha256, adapter: criterion.adapter, adapter_version: criterion.adapter_version, evidence_profile: criterion.evidence_profile, evidence_binding: criterion.evidence_binding}))
    };
    document.querySelector("#review-technical").textContent = JSON.stringify(technical, null, 2);
    const done = review.status !== "PENDING";
    form.hidden = done;
    result.hidden = !done;
    if (done) {
      document.querySelector("#review-result-heading").textContent = review.status === "CONFIRMED" ? "Agreement confirmed" : review.status === "CHANGES_REQUESTED" ? "Changes requested" : "Review link expired";
      document.querySelector("#review-result-copy").textContent = review.status === "CHANGES_REQUESTED" ? "This version is terminal and cannot be frozen. Return to the agreement workspace for a changed successor." : "This review capability cannot authorize downstream work.";
      const returnUrl = review.local_demo?.return_url;
      document.querySelector("#return-to-agreement").href = typeof returnUrl === "string" && /^\/app\/pocs\/poc_[a-z0-9][a-z0-9_-]{2,63}\/agreement$/.test(returnUrl) ? returnUrl : "/app";
    }
  }
  async function initialise() {
    if (!token || !TOKEN.test(token)) { setError("This customer review link is invalid."); task.setAttribute("aria-busy", "false"); return; }
    try { render(await getJson()); task.setAttribute("aria-busy", "false"); } catch { setError("This customer review link is invalid, stale, or unavailable."); task.setAttribute("aria-busy", "false"); }
  }
  form.addEventListener("submit", async (event) => {
    event.preventDefault(); setError("");
    const decision = event.submitter?.dataset.decision;
    const acknowledged = document.querySelector("#agreement-checkbox").checked;
    const rationale = document.querySelector("#review-rationale").value.trim();
    if (!["CONFIRM", "REQUEST_CHANGES"].includes(decision) || !acknowledged || !rationale) { setError("A checked acknowledgement and rationale are required."); return; }
    [...form.querySelectorAll("button")].forEach((button) => { button.disabled = true; });
    try { const payload = await postJson({review_id: review.review_id, contract_id: review.contract_id, contract_version: review.contract_version, confirmation_fingerprint: review.confirmation_fingerprint, decision, agreement_acknowledged: true, rationale, idempotency_key: key()}); render(payload); } catch (error) { setError(error.message || "The decision was refused safely."); [...form.querySelectorAll("button")].forEach((button) => { button.disabled = false; }); }
  });
  initialise();
})();
