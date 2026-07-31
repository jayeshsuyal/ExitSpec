# ExitSpec three-minute product demo

## The one-sentence story

ExitSpec turns a customer request into an exact agreement, proves only that
agreement, and packages the evidence without silently granting production
authorization.

## Before the take

1. Start the local server and set the browser to 1280×720 at 100% zoom.
2. Open `http://127.0.0.1:8765/app`.
3. Choose **Guided demo**. This resets only the seeded support-agent POC and
   opens its 75-second guided workbench.
4. Keep Fireworks disabled. The core demonstration is deterministic and does
   not need a funded provider account.

## Exact script

| Time | Show and do | What to say |
| --- | --- | --- |
| 0:00–0:20 | On **POCs**, point to **Continue working**, the five-step journey, and **Guided demo**. Choose **Guided demo**. | “A solutions engineer sees every POC and its next required decision—not a wall of project data.” |
| 0:20–0:45 | Review requirement 1 with **Matches intent**. Keep requirement 2 as context. | “Customer language becomes a proposed measurable rule, but a human decides what belongs in the agreement.” |
| 0:45–1:05 | Choose **Create customer review**, then **Open customer review**. | “ExitSpec creates a customer-readable draft immediately. This draft is an agreement, not evidence.” |
| 1:05–1:25 | Check the exact-draft confirmation and choose **Confirm requirements**. Return to the owner view. | “Confirmation is bound to the visible version. It does not run a test or create a PASS.” |
| 1:25–1:45 | Choose **Freeze confirmed contract**. | “The confirmed contract is now immutable. Future proof must refer to this exact hash.” |
| 1:45–2:10 | Keep **Reference set A** selected and choose **Run this POC**. | “A deterministic adapter measures 200 fixed cases against the frozen criterion.” |
| 2:10–2:35 | Read the observed result and Wilson lower bound, then choose **Open evidence pack**. | “197 of 200 is 98.5%. The 95% Wilson lower bound is 95.68%, so the frozen 95% criterion passes with statistical sufficiency.” |
| 2:35–2:50 | Point to the Evidence Pack boundary, then return to the owner view. | “The pack is customer-facing and inspectable. PASS applies only to this contract and is not production authorization.” |
| 2:50–3:00 | Record the handoff as completed and show the POC under **Completed**. | “The loop ends with a named human handoff—not an endless AI workflow.” |

## Optional real-input proof

After the main take, choose **New POC** → **Email**, then paste:

```text
P95 time to first token must remain below 500 ms at concurrency 4.
Error rate must remain below 1%.
```

ExitSpec redacts recognized sensitive values before storage and creates
review-only proposals. Pasted email has no approval, confirmation, freeze,
measurement, or verdict authority.

## Non-negotiable claims

- Say **deterministic reference measurement**, not live production benchmark.
- Say **pasted email text**, not Gmail, Outlook, or mailbox integration.
- Say **customer-confirmed contract**, not autonomous approval.
- Say **PASS for this frozen criterion**, not permission to deploy.
- If the optional provider action is unavailable, continue the demo; Fireworks
  is not on the critical path.
