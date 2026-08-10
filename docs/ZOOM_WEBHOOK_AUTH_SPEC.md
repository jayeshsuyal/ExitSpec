# Zoom webhook authentication boundary

## Status and decision

This document defines the implemented, synthetic-only authentication seam for
opaque Zoom webhook bytes, checked against Zoom's public material on
**2026-08-06**.

The seam answers exactly one question:

> Does this exact bounded byte string carry a valid Zoom `v0` HMAC under this
> active, reviewed server policy and freshness window?

An authenticated delivery receives a content-free receipt with no downstream
authority. The implementation does **not** expose an HTTP endpoint, parse a
Zoom event, freeze a Zoom wire schema, create a meeting or stream binding,
append to the durable meeting inbox, call RTMS, or create an ExitSpec source.
Those steps remain blocked on the first private untouched Zoom capture and a
separately reviewed sanitized golden fixture.

The executable boundary is
[`zoom_webhook_auth.py`](../src/exitspec/zoom_webhook_auth.py).

## Source basis and unresolved wire question

Zoom's official webhook sample constructs the versioned signing message as:

```text
v0:{x-zm-request-timestamp}:{JSON.stringify(request.body)}
```

It then calculates HMAC-SHA256 with the webhook secret and compares the result
with `x-zm-signature` in the shape `v0=<lowercase hex>`.

ExitSpec deliberately accepts an already captured `bytes` value and signs:

```text
message = b"v0:" + ASCII(timestamp) + b":" + exact_opaque_body
expected = "v0=" + HEX(HMAC_SHA256(server_secret, message))
```

The verifier never parses and reserializes JSON. This removes accidental body
mutation inside the authentication seam, but it is not yet a claim that an HTTP
framework's raw request bytes are always identical to Zoom's signing
representation. The future HTTP adapter must prove that exact extraction
against an untouched synthetic Zoom delivery before it can call this seam.
Matching a hand-authored JSON example is not sufficient.

Primary sources:

- [Zoom webhook sample at the reviewed commit](https://github.com/zoom/webhook-sample/blob/b112a2ca826379328d17102ac4e94b6da5944d18/index.js)
- [Zoom webhook sample repository](https://github.com/zoom/webhook-sample)
- [Zoom RTMS REST and lifecycle quickstart](https://developers.zoom.us/docs/rtms/meetings/quickstart-rest-api/)
- [Zoom RTMS stream lifecycle](https://developers.zoom.us/docs/rtms/meetings/work-with-streams/)

## Reviewed policy

`ZoomWebhookAuthenticationPolicy` binds all server-owned limits into one
SHA-256 identity:

- policy ID and version;
- signature version `v0`;
- maximum non-empty body size, capped by the implementation at 1 MiB;
- maximum past age, capped at 15 minutes;
- maximum future clock skew, capped at five minutes;
- bounded process-local replay-record capacity;
- reviewed-at and expires-at timestamps; and
- the non-overridable `synthetic_only=true` classification.

The webhook secret is passed separately as server-owned bytes. It is never a
policy field, receipt field, log value, browser value, fixture, or exception
message.

## Verification algorithm

For one request-local call, ExitSpec:

1. requires a timezone-aware monotonic application-clock observation;
2. requires an active, unexpired reviewed policy and an open authenticator;
3. accepts only a non-empty exact `bytes` body within the reviewed bound;
4. accepts only a canonical decimal timestamp and exact lowercase
   `v0=<64 hex>` signature shape;
5. calculates the expected signature over the exact supplied timestamp text
   and opaque bytes and compares it in constant time;
6. parses the now-authenticated timestamp and enforces the reviewed past-age
   and future-skew windows;
7. calculates the body digest and delivery identity without retaining the body;
8. atomically checks process-local replay state, clock rollback, and capacity;
   and
9. returns one immutable, content-free receipt.

Malformed timestamp and signature shapes use the same public authentication
failure as a wrong HMAC. Untrusted body, header, secret, and identifier values
are never reflected in public failure messages.

## Replay semantics

One delivery identity binds the timestamp text, supplied signature, exact body
digest, and body length. During the freshness window:

- the first valid observation returns `first_observation=true`;
- an exact replay returns the original immutable receipt with
  `exact_replay=true`;
- both results set `downstream_effect_permitted=false`; and
- capacity exhaustion fails closed instead of evicting an unexpired record.

Expired replay entries are pruned before a new record is admitted. A backwards
application clock is a local state error, not an untrusted-request verdict.

Replay state is bounded and process-local. It does not survive restart and is
not sufficient for production or multi-instance replay prevention. The future
network transport requires durable, tenant-scoped delivery identity and
operational clock controls before real customer traffic.

## Receipt and authority

The receipt may disclose only:

- policy identity and digest;
- opaque body SHA-256 and byte count;
- content-free delivery and receipt identities;
- request, authentication, and replay-expiry times;
- signature version and successful verification; and
- explicit negative authority fields.

The receipt fixes all of these to `false`:

```text
may_parse_zoom_payload
may_create_transport_binding
may_append_meeting_inbox
may_confirm_contract
may_freeze_contract
may_start_measurement
may_assign_verdict
```

Authentication therefore cannot bridge the architectural gap from untrusted
network bytes to provider-neutral meeting events. The golden-fixture mapper,
transport binding, durable inbox write, redaction handoff, human review,
customer confirmation, contract freeze, measurement, and verdict remain
separate gates.

## Privacy and secret handling

The raw body exists only as a request-local input to authentication. The seam
does not retain it in replay state or return it in a model. This is a data-flow
rule, not a Python memory-erasure claim.

The authenticator refuses ordinary copy, deep-copy, and pickle paths. `close()`
drops its process-local secret and replay references, but does not claim
forensic erasure of immutable Python bytes or process memory. Real secret
storage, rotation, audit, and incident response remain production gates.

## Frozen adversarial cases

The executable tests cover:

- exact documented `v0` message construction;
- changed body bytes, JSON whitespace, or timestamp text;
- missing, malformed, wrong-version, uppercase, or forged signatures;
- noncanonical, stale, and future timestamps, including exact boundaries;
- empty, non-byte, and oversized bodies;
- exact serial and concurrent replay;
- bounded replay capacity and expiry pruning;
- inactive and expired policies;
- invalid or backwards application clocks;
- receipt-identity mutation;
- public error and receipt non-disclosure;
- policy immutability and digest binding; and
- secret copy, serialization, and closed-state refusal.

## Deferred integration gate

No HTTP route, Zoom URL-validation response, event-name dispatch, RTMS start or
stop, OAuth, WebSocket handshake, raw-packet mapper, transport binding, or inbox
append is authorized by this slice.

The next provider-semantic change requires the untouched sanitized synthetic
fixture listed in [the meeting connector specification](MEETING_CONNECTOR_SPEC.md).
It must prove the exact signing representation and header extraction before the
network adapter may invoke this verifier. It must then separately prove event
shape, meeting and stream identity, ordering, duplicate behavior, transcript
semantics, reconnect behavior, and privacy classification before any raw packet
can become a provider-neutral `MeetingTranscriptEvent`.

The local-only [Zoom golden-fixture capture runbook](ZOOM_GOLDEN_FIXTURE_RUNBOOK.md)
now provides the preflight, fixed opaque inventory, immutable custody manifest,
independent re-verification, and privacy-review workflow for that gate. It does
not establish that a framework preserved Zoom's signing bytes and therefore
does not reduce this authenticator's deferred-integration requirements.
