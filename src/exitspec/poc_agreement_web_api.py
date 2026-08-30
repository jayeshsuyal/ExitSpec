"""Pure HTTP transport for the source-neutral A5 agreement lifecycle."""

from __future__ import annotations

from dataclasses import dataclass
from http import HTTPStatus
import re
from typing import Any, Mapping
from urllib.parse import urlparse

from pydantic import ValidationError

from .confirmations import ContractConfirmation
from .models import POCContract
from .poc_agreement import (
    AgreementCapacityExceeded,
    AgreementConflict,
    AgreementError,
    AgreementInvalid,
    AgreementNotFound,
    AgreementRevision,
    AgreementStale,
    AgreementWriteResult,
    ProcessLocalAgreementLifecycleService,
)
from .poc_creation import POC_ID_PATTERN
from .review_links import CustomerReviewInvitation, ReviewInvitationError


_POC_ID_RE = re.compile(POC_ID_PATTERN)
_AGREEMENT_ROUTE_RE = re.compile(
    r"^/api/pocs/(poc_[a-z0-9][a-z0-9_-]{2,63})/agreement(?:/(freeze|review|revision))?$"
)
_CUSTOMER_REVIEW_ROUTE_RE = re.compile(
    r"^/api/review/([A-Za-z0-9_-]{32,512})$"
)

_PREPARE_FIELDS = {"reviewer", "rationale", "idempotency_key"}
_ACTION_FIELDS = {"idempotency_key"}
_REVISION_FIELDS = {"reviewer", "rationale", "idempotency_key"}
_DECISION_FIELDS = {
    "review_id",
    "contract_id",
    "contract_version",
    "confirmation_fingerprint",
    "decision",
    "agreement_acknowledged",
    "rationale",
    "idempotency_key",
}


@dataclass(frozen=True, slots=True)
class AgreementWebAPIResponse:
    status: HTTPStatus
    payload: dict[str, Any]


class AgreementWebAPIRequestError(ValueError):
    """A request violated the exact A5 transport contract."""


def is_poc_agreement_web_api_target(target: str) -> bool:
    if type(target) is not str:
        return False
    return bool(_AGREEMENT_ROUTE_RE.fullmatch(urlparse(target).path))


def is_customer_review_web_api_target(target: str) -> bool:
    if type(target) is not str:
        return False
    return bool(_CUSTOMER_REVIEW_ROUTE_RE.fullmatch(urlparse(target).path))


def handle_poc_agreement_web_api_request(
    *,
    method: str,
    target: str,
    payload: Mapping[str, Any] | None,
    runtime: ProcessLocalAgreementLifecycleService,
) -> AgreementWebAPIResponse | None:
    if type(runtime) is not ProcessLocalAgreementLifecycleService:
        raise TypeError("runtime must be a ProcessLocalAgreementLifecycleService.")
    if not is_poc_agreement_web_api_target(target):
        return None
    try:
        path = _exact_target(target)
        match = _AGREEMENT_ROUTE_RE.fullmatch(path)
        if match is None:  # pragma: no cover - guarded by target predicate
            raise AgreementWebAPIRequestError
        poc_id, action = match.group(1), match.group(2)
        if method == "GET":
            if payload is not None or action is not None:
                raise AgreementWebAPIRequestError
            return _ok(runtime.snapshot_payload(poc_id))
        if method != "POST":
            return _error(HTTPStatus.METHOD_NOT_ALLOWED, "Agreement method is not allowed.")
        body = _object(payload)
        if action is None:
            _only(body, _PREPARE_FIELDS)
            result = runtime.prepare(
                poc_id,
                reviewer=body["reviewer"],
                rationale=body["rationale"],
                idempotency_key=body["idempotency_key"],
            )
            return _write_response(runtime, poc_id, result)
        if action == "freeze":
            _only(body, _ACTION_FIELDS)
            result = runtime.freeze(poc_id, idempotency_key=body["idempotency_key"])
        elif action == "revision":
            _only(body, _REVISION_FIELDS)
            result = runtime.start_revision(
                poc_id,
                reviewer=body["reviewer"],
                rationale=body["rationale"],
                idempotency_key=body["idempotency_key"],
            )
        else:
            _only(body, _ACTION_FIELDS)
            result = runtime.reissue_customer_review(poc_id, idempotency_key=body["idempotency_key"])
        return _write_response(runtime, poc_id, result)
    except AgreementWebAPIRequestError:
        return _error(HTTPStatus.BAD_REQUEST, "Agreement request is invalid.")
    except (ValidationError, AgreementInvalid, TypeError, ValueError):
        return _error(HTTPStatus.BAD_REQUEST, "Agreement request is invalid.")
    except AgreementNotFound:
        return _error(HTTPStatus.NOT_FOUND, "Agreement was not found.")
    except (AgreementStale, AgreementConflict):
        return _error(HTTPStatus.CONFLICT, "Agreement conflicts with current POC state.")
    except AgreementCapacityExceeded:
        return _error(HTTPStatus.SERVICE_UNAVAILABLE, "Agreement capacity is exhausted.")
    except AgreementError:
        return _error(HTTPStatus.SERVICE_UNAVAILABLE, "Agreement is temporarily unavailable.")


def handle_customer_review_web_api_request(
    *,
    method: str,
    target: str,
    payload: Mapping[str, Any] | None,
    runtime: ProcessLocalAgreementLifecycleService,
) -> AgreementWebAPIResponse | None:
    if type(runtime) is not ProcessLocalAgreementLifecycleService:
        raise TypeError("runtime must be a ProcessLocalAgreementLifecycleService.")
    if not is_customer_review_web_api_target(target):
        return None
    try:
        path = _exact_target(target)
        match = _CUSTOMER_REVIEW_ROUTE_RE.fullmatch(path)
        if match is None:  # pragma: no cover - guarded by target predicate
            raise AgreementWebAPIRequestError
        token = match.group(1)
        if method == "GET":
            if payload is not None:
                raise AgreementWebAPIRequestError
            return _ok(runtime.customer_review_payload(token))
        if method != "POST":
            return _error(HTTPStatus.METHOD_NOT_ALLOWED, "Customer review method is not allowed.")
        body = _object(payload)
        _only(body, _DECISION_FIELDS)
        review = runtime.customer_review_payload(token)["review"]
        if (
            body["review_id"] != review["review_id"]
            or body["contract_id"] != review["contract_id"]
            or body["contract_version"] != review["contract_version"]
            or body["confirmation_fingerprint"] != review["confirmation_fingerprint"]
        ):
            raise AgreementWebAPIRequestError
        result = runtime.record_customer_review_decision(
            token,
            decision=body["decision"],
            agreement_acknowledged=body["agreement_acknowledged"],
            rationale=body["rationale"],
            idempotency_key=body["idempotency_key"],
            contract_id=body["contract_id"],
            contract_version=body["contract_version"],
            confirmation_fingerprint=body["confirmation_fingerprint"],
        )
        if type(result.value) is not ContractConfirmation:
            raise AgreementError("Customer confirmation result is invalid.")
        current = runtime.customer_review_payload(token)
        return AgreementWebAPIResponse(
            HTTPStatus.OK,
            {
                "confirmation": current["confirmation"],
                "decision": current["review"]["decision"],
                "review": current["review"],
                "confirmation_id": result.value.confirmation_id,
                "idempotent_replay": result.replayed,
            },
        )
    except AgreementWebAPIRequestError:
        return _error(HTTPStatus.BAD_REQUEST, "Customer review request is invalid.")
    except ReviewInvitationError as error:
        if "expired" in str(error).lower():
            return _error(HTTPStatus.GONE, "Customer review link has expired.")
        return _error(HTTPStatus.NOT_FOUND, "Customer review link is invalid.")
    except (ValidationError, AgreementInvalid, TypeError, ValueError):
        return _error(HTTPStatus.BAD_REQUEST, "Customer review request is invalid.")
    except AgreementNotFound:
        return _error(HTTPStatus.NOT_FOUND, "Customer review link is invalid.")
    except (AgreementStale, AgreementConflict):
        return _error(HTTPStatus.CONFLICT, "Customer review conflicts with current agreement state.")
    except AgreementError:
        return _error(HTTPStatus.SERVICE_UNAVAILABLE, "Customer review is temporarily unavailable.")


def _write_response(
    runtime: ProcessLocalAgreementLifecycleService,
    poc_id: str,
    result: AgreementWriteResult,
) -> AgreementWebAPIResponse:
    snapshot = runtime.snapshot_payload(poc_id)
    payload: dict[str, Any] = {
        "poc_id": poc_id,
        "disposition": "IDEMPOTENT_REPLAY" if result.replayed else "CREATED",
        "agreement": snapshot,
    }
    if isinstance(result.value, CustomerReviewInvitation):
        payload["customer_review"] = snapshot["customer_review"]
    elif isinstance(result.value, ContractConfirmation):
        payload["confirmation"] = snapshot["confirmation"]
    elif isinstance(result.value, AgreementRevision):
        payload["revision"] = snapshot["revision"]
    elif isinstance(result.value, POCContract):
        payload["frozen_contract"] = snapshot["frozen_contract"]
    else:
        payload["draft"] = snapshot["draft"]
    return AgreementWebAPIResponse(
        HTTPStatus.OK if result.replayed else HTTPStatus.CREATED,
        payload,
    )


def _exact_target(target: str) -> str:
    parsed = urlparse(target)
    if (
        parsed.scheme
        or parsed.netloc
        or parsed.params
        or parsed.query
        or parsed.fragment
        or parsed.path != target
    ):
        raise AgreementWebAPIRequestError
    return parsed.path


def _object(payload: Mapping[str, Any] | None) -> Mapping[str, Any]:
    if type(payload) is not dict:
        raise AgreementWebAPIRequestError
    return payload


def _only(payload: Mapping[str, Any], fields: set[str]) -> None:
    if set(payload) != fields or any(type(key) is not str for key in payload):
        raise AgreementWebAPIRequestError


def _ok(payload: dict[str, Any]) -> AgreementWebAPIResponse:
    return AgreementWebAPIResponse(HTTPStatus.OK, payload)


def _error(status: HTTPStatus, message: str) -> AgreementWebAPIResponse:
    return AgreementWebAPIResponse(status, {"error": message})


__all__ = [
    "AgreementWebAPIRequestError",
    "AgreementWebAPIResponse",
    "handle_customer_review_web_api_request",
    "handle_poc_agreement_web_api_request",
    "is_customer_review_web_api_target",
    "is_poc_agreement_web_api_target",
]
