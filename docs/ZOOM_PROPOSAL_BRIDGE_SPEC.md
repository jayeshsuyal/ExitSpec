# Zoom transcript proposal bridge

Status: implemented as a local, review-only handoff into the existing POC
source and proposal services

The bridge consumes the private ordered processing input from the PR 4 session
state machine. It creates one stable draft POC with the existing
`FirstSourceChoice.MEETING`, attaches one redacted source through
`ProcessLocalPOCSourceIntake`, and returns the existing `SourceBoundProposal`
objects used by `/app` and human proposal review.

```text
ZoomSessionProcessingInput
        |
        v
digest-only Zoom provenance + bounded redacted speaker lines
        |
        v
existing draft POC service + existing source intake
        |
        v
existing SourceBoundProposal / NEEDS_REVIEW review queue
```

There is no second proposal store, no new contract-definition path, no
provider call, and no Fireworks dependency. If a provider-assisted authoring
adapter is added later, its output must enter the existing schema and source
anchor checks; the bridge does not accept arbitrary provider fields.

## POC and source identity

The POC ID, POC-create idempotency key, source-attach idempotency key, and
session completion key are all derived from the server-owned session ID. A
retry therefore returns the same POC and source, while changed request metadata
fails closed through the existing draft service's idempotency boundary.

The attached source remains `SourceKind.MEETING`, because that is the existing
source catalog. Its external identity is a digest-bound `zoom.rtms.*` value and
its adapter is `zoom_rtms` with version `zoom-rtms-decoder-1.0`. The bridge
result additionally carries the full `ZoomSourceProvenance` record so the
Zoom-specific source binding is not lost at the handoff boundary.

## Provenance

`ZoomSourceProvenance` binds:

- `ZOOM_RTMS` as the source provider;
- the session, decoder version, and pinned packet schema version;
- the source classification;
- the normalized transcript digest;
- fixture, capture-plan, setup-attestation, and runtime-plan digests; and
- the ordered unique packet digest population and count.

Every segment must agree on the setup/runtime provenance fields. Packet digests
must be unique in the processing input. A mismatch rejects the bridge before
POC creation or source attachment. No provider meeting ID, participant ID,
token, URL, or raw packet enters the bridge result.

## Proposal and metric boundaries

The source intake performs the existing redaction, bounded transcript parsing,
source anchoring, candidate extraction, append-only attachment, and replay
handling. Every returned proposal is `NEEDS_REVIEW` and source-bound.

The bridge may annotate a proposal with the existing
`Metric.EXACT_TOOL_SELECTION_RATE` only when the source wording explicitly
mentions tool selection. It never invents a threshold, minimum sample count,
criterion ID, workload, adapter, or evidence policy. Vague or unsupported
claims retain `NEEDS_REVIEW`, have `catalog_metric = null`, and remain
`evaluation_state = NOT_RUN`. The existing contract/evaluation spine—not this
bridge—may later produce a `NOT_PROVEN` verdict when an unsupported or
insufficient claim reaches proof.

The bridge result and assessments all set confirmation, freeze, measurement,
and verdict authority to false. Customer review, employee edits, contract
freeze, supported evaluation, and Evidence Pack generation remain the existing
human-gated flows.

## Failure and replay

The bridge refuses a session that is not `PROCESSING` or `DRAFT_READY`, mixed
segment provenance, malformed request metadata, failed POC creation, failed
source attachment, or a source/proposal projection mismatch. It does not mark a
transient attachment failure as a successful session completion. A successful
retry reuses the same draft/source identities and records the session's single
processing result digest.

## Verification

[`test_zoom_proposal_bridge.py`](../tests/test_zoom_proposal_bridge.py) covers
one Zoom source and POC, provenance binding, supported-catalog annotation,
vague-claim review handling, exact replay without duplicate sources, changed
metadata conflict, not-ready sessions, and mixed provenance refusal.
