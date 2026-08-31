# ExitSpec v0.5 execution ledger

Status: durable PR-train state for the ExitSpec-only qualification-gate train.
Last updated: PR1–PR3 are merged; the local PR4 provider-neutral
producer-capability candidate is prepared for Mission Control review after all
required local gates passed.

## Train controls

- **Authoritative plan:** [V0_5_QUALIFICATION_GATE_PLAN.md](V0_5_QUALIFICATION_GATE_PLAN.md)
- **Operating procedure:** [V0_5_EXECUTION_RUNBOOK.md](V0_5_EXECUTION_RUNBOOK.md)
- **PR1 base revision:** `2a6ce7b681063b73450bf7a4573dea5dac8314b5` (PR #157 merge)
- **PR3 base revision:** `edb62a071d68a9281e6127ee8ade51f7f23daa02` (PR #158 merge)
- **PR4 base revision:** `101dabbadd1d986f38b56794633ec9e45cea9ac1` (PR #159 merge)
- **Rejected candidate history:**
  `78fe2cdae5fcb4e1230636dc1db8a2b6222c543a` and
  `e76e0735f6cc3eb2eecb05eeac06880d4a525b6c`; PR2 candidate
  `426c792c35ed5ea212b9cdedcbb58612e3f581ab` and
  `b473b8bae5644aa8ef7ef5dcb02119230efe8c72`; PR3 candidate
  `6181bef889ccc99641e8a49784f4bbf31d05724d`
- **Candidate selector:** `HEAD` after the local PR4 candidate commit; resolve
  its immutable SHA with `git rev-parse HEAD` during review.
- **Scope:** merged PR1 architecture/process contract and PR2 serving-subject
  identity plus PR3 qualification scope/context. PR4 adds only the server-owned
  provider-neutral capability descriptor/registry; it adds no proofability,
  evidence, verdict, execution, deployment, or traffic functionality.
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
| PR2 | Serving-subject identity | PR1 | MERGED | PR #158; reviewed head `00b4f01c27eabac37a63adb1015d8e1434113009`; merge `edb62a071d68a9281e6127ee8ade51f7f23daa02`; PR CI `33415971409`; [MTS attestation](https://github.com/jayeshsuyal/ExitSpec/pull/158#issuecomment-5481583707); post-merge main CI `33416637002`, all four jobs green. |
| PR3 | Qualification scope and context | PR2 | MERGED | PR #159; reviewed head `7da388ecfb83c2262a4f30d161a272f674839826`; merge `101dabbadd1d986f38b56794633ec9e45cea9ac1`; PR CI `33423877517`; [MTS attestation](https://github.com/jayeshsuyal/ExitSpec/pull/159#issuecomment-5482615994); post-merge main CI `33424573497`, all four jobs green. |
| PR4 | Producer capability descriptor | PR3 | CANDIDATE | Strict package-owned provider-neutral descriptor/registry, byte-exact golden vector, and adversarial no-expansion boundary are verified locally. Mission Control exact-SHA review is pending. |
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
| `426c792c35ed5ea212b9cdedcbb58612e3f581ab` | Mission Control | `CHANGES_REQUIRED` | P1 — runtime-config deny pairs were checked only within an individual key and did not reject accumulated nested paths such as `api`/`key`, `private`/`key`, or `gpu`/`reservation`. P2 — render the Markdown domain separator with one literal `\x00`, not a doubled slash. Preserve this candidate and supersede it locally. |
| `b473b8bae5644aa8ef7ef5dcb02119230efe8c72` | Mission Control | `CHANGES_REQUIRED` | P1 — the compact fallback joined the entire accumulated path, incorrectly rejecting harmless multi-key split paths such as `a`/`pi`/`k`/`ey` and `gpu`/`re`/`servation`. Preserve real nested denied pairs, but scope compact matching to one key's segments. Preserve this candidate and supersede it locally. |
| `00b4f01c27eabac37a63adb1015d8e1434113009` | Mission Control and independent MTS | MERGED | PR2 review accepted. Merged as PR #158 at `edb62a071d68a9281e6127ee8ade51f7f23daa02`; PR CI `33415971409`; [MTS attestation](https://github.com/jayeshsuyal/ExitSpec/pull/158#issuecomment-5481583707); post-merge main CI `33416637002`, all four jobs green. |
| `6181bef889ccc99641e8a49784f4bbf31d05724d` | Mission Control | `CHANGES_REQUIRED` | P2 — every material context identity leaf needs a valid mutation vector. Subject/scope drift and protocol-version mutation were covered, but a distinct valid `protocol_id` with unchanged subject, scope, and protocol version was not shown to change `qualification_context_digest`. Preserve this candidate unchanged and supersede it locally. |
| `7da388ecfb83c2262a4f30d161a272f674839826` | Mission Control and independent MTS | MERGED | PR3 review accepted and merged as PR #159 at `101dabbadd1d986f38b56794633ec9e45cea9ac1`; PR CI `33423877517`; [MTS attestation](https://github.com/jayeshsuyal/ExitSpec/pull/159#issuecomment-5482615994); post-merge main CI `33424573497`, all four jobs green. |

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
| Mission Control correction | Preserve `426c792c35ed5ea212b9cdedcbb58612e3f581ab` as `CHANGES_REQUIRED`. The superseding implementation accumulates normalized key segments through nested objects and arrays before evaluating prohibited segments/pairs; it adds split, deeper, case, dash, dot, camel-case, harmless-key, and content-safe-error coverage. The plan now renders one literal `\x00` in the Markdown code span. |
| Second Mission Control correction | Preserve `b473b8bae5644aa8ef7ef5dcb02119230efe8c72` as `CHANGES_REQUIRED`. Accumulated paths now reject only exact denied segments and exact adjacent denied pairs; the unseparated `apikey`, `privatekey`, and `gpureservation` fallback inspects only the current key's segments. Positive regressions admit `a`/`pi`/`k`/`ey` and `gpu`/`re`/`servation`, while hostile split, deeper, case, dash, dot, camel-case, and non-echoing error cases remain rejected. |
| Focused checks | `/private/tmp/exitspec-b13-venv/bin/python -m pytest -q tests/test_serving_subject.py tests/test_canonical.py tests/test_source_models.py tests/test_performance_contract_models.py tests/test_distribution.py tests/test_engineering_process.py tests/test_v0_4_release_checkpoint.py tests/test_v0_5_planning_contract.py` — passed; Ruff on changed Python and `git diff --check` — passed. |
| Required gates | `EXITSPEC_PYTHON=/private/tmp/exitspec-b13-venv/bin/python EXITSPEC_DIFF_BASE=2a6ce7b681063b73450bf7a4573dea5dac8314b5 ./scripts/engineering_gate.sh` — 3,755 passed, 33 skipped; `EXITSPEC_PYTHON=/private/tmp/exitspec-b13-venv/bin/python ./scripts/v0_4_release_gate.sh` — 3,768 passed, 23 skipped, including 4 Chromium, 17 adversarial, and 4 artifact-reader checks. |
| PR2 merge evidence | PR #158 closed PR2 at reviewed head `00b4f01c27eabac37a63adb1015d8e1434113009`; merge `edb62a071d68a9281e6127ee8ade51f7f23daa02`; PR CI `33415971409`; MTS attestation `https://github.com/jayeshsuyal/ExitSpec/pull/158#issuecomment-5481583707`; post-merge main CI `33416637002`, all four jobs green. |
| Remaining risks | A self-consistent subject digest proves identity/integrity only, not authorship, execution, physical hardware truth, chronology, or authority. `launch_arguments_digest` relies on a future separately bounded argument-capture policy. Future milestones must preserve this zero-authority boundary. |
| Reviewer handoff | PR2 is closed. PR3 remains local only: do not push, open a PR, merge, tag, release, execute a GPU/provider, or authorize deployment/traffic. |

## PR3 evidence record

| Item | Record |
| --- | --- |
| Decision | Add only strict immutable qualification scope and context. Scope remains independent from serving-subject identity; context binds their digests and one exact protocol version. |
| Base | `edb62a071d68a9281e6127ee8ade51f7f23daa02` (PR #158 merge). |
| Schema review | Scope carries only a bounded qualification question: typed `CANARY_CONSIDERATION`, maximum traffic percent 1–5, typed contract/workload/environment/profile identity and digests, explicit-null optional freshness policy, and requirement-controlled optional reference subject. It has no authorization-shaped fields. The prospective freshness policy uses `EVIDENCE_CAPTURED_AT` plus a bounded maximum age and does not establish chronology, expiry, or currentness. |
| Mission Control correction | Preserve `6181bef889ccc99641e8a49784f4bbf31d05724d` as `CHANGES_REQUIRED`. Add a direct valid `protocol_id` mutation vector with unchanged subject digest, scope digest, and protocol version; group it with the valid protocol-version vector so every material context identity leaf is explicit. Preserve canonical-object/no-concatenation coverage; do not alter the schema unless the test exposes a defect. |
| Changed files | `src/exitspec/qualification_scope.py`; `tests/test_qualification_scope.py`; `tests/fixtures/qualification_scope/v1/golden-scope.json`; `tests/fixtures/qualification_scope/v1/golden-context.json`; `docs/V0_5_QUALIFICATION_GATE_PLAN.md`; this ledger; planning-contract test. |
| Schema and identity | `exitspec.qualification-scope.v1` and `exitspec.qualification-context.v1`; RFC 8785 JCS; unsigned projections exclude only `scope_digest` or `qualification_context_digest`; domain separator bytes `exitspec-qualification-scope-v1\x00` and `exitspec-qualification-context-v1\x00`; every digest is `sha256:<64 lowercase hex>`. |
| Golden vectors | Scope fixture raw bytes equal JCS and independently derive `sha256:5db651e8c2eae05147d2c5fc52bae0b4526ed84508f76d62d41471ac4ca677ab`; context fixture raw bytes equal JCS and independently derives `sha256:9159ac21169d0674b916053e6605a72f6f25e65cfe94b30b708a86f343d0193c`. |
| Focused checks | `/private/tmp/exitspec-b13-venv/bin/python -m pytest -q tests/test_qualification_scope.py tests/test_serving_subject.py tests/test_canonical.py tests/test_source_models.py tests/test_performance_contract_models.py tests/test_distribution.py tests/test_engineering_process.py tests/test_v0_4_release_checkpoint.py tests/test_v0_5_planning_contract.py` — passed; Ruff on changed Python and `git diff --check` — passed. |
| Required gates | `EXITSPEC_PYTHON=/private/tmp/exitspec-b13-venv/bin/python EXITSPEC_DIFF_BASE=edb62a071d68a9281e6127ee8ade51f7f23daa02 ./scripts/engineering_gate.sh` — 3,799 passed, 33 skipped; `EXITSPEC_PYTHON=/private/tmp/exitspec-b13-venv/bin/python ./scripts/v0_4_release_gate.sh` — 3,812 passed, 23 skipped, including 4 Chromium, 17 adversarial, and 4 artifact-reader checks. |
| PR3 merge evidence | PR #159 closed PR3 at reviewed head `7da388ecfb83c2262a4f30d161a272f674839826`; merge `101dabbadd1d986f38b56794633ec9e45cea9ac1`; PR CI `33423877517`; [MTS attestation](https://github.com/jayeshsuyal/ExitSpec/pull/159#issuecomment-5482615994); post-merge main CI `33424573497`, all four jobs green. |
| Remaining risks | Scope/context self-consistency proves deterministic identity/integrity only, not execution, authorship, chronology, physical hardware truth, authenticated identity, evidence admission, verdict, or validity. The prospective `EVIDENCE_CAPTURED_AT` age-policy basis establishes no capture time, expiry, or currentness before later protocol work. |
| Reviewer handoff | PR3 is closed. PR4 is local only: do not push, open a PR, merge, tag, release, execute a GPU/provider, or authorize deployment/traffic. |

## PR4 evidence record

| Item | Record |
| --- | --- |
| Decision | Add only a strict immutable package-owned provider-neutral producer-capability descriptor and exact-profile registry request boundary. |
| Base | `101dabbadd1d986f38b56794633ec9e45cea9ac1` (PR #159 merge). |
| Frozen schema | `exitspec.producer-capability-descriptor.v1`, `exitspec.producer-capability-request.v1`, and `exitspec.producer-capability-registry.v1`; RFC 8785 JCS; unsigned projection excludes only `capability_digest`; domain separator bytes `exitspec-producer-capability-descriptor-v1\x00`; every digest is `sha256:<64 lowercase hex>`. |
| Capability boundary | The one ExitSpec-owned profile advertises only native `vllm_first_choices_event_v0_26` TTFT samples, `ns`, `successful_measured_requests_with_observed_ttft`, `nearest_rank_v1` p95, and fixed measured-request reliability observations. `first_nonempty_choices_delta_content_v1` is absent and unsupported. |
| Trust boundary | Registry content is package-owned. Callers may submit only an exact profile ID/version request; unknown, aliased, malformed, duplicate, extra, oversized, unsupported-version, noncanonical, override, and merge attempts fail closed. No network, API, browser, execution, evidence admission, proofability, verdict, receipt, deployment, or traffic path is added. |
| Scope correction | The initial PR4 wording was corrected before candidate creation: the train remains provider-neutral. No provider-specific profile, dependency, documentation, schema, or existing implementation change is carried into PR4. |
| Changed files | `src/exitspec/producer_capability.py`; `tests/test_producer_capability.py`; `tests/fixtures/producer_capability/v1/golden.json`; `docs/V0_5_QUALIFICATION_GATE_PLAN.md`; this ledger; planning-contract test. |
| Golden vector | `tests/fixtures/producer_capability/v1/golden.json` has raw bytes equal to its JCS serialization and independently derives `sha256:e599ce28eeadf9d1ce1aa65486547be636b17c911cd2a2e674905c52bf4b8435` from the literal domain separator plus unsigned projection. |
| Focused checks | `/private/tmp/exitspec-b13-venv/bin/python -m pytest -q tests/test_producer_capability.py tests/test_canonical.py tests/test_serving_subject.py tests/test_qualification_scope.py tests/test_source_models.py tests/test_performance_contract_models.py tests/test_distribution.py tests/test_engineering_process.py tests/test_v0_4_release_checkpoint.py tests/test_v0_5_planning_contract.py` — passed; Ruff on changed Python/tests, `git diff --check`, provider-neutral planning-document scan, and provider-specific module-dependency scan — passed. |
| Required gates | `EXITSPEC_PYTHON=/private/tmp/exitspec-b13-venv/bin/python EXITSPEC_DIFF_BASE=101dabbadd1d986f38b56794633ec9e45cea9ac1 ./scripts/engineering_gate.sh` — 3,840 passed, 33 skipped; `EXITSPEC_PYTHON=/private/tmp/exitspec-b13-venv/bin/python ./scripts/v0_4_release_gate.sh` — 3,853 passed, 23 skipped, including 4 mandatory Chromium, 4 B13 Chromium, 17 adversarial, and 4 artifact-reader checks. |
| Remaining risks | A self-consistent descriptor proves only ExitSpec's declared planning capability, not producer execution, hardware truth, evidence, chronology, authorship, provider identity, verdict, receipt, or authority. |
| Reviewer handoff | CANDIDATE is local only. Do not push, open a PR, merge, tag, release, execute a GPU/provider, or authorize deployment/traffic. Mission Control reviews the immutable `HEAD` SHA before PR5. |

## Proposed PR metadata

- **PR2 title:** `feat: add v0.5 serving-subject identity manifest`
- **PR2 body summary:** Adds the strict, immutable `ServingSubjectManifestV1`
  boundary with RFC 8785 JCS serialization, domain-separated digesting,
  canonical raw-byte parsing, explicit optional-field presence, a digest-only
  launch-argument identity, and a bounded denylisted runtime configuration.
  Includes a checked-in golden vector and adversarial mutation, bypass, and
  content-safety coverage. It does not add workload/scope, evidence, verdict,
  provider execution, deployment, or traffic authority.
- **Historical PR2 evidence note:** Focused checks, the full engineering gate,
  and the v0.4 release gate passed locally; the reviewed candidate was then
  accepted and merged as PR #158 above.
- **PR3 title:** `feat: add v0.5 qualification scope and context`
- **PR3 body summary:** Adds strict immutable `QualificationScopeV1` and
  `QualificationContextV1` boundaries with RFC 8785 JCS, domain-separated
  digests, byte-exact golden vectors, explicit optional presence, and distinct
  subject/scope drift coverage. Scope expresses only bounded 1–5% canary
  consideration and contains no authorization field; this change adds no
  proofability, evidence, verdict, provider execution, deployment, or traffic
  authority.
- **PR4 title:** `feat: add v0.5 producer capability registry`
- **PR4 body summary:** Adds one strict immutable package-owned,
  provider-neutral producer-capability descriptor for an exact declared
  external-evidence profile. It exposes only native vLLM 0.26 first-event TTFT
  semantics and measured-request reliability observations through canonical
  JCS, a domain-separated digest, and a closed registry request boundary. It
  adds no execution, evidence admission, proofability, verdict, receipt,
  provider integration, deployment, or traffic authority.
- **Historical PR1 title:** `docs: freeze v0.5 provider-neutral qualification execution contract`
- **Historical PR1 evidence note:** The merged PR1 candidate incorporated
  independent review corrections, preserved above, including the valid GitHub
  Actions permissions contract.
