(() => {
  "use strict";

  const DETAIL_API = "/api/workspace/pocs/poc_inference_latency_demo";
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

  function compactOwner(owner) {
    return String(owner || "Unassigned")
      .replaceAll("_", " ")
      .replace(/\b\w/g, (character) => character.toUpperCase());
  }

  function spacedStatus(status) {
    return String(status || "UNAVAILABLE").replaceAll("_", " ");
  }

  function renderRequirements(requirements) {
    const list = $("#requirement-list");
    list.replaceChildren();
    list.setAttribute("aria-busy", "false");

    requirements.forEach((requirement, index) => {
      const item = element("li");
      const identity = element("div", "requirement-identity");
      identity.append(
        element("span", "requirement-number", String(index + 1).padStart(2, "0")),
        element("strong", "", requirement.label),
        element("small", "", requirement.sample_requirement)
      );
      item.append(
        identity,
        element("strong", "requirement-threshold", requirement.threshold)
      );
      list.append(item);
    });
  }

  function renderDetail(detail) {
    $("#performance-title").textContent = detail.display_name;
    $("#performance-customer").textContent = detail.customer_label;
    $("#performance-owner").textContent = compactOwner(detail.owner);
    $("#performance-phase").textContent = spacedStatus(detail.phase);
    $("#performance-use-case").textContent = detail.use_case;

    $("#agreement-status").textContent = spacedStatus(detail.agreement_status);
    $("#execution-status").textContent = spacedStatus(detail.execution_status);
    $("#evidence-status").textContent = spacedStatus(detail.evidence_status);
    $("#evidence-verdict").textContent = spacedStatus(detail.evidence_status);
    $("#customer-status").textContent = spacedStatus(detail.customer_status);
    $("#evidence-reason").textContent = detail.evidence_reason;

    $(".agreement-state").dataset.state = detail.agreement_status;
    $(".execution-state").dataset.state = detail.execution_status;
    $(".evidence-state").dataset.state = detail.evidence_status;
    $(".evidence-panel").dataset.state = detail.evidence_status;

    renderRequirements(detail.requirements);

    $("#measured-requests").textContent = String(
      detail.run_plan.measured_requests
    );
    $("#configured-concurrency").textContent = String(
      detail.run_plan.configured_concurrency
    );
    $("#warmup-requests").textContent = String(
      detail.run_plan.warmup_requests
    );
    $("#model-name").textContent = detail.run_plan.model;
    $("#endpoint-class").textContent = detail.run_plan.endpoint_class;
    $("#adapter-identity").textContent =
      `${detail.technical.adapter} · v${detail.technical.adapter_version}`;
    $("#contract-identity").textContent =
      `${detail.technical.contract_id} · v${detail.technical.contract_version}`;
    $("#performance-limitation").textContent = detail.limitation;
  }

  function showUnavailable(message) {
    $("#requirement-list").setAttribute("aria-busy", "false");
    $("#requirement-list").replaceChildren(
      element(
        "li",
        "panel-loading",
        "Frozen requirements are unavailable. No result has been inferred."
      )
    );
    $("#agreement-status").textContent = "UNAVAILABLE";
    $("#execution-status").textContent = "NOT STARTED";
    $("#evidence-status").textContent = "NOT RUN";
    $("#evidence-verdict").textContent = "NOT RUN";
    const error = $("#performance-error");
    error.textContent = message;
    error.hidden = false;
  }

  async function loadDetail() {
    try {
      const response = await fetch(DETAIL_API, {
        headers: { Accept: "application/json" },
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(payload.error || "Performance POC is unavailable.");
      }
      renderDetail(payload);
    } catch (error) {
      showUnavailable(error.message || "Performance POC is unavailable.");
    }
  }

  loadDetail();
})();
