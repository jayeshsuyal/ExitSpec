"""Bounded demo control/usage regressions. All transports are synthetic."""
import hashlib
import json
import os
import subprocess
import sys
from dataclasses import asdict, replace
from decimal import Decimal

import pytest

from exitspec import source_authoring_demo_run as run
from exitspec import source_authoring_launch as launch
from exitspec import source_authoring_operator as operator
from exitspec.source_authoring_operations import (
    SourceAuthoringOperationError,
    create_live_source_authoring_operations,
)
from exitspec.source_authoring_transport import decode_response, observe_response
from tests.helpers.source_authoring_demo import (
    demo_profile,
    fake_demo_transport,
    install_demo_profile,
    make_demo_launch,
)
from tests.test_source_authoring_operations import setup


def demo_setup(monkeypatch, tmp_path, scenario='authoring'):
    synthetic, _, old_permit, _, context = setup()
    handle, profile, path, _digest = make_demo_launch(monkeypatch, tmp_path)
    ops = create_live_source_authoring_operations(owners=synthetic._owners,
                                                 installation=launch._reserve_runtime_install(handle))
    session = ops.new_live_session()
    source_id = synthetic._records[old_permit._operation].source.source_receipt_id
    disclosure = ops.prepare(session, context[0], source_id)
    permit = ops.authorize(session, disclosure, acknowledged=True, idempotency_key='demo')
    children = fake_demo_transport(monkeypatch, path, scenario)
    return ops, session, permit, handle, profile, path, context, source_id, children


@pytest.mark.parametrize('scenario,expected,usage', [
    ('authoring', 'SUCCEEDED', 'REPORTED'), ('demo_delayed', 'SUCCEEDED', 'REPORTED'),
    ('demo_rejected', 'FAILED', 'REPORTED'), ('demo_over_limit', 'OUTCOME_UNKNOWN', 'REPORTED'),
    ('demo_missing', 'OUTCOME_UNKNOWN', 'ABSENT'), ('demo_malformed', 'OUTCOME_UNKNOWN', 'MALFORMED')])
def test_usage_survives_rejection_and_one_claim_preserves_result(monkeypatch, tmp_path, scenario, expected, usage):
    ops, session, permit, handle, _profile, _path, context, source_id, children = demo_setup(monkeypatch, tmp_path, scenario)
    result = ops.execute_live(session, permit)
    assert result.state == expected
    assert not ops._closed and ops.ledger == (1, Decimal('0.01'))
    assert ops.execute_live(session, permit) == result
    assert ops.status(session, permit) == result
    assert len(children) == 1 and children[0].returncode == 0
    observation = json.loads((tmp_path/'run/observation.json').read_bytes())
    assert observation['usage_status'] == usage and observation['billing_status'] == 'UNKNOWN'
    assert observation['body_sha256'] == ops._records[permit._operation].intent.body_sha256
    if scenario == 'demo_over_limit':
        assert observation['provider_usage']['prompt_tokens'] == 9000
    if usage != 'REPORTED':
        assert observation['provider_usage'] is None
    with pytest.raises(SourceAuthoringOperationError, match='demo_consumed'):
        ops.prepare(session, context[0], source_id)
    assert bool(context[3]._results_by_request) == (expected == 'SUCCEEDED')
    handle.revoke()


def test_receipt_failure_does_not_publish_or_refund(monkeypatch, tmp_path):
    ops, session, permit, handle, profile, _path, context, _source_id, children = demo_setup(monkeypatch, tmp_path)
    monkeypatch.setattr(run, 'observe', lambda *_: (_ for _ in ()).throw(run.DemoRunError()))
    result = ops.execute_live(session, permit)
    assert result.state == 'FAILED' and not context[3]._results_by_request
    assert run.consumed(profile) and len(children) == 1
    assert json.loads((tmp_path/'run/consumed.json').read_bytes())['provider_usage'] is None
    assert not ops._closed
    handle.revoke()


def test_atomic_claim_across_processes_and_restarts(tmp_path):
    profile = demo_profile(tmp_path)
    path = tmp_path/'profile.json'
    path.write_text(json.dumps(asdict(profile)))
    code = '''import json,sys
from types import SimpleNamespace
from exitspec.source_authoring_demo_run import consume,DemoRunError
p=SimpleNamespace(**json.load(open(sys.argv[1])))
try: consume(p,'b'*64,operation='c'*64,source_sha256='d'*64,body_sha256='e'*64)
except DemoRunError: sys.exit(2)
'''
    processes = [subprocess.Popen([sys.executable, '-I', '-c', code, str(path)], env={}) for _ in range(6)]
    codes = [p.wait(timeout=10) for p in processes]
    assert codes.count(0) == 1 and codes.count(2) == 5
    assert subprocess.run([sys.executable, '-I', '-c', code, str(path)], env={}, check=False).returncode == 2
    assert run.consumed(profile)


@pytest.mark.parametrize('fault', ['file_sync', 'directory_sync', 'write', 'partial', 'alternate', 'replaced', 'symlink', 'permissions'])
def test_consumption_faults_fail_closed(monkeypatch, tmp_path, fault):
    profile = demo_profile(tmp_path)
    directory = tmp_path/'run'
    if fault in {'file_sync', 'directory_sync'}:
        count = 0
        original = os.fsync
        def fail(fd):
            nonlocal count
            count += 1
            if count == (1 if fault == 'file_sync' else 2):
                raise OSError('synthetic sync failure')
            return original(fd)
        monkeypatch.setattr(os, 'fsync', fail)
    elif fault == 'write':
        monkeypatch.setattr(os, 'write', lambda *_: 0)
    elif fault == 'partial':
        (directory/'consumed.json').write_bytes(b'{')
    elif fault == 'alternate':
        other = tmp_path/'other'
        other.mkdir(mode=0o700)
        profile = replace(profile, run_directory=str(other))
    elif fault == 'replaced':
        directory.rename(tmp_path/'original')
        directory.mkdir(mode=0o700)
    elif fault == 'symlink':
        directory.rename(tmp_path/'original')
        directory.symlink_to(tmp_path/'original', target_is_directory=True)
    elif fault == 'permissions':
        directory.chmod(0o755)
    with pytest.raises(run.DemoRunError):
        run.consume(profile, 'b'*64, operation='c'*64, source_sha256='d'*64, body_sha256='e'*64)
    if fault in {'file_sync', 'directory_sync', 'write', 'partial'}:
        assert run.consumed(profile)


def test_demo_admission_is_distinct_and_requires_exact_record(monkeypatch, tmp_path):
    profile, path, digest = install_demo_profile(monkeypatch, tmp_path)
    with pytest.raises(launch.SourceAuthoringLaunchError):
        launch._admit_operator_profile(profile.approval_id, approval_file=str(path), expected_sha256=digest)
    with pytest.raises(launch.SourceAuthoringLaunchError):
        launch._admit_operator_profile(profile.approval_id, approval_file=str(path), expected_sha256='0'*64, demo=True)
    admitted = launch._admit_operator_profile(profile.approval_id, approval_file=str(path), expected_sha256=digest, demo=True)
    assert launch._PRODUCTION_PROFILES == launch._QUALIFIED_SERVING_CONTRACTS == ()
    assert launch._is_demo(launch._admitted_profile(admitted))
    assert operator.main([]) == 2


def test_observation_does_not_change_decoder_acceptance():
    from tests.test_source_authoring_transport import encode, envelope
    value = envelope()
    value['usage'] = {'prompt_tokens': 9000, 'completion_tokens': 3, 'total_tokens': 9003}
    raw = encode(value)
    observation = observe_response(raw)
    assert observation['provider_usage']['prompt_tokens'] == 9000
    assert observation['response_sha256'] == hashlib.sha256(raw).hexdigest()
    with pytest.raises(ValueError):
        decode_response(raw)


@pytest.mark.parametrize('ending', ['cancel', 'timeout'])
def test_uncertain_delivery_preserves_unknown_usage_and_no_retry(monkeypatch, tmp_path, ending):
    import time

    from exitspec.source_authoring_supervisor import _BoundedLiveSupervisor
    ops, session, permit, handle, profile, _path, context, _source, children = demo_setup(monkeypatch, tmp_path, 'stall_result')
    if ending == 'timeout':
        prepare = _BoundedLiveSupervisor.prepare
        def short(self, metadata, *, deadline):
            return prepare(self, metadata, deadline=min(deadline, time.monotonic() + 0.3))
        monkeypatch.setattr(_BoundedLiveSupervisor, 'prepare', short)
    else:
        handoff = _BoundedLiveSupervisor.handoff
        def cancel(self):
            handoff(self)
            ops.revoke(session, permit)
        monkeypatch.setattr(_BoundedLiveSupervisor, 'handoff', cancel)
    result = ops.execute_live(session, permit)
    assert result.state == 'OUTCOME_UNKNOWN' and not context[3]._results_by_request
    assert run.consumed(profile) and len(children) == 1
    record = json.loads((tmp_path/'run/consumed.json').read_bytes())
    assert record['usage_status'] == record['billing_status'] == 'UNKNOWN'
    assert record['provider_usage'] is None and not (tmp_path/'run/observation.json').exists()
    assert ops.execute_live(session, permit) == result
    handle.revoke()


def test_delayed_result_remains_pollable_when_new_admission_closes(monkeypatch, tmp_path):
    import threading
    import time
    ops, session, permit, handle, profile, _path, context, source, children = demo_setup(monkeypatch, tmp_path, 'demo_delayed')
    other_session = ops.new_live_session()
    other = ops.prepare(other_session, context[0], source)
    other_permit = ops.authorize(other_session, other, acknowledged=True, idempotency_key='second')
    results = []
    thread = threading.Thread(target=lambda: results.append(ops.execute_live(session, permit)))
    thread.start()
    try:
        deadline = time.monotonic() + 3
        while not run.consumed(profile) and time.monotonic() < deadline:
            time.sleep(0.01)
        assert run.consumed(profile)
        assert ops.status(session, permit).state in {'CLAIMED', 'DISPATCH_AUTHORIZED'}
        with pytest.raises(SourceAuthoringOperationError):
            ops.execute_live(other_session, other_permit)
        assert not ops._closed
        thread.join(timeout=12)
        assert not thread.is_alive() and results[0].state == 'SUCCEEDED'
        assert ops.status(session, permit) == results[0]
        assert len(children) == 1 and context[4].list_proposals(context[0])
    finally:
        handle.revoke()
        thread.join(timeout=2)


def test_installed_child_dispatch_fence_rejects_replay_and_wrong_body(tmp_path):
    from types import SimpleNamespace
    profile = demo_profile(tmp_path)
    binding = SimpleNamespace(operation='c'*64, body_sha256='e'*64,
                              launch_profile_sha256=profile.launch_profile_sha256)
    with pytest.raises(run.DemoRunError):
        run.claim_dispatch(profile, binding)
    run.consume(profile, 'b'*64, operation='c'*64, source_sha256='d'*64, body_sha256='e'*64)
    wrong = SimpleNamespace(**vars(binding))
    wrong.body_sha256 = 'f'*64
    with pytest.raises(run.DemoRunError):
        run.claim_dispatch(profile, wrong)
    assert not (tmp_path/'run/dispatch.json').exists()
    run.claim_dispatch(profile, binding)
    with pytest.raises(run.DemoRunError):
        run.claim_dispatch(profile, binding)


def test_demo_transport_returns_observation_without_changing_original_decoder(monkeypatch):
    import time

    from exitspec import source_authoring_transport as transport
    from tests.test_source_authoring_transport import (
        FakeResponse,
        encode,
        envelope,
        fake_transport,
        request_body,
    )
    value = envelope()
    value['usage']['prompt_tokens'] = 9000
    value['usage']['total_tokens'] = 9000 + value['usage']['completion_tokens']
    response = FakeResponse(raw=encode(value))
    connection, calls = fake_transport(monkeypatch, response)
    raw = transport._post_exact(request_body(), b'SYNTHETIC-KEY', deadline=time.monotonic()+2,
                                demo_observation=True)
    assert len(calls) == len(connection.requests) == 1
    assert observe_response(raw)['provider_usage']['prompt_tokens'] == 9000
    with pytest.raises(ValueError):
        decode_response(raw)
