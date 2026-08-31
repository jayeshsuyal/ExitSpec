# ExitSpec v0.5 qualification-gate plan

Status: proposed product and release contract. Implementation has not started.
ExitSpec v0.4.0 remains the immutable released baseline. This document grants
no provider, GPU, deployment, traffic, spending, release-publication, or
production authority. ExitSpec never authorizes deployment or traffic.

## Product decision

ExitSpec v0.5 will deliver one narrow, complete product:

> **Qualify this exact inference change before it receives traffic.**

The long-term destination remains an evidence and acceptance control plane for
LLM inference infrastructure. The v0.5 wedge is the independently verifiable
qualification gate that makes that direction useful without building a
benchmark platform, deployment controller, hosted control plane, and connector
marketplace simultaneously.

The customer question is:

> Does this exact serving subject satisfy this exact frozen requirement and
> workload, what evidence supports the answer, and is that qualification still
> current?

ExitSpec answers that question. It does not decide whether production traffic
is actually sent.

## Released baseline and compatibility

The v0.4.0 tag and public release freeze the current source-neutral
Define -> Confirm -> Prove workbench, bounded Routing Evidence Pack, offline
external-evidence integration, and synthetic Zoom connector boundary. v0.5
is additive and must preserve every v0.4 authority, compatibility, packaging,
browser, and release invariant unless a separate approved migration contract
explicitly replaces one.

Zoom remains a source adapter during this train:

```text
Zoom or meeting transcript
        -> redacted source
        -> review-only proposal
        -> confirmed agreement
```

No v0.5 pull request expands Zoom OAuth, Marketplace publication, live RTMS,
durable meeting storage, customer identity, audio egress, or production claims.
Existing Zoom compatibility and synthetic fixture gates remain required.

## Authority boundary

The most important v0.5 invariant is unchanged from the current product:

> Qualification evidence is not deployment authorization.

| Component | May do | Must never do |
| --- | --- | --- |
| Source or authoring adapter | Propose source-linked requirements | Approve, freeze, measure, judge, or authorize |
| Customer and internal reviewers | Confirm or reject exact visible terms | Manufacture evidence or turn missing proof into a pass |
| ExitSpec capability planner | Determine which observations a claim requires | Infer capability from source prose or authorize execution |
| External evidence producer or custodian | Prepare an evidence package outside this train | Grade the customer contract, be invoked by ExitSpec, or authorize traffic |
| ExitSpec verifier | Admit evidence, recalculate facts, and assign `PASS`, `FAIL`, or `NOT_PROVEN` | Trust a producer summary or change the frozen rule |
| ExitSpec qualification assessment | Determine whether a receipt is current for an exact requested scope | Issue a deployment or canary grant |
| External policy consumer | Consume the assessment under separately configured policy | Rewrite ExitSpec evidence, silently broaden its scope, or gain authority from ExitSpec |

Every ExitSpec qualification artifact must retain explicit zero-authority
fields or limitations equivalent to:

```json
{
  "deployment_authorized": false,
  "production_traffic_authorized": false,
  "traffic_expansion_authorized": false,
  "external_authorization_required": true
}
```

ExitSpec may state that evidence supports consideration for a bounded purpose,
such as a canary of at most 5%. That statement limits the qualification; it does
not grant the canary. ExitSpec never authorizes deployment or traffic.

## End-to-end product loop

```text
Serving subject + qualification scope
                  |
                  v
        canonical context binding
                  |
                  v
        proofability preflight
                  |
       +----------+-----------+
       |                      |
       v                      v
  NOT_PROVABLE          PROVABLE
  stop before spend           |
                              v
                 prospective external handoff
                              |
                              v
           externally supplied evidence package
                              |
                              v
                 ExitSpec admission + recalculation
                              |
                              v
                    PASS / FAIL / NOT_PROVEN
                              |
                              v
                  purpose-bound qualification receipt
                              |
                              v
                    CURRENT / STALE / EXPIRED
                              |
                              v
                 local policy-consumer result
                              |
                              v
                external human/deployment decision
```

The default local and CI path remains provider-free and deterministic. v0.5
does not capture evidence, execute a GPU or provider, spend money, contact an
external producer, or make a cross-repository change. A prospective handoff is
only a canonical local artifact; it is never a dispatch or authorization.

## Core artifact model

### 1. `ServingSubjectManifestV1`

The serving subject is the exact inference system being qualified. The UI may
call it the **candidate**; the schema uses **subject** to distinguish identity
from qualification scope.

The first version binds only fields demonstrated by an admitted profile:

- model ID and pinned revision;
- tokenizer ID and pinned revision;
- engine ID and exact version;
- container, package, or runtime artifact digest where available;
- canonical runtime configuration and launch-argument digest;
- hardware class and topology required by the profile;
- routing-policy identity and digest when the subject includes routing; and
- the profile or adapter identity that defines material fields.

It does not contain the customer workload, threshold, use purpose, verdict,
evidence, run ID, or deployment authorization.

Its `subject_digest` is domain-separated SHA-256 over RFC 8785 JCS canonical
UTF-8 bytes of the complete validated identity object, excluding only the
derived digest field. Unsupported, ambiguous, extra, duplicate, noncanonical,
or unbounded fields fail closed.

### 2. `QualificationScopeV1`

The scope defines the question asked about the serving subject:

- frozen contract identity and canonical hash;
- exact workload identity and digest;
- declared measurement environment or environment profile;
- intended qualification purpose;
- maximum use being evaluated, such as consideration for a 5% canary;
- evidence freshness or expiry policy when the protocol supports it; and
- optional reference-subject identity only when a protocol requires a
  baseline comparison.

Changing the workload or purpose changes `scope_digest`; it does not pretend
that the unchanged serving subject became a different subject.

The same strict canonicalization, bounds, validation, and domain-separated
hashing rules apply to `scope_digest`.

### 3. `QualificationContextV1`

The context binds subject, scope, and qualification protocol without informal
string concatenation:

```json
{
  "schema_version": "exitspec.qualification-context.v1",
  "subject_digest": "sha256:<64 lowercase hex>",
  "scope_digest": "sha256:<64 lowercase hex>",
  "protocol_id": "<versioned protocol identity>",
  "protocol_version": "<exact version>"
}
```

`qualification_context_digest` is domain-separated SHA-256 over this canonical
object. It becomes the common substitution boundary across the prospective
handoff, admitted evidence, receipt, assessment, CLI, and UI.

Self-consistent digests are not proof of execution, authorship, chronology,
hardware truth, or authenticated identity. Those assurances remain explicit
and profile-specific.

### 4. `ProofabilityReportV1`

Proofability is evaluated before any external operation. For every frozen
criterion the report records:

- required observations and metric semantics;
- observations the selected producer/profile can supply;
- missing or incompatible observations;
- selected adapter and profile identities;
- deterministic disposition and stable reason codes;
- remediation or clarification required; and
- zero execution and verdict authority.

Criterion-level dispositions are:

```text
PROVABLE
CLARIFICATION_REQUIRED
NOT_PROVABLE
```

`PARTIALLY_PROVABLE` may describe a multi-criterion plan whose criteria have
different dispositions. It is never a criterion verdict and can never be
converted into partial acceptance.

The first required negative case is the existing semantic/native TTFT boundary:
a first-nonempty-content criterion must be `NOT_PROVABLE` when the selected
producer profile exposes only native first-event TTFT.

### 5. protocol-specific qualification receipt

The first implementation is
`InferencePerformanceQualificationReceiptV1`. It wraps but never replaces the
applicable performance evidence admission and verdict protocol.

Issuance requires the original:

- serving subject;
- qualification scope and context;
- frozen contract and affirmative exact-version confirmation;
- admitted canonical evidence bundle;
- independently recalculated result; and
- verifier/profile identities.

The receipt binds their canonical digests, evidence class, evidence-set
identity, verdict, limitations, issue time, and any supported freshness facts.
Result-only input, producer verdicts, digest-only self-consistency, rewritten
evidence, mixed evidence classes, or a different context cannot issue or
validate a receipt.

Protocol-specific receipts remain separate. v0.5 does not replace or silently
generalize the frozen B12 routing receipt. A small common assessment layer may
consume validated receipts only through an explicit typed protocol adapter.

### 6. `QualificationAssessmentV1`

The assessment compares a fully validated receipt with the currently requested
subject, scope, context, time, and use. It preserves three independent axes:

| Axis | Values | Meaning |
| --- | --- | --- |
| Proofability | `PROVABLE`, `CLARIFICATION_REQUIRED`, `NOT_PROVABLE` | Whether execution can establish the criterion |
| Evidence verdict | `PASS`, `FAIL`, `NOT_PROVEN` | What admitted evidence establishes |
| Validity | `CURRENT`, `STALE`, `EXPIRED`, `INVALID` | Whether that verdict remains applicable now |

Examples:

- measurable criterion with sufficient evidence above the allowed threshold:
  `PROVABLE` + `FAIL` + `CURRENT`;
- producer lacks the required observation: `NOT_PROVABLE`, with no execution
  or receipt;
- incomplete evidence: `PROVABLE` + `NOT_PROVEN` + `CURRENT`;
- engine digest changes after a valid receipt: original verdict remains
  inspectable, but validity becomes `STALE` for the new context; and
- freshness deadline passes: original verdict remains inspectable, but
  validity becomes `EXPIRED`.

The assessment may expose a derived machine outcome for a local policy consumer,
but it must retain the underlying axes. It is not named or represented as a
canary or deployment grant. ExitSpec never authorizes deployment or traffic.

## Initial product wedge

The first supported vertical is provider-neutral. It defines one frozen
qualification method and a local admission boundary rather than introducing a
runtime matrix, an evidence producer integration, or an execution service.

The default product story is:

- one exact pinned synthetic serving-subject fixture;
- one fixed immutable workload;
- one native nearest-rank p95 TTFT criterion and its existing reliability rule;
- one declared producer-capability profile;
- one explicitly synthetic CI fixture or manually supplied external evidence
  package at the local admission boundary;
- one independently recalculated ExitSpec verdict;
- one qualification assessment scoped to consideration for at most a 5%
  canary; and
- one material subject or workload mutation that makes the prior
  qualification stale.

The existing strict under-20 ms, strict under-10 ms, and semantic
first-nonempty-content prospective cases remain useful conformance cases. No
future measurement or verdict may be invented to make their story convenient.

## Proposed artifact flow

```text
subject.json
scope.json
context.json
proofability.json
        |
        v
prospective-handoff/
        |
        v
external-evidence-package/     # untrusted, externally supplied input
        |
        v
qualification-receipt.json
qualification-assessment.json
        |
        v
CLI JSON + local policy-consumer result
```

Raw customer source, prompts, generated response content, secrets, credentials,
provider bodies, private paths, and deployment tokens must not enter public
receipts, assessments, check output, logs, or errors.

## ExitSpec pull-request train

The train is organized around independently reviewable invariants. The count is
a planning estimate, not a target to inflate or compress. A pull request may be
split when a trust boundary cannot be reviewed safely together; adjacent work
may be combined only when the same exit gate proves it.

### PR1 — Architecture, vocabulary, and threat contract

Claim: the product question, authority boundary, object ownership, status axes,
canonicalization, initial wedge, non-goals, and threat model are explicit.

Exit gate: contradictory authority or identity interpretations are resolved in
the specification or an ADR before executable code lands.

### PR2 — Serving-subject identity

Claim: one immutable typed subject has strict validation, canonical bytes, a
domain-separated digest, golden identity, and complete per-field mutation
coverage.

Exit gate: every material field mutation changes identity; malformed,
duplicate, extra, oversized, unsupported, and noncanonical input fails closed.

### PR3 — Qualification scope and context

Claim: workload and use scope are independent from subject identity but bound
together by one canonical qualification context.

Exit gate: subject drift and scope drift are distinguishable and both produce a
different context without ambiguous concatenation or legacy identity changes.

### PR4 — Producer capability descriptor

Claim: a server-owned, versioned descriptor states exactly which observations
and metric semantics a declared external-evidence profile can provide.

Exit gate: source text, provider output, or browser input cannot forge or expand
capability.

### PR5 — Proofability engine

Claim: ExitSpec deterministically maps each frozen criterion to required,
available, missing, and incompatible observations before execution.

Exit gate: native TTFT is provable under the admitted profile, while unsupported
semantic first-nonempty TTFT stops before any external operation.

### PR6 — Proofability service and workspace projection

Claim: the current capability-planning lifecycle can create, retrieve, and
present a context-bound proofability report without creating an agreement,
execution, verdict, or authority side effect.

Exit gate: API, replay, stale-plan, cross-POC, redaction, and browser behavior
are deterministic and preserve all v0.4 routes.

### PR7 — Provider-neutral prospective handoff boundary

Claim: ExitSpec can create and verify one canonical, provider-neutral
prospective handoff that carries the exact qualification context and declared
observation requirements without adding run IDs, measurements, credentials,
request-plan digests, evidence bundles, or verdicts.

Exit gate: a handoff with changed subject, scope, context, contract, workload,
or profile identity fails closed, and a valid handoff performs no dispatch,
execution, provider call, spend, cross-repository change, or authority action.

### PR8 — Provider-neutral external-evidence admission boundary

Claim: ExitSpec safely admits one exact context-bound external evidence package
from a local untrusted-input boundary, verifies its declared provenance and
integrity, and independently recalculates supported facts without contacting
the producer.

Exit gate: cross-context, cross-contract, synthetic-as-real, corrupt,
noncanonical, unsafe-tree, unsupported-profile, and producer-verdict inputs are
rejected before verdicting; admission does not execute, spend, dispatch, or
authorize deployment or traffic.

### PR9 — Inference performance qualification receipt

Claim: a protocol-specific immutable receipt can issue only from the original
validated context and evidence authority path.

Exit gate: `PASS`, `FAIL`, and `NOT_PROVEN` remain distinct; ingestion rejection
issues nothing; receipt-only or digest-only validation cannot establish trust.

### PR10 — Qualification validity and staleness

Claim: ExitSpec can determine whether a validated receipt remains current for a
requested subject, scope, purpose, and time without mutating historical facts.

Exit gate: every material subject/scope mutation, expiry boundary, unsupported
protocol, and malformed context produces the exact fail-closed validity state.

### PR11 — Qualification CLI

Claim: a noninteractive command validates the complete context and emits stable,
content-safe machine-readable output and exit behavior.

Proposed interface:

```bash
exitspec qualification check \
  --subject subject.json \
  --scope scope.json \
  --receipt qualification-receipt.json \
  --json
```

Exit gate: exit code `0` is reserved for a current `PASS` covering the exact
requested scope. `FAIL`, `NOT_PROVEN`, `STALE`, `EXPIRED`, `INVALID`, malformed
input, and operational failure remain machine-distinguishable and nonzero.

### PR12 — Policy-consumer compatibility contract

Claim: a minimal documented local policy-consumer contract can consume CLI
output without receiving deployment credentials, calling an evidence producer,
or taking a deployment action.

Exit gate: current exact-scope `PASS` succeeds; failure, missing proof,
staleness, expiry, tampering, or skipped required evaluation cannot silently
produce a passing consumer result. A passing result remains evidence only, never
a deployment or traffic grant.

### PR13 — Guided four-screen product surface

Claim: the existing `/app` shell presents the end-to-end qualification story
without a new dashboard maze or workflow-length body scroll.

Required states:

1. exact serving subject and qualification scope;
2. proofability and missing observations before execution;
3. admitted evidence and independent verdict; and
4. current qualification followed by one clearly labeled deterministic
   material mutation that makes it stale.

Exit gate: a first-time tester can explain the product and the authority
boundary after a two-minute guided run at 1280x720 and accessible narrower
layouts.

### PR14 — Adversarial closure and v0.5 candidate checkpoint

Claim: the complete train is packaged, documented, reproducible, and honest
under the v0.5 engineering and browser gates, ready for independent review.

Exit gate: all focused, engineering, installed-wheel, browser, adversarial,
documentation, and candidate-state checks are green locally. Tagging, release
publication, deployment, traffic, real capture, and cross-repository conformance
are not v0.5 train work.

## Parallel execution lanes

PR1 is the shared entry gate. After its vocabulary is accepted, three lanes may
advance with bounded dependencies:

```text
Truth kernel
  PR2 -> PR3 -> PR4 -> PR5 -> PR9 -> PR10 -> PR11

External-evidence boundary
  PR3 -> PR7 -> PR8 -> PR9

Product surface
  PR6 -----------------> PR12 -> PR13

All lanes
  -----------------------------> PR14
```

The truth kernel is merge authority for status and identity. The UI never
derives its own decision. An external evidence package never assigns the
customer verdict. No PR broadens qualification into deployment or traffic
permission.

## Demo contract

The v0.5 seeded demo has four screens and one primary action per screen:

1. **What is being qualified?** Show the exact candidate identity, workload,
   frozen requirement, and bounded intended use.
2. **Can it be proven?** Show required, available, and missing observations;
   stop an unsupported criterion before spend.
3. **What does the evidence establish?** Show admitted evidence identity,
   independently recalculated facts, verdict, limitations, and receipt.
4. **Is the qualification current?** Show current exact-context status, then
   mutate one material engine or workload field and visibly require
   requalification.

The mutation is deterministic synthetic demonstration behavior and must be
labeled as such. It may not alter an immutable historical receipt or imply that
a production system was changed.

## Adversarial and security matrix

At minimum, the release train must prove fail-closed behavior for:

- duplicate, extra, missing, wrong-version, wrong-type, oversized, malformed,
  and noncanonical JSON;
- digest self-consistency without original context or evidence;
- subject, scope, contract, workload, profile, reducer, and evidence
  substitution;
- changed subject fields hidden behind an unchanged label;
- producer-supplied verdict or authorization fields;
- synthetic evidence relabeled as external or real;
- incomplete populations, failed requests, missing repetitions, and unsupported
  metric semantics;
- stale, expired, future-dated, and malformed time boundaries;
- symlinks, hard links, path escapes, extra files, replacement races, and
  oversized evidence trees;
- raw customer content, prompts, response content, credentials, private paths,
  or provider bodies entering receipts, errors, logs, browser state, or checks;
- policy-consumer checks that skip a required qualification and accidentally
  report success; and
- any UI, API, CLI, receipt, or assessment path implying deployment authority.

## Candidate-closure gate

### Engineering candidate gate

The ExitSpec-only v0.5 candidate is ready for independent review when:

- all 14 ExitSpec PR invariants are merged and green;
- required provider-neutral synthetic fixtures are immutable and independently
  verified;
- the complete deterministic synthetic loop works from installed package;
- required Python and Chromium collections have zero failures, errors, or
  required skips;
- v0.4 compatibility, Zoom synthetic, security, packaging, and documentation
  gates remain green;
- the public README and demo label synthetic, local, process-local,
  unauthenticated, non-durable, and non-authorizing behavior accurately; and
- the local candidate commit is available for Mission Control and independent
  MTS review. This gate neither publishes a release nor grants authority to
  conduct a real capture or external conformance activity.

## Business validation running in parallel

Before a hosted control plane, direct deployment integration, or broad adapter
matrix, conduct at least five customer-discovery sessions with inference
platform, model-serving, solutions-engineering, or AI infrastructure owners.
Ask each team to show the actual artifact and approval path used for its most
recent serving change.

The strongest continuation signal is at least two teams willing to evaluate the
CLI or required check on a real internal change. If teams consistently accept
their existing benchmark scripts and rollout thresholds, revisit the wedge
before building hosted infrastructure.

## Explicit non-goals for v0.5

- issuing a canary, deployment, shipping, release, procurement, spending, or
  traffic grant;
- directly changing GitHub protection, Kubernetes resources, Argo rollout
  state, router weights, provider configuration, or production traffic;
- any named external evidence-producer integration, GPU execution, provider
  spending, real capture, or cross-repository change;
- a Kubernetes admission controller, GitHub App, automatic rollback, or
  continuous runtime monitor;
- hosted identity, multi-tenancy, durable enterprise storage, signing keys, or
  a custom attestation system;
- universal metric execution, free-text metric semantics, or every inference
  runtime, GPU, provider, and router;
- a new benchmark leaderboard, generic LLM application-eval platform, or
  observability dashboard;
- live Zoom expansion, customer audio, provider spend, or marketplace work; and
- claims of cryptographic proof for physical hardware, chronology, authorship,
  or facts the admitted evidence cannot establish.

## Rough delivery expectation

The schedule is an estimate, not authority to skip gates:

- days 1-3: architecture, subject, scope, and context;
- days 4-7: capability descriptor, proofability, and workspace projection;
- days 8-11: prospective handoff boundary, evidence admission, receipt, and
  validity;
- days 12-14: CLI, policy-consumer contract, and guided UI; and
- days 15-18: adversarial closure, documentation, and candidate readiness.

A working local vertical slice should appear before the train reaches candidate
closure. The project remains `NOT_READY` until every candidate gate required for
the claim being made has passed.

## Definition of done

The v0.5 product is complete when a first-time tester can:

1. identify the exact serving subject and frozen qualification scope;
2. see an unsupported claim rejected before execution;
3. admit an unchanged synthetic or externally supplied evidence package through
   the local boundary;
4. inspect independently recalculated facts and a typed verdict;
5. validate a purpose-bound, zero-authority receipt;
6. obtain a current qualification assessment for the exact context;
7. change one material subject or scope field and see the receipt become stale
   for the new context;
8. observe the corresponding local policy-consumer result block without any
   deployment action; and
9. explain that only an external human or deployment system may authorize
   traffic.

That complete loop—not the number of schemas, pull requests, dashboards, or
integrations—is the v0.5 product.
