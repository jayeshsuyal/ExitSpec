# Speech-to-text boundary

Status: five-slice synthetic-data demo implemented. The default browser path is
provider-free; an explicit server flag enables one real Fireworks prerecorded
transcription of a consenting operator's synthetic clip. A funded live smoke,
real customer audio, and meeting-platform integration are not yet proven.

## Decision

ExitSpec may add speech-to-text only through a provider-neutral, fail-closed
boundary that proves consent and policy match before raw audio can leave the
process. A transcript remains untrusted source material. It can feed the
existing proposal-review flow after redaction, but it can never approve a
proposal, confirm or freeze a contract, run a measurement, or assign a verdict.

This first slice is intentionally smaller than an STT integration:

```text
reviewed STT policy
        +
meeting consent attestation
        +
bounded audio metadata (digest, bytes, duration, media type)
        |
        v
fail-closed policy evaluation
        |
        v
safe authorization record
transport_capability_issued = false
```

No raw audio, provider SDK, network request, browser microphone, Zoom or Google
Meet connection, provider credential, or persisted transcript is added by this
first policy contract. The later browser-demo adapter is separately bounded
below and does not change this contract into a provider integration.

## Why this boundary comes first

Text can be redacted before it is sent to a model. Raw audio cannot. Once audio
crosses a provider boundary, later transcript redaction cannot undo that
disclosure. The authorization decision therefore binds the exact meeting,
participant consent, provider, model, region, data-policy snapshot, retention
mode, media type, byte count, duration, and time window before a future
transport can receive bytes.

The first policy is deliberately synthetic-only and zero-retention-only. Real
customer audio remains behind the C4 production gates in
[SECURITY.md](SECURITY.md).

## Authority map

| Object or component | May do | Must never do |
| --- | --- | --- |
| `STTPrivacyPolicy` | Pin one reviewed provider configuration and bounded audio policy | Carry credentials or authorize arbitrary provider choices |
| `MeetingConsentAttestation` | Bind one meeting, participant set, notice, attester, and time | Infer consent from attendance, silence, or a transcript |
| `AudioDescriptor` | Bind a digest, size, duration, media type, capture time, and meeting ID | Carry or serialize raw audio bytes |
| `authorize_stt_egress` | Return a safe policy-match record or one typed denial | Perform network I/O or issue a transport capability |
| `FireworksSTTTransport` | Consume one private request and call one pinned Fireworks endpoint | Accept caller-selected host/model/policy, redirect, retry, or log audio |
| `UntrustedSTTTranscript` | Hold normalized provider output request-locally for immediate redaction | Serialize, persist, approve, confirm, freeze, measure, or judge |
| `STTTranscriptReceipt` | Preserve content-free provenance after redaction | Contain audio, transcript text, participant IDs, or verified speaker identity |

Agreement, evidence, and business authorization remain independent:

```text
STT output
    -> redaction
    -> MEETING source
    -> NEEDS_REVIEW proposals
    -> named employee review
    -> canonical customer confirmation
    -> freeze
    -> measurement
    -> deterministic verdict
```

There is no shortcut from STT output to any later state.

## Frozen policy contract

`STTPrivacyPolicy` is server-owned and immutable. It pins:

- exact policy ID and version;
- exact provider, model, and processing region;
- an allowlist of canonical audio media types;
- maximum raw-audio bytes and duration;
- a maximum five-minute provider-transport timeout;
- zero-retention behavior;
- a SHA-256 binding to the reviewed provider data-policy snapshot;
- a SHA-256 binding to the exact recording notice;
- deletion and incident-response policy references;
- review and expiry timestamps, with at most a 90-day review window;
- a maximum five-minute authorization window; and
- `synthetic_only=true`.

Provider documentation changing does not silently mutate this policy. A new
review produces a new policy version and digest.

## Consent contract

Consent is explicit recorded state, not an inference. One
`MeetingConsentAttestation` includes:

- an opaque attestation ID;
- the exact meeting ID;
- the complete bounded participant-ID set;
- the exact set that consented;
- explicit recording-notice acknowledgement;
- the notice digest;
- the fixed `POC_REQUIREMENTS_TRANSCRIPTION` scope;
- `GRANTED` or `REVOKED` state;
- the named employee attester; and
- attested and optional revoked timestamps.

Authorization requires every listed participant to appear in the consented set.
Consent must precede capture. Revocation always blocks egress. A participant-set
change requires a new attestation; the boundary never guesses whether a new
participant consented.

## Evaluation order

The policy evaluator checks in a stable order:

1. typed policy, intent, and timezone-aware current time;
2. policy activation and expiry;
3. request freshness;
4. exact meeting identity across request, audio, and consent;
5. consent state, notice acknowledgement, and complete participant coverage;
6. recording-notice digest;
7. `policy review <= consent <= capture <= request <= now`;
8. exact provider, model, region, and retention mode;
9. media-type allowlist; and
10. byte and duration ceilings.

Failure produces no authorization record and has no state mutation.

## Typed denial matrix

| Failure code | Meaning | Next human action |
| --- | --- | --- |
| `STT_INVALID_REQUEST` | Boundary objects or current time are invalid | Correct request metadata |
| `STT_POLICY_NOT_ACTIVE` | Review window has not begun | Review the STT policy |
| `STT_POLICY_EXPIRED` | Provider policy review is stale | Refresh the STT policy |
| `STT_REQUEST_EXPIRED` | The intent exceeded the five-minute window | Start a new request |
| `STT_MEETING_IDENTITY_MISMATCH` | Audio, consent, and request name different meetings | Reconcile meeting identity |
| `STT_CONSENT_REQUIRED` | Recording notice was not acknowledged | Record participant consent |
| `STT_CONSENT_INCOMPLETE` | At least one listed participant is not consented | Resolve participant consent |
| `STT_CONSENT_REVOKED` | Consent is revoked | Stop audio processing |
| `STT_CONSENT_NOTICE_MISMATCH` | Consent binds a different notice | Record consent for the current notice |
| `STT_TIMELINE_INVALID` | Consent, capture, request, or current time is out of order | Restart capture after consent |
| `STT_PROVIDER_NOT_ALLOWED` | Provider differs from policy | Use the reviewed configuration |
| `STT_MODEL_NOT_ALLOWED` | Model differs from policy | Use the reviewed configuration |
| `STT_REGION_NOT_ALLOWED` | Region differs from policy | Use the reviewed configuration |
| `STT_RETENTION_NOT_ALLOWED` | Retention differs from zero-retention policy | Use zero retention |
| `STT_MEDIA_TYPE_NOT_ALLOWED` | Audio format is not allowlisted | Use a supported format |
| `STT_AUDIO_TOO_LARGE` | Byte ceiling is exceeded | Reduce audio size |
| `STT_AUDIO_TOO_LONG` | Duration ceiling is exceeded | Shorten the audio |

Every denial is content-free, non-automatically-retryable, and carries one
bounded next action. Provider-runtime failures such as authentication, quota,
rate limit, timeout, service unavailability, and malformed output belong to the
separate bounded operation layer; they are not invented by the policy evaluator.

## Safe records and private transcript material

The authorization record contains policy and provenance digests but omits
participant IDs and the raw meeting identity. It sets:

```text
authority = AUDIO_EGRESS_POLICY_MATCH_ONLY
transcript_authority = UNTRUSTED_SOURCE_ONLY
transport_capability_issued = false
synthetic_only = true
```

It is not a bearer token and cannot be consumed as a provider credential.

`UntrustedSTTTranscript` is a request-local private object. Its ordinary string
representation hides content, and standard dump, JSON, iteration, copy, and
pickle paths fail closed. The only explicit content path is
`transient_redaction_input()`, which is consumed by the immediate redaction
handoff.

Provider speaker labels are represented only as
`PROVIDER_ASSIGNED_UNVERIFIED`. ExitSpec does not claim that diarization proves
who spoke.

After redaction, `STTTranscriptHandoffService` publishes
`STTTranscriptReceipt`. That receipt links the operation, authorization, and
attached source; contains hashes, counts, provider configuration,
redaction-policy version, unverified speaker-mapping state, and `NEEDS_REVIEW`;
and contains no audio bytes, transcript text, participant IDs, provider speaker
labels, or raw meeting ID.

## Bounded synthetic audio operation

`stt_operation.py` implements the second slice without choosing or shipping a
provider. It accepts synthetic bytes only through `STTAudioPermitIssuer`, which:

1. re-evaluates the exact PR95 policy and consent intent at the server clock;
2. requires immutable `bytes` whose length and SHA-256 exactly match the approved
   `AudioDescriptor`;
3. records only the authorization ID, never the audio;
4. refuses a second permit for the same deterministic authorization;
5. fails closed at a bounded process-local issuance capacity; and
6. returns a private, non-serializable permit with the exact bytes.

`STTOperationExecutor` is disabled by default. When explicitly enabled with a
typed transport, it:

```text
private one-use audio permit
        |
        +-- disabled / invalid / expired / replayed -> no transport
        |
        v
consume permit and detach bytes from permit
        |
        v
one transport attempt; automatic_retries = 0
        |
        +-- typed provider failure -> no transcript, permit remains consumed
        |
        v
validate provider request ID, language, speaker mode, segment shape and timing
        |
        v
private UntrustedSTTTranscript + content-free STTOperationReceipt
```

The executor accepts neither raw bytes nor caller policy. It accepts only the
private permit. The request releases its audio reference immediately after the
transport returns or raises. Python cannot guarantee physical memory zeroing,
so this is bounded reference release, not a memory-forensics claim.

The operation makes at most one provider attempt. A timeout can occur after the
provider accepted audio, so an automatic retry could disclose the same audio
twice. Authentication, account, rate-limit, timeout, service, and transport
failures therefore consume the permit and require a new explicit request.

The provider-neutral operation seam remains fake-proven independently. The
separate `FireworksSTTTransport` below is the only current network adapter. No
successful funded external request or STT pricing claim is committed as
evidence yet.

### Operation failure matrix

| Failure code | Transport attempts | Permit state | Next action |
| --- | ---: | --- | --- |
| `STT_OPERATION_DISABLED` | 0 | Available until expiry | Enable an approved transport |
| `STT_PERMIT_INVALID` | 0 | No authority | Issue a new permit |
| `STT_PERMIT_EXPIRED` | 0 | Consumed | Issue a new permit |
| `STT_PERMIT_REPLAYED` | 0 | Already issued/consumed | Start a new request |
| `STT_PERMIT_CAPACITY_EXCEEDED` | 0 | Not issued | Restart the local runtime safely |
| `STT_AUDIO_BINDING_MISMATCH` | 0 | Not issued | Recapture and reauthorize |
| `STT_TRANSPORT_CONFIGURATION` | 1 | Consumed | Configure reviewed transport |
| `STT_PROVIDER_AUTHENTICATION` | 1 | Consumed | Check provider credential |
| `STT_PROVIDER_ACCOUNT_UNAVAILABLE` | 1 | Consumed | Restore provider account |
| `STT_PROVIDER_RATE_LIMITED` | 1 | Consumed | Start a new request later |
| `STT_PROVIDER_TIMEOUT` | 1 | Consumed | Review provider state first |
| `STT_PROVIDER_SERVICE_UNAVAILABLE` | 1 | Consumed | Start a new request later |
| `STT_PROVIDER_TRANSPORT` | 1 | Consumed | Check connectivity |
| `STT_PROVIDER_INVALID_RESPONSE` | 1 | Consumed | Review provider output |
| `STT_OPERATION_INTERNAL` | 0 or 1 | Fail closed | Review the operation |

`STTOperationReceipt` contains only authorization and provenance hashes, pinned
provider configuration, the requested zero-retention policy, bounded audio
metadata and timeout, segment count, elapsed time, one attempt, zero retries,
and `TRANSCRIBED_UNTRUSTED`. It records that ExitSpec persisted neither audio nor
transcript; it does not claim that a future provider honored retention. It
contains no provider request ID, meeting ID, participant ID, audio, or transcript
text.

## Experimental Fireworks prerecorded transport

`providers/fireworks_stt.py` implements the fifth, opt-in network slice. It is
not a general audio client. The server constructs it only when
`--enable-fireworks-stt` is present and `FIREWORKS_API_KEY` is valid. The
browser never receives the credential.

The adapter pins:

- `POST https://audio-prod.us-virginia-1.direct.fireworks.ai/v1/audio/transcriptions`;
- model `whisper-v3`, language `en`, `verbose_json`, word/segment timestamps,
  and diarization;
- region `us-virginia-1` and the reviewed zero-data-retention policy digest;
- one HTTPS attempt, no redirect, no automatic retry, and a 30-second timeout;
- a bounded multipart request and bounded JSON response; and
- strict segment ordering, timing, text, language, speaker-label, request-ID,
  and duplicate-key validation.

[Fireworks currently advertises streaming STT](https://docs.fireworks.ai/examples/cookbooks)
in its cookbook catalog, but its current documentation index does not expose
the old prerecorded API reference. The pinned request shape therefore follows
[Fireworks' archived official prerecorded-STT cookbook](https://github.com/fw-ai/cookbook/blob/main/archived/learn/audio/audio_prerecorded_speech_to_text/audio_prerecorded_speech_to_text.ipynb)
and remains labeled experimental until the funded smoke runbook succeeds.
Endpoint drift becomes a typed configuration failure; ExitSpec does not
silently switch hosts or models.

HTTP failures follow
[Fireworks' documented inference error classes](https://docs.fireworks.ai/guides/inference-error-codes)
and become content-free product outcomes. In particular, `401/403`
means credential failure, `402/412` means account or billing state, `429` means
rate limiting, `408/504` means timeout, and `500/502/503` means service
unavailability. A failed or ambiguous request consumes the one-use permit and
cannot resend the same audio automatically.

## Transcript-to-source handoff

`stt_handoff.py` implements the third synthetic slice:

```text
sealed STTOperationResult
        |
        +-- receipt/transcript mismatch -> typed refusal; no source write
        |
        v
neutralize unverified provider speaker labels
        |
        v
deterministic redaction and exact redacted-content digest
        |
        v
existing ProcessLocalPOCSourceIntake MEETING path
        |
        +-- exact replay -> same source and proposals
        +-- changed content under one operation -> conflict
        |
        v
linked content-free receipts + source-linked NEEDS_REVIEW proposals
```

The operation ID supplies the source identity and idempotency key. The source
boundary redacts again and must reproduce the exact expected SHA-256 before it
can attach content. Provider-assigned labels become stable `Speaker 1`,
`Speaker 2`, and so on; missing mapping becomes `Speaker unknown`. These labels
preserve dialogue shape without claiming participant identity.

The handoff can create neither a criterion nor any lifecycle transition. It
only attaches a process-local `MEETING` source and projects candidates for the
existing employee-review screen. No candidate can approve itself, confirm a
customer agreement, freeze a contract, run proof, or assign a verdict.

`raw_transcript_retained=false` describes ExitSpec source and durable state. The
request-local operation result still exists until its caller releases it, and
Python cannot guarantee physical memory zeroing. The handoff drops its own raw
text reference after redaction; it does not make a memory-forensics claim.

### Handoff failure matrix

| Failure code | Meaning | Source effect |
| --- | --- | --- |
| `STT_HANDOFF_INVALID_RESULT` | Input is not one sealed operation result | No write |
| `STT_HANDOFF_BINDING_MISMATCH` | Receipt and private transcript disagree | No write |
| `STT_HANDOFF_REDACTION_FAILED` | Redaction or exact source binding failed | No write |
| `STT_HANDOFF_SOURCE_UNAVAILABLE` | Draft POC cannot accept a source | No write |
| `STT_HANDOFF_SOURCE_CONFLICT` | One operation identity names changed content | Existing source unchanged |
| `STT_HANDOFF_CAPACITY_EXCEEDED` | Bounded process-local source state is full | No write |
| `STT_HANDOFF_INTERNAL` | Handoff could not complete safely | Fail closed |

## Browser-microphone demo

`stt_demo_runtime.py` and `stt_demo_web_api.py` implement the browser slice. The
default mode proves the product control loop without claiming speech-recognition
quality:

```text
fixed disclosure shown
        |
        v
three explicit acknowledgements recorded on the server
        |
        v
browser may request one local operator's microphone
        |
        v
WebM-signature clip: browser-declared 250 ms–8 s, at most 64 KiB
        |
        v
exact bytes + digest -> PR95 policy -> PR96 one-use operation
        |
        v
code-pinned synthetic transcript -> PR97 redaction and handoff
        |
        v
MEETING source -> NEEDS_REVIEW proposals -> existing product flow
```

The interface says `Not real STT` and displays the exact two fixed requirements
before consent:

1. P95 time to first token must stay below 500 ms.
2. Error rate must remain below 1%.

The spoken words cannot change that output. The adapter connects no provider,
and `spoken_words_transcribed=false` appears in both disclosure and completion
receipts. The browser path is an alternate input inside the existing Meeting
source card; Paste transcript remains available and both inputs converge on the
same proposal-review page.

With `--enable-fireworks-stt`, the same Meeting card changes visibly to
`Fireworks STT · experimental`. Before microphone permission, the operator must
acknowledge that the synthetic clip will be sent once to Fireworks. The fixed
output disappears; the provider transcript is immediately redacted and enters
the same `NEEDS_REVIEW` proposal queue. The completion receipt says
`spoken_words_transcribed=true`, `provider_connected=true`, and still says
`raw_audio_retained=false` and `raw_transcript_retained=false` for ExitSpec.
Known account, credential, rate, timeout, transport, service, and response
failures receive bounded recovery copy; raw provider bodies are never rendered.

The HTTP surface is deliberately narrow:

- `GET /api/pocs/{poc_id}/stt/disclosure` returns exact copy and bounds for one
  active draft;
- `POST /api/pocs/{poc_id}/stt/consents` records all acknowledgements and issues
  one two-minute process-local capture identity; and
- `POST /api/pocs/{poc_id}/stt/captures/{capture_id}` accepts one exact base64
  audio binding and returns only linked review receipts; and
- `GET /api/pocs/{poc_id}/stt/captures/{capture_id}` returns only a completed,
  content-free receipt so the browser can reconcile an interrupted response
  without resending audio.

Writes require strict JSON, exact same-origin requests, no query parameters, no
duplicate keys, and no `Idempotency-Key` header. Consent must succeed and its
response must pass exact browser validation before `getUserMedia` is called.
Capture is one attempt with no automatic audio retry. The server verifies the
EBML/WebM signature, exact bytes, digest, and declared byte count; duration is
explicitly labeled as measured and declared by the browser monotonic clock, not
derived by a server-side media decoder. Exact successful replay and content-free
receipt recovery return the same source; changed bytes conflict; expired,
failed, or consumed captures require fresh consent and a new recording.

ExitSpec stores neither the clip nor a raw transcript. Browser references and
server request-local references are released after success or failure. As with
the operation layer, Python and browser runtimes do not guarantee physical
memory zeroing, so this is a zero-persistence and bounded-reference claim, not
a memory-forensics claim.

## Five-slice delivery train

| PR | Decision | Still deliberately false |
| --- | --- | --- |
| 95 — boundary | Consent, policy, limits, provenance, private output, and typed denials are executable | No audio upload, provider call, or UI |
| 96 — bounded audio operation | Implemented on the current stack: exact synthetic bytes cross one private permit into one fake-proven transport attempt; execution is disabled by default | No real provider, product upload UI, automatic proposal approval, or meeting-platform bot |
| 97 — transcript-to-source handoff | Implemented on the current stack: valid synthetic output is immediately redacted and attached as a `MEETING` source with source-linked `NEEDS_REVIEW` proposals | No transcript-to-contract shortcut, provider, or product audio UI |
| 98 — live demo and hardening | Implemented on the current stack: browser microphone completes the fixed synthetic loop with visible consent, bounded process-local state, safe recovery, and Chromium regression evidence | No speech recognition, real provider, Zoom/Meet bot, customer audio, or production claim |
| 103 — Fireworks prerecorded transport | Implemented on the current stack: an explicit server flag wires one pinned, single-attempt Fireworks transcription of a consenting synthetic operator clip into the same review queue | No funded live-smoke success, customer audio, streaming STT, Zoom/Meet bot, or production claim |

Zoom or Google Meet is a later transport adapter. The first undeniable demo uses
the browser microphone because it proves the product loop without OAuth,
calendar, bot admission, webhook ordering, reconnect, or vendor review noise.

## PR95 exit gate

PR95 passes only when all of the following are true in automated tests:

1. one exact valid metadata intent returns a deterministic safe record;
2. every declared metadata denial returns its exact typed code and no content;
3. raw audio has no field in the public contract;
4. private transcript content cannot be serialized or copied;
5. transcript authority is fixed to `UNTRUSTED_SOURCE_ONLY` and review state to
   `NEEDS_REVIEW`;
6. speaker mapping remains explicitly unverified; and
7. the complete existing deterministic ExitSpec loop remains green.

## PR96 exit gate

PR96 passes only when all of the following are true in automated tests:

1. exact immutable bytes matching the approved digest and length can issue one
   private permit;
2. mismatched or mutable bytes cannot issue a permit;
3. one authorization cannot issue two permits and one permit cannot make two
   transport calls, including under concurrency;
4. the process-local issuance record is bounded, and disabled or expired
   operations make zero transport calls;
5. every declared provider failure is typed, content-free, single-attempt, and
   leaves no transcript or success receipt;
6. malformed provider output cannot become an `UntrustedSTTTranscript`;
7. success produces only a private review-only transcript and a content-free
   receipt with one attempt and zero automatic retries; and
8. the complete existing deterministic ExitSpec loop remains green.

## PR97 exit gate

PR97 passes only when all of the following are true in automated tests:

1. one sealed operation result becomes one redacted `MEETING` source;
2. operation, authorization, transcript, and source bindings match exactly;
3. raw transcript values and provider speaker labels never enter source or
   public receipt state;
4. every derived proposal is source-linked and remains `NEEDS_REVIEW`;
5. exact serial and concurrent replay creates no duplicate source or proposal;
6. changed content under one operation identity fails closed;
7. redaction, draft, conflict, capacity, and internal failures are typed and
   content-free; and
8. the complete existing deterministic ExitSpec loop remains green.

## PR98 exit gate

PR98 passes only when all of the following are true in automated tests:

1. the exact disclosure and fixed synthetic output are visible before consent;
2. every acknowledgement is required and the server records consent before the
   browser asks for microphone permission;
3. only one local operator, an EBML/WebM signature, browser-declared 250 ms–8
   seconds, and at most 64 KiB are accepted;
4. audio bytes and transcript text appear in no public receipt or persistent
   browser state;
5. spoken audio cannot change the fixed synthetic proposal output;
6. exact replay creates no duplicate source, while changed, expired, malformed,
   oversized, and consumed attempts fail closed without automatic audio retry;
7. successful capture lands in the existing `MEETING` source review screen with
   source-linked `NEEDS_REVIEW` proposals;
8. Chromium proves consent precedes microphone access, denial leaves Paste
   transcript available, upload failure requires a fresh recording, and media
   tracks stop even when the browser omits the final `stop` event; and
9. the original email-to-Evidence-Pack browser lifecycle and complete Python
   regression suite remain green.

## PR103 exit gate

PR103 passes only when all of the following are true:

1. Fireworks STT is disabled by default and an invalid or missing credential
   leaves Paste transcript and fixed synthetic recording available;
2. the exact host, path, model, region, retention snapshot, multipart fields,
   timeout, and zero-retry policy are pinned and fake-proven;
3. live-mode disclosure and provider-processing acknowledgement succeed before
   browser microphone permission;
4. one provider transcript becomes only redacted, source-linked
   `NEEDS_REVIEW` proposals;
5. every documented provider failure is typed, single-attempt, content-free,
   and shown with safe recovery guidance;
6. neither credentials, audio, raw transcript, provider body, nor speaker labels
   enter public receipts or browser persistence;
7. synthetic and provider-backed modes both complete through the real HTTP
   surface in automated tests; and
8. the complete existing ExitSpec regression gate remains green.

## Gates before real customer audio

The contract is necessary but not sufficient for C4 real-customer processing.
Before that claim, ExitSpec still requires:

- approved provider terms and a dated data-policy snapshot;
- authenticated tenant, employee, and participant identity;
- jurisdiction-specific recording-consent review;
- durable consent, revocation, deletion, and audit records;
- encrypted transport and storage with access policy;
- verified zero-retention or an approved bounded retention lifecycle;
- deletion and backup behavior tested against the selected provider;
- incident detection, response, and customer notification paths;
- speaker-identity limitations communicated to the reviewer; and
- a second-human privacy/security approval and operational rollback.

Until those gates pass, all STT input must remain synthetic demo data. The
opt-in Fireworks mode performs real external processing, but it is not evidence
that ExitSpec is approved for customer meetings or production audio.
