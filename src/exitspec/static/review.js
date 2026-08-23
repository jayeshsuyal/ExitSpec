(() => {
  "use strict";

  const POC_ID_PATTERN = /^poc_[a-z0-9][a-z0-9_-]{2,63}$/;
  const $ = (selector) => document.querySelector(selector);
  const elements = {
    main: $("#main-content"),
    loading: $("#loading-state"),
    error: $("#error-state"),
    review: $("#review-view"),
    terminal: $("#terminal-state"),
    retry: $("#retry-load"),
    errorEyebrow: $("#error-eyebrow"),
    errorTitle: $("#error-title"),
    errorMessage: $("#error-message"),
    mockBanner: $("#mock-banner"),
    pocTitle: $("#poc-title"),
    customerName: $("#customer-name"),
    contractId: $("#contract-id"),
    contractVersion: $("#contract-version"),
    criteriaCount: $("#criteria-count"),
    criteriaList: $("#criteria-summary-list"),
    position: $("#criterion-position"),
    criterionTitle: $("#criterion-title"),
    importance: $("#criterion-importance"),
    summary: $("#criterion-summary"),
    quote: $("#source-quote"),
    metric: $("#criterion-metric"),
    threshold: $("#criterion-threshold"),
    sample: $("#criterion-sample"),
    workload: $("#criterion-workload"),
    rule: $("#criterion-rule"),
    adapter: $("#criterion-adapter"),
    criterionOwner: $("#criterion-owner"),
    sourceLocation: $("#criterion-source-location"),
    evidencePolicy: $("#criterion-evidence-policy"),
    countingPolicy: $("#review-counting-policy"),
    countingPopulation: $("#review-counting-population"),
    countingLatency: $("#review-counting-latency"),
    countingReliability: $("#review-counting-reliability"),
    countingBoundary: $("#review-counting-boundary"),
    previous: $("#previous-criterion"),
    next: $("#next-criterion"),
    progress: $("#criterion-progress"),
    progressBar: $("#criterion-progress span"),
    evidenceMethod: $("#evidence-method"),
    targetModel: $("#target-model"),
    targetRuntime: $("#target-runtime"),
    workloadFixture: $("#workload-fixture"),
    workloadSha256: $("#workload-sha256"),
    agreementOwners: $("#agreement-owners"),
    evidenceRetention: $("#evidence-retention"),
    exclusions: $("#excluded-list"),
    identity: $("#reviewer-identity"),
    expiry: $("#review-expiry"),
    identityNotice: $("#identity-notice"),
    agreementManifest: $("#agreement-manifest"),
    form: $("#decision-form"),
    ackGroup: $("#acknowledgement-group"),
    ack: $("#agreement-checkbox"),
    ackLabel: $("#agreement-label"),
    changes: $("#change-details"),
    rationale: $("#change-rationale"),
    formMessage: $("#form-message"),
    confirm: $("#confirm-requirements"),
    requestChanges: $("#request-changes"),
    terminalMark: $("#terminal-mark"),
    terminalEyebrow: $("#terminal-eyebrow"),
    terminalTitle: $("#terminal-title"),
    terminalMessage: $("#terminal-message"),
    terminalContract: $("#terminal-contract"),
    terminalDecision: $("#terminal-decision"),
    terminalRecordedAt: $("#terminal-recorded-at"),
    terminalReviewer: $("#terminal-reviewer"),
    terminalBoundary: $("#terminal-boundary"),
    terminalNextTitle: $("#terminal-next-title"),
    terminalNextDetail: $("#terminal-next-detail"),
    localDemoReturn: $("#local-demo-return"),
    returnToApp: $("#return-to-app"),
    localDemoNotice: $("#local-demo-notice"),
  };

  const params = new URLSearchParams(window.location.search);
  const localSyntheticRequested =
    params.get("mock") === "local-synthetic" ||
    params.get("mode") === "local-synthetic";
  const requestKeys = new Map();
  let token = tokenFromLocation();
  let review = null;
  let criterionIndex = 0;
  let localSynthetic = false;

  function tokenFromLocation() {
    const parts = window.location.pathname.split("/").filter(Boolean);
    const reviewPart = parts.lastIndexOf("review");
    if (reviewPart >= 0 && parts[reviewPart + 1]) {
      try {
        return decodeURIComponent(parts[reviewPart + 1]);
      } catch (_error) {
        return "";
      }
    }
    return (params.get("token") || "").trim();
  }

  function endpoint() {
    return `/api/review/${encodeURIComponent(token)}`;
  }

  function showOnly(target) {
    [elements.loading, elements.error, elements.review, elements.terminal].forEach(
      (section) => {
        section.hidden = section !== target;
      }
    );
    elements.main.setAttribute(
      "aria-busy",
      target === elements.loading ? "true" : "false"
    );
  }

  function focusHeading(container) {
    const heading = container.querySelector("h1");
    if (heading) {
      heading.tabIndex = -1;
      requestAnimationFrame(() => heading.focus());
    }
  }

  function showError(kind) {
    const copy = {
      invalid: [
        "Review unavailable",
        "This review link is not valid.",
        "Check that you opened the complete link, or ask the POC owner for a new one. No decision has been recorded.",
        false,
      ],
      expired: [
        "Review link expired",
        "This review window has closed.",
        "Ask the POC owner to issue a new link for the current contract version. No decision has been recorded.",
        false,
      ],
      network: [
        "Connection interrupted",
        "We couldn’t load this review.",
        "Nothing has changed. Check your connection and try again.",
        true,
      ],
      unavailable: [
        "Review unavailable",
        "We couldn’t load this agreement.",
        "ExitSpec did not receive a complete review record. No decision has been recorded.",
        true,
      ],
    }[kind];

    elements.errorEyebrow.textContent = copy[0];
    elements.errorTitle.textContent = copy[1];
    elements.errorMessage.textContent = copy[2];
    elements.retry.hidden = !copy[3];
    showOnly(elements.error);
    focusHeading(elements.error);
  }

  async function responseJson(response) {
    if (!(response.headers.get("content-type") || "").includes("application/json")) {
      return null;
    }
    try {
      return await response.json();
    } catch (_error) {
      return null;
    }
  }

  function isDisplayString(value, maximum = 2048) {
    return Boolean(
      typeof value === "string" &&
        value.trim() &&
        value === value.trim() &&
        value.length <= maximum &&
        !value.includes("\n") &&
        !value.includes("\r")
    );
  }

  function isOptionalTargetEndpoint(value) {
    if (value === undefined) {
      return true;
    }
    if (!isDisplayString(value)) {
      return false;
    }
    try {
      const parsed = new URL(value);
      return Boolean(
        ["http:", "https:"].includes(parsed.protocol) &&
          parsed.username === "" &&
          parsed.password === "" &&
          parsed.search === "" &&
          parsed.hash === ""
      );
    } catch (_error) {
      return false;
    }
  }

  function displayTarget(record, agreement) {
    const candidates = [
      record?.target_system,
      record?.contract?.target_system,
      agreement?.target_system,
    ];
    return candidates.find(
      (candidate) =>
        candidate &&
        isDisplayString(candidate.provider, 160) &&
        isDisplayString(candidate.endpoint_class, 160) &&
        isDisplayString(candidate.model, 300) &&
        isOptionalTargetEndpoint(candidate.endpoint)
    ) || null;
  }

  function evidenceMethodLabel(value) {
    return {
      EXIT_SPEC_DETERMINISTIC_TOOL_SELECTION:
        "Evaluate with ExitSpec · deterministic tool-selection fixture",
      EXIT_SPEC_STREAMING_PROBE: "Run from ExitSpec · first non-empty content",
      INFERDROME_EXTERNAL_BUNDLE: "Import sealed Inferdrome evidence · native vLLM semantics",
    }[value] || "";
  }

  function hasExactKeys(value, keys) {
    if (!value || typeof value !== "object" || Array.isArray(value)) {
      return false;
    }
    const actual = Object.keys(value).sort();
    const expected = [...keys].sort();
    return (
      actual.length === expected.length &&
      actual.every((key, index) => key === expected[index])
    );
  }

  function hasTrustedManagedCriterion(criterion) {
    const ttft = criterion?.ttft_p95;
    const errorRate = criterion?.error_rate;
    const identity = criterion?.evidence_identity;
    return Boolean(
      criterion?.criterion_type === "inference_performance_v3" &&
        hasExactKeys(ttft, [
          "aggregation",
          "definition_id",
          "metric",
          "minimum_successful_samples",
          "must_pass",
          "operator",
          "population",
          "reducer_id",
          "threshold_ns",
          "unit",
        ]) &&
        ttft.metric === "time_to_first_token" &&
        ttft.definition_id === "vllm_first_choices_event_v0_26" &&
        ttft.aggregation === "p95" &&
        ttft.unit === "nanoseconds" &&
        ttft.operator === "lt" &&
        Number.isInteger(ttft.threshold_ns) &&
        ttft.threshold_ns > 0 &&
        ttft.reducer_id === "nearest_rank_v1" &&
        ttft.population ===
          "successful_measured_requests_with_observed_ttft" &&
        ttft.minimum_successful_samples === 100 &&
        ttft.must_pass === true &&
        hasExactKeys(errorRate, [
          "aggregation",
          "denominator",
          "exact_attempts",
          "metric",
          "must_pass",
          "numerator",
          "operator",
          "threshold_basis_points",
        ]) &&
        errorRate.metric === "error_rate" &&
        errorRate.aggregation === "rate" &&
        errorRate.operator === "lt" &&
        Number.isInteger(errorRate.threshold_basis_points) &&
        errorRate.threshold_basis_points > 0 &&
        errorRate.threshold_basis_points < 10000 &&
        errorRate.numerator ===
          "failed_or_anomalous_native_measured_requests" &&
        errorRate.denominator === "all_measured_requests" &&
        errorRate.exact_attempts === 100 &&
        errorRate.must_pass === true &&
        identity?.schema_version ===
          "exitspec.inferdrome-evidence-identity.v1" &&
        identity.evidence_schema_version === "inferdrome.evidence.v1" &&
        identity.producer_name === "vllm" &&
        identity.producer_version === "0.26.0" &&
        identity.adapter_id === "vllm_bench_serve" &&
        identity.adapter_version === "1.0.0" &&
        identity.configured_max_concurrency > 0 &&
        identity.exact_measured_attempts === 100 &&
        identity.warmup_requests >= 0 &&
        identity.binding_mode === "EXTERNAL_RECEIPT_BINDING" &&
        identity.chronology === "RETROSPECTIVE" &&
        identity.producer_contract_link === "ABSENT" &&
        criterion.concurrency_semantics ===
          "configured_maximum_concurrency_not_observed_overlap"
    );
  }

  function hasTrustedCountingPolicy(criterion) {
    if (criterion?.criterion_type === "inference_performance_v3") {
      return hasTrustedManagedCriterion(criterion);
    }
    if (criterion?.criterion_type !== "inference_performance_v2") {
      return true;
    }
    const policy = criterion.measurement_policy;
    const measured = policy?.measured_population;
    const latency = policy?.latency_population;
    const reliability = policy?.reliability;
    const invalid = policy?.invalid_evidence;
    return Boolean(
      hasExactKeys(policy, [
        "calculation_version",
        "invalid_evidence",
        "latency_population",
        "measured_population",
        "reliability",
        "schema_version",
      ]) &&
        policy.schema_version === "exitspec.measurement-population.v1" &&
        policy.calculation_version === "exitspec.performance-verdicts.v2" &&
        hasExactKeys(measured, [
          "exact_attempts",
          "phases",
          "preflight_included",
          "retries",
          "warmups_included",
        ]) &&
        Array.isArray(measured.phases) &&
        measured.phases.join("|") === "MEASURED" &&
        Number.isInteger(measured.exact_attempts) &&
        measured.exact_attempts === criterion.error_rate?.minimum_attempts &&
        measured.warmups_included === false &&
        measured.preflight_included === false &&
        measured.retries === 0 &&
        hasExactKeys(latency, ["failed_attempts", "population"]) &&
        latency.population ===
          "successful_measured_attempts_with_valid_ttft" &&
        latency.failed_attempts ===
          "excluded_from_latency_counted_in_reliability" &&
        hasExactKeys(reliability, [
          "denominator",
          "numerator",
          "outcomes",
        ]) &&
        reliability.numerator === "external_error_outcomes" &&
        reliability.denominator === "all_measured_attempts" &&
        Array.isArray(reliability.outcomes) &&
        reliability.outcomes.join("|") ===
          "HTTP_ERROR|TIMEOUT|PROTOCOL_ERROR|TRANSPORT_ERROR" &&
        hasExactKeys(invalid, [
          "disposition",
          "integrity_mismatch",
          "record_conditions",
          "terminal_outcomes",
        ]) &&
        Array.isArray(invalid.terminal_outcomes) &&
        invalid.terminal_outcomes.join("|") ===
          "CANCELLED|INTERNAL_ERROR" &&
        Array.isArray(invalid.record_conditions) &&
        invalid.record_conditions.join("|") ===
          "MISSING_RECORD|DUPLICATE_RECORD|EXTRA_RECORD" &&
        invalid.disposition === "NOT_PROVEN" &&
        invalid.integrity_mismatch === "NOT_PROVEN"
    );
  }

  function normalize(payload) {
    const value = payload?.review || payload;
    const agreement = value?.agreement;
    const requiredStrings = [
      value?.review_id,
      value?.status,
      value?.poc?.title,
      value?.poc?.customer_name,
      value?.contract?.id,
      value?.contract?.version,
      value?.evidence_method,
      value?.identity?.display_name,
      value?.identity?.notice,
      value?.expires_at,
    ];
    const agreementStrings = [
      agreement?.id,
      agreement?.version,
      agreement?.customer,
      agreement?.use_case,
      agreement?.target_system?.provider,
      agreement?.target_system?.endpoint_class,
      agreement?.target_system?.model,
      agreement?.workload?.fixture_path,
      agreement?.workload?.sha256,
      agreement?.evidence_retention_policy,
    ];
    const criterionFields = [
      "id",
      "title",
      "plain_language",
      "source_quote",
      "metric",
      "threshold",
      "sample",
      "workload",
    ];
    const criteriaAreComplete =
      Array.isArray(value?.contract?.criteria) &&
      value.contract.criteria.length > 0 &&
      value.contract.criteria.every((criterion) =>
        criterionFields.every(
          (field) =>
            typeof criterion[field] === "string" && criterion[field].trim()
        ) &&
        (criterion.excluded === undefined ||
          (Array.isArray(criterion.excluded) &&
            criterion.excluded.every((item) => typeof item === "string")))
      );
    const contractExclusionsAreValid =
      value?.contract?.excluded === undefined ||
      (Array.isArray(value.contract.excluded) &&
        value.contract.excluded.every((item) => typeof item === "string"));
    const canonicalAgreementIsComplete =
      agreement &&
      agreementStrings.every(
        (item) => typeof item === "string" && item.trim()
      ) &&
      Array.isArray(agreement.criteria) &&
      agreement.criteria.length > 0 &&
      agreement.criteria.every(
        (criterion) =>
          criterion &&
          typeof criterion === "object" &&
          hasTrustedCountingPolicy(criterion)
      ) &&
      Array.isArray(agreement.owners) &&
      agreement.owners.length > 0 &&
      agreement.owners.every(
        (owner) => typeof owner === "string" && owner.trim()
      ) &&
      Array.isArray(agreement.non_goals) &&
      agreement.non_goals.every((item) => typeof item === "string") &&
      agreement.id === value?.contract?.id &&
      agreement.version === value?.contract?.version &&
      agreement.customer === value?.poc?.customer_name &&
      agreement.use_case === value?.poc?.title;
    const targetSystem = displayTarget(value, agreement);
    if (
      !value ||
      requiredStrings.some((item) => typeof item !== "string" || !item.trim()) ||
      !criteriaAreComplete ||
      !contractExclusionsAreValid ||
      !canonicalAgreementIsComplete ||
      !targetSystem ||
      !evidenceMethodLabel(value.evidence_method) ||
      value.contract.evidence_method !== value.evidence_method ||
      (value.poc_id !== undefined && !POC_ID_PATTERN.test(value.poc_id)) ||
      typeof value.acknowledgement_required !== "boolean"
    ) {
      throw new Error("Incomplete review response");
    }
    return value;
  }

  function isExpired(value) {
    const expiresAt = new Date(value);
    return !Number.isNaN(expiresAt.getTime()) && expiresAt.getTime() <= Date.now();
  }

  async function loadReview() {
    showOnly(elements.loading);
    clearFormMessage();

    if (!token) {
      if (!localSyntheticRequested) {
        showError("invalid");
        return;
      }
      token = "local-synthetic-preview";
    }

    // The explicit local preview is a browser-only fixture. Do not probe a
    // production review endpoint for a token that can never exist; doing so
    // creates a misleading 404 in an otherwise healthy demo.
    if (localSyntheticRequested) {
      hydrate(MOCK_REVIEW, true);
      return;
    }

    let response;
    try {
      response = await fetch(endpoint(), {
        headers: { Accept: "application/json" },
        cache: "no-store",
        credentials: "omit",
      });
    } catch (_error) {
      if (localSyntheticRequested) {
        hydrate(MOCK_REVIEW, true);
      } else {
        showError("network");
      }
      return;
    }

    const payload = await responseJson(response);

    // A successful API response is authoritative. Mock data never replaces it.
    if (response.ok) {
      try {
        hydrate(normalize(payload), false);
      } catch (_error) {
        showError("unavailable");
      }
      return;
    }

    if (localSyntheticRequested) {
      hydrate(MOCK_REVIEW, true);
    } else if ([401, 403, 404].includes(response.status)) {
      showError("invalid");
    } else if (response.status === 410) {
      showError("expired");
    } else {
      showError("unavailable");
    }
  }

  function hydrate(record, isMock) {
    review = record;
    localSynthetic = isMock;
    const status = record.status.toUpperCase();

    if (status === "INVALID") {
      showError("invalid");
      return;
    }
    if (status === "EXPIRED" || isExpired(record.expires_at)) {
      showError("expired");
      return;
    }
    if (status === "CONFIRMED" || status === "CHANGES_REQUESTED") {
      showTerminal(
        record.decision || {
          decision: status === "CONFIRMED" ? "CONFIRM" : "REQUEST_CHANGES",
          reviewer_display_name: record.identity.display_name,
        }
      );
      return;
    }

    criterionIndex = 0;
    const agreement = record.agreement;
    const targetSystem = displayTarget(record, agreement);
    elements.mockBanner.hidden = !localSynthetic;
    elements.pocTitle.textContent = agreement.use_case;
    elements.customerName.textContent = agreement.customer;
    elements.contractId.textContent = agreement.id;
    elements.contractVersion.textContent = agreement.version;
    elements.evidenceMethod.textContent = evidenceMethodLabel(
      record.evidence_method
    );
    elements.targetModel.textContent = targetSystem.model;
    elements.targetRuntime.textContent =
      [
        targetSystem.provider,
        targetSystem.endpoint_class,
        targetSystem.endpoint,
      ].filter(Boolean).join(" · ");
    elements.workloadFixture.textContent = agreement.workload.fixture_path;
    elements.workloadSha256.textContent = agreement.workload.sha256;
    elements.agreementOwners.textContent = agreement.owners.join(" · ");
    elements.evidenceRetention.textContent =
      agreement.evidence_retention_policy;
    elements.agreementManifest.textContent = JSON.stringify(agreement, null, 2);
    elements.identity.textContent = record.identity.display_name;
    elements.identityNotice.textContent = record.identity.notice;
    elements.expiry.textContent = formatDate(record.expires_at, "Not provided");
    elements.ackGroup.hidden = !record.acknowledgement_required;
    elements.ack.checked = false;
    elements.ackLabel.textContent =
      "I confirm these requirements and test conditions.";
    renderCriteriaSummary();
    renderCriterion();
    showOnly(elements.review);
  }

  function renderCriteriaSummary() {
    const criteria = review.contract.criteria;
    elements.criteriaCount.textContent =
      `${criteria.length} ${criteria.length === 1 ? "requirement" : "requirements"}`;
    elements.criteriaList.replaceChildren();

    criteria.forEach((criterion, index) => {
      const item = document.createElement("li");
      item.className = "requirement-row";

      const identity = document.createElement("div");
      identity.className = "requirement-row__identity";
      const number = document.createElement("span");
      number.className = "requirement-number";
      number.setAttribute("aria-hidden", "true");
      number.textContent = String(index + 1).padStart(2, "0");
      const title = document.createElement("h3");
      title.textContent = criterion.title;
      const importance = document.createElement("span");
      importance.className = "requirement-kind";
      importance.textContent =
        criterion.required === false ? "Supporting" : "Required";
      identity.append(number, title, importance);

      const target = document.createElement("div");
      target.className = "requirement-row__target";
      const targetLabel = document.createElement("span");
      targetLabel.textContent = "Target";
      const targetValue = document.createElement("strong");
      targetValue.textContent = criterion.threshold;
      target.append(targetLabel, targetValue);

      const method = document.createElement("p");
      method.className = "requirement-row__method";
      method.textContent =
        `${criterion.metric} · ${criterion.sample} · ${criterion.workload}`;

      item.append(identity, target, method);
      elements.criteriaList.append(item);
    });
  }

  function renderCriterion() {
    const criteria = review.contract.criteria;
    const criterion = criteria[criterionIndex];
    const boundCriterion = review.agreement.criteria[criterionIndex];
    const position = criterionIndex + 1;

    elements.position.textContent = `Requirement ${position} of ${criteria.length}`;
    elements.criterionTitle.textContent = criterion.title;
    elements.importance.textContent =
      criterion.required === false ? "Supporting requirement" : "Required for the POC";
    elements.summary.textContent = criterion.plain_language;
    elements.quote.textContent = `“${criterion.source_quote}”`;
    elements.metric.textContent = criterion.metric;
    elements.threshold.textContent = criterion.threshold;
    elements.sample.textContent = criterion.sample;
    elements.workload.textContent = criterion.workload;
    elements.rule.textContent = criterionRuleText(boundCriterion);
    const managedIdentity =
      boundCriterion.criterion_type === "inference_performance_v3"
        ? boundCriterion.evidence_identity
        : null;
    elements.adapter.textContent = managedIdentity
      ? `${managedIdentity.adapter_id}@${managedIdentity.adapter_version} · native first-choices-event · ${managedIdentity.chronology.toLowerCase()}`
      : `${boundCriterion.adapter}@${boundCriterion.adapter_version} · ` +
        boundCriterion.workload_slice;
    elements.criterionOwner.textContent = boundCriterion.owner;
    elements.sourceLocation.textContent = boundCriterion.source
      ? `${boundCriterion.source.speaker} · ${boundCriterion.source.location}`
      : "Explicitly human-added requirement";
    elements.evidencePolicy.textContent = boundCriterion.evidence_policy;
    renderCountingPolicy(boundCriterion);
    elements.previous.disabled = criterionIndex === 0;
    elements.next.disabled = criterionIndex === criteria.length - 1;
    elements.progress.setAttribute("aria-valuemax", String(criteria.length));
    elements.progress.setAttribute("aria-valuenow", String(position));
    elements.progress.setAttribute(
      "aria-valuetext",
      `Requirement ${position} of ${criteria.length}`
    );
    elements.progressBar.style.width = `${(position / criteria.length) * 100}%`;

    const exclusions = [
      ...(review.agreement.non_goals || []),
      ...review.contract.criteria.flatMap((item) => item.excluded || []),
    ];
    elements.exclusions.replaceChildren();
    [...new Set(exclusions.length ? exclusions : ["Anything not stated here"])].forEach(
      (exclusion) => {
        const item = document.createElement("li");
        item.textContent = exclusion;
        elements.exclusions.append(item);
      }
    );
  }

  function renderCountingPolicy(boundCriterion) {
    if (boundCriterion?.criterion_type === "inference_performance_v3") {
      const ttft = boundCriterion.ttft_p95;
      const errorRate = boundCriterion.error_rate;
      const identity = boundCriterion.evidence_identity;
      elements.countingPolicy.hidden = false;
      elements.countingPopulation.textContent =
        `${errorRate.exact_attempts} measured requests`;
      elements.countingLatency.textContent =
        "TTFT p95: first choices event (role-only or empty content counts) · nearest rank";
      elements.countingReliability.textContent =
        `Error rate: all ${errorRate.exact_attempts} requests; failed or anomalous requests count`;
      elements.countingBoundary.textContent =
        `${identity.chronology} evidence · warmups excluded · retries = NOT_AVAILABLE · invalid bundle = INGESTION REJECTED (no verdict) · insufficient evidence = NOT PROVEN`;
      return;
    }
    const policy = boundCriterion?.measurement_policy;
    elements.countingPolicy.hidden = !policy;
    if (!policy) {
      return;
    }
    const attempts = policy.measured_population.exact_attempts;
    elements.countingPopulation.textContent = `${attempts} measured attempts`;
    elements.countingLatency.textContent =
      "TTFT p95: successful requests only; failures still count toward reliability";
    elements.countingReliability.textContent =
      `Error rate: all ${attempts} attempts; HTTP, timeout, protocol, and transport errors count`;
    elements.countingBoundary.textContent =
      "Warmups and preflight excluded · no retries · invalid evidence is NOT PROVEN";
  }

  function criterionRuleText(boundCriterion) {
    if (boundCriterion?.criterion_type === "inference_performance_v3") {
      const ttft = boundCriterion.ttft_p95;
      const errorRate = boundCriterion.error_rate;
      const thresholdMs = ttft.threshold_ns / 1000000;
      const errorPercent = errorRate.threshold_basis_points / 100;
      return (
        `Native p95 TTFT < ${thresholdMs} ms · ` +
        `${ttft.minimum_successful_samples} successful records · ` +
        `error rate < ${errorPercent}% across ${errorRate.exact_attempts} records · ` +
        `${ttft.reducer_id}`
      );
    }
    if (
      ["inference_performance_v1", "inference_performance_v2"].includes(
        boundCriterion?.criterion_type
      )
    ) {
      const ttft = boundCriterion.ttft_p95 || {};
      const errorRate = boundCriterion.error_rate || {};
      const ttftOperator = ttft.operator === "lte" ? "≤" : "<";
      const errorPercent =
        typeof errorRate.threshold === "number"
          ? `${errorRate.threshold * 100}%`
          : "unavailable";
      return (
        `P95 TTFT ${ttftOperator} ${ttft.threshold ?? "unavailable"} ms · ` +
        `${ttft.minimum_successful_samples ?? "unavailable"} successful samples · ` +
        `error rate < ${errorPercent} · ` +
        `${errorRate.minimum_attempts ?? "unavailable"} attempts`
      );
    }
    const rule = boundCriterion?.rule || {};
    const confidence =
      typeof rule.confidence_level === "number"
        ? `${Math.round(rule.confidence_level * 100)}% `
        : "";
    return (
      `${rule.operator ?? "Rule"} ${rule.threshold ?? "unavailable"} · ` +
      `${rule.minimum_samples ?? "unavailable"} samples · ` +
      `${confidence}${rule.confidence_method ?? "method unavailable"} · ` +
      `${boundCriterion?.aggregation ?? "aggregation unavailable"} ` +
      `(${boundCriterion?.unit ?? "unit unavailable"})`
    );
  }

  function moveCriterion(direction) {
    const lastIndex = review.contract.criteria.length - 1;
    const nextIndex = Math.max(0, Math.min(lastIndex, criterionIndex + direction));
    if (nextIndex !== criterionIndex) {
      criterionIndex = nextIndex;
      renderCriterion();
      elements.criterionTitle.focus({ preventScroll: true });
    }
  }

  function formatDate(value, fallback = "—") {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) {
      return fallback;
    }
    return new Intl.DateTimeFormat(undefined, {
      year: "numeric",
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
      timeZoneName: "short",
    }).format(date);
  }

  function formMessage(message, status = false) {
    elements.formMessage.hidden = false;
    elements.formMessage.textContent = message;
    elements.formMessage.classList.toggle("is-status", status);
  }

  function clearFormMessage() {
    elements.formMessage.hidden = true;
    elements.formMessage.textContent = "";
    elements.formMessage.classList.remove("is-status");
  }

  function setSubmitting(submitting, decision = "") {
    [elements.confirm, elements.requestChanges, elements.ack, elements.rationale].forEach(
      (control) => {
        control.disabled = submitting;
      }
    );
    elements.confirm.textContent =
      submitting && decision === "CONFIRM"
        ? "Recording confirmation…"
        : "Confirm POC agreement";
    elements.requestChanges.textContent =
      submitting && decision === "REQUEST_CHANGES"
        ? "Recording request…"
        : "Request changes";
  }

  function idempotencyKey(decision) {
    const storageKey = `exitspec:review:${review.review_id}:${decision}`;
    if (requestKeys.has(storageKey)) {
      return requestKeys.get(storageKey);
    }
    try {
      const stored = sessionStorage.getItem(storageKey);
      if (stored) {
        requestKeys.set(storageKey, stored);
        return stored;
      }
    } catch (_error) {
      // Storage can be unavailable in privacy modes; the in-memory key still works.
    }
    const random =
      window.crypto && typeof window.crypto.randomUUID === "function"
        ? window.crypto.randomUUID()
        : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
    const key = `review-${decision.toLowerCase()}-${random}`;
    requestKeys.set(storageKey, key);
    try {
      sessionStorage.setItem(storageKey, key);
    } catch (_error) {
      // Nothing else is required; requestKeys preserves the retry key for this page.
    }
    return key;
  }

  async function submitDecision(decision) {
    clearFormMessage();
    const rationale = elements.rationale.value.trim();

    if (isExpired(review.expires_at)) {
      showError("expired");
      return;
    }
    if (decision === "REQUEST_CHANGES" && !rationale) {
      elements.changes.hidden = false;
      elements.changes.open = true;
      formMessage("Add a short note describing what should change.");
      requestAnimationFrame(() => elements.rationale.focus());
      return;
    }
    if (
      decision === "CONFIRM" &&
      review.acknowledgement_required &&
      !elements.ack.checked
    ) {
      formMessage("Check the confirmation box before confirming this agreement.");
      elements.ack.focus();
      return;
    }

    setSubmitting(true, decision);
    formMessage(
      decision === "CONFIRM"
        ? "Recording your confirmation against this exact version…"
        : "Recording your requested changes against this exact version…",
      true
    );

    if (localSynthetic) {
      setTimeout(() => {
        setSubmitting(false);
        showTerminal({
          decision,
          reviewer_display_name: "Local synthetic reviewer",
          recorded_at: new Date().toISOString(),
          synthetic: true,
        });
      }, 250);
      return;
    }

    const body = {
      review_id: review.review_id,
      contract_id: review.contract.id,
      contract_version: review.contract.version,
      decision,
      agreement_acknowledged: Boolean(elements.ack.checked),
      rationale: decision === "REQUEST_CHANGES" ? rationale : null,
    };

    try {
      const response = await fetch(`${endpoint()}/decision`, {
        method: "POST",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
          "Idempotency-Key": idempotencyKey(decision),
        },
        cache: "no-store",
        credentials: "omit",
        body: JSON.stringify(body),
      });
      const payload = await responseJson(response);

      if (response.ok) {
        showTerminal(
          payload?.decision
            ? {
                ...payload.decision,
                idempotent_replay:
                  payload.idempotent_replay ??
                  payload.decision.idempotent_replay ??
                  false,
              }
            : {
                decision,
                reviewer_display_name: review.identity.display_name,
                idempotent_replay: Boolean(payload?.idempotent_replay),
              }
        );
      } else if (response.status === 409 && payload?.decision) {
        showTerminal({ ...payload.decision, idempotent_replay: true });
      } else if ([401, 403, 404].includes(response.status)) {
        showError("invalid");
      } else if (response.status === 410) {
        showError("expired");
      } else {
        setSubmitting(false);
        formMessage(
          "ExitSpec could not record this decision. Nothing changed; try again."
        );
      }
    } catch (_error) {
      setSubmitting(false);
      formMessage(
        "The connection was interrupted. Nothing changed. Try again—the same request key will be reused."
      );
    }
  }

  function safeLocalReturnPath(value) {
    if (value === "/app/pocs/poc_support_agent_demo") {
      return value;
    }
    if (typeof value !== "string") {
      return null;
    }
    const match = value.match(
      /^\/app\/pocs\/(poc_[a-z0-9][a-z0-9_-]{2,63})\/agreement$/
    );
    if (
      !match ||
      (review?.poc_id !== undefined && match[1] !== review.poc_id)
    ) {
      return null;
    }
    try {
      const parsed = new URL(value, window.location.origin);
      return parsed.origin === window.location.origin &&
        parsed.pathname === value &&
        parsed.search === "" &&
        parsed.hash === ""
        ? value
        : null;
    } catch (_error) {
      return null;
    }
  }

  function showTerminal(decision) {
    const changesRequested = decision.decision === "REQUEST_CHANGES";
    const replay = Boolean(decision.idempotent_replay);
    const synthetic = Boolean(decision.synthetic);
    const localReturn = review?.local_demo;
    const safeLocalReturnUrl = safeLocalReturnPath(localReturn?.return_url);
    const dynamicPOCAgreement = Boolean(
      safeLocalReturnUrl?.endsWith("/agreement")
    );

    elements.terminalMark.textContent = changesRequested ? "↺" : "✓";
    elements.terminalMark.className =
      `state-mark ${changesRequested ? "state-mark--changes" : "state-mark--success"}`;
    elements.terminalBoundary.classList.toggle(
      "terminal-boundary--changes",
      changesRequested
    );
    elements.terminalEyebrow.textContent = replay
      ? "Already recorded"
      : synthetic
        ? "Local synthetic preview"
        : "Decision recorded";
    elements.terminalTitle.textContent = changesRequested
      ? "Changes requested"
      : "POC agreement confirmed";
    elements.terminalMessage.textContent = synthetic
      ? "This was a local synthetic interaction. No customer decision was submitted."
      : replay
        ? "ExitSpec found the same completed decision. The original record is unchanged."
        : changesRequested
          ? "This version stays immutable. The POC owner can preserve it and issue a revised version for review."
          : "Recorded against the exact contract version below.";
    elements.terminalContract.textContent =
      `${review.contract.id} · Version ${review.contract.version}`;
    elements.terminalDecision.textContent = changesRequested
      ? "Changes requested"
      : "POC agreement confirmed";
    elements.terminalRecordedAt.textContent = formatDate(
      decision.recorded_at,
      "Recorded by ExitSpec"
    );
    elements.terminalReviewer.textContent =
      decision.reviewer_display_name || review.identity.display_name;
    if (synthetic) {
      elements.terminalNextTitle.textContent = changesRequested
        ? "Preview only: a real request would return for revision."
        : "Preview only: a real confirmation would unlock contract freeze.";
      elements.terminalNextDetail.textContent =
        "No agreement, evidence, or lifecycle state changed.";
    } else if (changesRequested) {
      elements.terminalNextTitle.textContent =
        "Next: revise the test plan and issue a new version.";
      elements.terminalNextDetail.textContent =
        "This agreement version stays immutable and cannot be frozen.";
    } else {
      elements.terminalNextTitle.textContent =
        "Next: freeze the confirmed contract.";
      elements.terminalNextDetail.textContent =
        "The POC owner freezes this exact version before any proof run.";
    }
    const canReturnToLocalApp = Boolean(safeLocalReturnUrl);
    elements.localDemoReturn.hidden = !canReturnToLocalApp;
    if (canReturnToLocalApp) {
      elements.returnToApp.href = safeLocalReturnUrl;
      elements.returnToApp.textContent = dynamicPOCAgreement
        ? "Return to the POC agreement"
        : "Return to the local POC owner";
      elements.localDemoNotice.textContent =
        localReturn.notice ||
        "Local demo only. Hosted customer reviews do not expose an internal workspace shortcut.";
    }
    showOnly(elements.terminal);
    focusHeading(elements.terminal);
  }

  elements.previous.addEventListener("click", () => moveCriterion(-1));
  elements.next.addEventListener("click", () => moveCriterion(1));
  elements.retry.addEventListener("click", loadReview);
  elements.form.addEventListener("submit", (event) => {
    event.preventDefault();
    if (["CONFIRM", "REQUEST_CHANGES"].includes(event.submitter?.value)) {
      submitDecision(event.submitter.value);
    }
  });

  const MOCK_AGREEMENT = {
    id: "support-agent-poc",
    version: "3",
    customer: "Northstar Support (synthetic)",
    use_case: "Support-agent tool selection POC",
    target_system: {
      provider: "deterministic-local",
      endpoint_class: "mock",
      model: "deterministic-tool-selector-v1",
    },
    workload: {
      fixture_path: "synthetic/support-tool-selection-200.json",
      sha256: "a".repeat(64),
    },
    criteria: [
      {
        id: "TOOL-SELECT-01",
        title: "Correct tool selection",
        must_have: true,
        source: {
          speaker: "customer_vp_engineering",
          quote:
            "Our support agent must select the correct tool at least 95% of the time.",
          location: "synthetic-discovery:12",
        },
        human_added: false,
        normalized_claim:
          "The support agent must select the expected tool for at least 95% of the fixed, labeled support requests.",
        metric: "exact_tool_selection_rate",
        unit: "proportion",
        aggregation: "exact-match proportion",
        rule: {
          operator: "gte",
          threshold: 0.95,
          minimum_samples: 200,
          confidence_level: 0.95,
          confidence_method: "wilson_two_sided_lower_bound",
        },
        workload_slice: "synthetic-support-requests",
        adapter: "deterministic_tool_selection",
        adapter_version: "1.0.0",
        owner: "vendor_solutions_engineer",
        evidence_policy:
          "Persist synthetic case IDs, expected and selected tools, calculation inputs, and hashes.",
        approved: true,
      },
      {
        id: "FAILURE-REVIEW-01",
        title: "Inspectable mistakes",
        must_have: true,
        source: {
          speaker: "customer_vp_engineering",
          quote: "We want to inspect any mistakes before we scale traffic.",
          location: "synthetic-discovery:14",
        },
        human_added: false,
        normalized_claim:
          "Every incorrect selection must retain a case identifier, expected tool, selected tool, and safe error detail for human review.",
        metric: "exact_tool_selection_rate",
        unit: "proportion",
        aggregation: "reviewable failure proportion",
        rule: {
          operator: "gte",
          threshold: 1,
          minimum_samples: 1,
          confidence_level: 0.95,
          confidence_method: "wilson_two_sided_lower_bound",
        },
        workload_slice: "synthetic-support-errors",
        adapter: "deterministic_tool_selection",
        adapter_version: "1.0.0",
        owner: "vendor_solutions_engineer",
        evidence_policy:
          "Persist safe failure details for every incorrect selection.",
        approved: true,
      },
    ],
    owners: ["customer_vp_engineering", "vendor_solutions_engineer"],
    non_goals: [
      "Production rollout or traffic expansion",
      "Security, legal, and procurement approval",
      "Performance outside the fixed support workload",
    ],
    evidence_retention_policy:
      "Synthetic fixture only; no secrets or raw PII; local artifacts retained for demo review.",
  };

  const MOCK_REVIEW = {
    review_id: "local-synthetic-review",
    status: "PENDING",
    evidence_method: "EXIT_SPEC_DETERMINISTIC_TOOL_SELECTION",
    poc: {
      title: "Support-agent tool selection POC",
      customer_name: "Northstar Support (synthetic)",
    },
    contract: {
      id: "support-agent-poc",
      version: "3",
      evidence_method: "EXIT_SPEC_DETERMINISTIC_TOOL_SELECTION",
      excluded: [
        "Production rollout or traffic expansion",
        "Security, legal, and procurement approval",
        "Performance outside the fixed support workload",
      ],
      criteria: [
        {
          id: "TOOL-SELECT-01",
          title: "Correct tool selection",
          plain_language:
            "The support agent must select the expected tool for at least 95% of the fixed, labeled support requests.",
          source_quote:
            "Our support agent must select the correct tool at least 95% of the time.",
          metric: "Exact tool-selection rate",
          threshold: "At least 95%",
          sample: "200 fixed, labeled cases",
          workload: "Synthetic support requests",
          required: true,
          excluded: ["Answer quality after the tool is selected"],
        },
        {
          id: "FAILURE-REVIEW-01",
          title: "Inspectable mistakes",
          plain_language:
            "Every incorrect selection must retain a case identifier, expected tool, selected tool, and safe error detail for human review.",
          source_quote: "We want to inspect any mistakes before we scale traffic.",
          metric: "Reviewable failure records",
          threshold: "100% of incorrect cases",
          sample: "Every error in the same run",
          workload: "Synthetic support requests",
          required: true,
          excluded: ["Automatic approval to scale traffic"],
        },
      ],
    },
    agreement: MOCK_AGREEMENT,
    expires_at: new Date(Date.now() + 72 * 60 * 60 * 1000).toISOString(),
    acknowledgement_required: true,
    identity: {
      display_name: "Local synthetic reviewer",
      notice:
        "Synthetic preview only. A production link must bind a verified identity to this exact contract version.",
    },
    local_demo: {
      return_url: "/app",
      notice:
        "Local synthetic preview only. A hosted customer review would not expose an internal workspace shortcut.",
    },
  };

  loadReview();
})();
