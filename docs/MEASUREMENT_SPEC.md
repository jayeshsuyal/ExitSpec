# Measurement Specification

## Measurement principle

An adapter produces facts and evidence. The verdict engine applies the approved contract rule. Keeping those responsibilities separate prevents an adapter from turning a partial measurement into a silent pass.

## Adapter interface

Each adapter exposes a name, version, validation phase, execution phase, structured result, and artifact list.

```text
validate(plan, environment)
execute(plan, fixture, run_context)
  -> measurement facts + evidence artifact references + typed failure information
```

Adapters are independently versioned and tested. A generated adapter is untrusted until reviewed and tested.

## Required version-one adapters

1. JSON/schema-validity adapter.
2. Exact tool-selection adapter.
3. Endpoint error/timeout adapter.
4. Token and estimated-cost adapter.
5. PII detection/redaction verification adapter.
6. Bounded streaming-performance adapter; larger-scale future versions may
   wrap a mature load tool such as GuideLLM.

The browser Brick 1 flow implements the deterministic local
exact-tool-selection adapter. The separate `exitspec performance` command
implements the first bounded OpenAI-compatible streaming-latency adapter. Its
frozen adapter ID, `vllm_streaming_latency`, names ExitSpec's custom probe; it
does not invoke or wrap native `vllm bench serve`.

## Proportion criteria

For `k` observed successes among `n` valid samples, the observed rate is:

```text
p = k / n
```

For a two-sided confidence level `1 - alpha`, let `z` be the corresponding normal quantile. The Wilson lower bound is:

```text
lower = (p + z²/(2n) - z * sqrt((p(1-p) + z²/(4n))/n)) / (1 + z²/n)
```

ExitSpec reports the point estimate and lower bound. It does not use the point estimate alone to establish a pass.

### Brick 1 decision rule

For `TOOL-SELECT-01`:

| Condition | Verdict |
| --- | --- |
| External dependency blocks execution | `BLOCKED` |
| Adapter error, invalid metadata, workload/hash mismatch, corrupted evidence | `NOT_PROVEN` |
| Fewer than 200 valid samples | `NOT_PROVEN` |
| Point estimate below 95% with enough valid samples | `FAIL` |
| Wilson lower bound at or above 95% | `PASS` |
| Point estimate meets threshold but lower bound does not | `NOT_PROVEN` |

With a two-sided 95% Wilson lower bound and 200 samples, 197/200 is the first passing count for a 95% threshold. This is an intentional teaching example: a high observed percentage can still be insufficient to establish the claim.

## Inference performance v1

The v1 CLI adapter executes one customer-confirmed frozen workload against an
OpenAI-compatible streaming endpoint. It first reserves a durable idempotency
operation, then sends one bounded preflight request. External preflight failure
is `BLOCKED` and starts no measured workload.

For an available endpoint, the adapter runs the exact frozen warmup and measured
request counts with the configured client concurrency recorded in the manifest.
This is a load-generator setting, not proof of achieved request overlap. The
adapter records one sanitized terminal record per attempt. Prompt and response
text, raw API keys, and raw execution idempotency keys never enter measurement
records or the customer Evidence Pack.

The first-token definition is the first non-empty
`choices[].delta.content` event. A valid `[DONE]` event terminates measurement;
the client does not wait for the server to close a persistent SSE connection.
One monotonic absolute timeout covers connection setup, request write, response
headers, and the streamed body; later phases receive only the remaining time.

The exact frozen definition ID is
`first_nonempty_choices_delta_content_v1`. Role-only, `null`-content, and
empty-string-content events do not stop this probe's TTFT clock. Native
`vllm bench serve` currently records OpenAI chat-completions TTFT on the first
event with a non-empty `choices` list, even when its content is empty. The two
metrics are semantically incompatible and must never satisfy one another's
criteria. Existing `vllm_streaming_latency`, `vllm-ttft-v2`, manifest, reducer,
test, and evidence identifiers remain unchanged.

The current composite criterion is:

```text
client-observed nearest-rank p95 TTFT < approved threshold
AND
measured external-error count / all measured attempts < approved threshold
```

Warmups are excluded. Successful measured requests enter the TTFT distribution.
HTTP errors, timeouts, malformed streams, and transport errors enter the error
denominator. Cancellation, internal adapter failure, missing records, mixed
executions, or any integrity mismatch produce `NOT_PROVEN`, never `PASS`.

The current bounded example deliberately uses exactly 100 measured attempts and a strict
error-rate threshold below 1%. Therefore zero errors passes that rule and one
error fails it.

TTFT is client-observed. It includes network, proxy, queueing, and inference
time; it is not presented as GPU execution latency.

The authoritative runner reconstructs the contract, confirmation, workload,
successful readiness probe, manifest, records, receipt, calculations, verdict,
and static HTML from persisted bytes and recalculates the decision before
returning it. The readiness probe is sanitized, hashed, and checked against its
independently derived one-request configuration; it is not part of the customer
criterion denominator. SQLite binds the ledger run, probe execution, receipt,
and artifact-registry hash. A crashed `RUNNING` operation is never silently
rerun or auto-promoted from an orphaned directory.

Future performance versions may add complete p50/p95/p99 distributions,
throughput, output-token distributions, warm/cold state, environment metadata,
and confidence intervals. Those are not claimed by v1.

## External evidence import (bounded v1)

The Inferdrome boundary is governed by
[EXTERNAL_EVIDENCE_PROTOCOL.md](EXTERNAL_EVIDENCE_PROTOCOL.md). Inferdrome may
produce measurements and sealed artifacts, but ExitSpec must independently
validate semantic compatibility, reconstruct supported populations, recalculate
measurements, and exclusively issue acceptance verdicts. Invalid, unsafe,
unsupported, or incompatible external bundles are rejected before acceptance;
they do not receive an acceptance verdict.

The first offline importer accepts only the exact vendored Inferdrome v1 schema
digests and independently recalculates supported facts from sealed canonical
records. Its compatibility tests are grounded in the pinned-vLLM `0.26.0`
golden fixture. Native `vllm_first_choices_event_v0_26` TTFT remains distinct
from ExitSpec's `first_nonempty_choices_delta_content_v1`, so the current TTFT
criterion is `NOT_PROVEN` rather than silently translated. New schema versions,
producer versions, online ingestion, and sensitive bundles remain blocked on a
new reviewed fixture and compatibility decision.

## Cost criteria (planned)

Separate estimated token cost, tool/platform cost, retry cost, total cost, cost per request, cost per successful task, and billed cost where available. Price snapshots identify provider, model, unit, and effective date.

## Privacy criteria (planned)

An absence-of-detection result means only that the declared detector and redaction policy found no PII in the persisted artifact. The report must include detector/version, policy, coverage limitations, and positive-control test evidence.

## Failure taxonomy

This table describes the current in-process adapter and run boundary. Its
existing invalid-evidence behavior remains unchanged. A future external bundle
first passes the separate ingestion boundary defined in the external-evidence
protocol; `INGESTION_REJECTED` there is not an acceptance verdict.

| Class | Example | Criterion impact |
| --- | --- | --- |
| External block | Missing customer credential, endpoint outage | `BLOCKED` |
| Internal adapter failure | Parser crash, unsupported response format | `NOT_PROVEN`; run may be `FAILED_INTERNAL` |
| Evidence invalid | Hash mismatch, missing model metadata | `NOT_PROVEN` |
| Customer criterion failure | Rate below threshold with sufficient evidence | `FAIL` |
| Statistical inconclusiveness | Favorable point estimate but bound below threshold | `NOT_PROVEN` |
