# Roadmap

## Completed local product

The current local product closes one deterministic agreement-to-proof loop and
adds one bounded synthetic-email path into that same spine.

### Truth kernel

- Immutable typed contracts and nested contract data.
- RFC 8785 JCS canonicalization and SHA-256 digests.
- Exact fixture identity and artifact-integrity checks.
- Deterministic Wilson calculation.
- Tested `PASS`, `FAIL`, `BLOCKED`, and `NOT_PROVEN` semantics.

### Human-defined browser authoring

- Redaction-first capture of pasted synthetic notes.
- Unresolved source candidates rather than invented executable claims.
- Human definition and correction of the one supported exact tool-selection
  proportion rule.
- Server-generated normalized claim from title, threshold, sample count, and
  workload label.
- Named internal approval or rejection; unrelated asks remain context.

### Wave 2 guided synthetic email intake

- `/app?intake=email` exposes exactly two manifest-approved samples:
  **Support-agent requirements** and **Untrusted-instructions test**.
- `GET /api/source/fixtures` returns the bounded catalog;
  `POST /api/source/import` accepts only one approved fixture ID through a
  loopback same-origin, exact-JSON boundary.
- RFC822 bytes are deterministically validated, normalized, redacted, and
  finalized into immutable provider-neutral source records before source-linked
  proposals enter the existing `NEEDS_REVIEW` flow.
- The primary sample yields a measurable 95%/200 exact-tool-selection proposal
  and a latency sentence that remains context because no latency adapter exists.
- Employee decisions, customer acknowledgement and confirmation, explicit
  freeze, Reference A/B/C proof, and the Evidence Pack reuse the existing
  agreement spine without granting email any control-plane authority.
- Replays preserve review state, changing samples requires reset, and source
  imports lock once customer review or later downstream state exists.
- The normal 1280×720 shell has no workflow-length body scroll. Narrower layouts
  reflow and bound scrolling inside panels; the customer-facing Evidence Pack
  remains a separate surface.

The frozen Wave 2 acceptance and web manifests remain immutable historical
contracts. The source-web contract's pre-implementation
`contract_only`/`implemented=false` fields are intentionally unchanged.
Post-implementation status is recorded separately in
`examples/support-agent/evidence/wave-2-implementation-evidence-v1.json`.

### Customer agreement boundary

- One canonical customer-visible projection for both rendering and
  fingerprinting.
- Every bound term visible in the customer review.
- Expiring ID/version/fingerprint-scoped review capabilities.
- Server-enforced explicit acknowledgement for `CONFIRM`.
- Immutable idempotent `CONFIRM` and `REQUEST_CHANGES` decisions.
- Expired pending-link reissue with old-link invalidation.
- Honest local limitation: typed synthetic identity and in-memory, non-durable
  links and decisions.

### Workbench continuity

- Pending-only customer decision polling with safe focus reconciliation.
- Versioned change requests with a parent reference and fresh confirmation.
- Explicit freeze of only the currently confirmed version.
- Exact non-`PASS` reasons and next actions.
- Rerun of another reference set against the same frozen contract.
- Deterministic recording mode and `Restart`.
- One-task desktop workbench without workflow-length body scroll.

### Evidence handoff

- Compact graphite/orange POC Acceptance Evidence Pack.
- Verdict, reason, exact equation, canonical hash, limitation, next action, and
  six artifact links in the first viewport.
- Seven collapsed audit records for source, contract, test, sufficiency,
  observation, limits, and follow-up.
- Distinct honest rendering for every verdict.
- Explicit statement that `PASS` is not authorization.

### Distribution and CI

- Deterministic discovery, review, contract, fixture, and browser data bundled in
  the wheel.
- `define`, `demo`, and `serve` resource loading independent of repository paths.
- Python 3.12 and 3.13 CI.
- Python tests, browser JavaScript syntax checks, and installed-wheel gates.

### Provider boundary

- Provider-neutral structured request, result, receipt, retry, schema, and budget
  types.
- `FireworksProvider`, the permit-only executor, and the pinned first-hop HTTPS
  transport tested through fake transports and fake connections.
- Complete frozen fake failure matrix, including Fireworks'
  [documented](https://docs.fireworks.ai/guides/inference-error-codes)
  `401`/`403`, `402`, `412`, `429`, and `503` semantics, bounded internal
  retries, terminal exhaustion, and typed exact-source-link rejection. Generic
  `412` handling stays neutral; the exact frozen non-LoRA Wave-1 boundary
  safely narrows it to account unavailability.
- Loopback disclosure, authorization, and bounded execution routes derived from
  the frozen policy; the browser action is disabled by default, accepts no
  request-policy fields, and has complete fake-HTTPS proof. A separate explicit
  flag now wires one pinned Fireworks prerecorded-STT transport for consenting
  synthetic browser audio. No successful funded authoring or STT smoke evidence
  exists yet.

## Next sequence

### 0. POC-first workspace and multi-source shell

The product direction is frozen in
[POC_WORKSPACE_SPEC.md](POC_WORKSPACE_SPEC.md) and
[`poc-workspace-acceptance-v1.json`](../examples/product/poc-workspace-acceptance-v1.json).
The contract remains `contract_only` for the complete workspace. The read-only
registry, deterministic projection, POC dashboard, route split, and
graphite/orange visual contract are implemented; creation and multi-source
mutation remain unimplemented.

Build the local synthetic/demo track in this order:

1. **Complete:** read-only POC registry and deterministic next-action
   projection, integrated into `/api/state.workspace` for the one seeded POC;
2. **Complete:** `/app` read-only dashboard with no dead destinations or
   unavailable actions, one non-duplicative bounded POC list, at most one
   **Next up** card, and three deterministic filters;
3. **Complete:** `/app/pocs/new` local draft creation with zero authority side
   effects;
4. **Complete:** source chooser with honest availability;
5. **Complete:** bridge the existing manifest-approved synthetic email loop into one seeded
   POC without modifying either frozen Wave 2 contract;
6. **Complete:** add a pasted or deterministic synthetic meeting transcript as a second
   source type, without raw audio;
7. enforce new agreement versions and new customer decisions for material
   multi-source changes;
8. add the Evidence Pack library projection;
9. **Complete ahead of sequence:** restore the graphite/orange visual contract
   across the dashboard, employee workbench, and existing customer review; and
10. publish adversarial implementation evidence for every frozen workspace gate.

This local train does not authorize real email, live meetings, raw audio,
customer-bound delivery, authenticated identity, or durable production storage.
Those remain behind the real-customer trust gate below.

Exit gate: a first-time tester can create or resume one POC, understand email
and meetings as sources, add two synthetic source types to the same POC,
continue through the existing agreement-to-evidence spine, and distinguish
agreement status from evidence verdict while every compatibility, authority,
versioning, 1280×720, narrow-width, and graphite/orange rule in the frozen
contract passes.

### 1. Production-grade customer confirmation

Preserve the canonical projection and exact-version gate while adding:

- authenticated reviewer identity;
- contract-scoped authorization;
- durable append-only decision and invitation storage;
- capability revocation and expiry operations;
- auditable delivery; and
- retention and deletion policy.

Exit gate: a hosted test proves authorized access, durable idempotent replay,
revocation, expiry, version invalidation, and complete audit history without
weakening the current acknowledgement or fingerprint checks.

### 2. Explicit assisted-authoring workflow — bounded action complete

The browser now exposes the redaction-first assisted-authoring service through an
explicit opt-in action backed by a deterministic local executor. It makes no
external call, keeps unsupported and conflicting requests unresolved, and leaves
every proposal `NEEDS_REVIEW`. The ordinary deterministic capture path remains
the default.

The optional server-owned Wave-1 action now places provider use behind the
frozen manifest, acknowledgement, permit, and failure gates while keeping the
local path available. The manifest fixes the provider, model, endpoint, approved
synthetic payload digest, fixture/case provenance, redaction configuration,
data/pricing snapshots, request ceilings, and spend cap.

The provider-egress contract uses server-owned clock and randomness for a
five-minute, single-use acknowledgement. Authorization recomputes the binding
from the exact `StructuredJSONRequest` and trusted policy, then returns a
one-use permit that privately carries that request. The authorized transport
accepts and takes only that permit. Public records do not serialize the token
verifier, nonce, or raw request; malformed, mismatched, expired, and replayed
paths fail closed as typed, sanitized `egress_not_authorized`.

The permit-only executor now takes the request once, applies the frozen pricing
and retry ceilings, and passes it to an exact-origin HTTPS seam. The seam issues
one first-hop request and rejects redirects without following `Location`; all
connections are fake.

The loopback server exposes
`GET /api/provider/fireworks/disclosure` and
`POST /api/provider/fireworks/authorization`. Disclosure is derived from the
code-pinned frozen policy. Both authority routes reject URL parameters.
Authorization requires the byte-exact disclosure identity, explicit `true`
acknowledgement, JSON whose `Origin` authority exactly matches the request
`Host`, and an idempotency key. Identical replay returns the same public result;
conflicting key reuse is rejected. A new authorization replaces the previous
active private state without allowing an old replay to reactivate it. A bounded,
content-free operation history survives reset, fails closed at 64 entries, and
clears only on process restart. Active authority clears on reset or relevant
workflow change. The capability token and exact request never leave the server.
Authorization alone is never counted as a provider call.

`POST /api/provider/fireworks/execution` now provides the one bounded action.
It accepts only exact same-origin JSON, a header-only idempotency key, and an
empty object. The browser cannot replace any request or policy field. The server
claims and reserves the action under lock, executes outside the lock, and
publishes only if its workflow guard is unchanged. Identical concurrent retries
share one terminal record; reset during the call makes the result stale instead
of overwriting new work.

The action is disabled by default and reads `FIREWORKS_API_KEY` only with
`--enable-fireworks`. Each claim conservatively reserves `$0.01` against a
`$0.10` process-local ceiling. Spend state, provider-call history, and
idempotency tombstones survive reset but not process restart.

The complete frozen fake failure matrix now covers missing configuration,
authentication, billing/usage unavailability, neutral precondition failures,
rate limiting, timeout, service failure, malformed or invalid output,
exact-source-link rejection, retry exhaustion, budget refusal, and redirects.
The frozen Wave-1 account case executes both `402` and `412`; only its exact
provider-owned base-model boundary narrows `412` to `account_unavailable`.
Only timeout, `429`, and `503` receive bounded internal retries, following Fireworks'
[serverless guidance](https://docs.fireworks.ai/serverless/rate-limits);
terminal exhaustion does not authorize another automatic retry.

The complete action is proven with fake HTTPS connections. No successful
real-account Fireworks call or live evidence is claimed. Wave 1 remains blocked
on one explicitly approved, funded live smoke.

```text
explicit user action
    -> redaction + fresh egress check
    -> local or explicitly authorized provider candidate
    -> local schema and source validation
    -> NEEDS_REVIEW
    -> named human decision
```

Exit gate: raw secrets and customer terms never enter provider requests,
browser state, receipts, errors, or persistence; every provider result stays
`NEEDS_REVIEW`; provider failure creates no agreement or verdict.

### 3. Consume one authorization, then prove one live Fireworks boundary

PR24 adds one bounded server action for the approved synthetic structured
request. It consumes the exact private authorization already held by the server
and accepts neither a capability token nor replacement provider request from the
browser. It is disabled by default, guarded by exact-origin and idempotency
contracts, bounded by manifest cost/retry ceilings, and publishes only
locally-validated review-only proposals.

Implementation gate: success, authentication failure, rate limit, timeout, malformed
response, schema failure, retry exhaustion, and budget behavior produce typed,
sanitized outcomes and content-free receipts. Fireworks still cannot approve,
freeze, choose policy, or assign a verdict.

Remaining Wave-1 gate: run one separately approved, funded request against the
real pinned endpoint and retain only the sanitized receipt and typed outcome.
Until that smoke passes, the repository claims live-capable code and fake
transport evidence—not successful live-provider evidence.

### 4. Add one hosted measurement adapter

Run the fixed customer-shaped synthetic fixture against one hosted endpoint while
keeping the contract and verdict engine unchanged. Record endpoint identity,
model, environment, fixture hash, adapter version, retries, token usage, latency,
cost estimate, and evidence hashes.

Exit gate: changing the execution adapter changes measurement facts only. It does
not change agreement, freeze, or verdict authority.

### 5. Expand the evidence model deliberately

An Inferdrome external-evidence importer is deferred under the
[External Evidence Protocol](EXTERNAL_EVIDENCE_PROTOCOL.md). Do not define its
wire schema until the pinned-vLLM capability spike supplies the exact vLLM
version, untouched native detailed output, field-availability analysis, verified
TTFT semantics, request ordering and ID behavior, privacy classification, and
first golden fixture.

Add, in order:

1. multi-criterion contracts and report aggregation;
2. schema-validity criteria;
3. latency and throughput criteria through a mature load tool;
4. estimated cost criteria with explicit accounting boundaries;
5. durable metadata and object storage; and
6. multi-user hosted workflow.

Each new criterion type requires its own schema, adapter contract, evidence
sufficiency rules, failure semantics, report rendering, and adversarial tests.
Free-text metric execution is not a shortcut.

### 6. Speech-to-text train — boundary first

The provider-neutral pre-transport contract is implemented in
`stt_boundary.py` and [STT_SPEC.md](STT_SPEC.md). It binds synthetic audio
metadata to explicit consent, exact meeting identity, provider/model/region,
zero retention, a reviewed data-policy snapshot, recording notice, deletion and
incident references, format, size, duration, and a bounded time window. It
returns either a content-free typed denial or a safe record that explicitly
states `transport_capability_issued=false`.

The five-slice synthetic-data train is implemented in this order:

1. **PR95 — boundary:** contract, consent, limits, provenance, private transcript
   handling, and typed denials;
2. **PR96 — bounded audio operation (implemented on the current stack):** exact
   synthetic bytes through one private permit and one disabled-by-default
   provider-neutral transport attempt, with fake-transport proof and no
   automatic retry;
3. **PR97 — transcript handoff (implemented on the current stack):** immediate
   redaction, neutral speaker labels, operation-bound idempotency, attachment to
   the existing `MEETING` source path, and source-linked `NEEDS_REVIEW`
   proposals; and
4. **PR98 — live demo and hardening (implemented on the current stack):** one
   local operator records a bounded browser clip only after server-recorded
   consent; the disclosed fixed synthetic transcript enters the existing
   review-only Meeting flow with content-free receipt recovery, fail-safe track
   shutdown, adversarial checks, and Chromium evidence; and
5. **PR103 — Fireworks prerecorded transport (implemented, funded smoke
   pending):** one explicit server flag replaces the fixed fixture with a
   pinned, single-attempt Fireworks Whisper v3 transcription of the consenting
   operator's synthetic clip. Output is immediately redacted and remains
   `NEEDS_REVIEW`; typed provider failures never resend audio.

The repository may now claim a real provider-capable STT path for synthetic
demo data, backed by fake HTTPS proof. It may not claim a successful funded
live smoke, streaming STT, a Zoom/Meet bot, real customer audio, verified
speaker identity, or production readiness until their separate C3/C4 gates
pass.

## Public demo gate

A release candidate is demo-ready when a fresh operator can:

1. start the product in five minutes;
2. run the guided synthetic-email script from a clean server in about 90 seconds;
3. complete the optional versioned revision script without changing 95%/200;
4. explain why 197/200 passes the approved Wilson rule;
5. show all six evidence artifacts;
6. demonstrate the hostile-email, replay, and reset safety path;
7. demonstrate one precise non-`PASS` reason and rerun; and
8. state the remaining human decision and product limits without notes.

The exact operator sequence is maintained in
[the Wave 2 email demo runbook](WAVE2_EMAIL_DEMO_RUNBOOK.md). Manual
real-browser observations cover six representative task states; they are
acceptance evidence for those states, not exhaustive every-state coverage or CI
browser automation. The frozen no-scroll oracle still applies at every guided
step.

## Decisions requiring explicit approval

1. Public name and trademark availability for “ExitSpec.”
2. Final statistical policy for each future criterion type.
3. Multi-criterion overall-verdict precedence.
4. First live provider, model, synthetic payload, and spend ceiling.
5. Hosted identity, signature, authorization, retention, and deletion policy.
6. First hosted measurement target and approved evidence-retention boundary.
7. Consent and raw-audio policy before any speech-to-text implementation.
8. Production POC retention, archive, deletion, and tenant-ownership policy.
