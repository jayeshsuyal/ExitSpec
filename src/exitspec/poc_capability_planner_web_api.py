"""Pure HTTP projection for the Train A A4 capability planner."""

from __future__ import annotations

from dataclasses import dataclass
from http import HTTPStatus
import re
from typing import Any, Mapping
from urllib.parse import urlparse

from pydantic import ValidationError

from .poc_capability_planner import (
    CapabilityPlan,
    CapabilityPlanningCapacityExceeded,
    CapabilityPlanningCrossPOC,
    CapabilityPlanningError,
    CapabilityPlanningIdempotencyConflict,
    CapabilityPlanningInvalid,
    CapabilityPlanningLookupUnavailable,
    CapabilityPlanningProposalUnavailable,
    CapabilityPlanningStaleProposal,
    PlannerItemInput,
    ProcessLocalCapabilityPlannerService,
)
from .poc_creation import POC_ID_PATTERN


_PLAN_ROUTE_RE = re.compile(
    r"^/api/pocs/(poc_[a-z0-9][a-z0-9_-]{2,63})/capability-plan$"
)


@dataclass(frozen=True, slots=True)
class POCCapabilityPlannerWebAPIResponse:
    status: HTTPStatus
    payload: dict[str, Any]


class POCCapabilityPlannerWebAPIRequestError(ValueError):
    """A request targeted A4 but violated its exact transport contract."""


def is_poc_capability_planner_web_api_target(target: str) -> bool:
    if type(target) is not str:
        return False
    path = urlparse(target).path
    return bool(_PLAN_ROUTE_RE.fullmatch(path))


def handle_poc_capability_planner_web_api_request(
    *,
    method: str,
    target: str,
    payload: Mapping[str, Any] | None,
    runtime: ProcessLocalCapabilityPlannerService,
) -> POCCapabilityPlannerWebAPIResponse | None:
    if type(runtime) is not ProcessLocalCapabilityPlannerService:
        raise TypeError("runtime must be a ProcessLocalCapabilityPlannerService.")
    if not is_poc_capability_planner_web_api_target(target):
        return None
    try:
        path = _require_exact_target(target)
        match = _PLAN_ROUTE_RE.fullmatch(path)
        if match is None:  # pragma: no cover - guarded by target predicate
            raise POCCapabilityPlannerWebAPIRequestError
        poc_id = match.group(1)
        if method == "GET":
            if payload is not None:
                raise POCCapabilityPlannerWebAPIRequestError
            current, needs_replan = runtime.current_plan_status(poc_id)
            plans = runtime.plans(poc_id)
            return _ok(
                {
                    "poc_id": poc_id,
                    "registry": [entry.model_dump(mode="json") for entry in runtime.registry],
                    "semantics": runtime.semantics.model_dump(mode="json"),
                    "plan": None if current is None else current.model_dump(mode="json"),
                    "needs_replan": needs_replan,
                    "plans": [plan.model_dump(mode="json") for plan in plans],
                }
            )
        if method != "POST":
            return _error(HTTPStatus.METHOD_NOT_ALLOWED, "Capability planning method is not allowed.")
        body = _require_object_payload(payload)
        _require_only_fields(body, {"items", "idempotency_key"})
        if type(body.get("items")) not in {list, tuple}:
            raise POCCapabilityPlannerWebAPIRequestError
        items = tuple(PlannerItemInput.model_validate(item) for item in body["items"])
        if type(body.get("idempotency_key")) is not str or not body["idempotency_key"].strip():
            raise POCCapabilityPlannerWebAPIRequestError
        result = runtime.plan_with_status(poc_id, items, idempotency_key=body["idempotency_key"])
        return POCCapabilityPlannerWebAPIResponse(
            HTTPStatus.OK if result.idempotent_replay else HTTPStatus.CREATED,
            {"plan": result.plan.model_dump(mode="json"), "idempotent_replay": result.idempotent_replay},
        )
    except POCCapabilityPlannerWebAPIRequestError:
        return _error(HTTPStatus.BAD_REQUEST, "Capability planning request is invalid.")
    except ValidationError:
        return _error(HTTPStatus.BAD_REQUEST, "Capability planning request is invalid.")
    except CapabilityPlanningCrossPOC:
        return _error(HTTPStatus.NOT_FOUND, "The retained proposal was not found.")
    except CapabilityPlanningProposalUnavailable:
        return _error(HTTPStatus.CONFLICT, "Every current retained proposal must be planned exactly once.")
    except (CapabilityPlanningIdempotencyConflict, CapabilityPlanningStaleProposal):
        return _error(HTTPStatus.CONFLICT, "Capability planning conflicts with current POC state.")
    except CapabilityPlanningInvalid:
        return _error(HTTPStatus.BAD_REQUEST, "Capability planning request is invalid.")
    except (CapabilityPlanningCapacityExceeded, CapabilityPlanningLookupUnavailable, CapabilityPlanningError):
        return _error(HTTPStatus.SERVICE_UNAVAILABLE, "Capability planning is temporarily unavailable.")


def _require_exact_target(target: str) -> str:
    parsed = urlparse(target)
    if parsed.scheme or parsed.netloc or parsed.params or parsed.query or parsed.fragment or parsed.path != target:
        raise POCCapabilityPlannerWebAPIRequestError
    return parsed.path


def _require_object_payload(payload: Mapping[str, Any] | None) -> Mapping[str, Any]:
    if type(payload) is not dict:
        raise POCCapabilityPlannerWebAPIRequestError
    return payload


def _require_only_fields(payload: Mapping[str, Any], allowed: set[str]) -> None:
    if set(payload) != allowed:
        raise POCCapabilityPlannerWebAPIRequestError


def _ok(payload: dict[str, Any]) -> POCCapabilityPlannerWebAPIResponse:
    return POCCapabilityPlannerWebAPIResponse(HTTPStatus.OK, payload)


def _error(status: HTTPStatus, message: str) -> POCCapabilityPlannerWebAPIResponse:
    return POCCapabilityPlannerWebAPIResponse(status, {"error": message})


__all__ = [
    "POCCapabilityPlannerWebAPIRequestError",
    "POCCapabilityPlannerWebAPIResponse",
    "handle_poc_capability_planner_web_api_request",
    "is_poc_capability_planner_web_api_target",
]
