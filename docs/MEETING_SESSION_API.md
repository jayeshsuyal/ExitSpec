# Guided meeting-session API

## Status

ExitSpec implements a local, provider-neutral meeting-session application
boundary for the synthetic demo. It connects the existing meeting connector,
durable inbox, sealed-window orchestration, redaction, and `MEETING` source
pipeline without freezing any Zoom packet shape. The existing source-intake
workbench exposes this as the default `Meeting session` submode when a draft
starts from a meeting.

The current injected adapter is `exitspec.synthetic`. It makes no network call,
does not connect to Zoom, uses exactly two fixed synthetic participants, and
produces a fixed synthetic transcript. A successful run proves the ExitSpec
application workflow around a meeting source. It does **not** prove Zoom OAuth,
webhook delivery, RTMS packet mapping, or a live provider connection.

## Product flow

One server-owned state machine exposes one next action at a time:

| State | Required action | Result |
| --- | --- | --- |
| `SETUP` | `RECORD_CONSENT` | Record the exact current disclosure for both synthetic participants |
| `READY` | `START_CAPTURE` | Start the injected server-owned adapter and append its authenticated start event |
| `LIVE` | `DRAFT_REQUIREMENTS` | Append the remaining events and finalize one redacted source |
| `DRAFT_READY` | `REVIEW_REQUIREMENTS` | Continue at `/app/pocs/{poc_id}/review` |

The final state contains only content-free source facts. The source and every
derived proposal remain `NEEDS_REVIEW`. No meeting session may confirm a
proposal, freeze a contract, start measurement, create evidence, or assign a
verdict.

## Guided workbench

The meeting source remains one part of the existing `/app` flow:

```text
New POC -> Meeting -> Meeting session
        -> consent -> start -> draft -> human review
```

`Meeting session`, `Paste transcript`, and `Record synthetic demo` are three
bounded submodes of one `MEETING` source; they are not parallel products. The
session panel shows four finite steps, one current state, one primary footer
action, and an explicit `Not connected` badge before consent. It does not add a
second dashboard, an unbounded activity feed, or another approval surface.

Every browser response is checked against the complete expected disclosure or
session shape before it can update the interface. Changed, missing, extra, or
contradictory response fields fail closed. A refresh recovers only the safe
content-free current session; browser storage is not used. Once a session
exists, source and meeting-mode selection lock until its review-only handoff is
complete.

## HTTP surface

All routes are scoped to one active draft POC whose selected first source is
`MEETING`:

| Method | Route | Purpose |
| --- | --- | --- |
| `GET` | `/api/pocs/{poc_id}/meeting-sessions/disclosure` | Read the exact disclosure and adapter capability projection |
| `POST` | `/api/pocs/{poc_id}/meeting-sessions` | Create the POC's one process-local meeting session |
| `GET` | `/api/pocs/{poc_id}/meeting-sessions/current` | Recover the current safe session projection |
| `GET` | `/api/pocs/{poc_id}/meeting-sessions/{session_id}` | Recover one exact safe session projection |
| `POST` | `/api/pocs/{poc_id}/meeting-sessions/{session_id}/consent` | Record complete acknowledgement of the current synthetic disclosure |
| `POST` | `/api/pocs/{poc_id}/meeting-sessions/{session_id}/start` | Start the server-selected adapter |
| `POST` | `/api/pocs/{poc_id}/meeting-sessions/{session_id}/draft` | Finalize the captured window into the existing review queue |

Every mutation carries `idempotency_key` in its exact JSON body. Exact replay
returns the original result with `idempotent_replay=true`; changed or
cross-action reuse returns a typed conflict. Browser-supplied
`Idempotency-Key` headers are rejected.

Writes require `application/json`, an exact same-origin `Origin`, an exact
parameter-free route, a bounded strict JSON object, the complete allowlisted
field set, and an open POC lifecycle. Unknown fields fail closed. In
particular, the browser cannot submit:

- meeting, stream, participant, or provider request identities;
- transport verification or authorization claims;
- transcript packets, transcript text, or raw audio;
- adapter selection or a provider-connected flag; or
- proposal, contract, execution, evidence, or verdict authority.

## Disclosure and consent

The synthetic disclosure is version-bound by
`meeting_synthetic_disclosure_v1` and uses this exact notice:

```text
This is a synthetic ExitSpec Zoom RTMS test. Transcript only; no customer data.
```

Capture cannot start unless both fixed synthetic participants have accepted
that exact disclosure and the operator has acknowledged that this is a
synthetic demo. A changed disclosure ID, partial consent, wrong POC source,
archived draft, duplicate active session, or out-of-order action releases no
source.

The word `Zoom` in the notice describes the planned synthetic test scenario;
it is not a connection claim. Public projections therefore state
`provider_connected=false` and `synthetic_only=true`.

## Adapter and trust boundary

`MeetingSessionAdapter` is an internal dependency-injection seam, not a public
provider wire contract. The application runtime accepts only a server-owned
adapter. The adapter prepares private identities, mints connector artifacts,
and supplies provider-neutral events; the browser never receives those private
objects.

The fixed synthetic adapter deliberately exercises the same implemented path a
future mapper must use:

```text
server-owned adapter
        -> capture authorization + transport binding
        -> durable meeting-event inbox
        -> recovery + independent envelope validation
        -> consent-bound sealed transcript window
        -> label neutralization + immediate redaction
        -> MEETING source + source-linked NEEDS_REVIEW proposals
```

The future Zoom adapter must not enter this seam until the untouched golden
fixture resolves the exact signing input, packet fields, ordering, identities,
partial/final behavior, timestamps, duplicate delivery, and reconnect
semantics. See the
[meeting connector specification](MEETING_CONNECTOR_SPEC.md) and
[golden-fixture runbook](ZOOM_GOLDEN_FIXTURE_RUNBOOK.md).

## Retry and recovery semantics

Each start attempt retains its exact private authorization and event artifact
until the durable append succeeds. A transient append failure therefore
retries the same artifact instead of minting a time-shifted authorization.
Draft finalization likewise reuses one stable attempt timestamp and stable
event identities. The inbox handles exact replay without duplicating canonical
events.

The session coordinator and redacted source remain process-local. The meeting
event inbox is a private temporary SQLite database owned by the local server
and is removed when that server closes. Safe session reads support in-process
recovery only; no restart durability or production multi-worker semantics are
claimed.

## Deferred work

The following remain deliberately outside this boundary:

1. the first two-person private Zoom golden capture;
2. a fixture-pinned Zoom-to-provider-neutral mapper;
3. server-owned OAuth, webhook, RTMS WebSocket, reconnect, and shutdown;
4. durable production session coordination and redacted-source storage; and
5. one real synthetic Zoom meeting completing the unchanged ExitSpec
   review-to-Evidence-Pack loop.
