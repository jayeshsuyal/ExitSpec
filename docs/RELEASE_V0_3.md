# ExitSpec v0.3.0 release checkpoint

Status: review candidate; no tag or GitHub release has been created.

## Canonical local path

Start the provider-free source-neutral runtime and open `/app`:

```bash
exitspec serve --source-neutral --open-browser
```

The canonical v0.3 path is:

**Capture → Review → Plan → Confirm → Prove → Decide**

```text
source choice + generated POC ID
    -> Capture source receipt
    -> Review schema-bound proposals
    -> Plan with server-owned evidence authority
    -> Confirm and freeze the exact customer agreement
    -> Prove with independently reduced evidence
    -> Decide through human handoff or stop
```

Email text, notes/document text, and meeting transcript or recording-derived
text all enter the same path. Source inputs remain untrusted and review-only.
The browser never selects an adapter, adapter version, evidence profile,
measurement population, provenance, workload, or verdict. The server expands
the bounded human planning decision from the existing A4 registry.

After exact A5 freeze, the existing A6 service selects `EXECUTABLE` or
`EVIDENCE_IMPORT` from the frozen binding. Rejected ingestion remains distinct
from admitted `NOT_PROVEN`; unsupported, missing, stale, or tampered evidence
cannot pass. The immutable customer Evidence Pack shows current versus
historical attempts, limitations, next action, and non-authorization before a
named human records handoff or stop.

## Compatibility adapters

The seeded support-agent dashboard, performance workbench, legacy routes, and
optional exact A10 archive remain compatibility adapters. They do not control
fresh `/app` state and are not evidence for the canonical v0.3 path. Existing
frozen IDs, hashes, confirmation semantics, evidence reduction, and pack
integrity contracts are unchanged.

## Clean checkout verification

Use Python 3.12 or 3.13, Node.js, and Playwright Chromium:

```bash
python3 -m pip install -e '.[dev,browser]'
python3 -m playwright install chromium
./scripts/v0_3_release_gate.sh
```

The release gate executes four exact mandatory Chromium cases (three complete
fresh source classes plus narrow responsive/focus verification), parses JUnit,
and fails unless the expected count is collected with zero skips, failures, or
errors. It then runs the complete engineering, distribution, syntax, lint,
audit, and Python gates.

## Limitations

This remains a loopback-only, process-local, non-durable demonstration using a
server-owned deterministic evidence fixture. It has no hosted identity, live
mailbox, live Zoom connection, provider execution, production durability, or
deployment/spending/procurement/shipping authority. The optional exact A10
archive is not fabricated or required by the canonical deterministic path.

## Rollback

Revert the A7 convergence commit and use the historical v0.2 compatibility
entry points and `scripts/v0_2_release_gate.sh`. A rollback removes the v0.3
composition layer; it must not rewrite frozen contracts, evidence packs, tags,
or domain identifiers.

Tagging and GitHub release creation are deliberately outside this checkpoint.
