# Speech-to-text boundary

Status: contract implemented; audio transport and transcription not implemented

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
contract.

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
| Future STT transport | Consume a separately designed one-use private capability | Accept caller-selected provider policy or log audio |
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
transport PR; they are not invented by this contract-only slice.

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
`transient_redaction_input()`, which exists for the future immediate redaction
handoff.

Provider speaker labels are represented only as
`PROVIDER_ASSIGNED_UNVERIFIED`. ExitSpec does not claim that diarization proves
who spoke.

After redaction, a future adapter may publish `STTTranscriptReceipt`. That
receipt contains hashes, counts, provider configuration, redaction-policy
version, unverified speaker-mapping state, and `NEEDS_REVIEW`. It contains no
audio bytes, transcript text, participant IDs, or raw meeting ID.

## Four-PR delivery train

| PR | Decision | Still deliberately false |
| --- | --- | --- |
| 95 — boundary | Consent, policy, limits, provenance, private output, and typed denials are executable | No audio upload, provider call, or UI |
| 96 — bounded audio operation | One synthetic upload/capture can cross one permit-only adapter with fake-transport proof and disabled-by-default real configuration | No automatic proposal approval or meeting-platform bot |
| 97 — transcript-to-source handoff | Valid provider output is immediately redacted and attached as a `MEETING` source with source-linked `NEEDS_REVIEW` proposals | No transcript-to-contract shortcut |
| 98 — live demo and hardening | Browser microphone completes the synthetic demo loop with visible consent, bounded state, recovery, and full regression evidence | No Zoom/Meet bot, real customer audio, or production claim |

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

Until those gates pass, all STT demonstrations remain local and synthetic.
