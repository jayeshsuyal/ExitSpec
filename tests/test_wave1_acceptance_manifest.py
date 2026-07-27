import hashlib
import json
from collections import Counter
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from exitspec.assisted_authoring import _provider_request
from exitspec.canonical import canonical_json_bytes
from exitspec.intake import redact_and_parse_pasted_transcript


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = (
    PROJECT_ROOT
    / "examples/support-agent/fireworks/wave-1-acceptance-v1.json"
)


def _load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _manifest_and_fixture():
    manifest = _load_json(MANIFEST_PATH)
    fixture_path = PROJECT_ROOT / manifest["source_fixture"]["path"]
    return manifest, fixture_path, _load_json(fixture_path)


def _structured_request_payload(request):
    return {
        "model": request.model,
        "messages": [
            {"role": message.role, "content": message.content}
            for message in request.messages
        ],
        "schema_name": request.schema_name,
        "response_schema": request.response_schema_payload(),
        "max_output_tokens": request.max_output_tokens,
        "estimated_input_tokens": request.estimated_input_tokens,
        "budget_usd": str(request.budget_usd),
        "timeout_seconds": request.timeout_seconds,
        "temperature": request.temperature,
    }


def test_wave1_manifest_freezes_the_exact_synthetic_fixture():
    manifest, fixture_path, fixture = _manifest_and_fixture()
    fixture_bytes = fixture_path.read_bytes()
    cases = fixture["cases"]

    assert manifest["status"] == "FROZEN"
    assert manifest["manifest_version"] == "1.0.0"
    assert fixture["synthetic_only"] is True
    assert manifest["source_fixture"]["synthetic_only"] is True
    assert hashlib.sha256(fixture_bytes).hexdigest() == manifest["source_fixture"][
        "sha256"
    ]
    assert len(cases) == manifest["source_fixture"]["case_count"]
    assert len({case["id"] for case in cases}) == len(cases)
    assert sum(len(case["expected_proposals"]) for case in cases) == manifest[
        "source_fixture"
    ]["expected_proposal_count"]
    live_smoke_case_id = manifest["source_fixture"]["live_smoke_case_id"]
    assert live_smoke_case_id in {case["id"] for case in cases}
    assert manifest["approved_live_smoke_request"]["source_case_id"] == (
        live_smoke_case_id
    )
    assert len(
        manifest["approved_live_smoke_request"]["redacted_payload_digest"]
    ) == 64
    assert len(
        manifest["approved_live_smoke_request"][
            "redaction_configuration_digest"
        ]
    ) == 64

    actual_slice_counts = Counter(
        slice_name for case in cases for slice_name in case["slices"]
    )
    assert dict(sorted(actual_slice_counts.items())) == manifest["source_fixture"][
        "case_slice_counts"
    ]


def test_wave1_expected_sources_match_the_redacted_fixture_exactly():
    _, _, fixture = _manifest_and_fixture()

    for case in fixture["cases"]:
        intake = redact_and_parse_pasted_transcript(
            case["transcript"],
            transcript_id=case["id"],
            title=case["title"],
            customer_terms=case["customer_terms"],
        )
        lines = {
            line.line_number: line
            for line in intake.transcript.lines
        }
        assert set(case.get("expected_redaction_kinds", ())) == {
            kind
            for kind, count in intake.redaction.counts.items()
            if count
        }
        for proposal in case["expected_proposals"]:
            source = lines[proposal["line_number"]]
            assert source.speaker == proposal["speaker"]
            assert source.text == proposal["quote"]
            assert proposal["classification"] in {"measurable", "vague"}
            if proposal["classification"] == "measurable":
                assert proposal["threshold"] is not None
                assert proposal["minimum_samples"] is not None
                assert proposal["open_question_required"] is False
            else:
                assert proposal["open_question_required"] is True


def test_wave1_live_smoke_payload_digest_is_reproducible():
    manifest, _, fixture = _manifest_and_fixture()
    approved = manifest["approved_live_smoke_request"]
    case = next(
        case
        for case in fixture["cases"]
        if case["id"] == approved["source_case_id"]
    )
    intake = redact_and_parse_pasted_transcript(
        case["transcript"],
        transcript_id=case["id"],
        title=case["title"],
        customer_terms=case["customer_terms"],
    )
    request, _ = _provider_request(
        intake,
        model=manifest["provider_boundary"]["model"],
        customer_terms=case["customer_terms"],
    )
    request = replace(
        request,
        budget_usd=Decimal(
            manifest["provider_boundary"]["request_limits"][
                "max_request_cost_usd"
            ]
        ),
    )
    payload_digest = hashlib.sha256(
        b"exitspec-provider-egress-payload-v1\x00"
        + canonical_json_bytes(_structured_request_payload(request))
    ).hexdigest()

    assert payload_digest == approved["redacted_payload_digest"]
    assert request.estimated_input_tokens <= manifest["provider_boundary"][
        "request_limits"
    ]["estimated_input_tokens_max"]
    assert request.max_output_tokens <= manifest["provider_boundary"][
        "request_limits"
    ]["output_tokens_max"]


def test_wave1_provider_destination_pricing_and_budget_are_explicit():
    manifest, _, _ = _manifest_and_fixture()
    boundary = manifest["provider_boundary"]
    limits = boundary["request_limits"]
    pricing = boundary["pricing_snapshot"]

    assert boundary["provider"] == "fireworks"
    assert boundary["api_surface"] == "chat_completions"
    assert boundary["endpoint"] == (
        "https://api.fireworks.ai/inference/v1/chat/completions"
    )
    assert boundary["service_tier"] == "standard"
    assert boundary["model"] == (
        "accounts/fireworks/models/deepseek-v4-flash"
    )
    assert boundary["credential_source"] == (
        "server_environment:FIREWORKS_API_KEY"
    )
    assert boundary["redaction_policy_version"] == (
        "exitspec-transcript-redaction/1.0"
    )
    assert pricing["effective_checked_at"] == manifest["frozen_at"]
    assert boundary["data_policy_snapshot"]["effective_checked_at"] == manifest[
        "frozen_at"
    ]
    assert pricing["source"].startswith("https://docs.fireworks.ai/")
    assert boundary["data_policy_snapshot"]["source"].startswith(
        "https://docs.fireworks.ai/"
    )

    worst_case_cost = (
        Decimal(limits["estimated_input_tokens_max"]) * Decimal(pricing["input"])
        + Decimal(limits["output_tokens_max"]) * Decimal(pricing["output"])
    ) / Decimal("1000000")
    assert worst_case_cost <= Decimal(limits["max_request_cost_usd"])
    assert Decimal(limits["max_request_cost_usd"]) <= Decimal(
        limits["max_live_smoke_total_cost_usd"]
    )


def test_wave1_requires_payload_bound_one_time_server_authorization():
    manifest, _, _ = _manifest_and_fixture()
    acknowledgement = manifest["egress_acknowledgement"]

    assert acknowledgement["required"] is True
    assert acknowledgement["server_validated"] is True
    assert acknowledgement["one_time_use"] is True
    assert acknowledgement["payload_binding"] == (
        "sha256_of_canonical_redacted_request_intent"
    )
    assert {
        "acceptance_manifest_id",
        "acceptance_manifest_version",
        "source_fixture_sha256",
        "source_case_id",
        "redacted_payload_digest",
        "redaction_policy_version",
        "redaction_configuration_digest",
        "provider",
        "model",
        "endpoint",
        "data_policy_snapshot",
        "pricing_snapshot",
        "request_limits",
        "max_request_cost_usd",
        "issued_at",
        "expires_at",
        "nonce",
    } == set(acknowledgement["bound_fields"])
    assert {
        "missing",
        "expired",
        "payload_mismatch",
        "policy_mismatch",
        "acceptance_manifest_mismatch",
        "source_fixture_mismatch",
        "source_case_mismatch",
        "redaction_configuration_mismatch",
        "provider_mismatch",
        "model_mismatch",
        "endpoint_mismatch",
        "pricing_mismatch",
        "request_limit_mismatch",
        "budget_mismatch",
        "replayed",
    } == set(acknowledgement["required_rejections"])


def test_wave1_failure_matrix_is_complete_and_fail_closed():
    manifest, _, _ = _manifest_and_fixture()
    failures = {
        failure["case"]: failure
        for failure in manifest["required_failure_matrix"]
    }

    assert {
        "missing_configuration",
        "invalid_credential",
        "suspended_or_unfunded_account",
        "rate_limit",
        "timeout",
        "service_unavailable_503",
        "other_5xx",
        "malformed_json",
        "schema_violation",
        "source_link_violation",
        "retry_exhaustion",
        "preflight_budget_refusal",
        "postflight_budget_refusal",
        "redirect_301",
        "redirect_302",
        "redirect_303",
        "redirect_307",
        "redirect_308",
        "missing_egress_acknowledgement",
        "invalid_egress_acknowledgement",
    } == set(failures)
    assert {
        status
        for status in (301, 302, 303, 307, 308)
        if failures[f"redirect_{status}"]["expected_code"]
        == "redirect_rejected"
        and failures[f"redirect_{status}"]["retry"] is False
    } == {301, 302, 303, 307, 308}
    assert failures["suspended_or_unfunded_account"]["expected_code"] == (
        "account_unavailable"
    )
    assert failures["missing_egress_acknowledgement"]["expected_code"] == (
        "egress_not_authorized"
    )


def test_wave1_zero_tolerance_authority_privacy_and_receipt_gates():
    manifest, _, _ = _manifest_and_fixture()
    quality = manifest["quality_gates"]
    receipt = manifest["receipt_contract"]

    assert quality["schema_valid_rate_min"] == 1.0
    assert quality["source_anchor_exact_rate_min"] == 1.0
    assert quality["classification_exact_rate_min"] == 1.0
    assert quality["numeric_fact_exact_rate_min"] == 1.0
    assert quality["unresolved_open_question_rate_min"] == 1.0
    for gate, maximum in quality.items():
        if gate.endswith("_count_max"):
            assert maximum == 0

    assert {
        "credential",
        "request_headers",
        "request_body",
        "response_body",
        "raw_transcript",
        "redacted_transcript",
        "proposal_text",
        "provider_request_id",
    } == set(receipt["forbidden_content"])
    assert manifest["operational_gates"][
        "deterministic_path_must_pass_when_provider_disabled"
    ] is True
