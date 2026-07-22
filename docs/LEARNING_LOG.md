# Learning Log

## How to use this file

Each brick ends only when the builder can explain the mechanism, design decision, rejected alternative, intentional failure, critical code paths, customer explanation, and interview answer without notes.

## Brick 1 — Contract and verdict truth kernel

### What the system now proves

- A source-linked approved criterion can become a frozen canonical contract with a verifiable SHA-256 digest.
- A deterministic 200-case synthetic fixture can produce raw JSONL evidence, a run manifest, calculation inputs, artifact digests, machine-readable verdicts, and a static HTML decision packet.
- Fewer than the approved 200 samples results in `NOT_PROVEN`.
- 197/200 correct produces `PASS` because the two-sided 95% Wilson lower bound is approximately 95.68%.
- 196/200 correct remains `NOT_PROVEN`: its 98% point estimate is favorable, but its lower confidence bound is approximately 94.97%.
- A sufficient observed rate below 95% produces `FAIL`.
- An external block produces `BLOCKED`; an internal adapter failure produces `NOT_PROVEN` rather than a false customer-system failure.

### What it still does not prove

- Live endpoint correctness or performance.
- Transcript extraction quality.
- PII prevention in production data.
- Multi-user, deployment, or hosted-worker reliability.

### Reading assignment

Read these textbook sections in order:

1. How to use this textbook
2. Chapter 12.1 — Discovery is model identification
3. Chapter 12.2 — Proof of value versus proof of concept
4. Chapter 8.1 — Start from the decision and failure taxonomy
5. Chapter 2.1 — Name the metric before discussing speed
6. Chapter 2.4 — Percentiles, distributions, and coordinated omission
7. Chapter 4.4 — SLOs, SLIs, SLAs, and error budgets
8. Technical formula and decision sheet — The universal system-design opening

### Five questions to answer aloud

1. What decision should a POC end with?
2. What is the difference between a customer promise and an executable criterion?
3. Why is “p95 latency below 2.5 seconds” incomplete?
4. When should ExitSpec return `NOT_PROVEN`?
5. What is the difference between an SLI, SLO, and SLA?

### Key design decision

The trusted system begins at the frozen contract, not at transcript extraction. An LLM may draft a criterion, but deterministic code validates and decides.

### Rejected alternative

Hashing human-authored YAML directly. Canonical typed JSON is used because formatting changes in YAML should not create a new identity for the same contract.

### Intentional failure

Run only 100 out of the approved 200 samples. The result must be `NOT_PROVEN`, even if every observed sample is correct.

### Code paths to personally review

- `src/exitspec/models.py`
- `src/exitspec/contracts.py`
- `src/exitspec/statistics.py`
- `src/exitspec/verdicts.py`
- `src/exitspec/runner.py`

### 90-second technical explanation

ExitSpec treats a POC promise as an acceptance-test contract rather than a slide claim. In Brick 1, the customer requires at least 95% exact tool selection on a fixed 200-case fixture. The contract records the source quote, threshold, minimum sample count, Wilson confidence rule, adapter version, and evidence policy. Once approved, ExitSpec serializes that typed contract into canonical JSON and hashes it. The deterministic adapter creates case-level evidence, while a separate verdict engine applies the frozen rule. It cannot pass if the sample count is short, the fixture hash changes, evidence is invalid, or the confidence bound is inconclusive. The output is an inspectable evidence packet: manifest, raw records, calculation, verdicts, hashes, and a static decision report. The architecture keeps adapters responsible for facts and the verdict engine responsible for decisions, so a parser crash does not get misrepresented as a customer failure.

### Customer-facing explanation

Before a POC starts, ExitSpec records what success means and how it will be measured. It freezes that agreement so the goalposts cannot move later. During the test, it collects the supporting evidence and shows whether each promise passed, failed, was blocked, or still has not been proven. If the evidence is incomplete, it will not show a green checkmark. You can open every conclusion and see the agreed rule, the test data, and the calculation behind it.

### Evidence that would change the design

- A field engineer shows that customer criteria require a richer approval workflow.
- A practitioner demonstrates that one-sided rather than two-sided confidence bounds are the appropriate contractual policy.
- A hosted run reveals that the local artifact model cannot capture required metadata.

## Brick 2 — Define: discovery text to approved contract

### What the system now proves

- A synthetic discovery transcript is normalized into numbered, speaker-attributed lines.
- A source-linked draft records an exact quote and line range; ExitSpec rejects a quote that is not present in the declared transcript source.
- A proposed executable criterion must preserve that source exactly. A source-less proposal must be visibly marked `human_added` and include a rationale.
- A draft with an unresolved question cannot be approved.
- Approval and rejection are explicit, timestamped human review records; the rejected request stays visible in the authoring packet.
- Only approved drafts can assemble an approved `POCContract`, which then flows through the existing freeze, evidence, and verdict path.
- `exitspec define` creates a local packet containing the source, reviewed drafts, approved contract, static Define page, manifest, and SHA-256 artifact hashes.

### What it still does not prove

- LLM extraction quality or whether a model can reliably draft criteria from real discovery calls.
- Production authentication, role-based authorization, or multi-reviewer conflict resolution.
- PII-safe ingestion of real customer transcripts.
- An interactive application; the current Define page is a static local artifact driven by the same domain rules.

### Read next

Read only these sections before we add a live provider:

1. Chapter 12.3 — ROI and sensitivity
2. Chapter 12.4 — Demo engineering
3. Chapter 7.1 — Workflow first, agent second

### Five questions to answer aloud

1. Why is a transcript quote not yet an executable acceptance criterion?
2. Why can an LLM draft a criterion but not approve or freeze it?
3. What happens to a useful but vague customer request in ExitSpec?
4. What is the difference between approving one criterion and freezing an entire contract?
5. Why does source linkage matter if the customer later disputes the POC outcome?

### Key design decision

Keep the `CriterionDraft` lifecycle separate from the contract lifecycle. A draft can be approved or rejected without pretending that the whole POC agreement is final; only selected, explicitly approved drafts assemble a contract that can be frozen.

### Rejected alternative

Letting an extraction model create a `Criterion` directly from a transcript. That would hide ambiguity, make source drift easy, and allow a plausible-looking invented threshold to enter the proof chain.

### Intentional failure

Try to approve the request “We need to inspect why any case is wrong.” It is rejected because the inspection workflow, evidence fields, and measurable acceptance rule are missing.

### Code paths to personally review

- `src/exitspec/models.py` — `TranscriptSpan`, `CriterionDraft`, and their invariants.
- `src/exitspec/authoring.py` — review actions, contract assembly, and the Define packet.
- `src/exitspec/reporting.py` — the simple source-to-criterion page.
- `tests/test_authoring.py` — the deliberate failure cases.

### 90-second technical explanation

ExitSpec treats discovery text as untrusted authoring input, not as an agreement. Brick 2 stores a transcript as numbered speaker-attributed lines and makes every candidate criterion point to an exact quote. The proposed criterion must preserve that source, or be marked as a human-added requirement with a reason. A human reviewer must explicitly approve or reject the draft; unresolved questions prevent approval. Only the approved drafts can assemble the normal POC contract, which then follows the existing canonical-freeze and deterministic-verdict path. This preserves a clean boundary: models can help propose requirements, but they cannot silently invent what the customer agreed to or decide whether the target passed.

### Customer-facing explanation

We start by showing the words that led to each success criterion. If a request is clear enough to test, we turn it into a proposed rule for you to approve. If it is not clear enough, we keep it visible as an open item instead of pretending we know what success means. Once the approved criteria are frozen, the test and final recommendation use that exact agreement.
