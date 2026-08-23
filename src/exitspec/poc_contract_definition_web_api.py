"""Pure browser API projection for bounded POC contract definitions.

This module owns exact route matching, exact request bodies, safe public
projections, and content-free error mapping. Transport concerns such as body
limits, JSON decoding, media types, and same-origin enforcement remain outside
this pure boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from http import HTTPStatus
import re
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import urlparse

from .poc_contract_definition import (
    ContractDefinitionCapacityExceeded,
    ContractDefinitionConflict,
    ContractDefinitionDisposition,
    ContractDefinitionError,
    ContractDefinitionIdempotencyConflict,
    ContractDefinitionInvalid,
    ContractDefinitionLookupUnavailable,
    ContractDefinitionOperator,
    ContractDefinitionProposalNotKept,
    ContractDefinitionProposalUnavailable,
    ContractDefinitionReceipt,
    ContractDefinitionStaleProposal,
    InferencePerformanceCriterionDefinition,
    InferencePerformanceMetric,
    ProcessLocalContractDefinitionService,
)
from .poc_creation import POC_ID_PATTERN
from .poc_proposal_review import (
    ProcessLocalProposalReviewService,
    ProposalReviewCapacityExceeded,
    ProposalReviewCrossPOC,
    ProposalReviewDecisionConflict,
    ProposalReviewError,
    ProposalReviewIdempotencyConflict,
    ProposalReviewInvalid,
    ProposalReviewItem,
    ProposalReviewLookupUnavailable,
    ProposalReviewProposalUnavailable,
    ProposalReviewStaleProposal,
    ProposalReviewState,
)


_POC_ID_RE = re.compile(POC_ID_PATTERN)
_DEFINITION_FIELDS = {
    "concurrency",
    "idempotency_key",
    "metric",
    "minimum_samples",
    "operator",
    "output_tokens_max",
    "output_tokens_min",
    "prompt_tokens_max",
    "prompt_tokens_min",
    "proposal_id",
    "rationale",
    "reviewer",
    "threshold",
}


@dataclass(frozen=True, slots=True)
class POCContractDefinitionWebAPIResponse:
    """Transport-neutral HTTP status and JSON object."""

    status: HTTPStatus
    payload: dict[str, Any]


class POCContractDefinitionWebAPIRequestError(ValueError):
    """A definition request violated the exact browser API contract."""


def is_poc_contract_definition_web_api_target(target: str) -> bool:
    """Return whether a raw request target belongs to this API namespace."""

    if type(target) is not str:
        return False
    path = urlparse(target).path
    return bool(
        re.match(
            r"^/api/+pocs/[^/]+/definitions(?:/|$)",
            path,
        )
    )


def handle_poc_contract_definition_web_api_request(
    *,
    method: str,
    target: str,
    payload: Mapping[str, Any] | None,
    definition_runtime: ProcessLocalContractDefinitionService,
    proposal_runtime: ProcessLocalProposalReviewService,
    current_proposal_lookup: (
        Callable[[str], Sequence[ProposalReviewItem]] | None
    ) = None,
) -> POCContractDefinitionWebAPIResponse | None:
    """Handle one exact POC contract-definition API request.

    ``None`` means the request does not belong to this namespace. Once the
    namespace is claimed, malformed or unknown routes fail closed and never
    fall through to another handler.
    """

    if type(definition_runtime) is not ProcessLocalContractDefinitionService:
        raise TypeError(
            "definition_runtime must be a "
            "ProcessLocalContractDefinitionService."
        )
    if type(proposal_runtime) is not ProcessLocalProposalReviewService:
        raise TypeError(
            "proposal_runtime must be a ProcessLocalProposalReviewService."
        )
    if current_proposal_lookup is not None and not callable(
        current_proposal_lookup
    ):
        raise TypeError("current_proposal_lookup must be callable.")
    if not is_poc_contract_definition_web_api_target(target):
        return None

    try:
        path = _require_exact_local_target(target)
        poc_id, is_exact_route = _parse_definition_path(path)
        if not is_exact_route:
            return _error(
                HTTPStatus.NOT_FOUND,
                "Contract definition route was not found.",
            )
        return _dispatch(
            method=method,
            poc_id=poc_id,
            payload=payload,
            definition_runtime=definition_runtime,
            proposal_runtime=proposal_runtime,
            current_proposal_lookup=current_proposal_lookup,
        )
    except (
        POCContractDefinitionWebAPIRequestError,
        ContractDefinitionInvalid,
        ProposalReviewInvalid,
        TypeError,
        ValueError,
    ):
        return _error(
            HTTPStatus.BAD_REQUEST,
            "Contract definition request is invalid.",
        )
    except (
        ContractDefinitionProposalUnavailable,
        ProposalReviewProposalUnavailable,
        ProposalReviewCrossPOC,
    ):
        return _error(
            HTTPStatus.NOT_FOUND,
            "Contract definition proposal was not found.",
        )
    except (
        ContractDefinitionConflict,
        ContractDefinitionIdempotencyConflict,
        ContractDefinitionProposalNotKept,
        ContractDefinitionStaleProposal,
        ProposalReviewDecisionConflict,
        ProposalReviewIdempotencyConflict,
        ProposalReviewStaleProposal,
    ):
        return _error(
            HTTPStatus.CONFLICT,
            "Contract definition conflicts with the current POC state.",
        )
    except (
        ContractDefinitionCapacityExceeded,
        ContractDefinitionLookupUnavailable,
        ProposalReviewCapacityExceeded,
        ProposalReviewLookupUnavailable,
    ):
        return _error(
            HTTPStatus.SERVICE_UNAVAILABLE,
            "Contract definition is temporarily unavailable.",
        )
    except (ContractDefinitionError, ProposalReviewError):
        return _error(
            HTTPStatus.SERVICE_UNAVAILABLE,
            "Contract definition is temporarily unavailable.",
        )


def _require_exact_local_target(target: str) -> str:
    parsed = urlparse(target)
    if (
        parsed.scheme
        or parsed.netloc
        or parsed.params
        or parsed.query
        or parsed.fragment
        or parsed.path != target
    ):
        raise POCContractDefinitionWebAPIRequestError
    return parsed.path


def _parse_definition_path(path: str) -> tuple[str, bool]:
    parts = path.strip("/").split("/")
    if (
        len(parts) < 4
        or parts[:2] != ["api", "pocs"]
        or parts[3] != "definitions"
        or _POC_ID_RE.fullmatch(parts[2]) is None
        or path.startswith("//")
        or "//" in path
    ):
        raise POCContractDefinitionWebAPIRequestError
    exact_path = "/api/pocs/{0}/definitions".format(parts[2])
    return parts[2], path == exact_path


def _dispatch(
    *,
    method: str,
    poc_id: str,
    payload: Mapping[str, Any] | None,
    definition_runtime: ProcessLocalContractDefinitionService,
    proposal_runtime: ProcessLocalProposalReviewService,
    current_proposal_lookup: (
        Callable[[str], Sequence[ProposalReviewItem]] | None
    ),
) -> POCContractDefinitionWebAPIResponse:
    if type(method) is not str:
        raise POCContractDefinitionWebAPIRequestError

    if method == "GET":
        if payload is not None:
            raise POCContractDefinitionWebAPIRequestError
        proposals = _current_kept_proposals(
            proposal_runtime,
            poc_id,
            current_proposal_lookup=current_proposal_lookup,
        )
        definitions = _current_definition_receipts(
            definition_runtime,
            poc_id=poc_id,
            proposals=proposals,
        )
        return _ok(
            {
                "poc_id": poc_id,
                "proposals": [
                    _proposal_payload(
                        proposal,
                        definition=definitions.get(proposal.proposal_id),
                    )
                    for proposal in proposals
                ],
            }
        )

    if method == "POST":
        body = _require_object_payload(payload)
        _require_only_fields(body, _DEFINITION_FIELDS)
        if type(body["proposal_id"]) is not str:
            raise POCContractDefinitionWebAPIRequestError
        try:
            metric = InferencePerformanceMetric(body["metric"])
            operator = ContractDefinitionOperator(body["operator"])
        except (TypeError, ValueError) as error:
            raise POCContractDefinitionWebAPIRequestError from error
        criterion = InferencePerformanceCriterionDefinition(
            metric=metric,
            operator=operator,
            threshold=body["threshold"],
            minimum_samples=body["minimum_samples"],
            concurrency=body["concurrency"],
            prompt_tokens_min=body["prompt_tokens_min"],
            prompt_tokens_max=body["prompt_tokens_max"],
            output_tokens_min=body["output_tokens_min"],
            output_tokens_max=body["output_tokens_max"],
            reviewer=body["reviewer"],
            rationale=body["rationale"],
        )
        if current_proposal_lookup is not None:
            current = _current_proposals(
                proposal_runtime,
                poc_id,
                current_proposal_lookup=current_proposal_lookup,
            )
            matching = next(
                (
                    proposal
                    for proposal in current
                    if proposal.proposal_id == body["proposal_id"]
                ),
                None,
            )
            if matching is None:
                raise ContractDefinitionProposalUnavailable(
                    "Proposal is not current for this agreement version."
                )
            if matching.review_state != ProposalReviewState.KEEP_FOR_CONTRACT:
                raise ContractDefinitionProposalNotKept(
                    "Current proposal was not kept for contract work."
                )
        result = definition_runtime.define(
            poc_id,
            body["proposal_id"],
            criterion,
            idempotency_key=body["idempotency_key"],
        )
        status = (
            HTTPStatus.CREATED
            if result.disposition == ContractDefinitionDisposition.CREATED
            else HTTPStatus.OK
        )
        return POCContractDefinitionWebAPIResponse(
            status,
            {
                "poc_id": result.receipt.poc_id,
                "proposal_id": result.receipt.proposal_id,
                "disposition": result.disposition.value,
                "definition": _definition_payload(result.receipt),
            },
        )

    return _error(
        HTTPStatus.METHOD_NOT_ALLOWED,
        "Contract definition method is not allowed.",
    )


def _current_kept_proposals(
    runtime: ProcessLocalProposalReviewService,
    poc_id: str,
    *,
    current_proposal_lookup: (
        Callable[[str], Sequence[ProposalReviewItem]] | None
    ) = None,
) -> tuple[ProposalReviewItem, ...]:
    detached = _current_proposals(
        runtime,
        poc_id,
        current_proposal_lookup=current_proposal_lookup,
    )
    return tuple(
        proposal
        for proposal in detached
        if proposal.review_state == ProposalReviewState.KEEP_FOR_CONTRACT
    )


def _current_proposals(
    runtime: ProcessLocalProposalReviewService,
    poc_id: str,
    *,
    current_proposal_lookup: (
        Callable[[str], Sequence[ProposalReviewItem]] | None
    ) = None,
) -> tuple[ProposalReviewItem, ...]:
    try:
        raw = (
            runtime.list_proposals(poc_id)
            if current_proposal_lookup is None
            else current_proposal_lookup(poc_id)
        )
    except ProposalReviewError:
        raise
    except Exception as error:
        raise ProposalReviewLookupUnavailable(
            "Current agreement proposal scope is unavailable."
        ) from error
    if not isinstance(raw, (tuple, list)):
        raise ProposalReviewLookupUnavailable(
            "Current agreement proposal scope is unavailable."
        )
    detached = tuple(raw)
    if (
        any(
            type(item) is not ProposalReviewItem or item.poc_id != poc_id
            for item in detached
        )
        or len({item.proposal_id for item in detached}) != len(detached)
    ):
        raise ProposalReviewLookupUnavailable(
            "Current agreement proposal scope is unavailable."
        )
    return detached


def _current_definition_receipts(
    runtime: ProcessLocalContractDefinitionService,
    *,
    poc_id: str,
    proposals: tuple[ProposalReviewItem, ...],
) -> dict[str, ContractDefinitionReceipt]:
    current_ids = {proposal.proposal_id for proposal in proposals}
    projected: dict[str, ContractDefinitionReceipt] = {}
    for receipt in runtime.definitions():
        if receipt.poc_id != poc_id or receipt.proposal_id not in current_ids:
            continue
        if receipt.proposal_id in projected:
            raise ContractDefinitionError(
                "Definition projection contains duplicate proposal bindings."
            )
        projected[receipt.proposal_id] = receipt
    return projected


def _require_object_payload(
    payload: Mapping[str, Any] | None,
) -> Mapping[str, Any]:
    if type(payload) is not dict:
        raise POCContractDefinitionWebAPIRequestError
    return payload


def _require_only_fields(
    payload: Mapping[str, Any],
    allowed: set[str],
) -> None:
    if set(payload) != allowed or any(type(key) is not str for key in payload):
        raise POCContractDefinitionWebAPIRequestError


def _proposal_payload(
    proposal: ProposalReviewItem,
    *,
    definition: ContractDefinitionReceipt | None,
) -> dict[str, Any]:
    if (
        type(proposal) is not ProposalReviewItem
        or proposal.review_state != ProposalReviewState.KEEP_FOR_CONTRACT
    ):
        raise ProposalReviewError(
            "Proposal could not be projected safely."
        )
    return {
        "proposal_id": proposal.proposal_id,
        "source_receipt_id": proposal.source_receipt_id,
        "source_kind": proposal.source_kind.value,
        "source_quote": proposal.source_quote,
        "normalized_claim": proposal.normalized_claim,
        "review_state": proposal.review_state.value,
        "definition": (
            None if definition is None else _definition_payload(definition)
        ),
    }


def _definition_payload(
    receipt: ContractDefinitionReceipt,
) -> dict[str, Any]:
    if type(receipt) is not ContractDefinitionReceipt:
        raise ContractDefinitionError(
            "Definition receipt could not be projected safely."
        )
    return {
        "definition_id": receipt.definition_id,
        "definition_sha256": receipt.definition_sha256,
        "metric": receipt.metric.value,
        "unit": receipt.unit.value,
        "operator": receipt.operator.value,
        "threshold": receipt.threshold,
        "minimum_samples": receipt.minimum_samples,
        "concurrency": receipt.concurrency,
        "prompt_tokens_min": receipt.prompt_tokens_min,
        "prompt_tokens_max": receipt.prompt_tokens_max,
        "output_tokens_min": receipt.output_tokens_min,
        "output_tokens_max": receipt.output_tokens_max,
        "defined_at": receipt.defined_at.isoformat(),
    }


def _ok(
    payload: dict[str, Any],
) -> POCContractDefinitionWebAPIResponse:
    return POCContractDefinitionWebAPIResponse(HTTPStatus.OK, payload)


def _error(
    status: HTTPStatus,
    message: str,
) -> POCContractDefinitionWebAPIResponse:
    return POCContractDefinitionWebAPIResponse(status, {"error": message})


__all__ = [
    "POCContractDefinitionWebAPIRequestError",
    "POCContractDefinitionWebAPIResponse",
    "handle_poc_contract_definition_web_api_request",
    "is_poc_contract_definition_web_api_target",
]
