# Managed Inferdrome receipts

Status: exact retrospective A10 consumer path implemented; raw fixture remains
external and publication-gated.

This path is additive. It does not widen or rename the existing
`exitspec.inferdrome-receipt.v1` importer, its first-nonempty-content metric, or
the frozen `vllm-ttft-v2` artifacts. Managed native vLLM evidence uses a new
`inference_performance_v3` contract criterion and an
`exitspec.inferdrome-managed-receipt.v2` receipt.

## Frozen criterion identity

The customer-confirmed criterion binds all of the following before ExitSpec
evaluates evidence:

- evidence schema, producer, adapter, and native-schema fingerprint;
- managed-profile and local-GPU-proof schema identities and digests;
- target model, model revision, tokenizer revision, and endpoint;
- workload and request-plan digests;
- configured maximum concurrency, exact measured request-record population,
  and warmups;
- TTFT definition, population, reducer, unit, operator, and integer-nanosecond
  threshold; and
- strict reliability numerator, denominator, attempt count, and threshold in
  integer basis points.

The concurrency field means configured maximum concurrency. The retained
evidence does not prove exact simultaneous overlap.

## Retrospective binding

The retained producer bundle has a null ExitSpec contract link because the
customer contracts were frozen after measurement. A null link is accepted only
for the exact pinned bundle digest, exact managed profile, and explicit
`EXTERNAL_RECEIPT_BINDING` mode. ExitSpec binds the later frozen contract hash
to the unchanged bundle digest in its own receipt.

Every receipt states:

```text
purpose: CONFORMANCE_DEMONSTRATION
temporal_assurance: RETROSPECTIVE
contract_preceded_measurement: false
producer_evidence_consistency: VERIFIED
hardware_attestation: NOT_AVAILABLE
execution_attestation: NOT_AVAILABLE
exact_achieved_concurrency: NOT_AVAILABLE
transport_retry_behavior: NOT_AVAILABLE
production_authorization: false
```

These limits are part of the immutable receipt, not UI disclaimers.

## Acceptance calculation

ExitSpec independently validates the managed profile, binds native vLLM arrays
to all 100 canonical records, and recalculates nearest-rank p95 as
`14,797,213 ns`. It then compares that value with the frozen integer threshold.

Reliability is evaluated over the canonical native measured-request records as:

```text
(failed or anomalous measured requests) / all measured requests < 1%
```

The comparison is integer arithmetic. With exactly 100 counted records, zero
failures passes and one failure fails; failed records cannot disappear from the
denominator. The retained bundle does not independently establish lower-level
HTTP transport retry behavior, so neither the contract nor receipt claims zero
transport retries.

Recognized evidence that does not match the requested customer slice yields
`NOT_PROVEN`. In the canonical demonstration, native
`vllm_first_choices_event_v0_26` cannot satisfy the unavailable
`first_nonempty_choices_delta_content_v1` observation. No metric is silently
substituted, even though the same run supports the reliability rule.

## Receipt integrity

Accepted evaluations emit an `irc2_...` receipt. The derived ID uses a distinct
domain separator and binds the bundle digest, contract hash, criterion, profile,
metric, population, applicability, verdict, versions, assurances, and receipt
timestamp. An ordinary SHA-256 over the final canonical JSON is reported
separately for publication and transport verification.

Rejected, corrupt, unsafe, unsupported, synthetic, or ineligible evidence gets
no acceptance verdict and no receipt.

## Deterministic A10 demonstration

The checked-in [portable demonstration](../examples/inference-performance/inferdrome-a10/readme.md)
contains three independently frozen contracts, confirmations, v2 receipts, and
customer Evidence Packs over the same unchanged bundle:

| Customer question | Expected result |
|---|---|
| Configured concurrency 4, native p95 TTFT below 20 ms | `PASS` |
| Configured concurrency 4, native p95 TTFT below 10 ms | `FAIL` |
| Configured concurrency 4, first-nonempty-content p95 TTFT below 20 ms | `NOT_PROVEN` |

Its canonical `manifest.json` records every contract hash, receipt ID, receipt
SHA-256, and the independent `14,797,213 ns` recalculation. The corrupt fixture
is recorded as ingestion `INTEGRITY_MISMATCH`; the synthetic fixture is
recorded as `EVIDENCE_INELIGIBLE`. Both carry a null acceptance verdict and
`receipt_emitted: false`.

The raw archive is deliberately absent. An exact byte-for-byte regeneration
test runs locally when `EXITSPEC_INFERDROME_A10_ARCHIVE` names the retained
archive. CI still validates every portable artifact without that private input.
