# Demo Plan

## Demo objective

Show that ExitSpec constrains false confidence in an AI infrastructure POC. The
viewer should understand the product without trusting narration and should be able
to operate the complete local loop in a browser.

## Three-screen story

```text
Define -> Prove -> Decide
```

### Define

Show a synthetic discovery transcript beside source-linked criteria. Highlight the
customer quote, metric, threshold, workload, sample requirement, and ambiguity
warnings. A named field engineer explicitly approves the measurable claim and
rejects the vague one. Only then can the contract be frozen; display version and
digest.

The current local `exitspec define` command implements the review portion as a static artifact: one candidate is approved and one intentionally vague request is rejected. The generated approved contract then enters the existing freeze-and-run path.

### Prove

Run the deterministic sample. The scenario selector makes three states easy to
demonstrate: `PASS`, `NOT_PROVEN`, and `BLOCKED`. The viewer should see that
insufficient evidence does not pass and an external failure does not become a
fabricated result.

### Decide

Show the overall and per-criterion verdicts. Open the Proof Pack to show the
source, frozen rule, evidence JSONL, Wilson calculation, limitations, and the next
human action. `PASS` must be described as evidence—not automatic shipment or
authorization.

## 90-second script

1. **0–10 seconds:** “POC criteria live in calls and slides. ExitSpec turns them into an approved test contract.”
2. **10–30 seconds:** Show the source quote, approve the measurable requirement, and reject the vague request.
3. **30–52 seconds:** Run the `NOT_PROVEN` scenario and show why the POC cannot yet be called successful.
4. **52–70 seconds:** Run the `PASS` scenario using the same frozen contract.
5. **70–84 seconds:** Open the Proof Pack and point to the source, hash, evidence, and Wilson calculation.
6. **84–90 seconds:** “This proves a POC claim. A human still decides what moves next.”

## What is real versus simulated

| Element | Brick 1 | Public version |
| --- | --- | --- |
| Discovery transcript | Synthetic | Synthetic public sample; customer data only with explicit policy |
| Tool-selection fixture | Deterministic synthetic | Fixed 200-case synthetic customer-shaped fixture |
| Endpoint | Deterministic adapter | Deterministic adapter plus one hosted OpenAI-compatible endpoint |
| Load run | Simulated | Real GuideLLM-backed run |
| Statistics/verdicts | Real | Real |
| Hashes/evidence bundle | Real | Real |
| UI | Local browser demo over real Python domain logic | Hosted multi-user product later |

## Demo reliability rules

- The default demo is deterministic and resettable.
- It binds to loopback only and uses no provider credentials or network calls.
- A live-provider run is optional and clearly labelled.
- Every screen works at 1280×720.
- The app must show a source-to-evidence path in two clicks or fewer.
- The `NOT_PROVEN` state is visible in the default narrative.
- A backup static packet and recording are available before any event.
