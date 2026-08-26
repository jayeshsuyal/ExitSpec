from __future__ import annotations

import json

from tests.test_meeting_session_web_transport import (
    _create_draft,
    _request,
    _running_server,
)


def test_http_zoom_handoff_reaches_draft_ready_without_a_second_poc(tmp_path):
    with _running_server(tmp_path) as server:
        poc_id = _create_draft(server)
        disclosure = _request(
            server,
            "GET",
            f"/api/pocs/{poc_id}/zoom-handoff-disclosure",
            content_type=None,
            origin=None,
        )
        current = _request(
            server,
            "GET",
            f"/api/pocs/{poc_id}/zoom-handoff",
            content_type=None,
            origin=None,
        )
        started = _request(
            server,
            "POST",
            f"/api/pocs/{poc_id}/zoom-handoff",
            payload={
                "action": "start",
                "consent_acknowledged": True,
                "idempotency_key": "http-zoom-start-123",
            },
        )
        stopped = _request(
            server,
            "POST",
            f"/api/pocs/{poc_id}/zoom-handoff",
            payload={
                "action": "stop",
                "idempotency_key": "http-zoom-stop-123",
            },
        )
        processed = _request(
            server,
            "POST",
            f"/api/pocs/{poc_id}/zoom-handoff",
            payload={
                "action": "process",
                "idempotency_key": "http-zoom-process-123",
            },
        )
        replay = _request(
            server,
            "POST",
            f"/api/pocs/{poc_id}/zoom-handoff",
            payload={
                "action": "process",
                "idempotency_key": "http-zoom-process-123",
            },
        )
        sources = _request(
            server,
            "GET",
            f"/api/pocs/{poc_id}/sources",
            content_type=None,
            origin=None,
        )
        drafts = server.draft_poc_service.snapshots()

    assert disclosure[0] == current[0] == 200
    assert disclosure[1]["provider_connected"] is False
    assert disclosure[1]["live_network"] is False
    assert current[1]["state"] == "IDLE"
    assert started[0] == stopped[0] == processed[0] == 201
    assert started[1]["handoff"]["state"] == "LISTENING"
    assert stopped[1]["handoff"]["state"] == "PROCESSING"
    assert processed[1]["handoff"]["state"] == "DRAFT_READY"
    assert replay[0] == 200
    assert replay[1]["idempotent_replay"] is True
    assert len(drafts) == 1
    assert len(sources[1]["sources"]) == 1
    serialized = json.dumps(
        [disclosure[1], current[1], started[1], stopped[1], processed[1]]
    )
    assert "guided-customer" not in serialized
    assert "Criterion: p95" not in serialized
    assert "Criterion: error rate" not in serialized


def test_http_zoom_handoff_preserves_same_origin_and_closed_poc_gates(tmp_path):
    with _running_server(tmp_path) as server:
        poc_id = _create_draft(server)
        target = f"/api/pocs/{poc_id}/zoom-handoff"
        wrong_origin = _request(
            server,
            "POST",
            target,
            payload={
                "action": "start",
                "consent_acknowledged": True,
                "idempotency_key": "http-zoom-origin-123",
            },
            origin="https://evil.test",
        )
        extra = _request(
            server,
            "POST",
            target,
            payload={
                "action": "start",
                "consent_acknowledged": True,
                "idempotency_key": "http-zoom-extra-123",
                "transcript": "private marker",
            },
        )
        server.draft_poc_service.archive(poc_id)
        closed = _request(
            server,
            "POST",
            target,
            payload={
                "action": "start",
                "consent_acknowledged": True,
                "idempotency_key": "http-zoom-closed-123",
            },
        )

    assert wrong_origin == (403, {"error": "Origin is not allowed."})
    assert extra == (400, {"error": "Zoom handoff request is invalid."})
    assert "private marker" not in json.dumps(extra)
    assert closed[0] == 404
    assert closed[1]["code"] == "ZOOM_GUIDED_HANDOFF_POC_UNAVAILABLE"
