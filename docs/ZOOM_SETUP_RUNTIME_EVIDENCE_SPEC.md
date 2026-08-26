# Zoom setup attestation and runtime evidence boundary

Status: PR 1 design and executable contract

ExitSpec has two different kinds of Zoom evidence. A one-time app/setup
attestation describes the configured General App and endpoint validation. A
per-meeting runtime plan describes only the events observed during one
authorized synthetic meeting. Neither contract is a Zoom wire-schema claim or
an ExitSpec product decision.

```text
one-time setup attestation
  app configuration + endpoint configuration + CRC validation + reviewed scopes
  credential rotation receipt (content-free, external to the repository)
          |
          v
per-meeting runtime evidence plan
  RTMS lifecycle + WebSocket handshakes + participant events + transcript bytes
  reconnect/duplicate traces + timestamps
          |
          v
later privacy gate -> sanitized fixture candidate -> decoder review
```

## Contracts

`exitspec.zoom-rtms-setup-attestation.v1` has no meeting ID, stream ID,
participant ID, URL, secret, token, or transcript. It requires validated app and
endpoint configuration, the reviewed RTMS scope set, a validated CRC/endpoint
challenge, and a content-free receipt that the exposed credential was rotated
or disabled outside the repository. The three setup artifact digests are
private custody metadata only.

`exitspec.zoom-rtms-runtime-evidence.v1` references the setup attestation by
ID and SHA-256, but contains only the nine per-meeting runtime roles. Setup
roles cannot be placed in its inventory. The contract grants no authority to
call Zoom, publish a fixture, define a mapper, create a source, confirm or
freeze a contract, run measurement, or assign a verdict.

The existing `exitspec.zoom-golden-capture-plan.v1` remains a legacy private
custody contract with its original twelve-role inventory. It is classified as
`LEGACY_V1_PRIVATE_CUSTODY_ONLY`; it is not silently upgraded. In particular,
an old capture missing endpoint-validation files remains
`ZOOM_FIXTURE_CAPTURE_INCOMPLETE`.

## Credential exposure response

Before another live run, the operator must perform this procedure without
copying the old or replacement value into a shell transcript, issue, PR, log,
fixture, test, or evidence receipt:

1. Stop the operator harness and leave `ALLOW_REAL_ZOOM_NETWORK=false`,
   `RTMS_CREDITS_CONFIRMED=false`, `SYNTHETIC_CAPTURE_AUTHORIZED=false`, and
   `ZOOM_CREDENTIAL_ROTATION_ATTESTED=false`.
2. In the Zoom Developer Portal, disable or rotate the exposed webhook secret
   and revoke any access/refresh credential that was present in the affected
   local environment. Do not paste either value into Codex or the repository.
3. Store the replacement only in the approved local secret store or protected
   environment file, with owner-only permissions. The repository receives only
   a content-free operator receipt ID and the status
   `ROTATED_OR_DISABLED_OUTSIDE_REPO`.
4. Restart the operator process from a fresh shell. Check variable names and
   file permissions only; never print environment values. Confirm the old
   process is gone and the old credential is no longer accepted through the
   provider's own portal/audit surface.
5. Run a repository secret scan and inspect `git status --short`. If any value,
   provider response, or private capture appears, stop and keep the run
   private. A successful local scan does not replace provider-side rotation.
6. Only after the operator has recorded the external rotation receipt may a
   new setup attestation be created. A missing or pending receipt blocks live
   capture.

If the Zoom portal requires a human account owner, that owner action is an
external prerequisite. No local test or attestation may claim it happened.

## Migration and rollback

- v1 capture plans and manifests remain readable only under their original
  schema and inventory rules.
- A v1 capture is not migrated in place. To use the new boundary, create a new
  setup attestation after the credential procedure, then create a new runtime
  capture ID. The old private workspace remains immutable and private.
- A missing setup attestation, stale digest, missing CRC validation, changed
  scope set, or incomplete runtime inventory fails closed.
- Rollback is code rollback only. Do not edit old plans, receipts, manifests,
  raw artifacts, or private review records. Re-run with a fresh capture ID
  after the boundary is restored.

The setup/runtime model is deliberately separate from the RTMS decoder. The
decoder may consume only a later privacy-reviewed, sanitized candidate; setup
attestation does not authorize it to inspect raw transcript bytes.
