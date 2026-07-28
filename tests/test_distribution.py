"""Distribution-level checks for ExitSpec's self-contained deterministic demo."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import sysconfig
import zipfile

import pytest

from exitspec.demo_data import (
    SupportAgentEmailPaths,
    SupportAgentEmailResourceError,
    SupportAgentSourceWebContract,
    SupportAgentSourceWebContractError,
    support_agent_demo_paths,
    support_agent_email_paths,
    support_agent_source_web_contract,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SUPPORT_AGENT_EXAMPLES = PROJECT_ROOT / "examples" / "support-agent"
SUPPORT_AGENT_EMAIL_EXAMPLES = SUPPORT_AGENT_EXAMPLES / "email"
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
EMAIL_MANIFEST_NAME = "wave-2-acceptance-v1.json"
EXPECTED_EMAIL_MANIFEST_SHA256 = (
    "aa514787eb6b14a93216682d702fc29a32d630eb1a91a16dae6ce0873a268ae2"
)
SOURCE_WEB_CONTRACT_NAME = "wave-2-source-web-v1.json"
EXPECTED_SOURCE_WEB_CONTRACT_SHA256 = (
    "f89825510155b1d579814da0f6e3a639c1b03d3111deba170556654eaca35ffd"
)
IMPLEMENTATION_EVIDENCE_NAME = "wave-2-implementation-evidence-v1.json"
EXPECTED_IMPLEMENTATION_EVIDENCE_SHA256 = (
    "bc3986ac0b8a5e718f98398b4c76bd42d2dc19f9d0cfb54774ee1707afe92990"
)
EXPECTED_EMAIL_CASES = {
    "allowed-text-attachment": (
        "a1edd2fde773cd07dab79d7a3fbae80232660fa1ae340bb8f46a888769c38b51"
    ),
    "authority-attack": (
        "cf482b06b5da2794aae3aa9128671fada8e50ecd7e1cd56740cd4632efe8bb6a"
    ),
    "html-plain-disagreement": (
        "70fa6f3e0bd693529a17563a80ea16c70d3a57cfff4081af2490c97b1445e3ca"
    ),
    "missing-body": (
        "8bfaa76f82fd3c40f6041dc5ec2f1713b30204b642ec12f3bfd04e07a96d668c"
    ),
    "missing-message-id": (
        "005cb6391a08fcf148230d12b3ff9db664eb440b89ca20fce27b13d3c0be9b70"
    ),
    "oversized-body": (
        "1d9ffa391471b09dcc9315d8682b2074306a5c3e0beef8f694c349f005ffe401"
    ),
    "sender-ambiguous": (
        "8b377b725d6612497c5cace8b1e63b6b9a4dcc77b75435de6a4f494d474596c8"
    ),
    "thread-follow-up": (
        "08eaf6943f2caa35e827affc3be555406dae2044d9b027f5731179a1c0aca7bc"
    ),
    "thread-root": (
        "64b188fac2f4a7e0f2eef1397099ce7ca081654a7c5f23fb5b04d1cf761810d8"
    ),
    "thread-root-mutated": (
        "0428d6c5feaae011d0c2baa226174ca37a7c659ecf4802a8aed2a4ebf6340624"
    ),
    "unsupported-attachment": (
        "c55b502b67be690b974a77aad11689fa7dfd4398ef03e418b6e938bda07e2a5c"
    ),
}
EXPECTED_EMAIL_RESOURCES = {
    EMAIL_MANIFEST_NAME: EXPECTED_EMAIL_MANIFEST_SHA256,
    SOURCE_WEB_CONTRACT_NAME: EXPECTED_SOURCE_WEB_CONTRACT_SHA256,
    **{
        f"{case_id}.eml": expected_sha256
        for case_id, expected_sha256 in EXPECTED_EMAIL_CASES.items()
    },
}
EMAIL_RESOURCE_ERROR = (
    "ExitSpec's bundled Wave-2 email resources failed validation."
)


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


def test_bundled_email_inputs_exactly_match_the_frozen_authoritative_examples():
    with (
        support_agent_email_paths() as bundled,
        support_agent_source_web_contract() as web_contract,
    ):
        assert bundled.case_ids == tuple(sorted(EXPECTED_EMAIL_CASES))
        assert tuple(bundled.fixtures) == bundled.case_ids
        bundled_by_filename = {
            EMAIL_MANIFEST_NAME: bundled.manifest,
            SOURCE_WEB_CONTRACT_NAME: web_contract.path,
            **{
                f"{case_id}.eml": bundled.fixture_for(case_id)
                for case_id in bundled.case_ids
            },
        }

        for filename, expected_sha256 in EXPECTED_EMAIL_RESOURCES.items():
            bundled_payload = bundled_by_filename[filename].read_bytes()
            example_payload = (SUPPORT_AGENT_EMAIL_EXAMPLES / filename).read_bytes()
            assert bundled_payload == example_payload
            assert _sha256(bundled_payload) == expected_sha256

        manifest = json.loads(bundled.manifest.read_text(encoding="utf-8"))
        manifest_case_ids = tuple(
            sorted(
                fixture["case_id"]
                for fixture in manifest["fixture_set"]["fixtures"]
            )
        )
        assert manifest_case_ids == bundled.case_ids
        with pytest.raises(TypeError):
            bundled.fixtures["not-approved"] = bundled.manifest
        with pytest.raises(KeyError) as unknown_case:
            bundled.fixture_for("not-approved")
        assert str(unknown_case.value) == (
            "'Wave-2 email case ID is not manifest-approved.'"
        )
        assert web_contract.payload == web_contract.path.read_bytes()
        assert (
            web_contract.contract["contract_version"]
            == "wave2-source-web-v1"
        )


def test_email_resource_paths_cannot_be_constructed_without_validation(
    tmp_path,
):
    with pytest.raises(TypeError, match="must be created through from_root"):
        SupportAgentEmailPaths(
            root=tmp_path,
            manifest=tmp_path / EMAIL_MANIFEST_NAME,
            fixtures={"not-approved": tmp_path / "arbitrary.eml"},
        )


def test_source_web_contract_cannot_be_constructed_without_validation(tmp_path):
    with pytest.raises(TypeError, match="must be created through from_path"):
        SupportAgentSourceWebContract(
            path=tmp_path / SOURCE_WEB_CONTRACT_NAME,
            payload=b"{}",
            contract={},
        )

    invalid = tmp_path / SOURCE_WEB_CONTRACT_NAME
    invalid.write_bytes(b"{}")
    with pytest.raises(SupportAgentSourceWebContractError):
        SupportAgentSourceWebContract.from_path(invalid)


@pytest.mark.parametrize("web_contract_state", ["missing", "invalid"])
def test_email_fixture_loading_is_independent_of_web_contract_validity(
    tmp_path, web_contract_state
):
    resource_root = tmp_path / web_contract_state / "email"
    shutil.copytree(SUPPORT_AGENT_EMAIL_EXAMPLES, resource_root)
    web_contract_path = resource_root / SOURCE_WEB_CONTRACT_NAME
    if web_contract_state == "missing":
        web_contract_path.unlink()
    else:
        web_contract_path.write_bytes(b'{"status":"INVALID"}\n')

    bundled = SupportAgentEmailPaths.from_root(resource_root)
    assert bundled.case_ids == tuple(sorted(EXPECTED_EMAIL_CASES))
    assert _sha256(bundled.fixture_for("thread-root").read_bytes()) == (
        EXPECTED_EMAIL_CASES["thread-root"]
    )

    with pytest.raises(SupportAgentSourceWebContractError):
        SupportAgentSourceWebContract.from_path(web_contract_path)


def test_email_resource_paths_anchor_a_relative_root_before_return(
    tmp_path,
    monkeypatch,
):
    workspace = tmp_path / "workspace"
    resource_root = workspace / "email"
    other_directory = tmp_path / "other"
    workspace.mkdir()
    other_directory.mkdir()
    shutil.copytree(SUPPORT_AGENT_EMAIL_EXAMPLES, resource_root)
    monkeypatch.chdir(workspace)

    bundled = SupportAgentEmailPaths.from_root(Path("email"))
    expected_payload = bundled.fixture_for("thread-root").read_bytes()

    assert bundled.root.is_absolute()
    assert bundled.manifest.is_absolute()
    assert all(path.is_absolute() for path in bundled.fixtures.values())

    monkeypatch.chdir(other_directory)
    assert bundled.fixture_for("thread-root").read_bytes() == expected_payload


@pytest.mark.parametrize(
    "invalid_state",
    [
        "incomplete",
        "extra",
        "duplicate",
        "traversal",
        "manifest-path-mismatch",
        "manifest-identity-mismatch",
        "fixture-identity-mismatch",
        "symlink",
    ],
)
def test_email_resource_map_rejects_invalid_states_without_content(
    tmp_path, invalid_state
):
    resource_root = tmp_path / invalid_state / "email"
    shutil.copytree(SUPPORT_AGENT_EMAIL_EXAMPLES, resource_root)
    manifest_path = resource_root / EMAIL_MANIFEST_NAME

    if invalid_state == "incomplete":
        (resource_root / "thread-root.eml").unlink()
    elif invalid_state == "extra":
        (resource_root / "not-manifest-approved.eml").write_bytes(
            b"synthetic extra resource"
        )
    elif invalid_state in {
        "duplicate",
        "traversal",
        "manifest-path-mismatch",
        "manifest-identity-mismatch",
    }:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        fixtures = manifest["fixture_set"]["fixtures"]
        if invalid_state == "duplicate":
            fixtures[1]["case_id"] = fixtures[0]["case_id"]
            fixtures[1]["path"] = fixtures[0]["path"]
        elif invalid_state == "traversal":
            fixtures[0]["path"] = (
                "examples/support-agent/email/../thread-root.eml"
            )
        elif invalid_state == "manifest-path-mismatch":
            fixtures[0]["path"] = (
                "examples/support-agent/email/authority-attack.eml"
            )
        else:
            manifest["manifest_id"] = "not-the-approved-manifest"
        manifest_path.write_text(
            json.dumps(manifest, indent=2) + "\n",
            encoding="utf-8",
        )
    elif invalid_state == "symlink":
        fixture_path = resource_root / "thread-root.eml"
        fixture_path.unlink()
        fixture_path.symlink_to(
            SUPPORT_AGENT_EMAIL_EXAMPLES / "thread-root.eml"
        )
    else:
        fixture_path = resource_root / "thread-root.eml"
        fixture_path.write_bytes(fixture_path.read_bytes() + b"\n")

    with pytest.raises(SupportAgentEmailResourceError) as invalid:
        SupportAgentEmailPaths.from_root(resource_root)
    assert str(invalid.value) == EMAIL_RESOURCE_ERROR


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
        expected_email_members = {
            "exitspec/demo_data/support_agent/email/{0}".format(filename)
            for filename in EXPECTED_EMAIL_RESOURCES
        }
        actual_email_members = {
            member
            for member in members
            if member.startswith("exitspec/demo_data/support_agent/email/")
        }
        assert actual_email_members == expected_email_members
        evidence_member = (
            "exitspec/demo_data/support_agent/evidence/{0}".format(
                IMPLEMENTATION_EVIDENCE_NAME
            )
        )
        assert evidence_member in members
        assert _sha256(archive.read(evidence_member)) == (
            EXPECTED_IMPLEMENTATION_EVIDENCE_SHA256
        )
        for filename, expected_sha256 in EXPECTED_EMAIL_RESOURCES.items():
            member = "exitspec/demo_data/support_agent/email/{0}".format(
                filename
            )
            archived_payload = archive.read(member)
            authoritative_payload = (
                SUPPORT_AGENT_EMAIL_EXAMPLES / filename
            ).read_bytes()
            assert archived_payload == authoritative_payload
            assert _sha256(archived_payload) == expected_sha256
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
import hashlib
import json
from pathlib import Path
import exitspec
from exitspec.authoring import load_contract_seed, load_discovery_pack, load_review_plan
from exitspec.demo_data import (
    support_agent_demo_paths,
    support_agent_email_paths,
    support_agent_source_web_contract,
)
from exitspec.web import DemoSession
from exitspec.workspace import DashboardFilter

with support_agent_demo_paths() as data:
    discovery = load_discovery_pack(data.discovery_pack)
    session = DemoSession(
        discovery_pack=discovery,
        contract_seed=load_contract_seed(data.contract_seed),
        fixture_path=data.fixture,
        output_root=Path("session-runs"),
        reviewed_drafts=list(discovery.drafts),
    )
    workspace = session.state_payload()["workspace"]
    payload = {
        "module": str(Path(exitspec.__file__).resolve()),
        "resource_root": str(data.root),
        "transcript_id": session.state_payload()["transcript"]["id"],
        "review_actions": len(load_review_plan(data.review_plan).actions),
        "fixture_exists": session.fixture_path.is_file(),
        "workspace_filter": workspace["selected_filter"],
        "workspace_poc_id": workspace["continue_working"]["poc_id"],
        "workspace_next_action": workspace["continue_working"]["next_action_code"],
        "workspace_module_available": DashboardFilter.ACTIVE.value,
    }
with support_agent_email_paths() as email:
    payload["email_resource_root"] = str(email.root)
    payload["email_manifest_sha256"] = hashlib.sha256(
        email.manifest.read_bytes()
    ).hexdigest()
    payload["email_fixture_sha256"] = {
        case_id: hashlib.sha256(path.read_bytes()).hexdigest()
        for case_id, path in email.fixtures.items()
    }
with support_agent_source_web_contract() as source_web:
    payload["source_web_contract_root"] = str(source_web.path.parent)
    payload["source_web_contract_sha256"] = hashlib.sha256(
        source_web.payload
    ).hexdigest()
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
    assert probe["workspace_filter"] == "Active"
    assert probe["workspace_poc_id"] == "poc_support_agent_demo"
    assert probe["workspace_next_action"] == "REVIEW_PROPOSALS"
    assert probe["workspace_module_available"] == "Active"
    assert Path(probe["email_resource_root"]).is_relative_to(
        installed_site_packages
    )
    assert probe["email_manifest_sha256"] == EXPECTED_EMAIL_MANIFEST_SHA256
    assert probe["email_fixture_sha256"] == EXPECTED_EMAIL_CASES
    assert Path(probe["source_web_contract_root"]).is_relative_to(
        installed_site_packages
    )
    assert (
        probe["source_web_contract_sha256"]
        == EXPECTED_SOURCE_WEB_CONTRACT_SHA256
    )
