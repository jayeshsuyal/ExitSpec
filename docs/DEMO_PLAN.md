# Demo Plan

## Demo promise

Show one complete, honest chain:

```text
manifest-approved synthetic email -> redacted source-linked proposals
-> named employee decisions -> exact customer agreement -> frozen version
-> evidence -> deterministic verdict -> human handoff
```

The viewer should understand the product from the screen without trusting the
narrator. The demo proves one exact tool-selection criterion and a powerless
untrusted-source boundary; it does not imply live email, live provider
execution, arbitrary metric support, or production authorization.

## Recording setup and reset

Start a clean loopback process:

```bash
exitspec serve
```

Open the guided entry:

```text
http://127.0.0.1:8765/app?intake=email
```

Use a newly started process for the primary take. If rehearsing in the same
process, expand **Source details** and select **Reset to choose another** before
starting. Do not continue from a prior customer decision, freeze, or proof.

The older prepared-notes take remains available at `/app?mode=recording`, but
the public Wave 2 story uses the email entry above.

## A. Reliable ~90-second primary PASS recording

This is the default public demo. The operator version with expected copy and
fallbacks is the
[Wave 2 email demo runbook](WAVE2_EMAIL_DEMO_RUNBOOK.md).

| Time | Screen action | Narration |
| --- | --- | --- |
| 0–8s | Show **Start from a sample email** and the two-item **Sample email** list. | “ExitSpec begins with a bounded customer-shaped source, not a blank prompt.” |
| 8–18s | Choose **Support-agent requirements** and click **Import sample email**. | “The synthetic email is normalized and redacted before it becomes review-only proposals.” |
| 18–30s | On the 95%/200 proposal, click **Matches intent**. | “A named employee—not the email—accepts the measurable rule.” |
| 30–38s | On the latency proposal, click **Keep as context**. | “There is no latency adapter, so useful context does not become fake executable success criteria.” |
| 38–47s | Click **Create customer review**, then **Open customer review**. | “The customer sees the exact version and every term included in its fingerprint.” |
| 47–61s | Scan the requirement, check the acknowledgement, and click **Confirm requirements**. | “Confirmation is explicit and version-bound. It creates no evidence and no production approval.” |
| 61–69s | Click **Return to the local POC owner** or return to the original app tab. | “The employee workbench reconciles the customer’s terminal decision.” |
| 69–76s | Click **Freeze confirmed contract**. | “Only the customer-confirmed version can become the immutable measurement input.” |
| 76–83s | Keep **Reference set A** selected and click **Run this POC**. | “The deterministic adapter runs the approved 200 fixed cases.” |
| 83–88s | Point to the result. | “197 of 200 is 98.50%; its Wilson lower bound is 95.68%, which is still at least the frozen 95% threshold, so this exact criterion passes.” |
| 88–90s | Click **Open evidence pack**. | “The customer-facing pack separates proof from authority: PASS does not deploy anything.” |

The exact primary equation is:

```text
Required ≥ 95.00% · Observed 197/200 (98.50%)
· Wilson lower bound 95.68% · PASS
```

In the compact pack, point to these six relative artifact links:

1. `contract.json`
2. `evidence-artifacts.json`
3. `calculations.json`
4. `verdicts.json`
5. `run-manifest.json`
6. `artifact-hashes.json`

Leave the seven audit sections collapsed during the 90-second take. Open one only
if the interviewer asks for source, calculation, sufficiency, or limitation
detail.

## B. Optional extended revision recording

Use this only when the audience needs to see that customer feedback creates a new
version rather than mutating the original agreement.

1. **Import and internal review.** Import **Support-agent requirements**,
   approve the measurable rule with **Matches intent**, keep the latency request
   as context, create the customer review, and open it.
2. **Customer requests a nomenclature change.** Expand **Need something
   changed?** and enter:

   > Rename the rule to “Customer-confirmed exact support routing” and label the
   > workload “support-tool-selection-reference-a.” Keep the 95% threshold and
   > the same approved 200-case fixture.

   Click **Request changes**. Do not check the confirmation acknowledgement for a
   change request.
3. **Return and version.** Click **Return to the local POC owner**. The
   workbench reconciles the terminal change request; click **Start revision**.
   State that the next approved agreement becomes a new version. Its parent
   reference is inspectable later in `contract.json`.
4. **Apply the structured revision.** Set:

   - rule title: `Customer-confirmed exact support routing`
   - threshold: `95.00`
   - minimum samples: `200`
   - workload label: `support-tool-selection-reference-a`

   This is a label-only change: the approved 200-case fixture and its hash remain
   the same. Click **Apply revision**, inspect the generated sentence, then click
   **Matches intent**.
5. **Issue the replacement agreement.** Create and open the new customer
   review. Show that the new title, workload label, threshold, sample count, and
   version are all visible and fingerprint-bound.
6. **Confirm explicitly.** Check the acknowledgement and click
   **Confirm requirements**, then return to the owner workbench.
7. **Freeze.** Click **Freeze confirmed contract**. The prior link and
   prior decision cannot authorize this version.
8. **Prove.** Keep **Reference set A** selected and click **Run this
   POC**.
9. **Handoff.** Show the legitimate final equation and open the compact
   evidence pack:

   ```text
   Required ≥ 95.00% · Observed 197/200 (98.50%)
   · Wilson lower bound 95.68% · PASS
   ```

Do not change the minimum to 250 and then show a 200-case `PASS`. Any script that
does so contradicts the frozen rule and is rejected as a valid recording.

## Acceptance gates before recording

### State and authority

- The take begins at `/app?intake=email` on a clean server or after an explicit
  workflow reset.
- The picker exposes only **Support-agent requirements** and
  **Untrusted-instructions test**.
- Import publishes source-linked `NEEDS_REVIEW` proposals only; email creates no
  employee decision or downstream authority.
- The browser shows one current task and one primary action.
- Pasted synthetic notes, when used, begin unresolved and require human
  structured input.
- The customer review displays every canonical fingerprint-bound term.
- `CONFIRM` cannot succeed until the acknowledgement checkbox is checked; direct
  API submission with false or missing acknowledgement is also rejected.
- A requested change creates a new version with a parent reference and a new
  review capability.
- Freeze is unavailable until the exact current version is confirmed.

### Proof and handoff

- Reference A renders exactly:

  ```text
  Required ≥ 95.00% · Observed 197/200 (98.50%)
  · Wilson lower bound 95.68% · PASS
  ```

- Reference B gives a precise `NOT_PROVEN` reason and Reference C gives a precise
  `BLOCKED` reason.
- **Run another reference set** reruns the same frozen contract and replaces the
  current proof only after a valid run.
- At 1280×720, the workbench avoids workflow-length body scrolling.
- Narrower and zoomed layouts reflow with bounded panel scrolling.
- The pack first viewport contains the verdict, reason, equation, contract hash,
  limitation, next action, six artifact links, and “Evidence is not
  authorization.”
- All seven detail records are collapsed initially, and all six artifact links
  resolve.

### Reliability and honesty

- The recording path makes no provider or external network call.
- Fireworks remains optional and disabled by default; no funded real-account
  success is claimed.
- No live mailbox, real customer email, arbitrary upload, live endpoint,
  speech-to-text, authenticated identity, durable signature, multi-tenancy, or
  production authorization is implied.
- A backup local pack may be prepared for event reliability, but it must come
  from the same frozen deterministic sample.

Manual real-browser observations of six representative task states are
acceptance evidence for those states, not exhaustive every-state coverage or CI
browser automation. The frozen contract still requires its no-scroll oracle at
every guided step. The separate machine-readable implementation record is
`examples/support-agent/evidence/wave-2-implementation-evidence-v1.json`; the
frozen Wave 2 contracts remain unchanged, including the source-web contract's
historical pre-implementation fields.
