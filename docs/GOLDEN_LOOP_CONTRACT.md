# Golden Loop Contract: Request → Proof v0.3

Status: frozen for Train A (PR #1–#7)
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
    → safe source record and provenance
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

PR #1 makes this constitution inspectable and characterizes the current generic
seams. It does not claim that the complete v0.3 release statement is already
implemented. `covered`, `partial`, and `characterized_gap` are honest matrix
statuses, not release verdicts.

The stable product object is the POC. Email, notes, meeting transcripts,
documents, and existing ExitSpec contracts are source choices attached to that
POC. A human-added requirement is explicit provenance, not a source type. The
current browser’s **Notes or document** choice is recorded as a compatibility
mapping to the existing `DOCUMENT` boundary until a distinct note vocabulary is
introduced by a versioned contract.

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

## Authority and fail-closed rules

| Boundary | May do | Must never do |
| --- | --- | --- |
| Source adapter or provider | Normalize bounded input and propose source-linked facts | Approve, confirm, freeze, execute, issue evidence, or issue a verdict |
| Internal human | Review, retain, reject, or correct a candidate; define a criterion with rationale | Confirm for the customer or manufacture evidence |
| Customer | Confirm the exact visible version or request changes | Create evidence or authorize production |
| Contract service | Validate lifecycle rules and freeze an exactly confirmed version | Measure or assign a business verdict |
| Approved adapter | Execute an approved plan or import evidence; return typed facts, artifacts, and provenance | Relax policy, select a customer rule, or assign ExitSpec’s verdict |
| ExitSpec verifier | Validate integrity, scope, provenance, sufficiency, and deterministic calculation | Trust a producer verdict or authorize deployment |

The following rules are release-blocking for v0.3:

1. Source material and provider output are untrusted proposal material only.
2. Every executable requirement has exact source provenance or an explicit
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
8. Missing, invalid, stale, tampered, or insufficient evidence never becomes
   `PASS`.
9. `PASS` is evidence about the approved criterion only. It is not deployment,
   procurement, spend, traffic, security, or production authorization.

## Acceptance matrix

The full matrix is kept in the JSON contract so tests and release tooling can
validate it without parsing prose. Its rows are the v0.3 constitution:

| ID | Statement | Current status | Owner |
| --- | --- | --- | --- |
| GL-01 | One stable POC identity spans source attachment and later proof. | covered | PR #2 |
| GL-02 | All supported source types converge through safe, provenance-bound intake. | covered | PR #2 |
| GL-03 | Assisted authoring is schema-bound and review-only. | covered | PR #3 |
| GL-04 | Every candidate receives one of four capability outcomes. | characterized gap | PR #4 |
| GL-05 | Named human review is the proposal-approval boundary. | covered | PR #3 |
| GL-06 | Retained criteria declare scope, rule, population, method, and provenance. | characterized gap | PR #4 |
| GL-07 | Agreements contain only reviewed retained claims. | partial | PR #5 |
| GL-08 | Customer confirmation binds one exact visible version. | covered | PR #5 |
| GL-09 | Change requests create immutable successor versions. | covered | PR #5 |
| GL-10 | Freeze creates an immutable canonical contract after confirmation. | covered | PR #5 |
| GL-11 | Execution/import is bound to the frozen server-owned plan. | partial | PR #6 |
| GL-12 | ExitSpec independently verifies evidence integrity and sufficiency. | partial | PR #6 |
| GL-13 | ExitSpec deterministically calculates the typed verdict. | covered | PR #6 |
| GL-14 | Failed or stale runs cannot retain an earlier PASS as current. | characterized gap | PR #6 |
| GL-15 | Evidence Pack and handoff bind to exact frozen contract and run. | partial | PR #6 |
| GL-16 | UI and release gates expose one source-agnostic, bounded journey. | partial | PR #7 |

The matrix is intentionally not a second implementation. Its `observable` and
`failure_outcome` fields state what a release gate must be able to inspect;
`current_coverage` points to existing or PR #1 characterization tests, while
`target_pr` assigns the remaining implementation work.

## Current characterization boundary

The current repository already proves several reusable generic properties:

- process-local POC creation is identity-only and source-neutral;
- email, meeting, document/note, and existing-contract intake can attach safe
  source records to a POC;
- source-linked candidates remain `NEEDS_REVIEW`;
- assisted-authoring output is schema-bound, redaction-first, source-anchored,
  and authority-free;
- human review, exact confirmation, revision, freeze, deterministic verdicts,
  Evidence Pack projection, and closure have focused adversarial tests.

The characterization tests also keep the remaining gaps visible without making
CI intentionally red:

- `web.py` still composes a seeded support-agent branch and a bounded
  performance branch; this is a known convergence gap, not generic proof;
- there is no four-outcome capability/evidence-method planner yet;
- execution/import, feedback, and Evidence Pack publication are not yet one
  generic source-agnostic orchestration contract;
- the standard browser job does not make every guided/demo and managed evidence
  path mandatory.

These are release-blocking v0.3 gaps and are assigned to PRs #2–#7 in the JSON
contract. They are not silently promoted to “covered” because a seeded demo
journey passes.

## Train A ownership

- **PR #2:** unify email, notes, and meeting intake behind one generic source
  spine and remove seeded-path dependence.
- **PR #3:** complete schema-bound assisted authoring and source-linked human
  proposal review.
- **PR #4:** add capability and evidence-method planning with executable,
  evidence-import, clarification-required, and unsupported outcomes.
- **PR #5:** generalize confirmation, revision, exact-version binding, and
  immutable freeze.
- **PR #6:** generalize evidence orchestration, independent verification,
  deterministic verdict, and feedback/next-action projection.
- **PR #7:** unify UI routes, make browser release checks no-skip, prove clean
  install/distribution, and establish v0.3 readiness.

## Change control and non-goals

This contract does not alter existing frozen identifiers or semantics. In
particular, `PASS`, `FAIL`, `BLOCKED`, `NOT_PROVEN`, and the presentation-only
`NOT_RUN` state remain unchanged. A Wilson lower bound remains a Wilson lower
bound; it is not described here as a confidence interval. Existing evidence and
performance identifiers remain compatibility surfaces.

Train B routing qualification, profile admission, campaign evidence,
confidence-bearing SLO attainment, multi-run reduction, qualification receipts,
Docker/guarded staging, and real Zoom transport are out of scope. This PR also
does not add lifecycle behavior, new verdict rules, a provider integration, a
release tag, deployment, or production authorization.

The executable contract checks are:

```text
python -m pytest tests/test_golden_loop_contract.py tests/test_golden_loop_characterization.py
```

The complete repository engineering gate remains the merge gate for this PR.
