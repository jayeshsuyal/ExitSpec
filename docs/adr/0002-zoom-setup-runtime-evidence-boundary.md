# ADR 0002: Separate Zoom setup attestation from runtime evidence

- Status: Accepted for the local synthetic train
- Date: 2026-08-25
- Scope: Zoom RTMS private custody and future decoder input

## Decision

One-time Zoom app/setup facts and per-meeting runtime observations use
different versioned contracts. Setup attestation covers app and endpoint
configuration, the reviewed scope set, CRC/endpoint validation, and a
content-free external credential-rotation receipt. Runtime evidence covers
only one meeting's lifecycle, handshakes, participant events, transcript
packets, reconnect/duplicate traces, and timestamps.

Neither contract parses raw packets or grants network, mapper, source,
agreement, measurement, evidence, or verdict authority.

## Compatibility

`exitspec.zoom-golden-capture-plan.v1` remains the legacy twelve-role private
custody format. It is not rewritten or migrated in place. An incomplete v1
capture remains incomplete, including when endpoint-validation roles are
missing. New work binds `exitspec.zoom-rtms-runtime-evidence.v1` to
`exitspec.zoom-rtms-setup-attestation.v1` by exact ID and SHA-256.

## Security consequences

The exposed Zoom credential must be rotated or disabled in the provider portal
before another live run. Repository state contains only an operator receipt ID,
never the credential. A missing rotation receipt or missing CRC validation
blocks setup attestation and therefore blocks runtime capture.

## Rollback

Rollback is a code rollback, not an edit to private custody state. Preserve old
plans, receipts, manifests, and raw bytes; use a fresh capture ID after the
boundary is restored.
