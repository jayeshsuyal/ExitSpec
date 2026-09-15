"""The sealed installation drives existing SourceNeutral product APIs only."""
import threading
from types import SimpleNamespace

import pytest

from exitspec import source_authoring_launch as launch
from exitspec.poc_creation import DraftPOCCreateRequest, FirstSourceChoice
from exitspec.poc_source_demo import SourceNeutralPOCDemoServer
from exitspec.poc_source_intake import POCSourceInput
from exitspec.poc_sources import SourceKind
from tests.helpers.source_authoring_admission import fake_transport, make_launch
from tests.test_source_authoring_web import POC, authorize, call, prepare, wait


@pytest.fixture
def rig(monkeypatch):
    handle = make_launch(monkeypatch)
    children = fake_transport(monkeypatch)
    server = SourceNeutralPOCDemoServer(("127.0.0.1", 0), source_authoring_launch=handle)
    server.draft_poc_service.create(DraftPOCCreateRequest(
        poc_id=POC, display_name="Offline unified source test", customer_label="Synthetic",
        use_case="Source-bound draft review", owner="local_operator", first_source_choice=FirstSourceChoice.DOCUMENT,
    ), idempotency_key="create")
    receipt = server.poc_source_intake.capture_source(
        poc_id=POC, source=POCSourceInput(source_kind=SourceKind.DOCUMENT, content="The error rate must remain below 1%."),
        idempotency_key="capture",
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield SimpleNamespace(server=server, runtime=server.source_authoring_web, receipt=receipt,
                              prefix=f"/api/pocs/{POC}/source-authoring/", children=children, handle=handle)
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def bootstrap(rig):
    status, body, _ = call(rig, "bootstrap")
    assert status == 200 and body["mode"] == "OFFLINE_FAKE_FIREWORKS"
    assert body["live_enabled"] is False and body["live_missing"] == []
    return body["capability"]


def test_one_server_real_api_authoring_has_exact_owners_and_explicit_consent(rig, monkeypatch):
    server = rig.server
    assert server.poc_closure_service is server.generic_evidence_service.closure_service
    assert server.zoom_live_runtime._run_if_open.__self__ is server.poc_closure_service
    assert rig.runtime._owners._run_if_open.__self__ is server.poc_closure_service
    cap = bootstrap(rig)
    prepared = prepare(rig, cap)
    assert "Offline fake transport" in prepared["disclosure"]["custody"]
    operation = prepared["operation_id"]
    status, _, _ = call(rig, "run", {"operation_id": operation}, capability=cap)
    assert status == 409 and rig.children == []
    authorize(rig, cap, operation)
    assert rig.children == []
    status, _, _ = call(rig, "run", {"operation_id": operation}, capability=cap)
    assert status == 200
    result = wait(rig, cap, operation)
    assert result["state"] == "SUCCEEDED" and result["mode"] == "OFFLINE_FAKE_FIREWORKS"
    assert len(rig.children) == 1 and rig.runtime.operations.ledger[0] == 1
    assert all(row.decision is None for row in server.proposal_review_service.list_proposals(POC))


@pytest.mark.parametrize("bad", ["origin", "host", "capability", "missing_ack", "live_flag", "credential", "wrong_source"])
def test_web_refusals_have_no_claim_or_child(rig, bad):
    cap = bootstrap(rig)
    if bad in {"origin", "host", "capability"}:
        kwargs = {"origin": "http://external.invalid"} if bad == "origin" else (
            {"host": "external.invalid"} if bad == "host" else {})
        status, _, _ = call(rig, "sources", capability="0" * 64 if bad == "capability" else cap, **kwargs)
    elif bad == "wrong_source":
        status, _, _ = call(rig, "prepare", {"source_receipt_id": "srcpt_" + "0" * 32}, capability=cap)
    else:
        prepared = prepare(rig, cap)
        payload = {"operation_id": prepared["operation_id"], "business_text": True,
                   "acknowledged": bad != "missing_ack", "idempotency_key": "ack"}
        if bad != "missing_ack":
            payload[bad] = "PRIVATE-MARKER"
        status, _, _ = call(rig, "authorize", payload, capability=cap)
    assert status >= 400 and rig.children == [] and rig.runtime.operations.ledger[0] == 0
    assert not rig.server.assisted_authoring_service._results_by_request


def test_revoked_launch_refuses_existing_capability_and_clears_secret(rig):
    cap = bootstrap(rig)
    prepared = prepare(rig, cap)
    authorize(rig, cap, prepared["operation_id"])
    rig.handle.revoke()
    status, _, _ = call(rig, "run", {"operation_id": prepared["operation_id"]}, capability=cap)
    assert status >= 400 and rig.children == []
    assert launch._LAUNCHES[rig.handle].credential == b""


@pytest.mark.parametrize("failure", ["static", "socket", "zoom"])
def test_constructor_failure_revokes_consumed_installation(monkeypatch, failure, tmp_path):
    from exitspec import poc_source_demo as demo
    handle = make_launch(monkeypatch)

    def fail(*args, **kwargs):
        raise RuntimeError("fixed test failure")

    kwargs = {}
    if failure == "static":
        kwargs["static_root"] = tmp_path / "absent"
    elif failure == "socket":
        monkeypatch.setattr(demo.ThreadingHTTPServer, "server_bind", fail)
    else:
        monkeypatch.setattr(demo, "ZoomLiveRuntime", fail)
    with pytest.raises(RuntimeError):
        SourceNeutralPOCDemoServer(("127.0.0.1", 0), source_authoring_launch=handle, **kwargs)
    assert launch._LAUNCHES[handle].state == "REVOKED"
    assert launch._LAUNCHES[handle].credential == b""
    with pytest.raises(launch.SourceAuthoringLaunchError):
        SourceNeutralPOCDemoServer(("127.0.0.1", 0), source_authoring_launch=handle)
