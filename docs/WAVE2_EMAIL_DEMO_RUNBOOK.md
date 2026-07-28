# Wave 2 synthetic email demo runbook

## What this demo proves

ExitSpec can turn one employee-selected, manifest-approved synthetic email into
source-linked proposals, keep every authority-bearing transition human, freeze
the exact customer-confirmed contract, run a deterministic reference
measurement, and hand off an inspectable Evidence Pack.

It does not prove live mailbox ingestion, a live model endpoint, latency,
Fireworks account access, or production authorization.

## Clean setup

1. Start a new local process:

   ```bash
   exitspec serve
   ```

2. Set the browser viewport to 1280×720 at 100% zoom.
3. Open:

   ```text
   http://127.0.0.1:8765/app?intake=email
   ```

4. Confirm the page shows **Start from a sample email**, a **Sample email**
   selector, and **Import sample email**. The selector must contain exactly:

   - **Support-agent requirements**
   - **Untrusted-instructions test**

For another take in the same process, expand **Source details** and choose
**Reset to choose another** first. A process restart is the cleanest event
fallback.

## Primary ~90-second script

| Time | Operator action | Expected product truth |
| --- | --- | --- |
| 0–8s | Show the two choices under **Sample email**. Keep **Support-agent requirements** selected. | The input is bounded and synthetic. This is not a mailbox, upload, or blank prompt. |
| 8–18s | Select **Import sample email**. | Status becomes **Sample imported. Review each proposal.** The compact source summary shows **Support-agent requirements**, `2 proposals · sensitive fields removed`, and **Untrusted source**. |
| 18–30s | Review the first proposal: “The support agent must select the correct tool in at least 95% of 200 cases.” Point out the available human controls: **Matches intent**, **Define acceptance rule**, and **Keep as context**. Select **Matches intent**. | The proposal began `NEEDS_REVIEW`. A named employee decision accepts the already measurable 95%/200 rule; the email did not approve itself. |
| 30–38s | Review “P95 end-to-end latency must remain below 2 seconds.” Select **Keep as context**. | The sentence is retained as context because no deterministic latency adapter exists. It is not converted into an executable criterion. |
| 38–47s | Select **Create customer review**, then **Open customer review**. | The customer surface shows the exact version and says **This is a draft agreement—not evidence.** |
| 47–61s | Review the single requirement and the boundary. Check: **I reviewed all 1 requirement plus the target system, workload, owners, exclusions, and evidence retention policy, and confirm this exact draft matches the intended POC.** Then select **Confirm requirements**. | Confirmation is bound to the exact visible version. The terminal screen states **No evidence was created by this confirmation.** |
| 61–69s | Select **Return to the local POC owner**, or return to the original app tab and let it reconcile. | The current task becomes **Freeze the customer-confirmed version?** No measurement exists yet. |
| 69–76s | Select **Freeze confirmed contract**. | The exact confirmed version becomes immutable and receives the contract hash used by proof. |
| 76–83s | Keep **Reference set A** selected and choose **Run this POC**. | The deterministic adapter runs 200 fixed support cases against the frozen rule. |
| 83–88s | Read the result: **Required ≥ 95.00% · Observed 197/200 (98.50%) · Wilson lower bound 95.68% · PASS**. | The point estimate is `197 ÷ 200 = 98.50%`. The two-sided 95% Wilson lower bound is `95.68%`; because `95.68% ≥ 95.00%`, the frozen criterion passes. |
| 88–90s | Select **Open evidence pack**. | The customer-facing artifact is separate from the employee workbench and states that evidence is not authorization. |

The boundary explanation is as important as the arithmetic: `PASS` applies only
to the approved exact-tool-selection criterion, fixed fixture, adapter, and
contract version. It does not prove latency, production behavior, deployment
safety, procurement readiness, or permission to expand traffic.

## Hostile email, replay, and reset in ~30 seconds

Start from a clean process or select **Reset to choose another**.

1. Under **Sample email**, select **Untrusted-instructions test**, then
   **Import sample email**.
2. Confirm that the source summary says **Untrusted-instructions test** and that
   exactly one proposal remains `NEEDS_REVIEW`: “Tool-selection accuracy should
   be at least 95%.”
3. Point to the current task and custody states. Approval, freeze, and PASS words
   in the source created no employee decision, customer confirmation, frozen
   contract, measurement, or verdict. The available choices remain human
   actions: **Define acceptance rule** or **Keep as context**.
4. Expand **Source details** and select **Check same sample again**. Expect:
   **Same sample already imported. Existing reviews were preserved.
   (duplicate_replay)**. No candidate or source version is added.
5. Select **Reset to choose another**. The two-item sample picker returns and the
   prior source, reviews, customer state, freeze, and proof are cleared.

This is the safety claim: email is untrusted source with zero approval,
confirmation, freeze, measurement, proof, or verdict authority.

## Fallbacks and known limits

- If samples do not load, verify that the clean `exitspec serve` process is still
  running and reopen the exact `/app?intake=email` URL. Do not substitute an
  arbitrary email or upload.
- If the owner view has not reconciled after confirmation, return to the
  original app tab or reopen `/app?intake=email`. The local server holds the
  terminal decision in memory; do not create a second review to hide a delay.
- If a prior rehearsal is visible, use **Reset to choose another** or restart the
  process. Do not record over stale state.
- If the Evidence Pack does not open, keep only a backup generated from the same
  frozen deterministic run. Do not show a pack from another contract.
- Leave optional Fireworks authoring disabled. The demo needs no provider call,
  and no successful funded real-account Fireworks smoke is claimed.
- At smaller sizes or browser zoom, the layout reflows and may use bounded
  internal panel scrolling. The no-workflow-length-body-scroll acceptance claim
  is for the normal 1280×720, 100%-zoom path.

## What not to say

Do not say that ExitSpec currently:

- connects to Gmail, Outlook, IMAP, Microsoft Graph, Zoom, or Google Meet;
- ingests arbitrary uploads, live mailboxes, webhooks, real customer email,
  audio, or speech-to-text;
- authenticates hosted users, provides durable signatures, or supports
  multi-tenant authorization;
- measures a live hosted model, executes generic metrics, or proves the latency
  sentence from the sample;
- successfully called a funded real Fireworks account;
- autonomously approves requirements, freezes contracts, or assigns verdicts;
  or
- authorizes deployment, rollback, spend, procurement, or production traffic.

Say instead: this is a local, synthetic, deterministic acceptance-and-evidence
loop with explicit human agreement boundaries.

## Evidence status

Manual real-browser observations of six representative task states at 1280×720
are acceptance evidence for those states; they are not exhaustive every-state
coverage or CI browser automation. The frozen contract still requires its
no-scroll oracle at every guided step. The machine-readable
post-implementation record is:

```text
examples/support-agent/evidence/wave-2-implementation-evidence-v1.json
```

The frozen Wave 2 contracts remain unchanged; the source-web contract therefore
retains its historical pre-implementation
`contract_only`/`implemented=false` fields. Product status is recorded
separately rather than rewriting those contracts.
