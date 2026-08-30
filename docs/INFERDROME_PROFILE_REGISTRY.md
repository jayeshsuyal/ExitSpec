# Managed evidence profile registry

ExitSpec keeps a small versioned registry of managed evidence compatibility
facts in `exitspec.inferdrome_profile_registry`. The registry is provider- and
orchestrator-agnostic at its boundary: a profile identifies the evidence
schema, producer/adapter identity, model and revision, workload population,
metric semantics, runtime configuration, and required chronology/provenance.
It does not identify a host, run, archive location, GPU UUID, process, prompt,
or generated response.

The existing `inferdrome.managed-vllm-0.26-evidence-profile.v1` entry remains
registered and its A10/Qwen2.5 validator and import paths are unchanged. The
new `managed-vllm-0.26-qwen3-8b-bf16-v1` entry is exact: its campaign profile
binding has no missing, extra, aliased, or substituted fields. Compatibility is
not inferred from labels such as “vLLM”, “Qwen”, or “p95”.

## Trust and privacy rules

The registry is not an attestation system. Profile hashes and bundle hashes
prove byte identity and internal consistency only; they do not prove provider
authorship, hardware attestation, endpoint ownership, truthful execution, or
the absence of sensitive content. Producer measurements and producer verdicts
are untrusted. ExitSpec recalculates supported facts from the sealed request
records and emits no acceptance verdict at the B8 ingestion boundary.

Profile extensions must be additive and reviewed against one exact golden
fixture. New facts must be stable compatibility facts, not private or
run-specific observations. A new profile must declare exact schema, producer,
adapter, model/revision, workload, population, reducer, unit, and provenance
requirements. Unknown profile IDs and all incomplete, extra, aliased, or
incompatible bindings fail closed with a stable reason code.

The frozen external metadata documents use exact nested object shapes as well
as exact top-level shapes. Legitimate fields are enumerated at each consumed
boundary; unrecognized fields, duplicate JSON keys, boolean/integer aliases,
oversized integers, and excessive nesting fail closed.

The legacy A10 profile is admitted only through its established legacy
validator; the managed-profile registry API rejects it rather than creating a
second validation path. The A100 specialization reuses the mature invocation
and local-GPU proof validator, adding only its exact `campaign_profile` field
and server-argument template. The public A100 gate owns the archive, member,
bundle, run, digest, and metric expectations; callers cannot replace those
pins. External metadata uses exact frozen object shapes, rejects boolean
integer aliases, and applies bounded integer/depth parsing. Any rejection
after extraction deterministically removes the extracted bundle; cleanup
failure is itself a stable rejection.

## External-only operator gate

`admit_a100_qwen3_retrospective` is an offline operator gate for the exact
retrospective A100/Qwen3 bundle. The caller supplies the raw archive, reviewed
safe metadata, and the external profile document. The gate:

1. verifies the expected archive size and SHA-256;
2. scans every archive member for bounded safe paths, regular-file types,
   duplicate/case-fold collisions, and resource limits, while materializing
   only the exact bundle subtree;
3. verifies the exact bundle digest and every declared artifact hash through
   the existing no-follow bundle reader;
4. requires `CUSTOMER_ELIGIBLE` evidence and exact profile, model, workload,
   method, environment, chronology, and external-receipt-binding facts; and
5. independently recalculates nearest-rank p95 from
   `records/requests.jsonl`, requiring 96 successful measured records, zero
   errors, and `79,279,716 ns`.

If any post-extraction check rejects the archive or bundle, the extractor
removes its owned destination deterministically. A cleanup error or leftover
destination is itself reported as `CLEANUP_FAILED` through the admission
boundary.

This is an external-only admission gate: metadata cannot substitute for the
sealed archive or bundle, and a synthetic fixture is structure-only and must
be labeled as such. Admission returns validated identity and recalculated
facts, not a performance or acceptance verdict.

The result contains admitted identity and recalculated facts only. It has no
`PASS`, `FAIL`, or `NOT_PROVEN` field. The raw archive is never rewritten,
vendored, normalized, or uploaded. Metadata alone cannot admit evidence: the
exact archive and complete sealed bundle must also pass independently.
