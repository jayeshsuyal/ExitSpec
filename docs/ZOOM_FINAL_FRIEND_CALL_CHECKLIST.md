# Final friend-call operator checklist

Use this checklist only for one fresh, explicitly authorized friend call. The current private capture remains diagnostic and incomplete; it is not a golden fixture.

## Authorization and privacy gate

- [ ] The Zoom account owner has rotated or disabled the webhook secret that appeared in local inspection output. Never paste the replacement secret into chat, git, logs, tests, evidence, or screenshots.
- [ ] The owner has explicitly approved opening the private capture for a bounded privacy review, or the call will use a newly captured, consented meeting. Without that approval, do not inspect transcript bytes.
- [ ] Participants know the meeting is being used for the ExitSpec integration test and consent to the bounded capture purpose.
- [ ] The app/setup attestation is recorded separately from runtime evidence: CRC validation, scopes, endpoint configuration, and event subscription state are setup facts, not meeting proof.

## Pre-call safety checks

- [ ] Live-network gates are enabled only for this authorized run and are disabled again immediately afterward.
- [ ] The original webhook endpoint and only the approved event set are recorded for restoration.
- [ ] No temporary subscription, debug logger, raw packet dump, or transcript content logger is active.
- [ ] The private artifact directory is ignored, access-controlled, and treated as immutable diagnostic material.
- [ ] The operator has a content-free capture ID, start/stop timestamps, and a place to record outcome codes without copying transcript text.

## During and after the call

- [ ] Verify participant lifecycle, RTMS start/stop, interruption/reconnect, duplicate suppression, and finalization once using status and digest metadata only.
- [ ] If a required endpoint-validation artifact is absent, preserve the result as incomplete (`ZOOM_FIXTURE_CAPTURE_INCOMPLETE`). Do not decode, publish, or call it golden.
- [ ] Rotate/disable the temporary credential after the run if the provider workflow requires it; never expose the value.
- [ ] Restore the original endpoint and exactly the approved events; delete temporary subscriptions and verify the final state.
- [ ] Return live-network gates to false and record the content-free operator receipt.
- [ ] Only a fresh authorized call that reaches `/app` with one Zoom-sourced POC, human confirmation, freeze, deterministic proof, and Evidence Pack can support a real-loop completion claim.

## Stop conditions

Stop and preserve a fail-closed outcome for missing consent, malformed packets, missing endpoint validation, a reconnect failure, timeout, duplicate finalization, stale customer decision, provider/model failure, or any suspected secret leakage. Escalate the exact outcome code and required owner action without sharing raw content.
