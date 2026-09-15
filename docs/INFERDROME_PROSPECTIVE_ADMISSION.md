# Offline prospective P1 admission

The [CLI receipt bridge](INFERDROME_RECEIPT_BRIDGE.md) now supplies explicit
operator-started dispatch, admission and durable local receipt retention. The
Python functions documented here remain separately usable and do not own storage.

The Python API independently checks a retained Inferdrome bundle against one
frozen P1 agreement and returns an immutable conformance receipt. V4 identifies
the agreement criterion; the new receipt schema is
`exitspec.inferdrome-prospective-receipt.v1`. This is an operator API in the
installed `exitspec` package, with no new CLI command or browser control.

## Required inputs

Use an ExitSpec environment containing this implementation. Retain the complete
P1 handoff directory (contracts, confirmations, sources, manifest and `.complete`)
and the complete original bundle directory. Both paths must be absolute and
contain no symlinks. Keep both trees immutable during admission and later replay.

Supply these pins from the retained handoff and run records, independently of
the receipt being checked. Do not replace a trusted pin with a hash of an altered
input simply to make validation pass.

| Argument | Required value |
| --- | --- |
| `handoff_path` | Absolute `str` or `Path` to the complete frozen P1 directory. |
| `case_id` | One of the three exact case IDs below. |
| `bundle_path` | Absolute `str` or `Path` to the retained evidence bundle. |
| `expected_handoff_manifest_sha256` | Bare lowercase 64-hex SHA-256 of the agreed handoff manifest bytes. |
| `expected_bundle_digest` | Exact tagged `sha256:<64 lowercase hex>` bundle digest from the retained run record; this is the producer's domain-separated artifact-manifest digest, not a directory or ZIP hash. |
| `expected_run_id` | Exact `run-<32 lowercase hex>` from the retained run record. |
| `received_at` | Explicit timezone-aware Python `datetime`; normalized to UTC and retained as caller-declared audit time. |
| `limits` | Optional `InferdromeBundleLimits`, with positive integer values no greater than its defaults. Omit to use defaults. |

| Case ID | Requested measurement | Strict p95 bound |
| --- | --- | --- |
| `native-p95-under-20ms` | Native vLLM first-choices event | `< 20,000,000 ns` |
| `native-p95-under-10ms` | Native vLLM first-choices event | `< 10,000,000 ns` |
| `semantic-first-nonempty-under-20ms` | First nonempty choices delta content | `< 20,000,000 ns` |

Each case requires its own freshly produced, exact case-linked bundle. Changing
links in an old bundle or reusing one run for another threshold is not admission.
The existing source YAML and workload in the handoff are the producer inputs.

## Call, retain, and re-verify

The following helper is ordinary operator Python, not an additional product API.
Pass the actual paths and pins as arguments. It returns canonical receipt bytes
only after independent admission and a second, complete re-verification. No
receipt file is created by the product functions.

```python
from exitspec.inferdrome_prospective_admission import (
    admit_prospective_bundle,
    verify_prospective_receipt,
)
from exitspec.inferdrome_prospective_receipt import serialize_prospective_receipt


def admit_and_recheck(
    handoff_path, case_id, bundle_path, *,
    manifest_sha256, bundle_digest, run_id, received_at,
):
    pins = dict(
        expected_handoff_manifest_sha256=manifest_sha256,
        expected_bundle_digest=bundle_digest,
        expected_run_id=run_id,
    )
    receipt = admit_prospective_bundle(
        handoff_path, case_id, bundle_path,
        **pins, received_at=received_at,
    )
    raw = serialize_prospective_receipt(receipt)
    checked = verify_prospective_receipt(
        raw, handoff_path, case_id, bundle_path, **pins,
    )
    assert checked.receipt_id == receipt.receipt_id
    return raw
```

An aware audit time can be captured once with `datetime.now(UTC)` from Python's
`datetime` module. Retain that value for deterministic repeat admission. For
later verification, pass the retained canonical bytes to
`verify_prospective_receipt` with the original paths and independent pins; it
uses the audit time inside those bytes. If the operator persists the output,
write it without indentation or an added newline, for example with an exclusive
binary file open (`open(output_path, "xb")`). The operator owns that file and
its durable storage policy. Do not overwrite a retained receipt.

Both public functions return `InferdromeProspectiveReceiptV1`. Relevant fields
include `receipt_id`, `acceptance_verdict`, `applicability_codes`, `metric`,
`population`, `handoff_case` and `assurance`. The `ipr1_` identity binds every
receipt field using a separate SHA-256 domain and canonical JSON. Identical
input bytes, pins and audit time produce identical receipt bytes. A different
valid audit time creates a different receipt identity.

`parse_prospective_receipt(raw)` only checks the bounded representation,
internal relationships and receipt identity. A self-rehashed receipt is not
independently verified evidence. Use `verify_prospective_receipt` for that claim;
it rereads both trees, repeats admission, and requires exact canonical equality.

## Outcomes and refusals

Admission checks the affirmative frozen confirmation, both producer contract
links, exact source YAML digest, target and profile pins, run and bundle pins,
configured methodology, all 100 ordered prompt texts and hashes, and the
independently recalculated measurements. The receipt stores the observed plan
digest; P1 has no invented pre-run requested plan digest.

With valid exact P1 evidence, latency uses nearest-rank p95 over successful
measured requests and a strict threshold. Equality fails. Reliability requires
strictly fewer than 1% failed requests: one failure among the required 100 is
`FAIL`. Anomalous requests are already included in the failed count. A valid
reliability failure takes precedence over a TTFT mismatch or sample shortfall.
With passing reliability, the semantic case is `NOT_PROVEN` because native
first-choices timing does not establish first-nonempty-content timing. Invalid
evidence cannot establish a performance `FAIL`.

`ProspectiveAdmissionRejected` exposes a stable `.code` and a tuple of bounded
`.field_paths`; it returns no partial receipt. The existing independent verifier
may instead raise `InferdromeBundleRejected`, whose original refusal is retained.
Catch both exception types at the operator boundary and retain the refusal
separately from successful receipt bytes.

| Code | Meaning |
| --- | --- |
| `CONTEXT_NOT_AUTHORIZED` | Missing, unsafe, changed or nonexact frozen P1 context/confirmation. |
| `BINDING_MISMATCH` | Invalid argument/pin, wrong manifest/run/bundle binding, or wrong/missing contract link. |
| Existing `InferdromeBundleRejected` code | Unsafe, malformed, inconsistent, unsupported or ineligible bundle, including synthetic evidence. |
| `P1_NOT_APPLICABLE` | Validated facts fail the fixed P1 methodology, exact ordered workload or measurement invariants; no verdict issued. |
| `DECLARED_CHRONOLOGY_CONTRADICTION` | Invalid/naive time or contradictory declared order. |
| `INVALID_RECEIPT` | Supplied receipt fails strict bounded canonical parsing or internal validation. |
| `RECEIPT_MISMATCH` | A parseable receipt differs from complete independent re-admission. |

Re-verification can also raise any admission refusal in the table. No refusal
updates the dispatch record or yields a performance verdict.

## Bounds, assurance, and completion boundary

The handoff reader requires the exact 12 files and four directories, with at
most 8 MiB per file and 16 MiB total. It takes checked, nonblocking, no-follow
file snapshots and rechecks the original tree after evaluation. Validation uses
a private temporary copy that is removed on exit. Default bundle limits are
64 files, 64 directories, 256 MiB per file, 512 MiB total, 4 MiB per JSONL line,
100,000 records and depth eight; exact P1 admission further requires 100 measured
records. Receipt bytes are limited to 64 KiB, depth 24, 8,192 nodes, 8,192-character
strings and JSON-safe integers. Duplicate keys, floats and noncanonical bytes
are refused.

The declared timestamps must satisfy creation ≤ approval ≤ confirmation ≤
freeze ≤ execution start ≤ execution end ≤ receive time. This checks consistency
of declarations. `temporal_assurance` remains `UNAVAILABLE`,
`contract_preceded_measurement` remains `null`, and confirmer identity remains
process-local and unauthenticated. Hardware, execution, actual achieved
concurrency and producer retry attestation remain unavailable. A conformance
receipt never grants production authorization.

The offline admission, serialization and replay functions are implemented.
Positive arithmetic and receipt tests use explicitly synthetic in-memory facts;
public negative tests invoke the real verifier against unchanged synthetic or
damaged evidence. These tests are not positive real-run qualification.

The remaining qualification prerequisite is a genuine, immutable bundle from
the user-owned producer runtime, measured for the selected frozen P1 case. Run
the callable workflow above on that bundle and retain its result to qualify
this positive public path. Runtime provenance and launcher behavior also remain
part of that operator qualification.

The accepted local transport continues to end at `AWAITING_ADMISSION`. This
increment supplies the separately callable next step; it does not automatically
connect dispatch state, receipt persistence, UI or customer workflow to that
result. Operators can use the Python functions now, but the complete customer
flow is not claimed. V3 behavior, producer schemas/profile pins and the accepted
UI and transport are unchanged.
