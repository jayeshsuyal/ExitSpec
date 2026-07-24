# Security and Privacy

## Version-one security posture

ExitSpec is a local, single-user prototype. It is not a security product and it does not yet claim enterprise security, tenant isolation, tamper-proof execution, or formal compliance.

The public browser demo is synthetic, loopback-only, provider-free, and
in-memory. Its pasted-note intake is redaction-first and retains only redacted
source plus safe summary metadata.

The repository also has a composed, side-effect-free assisted-authoring service.
It is tested through the real `FireworksProvider` with a fake injected transport
and performs no persistence or browser/session mutation. It has no built-in live
network transport and is not wired into the browser UI. There is no live
Fireworks call and no speech-to-text integration.

## Raw-source boundary

The required real-customer path is:

```text
raw source in transient memory
    -> redact_transcript
    -> immutable redacted-only result
    -> assert_redaction_egress
    -> provider or persistence boundary
```

Raw source must not appear in provider requests, persisted state, receipts, logs,
error messages, screenshots, or exported artifacts. Redaction findings contain
category, count, placeholder, and line numbers—not the matched value.

`ALLOW_REDACTED_ONLY` means only that the text passed the current mechanical
policy. The redactor is deliberately best-effort. It cannot discover every name,
code word, credential format, contextual secret, personal identifier, or
regulated value. Configured customer terms and human review remain required
before real customer material is shared or retained.

## Provider boundary

- Fireworks is a replaceable structured authoring/execution provider, never an
  approval or verdict authority.
- Provider schemas may describe candidate facts, not `approved`, reviewer,
  contract status, canonical hash, or acceptance verdict fields.
- Structured output is validated locally against JSON Schema Draft 2020-12 before
  it reaches the typed application callback.
- External schema references are rejected, so schema validation cannot fetch
  remote content.
- The Fireworks adapter accepts only the pinned official Chat Completions
  endpoint and an injected transport.
- Request/response bodies, credentials, provider request IDs, and generated output
  are hidden from object representations and sanitized errors.
- Receipts contain execution metadata such as model, attempts, latency, token
  counts, and estimated cost; they do not confer authority.
- Retry and budget controls reduce operational risk, but a client-side estimate
  is not a guaranteed provider billing ceiling.

The provider and assisted-authoring integration tests use fake injected
transports and synthetic values. They verify that redaction precedes provider
execution, provider output is locally validated and source-exact, authority
fields are rejected, returned drafts remain `NEEDS_REVIEW`, and provider failures
return no authoring state. A live transport still requires an explicit credential,
model, data policy, and spend ceiling.

## Required controls

- API keys must come from environment variables or a local secret manager, never contracts, logs, committed fixtures, screenshots, or public artifacts.
- Synthetic discovery data is mandatory for the public demo.
- Real customer source must pass the redaction and fresh egress checks before any
  provider or persistence boundary.
- Browser intake must remain provider-free unless an explicit assisted workflow,
  disclosure, and policy gate are implemented.
- The current redaction result retains policy version, category, placeholder,
  count, and line numbers without retaining the sensitive value. Any future
  persisted audit envelope must add timestamp and artifact identity without raw
  matches.
- Human review is mandatory because passing the mechanical redaction policy does
  not prove that the material is safe.
- User-facing reports and internal debugging artifacts have separate export policies.
- Uploaded contracts are data, not executable code. Adapters are allowlisted and typed; contract fields never become arbitrary shell commands.
- Published evidence bundles undergo a secret and PII review before inclusion in Git.

## Artifact integrity

RFC 8785 JCS plus SHA-256 provides a deterministic change-detection reference for
a frozen contract. Artifact SHA-256 values provide the same kind of integrity
reference for recorded files. A hash is not a signature: it does not prove who
created the artifact, whether a request reached the claimed endpoint, whether
evidence was omitted, or whether the source was honest.

Frozen contract models and their nested contract graph reject assignment. The POC
Acceptance Evidence Pack additionally verifies the frozen digest, manifest
identity, criterion identity, measurement identity, and deterministic criterion
and overall verdicts before rendering. The current report supports exactly one
frozen criterion.

## Data lifecycle

```text
input -> transient redaction -> egress check -> typed validation
      -> approved persistence -> artifact hash -> report export
      -> retention/deletion
```

The first public sample contains no personal data, customer data, or live credentials. Production-facing retention and deletion workflows are deferred until the project has a real multi-user threat model.

## Threats to test before publishing

1. Secret in a contract, fixture, environment dump, or error string.
2. PII in a request, response, trace, or generated HTML.
3. Uploaded path traversal or arbitrary artifact-path write.
4. Contract attempting to choose an unapproved adapter or command.
5. Missing redaction audit record.
6. Artifact altered after hashing.
7. Customer-system failure incorrectly attributed to an ExitSpec bug, or vice versa.
8. Prompt injection attempting to approve a draft, select an adapter, freeze a
   contract, or emit a verdict.
9. A forged or stale redaction result crossing egress.
10. A provider output that passes remote structured-output handling but fails the
    local schema or typed validator.
