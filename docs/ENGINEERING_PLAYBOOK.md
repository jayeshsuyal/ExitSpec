# ExitSpec Engineering Playbook

## Purpose

ExitSpec turns ambiguous POC expectations into an exact agreement and then
requires evidence before making a claim. The project must be built the same way.

Every meaningful change is an engineering claim:

```text
decision
    -> bounded implementation
    -> explicit exit gate
    -> adversarial verification
    -> inspectable evidence
    -> merge or stop
```

This playbook defines how ExitSpec plans changes, structures pull requests,
classifies bugs, proves merge readiness, hardens product waves, and decides when
a demo or release is honest.

It is deliberately stricter at trust boundaries: customer source, provider
egress, internal approval, customer confirmation, contract freeze, measurement,
verdict, and evidence publication.

## Status and document authority

This document governs the engineering process. The domain specifications govern
product behavior:

- `CONTRACT_SPEC.md` defines agreement, confirmation, freeze, and evidence-pack
  invariants.
- `MEASUREMENT_SPEC.md` defines measurement and verdict semantics.
- `PROVIDER_SPEC.md` defines replaceable provider behavior.
- `REDACTION_SPEC.md` defines source-redaction behavior.
- `SECURITY.md` defines the current trust posture and production gates.
- Architecture Decision Records preserve decisions that should not be silently
  reversed.

If this playbook conflicts with a domain specification, do not choose the more
convenient interpretation. Stop, identify the conflict, and resolve it in the
appropriate specification or ADR before merging implementation.

This document does not claim that every future gate is already automated. It
distinguishes current repository checks from gates that must be implemented as
the corresponding product wave lands.

## The operating rule

No pull request is complete merely because:

- the happy path works;
- the UI looks finished;
- a provider returned a successful response once;
- an LLM produced plausible JSON;
- the full test suite passed without testing the new failure mode; or
- a demo can be completed with private knowledge that is absent from the product.

A pull request is complete when its scoped claim has a binary exit gate, the gate
has been exercised, the evidence is inspectable, failure behavior is intentional,
and the change preserves ExitSpec's authority boundaries.

## Vocabulary

### Entry gate

What must be known before implementation begins. It prevents coding against an
undefined decision.

### Exit gate

The smallest set of observable conditions that proves the scoped change is ready
to merge. An exit gate is binary. If reviewers cannot tell whether it passed, it
is not a usable gate.

### Wave gate

The combined gate for a coherent product capability such as email intake or live
Fireworks authoring. Passing individual pull requests does not pass the wave gate
until the end-to-end behavior and its failure paths are proven together.

### Release gate

The conditions for calling a selected version demo-ready, public-release-ready,
or production-ready. These are different claims and therefore have different
gates.

### Evidence

An artifact that supports the engineering claim: test output, a deterministic
fixture, a sanitized receipt, an inspected state transition, a browser capture,
an artifact hash, or a reproducible command and result.

A screenshot can prove presentation. It cannot, by itself, prove server-side
authorization, immutability, privacy, idempotency, or statistical correctness.

## Product invariants

The following invariants apply across the PR train. A change that intentionally
alters one requires an explicit product decision, updated specification, an ADR
when appropriate, migration or compatibility analysis, and adversarial tests.

### INV-01 — Source is input, not authority

Email, meeting transcript, pasted note, uploaded document, prompt, and provider
output may propose requirements. None may approve a criterion, confirm an
agreement, freeze a contract, create evidence, or assign a verdict.

### INV-02 — Every executable requirement has provenance

An executable criterion must point to an exact allowed source span or be marked
as a named human addition with a rationale. Extraction must not manufacture
unsupported facts.

### INV-03 — Provider output remains review-only

Fireworks and every future model provider are replaceable structured-authoring or
execution adapters. Provider output must pass local schema, type, source-link,
redaction, and policy checks and must remain `NEEDS_REVIEW`.

### INV-04 — Internal review and customer confirmation are distinct

A named internal human may approve a requirement for presentation. Only a
separate customer decision bound to the exact visible agreement may authorize the
confirmed freeze path. Neither action creates evidence.

### INV-05 — Confirmation binds exact content and version

Customer confirmation is valid only for the exact contract ID, version, and
fingerprint that was shown. Changed content, stale versions, expired or revoked
capabilities, conflicting idempotency replays, and missing acknowledgement must
fail closed.

### INV-06 — Frozen means immutable

A frozen contract and its nested graph are never edited in place. A change
creates a new draft version linked to its parent. Supersession, when implemented,
must use a separate record rather than mutate historical truth.

### INV-07 — Measurement facts and verdict authority are separate

Adapters return typed facts and evidence references. The deterministic verdict
engine applies the frozen rule. A provider, adapter, UI, or report renderer may
not decide that a criterion passed.

### INV-08 — Missing, invalid, or insufficient evidence never becomes `PASS`

Evidence corruption, fixture mismatch, missing metadata, too few valid samples,
internal execution errors, or statistical inconclusiveness must produce the
appropriate non-`PASS` state.

### INV-09 — Product failure and software failure are not interchangeable

A customer system that misses a sufficiently measured threshold may receive
`FAIL`. A dependency that prevents execution is `BLOCKED`. Invalid or
insufficient evidence is `NOT_PROVEN`. An ExitSpec or adapter defect is an
internal failure. Internal failure must never be presented as customer failure.

### INV-10 — Evidence is traceable to the frozen agreement

Run manifests, fixtures, criteria, measurements, verdicts, and rendered artifacts
must agree on identity and version. Required hashes must verify before an
Evidence Pack is published.

### INV-11 — Privacy gates precede provider and persistence boundaries

Raw text source is handled transiently. Allowed text must pass the declared
redaction and egress checks before provider use or persistence. Passing a
mechanical redaction gate does not replace human privacy review.

Raw audio is a separate boundary: it cannot be content-redacted before an STT
provider receives it. Audio egress therefore requires its own authorization
contract covering exact meeting identity, participants, recorded consent,
provider, region, retention, zero-retention behavior, deletion, and incident
handling. Transcript redaction must never be represented as protection for audio
that already crossed the provider boundary.

### INV-12 — Secrets and sensitive source never become evidence

Credentials, raw provider bodies, customer secrets, unapproved customer source,
and raw audio must not appear in contracts, fixtures, logs, exceptions,
screenshots, receipts, test snapshots, commits, or public Evidence Packs.

### INV-13 — `PASS` is not business authorization

A passing POC is evidence about the approved criteria and workload. It does not
authorize production deployment, procurement, traffic expansion, model rollout,
spend, or security approval.

### INV-14 — Current capability is described honestly

Synthetic, local, in-memory, unauthenticated, estimated, and provider-free
behavior must be labeled as such. Planned functionality must not be described as
implemented.

### INV-15 — External systems remain replaceable

Source ingestion, model authoring, meeting transport, speech-to-text, measurement,
storage, and delivery must meet provider-neutral contracts. Provider-specific
code belongs behind those boundaries.

### INV-16 — Safe defaults preserve the deterministic loop

An absent credential, unavailable provider, or disabled integration must not
destroy the deterministic local path. External behavior is explicit, visible,
bounded, and fail-closed.

## Authority map

| Layer | May do | Must never do |
| --- | --- | --- |
| Source adapter | Normalize an allowed source and preserve provenance | Approve, confirm, freeze, measure, or judge |
| Assisted authoring | Propose source-linked structured facts | Set policy, approval, contract state, or verdict |
| Internal reviewer | Approve, reject, or correct a proposal | Confirm for the customer or create evidence |
| Customer reviewer | Confirm exact visible terms or request changes | Create evidence or authorize production |
| Contract service | Validate lifecycle and freeze an exactly confirmed version | Measure or assign a business verdict |
| Measurement adapter | Produce typed facts and evidence references | Relax a rule or assign `PASS` |
| Verdict engine | Apply the frozen deterministic rule | Change the agreement or authorize deployment |
| Evidence renderer | Present verified facts, calculation, limits, and artifacts | Expand the claim beyond what was proved |

Every PR that crosses an authority boundary must state which component owns the
decision before and after the change. “The model decides” is not an acceptable
authority assignment.

## Change risk levels

Risk controls how much evidence and review a change needs. It does not measure
how many lines changed.

| Level | Typical change | Minimum additional discipline |
| --- | --- | --- |
| C0 | Documentation or copy with no behavioral claim | Link and terminology validation |
| C1 | Deterministic local behavior with no authority change | Unit tests and relevant regression |
| C2 | Contract, confirmation, verdict, persistence, or artifact behavior | Invariant tests, adversarial cases, migration and rollback analysis |
| C3 | External provider, email, Zoom, hosted endpoint, or other network boundary | Fake transport tests, typed failure matrix, budgets/timeouts, sanitized receipts, disabled-mode proof |
| C4 | Real customer data, authenticated identity, durable authorization, raw audio, or production deployment | Threat model, privacy and retention approval, second-human review, operational rollback, incident path |

When uncertain, use the higher level. A small diff at an authority boundary can
be more dangerous than a large CSS refactor.

## Pull request sizing

A strong PR normally makes one primary decision and proves one coherent behavior.
It should be possible to answer:

1. What claim becomes true after this merges?
2. What deliberately remains untrue?
3. Which authority boundary, if any, changes?
4. What single exit gate decides whether it may merge?
5. How can the change be disabled or reverted?

Split a PR when it combines independently reversible decisions, unrelated
failure domains, multiple provider boundaries, or implementation plus broad
cleanup that hides the actual risk.

Do not split work by arbitrary file count or create empty PRs to inflate activity.
A large, meaningful PR train comes from narrow semantic slices:

```text
contract
    -> deterministic implementation
    -> failure behavior
    -> integration
    -> hardening
    -> documentation and demo proof
```

Tests belong with the behavior they prove. A follow-up test-only PR is not a
substitute for testing the feature before merge.

## Required PR contract

Every product or engineering PR must contain the following sections. Short
answers are acceptable for low-risk changes; omitted decisions are not.

```markdown
## Decision
One sentence describing the claim this PR makes true.

## User outcome
Who benefits, what they can now do, and what they will observe.

## Scope
- Included behavior

## Non-goals
- Behavior intentionally not implemented or proved

## Risk and authority
- Change risk: C0–C4
- Authority boundary affected:
- Invariants exercised:

## Exit gate
- [ ] One binary, observable merge condition

## Failure matrix
| Failure | Expected user-visible outcome | State mutation | Retry | Evidence/test |
| --- | --- | --- | --- | --- |

## Evidence
- Automated:
- Manual:
- Artifacts:

## Security and privacy
- Data handled:
- Egress/persistence:
- Secret behavior:

## Rollback
How to disable or revert the change without corrupting accepted history.

## Follow-ups
Work explicitly deferred; none may be required for this PR's claim to be honest.
```

The exit gate must test the decision, not merely restate implementation:

Weak:

```text
Fireworks adapter added.
```

Strong:

```text
For one approved synthetic request, live Fireworks success and every declared
failure class return locally validated typed outcomes and content-free receipts;
no provider output can approve, freeze, or assign a verdict, and disabling the
integration preserves the deterministic workflow.
```

## Entry gate before coding

Before a C2–C4 change begins, establish:

- the user and decision being served;
- the source of truth for the relevant schema or API;
- the authority owner for every state transition;
- input and output contracts;
- data classification and retention;
- failure and retry semantics;
- spend, latency, and resource ceilings where applicable;
- the exact non-goals;
- a deterministic test seam;
- rollback or disable behavior; and
- the binary exit gate.

Research may be part of an earlier PR or design note. Unknowns that affect
authority, privacy, billing, or data loss are blockers, not implementation details
to guess.

## Universal merge gate

Every behavioral PR must pass all applicable checks below.

### 1. Decision and scope

- The PR contract names one primary decision.
- Scope and non-goals match the implementation.
- The diff contains no unexplained adjacent feature.
- User-facing claims do not exceed tested capability.
- Deferred work is not secretly required for the merged behavior to be safe.

### 2. Correctness

- The happy path has a deterministic test where practical.
- Every fixed bug has a regression test that fails without the fix.
- Relevant product invariants are exercised directly.
- State transitions reject illegal orderings and stale versions.
- Repeated operations have explicit idempotency behavior.
- Time, ordering, partial completion, and concurrency are tested when relevant.

### 3. Failure behavior

- Expected failures are typed rather than collapsed into a generic error.
- Failure does not leave hidden authority or partially valid state behind.
- Retry behavior is bounded and does not silently multiply cost or side effects.
- User-visible error language gives the honest state and a useful next action.
- Provider or internal errors cannot become customer `FAIL`.
- Missing evidence cannot become `PASS`.

### 4. Security and privacy

- No secret or sensitive customer data appears in the diff or evidence.
- Inputs are bounded and validated at the receiving boundary.
- Egress and persistence are explicit.
- Error strings and object representations are sanitized.
- Logs and receipts contain only approved metadata.
- Authorization and capability scope are enforced server-side, not only in UI.
- The current security posture remains accurate.

### 5. Operational behavior

- External calls have explicit timeouts.
- Retry count and backoff are bounded.
- Cost or usage has a declared ceiling where applicable.
- A disabled, unconfigured, or degraded state is intentional and testable.
- Partial writes and restart behavior are defined.
- Observability identifies failure class without exposing protected content.

### 6. User experience

- The current task and primary action are clear.
- Agreement status and evidence verdict remain visually and semantically distinct.
- Destructive, irreversible, billed, or external actions require appropriate
  disclosure and confirmation.
- Loading, empty, success, non-`PASS`, and error states are understandable.
- Keyboard focus, labels, contrast, zoom, and responsive reflow are checked for
  affected UI.
- The normal seeded workflow does not gain accidental workflow-length scrolling.

### 7. Documentation

- Specs, architecture, security posture, runbooks, and roadmap are updated when
  their claims change.
- Current, experimental, and planned functionality are clearly distinguished.
- Setup steps work from a clean environment.
- New environment variables document purpose, safe default, and failure mode.
- External prices, model names, API behavior, and scopes include a checked date
  when recorded.

### 8. Repository hygiene

- Tests and fixtures are deterministic by default.
- Generated files and local artifacts are not accidentally committed.
- No credential, customer data, raw audio, or private transcript is committed.
- The built package contains the required runtime resources.
- The diff is clean and contains no unresolved conflict markers or whitespace
  errors.
- CI passes on every supported Python version.

## Current repository verification

The current baseline verification is:

```bash
python3 -m pytest tests/test_distribution.py
python3 -m pytest --ignore=tests/test_distribution.py
node --check src/exitspec/static/app.js
node --check src/exitspec/static/review.js
git diff --check
```

`tests/test_distribution.py` builds and installs the wheel outside the checkout
and proves the bundled deterministic demo. The remaining Python suite and both
browser syntax checks mirror the current CI split.

These commands are the floor, not the entire gate. A PR also runs focused tests
for the behavior and failure classes it changes. Browser behavior requires
browser inspection; external integrations require transport-boundary tests; data
migrations require forward and rollback proof.

The planned automated release-gate command should compose these checks rather
than introduce different truth.

## Conditional verification by change type

| Change | Required evidence in addition to the baseline |
| --- | --- |
| Domain model or lifecycle | Illegal-transition, immutability, stale-version, serialization, and compatibility tests |
| Verdict or statistics | Boundary vectors, recomputation, insufficient-evidence, corrupted-evidence, and known-answer tests |
| Provider adapter | Fake transport success and failure matrix, schema rejection, timeout/retry/budget proof, sanitized receipt |
| Web/API boundary | Request bounds, media type, origin/auth rules, state mutation, idempotency, and error-shape tests |
| Persistence | Schema/migration proof, transaction atomicity, restart recovery, duplicate replay, unavailable-store behavior |
| UI | 1280×720 seeded-flow inspection, keyboard and focus check, narrower/zoomed reflow, all relevant states |
| Email source | Identity and thread metadata, exact source spans, attachments policy, redaction, duplicate delivery, no auto-confirmation |
| Meeting transport | Consent state, participant/source identity, reconnect/order behavior, redaction, zero-retention mode |
| Raw audio/STT | Explicit consent, audio lifecycle, residency/retention, deletion, speaker mapping, provider failure, no-audio logging |
| Evidence Pack | Frozen identity, manifest linkage, hash validation, deterministic recomputation, non-`PASS` rendering, static export |

## Failure matrix standard

Every C2–C4 PR lists the relevant failure classes before merge. The complete
catalog is not required for every change, but omission must be intentional.

Common classes include:

| Failure class | Expected treatment |
| --- | --- |
| Invalid or oversized input | Reject before state mutation |
| Missing source provenance | Keep review-only or reject executable conversion |
| Stale contract version or fingerprint | Reject; preserve current version |
| Missing or false acknowledgement | Reject confirmed freeze |
| Duplicate identical idempotency replay | Return the original result |
| Conflicting idempotency replay | Reject without replacing history |
| Missing integration configuration | Visible disabled state; preserve deterministic path |
| Authentication or permission failure | Typed external failure; no secret echo |
| Rate limit or quota exhaustion | Typed failure with bounded retry and next action |
| Insufficient provider balance | Typed external block; no fabricated success |
| Timeout, disconnect, or provider `5xx` | Bounded retry or typed failure; no partial authority |
| Malformed or schema-invalid provider output | Reject locally; remain review-only |
| Redaction or egress failure | Stop before provider or persistence boundary |
| Partial persistence or unavailable store | Atomic rollback or typed unavailable state |
| Fixture, manifest, or artifact hash mismatch | `NOT_PROVEN`; never publish `PASS` |
| Adapter crash or unsupported response | Internal failure; never customer `FAIL` |
| Insufficient valid samples | `NOT_PROVEN` |
| Valid evidence below threshold | `FAIL` |

For each relevant row, the PR records:

- user-visible outcome;
- resulting state;
- whether retry is safe;
- whether cost or side effects may have occurred;
- what receipt or log is retained; and
- the test or inspection that proves it.

## Bug policy

### What counts as a bug

A bug is a gap between declared behavior and observed behavior. The declaration
may come from:

- a product invariant;
- a frozen schema or lifecycle rule;
- a documented API or UI contract;
- an accepted PR exit gate;
- a security or privacy rule;
- an accessibility requirement; or
- an explicit user-facing promise.

An expected product outcome is not automatically a bug:

- `FAIL` can be the correct verdict for sufficient evidence below threshold.
- `BLOCKED` can be the correct state when an external dependency prevents a run.
- `NOT_PROVEN` can be the correct state for invalid or insufficient evidence.
- A disabled live integration can be correct when no approved credential exists.

It becomes a bug when ExitSpec classifies, renders, persists, retries, secures, or
explains that outcome incorrectly.

### Severity

| Severity | Definition | Examples | Response |
| --- | --- | --- | --- |
| P0 — Critical | Active secret/privacy exposure, unrecoverable history corruption, unauthorized production action, or false `PASS` with immediate material impact | Credential committed publicly; frozen agreement silently rewritten; corrupted evidence published as valid | Stop release/use, contain immediately, preserve evidence, rotate/revoke as needed |
| P1 — High | Core trust invariant or primary workflow is broken with no safe workaround | Wrong version freezes; customer decision can be forged; internal failure becomes customer `FAIL`; widespread data loss | Block merge/release; fix before feature work continues |
| P2 — Medium | Important behavior is wrong or substantially degraded, but trust boundaries remain intact and a safe workaround exists | Retry status misleading; one supported browser path unusable; serious accessibility regression | Prioritize in the active wave; add regression proof |
| P3 — Low | Limited polish, copy, documentation, or minor usability defect | Misaligned secondary text; stale non-safety example; small responsive defect | Schedule normally; do not mislabel as hardening proof |

Severity describes impact, not implementation difficulty or how loudly the bug was
reported.

### Bug record

A useful bug record contains:

```text
title
observed behavior
expected behavior
violated invariant or contract
severity and rationale
minimal reproduction
environment/version
customer or data exposure
safe containment
evidence
```

Do not paste secrets, customer source, raw audio, or unrestricted provider bodies
into an issue.

### Bug lifecycle

```text
observe
    -> contain if necessary
    -> reproduce
    -> classify impact and violated invariant
    -> add a failing regression
    -> make the smallest safe fix
    -> run nearby adversarial cases
    -> verify full applicable gates
    -> document limitation or operational action
    -> close with evidence
```

Before a feature PR merges, a bug caused by that PR is fixed in the same PR and
covered by a regression test. It is not counted as a separate accomplishment.

After merge, use a dedicated bug-fix PR when practical. It must identify the
violated invariant, include a regression that fails on the broken revision, and
avoid unrelated feature work.

Do not invent, duplicate, or artificially fragment bugs to increase PR count.

### Security and privacy defects

Potential exposure is handled privately until the scope is understood. First
actions are containment and evidence preservation, not public reproduction.
Rotate or revoke affected credentials and capabilities, identify every copy, and
do not include sensitive payloads in the eventual public fix.

## Regression-test rule

Every bug fix asks:

1. What exact input or event order triggered the defect?
2. Which invariant should have rejected or handled it?
3. Why did the existing suite miss it?
4. What is the smallest test that fails without the fix?
5. Which adjacent variants could fail for the same reason?

The regression test should exercise the public or domain boundary where the bug
became possible. Testing a private helper alone is insufficient when the failure
depends on API state, persistence, browser behavior, or authority ordering.

## Hardening PRs

Each product wave ends with a dedicated hardening PR or an explicit documented
decision that the final implementation PR already satisfies the same gate.

A hardening PR does not add a new headline feature. It closes the wave by:

- running the complete failure matrix;
- checking retries, idempotency, restart, and partial-state behavior;
- validating privacy, secrets, and receipts;
- checking accessibility and degraded UX;
- reconciling specs, security posture, roadmap, and demo claims;
- testing clean installation or migration;
- recording known limitations; and
- proving the wave's end-to-end exit gate.

Hardening is not a place to postpone correctness that was required for an earlier
PR to be safe.

## Engineering Evidence Pack

Each C2–C4 PR should leave a compact evidence trail in its PR description or
linked CI artifacts.

### Minimum contents

1. **Claim** — the decision the PR makes true.
2. **Scope identity** — branch/commit and relevant contract, fixture, schema, or
   adapter version.
3. **Automated proof** — focused tests and baseline gate results.
4. **Failure proof** — the relevant failure matrix with observed outcomes.
5. **Manual proof** — browser or operator steps that cannot yet be automated.
6. **Artifacts** — sanitized screenshots, receipts, manifests, or hashes where
   useful.
7. **Limits** — what the evidence does not prove.
8. **Rollback** — how the behavior can be safely disabled or reverted.

### Evidence rules

- Evidence must be reproducible from approved synthetic inputs where practical.
- Evidence must correspond to the reviewed revision.
- A successful live call is supporting evidence, not a replacement for
  deterministic transport tests.
- Provider dashboards are useful for billing confirmation but do not prove local
  authority enforcement.
- Screenshots are presentation evidence only.
- Raw customer source, credentials, and unrestricted provider payloads are never
  accepted evidence artifacts.
- “Tests passed” without naming the relevant tests is insufficient for C2–C4
  behavior.

## PR-train governance

### One dependency chain, multiple safe lanes

Parallel work is encouraged when authority and file ownership are clear. Good
parallel lanes include:

- domain contracts and tests;
- source or provider adapter behind an agreed interface;
- UI against deterministic fixtures;
- documentation and runbooks after behavior is known; and
- adversarial review in a separate lane.

Do not parallelize two implementations of the same state transition without a
locked contract. Do not let UI invent a server authority that has not landed.

### Dependency declaration

Each PR in a train identifies:

- its direct prerequisite;
- whether it can merge independently;
- the capability it unlocks;
- the wave exit gate it contributes to; and
- whether rollback leaves later PRs safe.

Stacked PRs may be useful, but only the reviewed dependency order may merge.

### Wave discipline

The next product wave does not become the default path until the current wave
passes its end-to-end gate. Research and provider-neutral contracts for later
waves may proceed in parallel, but their UI and documentation must remain clearly
experimental.

### Meaningful PR count

The project values many narrow, inspectable decisions—not PR farming. A strong
train naturally includes contracts, fixtures, deterministic behavior, failure
semantics, integration, security, hardening, docs, and demo proof. Bug PRs are
counted only when real defects are discovered after merge.

## Wave exit gates

These gates define “done” for the planned product sequence. Each wave may contain
multiple PRs, but it is incomplete until the full gate passes.

### Dependency tracks

The numbered waves describe capability dependencies, not permission to use real
customer data.

**Synthetic/demo track:** provider-neutral contracts, approved synthetic
fixtures, deterministic replay, and explicitly synthetic two-person demos may
proceed before hosted identity exists. These paths cannot ingest real customer
source, represent a reviewer as authenticated, deliver a real customer decision,
or claim durable audit history.

**Real/hosted track:** real mailbox access, real customer email, real meeting
capture, customer-bound review links, raw customer audio, and hosted customer
decisions require the durable identity and authorization trust gate in Wave 7
first. They also require their own retention, deletion, incident, and provider
approval.

A synthetic connector passing its wave does not automatically authorize its real
counterpart.

### Binary wave-gate standard

Before implementation begins, every eval- or transport-dependent wave must freeze
a versioned acceptance manifest containing:

- exact synthetic fixtures and digests;
- case and slice counts;
- metric definitions and timing start/end points;
- minimum quality thresholds;
- maximum event loss, duplication, and reconnect recovery limits;
- zero-tolerance authority, consent, secret, and privacy violations;
- supported failure classes; and
- the adapter and policy versions being judged.

The wave passes only when every required manifest rule passes. If the manifest is
not frozen, the wave has not entered implementation. “Looks good,”
“reviewer-correctable,” and “survives reconnects” are not executable gates by
themselves.

### Wave 0 — Deterministic agreement-to-evidence spine

The local spine is ready when a fresh operator can:

1. turn allowed synthetic source into unresolved candidates;
2. make a named internal decision;
3. present the complete canonical agreement;
4. record an exact-version customer decision;
5. freeze only the confirmed version;
6. run deterministic measurement;
7. receive the correct typed verdict; and
8. inspect a verified static Evidence Pack.

Required adversarial proof includes stale confirmation, missing acknowledgement,
change request, frozen immutability, fixture mismatch, artifact mutation,
insufficient evidence, adapter failure, and external block semantics.

### Wave 1 — Explicit assisted authoring and live Fireworks

The frozen V1 decision contract is
[`wave-1-acceptance-v1.json`](../examples/support-agent/fireworks/wave-1-acceptance-v1.json).
It fixes the approved synthetic cases, provider destination, pricing and data
policy snapshots, request ceilings, payload-bound acknowledgement contract,
failure matrix, and binary quality gates. Freezing the manifest authorizes
implementation against those tests; it does not prove that Wave 1 has passed.

Entry requirements:

- approved synthetic payload;
- explicit model and endpoint;
- credential source;
- provider disclosure and data policy;
- a server-enforced per-request egress acknowledgement bound to the exact
  redacted payload and policy version;
- visible model, destination, provider retention policy, and maximum spend before
  sending;
- configured customer-term redaction where relevant;
- a transport that rejects redirects before an authorization header can leave
  the pinned origin;
- timeout, retry, token, and spend ceilings; and
- deterministic fake transport.

Exit gate:

```text
An explicit user action with a server-validated payload-bound acknowledgement
sends only redaction-approved synthetic source to the pinned Fireworks origin.
Success and every failure in the frozen acceptance manifest produce locally
validated typed outcomes, safe next actions, and content-free receipts. Every
model proposal remains NEEDS_REVIEW. Fireworks cannot approve, confirm, freeze,
measure, or assign a verdict. Redirects are rejected, and disabled or
unconfigured Fireworks preserves the complete deterministic path.
```

Required failures include missing configuration, invalid credential, suspended or
unfunded account, rate limit, timeout, `5xx`, malformed JSON, schema violation,
source-link violation, retry exhaustion, budget refusal, and every `301`, `302`,
`303`, `307`, or `308` redirect.

### Wave 2 — SourceEnvelope and synthetic RFC822 intake

Entry requirements:

- provider-neutral `SourceEnvelope`;
- source type, stable identity, digest, timestamps, and provenance span model;
- attachment allowlist and size policy;
- redaction-before-persistence rule; and
- explicit statement that an email fixture is source, never confirmation.

Exit gate:

```text
From each approved synthetic RFC822 fixture in the frozen manifest, an employee
can create source-linked review candidates in under 60 seconds, inspect exact
provenance, correct or reject proposals, and continue through the existing
agreement spine. Reimport is idempotent, follow-up fixture changes create a new
version, unsupported attachments fail safely, and no email content can
auto-approve or auto-confirm.
```

Required failures include duplicate import, changed thread content, sender
ambiguity, missing body, oversized content, unsupported attachment, HTML/plain
text disagreement, redaction failure, and assisted-authoring provider failure.

Inbox automation, OAuth, remote mailbox access, webhook delivery, and sending
mail are non-goals for this wave.

### Wave 2.5 — POC workspace and multi-source shell

The frozen V1 decision contract is
[`poc-workspace-acceptance-v1.json`](../examples/product/poc-workspace-acceptance-v1.json),
with its human-readable product contract in
[`POC_WORKSPACE_SPEC.md`](POC_WORKSPACE_SPEC.md). Freezing these documents
authorizes implementation against their gates; it does not prove the workspace
has been implemented.

Entry requirements:

- POC as the stable aggregate root;
- source channels modeled as children rather than POC types;
- read-only derived `Define`, `Prove`, and `Decide` phases;
- explicit route and compatibility ownership;
- material-change versioning rules;
- the graphite/orange visual token contract; and
- machine-readable dashboard, creation, multi-source, responsive, and authority
  gates.

Exit gate:

```text
A first-time tester can create or resume one local POC, identify its exact next
action, bring the existing synthetic email and one synthetic meeting transcript
into that same POC, and continue through the existing agreement-to-evidence
spine. Source additions cannot mutate reviewed, confirmed, or frozen agreement
versions. Agreement status and evidence verdict remain distinct. Existing demo,
review, and artifact routes remain compatible. Every 1280x720, narrow-width,
keyboard, no-infinite-scroll, and graphite/orange rule in the frozen contract
passes.
```

Real mailbox, live meeting, raw audio, authenticated identity, tenant
authorization, and durable production storage remain outside this synthetic
wave.

### Wave 3 — Source-linked authoring quality

Exit gate:

```text
Across the frozen synthetic authoring manifest containing measurable, vague,
conflicting, and unsupported requests, every required slice meets its pinned
quality threshold; unsupported thresholds are invented zero times; source,
approval, freeze, and verdict authority violations are zero; and results are
reported by slice as well as in aggregate.
```

The eval set must include critical slices for numerical thresholds, budgets,
latency, tool calls, safety constraints, non-goals, conflicting speakers, and
requests that the current adapter cannot execute.

### Wave 4 — Meeting-source foundation and synthetic live replay

Entry requirements:

- provider-neutral `TranscriptEvent` contract;
- participant identity and timestamp semantics;
- ordering, deduplication, and reconnect rules;
- consent state;
- redaction and retention policy; and
- synthetic event replay.

Exit gate:

```text
A deterministic synthetic meeting stream can disconnect, reconnect, reorder, and
repeat events while meeting every loss, duplication, ordering, and recovery rule
in the frozen event manifest. Authority and consent violations are zero. The
workbench visibly shows capture and consent state, produces source-linked
review-only candidates, and retains no raw audio.
```

### Wave 5 — Synthetic Zoom transcript demo

Exit gate:

```text
With two consenting synthetic participants, the Zoom transport maps live
participant-attributed transcript events into the provider-neutral contract,
meets every loss, duplication, ordering, reconnect, and timing rule in the frozen
Zoom manifest, and produces a reviewable synthetic draft during the call. The
employee can show the draft and exercise the explicitly synthetic confirmation
demo. Zoom transport never bypasses review or confirmation, and this wave does
not authorize real customer meetings, delivery, or decisions.
```

Required evidence includes scopes, start/stop disclosure, participant mapping,
reconnect behavior, duplicate events, late final transcript, meeting end,
provider outage, and explicit no-consent behavior.

### Wave 6 — Synthetic raw audio and replaceable speech-to-text

This wave must not begin with real customer audio until its C4 entry gate is
approved.

Entry requirements:

- explicit recorded consent design;
- raw-audio memory, storage, retention, deletion, and incident lifecycle;
- an audio-egress authorization bound to the exact synthetic meeting,
  participants, provider, region, retention policy, and consent record;
- region and data-residency decision;
- speaker attribution policy;
- provider disclosure;
- zero-retention mode;
- encrypted transport and storage where persistence exists; and
- approved synthetic audio fixtures.

Exit gate:

```text
Approved synthetic audio can be streamed through a replaceable STT adapter into
the same TranscriptEvent contract while meeting every latency, speaker, loss,
duplication, and retention rule in the frozen STT manifest. Consent refusal
prevents capture, raw audio is absent from logs and Evidence Packs,
retention/deletion behavior is verified, and provider failure cannot create false
transcript authority. Transcript redaction is not credited as protection for
audio already sent to the STT provider.
```

### Wave 7 — Real-customer trust gate: durable identity and confirmation

This gate must pass before any real mailbox connector, real customer meeting,
customer-bound delivery, real customer source, or hosted customer decision is
enabled.

Exit gate:

```text
Authenticated, contract-scoped reviewers can access only authorized versions.
Invitations, revocations, expiry, delivery, decisions, and idempotent replays are
durable and append-only. Restart preserves history, stale or revoked capabilities
fail closed, material agreement changes require a new version, and the complete
audit chain is inspectable without weakening exact acknowledgement or fingerprint
checks.
```

Migration, backup, restore, tenant isolation, retention, deletion, and
unavailable-store behavior are part of this gate.

### Wave 7A — Real mailbox connector

Entry requirements include Wave 7 plus least-privilege OAuth scopes, encrypted
token custody, webhook authenticity, HTML sanitization, disabled remote-resource
loading, attachment malware and archive controls, mailbox retention, and
revocation behavior.

Exit gate:

```text
For an authorized test tenant and the frozen mailbox manifest, ExitSpec imports
only the selected messages, preserves exact source provenance, applies the
approved retention policy, handles duplicate delivery idempotently, and revokes
access without losing accepted history. Unauthorized tenants, forged webhooks,
remote content, unsupported attachments, expired tokens, and revoked access fail
closed. Email remains source and can never act as customer confirmation.
```

### Wave 7B — Real meeting and Zoom connector

Entry requirements include Wave 7 plus meeting-scoped authorization, verified
participant/organizer rules, explicit capture disclosure, consent records,
retention/deletion, and an incident path.

Exit gate:

```text
For an authorized test tenant and frozen meeting manifest, only an approved
meeting can start capture; every participant sees the declared disclosure;
consent refusal prevents capture; reconnect and finalization meet pinned event
limits; retention and deletion are verified; and no transcript event can approve,
confirm, freeze, or judge a contract.
```

Raw customer audio additionally requires the Wave 6 audio-egress contract to be
approved for real data.

### Wave 8 — Hosted measurement and multi-criterion evidence

Each new criterion type needs its own schema, adapter, sufficiency rules, failure
semantics, renderer, and adversarial tests.

Exit gate:

```text
For an approved frozen multi-criterion contract, hosted adapters return facts
with complete environment, retry, latency, usage, cost, and artifact provenance.
The deterministic engine recomputes every criterion and the overall verdict.
Changing an adapter cannot change agreement or verdict authority, and no partial,
corrupt, or insufficient evidence can publish PASS.
```

### Wave 9 — Public hosted release

Exit gate:

```text
A new authorized user can complete the documented workflow in the hosted product
with tenant-scoped access, durable history, bounded integrations, inspectable
evidence, operational observability, backup and recovery, deletion controls, and
an exercised rollback path. Security, privacy, and product claims describe the
deployed revision exactly.
```

This is not equivalent to the local public demo gate and is not satisfied by a
successful screen recording.

## Demo, open-source, and production release gates

### Demo-ready

A demo release is ready when:

- a fresh operator can start it from documented steps;
- the deterministic path does not require a paid provider;
- the primary script works from `Restart`;
- one non-`PASS` case and its next action are demonstrated;
- the frozen rule and calculation can be explained;
- Evidence Pack artifacts can be inspected;
- optional live integrations have a tested backup path; and
- every synthetic, local, in-memory, unauthenticated, and estimated limitation is
  stated honestly.

### Public open-source-ready

In addition to the demo gate:

- a clean clone installs on supported versions;
- the wheel works outside the checkout;
- license and attribution files are correct;
- no secret, customer data, private source, raw audio, or local artifact is
  present;
- configuration fails safely;
- contribution and security-reporting paths are documented;
- CI passes;
- current architecture, roadmap, security, and provider claims agree; and
- a tagged revision can be reproduced.

### Production-ready

Production readiness requires the applicable C4 and Wave 9 gates, including
authenticated identity, tenant authorization, durable audit history, encrypted
storage and transport, retention/deletion, backups, recovery, observability,
incident response, and an exercised rollback.

Until those gates pass, do not describe ExitSpec as production-ready.

## Rollback standard

Every C2–C4 PR answers:

1. Can the capability be disabled without deleting accepted history?
2. What happens to in-flight operations?
3. Are schema or data changes backward compatible?
4. Can an older binary read the new records safely?
5. Which external side effects cannot be undone?
6. Does retry after rollback duplicate cost, delivery, or customer decisions?
7. How will the operator know rollback completed?

Feature flags protect rollout only when safe defaults and state compatibility are
tested. A flag must not allow an invalid lifecycle transition or reinterpret old
evidence.

Frozen contracts, customer decisions, and published evidence history are never
silently deleted or rewritten as a rollback technique.

## Observability and receipts

Logs and receipts exist to answer:

- what boundary was attempted;
- which typed outcome occurred;
- when it occurred;
- which safe identifiers and versions were involved;
- whether retry or cost may have occurred; and
- what the operator should do next.

They must not contain credentials, raw customer source, raw audio, unrestricted
provider payloads, or hidden model reasoning.

External execution receipts should be content-free and identify provider,
operation type, model or adapter version, timestamps, attempt count, token/usage
metadata where approved, estimated cost boundary, and typed result. A receipt
does not confer approval or prove that generated content was correct.

## Documentation freshness

Documentation is part of the product contract.

Update the relevant document in the same PR when a change alters:

- current capability;
- lifecycle or authority;
- schema or artifact shape;
- provider behavior or pricing assumptions;
- privacy, security, retention, or egress;
- setup, configuration, or safe defaults;
- demo steps; or
- roadmap sequencing.

If code and documentation disagree, the merge gate fails. A follow-up
documentation issue is acceptable only for non-material editorial improvement,
not for a misleading capability or safety claim.

## Review standard

The author performs the first adversarial review:

- How could this create authority from untrusted input?
- How could stale or duplicated state win?
- How could partial failure look successful?
- How could missing evidence become `PASS`?
- How could a retry duplicate cost or side effects?
- What sensitive content could enter logs, errors, fixtures, screenshots, or
  artifacts?
- What happens after restart?
- What happens when the external dependency is disabled or unavailable?
- Which claim would a reasonable user over-infer from the UI or documentation?

C3 changes should receive a review focused on the provider or network boundary.
C4 changes require a second-human security/privacy review before public or real
customer use.

## Definition of done

A change is done only when:

- its PR contract is complete;
- its binary exit gate passes;
- applicable product invariants are preserved and tested;
- the failure matrix is exercised;
- focused and baseline verification pass;
- evidence corresponds to the reviewed revision;
- secrets and customer data are absent;
- user-visible and operational failure behavior is clear;
- rollback or disable behavior is known;
- relevant documentation is current;
- no P0, P1, or release-blocking P2 defect remains; and
- reviewers can state what the change does **not** prove.

## Anti-patterns

Reject the following:

- “Works on my machine” as the exit gate.
- A giant PR combining contracts, provider code, UI, persistence, and cleanup.
- A provider response directly setting approval, freeze, or verdict.
- A screenshot used as proof of authorization or immutability.
- Catching every exception and returning one generic failure.
- Retrying billed operations without explicit limits and idempotency.
- Logging request or response bodies for convenience.
- Committing a real API key, customer email, meeting transcript, or raw audio.
- Calling an estimated cost a billed cost.
- Reporting only an aggregate eval score while a critical slice regresses.
- Treating a high point estimate as sufficient when the frozen rule requires a
  confidence bound.
- Calling `BLOCKED` or `NOT_PROVEN` a product failure to make a demo look cleaner.
- Describing a local in-memory capability as authenticated or durable.
- Moving required correctness into a future hardening PR.
- Inventing bugs or microscopic PRs to inflate contribution count.

## Worked examples

### Example A — Fireworks live authoring

**Decision:** ExitSpec may optionally ask one approved Fireworks model to propose
structured source-linked facts from redaction-approved synthetic input.

**Non-goals:** production customer data, automatic approval, model-driven policy,
measurement, verdicts, or Fireworks as a required dependency.

**Exit gate:** successful and failed live attempts create typed, sanitized
outcomes; all accepted proposals pass local validation and remain
`NEEDS_REVIEW`; disabled mode preserves the deterministic loop.

**Evidence:** fake-transport matrix, one sanitized live smoke when the account is
available, budget refusal, browser disabled state, and full regression suite.

### Example B — Synthetic RFC822 intake

**Decision:** An employee may turn an approved synthetic RFC822 fixture into a
source-linked review packet.

**Non-goals:** mailbox access, OAuth, webhook delivery, inbox automation, email
as customer confirmation, automatic contract freeze, arbitrary attachment
ingestion, or sending customer mail.

**Exit gate:** exact fixture provenance survives into review candidates;
duplicate import is idempotent; unsupported or unsafe input fails before
persistence; every proposal remains review-only.

**Evidence:** synthetic email fixtures, duplicate/thread/update cases, redaction
failure, attachment rejection, source-span inspection, and under-60-second manual
workflow.

### Example C — Stale confirmation bug

**Observed:** a customer confirms version 1, an employee edits the agreement to
version 2, and version 2 can still freeze with the version-1 decision.

**Severity:** P1 because exact-version confirmation is a core trust invariant.

**Required fix:** reproduce through the public confirmation boundary, add a test
that fails against the broken revision, reject the stale ID/version/fingerprint,
preserve both historical records, and verify that a new version-2 confirmation
can freeze normally.

**Evidence:** regression test, ledger inspection, full confirmation suite, and no
mutation of the version-1 record.

## Immediate implementation sequence for this playbook — completed

The original engineering-system sequence made this playbook operational:

1. Add a pull-request template implementing the required PR contract.
2. Add a structured bug-report template implementing severity, invariant,
   reproduction, containment, and evidence fields.
3. Add one automated repository gate that composes the current Python,
   JavaScript, distribution, and diff checks.
4. Update CI to call that same gate so local and remote truth cannot drift.
5. Add a lightweight test that ensures required template sections and gate
   commands remain present.

Those PRs should remain separate where each is independently reviewable and
reversible. The automation must encode this playbook; it must not create a second,
conflicting definition of done.

The current product-contract sequence now begins with Wave 2.5. Its contract and
acceptance manifest must land before any dashboard implementation PR. The local
workspace train may proceed on synthetic data while production identity,
customer-bound delivery, live connectors, and raw audio remain behind their
later trust gates.
