from __future__ import annotations

from datetime import datetime, timezone
from http import HTTPStatus
from typing import Any

import pytest

from exitspec.poc_contract_definition import (
    ProcessLocalContractDefinitionService,
)
from exitspec.poc_contract_definition_web_api import (
    handle_poc_contract_definition_web_api_request,
    is_poc_contract_definition_web_api_target,
)
from exitspec.poc_proposal_review import (
    ProcessLocalProposalReviewService,
    ProposalDecision,
    SourceBoundProposal,
)
from exitspec.poc_sources import SourceKind


NOW = datetime(2026, 7, 29, 17, 0, tzinfo=timezone.utc)
POC_ID = "poc_definition_web_alpha"
OTHER_POC_ID = "poc_definition_web_beta"
TTFT_PROPOSAL_ID = "prop_ttft_latency_001"
ERROR_PROPOSAL_ID = "prop_error_rate_001"
DISCARDED_PROPOSAL_ID = "prop_discarded_001"
UNREVIEWED_PROPOSAL_ID = "prop_unreviewed_001"
ROOT = f"/api/pocs/{POC_ID}/definitions"

PROPOSAL_FIELDS = {
    "definition",
    "normalized_claim",
    "proposal_id",
    "review_state",
    "source_kind",
    "source_quote",
    "source_receipt_id",
}
DEFINITION_FIELDS = {
    "concurrency",
    "defined_at",
    "definition_id",
    "definition_sha256",
    "metric",
    "minimum_samples",
    "operator",
    "output_tokens_max",
    "output_tokens_min",
    "prompt_tokens_max",
    "prompt_tokens_min",
    "threshold",
    "unit",
}


class _SourceLookup:
    def __init__(self) -> None:
        self.by_poc: dict[str, tuple[SourceBoundProposal, ...]] = {}
        self.failure: Exception | None = None

    def __call__(self, poc_id: str) -> tuple[SourceBoundProposal, ...]:
        if self.failure is not None:
            raise self.failure
        return self.by_poc.get(poc_id, ())


def _proposal(
    proposal_id: str,
    *,
    poc_id: str = POC_ID,
    source_kind: SourceKind = SourceKind.DOCUMENT,
    source_quote: str | None = None,
    normalized_claim: str | None = None,
) -> SourceBoundProposal:
    quote = source_quote or {
        TTFT_PROPOSAL_ID: "P95 TTFT must remain at or below 500 ms.",
        ERROR_PROPOSAL_ID: "Error rate must remain at or below 1 percent.",
        DISCARDED_PROPOSAL_ID: "The dashboard should use the customer logo.",
        UNREVIEWED_PROPOSAL_ID: "Throughput should be discussed later.",
    }.get(proposal_id, "A bounded source claim.")
    return SourceBoundProposal(
        poc_id=poc_id,
        proposal_id=proposal_id,
        source_receipt_id="srcpt_{0}".format(proposal_id.removeprefix("prop_")),
        source_kind=source_kind,
        source_quote=quote,
        normalized_claim=normalized_claim or quote,
    )


def _services(
    *,
    definition_options: dict[str, Any] | None = None,
) -> tuple[
    _SourceLookup,
    ProcessLocalProposalReviewService,
    ProcessLocalContractDefinitionService,
]:
    lookup = _SourceLookup()
    lookup.by_poc[POC_ID] = (
        _proposal(TTFT_PROPOSAL_ID),
        _proposal(ERROR_PROPOSAL_ID, source_kind=SourceKind.MEETING),
        _proposal(DISCARDED_PROPOSAL_ID),
        _proposal(UNREVIEWED_PROPOSAL_ID),
    )
    review = ProcessLocalProposalReviewService(
        proposal_lookup=lookup,
        clock=lambda: NOW,
    )
    review.decide(
        POC_ID,
        TTFT_PROPOSAL_ID,
        ProposalDecision.KEEP_FOR_CONTRACT,
        "Jayesh Suyal",
        "The latency claim is measurable.",
        "review-keep-ttft",
    )
    review.decide(
        POC_ID,
        ERROR_PROPOSAL_ID,
        ProposalDecision.KEEP_FOR_CONTRACT,
        "Jayesh Suyal",
        "The reliability claim is measurable.",
        "review-keep-error",
    )
    review.decide(
        POC_ID,
        DISCARDED_PROPOSAL_ID,
        ProposalDecision.DISCARD,
        "Jayesh Suyal",
        "This is not a performance acceptance criterion.",
        "review-discard-cosmetic",
    )
    options: dict[str, Any] = {
        "proposal_lookup": review.list_proposals,
        "clock": lambda: NOW,
    }
    options.update(definition_options or {})
    definitions = ProcessLocalContractDefinitionService(**options)
    return lookup, review, definitions


def _body(
    *,
    proposal_id: str = TTFT_PROPOSAL_ID,
    idempotency_key: str = "define-ttft-web-001",
    **updates: Any,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "proposal_id": proposal_id,
        "metric": "TTFT_P95_MS",
        "operator": "LT",
        "threshold": 500.0,
        "minimum_samples": 100,
        "concurrency": 4,
        "prompt_tokens_min": 512,
        "prompt_tokens_max": 4_096,
        "output_tokens_min": 64,
        "output_tokens_max": 512,
        "reviewer": "Jayesh Suyal",
        "rationale": "This is the bounded latency acceptance requirement.",
        "idempotency_key": idempotency_key,
    }
    payload.update(updates)
    return payload


def _handle(
    definitions: ProcessLocalContractDefinitionService,
    review: ProcessLocalProposalReviewService,
    method: str,
    target: str,
    payload: Any = None,
):
    response = handle_poc_contract_definition_web_api_request(
        method=method,
        target=target,
        payload=payload,
        definition_runtime=definitions,
        proposal_runtime=review,
    )
    assert response is not None
    return response


def _create_ttft(
    definitions: ProcessLocalContractDefinitionService,
    review: ProcessLocalProposalReviewService,
):
    return _handle(definitions, review, "POST", ROOT, _body())


def test_unrelated_routes_are_not_claimed():
    _, review, definitions = _services()

    assert is_poc_contract_definition_web_api_target("/api/pocs") is False
    assert (
        is_poc_contract_definition_web_api_target(
            f"/api/pocs/{POC_ID}/proposals"
        )
        is False
    )
    assert (
        handle_poc_contract_definition_web_api_request(
            method="GET",
            target=f"/api/pocs/{POC_ID}",
            payload=None,
            definition_runtime=definitions,
            proposal_runtime=review,
        )
        is None
    )


def test_runtime_types_are_exact_even_for_unrelated_targets():
    _, review, definitions = _services()

    with pytest.raises(TypeError, match="definition_runtime"):
        handle_poc_contract_definition_web_api_request(
            method="GET",
            target="/unrelated",
            payload=None,
            definition_runtime=object(),  # type: ignore[arg-type]
            proposal_runtime=review,
        )
    with pytest.raises(TypeError, match="proposal_runtime"):
        handle_poc_contract_definition_web_api_request(
            method="GET",
            target="/unrelated",
            payload=None,
            definition_runtime=definitions,
            proposal_runtime=object(),  # type: ignore[arg-type]
        )


def test_get_returns_only_current_kept_proposals_with_null_definitions():
    _, review, definitions = _services()

    response = _handle(definitions, review, "GET", ROOT)

    assert response.status == HTTPStatus.OK
    assert set(response.payload) == {"poc_id", "proposals"}
    assert response.payload["poc_id"] == POC_ID
    proposals = response.payload["proposals"]
    assert [item["proposal_id"] for item in proposals] == [
        TTFT_PROPOSAL_ID,
        ERROR_PROPOSAL_ID,
    ]
    for proposal in proposals:
        assert set(proposal) == PROPOSAL_FIELDS
        assert proposal["review_state"] == "KEEP_FOR_CONTRACT"
        assert proposal["definition"] is None
    assert proposals[0]["source_kind"] == "DOCUMENT"
    assert proposals[1]["source_kind"] == "MEETING"


def test_post_creates_then_exactly_replays_a_compact_definition():
    _, review, definitions = _services()

    created = _create_ttft(definitions, review)
    replay = _create_ttft(definitions, review)
    listed = _handle(definitions, review, "GET", ROOT)

    assert created.status == HTTPStatus.CREATED
    assert set(created.payload) == {
        "definition",
        "disposition",
        "poc_id",
        "proposal_id",
    }
    assert created.payload["poc_id"] == POC_ID
    assert created.payload["proposal_id"] == TTFT_PROPOSAL_ID
    assert created.payload["disposition"] == "CREATED"
    assert set(created.payload["definition"]) == DEFINITION_FIELDS
    assert created.payload["definition"] == {
        "definition_id": created.payload["definition"]["definition_id"],
        "definition_sha256": created.payload["definition"][
            "definition_sha256"
        ],
        "metric": "TTFT_P95_MS",
        "unit": "MILLISECONDS",
        "operator": "LT",
        "threshold": 500.0,
        "minimum_samples": 100,
        "concurrency": 4,
        "prompt_tokens_min": 512,
        "prompt_tokens_max": 4_096,
        "output_tokens_min": 64,
        "output_tokens_max": 512,
        "defined_at": NOW.isoformat(),
    }
    assert replay.status == HTTPStatus.OK
    assert replay.payload == {
        **created.payload,
        "disposition": "IDEMPOTENT_REPLAY",
    }
    listed_by_id = {
        item["proposal_id"]: item for item in listed.payload["proposals"]
    }
    assert (
        listed_by_id[TTFT_PROPOSAL_ID]["definition"]
        == created.payload["definition"]
    )
    assert listed_by_id[ERROR_PROPOSAL_ID]["definition"] is None


def test_error_rate_definition_derives_percent_unit():
    _, review, definitions = _services()
    body = _body(
        proposal_id=ERROR_PROPOSAL_ID,
        idempotency_key="define-error-rate-web-001",
        metric="ERROR_RATE_PERCENT",
        threshold=1.0,
    )

    response = _handle(definitions, review, "POST", ROOT, body)

    assert response.status == HTTPStatus.CREATED
    assert response.payload["definition"]["metric"] == "ERROR_RATE_PERCENT"
    assert response.payload["definition"]["unit"] == "PERCENT"
    assert response.payload["definition"]["threshold"] == 1.0


def test_public_projection_never_exposes_private_or_downstream_authority():
    _, review, definitions = _services()
    created = _create_ttft(definitions, review)
    listed = _handle(definitions, review, "GET", ROOT)
    serialized = repr((created.payload, listed.payload)).lower()

    for forbidden in (
        "reviewer",
        "rationale",
        "decided_at",
        "decision_sha",
        "proposal_sha",
        "idempotency",
        "approval",
        "confirmation",
        "freeze",
        "execution",
        "evidence",
        "verdict",
    ):
        assert forbidden not in serialized


@pytest.mark.parametrize(
    "payload",
    (
        None,
        {},
        [],
        {"proposal_id": TTFT_PROPOSAL_ID},
        {**_body(), "freeze_contract": True},
        {**_body(), "issue_verdict": "PASS"},
        {key: value for key, value in _body().items() if key != "rationale"},
    ),
)
def test_post_requires_one_exact_object_allowlist(payload: Any):
    _, review, definitions = _services()

    response = _handle(definitions, review, "POST", ROOT, payload)

    assert response.status == HTTPStatus.BAD_REQUEST
    assert response.payload == {
        "error": "Contract definition request is invalid."
    }
    assert definitions.definitions() == ()


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("metric", "LATENCY"),
        ("metric", 1),
        ("operator", "GTE"),
        ("operator", 1),
        ("threshold", True),
        ("threshold", "500"),
        ("threshold", float("nan")),
        ("threshold", float("inf")),
        ("minimum_samples", True),
        ("minimum_samples", 100.0),
        ("concurrency", "4"),
        ("prompt_tokens_min", 0),
        ("prompt_tokens_max", 1_000_001),
        ("output_tokens_min", 0),
        ("output_tokens_max", 1_000_001),
        ("reviewer", 7),
        ("reviewer", ""),
        ("rationale", 7),
        ("rationale", ""),
        ("idempotency_key", 7),
        ("proposal_id", 7),
    ),
)
def test_post_rejects_coercion_nonfinite_values_and_invalid_bounds(
    field: str,
    value: Any,
):
    _, review, definitions = _services()

    response = _handle(
        definitions,
        review,
        "POST",
        ROOT,
        _body(**{field: value}),
    )

    assert response.status == HTTPStatus.BAD_REQUEST
    assert definitions.definitions() == ()


@pytest.mark.parametrize(
    "updates",
    (
        {"threshold": 0.0},
        {"threshold": 60_001.0},
        {"prompt_tokens_min": 513, "prompt_tokens_max": 512},
        {"output_tokens_min": 513, "output_tokens_max": 512},
        {"metric": "ERROR_RATE_PERCENT", "threshold": -0.1},
        {"metric": "ERROR_RATE_PERCENT", "threshold": 100.1},
    ),
)
def test_post_rejects_invalid_metric_and_workload_semantics(updates: dict):
    _, review, definitions = _services()

    response = _handle(
        definitions,
        review,
        "POST",
        ROOT,
        _body(**updates),
    )

    assert response.status == HTTPStatus.BAD_REQUEST
    assert definitions.definitions() == ()


@pytest.mark.parametrize(
    "target",
    (
        f"{ROOT}?include=private",
        f"{ROOT};authority=expanded",
        f"{ROOT}#fragment",
        f"https://example.test{ROOT}",
        f"//example.test{ROOT}",
        f"/api//pocs/{POC_ID}/definitions",
        "/api/pocs/poc_BAD/definitions",
    ),
)
def test_nonlocal_parameterized_and_malformed_targets_fail_closed(target: str):
    _, review, definitions = _services()

    response = _handle(definitions, review, "GET", target)

    assert response.status == HTTPStatus.BAD_REQUEST
    assert response.payload == {
        "error": "Contract definition request is invalid."
    }


@pytest.mark.parametrize(
    "target",
    (
        f"{ROOT}/",
        f"{ROOT}/approve",
        f"{ROOT}/cdef_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    ),
)
def test_unknown_routes_inside_the_namespace_return_not_found(target: str):
    _, review, definitions = _services()

    response = _handle(definitions, review, "GET", target)

    assert response.status == HTTPStatus.NOT_FOUND
    assert response.payload == {
        "error": "Contract definition route was not found."
    }


@pytest.mark.parametrize("method", ("DELETE", "PATCH", "PUT", "get"))
def test_unsupported_methods_are_explicit(method: str):
    _, review, definitions = _services()

    response = _handle(definitions, review, method, ROOT)

    assert response.status == HTTPStatus.METHOD_NOT_ALLOWED
    assert response.payload == {
        "error": "Contract definition method is not allowed."
    }


def test_get_rejects_any_payload():
    _, review, definitions = _services()

    response = _handle(definitions, review, "GET", ROOT, {})

    assert response.status == HTTPStatus.BAD_REQUEST


def test_missing_proposal_is_not_found_without_mutation():
    _, review, definitions = _services()

    response = _handle(
        definitions,
        review,
        "POST",
        ROOT,
        _body(proposal_id="prop_missing_0001"),
    )

    assert response.status == HTTPStatus.NOT_FOUND
    assert response.payload == {
        "error": "Contract definition proposal was not found."
    }
    assert definitions.definitions() == ()


@pytest.mark.parametrize(
    "proposal_id",
    (DISCARDED_PROPOSAL_ID, UNREVIEWED_PROPOSAL_ID),
)
def test_discarded_and_unreviewed_proposals_conflict(proposal_id: str):
    _, review, definitions = _services()

    response = _handle(
        definitions,
        review,
        "POST",
        ROOT,
        _body(proposal_id=proposal_id),
    )

    assert response.status == HTTPStatus.CONFLICT
    assert response.payload == {
        "error": "Contract definition conflicts with the current POC state."
    }
    assert definitions.definitions() == ()


def test_new_key_cannot_overwrite_an_immutable_definition():
    _, review, definitions = _services()
    _create_ttft(definitions, review)

    response = _handle(
        definitions,
        review,
        "POST",
        ROOT,
        _body(
            threshold=250.0,
            idempotency_key="define-ttft-overwrite",
        ),
    )

    assert response.status == HTTPStatus.CONFLICT
    assert len(definitions.definitions()) == 1


def test_idempotency_reuse_for_changed_request_conflicts():
    _, review, definitions = _services()
    _create_ttft(definitions, review)

    response = _handle(
        definitions,
        review,
        "POST",
        ROOT,
        _body(threshold=250.0),
    )

    assert response.status == HTTPStatus.CONFLICT
    assert len(definitions.definitions()) == 1


def test_cross_poc_proposal_identity_is_safely_not_found():
    lookup, review, definitions = _services()
    _handle(definitions, review, "GET", ROOT)
    lookup.by_poc[OTHER_POC_ID] = (
        _proposal(TTFT_PROPOSAL_ID, poc_id=OTHER_POC_ID),
    )

    response = _handle(
        definitions,
        review,
        "GET",
        f"/api/pocs/{OTHER_POC_ID}/definitions",
    )

    assert response.status == HTTPStatus.NOT_FOUND
    assert response.payload == {
        "error": "Contract definition proposal was not found."
    }


def test_stale_proposal_binding_conflicts_without_leaking_details():
    lookup, review, definitions = _services()
    _handle(definitions, review, "GET", ROOT)
    lookup.by_poc[POC_ID] = (
        _proposal(
            TTFT_PROPOSAL_ID,
            source_quote="Changed source binding.",
            normalized_claim="Changed source binding.",
        ),
        *lookup.by_poc[POC_ID][1:],
    )

    response = _handle(definitions, review, "GET", ROOT)

    assert response.status == HTTPStatus.CONFLICT
    assert response.payload == {
        "error": "Contract definition conflicts with the current POC state."
    }


def test_proposal_lookup_failure_is_service_unavailable():
    lookup, review, definitions = _services()
    lookup.failure = RuntimeError("sensitive provider detail")

    response = _handle(definitions, review, "GET", ROOT)

    assert response.status == HTTPStatus.SERVICE_UNAVAILABLE
    assert response.payload == {
        "error": "Contract definition is temporarily unavailable."
    }
    assert "sensitive" not in repr(response.payload).lower()


def test_definition_lookup_failure_is_service_unavailable():
    _, review, _ = _services()

    def unavailable(_poc_id: str):
        raise RuntimeError("sensitive storage detail")

    definitions = ProcessLocalContractDefinitionService(
        proposal_lookup=unavailable,
        clock=lambda: NOW,
    )

    response = _handle(
        definitions,
        review,
        "POST",
        ROOT,
        _body(),
    )

    assert response.status == HTTPStatus.SERVICE_UNAVAILABLE
    assert response.payload == {
        "error": "Contract definition is temporarily unavailable."
    }


def test_definition_capacity_is_service_unavailable_and_atomic():
    _, review, definitions = _services(
        definition_options={"max_definitions": 1}
    )
    _create_ttft(definitions, review)

    response = _handle(
        definitions,
        review,
        "POST",
        ROOT,
        _body(
            proposal_id=ERROR_PROPOSAL_ID,
            idempotency_key="define-error-at-capacity",
            metric="ERROR_RATE_PERCENT",
            threshold=1.0,
        ),
    )

    assert response.status == HTTPStatus.SERVICE_UNAVAILABLE
    assert len(definitions.definitions()) == 1
