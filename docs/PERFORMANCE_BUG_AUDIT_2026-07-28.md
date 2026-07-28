# Inference-performance post-merge bug audit

Date: 2026-07-28
Scope: the performance evidence train merged through PRs #46 and #47.

## Release decision

The local, synthetic, OpenAI-compatible performance loop is suitable for
continued development and a controlled local demo after the immediate fixes
below. It is not yet approved for a paid remote-provider demo, a real-vLLM
performance claim, customer prompt ingestion, or an in-app execution button.

The existing support-agent `/app` workflow remains unchanged and ready. The
performance runner remains CLI-only until verified results can be projected
read-only without confusing run state, agreement state, and evidence verdict.

## Immediate defects fixed

1. **Exact confirmation was not part of the operation input binding.**
   The v2 operation-input digest now includes the SHA-256 of the canonical,
   key-redacted confirmation record. Reusing one execution key with changed
   confirmer metadata now conflicts before network access.
2. **Malformed preflight evidence was classified as an external block.**
   Protocol errors now become `NOT_PROVEN`; only HTTP, timeout, and transport
   failures are classified as endpoint blocks.
3. **Invalid API-key shape could strand a durable `RUNNING` reservation.**
   Transport configuration is now validated before output or ledger creation,
   so a local configuration error can be corrected and retried safely.
4. **README capability statements contradicted the implemented CLI adapter.**
   The support-agent browser limitation and separate performance CLI path are
   now stated independently.
5. **The Evidence Pack repeated the authorization warning.**
   It now keeps the human authorization boundary once, prominently, in the
   footer.

## Required before a paid remote-provider demo

- Bind each API credential to an explicitly authorized endpoint host, port,
  path, and safe resolved network range. A frozen endpoint alone is not a
  credential policy.
- Require a frozen maximum-call and conservative maximum-cost authorization
  before a remote reservation. The current workload bounds request and output
  counts but does not prove a provider charge ceiling.
- Enforce one absolute request deadline across DNS, connection, request write,
  headers, and streamed body.
- Persist a sanitized preflight fact or remove preflight from the authoritative
  path. The current preflight is operationally useful but is not part of the
  successful Evidence Pack.

## Required before claiming real vLLM performance

- Run against a real vLLM endpoint.
- Pin and capture model revision, vLLM version, launch flags, GPU model,
  driver/CUDA versions, and relevant environment configuration.
- Describe the workload as **configured concurrency** unless achieved overlap
  is persisted and independently verifiable.
- Preserve the current wording that TTFT is client-observed and includes
  network, proxy, queueing, and inference time.

## Required before production trust claims

- Sign the receipt or registry with a runner-controlled key, or anchor
  completion in an append-only trusted store. SHA-256 detects accidental or
  partial mutation but does not stop a writer who controls both artifacts and
  SQLite from manufacturing a new self-consistent history.
- Cross-bind the durable ledger operation identity and receipt operation
  identity explicitly.
- Add reconstruction-based crash reconciliation after atomic artifact
  publication. It must never repeat network work.
- Keep synthetic prompts as the only accepted v1 input. Customer prompts need
  enforced redaction/detection evidence and a separate internal/customer
  artifact policy.

## UI gate

Do not add graphs or an in-app **Run** button for v1. One run needs two factual
rows, not a chart:

- `p95 client-observed TTFT`
- `Measured error rate`

The later read-only `/app` projection should show:

- `Agreement`
- `Run`
- `Evidence`
- one `Open Evidence Pack` action
- one collapsed `Technical details` disclosure

`Run: Blocked` must render with `Evidence: —`; it must never be presented as an
evidence verdict.

## Verification

Focused regression gate:

```text
python -m pytest -q \
  tests/test_performance_operations.py \
  tests/test_performance_runner.py \
  tests/test_performance_cli.py
```

The repository engineering gate remains the merge requirement.
