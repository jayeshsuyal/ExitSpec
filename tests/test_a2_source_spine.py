"""Train A A2 closure proof for the source-neutral local intake spine."""

from __future__ import annotations

import json
from http.client import HTTPConnection, HTTPResponse
from datetime import datetime, timezone
import threading
from pathlib import Path
import socket

import pytest

from exitspec.poc_creation import (
    DraftPOCCreateRequest,
    FirstSourceChoice,
    ProcessLocalDraftPOCService,
)
import exitspec.cli as cli
from exitspec.poc_source_demo import SourceNeutralPOCDemoServer
from exitspec.poc_source_intake import (
    POCSourceInput,
    POCSourceIntakeError,
    ProcessLocalPOCSourceIntake,
)
from exitspec.poc_sources import SourceKind
from exitspec.workspace import WorkspaceSourceType


CONTRACT = {
    "id": "customer-contract",
    "version": "1.0.0",
    "status": "DRAFT",
    "created_at": "2026-08-28T00:00:00Z",
    "customer": "Example customer",
    "use_case": "Validate a bounded customer requirement.",
    "target_system": {
        "provider": "local",
        "endpoint_class": "mock",
        "model": "example-v1",
    },
    "workload": {
        "fixture_path": "local-fixture",
        "sha256": "a" * 64,
    },
    "criteria": [
        {
            "id": "REQ-001",
            "title": "Bound latency",
            "must_have": True,
            "source": {
                "speaker": "customer",
                "quote": "Latency must stay below 500 ms.",
                "location": "existing contract",
            },
            "human_added": False,
            "normalized_claim": "Latency must stay below 500 ms.",
            "metric": "exact_tool_selection_rate",
            "unit": "proportion",
            "aggregation": "exact-match proportion",
            "rule": {
                "operator": "gte",
                "threshold": 0.95,
                "minimum_samples": 10,
                "confidence_level": 0.95,
                "confidence_method": "wilson_two_sided_lower_bound",
            },
            "workload_slice": "example",
            "adapter": "example",
            "adapter_version": "1.0.0",
            "owner": "field_engineer",
            "evidence_policy": "Local evidence only.",
            "approved": False,
        }
    ],
    "owners": ["field_engineer"],
    "non_goals": ["Production authorization."],
    "evidence_retention_policy": "Local only.",
}


def _drafts(*poc_ids: str) -> ProcessLocalDraftPOCService:
    drafts = ProcessLocalDraftPOCService(
        max_drafts=max(1, len(poc_ids)),
        clock=lambda: datetime(2026, 8, 28, tzinfo=timezone.utc),
    )
    for number, poc_id in enumerate(poc_ids, start=1):
        drafts.create(
            DraftPOCCreateRequest(
                poc_id=poc_id,
                display_name="Customer POC",
                customer_label="Example customer",
                use_case="Validate bounded requirements.",
                owner="field_engineer",
                first_source_choice=FirstSourceChoice.DOCUMENT,
            ),
            idempotency_key=f"create-{number}",
        )
    return drafts


def test_one_typed_spine_accepts_all_kinds_and_notes_is_document_alias():
    drafts = _drafts("poc_a2_alpha")
    intake = ProcessLocalPOCSourceIntake(draft_lookup=drafts.get)
    values = {
        SourceKind.EMAIL: "P95 latency must stay below 500 ms.",
        SourceKind.MEETING: "Customer: Error rate must remain below 1%.",
        SourceKind.DOCUMENT: "The throughput must exceed 100 requests per second.",
        SourceKind.EXISTING_CONTRACT: json.dumps(CONTRACT),
    }

    receipts = tuple(
        intake.capture_source(
            poc_id="poc_a2_alpha",
            source=POCSourceInput(source_kind=kind, content=value),
            idempotency_key=f"capture-{kind.value}",
        )
        for kind, value in values.items()
    )
    assert tuple(receipt.source_kind for receipt in receipts) == tuple(values)
    assert all(receipt.poc_id == "poc_a2_alpha" for receipt in receipts)
    assert all(receipt.status == "NEEDS_REVIEW" for receipt in receipts)
    assert all("approved" not in receipt.model_dump_json().lower() for receipt in receipts)

    notes = intake.capture_source(
        poc_id="poc_a2_alpha",
        source=POCSourceInput(
            source_kind=SourceKind.DOCUMENT,
            content="Notes: the budget must stay below 100 dollars.",
        ),
        idempotency_key="capture-notes",
    )
    assert notes.source_kind is SourceKind.DOCUMENT
    assert {item.source_kind for item in intake.proposal_inputs("poc_a2_alpha")} == set(values)
    assert all(item.state.value == "NEEDS_REVIEW" for item in intake.proposal_inputs("poc_a2_alpha"))


def test_source_neutral_http_runtime_has_no_seeded_dependency_and_preserves_poc_id(tmp_path: Path):
    server = SourceNeutralPOCDemoServer(("127.0.0.1", 0))
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()

    def request(method: str, target: str, payload: dict | None = None) -> tuple[int, dict]:
        connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        try:
            body = None if payload is None else json.dumps(payload).encode()
            headers = {}
            if body is not None:
                headers = {
                    "Content-Type": "application/json",
                    "Origin": f"http://127.0.0.1:{server.server_port}",
                }
            connection.request(method, target, body=body, headers=headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read().decode())
        finally:
            connection.close()

    try:
        status, created = request(
            "POST",
            "/api/pocs",
            {
                "display_name": "Dynamic customer POC",
                "customer_label": "Northstar",
                "use_case": "Validate source convergence.",
                "owner": "field_engineer",
                "first_source_choice": "EMAIL",
                "idempotency_key": "create-dynamic-a2",
            },
        )
        poc_id = created["poc_id"]
        capture_status, capture = request(
            "POST",
            f"/api/pocs/{poc_id}/sources/email-text",
            {
                "email_text": "The p95 latency must stay below 500 ms.",
                "idempotency_key": "capture-dynamic-a2",
            },
        )
        notes_status, notes = request(
            "POST",
            f"/api/pocs/{poc_id}/sources/notes",
            {
                "document_text": "Notes: the budget must stay below 100 dollars.",
                "idempotency_key": "capture-dynamic-notes-a2",
            },
        )
        source_status, sources = request(
            "GET", f"/api/pocs/{poc_id}/sources"
        )
        proposal_status, proposals = request(
            "GET", f"/api/pocs/{poc_id}/proposals"
        )
        workspace_status, workspace = request("GET", "/api/workspace")
        page_status, page = request("GET", "/api/state")
    finally:
        server.shutdown()
        worker.join(timeout=5)
        server.server_close()

    assert status == 201
    assert capture_status == notes_status == 201
    assert source_status == proposal_status == workspace_status == page_status == 200
    assert sources["poc_id"] == proposals["poc_id"] == poc_id
    assert sources["sources"][0]["source_kind"] == "EMAIL"
    assert notes["source_kind"] == "DOCUMENT"
    assert proposals["proposals"][0]["review_state"] == "NEEDS_REVIEW"
    assert proposals["proposals"][0]["source_kind"] == "EMAIL"
    source_types = workspace["continue_working"]["source_summary"]["types"]
    assert "note" not in source_types
    assert "document" in source_types
    assert page["mode"] == "local_source_neutral"
    serialized = json.dumps((created, capture, sources, proposals, page)).lower()
    assert "poc_support_agent_demo" not in serialized
    assert "synthetic_support_agent" not in serialized


@pytest.mark.parametrize(
    ("first_source", "route", "field", "value", "expected_kind"),
    (
        ("EMAIL", "email-text", "email_text", "The p95 latency must stay below 500 ms.", "EMAIL"),
        ("MEETING", "meeting", "transcript_text", "Customer: The error rate must remain below 1%.", "MEETING"),
        ("DOCUMENT", "document", "document_text", "The throughput must exceed 100 requests per second.", "DOCUMENT"),
        ("DOCUMENT", "notes", "document_text", "Notes: The budget must stay below 100 dollars.", "DOCUMENT"),
        ("EXISTING_CONTRACT", "contract", "contract_json", json.dumps(CONTRACT), "EXISTING_CONTRACT"),
    ),
)
def test_source_neutral_http_runtime_accepts_each_kind_on_a_fresh_dynamic_poc(
    first_source, route, field, value, expected_kind
):
    server = SourceNeutralPOCDemoServer(("127.0.0.1", 0))
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()

    def request(method: str, target: str, payload: dict | None = None) -> tuple[int, dict]:
        connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        try:
            body = None if payload is None else json.dumps(payload).encode()
            headers = {}
            if body is not None:
                headers = {
                    "Content-Type": "application/json",
                    "Origin": f"http://127.0.0.1:{server.server_port}",
                }
            connection.request(method, target, body=body, headers=headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read().decode())
        finally:
            connection.close()

    try:
        create_status, created = request(
            "POST",
            "/api/pocs",
            {
                "display_name": "Fresh dynamic source POC",
                "customer_label": "Northstar",
                "use_case": "Validate each source adapter through one spine.",
                "owner": "field_engineer",
                "first_source_choice": first_source,
                "idempotency_key": f"fresh-create-{route}",
            },
        )
        poc_id = created["poc_id"]
        capture_status, receipt = request(
            "POST",
            f"/api/pocs/{poc_id}/sources/{route}",
            {field: value, "idempotency_key": f"fresh-capture-{route}"},
        )
        proposal_status, proposals = request(
            "GET", f"/api/pocs/{poc_id}/proposals"
        )
        assert create_status == 201
        assert capture_status == 201
        assert proposal_status == 200
        assert receipt["poc_id"] == proposals["poc_id"] == poc_id
        assert receipt["source_kind"] == expected_kind
        assert proposals["proposals"][0]["source_kind"] == expected_kind
        assert proposals["proposals"][0]["review_state"] == "NEEDS_REVIEW"
    finally:
        server.shutdown()
        worker.join(timeout=5)
        server.server_close()


@pytest.mark.parametrize(
    "content",
    ("bad\x00input", "bad\tinput", "x" * 20_001),
)
def test_typed_spine_rejects_unsafe_or_oversized_input_without_source_write(content: str):
    drafts = _drafts("poc_a2_safe")
    intake = ProcessLocalPOCSourceIntake(draft_lookup=drafts.get)
    with pytest.raises((ValueError, POCSourceIntakeError)):
        intake.capture_source(
            poc_id="poc_a2_safe",
            source=POCSourceInput(source_kind=SourceKind.DOCUMENT, content=content),
            idempotency_key="unsafe-a2",
        )
    assert intake.list_receipts("poc_a2_safe") == ()


def test_a2_module_does_not_name_the_seeded_support_agent():
    source = Path(__file__).parents[1] / "src" / "exitspec" / "poc_source_demo.py"
    text = source.read_text(encoding="utf-8")
    assert "DemoSession" not in text
    assert "poc_support_agent_demo" not in text
    assert "support_agent" not in text


def test_crlf_is_normalized_and_exact_line_ending_replay_does_not_fork_identity():
    source = POCSourceInput(
        source_kind=SourceKind.MEETING,
        content="Customer:\r\np95 latency must stay below 500 ms.\rDone.",
    )
    assert source.content == "Customer:\np95 latency must stay below 500 ms.\nDone."

    drafts = _drafts("poc_a2_line_endings")
    intake = ProcessLocalPOCSourceIntake(draft_lookup=drafts.get)
    first = intake.capture_source(
        poc_id="poc_a2_line_endings",
        source=POCSourceInput(
            source_kind=SourceKind.MEETING,
            content="Customer: p95 latency must stay below 500 ms.\r\nCustomer: The error rate must remain below 1%.",
        ),
        idempotency_key="line-ending-replay",
    )
    replay = intake.capture_source(
        poc_id="poc_a2_line_endings",
        source=POCSourceInput(
            source_kind=SourceKind.MEETING,
            content="Customer: p95 latency must stay below 500 ms.\nCustomer: The error rate must remain below 1%.",
        ),
        idempotency_key="line-ending-replay",
    )
    assert first.source_receipt_id == replay.source_receipt_id
    assert replay.idempotent_replay is True
    assert len(intake.list_receipts("poc_a2_line_endings")) == 1


def test_http_crlf_is_accepted_and_exact_line_ending_replay_has_one_receipt():
    server = SourceNeutralPOCDemoServer(("127.0.0.1", 0))
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()

    def request(content: str) -> tuple[int, dict]:
        payload = {
            "transcript_text": content,
            "idempotency_key": "http-line-ending-replay",
        }
        body = json.dumps(payload).encode()
        connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        try:
            connection.request(
                "POST",
                "/api/pocs/poc_a2_http_lines/sources/meeting",
                body=body,
                headers={
                    "Content-Type": "application/json",
                    "Origin": f"http://127.0.0.1:{server.server_port}",
                },
            )
            response = connection.getresponse()
            return response.status, json.loads(response.read().decode())
        finally:
            connection.close()

    try:
        server.draft_poc_service.create(
            DraftPOCCreateRequest(
                poc_id="poc_a2_http_lines",
                display_name="HTTP line endings",
                customer_label="Northstar",
                use_case="Check normalized replay.",
                owner="field_engineer",
                first_source_choice=FirstSourceChoice.MEETING,
            ),
            idempotency_key="http-lines-create",
        )
        first_status, first = request(
            "Customer: p95 latency must stay below 500 ms.\r\nCustomer: Error rate must remain below 1%."
        )
        replay_status, replay = request(
            "Customer: p95 latency must stay below 500 ms.\nCustomer: Error rate must remain below 1%."
        )
        assert first_status == 201
        assert replay_status == 200
        assert replay["idempotent_replay"] is True
        assert first["source_receipt_id"] == replay["source_receipt_id"]
        assert len(server.poc_source_intake._source_service.snapshots("poc_a2_http_lines")) == 1
    finally:
        server.shutdown()
        worker.join(timeout=5)
        server.server_close()


def _raw_write(
    server: SourceNeutralPOCDemoServer,
    headers: list[tuple[str, str]],
    *,
    payload: dict | None = None,
) -> tuple[int, object]:
    body = json.dumps(
        payload
        or {
            "display_name": "Raw header POC",
            "customer_label": "Northstar",
            "use_case": "Check raw transport fail-closed behavior.",
            "owner": "field_engineer",
            "first_source_choice": "DOCUMENT",
            "idempotency_key": "raw-header-create",
        }
    ).encode()
    request_headers = list(headers)
    request_headers.append(("Content-Length", str(len(body))))
    request = b"POST /api/pocs HTTP/1.1\r\n" + b"".join(
        (name.encode() + b": " + value.encode() + b"\r\n")
        for name, value in request_headers
    ) + b"Connection: close\r\n\r\n" + body
    connection = socket.create_connection(("127.0.0.1", server.server_port), timeout=5)
    try:
        connection.sendall(request)
        response = HTTPResponse(connection)
        response.begin()
        raw = response.read().decode("utf-8", errors="replace")
        try:
            return response.status, json.loads(raw)
        except json.JSONDecodeError:
            return response.status, raw
    finally:
        connection.close()


@pytest.mark.parametrize(
    ("header_mutation", "expected_status"),
    (
        (lambda headers, port: headers + [("Origin", "https://evil.example")], 403),
        (lambda headers, port: headers + [("Content-Type", "text/plain")], 415),
        (lambda headers, port: headers + [("Host", "evil.example")], 403),
        (lambda headers, port: [("Host", "evil.example")] + headers[1:], 403),
        (lambda headers, port: [item for item in headers if item[0] != "Host"], 403),
        (lambda headers, port: [item for item in headers if item[0] != "Origin"], 403),
        (lambda headers, port: headers[:1] + [("Origin", "http://[bad")] + headers[2:], 403),
        (lambda headers, port: headers[:1] + [("Origin", "https://127.0.0.1:{0}".format(port))] + headers[2:], 403),
        (lambda headers, port: headers[:1] + [("Content-Type", "text/plain")] + headers[2:], 415),
    ),
)
def test_raw_write_headers_fail_closed_before_any_poc_write(header_mutation, expected_status):
    server = SourceNeutralPOCDemoServer(("127.0.0.1", 0))
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        base_headers = [
            ("Host", "127.0.0.1:{0}".format(server.server_port)),
            ("Origin", "http://127.0.0.1:{0}".format(server.server_port)),
            ("Content-Type", "application/json"),
        ]
        status, _ = _raw_write(
            server,
            header_mutation(base_headers, server.server_port),
        )
        assert status == expected_status
        assert len(server.draft_poc_service) == 0
    finally:
        server.shutdown()
        worker.join(timeout=5)
        server.server_close()


def test_missing_and_archived_pocs_are_not_written_and_use_distinct_statuses():
    server = SourceNeutralPOCDemoServer(("127.0.0.1", 0))
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()

    def request(method: str, target: str, payload: dict | None = None) -> tuple[int, dict]:
        connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        try:
            body = None if payload is None else json.dumps(payload).encode()
            headers = {}
            if body is not None:
                headers = {
                    "Content-Type": "application/json",
                    "Origin": f"http://127.0.0.1:{server.server_port}",
                }
            connection.request(method, target, body=body, headers=headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read().decode())
        finally:
            connection.close()

    try:
        _, created = request(
            "POST",
            "/api/pocs",
            {
                "display_name": "Archived POC",
                "customer_label": "Northstar",
                "use_case": "Check archived source rejection.",
                "owner": "field_engineer",
                "first_source_choice": "DOCUMENT",
                "idempotency_key": "archived-create",
            },
        )
        poc_id = created["poc_id"]
        server.draft_poc_service.archive(poc_id)
        archived_status, _ = request(
            "POST",
            f"/api/pocs/{poc_id}/sources/document",
            {"document_text": "The budget must stay below 100 dollars.", "idempotency_key": "archived-source"},
        )
        missing_status, _ = request(
            "POST",
            "/api/pocs/poc_a2_missing/sources/document",
            {"document_text": "The budget must stay below 100 dollars.", "idempotency_key": "missing-source"},
        )
        assert archived_status == 409
        assert missing_status == 404
        assert server.poc_source_intake._source_service.snapshots(poc_id) == ()
    finally:
        server.shutdown()
        worker.join(timeout=5)
        server.server_close()


def test_a2_closure_evidence_is_bounded_and_references_executable_tests():
    path = Path(__file__).parents[1] / "examples" / "product" / "request-to-proof-a2-closure-evidence-v1.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert set(payload) == {"schema_version", "train_slice", "status", "scope", "claims", "authority_boundary", "limitations", "frozen_baseline_edited"}
    assert payload["schema_version"] == "exitspec.request-to-proof-a2-closure-evidence.v1"
    assert payload["train_slice"] == "A2"
    assert payload["status"] == "IMPLEMENTED_AND_TESTED"
    assert payload["scope"] == "GL-01 and GL-02 only"
    assert set(payload["claims"]) == {"GL-01", "GL-02"}
    assert set(payload["authority_boundary"]) == {
        "source_authority",
        "proposal_state",
        "may_approve",
        "may_confirm",
        "may_freeze",
        "may_execute",
        "may_issue_evidence",
        "may_issue_verdict",
    }
    assert payload["authority_boundary"]["source_authority"] == "UNTRUSTED_SOURCE_ONLY"
    assert payload["authority_boundary"]["proposal_state"] == "NEEDS_REVIEW"
    assert all(value is False for key, value in payload["authority_boundary"].items() if key.startswith("may_"))
    assert isinstance(payload["limitations"], list) and payload["limitations"]
    for claim in payload["claims"].values():
        assert set(claim) == {"statement", "proof"}
        assert claim["proof"]
        for reference in claim["proof"]:
            file_name, node = reference.split("::", 1)
            test_path = Path(__file__).parents[1] / file_name
            assert test_path.is_file()
            assert f"def {node}(" in test_path.read_text(encoding="utf-8")
    assert payload["frozen_baseline_edited"] is False
    assert len(path.read_bytes()) < 8_192


def test_a2_preserves_note_compatibility_identifier_but_never_emits_it():
    assert WorkspaceSourceType("note") is WorkspaceSourceType.NOTE
    assert WorkspaceSourceType.NOTE.value == "note"
    assert WorkspaceSourceType.DOCUMENT.value == "document"


@pytest.mark.parametrize(
    "incompatible",
    (
        ("--enable-fireworks",),
        ("--enable-fireworks-stt",),
        ("--inferdrome-runs-root", "/tmp/runs"),
    ),
)
def test_source_neutral_cli_rejects_incompatible_provider_or_import_flags(incompatible):
    with pytest.raises(ValueError, match="--source-neutral cannot be combined"):
        cli.main(["serve", "--source-neutral", *incompatible])


def test_source_neutral_cli_keeps_open_browser_valid(monkeypatch, capsys):
    calls = {}

    class StubServer:
        server_port = 9876

        def serve_forever(self):
            raise KeyboardInterrupt

        def server_close(self):
            calls["closed"] = True

    def fake_serve(**kwargs):
        calls.update(kwargs)
        return StubServer()

    monkeypatch.setattr(cli, "serve_source_neutral_demo", fake_serve)
    assert cli.main(["serve", "--source-neutral", "--open-browser"]) == 0
    assert calls == {"host": "127.0.0.1", "port": 8765, "open_browser": True, "closed": True}
    output = capsys.readouterr().out
    assert "v0.3 source-neutral Request-to-Proof" in output
    assert "http://127.0.0.1:9876/app" in output
