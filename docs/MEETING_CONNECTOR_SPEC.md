# Meeting connector specification

## Status and decision

This document defines the first provider-neutral meeting-stream boundary and
the Zoom RTMS capability decision checked on **2026-08-06**.

The first platform integration will use **Zoom Realtime Media Streams (RTMS),
transcript only**, for an explicitly synthetic meeting with consenting test
participants. It will not request audio, video, screen share, or chat.

This is not production authorization. Real customer meetings remain behind the
durable identity, tenant authorization, consent, retention, deletion, and
incident gates in Wave 7B of the
[engineering playbook](ENGINEERING_PLAYBOOK.md).

## Product promise

The connector adds one new source transport. It does not add a new product
loop:

```text
approved synthetic Zoom meeting
        |
        v
exact ExitSpec disclosure + participant consent
        |
        v
short-lived capture-only authorization
        |
        v
verified Zoom webhook + authenticated RTMS WebSocket
        |
        v
private provider-neutral transcript events
        |
        v
ordered, deduplicated, bounded sealed window
        |
        v
redacted MEETING source + source-linked NEEDS_REVIEW proposals
        |
        v
existing review -> customer confirmation -> freeze -> prove -> Evidence Pack
```

Zoom may supply words and participant attribution. It may never approve a
proposal, confirm an agreement, freeze a contract, start measurement, create
evidence, or assign a verdict.

## Ownership boundary

| Component | Owns | Must never own |
| --- | --- | --- |
| Zoom RTMS | Delivery of provider packets and provider participant labels | ExitSpec consent, source authority, agreement state, measurement, or verdict |
| Zoom adapter | Webhook verification, WebSocket handshake, raw-packet parsing, and mapping into the provider-neutral contract | Customer confirmation, freeze, proof, or `PASS` |
| Meeting connector core | Capture authorization, exact bindings, event integrity, participant-set enforcement, limits, and sealed-window receipt | OAuth credentials, network transport, redaction policy, or downstream decisions |
| Existing source pipeline | Immediate redaction, source attachment, proposal provenance, and `NEEDS_REVIEW` state | Automatic proposal approval |
| Existing ExitSpec spine | Human review, exact customer confirmation, freeze, measurement, verdict, Evidence Pack, and handoff | Treating transcript text as authority |

## Two-stage authorization

Capture and transport are intentionally separate.

### 1. Capture authorization

Before ExitSpec requests RTMS start, it must bind:

- one draft POC;
- one approved synthetic meeting identity;
- one exact participant set and organizer;
- one exact disclosure digest;
- complete, unrevoked participant consent recorded before the request;
- one provider, adapter ID, and adapter version;
- event, transcript-size, duration, and start-time limits; and
- the `SYNTHETIC_SOURCE_CAPTURE_ONLY` authority.

The resulting authorization expires within five minutes and explicitly states:

```text
transcript_authority = UNTRUSTED_SOURCE_ONLY
review_state = NEEDS_REVIEW
may_confirm_contract = false
may_freeze_contract = false
may_start_measurement = false
may_assign_verdict = false
```

### 2. Transport binding

After RTMS starts, a future Zoom transport may mint a server-internal binding
only after it has:

- verified the exact `meeting.rtms_started` webhook signature;
- checked timestamp freshness and replay state;
- matched the authorized meeting digest;
- authenticated the signaling and transcript WebSocket handshakes;
- bound one RTMS stream identity and protocol version; and
- established the stream before the capture authorization expires.

The browser cannot construct or submit this binding. A Zoom connection without
the earlier ExitSpec consent authorization remains powerless.

## Provider-neutral event contract

The executable contract is implemented in
[`meeting_connector.py`](../src/exitspec/meeting_connector.py).

The first version accepts five canonical event kinds:

1. `STREAM_STARTED`, carrying the exact participant snapshot;
2. `PARTICIPANT_JOINED`;
3. `PARTICIPANT_LEFT`;
4. `TRANSCRIPT_SEGMENT`, carrying private text and provider timing metadata;
5. `STREAM_STOPPED`, carrying a bounded stop reason.

Every event binds the adapter, adapter version, meeting, stream, transport
binding, canonical sequence, and server receipt time. Raw meeting IDs,
participant IDs, provider labels, and transcript text are private models that
refuse ordinary serialization and copying.

The canonical event sequence is an ExitSpec adapter concept, not a claim about
the current Zoom wire protocol. The exact Zoom-to-canonical ordering mapping is
deferred until the first untouched golden RTMS fixture is captured.

## Ordering, replay, and finalization

The first sealed-window rules are deliberately strict:

- input arrival may be reordered;
- exact duplicate event identities with identical fingerprints are ignored and
  counted;
- one event identity with changed content is a conflict;
- two different event identities may not claim the same canonical sequence;
- canonical sequences must be contiguous and begin at one;
- the first event must be one `STREAM_STARTED`;
- the last event must be one `STREAM_STOPPED`;
- at least one transcript segment is required;
- every transcript participant must belong to the consented snapshot;
- join or leave after consent invalidates the complete window in v1;
- meeting, stream, adapter, or transport mismatch fails closed;
- timestamps must be monotonic in canonical order;
- duration, event-count, and transcript-size limits are enforced; and
- incomplete, conflicting, expired, or over-limit input releases no sealed
  transcript.

An exact replay creates the same event-stream digest. Recalculation does not
rewrite a previously sealed record.

## Consent semantics

Zoom documents a participant-visible RTMS disclosure and activity indicators.
Those are useful platform signals, but they are not treated as ExitSpec's exact
consent record.

ExitSpec requires its own version-bound disclosure acknowledgement from every
participant in the approved set before requesting RTMS start. Silence,
attendance, host approval, the Zoom disclosure, or transcript participation is
not inferred as consent.

If consent is absent, incomplete, revoked, or bound to another disclosure,
capture does not start. If the participant set changes, v1 stops and requires a
new consent record before another capture window.

## Zoom capability snapshot

The machine-readable snapshot is
[`zoom-rtms-capability-spike-v1.json`](../examples/meeting/zoom-rtms-capability-spike-v1.json).
It records architectural inputs, not a frozen Zoom wire schema.

The official Zoom documentation checked on 2026-08-06 supports the following
decision:

- RTMS can deliver live transcript data over WebSockets;
- transcript-only access uses `meeting:read:meeting_transcript`;
- on-demand start uses the participant RTMS app-status API and its granular
  scope;
- `meeting.rtms_started` and `meeting.rtms_stopped` are required lifecycle
  events;
- webhook authenticity uses `x-zm-signature` with HMAC SHA-256 over the exact
  timestamp and body;
- the RTMS start event supplies the meeting, stream, and signaling-server
  binding;
- transcript packets use media type `8`, message type `17`, and documented
  participant, timing, language, and text fields;
- signaling events document participant joins and leaves; and
- RTMS requires Zoom Developer Pack credits.

Primary sources:

- [Getting started with RTMS](https://developers.zoom.us/docs/rtms/meetings/getting-started/)
- [Add RTMS features and scopes](https://developers.zoom.us/docs/rtms/meetings/add-features/)
- [REST start and lifecycle quickstart](https://developers.zoom.us/docs/rtms/meetings/quickstart-rest-api/)
- [Transcript WebSocket quickstart](https://developers.zoom.us/docs/rtms/meetings/quickstart-websockets/)
- [Transcript packet fields](https://developers.zoom.us/docs/rtms/meetings/media/)
- [Participant-visible RTMS experience](https://developers.zoom.us/docs/rtms/meetings/ux-participant/)
- [Webhook verification](https://developers.zoom.us/docs/api/webhooks/)

## Least-privilege first demo

The first Zoom app configuration is:

```text
data classification: synthetic only
start mode: on demand after ExitSpec consent
media: transcript only
required media scope: meeting:read:meeting_transcript
raw audio: never requested or received
participants: two approved synthetic test identities
confirmation: explicitly synthetic demo only
```

Auto-start is disabled because it would begin the platform sharing flow before
ExitSpec has completed its own consent gate.

## Privacy and retention

The initial adapter must not persist raw webhook bodies, raw meeting IDs,
participant IDs, participant names, or transcript packets in public records.
Raw packets may exist only in the bounded connector inbox needed for ordering
and replay. The future handoff must immediately neutralize speaker labels,
redact transcript text, attach one `MEETING` source, and retain only
content-free provenance plus the existing redacted source.

No raw audio is requested. Customer meetings, customer identities, and customer
data are prohibited until Wave 7B passes.

## Frozen first acceptance cases

The code and capability manifest require proof for:

- reordered exact duplicates;
- a changed duplicate identity;
- a missing canonical sequence;
- a missing stop event;
- participant join or leave after consent;
- wrong meeting or stream binding;
- late transport establishment;
- absent, incomplete, mismatched, or revoked consent;
- transcript text attempting to grant confirmation or verdict authority; and
- private-data serialization attempts.

Every integrity or consent violation releases no source. Authority attacks in
otherwise valid transcript text remain ordinary untrusted text and can create
only `NEEDS_REVIEW` proposals after redaction.

## Deferred until the first golden Zoom fixture

Do not freeze a raw Zoom packet schema or implement the network mapper until a
real Zoom developer account produces one sanitized, untouched synthetic
fixture containing:

- exact app configuration and granted scopes;
- RTMS-started and RTMS-stopped webhook bodies;
- signaling and transcript handshakes;
- two-participant join, transcript, and leave packets;
- one disconnect/reconnect trace;
- one exact duplicate-delivery trace;
- observed ordering and timestamp behavior; and
- privacy and retention classification.

The fixture must resolve the mapping of `timestamp`, `start_time`, and
`end_time`, partial-versus-final transcript behavior, participant identity
stability, and reconnect ordering. Matching documented field names is not
enough.

## Implementation train

1. **PR108 — connector contract and capability spike:** provider-neutral
   models, two-stage authorization, sealing rules, frozen acceptance cases, and
   no network runtime.
2. **PR109 — durable connector inbox:** append-only event receipt,
   idempotency, conflict detection, restart recovery, and bounded retention.
3. **PR110 — Zoom golden-fixture mapper:** raw Zoom packets map into the
   provider-neutral contract using the pinned fixture; fake transport only.
4. **PR111 — Zoom webhook and RTMS transport:** secrets remain server-owned;
   signatures, freshness, OAuth, REST start/stop, handshakes, reconnect, and
   shutdown fail closed.
5. **PR112 — source bridge:** sealed transcript windows enter the existing
   redacted `MEETING` source and proposal-review path.
6. **PR113 — guided product UI:** connect, consent, capture, draft-now,
   finalization, and safe recovery states without changing the product spine.
7. **PR114 — synthetic live E2E and hardening:** one real Zoom meeting with two
   consenting synthetic participants completes the existing ExitSpec demo loop.

## Exit gate for the complete Zoom train

```text
One approved synthetic Zoom meeting with two consenting test participants
produces one exact source-linked draft during the call. Forged, stale,
duplicate-conflicting, reordered-with-gaps, non-consented, participant-drifted,
or incomplete input cannot create source or downstream authority. Restart
preserves accepted event history. The employee reviews the draft, the customer
confirms the exact version in the explicitly synthetic demo, and the unchanged
ExitSpec spine reaches its existing Evidence Pack and human handoff.
```
