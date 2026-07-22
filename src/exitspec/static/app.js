(() => {
  "use strict";

  const API = {
    state: "/api/state",
    intake: "/api/intake",
    review: "/api/review",
    customerDraft: "/api/customer-draft",
    prove: "/api/prove",
    reset: "/api/reset",
  };

  const DEFAULT_DEMO_NOTES = "Customer: Our support agent must select the correct tool at least 95% of the time. We want to inspect any mistakes before we scale traffic.";

  let state = null;
  let selectedScenario = "pass";

  const $ = (selector) => document.querySelector(selector);
  const modeChip = $("#mode-chip");
  const intakeStatus = $("#intake-status");
  const proveStatus = $("#prove-status");

  function setMode(mode, message) {
    modeChip.textContent = message;
    modeChip.className = `mode-chip ${mode}`;
  }

  function setStatus(element, message) {
    element.textContent = message || "";
  }

  async function request(path, options = {}) {
    const response = await fetch(path, {
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
      ...options,
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(body.error || `Request failed with ${response.status}.`);
    }
    return body;
  }

  function applyState(payload) {
    const incoming = payload && (payload.state || payload);
    if (!incoming || typeof incoming !== "object") {
      throw new Error("The local demo returned an invalid state.");
    }
    state = incoming;
  }

  function drafts() {
    return state && Array.isArray(state.drafts) ? state.drafts : [];
  }

  function transcriptLines() {
    if (!state || !state.transcript || !Array.isArray(state.transcript.lines)) {
      return [];
    }
    return state.transcript.lines;
  }

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>'"]/g, (character) => {
      const entities = { "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" };
      return entities[character];
    });
  }

  function percentage(value) {
    return typeof value === "number" ? `${(value * 100).toFixed(2)}%` : "—";
  }

  function tagClass(status) {
    return String(status || "needs-review").toLowerCase().replace(/_/g, "-");
  }

  function sourceDescription(draft) {
    const span = draft.source_span;
    if (!span) {
      return draft.human_added ? "Human-added requirement" : "Source not available";
    }
    const lineRange = span.start_line === span.end_line ? span.start_line : `${span.start_line}–${span.end_line}`;
    return `${span.speaker} · line ${lineRange}`;
  }

  function sourceQuote(draftOrCriterion) {
    const source = draftOrCriterion && (draftOrCriterion.source || draftOrCriterion.source_span);
    return source && source.quote ? `“${source.quote}”` : "No customer quote is attached yet.";
  }

  function criterionSummary(criterion) {
    if (!criterion) {
      return "No complete measurement rule exists yet.";
    }
    const rule = criterion.rule || {};
    return `${String(criterion.metric || "measurement").replaceAll("_", " ")} ≥ ${percentage(rule.threshold)} on at least ${rule.minimum_samples || "—"} samples; ${String(rule.confidence_method || "confidence rule").replaceAll("_", " ")}.`;
  }

  function reviewSummary(draft) {
    if (!draft.review) {
      return "Awaiting explicit human review.";
    }
    return `${draft.review.decision} by ${draft.review.reviewer}: ${draft.review.rationale}`;
  }

  function renderTranscript() {
    $("#transcript-title").textContent = state?.transcript?.title || "Discovery transcript";
    const lines = $("#transcript-lines");
    lines.innerHTML = transcriptLines()
      .map((line) => `
        <li>
          <span class="line-number">${escapeHtml(line.line_number)}</span>
          <div class="line-copy">
            <span class="speaker">${escapeHtml(line.speaker)}</span>
            <p>${escapeHtml(line.text)}</p>
          </div>
        </li>`)
      .join("");
  }

  function candidateActions(draft) {
    if (draft.status !== "NEEDS_REVIEW") {
      return "";
    }
    const complete = draft.proposed_criterion && (!draft.open_questions || draft.open_questions.length === 0);
    const approve = complete
      ? `<button class="button primary" type="button" data-decision="APPROVE" data-id="${escapeHtml(draft.id)}">Approve exact rule</button>`
      : `<p class="review-constraint">This cannot be approved: the metric or evidence rule is still incomplete.</p>`;
    return `
      <div class="candidate-actions">
        ${approve}
        <button class="button danger" type="button" data-decision="REJECT" data-id="${escapeHtml(draft.id)}">Reject incomplete request</button>
      </div>`;
  }

  function renderCandidates() {
    const currentDrafts = drafts();
    const pending = currentDrafts.filter((draft) => draft.status === "NEEDS_REVIEW").length;
    $("#review-count").textContent = pending ? `${pending} needs review` : "review complete";

    $("#candidate-list").innerHTML = currentDrafts
      .map((draft) => {
        const criterion = draft.proposed_criterion;
        const detail = criterionSummary(criterion);
        const questions = (draft.open_questions || []).map((question) => `<li>${escapeHtml(question)}</li>`).join("");
        return `
          <article class="candidate">
            <div class="candidate-top">
              <h4>${escapeHtml(criterion?.title || "Unresolved customer request")}</h4>
              <span class="status-tag ${tagClass(draft.status)}">${escapeHtml(draft.status)}</span>
            </div>
            <p class="requirement">${escapeHtml(draft.normalized_claim)}</p>
            <p>${escapeHtml(detail)}</p>
            ${questions ? `<ul class="open-questions">${questions}</ul>` : ""}
            <p class="candidate-meta">${escapeHtml(sourceDescription(draft))} · ${escapeHtml(reviewSummary(draft))}</p>
            ${candidateActions(draft)}
          </article>`;
      })
      .join("");

    $("#candidate-list").querySelectorAll("[data-decision]").forEach((button) => {
      button.addEventListener("click", () => reviewDraft(button.dataset.id, button.dataset.decision));
    });
  }

  function renderActions() {
    const ready = Boolean(state?.ready_to_prove);
    const customerDraftButton = $("#create-customer-draft");
    customerDraftButton.disabled = !ready;
    customerDraftButton.title = ready ? "" : "Resolve each candidate before preparing a customer draft.";

    const runButton = $("#run-proof");
    runButton.disabled = !ready;
    runButton.title = ready ? "" : "Resolve each candidate before running a proof.";

    const draftLink = $("#customer-draft-link");
    if (state?.customer_draft_url) {
      draftLink.href = state.customer_draft_url;
      draftLink.hidden = false;
    } else {
      draftLink.hidden = true;
    }

    const proofLink = $("#proof-pack-link");
    if (state?.proof_pack?.report_url) {
      proofLink.href = state.proof_pack.report_url;
      proofLink.hidden = false;
    } else {
      proofLink.hidden = true;
    }

    const contract = state?.contract;
    $("#contract-chip").textContent = contract
      ? state?.proof_pack
        ? `v${contract.version} · frozen run`
        : `v${contract.version} · approved`
      : "Awaiting review";
  }

  function renderProof() {
    const proof = state?.proof_pack;
    const verdict = $("#proof-verdict");
    const criterion = state?.contract?.criteria?.[0] || drafts().find((draft) => draft.proposed_criterion)?.proposed_criterion;
    const source = criterion?.source ? `“${criterion.source.quote}”` : sourceQuote(drafts()[0]);

    if (!proof) {
      verdict.className = "verdict-badge";
      verdict.textContent = "AWAITING";
      $("#metric-row").innerHTML = [
        ["Fixed cases", "—"],
        ["95% lower bound", "—"],
        ["Agreed target", criterion?.rule ? percentage(criterion.rule.threshold) : "—"],
      ].map(([label, value]) => `<div class="metric"><span>${label}</span><strong>${escapeHtml(value)}</strong></div>`).join("");
      $("#evidence-explanation").textContent = state?.ready_to_prove
        ? "The reviewed contract is ready. Choose a scenario and run the real deterministic evidence chain."
        : "Finish human review first. ExitSpec refuses to prove a POC while a visible requirement is unresolved.";
      $("#evidence-footnote").textContent = "No evidence run has been created.";
      $("#pack-source").textContent = source;
      $("#pack-criterion").textContent = criterion ? criterionSummary(criterion) : "Human review is still required.";
      $("#pack-contract").textContent = contractLabel();
      $("#pack-result").textContent = "No evidence result yet.";
      $("#pack-result").style.color = "var(--muted)";
      $("#pack-next-step").textContent = state?.ready_to_prove
        ? "Prepare the customer review draft or run the agreed proof."
        : "Resolve the visible candidate requirements with a named human reviewer.";
      $("#pack-id").textContent = "LOCAL / SYNTHETIC";
      return;
    }

    const verdictClass = tagClass(proof.overall_verdict);
    verdict.className = `verdict-badge ${verdictClass}`;
    verdict.textContent = proof.overall_verdict;
    $("#metric-row").innerHTML = [
      ["Fixed cases", `${proof.sample_count || 0} / ${criterion?.rule?.minimum_samples || "—"}`],
      ["Observed rate", percentage(proof.observed_rate)],
      ["95% lower bound", percentage(proof.confidence_lower_bound)],
    ].map(([label, value]) => `<div class="metric"><span>${label}</span><strong>${escapeHtml(value)}</strong></div>`).join("");
    $("#evidence-explanation").textContent = proof.criterion_reason;
    $("#evidence-footnote").textContent = proof.next_human_action;
    $("#pack-source").textContent = source;
    $("#pack-criterion").textContent = criterion ? criterionSummary(criterion) : "Frozen criterion is available in the full Proof Pack.";
    $("#pack-contract").textContent = proof.contract_hash || contractLabel();
    $("#pack-result").textContent = `${proof.overall_verdict} — ${percentage(proof.observed_rate)} observed; lower bound ${percentage(proof.confidence_lower_bound)}.`;
    $("#pack-result").style.color = proof.overall_verdict === "PASS" ? "var(--green)" : proof.overall_verdict === "BLOCKED" ? "var(--red)" : "var(--amber)";
    $("#pack-next-step").textContent = proof.next_human_action;
    $("#pack-id").textContent = "EVIDENCE / VERIFIED";
  }

  function contractLabel() {
    if (!state?.contract) {
      return "Waiting for human review";
    }
    return state.contract.canonical_hash || `Approved ${state.contract.id} v${state.contract.version}; hash is created when proving starts.`;
  }

  function render() {
    renderTranscript();
    renderCandidates();
    renderActions();
    renderProof();
  }

  async function reviewDraft(draftId, decision) {
    const isApprove = decision === "APPROVE";
    setStatus(intakeStatus, isApprove ? "Recording the field engineer’s explicit approval…" : "Recording the field engineer’s explicit rejection…");
    try {
      const response = await request(API.review, {
        method: "POST",
        body: JSON.stringify({
          draft_id: draftId,
          decision,
          reviewer: "field_engineer",
          rationale: isApprove
            ? "Field engineer confirmed the complete measurement rule during this local demo."
            : "Field engineer rejected the request because its acceptance rule remains incomplete.",
        }),
      });
      applyState(response);
      setStatus(intakeStatus, "Review recorded. ExitSpec kept the complete and incomplete requirements visibly distinct.");
      render();
    } catch (error) {
      setStatus(intakeStatus, error.message);
    }
  }

  async function loadIntake() {
    const transcript = $("#meeting-notes").value.trim();
    if (!transcript) {
      setStatus(intakeStatus, "Paste synthetic meeting notes before capturing a transcript.");
      return;
    }
    setStatus(intakeStatus, "Capturing a source-linked synthetic transcript…");
    try {
      const response = await request(API.intake, {
        method: "POST",
        body: JSON.stringify({ transcript, title: "Pasted synthetic discovery notes" }),
      });
      applyState(response);
      setStatus(intakeStatus, response.notice || "Source notes captured. A human must still define a complete measurement rule.");
      render();
    } catch (error) {
      setStatus(intakeStatus, error.message);
    }
  }

  async function resetDemo() {
    try {
      applyState(await request(API.reset, { method: "POST", body: "{}" }));
      $("#meeting-notes").value = DEFAULT_DEMO_NOTES;
      setStatus(intakeStatus, "Restored the reproducible support-agent sample.");
      setStatus(proveStatus, "");
      render();
    } catch (error) {
      setStatus(intakeStatus, error.message);
    }
  }

  async function createCustomerDraft() {
    try {
      applyState(await request(API.customerDraft, { method: "POST", body: "{}" }));
      setStatus(intakeStatus, "Customer review draft is ready. It is a proposed test, not an authorization.");
      render();
    } catch (error) {
      setStatus(intakeStatus, error.message);
    }
  }

  async function runProof() {
    const selected = document.querySelector('input[name="scenario"]:checked');
    selectedScenario = selected ? selected.value : "pass";
    setStatus(proveStatus, "Running the deterministic evidence chain against the frozen contract…");
    try {
      applyState(await request(API.prove, { method: "POST", body: JSON.stringify({ scenario: selectedScenario }) }));
      setStatus(proveStatus, "Proof completed. Review the evidence—not just the verdict—before deciding what moves next.");
      render();
    } catch (error) {
      setStatus(proveStatus, error.message);
    }
  }

  async function initialise() {
    try {
      applyState(await request(API.state));
      setMode("live", "Local synthetic demo");
      render();
    } catch (error) {
      setMode("demo", "Synthetic demo state unavailable");
      setStatus(intakeStatus, "Start the local server with exitspec serve, then refresh this page.");
      setStatus(proveStatus, "The browser surface never fabricates a proof result when the local evidence engine is unavailable.");
    }
  }

  $("#load-transcript").addEventListener("click", loadIntake);
  $("#reset-demo").addEventListener("click", resetDemo);
  $("#create-customer-draft").addEventListener("click", createCustomerDraft);
  $("#run-proof").addEventListener("click", runProof);
  document.querySelectorAll('input[name="scenario"]').forEach((input) => {
    input.addEventListener("change", () => { selectedScenario = input.value; });
  });

  initialise();
})();
