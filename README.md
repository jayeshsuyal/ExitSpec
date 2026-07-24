# ExitSpec

**Turn POC claims into proof.**

ExitSpec is an open-source acceptance-test and evidence layer for AI infrastructure proofs of concept. It turns source-linked customer success criteria into a frozen contract, runs approved measurements, and produces traceable `PASS`, `FAIL`, `BLOCKED`, or `NOT_PROVEN` verdicts.

The core rule is simple: missing or insufficient evidence never passes.

## Product boundary

ExitSpec owns the acceptance chain from a customer statement to an inspectable POC
decision:

```text
customer source
    -> redaction-first assisted facts
    -> source-linked NEEDS_REVIEW drafts
    -> named human approval or rejection
    -> version-scoped customer review
    -> recorded customer confirmation
    -> explicit immutable FROZEN contract
    -> deterministic provider-neutral measurement
    -> PASS / FAIL / BLOCKED / NOT_PROVEN
    -> POC Acceptance Evidence Pack
```

An AI provider may help extract structured facts or execute a typed request. It
cannot approve a requirement, freeze a contract, assign an acceptance verdict, or
authorize deployment. Humans own agreement; deterministic ExitSpec code owns the
verdict.

## Current implementation

ExitSpec has a local **Define → Prove → Decide** browser demo over one synthetic
support-agent POC. It source-links two candidates, requires a named human to
approve the measurable one and reject the vague one, renders a customer
review link, records a decision against the exact contract fingerprint, requires
an explicit confirmed freeze, runs a deterministic measurement, and renders a
POC Acceptance Evidence Pack.

The approved first criterion measures exact tool selection on a fixed synthetic
fixture. The deterministic demo exposes all four verdict statuses:

- `PASS`: 197/200 correct and the two-sided 95% Wilson lower bound is at least
  the approved 95% threshold;
- `FAIL`: sufficient evidence exists and the observed rate is below the approved
  threshold;
- `BLOCKED`: an attributable external blocker prevents a valid measurement; and
- `NOT_PROVEN`: evidence is incomplete, internally invalid, or statistically
  inconclusive.

Browser intake is redaction-first and provider-free: pasted synthetic notes are
redacted before parsing, retained only in redacted form, and converted into
unresolved source candidates for human review.

The repository also implements a side-effect-free assisted-authoring service. It
composes redaction-first intake, a provider-neutral structured request, local
schema and source validation, locally controlled execution policy, and
`NEEDS_REVIEW` drafts. Its integration tests execute through the real
`FireworksProvider` with a fake injected transport. The service has no built-in
live network transport and is not wired into the browser UI.

The project therefore does not claim live Fireworks execution, speech-to-text
ingestion, or production-safe handling of real customer calls.

## Why this is not another eval dashboard

Eval and benchmark tools produce measurements. Presales tools organize plans. ExitSpec owns the contractual and evidentiary chain around those tools:

1. Where did the criterion come from?
2. What exact rule was reviewed and presented for confirmation?
3. Which contract version governed the run?
4. Is the evidence sufficient and intact?
5. What deterministic verdict follows?
6. Can a customer inspect the source, evidence, calculation, and limitation?

ExitSpec will consume mature load and eval tools rather than recreate them.

## Product flow

The public demo uses one bounded workbench that advances through three stages:

```text
Define -> Prove -> Decide
```

- **Define:** connect customer statements to reviewed criteria, prepare the
  customer review, record confirmation against the exact version, and explicitly
  freeze only that confirmed contract.
- **Prove:** execute adapters and expose typed progress, failures, and evidence.
- **Decide:** show the overall decision, per-criterion verdicts, limitations, and
  a customer-readable POC Acceptance Evidence Pack.

The browser demo is deliberately local, synthetic, redaction-first, and
provider-free. The separate assisted-authoring service can suggest a criterion,
but every result remains `NEEDS_REVIEW`; a named human must review it. A `PASS`
proves evidence sufficiency, not automatic approval to ship.

## Learning contract

Implementation is only one acceptance criterion. A brick is complete when the builder can explain the mechanism, defend the design choice, trigger and diagnose a failure, review the critical code paths, and communicate the result to both an engineer and a customer.

See [docs/LEARNING_LOG.md](docs/LEARNING_LOG.md) for the active learning gate.

## Local development

ExitSpec requires Python 3.12+ and uses a `src/` package layout.

```bash
python3 -m pip install -e '.[dev]'
pytest
exitspec define --session-id define-demo
exitspec demo --scenario pass
exitspec demo --scenario insufficient
exitspec serve --open-browser
```

The CLI runner accepts only a customer-confirmed frozen contract with a valid
digest. The curated default is
`examples/support-agent/contracts/tool-selection-v1.frozen.yaml`; an internally
approved contract from `exitspec define` cannot run until a confirmation-aware
workflow freezes it.

Demo artifacts are written under `runs/` by default. `runs/` is intentionally ignored by Git; curated public evidence bundles will be copied into a separately reviewed example directory later.

`exitspec serve` binds only to `127.0.0.1` by default. It uses the built-in
synthetic transcript and deterministic fixture, makes no provider calls, and keeps
review state only in memory for the running demo.

The static evidence artifact still uses `decision-packet.html` as an internal
compatibility filename. Its public product name and rendered heading are **POC
Acceptance Evidence Pack**.

## Documentation

- [Product requirements](docs/PRD.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Contract specification](docs/CONTRACT_SPEC.md)
- [Measurement specification](docs/MEASUREMENT_SPEC.md)
- [Security and privacy](docs/SECURITY.md)
- [Redaction boundary](docs/REDACTION_SPEC.md)
- [Provider boundary](docs/PROVIDER_SPEC.md)
- [Demo plan](docs/DEMO_PLAN.md)
- [Learning log](docs/LEARNING_LOG.md)
- [Roadmap and issue order](docs/ROADMAP.md)

## Honest scope

The current browser loop is deterministic, local, synthetic, redaction-first, and
provider-free. The assisted-authoring composition is tested as a side-effect-free
service through `FireworksProvider` with fake transport, but it is not exposed in
the browser or a hosted workflow. Customer review links, typed reviewer identity,
confirmation records, and idempotency state are ephemeral and unauthenticated in
the local process; they are not durable signatures or production authorization.
ExitSpec therefore does not prove live model extraction, live hosted-endpoint
behavior, speech-to-text consent or privacy, production retention controls,
multi-user operation, or deployment reliability.

## License

Apache-2.0. See [LICENSE](LICENSE).
