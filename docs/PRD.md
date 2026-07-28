# Product Requirements Document

## Product thesis

AI infrastructure POCs often begin with a promise in a call and end with a
manually assembled success narrative. The exact acceptance rule, customer
agreement, measurement conditions, and evidence are rarely kept as one
inspectable chain.

ExitSpec turns one customer-shaped requirement into a versioned executable
contract, binds confirmation to the exact visible agreement, runs an approved
measurement, and produces a defensible evidence handoff.

## Users and jobs

The primary user is a field, solutions, deployment, or customer engineer.

> Agree on what success means before execution, preserve every material change,
> and make the final POC result reproducible without trusting narration.

The secondary user is the customer approver.

> Review the complete agreement in plain language, confirm that exact version or
> request a change, and inspect what the final evidence does and does not prove.

## Differentiated product boundary

ExitSpec is not a generic eval dashboard, benchmark runner, CRM, or autonomous
POC generator.

- Eval products may own datasets, traces, graders, and experiment comparison.
- Benchmark products may own traffic generation and performance measurements.
- Presales products may own discovery documents, tasks, and CRM visibility.
- ExitSpec owns source-linked criteria, structured human review, exact-version
  customer confirmation, immutable freeze, evidence sufficiency, deterministic
  verdicts, and the acceptance evidence handoff.

The product may consume mature evaluation and load tools later. Those tools do
not receive agreement or verdict authority.

## Current supported vertical

The completed local product supports one synthetic support-agent POC and exactly
one executable criterion:

> Exact expected support-tool selection must meet a human-defined proportion
> threshold over a human-defined minimum sample count, and the two-sided 95%
> Wilson lower bound must meet the same threshold.

The prepared primary contract uses a 95% threshold and 200-case minimum. Reference
A observes 197 exact selections out of 200:

```text
Required ≥ 95.00% · Observed 197/200 (98.50%)
· Wilson lower bound 95.68% · PASS
```

The browser does not claim that arbitrary pasted metrics are executable.

The primary guided entry, `/app?intake=email`, starts from one of exactly two
manifest-approved synthetic samples. **Support-agent requirements** produces the
executable 95%/200 proposal plus a latency sentence that remains context because
no latency adapter exists. **Untrusted-instructions test** proves that source
language cannot approve, confirm, freeze, measure, or assign a verdict.

## Accepted workspace direction

The next product surface is governed by the
[POC Workspace Specification](POC_WORKSPACE_SPEC.md) and its frozen
[acceptance contract](../examples/product/poc-workspace-acceptance-v1.json).
That contract is accepted but not implemented.

ExitSpec is POC-first:

```text
POC
    -> email, meeting transcript, notes, and document sources
    -> explicit named human additions
    -> reviewed requirements
    -> versioned customer agreement
    -> frozen contract
    -> proof runs
    -> Evidence Packs
```

Email and meetings are source types inside one POC, not separate POC products.
The target `/app` surface is a bounded POC dashboard with at most one **Next
up** task, one non-duplicative POC list, and the three filters **Active**,
**Needs attention**, and **Completed**. Unimplemented destinations and actions
remain absent instead of appearing as disabled product promises. **New POC**
becomes the persistent primary action when local creation authority lands. The current
`/app?intake=email` and `/app?mode=recording` entries remain compatibility paths
until the dashboard and create flow reach parity.

The workspace phase `Define`, `Prove`, or `Decide` is a read-only projection for
navigation. It cannot replace or mutate contract lifecycle, customer decision,
run, or verdict state. Agreement status and evidence verdict remain visibly and
semantically distinct.

## Product principles

### Agreement before evidence

The customer-visible requirement must be exact and confirmed before a run can
produce acceptance evidence.

### Missing evidence never passes

Insufficient samples, invalid workload identity, missing metadata, corrupted
artifacts, adapter failure, and statistical inconclusiveness must remain visible
as `BLOCKED` or `NOT_PROVEN`.

### Structured facts, generated claims

Humans edit the supported fields. ExitSpec generates the normalized claim from
those fields so prose and execution cannot contradict each other.

### Models may propose; deterministic systems decide

A model may suggest source-linked candidate facts. It cannot approve, confirm,
freeze, select execution policy, or assign an acceptance verdict.

### Frozen means immutable

Any material change creates another version and another customer decision. A
frozen version is never edited in place.

### Evidence is not authorization

`PASS` answers only the frozen POC acceptance question. It never authorizes
deployment, spend, procurement, production traffic, or policy exceptions.

## Functional requirements

### R0. POC workspace and creation

- Make the POC the stable workspace object, independent of source channel,
  agreement version, proof run, and verdict.
- Let one POC contain multiple provider-neutral sources while preserving exact
  provenance and source authority boundaries.
- Make `/app` the target POC dashboard and `/app/pocs/new` the target guided
  creation flow.
- Require display name, customer label, use case, and owner to create a local
  draft POC; draft creation must not create an agreement, confirmation, freeze,
  measurement, evidence, or provider authorization.
- Offer Email, Meeting or transcript, Notes or document, and Start manually as
  starting-source choices while labeling unavailable integrations honestly.
- Derive the displayed workspace phase and next action from underlying domain
  truth; do not store phase as authority or let a model rank customer priority.
- Preserve the existing guided email, recording, review, and artifact URLs
  during migration.
- Require material source changes after review, confirmation, or freeze to use a
  new agreement version and, when applicable, a new customer decision.
- Restore the frozen graphite/orange visual contract defined in
  `POC_WORKSPACE_SPEC.md`; orange is an action colour, not a `PASS` colour.

### R1. Source capture and supported rule definition

- Ship a deterministic prepared synthetic source and allow pasted synthetic
  notes.
- At `/app?intake=email`, list only **Support-agent requirements** and
  **Untrusted-instructions test** from the manifest-approved catalog.
- Import a selected synthetic RFC822 fixture through deterministic validation,
  normalization, redaction, immutable source finalization, and source-linked
  `NEEDS_REVIEW` proposal projection.
- Expose the catalog through `GET /api/source/fixtures`; accept imports only
  through strict loopback same-origin `POST /api/source/import` exact JSON.
- Preserve reviews on exact replay, require reset before changing samples, and
  lock import after customer review or any later downstream state.
- Return only safe source state and content-free receipts; never expose raw
  RFC822, identities, surrounding instructions, private digests, or replay data.
- Redact pasted notes before they enter returned browser state.
- Create unresolved, source-linked candidates without inventing a measurement
  rule.
- Offer an explicit provider-free assisted action that runs locally against the
  redacted source, recognizes only the supported exact-tool-selection shape,
  and leaves every output `NEEDS_REVIEW`.
- Let a human define or correct title, threshold, minimum samples, and workload
  label for exact tool selection.
- Fix metric, unit, aggregation, adapter, confidence method, and evidence policy
  in trusted server policy.
- Generate the normalized claim server-side and reject client-supplied claim
  text.
- Permit only one executable rule in the current product; preserve other asks as
  context.
- Give email zero authority to approve or reject a proposal, confirm an
  agreement, freeze a contract, create measurement, run proof, or set a verdict.

### R2. Internal review

- Require a named reviewer and rationale for approval or rejection.
- Keep each candidate in `NEEDS_REVIEW` until an explicit decision exists.
- Require every visible candidate to be resolved and at least one rule to be
  approved before customer review.

### R3. Canonical customer confirmation

- Derive rendering and fingerprinting from one canonical customer-visible
  projection.
- Display every bound term: identity, version, customer, use case, target system,
  workload, criteria, owners, non-goals, and retention policy.
- Bind a review capability to contract ID, version, and confirmation
  fingerprint.
- Require server-enforced `agreement_acknowledged=true` for `CONFIRM`.
- Record immutable, idempotent terminal decisions.
- Allow `REQUEST_CHANGES` with rationale without treating it as confirmation.
- Reissue expired pending links and invalidate the old capability.

### R4. Versioning and employee continuity

- Convert a customer change request into a new version with a parent reference.
- Reopen the approved rule for explicit structured revision and review.
- Invalidate stale downstream review, confirmation, freeze, and proof state after
  a revision.
- Poll only while a customer decision is pending and stop on a terminal or
  inactive state.
- Carry the user automatically to the next valid action.

### R5. Freeze and prove

- Freeze only an internally approved contract with a matching affirmative
  customer confirmation and explicit acknowledgement.
- Calculate and verify a canonical contract digest.
- Run only an approved, allowlisted measurement adapter.
- Record manifest, fixture hash, case-level evidence, calculation, verdict, and
  artifact hashes.
- Return `PASS`, `FAIL`, `BLOCKED`, or `NOT_PROVEN` with an exact reason.
- Allow another deterministic reference set to rerun against the same frozen
  contract.
- Never retain a stale prior result when a replacement run fails before proof is
  recorded.

### R6. Evidence handoff

- Render a static POC Acceptance Evidence Pack only from a frozen contract and
  consistency-checked run.
- Put verdict, reason, exact equation, limitation, next action, canonical
  contract hash, and six artifact links in the first viewport.
- Keep seven detailed proof-chain sections collapsed by default.
- Link the contract, evidence index, calculation, verdict, manifest, and hash
  manifest with relative URLs.
- State prominently that evidence is not authorization.

### R7. Demo reliability and distribution

- Provide a query-driven recording mode and deterministic `Restart`.
- Keep the target dashboard and desktop workbench usable at 1280×720 without
  workflow-length body scroll.
- Reflow smaller and zoomed layouts into bounded panel scrolling, and keep the
  customer-facing Evidence Pack separate from the employee workbench.
- Avoid infinite body scrolling, vanity metrics, decorative dashboard cards,
  and channel-specific POC taxonomies.
- Bundle all deterministic demo inputs and browser assets in the wheel.
- Make `exitspec define`, `exitspec demo`, and `exitspec serve` operate outside
  the source checkout.
- Gate Python 3.12 and 3.13, JavaScript syntax, the full suite, and installed-wheel
  behavior in CI.

## Verdict semantics

- `PASS`: sufficient valid evidence establishes the approved rule.
- `FAIL`: sufficient valid evidence establishes that the approved rule was not
  met.
- `BLOCKED`: an attributable external condition prevented a valid measurement.
- `NOT_PROVEN`: evidence is missing, invalid, or statistically inconclusive.

For multi-criterion future work, the current proposed must-have precedence is:

```text
FAIL > BLOCKED > NOT_PROVEN > PASS
```

The current browser and evidence pack intentionally support one criterion, so no
multi-criterion aggregation claim is made.

## Quality bar

The product is accepted when:

- the default public guided synthetic-email script is repeatable from a clean
  server in about 90 seconds;
- the secondary prepared-notes 75-second script remains repeatable from
  `Restart`;
- exact replay preserves review state and the hostile-email sample cannot advance
  any authority-bearing state;
- the optional revision script preserves 95%/200 and ends in a legitimate
  Reference A `PASS`;
- direct confirmation without acknowledgement is rejected;
- stale versions and fingerprints cannot freeze;
- all four verdict classes and exact reasons are tested;
- all six pack links resolve and every artifact listed in the hash manifest
  verifies;
- a built wheel runs outside the repository;
- public copy distinguishes the disabled-by-default, synthetic-only Fireworks
  action from successful live-provider evidence and never implies
  speech-to-text, authenticated customer identity, arbitrary metric execution,
  live email connectivity, or production authorization; and
- frozen Wave 2 contract status is kept separate from post-implementation
  evidence in
  `examples/support-agent/evidence/wave-2-implementation-evidence-v1.json`; and
- the frozen POC workspace acceptance contract remains explicitly
  `contract_only` until dashboard, creation, multi-source, compatibility, visual,
  accessibility, and browser gates have implementation evidence.

## Non-goals for the current product

- Arbitrary customer-controlled provider requests or production-grade provider
  execution.
- Successful real-account Fireworks evidence until the separately approved,
  funded smoke gate passes.
- Speech-to-text or real customer call ingestion.
- Live email connectors, Gmail/Outlook/IMAP access, mailbox OAuth, webhooks,
  arbitrary upload, sending/replying, or real customer email.
- Generic metrics, multi-criterion contracts, latency or cost execution.
- Hosted endpoint measurement or load testing.
- Authenticated customer identity, durable signatures, or durable review storage.
- Multi-tenant authorization, CRM integration, billing, or Kubernetes.
- A completed multi-POC dashboard, create flow, synthetic meeting source, or
  graphite/orange restoration until the POC workspace implementation train
  passes its frozen acceptance contract.
- Automatic deployment, rollback, procurement, or traffic-expansion decisions.

## Principal risks

1. **Authoring friction:** rigorous agreement must remain faster than a
   spreadsheet.
2. **Authority confusion:** customer confirmation, evidence, and production
   approval must never collapse into one status.
3. **Statistical overclaim:** a point estimate must not override the approved
   confidence rule.
4. **Integrity overclaim:** hashes detect later changes; they do not prove the
   original source or execution was honest or complete.
5. **Thin-wrapper perception:** the value must remain visible in versioning,
   trust boundaries, evidence sufficiency, reruns, and inspectable artifacts.
