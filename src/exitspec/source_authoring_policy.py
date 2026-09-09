"""Synthetic-only source authorization values and exact r2 payload validation.

These public values confer no authority. No transport is imported or created;
the operation owner must issue and consume its own private one-use permits.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .assisted_authoring import (
    SourceNeutralProposalBatch,
    _validate_source_neutral_batch,
)
from .canonical import canonical_json_bytes
from .poc_sources import POCSourceSnapshot
from .redaction import POLICY_VERSION, assert_redaction_egress, redact_transcript
from .source_authoring_pins import REQUEST_PROFILE_JSON

PROFILE_SHA256 = "1ddd5c2f16ca9a4d44802b0c9491488684546d5096233b320451c9d3306c8ef6"
SCHEMA_SHA256 = "289330f67c67d1cb07e367858c72d6c67ec29f4750db17a4ff5cf2e32af9ff99"
SYSTEM_SHA256 = "5f113942ed99dbc8ca51adc4f65772fc1eb959007ae387738f902d466a8a4696"
ENDPOINT = "https://api.fireworks.ai/inference/v1/chat/completions"
MODEL = "accounts/fireworks/models/deepseek-v4-flash-0731"
Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Identity = Annotated[str, Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9_.:/-]+$")]


class SourceAuthoringPolicyError(ValueError):
    """Content-free public failure; never retains source or provider input."""

    def __init__(self, code: str = "invalid_binding") -> None:
        self.code = code
        super().__init__("Source authoring policy rejected the operation.")


def template_canonical_bytes(value: Any) -> bytes:
    """Canonical finite JSON, without allowing encoding errors to leak text."""
    failed = False
    try:
        result = json.dumps(value, ensure_ascii=False, allow_nan=False,
                            sort_keys=True, separators=(",", ":")).encode("utf-8")
    except (ValueError, TypeError, AttributeError, KeyError, RecursionError, OverflowError):
        failed = True
    if failed:
        raise SourceAuthoringPolicyError("invalid_json") from None
    return result


def canonical_bytes(value: Any) -> bytes:
    failed = False
    try:
        result = canonical_json_bytes(value)
    except (ValueError, TypeError, AttributeError, KeyError, RecursionError, OverflowError):
        failed = True
    if failed:
        raise SourceAuthoringPolicyError("invalid_json") from None
    return result


def sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def body_digest(body: bytes) -> str:
    if type(body) is not bytes:
        raise SourceAuthoringPolicyError()
    return sha256(b"exitspec-source-authoring-body-v1\0" + body)


HEADER_POLICY_SHA256 = sha256(canonical_bytes({
    "Accept": "application/json", "Content-Type": "application/json",
    "User-Agent": "ExitSpec/0.1 provider-boundary",
    "Authorization": "server-injected-bearer",
}))


REDACTION_CONFIGURATION_DIGEST = sha256(
    b"exitspec-source-authoring-redaction-config-v1\0" + canonical_bytes(
        {"policy_version": POLICY_VERSION, "customer_terms": []}
    )
)


class _Immutable(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid",
                              hide_input_in_errors=True, revalidate_instances="always")

    @field_validator("*", mode="before")
    @classmethod
    def reject_unsafe_scalars(cls, value: Any) -> Any:
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("Nonfinite policy value.")
        return value

    def model_copy(self, *, update: dict[str, Any] | None = None, deep: bool = False):
        values = self.model_dump(mode="python", warnings=False)
        values.update(update or {})
        return type(self).model_validate(values)


class SourceBoundAuthoringPolicy(_Immutable):
    """Fixed synthetic-core profile. Live prerequisites cannot be supplied."""

    policy_id: Literal["exitspec-source-authoring-synthetic-core-r2"] = "exitspec-source-authoring-synthetic-core-r2"
    version: Literal["r2"] = "r2"
    profile_sha256: Literal[PROFILE_SHA256] = PROFILE_SHA256
    schema_sha256: Literal[SCHEMA_SHA256] = SCHEMA_SHA256
    system_sha256: Literal[SYSTEM_SHA256] = SYSTEM_SHA256
    provider: Literal["fireworks"] = "fireworks"
    model: Literal[MODEL] = MODEL
    endpoint: Literal[ENDPOINT] = ENDPOINT
    method: Literal["POST"] = "POST"
    canonicalization_version: Literal["rfc8785-v1"] = "rfc8785-v1"
    template_canonicalization_version: Literal["sorted-compact-json-v1"] = "sorted-compact-json-v1"
    validator_version: Literal["a3-local-source-validator-v1"] = "a3-local-source-validator-v1"
    authoring_adapter_version: Literal["source-authoring-r2"] = "source-authoring-r2"
    tokenizer_profile: Literal["synthetic-only-no-live-token-proof"] = "synthetic-only-no-live-token-proof"
    pricing_profile: Literal["synthetic-only-no-live-pricing-approval"] = "synthetic-only-no-live-pricing-approval"
    redaction_configuration_digest: Literal[REDACTION_CONFIGURATION_DIGEST] = REDACTION_CONFIGURATION_DIGEST
    region_policy: Literal["synthetic-no-network"] = "synthetic-no-network"
    source_bytes_max: int = Field(default=16384, ge=16384, le=16384)
    body_bytes_max: int = Field(default=65536, ge=65536, le=65536)
    response_bytes_max: int = Field(default=262144, ge=262144, le=262144)
    input_tokens_max: int = Field(default=8192, ge=8192, le=8192)
    output_tokens_max: int = Field(default=2000, ge=2000, le=2000)
    attempts_max: int = Field(default=1, ge=1, le=1)
    deadline_seconds: int = Field(default=30, ge=30, le=30)
    consent_ttl_seconds: int = Field(default=300, ge=300, le=300)
    claim_interval_seconds: int = Field(default=10, ge=10, le=10)
    launch_claims_max: int = Field(default=10, ge=10, le=10)
    operations_max: int = Field(default=1024, ge=1024, le=1024)
    aliases_max: int = Field(default=16384, ge=16384, le=16384)
    request_budget_usd: Literal["0.01"] = "0.01"
    launch_budget_usd: Literal["0.10"] = "0.10"
    network_enabled: bool = False

    @model_validator(mode="after")
    def synthetic_only(self):
        if self.network_enabled:
            raise ValueError("Live activation is unavailable in synthetic core.")
        return self

    @property
    def digest(self) -> str:
        _validate_policy(self)
        return sha256(b"exitspec-source-authoring-policy-v1\0" + canonical_bytes(self.model_dump(warnings=False)))


def synthetic_policy() -> SourceBoundAuthoringPolicy:
    return SourceBoundAuthoringPolicy()


def require_live_activation(policy: SourceBoundAuthoringPolicy) -> None:
    """No configuration or proof can activate network in this checkpoint."""
    raise SourceAuthoringPolicyError("live_prerequisites_unavailable")


class SyntheticTokenProof(_Immutable):
    """Test declaration only, expressly not a real tokenizer or spend proof."""

    profile: Literal["synthetic-only-no-live-token-proof"] = "synthetic-only-no-live-token-proof"
    body_sha256: Digest
    input_tokens: int = Field(ge=1, le=8192)


def synthetic_token_proof(body: bytes, input_tokens: int) -> SyntheticTokenProof:
    failed = False
    try:
        result = SyntheticTokenProof(body_sha256=body_digest(body), input_tokens=input_tokens)
    except (ValueError, TypeError, AttributeError, KeyError, RecursionError, OverflowError):
        failed = True
    if failed:
        raise SourceAuthoringPolicyError("invalid_synthetic_token_proof") from None
    return result


def _profile() -> dict[str, Any]:
    if sha256(b"exitspec-source-authoring-request-profile-v1\0" + REQUEST_PROFILE_JSON.encode("utf-8")) != PROFILE_SHA256:
        raise SourceAuthoringPolicyError("profile_mismatch")
    profile = json.loads(REQUEST_PROFILE_JSON)
    body = profile["body_template"]
    schema = body["response_format"]["json_schema"]["schema"]
    if (sha256(template_canonical_bytes(schema)) != SCHEMA_SHA256
            or sha256(body["messages"][0]["content"].encode()) != SYSTEM_SHA256
            or template_canonical_bytes(SourceNeutralProposalBatch.model_json_schema()) != template_canonical_bytes(schema)):
        raise SourceAuthoringPolicyError("profile_mismatch")
    return profile


def _validate_policy(policy: SourceBoundAuthoringPolicy) -> None:
    failed = False
    try:
        if type(policy) is not SourceBoundAuthoringPolicy:
            raise ValueError
        SourceBoundAuthoringPolicy.model_validate(policy.model_dump(warnings=False))
    except (ValueError, TypeError, AttributeError, KeyError, RecursionError, OverflowError):
        failed = True
    if failed:
        raise SourceAuthoringPolicyError("invalid_policy") from None


def build_body(source: POCSourceSnapshot, *, policy: SourceBoundAuthoringPolicy | None = None) -> bytes:
    policy = synthetic_policy() if policy is None else policy
    _validate_policy(policy)
    failed = False
    try:
        if type(source) is not POCSourceSnapshot:
            raise ValueError
        source_values = source.model_dump(mode="python", warnings=False)
        source = POCSourceSnapshot.model_validate(source_values)
        if source.model_dump(mode="python", warnings=False) != source_values:
            raise ValueError
        text = source.redacted_text
        encoded = text.encode("utf-8")
        if not encoded or len(encoded) > policy.source_bytes_max or sha256(encoded) != source.content_sha256:
            raise ValueError
        if assert_redaction_egress(redact_transcript(text)) != text:
            raise ValueError
        body = _profile()["body_template"]
        body["messages"][1]["content"] = "Untrusted redacted source JSON follows:\n" + canonical_bytes({"text": text}).decode("utf-8")
        for message in body["messages"]:
            if assert_redaction_egress(redact_transcript(message["content"])) != message["content"]:
                raise ValueError
        result = canonical_bytes(body)
        if len(result) > policy.body_bytes_max:
            raise ValueError
    except (ValueError, TypeError, AttributeError, KeyError, RecursionError, OverflowError):
        failed = True
    if failed:
        raise SourceAuthoringPolicyError("invalid_source_or_body") from None
    return result


class SourceBoundAuthoringIntent(_Immutable):
    """Detached public binding; only the operation owner can mint authority."""

    server_epoch: Identity
    browser_session_id: Identity
    idempotency_id: Digest
    consent_generation: int = Field(ge=0, le=9007199254740991)
    operation_id: Identity
    draft_generation: Identity
    poc_id: Identity
    source_receipt_id: Identity
    source_id: Identity
    source_revision: int = Field(ge=1, le=9007199254740991)
    source_kind: Literal["EMAIL", "MEETING", "DOCUMENT", "EXISTING_CONTRACT"]
    content_classification: Literal["OWNER_APPROVED_REDACTED_BUSINESS_TEXT"]
    source_adapter_name: Identity
    source_adapter_version: Identity
    source_sha256: Digest
    redaction_policy_version: Identity
    redaction_configuration_digest: Digest
    policy: SourceBoundAuthoringPolicy
    policy_digest: Digest
    body_sha256: Digest
    header_policy_digest: Literal[HEADER_POLICY_SHA256] = HEADER_POLICY_SHA256
    credential_configuration_generation: int = Field(ge=0, le=9007199254740991)
    synthetic_input_tokens: int = Field(ge=1, le=8192)
    token_proof: SyntheticTokenProof
    launch_grant_id: Identity
    disclosure_version: Literal["source-authoring-disclosure-r2"] = "source-authoring-disclosure-r2"
    disclosure_digest: Digest
    issued_at: float = Field(ge=0)
    expires_at: float = Field(ge=0)
    issued_monotonic: float = Field(ge=0)
    expires_monotonic: float = Field(ge=0)
    acknowledged: bool
    acknowledged_at: float = Field(ge=0)

    @field_validator("issued_at", "expires_at", "issued_monotonic", "expires_monotonic", "acknowledged_at", mode="before")
    @classmethod
    def exact_clock_type(cls, value: Any) -> float:
        if type(value) is not float:
            raise ValueError("Clock binding requires a finite float.")
        return value

    @model_validator(mode="after")
    def consistent(self):
        if (not self.acknowledged or self.policy_digest != self.policy.digest
                or self.redaction_configuration_digest != self.policy.redaction_configuration_digest
                or self.token_proof.body_sha256 != self.body_sha256
                or self.token_proof.input_tokens != self.synthetic_input_tokens
                or self.expires_at != self.issued_at + self.policy.consent_ttl_seconds
                or self.expires_monotonic != self.issued_monotonic + self.policy.consent_ttl_seconds
                or not self.issued_at <= self.acknowledged_at < self.expires_at):
            raise ValueError("Inconsistent authorization binding.")
        return self

    @property
    def digest(self) -> str:
        failed = False
        try:
            if type(self) is not SourceBoundAuthoringIntent:
                raise ValueError
            checked = SourceBoundAuthoringIntent.model_validate(self.model_dump(warnings=False))
            result = sha256(b"exitspec-source-authoring-intent-v1\0" + canonical_bytes(checked.model_dump(warnings=False)))
        except (ValueError, TypeError, AttributeError, KeyError, RecursionError, OverflowError):
            failed = True
        if failed:
            raise SourceAuthoringPolicyError("invalid_binding") from None
        return result


def build_intent(source: POCSourceSnapshot, *, body: bytes,
                 policy: SourceBoundAuthoringPolicy | None = None, **bindings: Any) -> SourceBoundAuthoringIntent:
    """Owner supplies consent metadata; all source/program/body fields are derived."""
    policy = synthetic_policy() if policy is None else policy
    if body != build_body(source, policy=policy) or type(body) is not bytes:
        raise SourceAuthoringPolicyError("body_mismatch")
    expected_receipt = "srcpt_" + source.source_id.removeprefix("src_")
    if bindings.pop("source_receipt_id", expected_receipt) != expected_receipt:
        raise SourceAuthoringPolicyError("source_mismatch")
    failed = False
    try:
        proof = synthetic_token_proof(body, bindings["synthetic_input_tokens"])
        if bindings.pop("token_proof", proof) != proof:
            raise ValueError
        intent = SourceBoundAuthoringIntent(
            poc_id=source.poc_id, source_receipt_id=expected_receipt,
            source_id=source.source_id, source_revision=source.source_revision,
            source_kind=source.kind.value, source_adapter_name=source.adapter_name,
            source_adapter_version=source.adapter_version, source_sha256=source.content_sha256,
            redaction_policy_version=source.redaction_policy_version,
            policy=policy, policy_digest=policy.digest, body_sha256=body_digest(body), token_proof=proof, **bindings)
    except (ValueError, TypeError, AttributeError, KeyError, RecursionError, OverflowError):
        failed = True
    if failed:
        raise SourceAuthoringPolicyError("invalid_binding") from None
    return intent


def validate_intent(intent: SourceBoundAuthoringIntent, source: POCSourceSnapshot,
                    body: bytes, policy: SourceBoundAuthoringPolicy | None = None) -> None:
    policy = synthetic_policy() if policy is None else policy
    _validate_policy(policy)
    failed = False
    try:
        if type(intent) is not SourceBoundAuthoringIntent:
            raise ValueError
        checked = SourceBoundAuthoringIntent.model_validate(intent.model_dump(warnings=False))
        source_fields = {"poc_id", "source_receipt_id", "source_id", "source_revision", "source_kind",
                         "source_adapter_name", "source_adapter_version", "source_sha256",
                         "redaction_policy_version", "policy", "policy_digest", "body_sha256", "token_proof"}
        bindings = {k: v for k, v in checked.model_dump(warnings=False).items() if k not in source_fields}
        if build_intent(source, body=body, policy=policy, **bindings) != checked:
            raise ValueError
    except (ValueError, TypeError, AttributeError, KeyError, RecursionError, OverflowError):
        failed = True
    if failed:
        raise SourceAuthoringPolicyError("intent_mismatch") from None


def validate_output(raw_json: bytes, source: POCSourceSnapshot) -> SourceNeutralProposalBatch:
    """Strict finite duplicate-free JSON plus existing A3 authority/anchor rules."""
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError
            result[key] = value
        return result

    def invalid_constant(value):
        raise ValueError

    failed = False
    try:
        build_body(source)
        if type(raw_json) is not bytes or len(raw_json) > 262144:
            raise ValueError
        payload = json.loads(raw_json.decode("utf-8"), object_pairs_hook=pairs,
                             parse_constant=invalid_constant)
        canonical_bytes(payload)
        batch = SourceNeutralProposalBatch.model_validate(payload, strict=True)
        _validate_source_neutral_batch(batch, source=source)
    except (ValueError, TypeError, AttributeError, KeyError, RecursionError, OverflowError):
        failed = True
    if failed:
        raise SourceAuthoringPolicyError("invalid_output") from None
    return batch
