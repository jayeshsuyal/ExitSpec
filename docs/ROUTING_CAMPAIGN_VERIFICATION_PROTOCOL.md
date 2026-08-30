# Routing campaign verification protocol (B11)

Status: frozen additive executable verification layer. B11 admits bounded,
provider-neutral run evidence for the unchanged B9+B10 contract and performs
an ExitSpec-owned deterministic reduction. It does not execute a router, call
a provider, issue a qualification receipt, create an Evidence Pack, render a
UI, or authorize a release.

`SYNTHETIC_FIXTURE` evidence is test-only and cannot substantiate production
qualification or release.

## Contract authority

B9 remains the `routing_qualification_v1` vocabulary and B10 remains the
`routing_slo_attainment_v1` SLO vocabulary. Both are unchanged, including
their standalone/full fixture hashes:

| Contract | Frozen SHA-256 |
| --- | --- |
| B9 standalone | `e3bbcab57ac37987f981e0de1a36e56ae6f649cd2f3c75d8d7bcd637583a0516` |
| B10 full B9+B10 | `097a49c40646e8a58c31a375160cc0e453ed88dfa5e30cd78120f9d89a460f07` |
| B11 full B9+B10+B11 | `66a6642ab761e8430e0a955e4b43de4779dda12fa08207ad25bb708c858bd260` |

B11 legally defines cross-run reduction by adding exactly one approved
`routing_campaign_reduction_v1` criterion to the existing full `POCContract`.
The only accepted order is `routing_qualification_v1`,
`routing_slo_attainment_v1`, then `routing_campaign_reduction_v1`. The B11
criterion binds the B9/B10 IDs, protocol/schema versions, candidate and
baseline policy IDs/digests, B9 run-policy schema, B9 independent-run mode,
and the B9 default repetition count. B11 has no criterion-only digest; the
existing full-contract canonical hash and customer confirmation lifecycle
remain authoritative. Executable reduction requires the typed affirmative
`ContractConfirmation` whose ID matches `POCContract.confirmation_id` and whose
fingerprint is accepted by `require_affirmative_confirmation` for that exact
frozen contract. A compatibility freeze or fabricated confirmation ID is not
sufficient.

The frozen reducer identity is
`routing_campaign_deterministic_reducer_v1` version `1.0.0`.
`required_repetition_indices` is the exact one-based sequence `1..N`, where
`N` is B9's frozen default repetition count. Runs are ordered by repetition
index and assignment records by trial index, request index, candidate, then
baseline. Run populations are never concatenated, averaged, pooled, or used
for a cross-run Wilson calculation.

## Evidence and ownership

The run envelope is `exitspec.routing-campaign-evidence.v1`; a portable
multi-run bundle is `exitspec.routing-campaign-evidence-bundle.v1`. Evidence
binds the full frozen contract hash, run ID and repetition index, both B9
policy IDs/digests, routing configuration, request trace/workload, failure
injection, serving/model/environment identity, producer identity/version/source
digest, cache-reset evidence, telemetry capsule identity/digest/freshness and
provenance, and exactly one route-decision receipt coordinate when a receipt
is present.

Receipt, telemetry, and reset digests are recomputed from their canonical
bytes. Receipt coordinates are exact `(trial_index, request_id, policy)`
values, with zero-based six-digit request IDs from `request-000000` through
the zero-padded `request_count - 1`.
Producer fields named `attained`, counts, rates, confidence bounds, verdicts,
recommendations, decisions, or acceptance are not admitted. The producer
seals facts; ExitSpec recalculates all outcomes, counts, rates, Wilson lower
bounds, per-run subject results, and the campaign result.

Public result validation and serialization require the same frozen contract,
matching affirmative confirmation, and original evidence used for the
recomputation; an internal result-only check is not an acceptance authority.
The B11 evidence parser retains the hardened 1,000-item JSON-array boundary,
so the full contract rejects any B11 assignment population larger than the
admittable per-run envelope (`trial_count * request_count * 2 > 1,000`).

Ingestion is separate from acceptance. Duplicate-key, noncanonical, wrong
version/type, oversized, malformed, digest-mismatched, duplicate, reordered,
extra, cross-run-reused, or contract-incompatible input is a typed
`INGESTION_REJECTED` boundary failure with no acceptance verdict. A structurally
valid but incomplete, stale, cancelled, internal, or otherwise insufficient
run is admitted for evaluation and becomes `NOT_PROVEN`; missing evidence can
never pass.

## Measurement and reduction

For each required run and each subject policy, ExitSpec derives:

* `SUCCESS` with `latency_ns <= threshold_ns` as attained;
* `SUCCESS` above the threshold as not attained;
* external errors and timeouts as not attained, retained in the denominator;
* missing, invalid, internal, and `CANCELLED` evidence as not proven, retained
  as required denominator population.

The B10 cancellation literal remains exactly
`NOT_PROVEN_AND_REMAINS_IN_DENOMINATOR`. Each run's eligible sample count is
the complete B9 subject population. ExitSpec calculates the point estimate
and the exact frozen two-sided `wilson-two-sided-v1` lower bound, and enforces
the B10 minimum sample count and required rate. A favorable point estimate
whose Wilson lower bound is below the required rate is `NOT_PROVEN`, not a
pass and not a genuine failure. A complete point estimate below the required
rate is a genuine `FAIL`.

The candidate is `QUALIFICATION_GATE`; baseline is
`REFERENCE_CONTROL`. Every required candidate run must pass, and all required
contextual evidence must be complete, for campaign `PASS`. A complete genuine
candidate run failure produces campaign `FAIL` when no required evidence is
incomplete. Missing/insufficient/stale/cancelled/internal required evidence
has precedence and produces campaign `NOT_PROVEN`. A baseline SLO `FAIL` or
SLO `NOT_PROVEN` never gates the campaign; missing, stale, or otherwise
incomplete baseline evidence can still block contextual completeness.

The portable B11 contract golden is
`examples/routing-qualification/contracts/routing-campaign-reduction-v1.synthetic.json`.
Its matching affirmative confirmation golden is
`examples/routing-qualification/contracts/routing-campaign-reduction-v1.synthetic.confirmation.json`,
with canonical SHA-256
`3a64a55affa7bfc661b311651c55c2120ac8bb9492645c75ccceb3a8e7d8f6d5`.
The compact synthetic evidence golden is
`examples/routing-qualification/evidence/routing-campaign-evidence-v1.synthetic.json`;
its canonical SHA-256 is
`01bdc0f93b6f9bb40c72be17bae0aab07edba31f456881e3aa2596e863c31f86`.
The golden is intentionally incomplete and reduces to `NOT_PROVEN`; tests use
deterministic generators for complete PASS, genuine FAIL, Wilson-inconclusive
`NOT_PROVEN`, and ingestion-rejection paths without duplicating hundreds of
synthetic receipts.

B12 may later wrap the immutable in-memory reduction facts in a purpose-bound
qualification receipt. B13 may later define an Evidence Pack, UI, browser
route, release gate, and tag. Those authorities are explicitly out of scope
for B11.
