# ExitSpec

**Turn POC claims into proof.**

ExitSpec is an open-source acceptance-test and evidence layer for AI infrastructure proofs of concept. It turns source-linked customer success criteria into a frozen contract, runs approved measurements, and produces traceable `PASS`, `FAIL`, `BLOCKED`, or `NOT_PROVEN` verdicts.

The core rule is simple: missing or insufficient evidence never passes.

## Project status

ExitSpec now has a **local Define → Prove → Decide demo loop** over one synthetic
support-agent POC:

```text
synthetic discovery transcript
    -> source-linked draft criterion
    -> explicit human approval or rejection
    -> approved contract
    -> frozen canonical contract
    -> deterministic measurement
    -> versioned raw evidence
    -> statistical calculation
    -> criterion and overall verdicts
    -> customer-readable Proof Pack
```

Brick 2 deliberately rejects one vague customer request rather than quietly turning it into a test. The approved first criterion measures exact tool selection on a fixed synthetic fixture. The sample supports four evidence outcomes:

- fewer than the approved 200 samples: `NOT_PROVEN`;
- 197/200 correct with a two-sided 95% Wilson lower bound above 95%: `PASS`;
- a point estimate above 95% whose confidence bound is still inconclusive: `NOT_PROVEN`;
- a point estimate below 95% with sufficient samples: `FAIL`.

## Why this is not another eval dashboard

Eval and benchmark tools produce measurements. Presales tools organize plans. ExitSpec owns the contractual and evidentiary chain around those tools:

1. Where did the criterion come from?
2. What exact rule did both parties approve?
3. Which contract version governed the run?
4. Is the evidence sufficient and intact?
5. What deterministic verdict follows?
6. Can a customer inspect the source, evidence, calculation, and limitation?

ExitSpec will consume mature load and eval tools rather than recreate them.

## Product flow

The public demo uses three screens:

```text
Define -> Prove -> Decide
```

- **Define:** connect customer statements to reviewed criteria and freeze the contract.
- **Prove:** execute adapters and expose typed progress, failures, and evidence.
- **Decide:** show the overall decision, per-criterion verdicts, limitations, and a customer-readable Proof Pack.

The browser demo is deliberately local and synthetic. It makes the authority
boundary visible: a model or a prepared draft can suggest a criterion, but a named
human must review it; a `PASS` proves evidence sufficiency, not automatic approval
to ship.

## Learning contract

Implementation is only one acceptance criterion. A brick is complete when the builder can explain the mechanism, defend the design choice, trigger and diagnose a failure, review the critical code paths, and communicate the result to both an engineer and a customer.

See [docs/LEARNING_LOG.md](docs/LEARNING_LOG.md) for the active learning gate.

## Local development

The first implementation targets Python 3.9+ and uses a `src/` package layout.

```bash
python3 -m pip install -e '.[dev]'
pytest
exitspec define --session-id define-demo
exitspec demo --scenario pass --contract runs/define-demo/approved-contract.json
exitspec demo --scenario insufficient
exitspec serve --open-browser
```

Demo artifacts are written under `runs/` by default. `runs/` is intentionally ignored by Git; curated public evidence bundles will be copied into a separately reviewed example directory later.

`exitspec serve` binds only to `127.0.0.1` by default. It uses the built-in
synthetic transcript and deterministic fixture, makes no provider calls, and keeps
review state only in memory for the running demo.

## Documentation

- [Product requirements](docs/PRD.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Contract specification](docs/CONTRACT_SPEC.md)
- [Measurement specification](docs/MEASUREMENT_SPEC.md)
- [Security and privacy](docs/SECURITY.md)
- [Demo plan](docs/DEMO_PLAN.md)
- [Learning log](docs/LEARNING_LOG.md)
- [Roadmap and issue order](docs/ROADMAP.md)

## Honest scope

The current loop is deterministic and local. It validates a synthetic transcript and
explicit human decisions; it does not yet prove live LLM extraction quality,
hosted-endpoint behavior, production privacy guarantees, multi-user operation, or
deployment reliability. Those claims remain explicitly unproven until the
corresponding evidence exists.

## License

Apache-2.0. See [LICENSE](LICENSE).
