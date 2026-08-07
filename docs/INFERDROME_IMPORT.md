# Inferdrome evidence import

Status: implemented for the independent v1 importer on 2026-08-06.

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

## Security scope

SHA-256 and the read-only verifier establish integrity and internal
consistency, not producer authorship. The receipt is ExitSpec's first external
anchor for the exact received bundle digest. Signing or transparency-log
publication remains future work.

The importer intentionally has no network path, does not execute producer
commands, does not load Inferdrome Python modules, and does not trust native
benchmark summaries as acceptance facts.
