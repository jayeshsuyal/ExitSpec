"""B10 confidence-bearing routing SLO attainment contract boundary.

This module is an additive companion to B9.  It freezes per-assignment SLO
semantics and confidence sufficiency inside the existing full ``POCContract``
digest.  It deliberately has no evidence parser, run reducer, measurement
calculator, receipt issuer, or verdict authority.
"""

from __future__ import annotations

from typing import Any, Mapping

from .contracts import contract_digest, verify_contract_digest
from .models import (
    POCContract,
    RoutingQualificationCriterionV1,
    RoutingSLOAttainmentCriterionV1,
)
from .routing_qualification import (
    RoutingQualificationRejected,
    RoutingQualificationValidationCode,
    _load_object,
    _reject,
    _reject_producer_verdict_aliases,
    _revalidate_typed_model,
    _validate_model,
)


ROUTING_SLO_ATTAINMENT_PROTOCOL_ID = "routing_slo_attainment_v1"
ROUTING_SLO_ATTAINMENT_SCHEMA_VERSION = "exitspec.routing-slo-attainment.v1"
ROUTING_SLO_ATTAINMENT_CANONICALIZATION_VERSION = "rfc8785_jcs_v1"
ROUTING_SLO_ATTAINMENT_HASH_VERSION = "sha256_v1"
_ROUTING_SLO_MAX_JSON_INTEGER = 60_000_000_001

# B10 uses the same hardened error vocabulary and boundary as B9.  Keeping
# these aliases public makes it clear that parsing failures are protocol
# failures, not provider- or verdict-specific outcomes.
RoutingSLOAttainmentRejected = RoutingQualificationRejected
RoutingSLOAttainmentValidationCode = RoutingQualificationValidationCode


def routing_slo_attainment_contract_digest(value: POCContract) -> str:
    """Return the existing full frozen POC contract digest.

    There is no B10 criterion-only digest.  The outer contract hash remains
    the sole acceptance binding authority.
    """

    validated = _validated_frozen_slo_contract(value)
    return contract_digest(validated)


def parse_routing_slo_attainment_contract(
    value: bytes | Mapping[str, Any],
) -> POCContract:
    """Strictly parse a frozen full contract containing B9 and B10."""

    payload = _load_object(
        value,
        label="routing SLO attainment contract",
        max_json_integer=_ROUTING_SLO_MAX_JSON_INTEGER,
    )
    _reject_producer_verdict_aliases(payload)
    criteria = payload.get("criteria")
    if type(criteria) is list:
        for index, criterion_payload in enumerate(criteria):
            if type(criterion_payload) is not dict:
                continue
            criterion_type = criterion_payload.get("criterion_type")
            if criterion_type == "routing_qualification_v1":
                _validate_model(
                    RoutingQualificationCriterionV1,
                    criterion_payload,
                    label=f"routing SLO attainment contract.criteria[{index}]",
                    path_prefix=f"criteria[{index}]",
                )
            elif criterion_type == ROUTING_SLO_ATTAINMENT_PROTOCOL_ID:
                _validate_model(
                    RoutingSLOAttainmentCriterionV1,
                    criterion_payload,
                    label=f"routing SLO attainment contract.criteria[{index}]",
                    path_prefix=f"criteria[{index}]",
                )
    parsed = _validate_model(
        POCContract, payload, label="routing SLO attainment contract"
    )
    return _validated_frozen_slo_contract(parsed)


def validate_routing_slo_attainment_contract(value: POCContract) -> POCContract:
    """Revalidate and bind one typed full B9+B10 frozen contract."""

    return _validated_frozen_slo_contract(value)


def serialize_routing_slo_attainment_contract(value: POCContract) -> bytes:
    """Return canonical bytes after complete strict B10 contract validation."""

    from .canonical import canonical_json_bytes

    validated = _validated_frozen_slo_contract(value)
    content = canonical_json_bytes(validated.model_dump(mode="json"))
    parsed = parse_routing_slo_attainment_contract(content)
    if parsed != validated:
        _reject(
            RoutingQualificationValidationCode.SEMANTIC_INCONSISTENCY,
            "Routing SLO attainment contract changed during serialization.",
        )
    return content


def _validated_frozen_slo_contract(contract: object) -> POCContract:
    if type(contract) is not POCContract:
        _reject(
            RoutingQualificationValidationCode.WRONG_TYPE,
            "A frozen full POCContract is required; a B10 criterion is not a contract.",
        )
    validated = _revalidate_typed_model(
        contract, POCContract, label="routing SLO attainment contract"
    )
    if validated.status.value != "FROZEN":
        _reject(
            RoutingQualificationValidationCode.CONTRACT_BINDING_MISMATCH,
            "Routing SLO attainment requires a FROZEN POCContract.",
            "status",
        )
    if not verify_contract_digest(validated):
        _reject(
            RoutingQualificationValidationCode.CONTRACT_BINDING_MISMATCH,
            "Routing SLO attainment requires a digest-valid frozen contract.",
        )

    b9_criteria = tuple(
        criterion
        for criterion in validated.criteria
        if type(criterion) is RoutingQualificationCriterionV1
    )
    slo_criteria = tuple(
        criterion
        for criterion in validated.criteria
        if type(criterion) is RoutingSLOAttainmentCriterionV1
    )
    if (
        len(validated.criteria) != 2
        or len(b9_criteria) != 1
        or len(slo_criteria) != 1
        or type(validated.criteria[0]) is not RoutingQualificationCriterionV1
        or type(validated.criteria[1]) is not RoutingSLOAttainmentCriterionV1
    ):
        _reject(
            RoutingQualificationValidationCode.CONTRACT_BINDING_MISMATCH,
            "B10 requires exactly one B9 campaign criterion followed by one B10 SLO criterion.",
            "criteria",
        )

    campaign = b9_criteria[0]
    slo = slo_criteria[0]
    expected = (
        ("campaign_criterion_id", slo.campaign_criterion_id, campaign.id),
        ("campaign_protocol_id", slo.campaign_protocol_id, campaign.protocol_id),
        (
            "campaign_schema_version",
            slo.campaign_schema_version,
            campaign.schema_version,
        ),
        (
            "candidate_policy_id",
            slo.candidate_policy_id,
            campaign.candidate_policy.policy_id,
        ),
        (
            "candidate_policy_sha256",
            slo.candidate_policy_sha256,
            campaign.candidate_policy.policy_sha256,
        ),
        (
            "baseline_policy_id",
            slo.baseline_policy_id,
            campaign.baseline_policy.policy_id,
        ),
        (
            "baseline_policy_sha256",
            slo.baseline_policy_sha256,
            campaign.baseline_policy.policy_sha256,
        ),
    )
    for field, actual, expected_value in expected:
        if actual != expected_value:
            _reject(
                RoutingQualificationValidationCode.CONTRACT_BINDING_MISMATCH,
                "B10 SLO binding does not match the B9 campaign in the same full contract.",
                field,
            )
    per_subject_assignments = (
        campaign.trial_order.trial_count * campaign.trial_order.request_count
    )
    for index, rule in enumerate(slo.policy_confidence_rules):
        if rule.confidence.minimum_sample_count > per_subject_assignments:
            _reject(
                RoutingQualificationValidationCode.INVALID_BOUND,
                "B10 minimum_sample_count exceeds this policy subject's frozen B9 assignment population.",
                f"criteria[1].policy_confidence_rules[{index}].confidence.minimum_sample_count",
            )
    return validated


parse_routing_slo_contract = parse_routing_slo_attainment_contract
serialize_routing_slo_contract = serialize_routing_slo_attainment_contract
routing_slo_contract_digest = routing_slo_attainment_contract_digest


__all__ = [
    "ROUTING_SLO_ATTAINMENT_CANONICALIZATION_VERSION",
    "ROUTING_SLO_ATTAINMENT_HASH_VERSION",
    "ROUTING_SLO_ATTAINMENT_PROTOCOL_ID",
    "ROUTING_SLO_ATTAINMENT_SCHEMA_VERSION",
    "RoutingSLOAttainmentRejected",
    "RoutingSLOAttainmentValidationCode",
    "parse_routing_slo_attainment_contract",
    "parse_routing_slo_contract",
    "routing_slo_attainment_contract_digest",
    "routing_slo_contract_digest",
    "serialize_routing_slo_attainment_contract",
    "serialize_routing_slo_contract",
    "validate_routing_slo_attainment_contract",
]
