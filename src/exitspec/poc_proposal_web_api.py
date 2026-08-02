"""Pure browser API projection for human proposal triage."""

from __future__ import annotations

from dataclasses import dataclass
from http import HTTPStatus
import re
from typing import Any, Mapping
from urllib.parse import urlparse

from .poc_creation import POC_ID_PATTERN
from .poc_proposal_review import (
    PROPOSAL_ID_PATTERN,
    ProcessLocalProposalReviewService,
    ProposalDecision,
    ProposalDecisionDisposition,
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
_PROPOSAL_ID_RE = re.compile(PROPOSAL_ID_PATTERN)


@dataclass(frozen=True, slots=True)
class POCProposalWebAPIResponse:
    """Transport-neutral status and JSON object."""

    status: HTTPStatus
    payload: dict[str, Any]


class POCProposalWebAPIRequestError(ValueError):
    """A request targeted proposal review but violated its exact contract."""


def is_poc_proposal_web_api_target(target: str) -> bool:
    """Return whether a raw request target belongs to proposal review."""

    if type(target) is not str:
        return False
    parts = urlparse(target).path.strip("/").split("/")
    return (
        len(parts) >= 4
        and parts[:2] == ["api", "pocs"]
        and parts[3] == "proposals"
    )


def handle_poc_proposal_web_api_request(
    *,
    method: str,
    target: str,
    payload: Mapping[str, Any] | None,
    runtime: ProcessLocalProposalReviewService,
) -> POCProposalWebAPIResponse | None:
    """Handle one exact proposal-review API request."""

    if type(runtime) is not ProcessLocalProposalReviewService:
        raise TypeError("runtime must be a ProcessLocalProposalReviewService.")
    if not is_poc_proposal_web_api_target(target):
        return None

    try:
        path = _require_exact_target(target)
        poc_id, proposal_id, action = _parse_path(path)
        return _dispatch(
            method=method,
            poc_id=poc_id,
            proposal_id=proposal_id,
            action=action,
            payload=payload,
            runtime=runtime,
        )
    except (POCProposalWebAPIRequestError, ProposalReviewInvalid, ValueError):
        return _error(HTTPStatus.BAD_REQUEST, "Proposal review request is invalid.")
    except (
        ProposalReviewProposalUnavailable,
        ProposalReviewCrossPOC,
    ):
        return _error(HTTPStatus.NOT_FOUND, "Proposal was not found.")
    except (
        ProposalReviewDecisionConflict,
        ProposalReviewIdempotencyConflict,
        ProposalReviewStaleProposal,
    ):
        return _error(
            HTTPStatus.CONFLICT,
            "Proposal review conflicts with the current POC state.",
        )
    except (
        ProposalReviewCapacityExceeded,
        ProposalReviewLookupUnavailable,
    ):
        return _error(
            HTTPStatus.SERVICE_UNAVAILABLE,
            "Proposal review is temporarily unavailable.",
        )
    except ProposalReviewError:
        return _error(
            HTTPStatus.SERVICE_UNAVAILABLE,
            "Proposal review is temporarily unavailable.",
        )


def _require_exact_target(target: str) -> str:
    parsed = urlparse(target)
    if (
        parsed.scheme
        or parsed.netloc
        or parsed.params
        or parsed.query
        or parsed.fragment
        or parsed.path != target
    ):
        raise POCProposalWebAPIRequestError
    return parsed.path


def _parse_path(path: str) -> tuple[str, str | None, str | None]:
    parts = path.strip("/").split("/")
    if (
        len(parts) not in {4, 6}
        or parts[:2] != ["api", "pocs"]
        or parts[3] != "proposals"
        or _POC_ID_RE.fullmatch(parts[2]) is None
    ):
        raise POCProposalWebAPIRequestError
    if len(parts) == 4:
        return parts[2], None, None
    if (
        _PROPOSAL_ID_RE.fullmatch(parts[4]) is None
        or parts[5] != "decision"
    ):
        raise POCProposalWebAPIRequestError
    return parts[2], parts[4], parts[5]


def _dispatch(
    *,
    method: str,
    poc_id: str,
    proposal_id: str | None,
    action: str | None,
    payload: Mapping[str, Any] | None,
    runtime: ProcessLocalProposalReviewService,
) -> POCProposalWebAPIResponse:
    if type(method) is not str:
        raise POCProposalWebAPIRequestError

    if method == "GET":
        if payload is not None or proposal_id is not None or action is not None:
            raise POCProposalWebAPIRequestError
        proposals = tuple(
            item
            for item in runtime.list_proposals(poc_id)
            if item.review_state == ProposalReviewState.NEEDS_REVIEW
        )
        return _ok(
            {
                "poc_id": poc_id,
                "proposals": [_proposal_payload(item) for item in proposals],
            }
        )

    if method == "POST":
        if proposal_id is None or action != "decision":
            return _error(
                HTTPStatus.NOT_FOUND,
                "Proposal review route was not found.",
            )
        body = _require_object_payload(payload)
        _require_only_fields(
            body,
            {
                "decision",
                "reviewer",
                "rationale",
                "idempotency_key",
            },
        )
        try:
            decision = ProposalDecision(body["decision"])
        except (TypeError, ValueError) as error:
            raise POCProposalWebAPIRequestError from error
        result = runtime.decide(
            poc_id,
            proposal_id,
            decision,
            body["reviewer"],
            body["rationale"],
            body["idempotency_key"],
        )
        status = (
            HTTPStatus.CREATED
            if result.disposition == ProposalDecisionDisposition.CREATED
            else HTTPStatus.OK
        )
        return POCProposalWebAPIResponse(
            status,
            {
                "decision": result.receipt.decision.value,
                "disposition": result.disposition.value,
                "poc_id": result.receipt.poc_id,
                "proposal_id": result.receipt.proposal_id,
                "review_state": result.receipt.decision.value,
            },
        )

    return _error(
        HTTPStatus.METHOD_NOT_ALLOWED,
        "Proposal review method is not allowed.",
    )


def _require_object_payload(
    payload: Mapping[str, Any] | None,
) -> Mapping[str, Any]:
    if type(payload) is not dict:
        raise POCProposalWebAPIRequestError
    return payload


def _require_only_fields(
    payload: Mapping[str, Any],
    allowed: set[str],
) -> None:
    if set(payload) != allowed:
        raise POCProposalWebAPIRequestError


def _proposal_payload(item: ProposalReviewItem) -> dict[str, Any]:
    return {
        "normalized_claim": item.normalized_claim,
        "proposal_id": item.proposal_id,
        "source_receipt_id": item.source_receipt_id,
        "source_kind": item.source_kind.value,
        "source_quote": item.source_quote,
        "review_state": item.review_state.value,
    }


def _ok(payload: dict[str, Any]) -> POCProposalWebAPIResponse:
    return POCProposalWebAPIResponse(HTTPStatus.OK, payload)


def _error(status: HTTPStatus, message: str) -> POCProposalWebAPIResponse:
    return POCProposalWebAPIResponse(status, {"error": message})


__all__ = [
    "POCProposalWebAPIRequestError",
    "POCProposalWebAPIResponse",
    "handle_poc_proposal_web_api_request",
    "is_poc_proposal_web_api_target",
]
