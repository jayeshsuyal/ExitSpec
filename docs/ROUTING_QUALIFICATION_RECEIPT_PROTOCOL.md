# Routing policy qualification receipt protocol (B12)

Status: frozen purpose-bound receipt layer over B11. B12 issues one immutable
receipt only after the exact B11 contract, matching affirmative customer
confirmation, original B11-admitted evidence, and supplied B11 result have
passed B11's context-bound result validation and recomputation. It does not
execute a router, call a provider, alter B9/B10/B11 reduction semantics, create
an Evidence Pack, add UI, or authorize a product or release action.

## Identity and authority

The receipt schema is
`exitspec.routing-policy-qualification-receipt.v1`. Its protocol identity is
`routing_policy_qualification_receipt_v1` version `1.0.0`; its verifier is
`routing_policy_qualification_receipt_verifier_v1` version `1.0.0`. The
purpose is exactly `ROUTING_POLICY_QUALIFICATION`, canonicalization is
`rfc8785_jcs_v1`, and hashing is `sha256_v1`.

The `rqr_` receipt ID is SHA-256 over RFC 8785 canonical bytes of every
persisted field except the ID itself, prefixed by the frozen domain
`exitspec-routing-policy-qualification-receipt-v1` and a NUL byte. This is a
deterministic identity and consistency check, not a signature or attestation.
Self-consistency alone is never acceptance authority: authoritative validation
requires the original contract, confirmation, evidence bundle, and result and
reruns B11 recomputation. The verdict authority is exactly `EXIT_SPEC_ONLY`.

The receipt binds:

- full frozen B11 contract ID, version, and outer canonical hash;
- confirmation ID, canonical confirmation SHA-256, and contract fingerprint;
- candidate policy ID/digest as the qualification subject and baseline policy
  ID/digest as contextual reference;
- B11 reducer ID/version, result schema, canonical result SHA-256, campaign
  verdict, and required/missing repetition indices; and
- B11 evidence class, every admitted run ID/repetition/canonical run SHA-256,
  and one ordered domain-separated evidence-set SHA-256.

The accepted issuance input is intentionally narrow: one typed canonical B11
`RoutingCampaignEvidenceBundleV1`. B12 round-trips it through B11's canonical
serializer/parser, preserving run order and records without pooling, filling,
or rewriting evidence. Every run is identified independently. Missing runs
remain absent and are recorded in `missing_repetition_indices`; a receipt with
missing evidence can only be `NOT_PROVEN`. Synthetic and external evidence
classes cannot be mixed.

## Issuance and rejection

`issue_routing_qualification_receipt` first strictly revalidates the B11
contract and confirmation rather than persisting fields from caller-owned
objects. It then calls `validate_routing_campaign_reduction_result`, which
re-admits the original evidence and recomputes the result. Result-only input,
producer verdicts, copied/tampered raw model state, a different context, or a
noncanonical/mixed-class bundle cannot issue a receipt.

Admitted B11 `PASS`, `FAIL`, and `NOT_PROVEN` results all receive receipts.
B11 ingestion rejection has no verdict and produces no B12 receipt. Receipt
parsing is bounded and strict: duplicate, extra, missing, wrong-version,
wrong-type, noncanonical, malformed, reordered, and identity-inconsistent
input fails closed. Context-bound serialization and validation re-run the same
B11 authority path.

`issued_at` is supplied explicitly and normalized to a real UTC
whole-second `YYYY-MM-DDTHH:MM:SSZ` value. Naive or fractional timestamps are
rejected. B12 has no clock singleton, database, ledger, signature service, or
idempotency service.

## Purpose limitations

`SYNTHETIC_FIXTURE` always produces `evidence_use: TEST_ONLY`, including when
the recalculated verdict is `PASS`. `EXTERNAL_SEALED_EVIDENCE` may carry any
admitted B11 verdict but does not gain product authority. Every receipt fixes
all deployment, shipping, production-traffic, traffic-expansion, release, and
contract-mutation authorization flags to false and requires a separate human
product decision. A receipt never authorizes deployment, shipping, production
traffic, traffic expansion, release, or contract mutation.

## Frozen golden and non-regression identities

The canonical synthetic test-only golden is
`examples/routing-qualification/receipts/routing-qualification-receipt-v1.synthetic.json`.
It wraps the intentionally incomplete B11 synthetic evidence and therefore has
verdict `NOT_PROVEN`, derived receipt ID
`rqr_ab83f702d765ce428c88c7deea0a7aa4f46293c098d25117f59633c6f37b5c34`,
and canonical SHA-256
`c502a1e3bae757015b90ecca96839b5c792a1d3c2fab9a048a40d00829cfaa87`.
Its ordered evidence-set SHA-256 is
`0d16cd4df1a940aa8b88b0780ab89d2b4ae7b78dccf3e9c7bdecc7070419152b`.

B12 preserves these earlier frozen identities exactly:

| Artifact | Frozen SHA-256 |
| --- | --- |
| B9 standalone contract | `e3bbcab57ac37987f981e0de1a36e56ae6f649cd2f3c75d8d7bcd637583a0516` |
| B10 full contract | `097a49c40646e8a58c31a375160cc0e453ed88dfa5e30cd78120f9d89a460f07` |
| B11 full contract | `66a6642ab761e8430e0a955e4b43de4779dda12fa08207ad25bb708c858bd260` |
| B11 confirmation canonical bytes | `3a64a55affa7bfc661b311651c55c2120ac8bb9492645c75ccceb3a8e7d8f6d5` |
| B11 evidence canonical bytes | `01bdc0f93b6f9bb40c72be17bae0aab07edba31f456881e3aa2596e863c31f86` |

B13 alone may later define a Routing Evidence Pack, UI, browser route, release
gate, or tag. None is part of B12.
