# ExitSpec Contract Specification

## Purpose

A POC contract is the human-approved source of truth for the claims being evaluated. It records the criterion, how it will be measured, what counts as sufficient evidence, and what each terminal verdict means.

Contracts are versioned documents. A frozen contract is never overwritten.

## Canonicalization and hashing

Human-authored YAML or JSON is parsed into strict typed models. ExitSpec normalizes dates and enums to JSON strings, excludes `canonical_hash` from its own digest, and serializes the remaining payload with the RFC 8785 JSON Canonicalization Scheme (JCS). JCS provides:

- UTF-8 encoding;
- recursive object-key ordering by UTF-16 code units;
- ECMAScript-compatible JSON primitive and number serialization;
- no insignificant whitespace;
- preservation of Unicode strings as written (JCS does not apply Unicode normalization).

ExitSpec delegates JCS serialization to the Apache-2.0 `rfc8785` package published by Trail of Bits (`rfc8785>=0.1.4,<0.2`). It is a small, pure-Python package with no transitive runtime dependencies. Using it avoids maintaining security-sensitive Unicode ordering and ECMAScript number-rendering code in ExitSpec. This dependency choice is not a claim that the package or ExitSpec has received a formal security audit; RFC vectors and ExitSpec regression tests guard the behavior used here.

The supported canonical payload domain is JSON `null`, booleans, valid Unicode strings, arrays, objects with string keys, finite IEEE-754 double-precision numbers, and integers in the interoperable safe range enforced by `rfc8785`. Non-finite numbers, out-of-range integers, invalid Unicode, non-string object keys, and non-JSON Python objects fail canonicalization instead of receiving a digest. Numeric values needing greater precision must be represented as strings in the typed schema.

The SHA-256 digest of the JCS bytes becomes `canonical_hash`. This proves only that the recorded payload matches the digest after it was computed. It does not prove that the source, fixture, or run was honest, and it is not a signature. Verification now recomputes canonical bytes with RFC 8785, but `canonical_hash` is a bare SHA-256 digest and does not identify which canonicalization algorithm originally produced it; the earlier `sort_keys` serializer and JCS may also produce identical bytes for some payloads. Development artifacts frozen with the earlier serializer should be revised and frozen again. Future persisted format or version metadata should identify the canonicalization scheme explicitly.

## Contract states

```text
DRAFT -> IN_REVIEW -> APPROVED (internal review complete)
                               |
                               +-- exact affirmative confirmation
                                      |
                                      +-- freeze_confirmed_contract --> FROZEN
                                                                           |
                                                                           +-- terminal immutable record
```

Allowed transitions are one-way. The system rejects an attempt to freeze a
contract through the customer workflow until every criterion is internally
approved and a separate affirmative confirmation matches the exact contract ID,
version, and confirmation fingerprint. `freeze_confirmed_contract` then creates a
new immutable contract object whose nested graph is also immutable and whose
digest verifies. The older `freeze_contract` primitive remains as a compatibility
seam for legacy local callers and must not be used by new customer-facing paths.

`FROZEN` cannot transition to `SUPERSEDED`. The enum value is reserved for a
future supersession model, but mutating a frozen record into that state is
explicitly rejected. Future supersession must be a separate record linking the
old frozen version to a replacement. A revision creates a new `DRAFT` contract
with `parent_version` referencing the prior version.

## Discovery authoring boundary

Customer source is authoring input, not verdict evidence. For any future
real-customer path, raw text must be handled transiently, passed through
`redact_transcript`, and pass `assert_redaction_egress` before it reaches a
provider or persistence boundary. The redaction policy is best-effort, so this
mechanical gate never replaces human privacy review.

The authoring path represents allowed source as numbered speaker-attributed lines
and creates candidate `CriterionDraft` records before a `Criterion` can enter a
contract.

```text
NEEDS_REVIEW -> APPROVED
      |
      +-------> REJECTED
```

An approved draft requires all of the following:

1. A transcript span with transcript ID, speaker, line range, and a quote that appears in those source lines; or an explicit `human_added` marker with a rationale.
2. A complete proposed criterion whose source exactly preserves the draft source, or whose human-added marker is preserved.
3. No unresolved open questions.
4. An explicit review record with reviewer, timestamp, decision, and rationale.

A rejected draft remains in the authoring packet so the team can see that a request was considered but not silently converted into a test. Only `APPROVED` drafts may enter `assemble_approved_contract`; that function returns a normal `POCContract` through the existing `DRAFT -> IN_REVIEW -> APPROVED` lifecycle.

An assisted-authoring provider may propose structured facts, but it cannot supply
approval, reviewer identity, contract status, adapter policy, canonical hash, or
verdict. Fireworks is one replaceable structured authoring/execution adapter and
never an authority. Provider output must pass the local JSON schema, typed
validation, and exact source-link validation before it can become a
`NEEDS_REVIEW` draft.

`build_assisted_discovery_pack` implements that composition as a side-effect-free
service. It performs redaction-first intake, a fresh provider-egress check,
provider-neutral structured execution, strict fact DTO validation, exact
line/speaker/quote matching against the redacted transcript, and locally
controlled criterion policy. Complete measurable proposals and vague proposals
both remain `NEEDS_REVIEW`; vague or incomplete proposals have no executable
criterion.

Tests run the service through the real `FireworksProvider` with a fake injected
transport. The service performs no persistence or browser/session mutation, has no
built-in live network transport, and returns only redacted source, review-only
drafts, safe redaction metadata, and a content-free receipt.

The local `exitspec define` and browser paths remain synthetic and provider-free.
Browser pasted-note intake redacts before parsing and retains only redacted
source plus safe summary metadata. An explicit optional browser action invokes
the assisted service with `SyntheticAssistedAuthoringExecutor`, a deterministic
local implementation of the provider-neutral interface. It performs no external
call, supports only the exact-tool-selection rule, and leaves every result
`NEEDS_REVIEW`. The local command writes the synthetic transcript, review
decisions, approved contract, static review page, and artifact hashes. None of
these paths claims live model extraction, live Fireworks execution, STT,
authenticated customer identity, or production authorization.

## Customer confirmation boundary

The normative product sequence is:

```text
named human review
    -> version-scoped customer review
    -> recorded customer confirmation
    -> explicit FROZEN contract
    -> evidence run
```

The review is a plain-language agreement artifact, not evidence and not
authorization. `ContractConfirmation` records the exact contract ID, version,
confirmation-content fingerprint, typed confirmer identity, decision, timestamp,
rationale, and idempotency key. Only `CONFIRM` may freeze; `REQUEST_CHANGES`,
changed content, a different version, or a conflicting idempotency replay is
rejected.

The local browser stores review capabilities and confirmation records only in
memory and uses a synthetic unauthenticated reviewer label. This proves the domain
gate and demo interaction, not real identity, durable consent, signature,
multi-user authorization, or production approval.

## Minimum schema

### `POCContract`

| Field | Meaning |
| --- | --- |
| `id` | Stable contract identifier. |
| `version` | Immutable semantic or sequential version. |
| `status` | Contract lifecycle state. |
| `created_at` | Creation timestamp. |
| `approved_at` / `frozen_at` | Approval and freeze timestamps. |
| `customer` / `use_case` | Customer and job context. |
| `target_system` | Provider, endpoint class, model, and environment reference. |
| `workload` | Fixture path and SHA-256 digest. |
| `criteria` | One or more `Criterion` records. |
| `owners` | Named customer/vendor ownership. |
| `non_goals` | Explicit exclusions. |
| `evidence_retention_policy` | Retention/redaction rules. |
| `parent_version` | Prior contract if revised. |
| `confirmation_id` | Immutable customer-confirmation provenance attached by the confirmed freeze path. |
| `canonical_hash` | Digest assigned at freeze. |

### `Criterion`

| Field | Meaning |
| --- | --- |
| `id` | Stable criterion identifier, e.g. `TOOL-SELECT-01`. |
| `must_have` | Whether it affects the overall verdict. |
| `source` | Speaker, quote, and source location. |
| `human_added` | Explicit marker when no source quote exists. |
| `normalized_claim` | Plain-language, testable requirement. |
| `metric` | Metric being measured. |
| `unit` / `aggregation` | Units and aggregation procedure. |
| `rule` | Operator, threshold, sample requirement, confidence rule. |
| `workload_slice` | Fixture or approved slice reference. |
| `adapter` | Measurement adapter and expected version. |
| `owner` | Person accountable for the criterion. |
| `evidence_policy` | Artifact/redaction expectations. |
| `approved` | Human approval gate. |

### `MeasurementPlan`

Each criterion resolves to an immutable measurement plan containing adapter/version, inputs, environment requirements, execution budget, statistical procedure, required artifacts, validation rules, and expected failure classes.

### `RunManifest`

The manifest records run ID, contract ID/version/hash, fixture hash, timestamps, provider, endpoint class, model/version, region, runtime configuration, traffic shape, warm/cold state, adapter versions, retry policy, redaction policy, environment metadata, and run status.

### `EvidenceArtifact`

Each artifact records artifact ID, criterion ID, run ID, type, storage path, media type, SHA-256, creation time, redaction state, producer adapter, and provenance metadata.

### `CriterionVerdict`

Each verdict records criterion ID, terminal verdict, observed result, threshold, sample count, uncertainty interval or bound, evidence references, calculation version, reason, and limitations.

### `POC Acceptance Evidence Pack`

Before rendering the current single-criterion artifact, ExitSpec requires:

1. a `FROZEN` contract with `frozen_at` and a valid recomputed digest;
2. a run manifest whose contract ID, version, and digest match;
3. a rendered criterion that exactly matches the criterion frozen in the
   contract;
4. measurement and criterion-verdict IDs that match that criterion; and
5. supplied criterion and overall verdict objects that exactly match deterministic
   recomputation.

Contradictory or unfrozen inputs are rejected before HTML is generated. The
historical function and output filename remain `render_decision_packet` and
`decision-packet.html` for internal compatibility; the public artifact name is
**POC Acceptance Evidence Pack**.

## Brick 1 proportion criterion

```yaml
id: TOOL-SELECT-01
must_have: true
metric: exact_tool_selection_rate
aggregation: proportion
rule:
  operator: gte
  threshold: 0.95
  minimum_samples: 200
  confidence_level: 0.95
  confidence_method: wilson_two_sided_lower_bound
adapter: deterministic_tool_selection
```

The `confidence_level` interpretation is deliberately named `wilson_two_sided_lower_bound`. This is a provisional choice requiring approval before any customer-facing contract is presented.

## Contract validation rules

1. Every criterion must have a source quote or explicitly be marked `human_added`.
2. Criterion IDs must be unique within a contract version.
3. Must-have criteria require a measurement rule, workload reference, adapter, and owner.
4. Proportion thresholds must be in `[0, 1]` and minimum samples must be positive.
5. Only approved criteria may enter a frozen contract.
6. A frozen contract must have `frozen_at` and a matching digest.
7. Fixture hash mismatch invalidates the run for affected criteria.
8. In the source-linked authoring path, a quoted span must be present in the declared transcript lines and preserve its speaker and location when converted into the criterion.
9. In the source-linked authoring path, an unresolved or rejected draft cannot be assembled into a contract.
10. Raw customer source cannot cross a provider or persistence boundary; only
    text that passes the current redaction egress check is eligible.
11. Provider output cannot set human approval, contract state, hash, or verdict.
12. A frozen contract cannot be mutated or transitioned to `SUPERSEDED`; revision
    and future supersession use separate records.
