# ExitSpec v0.2.0 release checkpoint

Status: release checklist

## Release decision

ExitSpec v0.2.0 checkpoints the complete local agreement-to-evidence product
after the first Zoom connector-core and Inferdrome evidence-handoff trains. It
is a backward-compatible capability release from the immutable `v0.1.0` tag.
The historical tag must not be moved or rewritten.

The product promise remains unchanged:

```text
customer source
    -> human-reviewed measurable agreement
    -> exact customer confirmation
    -> immutable freeze
    -> supported measurement or imported evidence
    -> ExitSpec-owned typed verdict and Evidence Pack
    -> named human handoff
```

This release is still a local, synthetic-data demonstration. It does not claim
hosted identity, tenant isolation, production durability, a live mailbox, a
live Zoom connector, a funded Fireworks result, or a real vLLM/GPU benchmark.

## Added since v0.1.0

- A provider-neutral, synthetic-only Zoom meeting contract, durable local event
  inbox, exact-byte webhook-authentication seam, sealed-window source bridge,
  and restart-aware inbox-to-source orchestration core.
- An independent offline importer for pinned `inferdrome.evidence.v1` bundles
  that ignores producer verdicts and independently validates, recalculates,
  and applies the frozen ExitSpec criterion.
- A pathless `/app` handoff for one operator-configured local Inferdrome runs
  root. The browser selects only a verified run identity and digest.
- A customer-visible frozen evidence method that prevents local-probe and
  external-bundle evidence from being silently interchanged.
- A compact external-evidence receipt, typed `NOT_PROVEN` handling, immutable
  Evidence Pack, Evidence Library entry, and final human handoff.
- Responsive dashboard and handoff hardening, including the explicit
  **Select sealed evidence** action and a bounded 390-pixel closure layout.

None of these additions gives Zoom, Inferdrome, Fireworks, source text, or the
browser authority to confirm an agreement, freeze a contract, assign a verdict,
or authorize production.

## Clean checkout verification

Use Python 3.12 or 3.13, Node.js, SQLite 3.37 or newer, and a Playwright-supported
Chromium installation:

```bash
python3 -m pip install -e '.[dev,browser]'
python3 -m playwright install chromium
./scripts/v0_2_release_gate.sh
```

The gate requires:

- built-wheel distribution proof;
- the complete Python behavior and adversarial suite;
- the clean-process dynamic email lifecycle;
- the consent-bound synthetic meeting lifecycle;
- the complete sealed Inferdrome import, independent recalculation, Evidence
  Pack, and handoff lifecycle;
- bounded 1280×720 and narrow-width browser behavior; and
- JavaScript syntax checks for every product surface.

Optional Fireworks actions remain disabled and no provider account is required.

## Product demonstration

Start the local product with:

```bash
exitspec serve --open-browser
```

Follow the [three-minute product demo](DEMO_RUNBOOK.md). The deterministic
Define → Confirm → Prove loop is the primary release demonstration.

The optional Inferdrome handoff in that runbook requires one already-produced,
synthetic, non-sensitive, customer-eligible bundle beneath an explicitly
configured local runs root. It is not part of the clean provider-free demo.

## Tagging rule

Tag only the merged revision whose local release gate and GitHub checks both
pass:

```bash
git tag -a v0.2.0 -m "ExitSpec v0.2.0"
git push origin v0.2.0
```

The tag identifies a reproducible local open-source product revision. It does
not convert any experimental adapter or synthetic connector core into a
production integration.

## Next boundary after the tag

The first post-release Zoom change is fixture acquisition, not a guessed wire
adapter. One sanitized untouched synthetic Zoom RTMS fixture must resolve exact
webhook signing input, lifecycle bodies, transcript packet semantics,
participant identity, duplicate delivery, reconnect, ordering, and privacy
classification before raw Zoom packets may enter ExitSpec's provider-neutral
meeting contract.
