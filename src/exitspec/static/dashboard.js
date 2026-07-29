(() => {
  "use strict";

  const WORKSPACE_API = "/api/workspace";
  const FILTERS = ["Active", "Needs attention", "Completed"];
  const POC_ID_PATTERN = /^poc_[a-z0-9][a-z0-9_-]{2,63}$/;
  const SEEDED_POC_IDS = new Set([
    "poc_support_agent_demo",
    "poc_inference_latency_demo",
  ]);
  const CONFIRM_ACTIONS = new Set([
    "PREPARE_AGREEMENT",
    "CREATE_CUSTOMER_REVIEW",
    "WAIT_FOR_CUSTOMER",
    "FREEZE_CONFIRMED_CONTRACT",
  ]);
  const PROVE_ACTIONS = new Set([
    "RUN_POC",
    "WAIT_FOR_PROOF",
    "RERUN_POC",
  ]);
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
    if (CONFIRM_ACTIONS.has(poc.next_action_code)) {
      return `${base}/agreement`;
    }
    if (
      PROVE_ACTIONS.has(poc.next_action_code) ||
      poc.next_action_code === "REVIEW_EVIDENCE"
    ) {
      return base;
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

  function sourceLabel(poc) {
    const summary = poc?.source_summary;
    if (!summary || summary.count === 0) {
      return "No source";
    }
    if (typeof summary.label !== "string") {
      return "Source unavailable";
    }
    const separator = summary.label.indexOf(" · ");
    return separator >= 0
      ? summary.label.slice(separator + 3)
      : summary.label;
  }

  function journeyStep(poc) {
    const action = poc?.next_action_code;
    if (action === "ADD_SOURCE") {
      return { number: 1, label: "Capture" };
    }
    if (
      [
        "REVIEW_PROPOSALS",
        "DEFINE_CRITERIA",
        "START_REVISION",
      ].includes(action)
    ) {
      return { number: 2, label: "Review" };
    }
    if (CONFIRM_ACTIONS.has(action)) {
      return { number: 3, label: "Confirm" };
    }
    if (PROVE_ACTIONS.has(action)) {
      return { number: 4, label: "Prove" };
    }
    if (action === "REVIEW_EVIDENCE" || poc?.derived_phase === "DECIDE") {
      return { number: 5, label: "Decide" };
    }
    return {
      number: poc?.derived_phase === "PROVE" ? 4 : 2,
      label: poc?.derived_phase === "PROVE" ? "Prove" : "Review",
    };
  }

  function actionPresentation(poc) {
    if (Array.isArray(poc?.blockers) && poc.blockers.length > 0) {
      return {
        title:
          poc.blockers[0].message || "Resolve the visible POC blocker.",
        button: "Resolve blocker",
        blocked: true,
      };
    }
    const presentation = {
      ADD_SOURCE: {
        title: "Add the first customer source.",
        button: "Add source",
      },
      REVIEW_PROPOSALS: {
        title: poc.next_human_action || "Review the extracted requirements.",
        button: "Review requirements",
      },
      DEFINE_CRITERIA: {
        title: poc.next_human_action || "Define measurable acceptance criteria.",
        button: "Define acceptance",
      },
      START_REVISION: {
        title: "Revise the customer agreement.",
        button: "Start revision",
      },
      PREPARE_AGREEMENT: {
        title: "Prepare the customer-visible agreement.",
        button: "Prepare agreement",
      },
      CREATE_CUSTOMER_REVIEW: {
        title: "Create the customer review draft.",
        button: "Create customer draft",
      },
      WAIT_FOR_CUSTOMER: {
        title: "Customer confirmation is pending.",
        button: "View agreement",
      },
      FREEZE_CONFIRMED_CONTRACT: {
        title: "Freeze the confirmed agreement.",
        button: "Freeze contract",
      },
      RUN_POC: {
        title: "Run the frozen proof.",
        button: "Run frozen proof",
      },
      WAIT_FOR_PROOF: {
        title: "The proof run is in progress.",
        button: "View proof run",
      },
      RERUN_POC: {
        title: "Retry the frozen proof.",
        button: "Retry frozen proof",
      },
      REVIEW_EVIDENCE: {
        title: "Review the verified decision.",
        button: "Review evidence",
      },
    }[poc?.next_action_code];
    return {
      title:
        presentation?.title || poc?.next_human_action || "Review this POC.",
      button: presentation?.button || "Open POC",
      blocked: false,
    };
  }

  function agreementLabel(poc) {
    if (!poc?.active_contract_id) {
      return "Not ready";
    }
    if (poc.next_action_code === "WAIT_FOR_CUSTOMER") {
      return "In review";
    }
    if (poc.next_action_code === "FREEZE_CONFIRMED_CONTRACT") {
      return "Confirmed";
    }
    if (
      PROVE_ACTIONS.has(poc.next_action_code) ||
      poc.next_action_code === "REVIEW_EVIDENCE" ||
      poc.derived_phase === "PROVE" ||
      poc.derived_phase === "DECIDE"
    ) {
      return "Frozen";
    }
    return "Draft";
  }

  function renderContinue(poc) {
    const card = $("#continue-card");
    card.replaceChildren();
    card.setAttribute("aria-busy", "false");

    if (!poc) {
      const empty = element("div", "continue-empty");
      empty.append(
        element("strong", "", "No POC needs a decision."),
        element(
          "p",
          "",
          "Create a POC when new customer requirements arrive."
        )
      );
      card.append(empty);
      return;
    }

    const action = actionPresentation(poc);
    const step = journeyStep(poc);
    const evidenceStatus =
      poc.latest_evidence_summary?.status || "NOT_RUN";

    const identity = element("section", "continue-identity");
    identity.append(
      element(
        "p",
        "journey-position",
        `Step ${step.number} of 5 · ${step.label}`
      ),
      element("h3", "", poc.display_name),
      element(
        "p",
        "continue-meta",
        `${poc.customer_label} · ${sourceLabel(poc)}`
      )
    );

    const nextAction = element(
      "section",
      `continue-action${action.blocked ? " is-blocked" : ""}`
    );
    nextAction.append(
      element(
        "p",
        "continue-label",
        action.blocked ? "Blocker" : "Next action"
      ),
      element("strong", "", action.title)
    );

    const boundaries = element("dl", "continue-boundaries");
    const agreement = element("div");
    agreement.append(
      element("dt", "", "Agreement"),
      element("dd", "", agreementLabel(poc))
    );
    const evidence = element("div");
    const evidenceValue = element(
      "dd",
      "",
      evidenceLabel(evidenceStatus)
    );
    evidenceValue.dataset.state = evidenceStatus;
    evidence.append(element("dt", "", "Evidence"), evidenceValue);
    boundaries.append(agreement, evidence);

    const footer = element("div", "continue-cta");
    const destination = workbenchUrl(poc);
    if (destination) {
      const link = element("a", "continue-link", action.button);
      link.href = destination;
      link.setAttribute(
        "aria-label",
        `${action.button} for ${poc.display_name}`
      );
      footer.append(link);
    } else {
      const unavailable = element(
        "span",
        "continue-link is-unavailable",
        "Action unavailable"
      );
      footer.append(unavailable);
    }

    card.append(identity, nextAction, boundaries, footer);
  }

  function renderRow(poc) {
    const item = element("li", "poc-list-item");
    const destination = workbenchUrl(poc);
    const row = element(
      destination ? "a" : "div",
      `poc-row${destination ? "" : " is-unavailable"}`
    );
    const action = actionPresentation(poc);
    const step = journeyStep(poc);
    const evidenceStatus =
      poc.latest_evidence_summary?.status || "NOT_RUN";

    if (destination) {
      row.href = destination;
      row.setAttribute(
        "aria-label",
        `Open ${poc.display_name}. Next step: ${action.title}`
      );
    } else {
      row.setAttribute("aria-disabled", "true");
    }

    const identity = element("span", "poc-name");
    identity.append(
      element("strong", "", poc.display_name),
      element("span", "", poc.customer_label)
    );

    const source = element("span", "source-value", sourceLabel(poc));

    const next = element(
      "span",
      `next-step-value${action.blocked ? " is-blocked" : ""}`
    );
    next.append(
      element("strong", "", action.title),
      element("small", "", `Step ${step.number} · ${step.label}`)
    );

    const evidence = element(
      "span",
      "evidence-value",
      evidenceLabel(evidenceStatus)
    );
    evidence.dataset.evidence = evidenceStatus;

    const arrow = element("span", "row-open", destination ? "→" : "—");
    arrow.setAttribute("aria-hidden", "true");
    row.append(identity, source, next, evidence, arrow);
    item.append(row);
    return item;
  }

  function listSummary(count) {
    const noun = count === 1 ? "POC" : "POCs";
    if (activeFilter === "Needs attention") {
      return `${count} ${noun} ${count === 1 ? "needs" : "need"} attention`;
    }
    return `${count} ${activeFilter.toLowerCase()} ${noun}`;
  }

  function renderWorkspace(workspace) {
    const pocs = Array.isArray(workspace.pocs) ? workspace.pocs : [];
    const list = $("#poc-list");
    const empty = $("#empty-state");

    renderContinue(workspace.continue_working || null);
    list.replaceChildren();
    list.setAttribute("aria-busy", "false");
    empty.hidden = pocs.length > 0;
    $("#list-labels").hidden = pocs.length === 0;
    pocs.forEach((poc) => list.append(renderRow(poc)));

    $("#list-summary").textContent = listSummary(pocs.length);

    if (pocs.length === 0) {
      $("#empty-title").textContent = activeFilter === "Active"
        ? "No active POCs."
        : "No POCs match this view.";
      $("#empty-copy").textContent = activeFilter === "Active"
        ? "Create a POC when new customer requirements arrive."
        : "Choose Active to return to current customer work.";
      $("#show-active").hidden = activeFilter === "Active";
      return;
    }
    $("#show-active").hidden = true;
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
    const card = $("#continue-card");
    card.replaceChildren(
      element(
        "p",
        "loading-copy",
        "The next action is unavailable. No status has been inferred."
      )
    );
    card.setAttribute("aria-busy", "false");
    $("#list-labels").hidden = true;

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
