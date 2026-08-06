from __future__ import annotations

import base64
import hashlib
import json
import threading
from contextlib import contextmanager
from http.client import HTTPConnection
from pathlib import Path

import pytest

from exitspec.stt_boundary import STTSpeakerMappingState
from exitspec.stt_operation import (
    STTTransportResponse,
    STTTransportSegment,
)
from exitspec.web import MAX_REQUEST_BYTES, DemoSession, ExitSpecDemoServer


WEBM_SIGNATURE = b"\x1a\x45\xdf\xa3"
AUDIO = WEBM_SIGNATURE + b"loopback browser audio"


class LiveTransport:
    def __init__(self):
        self.calls = 0

    def transcribe(self, request):
        request.read_audio_bytes()
        self.calls += 1
        return STTTransportResponse(
            provider_request_id="fireworks-http-request-001",
            language="en",
            speaker_mapping=STTSpeakerMappingState.NOT_PROVIDED,
            segments=(
                STTTransportSegment(
                    start_ms=0,
                    end_ms=900,
                    text="P95 latency must stay below 625 ms.",
                ),
            ),
        )


@contextmanager
def _running_server(tmp_path: Path, *, stt_fireworks_transport=None):
    session = DemoSession.synthetic_support_agent(output_root=tmp_path / "runs")
    server = ExitSpecDemoServer(
        ("127.0.0.1", 0),
        session,
        stt_fireworks_transport=stt_fireworks_transport,
    )
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield server
    finally:
        server.shutdown()
        worker.join(timeout=5)
        server.server_close()
        assert not worker.is_alive()


def _request(
    server: ExitSpecDemoServer,
    method: str,
    target: str,
    *,
    payload=None,
    raw_body: bytes | None = None,
    content_type: str | None = "application/json",
    origin: str | None = "same",
    headers: dict[str, str] | None = None,
) -> tuple[int, dict]:
    body = (
        json.dumps(payload).encode("utf-8")
        if raw_body is None and payload is not None
        else raw_body
    )
    request_headers = dict(headers or {})
    if content_type is not None:
        request_headers["Content-Type"] = content_type
    if origin == "same":
        request_headers["Origin"] = "http://127.0.0.1:{0}".format(
            server.server_port
        )
    elif origin is not None:
        request_headers["Origin"] = origin

    connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    try:
        connection.request(
            method,
            target,
            body=body,
            headers=request_headers,
        )
        response = connection.getresponse()
        return response.status, json.loads(response.read().decode("utf-8"))
    finally:
        connection.close()


def _create_draft(server: ExitSpecDemoServer) -> str:
    status, payload = _request(
        server,
        "POST",
        "/api/pocs",
        payload={
            "display_name": "Browser microphone POC",
            "customer_label": "Northstar",
            "use_case": "Prove one consented synthetic recording handoff.",
            "owner": "field_engineer",
            "first_source_choice": "MEETING",
            "idempotency_key": "create-stt-transport",
        },
    )
    assert status == 201
    return payload["poc_id"]


def _consent(server: ExitSpecDemoServer, poc_id: str, *, live: bool = False):
    return _request(
        server,
        "POST",
        f"/api/pocs/{poc_id}/stt/consents",
        payload={
            "all_speakers_consented": True,
            "disclosure_id": (
                "stt_fireworks_disclosure_v1"
                if live
                else "stt_demo_disclosure_v1"
            ),
            "idempotency_key": "transport-consent-key",
            "recording_notice_acknowledged": True,
            **(
                {"provider_processing_acknowledged": True}
                if live
                else {"synthetic_demo_acknowledged": True}
            ),
        },
    )


def _capture_payload():
    return {
        "audio_base64": base64.b64encode(AUDIO).decode("ascii"),
        "audio_sha256": hashlib.sha256(AUDIO).hexdigest(),
        "byte_length": len(AUDIO),
        "duration_ms": 1_000,
        "idempotency_key": "transport-capture-key",
        "media_type": "audio/webm",
    }


def test_real_transport_completes_disclosure_consent_capture_and_source_list(
    tmp_path,
):
    with _running_server(tmp_path) as server:
        poc_id = _create_draft(server)
        disclosure = _request(
            server,
            "GET",
            f"/api/pocs/{poc_id}/stt/disclosure",
            content_type=None,
            origin=None,
        )
        consent = _consent(server, poc_id)
        capture_id = consent[1]["capture_id"]
        captured = _request(
            server,
            "POST",
            f"/api/pocs/{poc_id}/stt/captures/{capture_id}",
            payload=_capture_payload(),
        )
        recovered = _request(
            server,
            "GET",
            f"/api/pocs/{poc_id}/stt/captures/{capture_id}",
            content_type=None,
            origin=None,
        )
        listed = _request(
            server,
            "GET",
            f"/api/pocs/{poc_id}/sources",
            content_type=None,
            origin=None,
        )

    assert disclosure[0] == 200
    assert disclosure[1]["spoken_words_transcribed"] is False
    assert disclosure[1]["webm_signature_required"] is True
    assert consent[0] == captured[0] == 201
    assert captured[1]["status"] == "NEEDS_REVIEW"
    assert captured[1]["webm_signature_verified"] is True
    assert captured[1]["proposal_count"] == 2
    assert recovered == (
        200,
        {**captured[1], "idempotent_replay": True},
    )
    assert listed[0] == 200
    assert len(listed[1]["sources"]) == 1
    assert listed[1]["sources"][0]["source_receipt_id"] == (
        captured[1]["source_receipt_id"]
    )
    serialized = json.dumps((disclosure, consent, captured, listed))
    assert AUDIO.hex() not in serialized


def test_live_provider_transport_is_wired_through_http_to_review_queue(tmp_path):
    transport = LiveTransport()
    with _running_server(
        tmp_path,
        stt_fireworks_transport=transport,
    ) as server:
        poc_id = _create_draft(server)
        disclosure = _request(
            server,
            "GET",
            f"/api/pocs/{poc_id}/stt/disclosure",
            content_type=None,
            origin=None,
        )
        consent = _consent(server, poc_id, live=True)
        capture_id = consent[1]["capture_id"]
        captured = _request(
            server,
            "POST",
            f"/api/pocs/{poc_id}/stt/captures/{capture_id}",
            payload=_capture_payload(),
        )
        listed = _request(
            server,
            "GET",
            f"/api/pocs/{poc_id}/sources",
            content_type=None,
            origin=None,
        )

    assert disclosure[0] == 200
    assert disclosure[1]["mode"] == "FIREWORKS_PRERECORDED_TRANSCRIPTION"
    assert disclosure[1]["provider_transport_configured"] is True
    assert disclosure[1]["spoken_words_transcribed"] is True
    assert "fixed_output" not in disclosure[1]
    assert consent[0] == captured[0] == 201
    assert consent[1]["provider_processing_acknowledged"] is True
    assert captured[1]["status"] == "NEEDS_REVIEW"
    assert captured[1]["proposal_count"] == 1
    assert captured[1]["provider"] == "fireworks"
    assert captured[1]["raw_audio_retained"] is False
    assert listed[1]["sources"][0]["source_receipt_id"] == (
        captured[1]["source_receipt_id"]
    )
    assert transport.calls == 1
    serialized = json.dumps((disclosure, consent, captured, listed))
    assert AUDIO.hex() not in serialized
    assert "625 ms" not in serialized


@pytest.mark.parametrize(
    ("content_type", "origin", "expected"),
    (
        (
            "text/plain",
            "same",
            (415, {"error": "Content-Type must be application/json."}),
        ),
        (
            None,
            "same",
            (415, {"error": "Content-Type must be application/json."}),
        ),
        (
            "application/json",
            None,
            (403, {"error": "Origin is not allowed."}),
        ),
        (
            "application/json",
            "https://evil.test",
            (403, {"error": "Origin is not allowed."}),
        ),
    ),
)
def test_stt_writes_require_json_and_exact_same_origin(
    tmp_path,
    content_type,
    origin,
    expected,
):
    with _running_server(tmp_path) as server:
        poc_id = _create_draft(server)
        response = _request(
            server,
            "POST",
            f"/api/pocs/{poc_id}/stt/consents",
            payload={
                "all_speakers_consented": True,
                "disclosure_id": "stt_demo_disclosure_v1",
                "idempotency_key": "transport-gate-key",
                "recording_notice_acknowledged": True,
                "synthetic_demo_acknowledged": True,
            },
            content_type=content_type,
            origin=origin,
        )

    assert response == expected


def test_duplicate_json_header_idempotency_and_authority_fields_are_rejected(
    tmp_path,
):
    with _running_server(tmp_path) as server:
        poc_id = _create_draft(server)
        target = f"/api/pocs/{poc_id}/stt/consents"
        duplicate = _request(
            server,
            "POST",
            target,
            raw_body=(
                b'{"disclosure_id":"stt_demo_disclosure_v1",'
                b'"disclosure_id":"changed","raw":"private"}'
            ),
        )
        header = _request(
            server,
            "POST",
            target,
            payload={"invalid": True},
            headers={"Idempotency-Key": "header-key"},
        )
        authority = _request(
            server,
            "POST",
            target,
            payload={
                "all_speakers_consented": True,
                "approve": True,
                "disclosure_id": "stt_demo_disclosure_v1",
                "idempotency_key": "authority-key",
                "recording_notice_acknowledged": True,
                "synthetic_demo_acknowledged": True,
            },
        )

    expected = (400, {"error": "Recording request is invalid."})
    assert duplicate == header == authority == expected
    assert "private" not in json.dumps(duplicate)


def test_oversized_body_query_and_unsupported_method_fail_closed(tmp_path):
    with _running_server(tmp_path) as server:
        poc_id = _create_draft(server)
        target = f"/api/pocs/{poc_id}/stt/consents"
        oversized = _request(
            server,
            "POST",
            target,
            raw_body=b"{" + b"x" * MAX_REQUEST_BYTES + b"}",
        )
        query = _request(
            server,
            "POST",
            target + "?provider=real",
            payload={"invalid": True},
        )
        unsupported = _request(
            server,
            "DELETE",
            f"/api/pocs/{poc_id}/stt/disclosure",
            content_type=None,
            origin=None,
        )
        trailing = _request(
            server,
            "GET",
            f"/api/pocs/{poc_id}/stt/disclosure/",
            content_type=None,
            origin=None,
        )
        doubled = _request(
            server,
            "GET",
            f"/api/pocs/{poc_id}/stt//disclosure",
            content_type=None,
            origin=None,
        )

    assert oversized == (
        413,
        {"error": "Recording request is too large."},
    )
    assert query == (
        400,
        {"error": "Recording request is invalid."},
    )
    assert unsupported == (
        405,
        {"error": "Recording method is not allowed."},
    )
    assert trailing == doubled == (
        400,
        {"error": "Recording request is invalid."},
    )
