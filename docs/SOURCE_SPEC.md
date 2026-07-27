# ExitSpec Source Contract

## Status

This document freezes the provider-neutral source boundary for Wave 2. The
authoritative executable contract is
[`wave-2-acceptance-v1.json`](../examples/support-agent/email/wave-2-acceptance-v1.json).
That manifest is `FROZEN` at version `1.0.0`.

Freezing the contract authorizes implementation against its fixtures and gates.
It does not claim that the parser, workflow, browser flow, or Wave 2 exit gate
already exists.

The first adapter accepts only employee-selected, manifest-approved synthetic
RFC822 fixtures. It does not authorize real email, arbitrary upload, mailbox
access, OAuth, webhooks, or sending.

## Decision

Email enters ExitSpec as untrusted source. It is normalized, redacted, and
wrapped in a provider-neutral `SourceEnvelope` before any durable write or
assisted-authoring request. It can propose facts for employee review; it cannot
approve an agreement, represent customer confirmation, freeze a contract,
measure a POC, or assign a verdict.

The required flow is:

```text
explicit employee fixture selection
  -> verify frozen fixture identity and digest
  -> parse in request-local memory
  -> validate identity, MIME, body, and attachments
  -> normalize and redact headers and textual parts
  -> build SourceEnvelope with exact redacted provenance
  -> atomically persist one source version and NEEDS_REVIEW candidates
  -> release raw RFC822 bytes
```

Any validation or redaction failure before envelope construction persists
nothing. Assisted-authoring failure after a valid envelope may retain only that
redacted envelope; it cannot fabricate candidates or change agreement state.
Missing or invalid synthetic approval markers fail as `source_not_approved`;
fixture bytes that do not match the frozen manifest digest fail as
`fixture_digest_mismatch`.

## Normative language

`MUST`, `MUST NOT`, `SHOULD`, and `MAY` are normative. The frozen manifest wins
if this explanatory document and the machine-readable contract ever disagree.

## `SourceEnvelope` V1

The implementation contract is intentionally provider-neutral:

```text
SourceEnvelope
  schema_version
  source_type = "rfc822"
  source_id
  source_version
  version_id
  observed_at
  ingested_at
  authored_at?                 # source-asserted, never trusted for ordering
  synthetic = true
  authority = "untrusted_source_only"
  redaction
    policy_version
    counts
  messages[]
    message_key
    redacted header projection
    parts[]
      part_path
      kind                     # body | attachment
      media_type
      redacted_text
      redacted_text_sha256
      redacted_filename_sha256? # attachments only
  content_sha256               # canonical redacted envelope only
  candidates[]                 # exact manifest projection, always NEEDS_REVIEW
```

The envelope MUST contain no raw RFC822, raw address, raw `Message-ID`, raw
attachment filename, unredacted customer term, or secret. A real-source raw
fixture digest is not part of the persisted envelope. Exact fixture SHA-256
values exist in the acceptance manifest only because every Wave 2 input is
synthetic and immutable.

The envelope is source evidence, not an agreement and not a decision record.

## Stable identity

V1 requires exactly one syntactically valid `Message-ID`.

1. Trim ASCII whitespace.
2. Remove one surrounding angle-bracket pair.
3. Lowercase ASCII.
4. Reject an empty result.

The message key is:

```text
msg:<sha256("exitspec-rfc822-message-id-v1" || NUL || normalized_message_id)>
```

The thread root is resolved in this order:

1. the first valid `Message-ID` in `References`;
2. the single valid `Message-ID` in `In-Reply-To`;
3. the message's own `Message-ID`.

The thread-level source identity is:

```text
rfc822:<sha256("exitspec-rfc822-thread-id-v1" || NUL || normalized_root_id)>
```

Raw identifiers MUST NOT be persisted or returned. A missing or ambiguous
identity fails before persistence.

## Versions, duplicates, and changed threads

`source_version` is a positive integer allocated atomically in accepted-ingest
order. The untrusted `Date` header never controls order.

- A first unique message creates version `1`.
- Reimporting the same message key with the same frozen fixture digest returns
  the existing envelope and candidates. It performs zero writes and creates zero
  candidates.
- A new message key that references an existing root appends one message and
  creates exactly one new source version.
- Different bytes under an existing message key are an identity conflict. The
  previous source remains unchanged.
- An unknown parent fails as `thread_parent_not_found`; the adapter does not
  silently fork a new thread.

`version_id` is a domain-separated digest of canonical JSON. The exact top-level
projection is `messages`, `source_id`, and `source_version`. Each message
contains exactly `message_key`, `parts`, and `redacted_header_sha256`; each part
contains exactly `part_path`, `redacted_filename_sha256`, and
`redacted_text_sha256`. Messages remain in accepted-ingest order and parts remain
in accepted MIME traversal order.

The redacted header digest covers exactly `authored_at`, `from`, `subject`, and
`to` under the domain `exitspec-rfc822-redacted-headers-v1`. The version digest
uses `exitspec-source-version-v1`. Both prepend the ASCII domain and NUL byte to
canonical UTF-8 JSON with sorted keys, no insignificant whitespace, unescaped
Unicode, JSON numbers for integers, and JSON `null` for absent optional values.
The manifest freezes the expected version ID for every accepted case. No raw
content enters either identity.

Identity lookup, version allocation, envelope persistence, and candidate
persistence MUST be one transaction. The transaction is acquired before identity
lookup and held through idempotency-record persistence.

The manifest freezes both possible commit orders for two concurrent imports of
`thread-root`. Both requests may finish request-local validation before either
acquires the transaction, but exactly one response is `accepted` and exactly one
is `duplicate_replay`. The final store contains one thread source, one source
version, one idempotency record, and the fixture's exact two candidates. The
duplicate creates zero writes and zero candidates. Any other response multiset
or final cardinality fails the concurrent idempotency gate.

## Normalization

V1 supports `utf-8` and `us-ascii` text only. Text is transformed in this exact
order:

1. strictly decode the declared charset;
2. convert CRLF and bare CR to LF;
3. normalize Unicode to NFC;
4. remove trailing SPACE and TAB from each line;
5. remove leading and trailing blank lines;
6. append exactly one LF.

Malformed transfer encoding fails as `malformed_transfer_encoding`; an
unsupported charset fails as `unsupported_charset`. Raw message size, unfolded
header size, header count, MIME depth, and MIME part count fail respectively as
`raw_message_too_large`, `header_too_large`, `too_many_headers`,
`mime_too_deep`, and `too_many_mime_parts`. Every path persists nothing and
creates no candidates.

A non-empty inline `text/plain` body is required. When both HTML and plain-text
alternatives are present, V1 accepts them only if the conservative alternative
comparison proves they carry the same normalized text. A disagreement fails as
`alternative_disagreement`; ExitSpec does not guess which threshold is correct.
HTML is never rendered in the product.

## Redaction before persistence

Parsing happens in request-local memory. Redaction runs before logging,
persistence, candidate creation, provider egress, error detail, or receipt
creation.

The manifest—not test code—owns the exact V1 matching rules:

| Order | Kind | Pattern/semantics | Case |
|---:|---|---|---|
| 1 | Secret | `(?<![A-Za-z0-9_])api_key=[A-Za-z0-9._-]+(?![A-Za-z0-9._-])` with ASCII identifier boundaries | Sensitive |
| 2 | Customer term | NFC-normalized, regex-escaped fixture literal; no implicit word boundary; longest first, then casefolded lexical order | Insensitive |
| 3 | Email | `(?<![A-Z0-9._%+-])[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}(?![A-Z0-9.-])` with ASCII token boundaries | Insensitive |
| 4 | Phone | `(?<![0-9])(?:\+?1[ .-]?)?\(?[2-9][0-9]{2}\)?[ .-][0-9]{3}[ .-][0-9]{4}(?![0-9])` with ASCII digit boundaries | Not applicable |

All matches are leftmost and non-overlapping. Customer terms come only from the
exact `customer_terms` array on the selected fixture. Empty terms are invalid;
NFC-casefold duplicates collapse before sorting.

Replacement tokens are `[SECRET]`, `[CUSTOMER_TERM]`, `[EMAIL]`, and `[PHONE]`.
The same operation covers allowed persisted headers, inline text, textual
attachments, filenames, and provenance text. A redaction exception or
unredactable value produces `redaction_failed`, persists nothing, and emits only
a content-free typed receipt.

The persisted header projection is limited to redacted display names, redacted
subject, a source-asserted parsed date, and opaque identity digests.

Raw bytes MUST NOT appear in logs, exceptions, metrics labels, receipts, browser
state, or provider requests.

## Attachments

Attachments are fail-closed. V1 allows only:

| Media type | Extension | Treatment |
|---|---|---|
| `text/plain` | `.txt` | Strict decode, normalize, redact, persist as text |

Both media type and extension must match. Filenames must be basenames and are
redacted before display or persistence. V1 limits are:

- three attachments;
- 32,768 decoded bytes per attachment;
- 65,536 decoded bytes in total;
- 128 UTF-8 bytes per filename;
- 20 MIME leaf parts and MIME depth eight.

Unsupported types, path-like filenames, decoding errors, size/count violations,
or attachment redaction failures reject the entire import. Partial attachment
skipping is forbidden because it would create a misleadingly incomplete source.
The adapter never executes, expands, previews, OCRs, or sends an attachment.
The allowed attachment fixture deliberately names its file
`ExampleCo-(priya@customer.example)-requirements.txt`; its frozen persisted
oracle is `[CUSTOMER_TERM]-([EMAIL])-requirements.txt`. The manifest fixes both
that value and its SHA-256 digest.

## Provenance

Every accepted fixture MUST produce the exact positive `expected_candidate_count`
and exact ordered `expected_candidates` projection frozen in the manifest. Zero
candidates, an additional candidate, a missing candidate, or a reordered
projection fails the case.

Each candidate contains exactly its type, `NEEDS_REVIEW` state, numeric
projection, and one source link carrying:

```text
source_id
source_version
version_id
message_key
part_path
start_byte
end_byte
quote_sha256
```

`part_path` is
`<body|attachment>:<normalized_media_type>:<zero_based_index>`.

Offsets are zero-based, half-open UTF-8 byte ranges into the normalized,
redacted, persisted part—not the raw email. `quote_sha256` is calculated over
the exact selected redacted bytes. The fixture manifest freezes concrete spans,
candidate projections, candidate counts, and version IDs for every accepted
case.

If the range is invalid, the digest differs, or the cited source version is no
longer current, candidate creation fails as `source_link_violation`. The system
must never repair provenance by searching for similar text.

## Authority boundary

RFC822 headers, body text, HTML, and attachments are all untrusted source.
Phrases such as “approved,” “freeze this,” or “mark PASS” have no control-plane
meaning.

Every generated fact starts in `NEEDS_REVIEW`. Only the existing named employee
and exact-version customer-decision flow may advance agreement state. The email
adapter and any authoring provider have zero methods or fields that can:

- approve a candidate;
- record employee review;
- record customer confirmation;
- freeze a contract;
- create or alter measurement evidence;
- assign `PASS`, `FAIL`, `BLOCKED`, or `NOT_PROVEN`; or
- impersonate an employee or customer.

The `authority-attack.eml` fixture makes this boundary executable.

## Timing gate

The user-visible clock starts immediately before the browser dispatches the
explicit **Import sample email** action. It ends on the first animation frame
after source-linked `NEEDS_REVIEW` candidates and provenance controls are
visible and interactive.

Every warmup and measured run gets a fresh empty source, candidate, idempotency,
and operation store. Browser source state and the timing recorder are reset
before setup. The server process may remain running, but no durable or
process-local workflow state may carry across runs.

For the follow-up case, `thread-root` is seeded and verified at source version
`1` with exactly two candidates outside the measured interval. Seed timing is
cleared before the follow-up clock starts. Other accepted cases begin from an
empty store.

On the seeded localhost application:

- every accepted fixture must complete within 60,000 ms;
- p95 across five measured runs of each of the four accepted fixtures must be at
  most 10,000 ms, using nearest-rank over all 20 measured values;
- every local typed refusal must render within 5,000 ms; and
- an external authoring provider is not required to pass.

Duplicate timing is a separate five-run series. Each run uses a fresh store,
imports `thread-root` once outside the interval, clears setup timing, and then
measures the second exact import from dispatch to visible `duplicate_replay`.
Each duplicate must finish within 5,000 ms, duplicate p95 must be at most
2,000 ms, and writes/candidates created must both be zero.

Teardown occurs after the end timestamp and evidence capture. It destroys the
store, clears browser source state, discards the timing recorder, and asserts
that no source task remains pending. Setup and teardown never enter latency
samples.

The source adapter itself performs zero external egress.

## Required outcome matrix

The manifest freezes exact typed behavior for:

- a missing or invalid synthetic marker;
- fixture digest mismatch;
- raw message, unfolded-header, header-count, MIME-depth, and MIME-part limits;
- malformed transfer encoding and unsupported charset;
- exact duplicate import;
- both commit orders of a concurrent duplicate import;
- a new follow-up in an existing thread;
- changed bytes under the same `Message-ID`;
- sender ambiguity;
- missing inline body;
- oversized content;
- unsupported attachment;
- unsafe attachment filename;
- per-file attachment size, total attachment size, and attachment count;
- attachment decoding and attachment redaction failure;
- HTML/plain-text disagreement;
- redaction failure;
- assisted-authoring provider failure;
- unknown thread parent;
- source-link violation; and
- missing stable identity.

Every non-physical fault is frozen as a deterministic manifest operation over an
exact base fixture. The acceptance test materializes or evaluates that
operation, verifies the exact code, and enforces zero new persistence and zero
new candidates. Large attachment limits use exact virtual decoded-size vectors
instead of committing giant binary fixtures.

Failure before a redacted envelope persists nothing. The one exception is an
assisted-authoring failure after successful source persistence: the safe
redacted envelope remains available for local/manual authoring, with no provider
candidates.

Every terminal outcome emits a content-free receipt containing only the fields
allowed by the manifest. A receipt never includes source text, identifiers,
addresses, attachment details, customer terms, secrets, candidate text, or
provider payloads.

## Binary exit gate

Wave 2 passes only when every manifest rule passes:

- all fixture hashes match;
- source identities, versions, and version IDs are exact;
- every accepted fixture emits its exact non-zero candidate count and typed
  projection;
- redaction and provenance oracles match byte-for-byte;
- the sensitive attachment filename redacts exactly;
- every accepted candidate remains `NEEDS_REVIEW`;
- duplicate reimport creates no writes or candidates;
- both concurrent duplicate commit orders produce one accepted response, one
  duplicate response, one stored version, and one exact candidate set;
- a valid follow-up creates exactly one version;
- every declared failure has an executable fixture or deterministic fault and
  returns its typed safe outcome;
- timing thresholds pass;
- raw persistence, leaks, loss, duplication, implicit import, external source
  egress, and authority violations are all zero; and
- the existing agreement spine still passes unchanged.

No aggregate score may hide a failed critical slice.

## Explicit non-goals

Wave 2 does not include OAuth, Gmail, Outlook, IMAP, Microsoft Graph, mailbox
credentials, remote mailbox access, webhook ingestion, arbitrary upload,
real customer email, real customer data, sending or replying, production
identity, attachment execution, archive expansion, PDF/OCR, STT, or meeting
capture.

Real mailbox and customer-bound source remain blocked on the hosted identity,
authorization, retention, deletion, incident, and provider-approval gates
defined later in the engineering playbook.
