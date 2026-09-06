# Native Zoom transcript adapter — staged contract

This adapter is a parsing component for the bounded local live integration.
It is not wired into `/app` yet and does not establish live readiness. It opens
no network connection, records no packets, and cannot authorize capture or
provider egress. Existing fixture decoder and digest semantics are unchanged.

## Provider mapping and evidence

Checked 2026-09-06 against the official
[Meetings media guide](https://developers.zoom.us/docs/rtms/meetings/media/#transcripts)
and [event reference](https://developers.zoom.us/docs/rtms/event-reference/#transcript-data).
The supported native envelope is `msg_type: 17` and a `content` object. It carries
integer `user_id`, string `user_name`, integer `start_time`, `end_time`,
`timestamp`, integer `language`, and UTF-8 string `data`. The participant identity
belongs to the authenticated stream; the packet itself has no meeting or stream ID.
No documented transcript sequence, revision or finality flag is assumed.

The references describe Unix timestamps but do not unambiguously specify the raw
transcript unit. The
[pinned official sample writer](https://github.com/zoom/rtms-samples/blob/d387004a55dd364252d3993ab51f3e87577cb4a4/transcript/save_transcript_js/writeTranscriptToVtt.js)
uses magnitude heuristics and substitutes missing metadata. Those are sample
choices, not provider guarantees. The adapter preserves native integers, marks
units `UNSPECIFIED_UNIX`, and makes no duration or wall-clock inference. It keeps
the integer language code without manufacturing a BCP-47 tag.

This first version supports the complete documented Meetings example shape.
It rejects missing and extra fields; this is a local subset policy, not a claim
that Zoom guarantees all fields. Other shapes require a reviewed adapter update.
It rejects unknown participants, ambiguous pseudonyms, non-integer numeric fields,
invalid Unicode, control text, duplicate JSON keys, and reversed start/end values.
Local bounds: 64 KiB packet, 8,192 text characters, two participants, 256 arrivals,
32-bit participant IDs and interoperable unsigned JSON integers for native times.
Text is NFC/whitespace normalized; names and provider IDs are discarded.

## Distinct source binding

`ZoomNativeStreamBinding` binds process generation, session, POC, consent receipt,
keyed meeting/stream identity digests, code revision, and the permitted synthetic
requirements content classification. It is metadata, **not authentication**.
The future runtime must create these values after authenticating pairing and the
provider connection. It must never accept a browser-created binding as authority.

The normalized digest includes the binding, original raw-packet SHA-256, native
facts, pseudonym and normalized text. It excludes local arrival index so exact
redelivery within one binding has a stable identity. Different session, consent,
stream, POC, process generation or code revision changes that identity. These
digests prove neither capture authenticity nor measurement validity.

The returned object is private, unredacted source material. Its repr is redacted,
and decoder refusals retain no parser/validation exception chain. It is not a
browser receipt; the runtime must use content-free projections. Attachment must
still pass the common source redaction and atomic closure/attachment guards.

## Runtime integration still required

The intended bootstrap uses operator-local authority unavailable to web pages.
No unauthenticated HTTP endpoint may mint a capability or bind a POC/stream.
Pairing must bind an explicitly consented active POC/session to an authenticated
operator and stream, with expiry and invalidation on reset, closure, revocation,
or replacement. Credentials and capabilities must not enter URLs, command
arguments, logs, exceptions, browser receipts or evidence.

Future transport integration must separate normal memory-only operation from
explicit diagnostic recording/chaos. It must enforce Host/Origin defenses plus
operator authentication, aggregate byte/text/event/retry/idempotency bounds,
replay suppression and authenticated participant mapping. No network call may
hold shared workflow locks.

Stop request, provider acknowledgement, local drain and finalization need separate
states. A transcript packet is not a finality signal; cross-socket drain ordering
is not specified by the retrieved reference. Timeouts, overflow and incomplete
drain must fail explicitly. Late/stale events must not attach to a replacement
session. The existing synthetic UI remains synthetic until that integration and
its adversarial/browser coverage are complete.
