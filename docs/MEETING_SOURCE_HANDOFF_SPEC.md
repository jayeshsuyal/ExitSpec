# Meeting source handoff specification

## Status

The provider-neutral, synthetic-only meeting source bridge is implemented in
[`meeting_source_handoff.py`](../src/exitspec/meeting_source_handoff.py). It
connects one sealer-minted `SealedMeetingTranscript` to the existing
process-local `MEETING` source intake. It adds no HTTP route, browser action,
provider SDK, Zoom packet mapper, or live meeting transport.

This bridge is an authority boundary, not an automatic agreement generator.

## Product contract

```text
sealed provider-neutral transcript window
        |
        v
verify sealer authority + exact post-seal integrity
        |
        v
replace provider labels with stable neutral speaker labels
        |
        v
redact private text immediately
        |
        v
recheck the exact redacted digest at MEETING source intake
        |
        v
source-linked NEEDS_REVIEW proposals
        |
        v
existing human review -> customer confirmation -> freeze -> prove
```

Transcript text may describe a requirement. It may never approve that
requirement, confirm an agreement, freeze a contract, start measurement, create
evidence, or assign `PASS`, `FAIL`, `BLOCKED`, or `NOT_PROVEN`.

## Accepted input

`MeetingTranscriptSourceHandoffService` accepts only the exact
`SealedMeetingTranscript` type returned by
`seal_meeting_transcript_window(...)`. The sealer binds a private in-process
authority marker and an integrity digest covering:

- authorization, request, and POC identity;
- hashed meeting and stream identity;
- the complete public sealed-window receipt; and
- every private transcript-segment fingerprint.

A directly constructed transcript object has no sealer authority. Mutation of
the receipt or a private segment after sealing changes the integrity projection
and is rejected before redaction or source attachment. This is an in-process
object-integrity boundary, not a portable signature or proof of Zoom
authorship.

## Redaction and speaker semantics

The private sealed transcript supplies one transient redaction projection.
Provider participant IDs and labels are replaced deterministically by
`Speaker 1`, `Speaker 2`, and so on in first-seen segment order. ExitSpec does
not claim those labels prove a person's identity or role.

The bridge then applies the existing transcript redaction boundary and passes
only the redacted projection to `ProcessLocalPOCSourceIntake`. Source intake
parses and redacts again, then requires the exact content digest supplied by the
bridge. Raw meeting IDs, stream IDs, participant IDs, provider labels, and
unredacted transcript text are absent from the public handoff result.

The source store intentionally retains the redacted source text because it is
the provenance for human review. The bridge retains no second transcript copy.
Clearing a Python reference is not represented as secure memory erasure.

## Identity, replay, and revision

Three identities have distinct jobs:

- the stream-identity digest is the stable source identity within one POC;
- the event-stream digest identifies the exact sealed event population; and
- the redacted-content digest binds the source text that crossed the handoff.

The process-local idempotency key is derived from the POC and stable stream
identity. Exact serial or concurrent replay returns the original source receipt
and creates no duplicate proposal. Changed transcript content sealed for the
same stream conflicts with the original attach request and requires a future
explicit revision path; it cannot silently become a second source.

## Public receipt and authority

`MeetingSourceHandoffResult` links the existing six-field `POCSourceReceipt` to
a content-free `MeetingSourceHandoffReceipt`. The handoff receipt records only
hashes, counts, versions, times, the sealed-window receipt, and fixed safety
facts:

```text
transcript_authority = UNTRUSTED_SOURCE_ONLY
review_state = NEEDS_REVIEW
speaker_labels_neutralized = true
raw_audio_received = false
raw_transcript_retained_by_handoff = false
inbox_retention_authority = UNCHANGED
may_delete_private_inbox_payloads = false
may_confirm_contract = false
may_freeze_contract = false
may_start_measurement = false
may_assign_verdict = false
synthetic_only = true
```

The embedded sealed-window receipt remains content-free. The result carries no
candidate text; candidates are retrieved only through the existing human-review
source projection.

## Durable inbox retention boundary

The bridge does not mutate `SQLiteMeetingEventInbox` and does not delete its
private payload annex. The inbox's existing bounded TTL, owner-only file mode,
SQLite secure deletion, and WAL truncation remain unchanged.

This is deliberate for the current local architecture: the accepted redacted
source is process-local and does not survive restart. Deleting the durable
private population immediately after a process-local source attach could lose
both the source and the ability to reconstruct it after a crash. The receipt
therefore says `inbox_retention_authority=UNCHANGED` and
`may_delete_private_inbox_payloads=false`.

The live connector orchestrator remains deferred. It must either make the
redacted source durable before deleting the private annex or define an atomic,
recoverable handoff protocol. Until then, ExitSpec must not claim immediate
post-handoff inbox purge or a complete live Zoom lifecycle.

## Fail-closed outcomes

| Failure | Meaning | Source effect |
| --- | --- | --- |
| `MEETING_HANDOFF_INVALID_TRANSCRIPT` | Input is not the sealed transcript type | No write |
| `MEETING_HANDOFF_BINDING_MISMATCH` | Sealer authority, receipt, or post-seal integrity does not match | No write |
| `MEETING_HANDOFF_REDACTION_FAILED` | Redaction or exact source-content binding failed | No write |
| `MEETING_HANDOFF_SOURCE_UNAVAILABLE` | The draft POC cannot accept a source | No write |
| `MEETING_HANDOFF_SOURCE_CONFLICT` | Stable stream identity names changed content or stale state | Existing source unchanged |
| `MEETING_HANDOFF_CAPACITY_EXCEEDED` | Bounded process-local source state is full | No write |
| `MEETING_HANDOFF_INTERNAL` | Clock, projection, or unknown internal work failed | Fail closed |

Failures are content-free and do not echo transcript text, participant labels,
provider bodies, or source exception messages.

## Implemented acceptance evidence

The adversarial test suite proves:

1. one sealed synthetic window becomes one redacted `MEETING` source;
2. all candidates remain source-linked `NEEDS_REVIEW` input;
3. provider labels, participant identities, email addresses, and supported
   secret patterns do not cross the public boundary;
4. direct construction and post-seal mutation fail before source attachment;
5. exact serial and concurrent replay create one source;
6. changed content under the same stable stream identity is a conflict;
7. transcript instructions to confirm, freeze, run, or return `PASS` remain
   ordinary review-only text;
8. invalid clocks, archived drafts, redaction failures, and source failures are
   typed and content-free; and
9. the public result is immutable, exactly linked, and content-free.

## Deferred live connector work

The following remain outside this implementation:

- the first untouched sanitized Zoom RTMS golden fixture;
- a pinned raw Zoom packet-to-canonical-event mapper;
- HTTP signing-input extraction proven against that fixture;
- Zoom OAuth, REST start/stop, and authenticated signaling/transcript sockets;
- a durable orchestrator from inbox recovery through sealing and handoff;
- an atomic redacted-source durability and private-annex deletion policy;
- product UI for connect, consent, capture, finalization, and recovery; and
- one real synthetic Zoom meeting completing the unchanged ExitSpec loop.

Real customer meetings remain prohibited until the production security gates
in [SECURITY.md](SECURITY.md) pass.
