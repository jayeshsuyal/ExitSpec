"""Test-only demo admission and fake child. No live authority or network."""
import hashlib
import json
import os
import subprocess
import sys
from dataclasses import asdict, replace
from pathlib import Path

from exitspec import source_authoring_launch as launch
from tests.helpers.source_authoring_admission import fake_profile


def demo_profile(tmp_path):
    directory = tmp_path / 'run'
    directory.mkdir(mode=0o700)
    status = directory.stat()
    values = asdict(fake_profile())
    values.update(launch_budget_usd='0.01', run_id='a' * 64, run_directory=str(directory.resolve()),
                  run_device=status.st_dev, run_inode=status.st_ino, run_uid=os.getuid(),
                  run_host=os.uname().nodename)
    profile = launch._DemoLaunchProfile(**values)
    return replace(profile, launch_profile_sha256=launch._profile_digest(profile))


def install_demo_profile(monkeypatch, tmp_path):
    profile = demo_profile(tmp_path)
    path = tmp_path / 'demo-approval.json'
    raw = json.dumps({'schema_version': launch._DEMO_APPROVAL_SCHEMA, 'profile': asdict(profile)}).encode()
    path.write_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()
    for name in ('_APPROVALS', '_ADMISSIONS', '_LAUNCHES', '_INSTALLS', '_LEASES', '_TOKENS'):
        monkeypatch.setattr(launch, name, {})
    monkeypatch.setattr(launch, '_DEMO_PROFILES', ())
    monkeypatch.setattr(launch, '_PRODUCTION_PROFILES', ())
    monkeypatch.setattr(launch, '_QUALIFIED_SERVING_CONTRACTS', ())
    # This fixture does not qualify code, local tokens, an account or a deployment.
    monkeypatch.setattr(launch, '_verify_code_and_artifacts', lambda _: None)
    monkeypatch.setattr(launch, '_evaluate_local_tokens', lambda *_: 100)
    return profile, path, digest


def make_demo_launch(monkeypatch, tmp_path):
    profile, path, digest = install_demo_profile(monkeypatch, tmp_path)
    admitted = launch._admit_operator_profile(profile.approval_id, approval_file=str(path),
                                              expected_sha256=digest, demo=True)
    return launch._issue_live_launch(admitted, b'SYNTHETIC-KEY'), profile, path, digest


def fake_demo_transport(monkeypatch, approval_path, scenario='authoring'):
    from exitspec.source_authoring_supervisor import _BoundedLiveSupervisor
    children = []

    def spawn(self, fd):
        child = subprocess.Popen(
            [os.path.abspath(sys.executable), '-I',
             str(Path(__file__).with_name('source_authoring_fake_worker.py')), scenario, str(approval_path)],
            env={}, close_fds=True, pass_fds=(fd,), stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0,
        )
        children.append(child)
        return child
    monkeypatch.setattr(_BoundedLiveSupervisor, '_spawn', spawn)
    return children
