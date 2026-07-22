# ExitSpec

**Turn POC claims into proof.**

ExitSpec is an open-source acceptance-test and evidence layer for AI infrastructure proofs of concept. It turns source-linked customer success criteria into a frozen contract, runs approved measurements, and produces traceable `PASS`, `FAIL`, `BLOCKED`, or `NOT_PROVEN` verdicts.

The core rule is simple: missing or insufficient evidence never passes.

## Project status

ExitSpec has completed **Brick 2: Define**. It now proves one local source-to-decision path:

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
    -> static decision packet
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

## Planned product flow

The public demo uses three screens:

```text
Define -> Prove -> Decide
```

- **Define:** connect customer statements to reviewed criteria and freeze the contract.
- **Prove:** execute adapters and expose typed progress, failures, and evidence.
- **Decide:** show the overall decision, per-criterion verdicts, limitations, and downloadable evidence pack.

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
```

Demo artifacts are written under `runs/` by default. `runs/` is intentionally ignored by Git; curated public evidence bundles will be copied into a separately reviewed example directory later.

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

Bricks 1 and 2 are deterministic and local. The authoring workflow validates a synthetic transcript and explicit human decisions; it does not yet prove LLM extraction quality, interactive authorization, hosted-endpoint behavior, production privacy guarantees, multi-user operation, or deployment reliability. Those claims remain explicitly unproven until the corresponding evidence exists.

## License

Apache-2.0. See [LICENSE](LICENSE).
