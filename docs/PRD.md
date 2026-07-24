# Product Requirements Document

## Product thesis

AI infrastructure POCs often begin with customer promises distributed across transcripts, spreadsheets, scripts, dashboards, and slides. The measurement plan is rarely frozen with the promise, and the final narrative can overstate what the evidence establishes.

ExitSpec converts agreed success criteria into a versioned executable contract, runs approved measurements against a target system, and produces a traceable decision for every must-have promise.

The primary user is a field, solutions, deployment, or customer engineer who needs to run a technically honest POC and defend the final recommendation to both the customer and the product team.

## User job

> Help the customer and vendor agree on what success means before execution, then make the final go/no-go decision reproducible without trusting a manually assembled narrative.

## Differentiated seam

ExitSpec is neither a generic eval platform nor a presales management system.

- Presales products may own discovery documents, tasks, and CRM visibility.
- Eval products may own datasets, traces, graders, and experiment comparison.
- Benchmark products may own request generation and performance measurements.
- ExitSpec owns source-linked criteria, internal review, exact-version customer
  confirmation, explicit freeze, contract versioning, evidence sufficiency, typed
  verdicts, and the final decision packet.

The project does not claim that no adjacent product can add this workflow. Its value depends on implementing the complete evidence chain credibly and making it easy enough to use.

## First vertical scenario

A support-automation company is evaluating a hosted inference endpoint for a production tool-calling agent.

The eventual demonstration includes five must-have criteria:

1. p95 end-to-end latency below 2.5 seconds at 20 requests per second.
2. At least 99% JSON-schema-valid tool calls under an approved confidence rule.
3. At least 95% exact tool selection on a fixed 200-case fixture.
4. Estimated model cost below $0.02 per successfully completed ticket.
5. No PII detected in persisted evidence or the generated report under a declared detector and policy.

Brick 1 implements only criterion 3 so that the contract and verdict semantics are proven before the product generalizes.

## Product principles

### Evidence before narrative

Every displayed result must connect the source statement, normalized criterion, measurement rule, workload, run environment, evidence, calculation, verdict, and limitations.

### Missing evidence never passes

Insufficient samples, invalid workloads, missing metadata, corrupted artifacts, adapter failures, and inconclusive statistics produce `NOT_PROVEN` unless an attributable external condition makes the correct state `BLOCKED`.

### Models may propose; deterministic systems decide

An LLM may draft criteria, identify ambiguity, or explain a verified result. It cannot silently assign a terminal verdict.

### Humans approve the contract

The normalized claim, metric, threshold, workload, sample requirement, measurement method, failure semantics, and retention policy require explicit approval.
The customer confirmation is recorded separately against the exact proposed
version and content fingerprint before freeze.

### Frozen contracts are append-only

A frozen contract cannot be silently changed. Revision creates a new version with a parent reference.

### Integrity claims remain narrow

A digest can show that a recorded artifact has changed since its hash was computed. It cannot prove that the original measurement was honest or complete.

## Required verdicts

- `PASS`: sufficient valid evidence exists and the approved condition is established.
- `FAIL`: sufficient valid evidence exists and the approved condition is not met.
- `BLOCKED`: execution cannot complete because of an attributable external or environmental condition.
- `NOT_PROVEN`: the evidence cannot justify either pass or fail.

Overall must-have precedence for version one is:

```text
FAIL > BLOCKED > NOT_PROVEN > PASS
```

This precedence is provisional and must be explicitly approved before the public contract specification is frozen.

## Version-one requirements

### Contract

- Source-linked or explicitly human-added criteria.
- Strict schema validation.
- Explicit human approval.
- Separate affirmative customer confirmation bound to the exact version and
  confirmation fingerprint.
- Application-enforced immutable frozen versions.
- Canonical JSON serialization and SHA-256 digest.

### Execution

- Deterministic local mock endpoint or adapter.
- One OpenAI-compatible live provider after the local chain is solid.
- Independently testable, versioned adapters.
- Typed external, internal, and evidentiary failures.

### Evidence

- Run manifest and fixture hash.
- Raw redacted artifacts stored separately from relational metadata.
- SHA-256 digest for every published artifact.
- Calculation version and direct evidence references.

### Decision packet

- Overall and per-criterion verdicts.
- Observed result, threshold, sample count, and uncertainty.
- Source, evidence, calculation, assumptions, and limitations.
- Downloadable static HTML and machine-readable JSON.

## Three largest product risks

1. **Category convergence:** eval or presales incumbents can add contract and reporting features.
2. **Authoring friction:** users may prefer a spreadsheet if creating a rigorous contract feels expensive.
3. **Thin-wrapper perception:** the project will look superficial unless versioning, evidence sufficiency, and verdict semantics are visibly substantive.

## Five largest technical risks

1. Statistical rules may be misconfigured or overclaimed.
2. Performance measurements may hide errors, retries, coordinated omission, or workload drift.
3. Adapter failures may be confused with customer-system failures.
4. Sensitive data may reach storage before redaction.
5. Canonicalization or artifact provenance may be inconsistent across versions.

## Claims narrowed before implementation

- Replace absolute “no PII” claims with a declared detection and redaction policy unless a stronger prevention mechanism is proven.
- Distinguish estimated token cost from billed and total platform cost.
- Treat live performance results as time- and environment-specific, not universally reproducible.
- Do not claim cryptographic truth, tamper-proof execution, reduced sales cycles, or improved win rates.
- Do not call a 200-case 99% proportion test conclusive under a 95% Wilson lower-bound rule; the sample policy must make passing mathematically possible.

## Non-goals

Version one does not include CRM integration, multi-tenant authentication, billing, a generic workflow builder, automatic POC generation, a new tracing platform, a new load generator, automatic remediation, broad provider coverage, or Kubernetes.

## Public-version completion bar

- Complete local deterministic evidence chain.
- At least one hosted endpoint and one real load test.
- At least 25 automated tests spanning success and failure paths.
- Inspectable curated evidence bundle with no secrets or raw PII.
- Intentional `NOT_PROVEN` or failure in the public demo.
- Five-minute local setup and a reliable 90-second recording.
- Architecture diagram that matches the implementation.
- Two field/solutions/presales practitioner reviews, or a prominent statement that validation is still outstanding.
