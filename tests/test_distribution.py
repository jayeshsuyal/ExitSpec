"""Distribution-level checks for ExitSpec's self-contained deterministic demo."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import sysconfig
import zipfile

from exitspec.demo_data import support_agent_demo_paths


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SUPPORT_AGENT_EXAMPLES = PROJECT_ROOT / "examples" / "support-agent"
EXPECTED_RESOURCES = {
    "authoring/discovery-pack-v1.json": (
        "33ebc4f9e0e8eec1e3bbfaf1b2942b548c8934991a30d47be7f2e92871eb32ed"
    ),
    "authoring/review-plan-v1.json": (
        "c2936f2785c46f80563c33c6d918eae46ca24f711f42cd57d05344e2bfcca797"
    ),
    "authoring/contract-seed-v1.json": (
        "acc0261d4ca55c1b0f68ac3ae278a818e7d533bce8828548af6ec44170f472bb"
    ),
    "contracts/tool-selection-v1.frozen.yaml": (
        "e15073ce7e94f793e83343432b15c3a1dc141e250a8a5c107253597cc814edcf"
    ),
    "fixtures/tool-selection-200.json": (
        "75ef6f83450de100a920e9489a0b5966464f1dba2e3d339c4b57e64fb95d8271"
    ),
}


def _run(command: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> str:
    completed = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, (
        "Command failed: {0}\nstdout:\n{1}\nstderr:\n{2}".format(
            " ".join(command), completed.stdout, completed.stderr
        )
    )
    return completed.stdout


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def test_bundled_demo_inputs_exactly_match_the_authoritative_examples():
    with support_agent_demo_paths() as bundled:
        bundled_by_relative_path = {
            "authoring/discovery-pack-v1.json": bundled.discovery_pack,
            "authoring/review-plan-v1.json": bundled.review_plan,
            "authoring/contract-seed-v1.json": bundled.contract_seed,
            "contracts/tool-selection-v1.frozen.yaml": bundled.frozen_contract,
            "fixtures/tool-selection-200.json": bundled.fixture,
        }

        for relative_path, expected_sha256 in EXPECTED_RESOURCES.items():
            bundled_payload = bundled_by_relative_path[relative_path].read_bytes()
            example_payload = (SUPPORT_AGENT_EXAMPLES / relative_path).read_bytes()
            assert bundled_payload == example_payload
            assert _sha256(bundled_payload) == expected_sha256


def test_wheel_runs_demo_and_materializes_session_data_outside_checkout(tmp_path):
    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir()
    outside_checkout = tmp_path / "outside-checkout"
    outside_checkout.mkdir()
    assert not (outside_checkout / "examples").exists()

    _run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "--no-deps",
            "--no-build-isolation",
            "--wheel-dir",
            str(wheelhouse),
            str(PROJECT_ROOT),
        ],
        cwd=outside_checkout,
    )
    wheels = list(wheelhouse.glob("exitspec-*.whl"))
    assert len(wheels) == 1
    wheel = wheels[0]

    with zipfile.ZipFile(wheel) as archive:
        members = set(archive.namelist())
        for relative_path, expected_sha256 in EXPECTED_RESOURCES.items():
            member = "exitspec/demo_data/support_agent/{0}".format(relative_path)
            assert member in members
            assert _sha256(archive.read(member)) == expected_sha256
        entry_points = [
            name for name in members if name.endswith(".dist-info/entry_points.txt")
        ]
        assert len(entry_points) == 1
        assert "exitspec = exitspec.cli:main" in archive.read(
            entry_points[0]
        ).decode("utf-8")

    install_prefix = tmp_path / "installed"
    _run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--no-deps",
            "--ignore-installed",
            "--prefix",
            str(install_prefix),
            str(wheel),
        ],
        cwd=outside_checkout,
    )

    install_paths = sysconfig.get_paths(
        vars={"base": str(install_prefix), "platbase": str(install_prefix)}
    )
    installed_site_packages = Path(install_paths["purelib"])
    exitspec_command = Path(install_paths["scripts"]) / "exitspec"
    assert exitspec_command.is_file()

    isolated_env = os.environ.copy()
    isolated_env.pop("PYTHONHOME", None)
    isolated_env["PYTHONNOUSERSITE"] = "1"
    isolated_env["PYTHONPATH"] = str(installed_site_packages)

    demo_output = outside_checkout / "demo-runs"
    stdout = _run(
        [
            str(exitspec_command),
            "demo",
            "--scenario",
            "pass",
            "--output-dir",
            str(demo_output),
            "--run-id",
            "wheel-pass",
        ],
        cwd=outside_checkout,
        env=isolated_env,
    )
    assert "Criterion verdict: PASS" in stdout
    assert "Overall verdict: PASS" in stdout
    assert (demo_output / "wheel-pass" / "decision-packet.html").is_file()

    define_output = outside_checkout / "define-runs"
    _run(
        [
            str(exitspec_command),
            "define",
            "--output-dir",
            str(define_output),
            "--session-id",
            "wheel-define",
        ],
        cwd=outside_checkout,
        env=isolated_env,
    )
    assert (define_output / "wheel-define" / "approved-contract.json").is_file()

    session_probe = """
import json
from pathlib import Path
import exitspec
from exitspec.authoring import load_contract_seed, load_discovery_pack, load_review_plan
from exitspec.demo_data import support_agent_demo_paths
from exitspec.web import DemoSession

with support_agent_demo_paths() as data:
    discovery = load_discovery_pack(data.discovery_pack)
    session = DemoSession(
        discovery_pack=discovery,
        contract_seed=load_contract_seed(data.contract_seed),
        fixture_path=data.fixture,
        output_root=Path("session-runs"),
        reviewed_drafts=list(discovery.drafts),
    )
    payload = {
        "module": str(Path(exitspec.__file__).resolve()),
        "resource_root": str(data.root),
        "transcript_id": session.state_payload()["transcript"]["id"],
        "review_actions": len(load_review_plan(data.review_plan).actions),
        "fixture_exists": session.fixture_path.is_file(),
    }
    print(json.dumps(payload))
"""
    probe_stdout = _run(
        [sys.executable, "-c", session_probe],
        cwd=outside_checkout,
        env=isolated_env,
    )
    probe = json.loads(probe_stdout)
    assert Path(probe["module"]).is_relative_to(installed_site_packages)
    assert Path(probe["resource_root"]).is_relative_to(installed_site_packages)
    assert probe["transcript_id"] == "support-discovery-v1"
    assert probe["review_actions"] == 2
    assert probe["fixture_exists"] is True
