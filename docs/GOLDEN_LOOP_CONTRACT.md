# Golden Loop Contract: Request → Proof v0.3

Status: frozen for Train A slices A1–A7
Contract ID: `request-to-proof-golden-loop-v0.3`
Contract version: `1.0.0`
Release target: `v0.3.0`

The machine-readable acceptance matrix is
[`examples/product/request-to-proof-acceptance-v1.json`](../examples/product/request-to-proof-acceptance-v1.json).
This document explains the decision and the boundaries that the matrix makes
executable.

## Decision

ExitSpec’s release constitution is one source-agnostic Request → Proof spine:

```text
request source
    → safe source record and declared provenance
    → source-linked candidate proposals
    → capability and evidence-method classification
    → named human review
    → exact criterion and measurement/evidence plan
    → exact-version customer confirmation or revision
    → immutable frozen contract
    → approved execution or evidence import
    → independent evidence verification
    → deterministic PASS / FAIL / BLOCKED / NOT_PROVEN
    → Evidence Pack, limitation, and next human action
```

Train A slice A1 makes this constitution inspectable and characterizes the
current generic seams. It does not claim that the complete v0.3 release
statement is already implemented. `covered`, `partial`,
`characterized_gap`, and `unverified_gap` are A1 baseline labels, not release
verdicts.

The stable product object is the POC. Email, meeting transcripts, documents,
and existing ExitSpec contracts are source choices attached to that POC. A
human-added requirement has declared provenance, not a source type. The current
browser’s **Notes or document** choice is an explicit `notes → DOCUMENT`
UI/input alias; `NOTE` is not an independent domain source kind in v0.3.

## Normative vocabulary

The acceptance contract recognizes these capability outcomes:

- `EXECUTABLE`: a complete criterion can be run by an approved adapter.
- `EVIDENCE_IMPORT`: a complete criterion can be evaluated from an admitted
  external evidence bundle.
- `CLARIFICATION_REQUIRED`: the request is meaningful but lacks a testable
  rule, population, method, or other required decision.
- `UNSUPPORTED`: the request is outside current ExitSpec capability or policy.

Agreement states remain `DRAFT`, `IN_REVIEW`, `APPROVED`, `FROZEN`, and the
reserved `SUPERSEDED` state. Evidence presentation may show `NOT_RUN`; terminal
evidence outcomes remain `PASS`, `FAIL`, `BLOCKED`, and `NOT_PROVEN`. Existing
identifiers and meanings are preserved. New semantics require an additive,
explicitly versioned schema or identifier.

The state dimensions are intentionally separate:

- `RunStatus` / operation state includes `RUNNING`; it describes process
  progress and is never an evidence presentation state or acceptance verdict.
- Workspace evidence presentation is `NOT_RUN` or one terminal acceptance
  verdict. `NOT_RUN` is presentation-only.
- External-evidence ingestion has an `ADMITTED` or `INGESTION_REJECTED`
  disposition. Invalid, corrupt, unsafe, unsupported, or incompatible input is
  `INGESTION_REJECTED` with no acceptance verdict.
- ExitSpec acceptance is exactly `PASS`, `FAIL`, `BLOCKED`, or `NOT_PROVEN`.
  A valid recognized evidence profile that is insufficient or inapplicable is
  admitted and may become `NOT_PROVEN`.

For a retained mixed request, every claim remains visible with one planner
disposition and one scope: `MUST_HAVE` or `ADVISORY`. A `CLARIFICATION_REQUIRED`
must-have blocks customer-ready confirmation and freeze until resolved or
explicitly excluded by a new reviewed version. An `UNSUPPORTED` claim cannot
enter an executable criterion; it remains an excluded or unproven limitation
with rationale. A6 must implement the policy that all frozen must-haves must
pass for overall `PASS`, any mandatory `FAIL` yields `FAIL`, absent or
insufficient supported evidence yields `NOT_PROVEN`, and an external
operational blocker yields `BLOCKED`. A1 freezes this policy only; it adds no
runtime reducer.

## Authority and fail-closed rules

| Boundary | May do | Must never do |
| --- | --- | --- |
| Source adapter or provider | Normalize bounded input and propose source-linked facts | Approve, confirm, freeze, execute, issue evidence, or issue a verdict |
| Internal human | Review, retain, reject, or correct a candidate; define a criterion with rationale | Confirm for the customer or manufacture evidence |
| Customer | Confirm the exact visible version or request changes | Create evidence or authorize production |
| Contract service | Validate lifecycle rules and freeze an exactly confirmed version | Measure or assign a business verdict |
| Approved adapter | Execute an approved plan or import evidence; return typed facts, artifacts, and declared provenance | Relax policy, select a customer rule, or assign ExitSpec’s verdict |
| ExitSpec verifier | Validate integrity, declared provenance bindings, scope, sufficiency, and deterministic calculation | Trust a producer verdict or authorize deployment |

Process-local reviewer and confirmer labels establish the recorded decision
boundary only. They are not authenticated person or organization identity proof,
and local capability links are not durable hosted authorization. Hosted identity
is outside v0.3. Exact confirmation/fingerprint requirements remain unchanged.

The following rules are release-blocking for v0.3:

1. Source material and provider output are untrusted proposal material only.
2. Every executable requirement has exact declared source provenance or an explicit
   named human addition with rationale.
3. Unknown fields, authority-bearing output, unsafe source, stale decisions,
   conflicting identity, and tampered artifacts fail closed without silently
   dropping the affected request.
4. Unsupported or insufficient claims remain visible as
   `CLARIFICATION_REQUIRED`, `UNSUPPORTED`, or `NOT_PROVEN`; they are never
   invented as executable criteria.
5. Customer confirmation binds the exact visible contract identity, version,
   and fingerprint. A request for changes creates a successor version and does
   not mutate its predecessor.
6. A frozen contract is immutable. Proof and evidence are bound to that exact
   frozen version.
7. ExitSpec independently recalculates the verdict from verified facts. A
   producer-generated verdict is never accepted as ExitSpec’s verdict.
8. External invalid, corrupt, unsafe, unsupported, or incompatible evidence is
   `INGESTION_REJECTED` with no acceptance verdict; a valid recognized but
   insufficient or inapplicable exact profile may become `NOT_PROVEN`.
9. Missing, invalid, stale, tampered, or insufficient evidence never becomes
   `PASS`.
10. `PASS` is evidence about the approved criterion only. It is not deployment,
   procurement, spend, traffic, security, or production authorization.

## Acceptance matrix

The full matrix is kept in the JSON contract so tests and release tooling can
validate it without parsing prose. Its rows are the v0.3 constitution:

| ID | Statement | A1 baseline status | Owner |
| --- | --- | --- | --- |
| GL-01 | One stable POC identity spans source attachment and later proof. | partial | A2 |
| GL-02 | All supported source types converge through safe, declared-provenance intake. | partial | A2 |
| GL-03 | Assisted authoring is schema-bound and review-only. | covered | A3 |
| GL-04 | Every candidate receives one of four capability outcomes. | characterized_gap | A4 |
| GL-05 | Human review is the proposal-retention and triage boundary. | partial | A3 |
| GL-06 | Retained criteria declare scope, rule, population, method, and declared provenance. | characterized_gap | A4 |
| GL-07 | Agreements contain only reviewed retained claims. | partial | A5 |
| GL-08 | Customer confirmation binds one exact visible version. | covered | A5 |
| GL-09 | Change requests create immutable successor versions. | covered | A5 |
| GL-10 | Freeze creates an immutable canonical contract after confirmation. | partial | A5 |
| GL-11 | Execution/import is bound to the frozen server-owned plan. | partial | A6 |
| GL-12 | ExitSpec independently validates evidence integrity and declared-provenance bindings. | partial | A6 |
| GL-13 | ExitSpec deterministically calculates the typed verdict. | partial | A6 |
| GL-14 | Failed or stale runs cannot retain an earlier PASS as current. | unverified_gap | A6 |
| GL-15 | Evidence Pack and handoff bind to exact frozen contract and run. | partial | A6 |
| GL-16 | UI and release gates expose one source-agnostic, bounded journey. | partial | A7 |

The matrix is intentionally not a second implementation. Its `observable` and
`failure_outcome` fields state what a release gate must eventually inspect.
`a1_baseline_status` and `a1_baseline_coverage` are immutable A1 baseline
fields: the latter contains trace/characterization anchors only, not release
closure evidence. An anchor proves only that a named test function exists; it
does not prove that the test is collected, unskipped, or semantically
sufficient. Later slices do not edit this baseline in place; they emit closure
evidence separately or version the constitution. `target_train_slice` assigns
the remaining implementation work.

The v1 JSON has an exact top-level field set and an exact row field set. Rows
must appear in canonical GL-01 through GL-16 order, references are unique and
bounded, and unknown or additive fields are rejected under this schema version.
The frozen byte sequence is SHA-256 pinned by the A1 integrity tests. A7 owns
the no-skip executable closure ledger/gate that must collect and execute every
required node and reject skip/xfail substitution; A1 trace anchors do not do
that job.

## Current characterization boundary

The current repository already has tests for several reusable generic properties:

- process-local POC creation is identity-only and source-neutral;
- email, meeting, document, and existing-contract intake can attach safe
  source records to a POC;
- source-linked candidates remain `NEEDS_REVIEW`;
- assisted-authoring output is schema-bound, redaction-first, source-anchored,
  and authority-free;
- human review, exact confirmation, revision, freeze, deterministic verdicts,
  Evidence Pack projection, and closure have focused adversarial tests.

These are A1 trace points, not a release-closure ledger. The trace tests do not
claim complete semantic coverage, collection, or no-skip execution. In
particular, GL-14 remains `unverified_gap` because the A1 trace does not prove
that a failed or stale replacement cannot inherit an earlier PASS.

The characterization tests also keep the remaining gaps visible without making
CI intentionally red:

- `web.py` still composes a seeded support-agent branch and a bounded
  performance branch; this is a known convergence gap, not generic proof;
- there is no four-outcome capability/evidence-method planner yet;
- execution/import, feedback, and Evidence Pack publication are not yet one
  generic source-agnostic orchestration contract;
- the standard browser job does not make every guided/demo and managed evidence
  path mandatory.

These are release-blocking v0.3 gaps and are assigned to Train A slices A2–A7
in the JSON contract. They are not silently promoted to “covered” because a
seeded demo journey passes.

## Train A slice ownership

- **A2:** unify email, notes-as-DOCUMENT, and meeting intake behind one generic source
  spine and remove seeded-path dependence.
- **A3:** complete schema-bound assisted authoring and source-linked human
  proposal review.
- **A4:** add capability and evidence-method planning with executable,
  evidence-import, clarification-required, and unsupported outcomes.
- **A5:** generalize confirmation, revision, exact-version binding, and
  immutable freeze.
- **A6:** generalize evidence orchestration, independent verification, mixed-claim
  reduction policy, deterministic verdict, and feedback/next-action projection.
- **A7:** unify UI routes, add the no-skip executable closure ledger/gate for
  every required node, prove clean install/distribution, and establish v0.3
  readiness.

## Change control and non-goals

This contract does not alter existing runtime schemas, frozen identifiers, or
semantics. It introduces one additive frozen Train A acceptance schema and
constitution. In particular, `RUNNING` remains an operation/RunStatus value,
while `PASS`, `FAIL`, `BLOCKED`, `NOT_PROVEN`, and the presentation-only
`NOT_RUN` state remain unchanged. `INGESTION_REJECTED` is an ingestion
disposition with no acceptance verdict. A Wilson lower bound remains a Wilson
lower bound; it is not described here as a confidence interval. Existing
evidence and performance identifiers remain compatibility surfaces.

The external-evidence distinction is intentionally narrow: invalid, corrupt,
unsafe, unsupported, or incompatible input is `INGESTION_REJECTED` with no
verdict. A valid recognized exact profile that is insufficient or inapplicable
is admitted evidence and may become `NOT_PROVEN`; it is never substituted into
another criterion. Hashes and declared provenance/integrity bindings do not
prove authorship, truthful execution, endpoint identity, or hardware identity.

Process-local reviewer/confirmer labels and local capability links establish the
recorded decision boundary only. They are not authenticated person,
organization, or durable hosted authorization proof. Hosted identity is outside
v0.3, while exact confirmation and fingerprint requirements remain mandatory.

Train B routing qualification, profile admission, campaign evidence,
confidence-bearing SLO attainment, multi-run reduction, qualification receipts,
Docker/guarded staging, and real Zoom transport are out of scope. The A1 slice also
does not add lifecycle behavior, a runtime reducer, new verdict rules, a
provider integration, a release tag, deployment, or production authorization.

The B9 protocol-only routing qualification slice is documented in
[ROUTING_QUALIFICATION_PROTOCOL.md](ROUTING_QUALIFICATION_PROTOCOL.md). It
freezes a future campaign vocabulary without claiming that routing qualification
is executable or proven.

The executable contract checks are:

```text
python -m pytest tests/test_golden_loop_contract.py tests/test_golden_loop_characterization.py
```

The complete repository engineering gate remains the merge gate for this PR.
