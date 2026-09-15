"""Independent detached anchor, frozen full manifest and admission ordering."""
import hashlib
import json
import os
import subprocess
import sys
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from exitspec import source_authoring_launch as launch
from exitspec import source_authoring_live_worker as worker
from exitspec import source_authoring_operator as operator
from tests.helpers.source_authoring_admission import fake_profile


def write_approval(path, profile):
    raw = json.dumps({"schema_version": launch._APPROVAL_SCHEMA, "profile": asdict(profile)}).encode()
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def sealed_profile(monkeypatch, tmp_path):
    root = tmp_path / "frozen"
    module = root / "src/exitspec/source_authoring_launch.py"
    module.parent.mkdir(parents=True)
    module.write_bytes(Path(launch.__file__).read_bytes())
    env = {"PATH": "/usr/bin:/bin", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
           "GIT_AUTHOR_NAME": "Offline Test", "GIT_AUTHOR_EMAIL": "test@example.invalid",
           "GIT_COMMITTER_NAME": "Offline Test", "GIT_COMMITTER_EMAIL": "test@example.invalid"}

    def git(*args):
        return subprocess.check_output(["/usr/bin/git", "-C", str(root), *args], env=env,
                                       stderr=subprocess.DEVNULL).decode().strip()

    git("init", "--quiet")
    git("add", ".")
    git("commit", "--quiet", "-m", "Freeze code before approval exists")
    artifact = tmp_path / "offline-tokenizer"
    artifact.write_bytes(b"synthetic tokenizer admission fixture")
    profile = replace(fake_profile(), code_revision=git("rev-parse", "HEAD"),
                      code_tree=git("rev-parse", "HEAD^{tree}"),
                      files=((str(module.relative_to(root)), hashlib.sha256(module.read_bytes()).hexdigest()),),
                      tokenizer_artifacts=((str(artifact), hashlib.sha256(artifact.read_bytes()).hexdigest()),))
    profile = replace(profile, launch_profile_sha256=launch._profile_digest(profile))
    approval = tmp_path / "detached.json"
    digest = write_approval(approval, profile)
    monkeypatch.setattr(launch, "__file__", str(module))
    monkeypatch.setattr(launch, "_PRODUCTION_PROFILES", ())
    monkeypatch.setattr(launch, "_QUALIFIED_SERVING_CONTRACTS", (profile.tokenizer_identity,))
    for name in ("_APPROVALS", "_ADMISSIONS", "_LAUNCHES", "_INSTALLS", "_LEASES", "_TOKENS"):
        monkeypatch.setattr(launch, name, {})
    monkeypatch.setattr(launch, "_verify_tokenizer_implementation", lambda _: None)
    return profile, approval, digest, root


def test_code_is_frozen_before_detached_approval_without_changing_any_tracked_byte(monkeypatch, tmp_path):
    profile, path, digest, root = sealed_profile(monkeypatch, tmp_path)
    before = (root / profile.files[0][0]).read_bytes()
    admitted = launch._admit_operator_profile(profile.approval_id, approval_file=str(path), expected_sha256=digest)
    handle = launch._issue_live_launch(admitted, b"SYNTHETIC-KEY")
    assert launch._LAUNCHES[handle].profile == profile
    assert (root / profile.files[0][0]).read_bytes() == before
    assert not path.is_relative_to(root)
    handle.revoke()


@pytest.mark.parametrize("fault", ["wrong_anchor", "substituted_record", "extra_root", "extra_profile",
                                     "duplicate", "expired", "model_policy", "budget", "tokenizer",
                                     "missing_files", "relative", "inside_worktree", "symlink", "fifo",
                                     "directory", "oversized", "nonfinite", "invalid_json"])
def test_detached_rejections_precede_credentials_and_leave_no_authority(monkeypatch, tmp_path, fault):
    profile, path, digest, root = sealed_profile(monkeypatch, tmp_path)
    if fault == "wrong_anchor":
        digest = "0" * 64
    elif fault == "substituted_record":
        write_approval(path, replace(profile, custody_approval="unreviewed"))
    elif fault in {"extra_root", "extra_profile", "duplicate", "nonfinite", "invalid_json"}:
        value = json.loads(path.read_bytes())
        if fault == "extra_root":
            value["expected_sha256"] = digest
        elif fault == "extra_profile":
            value["profile"]["endpoint"] = "https://example.invalid"
        raw = json.dumps(value).encode()
        if fault == "duplicate":
            raw = raw.replace(b'"schema_version":', b'"schema_version":"ignored","schema_version":', 1)
        elif fault == "nonfinite":
            raw = raw.replace(b'"valid_until": 4102444800.0', b'"valid_until": NaN')
        elif fault == "invalid_json":
            raw = b'{"profile":'
        path.write_bytes(raw)
        digest = hashlib.sha256(raw).hexdigest()
    elif fault in {"expired", "model_policy", "budget", "tokenizer", "missing_files"}:
        changes = {"expired": {"valid_until": 0.0}, "model_policy": {"request_profile_sha256": "0" * 64},
                   "budget": {"input_tokens_max": 8193}, "tokenizer": {"tokenizer_identity": "unqualified"},
                   "missing_files": {"files": ()}}[fault]
        changed = replace(profile, **changes)
        changed = replace(changed, launch_profile_sha256=launch._profile_digest(changed))
        digest = write_approval(path, changed)
    elif fault == "relative":
        path = Path("detached.json")
    elif fault == "inside_worktree":
        new = root / "detached.json"
        new.write_bytes(path.read_bytes())
        path = new
    elif fault == "symlink":
        link = tmp_path / "link"
        link.symlink_to(path)
        path = link
    elif fault in {"fifo", "directory"}:
        path.unlink()
        if fault == "fifo":
            os.mkfifo(path)
        else:
            path.mkdir()
    elif fault == "oversized":
        path.write_bytes(b"x" * (launch._APPROVAL_MAX_BYTES + 1))
    effects = []
    monkeypatch.setattr(operator, "_read_text_tty", lambda *_: effects.append("tty"))
    monkeypatch.setattr(operator, "_read_credential_tty", lambda: effects.append("key"))
    assert operator.main(["--approval-id", profile.approval_id, "--approval-file", str(path),
                          "--approval-sha256", digest]) == 2
    assert effects == [] and launch._ADMISSIONS == {} and launch._LAUNCHES == {}


@pytest.mark.parametrize("fault", ["changed_tracked", "new_untracked", "missing_tracked", "artifact_drift"])
def test_reviewed_record_does_not_authorize_code_or_artifact_drift(monkeypatch, tmp_path, fault):
    profile, path, digest, root = sealed_profile(monkeypatch, tmp_path)
    module = root / profile.files[0][0]
    if fault == "changed_tracked":
        module.write_bytes(module.read_bytes() + b"\n# changed\n")
    elif fault == "new_untracked":
        (root / "extra.py").write_text("changed")
    elif fault == "missing_tracked":
        module.unlink()
    else:
        Path(profile.tokenizer_artifacts[0][0]).write_bytes(b"changed")
    with pytest.raises(launch.SourceAuthoringLaunchError):
        launch._admit_operator_profile(profile.approval_id, approval_file=str(path), expected_sha256=digest)
    assert launch._ADMISSIONS == {} and launch._LAUNCHES == {}


@pytest.mark.parametrize("moment", ["confirmation", "credential"])
def test_substitution_during_operator_prompts_cannot_dispatch(monkeypatch, tmp_path, moment):
    profile, path, digest, _ = sealed_profile(monkeypatch, tmp_path)
    effects = []

    def changed():
        path.write_bytes(path.read_bytes() + b" ")

    def confirm(_):
        if moment == "confirmation":
            changed()
        return "APPROVED"

    def credential():
        effects.append("key")
        if moment == "credential":
            changed()
        return b"SYNTHETIC-KEY"

    monkeypatch.setattr(operator, "_read_text_tty", confirm)
    monkeypatch.setattr(operator, "_read_credential_tty", credential)
    monkeypatch.setattr(operator, "serve_source_neutral_demo", lambda **_: effects.append("server"))
    assert operator.main(["--approval-id", profile.approval_id, "--approval-file", str(path),
                          "--approval-sha256", digest]) == 2
    assert effects == ([] if moment == "confirmation" else ["key"])
    assert launch._LAUNCHES == {}


def test_fixed_worker_rechecks_detached_anchor_before_protocol(monkeypatch, tmp_path):
    _profile, path, digest, _ = sealed_profile(monkeypatch, tmp_path)
    monkeypatch.setattr(sys, "argv", ["worker", "--approval-file", str(path), "--approval-sha256", digest])
    path.write_bytes(path.read_bytes() + b" ")
    effects = []
    monkeypatch.setattr(worker, "_run_protocol", lambda *_: effects.append("protocol"))
    with pytest.raises(ValueError):
        worker.run()
    assert effects == [] and launch._ADMISSIONS == {} and launch._LAUNCHES == {}


def test_fixed_worker_admits_the_same_independently_anchored_frozen_record(monkeypatch, tmp_path):
    profile, path, digest, _ = sealed_profile(monkeypatch, tmp_path)
    result = launch._bootstrap_worker_approval(["--approval-file", str(path), "--approval-sha256", digest])
    assert result == profile and result is launch._PRODUCTION_PROFILES[0]


def test_unqualified_serving_refuses_before_any_record_or_bootstrap_read(monkeypatch):
    monkeypatch.setattr(launch, "_QUALIFIED_SERVING_CONTRACTS", ())
    effects = []
    monkeypatch.setattr(launch, "_read_approval", lambda *_: effects.append("read"))
    for call in (lambda: launch._load_detached_approval("/private/tmp/record", "0" * 64),
                 lambda: launch._bootstrap_worker_approval(["--approval-file", "/private/tmp/record",
                                                          "--approval-sha256", "0" * 64])):
        with pytest.raises(ValueError):
            call()
    assert effects == []
