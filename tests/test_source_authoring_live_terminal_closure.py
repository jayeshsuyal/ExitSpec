"""Real completed evidence/terminal owner controls on the sealed live realm."""
import os

import pytest

from exitspec.source_authoring_supervisor import _BoundedLiveSupervisor
from tests import test_source_authoring_terminal_closure as controls
from tests.helpers.source_authoring_admission import fake_transport, make_launch
from tests.test_source_authoring_terminal_closure import completed as _completed

completed = _completed
pytestmark = pytest.mark.skipif(
    os.environ.get("EXITSPEC_BROWSER_E2E") != "1", reason="mandatory unified terminal browser controls",
)


@pytest.fixture(autouse=True)
def admitted_composition(monkeypatch):
    server_type = controls.SourceNeutralPOCDemoServer
    children = fake_transport(monkeypatch)
    handle = make_launch(monkeypatch)

    def construct(*args, **kwargs):
        return server_type(*args, source_authoring_launch=handle, **kwargs)

    monkeypatch.setattr(controls, "SourceNeutralPOCDemoServer", construct)
    yield children
    handle.revoke()


@pytest.mark.parametrize("decision", controls.DECISIONS)
@pytest.mark.parametrize("point", ["before_claim", "pre_dispatch", "pre_commit"])
def test_real_terminal_owner_fences_live_claim_dispatch_and_publication(completed, admitted_composition, monkeypatch, decision, point):
    rig = completed
    runtime = rig.server.source_authoring_web
    cap, operation = controls.prepared(rig, authorize=True)
    prior = rig.server.assisted_authoring_service.list_receipts(rig.poc)
    handoffs = []
    original = _BoundedLiveSupervisor.handoff

    def handoff(worker):
        handoffs.append(True)
        return original(worker)

    def schedule(event):
        if event == point:
            controls.close_actual(rig, decision)

    monkeypatch.setattr(_BoundedLiveSupervisor, "handoff", handoff)
    monkeypatch.setattr(runtime.operations, "_schedule", schedule)
    if point == "before_claim":
        controls.close_actual(rig, decision)
    runtime.request(rig.poc, "run", {"operation_id": operation}, cap)
    if runtime._thread is not None:
        runtime._thread.join(timeout=5)
        assert not runtime._thread.is_alive()
    result = runtime.operations._records[operation].receipt
    assert result.state == "STALE" and result.authoring_receipt_id is None
    assert result.attempts == runtime.operations.ledger[0] == (0 if point == "before_claim" else 1)
    assert len(handoffs) == (1 if point == "pre_commit" else 0)
    assert len(admitted_composition) == (0 if point == "before_claim" else 1)
    assert all(child.poll() is not None for child in admitted_composition)
    assert rig.server.assisted_authoring_service.list_receipts(rig.poc) == prior
    assert runtime.operations._active is None
    assert rig.server.poc_closure_service._active_mutations == {}
