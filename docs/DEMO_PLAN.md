# Demo Plan

## Demo objective

Show that ExitSpec constrains false confidence in an AI infrastructure POC. The viewer should understand the product without trusting narration.

## Three-screen story

```text
Define -> Prove -> Decide
```

### Define

Show a synthetic discovery transcript beside source-linked criteria. Highlight the customer quote, metric, threshold, workload, sample requirement, and ambiguity warnings. Approve and freeze the contract; display version and digest.

The current local `exitspec define` command implements the review portion as a static artifact: one candidate is approved and one intentionally vague request is rejected. The generated approved contract then enters the existing freeze-and-run path.

### Prove

Run the deterministic sample. The first result has only 100 of the approved 200 samples, so the main status is `NOT_PROVEN`. Explain the insufficiency, then execute the full sample.

### Decide

Show the overall and per-criterion verdicts. Open the exact-tool-selection criterion to show the source, frozen rule, evidence JSONL, Wilson calculation, and limitations. Download an HTML/JSON bundle.

## 90-second script

1. **0–10 seconds:** “POC criteria live in calls and slides. ExitSpec turns them into an approved test contract.”
2. **10–28 seconds:** Show source quote, normalized criterion, and freeze.
3. **28–50 seconds:** Run the initial measurement and reveal `NOT_PROVEN`.
4. **50–65 seconds:** Show the missing-sample explanation and corrected run.
5. **65–82 seconds:** Open the calculation/evidence drill-down.
6. **82–90 seconds:** Show the customer decision packet and engineering-gap link.

## What is real versus simulated

| Element | Brick 1 | Public version |
| --- | --- | --- |
| Discovery transcript | Synthetic | Synthetic public sample; customer data only with explicit policy |
| Tool-selection fixture | Deterministic synthetic | Fixed 200-case synthetic customer-shaped fixture |
| Endpoint | Deterministic adapter | Deterministic adapter plus one hosted OpenAI-compatible endpoint |
| Load run | Simulated | Real GuideLLM-backed run |
| Statistics/verdicts | Real | Real |
| Hashes/evidence bundle | Real | Real |
| UI | Static Define review artifact + static Decide packet | React implementation |

## Demo reliability rules

- The default demo is deterministic and resettable.
- A live-provider run is optional and clearly labelled.
- Every screen works at 1280×720.
- The app must show a source-to-evidence path in two clicks or fewer.
- The `NOT_PROVEN` state is visible in the default narrative.
- A backup static packet and recording are available before any event.
