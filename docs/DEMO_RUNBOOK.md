# ExitSpec three-minute product demo

## The one-sentence story

ExitSpec turns a customer request into an exact agreement, proves only that
agreement, and packages the evidence without silently granting production
authorization.

## Before the take

1. Verify the exact recording revision once with
   `./scripts/v0_2_release_gate.sh`.
2. Start the local server and set the browser to 1280×720 at 100% zoom.
3. Open `http://127.0.0.1:8765/app`.
4. Choose **Guided demo**. This resets only the seeded support-agent POC and
   opens its 75-second guided workbench.
5. Keep Fireworks disabled. The core demonstration is deterministic and does
   not need a funded provider account.

## Exact script

| Time | Show and do | What to say |
| --- | --- | --- |
| 0:00–0:20 | On **POCs**, point to **Continue working**, the three-step journey, and **Guided demo**. Choose **Guided demo**. | “A solutions engineer sees every POC and its next required decision—not a wall of project data.” |
| 0:20–0:45 | Review requirement 1 with **Matches intent**. Keep requirement 2 as context. | “Customer language becomes a proposed measurable rule, but a human decides what belongs in the agreement.” |
| 0:45–1:05 | Choose **Create customer review**, then **Open customer review**. | “ExitSpec creates a customer-readable draft immediately. This draft is an agreement, not evidence.” |
| 1:05–1:25 | Check the exact-draft confirmation and choose **Confirm requirements**. Return to the owner view. | “Confirmation is bound to the visible version. It does not run a test or create a PASS.” |
| 1:25–1:45 | Choose **Freeze confirmed contract**. | “The confirmed contract is now immutable. Future proof must refer to this exact hash.” |
| 1:45–2:10 | Keep **Reference set A** selected and choose **Run this POC**. | “A deterministic adapter measures 200 fixed cases against the frozen criterion.” |
| 2:10–2:35 | Read the observed result and Wilson lower bound, then choose **Open evidence pack**. | “197 of 200 is 98.5%. The 95% Wilson lower bound is 95.68%, so the frozen 95% criterion passes with statistical sufficiency.” |
| 2:35–2:50 | Point to the Evidence Pack boundary, then return to the owner view. | “The pack is customer-facing and inspectable. PASS applies only to this contract and is not production authorization.” |
| 2:50–3:00 | Record the handoff as completed and show the POC under **Completed**. | “The loop ends with a named human handoff—not an endless AI workflow.” |

## Dynamic email-to-performance proof

Choose **New POC** → **Email**, then paste:

```text
P95 time to first token must remain below 500 ms at concurrency 4.
Error rate must remain below 1%.
Answer quality must feel delightful.
```

ExitSpec redacts recognized sensitive values before storage and creates
review-only proposals. Pasted email has no approval, confirmation, freeze,
measurement, or verdict authority.

1. Keep the TTFT and error-rate proposals for the contract.
2. Discard the quality proposal. State that it remains explicitly
   `NOT_PROVEN`; ExitSpec does not invent a quality grader.
3. Define TTFT as `< 500 ms`, error rate as `< 1%`, 100 samples, and
   concurrency 4.
4. On the agreement screen choose **Use local reference target**.
5. Enter the employee reviewer and rationale, create the customer draft, record
   the customer confirmation, and choose **Freeze confirmed contract**.
6. Continue to proof, acknowledge the run, and run the POC.
7. Open the Evidence Pack. Show the measured TTFT and error-rate facts, then
   show the excluded quality claim under `NOT_PROVEN` limits.
8. Return to ExitSpec, record the Evidence Pack handoff, and show the POC under
   **Completed**.

The local reference target performs one preflight, ten warmups, and 100
measured streamed requests through the real performance probe. It proves the
ExitSpec product loop, not vLLM, GPU, provider, or production performance.

## Optional sealed Inferdrome evidence handoff

This optional extension demonstrates that ExitSpec can judge evidence produced
outside its own measurement runtime without trusting the producer's verdict.
Use only an already-produced, synthetic, non-sensitive,
`CUSTOMER_ELIGIBLE` bundle. Do not manufacture or edit a bundle for the take.

Start ExitSpec with one immutable operator-selected runs root:

```bash
exitspec serve \
  --inferdrome-runs-root /absolute/path/to/inferdrome/runs \
  --open-browser
```

1. Create a performance POC from a synthetic email or meeting transcript and
   keep the supported TTFT and error-rate requirements.
2. Define the exact thresholds, minimum samples, concurrency, and workload.
3. On the agreement screen choose **Import from Inferdrome**, then
   **Use Inferdrome demo target**. Create the customer draft, confirm that exact
   evidence method and target, and freeze the confirmed contract.
4. From `/app`, show that the next action is **Select sealed evidence**, not
   **Run frozen proof**.
5. Select the server-discovered bundle and acknowledge the exact run ID and
   digest. Choose **Import sealed evidence**.
6. Show the immutable import receipt, independently recalculated observations,
   typed verdict, and Evidence Pack. A valid but insufficient bundle remains
   `NOT_PROVEN`; an invalid bundle is `INGESTION_REJECTED`, which is not a
   verdict.
7. Record the human handoff. State that neither the producer verdict nor the
   ExitSpec verdict authorizes shipping.

The browser receives no filesystem path and cannot replace the evidence method,
contract, verifier, customer confirmation, or verdict. ExitSpec does not run
Inferdrome or vLLM in this flow.

## Non-negotiable claims

- Say **deterministic reference measurement**, not live production benchmark.
- Say **pasted email text**, not Gmail, Outlook, or mailbox integration.
- Say **unsupported claim remains NOT_PROVEN**, not “the AI figured it out.”
- Say **customer-confirmed contract**, not autonomous approval.
- Say **PASS for this frozen criterion**, not permission to deploy.
- If the optional provider action is unavailable, continue the demo; Fireworks
  is not on the critical path.
- If no eligible Inferdrome bundle appears, skip the optional external-evidence
  extension. Do not bypass catalog eligibility or select an arbitrary path.
