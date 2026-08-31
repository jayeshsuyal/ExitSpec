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
        "### 4. `ProofabilityReportV1`", 1
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
    assert ledger.count("CHANGES_REQUIRED") >= 3
    assert "P1 — invalid permissions syntax:" in ledger
    assert "local PR3 qualification-scope candidate is" in ledger
    assert "GitHub required-check integration" in ledger
    assert (
        "docs: freeze v0.5 provider-neutral qualification execution contract" in ledger
    )
    assert "2a6ce7b681063b73450bf7a4573dea5dac8314b5" in ledger
    assert "ca96e6e737402fe3fcbea990f5ac411e5cb6105c" in ledger
    assert "PR CI `33363876409`; main CI `33364429844`" in ledger
    assert "| PR2 | Serving-subject identity | PR1 | MERGED |" in ledger
    assert "| PR3 | Qualification scope and context | PR2 | CANDIDATE |" in ledger
    assert "00b4f01c27eabac37a63adb1015d8e1434113009" in ledger
    assert "edb62a071d68a9281e6127ee8ade51f7f23daa02" in ledger
    assert "PR CI `33415971409`" in ledger
    assert "main CI `33416637002`, all four jobs green" in ledger
    assert "426c792c35ed5ea212b9cdedcbb58612e3f581ab" in ledger
    assert "P1 — runtime-config deny pairs" in ledger
    assert "b473b8bae5644aa8ef7ef5dcb02119230efe8c72" in ledger
    assert "compact fallback" in ledger
    assert "3,755 passed, 33 skipped" in ledger
    assert "3,768 passed, 23 skipped" in ledger
    assert "3,798 passed, 33 skipped" in ledger
    assert "3,811 passed, 23 skipped" in ledger


def test_v0_5_planning_documents_have_resolvable_local_links():
    for document in (PLAN, RUNBOOK, LEDGER):
        markdown = _read(document)
        for target in re.findall(r"\]\(([^)#]+)(?:#[^)]*)?\)", markdown):
            if target.startswith(("https://", "http://")):
                continue
            assert (document.parent / target).is_file(), (
                f"Broken local link in {document}: {target}"
            )
