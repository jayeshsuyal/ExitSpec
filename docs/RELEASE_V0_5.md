# ExitSpec v0.5.0 release checkpoint

Status: released as v0.5.0 from the exact green main commit; the annotated
`v0.5.0` tag and public GitHub release point to this commit. This checkpoint
closes the frozen PR1–PR14 train. It records the verified release boundary;
it does not grant deployment, provider, GPU, spending, or production-traffic
authority.

## What is included

- Exact serving-subject, qualification-scope, and context identities.
- Provider-neutral capability and proofability planning before external work.
- Canonical prospective handoff and bounded external-evidence admission.
- Deterministic qualification receipts with `PASS`, `FAIL`, and `NOT_PROVEN`.
- Purpose-bound `CURRENT`, `STALE`, `EXPIRED`, and `INVALID` assessment.
- A local CLI and least-privilege GitHub required check that reports state only.
- The `/app?mode=qualification` four-screen walkthrough: exact target,
  proofability, admitted evidence/verdict, and current-to-stale recheck.

## Verification boundary

The release's required PR and main workflows are the evidence for this
checkpoint. The engineering workflow runs the built-wheel distribution proof,
Python behavior suite, static JavaScript checks, security scans, and the
repository's established local gates. The browser workflow runs the mandatory
v0.4 no-skip Chromium, adversarial, and artifact-reader gates. The qualification
workflow runs the local CLI and assessment contracts under:

```yaml
permissions:
  contents: read
```

No workflow in this train has deployment credentials, provider credentials,
`id-token`, write permissions, or a `pull_request_target` path for untrusted
contribution code.

## Publication boundary

The `v0.5.0` tag and GitHub release are publication records only. They do not
authorize deployment, provider or GPU execution, spending money, changing
traffic, or any other production activity. `PASS` and `CURRENT` remain bounded
evidence states; an external human or deployment system must make any
operational decision.

## Honest limitations

All v0.5 qualification demonstrations and fixtures are local, synthetic,
process-local, non-durable, and unauthenticated. They do not prove producer
authorship, physical hardware truth, chronology, or facts absent from admitted
evidence. ExitSpec does not capture real external evidence, run a provider or
GPU, deploy, change traffic, spend money, or authorize production activity.
