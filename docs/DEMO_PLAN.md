# Demo Plan

## Demo objective

Show that ExitSpec constrains false confidence in an AI infrastructure POC. The
viewer should understand the product without trusting narration and should be able
to operate the complete local loop in a browser.

## One-workbench story

```text
Define -> Prove -> Decide
```

### Define

Show a synthetic discovery transcript beside source-linked criteria. Highlight the
customer quote, metric, threshold, workload, sample requirement, and ambiguity
warnings. A named field engineer explicitly approves the measurable claim and
rejects the vague one. Prepare the version-scoped customer review, record the
customer decision, return to the workbench, and explicitly freeze the confirmed
version. The freeze creates the digest before any proof can run.

Pasted browser notes are redacted before parsing, but browser authoring remains
provider-free. The implemented assisted-authoring service is not running behind
this screen and must not be narrated as though it is.

The confirmation is an immutable local decision record, not evidence, a
signature, or production authorization. The local demo uses an unauthenticated
synthetic identity and keeps the review link and record only in memory.

### Prove

Run the deterministic sample. The workbench offers the clean `PASS`,
`NOT_PROVEN`, and `BLOCKED` story; the engine and CLI retain the broader
deterministic failure scenarios for questions. The short recording uses one
`PASS` run so the agreement-to-evidence chain stays legible. The viewer should
still understand that insufficient evidence does not pass and an external failure
does not become a fabricated result.

### Decide

Show the overall and per-criterion verdicts. Open the POC Acceptance Evidence Pack
to show the source, frozen rule, evidence JSONL, Wilson calculation, limitations,
and the next human action. `PASS` must be described as evidence—not automatic
shipment or authorization.

## Approximately 75-second script

1. **0–8 seconds:** “POC criteria live in calls and slides. ExitSpec turns them into an agreed test and an inspectable decision.”
2. **8–23 seconds:** Show the source quote, approve the measurable requirement, and reject the vague request.
3. **23–36 seconds:** Open the customer review, show the exact source/rule/threshold/sample, and record confirmation. State that confirmation is not evidence.
4. **36–44 seconds:** Return to the workbench and explicitly freeze the confirmed version.
5. **44–56 seconds:** Run `PASS` against that exact frozen contract.
6. **56–70 seconds:** Open the POC Acceptance Evidence Pack and point to the source, contract hash, evidence, calculation, limitations, and next human action.
7. **70–75 seconds:** “ExitSpec proves or disproves the agreed POC claim. A human still decides what moves next.”

## What is real versus simulated

| Element | Current browser demo | Status beyond the demo |
| --- | --- | --- |
| Discovery source | Synthetic transcript or pasted synthetic notes | No STT and no real customer call ingestion |
| Redaction | Pasted notes are redacted before parsing; only redacted source and safe summary metadata enter browser state | Best-effort policy still requires human privacy review |
| Assisted authoring | Not invoked; prepared local candidates are shown and pasted notes become unresolved candidates | Side-effect-free composition is implemented and tested through `FireworksProvider` with fake injected transport, but is not exposed in the UI or live |
| Agreement review | Real internal approval/rejection plus exact-version customer decision and confirmation-gated freeze | Synthetic unauthenticated identity; in-memory links and records only |
| Tool-selection fixture | Fixed deterministic 200-case synthetic fixture | No live target endpoint |
| Statistics and verdicts | Real deterministic calculations | Same authority boundary applies to future adapters |
| Contract and artifact hashes | Real RFC 8785 JCS/SHA-256 contract digest and artifact hashes | Hashes are integrity references, not signatures |
| Evidence artifact | Real consistency-checked POC Acceptance Evidence Pack | Current renderer supports one frozen criterion |
| UI | Local loopback browser over Python domain logic | Hosted multi-user product is future scope |

## Demo reliability rules

- The default demo is deterministic and resettable.
- It binds to loopback only and uses no provider credentials or network calls.
- Browser intake is redaction-first and provider-free.
- Do not imply that Fireworks, another provider, or STT is live.
- Every workbench state works at 1280×720 without workflow-length body scroll.
- One current task and one primary next action remain obvious at each stage.
- `NOT_PROVEN` remains available as a deliberate scenario even though the short
  recording uses the clean `PASS` path.
- A backup static POC Acceptance Evidence Pack and recording are available before
  any event.
