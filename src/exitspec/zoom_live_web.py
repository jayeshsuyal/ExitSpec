"""Shared, bounded loopback HTTP boundary for the existing native Zoom owner."""

from __future__ import annotations

import json
import math
import re
from http import HTTPStatus
from time import monotonic
from urllib.parse import urlparse

from .source_authoring_web import _read_request_body
from .zoom_live_runtime import ZoomLiveError

_ROUTE = re.compile(r"/api/pocs/(poc_[a-z0-9][a-z0-9_-]{2,63})/zoom-live(-receipt)?")
_BODY_DEADLINE_SECONDS = 0.5
_SURPLUS_GRACE_SECONDS = 0.01


def _origin_allowed(handler, host):
    origins = handler.headers.get_all("Origin") or []
    if not origins:
        return handler.command != "POST"
    if len(origins) != 1 or origins[0] != origins[0].strip():
        return False
    origin = origins[0]
    try:
        parsed = urlparse(origin)
        port = parsed.port
    except ValueError:
        return False
    authority_start = origin.find("://") + 3
    return (
        parsed.scheme.lower() == "http"
        and parsed.hostname in {"127.0.0.1", "localhost"}
        and parsed.username is None and parsed.password is None
        and not any((parsed.path, parsed.params, parsed.query, parsed.fragment))
        and authority_start >= 3
        and origin[authority_start:] == parsed.netloc
        and parsed.netloc.lower() == host
        and (80 if port is None else port) == handler.server.server_port
    )


def _strict_body(handler, size):
    deadline = monotonic() + _BODY_DEADLINE_SECONDS
    previous_timeout = handler.connection.gettimeout()
    try:
        body = _read_request_body(handler, size, deadline)
        handler.connection.settimeout(min(_SURPLUS_GRACE_SECONDS, max(0.001, deadline - monotonic())))
        try:
            surplus = handler.rfile.read1(1)
        except TimeoutError:
            surplus = b""
        if surplus:
            raise ValueError()
    finally:
        handler.connection.settimeout(previous_timeout)

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError()
            result[key] = value
        return result

    payload = json.loads(
        body.decode("utf-8"), object_pairs_hook=pairs,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
    )
    pending = [(payload, 0)]
    nodes = 0
    while pending:
        value, depth = pending.pop()
        nodes += 1
        if nodes > 1024 or depth > 24:
            raise ValueError()
        if type(value) is float and not math.isfinite(value):
            raise ValueError()
        if type(value) is str:
            value.encode("utf-8")
        elif type(value) is dict:
            for key, child in value.items():
                key.encode("utf-8")
                pending.append((child, depth + 1))
        elif type(value) is list:
            pending.extend((child, depth + 1) for child in value)
    if type(payload) is not dict:
        raise ValueError()
    if monotonic() >= deadline:
        raise TimeoutError()
    return payload


def handle_zoom_live_http(handler):
    """Dispatch exact existing routes without exposing pairing or settings."""
    parsed = urlparse(handler.path)
    match = _ROUTE.fullmatch(parsed.path)
    if match is None:
        return False
    handler.close_connection = True

    def reply(status, payload):
        data = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        handler.connection.settimeout(2)
        try:
            handler.send_response(status)
            handler.send_header("Content-Type", "application/json; charset=utf-8")
            handler.send_header("Content-Length", str(len(data)))
            handler.send_header("Cache-Control", "no-store")
            handler.send_header("X-Content-Type-Options", "nosniff")
            handler.send_header("Connection", "close")
            handler.end_headers()
            if handler.command != "HEAD":
                handler.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass
        return True

    def refuse(status=HTTPStatus.BAD_REQUEST):
        return reply(status, {"code": "ZOOM_LIVE_REFUSED", "error": "The live Zoom operation was refused."})

    if parsed.query or parsed.params or parsed.fragment or handler.path != parsed.path:
        return refuse()
    hosts = handler.headers.get_all("Host") or []
    allowed_hosts = {f"127.0.0.1:{handler.server.server_port}", f"localhost:{handler.server.server_port}"}
    if len(hosts) != 1 or hosts[0] not in allowed_hosts or not _origin_allowed(handler, hosts[0]):
        return refuse(HTTPStatus.FORBIDDEN)
    try:
        if handler.command == "GET":
            result = (handler.server.zoom_live_runtime.receipt(match[1]) if match[2]
                      else handler.server.zoom_live_runtime.current(match[1]))
        elif handler.command == "POST":
            if match[2]:
                return refuse(HTTPStatus.METHOD_NOT_ALLOWED)
            lengths = handler.headers.get_all("Content-Length") or []
            if (not handler._has_json_media_type() or len(lengths) != 1
                or not re.fullmatch(r"[0-9]{1,4}", lengths[0])
                or not 0 < int(lengths[0]) <= 4096
                or handler.headers.get_all("Transfer-Encoding")
                or handler.headers.get_all("Content-Encoding")
                or handler.headers.get_all("Idempotency-Key")):
                return refuse()
            payload = _strict_body(handler, int(lengths[0]))
            result = handler.server.zoom_live_runtime.action(match[1], payload)
        else:
            return refuse(HTTPStatus.METHOD_NOT_ALLOWED)
    except TimeoutError:
        return refuse(HTTPStatus.REQUEST_TIMEOUT)
    except (ValueError, OverflowError, RecursionError, OSError, ZoomLiveError):
        return refuse(HTTPStatus.CONFLICT)
    return reply(HTTPStatus.OK, result)
