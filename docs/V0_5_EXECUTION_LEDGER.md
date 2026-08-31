# ExitSpec v0.5 execution ledger

Status: durable PR-train state for the ExitSpec-only qualification-gate train.
Last updated: PR1 candidate prepared for local commit.

## Train controls

- **Authoritative plan:** [V0_5_QUALIFICATION_GATE_PLAN.md](V0_5_QUALIFICATION_GATE_PLAN.md)
- **Operating procedure:** [V0_5_EXECUTION_RUNBOOK.md](V0_5_EXECUTION_RUNBOOK.md)
- **Base revision:** `05e66208e9fdd98a04bde0bd3a4d83ee1ec71c3c`
- **Candidate selector:** `HEAD` after the PR1 local candidate commit; resolve
  its immutable SHA with `git rev-parse HEAD` during review.
- **Scope:** documentation/process only for PR1; no product feature code.
- **Non-authority:** ExitSpec never authorizes deployment or traffic. Provider
  integration, GPU execution, spending, external capture, cross-repository
  work, deployment, release publication, and traffic changes are out of scope.

The ledger is append-only in substance: retain completed evidence and risks;
add superseding entries rather than rewriting historical assertions. Permitted
states are `NOT_STARTED`, `IN_PROGRESS`, `BLOCKED`, `CANDIDATE`, `IN_REVIEW`,
and `MERGED`. A candidate is not merged, released, deployed, or authorized.

## Milestone state

| PR | Decision boundary | Depends on | State | Exit evidence / hold |
| --- | --- | --- | --- | --- |
| PR1 | Architecture, vocabulary, and threat contract | v0.4 baseline | CANDIDATE | Docs contract, process checks, link audit, and clean local candidate passed. |
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
| PR12 | Policy-consumer compatibility contract | PR11 | NOT_STARTED | Consumer result remains evidence only, never an authority grant. |
| PR13 | Guided four-screen product surface | PR6, PR12 | NOT_STARTED | Four states preserve proofability, verdict, validity, and zero authority. |
| PR14 | Adversarial closure and candidate checkpoint | PR2–PR13 | NOT_STARTED | Local deterministic, adversarial, documentation, and candidate-state gates. |

## PR1 evidence record

| Item | Record |
| --- | --- |
| Decision | Freeze the provider-neutral ExitSpec-only v0.5 architecture, 14-PR execution contract, and durable operating state before product feature code. |
| Changed files | `docs/V0_5_QUALIFICATION_GATE_PLAN.md`; `docs/V0_5_EXECUTION_RUNBOOK.md`; `docs/V0_5_EXECUTION_LEDGER.md`; `docs/ROADMAP.md`; planning-contract test. |
| Required distinctions | Proofability is pre-admission capability; Verdict is ExitSpec's result from admitted evidence; Validity is present applicability of a validated receipt. |
| Authority result | No authority owner changes. ExitSpec never authorizes deployment or traffic. |
| Tests | `/private/tmp/exitspec-v05-docs-venv/bin/python -m pytest tests/test_v0_5_planning_contract.py tests/test_engineering_process.py tests/test_v0_4_release_checkpoint.py` — 13 passed; `/private/tmp/exitspec-v05-docs-venv/bin/ruff check tests/test_v0_5_planning_contract.py` — passed. Local scan found no named provider delivery dependency, 14 PR headings, and six zero-authority assertions. |
| Remaining risks | The 14 milestones are an execution contract, not an implementation claim. Later PRs must keep provider and real-evidence operations outside this train. |
| Reviewer handoff | After the candidate commit, Mission Control and an independent MTS reviewer inspect the `HEAD` SHA and this ledger before any next milestone. |

## Proposed PR metadata

- **Title:** `docs: freeze v0.5 provider-neutral qualification execution contract`
- **Body summary:** Establishes the ExitSpec-only v0.5 architecture and
  execution contract, adds durable runbook and ledger state, preserves all 14
  milestones, makes PR7/PR8 provider-neutral local boundaries, and explicitly
  keeps deployment and traffic authority outside ExitSpec. No product feature
  code, external evidence operation, provider action, cross-repository change,
  release publication, or traffic action is included.
