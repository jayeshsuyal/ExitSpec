(() => {
  "use strict";

  const CREATE_API = "/api/pocs";
  const SOURCE_COPY = {
    EMAIL: {
      label: "Email intake",
      note: "Capture one customer email as NEEDS_REVIEW proposals.",
    },
    MEETING: {
      label: "Meeting intake",
      note: "Capture one redacted transcript as NEEDS_REVIEW proposals.",
    },
    DOCUMENT: {
      label: "Notes or document intake",
      note: "Capture bounded notes or document text as NEEDS_REVIEW proposals.",
    },
    EXISTING_CONTRACT: {
      label: "Existing contract intake",
      note: "Capture one strict contract import as NEEDS_REVIEW proposals.",
    },
  };

  const form = document.querySelector("#new-poc-form");
  const identityPanel = document.querySelector("#identity-panel");
  const fields = Array.from(
    identityPanel.querySelectorAll("input, textarea")
  );
  const createButton = document.querySelector("#create-poc");
  const status = document.querySelector("#form-status");
  const errorPanel = document.querySelector("#creation-error");
  const createdPanel = document.querySelector("#created-panel");
  const addFirstSourceLink = document.querySelector("#add-first-source");
  const sourceRadios = Array.from(
    document.querySelectorAll('input[name="first_source_choice"]')
  );
  let selectedSource = null;
  let inFlight = false;
  let idempotencyKey = null;
  let pendingPayload = null;

  function newIdempotencyKey() {
    if (globalThis.crypto && typeof globalThis.crypto.randomUUID === "function") {
      return `create-poc-${globalThis.crypto.randomUUID()}`;
    }
    return `create-poc-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  }

  function showError(message) {
    errorPanel.textContent = message;
    errorPanel.hidden = false;
  }

  function clearError() {
    errorPanel.hidden = true;
    errorPanel.textContent = "";
  }

  function updateIdentityState() {
    const editable = Boolean(selectedSource) && !inFlight && !pendingPayload;
    const canSubmit = Boolean(selectedSource) && !inFlight;
    identityPanel.setAttribute("aria-disabled", String(!editable));
    fields.forEach((field) => {
      field.disabled = !editable;
    });
    sourceRadios.forEach((radio) => {
      radio.disabled = inFlight || Boolean(pendingPayload);
    });
    createButton.disabled = !canSubmit;
    createButton.textContent = pendingPayload
      ? "Retry creating POC"
      : "Create POC and continue";
    status.textContent = editable
      ? "Add the draft identity, then create it."
      : pendingPayload
        ? "The response was interrupted. Retry the same draft safely."
        : inFlight
          ? "Creating the local draft…"
          : "Choose a starting source to continue.";
  }

  function renderCreated(payload) {
    const source = SOURCE_COPY[payload.first_source_choice];
    const destination = `/app/pocs/${encodeURIComponent(
      payload.poc_id
    )}/sources/new`;
    form.hidden = true;
    createdPanel.hidden = false;
    document.querySelector("#created-summary").textContent =
      `${payload.display_name} for ${payload.customer_label} was created as ${payload.poc_id}.`;
    document.querySelector("#next-intake-label").textContent =
      source ? source.label : "Selected source intake";
    document.querySelector("#next-intake-note").textContent =
      source
        ? source.note
        : "Capture the selected source as NEEDS_REVIEW proposals.";
    addFirstSourceLink.setAttribute(
      "href",
      destination
    );
    addFirstSourceLink.hidden = false;
    createdPanel.focus?.();
    try {
      window.location.replace(destination);
    } catch {
      // The verified fallback panel remains available if navigation is blocked.
    }
  }

  function isTrustedDraftResponse(payload) {
    if (!payload || typeof payload !== "object") {
      return false;
    }
    const source = SOURCE_COPY[payload.first_source_choice];
    const expectedRoutes = {
      DOCUMENT: "document",
      EMAIL: "email",
      EXISTING_CONTRACT: "existing_contract",
      MEETING: "meeting",
    };
    return Boolean(
      source &&
        typeof payload.poc_id === "string" &&
        /^poc_[a-z0-9][a-z0-9_-]{2,63}$/.test(payload.poc_id) &&
        typeof payload.display_name === "string" &&
        typeof payload.customer_label === "string" &&
        payload.source_ingestion_state === "NOT_STARTED" &&
        payload.archive_state === "ACTIVE" &&
        payload.next_intake_route ===
          expectedRoutes[payload.first_source_choice] &&
        typeof payload.idempotent_replay === "boolean"
    );
  }

  function safeFailureCopy(responseStatus) {
    if (responseStatus === 400) {
      return "Review the draft details and try again.";
    }
    if (responseStatus === 403 || responseStatus === 415) {
      return "This browser request was not accepted. Reload the page and try again.";
    }
    if (responseStatus === 409) {
      return "This draft attempt conflicts with an earlier request. Review the details and retry.";
    }
    if (responseStatus !== null && responseStatus < 500) {
      return "The draft request was not accepted. Review the form and retry.";
    }
    return "The response was interrupted or could not be trusted. Retry uses the same draft key.";
  }

  sourceRadios.forEach((radio) => {
    radio.addEventListener("change", () => {
      selectedSource = radio.checked ? radio.value : selectedSource;
      clearError();
      updateIdentityState();
      if (selectedSource) {
        document.querySelector("#display-name").focus();
      }
    });
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (inFlight || !selectedSource) {
      return;
    }
    if (!form.reportValidity()) {
      return;
    }

    inFlight = true;
    clearError();
    idempotencyKey ||= newIdempotencyKey();
    pendingPayload ||= {
      display_name: document.querySelector("#display-name").value,
      customer_label: document.querySelector("#customer-label").value,
      use_case: document.querySelector("#use-case").value,
      owner: document.querySelector("#owner").value,
      first_source_choice: selectedSource,
      idempotency_key: idempotencyKey,
    };
    updateIdentityState();

    let responseStatus = null;
    try {
      const response = await fetch(CREATE_API, {
        method: "POST",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
        },
        body: JSON.stringify(pendingPayload),
      });
      responseStatus = response.status;
      const result = await response.json().catch(() => null);
      if (!response.ok) {
        throw new Error("Draft request rejected.");
      }
      if (!isTrustedDraftResponse(result)) {
        throw new TypeError("Untrusted draft response.");
      }
      renderCreated(result);
      pendingPayload = null;
      idempotencyKey = null;
    } catch {
      if (
        responseStatus !== null &&
        responseStatus >= 400 &&
        responseStatus < 500
      ) {
        pendingPayload = null;
        idempotencyKey = null;
      }
      showError(safeFailureCopy(responseStatus));
    } finally {
      inFlight = false;
      if (!form.hidden) {
        updateIdentityState();
      }
    }
  });

  updateIdentityState();
})();
