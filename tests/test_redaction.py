import json

import pytest
from pydantic import ValidationError

from exitspec.redaction import (
    POLICY_VERSION,
    RedactionBoundaryError,
    RedactionConfigurationError,
    RedactionDecision,
    RedactionFinding,
    RedactionKind,
    RedactionResult,
    assert_redaction_egress,
    redact_transcript,
)


RAW_EMAIL = "jayesh.suyal@example.com"
RAW_PHONE = "+1 (415) 555-2671"
RAW_BEARER = "bearerSecretABC123456"
RAW_API_TOKEN = "sk-proj_ABCdef1234567890"
RAW_JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.signatureABC123"
RAW_CARD = "4111 1111 1111 1111"
RAW_CUSTOMER = "Project Phoenix"


def _sensitive_transcript():
    return (
        "Field Engineer: Contact {0} or {1}.\n"
        "Customer: Authorization: Bearer {2}\n"
        "Field Engineer: api_key={3}\n"
        "Customer: Session JWT {4}\n"
        "Customer: Test card {5} belongs to {6}."
    ).format(
        RAW_EMAIL,
        RAW_PHONE,
        RAW_BEARER,
        RAW_API_TOKEN,
        RAW_JWT,
        RAW_CARD,
        RAW_CUSTOMER,
    )


def test_redacts_supported_values_without_leaking_raw_material():
    raw = _sensitive_transcript()

    result = redact_transcript(raw, customer_terms=[RAW_CUSTOMER])

    for sensitive_value in (
        RAW_EMAIL,
        RAW_PHONE,
        RAW_BEARER,
        RAW_API_TOKEN,
        RAW_JWT,
        RAW_CARD,
        RAW_CUSTOMER,
    ):
        assert sensitive_value not in result.redacted_text
        assert sensitive_value not in result.model_dump_json()

    assert result.counts == {
        "BEARER_TOKEN": 1,
        "API_TOKEN": 1,
        "JWT": 1,
        "PAYMENT_CARD": 1,
        "EMAIL": 1,
        "PHONE": 1,
        "CUSTOMER_TERM": 1,
    }
    assert result.policy_version == POLICY_VERSION
    assert result.decision == RedactionDecision.ALLOW_REDACTED_ONLY
    assert result.safe_to_send is True
    assert result.safe_to_persist is True


def test_preserves_line_structure_speaker_labels_and_auditable_line_numbers():
    raw = _sensitive_transcript()

    result = redact_transcript(raw, customer_terms=[RAW_CUSTOMER])

    assert result.redacted_text.count("\n") == raw.count("\n")
    assert [line.split(":", 1)[0] for line in result.redacted_text.splitlines()] == [
        "Field Engineer",
        "Customer",
        "Field Engineer",
        "Customer",
        "Customer",
    ]
    lines_by_kind = {
        finding.kind: finding.line_numbers for finding in result.findings
    }
    assert lines_by_kind[RedactionKind.EMAIL] == (1,)
    assert lines_by_kind[RedactionKind.PHONE] == (1,)
    assert lines_by_kind[RedactionKind.BEARER_TOKEN] == (2,)
    assert lines_by_kind[RedactionKind.API_TOKEN] == (3,)
    assert lines_by_kind[RedactionKind.JWT] == (4,)
    assert lines_by_kind[RedactionKind.PAYMENT_CARD] == (5,)
    assert lines_by_kind[RedactionKind.CUSTOMER_TERM] == (5,)
    assert all("\n" not in finding.placeholder for finding in result.findings)


def test_repeated_redaction_is_fully_idempotent():
    first = redact_transcript(
        _sensitive_transcript(), customer_terms=[RAW_CUSTOMER]
    )

    second = redact_transcript(
        first.redacted_text, customer_terms=[RAW_CUSTOMER]
    )

    assert second == first


def test_luhn_safeguard_and_phone_shape_preserve_benign_numbers():
    raw = (
        "Customer: Threshold 95%, 200 samples, date 2026-07-22, "
        "build 1234567890123456, ticket 8675309.\n"
        "Field Engineer: Card 4242-4242-4242-4242 and phone 415-555-2671."
    )

    result = redact_transcript(raw)

    assert "95%" in result.redacted_text
    assert "200 samples" in result.redacted_text
    assert "2026-07-22" in result.redacted_text
    assert "1234567890123456" in result.redacted_text
    assert "8675309" in result.redacted_text
    assert result.counts["PAYMENT_CARD"] == 1
    assert result.counts["PHONE"] == 1
    assert "4242-4242-4242-4242" not in result.redacted_text
    assert "415-555-2671" not in result.redacted_text


def test_redacts_environment_style_api_keys_and_e164_phones():
    api_token = "fireworksSecretABC123456789"
    phone = "+442071838750"
    raw = "Field Engineer: FIREWORKS_API_KEY={0}; call {1}.".format(
        api_token, phone
    )

    result = redact_transcript(raw)

    assert api_token not in result.redacted_text
    assert phone not in result.redacted_text
    assert "FIREWORKS_API_KEY=[REDACTED:API_TOKEN]" in result.redacted_text
    assert result.counts["API_TOKEN"] == 1
    assert result.counts["PHONE"] == 1


def test_finding_metadata_contains_categories_counts_and_locations_only():
    raw = _sensitive_transcript()

    result = redact_transcript(raw, customer_terms=[RAW_CUSTOMER])
    metadata = json.dumps(
        [finding.model_dump(mode="json") for finding in result.findings],
        sort_keys=True,
    )

    assert set(result.findings[0].model_fields_set) == {
        "kind",
        "count",
        "line_numbers",
        "placeholder",
    }
    for sensitive_value in (
        RAW_EMAIL,
        RAW_PHONE,
        RAW_BEARER,
        RAW_API_TOKEN,
        RAW_JWT,
        RAW_CARD,
        RAW_CUSTOMER,
    ):
        assert sensitive_value not in metadata


def test_result_states_policy_scope_instead_of_claiming_complete_pii_detection():
    result = redact_transcript("Customer: No supported pattern here.")

    assert result.decision == RedactionDecision.ALLOW_REDACTED_ONLY
    assert any("do not detect every form" in item for item in result.limitations)
    assert any("human review" in item for item in result.limitations)
    assert not any("PII-free" in item for item in result.limitations)


@pytest.mark.parametrize(
    "customer_terms",
    ["Project Phoenix", [""], ["x"], ["valid", 123], ["line\nbreak"]],
)
def test_rejects_unsafe_customer_term_configuration_without_echoing_values(
    customer_terms,
):
    with pytest.raises(RedactionConfigurationError) as captured:
        redact_transcript("Customer: Example", customer_terms=customer_terms)

    assert "Project Phoenix" not in str(captured.value)
    assert "line\nbreak" not in str(captured.value)


def test_result_and_findings_block_assignment():
    result = redact_transcript(
        _sensitive_transcript(), customer_terms=[RAW_CUSTOMER]
    )

    with pytest.raises(ValidationError):
        result.redacted_text = "Customer: Authorization: Bearer {0}".format(
            RAW_BEARER
        )
    with pytest.raises(ValidationError):
        result.findings[0].count = 999


def test_nested_metadata_is_immutable_or_isolated():
    result = redact_transcript(
        _sensitive_transcript(), customer_terms=[RAW_CUSTOMER]
    )

    assert isinstance(result.findings, tuple)
    assert isinstance(result.findings[0].line_numbers, tuple)
    assert isinstance(result.limitations, tuple)
    with pytest.raises(AttributeError):
        result.findings[0].line_numbers.append(99)
    with pytest.raises(AttributeError):
        result.limitations.append("Changed")

    detached_counts = result.counts
    detached_counts[RedactionKind.EMAIL.value] = 999
    assert result.counts[RedactionKind.EMAIL.value] == 1
    assert result.model_dump(mode="json")["counts"] == result.counts


def test_manual_allowed_result_with_raw_secret_is_rejected_safely():
    with pytest.raises(ValidationError) as captured:
        RedactionResult(
            policy_version=POLICY_VERSION,
            redacted_text="Customer: Authorization: Bearer {0}".format(
                RAW_BEARER
            ),
            findings=(),
            decision=RedactionDecision.ALLOW_REDACTED_ONLY,
            safe_to_send=True,
            safe_to_persist=True,
            limitations=("Best-effort policy.",),
        )

    assert RAW_BEARER not in str(captured.value)
    assert RAW_BEARER not in repr(captured.value)


def test_model_copy_forgery_is_caught_at_egress_without_leaking_secret():
    result = redact_transcript("Customer: Approved synthetic transcript.")
    forged = result.model_copy(
        update={
            "redacted_text": (
                "Customer: Authorization: Bearer {0}".format(RAW_BEARER)
            )
        }
    )

    assert forged.safe_to_send is True
    with pytest.raises(RedactionBoundaryError) as captured:
        assert_redaction_egress(forged)

    assert RAW_BEARER not in str(captured.value)
    assert RAW_BEARER not in repr(captured.value)


def test_forged_limitations_cannot_weaken_honest_egress_boundary():
    result = redact_transcript("Customer: Approved synthetic transcript.")
    forged = result.model_copy(update={"limitations": ("This is PII-free.",)})

    assert forged.safe_to_send is True
    with pytest.raises(RedactionBoundaryError) as captured:
        assert_redaction_egress(forged)

    assert "PII-free" not in str(captured.value)
    assert "PII-free" not in repr(captured.value)


def test_unchecked_construction_is_caught_at_egress():
    forged = RedactionResult.model_construct(
        policy_version=POLICY_VERSION,
        redacted_text="Field Engineer: bearer {0}".format(RAW_BEARER),
        findings=(),
        decision=RedactionDecision.ALLOW_REDACTED_ONLY,
        safe_to_send=True,
        safe_to_persist=True,
        limitations=("Best-effort policy.",),
    )

    with pytest.raises(RedactionBoundaryError):
        assert_redaction_egress(forged)


def test_egress_rescans_configured_customer_terms():
    result = redact_transcript(
        "Customer: Project Phoenix is the confidential project."
    )

    with pytest.raises(RedactionBoundaryError) as captured:
        assert_redaction_egress(result, customer_terms=[RAW_CUSTOMER])

    assert RAW_CUSTOMER not in str(captured.value)
    assert RAW_CUSTOMER not in repr(captured.value)


def test_safe_normal_egress_returns_only_redacted_text():
    result = redact_transcript(
        _sensitive_transcript(), customer_terms=[RAW_CUSTOMER]
    )

    allowed_text = assert_redaction_egress(
        result, customer_terms=[RAW_CUSTOMER]
    )

    assert allowed_text == result.redacted_text
    assert RAW_BEARER not in allowed_text
    assert RAW_CUSTOMER not in allowed_text


def test_finding_manual_construction_remains_immutable():
    finding = RedactionFinding(
        kind=RedactionKind.EMAIL,
        count=1,
        line_numbers=[1],
        placeholder="[REDACTED:EMAIL]",
    )

    assert finding.line_numbers == (1,)
    with pytest.raises(ValidationError):
        finding.line_numbers = (2,)
