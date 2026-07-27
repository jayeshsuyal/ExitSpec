"""Contract checks for ExitSpec's repository engineering process."""

from __future__ import annotations

from pathlib import Path
import re
import stat


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PLAYBOOK = PROJECT_ROOT / "docs" / "ENGINEERING_PLAYBOOK.md"
PULL_REQUEST_TEMPLATE = PROJECT_ROOT / ".github" / "pull_request_template.md"
BUG_REPORT_TEMPLATE = (
    PROJECT_ROOT / ".github" / "ISSUE_TEMPLATE" / "bug_report.yml"
)
ENGINEERING_GATE = PROJECT_ROOT / "scripts" / "engineering_gate.sh"


def _read_required(path: Path) -> str:
    assert path.is_file(), f"Required engineering-process artifact is missing: {path}"
    return path.read_text(encoding="utf-8")


def _normalise_marker(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.casefold()))


def _markdown_headings(markdown: str) -> set[str]:
    """Return normalized ATX headings while ignoring fenced examples."""
    headings: set[str] = set()
    in_fence = False

    for line in markdown.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue

        match = re.match(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$", line)
        if match:
            headings.add(_normalise_marker(match.group(1)))

    return headings


def _assert_required_headings(markdown: str, required: set[str]) -> None:
    headings = _markdown_headings(markdown)
    missing = {_normalise_marker(heading) for heading in required} - headings
    assert not missing, f"Missing required process sections: {sorted(missing)}"


def _command_lines(shell_text: str) -> list[str]:
    """Collect non-comment shell lines, joining explicit continuations."""
    joined = re.sub(r"\\\r?\n\s*", " ", shell_text)
    lines: list[str] = []
    for raw_line in joined.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        lines.append(line.split(" #", 1)[0].strip())
    return lines


def _assert_baseline_commands(shell_text: str) -> None:
    lines = _command_lines(shell_text)

    def has_line(*markers: str, excluded: tuple[str, ...] = ()) -> bool:
        return any(
            all(marker in line for marker in markers)
            and all(marker not in line for marker in excluded)
            for line in lines
        )

    assert has_line(
        "pytest",
        "tests/test_distribution.py",
        excluded=("--ignore",),
    ), "Gate must run the Python distribution test separately"
    assert has_line(
        "pytest",
        "--ignore",
        "tests/test_distribution.py",
    ), "Gate must run the remaining Python tests"
    assert has_line(
        "node",
        "--check",
        "src/exitspec/static/app.js",
    ), "Gate must syntax-check app.js"
    assert has_line(
        "node",
        "--check",
        "src/exitspec/static/review.js",
    ), "Gate must syntax-check review.js"
    assert has_line("git", "diff", "--check"), "Gate must reject an unclean diff"


def test_engineering_playbook_exposes_the_process_source_of_truth():
    playbook = _read_required(PLAYBOOK)

    _assert_required_headings(
        playbook,
        {
            "Purpose",
            "Product invariants",
            "Authority map",
            "Change risk levels",
            "Required PR contract",
            "Universal merge gate",
            "Current repository verification",
            "Bug policy",
            "Regression-test rule",
            "Engineering Evidence Pack",
            "PR-train governance",
            "Wave exit gates",
            "Demo, open-source, and production release gates",
            "Rollback standard",
            "Definition of done",
        },
    )
    _assert_baseline_commands(playbook)


def test_pull_request_template_exposes_the_required_decision_contract():
    template = _read_required(PULL_REQUEST_TEMPLATE)

    _assert_required_headings(
        template,
        {
            "Decision",
            "User outcome",
            "Scope",
            "Non-goals",
            "Risk and authority",
            "Exit gate",
            "Failure matrix",
            "Evidence",
            "Security and privacy",
            "Rollback",
            "Follow-ups",
        },
    )

    normalized = _normalise_marker(template)
    for marker in (
        "change risk",
        "authority boundary",
        "invariants",
        "state mutation",
        "retry",
        "automated",
        "manual",
        "artifacts",
    ):
        assert marker in normalized, f"PR template must capture {marker!r}"

    assert re.search(r"\bC0\b.*\bC4\b", template, re.IGNORECASE | re.DOTALL)
    assert re.search(r"(?m)^\s*-\s*\[\s\]", template), (
        "The exit gate must provide an observable checklist item"
    )


def test_bug_report_form_exposes_impact_reproduction_and_safety_contracts():
    form = _read_required(BUG_REPORT_TEMPLATE)
    labels = [
        _normalise_marker(match.group(1).strip().strip("\"'"))
        for match in re.finditer(r"(?m)^\s*label:\s*(.+?)\s*$", form)
    ]

    required_label_concepts = {
        "observed behavior": ("observed",),
        "expected behavior": ("expected",),
        "violated invariant or contract": ("invariant", "contract"),
        "severity": ("severity",),
        "rationale": ("rationale",),
        "minimal reproduction": ("reproduction",),
        "environment or version": ("environment", "version"),
        "customer or data exposure": ("exposure",),
        "safe containment": ("containment",),
        "evidence": ("evidence",),
    }
    for field, alternatives in required_label_concepts.items():
        assert any(
            any(alternative in label for alternative in alternatives)
            for label in labels
        ), f"Bug report must expose a field for {field}"

    for severity in ("P0", "P1", "P2", "P3"):
        assert re.search(rf"\b{severity}\b", form, re.IGNORECASE), (
            f"Bug report must expose the {severity} severity class"
        )

    normalized = _normalise_marker(form)
    for safety_marker in ("secret", "customer", "raw audio", "provider"):
        assert safety_marker in normalized, (
            f"Bug report safety guidance must address {safety_marker!r}"
        )


def test_engineering_gate_is_executable_and_composes_the_baseline_checks():
    gate = _read_required(ENGINEERING_GATE)

    assert gate.startswith("#!"), "Engineering gate must declare its interpreter"
    assert ENGINEERING_GATE.stat().st_mode & stat.S_IXUSR, (
        "Engineering gate must be executable by its owner"
    )
    _assert_baseline_commands(gate)


def test_engineering_gate_prefers_the_remote_default_branch_as_local_diff_base():
    gate = _read_required(ENGINEERING_GATE)

    remote_default_marker = "refs/remotes/origin/HEAD"
    feature_upstream_marker = "'@{upstream}'"
    assert remote_default_marker in gate
    assert feature_upstream_marker in gate
    assert gate.index(remote_default_marker) < gate.index(feature_upstream_marker), (
        "A tracked feature branch points upstream to itself; the gate must prefer "
        "the remote default branch or its branch diff silently becomes empty"
    )
