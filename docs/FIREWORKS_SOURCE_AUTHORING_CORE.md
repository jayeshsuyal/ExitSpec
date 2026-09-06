# Source-bound Fireworks authoring: synthetic core checkpoint

This checkpoint implements the accepted r2 core contract against main
`102b521e2f9a8b9c04d52c5fc77d06abe76aa006`. It is a synthetic-only library and
worker composition. No HTTP endpoint, browser capability bootstrap, UI action,
operator launch grant, credential configuration or live provider execution is
wired. Both real activation entry points reject unconditionally. Synthetic
token declarations and response fixtures cannot enable networking.

The future user action remains: explicitly select current redacted text,
acknowledge its source-specific disclosure, then request draft proposals for
human review. Zoom already supplies text; there is no STT stage. Capture
consent is separate from source-authoring consent. All successful proposals
enter the existing review service as `NEEDS_REVIEW`; they confer no confirmation,
evidence, verdict, closure or deployment authority.

## Serialization disposition

The coordinator's serializer correction is applied without changing frozen
Wave-1 behavior. Two encodings have distinct purposes:

| Value | Normative encoding and hash |
|---|---|
| Schema pin | SHA256 of sorted compact `json.dumps(..., ensure_ascii=False, allow_nan=False)` UTF-8 |
| System pin | SHA256 of exact system-message UTF-8 bytes |
| Request-template/profile pin | SHA256 of `b'exitspec-source-authoring-request-profile-v1\0'` followed by the same sorted compact JSON encoding of `request_profile` |
| Exact outbound body | Existing `canonical_json_bytes` (RFC 8785); SHA256 of `b'exitspec-source-authoring-body-v1\0'` followed by those bytes |
| Policy and intent | RFC 8785, with domains `exitspec-source-authoring-policy-v1\0` and `exitspec-source-authoring-intent-v1\0` respectively |

The template/profile pin remains
`1ddd5c2f16ca9a4d44802b0c9491488684546d5096233b320451c9d3306c8ef6`.
Schema and system pins remain
`289330f67c67d1cb07e367858c72d6c67ec29f4750db17a4ff5cf2e32af9ff99`
and `5f113942ed99dbc8ca51adc4f65772fc1eb959007ae387738f902d466a8a4696`.
Template bytes are not claimed to equal HTTP bytes: RFC 8785 normalizes numeric
values such as `0.0`. Tests compare the exact body against bytes passed to an
injected fake HTTPS connection, including numeric schema values, Unicode,
escaping and all explicit request fields.

PR2 pins `reasoning_effort="none"`, `n=1`, `service_tier="default"` and
`context_length_exceeded_behavior="error"`. Its only candidate endpoint/model
pair is global `api.fireworks.ai/inference/v1/chat/completions` with
`accounts/fireworks/models/deepseek-v4-flash-0731`. These pins are not approval
to spend or evidence of exact-model/schema compatibility. The frozen Wave-1
manifest, fixture-only permits, original serializer and retry policy remain
unchanged.

## Core ownership and dispatch

The operation owner privately issues identity-checked session and request
handles. Public IDs, digests, copied handles and handles from another issuer
cannot authorize a request. It stores content-free terminal receipts and keeps
consumed attempts after failure, expiry or ambiguous delivery. Source text/body
references are cleared from terminal operation records; successful A3 proposals
retain their existing custody behavior.

A synthetic runtime owns one ledger across all POCs and sessions: one worker
slot, ten claims, USD 0.01 per claim, USD 0.10 total reserved, and ten seconds
between claims. Reservations are never refunded. Capacity is 16 sessions,
1,024 operation records and 16,384 aliases. Exhaustion refuses new work without
evicting replay records. Shutdown revokes its handles; no reset/resume API or
live grant factory is provided.

Claim consumes a permit and reserves the worker slot. A prepared worker is
`READY_NO_SEND`. At **D**, current owner/consent/epoch/deadline bindings are
rechecked and the operation plus one private ticket become dispatch-authorized.
Before D, winning invalidation means zero handoff/send. After D, delivery is
conservatively possible; cancellation is best effort and prevents publication.
Handoff, subprocess startup/reaping and IPC occur outside owner locks.

The complete nested order is source → draft → review → assisted publication →
operation → supervisor, surrounded by a short closure mutation reservation.
The closure lock itself is not held through the transaction or worker lifetime.
Final **F** prepares all fallible proposal/registration/receipt/map work, then
publishes prepared review/A3 pointers and the operation success map while their
locks remain held. No callbacks or IPC occur after the first published pointer.
Publication reuses A3's materialization and owner guards rather than duplicating
the service. A prior A3 source result/in-flight authoring conflicts fail closed.

## Worker and wire bounds

The fixed subprocess command uses the installed interpreter with
`-I -m exitspec.source_authoring_worker`, an empty environment, a fixed installed
working directory and private inherited stdin/stdout pipes. This checkpoint's
worker contains no network implementation and accepts only the explicit
`SYNTHETIC_NO_NETWORK` protocol. It receives synthetic batch fixtures, not a
live Chat Completions response or credential.

IPC frames contain length-prefixed RFC 8785 metadata plus detached raw payload
bytes, avoiding JSON/base64 expansion of body data. The request/result limits
of 81,920/278,528 apply to actual wire bytes. Raw body/response caps remain
65,536/262,144. Epoch, grant, operation, worker generation, nonce and body/profile
digests must match. Partial, extra, malformed and oversized frames fail closed.
Only one prepared ticket can be handed off. A one-second handoff bound sits
inside the total 30-second deadline. Cleanup retains the slot until exit is
observed; an unobserved exit disables further operations. Tests simulate DNS,
connect, headers, body stalls and slow trickles without DNS or network traffic.

## Review boundary and remaining work

The early checkpoint covers strict policy/intent values, issuer-owned permits,
ledger, D/F ownership, synthetic worker/IPC and deterministic verification.
It does not implement browser/product wiring. Later bootstrap issuance is
itself a mutation: exact same-origin/Host validation must run before minting any
capability or consuming session capacity; missing, foreign, null and duplicate
Origin values must fail. Capabilities must remain out of URLs, logs, receipts
and persistent browser storage. Loopback restrictions do not authenticate a
user; the installed single-user application and local processes remain trusted.

Real activation remains blocked pending owner-approved tokenizer/template and
total billable-token proof, exact-model/full-schema acceptance, account/rate and
logging conditions, current pricing, custody/region approval, credentials and
an explicit launch budget. The global candidate has no regional guarantee;
a US requirement blocks it rather than widening the allowlist. A reservation
is not proof of token or invoice bounds. Provider schema acceptance never
weakens local schema, numeric or exact-source-anchor validation.

The coordinator must accept this exact core candidate before browser or
launcher integration. Full engineering/release/qualification gates and mandatory
browser coverage remain required for the later integrated candidate before merge.
