"""Adversarial transport tests for the frozen guided source boundary."""

from __future__ import annotations

from itertools import combinations
import json
from pathlib import Path
import socket
import threading
import time

import pytest

from exitspec.source_web import (
    SOURCE_CATALOG_PATH,
    SOURCE_IMPORT_PATH,
    SOURCE_REQUEST_LIMIT_BYTES,
    SourceWebRefusal,
    SourceWebRequest,
    handle_source_web_request,
    is_source_pipeline_target,
)
from exitspec.web import DemoSession, ExitSpecDemoServer


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONTRACT = json.loads(
    (
        PROJECT_ROOT
        / "examples"
        / "support-agent"
        / "email"
        / "wave-2-source-web-v1.json"
    ).read_text(encoding="utf-8")
)
PORT = 8765
VALID_BODY = b'{"fixture_case_id":"thread-root"}'
FROZEN_REQUEST_LIMIT_BYTES = 65_536
FROZEN_OBSERVED_LIMIT_BYTES = 65_537


class _Headers:
    def __init__(self, entries: list[tuple[str, str]]) -> None:
        self.entries = entries

    def values(self, name: str) -> tuple[str, ...]:
        return tuple(
            value
            for header, value in self.entries
            if header.lower() == name.lower()
        )


def _request(
    *,
    method: str = "POST",
    target: str = SOURCE_IMPORT_PATH,
    body: bytes = VALID_BODY,
    entries: list[tuple[str, str]] | None = None,
    observed_body: bytes | None = None,
) -> SourceWebRequest:
    if entries is None:
        entries = [
            ("Host", "127.0.0.1:{0}".format(PORT)),
            ("Origin", "http://127.0.0.1:{0}".format(PORT)),
            ("Sec-Fetch-Site", "same-origin"),
            ("Content-Type", "application/json"),
            ("Content-Length", str(len(body))),
        ]
    headers = _Headers(entries)
    return SourceWebRequest(
        method=method,
        target=target,
        server_port=PORT,
        header_values=headers.values,
        read_body=lambda _declared, _maximum: (
            body if observed_body is None else observed_body
        ),
    )


def _catalog_example() -> dict:
    return next(
        endpoint["exact_success_example"]
        for endpoint in CONTRACT["endpoints"]
        if endpoint["method"] == "GET"
    )


def _run(
    request: SourceWebRequest,
    *,
    importer=None,
):
    if importer is None:
        def importer(_case_id):
            return {"accepted": True}
    response = handle_source_web_request(
        request,
        catalog_payload=_catalog_example,
        import_fixture=importer,
    )
    assert response is not None
    return response


def _code(response) -> str:
    return response.payload["error"]["code"]


def _replace(
    entries: list[tuple[str, str]],
    name: str,
    values: list[str],
) -> list[tuple[str, str]]:
    retained = [
        (header, value)
        for header, value in entries
        if header.lower() != name.lower()
    ]
    return retained + [(name, value) for value in values]


def _valid_entries(body: bytes = VALID_BODY) -> list[tuple[str, str]]:
    return [
        ("Host", "127.0.0.1:{0}".format(PORT)),
        ("Origin", "http://127.0.0.1:{0}".format(PORT)),
        ("Sec-Fetch-Site", "same-origin"),
        ("Content-Type", "application/json"),
        ("Content-Length", str(len(body))),
    ]


def _fault_request(
    *,
    method_context: str,
    active_orders: frozenset[int],
) -> SourceWebRequest:
    """Build one route-valid request with independently healable faults."""

    if method_context == "GET":
        body = b"X" if 4 in active_orders else b""
        method = "GET"
        target = SOURCE_CATALOG_PATH
    else:
        if 9 in active_orders:
            if 10 in active_orders:
                body = (
                    b'{"fixture_case_id":"thread-root","extra":true'
                )
            elif 11 in active_orders:
                body = b'{"fixture_case_id":"not-guided"'
            else:
                body = b'{"fixture_case_id":"thread-root"'
        elif 10 in active_orders:
            if 11 in active_orders:
                body = (
                    b'{"fixture_case_id":"not-guided","extra":true}'
                )
            else:
                body = (
                    b'{"fixture_case_id":"thread-root","extra":true}'
                )
        elif 11 in active_orders:
            body = b'{"fixture_case_id":"not-guided"}'
        else:
            body = VALID_BODY
        method = "POST"
        target = SOURCE_IMPORT_PATH

    entries = _valid_entries(body)
    if 1 in active_orders:
        entries = _replace(entries, "Host", ["evil.example:443"])
    if 2 in active_orders:
        method = "BREW"
    if 3 in active_orders:
        target += "?fault=1"
    if 5 in active_orders:
        entries = _replace(entries, "Origin", [])
    if 6 in active_orders:
        entries = _replace(
            entries,
            "Sec-Fetch-Site",
            ["cross-site"],
        )
    if 7 in active_orders:
        entries = _replace(entries, "Content-Type", ["text/plain"])
    if 8 in active_orders:
        entries = _replace(
            entries,
            "Content-Length",
            [str(FROZEN_OBSERVED_LIMIT_BYTES)],
        )

    return _request(
        method=method,
        target=target,
        body=body,
        entries=entries,
    )


def _execute_faults(
    *,
    method_context: str,
    active_orders: frozenset[int],
):
    calls = {"catalog": 0, "imports": 0, "mutations": 0}

    def catalog():
        calls["catalog"] += 1
        return _catalog_example()

    def importer(_case_id):
        calls["imports"] += 1
        if 12 in active_orders:
            raise SourceWebRefusal("source_import_locked")
        calls["mutations"] += 1
        return {"accepted": True}

    response = handle_source_web_request(
        _fault_request(
            method_context=method_context,
            active_orders=active_orders,
        ),
        catalog_payload=catalog,
        import_fixture=importer,
    )
    assert response is not None
    return response, calls


def _assert_fault_outcome(response, fault: dict) -> None:
    assert response.status == fault["http_status"]
    assert _code(response) == fault["code"]
    assert response.payload["state_unchanged"] is True


def _assert_zero_mutation(
    calls: dict[str, int],
    active_orders: frozenset[int],
) -> None:
    assert calls["catalog"] == 0
    assert calls["mutations"] == 0
    assert calls["imports"] == (1 if active_orders == {12} else 0)


def _canonical_pair_partition():
    canonical = CONTRACT["transport_validation"][
        "canonical_faults_for_pairwise_testing"
    ]
    method_domains = {
        gate["order"]: frozenset(gate["methods"])
        for gate in CONTRACT["transport_validation"]["ordered_pipeline"]
    }
    pairs = list(combinations(canonical, 2))
    compatible = [
        pair
        for pair in pairs
        if method_domains[pair[0]["order"]]
        & method_domains[pair[1]["order"]]
    ]
    incompatible = [
        pair
        for pair in pairs
        if not (
            method_domains[pair[0]["order"]]
            & method_domains[pair[1]["order"]]
        )
    ]
    return method_domains, pairs, compatible, incompatible


@pytest.mark.parametrize(
    ("case_request", "expected_status", "expected_code"),
    [
        (
            _request(entries=_replace(_valid_entries(), "Host", [])),
            400,
            "invalid_local_host",
        ),
        (
            _request(target="/api/source/unknown"),
            404,
            "unknown_source_route",
        ),
        (
            _request(method="PUT"),
            405,
            "method_not_allowed",
        ),
        (
            _request(target=SOURCE_IMPORT_PATH + "?x=1"),
            400,
            "route_parameters_not_allowed",
        ),
        (
            _request(
                method="GET",
                target=SOURCE_CATALOG_PATH,
                body=b"",
                entries=[
                    ("Host", "127.0.0.1:{0}".format(PORT)),
                    ("Content-Length", "1"),
                ],
            ),
            400,
            "get_body_not_allowed",
        ),
        (
            _request(entries=_replace(_valid_entries(), "Origin", [])),
            403,
            "forbidden_origin",
        ),
        (
            _request(
                entries=_replace(
                    _valid_entries(),
                    "Sec-Fetch-Site",
                    ["cross-site"],
                )
            ),
            403,
            "forbidden_fetch_site",
        ),
        (
            _request(
                entries=_replace(
                    _valid_entries(),
                    "Content-Type",
                    ["text/plain"],
                )
            ),
            415,
            "unsupported_media_type",
        ),
        (
            _request(
                entries=_replace(
                    _replace(_valid_entries(), "Content-Length", []),
                    "Transfer-Encoding",
                    ["chunked"],
                )
            ),
            411,
            "request_length_required",
        ),
        (
            _request(
                entries=_replace(
                    _valid_entries(),
                    "Content-Length",
                    ["033"],
                )
            ),
            400,
            "invalid_content_length",
        ),
        (
            _request(
                entries=_replace(
                    _valid_entries(),
                    "Content-Length",
                    ["65537"],
                )
            ),
            413,
            "source_request_too_large",
        ),
        (
            _request(observed_body=VALID_BODY[:-1]),
            400,
            "content_length_mismatch",
        ),
        (
            _request(
                body=b"",
                entries=_replace(
                    _valid_entries(b""),
                    "Content-Length",
                    ["0"],
                ),
            ),
            400,
            "empty_json_body",
        ),
        (
            _request(body=b"{"),
            400,
            "malformed_json",
        ),
        (
            _request(
                body=(
                    b'{"fixture_case_id":"thread-root",'
                    b'"fixture_case_id":"authority-attack"}'
                )
            ),
            400,
            "duplicate_json_member",
        ),
        (
            _request(body=b"[]"),
            400,
            "json_object_required",
        ),
        (
            _request(
                body=b'{"fixture_case_id":"thread-root","extra":true}'
            ),
            400,
            "invalid_source_request",
        ),
        (
            _request(body=b'{"fixture_case_id":"not-guided"}'),
            404,
            "source_not_approved",
        ),
    ],
)
def test_each_transport_fault_has_the_frozen_status_and_code(
    case_request,
    expected_status,
    expected_code,
):
    response = _run(case_request)
    assert response.status == expected_status
    assert _code(response) == expected_code
    assert response.payload["state_unchanged"] is True
    assert set(response.payload) == {
        "contract_version",
        "error",
        "state_unchanged",
    }


def test_gate_one_wins_for_wrong_path_and_wrong_method():
    bad_host = _replace(_valid_entries(), "Host", ["evil.example:443"])
    for request in (
        _request(target="/api/source/unknown", entries=bad_host),
        _request(method="DELETE", entries=bad_host),
    ):
        response = _run(request)
        assert response.status == 400
        assert _code(response) == "invalid_local_host"


@pytest.mark.parametrize(
    "method",
    ["CONNECT", "DELETE", "HEAD", "OPTIONS", "PATCH", "PUT", "TRACE"],
)
def test_all_standard_wrong_methods_receive_typed_405(method):
    response = _run(_request(method=method))
    assert response.status == 405
    assert _code(response) == "method_not_allowed"


def test_absolute_form_target_is_not_accepted_as_local_authority():
    response = _run(
        _request(
            target="http://evil.example{0}".format(SOURCE_IMPORT_PATH)
        )
    )
    assert response.status == 400
    assert _code(response) == "invalid_local_host"


def test_source_scope_is_exact_and_does_not_capture_source_control():
    scope = CONTRACT["transport_validation"]["scope"]
    assert all(
        is_source_pipeline_target(path)
        for path in scope["in_scope_examples"]
    )
    assert all(
        not is_source_pipeline_target(path)
        for path in scope["out_of_scope_examples"]
    )
    assert is_source_pipeline_target("/api/source-control") is False
    assert (
        handle_source_web_request(
            _request(target="/api/source-control"),
            catalog_payload=_catalog_example,
            import_fixture=lambda _case_id: {},
        )
        is None
    )


def test_catalog_origin_fetch_and_body_semantics_are_exact():
    base = [
        ("Host", "127.0.0.1:{0}".format(PORT)),
        ("Sec-Fetch-Site", "same-origin"),
    ]
    absent = _run(
        _request(
            method="GET",
            target=SOURCE_CATALOG_PATH,
            body=b"",
            entries=base,
        )
    )
    assert absent.status == 200
    assert absent.payload == _catalog_example()

    exact = _run(
        _request(
            method="GET",
            target=SOURCE_CATALOG_PATH,
            body=b"",
            entries=base
            + [("Origin", "http://127.0.0.1:{0}".format(PORT))],
        )
    )
    assert exact.status == 200

    wrong = _run(
        _request(
            method="GET",
            target=SOURCE_CATALOG_PATH,
            body=b"",
            entries=base + [("Origin", "http://localhost:{0}".format(PORT))],
        )
    )
    assert wrong.status == 403
    assert _code(wrong) == "forbidden_origin"

    undeclared_body = _run(
        _request(
            method="GET",
            target=SOURCE_CATALOG_PATH,
            body=b"",
            observed_body=b"X",
            entries=base,
        )
    )
    assert undeclared_body.status == 400
    assert _code(undeclared_body) == "get_body_not_allowed"


def test_exact_request_size_boundary_and_observed_oversize():
    assert SOURCE_REQUEST_LIMIT_BYTES == FROZEN_REQUEST_LIMIT_BYTES
    assert FROZEN_REQUEST_LIMIT_BYTES == 65_536
    assert FROZEN_OBSERVED_LIMIT_BYTES == 65_537

    allowed = VALID_BODY + b" " * (
        FROZEN_REQUEST_LIMIT_BYTES - len(VALID_BODY)
    )
    assert len(allowed) == 65_536
    accepted = _run(_request(body=allowed))
    assert accepted.status == 200

    declared_oversize = _run(
        _request(
            body=allowed + b" ",
            entries=_replace(
                _valid_entries(allowed),
                "Content-Length",
                ["65537"],
            ),
        )
    )
    assert declared_oversize.status == 413
    assert _code(declared_oversize) == "source_request_too_large"

    observed_oversize = _run(
        _request(
            body=allowed,
            observed_body=allowed + b"X",
        )
    )
    assert observed_oversize.status == 413
    assert _code(observed_oversize) == "source_request_too_large"


def test_all_frozen_duplicate_member_and_malformed_precedence_probes():
    gate = CONTRACT["transport_validation"]["ordered_pipeline"][8]
    for probe in gate["duplicate_member_probes"]:
        body = probe["body_utf8"].encode("utf-8")
        assert len(body) == probe["content_length"]
        response = _run(_request(body=body))
        assert response.status == probe["expected_http_status"]
        assert _code(response) == probe["expected_code"]
        assert probe["zero_mutation"] is True

    for probe in gate["within_gate_precedence_probes"]:
        body = probe["body_utf8"].encode("utf-8")
        assert len(body) == probe["content_length"]
        response = _run(_request(body=body))
        assert response.status == probe["expected_http_status"]
        assert _code(response) == probe["expected_code"]


@pytest.mark.parametrize(
    "numeric_value",
    [
        b"7" * 5_000,
        b"0." + b"7" * 5_000,
    ],
)
def test_large_valid_json_numbers_reach_exact_field_validation(
    numeric_value,
):
    calls = {"imports": 0}

    def importer(_case_id):
        calls["imports"] += 1
        return {"accepted": True}

    body = b'{"fixture_case_id":' + numeric_value + b"}"
    response = _run(_request(body=body), importer=importer)
    assert response.status == 400
    assert _code(response) == "invalid_source_request"
    assert calls["imports"] == 0


def test_deep_json_is_iterative_and_preserves_gate_nine_precedence():
    calls = {"imports": 0}

    def importer(_case_id):
        calls["imports"] += 1
        return {"accepted": True}

    depth = 1_000
    valid_array = b"[" * depth + b"0" + b"]" * depth
    response = _run(_request(body=valid_array), importer=importer)
    assert response.status == 400
    assert _code(response) == "json_object_required"

    malformed_array = b"[" * depth + b"0" + b"]" * (depth - 1)
    response = _run(_request(body=malformed_array), importer=importer)
    assert response.status == 400
    assert _code(response) == "malformed_json"

    duplicate = b'{"deep":1,"\\u0064eep":2}'
    deep_duplicate = b"[" * depth + duplicate + b"]" * depth
    response = _run(_request(body=deep_duplicate), importer=importer)
    assert response.status == 400
    assert _code(response) == "duplicate_json_member"

    malformed_after_duplicate = deep_duplicate[:-1]
    response = _run(
        _request(body=malformed_after_duplicate),
        importer=importer,
    )
    assert response.status == 400
    assert _code(response) == "malformed_json"
    assert calls["imports"] == 0


def test_59_method_compatible_pairs_have_metamorphic_healing_proof():
    method_domains, pairs, compatible, incompatible = (
        _canonical_pair_partition()
    )
    assert len(pairs) == 66
    assert len(compatible) == 59
    assert len(incompatible) == 7
    assert len(compatible) + len(incompatible) == 66

    for lower, higher in compatible:
        common_methods = (
            method_domains[lower["order"]]
            & method_domains[higher["order"]]
        )
        method_context = "GET" if "GET" in common_methods else "POST"
        combined_orders = frozenset(
            {lower["order"], higher["order"]}
        )

        combined, calls = _execute_faults(
            method_context=method_context,
            active_orders=combined_orders,
        )
        _assert_fault_outcome(combined, lower)
        _assert_zero_mutation(calls, combined_orders)

        higher_only = frozenset({higher["order"]})
        lower_healed, calls = _execute_faults(
            method_context=method_context,
            active_orders=higher_only,
        )
        _assert_fault_outcome(lower_healed, higher)
        _assert_zero_mutation(calls, higher_only)

        lower_only = frozenset({lower["order"]})
        higher_healed, calls = _execute_faults(
            method_context=method_context,
            active_orders=lower_only,
        )
        _assert_fault_outcome(higher_healed, lower)
        _assert_zero_mutation(calls, lower_only)


def test_seven_method_exclusive_pairs_are_abstract_not_simultaneous_wire_claims():
    method_domains, pairs, compatible, incompatible = (
        _canonical_pair_partition()
    )
    incompatible_orders = {
        (left["order"], right["order"])
        for left, right in incompatible
    }
    expected_incompatible = {
        (4, 5),
        (4, 7),
        (4, 8),
        (4, 9),
        (4, 10),
        (4, 11),
        (4, 12),
    }

    assert len(pairs) == 66
    assert len(compatible) == 59
    assert len(incompatible) == 7
    assert incompatible_orders == expected_incompatible
    assert method_domains[4] == {"GET"}
    assert all(
        method_domains[post_order] == {"POST"}
        for _, post_order in expected_incompatible
    )

    # The contract's total precedence order covers these seven abstract pairs,
    # but disjoint method domains mean they cannot be one simultaneous request.
    for catalog_fault, post_fault in incompatible:
        gate_four_orders = frozenset({catalog_fault["order"]})
        response, calls = _execute_faults(
            method_context="GET",
            active_orders=gate_four_orders,
        )
        _assert_fault_outcome(response, catalog_fault)
        _assert_zero_mutation(calls, gate_four_orders)

        post_orders = frozenset({post_fault["order"]})
        response, calls = _execute_faults(
            method_context="POST",
            active_orders=post_orders,
        )
        _assert_fault_outcome(response, post_fault)
        _assert_zero_mutation(calls, post_orders)


def test_frozen_multi_fault_oracles_match_executable_early_failures():
    # These requests exercise representative combined faults without touching
    # parser/session callbacks. The 59 compatible pairs and seven explicitly
    # method-exclusive abstract pairs retain complete 66-pair accounting above.
    calls = {"imports": 0}

    def importer(_case_id):
        calls["imports"] += 1
        raise SourceWebRefusal("source_import_locked")

    host_faults = _replace(_valid_entries(b"not json"), "Host", ["evil:443"])
    host_faults = _replace(host_faults, "Origin", ["https://evil"])
    host_faults = _replace(host_faults, "Sec-Fetch-Site", ["cross-site"])
    host_faults = _replace(host_faults, "Content-Type", ["text/plain"])
    host_faults = _replace(host_faults, "Content-Length", ["65537"])
    response = _run(
        _request(body=b"not json", entries=host_faults),
        importer=importer,
    )
    assert _code(response) == "invalid_local_host"

    response = _run(
        _request(
            target=SOURCE_IMPORT_PATH + "?x=1",
            body=b"not json",
            entries=_replace(
                _replace(_valid_entries(), "Origin", []),
                "Content-Length",
                ["65537"],
            ),
        ),
        importer=importer,
    )
    assert _code(response) == "route_parameters_not_allowed"

    response = _run(
        _request(
            body=b"{",
            entries=_replace(
                _replace(_valid_entries(b"{"), "Content-Type", ["text/plain"]),
                "Content-Length",
                ["65537"],
            ),
        ),
        importer=importer,
    )
    assert _code(response) == "unsupported_media_type"
    assert calls["imports"] == 0


def _running_server(tmp_path: Path):
    session = DemoSession.synthetic_support_agent(tmp_path / "runs")
    server = ExitSpecDemoServer(("127.0.0.1", 0), session)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    return session, server, worker


def _raw_response(port: int, payload: bytes) -> tuple[float, bytes]:
    started = time.monotonic()
    with socket.create_connection(("127.0.0.1", port), timeout=3) as client:
        client.settimeout(3)
        client.sendall(payload)
        response = b""
        while True:
            chunk = client.recv(65536)
            if not chunk:
                break
            response += chunk
    return time.monotonic() - started, response


def _raw_delayed_response(
    port: int,
    initial: bytes,
    delayed: bytes,
    *,
    delay_seconds: float,
) -> tuple[float, bytes]:
    started = time.monotonic()
    with socket.create_connection(("127.0.0.1", port), timeout=3) as client:
        client.settimeout(3)
        client.sendall(initial)
        time.sleep(delay_seconds)
        client.sendall(delayed)
        response = b""
        while True:
            chunk = client.recv(65536)
            if not chunk:
                break
            response += chunk
    return time.monotonic() - started, response


def _response_json(response: bytes) -> tuple[int, dict]:
    head, body = response.split(b"\r\n\r\n", 1)
    status = int(head.split(b"\r\n", 1)[0].split()[1])
    return status, json.loads(body.decode("utf-8"))


def _source_import_wire_request(
    authority: str,
    body: bytes,
    *,
    declared_length: int | None = None,
) -> bytes:
    length = len(body) if declared_length is None else declared_length
    return (
        "POST {path} HTTP/1.1\r\n"
        "Host: {authority}\r\n"
        "Origin: http://{authority}\r\n"
        "Sec-Fetch-Site: same-origin\r\n"
        "Content-Type: application/json\r\n"
        "Content-Length: {length}\r\n"
        "\r\n"
    ).format(
        path=SOURCE_IMPORT_PATH,
        authority=authority,
        length=length,
    ).encode("ascii") + body


def test_arbitrary_method_tokens_use_source_dispatcher_only_in_scope(
    tmp_path,
):
    session, server, worker = _running_server(tmp_path)
    try:
        authority = "127.0.0.1:{0}".format(server.server_port)
        cases = [
            (
                SOURCE_IMPORT_PATH,
                "evil.example:443",
                400,
                "invalid_local_host",
            ),
            (
                SOURCE_IMPORT_PATH,
                authority,
                405,
                "method_not_allowed",
            ),
            (
                "/api/source/unknown",
                authority,
                404,
                "unknown_source_route",
            ),
        ]
        for path, host, expected_status, expected_code in cases:
            request = (
                "BREW {path} HTTP/1.1\r\n"
                "Host: {host}\r\n"
                "Connection: close\r\n"
                "\r\n"
            ).format(path=path, host=host).encode("ascii")
            _, raw = _raw_response(server.server_port, request)
            status, payload = _response_json(raw)
            assert status == expected_status
            assert payload["error"]["code"] == expected_code
            assert b"Content-Type: application/json" in raw

        ordinary = (
            "BREW /api/state HTTP/1.1\r\n"
            "Host: {authority}\r\n"
            "Connection: close\r\n"
            "\r\n"
        ).format(authority=authority).encode("ascii")
        _, raw = _raw_response(server.server_port, ordinary)
        head = raw.split(b"\r\n\r\n", 1)[0]
        assert int(head.split(b"\r\n", 1)[0].split()[1]) == 501
        assert b"Content-Type: text/html" in head
        assert session._source_runtime.counts().accepted_write_transaction_count == 0
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


def test_stalled_short_body_is_bounded_and_zero_mutation(tmp_path):
    session, server, worker = _running_server(tmp_path)
    try:
        authority = "127.0.0.1:{0}".format(server.server_port)
        request = (
            "POST {path} HTTP/1.1\r\n"
            "Host: {authority}\r\n"
            "Origin: http://{authority}\r\n"
            "Sec-Fetch-Site: same-origin\r\n"
            "Content-Type: application/json\r\n"
            "Content-Length: 33\r\n"
            "\r\n"
            "{{"
        ).format(path=SOURCE_IMPORT_PATH, authority=authority).encode("ascii")
        elapsed, raw = _raw_response(server.server_port, request)
        status, payload = _response_json(raw)
        assert elapsed < 1.5
        assert status == 400
        assert payload["error"]["code"] == "content_length_mismatch"
        assert session.state_payload()["source_intake"] is None
        assert session._source_runtime.counts().accepted_write_transaction_count == 0
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


def test_surplus_smuggled_request_bytes_are_refused_and_connection_closed(
    tmp_path,
):
    session, server, worker = _running_server(tmp_path)
    try:
        authority = "127.0.0.1:{0}".format(server.server_port)
        smuggled = b"GET /api/state HTTP/1.1\r\nHost: " + authority.encode(
            "ascii"
        ) + b"\r\n\r\n"
        request = (
            "POST {path} HTTP/1.1\r\n"
            "Host: {authority}\r\n"
            "Origin: http://{authority}\r\n"
            "Sec-Fetch-Site: same-origin\r\n"
            "Content-Type: application/json\r\n"
            "Content-Length: {length}\r\n"
            "\r\n"
        ).format(
            path=SOURCE_IMPORT_PATH,
            authority=authority,
            length=len(VALID_BODY),
        ).encode("ascii") + VALID_BODY + smuggled
        _, raw = _raw_response(server.server_port, request)
        status, payload = _response_json(raw)
        assert status == 400
        assert payload["error"]["code"] == "content_length_mismatch"
        assert raw.count(b"HTTP/1.") == 1
        assert session.state_payload()["source_intake"] is None
        assert session._source_runtime.counts().accepted_write_transaction_count == 0
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


def test_delayed_pipelined_bytes_are_caught_before_source_mutation(
    tmp_path,
):
    session, server, worker = _running_server(tmp_path)
    try:
        authority = "127.0.0.1:{0}".format(server.server_port)
        initial = _source_import_wire_request(authority, VALID_BODY)
        delayed = (
            "GET /api/state HTTP/1.1\r\n"
            "Host: {authority}\r\n"
            "\r\n"
        ).format(authority=authority).encode("ascii")
        elapsed, raw = _raw_delayed_response(
            server.server_port,
            initial,
            delayed,
            delay_seconds=0.001,
        )
        status, payload = _response_json(raw)
        assert elapsed < 1.0
        assert status == 400
        assert payload["error"]["code"] == "content_length_mismatch"
        assert raw.count(b"HTTP/1.") == 1
        assert session.state_payload()["source_intake"] is None
        assert session._source_runtime.counts().accepted_write_transaction_count == 0
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


def test_raw_transport_pins_65536_and_65537_byte_boundaries(tmp_path):
    session, server, worker = _running_server(tmp_path)
    try:
        authority = "127.0.0.1:{0}".format(server.server_port)
        allowed = VALID_BODY + b" " * (65_536 - len(VALID_BODY))
        assert len(allowed) == 65_536

        _, raw = _raw_response(
            server.server_port,
            _source_import_wire_request(
                authority,
                allowed,
                declared_length=65_536,
            ),
        )
        status, _ = _response_json(raw)
        assert status == 200
        assert session._source_runtime.counts().accepted_write_transaction_count == 1

        _, raw = _raw_response(
            server.server_port,
            _source_import_wire_request(
                authority,
                b"",
                declared_length=65_537,
            ),
        )
        status, payload = _response_json(raw)
        assert status == 413
        assert payload["error"]["code"] == "source_request_too_large"

        observed_oversize = allowed + b"X"
        assert len(observed_oversize) == 65_537
        _, raw = _raw_response(
            server.server_port,
            _source_import_wire_request(
                authority,
                observed_oversize,
                declared_length=65_536,
            ),
        )
        status, payload = _response_json(raw)
        assert status == 413
        assert payload["error"]["code"] == "source_request_too_large"
        assert session._source_runtime.counts().accepted_write_transaction_count == 1
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
