# Zoom RTMS guided `/app` handoff

Status: local conformance path, version `exitspec.zoom-guided-handoff/1.0`.

This is the employee-facing handoff for an already-created Meeting POC. It is
deliberately additive: the established provider-neutral Meeting session,
pasted transcript, browser recording, email intake, review, customer draft,
freeze, proof, and Evidence Pack contracts remain unchanged.

## User sequence

The Meeting source page adds one explicit mode: **Zoom RTMS handoff**.

1. The employee reviews the local disclosure and authorizes the handoff.
2. ExitSpec shows **Listening** while a fixed, server-owned synthetic RTMS
   packet set passes through the pinned decoder and state machine.
3. **Stop meeting** moves the session to **Processing**. Processing attaches
   one Zoom source through `ProcessLocalPOCSourceIntake` and the existing
   proposal-review spine.
4. **Draft ready** exposes **Open draft**. The resulting proposals are
   `NEEDS_REVIEW`; no customer confirmation, contract freeze, proof run, or
   verdict is issued.

The local mode does not open a Zoom connection. It must not be described as a
fresh live Zoom proof. A new live call is required before making that claim.

## HTTP contract

The existing same-origin JSON and closed-POC mutation gates apply.

- `GET /api/pocs/{poc_id}/zoom-handoff-disclosure`
- `GET /api/pocs/{poc_id}/zoom-handoff`
- `POST /api/pocs/{poc_id}/zoom-handoff`

The POST body is one of:

```json
{"action":"start","consent_acknowledged":true,"idempotency_key":"..."}
{"action":"stop","idempotency_key":"..."}
{"action":"process","idempotency_key":"..."}
```

Unknown fields, missing fields, query strings, alternate origins, and
idempotency headers are rejected. POST responses contain only a bounded
`handoff` snapshot and replay metadata. They never contain transcript text,
provider user IDs, meeting IDs, URLs from Zoom, or credentials.

## Compatibility and rollback

The old `/meeting-sessions` API and its `SETUP/READY/LIVE/DRAFT_READY`
projection are not reinterpreted. Existing saved pages and tests continue to
use that contract. The new route is safe to roll back by removing the new
mode and route; existing Meeting intake remains available through paste and
the provider-neutral synthetic session. A process restart clears the local
handoff runtime, consistent with the repository's process-local demo
semantics; it does not claim recovery of a live Zoom capture.

## Acceptance criteria

- A Meeting POC can authorize the local handoff and see the three statuses in
  order at 1280×720 without broad page scrolling.
- One processing replay produces one source and one review draft.
- The browser projection is source-attributed, bounded, and review-only.
- Existing Meeting session and non-Meeting intake browser contracts remain
  green.
