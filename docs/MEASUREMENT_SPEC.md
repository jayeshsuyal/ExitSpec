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
6. Performance adapter that wraps a mature load tool such as GuideLLM.

Brick 1 implements a deterministic local exact-tool-selection adapter only.

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

## Latency criteria (planned)

Record complete distributions and report p50, p95, p99, minimum/maximum, successful sample count, errors/timeouts, traffic shape, prompt/output distributions, warm/cold state, and environment metadata.

The proposed success rule is:

```text
upper bootstrap confidence bound for p95 < approved threshold
```

Before implementation, the team must approve how timeouts, errors, retries, client cancellations, and partial responses enter the denominator. A fast p95 that omits a high error rate is not a meaningful production conclusion.

## Cost criteria (planned)

Separate estimated token cost, tool/platform cost, retry cost, total cost, cost per request, cost per successful task, and billed cost where available. Price snapshots identify provider, model, unit, and effective date.

## Privacy criteria (planned)

An absence-of-detection result means only that the declared detector and redaction policy found no PII in the persisted artifact. The report must include detector/version, policy, coverage limitations, and positive-control test evidence.

## Failure taxonomy

| Class | Example | Criterion impact |
| --- | --- | --- |
| External block | Missing customer credential, endpoint outage | `BLOCKED` |
| Internal adapter failure | Parser crash, unsupported response format | `NOT_PROVEN`; run may be `FAILED_INTERNAL` |
| Evidence invalid | Hash mismatch, missing model metadata | `NOT_PROVEN` |
| Customer criterion failure | Rate below threshold with sufficient evidence | `FAIL` |
| Statistical inconclusiveness | Favorable point estimate but bound below threshold | `NOT_PROVEN` |
