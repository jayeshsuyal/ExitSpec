# Zoom golden-fixture capture runbook

Status: implemented local capture kit; first real synthetic capture pending

This runbook prepares the evidence required to decide whether ExitSpec may
implement a Zoom RTMS packet mapper. It does not implement that mapper and does
not connect ExitSpec to Zoom.

The capture kit has one narrow authority:

> preserve an exact, private, synthetic-only set of opaque bytes long enough
> to inspect Zoom's observed behavior safely.

It never parses a Zoom payload, freezes a wire schema, sanitizes an artifact,
publishes a fixture, starts a meeting, authorizes a network transport, creates
an ExitSpec source, confirms or freezes a contract, starts measurement, or
assigns a verdict.

## Why this gate exists

Documentation and matching field names are not enough to freeze a provider
adapter. The first real fixture must establish the exact signing input, packet
shapes, ordering, duplicate behavior, reconnect behavior, participant identity,
transcript finality, timestamps, and privacy surface for one pinned Zoom
version.

The original bytes remain untrusted and private. SHA-256 digests detect a
change after sealing; they do not prove who produced the bytes, whether Zoom
executed truthfully, or whether the capture is complete before it is sealed.

## Hard prerequisites

Do not start until one operator has confirmed all of the following:

- a Zoom General App is configured for a non-production developer account;
- RTMS is enabled and the account has sufficient Developer Pack credits;
- the exact scopes are `meeting:read:meeting_transcript` and
  `meeting:update:participant_rtms_app_status`;
- the webhook receiver and transcript capture utility are operator-controlled;
- only transcript is requested; audio, video, screen share, and chat are off;
- exactly two test participants will use synthetic labels and synthetic words;
- both participants have accepted the same disclosure before capture;
- no customer meeting, customer identifier, secret, or confidential content is
  involved; and
- the private original can be reviewed and deleted within 24 hours.

The preflight command validates these declarations and local filesystem
guardrails. It does not query Zoom and labels provider state as not
independently verified.

## 1. Prepare the capture plan

Copy
[`zoom-golden-capture-plan-v1.example.json`](../examples/meeting/zoom-golden-capture-plan-v1.example.json)
to a temporary operator-owned file. Change only the capture ID, UTC schedule,
consent timestamps, synthetic participant labels, and disclosure digest.

The example disclosure text is exactly:

```text
This is a synthetic ExitSpec Zoom RTMS test. Transcript only; no customer data.
```

Its UTF-8 SHA-256 is:

```text
4d10d4a067ca5597e39cdc8fe12ad6a79f26bf19832c9a41c4a9a2f29b0a431d
```

Do not put a client secret, access token, webhook secret, account ID, meeting
ID, meeting URL, participant ID, or provider response in the plan. The loader
rejects URL, credential, and token-shaped strings, duplicate JSON keys,
unexpected fields, unbounded schedules, missing consent, extra media, and scope
drift.

Run:

```bash
python -m exitspec.zoom_fixture_capture preflight \
  --plan /path/to/operator-plan.json \
  --repository-root .
```

Success creates this git-ignored workspace with owner-only permissions:

```text
.zoom-fixture-private/<capture-id>/
  capture-plan.json
  preflight-receipt.json
  raw/
  review/
```

The command prints a content-free receipt. `READY_FOR_OPERATOR_CONTROLLED_SYNTHETIC_CAPTURE`
means only that the plan and local workspace passed. It is not Zoom
authorization.

## 2. Run the two-person synthetic meeting

Both participants should say only the following predetermined script:

```text
Host: This is a synthetic ExitSpec Zoom RTMS test. Transcript only; no customer data.
Guest: I consent to this synthetic transcript-only test.
Host: Criterion: p95 response latency must be at most 500 milliseconds at concurrency four.
Guest: Confirmed as a synthetic test requirement.
Host: Timeouts count as errors over all measured attempts.
Guest: Confirmed.
Host: End of synthetic test.
```

During the same synthetic session:

1. capture normal join, transcript, and leave behavior;
2. after at least one transcript packet, interrupt the transcript connection
   once and capture the close, reconnect, and next delivery;
3. capture one exact provider retry or controlled exact replay as the duplicate
   trace; and
4. stop RTMS before ending the meeting.

Do not improvise with real names, customer facts, endpoint credentials, or
production requirements.

## 3. Preserve the exact opaque artifacts

Store one non-empty original capture under every fixed filename below. Do not
pretty-print, reorder, decode, redact, normalize, concatenate after the fact,
or rename the bytes. If the capture tool emits a native binary or textual dump,
preserve that exact output as the `.bin` file.

| Opaque role | Private filename |
| --- | --- |
| App configuration snapshot | `app-configuration-snapshot.bin` |
| Endpoint-validation request | `endpoint-validation-request.bin` |
| Endpoint-validation response | `endpoint-validation-response.bin` |
| RTMS-started webhook | `rtms-started-webhook.bin` |
| RTMS-stopped webhook | `rtms-stopped-webhook.bin` |
| Signaling WebSocket handshake | `signaling-websocket-handshake.bin` |
| Transcript WebSocket handshake | `transcript-websocket-handshake.bin` |
| Participant lifecycle events | `participant-lifecycle-events.bin` |
| Transcript packets | `transcript-packets.bin` |
| Disconnect/reconnect trace | `disconnect-reconnect-trace.bin` |
| Exact duplicate-delivery trace | `duplicate-delivery-trace.bin` |
| Timestamp observations | `timestamp-observations.bin` |

All files belong directly in:

```text
.zoom-fixture-private/<capture-id>/raw/
```

The sealer rejects an absent, empty, extra, oversized, symlinked, hard-linked,
or changing artifact. It does not inspect artifact content.

## 4. Seal and independently verify custody

Run:

```bash
python -m exitspec.zoom_fixture_capture seal \
  --capture-id <capture-id> \
  --repository-root .
```

The sealer:

- hashes every exact artifact with SHA-256;
- records only role, byte count, and digest in `custody-manifest.json`;
- binds that inventory to the canonical capture plan;
- makes the plan, preflight receipt, manifest, raw directory, and raw files
  owner-readable but not writable; and
- creates `review/privacy-review-template.json`.

The manifest contains no artifact path, captured payload, meeting ID,
participant value, or secret. A second seal is an exact verification replay;
it does not rewrite completed evidence.

Verify again at any time with:

```bash
python -m exitspec.zoom_fixture_capture verify \
  --capture-id <capture-id> \
  --repository-root .
```

Verification re-reads the canonical control files and re-hashes every opaque
artifact. Mutation, replacement, extra files, changed permissions, broken
inventory, or identity drift fails closed.

## 5. Complete the privacy review

Inspect the private original in an isolated operator environment. Never copy
it into a Git worktree, issue, PR, chat, log, or public artifact.

Complete the generated review template. Every safety assertion must be true.
The decision is one of:

- `KEEP_PRIVATE`; or
- `SANITIZED_CANDIDATE_READY_FOR_REVIEW`.

The second decision means another human may inspect a separately prepared
candidate. It does not authorize publication or mapper implementation. Record
the review with:

```bash
python -m exitspec.zoom_fixture_capture review \
  --capture-id <capture-id> \
  --review /path/to/completed-privacy-review.json \
  --repository-root .
```

The review must explicitly preserve these facts:

- the original stays private;
- only synthetic content was observed;
- customer data is absent;
- secrets are absent from the candidate;
- provider identifiers are removed or documented;
- a named, versioned secret scan completed;
- transformations are documented;
- an original Zoom signature after redaction is `NOT_CLAIMED`; and
- candidate publication remains unauthorized.

The immutable public-shaped receipt hashes the reviewer label instead of
exposing it. A different second review conflicts; it cannot silently replace
the first.

## 6. Golden-fixture decision

This kit deliberately stops before creating a repository fixture. After the
privacy review, a separate human review must decide whether to:

1. keep the entire capture private and document unavailable fields; or
2. prepare a sanitized candidate plus a field-by-field capability report.

Only a later PR may add a sanitized fixture. That PR must demonstrate that the
candidate contains no secret or private identifier, document every
transformation, distinguish exact untouched-byte claims from sanitized-content
claims, and add adversarial cases for ordering, duplicates, reconnects,
timestamps, participant drift, and transcript partial/final behavior.

Passing that later fixture gate can authorize only the fake-transport packet
mapper. HTTP ingress, OAuth, RTMS start/stop, WebSocket transport, production
meetings, and all ExitSpec product decisions remain separate gates.

## Failure and recovery rules

- A failed preflight creates no authority. Correct the plan and use a new
  capture ID if conflicting state already exists.
- A partial raw inventory is not evidence. Complete it before sealing.
- A failed seal or verify means the capture cannot support implementation.
- A post-seal mutation requires a new capture ID and a complete recapture.
- An incomplete privacy review keeps the original private.
- Never bypass a failure by editing a receipt, manifest, or digest.
- Keep `.zoom-fixture-private/` ignored. Before every commit, verify that no raw
  or review artifact appears in `git status`.

The automated contract lives in
[`test_zoom_fixture_capture.py`](../tests/test_zoom_fixture_capture.py). It
covers secret-shaped inputs, duplicate keys, workspace conflicts, missing and
extra artifacts, symlinks, hardlinks, mutation, manifest tampering, incomplete
reviews, immutable replay, and zero downstream authority.
