(() => {
  "use strict";

  const POC_ID_PATTERN = /^poc_[a-z0-9][a-z0-9_-]{2,63}$/;
  const PROPOSAL_ID_PATTERN = /^prop_[a-z0-9][a-z0-9_-]{7,95}$/;
  const ROUTE_PATTERN =
    /^\/app\/pocs\/(poc_[a-z0-9][a-z0-9_-]{2,63})\/review$/;
  const SOURCE_KINDS = Object.freeze([
    "EMAIL",
    "MEETING",
    "DOCUMENT",
    "EXISTING_CONTRACT",
  ]);
  const SOURCE_LABELS = Object.freeze({
    EMAIL: "Email",
    MEETING: "Meeting transcript",
    DOCUMENT: "Notes or document",
    EXISTING_CONTRACT: "Existing contract",
  });
  const DECISIONS = Object.freeze([
    "KEEP_FOR_CONTRACT",
    "DISCARD",
  ]);
  const DECISION_DISPOSITIONS = Object.freeze([
    "CREATED",
    "IDEMPOTENT_REPLAY",
    "DECISION_REPLAY",
  ]);
  const ERROR_RATE_CUE = /\berror[\s-]*rate\b/i;
  const TTFT_CUE =
    /\b(?:ttft|time\s+to\s+(?:the\s+)?first\s+token|first[\s-]*token(?:\s+latency)?)\b/i;
  const routeMatch =
    window.location.search === "" && window.location.hash === ""
      ? window.location.pathname.match(ROUTE_PATTERN)
      : null;
  const pocId =
    routeMatch && POC_ID_PATTERN.test(routeMatch[1]) ? routeMatch[1] : null;
  const pocApi = pocId ? `/api/pocs/${pocId}` : null;
  const proposalsApi = pocApi ? `${pocApi}/proposals` : null;
  const assistedApi = pocApi ? `${pocApi}/assisted-authoring` : null;
  const stateApi = "/api/state";

  const currentTask = document.querySelector("#proposal-current-task");
  const form = document.querySelector("#proposal-decision-form");
  const reviewerInput = document.querySelector("#reviewer");
  const rationaleInput = document.querySelector("#rationale");
  const keepButton = document.querySelector("#keep-proposal");
  const discardButton = document.querySelector("#discard-proposal");
  const decisionStatus = document.querySelector("#decision-status");
  const errorPanel = document.querySelector("#proposal-review-error");
  const completionPanel = document.querySelector("#review-complete");
  const defineCriteriaLink = document.querySelector("#define-criteria");

  let proposals = [];
  let initialCount = 0;
  let keptCount = 0;
  let discardedCount = 0;
  const selectedMetricCues = new Set();
  let pocCustomerLabel = null;
  let a3Capability = false;
  let a3Mode = false;
  let inFlight = false;
  let pendingAttempt = null;

  class SafeRequestError extends Error {
    constructor(statusCode, retrySameAttempt) {
      super("Proposal review request failed.");
      this.name = "SafeRequestError";
      this.statusCode = statusCode;
      this.retrySameAttempt = retrySameAttempt;
    }
  }

  function decisionPath(proposalId) {
    if (
      !proposalsApi ||
      typeof proposalId !== "string" ||
      !PROPOSAL_ID_PATTERN.test(proposalId)
    ) {
      return null;
    }
    return `${proposalsApi}/${proposalId}/decision`;
  }

  function isTrustedApiPath(value) {
    if (
      !pocId ||
      typeof value !== "string" ||
      value.includes("?") ||
      value.includes("#")
    ) {
      return false;
    }
    try {
      const parsed = new URL(value, window.location.origin);
      if (
        parsed.origin !== window.location.origin ||
        parsed.pathname !== value ||
        parsed.search !== "" ||
        parsed.hash !== ""
      ) {
        return false;
      }
      if (
        value === stateApi ||
        value === pocApi ||
        value === proposalsApi ||
        value === assistedApi
      ) {
        return true;
      }
      return proposals.some(
        (proposal) => value === decisionPath(proposal.proposal_id)
      );
    } catch {
      return false;
    }
  }

  function hasExactKeys(payload, keys) {
    if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
      return false;
    }
    const actual = Object.keys(payload).sort();
    const expected = [...keys].sort();
    return (
      actual.length === expected.length &&
      actual.every((key, index) => key === expected[index])
    );
  }

  function isSafeBoundedText(value, maximum) {
    return (
      typeof value === "string" &&
      value.trim().length > 0 &&
      value.length <= maximum &&
      !/[\u0000-\u0008\u000b\u000c\u000e-\u001f]/.test(value)
    );
  }

  function isTrustedDraft(payload) {
    return Boolean(
      payload &&
        typeof payload === "object" &&
        !Array.isArray(payload) &&
        payload.poc_id === pocId &&
        isSafeBoundedText(payload.display_name, 160) &&
        isSafeBoundedText(payload.customer_label, 160) &&
        payload.archive_state === "ACTIVE"
    );
  }

  function isTrustedA3Capability(payload) {
    const safetyKeys = [
      "may_approve",
      "may_confirm",
      "may_execute",
      "may_freeze",
      "may_issue_evidence",
      "may_issue_verdict",
      "source_authority",
    ];
    return Boolean(
      hasExactKeys(payload, ["mode", "safety"]) &&
        payload.mode === "local_source_neutral" &&
        hasExactKeys(payload.safety, safetyKeys) &&
        payload.safety.source_authority === "UNTRUSTED_SOURCE_ONLY" &&
        safetyKeys
          .filter((key) => key !== "source_authority")
          .every((key) => payload.safety[key] === false)
    );
  }

  function isTrustedLegacyCapability(payload) {
    return Boolean(
      payload &&
        typeof payload === "object" &&
        !Array.isArray(payload) &&
        payload.mode === "local_synthetic_demo" &&
        hasExactKeys(payload.safety, [
          "authorization",
          "provider_calls",
          "synthetic_only",
        ]) &&
        payload.safety.synthetic_only === true &&
        typeof payload.safety.provider_calls === "boolean" &&
        payload.safety.authorization ===
          "ExitSpec proves evidence; humans retain every approval decision."
    );
  }

  function isTrustedProposal(proposal) {
    return Boolean(
      hasExactKeys(proposal, [
        "normalized_claim",
        "proposal_id",
        "source_receipt_id",
        "source_kind",
        "source_quote",
        "review_state",
      ]) &&
        PROPOSAL_ID_PATTERN.test(proposal.proposal_id) &&
        /^srcpt_[a-z0-9][a-z0-9_-]{7,95}$/.test(
          proposal.source_receipt_id
        ) &&
        SOURCE_KINDS.includes(proposal.source_kind) &&
        isSafeBoundedText(proposal.source_quote, 4000) &&
        isSafeBoundedText(proposal.normalized_claim, 2000) &&
        proposal.review_state === "NEEDS_REVIEW"
    );
  }

  function isTrustedReviewSummary(summary) {
    if (
      !hasExactKeys(summary, [
        "discarded",
        "kept_for_contract",
        "needs_review",
        "total",
      ])
    ) {
      return false;
    }
    const counts = [
      summary.discarded,
      summary.kept_for_contract,
      summary.needs_review,
      summary.total,
    ];
    return Boolean(
      counts.every(
        (count) => Number.isInteger(count) && count >= 0 && count <= 1024
      ) &&
        summary.total ===
          summary.discarded +
            summary.kept_for_contract +
            summary.needs_review
    );
  }

  function isTrustedProposalList(payload) {
    if (
      !hasExactKeys(payload, ["poc_id", "proposals", "review_summary"]) ||
      payload.poc_id !== pocId ||
      !Array.isArray(payload.proposals) ||
      payload.proposals.length > 1024 ||
      !payload.proposals.every(isTrustedProposal) ||
      !isTrustedReviewSummary(payload.review_summary) ||
      payload.review_summary.needs_review !== payload.proposals.length
    ) {
      return false;
    }
    const proposalIds = payload.proposals.map(
      (proposal) => proposal.proposal_id
    );
    return new Set(proposalIds).size === proposalIds.length;
  }

  function sourceReceiptIdForSourceId(sourceId) {
    return typeof sourceId === "string" && sourceId.startsWith("src_")
      ? `srcpt_${sourceId.slice(4)}`
      : null;
  }

  function isTrustedAssistedReceipt(receipt) {
    return Boolean(
      hasExactKeys(receipt, [
        "authoring_adapter_name",
        "authoring_adapter_version",
        "authoring_receipt_id",
        "authoring_result_id",
        "endpoint",
        "generated_at",
        "idempotent_replay",
        "model",
        "poc_id",
        "proposal_count",
        "proposal_ids",
        "provider",
        "redaction_policy_version",
        "schema_version",
        "source_adapter_name",
        "source_adapter_version",
        "source_content_sha256",
        "source_id",
        "source_kind",
        "source_receipt_id",
        "source_revision",
        "status",
      ]) &&
      receipt.poc_id === pocId &&
      receipt.schema_version === "exitspec.assisted-authoring-receipt.v1" &&
      /^arcp_[a-f0-9]{32}$/.test(receipt.authoring_receipt_id) &&
      /^ares_[a-f0-9]{32}$/.test(receipt.authoring_result_id) &&
      /^src_[a-z0-9][a-z0-9_-]{2,63}$/.test(receipt.source_id) &&
      receipt.source_receipt_id === sourceReceiptIdForSourceId(receipt.source_id) &&
      SOURCE_KINDS.includes(receipt.source_kind) &&
      /^[a-f0-9]{64}$/.test(receipt.source_content_sha256) &&
      Number.isSafeInteger(receipt.source_revision) &&
      receipt.source_revision >= 1 &&
      isSafeBoundedText(receipt.source_adapter_name, 64) &&
      isSafeBoundedText(receipt.source_adapter_version, 64) &&
      isSafeBoundedText(receipt.redaction_policy_version, 64) &&
      isSafeBoundedText(receipt.authoring_adapter_name, 64) &&
      isSafeBoundedText(receipt.authoring_adapter_version, 64) &&
      isSafeBoundedText(receipt.provider, 64) &&
      isSafeBoundedText(receipt.model, 160) &&
      isSafeBoundedText(receipt.endpoint, 300) &&
      typeof receipt.generated_at === "string" &&
      receipt.generated_at.length <= 64 &&
      Number.isFinite(Date.parse(receipt.generated_at)) &&
      receipt.status === "NEEDS_REVIEW" &&
      Number.isSafeInteger(receipt.proposal_count) &&
      receipt.proposal_count > 0 &&
      Array.isArray(receipt.proposal_ids) &&
      receipt.proposal_ids.length === receipt.proposal_count &&
      receipt.proposal_ids.every((proposalId) =>
        PROPOSAL_ID_PATTERN.test(proposalId)
      ) &&
      new Set(receipt.proposal_ids).size === receipt.proposal_ids.length &&
      typeof receipt.idempotent_replay === "boolean"
    );
  }

  function isTrustedAssistedReceiptCollection(payload, proposalList) {
    if (
      !hasExactKeys(payload, ["poc_id", "receipts"]) ||
      payload.poc_id !== pocId ||
      !Array.isArray(payload.receipts) ||
      payload.receipts.length > 1024
    ) {
      return false;
    }
    const receiptIds = new Set();
    const proposalIds = [];
    for (const receipt of payload.receipts) {
      if (!isTrustedAssistedReceipt(receipt)) return false;
      receiptIds.add(receipt.authoring_receipt_id);
      proposalIds.push(...receipt.proposal_ids);
    }
    if (receiptIds.size !== payload.receipts.length) return false;
    if (!proposalList || !isTrustedProposalList(proposalList)) return false;
    if (payload.receipts.length === 0) return true;
    const queueIds = proposalList.proposals.map((proposal) => proposal.proposal_id);
    return (
      proposalIds.length === queueIds.length &&
      proposalIds.every((proposalId, index) => proposalId === queueIds[index])
    );
  }

  function isTrustedDecisionResponse(payload, attempt) {
    return Boolean(
      hasExactKeys(payload, [
        "decision",
        "disposition",
        "poc_id",
        "proposal_id",
        "review_state",
      ]) &&
        payload.poc_id === pocId &&
        payload.proposal_id === attempt.proposalId &&
        payload.decision === attempt.payload.decision &&
        payload.review_state === attempt.payload.decision &&
        DECISION_DISPOSITIONS.includes(payload.disposition)
    );
  }

  async function requestJson(path, options = {}) {
    if (!isTrustedApiPath(path)) {
      throw new SafeRequestError(null, true);
    }

    let response;
    try {
      response = await fetch(path, {
        ...options,
        cache: "no-store",
        credentials: "same-origin",
        headers: {
          Accept: "application/json",
          ...(options.headers || {}),
        },
      });
    } catch {
      throw new SafeRequestError(null, true);
    }

    let responseUrl;
    try {
      responseUrl = new URL(response.url || path, window.location.origin);
    } catch {
      throw new SafeRequestError(response.status, true);
    }
    if (
      responseUrl.origin !== window.location.origin ||
      responseUrl.pathname !== path ||
      responseUrl.search ||
      responseUrl.hash
    ) {
      throw new SafeRequestError(response.status, true);
    }
    if (!response.ok) {
      const retrySameAttempt =
        response.status >= 500 ||
        response.status === 408 ||
        response.status === 429;
      throw new SafeRequestError(response.status, retrySameAttempt);
    }

    const payload = await response.json().catch(() => null);
    if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
      throw new SafeRequestError(response.status, true);
    }
    return payload;
  }

  function newIdempotencyKey() {
    if (
      window.crypto &&
      typeof window.crypto.randomUUID === "function"
    ) {
      return `proposal-decision-${window.crypto.randomUUID()}`;
    }
    const bytes = new Uint8Array(16);
    window.crypto.getRandomValues(bytes);
    const suffix = Array.from(bytes, (value) =>
      value.toString(16).padStart(2, "0")
    ).join("");
    return `proposal-decision-${suffix}`;
  }

  function currentProposal() {
    return proposals[0] || null;
  }

  function executableMetricCue(proposal) {
    if (!proposal || a3Mode) {
      return null;
    }
    const hasTTFT = TTFT_CUE.test(proposal.normalized_claim);
    const hasErrorRate = ERROR_RATE_CUE.test(proposal.normalized_claim);
    if (hasTTFT === hasErrorRate) {
      return null;
    }
    return hasTTFT ? "TTFT_P95_MS" : "ERROR_RATE_PERCENT";
  }

  function metricCueLabel(metricCue) {
    return metricCue === "TTFT_P95_MS" ? "TTFT" : "error rate";
  }

  function clearError() {
    errorPanel.hidden = true;
    errorPanel.textContent = "";
  }

  function safeFailureCopy(error) {
    if (!(error instanceof SafeRequestError)) {
      return "The decision could not be recorded safely. Retry the same attempt.";
    }
    if (error.statusCode === 400 || error.statusCode === 422) {
      return "The decision was not accepted. Review the reviewer and rationale.";
    }
    if (error.statusCode === 404) {
      return "This POC or proposal is unavailable. No decision was recorded.";
    }
    if (
      error.statusCode === 403 ||
      error.statusCode === 409 ||
      error.statusCode === 415
    ) {
      return "The review request was refused safely. Reload the POC before continuing.";
    }
    return "The response was interrupted or could not be trusted. Retry uses the same decision key.";
  }

  function validatedReviewFields() {
    const reviewer = reviewerInput.value.trim();
    const rationale = rationaleInput.value.trim();
    if (
      !isSafeBoundedText(reviewer, 160) ||
      !isSafeBoundedText(rationale, 2000)
    ) {
      return null;
    }
    return { reviewer, rationale };
  }

  function setFieldAvailability(enabled) {
    reviewerInput.disabled = !enabled;
    rationaleInput.disabled = !enabled;
  }

  function updateDecisionControls() {
    const proposal = currentProposal();
    const hasProposal = proposal !== null;
    const metricCue = executableMetricCue(proposal);
    const duplicateMetric =
      metricCue !== null && selectedMetricCues.has(metricCue);
    const executableSlotAvailable = a3Mode || (
      metricCue !== null &&
      !duplicateMetric &&
      keptCount < 2
    );
    const fieldsValid = validatedReviewFields() !== null;
    const editable = hasProposal && !inFlight && !pendingAttempt;
    const pendingDecision = pendingAttempt
      ? pendingAttempt.payload.decision
      : null;

    setFieldAvailability(editable);
    keepButton.disabled =
      !hasProposal ||
      inFlight ||
      (pendingAttempt
        ? pendingDecision !== "KEEP_FOR_CONTRACT"
        : !fieldsValid || !executableSlotAvailable);
    discardButton.disabled =
      !hasProposal ||
      inFlight ||
      (pendingAttempt ? pendingDecision !== "DISCARD" : !fieldsValid);
    keepButton.textContent =
      pendingDecision === "KEEP_FOR_CONTRACT"
        ? "Retry keep decision"
        : "Keep for contract";
    discardButton.textContent =
      pendingDecision === "DISCARD"
        ? "Retry discard decision"
        : "Discard";

    decisionStatus.textContent = inFlight
      ? "Recording this triage decision…"
      : pendingAttempt
        ? "The response was interrupted. Retry will use the same decision key."
        : a3Mode && fieldsValid
          ? "A3 source-bound proposal only · capability and policy classification remain a later step."
          : fieldsValid && metricCue === null
          ? "The current evaluator cannot execute this claim. Discard keeps it visible as NOT_PROVEN."
          : fieldsValid && duplicateMetric
            ? `One ${metricCueLabel(metricCue)} claim is already selected. Discard this duplicate to NOT_PROVEN.`
            : fieldsValid && keptCount >= 2
              ? "The two executable slots are filled. Discard remaining claims to NOT_PROVEN."
              : fieldsValid
                ? "Choose one triage decision."
          : "Enter the reviewer and rationale to unlock both decisions.";
  }

  function renderProgress() {
    const reviewedCount = keptCount + discardedCount;
    const currentNumber = Math.min(reviewedCount + 1, initialCount);
    const progressBar = document.querySelector("#progress-bar");
    const progressFill = document.querySelector("#progress-fill");
    const progressCopy = document.querySelector("#progress-copy");

    progressBar.setAttribute("aria-valuemax", String(initialCount || 1));
    progressBar.setAttribute("aria-valuenow", String(reviewedCount));
    progressCopy.textContent =
      initialCount === 0
        ? "No proposals to review"
        : `Proposal ${currentNumber} of ${initialCount}`;
    progressFill.style.width =
      initialCount === 0
        ? "100%"
        : `${Math.round((reviewedCount / initialCount) * 100)}%`;
  }

  function renderCurrentProposal() {
    const proposal = currentProposal();
    if (!proposal) {
      renderCompletion();
      return;
    }

    document.querySelector("#proposal-heading").textContent =
      `Proposal ${keptCount + discardedCount + 1}`;
    document.querySelector("#source-kind").textContent =
      SOURCE_LABELS[proposal.source_kind];
    document.querySelector("#source-quote").textContent =
      proposal.source_quote;
    document.querySelector("#normalized-claim").textContent =
      proposal.normalized_claim;
    const metricCue = executableMetricCue(proposal);
    const support = document.querySelector("#proposal-support");
    support.setAttribute("data-supported", String(metricCue !== null));
    support.textContent = a3Mode
      ? "Source-bound proposal material · later classification is not assigned here"
      : metricCue === null
      ? "Not executable in this demo · discard to NOT_PROVEN"
      : `Executable candidate · ${metricCueLabel(metricCue)}`;
    reviewerInput.value = "";
    rationaleInput.value = "";
    pendingAttempt = null;
    clearError();
    renderProgress();
    updateDecisionControls();
    document.querySelector("#proposal-evidence").focus?.();
  }

  function renderCompletion() {
    proposals = [];
    document.querySelector("#source-quote").textContent = "";
    document.querySelector("#normalized-claim").textContent = "";
    document.querySelector("#proposal-support").textContent = "";
    reviewerInput.value = "";
    rationaleInput.value = "";
    pendingAttempt = null;
    currentTask.hidden = true;
    completionPanel.hidden = false;
    document.querySelector("#review-complete-summary").textContent =
      a3Mode
        ? initialCount === 0
          ? "There are no source proposals awaiting review. No proposal was retained for acceptance drafting. No contract was created or approved."
          : `${initialCount} proposals reviewed: ${keptCount} retained for acceptance drafting and ${discardedCount} discarded. No contract was created or approved.`
        : initialCount === 0
        ? "There are no source proposals awaiting review. No contract was created or approved."
        : `${initialCount} proposals reviewed: ${keptCount} kept for contract authoring and ${discardedCount} discarded. No contract was created or approved.`;
    if (pocId && keptCount === 2) {
      if (!a3Mode) {
        const destination = `/app/pocs/${encodeURIComponent(pocId)}/define`;
        defineCriteriaLink.textContent = "Define acceptance criteria";
        defineCriteriaLink.href = destination;
        defineCriteriaLink.hidden = false;
        completionPanel.focus();
        try {
          window.location.replace(destination);
        } catch {
          // The verified fallback panel remains available if navigation is blocked.
        }
      } else {
        defineCriteriaLink.textContent = "Return to POC workspace";
        defineCriteriaLink.href = "/app";
        defineCriteriaLink.hidden = false;
      }
    } else if (a3Mode) {
      defineCriteriaLink.textContent = "Return to POC workspace";
      defineCriteriaLink.href = "/app";
      defineCriteriaLink.hidden = false;
    } else {
      defineCriteriaLink.textContent =
        keptCount === 1
          ? "Add the missing executable requirement"
          : "Add another source";
      defineCriteriaLink.href = pocId
        ? `/app/pocs/${encodeURIComponent(pocId)}/sources/new`
        : "/app";
      defineCriteriaLink.hidden = false;
    }
    const progressBar = document.querySelector("#progress-bar");
    progressBar.setAttribute("aria-valuenow", String(initialCount));
    document.querySelector("#progress-fill").style.width = "100%";
    completionPanel.focus();
  }

  function renderPOCContext(remainingCount) {
    if (!pocCustomerLabel) {
      return;
    }
    document.querySelector("#poc-context").textContent =
      `${pocCustomerLabel} · ${remainingCount} ${remainingCount === 1 ? "proposal" : "proposals"} awaiting triage`;
  }

  function applyLoadedData(draft, proposalList) {
    proposals = proposalList.proposals.slice();
    initialCount = proposalList.review_summary.total;
    keptCount = proposalList.review_summary.kept_for_contract;
    discardedCount = proposalList.review_summary.discarded;
    pocCustomerLabel = draft.customer_label;
    document.querySelector("#poc-title").textContent = draft.display_name;
    const assistedLink = document.querySelector("#assisted-authoring-link");
    if (a3Capability) {
      assistedLink.href = `/app/pocs/${encodeURIComponent(pocId)}/assisted-authoring`;
      assistedLink.hidden = false;
    } else {
      assistedLink.href = "/app";
      assistedLink.hidden = true;
    }
    renderPOCContext(proposalList.review_summary.needs_review);
    currentTask.setAttribute("aria-busy", "false");
    renderCurrentProposal();
  }

  async function reconcileQueueAfterDecision() {
    const proposalList = await requestJson(proposalsApi);
    if (!isTrustedProposalList(proposalList)) {
      throw new SafeRequestError(200, true);
    }
    proposals = proposalList.proposals.slice();
    initialCount = proposalList.review_summary.total;
    keptCount = proposalList.review_summary.kept_for_contract;
    discardedCount = proposalList.review_summary.discarded;
    renderPOCContext(proposalList.review_summary.needs_review);
    renderCurrentProposal();
  }

  function blockReview(message) {
    currentTask.setAttribute("aria-busy", "false");
    const assistedLink = document.querySelector("#assisted-authoring-link");
    assistedLink.href = "/app";
    assistedLink.hidden = true;
    setFieldAvailability(false);
    keepButton.disabled = true;
    discardButton.disabled = true;
    decisionStatus.textContent = "Proposal review is unavailable.";
    errorPanel.textContent = message;
    errorPanel.hidden = false;
  }

  reviewerInput.addEventListener("input", () => {
    if (!inFlight && !pendingAttempt) {
      clearError();
      updateDecisionControls();
    }
  });

  rationaleInput.addEventListener("input", () => {
    if (!inFlight && !pendingAttempt) {
      clearError();
      updateDecisionControls();
    }
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const proposal = currentProposal();
    let decisionRecorded = false;
    if (inFlight || !proposal) {
      return;
    }

    if (!pendingAttempt) {
      const decision = event.submitter ? event.submitter.value : null;
      const fields = validatedReviewFields();
      if (
        !DECISIONS.includes(decision) ||
        !fields ||
        !form.reportValidity()
      ) {
        decisionStatus.textContent =
          "Enter the reviewer and rationale, then choose one decision.";
        return;
      }
      const endpoint = decisionPath(proposal.proposal_id);
      if (!endpoint || !isTrustedApiPath(endpoint)) {
        blockReview(
          "The proposal decision route is invalid. No decision was recorded."
        );
        return;
      }
      pendingAttempt = {
        endpoint,
        proposalId: proposal.proposal_id,
        payload: {
          decision,
          reviewer: fields.reviewer,
          rationale: fields.rationale,
          idempotency_key: newIdempotencyKey(),
        },
      };
    } else {
      const retriedDecision = event.submitter
        ? event.submitter.value
        : null;
      if (retriedDecision !== pendingAttempt.payload.decision) {
        return;
      }
    }

    inFlight = true;
    clearError();
    updateDecisionControls();

    try {
      const response = await requestJson(pendingAttempt.endpoint, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify(pendingAttempt.payload),
      });
      if (!isTrustedDecisionResponse(response, pendingAttempt)) {
        throw new SafeRequestError(200, true);
      }
      if (pendingAttempt.payload.decision === "KEEP_FOR_CONTRACT") {
        const metricCue = executableMetricCue(proposal);
        if (metricCue !== null) {
          selectedMetricCues.add(metricCue);
        }
      }
      pendingAttempt = null;
      proposals.shift();
      decisionRecorded = true;
      await reconcileQueueAfterDecision();
    } catch (error) {
      if (decisionRecorded) {
        proposals = [];
        pendingAttempt = null;
        blockReview(
          "The decision was recorded, but the current proposal queue could not be refreshed. Reload before continuing."
        );
        return;
      }
      if (
        error instanceof SafeRequestError &&
        !error.retrySameAttempt
      ) {
        pendingAttempt = null;
      }
      errorPanel.textContent = safeFailureCopy(error);
      errorPanel.hidden = false;
    } finally {
      inFlight = false;
      if (!completionPanel.hidden) {
        return;
      }
      updateDecisionControls();
    }
  });

  async function initialise() {
    if (!pocId || !pocApi || !proposalsApi) {
      blockReview(
        "This proposal-review address is invalid. Return to the POC workspace."
      );
      return;
    }
    try {
      const [draft, proposalList] = await Promise.all([
        requestJson(pocApi),
        requestJson(proposalsApi),
      ]);
      if (
        !isTrustedDraft(draft) ||
        !isTrustedProposalList(proposalList)
      ) {
        throw new SafeRequestError(200, true);
      }
      let capability = null;
      try {
        capability = await requestJson(stateApi);
      } catch {
        throw new SafeRequestError(503, true);
      }
      if (isTrustedA3Capability(capability) && assistedApi) {
        try {
          const assistedList = await requestJson(assistedApi);
          if (!isTrustedAssistedReceiptCollection(assistedList, proposalList)) {
            throw new SafeRequestError(200, true);
          }
          a3Capability = true;
          a3Mode = assistedList.receipts.length > 0;
        } catch {
          throw new SafeRequestError(503, true);
        }
      } else if (!isTrustedLegacyCapability(capability)) {
        throw new SafeRequestError(503, true);
      }
      applyLoadedData(draft, proposalList);
    } catch {
      blockReview(
        "The draft or proposal queue could not be validated. No review action is available."
      );
    }
  }

  window.addEventListener("pagehide", () => {
    proposals = [];
    selectedMetricCues.clear();
    pocCustomerLabel = null;
    pendingAttempt = null;
    reviewerInput.value = "";
    rationaleInput.value = "";
    document.querySelector("#source-quote").textContent = "";
    document.querySelector("#normalized-claim").textContent = "";
  });

  initialise();
})();
