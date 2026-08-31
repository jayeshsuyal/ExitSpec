# ExitSpec v0.5 execution ledger

Status: durable PR-train state for the ExitSpec-only qualification-gate train.
Last updated: PR1 superseding candidate prepared for Mission Control review.

## Train controls

- **Authoritative plan:** [V0_5_QUALIFICATION_GATE_PLAN.md](V0_5_QUALIFICATION_GATE_PLAN.md)
- **Operating procedure:** [V0_5_EXECUTION_RUNBOOK.md](V0_5_EXECUTION_RUNBOOK.md)
- **Base revision:** `05e66208e9fdd98a04bde0bd3a4d83ee1ec71c3c`
- **Rejected candidate history:**
  `78fe2cdae5fcb4e1230636dc1db8a2b6222c543a` and
  `e76e0735f6cc3eb2eecb05eeac06880d4a525b6c`
- **Superseding candidate selector:** `HEAD` after the current local corrective
  commit; resolve its immutable SHA with `git rev-parse HEAD` during review.
- **Scope:** documentation/process only for PR1; no product feature code.
- **Non-authority:** ExitSpec never authorizes deployment or traffic. Provider
  integration, GPU execution, spending, external capture, cross-repository
  work, deployment, release publication, and traffic changes are out of scope.
  PR12's GitHub required check is status-only and least-privilege; it grants no
  deployment or traffic authority.

The ledger is append-only in substance: retain completed evidence and risks;
add superseding entries rather than rewriting historical assertions. Permitted
states are `NOT_STARTED`, `IN_PROGRESS`, `BLOCKED`, `CANDIDATE`, `IN_REVIEW`,
and `MERGED`. A candidate is not merged, released, deployed, or authorized.

## Milestone state

| PR | Decision boundary | Depends on | State | Exit evidence / hold |
| --- | --- | --- | --- | --- |
| PR1 | Architecture, vocabulary, and threat contract | v0.4 baseline | CANDIDATE | GitHub Actions contract correction, focused validation, link and scope scans, and clean superseding candidate passed; Mission Control review is pending. |
| PR2 | Serving-subject identity | PR1 | NOT_STARTED | Strict identity mutation and malformed-input coverage. |
| PR3 | Qualification scope and context | PR2 | NOT_STARTED | Distinguishable subject/scope drift and canonical context. |
| PR4 | Producer capability descriptor | PR3 | NOT_STARTED | Server-owned profile; no caller can expand capability. |
| PR5 | Proofability engine | PR4 | NOT_STARTED | Unsupported semantics stop before any external operation. |
| PR6 | Proofability service and workspace projection | PR5 | NOT_STARTED | Deterministic, redacted, zero-side-effect UI/API projection. |
| PR7 | Provider-neutral prospective handoff boundary | PR3, PR5 | NOT_STARTED | Context mismatch rejects; valid artifact has no dispatch or external effect. |
| PR8 | Provider-neutral external-evidence admission boundary | PR7 | NOT_STARTED | Untrusted local package is validated and recalculated without producer contact. |
| PR9 | Inference-performance qualification receipt | PR8 | NOT_STARTED | Only original admitted context/evidence can issue typed receipt. |
| PR10 | Qualification validity and staleness | PR9 | NOT_STARTED | Context drift and time boundaries fail closed without rewriting history. |
| PR11 | Qualification CLI | PR10 | NOT_STARTED | Stable safe output; only current exact-scope `PASS` receives exit code 0. |
| PR12 | GitHub required-check integration | PR11 | NOT_STARTED | Least-privilege GitHub check reports qualification state only; `PASS` never grants authority. |
| PR13 | Guided four-screen product surface | PR6, PR12 | NOT_STARTED | Four states preserve proofability, verdict, validity, and zero authority. |
| PR14 | Adversarial closure and candidate checkpoint | PR2–PR13 | NOT_STARTED | Local deterministic, adversarial, documentation, and candidate-state gates. |

## Mission Control review history

| Candidate | Reviewer | Decision | Record |
| --- | --- | --- | --- |
| `78fe2cdae5fcb4e1230636dc1db8a2b6222c543a` | Mission Control | `CHANGES_REQUIRED` | Preserve this parent. Restore PR12 as a least-privilege GitHub required check, make PR1–PR14 exact, add an explicit threat model, strengthen contract tests, and do not send this candidate to MTS as approved. |
| `e76e0735f6cc3eb2eecb05eeac06880d4a525b6c` | Mission Control | `CHANGES_REQUIRED` | P1 — invalid permissions syntax: `permissions: contents: read` is not valid GitHub Actions YAML. Replace prose/test assertions with the exact valid least-privilege block; forbid `pull_request_target` for untrusted contribution code and privileged/untrusted checkout combinations; retain no `id-token`, secrets, deployment/provider credentials, or write permissions; and keep required-status branch protection owner-configured outside ExitSpec. |
| `HEAD` after the current local corrective commit | Mission Control and independent MTS | PENDING | New candidate must retain both rejection records, use the valid YAML block, and pass the focused validation before Mission Control review. |

## PR1 evidence record

| Item | Record |
| --- | --- |
| Decision | Freeze the provider-neutral ExitSpec-only v0.5 architecture, 14-PR execution contract, and durable operating state before product feature code. |
| Changed files | `docs/V0_5_QUALIFICATION_GATE_PLAN.md`; `docs/V0_5_EXECUTION_RUNBOOK.md`; `docs/V0_5_EXECUTION_LEDGER.md`; `docs/ROADMAP.md`; planning-contract test. |
| Required distinctions | Proofability is pre-admission capability; Verdict is ExitSpec's result from admitted evidence; Validity is present applicability of a validated receipt. |
| Authority result | No authority owner changes. ExitSpec never authorizes deployment or traffic. |
| Tests | `/private/tmp/exitspec-v05-docs-venv/bin/python -m pytest tests/test_v0_5_planning_contract.py tests/test_engineering_process.py tests/test_v0_4_release_checkpoint.py` — 16 passed; `/private/tmp/exitspec-v05-docs-venv/bin/ruff check tests/test_v0_5_planning_contract.py` — passed. Local link audit, 14-milestone scan, provider-specific dependency scan, invalid-inline-permissions scan, retired permissive-language scan, and `git diff --check` passed. |
| Remaining risks | The 14 milestones are a fixed execution contract, not an implementation claim. PR12 must retain the exact valid read-only YAML block, a status-only GitHub boundary, and owner-configured branch protection; later PRs must keep provider and real-evidence operations outside this train. |
| Reviewer handoff | Mission Control requested a second superseding candidate. Do not submit either preserved parent to MTS as approved; after correction, Mission Control inspects the new `HEAD` SHA and this ledger before any next milestone. |

## Proposed PR metadata

- **Title:** `docs: freeze v0.5 provider-neutral qualification execution contract`
- **Body summary:** Freezes the ExitSpec-only v0.5 architecture and execution
  contract: exactly PR1–PR14, provider-neutral PR7/PR8 boundaries, an explicit
  threat model, and a least-privilege GitHub required check that reports
  qualification state only. ExitSpec never authorizes deployment or traffic.
  No product feature code, external evidence operation, provider action,
  cross-repository change, release publication, deployment, or traffic action
  is included.
- **Evidence note:** This superseding candidate incorporates independent review
  corrections, preserved in the review history above, including the valid
  GitHub Actions permissions contract.
