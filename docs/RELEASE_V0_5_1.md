# ExitSpec v0.5.1 patch release checkpoint

Status: released as v0.5.1 from the exact green main commit. The annotated
`v0.5.1` tag and public GitHub release point to that commit. This patch does
not move, replace, or otherwise alter the existing v0.5.0 release record. It
does not grant deployment, provider, GPU, spending, or production-traffic
authority.

## Patch boundary

- Qualification expiry handling preserves the exact boundary and fails closed
  as `INVALID_EXPIRY` if expiry arithmetic cannot be represented safely.
- Evidence-bundle verification detects a deterministic extra-file race between
  tree observations and rejects the bundle as unsafe.
- The compact desktop qualification shell falls back to scrolling in zoomed or
  short desktop views.
- The engineering gate binds its final result to the candidate revision and
  rejects a worktree that changes during the gate.
- The v0.5 qualification check uses a trusted, base-owned control checkout to
  bound and qualify the candidate source as data; it has read-only contents
  permission and makes no provider, deployment, or traffic action.

## Verification boundary

The release's required PR and main workflows are the evidence for this
checkpoint. The engineering workflow runs the built-wheel distribution proof,
Python behavior suite, static JavaScript checks, security scans, and the
repository's established local gates. The browser workflow runs the mandatory
v0.4 no-skip Chromium, adversarial, and artifact-reader gates. The
qualification workflow runs the local CLI and assessment contracts through the
trusted control-checkout harness.

## Honest limitations

All v0.5 qualification demonstrations and fixtures remain local,
process-local, synthetic, unauthenticated, and non-durable. They do not prove
producer authorship, physical hardware truth, chronology, or facts absent from
admitted evidence. ExitSpec does not capture real external evidence, run a
provider or GPU, deploy, change traffic, spend money, or authorize production
activity.
