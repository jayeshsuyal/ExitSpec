# Bounded numeric RTMS roster enrollment

This candidate adds an explicit local operator mode to the existing source app:

```sh
python -m exitspec.zoom_live_operator --enroll-metadata --port 8765
```

Use only after exact-candidate review and separate account, cost and data-custody approval. No live protocol or identity qualification is asserted by the offline tests. Direct pairing with an already approved exact numeric roster remains available without this flag.

Before starting, pre-stage the existing RTMS app, transcript-only scope, callback-only HTTPS tunnel and two consenting people. The operator supplies two distinct content-free metadata consent receipts and the existing owner/rotation/cost attestations. Credentials and numeric confirmations use the existing hidden local TTY reader. Raw names, IDs and events are not logged or placed in receipts.

Enrollment starts the callback and authenticated signaling connection before browser capture consent. It never opens a media socket, sends a media handshake/ready ACK, or admits transcript content. It has 30 seconds within a single approved cumulative window of 60–120 seconds. Adoption, browser waiting and capture cannot renew that deadline.

The operator arms each of two speaking turns. Each arm waits for a later provider keep-alive on the same signaling connection, then accepts only a speaker-event timestamp strictly later than that barrier and no later than local receipt time. Queued pre-arm events, old nonces and replayed timestamps cannot satisfy the slot. Clock disagreement or missing metadata may cause refusal. The two distinct provider numeric IDs require explicit operator confirmation against the observed speaking turns; this is **human-attested association, not authenticated Zoom identity**. SDK string IDs and display names are never used as a mapping.

After both confirmations, a private one-use handle adopts the same child, stream, generation and absolute deadline into ordinary exact-roster pairing. Existing browser consent/Start is separately required before media can open. Unknown participants, ambiguous activity, leave/rejoin, changed streams, expiry, reset, archive, replacement or shutdown revoke and reap the pending/adopted session. Enrollment has no reconnect/retry or media fallback.

Zoom's public contract documents [speaker metadata and keep-alive timestamps](https://developers.zoom.us/docs/rtms/event-reference/), but does **not establish speaker-event delivery before media readiness**. If that metadata does not arrive, stop at the enrollment deadline. A separately authorized live observation is required to establish this startup path; fake tests cannot establish it.

For the subsequent synthetic capture, use the existing weekly-total/monthly-total utterances, target roughly 45 seconds of capture and stop by 90 seconds of the cumulative window. Require the existing provider stop acknowledgement, normal media close, attached source and content-free receipt. The receipt binds the metadata enrollment and labels association as human-attested. A local time/spend declaration is not a provider-enforced billing cap.

The independent Fireworks diagnostic and its frozen request are unchanged. This operator mode supplies no Fireworks launch handle or serving qualification.
