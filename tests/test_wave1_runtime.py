import ast
import builtins
import hashlib
import inspect
import json
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

import exitspec.wave1_runtime as wave1_runtime
from exitspec.assisted_authoring import _provider_request
from exitspec.canonical import canonical_json_bytes
from exitspec.intake import redact_and_parse_pasted_transcript
from exitspec.provider_egress import (
    WAVE1_POLICY_IDENTITY_SHA256,
    ProviderEgressPolicy,
    build_provider_egress_intent,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = (
    PROJECT_ROOT
    / "examples/support-agent/fireworks/wave-1-acceptance-v1.json"
)
DISCLOSURE_DOMAIN = b"exitspec-wave-1-provider-disclosure-v1\x00"


def _load_manifest_and_fixture():
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    fixture = json.loads(
        (PROJECT_ROOT / manifest["source_fixture"]["path"]).read_text(
            encoding="utf-8"
        )
    )
    return manifest, fixture


def _approved_fixture_case(manifest, fixture):
    case_id = manifest["approved_live_smoke_request"]["source_case_id"]
    return next(case for case in fixture["cases"] if case["id"] == case_id)


def _request_projection(request):
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


def _all_mapping_keys(value, prefix=()):
    if isinstance(value, dict):
        for key, nested in value.items():
            path = prefix + (key,)
            yield path
            yield from _all_mapping_keys(nested, path)
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            yield from _all_mapping_keys(nested, prefix + (str(index),))


def test_frozen_policy_is_fresh_and_exactly_matches_authoritative_manifest():
    manifest, _ = _load_manifest_and_fixture()
    expected = ProviderEgressPolicy.from_frozen_manifest(manifest)

    first = wave1_runtime.frozen_wave1_policy()
    second = wave1_runtime.frozen_wave1_policy()

    assert first is not second
    assert first.identity_payload() == expected.identity_payload()
    assert first.is_frozen_wave1_policy()
    assert WAVE1_POLICY_IDENTITY_SHA256 == (
        "5dcd98965bc158ed915f4bf9207a8b43e647e2d069126da970ea36bac008aa71"
    )

    detached = first.data_policy_snapshot()
    detached["region"] = "mutated"
    assert second.data_policy_snapshot() == manifest["provider_boundary"][
        "data_policy_snapshot"
    ]
    with pytest.raises(ValidationError, match="Instance is frozen"):
        first.endpoint = "https://provider.example.test/v1/chat/completions"


def test_frozen_request_exactly_matches_authoritative_synthetic_fixture():
    manifest, fixture = _load_manifest_and_fixture()
    case = _approved_fixture_case(manifest, fixture)
    expected_policy = ProviderEgressPolicy.from_frozen_manifest(manifest)
    intake = redact_and_parse_pasted_transcript(
        case["transcript"],
        transcript_id=case["id"],
        title=case["title"],
        customer_terms=case["customer_terms"],
    )
    expected_request, _ = _provider_request(
        intake,
        model=expected_policy.model,
        customer_terms=case["customer_terms"],
    )
    expected_request = replace(
        expected_request,
        budget_usd=expected_policy.max_request_cost_usd,
    )

    actual_request = wave1_runtime.build_frozen_wave1_request()

    assert _request_projection(actual_request) == _request_projection(
        expected_request
    )
    assert actual_request.budget_usd == Decimal(
        manifest["provider_boundary"]["request_limits"][
            "max_request_cost_usd"
        ]
    )
    intent = build_provider_egress_intent(
        actual_request,
        policy=wave1_runtime.frozen_wave1_policy(),
        customer_terms=case["customer_terms"],
    )
    assert intent.redacted_payload_digest == manifest[
        "approved_live_smoke_request"
    ]["redacted_payload_digest"]


def test_frozen_request_returns_fresh_detached_mutable_snapshots():
    first = wave1_runtime.build_frozen_wave1_request()
    second = wave1_runtime.build_frozen_wave1_request()

    assert first is not second
    assert first.messages is not second.messages
    assert first.response_schema is not second.response_schema
    first.response_schema["runtime_mutation"] = True
    assert "runtime_mutation" not in second.response_schema
    assert (
        "runtime_mutation"
        not in wave1_runtime.build_frozen_wave1_request().response_schema
    )


def test_frozen_source_matches_request_fixture_and_returns_a_fresh_copy():
    manifest, fixture = _load_manifest_and_fixture()
    case = _approved_fixture_case(manifest, fixture)

    first = wave1_runtime.frozen_wave1_source()
    second = wave1_runtime.frozen_wave1_source()

    assert first == {
        "transcript_id": case["id"],
        "title": case["title"],
        "transcript": case["transcript"],
        "customer_terms": case["customer_terms"],
    }
    assert first is not second
    assert first["customer_terms"] is not second["customer_terms"]
    first["customer_terms"].append("runtime-mutation")
    assert wave1_runtime.frozen_wave1_source() == second


def test_disclosure_is_exact_content_free_json_and_execution_is_bounded():
    manifest, fixture = _load_manifest_and_fixture()
    case = _approved_fixture_case(manifest, fixture)
    disclosure = wave1_runtime.wave1_provider_disclosure()

    assert set(disclosure) == {
        "disclosure_id",
        "manifest",
        "provider",
        "model",
        "endpoint",
        "synthetic_case",
        "pricing_snapshot",
        "data_policy_snapshot",
        "limits",
        "acknowledgement_policy",
        "execution_policy",
        "next_action",
    }
    assert set(disclosure["manifest"]) == {
        "id",
        "version",
        "policy_identity_sha256",
    }
    assert set(disclosure["synthetic_case"]) == {
        "case_id",
        "source_fixture_sha256",
        "redacted_payload_digest",
        "redaction_configuration_digest",
        "synthetic_only",
    }
    assert set(disclosure["limits"]) == set(
        manifest["provider_boundary"]["request_limits"]
    )
    assert set(disclosure["acknowledgement_policy"]) == {
        "policy_version",
        "required",
        "server_validated",
        "one_time_use",
        "ttl_seconds",
        "payload_binding",
    }
    assert disclosure["execution_policy"] == {
        "server_owned_action": True,
        "disabled_by_default": True,
        "requires_active_authorization": True,
        "browser_supplied_request_fields": [],
    }

    assert disclosure["provider"] == manifest["provider_boundary"]["provider"]
    assert disclosure["model"] == manifest["provider_boundary"]["model"]
    assert disclosure["endpoint"] == manifest["provider_boundary"]["endpoint"]
    assert disclosure["pricing_snapshot"] == manifest["provider_boundary"][
        "pricing_snapshot"
    ]
    assert disclosure["data_policy_snapshot"] == manifest[
        "provider_boundary"
    ]["data_policy_snapshot"]
    assert disclosure["limits"] == manifest["provider_boundary"][
        "request_limits"
    ]
    assert disclosure["synthetic_case"] == {
        "case_id": case["id"],
        "source_fixture_sha256": manifest["source_fixture"]["sha256"],
        "redacted_payload_digest": manifest[
            "approved_live_smoke_request"
        ]["redacted_payload_digest"],
        "redaction_configuration_digest": manifest[
            "approved_live_smoke_request"
        ]["redaction_configuration_digest"],
        "synthetic_only": True,
    }
    assert disclosure["acknowledgement_policy"] == {
        "policy_version": manifest["egress_acknowledgement"][
            "policy_version"
        ],
        "required": True,
        "server_validated": True,
        "one_time_use": True,
        "ttl_seconds": 300,
        "payload_binding": "exact_frozen_request_digest",
    }
    assert disclosure["next_action"] == (
        "Review this disclosure, acknowledge the exact synthetic request, "
        "then run the server-owned action once."
    )
    json.dumps(disclosure, allow_nan=False)

    serialized = json.dumps(disclosure, sort_keys=True)
    assert case["transcript"] not in serialized
    assert "Untrusted redacted transcript JSON follows" not in serialized
    assert '"messages"' not in serialized
    assert '"response_schema"' not in serialized
    assert "FIREWORKS_API_KEY" not in serialized
    assert "Bearer " not in serialized
    assert "api_key" not in serialized.lower()
    assert '"authorization":' not in serialized.lower()

    forbidden_authority_fragments = {
        "authority",
        "approval",
        "approved",
        "confirmation",
        "confirmed",
        "contract",
        "freeze",
        "verdict",
        "measurement",
    }
    for key_path in _all_mapping_keys(disclosure):
        normalized = key_path[-1].lower()
        assert not any(
            fragment in normalized
            for fragment in forbidden_authority_fragments
        )
    token_paths = {
        ".".join(path)
        for path in _all_mapping_keys(disclosure)
        if "token" in path[-1].lower()
    }
    assert token_paths == {
        "limits.estimated_input_tokens_max",
        "limits.output_tokens_max",
    }


def test_disclosure_id_is_stable_content_bound_and_returns_a_fresh_copy():
    first = wave1_runtime.wave1_provider_disclosure()
    second = wave1_runtime.wave1_provider_disclosure()

    assert first == second
    assert first is not second
    assert first["pricing_snapshot"] is not second["pricing_snapshot"]

    disclosure_id = first.pop("disclosure_id")
    expected_digest = hashlib.sha256(
        DISCLOSURE_DOMAIN + canonical_json_bytes(first)
    ).hexdigest()
    assert disclosure_id == "wave1_provider_disclosure_" + expected_digest

    first["pricing_snapshot"]["input"] = "999"
    fresh = wave1_runtime.wave1_provider_disclosure()
    assert fresh["pricing_snapshot"]["input"] == "0.14"
    assert fresh["disclosure_id"] == second["disclosure_id"]


def test_runtime_does_not_read_checkout_files_or_environment(
    monkeypatch,
):
    sentinel = "DO_NOT_LOAD_RUNTIME_CREDENTIAL"
    monkeypatch.setenv("FIREWORKS_API_KEY", sentinel)

    def fail_file_read(*args, **kwargs):
        raise AssertionError("Runtime projection attempted a file read.")

    monkeypatch.setattr(builtins, "open", fail_file_read)
    monkeypatch.setattr(Path, "read_text", fail_file_read)
    monkeypatch.setattr(Path, "read_bytes", fail_file_read)

    policy = wave1_runtime.frozen_wave1_policy()
    request = wave1_runtime.build_frozen_wave1_request()
    disclosure = wave1_runtime.wave1_provider_disclosure()

    public_text = json.dumps(disclosure, sort_keys=True)
    assert sentinel not in public_text
    assert sentinel not in repr(policy)
    assert sentinel not in repr(request)
    assert "FIREWORKS_API_KEY" not in public_text


def test_runtime_module_has_no_network_environment_or_execution_seam():
    module_source = inspect.getsource(wave1_runtime)
    tree = ast.parse(module_source)
    imported_roots = set()
    referenced_names = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_roots.update(
                alias.name.split(".", 1)[0] for alias in node.names
            )
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_roots.add(node.module.split(".", 1)[0])
            referenced_names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Name):
            referenced_names.add(node.id)
        elif isinstance(node, ast.Attribute):
            referenced_names.add(node.attr)

    assert imported_roots.isdisjoint(
        {
            "aiohttp",
            "http",
            "httpx",
            "os",
            "requests",
            "socket",
            "subprocess",
            "urllib",
        }
    )
    assert referenced_names.isdisjoint(
        {
            "AuthorizedFireworksExecutor",
            "FireworksProvider",
            "HTTPSConnection",
            "PinnedFireworksHTTPSTransport",
            "environ",
            "getenv",
            "urlopen",
        }
    )
