# Architecture

## System boundary

The product journey begins with customer source material. The trusted decision
boundary begins later, at a named human decision and an immutable frozen contract.
Redaction and assisted extraction are authoring controls; neither is verdict
evidence.

```text
customer source (raw, ephemeral at intake)
      |
      v
best-effort redaction + fresh egress check
      |
      +-- raw source stops here
      v
redacted source -> optional structured provider assistance
                         |
                         v
              locally validated facts only
                         |
                         v
              source-linked NEEDS_REVIEW drafts
                         |
                         v
           named human approval or rejection
                         |
                         v
              version-scoped customer review
                         |
                         v
          immutable exact-version confirmation
                         |
                         v
              explicit immutable FROZEN contract + digest
                                      |
                                      v
                         provider-neutral measurement adapter
                                      |
                                      v
                         deterministic verdict engine
                                      |
                                      v
                  POC Acceptance Evidence Pack
```

This is the required product boundary. `build_assisted_discovery_pack` now
implements the redaction-first assisted-authoring composition as a side-effect-free
service: it redacts and parses raw notes, performs a fresh provider-egress check,
executes a provider-neutral structured request, validates returned facts and exact
source anchors locally, applies only locally supplied execution policy, and emits
`NEEDS_REVIEW` drafts. Tests exercise this service through the real
`FireworksProvider` with a fake injected transport.

The service performs no persistence or browser/session mutation, has no built-in
live network transport, and is not wired into the browser UI. The browser follows
a separate redaction-first, provider-free path.

## Architectural rules

1. Raw customer source cannot cross the provider or persistence boundary.
2. Redaction is best-effort; its allow decision is not a guarantee that content is
   free of personal, confidential, or regulated data.
3. A fresh `assert_redaction_egress` check is required immediately before any
   redacted source is sent or persisted.
4. Provider output contains candidate facts only. It cannot set review status,
   approval, contract state, hashes, or verdicts.
5. Every candidate is source-linked and enters as `NEEDS_REVIEW`; vague material
   remains unresolved instead of receiving an invented measurement rule.
6. A named human records internal approval or rejection. Customer confirmation is
   a separate immutable record bound to contract ID, version, and fingerprint;
   it is never inferred from provider output.
7. Measurement adapters return facts and artifacts; they do not assign verdicts.
8. The verdict engine receives only a frozen criterion plus validated measurement
   facts.
9. Frozen contract objects and their nested contract graph are immutable. A new
   agreement becomes a new draft version.
10. The domain package does not import a web framework, frontend framework, or
    provider SDK.

## Current implementation map

| Boundary | Current evidence | Current limitation |
| --- | --- | --- |
| Intake | Browser notes are redacted before the bounded `Speaker: message` parser and retained only as redacted source plus safe summary metadata | Synthetic demo only; provider-free; no STT |
| Redaction | Immutable result, category placeholders, line-only findings, and fresh egress rescans are used by browser intake and assisted authoring | Best-effort patterns still require human review |
| Assisted facts | `build_assisted_discovery_pack` composes redaction, provider execution, strict DTO validation, exact source matching, local policy, and `NEEDS_REVIEW` drafts; tests use `FireworksProvider` with fake injected transport | Side-effect-free service only; not exposed in browser or hosted workflow; no live call |
| Draft review | Exact source-span validation and named approval/rejection records | Pasted notes remain unresolved; the complete sample uses prepared synthetic drafts |
| Customer review | Expiring local review link, immutable idempotent decision, and exact ID/version/fingerprint binding | Local typed identity only; no authentication or durable persistence |
| Contract | `DRAFT -> IN_REVIEW -> APPROVED` for internal review, then confirmation-gated RFC 8785 JCS freeze | One local POC shape; legacy non-web freeze primitive remains for compatibility |
| Measurement | Deterministic exact-tool-selection adapter and fixed fixture | No hosted endpoint adapter |
| Decision | Deterministic four-way verdict and consistency-checked report | Current report supports exactly one frozen criterion |

## Version-one components

### Domain core

Pure Python models and services for:

- contract and criterion validation;
- state transitions;
- canonical serialization and digest verification;
- statistical calculations;
- criterion and overall verdict aggregation.

### Authoring workflow

The local **Define** path uses strict domain models plus CLI and browser review
artifacts:

1. A transcript span must name its transcript, speaker, line range, and exact quote.
2. A candidate draft must preserve that source in its proposed criterion, or be explicitly human-added with a rationale.
3. A human review records an approval or rejection with reviewer, timestamp, and rationale.
4. Open questions, missing measurement fields, or an unreviewed draft cannot enter contract assembly.
5. Only approved drafts can create the internally approved contract proposed to
   the customer.
6. Only an affirmative confirmation matching that exact ID, version, and
   confirmation fingerprint can enter the web freeze-and-run path.

The current UI is a dependency-free local browser demo served by `exitspec serve`.
It calls an in-process, loopback-only HTTP boundary that delegates to the same
domain functions; the page never reimplements approval, freezing, or verdict
logic. It accepts only synthetic pasted notes, redacts them before parsing, and
retains only the redacted transcript and safe redaction summary in memory for the
running demo. It makes no provider call. Pasted notes become unresolved source
candidates; ExitSpec does not pretend that a parser negotiated a complete
criterion.

In the current model, `APPROVED` means the internal review is complete; customer
confirmation is a separate immutable record rather than another contract status.
The local browser issues an expiring capability, records an idempotent terminal
decision against the exact confirmation fingerprint, and calls
`freeze_confirmed_contract`. Confirmation and review-link state live only in the
running unauthenticated process, so they are not signatures, durable approvals,
or production authorization.

### Redaction boundary

`redact_transcript` receives raw text transiently and returns an immutable
`RedactionResult` containing only redacted text, aggregate categories, counts, and
line numbers. It never returns the matched values. `assert_redaction_egress`
rescans the result under the current policy immediately before provider or
persistence egress and denies forged, stale, blocked, or inconsistent results.

This boundary detects a documented set of patterns and configured customer terms.
It is not a general PII detector. Real-customer use therefore still requires human
review, consent, retention policy, and additional controls.

Both browser intake and assisted authoring call this boundary before raw notes can
enter returned application state. Assisted authoring performs another fresh
egress check immediately before invoking its injected executor.

### Structured provider boundary

`StructuredJSONRequest` pins the model, messages, schema, timeout, token estimate,
and optional budget. JSON Schema Draft 2020-12 is validated locally, external
schema references are rejected, and returned content is checked locally before a
typed callback receives it.

`FireworksProvider` is one replaceable implementation. It builds the documented
Fireworks Chat Completions structured-output request, accepts an injected
transport, records a content-free receipt, bounds retries, and emits sanitized
typed errors. It imports no ExitSpec contract or verdict model. It may assist
authoring or execute a typed provider operation, but it is never an authority.

The assisted-authoring integration tests pass `FireworksProvider` an injected fake
transport and verify redaction order, authority-field rejection, source matching,
failure sanitization, and review-only output. There is no built-in live network
transport in the current repository, and the browser never constructs or invokes
this provider path.

### Run orchestrator

The orchestrator validates the frozen contract and environment, writes a manifest before execution, invokes adapters, records events, validates artifacts, and requests verdict calculation.

Version one begins as one local process. Hosted long-running benchmarks will move to a separate worker boundary when the need is demonstrated; no queue infrastructure is introduced in Brick 1.

### Measurement adapters

Each adapter has a stable name and version and implements a narrow interface:

```python
class MeasurementAdapter(Protocol):
    name: str
    version: str

    def validate(self, criterion, environment) -> list[ValidationIssue]: ...
    def execute(self, criterion, fixture, context) -> MeasurementResult: ...
```

The interface will become asynchronous when endpoint I/O is added. The initial deterministic adapter remains synchronous so its semantics are easy to inspect.

### Evidence store

The artifact store uses a run-scoped directory:

```text
runs/<run-id>/
  contract.json
  run-manifest.json
  calculations.json
  verdicts.json
  decision-packet.html
  artifact-hashes.json
  evidence/
    <criterion-id>.jsonl
```

`decision-packet.html` is retained as an internal compatibility filename; the
public artifact is the **POC Acceptance Evidence Pack**. SQLite may later index
contracts, criteria, runs, verdicts, and artifact metadata. It is not part of the
current local implementation.

### Verdict engine

The engine is deterministic and versioned. For the first proportion criterion:

1. Return `BLOCKED` for a declared attributable external block.
2. Return `NOT_PROVEN` for internal measurement errors, metadata gaps, workload mismatch, artifact-integrity failure, or insufficient samples.
3. Return `FAIL` when sufficient evidence exists and the observed rate is below the approved threshold.
4. Return `PASS` when the approved Wilson lower bound is at or above the threshold.
5. Otherwise return `NOT_PROVEN` because the point estimate is favorable but statistically inconclusive.

### Local browser demo

The current browser surface is a small HTML/CSS/JavaScript client with a Python
standard-library server:

```text
Define -> Prove -> Decide
```

- **Define** shows source text beside candidate requirements and requires explicit
  approval or rejection.
- **Prove** runs a real deterministic scenario only after internal review,
  customer confirmation, and explicit freeze are complete.
- **Decide** exposes the verdict, its limits, the next human action, and the full
  static POC Acceptance Evidence Pack.

The client consumes the local API response contracts and never reimplements
verdict logic. It is not an authorization surface; a `PASS` remains evidence for a
human decision.

## State machines

### Contract

```text
DRAFT -> IN_REVIEW -> APPROVED (internal review complete)
                               |
                               +-- exact affirmative confirmation
                                      |
                                      +-- explicit freeze --> FROZEN
                                                                |
                                                                +-- terminal immutable record
```

No backward transition is allowed. `FROZEN` cannot be mutated to `SUPERSEDED`;
future supersession must be represented by a separate record that points from the
old frozen version to its replacement. Editing an approved or frozen contract
creates a new `DRAFT` revision with `parent_version` rather than mutating history.

### Criterion draft

```text
NEEDS_REVIEW -> APPROVED
      |
      +-------> REJECTED
```

This is deliberately separate from contract lifecycle. `APPROVED` means a human accepted one proposed criterion; it does not mean the whole POC contract is frozen.

### Run

```text
QUEUED -> VALIDATING -> RUNNING -> AGGREGATING -> COMPLETED
             |             |            |
             +----------> BLOCKED <------+
             +-------> FAILED_INTERNAL
             +------------> CANCELLED
```

A criterion failing is not a run software failure. A crashed adapter is not evidence that the target system failed.

## Repository structure

```text
.
├── README.md
├── pyproject.toml
├── docs/
├── examples/
│   └── support-agent/
│       ├── authoring/
│       ├── contracts/
│       └── fixtures/
├── src/exitspec/
│   ├── adapters/
│   ├── assisted_authoring.py
│   ├── authoring.py
│   ├── canonical.py
│   ├── intake.py
│   ├── models.py
│   ├── contracts.py
│   ├── providers/
│   ├── redaction.py
│   ├── statistics.py
│   ├── verdicts.py
│   ├── runner.py
│   ├── reporting.py
│   ├── web.py
│   ├── static/
│   └── cli.py
└── tests/
```

Hosted API and multi-user frontend layers will be added only when the local domain
contracts and demo loop have earned that complexity.

## Key decisions and rejected alternatives

### Canonical JSON instead of hashing YAML

YAML formatting, comments, aliases, and key order can change without changing meaning. ExitSpec validates the input into a typed model and hashes a documented canonical JSON representation.

### Filesystem artifacts now, metadata index later

Storing raw evidence as database blobs would make inspection and static bundles
harder. The current local implementation uses run-scoped filesystem artifacts.
If relationship and state queries later require an index, SQLite can index
metadata while artifact content remains inspectable files.

### CLI and local loopback server before hosted execution

The first slices exercise the same domain functions with fewer moving parts. The
`define` CLI and `serve` demo produce inspectable local artifacts; any later hosted
API or client will be a consumer of the proven core rather than the place where the
core rules live.

### Source-linked drafts instead of autonomous contract creation

An LLM or a deterministic extractor may propose a criterion, but the proposal is not allowed to become contract input until its source survives validation and a human records an explicit decision. A vague quote becomes a visible rejection or clarification request, not a silently invented metric.

### Replaceable providers instead of provider authority

Provider adapters stop at structured, locally validated output and observable
execution receipts. Contract approval, adapter selection, workload selection,
freezing, and verdict assignment stay in local policy and deterministic domain
code. Replacing Fireworks must not alter those authority boundaries.

### Mature load generator instead of a custom generator

Performance traffic generation has subtle correctness risks. ExitSpec will wrap and validate structured GuideLLM output rather than create a competing load engine.

## Scale path, not version-one scope

If evidence supports multi-user or hosted use, the natural evolution is PostgreSQL metadata, object storage, an isolated worker queue, signed identity/audit events, tenant-scoped authorization, and retention jobs. None is required to prove the first customer-shaped evidence chain.
