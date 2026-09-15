"""Both app compositions share the same bounded native Zoom boundary."""

from __future__ import annotations

import json
import socket
import threading
import time
from contextlib import contextmanager
from http.client import HTTPResponse

import pytest

from exitspec.poc_source_demo import SourceNeutralPOCDemoServer
from tests.test_meeting_session_web_transport import (
    _create_draft,
    _request,
    _running_server,
)


@contextmanager
def running_source():
    server = SourceNeutralPOCDemoServer(("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()
        assert not thread.is_alive()
        assert not server.zoom_live_runtime._watcher.is_alive()


@pytest.fixture(params=["main", "source-neutral"])
def composition(request, tmp_path):
    manager = _running_server(tmp_path) if request.param == "main" else running_source()
    with manager as server:
        yield server


def raw_request(server, *, method="POST", suffix="", body=b"{}", changes=None, extra=(), declared=None):
    host = f"127.0.0.1:{server.server_port}"
    headers = {"Host": host, "Origin": "http://" + host, "Content-Type": "application/json",
               "Content-Length": str(len(body) if declared is None else declared)}
    for key, value in (changes or {}).items():
        if value is None:
            headers.pop(key, None)
        else:
            headers[key] = value.format(host=host)
    target = f"/api/pocs/{server.test_poc}/zoom-live" + suffix
    wire = f"{method} {target} HTTP/1.1\r\n".encode()
    wire += b"".join(f"{key}: {value}\r\n".encode() for key, value in [*headers.items(), *extra])
    with socket.create_connection(("127.0.0.1", server.server_port), timeout=3) as connection:
        connection.sendall(wire + b"\r\n" + body)
        response = HTTPResponse(connection, method=method)
        response.begin()
        payload = response.read()
        return response.status, json.loads(payload) if payload else None


BAD_REQUESTS = [
    ({"changes": {"Host": "evil.test"}}, 403),
    ({"changes": {"Host": None}}, 403),
    ({"extra": [("Host", "evil.test")]}, 403),
    ({"changes": {"Origin": None}}, 403),
    ({"changes": {"Origin": "https://{host}"}}, 403),
    ({"changes": {"Origin": "http://evil.test"}}, 403),
    ({"changes": {"Origin": "http://{host}/"}}, 403),
    ({"extra": [("Origin", "http://evil.test")]}, 403),
    ({"suffix": "?extra=1"}, 400),
    ({"suffix": "#fragment"}, 400),
    ({"suffix": ";parameter"}, 400),
    ({"changes": {"Content-Type": "text/plain"}}, 400),
    ({"extra": [("Content-Type", "application/json")]}, 400),
    ({"changes": {"Content-Length": None}}, 400),
    ({"changes": {"Content-Length": "-1"}}, 400),
    ({"changes": {"Content-Length": "0"}}, 400),
    ({"changes": {"Content-Length": "4097"}}, 400),
    ({"extra": [("Content-Length", "2")]}, 400),
    ({"extra": [("Transfer-Encoding", "chunked")]}, 400),
    ({"extra": [("Content-Encoding", "gzip")]}, 400),
    ({"extra": [("Idempotency-Key", "no-authority")]}, 400),
    ({"body": b'{"action":"start","action":"stop"}'}, 409),
    ({"body": b'{"nested":{"key":1,"key":2}}'}, 409),
    ({"body": b'{"value":NaN}'}, 409),
    ({"body": b'{"value":Infinity}'}, 409),
    ({"body": b'{"value":1e999}'}, 409),
    ({"body": b'{"value":"\\ud800"}'}, 409),
    ({"body": b'{"value":"\xff"}'}, 409),
    ({"body": b"[]"}, 409),
    ({"body": b"null"}, 409),
    ({"body": b'{"value":' + b"[" * 30 + b"0" + b"]" * 30 + b"}"}, 409),
    ({"body": b"{}X", "declared": 2}, 409),
    ({"body": b"{", "declared": 2}, 408),
    ({"body": b"", "declared": 2}, 408),
    ({"suffix": "-receipt"}, 405),
    *[({"method": method}, 405) for method in ["PUT", "PATCH", "DELETE", "OPTIONS", "HEAD", "TRACE"]],
]


@pytest.mark.parametrize("kwargs,status", BAD_REQUESTS)
def test_shared_zoom_refuses_before_runtime_effect(composition, monkeypatch, kwargs, status):
    server = composition
    server.test_poc = _create_draft(server)
    runtime = server.zoom_live_runtime
    calls = []
    monkeypatch.setattr(runtime, "action", lambda *args: calls.append(args) or {})
    before = runtime.current(server.test_poc)
    started = time.monotonic()
    observed, body = raw_request(server, **kwargs)
    assert observed == status
    assert time.monotonic() - started < 2
    if kwargs.get("method") != "HEAD":
        assert body == {"code": "ZOOM_LIVE_REFUSED", "error": "The live Zoom operation was refused."}
    assert calls == []
    assert runtime.current(server.test_poc) == before
    assert server.poc_source_intake.list_receipts(server.test_poc) == ()


def test_neighboring_exact_requests_work_in_both_compositions(composition, monkeypatch):
    server = composition
    server.test_poc = _create_draft(server)
    target = f"/api/pocs/{server.test_poc}/zoom-live"
    assert _request(server, "GET", target, origin=None)[1]["state"] == "UNPAIRED"
    calls = []
    monkeypatch.setattr(server.zoom_live_runtime, "action", lambda *args: calls.append(args) or {"ok": True})
    assert raw_request(server, body=b'{"action":"stop"}')[0] == 200
    assert len(calls) == 1
    assert raw_request(server, body=b'{"action":"stop"}', changes={"Content-Type": "application/json; charset=utf-8"})[0] == 200
    assert len(calls) == 2


def test_trickled_body_cannot_renew_the_total_deadline(composition, monkeypatch):
    server = composition
    poc = _create_draft(server)
    calls = []
    monkeypatch.setattr(server.zoom_live_runtime, "action", lambda *args: calls.append(args) or {})
    host = f"127.0.0.1:{server.server_port}"
    headers = (f"POST /api/pocs/{poc}/zoom-live HTTP/1.1\r\nHost: {host}\r\n"
               f"Origin: http://{host}\r\nContent-Type: application/json\r\nContent-Length: 64\r\n\r\n")
    stopped = threading.Event()
    with socket.create_connection(("127.0.0.1", server.server_port), timeout=3) as connection:
        connection.sendall(headers.encode())

        def trickle():
            try:
                while not stopped.wait(0.04):
                    connection.sendall(b" ")
            except OSError:
                pass

        sender = threading.Thread(target=trickle, daemon=True)
        started = time.monotonic()
        sender.start()
        try:
            response = HTTPResponse(connection)
            response.begin()
            assert response.status == 408
            assert json.loads(response.read())["code"] == "ZOOM_LIVE_REFUSED"
            assert time.monotonic() - started < 1.5
        finally:
            stopped.set()
            sender.join(timeout=1)
        assert not sender.is_alive() and calls == []
        assert server.poc_source_intake.list_receipts(poc) == ()


def test_source_neutral_uses_actual_generic_terminal_owner():
    with running_source() as server:
        assert server.poc_closure_service is server.generic_evidence_service.closure_service
        assert server.zoom_live_runtime._run_if_open.__self__ is server.poc_closure_service
        assert server.source_authoring_web._closure is server.poc_closure_service


def test_constructor_failure_closes_created_zoom_and_authoring(monkeypatch, tmp_path):
    import exitspec.poc_source_demo as module
    zooms, authors = [], []
    zoom_type, author_type = module.ZoomLiveRuntime, module.SourceAuthoringWebRuntime

    def zoom(**kwargs):
        value = zoom_type(**kwargs)
        zooms.append(value)
        return value

    def author(**kwargs):
        value = author_type(**kwargs)
        authors.append(value)
        return value

    monkeypatch.setattr(module, "ZoomLiveRuntime", zoom)
    monkeypatch.setattr(module, "SourceAuthoringWebRuntime", author)
    with pytest.raises(RuntimeError):
        module.SourceNeutralPOCDemoServer(("127.0.0.1", 0), static_root=tmp_path / "missing")
    assert len(zooms) == len(authors) == 1
    assert zooms[0]._closed.is_set() and not zooms[0]._watcher.is_alive()
    assert authors[0]._closed and authors[0].operations._closed
