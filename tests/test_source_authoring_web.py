"""Real loopback API tests; synthetic subprocesses, never provider traffic."""

from __future__ import annotations

import json
import threading
import time
from decimal import Decimal
from http.client import HTTPConnection
from types import SimpleNamespace

import pytest

from exitspec.poc_creation import DraftPOCCreateRequest, FirstSourceChoice
from exitspec.poc_proposal_review import ProposalDecision
from exitspec.poc_source_demo import SourceNeutralPOCDemoServer
from exitspec.poc_source_intake import POCSourceInput
from exitspec.poc_sources import SourceKind
from exitspec.source_authoring_supervisor import SyntheticSourceAuthoringSupervisor
from exitspec.source_authoring_web import CAPABILITY_HEADER
from exitspec.web import DemoSession, ExitSpecDemoServer
from tests.test_a2_source_spine import CONTRACT
from tests.test_source_authoring_owners import revise_source
from tests.test_workspace_closure import _binding
from tests.test_workspace_closure import _request as closure_request

POC = "poc_source_authoring_web"
TEXT = "The error rate must remain below 0.7%. Contact alice@example.com."


@pytest.fixture(params=["main", "source-neutral"])
def rig(request, tmp_path):
    server = (
        ExitSpecDemoServer(
            ("127.0.0.1", 0),
            DemoSession.synthetic_support_agent(output_root=tmp_path / "runs"),
        )
        if request.param == "main"
        else SourceNeutralPOCDemoServer(("127.0.0.1", 0))
    )
    server.draft_poc_service.create(
        DraftPOCCreateRequest(
            poc_id=POC,
            display_name="Source authoring test",
            customer_label="Synthetic",
            use_case="Review synthetic requirements",
            owner="local_operator",
            first_source_choice=FirstSourceChoice.DOCUMENT,
        ),
        idempotency_key="create",
    )
    receipt = server.poc_source_intake.capture_source(
        poc_id=POC,
        source=POCSourceInput(source_kind=SourceKind.DOCUMENT, content=TEXT),
        idempotency_key="capture",
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield SimpleNamespace(
            server=server,
            receipt=receipt,
            runtime=server.source_authoring_web,
            prefix=f"/api/pocs/{POC}/source-authoring/",
            kind=request.param,
        )
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def call(
    rig,
    action,
    payload=None,
    *,
    capability=None,
    method="POST",
    origin="same",
    host="same",
    raw=None,
    headers=(),
    path=None,
):
    server = rig.server
    connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    body = (
        json.dumps({} if payload is None else payload).encode() if raw is None else raw
    )
    try:
        connection.putrequest(
            method,
            path or rig.prefix + action,
            skip_host=True,
            skip_accept_encoding=True,
        )
        if host is not None:
            connection.putheader(
                "Host", f"127.0.0.1:{server.server_port}" if host == "same" else host
            )
        if origin is not None:
            connection.putheader(
                "Origin",
                f"http://127.0.0.1:{server.server_port}"
                if origin == "same"
                else origin,
            )
        connection.putheader("Content-Type", "application/json")
        connection.putheader("Content-Length", str(len(body)))
        if capability is not None:
            connection.putheader(CAPABILITY_HEADER, capability)
        for key, value in headers:
            connection.putheader(key, value)
        connection.endheaders(body)
        response = connection.getresponse()
        assert response.getheader("Cache-Control") == "no-store"
        assert response.getheader("Access-Control-Allow-Origin") is None
        return response.status, json.loads(response.read()), dict(response.getheaders())
    finally:
        connection.close()


def bootstrap(rig):
    status, body, _ = call(rig, "bootstrap")
    assert status == 200 and body["mode"] == "SYNTHETIC_NO_NETWORK"
    assert body["live_enabled"] is False and len(body["capability"]) == 64
    return body["capability"]


def prepare(rig, capability):
    status, body, _ = call(
        rig,
        "prepare",
        {"source_receipt_id": rig.receipt.source_receipt_id},
        capability=capability,
    )
    assert status == 200 and body["state"] == "PREPARED"
    return body


def authorize(rig, capability, operation):
    status, body, _ = call(
        rig,
        "authorize",
        {
            "operation_id": operation,
            "business_text": True,
            "acknowledged": True,
            "idempotency_key": "ack",
        },
        capability=capability,
    )
    assert status == 200 and body["state"] == "AUTHORIZED"
    return body


def wait(rig, capability, operation):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        status, body, _ = call(
            rig, "status", {"operation_id": operation}, capability=capability
        )
        assert status == 200
        if not body["processing"]:
            return body
        time.sleep(0.02)
    raise AssertionError("Synthetic operation did not finish within test bound")


def test_fractional_monotonic_clock_reaches_review_only_publication(rig):
    rig.runtime.operations._now = lambda: 1000.1
    capability = bootstrap(rig)
    operation = prepare(rig, capability)["operation_id"]
    authorize(rig, capability, operation)
    status, body, _ = call(
        rig, "run", {"operation_id": operation}, capability=capability
    )
    assert status == 200, body
    result = wait(rig, capability, operation)
    assert result["state"] == "SUCCEEDED" and result["attempts"] == 1
    proposals = rig.server.proposal_review_service.list_proposals(POC)
    assert proposals and all(row.review_state.value == "NEEDS_REVIEW" for row in proposals)


def test_api_journey_exact_preview_separate_ack_run_and_human_review(rig, monkeypatch):
    starts = []
    original = SyntheticSourceAuthoringSupervisor.prepare

    def observed(self, *args, **kwargs):
        starts.append(1)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(SyntheticSourceAuthoringSupervisor, "prepare", observed)
    capability = bootstrap(rig)
    status, listing, _ = call(rig, "sources", capability=capability)
    assert status == 200 and listing["sources"][0]["eligible"]
    preview = prepare(rig, capability)
    disclosure = preview["disclosure"]
    source = rig.server.poc_source_intake.source_snapshot(
        POC, rig.receipt.source_receipt_id
    )
    assert disclosure["redacted_text"] == source.redacted_text
    assert "alice@example.com" not in json.dumps(preview)
    operation = preview["operation_id"]
    assert (
        call(rig, "run", {"operation_id": operation}, capability=capability)[0] == 409
    )
    authorize(rig, capability, operation)
    assert (
        call(rig, "status", {"operation_id": operation}, capability=capability)[1][
            "attempts"
        ]
        == 0
    )
    assert starts == [] and rig.runtime.operations.ledger == (0, Decimal("0.00"))
    assert (
        call(rig, "run", {"operation_id": operation}, capability=capability)[0] == 200
    )
    result = wait(rig, capability, operation)
    assert result["state"] == "SUCCEEDED" and result["attempts"] == 1
    assert (
        call(rig, "run", {"operation_id": operation}, capability=capability)[1]["state"]
        == "SUCCEEDED"
    )
    assert starts == [1] and rig.runtime.operations.ledger == (1, Decimal("0.01"))
    assert capability not in json.dumps(result) and "redacted_text" not in result
    assert "source" not in vars(
        next(iter(rig.runtime._browsers[0].operations.values()))
    )
    proposals = rig.server.proposal_review_service.list_proposals(POC)
    assert len(proposals) == 1 and proposals[0].review_state.value == "NEEDS_REVIEW"
    assert proposals[0].decision is None
    rig.server.proposal_review_service.decide(
        POC,
        proposals[0].proposal_id,
        ProposalDecision.KEEP_FOR_CONTRACT,
        "named_reviewer",
        "Keep this exact requirement",
        "human-decision",
    )
    assert (
        rig.server.proposal_review_service.list_proposals(POC)[0].decision is not None
    )


@pytest.mark.parametrize(
    "origin,host,headers",
    [
        (None, "same", ()),
        ("null", "same", ()),
        ("https://example.invalid", "same", ()),
        ("same", "example.invalid:80", ()),
        ("same", None, ()),
        ("same", "same", (("Origin", "null"),)),
        ("same", "same", (("Host", "localhost:80"),)),
    ],
)
def test_bad_origin_host_cannot_consume_bootstrap_capacity(rig, origin, host, headers):
    assert call(rig, "bootstrap", origin=origin, host=host, headers=headers)[0] == 403
    assert rig.runtime._browsers == [] and rig.runtime.operations._sessions == {}
    assert rig.runtime.operations.ledger[0] == 0


@pytest.mark.parametrize(
    "raw,headers",
    [
        (b'{"network_enabled":true}', ()),
        (b'{"capability":"public-id"}', ()),
        (b'{"a":1,"a":2}', ()),
        (b"[]", ()),
        (b"{", ()),
        (b'{"x":NaN}', ()),
        (b"{}", (("Content-Length", "2"),)),
        (b"{}", (("Content-Type", "application/json"),)),
        (b"{}", (("Transfer-Encoding", "chunked"),)),
        (b"{}", (("Content-Encoding", "gzip"),)),
        (b"{}", (("Idempotency-Key", "not-a-bootstrap"),)),
        (b" " * 4097, ()),
    ],
)
def test_bad_json_and_framing_cannot_mint_capability(rig, raw, headers):
    assert call(rig, "bootstrap", raw=raw, headers=headers)[0] in {400, 413}
    assert rig.runtime._browsers == [] and rig.runtime.operations._sessions == {}


def test_capability_is_required_for_every_read_and_mutation_and_bound_to_session(rig):
    first, second = bootstrap(rig), bootstrap(rig)
    operation = prepare(rig, first)["operation_id"]
    for action, payload in [
        ("sources", {}),
        ("prepare", {"source_receipt_id": rig.receipt.source_receipt_id}),
        ("preview", {"operation_id": operation}),
        ("status", {"operation_id": operation}),
        (
            "authorize",
            {
                "operation_id": operation,
                "acknowledged": True,
                "business_text": True,
                "idempotency_key": "x",
            },
        ),
        ("run", {"operation_id": operation}),
        ("revoke", {"operation_id": operation}),
    ]:
        for bad in (None, operation, rig.receipt.source_receipt_id, "0" * 64):
            assert call(rig, action, payload, capability=bad)[0] == 403
        if action not in {"sources", "prepare"}:
            assert call(rig, action, payload, capability=second)[0] == 403
    assert (
        call(
            rig,
            "status",
            {"operation_id": operation},
            capability=first,
            headers=((CAPABILITY_HEADER, first),),
        )[0]
        == 403
    )
    assert rig.runtime.operations.ledger[0] == 0


def test_bootstrap_cap_and_new_pages_cannot_reset_global_ledger(rig):
    capabilities = [bootstrap(rig) for _ in range(16)]
    assert len(set(capabilities)) == 16
    assert call(rig, "bootstrap")[0] == 409
    assert len(rig.runtime.operations._sessions) == 16
    for action in ("reset", "renew", "grant", "live", "configure"):
        assert call(rig, action, capability=capabilities[0])[0] == 400
    assert rig.runtime.operations.ledger[0] == 0


@pytest.mark.parametrize(
    "extra",
    [
        {"model": "other"},
        {"body": "arbitrary"},
        {"credential": "PRIVATE_SENTINEL"},
        {"profile": {}},
        {"source_sha256": "0" * 64},
        {"permit": "public-id"},
    ],
)
def test_browser_cannot_supply_policy_source_body_credential_or_permit(rig, extra):
    capability = bootstrap(rig)
    payload = {"source_receipt_id": rig.receipt.source_receipt_id, **extra}
    status, response, _ = call(rig, "prepare", payload, capability=capability)
    assert status == 400 and "PRIVATE_SENTINEL" not in json.dumps(response)
    assert rig.runtime.operations._records == {}


@pytest.mark.parametrize(
    "business,acknowledged", [(False, True), (True, False), (1, True), (True, "true")]
)
def test_classification_and_acknowledgement_must_both_be_literal_true(
    rig, business, acknowledged
):
    capability = bootstrap(rig)
    operation = prepare(rig, capability)["operation_id"]
    assert (
        call(
            rig,
            "authorize",
            {
                "operation_id": operation,
                "business_text": business,
                "acknowledged": acknowledged,
                "idempotency_key": "ack",
            },
            capability=capability,
        )[0]
        == 400
    )
    assert (
        call(rig, "status", {"operation_id": operation}, capability=capability)[1][
            "state"
        ]
        == "PREPARED"
    )
    assert rig.runtime.operations.ledger[0] == 0


@pytest.mark.parametrize(
    "mutation", ["archive", "review", "expiry", "revoke", "source", "closure"]
)
def test_current_state_invalidates_consent_before_run(rig, mutation):
    capability = bootstrap(rig)
    operation = prepare(rig, capability)["operation_id"]
    authorize(rig, capability, operation)
    if mutation == "archive":
        rig.server.draft_poc_service.archive(POC)
    elif mutation == "review":
        proposal = rig.server.proposal_review_service.list_proposals(POC)[0]
        rig.server.proposal_review_service.decide(
            POC,
            proposal.proposal_id,
            ProposalDecision.DISCARD,
            "human",
            "No longer required",
            "discard",
        )
    elif mutation == "expiry":
        rig.runtime.operations._clock = lambda: time.monotonic() + 301
    elif mutation == "source":
        source = rig.server.poc_source_intake.source_snapshot(
            POC, rig.receipt.source_receipt_id
        )
        revise_source(
            SimpleNamespace(
                server=rig.server, poc_id=POC, snapshot=SimpleNamespace(source=source)
            )
        )
    elif mutation == "closure":
        binding = _binding(POC)
        rig.server.poc_closure_service._evidence_resolver = lambda _: binding
        rig.server.poc_closure_service.record(
            POC, closure_request(binding), idempotency_key="human-close"
        )
    else:
        assert (
            call(rig, "revoke", {"operation_id": operation}, capability=capability)[0]
            == 200
        )
    result = call(rig, "status", {"operation_id": operation}, capability=capability)[1]
    assert result["state"] in {"STALE", "EXPIRED", "REVOKED"}
    assert (
        call(rig, "run", {"operation_id": operation}, capability=capability)[1]["state"]
        == result["state"]
    )
    assert rig.runtime.operations.ledger[0] == 0
    assert rig.server.assisted_authoring_service._results_by_request == {}
    record = rig.runtime.operations._records[operation]
    assert record.source is None and record.body is None


def test_midflight_cancel_duplicate_run_and_shared_worker_bound(rig):
    entered, release = threading.Event(), threading.Event()
    rig.runtime.operations._schedule = lambda phase: (
        (entered.set(), release.wait(3)) if phase == "pre_dispatch" else None
    )
    capability = bootstrap(rig)
    operation = prepare(rig, capability)["operation_id"]
    authorize(rig, capability, operation)
    try:
        assert (
            call(rig, "run", {"operation_id": operation}, capability=capability)[0]
            == 200
        )
        assert entered.wait(2)
        for _ in range(3):
            assert (
                call(rig, "run", {"operation_id": operation}, capability=capability)[1][
                    "attempts"
                ]
                == 1
            )
        result = call(
            rig, "revoke", {"operation_id": operation}, capability=capability
        )[1]
        assert result["state"] == "REVOKED"
    finally:
        release.set()
    assert wait(rig, capability, operation)["state"] == "REVOKED"
    assert rig.runtime.operations.ledger == (1, Decimal("0.01"))
    assert rig.server.assisted_authoring_service._results_by_request == {}


@pytest.mark.parametrize(
    "kind,content",
    [
        ("DOCUMENT", "The throughput must exceed 100 requests per second."),
        ("EMAIL", "The budget must stay below 100 dollars."),
        ("MEETING", "Customer: The error rate must stay below 1%."),
        ("EXISTING_CONTRACT", json.dumps(CONTRACT)),
    ],
)
def test_all_four_existing_source_kinds_reach_review_only_publication(
    rig, kind, content
):
    rig.receipt = rig.server.poc_source_intake.capture_source(
        poc_id=POC,
        source=POCSourceInput(source_kind=SourceKind(kind), content=content),
        idempotency_key="second-kind",
    )
    capability = bootstrap(rig)
    preview = prepare(rig, capability)
    assert preview["disclosure"]["source_kind"] == kind
    authorize(rig, capability, preview["operation_id"])
    call(rig, "run", {"operation_id": preview["operation_id"]}, capability=capability)
    assert wait(rig, capability, preview["operation_id"])["state"] == "SUCCEEDED"
    rows = [
        row
        for row in rig.server.proposal_review_service.list_proposals(POC)
        if row.source_receipt_id == rig.receipt.source_receipt_id
    ]
    assert rows and all(
        row.decision is None and row.review_state.value == "NEEDS_REVIEW"
        for row in rows
    )


@pytest.mark.parametrize("method", ["GET", "PUT", "PATCH", "DELETE", "OPTIONS"])
def test_nonpost_methods_cannot_bootstrap_or_run(rig, method):
    assert call(rig, "bootstrap", method=method)[0] == 405
    assert rig.runtime._browsers == [] and rig.runtime.operations.ledger[0] == 0


@pytest.mark.parametrize("suffix", ["?network=true", "/", ";params", "#fragment"])
def test_nonexact_routes_cannot_mint_or_accept_authority(rig, suffix):
    assert call(rig, "bootstrap", path=rig.prefix + "bootstrap" + suffix)[0] == 400
    assert rig.runtime._browsers == []


def test_response_and_source_byte_limits_fail_with_content_free_errors(
    rig, monkeypatch
):
    capability = bootstrap(rig)
    original = rig.runtime.request
    monkeypatch.setattr(
        rig.runtime,
        "request",
        lambda *args: {"private": "PRIVATE_SENTINEL" + "x" * 262144},
    )
    status, response, _ = call(rig, "sources", capability=capability)
    assert status == 503 and response["code"] == "RESPONSE_LIMIT"
    assert "PRIVATE_SENTINEL" not in json.dumps(response)
    monkeypatch.setattr(rig.runtime, "request", original)
    text = (
        "The error rate must stay below 1%. "
        + "Bounded synthetic source context. " * 510
    )
    receipt = rig.server.poc_source_intake.capture_source(
        poc_id=POC,
        source=POCSourceInput(source_kind=SourceKind.DOCUMENT, content=text),
        idempotency_key="large-source",
    )
    assert len(text.encode()) > 16384
    status, response, _ = call(
        rig,
        "prepare",
        {"source_receipt_id": receipt.source_receipt_id},
        capability=capability,
    )
    assert status == 409 and "redacted_text" not in response
    assert (
        rig.runtime.operations.ledger[0] == 0 and rig.runtime.operations._records == {}
    )


def test_capabilities_are_poc_scoped_while_claims_share_one_global_ledger(rig):
    first = bootstrap(rig)
    operation = prepare(rig, first)["operation_id"]
    authorize(rig, first, operation)
    call(rig, "run", {"operation_id": operation}, capability=first)
    assert wait(rig, first, operation)["state"] == "SUCCEEDED"
    other_poc = "poc_source_authoring_second"
    rig.server.draft_poc_service.create(
        DraftPOCCreateRequest(
            poc_id=other_poc,
            display_name="Second POC",
            customer_label="Synthetic",
            use_case="Global ledger proof",
            owner="local_operator",
            first_source_choice=FirstSourceChoice.DOCUMENT,
        ),
        idempotency_key="second-poc",
    )
    receipt = rig.server.poc_source_intake.capture_source(
        poc_id=other_poc,
        source=POCSourceInput(
            source_kind=SourceKind.DOCUMENT,
            content="The error rate must stay below 0.8%.",
        ),
        idempotency_key="second-poc-source",
    )
    other = SimpleNamespace(
        server=rig.server,
        runtime=rig.runtime,
        receipt=receipt,
        prefix=f"/api/pocs/{other_poc}/source-authoring/",
    )
    assert call(other, "sources", capability=first)[0] == 403
    second = bootstrap(other)
    assert second != first
    rig.runtime.operations._clock = lambda: time.monotonic() + 11
    next_operation = prepare(other, second)["operation_id"]
    authorize(other, second, next_operation)
    call(other, "run", {"operation_id": next_operation}, capability=second)
    assert wait(other, second, next_operation)["state"] == "SUCCEEDED"
    assert rig.runtime.operations.ledger == (2, Decimal("0.02"))
