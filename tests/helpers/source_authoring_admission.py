"""Private offline admission fixtures. Never imported by installed entrypoints."""
from dataclasses import replace

from exitspec import source_authoring_launch as launch


def fake_profile():
    profile = launch._QualifiedLaunchProfile(
        approval_id="offline-test-only", launch_profile_sha256="0" * 64,
        code_revision="e" * 40, code_tree="f" * 40,
        files=(("offline-test-only", "1" * 64),), tokenizer_identity="offline-token-evaluator",
        tokenizer_artifacts=(("/offline-test-only/tokenizer", "2" * 64),),
        account_pricing_approval="offline-no-account", custody_approval="offline-no-custody",
        region="offline-no-region", valid_from=0.0, valid_until=4102444800.0,
    )
    return replace(profile, launch_profile_sha256=launch._profile_digest(profile))


def install_fake_profile(monkeypatch):
    if not launch._PRODUCTION_PROFILES:
        monkeypatch.setattr(launch, "_PRODUCTION_PROFILES", (fake_profile(),))
        for name in ("_ADMISSIONS", "_LAUNCHES", "_INSTALLS", "_LEASES", "_TOKENS"):
            monkeypatch.setattr(launch, name, {})
    monkeypatch.setattr(launch, "_QUALIFIED_SERVING_CONTRACTS", ("offline-token-evaluator",))
    monkeypatch.setattr(launch, "_load_detached_approval", lambda *_: launch._PRODUCTION_PROFILES[0])
    monkeypatch.setattr(launch, "_verify_code_and_artifacts", lambda profile: None)
    monkeypatch.setattr(launch, "_evaluate_local_tokens", lambda profile, body: 100)
    monkeypatch.setattr(launch, "_display_mode", lambda lease: "OFFLINE_FAKE_FIREWORKS")
    return launch._PRODUCTION_PROFILES[0]


def make_launch(monkeypatch, credential=b"SYNTHETIC-KEY"):
    profile = install_fake_profile(monkeypatch)
    admitted = launch._admit_operator_profile(profile.approval_id)
    return launch._issue_live_launch(admitted, credential)


def bound_lease(monkeypatch, owners=None):
    if owners is None:
        from tests.test_source_authoring_operations import setup
        synthetic, *_ = setup()
        owners = synthetic._owners
    handle = make_launch(monkeypatch)
    record = launch._LAUNCHES[handle]
    # Fixed ephemeral test values preserve the original transport assertions.
    record.epoch, record.grant = "a" * 64, "b" * 64
    install = launch._reserve_runtime_install(handle)
    return launch._bind_runtime_install(install, owners)


def fake_transport(monkeypatch, scenario="authoring"):
    import os
    import subprocess
    import sys
    from pathlib import Path

    from exitspec.source_authoring_supervisor import _BoundedLiveSupervisor

    children = []

    def spawn(self, fd):
        child = subprocess.Popen(
            [os.path.abspath(sys.executable), "-I",
             str(Path(__file__).with_name("source_authoring_fake_worker.py")), scenario],
            env={}, close_fds=True, pass_fds=(fd,), stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0,
        )
        children.append(child)
        return child

    monkeypatch.setattr(_BoundedLiveSupervisor, "_spawn", spawn)
    return children
