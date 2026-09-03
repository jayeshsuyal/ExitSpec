(() => {
  "use strict";

  const POC_ID_PATTERN = /^poc_[a-z0-9][a-z0-9_-]{2,63}$/;
  const OPERATION_ID_PATTERN = /^prun_[a-f0-9]{32}$/;
  const IMPORT_OPERATION_ID_PATTERN = /^pimp_[a-f0-9]{32}$/;
  const INFERDROME_RUN_ID_PATTERN = /^run-[a-f0-9]{32}$/;
  const RECEIPT_ID_PATTERN = /^irc_[a-f0-9]{64}$/;
  const MANAGED_RECEIPT_ID_PATTERN = /^irc2_[a-f0-9]{64}$/;
  const TAGGED_SHA256_PATTERN = /^sha256:[a-f0-9]{64}$/;
  const SHA256_PATTERN = /^[a-f0-9]{64}$/;
  const LOCAL_EVIDENCE_METHOD = "EXIT_SPEC_STREAMING_PROBE";
  const EXTERNAL_EVIDENCE_METHOD = "INFERDROME_EXTERNAL_BUNDLE";
  const MANAGED_CRITERION_TYPE = "inference_performance_v3";
  const ROUTE_PATTERN =
    /^\/app\/pocs\/(poc_[a-z0-9][a-z0-9_-]{2,63})$/;
  const LOCAL_EVIDENCE_PACK_PATTERN =
    /^\/artifacts\/run_[a-f0-9]{32}\/decision-packet\.html$/;
  const IMPORT_EVIDENCE_PACK_PATTERN =
    /^\/artifacts\/pimp_[a-f0-9]{32}\/decision-packet\.html$/;
  const RUN_STATUSES = new Set([
    "NOT_STARTED",
    "RUNNING",
    "COMPLETED",
    "BLOCKED",
    "NOT_PROVEN",
  ]);
  const TERMINAL_STATUSES = new Set([
    "COMPLETED",
    "BLOCKED",
    "NOT_PROVEN",
  ]);
  const IMPORT_STATUSES = new Set([
    "NOT_STARTED",
    "IMPORTING",
    "COMPLETED",
    "INGESTION_REJECTED",
    "FAILED_CLOSED",
  ]);
  const IMPORT_TERMINAL_STATUSES = new Set([
    "COMPLETED",
    "INGESTION_REJECTED",
    "FAILED_CLOSED",
  ]);
  const VERDICTS = new Set(["PASS", "FAIL", "NOT_PROVEN"]);
  const METRICS = new Set(["TTFT_P95_MS", "ERROR_RATE_PERCENT"]);
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
  const MANAGED_COUNTING_POLICY_KEYS = Object.freeze([
    "chronology",
    "compatible_insufficient_evidence_disposition",
    "concurrency_semantics",
    "error_threshold_basis_points",
    "exact_attempts",
    "ingestion_failure_disposition",
    "latency_population",
    "metric_definition_id",
    "minimum_successful_samples",
    "policy_sha256",
    "producer_contract_link",
    "reducer_id",
    "reliability_denominator",
    "reliability_numerator",
    "required_configured_max_concurrency",
    "retry_behavior",
    "schema_version",
    "warmup_requests",
    "warmups_included",
  ]);
  const OUTCOME_COUNT_KEYS = Object.freeze([
    "cancelled",
    "http_error",
    "internal_error",
    "protocol_error",
    "success",
    "timeout",
    "transport_error",
  ]);
  const POLL_DELAYS = Object.freeze([500, 900, 1500, 2500, 4000]);
  const MAX_POLLS = 90;
  const REASON_COPY = Object.freeze({
    ENDPOINT_PREFLIGHT_FAILED:
      "The frozen endpoint did not pass the bounded preflight. No performance conclusion was made.",
    RUNNER_INTERNAL_FAILURE:
      "The proof failed closed before verified evidence could be released.",
    RUN_NOT_PROVEN:
      "The run produced compatible but insufficient evidence, so the result is NOT PROVEN.",
    WORKER_LAUNCH_FAILED:
      "The local proof worker did not start. No requests were inferred.",
  });

  const routeMatch =
    window.location.search === "" && window.location.hash === ""
      ? window.location.pathname.match(ROUTE_PATTERN)
      : null;
  const pocId =
    routeMatch && POC_ID_PATTERN.test(routeMatch[1]) ? routeMatch[1] : null;
  const pocApi = pocId ? `/api/pocs/${pocId}` : null;
  const agreementApi = pocApi ? `${pocApi}/agreement` : null;
  const runsApi = pocApi ? `${pocApi}/runs` : null;
  const latestRunApi = runsApi ? `${runsApi}/latest` : null;
  const inferdromeApi = pocApi ? `${pocApi}/inferdrome` : null;
  const inferdromeCatalogApi = inferdromeApi
    ? `${inferdromeApi}/runs`
    : null;
  const importsApi = inferdromeApi ? `${inferdromeApi}/imports` : null;
  const latestImportApi = importsApi ? `${importsApi}/latest` : null;

  const main = document.querySelector("#performance-main");
  const runButton = document.querySelector("#run-proof");
  const acknowledgement = document.querySelector("#execution-acknowledged");
  const acknowledgementLabel = document.querySelector(
    ".run-acknowledgement"
  );
  const errorPanel = document.querySelector("#performance-error");
  const inferdromeSelection = document.querySelector(
    "#inferdrome-selection"
  );
  const inferdromeBundle = document.querySelector("#inferdrome-bundle");
  const inferdromeCatalogStatus = document.querySelector(
    "#inferdrome-catalog-status"
  );
  const importReceipt = document.querySelector("#import-receipt");
  let draft = null;
  let agreement = null;
  let run = null;
  let catalog = null;
  let selectedBundle = null;
  let actionPending = false;
  let pollCount = 0;
  let pollTimer = null;
  let pocLifecycleClosed = document.body.dataset.pocLifecycle === "closed";

  class TrustedRequestError extends Error {
    constructor(status) {
      super("Trusted proof request failed.");
      this.name = "TrustedRequestError";
      this.status = status;
    }
  }

  function hasExactKeys(value, expectedKeys) {
    if (!value || typeof value !== "object" || Array.isArray(value)) {
      return false;
    }
    const actual = Object.keys(value).sort();
    const expected = [...expectedKeys].sort();
    return (
      actual.length === expected.length &&
      actual.every((key, index) => key === expected[index])
    );
  }

  function isExternalEvidence() {
    return (
      agreement?.draft?.evidence_method === EXTERNAL_EVIDENCE_METHOD
    );
  }

  function isManagedEvidence() {
    return Boolean(
      isExternalEvidence() &&
        agreement?.counting_policy?.schema_version ===
          "exitspec.inferdrome-managed-counting.v1" &&
        agreement.counting_policy.metric_definition_id ===
          "vllm_first_choices_event_v0_26" &&
        MANAGED_CRITERION_TYPE === "inference_performance_v3" &&
        INFERDROME_RUN_ID_PATTERN.test(
          agreement.draft.inferdrome_run_id
        ) &&
        TAGGED_SHA256_PATTERN.test(
          agreement.draft.inferdrome_bundle_digest
        )
    );
  }

  function frozenManagedSelection() {
    if (!isManagedEvidence()) {
      return null;
    }
    const frozen = agreement.frozen_contract;
    return frozen.inferdrome_run_id === agreement.draft.inferdrome_run_id &&
      frozen.inferdrome_bundle_digest ===
        agreement.draft.inferdrome_bundle_digest
      ? {
          run_id: agreement.draft.inferdrome_run_id,
          bundle_digest: agreement.draft.inferdrome_bundle_digest,
        }
      : null;
  }

  function isTerminalStatus(status) {
    return (
      isExternalEvidence()
        ? IMPORT_TERMINAL_STATUSES
        : TERMINAL_STATUSES
    ).has(status);
  }

  function isActiveStatus(status) {
    return status === (isExternalEvidence() ? "IMPORTING" : "RUNNING");
  }

  function safeText(value, maximum) {
    return Boolean(
      typeof value === "string" &&
        value.trim().length > 0 &&
        value.length <= maximum &&
        !/[\u0000-\u0008\u000b\u000c\u000e-\u001f]/.test(value)
    );
  }

  function exactTargetUrl(value) {
    if (typeof value !== "string" || value.length > 2048) {
      return false;
    }
    try {
      const parsed = new URL(value);
      return Boolean(
        ["http:", "https:"].includes(parsed.protocol) &&
          parsed.hostname &&
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

  function safeEvidenceUrl(value) {
    if (
      typeof value !== "string" ||
      !LOCAL_EVIDENCE_PACK_PATTERN.test(value) &&
      !IMPORT_EVIDENCE_PACK_PATTERN.test(value)
    ) {
      return null;
    }
    try {
      const parsed = new URL(value, window.location.origin);
      return parsed.origin === window.location.origin &&
        parsed.pathname === value &&
        !parsed.search &&
        !parsed.hash
        ? parsed.pathname
        : null;
    } catch {
      return null;
    }
  }

  function trustedDraft(value) {
    return Boolean(
      value &&
        value.poc_id === pocId &&
        value.archive_state === "ACTIVE" &&
        safeText(value.display_name, 160) &&
        safeText(value.customer_label, 160) &&
        safeText(value.use_case, 500) &&
        safeText(value.owner, 160)
    );
  }

  function trustedDefinition(value) {
    if (
      !value ||
      typeof value !== "object" ||
      !METRICS.has(value.metric) ||
      !["LT", "LTE"].includes(value.operator) ||
      typeof value.threshold !== "number" ||
      !Number.isFinite(value.threshold) ||
      !Number.isInteger(value.minimum_samples) ||
      !Number.isInteger(value.concurrency) ||
      value.minimum_samples < 1 ||
      value.minimum_samples > 1000 ||
      value.concurrency < 1 ||
      value.concurrency > 32 ||
      value.concurrency > value.minimum_samples ||
      !safeText(value.source_quote, 4000)
    ) {
      return false;
    }
    if (value.metric === "ERROR_RATE_PERCENT") {
      return (
        value.operator === "LT" &&
        value.threshold > 0 &&
        value.threshold < 100
      );
    }
    return value.threshold > 0 && value.threshold <= 60000;
  }

  function trustedCountingPolicy(value) {
    const legacy = Boolean(
      hasExactKeys(value, COUNTING_POLICY_KEYS) &&
        value.schema_version === "exitspec.measurement-population.v1" &&
        SHA256_PATTERN.test(value.policy_sha256) &&
        Number.isInteger(value.exact_attempts) &&
        value.exact_attempts >= 1 &&
        value.exact_attempts <= 1000 &&
        value.warmups_included === false &&
        value.preflight_included === false &&
        value.retries === 0 &&
        value.latency_population ===
          "successful_measured_attempts_with_valid_ttft" &&
        value.latency_failed_attempts ===
          "excluded_from_latency_counted_in_reliability" &&
        value.reliability_denominator === "all_measured_attempts" &&
        Array.isArray(value.external_error_outcomes) &&
        value.external_error_outcomes.join("|") ===
          "HTTP_ERROR|TIMEOUT|PROTOCOL_ERROR|TRANSPORT_ERROR" &&
        value.invalid_evidence_disposition === "NOT_PROVEN"
    );
    if (legacy) {
      return true;
    }
    return Boolean(
      hasExactKeys(value, MANAGED_COUNTING_POLICY_KEYS) &&
        value.schema_version === "exitspec.inferdrome-managed-counting.v1" &&
        SHA256_PATTERN.test(value.policy_sha256) &&
        value.metric_definition_id === "vllm_first_choices_event_v0_26" &&
        value.reducer_id === "nearest_rank_v1" &&
        value.latency_population ===
          "successful_measured_requests_with_observed_ttft" &&
        value.minimum_successful_samples === 100 &&
        value.exact_attempts === 100 &&
        value.reliability_numerator ===
          "failed_or_anomalous_native_measured_requests" &&
        value.reliability_denominator === "all_measured_requests" &&
        Number.isInteger(value.error_threshold_basis_points) &&
        value.error_threshold_basis_points > 0 &&
        value.error_threshold_basis_points < 10000 &&
        Number.isInteger(value.required_configured_max_concurrency) &&
        value.required_configured_max_concurrency > 0 &&
        value.required_configured_max_concurrency <= 1000 &&
        value.concurrency_semantics ===
          "configured_maximum_concurrency_not_observed_overlap" &&
        Number.isInteger(value.warmup_requests) &&
        value.warmup_requests >= 0 &&
        value.warmup_requests <= 1000 &&
        value.warmups_included === false &&
        value.retry_behavior === "NOT_AVAILABLE" &&
        value.chronology === "RETROSPECTIVE" &&
        value.producer_contract_link === "ABSENT" &&
        value.ingestion_failure_disposition === "INGESTION_REJECTED" &&
        value.compatible_insufficient_evidence_disposition === "NOT_PROVEN"
    );
  }

  function trustedAgreement(value) {
    if (
      !value ||
      value.poc_id !== pocId ||
      !Array.isArray(value.definitions) ||
      value.definitions.length !== 2 ||
      !value.definitions.every(trustedDefinition) ||
      !trustedCountingPolicy(value.counting_policy) ||
      !value.draft ||
      !value.confirmation ||
      !value.frozen_contract ||
      ![LOCAL_EVIDENCE_METHOD, EXTERNAL_EVIDENCE_METHOD].includes(
        value.draft.evidence_method
      ) ||
      value.frozen_contract.evidence_method !== value.draft.evidence_method
    ) {
      return false;
    }
    const metrics = new Set(
      value.definitions.map((definition) => definition.metric)
    );
    const managed =
      value.counting_policy.schema_version ===
      "exitspec.inferdrome-managed-counting.v1";
    const managedSelectionValid = managed
      ? INFERDROME_RUN_ID_PATTERN.test(value.draft.inferdrome_run_id) &&
        TAGGED_SHA256_PATTERN.test(value.draft.inferdrome_bundle_digest) &&
        value.frozen_contract.inferdrome_run_id ===
          value.draft.inferdrome_run_id &&
        value.frozen_contract.inferdrome_bundle_digest ===
          value.draft.inferdrome_bundle_digest
      : value.draft.inferdrome_run_id === undefined &&
        value.draft.inferdrome_bundle_digest === undefined &&
        value.frozen_contract.inferdrome_run_id === undefined &&
        value.frozen_contract.inferdrome_bundle_digest === undefined;
    return Boolean(
      metrics.size === 2 &&
        managedSelectionValid &&
        [...METRICS].every((metric) => metrics.has(metric)) &&
        SHA256_PATTERN.test(value.frozen_contract.canonical_hash) &&
        safeText(value.frozen_contract.contract_id, 512) &&
        safeText(value.frozen_contract.target_provider, 512) &&
        safeText(value.frozen_contract.endpoint_class, 512) &&
        exactTargetUrl(value.frozen_contract.endpoint) &&
        safeText(value.frozen_contract.model, 512) &&
        value.confirmation.agreement_acknowledged === true
    );
  }

  const RUN_KEYS = Object.freeze([
    "adapter",
    "adapter_version",
    "attempted_count",
    "authorized_request_count",
    "concurrency",
    "contract_hash",
    "contract_id",
    "contract_version",
    "endpoint",
    "endpoint_class",
    "error_count",
    "error_rate_percent",
    "evidence_pack_url",
    "is_terminal",
    "measured_requests",
    "model",
    "operation_id",
    "outcome_counts",
    "p95_ttft_ms",
    "poc_id",
    "reason_code",
    "status",
    "successful_count",
    "target_provider",
    "verdict",
    "warmup_requests",
    "workload_id",
  ]);

  function nullableCount(value) {
    return (
      value === null ||
      (Number.isInteger(value) && value >= 0 && value <= 1000)
    );
  }

  function nullableDecimal(value) {
    return (
      value === null ||
      (typeof value === "string" &&
        /^(?:0|[1-9]\d*)(?:\.\d+)?$/.test(value) &&
        Number.isFinite(Number(value)))
    );
  }

  function trustedOutcomeCounts(value) {
    return Boolean(
      value === null ||
        (hasExactKeys(value, OUTCOME_COUNT_KEYS) &&
          OUTCOME_COUNT_KEYS.every(
            (key) =>
              Number.isInteger(value[key]) &&
              value[key] >= 0 &&
              value[key] <= 1000
          ))
    );
  }

  function trustedRun(value) {
    if (
      !hasExactKeys(value, RUN_KEYS) ||
      value.poc_id !== pocId ||
      !SHA256_PATTERN.test(value.contract_hash) ||
      !safeText(value.contract_id, 512) ||
      !safeText(value.contract_version, 80) ||
      !safeText(value.workload_id, 512) ||
      !safeText(value.target_provider, 512) ||
      !safeText(value.endpoint_class, 512) ||
      !exactTargetUrl(value.endpoint) ||
      !safeText(value.model, 512) ||
      !safeText(value.adapter, 160) ||
      !safeText(value.adapter_version, 80) ||
      !Number.isInteger(value.measured_requests) ||
      value.measured_requests < 1 ||
      value.measured_requests > 1000 ||
      !Number.isInteger(value.concurrency) ||
      value.concurrency < 1 ||
      value.concurrency > 32 ||
      value.concurrency > value.measured_requests ||
      !Number.isInteger(value.warmup_requests) ||
      value.warmup_requests < 0 ||
      value.warmup_requests > 100 ||
      value.authorized_request_count !==
        1 + value.warmup_requests + value.measured_requests ||
      !RUN_STATUSES.has(value.status) ||
      !nullableCount(value.attempted_count) ||
      !nullableCount(value.successful_count) ||
      !nullableCount(value.error_count) ||
      !trustedOutcomeCounts(value.outcome_counts) ||
      !nullableDecimal(value.p95_ttft_ms) ||
      !nullableDecimal(value.error_rate_percent)
    ) {
      return false;
    }
    const operationValid =
      value.operation_id === null ||
      OPERATION_ID_PATTERN.test(value.operation_id);
    const evidenceUrl =
      value.evidence_pack_url === null
        ? null
        : safeEvidenceUrl(value.evidence_pack_url);
    if (!operationValid || (value.evidence_pack_url !== null && !evidenceUrl)) {
      return false;
    }
    if (value.status === "NOT_STARTED") {
      return (
        value.operation_id === null &&
        value.verdict === null &&
        value.attempted_count === null &&
        value.successful_count === null &&
        value.error_count === null &&
        value.outcome_counts === null &&
        value.p95_ttft_ms === null &&
        value.error_rate_percent === null &&
        value.evidence_pack_url === null &&
        value.is_terminal === false
      );
    }
    if (value.status === "RUNNING") {
      return (
        OPERATION_ID_PATTERN.test(value.operation_id) &&
        value.verdict === null &&
        value.attempted_count === null &&
        value.successful_count === null &&
        value.error_count === null &&
        value.outcome_counts === null &&
        value.p95_ttft_ms === null &&
        value.error_rate_percent === null &&
        value.evidence_pack_url === null &&
        value.is_terminal === false
      );
    }
    if (["BLOCKED", "NOT_PROVEN"].includes(value.status)) {
      return (
        OPERATION_ID_PATTERN.test(value.operation_id) &&
        value.verdict === null &&
        value.attempted_count === null &&
        value.successful_count === null &&
        value.error_count === null &&
        value.outcome_counts === null &&
        value.p95_ttft_ms === null &&
        value.error_rate_percent === null &&
        value.evidence_pack_url === null &&
        value.is_terminal === true
      );
    }
    const counts = value.outcome_counts;
    const countedAttempts =
      counts === null
        ? null
        : OUTCOME_COUNT_KEYS.reduce((total, key) => total + counts[key], 0);
    const externalErrors =
      counts === null
        ? null
        : counts.http_error +
          counts.timeout +
          counts.protocol_error +
          counts.transport_error;
    return Boolean(
      value.status === "COMPLETED" &&
        OPERATION_ID_PATTERN.test(value.operation_id) &&
        VERDICTS.has(value.verdict) &&
        Number.isInteger(value.attempted_count) &&
        Number.isInteger(value.successful_count) &&
        Number.isInteger(value.error_count) &&
        counts !== null &&
        value.attempted_count === value.measured_requests &&
        countedAttempts === value.attempted_count &&
        counts.success === value.successful_count &&
        externalErrors === value.error_count &&
        value.evidence_pack_url !== null &&
        value.is_terminal === true
    );
  }

  const IMPORT_KEYS = Object.freeze([
    "adapter",
    "adapter_version",
    "applicability_codes",
    "attempted_count",
    "bundle_digest",
    "completed_at",
    "concurrency",
    "contract_hash",
    "contract_id",
    "contract_version",
    "endpoint",
    "endpoint_class",
    "error_count",
    "error_rate_percent",
    "evidence_pack_url",
    "is_terminal",
    "measured_requests",
    "model",
    "operation_id",
    "p95_ttft_ms",
    "poc_id",
    "producer_run_id",
    "receipt_id",
    "rejection_code",
    "selected_run_id",
    "status",
    "successful_count",
    "target_provider",
    "verdict",
    "warmup_requests",
    "workload_id",
  ]);
  const MANAGED_IMPORT_EXTENSION_KEYS = Object.freeze([
    "anomalous_count",
    "observed_configured_max_concurrency",
    "required_configured_max_concurrency",
  ]);
  const MANAGED_IMPORT_KEYS = Object.freeze([
    ...IMPORT_KEYS,
    ...MANAGED_IMPORT_EXTENSION_KEYS,
  ]);

  function trustedCatalog(value) {
    return Boolean(
      hasExactKeys(value, ["configured", "rejected_count", "runs"]) &&
        typeof value.configured === "boolean" &&
        Number.isInteger(value.rejected_count) &&
        value.rejected_count >= 0 &&
        value.rejected_count <= 1000 &&
        Array.isArray(value.runs) &&
        value.runs.length <= 1000 &&
        value.runs.every(
          (entry) =>
            hasExactKeys(entry, ["bundle_digest", "run_id"]) &&
            INFERDROME_RUN_ID_PATTERN.test(entry.run_id) &&
            TAGGED_SHA256_PATTERN.test(entry.bundle_digest)
        ) &&
        (value.configured || value.runs.length === 0)
    );
  }

  function trustedCompletedAt(value) {
    return Boolean(
      typeof value === "string" &&
        value.length <= 64 &&
        Number.isFinite(Date.parse(value))
    );
  }

  function trustedImport(value) {
    const managed = isManagedEvidence();
    const managedExtension = {
      anomalous_count: value?.anomalous_count,
      observed_configured_max_concurrency:
        value?.observed_configured_max_concurrency,
      required_configured_max_concurrency:
        value?.required_configured_max_concurrency,
    };
    if (
      !(managed
        ? hasExactKeys(value, MANAGED_IMPORT_KEYS) &&
          hasExactKeys(managedExtension, MANAGED_IMPORT_EXTENSION_KEYS)
        : hasExactKeys(value, IMPORT_KEYS)) ||
      value.poc_id !== pocId ||
      !SHA256_PATTERN.test(value.contract_hash) ||
      !safeText(value.contract_id, 512) ||
      !safeText(value.contract_version, 80) ||
      !safeText(value.workload_id, 512) ||
      !safeText(value.target_provider, 512) ||
      !safeText(value.endpoint_class, 512) ||
      !exactTargetUrl(value.endpoint) ||
      !safeText(value.model, 512) ||
      !safeText(value.adapter, 160) ||
      !safeText(value.adapter_version, 80) ||
      !Number.isInteger(value.measured_requests) ||
      value.measured_requests < 1 ||
      value.measured_requests > 1000 ||
      !Number.isInteger(value.concurrency) ||
      value.concurrency < 1 ||
      value.concurrency > (managed ? 1000 : 32) ||
      value.concurrency > value.measured_requests ||
      !Number.isInteger(value.warmup_requests) ||
      value.warmup_requests < 0 ||
      value.warmup_requests > 100 ||
      !IMPORT_STATUSES.has(value.status) ||
      !nullableCount(value.attempted_count) ||
      !nullableCount(value.successful_count) ||
      !nullableCount(value.error_count) ||
      !nullableDecimal(value.p95_ttft_ms) ||
      !nullableDecimal(value.error_rate_percent) ||
      !Array.isArray(value.applicability_codes) ||
      value.applicability_codes.length > 32 ||
      !value.applicability_codes.every((code) => safeText(code, 160))
    ) {
      return false;
    }
    if (
      managed &&
      (!Number.isInteger(value.required_configured_max_concurrency) ||
        value.required_configured_max_concurrency !== value.concurrency ||
        !Number.isInteger(value.observed_configured_max_concurrency) ||
        value.observed_configured_max_concurrency < 1 ||
        value.observed_configured_max_concurrency > 1000 ||
        !nullableCount(value.anomalous_count))
    ) {
      return false;
    }
    const operationValid =
      value.operation_id === null ||
      IMPORT_OPERATION_ID_PATTERN.test(value.operation_id);
    const evidenceUrl =
      value.evidence_pack_url === null
        ? null
        : safeEvidenceUrl(value.evidence_pack_url);
    if (!operationValid || (value.evidence_pack_url !== null && !evidenceUrl)) {
      return false;
    }
    const noCalculatedResult =
      value.verdict === null &&
      value.attempted_count === null &&
      value.successful_count === null &&
      value.error_count === null &&
      value.p95_ttft_ms === null &&
      value.error_rate_percent === null &&
      value.producer_run_id === null &&
      value.receipt_id === null &&
      (!managed || value.anomalous_count === null) &&
      value.applicability_codes.length === 0 &&
      value.evidence_pack_url === null;
    if (value.status === "NOT_STARTED") {
      return Boolean(
        value.operation_id === null &&
          value.selected_run_id === null &&
          value.rejection_code === null &&
          value.bundle_digest === null &&
          value.completed_at === null &&
          noCalculatedResult &&
          value.is_terminal === false
      );
    }
    if (value.status === "IMPORTING") {
      return Boolean(
        IMPORT_OPERATION_ID_PATTERN.test(value.operation_id) &&
          INFERDROME_RUN_ID_PATTERN.test(value.selected_run_id) &&
          value.rejection_code === null &&
          TAGGED_SHA256_PATTERN.test(value.bundle_digest) &&
          value.completed_at === null &&
          noCalculatedResult &&
          value.is_terminal === false
      );
    }
    if (["INGESTION_REJECTED", "FAILED_CLOSED"].includes(value.status)) {
      return Boolean(
        IMPORT_OPERATION_ID_PATTERN.test(value.operation_id) &&
          INFERDROME_RUN_ID_PATTERN.test(value.selected_run_id) &&
          safeText(value.rejection_code, 160) &&
          TAGGED_SHA256_PATTERN.test(value.bundle_digest) &&
          trustedCompletedAt(value.completed_at) &&
          noCalculatedResult &&
          value.is_terminal === true
      );
    }
    return Boolean(
      value.status === "COMPLETED" &&
        IMPORT_OPERATION_ID_PATTERN.test(value.operation_id) &&
        value.rejection_code === null &&
        VERDICTS.has(value.verdict) &&
        Number.isInteger(value.attempted_count) &&
        Number.isInteger(value.successful_count) &&
        Number.isInteger(value.error_count) &&
        value.attempted_count === value.measured_requests &&
        value.successful_count + value.error_count === value.attempted_count &&
        value.selected_run_id === value.producer_run_id &&
        INFERDROME_RUN_ID_PATTERN.test(value.producer_run_id) &&
        TAGGED_SHA256_PATTERN.test(value.bundle_digest) &&
        (managed
          ? MANAGED_RECEIPT_ID_PATTERN.test(value.receipt_id)
          : RECEIPT_ID_PATTERN.test(value.receipt_id)) &&
        (managed
          ? Number.isInteger(value.anomalous_count) &&
            value.anomalous_count >= 0 &&
            value.anomalous_count <= value.error_count
          : true) &&
        value.evidence_pack_url !== null &&
        trustedCompletedAt(value.completed_at) &&
        value.is_terminal === true
    );
  }

  function crossBindingsValid() {
    const frozen = agreement.frozen_contract;
    const method = agreement.draft.evidence_method;
    const expectedAdapter =
      method === EXTERNAL_EVIDENCE_METHOD
        ? ["vllm_bench_serve", "1.0.0"]
        : ["vllm_streaming_latency", "1.0.0"];
    const ttft = agreement.definitions.find(
      (definition) => definition.metric === "TTFT_P95_MS"
    );
    const error = agreement.definitions.find(
      (definition) => definition.metric === "ERROR_RATE_PERCENT"
    );
    if (isManagedEvidence()) {
      const selection = frozenManagedSelection();
      const policy = agreement.counting_policy;
      return Boolean(
        selection &&
          run.contract_hash === frozen.canonical_hash &&
          run.contract_id === frozen.contract_id &&
          run.contract_version === frozen.contract_version &&
          frozen.evidence_method === method &&
          run.adapter === expectedAdapter[0] &&
          run.adapter_version === expectedAdapter[1] &&
          run.target_provider === frozen.target_provider &&
          run.endpoint_class === frozen.endpoint_class &&
          run.endpoint === frozen.endpoint &&
          run.model === frozen.model &&
          run.measured_requests === policy.exact_attempts &&
          run.concurrency === policy.required_configured_max_concurrency &&
          run.required_configured_max_concurrency === run.concurrency &&
          run.warmup_requests === policy.warmup_requests &&
          (run.selected_run_id === null ||
            run.selected_run_id === selection.run_id) &&
          (run.bundle_digest === null ||
            run.bundle_digest === selection.bundle_digest) &&
          ttft.concurrency === error.concurrency &&
          ttft.concurrency === policy.required_configured_max_concurrency
      );
    }
    return Boolean(
      run.contract_hash === frozen.canonical_hash &&
        run.contract_id === frozen.contract_id &&
        run.contract_version === frozen.contract_version &&
        frozen.evidence_method === method &&
        run.adapter === expectedAdapter[0] &&
        run.adapter_version === expectedAdapter[1] &&
        run.target_provider === frozen.target_provider &&
        run.endpoint_class === frozen.endpoint_class &&
        run.endpoint === frozen.endpoint &&
        run.model === frozen.model &&
        run.measured_requests === error.minimum_samples &&
        run.measured_requests === agreement.counting_policy.exact_attempts &&
        run.concurrency === ttft.concurrency &&
        ttft.concurrency === error.concurrency
    );
  }

  async function requestJson(url, options = {}) {
    const response = await fetch(url, {
      ...options,
      headers: {
        Accept: "application/json",
        ...(options.headers || {}),
      },
    });
    const payload = await response.json().catch(() => null);
    if (!response.ok || !payload || typeof payload !== "object") {
      throw new TrustedRequestError(response.status);
    }
    return payload;
  }

  function selectedCatalogEntry(runId) {
    return (
      catalog?.runs.find((entry) => entry.run_id === runId) || null
    );
  }

  function renderInferdromeSelection() {
    const external = isExternalEvidence();
    inferdromeSelection.hidden = !external || run.status === "COMPLETED";
    if (!external) {
      inferdromeBundle.disabled = true;
      selectedBundle = null;
      return;
    }
    if (isManagedEvidence()) {
      const selection = frozenManagedSelection();
      inferdromeBundle.replaceChildren();
      inferdromeSelection.querySelector("label").textContent =
        "Frozen evidence profile";
      if (!selection) {
        inferdromeBundle.append(new Option("Frozen selection is invalid", ""));
        inferdromeBundle.disabled = true;
        selectedBundle = null;
        inferdromeCatalogStatus.textContent =
          "The managed selection could not be cross-validated. No import is available.";
        return;
      }
      inferdromeBundle.append(
        new Option(
          `Customer-confirmed · ${selection.run_id}`,
          selection.run_id,
          true,
          true
        )
      );
      inferdromeBundle.disabled = true;
      selectedBundle = selection;
      inferdromeCatalogStatus.textContent =
        "Run and digest are frozen. ExitSpec will re-resolve and re-verify this exact bundle before evaluation.";
      return;
    }
    inferdromeSelection.querySelector("label").textContent =
      "Sealed evidence bundle";
    inferdromeBundle.replaceChildren();
    if (!catalog?.configured) {
      const option = new Option("Inferdrome runs root is not configured", "");
      inferdromeBundle.append(option);
      inferdromeBundle.disabled = true;
      selectedBundle = null;
      inferdromeCatalogStatus.textContent =
        "Configure --inferdrome-runs-root to list sealed, customer-eligible evidence.";
      return;
    }
    if (catalog.runs.length === 0) {
      const option = new Option("No eligible sealed bundles found", "");
      inferdromeBundle.append(option);
      inferdromeBundle.disabled = true;
      selectedBundle = null;
      inferdromeCatalogStatus.textContent =
        catalog.rejected_count > 0
          ? `${catalog.rejected_count} candidate bundle${catalog.rejected_count === 1 ? " was" : "s were"} rejected safely.`
          : "No customer-eligible evidence is available in the configured runs root.";
      return;
    }
    const preferredRunId =
      run.selected_run_id || selectedBundle?.run_id || catalog.runs[0].run_id;
    catalog.runs.forEach((entry) => {
      const option = new Option(
        `${entry.run_id} · ${entry.bundle_digest.slice(0, 19)}…`,
        entry.run_id
      );
      option.selected = entry.run_id === preferredRunId;
      inferdromeBundle.append(option);
    });
    selectedBundle =
      selectedCatalogEntry(inferdromeBundle.value) || catalog.runs[0];
    const locked = actionPending || isActiveStatus(run.status);
    inferdromeBundle.disabled = locked;
    inferdromeCatalogStatus.textContent =
      `${catalog.runs.length} eligible sealed bundle${catalog.runs.length === 1 ? "" : "s"}. ` +
      "ExitSpec re-verifies the digest before evaluation.";
  }

  function renderImportReceipt() {
    importReceipt.hidden = !(
      isExternalEvidence() && run.status === "COMPLETED"
    );
    if (importReceipt.hidden) {
      return;
    }
    document.querySelector("#receipt-run").textContent = run.producer_run_id;
    document.querySelector("#receipt-id").textContent = run.receipt_id;
    document.querySelector("#receipt-digest").textContent = run.bundle_digest;
    document.querySelector("#receipt-applicability").textContent =
      run.applicability_codes.length === 0
        ? "Compatible"
        : run.applicability_codes.join(" · ");
  }

  function renderManagedResultSummary() {
    const summary = document.querySelector("#managed-result-summary");
    summary.hidden = !(
      isManagedEvidence() && run.status === "COMPLETED"
    );
    if (summary.hidden) {
      return;
    }
    document.querySelector("#managed-result-p95-ttft").textContent =
      run.p95_ttft_ms === null ? "Not proven" : `${run.p95_ttft_ms} ms`;
    document.querySelector("#managed-result-error-rate").textContent =
      run.error_rate_percent === null
        ? "Not proven"
        : `${run.error_rate_percent}%`;
    document.querySelector("#managed-result-records").textContent =
      `${run.successful_count} / ${run.attempted_count}`;
    document.querySelector(
      "#managed-result-required-concurrency"
    ).textContent = String(run.required_configured_max_concurrency);
    document.querySelector(
      "#managed-result-observed-concurrency"
    ).textContent = String(run.observed_configured_max_concurrency);
  }

  function compactOwner(value) {
    return String(value)
      .replaceAll("_", " ")
      .replace(/\b\w/g, (character) => character.toUpperCase());
  }

  function operatorSymbol(value) {
    return value === "LT" ? "<" : "≤";
  }

  function observedFor(definition) {
    if (!run || run.status !== "COMPLETED") {
      return "Not measured";
    }
    if (definition.metric === "TTFT_P95_MS") {
      return run.p95_ttft_ms === null
        ? "Not proven"
        : `Observed ${run.p95_ttft_ms} ms`;
    }
    return run.error_rate_percent === null
      ? "Not proven"
      : `Observed ${run.error_rate_percent}%`;
  }

  function renderRequirements() {
    const list = document.querySelector("#requirement-list");
    list.replaceChildren();
    agreement.definitions.forEach((definition, index) => {
      const item = document.createElement("li");
      const identity = document.createElement("div");
      identity.className = "requirement-identity";
      const number = document.createElement("span");
      number.className = "requirement-number";
      number.textContent = String(index + 1).padStart(2, "0");
      const label = document.createElement("strong");
      label.textContent =
        definition.metric === "TTFT_P95_MS"
          ? "P95 time to first token"
          : "Attempted-request error rate";
      const samples = document.createElement("small");
      samples.textContent = `${definition.minimum_samples} samples · concurrency ${definition.concurrency}`;
      identity.append(number, label, samples);

      const result = document.createElement("div");
      result.className = "requirement-result";
      result.dataset.state =
        run.status === "COMPLETED" ? run.verdict : "NOT_RUN";
      const threshold = document.createElement("strong");
      threshold.className = "requirement-threshold";
      threshold.textContent =
        `${operatorSymbol(definition.operator)} ${definition.threshold}` +
        (definition.metric === "TTFT_P95_MS" ? " ms" : "%");
      const observed = document.createElement("small");
      observed.textContent = observedFor(definition);
      result.append(threshold, observed);
      item.append(identity, result);
      list.append(item);
    });
    list.setAttribute("aria-busy", "false");
  }

  function reasonCopy() {
    if (isExternalEvidence()) {
      return run.status === "INGESTION_REJECTED"
        ? `The sealed bundle was rejected during ingestion (${run.rejection_code}). No acceptance verdict was issued.`
        : "The import failed closed before a trusted receipt or verdict could be released.";
    }
    return (
      REASON_COPY[run.reason_code] ||
      "The run stopped without releasing a performance conclusion."
    );
  }

  function setJourney(hasEvidence) {
    document.querySelector("#journey-prove").dataset.state = hasEvidence
      ? "complete"
      : "current";
    document.querySelector("#performance-phase").textContent = hasEvidence
      ? "EVIDENCE"
      : "PROVE";
  }

  function renderEvidence() {
    const panel = document.querySelector(".evidence-panel");
    const status = document.querySelector("#evidence-status");
    const verdict = document.querySelector("#evidence-verdict");
    const reason = document.querySelector("#evidence-reason");
    const breakdown = document.querySelector("#outcome-breakdown");
    const link = document.querySelector("#evidence-pack-link");
    link.hidden = true;
    link.removeAttribute("href");
    breakdown.hidden = true;
    breakdown.textContent = "";

    if (run.status === "COMPLETED") {
      const packUrl = safeEvidenceUrl(run.evidence_pack_url);
      panel.dataset.state = run.verdict;
      status.textContent = run.verdict.replaceAll("_", " ");
      verdict.textContent = run.verdict.replaceAll("_", " ");
      reason.textContent =
        isExternalEvidence()
          ? "ExitSpec independently verified, recalculated, and applied the frozen criterion to this untrusted bundle."
          : "This verdict was released only after artifact verification and independent recomputation.";
      breakdown.textContent = outcomeBreakdownText();
      breakdown.hidden = false;
      link.href = packUrl;
      link.hidden = false;
      setJourney(true);
      return;
    }
    if (run.status === "BLOCKED") {
      panel.dataset.state = "BLOCKED";
      status.textContent = "BLOCKED";
      verdict.textContent = "BLOCKED";
      reason.textContent =
        "Execution was blocked. No performance verdict exists.";
      setJourney(false);
      return;
    }
    if (run.status === "NOT_PROVEN") {
      panel.dataset.state = "NOT_PROVEN";
      status.textContent = "NOT PROVEN";
      verdict.textContent = "NOT PROVEN";
      reason.textContent =
        "No verified result was released from this execution attempt.";
      setJourney(false);
      return;
    }
    if (["INGESTION_REJECTED", "FAILED_CLOSED"].includes(run.status)) {
      panel.dataset.state = "NOT_PROVEN";
      status.textContent = "NO VERDICT";
      verdict.textContent = "NO VERDICT";
      reason.textContent = reasonCopy();
      setJourney(false);
      return;
    }
    panel.dataset.state = run.status;
    status.textContent =
      isActiveStatus(run.status) ? "PENDING" : "NOT RUN";
    verdict.textContent =
      isActiveStatus(run.status) ? "PENDING" : "NOT RUN";
    reason.textContent =
      isActiveStatus(run.status)
        ? isExternalEvidence()
          ? "Verification is active. This in-progress state is not a verdict."
          : "Execution is active. RUNNING is not a verdict."
        : "No performance conclusion exists before verified evidence.";
    setJourney(false);
  }

  function outcomeBreakdownText() {
    if (isManagedEvidence()) {
      return [
        `${run.attempted_count} records`,
        `${run.successful_count} successful`,
        `${run.error_count} failed`,
        `${run.anomalous_count} anomalous`,
      ].join(" · ");
    }
    if (isExternalEvidence()) {
      return [
        `${run.attempted_count} attempts`,
        `${run.successful_count} successful`,
        `${run.error_count} producer-reported failed`,
      ].join(" · ");
    }
    const counts = run.outcome_counts;
    const parts = [
      `${run.attempted_count} attempts`,
      `${counts.success} successful`,
    ];
    const labels = [
      ["http_error", "HTTP error", "HTTP errors"],
      ["timeout", "timeout", "timeouts"],
      ["protocol_error", "protocol error", "protocol errors"],
      ["transport_error", "transport error", "transport errors"],
      ["cancelled", "cancelled", "cancelled"],
      ["internal_error", "internal error", "internal errors"],
    ];
    labels.forEach(([key, singular, plural]) => {
      if (counts[key] > 0) {
        parts.push(
          `${counts[key]} ${counts[key] === 1 ? singular : plural}`
        );
      }
    });
    return parts.join(" · ");
  }

  function renderAction() {
    const heading = document.querySelector("#current-task-heading");
    const kicker = document.querySelector("#task-kicker");
    const guidance = document.querySelector("#task-guidance");
    const runReason = document.querySelector("#run-reason");
    const executionState = document.querySelector(".execution-state");
    executionState.dataset.state = run.status;
    document.querySelector("#execution-status").textContent =
      run.status.replaceAll("_", " ");
    document.querySelector("#operation-reference").textContent =
      run.operation_id || "Not started";

    runButton.hidden = false;
    runButton.disabled = true;
    acknowledgement.disabled = true;
    acknowledgementLabel.hidden = false;

    if (pocLifecycleClosed) {
      heading.textContent = "The final POC decision is recorded";
      kicker.textContent = "Current task · Closed";
      guidance.textContent =
        "Starting another proof run is unavailable after the POC lifecycle closes.";
      runReason.textContent =
        "Inspect the exact recorded evidence or terminal receipt instead.";
      runButton.hidden = true;
      acknowledgementLabel.hidden = true;
      return;
    }

    if (actionPending || isActiveStatus(run.status)) {
      heading.textContent = isExternalEvidence()
        ? "Evidence verification in progress"
        : "Proof run in progress";
      kicker.textContent = "Current task · Prove";
      guidance.textContent =
        isExternalEvidence()
          ? "ExitSpec is re-verifying the sealed bundle and independently recalculating supported facts."
          : "The server owns the target, workload, limits, credentials, and evidence path.";
      runReason.textContent =
        isExternalEvidence()
          ? "Producer verdicts are ignored. No progress percentage or ETA is inferred."
          : "Preflight runs first. No progress percentage or ETA is inferred.";
      runButton.textContent = isExternalEvidence()
        ? "Import in progress…"
        : "Run in progress…";
      acknowledgementLabel.hidden = true;
      return;
    }
    if (run.status === "COMPLETED") {
      heading.textContent = "Review the verified Evidence Pack";
      kicker.textContent = "Current task · Evidence";
      guidance.textContent =
        `${run.verdict.replaceAll("_", " ")} is the evidence verdict, not automatic authorization to ship.`;
      runReason.textContent =
        isExternalEvidence()
          ? "Import completed and the ExitSpec Evidence Pack passed independent verification."
          : "Execution completed and the Evidence Pack passed independent verification.";
      runButton.hidden = true;
      acknowledgementLabel.hidden = true;
      return;
    }
    if (
      isExternalEvidence() &&
      ["INGESTION_REJECTED", "FAILED_CLOSED"].includes(run.status)
    ) {
      heading.textContent = isManagedEvidence()
        ? "Evidence ingestion was rejected"
        : "Choose another sealed bundle";
      kicker.textContent = "Current task · Prove";
      guidance.textContent = isManagedEvidence()
        ? `${reasonCopy()} The frozen agreement cannot switch evidence; start a revision to select another profile.`
        : reasonCopy();
      runReason.textContent = guidance.textContent;
      runButton.textContent = isManagedEvidence()
        ? "Evidence rejected"
        : "Retry evidence import";
      runButton.hidden = isManagedEvidence();
      acknowledgementLabel.hidden = isManagedEvidence();
      acknowledgement.disabled = !selectedBundle;
      runButton.disabled =
        isManagedEvidence() || !selectedBundle || !acknowledgement.checked;
      return;
    }
    if (isExternalEvidence()) {
      heading.textContent = isManagedEvidence()
        ? "Verify the frozen evidence"
        : "Import sealed evidence";
      kicker.textContent = "Current task · Prove";
      guidance.textContent = selectedBundle
        ? isManagedEvidence()
          ? "The customer-confirmed run and digest will be re-verified, recalculated, and judged now."
          : "One sealed bundle will be verified, recalculated, and judged against the frozen agreement."
        : "Select a server-discovered, customer-eligible Inferdrome bundle to continue.";
      runReason.textContent =
        isManagedEvidence()
          ? "Native first-choices-event TTFT is accepted only under the exact frozen v3 identity."
          : "ExitSpec trusts neither the producer verdict nor a matching field name.";
      runButton.textContent = isManagedEvidence()
        ? "Verify & import evidence"
        : "Import sealed evidence";
      acknowledgement.disabled = !selectedBundle;
      runButton.disabled = !selectedBundle || !acknowledgement.checked;
      return;
    }
    if (["BLOCKED", "NOT_PROVEN"].includes(run.status)) {
      heading.textContent = "Retry the frozen proof";
      kicker.textContent = "Current task · Prove";
      guidance.textContent = reasonCopy();
      runReason.textContent = guidance.textContent;
      runButton.textContent = "Retry frozen proof";
    } else {
      heading.textContent = "Run the frozen proof";
      kicker.textContent = "Current task · Prove";
      guidance.textContent =
        "One bounded run will return verified evidence or fail closed.";
      runReason.textContent =
        "The readiness preflight is request 1 and does not itself produce a verdict.";
      runButton.textContent = "Run frozen proof";
    }
    acknowledgement.disabled = false;
    runButton.disabled = !acknowledgement.checked;
  }

  function renderAll() {
    document.querySelector("#performance-title").textContent =
      draft.display_name;
    document.querySelector("#performance-customer").textContent =
      draft.customer_label;
    document.querySelector("#performance-owner").textContent =
      compactOwner(draft.owner);
    document.querySelector("#performance-use-case").textContent =
      draft.use_case;
    document.querySelector("#agreement-status").textContent = "FROZEN";
    document.querySelector("#target-summary").textContent =
      `${run.target_provider} · ${run.model}`;
    document.querySelector("#measured-requests").textContent = String(
      run.measured_requests
    );
    document.querySelector("#configured-concurrency").textContent = String(
      run.concurrency
    );
    document.querySelector("#warmup-requests").textContent = String(
      run.warmup_requests
    );
    document.querySelector("#model-name").textContent = run.model;
    document.querySelector("#endpoint-class").textContent =
      run.endpoint_class;
    document.querySelector("#adapter-identity").textContent =
      `${run.adapter} · v${run.adapter_version}`;
    document.querySelector("#contract-identity").textContent =
      `${run.contract_id} · v${run.contract_version}`;
    document.querySelector("#readiness-status").textContent =
      isExternalEvidence() ? "Verify first" : "Runs first";
    renderInferdromeSelection();
    document.querySelector("#execution-acknowledgement-copy").textContent =
      isExternalEvidence()
        ? selectedBundle
          ? isManagedEvidence()
            ? `I authorize ExitSpec to re-verify and evaluate the exact customer-confirmed run ${selectedBundle.run_id}.`
            : `I authorize ExitSpec to import and evaluate ${selectedBundle.run_id} (${selectedBundle.bundle_digest}) against this frozen agreement.`
          : "Select one eligible sealed bundle before authorizing import."
        : `I authorize this exact ${run.authorized_request_count}-request run ` +
          `(${run.measured_requests} measured + ${run.warmup_requests} warmups + 1 preflight) against the frozen target.`;
    renderImportReceipt();
    renderManagedResultSummary();
    renderRequirements();
    renderEvidence();
    renderAction();
    main.setAttribute("aria-busy", "false");
    window.dispatchEvent(new CustomEvent("exitspec:evidence-updated"));
  }

  function blockProof(message) {
    actionPending = false;
    stopPolling();
    runButton.disabled = true;
    acknowledgement.disabled = true;
    errorPanel.textContent = message;
    errorPanel.hidden = false;
    main.setAttribute("aria-busy", "false");
  }

  function attemptStorageKey() {
    const selection = isExternalEvidence() && selectedBundle
      ? `.${selectedBundle.run_id}.${selectedBundle.bundle_digest}`
      : "";
    return `exitspec.proof.attempt.v1.${pocId}.${run.contract_hash}${selection}`;
  }

  function readAttempt() {
    try {
      const value = window.sessionStorage.getItem(attemptStorageKey());
      return safeText(value, 200) ? value : null;
    } catch {
      return null;
    }
  }

  function saveAttempt(value) {
    try {
      window.sessionStorage.setItem(attemptStorageKey(), value);
    } catch {
      // Server idempotency remains authoritative without browser storage.
    }
  }

  function clearAttempt() {
    try {
      window.sessionStorage.removeItem(attemptStorageKey());
    } catch {
      // Storage does not control proof authority.
    }
  }

  function newIdempotencyKey() {
    if (
      window.crypto &&
      typeof window.crypto.randomUUID === "function"
    ) {
      return `${isExternalEvidence() ? "import" : "proof"}_${window.crypto.randomUUID()}`;
    }
    const bytes = new Uint8Array(16);
    window.crypto.getRandomValues(bytes);
    return (
      `${isExternalEvidence() ? "import" : "proof"}_` +
      Array.from(bytes, (value) =>
        value.toString(16).padStart(2, "0")
      ).join("")
    );
  }

  function stopPolling() {
    if (pollTimer !== null) {
      window.clearTimeout(pollTimer);
      pollTimer = null;
    }
  }

  function latestProofApi() {
    return isExternalEvidence() ? latestImportApi : latestRunApi;
  }

  function trustedProofState(value) {
    return isExternalEvidence() ? trustedImport(value) : trustedRun(value);
  }

  function schedulePoll() {
    stopPolling();
    if (isTerminalStatus(run.status)) {
      return;
    }
    if (pollCount >= MAX_POLLS) {
      blockProof(
        "Status polling paused safely. Reload to resume; the run was not restarted."
      );
      return;
    }
    const delay =
      POLL_DELAYS[Math.min(pollCount, POLL_DELAYS.length - 1)];
    pollTimer = window.setTimeout(() => {
      void pollLatest();
    }, delay);
  }

  async function pollLatest() {
    pollCount += 1;
    try {
      const payload = await requestJson(latestProofApi());
      if (!trustedProofState(payload)) {
        throw new TypeError("Malformed run projection.");
      }
      run = payload;
      renderAll();
      schedulePoll();
    } catch {
      blockProof(
        "The run status could not be validated. Reload to resume without starting another run."
      );
    }
  }

  async function startProof() {
    if (
      actionPending ||
      pocLifecycleClosed ||
      !acknowledgement.checked ||
      isActiveStatus(run.status) ||
      run.status === "COMPLETED" ||
      (isExternalEvidence() && !selectedBundle)
    ) {
      return;
    }
    actionPending = true;
    errorPanel.hidden = true;
    renderAction();
    const idempotencyKey = readAttempt() || newIdempotencyKey();
    saveAttempt(idempotencyKey);
    try {
      const external = isExternalEvidence();
      const requestOptions = external
        ? {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({
              run_id: selectedBundle.run_id,
              bundle_digest: selectedBundle.bundle_digest,
              import_acknowledged: true,
              idempotency_key: idempotencyKey,
            }),
          }
        : {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({
              execution_acknowledged: true,
              idempotency_key: idempotencyKey,
            }),
          };
      const payload = await requestJson(
        external ? importsApi : runsApi,
        requestOptions
      );
      if (
        !hasExactKeys(payload, ["operation", "replayed"]) ||
        typeof payload.replayed !== "boolean" ||
        !(external
          ? trustedImport(payload.operation)
          : trustedRun(payload.operation))
      ) {
        throw new TypeError("Malformed start projection.");
      }
      run = payload.operation;
      acknowledgement.checked = false;
      pollCount = 0;
      renderAll();
      schedulePoll();
    } catch {
      blockProof(
        isExternalEvidence()
          ? "The import response was not trusted. Retry reuses the same operation key."
          : "The run start response was not trusted. Retry reuses the same operation key."
      );
    } finally {
      actionPending = false;
      if (run) {
        renderAction();
      }
    }
  }

  async function loadProof() {
    if (
      !pocId ||
      !pocApi ||
      !agreementApi ||
      !runsApi ||
      !latestRunApi ||
      !inferdromeCatalogApi ||
      !importsApi ||
      !latestImportApi
    ) {
      blockProof("This proof route is invalid.");
      return;
    }
    try {
      const [draftPayload, agreementPayload] = await Promise.all([
        requestJson(pocApi),
        requestJson(agreementApi),
      ]);
      if (
        !trustedDraft(draftPayload) ||
        !trustedAgreement(agreementPayload)
      ) {
        throw new TypeError("Malformed proof inputs.");
      }
      draft = draftPayload;
      agreement = agreementPayload;
      if (isExternalEvidence()) {
        if (isManagedEvidence()) {
          const importPayload = await requestJson(latestImportApi);
          if (!trustedImport(importPayload)) {
            throw new TypeError("Malformed managed proof inputs.");
          }
          catalog = null;
          selectedBundle = frozenManagedSelection();
          run = importPayload;
        } else {
          const [catalogPayload, importPayload] = await Promise.all([
            requestJson(inferdromeCatalogApi),
            requestJson(latestImportApi),
          ]);
          if (
            !trustedCatalog(catalogPayload) ||
            !trustedImport(importPayload)
          ) {
            throw new TypeError("Malformed Inferdrome proof inputs.");
          }
          catalog = catalogPayload;
          run = importPayload;
        }
      } else {
        const runPayload = await requestJson(latestRunApi);
        if (!trustedRun(runPayload)) {
          throw new TypeError("Malformed run projection.");
        }
        catalog = null;
        run = runPayload;
      }
      if (!crossBindingsValid()) {
        throw new TypeError("Cross-POC proof binding failed.");
      }
      if (isTerminalStatus(run.status)) {
        clearAttempt();
      }
      renderAll();
      if (isActiveStatus(run.status)) {
        schedulePoll();
      }
    } catch {
      blockProof(
        "The frozen agreement and run state could not be cross-validated. Proof actions remain disabled."
      );
    }
  }

  acknowledgement.addEventListener("change", renderAction);
  inferdromeBundle.addEventListener("change", () => {
    if (isManagedEvidence()) {
      inferdromeBundle.disabled = true;
      return;
    }
    selectedBundle = selectedCatalogEntry(inferdromeBundle.value);
    acknowledgement.checked = false;
    renderAll();
  });
  runButton.addEventListener("click", () => {
    void startProof();
  });
  window.addEventListener("exitspec:closure-state", (event) => {
    const closed = event.detail?.closed;
    if (typeof closed !== "boolean" || pocLifecycleClosed === closed) {
      return;
    }
    pocLifecycleClosed = closed;
    if (draft && agreement && run) {
      renderAll();
    }
  });
  window.addEventListener("pagehide", stopPolling);
  void loadProof();
})();
