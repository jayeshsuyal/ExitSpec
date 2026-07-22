# Security and Privacy

## Version-one security posture

ExitSpec is a local, single-user prototype. It is not a security product and it does not yet claim enterprise security, tenant isolation, tamper-proof execution, or formal compliance.

## Required controls

- API keys must come from environment variables or a local secret manager, never contracts, logs, committed fixtures, screenshots, or public artifacts.
- Synthetic discovery data is mandatory for the public demo.
- Secrets and detected PII are redacted before persistence.
- Redaction audit records retain rule, location, detector version, timestamp, and artifact identifier without retaining the sensitive value.
- User-facing reports and internal debugging artifacts have separate export policies.
- Uploaded contracts are data, not executable code. Adapters are allowlisted and typed; contract fields never become arbitrary shell commands.
- Published evidence bundles undergo a secret and PII review before inclusion in Git.

## Artifact integrity

SHA-256 provides a change-detection reference for a recorded artifact. It does not prove who created the artifact, whether the request reached the claimed endpoint, whether a measurement was omitted, or whether the original source was honest.

## Data lifecycle

```text
input -> validation -> redaction -> persistence -> artifact hash -> report export -> retention/deletion
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
