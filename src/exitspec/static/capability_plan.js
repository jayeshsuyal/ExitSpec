(() => {
  "use strict";
  const POC_ID_PATTERN = /^poc_[a-z0-9][a-z0-9_-]{2,63}$/;
  const PROPOSAL_ID_PATTERN = /^prop_[a-z0-9][a-z0-9_-]{7,95}$/;
  const ROUTE = /^\/app\/pocs\/(poc_[a-z0-9][a-z0-9_-]{2,63})\/capability-plan$/;
  const route = window.location.search === "" && window.location.hash === "" ? window.location.pathname.match(ROUTE) : null;
  const pocId = route && POC_ID_PATTERN.test(route[1]) ? route[1] : null;
  const pocApi = pocId ? `/api/pocs/${pocId}` : null;
  const planApi = pocApi ? `${pocApi}/capability-plan` : null;
  const convergenceApi = planApi ? `${planApi}/converge` : null;
  const retainedApi = pocApi ? `${pocApi}/retained-proposals` : null;
  const task = document.querySelector("#capability-current-task");
  const records = document.querySelector("#planning-records");
  const form = document.querySelector("#planning-form");
  const submit = document.querySelector("#planning-submit");
  const status = document.querySelector("#planner-status");
  const errorPanel = document.querySelector("#capability-plan-error");
  const resultPanel = document.querySelector("#planning-result");
  const openAgreement = document.querySelector("#open-agreement");
  const RETAINED_PROPOSAL_KEYS = ["schema_version", "poc_id", "proposal_id", "authoring_receipt_id", "authoring_result_id", "source_receipt_id", "source_id", "source_kind", "source_content_sha256", "source_revision", "source_adapter_name", "source_adapter_version", "redaction_policy_version", "proposal_key", "source_quote", "normalized_claim", "numeric_facts", "retention_state", "reviewer", "rationale", "decided_at"];
  const SOURCE_KINDS = new Set(["EMAIL", "MEETING", "DOCUMENT", "EXISTING_CONTRACT"]);
  const RETAINED_RECEIPT = /^arcp_[a-f0-9]{32}$/;
  const RETAINED_RESULT = /^ares_[a-f0-9]{32}$/;
  const RETAINED_SOURCE_RECEIPT = /^srcpt_[a-z0-9][a-z0-9_-]{7,95}$/;
  const RETAINED_SOURCE = /^src_[a-z0-9][a-z0-9_-]{2,63}$/;
  const RETAINED_PROPOSAL_KEY = /^[a-z0-9][a-z0-9_.-]{0,63}$/;
  let retained = [];
  let registry = [];

  function exactKeys(value, keys) { return Boolean(value && typeof value === "object" && !Array.isArray(value) && Object.keys(value).sort().join("|") === [...keys].sort().join("|")); }
  function safeText(value, max) { return typeof value === "string" && value.trim() && value.length <= max && !/[\u0000-\u001f\u007f]/.test(value); }
  function safePath(value) { if (typeof value !== "string") return false; try { const parsed = new URL(value, window.location.origin); return parsed.origin === window.location.origin && parsed.pathname === value && !parsed.search && !parsed.hash && [pocApi, planApi, convergenceApi, retainedApi].includes(value); } catch { return false; } }
  async function getJson(path) {
    if (!safePath(path)) throw new Error("untrusted path");
    const response = await fetch(path, {cache: "no-store", credentials: "same-origin", headers: {Accept: "application/json"}});
    if (!response.ok) throw new Error("request failed");
    const payload = await response.json();
    if (!payload || typeof payload !== "object" || Array.isArray(payload)) throw new Error("invalid response");
    return payload;
  }
  function newKey() { return `capability-plan-${window.crypto?.randomUUID?.() || Math.random().toString(16).slice(2)}`; }
  function field(label, name, value, wide = false) {
    const wrapper = document.createElement("label");
    if (wide) wrapper.className = "wide";
    wrapper.textContent = label;
    const input = document.createElement("input"); input.name = name; input.value = value ?? ""; input.maxLength = name === "measurement_population" ? 300 : 200; input.autocomplete = "off";
    wrapper.appendChild(input); return wrapper;
  }
  function selectField(label, name, options, value) {
    const wrapper = document.createElement("label"); wrapper.textContent = label;
    const select = document.createElement("select"); select.name = name;
    options.forEach((option) => { const item = document.createElement("option"); item.value = option.value; item.textContent = option.label; item.dataset.registry = option.registry || ""; select.appendChild(item); });
    select.value = value || options[0]?.value || ""; wrapper.appendChild(select); return wrapper;
  }
  function registryOptions() { return [{value: "", label: "Choose proof method"}, {value: "unsupported_capability", label: "Unsupported boundary"}, ...registry.map((entry) => ({value: entry.capability_key, label: entry.label, registry: "true"}))]; }
  function setCriterionAvailability(row, enabled) { ["rule", "operator", "threshold", "unit", "measurement_population", "evidence_method", "adapter", "adapter_version", "evidence_profile"].forEach((name) => { const input = row.querySelector(`[name="${name}"]`); if (input) input.disabled = !enabled; }); }
  function resetOperator(row) { const operator = row.querySelector('[name="operator"]'); if (!operator) return; operator.textContent = ""; const placeholder = document.createElement("option"); placeholder.value = ""; placeholder.textContent = "Choose operator"; operator.appendChild(placeholder); }
  function clearCriterion(row) {
    ["rule", "unit", "measurement_population", "evidence_method", "adapter", "adapter_version", "evidence_profile"].forEach((name) => { const input = row.querySelector(`[name="${name}"]`); if (input) input.value = ""; });
    resetOperator(row);
    const threshold = row.querySelector('[name="threshold"]'); if (threshold) { threshold.value = ""; threshold.readOnly = false; }
    const provenance = row.querySelector('[name="provenance"]'); if (provenance) provenance.value = "";
  }
  function fillFromRegistry(row, key) {
    clearCriterion(row);
    const entry = registry.find((candidate) => candidate.capability_key === key); if (!entry) { setCriterionAvailability(row, false); return; }
    ["rule", "unit", "measurement_population", "evidence_method", "adapter", "adapter_version", "evidence_profile"].forEach((name) => { const input = row.querySelector(`[name="${name}"]`); if (input) input.value = entry[name] || ""; });
    const operator = row.querySelector('[name="operator"]'); if (operator) { operator.textContent = ""; const placeholder = document.createElement("option"); placeholder.value = ""; placeholder.textContent = "Choose operator"; operator.appendChild(placeholder); (entry.allowed_operators || []).forEach((value) => { const option = document.createElement("option"); option.value = value; option.textContent = value; operator.appendChild(option); }); operator.value = ""; }
    const provenance = row.querySelector('[name="provenance"]'); if (provenance) provenance.value = "HUMAN_DECLARED";
    setCriterionAvailability(row, true);
  }
  function isTrustedNumericFacts(value) {
    if (value === null) return true;
    if (!exactKeys(value, ["threshold", "minimum_samples"])) return false;
    const thresholdValid = value.threshold === null || (typeof value.threshold === "number" && Number.isFinite(value.threshold) && value.threshold >= 0 && value.threshold <= 1);
    const samplesValid = value.minimum_samples === null || (Number.isSafeInteger(value.minimum_samples) && value.minimum_samples > 0);
    return thresholdValid && samplesValid && (value.threshold !== null || value.minimum_samples !== null);
  }
  function isTrustedRetainedProposal(item, expectedPocId) {
    return exactKeys(item, RETAINED_PROPOSAL_KEYS) && item.schema_version === "exitspec.retained-proposal-projection.v1" && item.poc_id === expectedPocId && PROPOSAL_ID_PATTERN.test(item.proposal_id) && RETAINED_RECEIPT.test(item.authoring_receipt_id) && RETAINED_RESULT.test(item.authoring_result_id) && RETAINED_SOURCE_RECEIPT.test(item.source_receipt_id) && RETAINED_SOURCE.test(item.source_id) && SOURCE_KINDS.has(item.source_kind) && /^[a-f0-9]{64}$/.test(item.source_content_sha256) && Number.isInteger(item.source_revision) && item.source_revision >= 1 && safeText(item.source_adapter_name, 64) && safeText(item.source_adapter_version, 64) && safeText(item.redaction_policy_version, 64) && RETAINED_PROPOSAL_KEY.test(item.proposal_key) && safeText(item.source_quote, 4000) && safeText(item.normalized_claim, 2000) && isTrustedNumericFacts(item.numeric_facts) && item.retention_state === "KEEP_FOR_CONTRACT" && safeText(item.reviewer, 160) && safeText(item.rationale, 2000) && safeText(item.decided_at, 64) && Number.isFinite(Date.parse(item.decided_at));
  }
  function render() {
    records.textContent = "";
    if (!retained.length) { const empty = document.createElement("p"); empty.className = "planning-placeholder"; empty.textContent = "Approve a request before defining how to prove it."; records.appendChild(empty); submit.disabled = true; return; }
    retained.forEach((proposal, index) => {
      const row = document.createElement("article"); row.className = "planning-row"; row.dataset.proposalId = proposal.proposal_id;
      const claim = document.createElement("section"); claim.className = "planning-claim";
      const heading = document.createElement("h3"); heading.textContent = `Claim ${index + 1}`;
      const text = document.createElement("p"); text.textContent = proposal.normalized_claim;
      const id = document.createElement("small"); id.textContent = `${proposal.proposal_id} · ${proposal.source_receipt_id}`; claim.append(heading, text, id);
      const inputs = document.createElement("section"); inputs.className = "planning-inputs";
      const scope = selectField("Scope", "scope", [{value: "", label: "Choose scope"}, {value: "MUST_HAVE", label: "Must have"}, {value: "ADVISORY", label: "Advisory"}], "");
      const capability = selectField("Proof method", "capability_key", registryOptions(), "");
      capability.querySelector("select").addEventListener("change", (event) => fillFromRegistry(row, event.target.value));
      const provenance = selectField("Threshold source", "provenance", [{value: "", label: "Server-bound after selection"}, {value: "HUMAN_DECLARED", label: "Human-declared"}], "");
      provenance.querySelector("select").disabled = true;
      inputs.append(scope, capability, selectField("Operator", "operator", [{value: "", label: "Choose operator"}], ""), field("Threshold", "threshold", ""), provenance);
      const serverOwned = document.createElement("div"); serverOwned.hidden = true; serverOwned.setAttribute("aria-hidden", "true");
      serverOwned.append(field("Rule", "rule", ""), field("Unit", "unit", ""), field("Population", "measurement_population", ""), field("Evidence method", "evidence_method", ""), field("Adapter", "adapter", ""), field("Adapter version", "adapter_version", ""), field("Evidence profile", "evidence_profile", ""));
      inputs.append(serverOwned);
      const human = document.createElement("section"); human.className = "planning-inputs"; human.append(field("Named reviewer", "reviewer", "", true), field("Rationale", "rationale", "", true));
      const exclusion = document.createElement("label"); exclusion.className = "planning-exclusion"; const check = document.createElement("input"); check.type = "checkbox"; check.name = "explicit_exclusion"; check.addEventListener("change", () => { const selectedCapability = capability.querySelector("select").value; if (check.checked) { clearCriterion(row); setCriterionAvailability(row, false); } else { fillFromRegistry(row, selectedCapability); } }); exclusion.append(check, document.createTextNode("Explicitly exclude in this named, rationale-bound plan version")); human.append(exclusion);
      row.append(claim, inputs, human); records.appendChild(row);
      setCriterionAvailability(row, false);
    });
    submit.disabled = false; status.textContent = `${retained.length} approved request${retained.length === 1 ? "" : "s"} need a definition each.`;
  }
  function collect() {
    return [...records.querySelectorAll(".planning-row")].map((row) => {
      const value = (name) => row.querySelector(`[name="${name}"]`)?.value.trim() || null;
      const thresholdText = value("threshold");
      const threshold = thresholdText === null ? null : Number(thresholdText);
      return {proposal_id: row.dataset.proposalId, scope: value("scope"), capability_key: value("capability_key"), operator: value("operator"), threshold: Number.isNaN(threshold) ? null : threshold, reviewer: value("reviewer") || "", rationale: value("rationale") || "", explicit_exclusion: Boolean(row.querySelector('[name="explicit_exclusion"]')?.checked)};
    });
  }
  function clearError() { errorPanel.hidden = true; errorPanel.textContent = ""; }
  function renderResult(plan) {
    resultPanel.hidden = false; document.querySelector("#planning-result-heading").textContent = "Plan created";
    const ready = document.querySelector("#ready-for-agreement"); ready.textContent = plan.ready_for_agreement ? "READY FOR NEXT REVIEW" : "NOT READY"; ready.dataset.ready = String(plan.ready_for_agreement);
    openAgreement.hidden = !plan.ready_for_agreement;
    if (plan.ready_for_agreement && pocId) openAgreement.href = `/app/pocs/${encodeURIComponent(pocId)}/agreement`;
    document.querySelector("#planning-result-summary").textContent = `${plan.records.length} requests remain visible. No POC has run yet.`;
    const output = document.querySelector("#planning-result-records"); output.textContent = "";
    plan.records.forEach((record) => { const item = document.createElement("article"); item.className = "result-record"; item.dataset.disposition = record.disposition; const title = document.createElement("strong"); title.textContent = `${record.proposal_id} · ${record.scope} · ${record.disposition}`; const reason = document.createElement("span"); reason.textContent = record.reason; const next = document.createElement("small"); next.textContent = `Next action: ${record.next_action}`; item.append(title, reason, next); output.appendChild(item); });
    form.hidden = true; task.setAttribute("aria-busy", "false"); resultPanel.focus();
  }
  async function initialise() {
    if (!pocId) { errorPanel.textContent = "This capability-plan address is invalid."; errorPanel.hidden = false; task.setAttribute("aria-busy", "false"); return; }
    try {
      const [draft, retainedPayload, planner] = await Promise.all([getJson(pocApi), getJson(retainedApi), getJson(planApi)]);
      if (draft.poc_id !== pocId || draft.archive_state !== "ACTIVE" || !Array.isArray(retainedPayload.retained_proposals) || planner.poc_id !== pocId || !Array.isArray(planner.registry) || !retainedPayload.retained_proposals.every((item) => isTrustedRetainedProposal(item, pocId))) throw new Error("untrusted data");
      document.querySelector("#poc-title").textContent = draft.display_name; document.querySelector("#poc-context").textContent = `${draft.customer_label} · approved requests`;
      retained = retainedPayload.retained_proposals.slice(); registry = planner.registry; render(); task.setAttribute("aria-busy", "false");
    } catch { retained = []; registry = []; records.textContent = ""; submit.disabled = true; errorPanel.textContent = "The approved requests or proof methods could not be verified. No plan was created."; errorPanel.hidden = false; task.setAttribute("aria-busy", "false"); }
  }
  form.addEventListener("submit", async (event) => { event.preventDefault(); clearError(); submit.disabled = true; status.textContent = "Creating the immutable process-local plan…"; try { const response = await fetch(convergenceApi, {method: "POST", cache: "no-store", credentials: "same-origin", headers: {Accept: "application/json", "Content-Type": "application/json", Origin: window.location.origin}, body: JSON.stringify({items: collect(), idempotency_key: newKey()})}); const payload = await response.json().catch(() => null); if (!response.ok || !payload?.plan) throw new Error("plan refused"); renderResult(payload.plan); } catch { errorPanel.textContent = "The plan was refused safely. Check each named field and retry; no downstream object was created."; errorPanel.hidden = false; submit.disabled = false; status.textContent = "Planning did not complete."; } });
  initialise();
})();
