# Inferdrome evidence import

Status: independent v1 importer, pinned managed-vLLM receipt path, and pathless
local `/app` orchestration implemented through 2026-08-20.

ExitSpec can ingest a completed `inferdrome.evidence.v1` directory without
installing or importing Inferdrome. Inferdrome owns execution and canonical
request evidence; ExitSpec owns evidence sufficiency, customer-contract
applicability, acceptance calculation, and the first external receipt for the
bundle digest.

## Trust boundary

The importer is offline and fail-closed. Before a verdict is considered it:

1. walks the directory with bounded file, byte, depth, and JSONL-line limits;
2. rejects symlinks, hard links, non-regular nodes, traversal paths, undeclared
   files, and file or directory replacement races;
3. verifies the pinned digests of all eight vendored Inferdrome public schemas;
4. validates the complete v1 role inventory and exact artifact-hash manifest;
5. computes the domain-separated Inferdrome bundle digest;
6. rejects anything except `CUSTOMER_ELIGIBLE` attached-endpoint vLLM evidence;
7. checks run, experiment, producer, target, environment, plan, execution,
   invocation, preflight, and digest cross-bindings;
8. validates every canonical request-record line and rejects duplicate IDs,
   invalid outcomes, timestamp inversions, and population drift; and
9. independently recalculates counts, failure ratio, nearest-rank latency
   quantiles, spans, and throughput from raw records.

Stored Inferdrome summary measurements are never verdict inputs. They are
compared with ExitSpec's independent recalculation; disagreement rejects the
bundle as internally inconsistent.

## Rejection versus `NOT_PROVEN`

Corrupt, unsafe, unsupported, synthetic, and ineligible bundles are rejected,
so no ingestion receipt is issued. A valid customer bundle can still be
accepted while failing applicability:

- a frozen-contract link, model, endpoint, workload, population, traffic,
  sampling, or adapter mismatch becomes `NOT_PROVEN`;
- insufficient successful samples become `NOT_PROVEN`;
- a known threshold violation becomes `FAIL`; and
- only fully applicable, sufficient evidence can become `PASS`.

This distinction is material for vLLM 0.26.0. Inferdrome records
`vllm_first_choices_event_v0_26`, while ExitSpec's current performance workload
defines TTFT as `first_nonempty_choices_delta_content_v1`. The importer does
not equate them. It can independently decide the reliability rule, but the
current TTFT rule remains `NOT_PROVEN` unless a future frozen context and
evidence format share the same observation definition.

## Python API

```python
from datetime import UTC, datetime
from pathlib import Path

from exitspec.inferdrome_import import import_inferdrome_bundle

result = import_inferdrome_bundle(
    Path("runs/run-.../bundle"),
    validated_performance_context,
    customer_confirmation,
    expected_bundle_digest="sha256:...",  # optional retained transport anchor
    received_at=datetime.now(UTC),
)

print(result.performance_verdict.verdict.value)
print(result.receipt.receipt_id)
```

The context must already be frozen, digest-valid, and bound to the supplied
affirmative customer confirmation. The immutable receipt binds the bundle
digest, frozen contract hash, criterion, verifier version, receipt time,
importer calculation version, acceptance verdict, applicability codes, and
independent recalculation digest.

## Guided `/app` orchestration

The local app can coordinate the same offline importer without giving the
browser filesystem authority:

```bash
exitspec serve \
  --inferdrome-runs-root /absolute/path/to/inferdrome/runs \
  --open-browser
```

The operator-configured root is immutable for the server lifetime. ExitSpec
scans only the root or its direct run/bundle children, does not follow
symlinks, and publishes only fully verified `CUSTOMER_ELIGIBLE` identities.
The browser receives only `run_id` and `bundle_digest`; it cannot provide a
path, schema, producer version, verifier setting, contract, confirmation, or
verdict.

The guided flow is:

```text
customer-confirmed frozen performance agreement
        -> evidence method: INFERDROME_EXTERNAL_BUNDLE
        -> employee selects one cataloged run ID + digest
        -> explicit import acknowledgement
        -> digest re-verification and defensive ingestion
        -> independent ExitSpec recalculation
        -> INGESTION_REJECTED with no verdict
           or PASS / FAIL / NOT_PROVEN
        -> immutable ExitSpec Evidence Pack
        -> existing human handoff or stop decision
```

That flow remains the generic v2 compatibility path. Native vLLM first-event
TTFT is deliberately not substituted for its frozen first-nonempty-content
metric, so native latency evidence normally remains `NOT_PROVEN` for that
criterion.

## Pinned managed-vLLM v3 path

The retained real-A10 handoff has a separate, narrower v3 path. It does not
rename or weaken the generic importer. Before customer review, the employee
chooses one independently verified, pathless managed-evidence profile from:

```text
GET /api/pocs/{poc_id}/agreement/managed-evidence
```

The response contains only bounded display facts and the verified `run_id`
plus bundle digest. It never contains a filesystem path, runs root, archive
path, native output, request content, or response text.

The managed flow is:

```text
reviewed customer requirements
        -> pathless managed profile selection
        -> one verified projection captured in the agreement draft
        -> exact run ID + bundle digest hash-bound in the workload reference
        -> customer review and confirmation of native metric semantics
        -> frozen v3 contract
        -> re-resolve and reverify the same sealed bundle
        -> independently recalculate canonical request records
        -> INGESTION_REJECTED with no verdict or PASS / FAIL / NOT_PROVEN
        -> managed v2 receipt and immutable Evidence Pack
```

Review and freeze use the captured projection; they do not reread mutable
producer storage. Import is the separate trust boundary and always resolves
and verifies the frozen selection again. A caller cannot switch to a different
run or digest after confirmation.

The managed contract explicitly freezes:

- `vllm_first_choices_event_v0_26` with `nearest_rank_v1`;
- 100 measured attempts and 100 required successful TTFT samples;
- all measured records as the reliability denominator;
- failed-or-anomalous native measured records as the numerator;
- configured maximum concurrency as distinct from achieved overlap;
- retrospective chronology and absent producer contract link; and
- unavailable retry, hardware-attestation, execution-attestation, and
  production-authorization claims.

Invalid, unsafe, corrupt, synthetic, or unsupported input is
`INGESTION_REJECTED` and has no acceptance verdict. Only an internally valid,
compatible bundle can become `NOT_PROVEN`, `FAIL`, or `PASS`.

An Inferdrome agreement cannot call ExitSpec's local streaming probe, and a
local-probe agreement cannot import Inferdrome evidence. The evidence method is
selected before customer review, displayed in the customer agreement, bound
through the criterion adapter and confirmation fingerprint, and checked again
server-side at execution/import time.

`INGESTION_REJECTED` and `FAILED_CLOSED` are import-operation states, not
acceptance verdicts. A valid but insufficient bundle may produce a verified
`NOT_PROVEN` Evidence Pack. In particular, generic producer `FAILED` records
cannot be silently classified as one of ExitSpec's frozen external-error
outcomes; where that classification is required but unavailable, the
reliability criterion remains `NOT_PROVEN`.

## Security scope

SHA-256 and the read-only verifier establish integrity and internal
consistency, not producer authorship. The receipt is ExitSpec's first external
anchor for the exact received bundle digest. Signing or transparency-log
publication remains future work.

The importer intentionally has no remote evidence-ingestion path, does not
execute producer commands, does not load Inferdrome Python modules, and does
not trust native benchmark summaries as acceptance facts. The loopback browser
route only orchestrates reads from the explicit local runs root; it is not a
bundle upload or producer connection.
