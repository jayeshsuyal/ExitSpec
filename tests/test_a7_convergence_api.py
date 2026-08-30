"""Focused transport regressions for A7's server-owned planning expansion."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from http.client import HTTPConnection
import json
import threading

import pytest

from exitspec.assisted_authoring import RetainedProposalProjection
from exitspec.poc_capability_planner import ProcessLocalCapabilityPlannerService
from exitspec.poc_source_demo import SourceNeutralPOCDemoServer
from exitspec.poc_sources import SourceKind


POC_ID = "poc_a7_convergence"
NOW = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)


def _proposal(suffix: str, claim: str) -> RetainedProposalProjection:
    return RetainedProposalProjection(
        schema_version="exitspec.retained-proposal-projection.v1",
        poc_id=POC_ID,
        proposal_id=f"prop_a7_{suffix}_001",
        authoring_receipt_id="arcp_" + "a" * 32,
        authoring_result_id="ares_" + "b" * 32,
        source_receipt_id="srcpt_source_a7_001",
        source_id="src_source_a7_001",
        source_kind=SourceKind.DOCUMENT,
        source_content_sha256="c" * 64,
        source_revision=1,
        source_adapter_name="exitspec_document",
        source_adapter_version="1.0.0",
        redaction_policy_version="redaction-v1",
        proposal_key=f"proposal-{suffix}",
        source_quote=claim,
        normalized_claim=claim,
        numeric_facts=None,
        reviewer="a7.source.reviewer",
        rationale="Retained for convergence planning.",
        decided_at=NOW,
    )


PROPOSALS = (
    _proposal("unsupported", "The response should be acceptable."),
    _proposal("executable", "The system must select the exact tool."),
    _proposal("excluded", "Production deployment remains excluded."),
)


@contextmanager
def _running_server():
    server = SourceNeutralPOCDemoServer(("127.0.0.1", 0))
    server.capability_planner_service = ProcessLocalCapabilityPlannerService(
        proposal_lookup=lambda poc_id: PROPOSALS if poc_id == POC_ID else (),
        clock=lambda: NOW,
    )
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield server
    finally:
        server.shutdown()
        worker.join(timeout=5)
        assert not worker.is_alive()
        server.server_close()


def _post(server, payload: dict) -> tuple[int, dict]:
    connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    try:
        body = json.dumps(payload).encode("utf-8")
        connection.request(
            "POST",
            f"/api/pocs/{POC_ID}/capability-plan/converge",
            body=body,
            headers={
                "Content-Type": "application/json",
                "Content-Length": str(len(body)),
                "Origin": f"http://127.0.0.1:{server.server_port}",
            },
        )
        response = connection.getresponse()
        return response.status, json.loads(response.read().decode("utf-8"))
    finally:
        connection.close()


def _item(
    proposal_id: str,
    *,
    scope: str,
    capability_key: str,
    operator: str | None = None,
    threshold: float | None = None,
    explicit_exclusion: bool = False,
) -> dict:
    return {
        "proposal_id": proposal_id,
        "scope": scope,
        "capability_key": capability_key,
        "operator": operator,
        "threshold": threshold,
        "reviewer": "a7.named.reviewer",
        "rationale": "Record this exact human planning decision.",
        "explicit_exclusion": explicit_exclusion,
    }


def _valid_payload() -> dict:
    return {
        "items": [
            _item(
                PROPOSALS[0].proposal_id,
                scope="ADVISORY",
                capability_key="unsupported_capability",
            ),
            _item(
                PROPOSALS[1].proposal_id,
                scope="MUST_HAVE",
                capability_key="exact_tool_selection",
                operator="GTE",
                threshold=0.95,
            ),
            _item(
                PROPOSALS[2].proposal_id,
                scope="MUST_HAVE",
                capability_key="exact_tool_selection",
                explicit_exclusion=True,
            ),
        ],
        "idempotency_key": "a7-convergence-plan",
    }


def test_convergence_route_expands_supported_authority_and_preserves_boundaries():
    with _running_server() as server:
        status, body = _post(server, _valid_payload())

    assert status == 201
    records = body["plan"]["records"]
    assert [record["disposition"] for record in records] == [
        "UNSUPPORTED",
        "EXECUTABLE",
        "UNSUPPORTED",
    ]
    assert records[0]["criterion"] is None
    assert records[2]["criterion"] is None
    assert records[2]["explicit_exclusion"] is True
    criterion = records[1]["criterion"]
    assert criterion == {
        "rule": "exact_tool_selection_rate",
        "operator": "GTE",
        "threshold": 0.95,
        "unit": "PROPORTION",
        "measurement_population": "approved_synthetic_cases",
        "evidence_method": "EXIT_SPEC_STREAMING_PROBE",
        "adapter": "deterministic_tool_selection",
        "adapter_version": "1.0.0",
        "evidence_profile": None,
        "provenance": "SOURCE_EXTRACTED",
    }


@pytest.mark.parametrize(
    "injected_field",
    (
        "adapter",
        "adapter_version",
        "evidence_profile",
        "measurement_population",
        "provenance",
        "verdict",
        "workload",
    ),
)
def test_convergence_route_rejects_browser_authority_fields(injected_field):
    payload = _valid_payload()
    payload["items"][1][injected_field] = "caller-controlled"
    with _running_server() as server:
        status, body = _post(server, payload)

    assert status == 400
    assert body == {"error": "Convergence planning request is invalid."}


@pytest.mark.parametrize("mutation", ("missing", "extra", "bad_operator"))
def test_convergence_route_requires_exact_human_fields(mutation):
    payload = _valid_payload()
    if mutation == "missing":
        del payload["items"][1]["reviewer"]
    elif mutation == "extra":
        payload["unexpected"] = True
    else:
        payload["items"][1]["operator"] = "LT"
    with _running_server() as server:
        status, body = _post(server, payload)

    assert status == 400
    assert body == {"error": "Convergence planning request is invalid."}
