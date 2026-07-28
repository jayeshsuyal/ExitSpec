(() => {
  "use strict";

  const API = {
    state: "/api/state",
    intake: "/api/intake",
    assistedIntake: "/api/assisted-intake",
    fireworksDisclosure: "/api/provider/fireworks/disclosure",
    fireworksAuthorization: "/api/provider/fireworks/authorization",
    fireworksExecution: "/api/provider/fireworks/execution",
    draftDefine: "/api/draft/define",
    review: "/api/review",
    customerDraft: "/api/customer-draft",
    revisionStart: "/api/revision/start",
    revisionEdit: "/api/revision/edit",
    freeze: "/api/freeze",
    prove: "/api/prove",
    reset: "/api/reset",
    sourceFixtures: "/api/source/fixtures",
    sourceImport: "/api/source/import",
  };

  const DEFAULT_DEMO_NOTES = "Customer: Our support agent must select the correct tool at least 95% of the time. We want to inspect any mistakes before we scale traffic.";
  const CUSTOMER_POLL_INTERVAL_MS = 1800;
  const recordingMode =
    new URLSearchParams(window.location.search).get("mode") === "recording";
  const emailIntakeMode =
    new URLSearchParams(window.location.search).get("intake") === "email";

  let state = null;
  let selectedScenario = "pass";
  let editingDraftId = null;
  let rerunMode = false;
  let customerPollTimer = null;
  let stateRefreshPromise = null;
  let stateRefreshVersion = 0;
  let pageActive = true;
  let fireworksDisclosure = null;
  let fireworksRunning = false;
  let fireworksAttempt = null;
  let fireworksAcknowledgedDisclosureId = null;
  let fireworksStatusMessage = "";
  let fireworksStatusTone = "";
  let sourceCatalog = null;
  let sourceCatalogRunning = false;
  let sourceCatalogVersion = 0;
  let sourceImportRunning = false;
  let sourceImportRequestPromise = null;
  let sourceOperationVersion = 0;
  let resetRunning = false;
  let sourceLastReceipt = null;
  let sourceTimingEvidence = null;
  let sourceStatusMessage = "";
  let sourceStatusTone = "";

  const $ = (selector) => document.querySelector(selector);
  const modeChip = $("#mode-chip");
  const intakeStatus = $("#intake-status");
  const proveStatus = $("#prove-status");
  const sourceIntakeStatus = $("#source-intake-status");

  function applyPresentationMode() {
    document.body.dataset.mode = recordingMode ? "recording" : "standard";
    document.body.classList.toggle("recording-mode", recordingMode);
    document.body.classList.toggle("email-intake-mode", emailIntakeMode);
    $("#recording-cue").hidden = !recordingMode;
  }

  function setMode(mode, message) {
    modeChip.textContent = message;
    modeChip.className = `mode-chip ${mode}`;
  }

  function setStatus(element, message) {
    element.textContent = message || "";
  }

  async function request(path, options = {}) {
    const response = await fetch(path, {
      ...options,
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      const typedError = body && typeof body.error === "object"
        ? body.error
        : null;
      const error = new Error(
        typedError?.message
        || (typeof body.error === "string" ? body.error : "")
        || `Request failed with ${response.status}.`
      );
      error.payload = body;
      error.status = response.status;
      error.code = typedError?.code || body.code || "";
      throw error;
    }
    return body;
  }

  function applyState(payload) {
    const incoming = payload && (payload.state || payload);
    if (!incoming || typeof incoming !== "object") {
      throw new Error("The local demo returned an invalid state.");
    }
    state = incoming;
    if (state.confirmation) {
      stopCustomerPolling();
    }
  }

  function newProviderOperationId(prefix) {
    if (window.crypto && typeof window.crypto.randomUUID === "function") {
      return `${prefix}-${window.crypto.randomUUID()}`;
    }
    const bytes = new Uint8Array(16);
    window.crypto.getRandomValues(bytes);
    const suffix = Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("");
    return `${prefix}-${suffix}`;
  }

  function providerActionLabel(nextAction) {
    const labels = {
      check_provider_credential: "Check the server credential, then authorize a new attempt.",
      check_provider_connectivity: "Check connectivity, then authorize a new attempt.",
      configure_provider: "Restart with Fireworks enabled and a server-owned key.",
      contact_provider: "Review the provider incident before authorizing another attempt.",
      reduce_request: "The frozen request exceeded its cost boundary; do not retry unchanged.",
      restore_provider_account: "Restore provider balance or access, then authorize a new attempt.",
      retry_later: "Wait, then authorize a new bounded attempt.",
      review_current_workflow: "Review the current workflow, then authorize again.",
      review_provider_destination: "Review the pinned destination before another attempt.",
      review_provider_output: "No proposal was accepted; inspect the local validation boundary.",
      review_provider_precondition: "Resolve the provider precondition before another attempt.",
      review_request: "Review the frozen request before another attempt.",
      review_source_link: "No proposal was accepted; review its source link.",
      restart_after_budget_review: "Review the spend ceiling before restarting the local server.",
      restart_with_provider_configuration: "Restart with one stable provider configuration.",
    };
    return labels[nextAction] || "";
  }

  function providerDataPolicy(disclosure) {
    const policy = disclosure?.data_policy_snapshot;
    if (!policy) {
      return "Provider policy details are unavailable.";
    }
    if (
      policy.prompt_and_generation_persistent_retention
      === "none_by_default_for_open_models_without_explicit_opt_in"
    ) {
      return "No persistent prompt or generation retention by default for open models without opt-in.";
    }
    return "Review the pinned provider data-policy snapshot before authorizing.";
  }

  function renderFireworksAssist() {
    const panel = $("#fireworks-assist");
    if (!panel) {
      return;
    }
    const checkbox = $("#fireworks-acknowledgement");
    const button = $("#fireworks-assist-button");
    const runtime = state?.provider_execution || fireworksDisclosure?.runtime;
    const enabled = Boolean(runtime?.enabled);
    const configured = Boolean(runtime?.configured);
    const disclosureReady = Boolean(fireworksDisclosure?.disclosure_id);
    const perRequestAmount = Number(
      fireworksDisclosure?.limits?.max_request_cost_usd
    );
    const remainingAmount = Number(runtime?.remaining_spend_usd);
    const budgetAvailable = (
      !Number.isFinite(perRequestAmount)
      || !Number.isFinite(remainingAmount)
      || remainingAmount >= perRequestAmount
    );

    document.body.classList.toggle("provider-enabled", enabled);
    $("#fireworks-provider").textContent = fireworksDisclosure?.provider
      ? fireworksDisclosure.provider[0].toUpperCase() + fireworksDisclosure.provider.slice(1)
      : "Fireworks";
    $("#fireworks-model").textContent = fireworksDisclosure?.model || "Unavailable";
    try {
      $("#fireworks-destination").textContent = fireworksDisclosure?.endpoint
        ? new URL(fireworksDisclosure.endpoint).host
        : "Unavailable";
    } catch (_error) {
      $("#fireworks-destination").textContent = "Unavailable";
    }
    const perRequest = fireworksDisclosure?.limits?.max_request_cost_usd;
    const remaining = runtime?.remaining_spend_usd;
    $("#fireworks-max-cost").textContent = perRequest
      ? `$${perRequest} max/action${remaining ? ` · $${remaining} process guardrail left` : ""}`
      : "Unavailable";
    $("#fireworks-data-policy").textContent = providerDataPolicy(fireworksDisclosure);
    const policy = fireworksDisclosure?.data_policy_snapshot;
    const limits = fireworksDisclosure?.limits;
    $("#fireworks-acknowledgement-copy").textContent = limits?.max_attempts
      ? `I authorize one bounded synthetic action (up to ${limits.max_attempts} provider attempts).`
      : "I authorize one bounded synthetic action under this disclosure.";
    $("#fireworks-policy-detail-copy").textContent = fireworksDisclosure
      ? [
          `Exact endpoint: ${fireworksDisclosure.endpoint}.`,
          policy?.prompt_cache === "volatile_memory_for_several_minutes_when_active"
            ? "Prompt cache may remain in volatile memory for several minutes while active."
            : "",
          policy?.metadata_logging === "service_metadata_including_token_counts"
            ? "Service metadata, including token counts, may be logged."
            : "",
          `Timeout ${limits?.timeout_seconds || "—"}s · up to ${limits?.max_attempts || "—"} attempts.`,
          policy?.region === "not_asserted_by_the_cited_provider_documentation"
            ? "Region is not asserted by the cited policy snapshot."
            : "",
        ].filter(Boolean).join(" ")
      : "";

    const consentMatchesDisclosure = Boolean(
      checkbox.checked
      && fireworksAcknowledgedDisclosureId === fireworksDisclosure?.disclosure_id
    );
    checkbox.disabled = (
      fireworksRunning
      || !configured
      || !budgetAvailable
      || !disclosureReady
    );
    button.disabled = (
      fireworksRunning
      || !configured
      || !budgetAvailable
      || !disclosureReady
      || !consentMatchesDisclosure
    );
    button.textContent = fireworksRunning ? "Calling Fireworks…" : "Draft with Fireworks";

    let runtimeLabel = "Checking…";
    let panelStatus = "loading";
    if (runtime) {
      if (!enabled) {
        runtimeLabel = "Disabled";
        panelStatus = "disabled";
      } else if (!configured) {
        runtimeLabel = "Key missing";
        panelStatus = "disabled";
      } else if (!budgetAvailable) {
        runtimeLabel = "Budget limit";
        panelStatus = "disabled";
      } else if (fireworksRunning) {
        runtimeLabel = "Running";
        panelStatus = "running";
      } else {
        runtimeLabel = "Ready";
        panelStatus = "ready";
      }
    }
    if (fireworksStatusMessage && !fireworksRunning) {
      panelStatus = fireworksStatusTone || panelStatus;
    }
    panel.dataset.status = panelStatus;
    $("#fireworks-runtime-status").textContent = runtimeLabel;
    $("#fireworks-assist-status").textContent = fireworksStatusMessage;
  }

  async function loadFireworksDisclosure() {
    try {
      const disclosure = await request(API.fireworksDisclosure);
      if (
        fireworksDisclosure
        && fireworksDisclosure.disclosure_id !== disclosure.disclosure_id
      ) {
        fireworksAttempt = null;
        fireworksAcknowledgedDisclosureId = null;
        $("#fireworks-acknowledgement").checked = false;
        fireworksStatusMessage = "Disclosure changed. Review it before authorizing.";
        fireworksStatusTone = "error";
      }
      fireworksDisclosure = disclosure;
    } catch (_error) {
      fireworksDisclosure = null;
      fireworksStatusMessage = "Provider disclosure is unavailable. The local fallback still works.";
      fireworksStatusTone = "error";
    }
    renderFireworksAssist();
  }

  function recordFireworksAcknowledgement() {
    const checkbox = $("#fireworks-acknowledgement");
    if (checkbox.checked && fireworksDisclosure?.disclosure_id) {
      fireworksAcknowledgedDisclosureId = fireworksDisclosure.disclosure_id;
      fireworksStatusMessage = "";
      fireworksStatusTone = "";
    } else {
      checkbox.checked = false;
      fireworksAcknowledgedDisclosureId = null;
    }
    renderFireworksAssist();
  }

  async function runFireworksAssist() {
    if (fireworksRunning) {
      return;
    }
    const checkbox = $("#fireworks-acknowledgement");
    if (
      !checkbox.checked
      || !fireworksAcknowledgedDisclosureId
      || fireworksAcknowledgedDisclosureId !== fireworksDisclosure?.disclosure_id
    ) {
      checkbox.checked = false;
      fireworksAcknowledgedDisclosureId = null;
      fireworksStatusMessage = "Review the disclosure and authorize one request first.";
      fireworksStatusTone = "error";
      renderFireworksAssist();
      return;
    }

    fireworksRunning = true;
    fireworksStatusMessage = "Checking the current disclosure…";
    fireworksStatusTone = "";
    renderFireworksAssist();
    try {
      const currentDisclosure = await request(API.fireworksDisclosure);
      fireworksDisclosure = currentDisclosure;
      if (
        fireworksAcknowledgedDisclosureId
        !== currentDisclosure.disclosure_id
      ) {
        fireworksAttempt = null;
        checkbox.checked = false;
        fireworksAcknowledgedDisclosureId = null;
        fireworksStatusMessage = "Disclosure changed. Review it before authorizing.";
        fireworksStatusTone = "error";
        return;
      }
      const runtime = currentDisclosure.runtime || {};
      if (!runtime.enabled || !runtime.configured) {
        fireworksAttempt = null;
        checkbox.checked = false;
        fireworksAcknowledgedDisclosureId = null;
        fireworksStatusMessage = "Fireworks is disabled or its server credential is missing. Use the local fallback.";
        fireworksStatusTone = "error";
        return;
      }
      if (
        !fireworksAttempt
        || fireworksAttempt.disclosureId !== currentDisclosure.disclosure_id
      ) {
        fireworksAttempt = {
          disclosureId: currentDisclosure.disclosure_id,
          authorizationKey: newProviderOperationId("provider-authorization"),
          executionKey: newProviderOperationId("provider-execution"),
        };
      }

      fireworksStatusMessage = "Authorizing this exact synthetic request…";
      renderFireworksAssist();
      const authorization = await request(API.fireworksAuthorization, {
        method: "POST",
        headers: { "Idempotency-Key": fireworksAttempt.authorizationKey },
        body: JSON.stringify({
          disclosure_id: fireworksAttempt.disclosureId,
          acknowledged: true,
        }),
      });
      fireworksDisclosure = authorization.disclosure || fireworksDisclosure;

      fireworksStatusMessage = "Calling Fireworks within the local cap and retry limits…";
      renderFireworksAssist();
      const response = await request(API.fireworksExecution, {
        method: "POST",
        headers: { "Idempotency-Key": fireworksAttempt.executionKey },
        body: "{}",
      });
      applyState(response);
      const execution = response.execution || {};
      const nextAction = providerActionLabel(execution.next_action);
      fireworksStatusMessage = [execution.safe_message, nextAction]
        .filter(Boolean)
        .join(" ");
      fireworksStatusTone = execution.status === "succeeded_needs_review"
        ? "success"
        : "error";
      fireworksAttempt = null;
      checkbox.checked = false;
      fireworksAcknowledgedDisclosureId = null;
      if (execution.status === "succeeded_needs_review") {
        closeSourceDrawer();
        window.requestAnimationFrame(() => {
          $("#candidate-list [data-decision], #candidate-list [data-edit-rule]")?.focus();
        });
      }
    } catch (error) {
      const executionPending = (
        error.status === 409
        && error.payload?.code === "provider_execution_in_progress"
      );
      if (error.status && !executionPending) {
        fireworksAttempt = null;
        checkbox.checked = false;
        fireworksAcknowledgedDisclosureId = null;
      }
      fireworksStatusMessage = executionPending
        ? "Fireworks is still working. Retry to check the same bounded action."
        : error.status
        ? error.message
        : "Connection interrupted. Retry to replay the same bounded action safely.";
      fireworksStatusTone = "error";
    } finally {
      fireworksRunning = false;
      render();
      renderFireworksAssist();
    }
  }

  function drafts() {
    return state && Array.isArray(state.drafts) ? state.drafts : [];
  }

  function approvedDrafts() {
    return drafts().filter((draft) => draft.status === "APPROVED");
  }

  function isAwaitingCustomerDecision() {
    return Boolean(
      pageActive
      && state?.customer_review_url
      && !state?.confirmation
      && !drafts().some((draft) => draft.status === "NEEDS_REVIEW")
    );
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

  function guidedSourceIntake() {
    return emailIntakeMode && state?.source_intake
      && typeof state.source_intake === "object"
      ? state.source_intake
      : null;
  }

  function setSourceStatus(message, tone = "") {
    sourceStatusMessage = message || "";
    sourceStatusTone = tone;
  }

  function preserveSourceReceipt(receipt) {
    const exactFields = [
      "source_type",
      "manifest_id",
      "manifest_version",
      "fixture_case_id",
      "outcome_code",
      "source_version",
      "candidate_count",
    ];
    if (
      !receipt
      || typeof receipt !== "object"
      || Object.keys(receipt).sort().join("|") !== exactFields.slice().sort().join("|")
    ) {
      throw new Error("The local source service returned an invalid receipt.");
    }
    return Object.freeze(Object.fromEntries(
      exactFields.map((field) => [field, receipt[field]])
    ));
  }

  function requireCompleteWorkflowState(payload) {
    const incoming = payload && (payload.state || payload);
    const requiredFields = [
      "mode",
      "safety",
      "provider_execution",
      "drafts",
      "contract",
      "ready_to_prepare_customer_review",
      "ready_to_freeze",
      "ready_to_prove",
      "proof_pack",
      "source_intake",
    ];
    if (
      !incoming
      || typeof incoming !== "object"
      || !requiredFields.every((field) => Object.hasOwn(incoming, field))
      || !Array.isArray(incoming.drafts)
      || !incoming.source_intake
      || typeof incoming.source_intake !== "object"
    ) {
      throw new Error("The complete local workflow state is unavailable.");
    }
    return payload;
  }

  function sourceFailureMessage(error) {
    const candidateCode = error?.code
      || error?.payload?.error?.code
      || error?.payload?.code
      || "";
    const code = typeof candidateCode === "string" ? candidateCode : "";
    if (!error?.status) {
      return "Local source service unavailable. Start the ExitSpec service, then try again.";
    }
    const pinnedMessages = {
      invalid_local_host: "Use the local ExitSpec application address.",
      forbidden_origin: "Open this action from the same local ExitSpec application.",
      forbidden_fetch_site: "Open this action from the same local ExitSpec application.",
      route_parameters_not_allowed: "The local source action does not accept route parameters.",
      get_body_not_allowed: "The local source catalog does not accept a request body.",
      unsupported_media_type: "The local service refused the source request format.",
      request_length_required: "The local service requires a bounded source request.",
      invalid_content_length: "The local service refused an invalid request length.",
      source_request_too_large: "The source request exceeded the safe size limit.",
      content_length_mismatch: "The source request length did not match its body.",
      empty_json_body: "The source request was empty.",
      malformed_json: "The source request was not valid JSON.",
      duplicate_json_member: "The source request repeated a JSON field.",
      json_object_required: "The source request must be one JSON object.",
      invalid_source_request: "Choose one approved sample email.",
      source_not_approved: "That sample is not approved for this guided demo.",
      source_import_refused: "The sample could not pass the safe source boundary.",
      source_change_requires_reset: "Reset this workflow before choosing a different sample. Nothing was changed.",
      source_import_locked: "Source import is locked because downstream review already exists. Reset to start over.",
      unknown_source_route: "The local source service is unavailable at the expected route.",
      method_not_allowed: "The local source service refused this action.",
    };
    if (Object.hasOwn(pinnedMessages, code)) {
      return `${pinnedMessages[code]} (${code})`;
    }
    const safeCode = /^[a-z][a-z0-9_]{0,47}$/.test(code) ? code : "";
    const safeRefusal = "The source action failed safely.";
    return safeCode ? `${safeRefusal} (${safeCode})` : safeRefusal;
  }

  function sourceReviewControl(draftId) {
    const controls = guidedSourceIntake()?.review_controls;
    if (!Array.isArray(controls)) {
      return null;
    }
    return controls.find((control) => control?.draft_id === draftId) || null;
  }

  function renderSourceIntake() {
    const panel = $("#source-intake-panel");
    panel.hidden = !emailIntakeMode;
    if (!emailIntakeMode) {
      return;
    }

    const intake = guidedSourceIntake();
    const start = panel.querySelector(".source-intake-start");
    const summary = $("#source-summary");
    const details = $("#source-summary-details");
    panel.classList.toggle("has-source", Boolean(intake));
    start.hidden = Boolean(intake);
    summary.hidden = !intake;

    if (intake) {
      summary.querySelector(".source-summary-copy strong").textContent = intake.label;
      summary.querySelector(".source-summary-copy span").textContent =
        `${intake.proposal_count} proposals · sensitive fields removed`;
      const detailCopy = details.querySelector(".source-summary-technical");
      const reviewState = intake.pending_count > 0
        ? `${intake.pending_count} awaiting human review`
        : "Human review complete";
      detailCopy.replaceChildren();
      const paragraph = document.createElement("p");
      paragraph.textContent = [
        `Version ${intake.source_version}.`,
        reviewState + ".",
        "Sensitive fields were removed before review.",
        sourceLastReceipt?.fixture_case_id === intake.fixture_case_id
          ? `Last import: ${sourceLastReceipt.outcome_code}.`
          : "",
        sourceTimingEvidence?.fixture_case_id === intake.fixture_case_id
          ? `Rendered locally in ${sourceTimingEvidence.elapsed_ms} ms.`
          : "",
        "To choose a different sample, reset this workflow first; current reviews remain unchanged until reset.",
      ].filter(Boolean).join(" ");
      detailCopy.append(paragraph);
      details.querySelector(".source-replay-action").disabled =
        sourceImportRunning || resetRunning;
      details.querySelector(".source-reset-action").disabled = resetRunning;
    } else {
      details.open = false;
      const select = $("#source-fixture-select");
      const selectedValue = select.value;
      select.replaceChildren();
      const fixtures = Array.isArray(sourceCatalog?.fixtures)
        ? sourceCatalog.fixtures
        : [];
      if (fixtures.length > 0) {
        fixtures.forEach((fixture) => {
          const option = document.createElement("option");
          option.value = fixture.fixture_case_id;
          option.textContent = fixture.label;
          select.append(option);
        });
        const allowedValues = fixtures.map((fixture) => fixture.fixture_case_id);
        select.value = allowedValues.includes(selectedValue)
          ? selectedValue
          : sourceCatalog.default_fixture_case_id;
      } else {
        const option = document.createElement("option");
        option.value = "";
        option.textContent = sourceCatalogRunning
          ? "Loading samples…"
          : "Samples unavailable";
        select.append(option);
      }
      select.disabled = sourceCatalogRunning
        || fixtures.length === 0
        || sourceImportRunning
        || resetRunning;
      const importButton = $("#import-source-fixture");
      importButton.disabled = select.disabled || !select.value;
      importButton.textContent = sourceImportRunning
        ? "Importing sample…"
        : "Import sample email";
    }

    sourceIntakeStatus.textContent = sourceStatusMessage;
    sourceIntakeStatus.className = `source-intake-status${sourceStatusTone ? ` is-${sourceStatusTone}` : ""}`;
  }

  async function loadSourceCatalog() {
    if (!emailIntakeMode || sourceCatalogRunning) {
      return;
    }
    const catalogVersion = ++sourceCatalogVersion;
    sourceCatalogRunning = true;
    setSourceStatus("Loading approved samples…");
    renderSourceIntake();
    try {
      const catalog = await request(API.sourceFixtures);
      if (catalogVersion !== sourceCatalogVersion || !pageActive) {
        return;
      }
      const fixtureIds = Array.isArray(catalog?.fixtures)
        ? catalog.fixtures.map((fixture) => fixture?.fixture_case_id).sort()
        : [];
      if (
        !catalog
        || !Array.isArray(catalog.fixtures)
        || catalog.fixtures.length !== 2
        || fixtureIds.join("|") !== "authority-attack|thread-root"
        || !fixtureIds.includes(catalog.default_fixture_case_id)
        || catalog.fixtures.some((fixture) => typeof fixture?.label !== "string")
      ) {
        throw new Error("The local source catalog returned an invalid response.");
      }
      sourceCatalog = catalog;
      setSourceStatus("");
    } catch (error) {
      if (catalogVersion !== sourceCatalogVersion || !pageActive) {
        return;
      }
      sourceCatalog = null;
      setSourceStatus(sourceFailureMessage(error), "error");
    } finally {
      if (catalogVersion === sourceCatalogVersion) {
        sourceCatalogRunning = false;
        if (pageActive) {
          renderSourceIntake();
        }
      }
    }
  }

  async function importSourceFixture(fixtureCaseId = "") {
    if (!emailIntakeMode || sourceImportRunning || resetRunning) {
      return;
    }
    const selectedFixture = fixtureCaseId || $("#source-fixture-select").value;
    if (!selectedFixture) {
      setSourceStatus("Choose one approved sample email.", "error");
      renderSourceIntake();
      return;
    }

    const operationVersion = ++sourceOperationVersion;
    const workflowVersion = ++stateRefreshVersion;
    const startedAt = window.performance.now();
    sourceImportRunning = true;
    setSourceStatus("Importing through the safe source boundary…");
    renderSourceIntake();
    let importRequest = null;
    try {
      importRequest = request(API.sourceImport, {
        method: "POST",
        body: JSON.stringify({ fixture_case_id: selectedFixture }),
      });
      sourceImportRequestPromise = importRequest;
      const response = await importRequest;
      if (
        operationVersion !== sourceOperationVersion
        || workflowVersion !== stateRefreshVersion
        || !pageActive
      ) {
        return;
      }
      sourceLastReceipt = preserveSourceReceipt(response?.receipt);
      const completeWorkflow = await request(API.state);
      if (
        operationVersion !== sourceOperationVersion
        || workflowVersion !== stateRefreshVersion
        || !pageActive
      ) {
        return;
      }
      applyState(requireCompleteWorkflowState(completeWorkflow));
      editingDraftId = null;
      const outcomeCode = sourceLastReceipt.outcome_code;
      setSourceStatus(
        outcomeCode === "duplicate_replay"
          ? "Same sample already imported. Existing reviews were preserved. (duplicate_replay)"
          : "Sample imported. Review each proposal.",
        "success"
      );
      render();
      await new Promise((resolve) => window.requestAnimationFrame(resolve));
      if (
        operationVersion !== sourceOperationVersion
        || workflowVersion !== stateRefreshVersion
        || !pageActive
      ) {
        return;
      }
      sourceTimingEvidence = Object.freeze({
        fixture_case_id: sourceLastReceipt.fixture_case_id,
        outcome_code: sourceLastReceipt.outcome_code,
        elapsed_ms: Math.max(
          0,
          Math.round((window.performance.now() - startedAt) * 100) / 100
        ),
      });
      renderSourceIntake();
      if (sourceLastReceipt.outcome_code !== "duplicate_replay") {
        window.requestAnimationFrame(() => {
          if (operationVersion === sourceOperationVersion) {
            $("#candidate-list [data-decision], #candidate-list [data-edit-rule]")?.focus();
          }
        });
      }
    } catch (error) {
      if (
        operationVersion !== sourceOperationVersion
        || workflowVersion !== stateRefreshVersion
        || !pageActive
      ) {
        return;
      }
      setSourceStatus(sourceFailureMessage(error), "error");
    } finally {
      if (sourceImportRequestPromise === importRequest) {
        sourceImportRequestPromise = null;
      }
      if (
        operationVersion === sourceOperationVersion
        && workflowVersion === stateRefreshVersion
      ) {
        sourceImportRunning = false;
        if (pageActive) {
          render();
        }
      }
    }
  }

  function percentage(value) {
    return typeof value === "number" ? `${(value * 100).toFixed(2)}%` : "—";
  }

  function tagClass(status) {
    return String(status || "needs-review").toLowerCase().replace(/_/g, "-");
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

  function criterionRows(criterion) {
    if (!criterion) {
      return "";
    }
    const rule = criterion.rule || {};
    const threshold = typeof rule.threshold === "number"
      ? `At least ${(rule.threshold * 100).toFixed(0)}%`
      : "Not defined";
    const samples = rule.minimum_samples
      ? `${rule.minimum_samples} fixed cases`
      : "Not defined";
    const confidence = String(rule.confidence_method || "Not defined")
      .replaceAll("_", " ")
      .replace("wilson two sided lower bound", "Wilson lower bound");
    return `
      <dl class="rule-rows">
        <div><dt>Target</dt><dd>${escapeHtml(threshold)}</dd></div>
        <div><dt>Test set</dt><dd>${escapeHtml(samples)}</dd></div>
        <div><dt>Calculation</dt><dd>${escapeHtml(confidence)}</dd></div>
      </dl>`;
  }

  function scopeLimit() {
    const nonGoals = state?.contract?.non_goals;
    if (Array.isArray(nonGoals) && nonGoals.length > 0) {
      return nonGoals.join(" ");
    }
    return "Only the frozen criterion and recorded synthetic run are covered; no broader production claim is made.";
  }

  function compactProofReason(proof) {
    return proof?.criterion_reason || proof?.overall_reason || "No evidence yet.";
  }

  function compactScopeLimit() {
    return "Live performance and production readiness.";
  }

  function compactNextAction(proof) {
    const verdict = proof?.overall_verdict;
    if (verdict === "PASS") {
      return "Review with the customer. A human decides what moves next.";
    }
    if (verdict === "FAIL") {
      return "Fix the failed criterion, then rerun.";
    }
    if (verdict === "BLOCKED") {
      return "Resolve the blocker, then rerun.";
    }
    if (verdict === "NOT_PROVEN") {
      return "Collect the missing evidence, then rerun.";
    }
    return "Run the frozen POC.";
  }

  function workflowModel() {
    if (emailIntakeMode && !guidedSourceIntake()) {
      return {
        stage: "define",
        eyebrow: "Define · Synthetic source",
        title: "Bring one sample request into review",
        copy: "Choose an approved synthetic email. Nothing is imported until you select the action below.",
        nextTitle: "Import sample email",
        nextCopy: "The source can propose requirements; only people can approve them.",
        blockers: ["An approved sample email must be imported."],
      };
    }

    const currentDrafts = drafts();
    const pendingDrafts = currentDrafts.filter((draft) => draft.status === "NEEDS_REVIEW");
    const pending = pendingDrafts.length;
    const reviewed = currentDrafts.length - pending;
    const decision = state?.confirmation?.decision;
    const hasReviewLink = Boolean(state?.customer_review_url);
    const hasProof = Boolean(state?.proof_pack);

    if (pending > 0) {
      const currentDraft = pendingDrafts[0];
      const hasMeasurableRule = Boolean(
        currentDraft?.proposed_criterion
        && (!currentDraft.open_questions || currentDraft.open_questions.length === 0)
      );
      const anotherRuleIsActive = currentDrafts.some(
        (draft) => draft.id !== currentDraft?.id
          && draft.status !== "REJECTED"
          && draft.proposed_criterion
      );
      return {
        stage: "define",
        eyebrow: emailIntakeMode
          ? "Email proposal · synthetic source"
          : `Define · Requirement ${reviewed + 1} of ${currentDrafts.length}`,
        title: emailIntakeMode
          ? "Does this match the intended POC?"
          : !hasMeasurableRule && anotherRuleIsActive
          ? "Keep this request as context?"
          : editingDraftId === currentDraft?.id || (!hasMeasurableRule && state?.revision_request)
          ? "Define the exact acceptance rule"
          : hasMeasurableRule
          ? "Does this rule match the customer’s intent?"
          : "Can this request use the supported measurement?",
        copy: emailIntakeMode
          ? `Review proposal ${reviewed + 1} of ${currentDrafts.length}. The email cannot approve its own request.`
          : !hasMeasurableRule && anotherRuleIsActive
          ? "One rule is already executable. Keep this request outside the agreement."
          : editingDraftId === currentDraft?.id || !hasMeasurableRule
          ? "Add the metric, threshold, and evidence rule."
          : hasMeasurableRule
          ? "Check the rule against the request."
          : "Context stays out until it has a pass/fail rule.",
        nextTitle: !hasMeasurableRule && anotherRuleIsActive
          ? "Keep as context"
          : hasMeasurableRule
            ? "Review this requirement"
            : "Define acceptance rule",
        nextCopy: !hasMeasurableRule && anotherRuleIsActive
          ? "Preserve the source without adding an unsupported claim."
          : hasMeasurableRule
          ? "Approve, edit, or keep it as context."
          : "The current engine supports exact support-tool selection only.",
        blockers: [`${pending} ${pending === 1 ? "item needs" : "items need"} a decision.`],
      };
    }
    if (approvedDrafts().length === 0) {
      return {
        stage: "define",
        eyebrow: "Define · No acceptance rule",
        title: "No measurable rule is included yet",
        copy: "No agreement is created without a measurable rule.",
        nextTitle: "Capture another requirement",
        nextCopy: "Open the meeting source and add a measurable requirement.",
        blockers: ["At least one supported rule must be defined and approved."],
      };
    }
    if (!hasReviewLink) {
      return {
        stage: "define",
        eyebrow: "Define · Draft ready",
        title: "Ready to create the customer review?",
        copy: "Send only the approved rule for customer confirmation.",
        nextTitle: "Create customer review",
        nextCopy: "Create a link for this exact version.",
        blockers: ["Customer confirmation."],
      };
    }
    if (decision === "REQUEST_CHANGES") {
      return {
        stage: "define",
        eyebrow: "Define · Changes requested",
        title: "What needs to change before approval?",
        copy: "Start a new version and keep the review history.",
        nextTitle: "Start a new version",
        nextCopy: "Edit, review, and resend.",
        blockers: ["Requested change."],
      };
    }
    if (decision !== "CONFIRM") {
      return {
        stage: "define",
        eyebrow: "Define · Awaiting customer",
        title: "Ready for the customer’s decision?",
        copy: "Open the draft to confirm it or request changes.",
        nextTitle: "Open customer review",
        nextCopy: "Confirm or request changes.",
        blockers: ["Customer decision."],
      };
    }
    if (state?.ready_to_freeze) {
      return {
        stage: "define",
        eyebrow: "Define · Customer confirmed",
        title: "Freeze the customer-confirmed version?",
        copy: "Lock the agreement used by every evidence run.",
        nextTitle: "Freeze confirmed contract",
        nextCopy: "Lock the version used by every evidence run.",
        blockers: ["Contract freeze."],
      };
    }
    if (state?.ready_to_prove && (!hasProof || rerunMode)) {
      return {
        stage: "prove",
        eyebrow: rerunMode ? "Prove · Run another set" : "Prove · Frozen agreement",
        title: rerunMode
          ? "Which reference set should run next?"
          : "Which dataset should test this agreement?",
        copy: "Every run uses the same frozen contract. Only the reference evidence changes.",
        nextTitle: rerunMode ? "Run selected reference set" : "Run this POC",
        nextCopy: "Measure the selected fixture against the frozen rule.",
        blockers: ["Evidence run."],
      };
    }
    if (hasProof) {
      const verdict = state.proof_pack.overall_verdict;
      const isPass = verdict === "PASS";
      return {
        stage: "decide",
        eyebrow: "Decide · Evidence recorded",
        title: isPass
          ? "The agreement was proved. What does the evidence say?"
          : `${verdict.replace("_", " ")} — what should happen next?`,
        copy: compactProofReason(state.proof_pack),
        nextTitle: isPass ? "Open evidence pack" : "Run another reference set",
        nextCopy: isPass
          ? "Inspect the complete artifact. PASS is not authorization."
          : "Resolve or change the evidence condition, then rerun the same frozen contract.",
        blockers: isPass
          ? ["No automatic deployment authorization is created."]
          : [compactProofReason(state.proof_pack)],
      };
    }
    return {
      stage: "define",
      eyebrow: "Define · Action required",
      title: "What should happen next?",
      copy: "Review the current agreement state before continuing.",
      nextTitle: "Continue",
      nextCopy: "Review the current state.",
      blockers: ["Incomplete contract."],
    };
  }

  function renderCustody(model) {
    const sourcePresent = !emailIntakeMode || Boolean(guidedSourceIntake());
    const hasPendingDrafts = sourcePresent
      && drafts().some((draft) => draft.status === "NEEDS_REVIEW");
    const hasReviewLink = Boolean(state?.customer_review_url || state?.customer_draft_url);
    const customerDecision = state?.confirmation?.decision;
    const isFrozen = state?.contract?.status === "FROZEN";
    const proof = state?.proof_pack;

    const entries = [
      {
        id: "source",
        className: sourcePresent ? "is-recorded" : "is-current",
        text: sourcePresent ? "CAPTURED" : "CHOOSE SAMPLE",
      },
      {
        id: "agreement",
        className: !sourcePresent
          ? "is-pending"
          : hasPendingDrafts
            ? "is-current"
            : "is-recorded",
        text: !sourcePresent ? "PENDING" : hasPendingDrafts ? "IN REVIEW" : "DRAFTED",
      },
      {
        id: "customer",
        className: customerDecision === "REQUEST_CHANGES"
          ? "is-warning"
          : customerDecision === "CONFIRM"
            ? "is-recorded"
            : hasReviewLink && !hasPendingDrafts
              ? "is-current"
              : "is-pending",
        text: customerDecision === "REQUEST_CHANGES"
          ? "CHANGES"
          : customerDecision === "CONFIRM"
            ? "CONFIRMED"
            : hasReviewLink
              ? "AWAITING"
              : "PENDING",
      },
      {
        id: "freeze",
        className: isFrozen
          ? "is-recorded"
          : state?.ready_to_freeze
            ? "is-current"
            : "is-pending",
        text: isFrozen ? "FROZEN" : state?.ready_to_freeze ? "READY" : "PENDING",
      },
      {
        id: "evidence",
        className: proof
          ? "is-recorded"
          : state?.ready_to_prove
            ? "is-current"
            : "is-pending",
        text: proof?.overall_verdict || (state?.ready_to_prove ? "READY" : "NOT RUN"),
      },
      {
        id: "decision",
        className: proof && model.stage === "decide" ? "is-current" : "is-pending",
        text: proof ? "READY" : "PENDING",
      },
    ];
    const stateClasses = ["is-pending", "is-current", "is-recorded", "is-warning"];

    entries.forEach((entry) => {
      const item = $(`#custody-${entry.id}`);
      const itemState = $(`#custody-${entry.id}-state`);
      item.classList.remove(...stateClasses);
      item.classList.add(entry.className);
      itemState.textContent = entry.text;
      if (entry.className === "is-current") {
        item.setAttribute("aria-current", "step");
      } else {
        item.removeAttribute("aria-current");
      }
    });
  }

  function renderWorkflow(model) {
    const stageOrder = ["define", "prove", "decide"];
    const currentIndex = stageOrder.indexOf(model.stage);

    $("#poc-label").textContent = state?.poc_label
      || guidedSourceIntake()?.label
      || state?.transcript?.title
      || "Current POC";
    $("#proof-pack-title").textContent = state?.contract?.use_case || state?.poc_label || "Current POC";
    $("#workspace-eyebrow").textContent = model.eyebrow;
    $("#current-task-title").textContent = model.title;
    $("#current-task-copy").textContent = model.copy;
    $("#next-action-title").textContent = model.nextTitle;
    $("#next-action-label").textContent = model.nextCopy;
    $("#review-count").hidden = true;

    document.querySelectorAll("[data-stage]").forEach((view) => {
      const isCurrent = view.dataset.stage === model.stage;
      view.hidden = !isCurrent;
      view.classList.toggle("is-current", isCurrent);
    });

    stageOrder.forEach((stage, index) => {
      const step = $(`#step-${stage}`);
      const isCurrent = index === currentIndex;
      step.classList.toggle("is-current", isCurrent);
      step.classList.toggle("is-complete", index < currentIndex);
      if (isCurrent) {
        step.setAttribute("aria-current", "step");
      } else {
        step.removeAttribute("aria-current");
      }
    });

    const blockerList = $("#blocker-list");
    blockerList.replaceChildren();
    model.blockers.forEach((blocker) => {
      const item = document.createElement("li");
      item.textContent = blocker;
      blockerList.append(item);
    });
  }

  function limitSummary(proof) {
    const scope = scopeLimit();
    if (!proof) {
      return `No acceptance result exists yet. ${scope}`;
    }
    if (proof.overall_verdict === "NOT_PROVEN") {
      return `The agreed acceptance claim remains unproven. ${scope}`;
    }
    if (proof.overall_verdict === "BLOCKED") {
      return `No acceptance conclusion can be drawn while the run is blocked. ${scope}`;
    }
    if (proof.overall_verdict === "FAIL") {
      return `This result does not prove the agreed acceptance claim. ${scope}`;
    }
    return scope;
  }

  function verdictColor(verdict) {
    if (verdict === "PASS") {
      return "var(--green)";
    }
    if (verdict === "FAIL" || verdict === "BLOCKED") {
      return "var(--red)";
    }
    return "var(--amber)";
  }

  function renderTranscript() {
    $("#transcript-title").textContent = state?.transcript?.title || "Discovery transcript";
    const firstSourcedDraft = drafts().find((draft) => draft.source_span?.quote);
    if (firstSourcedDraft) {
      $("#source-title").textContent = sourceQuote(firstSourcedDraft);
    }
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

  function structuredRuleEditor(draft) {
    const criterion = draft.proposed_criterion;
    const template = state?.supported_rule_template || {};
    const threshold = Number(criterion?.rule?.threshold ?? 0.95) * 100;
    const title = criterion?.title || "Exact tool selection";
    const samples = criterion?.rule?.minimum_samples || 200;
    const workload = criterion?.workload_slice || "support-tool-selection-v1";
    const isRevision = Boolean(state?.revision_request);
    const sourceControl = emailIntakeMode ? sourceReviewControl(draft.id) : null;
    const allowReject = !emailIntakeMode || Boolean(
      sourceControl
      && Array.isArray(sourceControl.allowed_actions)
      && sourceControl.allowed_actions.includes("REJECT")
    );
    return `
      <div class="rule-editor" data-rule-editor="${escapeHtml(draft.id)}">
        <div class="rule-editor__heading">
          <div>
            <p class="section-label">${isRevision ? "Structured revision" : "Human-defined rule"}</p>
            <h3>${isRevision ? "Update the measurable agreement" : "Define acceptance rule"}</h3>
          </div>
          <span>Human input required</span>
        </div>
        <p class="rule-boundary">${escapeHtml(template.limitation || "This demo currently supports exact support-tool selection only.")}</p>
        <div class="rule-editor__fields">
          <label class="rule-title-field">
            <span>Rule title</span>
            <input type="text" value="${escapeHtml(title)}" data-rule-field="title" />
          </label>
          <label>
            <span>Threshold (%)</span>
            <input type="number" min="0.01" max="100" step="0.01" value="${escapeHtml(threshold.toFixed(2))}" data-rule-field="threshold" />
          </label>
          <label>
            <span>Minimum samples</span>
            <input type="number" min="1" step="1" value="${escapeHtml(samples)}" data-rule-field="samples" />
          </label>
          <label class="rule-workload-field">
            <span>Workload slice</span>
            <input type="text" value="${escapeHtml(workload)}" data-rule-field="workload" />
          </label>
        </div>
        <details class="rule-technical-details">
          <summary>Measurement details</summary>
          <div class="rule-technical-panel">
            <dl class="supported-rule-ledger" aria-label="Fixed deterministic measurement fields">
              <div><dt>Metric</dt><dd>${escapeHtml(template.metric_label || "Exact expected support-tool selection")}</dd></div>
              <div><dt>Adapter</dt><dd>${escapeHtml(`${template.adapter || "deterministic_tool_selection"}@${template.adapter_version || "1.0.0"}`)}</dd></div>
              <div><dt>Calculation</dt><dd>${escapeHtml(template.confidence_method || "95% Wilson lower bound")}</dd></div>
              <div><dt>Evidence</dt><dd>${escapeHtml(template.evidence_policy || "Case-level records and SHA-256 digests.")}</dd></div>
            </dl>
            <p class="generated-claim-note">The customer-facing sentence is generated from these fields; free-text claims are not accepted.</p>
          </div>
        </details>
        <div class="candidate-actions">
          <button class="button primary" type="button" data-save-rule="${escapeHtml(draft.id)}">${isRevision ? "Apply revision" : "Save rule"}</button>
          ${isRevision ? "" : `<button class="button secondary" type="button" data-cancel-rule="${escapeHtml(draft.id)}">Cancel</button>`}
          ${allowReject ? `<button class="text-action" type="button" data-decision="REJECT" data-id="${escapeHtml(draft.id)}">Keep as context</button>` : ""}
        </div>
      </div>`;
  }

  function bindTechnicalDetailsFocus(details) {
    details.addEventListener("focusout", (event) => {
      if (!event.relatedTarget || !details.contains(event.relatedTarget)) {
        details.open = false;
      }
    });
  }

  function candidateActions(draft) {
    if (draft.status !== "NEEDS_REVIEW") {
      return "";
    }
    if (emailIntakeMode && guidedSourceIntake()) {
      return emailCandidateActions(draft);
    }
    const complete = Boolean(
      draft.proposed_criterion
      && (!draft.open_questions || draft.open_questions.length === 0)
    );
    const revisionApplied = Array.isArray(state?.revision_edit_applied_ids)
      && state.revision_edit_applied_ids.includes(draft.id);
    const editorOpen = editingDraftId === draft.id
      || (Boolean(state?.revision_request) && !revisionApplied);
    if (editorOpen) {
      return structuredRuleEditor(draft);
    }
    if (!complete) {
      const anotherRuleIsActive = drafts().some(
        (candidate) => candidate.id !== draft.id
          && candidate.status !== "REJECTED"
          && candidate.proposed_criterion
      );
      if (anotherRuleIsActive) {
        return `
          <div class="candidate-actions contextual-only">
            <span>One executable rule is already included.</span>
            <button class="button primary" type="button" data-decision="REJECT" data-id="${escapeHtml(draft.id)}">Keep as context</button>
          </div>`;
      }
      return `
        <div class="candidate-actions">
          <button class="button primary" type="button" data-edit-rule="${escapeHtml(draft.id)}">Define acceptance rule</button>
          <button class="button secondary" type="button" data-decision="REJECT" data-id="${escapeHtml(draft.id)}">Keep as context</button>
        </div>`;
    }
    return `
      <div class="candidate-actions">
        <button class="button primary" type="button" data-decision="APPROVE" data-id="${escapeHtml(draft.id)}">Matches intent</button>
        <button class="button secondary" type="button" data-edit-rule="${escapeHtml(draft.id)}">Edit rule</button>
        <button class="text-action" type="button" data-decision="REJECT" data-id="${escapeHtml(draft.id)}">Keep as context</button>
      </div>`;
  }

  function emailCandidateActions(draft) {
    const control = sourceReviewControl(draft.id);
    if (!control) {
      return `
        <div class="candidate-actions contextual-only">
          <span>Review controls are unavailable. Refresh the local workflow.</span>
        </div>`;
    }
    const allowedActions = Array.isArray(control.allowed_actions)
      ? control.allowed_actions
      : [];
    const allowApprove = allowedActions.includes("APPROVE");
    const allowReject = allowedActions.includes("REJECT");
    const canEditRule = control.can_edit_rule === true;
    const complete = Boolean(
      draft.proposed_criterion
      && (!draft.open_questions || draft.open_questions.length === 0)
    );
    const editorOpen = editingDraftId === draft.id;
    if (editorOpen && canEditRule) {
      return structuredRuleEditor(draft);
    }

    const actions = [];
    if (allowApprove && complete) {
      actions.push(
        `<button class="button primary" type="button" data-decision="APPROVE" data-id="${escapeHtml(draft.id)}">Matches intent</button>`
      );
    }
    if (canEditRule) {
      actions.push(
        `<button class="button ${actions.length === 0 ? "primary" : "secondary"}" type="button" data-edit-rule="${escapeHtml(draft.id)}">Define acceptance rule</button>`
      );
    }
    if (allowReject) {
      actions.push(
        `<button class="${actions.length === 0 ? "button primary" : "text-action"}" type="button" data-decision="REJECT" data-id="${escapeHtml(draft.id)}">Keep as context</button>`
      );
    }
    if (actions.length === 0) {
      return `
        <div class="candidate-actions contextual-only">
          <span>No review action is allowed by the current server state.</span>
        </div>`;
    }
    return `<div class="candidate-actions">${actions.join("")}</div>`;
  }

  function renderCandidates() {
    const candidateList = $("#candidate-list");
    if (emailIntakeMode && !guidedSourceIntake()) {
      candidateList.hidden = true;
      candidateList.replaceChildren();
      $("#review-count").textContent = "0 reviewed";
      return;
    }
    candidateList.hidden = false;
    const currentDrafts = drafts();
    const pendingDrafts = currentDrafts.filter((draft) => draft.status === "NEEDS_REVIEW");
    const reviewed = currentDrafts.length - pendingDrafts.length;
    $("#review-count").textContent = `${reviewed} of ${currentDrafts.length} reviewed`;

    if (pendingDrafts.length > 0) {
      const draft = pendingDrafts[0];
      const criterion = draft.proposed_criterion;
      const source = sourceQuote(draft);
      const revisionApplied = Array.isArray(state?.revision_edit_applied_ids)
        && state.revision_edit_applied_ids.includes(draft.id);
      const editorVisible = editingDraftId === draft.id
        || (Boolean(state?.revision_request) && !revisionApplied);
      candidateList.innerHTML = `
        <article class="candidate decision-card">
          <div class="customer-ask">
            <p class="section-label">${emailIntakeMode ? "Email proposal · synthetic source" : "Customer asked · CALL 02:14 · CUSTOMER"}</p>
            <div class="customer-ask-copy">
              <blockquote>${escapeHtml(source)}</blockquote>
            </div>
          </div>

          ${editorVisible
            ? ""
            : `
              <div class="rule-review">
                <p class="section-label">${criterion ? "Proposed acceptance rule" : "NOT A TEST"}</p>
                <h2>${escapeHtml(criterion?.title || "No pass/fail rule")}</h2>
                ${criterion
                  ? `<p class="rule-explanation">${escapeHtml(criterion.normalized_claim)}</p>${criterionRows(criterion)}`
                  : `
                    <p class="rule-explanation">
                      Context only. It does not define measurable success.
                    </p>
                    <div class="missing-callout">
                      <strong>Missing</strong>
                      <span>Metric, threshold, evidence rule.</span>
                    </div>`}
              </div>`}

          ${candidateActions(draft)}
        </article>`;
    } else {
      const approved = currentDrafts.filter((draft) => draft.status === "APPROVED");
      const rejected = currentDrafts.filter((draft) => draft.status === "REJECTED");
      const approvedCriterion = approved.find((draft) => draft.proposed_criterion)?.proposed_criterion;
      const contractVersion = state?.contract?.version ? `v${state.contract.version}` : "Draft";
      const contractState = state?.contract?.status === "FROZEN"
        ? "Frozen contract"
        : state?.confirmation?.decision === "CONFIRM"
          ? "Customer-confirmed contract"
          : approved.length > 0
            ? "Customer review draft"
            : "No agreement created";
      const criterionLabel = `${approved.length} ${approved.length === 1 ? "criterion" : "criteria"} included`;
      const noteLabel = `${rejected.length} ${rejected.length === 1 ? "note" : "notes"} excluded`;
      candidateList.innerHTML = `
        <article class="contract-summary">
          <div class="summary-heading">
            <div>
              <p class="section-label">${escapeHtml(contractState)} · ${escapeHtml(contractVersion)}</p>
              <h2>${escapeHtml(approvedCriterion?.title || "No approved acceptance rule")}</h2>
            </div>
            <strong>${escapeHtml(criterionLabel)} · ${escapeHtml(noteLabel)}</strong>
          </div>
          ${approvedCriterion
            ? `<p class="rule-explanation">${escapeHtml(approvedCriterion.normalized_claim)}</p>${criterionRows(approvedCriterion)}`
            : `<p class="rule-explanation">No supported acceptance rule is included. Capture another requirement or restore the sample.</p>`}
        </article>`;
    }

    candidateList.querySelectorAll("[data-decision]").forEach((button) => {
      button.addEventListener("click", () => reviewDraft(button.dataset.id, button.dataset.decision));
    });
    candidateList.querySelectorAll("[data-edit-rule]").forEach((button) => {
      button.addEventListener("click", () => {
        editingDraftId = button.dataset.editRule;
        render();
      });
    });
    candidateList.querySelectorAll("[data-cancel-rule]").forEach((button) => {
      button.addEventListener("click", () => {
        editingDraftId = null;
        render();
      });
    });
    candidateList.querySelectorAll("[data-save-rule]").forEach((button) => {
      button.addEventListener("click", () => saveStructuredRule(button));
    });
    candidateList
      .querySelectorAll(".rule-technical-details")
      .forEach(bindTechnicalDetailsFocus);
  }

  function renderRevisionRequest() {
    const card = $("#revision-request-card");
    const hasPendingRevision = drafts().some(
      (draft) => draft.status === "NEEDS_REVIEW"
    );
    const rationale = state?.confirmation?.decision === "REQUEST_CHANGES"
      ? state.confirmation.rationale
      : hasPendingRevision
        ? state?.revision_request
        : null;
    card.hidden = !rationale;
    if (rationale) {
      $("#revision-request-text").textContent = `“${rationale}”`;
    }
  }

  function renderActions() {
    const sourceIntake = guidedSourceIntake();
    const sourceReviewIncomplete = Boolean(
      emailIntakeMode
      && sourceIntake
      && drafts().some((draft) => draft.status === "NEEDS_REVIEW")
    );
    const sourceProofEligible = !emailIntakeMode || Boolean(
      sourceIntake
      && state?.confirmation?.decision === "CONFIRM"
      && state?.contract?.status === "FROZEN"
    );
    const readyToPrepareReview = Boolean(
      state?.ready_to_prepare_customer_review
      && (!emailIntakeMode || (sourceIntake && !sourceReviewIncomplete))
    );
    const readyToFreeze = Boolean(state?.ready_to_freeze);
    const readyToProve = Boolean(state?.ready_to_prove && sourceProofEligible);
    const confirmationDecision = state?.confirmation?.decision;
    const hasCustomerReview = Boolean(state?.customer_review_url);
    const hasProof = Boolean(state?.proof_pack);
    const proofVerdict = state?.proof_pack?.overall_verdict;
    const noApprovedRule = drafts().length > 0
      && !drafts().some((draft) => draft.status === "NEEDS_REVIEW")
      && approvedDrafts().length === 0;
    const customerDraftButton = $("#create-customer-draft");
    customerDraftButton.disabled = !readyToPrepareReview;
    customerDraftButton.hidden = !readyToPrepareReview || hasCustomerReview;
    customerDraftButton.title = readyToPrepareReview ? "" : "Resolve each candidate before preparing a customer review.";

    const sourceButton = $("#open-source-controls");
    sourceButton.hidden = emailIntakeMode || !noApprovedRule;

    const runButton = $("#run-proof");
    runButton.disabled = !readyToProve;
    runButton.hidden = !readyToProve || (hasProof && !rerunMode);
    runButton.textContent = rerunMode ? "Run selected reference set" : "Run this POC";
    runButton.title = readyToProve ? "" : "Customer confirmation and an explicit freeze are required before running proof.";

    const rerunButton = $("#rerun-proof");
    rerunButton.hidden = !hasProof || rerunMode;
    rerunButton.className = proofVerdict === "PASS"
      ? "button secondary"
      : "button primary";

    const freezeButton = $("#freeze-contract");
    if (freezeButton) {
      freezeButton.disabled = !readyToFreeze;
      freezeButton.hidden = !readyToFreeze;
      freezeButton.title = readyToFreeze
        ? ""
        : "The exact contract must receive affirmative customer confirmation first.";
    }

    const revisionButton = $("#start-revision");
    revisionButton.hidden = confirmationDecision !== "REQUEST_CHANGES";

    const draftLink = $("#customer-draft-link");
    if (
      (state?.customer_review_url || state?.customer_draft_url)
      && !["CONFIRM", "REQUEST_CHANGES"].includes(confirmationDecision)
    ) {
      draftLink.href = state.customer_review_url || state.customer_draft_url;
      draftLink.textContent = confirmationDecision === "REQUEST_CHANGES"
        ? "Review requested changes"
        : "Open customer review";
      draftLink.hidden = false;
    } else {
      draftLink.hidden = true;
    }

    const proofLink = $("#proof-pack-link");
    if (state?.proof_pack?.report_url) {
      proofLink.href = state.proof_pack.report_url;
      proofLink.hidden = false;
      proofLink.className = proofVerdict === "PASS" && !rerunMode
        ? "button primary button-link"
        : "button secondary button-link";
    } else {
      proofLink.hidden = true;
    }

    const contract = state?.contract;
    $("#contract-chip").textContent = contract
      ? contract.status === "FROZEN"
        ? `v${contract.version} · frozen`
        : state?.confirmation?.decision === "CONFIRM"
          ? `v${contract.version} · customer confirmed`
          : state?.confirmation?.decision === "REQUEST_CHANGES"
            ? `v${contract.version} · changes requested`
            : `v${contract.version} · awaiting customer`
      : "Awaiting review";

    const agreementStatus = $("#agreement-status");
    if (agreementStatus) {
      agreementStatus.textContent = contract
        ? contract.status === "FROZEN"
          ? "Frozen"
          : state?.confirmation?.decision === "CONFIRM"
            ? "Customer confirmed"
            : state?.confirmation?.decision === "REQUEST_CHANGES"
              ? "Changes requested"
              : state?.customer_review_url
                ? "Waiting on customer"
                : "Internal review"
        : "Draft";
    }

    const evidenceStatus = $("#evidence-status");
    if (evidenceStatus) {
      evidenceStatus.textContent = hasProof
        ? state.proof_pack.overall_verdict
        : readyToProve
          ? "Ready to run"
          : "Not run";
      evidenceStatus.className = hasProof ? tagClass(state.proof_pack.overall_verdict) : "";
    }

    if (emailIntakeMode && !sourceIntake) {
      customerDraftButton.hidden = true;
      sourceButton.hidden = true;
      runButton.hidden = true;
      rerunButton.hidden = true;
      freezeButton.hidden = true;
      revisionButton.hidden = true;
      draftLink.hidden = true;
      proofLink.hidden = true;
    }
  }

  function renderProof() {
    const proof = state?.proof_pack;
    const verdict = $("#proof-verdict");
    const criterion = state?.contract?.criteria?.[0] || drafts().find((draft) => draft.proposed_criterion)?.proposed_criterion;
    const source = criterion?.source ? `“${criterion.source.quote}”` : sourceQuote(drafts()[0]);

    if (!proof) {
      verdict.className = "verdict-badge";
      verdict.textContent = "AWAITING";
      $("#pack-verdict").className = "pack-verdict";
      $("#pack-verdict").textContent = "AWAITING";
      $("#metric-row").innerHTML = [
        ["Fixed cases", "—"],
        ["95% lower bound", "—"],
        ["Agreed target", criterion?.rule ? percentage(criterion.rule.threshold) : "—"],
      ].map(([label, value]) => `<div class="metric"><span>${label}</span><strong>${escapeHtml(value)}</strong></div>`).join("");
      $("#evidence-explanation").textContent = state?.ready_to_prove
        ? "Ready. Run a fixed fixture against this contract."
        : state?.ready_to_freeze
          ? "Freeze the confirmed contract first."
          : "Complete review and confirmation first.";
      $("#evidence-footnote").textContent = "No evidence yet.";
      $("#pack-source").textContent = source;
      $("#pack-criterion").textContent = criterion ? criterionSummary(criterion) : "Human review is still required.";
      $("#pack-contract").textContent = contractLabel();
      $("#pack-result").textContent = "No evidence result yet.";
      $("#pack-result").style.color = "var(--muted)";
      $("#pack-why").className = "";
      $("#pack-why").textContent = state?.ready_to_prove
        ? "The confirmed contract is frozen, but no evidence run exists yet."
        : "The acceptance agreement is still waiting for customer confirmation and freeze.";
      $("#pack-limits").textContent = limitSummary(null);
      $("#pack-next-step").textContent = state?.ready_to_prove
        ? "Run the agreed proof against this exact frozen version."
        : state?.ready_to_freeze
          ? "Freeze the exact customer-confirmed contract."
          : state?.customer_review_url
            ? "Wait for the customer decision or reopen the review link."
            : "Resolve the visible requirements and prepare customer review.";
      $("#pack-id").textContent = "LOCAL / SYNTHETIC";
      return;
    }

    const verdictClass = tagClass(proof.overall_verdict);
    verdict.className = `verdict-badge ${verdictClass}`;
    verdict.textContent = proof.overall_verdict;
    $("#pack-verdict").className = `pack-verdict ${verdictClass}`;
    $("#pack-verdict").textContent = proof.overall_verdict;
    $("#metric-row").innerHTML = [
      ["Fixed cases", `${proof.sample_count || 0} / ${criterion?.rule?.minimum_samples || "—"}`],
      ["Observed rate", percentage(proof.observed_rate)],
      ["95% lower bound", percentage(proof.confidence_lower_bound)],
    ].map(([label, value]) => `<div class="metric"><span>${label}</span><strong>${escapeHtml(value)}</strong></div>`).join("");
    $("#evidence-explanation").textContent = compactProofReason(proof);
    $("#evidence-footnote").textContent = compactNextAction(proof);
    $("#pack-source").textContent = source;
    $("#pack-criterion").textContent = criterion ? criterionSummary(criterion) : "Frozen criterion is available in the full POC Acceptance Evidence Pack.";
    $("#pack-contract").textContent = proof.contract_hash || contractLabel();
    $("#pack-result").textContent = typeof proof.observed_rate === "number"
      ? `${proof.overall_verdict} — ${percentage(proof.observed_rate)} observed; lower bound ${percentage(proof.confidence_lower_bound)}.`
      : `${proof.overall_verdict} — ${compactProofReason(proof)}`;
    $("#pack-result").style.color = verdictColor(proof.overall_verdict);
    const sampleCount = proof.sample_count || 0;
    const observedCases = typeof proof.observed_rate === "number"
      ? Math.round(proof.observed_rate * sampleCount)
      : "—";
    const requiredThreshold = criterion?.rule
      ? percentage(criterion.rule.threshold)
      : "—";
    $("#pack-why").className = proof.overall_verdict === "PASS"
      ? "evidence-equation"
      : "evidence-reason";
    $("#pack-why").textContent = proof.overall_verdict === "PASS"
      ? `Required ≥ ${requiredThreshold} · Observed ${observedCases}/${sampleCount} (${percentage(proof.observed_rate)}) · Wilson lower bound ${percentage(proof.confidence_lower_bound)} · ${proof.overall_verdict}`
      : compactProofReason(proof);
    $("#pack-limits").textContent = compactScopeLimit();
    $("#pack-next-step").textContent = compactNextAction(proof);
    $("#pack-id").textContent = "EVIDENCE / RECORDED";
  }

  function contractLabel() {
    if (!state?.contract) {
      return "Waiting for human review";
    }
    return state.contract.canonical_hash || `Contract ${state.contract.id} v${state.contract.version}; the canonical hash is created only at explicit freeze.`;
  }

  function render() {
    const model = workflowModel();
    renderSourceIntake();
    renderTranscript();
    renderRevisionRequest();
    renderCandidates();
    renderActions();
    renderProof();
    renderCustody(model);
    renderWorkflow(model);
    renderFireworksAssist();
    reconcileCustomerPolling();
  }

  async function reviewDraft(draftId, decision) {
    const isApprove = decision === "APPROVE";
    setStatus(intakeStatus, "Saving review…");
    try {
      const response = await request(API.review, {
        method: "POST",
        body: JSON.stringify({
          draft_id: draftId,
          decision,
          reviewer: "field_engineer",
          rationale: isApprove
            ? "Field engineer confirmed the complete measurement rule during this local demo."
            : "Field engineer kept this source as context and excluded it from the current POC agreement.",
        }),
      });
      applyState(response);
      editingDraftId = null;
      if (emailIntakeMode) {
        setSourceStatus("");
      }
      setStatus(intakeStatus, "");
      render();
    } catch (error) {
      setStatus(intakeStatus, error.message);
    }
  }

  function selectScenario(scenario = "pass") {
    selectedScenario = scenario;
    document.querySelectorAll('input[name="scenario"]').forEach((input) => {
      input.checked = input.value === scenario;
    });
  }

  function closeSourceDrawer() {
    const sourceDetails = $("#source-details");
    if (sourceDetails) {
      sourceDetails.open = false;
    }
  }

  function resetLocalWorkbench() {
    stateRefreshVersion += 1;
    sourceOperationVersion += 1;
    sourceCatalogVersion += 1;
    sourceCatalogRunning = false;
    stopCustomerPolling();
    editingDraftId = null;
    rerunMode = false;
    fireworksAttempt = null;
    fireworksAcknowledgedDisclosureId = null;
    fireworksStatusMessage = "";
    fireworksStatusTone = "";
    fireworksRunning = false;
    sourceImportRunning = false;
    sourceLastReceipt = null;
    sourceTimingEvidence = null;
    setSourceStatus("");
    if ($("#fireworks-acknowledgement")) {
      $("#fireworks-acknowledgement").checked = false;
    }
    selectScenario("pass");
    closeSourceDrawer();
  }

  async function loadIntake() {
    const transcript = $("#meeting-notes").value.trim();
    if (!transcript) {
      setStatus(intakeStatus, "Paste synthetic meeting notes before capturing a transcript.");
      return;
    }
    setStatus(intakeStatus, "Capturing notes…");
    try {
      resetLocalWorkbench();
      const response = await request(API.intake, {
        method: "POST",
        body: JSON.stringify({ transcript, title: "Pasted synthetic discovery notes" }),
      });
      applyState(response);
      setStatus(intakeStatus, response.notice ? "Notes captured. Review the draft." : "Notes captured.");
      render();
    } catch (error) {
      setStatus(intakeStatus, error.message);
    }
  }

  async function runAssistedAuthoring() {
    const transcript = $("#meeting-notes").value.trim();
    if (!transcript) {
      setStatus(intakeStatus, "Paste meeting notes before drafting with assisted authoring.");
      return;
    }
    setStatus(intakeStatus, "Redacting notes and drafting locally…");
    try {
      resetLocalWorkbench();
      const response = await request(API.assistedIntake, {
        method: "POST",
        body: JSON.stringify({
          transcript,
          title: "Assisted discovery notes",
          customer_terms: [],
        }),
      });
      applyState(response);
      setStatus(intakeStatus, response.notice || "Assisted draft ready for human review.");
      render();
    } catch (error) {
      setStatus(intakeStatus, error.message);
    }
  }

  async function resetDemo() {
    if (resetRunning) {
      return;
    }
    const pendingSourceImport = sourceImportRequestPromise;
    resetLocalWorkbench();
    const resetVersion = stateRefreshVersion;
    resetRunning = true;
    if (emailIntakeMode) {
      setSourceStatus("Resetting workflow…");
      renderSourceIntake();
    }
    try {
      if (pendingSourceImport) {
        await pendingSourceImport.catch(() => null);
      }
      if (resetVersion !== stateRefreshVersion || !pageActive) {
        return;
      }
      applyState(await request(API.reset, { method: "POST", body: "{}" }));
      if (resetVersion !== stateRefreshVersion || !pageActive) {
        return;
      }
      await loadFireworksDisclosure();
      $("#meeting-notes").value = DEFAULT_DEMO_NOTES;
      setStatus(intakeStatus, "");
      setStatus(proveStatus, "");
      setSourceStatus("");
      render();
      if (emailIntakeMode && !sourceCatalog) {
        await loadSourceCatalog();
      }
      if (emailIntakeMode) {
        $("#source-fixture-select").focus();
      } else {
        $("#current-task").scrollIntoView({ block: "start", behavior: "auto" });
      }
    } catch (error) {
      if (emailIntakeMode) {
        setSourceStatus(sourceFailureMessage(error), "error");
        renderSourceIntake();
      } else {
        setStatus(intakeStatus, error.message);
      }
    } finally {
      if (resetVersion === stateRefreshVersion) {
        resetRunning = false;
        if (pageActive) {
          renderSourceIntake();
        }
      }
    }
  }

  async function createCustomerDraft() {
    try {
      applyState(await request(API.customerDraft, { method: "POST", body: "{}" }));
      setStatus(intakeStatus, "");
      render();
    } catch (error) {
      setStatus(intakeStatus, error.message);
    }
  }

  async function startRevision() {
    setStatus(intakeStatus, "Starting revision…");
    try {
      applyState(await request(API.revisionStart, { method: "POST", body: "{}" }));
      editingDraftId = null;
      rerunMode = false;
      setStatus(intakeStatus, "");
      render();
    } catch (error) {
      setStatus(intakeStatus, error.message);
    }
  }

  async function saveStructuredRule(button) {
    const editor = button.closest("[data-rule-editor]");
    if (!editor) {
      setStatus(intakeStatus, "The structured rule editor is unavailable.");
      return;
    }
    const title = editor.querySelector('[data-rule-field="title"]').value.trim();
    const threshold = Number(editor.querySelector('[data-rule-field="threshold"]').value);
    const samples = Number(editor.querySelector('[data-rule-field="samples"]').value);
    const workload = editor.querySelector('[data-rule-field="workload"]').value.trim();
    if (!title || !workload || !Number.isFinite(threshold) || !Number.isInteger(samples)) {
      setStatus(intakeStatus, "Complete the title, threshold, sample count, and workload before saving the rule.");
      return;
    }
    const isRevision = Boolean(state?.revision_request);
    setStatus(intakeStatus, isRevision ? "Saving revision…" : "Defining rule…");
    try {
      applyState(await request(isRevision ? API.revisionEdit : API.draftDefine, {
        method: "POST",
        body: JSON.stringify({
          draft_id: button.dataset.saveRule,
          title,
          threshold_percent: threshold,
          minimum_samples: samples,
          workload_slice: workload,
        }),
      }));
      editingDraftId = null;
      setStatus(intakeStatus, "");
      render();
    } catch (error) {
      setStatus(intakeStatus, error.message);
    }
  }

  async function freezeContract() {
    setStatus(proveStatus, "Freezing contract…");
    try {
      applyState(await request(API.freeze, { method: "POST", body: "{}" }));
      setStatus(proveStatus, "");
      render();
    } catch (error) {
      setStatus(proveStatus, error.message);
    }
  }

  function openSourceControls() {
    const sourceDetails = $("#source-details");
    if (!sourceDetails) {
      return;
    }
    sourceDetails.open = true;
    window.requestAnimationFrame(() => $("#meeting-notes").focus());
  }

  function beginRerun() {
    rerunMode = true;
    setStatus(proveStatus, "");
    render();
    window.requestAnimationFrame(() => {
      $("#prove").scrollIntoView({ block: "start", behavior: "auto" });
    });
  }

  function stopCustomerPolling() {
    if (customerPollTimer !== null) {
      window.clearTimeout(customerPollTimer);
      customerPollTimer = null;
    }
  }

  function reconcileCustomerPolling() {
    if (!isAwaitingCustomerDecision()) {
      stopCustomerPolling();
      return;
    }
    if (customerPollTimer === null && stateRefreshPromise === null) {
      customerPollTimer = window.setTimeout(() => {
        customerPollTimer = null;
        refreshState();
      }, CUSTOMER_POLL_INTERVAL_MS);
    }
  }

  async function refreshState() {
    if (!pageActive || sourceImportRunning || resetRunning) {
      return;
    }
    if (stateRefreshPromise !== null) {
      return stateRefreshPromise;
    }
    const requestedVersion = stateRefreshVersion;
    stateRefreshPromise = (async () => {
      try {
        const incoming = await request(API.state);
        if (!pageActive || requestedVersion !== stateRefreshVersion) {
          return;
        }
        applyState(incoming);
        render();
      } catch (_error) {
        // Keep the last valid local state visible; explicit actions surface errors.
      }
    })();
    try {
      await stateRefreshPromise;
    } finally {
      stateRefreshPromise = null;
      reconcileCustomerPolling();
    }
  }

  async function runProof() {
    const selected = document.querySelector('input[name="scenario"]:checked');
    selectedScenario = selected ? selected.value : "pass";
    setStatus(proveStatus, "Running POC…");
    try {
      applyState(await request(API.prove, { method: "POST", body: JSON.stringify({ scenario: selectedScenario }) }));
      rerunMode = false;
      setStatus(proveStatus, "");
      render();
      if (recordingMode) {
        $("#decide").scrollIntoView({ block: "start", behavior: "auto" });
      }
    } catch (error) {
      rerunMode = false;
      await refreshState();
      setStatus(proveStatus, error.message);
      render();
    }
  }

  async function initialise() {
    try {
      applyState(await request(API.state));
      await Promise.all([
        loadFireworksDisclosure(),
        emailIntakeMode ? loadSourceCatalog() : Promise.resolve(),
      ]);
      setMode(
        "live",
        recordingMode
          ? "Recording · synthetic"
          : emailIntakeMode
            ? "Local demo · synthetic email"
            : "Local demo"
      );
      render();
      if (recordingMode) {
        window.requestAnimationFrame(() => {
          $("#define").scrollIntoView({ block: "start", behavior: "auto" });
        });
      }
    } catch (error) {
      setMode("demo", "Synthetic demo state unavailable");
      if (emailIntakeMode) {
        setSourceStatus(
          "Local source service unavailable. Start ExitSpec, then refresh this page.",
          "error"
        );
        renderSourceIntake();
      } else {
        setStatus(intakeStatus, "Start the local server with exitspec serve, then refresh this page.");
      }
      setStatus(proveStatus, "The browser surface never fabricates a proof result when the local evidence engine is unavailable.");
    }
  }

  applyPresentationMode();

  $("#import-source-fixture").addEventListener("click", () => importSourceFixture());
  $("#source-summary-details .source-replay-action").addEventListener("click", () => {
    const fixtureCaseId = guidedSourceIntake()?.fixture_case_id;
    if (fixtureCaseId) {
      importSourceFixture(fixtureCaseId);
    }
  });
  $("#source-summary-details .source-reset-action").addEventListener("click", resetDemo);
  $("#load-transcript").addEventListener("click", loadIntake);
  if ($("#assisted-authoring")) {
    $("#assisted-authoring").addEventListener("click", runAssistedAuthoring);
  }
  $("#fireworks-acknowledgement").addEventListener("change", recordFireworksAcknowledgement);
  $("#fireworks-assist-button").addEventListener("click", runFireworksAssist);
  $("#reset-demo").addEventListener("click", resetDemo);
  $("#recording-restart").addEventListener("click", resetDemo);
  $("#open-source-controls").addEventListener("click", openSourceControls);
  $("#create-customer-draft").addEventListener("click", createCustomerDraft);
  $("#start-revision").addEventListener("click", startRevision);
  if ($("#freeze-contract")) {
    $("#freeze-contract").addEventListener("click", freezeContract);
  }
  $("#run-proof").addEventListener("click", runProof);
  $("#rerun-proof").addEventListener("click", beginRerun);
  document.querySelectorAll('input[name="scenario"]').forEach((input) => {
    input.addEventListener("change", () => { selectedScenario = input.value; });
  });
  window.addEventListener("focus", refreshState);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") {
      refreshState();
    }
  });
  window.addEventListener("pagehide", () => {
    pageActive = false;
    stateRefreshVersion += 1;
    sourceOperationVersion += 1;
    sourceCatalogVersion += 1;
    sourceImportRunning = false;
    sourceCatalogRunning = false;
    resetRunning = false;
    stopCustomerPolling();
  });
  window.addEventListener("pageshow", () => {
    pageActive = true;
    refreshState();
    if (emailIntakeMode && !sourceCatalog) {
      loadSourceCatalog();
    }
  });

  initialise();
})();
