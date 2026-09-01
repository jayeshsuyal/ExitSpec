(() => {
  "use strict";

  const PROFILE_ID = "exitspec.external-evidence.native-ttft-profile.v1";
  const PROFILE_VERSION = "v1";
  const RESPONSE_SCHEMA = "exitspec.proofability-workspace-response.v1";
  const REPORT_SCHEMA = "exitspec.proofability-report.v1";
  const PATH_RE = /^\/app\/pocs\/(poc_[a-z0-9][a-z0-9_-]{2,63})\/qualification\/proofability$/;
  const DIGEST_RE = /^sha256:[a-f0-9]{64}$/;
  const CRITERION_DISPOSITIONS = new Set([
    "PROVABLE",
    "CLARIFICATION_REQUIRED",
    "NOT_PROVABLE",
  ]);
  const REPORT_DISPOSITIONS = new Set([
    "PROVABLE",
    "PARTIALLY_PROVABLE",
    "CLARIFICATION_REQUIRED",
    "NOT_PROVABLE",
  ]);
  const REASON_CODES = new Set([
    "ALL_REQUIRED_OBSERVATIONS_AVAILABLE",
    "MISSING_OBSERVATION",
    "INCOMPATIBLE_METRIC_DEFINITION",
    "INCOMPATIBLE_SOURCE_FIELD",
    "INCOMPATIBLE_UNIT",
    "INCOMPATIBLE_POPULATION",
    "INCOMPATIBLE_REDUCER",
    "INCOMPATIBLE_PERCENTILE",
    "INCOMPATIBLE_RELIABILITY_BINDING",
    "UNMAPPABLE_FROZEN_CRITERION_SCHEMA",
  ]);
  const REMEDIATION_CODES = new Set([
    "NO_REMEDIATION_REQUIRED",
    "DECLARE_REQUIRED_OBSERVATION",
    "FREEZE_PROVIDER_NEUTRAL_CRITERION_SCHEMA",
  ]);
  const ERROR_CODES = new Set([
    "INVALID_REQUEST",
    "ORIGIN_FORBIDDEN",
    "POC_NOT_FOUND",
    "METHOD_NOT_ALLOWED",
    "IDEMPOTENCY_CONFLICT",
    "PAYLOAD_TOO_LARGE",
    "UNSUPPORTED_MEDIA_TYPE",
    "PROFILE_UNSUPPORTED",
    "CAPACITY_EXHAUSTED",
    "WORKSPACE_UNAVAILABLE",
  ]);

  const statusNode = document.getElementById("workspace-status");
  const contentNode = document.getElementById("workspace-content");
  const actionNode = document.getElementById("workspace-action");
  if (!(statusNode instanceof HTMLElement) ||
      !(contentNode instanceof HTMLElement) ||
      !(actionNode instanceof HTMLElement)) {
    return;
  }

  if (location.search !== "" ||
      location.hash !== "" ||
      location.href !== location.origin + location.pathname) {
    return;
  }
  const pathMatch = PATH_RE.exec(location.pathname);
  if (pathMatch === null) {
    return;
  }
  const pocId = pathMatch[1];
  const apiPath = `/api/pocs/${pocId}/qualification/proofability`;
  let pendingKey = null;
  let inFlight = false;

  function isObject(value) {
    return value !== null && typeof value === "object" && !Array.isArray(value);
  }

  function exactKeys(value, expected) {
    if (!isObject(value)) {
      return false;
    }
    const actual = Object.keys(value).sort();
    const wanted = [...expected].sort();
    return actual.length === wanted.length &&
      actual.every((key, index) => key === wanted[index]);
  }

  function isDigest(value) {
    return typeof value === "string" && DIGEST_RE.test(value);
  }

  function isStringArray(value, allowed) {
    return Array.isArray(value) && value.every(
      (item) => typeof item === "string" && allowed.has(item),
    );
  }

  function validateObservation(value) {
    if (!isObject(value) || typeof value.observation_kind !== "string") {
      return false;
    }
    if (value.observation_kind === "NATIVE_TTFT") {
      return exactKeys(value, [
        "observation_kind", "observation_id", "metric_definition_id",
        "source_field", "unit", "population", "reducer_id", "percentile",
      ]) && value.observation_id === "native_ttft_sample" &&
        value.metric_definition_id === "vllm_first_choices_event_v0_26" &&
        value.source_field === "request.timing.ttft_ns" &&
        value.unit === "ns" &&
        value.population === "successful_measured_requests_with_observed_ttft" &&
        value.reducer_id === "nearest_rank_v1" && value.percentile === "p95";
    }
    if (value.observation_kind === "SEMANTIC_FIRST_NONEMPTY_TTFT") {
      return exactKeys(value, [
        "observation_kind", "observation_id", "metric_definition_id",
        "source_field", "unit", "population", "reducer_id", "percentile",
      ]) && value.observation_id === "semantic_first_nonempty_ttft_sample" &&
        value.metric_definition_id === "first_nonempty_choices_delta_content_v1" &&
        value.source_field === "response.choices[].delta.content" &&
        value.unit === "ns" &&
        value.population === "successful_measured_requests_with_observed_ttft" &&
        value.reducer_id === "nearest_rank_v1" && value.percentile === "p95";
    }
    if (value.observation_kind === "MEASURED_ATTEMPT_RELIABILITY") {
      return exactKeys(value, [
        "observation_kind", "observation_id", "source_field",
        "latency_population", "reliability_numerator", "reliability_denominator",
      ]) && value.observation_id === "native_measured_request_outcome" &&
        value.source_field === "request.outcome.status" &&
        value.latency_population === "successful_measured_requests_with_observed_ttft" &&
        value.reliability_numerator === "failed_or_anomalous_native_measured_requests" &&
        value.reliability_denominator === "all_measured_requests";
    }
    return false;
  }

  function validateIncompatible(value) {
    return exactKeys(value, [
      "required_observation", "available_observation", "reason_code",
    ]) && validateObservation(value.required_observation) &&
      validateObservation(value.available_observation) &&
      typeof value.reason_code === "string" && REASON_CODES.has(value.reason_code);
  }

  function validateCriterion(value) {
    return exactKeys(value, [
      "criterion_id", "disposition", "required_observations",
      "available_observations", "missing_observations",
      "incompatible_observations", "reason_codes", "remediation_codes",
    ]) && typeof value.criterion_id === "string" &&
      typeof value.disposition === "string" &&
      CRITERION_DISPOSITIONS.has(value.disposition) &&
      Array.isArray(value.required_observations) &&
      value.required_observations.every(validateObservation) &&
      Array.isArray(value.available_observations) &&
      value.available_observations.every(validateObservation) &&
      Array.isArray(value.missing_observations) &&
      value.missing_observations.every(validateObservation) &&
      Array.isArray(value.incompatible_observations) &&
      value.incompatible_observations.every(validateIncompatible) &&
      isStringArray(value.reason_codes, REASON_CODES) &&
      isStringArray(value.remediation_codes, REMEDIATION_CODES);
  }

  function validateReport(value) {
    return exactKeys(value, [
      "schema_version", "canonicalization_version", "hash_version",
      "subject_digest", "scope_digest", "qualification_context_digest",
      "protocol_id", "protocol_version", "contract_id",
      "contract_canonical_digest", "capability_digest", "profile_id",
      "profile_version", "engine_id", "engine_version", "adapter_id",
      "adapter_version", "criterion_results", "overall_disposition",
      "proofability_report_digest",
    ]) && value.schema_version === REPORT_SCHEMA &&
      value.canonicalization_version === "rfc8785_jcs_v1" &&
      value.hash_version === "sha256_v1" &&
      isDigest(value.subject_digest) && isDigest(value.scope_digest) &&
      isDigest(value.qualification_context_digest) &&
      value.protocol_id === "inference-performance-qualification" &&
      value.protocol_version === "1.0.0" &&
      value.contract_id === "pr5-proofability-contract" &&
      isDigest(value.contract_canonical_digest) &&
      isDigest(value.capability_digest) && value.profile_id === PROFILE_ID &&
      value.profile_version === PROFILE_VERSION && value.engine_id === "vllm" &&
      value.engine_version === "0.26.0" &&
      value.adapter_id === "vllm_bench_serve" &&
      value.adapter_version === "1.0.0" &&
      Array.isArray(value.criterion_results) && value.criterion_results.length > 0 &&
      value.criterion_results.every(validateCriterion) &&
      typeof value.overall_disposition === "string" &&
      REPORT_DISPOSITIONS.has(value.overall_disposition) &&
      isDigest(value.proofability_report_digest);
  }

  function validateResponse(value, postResponse) {
    const keys = [
      "schema_version", "poc_id", "report", "needs_replan",
      "reported_context_digest", "resolved_context_digest", "profile_request",
      "context_source", "storage", "authority",
    ];
    if (postResponse) {
      keys.push("idempotent_replay");
    }
    if (!exactKeys(value, keys) || value.schema_version !== RESPONSE_SCHEMA ||
        value.poc_id !== pocId || typeof value.needs_replan !== "boolean" ||
        !isDigest(value.resolved_context_digest)) {
      return false;
    }
    if (postResponse && typeof value.idempotent_replay !== "boolean") {
      return false;
    }
    if (!exactKeys(value.profile_request, ["profile_id", "profile_version"]) ||
        value.profile_request.profile_id !== PROFILE_ID ||
        value.profile_request.profile_version !== PROFILE_VERSION) {
      return false;
    }
    if (!exactKeys(value.context_source, [
      "kind", "fixture_id", "fixture_version", "poc_derived",
    ]) || value.context_source.kind !== "PACKAGE_SYNTHETIC_FIXTURE" ||
        value.context_source.fixture_id !== "exitspec.synthetic-proofability-preflight.native-v1" ||
        value.context_source.fixture_version !== "v1" ||
        value.context_source.poc_derived !== false) {
      return false;
    }
    if (!exactKeys(value.storage, [
      "scope", "survives_process_restart", "shared_across_workers",
    ]) || value.storage.scope !== "PROCESS_LOCAL" ||
        value.storage.survives_process_restart !== false ||
        value.storage.shared_across_workers !== false) {
      return false;
    }
    if (!exactKeys(value.authority, [
      "deployment_authorized", "production_traffic_authorized",
      "traffic_expansion_authorized", "external_authorization_required",
    ]) || value.authority.deployment_authorized !== false ||
        value.authority.production_traffic_authorized !== false ||
        value.authority.traffic_expansion_authorized !== false ||
        value.authority.external_authorization_required !== true) {
      return false;
    }
    if (value.report === null) {
      if (value.needs_replan) {
        return isDigest(value.reported_context_digest) &&
          value.reported_context_digest !== value.resolved_context_digest;
      }
      return value.reported_context_digest === null;
    }
    return value.needs_replan === false && validateReport(value.report) &&
      value.reported_context_digest === value.report.qualification_context_digest &&
      value.resolved_context_digest === value.report.qualification_context_digest;
  }

  function validateError(value) {
    return exactKeys(value, ["error_code"]) &&
      typeof value.error_code === "string" && ERROR_CODES.has(value.error_code);
  }

  function appendDatum(parent, label, value) {
    const item = document.createElement("div");
    item.className = "datum";
    const itemLabel = document.createElement("span");
    itemLabel.className = "datum-label";
    itemLabel.textContent = label;
    const itemValue = document.createElement("span");
    itemValue.className = "datum-value";
    itemValue.textContent = value;
    item.append(itemLabel, itemValue);
    parent.append(item);
  }

  function renderResponse(value) {
    contentNode.replaceChildren();
    actionNode.replaceChildren();
    statusNode.textContent = value.report === null
      ? (value.needs_replan ? "The active binding changed. Create a new preflight." : "No preflight exists for this POC.")
      : "The current package-synthetic preflight is available.";

    const identity = document.createElement("div");
    identity.className = "identity-grid";
    appendDatum(identity, "POC", value.poc_id);
    appendDatum(identity, "Fixed profile", `${value.profile_request.profile_id} / ${value.profile_request.profile_version}`);
    appendDatum(identity, "Context fixture", `${value.context_source.fixture_id} / ${value.context_source.fixture_version}`);
    appendDatum(identity, "Resolved context", value.resolved_context_digest);
    contentNode.append(identity);

    if (value.report !== null) {
      const reportGrid = document.createElement("div");
      reportGrid.className = "report-grid";
      appendDatum(reportGrid, "Report planning disposition", value.report.overall_disposition);
      appendDatum(reportGrid, "Report identity", value.report.proofability_report_digest);
      value.report.criterion_results.forEach((criterion) => {
        const card = document.createElement("article");
        card.className = "criterion";
        const label = document.createElement("span");
        label.className = "criterion-label";
        label.textContent = criterion.criterion_id;
        const disposition = document.createElement("span");
        disposition.className = "criterion-value";
        disposition.textContent = criterion.disposition;
        const detail = document.createElement("p");
        detail.className = "criterion-detail";
        detail.textContent = `Required ${criterion.required_observations.length}; available ${criterion.available_observations.length}; missing ${criterion.missing_observations.length}; incompatible ${criterion.incompatible_observations.length}. Reasons: ${criterion.reason_codes.join(", ")}. Next: ${criterion.remediation_codes.join(", ")}.`;
        card.append(label, disposition, detail);
        reportGrid.append(card);
      });
      contentNode.append(reportGrid);
      return;
    }

    const button = document.createElement("button");
    button.type = "button";
    button.textContent = "Create preflight";
    button.addEventListener("click", () => createPreflight(button));
    actionNode.append(button);
  }

  function newKey() {
    const bytes = new Uint8Array(16);
    crypto.getRandomValues(bytes);
    return `preflight-${Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("")}`;
  }

  async function parseResponse(response, postResponse) {
    const text = await response.text();
    let value;
    try {
      value = JSON.parse(text);
    } catch (_) {
      throw new Error("UNUSABLE_RESPONSE");
    }
    if (!response.ok) {
      if (!validateError(value)) {
        throw new Error("UNUSABLE_RESPONSE");
      }
      throw new Error(value.error_code);
    }
    if (!validateResponse(value, postResponse)) {
      throw new Error("UNUSABLE_RESPONSE");
    }
    return value;
  }

  async function createPreflight(button) {
    if (inFlight) {
      return;
    }
    inFlight = true;
    button.disabled = true;
    if (pendingKey === null) {
      pendingKey = newKey();
    }
    statusNode.textContent = "Creating the package-synthetic preflight.";
    try {
      const response = await fetch(apiPath, {
        method: "POST",
        credentials: "same-origin",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
          profile_id: PROFILE_ID,
          profile_version: PROFILE_VERSION,
          idempotency_key: pendingKey,
        }),
      });
      const value = await parseResponse(response, true);
      pendingKey = null;
      renderResponse(value);
    } catch (error) {
      statusNode.textContent = `Preflight unavailable: ${error instanceof Error ? error.message : "UNUSABLE_RESPONSE"}. Retry uses the same request identity.`;
      button.disabled = false;
    } finally {
      inFlight = false;
    }
  }

  async function loadWorkspace() {
    statusNode.textContent = "Loading the local proofability workspace.";
    try {
      const response = await fetch(apiPath, {
        method: "GET",
        credentials: "same-origin",
      });
      renderResponse(await parseResponse(response, false));
    } catch (error) {
      contentNode.replaceChildren();
      actionNode.replaceChildren();
      statusNode.textContent = `Workspace unavailable: ${error instanceof Error ? error.message : "UNUSABLE_RESPONSE"}.`;
    }
  }

  loadWorkspace();
})();
