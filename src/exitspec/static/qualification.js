(() => {
  "use strict";

  const screens = Array.from(document.querySelectorAll("[data-screen]"));
  const steps = Array.from(document.querySelectorAll("[data-step]"));
  let current = 1;

  function showScreen(number) {
    current = number;
    screens.forEach((screen) => {
      const visible = Number(screen.dataset.screen) === current;
      screen.hidden = !visible;
      screen.classList.toggle("is-visible", visible);
    });
    steps.forEach((step) => {
      const active = Number(step.dataset.step) === current;
      step.classList.toggle("is-active", active);
      step.setAttribute("aria-current", active ? "step" : "false");
    });
    document.querySelector(`[data-screen="${current}"] h2`)?.focus?.();
  }

  document.querySelectorAll("[data-next]").forEach((button) => {
    button.addEventListener("click", () => showScreen(Number(button.dataset.next)));
  });

  document.getElementById("mutate-button")?.addEventListener("click", (event) => {
    const card = document.getElementById("currency-card");
    const state = document.getElementById("currency-state");
    const title = document.getElementById("currency-title");
    const detail = document.getElementById("currency-detail");
    const status = document.getElementById("mutation-status");
    const boundaryTitle = document.getElementById("final-boundary-title");
    const boundaryCopy = document.getElementById("final-boundary-copy");
    if (!(event.currentTarget instanceof HTMLButtonElement) ||
        !(card instanceof HTMLElement) || !(state instanceof HTMLElement) ||
        !(title instanceof HTMLElement) || !(detail instanceof HTMLElement) ||
        !(status instanceof HTMLElement) || !(boundaryTitle instanceof HTMLElement) ||
        !(boundaryCopy instanceof HTMLElement)) {
      return;
    }
    card.classList.add("is-stale");
    state.textContent = "STALE";
    title.textContent = "Requalification is required";
    detail.textContent = "Subject digest changed after engine version mutation; the old receipt no longer covers this exact target.";
    status.textContent = "Synthetic mutation applied · immutable receipt preserved.";
    boundaryTitle.textContent = "Required next step: requalify";
    boundaryCopy.textContent = "STALE is a status, not a deployment block or approval. An external human decision remains required.";
    event.currentTarget.textContent = "Mutation shown · receipt remains unchanged";
    event.currentTarget.disabled = true;
  });
})();
