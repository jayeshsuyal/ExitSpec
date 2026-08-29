"""Train A A3 proof for source-neutral assisted authoring and human triage."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from http.client import HTTPConnection
import json
from pathlib import Path
import threading

import pytest

from exitspec.assisted_authoring import (
    ASSISTED_AUTHORING_MODEL,
    AssistedAuthoringError,
    NumericProposalMaterial,
    ProcessLocalAssistedAuthoringService,
    SourceNeutralProposalBatch,
)
from exitspec.poc_creation import (
    DraftPOCCreateRequest,
    FirstSourceChoice,
    ProcessLocalDraftPOCService,
)
from exitspec.poc_assisted_authoring_web_api import (
    handle_poc_assisted_authoring_web_api_request,
)
from exitspec.poc_proposal_review import (
    ProcessLocalProposalReviewService,
    ProposalDecision,
    ProposalReviewState,
)
from exitspec.poc_source_intake import (
    POCSourceInput,
    ProcessLocalPOCSourceIntake,
)
from exitspec.poc_sources import SourceKind
from exitspec.providers import (
    ProviderError,
    ProviderErrorCode,
    ProviderReceipt,
    StructuredJSONResult,
)
from exitspec.synthetic_assisted_authoring import (
    SyntheticSourceNeutralAssistedAuthoringExecutor,
)
from exitspec.poc_source_demo import SourceNeutralPOCDemoServer
from exitspec.poc_source_intake import POCSourceIntakeRevisionRequired
from tests.test_a2_source_spine import CONTRACT


NOW = datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc)
RAW_EMAIL = "customer.a3@example.com"
RAW_TOKEN = "fw_a3_private_token_123456789"


def _drafts(*poc_ids: str) -> ProcessLocalDraftPOCService:
    drafts = ProcessLocalDraftPOCService(
        max_drafts=max(1, len(poc_ids)),
        clock=lambda: NOW,
    )
    for ordinal, poc_id in enumerate(poc_ids, start=1):
        drafts.create(
            DraftPOCCreateRequest(
                poc_id=poc_id,
                display_name="A3 dynamic POC",
                customer_label="A3 customer",
                use_case="Validate source-bound proposal material.",
                owner="field_engineer",
                first_source_choice=FirstSourceChoice.DOCUMENT,
            ),
            idempotency_key="create-{0}".format(ordinal),
        )
    return drafts


def _runtime(
    *poc_ids: str,
    executor: object | None = None,
) -> tuple[
    ProcessLocalDraftPOCService,
    ProcessLocalPOCSourceIntake,
    ProcessLocalAssistedAuthoringService,
]:
    drafts = _drafts(*poc_ids)
    intake = ProcessLocalPOCSourceIntake(
        draft_lookup=drafts.get,
        clock=lambda: NOW,
    )
    service = ProcessLocalAssistedAuthoringService(
        source_lookup=intake.source_snapshot,
        executor=(
            SyntheticSourceNeutralAssistedAuthoringExecutor()
            if executor is None
            else executor
        ),
        clock=lambda: NOW,
    )
    return drafts, intake, service


def _attach_document(
    intake: ProcessLocalPOCSourceIntake,
    poc_id: str,
    text: str = "The error rate must remain below 1%.",
    key: str = "capture-a3",
) -> str:
    receipt = intake.capture_source(
        poc_id=poc_id,
        source=POCSourceInput(source_kind=SourceKind.DOCUMENT, content=text),
        idempotency_key=key,
    )
    return receipt.source_receipt_id


def _safe_provider_receipt() -> ProviderReceipt:
    return ProviderReceipt(
        provider="test-provider",
        model=ASSISTED_AUTHORING_MODEL,
        endpoint="local://test-provider/a3",
        attempts=1,
        latency_ms=0.0,
        input_tokens=10,
        output_tokens=10,
        total_tokens=20,
        provider_request_id=None,
        estimated_cost_usd=None,
        pricing_version=None,
    )


class PayloadExecutor:
    def __init__(self, payload: object) -> None:
        self.payload = payload
        self.requests = []

    def execute(self, request):
        self.requests.append(request)
        return StructuredJSONResult(
            output=self.payload,
            receipt=_safe_provider_receipt(),
        )


class FailureExecutor:
    def __init__(self, error: Exception) -> None:
        self.error = error
        self.requests = []

    def execute(self, request):
        self.requests.append(request)
        raise self.error


def _valid_payload(quote: str = "The error rate must remain below 1%.") -> dict:
    return {
        "schema_version": "exitspec.assisted-authoring-output.v1",
        "proposals": [
            {
                "proposal_key": "proposal-001",
                "source_quote": quote,
                "normalized_claim": quote,
                "numeric_facts": {"threshold": 0.01},
            }
        ],
    }


def test_a3_source_neutral_action_binds_exact_source_and_requires_named_human_triage():
    _, intake, service = _runtime("poc_a3_review")
    source_receipt_id = _attach_document(intake, "poc_a3_review")

    result = service.create_assisted_draft(
        poc_id="poc_a3_review",
        source_receipt_id=source_receipt_id,
        idempotency_key="author-a3",
    )
    proposal = result.proposals[0]
    assert result.receipt.status == "NEEDS_REVIEW"
    assert proposal.review_state == "NEEDS_REVIEW"
    assert proposal.source_receipt_id == source_receipt_id
    assert proposal.source_quote == "The error rate must remain below 1%."
    assert proposal.numeric_facts == NumericProposalMaterial(threshold=0.01)

    review = ProcessLocalProposalReviewService(
        proposal_lookup=service.proposal_inputs,
        clock=lambda: NOW,
    )
    before = review.list_proposals("poc_a3_review")
    assert before[0].review_state is ProposalReviewState.NEEDS_REVIEW
    assert before[0].decision is None

    decision = review.decide(
        "poc_a3_review",
        proposal.proposal_id,
        ProposalDecision.KEEP_FOR_CONTRACT,
        "A named employee",
        "Retain this source-bound material for the later A4 authoring step.",
        "decision-a3",
    )
    assert decision.receipt.decision is ProposalDecision.KEEP_FOR_CONTRACT
    retained = service.retained_projection("poc_a3_review", review)
    assert len(retained) == 1
    assert retained[0].retention_state == "KEEP_FOR_CONTRACT"
    assert retained[0].reviewer == "A named employee"
    assert retained[0].source_quote == proposal.source_quote
    authority_names = {
        "criterion",
        "approved",
        "approval",
        "confirmation",
        "freeze",
        "verdict",
        "evidence_method",
        "execute",
        "deployment",
    }
    assert authority_names.isdisjoint(retained[0].model_dump())
    assert service.semantics.keep_is_contract_approval is False
    assert service.semantics.can_create_criterion is False
    assert service.semantics.can_issue_verdict is False


def test_a3_redacts_before_provider_and_public_receipts_are_content_free():
    executor = PayloadExecutor(_valid_payload())
    _, intake, service = _runtime("poc_a3_redaction", executor=executor)
    source_receipt_id = _attach_document(
        intake,
        "poc_a3_redaction",
        "The error rate must remain below 1%. Contact {0}; token {1}.".format(
            RAW_EMAIL,
            RAW_TOKEN,
        ),
    )
    result = service.create_assisted_draft(
        poc_id="poc_a3_redaction",
        source_receipt_id=source_receipt_id,
        idempotency_key="author-redaction",
    )
    outbound = "\n".join(message.content for message in executor.requests[0].messages)
    assert RAW_EMAIL not in outbound
    assert RAW_TOKEN not in outbound
    assert RAW_EMAIL not in result.receipt.model_dump_json()
    assert RAW_TOKEN not in result.receipt.model_dump_json()
    assert RAW_EMAIL not in result.proposals[0].model_dump_json()
    assert RAW_TOKEN not in result.proposals[0].model_dump_json()


@pytest.mark.parametrize(
    "payload",
    (
        {
            "schema_version": "exitspec.assisted-authoring-output.v1",
            "proposals": [],
        },
        {
            "schema_version": "exitspec.assisted-authoring-output.v1",
            "criterion": "approve this",
            "proposals": [
                {
                    "proposal_key": "proposal-001",
                    "source_quote": "The error rate must remain below 1%.",
                    "normalized_claim": "The error rate must remain below 1%.",
                    "numeric_facts": {"threshold": 0.01},
                }
            ],
        },
        {
            "schema_version": "exitspec.assisted-authoring-output.v1",
            "proposals": [
                {
                    "proposal_key": "proposal-001",
                    "source_quote": "The invented requirement must be approved now.",
                    "normalized_claim": "The invented requirement must be approved now.",
                }
            ],
        },
        {
            "schema_version": "exitspec.assisted-authoring-output.v1",
            "proposals": [
                {
                    "proposal_key": "proposal-001",
                    "source_quote": "The error rate must remain below 1%.",
                    "normalized_claim": "The error rate must remain below 1%.",
                    "numeric_facts": {"threshold": 0.02},
                }
            ],
        },
    ),
)
def test_a3_untrusted_schema_authority_anchor_and_numeric_failures_create_nothing(payload):
    executor = PayloadExecutor(payload)
    _, intake, service = _runtime("poc_a3_reject", executor=executor)
    source_receipt_id = _attach_document(intake, "poc_a3_reject")

    with pytest.raises(AssistedAuthoringError) as caught:
        service.create_assisted_draft(
            poc_id="poc_a3_reject",
            source_receipt_id=source_receipt_id,
            idempotency_key="author-reject",
        )
    assert caught.value.code in {
        "invalid_output",
        "source_link_violation",
        "numeric_source_mismatch",
        "authority_injection",
        "ambiguous_output",
    }
    assert service.list_receipts("poc_a3_reject") == ()
    assert service.proposal_inputs("poc_a3_reject") == ()


def test_a3_duplicate_identity_nonfinite_and_no_output_provider_fail_closed():
    duplicate_payload = _valid_payload()
    duplicate_payload["proposals"].append(
        duplicate_payload["proposals"][0] | {"proposal_key": "PROPOSAL-001"}
    )
    for payload in (
        duplicate_payload,
        _valid_payload() | {"proposals": [{**_valid_payload()["proposals"][0], "numeric_facts": {"threshold": float("nan")}}]},
    ):
        executor = PayloadExecutor(payload)
        _, intake, service = _runtime("poc_a3_duplicate", executor=executor)
        source_receipt_id = _attach_document(intake, "poc_a3_duplicate", key="capture-{0}".format(id(payload)))
        with pytest.raises(AssistedAuthoringError):
            service.create_assisted_draft(
                poc_id="poc_a3_duplicate",
                source_receipt_id=source_receipt_id,
                idempotency_key="author-{0}".format(id(payload)),
            )
        assert service.list_receipts("poc_a3_duplicate") == ()

    failing = FailureExecutor(
        ProviderError(
            ProviderErrorCode.TIMEOUT,
            "provider failure with {0}".format(RAW_TOKEN),
            retryable=True,
            attempts=2,
        )
    )
    _, intake, service = _runtime("poc_a3_provider_failure", executor=failing)
    source_receipt_id = _attach_document(intake, "poc_a3_provider_failure")
    with pytest.raises(AssistedAuthoringError) as caught:
        service.create_assisted_draft(
            poc_id="poc_a3_provider_failure",
            source_receipt_id=source_receipt_id,
            idempotency_key="author-provider-failure",
        )
    assert RAW_TOKEN not in str(caught.value)
    assert service.list_receipts("poc_a3_provider_failure") == ()


def test_a3_replay_conflict_isolation_and_concurrent_same_key_are_immutable():
    _, intake, service = _runtime("poc_a3_one", "poc_a3_two")
    one_source = _attach_document(intake, "poc_a3_one", key="capture-one")
    two_source = _attach_document(intake, "poc_a3_two", key="capture-two")
    first = service.create_assisted_draft(
        poc_id="poc_a3_one",
        source_receipt_id=one_source,
        idempotency_key="same-author-key",
    )
    # The exact request is replayed with the same receipt and proposal IDs.
    replay = service.create_assisted_draft(
        poc_id="poc_a3_one",
        source_receipt_id=one_source,
        idempotency_key="same-author-key",
    )
    assert replay.receipt.authoring_receipt_id == first.receipt.authoring_receipt_id
    assert replay.receipt.idempotent_replay is True

    with pytest.raises(AssistedAuthoringError) as cross_poc:
        service.create_assisted_draft(
            poc_id="poc_a3_two",
            source_receipt_id=two_source,
            idempotency_key="same-author-key",
        )
    assert cross_poc.value.code == "idempotency_conflict"

    results = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [
            pool.submit(
                service.create_assisted_draft,
                poc_id="poc_a3_two",
                source_receipt_id=two_source,
                idempotency_key="concurrent-author-key",
            )
            for _ in range(8)
        ]
        results = [future.result() for future in futures]
    assert len({result.receipt.authoring_receipt_id for result in results}) == 1
    assert len({result.proposals[0].proposal_id for result in results}) == 1


def test_a3_review_api_reuses_human_decision_and_retained_projection():
    _, intake, service = _runtime("poc_a3_api")
    source_receipt_id = _attach_document(intake, "poc_a3_api")
    response = handle_poc_assisted_authoring_web_api_request(
        method="POST",
        target="/api/pocs/poc_a3_api/sources/{0}/assisted-authoring".format(
            source_receipt_id
        ),
        payload={"idempotency_key": "api-author-a3"},
        runtime=service,
    )
    assert response is not None and response.status == 201
    assert set(response.payload) == {"authoring_receipt", "proposals"}
    receipt = response.payload["authoring_receipt"]
    proposal = response.payload["proposals"][0]
    assert receipt["status"] == "NEEDS_REVIEW"
    assert proposal["review_state"] == "NEEDS_REVIEW"

    review = ProcessLocalProposalReviewService(
        proposal_lookup=service.proposal_inputs,
        clock=lambda: NOW,
    )
    decision = review.decide(
        "poc_a3_api",
        proposal["proposal_id"],
        ProposalDecision.DISCARD,
        "named.employee",
        "Do not retain this proposal for A4.",
        "api-decision-a3",
    )
    assert decision.receipt.decision is ProposalDecision.DISCARD
    retained_response = handle_poc_assisted_authoring_web_api_request(
        method="GET",
        target="/api/pocs/poc_a3_api/retained-proposals",
        payload=None,
        runtime=service,
        review_runtime=review,
    )
    assert retained_response is not None and retained_response.status == 200
    assert retained_response.payload["retained_count"] == 0


def test_a3_stale_source_fails_closed_and_cannot_reenter_review_queue():
    _, intake, service = _runtime("poc_a3_stale")
    source_receipt_id = _attach_document(intake, "poc_a3_stale")
    first = service.create_assisted_draft(
        poc_id="poc_a3_stale",
        source_receipt_id=source_receipt_id,
        idempotency_key="author-stale-first",
    )
    current_source = intake.source_snapshot("poc_a3_stale", source_receipt_id)
    stale = {"value": False}

    def lookup(poc_id: str, receipt_id: str):
        if stale["value"]:
            raise POCSourceIntakeRevisionRequired(
                "The source receipt is stale; use the latest source revision."
            )
        return current_source

    isolated = ProcessLocalAssistedAuthoringService(
        source_lookup=lookup,
        executor=SyntheticSourceNeutralAssistedAuthoringExecutor(),
        clock=lambda: NOW,
    )
    isolated.create_assisted_draft(
        poc_id="poc_a3_stale",
        source_receipt_id=source_receipt_id,
        idempotency_key="author-stale-isolated",
    )
    stale["value"] = True
    with pytest.raises(AssistedAuthoringError) as caught:
        isolated.create_assisted_draft(
            poc_id="poc_a3_stale",
            source_receipt_id=source_receipt_id,
            idempotency_key="author-stale-retry",
        )
    assert caught.value.code == "source_stale"
    assert isolated.proposal_inputs("poc_a3_stale") == ()
    assert first.proposals[0].review_state == "NEEDS_REVIEW"


def test_a3_http_api_accepts_all_a2_source_kinds_and_notes_alias():
    server = SourceNeutralPOCDemoServer(("127.0.0.1", 0))
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()

    cases = (
        ("EMAIL", "email-text", "email_text", "The error rate must remain below 1%.", "EMAIL"),
        ("MEETING", "meeting", "transcript_text", "Customer: The error rate must remain below 1%.", "MEETING"),
        ("DOCUMENT", "document", "document_text", "The throughput must exceed 100 requests per second.", "DOCUMENT"),
        ("DOCUMENT", "notes", "document_text", "Notes: the budget must stay below 100 dollars.", "DOCUMENT"),
        ("EXISTING_CONTRACT", "contract", "contract_json", json.dumps(CONTRACT), "EXISTING_CONTRACT"),
    )

    def request(method: str, target: str, payload: dict | None = None) -> tuple[int, dict]:
        connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        try:
            body = None if payload is None else json.dumps(payload).encode()
            headers = {}
            if body is not None:
                headers = {
                    "Content-Type": "application/json",
                    "Origin": "http://127.0.0.1:{0}".format(server.server_port),
                }
            connection.request(method, target, body=body, headers=headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read().decode())
        finally:
            connection.close()

    try:
        for ordinal, (first_choice, route, field, value, expected_kind) in enumerate(cases, start=1):
            create_status, created = request(
                "POST",
                "/api/pocs",
                {
                    "display_name": "A3 API source {0}".format(ordinal),
                    "customer_label": "A3 customer",
                    "use_case": "Validate source-neutral assisted authoring.",
                    "owner": "field_engineer",
                    "first_source_choice": first_choice,
                    "idempotency_key": "api-create-a3-{0}".format(ordinal),
                },
            )
            assert create_status == 201
            poc_id = created["poc_id"]
            capture_status, captured = request(
                "POST",
                "/api/pocs/{0}/sources/{1}".format(poc_id, route),
                {field: value, "idempotency_key": "api-capture-a3-{0}".format(ordinal)},
            )
            assert capture_status == 201
            assert captured["source_kind"] == expected_kind
            author_status, authored = request(
                "POST",
                "/api/pocs/{0}/sources/{1}/assisted-authoring".format(
                    poc_id,
                    captured["source_receipt_id"],
                ),
                {"idempotency_key": "api-author-a3-{0}".format(ordinal)},
            )
            assert author_status == 201
            assert authored["authoring_receipt"]["source_kind"] == expected_kind
            assert authored["authoring_receipt"]["status"] == "NEEDS_REVIEW"
            assert authored["proposals"]
            assert all(item["review_state"] == "NEEDS_REVIEW" for item in authored["proposals"])
    finally:
        server.shutdown()
        worker.join(timeout=5)
        server.server_close()


def test_a3_closure_evidence_is_bounded_and_references_executable_tests():
    path = Path(__file__).parents[1] / "examples" / "product" / "request-to-proof-a3-closure-evidence-v1.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert set(payload) == {
        "schema_version",
        "train_slice",
        "status",
        "scope",
        "claims",
        "authority_boundary",
        "limitations",
        "frozen_baseline_edited",
    }
    assert payload["schema_version"] == "exitspec.request-to-proof-a3-closure-evidence.v1"
    assert payload["train_slice"] == "A3"
    assert payload["status"] == "IMPLEMENTED_AND_TESTED"
    assert payload["scope"] == "GL-03 and GL-05 only"
    assert set(payload["claims"]) == {"GL-03", "GL-05"}
    assert set(payload["authority_boundary"]) == {
        "source_authority",
        "proposal_state",
        "retention_state",
        "may_approve",
        "may_confirm",
        "may_freeze",
        "may_select_evidence",
        "may_execute",
        "may_import_evidence",
        "may_issue_verdict",
        "may_authorize_deployment",
    }
    assert payload["authority_boundary"]["source_authority"] == "UNTRUSTED_SOURCE_ONLY"
    assert payload["authority_boundary"]["proposal_state"] == "NEEDS_REVIEW"
    assert payload["authority_boundary"]["retention_state"] == "KEEP_FOR_CONTRACT_ONLY"
    assert all(
        value is False
        for key, value in payload["authority_boundary"].items()
        if key.startswith("may_")
    )
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
