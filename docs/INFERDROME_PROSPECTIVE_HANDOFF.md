# Prospective Inferdrome P1 handoff

This slice freezes the ExitSpec-to-Inferdrome handshake for three independent,
customer-confirmed cases. It stops at contract and source handoff artifacts. It
does not execute a provider or GPU, create a request plan, read or admit an
evidence bundle, calculate a verdict, issue a receipt, authorize deployment, or
change the retrospective Inferdrome V1/V3 path.

## Frozen cases

The checked-in tree contains one immutable `InferencePerformanceCriterionV4`
and one `InferdromeEvidenceIdentityV2` per case, in this exact order:

1. `native-p95-under-20ms`: native vLLM
   `vllm_first_choices_event_v0_26`, p95 TTFT `< 20_000_000` nanoseconds.
2. `native-p95-under-10ms`: the same native metric, p95 TTFT
   `< 10_000_000` nanoseconds.
3. `semantic-first-nonempty-under-20ms`:
   `first_nonempty_choices_delta_content_v1`, p95 TTFT `< 20_000_000`
   nanoseconds.

All three use the pinned Qwen2.5 reference, vLLM 0.26.0, configured
concurrency 4, 10 warmups, 100 measured attempts, `nearest_rank_v1`, the
existing strict reliability population, and independent single-run/no-pooling
aggregation. Equality is explicitly `FAIL` when the required evidence is
otherwise sufficient. P1 freezes these semantics; it emits no acceptance
verdict.

The producer always emits the native metric. The requested criterion metric is
bound separately, so the semantic case is a customer criterion and not a claim
that the producer emitted semantic evidence. Every retained claim remains
visible; later unsupported or insufficient evidence must remain a limitation,
not be hidden by another case's result.

## Identity, hashing, and authority

V2 is run-independent. It contains target, workload, producer, adapter,
profile, native/local schema, traffic, sampling, metric, reducer, population,
source-schema, and expected execution identities, but no request-plan digest,
run ID, bundle digest, observed measurement, source-spec digest, or producer
link. `expected_execution_fingerprint` is the deterministic expected value;
the observed execution fingerprint belongs to a future P2 receipt.

The canonicalization binding freezes RFC 8785 JCS UTF-8 bytes, SHA-256, and
lowercase hexadecimal without a prefix. The ExitSpec `canonical_hash` is the
bare 64-hex digest. The producer link is derived by the versioned
`exitspec.producer_link.sha256_canonical_hash.v1` policy as
`sha256:<canonical_hash>`; that operation prefixes the bare digest and does not
hash it a second time. Contract transport digests in the manifest are separate
SHA-256 digests of serialized artifact bytes.

Each case has its own approved contract, affirmative exact-version confirmation,
frozen digest, and distinct producer link. Provider/LLM output cannot confirm,
freeze, or issue a verdict. The manifest explicitly records
`PROCESS_LOCAL_DECLARED_IDENTITY_NOT_AUTHENTICATED`: process-local confirmer
labels establish the recorded decision boundary, not authenticated person,
customer, organization, or durable hosted authorization. Confirmation
fingerprints and exact-version bindings remain mandatory. A future PASS is not
deployment authorization.

`sequence_requirement=OPERATOR_MUST_FREEZE_BEFORE_MEASUREMENT` is a required
operational sequence. `chronology_assurance=UNAVAILABLE` remains explicit:
`created_at <= decided_at <= frozen_at` is only local artifact consistency and
does not cryptographically prove event ordering or authorship.

## Source handoff and closed-tree safety

Only after each case is frozen, the writer emits its exact Inferdrome
`inferdrome.source-experiment.v1` YAML. The source preserves the pinned
Inferdrome #36 methodology and changes only the experiment identity and the
post-freeze `sha256:` contract link. It contains no future capture identity.
The manifest is an exact inventory binding each case ID, contract ID/version,
contract artifact transport digest and bare canonical hash, producer link,
confirmation ID/fingerprint/record digest, source artifact digest, full
methodology identity, and the synthetic workload path/digest. Unknown or
additive fields under v1 fail validation.

Readers use an `O_NOFOLLOW` descriptor-based regular-file read with identity
rechecks, reject symlinks, hard links, path escapes, extra files, incomplete
trees, oversized files/trees, and depth/file-count overflow, and revalidate the
closed inventory after all artifact reads. Publication uses an exclusive
publish lock, no-replace directory/file operations, and moves `.complete` last.
An incomplete root has no valid completion marker and is rejected; an existing
root is never replaced. Staging trees are removed on success and failure.

The workload is synthetic, public, and checked in at
`sources/real-gpu/workload.jsonl`; it is copied as an input fixture, not
evidence or authorization. No credentials, secrets, customer data, private
captures, external endpoints, or machine-specific paths are included.

## Inferdrome #36 cross-check

The generated sources were checked read-only with merged Inferdrome commit
`355929e33758eeff5b11af1f547f369b05cc3143`:

```text
python scripts/prospective_real_gpu_capture.py --check \
  --case native-p95-under-20ms=.../sources/native-p95-under-20ms.yaml \
  --case native-p95-under-10ms=.../sources/native-p95-under-10ms.yaml \
  --case semantic-first-nonempty-under-20ms=.../sources/semantic-first-nonempty-under-20ms.yaml \
  --expected-contract-digest native-p95-under-20ms=sha256:<bare-contract-hash> \
  --expected-contract-digest native-p95-under-10ms=sha256:<bare-contract-hash> \
  --expected-contract-digest semantic-first-nonempty-under-20ms=sha256:<bare-contract-hash>
```

The checker arguments use the exact tagged producer link
`sha256:<bare-contract-hash>`, even though the value inside the frozen
contract and manifest is the separate bare 64-hex ExitSpec canonical hash.

Result: `status=PROSPECTIVE_INPUTS_VALID`, `valid=true`,
`provider_or_gpu_mutation=NONE`. The expected execution fingerprint derived
from the exact pinned methodology is
`sha256:76d984ea57a0e7cb00520255a6e362f22885d713a875195a7397771937060edd`.
Observed source-spec digests are P2 capture values and are intentionally not
part of these frozen contracts.

## Checked-in artifact digests

The manifest itself is SHA-256
`2dfb5808c2b172f0fd17d034421aa8439f96c54f0a578b7c3f42bdcba2b8231c`.

| Case | Bare contract hash | Producer link | Contract bytes | Confirmation bytes | Source YAML bytes |
| --- | --- | --- | --- | --- | --- |
| native-p95-under-20ms | `c73f3fe1127575443bc30baa1cac4a610dfebfcd721ac72a2c998a6bf1c21580` | `sha256:c73f3fe1127575443bc30baa1cac4a610dfebfcd721ac72a2c998a6bf1c21580` | `sha256:1927a81005adcfef665221ac515ae528143394ef159a34074cba0595f52c05d9` | `sha256:5cb2128073c9626d42637e60c7a86155cf33f78d3c730f7c655079a28fb35304` | `sha256:97a9b6266fec0036d764f78a6670888562abc35ce4da537d58b68dd835a429f5` |
| native-p95-under-10ms | `6a499cfc2e15245e905ecec8282910536e1a594ca3a4d9117e50394ee4f0d855` | `sha256:6a499cfc2e15245e905ecec8282910536e1a594ca3a4d9117e50394ee4f0d855` | `sha256:e5d30b7067fea926956353d8936d1ecf7e49801675c39eb5f40ea3ca8cf59856` | `sha256:38ca98ac2f018de3f3ef0c38da0021ad98617a590dcb08e5226e9244780f03bd` | `sha256:a7645e6a74789245c443d3b38d77eca769e57e7460815e6aad9895a33e09d610` |
| semantic-first-nonempty-under-20ms | `fe776bccedbd5a935480be5808bfaf73b60e17c3f275c2c9b1ba46c2ba9eb248` | `sha256:fe776bccedbd5a935480be5808bfaf73b60e17c3f275c2c9b1ba46c2ba9eb248` | `sha256:a946b22825f27e352dd1c0a3453051c7401348ce8576b350b1a96935c9134516` | `sha256:fe7790ffdbe113db11a07462e11e304c970d89400b1fedd9e5864c650a213e67` | `sha256:85b1fefc07f1c99237d1810baa17c42dda5320c920da6211e14a56e9d0fbc8f0` |

P2 must separately bind observed request-plan, run, bundle, execution,
telemetry, provenance, and receipt data before any evidence admission or
purpose-bound verdict. P1 does not start that capture or verification work.
