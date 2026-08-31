# ExitSpec v0.5 execution runbook

Status: PR1 operating contract. This runbook governs the ExitSpec-only v0.5
qualification-gate train described in
[the plan](V0_5_QUALIFICATION_GATE_PLAN.md). The
[execution ledger](V0_5_EXECUTION_LEDGER.md) is the durable task-state record;
chat is not a source of truth.

## Operating boundary

The current train is exactly PR1–PR14. Work one milestone at a time.
Renumbering, combining, skipping, or adding a milestone requires an explicit
user-approved plan/goal amendment before implementation; the current milestones
may not be split or combined. PR1 freezes vocabulary and operating constraints
before product feature code begins.

ExitSpec never authorizes deployment or traffic. This train does not perform or
request provider integration, GPU execution, provider spend, external evidence
capture, cross-repository work, deployment, release publication, or traffic
changes. A prospective handoff and an externally supplied evidence package are
local, provider-neutral contract boundaries only.

PR12 is the one planned GitHub integration: a least-privilege required check
that consumes local ExitSpec output and reports qualification state. Conserving
GitHub usage confines that work to PR12; it does not remove the milestone or
permit its implementation early. The check never receives deployment, traffic,
provider, or authorization authority.

## Three independent axes

Keep these axes separate in schemas, UI, CLI output, receipts, and review:

| Axis | Question | Values | Consequence |
| --- | --- | --- | --- |
| Proofability | Can the frozen criterion be established by the declared method? | `PROVABLE`, `CLARIFICATION_REQUIRED`, `NOT_PROVABLE` | It is evaluated before evidence admission; a non-provable criterion has no execution request or receipt. |
| Verdict | What do admitted facts establish? | `PASS`, `FAIL`, `NOT_PROVEN` | It is assigned only by ExitSpec's deterministic recalculation, never by a producer. |
| Validity | Does a validated receipt still apply to the requested context and time? | `CURRENT`, `STALE`, `EXPIRED`, `INVALID` | It preserves historical facts while deciding present applicability. |

No axis converts into authority. In particular, `PROVABLE` is not a verdict,
`PASS` is not a deployment or traffic grant, and `CURRENT` is not permission to
act.

## PR execution procedure

1. Read the plan, this runbook, and the current ledger before changing a
   milestone. Record the base revision and set only that PR to `IN_PROGRESS`.
2. Keep the stated PR claim, non-goals, authority owner, and binary exit gate
   intact. A milestone change requires the explicit user-approved plan/goal
   amendment defined above before any implementation begins.
3. Make the smallest scoped change. Preserve frozen contracts and historical
   evidence; do not alter artifacts merely to make a later implementation pass.
4. Record changed files, focused checks, outcomes, and unresolved risks in the
   ledger. A failed check leaves the milestone in progress and is not hidden.
5. Run `git diff --check`, the focused contract/process tests, and any safe
   local validation proportionate to the change. Run the full engineering gate
   only when its untracked-file precondition can be met without hiding work.
6. Only after required checks pass, update the ledger state to `CANDIDATE` and
   create the local candidate commit. Identify the immutable candidate from the
   repository at review time with `git rev-parse HEAD`.
7. Stop for Mission Control and independent MTS review. A review request,
   merge, tag, release, deployment, or traffic action is outside this runbook.

## PR7 and PR8 boundary procedure

PR7 creates or verifies a canonical prospective handoff. It may bind the
subject, scope, context, frozen contract, workload, declared profile, and
required observations. It must contain no credentials, run identifier,
execution request, dispatch capability, provider configuration, measurement,
bundle, producer verdict, or authority field. Validating it must have no
external side effect.

PR8 accepts an evidence package only as untrusted local input. It verifies the
declared profile, context, canonical form, bounded file tree, integrity,
evidence class, and allowed facts before deterministic recalculation. It does
not fetch from, call, provision, authenticate to, or otherwise contact an
external producer. Rejection issues no verdict or receipt; admitted
insufficiency may become `NOT_PROVEN` only through the frozen verdict protocol.

## PR12 GitHub required-check procedure

PR12 may add only a documented GitHub required check that invokes local
ExitSpec CLI or assessment validation and reports the qualification state. Its
workflow declares `permissions: contents: read`, has no `id-token`, and receives
no deployment, traffic, provider, or credential authority. It must not call a
provider, mutate GitHub protection, dispatch deployment, change traffic, or
convert `PASS` into authorization. Every non-current or non-`PASS` state, plus
tampering and skipped evaluation, reports a non-passing check.

## Candidate review checklist

Before a reviewer is asked to inspect a candidate, confirm that the ledger
contains the exact base, candidate selector, changed-file list, commands and
results, remaining risks, PR title/body, and review state. The reviewer must be
able to reconstruct every assertion from the candidate revision and ledger
without chat history.
