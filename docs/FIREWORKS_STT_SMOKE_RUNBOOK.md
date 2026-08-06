# Fireworks STT smoke runbook

Status: prepared, fake-proven, funded live success not yet recorded

## What this run proves

One consenting operator can record one short synthetic clip in ExitSpec. The
server sends that exact clip once to the pinned Fireworks Whisper v3 endpoint,
accepts only a bounded transcript response, redacts it, and creates review-only
meeting proposals.

It does not prove Zoom or Google Meet ingestion, customer-audio approval,
speaker identity, transcript accuracy at scale, durable storage, or production
readiness.

## Frozen smoke boundary

| Control | Value |
| --- | --- |
| Input | One consenting operator speaking synthetic requirements |
| Duration | Browser-declared 250 ms–8 s |
| Size | At most 64 KiB |
| Media | `audio/webm` with EBML signature |
| Provider | Fireworks |
| Endpoint | `audio-prod.us-virginia-1.direct.fireworks.ai/v1/audio/transcriptions` |
| Model | `whisper-v3` |
| Region | `us-virginia-1` |
| Retention request | Fireworks zero-data-retention policy snapshot checked 2026-08-05 |
| Attempts | Exactly one; no redirect and no automatic audio retry |
| ExitSpec persistence | No raw audio or raw provider transcript |
| Output authority | `MEETING` source proposals in `NEEDS_REVIEW` |

[The current Fireworks docs catalog](https://docs.fireworks.ai/examples/cookbooks)
advertises production-ready streaming STT, but the current docs index does not
list the prerecorded API reference. This adapter follows
[Fireworks' archived official prerecorded-STT cookbook](https://github.com/fw-ai/cookbook/blob/main/archived/learn/audio/audio_prerecorded_speech_to_text/audio_prerecorded_speech_to_text.ipynb).
Treat a
404, 405, 413, 415, or 422 as configuration drift and stop; do not improvise a
new host, model, or request shape during the smoke.

## Before starting

1. Use a fresh, dedicated Fireworks key with only the access needed for this
   smoke. Never put it in source, command arguments, screenshots, chat, or a
   recording.
2. Confirm the account has a payment method or usable balance.
   [Fireworks documents](https://docs.fireworks.ai/guides/inference-error-codes)
   `402` as payment/usage-limit failure and `412` as account-status failure.
3. Speak synthetic requirements only. Do not use customer names, meeting audio,
   secrets, personal data, or copied production content.
4. Review the current
   [Fireworks zero-data-retention page](https://docs.fireworks.ai/guides/security_compliance/data_handling)
   and confirm there has been no policy change since the pinned snapshot.
5. Start from a clean local ExitSpec process and current tested revision.

## Start the product

Place `FIREWORKS_API_KEY` in the server environment using the local shell or
secret manager, then run:

```bash
exitspec serve --enable-fireworks-stt --open-browser
```

Expected terminal copy includes:

```text
Experimental Fireworks STT enabled for consented synthetic browser clips.
```

If the terminal instead says `requested but not configured`, stop and repair
the server-owned credential. The product intentionally falls back to Paste
transcript and the fixed synthetic recording mode.

## Execute one smoke

1. Create a POC whose first source is **Meeting**, or open an active local draft.
2. Choose **Meeting → Record with Fireworks STT**.
3. Confirm the card visibly says:
   - `Fireworks STT · experimental`;
   - provider `Fireworks`, model `whisper-v3`, region `us-virginia-1` in the
     disclosure; and
   - audio and raw transcript are not persisted by ExitSpec.
4. Check all three acknowledgements. Microphone permission must remain disabled
   until the server accepts them.
5. Record this synthetic phrase in under eight seconds:

   ```text
   P95 time to first token must stay below 650 milliseconds.
   Error rate must remain below two percent.
   ```

6. Stop, then choose **Create review proposals** exactly once.
7. Verify the app opens the proposal-review screen and the extracted claims
   reflect the spoken thresholds. Every proposal must still say
   `NEEDS_REVIEW`; nothing may be approved, confirmed, frozen, measured, or
   passed automatically.

## Pass conditions

The smoke passes only if all are true:

- the spoken content, not the fixed synthetic fixture, appears as proposals;
- exactly one provider attempt occurred and no browser audio resend occurred;
- the resulting source kind is `MEETING` and all proposals are `NEEDS_REVIEW`;
- no key, authorization header, audio bytes, raw transcript, provider body, or
  provider speaker label appears in browser-visible receipts or logs;
- the normal human review → customer confirmation → freeze → proof → Evidence
  Pack flow remains unchanged; and
- the account dashboard shows only the expected single bounded transcription
  attempt.

Record the revision, UTC timestamp, terminal mode line, visible provider/model/
region, result state, and Fireworks dashboard reconciliation. Do not record the
key, raw request, audio, or raw response.

## Safe failure matrix

| Product code | Meaning | Next action |
| --- | --- | --- |
| `STT_PROVIDER_CONFIGURATION` | Request shape, endpoint, or server setup is not accepted | Stop; review the pinned integration |
| `STT_PROVIDER_AUTHENTICATION` | Credential rejected | Replace the dedicated key |
| `STT_PROVIDER_ACCOUNT_UNAVAILABLE` | Payment, balance, suspension, or account state | Restore the account before a fresh recording |
| `STT_PROVIDER_RATE_LIMITED` | Provider capacity or account rate limit | Wait, then create a fresh recording |
| `STT_PROVIDER_TIMEOUT` | One attempt timed out ambiguously | Check provider state; never resend the same audio |
| `STT_PROVIDER_SERVICE_UNAVAILABLE` | Fireworks service failure | Use Paste transcript or retry later with fresh consent |
| `STT_PROVIDER_TRANSPORT` | TLS/network/unknown HTTP failure | Check connectivity and provider status |
| `STT_PROVIDER_INVALID_RESPONSE` | Response body failed strict validation | Stop and inspect the adapter with synthetic data |

Every failed provider attempt consumes its one-use capture. ExitSpec clears its
audio references and requires fresh consent plus a new clip; it never retries
the same audio automatically.

## Rollback

Stop the server, remove `FIREWORKS_API_KEY` from the process environment, revoke
the dedicated smoke key, and restart without `--enable-fireworks-stt`. Paste
transcript and the fixed synthetic recording path remain available.
