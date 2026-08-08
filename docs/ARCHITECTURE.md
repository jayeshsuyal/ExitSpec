# Architecture

## Scope and governing invariant

ExitSpec owns the agreement and evidence chain around an AI infrastructure POC.
The current implementation deliberately proves one vertical slice: an exact
support-tool-selection proportion rule over a fixed 200-case synthetic fixture.
Wave 2 adds a bounded synthetic-email source path to that slice; it does not add
a mailbox integration or another measurement type.

```text
employee-selected manifest-approved synthetic email
        |
        v
deterministic validation, normalization, and redaction
        |
        v
immutable source envelope + source-linked NEEDS_REVIEW proposals
        |
        +-- named employee approves, rejects, or defines the supported rule
        v
canonical customer-visible agreement
        |
        v
explicit customer decision bound to its fingerprint
        |
        v
immutable frozen contract + canonical hash
        |
        v
measurement facts + hashed artifacts
        |
        v
deterministic verdict
        |
        v
POC Acceptance Evidence Pack
```

Missing, invalid, or insufficient evidence never becomes `PASS`. Agreement,
measurement, verdict, and business authorization are separate authorities.

## Implemented local POC workspace

The implemented local product owns one process-scoped demo session. Its
workspace architecture is defined in
[POC_WORKSPACE_SPEC.md](POC_WORKSPACE_SPEC.md) and frozen in
[`poc-workspace-acceptance-v1.json`](../examples/product/poc-workspace-acceptance-v1.json).
The acceptance file remains an immutable historical contract, including its
pre-implementation status block. The separate
[workspace implementation evidence](../examples/product/poc-workspace-implementation-evidence-v1.json)
maps every frozen gate to executable positive and adversarial proof.

`workspace.py` provides immutable registry entries, strict workflow facts,
deterministic POC projections, bounded filters, and continue-card ordering;
`/app` renders that projection as the read-only POC dashboard. Draft creation,
source capture, proposal review, criterion definition, customer agreement,
freeze, proof, Evidence Pack history, and closure delegate to their dedicated
domain services rather than giving the dashboard write authority.

The workspace introduces a POC aggregate above the existing source, agreement,
run, and evidence services:

```text
POC workspace projection
        |
        +-- POC identity and ownership metadata
        +-- zero or more provider-neutral sources
        +-- one active draft or review agreement version
        +-- immutable historical agreement versions
        +-- proof runs bound to exact frozen versions
        +-- run-scoped Evidence Packs
```

The POC ID is stable across source additions, agreement revisions, proof reruns,
and verdict changes. It is not a contract ID, review token, or run path.

`Define`, `Prove`, and `Decide` are derived workspace phases. The projection
calculates phase, next human action, blockers, source summary, and latest
evidence summary from underlying state. It has no write authority. Inconsistent
state produces a visible blocker rather than a guessed phase.

The target route ownership is:

```text
/app                         POC dashboard
/app/pocs/new                local draft creation
/app/pocs/{poc_id}           one POC workbench
/app/pocs/{poc_id}/sources   source history and add-source entry
/review/{token}              existing exact-version customer review
/artifacts/{run_path}        existing static evidence boundary
```

`/app?intake=email` and `/app?mode=recording` remain compatibility entries that
open the seeded workbench directly. The dashboard opens the same workbench at
`/app/pocs/poc_support_agent_demo`; current review and artifact URLs are
unchanged. The bundled frozen inference-performance agreement has a guided
detail route at `/app/pocs/poc_inference_latency_demo`. It exposes explicit
readiness and proof controls only after the agreement gate is satisfied, and
keeps evidence at `NOT RUN`, `BLOCKED`, or `NOT_PROVEN` until verified state can
be projected.

The first registry may remain explicitly local and process-scoped. Durable POC
storage, authenticated workspace identity, tenant isolation, and real-customer
source remain behind the real-customer trust gate.

The current `DemoSession` adapts its existing synthetic state into the dynamic
`poc_support_agent_demo` projection. The bounded
`/api/workspace?filter=...` endpoint combines it with one immutable,
resource-validated `poc_inference_latency_demo` projection. The support-agent
projection derives timestamps from existing domain records rather than reading
the clock and preserves one POC identity when its guided source changes from
transcript to email. Both projections use the existing contract, confirmation,
run, and verdict enums. Missing or contradictory facts become typed visible
blockers and reset the navigation phase to `Define`; the projection and
dashboard never repair or advance underlying state.

The dashboard has no dead destinations or unavailable actions, at most one
**Continue working** card, one finite list, and only the accepted Active, Needs
attention, and Completed filters. **New POC** creates only a process-local,
authority-free draft. Durable persistence, authenticated identity, live
mailbox/meeting connections, and new external authority remain excluded.

## Authority boundaries

| Boundary | Authority | Explicitly excluded |
| --- | --- | --- |
| Intake and assisted authoring | Redact source and propose source-linked facts | Approval, confirmation, freeze, adapter policy, verdict |
| Internal human review | Approve, reject, or correct one proposed requirement | Customer confirmation and evidence |
| Customer review | Confirm the exact visible agreement or request changes | Evidence creation and production authorization |
| Contract service | Validate lifecycle, freeze the confirmed version, calculate its digest | Measurement and business decision |
| Measurement adapter | Return typed facts and evidence artifacts | Acceptance verdict |
| Verdict engine | Apply the frozen deterministic rule | Deployment, spend, or procurement authorization |
| Evidence renderer | Present verified inputs, calculation, limits, and artifacts | Expand the scope of what was proved |

Future evidence produced by Inferdrome or another external system follows the
[External Evidence Protocol](EXTERNAL_EVIDENCE_PROTOCOL.md). The producer may
create measurements, native artifacts, canonical records, and provenance
claims. ExitSpec independently validates and recalculates admitted evidence and
remains the only acceptance-verdict authority. An invalid or incompatible
external bundle is rejected before verdicting; a producer verdict is never an
ExitSpec verdict.

The domain core does not import a frontend framework or provider SDK. The browser
calls a loopback HTTP boundary, and the server delegates to the same typed domain
services used by the CLI.

## Speech-to-text boundary, transport, and handoff

`stt_boundary.py` implements a provider-neutral, synthetic-only policy seam.
One exact consent attestation and bounded
audio metadata intent are evaluated against a reviewed provider, model, region,
zero-retention, recording-notice, deletion, incident-response, media-type,
byte, duration, and time-window policy.

```text
reviewed STT policy + consent attestation + audio metadata
        |
        v
typed fail-closed evaluation
        |
        +-- denial: content-free code + one next action
        |
        +-- allow: safe policy-match record
                   transport_capability_issued = false
```

Raw audio is absent from the policy contract and no network I/O occurs there. A
provider transcript is represented only as a private, non-serializable,
request-local object with `UNTRUSTED_SOURCE_ONLY` authority and `NEEDS_REVIEW`
state. The sole explicit content path is the immediate redaction handoff in
`stt_handoff.py`, which may turn redacted text into the existing
provider-neutral `MEETING` source.

Provider speaker labels remain `PROVIDER_ASSIGNED_UNVERIFIED`; they do not prove
participant identity. Public STT receipts contain hashes, counts, provider
configuration, and redaction provenance but no audio, transcript text,
participant IDs, or raw meeting ID.

The full contract and five-slice delivery sequence are specified in
[STT_SPEC.md](STT_SPEC.md). Real customer audio remains behind the C4 production
security gates.

The bounded operation layer adds `STTAudioPermitIssuer` and
`STTOperationExecutor`. The issuer revalidates the policy intent, binds exact
immutable synthetic bytes by length and SHA-256, and refuses duplicate permit
issuance without storing audio. Its content-free issuance set is bounded and
fails closed at capacity. The private permit detaches its bytes when consumed.
The executor is disabled by default, accepts only that permit, exposes the
reviewed transport timeout, releases the request's audio reference after one
transport attempt, and performs no automatic retry.

Provider-runtime output must survive local request-ID, language, speaker-mode,
segment-shape, ordering, and audio-duration validation before it becomes a
private `UntrustedSTTTranscript`. The separately serializable operation receipt
contains content-free provenance only. Fake transports prove the seam. An
explicit `--enable-fireworks-stt` composition adds one pinned, single-attempt
Fireworks Whisper v3 HTTPS adapter and the browser provides one consent-bound,
bounded WebM upload route for synthetic demo audio. Endpoint/model choice and
automatic audio retries remain unavailable, and no funded live success is
claimed.

`STTTranscriptHandoffService` accepts only a sealed operation result. It checks
the operation/transcript bindings, replaces provider speaker labels with stable
neutral labels, performs deterministic redaction, and rechecks the exact
redacted digest at `ProcessLocalPOCSourceIntake`. The operation ID supplies both
the source identity and idempotency key. Exact replay returns the same source;
changed content under the same operation identity fails closed. The linked
receipts contain operation, authorization, source, and content hashes but no
audio or transcript text. Attached candidates retain `NEEDS_REVIEW` and have no
agreement, execution, evidence, or verdict authority.

## Provider-neutral meeting connector boundary

`meeting_connector.py` starts the Zoom train without changing the source or
agreement spine. It separates consent-bound permission to request capture from
the later proof that a particular provider stream was authenticated:

```text
exact participant consent
        -> short-lived capture-only authorization
        -> verified webhook + authenticated stream binding
        -> private provider-neutral transcript events
        -> durable bounded inbox + immutable ingress receipts
        -> restart recovery + independent event revalidation
        -> deduplicated, contiguous, bounded sealed window
        -> verified redaction handoff into the existing MEETING source
        -> source-linked NEEDS_REVIEW proposals
```

The connector contract, durable inbox, and source bridge are synthetic-only and
perform no OAuth, REST, or WebSocket work. The inbox adds a local SQLite
ingestion ledger, not a provider transport. Raw meeting identifiers,
participant identities, provider labels, and transcript text remain private and
refuse ordinary serialization. Immutable ingress receipts contain hashes,
counts, times, dispositions, and explicit zero-authority fields only. A separate
private payload annex exists only for bounded restart recovery and is removed
after expiry through secure-delete plus WAL truncation.

One ingress idempotency key replay writes no second ingress or event record and
never extends private retention. A new ingress key carrying an identical event
records an exact provider duplicate without duplicating the canonical event.
Changed event identities, changed sequence occupants, changed idempotency
inputs, or capacity truncation create an immutable stream taint that survives
restart and prevents sealing. Missing canonical sequences, incomplete
lifecycle, mismatched bindings, and participant-set drift still fail at the
unchanged PR108 sealer. Transcript text remains `UNTRUSTED_SOURCE_ONLY` and
`NEEDS_REVIEW`, even when it contains instructions to confirm, freeze, run, or
return `PASS`.

`MeetingTranscriptSourceHandoffService` accepts only an unchanged transcript
object minted by the sealer. It verifies the private in-process sealer marker
and integrity projection before reading private text, replaces provider labels
with stable neutral labels, redacts immediately, and rechecks the exact digest
at the existing source intake. POC plus stable stream identity supplies replay
identity; exact serial or concurrent replay creates one source, while changed
content under the same stream fails as a conflict. Public linked receipts
contain hashes, counts, versions, times, and zero-authority facts only.

`MeetingInboxSourceOrchestrationService` now closes the synthetic core between
those boundaries: it recovers and revalidates one durable inbox population,
rechecks current consent while sealing, invokes the unchanged source bridge,
and returns one content-free, digest-bound result. Finalization is serialized
within one service instance; this is not a cross-process lock or a durable
exactly-once completion claim.

The bridge and orchestration core have no route and no inbox deletion
authority. The accepted redacted source is currently process-local, so the
durable private annex remains under its existing TTL instead of being deleted
after a non-durable attach. A future production coordinator must make the
completion marker and redacted source durable, then make annex deletion atomic
or recoverable. See
[MEETING_SOURCE_HANDOFF_SPEC.md](MEETING_SOURCE_HANDOFF_SPEC.md) and
[MEETING_SOURCE_ORCHESTRATION_SPEC.md](MEETING_SOURCE_ORCHESTRATION_SPEC.md).

`zoom_webhook_auth.py` now adds a narrower pre-transport seam. It verifies one
exact supplied byte string against Zoom's `v0` HMAC, reviewed freshness limits,
and bounded process-local replay state. It emits only a content-free receipt
whose authority to parse a Zoom event, mint a transport binding, append to the
inbox, or affect the ExitSpec lifecycle is fixed to false. It exposes no HTTP
route and deliberately does not resolve the deferred Zoom wire mapping. See
[ZOOM_WEBHOOK_AUTH_SPEC.md](ZOOM_WEBHOOK_AUTH_SPEC.md).

The architecture, current Zoom RTMS capability snapshot, deferred raw-wire
mapping, and PR train are defined in
[MEETING_CONNECTOR_SPEC.md](MEETING_CONNECTOR_SPEC.md). Real customer meetings
remain behind Wave 7B.

## Guided synthetic email boundary

The guided entry point is `/app?intake=email`. It offers exactly two
manifest-approved fixtures:

- **Support-agent requirements**, containing the measurable 95%/200
  tool-selection request and a latency sentence; and
- **Untrusted-instructions test**, proving that approval, freeze, and PASS words
  inside source text cannot advance workflow state.

The browser retrieves the bounded catalog from `GET /api/source/fixtures` and
submits only `{"fixture_case_id":"..."}` to `POST /api/source/import`. The source
router is loopback-only and fail-closed: it validates local authority, method,
path, route parameters, same-origin metadata, media type, canonical length,
strict JSON, exact body fields, the approved fixture, and workflow state in a
fixed order. Its success response is a narrow source-intake projection rather
than the generic session object.

For an accepted fixture, the RFC822 adapter works in request-local memory,
validates identity and MIME limits, normalizes and redacts allowed text, builds
an immutable prepared envelope, then finalizes the source version and its
current-version candidates in one atomic store transaction. Browser state
contains only a guided label, source version and counts, redacted candidate
quotes, safe proposal fields, and review controls. It excludes raw RFC822,
addresses, subjects, message identifiers, digests, private replay data, and
surrounding instructions.

Exact replay creates no source version or candidate and preserves all review
state. A different sample requires an explicit reset. Once customer review,
confirmation, freeze, or evidence exists, every import is locked. Source import,
customer review, reset, and proof transitions serialize against one session
boundary so a race cannot publish a hybrid state.

Email has zero methods or fields for employee approval, customer confirmation,
freeze, measurement, proof, or verdict. Those transitions remain explicit
actions in the existing agreement spine.

The Wave 2 machine manifests remain immutable historical contracts; the
source-web contract therefore retains its pre-implementation status fields. The
separate post-implementation record is
`examples/support-agent/evidence/wave-2-implementation-evidence-v1.json`; product
status is not inferred by rewriting a frozen contract.

## Browser authoring

The currently implemented browser has two synthetic-only entry paths. The guided email path above
projects manifest-pinned source-linked proposals. The existing pasted-notes path
handles raw text transiently, redacts it before parsing, and replaces it with
redacted source plus safe summary metadata. Source lines become unresolved
candidates; intake does not manufacture a metric, threshold, workload, or
approval.

An explicit optional action runs those already-redacted notes through
`SyntheticAssistedAuthoringExecutor`. This deterministic local adapter implements
the provider-neutral structured-authoring interface without network access. It
can recognize the one supported exact-tool-selection shape, keeps unsupported or
conflicting requests unresolved, and cannot approve a proposal. The ordinary
capture path remains the default.

The human can define or correct the one supported rule through four fields:

- title;
- threshold percentage;
- minimum sample count; and
- workload label.

The server fixes the metric, unit, aggregation, adapter, adapter version,
confidence method, and evidence policy. It generates the normalized customer
claim from the structured fields and rejects a submitted `normalized_claim`.
Consequently, the displayed claim cannot drift from the executable rule. A
second unrelated request remains context until a compatible adapter exists.

Every candidate remains `NEEDS_REVIEW` until a named internal reviewer records an
approval or rejection with rationale. All visible candidates must be resolved,
and at least one supported rule must be approved, before customer review can
begin.

## Customer agreement trust boundary

`canonical_confirmation_payload` is the single source for customer rendering and
the confirmation fingerprint. It contains:

```text
id, version, customer, use_case, target_system, workload,
criteria, owners, non_goals, evidence_retention_policy
```

Every bound field is visible in the customer review, including an expandable
exact agreement manifest. Internal review rationale, raw transcripts, lifecycle
state, and verdict data are not part of this projection.

The projection is serialized with RFC 8785 JCS and hashed with SHA-256 under a
confirmation-specific domain. The review capability is bound to the contract ID,
version, and that fingerprint. An expired pending capability is reissued and the
old token becomes invalid.

Customer decisions are immutable and idempotent:

- `CONFIRM` requires `agreement_acknowledged=true`; the server rejects missing or
  false acknowledgement even if a client bypasses the checkbox.
- `REQUEST_CHANGES` requires a rationale but does not masquerade as confirmation.
- One terminal decision cannot be replaced on the same version.

The local reviewer name is typed, not authenticated. Capabilities, identities,
decisions, and idempotency records live only in memory and disappear with the
process. They are demo records, not signatures or durable authorization.

## Revision and workbench continuity

The employee workbench polls state every 1.8 seconds only while a valid customer
review is pending. It stops on a terminal decision, page exit, reset, or inactive
workflow state. Focus and visibility changes trigger safe reconciliation without
creating a second polling loop.

A customer change request creates a new draft contract version and records the
prior `contract@version` as its parent. Approved criteria reopen for explicit
editing and review. Editing invalidates the old review capability, confirmation,
frozen state, and proof; history is not silently rewritten.

The employee can then:

1. apply a structured revision;
2. review the generated claim;
3. issue a new customer link;
4. receive acknowledgement and confirmation for that exact version;
5. freeze it; and
6. prove it.

Recording mode is query-driven at `/app?mode=recording`. `Restart` restores the
bundled source, draft state, Reference A selection, closed drawers, and empty
downstream state deterministically.

The guided email path is query-driven at `/app?intake=email`. At 1280×720 and
100% zoom, each normal guided step stays within the application shell without
workflow-length body scrolling. Smaller or zoomed layouts reflow and use bounded
panel scrolling. The Evidence Pack remains a distinct customer-facing artifact,
not another workbench panel.

## Contract and run lifecycle

### Criterion draft

```text
NEEDS_REVIEW -> APPROVED
      |
      +-------> REJECTED
```

### Customer decision

```text
PENDING -> CONFIRM
    |
    +-----> REQUEST_CHANGES -> new DRAFT version -> new review
```

### Contract

```text
DRAFT -> IN_REVIEW -> APPROVED
                            |
                  matching CONFIRM + acknowledgement
                            |
                            v
                          FROZEN
```

`FROZEN` is immutable. A later agreement is represented by another version, not a
mutation or backward transition.

### Run

```text
QUEUED -> VALIDATING -> RUNNING -> AGGREGATING -> COMPLETED
             |             |             |
             +----------> BLOCKED <------+
             +-------> FAILED_INTERNAL
             +------------> CANCELLED
```

A failed criterion is not a software failure. An adapter crash is not evidence
that the target system failed.

## Deterministic measurement and verdicts

The current adapter runs fixed synthetic cases and returns facts only. The runner:

1. requires a customer-confirmed frozen contract with a valid digest;
2. writes the initial run manifest;
3. records case-level evidence;
4. checks the approved fixture hash and artifact integrity;
5. calculates the proportion and two-sided 95% Wilson interval; and
6. asks the verdict engine to apply the frozen rule.

The browser exposes Reference A (`PASS`), Reference B (`NOT_PROVEN`), and
Reference C (`BLOCKED`). The CLI also retains deterministic `FAIL`,
insufficient-evidence, and internal-error cases. A user can rerun another
reference set against the same frozen contract. Starting the rerun clears the
previous proof, and only a completed run becomes the current result.

For Reference A:

```text
Required ≥ 95.00% · Observed 197/200 (98.50%)
· Wilson lower bound 95.68% · PASS
```

## Evidence store and renderer

Each run owns an inspectable directory:

```text
runs/<run-id>/
  contract.json
  run-manifest.json
  evidence-artifacts.json
  calculations.json
  verdicts.json
  artifact-hashes.json
  decision-packet.html
  evidence/
    <criterion-id>.jsonl
```

`decision-packet.html` is the compatibility filename for the public **POC
Acceptance Evidence Pack**. Before rendering, ExitSpec checks the frozen digest,
manifest identity, criterion identity, measurement identity, artifact integrity,
and deterministic verdict consistency.

The compact graphite/orange first viewport contains the verdict, reason, exact
equation, limitation, next human action, canonical contract hash, and links to
the six top-level JSON artifacts. Seven audit records remain collapsed by
default. The pack contains no scripts or remote dependencies and states that
evidence is not authorization.

The current renderer accepts exactly one frozen criterion.

## Provider boundary

`StructuredJSONRequest` pins model, messages, schema, timeout, token estimate, and
optional budget. JSON Schema Draft 2020-12 output is validated locally and
external schema references are rejected.

`FireworksProvider` is a replaceable adapter with an injected transport,
content-free receipt, bounded retries, and sanitized typed errors. Tests execute
the real adapter and assisted-authoring composition with fake transports. The
`AuthorizedFireworksExecutor` is the only composition used by the optional
server action: it accepts a sealed permit, not a raw request, and applies the
frozen pricing and retry limits. It constructs the pinned HTTPS transport
itself rather than accepting an arbitrary provider transport.

`PinnedFireworksHTTPSTransport` is a narrow standard-library HTTPS seam for the
exact Fireworks host and path. It performs one first-hop request, rejects every
redirect without following `Location`, bounds and strictly decodes the response,
and closes on every path. Its tests inject fake connections. The deterministic
local action remains available without a credential. A separate experimental
browser action can reach this seam only when the server is explicitly enabled
with a server-owned credential. No successful real-provider smoke evidence
exists yet.

The provider-egress contract starts from a frozen Wave-1 manifest. That
trusted policy fixes the provider, model, exact HTTPS endpoint, approved
synthetic payload digest, fixture and case provenance, redaction configuration,
data-policy and pricing snapshots, request ceilings, and spend cap. Request
input cannot choose or weaken those terms.

The in-memory authorizer owns its clock and randomness. After explicit
acknowledgement, it issues a five-minute, single-use capability. Authorization
recomputes the binding from the exact `StructuredJSONRequest` and trusted policy,
then returns a one-use permit that privately carries that same request. A future
transport must accept and take only this permit; it must not accept a separately
supplied request. The permit rechecks server time when the transport takes it,
so authorization just before expiry cannot be held for a later send. Public
records never serialize the token verifier, nonce, or raw request. Malformed,
mismatched, expired, and replayed paths fail closed as the typed, sanitized
`egress_not_authorized` error.

### Loopback provider authorization and execution

The loopback server exposes three narrow routes:

- `GET /api/provider/fireworks/disclosure` derives the public disclosure and its
  identity from the code-pinned frozen Wave-1 policy and rejects URL
  parameters; and
- `POST /api/provider/fireworks/authorization` rejects URL parameters and
  requires that byte-exact disclosure identity, an explicit `true`
  acknowledgement, JSON whose `Origin` authority exactly matches the request
  `Host`, and an idempotency key; and
- `POST /api/provider/fireworks/execution` requires that same exact-origin
  boundary, one header-only `Idempotency-Key`, and an empty JSON object. It
  accepts no provider, model, endpoint, prompt, source, request, capability,
  retry, or budget field.

An identical replay returns the same public authorization result. Conflicting
reuse of an idempotency key is rejected. A new valid authorization replaces the
previous active private authorization, while an old replay cannot reactivate
it. Active authority is cleared by reset or any relevant workflow change, but
the content-free authorization and execution tombstones survive reset so a
delayed retry cannot gain fresh authority. Each history is capped at 64
operations and fails closed until process restart. The capability token and
exact `StructuredJSONRequest` remain server-private and are never returned.

Execution uses a claim → external call → guarded publish state machine. The
session lock atomically claims an operation, reserves the manifest's `$0.01`
maximum, detaches the one-use authorization, and fingerprints the current
workflow. The network call happens outside the lock. Publication occurs only if
the workflow fingerprint is unchanged; otherwise the result is discarded as
`stale_workflow`. Identical concurrent execution keys wait for and replay one
terminal record; a different key cannot start while an operation is pending.

The `$0.10` reservation ceiling, provider-call history, and idempotency
tombstones survive browser reset and clear only on process restart. This is a
conservative local ceiling, not provider billing reconciliation. Process crash
after a send can leave the provider outcome unknown, so exactly-once behavior is
claimed only within one running process.

The credential is read from `FIREWORKS_API_KEY` only when the operator supplies
`--enable-fireworks`. Disabled or missing configuration preserves the local
deterministic path and performs no provider call. Automated proof uses fake HTTPS
connections; one separately approved, funded real-account smoke is still
required before Wave 1 can claim live evidence.

Provider output can propose facts; it cannot set review status, confirmation,
contract state, adapter selection, canonical hashes, or verdicts.

## Packaging and runtime

The package uses a `src/` layout. Browser assets, the deterministic support-agent
inputs, the approved synthetic RFC822 fixtures, the validated
inference-performance workspace bundle, and their frozen contracts are package
data. Resource contexts resolve those inputs for both source installs and
installed wheels.

Therefore `exitspec define`, `exitspec demo`, and `exitspec serve` do not depend
on checkout-relative example paths. CI runs on Python 3.12 and 3.13 and gates:

- browser JavaScript syntax;
- the full Python test suite; and
- a wheel build/install/run from outside the repository.

## Deliberate limits and scale path

The create flow, multi-source navigation, pasted meeting source, browser
recording source, registry projection, dashboard, route split, and
graphite/orange visual contract are implemented against local process state plus
one validated bundled performance agreement; the frozen overall contract
remains unchanged.

The workspace implementation must reuse the existing source, review,
confirmation, freeze, measurement, verdict, and artifact authorities. A
dashboard projection or POC registry may coordinate identifiers and navigation;
it may not become a parallel contract or verdict engine.

The current system is one local process with filesystem artifacts and in-memory
review state. It has an opt-in real-provider STT adapter for synthetic browser
audio, but no funded smoke receipt, customer-audio authorization, streaming
meeting transport, hosted identity, durable confirmation store, queue, object
store, generic metric engine, or multi-tenant authorization. It also has no live
email connector, mailbox OAuth, webhook, arbitrary upload, or real-customer
email path. It has one bounded offline external-evidence importer for the exact
Inferdrome v1 schemas and pinned-vLLM `0.26.0` golden fixture authorized by the
[External Evidence Protocol](EXTERNAL_EVIDENCE_PROTOCOL.md). The loopback app
may orchestrate that offline importer over one explicit local runs root while
publishing no filesystem path to the browser. It has no remote bundle upload,
authenticated producer identity, arbitrary producer or schema compatibility,
or customer-sensitive native-bundle authorization.

If the product earns hosted use, the next boundaries are authenticated identity,
append-only durable decisions, PostgreSQL metadata, object storage, isolated
workers, tenant-scoped authorization, and retention jobs. Those additions must
preserve the same agreement, measurement, verdict, and authorization separation.
