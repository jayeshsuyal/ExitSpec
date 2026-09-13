"""Real local subprocess tests using an ExitSpec-owned stub, never Inferdrome."""

from __future__ import annotations

import hashlib
import json
import os
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
if mode.startswith('detached'):
 marker=runs/(run_id+'.pid')
 child_code='import os,pathlib,signal,sys,time; '+('signal.signal(signal.SIGTERM,signal.SIG_IGN); ' if 'stubborn' in mode else '')+'pathlib.Path(sys.argv[1]).write_text(str(os.getpid())); time.sleep(30)'
 subprocess.Popen([sys.executable,'-c',child_code,str(marker)],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
 deadline=time.monotonic()+5
 while not marker.exists():
  assert time.monotonic()<deadline
  time.sleep(.01)
 print('offline descendant started',flush=True)
 if 'parentloss' in mode: time.sleep(30)
 sys.exit(7 if 'nonzero' in mode else 0)
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
        ("descendant", "RESULT_INVALID"),
        ("stubborn-descendant", "RESULT_INVALID"),
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


def process_is_executing(pid):
    result = subprocess.run(
        ["/bin/ps", "-o", "stat=", "-p", str(pid)],
        capture_output=True,
        timeout=1,
        check=False,
    )
    assert result.returncode in (0, 1)
    return bool(result.stdout.strip()) and result.stdout.strip()[:1] not in (b"Z", b"X")


@pytest.mark.parametrize(
    "mode",
    [
        "detached",
        "detached-nonzero",
        "detached-stubborn",
        "detached-stubborn-nonzero",
    ],
)
def test_leader_exit_stops_detached_stdio_descendants_before_terminal(tmp_path, mode):
    operation, pin, _, runs = setup(tmp_path, mode, timeout=1)
    result = transport.dispatch(operation, pin)
    assert result["current"]["state"] == (
        "FAILED" if "nonzero" in mode else "RESULT_INVALID"
    )
    marker = runs / (result["run_id"] + ".pid")
    assert not process_is_executing(int(marker.read_text()))
    assert result["current"]["returncode"] == (7 if "nonzero" in mode else 0)
    assert transport.retry(operation, pin)["current"]["state"] == "PREPARED"


def test_retry_replays_one_child_even_after_child_dispatch(tmp_path):
    operation, pin, _, _ = setup(tmp_path, "fail")
    transport.dispatch(operation, pin)
    child = transport.retry(operation, pin)
    replay = transport.retry(operation, pin)
    assert replay["operation"] == child["operation"]
    assert replay["plan_sha256"] == child["plan_sha256"]
    transport.dispatch(Path(child["operation"]), child["plan_sha256"])
    replay = transport.retry(operation, pin)
    assert replay["operation"] == child["operation"]
    assert replay["current"]["state"] == "FAILED"
    assert replay["execution_performed"] is False
    assert len(list(operation.parent.iterdir())) == 2


def test_concurrent_retry_has_only_one_reserved_successor(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    operation, pin, _, _ = setup(tmp_path, "fail")
    transport.dispatch(operation, pin)
    barrier = threading.Barrier(8)

    def attempt(_):
        barrier.wait(timeout=3)
        try:
            return transport.retry(operation, pin)["operation"]
        except transport.DispatchRejected as error:
            assert error.code == "DISPATCH_BUSY"
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(attempt, range(8)))
    issued = {item for item in results if item}
    assert len(issued) == 1
    assert transport.retry(operation, pin)["operation"] in issued
    assert len(list(operation.parent.iterdir())) == 2


@pytest.mark.parametrize("partial_directory", [False, True])
def test_interrupted_retry_materialization_never_allocates_siblings(
    tmp_path, monkeypatch, partial_directory
):
    operation, pin, _, runs = setup(tmp_path, "fail")
    transport.dispatch(operation, pin)
    original = transport._prepare_plan

    def interrupt(handoff, operations_root, plan):
        if partial_directory:
            (operations_root / plan.operation_id).mkdir(mode=0o700)
        raise OSError("interrupted offline preparation")

    monkeypatch.setattr(transport, "_prepare_plan", interrupt)
    with pytest.raises(OSError):
        transport.retry(operation, pin)
    reserved = (operation / "retry.json").read_bytes()
    identity = json.loads(reserved)["child_plan"]["operation_id"]
    monkeypatch.setattr(transport, "_prepare_plan", original)
    if partial_directory:
        for _ in range(2):
            with pytest.raises(
                transport.DispatchRejected, match="RETRY_MATERIALIZATION_INCOMPLETE"
            ):
                transport.retry(operation, pin)
    else:
        assert Path(transport.retry(operation, pin)["operation"]).name == identity
    assert (operation / "retry.json").read_bytes() == reserved
    assert len(list(operation.parent.iterdir())) == 2
    assert not list(runs.iterdir())


@pytest.mark.parametrize(
    "field",
    [
        "parent_plan_sha256",
        "run_id",
        "operation_id",
        "retry_of",
        "executable_sha256",
        "timeout_seconds",
        "manifest_sha256",
    ],
)
def test_retry_reservation_replay_requires_exact_parent_config_and_child(
    tmp_path, field
):
    operation, pin, _, _ = setup(tmp_path, "fail")
    transport.dispatch(operation, pin)
    transport.retry(operation, pin)
    path = operation / "retry.json"
    record = json.loads(path.read_bytes())
    if field == "parent_plan_sha256":
        record[field] = "0" * 64
    elif field == "run_id":
        record["child_plan"][field] = json.loads(
            (operation / "plan.json").read_bytes()
        )[field]
    elif field == "operation_id":
        record["child_plan"][field] = operation.name
    elif field == "timeout_seconds":
        record["child_plan"][field] += 1
    else:
        record["child_plan"][field] = (
            "iop_" + "0" * 32 if field == "retry_of" else "0" * 64
        )
    path.write_text(json.dumps(record))
    with pytest.raises(transport.DispatchRejected, match="INVALID_RETRY_RESERVATION"):
        transport.retry(operation, pin)
    assert len(list(operation.parent.iterdir())) == 2


def test_retry_publication_failure_cannot_materialize_a_child(tmp_path, monkeypatch):
    operation, pin, _, _ = setup(tmp_path, "fail")
    transport.dispatch(operation, pin)
    original = transport.os.rename

    def interrupt(source, destination):
        if Path(destination).name == "retry.json":
            raise OSError("offline atomic publication failure")
        return original(source, destination)

    monkeypatch.setattr(transport.os, "rename", interrupt)
    with pytest.raises(OSError):
        transport.retry(operation, pin)
    assert not (operation / "retry.json").exists()
    assert len(list(operation.parent.iterdir())) == 1
    assert not list(operation.glob(".record-*"))
    monkeypatch.setattr(transport.os, "rename", original)
    transport.retry(operation, pin)
    assert len(list(operation.parent.iterdir())) == 2


@pytest.mark.parametrize(
    "raw", [b"", b"R\0", b"X\0\0\0\0", b"R\0\0\0\0extra", b"F\0\0\0\1"]
)
def test_invalid_or_truncated_worker_status_blocks_retry(tmp_path, monkeypatch, raw):
    operation, pin, _, _ = setup(tmp_path, "fail")

    def command(status_fd, owner_fd, argv):
        code = (
            "import os,signal,time; signal.signal(signal.SIGTERM,lambda *_:None); "
            f"os.write({status_fd},{raw!r}); os.close({status_fd}); time.sleep(30)"
        )
        return [sys.executable, "-I", "-B", "-c", code]

    monkeypatch.setattr(transport, "_worker_command", command)
    result = transport.dispatch(operation, pin)
    assert result["current"]["reason"] == "PROCESS_CONTROL_FAILED"
    with pytest.raises(transport.DispatchRejected, match="NOT_RETRYABLE"):
        transport.retry(operation, pin)


def test_unavailable_process_inspection_blocks_retry(tmp_path, monkeypatch):
    operation, pin, _, _ = setup(tmp_path, "detached-stubborn")

    def unavailable(group):
        raise OSError("offline process inspection unavailable")

    monkeypatch.setattr(transport, "_group_is_stopped", unavailable)
    result = transport.dispatch(operation, pin)
    assert result["current"]["reason"] == "PROCESS_CONTROL_FAILED"
    with pytest.raises(transport.DispatchRejected, match="NOT_RETRYABLE"):
        transport.retry(operation, pin)


def test_owner_control_pipe_loss_kills_anchor_and_detached_descendant(tmp_path):
    status_read, status_write = os.pipe()
    owner_read, owner_write = os.pipe()
    marker = tmp_path / "child.pid"
    child_code = (
        "import os,pathlib,signal,sys,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); "
        "pathlib.Path(sys.argv[1]).write_text(str(os.getpid())); time.sleep(30)"
    )
    producer = (
        "import subprocess,sys,time; "
        f'subprocess.Popen([sys.executable,"-c",{child_code!r},{str(marker)!r}],'
        "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); time.sleep(30)"
    )
    anchor = subprocess.Popen(
        transport._worker_command(
            status_write, owner_read, [sys.executable, "-c", producer]
        ),
        pass_fds=(status_write, owner_read),
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    os.close(status_write)
    os.close(owner_read)
    try:
        deadline = time.monotonic() + 5
        while not marker.exists():
            assert time.monotonic() < deadline
            time.sleep(0.02)
        os.close(owner_write)
        owner_write = None
        # Keep the anchor unreaped until inspecting its reserved group.
        deadline = time.monotonic() + 3
        while not transport._group_is_stopped(anchor.pid):
            assert time.monotonic() < deadline
            time.sleep(0.02)
        assert not process_is_executing(int(marker.read_text()))
    finally:
        if owner_write is not None:
            os.close(owner_write)
        anchor.wait(timeout=3)
        os.close(status_read)


def test_dispatch_owner_death_stops_work_and_leaves_nonretryable_running_record(
    tmp_path,
):
    operation, pin, _, runs = setup(tmp_path, "detached-stubborn-parentloss", 20)
    owner = subprocess.Popen(
        [
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
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    marker = runs / (transport.status(operation, pin)["run_id"] + ".pid")
    try:
        deadline = time.monotonic() + 5
        while not marker.exists():
            assert time.monotonic() < deadline
            time.sleep(0.02)
        descendant = int(marker.read_text())
        owner.kill()
        owner.wait(timeout=3)
        deadline = time.monotonic() + 3
        while process_is_executing(descendant):
            assert time.monotonic() < deadline
            time.sleep(0.02)
        assert transport.status(operation, pin)["current"]["state"] == "RUNNING"
        with pytest.raises(transport.DispatchRejected, match="NOT_RETRYABLE"):
            transport.retry(operation, pin)
    finally:
        if owner.poll() is None:
            owner.kill()
        owner.wait(timeout=3)


def test_cleanup_signals_only_unreaped_anchor_group(tmp_path, monkeypatch):
    operation, pin, _, _ = setup(tmp_path, "detached-stubborn")
    sentinel = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True
    )
    original = transport.os.killpg
    groups = []

    def checked(group, sig):
        assert group != sentinel.pid
        assert group != os.getpgrp()
        # Group identity must still be reserved by our unreaped child anchor.
        process = subprocess.run(
            ["/bin/ps", "-o", "ppid=", "-p", str(group)],
            capture_output=True,
            timeout=1,
            check=True,
        )
        assert int(process.stdout.strip()) == os.getpid()
        groups.append(group)
        return original(group, sig)

    monkeypatch.setattr(transport.os, "killpg", checked)
    try:
        result = transport.dispatch(operation, pin)
        assert result["current"]["state"] == "RESULT_INVALID"
        assert len(set(groups)) == 1
        assert sentinel.poll() is None
    finally:
        sentinel.kill()
        sentinel.wait(timeout=3)


def test_cancel_during_supervisor_startup_verifies_dead_group(tmp_path, monkeypatch):
    operation, pin, _, _ = setup(tmp_path, "sleep", 20)
    monkeypatch.setattr(
        transport,
        "_worker_command",
        lambda *_: [sys.executable, "-I", "-B", "-c", "import time; time.sleep(30)"],
    )

    class CancelAfterSpawn:
        calls = 0

        def is_set(self):
            self.calls += 1
            return self.calls > 1

    result = transport.dispatch(operation, pin, cancelled=CancelAfterSpawn())
    assert result["current"]["state"] == "CANCELLED"
    assert transport.retry(operation, pin)["current"]["state"] == "PREPARED"


@pytest.mark.parametrize("corruption", ["reservation-truncated", "child-plan-changed"])
def test_retry_partial_records_fail_closed_without_siblings(tmp_path, corruption):
    operation, pin, _, _ = setup(tmp_path, "fail")
    transport.dispatch(operation, pin)
    child = transport.retry(operation, pin)
    if corruption == "reservation-truncated":
        (operation / "retry.json").write_bytes(b'{"parent_plan_sha256":')
    else:
        path = Path(child["operation"]) / "plan.json"
        path.write_bytes(path.read_bytes() + b" ")
    for _ in range(2):
        with pytest.raises(transport.DispatchRejected):
            transport.retry(operation, pin)
    assert len(list(operation.parent.iterdir())) == 2
