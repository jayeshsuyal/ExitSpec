import json

import pytest

import exitspec.assisted_authoring as assisted_authoring
from exitspec.assisted_authoring import (
    AssistedAuthoringError,
    ExactToolSelectionPolicy,
    build_assisted_discovery_pack,
)
from exitspec.models import DraftStatus, Metric
from exitspec.providers import (
    FireworksProvider,
    ProviderHTTPRequest,
    ProviderHTTPResponse,
)


MODEL = "accounts/fireworks/models/assisted-authoring-test-v1"
FIREWORKS_API_KEY = "fw_test_transport_credential"
RAW_EMAIL = "jayesh.suyal@example.com"
RAW_API_TOKEN = "sk-proj_ABCdef1234567890"
RAW_CUSTOMER_TERM = "Project Phoenix"
ALLOWED_FACT_FIELDS = {
    "line_number",
    "speaker",
    "quote",
    "title",
    "normalized_claim",
    "classification",
    "threshold",
    "minimum_samples",
    "open_questions",
}
FORBIDDEN_AUTHORITY_FIELDS = {
    "id",
    "criterion_id",
    "metric",
    "metric_choice",
    "approved",
    "status",
    "review",
    "reviewer",
    "hash",
    "canonical_hash",
    "verdict",
    "adapter",
    "adapter_version",
    "workload",
    "workload_slice",
    "owner",
    "evidence_policy",
    "confidence",
    "confidence_level",
    "confidence_method",
}


class FakeTransport:
    def __init__(self, response, *, events=None):
        self.response = response
        self.events = events
        self.requests = []

    def send(self, request):
        assert isinstance(request, ProviderHTTPRequest)
        if self.events is not None:
            self.events.append("transport")
        self.requests.append(request)
        return self.response


def _response(payload, *, request_id="fw-safe-request-id"):
    return ProviderHTTPResponse(
        status_code=200,
        headers={"X-Request-ID": request_id},
        body=json.dumps(
            {
                "id": "body-request-id",
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(payload),
                        }
                    }
                ],
                "usage": {
                    "prompt_tokens": 120,
                    "completion_tokens": 80,
                    "total_tokens": 200,
                },
            }
        ),
    )


def _provider(response, *, events=None):
    transport = FakeTransport(response, events=events)
    provider = FireworksProvider(
        transport=transport,
        api_key=FIREWORKS_API_KEY,
        max_attempts=1,
    )
    return provider, transport


def _policy():
    return ExactToolSelectionPolicy(
        workload_slice="support-routing-v1",
        adapter="deterministic-tool-selection",
        adapter_version="1.4.2",
        owner="solutions-engineering",
        evidence_policy="retain-summary-30-days",
        unit="successful selections / eligible cases",
        aggregation="exact-match proportion over the pinned slice",
        must_have=True,
        confidence_level=0.97,
    )


def _sensitive_transcript():
    return (
        "Customer: Exact tool selection must be at least 95% over 200 cases.\n"
        "Field Engineer: Tool selection should usually be reliable.\n"
        "Customer: Contact {0} with api_key={1} about {2}."
    ).format(RAW_EMAIL, RAW_API_TOKEN, RAW_CUSTOMER_TERM)


def _measurable_proposal():
    return {
        "line_number": 1,
        "speaker": "Customer",
        "quote": "Exact tool selection must be at least 95% over 200 cases.",
        "title": "Exact tool selection",
        "normalized_claim": (
            "Exact tool selection is at least 95% over at least 200 cases."
        ),
        "classification": "measurable",
        "threshold": 0.95,
        "minimum_samples": 200,
        "open_questions": [],
    }


def _vague_proposal():
    return {
        "line_number": 2,
        "speaker": "Field Engineer",
        "quote": "Tool selection should usually be reliable.",
        "title": "Reliable tool selection",
        "normalized_claim": "Tool selection should usually be reliable.",
        "classification": "vague",
        "threshold": None,
        "minimum_samples": None,
        "open_questions": [
            "What proportion and sample count make tool selection reliable?"
        ],
    }


def _build(provider, *, raw_transcript=None):
    if raw_transcript is None:
        raw_transcript = _sensitive_transcript()
    return build_assisted_discovery_pack(
        raw_transcript,
        executor=provider,
        model=MODEL,
        policy=_policy(),
        customer_terms=[RAW_CUSTOMER_TERM],
        transcript_id="assisted-security-test",
        title="Assisted security test",
    )


def _property_names(schema):
    names = set()
    if isinstance(schema, dict):
        properties = schema.get("properties")
        if isinstance(properties, dict):
            names.update(properties)
        for value in schema.values():
            names.update(_property_names(value))
    elif isinstance(schema, list):
        for value in schema:
            names.update(_property_names(value))
    return names


def test_happy_batch_is_redaction_first_source_exact_and_review_only(monkeypatch):
    events = []
    original_egress = assisted_authoring.assert_redaction_egress

    def tracking_egress(result, *, customer_terms=()):
        if result.redacted_text.startswith(
            "Untrusted redacted transcript JSON follows:"
        ):
            events.append("request-egress")
        else:
            events.append("output-egress")
        return original_egress(result, customer_terms=customer_terms)

    monkeypatch.setattr(
        assisted_authoring,
        "assert_redaction_egress",
        tracking_egress,
    )
    malicious_request_id = "{0}|{1}|{2}".format(
        RAW_EMAIL,
        RAW_API_TOKEN,
        RAW_CUSTOMER_TERM,
    )
    provider, transport = _provider(
        _response(
            {
                "proposals": [
                    _measurable_proposal(),
                    _vague_proposal(),
                ]
            },
            request_id=malicious_request_id,
        ),
        events=events,
    )

    result = _build(provider)

    assert events == ["request-egress", "transport", "output-egress"]
    assert len(transport.requests) == 1
    sent = transport.requests[0]
    sent_material = json.dumps(
        {
            "method": sent.method,
            "url": sent.url,
            "headers": dict(sent.headers),
            "json_body": sent.json_body,
            "timeout_seconds": sent.timeout_seconds,
        },
        sort_keys=True,
    )
    for sensitive_value in (RAW_EMAIL, RAW_API_TOKEN, RAW_CUSTOMER_TERM):
        assert sensitive_value not in sent_material
        assert sensitive_value not in repr(sent)
        assert sensitive_value not in str(sent)
    assert "[REDACTED:EMAIL]" in sent_material
    assert "[REDACTED:API_TOKEN]" in sent_material
    assert "[REDACTED:CUSTOMER_TERM]" in sent_material

    messages = sent.json_body["messages"]
    assert [message["role"] for message in messages] == ["system", "user"]
    system_prompt = messages[0]["content"].lower()
    for authority_term in (
        "approve",
        "freeze",
        "select policy",
        "verdict",
        "reviewer",
        "canonical hash",
    ):
        assert authority_term not in system_prompt
    user_payload = json.loads(messages[1]["content"].split("\n", 1)[1])
    assert set(user_payload) == {"lines"}
    assert all(
        set(line) == {"line_number", "speaker", "quote"}
        for line in user_payload["lines"]
    )

    response_schema = sent.json_body["response_format"]["json_schema"]["schema"]
    assert set(response_schema["properties"]) == {"proposals"}
    assert response_schema["additionalProperties"] is False
    fact_schema = response_schema["$defs"]["ProposalFacts"]
    assert set(fact_schema["properties"]) == ALLOWED_FACT_FIELDS
    assert fact_schema["additionalProperties"] is False
    assert not FORBIDDEN_AUTHORITY_FIELDS.intersection(_property_names(response_schema))

    measurable, vague = result.discovery_pack.drafts
    assert measurable.id == "DRAFT-TOOL-SELECT-01"
    assert measurable.status == DraftStatus.NEEDS_REVIEW
    assert measurable.review is None
    assert measurable.open_questions == []
    assert measurable.source_span is not None
    assert measurable.source_span.quote == _measurable_proposal()["quote"]
    criterion = measurable.proposed_criterion
    assert criterion is not None
    assert criterion.id == "TOOL-SELECT-01"
    assert criterion.approved is False
    assert criterion.metric == Metric.EXACT_TOOL_SELECTION_RATE
    assert criterion.rule.threshold == 0.95
    assert criterion.rule.minimum_samples == 200
    assert criterion.rule.confidence_level == 0.97
    assert criterion.workload_slice == _policy().workload_slice
    assert criterion.adapter == _policy().adapter
    assert criterion.adapter_version == _policy().adapter_version
    assert criterion.owner == _policy().owner
    assert criterion.evidence_policy == _policy().evidence_policy

    assert vague.id == "DRAFT-TOOL-SELECT-02"
    assert vague.status == DraftStatus.NEEDS_REVIEW
    assert vague.review is None
    assert vague.proposed_criterion is None
    assert vague.open_questions == _vague_proposal()["open_questions"]

    assert result.receipt.provider_request_id is None
    assert result.receipt.provider == "fireworks"
    assert result.receipt.model == MODEL
    assert result.redaction.counts["EMAIL"] == 1
    assert result.redaction.counts["API_TOKEN"] == 1
    assert result.redaction.counts["CUSTOMER_TERM"] == 1
    returned_renderings = (
        result.discovery_pack.model_dump_json(),
        result.redaction.model_dump_json(),
        repr(result),
        str(result),
        repr(result.discovery_pack),
        str(result.discovery_pack),
        repr(result.redaction),
        str(result.redaction),
        repr(result.receipt),
        str(result.receipt),
    )
    for rendering in returned_renderings:
        for sensitive_value in (RAW_EMAIL, RAW_API_TOKEN, RAW_CUSTOMER_TERM):
            assert sensitive_value not in rendering


@pytest.mark.parametrize(
    "authority_field",
    sorted(FORBIDDEN_AUTHORITY_FIELDS),
)
def test_provider_schema_rejects_every_authority_field_before_typed_conversion(
    authority_field,
    monkeypatch,
):
    proposal = _measurable_proposal()
    proposal[authority_field] = "attacker-controlled-authority"
    validator_inputs = []
    original_validator = assisted_authoring._validate_provider_output

    def tracking_validator(payload):
        validator_inputs.append(payload)
        return original_validator(payload)

    monkeypatch.setattr(
        assisted_authoring,
        "_validate_provider_output",
        tracking_validator,
    )
    provider, transport = _provider(_response({"proposals": [proposal]}))

    with pytest.raises(AssistedAuthoringError) as captured:
        _build(provider)

    assert validator_inputs == []
    assert len(transport.requests) == 1
    assert str(captured.value) == (
        "Provider-assisted discovery could not be completed."
    )
    assert "attacker-controlled-authority" not in str(captured.value)
    assert "attacker-controlled-authority" not in repr(captured.value)
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert captured.value.__suppress_context__ is True


@pytest.mark.parametrize(
    ("field", "mismatch"),
    [
        ("line_number", 4),
        ("speaker", "Unmatched Speaker"),
        ("quote", "Altered source text must not be repaired."),
    ],
)
def test_source_line_speaker_and_quote_must_match_exactly(field, mismatch):
    proposal = _measurable_proposal()
    proposal[field] = mismatch
    provider, transport = _provider(_response({"proposals": [proposal]}))

    with pytest.raises(AssistedAuthoringError) as captured:
        _build(provider)

    assert len(transport.requests) == 1
    assert str(captured.value) == (
        "Provider proposal source did not exactly match the redacted transcript."
    )
    assert str(mismatch) not in str(captured.value)
    assert str(mismatch) not in repr(captured.value)


def test_measurable_but_incomplete_fact_stays_open_without_a_criterion():
    proposal = _measurable_proposal()
    proposal["threshold"] = None
    proposal["minimum_samples"] = None
    provider, _ = _provider(_response({"proposals": [proposal]}))

    result = _build(provider)

    draft = result.discovery_pack.drafts[0]
    assert draft.status == DraftStatus.NEEDS_REVIEW
    assert draft.proposed_criterion is None
    assert draft.open_questions == [
        "What proportion threshold defines acceptance?",
        "What minimum sample count is required?",
    ]


def test_provider_cannot_reintroduce_sensitive_values_in_proposal_facts():
    proposal = _measurable_proposal()
    proposal["title"] = RAW_EMAIL
    proposal["normalized_claim"] = RAW_API_TOKEN
    proposal["open_questions"] = [RAW_CUSTOMER_TERM]
    provider, transport = _provider(_response({"proposals": [proposal]}))

    with pytest.raises(AssistedAuthoringError) as captured:
        _build(provider)

    assert len(transport.requests) == 1
    for sensitive_value in (RAW_EMAIL, RAW_API_TOKEN, RAW_CUSTOMER_TERM):
        assert sensitive_value not in str(captured.value)
        assert sensitive_value not in repr(captured.value)


def test_provider_error_is_sanitized_from_none_and_returns_no_authoring_state():
    response = ProviderHTTPResponse(
        status_code=400,
        headers={
            "X-Request-ID": "{0}|{1}|{2}".format(
                RAW_EMAIL,
                RAW_API_TOKEN,
                RAW_CUSTOMER_TERM,
            )
        },
        body=("PASS approved draft containing {0}, {1}, and {2}").format(
            RAW_EMAIL, RAW_API_TOKEN, RAW_CUSTOMER_TERM
        ),
    )
    provider, transport = _provider(response)

    with pytest.raises(AssistedAuthoringError) as captured:
        _build(provider)

    assert len(transport.requests) == 1
    assert not hasattr(captured.value, "discovery_pack")
    assert str(captured.value) == (
        "Provider-assisted discovery could not be completed."
    )
    assert "PASS" not in str(captured.value)
    assert "draft" not in str(captured.value).lower()
    for sensitive_value in (RAW_EMAIL, RAW_API_TOKEN, RAW_CUSTOMER_TERM):
        assert sensitive_value not in str(captured.value)
        assert sensitive_value not in repr(captured.value)
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert captured.value.__suppress_context__ is True
