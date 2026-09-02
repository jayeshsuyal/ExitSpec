"""Raw HTTP and source-neutral host tests for the PR6 workspace."""

from __future__ import annotations

import http.client
import json
import socket
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

import pytest

from exitspec.canonical import canonical_json_bytes
from exitspec.poc_creation import DraftPOCCreateRequest
from exitspec.poc_source_demo import SourceNeutralPOCDemoServer
from exitspec.proofability_workspace_fixture import PROFILE_ID, PROFILE_VERSION


@dataclass(frozen=True)
class RawResponse:
    status: int
    headers: dict[str, str]
    body: bytes


@contextmanager
def _running_server():
    server = SourceNeutralPOCDemoServer(("127.0.0.1", 0))
    draft = server.draft_poc_service.create(
        DraftPOCCreateRequest(
            display_name="Proofability workspace",
            customer_label="Synthetic label",
            use_case="Exercise the local planning projection.",
            owner="owner",
            first_source_choice="DOCUMENT",
            poc_id="poc_alpha",
        ),
        idempotency_key="draft-alpha",
    ).draft
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield server, draft.poc_id
    finally:
        server.shutdown()
        worker.join(timeout=5)
        server.server_close()


def _request(
    server: SourceNeutralPOCDemoServer,
    method: str,
    target: str,
    *,
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], bytes]:
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    connection.request(method, target, body=body, headers=headers or {})
    response = connection.getresponse()
    content = response.read()
    response_headers = {key.lower(): value for key, value in response.getheaders()}
    status = response.status
    connection.close()
    return status, response_headers, content


def _post(
    server: SourceNeutralPOCDemoServer,
    poc_id: str,
    key: str,
    *,
    profile_id: Any = PROFILE_ID,
    profile_version: Any = PROFILE_VERSION,
) -> tuple[int, dict[str, str], bytes]:
    body = canonical_json_bytes(
        {
            "profile_id": profile_id,
            "profile_version": profile_version,
            "idempotency_key": key,
        }
    )
    return _request(
        server,
        "POST",
        f"/api/pocs/{poc_id}/qualification/proofability",
        body=body,
        headers={
            "Content-Type": "application/json",
            "Origin": f"http://127.0.0.1:{server.server_port}",
        },
    )


def _raw_exchange(port: int, request: bytes) -> RawResponse:
    with socket.create_connection(("127.0.0.1", port), timeout=5) as client:
        client.sendall(request)
        client.shutdown(socket.SHUT_WR)
        chunks: list[bytes] = []
        while True:
            chunk = client.recv(65_536)
            if not chunk:
                break
            chunks.append(chunk)
    raw = b"".join(chunks)
    head, separator, body = raw.partition(b"\r\n\r\n")
    assert separator == b"\r\n\r\n", raw[:500]
    lines = head.split(b"\r\n")
    status = int(lines[0].split(b" ", 2)[1])
    headers: dict[str, str] = {}
    for line in lines[1:]:
        key, value = line.split(b":", 1)
        headers[key.decode("ascii").lower()] = value.strip().decode("latin-1")
    return RawResponse(status=status, headers=headers, body=body)


def _raw_request(
    server: SourceNeutralPOCDemoServer,
    method: str,
    target: str,
    headers: list[tuple[str, str]] | None = None,
    body: bytes = b"",
    *,
    host: str | None = None,
) -> RawResponse:
    lines = [
        f"{method} {target} HTTP/1.1",
        f"Host: {host or f'127.0.0.1:{server.server_port}'}",
        "Connection: close",
    ]
    lines.extend(f"{key}: {value}" for key, value in (headers or []))
    return _raw_exchange(
        server.server_port,
        ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1") + body,
    )


def _json(response_body: bytes) -> dict[str, Any]:
    return json.loads(response_body.decode("utf-8"))


def _graph_keys_and_strings(value: Any) -> tuple[set[str], set[str]]:
    keys: set[str] = set()
    strings: set[str] = set()
    if type(value) is dict:
        for key, child in value.items():
            keys.add(key)
            nested_keys, nested_strings = _graph_keys_and_strings(child)
            keys.update(nested_keys)
            strings.update(nested_strings)
    elif type(value) is list:
        for child in value:
            nested_keys, nested_strings = _graph_keys_and_strings(child)
            keys.update(nested_keys)
            strings.update(nested_strings)
    elif type(value) is str:
        strings.add(value)
    return keys, strings


def _assert_code(response: RawResponse, status: int, code: str) -> None:
    assert response.status == status
    assert response.body == canonical_json_bytes({"error_code": code})
    assert response.headers["content-type"] == "application/json; charset=utf-8"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["content-length"] == str(len(response.body))


def test_source_neutral_api_page_assets_and_legacy_routes_remain_compatible():
    with _running_server() as (server, poc_id):
        api = f"/api/pocs/{poc_id}/qualification/proofability"
        status, headers, content = _request(server, "GET", api)
        assert status == 200
        assert headers["content-type"] == "application/json; charset=utf-8"
        assert headers["cache-control"] == "no-store"
        assert headers["content-length"] == str(len(content))
        empty = _json(content)
        assert empty["report"] is None
        assert empty["needs_replan"] is False
        assert set(empty) == {
            "schema_version",
            "poc_id",
            "report",
            "needs_replan",
            "reported_context_digest",
            "resolved_context_digest",
            "profile_request",
            "context_source",
            "storage",
            "authority",
        }

        status, _, content = _post(server, poc_id, "web-key")
        assert status == 201
        fresh = _json(content)
        assert fresh["idempotent_replay"] is False
        assert len(canonical_json_bytes(fresh["report"])) == 2_602
        projected_keys, projected_strings = _graph_keys_and_strings(fresh)
        assert projected_keys.isdisjoint(
            {
                "customer",
                "provider",
                "fixture_path",
                "FROZEN_AT",
                "expected_subject_digest",
                "expected_scope_digest",
                "expected_canonical_report_byte_count",
            }
        )
        assert projected_strings.isdisjoint(
            {
                "PASS",
                "FAIL",
                "NOT_PROVEN",
                "CURRENT",
                "STALE",
                "EXPIRED",
                "INVALID",
            }
        )
        status, _, content = _post(server, poc_id, "web-key")
        assert status == 200
        assert _json(content)["idempotent_replay"] is True
        status, _, content = _request(server, "GET", api)
        assert status == 200
        assert _json(content)["report"] == fresh["report"]

        page = f"/app/pocs/{poc_id}/qualification/proofability"
        status, headers, content = _request(server, "GET", page)
        assert status == 200
        assert headers["content-type"] == "text/html; charset=utf-8"
        assert b"package synthetic fixture" in content
        for asset, media in (
            ("/proofability_workspace.css", "text/css; charset=utf-8"),
            ("/proofability_workspace.js", "text/javascript; charset=utf-8"),
        ):
            status, headers, asset_content = _request(server, "GET", asset)
            assert status == 200
            assert headers["content-type"] == media
            assert asset_content

        status, headers, content = _request(
            server, "GET", "/proofability_workspace.html"
        )
        assert status == 404
        assert headers["content-type"] == "application/json; charset=utf-8"
        assert content == b'{"error":"Page not found."}'
        assert len(content) == 27

        assert _request(server, "GET", "/api/state")[0] == 200
        assert _request(server, "GET", "/app")[0] == 200
        outside = _raw_request(server, "BREW", "/legacy-unrelated")
        assert outside.status == 501
        assert b"Unsupported method" in outside.body


@pytest.mark.parametrize(
    "target",
    [
        "/api/pocs/poc_alpha/qualification/proofability?",
        "/api/pocs/poc_alpha/qualification/proofability?x=1",
        "/api/pocs/poc_alpha/qualification/proofability#",
        "/api/pocs/poc_alpha/qualification/proofability#fragment",
        "/api/pocs/poc_alpha/qualification/proofability;",
        "/api/pocs/poc_alpha/qualification/proofability;params",
        "/api/pocs/poc_alpha/qualification/proofability;params?x=1",
        "/api/pocs/poc%5falpha/qualification/proofability",
        "/api/pocs/POC_alpha/qualification/proofability",
        "/app/pocs/poc_alpha/qualification/proofability?",
        "/app/pocs/poc_alpha/qualification/proofability#fragment",
        "/app/pocs/poc_alpha/qualification/proofability;",
        "/app/pocs/poc_alpha/qualification/proofability;params#x",
    ],
)
def test_raw_target_aliases_are_classification_only_invalid_requests(target):
    with _running_server() as (server, _):
        response = _raw_request(server, "GET", target)
        _assert_code(response, 400, "INVALID_REQUEST")


def test_absolute_and_network_form_targets_never_become_accepted_routes():
    with _running_server() as (server, _):
        path = "/api/pocs/poc_alpha/qualification/proofability"
        targets = [
            f"http://127.0.0.1:{server.server_port}{path}",
            f"http://different.invalid:{server.server_port}{path}",
            f"//127.0.0.1:{server.server_port}{path}",
        ]
        for target in targets:
            _assert_code(_raw_request(server, "GET", target), 400, "INVALID_REQUEST")


def test_trailing_extra_and_unrelated_semicolon_paths_preserve_generic_host_behavior():
    with _running_server() as (server, _):
        for target in (
            "/api/pocs/poc_alpha/qualification/proofability/",
            "/api/pocs/poc_alpha/qualification/proofability/extra",
        ):
            response = _raw_request(server, "GET", target)
            assert response.status == 404
            assert response.body == b'{"error":"Page not found."}'
        unrelated = _raw_request(server, "GET", "/unrelated;params")
        assert unrelated.status == 400
        assert unrelated.body == (
            b'{"error":"Route parameters are not accepted."}'
        )


@pytest.mark.parametrize("method", ["HEAD", "PUT", "PATCH", "DELETE", "OPTIONS", "BREW"])
def test_every_method_token_is_intercepted_with_exact_allow_and_head_semantics(method):
    with _running_server() as (server, _):
        api = "/api/pocs/poc_alpha/qualification/proofability"
        response = _raw_request(server, method, api)
        assert response.status == 405
        assert response.headers["allow"] == "GET, POST"
        representation = canonical_json_bytes({"error_code": "METHOD_NOT_ALLOWED"})
        assert response.headers["content-length"] == str(len(representation))
        assert response.body == (b"" if method == "HEAD" else representation)


def test_page_wrong_method_has_exact_allow_and_head_invalid_target_has_no_body():
    with _running_server() as (server, _):
        page = "/app/pocs/poc_alpha/qualification/proofability"
        put = _raw_request(server, "PUT", page)
        _assert_code(put, 405, "METHOD_NOT_ALLOWED")
        assert put.headers["allow"] == "GET"
        head = _raw_request(server, "HEAD", page + "?")
        assert head.status == 400
        assert head.body == b""
        assert head.headers["content-length"] == str(
            len(canonical_json_bytes({"error_code": "INVALID_REQUEST"}))
        )


@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        ([], 400),
        ([('Content-Length', '1'), ('Content-Length', '1')], 400),
        ([('Content-Length', '1, 1')], 400),
        ([('Content-Length', '+1')], 400),
        ([('Content-Length', '-1')], 400),
        ([('Content-Length', '01')], 400),
        ([('Content-Length', '1 0')], 400),
        ([('Content-Length', '\u00a01')], 400),
    ],
)
def test_post_content_length_occurrence_and_grammar_fail_closed(headers, expected):
    with _running_server() as (server, _):
        response = _raw_request(
            server,
            "POST",
            "/api/pocs/poc_alpha/qualification/proofability",
            headers=headers,
        )
        _assert_code(response, expected, "INVALID_REQUEST")
        assert response.headers["connection"] == "close"


def test_obs_fold_content_length_is_invalid_and_closes_connection():
    with _running_server() as (server, _):
        response = _raw_request(
            server,
            "POST",
            "/api/pocs/poc_alpha/qualification/proofability",
            headers=[("Content-Length", "1\r\n 1")],
        )
        _assert_code(response, 400, "INVALID_REQUEST")
        assert response.headers["connection"] == "close"


def test_accepted_content_length_space_and_tab_ows_reaches_fresh_and_replay():
    with _running_server() as (server, _):
        path = "/api/pocs/poc_alpha/qualification/proofability"
        body = canonical_json_bytes(
            {
                "profile_id": PROFILE_ID,
                "profile_version": PROFILE_VERSION,
                "idempotency_key": "ows-key",
            }
        )
        common = [
            ("Content-Type", "application/json"),
            ("Origin", f"http://127.0.0.1:{server.server_port}"),
        ]
        first = _raw_request(
            server,
            "POST",
            path,
            headers=[("Content-Length", f"\t{len(body)} \t"), *common],
            body=body,
        )
        assert first.status == 201
        second = _raw_request(
            server,
            "POST",
            path,
            headers=[("Content-Length", f" {len(body)} "), *common],
            body=body,
        )
        assert second.status == 200
        assert _json(second.body)["idempotent_replay"] is True


def test_five_thousand_digit_length_is_413_without_conversion_or_body_read():
    with _running_server() as (server, _):
        response = _raw_request(
            server,
            "POST",
            "/api/pocs/poc_alpha/qualification/proofability",
            headers=[("Content-Length", "9" * 5_000)],
        )
        _assert_code(response, 413, "PAYLOAD_TOO_LARGE")
        assert response.headers["connection"] == "close"


def test_incomplete_body_is_invalid_and_closes_connection():
    with _running_server() as (server, _):
        response = _raw_request(
            server,
            "POST",
            "/api/pocs/poc_alpha/qualification/proofability",
            headers=[
                ("Content-Length", "20"),
                ("Content-Type", "application/json"),
                ("Origin", f"http://127.0.0.1:{server.server_port}"),
            ],
            body=b"{}",
        )
        _assert_code(response, 400, "INVALID_REQUEST")
        assert response.headers["connection"] == "close"


@pytest.mark.parametrize(
    "headers",
    [
        [("Transfer-Encoding", "chunked"), ("Content-Length", "999999")],
        [("Idempotency-Key", "forbidden"), ("Content-Length", "999999")],
        [
            ("Content-Length", "1"),
            ("Content-Length", "999999"),
        ],
        [("Content-Length", "9 9")],
    ],
)
def test_framing_faults_precede_oversize(headers):
    with _running_server() as (server, _):
        response = _raw_request(
            server,
            "POST",
            "/api/pocs/poc_alpha/qualification/proofability",
            headers=headers,
        )
        _assert_code(response, 400, "INVALID_REQUEST")
        assert response.headers["connection"] == "close"


def test_wrong_method_nonzero_length_precedes_method_and_query_precedes_method():
    with _running_server() as (server, _):
        path = "/api/pocs/poc_alpha/qualification/proofability"
        nonzero = _raw_request(
            server, "PATCH", path, headers=[("Content-Length", "1")]
        )
        _assert_code(nonzero, 400, "INVALID_REQUEST")
        query = _raw_request(server, "PATCH", path + "?")
        _assert_code(query, 400, "INVALID_REQUEST")
        exact = _raw_request(server, "PATCH", path)
        _assert_code(exact, 405, "METHOD_NOT_ALLOWED")


def test_get_accepts_absent_or_zero_length_and_rejects_nonzero():
    with _running_server() as (server, _):
        path = "/api/pocs/poc_alpha/qualification/proofability"
        assert _raw_request(server, "GET", path).status == 200
        assert _raw_request(
            server, "GET", path, headers=[("Content-Length", "0")]
        ).status == 200
        nonzero = _raw_request(
            server, "GET", path, headers=[("Content-Length", "1")]
        )
        _assert_code(nonzero, 400, "INVALID_REQUEST")
        assert nonzero.headers["connection"] == "close"


def test_media_origin_and_fixed_profile_precedence_are_exact_and_code_only():
    with _running_server() as (server, poc_id):
        path = f"/api/pocs/{poc_id}/qualification/proofability"
        body = canonical_json_bytes(
            {
                "profile_id": PROFILE_ID,
                "profile_version": PROFILE_VERSION,
                "idempotency_key": "key",
            }
        )
        unsupported = _raw_request(
            server,
            "POST",
            path,
            headers=[("Content-Length", str(len(body)))],
            body=body,
        )
        _assert_code(unsupported, 415, "UNSUPPORTED_MEDIA_TYPE")
        forbidden = _raw_request(
            server,
            "POST",
            path,
            headers=[
                ("Content-Length", str(len(body))),
                ("Content-Type", "application/json"),
                ("Origin", "http://example.invalid"),
            ],
            body=body,
        )
        _assert_code(forbidden, 403, "ORIGIN_FORBIDDEN")
        status, _, content = _post(
            server,
            poc_id,
            "key",
            profile_id="profil\u00e9",
        )
        assert status == 422
        assert content == canonical_json_bytes({"error_code": "PROFILE_UNSUPPORTED"})
        status, _, content = _post(
            server,
            poc_id,
            "key",
            profile_id=PROFILE_ID.upper(),
        )
        assert status == 422
        assert content == canonical_json_bytes({"error_code": "PROFILE_UNSUPPORTED"})
        status, _, content = _post(
            server,
            poc_id,
            "key",
            profile_id=PROFILE_ID + " ",
        )
        assert status == 400
        assert content == canonical_json_bytes({"error_code": "INVALID_REQUEST"})
        status, _, content = _post(
            server,
            poc_id,
            "key",
            profile_version="v\u00e9",
        )
        assert status == 422
        assert content == canonical_json_bytes({"error_code": "PROFILE_UNSUPPORTED"})


def test_exact_loopback_host_and_port_are_required_independently_of_origin():
    with _running_server() as (server, _):
        body = canonical_json_bytes(
            {
                "profile_id": PROFILE_ID,
                "profile_version": PROFILE_VERSION,
                "idempotency_key": "host-key",
            }
        )
        response = _raw_request(
            server,
            "POST",
            "/api/pocs/poc_alpha/qualification/proofability",
            headers=[
                ("Content-Length", str(len(body))),
                ("Content-Type", "application/json"),
                ("Origin", f"http://127.0.0.1:{server.server_port}"),
            ],
            body=body,
            host=f"127.0.0.1:{server.server_port + 1}",
        )
        _assert_code(response, 403, "ORIGIN_FORBIDDEN")
        assert response.headers["connection"] == "close"


@pytest.mark.parametrize(
    "body",
    [
        b'[]',
        b'{"profile_id":"x","profile_id":"y","profile_version":"v1","idempotency_key":"k"}',
        b'{"profile_id":',
        b'\xff',
        b'{"profile_id":"exitspec.external-evidence.native-ttft-profile.v1","profile_version":"v1","idempotency_key":"\\ud800"}',
        b'{"profile_id":"exitspec.external-evidence.native-ttft-profile.v1","profile_version":"v1","idempotency_key":""}',
        b'{"profile_id":"exitspec.external-evidence.native-ttft-profile.v1","profile_version":"v1","idempotency_key":" key"}',
        b'{"profile_id":"exitspec.external-evidence.native-ttft-profile.v1","profile_version":"v1","idempotency_key":"key\\u00a0"}',
        canonical_json_bytes(
            {
                "profile_id": PROFILE_ID,
                "profile_version": PROFILE_VERSION,
                "idempotency_key": "x" * 129,
            }
        ),
        b'{"profile_id":"exitspec.external-evidence.native-ttft-profile.v1","profile_version":"v1","idempotency_key":"k","extra":"HOSTILE_SENTINEL"}',
    ],
)
def test_json_shape_duplicate_utf8_surrogate_and_scalar_failures_are_400(body):
    with _running_server() as (server, _):
        response = _raw_request(
            server,
            "POST",
            "/api/pocs/poc_alpha/qualification/proofability",
            headers=[
                ("Content-Length", str(len(body))),
                ("Content-Type", "application/json"),
                ("Origin", f"http://127.0.0.1:{server.server_port}"),
            ],
            body=body,
        )
        _assert_code(response, 400, "INVALID_REQUEST")
        assert b"HOSTILE_SENTINEL" not in response.body


def test_oversized_declared_body_is_413_before_read_and_unknown_poc_is_404():
    with _running_server() as (server, _):
        oversized = _raw_request(
            server,
            "POST",
            "/api/pocs/poc_alpha/qualification/proofability",
            headers=[("Content-Length", "131073")],
        )
        _assert_code(oversized, 413, "PAYLOAD_TOO_LARGE")
        missing = _raw_request(
            server,
            "GET",
            "/api/pocs/poc_unknown/qualification/proofability",
        )
        _assert_code(missing, 404, "POC_NOT_FOUND")


@pytest.mark.parametrize(
    "body",
    [
        canonical_json_bytes(
            {
                "profile_id": PROFILE_ID,
                "profile_version": PROFILE_VERSION,
                "idempotency_key": "node-heavy",
                "extra": [0] * 4_096,
            }
        ),
        (
            b'{"profile_id":"'
            + PROFILE_ID.encode("ascii")
            + b'","profile_version":"v1","idempotency_key":"deep","extra":'
            + b"[" * 40
            + b"0"
            + b"]" * 40
            + b"}"
        ),
        b"[" * 1_500 + b"0" + b"]" * 1_500,
    ],
)
def test_depth_node_and_parser_recursion_overflow_are_payload_too_large(body):
    with _running_server() as (server, _):
        response = _raw_request(
            server,
            "POST",
            "/api/pocs/poc_alpha/qualification/proofability",
            headers=[
                ("Content-Length", str(len(body))),
                ("Content-Type", "application/json"),
                ("Origin", f"http://127.0.0.1:{server.server_port}"),
            ],
            body=body,
        )
        _assert_code(response, 413, "PAYLOAD_TOO_LARGE")


def test_nfc_and_nfd_idempotency_keys_remain_distinct_through_http_and_jcs():
    with _running_server() as (server, poc_id):
        nfc = "caf\u00e9"
        nfd = "cafe\u0301"
        first = _json(_post(server, poc_id, nfc)[2])
        second = _json(_post(server, poc_id, nfd)[2])
        assert first["idempotent_replay"] is False
        assert second["idempotent_replay"] is False
        assert _json(_post(server, poc_id, nfc)[2])["idempotent_replay"] is True
        assert _json(_post(server, poc_id, nfd)[2])["idempotent_replay"] is True


@pytest.mark.parametrize("suffix", [";", ";params"])
@pytest.mark.parametrize("content_length", [None, "0"])
def test_final_segment_semicolon_wrong_method_is_target_error_before_method(
    suffix, content_length
):
    with _running_server() as (server, _):
        for prefix in ("api", "app"):
            target = (
                f"/{prefix}/pocs/poc_alpha/qualification/proofability{suffix}"
            )
            headers = (
                []
                if content_length is None
                else [("Content-Length", content_length)]
            )
            response = _raw_request(server, "PATCH", target, headers=headers)
            _assert_code(response, 400, "INVALID_REQUEST")


def test_error_bodies_never_echo_hostile_customer_provider_key_path_or_exception():
    with _running_server() as (server, _):
        sentinels = [
            "HOSTILE_CUSTOMER_9F2A",
            "HOSTILE_PROVIDER_9F2A",
            "HOSTILE_CREDENTIAL_9F2A",
            "/private/HOSTILE_PATH_9F2A",
            "HOSTILE_KEY_9F2A",
        ]
        body = canonical_json_bytes(
            {
                "profile_id": sentinels[1],
                "profile_version": PROFILE_VERSION,
                "idempotency_key": sentinels[4],
                "customer": sentinels[0],
                "credential": sentinels[2],
                "path": sentinels[3],
            }
        )
        response = _raw_request(
            server,
            "POST",
            "/api/pocs/poc_alpha/qualification/proofability",
            headers=[
                ("Content-Length", str(len(body))),
                ("Content-Type", "application/json"),
                ("Origin", f"http://127.0.0.1:{server.server_port}"),
            ],
            body=body,
        )
        _assert_code(response, 400, "INVALID_REQUEST")
        assert all(sentinel.encode() not in response.body for sentinel in sentinels)
