# Meeting source orchestration specification

## Status

The synthetic inbox-to-source orchestration core is implemented in
[`meeting_source_orchestration.py`](../src/exitspec/meeting_source_orchestration.py).
It composes already-authorized provider-neutral events into the existing
review-only source path. It adds no HTTP route, browser action, Zoom API call,
OAuth flow, raw-packet parser, or production authorization.

## Product transition

```text
durable authenticated synthetic event inbox
        |
        v
recover + independently revalidate private event population
        |
        v
recheck current consent + seal complete stream
        |
        v
neutralize provider labels + redact immediately
        |
        v
attach one replay-safe MEETING source
        |
        v
source-linked NEEDS_REVIEW proposals
        |
        v
existing human review -> customer confirmation -> freeze -> prove
```

This closes the provider-neutral code path between the durable meeting inbox
and the existing ExitSpec product spine. It does not make the live Zoom
transport complete.

## Exact inputs

`MeetingInboxSourceOrchestrationService` accepts only:

- one `SQLiteMeetingEventInbox` created under its frozen local configuration;
- one `ProcessLocalPOCSourceIntake` for the target draft POC;
- the exact `MeetingCaptureAuthorization` used for inbox ingestion;
- the exact authenticated `MeetingTransportBinding` for the stream;
- the current `MeetingConsentAttestation`; and
- one timezone-aware orchestration clock.

The service does not accept transcript text, raw provider packets, caller-
selected source IDs, agreement fields, measurement instructions, or verdicts.

## Composed validation

One finalization attempt performs these boundaries in order:

1. Validate exact orchestration input types and current time.
2. Ask the durable inbox to revalidate its schema, stream binding, append-only
   ingress history, payload hashes, event fingerprints, retention state, and
   every recovered event envelope.
3. Pass the private recovered delivery population to the unchanged meeting
   sealer.
4. Recheck current consent, complete start/stop lifecycle, contiguous canonical
   sequence, duplicate identity, participant set, timestamps, duration, and
   frozen limits.
5. Hand the sealer-minted private transcript to the existing meeting source
   bridge.
6. Neutralize provider speaker labels, redact private text, recheck the exact
   redacted digest, and attach one process-local `MEETING` source.
7. Return one content-free orchestration projection linking the inbox recovery
   receipt, sealed-window receipt, handoff receipt, and source receipt.

Private recovered events and the sealed private transcript are request-local
references. Public orchestration output contains no transcript or candidate
text.

## Replay and concurrency

The service serializes finalization attempts within one service instance. This
avoids competing retention/recovery transactions inside the local SQLite
boundary. It is not a cross-process distributed lock.

The downstream source identity remains the POC plus stable stream digest.
Therefore:

- an exact serial replay returns the original source with
  `idempotent_replay=true`;
- concurrent calls through one service instance create one source and one set
  of proposals; and
- changed content under one stable stream cannot silently become another
  source.

The durable inbox remains the source of restart recovery until its private
payload TTL expires. The redacted source itself is process-local, so a new
process can reconstruct and attach a new process-local source while the private
payload remains available. ExitSpec does not yet claim a durable exactly-once
completion record across processes.

## Public projection

`MeetingSourceOrchestrationResult` contains:

- the content-free `MeetingEventInboxStreamReceipt`;
- the content-free `MeetingSourceHandoffResult`;
- an RFC 8785-based SHA-256 binding those two projections;
- `SYNTHETIC_REVIEW_SOURCE_ONLY` completion scope;
- `INBOX_TTL_UNCHANGED` retention state; and
- explicit zero downstream authority.

The orchestration digest detects contradictory mutation inside the result. It
does not prove Zoom authorship, truthful execution, hardware identity, or
cross-process exactly-once completion.

```text
transcript_authority = UNTRUSTED_SOURCE_ONLY
review_state = NEEDS_REVIEW
may_delete_private_inbox_payloads = false
may_confirm_contract = false
may_freeze_contract = false
may_start_measurement = false
may_assign_verdict = false
synthetic_only = true
```

Transcript text containing words such as `confirm`, `freeze`, `run`, or `PASS`
remains ordinary source text and cannot change these fields.

## Fail-closed outcomes

| Failure | Meaning | Source effect |
| --- | --- | --- |
| `MEETING_ORCHESTRATION_INVALID_REQUEST` | Boundary input type is unsupported | No write |
| `MEETING_ORCHESTRATION_STREAM_NOT_FOUND` | No authorized stream is available | No write |
| `MEETING_ORCHESTRATION_STREAM_CONFLICT` | Inbox stream is durably tainted by conflicting input | No write |
| `MEETING_ORCHESTRATION_STREAM_CAPACITY` | Frozen event or character bounds were exceeded | No write |
| `MEETING_ORCHESTRATION_PAYLOAD_EXPIRED` | Private replay material expired before finalization | No write |
| `MEETING_ORCHESTRATION_INBOX_INTEGRITY` | Durable state failed independent integrity verification | No write |
| `MEETING_ORCHESTRATION_INBOX_STORAGE` | SQLite recovery is unavailable | No write |
| `MEETING_ORCHESTRATION_SEALING_REJECTED` | Completeness, consent, timeline, or binding failed | No write |
| `MEETING_ORCHESTRATION_SOURCE_HANDOFF_REJECTED` | Redaction, draft, identity, or source attachment failed | No new source |
| `MEETING_ORCHESTRATION_INTERNAL` | Clock, projection, or unknown internal work failed | Fail closed |

Sealing and source-handoff refusals may expose only their existing typed safe
upstream code. Raw exception messages, transcript text, provider labels, and
participant identities are discarded.

## Retention boundary

Recovery applies the durable inbox's existing retention policy before reading
private payloads. Expired payloads are securely deleted under the inbox's
retention-only guard and cannot be reconstructed.

Successful orchestration does not delete an unexpired private annex. The public
result explicitly says `INBOX_TTL_UNCHANGED` and
`may_delete_private_inbox_payloads=false`. This remains necessary because the
accepted redacted source is process-local. Immediate deletion after a
non-durable source write could destroy both restart recovery and the only
durable input.

## Implemented acceptance evidence

The adversarial suite proves:

1. reordered durable events and one exact provider duplicate recover, seal,
   redact, and create one source after inbox restart;
2. serial and concurrent replay create one process-local source and one set of
   proposals;
3. gaps, missing stop, revoked consent, conflicts, capacity taint, missing
   streams, and expired payloads create no source;
4. inbox integrity, storage, clock, and source failures are sanitized;
5. provider labels, participant identities, email addresses, and supported
   secret patterns do not enter the public result or redacted source;
6. authority attacks remain `NEEDS_REVIEW` source text; and
7. the public projection is immutable, population-linked, digest-bound, and
   explicit about retention and zero lifecycle authority.

## Deferred live product work

The following remain outside this implementation:

- the first untouched sanitized Zoom RTMS golden fixture;
- the pinned Zoom packet-to-provider-neutral event mapper;
- exact HTTP signing-input extraction proven against that fixture;
- server-owned OAuth, RTMS start/stop, sockets, reconnect, and shutdown;
- a durable completion marker and durable redacted source store;
- an atomic or recoverable successful-handoff/private-annex deletion protocol;
- the connect, consent, capture, finalization, and recovery UI; and
- one real synthetic Zoom meeting completing the unchanged ExitSpec loop.

Real customer meetings remain prohibited until the production security gates
in [SECURITY.md](SECURITY.md) pass.
