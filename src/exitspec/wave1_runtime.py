"""Wheel-safe runtime projection for the frozen Wave-1 provider policy.

This module deliberately contains no file loading, environment lookup, provider
executor, or transport.  It reconstructs one code-pinned synthetic request and
returns a content-free disclosure for the optional server-owned action.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from typing import Any, Dict

from .assisted_authoring import ProposalBatch, _provider_request
from .canonical import canonical_json_bytes
from .intake import redact_and_parse_pasted_transcript
from .provider_egress import (
    WAVE1_POLICY_IDENTITY_SHA256,
    ProviderEgressPolicy,
    build_provider_egress_intent,
)
from .providers import StructuredJSONRequest


_MANIFEST_ID = "exitspec-wave-1-fireworks-assisted-authoring"
_MANIFEST_VERSION = "1.0.0"
_SOURCE_FIXTURE_SHA256 = (
    "159c7729450b1ace0646f25943850b5561a3307558eb41f9a0fd48628a436a94"
)
_SOURCE_CASE_ID = "measurable-integer-threshold"
_SOURCE_CASE_TITLE = "Exact tool selection with integer threshold"
_SOURCE_CASE_TRANSCRIPT = (
    "Customer: The agent must reach 95% exact tool-selection accuracy "
    "across 200 approved cases."
)
_REDACTED_PAYLOAD_DIGEST = (
    "417e3f118997ca43e317f46b69cce72537c2a5b2a1e258f5d2e93a329717dc6d"
)
_REDACTION_CONFIGURATION_DIGEST = (
    "ff66c680e81956fad57d019ea9a517fbf305c1adf752432f6914dbc9d4ca4422"
)
_PROVIDER = "fireworks"
_MODEL = "accounts/fireworks/models/deepseek-v4-flash"
_ENDPOINT = "https://api.fireworks.ai/inference/v1/chat/completions"
_REDACTION_POLICY_VERSION = "exitspec-transcript-redaction/1.0"
_ACKNOWLEDGEMENT_POLICY_VERSION = "exitspec-provider-egress/1.0"
_ACKNOWLEDGEMENT_TTL_SECONDS = 300
_DISCLOSURE_DOMAIN = b"exitspec-wave-1-provider-disclosure-v1\x00"


def _frozen_policy_manifest_projection() -> Dict[str, Any]:
    """Return fresh manifest fields needed to reconstruct the frozen policy."""

    return {
        "manifest_id": _MANIFEST_ID,
        "manifest_version": _MANIFEST_VERSION,
        "status": "FROZEN",
        "source_fixture": {
            "sha256": _SOURCE_FIXTURE_SHA256,
            "synthetic_only": True,
            "live_smoke_case_id": _SOURCE_CASE_ID,
        },
        "approved_live_smoke_request": {
            "source_case_id": _SOURCE_CASE_ID,
            "redacted_payload_digest": _REDACTED_PAYLOAD_DIGEST,
            "redaction_configuration_digest": (
                _REDACTION_CONFIGURATION_DIGEST
            ),
        },
        "provider_boundary": {
            "provider": _PROVIDER,
            "model": _MODEL,
            "endpoint": _ENDPOINT,
            "redaction_policy_version": _REDACTION_POLICY_VERSION,
            "pricing_snapshot": {
                "currency": "USD",
                "unit": "per_1_million_tokens",
                "input": "0.14",
                "cached_input": "0.028",
                "output": "0.28",
                "effective_checked_at": "2026-07-27",
                "source": "https://docs.fireworks.ai/serverless/pricing",
            },
            "data_policy_snapshot": {
                "effective_checked_at": "2026-07-27",
                "source": (
                    "https://docs.fireworks.ai/guides/"
                    "security_compliance/data_handling"
                ),
                "prompt_and_generation_persistent_retention": (
                    "none_by_default_for_open_models_without_explicit_opt_in"
                ),
                "prompt_cache": (
                    "volatile_memory_for_several_minutes_when_active"
                ),
                "metadata_logging": (
                    "service_metadata_including_token_counts"
                ),
                "advanced_feature_opt_in": False,
                "region": "not_asserted_by_the_cited_provider_documentation",
            },
            "request_limits": {
                "estimated_input_tokens_max": 6000,
                "output_tokens_max": 2000,
                "timeout_seconds": 30,
                "max_attempts": 2,
                "max_retry_after_seconds": 10,
                "max_request_cost_usd": "0.01",
                "max_live_smoke_total_cost_usd": "0.10",
            },
        },
        "egress_acknowledgement": {
            "policy_version": _ACKNOWLEDGEMENT_POLICY_VERSION,
            "required": True,
            "server_validated": True,
            "one_time_use": True,
            "ttl_seconds": _ACKNOWLEDGEMENT_TTL_SECONDS,
            "payload_binding": (
                "sha256_of_canonical_redacted_request_intent"
            ),
            "bound_fields": [
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
            ],
            "required_rejections": [
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
            ],
        },
    }


def frozen_wave1_policy() -> ProviderEgressPolicy:
    """Return a fresh policy anchored to the code-pinned Wave-1 identity."""

    policy = ProviderEgressPolicy.from_frozen_manifest(
        _frozen_policy_manifest_projection()
    )
    if (
        len(WAVE1_POLICY_IDENTITY_SHA256) != 64
        or not policy.is_frozen_wave1_policy()
    ):
        raise RuntimeError("The code-pinned Wave-1 provider policy is invalid.")
    return policy


def build_frozen_wave1_request() -> StructuredJSONRequest[ProposalBatch]:
    """Rebuild and bind the one frozen, redacted synthetic provider request."""

    policy = frozen_wave1_policy()
    intake = redact_and_parse_pasted_transcript(
        _SOURCE_CASE_TRANSCRIPT,
        transcript_id=_SOURCE_CASE_ID,
        title=_SOURCE_CASE_TITLE,
        customer_terms=(),
    )
    request, _ = _provider_request(
        intake,
        model=policy.model,
        customer_terms=(),
    )
    request = replace(
        request,
        budget_usd=policy.max_request_cost_usd,
    )
    intent = build_provider_egress_intent(
        request,
        policy=policy,
        customer_terms=(),
    )
    if intent.redacted_payload_digest != _REDACTED_PAYLOAD_DIGEST:
        raise RuntimeError("The frozen Wave-1 provider request is invalid.")
    return request


def frozen_wave1_source() -> Dict[str, Any]:
    """Return a fresh copy of the one approved synthetic authoring source."""

    return {
        "transcript_id": _SOURCE_CASE_ID,
        "title": _SOURCE_CASE_TITLE,
        "transcript": _SOURCE_CASE_TRANSCRIPT,
        "customer_terms": [],
    }


def _detached_json_object(value: Dict[str, Any]) -> Dict[str, Any]:
    return json.loads(
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )


def wave1_provider_disclosure() -> Dict[str, Any]:
    """Return a detached disclosure without exposing execution authority."""

    policy = frozen_wave1_policy()
    limits = policy.request_limits()
    disclosure = {
        "manifest": {
            "id": policy.acceptance_manifest_id,
            "version": policy.acceptance_manifest_version,
            "policy_identity_sha256": WAVE1_POLICY_IDENTITY_SHA256,
        },
        "provider": policy.provider,
        "model": policy.model,
        "endpoint": policy.endpoint,
        "synthetic_case": {
            "case_id": policy.source_case_id,
            "source_fixture_sha256": policy.source_fixture_sha256,
            "redacted_payload_digest": policy.redacted_payload_digest,
            "redaction_configuration_digest": (
                policy.redaction_configuration_digest
            ),
            "synthetic_only": True,
        },
        "pricing_snapshot": policy.pricing_snapshot(),
        "data_policy_snapshot": policy.data_policy_snapshot(),
        "limits": {
            "estimated_input_tokens_max": limits[
                "estimated_input_tokens_max"
            ],
            "output_tokens_max": limits["output_tokens_max"],
            "timeout_seconds": limits["timeout_seconds"],
            "max_attempts": limits["max_attempts"],
            "max_retry_after_seconds": limits[
                "max_retry_after_seconds"
            ],
            "max_request_cost_usd": limits["max_request_cost_usd"],
            "max_live_smoke_total_cost_usd": limits[
                "max_live_smoke_total_cost_usd"
            ],
        },
        "acknowledgement_policy": {
            "policy_version": policy.acknowledgement_policy_version,
            "required": True,
            "server_validated": True,
            "one_time_use": True,
            "ttl_seconds": policy.acknowledgement_ttl_seconds,
            "payload_binding": "exact_frozen_request_digest",
        },
        "execution_policy": {
            "server_owned_action": True,
            "disabled_by_default": True,
            "requires_active_authorization": True,
            "browser_supplied_request_fields": [],
        },
        "next_action": (
            "Review this disclosure, acknowledge the exact synthetic request, "
            "then run the server-owned action once."
        ),
    }
    disclosure_digest = hashlib.sha256(
        _DISCLOSURE_DOMAIN + canonical_json_bytes(disclosure)
    ).hexdigest()
    disclosure["disclosure_id"] = (
        "wave1_provider_disclosure_" + disclosure_digest
    )
    return _detached_json_object(disclosure)
