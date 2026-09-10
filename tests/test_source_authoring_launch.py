"""Admission identity, inert metadata, token proof and side-effect ordering."""
import copy
import os
import pickle
import socket
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from exitspec import source_authoring_launch as launch
from exitspec import source_authoring_operator as operator
from exitspec.poc_source_demo import (
    SourceNeutralPOCDemoServer,
    serve_source_neutral_demo,
)
from exitspec.source_authoring_operations import create_live_source_authoring_operations
from exitspec.source_authoring_supervisor import _BoundedLiveSupervisor
from tests.helpers.source_authoring_admission import (
    bound_lease,
    fake_profile,
    make_launch,
)
from tests.test_source_authoring_live_worker import admission, body


@pytest.mark.parametrize("entry", ["admit", "factory", "operator", "server", "serve", "supervisor"])
def test_empty_installed_registry_refuses_before_effects(monkeypatch, entry, capsys):
    effects = []

    def forbidden(*args, **kwargs):
        effects.append(True)
        raise AssertionError("PRIVATE-MARKER")

    monkeypatch.setattr(launch, "_PRODUCTION_PROFILES", ())
    for module, name in ((os, "open"), (os, "pipe"), (os, "read"), (subprocess, "Popen"),
                         (socket, "socket"), (socket, "getaddrinfo"),
                         (operator, "_read_credential_tty"), (operator, "_read_text_tty")):
        monkeypatch.setattr(module, name, forbidden)
    fake = object.__new__(launch.LiveSourceAuthoringLaunch)
    calls = {
        "admit": lambda: launch._admit_operator_profile("PRIVATE-MARKER"),
        "factory": lambda: create_live_source_authoring_operations(installation=fake),
        "server": lambda: SourceNeutralPOCDemoServer(("127.0.0.1", 0), source_authoring_launch=fake),
        "serve": lambda: serve_source_neutral_demo(source_authoring_launch=fake),
        "supervisor": lambda: _BoundedLiveSupervisor(lease=fake),
    }
    if entry == "operator":
        assert operator.main(["--approval-id", "PRIVATE-MARKER", "--key", "PRIVATE-MARKER"]) == 2
    else:
        with pytest.raises(ValueError) as error:
            calls[entry]()
        assert "PRIVATE-MARKER" not in str(error.value)
    captured = capsys.readouterr()
    assert "PRIVATE-MARKER" not in captured.out + captured.err and effects == []


@pytest.mark.parametrize("kind", [launch._AdmittedProfile, launch.LiveSourceAuthoringLaunch,
                                  launch._RuntimeInstall, launch._LiveLease, launch._VerifiedTokenProof])
def test_launch_handles_refuse_public_construction_and_serialization(kind):
    with pytest.raises(launch.SourceAuthoringLaunchError):
        kind()
    forged = object.__new__(kind)
    for clone in (copy.copy, copy.deepcopy, pickle.dumps):
        with pytest.raises(launch.SourceAuthoringLaunchError):
            clone(forged)


@pytest.mark.parametrize("field,value", [
    ("approval_id", ""), ("code_revision", "a" * 39), ("code_tree", True),
    ("launch_profile_sha256", "a" * 64), ("input_tokens_max", True), ("input_tokens_max", 8192.0),
    ("output_tokens_max", 2001), ("request_budget_usd", 0.01), ("launch_budget_usd", "NaN"),
    ("valid_until", 0.0), ("valid_from", float("nan")), ("valid_from", True),
    ("valid_until", float("inf")), ("files", ()), ("tokenizer_artifacts", ()),
    ("tokenizer_identity", "bad\nmarker"), ("region", ""), ("account_pricing_approval", ""),
    ("custody_approval", ""), ("header_policy_sha256", "0" * 64),
])
def test_invalid_compiled_metadata_refuses_before_verification(monkeypatch, field, value):
    profile = replace(fake_profile(), **{field: value})
    # A matching digest never upgrades invalid fields into qualified metadata.
    if field != "launch_profile_sha256":
        try:
            profile = replace(profile, launch_profile_sha256=launch._profile_digest(profile))
        except (ValueError, TypeError):
            pass
    monkeypatch.setattr(launch, "_PRODUCTION_PROFILES", (profile,))
    effects = []
    monkeypatch.setattr(launch, "_verify_code_and_artifacts", lambda *_: effects.append(True))
    with pytest.raises(launch.SourceAuthoringLaunchError):
        launch._admit_operator_profile(profile.approval_id)
    assert effects == []


def test_matching_profile_copy_is_inert(monkeypatch):
    profile = fake_profile()
    monkeypatch.setattr(launch, "_PRODUCTION_PROFILES", (profile,))
    with pytest.raises(launch.SourceAuthoringLaunchError):
        launch._require_profile(replace(profile))
    assert launch._require_profile(profile) is profile


def test_one_reservation_wins_concurrently_and_revocation_cannot_restore_it(monkeypatch):
    handle = make_launch(monkeypatch)

    def reserve(_):
        try:
            return launch._reserve_runtime_install(handle)
        except launch.SourceAuthoringLaunchError:
            return None

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(reserve, range(4)))
    assert sum(item is not None for item in results) == 1
    install = next(item for item in results if item is not None)
    handle.revoke()
    assert launch._LAUNCHES[handle].credential == b"" and launch._LAUNCHES[handle].generation == 2
    with pytest.raises(launch.SourceAuthoringLaunchError):
        launch._reserve_runtime_install(handle)
    assert launch._INSTALLS[install] is handle


@pytest.mark.parametrize("count", [True, 1.0, 0, -1, 8193, "100", None])
def test_public_token_counts_never_substitute_for_a_verified_proof(monkeypatch, count):
    lease = bound_lease(monkeypatch)
    monkeypatch.setattr(launch, "_evaluate_local_tokens", lambda *_: count)
    with pytest.raises(launch.SourceAuthoringLaunchError):
        launch._issue_token_proof(lease, body())
    assert launch._TOKENS == {}


def test_token_proof_binds_exact_lease_generation_and_body(monkeypatch):
    first, second = bound_lease(monkeypatch), bound_lease(monkeypatch)
    raw = body()
    proof = launch._issue_token_proof(first, raw)
    assert launch._token_metadata(first, proof, raw)["input_tokens"] == 100
    for lease, candidate, payload in ((second, proof, raw), (first, object(), raw),
                                     (first, proof, body("Changed literal synthetic text."))):
        with pytest.raises(launch.SourceAuthoringLaunchError):
            launch._credential_for_ticket(lease, candidate, payload)
    handle = launch._LEASES[first]
    handle.revoke()
    assert proof not in launch._TOKENS
    with pytest.raises(launch.SourceAuthoringLaunchError):
        launch._token_metadata(first, proof, raw)


@pytest.mark.parametrize("field,value", [("epoch", "9" * 64), ("grant", "9" * 64),
                                        ("profile_sha256", "9" * 64), ("launch_profile_sha256", "9" * 64),
                                        ("credential_generation", 2), ("code_revision", "9" * 40)])
def test_parent_binding_mismatch_has_no_pipe_effect(monkeypatch, field, value):
    import time
    lease = bound_lease(monkeypatch)
    supervisor = _BoundedLiveSupervisor(lease=lease)
    effects = []
    monkeypatch.setattr(os, "pipe", lambda: effects.append(True))
    with pytest.raises(ValueError):
        supervisor.prepare(admission(body()) | {field: value}, time.monotonic() + 1)
    assert effects == [] and supervisor.state == "NEW"


@pytest.mark.parametrize("fault", ["overflow", "timeout"])
def test_local_git_reader_caps_output_and_reaps_its_own_probe(monkeypatch, fault, tmp_path):
    import sys
    import time
    original = subprocess.Popen
    children = []

    def spawn(_args, **kwargs):
        code = "import time; time.sleep(10)" if fault == "timeout" else "import os; os.write(1, b'x' * 65536)"
        child = original([sys.executable, "-I", "-c", code], **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(launch.subprocess, "Popen", spawn)
    started = time.monotonic()
    with pytest.raises(launch.SourceAuthoringLaunchError):
        launch._git_read(tmp_path, ["status"], started + 0.2, [16])
    assert time.monotonic() - started < 1.3
    assert len(children) == 1 and children[0].poll() is not None and children[0].stdout.closed


@pytest.mark.parametrize("fault", ["symlink", "fifo", "directory", "file_cap", "aggregate_cap", "deadline"])
def test_local_artifact_reader_refuses_nonregular_unbounded_or_late_input(tmp_path, fault):
    import time
    path = tmp_path / "artifact"
    budget, deadline = [64 * 1024 * 1024], time.monotonic() + 1
    if fault == "symlink":
        target = tmp_path / "target"
        target.write_bytes(b"synthetic")
        path.symlink_to(target)
    elif fault == "fifo":
        os.mkfifo(path)
    elif fault == "directory":
        path.mkdir()
    else:
        path.write_bytes(b"x" * (8 * 1024 * 1024 + 1) if fault == "file_cap" else b"synthetic")
        if fault == "aggregate_cap":
            budget = [1]
        if fault == "deadline":
            deadline = time.monotonic() - 1
    started = time.monotonic()
    with pytest.raises(launch.SourceAuthoringLaunchError):
        launch._hash_local_file(path, deadline, budget)
    assert time.monotonic() - started < 1


def test_failed_owner_binding_aborts_only_an_unconsumed_installation(monkeypatch):
    handle = make_launch(monkeypatch)
    install = launch._reserve_runtime_install(handle)
    with pytest.raises(ValueError):
        create_live_source_authoring_operations(owners=object(), installation=install)
    assert launch._LAUNCHES[handle].state == "REVOKED" and launch._LAUNCHES[handle].credential == b""


def test_losing_bind_cannot_revoke_already_bound_engine(monkeypatch):
    from tests.test_source_authoring_operations import setup
    synthetic, *_ = setup()
    handle = make_launch(monkeypatch)
    install = launch._reserve_runtime_install(handle)
    engine = create_live_source_authoring_operations(owners=synthetic._owners, installation=install)
    with pytest.raises(ValueError):
        create_live_source_authoring_operations(owners=synthetic._owners, installation=install)
    assert engine.new_live_session() is not None and launch._LAUNCHES[handle].state == "BOUND"
    engine.shutdown()
    assert launch._LAUNCHES[handle].state == "REVOKED" and launch._LAUNCHES[handle].credential == b""
