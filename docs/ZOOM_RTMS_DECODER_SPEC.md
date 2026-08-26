# Zoom RTMS transcript decoder

Status: implemented as a bounded, provider-adapter decoder for the pinned
synthetic transcript subset; no raw private capture is a decoder fixture

This decoder is deliberately smaller than a general Zoom parser. It accepts
only the transcript envelope pinned below. The current repository vectors are
synthetic test vectors, not a claim that the incomplete private diagnostic
capture is golden and not a claim about any unreviewed provider payload.

The privacy gate comes first. A private capture may be opened only under the
explicit consent and custody receipt defined in
[`ZOOM_PRIVACY_FIXTURE_PIPELINE_SPEC.md`](ZOOM_PRIVACY_FIXTURE_PIPELINE_SPEC.md).
The decoder itself has no raw-capture reader. A production or live adapter must
provide a digest-only provenance record and an explicit provider-ID to
pseudonym map owned by the server.

## Supported envelope

The exact JSON object contains these keys and no others:

```json
{
  "schema_version": "exitspec.zoom-rtms-transcript-packet.v1",
  "media_type": 8,
  "message_type": 17,
  "user_id": "provider-owned value",
  "start_time": "2026-08-25T19:00:00.000Z",
  "end_time": "2026-08-25T19:00:01.000Z",
  "timestamp": 1787684400000,
  "language": "en-US",
  "data": "transcript text"
}
```

The version, media type, and message type are exact. The provider user ID is
used only for a server-owned lookup and never appears in the normalized
segment. `data` must be a string; objects, arrays, partial records, and
undocumented fields are rejected. The decoder does not infer finality,
speaker identity, missing timestamps, or an alternative schema.

## Limits and refusal behavior

| Boundary | Limit |
| --- | --- |
| One packet | 64 KiB |
| Stream | 1 MiB, at most 256 newline-delimited packets |
| JSON nesting | 8 levels |
| Transcript text | 8,192 characters |
| Language tag | 35 characters, bounded BCP-47-shaped subset |
| Segment duration | 10 minutes |
| Timestamp | non-negative integer milliseconds through 2100-01-01T00:00:00Z |

UTF-8 must be valid. Duplicate object keys, non-finite JSON constants,
malformed JSON, blank stream records, exact duplicate packet digests, and
missing provenance fail closed. Timestamps must be UTC RFC 3339 strings ending
in `Z`; no timezone guessing or silent coercion is performed. Text is NFC
normalized and bounded whitespace is compacted, but it remains untrusted
source text.

Refusal errors expose only stable content-free codes such as
`ZOOM_DECODER_DUPLICATE_KEY` and `ZOOM_DECODER_INVALID_TIMESTAMP`. They never
echo packet bytes, transcript text, provider IDs, or exception details.

## Normalized output

`ZoomNormalizedTranscriptSegment` contains:

- a digest-derived segment ID and packet digest;
- arrival position, provider timestamp, start/end/duration milliseconds, and
  fixed ordering metadata;
- a server-assigned `SPEAKER_1`, `SPEAKER_2`, or `SPEAKER_UNKNOWN` pseudonym;
- the normalized transcript text and language; and
- digest-only provenance for the reviewed fixture, capture plan, setup
  attestation, runtime plan, and packet.

Every segment is marked `UNTRUSTED_SOURCE_ONLY` and `NEEDS_REVIEW`. Its
authority flags are all false: it cannot confirm a contract, freeze a
contract, start measurement, or assign a verdict. Later proposal code must
preserve those facts while attaching the segment to the existing source and
review pipeline.

## Compatibility and migration

`exitspec.zoom-rtms-decoder/1.0` and
`exitspec.zoom-rtms-transcript-packet.v1` are versioned independently. A future
wire version requires a new explicit decoder branch and vectors. Unknown
versions are rejected; they are not migrated by field-name matching. The
decoder does not reinterpret legacy incomplete captures, and the PR 1
setup/runtime evidence compatibility rules remain in force.

The stream decoder is a local bounded helper for the upcoming session bridge.
It does not add a network transport, durable multi-tenant runtime, or direct
proposal/contract authority. The current incomplete private capture remains
private, unsealed, and outside these tests.

## Verification

The vectors in
[`test_zoom_rtms_decoder.py`](../tests/test_zoom_rtms_decoder.py) cover:

- normalization, pseudonymization, timing, provenance, and authority flags;
- malformed UTF-8/JSON, duplicate keys, unsupported versions and types, extra
  fields, ambiguous payload shapes, and invalid text;
- packet, stream, depth, record, timestamp, and segment-duration limits; and
- duplicate delivery, missing provenance, blank records, identity gaps, and
  arrival/provenance binding.
