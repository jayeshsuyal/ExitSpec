# ExitSpec

[![Tests](https://github.com/jayeshsuyal/ExitSpec/actions/workflows/tests.yml/badge.svg?branch=main)](https://github.com/jayeshsuyal/ExitSpec/actions/workflows/tests.yml)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-2f6f9f)](LICENSE)

**Turn a customer POC claim into a decision you can defend.**

ExitSpec is a local, source-neutral acceptance workbench for solutions
engineers, platform teams, and customer-facing AI infrastructure teams. It
turns a request into an exact agreement, proves only that agreement, and
packages the result as inspectable evidence. Missing, invalid, or insufficient
evidence never passes.

## Who it is for

Use ExitSpec when someone asks, “Can this system meet our requirement?” and you
need a shared answer that survives customer, engineering, or leadership review.
It connects the request, measurable rule, confirmation, evidence, verdict, and
named human handoff without presenting a local demo as production authorization.

## See the product

![ExitSpec seeded workbench showing the Define → Confirm → Prove flow](docs/assets/exitspec-seeded-workbench.jpg)

*Current v0.4 review-candidate product surface, captured from a clean local
seeded demo. The sample data is synthetic.*

> **Release status:** v0.4.0 is a review candidate. No v0.4.0 tag or GitHub
> release has been created. The v0.3 Request-to-Proof contract remains the
> historical compatibility reference; the current package and release gate are
> v0.4.0.

## 60-second quickstart

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -e '.[dev,browser]'
.venv/bin/exitspec serve
```

Open [the local dashboard](http://127.0.0.1:8765/app) and choose **Guided
demo**, or open the clean seeded path directly with the [75-second guided
demo](http://127.0.0.1:8765/app?mode=recording). It resets only the seeded
support-agent POC and walks through the product in about 75 seconds. Stop the
server with `Ctrl-C`.

The v0.3 source-neutral browser product starts at `/app`; the current package
and release gate are v0.4.0.

## Define → Confirm → Prove

1. **Define:** capture a bounded request, review source-linked proposals, and
   choose the measurable rule for the POC.
2. **Confirm:** show the exact customer-facing version, record explicit
   acknowledgement, and freeze the contract before measurement.
3. **Prove:** run the approved evidence method, recalculate the typed verdict,
   inspect the Evidence Pack, and record a human handoff or stop decision.

The detailed source-neutral journey is **Capture → Review → Plan → Confirm → Prove → Decide**.
Its compact product flow is:

```text
bounded source text
  → redacted receipt and fresh process-local POC
  → NEEDS_REVIEW proposal
  → human planning and customer acknowledgement
  → immutable frozen contract
  → approved measurement and typed verdict
  → inspectable Evidence Pack
  → explicit human handoff or stop
```

## What the guided demo proves

The seeded support-agent scenario freezes an exact-tool-selection criterion,
then evaluates 200 deterministic synthetic cases: `197/200 (98.50%)`, with a
Wilson lower bound of `95.68%`, produces `PASS`. The result is scoped to that
frozen criterion and fixture; it is not a claim about a production system.

The demo is provider-free and deterministic. The workbench shows the agreement,
run state, equation, contract hash, limitation, next action, and artifact links
in the Evidence Pack. The [three-minute demo runbook](docs/DEMO_RUNBOOK.md)
explains the narrated path and optional extensions.

## Compact architecture

ExitSpec keeps authority in the server-owned lifecycle: capture creates
source-linked candidates; a server-owned A4 registry selects the approved
adapter, profile, evidence method, population, and provenance; a human reviews
and freezes the exact contract; A6 evidence services calculate the verdict; and
the Evidence Pack binds the result to the contract and human handoff.

Email, meeting text, notes/document text, the seeded dashboard, and the bounded
performance path converge on the same typed POC object. Legacy routes and
optional performance or archive paths are compatibility adapters; they cannot
choose lifecycle state, evidence method, verdict, or production action.

Inferdrome is a concise differentiator: its offline `inferdrome.evidence.v1`
importer verifies structure and exact-byte hashes, rejects synthetic customer
evidence, recalculates summaries, and binds an admitted bundle to a frozen
context. See the [Inferdrome import boundary](docs/INFERDROME_IMPORT.md).
The v0.4 routing Evidence Pack and its B9–B13 contracts are maintained in the
[v0.4 checkpoint](docs/RELEASE_V0_4.md), not duplicated here.

## Authority boundary

ExitSpec can record a customer-confirmed contract, calculate a scoped evidence
verdict, and package inputs and limitations for review. A `PASS` is only a
verdict for the frozen criterion. It does **not** authorize deployment,
spending, procurement, production traffic, shipping, or any other external
action. A named human remains responsible for the final handoff or stop.

| Actor or component | May do | May not do |
| --- | --- | --- |
| Human POC owner | Define, revise, approve, reject, and freeze the agreed rule | Turn missing evidence into a pass |
| Customer reviewer | Confirm the exact visible version or request changes | Create evidence or authorize production |
| Provider or measurement adapter | Return bounded facts, receipts, and artifacts | Approve, freeze, select policy, or assign verdicts |
| ExitSpec verifier | Validate integrity and calculate the typed verdict | Make the final business or deployment decision |

## Honest current limitations

- The browser product is local, loopback-only, single-process, and restricted to
  synthetic demo data. Reviewer identity, confirmation storage, and lifecycle
  records are process-local and non-durable.
- Default capture and assisted-authoring paths make no provider call. The
  Fireworks authoring and STT flags are disabled by default, server-keyed, and
  experimental; no successful funded real-account STT smoke result is claimed.
- The performance adapter is limited to one frozen OpenAI-compatible streaming
  workload and client-observed TTFT plus error rate. No real vLLM/GPU result is
  claimed by this repository.
- There is no hosted identity, multi-tenant authorization, live mailbox or
  meeting connector, arbitrary upload, generic metric execution, durable
  confirmation service, or production deployment authorization.

See the [security boundary](docs/SECURITY.md), [redaction boundary](docs/REDACTION_SPEC.md),
[provider boundary](docs/PROVIDER_SPEC.md), and [measurement specification](docs/MEASUREMENT_SPEC.md)
for the detailed limits. Evidence gaps fail closed as `FAIL`, `BLOCKED`, or
`NOT_PROVEN`; only sufficient valid evidence can produce `PASS`.

## For contributors

ExitSpec requires Python 3.12+, SQLite 3.37+, and Node.js for JavaScript syntax
checks. Install development and browser dependencies, then run the same gates
used by CI:

```bash
python3.12 -m pip install -e '.[dev,browser]'
python3.12 -m playwright install chromium
./scripts/engineering_gate.sh
./scripts/v0_3_release_gate.sh
./scripts/v0_4_release_gate.sh
```

The v0.4 wrapper requires the exact Chromium collections and the complete
engineering gate to finish with zero skips, failures, or errors. The canonical
source-neutral server command is:

```bash
exitspec serve --source-neutral --open-browser
```

The implementation order and authority boundaries are frozen in the [local E2E
product contract](docs/LOCAL_E2E_CONTRACT.md). The [Engineering Playbook](docs/ENGINEERING_PLAYBOOK.md)
describes the one-bounded-decision release discipline.

## Curated documentation

- [Architecture](docs/ARCHITECTURE.md)
- [Product requirements](docs/PRD.md)
- [Golden Loop Contract](docs/GOLDEN_LOOP_CONTRACT.md)
- [Contract specification](docs/CONTRACT_SPEC.md)
- [Source and intake specification](docs/SOURCE_SPEC.md)
- [Security and privacy](docs/SECURITY.md)
- [v0.3 release checkpoint](docs/RELEASE_V0_3.md)
- [v0.4 release checkpoint](docs/RELEASE_V0_4.md)
- [Contributing](CONTRIBUTING.md)
- [Security reporting](SECURITY.md)

Detailed meeting, Zoom, Fireworks STT, routing, and historical release
material remains in the linked authoritative docs and runbooks.

## License

Apache-2.0. See [LICENSE](LICENSE).
