(() => {
  "use strict";

  const WORKSPACE_API = "/api/workspace";
  const FILTERS = ["Active", "Needs attention", "Completed"];
  const POC_ID_PATTERN = /^poc_[a-z0-9][a-z0-9_-]{2,63}$/;
  const SEEDED_POC_IDS = new Set([
    "poc_support_agent_demo",
    "poc_inference_latency_demo",
  ]);
  let activeFilter = "Active";
  let requestVersion = 0;
  let selectedPocId = null;
  let visiblePocs = [];
  let nextUpPocId = null;

  const $ = (selector) => document.querySelector(selector);

  function element(tagName, className, text) {
    const node = document.createElement(tagName);
    if (className) {
      node.className = className;
    }
    if (text !== undefined) {
      node.textContent = text;
    }
    return node;
  }

  function workbenchUrl(poc) {
    const pocId = poc?.poc_id;
    if (typeof pocId !== "string" || !POC_ID_PATTERN.test(pocId)) {
      return null;
    }
    const base = `/app/pocs/${encodeURIComponent(pocId)}`;
    if (SEEDED_POC_IDS.has(pocId)) {
      return base;
    }
    if (poc.next_action_code === "ADD_SOURCE") {
      return `${base}/sources/new`;
    }
    if (poc.next_action_code === "REVIEW_PROPOSALS") {
      return `${base}/review`;
    }
    if (poc.next_action_code === "DEFINE_CRITERIA") {
      return `${base}/define`;
    }
    return null;
  }

  function evidenceLabel(status) {
    return {
      NOT_RUN: "Not run",
      PASS: "Pass",
      FAIL: "Fail",
      BLOCKED: "Blocked",
      NOT_PROVEN: "Not proven",
    }[status] || "Unavailable";
  }

  function compactOwner(owner) {
    return String(owner || "Unassigned")
      .replaceAll("_", " ")
      .replace(/\b\w/g, (character) => character.toUpperCase());
  }

  function updatedLabel(timestamp) {
    const value = new Date(timestamp);
    if (Number.isNaN(value.getTime())) {
      return "Update unavailable";
    }
    return new Intl.DateTimeFormat(undefined, {
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
    }).format(value);
  }

  function nextAction(poc) {
    if (Array.isArray(poc.blockers) && poc.blockers.length > 0) {
      return {
        text: poc.blockers[0].message || "Resolve the visible POC blocker.",
        blocked: true,
      };
    }
    return {
      text: poc.next_human_action || "Review this POC.",
      blocked: false,
    };
  }

  function agreementSummary(poc) {
    if (!poc.active_contract_id) {
      return {
        value: "Not created",
        detail: "Agreement is still being defined.",
      };
    }
    return {
      value: poc.active_contract_version
        ? `Contract v${poc.active_contract_version}`
        : "Contract recorded",
      detail: poc.active_contract_id,
    };
  }

  function evidenceSummary(poc) {
    const summary = poc.latest_evidence_summary || {};
    return {
      status: summary.status || "NOT_RUN",
      detail: summary.reason || "No Evidence Pack has been recorded.",
    };
  }

  function detailRow(label, value) {
    const row = element("div", "preview-meta-row");
    row.append(
      element("dt", "", label),
      element("dd", "", value)
    );
    return row;
  }

  function renderPreview(poc) {
    const card = $("#continue-card");
    card.replaceChildren();
    card.setAttribute("aria-busy", "false");

    if (!poc) {
      const empty = element("div", "preview-empty");
      empty.append(
        element("strong", "", "No POC selected"),
        element(
          "p",
          "",
          "Choose a POC from the current work queue to review its next decision."
        )
      );
      card.append(empty);
      return;
    }

    const action = nextAction(poc);
    const agreement = agreementSummary(poc);
    const evidence = evidenceSummary(poc);

    const identity = element("header", "preview-identity");
    const identityCopy = element("div");
    const priority = element(
      "p",
      "preview-priority",
      poc.poc_id === nextUpPocId ? "Next up" : "Selected"
    );
    const title = element("h3", "", poc.display_name);
    const customer = element(
      "p",
      "preview-customer",
      `${poc.customer_label} · ${poc.source_summary.label}`
    );
    identityCopy.append(priority, title, customer);
    const phase = element("span", "preview-phase", poc.derived_phase);
    phase.dataset.phase = poc.derived_phase;
    identity.append(identityCopy, phase);

    const current = element(
      "section",
      `preview-action${action.blocked ? " is-blocked" : ""}`
    );
    current.append(
      element("p", "preview-label", action.blocked ? "Blocker" : "Current action"),
      element("strong", "", action.text)
    );

    const boundaries = element("div", "decision-boundaries");

    const agreementBlock = element("section", "boundary-card agreement-boundary");
    agreementBlock.append(
      element("p", "preview-label", "Agreement"),
      element("strong", "", agreement.value),
      element("small", "", agreement.detail)
    );

    const evidenceBlock = element("section", "boundary-card evidence-boundary");
    evidenceBlock.dataset.evidence = evidence.status;
    evidenceBlock.append(
      element("p", "preview-label", "Evidence"),
      element("strong", "", evidenceLabel(evidence.status)),
      element("small", "", evidence.detail)
    );
    boundaries.append(agreementBlock, evidenceBlock);

    const meta = element("dl", "preview-meta");
    meta.append(
      detailRow("Owner", compactOwner(poc.owner)),
      detailRow("Updated", updatedLabel(poc.updated_at))
    );

    const footer = element("footer", "preview-footer");
    const destination = workbenchUrl(poc);
    if (destination) {
      const link = element("a", "continue-link", "Open POC");
      link.href = destination;
      link.setAttribute(
        "aria-label",
        `Open ${poc.display_name}: ${action.text}`
      );
      footer.append(link);
    } else {
      const unavailableLabel =
        poc.next_action_code === "PREPARE_AGREEMENT"
          ? "Agreement builder is next"
          : "POC unavailable";
      footer.append(
        element("span", "continue-link is-unavailable", unavailableLabel)
      );
    }

    card.append(identity, current, boundaries, meta, footer);
  }

  function updateSelectedRow() {
    document.querySelectorAll(".poc-row").forEach((button) => {
      const selected = button.dataset.pocId === selectedPocId;
      button.setAttribute("aria-pressed", String(selected));
      button.closest("li")?.classList.toggle("is-selected", selected);
    });
  }

  function selectPoc(pocId) {
    const selected = visiblePocs.find((poc) => poc.poc_id === pocId);
    if (!selected) {
      return;
    }
    selectedPocId = selected.poc_id;
    updateSelectedRow();
    renderPreview(selected);
  }

  function renderRow(poc) {
    const item = element("li", "poc-list-item");
    const button = element("button", "poc-row");
    button.type = "button";
    button.dataset.pocId = poc.poc_id;
    button.setAttribute("aria-pressed", "false");

    const action = nextAction(poc);
    button.setAttribute(
      "aria-label",
      `Select ${poc.display_name}, ${poc.customer_label}: ${action.text}`
    );

    const name = element("span", "poc-name");
    name.append(
      element("strong", "", poc.display_name),
      element("span", "", `${poc.customer_label} · ${poc.source_summary.label}`)
    );

    const phase = element("span", "phase-value", poc.derived_phase);
    phase.dataset.phase = poc.derived_phase;

    const evidenceStatus = poc.latest_evidence_summary?.status || "NOT_RUN";
    const evidence = element(
      "span",
      "evidence-value",
      evidenceLabel(evidenceStatus)
    );
    evidence.dataset.evidence = evidenceStatus;

    const actionValue = element(
      "span",
      `row-next-action${action.blocked ? " is-blocked" : ""}`,
      action.text
    );

    button.append(name, phase, evidence, actionValue);
    button.addEventListener("click", () => selectPoc(poc.poc_id));
    item.append(button);
    return item;
  }

  function prioritizedPocs(pocs, preferredId) {
    const preferred = pocs.find((poc) => poc.poc_id === preferredId);
    if (!preferred) {
      return pocs;
    }
    return [preferred, ...pocs.filter((poc) => poc.poc_id !== preferredId)];
  }

  function renderWorkspace(workspace) {
    const pocs = Array.isArray(workspace.pocs) ? workspace.pocs : [];
    nextUpPocId = workspace.continue_working?.poc_id || null;
    visiblePocs = prioritizedPocs(pocs, nextUpPocId);

    const list = $("#poc-list");
    const empty = $("#empty-state");
    list.replaceChildren();
    list.setAttribute("aria-busy", "false");
    empty.hidden = visiblePocs.length > 0;
    $("#list-labels").hidden = visiblePocs.length === 0;

    visiblePocs.forEach((poc) => list.append(renderRow(poc)));
    $("#list-summary").textContent = visiblePocs.length === 1
      ? `1 ${activeFilter.toLowerCase()} POC`
      : `${visiblePocs.length} ${activeFilter.toLowerCase()} POCs`;

    if (visiblePocs.length === 0) {
      selectedPocId = null;
      $("#empty-title").textContent = activeFilter === "Active"
        ? "No other active POCs."
        : "No POCs match this view.";
      $("#empty-copy").textContent = activeFilter === "Active"
        ? "There is no current customer work in this local workspace."
        : "Choose Active to return to current customer work.";
      $("#show-active").hidden = activeFilter === "Active";
      renderPreview(null);
      return;
    }

    $("#show-active").hidden = true;
    const retainedSelection = visiblePocs.find(
      (poc) => poc.poc_id === selectedPocId
    );
    const initialSelection = retainedSelection
      || visiblePocs.find((poc) => poc.poc_id === nextUpPocId)
      || visiblePocs[0];
    selectPoc(initialSelection.poc_id);
  }

  function setFilter(filter) {
    activeFilter = filter;
    document.querySelectorAll("[data-filter]").forEach((button) => {
      button.setAttribute(
        "aria-pressed",
        String(button.dataset.filter === filter)
      );
    });
  }

  function showError(message) {
    const error = $("#workspace-error");
    error.textContent = message;
    error.hidden = false;
  }

  function renderUnavailable() {
    visiblePocs = [];
    selectedPocId = null;
    nextUpPocId = null;
    $("#poc-region").hidden = false;
    $("#list-labels").hidden = true;

    const card = $("#continue-card");
    card.replaceChildren(
      element(
        "p",
        "loading-copy",
        "POC priority is unavailable. No status has been inferred."
      )
    );
    card.setAttribute("aria-busy", "false");

    const list = $("#poc-list");
    list.replaceChildren(
      element(
        "li",
        "error-row",
        "POC summaries are unavailable. Reload after the workspace recovers."
      )
    );
    list.setAttribute("aria-busy", "false");
  }

  async function loadWorkspace(filter) {
    const version = ++requestVersion;
    setFilter(filter);
    $("#workspace-error").hidden = true;
    $("#poc-list").setAttribute("aria-busy", "true");
    try {
      const response = await fetch(
        `${WORKSPACE_API}?filter=${encodeURIComponent(filter)}`,
        { headers: { Accept: "application/json" } }
      );
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(payload.error || "The POC workspace is unavailable.");
      }
      if (version !== requestVersion || payload.selected_filter !== filter) {
        return;
      }
      renderWorkspace(payload);
    } catch (error) {
      if (version !== requestVersion) {
        return;
      }
      renderUnavailable();
      $("#empty-state").hidden = true;
      showError(error.message || "The POC workspace is unavailable.");
    }
  }

  document.querySelectorAll("[data-filter]").forEach((button) => {
    button.addEventListener("click", () => {
      const filter = button.dataset.filter;
      if (FILTERS.includes(filter)) {
        loadWorkspace(filter);
      }
    });
  });

  $("#show-active").addEventListener("click", () => loadWorkspace("Active"));
  loadWorkspace(activeFilter);
})();
