# ExitSpec v0.4.0 release checkpoint

Status: review candidate until the parent task tags it; no tag or GitHub release has been created.

## B13 closure

v0.4 adds a bounded Routing Evidence Pack around the frozen B9 routing
qualification vocabulary, B10 confidence-bearing SLO, B11 independent campaign
reducer, and B12 purpose-bound receipt. The existing Evidence Packs library can
open one clearly labeled synthetic routing qualification demo from the normal
local `/app` journey. The pack is reverified from disk before its library link
is released.

The frozen fixture honestly presents `NOT_PROVEN`: required repetition 2 is
missing. It is not relabeled as failure, changes requested, or approval. The
candidate qualification subject and contextual baseline remain distinct, and
the missing repetition is visible above the fold.

## Verification

The mandatory v0.4 gate preserves the complete v0.3 four-case no-skip Chromium
gate, adds four exact B13 Chromium cases, 16 adversarial pack cases, and four
direct descriptor-reader cases, then runs the full engineering and distribution
checks. It fails on any skip, error, or failure:

```bash
python3 -m pip install -e '.[dev,browser]'
python3 -m playwright install chromium
./scripts/v0_4_release_gate.sh
```

The machine-readable closure record is
`examples/product/routing-evidence-pack-v0_4-acceptance-v1.json`.

## Honest limits

- local, process-local, and demo-only; no hosted identity or durable storage;
- the routing fixture is synthetic and permanently `TEST ONLY`;
- no router execution or provider provisioning;
- no deployment, shipping, production-traffic, traffic-expansion, release,
  spending, procurement, or contract-mutation authority;
- a separate named human/product decision remains required;
- no actual tag or GitHub release is part of this checkpoint.

## Rollback

Revert the B13 closure commit and use the v0.3 release checkpoint and gate.
Rollback must not rewrite the frozen B9–B12 contracts, receipts, evidence, or
identities.
