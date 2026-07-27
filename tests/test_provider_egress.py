import inspect
import json
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from exitspec.assisted_authoring import _provider_request
from exitspec.intake import redact_and_parse_pasted_transcript
from exitspec.provider_egress import (
    DEFAULT_EGRESS_ACKNOWLEDGEMENT_TTL,
    AuthorizedProviderRequest,
    EgressRejectionReason,
    InMemoryProviderEgressAuthorizer,
    ProviderEgressAcknowledgementError,
    ProviderEgressIntentError,
    ProviderEgressPolicy,
    ProviderEgressPolicyError,
    WAVE1_POLICY_IDENTITY_SHA256,
    build_provider_egress_intent,
    provider_egress_binding_payload,
)
from exitspec.providers import ProviderMessage


FIXED_TIME = datetime(2026, 7, 27, 17, 0, tzinfo=timezone.utc)
RAW_SECRET = "fw_live_PROVIDER_EGRESS_SECRET_123456"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = (
    PROJECT_ROOT
    / "examples/support-agent/fireworks/wave-1-acceptance-v1.json"
)
WAVE1_MANIFEST = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
FIXTURE = json.loads(
    (
        PROJECT_ROOT / WAVE1_MANIFEST["source_fixture"]["path"]
    ).read_text(encoding="utf-8")
)
POLICY = ProviderEgressPolicy.from_frozen_manifest(WAVE1_MANIFEST)


class MutableClock:
    def __init__(self, value=FIXED_TIME):
        self.value = value

    def __call__(self):
        return self.value


def _approved_case():
    source_case_id = WAVE1_MANIFEST["approved_live_smoke_request"][
        "source_case_id"
    ]
    return next(case for case in FIXTURE["cases"] if case["id"] == source_case_id)


def _approved_request():
    case = _approved_case()
    intake = redact_and_parse_pasted_transcript(
        case["transcript"],
        transcript_id=case["id"],
        title=case["title"],
        customer_terms=case["customer_terms"],
    )
    request, _ = _provider_request(
        intake,
        model=POLICY.model,
        customer_terms=case["customer_terms"],
    )
    return replace(request, budget_usd=POLICY.max_request_cost_usd)


def _authorizer(clock=None):
    return InMemoryProviderEgressAuthorizer(
        POLICY,
        clock=clock or MutableClock(),
        nonce_factory=lambda: "synthetic-nonce",
        capability_secret_factory=lambda: "synthetic-capability-secret",
    )


def _issue(authorizer, request=None):
    return authorizer.issue(
        request or _approved_request(),
        acknowledged=True,
    )


def _changed_message_request(content):
    request = _approved_request()
    return replace(
        request,
        messages=(
            request.messages[0],
            ProviderMessage(role="user", content=content),
        ),
    )


def test_policy_is_an_exact_detached_projection_of_the_frozen_manifest():
    boundary = WAVE1_MANIFEST["provider_boundary"]
    approved = WAVE1_MANIFEST["approved_live_smoke_request"]
    source = WAVE1_MANIFEST["source_fixture"]

    assert POLICY.acceptance_manifest_id == WAVE1_MANIFEST["manifest_id"]
    assert POLICY.acceptance_manifest_version == WAVE1_MANIFEST[
        "manifest_version"
    ]
    assert POLICY.source_fixture_sha256 == source["sha256"]
    assert POLICY.source_case_id == approved["source_case_id"]
    assert POLICY.redacted_payload_digest == approved[
        "redacted_payload_digest"
    ]
    assert POLICY.redaction_configuration_digest == approved[
        "redaction_configuration_digest"
    ]
    assert POLICY.provider == boundary["provider"]
    assert POLICY.model == boundary["model"]
    assert POLICY.endpoint == boundary["endpoint"]
    assert POLICY.data_policy_snapshot() == boundary["data_policy_snapshot"]
    assert POLICY.pricing_snapshot() == boundary["pricing_snapshot"]
    assert POLICY.request_limits() == boundary["request_limits"]
    assert POLICY.max_request_cost_usd == Decimal("0.01")
    assert POLICY.is_frozen_wave1_policy()
    assert len(WAVE1_POLICY_IDENTITY_SHA256) == 64

    detached = POLICY.data_policy_snapshot()
    detached["region"] = "changed"
    assert POLICY.data_policy_snapshot() == boundary["data_policy_snapshot"]

    with pytest.raises(ValidationError, match="Instance is frozen"):
        POLICY.endpoint = "https://provider.example.test/v1/chat/completions"


def test_policy_loader_rejects_unfrozen_or_credential_bearing_input_safely():
    unfrozen = deepcopy(WAVE1_MANIFEST)
    unfrozen["status"] = "DRAFT"
    with pytest.raises(
        ProviderEgressPolicyError,
        match="Frozen provider egress policy is invalid",
    ):
        ProviderEgressPolicy.from_frozen_manifest(unfrozen)

    credentialed = deepcopy(WAVE1_MANIFEST)
    credentialed["provider_boundary"]["endpoint"] = (
        f"https://operator:{RAW_SECRET}@api.fireworks.ai/"
        "inference/v1/chat/completions"
    )
    with pytest.raises(ProviderEgressPolicyError) as error:
        ProviderEgressPolicy.from_frozen_manifest(credentialed)
    assert RAW_SECRET not in str(error.value)
    assert "operator:" not in str(error.value)

    changed_destination = deepcopy(WAVE1_MANIFEST)
    changed_destination["provider_boundary"]["endpoint"] = (
        "https://provider.example.test/v1/chat/completions"
    )
    with pytest.raises(ProviderEgressPolicyError):
        ProviderEgressPolicy.from_frozen_manifest(changed_destination)

    model_copy = POLICY.model_copy(
        update={
            "endpoint": "https://provider.example.test/v1/chat/completions"
        }
    )
    assert not model_copy.is_frozen_wave1_policy()
    with pytest.raises(ValueError, match="trusted frozen policy"):
        InMemoryProviderEgressAuthorizer(model_copy)


def test_exact_approved_request_builds_a_content_free_operator_preview():
    request = _approved_request()
    intent = build_provider_egress_intent(request, policy=POLICY)
    preview = intent.public_preview()

    assert preview["redacted_payload_digest"] == WAVE1_MANIFEST[
        "approved_live_smoke_request"
    ]["redacted_payload_digest"]
    assert preview["provider"] == "fireworks"
    assert preview["endpoint"] == (
        "https://api.fireworks.ai/inference/v1/chat/completions"
    )
    assert preview["max_request_cost_usd"] == "0.01"
    assert preview["request_limits"] == WAVE1_MANIFEST["provider_boundary"][
        "request_limits"
    ]
    assert _approved_case()["transcript"] not in repr(intent)
    assert RAW_SECRET not in repr(intent)


@pytest.mark.parametrize(
    "changed_request",
    (
        _changed_message_request(
            "Untrusted redacted transcript JSON follows:\n"
            '{"lines":[{"line_number":1,"quote":"A different clean request.",'
            '"speaker":"Customer"}]}'
        ),
        replace(
            _approved_request(),
            model="accounts/fireworks/models/another-model",
        ),
        replace(_approved_request(), max_output_tokens=1000),
        replace(_approved_request(), estimated_input_tokens=1),
        replace(_approved_request(), budget_usd=Decimal("0.009")),
        replace(_approved_request(), timeout_seconds=29),
    ),
)
def test_any_request_change_is_rejected_by_the_frozen_policy(changed_request):
    with pytest.raises(ProviderEgressIntentError):
        build_provider_egress_intent(changed_request, policy=POLICY)


def test_arbitrary_clean_source_and_changed_redaction_config_are_not_approved():
    arbitrary_clean = _changed_message_request(
        "Untrusted redacted transcript JSON follows:\n"
        '{"lines":[{"line_number":1,"quote":"Reach 99% across 500 cases.",'
        '"speaker":"Customer"}]}'
    )
    with pytest.raises(
        ProviderEgressIntentError,
        match="approved synthetic live-smoke payload",
    ):
        build_provider_egress_intent(arbitrary_clean, policy=POLICY)

    with pytest.raises(
        ProviderEgressIntentError,
        match="redaction configuration is not approved",
    ):
        build_provider_egress_intent(
            _approved_request(),
            policy=POLICY,
            customer_terms=("Acme",),
        )


def test_sensitive_values_in_any_request_field_fail_without_echoing_them():
    request = _approved_request()
    secret_request = replace(
        request,
        messages=(
            request.messages[0],
            ProviderMessage(
                role="user",
                content=f"api_key={RAW_SECRET}",
            ),
        ),
    )
    with pytest.raises(ProviderEgressIntentError) as error:
        build_provider_egress_intent(secret_request, policy=POLICY)
    assert RAW_SECRET not in str(error.value)

    secret_schema = request.response_schema_payload()
    secret_schema["description"] = f"api_key={RAW_SECRET}"
    secret_schema_request = replace(request, response_schema=secret_schema)
    with pytest.raises(ProviderEgressIntentError) as error:
        build_provider_egress_intent(secret_schema_request, policy=POLICY)
    assert RAW_SECRET not in str(error.value)


def test_binding_payload_covers_every_frozen_manifest_field():
    intent = build_provider_egress_intent(_approved_request(), policy=POLICY)
    payload = provider_egress_binding_payload(
        intent,
        issued_at=FIXED_TIME,
        expires_at=FIXED_TIME + DEFAULT_EGRESS_ACKNOWLEDGEMENT_TTL,
        nonce="synthetic-nonce",
    )

    assert set(payload) == set(
        WAVE1_MANIFEST["egress_acknowledgement"]["bound_fields"]
    )
    assert payload["acceptance_manifest_id"] == WAVE1_MANIFEST["manifest_id"]
    assert payload["source_case_id"] == POLICY.source_case_id
    assert payload["data_policy_snapshot"] == POLICY.data_policy_snapshot()
    assert payload["pricing_snapshot"] == POLICY.pricing_snapshot()
    assert payload["request_limits"] == POLICY.request_limits()
    assert payload["max_request_cost_usd"] == "0.01"


def test_issue_requires_explicit_acknowledgement_and_owns_policy_time_and_randomness():
    authorizer = _authorizer()
    with pytest.raises(
        ProviderEgressAcknowledgementError,
        match="explicit acknowledgement",
    ) as error:
        authorizer.issue(
            _approved_request(),
            acknowledged=False,
        )
    assert error.value.code == "egress_not_authorized"
    assert error.value.reason == EgressRejectionReason.NOT_ACKNOWLEDGED
    assert error.value.retryable is False
    assert error.value.next_action == "reauthorize_provider_egress"

    issue_parameters = inspect.signature(authorizer.issue).parameters
    assert "policy" not in issue_parameters
    assert "provider" not in issue_parameters
    assert "endpoint" not in issue_parameters
    assert "issued_at" not in issue_parameters
    assert "nonce" not in issue_parameters
    assert "token_secret" not in issue_parameters

    record, token = _issue(authorizer)
    assert token.startswith(record.acknowledgement_id + ".")
    assert record.expires_at - record.issued_at == timedelta(minutes=5)
    assert record.intent.endpoint == POLICY.endpoint


def test_public_record_and_permit_never_serialize_verifier_nonce_or_request():
    authorizer = _authorizer()
    request = _approved_request()
    record, token = _issue(authorizer, request)
    permit = authorizer.authorize(token, request)

    public_json = json.dumps(
        record.model_dump(mode="json"),
        sort_keys=True,
    )
    combined = public_json + repr(record) + repr(permit)
    assert token not in combined
    assert "synthetic-capability-secret" not in combined
    assert "synthetic-nonce" not in combined
    assert "token_digest" not in combined
    assert _approved_case()["transcript"] not in combined
    assert "request=<redacted>" in repr(permit)
    assert not hasattr(permit, "model_dump")


def test_authorization_returns_only_a_detached_one_use_exact_request():
    authorizer = _authorizer()
    request = _approved_request()
    original_schema = request.response_schema_payload()
    record, token = _issue(authorizer, request)

    permit = authorizer.authorize(token, request)
    assert isinstance(permit, AuthorizedProviderRequest)
    assert permit.acknowledgement is record
    assert authorizer.is_consumed(record.acknowledgement_id)
    request.response_schema["description"] = "caller mutation after authorization"

    authorized_request = permit.take_request()
    assert authorized_request is not request
    assert authorized_request.response_schema_payload() == original_schema
    assert authorized_request.messages == request.messages
    assert permit.is_taken

    with pytest.raises(
        ProviderEgressAcknowledgementError,
        match="already taken",
    ) as error:
        permit.take_request()
    assert error.value.reason == EgressRejectionReason.REPLAYED

    with pytest.raises(
        ProviderEgressAcknowledgementError,
        match="already used",
    ) as error:
        authorizer.authorize(token, _approved_request())
    assert error.value.reason == EgressRejectionReason.REPLAYED


def test_transport_permit_cannot_be_constructed_outside_authorization():
    authorizer = _authorizer()
    request = _approved_request()
    record, _ = _issue(authorizer, request)

    with pytest.raises(
        ProviderEgressAcknowledgementError,
        match="permit is invalid",
    ) as error:
        AuthorizedProviderRequest(record, request)
    assert error.value.code == "egress_not_authorized"
    assert error.value.reason == EgressRejectionReason.INVALID


def test_transport_must_take_the_permit_before_acknowledgement_expiry():
    clock = MutableClock()
    authorizer = _authorizer(clock)
    request = _approved_request()
    _, token = _issue(authorizer, request)
    clock.value = (
        FIXED_TIME
        + DEFAULT_EGRESS_ACKNOWLEDGEMENT_TTL
        - timedelta(seconds=1)
    )
    permit = authorizer.authorize(token, request)

    clock.value = FIXED_TIME + DEFAULT_EGRESS_ACKNOWLEDGEMENT_TTL
    with pytest.raises(
        ProviderEgressAcknowledgementError,
        match="expired before transport",
    ) as error:
        permit.take_request()
    assert error.value.code == "egress_not_authorized"
    assert error.value.reason == EgressRejectionReason.EXPIRED
    assert permit.is_taken


def test_changed_request_cannot_consume_then_swap_an_acknowledgement():
    authorizer = _authorizer()
    approved = _approved_request()
    record, token = _issue(authorizer, approved)
    changed = _changed_message_request(
        "Untrusted redacted transcript JSON follows:\n"
        '{"lines":[{"line_number":1,"quote":"Different clean payload.",'
        '"speaker":"Customer"}]}'
    )

    with pytest.raises(
        ProviderEgressAcknowledgementError,
        match="not authorized by frozen policy",
    ) as error:
        authorizer.authorize(token, changed)
    assert error.value.code == "egress_not_authorized"
    assert error.value.reason == EgressRejectionReason.INTENT_MISMATCH
    assert not authorizer.is_consumed(record.acknowledgement_id)

    permit = authorizer.authorize(token, approved)
    assert permit.take_request().messages == approved.messages


def test_expired_invalid_and_preissued_acknowledgements_fail_safely():
    clock = MutableClock()
    authorizer = _authorizer(clock)
    request = _approved_request()
    _, token = _issue(authorizer, request)

    clock.value = FIXED_TIME + DEFAULT_EGRESS_ACKNOWLEDGEMENT_TTL
    with pytest.raises(
        ProviderEgressAcknowledgementError,
        match="expired",
    ) as error:
        authorizer.authorize(token, request)
    assert error.value.reason == EgressRejectionReason.EXPIRED
    assert token not in str(error.value)

    clock.value = FIXED_TIME - timedelta(seconds=1)
    with pytest.raises(
        ProviderEgressAcknowledgementError,
        match="not valid yet",
    ) as error:
        authorizer.authorize(token, request)
    assert error.value.reason == EgressRejectionReason.INVALID

    for invalid_token in (
        None,
        "",
        "missing-separator",
        token + "-changed",
        "x" * 2049 + ".secret",
    ):
        with pytest.raises(ProviderEgressAcknowledgementError) as error:
            authorizer.authorize(invalid_token, request)
        assert error.value.code == "egress_not_authorized"
        assert error.value.reason == EgressRejectionReason.INVALID
        if isinstance(invalid_token, str) and invalid_token:
            assert invalid_token not in str(error.value)


def test_clock_and_malformed_request_fail_as_typed_sanitized_denials():
    bad_clock = MutableClock("not-a-time")
    authorizer = _authorizer(bad_clock)
    with pytest.raises(ProviderEgressAcknowledgementError) as error:
        _issue(authorizer)
    assert error.value.code == "egress_not_authorized"
    assert error.value.reason == EgressRejectionReason.INVALID
    assert "not-a-time" not in str(error.value)

    good_authorizer = _authorizer()
    _, token = _issue(good_authorizer)
    with pytest.raises(ProviderEgressAcknowledgementError) as error:
        good_authorizer.authorize(token, object())
    assert error.value.code == "egress_not_authorized"
    assert error.value.reason == EgressRejectionReason.INTENT_MISMATCH


def test_concurrent_authorization_has_exactly_one_winner():
    authorizer = _authorizer()
    request = _approved_request()
    _, token = _issue(authorizer, request)

    def authorize_once():
        try:
            authorizer.authorize(token, request)
            return "accepted"
        except ProviderEgressAcknowledgementError as error:
            return error.reason.value

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: authorize_once(), range(2)))

    assert sorted(outcomes) == ["accepted", "replayed"]


def test_ttl_is_fixed_at_server_construction_and_public_records_are_frozen():
    with pytest.raises(ValueError, match="no longer than five minutes"):
        InMemoryProviderEgressAuthorizer(
            POLICY,
            ttl=timedelta(minutes=5, seconds=1),
        )

    authorizer = _authorizer()
    record, _ = _issue(authorizer)
    with pytest.raises(ValidationError, match="Instance is frozen"):
        record.expires_at = FIXED_TIME + timedelta(hours=1)
