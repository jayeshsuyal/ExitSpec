# ExitSpec v0.5 execution ledger

Status: durable PR-train state for the ExitSpec-only qualification-gate train.
Last updated: PR2 local candidate prepared for Mission Control review after all
required local gates passed.

## Train controls

- **Authoritative plan:** [V0_5_QUALIFICATION_GATE_PLAN.md](V0_5_QUALIFICATION_GATE_PLAN.md)
- **Operating procedure:** [V0_5_EXECUTION_RUNBOOK.md](V0_5_EXECUTION_RUNBOOK.md)
- **Base revision:** `2a6ce7b681063b73450bf7a4573dea5dac8314b5` (PR #157 merge)
- **Rejected candidate history:**
  `78fe2cdae5fcb4e1230636dc1db8a2b6222c543a` and
  `e76e0735f6cc3eb2eecb05eeac06880d4a525b6c`
- **Superseding candidate selector:** `HEAD` after the local PR2 candidate
  commit; resolve its immutable SHA with `git rev-parse HEAD` during review.
- **Scope:** merged PR1 architecture/process contract plus PR2 serving-subject
  identity only. PR2 adds no qualification scope/context, evidence, verdict,
  provider execution, deployment, or traffic functionality.
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
| PR1 | Architecture, vocabulary, and threat contract | v0.4 baseline | MERGED | PR #157; branch head `ca96e6e737402fe3fcbea990f5ac411e5cb6105c`; merge `2a6ce7b681063b73450bf7a4573dea5dac8314b5`; PR CI `33363876409`; main CI `33364429844`. |
| PR2 | Serving-subject identity | PR1 | CANDIDATE | Strict identity mutation/presence coverage, raw-JCS parser checks, golden vector, focused suite, engineering gate, and v0.4 release gate passed locally; Mission Control SHA review is pending. |
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
| `ca96e6e737402fe3fcbea990f5ac411e5cb6105c` | Mission Control and independent MTS | MERGED | PR1 corrections were accepted and merged as PR #157; post-merge main CI `33364429844` was green. |
| `HEAD` after the local PR2 candidate commit | Mission Control | PENDING | Inspect the exact PR2 SHA, digest-only subject boundary, final-review coverage, and local gate evidence before any next milestone. |

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

## PR2 evidence record

| Item | Record |
| --- | --- |
| Decision | Add only immutable serving-subject identity. It is distinct from future qualification scope/context, evidence, proofability, verdict, validity, deployment, and traffic authority. |
| Changed files | `src/exitspec/serving_subject.py`; `tests/test_serving_subject.py`; `tests/fixtures/serving_subject/v1/golden.json`; `docs/V0_5_QUALIFICATION_GATE_PLAN.md`; this ledger; planning-contract test. |
| Schema and identity | `exitspec.serving-subject-manifest.v1`; RFC 8785 JCS; unsigned projection excludes only `subject_digest`; domain separator bytes `exitspec-serving-subject-manifest-v1\x00`; output format `sha256:<64 lowercase hex>`. |
| Material boundary | Pinned model/tokenizer revisions; exact engine/profile/adapter versions; required explicit-null optional artifact/routing fields; runtime configuration; required `launch_arguments_digest`; hardware; profile/adapter; and all-or-none routing identity/digest. Raw launch arguments, workload, scope, evidence, verdict, run ID, provider execution, deployment, and traffic are excluded. |
| Golden vector | `tests/fixtures/serving_subject/v1/golden.json` has raw bytes equal to its JCS serialization and independently derives `sha256:2921dd76c90a5dd4a6131ef8bb7a369f7b4b1a3a829744751e6b38e81dfb988a` from the literal domain separator plus unsigned projection. |
| Focused checks | `/private/tmp/exitspec-b13-venv/bin/python -m pytest -q tests/test_serving_subject.py tests/test_canonical.py tests/test_source_models.py tests/test_performance_contract_models.py tests/test_distribution.py tests/test_engineering_process.py tests/test_v0_4_release_checkpoint.py tests/test_v0_5_planning_contract.py` — passed; Ruff on changed Python and `git diff --check` — passed. |
| Required gates | `EXITSPEC_PYTHON=/private/tmp/exitspec-b13-venv/bin/python EXITSPEC_DIFF_BASE=2a6ce7b681063b73450bf7a4573dea5dac8314b5 ./scripts/engineering_gate.sh` — 3,743 passed, 33 skipped; `EXITSPEC_PYTHON=/private/tmp/exitspec-b13-venv/bin/python ./scripts/v0_4_release_gate.sh` — passed, including 4 Chromium, 17 adversarial, and 4 artifact-reader checks. |
| Remaining risks | A self-consistent subject digest proves identity/integrity only, not authorship, execution, physical hardware truth, chronology, or authority. `launch_arguments_digest` relies on a future separately bounded argument-capture policy. Future milestones must preserve this zero-authority boundary. |
| Reviewer handoff | Candidate is local only. Do not push, open a PR, merge, tag, release, execute a GPU/provider, or authorize deployment/traffic. Mission Control reviews the immutable `HEAD` SHA before PR3. |

## Proposed PR metadata

- **PR2 title:** `feat: add v0.5 serving-subject identity manifest`
- **PR2 body summary:** Adds the strict, immutable `ServingSubjectManifestV1`
  boundary with RFC 8785 JCS serialization, domain-separated digesting,
  canonical raw-byte parsing, explicit optional-field presence, a digest-only
  launch-argument identity, and a bounded denylisted runtime configuration.
  Includes a checked-in golden vector and adversarial mutation, bypass, and
  content-safety coverage. It does not add workload/scope, evidence, verdict,
  provider execution, deployment, or traffic authority.
- **PR2 evidence note:** Focused checks, the full engineering gate, and the
  v0.4 release gate passed locally; this candidate is pending Mission Control
  review only.
- **Historical PR1 title:** `docs: freeze v0.5 provider-neutral qualification execution contract`
- **Historical PR1 evidence note:** The merged PR1 candidate incorporated
  independent review corrections, preserved above, including the valid GitHub
  Actions permissions contract.
