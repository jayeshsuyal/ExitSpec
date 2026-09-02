"""Regression checks for the v0.5 architecture-only entry contract."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "docs" / "V0_5_QUALIFICATION_GATE_PLAN.md"
RUNBOOK = ROOT / "docs" / "V0_5_EXECUTION_RUNBOOK.md"
LEDGER = ROOT / "docs" / "V0_5_EXECUTION_LEDGER.md"
READ_ONLY_PERMISSIONS_BLOCK = "```yaml\npermissions:\n  contents: read\n```"


def _read(path: Path) -> str:
    assert path.is_file(), f"Required v0.5 planning artifact is missing: {path}"
    return path.read_text(encoding="utf-8")


def _normalise(markdown: str) -> str:
    return " ".join(markdown.split())


def test_v0_5_plan_keeps_exactly_fourteen_exitspec_pr_milestones():
    plan = _read(PLAN)
    milestones = re.findall(r"(?m)^### PR(\d+) — (.+)$", plan)

    assert [number for number, _ in milestones] == [
        str(number) for number in range(1, 15)
    ]
    assert milestones[0][1] == "Architecture, vocabulary, and threat contract"
    assert "Provider-neutral prospective handoff boundary" in milestones[6][1]
    assert "Provider-neutral external-evidence admission boundary" in milestones[7][1]
    assert milestones[11][1] == "GitHub required-check integration"


def test_v0_5_train_count_is_fixed_pending_explicit_user_approval():
    plan = _read(PLAN)
    runbook = _read(RUNBOOK)

    assert "The current train is exactly PR1–PR14." in plan
    assert "explicit user-approved plan/goal amendment before\nimplementation" in plan
    assert "no implementation may\nsplit or combine the current milestones" in plan
    assert "user-approved plan/goal amendment before implementation" in runbook
    for retired_permissive_marker in (
        "a planning estimate",
        "may be\nsplit",
        "may be combined",
    ):
        assert retired_permissive_marker not in plan


def test_v0_5_contract_is_provider_neutral_and_zero_authority():
    plan = _read(PLAN)
    runbook = _read(RUNBOOK)
    ledger = _read(LEDGER)
    combined = f"{plan}\n{runbook}\n{ledger}"

    assert "Inferdrome" not in combined
    assert combined.count("ExitSpec never authorizes deployment or traffic.") >= 3
    assert "provider-neutral" in combined
    assert "cross-repository" in combined
    assert "CLI JSON + GitHub required check" in plan
    assert "GitHub required-check integration" in plan
    assert READ_ONLY_PERMISSIONS_BLOCK in plan
    assert READ_ONLY_PERMISSIONS_BLOCK in runbook
    assert "permissions: contents: read" not in plan
    assert "permissions: contents: read" not in runbook
    assert "no `id-token`" in combined
    assert "never deployment or traffic authorization" in combined


def test_v0_5_github_required_check_has_a_safe_untrusted_contribution_boundary():
    plan = _normalise(_read(PLAN))
    runbook = _normalise(_read(RUNBOOK))

    for contract in (plan, runbook):
        assert (
            "must not use `pull_request_target` for untrusted contribution code"
            in contract
        )
        assert (
            "must not combine privileged permissions, secrets, or an authenticated "
            "checkout with untrusted contribution code" in contract
        )
        assert (
            "no `id-token`, secrets, deployment or provider credentials, or write permissions"
            in contract
        )
        assert (
            "Repository owners configure branch-protection required status separately "
            "outside ExitSpec; the workflow itself must not mutate branch protection."
            in contract
        )


def test_v0_5_contract_preserves_proofability_verdict_and_validity_axes():
    plan = _read(PLAN)
    runbook = _read(RUNBOOK)

    for marker in (
        "`PROVABLE`, `CLARIFICATION_REQUIRED`, `NOT_PROVABLE`",
        "`PASS`, `FAIL`, `NOT_PROVEN`",
        "`CURRENT`, `STALE`, `EXPIRED`, `INVALID`",
    ):
        assert marker in plan
        assert marker in runbook

    assert "pre-admission capability" in _read(LEDGER)
    assert "ExitSpec's result from admitted evidence" in _read(LEDGER)
    assert "present applicability of a validated receipt" in _read(LEDGER)


def test_v0_5_pr2_subject_identity_contract_stays_digest_only_and_bounded():
    plan = _read(PLAN)

    for marker in (
        "`launch_arguments_digest` is a required `sha256:<64 lowercase hex>`",
        "PR2 does not persist raw launch arguments",
        "A parser never default-fills omitted optional fields.",
        "`api`/`key`, `private`/`key`, and `gpu`/`reservation`",
        "Every nested object key extends one accumulated path",
        "one key's segments only",
        "`exitspec-serving-subject-manifest-v1\\x00`",
        "JCS code-point semantics",
        "performs no Unicode normalization",
        "`tests/fixtures/serving_subject/v1/golden.json`",
        "sha256:2921dd76c90a5dd4a6131ef8bb7a369f7b4b1a3a829744751e6b38e81dfb988a",
    ):
        assert marker in plan
    assert "`exitspec-serving-subject-manifest-v1\\\\x00`" not in plan


def test_v0_5_pr3_scope_and_context_contract_remains_zero_authority():
    plan = _read(PLAN)
    scope_section = plan.split("### 2. `QualificationScopeV1`", 1)[1].split(
        "### 3. `QualificationContextV1`", 1
    )[0]

    for marker in (
        "`exitspec.qualification-scope.v1`",
        '"evaluated_use": "CANARY_CONSIDERATION"',
        '"maximum_traffic_percent": 1',
        "integer from 1 through 5",
        "`EVIDENCE_CAPTURED_AT`",
        "`NOT_REQUIRED` or `REQUIRED`",
        "No parser default-fills either optional\nfield.",
        "`exitspec-qualification-scope-v1\\x00`",
    ):
        assert marker in scope_section
    for prohibited in (
        "deployment_authorized",
        "production_traffic_authorized",
        "traffic_expansion_authorized",
        "external_authorization_required",
        "expires_after_seconds",
    ):
        assert prohibited not in scope_section

    context_section = plan.split("### 3. `QualificationContextV1`", 1)[1].split(
        "### 4. `ProducerCapabilityDescriptorV1`", 1
    )[0]
    for marker in (
        '"schema_version": "exitspec.qualification-context.v1"',
        '"qualification_context_digest": "sha256:<64 lowercase hex>"',
        "`exitspec-qualification-context-v1\\x00`",
        "never informal string concatenation",
        "tests/fixtures/qualification_scope/v1/golden-scope.json",
        "sha256:5db651e8c2eae05147d2c5fc52bae0b4526ed84508f76d62d41471ac4ca677ab",
        "sha256:9159ac21169d0674b916053e6605a72f6f25e65cfe94b30b708a86f343d0193c",
        "not proof of execution, authorship, chronology",
    ):
        assert marker in context_section


def test_v0_5_pr4_capability_descriptor_contract_stays_provider_neutral():
    plan = _read(PLAN)
    descriptor_section = _normalise(
        plan.split("### 4. `ProducerCapabilityDescriptorV1`", 1)[1].split(
            "### 5. `ProofabilityReportV1`", 1
        )[0]
    )

    for marker in (
        '"exitspec.producer-capability-descriptor.v1"',
        '"exitspec.producer-capability-request.v1"',
        '"exitspec.producer-capability-registry.v1"',
        "`exitspec.external-evidence.native-ttft-profile.v1`",
        "`vllm_first_choices_event_v0_26`",
        "`first_nonempty_choices_delta_content_v1`",
        "absent and unsupported",
        '"nearest_rank_v1"',
        '"source_field": "request.outcome.status"',
        "`exitspec-producer-capability-descriptor-v1\\x00`",
        "sha256:1b8732d26a94dadfab984b43a4c67c1fc858ddf39f95ec496f5914f1c08e066b",
        "There is no create, override, merge",
        "exact declared class (no subclass)",
        "PR4 introduces no network, API, browser, execution, evidence-admission, proofability",
    ):
        assert marker in descriptor_section
    assert "Inferdrome" not in descriptor_section


def test_v0_5_pr5_proofability_contract_is_closed_and_input_bound():
    plan = _read(PLAN)
    report_section = _normalise(
        plan.split("### 5. `ProofabilityReportV1`", 1)[1].split(
            "### 6. protocol-specific qualification receipt", 1
        )[0]
    )

    for marker in (
        "`inference_qualification_v1`",
        "`exitspec.inference-qualification-criterion.v1`",
        "`NATIVE_TTFT_P95`",
        "`SEMANTIC_FIRST_NONEMPTY_TTFT_P95`",
        "`native_ttft_sample`",
        "`semantic_first_nonempty_ttft_sample`",
        "`request.timing.ttft_ns`",
        "`request.outcome.status`",
        "`MISSING_OBSERVATION`",
        "`UNMAPPABLE_FROZEN_CRITERION_SCHEMA`",
        "`ALL_REQUIRED_OBSERVATIONS_AVAILABLE` / `NO_REMEDIATION_REQUIRED`",
        "`FREEZE_PROVIDER_NEUTRAL_CRITERION_SCHEMA`",
        "every full required observation model, exactly one",
        "complete, mutually exclusive partition",
        "reduced observation-kind/ID keys do not define exact availability",
        "complete canonical set returned by the closed semantic-leaf mismatch mapping",
        "both metric-definition and source-field mismatch reasons",
        "contradictory reports fail parsing before capability use",
        "`exitspec-proofability-report-v1\\x00`",
        "sha256:28c49bba2dd3791905a201a74777c9994e6ecc083cc3b9de083095f4c626d81e",
        "self-consistent replacement report never becomes a trusted evaluation",
        "seven ordinary criterion arms use `^[A-Z][A-Z0-9-]{2,63}$`",
        "`routing_qualification_v1`, `routing_slo_attainment_v1`, and `routing_campaign_reduction_v1`",
        "No other lowercase, mixed-case, punctuation, prefix, suffix",
        "1,048,576 bytes",
        "16,384 nodes",
        "3,349 counted JSON nodes",
        "evaluator serializes, parses, and strictly normalizes",
        "`__pydantic_private__`",
        "`__pydantic_fields_set__`",
        "value-equal `str` subclass",
        "`engine_id` and `engine_version` must equal",
        "`CAPABILITY_BINDING_MISMATCH`",
        "registered evidence profile or adapter with the subject's serving-profile adapter",
        "does not derive scope workload identity",
        "compare the scope measurement profile to the descriptor profile",
        "never execution, evidence admission, Verdict, Validity, deployment, traffic, or authorization",
    ):
        assert marker in report_section
    assert "Inferdrome" not in report_section


def test_v0_5_pr6_workspace_contract_is_bounded_synthetic_and_zero_authority():
    plan = _normalise(_read(PLAN))
    pr6 = plan.split("### PR6 — Proofability service and workspace projection", 1)[
        1
    ].split("### PR7 — Provider-neutral prospective handoff boundary", 1)[0]

    for marker in (
        "exactly one immutable package-owned synthetic fixture",
        "not derived from that POC, customer/source content, live input",
        "exactly 128 deterministic write stripes",
        "accepted replay precedes capacity",
        "there is no eviction",
        "Process-local state is lost on restart and is not shared across workers",
        "origin-form raw-target classification",
        "gates the serialized URL before fetch or dynamic render",
        "uses no browser storage",
        "zero deployment, production traffic, or traffic-expansion authority",
        "product workspace initiates no provider, external-network, or GPU call",
        "not a no-egress attestation",
        "may access or revalidate public vulnerability metadata",
        "explicit command-level runner trust boundary",
    ):
        assert marker in pr6
    assert "pass without network" not in pr6
    assert "Inferdrome" not in pr6


def test_v0_5_pr6_ledger_binds_the_current_packet_and_review_train():
    ledger = _normalise(_read(LEDGER))
    pr6 = ledger.split("## PR6 evidence record", 1)[1].split(
        "## Proposed PR metadata", 1
    )[0]

    for marker in (
        "exactly 16 repository paths",
        "scripts/engineering_gate.sh",
        "one fresh immutable exact-byte external freeze",
        "separate exact-byte review",
        "/private/tmp/exitspec-pr6-builder-r6-green-report.txt",
        "c1364e86f7ed1b6b25a8ebe30767102cc4431f26",
        "94fd30827369ffa14898495c0672943e703d2ecc4a2e3a6b7b8783ebe6bc862e",
        "58de8e8d0f1c7753e0462ca4a691461f717b797f7e34e904e94b60d07e96c542",
        "ffbebd9f7f9b3736eeb36fe9025aa6df11ad02d3",
        "67b23874a008fdee38932badb92dbc8020a4981a9168248745beba28eb0b938d",
        "a9265b7122855026100be5e2663d9d46416d3f8a6d0c9920acef10d78dbadfac",
        "7d4f3c36fc97a1927774423c8baaaaf0aa083f8d",
        "fa0d76770bc03be938030929292e19a8a48ad6a418dde46abbd59fc060239a22",
        "70feb42eae29a2728bfccc70b17a4d0f8a5d6a88b33cfeb768e02034540eb280",
        "e2ce37c0ef7fb68f57c77e5590e0c705ce97c6a1dddab1c0b114b7d9be5c7e49",
        "replaces the Zoom fixture operator npm wrapper",
        "three equivalent direct `node --check` commands",
        "retains the existing dependency-audit stage",
        "not no-egress attestations",
    ):
        assert marker in pr6
    assert "exactly 14 repository paths" not in pr6
    assert "/private/tmp/exitspec-pr6-builder-report.txt" not in pr6
    assert (
        "adds the proofability-workspace JavaScript syntax check and "
        "dependency-audit stage"
    ) not in pr6


def test_v0_5_threat_model_covers_required_boundaries_and_limitations():
    plan = _read(PLAN)

    assert "## Threat model and trust boundaries" in plan
    for threat in (
        "Untrusted local evidence input",
        "Producer overclaim or producer verdict injection",
        "Subject, scope, or context substitution",
        "Stale or replayed receipt",
        "Unsafe file-tree input",
        "Secret or private-content leakage",
        "Status-axis collapse",
        "Deployment-authority escalation",
    ):
        assert threat in plan

    for control in (
        "Reject before verdict or receipt.",
        "Domain-separated canonical digests",
        "Drift is `STALE`; expired, malformed, unsupported, or incompatible input is `EXPIRED` or `INVALID`, never current `PASS`.",
        "Reject symlinks, hard links, path escape",
        "zero-authority fields",
        "GitHub required check is least-privilege and status-only",
        "physical hardware truth, authorship, chronology",
    ):
        assert control in plan


def test_v0_5_ledger_captures_pr1_state_and_all_follow_on_milestones():
    ledger = _read(LEDGER)

    assert "PR1 base revision:" in ledger
    assert "PR3 base revision:" in ledger
    assert "Rejected candidate history:" in ledger
    assert "Candidate selector:" in ledger
    assert "PR1 | Architecture, vocabulary, and threat contract" in ledger
    assert "PR14 | Adversarial closure and candidate checkpoint" in ledger
    assert "78fe2cdae5fcb4e1230636dc1db8a2b6222c543a" in ledger
    assert "e76e0735f6cc3eb2eecb05eeac06880d4a525b6c" in ledger
    assert ledger.count("CHANGES_REQUIRED") >= 4
    assert ledger.count("MTS_FAIL") >= 3
    assert "P1 — invalid permissions syntax:" in ledger
    assert "one local-only PR6 candidate becomes immutable\n  `HEAD`" in ledger
    assert "GitHub required-check integration" in ledger
    assert (
        "docs: freeze v0.5 provider-neutral qualification execution contract" in ledger
    )
    assert "2a6ce7b681063b73450bf7a4573dea5dac8314b5" in ledger
    assert "ca96e6e737402fe3fcbea990f5ac411e5cb6105c" in ledger
    assert "PR CI `33363876409`; main CI `33364429844`" in ledger
    assert "| PR2 | Serving-subject identity | PR1 | MERGED |" in ledger
    assert "| PR3 | Qualification scope and context | PR2 | MERGED |" in ledger
    assert "| PR4 | Producer capability descriptor | PR3 | MERGED |" in ledger
    assert "| PR5 | Proofability engine | PR4 | MERGED |" in ledger
    assert (
        "| PR6 | Proofability service and workspace projection | PR5 | "
        "MERGED |" in ledger
    )
    assert "475b965309b77b1cab55fdf29d391b02851a695f" in ledger
    assert "8b1ac77f6d56a60ffe1df3fa8034302357f4511d" in ledger
    assert (
        "| PR7 | Provider-neutral prospective handoff boundary | PR3, PR5 | "
        "MERGED |" in ledger
    )
    assert (
        "| PR8 | Provider-neutral external-evidence admission boundary | PR7 | "
        "MERGED |" in ledger
    )
    assert "52b7fad3815099f67cf585c565f5d380f852a384" in ledger
    assert "| PR9 | Inference-performance qualification receipt | PR8 | MERGED |" in ledger
    assert "050fe4407337d4b443e577c795a37ec2bd1f51b0" in ledger
    assert "| PR10 | Qualification validity and staleness | PR9 | MERGED |" in ledger
    assert "9ddc0daa3bb405c54411041cf9e52dead8340104" in ledger
    assert "| PR11 | Qualification CLI | PR10 | MERGED |" in ledger
    assert "a099c1a498baf1ad9a7c9b75d28fca8bc213287a" in ledger
    assert "| PR12 | GitHub required-check integration | PR11 | MERGED |" in ledger
    assert "f2030a5e9d7286c5d28e56ccdd6c86fc904d5db4" in ledger
    assert "| PR13 | Guided four-screen product surface | PR6, PR12 | CANDIDATE |" in ledger
    assert "PR6 base revision:" in ledger
    assert "424aeae8a959f4249a35375141fd2c365bc68b71" in ledger
    assert "867f4ac9d29376ab5130864f5a2d39bb946bb447" in ledger
    assert "## PR6 evidence record" in ledger
    assert "Exactly 128 eager deterministic write stripes" in ledger
    assert "complete r2 focused suite passed 257/257" in ledger
    assert "touched A2–A7 regressions passed 178/178" in ledger
    assert "those results bind only rejected candidate" in ledger
    assert "No approval is claimed here" in ledger
    assert "3,919 passed, 33 skipped" in ledger
    assert "3,932 passed, 23 skipped" in ledger
    assert "3,924 passed, 33 skipped" in ledger
    assert "3,937 passed, 23 skipped" in ledger
    assert "/private/tmp/exitspec-pr5-r2-engineering-gate.log" in ledger
    assert "/private/tmp/exitspec-pr5-r2-v0_4-release-gate.log" in ledger
    assert "101dabbadd1d986f38b56794633ec9e45cea9ac1" in ledger
    assert "7da388ecfb83c2262a4f30d161a272f674839826" in ledger
    assert "PR CI `33423877517`" in ledger
    assert "main CI `33424573497`, all four jobs green" in ledger
    assert "1c5fe7960d5464fd40ae21b1a73a841ca0cbf27c" in ledger
    assert "7e1268373da3fea8cf441b7ad7d515df8af8f2f5" in ledger
    assert "PR CI `33435286412`" in ledger
    assert "main CI `33436107791`, all four jobs green" in ledger
    assert "a36c09450776c13342200aadd34a891bd4502c06" in ledger
    assert "4a4decd69f613c302d77280debc6c2b746f0df1b" in ledger
    assert "5c63ab581e497c64bdce8e8e44f8212fa7d2f922" in ledger
    assert "63bdec2dd7454132bf9c66fadde2f854dccc15f8b7b040b5b18431ddffc5a039" in ledger
    assert "/private/tmp/exitspec-pr5-r3-mts-report.txt" in ledger
    assert "552/552 tests" in ledger
    assert "3,966 passed, 33 skipped" in ledger
    assert "3,979 passed, 23 skipped" in ledger
    assert "06d13e92592d16fbb1b07f2bb01a2a3b5308cc85b7e3557d34ed2ec904c9f9eb" in ledger
    assert "/private/tmp/exitspec-pr5-r2-mts-report.txt" in ledger
    assert "exact subject/descriptor engine non-applicability" in ledger
    assert "private/field-set/primitive-subclass raw state" in ledger
    assert "1,048,576 bytes and 16,384 JSON nodes" in ledger
    assert "No registered evidence-profile/adapter equality" in ledger
    assert "does not claim MTS pass" in ledger
    assert "omitted accounting" in ledger
    assert "double classification" in ledger
    assert "complete, mutually exclusive partition" in ledger
    assert "Reduced kind/ID keys do not define availability" in ledger
    assert "complete canonical set returned by `_incompatibility_reason_codes`" in ledger
    assert "sha256:afd6ef64a481f78a99c25135470acc2aa0ba5cee5a9055c3b34a20c73876babf" in ledger
    assert "6181bef889ccc99641e8a49784f4bbf31d05724d" in ledger
    assert "P2 — every material context identity leaf" in ledger
    assert "00b4f01c27eabac37a63adb1015d8e1434113009" in ledger
    assert "edb62a071d68a9281e6127ee8ade51f7f23daa02" in ledger
    assert "PR CI `33415971409`" in ledger
    assert "main CI `33416637002`, all four jobs green" in ledger
    assert "426c792c35ed5ea212b9cdedcbb58612e3f581ab" in ledger
    assert "P1 — runtime-config deny pairs" in ledger
    assert "b473b8bae5644aa8ef7ef5dcb02119230efe8c72" in ledger
    assert "compact fallback" in ledger
    assert "5baa09e96075d94e730941cf3673f1047cb11818" in ledger
    assert "nested strict-model bypass" in ledger
    assert "cyclic mapping" in ledger
    assert "request.outcome.status" in ledger
    assert "3,755 passed, 33 skipped" in ledger
    assert "3,768 passed, 23 skipped" in ledger
    assert "3,799 passed, 33 skipped" in ledger
    assert "3,812 passed, 23 skipped" in ledger
    assert "3,840 passed, 33 skipped" in ledger
    assert "3,853 passed, 23 skipped" in ledger
    assert "3,866 passed, 33 skipped" in ledger
    assert "3,879 passed, 23 skipped" in ledger
    assert "295 passed" in ledger


def test_v0_5_planning_documents_have_resolvable_local_links():
    for document in (PLAN, RUNBOOK, LEDGER):
        markdown = _read(document)
        for target in re.findall(r"\]\(([^)#]+)(?:#[^)]*)?\)", markdown):
            if target.startswith(("https://", "http://")):
                continue
            assert (document.parent / target).is_file(), (
                f"Broken local link in {document}: {target}"
            )
