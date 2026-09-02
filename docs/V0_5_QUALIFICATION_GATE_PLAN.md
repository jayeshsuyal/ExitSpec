# ExitSpec v0.5 qualification-gate plan

Status: frozen product and release contract. PR1–PR9 are merged; PR10 is the
current qualification-validity assessment candidate.
ExitSpec v0.4.0 remains the immutable released baseline. This document grants
no provider, GPU, deployment, traffic, spending, release-publication, or
production authority. ExitSpec never authorizes deployment or traffic.

## Product decision

ExitSpec v0.5 will deliver one narrow, complete product:

> **Qualify this exact inference change before it receives traffic.**

The long-term destination remains an evidence and acceptance control plane for
LLM inference infrastructure. The v0.5 wedge is the independently verifiable
qualification gate that makes that direction useful without building a
benchmark platform, deployment controller, hosted control plane, and connector
marketplace simultaneously.

The customer question is:

> Does this exact serving subject satisfy this exact frozen requirement and
> workload, what evidence supports the answer, and is that qualification still
> current?

ExitSpec answers that question. It does not decide whether production traffic
is actually sent.

## Released baseline and compatibility

The v0.4.0 tag and public release freeze the current source-neutral
Define -> Confirm -> Prove workbench, bounded Routing Evidence Pack, offline
external-evidence integration, and synthetic Zoom connector boundary. v0.5
is additive and must preserve every v0.4 authority, compatibility, packaging,
browser, and release invariant unless a separate approved migration contract
explicitly replaces one.

Zoom remains a source adapter during this train:

```text
Zoom or meeting transcript
        -> redacted source
        -> review-only proposal
        -> confirmed agreement
```

No v0.5 pull request expands Zoom OAuth, Marketplace publication, live RTMS,
durable meeting storage, customer identity, audio egress, or production claims.
Existing Zoom compatibility and synthetic fixture gates remain required.

## Authority boundary

The most important v0.5 invariant is unchanged from the current product:

> Qualification evidence is not deployment authorization.

| Component | May do | Must never do |
| --- | --- | --- |
| Source or authoring adapter | Propose source-linked requirements | Approve, freeze, measure, judge, or authorize |
| Customer and internal reviewers | Confirm or reject exact visible terms | Manufacture evidence or turn missing proof into a pass |
| ExitSpec capability planner | Determine which observations a claim requires | Infer capability from source prose or authorize execution |
| External evidence producer or custodian | Prepare an evidence package outside this train | Grade the customer contract, be invoked by ExitSpec, or authorize traffic |
| ExitSpec verifier | Admit evidence, recalculate facts, and assign `PASS`, `FAIL`, or `NOT_PROVEN` | Trust a producer summary or change the frozen rule |
| ExitSpec qualification assessment | Determine whether a receipt is current for an exact requested scope | Issue a deployment or canary grant |
| GitHub required check | Consume local ExitSpec CLI or assessment output and report qualification state | Rewrite ExitSpec evidence, call a provider, deploy, alter traffic, or gain authority from `PASS` |

Every ExitSpec qualification artifact must retain explicit zero-authority
fields or limitations equivalent to:

```json
{
  "deployment_authorized": false,
  "production_traffic_authorized": false,
  "traffic_expansion_authorized": false,
  "external_authorization_required": true
}
```

ExitSpec may state that evidence supports consideration for a bounded purpose,
such as a canary of at most 5%. That statement limits the qualification; it does
not grant the canary. ExitSpec never authorizes deployment or traffic.

## Threat model and trust boundaries

The following threats are in scope for the ExitSpec-only train. Each boundary
fails closed: an untrusted or incompatible input produces no `PASS`, receipt,
deployment action, or traffic action.

| Threat | Protected asset / trust boundary | Fail-closed control |
| --- | --- | --- |
| Untrusted local evidence input | Original evidence, admission boundary, and verifier | Treat every supplied package as untrusted; enforce versioned schema, canonical form, bounded size, exact context, and independently recomputed facts before admission. Reject before verdict or receipt. |
| Producer overclaim or producer verdict injection | ExitSpec verdict authority | The producer supplies only declared observations and provenance. Reject producer verdict and authorization fields; the server-owned profile and deterministic verifier own capability and verdict. |
| Subject, scope, or context substitution | Canonical qualification context | Domain-separated canonical digests bind subject, scope, contract, workload, and protocol. Any mismatch rejects admission or makes the assessment `INVALID`/`STALE`; no digest-only self-consistency establishes trust. |
| Stale or replayed receipt | Receipt applicability for the requested use | Preserve immutable receipt facts, then recompute exact requested context, purpose, and time. Drift is `STALE`; expired, malformed, unsupported, or incompatible input is `EXPIRED` or `INVALID`, never current `PASS`. |
| Unsafe file-tree input | Local filesystem and evidence-tree boundary | Reject symlinks, hard links, path escape, replacement races, extra files, and oversized trees before artifact reading or recalculation. |
| Secret or private-content leakage | Receipts, checks, logs, errors, browser state, and review artifacts | Exclude raw source, prompts, response content, credentials, provider bodies, private paths, and deployment tokens; bound and sanitize error and status output. |
| Status-axis collapse | Proofability, Verdict, and Validity semantics | Preserve typed three-axis output in schemas, CLI, UI, receipts, and assessment. `PROVABLE`, `PASS`, and `CURRENT` are independent and no non-`PASS` state can be rendered as success. |
| Deployment-authority escalation | External deployment and traffic control boundary | Retain zero-authority fields on every qualification artifact. The GitHub required check is least-privilege and status-only; it receives no write permissions, deployment, traffic, provider, or credential authority; it must not use `pull_request_target` for untrusted contribution code; and `PASS` never grants it. |

These controls establish only the semantic and integrity boundaries stated
above. They do not prove physical hardware truth, authorship, chronology, or
facts the admitted evidence cannot establish; those limitations remain visible
on the receipt and assessment.

## End-to-end product loop

```text
Serving subject + qualification scope
                  |
                  v
        canonical context binding
                  |
                  v
        proofability preflight
                  |
       +----------+-----------+
       |                      |
       v                      v
  NOT_PROVABLE          PROVABLE
  stop before spend           |
                              v
                 prospective external handoff
                              |
                              v
           externally supplied evidence package
                              |
                              v
                 ExitSpec admission + recalculation
                              |
                              v
                    PASS / FAIL / NOT_PROVEN
                              |
                              v
                  purpose-bound qualification receipt
                              |
                              v
                    CURRENT / STALE / EXPIRED
                              |
                              v
                  GitHub required-check report
                              |
                              v
                external human/deployment decision
```

The default local and CI path remains provider-free and deterministic. v0.5
does not capture evidence, execute a GPU or provider, spend money, contact an
external producer, or make a cross-repository change. A prospective handoff is
only a canonical local artifact; it is never a dispatch or authorization.

## Core artifact model

### 1. `ServingSubjectManifestV1`

The serving subject is the exact inference system being qualified. The UI may
call it the **candidate**; the schema uses **subject** to distinguish identity
from qualification scope.

The first version binds only fields demonstrated by an admitted profile:

- model ID and pinned revision;
- tokenizer ID and pinned revision;
- engine ID and exact version;
- container, package, or runtime artifact digest where available;
- bounded canonical runtime configuration and launch-argument digest;
- hardware class and topology required by the profile;
- routing-policy identity and digest when the subject includes routing; and
- the profile or adapter identity that defines material fields.

Component revisions must be non-floating pinned references. Engine, profile,
and adapter versions use an exact semantic-version grammar without an arbitrary
minimum character length, so exact vLLM `0.26.0` is valid while floating labels
such as `latest` or `main` fail closed.

It does not contain the customer workload, threshold, use purpose, verdict,
evidence, run ID, or deployment authorization.

Its `subject_digest` is domain-separated SHA-256 over RFC 8785 JCS canonical
UTF-8 bytes of the complete validated identity object, excluding only the
derived digest field. Unsupported, ambiguous, extra, duplicate, noncanonical,
or unbounded fields fail closed.

PR2 fixes the executable contract as
`exitspec.serving-subject-manifest.v1`, RFC 8785 JCS canonicalization, and the
stable byte domain separator `exitspec-serving-subject-manifest-v1\x00` before
SHA-256. The unsigned projection contains every field above and excludes only
the derived `subject_digest`; no alternate projection, implicit defaults, or
free-form metadata bag participates in identity.

The one generic configuration boundary is `runtime_configuration_json`. It is
itself exact JCS JSON for a bounded object tree: identifier-keyed objects,
arrays, strings, booleans, null, and bounded integers only. Floats, non-finite
numbers, duplicate keys, noncanonical representations, unsupported key/value
types, excessive depth, nodes, keys, items, or bytes fail closed. Its recursive,
case-insensitive path-segment deny vocabulary is precisely `credential`,
`credentials`, `secret`, `secrets`, `token`, `tokens`, `password`, `passwords`,
`provider`, `providers`, `run`, `runs`, `execution`, `executions`, `deploy`,
`deployment`, `traffic`, `authorization`, and `authorisation`, plus the adjacent
segment pairs `api`/`key`, `private`/`key`, and `gpu`/`reservation`. It does not
reject harmless material keys such as `seed` or `gpu_memory_utilization`.
Every nested object key extends one accumulated path before this policy is
evaluated, so those pairs are rejected across object boundaries as well as
within one dotted, dashed, underscored, or camel-case key.
The compact fallback for unseparated `apikey`, `privatekey`, and
`gpureservation` is evaluated from one key's segments only; it never
concatenates segments across nested object boundaries.

`launch_arguments_digest` is a required `sha256:<64 lowercase hex>` material
field. PR2 does not persist raw launch arguments: they can contain credentials,
private paths, or provider-specific execution detail and are not part of the
frozen schema. The caller that supplies the digest owns the separate bounded
argument-capture policy; this manifest records only its digest. Every optional
material field is still physically required in the canonical object: use an
explicit `null` for an unavailable runtime artifact or absent routing-policy
pair. A parser never default-fills omitted optional fields.

Identifiers use the stated strict ASCII grammar and configuration strings use
JCS code-point semantics. ExitSpec performs no Unicode normalization: composed
and decomposed Unicode string values are distinct; a JSON escape form that does
not equal the JCS byte serialization is rejected rather than normalized.

The public PR2 API is deliberately small: create an unsigned projection,
strictly parse a complete manifest, serialize canonical bytes, derive the
digest, or verify a typed manifest. Byte parsing rejects duplicate JSON keys
and bytes that differ from the canonical serialization. Public failures expose
only stable reason classes, never field values or private input content. The
checked-in vector at
`tests/fixtures/serving_subject/v1/golden.json` asserts the exact subject
digest `sha256:2921dd76c90a5dd4a6131ef8bb7a369f7b4b1a3a829744751e6b38e81dfb988a`.
The fixture's checked-in raw bytes are themselves exact JCS bytes; tests do not
normalize it before parsing or independently deriving that digest from the
literal domain separator and unsigned projection.
That self-consistent digest is identity and integrity only, not authorship,
execution, hardware truth, chronology, proofability, verdict, validity, or
authority.

### 2. `QualificationScopeV1`

The scope defines the question asked about the serving subject:

- frozen contract identity and canonical hash;
- exact workload identity and digest;
- declared measurement environment or environment profile;
- intended qualification purpose;
- maximum use being evaluated, such as consideration for a 5% canary;
- evidence freshness or expiry policy when the protocol supports it; and
- optional reference-subject identity only when a protocol requires a
  baseline comparison.

Changing the workload or purpose changes `scope_digest`; it does not pretend
that the unchanged serving subject became a different subject.

The same strict canonicalization, bounds, validation, and domain-separated
hashing rules apply to `scope_digest`.

PR3 fixes `exitspec.qualification-scope.v1` as one fully explicit, typed
object. Its unsigned projection contains exactly:

```json
{
  "schema_version": "exitspec.qualification-scope.v1",
  "frozen_contract": {
    "contract_id": "<strict identity>",
    "contract_canonical_digest": "sha256:<64 lowercase hex>"
  },
  "workload": {
    "workload_id": "<strict identity>",
    "workload_digest": "sha256:<64 lowercase hex>"
  },
  "measurement_profile": {
    "environment_id": "<strict identity>",
    "environment_digest": "sha256:<64 lowercase hex>",
    "profile_id": "<strict identity>",
    "profile_version": "<exact semantic version>",
    "profile_digest": "sha256:<64 lowercase hex>"
  },
  "evaluated_use": "CANARY_CONSIDERATION",
  "maximum_use": {"maximum_traffic_percent": 1},
  "freshness_policy": {
    "age_basis": "EVIDENCE_CAPTURED_AT",
    "maximum_evidence_age_seconds": 1
  },
  "reference_subject_requirement": "NOT_REQUIRED",
  "reference_subject_digest": null
}
```

`maximum_traffic_percent` is an integer from 1 through 5. It records only the
bounded qualification question: consideration of a canary of at most 5%. It
does not grant a canary, deployment, production traffic, or traffic expansion.
The scope contains no authorization, permission, deployment, or traffic-grant
field; ExitSpec's zero-authority invariant remains an external contract rule.

Every field above is physically required. `freshness_policy` is either an
explicit `null` or the exact prospective evidence-age policy shown above; it
has no issuance, capture, expiry, or currentness fact of its own. Its
`EVIDENCE_CAPTURED_AT` basis only tells a later protocol which evidence fact it
would need to evaluate age. `reference_subject_requirement` is exactly
`NOT_REQUIRED` or `REQUIRED`; its digest is `null` exactly when not required
and present exactly when required. No parser default-fills either optional
field. The domain separator for `scope_digest` is the stable bytes
`exitspec-qualification-scope-v1\x00`; its unsigned projection excludes only
the derived `scope_digest`.

### 3. `QualificationContextV1`

The context binds subject, scope, and qualification protocol without informal
string concatenation:

```json
{
  "schema_version": "exitspec.qualification-context.v1",
  "subject_digest": "sha256:<64 lowercase hex>",
  "scope_digest": "sha256:<64 lowercase hex>",
  "protocol_id": "<versioned protocol identity>",
  "protocol_version": "<exact version>",
  "qualification_context_digest": "sha256:<64 lowercase hex>"
}
```

`qualification_context_digest` is domain-separated SHA-256 over this canonical
object. It becomes the common substitution boundary across the prospective
handoff, admitted evidence, receipt, assessment, CLI, and UI.

The context unsigned projection excludes only
`qualification_context_digest` and uses stable domain-separator bytes
`exitspec-qualification-context-v1\x00`. It binds validated subject and scope
digests through canonical JSON fields, never informal string concatenation.
PR3 parsing and self-consistency do not resolve those digests to a producer,
evidence, clock, hardware, or authenticated principal.

The checked-in PR3 vectors at
`tests/fixtures/qualification_scope/v1/golden-scope.json` and
`tests/fixtures/qualification_scope/v1/golden-context.json` have raw bytes
equal to their JCS serializations. They independently derive scope digest
`sha256:5db651e8c2eae05147d2c5fc52bae0b4526ed84508f76d62d41471ac4ca677ab`
and context digest
`sha256:9159ac21169d0674b916053e6605a72f6f25e65cfe94b30b708a86f343d0193c`
from their literal domain separators and unsigned projections.

Self-consistent digests are not proof of execution, authorship, chronology,
hardware truth, or authenticated identity. Those assurances remain explicit
and profile-specific.

### 4. `ProducerCapabilityDescriptorV1`

PR4 fixes one server-owned, provider-neutral registry entry. It declares only
which observations the selected external-evidence profile can provide; it does
not inspect source text, provider output, API or browser payloads, or evidence.
The first entry has ExitSpec-owned profile identity
`exitspec.external-evidence.native-ttft-profile.v1` at exact profile version
`v1`. Its profile identity is not a provider connection, a run, a producer
attestation, or an authorization.

The descriptor is one fully explicit closed object:

```json
{
  "schema_version": "exitspec.producer-capability-descriptor.v1",
  "registry_version": "exitspec.producer-capability-registry.v1",
  "profile": {
    "profile_id": "exitspec.external-evidence.native-ttft-profile.v1",
    "profile_version": "v1"
  },
  "engine_adapter": {
    "engine_id": "vllm",
    "engine_version": "0.26.0",
    "adapter_id": "vllm_bench_serve",
    "adapter_version": "1.0.0"
  },
  "available_observations": {
    "native_ttft": {
      "observation_id": "native_ttft_sample",
      "metric_definition_id": "vllm_first_choices_event_v0_26",
      "source_field": "request.timing.ttft_ns",
      "unit": "ns",
      "population": "successful_measured_requests_with_observed_ttft",
      "reducer_id": "nearest_rank_v1",
      "supported_percentile": "p95"
    },
    "measured_attempt_reliability": {
      "observation_id": "native_measured_request_outcome",
      "source_field": "request.outcome.status",
      "latency_population": "successful_measured_requests_with_observed_ttft",
      "reliability_numerator": "failed_or_anomalous_native_measured_requests",
      "reliability_denominator": "all_measured_requests"
    }
  },
  "capability_digest": "sha256:<64 lowercase hex>"
}
```

Every value above is material. The advertised TTFT semantics are native
`vllm_first_choices_event_v0_26` samples only: they are not the existing
`first_nonempty_choices_delta_content_v1` semantics. That semantic observation
is absent and unsupported by this descriptor; a later proofability boundary
must report it missing rather than infer a conversion.

Untrusted callers may submit only this exact canonical registry request:

```json
{
  "schema_version": "exitspec.producer-capability-request.v1",
  "profile_id": "exitspec.external-evidence.native-ttft-profile.v1",
  "profile_version": "v1"
}
```

There is no create, override, merge, or caller-supplied descriptor API. Unknown,
aliased, malformed, duplicate, extra, oversized, unsupported-version, or
noncanonical requests fail closed with stable content-safe reason classes. A
descriptor parser admits only a byte-exact canonical descriptor identical to
the package registry, so self-consistent replacement content cannot expand the
declared capability. Direct mapping input is bounded before canonicalization:
cycles, excessive depth, nodes, object keys, array items, and strings reject
with stable content-safe reason classes. At every public projection, digest,
verify, and serialization boundary, ExitSpec recursively requires each model
node to be its exact declared class (no subclass), have exactly its declared
raw fields, have no extra raw state, and have an empty Pydantic extra state
before any potentially lossy projection.

`capability_digest` is domain-separated SHA-256 over RFC 8785 JCS bytes of the
complete validated descriptor, excluding only the derived digest. The stable
domain-separator bytes are
`exitspec-producer-capability-descriptor-v1\x00`. The checked-in raw JCS vector
at `tests/fixtures/producer_capability/v1/golden.json` independently derives
`sha256:1b8732d26a94dadfab984b43a4c67c1fc858ddf39f95ec496f5914f1c08e066b`
from that literal separator and unsigned projection. Raw parsing rejects
duplicate keys and any representation that is not exactly canonical; typed
models are strict, deeply immutable, bounded, and revalidated at every public
verify or serialization boundary.

The registry is declaration only. Its self-consistent digest proves only
ExitSpec's declared planning capability, never producer execution, evidence,
hardware truth, chronology, authorship, provider identity, verdict, receipt,
deployment, traffic, or any other authority. PR4 introduces no network, API,
browser, execution, evidence-admission, proofability, verdict, or receipt path.

### 5. `ProofabilityReportV1`

Proofability is evaluated before any external operation. For every frozen
criterion the report records:

- required observations and metric semantics;
- observations the selected producer/profile can supply;
- missing or incompatible observations;
- selected adapter and profile identities;
- deterministic disposition and stable reason codes;
- remediation or clarification required; and
- zero execution and verdict authority.

Criterion-level dispositions are:

```text
PROVABLE
CLARIFICATION_REQUIRED
NOT_PROVABLE
```

`PARTIALLY_PROVABLE` may describe a multi-criterion plan whose criteria have
different dispositions. It is never a criterion verdict and can never be
converted into partial acceptance.

The first required negative case is the existing semantic/native TTFT boundary:
a first-nonempty-content criterion must be `NOT_PROVABLE` when the selected
producer profile exposes only native first-event TTFT.

PR5 freezes the provider-neutral `InferenceQualificationCriterionV1` contract
arm: `criterion_type` is `inference_qualification_v1`, `schema_version` is
`exitspec.inference-qualification-criterion.v1`, and the protocol is exactly
`inference-performance-qualification` / `1.0.0`. It carries bounded source or
human-origin metadata, one closed discriminated latency requirement, and one
closed measured-attempt reliability requirement. Requested thresholds and
counts are material prospective decision rules in the frozen contract hash;
they are not observed values, outcomes, evidence, a run, a verdict, a receipt,
or authorization.

The latency discriminator is exact, never a structural fallback:

- `NATIVE_TTFT_P95` requires observation `native_ttft_sample`, metric
  `vllm_first_choices_event_v0_26`, source `request.timing.ttft_ns`, `ns`,
  `successful_measured_requests_with_observed_ttft`, `nearest_rank_v1`, `p95`,
  `lt`, a bounded positive `threshold_ns`, bounded positive
  `minimum_successful_samples`, `equality_outcome` `FAIL`, and `must_pass`
  `true`.
- `SEMANTIC_FIRST_NONEMPTY_TTFT_P95` requires the distinct observation
  `semantic_first_nonempty_ttft_sample`, metric
  `first_nonempty_choices_delta_content_v1`, and source
  `response.choices[].delta.content` with the same frozen `ns`, population,
  reducer, percentile, operator, threshold, sample, equality, and must-pass
  fields. Native first-event availability is not a conversion or substitute.
- Reliability requires `native_measured_request_outcome`, source
  `request.outcome.status`, that same latency population,
  `failed_or_anomalous_native_measured_requests` over
  `all_measured_requests`, `lt`, bounded positive basis points, bounded exact
  attempts, and `must_pass` `true`.

`ProofabilityReportV1` is a closed immutable planning artifact. It binds the
literal report schema, canonicalization and hash versions; exact subject,
scope, and qualification-context digests; exact protocol; frozen contract ID
and `sha256:<canonical_hash>`; registered capability digest; declared profile,
engine, and adapter identities; contract-order criterion results; required,
available, missing, and incompatible observation references; closed reason and
remediation codes; and the deterministic overall disposition. It deliberately
does not duplicate contract thresholds, title, source, claim, workload path,
endpoint, credential, request, observed measurement, run, evidence, verdict,
receipt, time, authority, or free-form diagnostic text.

Every criterion result is internally self-consistent before it is serialized:
`PROVABLE` has a nonempty required tuple exactly equal to its available tuple,
no missing or incompatible tuple, and exactly
`ALL_REQUIRED_OBSERVATIONS_AVAILABLE` / `NO_REMEDIATION_REQUIRED`.
`CLARIFICATION_REQUIRED` has no required, missing, or incompatible tuple and
exactly `UNMAPPABLE_FROZEN_CRITERION_SCHEMA` /
`FREEZE_PROVIDER_NEUTRAL_CRITERION_SCHEMA`. `NOT_PROVABLE` has a nonempty
required tuple and at least one missing or incompatible member. For every full
required observation model, exactly one of these facts is true: that exact
model is present in `available_observations`, that exact model is present in
`missing_observations`, or an incompatible row has that full model as its
`required_observation`. These three categories are a complete, mutually
exclusive partition of the required tuple; reduced observation-kind/ID keys do
not define exact availability. Extra descriptor-available observations remain
visible without entering the required-observation partition.

Missing members are canonical, unique required members that are not exactly
available. Incompatible rows are canonical and unique; their full required
model is required, their full available model is available, and the two models
differ materially. For each required/available pair, the rows must enumerate
the complete canonical set returned by the closed semantic-leaf mismatch
mapping, never a selected subset. A semantic/native latency pair may therefore
be structurally coherent only with both metric-definition and source-field
mismatch reasons. Its closed aggregate reason/remediation tuple must exactly
describe the represented deficiency. In particular, the registered-profile
evaluation of the first semantic-TTFT case is exactly one missing semantic
observation, no incompatible pair, `MISSING_OBSERVATION`, and
`DECLARE_REQUIRED_OBSERVATION`. A self-consistent digest does not waive these
invariants: contradictory reports fail parsing before capability use, while a
structurally coherent replacement still fails input-bound verification after
the same frozen semantic contract and context are independently re-evaluated.

Its unsigned projection excludes only `proofability_report_digest`, is RFC
8785 JCS, and uses the literal byte domain separator
`exitspec-proofability-report-v1\x00` to produce
`sha256:<64 lowercase hex>`. Raw report bytes must already equal JCS; parsing
proves syntax and self-consistency only. Verification requires the original
subject, scope, context, frozen contract, and registered descriptor; it
independently re-evaluates and compares exact canonical bytes. A
self-consistent replacement report never becomes a trusted evaluation.
The checked-in test-only vector
`tests/fixtures/proofability/v1/golden.json` has no terminal newline and
independently derives
`sha256:28c49bba2dd3791905a201a74777c9994e6ecc083cc3b9de083095f4c626d81e`
from that literal separator and its unsigned projection.

Every result `criterion_id` accepts exactly the complete bounded language of
the existing `POCContract` criterion union: the seven ordinary criterion arms
use `^[A-Z][A-Z0-9-]{2,63}$`, while the routing arms use only the three exact
literals `routing_qualification_v1`, `routing_slo_attainment_v1`, and
`routing_campaign_reduction_v1`. No other lowercase, mixed-case, punctuation,
prefix, suffix, or over-64-character alias is admitted. This report grammar
does not make a routing criterion provable: every legacy arm remains opaque
and `CLARIFICATION_REQUIRED`.

The raw report boundary is finite and closed over the declared evaluator
domain. Canonical report bytes may contain at most 1,048,576 bytes and the
decoded graph may contain at most 16,384 nodes; existing depth, object-key,
array-item, string, and integer bounds remain enforced. The report model and
evaluator both retain the public maximum of 64 criterion results. Worst-case
ordinary evaluator outputs were measured independently across all-native,
all-semantic, all-legacy, and mixed 64-result contracts: the largest is the
all-semantic output at 3,349 counted JSON nodes and 127,286 bytes when criterion
IDs use their maximum ordinary width. The frozen limits therefore retain more
than four times node headroom and eight times byte headroom. Bytes or mappings
over any bound reject before trusted use. As a defense-in-depth invariant, the
evaluator serializes, parses, and strictly normalizes its constructed report
before returning it; every supported evaluator result must then serialize,
parse, and verify against the same exact inputs.

PR5 validates all five inputs before any result: strict recursive raw model
graphs, derived PR2/PR3 digests, exact context links and protocol, a `FROZEN`
contract with a valid canonical hash, exact scope contract ID/hash linkage, and
the package-registered descriptor. The trusted typed boundary covers the report
and every input and nested model before any potentially lossy dump. It requires
the exact declared model and tuple classes; exact primitive, enum, and
`datetime` node types; exact documented `__dict__` fields; no cycle or mutable
raw container; no Pydantic extra state; no nonempty or malformed
`__pydantic_private__`; and exact `__pydantic_fields_set__` state relative to an
independently strict canonical round-trip. The original raw graph and that
normalized graph are compared recursively by exact node type and value, not
equality alone, so a value-equal `str` subclass or other type-confused primitive
cannot disappear through serialization.

Only after those five independent validations, the subject's exact
`engine_id` and `engine_version` must equal the descriptor
`engine_adapter.engine_id` and `engine_adapter.engine_version`. A mismatch is a
content-safe `CAPABILITY_BINDING_MISMATCH`, never a proofability disposition,
and no criterion is mapped. This applicability link does not compare the
registered evidence profile or adapter with the subject's serving-profile
adapter. PR5 also deliberately does not derive scope workload identity from
`POCContract.workload.fixture_path` or compare the scope measurement profile to
the descriptor profile: those separately valid identities remain material
through their existing digests, and no unestablished profile/adapter link is
fabricated.

The deterministic mapping preserves frozen contract order. A new native
criterion with the declared native observations is `PROVABLE`. A new semantic
first-nonempty criterion is `NOT_PROVABLE` with
`MISSING_OBSERVATION`; available native observations remain separately listed.
Every legacy union arm is opaque and returns `CLARIFICATION_REQUIRED` with
`UNMAPPABLE_FROZEN_CRITERION_SCHEMA` without exposing its fields. Overall
precedence is all provable => `PROVABLE`; a mix containing a provable result =>
`PARTIALLY_PROVABLE`; no provable result with any not-provable result =>
`NOT_PROVABLE`; otherwise `CLARIFICATION_REQUIRED`. This is planning only and
never execution, evidence admission, Verdict, Validity, deployment, traffic,
or authorization.

### 6. protocol-specific qualification receipt

The first implementation is
`InferencePerformanceQualificationReceiptV1`. It wraps but never replaces the
applicable performance evidence admission and verdict protocol.

Issuance requires the original:

- serving subject;
- qualification scope and context;
- frozen contract and affirmative exact-version confirmation;
- admitted canonical evidence bundle;
- independently recalculated result; and
- verifier/profile identities.

The receipt binds their canonical digests, evidence class, evidence-set
identity, verdict, limitations, issue time, and any supported freshness facts.
Result-only input, producer verdicts, digest-only self-consistency, rewritten
evidence, mixed evidence classes, or a different context cannot issue or
validate a receipt.

Protocol-specific receipts remain separate. v0.5 does not replace or silently
generalize the frozen B12 routing receipt. A small common assessment layer may
consume validated receipts only through an explicit typed protocol adapter.

### 7. `QualificationAssessmentV1`

The assessment compares a fully validated receipt with the currently requested
subject, scope, context, time, and use. It preserves three independent axes:

| Axis | Values | Meaning |
| --- | --- | --- |
| Proofability | `PROVABLE`, `CLARIFICATION_REQUIRED`, `NOT_PROVABLE` | Whether execution can establish the criterion |
| Evidence verdict | `PASS`, `FAIL`, `NOT_PROVEN` | What admitted evidence establishes |
| Validity | `CURRENT`, `STALE`, `EXPIRED`, `INVALID` | Whether that verdict remains applicable now |

Examples:

- measurable criterion with sufficient evidence above the allowed threshold:
  `PROVABLE` + `FAIL` + `CURRENT`;
- producer lacks the required observation: `NOT_PROVABLE`, with no execution
  or receipt;
- incomplete evidence: `PROVABLE` + `NOT_PROVEN` + `CURRENT`;
- engine digest changes after a valid receipt: original verdict remains
  inspectable, but validity becomes `STALE` for the new context; and
- freshness deadline passes: original verdict remains inspectable, but
  validity becomes `EXPIRED`.

The assessment may expose a derived machine outcome for a local policy consumer,
but it must retain the underlying axes. It is not named or represented as a
canary or deployment grant. ExitSpec never authorizes deployment or traffic.

## Initial product wedge

The first supported vertical is provider-neutral. It defines one frozen
qualification method and a local admission boundary rather than introducing a
runtime matrix, an evidence producer integration, or an execution service.

The default product story is:

- one exact pinned synthetic serving-subject fixture;
- one fixed immutable workload;
- one native nearest-rank p95 TTFT criterion and its existing reliability rule;
- one declared producer-capability profile;
- one explicitly synthetic CI fixture or manually supplied external evidence
  package at the local admission boundary;
- one independently recalculated ExitSpec verdict;
- one qualification assessment scoped to consideration for at most a 5%
  canary; and
- one material subject or workload mutation that makes the prior
  qualification stale.

The existing strict under-20 ms, strict under-10 ms, and semantic
first-nonempty-content prospective cases remain useful conformance cases. No
future measurement or verdict may be invented to make their story convenient.

## Proposed artifact flow

```text
subject.json
scope.json
context.json
proofability.json
        |
        v
prospective-handoff/
        |
        v
external-evidence-package/     # untrusted, externally supplied input
        |
        v
qualification-receipt.json
qualification-assessment.json
        |
        v
CLI JSON + GitHub required check
```

Raw customer source, prompts, generated response content, secrets, credentials,
provider bodies, private paths, and deployment tokens must not enter public
receipts, assessments, check output, logs, or errors.

## ExitSpec pull-request train

The current train is exactly PR1–PR14. Renumbering, combining, skipping, or
adding a milestone requires an explicit user-approved plan/goal amendment before
implementation. Every current milestone is mandatory, and no implementation may
split or combine the current milestones.

### PR1 — Architecture, vocabulary, and threat contract

Claim: the product question, authority boundary, object ownership, status axes,
canonicalization, initial wedge, non-goals, and threat model are explicit.

Exit gate: contradictory authority or identity interpretations are resolved in
the specification or an ADR before executable code lands.

### PR2 — Serving-subject identity

Claim: one immutable typed subject has strict validation, canonical bytes, a
domain-separated digest, golden identity, and complete per-field mutation
coverage.

Exit gate: every material field mutation changes identity; malformed,
duplicate, extra, oversized, unsupported, and noncanonical input fails closed.

### PR3 — Qualification scope and context

Claim: workload and use scope are independent from subject identity but bound
together by one canonical qualification context.

Exit gate: subject drift and scope drift are distinguishable and both produce a
different context without ambiguous concatenation or legacy identity changes.

### PR4 — Producer capability descriptor

Claim: a server-owned, versioned provider-neutral descriptor and registry state
exactly which observations and metric semantics a declared external-evidence
profile can provide.

Exit gate: source text, provider output, browser input, aliases,
caller-supplied overrides, cyclic mappings, and nested hidden-state or subclass
bypasses cannot forge or expand capability; the descriptor has no execution,
evidence, verdict, receipt, deployment, or traffic effect.

### PR5 — Proofability engine

Claim: ExitSpec deterministically maps each frozen criterion to required,
available, missing, and incompatible observations before execution.

Exit gate: native TTFT is provable under the admitted profile, while unsupported
semantic first-nonempty TTFT stops before any external operation. The strict
frozen criterion/report schemas, byte-exact JCS golden vector, independent
literal-domain digest calculation, recursive typed-boundary defenses, and
input-bound re-evaluation all pass; no workload-path or profile-equality link
is invented.

### PR6 — Proofability service and workspace projection

Claim: the source-neutral local host can create, retrieve, and present one
bounded proofability planning preflight for an active POC. The report is
reconstructed only from exactly one immutable package-owned synthetic fixture;
it is not derived from that POC, customer/source content, live input, a
provider, or a clock.

The process-local workspace eagerly allocates exactly 128 deterministic write
stripes and retains immutable operations, a global idempotency relation, and a
latest-by-POC relation within fixed operation, key, latest, pending, per-report,
and aggregate-byte limits. Fresh publication is atomic only after PR5 verifier
and golden-root validation; accepted replay precedes capacity; reservations own
future slots and bytes; there is no eviction. Process-local state is lost on
restart and is not shared across workers.

Exit gate: the exact source-neutral API and page namespaces enforce origin-form
raw-target classification, closed method/framing/JSON/scalar/profile grammar,
code-only canonical errors, exact fresh/replay/GET semantics, and no leak. The
narrow browser projection gates the serialized URL before fetch or dynamic
render, uses the server-fixed profile and one in-memory idempotent create
action, renders through `textContent`, and uses no browser storage. It visibly
states synthetic/process-local limitations and zero deployment, production
traffic, or traffic-expansion authority. The PR6 product workspace initiates no
provider, external-network, or GPU call. Adversarial concurrency, corruption,
raw-socket, Chromium, installed-wheel, and v0.4 regression gates have no
provider, GPU, evidence, verdict, validity, agreement, deployment, or traffic
effect. Those composite gates are not a no-egress attestation: their dependency
audit may access or revalidate public vulnerability metadata under the explicit
command-level runner trust boundary, even when a seeded cache is available.

### PR7 — Provider-neutral prospective handoff boundary

Claim: ExitSpec can create and verify one canonical, provider-neutral
prospective handoff that carries the exact qualification context and declared
observation requirements without adding run IDs, measurements, credentials,
request-plan digests, evidence bundles, or verdicts.

Exit gate: a handoff with changed subject, scope, context, contract, workload,
or profile identity fails closed, and a valid handoff performs no dispatch,
execution, provider call, spend, cross-repository change, or authority action.

### PR8 — Provider-neutral external-evidence admission boundary

Claim: ExitSpec safely admits one exact context-bound external evidence package
from a local untrusted-input boundary, verifies its declared provenance and
integrity, and independently recalculates supported facts without contacting
the producer.

Exit gate: cross-context, cross-contract, synthetic-as-real, corrupt,
noncanonical, unsafe-tree, unsupported-profile, and producer-verdict inputs are
rejected before verdicting; admission does not execute, spend, dispatch, or
authorize deployment or traffic.

### PR9 — Inference performance qualification receipt

Claim: a protocol-specific immutable receipt can issue only from the original
validated context and evidence authority path.

Exit gate: `PASS`, `FAIL`, and `NOT_PROVEN` remain distinct; ingestion rejection
issues nothing; receipt-only or digest-only validation cannot establish trust.

### PR10 — Qualification validity and staleness

Claim: ExitSpec can determine whether a validated receipt remains current for a
requested subject, scope, purpose, and time without mutating historical facts.

Exit gate: every material subject/scope mutation, expiry boundary, unsupported
protocol, and malformed context produces the exact fail-closed validity state.

### PR11 — Qualification CLI

Claim: a noninteractive command validates the complete context and emits stable,
content-safe machine-readable output and exit behavior.

Proposed interface:

```bash
exitspec qualification check \
  --subject subject.json \
  --scope scope.json \
  --receipt qualification-receipt.json \
  --json
```

Exit gate: exit code `0` is reserved for a current `PASS` covering the exact
requested scope. `FAIL`, `NOT_PROVEN`, `STALE`, `EXPIRED`, `INVALID`, malformed
input, and operational failure remain machine-distinguishable and nonzero.

### PR12 — GitHub required-check integration

Claim: a minimal documented GitHub required check consumes local ExitSpec CLI or
assessment output and reports qualification state without deployment
credentials, provider access, or deployment and traffic action. It uses least
privilege with this exact workflow-level declaration:

```yaml
permissions:
  contents: read
```

It has no `id-token`, secrets, deployment or provider credentials, or write
permissions, and does no work beyond checkout, local validation, and check
reporting. It must not use `pull_request_target` for untrusted contribution code.
It must not combine privileged permissions, secrets, or an authenticated
checkout with untrusted contribution code. Repository owners configure branch-protection
required status separately outside ExitSpec; the workflow itself must not mutate
branch protection.

Exit gate: current exact-scope `PASS` succeeds; failure, missing proof,
staleness, expiry, tampering, or skipped required evaluation cannot silently
produce a passing required check. The check reports qualification state only:
`PASS` remains evidence, never deployment or traffic authorization.

### PR13 — Guided four-screen product surface

Claim: the existing `/app` shell presents the end-to-end qualification story
without a new dashboard maze or workflow-length body scroll.

Required states:

1. exact serving subject and qualification scope;
2. proofability and missing observations before execution;
3. admitted evidence and independent verdict; and
4. current qualification followed by one clearly labeled deterministic
   material mutation that makes it stale.

Exit gate: a first-time tester can explain the product and the authority
boundary after a two-minute guided run at 1280x720 and accessible narrower
layouts.

### PR14 — Adversarial closure and v0.5 candidate checkpoint

Claim: the complete train is packaged, documented, reproducible, and honest
under the v0.5 engineering and browser gates, ready for independent review.

Exit gate: all focused, engineering, installed-wheel, browser, adversarial,
documentation, and candidate-state checks are green locally. Tagging, release
publication, deployment, traffic, real capture, and cross-repository conformance
are not v0.5 train work.

## Parallel execution lanes

PR1 is the shared entry gate. After its vocabulary is accepted, three lanes may
advance with bounded dependencies:

```text
Truth kernel
  PR2 -> PR3 -> PR4 -> PR5 -> PR9 -> PR10 -> PR11

External-evidence boundary
  PR3 -> PR7 -> PR8 -> PR9

GitHub required check and product surface
  PR11 -> PR12 --+
                 +-> PR13
  PR6 ----------+

All lanes
  -----------------------------> PR14
```

The truth kernel is merge authority for status and identity. The UI never
derives its own decision. An external evidence package never assigns the
customer verdict. No PR broadens qualification into deployment or traffic
permission.

## Demo contract

The v0.5 seeded demo has four screens and one primary action per screen:

1. **What is being qualified?** Show the exact candidate identity, workload,
   frozen requirement, and bounded intended use.
2. **Can it be proven?** Show required, available, and missing observations;
   stop an unsupported criterion before spend.
3. **What does the evidence establish?** Show admitted evidence identity,
   independently recalculated facts, verdict, limitations, and receipt.
4. **Is the qualification current?** Show current exact-context status, then
   mutate one material engine or workload field and visibly require
   requalification; show the corresponding GitHub required-check state as
   evidence only, never as authorization.

The mutation is deterministic synthetic demonstration behavior and must be
labeled as such. It may not alter an immutable historical receipt or imply that
a production system was changed.

## Adversarial and security matrix

At minimum, the release train must prove fail-closed behavior for:

- duplicate, extra, missing, wrong-version, wrong-type, oversized, malformed,
  and noncanonical JSON;
- digest self-consistency without original context or evidence;
- subject, scope, contract, workload, profile, reducer, and evidence
  substitution;
- changed subject fields hidden behind an unchanged label;
- producer-supplied verdict or authorization fields;
- synthetic evidence relabeled as external or real;
- incomplete populations, failed requests, missing repetitions, and unsupported
  metric semantics;
- stale, expired, future-dated, and malformed time boundaries;
- symlinks, hard links, path escapes, extra files, replacement races, and
  oversized evidence trees;
- raw customer content, prompts, response content, credentials, private paths,
  or provider bodies entering receipts, errors, logs, browser state, or checks;
- GitHub required checks that skip a required qualification, exceed least
  privilege, use `pull_request_target` for untrusted contribution code, combine
  privileged or authenticated checkout with untrusted contribution code, mutate
  branch protection, or accidentally report success; and
- any UI, API, CLI, receipt, or assessment path implying deployment authority.

## Candidate-closure gate

### Engineering candidate gate

The ExitSpec-only v0.5 candidate is ready for independent review when:

- all 14 ExitSpec PR invariants are merged and green;
- required provider-neutral synthetic fixtures are immutable and independently
  verified;
- the complete deterministic synthetic loop works from installed package;
- required Python and Chromium collections have zero failures, errors, or
  required skips;
- v0.4 compatibility, Zoom synthetic, security, packaging, and documentation
  gates remain green;
- the public README and demo label synthetic, local, process-local,
  unauthenticated, non-durable, and non-authorizing behavior accurately; and
- the local candidate commit is available for Mission Control and independent
  MTS review. This gate neither publishes a release nor grants authority to
  conduct a real capture or external conformance activity.

## Business validation running in parallel

Before a hosted control plane, direct deployment integration, or broad adapter
matrix, conduct at least five customer-discovery sessions with inference
platform, model-serving, solutions-engineering, or AI infrastructure owners.
Ask each team to show the actual artifact and approval path used for its most
recent serving change.

The strongest continuation signal is at least two teams willing to evaluate the
CLI or required check on a real internal change. If teams consistently accept
their existing benchmark scripts and rollout thresholds, revisit the wedge
before building hosted infrastructure.

## Explicit non-goals for v0.5

- issuing a canary, deployment, shipping, release, procurement, spending, or
  traffic grant;
- directly changing GitHub protection, Kubernetes resources, Argo rollout
  state, router weights, provider configuration, or production traffic;
- any named external evidence-producer integration, GPU execution, provider
  spending, real capture, or cross-repository change;
- a Kubernetes admission controller, GitHub App, automatic rollback, or
  continuous runtime monitor;
- hosted identity, multi-tenancy, durable enterprise storage, signing keys, or
  a custom attestation system;
- universal metric execution, free-text metric semantics, or every inference
  runtime, GPU, provider, and router;
- a new benchmark leaderboard, generic LLM application-eval platform, or
  observability dashboard;
- live Zoom expansion, customer audio, provider spend, or marketplace work; and
- claims of cryptographic proof for physical hardware, chronology, authorship,
  or facts the admitted evidence cannot establish.

## Rough delivery expectation

The schedule is an estimate, not authority to skip gates:

- days 1-3: architecture, subject, scope, and context;
- days 4-7: capability descriptor, proofability, and workspace projection;
- days 8-11: prospective handoff boundary, evidence admission, receipt, and
  validity;
- days 12-14: CLI, least-privilege GitHub required check, and guided UI; and
- days 15-18: adversarial closure, documentation, and candidate readiness.

A working local vertical slice should appear before the train reaches candidate
closure. The project remains `NOT_READY` until every candidate gate required for
the claim being made has passed.

## Definition of done

The v0.5 product is complete when a first-time tester can:

1. identify the exact serving subject and frozen qualification scope;
2. see an unsupported claim rejected before execution;
3. admit an unchanged synthetic or externally supplied evidence package through
   the local boundary;
4. inspect independently recalculated facts and a typed verdict;
5. validate a purpose-bound, zero-authority receipt;
6. obtain a current qualification assessment for the exact context;
7. change one material subject or scope field and see the receipt become stale
   for the new context;
8. observe the corresponding GitHub required check report qualification state
   without any deployment action; and
9. explain that only an external human or deployment system may authorize
   traffic.

That complete loop—not the number of schemas, pull requests, dashboards, or
integrations—is the v0.5 product.
