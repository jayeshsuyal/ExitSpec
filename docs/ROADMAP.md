# Roadmap

## Current implementation

- **Truth kernel:** complete locally. Immutable frozen contracts, RFC 8785 JCS
  digests, deterministic measurement, evidence artifacts, confidence calculation,
  and `PASS`, `FAIL`, `BLOCKED`, and `NOT_PROVEN` paths are tested.
- **Source-linked Define path:** complete for synthetic data. A prepared discovery
  pack produces `NEEDS_REVIEW` drafts, named approval/rejection records, an
  approved contract, and review artifacts. Pasted synthetic notes create
  unresolved source-linked candidates instead of invented rules.
- **Customer-confirmation gate:** complete for the local synthetic path. An
  expiring version-scoped review link records an immutable, idempotent decision
  against the exact contract fingerprint. `REQUEST_CHANGES`, stale versions, and
  mismatched fingerprints cannot freeze or prove. Identity and records remain
  unauthenticated, in-memory, and non-durable.
- **Redaction-first intake:** implemented and adversarially tested. Browser intake
  redacts before parsing, retains only redacted source plus safe summary metadata,
  and remains provider-free. Redaction is still best-effort.
- **Structured provider boundary:** provider-neutral request/result/receipt types
  and a Fireworks Chat Completions adapter are implemented and tested with fake,
  injected transports. There is no live network transport or live Fireworks
  evidence.
- **Assisted-authoring composition:** implemented as a side-effect-free service.
  It composes redaction-first intake, fresh provider egress, the real
  `FireworksProvider` with fake injected transport in tests, strict fact DTOs,
  exact source matching, locally controlled execution policy, and review-only
  drafts. It is not wired into the browser or a hosted workflow.
- **Define → Prove → Decide demo:** complete locally for the deterministic
  synthetic path. Its browser intake is redaction-first and provider-free. It
  produces a consistency-checked POC Acceptance Evidence Pack and does not ingest
  speech or claim authorization.

## Next integration sequence

### 1. Expose the tested assisted-authoring service through an explicit workflow

Do not rebuild the composition. After deciding the customer-confirmation boundary
and the privacy, consent, retention, and deployment policy, expose the existing
service behind an explicit opt-in local or hosted workflow:

```text
explicit user action
    -> existing build_assisted_discovery_pack service
    -> redaction summary + content-free provider receipt
    -> source-linked NEEDS_REVIEW drafts
    -> named human review
```

The workflow must make provider use visible, preserve the provider-free browser
demo as a deterministic fallback, and never treat service output as customer
confirmation.

Exit condition: the existing adversarial service tests remain green; an
integration test proves that raw emails, tokens, configured customer terms, and
injected authority fields are absent from browser/hosted state, metadata, errors,
and persistence; provider failure leaves no approved draft or verdict; and every
returned draft is visibly awaiting named human review.

### 2. Make customer confirmation production-grade

Keep the implemented domain gate, then add authenticated identity, authorization,
durable append-only confirmation storage, revocation/expiry policy, and auditable
delivery. The current local sequence is:

```text
named internal review
    -> version-scoped customer review
    -> immutable confirmation record
    -> explicit confirmed freeze
    -> deterministic proof
```

Exit condition: the existing exact-version and fingerprint gate remains green,
while a hosted test proves authenticated reviewer identity, authorized access,
durable idempotent replay, expiry/revocation behavior, and a complete audit trail.

### 3. Prove one live provider boundary

Only after explicit approval of a model, credential handling, synthetic payload,
and spend ceiling, add a real transport and run one synthetic Fireworks structured
request. Fireworks remains replaceable and cannot control adapter selection,
workload, approval, freezing, or verdicts.

Exit condition: success, 401/403, 429, timeout, malformed response, invalid schema
output, retry exhaustion, and budget behavior produce typed, sanitized results and
a content-free receipt.

### 4. Add a real measurement adapter

Run a fixed, customer-shaped synthetic fixture against one hosted endpoint while
keeping the deterministic verdict engine unchanged. Record endpoint identity,
model, fixture hash, adapter version, retries, token usage, latency, cost estimate,
and evidence hashes.

Exit condition: swapping the execution adapter changes measurement facts, not
contract or verdict authority.

### 5. Expand only after the chain is proven

Add multi-criterion packs, latency/cost criteria, load-tool integration, durable
metadata, hosted identity and authorization, retention/deletion, and multi-user UI
in that order. Speech-to-text comes only after consent, raw-audio lifecycle,
redaction, and retention boundaries are defined and tested.

## Demo exit gate

The current recording target is approximately 75 seconds. A fresh developer
should be able to run the synthetic sample in about five minutes and explain:

1. where the customer requirement came from;
2. why the vague request remained unresolved or was rejected;
3. who approved the measurable criterion;
4. which exact frozen contract governed the run;
5. why the deterministic verdict followed;
6. what the POC Acceptance Evidence Pack does not prove; and
7. which human decision remains.

## Decisions requiring Jayesh’s approval before public implementation claims

1. The final public name and any trademark/name availability check for “ExitSpec.”
2. Whether proportion rules use a two-sided 95% Wilson interval, a one-sided bound, or a different approved procedure.
3. The exact overall-verdict precedence when `BLOCKED` and `NOT_PROVEN` coexist.
4. The first hosted provider and the spend ceiling for live testing.
5. The exact customer-confirmation identity and signature requirements.
6. The real-customer consent, redaction, audio/transcript retention, and deletion
   policy.
7. When the local three-screen demo has earned hosted deployment and external
   practitioner testing.
