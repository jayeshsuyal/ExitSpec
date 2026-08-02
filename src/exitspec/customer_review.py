"""Customer-safe projections for one exact contract review capability.

This module is deliberately presentation-only.  It cannot prepare, confirm,
freeze, execute, score, or issue a verdict for a POC.
"""

from __future__ import annotations

from typing import Any

from .confirmations import (
    ConfirmationDecision,
    ContractConfirmation,
    canonical_confirmation_payload,
    contract_confirmation_fingerprint,
)
from .models import POCContract
from .review_links import CustomerReviewInvitation, ReviewInvitationError


def customer_decision_payload(
    confirmation: ContractConfirmation | None,
    *,
    idempotent_replay: bool,
) -> dict[str, Any] | None:
    """Project one immutable decision without granting lifecycle authority."""

    if confirmation is None:
        return None
    return {
        "decision": confirmation.decision.value,
        "reviewer_display_name": confirmation.confirmer_identity,
        "recorded_at": confirmation.decided_at.isoformat(),
        "rationale": confirmation.rationale,
        "agreement_acknowledged": confirmation.agreement_acknowledged,
        "idempotent_replay": idempotent_replay,
        "synthetic": False,
    }


def customer_confirmation_payload(
    confirmation: ContractConfirmation | None,
) -> dict[str, Any] | None:
    """Project a confirmation without exposing its idempotency key."""

    if confirmation is None:
        return None
    return {
        "confirmation_id": confirmation.confirmation_id,
        "contract_id": confirmation.contract_id,
        "contract_version": confirmation.contract_version,
        "contract_fingerprint": confirmation.contract_fingerprint,
        "confirmer": confirmation.confirmer_identity,
        "decision": confirmation.decision.value,
        "agreement_acknowledged": confirmation.agreement_acknowledged,
        "confirmed_at": confirmation.decided_at.isoformat(),
        "rationale": confirmation.rationale,
    }


def build_customer_review_payload(
    *,
    invitation: CustomerReviewInvitation,
    contract: POCContract,
    confirmation: ContractConfirmation | None,
    poc_id: str,
    return_url: str,
    execution_endpoint: str,
) -> dict[str, Any]:
    """Return the bounded customer-facing projection for a valid invitation."""

    agreement = canonical_confirmation_payload(contract)
    fingerprint = contract_confirmation_fingerprint(contract)
    if (
        invitation.contract_id != agreement["id"]
        or invitation.contract_version != agreement["version"]
        or invitation.confirmation_fingerprint != fingerprint
    ):
        raise ReviewInvitationError(
            "Customer review link no longer matches the current contract."
        )
    if confirmation is not None and (
        confirmation.contract_id != agreement["id"]
        or confirmation.contract_version != agreement["version"]
        or confirmation.contract_fingerprint != fingerprint
    ):
        raise ReviewInvitationError(
            "Customer decision no longer matches the current contract."
        )
    if confirmation is None:
        status = "PENDING"
    elif confirmation.decision is ConfirmationDecision.CONFIRM:
        status = "CONFIRMED"
    else:
        status = "CHANGES_REQUESTED"

    customer_criteria = [
        _customer_criterion_payload(criterion)
        for criterion in agreement["criteria"]
    ]
    decision = customer_decision_payload(
        confirmation,
        idempotent_replay=False,
    )
    identity_notice = (
        "This local capability link records a customer decision but does not "
        "authenticate a real customer. A hosted deployment must bind verified "
        "identity and permission to this exact contract version."
    )
    display_target = {
        **agreement["target_system"],
        "endpoint": execution_endpoint,
    }
    return {
        "mode": "local_capability_review",
        "safety": {
            "synthetic_only": True,
            "not_evidence": True,
            "not_production_authorization": True,
            "identity_note": identity_notice,
        },
        "review": {
            "review_id": invitation.invitation_id,
            "status": status,
            "contract_id": agreement["id"],
            "contract_version": agreement["version"],
            "confirmation_fingerprint": fingerprint,
            "customer": agreement["customer"],
            "use_case": agreement["use_case"],
            "poc": {
                "title": agreement["use_case"],
                "customer_name": agreement["customer"],
            },
            "agreement": agreement,
            "contract": {
                "id": agreement["id"],
                "version": agreement["version"],
                "confirmation_fingerprint": fingerprint,
                "excluded": agreement["non_goals"],
                "criteria": customer_criteria,
                "target_system": display_target,
                "workload": agreement["workload"],
                "owners": agreement["owners"],
                "evidence_retention_policy": agreement[
                    "evidence_retention_policy"
                ],
            },
            "target_system": display_target,
            "workload": agreement["workload"],
            "criteria": customer_criteria,
            "owners": agreement["owners"],
            "non_goals": agreement["non_goals"],
            "evidence_retention_policy": agreement[
                "evidence_retention_policy"
            ],
            "expires_at": invitation.expires_at.isoformat(),
            "acknowledgement_required": True,
            "identity": {
                "display_name": "Customer approver · capability review",
                "notice": identity_notice,
            },
            "local_demo": {
                "return_url": return_url,
                "notice": (
                    "Local loopback demo only. A hosted customer review would "
                    "not expose an internal workspace shortcut."
                ),
            },
            "decision": decision,
            "poc_id": poc_id,
        },
        "confirmation": customer_confirmation_payload(confirmation),
    }


def _customer_criterion_payload(criterion: dict[str, Any]) -> dict[str, Any]:
    if criterion.get("criterion_type") == "inference_performance_v1":
        return _performance_criterion_payload(criterion)
    return _proportion_criterion_payload(criterion)


def _performance_criterion_payload(
    criterion: dict[str, Any],
) -> dict[str, Any]:
    ttft = criterion["ttft_p95"]
    error_rate = criterion["error_rate"]
    source = criterion.get("source")
    ttft_operator = "below" if ttft["operator"] == "lt" else "at most"
    error_percent = float(error_rate["threshold"]) * 100
    return {
        "id": criterion["id"],
        "title": criterion["title"],
        "normalized_claim": criterion["normalized_claim"],
        "plain_language": criterion["normalized_claim"],
        "source": source,
        "source_quote": (
            "Human-added requirement" if source is None else source["quote"]
        ),
        "metric": "P95 time to first token and error rate",
        "unit": "milliseconds and percent",
        "aggregation": "p95 and attempted-request rate",
        "threshold": (
            "P95 TTFT {0} {1:g} ms · error rate below {2:g}%".format(
                ttft_operator,
                float(ttft["threshold"]),
                error_percent,
            )
        ),
        "sample": (
            "{0} successful timing samples · {1} attempted requests".format(
                ttft["minimum_successful_samples"],
                error_rate["minimum_attempts"],
            )
        ),
        "workload": _humanize(criterion["workload_slice"]),
        "workload_slice": criterion["workload_slice"],
        "adapter": criterion["adapter"],
        "adapter_version": criterion["adapter_version"],
        "owner": criterion["owner"],
        "evidence_policy": criterion["evidence_policy"],
        "must_have": criterion["must_have"],
        "required": criterion["must_have"],
        "agreement": criterion,
        "excluded": [],
    }


def _proportion_criterion_payload(
    criterion: dict[str, Any],
) -> dict[str, Any]:
    source = criterion.get("source")
    metric_name = criterion["metric"]
    rule = criterion["rule"]
    operator = {
        "gte": "at least",
        "gt": "more than",
        "lte": "at most",
        "lt": "less than",
        "eq": "exactly",
    }.get(rule["operator"], rule["operator"])
    return {
        "id": criterion["id"],
        "title": criterion["title"],
        "normalized_claim": criterion["normalized_claim"],
        "plain_language": criterion["normalized_claim"],
        "source": source,
        "source_quote": (
            "Human-added requirement" if source is None else source["quote"]
        ),
        "metric": {
            "exact_tool_selection_rate": "Exact tool-selection rate",
        }.get(metric_name, _humanize(metric_name)),
        "unit": criterion["unit"],
        "aggregation": criterion["aggregation"],
        "rule": rule,
        "threshold": "{0} {1:.2f}%".format(
            operator,
            float(rule["threshold"]) * 100,
        ),
        "sample": "{0} or more fixed cases".format(rule["minimum_samples"]),
        "workload": _humanize(criterion["workload_slice"]),
        "workload_slice": criterion["workload_slice"],
        "adapter": criterion["adapter"],
        "adapter_version": criterion["adapter_version"],
        "owner": criterion["owner"],
        "evidence_policy": criterion["evidence_policy"],
        "must_have": criterion["must_have"],
        "required": criterion["must_have"],
        "agreement": criterion,
        "excluded": [],
    }


def _humanize(value: str) -> str:
    return value.replace("-", " ").replace("_", " ").capitalize()


__all__ = [
    "build_customer_review_payload",
    "customer_confirmation_payload",
    "customer_decision_payload",
]
