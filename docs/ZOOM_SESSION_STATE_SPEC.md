# Zoom RTMS session state and idempotency

Status: implemented as a bounded, single-session local coordinator; it does
not claim a live Zoom transport or durable multi-tenant workflow

[`zoom_session_runtime.py`](../src/exitspec/zoom_session_runtime.py) coordinates
one already-authorized session around the strict decoder. It is intentionally
separate from the existing provider-neutral inbox and guided UI projection.
The later proposal bridge can consume its private processing input without
creating a second source or contract system.

## States

| State | Meaning | Next action |
| --- | --- | --- |
| `STARTING` | Session exists, RTMS start is not yet acknowledged | wait for start |
| `LISTENING` | Transcript delivery is accepted | wait for stop |
| `INTERRUPTED` | Delivery was interrupted and reconnect has not begun | reconnect |
| `RECONNECTING` | Reconnect is in progress; bounded transcript delivery may continue | reconnect |
| `PROCESSING` | One stop has been accepted and normalized input is frozen for processing | process transcript |
| `DRAFT_READY` | Downstream processing has returned one bound result digest | review draft |
| `FAILED` | The session cannot advance without a safe restart | recover or restart |

The supported transitions are:

```text
STARTING -> LISTENING -> PROCESSING -> DRAFT_READY
                 |             |
                 v             v
            INTERRUPTED -> RECONNECTING
                 |             |
                 +-------------+

Any non-terminal state may fail on timeout; reconnect failure and missing
transcript fail closed. Processing failure is terminal for this session.
```

Packets may arrive out of order while listening, interrupted, or reconnecting.
Before processing, the coordinator sorts normalized segments by provider
timestamp, segment bounds, arrival index, and segment ID. It never invents a
missing packet or timing value.

## Idempotency and finalization

- Every mutation binds a server-owned idempotency key to an action and a
  canonical request digest. Reusing a key for different input fails closed.
- A transcript packet is deduplicated by its exact packet digest. A duplicate
  with different arrival metadata is suppressed; conflicting normalized content
  is rejected.
- Replayed start, interruption, reconnect, stop, timeout, and processing
  completion signals are safe no-ops when the state already reflects that
  signal. The public result marks the suppression and does not expose payload.
- The first accepted stop sets `finalization_count = 1` exactly once. A stop
  with no transcript moves directly to `FAILED/NO_TRANSCRIPT`; it never creates
  a draft. Repeated stops cannot create another processing input or POC.
- `PROCESSING` releases one private, digest-bound ordered input. Only a later
  processing result can move the session to `DRAFT_READY`, and a conflicting
  result digest is rejected.

All public snapshots contain only counts, states, digests, timestamps, and
authority flags. They contain no transcript text, provider IDs, or packet
payloads. Every snapshot remains `UNTRUSTED_SOURCE_ONLY` and `NEEDS_REVIEW`,
with confirmation, freeze, measurement, and verdict authority set to false.

## Recovery and compatibility

`ZoomSessionCheckpoint` is a private, non-serializable local checkpoint. It
retains normalized segments and the content-free idempotency ledger only for a
bounded process-local recovery handoff; its representation and dump methods do
not reveal transcript content. Recovery validates the state projection,
segment digests, processing digest, and operation identities before accepting
the checkpoint. A recovered `PROCESSING` session reuses the same input and
cannot finalize a second time.

The runtime version is `exitspec.zoom-session-runtime/1.0`. It is additive and
does not rewrite existing meeting connector contracts, inbox schema, legacy
capture plans, or incomplete captures. A future state or checkpoint format
requires a new explicit version and migration rule; unknown versions remain
unsupported. No production persistence or cross-worker exactly-once claim is
made.

## Verification

[`test_zoom_session_runtime.py`](../tests/test_zoom_session_runtime.py) covers
the complete local lifecycle, duplicate start/stop, out-of-order packets,
packet-digest suppression, reconnect recovery and failure, no transcript,
timeout, processing failure, idempotency conflicts, crash recovery, and public
projection secrecy.
