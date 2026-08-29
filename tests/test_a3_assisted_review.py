"""Train A A3 proof for source-neutral assisted authoring and human triage."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
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
    ProposalReviewCapacityExceeded,
    ProposalReviewCrossPOC,
    ProposalReviewLookupUnavailable,
    ProposalReviewProposalUnavailable,
    ProposalReviewState,
    ProposalReviewStaleProposal,
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
    max_idempotency_records: int = 16_384,
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
        draft_lookup=drafts.get,
        executor=(
            SyntheticSourceNeutralAssistedAuthoringExecutor()
            if executor is None
            else executor
        ),
        provider=getattr(
            SyntheticSourceNeutralAssistedAuthoringExecutor()
            if executor is None
            else executor,
            "provider_name",
        ),
        endpoint=getattr(
            SyntheticSourceNeutralAssistedAuthoringExecutor()
            if executor is None
            else executor,
            "endpoint",
        ),
        max_idempotency_records=max_idempotency_records,
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
    provider_name = "test-provider"
    model = ASSISTED_AUTHORING_MODEL
    endpoint = "local://test-provider/a3"

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
    provider_name = "test-provider"
    model = ASSISTED_AUTHORING_MODEL
    endpoint = "local://test-provider/a3"

    def __init__(self, error: Exception) -> None:
        self.error = error
        self.requests = []

    def execute(self, request):
        self.requests.append(request)
        raise self.error


class ForgedReceiptExecutor(PayloadExecutor):
    def __init__(self, payload: object, **receipt_updates: object) -> None:
        super().__init__(payload)
        self.receipt = replace(_safe_provider_receipt(), **receipt_updates)

    def execute(self, request):
        self.requests.append(request)
        return StructuredJSONResult(output=self.payload, receipt=self.receipt)


class BlockingExecutor(PayloadExecutor):
    def __init__(self, payload: object) -> None:
        super().__init__(payload)
        self.started = threading.Event()
        self.release = threading.Event()

    def execute(self, request):
        self.requests.append(request)
        self.started.set()
        self.release.wait(timeout=5)
        return StructuredJSONResult(
            output=self.payload,
            receipt=_safe_provider_receipt(),
        )


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


@pytest.mark.parametrize(
    ("provider_code", "expected_status"),
    (
        (ProviderErrorCode.RATE_LIMITED, 429),
        (ProviderErrorCode.TIMEOUT, 504),
        (ProviderErrorCode.TRANSPORT, 503),
        (ProviderErrorCode.SERVICE_UNAVAILABLE, 503),
        (ProviderErrorCode.RETRIES_EXHAUSTED, 503),
        (ProviderErrorCode.ACCOUNT_UNAVAILABLE, 503),
    ),
)
def test_a3_authoring_operational_failures_map_to_safe_status_without_writes(
    provider_code, expected_status
):
    poc_id = "poc_a3_http_{0}".format(provider_code.value.removesuffix("_error"))
    executor = FailureExecutor(
        ProviderError(
            provider_code,
            "provider detail must not cross the transport boundary",
            retryable=True,
            attempts=2,
        )
    )
    _, intake, service = _runtime(poc_id, executor=executor)
    source_receipt_id = _attach_document(intake, poc_id, key="capture-{0}".format(poc_id))

    response = handle_poc_assisted_authoring_web_api_request(
        method="POST",
        target="/api/pocs/{0}/sources/{1}/assisted-authoring".format(
            poc_id, source_receipt_id
        ),
        payload={"idempotency_key": "author-{0}".format(poc_id)},
        runtime=service,
    )

    assert response is not None and response.status == expected_status
    assert set(response.payload) == {"error"}
    assert "provider detail" not in str(response.payload)
    assert service.list_receipts(poc_id) == ()


def test_a3_idempotency_alias_capacity_is_bounded_and_fails_closed():
    _, intake, service = _runtime(
        "poc_a3_idempotency_capacity",
        max_idempotency_records=2,
    )
    source_receipt_id = _attach_document(
        intake,
        "poc_a3_idempotency_capacity",
        key="capture-idempotency-capacity",
    )
    first = service.create_assisted_draft(
        poc_id="poc_a3_idempotency_capacity",
        source_receipt_id=source_receipt_id,
        idempotency_key="author-idempotency-first",
    )
    replay = service.create_assisted_draft(
        poc_id="poc_a3_idempotency_capacity",
        source_receipt_id=source_receipt_id,
        idempotency_key="author-idempotency-alias",
    )
    assert replay.receipt.authoring_receipt_id == first.receipt.authoring_receipt_id
    assert replay.receipt.idempotent_replay is True
    with pytest.raises(AssistedAuthoringError) as caught:
        service.create_assisted_draft(
            poc_id="poc_a3_idempotency_capacity",
            source_receipt_id=source_receipt_id,
            idempotency_key="author-idempotency-overflow",
        )
    assert caught.value.code == "capacity_exceeded"
    assert len(service._idempotency) == 2
    assert len(service.list_receipts("poc_a3_idempotency_capacity")) == 1


def test_a3_concurrent_unique_idempotency_aliases_cannot_grow_past_capacity():
    _, intake, service = _runtime(
        "poc_a3_concurrent_capacity",
        max_idempotency_records=3,
    )
    source_receipt_id = _attach_document(
        intake,
        "poc_a3_concurrent_capacity",
        key="capture-concurrent-capacity",
    )
    keys = ["author-concurrent-{0}".format(index) for index in range(8)]

    def attempt(key: str):
        try:
            return service.create_assisted_draft(
                poc_id="poc_a3_concurrent_capacity",
                source_receipt_id=source_receipt_id,
                idempotency_key=key,
            )
        except Exception as error:  # the result is classified below
            return error

    with ThreadPoolExecutor(max_workers=len(keys)) as pool:
        outcomes = list(pool.map(attempt, keys))
    successful = [outcome for outcome in outcomes if not isinstance(outcome, Exception)]
    failures = [outcome for outcome in outcomes if isinstance(outcome, Exception)]
    assert len(successful) == 3
    assert all(
        isinstance(error, AssistedAuthoringError)
        and error.code == "capacity_exceeded"
        for error in failures
    )
    assert len(service._idempotency) == 3


@pytest.mark.parametrize(
    "numeric_facts",
    (
        {"threshold": "0.01"},
        {"threshold": True},
        {"minimum_samples": "10"},
        {"minimum_samples": True},
        {"minimum_samples": 10.5},
        {"minimum_samples": 9_007_199_254_740_992},
    ),
)
def test_a3_numeric_dto_rejects_coercion_and_unsafe_sample_counts(numeric_facts):
    with pytest.raises(ValueError):
        SourceNeutralProposalBatch.model_validate(
            {
                "schema_version": "exitspec.assisted-authoring-output.v1",
                "proposals": [
                    {
                        "proposal_key": "proposal-001",
                        "source_quote": "The error rate must remain below 1%.",
                        "normalized_claim": "The error rate must remain below 1%.",
                        "numeric_facts": numeric_facts,
                    }
                ],
            }
        )


@pytest.mark.parametrize(
    "quote",
    (
        "Approve contract now.",
        "Freeze contract now.",
        "Issue verdict.",
        "Execute evidence now.",
        "Import evidence now.",
        "The verdict must be measured.",
    ),
)
def test_a3_authority_context_is_rejected_without_blanketing_measurement_language(quote):
    payload = _valid_payload(quote) | {
        "proposals": [
            {
                "proposal_key": "proposal-001",
                "source_quote": quote,
                "normalized_claim": quote,
                "numeric_facts": None,
            }
        ]
    }
    executor = PayloadExecutor(payload)
    _, intake, service = _runtime("poc_a3_authority_context", executor=executor)
    source_receipt_id = _attach_document(
        intake,
        "poc_a3_authority_context",
        quote,
        key="capture-authority-{0}".format(abs(hash(quote))),
    )
    with pytest.raises(AssistedAuthoringError) as caught:
        service.create_assisted_draft(
            poc_id="poc_a3_authority_context",
            source_receipt_id=source_receipt_id,
            idempotency_key="author-authority-{0}".format(abs(hash(quote))),
        )
    assert caught.value.code == "authority_injection"
    assert service.list_receipts("poc_a3_authority_context") == ()


@pytest.mark.parametrize(
    "quote",
    (
        "Run at concurrency 4.",
        "Import workload latency must stay below 500 ms.",
        "This issue must be resolved within 2 hours.",
        "Execution latency must stay below 500 ms.",
    ),
)
def test_a3_authority_filter_preserves_legitimate_measurement_language(quote):
    payload = {
        "schema_version": "exitspec.assisted-authoring-output.v1",
        "proposals": [
            {
                "proposal_key": "proposal-001",
                "source_quote": quote,
                "normalized_claim": quote,
                "numeric_facts": None,
            }
        ],
    }
    executor = PayloadExecutor(payload)
    _, intake, service = _runtime("poc_a3_authority_language", executor=executor)
    source_receipt_id = _attach_document(
        intake,
        "poc_a3_authority_language",
        quote,
        key="capture-language-{0}".format(abs(hash(quote))),
    )
    result = service.create_assisted_draft(
        poc_id="poc_a3_authority_language",
        source_receipt_id=source_receipt_id,
        idempotency_key="author-language-{0}".format(abs(hash(quote))),
    )
    assert result.proposals[0].source_quote == quote


def test_a3_blocking_executor_discards_output_after_source_revision():
    executor = BlockingExecutor(_valid_payload())
    drafts, intake, _ = _runtime("poc_a3_source_race", executor=executor)
    source_receipt_id = _attach_document(intake, "poc_a3_source_race")
    stale = {"value": False}
    original_lookup = intake.source_snapshot

    def lookup(poc_id, receipt_id):
        if stale["value"]:
            raise POCSourceIntakeRevisionRequired("source is stale")
        return original_lookup(poc_id, receipt_id)

    service = ProcessLocalAssistedAuthoringService(
        source_lookup=lookup,
        draft_lookup=drafts.get,
        executor=executor,
        provider=getattr(executor, "provider_name", "synthetic"),
        endpoint=getattr(executor, "endpoint", "local://exitspec/source-neutral-assisted-authoring"),
        clock=lambda: NOW,
    )
    outcome = []

    def author():
        try:
            service.create_assisted_draft(
                poc_id="poc_a3_source_race",
                source_receipt_id=source_receipt_id,
                idempotency_key="author-source-race",
            )
        except Exception as error:
            outcome.append(error)

    thread = threading.Thread(target=author)
    thread.start()
    assert executor.started.wait(timeout=2)
    stale["value"] = True
    executor.release.set()
    thread.join(timeout=3)
    assert len(outcome) == 1
    assert isinstance(outcome[0], AssistedAuthoringError)
    assert outcome[0].code == "source_stale"
    assert service.list_receipts("poc_a3_source_race") == ()


def test_a3_blocking_executor_discards_output_after_poc_archive():
    executor = BlockingExecutor(_valid_payload())
    drafts, intake, service = _runtime("poc_a3_archive_race", executor=executor)
    source_receipt_id = _attach_document(intake, "poc_a3_archive_race")
    outcome = []

    def author():
        try:
            service.create_assisted_draft(
                poc_id="poc_a3_archive_race",
                source_receipt_id=source_receipt_id,
                idempotency_key="author-archive-race",
            )
        except Exception as error:
            outcome.append(error)

    thread = threading.Thread(target=author)
    thread.start()
    assert executor.started.wait(timeout=2)
    drafts.archive("poc_a3_archive_race")
    executor.release.set()
    thread.join(timeout=3)
    assert len(outcome) == 1
    assert isinstance(outcome[0], AssistedAuthoringError)
    assert outcome[0].code == "source_unavailable"
    assert len(service._results_by_request) == 0


def test_a3_same_key_waiter_has_bounded_in_progress_failure():
    executor = BlockingExecutor(_valid_payload())
    _, intake, service = _runtime("poc_a3_waiter", executor=executor)
    source_receipt_id = _attach_document(intake, "poc_a3_waiter")
    service._inflight_wait_seconds = 0.01
    first_outcome = []

    def author():
        try:
            first_outcome.append(
                service.create_assisted_draft(
                    poc_id="poc_a3_waiter",
                    source_receipt_id=source_receipt_id,
                    idempotency_key="author-waiter",
                )
            )
        except Exception as error:
            first_outcome.append(error)

    thread = threading.Thread(target=author)
    thread.start()
    assert executor.started.wait(timeout=2)
    with pytest.raises(AssistedAuthoringError) as caught:
        service.create_assisted_draft(
            poc_id="poc_a3_waiter",
            source_receipt_id=source_receipt_id,
            idempotency_key="author-waiter",
        )
    assert caught.value.code == "service_unavailable"
    assert caught.value.retryable is True
    executor.release.set()
    thread.join(timeout=3)
    assert len(first_outcome) == 1
    assert not isinstance(first_outcome[0], Exception)


def test_a3_retained_projection_discards_when_source_changes_during_lookup():
    drafts, intake, _ = _runtime("poc_a3_projection_race")
    source_receipt_id = _attach_document(intake, "poc_a3_projection_race")
    stale = {"value": False}
    original_lookup = intake.source_snapshot

    def lookup(poc_id, receipt_id):
        if stale["value"]:
            raise POCSourceIntakeRevisionRequired("source is stale")
        return original_lookup(poc_id, receipt_id)

    service = ProcessLocalAssistedAuthoringService(
        source_lookup=lookup,
        draft_lookup=drafts.get,
        executor=SyntheticSourceNeutralAssistedAuthoringExecutor(),
        clock=lambda: NOW,
    )
    authored = service.create_assisted_draft(
        poc_id="poc_a3_projection_race",
        source_receipt_id=source_receipt_id,
        idempotency_key="author-projection-race",
    )
    review = ProcessLocalProposalReviewService(
        proposal_lookup=service.proposal_inputs,
        clock=lambda: NOW,
    )
    review.decide(
        "poc_a3_projection_race",
        authored.proposals[0].proposal_id,
        ProposalDecision.KEEP_FOR_CONTRACT,
        "named.employee",
        "Retain for A4.",
        "decision-projection-race",
    )

    class RacingReview:
        def __init__(self):
            self.calls = 0

        def list_proposals(self, poc_id):
            self.calls += 1
            items = review.list_proposals(poc_id)
            stale["value"] = True
            return items

    with pytest.raises(AssistedAuthoringError) as caught:
        service.retained_projection(
            "poc_a3_projection_race",
            RacingReview(),
        )
    assert caught.value.code == "source_stale"


@pytest.mark.parametrize(
    ("quote", "threshold", "should_pass"),
    (
        ("The error rate must remain below 1%.", 0.01, True),
        ("The error rate must remain below 1%.", 1.0, False),
        ("The error rate must remain below 1.0%.", 0.01, True),
        ("The error rate must remain below 1.0%.", 1.0, False),
        ("The error rate must remain below 1.00%.", 0.01, True),
        ("The error rate must remain below 1.00%.", 1.0, False),
        ("The error rate must remain below 1.0 %.", 0.01, True),
        ("The error rate must remain below 1.0 %.", 1.0, False),
        ("The error rate must remain below 0.5%.", 0.005, True),
        ("The error rate must remain below 0.5%.", 0.5, False),
        ("The standalone threshold is 1.0.", 1.0, True),
        ("The standalone threshold is 0.5.", 0.5, True),
        ("The threshold is 1.0 and error rate is below 0.5%.", 1.0, True),
        ("The threshold is 1.0 and error rate is below 0.5%.", 0.5, False),
    ),
)
def test_a3_numeric_percent_anchor_does_not_accept_wrong_scaling(
    quote, threshold, should_pass
):
    executor = PayloadExecutor(_valid_payload(quote) | {
        "proposals": [
            _valid_payload(quote)["proposals"][0]
            | {"numeric_facts": {"threshold": threshold}}
        ]
    })
    _, intake, service = _runtime("poc_a3_numeric_anchor", executor=executor)
    source_receipt_id = _attach_document(
        intake,
        "poc_a3_numeric_anchor",
        quote,
        key="capture-numeric-{0}".format(threshold),
    )

    if should_pass:
        result = service.create_assisted_draft(
            poc_id="poc_a3_numeric_anchor",
            source_receipt_id=source_receipt_id,
            idempotency_key="author-numeric-{0}".format(threshold),
        )
        assert result.proposals[0].numeric_facts.threshold == threshold
    else:
        with pytest.raises(AssistedAuthoringError) as caught:
            service.create_assisted_draft(
                poc_id="poc_a3_numeric_anchor",
                source_receipt_id=source_receipt_id,
                idempotency_key="author-numeric-{0}".format(threshold),
            )
        assert caught.value.code == "numeric_source_mismatch"
        assert service.list_receipts("poc_a3_numeric_anchor") == ()


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


def test_a3_current_review_projection_preserves_decided_membership_and_binding():
    _, intake, service = _runtime("poc_a3_current_projection")
    source_receipt_id = _attach_document(intake, "poc_a3_current_projection")
    authored = service.create_assisted_draft(
        poc_id="poc_a3_current_projection",
        source_receipt_id=source_receipt_id,
        idempotency_key="current-projection-author",
    )
    review = ProcessLocalProposalReviewService(
        proposal_lookup=service.proposal_inputs,
        clock=lambda: NOW,
    )
    before = handle_poc_assisted_authoring_web_api_request(
        method="GET",
        target="/api/pocs/poc_a3_current_projection/assisted-authoring/current-review",
        payload=None,
        runtime=service,
        review_runtime=review,
    )
    assert before is not None and before.status == 200
    projection = before.payload["proposals"][0]
    assert projection["proposal_id"] == authored.proposals[0].proposal_id
    assert projection["source_receipt_id"] == source_receipt_id
    assert projection["authoring_receipt_id"] == authored.receipt.authoring_receipt_id
    assert projection["review_state"] == "NEEDS_REVIEW"
    assert projection["decision"] is None

    review.decide(
        "poc_a3_current_projection",
        authored.proposals[0].proposal_id,
        ProposalDecision.KEEP_FOR_CONTRACT,
        "named.employee",
        "Retain for later acceptance drafting.",
        "current-projection-decision",
    )
    after = handle_poc_assisted_authoring_web_api_request(
        method="GET",
        target="/api/pocs/poc_a3_current_projection/assisted-authoring/current-review",
        payload=None,
        runtime=service,
        review_runtime=review,
    )
    assert after is not None and after.status == 200
    assert after.payload["proposals"][0]["review_state"] == "KEEP_FOR_CONTRACT"
    assert after.payload["proposals"][0]["decision"]["proposal_id"] == authored.proposals[0].proposal_id


def test_a3_existing_decision_blocks_new_authoring_without_orphaning_decision():
    _, intake, service = _runtime("poc_a3_decision_guard")
    source_receipt_id = _attach_document(intake, "poc_a3_decision_guard")
    review = ProcessLocalProposalReviewService(
        proposal_lookup=intake.proposal_inputs,
        clock=lambda: NOW,
    )
    service.bind_decision_lookup(review.source_has_decision)
    service.bind_review_commit_guard(review.authoring_commit_guard)
    prior = review.list_proposals("poc_a3_decision_guard")[0]
    review.decide(
        "poc_a3_decision_guard",
        prior.proposal_id,
        ProposalDecision.DISCARD,
        "named.employee",
        "Discard the existing A2 candidate.",
        "decision-guard-prior",
    )
    with pytest.raises(AssistedAuthoringError) as caught:
        service.create_assisted_draft(
            poc_id="poc_a3_decision_guard",
            source_receipt_id=source_receipt_id,
            idempotency_key="decision-guard-new-authoring",
        )
    assert caught.value.code == "attempt_conflict"
    assert service.list_receipts("poc_a3_decision_guard") == ()
    assert review.list_proposals("poc_a3_decision_guard")[0].review_state is ProposalReviewState.DISCARD


def test_a3_inflight_authoring_discards_result_when_decision_lands():
    executor = BlockingExecutor(_valid_payload())
    _, intake, service = _runtime("poc_a3_decision_race", executor=executor)
    source_receipt_id = _attach_document(intake, "poc_a3_decision_race")
    review = ProcessLocalProposalReviewService(
        proposal_lookup=intake.proposal_inputs,
        clock=lambda: NOW,
    )
    service.bind_decision_lookup(review.source_has_decision)
    service.bind_review_commit_guard(review.authoring_commit_guard)
    prior = review.list_proposals("poc_a3_decision_race")[0]
    outcome = []

    def author():
        try:
            service.create_assisted_draft(
                poc_id="poc_a3_decision_race",
                source_receipt_id=source_receipt_id,
                idempotency_key="decision-race-author",
            )
        except Exception as error:
            outcome.append(error)

    worker = threading.Thread(target=author)
    worker.start()
    assert executor.started.wait(timeout=2)
    review.decide(
        "poc_a3_decision_race",
        prior.proposal_id,
        ProposalDecision.DISCARD,
        "named.employee",
        "Discard while the assist is still running.",
        "decision-race-human",
    )
    executor.release.set()
    worker.join(timeout=5)
    assert len(outcome) == 1
    assert isinstance(outcome[0], AssistedAuthoringError)
    assert outcome[0].code == "attempt_conflict"
    assert service.list_receipts("poc_a3_decision_race") == ()
    assert review.list_proposals("poc_a3_decision_race")[0].review_state is ProposalReviewState.DISCARD


@pytest.mark.parametrize(
    "decision",
    (ProposalDecision.DISCARD, ProposalDecision.KEEP_FOR_CONTRACT),
)
def test_a3_atomic_final_review_guard_prevents_decision_orphan_race(decision):
    poc_id = "poc_a3_atomic_decision_race_{0}".format(decision.value.lower())
    drafts = _drafts(poc_id)
    intake = ProcessLocalPOCSourceIntake(
        draft_lookup=drafts.get,
        clock=lambda: NOW,
    )
    source_receipt_id = _attach_document(intake, poc_id)
    review = ProcessLocalProposalReviewService(
        proposal_lookup=intake.proposal_inputs,
        clock=lambda: NOW,
    )
    prior = review.list_proposals(poc_id)[0]

    class FinalCheckDecisionExecutor(PayloadExecutor):
        def __init__(self, payload):
            super().__init__(payload)
            self.trigger = False
            self.triggered = False

        @property
        def endpoint(self):
            if self.trigger and not self.triggered:
                self.triggered = True
                review.decide(
                    poc_id,
                    prior.proposal_id,
                    decision,
                    "named.employee",
                    "Record the existing human triage decision.",
                    "atomic-final-check-{0}".format(decision.value.lower()),
                )
            return "local://test-provider/a3"

    executor = FinalCheckDecisionExecutor(_valid_payload())

    def clock():
        executor.trigger = True
        return NOW

    service = ProcessLocalAssistedAuthoringService(
        source_lookup=intake.source_snapshot,
        draft_lookup=drafts.get,
        executor=executor,
        provider=executor.provider_name,
        endpoint="local://test-provider/a3",
        clock=clock,
    )
    service.bind_decision_lookup(review.source_has_decision)
    service.bind_review_commit_guard(review.authoring_commit_guard)

    with pytest.raises(AssistedAuthoringError) as caught:
        service.create_assisted_draft(
            poc_id=poc_id,
            source_receipt_id=source_receipt_id,
            idempotency_key="atomic-final-check-authoring",
        )

    assert caught.value.code == "attempt_conflict"
    visible = review.list_proposals(poc_id)
    assert visible[0].review_state is ProposalReviewState(decision.value)
    assert visible[0].decision is not None
    assert service.list_receipts(poc_id) == ()
    assert service.proposal_inputs(poc_id) == ()
    assert service._source_attempts == {}
    assert service._idempotency == {}
    assert service._inflight == {}


def test_a3_executor_metadata_is_required_and_rechecked_before_write():
    class ExecuteOnly:
        def execute(self, request):
            return StructuredJSONResult(output=_valid_payload(), receipt=_safe_provider_receipt())

    with pytest.raises(ValueError, match="metadata"):
        ProcessLocalAssistedAuthoringService(
            source_lookup=lambda _poc, _source: None,
            draft_lookup=lambda _poc: None,
            executor=ExecuteOnly(),
        )

    executor = PayloadExecutor(_valid_payload())
    _, intake, service = _runtime("poc_a3_metadata_mutation", executor=executor)
    source_receipt_id = _attach_document(intake, "poc_a3_metadata_mutation")
    executor.endpoint = "local://forged-endpoint"
    with pytest.raises(AssistedAuthoringError) as caught:
        service.create_assisted_draft(
            poc_id="poc_a3_metadata_mutation",
            source_receipt_id=source_receipt_id,
            idempotency_key="metadata-mutated",
        )
    assert caught.value.code == "invalid_output"
    assert service.list_receipts("poc_a3_metadata_mutation") == ()

    for field, value in (
        ("provider", "forged-provider"),
        ("model", "wrong-model"),
        ("endpoint", "local://forged-endpoint"),
    ):
        executor = ForgedReceiptExecutor(_valid_payload(), **{field: value})
        _, intake, service = _runtime(
            "poc_a3_forged_{0}".format(field), executor=executor
        )
        source_receipt_id = _attach_document(
            intake, "poc_a3_forged_{0}".format(field), key="forged-{0}".format(field)
        )
        with pytest.raises(AssistedAuthoringError) as caught:
            service.create_assisted_draft(
                poc_id="poc_a3_forged_{0}".format(field),
                source_receipt_id=source_receipt_id,
                idempotency_key="forged-receipt-{0}".format(field),
            )
        assert caught.value.code == "invalid_output"
        assert service.list_receipts("poc_a3_forged_{0}".format(field)) == ()

    executor = PayloadExecutor(_valid_payload())
    drafts, intake, _ = _runtime("poc_a3_metadata_changes_during_attempt")

    def mutate_clock():
        executor.endpoint = "local://changed-after-provider-check"
        return NOW

    service = ProcessLocalAssistedAuthoringService(
        source_lookup=intake.source_snapshot,
        draft_lookup=drafts.get,
        executor=executor,
        provider=executor.provider_name,
        endpoint="local://test-provider/a3",
        clock=mutate_clock,
    )
    source_receipt_id = _attach_document(
        intake, "poc_a3_metadata_changes_during_attempt", key="metadata-clock"
    )
    with pytest.raises(AssistedAuthoringError) as caught:
        service.create_assisted_draft(
            poc_id="poc_a3_metadata_changes_during_attempt",
            source_receipt_id=source_receipt_id,
            idempotency_key="metadata-clock-mutated",
        )
    assert caught.value.code == "invalid_output"
    assert service.list_receipts("poc_a3_metadata_changes_during_attempt") == ()


def test_a3_stale_source_fails_closed_and_cannot_reenter_review_queue():
    drafts, intake, service = _runtime("poc_a3_stale")
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
        draft_lookup=drafts.get,
        executor=SyntheticSourceNeutralAssistedAuthoringExecutor(),
        clock=lambda: NOW,
    )
    isolated.create_assisted_draft(
        poc_id="poc_a3_stale",
        source_receipt_id=source_receipt_id,
        idempotency_key="author-stale-isolated",
    )
    isolated.create_assisted_draft(
        poc_id="poc_a3_stale",
        source_receipt_id=source_receipt_id,
        idempotency_key="author-stale-replay",
    )
    assert len(isolated.list_receipts("poc_a3_stale")) == 1
    stale["value"] = True
    assert isolated.list_receipts("poc_a3_stale") == ()
    collection = handle_poc_assisted_authoring_web_api_request(
        method="GET",
        target="/api/pocs/poc_a3_stale/assisted-authoring",
        payload=None,
        runtime=isolated,
    )
    assert collection is not None and collection.status == 200
    assert collection.payload["receipts"] == []
    with pytest.raises(AssistedAuthoringError) as replay_caught:
        isolated.create_assisted_draft(
            poc_id="poc_a3_stale",
            source_receipt_id=source_receipt_id,
            idempotency_key="author-stale-replay",
        )
    assert replay_caught.value.code == "source_stale"
    replay_response = handle_poc_assisted_authoring_web_api_request(
        method="POST",
        target="/api/pocs/poc_a3_stale/sources/{0}/assisted-authoring".format(
            source_receipt_id
        ),
        payload={"idempotency_key": "author-stale-replay"},
        runtime=isolated,
    )
    assert replay_response is not None and replay_response.status == 409
    assert replay_response.payload == {
        "error": "Assisted authoring conflicts with the current source state."
    }
    with pytest.raises(AssistedAuthoringError) as caught:
        isolated.create_assisted_draft(
            poc_id="poc_a3_stale",
            source_receipt_id=source_receipt_id,
            idempotency_key="author-stale-retry",
        )
    assert caught.value.code == "source_stale"
    assert isolated.proposal_inputs("poc_a3_stale") == ()
    assert first.proposals[0].review_state == "NEEDS_REVIEW"


def test_a3_collection_endpoints_require_active_draft_and_map_safe_failures():
    drafts, _, service = _runtime("poc_a3_transport")

    def response(target: str, review_runtime=None):
        return handle_poc_assisted_authoring_web_api_request(
            method="GET",
            target=target,
            payload=None,
            runtime=service,
            review_runtime=review_runtime,
        )

    missing_collection = response(
        "/api/pocs/poc_a3_missing/assisted-authoring"
    )
    missing_retained = response(
        "/api/pocs/poc_a3_missing/retained-proposals",
        ProcessLocalProposalReviewService(proposal_lookup=lambda _poc_id: ()),
    )
    assert missing_collection is not None
    assert missing_collection.status == 404
    assert missing_collection.payload == {"error": "The source was not found."}
    assert missing_retained is not None
    assert missing_retained.status == 404
    assert missing_retained.payload == {"error": "The source was not found."}

    drafts.archive("poc_a3_transport")
    archived_collection = response(
        "/api/pocs/poc_a3_transport/assisted-authoring"
    )
    archived_retained = response(
        "/api/pocs/poc_a3_transport/retained-proposals",
        ProcessLocalProposalReviewService(proposal_lookup=lambda _poc_id: ()),
    )
    assert archived_collection is not None and archived_collection.status == 404
    assert archived_retained is not None and archived_retained.status == 404

    class RaisingReview:
        def __init__(self, error_type):
            self.error_type = error_type

        def list_proposals(self, _poc_id):
            raise self.error_type("safe review failure")

    failure_cases = (
        (ProposalReviewProposalUnavailable, 404),
        (ProposalReviewCrossPOC, 404),
        (ProposalReviewStaleProposal, 409),
        (ProposalReviewLookupUnavailable, 503),
        (ProposalReviewCapacityExceeded, 503),
    )
    _, intake, service = _runtime("poc_a3_failure_mapping")
    source_receipt_id = _attach_document(intake, "poc_a3_failure_mapping")
    service.create_assisted_draft(
        poc_id="poc_a3_failure_mapping",
        source_receipt_id=source_receipt_id,
        idempotency_key="failure-mapping-author",
    )
    before = service.list_receipts("poc_a3_failure_mapping")
    for error_type, expected_status in failure_cases:
        mapped = handle_poc_assisted_authoring_web_api_request(
            method="GET",
            target="/api/pocs/poc_a3_failure_mapping/retained-proposals",
            payload=None,
            runtime=service,
            review_runtime=RaisingReview(error_type),
        )
        assert mapped is not None
        assert mapped.status == expected_status
        assert set(mapped.payload) == {"error"}
        assert "safe review failure" not in str(mapped.payload)
    assert service.list_receipts("poc_a3_failure_mapping") == before


def test_a3_http_collections_reject_missing_and_archived_pocs_without_writes():
    server = SourceNeutralPOCDemoServer(("127.0.0.1", 0))
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()

    def get(target: str) -> tuple[int, dict]:
        connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        try:
            connection.request("GET", target)
            response = connection.getresponse()
            return response.status, json.loads(response.read().decode())
        finally:
            connection.close()

    try:
        for suffix in ("assisted-authoring", "retained-proposals"):
            status, payload = get(
                "/api/pocs/poc_a3_transport_missing/" + suffix
            )
            assert status == 404
            assert payload == {"error": "The source was not found."}

        created = server.draft_poc_service.create(
            DraftPOCCreateRequest(
                poc_id="poc_a3_transport_archived",
                display_name="A3 transport test",
                customer_label="A3 customer",
                use_case="Validate safe collection lookup.",
                owner="field_engineer",
                first_source_choice=FirstSourceChoice.DOCUMENT,
            ),
            idempotency_key="transport-archived-create",
        )
        assert created.draft.archive_state.value == "ACTIVE"
        server.draft_poc_service.archive("poc_a3_transport_archived")
        for suffix in ("assisted-authoring", "retained-proposals"):
            status, payload = get(
                "/api/pocs/poc_a3_transport_archived/" + suffix
            )
            assert status == 404
            assert payload == {"error": "The source was not found."}
        assert len(server.assisted_authoring_service._source_attempts) == 0
        assert len(server.proposal_review_service) == 0
    finally:
        server.shutdown()
        worker.join(timeout=5)
        server.server_close()


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
            workspace_status, workspace = request("GET", "/api/workspace")
            assert workspace_status == 200
            current = next(item for item in workspace["pocs"] if item["poc_id"] == poc_id)
            assert current["current_proposal_count"] == len(authored["proposals"])
    finally:
        server.shutdown()
        worker.join(timeout=5)
        server.server_close()


def test_a3_http_json_boundary_rejects_duplicate_nonfinite_depth_and_nodes():
    server = SourceNeutralPOCDemoServer(("127.0.0.1", 0))
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()

    def raw(body: str) -> tuple[int, dict]:
        connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        try:
            connection.request(
                "POST",
                "/api/pocs",
                body=body.encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Origin": "http://127.0.0.1:{0}".format(server.server_port),
                },
            )
            response = connection.getresponse()
            return response.status, json.loads(response.read().decode("utf-8"))
        finally:
            connection.close()

    valid = json.dumps(
        {
            "display_name": "Bounded JSON POC",
            "customer_label": "Customer",
            "use_case": "Validate bounded JSON request parsing.",
            "owner": "field_engineer",
            "first_source_choice": "DOCUMENT",
            "idempotency_key": "http-json-valid",
        }
    )
    try:
        status, payload = raw(valid)
        assert status == 201
        assert payload["poc_id"].startswith("poc_")
        bad_bodies = (
            valid[:-1] + ',"idempotency_key":"duplicate"}',
            '{"display_name":"x","customer_label":"x","use_case":"x","owner":"x","first_source_choice":"DOCUMENT","idempotency_key":"nan","extra":NaN}',
            '{"display_name":"x","customer_label":"x","use_case":"x","owner":"x","first_source_choice":"DOCUMENT","idempotency_key":"inf","extra":Infinity}',
            '{"nested":' + '{"x":' * 40 + '0' + '}' * 40 + '}',
            '{"nested":[' + ','.join("0" for _ in range(4_100)) + ']}',
        )
        for body in bad_bodies:
            status, payload = raw(body)
            assert status == 400
            assert payload == {"error": "Request is invalid."}
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
