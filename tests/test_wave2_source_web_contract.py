"""Executable freeze for the Wave-2 guided source browser/API contract."""

from __future__ import annotations

import hashlib
from itertools import combinations
import json
from html.parser import HTMLParser
from pathlib import Path
import re
import shutil

import pytest

from exitspec.demo_data import (
    SupportAgentSourceWebContract,
    SupportAgentSourceWebContractError,
    support_agent_source_web_contract,
)
from exitspec.models import CriterionDraft


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = (
    PROJECT_ROOT
    / "examples"
    / "support-agent"
    / "email"
    / "wave-2-source-web-v1.json"
)
PACKAGED_CONTRACT_PATH = (
    PROJECT_ROOT
    / "src"
    / "exitspec"
    / "demo_data"
    / "support_agent"
    / "email"
    / "wave-2-source-web-v1.json"
)
SOURCE_MANIFEST_PATH = (
    PROJECT_ROOT
    / "examples"
    / "support-agent"
    / "email"
    / "wave-2-acceptance-v1.json"
)
INDEX_PATH = PROJECT_ROOT / "src" / "exitspec" / "static" / "index.html"
APP_JS_PATH = PROJECT_ROOT / "src" / "exitspec" / "static" / "app.js"
WEB_PATH = PROJECT_ROOT / "src" / "exitspec" / "web.py"
SOURCE_WEB_PATH = PROJECT_ROOT / "src" / "exitspec" / "source_web.py"
EXPECTED_CONTRACT_SHA256 = (
    "f89825510155b1d579814da0f6e3a639c1b03d3111deba170556654eaca35ffd"
)
EXPECTED_SOURCE_MANIFEST_SHA256 = (
    "aa514787eb6b14a93216682d702fc29a32d630eb1a91a16dae6ce0873a268ae2"
)
RECEIPT_FIELDS = {
    "source_type",
    "manifest_id",
    "manifest_version",
    "fixture_case_id",
    "outcome_code",
    "source_version",
    "candidate_count",
}
TIMING_FIELDS = {"fixture_case_id", "outcome_code", "elapsed_ms"}


def _load(path: Path = CONTRACT_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _source_manifest() -> dict:
    return json.loads(SOURCE_MANIFEST_PATH.read_text(encoding="utf-8"))


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _declared_response_fields(contract: dict) -> set[str]:
    shapes = contract["public_shapes"]
    response_shape_names = {
        "catalog_response",
        "import_success_response",
        "terminal_receipt",
        "state",
        "source_intake",
        "draft",
        "typed_refusal",
        "browser_timing_evidence",
    }
    fields: set[str] = set()
    for name in response_shape_names:
        shape = shapes[name]
        for key, value in shape.items():
            if key == "exact_fields" or key.endswith("_exact_fields"):
                fields.update(value)
    return fields


def _nested_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return set(value).union(
            *(_nested_keys(item) for item in value.values()),
            set(),
        )
    if isinstance(value, list):
        return set().union(*(_nested_keys(item) for item in value), set())
    return set()


class _IdParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: set[str] = set()
        self.data_attributes: set[str] = set()

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        del tag
        for name, value in attrs:
            if name == "id" and value is not None:
                self.ids.add(value)
            if name.startswith("data-"):
                self.data_attributes.add(name)


def _active_javascript_selectors(javascript: str) -> set[str]:
    patterns = (
        r"querySelector(?:All)?\((?:`([^`]+)`|\"([^\"]+)\"|'([^']+)')\)",
        r"closest\((?:`([^`]+)`|\"([^\"]+)\"|'([^']+)')\)",
        r"\$\((?:`([^`]+)`|\"([^\"]+)\"|'([^']+)')\)",
    )
    selectors: set[str] = set()
    for pattern in patterns:
        for groups in re.findall(pattern, javascript):
            selectors.add(next(group for group in groups if group))
    return selectors


def _active_data_attributes(index: str, javascript: str) -> set[str]:
    parser = _IdParser()
    parser.feed(index)
    attributes = set(parser.data_attributes)
    attributes.update(
        re.findall(
            r"\b(data-[a-z][a-z0-9-]*)(?=\s*=|\])",
            javascript,
        )
    )
    for property_name in re.findall(
        r"\.dataset\.([A-Za-z][A-Za-z0-9]*)",
        javascript,
    ):
        kebab = re.sub(r"(?<!^)(?=[A-Z])", "-", property_name).lower()
        attributes.add("data-{0}".format(kebab))
    return attributes


def _source_fixture(case_id: str) -> dict:
    return next(
        fixture
        for fixture in _source_manifest()["fixture_set"]["fixtures"]
        if fixture["case_id"] == case_id
    )


class _DuplicateJsonMember(ValueError):
    pass


class _JsonObjectPairs(list):
    pass


def _parse_json_rejecting_duplicate_members(payload: str) -> object:
    parsed = json.loads(payload, object_pairs_hook=_JsonObjectPairs)

    def reject_duplicate_members(value: object) -> None:
        if isinstance(value, _JsonObjectPairs):
            names: set[str] = set()
            for name, child in value:
                if name in names:
                    raise _DuplicateJsonMember
                names.add(name)
                reject_duplicate_members(child)
        elif isinstance(value, list):
            for child in value:
                reject_duplicate_members(child)

    reject_duplicate_members(parsed)
    return parsed


def _is_source_pipeline_path(path: str) -> bool:
    return path == "/api/source" or path.startswith("/api/source/")


def _candidate_quotes(case_id: str) -> list[str]:
    fixture = _source_fixture(case_id)
    fixture_path = SOURCE_MANIFEST_PATH.parent / "{0}.eml".format(case_id)
    normalized = fixture_path.read_bytes().replace(b"\r\n", b"\n")
    body = normalized.split(b"\n\n", 1)[1]
    quotes: list[str] = []
    for candidate in fixture["expected_candidates"]:
        assert candidate["part_path"] == "body:text/plain:0"
        selected = body[
            candidate["start_byte"] : candidate["end_byte"]
        ]
        assert _sha256(selected) == candidate["quote_sha256"]
        quotes.append(selected.decode("utf-8"))
    return quotes


def _supported_accuracy_criterion(
    *,
    draft_id: str,
    transcript_id: str,
    line_number: int,
    quote: str,
    normalized_claim: str,
) -> dict:
    return {
        "id": "RULE-{0}".format(draft_id),
        "title": "Exact tool selection",
        "must_have": True,
        "source": {
            "speaker": "synthetic_email_source",
            "quote": quote,
            "location": "{0}:{1}".format(transcript_id, line_number),
        },
        "human_added": False,
        "normalized_claim": normalized_claim,
        "metric": "exact_tool_selection_rate",
        "unit": "proportion",
        "aggregation": "exact-match proportion",
        "rule": {
            "operator": "gte",
            "threshold": 0.95,
            "minimum_samples": 200,
            "confidence_level": 0.95,
            "confidence_method": "wilson_two_sided_lower_bound",
        },
        "workload_slice": "support-tool-selection-v1",
        "adapter": "deterministic_tool_selection",
        "adapter_version": "1.0.0",
        "owner": "vendor_solutions_engineer",
        "evidence_policy": (
            "Persist synthetic case IDs, expected/actual tool names, calculation "
            "inputs, and SHA-256 digests."
        ),
        "approved": False,
    }


def _independent_expected_import_response(case_id: str) -> dict:
    fixture = _source_fixture(case_id)
    quotes = _candidate_quotes(case_id)
    metadata = {
        "thread-root": ("Support-agent requirements", 2),
        "authority-attack": ("Untrusted-instructions test", 1),
    }
    label, proposal_count = metadata[case_id]
    transcript_id = "email-{0}-v1".format(case_id)
    drafts = []
    review_controls = []

    for ordinal, (candidate, quote) in enumerate(
        zip(fixture["expected_candidates"], quotes, strict=True),
        start=1,
    ):
        projection = candidate["projection"]
        draft_id = "EMAIL-REQ-{0:02d}".format(ordinal)
        proposed_criterion = None
        if projection == {
            "criterion_type": "numeric_threshold",
            "metric": "tool_selection_accuracy",
            "operator": "gte",
            "threshold": "0.95",
            "unit": "ratio",
            "minimum_samples": 200,
        }:
            normalized_claim = (
                "Exact tool selection passes when exact expected support-tool "
                "selection reaches at least 95% across at least 200 fixed cases "
                "in the support-tool-selection-v1 workload, and the 95% Wilson "
                "lower bound meets the same threshold."
            )
            proposed_criterion = _supported_accuracy_criterion(
                draft_id=draft_id,
                transcript_id=transcript_id,
                line_number=ordinal,
                quote=quote,
                normalized_claim=normalized_claim,
            )
            open_questions = []
            allowed_actions = ["APPROVE", "REJECT"]
            can_edit_rule = True
        elif projection == {
            "criterion_type": "numeric_threshold",
            "metric": "end_to_end_latency_p95",
            "operator": "lt",
            "threshold": "2",
            "unit": "seconds",
            "minimum_samples": None,
        }:
            normalized_claim = (
                "P95 end-to-end latency must remain below 2 seconds."
            )
            open_questions = [
                "No deterministic latency adapter is available in this demo; "
                "keep this proposal as context or add a compatible adapter in a "
                "future contract."
            ]
            allowed_actions = ["REJECT"]
            can_edit_rule = False
        elif projection == {
            "criterion_type": "numeric_threshold",
            "metric": "tool_selection_accuracy",
            "operator": "gte",
            "threshold": "0.95",
            "unit": "ratio",
            "minimum_samples": None,
        }:
            normalized_claim = (
                "Tool-selection accuracy should be at least 95%; a human must "
                "define the fixed sample count before approval."
            )
            open_questions = [
                "What minimum fixed sample count should define this acceptance rule?"
            ]
            allowed_actions = ["REJECT"]
            can_edit_rule = True
        else:
            raise AssertionError("Unexpected guided candidate projection.")

        draft = {
            "id": draft_id,
            "status": "NEEDS_REVIEW",
            "source_span": {
                "transcript_id": transcript_id,
                "start_line": ordinal,
                "end_line": ordinal,
                "speaker": "synthetic_email_source",
                "quote": quote,
            },
            "human_added": False,
            "human_added_rationale": None,
            "normalized_claim": normalized_claim,
            "proposed_criterion": proposed_criterion,
            "open_questions": open_questions,
            "review": None,
        }
        CriterionDraft.model_validate(draft)
        drafts.append(draft)
        review_controls.append(
            {
                "draft_id": draft_id,
                "allowed_actions": allowed_actions,
                "can_edit_rule": can_edit_rule,
            }
        )

    return {
        "contract_version": "wave2-source-web-v1",
        "receipt": {
            "source_type": "rfc822",
            "manifest_id": "exitspec-wave-2-synthetic-rfc822-intake",
            "manifest_version": "1.0.1",
            "fixture_case_id": case_id,
            "outcome_code": "accepted",
            "source_version": 1,
            "candidate_count": fixture["expected_candidate_count"],
        },
        "state": {
            "source_intake": {
                "status": "NEEDS_REVIEW",
                "source_type": "rfc822",
                "fixture_case_id": case_id,
                "label": label,
                "source_version": 1,
                "message_count": 1,
                "proposal_count": proposal_count,
                "pending_count": proposal_count,
                "redaction_applied": True,
                "authority": "UNTRUSTED_SOURCE_ONLY",
                "review_controls": review_controls,
            },
            "drafts": drafts,
        },
    }


def test_contract_identity_source_pin_and_implementation_status_are_exact():
    contract = _load()
    source_payload = SOURCE_MANIFEST_PATH.read_bytes()

    assert contract["schema_version"] == "1.0.0"
    assert contract["contract_id"] == "wave2-source-web-v1"
    assert contract["contract_version"] == "wave2-source-web-v1"
    assert contract["status"] == "FROZEN"
    assert contract["source_manifest"] == {
        "manifest_id": "exitspec-wave-2-synthetic-rfc822-intake",
        "manifest_version": "1.0.1",
        "manifest_sha256": EXPECTED_SOURCE_MANIFEST_SHA256,
        "source_type": "rfc822",
        "synthetic_only": True,
    }
    assert _sha256(source_payload) == EXPECTED_SOURCE_MANIFEST_SHA256
    assert contract["implementation_status"] == {
        "contract_only": True,
        "fixture_catalog_endpoint_implemented": False,
        "source_import_endpoint_implemented": False,
        "email_intake_ui_implemented": False,
        "existing_review_confirmation_freeze_prove_flow_implemented": True,
        "implementation_must_not_be_inferred_from_this_contract": True,
    }


def test_guided_fixture_catalog_is_exact_and_matches_source_manifest_counts():
    contract = _load()
    guided = contract["guided_fixtures"]
    entries = guided["entries"]
    source_fixtures = {
        fixture["case_id"]: fixture
        for fixture in _source_manifest()["fixture_set"]["fixtures"]
    }

    assert guided["default_fixture_case_id"] == "thread-root"
    assert guided["exact_count"] == 2
    assert entries == [
        {
            "fixture_case_id": "thread-root",
            "label": "Support-agent requirements",
            "summary": "2 proposals · redacted before review",
            "proposal_count": 2,
        },
        {
            "fixture_case_id": "authority-attack",
            "label": "Untrusted-instructions test",
            "summary": "1 proposal · redacted before review",
            "proposal_count": 1,
        },
    ]
    for entry in entries:
        fixture = source_fixtures[entry["fixture_case_id"]]
        assert fixture["expected_outcome_code"] == "accepted"
        assert fixture["expected_candidate_state"] == "NEEDS_REVIEW"
        assert fixture["expected_candidate_count"] == entry["proposal_count"]
        assert len(fixture["expected_candidates"]) == entry["proposal_count"]


def test_endpoint_methods_paths_transport_and_public_shapes_are_exact():
    contract = _load()
    endpoints = {
        (endpoint["method"], endpoint["path"]): endpoint
        for endpoint in contract["endpoints"]
    }
    assert set(endpoints) == {
        ("GET", "/api/source/fixtures"),
        ("POST", "/api/source/import"),
    }

    catalog = endpoints[("GET", "/api/source/fixtures")]
    assert catalog["implemented"] is False
    assert catalog["query_allowed"] is False
    assert catalog["body_allowed"] is False
    assert catalog["exact_local_host_required"] is True
    assert catalog["origin_policy"] == "absent or exact server origin"
    assert catalog["fetch_site_policy"] == "absent, none, or same-origin"
    assert catalog["success_status"] == 200
    assert set(catalog["exact_success_example"]) == set(
        contract["public_shapes"]["catalog_response"]["exact_fields"]
    )
    assert catalog["exact_success_example"]["fixtures"] == (
        contract["guided_fixtures"]["entries"]
    )

    source_import = endpoints[("POST", "/api/source/import")]
    assert source_import["implemented"] is False
    assert source_import["query_allowed"] is False
    assert source_import["content_type_required"] == "application/json"
    assert source_import["json_charset"] == "utf-8"
    assert source_import["exact_local_host_required"] is True
    assert (
        source_import["origin_policy"]
        == "exactly one exact server origin required"
    )
    assert source_import["fetch_site_policy"] == "absent or same-origin"
    assert source_import["exact_request_examples"] == [
        {"fixture_case_id": "thread-root"},
        {"fixture_case_id": "authority-attack"},
    ]
    assert contract["public_shapes"]["import_request"]["exact_fields"] == [
        "fixture_case_id"
    ]
    assert set(source_import["request_forbidden_fields"]).isdisjoint(
        {"fixture_case_id"}
    )

    transport = contract["transport_policy"]
    assert transport["host"] == {
        "required_header_count": 1,
        "allowed_hosts": ["127.0.0.1", "localhost", "::1"],
        "port": "exact running server port",
        "canonical_authority_required": True,
        "credentials_allowed": False,
        "whitespace_allowed": False,
    }
    assert transport["catalog_origin"][
        "normal_same_origin_browser_without_origin_allowed"
    ] is True
    assert transport["catalog_origin"]["header_count_allowed"] == [0, 1]
    assert transport["import_origin"]["required_header_count"] == 1
    assert transport["request_size_limit_bytes"] == 65536
    assert transport["request_size_limit_inclusive"] is True
    assert transport["streamed_request_body_allowed"] is False


def test_transport_pipeline_order_faults_and_combined_precedence_are_exact():
    contract = _load()
    validation = contract["transport_validation"]
    pipeline = validation["ordered_pipeline"]
    expected_gates = [
        "host_and_local_authority",
        "method_and_path",
        "query_and_route_parameters",
        "catalog_get_body",
        "import_origin",
        "fetch_site",
        "import_content_type",
        "import_content_length_and_streaming",
        "import_json_document",
        "exact_import_body_fields",
        "guided_fixture_lookup",
        "workflow_state_and_fixture_relation",
    ]

    assert [gate["order"] for gate in pipeline] == list(range(1, 13))
    assert [gate["gate"] for gate in pipeline] == expected_gates
    assert all(
        fault["zero_mutation"] is True
        for gate in pipeline
        for fault in gate["fault_precedence"]
    )
    assert "stop at the first failure" in validation["pipeline_rule"]
    assert "lowest numeric gate order wins" in validation[
        "combined_failure_precedence"
    ]

    canonical = validation["canonical_faults_for_pairwise_testing"]
    assert [fault["order"] for fault in canonical] == list(range(1, 13))
    for fault in canonical:
        gate = pipeline[fault["order"] - 1]
        matching = [
            candidate
            for candidate in gate["fault_precedence"]
            if candidate["fault"] == fault["fault"]
        ]
        assert len(matching) == 1
        assert matching[0]["http_status"] == fault["http_status"]
        assert matching[0]["code"] == fault["code"]

    for left, right in combinations(canonical, 2):
        winner = min((left, right), key=lambda item: item["order"])
        observed = min((left, right), key=lambda item: item["order"])
        assert (observed["http_status"], observed["code"]) == (
            winner["http_status"],
            winner["code"],
        )

    by_order = {fault["order"]: fault for fault in canonical}
    for oracle in validation["multi_fault_oracles"]:
        winner = by_order[min(oracle["fault_orders"])]
        assert oracle["expected_http_status"] == winner["http_status"]
        assert oracle["expected_code"] == winner["code"]
        assert oracle["zero_mutation"] is True

    length_gate = pipeline[7]
    oversize = next(
        fault
        for fault in length_gate["fault_precedence"]
        if fault["code"] == "source_request_too_large"
    )
    assert oversize == {
        "fault": "content_length_or_observed_stream_bytes_exceed_65536",
        "http_status": 413,
        "code": "source_request_too_large",
        "zero_mutation": True,
    }
    assert "65,537 bytes" in length_gate["stream_read_oracle"]
    assert "65,536 bytes is allowed" in length_gate["stream_read_oracle"]


def test_gate_nine_rejects_duplicate_members_before_later_json_checks():
    contract = _load()
    gate = contract["transport_validation"]["ordered_pipeline"][8]

    assert gate["gate"] == "import_json_document"
    assert [
        fault["code"] for fault in gate["fault_precedence"]
    ] == [
        "empty_json_body",
        "malformed_json",
        "duplicate_json_member",
        "json_object_required",
    ]
    semantics = gate["duplicate_member_semantics"]
    assert semantics["precedes_top_level_object_check"] is True
    assert semantics["precedes_exact_field_validation"] is True
    assert "every nesting depth" in semantics["scope"]
    assert "after JSON escape decoding" in semantics["comparison"]
    assert "never echo" in semantics["error_disclosure"]
    assert gate["validation_algorithm"] == [
        (
            "after the empty-body check, decode the complete body as strict "
            "UTF-8; any decoding failure returns malformed_json"
        ),
        (
            "parse the complete document into a member-pair-preserving "
            "representation; any syntax or trailing-data failure returns "
            "malformed_json even when a duplicate member occurs earlier in "
            "document order"
        ),
        (
            "only after complete parse success, walk every object depth-first "
            "in parser document order and return duplicate_json_member for "
            "the first repeated decoded member name"
        ),
        (
            "only after duplicate scanning, require the top-level value to be "
            "an object; exact request-field validation remains gate 10"
        ),
    ]

    probes = gate["duplicate_member_probes"]
    assert [probe["probe_id"] for probe in probes] == [
        "duplicate_fixture_case_id",
        "nested_duplicate_before_extra_field_validation",
        "nested_duplicate_before_top_level_object_check",
        "escaped_name_decodes_to_duplicate",
    ]
    for probe in probes:
        assert len(probe["body_utf8"].encode("utf-8")) == (
            probe["content_length"]
        )
        with pytest.raises(_DuplicateJsonMember):
            _parse_json_rejecting_duplicate_members(probe["body_utf8"])
        assert probe["expected_http_status"] == 400
        assert probe["expected_code"] == "duplicate_json_member"
        assert probe["zero_mutation"] is True

    precedence_probes = gate["within_gate_precedence_probes"]
    assert len(precedence_probes) == 1
    malformed = precedence_probes[0]
    assert malformed["probe_id"] == "malformed_after_nested_duplicate"
    assert len(malformed["body_utf8"].encode("utf-8")) == (
        malformed["content_length"]
    )
    with pytest.raises(json.JSONDecodeError):
        _parse_json_rejecting_duplicate_members(malformed["body_utf8"])
    assert malformed["expected_http_status"] == 400
    assert malformed["expected_code"] == "malformed_json"
    assert malformed["zero_mutation"] is True


def test_source_pipeline_scope_excludes_app_and_existing_routes():
    contract = _load()
    validation = contract["transport_validation"]
    scope = validation["scope"]

    assert scope["path_rule"] == (
        "request path equals /api/source or starts with /api/source/"
    )
    assert all(_is_source_pipeline_path(path) for path in scope["in_scope_examples"])
    assert all(
        not _is_source_pipeline_path(path)
        for path in scope["out_of_scope_examples"]
    )
    assert scope["email_app_navigation_in_source_pipeline"] is False
    assert "existing router" in scope["out_of_scope_router"]

    app_profile = contract["acceptance_request_profiles"][
        "email_app_navigation"
    ]
    assert app_profile["path"] == "/app"
    assert _is_source_pipeline_path(app_profile["path"]) is False
    app_scenario = next(
        scenario
        for scenario in contract["acceptance_scenarios"]
        if scenario["scenario_id"] == "guided_browser_flow_1280x720"
    )
    assert app_scenario["expected_status"] == 200
    assert app_scenario["expected_code"] == "app_rendered"
    assert any(
        "outside transport_validation.scope" in assertion
        for assertion in app_scenario["assertions"]
    )


def test_terminal_receipt_and_browser_timing_are_exact_and_separate():
    contract = _load()
    receipt_shape = contract["public_shapes"]["terminal_receipt"]
    timing_shape = contract["public_shapes"]["browser_timing_evidence"]
    source_contract = _source_manifest()

    assert set(receipt_shape["exact_fields"]) == RECEIPT_FIELDS
    assert receipt_shape["candidate_count_means"] == (
        "candidates newly created by this operation"
    )
    assert receipt_shape["elapsed_ms_allowed"] is False
    assert set(timing_shape["exact_fields"]) == TIMING_FIELDS
    assert timing_shape["server_may_emit"] is False
    assert timing_shape["server_may_persist"] is False
    assert timing_shape["receipt_may_include"] is False
    assert "elapsed_ms" not in RECEIPT_FIELDS
    assert set(source_contract["receipt_contract"]["allowed_fields"]) == (
        RECEIPT_FIELDS
    )
    assert set(
        source_contract["browser_timing_evidence_contract"]["exact_fields"]
    ) == TIMING_FIELDS

    for name, receipt in contract["receipt_examples"].items():
        assert set(receipt) == RECEIPT_FIELDS, name
        assert receipt["source_type"] == "rfc822"
        assert receipt["manifest_version"] == "1.0.1"
    assert all(
        receipt["candidate_count"] == 0
        for name, receipt in contract["receipt_examples"].items()
        if name.endswith("duplicate_replay")
    )


def test_public_response_projections_cannot_name_private_source_fields():
    contract = _load()
    forbidden = set(contract["privacy_contract"]["forbidden_field_names"])
    declared = _declared_response_fields(contract)

    assert len(forbidden) >= 20
    assert len(declared) >= 25
    assert declared.isdisjoint(forbidden)
    assert contract["privacy_contract"][
        "generic_session_payload_may_be_returned_unfiltered"
    ] is False
    assert contract["privacy_contract"][
        "responses_must_use_exact_declared_projections"
    ] is True
    for name, example in contract["exact_import_response_examples"].items():
        assert _nested_keys(example).isdisjoint(forbidden), name
        serialized = json.dumps(example, sort_keys=True)
        for private_value in (
            "support-poc-001@customer.example",
            "authority-attack-001@customer.example",
            "priya@customer.example",
            "executive@customer.example",
            "rfc822:",
            "srcv:",
            "msg:",
            "demo-token-SYNTHETIC-0000",
        ):
            assert private_value not in serialized, (name, private_value)


def test_existing_draft_bridge_is_exact_and_stays_human_reviewed():
    contract = _load()
    draft = contract["public_shapes"]["draft"]

    assert draft["bridge"] == "existing CriterionDraft and POST /api/review"
    assert set(draft["exact_fields"]) == set(CriterionDraft.model_fields)
    assert draft["status_values"] == [
        "NEEDS_REVIEW",
        "APPROVED",
        "REJECTED",
    ]
    assert draft["review_endpoint"] == "/api/review"
    assert draft["review_actions"] == ["APPROVE", "REJECT"]
    assert contract["public_shapes"]["source_intake"]["authority_value"] == (
        "UNTRUSTED_SOURCE_ONLY"
    )


def test_safe_source_speaker_is_consistent_across_shape_projection_and_examples():
    contract = _load()
    expected_speaker = "synthetic_email_source"
    assert contract["public_shapes"]["draft"]["safe_source_span_rules"][
        "speaker"
    ] == expected_speaker
    projection = contract["candidate_review_projection"]
    assert projection["source_view"]["speaker"] == expected_speaker
    assert projection["source_span"]["speaker"] == expected_speaker

    for example in contract["exact_import_response_examples"].values():
        for draft in example["state"]["drafts"]:
            assert draft["source_span"]["speaker"] == expected_speaker
            criterion = draft["proposed_criterion"]
            if criterion is not None:
                assert criterion["source"]["speaker"] == expected_speaker


@pytest.mark.parametrize("case_id", ["thread-root", "authority-attack"])
def test_exact_import_examples_are_independently_recomputed_from_source_vectors(
    case_id,
):
    contract = _load()
    examples = contract["exact_import_response_examples"]
    key_prefix = case_id.replace("-", "_")
    accepted = examples["{0}_accepted".format(key_prefix)]
    replay = examples["{0}_duplicate_replay".format(key_prefix)]
    independently_computed = _independent_expected_import_response(case_id)

    assert accepted == independently_computed
    assert set(accepted) == set(
        contract["public_shapes"]["import_success_response"]["exact_fields"]
    )
    assert set(accepted["receipt"]) == RECEIPT_FIELDS
    assert set(accepted["state"]) == set(
        contract["public_shapes"]["state"]["exact_fields"]
    )
    assert all(
        set(draft) == set(CriterionDraft.model_fields)
        for draft in accepted["state"]["drafts"]
    )

    expected_replay = json.loads(json.dumps(independently_computed))
    expected_replay["receipt"]["outcome_code"] = "duplicate_replay"
    expected_replay["receipt"]["candidate_count"] = 0
    assert replay == expected_replay
    assert replay["state"] == accepted["state"]


def test_candidate_projection_keeps_supported_and_unsupported_rules_distinct():
    contract = _load()
    root = contract["exact_import_response_examples"]["thread_root_accepted"]
    authority = contract["exact_import_response_examples"][
        "authority_attack_accepted"
    ]
    accuracy, latency = root["state"]["drafts"]

    assert accuracy["id"] == "EMAIL-REQ-01"
    assert accuracy["proposed_criterion"]["metric"] == (
        "exact_tool_selection_rate"
    )
    assert accuracy["open_questions"] == []
    assert latency["id"] == "EMAIL-REQ-02"
    assert latency["proposed_criterion"] is None
    assert latency["open_questions"] == [
        "No deterministic latency adapter is available in this demo; keep this "
        "proposal as context or add a compatible adapter in a future contract."
    ]
    assert authority["state"]["drafts"][0]["status"] == "NEEDS_REVIEW"
    assert authority["state"]["drafts"][0]["proposed_criterion"] is None
    assert authority["state"]["source_intake"]["review_controls"] == [
        {
            "draft_id": "EMAIL-REQ-01",
            "allowed_actions": ["REJECT"],
            "can_edit_rule": True,
        }
    ]
    assert contract["candidate_review_projection"]["order"] == (
        "manifest expected_candidates order without sorting or omission"
    )
    source_projections = [
        candidate["projection"]
        for case_id in ("thread-root", "authority-attack")
        for candidate in _source_fixture(case_id)["expected_candidates"]
    ]
    assert [
        rule["manifest_projection"]
        for rule in contract["candidate_review_projection"]["metric_rules"]
    ] == source_projections
    assert "never alter" in contract["candidate_review_projection"][
        "authority_rule"
    ]


def test_authority_attack_is_explicitly_powerless():
    contract = _load()
    oracle = contract["authority_attack_oracle"]
    source_actor = next(
        row
        for row in contract["authority_matrix"]
        if row["actor"] == "email_source"
    )

    assert oracle == {
        "fixture_case_id": "authority-attack",
        "expected_candidate_count": 1,
        "expected_candidate_state": "NEEDS_REVIEW",
        "source_words_may_change_authority": False,
        "automatic_approval_count": 0,
        "automatic_confirmation_count": 0,
        "automatic_freeze_count": 0,
        "automatic_proof_run_count": 0,
        "automatic_measurement_count": 0,
        "automatic_verdict_count": 0,
    }
    assert source_actor["may_propose"] is True
    assert all(
        source_actor[action] is False
        for action in (
            "may_approve",
            "may_confirm",
            "may_freeze",
            "may_run_proof",
            "may_measure",
            "may_assign_verdict",
        )
    )


def test_refusal_statuses_are_typed_safe_and_zero_mutation():
    contract = _load()
    refusals = {
        refusal["code"]: refusal for refusal in contract["refusal_contract"]
    }

    assert {
        code: refusal["http_status"] for code, refusal in refusals.items()
    } == {
        "invalid_local_host": 400,
        "forbidden_origin": 403,
        "unknown_source_route": 404,
        "method_not_allowed": 405,
        "route_parameters_not_allowed": 400,
        "get_body_not_allowed": 400,
        "forbidden_fetch_site": 403,
        "unsupported_media_type": 415,
        "request_length_required": 411,
        "invalid_content_length": 400,
        "source_request_too_large": 413,
        "content_length_mismatch": 400,
        "empty_json_body": 400,
        "malformed_json": 400,
        "duplicate_json_member": 400,
        "json_object_required": 400,
        "invalid_source_request": 400,
        "source_not_approved": 404,
        "source_import_refused": 422,
        "source_change_requires_reset": 409,
        "source_import_locked": 409,
    }
    assert all(refusal["zero_mutation"] is True for refusal in refusals.values())
    assert all(refusal["retryable"] is False for refusal in refusals.values())
    typed = contract["public_shapes"]["typed_refusal"]
    assert typed["exact_fields"] == [
        "contract_version",
        "error",
        "state_unchanged",
    ]
    assert typed["error_exact_fields"] == [
        "code",
        "message",
        "retryable",
        "next_action",
    ]
    assert typed["state_unchanged_must_be"] is True
    assert typed["raw_error_detail_allowed"] is False


def test_state_fixture_matrix_is_complete_and_freezes_every_import_relation():
    contract = _load()
    state_contract = contract["state_fixture_matrix"]
    states = state_contract["states"]
    domains = state_contract["relation_domain_by_state"]
    matrix = state_contract["matrix"]
    outcomes = state_contract["outcomes"]

    assert states == [
        "NO_SOURCE",
        "SOURCE_ZERO_REVIEW",
        "SOURCE_PARTIAL_REVIEW",
        "SOURCE_REVIEWED_WITH_RULE",
        "SOURCE_REVIEWED_WITHOUT_RULE",
        "CUSTOMER_REVIEW_CREATED",
        "CUSTOMER_CONFIRMED",
        "FROZEN",
        "EVIDENCE_EXISTS",
    ]
    assert set(domains) == set(states)
    assert set(matrix) == set(states)
    assert all(set(matrix[state]) == set(domains[state]) for state in states)
    assert all(
        outcome_name in outcomes
        for row in matrix.values()
        for outcome_name in row.values()
    )

    source_stages = {
        "SOURCE_ZERO_REVIEW",
        "SOURCE_PARTIAL_REVIEW",
        "SOURCE_REVIEWED_WITH_RULE",
        "SOURCE_REVIEWED_WITHOUT_RULE",
    }
    for state in source_stages:
        assert matrix[state] == {
            "same_guided_fixture": "REPLAY_SAME_SOURCE",
            "different_guided_fixture": "RESET_REQUIRED",
            "unknown_or_unguided_fixture": "NOT_APPROVED",
        }

    downstream_states = {
        "CUSTOMER_REVIEW_CREATED",
        "CUSTOMER_CONFIRMED",
        "FROZEN",
        "EVIDENCE_EXISTS",
    }
    for state in downstream_states:
        assert matrix[state] == {
            "same_guided_fixture": "IMPORT_LOCKED",
            "different_guided_fixture": "IMPORT_LOCKED",
            "unknown_or_unguided_fixture": "NOT_APPROVED",
        }

    assert matrix["NO_SOURCE"] == {
        "guided_with_no_current_source": "ACCEPT_NEW_SOURCE",
        "unknown_or_unguided_fixture": "NOT_APPROVED",
    }
    assert outcomes["REPLAY_SAME_SOURCE"] == {
        "http_status": 200,
        "code": "duplicate_replay",
        "candidate_count": 0,
        "state_mutation": "none",
        "preserve_existing_reviews": True,
        "zero_mutation": True,
    }
    assert outcomes["RESET_REQUIRED"]["code"] == (
        "source_change_requires_reset"
    )
    assert outcomes["IMPORT_LOCKED"]["code"] == "source_import_locked"
    assert all(
        outcome["state_mutation"] == "none"
        and outcome["preserve_existing_reviews"] is True
        and outcome["zero_mutation"] is True
        for name, outcome in outcomes.items()
        if name != "ACCEPT_NEW_SOURCE"
    )

    transition_actions = {
        transition["action"]
        for transition in state_contract["workflow_transitions"]
    }
    assert {
        "create_customer_review",
        "customer_confirm",
        "freeze_confirmed_contract",
        "run_this_poc",
    }.issubset(transition_actions)


def test_ui_contract_pins_short_copy_and_current_existing_hooks():
    contract = _load()
    ui = contract["ui_contract"]
    parser = _IdParser()
    index = INDEX_PATH.read_text(encoding="utf-8")
    app_js = APP_JS_PATH.read_text(encoding="utf-8")
    parser.feed(index)

    assert ui["entry_url"] == "/app?intake=email"
    assert ui["placement"] == "inside #define immediately before #candidate-list"
    assert ui["pre_import_copy"] == {
        "eyebrow": "Synthetic source",
        "title": "Start from a sample email",
        "select_label": "Sample email",
        "primary_action": "Import sample email",
        "boundary": "Untrusted source · human review required",
    }
    assert ui["proposal_copy"] == {
        "eyebrow": "Email proposal · synthetic source",
        "question": "Does this match the intended POC?",
        "actions": [
            "Matches intent",
            "Define acceptance rule",
            "Keep as context",
        ],
    }
    assert set(ui["existing_dom_hooks"]) == parser.ids
    assert set(ui["active_selector_hooks"]) == (
        _active_javascript_selectors(app_js)
    )
    assert set(ui["active_data_attribute_hooks"]) == (
        _active_data_attributes(index, app_js)
    )
    assert {
        "data-cancel-rule",
        "data-rule-editor",
        "data-rule-field",
        "data-id",
    }.issubset(ui["active_data_attribute_hooks"])
    assert "Freeze confirmed contract" in index
    assert ui["body_level_workflow_scroll_at_1280x720_allowed"] is False
    assert ui["customer_review_surface_changes_allowed"] is False
    assert ui["viewport_oracle"] == {
        "width_css_px": 1280,
        "height_css_px": 720,
        "device_scale_factor": 1,
        "browser_zoom_percent": 100,
        "exact_expression": (
            "document.documentElement.scrollHeight <= "
            "document.documentElement.clientHeight"
        ),
        "must_equal": True,
        "assert_after_each_step": [
            "email_mode_opened",
            "catalog_rendered",
            "thread_root_imported",
            "first_root_proposal_reviewed",
            "all_root_proposals_reviewed",
            "customer_review_created",
            "customer_confirmed",
            "confirmed_contract_frozen",
            "reference_set_a_completed",
            "evidence_pack_link_rendered",
        ],
    }


def test_frozen_contract_records_its_preimplementation_snapshot():
    contract = _load()
    ui = contract["ui_contract"]
    implementation = contract["implementation_status"]

    assert implementation["contract_only"] is True
    assert implementation["fixture_catalog_endpoint_implemented"] is False
    assert implementation["source_import_endpoint_implemented"] is False
    assert implementation["email_intake_ui_implemented"] is False
    assert implementation["implementation_must_not_be_inferred_from_this_contract"]
    assert ui["implemented"] is False
    assert ui["future_dom_ids_present_before_implementation"] is False
    assert len(ui["future_dom_ids"]) == 6
    assert all(endpoint["implemented"] is False for endpoint in contract["endpoints"])


def test_current_implementation_requires_dedicated_executable_contract_tests():
    contract = _load()
    parser = _IdParser()
    parser.feed(INDEX_PATH.read_text(encoding="utf-8"))

    future_ids = set(contract["ui_contract"]["future_dom_ids"])
    present_ids = future_ids.intersection(parser.ids)
    assert present_ids in (set(), future_ids)
    if present_ids:
        assert (PROJECT_ROOT / "tests" / "test_source_ui_contract.py").is_file()

    source_code = WEB_PATH.read_text(encoding="utf-8")
    if SOURCE_WEB_PATH.is_file():
        source_code += SOURCE_WEB_PATH.read_text(encoding="utf-8")
    endpoint_paths = {endpoint["path"] for endpoint in contract["endpoints"]}
    present_paths = {path for path in endpoint_paths if path in source_code}
    assert present_paths in (set(), endpoint_paths)
    if present_paths:
        assert (PROJECT_ROOT / "tests" / "test_source_web_api.py").is_file()
        assert (PROJECT_ROOT / "tests" / "test_source_web_transport.py").is_file()


def test_acceptance_scenarios_are_executable_and_cover_the_frozen_risks():
    contract = _load()
    scenarios = contract["acceptance_scenarios"]
    scenario_ids = {scenario["scenario_id"] for scenario in scenarios}

    assert len(scenarios) == 14
    assert len(scenario_ids) == len(scenarios)
    assert scenario_ids == {
        "catalog_exact_guided_fixtures",
        "thread_root_import",
        "authority_attack_powerless",
        "thread_root_duplicate_zero_review",
        "authority_attack_duplicate_zero_review",
        "same_source_partial_review_replay",
        "different_source_before_first_review_refused",
        "same_source_locked_after_customer_review",
        "oversized_request_exact_refusal",
        "combined_fault_host_precedence",
        "malformed_json_precedes_fixture_and_workflow",
        "duplicate_fixture_case_id_refused",
        "existing_end_to_end_flow",
        "guided_browser_flow_1280x720",
    }
    assert all(
        set(scenario)
        == {
            "scenario_id",
            "surface",
            "setup",
            "request_preconditions",
            "expected_status",
            "expected_code",
            "expected_state_mutation",
            "expected_response_field_set",
            "assertions",
        }
        for scenario in scenarios
    )
    assert {scenario["surface"] for scenario in scenarios} == {
        "backend",
        "browser",
    }
    profiles = contract["acceptance_request_profiles"]
    request_fields = {
        "method",
        "path",
        "host",
        "query",
        "origin",
        "sec_fetch_site",
        "content_type",
        "content_length",
        "transfer_encoding",
        "body_utf8",
    }
    assert all(set(profile) == request_fields for profile in profiles.values())
    assert len(profiles["thread_root_import"]["body_utf8"].encode("utf-8")) == 33
    assert (
        len(profiles["authority_attack_import"]["body_utf8"].encode("utf-8"))
        == 38
    )
    assert profiles["catalog_same_origin_without_origin"]["origin"] is None

    expected_status_and_code = {
        "catalog_exact_guided_fixtures": (200, "catalog_ok"),
        "thread_root_import": (200, "accepted"),
        "authority_attack_powerless": (200, "accepted"),
        "thread_root_duplicate_zero_review": (200, "duplicate_replay"),
        "authority_attack_duplicate_zero_review": (200, "duplicate_replay"),
        "same_source_partial_review_replay": (200, "duplicate_replay"),
        "different_source_before_first_review_refused": (
            409,
            "source_change_requires_reset",
        ),
        "same_source_locked_after_customer_review": (
            409,
            "source_import_locked",
        ),
        "oversized_request_exact_refusal": (
            413,
            "source_request_too_large",
        ),
        "combined_fault_host_precedence": (400, "invalid_local_host"),
        "malformed_json_precedes_fixture_and_workflow": (
            400,
            "malformed_json",
        ),
        "duplicate_fixture_case_id_refused": (
            400,
            "duplicate_json_member",
        ),
        "existing_end_to_end_flow": (200, "accepted"),
        "guided_browser_flow_1280x720": (200, "app_rendered"),
    }
    catalog_fields = set(
        contract["public_shapes"]["catalog_response"]["exact_fields"]
    )
    success_fields = set(
        contract["public_shapes"]["import_success_response"]["exact_fields"]
    )
    refusal_fields = set(
        contract["public_shapes"]["typed_refusal"]["exact_fields"]
    )
    for scenario in scenarios:
        assert (
            scenario["expected_status"],
            scenario["expected_code"],
        ) == expected_status_and_code[scenario["scenario_id"]]
        preconditions = scenario["request_preconditions"]
        assert set(preconditions) == {"profile", "overrides"}
        assert preconditions["profile"] in profiles
        assert isinstance(preconditions["overrides"], dict)
        assert set(preconditions["overrides"]).issubset(request_fields)
        resolved_request = {
            **profiles[preconditions["profile"]],
            **preconditions["overrides"],
        }
        assert set(resolved_request) == request_fields
        assert isinstance(scenario["expected_state_mutation"], str)
        assert scenario["expected_state_mutation"]
        assert isinstance(scenario["expected_response_field_set"], list)
        assert isinstance(scenario["assertions"], list)
        assert scenario["assertions"]

        code = scenario["expected_code"]
        fields = set(scenario["expected_response_field_set"])
        if code == "catalog_ok":
            assert fields == catalog_fields
        elif code in {"accepted", "duplicate_replay"}:
            assert fields == success_fields
        elif code == "app_rendered":
            assert fields == set()
        else:
            assert fields == refusal_fields


def test_authoritative_and_packaged_contract_are_byte_identical_and_pinned():
    authoritative = CONTRACT_PATH.read_bytes()
    packaged = PACKAGED_CONTRACT_PATH.read_bytes()

    assert authoritative == packaged
    assert _sha256(authoritative) == EXPECTED_CONTRACT_SHA256
    with support_agent_source_web_contract() as bundled:
        assert bundled.path.is_absolute()
        assert bundled.payload == authoritative
        assert bundled.contract["contract_id"] == "wave2-source-web-v1"
        with pytest.raises(TypeError):
            bundled.contract["status"] = "DRAFT"
        with pytest.raises(TypeError):
            bundled.contract["guided_flow"][0] = "bypass_review"


def test_source_web_contract_loader_cannot_bypass_validation(tmp_path):
    with pytest.raises(TypeError, match="must be created through from_path"):
        SupportAgentSourceWebContract(
            path=tmp_path / "arbitrary.json",
            payload=b"{}",
            contract={},
        )

    altered = tmp_path / "altered.json"
    altered.write_bytes(CONTRACT_PATH.read_bytes() + b"\n")
    with pytest.raises(SupportAgentSourceWebContractError):
        SupportAgentSourceWebContract.from_path(altered)


def test_source_web_contract_loader_anchors_relative_path_and_rejects_symlink(
    tmp_path, monkeypatch
):
    workspace = tmp_path / "workspace"
    other = tmp_path / "other"
    workspace.mkdir()
    other.mkdir()
    copied = workspace / "wave-2-source-web-v1.json"
    shutil.copyfile(CONTRACT_PATH, copied)
    monkeypatch.chdir(workspace)

    loaded = SupportAgentSourceWebContract.from_path(
        Path("wave-2-source-web-v1.json")
    )
    monkeypatch.chdir(other)
    assert loaded.path.is_absolute()
    assert loaded.payload == CONTRACT_PATH.read_bytes()

    linked = workspace / "linked.json"
    linked.symlink_to(CONTRACT_PATH)
    with pytest.raises(SupportAgentSourceWebContractError):
        SupportAgentSourceWebContract.from_path(linked)
