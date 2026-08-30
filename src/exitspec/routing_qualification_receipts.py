"""B12 purpose-bound receipts over B11 routing campaign results.

This module wraps, but never replaces, B11 admission and reduction.  Receipt
issuance requires the original canonical B11 evidence bundle and calls B11's
context-bound result validator before binding the resulting immutable facts.
The hashes in this module provide deterministic identity and consistency;
they are not signatures, attestations, or deployment authority.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import hmac
from typing import Any, Final, Literal, Mapping

from pydantic import Field, field_validator, model_validator

from .canonical import canonical_json_bytes
from .confirmations import ContractConfirmation
from .models import FrozenExitSpecModel, POCContract, SHA256_PATTERN
from .routing_campaign_verifier import (
    RoutingCampaignEvidenceBundleV1,
    RoutingCampaignReductionResultV1,
    parse_routing_campaign_evidence,
    parse_routing_campaign_confirmation,
    serialize_routing_campaign_confirmation,
    serialize_routing_campaign_evidence,
    serialize_routing_campaign_reduction_result,
    serialize_routing_campaign_run_evidence,
    validate_routing_campaign_contract,
    validate_routing_campaign_reduction_result,
)
from .routing_qualification import (
    RoutingQualificationRejected,
    RoutingQualificationValidationCode,
    _load_object,
    _revalidate_typed_model,
    _validate_model,
)


ROUTING_QUALIFICATION_RECEIPT_SCHEMA_VERSION: Final = (
    "exitspec.routing-policy-qualification-receipt.v1"
)
ROUTING_QUALIFICATION_RECEIPT_PROTOCOL_ID: Final = (
    "routing_policy_qualification_receipt_v1"
)
ROUTING_QUALIFICATION_RECEIPT_PROTOCOL_VERSION: Final = "1.0.0"
ROUTING_QUALIFICATION_RECEIPT_VERIFIER_ID: Final = (
    "routing_policy_qualification_receipt_verifier_v1"
)
ROUTING_QUALIFICATION_RECEIPT_VERIFIER_VERSION: Final = "1.0.0"
ROUTING_QUALIFICATION_RECEIPT_PURPOSE: Final = "ROUTING_POLICY_QUALIFICATION"
ROUTING_QUALIFICATION_RECEIPT_CANONICALIZATION_VERSION: Final = "rfc8785_jcs_v1"
ROUTING_QUALIFICATION_RECEIPT_HASH_VERSION: Final = "sha256_v1"
ROUTING_QUALIFICATION_EVIDENCE_SET_SCHEMA_VERSION: Final = (
    "exitspec.routing-qualification-evidence-set.v1"
)

_EVIDENCE_SET_DOMAIN = b"exitspec-routing-qualification-evidence-set-v1\x00"
_RECEIPT_ID_DOMAIN = b"exitspec-routing-policy-qualification-receipt-v1\x00"
_MAX_RECEIPT_BYTES = 128 * 1024
_MAX_RECEIPT_JSON_INTEGER = 2_147_483_647
_LIMITATIONS = (
    "NO_DEPLOYMENT_AUTHORITY",
    "NO_SHIPPING_AUTHORITY",
    "NO_PRODUCTION_TRAFFIC_AUTHORITY",
    "NO_TRAFFIC_EXPANSION_AUTHORITY",
    "NO_RELEASE_AUTHORITY",
    "NO_CONTRACT_MUTATION_AUTHORITY",
    "SEPARATE_HUMAN_PRODUCT_DECISION_REQUIRED",
)

RoutingQualificationReceiptRejected = RoutingQualificationRejected
RoutingQualificationReceiptValidationCode = RoutingQualificationValidationCode


def _reject(
    code: RoutingQualificationValidationCode,
    message: str,
    path: str | None = None,
) -> None:
    raise RoutingQualificationRejected(code, message, path=path)


def _whole_second_utc(value: str) -> str:
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as error:
        raise ValueError(
            "issued_at must be a real UTC whole-second timestamp."
        ) from error
    return value


def _normalize_issued_at(value: datetime | str) -> str:
    if isinstance(value, str):
        return _whole_second_utc(value)
    if type(value) is not datetime:
        raise TypeError("issued_at must be a timezone-aware datetime or canonical UTC string.")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("issued_at must be timezone-aware.")
    if value.microsecond != 0:
        raise ValueError("issued_at must have whole-second precision.")
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class RoutingQualificationEvidenceRunIdentityV1(FrozenExitSpecModel):
    """Content-free identity for one B11-admitted run without rewriting it."""

    run_id: str = Field(pattern=r"^[a-z][a-z0-9._-]{2,127}$", max_length=128)
    repetition_index: int = Field(ge=1, le=100)
    run_canonical_sha256: str = Field(pattern=SHA256_PATTERN)


class RoutingQualificationAuthorizationV1(FrozenExitSpecModel):
    """Frozen zero-authority posture for every B12 receipt verdict."""

    deployment_authorized: Literal[False] = False
    shipping_authorized: Literal[False] = False
    production_traffic_authorized: Literal[False] = False
    traffic_expansion_authorized: Literal[False] = False
    release_authorized: Literal[False] = False
    contract_mutation_authorized: Literal[False] = False
    human_product_decision_required: Literal[True] = True


class RoutingPolicyQualificationReceiptV1(FrozenExitSpecModel):
    """Immutable, purpose-bound receipt for one recomputed B11 result."""

    schema_version: Literal[
        "exitspec.routing-policy-qualification-receipt.v1"
    ]
    protocol_id: Literal[
        "routing_policy_qualification_receipt_v1"
    ]
    protocol_version: Literal["1.0.0"]
    verifier_id: Literal[
        "routing_policy_qualification_receipt_verifier_v1"
    ]
    verifier_version: Literal["1.0.0"]
    purpose: Literal[
        "ROUTING_POLICY_QUALIFICATION"
    ]
    canonicalization_version: Literal[
        "rfc8785_jcs_v1"
    ]
    hash_version: Literal["sha256_v1"]
    identity_assurance: Literal[
        "CONSISTENCY_ONLY_NOT_SIGNATURE_OR_ATTESTATION"
    ]
    receipt_id: str = Field(pattern=r"^rqr_[a-f0-9]{64}$")
    issued_at: str = Field(
        pattern=(
            r"^20[0-9]{2}-[0-9]{2}-[0-9]{2}T[0-9]{2}:"
            r"[0-9]{2}:[0-9]{2}Z$"
        ),
        max_length=20,
    )
    contract_id: str = Field(pattern=r"^[a-z][a-z0-9-]{2,63}$")
    contract_version: str = Field(min_length=1)
    contract_sha256: str = Field(pattern=SHA256_PATTERN)
    confirmation_id: str = Field(pattern=r"^cnf_[a-f0-9]{64}$")
    confirmation_canonical_sha256: str = Field(pattern=SHA256_PATTERN)
    confirmation_fingerprint: str = Field(pattern=SHA256_PATTERN)
    candidate_policy_id: str = Field(
        pattern=r"^[a-z][a-z0-9._-]{2,127}$", max_length=128
    )
    candidate_policy_sha256: str = Field(pattern=SHA256_PATTERN)
    baseline_policy_id: str = Field(
        pattern=r"^[a-z][a-z0-9._-]{2,127}$", max_length=128
    )
    baseline_policy_sha256: str = Field(pattern=SHA256_PATTERN)
    reducer_id: Literal["routing_campaign_deterministic_reducer_v1"]
    reducer_version: Literal["1.0.0"]
    result_schema_version: Literal[
        "exitspec.routing-campaign-reduction-result.v1"
    ]
    result_canonical_sha256: str = Field(pattern=SHA256_PATTERN)
    verdict: Literal["PASS", "FAIL", "NOT_PROVEN"]
    verdict_authority: Literal["EXIT_SPEC_ONLY"]
    required_repetition_indices: tuple[int, ...] = Field(min_length=1, max_length=100)
    missing_repetition_indices: tuple[int, ...] = Field(min_length=0, max_length=100)
    evidence_set_schema_version: Literal[
        "exitspec.routing-qualification-evidence-set.v1"
    ]
    evidence_class: Literal["SYNTHETIC_FIXTURE", "EXTERNAL_SEALED_EVIDENCE"]
    evidence_use: Literal["TEST_ONLY", "EXTERNAL_EVIDENCE"]
    evidence_runs: tuple[RoutingQualificationEvidenceRunIdentityV1, ...] = Field(
        min_length=1, max_length=100
    )
    evidence_set_sha256: str = Field(pattern=SHA256_PATTERN)
    authorization: RoutingQualificationAuthorizationV1
    limitations: tuple[str, ...] = Field(min_length=7, max_length=7)

    _validate_issued_at = field_validator("issued_at")(_whole_second_utc)

    @model_validator(mode="after")
    def require_bound_identity_and_zero_authority(
        self,
    ) -> "RoutingPolicyQualificationReceiptV1":
        required = self.required_repetition_indices
        if required != tuple(range(1, len(required) + 1)):
            raise ValueError(
                "Required repetitions must be the canonical one-based sequence."
            )
        if any(index not in required for index in self.missing_repetition_indices):
            raise ValueError("Missing repetitions must belong to the required set.")
        if any(run.repetition_index not in required for run in self.evidence_runs):
            raise ValueError("Every evidence run repetition must be required.")
        if self.missing_repetition_indices != tuple(
            index
            for index in required
            if index not in {run.repetition_index for run in self.evidence_runs}
        ):
            raise ValueError(
                "Missing repetitions must exactly preserve absent evidence runs."
            )
        repetitions = tuple(run.repetition_index for run in self.evidence_runs)
        run_ids = tuple(run.run_id for run in self.evidence_runs)
        run_digests = tuple(run.run_canonical_sha256 for run in self.evidence_runs)
        if repetitions != tuple(sorted(repetitions)):
            raise ValueError("Evidence run identities must use repetition order.")
        if any(
            len(values) != len(set(values))
            for values in (repetitions, run_ids, run_digests)
        ):
            raise ValueError("Evidence run identities and digests must be unique.")
        expected_use = (
            "TEST_ONLY"
            if self.evidence_class == "SYNTHETIC_FIXTURE"
            else "EXTERNAL_EVIDENCE"
        )
        if self.evidence_use != expected_use:
            raise ValueError("Evidence use must match the admitted B11 evidence class.")
        if (
            self.candidate_policy_id == self.baseline_policy_id
            or self.candidate_policy_sha256 == self.baseline_policy_sha256
        ):
            raise ValueError("Candidate and baseline policy identities must be distinct.")
        if self.missing_repetition_indices and self.verdict != "NOT_PROVEN":
            raise ValueError("Missing required evidence can only produce NOT_PROVEN.")
        if self.limitations != _LIMITATIONS:
            raise ValueError("Receipt limitations must preserve the frozen B12 order.")
        expected_evidence_set_sha256 = _routing_qualification_evidence_set_digest(
            evidence_class=self.evidence_class,
            contract_sha256=self.contract_sha256,
            evidence_runs=self.evidence_runs,
        )
        if not hmac.compare_digest(
            self.evidence_set_sha256, expected_evidence_set_sha256
        ):
            raise ValueError("evidence_set_sha256 does not bind the evidence identities.")
        expected_receipt_id = _routing_qualification_receipt_id(
            self.model_dump(mode="json", exclude={"receipt_id"})
        )
        if not hmac.compare_digest(self.receipt_id, expected_receipt_id):
            raise ValueError("receipt_id does not bind every persisted receipt field.")
        return self


def _routing_qualification_evidence_set_digest(
    *,
    evidence_class: str,
    contract_sha256: str,
    evidence_runs: tuple[RoutingQualificationEvidenceRunIdentityV1, ...],
) -> str:
    """Derive one ordered content-free identity for the admitted B11 run set."""

    payload = {
        "contract_sha256": contract_sha256,
        "evidence_class": evidence_class,
        "runs": [run.model_dump(mode="json") for run in evidence_runs],
        "schema_version": ROUTING_QUALIFICATION_EVIDENCE_SET_SCHEMA_VERSION,
    }
    return hashlib.sha256(
        _EVIDENCE_SET_DOMAIN + canonical_json_bytes(payload)
    ).hexdigest()


def _routing_qualification_receipt_id(payload: Mapping[str, Any]) -> str:
    """Derive the receipt ID over exactly every persisted field but itself."""

    if type(payload) is not dict:
        raise TypeError("Receipt identity payload must be one plain mapping.")
    expected_fields = set(RoutingPolicyQualificationReceiptV1.model_fields) - {
        "receipt_id"
    }
    if set(payload) != expected_fields:
        raise ValueError("Receipt identity payload must contain every bound field exactly.")
    digest = hashlib.sha256(
        _RECEIPT_ID_DOMAIN + canonical_json_bytes(payload)
    ).hexdigest()
    return f"rqr_{digest}"


def _validated_receipt(value: object) -> RoutingPolicyQualificationReceiptV1:
    if type(value) is not RoutingPolicyQualificationReceiptV1:
        _reject(
            RoutingQualificationValidationCode.WRONG_TYPE,
            "A typed B12 routing qualification receipt is required.",
            "receipt",
        )
    return _revalidate_typed_model(
        value,
        RoutingPolicyQualificationReceiptV1,
        label="routing policy qualification receipt",
    )


def _validated_bundle(
    evidence: object,
) -> RoutingCampaignEvidenceBundleV1:
    if type(evidence) is not RoutingCampaignEvidenceBundleV1:
        _reject(
            RoutingQualificationValidationCode.WRONG_TYPE,
            "B12 accepts only one typed canonical B11 evidence bundle.",
            "evidence",
        )
    # Round-trip through B11's canonical parser to revalidate copied raw state,
    # internal digests, order, uniqueness, and evidence-class consistency.
    return parse_routing_campaign_evidence(
        serialize_routing_campaign_evidence(evidence)
    )


def _evidence_run_identities(
    evidence: RoutingCampaignEvidenceBundleV1,
) -> tuple[RoutingQualificationEvidenceRunIdentityV1, ...]:
    return tuple(
        RoutingQualificationEvidenceRunIdentityV1(
            run_id=run.run_id,
            repetition_index=run.repetition_index,
            run_canonical_sha256=hashlib.sha256(
                serialize_routing_campaign_run_evidence(run)
            ).hexdigest(),
        )
        for run in evidence.runs
    )


def issue_routing_qualification_receipt(
    contract: POCContract,
    confirmation: ContractConfirmation,
    evidence: RoutingCampaignEvidenceBundleV1,
    result: RoutingCampaignReductionResultV1,
    *,
    issued_at: datetime | str,
) -> RoutingPolicyQualificationReceiptV1:
    """Issue only after B11 re-admits evidence and recomputes the supplied result."""

    bundle = _validated_bundle(evidence)
    validated_contract = validate_routing_campaign_contract(contract)
    validated_confirmation = parse_routing_campaign_confirmation(
        serialize_routing_campaign_confirmation(confirmation)
    )
    validated_result = validate_routing_campaign_reduction_result(
        validated_contract, validated_confirmation, bundle, result
    )
    result_bytes = serialize_routing_campaign_reduction_result(
        validated_contract, validated_confirmation, bundle, validated_result
    )
    confirmation_bytes = serialize_routing_campaign_confirmation(validated_confirmation)
    campaign = validated_contract.criteria[0]
    evidence_runs = _evidence_run_identities(bundle)
    evidence_set_sha256 = _routing_qualification_evidence_set_digest(
        evidence_class=bundle.evidence_class,
        contract_sha256=validated_result.contract_sha256,
        evidence_runs=evidence_runs,
    )
    payload: dict[str, Any] = {
        "schema_version": ROUTING_QUALIFICATION_RECEIPT_SCHEMA_VERSION,
        "protocol_id": ROUTING_QUALIFICATION_RECEIPT_PROTOCOL_ID,
        "protocol_version": ROUTING_QUALIFICATION_RECEIPT_PROTOCOL_VERSION,
        "verifier_id": ROUTING_QUALIFICATION_RECEIPT_VERIFIER_ID,
        "verifier_version": ROUTING_QUALIFICATION_RECEIPT_VERIFIER_VERSION,
        "purpose": ROUTING_QUALIFICATION_RECEIPT_PURPOSE,
        "canonicalization_version": (
            ROUTING_QUALIFICATION_RECEIPT_CANONICALIZATION_VERSION
        ),
        "hash_version": ROUTING_QUALIFICATION_RECEIPT_HASH_VERSION,
        "identity_assurance": "CONSISTENCY_ONLY_NOT_SIGNATURE_OR_ATTESTATION",
        "issued_at": _normalize_issued_at(issued_at),
        "contract_id": validated_contract.id,
        "contract_version": validated_contract.version,
        "contract_sha256": validated_result.contract_sha256,
        "confirmation_id": validated_confirmation.confirmation_id,
        "confirmation_canonical_sha256": hashlib.sha256(
            confirmation_bytes
        ).hexdigest(),
        "confirmation_fingerprint": validated_confirmation.contract_fingerprint,
        "candidate_policy_id": campaign.candidate_policy.policy_id,
        "candidate_policy_sha256": campaign.candidate_policy.policy_sha256,
        "baseline_policy_id": campaign.baseline_policy.policy_id,
        "baseline_policy_sha256": campaign.baseline_policy.policy_sha256,
        "reducer_id": validated_result.reducer_id,
        "reducer_version": validated_result.reducer_version,
        "result_schema_version": validated_result.schema_version,
        "result_canonical_sha256": hashlib.sha256(result_bytes).hexdigest(),
        "verdict": validated_result.campaign_verdict,
        "verdict_authority": "EXIT_SPEC_ONLY",
        "required_repetition_indices": list(
            validated_result.required_repetition_indices
        ),
        "missing_repetition_indices": list(
            validated_result.missing_repetition_indices
        ),
        "evidence_set_schema_version": (
            ROUTING_QUALIFICATION_EVIDENCE_SET_SCHEMA_VERSION
        ),
        "evidence_class": bundle.evidence_class,
        "evidence_use": (
            "TEST_ONLY"
            if bundle.evidence_class == "SYNTHETIC_FIXTURE"
            else "EXTERNAL_EVIDENCE"
        ),
        "evidence_runs": [run.model_dump(mode="json") for run in evidence_runs],
        "evidence_set_sha256": evidence_set_sha256,
        "authorization": RoutingQualificationAuthorizationV1().model_dump(mode="json"),
        "limitations": list(_LIMITATIONS),
    }
    receipt_id = _routing_qualification_receipt_id(payload)
    return RoutingPolicyQualificationReceiptV1.model_validate(
        {"receipt_id": receipt_id, **payload}
    )


def parse_routing_qualification_receipt(
    value: bytes | Mapping[str, Any],
) -> RoutingPolicyQualificationReceiptV1:
    """Strictly parse one bounded canonical B12 receipt and verify its identities."""

    payload = _load_object(
        value,
        label="routing policy qualification receipt",
        max_json_integer=_MAX_RECEIPT_JSON_INTEGER,
        max_bytes=_MAX_RECEIPT_BYTES,
    )
    return _validate_model(
        RoutingPolicyQualificationReceiptV1,
        payload,
        label="routing policy qualification receipt",
        reject_producer_verdict_aliases=False,
    )


def validate_routing_qualification_receipt(
    contract: POCContract,
    confirmation: ContractConfirmation,
    evidence: RoutingCampaignEvidenceBundleV1,
    result: RoutingCampaignReductionResultV1,
    receipt: RoutingPolicyQualificationReceiptV1,
) -> RoutingPolicyQualificationReceiptV1:
    """Recompute B11 and require the receipt to match that exact context."""

    validated = _validated_receipt(receipt)
    expected = issue_routing_qualification_receipt(
        contract,
        confirmation,
        evidence,
        result,
        issued_at=validated.issued_at,
    )
    if validated != expected:
        _reject(
            RoutingQualificationValidationCode.CONTRACT_BINDING_MISMATCH,
            "Routing qualification receipt does not match its recomputed B11 context.",
            "receipt",
        )
    return validated


def serialize_routing_qualification_receipt(
    contract: POCContract,
    confirmation: ContractConfirmation,
    evidence: RoutingCampaignEvidenceBundleV1,
    result: RoutingCampaignReductionResultV1,
    receipt: RoutingPolicyQualificationReceiptV1,
) -> bytes:
    """Serialize only after complete receipt and B11-context revalidation."""

    validated = validate_routing_qualification_receipt(
        contract, confirmation, evidence, result, receipt
    )
    content = canonical_json_bytes(validated.model_dump(mode="json"))
    parsed = parse_routing_qualification_receipt(content)
    if parsed != validated:
        _reject(
            RoutingQualificationValidationCode.SEMANTIC_INCONSISTENCY,
            "Routing qualification receipt changed during serialization.",
        )
    return content


__all__ = [
    "ROUTING_QUALIFICATION_EVIDENCE_SET_SCHEMA_VERSION",
    "ROUTING_QUALIFICATION_RECEIPT_CANONICALIZATION_VERSION",
    "ROUTING_QUALIFICATION_RECEIPT_HASH_VERSION",
    "ROUTING_QUALIFICATION_RECEIPT_PROTOCOL_ID",
    "ROUTING_QUALIFICATION_RECEIPT_PROTOCOL_VERSION",
    "ROUTING_QUALIFICATION_RECEIPT_PURPOSE",
    "ROUTING_QUALIFICATION_RECEIPT_SCHEMA_VERSION",
    "ROUTING_QUALIFICATION_RECEIPT_VERIFIER_ID",
    "ROUTING_QUALIFICATION_RECEIPT_VERIFIER_VERSION",
    "RoutingPolicyQualificationReceiptV1",
    "RoutingQualificationAuthorizationV1",
    "RoutingQualificationEvidenceRunIdentityV1",
    "RoutingQualificationReceiptRejected",
    "RoutingQualificationReceiptValidationCode",
    "issue_routing_qualification_receipt",
    "parse_routing_qualification_receipt",
    "serialize_routing_qualification_receipt",
    "validate_routing_qualification_receipt",
]
