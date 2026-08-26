# Zoom privacy-reviewed fixture pipeline

Status: PR 2 implementation; no real capture has been exported

The privacy pipeline derives a sanitized conformance candidate only after an
explicit human gate. It does not read a raw-capture path. A reviewer may open
the private original in an isolated environment, but the repository process
receives only the bounded observation contract in
`zoom_privacy_pipeline.py`.

```text
complete custody + setup/runtime binding
              |
              v
explicit raw-review consent receipt
              |
              v
isolated human review -> enum-only observations + payload digests
              |
              v
review-pending candidate -> second-person privacy/secret review
              |
              v
approved JSON fixture + immutable review receipt
```

## Gate sequence

1. **Custody gate.** The private capture must have a valid immutable custody
   manifest, setup/runtime binding, and external credential-rotation receipt.
   The present diagnostic capture is not eligible because its old v1 custody
   inventory is incomplete.
2. **Consent gate.** A human submits
   `exitspec.zoom-rtms-raw-review-consent.v1` for one capture and one custody
   manifest. It authorizes only opening the private original to derive a
   synthetic candidate. It forbids raw export, transcript persistence, product
   source creation, contract confirmation, freeze, measurement, and verdicts.
3. **Minimization gate.** The isolated reviewer submits only enum-valued packet
   shape, media/message type numbers, bounded timing/order metadata,
   pseudonymous speaker slots, finality, duplicate metadata, and SHA-256
   payload digests. The candidate has no text field. Names, provider IDs,
   meeting/stream IDs, URLs, tokens, secrets, raw bytes, and free-form notes are
   structurally rejected.
4. **Second-person review gate.** A second receipt records the candidate digest,
   privacy classification, secret-scan tool, review decision, and immutable
   provenance. Only `APPROVED_FOR_DECODER_TESTS` may be written as a generated
   fixture. `KEEP_PRIVATE` remains private and cannot publish a bundle.

## Generated bundle

An approved bundle contains only `fixture.json` and
`privacy-review-receipt.json` outside `.zoom-fixture-private/`. Provenance is
digest-only: capture ID, custody manifest ID, plan, setup, runtime, consent,
and credential-rotation values are hashed before they cross the boundary.
The raw private workspace is never copied, changed, or made writable by this
pipeline. Replaying the same bundle is idempotent; a changed fixture or review
receipt conflicts rather than overwriting the first record.

The fixture is a conformance input, not a golden claim. Its observed semantics
remain `OBSERVED_REVIEW_PENDING_V1` until the decoder PR pins the supported
protocol subset. Privacy approval does not authorize Zoom network ingress,
OAuth, a provider mapper, a source, a proposal, a contract, a proof run, or a
verdict.

## Required human action

No repository action can authorize inspection of the current private capture.
Before it is considered for derivation, the authorized privacy reviewer must:

- complete the provider-side credential rotation procedure from
  `ZOOM_SETUP_RUNTIME_EVIDENCE_SPEC.md`;
- verify a complete immutable custody manifest (or use a fresh complete
  capture); and
- explicitly provide a content-free consent receipt for the exact capture and
  manifest, with permission limited to isolated synthetic minimization.

Until those actions occur, the capture remains private diagnostic material and
the pipeline must not be run against it.
