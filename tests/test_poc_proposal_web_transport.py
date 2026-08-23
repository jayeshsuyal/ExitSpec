from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from http.client import HTTPConnection
from pathlib import Path

import pytest

from exitspec.web import MAX_REQUEST_BYTES, DemoSession, ExitSpecDemoServer


@contextmanager
def _running_server(tmp_path: Path):
    session = DemoSession.synthetic_support_agent(output_root=tmp_path / "runs")
    server = ExitSpecDemoServer(("127.0.0.1", 0), session)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield server
    finally:
        server.shutdown()
        worker.join(timeout=5)
        server.server_close()


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
) -> tuple[int, dict | str, str]:
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
        connection.request(method, target, body=body, headers=request_headers)
        response = connection.getresponse()
        data = response.read()
        media_type = response.getheader("Content-Type") or ""
        parsed = (
            json.loads(data.decode("utf-8"))
            if media_type.startswith("application/json")
            else data.decode("utf-8")
        )
        return response.status, parsed, media_type
    finally:
        connection.close()


def _create_draft(server: ExitSpecDemoServer) -> str:
    status, payload, _ = _request(
        server,
        "POST",
        "/api/pocs",
        payload={
            "display_name": "Inference validation",
            "customer_label": "Northstar",
            "use_case": "Validate one bounded performance claim.",
            "owner": "field_engineer",
            "first_source_choice": "DOCUMENT",
            "idempotency_key": "create-proposal-transport",
        },
    )
    assert status == 201
    assert isinstance(payload, dict)
    return str(payload["poc_id"])


def _capture_source(server: ExitSpecDemoServer, poc_id: str) -> None:
    status, payload, _ = _request(
        server,
        "POST",
        f"/api/pocs/{poc_id}/sources/document",
        payload={
            "document_text": (
                "The p95 latency must stay below 500 ms. "
                "Error rate must remain below 1%."
            ),
            "idempotency_key": "capture-proposal-transport",
        },
    )
    assert status == 201
    assert isinstance(payload, dict)
    assert payload["proposal_count"] == 2


def test_source_to_human_decision_is_one_real_process_local_round_trip(tmp_path):
    with _running_server(tmp_path) as server:
        poc_id = _create_draft(server)
        _capture_source(server, poc_id)
        root = f"/api/pocs/{poc_id}/proposals"
        list_status, listed, _ = _request(
            server,
            "GET",
            root,
            content_type=None,
            origin=None,
        )
        assert isinstance(listed, dict)
        first = listed["proposals"][0]
        decision_status, decision, _ = _request(
            server,
            "POST",
            f"{root}/{first['proposal_id']}/decision",
            payload={
                "decision": "KEEP_FOR_CONTRACT",
                "reviewer": "Jayesh",
                "rationale": "This is a measurable customer requirement.",
                "idempotency_key": "proposal-transport-keep",
            },
        )
        remaining_status, remaining, _ = _request(
            server,
            "GET",
            root,
            content_type=None,
            origin=None,
        )

    assert list_status == 200
    assert decision_status == 201
    assert remaining_status == 200
    assert isinstance(decision, dict)
    assert decision["review_state"] == "KEEP_FOR_CONTRACT"
    assert isinstance(remaining, dict)
    assert len(remaining["proposals"]) == 1
    serialized = json.dumps((listed, decision, remaining)).lower()
    for forbidden in ("approved", "confirmation", "freeze", "verdict", "pass"):
        assert forbidden not in serialized


def test_refresh_restores_review_counts_and_define_action_after_exhaustion(
    tmp_path,
):
    with _running_server(tmp_path) as server:
        poc_id = _create_draft(server)
        _capture_source(server, poc_id)
        root = f"/api/pocs/{poc_id}/proposals"
        initial = _request(
            server,
            "GET",
            root,
            content_type=None,
            origin=None,
        )
        assert initial[0] == 200
        proposals = initial[1]["proposals"]
        assert len(proposals) == 2

        for index, (proposal, decision) in enumerate(
            zip(
                proposals,
                ("KEEP_FOR_CONTRACT", "DISCARD"),
                strict=True,
            )
        ):
            decided = _request(
                server,
                "POST",
                f"{root}/{proposal['proposal_id']}/decision",
                payload={
                    "decision": decision,
                    "reviewer": "Jayesh",
                    "rationale": "Record this bounded triage decision.",
                    "idempotency_key": f"review-before-refresh-{index}",
                },
            )
            assert decided[0] == 201

        refreshed = _request(
            server,
            "GET",
            root,
            content_type=None,
            origin=None,
        )
        javascript = _request(
            server,
            "GET",
            "/proposal_review.js",
            content_type=None,
            origin=None,
        )

    assert refreshed[0] == 200
    assert refreshed[1]["proposals"] == []
    assert refreshed[1]["review_summary"] == {
        "total": 2,
        "needs_review": 0,
        "kept_for_contract": 1,
        "discarded": 1,
    }
    assert javascript[0] == 200
    assert "review_summary.kept_for_contract" in javascript[1]
    assert "review_summary.discarded" in javascript[1]
    assert "if (pocId && keptCount === 2)" in javascript[1]
    assert "Add the missing executable requirement" in javascript[1]


def test_archived_poc_refuses_proposal_reads_and_decisions(tmp_path):
    with _running_server(tmp_path) as server:
        poc_id = _create_draft(server)
        _capture_source(server, poc_id)
        root = f"/api/pocs/{poc_id}/proposals"
        listed = _request(
            server,
            "GET",
            root,
            content_type=None,
            origin=None,
        )[1]
        assert isinstance(listed, dict)
        proposal_id = listed["proposals"][0]["proposal_id"]
        server.draft_poc_service.archive(poc_id)

        archived_list = _request(
            server,
            "GET",
            root,
            content_type=None,
            origin=None,
        )
        archived_decision = _request(
            server,
            "POST",
            f"{root}/{proposal_id}/decision",
            payload={
                "decision": "KEEP_FOR_CONTRACT",
                "reviewer": "Jayesh",
                "rationale": "This must not be accepted after archive.",
                "idempotency_key": "archived-proposal-decision",
            },
        )

    assert archived_list[:2] == archived_decision[:2] == (
        404,
        {"error": "Proposal was not found."},
    )


@pytest.mark.parametrize(
    ("content_type", "origin", "expected_status", "message"),
    (
        ("text/plain", "same", 415, "Content-Type must be application/json."),
        (None, "same", 415, "Content-Type must be application/json."),
        ("application/json", None, 403, "Origin is not allowed."),
        (
            "application/json",
            "https://evil.test",
            403,
            "Origin is not allowed.",
        ),
    ),
)
def test_proposal_writes_require_json_and_exact_origin(
    tmp_path,
    content_type,
    origin,
    expected_status,
    message,
):
    with _running_server(tmp_path) as server:
        poc_id = _create_draft(server)
        _capture_source(server, poc_id)
        listed = _request(
            server,
            "GET",
            f"/api/pocs/{poc_id}/proposals",
            content_type=None,
            origin=None,
        )[1]
        assert isinstance(listed, dict)
        proposal_id = listed["proposals"][0]["proposal_id"]
        status, response, _ = _request(
            server,
            "POST",
            f"/api/pocs/{poc_id}/proposals/{proposal_id}/decision",
            payload={
                "decision": "DISCARD",
                "reviewer": "Jayesh",
                "rationale": "Outside scope.",
                "idempotency_key": "proposal-transport-gate",
            },
            content_type=content_type,
            origin=origin,
        )

    assert status == expected_status
    assert response == {"error": message}


def test_duplicate_json_header_idempotency_and_authority_expansion_fail(
    tmp_path,
):
    with _running_server(tmp_path) as server:
        poc_id = _create_draft(server)
        _capture_source(server, poc_id)
        root = f"/api/pocs/{poc_id}/proposals"
        listed = _request(
            server,
            "GET",
            root,
            content_type=None,
            origin=None,
        )[1]
        assert isinstance(listed, dict)
        proposal_id = listed["proposals"][0]["proposal_id"]
        target = f"{root}/{proposal_id}/decision"
        duplicate = _request(
            server,
            "POST",
            target,
            raw_body=(
                b'{"decision":"DISCARD","decision":"KEEP_FOR_CONTRACT",'
                b'"reviewer":"Jayesh","rationale":"No.",'
                b'"idempotency_key":"duplicate"}'
            ),
        )
        header = _request(
            server,
            "POST",
            target,
            payload={
                "decision": "DISCARD",
                "reviewer": "Jayesh",
                "rationale": "No.",
                "idempotency_key": "body-key",
            },
            headers={"Idempotency-Key": "header-key"},
        )
        authority = _request(
            server,
            "POST",
            target,
            payload={
                "decision": "KEEP_FOR_CONTRACT",
                "reviewer": "Jayesh",
                "rationale": "No.",
                "idempotency_key": "authority-key",
                "freeze": True,
            },
        )

    assert duplicate[:2] == header[:2] == authority[:2] == (
        400,
        {"error": "Proposal review request is invalid."},
    )


def test_oversized_body_and_route_parameters_fail_closed(tmp_path):
    with _running_server(tmp_path) as server:
        poc_id = _create_draft(server)
        _capture_source(server, poc_id)
        root = f"/api/pocs/{poc_id}/proposals"
        listed = _request(
            server,
            "GET",
            root,
            content_type=None,
            origin=None,
        )[1]
        assert isinstance(listed, dict)
        proposal_id = listed["proposals"][0]["proposal_id"]
        target = f"{root}/{proposal_id}/decision"
        oversized = _request(
            server,
            "POST",
            target,
            raw_body=b"{" + b"x" * MAX_REQUEST_BYTES + b"}",
        )
        parameterized = _request(
            server,
            "POST",
            target + "?approve=true",
            payload={},
        )
        unsupported = _request(
            server,
            "PATCH",
            target,
            payload={},
        )

    assert oversized[:2] == (
        413,
        {"error": "Proposal review request is too large."},
    )
    assert parameterized[:2] == (
        400,
        {"error": "Proposal review request is invalid."},
    )
    assert unsupported[:2] == (
        405,
        {"error": "Proposal review method is not allowed."},
    )


def test_review_page_and_assets_are_served_only_on_exact_routes(tmp_path):
    with _running_server(tmp_path) as server:
        poc_id = _create_draft(server)
        page = _request(
            server,
            "GET",
            f"/app/pocs/{poc_id}/review",
            content_type=None,
            origin=None,
        )
        query = _request(
            server,
            "GET",
            f"/app/pocs/{poc_id}/review?raw=true",
            content_type=None,
            origin=None,
        )
        missing = _request(
            server,
            "GET",
            "/app/pocs/poc_missing_review/review",
            content_type=None,
            origin=None,
        )
        css = _request(
            server,
            "GET",
            "/proposal_review.css",
            content_type=None,
            origin=None,
        )
        javascript = _request(
            server,
            "GET",
            "/proposal_review.js",
            content_type=None,
            origin=None,
        )

    assert page[0] == 200
    assert page[2].startswith("text/html")
    assert "Review proposals" in str(page[1])
    assert query[:2] == (
        400,
        {"error": "Draft POC routes do not accept URL parameters."},
    )
    assert missing[0] == 404
    assert css[0] == 200
    assert css[2].startswith("text/css")
    assert javascript[0] == 200
    assert "javascript" in javascript[2]
