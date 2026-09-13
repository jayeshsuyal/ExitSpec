"""Real local subprocess tests using an ExitSpec-owned stub, never Inferdrome."""

from __future__ import annotations

import hashlib
import json
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from exitspec import inferdrome_dispatch as transport
from exitspec.inferdrome_prospective import (
    export_prospective_handoff,
    validate_prospective_handoff,
)

ROOT = Path(__file__).resolve().parents[1]
HANDOFF = ROOT / "examples/inference-performance/inferdrome-p1"
FAKE = ROOT / "tests/fixtures/inferdrome/fake-template"
CASE = "native-p95-under-20ms"

# Pure stdlib stub. It receives the public CLI argv, records no credentials,
# and may emit a copied SYNTHETIC_ONLY fixture. It does not import either project.
STUB = r"""
import hashlib,json,os,pathlib,shutil,signal,subprocess,sys,time
mode=MODE
args=sys.argv[1:]
assert args[0]=='run' and len(args)==8,args
source=pathlib.Path(args[1]); runs=pathlib.Path(args[3]); run_id=args[5]; link=args[7]
assert args[2]=='--runs-root' and args[4]=='--run-id' and args[6]=='--expected-exitspec-contract-digest'
assert source.is_file() and link.startswith('sha256:')
assert 'FIREWORKS_API_KEY' not in os.environ and 'PYTHONPATH' not in os.environ
if mode=='fail': sys.exit(7)
if mode=='sleep': time.sleep(30)
if mode=='stubborn':
 signal.signal(signal.SIGTERM,signal.SIG_IGN)
 time.sleep(30)
if mode in ('descendant','stubborn-descendant'):
 child_code='import time; time.sleep(30)' if mode=='descendant' else 'import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(30)'
 subprocess.Popen([sys.executable,'-c',child_code])
 sys.exit(0)
if mode=='flood':
 os.write(2,b'x'*100000)
 time.sleep(30)
if mode=='invalid': print('not json');sys.exit(0)
if mode=='duplicate': print('{"run_id":"a","run_id":"b"}');sys.exit(0)
bundle=runs/run_id/'bundle'
result={'run_id':run_id,'workspace_path':str(bundle.parent),'bundle_path':str(bundle),
        'bundle_digest':'sha256:'+'a'*64,'evidence_eligibility':'SYNTHETIC_ONLY','integrity_status':'VALID'}
if mode=='synthetic':
 shutil.copytree(FAKE,bundle)
 descriptor_path=bundle/'bundle.json';descriptor=json.loads(descriptor_path.read_text())
 descriptor['run_id']=run_id
 descriptor_path.write_text(json.dumps(descriptor,sort_keys=True,separators=(',',':')))
 manifest_path=bundle/'integrity/artifact-hashes.json';manifest=json.loads(manifest_path.read_text())
 manifest['run_id']=run_id
 for entry in manifest['entries']:
  if entry['path']=='bundle.json':
   raw=descriptor_path.read_bytes();entry['sha256']='sha256:'+hashlib.sha256(raw).hexdigest();entry['size_bytes']=len(raw)
 manifest_path.write_text(json.dumps(manifest,sort_keys=True,separators=(',',':')))
 result['bundle_digest']='sha256:'+hashlib.sha256(b'inferdrome:bundle-manifest-v1\0'+manifest_path.read_bytes()).hexdigest()
if mode=='wrong-run':result['run_id']='run-'+'b'*32
if mode=='declared-customer':result['evidence_eligibility']='CUSTOMER_ELIGIBLE'
if mode=='wrong-root':result['bundle_path']='/tmp/untrusted/bundle'
if mode=='relative':result['bundle_path']='../bundle'
if mode=='extra':result['acceptance_verdict']='PASS'
if mode=='bad-digest':result['bundle_digest']='not-a-digest'
if mode=='bool':result['run_id']=True
if mode=='symlink':
 bundle.parent.mkdir()
 bundle.symlink_to(FAKE,target_is_directory=True)
print(json.dumps(result))
"""


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def setup(tmp_path, mode="synthetic", timeout=2):
    operations = tmp_path / "operations with spaces"
    runs = tmp_path / "runs with spaces"
    operations.mkdir(mode=0o700)
    runs.mkdir(mode=0o700)
    executable = tmp_path / "owned stub with spaces"
    executable.write_text(
        "#!/usr/bin/env python3\n"
        + STUB.replace("MODE", repr(mode)).replace("FAKE", repr(str(FAKE)))
    )
    executable.chmod(0o700)
    prepared = transport.prepare(
        handoff=HANDOFF,
        operations_root=operations,
        case_id=CASE,
        executable=executable,
        executable_sha256=digest(executable),
        producer_revision="f" * 40,
        runs_root=runs,
        timeout_seconds=timeout,
    )
    return Path(prepared["operation"]), prepared["plan_sha256"], executable, runs


def test_export_copies_exact_frozen_confirmation_and_refuses_overwrite(tmp_path):
    target = tmp_path / "export"
    assert export_prospective_handoff(HANDOFF, target) == validate_prospective_handoff(
        HANDOFF
    )
    assert sorted(p.relative_to(target) for p in target.rglob("*")) == sorted(
        p.relative_to(HANDOFF) for p in HANDOFF.rglob("*")
    )
    with pytest.raises(ValueError):
        export_prospective_handoff(HANDOFF, target)


def test_preflight_is_read_only_and_argv_bound_to_exact_export(tmp_path):
    operation, pin, executable, runs = setup(tmp_path)
    before = {str(p): digest(p) for p in operation.rglob("*") if p.is_file()}
    projection = transport.preflight(operation, pin)
    assert projection["execution_performed"] is False
    assert projection["acceptance_verdict"] is None
    assert projection["argv"][0] == str(executable)
    assert projection["argv"][2] == str(operation / "handoff/sources" / f"{CASE}.yaml")
    assert projection["argv"][3:5] == ["--runs-root", str(runs)]
    assert (
        projection["expected_contract_digest"]
        == "sha256:"
        + validate_prospective_handoff(HANDOFF)
        .manifest.cases[0]
        .contract_canonical_hash
    )
    assert before == {str(p): digest(p) for p in operation.rglob("*") if p.is_file()}
    assert list(runs.iterdir()) == []


def test_synthetic_return_is_rejected_by_existing_independent_verifier(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("FIREWORKS_API_KEY", "must-not-reach-child")
    operation, pin, _, _ = setup(tmp_path)
    result = transport.dispatch(operation, pin)
    assert result["current"]["state"] == "INGESTION_REJECTED"
    assert result["current"]["reason"] == "EVIDENCE_INELIGIBLE"
    assert result["current"]["acceptance_verdict"] is None
    assert result["current"]["shipping_authorized"] is False
    assert [e["state"] for e in result["history"]] == [
        "PREPARED",
        "RUNNING",
        "INGESTION_REJECTED",
    ]
    with pytest.raises(transport.DispatchRejected, match="NOT_PREPARED"):
        transport.dispatch(operation, pin)


@pytest.mark.parametrize(
    "mode,state",
    [
        ("fail", "FAILED"),
        ("sleep", "TIMED_OUT"),
        ("stubborn", "TIMED_OUT"),
        ("descendant", "TIMED_OUT"),
        ("stubborn-descendant", "TIMED_OUT"),
        ("flood", "OUTPUT_LIMIT"),
        ("invalid", "RESULT_INVALID"),
        ("duplicate", "RESULT_INVALID"),
        ("wrong-run", "RESULT_INVALID"),
        ("wrong-root", "RESULT_INVALID"),
        ("relative", "RESULT_INVALID"),
        ("extra", "RESULT_INVALID"),
        ("bad-digest", "RESULT_INVALID"),
        ("bool", "RESULT_INVALID"),
        ("missing", "INGESTION_REJECTED"),
        ("declared-customer", "INGESTION_REJECTED"),
        ("symlink", "INGESTION_REJECTED"),
    ],
)
def test_bounded_subprocess_failures_never_issue_acceptance(tmp_path, mode, state):
    operation, pin, _, _ = setup(tmp_path, mode, timeout=1)
    start = time.monotonic()
    result = transport.dispatch(operation, pin)
    assert time.monotonic() - start < 5
    assert result["current"]["state"] == state
    assert result["current"]["acceptance_verdict"] is None
    assert result["current"]["shipping_authorized"] is False
    assert (
        result["current"]["stdout_bytes"] + result["current"]["stderr_bytes"]
        <= transport.MAX_OUTPUT_BYTES + 1
    )


def test_cancel_before_spawn_and_explicit_retry_use_new_run_and_bounded_chain(tmp_path):
    operation, pin, _, runs = setup(tmp_path, "fail")
    cancelled = threading.Event()
    cancelled.set()
    assert (
        transport.dispatch(operation, pin, cancelled=cancelled)["current"]["state"]
        == "CANCELLED"
    )
    assert list(runs.iterdir()) == []
    original = (operation / "plan.json").read_bytes()
    previous_runs = {transport.status(operation, pin)["run_id"]}
    for _ in range(2):
        next_attempt = transport.retry(operation, pin)
        assert next_attempt["run_id"] not in previous_runs
        previous_runs.add(next_attempt["run_id"])
        operation, pin = Path(next_attempt["operation"]), next_attempt["plan_sha256"]
        assert transport.dispatch(operation, pin)["current"]["state"] == "FAILED"
    with pytest.raises(transport.DispatchRejected, match="NOT_RETRYABLE"):
        transport.retry(operation, pin)
    assert json.loads(original)["attempt_number"] == 1


def test_changed_executable_or_handoff_refuses_before_spawn(tmp_path):
    operation, pin, executable, runs = setup(tmp_path)
    executable.write_text(executable.read_text() + "\n# drift\n")
    assert transport.dispatch(operation, pin)["current"]["state"] == "REFUSED"
    assert list(runs.iterdir()) == []


def test_plan_digest_existing_run_and_lock_refusals(tmp_path):
    operation, pin, _, runs = setup(tmp_path)
    with pytest.raises(transport.DispatchRejected, match="PLAN_DIGEST_MISMATCH"):
        transport.dispatch(operation, "0" * 64)
    with (
        transport._lock(operation),
        pytest.raises(transport.DispatchRejected, match="DISPATCH_BUSY"),
    ):
        transport.dispatch(operation, pin)
    (runs / transport.status(operation, pin)["run_id"]).mkdir()
    assert transport.dispatch(operation, pin)["current"]["state"] == "REFUSED"


def test_cli_signal_cancels_child_and_persists_terminal_state(tmp_path):
    operation, pin, _, _ = setup(tmp_path, "stubborn", timeout=20)
    command = [
        sys.executable,
        "-B",
        "-m",
        "exitspec.cli",
        "inferdrome-handoff",
        "dispatch",
        "--operation",
        str(operation),
        "--plan-sha256",
        pin,
    ]
    child = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 8
        while transport.status(operation, pin)["current"]["state"] != "RUNNING":
            assert time.monotonic() < deadline
            time.sleep(0.02)
        time.sleep(0.1)
        child.send_signal(signal.SIGINT)
        stdout, stderr = child.communicate(timeout=5)
        assert child.returncode == 2, stderr
        assert json.loads(stdout)["current"]["state"] == "CANCELLED"
        assert transport.status(operation, pin)["current"]["state"] == "CANCELLED"
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()


@pytest.mark.parametrize(
    "drift", ["handoff", "symlink", "hardlink", "history-verdict", "history-shipping"]
)
def test_local_integrity_drift_never_dispatches(tmp_path, drift):
    operation, pin, _executable, runs = setup(tmp_path)
    if drift == "handoff":
        path = operation / "handoff/handoff-manifest.json"
        path.chmod(0o600)
        path.write_bytes(path.read_bytes() + b" ")
        assert transport.dispatch(operation, pin)["current"]["state"] == "REFUSED"
    else:
        if drift == "symlink":
            path = operation / "plan.json"
            other = operation / "other.json"
            path.rename(other)
            path.symlink_to(other)
        elif drift == "hardlink":
            import os

            os.link(operation / "plan.json", operation / "other.json")
        else:
            path = operation / "event-0.json"
            event = json.loads(path.read_bytes())
            event[
                "acceptance_verdict"
                if drift == "history-verdict"
                else "shipping_authorized"
            ] = "PASS" if drift == "history-verdict" else True
            path.write_text(json.dumps(event))
        with pytest.raises(transport.DispatchRejected):
            transport.dispatch(operation, pin)
    assert list(runs.iterdir()) == []


def test_concurrent_dispatch_is_single_use_and_cancellation_is_durable(tmp_path):
    operation, pin, _, _ = setup(tmp_path, "sleep", 20)
    cancelled = threading.Event()
    out = []
    worker = threading.Thread(
        target=lambda: out.append(
            transport.dispatch(operation, pin, cancelled=cancelled)
        )
    )
    worker.start()
    try:
        deadline = time.monotonic() + 5
        while transport.status(operation, pin)["current"]["state"] != "RUNNING":
            assert time.monotonic() < deadline
            time.sleep(0.01)
        with pytest.raises(transport.DispatchRejected, match="DISPATCH_BUSY"):
            transport.dispatch(operation, pin)
    finally:
        cancelled.set()
        worker.join(timeout=5)
    assert not worker.is_alive()
    assert out[0]["current"]["state"] == "CANCELLED"
    assert len(out[0]["history"]) == 3


@pytest.mark.parametrize(
    "location", ["descriptor", "resolved", "run", "eligibility", None]
)
def test_verified_facts_still_require_exact_link_and_never_mint_verdict(
    tmp_path, monkeypatch, location
):
    """Unit-level verifier substitution, not fabricated customer evidence."""
    from types import SimpleNamespace

    operation, pin, _, _ = setup(tmp_path, "declared-customer")
    plan = transport._load(operation, pin)
    link = transport.preflight(operation, pin)["expected_contract_digest"]
    descriptor = {
        "run_id": plan.run_id,
        "evidence_eligibility": "CUSTOMER_ELIGIBLE",
        "digests": {"exitspec_contract_digest": link},
    }
    resolved = {"links": {"exitspec_contract_digest": link}}
    if location == "descriptor":
        descriptor["digests"]["exitspec_contract_digest"] = "sha256:" + "0" * 64
    if location == "resolved":
        resolved["links"]["exitspec_contract_digest"] = "sha256:" + "0" * 64
    if location == "run":
        descriptor["run_id"] = "run-" + "0" * 32
    if location == "eligibility":
        descriptor["evidence_eligibility"] = "SYNTHETIC_ONLY"
    calls = []

    def verifier(path, **kwargs):
        calls.append((path, kwargs))
        return SimpleNamespace(descriptor=descriptor, resolved_spec=resolved)

    monkeypatch.setattr(transport, "verify_inferdrome_bundle", verifier)
    result = transport.dispatch(operation, pin)
    assert result["current"]["state"] == (
        "RESULT_INVALID" if location else "AWAITING_ADMISSION"
    )
    assert result["current"]["acceptance_verdict"] is None
    assert result["current"]["shipping_authorized"] is False
    assert calls[0][0] == Path(plan.runs_root) / plan.run_id / "bundle"
    assert calls[0][1]["require_customer_eligible"] is True
    if location is None:
        with pytest.raises(transport.DispatchRejected, match="NOT_RETRYABLE"):
            transport.retry(operation, pin)


def test_cli_export_prepare_preflight_and_retry_are_nonexecuting(tmp_path, capsys):
    from exitspec.cli import main

    exported = tmp_path / "exported"
    assert (
        main(
            [
                "inferdrome-handoff",
                "export",
                "--handoff",
                str(HANDOFF),
                "--output",
                str(exported),
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["execution_performed"] is False
    operation, pin, executable, runs = setup(tmp_path, "fail")
    assert (
        main(
            [
                "inferdrome-handoff",
                "prepare",
                "--handoff",
                str(exported),
                "--operations-root",
                str(operation.parent),
                "--runs-root",
                str(runs),
                "--case",
                CASE,
                "--executable",
                str(executable),
                "--executable-sha256",
                digest(executable),
                "--producer-revision",
                "f" * 40,
            ]
        )
        == 0
    )
    prepared = json.loads(capsys.readouterr().out)
    assert prepared["execution_performed"] is False
    for action in ["preflight", "status"]:
        assert (
            main(
                [
                    "inferdrome-handoff",
                    action,
                    "--operation",
                    str(operation),
                    "--plan-sha256",
                    pin,
                ]
            )
            == 0
        )
        assert json.loads(capsys.readouterr().out)["current"]["state"] == "PREPARED"
    assert list(runs.iterdir()) == []
    assert (
        main(
            [
                "inferdrome-handoff",
                "retry",
                "--operation",
                str(operation),
                "--plan-sha256",
                pin,
            ]
        )
        == 2
    )
    assert json.loads(capsys.readouterr().out)["error"] == "LOCAL_HANDOFF_REFUSED"


@pytest.mark.parametrize("failure", ["launch", "control"])
def test_process_failures_preserve_retry_safety(tmp_path, monkeypatch, failure):
    operation, pin, _, _ = setup(tmp_path, "fail")

    def unavailable(*args, **kwargs):
        raise OSError("synthetic operating-system error")

    if failure == "launch":
        monkeypatch.setattr(transport.subprocess, "Popen", unavailable)
    else:
        monkeypatch.setattr(transport, "_execute", unavailable)
    result = transport.dispatch(operation, pin)
    assert result["current"]["state"] == "FAILED"
    assert result["current"]["reason"] == (
        "PROCESS_UNAVAILABLE" if failure == "launch" else "PROCESS_CONTROL_FAILED"
    )
    if failure == "control":
        with pytest.raises(transport.DispatchRejected, match="NOT_RETRYABLE"):
            transport.retry(operation, pin)
    else:
        assert transport.retry(operation, pin)["current"]["state"] == "PREPARED"


def test_corrupt_terminal_state_is_refused_without_reexecution(tmp_path):
    operation, pin, _, _ = setup(tmp_path, "fail")
    transport.dispatch(operation, pin)
    event_path = operation / "event-2.json"
    event = json.loads(event_path.read_text())
    event["state"] = {"invalid": "state"}
    event_path.write_text(json.dumps(event))
    with pytest.raises(transport.DispatchRejected, match="INVALID_HISTORY"):
        transport.status(operation, pin)
    with pytest.raises(transport.DispatchRejected, match="INVALID_HISTORY"):
        transport.dispatch(operation, pin)
