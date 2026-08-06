# ExitSpec v0.1 release gate

Status: release candidate

## Release decision

ExitSpec v0.1 is a reproducible local open-source demonstration of one complete
POC agreement-to-evidence loop. It is demo-ready when the exact release command
below passes on the revision that will be tagged.

This release is not production-ready. It does not claim hosted identity,
tenant isolation, durable workspace history, live mailbox or meeting
connectors, a funded Fireworks result, or a real vLLM/GPU benchmark.

## Clean checkout verification

Use Python 3.12 or 3.13, Node.js, SQLite 3.37 or newer, and a local Chromium
installation supported by Playwright:

```bash
python3 -m pip install -e '.[dev,browser]'
python3 -m playwright install chromium
./scripts/v0_1_release_gate.sh
```

`v0_1_release_gate.sh` enables the opt-in Chromium lifecycle tests and then runs
the same engineering gate used by CI. One command therefore checks:

- patch hygiene and conflict markers;
- a built wheel running outside the checkout;
- the complete Python behavior suite;
- a clean-process dynamic email POC through verified `PASS` and handoff;
- the consent-bound synthetic meeting recording path;
- JavaScript syntax for every product surface; and
- deterministic provider-free operation.

The command fails if Playwright is absent, Chromium cannot launch, a referenced
artifact is corrupt, or any underlying engineering gate fails.

## Product demonstration

After the release gate passes:

```bash
exitspec serve --open-browser
```

Follow the [three-minute product demo](DEMO_RUNBOOK.md). Keep both Fireworks
flags disabled: provider execution is optional and is not part of the v0.1
release proof.

The exact workspace acceptance-to-test map is recorded in
[`poc-workspace-implementation-evidence-v1.json`](../examples/product/poc-workspace-implementation-evidence-v1.json).
That record points to executable positive and adversarial tests for every frozen
`WS-01` through `WS-12` gate. It does not rewrite the historical frozen
acceptance contract.

## Tagging rule

Tag only the merged revision whose CI and local release gate both pass:

```bash
git tag -a v0.1.0 -m "ExitSpec v0.1.0"
git push origin v0.1.0
```

The tag identifies the reproducible source revision. It does not convert the
local demo into a hosted or production release.

## Release limitations

- Inputs and fixtures are synthetic.
- Workspace state and browser customer decisions are local and process-scoped.
- Optional Fireworks authoring and STT are disabled by default and have no
  successful funded live-account smoke receipt.
- The local reference target proves the ExitSpec measurement loop, not real
  inference performance.
- `PASS` applies only to the exact frozen criterion and never authorizes
  deployment, spend, procurement, or production traffic.
