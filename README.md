# ExitSpec

**Turn POC claims into proof.**

ExitSpec is an open-source acceptance and evidence layer for AI infrastructure
proofs of concept. It turns a customer requirement into an exact, reviewable
contract; binds customer confirmation to that contract; runs an approved
measurement; and produces an inspectable `PASS`, `FAIL`, `BLOCKED`, or
`NOT_PROVEN` decision.

The governing rule is simple: missing, invalid, or insufficient evidence never
passes.

## What the current product does

The local browser workbench implements one complete **Define → Prove → Decide**
loop for a synthetic support-agent POC:

```text
synthetic source notes
    -> human-defined exact tool-selection rule
    -> internal review
    -> exact-version customer review
    -> explicit customer acknowledgement
    -> immutable frozen contract
    -> deterministic measurement
    -> typed verdict
    -> POC Acceptance Evidence Pack
```

### Define

- The prepared sample contains one measurable requirement and one vague request.
- A human can also paste synthetic notes. ExitSpec redacts them before intake and
  creates unresolved source-linked candidates.
- For the one supported metric—exact support-tool selection—the human can define
  or correct the title, threshold, minimum sample count, and workload label.
- The customer-facing claim is generated from those structured fields. The
  browser cannot submit a contradictory free-text claim or pretend an arbitrary
  metric is executable.

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

### Prove and decide

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

For the primary reference set:

```text
Required ≥ 95.00% · Observed 197/200 (98.50%)
· Wilson lower bound 95.68% · PASS
```

`PASS` establishes only the approved fixture criterion. It does not authorize
deployment, spending, procurement, production traffic, or any other external
action.

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
also requires SQLite 3.37.0 or newer because it uses `STRICT` tables.

```bash
python3 -m pip install -e '.[dev]'
python3 -m pytest
exitspec serve --open-browser
```

The workbench is served at `http://127.0.0.1:8765/app`. For a clean recording,
open `http://127.0.0.1:8765/app?mode=recording` and click **Restart** before the
take.

The command-line demos use bundled synthetic defaults; they do not require paths
into this checkout:

```bash
exitspec define --session-id define-demo
exitspec demo --scenario pass
exitspec demo --scenario insufficient
```

`--discovery`, `--review-plan`, `--contract-seed`, `--contract`, and `--fixture`
remain available when explicit inputs are needed. Generated artifacts are written
under `runs/` by default.

The wheel includes the deterministic discovery pack, review plan, contract seed,
frozen contract, fixture, and browser assets. Installed `define`, `demo`, and
`serve` flows therefore work outside the repository. CI tests Python 3.12 and
3.13, browser JavaScript syntax, the full Python suite, and an installed-wheel
distribution gate.

## Verdicts

- `PASS`: sufficient valid evidence establishes the approved condition.
- `FAIL`: sufficient valid evidence establishes that the condition was not met.
- `BLOCKED`: an attributable external condition prevents a valid run.
- `NOT_PROVEN`: evidence is incomplete, invalid, or statistically inconclusive.

## Honest scope

The browser product is local, loopback-only, single-process, synthetic, and
provider-free. The Fireworks adapter and assisted-authoring composition are tested
through fake injected transports; no live Fireworks call exists and that path is
not wired into the UI. There is no speech-to-text ingestion.

ExitSpec does not yet provide hosted identity, durable confirmation storage,
multi-tenant authorization, a live endpoint measurement adapter, generic metric
execution, or production deployment authorization.

## Documentation

- [Product requirements](docs/PRD.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Demo plan](docs/DEMO_PLAN.md)
- [Security and privacy](docs/SECURITY.md)
- [Roadmap](docs/ROADMAP.md)
- [Contract specification](docs/CONTRACT_SPEC.md)
- [Measurement specification](docs/MEASUREMENT_SPEC.md)
- [Provider boundary](docs/PROVIDER_SPEC.md)
- [Redaction boundary](docs/REDACTION_SPEC.md)

## License

Apache-2.0. See [LICENSE](LICENSE).
