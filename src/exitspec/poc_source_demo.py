"""Source-neutral, process-local browser runtime for Train A A2.

This runtime deliberately owns only draft identity, source attachment, and
human proposal projection. It does not construct the seeded session, load
seeded fixtures, or expose agreement, proof, provider, or lifecycle routes.
The existing compatibility demo remains in :mod:`exitspec.web`.
"""

from __future__ import annotations

import json
import mimetypes
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import re
import threading
from typing import Any, Mapping
from urllib.parse import parse_qsl, unquote, urlparse
import webbrowser

from pydantic import ValidationError

from .draft_workspace import project_draft_dashboard
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
from .poc_proposal_web_api import handle_poc_proposal_web_api_request
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


STATIC_ROOT = Path(__file__).resolve().parent / "static"
MAX_REQUEST_BYTES = 128 * 1024
POC_ID_PATTERN = r"^poc_[a-z0-9][a-z0-9_-]{2,63}$"
_POC_ID_RE = re.compile(POC_ID_PATTERN)
_SOURCE_PAGE_RE = re.compile(
    r"^/app/pocs/(poc_[a-z0-9][a-z0-9_-]{2,63})/sources/new$"
)
_REVIEW_PAGE_RE = re.compile(
    r"^/app/pocs/(poc_[a-z0-9][a-z0-9_-]{2,63})/review$"
)
_DRAFT_API_RE = re.compile(r"^/api/pocs/(poc_[a-z0-9][a-z0-9_-]{2,63})$")
_SOURCE_API_RE = re.compile(
    r"^/api/pocs/(poc_[a-z0-9][a-z0-9_-]{2,63})/sources(?:/([^/]+))?$"
)
_PROPOSAL_API_RE = re.compile(
    r"^/api/pocs/(poc_[a-z0-9][a-z0-9_-]{2,63})/proposals(?:/([^/]+)/decision)?$"
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
    ) -> None:
        self.draft_poc_service = ProcessLocalDraftPOCService()
        self.poc_source_intake = ProcessLocalPOCSourceIntake(
            draft_lookup=self.draft_poc_service.get,
        )
        self.proposal_review_service = ProcessLocalProposalReviewService(
            proposal_lookup=self.poc_source_intake.proposal_inputs,
        )
        self.static_root = Path(static_root).resolve()
        if not self.static_root.is_dir():
            raise RuntimeError("ExitSpec static demo assets are unavailable.")
        super().__init__(address, SourceNeutralPOCDemoRequestHandler)

    def workspace_payload(self, selected_filter: str = "Active") -> dict[str, Any]:
        receipts: dict[str, tuple[Any, ...]] = {}
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
            else:
                receipts[draft.poc_id] = ()
                pending[draft.poc_id] = 0
                kept[draft.poc_id] = 0
        return project_draft_dashboard(
            self.draft_poc_service.snapshots(),
            receipts,
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
        if parsed.path in {"", "/", "/app", "/app/"}:
            self._file("dashboard.html")
            return
        if parsed.path == "/app/pocs/new":
            self._file("new_poc.html")
            return
        if _SOURCE_PAGE_RE.fullmatch(parsed.path) or _REVIEW_PAGE_RE.fullmatch(parsed.path):
            poc_id = parsed.path.split("/")[3]
            if self._active_draft(poc_id):
                self._file(
                    "source_intake.html"
                    if _SOURCE_PAGE_RE.fullmatch(parsed.path)
                    else "proposal_review.html"
                )
            else:
                self._json(HTTPStatus.NOT_FOUND, {"error": "Draft POC was not found in this local process."})
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
        if parsed.path == "/api/pocs":
            self._create(payload)
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
        except (
            POCSourceDraftArchived,
            POCSourceDraftUnavailable,
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

    def _active_draft(self, poc_id: str) -> bool:
        try:
            return self.server.draft_poc_service.get(poc_id).archive_state.value == "ACTIVE"
        except (ValueError, DraftPOCNotFound):
            return False

    def _json_request_allowed(self) -> bool:
        media_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if media_type != "application/json":
            self._json(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"error": "Content-Type must be application/json."})
            return False
        origin = self.headers.get("Origin")
        expected = {
            "http://127.0.0.1:{0}".format(self.server.server_port),
            "http://localhost:{0}".format(self.server.server_port),
        }
        if origin not in expected:
            self._json(HTTPStatus.FORBIDDEN, {"error": "Origin is not allowed."})
            return False
        return True

    def _read_json(self) -> dict[str, Any]:
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            raise ValueError("Content length is required.")
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
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
            raise ValueError("Request body must be valid JSON.") from error
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


def serve_source_neutral_demo(
    host: str = "127.0.0.1",
    port: int = 8765,
    *,
    open_browser: bool = False,
) -> SourceNeutralPOCDemoServer:
    """Construct the A2 browser runtime; the caller owns its serve loop."""

    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("ExitSpec demo only binds to a loopback address.")
    server = SourceNeutralPOCDemoServer((host, port))
    if open_browser:
        threading.Timer(
            0.15,
            lambda: webbrowser.open(
                "http://{0}:{1}/app/pocs/new".format(host, server.server_port)
            ),
        ).start()
    return server


__all__ = [
    "MAX_REQUEST_BYTES",
    "SourceNeutralPOCDemoServer",
    "serve_source_neutral_demo",
]
