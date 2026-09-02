# ExitSpec v0.5 execution ledger

Status: durable PR-train state for the ExitSpec-only qualification-gate train.
Last updated: PR1–PR9 are merged. Rejected PR5 r1
`a36c09450776c13342200aadd34a891bd4502c06` and r2
`4a4decd69f613c302d77280debc6c2b746f0df1b`, plus rejected r3
`5c63ab581e497c64bdce8e8e44f8212fa7d2f922`, are immutable
`CHANGES_REQUIRED` / `MTS_FAIL` history and remain preserved unchanged. PR5 r4
was accepted and merged as PR #161 at
`867f4ac9d29376ab5130864f5a2d39bb946bb447`. PR6 was accepted and merged as
PR #162 at `475b965309b77b1cab55fdf29d391b02851a695f`; its post-merge main
workflow was green. PR7 was accepted and merged as PR #163 at
`8b1ac77f6d56a60ffe1df3fa8034302357f4511d`; its PR and post-merge main
workflows were green. PR8 was accepted and merged as PR #164 at
`52b7fad3815099f67cf585c565f5d380f852a384`; its PR and post-merge main
workflows were green. PR9 was accepted and merged as PR #165 at
`050fe4407337d4b443e577c795a37ec2bd1f51b0`; its PR workflow is green and its
post-merge main workflow is in progress. PR10 is the current isolated candidate
and has not been pushed, reviewed, merged, released, deployed, or authorized.

## Train controls

- **Authoritative plan:** [V0_5_QUALIFICATION_GATE_PLAN.md](V0_5_QUALIFICATION_GATE_PLAN.md)
- **Operating procedure:** [V0_5_EXECUTION_RUNBOOK.md](V0_5_EXECUTION_RUNBOOK.md)
- **PR1 base revision:** `2a6ce7b681063b73450bf7a4573dea5dac8314b5` (PR #157 merge)
- **PR3 base revision:** `edb62a071d68a9281e6127ee8ade51f7f23daa02` (PR #158 merge)
- **PR4 base revision:** `101dabbadd1d986f38b56794633ec9e45cea9ac1` (PR #159 merge)
- **PR5 base revision:** `1c5fe7960d5464fd40ae21b1a73a841ca0cbf27c` (PR #160 merge)
- **PR6 base revision:** `867f4ac9d29376ab5130864f5a2d39bb946bb447` (PR #161 merge)
- **PR7 base revision:** `475b965309b77b1cab55fdf29d391b02851a695f` (PR #162 merge)
- **PR8 base revision:** `8b1ac77f6d56a60ffe1df3fa8034302357f4511d` (PR #163 merge)
- **PR9 base revision:** `52b7fad3815099f67cf585c565f5d380f852a384` (PR #164 merge)
- **PR10 base revision:** `050fe4407337d4b443e577c795a37ec2bd1f51b0` (PR #165 merge)
- **Rejected candidate history:**
  `78fe2cdae5fcb4e1230636dc1db8a2b6222c543a` and
  `e76e0735f6cc3eb2eecb05eeac06880d4a525b6c`; PR2 candidate
  `426c792c35ed5ea212b9cdedcbb58612e3f581ab` and
  `b473b8bae5644aa8ef7ef5dcb02119230efe8c72`; PR3 candidate
  `6181bef889ccc99641e8a49784f4bbf31d05724d`; PR4 candidate
  `5baa09e96075d94e730941cf3673f1047cb11818` (Mission Control
  `CHANGES_REQUIRED`); PR5 candidate
  `a36c09450776c13342200aadd34a891bd4502c06` (independent MTS
  `CHANGES_REQUIRED` / `MTS_FAIL`) and
  `4a4decd69f613c302d77280debc6c2b746f0df1b` (independent MTS
  `CHANGES_REQUIRED` / `MTS_FAIL`), plus
  `5c63ab581e497c64bdce8e8e44f8212fa7d2f922` (independent MTS
  `CHANGES_REQUIRED` / `MTS_FAIL`), all preserved unchanged.
- **Candidate selector:** the one local-only PR6 candidate becomes immutable
  `HEAD` after its single candidate commit; reviewers resolve its SHA with `git
  rev-parse HEAD`. No remote action is implied by this selector.
- **Scope:** merged PR1 architecture/process contract, PR2 serving-subject
  identity, PR3 qualification scope/context, and PR4 provider-neutral
  capability descriptor/registry, and merged PR5 deterministic proofability
  planning. PR6 adds only a bounded process-local package-synthetic preflight
  API/browser projection; it adds no agreement, execution, evidence, verdict,
  validity, deployment, or traffic functionality.
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
| PR4 | Producer capability descriptor | PR3 | MERGED | PR #160; reviewed head `7e1268373da3fea8cf441b7ad7d515df8af8f2f5`; merge `1c5fe7960d5464fd40ae21b1a73a841ca0cbf27c`; PR CI `33435286412`; [MTS attestation](https://github.com/jayeshsuyal/ExitSpec/pull/160#issuecomment-5484101232); post-merge main CI `33436107791`, all four jobs green. |
| PR5 | Proofability engine | PR4 | MERGED | PR #161; accepted r4 head `424aeae8a959f4249a35375141fd2c365bc68b71`; merge `867f4ac9d29376ab5130864f5a2d39bb946bb447`. Immutable rejected r1–r3 history remains preserved below. |
| PR6 | Proofability service and workspace projection | PR5 | MERGED | PR #162; merge `475b965309b77b1cab55fdf29d391b02851a695f`; post-merge main workflow `33588525253` green. |
| PR7 | Provider-neutral prospective handoff boundary | PR3, PR5 | MERGED | PR #163; merge `8b1ac77f6d56a60ffe1df3fa8034302357f4511d`; PR workflow `33612282828` and post-merge main workflow `33612909140` green. |
| PR8 | Provider-neutral external-evidence admission boundary | PR7 | MERGED | PR #164; merge `52b7fad3815099f67cf585c565f5d380f852a384`; PR workflow `33614338612` green; post-merge main workflow `33615038495` green. |
| PR9 | Inference-performance qualification receipt | PR8 | MERGED | PR #165; merge `050fe4407337d4b443e577c795a37ec2bd1f51b0`; PR workflow `33617012138` green; post-merge main workflow `33617659852` pending verification. |
| PR10 | Qualification validity and staleness | PR9 | CANDIDATE | Isolated candidate assesses exact subject/scope/context applicability and declared freshness without mutating receipt history; drift, unsupported protocol, malformed input, and expiry fail closed. |
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
| `7e1268373da3fea8cf441b7ad7d515df8af8f2f5` | Mission Control and independent MTS | MERGED | PR4 recursive-model-graph, bounded-mapping, exact reliability-source, and hostile self-consistent replacement corrections were accepted and merged as PR #160 at `1c5fe7960d5464fd40ae21b1a73a841ca0cbf27c`; PR CI `33435286412`; [MTS attestation](https://github.com/jayeshsuyal/ExitSpec/pull/160#issuecomment-5484101232); post-merge main CI `33436107791`, all four jobs green. |
| `a36c09450776c13342200aadd34a891bd4502c06` | independent MTS | `CHANGES_REQUIRED` / `MTS_FAIL` | Immutable r1 history. P1 — a digest-valid `NOT_PROVABLE` result could omit an unavailable required observation from both missing and incompatible accounting; P1 — one required observation could be classified as both missing and incompatible. The same boundary also accepted a cherry-picked subset of the actual closed incompatibility reasons. Preserve this candidate unchanged and replace it with one new commit whose sole parent is the exact PR5 base. |
| `4a4decd69f613c302d77280debc6c2b746f0df1b` | independent MTS | `CHANGES_REQUIRED` / `MTS_FAIL` | Immutable r2 history. The exact-subject engine was not bound to the registered descriptor engine; nonempty Pydantic private state, altered field-set state, and primitive subclasses survived lossy dumps; and a valid 12-result evaluator output exceeded the 512-node parser bound. Authoritative report: `/private/tmp/exitspec-pr5-r2-mts-report.txt`, SHA-256 `06d13e92592d16fbb1b07f2bb01a2a3b5308cc85b7e3557d34ed2ec904c9f9eb`. Preserve r2 unchanged and supersede it from the exact PR5 base. |

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
| Mission Control rejection | Candidate `5baa09e96075d94e730941cf3673f1047cb11818` is `CHANGES_REQUIRED` and remains immutable history. P1: a nested strict-model bypass let hidden `__dict__` or Pydantic-extra state, and exact-field subclasses, survive verification and a lossy serialization; P1: a cyclic mapping reached raw `RecursionError` before canonicalization. P2: measured-attempt reliability named an outcome without binding exact raw source `request.outcome.status`; P2: tests did not prove hostile self-consistent capability replacements reject before use. |
| Superseding correction | Require an exact recursive raw model graph before projection/digest/verify/serialize; bound direct mappings for cycles, depth, nodes, keys, items, and strings before canonicalization; add the closed reliability source field `request.outcome.status`; and adversarially recompute hostile replacement digests before confirming fail-closed parsing. |
| Changed files | `src/exitspec/producer_capability.py`; `tests/test_producer_capability.py`; `tests/fixtures/producer_capability/v1/golden.json`; `docs/V0_5_QUALIFICATION_GATE_PLAN.md`; this ledger; planning-contract test. |
| Golden vector | `tests/fixtures/producer_capability/v1/golden.json` has raw bytes equal to its JCS serialization and independently derives `sha256:e599ce28eeadf9d1ce1aa65486547be636b17c911cd2a2e674905c52bf4b8435` from the literal domain separator plus unsigned projection. |
| Superseding golden vector | The replacement vector adds the closed measured-attempt source field `request.outcome.status`; its raw bytes must equal JCS exactly and independently derive `sha256:1b8732d26a94dadfab984b43a4c67c1fc858ddf39f95ec496f5914f1c08e066b` from the literal domain separator plus unsigned projection. |
| Focused checks | `/private/tmp/exitspec-b13-venv/bin/python -m pytest -q tests/test_producer_capability.py tests/test_canonical.py tests/test_serving_subject.py tests/test_qualification_scope.py tests/test_source_models.py tests/test_performance_contract_models.py tests/test_distribution.py tests/test_engineering_process.py tests/test_v0_4_release_checkpoint.py tests/test_v0_5_planning_contract.py` — passed; Ruff on changed Python/tests, `git diff --check`, provider-neutral planning-document scan, and provider-specific module-dependency scan — passed. |
| Required gates | `EXITSPEC_PYTHON=/private/tmp/exitspec-b13-venv/bin/python EXITSPEC_DIFF_BASE=101dabbadd1d986f38b56794633ec9e45cea9ac1 ./scripts/engineering_gate.sh` — 3,840 passed, 33 skipped; `EXITSPEC_PYTHON=/private/tmp/exitspec-b13-venv/bin/python ./scripts/v0_4_release_gate.sh` — 3,853 passed, 23 skipped, including 4 mandatory Chromium, 4 B13 Chromium, 17 adversarial, and 4 artifact-reader checks. |
| Superseding validation | `PYTHONPATH=src /private/tmp/exitspec-b13-venv/bin/python -m pytest -q tests/test_producer_capability.py tests/test_canonical.py tests/test_serving_subject.py tests/test_qualification_scope.py tests/test_source_models.py tests/test_performance_contract_models.py tests/test_distribution.py tests/test_engineering_process.py tests/test_v0_4_release_checkpoint.py tests/test_v0_5_planning_contract.py` — 295 passed; Ruff on changed Python/tests and `git diff --check` — passed. Exact-base engineering and v0.4 release gates also passed on the replacement bytes before the new local candidate was created. |
| Superseding required gates | `EXITSPEC_PYTHON=/private/tmp/exitspec-b13-venv/bin/python EXITSPEC_DIFF_BASE=101dabbadd1d986f38b56794633ec9e45cea9ac1 ./scripts/engineering_gate.sh` — 3,866 passed, 33 skipped, including built-wheel distribution 16 passed; `EXITSPEC_PYTHON=/private/tmp/exitspec-b13-venv/bin/python ./scripts/v0_4_release_gate.sh` — 3,879 passed, 23 skipped, including 4 mandatory Chromium, 4 B13 Chromium, 17 adversarial, and 4 artifact-reader checks. |
| Remaining risks | A self-consistent descriptor proves only ExitSpec's declared planning capability, not producer execution, hardware truth, evidence, chronology, authorship, provider identity, verdict, receipt, or authority. |
| PR4 merge evidence | PR #160 closed the reviewed head `7e1268373da3fea8cf441b7ad7d515df8af8f2f5` at merge `1c5fe7960d5464fd40ae21b1a73a841ca0cbf27c`; PR CI `33435286412`; MTS attestation `https://github.com/jayeshsuyal/ExitSpec/pull/160#issuecomment-5484101232`; post-merge main CI `33436107791`, all four jobs green. |
| Reviewer handoff | PR4 is closed. PR5 remains local-only until a later green candidate; do not push, open a PR, merge, tag, release, execute a GPU/provider, or authorize deployment/traffic. |

## PR5 evidence record

| Item | Record |
| --- | --- |
| Decision | Add only deterministic provider-neutral proofability planning for exact frozen subject, scope, context, contract, and registered descriptor inputs. |
| Base | `1c5fe7960d5464fd40ae21b1a73a841ca0cbf27c` (PR #160 merge). |
| State | `CANDIDATE` local-only. Rejected r1 `a36c09450776c13342200aadd34a891bd4502c06`, r2 `4a4decd69f613c302d77280debc6c2b746f0df1b`, and r3 `5c63ab581e497c64bdce8e8e44f8212fa7d2f922` remain separately reachable, byte-identical `CHANGES_REQUIRED` / `MTS_FAIL` history. Reviewers resolve the corrected r4 candidate from immutable `HEAD`; no MTS pass is claimed. |
| Frozen criterion | `InferenceQualificationCriterionV1`: exact `inference_qualification_v1`, `exitspec.inference-qualification-criterion.v1`, and `inference-performance-qualification` / `1.0.0`; discriminated native or semantic TTFT requirement plus exact measured-attempt reliability requirement. Thresholds/counts are prospective and hash-material only. |
| Report identity | `ProofabilityReportV1`; RFC 8785 JCS; unsigned projection excludes only `proofability_report_digest`; literal domain separator `exitspec-proofability-report-v1\x00`; output `sha256:<64 lowercase hex>`. Test-only raw vector `tests/fixtures/proofability/v1/golden.json` must exactly equal JCS and independently derive `sha256:28c49bba2dd3791905a201a74777c9994e6ecc083cc3b9de083095f4c626d81e`. |
| Pre-candidate semantic correction | Before the rejected candidate was created, self-consistent but internally contradictory criterion results were made parse failures. `PROVABLE`, `CLARIFICATION_REQUIRED`, and `NOT_PROVABLE` received tuple/reason/remediation invariants, but the first version did not make the required-observation accounting exhaustive and disjoint. |
| Rejected-candidate focused checks | Candidate `a36c09450776c13342200aadd34a891bd4502c06`: the required 16-file focused PR5 suite — 505 passed in 7.18s. Changed-file Ruff, narrow `models.py` import-order Ruff, `git diff --check`, provider-neutral scans, and raw-golden-byte check passed. This evidence does not approve the rejected candidate. |
| Rejected-candidate required gates | Candidate `a36c09450776c13342200aadd34a891bd4502c06`: exact-base engineering — 3,919 passed, 33 skipped, log `/private/tmp/exitspec-pr5-engineering-gate-final.log`; mandatory v0.4 release gate — 3,932 passed, 23 skipped, log `/private/tmp/exitspec-pr5-v0_4-release-gate-final.log`. These green gates were superseded by the independent MTS P1 decision. |
| Independent MTS rejection | Exact stdlib-rehashed attacks demonstrated omitted accounting (`sha256:c01606ff8770340309ad7a74d06e9018ba344ee920cbe24d910307e1384ec44f`) and missing-plus-incompatible double classification (`sha256:1116305d43582e265bee40079de622f3892b2b95a4b74109ac9a2b0b1b2b6b03`). The r2 suite also fixes complete-reason double classification (`sha256:5337d34f3973add09ab8213fe6ab9ed29edf5ffe564418bb857122bba058bd65`), exact-available-plus-incompatible (`sha256:577846e372ff2ade7c05a3472c308c96b7c6157aaae9bae16db0fe39077bb28c`), and incomplete actual reasons (`sha256:5401528ac536f3cf064f28d69d1e129d79ebd8a48861c45e86aae1772baa751e`). |
| r2 partition correction | For each full required observation model, exact availability, exact missing membership, and any incompatible row with that full required model form a complete, mutually exclusive partition. Reduced kind/ID keys do not define availability. Every incompatible pair must enumerate the complete canonical set returned by `_incompatibility_reason_codes`. A coherent semantic/native replacement with both actual reasons (`sha256:afd6ef64a481f78a99c25135470acc2aa0ba5cee5a9055c3b34a20c73876babf`) remains parseable but fails verification against the same frozen semantic contract/context. Extra descriptor-available observations remain visible. |
| r2 focused checks | The complete 16-file PR5 suite passed 510 tests in 7.22s; log `/private/tmp/exitspec-pr5-r2-focused.log`. The byte-exact golden test independently recomputed the digest with stdlib JSON plus `hashlib` and the literal domain. Changed-file Ruff, narrow `models.py`/distribution import and fatal checks, `git diff --check`, provider-neutral document/module scans, and the independent read-only invariant audit passed. |
| r2 required gates | Exact-base engineering: `EXITSPEC_PYTHON=/private/tmp/exitspec-b13-venv/bin/python EXITSPEC_DIFF_BASE=1c5fe7960d5464fd40ae21b1a73a841ca0cbf27c ./scripts/engineering_gate.sh` — 3,924 passed, 33 skipped, including built-wheel distribution 16 passed; log `/private/tmp/exitspec-pr5-r2-engineering-gate.log`. Mandatory v0.4 release gate: `EXITSPEC_PYTHON=/private/tmp/exitspec-b13-venv/bin/python ./scripts/v0_4_release_gate.sh` — 3,937 passed, 23 skipped, including 4 mandatory Chromium, 4 B13 Chromium, 17 adversarial, and 4 direct artifact-reader checks; log `/private/tmp/exitspec-pr5-r2-v0_4-release-gate.log`. |
| r2 independent MTS rejection | The authoritative report `/private/tmp/exitspec-pr5-r2-mts-report.txt` has SHA-256 `06d13e92592d16fbb1b07f2bb01a2a3b5308cc85b7e3557d34ed2ec904c9f9eb` and records `MTS_FAIL` despite the full green gates above. Its reproduced findings are exact subject/descriptor engine non-applicability, lossy acceptance of private/field-set/primitive-subclass raw state, and evaluator/parser non-closure beginning at 12 ordinary native results. |
| r2 ledger finalization | The full-gate logs cover the corrected product, plan, regression-test, and provisional ledger bytes. Afterward, only this truthful ledger candidate/evidence text and its planning-contract assertions changed; the exact planning/process/document, Ruff, whitespace, and provider-neutral checks are rerun before commit. |
| r3 subject-engine applicability | After independently validating all five inputs, require exact subject `engine_id` and `engine_version` equality with the descriptor engine adapter before mapping. A valid regenerated `tgi` / `1.2.3` subject and context reject with content-safe `CAPABILITY_BINDING_MISMATCH` against the registered `vllm` / `0.26.0` descriptor, and verification is false. No registered evidence-profile/adapter equality with the subject serving-profile adapter is invented. |
| r3 raw typed-graph closure | The report and all five inputs plus every nested typed model now reject noncanonical `__pydantic_private__`, `__pydantic_fields_set__`, Pydantic extra state, extra/missing raw fields, model/container/primitive/enum/`datetime` type confusion, subclasses, mutable raw containers, and cycles before lossy projection. The exact original graph is compared recursively with an independently strict canonical round-trip by node type and value; attack content is not echoed. |
| r3 64-result closure | The frozen raw limits are 1,048,576 bytes and 16,384 JSON nodes. Independently counted maximum-ID-width 64-result evaluator outputs are 2,773 nodes / 102,194 bytes native, 3,349 / 127,286 semantic, 1,749 / 63,936 legacy, and 2,626 / 97,878 mixed; all serialize, parse, and verify. Evaluation enforces serialization and parsing before return. Raw bytes at 1,048,577 and graphs above 16,384 nodes reject `OVERSIZED`. |
| r3 focused checks | The complete historical 16-file PR5 suite collected 538 tests and passed 538/538. The first sandboxed run reproduced the known infrastructure-only `PermissionError` on an ephemeral `127.0.0.1` bind in the built-wheel distribution test; the identical command passed with local-loopback test permission and no external endpoint. Logs: `/private/tmp/exitspec-pr5-r3-focused.log`, `/private/tmp/exitspec-pr5-r3-focused-rerun.log`, and `/private/tmp/exitspec-pr5-r3-focused-collect.log`. Full configured Ruff passed on the four PR5-clean files; narrow `I,E9,F63,F7,F82` passed on `models.py` and distribution; exact-path, whitespace, golden, provider-neutral, authority, and import scans passed. |
| r3 required gates | Exact-base engineering: `EXITSPEC_PYTHON=/private/tmp/exitspec-b13-venv/bin/python EXITSPEC_DIFF_BASE=1c5fe7960d5464fd40ae21b1a73a841ca0cbf27c ./scripts/engineering_gate.sh` — 3,952 passed, 33 skipped, including built-wheel distribution 16 passed; log `/private/tmp/exitspec-pr5-r3-engineering-gate.log`. Mandatory v0.4 release gate: `EXITSPEC_PYTHON=/private/tmp/exitspec-b13-venv/bin/python ./scripts/v0_4_release_gate.sh` — 3,965 passed, 23 skipped, including mandatory Chromium 4/4, B13 Chromium 4/4, adversarial 17/17, and direct artifact-reader 4/4; log `/private/tmp/exitspec-pr5-r3-v0_4-release-gate.log`. Known post-success Playwright `TargetClosedError` shutdown warnings were non-fatal; all enforced sets and both gate exits were green. |
| r3 ledger finalization | The full gates cover the corrected product, plan, regression tests, and provisional r3 ledger. Afterward only this truthful focused/full-gate evidence text changed; exact planning/process/release-checkpoint tests, scoped Ruff, `git diff --check`, independent golden recomputation, and provider-neutral/authority/import scans are rerun before the one candidate commit. |
| r3 independent MTS rejection | Exact candidate `5c63ab581e497c64bdce8e8e44f8212fa7d2f922` remained local-only and was rejected because the valid frozen `examples/routing-qualification/contracts/routing-qualification-v1.json` contract raised an uncaught Pydantic `ValidationError`: its legitimate `routing_qualification_v1` criterion ID was outside the uppercase-only report grammar. The authoritative report `/private/tmp/exitspec-pr5-r3-mts-report.txt` has SHA-256 `63bdec2dd7454132bf9c66fadde2f854dccc15f8b7b040b5b18431ddffc5a039` and records `MTS_FAIL`. Preserve r3 unchanged and supersede it from the exact PR5 base. |
| r4 finite criterion-ID correction | `CriterionProofabilityV1.criterion_id` now equals the complete existing contract-union language: bounded uppercase `^[A-Z][A-Z0-9-]{2,63}$` for seven arms plus only `routing_qualification_v1`, `routing_slo_attainment_v1`, and `routing_campaign_reduction_v1`. The exact B9 fixture and the B10/B11 frozen synthetic contracts evaluate, serialize, parse, and verify as opaque `CLARIFICATION_REQUIRED` results with unchanged accounting/reason/remediation semantics. Arbitrary lowercase, mixed case, punctuation aliases, near literals, and overlength IDs remain rejected. The uppercase golden bytes and report digest remain unchanged. |
| r4 focused checks | The complete 16-file PR5 suite collected and passed 552/552 tests after the known sandbox-only ephemeral-loopback denial was rerun with local-loopback permission. The exact MTS fixture reproduction passed. The inherited r3 correction probe passed all four 64-result closure cases and bounds, and the independent harness passed 897 assertions with all six historical digest outcomes unchanged. |
| r4 required gates | Exact-base engineering: `EXITSPEC_PYTHON=/private/tmp/exitspec-b13-venv/bin/python EXITSPEC_DIFF_BASE=1c5fe7960d5464fd40ae21b1a73a841ca0cbf27c ./scripts/engineering_gate.sh` — 3,966 passed, 33 skipped, including built-wheel distribution 16 passed. Mandatory v0.4 release gate: `EXITSPEC_PYTHON=/private/tmp/exitspec-b13-venv/bin/python ./scripts/v0_4_release_gate.sh` — 3,979 passed, 23 skipped, including mandatory Chromium 4/4, B13 Chromium 4/4, adversarial 17/17, and direct artifact-reader 4/4. Known post-success Playwright `TargetClosedError` shutdown warnings were non-fatal; all enforced sets and both gate exits were green. |
| r4 ledger finalization | The full gates cover the r4 product and regression bytes plus the pre-final ledger. After this truthful MTS/focused/full-gate evidence update, exact planning/process/release-checkpoint tests, scoped Ruff, whitespace, independent golden recomputation, provider-neutral/authority/import scans, full base-diff inspection, and topology checks are rerun before the one candidate commit. |
| Binding and mapping | Revalidate exact raw graphs and PR2/PR3 digests, exact context protocol/links, frozen contract ID/hash and scope link, package-registered descriptor, and exact subject-engine applicability before mapping. Native is `PROVABLE`; semantic first-nonempty is `NOT_PROVABLE`/`MISSING_OBSERVATION`; legacy union arms remain opaque `CLARIFICATION_REQUIRED`/`UNMAPPABLE_FROZEN_CRITERION_SCHEMA`; report order follows frozen contract order. |
| Explicit non-links | PR5 does not read or derive `POCContract.workload.fixture_path` into scope workload and does not assert scope measurement-profile equality with the descriptor. Correctly regenerated valid scope/context variants remain material. |
| Authority and limits | Report identity/self-consistency proves planning integrity only, not execution, hardware truth, authorship, chronology, authenticated identity, evidence, Verdict, Validity, deployment, traffic, or authority. Parsing is not trusted evaluation; verification re-evaluates original bound inputs. |
| Reviewer handoff | r4 `CANDIDATE` is local-only. Do not push, open a PR, merge, tag, release, execute a GPU/provider, or authorize deployment/traffic. Mission Control and a fresh independent MTS lane must review the final exact r4 `HEAD` SHA before PR6; this ledger does not claim MTS pass. |

## PR6 evidence record

| Item | Record |
| --- | --- |
| Decision | Add only one bounded process-local proofability planning workspace and narrow source-neutral browser projection over the accepted PR5 boundary. |
| Superseded freeze history | Sole parent/base remains `867f4ac9d29376ab5130864f5a2d39bb946bb447`. The former `/private/tmp/exitspec-pr6-mission-control-freeze-v5-final.txt` record and its 14-path packet are rejected historical evidence only; they do not describe or authorize the current candidate. |
| Rejected r2 review | Exact r2 candidate `c1364e86f7ed1b6b25a8ebe30767102cc4431f26` remained local-only. Mission Control rejected it in `/private/tmp/exitspec-pr6-mission-control-review-v10.txt` (SHA-256 `94fd30827369ffa14898495c0672943e703d2ecc4a2e3a6b7b8783ebe6bc862e`) and independent MTS rejected it in `/private/tmp/exitspec-pr6-mts-review-v10.txt` (SHA-256 `58de8e8d0f1c7753e0462ca4a691461f717b797f7e34e904e94b60d07e96c542`) because this ledger described the wrong packet/evidence and the plan overclaimed no-network gate behavior. Preserve r2 and supersede it; it has no push authority. |
| Rejected v11 freeze review | Candidate `ffbebd9f7f9b3736eeb36fe9025aa6df11ad02d3` remained local-only and unchanged while `/private/tmp/exitspec-pr6-mission-control-freeze-v11-final.txt` (SHA-256 `67b23874a008fdee38932badb92dbc8020a4981a9168248745beba28eb0b938d`) was rejected by `/private/tmp/exitspec-pr6-freeze-v11-final-review.txt` (SHA-256 `a9265b7122855026100be5e2663d9d46416d3f8a6d0c9920acef10d78dbadfac`). The freeze failed before formal evidence because it did not disclose or separately authorize the unconstrained-egress-capable runner used by focused loopback/browser checks. It grants no authority. |
| Rejected r5 review | Exact r5 candidate `7d4f3c36fc97a1927774423c8baaaaf0aa083f8d` remained local-only. Its Builder report `/private/tmp/exitspec-pr6-builder-r5-green-report.txt` (SHA-256 `fa0d76770bc03be938030929292e19a8a48ad6a418dde46abbd59fc060239a22`) and independent MTS report `/private/tmp/exitspec-pr6-mts-r5-v17-review.txt` (SHA-256 `70feb42eae29a2728bfccc70b17a4d0f8a5d6a88b33cfeb768e02034540eb280`) were green, but Mission Control rejected it in `/private/tmp/exitspec-pr6-mission-control-r5-v17-review.txt` (SHA-256 `e2ce37c0ef7fb68f57c77e5590e0c705ce97c6a1dddab1c0b114b7d9be5c7e49`) because the exact-packet row incorrectly called the inherited dependency audit newly added and omitted the npm-to-direct-Node correction. Preserve r5 and supersede it from the accepted base; it has no push authority. |
| Controlling correction train | The corrected one-commit candidate must be governed by one fresh immutable exact-byte external freeze plus a separate exact-byte review. The repository does not predict a transient freeze filename or self-embed a future freeze hash. Exact candidate, tree, freeze, review, command, and output paths/hashes belong in `/private/tmp/exitspec-pr6-builder-r6-green-report.txt`. |
| Exact packet | Four production source modules, three static assets, three feature-test files, two regression-test files, three documentation files, and `scripts/engineering_gate.sh`: exactly 16 repository paths. There is no dependency, workflow, package-config, PR5 model, or v0.4 semantic change; the engineering-gate edit adds the proofability-workspace JavaScript syntax check, replaces the Zoom fixture operator npm wrapper with its three equivalent direct `node --check` commands, and retains the existing dependency-audit stage required by the frozen train. |
| Fixture authority | Exactly one immutable package-owned synthetic fixture reconstructs the accepted subject/scope/context/contract/descriptor roots and pins a 2,602-byte canonical report with digest `sha256:28c49bba2dd3791905a201a74777c9994e6ecc083cc3b9de083095f4c626d81e`. It accepts no runtime fixture choice, POC/customer/source/provider content, clock, network, or external side effect. |
| Workspace boundary | Exactly 128 eager deterministic write stripes; bounded immutable operation, global-idempotency, latest, pending, and byte state; replay before capacity; owned reservations; atomic publish after verifier/golden checks; no eviction. Storage is process-local, lost on restart, and not shared across workers. |
| HTTP and browser boundary | Exact source-neutral API/page namespaces use raw origin-form classification, closed method/framing/JSON/Unicode/profile grammar, canonical code-only errors, and exact fresh/replay/GET behavior. The narrow page gates the serialized URL before fetch/render, uses one server-fixed profile and one in-memory retry identity, renders only with text nodes, uses no browser storage, and exposes no agreement, execute, evidence, verdict, validity, deployment, release, or traffic action. |
| Superseded r2 checks | The complete r2 focused suite passed 257/257 and its touched A2–A7 regressions passed 178/178, but those results bind only rejected candidate `c1364e86f7ed1b6b25a8ebe30767102cc4431f26`. They cannot waive or substitute for fresh corrected-candidate checks or either complete gate. |
| Corrected-candidate gate rule | Focused checks, Ruff, direct `node --check`, HTML validation, source scans, `git diff --check`, complete engineering, and complete v0.4 release gates must run against the immutable corrected commit. Exact results and all disclosed runner-egress boundaries are recorded only in the current Builder report, never claimed here in advance. |
| Authority result | Fixed response fields keep deployment, production traffic, and traffic expansion false and external authorization required true. The PR6 product workspace initiates no provider, external-network, or GPU call and performs no spend, execution, evidence admission, verdict/receipt/validity action, deployment, release, or traffic change. Local HTTP/browser verification and release-gate dependency auditing are separate test-runner trust surfaces and are not no-egress attestations. |
| Reviewer handoff | The corrected one-commit Builder candidate remains local-only `CANDIDATE` until the current Builder report, Mission Control, and fresh independent MTS all approve its exact SHA and evidence. No approval is claimed here; do not push, open a PR, merge, tag, release, execute a GPU/provider, deploy, or change traffic from this record. |

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
- **PR5 title:** `feat: add v0.5 proofability engine`
- **PR5 body summary:** Adds the strict provider-neutral
  `InferenceQualificationCriterionV1` and a pure, input-bound
  `ProofabilityReportV1` with canonical JCS parsing/serialization,
  domain-separated digesting, exact subject-engine applicability,
  deterministic native/semantic/legacy mapping, 1 MiB/64-result closure,
  byte-exact golden coverage, and complete recursive raw-graph normalization.
  The report has no execution, evidence, verdict, receipt, validity,
  deployment, traffic, or authorization capability.
- **PR6 title:** `feat: add v0.5 proofability workspace`
- **PR6 body summary:** Adds one bounded process-local source-neutral
  proofability preflight over an immutable package-owned synthetic fixture,
  with exact fresh/replay/GET semantics, 128 eager write stripes, atomic
  immutable publication, a strict raw HTTP boundary, and a narrow text-only
  browser projection. It adds no agreement, provider/network/GPU execution,
  evidence, verdict, receipt, validity, deployment, release, traffic, or
  authorization capability.
- **Historical PR1 title:** `docs: freeze v0.5 provider-neutral qualification execution contract`
- **Historical PR1 evidence note:** The merged PR1 candidate incorporated
  independent review corrections, preserved above, including the valid GitHub
  Actions permissions contract.
