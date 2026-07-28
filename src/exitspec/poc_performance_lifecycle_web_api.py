"""Pure HTTP projection for the local performance-agreement lifecycle."""

from __future__ import annotations

from dataclasses import dataclass
from http import HTTPStatus
import re
from typing import Any, Mapping
from urllib.parse import urlparse

from pydantic import ValidationError

from .confirmations import ContractConfirmation
from .models import ContractStatus, POCContract
from .poc_contract_definition import (
    ProcessLocalContractDefinitionService,
)
from .poc_creation import POC_ID_PATTERN
from .poc_performance_contract import PerformanceTargetInput
from .poc_performance_lifecycle import (
    AgreementPreparation,
    PerformanceLifecycleCapacityExceeded,
    PerformanceLifecycleConflict,
    PerformanceLifecycleError,
    PerformanceLifecycleInvalid,
    PerformanceLifecycleNotFound,
    ProcessLocalPerformanceLifecycleService,
)
from .poc_proposal_review import (
    ProcessLocalProposalReviewService,
    ProposalReviewState,
)


_POC_ID_RE = re.compile(POC_ID_PATTERN)
_PREPARE_FIELDS = {
    "endpoint",
    "endpoint_class",
    "idempotency_key",
    "model",
    "rationale",
    "reviewer",
    "target_provider",
}
_CONFIRM_FIELDS = {
    "agreement_acknowledged",
    "confirmer",
    "idempotency_key",
    "rationale",
}
_FREEZE_FIELDS = {"idempotency_key"}


@dataclass(frozen=True, slots=True)
class PerformanceLifecycleWebAPIResponse:
    status: HTTPStatus
    payload: dict[str, Any]


class PerformanceLifecycleWebAPIRequestError(ValueError):
    pass


def is_performance_lifecycle_web_api_target(target: str) -> bool:
    if type(target) is not str:
        return False
    return bool(
        re.match(
            r"^/api/+pocs/[^/]+/agreement(?:/|$)",
            urlparse(target).path,
        )
    )


def handle_performance_lifecycle_web_api_request(
    *,
    method: str,
    target: str,
    payload: Mapping[str, Any] | None,
    lifecycle: ProcessLocalPerformanceLifecycleService,
    proposals: ProcessLocalProposalReviewService,
    definitions: ProcessLocalContractDefinitionService,
) -> PerformanceLifecycleWebAPIResponse | None:
    if type(lifecycle) is not ProcessLocalPerformanceLifecycleService:
        raise TypeError("lifecycle runtime is invalid.")
    if type(proposals) is not ProcessLocalProposalReviewService:
        raise TypeError("proposal runtime is invalid.")
    if type(definitions) is not ProcessLocalContractDefinitionService:
        raise TypeError("definition runtime is invalid.")
    if not is_performance_lifecycle_web_api_target(target):
        return None
    try:
        path = _exact_path(target)
        poc_id, action = _parse_path(path)
        if method == "GET":
            if payload is not None or action is not None:
                raise PerformanceLifecycleWebAPIRequestError
            return _ok(
                _snapshot_payload(
                    poc_id,
                    lifecycle=lifecycle,
                    proposals=proposals,
                    definitions=definitions,
                )
            )
        if method == "POST":
            body = _object(payload)
            if action is None:
                _only(body, _PREPARE_FIELDS)
                result = lifecycle.prepare(
                    poc_id,
                    target=PerformanceTargetInput(
                        provider=body["target_provider"],
                        endpoint_class=body["endpoint_class"],
                        endpoint=body["endpoint"],
                        model=body["model"],
                    ),
                    reviewer=body["reviewer"],
                    rationale=body["rationale"],
                    idempotency_key=body["idempotency_key"],
                )
                return PerformanceLifecycleWebAPIResponse(
                    HTTPStatus.OK if result.replayed else HTTPStatus.CREATED,
                    {
                        "poc_id": poc_id,
                        "disposition": (
                            "IDEMPOTENT_REPLAY" if result.replayed else "CREATED"
                        ),
                        "draft": _preparation_payload(result.value),
                    },
                )
            if action == "confirm":
                _only(body, _CONFIRM_FIELDS)
                result = lifecycle.confirm(
                    poc_id,
                    confirmer_identity=body["confirmer"],
                    agreement_acknowledged=body["agreement_acknowledged"],
                    rationale=body["rationale"],
                    idempotency_key=body["idempotency_key"],
                )
                confirmation = result.value
                preparation = lifecycle.snapshot(
                    poc_id,
                    allow_empty=False,
                ).preparation
                if preparation is None:
                    raise PerformanceLifecycleError
                return PerformanceLifecycleWebAPIResponse(
                    HTTPStatus.OK if result.replayed else HTTPStatus.CREATED,
                    {
                        "poc_id": poc_id,
                        "disposition": (
                            "IDEMPOTENT_REPLAY" if result.replayed else "CREATED"
                        ),
                        "confirmation": _confirmation_payload(
                            confirmation,
                            preparation,
                        ),
                    },
                )
            if action == "freeze":
                _only(body, _FREEZE_FIELDS)
                result = lifecycle.freeze(
                    poc_id,
                    idempotency_key=body["idempotency_key"],
                )
                preparation = lifecycle.snapshot(
                    poc_id,
                    allow_empty=False,
                ).preparation
                if preparation is None:
                    raise PerformanceLifecycleError
                return PerformanceLifecycleWebAPIResponse(
                    HTTPStatus.OK if result.replayed else HTTPStatus.CREATED,
                    {
                        "poc_id": poc_id,
                        "disposition": (
                            "IDEMPOTENT_REPLAY" if result.replayed else "CREATED"
                        ),
                        "frozen_contract": _frozen_payload(
                            result.value,
                            preparation,
                        ),
                    },
                )
        return _error(
            HTTPStatus.METHOD_NOT_ALLOWED,
            "Performance agreement method is not allowed.",
        )
    except (
        PerformanceLifecycleWebAPIRequestError,
        PerformanceLifecycleInvalid,
        ValidationError,
        TypeError,
        ValueError,
    ):
        return _error(
            HTTPStatus.BAD_REQUEST,
            "Performance agreement request is invalid.",
        )
    except PerformanceLifecycleNotFound:
        return _error(
            HTTPStatus.NOT_FOUND,
            "Performance agreement was not found.",
        )
    except PerformanceLifecycleConflict:
        return _error(
            HTTPStatus.CONFLICT,
            "Performance agreement conflicts with current POC state.",
        )
    except PerformanceLifecycleCapacityExceeded:
        return _error(
            HTTPStatus.SERVICE_UNAVAILABLE,
            "Performance agreement capacity is exhausted.",
        )
    except PerformanceLifecycleError:
        return _error(
            HTTPStatus.SERVICE_UNAVAILABLE,
            "Performance agreement is unavailable.",
        )


def _exact_path(target: str) -> str:
    parsed = urlparse(target)
    if (
        parsed.scheme
        or parsed.netloc
        or parsed.params
        or parsed.query
        or parsed.fragment
        or parsed.path != target
    ):
        raise PerformanceLifecycleWebAPIRequestError
    return parsed.path


def _parse_path(path: str) -> tuple[str, str | None]:
    parts = path.strip("/").split("/")
    if (
        len(parts) not in {4, 5}
        or parts[:2] != ["api", "pocs"]
        or parts[3] != "agreement"
        or _POC_ID_RE.fullmatch(parts[2]) is None
        or path.startswith("//")
        or "//" in path
    ):
        raise PerformanceLifecycleWebAPIRequestError
    action = None if len(parts) == 4 else parts[4]
    if action not in {None, "confirm", "freeze"}:
        raise PerformanceLifecycleWebAPIRequestError
    return parts[2], action


def _object(payload: Mapping[str, Any] | None) -> Mapping[str, Any]:
    if type(payload) is not dict:
        raise PerformanceLifecycleWebAPIRequestError
    return payload


def _only(payload: Mapping[str, Any], fields: set[str]) -> None:
    if set(payload) != fields or any(type(key) is not str for key in payload):
        raise PerformanceLifecycleWebAPIRequestError


def _snapshot_payload(
    poc_id: str,
    *,
    lifecycle: ProcessLocalPerformanceLifecycleService,
    proposals: ProcessLocalProposalReviewService,
    definitions: ProcessLocalContractDefinitionService,
) -> dict[str, Any]:
    current_proposals = {
        proposal.proposal_id: proposal
        for proposal in proposals.list_proposals(poc_id)
        if proposal.review_state is ProposalReviewState.KEEP_FOR_CONTRACT
    }
    definition_payloads = []
    for definition in definitions.definitions():
        if definition.poc_id != poc_id:
            continue
        proposal = current_proposals.get(definition.proposal_id)
        if proposal is None:
            raise PerformanceLifecycleConflict
        definition_payloads.append(
            {
                "proposal_id": definition.proposal_id,
                "definition_id": definition.definition_id,
                "definition_sha256": definition.definition_sha256,
                "metric": definition.metric.value,
                "unit": definition.unit.value,
                "operator": definition.operator.value,
                "threshold": definition.threshold,
                "minimum_samples": definition.minimum_samples,
                "concurrency": definition.concurrency,
                "prompt_tokens_min": definition.prompt_tokens_min,
                "prompt_tokens_max": definition.prompt_tokens_max,
                "output_tokens_min": definition.output_tokens_min,
                "output_tokens_max": definition.output_tokens_max,
                "source_kind": definition.source_kind.value,
                "source_quote": proposal.source_quote,
                "normalized_claim": definition.normalized_claim,
                "defined_at": definition.defined_at.isoformat(),
            }
        )
    metric_order = {"TTFT_P95_MS": 0, "ERROR_RATE_PERCENT": 1}
    definition_payloads.sort(
        key=lambda item: (
            metric_order.get(str(item["metric"]), 99),
            str(item["definition_id"]),
        )
    )
    snapshot = lifecycle.snapshot(poc_id)
    preparation = snapshot.preparation
    return {
        "poc_id": poc_id,
        "definitions": definition_payloads,
        "draft": (None if preparation is None else _preparation_payload(preparation)),
        "confirmation": (
            None
            if snapshot.confirmation is None or preparation is None
            else _confirmation_payload(snapshot.confirmation, preparation)
        ),
        "frozen_contract": (
            None
            if snapshot.frozen_contract is None or preparation is None
            else _frozen_payload(snapshot.frozen_contract, preparation)
        ),
    }


def _preparation_payload(preparation: object) -> dict[str, Any]:
    if type(preparation) is not AgreementPreparation:
        raise PerformanceLifecycleError
    return {
        "draft_id": preparation.draft_id,
        "draft_sha256": preparation.draft_sha256,
        "created_at": preparation.prepared_at.isoformat(),
        "target_provider": preparation.target.provider,
        "endpoint_class": preparation.target.endpoint_class,
        "endpoint": preparation.target.endpoint,
        "model": preparation.target.model,
        "reviewer": preparation.reviewer,
        "rationale": preparation.rationale,
    }


def _confirmation_payload(
    confirmation: object,
    preparation: AgreementPreparation,
) -> dict[str, Any]:
    if type(confirmation) is not ContractConfirmation:
        raise PerformanceLifecycleError
    return {
        "confirmation_id": confirmation.confirmation_id,
        "draft_sha256": preparation.draft_sha256,
        "confirmer": confirmation.confirmer_identity,
        "agreement_acknowledged": confirmation.agreement_acknowledged,
        "confirmed_at": confirmation.decided_at.isoformat(),
        "rationale": confirmation.rationale,
    }


def _frozen_payload(
    contract: object,
    preparation: AgreementPreparation,
) -> dict[str, Any]:
    if type(contract) is not POCContract:
        raise PerformanceLifecycleError
    if (
        contract.status is not ContractStatus.FROZEN
        or contract.canonical_hash is None
        or contract.confirmation_id is None
        or contract.frozen_at is None
    ):
        raise PerformanceLifecycleError
    return {
        "contract_id": contract.id,
        "canonical_hash": contract.canonical_hash,
        "confirmation_id": contract.confirmation_id,
        "frozen_at": contract.frozen_at.isoformat(),
        "target_provider": preparation.target.provider,
        "endpoint_class": preparation.target.endpoint_class,
        "endpoint": preparation.target.endpoint,
        "model": preparation.target.model,
    }


def _ok(payload: dict[str, Any]) -> PerformanceLifecycleWebAPIResponse:
    return PerformanceLifecycleWebAPIResponse(HTTPStatus.OK, payload)


def _error(
    status: HTTPStatus,
    message: str,
) -> PerformanceLifecycleWebAPIResponse:
    return PerformanceLifecycleWebAPIResponse(status, {"error": message})


__all__ = [
    "PerformanceLifecycleWebAPIRequestError",
    "PerformanceLifecycleWebAPIResponse",
    "handle_performance_lifecycle_web_api_request",
    "is_performance_lifecycle_web_api_target",
]
