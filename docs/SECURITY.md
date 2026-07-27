# Security and Privacy

## Current posture

ExitSpec is a local, single-user, synthetic prototype. It is not an enterprise
security product and does not claim tenant isolation, authenticated customer
identity, durable signatures, tamper-proof execution, or formal compliance.

The browser server:

- binds only to `127.0.0.1`, `localhost`, or `::1`;
- rejects non-loopback browser origins for state-changing requests;
- requires JSON media type and bounds request size;
- returns API and artifact responses with `Cache-Control: no-store`;
- contains static and artifact paths under their approved roots; and
- exposes provider integration only through disclosure, authorization, and one
  bounded synthetic execution route. Execution is disabled by default and
  accepts no browser-supplied request or policy fields.

Loopback binding is a demo safety boundary, not a production authorization model.
Requests without browser-origin context are not authenticated.

## Trust zones

```text
raw synthetic source
    -> redaction boundary
    -> redacted source candidate
    -> human review
    -> canonical customer agreement
    -> ephemeral customer capability and decision
    -> immutable frozen contract
    -> deterministic local measurement
    -> hashed artifacts and static evidence pack
```

Each transition narrows authority. Source text cannot confirm a contract,
customer confirmation cannot create evidence, measurement facts cannot assign a
verdict, and `PASS` cannot authorize an external action.

## Source and redaction boundary

The browser accepts synthetic notes only. The application path is:

```text
raw input in request memory
    -> redact_transcript
    -> immutable redacted result
    -> assert_redaction_egress
    -> direct candidate capture or local deterministic assisted authoring
    -> redacted browser/session state
```

Raw matched values are not returned in redaction findings. Findings retain
category, placeholder, count, and line number. The application does not
intentionally persist or export the raw note after redaction, but Python string
deletion is not a memory-erasure guarantee.

`ALLOW_REDACTED_ONLY` means the current mechanical policy found no remaining
blocked pattern. It does not prove that a note is free of every personal,
confidential, regulated, or customer-specific value. Configured customer terms
and human review remain mandatory before any real-customer use.

Pasted source becomes an unresolved candidate. A prompt, source sentence, or
provider response cannot set approval, confirmation, adapter policy, freeze, or
verdict fields.

The optional local assisted-authoring action runs after the same redaction gate.
It uses no credential or network transport, keeps unsupported and conflicting
requests unresolved, and marks every generated proposal `NEEDS_REVIEW`.

## Customer confirmation boundary

The confirmation fingerprint is calculated from the same canonical projection
rendered to the customer:

```text
id, version, customer, use_case, target_system, workload,
criteria, owners, non_goals, evidence_retention_policy
```

Every field is visible in the review surface. This prevents a hidden
fingerprint-bound term from being confirmed without disclosure.

Review capabilities are:

- random, expiring, and bound to contract ID, version, and fingerprint;
- compared through a SHA-256 digest using constant-time comparison;
- invalidated when the agreement changes or an expired pending link is reissued;
  and
- accepted for one immutable idempotent terminal decision.

The local session retains the active raw link token in memory so the employee
workbench can open the synthetic review. The invitation record itself keeps its
digest. Neither is durable.

`CONFIRM` requires an explicit acknowledgement in both the UI and the server
domain service. A false or missing acknowledgement cannot freeze a contract.
`REQUEST_CHANGES` does not confer confirmation authority and creates a new
version before another decision.

Known limitations:

- the displayed customer identity is typed and synthetic, not authenticated;
- capability possession is not verified organizational authorization;
- review links and decisions are in-memory and disappear on restart;
- there is no durable audit store, revocation service, or signature; and
- the local “return to owner” link must not appear in a hosted customer product.

## Provider boundary

Fireworks is implemented as a replaceable structured provider adapter, not an
authority.

- Requests pin schema, model, timeout, token estimate, and optional budget.
- JSON Schema Draft 2020-12 output is validated locally.
- External schema references are rejected.
- Credentials, request and response bodies, provider request IDs, and generated
  content are excluded from object representations and sanitized errors.
- Receipts are content-free execution metadata; they do not approve a rule.
- Retry and budget controls reduce risk but do not guarantee provider billing.
- The frozen Wave-1 manifest supplies immutable provider/model/endpoint,
  approved synthetic payload digest, fixture/case provenance, redaction
  configuration, data/pricing snapshots, request ceilings, and spend cap.
- The provider-egress authorizer owns its clock and randomness and issues a
  five-minute, single-use acknowledgement.
- Authorization recomputes the binding from the exact `StructuredJSONRequest`
  and trusted policy, then returns a one-use permit that privately carries that
  exact request. The authorized transport accepts and takes only the permit, which
  rechecks server time and fails closed if it expired before transport.
- Public acknowledgement and permit records never serialize the token verifier,
  nonce, or raw request. Malformed, mismatched, expired, and replayed paths fail
  closed as typed, sanitized `egress_not_authorized`.
- `GET /api/provider/fireworks/disclosure` derives its public disclosure and
  identity from the code-pinned frozen Wave-1 policy. Request input cannot
  select or weaken those terms, and URL parameters are rejected.
- `POST /api/provider/fireworks/authorization` rejects URL parameters and
  accepts only JSON whose `Origin` authority exactly matches the request
  `Host`. It requires the byte-exact disclosure identity, explicit `true`
  acknowledgement, and an idempotency key. Identical replay returns the
  original public result; conflicting key reuse is rejected.
- A new valid authorization replaces the previous active private authorization,
  and replaying an older operation cannot reactivate it. A content-free
  authorization history is bounded at 64 entries and fails closed until process
  restart. Reset clears active authority but deliberately preserves
  authorization and execution tombstones. The capability token and exact
  request remain server-private. Authorization alone does not set
  `provider_calls`.
- `POST /api/provider/fireworks/execution` rejects URL parameters and accepts
  only exact same-origin JSON, one exact header-only idempotency key, and an
  empty object. It accepts no browser-supplied provider, model, endpoint,
  prompt, source, request, schema, capability, retry, or budget field.
- The server claims the operation, reserves the manifest's `$0.01` maximum,
  detaches active authority, and fingerprints workflow state before releasing
  the lock. Network execution occurs outside the lock. Publication requires the
  same fingerprint; reset or workflow mutation produces `stale_workflow` and
  discards the provider result.
- Identical concurrent execution keys share one terminal record. A different
  key cannot execute concurrently. The `$0.10` process-local reservation cap,
  provider-call history, and operation tombstones survive reset and clear only
  on process restart. A crash after send can leave the external outcome unknown,
  so exactly-once is claimed only within one running process.
- The live-capable composition accepts only a sealed permit and reapplies the
  frozen model, pricing, retry, timeout, and spend policy before transport. It
  constructs the pinned HTTPS transport rather than accepting an arbitrary
  provider transport, and removes provider request IDs from both successful
  results and typed errors.
- The pinned HTTPS seam connects only to the exact Fireworks host and path,
  performs one first-hop request, rejects every redirect without reading or
  following `Location`, bounds/strictly decodes the body, and closes every path.
- Status handling follows Fireworks'
  [inference error meanings](https://docs.fireworks.ai/guides/inference-error-codes):
  `401`/`403` are authentication failures, `402` is billing or usage
  unavailability, `412` is a failed precondition, `429` is rate limiting, and
  `503` is service unavailability. Fireworks documents both account status and
  a failed LoRA load as possible `412` causes, so ExitSpec reports the neutral
  `precondition_failed` category and does not infer the cause.
- The narrower permit-only Wave-1 executor accepts only the frozen
  provider-owned DeepSeek-V4-Flash base model, excluding the failed-LoRA
  interpretation. At that exact boundary, both `402` and `412` normalize to
  the manifest's content-free `account_unavailable` outcome.
- Only timeout, `429`, and `503` trigger bounded internal retries, consistent
  with [Fireworks' serverless retry
  guidance](https://docs.fireworks.ai/serverless/rate-limits). Terminal
  `retries_exhausted` does not authorize another automatic retry.
- Schema-valid provider facts must still link to the exact allowed source.
  Missing, mismatched, or non-exact links fail as typed, non-retryable
  `source_link_violation` and cannot create a proposal, agreement, or verdict.

The complete frozen failure matrix runs through fake transports and fake
connections, including configuration, account, retry, output, budget, source,
redirect, concurrency, stale-publication, and spend-cap failures. Sanitized
failures are detached from their original exception graph before they cross the
provider or assisted-authoring boundary. Every connection in this automated
proof is fake.

The optional browser action is disabled by default. The CLI reads
`FIREWORKS_API_KEY` only when `--enable-fireworks` is explicit; disabled or
missing configuration preserves the deterministic local path. The code is
live-capable, but no successful real-account Fireworks smoke or verified
provider-spend evidence is claimed. Wave 1 remains blocked on one explicitly
approved, bounded live smoke.

Any future live provider use requires a frozen manifest for the approved model,
endpoint, synthetic payload, disclosure, data and pricing policy, request
ceilings, and spend cap, plus an approved credential source, region, and
retention policy.

## Contract and artifact integrity

RFC 8785 JCS plus SHA-256 gives a deterministic change-detection reference for
the frozen contract. The confirmation fingerprint and frozen contract digest are
separate domain concepts: one binds the customer-visible agreement; the other
identifies the frozen contract used by the run.

The runner records SHA-256 values for published artifacts. Before rendering the
POC Acceptance Evidence Pack, ExitSpec verifies:

- frozen contract digest;
- manifest contract identity and version;
- criterion and measurement identity;
- approved fixture hash;
- artifact integrity; and
- deterministic criterion and overall verdict consistency.

The pack is static HTML with no script or remote dependency. Its six artifact
links are relative and constrained to the run output root.

A hash is not a signature. It does not prove who created the source, whether the
original run was honest, whether evidence was omitted before hashing, whether a
request reached a claimed remote endpoint, or whether a reviewer had legal
authority.

## Secrets and exported data

- API keys must come from environment variables or a local secret manager, never
  contracts, logs, fixtures, screenshots, receipts, or public artifacts.
- The public demo and committed fixture must remain synthetic.
- User-facing exports and internal debugging artifacts require separate review
  policies.
- Uploaded contracts are data, not code. Adapters are typed and allowlisted;
  contract values never become arbitrary shell commands.
- Every curated public evidence bundle requires a secret and privacy review.

The current run artifacts contain the synthetic frozen contract, manifest,
case-level synthetic evidence, calculation, verdict, and hashes. They contain no
real customer source, live credential, provider body, or audio.

## Threats covered by current tests

1. Stale contract version or mismatched fingerprint attempting to freeze.
2. Confirmation submitted without explicit acknowledgement.
3. Conflicting reuse of an idempotency key.
4. Expired or invalid customer review capability.
5. Reset or revision leaving stale confirmation or proof authority.
6. Prompt or provider output injecting authority fields.
7. Forged or stale redaction result crossing an egress boundary.
8. Provider output failing local schema or source validation.
9. Artifact mutation or manifest/contract identity mismatch.
10. Static or artifact path traversal.
11. Non-loopback browser origin attempting a state-changing request.
12. Measurement failure being misreported as a customer-system `FAIL`.
13. Provider egress using a request or trusted-policy binding different from the
    acknowledged one.
14. Expired, invalid, malformed, or replayed provider-egress authorization.

## Production security gates

Before real customer or hosted use, ExitSpec needs:

1. authenticated users and customer reviewers;
2. tenant- and contract-scoped authorization;
3. durable append-only invitations, decisions, and audit events;
4. revocation, expiry, replay, and delivery controls;
5. encrypted transport and storage with managed keys;
6. explicit retention, deletion, backup, and incident-response policy;
7. hardened CSRF/session controls and deployment threat modeling;
8. approved real-customer redaction and privacy review;
9. hosted worker isolation and artifact access policy; and
10. consent, audio lifecycle, and residency controls before any speech-to-text.

Until those gates exist, ExitSpec must remain synthetic and local in its public
browser demonstration. The optional provider action must remain disabled by
default and must not be presented as successful live evidence or as
production-safe.
