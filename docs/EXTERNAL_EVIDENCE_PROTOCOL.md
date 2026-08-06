# External Evidence Protocol

- Status: Accepted architecture; bounded v1 importer authorized
- Date: 2026-08-05
- Implementation amendment: 2026-08-06
- Decision owners: ExitSpec maintainers
- Scope: Future evidence produced outside ExitSpec, beginning with Inferdrome

## Purpose

ExitSpec may evaluate evidence produced by Inferdrome or another external
measurement system. This document fixes the authority, compatibility, trust,
privacy, and immutability rules for that boundary. The original decision
deferred every wire-format and runtime choice until a pinned producer fixture
existed. The implementation amendment below records the narrow evidence that
unlocked the first offline importer; it does not weaken the original rules.

This remains the constitutional architecture decision rather than the importer
API specification. The bounded implementation is documented separately in
[`INFERDROME_IMPORT.md`](INFERDROME_IMPORT.md). It does not change an existing
frozen customer contract or authorize arbitrary external bundles.

## Implementation amendment: bounded Inferdrome v1 importer

The first importer gate is satisfied only for the exact synthetic capability
fixtures and schema digests reviewed on 2026-08-06. The reviewed evidence
includes:

1. native vLLM `0.26.0` as the exact pinned producer version;
2. the untouched detailed native artifact at
   `tests/fixtures/inferdrome/vllm-template/native/benchmark-result.json`;
3. explicit available and unavailable observations in the canonical fixture;
4. the native metric identity `vllm_first_choices_event_v0_26`, which remains
   incompatible with `first_nonempty_choices_delta_content_v1`;
5. explicit request ordering and request-ID derivation in
   `request-plan.json`;
6. artifact-level privacy classification recording that native response
   content is present; and
7. the sealed fake and pinned-vLLM golden fixtures under
   `tests/fixtures/inferdrome/`.

That evidence authorizes only the offline, bounded `inferdrome.evidence.v1`
reader in `inferdrome_bundle.py` and `inferdrome_import.py`, together with the
eight exact vendored schema digests it verifies. The importer executes no
producer code, performs no network request, admits no synthetic fixture as
customer evidence, and must independently recalculate supported measurements.

Any new producer or producer version, schema version, adapter, artifact role,
metric definition, population, reducer, provenance requirement, or privacy
class remains unsupported until a separate reviewed fixture and compatibility
decision explicitly authorize it. Missing identity never inherits this v1
authorization.

## Governing invariant

An external producer may report what it measured. Only ExitSpec may decide
whether those measurements satisfy a customer-confirmed ExitSpec criterion.

```text
Inferdrome measurement run
        |
        v
sealed native artifacts + canonical records + provenance claims
        |
        v
future ExitSpec ingestion validation
        |
        +-- unsafe, corrupt, unsupported, or incompatible
        |        -> INGESTION_REJECTED (no acceptance verdict)
        |
        v
independent ExitSpec parsing and recalculation
        |
        +-- compatible but insufficient -> NOT_PROVEN
        |
        +-- compatible and sufficient -> PASS or FAIL
```

ExitSpec must never trust, copy, translate, or import a producer-generated
acceptance verdict. If a native artifact happens to contain a producer verdict,
that value has no authority over ExitSpec acceptance.

## Current metric boundary

The existing performance path remains unchanged:

| Identity | Frozen current meaning |
| --- | --- |
| Adapter | `vllm_streaming_latency` |
| Adapter version | `1.0.0` |
| First-token definition | `first_nonempty_choices_delta_content_v1` |
| Criterion example | `vllm-ttft-v2` frozen contract artifacts |
| Observation point | ExitSpec client monotonic clock |
| Latency population | Successful measured attempts with valid TTFT |
| Reduction | Nearest-rank p95, converted to the frozen criterion unit |

Despite its name, `vllm_streaming_latency` is ExitSpec's custom bounded
OpenAI-compatible streaming probe. It does not invoke or wrap native
`vllm bench serve`. The probe ignores role-only events, `null` content, and
empty-string content. TTFT stops only when a `choices[].delta.content` value is
a non-empty string.

As inspected on 2026-08-05, native vLLM's OpenAI chat-completions benchmark
records TTFT at the first streamed event with a non-empty `choices` list and
then appends `delta.content` with `content or ""`. A role-only or empty-content
event can therefore stop native vLLM's TTFT clock. The relevant upstream logic
is visible in
[`benchmarks/backend_request_func.py`](https://github.com/vllm-project/vllm/blob/821717118fc26667dd474b9b0ab81d29259dfc5c/benchmarks/backend_request_func.py#L439-L454).
That inspected commit is a research reference, not the future integration's
pinned producer version.

These measurements are not equivalent. Native vLLM TTFT must not satisfy the
existing first-nonempty-content criterion, even when both values are called
`ttft`, use milliseconds, or happen to be numerically close. A future native
metric-definition ID could be `vllm_first_choices_event_v1`; that name is an
illustrative semantic identifier, not a frozen wire field or schema decision.

The existing adapter ID, first-token-definition ID, `vllm-ttft-v2` artifacts,
tests, calculations, and evidence semantics must not be renamed or reinterpreted
to make an external bundle appear compatible.

## Ownership boundary

Inferdrome owns production of:

- measurements;
- untouched native artifacts;
- canonical producer records;
- producer-side execution metadata; and
- provenance claims about those records.

ExitSpec owns:

- defensive parsing and structural validation;
- integrity and semantic-compatibility validation;
- independent recalculation of every supported measurement;
- application of the exact frozen customer criterion;
- evidence-sufficiency decisions; and
- the exclusive release of `PASS`, `FAIL`, or `NOT_PROVEN`.

Neither Inferdrome nor another producer may freeze an ExitSpec contract, weaken
its evidence requirements, choose a substitute metric, or issue an ExitSpec
acceptance verdict.

## Separate ingestion from acceptance

Future external evidence has two sequential decision boundaries.

### Ingestion disposition

`INGESTION_REJECTED` means ExitSpec did not admit the bundle into acceptance
evaluation because it was invalid, corrupt, unsafe, unsupported, or
semantically incompatible. Bundle rejection is not evidence that the customer
criterion passed or failed, and it is not `NOT_PROVEN`.

`INGESTION_REJECTED` is architectural vocabulary in this document. This change
does not add it to an existing enum, API, persisted object, Evidence Pack, or
run-state machine. A future importer specification must define its concrete
representation without overloading `VerdictStatus`.

### Acceptance verdict

Only an internally valid and semantically compatible bundle reaches acceptance
evaluation:

| Accepted evidence state | ExitSpec result |
| --- | --- |
| Compatible but insufficient for the frozen rule | `NOT_PROVEN` |
| Compatible, sufficient, and satisfies the frozen rule | `PASS` |
| Compatible, sufficient, and violates the frozen rule | `FAIL` |

The current internal performance runner already uses `NOT_PROVEN` for corrupt
or invalid evidence created inside its run boundary. That behavior remains
unchanged. The future external-ingestion boundary is an additional earlier
boundary; it must not retroactively change current run or verdict semantics.

## Exact semantic compatibility

A future importer must fail closed unless the frozen criterion is compatible
with the bundle's complete evidence identity. At minimum, compatibility must
cover:

- evidence schema and exact version;
- producer identity and producer version;
- adapter ID and adapter version;
- metric-definition ID;
- complete population policy;
- reducer ID and reducer version;
- request-plan or workload digest;
- timing units and percentile method; and
- the provenance level required for every criterion-relevant fact.

Every required component participates in compatibility. Matching field names,
display labels, units, or high-level phrases such as "p95 TTFT" is insufficient.
Missing identity is not a wildcard. A newer producer version is not compatible
merely because it parses. A conversion is not permitted unless a future frozen
criterion and reviewed adapter explicitly define and test that conversion.

Semantic incompatibility produces `INGESTION_REJECTED`; ExitSpec must not relabel
the external measurement, silently substitute a reducer, or weaken the frozen
criterion to admit it.

## Trust boundary and integrity limits

External evidence is always untrusted input.

Hashes and a sealed bundle can prove internal byte consistency and detect
mutation after publication. Alone, they do not prove:

- authorship or control of the producer identity;
- truthful execution;
- hardware identity;
- model, tokenizer, or engine identity;
- launch flags or runtime configuration;
- workload completeness before the bundle was sealed;
- request execution against the claimed endpoint;
- ownership of a remote endpoint; or
- that a claimed remote endpoint was actually vLLM.

A self-consistent fabricated bundle remains fabricated. Future signatures,
attestations, trusted runners, or server-side receipts may strengthen specific
claims, but they do not alter the rule that ExitSpec independently validates
and recalculates before verdicting.

## Field-level provenance

Future evidence may classify individual facts using these semantic categories:

| Provenance | Meaning |
| --- | --- |
| `DECLARED` | Supplied as a claim by a user, configuration, or producer |
| `CLIENT_OBSERVED` | Observed by the measurement client at its trust boundary |
| `SERVER_REPORTED` | Returned by the target server or its API |
| `LOCALLY_VERIFIED` | Independently verified inside an approved local trust boundary |
| `UNKNOWN` | Origin or verification level is unavailable |

These categories do not define a serialized field yet. The frozen ExitSpec
criterion must decide which provenance level is sufficient for each required
fact. A stronger-sounding label must not be inferred from a weaker source.

Merely attaching to an endpoint cannot establish model commit, tokenizer
identity, engine version, launch flags, GPU type, server ownership, or complete
workload execution. Such facts remain `DECLARED`, `SERVER_REPORTED`, or
`UNKNOWN` unless an approved mechanism independently verifies them.

## Privacy boundary

Native vLLM detailed output may contain generated response text. The initial
Inferdrome integration may admit only explicitly synthetic, non-sensitive
bundles whose complete contents are approved for ExitSpec processing and
retention.

ExitSpec must inspect the actual untouched native annex. It must not claim
`include_response_content: false` when that annex contains generated output,
even if a normalized producer record omits the text. Redacted annexes,
encrypted annexes, split retention, customer-sensitive workloads, and
separately retained native artifacts are deferred designs. None is authorized
by this document.

## Repetitions and populations

Inferdrome repetitions remain independent runs with independent identities and
artifacts. ExitSpec must not pool attempts, percentiles, failures, or summaries
across repetitions unless the frozen customer contract explicitly defines:

- the multi-run acceptance population;
- inclusion and exclusion rules;
- weighting;
- the aggregation or reducer method; and
- evidence-sufficiency requirements.

Repeated runs may be displayed side by side without becoming one acceptance
population.

## Immutability and independent verification

A completed external bundle must be sealed and immutable. Recalculation must
read the existing sealed bytes. It may produce a separate, content-addressed
ExitSpec validation or verdict artifact, but it must never rewrite, normalize
in place, repair, or reseal the producer bundle.

Before releasing a verdict, ExitSpec must independently:

1. parse the bundle defensively;
2. validate bounds, structure, internal identity, and integrity;
3. establish exact semantic compatibility with the frozen criterion;
4. enforce the criterion's required provenance levels;
5. reconstruct the supported measurement population from admitted records;
6. recalculate the supported reducer and measurement values; and
7. apply the frozen customer rule through ExitSpec's verdict authority.

Producer summaries can be compared for diagnostics but cannot replace any of
these steps.

## Importer gate

ExitSpec must not define a new evidence version, extend an importer, add a
runtime ingestion state, or freeze a new field mapping until the external
producer completes a pinned capability spike and supplies all of the following:

1. the exact pinned vLLM version or immutable source revision;
2. one untouched native detailed-output artifact;
3. a documented matrix of fields that are available, unavailable, declared,
   observed, and derivable;
4. verified TTFT event semantics for that exact version and backend;
5. verified request ordering and request-ID behavior;
6. a privacy classification of every native and normalized artifact; and
7. the first reviewable golden fixture.

Only that fixture may ground a new importer schema or compatibility rule. Field
names must be learned from evidence, not invented in advance. The v1 amendment
above records the sole currently authorized satisfaction of this gate.

## Non-goals of this decision

This decision and its v1 amendment do not:

- authorize an online importer, producer execution, or network ingestion path;
- authorize an unpinned producer, schema, adapter, or field mapping;
- modify the current `vllm_streaming_latency` adapter;
- rename any frozen identifier or artifact;
- change current performance populations, reducers, verdicts, or Evidence
  Packs;
- claim that native vLLM TTFT satisfies ExitSpec's current TTFT criterion;
- authenticate Inferdrome or a target endpoint;
- authorize sensitive native-output ingestion; or
- define cross-run aggregation.
