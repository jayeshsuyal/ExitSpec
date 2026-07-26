# Roadmap

## Completed local product

The current `codex/demo-loop` product closes one deterministic agreement-to-proof
loop.

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
- `FireworksProvider` and assisted-authoring composition tested through fake
  injected transports.
- No live provider transport, no Fireworks UI integration, and no
  speech-to-text.

## Next sequence

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

### 2. Explicit assisted-authoring workflow — local precursor complete

The browser now exposes the redaction-first assisted-authoring service through an
explicit opt-in action backed by a deterministic local executor. It makes no
external call, keeps unsupported and conflicting requests unresolved, and leaves
every proposal `NEEDS_REVIEW`. The ordinary deterministic capture path remains
the default.

The remaining step in this sequence is to place real provider use behind the
frozen Wave 1 acknowledgement, destination, policy, budget, and failure gates
while keeping that local path available.

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

### 3. Prove one live Fireworks boundary

Only after model, credential, synthetic payload, privacy policy, and spend ceiling
are explicitly approved, add a real replaceable transport for one synthetic
structured request.

Exit gate: success, authentication failure, rate limit, timeout, malformed
response, schema failure, retry exhaustion, and budget behavior produce typed,
sanitized outcomes and content-free receipts. Fireworks still cannot approve,
freeze, choose policy, or assign a verdict.

### 4. Add one hosted measurement adapter

Run the fixed customer-shaped synthetic fixture against one hosted endpoint while
keeping the contract and verdict engine unchanged. Record endpoint identity,
model, environment, fixture hash, adapter version, retries, token usage, latency,
cost estimate, and evidence hashes.

Exit gate: changing the execution adapter changes measurement facts only. It does
not change agreement, freeze, or verdict authority.

### 5. Expand the evidence model deliberately

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

### 6. Consider speech-to-text last

Speech-to-text begins only after explicit consent, raw-audio lifecycle, data
residency, provider disclosure, speaker identity, redaction, retention, deletion,
and incident-response boundaries are designed and tested.

No current product or roadmap statement should imply that STT already exists.

## Public demo gate

A release candidate is demo-ready when a fresh operator can:

1. start the product in five minutes;
2. run the exact 75-second primary script from `Restart`;
3. complete the optional versioned revision script without changing 95%/200;
4. explain why 197/200 passes the approved Wilson rule;
5. show all six evidence artifacts;
6. demonstrate one precise non-`PASS` reason and rerun; and
7. state the remaining human decision and product limits without notes.

## Decisions requiring explicit approval

1. Public name and trademark availability for “ExitSpec.”
2. Final statistical policy for each future criterion type.
3. Multi-criterion overall-verdict precedence.
4. First live provider, model, synthetic payload, and spend ceiling.
5. Hosted identity, signature, authorization, retention, and deletion policy.
6. First hosted measurement target and approved evidence-retention boundary.
7. Consent and raw-audio policy before any speech-to-text implementation.
