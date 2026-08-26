# 90-second Zoom-to-POC demo runbook

This is the local synthetic rehearsal. It demonstrates the guarded handoff and the existing human-controlled POC spine; it does not connect to Zoom, does not use a provider, and is not evidence that a real Zoom meeting has completed the product loop.

## Before recording

- Start the local ExitSpec app with the repository’s normal test/demo command.
- Use a clean temporary output directory. Do not open or copy `.zoom-fixture-private/`.
- Keep the browser at 1280×720 and use one employee window plus one customer-review window.
- Have the deterministic local reference evaluator selected; do not enable live provider execution.

## Timing script

| Time | Action | Proof point |
| --- | --- | --- |
| 0:00–0:10 | Open `/app`, create a POC with `Meeting` as the starting source, and choose `Zoom RTMS handoff`. | The source boundary says the input is untrusted and review is required. |
| 0:10–0:25 | Read the disclosure, check the local synthetic authorization, click `Start listening`, then `Stop meeting`. | `Listening → Processing`; no transcript text appears in the browser. |
| 0:25–0:35 | Let the bounded local process finish and click `Open draft`. | `Draft ready`; exactly two source-backed proposals are shown for review. |
| 0:35–0:50 | As the employee, keep the two measurable requirements, discard or flag anything unsupported, and save the supported metric definitions. | Requirements remain editable and explicitly human-reviewed. |
| 0:50–1:05 | Select the deterministic local target and create the customer review. Open the separate customer link. | The customer sees the exact version, counting policy, and NOT_PROVEN boundary. |
| 1:05–1:15 | Check the agreement acknowledgement and confirm as the synthetic customer. Return to the employee window and freeze the exact confirmed version. | Freeze is unavailable until customer confirmation. |
| 1:15–1:28 | Acknowledge execution and run the supported deterministic criterion. | One terminal `PASS` and a linked Evidence Pack appear. |
| 1:28–1:30 | Open the Evidence Pack and point out the bound contract, counts, and provenance. | Evidence is inspectable and bound to the frozen agreement. |

## Narration guardrails

Say “local synthetic Zoom handoff” and “deterministic reference evaluator.” Do not say “live Zoom integration is complete,” “the model accepted the proposal,” or “the meeting transcript was published.” The model/provider boundary is draft-only; confirmation, freeze, proof, and verdict are human or deterministic application actions.
