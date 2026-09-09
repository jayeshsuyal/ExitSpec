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
  const currentReviewApi = pocApi
    ? `${pocApi}/assisted-authoring/current-review`
    : null;
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
  const planCapabilitiesLink = document.querySelector("#plan-capabilities");
  const proposalTabs = document.querySelector("#proposal-tabs");
  const proposalPicker = document.querySelector("#proposal-picker");
  const reviewStart = document.querySelector("#review-start");
  const reviewEditor = document.querySelector("#review-editor");
  const reviewCancel = document.querySelector("#review-cancel");

  let proposals = [];
  let selectedProposalId = null;
  const reviewDrafts = new Map();
  const proposalNumbers = new Map();
  let initialCount = 0;
  let keptCount = 0;
  let discardedCount = 0;
  const selectedMetricCues = new Set();
  let pocCustomerLabel = null;
  let a3Capability = false;
  let hasA3Proposals = false;
  let a3Proposals = new Map();
  let inFlight = false;
  let pendingAttempt = null;
  let blocked = true;
  let pageEpoch = 0;
  let pageActive = true;
  let requestController = new AbortController();

  class StalePageError extends Error {}

  function assertCurrentPage(epoch) {
    if (!pageActive || epoch !== pageEpoch) throw new StalePageError();
  }

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
        value === assistedApi ||
        value === currentReviewApi
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

  function isTrustedAssistedReceiptCollection(payload) {
    if (
      !hasExactKeys(payload, ["poc_id", "receipts"]) ||
      payload.poc_id !== pocId ||
      !Array.isArray(payload.receipts) ||
      payload.receipts.length > 1024
    ) {
      return false;
    }
    const receiptIds = new Set();
    for (const receipt of payload.receipts) {
      if (!isTrustedAssistedReceipt(receipt)) return false;
      receiptIds.add(receipt.authoring_receipt_id);
    }
    if (receiptIds.size !== payload.receipts.length) return false;
    return true;
  }

  function isTrustedDecisionOverlay(decision, projection) {
    if (projection.review_state === "NEEDS_REVIEW") return decision === null;
    return Boolean(
      hasExactKeys(decision, [
        "decided_at",
        "decision",
        "poc_id",
        "proposal_id",
        "rationale",
        "reviewer",
        "source_kind",
        "source_receipt_id",
      ]) &&
        decision.poc_id === pocId &&
        decision.proposal_id === projection.proposal_id &&
        decision.source_receipt_id === projection.source_receipt_id &&
        decision.source_kind === projection.source_kind &&
        DECISIONS.includes(decision.decision) &&
        decision.decision === projection.review_state &&
        isSafeBoundedText(decision.reviewer, 160) &&
        isSafeBoundedText(decision.rationale, 2000) &&
        typeof decision.decided_at === "string" &&
        decision.decided_at.length <= 64 &&
        Number.isFinite(Date.parse(decision.decided_at))
    );
  }

  function isTrustedAssistedProjection(projection, receipt) {
    return Boolean(
      hasExactKeys(projection, [
        "authoring_receipt_id",
        "authoring_result_id",
        "decision",
        "normalized_claim",
        "numeric_facts",
        "poc_id",
        "proposal_id",
        "proposal_key",
        "redaction_policy_version",
        "review_state",
        "schema_version",
        "source_adapter_name",
        "source_adapter_version",
        "source_content_sha256",
        "source_id",
        "source_kind",
        "source_quote",
        "source_receipt_id",
        "source_revision",
      ]) &&
        projection.poc_id === pocId &&
        projection.schema_version === "exitspec.current-assisted-proposal.v1" &&
        PROPOSAL_ID_PATTERN.test(projection.proposal_id) &&
        receipt.authoring_receipt_id === projection.authoring_receipt_id &&
        receipt.authoring_result_id === projection.authoring_result_id &&
        receipt.poc_id === projection.poc_id &&
        receipt.source_receipt_id === projection.source_receipt_id &&
        receipt.source_id === projection.source_id &&
        receipt.source_kind === projection.source_kind &&
        receipt.source_content_sha256 === projection.source_content_sha256 &&
        receipt.source_revision === projection.source_revision &&
        receipt.source_adapter_name === projection.source_adapter_name &&
        receipt.source_adapter_version === projection.source_adapter_version &&
        receipt.redaction_policy_version === projection.redaction_policy_version &&
        receipt.proposal_ids.includes(projection.proposal_id) &&
        SOURCE_KINDS.includes(projection.source_kind) &&
        isSafeBoundedText(projection.source_quote, 4000) &&
        isSafeBoundedText(projection.normalized_claim, 2000) &&
        isSafeBoundedText(projection.proposal_key, 64) &&
        ["NEEDS_REVIEW", "KEEP_FOR_CONTRACT", "DISCARD"].includes(
          projection.review_state
        ) &&
        isTrustedDecisionOverlay(projection.decision, projection)
    );
  }

  function trustedAssistedProposals(assistedList, currentReview, proposalList) {
    if (
      !isTrustedAssistedReceiptCollection(assistedList) ||
      !hasExactKeys(currentReview, ["poc_id", "proposals"]) ||
      currentReview.poc_id !== pocId ||
      !Array.isArray(currentReview.proposals) ||
      currentReview.proposals.length > 1024 ||
      !isTrustedProposalList(proposalList)
    ) {
      return null;
    }
    const receiptsById = new Map();
    const receiptProposalIds = [];
    for (const receipt of assistedList.receipts) {
      if (receiptsById.has(receipt.authoring_receipt_id)) return null;
      receiptsById.set(receipt.authoring_receipt_id, receipt);
      receiptProposalIds.push(...receipt.proposal_ids);
    }
    const projectionsById = new Map();
    for (const projection of currentReview.proposals) {
      const receipt = receiptsById.get(projection.authoring_receipt_id);
      if (
        projectionsById.has(projection.proposal_id) ||
        !receipt ||
        !isTrustedAssistedProjection(projection, receipt)
      ) {
        return null;
      }
      projectionsById.set(projection.proposal_id, projection);
    }
    const projectionIds = currentReview.proposals.map(
      (projection) => projection.proposal_id
    );
    if (
      receiptProposalIds.length !== projectionIds.length ||
      new Set(receiptProposalIds).size !== receiptProposalIds.length ||
      receiptProposalIds.some(
        (proposalId, index) => proposalId !== projectionIds[index]
      )
    ) {
      return null;
    }
    const pendingIds = new Set(
      proposalList.proposals.map((proposal) => proposal.proposal_id)
    );
    for (const proposal of proposalList.proposals) {
      const projection = projectionsById.get(proposal.proposal_id);
      if (!projection) continue; // untouched A2 material remains reviewable.
      if (
        projection.review_state !== "NEEDS_REVIEW" ||
        projection.source_receipt_id !== proposal.source_receipt_id ||
        projection.source_kind !== proposal.source_kind ||
        projection.source_quote !== proposal.source_quote ||
        projection.normalized_claim !== proposal.normalized_claim
      ) {
        return null;
      }
    }
    for (const projection of currentReview.proposals) {
      if (
        projection.review_state === "NEEDS_REVIEW" &&
        !pendingIds.has(projection.proposal_id)
      ) {
        return null;
      }
      if (
        projection.review_state !== "NEEDS_REVIEW" &&
        pendingIds.has(projection.proposal_id)
      ) {
        return null;
      }
    }
    return projectionsById;
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
    const epoch = pageEpoch;
    assertCurrentPage(epoch);
    if (!isTrustedApiPath(path)) {
      throw new SafeRequestError(null, true);
    }

    let response;
    try {
      response = await fetch(path, {
        ...options,
        signal: requestController.signal,
        cache: "no-store",
        credentials: "same-origin",
        headers: {
          Accept: "application/json",
          ...(options.headers || {}),
        },
      });
    } catch {
      assertCurrentPage(epoch);
      throw new SafeRequestError(null, true);
    }
    assertCurrentPage(epoch);

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
    assertCurrentPage(epoch);
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
    return proposals.find(
      (proposal) => proposal.proposal_id === selectedProposalId
    ) || null;
  }

  function sameSourceBinding(left, right) {
    return left && right &&
      left.proposal_id === right.proposal_id &&
      left.source_receipt_id === right.source_receipt_id &&
      left.source_kind === right.source_kind &&
      left.source_quote === right.source_quote &&
      left.normalized_claim === right.normalized_claim;
  }

  function saveReviewDraft() {
    const proposal = currentProposal();
    if (!proposal || blocked || inFlight || pendingAttempt) return;
    reviewDrafts.set(proposal.proposal_id, {
      proposal,
      reviewer: reviewerInput.value,
      rationale: rationaleInput.value,
      expanded: !reviewEditor.hidden,
    });
  }

  function reconcileSelection() {
    const pendingIds = new Set(proposals.map((item) => item.proposal_id));
    for (const id of reviewDrafts.keys()) {
      const current = proposals.find((item) => item.proposal_id === id);
      if (!sameSourceBinding(reviewDrafts.get(id).proposal, current)) {
        reviewDrafts.delete(id);
      }
    }
    if (!pendingIds.has(selectedProposalId)) {
      selectedProposalId = proposals[0]?.proposal_id || null;
    }
    let nextNumber = Math.max(keptCount + discardedCount, ...proposalNumbers.values()) + 1;
    proposals.forEach((proposal) => {
      if (!proposalNumbers.has(proposal.proposal_id)) {
        proposalNumbers.set(proposal.proposal_id, nextNumber);
        nextNumber += 1;
      }
    });
  }

  function renderNavigator() {
    proposalTabs.replaceChildren();
    proposalPicker.replaceChildren();
    for (const proposal of proposals) {
      const label = `Proposal ${proposalNumbers.get(proposal.proposal_id)}`;
      const metricCue = executableMetricCue(proposal);
      const title = metricCue === "TTFT_P95_MS" ? "Time to first token"
        : metricCue === "ERROR_RATE_PERCENT" ? "Error rate"
          : proposal.normalized_claim.length > 64
            ? `${proposal.normalized_claim.slice(0, 64)}…` : proposal.normalized_claim;
      const item = document.createElement("li");
      const button = document.createElement("button");
      button.type = "button";
      button.className = "proposal-tab";
      button.setAttribute("aria-pressed", String(proposal.proposal_id === selectedProposalId));
      button.setAttribute("aria-controls", "proposal-evidence");
      button.setAttribute("aria-label", `${label}: ${proposal.normalized_claim}`);
      for (const [className, copy] of [
        ["proposal-nav-id", label],
        ["proposal-nav-title", title],
        ["proposal-nav-state", "Needs review"],
      ]) {
        const span = document.createElement("span");
        span.className = className;
        span.textContent = copy;
        button.append(span);
      }
      button.addEventListener("click", () => selectProposal(proposal.proposal_id));
      item.append(button);
      proposalTabs.append(item);
      const option = document.createElement("option");
      option.value = proposal.proposal_id;
      option.textContent = `${label} · ${title}`;
      proposalPicker.append(option);
    }
    proposalPicker.value = selectedProposalId || "";
  }

  function selectProposal(proposalId) {
    if (blocked || inFlight || pendingAttempt) {
      proposalPicker.value = selectedProposalId || "";
      return;
    }
    if (!proposals.some((proposal) => proposal.proposal_id === proposalId)) return;
    saveReviewDraft();
    selectedProposalId = proposalId;
    renderCurrentProposal();
  }

  function setReviewExpanded(expanded) {
    reviewEditor.hidden = !expanded;
    reviewStart.hidden = expanded;
    reviewStart.setAttribute("aria-expanded", String(expanded));
  }

  // These are word-boundary display chunks, never parsed acceptance facts.
  function sourceTextChunks(text) {
    const chunks = [];
    let start = null;
    let end = 0;
    for (const word of text.matchAll(/\S+/g)) {
      const connective = /^(?:must|shall|should|at|across|over|within|under|below|above|for)$/i.test(word[0]);
      if (start !== null && (word.index + word[0].length - start > 32 ||
          (connective && end - start >= 8))) {
        chunks.push([start, end]);
        start = null;
      }
      if (start === null) start = word.index;
      end = word.index + word[0].length;
    }
    if (start !== null) chunks.push([start, end]);
    return chunks;
  }

  function renderSourceMatch(proposal) {
    const claim = document.querySelector("#normalized-claim");
    const excerpt = document.querySelector("#source-excerpt");
    const note = document.querySelector("#source-match-note");
    claim.replaceChildren();
    excerpt.textContent = proposal.source_quote.slice(0, 112) +
      (proposal.source_quote.length > 112 ? "…" : "");
    note.textContent = "No exact wording match is highlighted. Compare the normalized claim with the full redacted source quote below.";
    let cursor = 0;
    let firstTerm = null;
    const trace = (term, start, phrase) => {
      if (!pageActive || !sameSourceBinding(currentProposal(), proposal)) return;
      if (proposal.source_quote.slice(start, start + phrase.length) !== phrase) return;
      const mark = document.createElement("mark");
      mark.textContent = phrase;
      excerpt.replaceChildren(mark);
      for (const button of claim.querySelectorAll("button")) {
        button.setAttribute("aria-pressed", String(button === term));
      }
      note.textContent = "Exact source excerpt. Select an underlined phrase to inspect its source.";
    };
    for (const [start, end] of sourceTextChunks(proposal.normalized_claim)) {
      claim.append(document.createTextNode(proposal.normalized_claim.slice(cursor, start)));
      const phrase = proposal.normalized_claim.slice(start, end);
      const sourceStart = proposal.source_quote.indexOf(phrase);
      if (phrase.length >= 8 && phrase.length <= 64 && sourceStart >= 0) {
        const term = document.createElement("button");
        term.type = "button";
        term.className = "source-term";
        term.textContent = phrase;
        term.setAttribute("aria-controls", "source-excerpt");
        term.setAttribute("aria-pressed", "false");
        term.addEventListener("click", () => trace(term, sourceStart, phrase));
        claim.append(term);
        if (!firstTerm) firstTerm = () => trace(term, sourceStart, phrase);
      } else {
        claim.append(document.createTextNode(phrase));
      }
      cursor = end;
    }
    claim.append(document.createTextNode(proposal.normalized_claim.slice(cursor)));
    firstTerm?.();
  }

  function executableMetricCue(proposal) {
    if (!proposal || a3Proposals.has(proposal.proposal_id)) {
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
      return "This POC or proposal is unavailable. Reload to check the recorded state before continuing.";
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
    const hasProposal = proposal !== null && !blocked && pageActive;
    const metricCue = executableMetricCue(proposal);
    const isA3Proposal = hasProposal && a3Proposals.has(proposal.proposal_id);
    const duplicateMetric =
      metricCue !== null && selectedMetricCues.has(metricCue);
    const executableSlotAvailable = isA3Proposal || (
      metricCue !== null &&
      !duplicateMetric &&
      keptCount < 2
    );
    const fieldsValid = validatedReviewFields() !== null;
    const editable = hasProposal && !inFlight && !pendingAttempt;
    const pendingDecision = pendingAttempt
      ? pendingAttempt.payload.decision
      : null;

    proposalPicker.disabled = !hasProposal || inFlight || Boolean(pendingAttempt);
    for (const button of proposalTabs.querySelectorAll("button")) {
      button.disabled = proposalPicker.disabled;
    }
    reviewStart.disabled = !editable;
    reviewCancel.disabled = !editable;

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

    decisionStatus.textContent = blocked
      ? "Proposal review is unavailable. Reload before continuing."
      : inFlight
      ? "Recording this triage decision…"
      : pendingAttempt
        ? "The response was interrupted. Retry will use the same decision key."
        : isA3Proposal && fieldsValid
          ? "A3 source-bound proposal only · capability and policy classification remain a later step."
          : fieldsValid && metricCue === null
          ? "The current evaluator cannot execute this claim. Discard keeps it visible as NOT_PROVEN."
          : fieldsValid && duplicateMetric
            ? `One ${metricCueLabel(metricCue)} claim is already selected. Discard this duplicate to NOT_PROVEN.`
            : fieldsValid && keptCount >= 2
              ? "The two executable slots are filled. Discard remaining claims to NOT_PROVEN."
              : fieldsValid
                ? "Choose one triage decision."
          : "Enter the reviewer and rationale to unlock the available decisions.";
  }

  function renderProgress() {
    const reviewedCount = keptCount + discardedCount;
    const progressBar = document.querySelector("#progress-bar");
    const progressFill = document.querySelector("#progress-fill");
    const progressCopy = document.querySelector("#progress-copy");

    progressBar.setAttribute("aria-valuemax", String(initialCount || 1));
    progressBar.setAttribute("aria-valuenow", String(reviewedCount));
    progressCopy.textContent =
      initialCount === 0
        ? "No proposals to review"
        : `${reviewedCount} reviewed · ${proposals.length} awaiting triage`;
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
      `Proposal ${proposalNumbers.get(proposal.proposal_id)}`;
    document.querySelector("#proposal-reference").textContent = proposal.proposal_id;
    document.querySelector("#source-receipt-id").textContent = proposal.source_receipt_id;
    document.querySelector("#source-kind").textContent =
      SOURCE_LABELS[proposal.source_kind];
    document.querySelector("#source-quote").textContent =
      proposal.source_quote;
    document.querySelector("#normalized-claim").textContent =
      proposal.normalized_claim;
    renderSourceMatch(proposal);
    const metricCue = executableMetricCue(proposal);
    document.querySelector("#requirement-heading").textContent =
      metricCue === "TTFT_P95_MS" ? "Time to first token"
        : metricCue === "ERROR_RATE_PERCENT" ? "Error rate" : "Proposed requirement";
    const support = document.querySelector("#proposal-support");
    support.setAttribute("data-supported", String(metricCue !== null));
    support.textContent = a3Proposals.has(proposal.proposal_id)
      ? "Source-bound proposal material · later classification is not assigned here"
      : metricCue === null
      ? "Not executable in this demo · discard to NOT_PROVEN"
      : `Executable candidate · ${metricCueLabel(metricCue)}`;
    const draft = reviewDrafts.get(proposal.proposal_id);
    reviewerInput.value = draft?.reviewer || "";
    rationaleInput.value = draft?.rationale || "";
    setReviewExpanded(Boolean(draft?.expanded));
    clearError();
    renderNavigator();
    renderProgress();
    updateDecisionControls();
    document.querySelector("#proposal-evidence").focus?.();
  }

  function renderCompletion() {
    proposals = [];
    selectedProposalId = null;
    reviewDrafts.clear();
    proposalNumbers.clear();
    renderNavigator();
    document.querySelector("#source-quote").textContent = "";
    document.querySelector("#normalized-claim").textContent = "";
    document.querySelector("#proposal-support").textContent = "";
    document.querySelector("#source-match-note").textContent = "";
    document.querySelector("#source-excerpt").textContent = "";
    document.querySelector("#source-receipt-id").textContent = "";
    document.querySelector("#proposal-reference").textContent = "";
    reviewerInput.value = "";
    rationaleInput.value = "";
    pendingAttempt = null;
    planCapabilitiesLink.hidden = true;
    currentTask.hidden = true;
    completionPanel.hidden = false;
    document.querySelector("#review-complete-summary").textContent =
      hasA3Proposals
        ? initialCount === 0
          ? "There are no source proposals awaiting review. No proposal was retained for acceptance drafting. No contract was created or approved."
          : `${initialCount} proposals reviewed: ${keptCount} retained for acceptance drafting and ${discardedCount} discarded. No contract was created or approved.`
        : initialCount === 0
        ? "There are no source proposals awaiting review. No contract was created or approved."
        : `${initialCount} proposals reviewed: ${keptCount} kept for contract authoring and ${discardedCount} discarded. No contract was created or approved.`;
    if (pocId && keptCount === 2) {
      if (!hasA3Proposals) {
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
        if (keptCount > 0) {
          planCapabilitiesLink.textContent = "Plan retained capabilities";
          planCapabilitiesLink.href = `/app/pocs/${encodeURIComponent(pocId)}/capability-plan`;
          planCapabilitiesLink.hidden = false;
        }
      }
    } else if (pocId && hasA3Proposals && keptCount > 0) {
      defineCriteriaLink.textContent = "Return to POC workspace";
      defineCriteriaLink.href = "/app";
      defineCriteriaLink.hidden = false;
      planCapabilitiesLink.textContent = "Plan retained capabilities";
      planCapabilitiesLink.href = `/app/pocs/${encodeURIComponent(pocId)}/capability-plan`;
      planCapabilitiesLink.hidden = false;
    } else if (hasA3Proposals) {
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
    document.querySelector("#poc-customer").textContent = draft.customer_label;
    const assistedLink = document.querySelector("#assisted-authoring-link");
    if (a3Capability) {
      assistedLink.href = `/app/pocs/${encodeURIComponent(pocId)}/assisted-authoring`;
      assistedLink.hidden = false;
    } else {
      assistedLink.href = "/app";
      assistedLink.hidden = true;
    }
    renderPOCContext(proposalList.review_summary.needs_review);
    blocked = false;
    currentTask.hidden = false;
    completionPanel.hidden = true;
    currentTask.setAttribute("aria-busy", "false");
    reconcileSelection();
    renderCurrentProposal();
  }

  async function reconcileQueueAfterDecision(attempt) {
    const proposalList = await requestJson(proposalsApi);
    if (!isTrustedProposalList(proposalList)) {
      throw new SafeRequestError(200, true);
    }
    if (proposalList.proposals.some((proposal) => proposal.proposal_id === attempt.proposalId)) {
      throw new SafeRequestError(200, true);
    }
    if (a3Capability) {
      const [assistedList, currentReview] = await Promise.all([
        requestJson(assistedApi),
        requestJson(currentReviewApi),
      ]);
      const projection = trustedAssistedProposals(
        assistedList,
        currentReview,
        proposalList
      );
      if (!projection) {
        throw new SafeRequestError(200, true);
      }
      a3Proposals = projection;
      hasA3Proposals = assistedList.receipts.length > 0;
    }
    proposals = proposalList.proposals.slice();
    initialCount = proposalList.review_summary.total;
    keptCount = proposalList.review_summary.kept_for_contract;
    discardedCount = proposalList.review_summary.discarded;
    renderPOCContext(proposalList.review_summary.needs_review);
    reconcileSelection();
    renderCurrentProposal();
  }

  function blockReview(message) {
    blocked = true;
    currentTask.setAttribute("aria-busy", "false");
    const assistedLink = document.querySelector("#assisted-authoring-link");
    assistedLink.href = "/app";
    assistedLink.hidden = true;
    updateDecisionControls();
    errorPanel.textContent = message;
    errorPanel.hidden = false;
  }

  reviewerInput.addEventListener("input", () => {
    if (!inFlight && !pendingAttempt) {
      saveReviewDraft();
      clearError();
      updateDecisionControls();
    }
  });

  rationaleInput.addEventListener("input", () => {
    if (!inFlight && !pendingAttempt) {
      saveReviewDraft();
      clearError();
      updateDecisionControls();
    }
  });

  proposalPicker.addEventListener("change", () => selectProposal(proposalPicker.value));
  reviewStart.addEventListener("click", () => {
    if (blocked || inFlight || pendingAttempt || !currentProposal()) return;
    setReviewExpanded(true);
    saveReviewDraft();
    reviewerInput.focus();
  });
  reviewCancel.addEventListener("click", () => {
    if (blocked || inFlight || pendingAttempt) return;
    setReviewExpanded(false);
    saveReviewDraft();
    reviewStart.focus();
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const proposal = currentProposal();
    let decisionRecorded = false;
    if (blocked || !pageActive || inFlight || !proposal) {
      return;
    }

    if (!pendingAttempt) {
      const decision = event.submitter ? event.submitter.value : null;
      const fields = validatedReviewFields();
      if (
        !DECISIONS.includes(decision) ||
        !fields ||
        reviewEditor.hidden ||
        (decision === "KEEP_FOR_CONTRACT" && keepButton.disabled) ||
        (decision === "DISCARD" && discardButton.disabled) ||
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

    const attempt = pendingAttempt;
    const epoch = pageEpoch;
    inFlight = true;
    clearError();
    updateDecisionControls();

    try {
      const response = await requestJson(attempt.endpoint, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify(attempt.payload),
      });
      assertCurrentPage(epoch);
      if (!isTrustedDecisionResponse(response, attempt)) {
        throw new SafeRequestError(200, true);
      }
      if (attempt.payload.decision === "KEEP_FOR_CONTRACT") {
        const metricCue = executableMetricCue(proposal);
        if (metricCue !== null) {
          selectedMetricCues.add(metricCue);
        }
      }
      pendingAttempt = null;
      proposals = proposals.filter((item) => item.proposal_id !== attempt.proposalId);
      reviewDrafts.delete(attempt.proposalId);
      decisionRecorded = true;
      await reconcileQueueAfterDecision(attempt);
    } catch (error) {
      if (!pageActive || epoch !== pageEpoch || error instanceof StalePageError) return;
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
        if ([403, 404, 409, 415].includes(error.statusCode)) {
          blockReview(safeFailureCopy(error));
          return;
        }
      }
      errorPanel.textContent = safeFailureCopy(error);
      errorPanel.hidden = false;
    } finally {
      if (!pageActive || epoch !== pageEpoch) return;
      inFlight = false;
      if (!completionPanel.hidden) {
        return;
      }
      updateDecisionControls();
    }
  });

  async function initialise() {
    const epoch = pageEpoch;
    if (!pocId || !pocApi || !proposalsApi || !currentReviewApi) {
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
          const [assistedList, currentReview] = await Promise.all([
            requestJson(assistedApi),
            requestJson(currentReviewApi),
          ]);
          const projection = trustedAssistedProposals(
            assistedList,
            currentReview,
            proposalList
          );
          if (!projection) {
            throw new SafeRequestError(200, true);
          }
          a3Capability = true;
          a3Proposals = projection;
          hasA3Proposals = assistedList.receipts.length > 0;
        } catch {
          throw new SafeRequestError(503, true);
        }
      } else if (!isTrustedLegacyCapability(capability)) {
        throw new SafeRequestError(503, true);
      }
      applyLoadedData(draft, proposalList);
    } catch (error) {
      if (!pageActive || epoch !== pageEpoch || error instanceof StalePageError) return;
      blockReview(
        "The draft or proposal queue could not be validated. No review action is available."
      );
    }
  }

  window.addEventListener("pagehide", () => {
    pageActive = false;
    pageEpoch += 1;
    requestController.abort();
    blocked = true;
    inFlight = false;
    proposals = [];
    selectedProposalId = null;
    reviewDrafts.clear();
    proposalNumbers.clear();
    selectedMetricCues.clear();
    a3Proposals.clear();
    a3Capability = false;
    hasA3Proposals = false;
    initialCount = 0;
    keptCount = 0;
    discardedCount = 0;
    pocCustomerLabel = null;
    pendingAttempt = null;
    reviewerInput.value = "";
    rationaleInput.value = "";
    document.querySelector("#source-quote").textContent = "";
    document.querySelector("#normalized-claim").textContent = "";
    document.querySelector("#proposal-reference").textContent = "";
    document.querySelector("#source-receipt-id").textContent = "";
    document.querySelector("#source-match-note").textContent = "";
    document.querySelector("#source-excerpt").textContent = "";
    document.querySelector("#proposal-support").textContent = "";
    renderNavigator();
    setReviewExpanded(false);
    clearError();
    updateDecisionControls();
  });

  window.addEventListener("pageshow", (event) => {
    if (!event.persisted) return;
    pageActive = true;
    requestController = new AbortController();
    currentTask.hidden = false;
    completionPanel.hidden = true;
    defineCriteriaLink.hidden = true;
    planCapabilitiesLink.hidden = true;
    currentTask.setAttribute("aria-busy", "true");
    initialise();
  });

  initialise();
})();
