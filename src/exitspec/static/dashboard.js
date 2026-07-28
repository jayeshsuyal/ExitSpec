(() => {
  "use strict";

  const WORKSPACE_API = "/api/workspace";
  const FILTERS = ["Active", "Needs attention", "Completed"];
  let activeFilter = "Active";
  let requestVersion = 0;

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

  function workbenchUrl(pocId) {
    return `/app/pocs/${encodeURIComponent(pocId)}`;
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

  function renderContinue(poc) {
    const card = $("#continue-card");
    card.replaceChildren();
    card.setAttribute("aria-busy", "false");

    if (!poc) {
      const copy = element(
        "p",
        "loading-copy",
        "No active POC is waiting for work."
      );
      card.append(copy);
      return;
    }

    const identity = element("div", "continue-identity");
    const name = element("h3", "", poc.display_name);
    const customer = element(
      "p",
      "continue-customer",
      `${poc.customer_label} · ${poc.source_summary.label}`
    );
    identity.append(name, customer);

    const action = nextAction(poc);
    const actionBlock = element("div", "continue-action");
    const actionLabel = element(
      "span",
      "",
      action.blocked ? "Blocker" : `${poc.derived_phase} · Next action`
    );
    const actionText = element("p", "", action.text);
    actionBlock.append(actionLabel, actionText);

    const link = element("a", "continue-link", "Open POC");
    link.href = workbenchUrl(poc.poc_id);
    link.setAttribute(
      "aria-label",
      `Open ${poc.display_name}: ${action.text}`
    );

    card.append(identity, actionBlock, link);
  }

  function renderRow(poc) {
    const item = element("li");
    const link = element("a", "poc-row");
    const action = nextAction(poc);
    link.href = workbenchUrl(poc.poc_id);
    link.setAttribute(
      "aria-label",
      `${poc.display_name}, ${poc.customer_label} — ${action.text}`
    );

    const name = element("div", "poc-name");
    name.append(
      element("strong", "", poc.display_name),
      element("span", "", `${poc.customer_label} · ${poc.source_summary.label}`)
    );

    const phase = element("span", "phase-value", poc.derived_phase);
    phase.dataset.phase = poc.derived_phase;

    const actionValue = element(
      "span",
      `next-action-value${action.blocked ? " is-blocked" : ""}`,
      action.text
    );
    const owner = element("span", "owner-value", compactOwner(poc.owner));

    const evidenceStatus = poc.latest_evidence_summary?.status || "NOT_RUN";
    const rowMeta = element("span", "row-meta");
    const evidence = element(
      "span",
      "evidence-value",
      evidenceLabel(evidenceStatus)
    );
    evidence.dataset.evidence = evidenceStatus;
    const updated = element(
      "span",
      "updated-value",
      updatedLabel(poc.updated_at)
    );
    rowMeta.append(evidence, updated);

    link.append(name, phase, actionValue, owner, rowMeta);
    item.append(link);
    return item;
  }

  function renderWorkspace(workspace) {
    const pocs = Array.isArray(workspace.pocs) ? workspace.pocs : [];
    const currentPocId = workspace.continue_working?.poc_id;
    const listedPocs = activeFilter === "Active" && currentPocId
      ? pocs.filter((poc) => poc.poc_id !== currentPocId)
      : pocs;
    const list = $("#poc-list");
    const empty = $("#empty-state");
    list.replaceChildren();
    list.setAttribute("aria-busy", "false");
    empty.hidden = listedPocs.length > 0;
    $("#list-labels").hidden = listedPocs.length === 0;

    listedPocs.forEach((poc) => list.append(renderRow(poc)));
    $("#list-summary").textContent = pocs.length === 1
      ? `1 ${activeFilter.toLowerCase()} POC total`
      : `${pocs.length} ${activeFilter.toLowerCase()} POCs total`;
    $("#poc-list-heading").textContent = activeFilter === "Active"
      ? "Other active POCs"
      : activeFilter;
    if (activeFilter === "Active" && pocs.length > 0 && listedPocs.length === 0) {
      $("#empty-title").textContent = "No other active POCs.";
      $("#empty-copy").textContent = "Your current work is shown above.";
      $("#show-active").hidden = true;
    } else {
      $("#empty-title").textContent = "No POCs match this view.";
      $("#empty-copy").textContent = "Choose Active to return to current customer work.";
      $("#show-active").hidden = activeFilter === "Active";
    }
    renderContinue(workspace.continue_working);
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
