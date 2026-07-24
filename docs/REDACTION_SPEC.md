# Transcript Redaction Boundary

## Purpose

ExitSpec applies a deterministic, best-effort redaction policy before transcript
text may be sent to a model provider or written to persistent storage. Only the
returned `redacted_text` may cross that boundary. The raw input is used
transiently during detection and is not retained in the result or finding
metadata.

Policy identifier: `exitspec-transcript-redaction/1.0`

This policy is a narrow safety control, not a PII classifier, compliance
certification, or guarantee that a transcript contains no sensitive data.

## Typed boundary

Call:

```python
result = redact_transcript(
    transcript_text,
    customer_terms=["Project Phoenix", "Example Customer"],
)
```

The result contains:

- `policy_version`: stable identifier for the detector behavior.
- `redacted_text`: category placeholders in place of supported values.
- `findings`: category, aggregate count, affected line numbers, and placeholder.
- `counts`: an explicit count for every supported category, including zeros.
- `decision`: `ALLOW_REDACTED_ONLY` or `BLOCK`.
- `safe_to_send` and `safe_to_persist`: mechanical boundary flags scoped to this
  policy version.
- `limitations`: permanent statements preventing a PII-free interpretation.

Neither findings nor counts include matched values, hashes of values, snippets,
prefixes, suffixes, or the raw transcript. Line numbers retain enough location
information for a reviewer to inspect the redacted source without copying a
secret into audit metadata.

`ALLOW_REDACTED_ONLY` means only the returned redacted text passed all supported
detectors after replacement. It does not make the original text safe, and it
does not prove that unsupported sensitive content is absent. `BLOCK` means a
supported pattern remained after replacement; neither sending nor persistence
is allowed.

## Supported categories

| Category | Detection boundary | Placeholder |
| --- | --- | --- |
| Bearer token | Token following a `Bearer` scheme | `[REDACTED:BEARER_TOKEN]` |
| API token | Labeled secrets, common environment-key labels, and conservative token prefixes | `[REDACTED:API_TOKEN]` |
| JWT | Three sufficiently long base64url-like segments | `[REDACTED:JWT]` |
| Payment card | 13–19 digits, optional spaces/hyphens, and a valid Luhn checksum | `[REDACTED:PAYMENT_CARD]` |
| Email | Conventional local part and dotted domain | `[REDACTED:EMAIL]` |
| Phone | Common separated forms or a `+`-prefixed E.164-like form | `[REDACTED:PHONE]` |
| Customer term | Case-insensitive literal term supplied by the caller | `[REDACTED:CUSTOMER_TERM]` |

Configured customer terms must be individual strings of at least three
characters. Line breaks and reserved redaction placeholders are rejected.
Terms are deduplicated case-insensitively and processed longest first. Their
values are never copied into the result.

## Determinism and overlap policy

Detection happens against the original input. Candidates are resolved in a
fixed risk order: existing placeholders, bearer tokens, JWTs, labeled or
prefixed API tokens, Luhn-valid payment cards, emails, phones, then configured
customer terms. Higher-priority candidates win overlapping spans. Selected
spans are replaced in source order.

Placeholders never contain line breaks, so LF, CRLF, and lone-CR line structure
is retained. Typical `Speaker: message` labels remain readable when the label
itself is not sensitive. Findings record one-based source line numbers; exact
character offsets are intentionally omitted because replacement changes line
length and detailed lengths add little audit value.

The policy recognizes its own placeholders. Applying it again to already
redacted output produces the same text, findings, counts, decision, and
limitations.

## Required integration rule

Callers must fail closed:

1. Keep raw transcript text in memory only long enough to call
   `redact_transcript`.
2. Discard the raw reference after the call.
3. Send or persist only `result.redacted_text` and non-secret result metadata.
4. Stop if `safe_to_send` or `safe_to_persist` is false for the intended action.
5. Keep downstream source references anchored to the redacted transcript.
6. Require a human confidentiality review before publishing or externally
   sharing a real customer transcript or evidence pack.

## Known limitations

Pattern matching will miss some names, addresses, account identifiers, internal
code words, novel credential formats, obfuscated values, transcription errors,
and sensitive facts that require context. Customer terms are not inferred.
False positives remain possible; for example, a non-card identifier that happens
to satisfy Luhn can be treated as a payment card. The policy does not inspect
audio, attachments, images, tool traces, model outputs, or external metadata.

Production deployments need organization-specific detectors, retention and
deletion controls, access control, encryption, incident response, and a human
review path appropriate to their threat model and legal obligations.
