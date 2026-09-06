from __future__ import annotations

import json

import pytest

from exitspec.zoom_live_runtime import ZoomLiveRuntime
from tests.test_meeting_session_web_transport import (
    _create_draft,
    _request,
    _running_server,
)
from tests.test_zoom_live_runtime import settings


@pytest.mark.parametrize(
    "invalid_action",
    [[], {}, None, False, 0, 1.25, ["start"], {"private": "synthetic-marker"}],
)
def test_http_non_string_actions_are_refused_without_mutation_or_child_launch(
    tmp_path, invalid_action,
):
    launches = []

    def forbidden_child(*args):
        launches.append(args)
        raise AssertionError("Invalid action must never launch a child.")

    with _running_server(tmp_path) as server:
        server.zoom_live_runtime.close()
        runtime = server.zoom_live_runtime = ZoomLiveRuntime(
            drafts=server.draft_poc_service,
            intake=server.poc_source_intake,
            run_if_open=server.poc_closure_service.run_if_open,
            child_factory=forbidden_child,
            fake_network=True,
        )
        poc_id = _create_draft(server)
        before = runtime.pair(poc_id, settings())
        status, body = _request(
            server, "POST", f"/api/pocs/{poc_id}/zoom-live",
            payload={
                "action": invalid_action,
                "session_id": before["session_id"],
                "idempotency_key": "invalid_action_123",
            },
        )
        assert status == 409
        assert body == {
            "code": "ZOOM_LIVE_REFUSED",
            "error": "The live Zoom operation was refused.",
        }
        assert len(json.dumps(body)) < 128
        assert runtime.current(poc_id) == before
        assert not runtime._record.operations
        assert not launches
        assert server.poc_source_intake.list_receipts(poc_id) == ()


def test_http_cannot_bootstrap_or_change_operator_pairing_and_defends_host_origin(
    tmp_path,
):
    with _running_server(tmp_path) as server:
        poc_id = _create_draft(server)
        target = f"/api/pocs/{poc_id}/zoom-live"
        assert _request(server, "GET", target, origin=None)[1]["state"] == "UNPAIRED"
        body = {"action": "pair", "client_secret": "never-accepted"}
        assert _request(server, "POST", target, payload=body)[0] == 409
        server.zoom_live_runtime.pair(poc_id, settings())
        start = {
            "action": "start",
            "session_id": server.zoom_live_runtime.current(poc_id)["session_id"],
            "idempotency_key": "http_native_start",
            "consent_acknowledged": True,
        }
        assert (
            _request(server, "POST", target, payload=start, origin="https://evil.test")[
                0
            ]
            == 403
        )
        assert _request(server, "POST", target, payload=start, origin=None)[0] == 403
        assert (
            _request(
                server, "POST", target, payload=start, headers={"Host": "evil.test"}
            )[0]
            == 403
        )
        assert (
            _request(server, "GET", target, headers={"Host": "evil.test"}, origin=None)[
                0
            ]
            == 403
        )
        assert (
            _request(server, "POST", target + "?stream=other", payload=start)[0] == 400
        )
        assert _request(server, "POST", target, raw_body=b"x" * 4097)[0] == 400
        assert (
            _request(
                server, "POST", target, payload=start | {"normalized_sha256": "a" * 64}
            )[0]
            == 409
        )
        assert server.zoom_live_runtime.current(poc_id)["state"] == "PAIRED"
