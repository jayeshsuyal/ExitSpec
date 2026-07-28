from datetime import datetime, timezone
from http import HTTPStatus

import pytest

from exitspec.poc_creation import (
    DraftPOCCreateRequest,
    ProcessLocalDraftPOCService,
)
from exitspec.poc_source_intake import ProcessLocalPOCSourceIntake
from exitspec.poc_source_web_api import (
    handle_poc_source_web_api_request,
    is_poc_source_web_api_target,
)


NOW = datetime(2026, 7, 28, 22, 0, tzinfo=timezone.utc)
POC_ID = "poc_source_web_alpha"
ROOT = f"/api/pocs/{POC_ID}/sources"


def _runtime() -> ProcessLocalPOCSourceIntake:
    drafts = ProcessLocalDraftPOCService(
        clock=lambda: NOW,
        poc_id_factory=lambda: POC_ID,
    )
    drafts.create(
        DraftPOCCreateRequest(
            display_name="Inference validation",
            customer_label="Northstar",
            use_case="Validate one bounded performance claim.",
            owner="field_engineer",
            first_source_choice="DOCUMENT",
        ),
        idempotency_key="create-source-web-alpha",
    )
    return ProcessLocalPOCSourceIntake(
        draft_lookup=drafts.get,
        clock=lambda: NOW,
    )


def _handle(
    runtime: ProcessLocalPOCSourceIntake,
    method: str,
    target: str,
    payload=None,
):
    response = handle_poc_source_web_api_request(
        method=method,
        target=target,
        payload=payload,
        runtime=runtime,
    )
    assert response is not None
    return response


def test_unrelated_targets_are_not_claimed():
    runtime = _runtime()

    assert is_poc_source_web_api_target("/api/pocs") is False
    assert is_poc_source_web_api_target(f"/api/pocs/{POC_ID}") is False
    assert (
        handle_poc_source_web_api_request(
            method="GET",
            target=f"/api/pocs/{POC_ID}",
            payload=None,
            runtime=runtime,
        )
        is None
    )


def test_empty_source_list_is_read_only_and_exact():
    runtime = _runtime()

    response = _handle(runtime, "GET", ROOT)

    assert response.status == HTTPStatus.OK
    assert response.payload == {"poc_id": POC_ID, "sources": []}
    assert _handle(runtime, "GET", ROOT).payload == response.payload


@pytest.mark.parametrize(
    ("route", "field", "value", "expected_kind"),
    (
        (
            "meeting",
            "transcript_text",
            "Customer: p95 latency must stay below 500 ms.",
            "MEETING",
        ),
        (
            "document",
            "document_text",
            "The error rate must remain below 1%.",
            "DOCUMENT",
        ),
    ),
)
def test_capture_returns_only_the_safe_six_field_receipt(
    route,
    field,
    value,
    expected_kind,
):
    runtime = _runtime()
    response = _handle(
        runtime,
        "POST",
        f"{ROOT}/{route}",
        {
            field: value,
            "idempotency_key": f"capture-{route}",
        },
    )

    assert response.status == HTTPStatus.CREATED
    assert set(response.payload) == {
        "poc_id",
        "source_kind",
        "source_receipt_id",
        "proposal_count",
        "status",
        "idempotent_replay",
    }
    assert response.payload["poc_id"] == POC_ID
    assert response.payload["source_kind"] == expected_kind
    assert response.payload["source_receipt_id"].startswith("srcpt_")
    assert response.payload["proposal_count"] == 1
    assert response.payload["status"] == "NEEDS_REVIEW"
    assert response.payload["idempotent_replay"] is False
    serialized = repr(response.payload).lower()
    for forbidden in (
        "latency must",
        "error rate must",
        "approved",
        "confirmation",
        "freeze",
        "verdict",
        "pass",
    ):
        assert forbidden not in serialized


def test_exact_retry_replays_without_a_second_source():
    runtime = _runtime()
    payload = {
        "document_text": "The error rate must remain below 1%.",
        "idempotency_key": "capture-document-replay",
    }

    first = _handle(runtime, "POST", f"{ROOT}/document", payload)
    replay = _handle(runtime, "POST", f"{ROOT}/document", payload)
    listed = _handle(runtime, "GET", ROOT)

    assert first.status == HTTPStatus.CREATED
    assert replay.status == HTTPStatus.OK
    assert replay.payload == {
        **first.payload,
        "idempotent_replay": True,
    }
    assert len(listed.payload["sources"]) == 1
    assert listed.payload["sources"][0]["source_receipt_id"] == (
        first.payload["source_receipt_id"]
    )


@pytest.mark.parametrize(
    "payload",
    (
        {},
        {"document_text": "Requirement only."},
        {"idempotency_key": "key-only"},
        {
            "document_text": "Requirement only.",
            "idempotency_key": "key",
            "approve": True,
        },
        {
            "document_text": "Requirement only.",
            "idempotency_key": "key",
            "endpoint": "https://evil.test",
        },
    ),
)
def test_payload_cannot_expand_source_or_workflow_authority(payload):
    runtime = _runtime()

    response = _handle(runtime, "POST", f"{ROOT}/document", payload)

    assert response.status == HTTPStatus.BAD_REQUEST
    assert response.payload == {"error": "Source intake request is invalid."}
    assert _handle(runtime, "GET", ROOT).payload["sources"] == []


@pytest.mark.parametrize(
    "target",
    (
        f"{ROOT}?include=raw",
        f"{ROOT};adapter=provider",
        f"{ROOT}/document?approve=true",
        f"{ROOT}/document#fragment",
        "/api/pocs/poc_BAD/sources",
        "/api/pocs/not-a-poc/sources/document",
    ),
)
def test_parameters_and_malformed_poc_identity_fail_closed(target):
    runtime = _runtime()

    response = _handle(runtime, "GET", target)

    assert response.status == HTTPStatus.BAD_REQUEST
    assert response.payload == {"error": "Source intake request is invalid."}


def test_unknown_route_and_method_are_explicit():
    runtime = _runtime()

    unknown = _handle(
        runtime,
        "POST",
        f"{ROOT}/provider",
        {"idempotency_key": "no-provider"},
    )
    method = _handle(runtime, "DELETE", ROOT)

    assert unknown.status == HTTPStatus.NOT_FOUND
    assert unknown.payload == {"error": "Source intake route was not found."}
    assert method.status == HTTPStatus.METHOD_NOT_ALLOWED
    assert method.payload == {"error": "Source intake method is not allowed."}


def test_missing_poc_and_invalid_content_have_distinct_safe_statuses():
    runtime = _runtime()

    missing = _handle(
        runtime,
        "GET",
        "/api/pocs/poc_missing_source_web/sources",
    )
    invalid = _handle(
        runtime,
        "POST",
        f"{ROOT}/meeting",
        {
            "transcript_text": "",
            "idempotency_key": "invalid-transcript",
        },
    )

    assert missing.status == HTTPStatus.NOT_FOUND
    assert missing.payload == {"error": "The source input was not accepted."}
    assert invalid.status == HTTPStatus.UNPROCESSABLE_ENTITY
    assert invalid.payload == {"error": "The source input was not accepted."}


def test_unapproved_synthetic_fixture_is_not_found_without_reflection():
    runtime = _runtime()

    response = _handle(
        runtime,
        "POST",
        f"{ROOT}/email",
        {
            "fixture_case_id": "private-customer-secret",
            "idempotency_key": "unapproved-fixture",
        },
    )

    assert response.status == HTTPStatus.NOT_FOUND
    assert response.payload == {
        "error": "The approved synthetic source was not found."
    }
    assert "private-customer-secret" not in repr(response.payload)
    assert _handle(runtime, "GET", ROOT).payload["sources"] == []
