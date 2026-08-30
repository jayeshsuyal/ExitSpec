"""Source-neutral, process-local browser runtime for Train A A2/A3/A4/A5.

This runtime deliberately owns only draft identity, source attachment, human
proposal projection, capability planning, customer agreement review, and the
generic A6 evidence façade. It does not construct the seeded session, load
seeded fixtures, or expose provider execution routes.
The existing compatibility demo remains in :mod:`exitspec.web`.
"""

from __future__ import annotations

import json
import math
import mimetypes
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import hashlib
import re
import tempfile
import threading
from typing import Any, Mapping
from urllib.parse import parse_qsl, unquote, urlparse
import webbrowser

from pydantic import ValidationError

from .assisted_authoring import ProcessLocalAssistedAuthoringService
from .draft_workspace import project_draft_dashboard
from .generic_evidence_pack import GenericEvidencePackError
from .poc_creation import (
    DraftPOCCapacityExceeded,
    DraftPOCCreateRequest,
    DraftPOCIdempotencyConflict,
    DraftPOCNotFound,
    DuplicateDraftPOCId,
    ProcessLocalDraftPOCService,
)
from .poc_proposal_review import (
    ProcessLocalProposalReviewService,
)
from .poc_assisted_authoring_web_api import (
    handle_poc_assisted_authoring_web_api_request,
    is_poc_assisted_authoring_web_api_target,
)
from .poc_proposal_web_api import handle_poc_proposal_web_api_request
from .poc_capability_planner import (
    PlannerCriterionInput,
    PlannerItemInput,
    PlanningProvenance,
    ProcessLocalCapabilityPlannerService,
)
from .poc_capability_planner_web_api import (
    handle_poc_capability_planner_web_api_request,
    is_poc_capability_planner_web_api_target,
)
from .poc_agreement import ProcessLocalAgreementLifecycleService
from .poc_agreement_web_api import (
    handle_customer_review_web_api_request,
    handle_poc_agreement_web_api_request,
    is_customer_review_web_api_target,
    is_poc_agreement_web_api_target,
)
from .poc_evidence_orchestration import ProcessLocalEvidenceOrchestrationService
from .poc_evidence_web_api import (
    handle_poc_evidence_web_api_request,
    is_poc_evidence_web_api_target,
)
from .poc_source_intake import (
    POCSourceInput,
    POCSourceIntakeCapacityExceeded,
    POCSourceIntakeError,
    POCSourceIntakeInvalid,
    POCSourceIntakeRevisionRequired,
    ProcessLocalPOCSourceIntake,
)
from .poc_source_web_api import (
    handle_poc_source_web_api_request,
    is_poc_source_web_api_target,
)
from .poc_sources import (
    DuplicatePOCSourceId,
    POCSourceCapacityExceeded,
    POCSourceDraftArchived,
    POCSourceDraftUnavailable,
    POCSourceIdempotencyConflict,
    POCSourceRevisionRequired,
    POCSourceStaleRevision,
    SourceKind,
)
from .synthetic_assisted_authoring import (
    SyntheticSourceNeutralAssistedAuthoringExecutor,
)


STATIC_ROOT = Path(__file__).resolve().parent / "static"
MAX_REQUEST_BYTES = 128 * 1024
# The byte cap is retained; these parser caps bound decoded-object work too.
MAX_REQUEST_JSON_DEPTH = 32
MAX_REQUEST_JSON_NODES = 4_096
POC_ID_PATTERN = r"^poc_[a-z0-9][a-z0-9_-]{2,63}$"
_POC_ID_RE = re.compile(POC_ID_PATTERN)
_SOURCE_PAGE_RE = re.compile(
    r"^/app/pocs/(poc_[a-z0-9][a-z0-9_-]{2,63})/sources/new$"
)
_REVIEW_PAGE_RE = re.compile(
    r"^/app/pocs/(poc_[a-z0-9][a-z0-9_-]{2,63})/review$"
)
_ASSISTED_PAGE_RE = re.compile(
    r"^/app/pocs/(poc_[a-z0-9][a-z0-9_-]{2,63})/assisted-authoring$"
)
_PLANNING_PAGE_RE = re.compile(
    r"^/app/pocs/(poc_[a-z0-9][a-z0-9_-]{2,63})/capability-plan$"
)
_AGREEMENT_PAGE_RE = re.compile(
    r"^/app/pocs/(poc_[a-z0-9][a-z0-9_-]{2,63})/agreement$"
)
_CUSTOMER_REVIEW_PAGE_RE = re.compile(
    r"^/review/[A-Za-z0-9_-]{32,512}$"
)
_DRAFT_API_RE = re.compile(r"^/api/pocs/(poc_[a-z0-9][a-z0-9_-]{2,63})$")
_SOURCE_API_RE = re.compile(
    r"^/api/pocs/(poc_[a-z0-9][a-z0-9_-]{2,63})/sources(?:/([^/]+))?$"
)
_PROPOSAL_API_RE = re.compile(
    r"^/api/pocs/(poc_[a-z0-9][a-z0-9_-]{2,63})/proposals(?:/([^/]+)/decision)?$"
)
_CONVERGENCE_PLAN_API_RE = re.compile(
    r"^/api/pocs/(poc_[a-z0-9][a-z0-9_-]{2,63})/capability-plan/converge$"
)
_EVIDENCE_ARTIFACT_RE = re.compile(
    r"^/artifacts/(eatm_[a-f0-9]{32})/decision-packet\.html$"
)
_SOURCE_ROUTES = {
    "email-text": (SourceKind.EMAIL, "email_text"),
    "meeting": (SourceKind.MEETING, "transcript_text"),
    "document": (SourceKind.DOCUMENT, "document_text"),
    # Notes is an input alias only; it never becomes a domain source kind.
    "notes": (SourceKind.DOCUMENT, "document_text"),
    "contract": (SourceKind.EXISTING_CONTRACT, "contract_json"),
}
_ASSET_NAMES = frozenset(
    {
        "dashboard.html",
        "dashboard.css",
        "dashboard.js",
        "new_poc.html",
        "new_poc.css",
        "new_poc.js",
        "source_intake.html",
        "source_intake.css",
        "source_intake.js",
        "proposal_review.html",
        "proposal_review.css",
        "proposal_review.js",
        "assisted_authoring.html",
        "assisted_authoring.css",
        "assisted_authoring.js",
        "capability_plan.html",
        "capability_plan.css",
        "capability_plan.js",
        "agreement_dynamic.html",
        "agreement_dynamic.css",
        "agreement_dynamic.js",
        "customer_review_dynamic.html",
        "customer_review_dynamic.css",
        "customer_review_dynamic.js",
        "generic_evidence.html",
        "generic_evidence.css",
        "generic_evidence.js",
        "workbench.css",
    }
)


class SourceNeutralPOCDemoServer(ThreadingHTTPServer):
    """A bounded local runtime with one generic POC source/proposal spine."""

    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 128

    def __init__(
        self,
        address: tuple[str, int],
        *,
        static_root: Path = STATIC_ROOT,
        assisted_authoring_executor: Any | None = None,
        evidence_artifact_root: Path | None = None,
    ) -> None:
        self.draft_poc_service = ProcessLocalDraftPOCService()
        self.poc_source_intake = ProcessLocalPOCSourceIntake(
            draft_lookup=self.draft_poc_service.get,
        )
        authoring_executor = (
            SyntheticSourceNeutralAssistedAuthoringExecutor()
            if assisted_authoring_executor is None
            else assisted_authoring_executor
        )
        self.assisted_authoring_service = ProcessLocalAssistedAuthoringService(
            source_lookup=self.poc_source_intake.source_snapshot,
            draft_lookup=self.draft_poc_service.get,
            executor=authoring_executor,
            provider=getattr(authoring_executor, "provider_name", ""),
            endpoint=getattr(authoring_executor, "endpoint", ""),
        )
        self.proposal_review_service = ProcessLocalProposalReviewService(
            proposal_lookup=self._proposal_inputs_for_review,
        )
        self.capability_planner_service = ProcessLocalCapabilityPlannerService(
            proposal_lookup=self._retained_proposals_for_planning,
        )
        self.agreement_service = ProcessLocalAgreementLifecycleService(
            poc_lookup=self.draft_poc_service.get,
            retained_lookup=self._retained_proposals_for_planning,
            planner=self.capability_planner_service,
        )
        if evidence_artifact_root is not None and (
            not isinstance(evidence_artifact_root, Path)
            or not evidence_artifact_root.is_absolute()
            or evidence_artifact_root.is_symlink()
        ):
            raise ValueError("evidence_artifact_root must be an absolute path.")
        self._owned_evidence_artifact_root = (
            tempfile.TemporaryDirectory(prefix="exitspec-source-a6-")
            if evidence_artifact_root is None
            else None
        )
        self.evidence_artifact_root = (
            Path(self._owned_evidence_artifact_root.name).resolve()
            if self._owned_evidence_artifact_root is not None
            else evidence_artifact_root
        )
        self.generic_evidence_service = ProcessLocalEvidenceOrchestrationService(
            contract_lookup=self._frozen_contract_for_evidence,
            confirmation_lookup=self._frozen_confirmation_for_evidence,
            output_root=self.evidence_artifact_root,
        )
        self.assisted_authoring_service.bind_decision_lookup(
            self.proposal_review_service.source_has_decision
        )
        self.assisted_authoring_service.bind_review_commit_guard(
            self.proposal_review_service.authoring_commit_guard
        )
        self.assisted_authoring_service.bind_source_commit_guard(
            self.poc_source_intake.authoring_commit_guard
        )
        self.assisted_authoring_service.bind_draft_commit_guard(
            self.draft_poc_service.authoring_commit_guard
        )
        self.static_root = Path(static_root).resolve()
        if not self.static_root.is_dir():
            raise RuntimeError("ExitSpec static demo assets are unavailable.")
        super().__init__(address, SourceNeutralPOCDemoRequestHandler)

    def server_close(self) -> None:
        try:
            super().server_close()
        finally:
            if self._owned_evidence_artifact_root is not None:
                self._owned_evidence_artifact_root.cleanup()
                self._owned_evidence_artifact_root = None

    def _frozen_contract_for_evidence(self, poc_id: str):
        snapshot = self.agreement_service.snapshot(poc_id)
        if snapshot.frozen_contract is None:
            raise KeyError("The POC does not have an exact frozen contract.")
        return snapshot.frozen_contract

    def _frozen_confirmation_for_evidence(self, poc_id: str):
        snapshot = self.agreement_service.snapshot(poc_id)
        if snapshot.frozen_contract is None or snapshot.confirmation is None:
            raise KeyError("The POC does not have an exact frozen confirmation.")
        return snapshot.confirmation

    def _proposal_inputs_for_review(self, poc_id: str):
        assisted = self.assisted_authoring_service.proposal_inputs(poc_id)
        a2 = self.poc_source_intake.proposal_inputs(poc_id)
        assisted_by_source: dict[str, tuple[Any, ...]] = {}
        for proposal in assisted:
            assisted_by_source.setdefault(proposal.source_receipt_id, tuple())
            assisted_by_source[proposal.source_receipt_id] = (
                *assisted_by_source[proposal.source_receipt_id],
                proposal,
            )
        merged = []
        replaced_sources: set[str] = set()
        for proposal in a2:
            replacement = assisted_by_source.get(proposal.source_receipt_id)
            if replacement is None:
                merged.append(proposal)
                continue
            if proposal.source_receipt_id not in replaced_sources:
                merged.extend(replacement)
                replaced_sources.add(proposal.source_receipt_id)
        for source_receipt_id, replacement in assisted_by_source.items():
            if source_receipt_id not in replaced_sources:
                merged.extend(replacement)
        return tuple(merged)

    def _retained_proposals_for_planning(self, poc_id: str):
        return self.assisted_authoring_service.retained_projection(
            poc_id,
            self.proposal_review_service,
        )

    def workspace_payload(self, selected_filter: str = "Active") -> dict[str, Any]:
        receipts: dict[str, tuple[Any, ...]] = {}
        current_counts: dict[str, int] = {}
        pending: dict[str, int] = {}
        kept: dict[str, int] = {}
        for draft in self.draft_poc_service.snapshots():
            if draft.archive_state.value == "ACTIVE":
                receipts[draft.poc_id] = self.poc_source_intake.list_receipts(
                    draft.poc_id
                )
                items = self.proposal_review_service.list_proposals(draft.poc_id)
                pending[draft.poc_id] = sum(
                    item.review_state.value == "NEEDS_REVIEW" for item in items
                )
                kept[draft.poc_id] = sum(
                    item.review_state.value == "KEEP_FOR_CONTRACT" for item in items
                )
                current_counts[draft.poc_id] = len(items)
            else:
                receipts[draft.poc_id] = ()
                pending[draft.poc_id] = 0
                kept[draft.poc_id] = 0
        return project_draft_dashboard(
            self.draft_poc_service.snapshots(),
            receipts,
            current_proposal_counts_by_poc_id=current_counts,
            pending_proposal_counts_by_poc_id=pending,
            kept_proposal_counts_by_poc_id=kept,
            selected_filter=selected_filter,
        ).model_dump(mode="json")


class SourceNeutralPOCDemoRequestHandler(BaseHTTPRequestHandler):
    server: SourceNeutralPOCDemoServer

    def do_GET(self) -> None:  # noqa: N802 - stdlib request handler API
        parsed = urlparse(self.path)
        if parsed.path != "/api/workspace" and (
            parsed.params or parsed.query or parsed.fragment
        ):
            self._json(HTTPStatus.BAD_REQUEST, {"error": "Route parameters are not accepted."})
            return
        if parsed.path == "/api/workspace":
            try:
                filter_value = self._workspace_filter(parsed.query)
                self._json(HTTPStatus.OK, self.server.workspace_payload(filter_value))
            except ValueError:
                self._json(HTTPStatus.BAD_REQUEST, {"error": "Workspace filter is invalid."})
            return
        if parsed.path == "/api/state":
            self._json(
                HTTPStatus.OK,
                {
                    "mode": "local_source_neutral",
                    "safety": {
                        "source_authority": "UNTRUSTED_SOURCE_ONLY",
                        "may_approve": False,
                        "may_confirm": False,
                        "may_freeze": False,
                        "may_execute": False,
                        "may_issue_evidence": False,
                        "may_issue_verdict": False,
                    },
                },
            )
            return
        draft_id = self._match_id(_DRAFT_API_RE, parsed.path)
        if draft_id is not None:
            self._send_draft(draft_id)
            return
        if is_poc_evidence_web_api_target(parsed.path):
            response = handle_poc_evidence_web_api_request(
                method="GET",
                target=parsed.path,
                payload=None,
                runtime=self.server.generic_evidence_service,
            )
            if response is not None:
                self._json(response.status, response.payload)
                return
        if is_poc_assisted_authoring_web_api_target(parsed.path):
            response = handle_poc_assisted_authoring_web_api_request(
                method="GET",
                target=parsed.path,
                payload=None,
                runtime=self.server.assisted_authoring_service,
                review_runtime=self.server.proposal_review_service,
                source_runtime=self.server.poc_source_intake,
            )
            if response is not None:
                self._json(response.status, response.payload)
                return
        if is_poc_capability_planner_web_api_target(parsed.path):
            response = handle_poc_capability_planner_web_api_request(
                method="GET",
                target=parsed.path,
                payload=None,
                runtime=self.server.capability_planner_service,
            )
            if response is not None:
                self._json(response.status, response.payload)
                return
        if is_poc_agreement_web_api_target(parsed.path):
            response = handle_poc_agreement_web_api_request(
                method="GET",
                target=parsed.path,
                payload=None,
                runtime=self.server.agreement_service,
            )
            if response is not None:
                self._json(response.status, response.payload)
                return
        if is_customer_review_web_api_target(parsed.path):
            response = handle_customer_review_web_api_request(
                method="GET",
                target=parsed.path,
                payload=None,
                runtime=self.server.agreement_service,
            )
            if response is not None:
                self._json(response.status, response.payload)
                return
        if is_poc_source_web_api_target(parsed.path):
            response = handle_poc_source_web_api_request(
                method="GET",
                target=parsed.path,
                payload=None,
                runtime=self.server.poc_source_intake,
            )
            if response is not None:
                self._json(response.status, response.payload)
                return
        if self._matches(_PROPOSAL_API_RE, parsed.path):
            response = handle_poc_proposal_web_api_request(
                method="GET",
                target=parsed.path,
                payload=None,
                runtime=self.server.proposal_review_service,
            )
            if response is not None:
                self._json(response.status, response.payload)
                return
        if parsed.path.startswith("/artifacts/"):
            self._serve_artifact(parsed.path)
            return
        if parsed.path in {"", "/", "/app", "/app/"}:
            # A7 canonical entry: source choice is the first fresh-flow task.
            # The seeded runtime retains its own compatibility dashboard.
            self._file("new_poc.html")
            return
        if parsed.path == "/app/pocs/new":
            self._file("new_poc.html")
            return
        evidence_page_poc_id = _generic_evidence_page_poc_id(parsed.path)
        if evidence_page_poc_id is not None:
            if self._active_draft(evidence_page_poc_id):
                self._file("generic_evidence.html")
            else:
                self._json(
                    HTTPStatus.NOT_FOUND,
                    {"error": "Draft POC was not found in this local process."},
                )
            return
        if (
            _SOURCE_PAGE_RE.fullmatch(parsed.path)
            or _REVIEW_PAGE_RE.fullmatch(parsed.path)
            or _ASSISTED_PAGE_RE.fullmatch(parsed.path)
            or _PLANNING_PAGE_RE.fullmatch(parsed.path)
            or _AGREEMENT_PAGE_RE.fullmatch(parsed.path)
        ):
            poc_id = parsed.path.split("/")[3]
            if self._active_draft(poc_id):
                if _SOURCE_PAGE_RE.fullmatch(parsed.path):
                    asset = "source_intake.html"
                elif _REVIEW_PAGE_RE.fullmatch(parsed.path):
                    asset = "proposal_review.html"
                elif _ASSISTED_PAGE_RE.fullmatch(parsed.path):
                    asset = "assisted_authoring.html"
                elif _AGREEMENT_PAGE_RE.fullmatch(parsed.path):
                    asset = "agreement_dynamic.html"
                else:
                    asset = "capability_plan.html"
                self._file(asset)
            else:
                self._json(HTTPStatus.NOT_FOUND, {"error": "Draft POC was not found in this local process."})
            return
        if _CUSTOMER_REVIEW_PAGE_RE.fullmatch(parsed.path):
            self._file("customer_review_dynamic.html")
            return
        asset = parsed.path.removeprefix("/")
        if asset in _ASSET_NAMES:
            self._file(asset)
            return
        self._json(HTTPStatus.NOT_FOUND, {"error": "Page not found."})

    def do_POST(self) -> None:  # noqa: N802 - stdlib request handler API
        parsed = urlparse(self.path)
        if parsed.params or parsed.query or parsed.fragment:
            self._json(HTTPStatus.BAD_REQUEST, {"error": "Route parameters are not accepted."})
            return
        if not self._json_request_allowed():
            return
        try:
            payload = self._read_json()
        except OverflowError:
            self._json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "Request is too large."})
            return
        except ValueError:
            self._json(HTTPStatus.BAD_REQUEST, {"error": "Request is invalid."})
            return
        convergence_match = _CONVERGENCE_PLAN_API_RE.fullmatch(parsed.path)
        if convergence_match is not None:
            self._converge_plan(convergence_match.group(1), payload)
            return
        if is_poc_evidence_web_api_target(parsed.path):
            response = handle_poc_evidence_web_api_request(
                method="POST",
                target=parsed.path,
                payload=payload,
                runtime=self.server.generic_evidence_service,
            )
            if response is not None:
                self._json(response.status, response.payload)
                return
        if parsed.path == "/api/pocs":
            self._create(payload)
            return
        if is_poc_assisted_authoring_web_api_target(parsed.path):
            response = handle_poc_assisted_authoring_web_api_request(
                method="POST",
                target=parsed.path,
                payload=payload,
                runtime=self.server.assisted_authoring_service,
                review_runtime=self.server.proposal_review_service,
                source_runtime=self.server.poc_source_intake,
            )
            if response is not None:
                self._json(response.status, response.payload)
                return
        if is_poc_capability_planner_web_api_target(parsed.path):
            response = handle_poc_capability_planner_web_api_request(
                method="POST",
                target=parsed.path,
                payload=payload,
                runtime=self.server.capability_planner_service,
            )
            if response is not None:
                self._json(response.status, response.payload)
                return
        if is_poc_agreement_web_api_target(parsed.path):
            response = handle_poc_agreement_web_api_request(
                method="POST",
                target=parsed.path,
                payload=payload,
                runtime=self.server.agreement_service,
            )
            if response is not None:
                self._json(response.status, response.payload)
                return
        if is_customer_review_web_api_target(parsed.path):
            response = handle_customer_review_web_api_request(
                method="POST",
                target=parsed.path,
                payload=payload,
                runtime=self.server.agreement_service,
            )
            if response is not None:
                self._json(response.status, response.payload)
                return
        source_match = _SOURCE_API_RE.fullmatch(parsed.path)
        if source_match is not None and source_match.group(2) is not None:
            self._capture(source_match.group(1), unquote(source_match.group(2)), payload)
            return
        proposal_match = _PROPOSAL_API_RE.fullmatch(parsed.path)
        if proposal_match is not None and proposal_match.group(2) is not None:
            response = handle_poc_proposal_web_api_request(
                method="POST",
                target=parsed.path,
                payload=payload,
                runtime=self.server.proposal_review_service,
            )
            if response is not None:
                self._json(response.status, response.payload)
                return
        self._json(HTTPStatus.NOT_FOUND, {"error": "Route was not found."})

    def _converge_plan(self, poc_id: str, payload: Any) -> None:
        """Expand bounded human planning input from the server-owned A4 registry."""

        try:
            self._exact_fields(payload, {"items", "idempotency_key"})
            idempotency_key = self._required_string(payload, "idempotency_key")
            raw_items = payload["items"]
            if type(raw_items) is not list or not raw_items:
                raise ValueError
            registry = {
                entry.capability_key: entry
                for entry in self.server.capability_planner_service.registry
            }
            items = []
            allowed = {
                "proposal_id",
                "scope",
                "capability_key",
                "operator",
                "threshold",
                "reviewer",
                "rationale",
                "explicit_exclusion",
            }
            for raw in raw_items:
                self._exact_fields(raw, allowed)
                capability_key = self._required_string(raw, "capability_key")
                excluded = raw["explicit_exclusion"]
                if type(excluded) is not bool:
                    raise ValueError
                criterion = None
                if not excluded and capability_key in registry:
                    entry = registry[capability_key]
                    operator = self._required_string(raw, "operator")
                    threshold = raw["threshold"]
                    if operator not in entry.allowed_operators or isinstance(threshold, bool):
                        raise ValueError
                    criterion = PlannerCriterionInput(
                        rule=entry.rule,
                        operator=operator,
                        threshold=threshold,
                        unit=entry.unit,
                        measurement_population=entry.measurement_population,
                        evidence_method=entry.evidence_method,
                        adapter=entry.adapter,
                        adapter_version=entry.adapter_version,
                        evidence_profile=entry.evidence_profile,
                        provenance=PlanningProvenance.SOURCE_EXTRACTED,
                    )
                elif not excluded and capability_key != "unsupported_capability":
                    raise ValueError
                items.append(
                    PlannerItemInput(
                        proposal_id=self._required_string(raw, "proposal_id"),
                        scope=self._required_string(raw, "scope"),
                        capability_key=capability_key,
                        criterion=criterion,
                        reviewer=self._required_string(raw, "reviewer"),
                        rationale=self._required_string(raw, "rationale"),
                        explicit_exclusion=excluded,
                    ).model_dump(mode="json")
                )
        except (KeyError, TypeError, ValueError, ValidationError):
            self._json(HTTPStatus.BAD_REQUEST, {"error": "Convergence planning request is invalid."})
            return
        response = handle_poc_capability_planner_web_api_request(
            method="POST",
            target=f"/api/pocs/{poc_id}/capability-plan",
            payload={"items": items, "idempotency_key": idempotency_key},
            runtime=self.server.capability_planner_service,
        )
        if response is None:  # pragma: no cover - exact internal route
            self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "Capability planning is unavailable."})
            return
        self._json(response.status, response.payload)

    def do_PUT(self) -> None:  # noqa: N802 - stdlib request handler API
        if is_poc_capability_planner_web_api_target(urlparse(self.path).path):
            response = handle_poc_capability_planner_web_api_request(
                method="PUT",
                target=self.path,
                payload=None,
                runtime=self.server.capability_planner_service,
            )
            if response is not None:
                self._json(response.status, response.payload)
                return
        self.send_error(HTTPStatus.NOT_IMPLEMENTED)

    def _create(self, payload: Any) -> None:
        allowed = {
            "display_name",
            "customer_label",
            "use_case",
            "owner",
            "first_source_choice",
            "idempotency_key",
        }
        try:
            self._exact_fields(payload, allowed)
            idempotency_key = self._required_string(payload, "idempotency_key")
            request = DraftPOCCreateRequest.model_validate(
                {key: value for key, value in payload.items() if key != "idempotency_key"}
            )
            result = self.server.draft_poc_service.create(
                request,
                idempotency_key=idempotency_key,
            )
        except DraftPOCIdempotencyConflict:
            self._json(HTTPStatus.CONFLICT, {"error": "Draft POC create conflicts with an earlier request."})
            return
        except DuplicateDraftPOCId:
            self._json(HTTPStatus.CONFLICT, {"error": "Draft POC is unavailable."})
            return
        except DraftPOCCapacityExceeded:
            self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "Draft POC is unavailable."})
            return
        except (TypeError, ValueError, ValidationError):
            self._json(HTTPStatus.BAD_REQUEST, {"error": "Draft POC request is invalid."})
            return
        response = result.draft.model_dump(mode="json")
        response["idempotent_replay"] = result.idempotent_replay
        self._json(HTTPStatus.OK if result.idempotent_replay else HTTPStatus.CREATED, response)

    def _capture(self, poc_id: str, route: str, payload: Any) -> None:
        route_spec = _SOURCE_ROUTES.get(route)
        if route_spec is None:
            self._json(HTTPStatus.NOT_FOUND, {"error": "Source route was not found."})
            return
        source_kind, content_field = route_spec
        try:
            self._exact_fields(payload, {content_field, "idempotency_key"})
            idempotency_key = self._required_string(payload, "idempotency_key")
            source = POCSourceInput(
                source_kind=source_kind,
                content=payload[content_field],
            )
            receipt = self.server.poc_source_intake.capture_source(
                poc_id=poc_id,
                source=source,
                idempotency_key=idempotency_key,
            )
        except POCSourceDraftUnavailable:
            self._json(HTTPStatus.NOT_FOUND, {"error": "Draft POC was not found in this local process."})
            return
        except (
            POCSourceDraftArchived,
            POCSourceIdempotencyConflict,
            POCSourceRevisionRequired,
            POCSourceStaleRevision,
            POCSourceIntakeRevisionRequired,
        ):
            self._json(HTTPStatus.CONFLICT, {"error": "Source request conflicts with the current draft."})
            return
        except (POCSourceIntakeInvalid, POCSourceIntakeError, ValueError, TypeError, ValidationError):
            self._json(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": "The source input was not accepted."})
            return
        except (POCSourceCapacityExceeded, POCSourceIntakeCapacityExceeded, DuplicatePOCSourceId):
            self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "Source intake is temporarily unavailable."})
            return
        self._json(HTTPStatus.OK if receipt.idempotent_replay else HTTPStatus.CREATED, receipt.model_dump(mode="json"))

    def _send_draft(self, poc_id: str) -> None:
        try:
            draft = self.server.draft_poc_service.get(poc_id)
        except ValueError:
            self._json(HTTPStatus.BAD_REQUEST, {"error": "Draft POC request is invalid."})
            return
        except DraftPOCNotFound:
            self._json(HTTPStatus.NOT_FOUND, {"error": "Draft POC was not found in this local process."})
            return
        self._json(HTTPStatus.OK, draft.model_dump(mode="json"))

    def _serve_artifact(self, request_path: str) -> None:
        match = _EVIDENCE_ARTIFACT_RE.fullmatch(request_path)
        if match is None:
            self._json(HTTPStatus.NOT_FOUND, {"error": "Artifact not found."})
            return
        attempt_id = match.group(1)
        root = self.server.evidence_artifact_root
        if root.is_symlink() or not root.is_dir():
            self._json(HTTPStatus.NOT_FOUND, {"error": "Artifact not found."})
            return
        target = root / attempt_id / "decision-packet.html"
        if target.is_symlink() or not target.is_file():
            self._json(HTTPStatus.NOT_FOUND, {"error": "Artifact not found."})
            return
        try:
            publication = self.server.generic_evidence_service.verify_evidence_pack_publication(
                attempt_id
            )
            stat = target.lstat()
            if stat.st_size > 4 * 1024 * 1024:
                raise OSError("artifact is too large")
            with target.open("rb") as handle:
                data = handle.read(4 * 1024 * 1024 + 1)
            if (
                len(data) != stat.st_size
                or len(data) > 4 * 1024 * 1024
                or hashlib.sha256(data).hexdigest()
                != publication.decision_packet_sha256
            ):
                raise OSError("artifact changed while it was read")
        except (GenericEvidencePackError, OSError, ValueError, KeyError):
            self._json(HTTPStatus.NOT_FOUND, {"error": "Artifact not found."})
            return
        self.send_response(HTTPStatus.OK)
        self.send_header(
            "Content-Type",
            mimetypes.guess_type(str(target))[0] or "application/octet-stream",
        )
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _active_draft(self, poc_id: str) -> bool:
        try:
            return self.server.draft_poc_service.get(poc_id).archive_state.value == "ACTIVE"
        except (ValueError, DraftPOCNotFound):
            return False

    def _json_request_allowed(self) -> bool:
        if not self._has_json_media_type():
            self._json(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"error": "Content-Type must be application/json."})
            return False
        if not self._has_exact_loopback_origin():
            self._json(HTTPStatus.FORBIDDEN, {"error": "Origin is not allowed."})
            return False
        return True

    def _has_json_media_type(self) -> bool:
        values = self.headers.get_all("Content-Type") or []
        if len(values) != 1:
            return False
        media_type, separator, parameter = values[0].partition(";")
        if media_type.strip().lower() != "application/json":
            return False
        if not separator:
            return True
        name, equals, value = parameter.partition("=")
        if name.strip().lower() != "charset" or not equals:
            return False
        charset = value.strip()
        if charset.startswith('"') and charset.endswith('"') and len(charset) >= 2:
            charset = charset[1:-1]
        return bool(charset) and all(
            character.isascii()
            and (character.isalnum() or character in "!#$%&'*+-.^_`|~")
            for character in charset
        )

    def _has_exact_loopback_origin(self) -> bool:
        origins = self.headers.get_all("Origin") or []
        hosts = self.headers.get_all("Host") or []
        if len(origins) != 1 or len(hosts) != 1:
            return False
        origin = origins[0]
        host = hosts[0]
        if origin != origin.strip() or host != host.strip():
            return False
        try:
            parsed_origin = urlparse(origin)
            parsed_host = urlparse("http://" + host)
            origin_hostname = parsed_origin.hostname
            host_hostname = parsed_host.hostname
            origin_port = parsed_origin.port
            host_port = parsed_host.port
        except ValueError:
            return False
        loopback = {"127.0.0.1", "localhost", "::1"}
        if (
            parsed_origin.scheme.lower() != "http"
            or origin_hostname not in loopback
            or host_hostname not in loopback
            or parsed_origin.username is not None
            or parsed_origin.password is not None
            or parsed_host.username is not None
            or parsed_host.password is not None
            or parsed_origin.path
            or parsed_origin.params
            or parsed_origin.query
            or parsed_origin.fragment
            or parsed_host.path
            or parsed_host.params
            or parsed_host.query
            or parsed_host.fragment
        ):
            return False
        normalized_origin_host = "[::1]" if origin_hostname == "::1" else origin_hostname
        normalized_request_host = "[::1]" if host_hostname == "::1" else host_hostname
        expected_port = self.server.server_port
        origin_authority = (
            normalized_origin_host
            if origin_port is None
            else "{0}:{1}".format(normalized_origin_host, origin_port)
        )
        request_authority = (
            normalized_request_host
            if host_port is None
            else "{0}:{1}".format(normalized_request_host, host_port)
        )
        return (
            parsed_origin.netloc.lower() == origin_authority.lower()
            and parsed_host.netloc.lower() == request_authority.lower()
            and (80 if origin_port is None else origin_port) == expected_port
            and (80 if host_port is None else host_port) == expected_port
            and parsed_origin.netloc.lower() == parsed_host.netloc.lower()
        )

    def _read_json(self) -> dict[str, Any]:
        lengths = self.headers.get_all("Content-Length") or []
        if len(lengths) != 1:
            raise ValueError("Content length is required.")
        raw_length = lengths[0]
        try:
            length = int(raw_length)
        except ValueError as error:
            raise ValueError("Content length is invalid.") from error
        if length < 0 or length > MAX_REQUEST_BYTES:
            raise OverflowError("Request is too large.")
        body = self.rfile.read(length)
        if len(body) != length:
            raise ValueError("Request body is incomplete.")

        def reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("Duplicate JSON object key.")
                result[key] = value
            return result

        try:
            payload = json.loads(
                body.decode("utf-8"),
                object_pairs_hook=reject_duplicate_pairs,
                parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
            )
        except (RecursionError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
            raise ValueError("Request body must be valid JSON.") from error
        nodes = 0

        def validate_value(value: Any, depth: int) -> None:
            nonlocal nodes
            nodes += 1
            if nodes > MAX_REQUEST_JSON_NODES or depth > MAX_REQUEST_JSON_DEPTH:
                raise ValueError("Request JSON exceeds its supported bounds.")
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError("Request JSON contains a non-finite number.")
            if isinstance(value, dict):
                for key, child in value.items():
                    if type(key) is not str:
                        raise ValueError("Request JSON object keys must be text.")
                    validate_value(child, depth + 1)
            elif isinstance(value, list):
                for child in value:
                    validate_value(child, depth + 1)

        try:
            validate_value(payload, 0)
        except (RecursionError, ValueError) as error:
            raise ValueError("Request JSON exceeds its supported bounds.") from error
        if type(payload) is not dict:
            raise ValueError("Request body must be an object.")
        return payload

    @staticmethod
    def _exact_fields(payload: Any, allowed: set[str]) -> None:
        if type(payload) is not dict or set(payload) != allowed:
            raise ValueError("Request contains unsupported fields.")

    @staticmethod
    def _required_string(payload: Mapping[str, Any], key: str) -> str:
        value = payload.get(key)
        if type(value) is not str or not value.strip() or len(value) > 200:
            raise ValueError("Request string is invalid.")
        return value.strip()

    @staticmethod
    def _workspace_filter(query: str) -> str:
        if not query:
            return "Active"
        fields = parse_qsl(query, keep_blank_values=True, strict_parsing=True)
        if len(fields) != 1 or fields[0][0] != "filter" or fields[0][1] not in {
            "Active",
            "Needs attention",
            "Completed",
        }:
            raise ValueError("Workspace filter is invalid.")
        return fields[0][1]

    @staticmethod
    def _match_id(pattern: re.Pattern[str], path: str) -> str | None:
        match = pattern.fullmatch(path)
        return None if match is None else match.group(1)

    @staticmethod
    def _matches(pattern: re.Pattern[str], path: str) -> bool:
        return pattern.fullmatch(path) is not None

    def _file(self, relative: str) -> None:
        target = (self.server.static_root / relative).resolve()
        try:
            target.relative_to(self.server.static_root)
        except ValueError:
            self._json(HTTPStatus.NOT_FOUND, {"error": "Page not found."})
            return
        if not target.is_file():
            self._json(HTTPStatus.NOT_FOUND, {"error": "Page not found."})
            return
        data = target.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mimetypes.guess_type(str(target))[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: Any) -> None:
        return


def _generic_evidence_page_poc_id(request_path: str) -> str | None:
    """Return the POC identity only for the exact A6 evidence page route."""

    parts = request_path.strip("/").split("/")
    if len(parts) != 4 or parts[:2] != ["app", "pocs"] or parts[3] != "evidence":
        return None
    poc_id = unquote(parts[2])
    return poc_id if _POC_ID_RE.fullmatch(poc_id) is not None else None


def serve_source_neutral_demo(
    host: str = "127.0.0.1",
    port: int = 8765,
    *,
    open_browser: bool = False,
    evidence_artifact_root: Path | None = None,
) -> SourceNeutralPOCDemoServer:
    """Construct the local A2/A3 browser runtime; caller owns its serve loop."""

    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("ExitSpec demo only binds to a loopback address.")
    server = SourceNeutralPOCDemoServer(
        (host, port),
        evidence_artifact_root=evidence_artifact_root,
    )
    if open_browser:
        threading.Timer(
            0.15,
            lambda: webbrowser.open(
                "http://{0}:{1}/app".format(host, server.server_port)
            ),
        ).start()
    return server


__all__ = [
    "MAX_REQUEST_BYTES",
    "MAX_REQUEST_JSON_DEPTH",
    "MAX_REQUEST_JSON_NODES",
    "SourceNeutralPOCDemoServer",
    "serve_source_neutral_demo",
]
