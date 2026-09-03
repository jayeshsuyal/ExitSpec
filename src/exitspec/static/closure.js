(() => {
  "use strict";

  const SEEDED_POC_IDS = new Set([
    "poc_support_agent_demo",
    "poc_inference_latency_demo",
  ]);

  function installCompatibilityClosurePanel() {
    const current = document.querySelector("#closure-panel");
    if (current) {
      return current;
    }
    const decideView = document.querySelector("#decide");
    if (!decideView) {
      return null;
    }
    const created = document.createElement("section");
    created.className = "closure-panel";
    created.id = "closure-panel";
    created.setAttribute("aria-labelledby", "closure-heading");
    created.hidden = true;
    created.innerHTML = `
      <header class="closure-heading">
        <div>
          <p class="eyebrow">Step 3 of 3 · Prove</p>
          <h2 id="closure-heading">Complete the POC handoff</h2>
        </div>
        <p>Record the human outcome against this exact Evidence Pack.</p>
      </header>
      <form class="closure-form" id="closure-form">
        <label>
          Outcome
          <select id="closure-decision">
            <option value="HANDOFF_COMPLETED">Handoff completed</option>
            <option value="POC_STOPPED">POC stopped</option>
          </select>
        </label>
        <label>
          Reviewer
          <input id="closure-actor" maxlength="160" value="field_engineer" required />
        </label>
        <label>
          Rationale
          <textarea
            id="closure-rationale"
            maxlength="2000"
            rows="2"
            placeholder="Evidence Pack reviewed and handed to the customer POC owner."
            required
          ></textarea>
        </label>
        <button class="closure-submit" id="record-closure" type="submit">
          Record final decision
        </button>
        <p class="closure-boundary">
          <strong>Evidence is not authorization.</strong>
          Closing the POC never authorizes shipping or deployment.
        </p>
        <p
          class="closure-status"
          id="closure-status"
          role="status"
          aria-live="polite"
        ></p>
      </form>
      <article
        class="closure-receipt"
        id="closure-receipt"
        aria-label="Recorded POC closure"
        hidden
      >
        <dl>
          <div>
            <dt>Outcome</dt>
            <dd id="closure-receipt-decision">—</dd>
          </div>
          <div>
            <dt>Reviewer</dt>
            <dd id="closure-receipt-actor">—</dd>
          </div>
          <div>
            <dt>Recorded</dt>
            <dd id="closure-receipt-time">—</dd>
          </div>
          <div>
            <dt>Rationale</dt>
            <dd id="closure-receipt-rationale">—</dd>
          </div>
        </dl>
        <div class="closure-receipt-actions">
          <a
            class="closure-evidence-link"
            id="closure-evidence-link"
            target="_blank"
            rel="noopener"
            hidden
          >
            Evidence Pack
          </a>
          <a class="closure-dashboard-link" href="/app?filter=Completed">
            Completed POCs
          </a>
        </div>
      </article>
    `;
    decideView.append(created);
    return created;
  }

  const panel = installCompatibilityClosurePanel();
  const form = panel?.querySelector("#closure-form");
  if (!panel || !form) {
    return;
  }

  const POC_ID_PATTERN = /^poc_[a-z0-9][a-z0-9_-]{2,63}$/;
  const EVIDENCE_BINDING_FIELDS = Object.freeze([
    "poc_id",
    "contract_id",
    "contract_version",
    "contract_hash",
    "run_id",
    "verdict",
    "evidence_pack_url",
    "evidence_pack_sha256",
  ]);
  const RUN_BINDING_FIELDS = Object.freeze([
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
  ]);
  const pathMatch = window.location.pathname.match(
    /^\/app\/pocs\/(poc_[a-z0-9][a-z0-9_-]{2,63})(?:\/|$)/
  );
  const pocId = pathMatch?.[1] || "poc_support_agent_demo";
  const endpoint = `/api/workspace/pocs/${encodeURIComponent(pocId)}/closure`;
  const decisionInput = document.querySelector("#closure-decision");
  const actorInput = document.querySelector("#closure-actor");
  const rationaleInput = document.querySelector("#closure-rationale");
  const submitButton = document.querySelector("#record-closure");
  const status = document.querySelector("#closure-status");
  const receipt = document.querySelector("#closure-receipt");
  const receiptDecision = document.querySelector("#closure-receipt-decision");
  const receiptActor = document.querySelector("#closure-receipt-actor");
  const receiptTime = document.querySelector("#closure-receipt-time");
  const receiptRationale = document.querySelector("#closure-receipt-rationale");
  const evidenceLink = document.querySelector("#closure-evidence-link");
  const dashboardLink = document.querySelector(".closure-dashboard-link");
  let eligibleEvidenceBinding = null;
  let eligibleTerminalRunBinding = null;
  let inFlight = false;
  let refreshVersion = 0;
  let refreshTimer = null;
  const idempotencyKey = `closure-${pocId}-${
    typeof window.crypto?.randomUUID === "function"
      ? window.crypto.randomUUID()
      : `${Date.now()}-${Math.random().toString(16).slice(2)}`
  }`;

  function exactBinding(candidate) {
    if (!candidate || typeof candidate !== "object") {
      return null;
    }
    const keys = Object.keys(candidate).sort();
    const isEvidenceBinding =
      keys.join("|") === EVIDENCE_BINDING_FIELDS.slice().sort().join("|");
    const isRunBinding =
      keys.join("|") === RUN_BINDING_FIELDS.slice().sort().join("|");
    if (
      candidate.poc_id !== pocId ||
      !POC_ID_PATTERN.test(candidate.poc_id) ||
      !/^[a-f0-9]{64}$/.test(candidate.contract_hash)
    ) {
      return null;
    }
    if (
      isEvidenceBinding &&
      /^[a-f0-9]{64}$/.test(candidate.evidence_pack_sha256) &&
      ["PASS", "FAIL", "BLOCKED", "NOT_PROVEN"].includes(
        candidate.verdict
      ) &&
      candidate.evidence_pack_url.startsWith("/artifacts/") &&
      candidate.evidence_pack_url.endsWith("/decision-packet.html")
    ) {
      return Object.freeze(
        Object.fromEntries(
          EVIDENCE_BINDING_FIELDS.map((key) => [key, candidate[key]])
        )
      );
    }
    if (
      isRunBinding &&
      candidate.run_status === "BLOCKED" &&
      typeof candidate.reason_code === "string" &&
      candidate.reason_code.length > 0 &&
      candidate.reason_code.length <= 200 &&
      /^prun_[a-f0-9]{32}$/.test(candidate.operation_id) &&
      /^run_[a-f0-9]{32}$/.test(candidate.runner_run_id) &&
      /^[a-f0-9]{64}$/.test(candidate.runner_input_digest) &&
      !Number.isNaN(Date.parse(candidate.terminal_at)) &&
      /^[a-f0-9]{64}$/.test(candidate.run_receipt_sha256)
    ) {
      return Object.freeze(
        Object.fromEntries(
          RUN_BINDING_FIELDS.map((key) => [key, candidate[key]])
        )
      );
    }
    return null;
  }

  function hasEvidencePack(binding) {
    return Boolean(binding && "evidence_pack_url" in binding);
  }

  function eligibleBinding() {
    return eligibleEvidenceBinding || eligibleTerminalRunBinding;
  }

  function setStatus(message, tone = "") {
    status.textContent = message || "";
    status.dataset.tone = tone;
  }

  function setLifecycleClosed(closed) {
    const lifecycle = closed ? "closed" : "open";
    if (document.body.dataset.pocLifecycle === lifecycle) {
      return;
    }
    document.body.dataset.pocLifecycle = lifecycle;
    window.dispatchEvent(
      new CustomEvent("exitspec:closure-state", {
        detail: { pocId, closed },
      })
    );
  }

  function renderPostClosureDestination() {
    if (!dashboardLink) {
      return;
    }
    if (SEEDED_POC_IDS.has(pocId)) {
      dashboardLink.href = "/app/evidence";
      dashboardLink.textContent = "Evidence Packs";
      return;
    }
    dashboardLink.href = "/app?filter=Completed";
    dashboardLink.textContent = "Completed POCs";
  }

  function renderReceipt(closure) {
    if (!closure || typeof closure !== "object") {
      receipt.hidden = true;
      form.hidden = false;
      return;
    }
    form.hidden = true;
    receipt.hidden = false;
    setLifecycleClosed(true);
    renderPostClosureDestination();
    receiptDecision.textContent =
      closure.decision === "HANDOFF_COMPLETED"
        ? "Handoff completed"
        : "POC stopped";
    receiptActor.textContent = closure.decided_by || "Recorded reviewer";
    receiptTime.textContent = closure.recorded_at
      ? new Date(closure.recorded_at).toLocaleString()
      : "Recorded";
    receiptRationale.textContent = closure.rationale || "No rationale returned.";
    const binding =
      exactBinding(closure.evidence_binding) ||
      exactBinding(closure.terminal_run_binding);
    if (hasEvidencePack(binding)) {
      evidenceLink.href = binding.evidence_pack_url;
      evidenceLink.hidden = false;
    } else {
      evidenceLink.hidden = true;
    }
    setStatus(
      "POC lifecycle closed. Shipping remains a separate human decision.",
      "success"
    );
  }

  function renderPayload(payload) {
    const closure = payload?.closure;
    eligibleEvidenceBinding = exactBinding(
      payload?.eligible_evidence_binding
    );
    eligibleTerminalRunBinding = exactBinding(
      payload?.eligible_terminal_run_binding
    );
    if (closure) {
      panel.hidden = false;
      renderReceipt(closure);
      return;
    }
    setLifecycleClosed(false);
    receipt.hidden = true;
    form.hidden = false;
    panel.hidden = !(payload?.closeable && eligibleBinding());
    submitButton.disabled = panel.hidden || inFlight;
    if (!panel.hidden) {
      const handoffOption = decisionInput.querySelector(
        'option[value="HANDOFF_COMPLETED"]'
      );
      const evidenceAvailable = hasEvidencePack(eligibleEvidenceBinding);
      handoffOption.disabled = !evidenceAvailable;
      if (evidenceAvailable) {
        evidenceLink.href = eligibleEvidenceBinding.evidence_pack_url;
        evidenceLink.hidden = false;
        setStatus(
          "Choose the human outcome after reviewing the exact Evidence Pack."
        );
      } else {
        decisionInput.value = "POC_STOPPED";
        evidenceLink.hidden = true;
        setStatus(
          "This terminal run has no Evidence Pack. Review the bound run receipt and stop the POC."
        );
      }
    }
  }

  async function refreshClosure() {
    const version = ++refreshVersion;
    try {
      const response = await fetch(endpoint, {
        headers: { Accept: "application/json" },
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(payload.error || "Final decision state is unavailable.");
      }
      if (version === refreshVersion) {
        renderPayload(payload);
      }
    } catch (error) {
      if (version === refreshVersion && !panel.hidden) {
        submitButton.disabled = true;
        setStatus(
          error.message || "Final decision state is unavailable.",
          "error"
        );
      }
    }
  }

  function scheduleRefresh() {
    window.clearTimeout(refreshTimer);
    refreshTimer = window.setTimeout(refreshClosure, 80);
  }

  async function submitClosure(event) {
    event.preventDefault();
    if (inFlight || !eligibleBinding()) {
      return;
    }
    const decidedBy = actorInput.value.trim();
    const rationale = rationaleInput.value.trim();
    if (!decidedBy || !rationale) {
      setStatus("Add the reviewer and rationale before closing the POC.", "error");
      return;
    }

    inFlight = true;
    submitButton.disabled = true;
    setStatus("Recording the evidence-bound human decision…");
    try {
      const response = await fetch(endpoint, {
        method: "POST",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          decision: decisionInput.value,
          decided_by: decidedBy,
          rationale,
          ...(eligibleEvidenceBinding
            ? { evidence_binding: eligibleEvidenceBinding }
            : { terminal_run_binding: eligibleTerminalRunBinding }),
          idempotency_key: idempotencyKey,
        }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(payload.error || "The POC could not be closed safely.");
      }
      renderReceipt(payload.closure);
      window.dispatchEvent(
        new CustomEvent("exitspec:closure-recorded", {
          detail: { pocId },
        })
      );
    } catch (error) {
      submitButton.disabled = false;
      setStatus(
        error.message || "The POC could not be closed safely.",
        "error"
      );
    } finally {
      inFlight = false;
    }
  }

  form.addEventListener("submit", submitClosure);
  window.addEventListener("exitspec:evidence-updated", scheduleRefresh);
  refreshClosure();
})();
