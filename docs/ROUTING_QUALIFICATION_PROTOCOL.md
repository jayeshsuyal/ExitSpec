# Routing qualification protocol (B9)

Status: frozen vocabulary only. This document and
`RoutingQualificationCriterionV1` define `routing_qualification_v1`; they do
not execute a router, admit a real campaign, calculate a statistic, or issue
an acceptance verdict.

## Identity and ownership

The criterion and protocol identifier is exactly `routing_qualification_v1`.
Its schema is `exitspec.routing-qualification.v1`, protocol version is
`1.0.0`, and its canonicalization/hash binding is `rfc8785_jcs_v1` plus
`sha256_v1` with lowercase hexadecimal and no prefix. When the criterion is
carried by the existing `POCContract`, the existing ExitSpec
`freeze_contract`, `contract_digest`, confirmation, and immutable-frozen
primitives remain the lifecycle and hash authority.

The ownership boundary is explicit:

- ExitSpec owns admissibility and any future acceptance verdict.
- A router or Cascade may emit route decisions.
- An evidence producer or Inferdrome may seal route-decision receipts,
  telemetry, request evidence, and provenance.
- Neither producer may supply or control an ExitSpec acceptance verdict.

No provider SDK, orchestrator, GPU, container, router runtime, or
cross-repository import is part of B9.

## Frozen campaign vocabulary

Before measurement, one criterion freezes distinct candidate and baseline
policy identities/digests, the routing configuration identity/digest, and the
request-trace identity/digest. It also freezes:

- deterministic trial allocation: every request receives one candidate and
  one baseline assignment per trial, ordered by trial index, request index,
  then candidate-before-baseline policy order;
- a cold starting state and a reset before every trial across router and
  serving-engine state, with no cross-policy cache reuse;
- a failure-injection identity/digest with an explicit `NO_INJECTION` posture;
- serving engine/version, model/revision, tokenizer/revision, quantization,
  tensor-parallel size, and an execution-environment identity/digest. The
  required environment bindings reuse normalized names such as
  `target.model_revision`, `target.tokenizer_revision`, `gpu.model`,
  `gpu.count`, `cuda.version`, and `driver.version`;
- telemetry capsule type and identity fields, required provenance, and a
  bounded integer `max_age_seconds` rule. Evidence is fresh when observed age
  is less than or equal to the bound and stale only when it is greater;
- route-decision receipt type, identity/digest fields, request/trial/policy/
  routing-configuration bindings, provenance, and the expectation of exactly
  one receipt per request/trial/policy assignment;
- independent runs by default. Pooling is forbidden unless a future frozen
  contract defines the population and aggregation. B9 defines no pooling or
  aggregation operation; and
- a privacy posture that forbids credentials, secrets, and raw sensitive
  customer content. Customer artifacts contain identities, digests, and
  bounded metadata only.

All fields are bounded, immutable nested models with extra fields forbidden.
The contract parser rejects duplicate JSON keys, non-canonical bytes, aliases,
wrong versions/types, oversized values, malformed digests, incomplete or
non-deterministic trial order, contradictory cache/reset or injection posture,
stale/negative age bounds, and insufficient provenance using stable reason
codes.

## Frozen requirements versus observed facts

The pre-measurement criterion contains requirements and identity/digest
bindings only. It contains no run ID, observed telemetry capsule ID, receipt
ID, request plan, bundle, measurement, or producer verdict. Those values are
run-scoped facts and must be bound by future evidence to the frozen contract;
they must not be inserted into the pre-measurement contract digest.

The checked-in evidence-side object at
`examples/routing-qualification/evidence/routing-qualification-evidence.synthetic.json`
is a loudly synthetic protocol fixture. Its synthetic run, capsule, and
receipt IDs exist only to test observed binding and cannot masquerade as a
real campaign or acceptance evidence. The portable contract fixture is a
criterion-only canonicalization test object; the tests also embed it into the
existing `POCContract` and verify that the outer ExitSpec contract hash changes
when routing vocabulary changes.

The request trace remains a prospective identity, rather than a
contract-to-request-plan-to-source-to-contract digest cycle. This preserves
the existing prospective-handshake semantics used by Inferdrome.

## Scope deferrals

B10 may add confidence-bearing SLO criteria. B11 may implement independent
multi-run verification and a future reduction policy. B12 may emit
purpose-bound qualification receipts. B13 may add the Routing Evidence Pack,
UI, and release gate. None of those behaviors is claimed or implemented by
B9. In particular, `routing_qualification_v1` is not executable or proven by
the presence of this schema, fixture, or digest.

The portable synthetic contract and evidence fixtures are covered by
`tests/test_routing_qualification.py`. Existing A100/A10 behavior,
retrospective/prospective external evidence, frozen V1 identifiers, UI flows,
and existing verdict behavior remain outside this additive protocol slice.
