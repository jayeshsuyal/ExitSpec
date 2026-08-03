# ExitSpec

**Turn POC claims into proof.**

ExitSpec is an open-source acceptance and evidence layer for AI infrastructure
proofs of concept. It turns a customer requirement into an exact, reviewable
contract; binds customer confirmation to that contract; runs an approved
measurement; and produces an inspectable `PASS`, `FAIL`, `BLOCKED`, or
`NOT_PROVEN` decision.

The governing rule is simple: missing, invalid, or insufficient evidence never
passes.

Engineering changes follow the
[ExitSpec Engineering Playbook](docs/ENGINEERING_PLAYBOOK.md): one bounded
decision, one binary exit gate, adversarial verification, and inspectable
evidence.

## What the current product does

The local browser product implements one guided **Define → Confirm → Prove**
journey. It includes a deterministic support-agent POC and a
bounded synthetic inference-performance POC:

```text
employee-selected synthetic sample email
    -> deterministic normalization and redaction
    -> source-linked NEEDS_REVIEW proposals
    -> named employee decisions
    -> exact-version customer review
    -> explicit customer acknowledgement
    -> immutable frozen contract
    -> deterministic Reference A/B/C measurement and typed verdict
    -> POC Acceptance Evidence Pack
    -> explicit human handoff or stop decision
    -> Completed POC
```

### Define

- `/app?intake=email` offers exactly two manifest-approved synthetic samples:
  **Support-agent requirements** and **Untrusted-instructions test**.
- The primary sample produces one executable 95%/200 exact-tool-selection
  proposal and one latency proposal. The latency sentence remains context
  because the guided support-agent workbench does not execute latency. A
  separate bounded inference-performance adapter is available through the CLI.
- Import deterministically normalizes and redacts the selected RFC822 fixture
  before it publishes source-linked proposals. Every proposal starts
  `NEEDS_REVIEW`; email has no approval, confirmation, freeze, measurement, or
  verdict authority.
- A human can also paste synthetic notes. ExitSpec redacts them before intake and
  creates unresolved source-linked candidates.
- An explicit **Draft with assisted authoring** action can run the same redacted
  notes through a local deterministic helper. It makes no external call, supports
  only exact tool selection, and leaves every proposal `NEEDS_REVIEW`.
- For the one supported metric—exact support-tool selection—the human can define
  or correct the title, threshold, minimum sample count, and workload label.
- The customer-facing claim is generated from those structured fields. The
  browser cannot submit a contradictory free-text claim or pretend an arbitrary
  metric is executable.

The guided browser uses two narrow loopback routes:

- `GET /api/source/fixtures` returns only the two approved sample labels and safe
  metadata.
- `POST /api/source/import` accepts only the selected fixture ID through a strict
  same-origin, exact-JSON boundary.

Imports are replay- and reset-aware. A replay preserves existing human review;
choosing another sample requires an explicit reset; and import locks after
customer review or any later agreement/evidence state.

Both frozen Wave 2 machine contracts remain unchanged; the source-web contract
therefore still carries its historical pre-implementation status fields.
Current implementation status is recorded separately at
`examples/support-agent/evidence/wave-2-implementation-evidence-v1.json`; ExitSpec
does not rewrite a frozen contract to claim completion.

The workspace derives source summary, internal `Define`/`Prove`/`Decide` phase,
next action, blockers, and latest evidence from existing domain state. `/app`
renders that state as a bounded POC dashboard with Active, Needs attention, and
Completed views. `/app/pocs/new` creates an authority-free local draft, then
routes the employee through source capture, proposal review, criterion
definition, customer agreement, proof, and evidence. Email and meeting notes
are source choices for the same POC object; they are not separate products.

### Confirm

- One canonical customer-visible projection is both rendered and fingerprinted.
  It includes the contract identity, customer, use case, target system, workload,
  criteria, owners, non-goals, and evidence-retention policy.
- Every fingerprint-bound term is visible on the customer review.
- `CONFIRM` requires explicit acknowledgement, enforced by the server rather than
  only by the checkbox UI.
- `REQUEST_CHANGES` leads to a new versioned revision; it never mutates the prior
  agreement.
- Local reviewer identity, links, decisions, and idempotency records are
  synthetic, unauthenticated, in-memory, and non-durable.

### Prove

- The employee workbench polls only while a customer decision is pending, then
  advances to the exact terminal state.
- A confirmed version must be explicitly frozen before evidence can run.
- The same frozen contract can be rerun against deterministic reference sets.
  Non-`PASS` results show the exact blocker or evidence gap and the next action.
- Recording mode has a deterministic `Restart` control.
- The graphite/orange POC Acceptance Evidence Pack places the verdict, exact
  equation, canonical contract hash, limitation, next human action, and six
  artifact links in the first viewport. Seven deeper audit sections stay
  collapsed until requested.
- A terminal Evidence Pack enables one explicit human decision: complete the
  handoff or stop the POC. A `BLOCKED` run without an Evidence Pack can only be
  stopped, and that decision binds to the durable terminal run receipt.
  Closure makes every scoped lifecycle mutation fail with `409`; shipping
  remains a separate authorization.

For the primary reference set:

```text
Required ≥ 95.00% · Observed 197/200 (98.50%)
· Wilson lower bound 95.68% · PASS
```

`PASS` establishes only the approved fixture criterion. It does not authorize
deployment, spending, procurement, production traffic, or any other external
action.

### Inference-performance proof

The bounded `exitspec performance` path proves one frozen streaming-latency
claim against an OpenAI-compatible endpoint:

```text
frozen + customer-confirmed contract
    -> durable pre-network reservation
    -> one endpoint preflight, persisted on completed runs
    -> exact warmup and measured request population
    -> sanitized terminal records
    -> deterministic p95 TTFT + error-rate verdict
    -> atomic graphite/orange Evidence Pack
    -> independent reload and recalculation
```

The current v2 example uses 100 measured attempts with configured client
concurrency set to four. It requires client-observed nearest-rank p95 TTFT below
500 ms and measured error rate below 1%. At exactly 100 attempts, zero errors
passes that rule and one error fails. It does not claim four-way request
overlap. TTFT includes network, proxy, queueing, and inference time; ExitSpec
does not label it GPU latency.

The same bounded operation is available from the guided local browser workbench
and the `exitspec performance` CLI. The browser exposes a Run action only after
the exact agreement is customer-confirmed and frozen. It never invents observed
results: the dashboard remains `NOT RUN`, `BLOCKED`, or `NOT_PROVEN` until the
operation service returns verified state, and it exposes an Evidence Pack only
after independent artifact validation succeeds.

For a provider-free local demonstration, the agreement screen offers an
explicit **Use local reference target** action. The target is a bounded,
deterministic OpenAI-compatible stream served on loopback. It exercises the
real preflight, warmup, 100-request measurement, verdict, artifact-validation,
Evidence Pack, and human-closure path; it is not an inference engine and does
not claim production performance. Reviewed claims outside the supported TTFT
plus error-rate criterion remain visible in the frozen non-goals and Evidence
Pack as `NOT_PROVEN`.

## Authority model

| Actor or component | May do | May not do |
| --- | --- | --- |
| Human POC owner | Define, correct, approve, reject, revise, and freeze the agreed rule | Turn missing evidence into a pass |
| Customer reviewer | Confirm the exact visible version or request changes | Create evidence or authorize production |
| Provider adapter | Return structured candidate facts or execution receipts | Approve, freeze, select policy, or assign verdicts |
| Measurement adapter | Return measurement facts and artifacts | Decide the verdict |
| Deterministic ExitSpec core | Validate integrity and calculate the typed verdict | Make the final business or deployment decision |

## Local development

ExitSpec requires Python 3.12 or newer. The frozen confirmation-ledger schema
also requires SQLite 3.37.0 or newer because it uses `STRICT` tables. Running
the complete engineering gate also requires Node.js for browser JavaScript
syntax checks.

The implementation order and authority boundaries for the complete local
source-to-proof product loop are frozen in the
[local E2E product contract](docs/LOCAL_E2E_CONTRACT.md).

```bash
python3 -m pip install -e '.[dev]'
./scripts/engineering_gate.sh
exitspec serve --open-browser
```

The POC dashboard is served at `http://127.0.0.1:8765/app`; its seeded
support-agent workbench is at
`http://127.0.0.1:8765/app/pocs/poc_support_agent_demo`, and its read-only
inference-latency POC is at
`http://127.0.0.1:8765/app/pocs/poc_inference_latency_demo`. For a clean
support-agent recording, choose **Guided demo** on the dashboard or open
`http://127.0.0.1:8765/app?mode=recording`. The guided entry resets only the
seeded support-agent demo before opening it. Follow the
[three-minute product demo](docs/DEMO_RUNBOOK.md).

For the guided Wave 2 email demo, open
`http://127.0.0.1:8765/app?intake=email` from a clean server and follow the
[90-second runbook](docs/WAVE2_EMAIL_DEMO_RUNBOOK.md).

The optional Wave-1 Fireworks authoring action is disabled by default. To expose
it, set `FIREWORKS_API_KEY` in the server environment and start:

```bash
exitspec serve --enable-fireworks
```

This flag permits only the code-pinned synthetic request shown in the browser
disclosure. It does not turn pasted notes into a caller-controlled provider
request.

The command-line demos use bundled synthetic defaults; they do not require paths
into this checkout:

```bash
exitspec define --session-id define-demo
exitspec demo --scenario pass
exitspec demo --scenario insufficient
```

To run the synthetic vLLM-compatible performance example after starting the
approved endpoint at `127.0.0.1:8000`:

```bash
exitspec performance \
  --contract examples/inference-performance/contracts/vllm-ttft-v2.frozen.json \
  --confirmation examples/inference-performance/contracts/vllm-ttft-v2.confirmation.json \
  --bundle-root . \
  --idempotency-key inference-latency-demo-run-v2
```

Use `--api-key-env NAME` for a remote HTTPS endpoint credential; API keys are
never accepted directly as command arguments. Credentialed execution also
requires `--credential-endpoint` to exactly match the frozen workload endpoint
and `--authorize-requests` to equal preflight + warmup + measured attempts.
Credential-free remote execution still requires the exact request
authorization. The command exits `0` on `PASS`, `2` on a completed non-pass
verdict, `3` on `BLOCKED`/pre-measurement `NOT_PROVEN`, and `4` for a safe
nonterminal replay.

`--discovery`, `--review-plan`, `--contract-seed`, `--contract`, and `--fixture`
remain available when explicit inputs are needed. Generated artifacts are written
under `runs/` by default.

The wheel includes the deterministic discovery pack, review plan, contract seed,
frozen contracts, fixtures, validated inference-performance workspace bundle,
and browser assets. Installed `define`, `demo`, and `serve` flows therefore work
outside the repository. CI runs the same `engineering_gate.sh` entry point used
locally on Python 3.12 and 3.13. The gate checks browser JavaScript syntax, the
full Python suite, the installed-wheel distribution, and patch hygiene.

## Verdicts

- `PASS`: sufficient valid evidence establishes the approved condition.
- `FAIL`: sufficient valid evidence establishes that the condition was not met.
- `BLOCKED`: an attributable external condition prevents a valid run.
- `NOT_PROVEN`: evidence is incomplete, invalid, or statistically inconclusive.

## Honest scope

The browser product is local, loopback-only, single-process, and synthetic. Its
default capture and local-assisted paths make no provider call. An experimental
Fireworks action is wired but disabled by default. When an operator explicitly
starts the server with `--enable-fireworks` and a server-owned
`FIREWORKS_API_KEY`, the browser can disclose and authorize one code-pinned
synthetic request, then ask the server to execute it within a `$0.01` request
ceiling and `$0.10` process-local reservation ceiling. Provider output still
passes local schema, redaction, and exact-source checks and remains
`NEEDS_REVIEW`.

The complete action and failure matrix use fake HTTPS connections in automated
tests. No successful real-account Fireworks smoke evidence is claimed yet.
Authorization, idempotency tombstones, provider-call history, and spend
reservations are in-memory process state; reset drops active authority but does
not erase those safety records, while process restart does. There is no working
speech-to-text or audio ingestion. A provider-neutral, synthetic-only STT
authorization contract defines consent, policy, limits, private transcript
handling, and typed pre-transport denials; it issues no transport capability and
performs no provider call. A second provider-neutral operation can bind exact
synthetic bytes to a one-use private permit and exercise one disabled-by-default
transport attempt. Its automated evidence uses fake transports only; no real STT
provider, credential, endpoint, or product audio upload exists.

ExitSpec does not yet provide hosted identity, durable confirmation storage,
multi-tenant authorization, generic metric execution, production deployment
authorization, a live email connector, mailbox OAuth or webhooks, or arbitrary
email upload. Local employees can paste bounded email text for redacted,
review-only requirement extraction; this is not mailbox ingestion. The performance adapter is
bounded to one synthetic, frozen OpenAI-compatible streaming workload; no real
vLLM/GPU endpoint result is claimed by the repository yet.

## Documentation

- [Product requirements](docs/PRD.md)
- [Accepted POC workspace contract — foundation implemented](docs/POC_WORKSPACE_SPEC.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Engineering playbook](docs/ENGINEERING_PLAYBOOK.md)
- [Demo plan](docs/DEMO_PLAN.md)
- [Three-minute product demo](docs/DEMO_RUNBOOK.md)
- [Security and privacy](docs/SECURITY.md)
- [Roadmap](docs/ROADMAP.md)
- [Contract specification](docs/CONTRACT_SPEC.md)
- [Measurement specification](docs/MEASUREMENT_SPEC.md)
- [Provider boundary](docs/PROVIDER_SPEC.md)
- [Speech-to-text boundary — contract only](docs/STT_SPEC.md)
- [Redaction boundary](docs/REDACTION_SPEC.md)
- [Wave 2 source specification](docs/SOURCE_SPEC.md)
- [Wave 2 source web contract](docs/SOURCE_WEB_CONTRACT.md)
- [Wave 2 email demo runbook](docs/WAVE2_EMAIL_DEMO_RUNBOOK.md)

## License

Apache-2.0. See [LICENSE](LICENSE).
