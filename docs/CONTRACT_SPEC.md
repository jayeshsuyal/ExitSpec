# ExitSpec Contract Specification

## Purpose

A POC contract is the human-approved source of truth for the claims being evaluated. It records the criterion, how it will be measured, what counts as sufficient evidence, and what each terminal verdict means.

Contracts are versioned documents. A frozen contract is never overwritten.

## Canonicalization and hashing

Human-authored YAML or JSON is parsed into strict typed models. ExitSpec then serializes the model into canonical JSON with:

- UTF-8 encoding;
- deterministic key ordering;
- compact separators;
- normalized JSON-compatible dates and enums;
- the `canonical_hash` field excluded from its own digest.

The SHA-256 digest of those canonical bytes becomes `canonical_hash`. This proves only that the recorded payload matches the digest after it was computed. It does not prove that the source, fixture, or run was honest.

## Contract states

```text
DRAFT -> IN_REVIEW -> APPROVED -> FROZEN -> SUPERSEDED
```

Allowed transitions are one-way. The system rejects an attempt to freeze a contract until every must-have criterion is approved. A revision creates a new `DRAFT` contract with `parent_version` referencing the prior version.

## Discovery authoring boundary

Discovery text is authoring input, not verdict evidence. Brick 2 represents it as numbered transcript lines and creates candidate `CriterionDraft` records before a `Criterion` can enter a contract.

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

The local `exitspec define` command writes the transcript, review decisions, approved contract, static review page, and artifact hashes. It does not claim LLM extraction quality or production authorization.

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
