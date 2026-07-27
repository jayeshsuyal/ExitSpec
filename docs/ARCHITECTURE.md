# Architecture

## Scope and governing invariant

ExitSpec owns the agreement and evidence chain around an AI infrastructure POC.
The current implementation deliberately proves one vertical slice: an exact
support-tool-selection proportion rule over a fixed 200-case synthetic fixture.

```text
synthetic customer source
        |
        v
redaction-first intake
        |
        v
unresolved source candidate
        |
        +-- human defines the supported structured rule
        v
named internal review
        |
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

The domain core does not import a frontend framework or provider SDK. The browser
calls a loopback HTTP boundary, and the server delegates to the same typed domain
services used by the CLI.

## Browser authoring

The browser accepts only synthetic pasted notes. Raw text is handled transiently,
redacted before parsing, and replaced by redacted source plus safe summary
metadata. Source lines become unresolved candidates; intake does not manufacture a
metric, threshold, workload, or approval.

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

The package uses a `src/` layout. Browser assets and deterministic support-agent
inputs are package data. A resource context resolves the discovery pack, review
plan, contract seed, frozen contract, and fixture for both source installs and
installed wheels.

Therefore `exitspec define`, `exitspec demo`, and `exitspec serve` do not depend
on checkout-relative example paths. CI runs on Python 3.12 and 3.13 and gates:

- browser JavaScript syntax;
- the full Python test suite; and
- a wheel build/install/run from outside the repository.

## Deliberate limits and scale path

The current system is one local process with filesystem artifacts and in-memory
review state. It has no speech-to-text, live endpoint adapter, hosted identity,
durable confirmation store, queue, object store, generic metric engine, or
multi-tenant authorization.

If the product earns hosted use, the next boundaries are authenticated identity,
append-only durable decisions, PostgreSQL metadata, object storage, isolated
workers, tenant-scoped authorization, and retention jobs. Those additions must
preserve the same agreement, measurement, verdict, and authorization separation.
