(() => {
  "use strict";

  const POC_ID_PATTERN = /^poc_[a-z0-9][a-z0-9_-]{2,63}$/;
  const PROPOSAL_ID_PATTERN = /^prop_[a-z0-9][a-z0-9_-]{7,95}$/;
  const DEFINITION_ID_PATTERN = /^cdef_[a-f0-9]{32}$/;
  const DRAFT_ID_PATTERN = /^agd_[a-f0-9]{32,64}$/;
  const REVISION_ID_PATTERN = /^agrrev_[a-f0-9]{32}$/;
  const CONFIRMATION_ID_PATTERN = /^cnf_[a-f0-9]{64}$/;
  const REVIEW_ID_PATTERN = /^review-[a-f0-9]{24}$/;
  const REVIEW_URL_PATTERN = /^\/review\/[A-Za-z0-9_-]{32,512}$/;
  const SHA256_PATTERN = /^[a-f0-9]{64}$/;
  const ROUTE_PATTERN =
    /^\/app\/pocs\/(poc_[a-z0-9][a-z0-9_-]{2,63})\/agreement$/;
  const METRICS = Object.freeze([
    "TTFT_P95_MS",
    "ERROR_RATE_PERCENT",
  ]);
  const METRIC_LABELS = Object.freeze({
    TTFT_P95_MS: "P95 time to first token",
    ERROR_RATE_PERCENT: "Error rate",
  });
  const METRIC_CONFIG = Object.freeze({
    TTFT_P95_MS: Object.freeze({
      unit: "MILLISECONDS",
      shortUnit: "ms",
      minimum: 0.001,
      maximum: 60000,
    }),
    ERROR_RATE_PERCENT: Object.freeze({
      unit: "PERCENT",
      shortUnit: "%",
      minimum: 0,
      maximum: 100,
    }),
  });
  const REFERENCE_TARGET = Object.freeze({
    target_provider: "ExitSpec local reference",
    endpoint_class: "OpenAI-compatible deterministic reference",
    model: "exitspec/reference-stream-v1",
    path: "/api/reference/inference/v1/chat/completions",
  });
  const INFERDROME_TARGET = Object.freeze({
    target_provider: "vllm-local",
    endpoint_class: "openai-compatible-chat-completions",
    endpoint: "http://127.0.0.1:18083/v1/chat/completions",
    model: "inferdrome/mock-model",
  });
  const EVIDENCE_METHODS = Object.freeze([
    "EXIT_SPEC_STREAMING_PROBE",
    "INFERDROME_EXTERNAL_BUNDLE",
  ]);
  const SOURCE_KINDS = Object.freeze([
    "EMAIL",
    "MEETING",
    "DOCUMENT",
    "EXISTING_CONTRACT",
  ]);
  const SOURCE_LABELS = Object.freeze({
    EMAIL: "Email",
    MEETING: "Meeting",
    DOCUMENT: "Document",
    EXISTING_CONTRACT: "Existing contract",
  });
  const DISPOSITIONS = Object.freeze([
    "CREATED",
    "IDEMPOTENT_REPLAY",
  ]);
  const AGREEMENT_KEYS = Object.freeze([
    "confirmation",
    "counting_policy",
    "customer_review",
    "definitions",
    "draft",
    "frozen_contract",
    "not_proven_claims",
    "poc_id",
    "revision",
    "superseded_version_count",
  ]);
  const COUNTING_POLICY_KEYS = Object.freeze([
    "exact_attempts",
    "external_error_outcomes",
    "invalid_evidence_disposition",
    "latency_failed_attempts",
    "latency_population",
    "policy_sha256",
    "preflight_included",
    "reliability_denominator",
    "retries",
    "schema_version",
    "warmups_included",
  ]);
  const POC_DRAFT_KEYS = Object.freeze([
    "archive_state",
    "archived_at",
    "created_at",
    "customer_label",
    "display_name",
    "first_source_choice",
    "next_intake_route",
    "owner",
    "poc_id",
    "source_ingestion_state",
    "updated_at",
    "use_case",
  ]);
  const DEFINITION_KEYS = Object.freeze([
    "concurrency",
    "defined_at",
    "definition_id",
    "definition_sha256",
    "metric",
    "minimum_samples",
    "normalized_claim",
    "operator",
    "output_tokens_max",
    "output_tokens_min",
    "prompt_tokens_max",
    "prompt_tokens_min",
    "proposal_id",
    "source_kind",
    "source_quote",
    "threshold",
    "unit",
  ]);
  const DRAFT_KEYS = Object.freeze([
    "contract_id",
    "contract_version",
    "created_at",
    "draft_id",
    "draft_sha256",
    "endpoint",
    "endpoint_class",
    "evidence_method",
    "model",
    "parent_version",
    "rationale",
    "reviewer",
    "target_provider",
  ]);
  const CONFIRMATION_KEYS = Object.freeze([
    "agreement_acknowledged",
    "confirmation_id",
    "confirmed_at",
    "confirmer",
    "decision",
    "draft_sha256",
    "rationale",
  ]);
  const CUSTOMER_REVIEW_KEYS = Object.freeze([
    "created_at",
    "expires_at",
    "review_id",
    "review_url",
    "status",
  ]);
  const FROZEN_CONTRACT_KEYS = Object.freeze([
    "canonical_hash",
    "confirmation_id",
    "contract_id",
    "contract_version",
    "endpoint",
    "endpoint_class",
    "evidence_method",
    "frozen_at",
    "model",
    "parent_version",
    "target_provider",
  ]);
  const REVISION_KEYS = Object.freeze([
    "contract_version",
    "parent_contract_id",
    "parent_contract_version",
    "parent_draft_sha256",
    "request_rationale",
    "requested_at",
    "revision_id",
    "revision_number",
  ]);
  const routeMatch =
    window.location.search === "" && window.location.hash === ""
      ? window.location.pathname.match(ROUTE_PATTERN)
      : null;
  const pocId =
    routeMatch && POC_ID_PATTERN.test(routeMatch[1]) ? routeMatch[1] : null;
  const pocApi = pocId ? `/api/pocs/${pocId}` : null;
  const agreementApi = pocId ? `/api/pocs/${pocId}/agreement` : null;
  const reviewApi = agreementApi ? `${agreementApi}/review` : null;
  const freezeApi = agreementApi ? `${agreementApi}/freeze` : null;
  const revisionApi = agreementApi ? `${agreementApi}/revision` : null;

  const workbench = document.querySelector("#agreement-workbench");
  const draftForm = document.querySelector("#create-draft-form");
  const targetProviderInput = document.querySelector("#target-provider");
  const endpointClassInput = document.querySelector("#endpoint-class");
  const endpointInput = document.querySelector("#endpoint");
  const modelInput = document.querySelector("#model");
  const draftReviewerInput = document.querySelector("#draft-reviewer");
  const draftRationaleInput = document.querySelector("#draft-rationale");
  const createDraftButton = document.querySelector("#create-customer-draft");
  const useReferenceTargetButton = document.querySelector(
    "#use-reference-target"
  );
  const useInferdromeTargetButton = document.querySelector(
    "#use-inferdrome-target"
  );
  const evidenceMethodInputs = Array.from(
    document.querySelectorAll('input[name="evidence_method"]')
  );
  const evidenceMethodNote = document.querySelector(
    "#evidence-method-note"
  );
  const draftStatus = document.querySelector("#draft-status");
  const endpointDetails = document.querySelector(".endpoint-fields");
  const confirmationPanel = document.querySelector("#confirmation-panel");
  const confirmationStatus = document.querySelector("#confirmation-status");
  const customerReviewInvitation = document.querySelector(
    "#review-invitation"
  );
  const customerReviewState = document.querySelector("#customer-review-state");
  const customerReviewHeading = document.querySelector(
    "#customer-review-heading"
  );
  const customerReviewExpiry = document.querySelector(
    "#customer-review-expiry"
  );
  const customerReviewLink = document.querySelector("#customer-review-link");
  const refreshCustomerReviewButton = document.querySelector(
    "#refresh-customer-review"
  );
  const reissueCustomerReviewButton = document.querySelector(
    "#reissue-customer-review"
  );
  const pendingReviewActions = document.querySelector(
    "#pending-review-actions"
  );
  const changesRequestedActions = document.querySelector(
    "#changes-requested-actions"
  );
  const startRevisionButton = document.querySelector("#start-revision");
  const revisionPanel = document.querySelector("#revision-panel");
  const continueRevision = document.querySelector("#continue-revision");
  const freezePanel = document.querySelector("#freeze-panel");
  const freezeForm = document.querySelector("#freeze-form");
  const freezeButton = document.querySelector("#freeze-contract");
  const freezeStatus = document.querySelector("#freeze-status");
  const completionPanel = document.querySelector("#agreement-complete");
  const continueToProof = document.querySelector("#continue-to-proof");
  const errorPanel = document.querySelector("#agreement-error");
  const draftControls = Object.freeze([
    targetProviderInput,
    endpointClassInput,
    endpointInput,
    modelInput,
    draftReviewerInput,
    draftRationaleInput,
  ]);

  let agreementState = null;
  let inFlight = null;
  let pendingDraftAttempt = null;
  let pendingReviewReissueAttempt = null;
  let pendingFreezeAttempt = null;
  let pendingRevisionAttempt = null;

  class SafeRequestError extends Error {
    constructor(statusCode, retrySameAttempt) {
      super("Agreement request failed.");
      this.name = "SafeRequestError";
      this.statusCode = statusCode;
      this.retrySameAttempt = retrySameAttempt;
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

  function isSingleLineText(value, maximum) {
    return (
      isSafeBoundedText(value, maximum) &&
      value === value.trim() &&
      !value.includes("\n") &&
      !value.includes("\r")
    );
  }

  function isExactInteger(value, minimum, maximum) {
    return (
      typeof value === "number" &&
      Number.isInteger(value) &&
      value >= minimum &&
      value <= maximum
    );
  }

  function isContractVersion(value) {
    return typeof value === "string" && /^[1-9]\d*$/.test(value);
  }

  function hasValidVersionLineage(value) {
    if (
      !value ||
      !isSingleLineText(value.contract_id, 160) ||
      !isContractVersion(value.contract_version)
    ) {
      return false;
    }
    const numericVersion = Number(value.contract_version);
    return numericVersion === 1
      ? value.parent_version === null
      : value.parent_version ===
          `${value.contract_id}@${numericVersion - 1}`;
  }

  function isTrustedTimestamp(value) {
    return Boolean(
      typeof value === "string" &&
        /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$/.test(
          value
        ) &&
        Number.isFinite(Date.parse(value))
    );
  }

  function isExactTargetUrl(value) {
    if (
      !isSingleLineText(value, 2048) ||
      value.includes("?") ||
      value.includes("#")
    ) {
      return false;
    }
    try {
      const parsed = new URL(value);
      return Boolean(
        (parsed.protocol === "https:" || parsed.protocol === "http:") &&
          parsed.hostname.length > 0 &&
          parsed.username === "" &&
          parsed.password === "" &&
          parsed.search === "" &&
          parsed.hash === "" &&
          parsed.href === value
      );
    } catch {
      return false;
    }
  }

  function isTrustedReviewUrl(value) {
    if (typeof value !== "string" || !REVIEW_URL_PATTERN.test(value)) {
      return false;
    }
    try {
      const parsed = new URL(value, window.location.origin);
      return Boolean(
        parsed.origin === window.location.origin &&
          parsed.pathname === value &&
          parsed.search === "" &&
          parsed.hash === ""
      );
    } catch {
      return false;
    }
  }

  function isTrustedApiPath(value) {
    if (
      typeof value !== "string" ||
      !pocId ||
      value.includes("?") ||
      value.includes("#")
    ) {
      return false;
    }
    try {
      const parsed = new URL(value, window.location.origin);
      return Boolean(
        parsed.origin === window.location.origin &&
          parsed.pathname === value &&
          parsed.search === "" &&
          parsed.hash === "" &&
          (value === pocApi ||
            value === agreementApi ||
            value === reviewApi ||
            value === freezeApi ||
            value === revisionApi)
      );
    } catch {
      return false;
    }
  }

  function isTrustedDefinition(definition) {
    if (
      !hasExactKeys(definition, DEFINITION_KEYS) ||
      !PROPOSAL_ID_PATTERN.test(definition.proposal_id) ||
      !DEFINITION_ID_PATTERN.test(definition.definition_id) ||
      !SHA256_PATTERN.test(definition.definition_sha256) ||
      !METRICS.includes(definition.metric) ||
      !SOURCE_KINDS.includes(definition.source_kind) ||
      !isSafeBoundedText(definition.source_quote, 4000) ||
      !isSafeBoundedText(definition.normalized_claim, 2000)
    ) {
      return false;
    }
    const config = METRIC_CONFIG[definition.metric];
    const operatorValid =
      definition.metric === "TTFT_P95_MS"
        ? ["LT", "LTE"].includes(definition.operator)
        : definition.operator === "LT";
    const thresholdValid =
      definition.metric === "TTFT_P95_MS"
        ? definition.threshold > 0 && definition.threshold <= config.maximum
        : definition.threshold > 0 && definition.threshold < config.maximum;
    return Boolean(
      definition.unit === config.unit &&
        operatorValid &&
        typeof definition.threshold === "number" &&
        Number.isFinite(definition.threshold) &&
        thresholdValid &&
        isExactInteger(definition.minimum_samples, 1, 1000) &&
        isExactInteger(definition.concurrency, 1, 32) &&
        definition.concurrency <= definition.minimum_samples &&
        isExactInteger(definition.prompt_tokens_min, 1, 1000000) &&
        isExactInteger(definition.prompt_tokens_max, 1, 1000000) &&
        definition.prompt_tokens_min <= definition.prompt_tokens_max &&
        isExactInteger(definition.output_tokens_min, 1, 1000000) &&
        isExactInteger(definition.output_tokens_max, 1, 1000000) &&
        definition.output_tokens_min <= definition.output_tokens_max &&
        isTrustedTimestamp(definition.defined_at)
    );
  }

  function isTrustedPOCDraft(payload) {
    const nextRouteBySource = {
      DOCUMENT: "document",
      EMAIL: "email",
      EXISTING_CONTRACT: "existing_contract",
      MEETING: "meeting",
    };
    if (
      !payload ||
      typeof payload !== "object" ||
      Array.isArray(payload) ||
      !hasExactKeys(payload, POC_DRAFT_KEYS) ||
      payload.poc_id !== pocId ||
      !isSafeBoundedText(payload.display_name, 160) ||
      !isSafeBoundedText(payload.customer_label, 160) ||
      !isSafeBoundedText(payload.use_case, 500) ||
      !isSafeBoundedText(payload.owner, 160) ||
      !SOURCE_KINDS.includes(payload.first_source_choice) ||
      payload.next_intake_route !==
        nextRouteBySource[payload.first_source_choice] ||
      payload.source_ingestion_state !== "NOT_STARTED" ||
      !isTrustedTimestamp(payload.created_at) ||
      !isTrustedTimestamp(payload.updated_at) ||
      Date.parse(payload.updated_at) < Date.parse(payload.created_at) ||
      payload.archive_state !== "ACTIVE" ||
      payload.archived_at !== null
    ) {
      return false;
    }
    return true;
  }

  function hasExecutableDefinitionPair(definitions) {
    return Boolean(
      Array.isArray(definitions) &&
        definitions.length === 2 &&
        METRICS.every(
          (metric) =>
            definitions.filter((definition) => definition.metric === metric)
              .length === 1
        )
    );
  }

  function isTrustedDraft(draft) {
    return Boolean(
      hasExactKeys(draft, DRAFT_KEYS) &&
        DRAFT_ID_PATTERN.test(draft.draft_id) &&
        SHA256_PATTERN.test(draft.draft_sha256) &&
        hasValidVersionLineage(draft) &&
        isTrustedTimestamp(draft.created_at) &&
        isSingleLineText(draft.target_provider, 160) &&
        isSingleLineText(draft.endpoint_class, 160) &&
        isExactTargetUrl(draft.endpoint) &&
        isSingleLineText(draft.model, 300) &&
        EVIDENCE_METHODS.includes(draft.evidence_method) &&
        isSingleLineText(draft.reviewer, 160) &&
        isSafeBoundedText(draft.rationale, 2000)
    );
  }

  function isTrustedConfirmation(confirmation) {
    return Boolean(
      hasExactKeys(confirmation, CONFIRMATION_KEYS) &&
        CONFIRMATION_ID_PATTERN.test(confirmation.confirmation_id) &&
        SHA256_PATTERN.test(confirmation.draft_sha256) &&
        isTrustedTimestamp(confirmation.confirmed_at) &&
        isSingleLineText(confirmation.confirmer, 160) &&
        ["CONFIRM", "REQUEST_CHANGES"].includes(confirmation.decision) &&
        (confirmation.decision !== "CONFIRM" ||
          confirmation.agreement_acknowledged === true) &&
        typeof confirmation.agreement_acknowledged === "boolean" &&
        isSafeBoundedText(confirmation.rationale, 2000)
    );
  }

  function isTrustedCustomerReview(customerReview) {
    return Boolean(
      hasExactKeys(customerReview, CUSTOMER_REVIEW_KEYS) &&
        REVIEW_ID_PATTERN.test(customerReview.review_id) &&
        ["PENDING", "EXPIRED", "CONFIRMED", "CHANGES_REQUESTED"].includes(
          customerReview.status
        ) &&
        isTrustedReviewUrl(customerReview.review_url) &&
        isTrustedTimestamp(customerReview.created_at) &&
        isTrustedTimestamp(customerReview.expires_at) &&
        Date.parse(customerReview.expires_at) >
          Date.parse(customerReview.created_at)
    );
  }

  function isTrustedFrozenContract(contract) {
    return Boolean(
      hasExactKeys(contract, FROZEN_CONTRACT_KEYS) &&
        hasValidVersionLineage(contract) &&
        SHA256_PATTERN.test(contract.canonical_hash) &&
        CONFIRMATION_ID_PATTERN.test(contract.confirmation_id) &&
        isTrustedTimestamp(contract.frozen_at) &&
        isSingleLineText(contract.target_provider, 160) &&
        isSingleLineText(contract.endpoint_class, 160) &&
        isExactTargetUrl(contract.endpoint) &&
        isSingleLineText(contract.model, 300) &&
        EVIDENCE_METHODS.includes(contract.evidence_method)
    );
  }

  function isTrustedRevision(revision) {
    if (
      !hasExactKeys(revision, REVISION_KEYS) ||
      !REVISION_ID_PATTERN.test(revision.revision_id) ||
      !isExactInteger(revision.revision_number, 1, 32) ||
      !isSingleLineText(revision.parent_contract_id, 160) ||
      !isContractVersion(revision.parent_contract_version) ||
      !isContractVersion(revision.contract_version) ||
      Number(revision.contract_version) !== revision.revision_number + 1 ||
      Number(revision.parent_contract_version) !== revision.revision_number ||
      !SHA256_PATTERN.test(revision.parent_draft_sha256) ||
      !isTrustedTimestamp(revision.requested_at) ||
      !isSafeBoundedText(revision.request_rationale, 2000)
    ) {
      return false;
    }
    return true;
  }

  function isTrustedCountingPolicy(policy) {
    return Boolean(
      hasExactKeys(policy, COUNTING_POLICY_KEYS) &&
        policy.schema_version === "exitspec.measurement-population.v1" &&
        SHA256_PATTERN.test(policy.policy_sha256) &&
        isExactInteger(policy.exact_attempts, 1, 1000) &&
        policy.warmups_included === false &&
        policy.preflight_included === false &&
        policy.retries === 0 &&
        policy.latency_population ===
          "successful_measured_attempts_with_valid_ttft" &&
        policy.latency_failed_attempts ===
          "excluded_from_latency_counted_in_reliability" &&
        policy.reliability_denominator === "all_measured_attempts" &&
        Array.isArray(policy.external_error_outcomes) &&
        policy.external_error_outcomes.join("|") ===
          "HTTP_ERROR|TIMEOUT|PROTOCOL_ERROR|TRANSPORT_ERROR" &&
        policy.invalid_evidence_disposition === "NOT_PROVEN"
    );
  }

  function targetMatches(left, right) {
    return Boolean(
      left.target_provider === right.target_provider &&
        left.endpoint_class === right.endpoint_class &&
        left.endpoint === right.endpoint &&
        left.model === right.model &&
        left.evidence_method === right.evidence_method
    );
  }

  function isTrustedAgreementProjection(payload) {
    if (
      !hasExactKeys(payload, AGREEMENT_KEYS) ||
      payload.poc_id !== pocId ||
      !Array.isArray(payload.definitions) ||
      payload.definitions.length > 1024 ||
      !payload.definitions.every(isTrustedDefinition) ||
      !Array.isArray(payload.not_proven_claims) ||
      payload.not_proven_claims.length > 1024 ||
      !payload.not_proven_claims.every((claim) =>
        isSafeBoundedText(claim, 2000)
      ) ||
      (payload.draft !== null && !isTrustedDraft(payload.draft)) ||
      (payload.counting_policy !== null &&
        !isTrustedCountingPolicy(payload.counting_policy)) ||
      (payload.customer_review !== null &&
        !isTrustedCustomerReview(payload.customer_review)) ||
      (payload.confirmation !== null &&
        !isTrustedConfirmation(payload.confirmation)) ||
      (payload.frozen_contract !== null &&
        !isTrustedFrozenContract(payload.frozen_contract)) ||
      (payload.revision !== null && !isTrustedRevision(payload.revision)) ||
      !isExactInteger(payload.superseded_version_count, 0, 32) ||
      (payload.revision === null && payload.superseded_version_count !== 0) ||
      (payload.revision !== null &&
        payload.superseded_version_count !== payload.revision.revision_number)
    ) {
      return false;
    }
    const proposalIds = payload.definitions.map(
      (definition) => definition.proposal_id
    );
    const definitionIds = payload.definitions.map(
      (definition) => definition.definition_id
    );
    if (
      new Set(proposalIds).size !== proposalIds.length ||
      new Set(definitionIds).size !== definitionIds.length
    ) {
      return false;
    }
    if (
      payload.draft === null &&
      (payload.customer_review !== null ||
        payload.counting_policy !== null ||
        payload.confirmation !== null ||
        payload.frozen_contract !== null)
    ) {
      return false;
    }
    if (payload.draft !== null && payload.customer_review === null) {
      return false;
    }
    if (payload.draft !== null && payload.counting_policy === null) {
      return false;
    }
    if (
      payload.draft !== null &&
      payload.revision !== null &&
      (payload.draft.contract_id !== payload.revision.parent_contract_id ||
        payload.draft.contract_version !== payload.revision.contract_version ||
        payload.draft.parent_version !==
          `${payload.revision.parent_contract_id}@${payload.revision.parent_contract_version}`)
    ) {
      return false;
    }
    if (
      payload.confirmation !== null &&
      (payload.draft === null ||
        payload.confirmation.draft_sha256 !== payload.draft.draft_sha256)
    ) {
      return false;
    }
    if (
      payload.customer_review !== null &&
      ((payload.confirmation === null &&
        !["PENDING", "EXPIRED"].includes(payload.customer_review.status)) ||
        (payload.confirmation?.decision === "CONFIRM" &&
          payload.customer_review.status !== "CONFIRMED") ||
        (payload.confirmation?.decision === "REQUEST_CHANGES" &&
          payload.customer_review.status !== "CHANGES_REQUESTED"))
    ) {
      return false;
    }
    if (
      payload.frozen_contract !== null &&
      (payload.draft === null ||
        payload.confirmation === null ||
        payload.frozen_contract.confirmation_id !==
          payload.confirmation.confirmation_id ||
        payload.confirmation.decision !== "CONFIRM" ||
        !targetMatches(payload.frozen_contract, payload.draft))
    ) {
      return false;
    }
    return true;
  }

  function isTrustedDraftActionResponse(payload, attempt) {
    return Boolean(
      hasExactKeys(payload, ["disposition", "draft", "poc_id"]) &&
        payload.poc_id === pocId &&
        DISPOSITIONS.includes(payload.disposition) &&
        isTrustedDraft(payload.draft) &&
        payload.draft.target_provider === attempt.payload.target_provider &&
        payload.draft.endpoint_class === attempt.payload.endpoint_class &&
        payload.draft.endpoint === attempt.payload.endpoint &&
        payload.draft.model === attempt.payload.model &&
        payload.draft.evidence_method === attempt.payload.evidence_method &&
        payload.draft.reviewer === attempt.payload.reviewer &&
        payload.draft.rationale === attempt.payload.rationale &&
        (agreementState?.revision === null
          ? payload.draft.contract_version === "1" &&
            payload.draft.parent_version === null
          : agreementState?.revision !== null &&
            payload.draft.contract_id ===
              agreementState.revision.parent_contract_id &&
            payload.draft.contract_version ===
              agreementState.revision.contract_version)
    );
  }

  function isTrustedFreezeActionResponse(payload) {
    return Boolean(
      hasExactKeys(payload, ["disposition", "frozen_contract", "poc_id"]) &&
        payload.poc_id === pocId &&
        DISPOSITIONS.includes(payload.disposition) &&
        isTrustedFrozenContract(payload.frozen_contract) &&
        agreementState &&
        agreementState.draft &&
        agreementState.confirmation &&
        payload.frozen_contract.confirmation_id ===
          agreementState.confirmation.confirmation_id &&
        payload.frozen_contract.contract_id === agreementState.draft.contract_id &&
        payload.frozen_contract.contract_version ===
          agreementState.draft.contract_version &&
        targetMatches(payload.frozen_contract, agreementState.draft)
    );
  }

  function isTrustedReviewReissueResponse(payload) {
    return Boolean(
      hasExactKeys(payload, ["customer_review", "disposition", "poc_id"]) &&
        payload.poc_id === pocId &&
        DISPOSITIONS.includes(payload.disposition) &&
        isTrustedCustomerReview(payload.customer_review) &&
        payload.customer_review.status === "PENDING"
    );
  }

  function isTrustedRevisionActionResponse(payload) {
    return Boolean(
      hasExactKeys(payload, ["disposition", "poc_id", "revision"]) &&
        payload.poc_id === pocId &&
        DISPOSITIONS.includes(payload.disposition) &&
        isTrustedRevision(payload.revision) &&
        agreementState?.draft !== null &&
        agreementState?.confirmation?.decision === "REQUEST_CHANGES" &&
        payload.revision.parent_contract_id ===
          agreementState.draft.contract_id &&
        payload.revision.parent_contract_version ===
          agreementState.draft.contract_version &&
        payload.revision.parent_draft_sha256 ===
          agreementState.draft.draft_sha256
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
        redirect: "error",
        referrerPolicy: "same-origin",
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
      responseUrl.search !== "" ||
      responseUrl.hash !== ""
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
    const contentType = (
      response.headers.get("content-type") || ""
    ).toLowerCase();
    if (contentType.split(";", 1)[0].trim() !== "application/json") {
      throw new SafeRequestError(response.status, true);
    }
    const payload = await response.json().catch(() => null);
    if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
      throw new SafeRequestError(response.status, true);
    }
    return payload;
  }

  function newOperationKey(prefix) {
    if (
      !window.crypto ||
      typeof window.crypto.randomUUID !== "function"
    ) {
      return null;
    }
    return `${prefix}-${window.crypto.randomUUID()}`;
  }

  function clearError() {
    errorPanel.hidden = true;
    errorPanel.textContent = "";
  }

  function safeFailureCopy(error, action) {
    if (!(error instanceof SafeRequestError)) {
      return `The ${action} action could not be completed safely. Retry the same attempt.`;
    }
    if (error.statusCode === 400 || error.statusCode === 422) {
      return `The ${action} request was not accepted. Review every required value.`;
    }
    if (error.statusCode === 404) {
      return "This POC agreement is unavailable. No lifecycle action was completed.";
    }
    if (
      error.statusCode === 403 ||
      error.statusCode === 409 ||
      error.statusCode === 415
    ) {
      return `The ${action} request conflicts with the current agreement state. Reload before continuing.`;
    }
    return `The ${action} response was interrupted or could not be trusted. Retry uses the same operation key.`;
  }

  function selectedEvidenceMethod() {
    const selected = evidenceMethodInputs.find((input) => input.checked);
    return selected && EVIDENCE_METHODS.includes(selected.value)
      ? selected.value
      : null;
  }

  function validatedDraftFields() {
    const targetProvider = targetProviderInput.value.trim();
    const endpointClass = endpointClassInput.value.trim();
    const endpoint = endpointInput.value.trim();
    const model = modelInput.value.trim();
    const reviewer = draftReviewerInput.value.trim();
    const rationale = draftRationaleInput.value.trim();
    const evidenceMethod = selectedEvidenceMethod();
    if (
      !isSingleLineText(targetProvider, 160) ||
      !isSingleLineText(endpointClass, 160) ||
      !isExactTargetUrl(endpoint) ||
      !isSingleLineText(model, 300) ||
      evidenceMethod === null ||
      !isSingleLineText(reviewer, 160) ||
      !isSafeBoundedText(rationale, 2000)
    ) {
      return null;
    }
    return {
      target_provider: targetProvider,
      endpoint_class: endpointClass,
      endpoint,
      model,
      evidence_method: evidenceMethod,
      reviewer,
      rationale,
    };
  }

  function setControlsAvailability(controls, enabled) {
    controls.forEach((control) => {
      control.disabled = !enabled;
    });
  }

  function updateDraftControls() {
    const available = Boolean(
      agreementState &&
        hasExecutableDefinitionPair(agreementState.definitions) &&
        agreementState.draft === null
    );
    const fieldsValid = validatedDraftFields() !== null;
    const editable =
      available && inFlight === null && pendingDraftAttempt === null;
    setControlsAvailability(draftControls, editable);
    setControlsAvailability(evidenceMethodInputs, editable);
    useReferenceTargetButton.disabled = !editable;
    useInferdromeTargetButton.disabled = !editable;
    createDraftButton.disabled = Boolean(
      !available ||
        inFlight !== null ||
        (!pendingDraftAttempt && !fieldsValid)
    );
    createDraftButton.textContent = pendingDraftAttempt
      ? "Retry create customer draft"
      : "Create customer draft";
    draftStatus.textContent =
      inFlight === "draft"
        ? "Creating the bounded customer draft…"
        : pendingDraftAttempt
          ? "The response was interrupted. Retry will reuse the same draft key."
          : fieldsValid
            ? "Verify the target and create the customer draft."
            : "Complete the exact target, reviewer, and rationale.";
    const method = selectedEvidenceMethod();
    evidenceMethodNote.textContent =
      method === "EXIT_SPEC_STREAMING_PROBE"
        ? "ExitSpec will execute the frozen streaming workload."
        : method === "INFERDROME_EXTERNAL_BUNDLE"
          ? "ExitSpec will import, validate, and independently recalculate one sealed Inferdrome bundle. Native vLLM TTFT must still match the frozen definition."
          : "Select the evidence method first.";
  }

  function useReferenceTarget() {
    if (
      !agreementState ||
      agreementState.draft !== null ||
      inFlight !== null ||
      pendingDraftAttempt !== null
    ) {
      return;
    }
    targetProviderInput.value = REFERENCE_TARGET.target_provider;
    endpointClassInput.value = REFERENCE_TARGET.endpoint_class;
    endpointInput.value =
      `${window.location.origin}${REFERENCE_TARGET.path}`;
    modelInput.value = REFERENCE_TARGET.model;
    evidenceMethodInputs.forEach((input) => {
      input.checked = input.value === "EXIT_SPEC_STREAMING_PROBE";
    });
    endpointDetails.open = true;
    clearError();
    updateDraftControls();
    draftReviewerInput.focus();
  }

  function useInferdromeTarget() {
    if (
      !agreementState ||
      agreementState.draft !== null ||
      inFlight !== null ||
      pendingDraftAttempt !== null
    ) {
      return;
    }
    targetProviderInput.value = INFERDROME_TARGET.target_provider;
    endpointClassInput.value = INFERDROME_TARGET.endpoint_class;
    endpointInput.value = INFERDROME_TARGET.endpoint;
    modelInput.value = INFERDROME_TARGET.model;
    evidenceMethodInputs.forEach((input) => {
      input.checked = input.value === "INFERDROME_EXTERNAL_BUNDLE";
    });
    endpointDetails.open = true;
    clearError();
    updateDraftControls();
    draftReviewerInput.focus();
  }

  function updateFreezeControls() {
    const available = Boolean(
      agreementState &&
        agreementState.draft !== null &&
        agreementState.confirmation !== null &&
        agreementState.confirmation.decision === "CONFIRM" &&
        agreementState.frozen_contract === null
    );
    freezeButton.disabled = !available || inFlight !== null;
    freezeButton.textContent = pendingFreezeAttempt
      ? "Retry freeze confirmed contract"
      : "Freeze confirmed contract";
    freezeStatus.textContent =
      inFlight === "freeze"
        ? "Freezing the confirmed contract…"
        : pendingFreezeAttempt
          ? "The response was interrupted. Retry will reuse the same freeze key."
          : "Recorded confirmation matches the exact agreement.";
  }

  function definitionRule(definition) {
    const config = METRIC_CONFIG[definition.metric];
    const operator = definition.operator === "LT" ? "<" : "≤";
    return `${METRIC_LABELS[definition.metric]} ${operator} ${definition.threshold} ${config.shortUnit}`;
  }

  function appendDefinitionCard(definition, position) {
    const card = document.createElement("article");
    card.className = "definition-card";
    const header = document.createElement("header");
    const heading = document.createElement("h4");
    heading.textContent = `Criterion ${position + 1}`;
    const sourceKind = document.createElement("span");
    sourceKind.textContent = SOURCE_LABELS[definition.source_kind];
    header.append(heading, sourceKind);

    const quote = document.createElement("blockquote");
    quote.textContent = definition.source_quote;
    const rule = document.createElement("p");
    rule.className = "definition-rule";
    rule.textContent = definitionRule(definition);
    const workload = document.createElement("p");
    workload.className = "definition-workload";
    workload.textContent =
      `${definition.minimum_samples} samples · concurrency ${definition.concurrency} · ` +
      `prompt ${definition.prompt_tokens_min}–${definition.prompt_tokens_max} · ` +
      `output ${definition.output_tokens_min}–${definition.output_tokens_max}`;
    card.append(header, quote, rule, workload);
    document.querySelector("#definition-list").append(card);
  }

  function renderDefinitions() {
    const list = document.querySelector("#definition-list");
    list.replaceChildren();
    const definitions = agreementState.definitions;
    document.querySelector("#definition-count").textContent =
      `${definitions.length} ${definitions.length === 1 ? "definition" : "definitions"}`;
    if (definitions.length === 0) {
      const empty = document.createElement("p");
      empty.className = "empty-copy";
      empty.textContent =
        "No bounded definitions are available. Agreement drafting remains blocked.";
      list.append(empty);
      return;
    }
    definitions.forEach(appendDefinitionCard);
  }

  function appendCustomerCriterion(definition) {
    const row = document.createElement("p");
    row.className = "customer-criterion";
    const claim = document.createElement("span");
    claim.textContent = definition.normalized_claim;
    const rule = document.createElement("strong");
    rule.textContent = definitionRule(definition);
    row.append(claim, rule);
    document.querySelector("#customer-criteria-list").append(row);
  }

  function renderCustomerAgreement() {
    const draft = agreementState.draft;
    const external =
      draft.evidence_method === "INFERDROME_EXTERNAL_BUNDLE";
    document.querySelector("#review-evidence-method").textContent = external
      ? "Sealed Inferdrome evidence bundle"
      : "ExitSpec streaming probe";
    document.querySelector("#review-target-provider").textContent =
      draft.target_provider;
    document.querySelector("#review-model").textContent = draft.model;
    document.querySelector("#review-endpoint-class").textContent =
      draft.endpoint_class;
    document.querySelector("#review-endpoint").textContent = draft.endpoint;
    document.querySelector("#review-evidence-method-boundary").textContent =
      external
        ? "ExitSpec will treat the bundle as untrusted input, independently recalculate supported measurements, and fail closed on semantic mismatch. Native vLLM first-event TTFT does not automatically satisfy the frozen first-nonempty-content rule."
        : "ExitSpec will execute the exact frozen streaming workload and verify its own sealed artifacts before releasing a verdict.";
    const criteria = document.querySelector("#customer-criteria-list");
    criteria.replaceChildren();
    agreementState.definitions.forEach(appendCustomerCriterion);
    const counting = agreementState.counting_policy;
    const countingPanel = document.querySelector("#counting-policy");
    countingPanel.hidden = counting === null;
    if (counting !== null) {
      document.querySelector("#counting-attempts").textContent = String(
        counting.exact_attempts
      );
      document.querySelector("#counting-latency").textContent =
        "Successful requests with valid first-token timing";
      document.querySelector("#counting-reliability").textContent =
        `All ${counting.exact_attempts} attempts; HTTP, timeout, protocol, and transport errors count`;
      document.querySelector("#counting-exclusions").textContent =
        "Warmups and readiness preflight are excluded. No retries. Invalid or incomplete evidence is NOT PROVEN.";
    }
    const notProven = document.querySelector(
      "#customer-not-proven-list"
    );
    notProven.replaceChildren();
    if (agreementState.not_proven_claims.length === 0) {
      const empty = document.createElement("li");
      empty.textContent = "No reviewed source claim was excluded.";
      notProven.append(empty);
    } else {
      agreementState.not_proven_claims.forEach((claim) => {
        const item = document.createElement("li");
        item.textContent = claim;
        notProven.append(item);
      });
    }
  }

  function formatReviewExpiry(value) {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) {
      return "Unavailable";
    }
    return new Intl.DateTimeFormat(undefined, {
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
      timeZoneName: "short",
    }).format(date);
  }

  function renderCustomerReviewState() {
    const customerReview = agreementState.customer_review;
    const changesRequested =
      agreementState.confirmation?.decision === "REQUEST_CHANGES";
    const expired = customerReview.status === "EXPIRED";
    customerReviewLink.href = customerReview.review_url;
    customerReviewLink.removeAttribute("aria-disabled");
    customerReviewExpiry.dateTime = customerReview.expires_at;
    customerReviewExpiry.textContent = formatReviewExpiry(
      customerReview.expires_at
    );
    customerReviewInvitation.dataset.state = changesRequested
      ? "changes-requested"
      : expired
        ? "expired"
        : "pending";
    pendingReviewActions.hidden = changesRequested;
    changesRequestedActions.hidden = !changesRequested;
    startRevisionButton.disabled = !changesRequested || inFlight !== null;
    startRevisionButton.textContent = pendingRevisionAttempt
      ? "Retry start revision"
      : "Start revision";
    customerReviewLink.hidden = expired;
    refreshCustomerReviewButton.hidden = expired;
    refreshCustomerReviewButton.disabled = inFlight !== null;
    reissueCustomerReviewButton.hidden = !expired;
    reissueCustomerReviewButton.disabled = inFlight !== null;

    if (changesRequested) {
      customerReviewState.textContent = "Review complete";
      customerReviewHeading.textContent = "Customer requested changes";
      confirmationStatus.textContent =
        inFlight === "revision"
          ? "Preserving this version and opening the revision…"
          : "This version cannot be frozen or edited in place.";
      return;
    }

    if (expired) {
      customerReviewState.textContent = "Review link expired";
      customerReviewHeading.textContent = "Issue a new review link";
      confirmationStatus.textContent =
        inFlight === "reissue"
          ? "Issuing a new link for this unchanged agreement…"
          : "The agreement is unchanged. Replace only the expired link.";
      return;
    }

    customerReviewState.textContent = "Waiting for customer";
    customerReviewHeading.textContent = "Customer confirmation is pending";
    confirmationStatus.textContent =
      inFlight === "refresh"
        ? "Checking the customer decision…"
        : "Open or share the review link, then refresh after the customer decides.";
  }

  async function refreshCustomerReview() {
    if (
      inFlight !== null ||
      !agreementState ||
      agreementState.customer_review?.status !== "PENDING"
    ) {
      return;
    }
    inFlight = "refresh";
    clearError();
    renderCustomerReviewState();
    try {
      await reconcileAgreement();
    } catch (error) {
      inFlight = null;
      confirmationStatus.textContent =
        "Status could not be refreshed. The agreement is unchanged.";
      errorPanel.textContent = safeFailureCopy(error, "review refresh");
      errorPanel.hidden = false;
      refreshCustomerReviewButton.disabled = false;
    }
  }

  async function reissueCustomerReview() {
    if (
      inFlight !== null ||
      !agreementState ||
      agreementState.customer_review?.status !== "EXPIRED"
    ) {
      return;
    }
    if (!pendingReviewReissueAttempt) {
      const idempotencyKey = newOperationKey("agreement-review-reissue");
      if (!idempotencyKey) {
        confirmationStatus.textContent =
          "A safe review-link operation key could not be created.";
        return;
      }
      pendingReviewReissueAttempt = {
        payload: { idempotency_key: idempotencyKey },
      };
    }
    inFlight = "reissue";
    clearError();
    renderCustomerReviewState();
    try {
      const response = await requestJson(reviewApi, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(pendingReviewReissueAttempt.payload),
      });
      if (!isTrustedReviewReissueResponse(response)) {
        throw new SafeRequestError(200, true);
      }
      pendingReviewReissueAttempt = null;
      await reconcileAgreement();
    } catch (error) {
      inFlight = null;
      if (error instanceof SafeRequestError && !error.retrySameAttempt) {
        pendingReviewReissueAttempt = null;
      }
      confirmationStatus.textContent =
        "The review link was not replaced. Retry the same attempt.";
      errorPanel.textContent = safeFailureCopy(error, "review-link reissue");
      errorPanel.hidden = false;
      reissueCustomerReviewButton.disabled = false;
    }
  }

  async function startRevision() {
    if (
      inFlight !== null ||
      !agreementState ||
      agreementState.confirmation?.decision !== "REQUEST_CHANGES" ||
      agreementState.frozen_contract !== null
    ) {
      return;
    }
    if (!pendingRevisionAttempt) {
      const idempotencyKey = newOperationKey("agreement-revision");
      if (!idempotencyKey) {
        confirmationStatus.textContent =
          "A safe revision operation key could not be created.";
        return;
      }
      pendingRevisionAttempt = {
        payload: { idempotency_key: idempotencyKey },
      };
    }
    inFlight = "revision";
    clearError();
    renderCustomerReviewState();
    try {
      const response = await requestJson(revisionApi, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(pendingRevisionAttempt.payload),
      });
      if (!isTrustedRevisionActionResponse(response)) {
        throw new SafeRequestError(200, true);
      }
      pendingRevisionAttempt = null;
      const destination = `/app/pocs/${encodeURIComponent(pocId)}/sources/new`;
      window.location.replace(destination);
    } catch (error) {
      inFlight = null;
      if (error instanceof SafeRequestError && !error.retrySameAttempt) {
        pendingRevisionAttempt = null;
      }
      confirmationStatus.textContent =
        "The revision was not started. Retry the same attempt safely.";
      errorPanel.textContent = safeFailureCopy(error, "revision");
      errorPanel.hidden = false;
      startRevisionButton.disabled = false;
    }
  }

  function setCurrentStep(stepName) {
    const steps = [
      ["draft", document.querySelector("#step-draft")],
      ["confirm", document.querySelector("#step-confirm")],
      ["freeze", document.querySelector("#step-freeze")],
    ];
    const currentIndex = steps.findIndex(([name]) => name === stepName);
    steps.forEach(([, element], index) => {
      element.removeAttribute("aria-current");
      element.removeAttribute("data-complete");
      if (index < currentIndex || stepName === "complete") {
        element.setAttribute("data-complete", "true");
      } else if (index === currentIndex) {
        element.setAttribute("aria-current", "step");
      }
    });
  }

  function showOnly(panel) {
    [
      draftForm,
      confirmationPanel,
      revisionPanel,
      freezePanel,
      completionPanel,
    ].forEach(
      (item) => {
        item.hidden = item !== panel;
      }
    );
  }

  function renderAgreementState() {
    renderDefinitions();
    workbench.setAttribute("aria-busy", "false");

    if (agreementState.frozen_contract !== null) {
      const destination = `/app/pocs/${encodeURIComponent(pocId)}`;
      continueToProof.href = destination;
      showOnly(completionPanel);
      setCurrentStep("complete");
      document.querySelector("#current-task-heading").textContent =
        "Agreement lifecycle complete";
      document.querySelector("#current-task-copy").textContent =
        "The confirmed contract is frozen. Execution remains separate.";
      document.querySelector("#frozen-contract-identity").textContent =
        `${agreementState.frozen_contract.contract_id} · ${agreementState.frozen_contract.canonical_hash.slice(0, 12)}…`;
      completionPanel.focus();
      try {
        window.location.replace(destination);
      } catch {
        // The verified fallback panel remains available if navigation is blocked.
      }
      return;
    }

    if (agreementState.confirmation?.decision === "CONFIRM") {
      showOnly(freezePanel);
      setCurrentStep("freeze");
      document.querySelector("#current-task-heading").textContent =
        "Freeze the confirmed contract";
      document.querySelector("#current-task-copy").textContent =
        "Create the immutable identity for this exact confirmed agreement.";
      document.querySelector("#confirmed-by").textContent =
        agreementState.confirmation.confirmer;
      document.querySelector("#confirmed-draft-identity").textContent =
        `${agreementState.draft.draft_id} · ${agreementState.draft.draft_sha256.slice(0, 12)}…`;
      updateFreezeControls();
      freezePanel.focus();
      return;
    }

    if (agreementState.draft !== null) {
      showOnly(confirmationPanel);
      setCurrentStep("confirm");
      renderCustomerAgreement();
      renderCustomerReviewState();
      const changesRequested =
        agreementState.confirmation?.decision === "REQUEST_CHANGES";
      document.querySelector("#current-task-heading").textContent =
        changesRequested
          ? "Customer requested changes"
          : "Get the customer decision";
      document.querySelector("#current-task-copy").textContent =
        changesRequested
          ? "Preserve this version, then capture the requested change as new source evidence."
          : "Share the separate review link. Freeze unlocks only after confirmation.";
      if (!changesRequested) {
        customerReviewLink.focus({ preventScroll: true });
      } else {
        startRevisionButton.focus({ preventScroll: true });
      }
      return;
    }

    if (
      agreementState.revision !== null &&
      !hasExecutableDefinitionPair(agreementState.definitions)
    ) {
      showOnly(revisionPanel);
      setCurrentStep("draft");
      document.querySelector("#current-task-heading").textContent =
        `Build agreement version ${agreementState.revision.contract_version}`;
      document.querySelector("#current-task-copy").textContent =
        "Capture, review, and define the customer's requested changes before drafting.";
      document.querySelector("#revision-copy").textContent =
        `Version ${agreementState.revision.parent_contract_version} is preserved. Continue from the workspace to build version ${agreementState.revision.contract_version}.`;
      continueRevision.href = "/app";
      revisionPanel.focus();
      return;
    }

    showOnly(draftForm);
    setCurrentStep("draft");
    document.querySelector("#current-task-heading").textContent =
      "Prepare the customer agreement";
    document.querySelector("#current-task-copy").textContent =
      "Bind the reviewed definitions to one explicit execution target.";
    updateDraftControls();
    document.querySelector("#definition-summary").focus?.();
  }

  async function reconcileAgreement() {
    const projection = await requestJson(agreementApi);
    if (!isTrustedAgreementProjection(projection)) {
      throw new SafeRequestError(200, true);
    }
    agreementState = projection;
    inFlight = null;
    renderAgreementState();
  }

  function blockAgreement(message) {
    workbench.setAttribute("aria-busy", "false");
    setControlsAvailability(draftControls, false);
    setControlsAvailability(evidenceMethodInputs, false);
    useReferenceTargetButton.disabled = true;
    useInferdromeTargetButton.disabled = true;
    createDraftButton.disabled = true;
    customerReviewLink.removeAttribute("href");
    customerReviewLink.setAttribute("aria-disabled", "true");
    refreshCustomerReviewButton.disabled = true;
    reissueCustomerReviewButton.disabled = true;
    startRevisionButton.disabled = true;
    freezeButton.disabled = true;
    draftStatus.textContent = "Agreement drafting is unavailable.";
    confirmationStatus.textContent = "Agreement confirmation is unavailable.";
    freezeStatus.textContent = "Contract freeze is unavailable.";
    errorPanel.textContent = message;
    errorPanel.hidden = false;
  }

  draftControls.forEach((control) => {
    control.addEventListener("input", () => {
      if (inFlight === null && pendingDraftAttempt === null) {
        clearError();
        updateDraftControls();
      }
    });
  });
  evidenceMethodInputs.forEach((control) => {
    control.addEventListener("change", () => {
      if (inFlight === null && pendingDraftAttempt === null) {
        clearError();
        updateDraftControls();
      }
    });
  });

  useReferenceTargetButton.addEventListener(
    "click",
    useReferenceTarget
  );
  useInferdromeTargetButton.addEventListener(
    "click",
    useInferdromeTarget
  );

  refreshCustomerReviewButton.addEventListener("click", refreshCustomerReview);
  reissueCustomerReviewButton.addEventListener("click", reissueCustomerReview);
  startRevisionButton.addEventListener("click", startRevision);

  draftForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    let actionRecorded = false;
    if (
      inFlight !== null ||
      !agreementState ||
      agreementState.draft !== null ||
      agreementState.definitions.length === 0
    ) {
      return;
    }
    if (!pendingDraftAttempt) {
      const fields = validatedDraftFields();
      const idempotencyKey = newOperationKey("agreement-draft");
      if (!fields || !idempotencyKey || !draftForm.reportValidity()) {
        endpointDetails.open = true;
        draftStatus.textContent =
          "Review the complete execution target and required authoring fields.";
        return;
      }
      pendingDraftAttempt = {
        payload: {
          target_provider: fields.target_provider,
          endpoint_class: fields.endpoint_class,
          endpoint: fields.endpoint,
          model: fields.model,
          evidence_method: fields.evidence_method,
          reviewer: fields.reviewer,
          rationale: fields.rationale,
          idempotency_key: idempotencyKey,
        },
      };
    }

    inFlight = "draft";
    clearError();
    updateDraftControls();
    try {
      const response = await requestJson(agreementApi, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify(pendingDraftAttempt.payload),
      });
      if (!isTrustedDraftActionResponse(response, pendingDraftAttempt)) {
        throw new SafeRequestError(200, true);
      }
      pendingDraftAttempt = null;
      actionRecorded = true;
      await reconcileAgreement();
      draftControls.forEach((control) => {
        control.value = "";
      });
    } catch (error) {
      if (actionRecorded) {
        agreementState = null;
        blockAgreement(
          "The customer draft was recorded, but the authoritative agreement state could not be refreshed. Reload before continuing."
        );
        return;
      }
      if (error instanceof SafeRequestError && !error.retrySameAttempt) {
        pendingDraftAttempt = null;
      }
      errorPanel.textContent = safeFailureCopy(error, "draft");
      errorPanel.hidden = false;
    } finally {
      inFlight = null;
      if (agreementState && agreementState.draft === null) {
        updateDraftControls();
      }
    }
  });

  freezeForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    let actionRecorded = false;
    if (
      inFlight !== null ||
      !agreementState ||
      agreementState.draft === null ||
      agreementState.confirmation === null ||
      agreementState.confirmation.decision !== "CONFIRM" ||
      agreementState.frozen_contract !== null
    ) {
      return;
    }
    if (!pendingFreezeAttempt) {
      const idempotencyKey = newOperationKey("agreement-freeze");
      if (!idempotencyKey) {
        freezeStatus.textContent =
          "A safe freeze operation key could not be created.";
        return;
      }
      pendingFreezeAttempt = {
        payload: {
          idempotency_key: idempotencyKey,
        },
      };
    }

    inFlight = "freeze";
    clearError();
    updateFreezeControls();
    try {
      const response = await requestJson(freezeApi, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify(pendingFreezeAttempt.payload),
      });
      if (!isTrustedFreezeActionResponse(response)) {
        throw new SafeRequestError(200, true);
      }
      pendingFreezeAttempt = null;
      actionRecorded = true;
      await reconcileAgreement();
    } catch (error) {
      if (actionRecorded) {
        agreementState = null;
        blockAgreement(
          "The contract was frozen, but the authoritative agreement state could not be refreshed. Reload before continuing."
        );
        return;
      }
      if (error instanceof SafeRequestError && !error.retrySameAttempt) {
        pendingFreezeAttempt = null;
      }
      errorPanel.textContent = safeFailureCopy(error, "freeze");
      errorPanel.hidden = false;
    } finally {
      inFlight = null;
      if (
        agreementState &&
        agreementState.confirmation !== null &&
        agreementState.frozen_contract === null
      ) {
        updateFreezeControls();
      }
    }
  });

  async function initialise() {
    if (
      !pocId ||
      !pocApi ||
      !agreementApi ||
      !reviewApi ||
      !freezeApi ||
      !revisionApi
    ) {
      blockAgreement(
        "This agreement address is invalid. Return to the POC workspace."
      );
      return;
    }
    try {
      const [draft, projection] = await Promise.all([
        requestJson(pocApi),
        requestJson(agreementApi),
      ]);
      if (
        !isTrustedPOCDraft(draft) ||
        !isTrustedAgreementProjection(projection)
      ) {
        throw new SafeRequestError(200, true);
      }
      agreementState = projection;
      document.querySelector("#poc-title").textContent = draft.display_name;
      document.querySelector("#poc-context").textContent =
        `${draft.customer_label} · ${draft.owner}`;
      renderAgreementState();
    } catch {
      blockAgreement(
        "The agreement state could not be validated. No confirmation or freeze action is available."
      );
    }
  }

  window.addEventListener("pagehide", () => {
    agreementState = null;
    pendingDraftAttempt = null;
    pendingReviewReissueAttempt = null;
    pendingFreezeAttempt = null;
    pendingRevisionAttempt = null;
    draftControls.forEach((control) => {
      control.value = "";
    });
    customerReviewLink.removeAttribute("href");
    document.querySelector("#definition-list").replaceChildren();
    document.querySelector("#customer-criteria-list").replaceChildren();
    document.querySelector("#review-target-provider").textContent = "";
    document.querySelector("#review-model").textContent = "";
    document.querySelector("#review-endpoint-class").textContent = "";
    document.querySelector("#review-endpoint").textContent = "";
  });

  window.addEventListener("focus", () => {
    if (agreementState?.customer_review?.status === "PENDING") {
      refreshCustomerReview();
    }
  });

  initialise();
})();
