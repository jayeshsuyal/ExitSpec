# Demo Plan

## Demo promise

Show one complete, honest chain:

```text
customer words -> exact agreement -> frozen version
-> evidence -> deterministic verdict -> human handoff
```

The viewer should understand the product from the screen without trusting the
narrator. The demo proves one exact tool-selection criterion; it does not imply
live provider execution, arbitrary metric support, or production authorization.

## Recording setup and reset

Start the loopback product:

```bash
exitspec serve
```

Open the exact recording URL:

```text
http://127.0.0.1:8765/app?mode=recording
```

Before every take, click **Restart** in the recording banner. This restores the
bundled synthetic source, two prepared candidates, contract version `0.1.0`,
Reference A selection, closed drawers, and no customer decision, freeze, or proof.
Do not continue a take from a previous browser state.

## A. Reliable 75-second primary PASS recording

This is the default public demo.

| Time | Screen action | Narration |
| --- | --- | --- |
| 0–7s | Show the workbench and six-step custody rail. | “POC promises usually live in calls and slides. ExitSpec turns one into an agreed test and an inspectable decision.” |
| 7–17s | On the prepared exact tool-selection candidate, click **Matches intent**. | “The measurable claim keeps its customer source, 95% threshold, 200-case minimum, and Wilson rule.” |
| 17–23s | On the vague inspection request, click **Keep as context**. | “Useful context does not become a fake executable metric.” |
| 23–31s | Click **Create customer review**, then **Open customer review**. | “The customer sees the exact version and every term included in its fingerprint.” |
| 31–42s | On the customer page, scan the rule and boundaries, check the acknowledgement, and click **Confirm requirements**. | “Confirmation requires explicit acknowledgement. It records agreement, not evidence or production approval.” |
| 42–49s | Click **Return to the local POC owner**. Let the pending-only poll reconcile, then click **Freeze confirmed contract**. | “The confirmed version is now immutable and receives its canonical hash.” |
| 49–59s | Keep **Reference set A** selected and click **Run this POC**. | “The deterministic adapter runs the approved fixed fixture against the frozen rule.” |
| 59–68s | Point to the on-screen result. | “Required ≥ 95.00% · Observed 197/200 (98.50%) · Wilson lower bound 95.68% · PASS.” |
| 68–75s | Click **Open evidence pack** and show its first viewport. | “The pack gives the proof, hash, limitation, next action, and raw artifacts. PASS is evidence—not authorization.” |

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

Leave the seven audit sections collapsed during the 75-second take. Open one only
if the interviewer asks for source, calculation, sufficiency, or limitation
detail.

## B. Optional ~95-second extended revision recording

Use this only when the audience needs to see that customer feedback creates a new
version rather than mutating the original agreement.

1. **0–18s — Internal review.** Approve the prepared measurable rule with
   **Matches intent**, keep the vague request as context, create the customer
   review, and open it.
2. **18–29s — Customer requests a nomenclature change.** Expand **Need something
   changed?** and enter:

   > Rename the rule to “Customer-confirmed exact support routing” and label the
   > workload “support-tool-selection-reference-a.” Keep the 95% threshold and
   > the same approved 200-case fixture.

   Click **Request changes**. Do not check the confirmation acknowledgement for a
   change request.
3. **29–39s — Return and version.** Click **Return to the local POC owner**. The
   workbench reconciles the terminal change request; click **Start revision**.
   State that the next approved agreement becomes a new version. Its parent
   reference is inspectable later in `contract.json`.
4. **39–54s — Apply the structured revision.** Set:

   - rule title: `Customer-confirmed exact support routing`
   - threshold: `95.00`
   - minimum samples: `200`
   - workload label: `support-tool-selection-reference-a`

   This is a label-only change: the approved 200-case fixture and its hash remain
   the same. Click **Apply revision**, inspect the generated sentence, then click
   **Matches intent**.
5. **54–65s — Issue the replacement agreement.** Create and open the new customer
   review. Show that the new title, workload label, threshold, sample count, and
   version are all visible and fingerprint-bound.
6. **65–75s — Confirm explicitly.** Check the acknowledgement and click
   **Confirm requirements**, then return to the owner workbench.
7. **75–83s — Freeze.** Click **Freeze confirmed contract**. The prior link and
   prior decision cannot authorize this version.
8. **83–90s — Prove.** Keep **Reference set A** selected and click **Run this
   POC**.
9. **90–95s — Handoff.** Show the legitimate final equation and open the compact
   evidence pack:

   ```text
   Required ≥ 95.00% · Observed 197/200 (98.50%)
   · Wilson lower bound 95.68% · PASS
   ```

Do not change the minimum to 250 and then show a 200-case `PASS`. Any script that
does so contradicts the frozen rule and is rejected as a valid recording.

## Acceptance gates before recording

### State and authority

- The take begins at `/app?mode=recording` after **Restart**.
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
- The pack first viewport contains the verdict, reason, equation, contract hash,
  limitation, next action, six artifact links, and “Evidence is not
  authorization.”
- All seven detail records are collapsed initially, and all six artifact links
  resolve.

### Reliability and honesty

- The recording path makes no provider or external network call.
- No Fireworks, live endpoint, speech-to-text, authenticated identity, durable
  signature, or production authorization is implied.
- A backup local pack may be prepared for event reliability, but it must come
  from the same frozen deterministic sample.
