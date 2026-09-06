from __future__ import annotations

from tests.test_meeting_session_web_transport import (
    _create_draft,
    _request,
    _running_server,
)
from tests.test_zoom_live_runtime import settings


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
