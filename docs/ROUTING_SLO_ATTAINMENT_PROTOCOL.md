# Routing SLO attainment protocol (B10)

Status: frozen pre-measurement contract vocabulary only. B10 adds a typed,
versioned companion criterion to a full `POCContract`; it does not execute a
router, ingest a campaign, calculate a verdict, reduce independent runs, or
issue a receipt.

## Identity and binding

The B10 criterion and protocol identifier are exactly
`routing_slo_attainment_v1`. Its schema is
`exitspec.routing-slo-attainment.v1`, protocol version is `1.0.0`, and its
canonicalization/hash vocabulary is the existing `rfc8785_jcs_v1` plus
`sha256_v1`. B10 has no criterion-only digest. The existing full
`POCContract` lifecycle and the outer `canonical_hash` remain the only
contract acceptance binding authority.

A B10 contract must contain exactly one unchanged B9
`routing_qualification_v1` criterion followed by exactly one B10 criterion.
The B10 criterion repeats and cross-validates the B9 criterion ID, protocol
and schema versions, and both policy IDs and SHA-256 digests from that same
full contract. An orphaned SLO rule, a policy rebound to another identity, or
an alternate criterion order is not admissible.

The B10 golden contract is the portable synthetic fixture at
`examples/routing-qualification/contracts/routing-slo-attainment-v1.synthetic.json`.
Its exact outer canonical hash is:

```text
097a49c40646e8a58c31a375160cc0e453ed88dfa5e30cd78120f9d89a460f07
```

The standalone B9 fixture and its hash remain unchanged. The B10 fixture uses
a separate B9 campaign instance with two trials and 100 requests per trial:
200 assignments for each subject policy and 400 total paired assignments.
There is no B10 evidence fixture and no observed run, receipt, telemetry,
measurement, or producer verdict in the pre-measurement contract.

## Per-assignment SLO semantics

B10 freezes one provider-neutral underlying observation metric for each
candidate and baseline assignment:

- metric definition ID `routing_terminal_end_to_end_latency_ns`, version
  `1.0.0`;
- a non-negative integer terminal end-to-end latency in nanoseconds;
- a monotonic per-assignment clock, starting at
  `ASSIGNMENT_DISPATCH_MONOTONIC_START` and stopping at
  `FINAL_RESPONSE_OR_EXTERNAL_TERMINAL_OUTCOME_MONOTONIC_STOP`;
- inclusive `lte` comparison: an observed value exactly equal to
  `threshold_ns` satisfies the underlying predicate;
- exactly one required observation in B10 v1, with no duplicate or alternate
  metric definition.

ExitSpec derives the assignment’s binary `ATTAINED`/`NOT_ATTAINED` outcome
from the frozen observation. A producer-supplied `attained` field is not part
of the contract and cannot satisfy the rule. A valid non-negative latency is
compared to the inclusive threshold. External errors and timeouts are
`NOT_ATTAINED` and remain in the denominator. Missing, invalid, or internal
measurement facts are `NOT_PROVEN` later and remain part of the required
population; they are never silently excluded.

Cancellation is an invalid terminal outcome, aligned with the existing
`InvalidEvidencePolicyV1` `CANCELLED` terminal outcome. The frozen assignment
treatment is exactly `NOT_PROVEN_AND_REMAINS_IN_DENOMINATOR`, with the
corresponding assignment disposition `NOT_PROVEN`. B11 must not reinterpret
cancellation as a successful request, remove it from the denominator, or invent
a different disposition.

The envelope says explicitly that ExitSpec derives attainment only when all
required underlying observations satisfy. B10 does not implement that future
measurement or verdict path.

## Subject-specific confidence rules

Each B9 policy subject gets its own frozen confidence rule:

- candidate subject role: `QUALIFICATION_GATE`;
- baseline subject role: `REFERENCE_CONTROL`;
- candidate and baseline each use all B9 request/trial assignments for that
  subject policy, including that subject’s external errors and timeouts;
- the two subject populations are not combined or pooled;
- both rules are mandatory evidence calculations because B9 emits paired
  candidate and baseline assignments. Only the candidate’s explicit
  `QUALIFICATION_GATE` role is customer-gating; a below-threshold baseline
  result is contextual reference-control information, not an implied
  acceptance failure;
- each `minimum_sample_count` must be no greater than that subject’s frozen
  B9 assignment population (`trial_count * request_count`).

The binary count is `attained_count` versus `not_attained_count`, with
`eligible_assignment_count` as the sample count. The frozen confidence rule
requires:

- minimum sample count;
- required attainment rate represented as a canonical decimal string without
  exponent notation;
- exact confidence level `0.95`;
- `wilson_two_sided_lower_bound` using calculator ID
  `exitspec.statistics.wilson_lower_bound`, version
  `wilson-two-sided-v1`;
- comparison `wilson_two_sided_lower_bound >= required_attainment_rate`;
- an explicit statement that a favorable point estimate is never sufficient
  alone.

For example, 197 attained assignments out of 200 produce a favorable point
estimate of 0.985 against the fixture’s 0.97 required rate, while the
two-sided 95% Wilson lower bound is approximately 0.9568. The point estimate
does not establish confidence sufficiency. B10 records this semantics; it
does not calculate a campaign result or emit `PASS`, `FAIL`, `BLOCKED`, or
`NOT_PROVEN`.

## Scope boundaries and deferrals

B10 preserves the B9 campaign wire schema, identifiers, parser semantics,
fixture bytes, and canonical hash. It adds no provider SDK, credentials,
customer content, router/orchestrator execution, GPU/container behavior, or
cross-repository import.

B11 adds independent run evidence handling and reduction in
[ROUTING_CAMPAIGN_VERIFICATION_PROTOCOL.md](ROUTING_CAMPAIGN_VERIFICATION_PROTOCOL.md).
B12 may issue purpose-bound qualification receipts. B13 may add the Routing
Evidence Pack, UI, and release gate. Those future slices own receipt and
product-delivery behavior; none is implemented by B10.
