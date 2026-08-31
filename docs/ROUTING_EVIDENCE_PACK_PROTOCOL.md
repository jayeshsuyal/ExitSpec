# Routing Evidence Pack protocol

Status: included in the released v0.4.0 checkpoint; the tag and GitHub release
are publication records only.

The B13 Routing Evidence Pack is a read-only, content-addressed presentation
of one already admitted B11 routing campaign and its B12 purpose-bound receipt.
It is not a new verdict engine. A pack may be issued only after ExitSpec
revalidates the exact frozen B11 contract, the matching affirmative customer
confirmation, the original canonical B11 evidence bundle, the B11 reducer
result, and the B12 receipt together.

## Bounded artifact set

Each pack is one directory named `rpk_<sha256-of-canonical-b12-receipt>` and
contains exactly these entries:

```text
contract.json
confirmation.json
evidence.json
result.json
receipt.json
summary.json
decision-packet.html
artifact-hashes.json
.complete
```

The first seven artifacts are bounded by per-file byte limits and canonical
JSON parsing where applicable. The manifest records the exact SHA-256 of every
artifact. Publication writes to a private temporary directory, claims the
destination without overwrite, links the completed files atomically, fsyncs
the directory, and writes `.complete` last. A reader requires the exact entry
set, regular non-symlink files, canonical JSON, manifest hashes, deterministic
summary/HTML, and the full B11/B12 context revalidation. Any rejection exposes
no Evidence Pack link.

The canonical contract remains the authority input. Its reviewed synthetic
fixture identifier is logical provenance, not a live filesystem or provider
endpoint. The presentation summary and HTML do not include prompts, responses,
credentials, secrets, absolute filesystem paths, provider endpoints, or
producer acceptance verdicts.

## Product semantics

The existing `/app` → `Evidence Packs` surface exposes one explicitly seeded
local demo pack when the standard local demo server is started. It is labeled
`Routing qualification · synthetic demo`, `TEST ONLY`, and `NOT_PROVEN` because
required repetition 2 is absent. The candidate is the qualification subject;
the baseline is contextual and is never pooled with it. Hashes, run identities,
methodology, and artifact links are secondary `<details>` content.

`PASS`, `FAIL`, and `NOT_PROVEN` are displayable only after B11 admission and
B12 binding. Synthetic evidence is always `TEST_ONLY`. Every verdict grants
zero deployment, shipping, production-traffic, traffic-expansion, release,
spending, procurement, and contract-mutation authority. A separate named
human/product decision remains required.

This is a local/process/demo boundary. B13 does not execute a router, provision
a provider, deploy software, expand traffic, approve a release, or mutate a
contract.
