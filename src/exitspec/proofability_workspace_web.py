"""Raw HTTP adapter for the bounded proofability workspace."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from http import HTTPStatus
from typing import Any, Final, Literal
from urllib.parse import urlsplit

from .canonical import canonical_json_bytes
from .proofability_workspace import (
    ProofabilityWorkspaceError,
    ProofabilityWorkspaceErrorCode,
)

_API_VALID_RE: Final = re.compile(
    r"^/api/pocs/(poc_[a-z0-9][a-z0-9_-]{2,63})/qualification/proofability$"
)
_PAGE_VALID_RE: Final = re.compile(
    r"^/app/pocs/(poc_[a-z0-9][a-z0-9_-]{2,63})/qualification/proofability$"
)
_API_SHAPE_RE: Final = re.compile(
    r"^/api/pocs/([^/]+)/qualification/proofability$"
)
_PAGE_SHAPE_RE: Final = re.compile(
    r"^/app/pocs/([^/]+)/qualification/proofability$"
)
_API_PARAMS_RE: Final = re.compile(
    r"^/api/pocs/(poc_[a-z0-9][a-z0-9_-]{2,63})/qualification/"
    r"proofability;[^/?#]*(?:[?#].*)?$"
)
_PAGE_PARAMS_RE: Final = re.compile(
    r"^/app/pocs/(poc_[a-z0-9][a-z0-9_-]{2,63})/qualification/"
    r"proofability;[^/?#]*(?:[?#].*)?$"
)
_CONTENT_LENGTH_RE: Final = re.compile(r"^[ \t]*(0|[1-9][0-9]*)[ \t]*$")
_MAX_BODY_BYTES: Final = 131_072
_MAX_JSON_DEPTH: Final = 32
_MAX_JSON_NODES: Final = 4_096

_STATUS_BY_CODE: Final = {
    ProofabilityWorkspaceErrorCode.INVALID_REQUEST: HTTPStatus.BAD_REQUEST,
    ProofabilityWorkspaceErrorCode.ORIGIN_FORBIDDEN: HTTPStatus.FORBIDDEN,
    ProofabilityWorkspaceErrorCode.POC_NOT_FOUND: HTTPStatus.NOT_FOUND,
    ProofabilityWorkspaceErrorCode.METHOD_NOT_ALLOWED: HTTPStatus.METHOD_NOT_ALLOWED,
    ProofabilityWorkspaceErrorCode.IDEMPOTENCY_CONFLICT: HTTPStatus.CONFLICT,
    ProofabilityWorkspaceErrorCode.PAYLOAD_TOO_LARGE: HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
    ProofabilityWorkspaceErrorCode.UNSUPPORTED_MEDIA_TYPE: HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
    ProofabilityWorkspaceErrorCode.PROFILE_UNSUPPORTED: HTTPStatus.UNPROCESSABLE_ENTITY,
    ProofabilityWorkspaceErrorCode.CAPACITY_EXHAUSTED: HTTPStatus.SERVICE_UNAVAILABLE,
    ProofabilityWorkspaceErrorCode.WORKSPACE_UNAVAILABLE: HTTPStatus.SERVICE_UNAVAILABLE,
}


@dataclass(frozen=True, slots=True)
class _Target:
    kind: Literal["api", "page"]
    valid: bool
    poc_id: str | None


@dataclass(frozen=True, slots=True)
class _Framing:
    body_length: int


class _JsonTooLarge(ValueError):
    pass


def _candidate_kind(path: str) -> tuple[Literal["api", "page"], str | None] | None:
    for kind, valid, shape, params in (
        ("api", _API_VALID_RE, _API_SHAPE_RE, _API_PARAMS_RE),
        ("page", _PAGE_VALID_RE, _PAGE_SHAPE_RE, _PAGE_PARAMS_RE),
    ):
        match = valid.fullmatch(path)
        if match is not None:
            return kind, match.group(1)
        params_match = params.fullmatch(path)
        if params_match is not None:
            return kind, params_match.group(1)
        shape_match = shape.fullmatch(path)
        if shape_match is not None:
            return kind, None
    return None


def _classify_target(raw_target: str) -> _Target | None:
    if type(raw_target) is not str:
        return None
    origin_form = raw_target.startswith("/") and not raw_target.startswith("//")
    candidate = raw_target
    if origin_form:
        suffix_positions = [
            position
            for marker in ("?", "#")
            if (position := raw_target.find(marker)) >= 0
        ]
        if suffix_positions:
            candidate = raw_target[: min(suffix_positions)]
    elif raw_target.startswith("//") or re.match(
        r"^[A-Za-z][A-Za-z0-9+.-]*://", raw_target
    ):
        try:
            candidate = urlsplit(raw_target).path
        except ValueError:
            return None
    else:
        return None
    classified = _candidate_kind(candidate)
    if classified is None:
        classified = _candidate_kind(raw_target)
    if classified is None:
        return None
    kind, _candidate_poc_id = classified
    valid_pattern = _API_VALID_RE if kind == "api" else _PAGE_VALID_RE
    exact = valid_pattern.fullmatch(raw_target) if origin_form else None
    return _Target(
        kind=kind,
        valid=exact is not None,
        poc_id=None if exact is None else exact.group(1),
    )


def _raw_target(handler: Any) -> str:
    raw_line = getattr(handler, "raw_requestline", b"")
    if type(raw_line) is bytes:
        line = raw_line.rstrip(b"\r\n")
        match = re.fullmatch(rb"[^ \t]+[ \t]+([^ \t]+)[ \t]+HTTP/[0-9.]+", line)
        if match is not None:
            try:
                target = match.group(1).decode("ascii", errors="strict")
            except UnicodeDecodeError:
                target = ""
            if target:
                return target
    path = getattr(handler, "path", "")
    return path if type(path) is str else ""


def _header_values(handler: Any, name: str) -> list[str]:
    values = handler.headers.get_all(name) or []
    return list(values)


def _send_json(
    handler: Any,
    status: HTTPStatus,
    payload: dict[str, Any],
    *,
    allow: str | None = None,
    close: bool = False,
) -> None:
    data = canonical_json_bytes(payload)
    if close:
        handler.close_connection = True
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Cache-Control", "no-store")
    if allow is not None:
        handler.send_header("Allow", allow)
    if close:
        handler.send_header("Connection", "close")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    if handler.command != "HEAD":
        handler.wfile.write(data)


def _send_error(
    handler: Any,
    code: ProofabilityWorkspaceErrorCode,
    *,
    allow: str | None = None,
    close: bool = False,
) -> None:
    _send_json(
        handler,
        _STATUS_BY_CODE[code],
        {"error_code": code.value},
        allow=allow,
        close=close,
    )


def _send_success(handler: Any, status: HTTPStatus, payload: dict[str, Any]) -> None:
    _send_json(handler, status, payload)


def _framing(handler: Any, target: _Target) -> _Framing | None:
    method = handler.command
    consumes_body = target.kind == "api" and method == "POST"
    transfer_encoding = _header_values(handler, "Transfer-Encoding")
    header_idempotency = _header_values(handler, "Idempotency-Key")
    lengths = _header_values(handler, "Content-Length")
    if transfer_encoding or header_idempotency:
        _send_error(
            handler,
            ProofabilityWorkspaceErrorCode.INVALID_REQUEST,
            close=True,
        )
        return None
    if len(lengths) > 1 or (consumes_body and len(lengths) != 1):
        _send_error(
            handler,
            ProofabilityWorkspaceErrorCode.INVALID_REQUEST,
            close=True,
        )
        return None
    if not lengths:
        return _Framing(body_length=0)
    raw_length = lengths[0]
    if type(raw_length) is not str or "\r" in raw_length or "\n" in raw_length:
        _send_error(
            handler,
            ProofabilityWorkspaceErrorCode.INVALID_REQUEST,
            close=True,
        )
        return None
    match = _CONTENT_LENGTH_RE.fullmatch(raw_length)
    if match is None:
        _send_error(
            handler,
            ProofabilityWorkspaceErrorCode.INVALID_REQUEST,
            close=True,
        )
        return None
    token = match.group(1)
    if not consumes_body and token != "0":
        _send_error(
            handler,
            ProofabilityWorkspaceErrorCode.INVALID_REQUEST,
            close=True,
        )
        return None
    if consumes_body and (
        len(token) > len(str(_MAX_BODY_BYTES))
        or (
            len(token) == len(str(_MAX_BODY_BYTES))
            and token > str(_MAX_BODY_BYTES)
        )
    ):
        _send_error(
            handler,
            ProofabilityWorkspaceErrorCode.PAYLOAD_TOO_LARGE,
            close=True,
        )
        return None
    length = int(token)
    return _Framing(body_length=length)


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate")
        result[key] = value
    return result


def _walk_json(value: Any, depth: int, counter: list[int]) -> None:
    counter[0] += 1
    if counter[0] > _MAX_JSON_NODES or depth > _MAX_JSON_DEPTH:
        raise _JsonTooLarge
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("non-finite")
    if type(value) is dict:
        for key, child in value.items():
            if type(key) is not str:
                raise ValueError("key")
            _walk_json(child, depth + 1, counter)
    elif type(value) is list:
        for child in value:
            _walk_json(child, depth + 1, counter)
    elif value is not None and type(value) not in {str, int, float, bool}:
        raise ValueError("value")


def _read_json(handler: Any, length: int) -> dict[str, Any] | None:
    body = handler.rfile.read(length)
    if len(body) != length:
        _send_error(
            handler,
            ProofabilityWorkspaceErrorCode.INVALID_REQUEST,
            close=True,
        )
        return None
    try:
        payload = json.loads(
            body.decode("utf-8", errors="strict"),
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
        )
        _walk_json(payload, 0, [0])
    except (_JsonTooLarge, RecursionError):
        _send_error(handler, ProofabilityWorkspaceErrorCode.PAYLOAD_TOO_LARGE)
        return None
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        _send_error(handler, ProofabilityWorkspaceErrorCode.INVALID_REQUEST)
        return None
    if type(payload) is not dict:
        _send_error(handler, ProofabilityWorkspaceErrorCode.INVALID_REQUEST)
        return None
    return payload


def _serve_page(handler: Any) -> None:
    target = handler.server.static_root / "proofability_workspace.html"
    try:
        if target.is_symlink() or not target.is_file():
            raise OSError
        data = target.read_bytes()
    except OSError:
        _send_error(handler, ProofabilityWorkspaceErrorCode.WORKSPACE_UNAVAILABLE)
        return
    handler.send_response(HTTPStatus.OK)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def handle_proofability_workspace_http(handler: Any) -> bool:
    """Handle one exact PR6 namespace request, or preserve legacy routing."""

    target = _classify_target(_raw_target(handler))
    if target is None:
        return False
    framing = _framing(handler, target)
    if framing is None:
        return True
    close_before_body = framing.body_length > 0
    if not target.valid or target.poc_id is None:
        _send_error(
            handler,
            ProofabilityWorkspaceErrorCode.INVALID_REQUEST,
            close=close_before_body,
        )
        return True
    if target.kind == "api" and handler.command not in {"GET", "POST"}:
        _send_error(
            handler,
            ProofabilityWorkspaceErrorCode.METHOD_NOT_ALLOWED,
            allow="GET, POST",
        )
        return True
    if target.kind == "page" and handler.command != "GET":
        _send_error(
            handler,
            ProofabilityWorkspaceErrorCode.METHOD_NOT_ALLOWED,
            allow="GET",
        )
        return True
    workspace = handler.server.proofability_workspace
    if target.kind == "page":
        try:
            workspace.require_active_poc(target.poc_id)
        except ProofabilityWorkspaceError as error:
            _send_error(handler, error.code)
            return True
        _serve_page(handler)
        return True
    if handler.command == "GET":
        try:
            payload = workspace.get(poc_id=target.poc_id)
        except ProofabilityWorkspaceError as error:
            _send_error(handler, error.code)
            return True
        _send_success(handler, HTTPStatus.OK, payload)
        return True
    if not handler._has_json_media_type():
        _send_error(
            handler,
            ProofabilityWorkspaceErrorCode.UNSUPPORTED_MEDIA_TYPE,
            close=close_before_body,
        )
        return True
    if not handler._has_exact_loopback_origin():
        _send_error(
            handler,
            ProofabilityWorkspaceErrorCode.ORIGIN_FORBIDDEN,
            close=close_before_body,
        )
        return True
    payload = _read_json(handler, framing.body_length)
    if payload is None:
        return True
    if set(payload) != {"profile_id", "profile_version", "idempotency_key"}:
        _send_error(handler, ProofabilityWorkspaceErrorCode.INVALID_REQUEST)
        return True
    try:
        response = workspace.create(
            poc_id=target.poc_id,
            profile_id=payload["profile_id"],
            profile_version=payload["profile_version"],
            idempotency_key=payload["idempotency_key"],
        )
    except ProofabilityWorkspaceError as error:
        _send_error(handler, error.code)
        return True
    status = (
        HTTPStatus.OK
        if response["idempotent_replay"] is True
        else HTTPStatus.CREATED
    )
    _send_success(handler, status, response)
    return True


__all__ = ["handle_proofability_workspace_http"]
