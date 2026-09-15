# One-command dispatch and prospective receipt completion

After an operator has reviewed, confirmed and frozen the exact P1 handoff,
configured a trusted launcher and private roots, and prepared an operation with
the existing [local transport commands](INFERDROME_LOCAL_DISPATCH.md), run:

```sh
exitspec inferdrome-handoff run \
  --operation /operator/operations/iop_... \
  --plan-sha256 "$EXIT_SPEC_PLAN_SHA256"
```

Replace the operation path with the path returned by `prepare`, and set
`EXIT_SPEC_PLAN_SHA256` to that operation's exact returned `plan_sha256`. Use the
same pair when repeating the command. The command accepts no separate bundle,
case, receipt output path, producer arguments, or eligibility override.

For a PREPARED operation, `run` requests the existing synchronous dispatch once.
If it returns AWAITING_ADMISSION, the command independently admits the bundle,
serializes the prospective receipt, re-verifies those canonical bytes against
the original pinned handoff/bundle, and saves the receipt and terminal result.
An operation already at AWAITING_ADMISSION goes directly to receipt completion.
A repeated completed command re-verifies the retained receipt and returns its
same identity. It does not start the producer again.

The original `dispatch`, `status`, `preflight` and explicit `retry` commands keep
their existing meanings. In particular, `dispatch` alone never issues a receipt,
and its AWAITING_ADMISSION state is not a customer PASS. The bridge does not call
`retry`. For a failed, cancelled, timed-out or unresolved RUNNING operation, it
returns a no-receipt outcome. Producer process reconciliation or an explicit
new retry preparation remains an operator decision.

## Output and exit codes

The command writes one JSON result to stdout. `transport_status` describes
transport; `bridge_status` describes admission/storage. `acceptance_verdict` is
the separately evaluated numerical result, never shipping permission.

| Exit | Bridge outcome | Meaning |
| --- | --- | --- |
| 0 | RECEIPT_SAVED, verdict PASS | Exact receipt was independently verified and durably saved; numerical agreement passed. |
| 3 | RECEIPT_SAVED, verdict FAIL | Receipt was verified and saved; valid evidence failed the numerical agreement. |
| 4 | RECEIPT_SAVED, verdict NOT_PROVEN | Receipt was verified and saved; the evidence does not establish the requested TTFT criterion. |
| 2 | NO_RECEIPT | Transport failed, was cancelled, rejected its evidence, or remains unclosed; no receipt issued. |
| 2 | ADMISSION_REJECTED | Returned evidence/context failed admission; a bound refusal record was saved, with no acceptance receipt or verdict. |
| 2 | REFUSED | Input, storage, replay, concurrency, cancellation or durability check failed; saved receipt success is not claimed. |

A RECEIPT_SAVED result includes `receipt_saved: true`, the fixed `receipt_path`,
`receipt_id`, `receipt_sha256`, byte count and verdict. A refusal has
`receipt_saved: false` and no acceptance verdict. `producer_dispatch_requested`
describes whether this invocation requested dispatch, not an execution
attestation. Every outcome has `shipping_authorized: false`.

SIGINT/SIGTERM during dispatch use its existing owned-process cancellation.
During bounded receipt work, cancellation is checked at publication boundaries.
Repeat the same operation/hash to reconcile interrupted receipt work. Cancellation
does not delete a file that has already been published.

## Stored files and recovery

The bridge uses three fixed files in the existing private operation directory:

| File | Role |
| --- | --- |
| `receipt-intent.json` | Immutable original plan/event/run/bundle/manifest/case/contract bindings and declared receive time. |
| `prospective-receipt.json` | Exact canonical bytes of the independently re-verifiable prospective receipt. |
| `receipt-result.json` | Immutable bound terminal receipt metadata or admission refusal. |

The plan hash is checked against the actual plan bytes. Run, bundle path/digest,
manifest and case come from the validated operation and its returned-result
record; the contract link comes from the exact frozen handoff. The transport
record bytes are also pinned into the intent. The receipt is checked against
these original inputs before publication, after publication, and on every
successful replay. Changed operation records are refused.

The bridge shares the operation's exclusive dispatch lock. A concurrent caller
can receive DISPATCH_BUSY and exit 2; it can repeat the same command after the
owner finishes. Concurrent callers cannot overwrite records or create additional
producer attempts. The operation root must be a real operator-owned directory
without group/world write permission. Storage uses anchored directory descriptors,
bounded nonblocking no-follow reads, private regular single-link files and
atomic hard-link publication without replacement. Each file is at most 64 KiB;
the operation root is limited to 32 directory entries. Existing bundle/handoff
bounds are retained.

File synchronization precedes publication. The receipt's file/directory durability
barriers precede the SAVED terminal record, and all retained files and the parent
directory are synchronized and checked before success is returned. A visible
file following a failed synchronization is not reported as durably saved.

An interrupted intent can resume with its original audit time. A complete receipt
without a result record is fully re-verified and reconciled. A completed result
with a missing receipt is refused; the command never silently recreates it and
claims it had been saved. Corrupt, mismatched, symlinked, multiply linked or partial
artifacts are refused without overwrite or automatic deletion. A crash between
the no-clobber hard link and temporary-link removal can leave a two-link file;
this is a safe refusal requiring operator inspection, not automatic recovery.
Unpublished temporary files have no authority. Repeated crashes can reach the
directory-entry bound and require operator inspection.

If `receipt_saved` is false, inspect the reported reason and retained files;
do not infer success from file existence. Repeating the same command can resolve
a completed publication or transient save failure without rerunning the producer.
Never alter the original pins, bundle or receipt to make a refusal pass.

## Admission scope and remaining qualification

The bridge reuses the [accepted prospective admission contract](INFERDROME_PROSPECTIVE_ADMISSION.md):
exact affirmative confirmation, both producer links, source YAML digest, profile,
target, methodology and all 100 ordered prompts, with independent measurement
recalculation and strict thresholds. Ineligible or corrupt evidence never produces
an acceptance receipt. Declared chronology remains unauthenticated; temporal
assurance is UNAVAILABLE, contract-preceded-measurement proof is null, and a PASS
does not authorize deployment.

This increment supplies a CLI operator bridge and durable local receipt retention.
It does not add a browser workflow, new service or queue, producer integration
protocol, or changes to the reviewed launcher trust restrictions. The operator
still owns real runtime readiness, genuine measurement and provenance.

Offline positive persistence tests use explicitly unit-only in-memory verifier
facts and temporary receipt files. They are not positive customer qualification.
Public subprocess rehearsals use ExitSpec-owned stubs; unchanged synthetic evidence
is rejected by the actual verifier. A genuine positive end-to-end result still
requires the user's new immutable Inferdrome bundle measured for the exact frozen
case. No real producer/model/provider/GPU execution is part of this increment's
offline validation.
