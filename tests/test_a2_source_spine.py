"""Train A A2 closure proof for the source-neutral local intake spine."""

from __future__ import annotations

import json
from http.client import HTTPConnection
from datetime import datetime, timezone
import threading
from pathlib import Path

import pytest

from exitspec.poc_creation import (
    DraftPOCCreateRequest,
    FirstSourceChoice,
    ProcessLocalDraftPOCService,
)
from exitspec.poc_source_demo import SourceNeutralPOCDemoServer
from exitspec.poc_source_intake import (
    POCSourceInput,
    POCSourceIntakeError,
    ProcessLocalPOCSourceIntake,
)
from exitspec.poc_sources import SourceKind


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
        page_status, page = request("GET", "/api/state")
    finally:
        server.shutdown()
        worker.join(timeout=5)
        server.server_close()

    assert status == 201
    assert capture_status == notes_status == 201
    assert source_status == proposal_status == page_status == 200
    assert sources["poc_id"] == proposals["poc_id"] == poc_id
    assert sources["sources"][0]["source_kind"] == "EMAIL"
    assert notes["source_kind"] == "DOCUMENT"
    assert proposals["proposals"][0]["review_state"] == "NEEDS_REVIEW"
    assert proposals["proposals"][0]["source_kind"] == "EMAIL"
    assert page["mode"] == "local_source_neutral"
    serialized = json.dumps((created, capture, sources, proposals, page)).lower()
    assert "poc_support_agent_demo" not in serialized
    assert "synthetic_support_agent" not in serialized


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
