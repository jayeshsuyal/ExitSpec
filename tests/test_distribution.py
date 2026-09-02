"""Distribution-level checks for ExitSpec's self-contained deterministic demo."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import sysconfig
import tarfile
import zipfile
from pathlib import Path

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
STATIC_ROOT = PROJECT_ROOT / "src" / "exitspec" / "static"
INFERDROME_SCHEMA_ROOT = (
    PROJECT_ROOT / "src" / "exitspec" / "schemas" / "inferdrome" / "v1"
)
INFERDROME_PROFILE_ROOT = (
    PROJECT_ROOT / "src" / "exitspec" / "profiles" / "inferdrome" / "v1"
)
ROUTING_FIXTURE_EXAMPLES = {
    "contracts/routing-campaign-reduction-v1.synthetic.json": (
        PROJECT_ROOT
        / "examples"
        / "routing-qualification"
        / "contracts"
        / "routing-campaign-reduction-v1.synthetic.json"
    ),
    "contracts/routing-campaign-reduction-v1.synthetic.confirmation.json": (
        PROJECT_ROOT
        / "examples"
        / "routing-qualification"
        / "contracts"
        / "routing-campaign-reduction-v1.synthetic.confirmation.json"
    ),
    "evidence/routing-campaign-evidence-v1.synthetic.json": (
        PROJECT_ROOT
        / "examples"
        / "routing-qualification"
        / "evidence"
        / "routing-campaign-evidence-v1.synthetic.json"
    ),
    "receipts/routing-qualification-receipt-v1.synthetic.json": (
        PROJECT_ROOT
        / "examples"
        / "routing-qualification"
        / "receipts"
        / "routing-qualification-receipt-v1.synthetic.json"
    ),
}
EXPECTED_INFERDROME_SCHEMAS = {
    "environment.schema.json": (
        "0a0c43552f86d45579786f30f71da62cf6c02ea7c5c2cfcf76dc1427dc9df777"
    ),
    "evidence-bundle.schema.json": (
        "276a8e2c3d14fd18f45f428bdda31964af879adbad0341ae5959c599dd5c3437"
    ),
    "execution.schema.json": (
        "f4615a340bea6566c6924e02777927c9491cd351a43f8aafa01ef9f34002dfe5"
    ),
    "experiment.schema.json": (
        "244f45d5aba43a45e7e9f0cf98965881a26667a56977fcc2bc418368382f86ab"
    ),
    "measurements.schema.json": (
        "39b86747910842a9f726ac8bcdd035cad6c2bdd9454cd090fde8eb3739438ecb"
    ),
    "metric-definitions.schema.json": (
        "d501d11c030e7b9fee71dfafd5f9c5462e48f237bac918139cb2cfaff34bc204"
    ),
    "request-plan.schema.json": (
        "c866742180909e982a6466553d296a8412734c49c2b5f5bbb549a6c63fb2417d"
    ),
    "request-record.schema.json": (
        "a65f763947207f0a312770d743a623363ae4eec36336e0126e87df350ec07ee4"
    ),
}
EXPECTED_INFERDROME_PROFILE_RESOURCES = {
    "a10-handoff-manifest.json": (
        "a6d91f202d805523e5a52a44174fd547ab6f460ea20113a7cabf4deaebd61fe0"
    ),
    "a10-publication-review.json": (
        "700e374b22e91ef459b5e8a978f9cad94aececa694ff25bdf8b9bde25b51e22d"
    ),
    "local-gpu-proof.schema.json": (
        "2f397c7608edd039fdbe904287f52c85647e54a71d608523b135332717de456a"
    ),
    "managed-vllm-0.26-evidence-profile.json": (
        "8be8a31f332087297a78c3b20e9f790d732e90e36a6b6f2ffa5213957ff3ef51"
    ),
}
EXPECTED_STATIC_RESOURCES = {
    "agreement.css",
    "agreement.html",
    "agreement.js",
    "app.js",
    "dashboard.css",
    "dashboard.html",
    "dashboard.js",
    "index.html",
    "performance.css",
    "performance.html",
    "performance.js",
    "proof.css",
    "proof.html",
    "proof.js",
    "proofability_workspace.css",
    "proofability_workspace.html",
    "proofability_workspace.js",
    "qualification.css",
    "qualification.html",
    "qualification.js",
    "review.css",
    "review.html",
    "review.js",
    "styles.css",
}
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
        "Command failed: {}\nstdout:\n{}\nstderr:\n{}".format(
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
    source_archive = tmp_path / "source.tar"
    source_root = tmp_path / "source"
    source_root.mkdir()
    _run(
        [
            "git",
            "archive",
            "--format=tar",
            f"--output={source_archive}",
            "HEAD",
        ],
        cwd=PROJECT_ROOT,
    )
    with tarfile.open(source_archive, mode="r:") as archive:
        archive.extractall(source_root, filter="data")
    assert (source_root / "pyproject.toml").is_file()
    assert not (source_root / "build").exists()
    assert not (source_root / "src" / "exitspec.egg-info").exists()

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
            str(source_root),
        ],
        cwd=outside_checkout,
    )
    wheels = list(wheelhouse.glob("exitspec-*.whl"))
    assert len(wheels) == 1
    wheel = wheels[0]

    with zipfile.ZipFile(wheel) as archive:
        members = set(archive.namelist())
        for filename, expected_sha256 in EXPECTED_INFERDROME_SCHEMAS.items():
            member = f"exitspec/schemas/inferdrome/v1/{filename}"
            assert member in members
            archived_schema = archive.read(member)
            assert archived_schema == (INFERDROME_SCHEMA_ROOT / filename).read_bytes()
            assert _sha256(archived_schema) == expected_sha256
        for filename, expected_sha256 in (
            EXPECTED_INFERDROME_PROFILE_RESOURCES.items()
        ):
            member = f"exitspec/profiles/inferdrome/v1/{filename}"
            assert member in members
            archived_profile = archive.read(member)
            assert archived_profile == (
                INFERDROME_PROFILE_ROOT / filename
            ).read_bytes()
            assert _sha256(archived_profile) == expected_sha256
        for filename in EXPECTED_STATIC_RESOURCES:
            member = f"exitspec/static/{filename}"
            assert member in members
            assert archive.read(member) == (STATIC_ROOT / filename).read_bytes()
        for relative_path, expected_sha256 in EXPECTED_RESOURCES.items():
            member = f"exitspec/demo_data/support_agent/{relative_path}"
            assert member in members
            assert _sha256(archive.read(member)) == expected_sha256
        for relative_path, authoritative_path in ROUTING_FIXTURE_EXAMPLES.items():
            member = f"exitspec/demo_data/routing_qualification/{relative_path}"
            assert member in members
            assert archive.read(member) == authoritative_path.read_bytes()
        expected_email_members = {
            f"exitspec/demo_data/support_agent/email/{filename}"
            for filename in EXPECTED_EMAIL_RESOURCES
        }
        actual_email_members = {
            member
            for member in members
            if member.startswith("exitspec/demo_data/support_agent/email/")
        }
        assert actual_email_members == expected_email_members
        evidence_member = (
            f"exitspec/demo_data/support_agent/evidence/{IMPLEMENTATION_EVIDENCE_NAME}"
        )
        assert evidence_member in members
        assert _sha256(archive.read(evidence_member)) == (
            EXPECTED_IMPLEMENTATION_EVIDENCE_SHA256
        )
        for filename, expected_sha256 in EXPECTED_EMAIL_RESOURCES.items():
            member = f"exitspec/demo_data/support_agent/email/{filename}"
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
import http.client
import inspect
import json
import threading
from pathlib import Path
import exitspec
import exitspec.proofability as proofability
import exitspec.proofability_workspace as proofability_workspace
import exitspec.proofability_workspace_fixture as proofability_workspace_fixture
import exitspec.proofability_workspace_web as proofability_workspace_web
from exitspec.authoring import load_contract_seed, load_discovery_pack, load_review_plan
from exitspec.canonical import canonical_json_bytes
from exitspec.demo_data import (
    support_agent_demo_paths,
    support_agent_email_paths,
    support_agent_source_web_contract,
)
from exitspec.inferdrome_bundle import INFERDROME_VERIFIER_VERSION
from exitspec.inferdrome_import import INFERDROME_RECEIPT_SCHEMA_VERSION
from exitspec.routing_evidence_pack import load_routing_evidence_demo_context
from exitspec.poc_creation import DraftPOCCreateRequest
from exitspec.poc_source_demo import SourceNeutralPOCDemoServer
from exitspec.web import DemoSession, ExitSpecDemoServer
from exitspec.workspace import DashboardFilter
from exitspec.performance_workspace import load_performance_demo_bundle

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
        "proofability_module": str(Path(proofability.__file__).resolve()),
        "inferdrome_receipt_schema": INFERDROME_RECEIPT_SCHEMA_VERSION,
        "inferdrome_verifier_version": INFERDROME_VERIFIER_VERSION,
        "resource_root": str(data.root),
        "transcript_id": session.state_payload()["transcript"]["id"],
        "review_actions": len(load_review_plan(data.review_plan).actions),
        "fixture_exists": session.fixture_path.is_file(),
        "workspace_filter": workspace["selected_filter"],
        "workspace_poc_id": workspace["continue_working"]["poc_id"],
        "workspace_next_action": workspace["continue_working"]["next_action_code"],
        "workspace_module_available": DashboardFilter.ACTIVE.value,
        "workspace_poc_ids": sorted(
            poc["poc_id"] for poc in workspace["pocs"]
        ),
    }
performance = load_performance_demo_bundle()
payload["performance_contract_hash"] = (
    performance.context.contract.canonical_hash
)
payload["performance_request_count"] = (
    performance.context.workload.request_count
)
with support_agent_demo_paths() as routing_data:
    routing_session = DemoSession.synthetic_support_agent(
        Path.cwd() / "routing-demo-runs",
        discovery_path=routing_data.discovery_pack,
        contract_seed_path=routing_data.contract_seed,
        fixture_path=routing_data.fixture,
    )
    routing_server = ExitSpecDemoServer(
        ("127.0.0.1", 0),
        routing_session,
        enable_routing_evidence_pack_demo=True,
    )
    try:
        routing_library = routing_server.evidence_pack_library_payload()
        payload["routing_pack_count"] = len(routing_library["packs"])
        payload["routing_pack_id"] = routing_server.routing_evidence_pack.pack_id
        payload["routing_verdict"] = routing_library["packs"][0]["verdict"]
        payload["routing_missing_repetition_indices"] = (
            load_routing_evidence_demo_context().receipt.missing_repetition_indices
        )
    finally:
        routing_server.server_close()
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
source_server = SourceNeutralPOCDemoServer(("127.0.0.1", 0))
source_server.draft_poc_service.create(
    DraftPOCCreateRequest(
        display_name="Installed proofability POC",
        customer_label="Synthetic label",
        use_case="Installed-wheel planning projection.",
        owner="owner",
        first_source_choice="DOCUMENT",
        poc_id="poc_wheel",
    ),
    idempotency_key="wheel-draft",
)
source_worker = threading.Thread(target=source_server.serve_forever, daemon=True)
source_worker.start()
try:
    connection = http.client.HTTPConnection(
        "127.0.0.1", source_server.server_port, timeout=5
    )
    api = "/api/pocs/poc_wheel/qualification/proofability"
    connection.request("GET", api)
    no_latest_response = connection.getresponse()
    no_latest = json.loads(no_latest_response.read())
    request_body = canonical_json_bytes(
        {
            "profile_id": "exitspec.external-evidence.native-ttft-profile.v1",
            "profile_version": "v1",
            "idempotency_key": "wheel-proofability",
        }
    )
    request_headers = {
        "Content-Type": "application/json",
        "Origin": "http://127.0.0.1:{0}".format(source_server.server_port),
    }
    connection.request("POST", api, body=request_body, headers=request_headers)
    fresh_response = connection.getresponse()
    fresh = json.loads(fresh_response.read())
    connection.request("POST", api, body=request_body, headers=request_headers)
    replay_response = connection.getresponse()
    replay = json.loads(replay_response.read())
    connection.request("GET", api)
    applicable_response = connection.getresponse()
    applicable = json.loads(applicable_response.read())

    class InstalledDriftProjection:
        def __init__(self, delegate):
            self.delegate = delegate

        def get(self, *, poc_id):
            value = dict(self.delegate.get(poc_id=poc_id))
            report = value["report"]
            value["report"] = None
            value["needs_replan"] = True
            value["reported_context_digest"] = report[
                "qualification_context_digest"
            ]
            value["resolved_context_digest"] = "sha256:" + "f" * 64
            return value

    installed_workspace = source_server.proofability_workspace
    source_server.proofability_workspace = InstalledDriftProjection(
        installed_workspace
    )
    connection.request("GET", api)
    drift_response = connection.getresponse()
    drift = json.loads(drift_response.read())
    source_server.proofability_workspace = installed_workspace
    connection.request(
        "GET", "/app/pocs/poc_wheel/qualification/proofability"
    )
    page_response = connection.getresponse()
    page_bytes = page_response.read()
    connection.request("GET", "/proofability_workspace.html")
    alias_response = connection.getresponse()
    alias_bytes = alias_response.read()
    connection.close()
    payload["proofability_workspace"] = {
        "fixture_module": str(
            Path(proofability_workspace_fixture.__file__).resolve()
        ),
        "workspace_module": str(Path(proofability_workspace.__file__).resolve()),
        "web_module": str(Path(proofability_workspace_web.__file__).resolve()),
        "factory_parameters": sorted(
            inspect.signature(
                proofability_workspace.create_production_proofability_workspace
            ).parameters
        ),
        "has_test_helper": hasattr(
            proofability_workspace, "make_test_only_proofability_workspace"
        ),
        "no_latest_status": no_latest_response.status,
        "no_latest_report": no_latest["report"],
        "fresh_status": fresh_response.status,
        "fresh_replay": fresh["idempotent_replay"],
        "fresh_report_bytes": len(canonical_json_bytes(fresh["report"])),
        "replay_status": replay_response.status,
        "replay": replay["idempotent_replay"],
        "applicable_status": applicable_response.status,
        "applicable_report": applicable["report"] is not None,
        "drift_status": drift_response.status,
        "drift_report": drift["report"],
        "drift_needs_replan": drift["needs_replan"],
        "page_status": page_response.status,
        "page_has_synthetic_notice": b"package synthetic fixture" in page_bytes,
        "alias_status": alias_response.status,
        "alias_body": alias_bytes.decode("utf-8"),
        "context_source": fresh["context_source"],
        "storage": fresh["storage"],
        "authority": fresh["authority"],
    }
finally:
    source_server.shutdown()
    source_worker.join(timeout=5)
    source_server.server_close()
print(json.dumps(payload))
"""
    probe_stdout = _run(
        [sys.executable, "-c", session_probe],
        cwd=outside_checkout,
        env=isolated_env,
    )
    probe = json.loads(probe_stdout)
    assert Path(probe["module"]).is_relative_to(installed_site_packages)
    assert Path(probe["proofability_module"]).is_relative_to(installed_site_packages)
    assert probe["inferdrome_receipt_schema"] == "exitspec.inferdrome-receipt.v1"
    assert probe["inferdrome_verifier_version"] == "1.0.0"
    assert Path(probe["resource_root"]).is_relative_to(installed_site_packages)
    assert probe["transcript_id"] == "support-discovery-v1"
    assert probe["review_actions"] == 2
    assert probe["fixture_exists"] is True
    assert probe["workspace_filter"] == "Active"
    assert probe["workspace_poc_id"] == "poc_support_agent_demo"
    assert probe["workspace_next_action"] == "REVIEW_PROPOSALS"
    assert probe["workspace_module_available"] == "Active"
    assert probe["workspace_poc_ids"] == [
        "poc_inference_latency_demo",
        "poc_support_agent_demo",
    ]
    assert probe["performance_contract_hash"] == (
        "88c4f55dd1a0810efa59fac1bd1041a21c3cbe1179ceb3e101e75000eb7d909f"
    )
    assert probe["performance_request_count"] == 100
    assert probe["routing_pack_count"] == 1
    assert probe["routing_pack_id"] == (
        "rpk_c502a1e3bae757015b90ecca96839b5c792a1d3c2fab9a048a40d00829cfaa87"
    )
    assert probe["routing_verdict"] == "NOT_PROVEN"
    assert probe["routing_missing_repetition_indices"] == [2]
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
    proofability_probe = probe["proofability_workspace"]
    for module_key in ("fixture_module", "workspace_module", "web_module"):
        assert Path(proofability_probe[module_key]).is_relative_to(
            installed_site_packages
        )
    assert proofability_probe["factory_parameters"] == [
        "draft_commit_guard",
        "draft_lookup",
    ]
    assert proofability_probe["has_test_helper"] is False
    assert proofability_probe["no_latest_status"] == 200
    assert proofability_probe["no_latest_report"] is None
    assert proofability_probe["fresh_status"] == 201
    assert proofability_probe["fresh_replay"] is False
    assert proofability_probe["fresh_report_bytes"] == 2_602
    assert proofability_probe["replay_status"] == 200
    assert proofability_probe["replay"] is True
    assert proofability_probe["applicable_status"] == 200
    assert proofability_probe["applicable_report"] is True
    assert proofability_probe["drift_status"] == 200
    assert proofability_probe["drift_report"] is None
    assert proofability_probe["drift_needs_replan"] is True
    assert proofability_probe["page_status"] == 200
    assert proofability_probe["page_has_synthetic_notice"] is True
    assert proofability_probe["alias_status"] == 404
    assert proofability_probe["alias_body"] == '{"error":"Page not found."}'
    assert proofability_probe["context_source"] == {
        "kind": "PACKAGE_SYNTHETIC_FIXTURE",
        "fixture_id": "exitspec.synthetic-proofability-preflight.native-v1",
        "fixture_version": "v1",
        "poc_derived": False,
    }
    assert proofability_probe["storage"] == {
        "scope": "PROCESS_LOCAL",
        "survives_process_restart": False,
        "shared_across_workers": False,
    }
    assert proofability_probe["authority"] == {
        "deployment_authorized": False,
        "production_traffic_authorized": False,
        "traffic_expansion_authorized": False,
        "external_authorization_required": True,
    }
