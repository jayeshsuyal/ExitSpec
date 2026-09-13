# Operator-only local Inferdrome transport

This increment copies the existing frozen P1 handoff and tracks one explicit local CLI invocation. It imports no Inferdrome code and does not add a browser action, accept prospective evidence, issue a receipt/verdict, or authorize deployment. It supports only the three cases already defined by `inferdrome_prospective.py`.

## Commands and local contract

Run `exitspec inferdrome-handoff --help` for the command surface. Paths below are operator-owned absolute paths; replace the example paths and hashes with independently checked values.

```sh
exitspec inferdrome-handoff export \
  --handoff /operator/frozen-p1 \
  --output /operator/exported-p1

exitspec inferdrome-handoff prepare \
  --handoff /operator/exported-p1 \
  --operations-root /operator/operations \
  --runs-root /operator/runs \
  --case native-p95-under-20ms \
  --executable /operator/bin/inferdrome-launcher \
  --executable-sha256 <64-lowercase-hex-digest> \
  --producer-revision <40-lowercase-hex-commit> \
  --timeout-seconds 900
```

`export` revalidates the complete P1 artifact tree, frozen contracts and existing affirmative confirmation records, then uses the existing materializer to publish an equivalent copy. It does not create a confirmation or freeze a draft. P1's declared process-local identities remain unauthenticated; copying them does not strengthen their assurance.

`prepare` creates a private `iop_<32hex>` operation directory with a fresh `run-<32hex>` producer ID, an independently validated handoff copy, a pinned plan and a PREPARED record. It returns the operation path, `plan_sha256` and exact argv. It performs no execution. An interrupted preparation has no valid PREPARED record and cannot dispatch. Operations and runs roots must already exist, be real operator-owned directories, and disallow group/world writes; the run destination must not exist. Symlink paths, linked plan files, overlapping operation/run roots and changed executable bytes are refused.

Use the returned operation path and plan hash with each subsequent command:

```sh
exitspec inferdrome-handoff preflight --operation /operator/operations/iop_... --plan-sha256 <plan-hash>
exitspec inferdrome-handoff dispatch --operation /operator/operations/iop_... --plan-sha256 <plan-hash>
exitspec inferdrome-handoff status --operation /operator/operations/iop_... --plan-sha256 <plan-hash>
exitspec inferdrome-handoff retry --operation /operator/operations/iop_... --plan-sha256 <plan-hash>
```

Preflight is read-only and verifies local transport inputs. It does **not** invoke the executable, probe hardware, initialize a tokenizer, contact an endpoint or attest producer installation readiness. The revision field is explicitly **operator-declared provenance**. The executable digest pins that file's bytes, not its interpreter, dependency tree, imported modules or clean Git state.

Dispatch uses structured argv, never a shell or a browser-supplied command:

```text
<operator-executable> run <copied-case.yaml>
  --runs-root <operator-runs-root>
  --run-id run-<fresh-32hex>
  --expected-exitspec-contract-digest sha256:<exact-frozen-contract-hash>
```

The operator's trusted launcher owns deployment-specific tokenizer and managed-vLLM configuration. This increment exposes no arbitrary argument-string or environment passthrough. A bare producer entrypoint still needs its attached-vLLM prerequisites; a successful transport preflight does not assert those exist. Child environment contains only a PATH rooted at the executable's directory plus `/usr/bin:/bin`, LANG and disabled Python bytecode writes. It does not inherit HOME, API credentials or PYTHONPATH. The launcher/runtime and local operator directories are a trust boundary, not an executable sandbox.

## Process and operation behavior

Dispatch is synchronous and POSIX-only. An exclusive advisory lock prevents concurrent claims; a durable RUNNING record precedes process creation. A prepared operation can dispatch once. The process owns a separate session; SIGINT/SIGTERM to the CLI request cancellation of that process group. A half-second grace interval precedes the final kill signal, including when the group leader has already exited. The maximum deadline is 900 seconds. Combined stdout/stderr has a 64 KiB limit with one overflow-detection byte; events retain counts and hashes, not raw logs or prompts.

The bounded history contains PREPARED, optionally RUNNING, and one terminal state:

| State | Meaning |
|---|---|
| REFUSED | Inputs or executable changed, or the run destination already exists; no process started. |
| FAILED | Process unavailable, nonzero exit, or process-control failure. A PROCESS_CONTROL_FAILED result blocks retry because process termination is uncertain. |
| TIMED_OUT / CANCELLED / OUTPUT_LIMIT | The operation was terminated for the named reason. |
| RESULT_INVALID | Returned JSON, paths, identity or frozen-input linkage was refused. |
| INGESTION_REJECTED | The existing independent bundle verifier rejected the returned directory, including synthetic/ineligible evidence. |
| AWAITING_ADMISSION | Bundle verification and exact producer contract linkage succeeded; prospective contract applicability and acceptance are still unevaluated. |

Every event has `acceptance_verdict: null` and `shipping_authorized: false`. Dispatch exits 0 only for AWAITING_ADMISSION, and 2 for unsuccessful transport or verification; exit 0 is **not** a customer PASS. No existing receipt or acceptance model is constructed.

Retry creates a new PREPARED operation and fresh run ID while preserving the exact handoff and operator configuration. It never executes automatically or overwrites the previous attempt. A chain permits at most three attempts. AWAITING_ADMISSION, unclosed RUNNING operations, and PROCESS_CONTROL_FAILED results cannot retry. Changing configuration requires a new explicit preparation. A host crash or SIGKILL can leave RUNNING without a terminal record; there is no automatic recovery/replay. The operator must reconcile any producer process/output before separately preparing new work.

## Producer return requirements

Stdout must be one JSON object with exactly six string fields (no progress preamble, duplicate keys or extra authority fields):

- `run_id`: the requested fresh producer ID.
- `workspace_path`: exactly `<runs-root>/<run-id>`.
- `bundle_path`: exactly `<runs-root>/<run-id>/bundle`.
- `bundle_digest`: the producer's tagged, domain-separated bundle-manifest digest.
- `integrity_status`: `VALID`.
- `evidence_eligibility`: `SYNTHETIC_ONLY` or `CUSTOMER_ELIGIBLE` as declared by the producer; the declaration alone confers no eligibility.

ExitSpec passes only the expected local bundle path and returned digest to its existing independent verifier with `require_customer_eligible=True`. That verifier checks the closed completed tree, pinned schemas, raw artifact hashes and internal evidence consistency/recalculation. ExitSpec then matches run ID, eligibility and both the descriptor and resolved-spec contract digest links. Source inputs are revalidated after the subprocess. It does not scan for a substitute bundle, follow a returned URL, accept a producer verdict, or weaken a schema/profile pin.

The checked-in offline stub belongs to ExitSpec and imports neither project in its child process. It exercises the CLI transport with a SYNTHETIC_ONLY fixture; the real independent verifier rejects it. Unit tests that substitute verifier facts test control flow only and are not measurement/qualification evidence.

## Separate prospective admission increment

`inferdrome_reporting_v2.InferdromeManagedReceiptV2` requires `EXTERNAL_RECEIPT_BINDING`, an `ABSENT` producer contract link, `ACCEPTED` ingestion and a verdict. Its `ManagedEvidenceAssuranceV1` requires retrospective timing and `contract_preceded_measurement=False`. Neither top-level model can truthfully be reused unchanged for a prospective receipt. The existing V3 importer and pinned historical exception remain untouched.

Potential reuse after review: the independent bundle verifier/recalculation, target/metric/population submodels and exact identity comparison primitives. A separately reviewed prospective representation must describe the required producer links and the assurance actually available, rather than relabel retrospective evidence or assert authenticated chronology. It must bind V4 contract/confirmation, profile and native schema, model/tokenizer/endpoint, workload/request plan, traffic/concurrency, metric definition, reducer and population before independent ExitSpec evaluation. No such acceptance branch is implemented here.

Before a real qualification run, the operator/producer side must provide:

1. An independently checked clean producer commit/tree and an isolated installed runtime/launcher bound to it; the declaration and launcher hash alone do not establish this.
2. Required tokenizer/model revision, attached or managed-vLLM configuration, and a host conforming to the reviewed profile.
3. A new completed bundle measured after this exact frozen contract handoff, with both required links and the unchanged P1 methodology. Historical GPU data cannot acquire a prospective link retroactively.
4. A reviewed compatibility decision for any newly introduced schema/profile/metric/population. The inspected producer endpoint-regex change accepts the existing P1 endpoint, so hash divergence alone is not blanket incompatibility; ExitSpec's schema pins remain unchanged.
5. A separately reviewed ExitSpec prospective receipt/applicability increment before customer acceptance. Native first-choices-event TTFT is not silently equivalent to semantic first-nonempty TTFT.

No real Inferdrome, model, provider or GPU run is part of this implementation evidence. The existing browser UI and its accepted B1–B4 fix candidate are preserved separately.
