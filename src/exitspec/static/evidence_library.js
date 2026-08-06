(() => {
  "use strict";

  const API_PATH = "/api/evidence-packs";
  const SCHEMA_VERSION = "exitspec.evidence-pack-library.v1";
  const AUTHORIZATION = "Evidence is proof, not shipping authorization.";
  const POC_ID_PATTERN = /^poc_[a-z0-9][a-z0-9_-]{2,63}$/;
  const SHA256_PATTERN = /^[a-f0-9]{64}$/;
  const ROOT_KEYS = Object.freeze([
    "authorization",
    "packs",
    "schema_version",
  ]);
  const PACK_KEYS = Object.freeze([
    "contract_hash",
    "contract_id",
    "contract_version",
    "customer_label",
    "display_name",
    "evidence_pack_sha256",
    "evidence_pack_url",
    "handoff_state",
    "poc_id",
    "run_id",
    "updated_at",
    "verdict",
  ]);
  const VERDICTS = Object.freeze([
    "PASS",
    "FAIL",
    "BLOCKED",
    "NOT_PROVEN",
  ]);
  const HANDOFF_STATES = Object.freeze([
    "READY_FOR_HANDOFF",
    "HANDOFF_COMPLETED",
    "POC_STOPPED",
    "REVIEW_REQUIRED",
    "HISTORICAL",
  ]);

  const list = document.querySelector("#evidence-pack-list");
  const empty = document.querySelector("#evidence-empty");
  const count = document.querySelector("#pack-count");
  const summary = document.querySelector("#evidence-list-summary");
  const authorization = document.querySelector("#authorization-boundary");
  const errorPanel = document.querySelector("#evidence-library-error");

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

  function isSafeText(value, maximum) {
    return Boolean(
      typeof value === "string" &&
        value.length > 0 &&
        value.length <= maximum &&
        value === value.trim() &&
        !/[\u0000-\u001f\u007f]/.test(value)
    );
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

  function safeEvidenceUrl(value) {
    if (
      typeof value !== "string" ||
      !value.startsWith("/artifacts/") ||
      !value.endsWith("/decision-packet.html") ||
      value.includes("\\") ||
      value.includes("?") ||
      value.includes("#")
    ) {
      return null;
    }
    try {
      const parsed = new URL(value, window.location.origin);
      const parts = parsed.pathname.split("/").slice(2);
      return parsed.origin === window.location.origin &&
        parsed.pathname === value &&
        parsed.search === "" &&
        parsed.hash === "" &&
        parts.every((part) => part && part !== "." && part !== "..")
        ? value
        : null;
    } catch {
      return null;
    }
  }

  function isTrustedPack(pack) {
    return Boolean(
      hasExactKeys(pack, PACK_KEYS) &&
        POC_ID_PATTERN.test(pack.poc_id) &&
        isSafeText(pack.display_name, 160) &&
        isSafeText(pack.customer_label, 160) &&
        isSafeText(pack.contract_id, 160) &&
        isSafeText(pack.contract_version, 100) &&
        SHA256_PATTERN.test(pack.contract_hash) &&
        isSafeText(pack.run_id, 200) &&
        VERDICTS.includes(pack.verdict) &&
        safeEvidenceUrl(pack.evidence_pack_url) !== null &&
        SHA256_PATTERN.test(pack.evidence_pack_sha256) &&
        HANDOFF_STATES.includes(pack.handoff_state) &&
        isTrustedTimestamp(pack.updated_at)
    );
  }

  function isTrustedProjection(payload) {
    if (
      !hasExactKeys(payload, ROOT_KEYS) ||
      payload.schema_version !== SCHEMA_VERSION ||
      payload.authorization !== AUTHORIZATION ||
      !Array.isArray(payload.packs) ||
      payload.packs.length > 2048 ||
      !payload.packs.every(isTrustedPack)
    ) {
      return false;
    }
    const identities = payload.packs.map(
      (pack) => `${pack.poc_id}\u0000${pack.run_id}\u0000${pack.evidence_pack_url}`
    );
    if (new Set(identities).size !== identities.length) {
      return false;
    }
    return payload.packs.every(
      (pack, index) =>
        index === 0 ||
        Date.parse(payload.packs[index - 1].updated_at) >=
          Date.parse(pack.updated_at)
    );
  }

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

  function shortIdentity(value) {
    return value.length > 18 ? `${value.slice(0, 15)}…` : value;
  }

  function handoffLabel(value) {
    return {
      READY_FOR_HANDOFF: "Ready for handoff",
      HANDOFF_COMPLETED: "Handoff completed",
      POC_STOPPED: "POC stopped",
      REVIEW_REQUIRED: "Review required",
      HISTORICAL: "Historical run",
    }[value];
  }

  function renderPack(pack) {
    const item = element("li", "evidence-pack-item");
    const row = element("article", "evidence-pack-row");

    const identity = element("div", "pack-identity");
    identity.append(
      element("strong", "", pack.display_name),
      element("span", "", pack.customer_label)
    );

    const contract = element("div", "pack-contract");
    const contractLabel = element(
      "strong",
      "",
      `${pack.contract_id} · v${pack.contract_version}`
    );
    contractLabel.title = `${pack.contract_id} · version ${pack.contract_version}`;
    const runLabel = element("span", "", shortIdentity(pack.run_id));
    runLabel.title = pack.run_id;
    contract.append(contractLabel, runLabel);

    const verdict = element("span", "pack-verdict", pack.verdict.replace("_", " "));
    verdict.dataset.verdict = pack.verdict;
    const handoff = element(
      "span",
      "pack-handoff",
      handoffLabel(pack.handoff_state)
    );
    const link = element("a", "open-pack-link", "Open pack");
    link.href = safeEvidenceUrl(pack.evidence_pack_url);
    link.setAttribute(
      "aria-label",
      `Open ${pack.verdict.replace("_", " ")} Evidence Pack for ${pack.display_name}`
    );

    row.append(identity, contract, verdict, handoff, link);
    item.append(row);
    return item;
  }

  function renderProjection(payload) {
    list.replaceChildren();
    list.setAttribute("aria-busy", "false");
    empty.hidden = payload.packs.length !== 0;
    count.textContent = String(payload.packs.length);
    authorization.textContent = payload.authorization;
    const noun = payload.packs.length === 1 ? "pack" : "packs";
    summary.textContent = `${payload.packs.length} verified ${noun}`;
    payload.packs.forEach((pack) => list.append(renderPack(pack)));
  }

  function renderUnavailable() {
    list.replaceChildren(
      element(
        "li",
        "evidence-loading",
        "Evidence Packs are unavailable. No artifact link was released."
      )
    );
    list.setAttribute("aria-busy", "false");
    empty.hidden = true;
    count.textContent = "—";
    authorization.textContent = AUTHORIZATION;
    summary.textContent = "Library unavailable";
    errorPanel.textContent =
      "The Evidence Pack library could not be validated. Reload after the workspace recovers.";
    errorPanel.hidden = false;
  }

  async function initialise() {
    try {
      const response = await fetch(API_PATH, {
        cache: "no-store",
        credentials: "same-origin",
        redirect: "error",
        headers: { Accept: "application/json" },
      });
      const responseUrl = new URL(response.url, window.location.origin);
      if (
        !response.ok ||
        responseUrl.origin !== window.location.origin ||
        responseUrl.pathname !== API_PATH ||
        responseUrl.search !== "" ||
        responseUrl.hash !== ""
      ) {
        throw new Error("Evidence Pack library request failed.");
      }
      const payload = await response.json();
      if (!isTrustedProjection(payload)) {
        throw new Error("Evidence Pack library response is invalid.");
      }
      renderProjection(payload);
    } catch {
      renderUnavailable();
    }
  }

  initialise();
})();
