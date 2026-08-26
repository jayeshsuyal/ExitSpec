# Meeting connector specification

## Status and decision

This document defines the first provider-neutral meeting-stream boundary, the
Zoom RTMS capability decision checked on **2026-08-06**, the implemented
synthetic-only authentication seam for opaque webhook bytes, and the
implemented provider-neutral sealed-window-to-source bridge, and the
implemented local synthetic meeting-session application boundary.

The setup/runtime custody correction is defined in
[ZOOM_SETUP_RUNTIME_EVIDENCE_SPEC.md](ZOOM_SETUP_RUNTIME_EVIDENCE_SPEC.md).
One-time app and endpoint attestation is not per-meeting transport evidence;
neither one grants downstream product authority.

The bounded transcript packet boundary is defined in
[ZOOM_RTMS_DECODER_SPEC.md](ZOOM_RTMS_DECODER_SPEC.md). It is limited to the
pinned synthetic envelope and is not a claim that the current incomplete
private capture is a golden fixture.

The bounded session lifecycle is defined in
[ZOOM_SESSION_STATE_SPEC.md](ZOOM_SESSION_STATE_SPEC.md). It coordinates one
decoded session locally, finalizes once on stop, and exposes no downstream
authority.

The transcript-to-existing-source handoff is defined in
[ZOOM_PROPOSAL_BRIDGE_SPEC.md](ZOOM_PROPOSAL_BRIDGE_SPEC.md). It creates a
stable meeting-source draft POC, preserves Zoom provenance, and leaves all
proposal facts in the existing `NEEDS_REVIEW` review queue.

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
| Meeting source bridge | Sealer-authority verification, neutral labels, immediate redaction, exact source binding, and replay-safe attachment | Inbox deletion, agreement, execution, evidence, or verdict authority |
| Meeting source orchestration | Durable-inbox recovery, current-consent sealing, source-handoff composition, and a content-free linked result | Network transport, cross-process exactly-once completion, inbox deletion, agreement, execution, evidence, or verdict authority |
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

### Implemented opaque webhook authentication seam

[`zoom_webhook_auth.py`](../src/exitspec/zoom_webhook_auth.py) now implements
only the first cryptographic step above. It verifies the Zoom `v0` HMAC over an
exact bounded byte string and timestamp, applies a reviewed freshness window,
and records exact process-local replay. Its content-free receipt explicitly has
no permission to parse the payload, create a transport binding, append to the
meeting inbox, or affect any agreement, measurement, or verdict.

This seam has no HTTP route and no Zoom event model. Its exact signing-input
extraction still must be proven against an untouched Zoom golden fixture before
a network adapter may call it. The complete rule, threat boundary, and
adversarial cases are in
[ZOOM_WEBHOOK_AUTH_SPEC.md](ZOOM_WEBHOOK_AUTH_SPEC.md).

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
rewrite a previously sealed record. The sealer also binds a private in-process
authority marker and integrity projection so a directly constructed or
post-seal-mutated object cannot enter the implemented source bridge.

## Durable connector inbox

PR110 implements the server-internal inbox in
[`meeting_event_inbox.py`](../src/exitspec/meeting_event_inbox.py). It accepts
only events that first pass the PR108 provider-neutral envelope validation. It
does not accept raw Zoom packets and does not define a Zoom wire schema.

The durable identity binds the complete capture authorization, verified
transport binding, adapter and versions, stream identity, consent-bound limits,
event identity, and event fingerprint. Ingestion then distinguishes two cases:

- replaying the same ingress idempotency key with the same input returns the
  original receipt, writes no second ingress or event record, and never extends
  private retention; and
- delivering the same event under a new ingress key records one
  `EXACT_DUPLICATE` receipt while retaining one canonical event.

Reusing an ingress key with changed input, changing content under one event
identity, or placing a different event identity at an occupied sequence creates
an immutable `TAINTED_CONFLICT` marker. Exhausting the global ingress bound,
the frozen per-stream event bound, or the transcript-character bound creates an
immutable `TAINTED_CAPACITY` marker. A tainted stream cannot be recovered or
sealed after restart; dropping the losing event can never repair it.

The SQLite layout separates:

- immutable, content-free stream bindings;
- one canonical event receipt per stream-scoped event identity and sequence;
- append-only ingress receipts for accepted and exact-duplicate deliveries;
- append-only conflict or capacity markers; and
- one private payload annex used only for ordering, restart recovery, and the
  later redaction handoff.

Recovery independently checks the schema, frozen stream binding, every ingress
digest, every private payload digest, the canonical codec, and the PR108 event
envelope. It then reconstructs exact-duplicate delivery counts and returns a
private non-serializable population to the unchanged PR108 sealer. Temporary
gaps and reordered arrival are allowed in the inbox; completeness remains the
sealer's responsibility.

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
- Zoom's public sample constructs `x-zm-signature` with HMAC SHA-256 over a
  versioned timestamp and serialized request body;
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

The implemented authentication seam does not retain its request-local opaque
body; it keeps only a digest, byte count, timestamps, identities, and explicit
zero-authority facts. The future adapter must not persist raw webhook bodies,
native Zoom packets, raw meeting IDs, participant IDs, participant names, or
transcript text in public records. The synthetic-only inbox may temporarily
persist the private provider-neutral canonical event needed for ordering and
restart recovery. Its retention is frozen between 60 seconds and 24 hours,
files are owner-only, and expiry uses SQLite secure deletion followed by WAL
truncation. This is not an encrypted production vault or a claim of forensic
erasure on every storage medium; production customer data remains prohibited.

The implemented source bridge immediately neutralizes speaker labels, redacts
transcript text, and attaches one process-local `MEETING` source while retaining
only content-free handoff provenance plus the existing redacted source. It does
not mutate or purge the durable inbox annex. That boundary is explicit because
deleting durable private input after a process-local source write could lose
both reconstructability and the redacted source after a crash. The inbox's
existing bounded TTL remains in force; a future live orchestrator must first
make the redacted source durable or define an atomic recoverable handoff before
post-handoff deletion can be claimed. The complete rule is in
[MEETING_SOURCE_HANDOFF_SPEC.md](MEETING_SOURCE_HANDOFF_SPEC.md). The PR108
sealed receipt field `raw_transcript_persisted=false` means the released sealed
artifact does not retain raw transcript text. It does not claim that the
short-lived pre-handoff inbox never existed or has already been purged. The
separate handoff receipt therefore exposes the inbox-retention boundary
explicitly instead of treating that field as deletion evidence.

The implemented synthetic orchestration core now composes durable inbox
recovery, independent event revalidation, current-consent sealing, and that
source bridge. It returns only linked content-free receipts and serializes
finalization within one service instance. It does not add a route, delete the
private annex, or claim a cross-process durable completion record. See
[MEETING_SOURCE_ORCHESTRATION_SPEC.md](MEETING_SOURCE_ORCHESTRATION_SPEC.md).

The local synthetic
[guided meeting-session API](MEETING_SESSION_API.md) now invokes that core
through an injected, server-owned adapter. It exposes disclosure, consent,
start, draft-now, and content-free recovery routes while keeping meeting IDs,
participant IDs, transcript events, transport proofs, and downstream authority
out of browser input and output. Its default adapter is fixed and synthetic,
makes no network call, and reports `provider_connected=false`; this is not a
Zoom integration claim. The existing source-intake workbench now exposes the
finite consent -> start -> draft -> review path with one primary action and an
explicit `Not connected` state. The golden-fixture-pinned Zoom adapter remains
deferred.

No raw audio is requested. Customer meetings, customer identities, and customer
data are prohibited until Wave 7B passes.

## Frozen first acceptance cases

The code and capability manifest require proof for:

- malformed, forged, stale, future, mutated-body, and exact-replay webhook
  authentication attempts;
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
real Zoom developer account produces one private, untouched synthetic capture
and a separately reviewed sanitized candidate fixture containing:

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

The implemented local-only
[golden-fixture capture kit](ZOOM_GOLDEN_FIXTURE_RUNBOOK.md) validates the
synthetic plan, creates a git-ignored owner-only workspace, inventories a fixed
set of opaque artifacts, hash-seals the private original, and records a
privacy-review decision. It does not parse or sanitize Zoom bytes and cannot
authorize a mapper, network transport, fixture publication, source creation,
or product decision. No sanitized repository fixture is claimed yet.

The implemented
[bounded RTMS transcript decoder](ZOOM_RTMS_DECODER_SPEC.md) accepts only the
versioned synthetic transcript subset, requires explicit server-owned speaker
pseudonyms and digest-only provenance, and refuses unknown or ambiguous input.
It produces review-only normalized segments; it does not read the private
capture, open a network connection, create a source, or grant product
authority.

## Implementation train

1. **PR108 — connector contract and capability spike:** provider-neutral
   models, two-stage authorization, sealing rules, frozen acceptance cases, and
   no network runtime.
2. **PR110 — durable connector inbox (implemented):** append-only ingress
   receipts, API idempotency, provider-duplicate accounting, permanent
   conflict/capacity taints, restart recovery, and bounded private retention.
3. **Opaque webhook authentication boundary (implemented):** exact supplied
   bytes, `v0` HMAC, policy lifetime, freshness, bounded process-local replay,
   content-free receipts, and no route, parsing, transport, or inbox authority.
4. **Golden-fixture capture kit (implemented):** synthetic plan preflight,
   fixed opaque-byte inventory, private git-ignored custody, independent hash
   verification, privacy-review receipt, and zero parser or network authority.
5. **Bounded RTMS transcript decoder (implemented):** versioned synthetic
   vectors, strict limits, explicit pseudonyms, digest-only provenance, and
   review-only normalized segments; no raw-capture reader or network authority.
6. **Meeting session state machine (implemented):** bounded
   STARTING/LISTENING/INTERRUPTED/RECONNECTING/PROCESSING/DRAFT_READY/FAILED
   lifecycle, packet-digest duplicate suppression, one-time stop finalization,
   and private crash recovery; no proposal authority.
7. **Zoom transcript proposal bridge (implemented):** one stable meeting-source
   draft POC, existing intake/proposal models, full digest-only provenance,
   catalog-only annotations, and zero customer/evaluation authority.
8. **Golden-fixture mapper:** after the fixture gate, raw Zoom packets map into
   the provider-neutral contract using the pinned fixture; fake transport only.
9. **Zoom webhook and RTMS transport:** secrets remain server-owned; the HTTP
   signing-input extraction, OAuth, REST start/stop, handshakes, reconnect, and
   shutdown fail closed.
10. **Source bridge (implemented):** sealer-minted transcript windows enter the
   existing redacted `MEETING` source and proposal-review path with stable
   stream identity, exact replay, changed-content conflict, neutral labels, and
   content-free provenance. It has no route or inbox-deletion authority.
11. **Synthetic source orchestration (implemented):** recover and independently
   revalidate the durable inbox, recheck current consent while sealing, invoke
   the source bridge, and return one digest-bound zero-authority result. This is
   process-local coordination, not the live Zoom transport or a cross-process
   exactly-once claim.
12. **Guided meeting-session API (implemented, synthetic only):** a
   provider-neutral server-owned adapter seam, disclosure, consent, start,
   draft-now, content-free recovery, exact replay, and existing review-queue
   handoff with no Zoom connection claim.
13. **Guided product UI (implemented, synthetic only):** the existing meeting
    source offers one finite consent, start, draft-now, and safe-recovery flow,
    then hands off to human proposal review without changing the product spine.
14. **Synthetic live E2E and hardening:** one real Zoom meeting with two
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
