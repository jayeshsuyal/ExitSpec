"""Same-origin, process-local UI adapter for synthetic source authoring only."""

from __future__ import annotations

import hmac
import json
import math
import re
import secrets
from dataclasses import dataclass, field
from http import HTTPStatus
from threading import RLock, Thread
from time import monotonic
from urllib.parse import urlparse

from .assisted_authoring import ASSISTED_AUTHORING_SCHEMA_VERSION
from .source_authoring_operations import (
    AuthorizedSourceAuthoringRequest,
    ProcessLocalSourceAuthoringOperations,
    SourceAuthoringDisclosure,
    SourceAuthoringOperationError,
    SyntheticBrowserSession,
)
from .source_authoring_owners import SourceAuthoringOwners, SourceAuthoringOwnersError
from .source_authoring_policy import MODEL, SourceAuthoringPolicyError, build_body
from .source_authoring_supervisor import SyntheticSourceAuthoringSupervisor

CAPABILITY_HEADER = "X-ExitSpec-Authoring-Capability"
MAX_REQUEST_BYTES = 4096
MAX_RESPONSE_BYTES = 262144
MODE = "SYNTHETIC_NO_NETWORK"
CLASSIFICATION = "OWNER_APPROVED_REDACTED_BUSINESS_TEXT"
_POC = r"poc_[a-z0-9][a-z0-9_-]{2,63}"
_API = re.compile(rf"/api/pocs/({_POC})/source-authoring/([a-z-]+)")
_PAGE = re.compile(rf"/app/pocs/({_POC})/source-authoring")
_HEX = re.compile(r"[a-f0-9]{64}")
_RECEIPT = re.compile(r"srcpt_[a-z0-9][a-z0-9_-]{7,95}")
_TERMINAL = {"SUCCEEDED", "FAILED", "OUTCOME_UNKNOWN", "STALE", "EXPIRED", "REVOKED"}
_FIELDS = {
    "bootstrap": set(),
    "sources": set(),
    "prepare": {"source_receipt_id"},
    "preview": {"operation_id"},
    "status": {"operation_id"},
    "authorize": {"operation_id", "business_text", "acknowledged", "idempotency_key"},
    "run": {"operation_id"},
    "revoke": {"operation_id"},
}


class SourceAuthoringWebError(ValueError):
    def __init__(self, code="REQUEST_REFUSED", status=HTTPStatus.CONFLICT):
        self.code, self.status = code, status
        super().__init__("Source authoring request was refused.")


@dataclass(repr=False)
class _Operation:
    disclosure: SourceAuthoringDisclosure
    permit: AuthorizedSourceAuthoringRequest | None = None
    started: bool = False
    error: str | None = None


@dataclass(repr=False)
class _Browser:
    secret: bytes
    session: SyntheticBrowserSession
    poc_id: str
    operations: dict[str, _Operation] = field(default_factory=dict)


class SourceAuthoringWebRuntime:
    """One synthetic grant/worker/ledger shared by every POC and browser page.

    The wrapper stores handles and content-free metadata only. Source snapshots
    are obtained from the core at each preview/run and never retained here.
    """

    def __init__(self, *, drafts, intake, assisted, review, closure):
        self._drafts, self._intake, self._closure = drafts, intake, closure
        self._owners = SourceAuthoringOwners(
            source_intake=intake,
            drafts=drafts,
            assisted=assisted,
            review=review,
            run_if_open=closure.run_if_open,
        )
        self.operations = ProcessLocalSourceAuthoringOperations(owners=self._owners)
        self._lock = RLock()
        self._browsers: list[_Browser] = []
        self._running: str | None = None
        self._thread: Thread | None = None
        self._closed = False

    def __repr__(self):
        return "<SourceAuthoringWebRuntime synthetic-only>"

    def close(self):
        with self._lock:
            self._closed = True
            self.operations.shutdown()
            self._browsers.clear()
            thread = self._thread
        if thread is not None:
            thread.join(timeout=2)

    def _browser(self, capability, poc_id):
        if type(capability) is not str or _HEX.fullmatch(capability) is None:
            raise SourceAuthoringWebError("CAPABILITY_REFUSED", HTTPStatus.FORBIDDEN)
        secret = bytes.fromhex(capability)
        for browser in self._browsers:
            if hmac.compare_digest(browser.secret, secret) and browser.poc_id == poc_id:
                return browser
        raise SourceAuthoringWebError("CAPABILITY_REFUSED", HTTPStatus.FORBIDDEN)

    def _active_draft(self, poc_id):
        draft = self._drafts.get(poc_id)
        if draft.archive_state.value != "ACTIVE":
            raise SourceAuthoringWebError("SOURCE_UNAVAILABLE")
        self._closure.run_if_open(poc_id, lambda: None)
        return draft

    def request(self, poc_id, action, payload, capability=None):
        # The HTTP adapter validates transport and exact fields before this
        # mutation boundary. Repeat the shape check for direct library callers.
        if (
            action not in _FIELDS
            or type(payload) is not dict
            or set(payload) != _FIELDS[action]
        ):
            raise SourceAuthoringWebError("REQUEST_REFUSED", HTTPStatus.BAD_REQUEST)
        with self._lock:
            if self._closed:
                raise SourceAuthoringWebError("RUNTIME_CLOSED")
            if action == "bootstrap":
                if capability is not None:
                    raise SourceAuthoringWebError(
                        "REQUEST_REFUSED", HTTPStatus.BAD_REQUEST
                    )
                draft = self._active_draft(poc_id)
                secret = secrets.token_bytes(32)
                browser = _Browser(
                    secret, self.operations.new_synthetic_session(), poc_id
                )
                self._browsers.append(browser)
                return {
                    "schema_version": "exitspec.source-authoring-web/1",
                    "capability": secret.hex(),
                    "mode": MODE,
                    "poc_id": poc_id,
                    "display_name": draft.display_name,
                    "live_enabled": False,
                    "live_missing": [
                        "live_worker_and_operator_launcher",
                        "owner_launch_approval",
                        "model_schema_token_and_billing_proof",
                        "account_pricing_and_custody_approval",
                    ],
                }
            browser = self._browser(capability, poc_id)
            if action == "sources":
                self._active_draft(poc_id)
                rows = []
                receipts = self._intake.list_current_receipts(poc_id)
                if len(receipts) > 256:
                    raise SourceAuthoringWebError("SOURCE_LIST_CAPACITY")
                for receipt in receipts:
                    eligible = True
                    try:
                        snapshot = self._owners.capture(
                            poc_id, receipt.source_receipt_id
                        )
                        build_body(snapshot.source)
                    except Exception:  # noqa: BLE001 - owner failures are content-free
                        eligible = False
                    rows.append(
                        {
                            "source_receipt_id": receipt.source_receipt_id,
                            "source_kind": receipt.source_kind.value,
                            "eligible": eligible,
                        }
                    )
                return {"mode": MODE, "poc_id": poc_id, "sources": rows}
            if action == "prepare":
                source_id = payload["source_receipt_id"]
                if type(source_id) is not str or _RECEIPT.fullmatch(source_id) is None:
                    raise SourceAuthoringWebError(
                        "REQUEST_REFUSED", HTTPStatus.BAD_REQUEST
                    )
                disclosure = self.operations.prepare(browser.session, poc_id, source_id)
                entry = browser.operations.setdefault(
                    disclosure.operation_id, _Operation(disclosure)
                )
                return self._projection(browser, entry, preview=True)
            operation_id = payload["operation_id"]
            if type(operation_id) is not str or _HEX.fullmatch(operation_id) is None:
                raise SourceAuthoringWebError("REQUEST_REFUSED", HTTPStatus.BAD_REQUEST)
            entry = browser.operations.get(operation_id)
            if entry is None:
                raise SourceAuthoringWebError("OPERATION_REFUSED", HTTPStatus.FORBIDDEN)
            if action == "authorize":
                if (
                    payload["business_text"] is not True
                    or payload["acknowledged"] is not True
                ):
                    raise SourceAuthoringWebError(
                        "ACKNOWLEDGEMENT_REQUIRED", HTTPStatus.BAD_REQUEST
                    )
                key = payload["idempotency_key"]
                if type(key) is not str or not 1 <= len(key) <= 200 or not key.strip():
                    raise SourceAuthoringWebError(
                        "REQUEST_REFUSED", HTTPStatus.BAD_REQUEST
                    )
                entry.permit = self.operations.authorize(
                    browser.session,
                    entry.disclosure,
                    acknowledged=True,
                    idempotency_key=key,
                )
            elif action == "revoke":
                self.operations.revoke_disclosure(browser.session, entry.disclosure)
            elif action == "run":
                self._run(browser, entry)
            return self._projection(browser, entry, preview=action == "preview")

    def _projection(self, browser, entry, *, preview=False):
        receipt, snapshot, remaining = self.operations.inspect_disclosure(
            browser.session, entry.disclosure
        )
        claims, reserved = self.operations.ledger
        result = {
            "mode": MODE,
            "poc_id": browser.poc_id,
            "operation_id": receipt.operation_id,
            "state": receipt.state,
            "processing": self._running == receipt.operation_id,
            "attempts": receipt.attempts,
            "reserved_usd": str(receipt.reserved_usd),
            "grant_claims": claims,
            "grant_reserved_usd": str(reserved),
            "expires_in_seconds": max(0, math.ceil(remaining)),
            "code": entry.error or receipt.code,
            "authoring_receipt_id": receipt.authoring_receipt_id,
        }
        if preview and snapshot is not None:
            source = snapshot.source
            result["disclosure"] = {
                "disclosure_sha256": entry.disclosure.disclosure_sha256,
                "source_receipt_id": snapshot.source_receipt_id,
                "source_kind": source.kind.value,
                "source_revision": source.source_revision,
                "source_sha256": source.content_sha256,
                "redacted_text": source.redacted_text,
                "classification": CLASSIFICATION,
                "provider": "fireworks",
                "model": MODEL,
                "purpose": "Draft proposals for human review from this exact redacted source.",
                "custody": "This synthetic run stays on this computer. The future global provider profile has no regional guarantee; external data handling approval is still required.",
                "limits": {
                    "source_bytes": 16384,
                    "body_bytes": 65536,
                    "response_bytes": 262144,
                    "output_tokens": 2000,
                    "deadline_seconds": 30,
                    "consent_seconds": 300,
                    "attempts": 1,
                    "claim_interval_seconds": 10,
                    "grant_claims": 10,
                    "reservation_usd": "0.01",
                    "grant_reservation_usd": "0.10",
                },
            }
        return result

    def _run(self, browser, entry):
        receipt, snapshot, _ = self.operations.inspect_disclosure(
            browser.session, entry.disclosure
        )
        if receipt.state in _TERMINAL or entry.started:
            return
        if receipt.state != "AUTHORIZED" or entry.permit is None:
            raise SourceAuthoringWebError("ACKNOWLEDGEMENT_REQUIRED")
        if self._running is not None:
            raise SourceAuthoringWebError("WORKER_BUSY", HTTPStatus.TOO_MANY_REQUESTS)
        # The fixed local fixture copies existing source-owner requirements. It
        # never invokes an executor/provider and is validated again after IPC.
        response = json.dumps(
            {
                "schema_version": ASSISTED_AUTHORING_SCHEMA_VERSION,
                "proposals": [
                    {
                        "proposal_key": f"synthetic-{index}",
                        "source_quote": candidate.source_quote,
                        "normalized_claim": candidate.normalized_claim,
                        "numeric_facts": None,
                    }
                    for index, candidate in enumerate(snapshot.source.candidates)
                ],
            },
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        worker = SyntheticSourceAuthoringSupervisor(synthetic_response=response)
        operation_id = entry.disclosure.operation_id

        def execute():
            try:
                self.operations.execute_synthetic(
                    browser.session, entry.permit, worker=worker
                )
            except Exception as error:  # noqa: BLE001 - never expose private execution details
                with self._lock:
                    entry.error = (
                        error.code
                        if type(error) is SourceAuthoringOperationError
                        and error.code
                        in {"rate_limited", "budget_exhausted", "grant_closed"}
                        else "execution_refused"
                    )
                    # Pre-claim refusal retains the same permit for an explicit
                    # later Run; there is no scheduled retry or second worker.
                    entry.started = False
            finally:
                with self._lock:
                    self._running = None

        thread = Thread(target=execute, name="exitspec-source-authoring", daemon=True)
        self._running, self._thread = operation_id, thread
        entry.started, entry.error = True, None
        try:
            thread.start()
        except Exception:  # noqa: BLE001 - no attempt has been consumed
            self._running, entry.started = None, False
            raise SourceAuthoringWebError("WORKER_UNAVAILABLE") from None


def review_inputs(intake, assisted, poc_id):
    """Use A3 replacements through the same existing human-review owner."""
    replacements = {}
    for proposal in assisted.proposal_inputs(poc_id):
        replacements.setdefault(proposal.source_receipt_id, []).append(proposal)
    merged, seen = [], set()
    for proposal in intake.proposal_inputs(poc_id):
        source = proposal.source_receipt_id
        if source not in replacements:
            merged.append(proposal)
        elif source not in seen:
            merged.extend(replacements[source])
            seen.add(source)
    for source, proposals in replacements.items():
        if source not in seen:
            merged.extend(proposals)
    return tuple(merged)


def source_authoring_page_poc(path):
    match = _PAGE.fullmatch(path)
    return match[1] if match else None


def _read_request_body(handler, size, deadline):
    body = bytearray()
    while len(body) < size:
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise TimeoutError()
        handler.connection.settimeout(remaining)
        # read1 consumes buffered bytes or performs one raw read. read(size)
        # can repeatedly renew an idle socket timeout while a body trickles in.
        chunk = handler.rfile.read1(size - len(body))
        if monotonic() >= deadline:
            raise TimeoutError()
        if not chunk:
            raise ValueError()
        body.extend(chunk)
    return bytes(body)


def handle_source_authoring_http(handler):
    """Validate every byte/header before bootstrapping or using UI authority.

    Reads use POST with an empty/exact JSON object so browser Origin is present.
    Bootstrap is the sole capability-free request; it only issues page access.
    """
    parsed = urlparse(handler.path)
    if "/source-authoring" not in parsed.path or not parsed.path.startswith("/api/"):
        return False
    handler.close_connection = True
    match = _API.fullmatch(handler.path)

    def reply(status, payload):
        data = json.dumps(
            payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        ).encode("utf-8")
        if len(data) > MAX_RESPONSE_BYTES:
            status, data = (
                HTTPStatus.SERVICE_UNAVAILABLE,
                b'{"code":"RESPONSE_LIMIT","error":"Source authoring response unavailable."}',
            )
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header("Content-Length", str(len(data)))
        handler.send_header("Cache-Control", "no-store")
        handler.send_header("Pragma", "no-cache")
        handler.send_header("Referrer-Policy", "no-referrer")
        handler.send_header("X-Content-Type-Options", "nosniff")
        handler.end_headers()
        if handler.command != "HEAD":
            handler.wfile.write(data)
        return True

    def refuse(code="REQUEST_REFUSED", status=HTTPStatus.BAD_REQUEST):
        return reply(
            status, {"code": code, "error": "Source authoring request was refused."}
        )

    hosts, origins = (
        handler.headers.get_all("Host") or [],
        handler.headers.get_all("Origin") or [],
    )
    allowed = {
        f"127.0.0.1:{handler.server.server_port}",
        f"localhost:{handler.server.server_port}",
    }
    if len(hosts) != 1 or hosts[0] not in allowed or origins != ["http://" + hosts[0]]:
        return refuse("ORIGIN_REFUSED", HTTPStatus.FORBIDDEN)
    if match is None or parsed.path != handler.path or match[2] not in _FIELDS:
        return refuse()
    if handler.command != "POST":
        return refuse("METHOD_REFUSED", HTTPStatus.METHOD_NOT_ALLOWED)
    lengths = handler.headers.get_all("Content-Length") or []
    if (
        handler.headers.get_all("Content-Type") != ["application/json"]
        or len(lengths) != 1
        or re.fullmatch(r"[0-9]{1,5}", lengths[0]) is None
        or handler.headers.get_all("Transfer-Encoding")
        or handler.headers.get_all("Content-Encoding")
        or handler.headers.get_all("Idempotency-Key")
    ):
        return refuse()
    size = int(lengths[0])
    if not 2 <= size <= MAX_REQUEST_BYTES:
        return refuse("REQUEST_LIMIT", HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
    caps = handler.headers.get_all(CAPABILITY_HEADER) or []
    if (match[2] == "bootstrap" and caps) or (
        match[2] != "bootstrap" and len(caps) != 1
    ):
        return refuse("CAPABILITY_REFUSED", HTTPStatus.FORBIDDEN)

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError()
            result[key] = value
        return result

    try:
        deadline = monotonic() + 2
        body = _read_request_body(handler, size, deadline)
        handler.connection.settimeout(2)
        payload = json.loads(
            body.decode("utf-8"),
            object_pairs_hook=pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
        )
        if type(payload) is not dict or set(payload) != _FIELDS[match[2]]:
            return refuse()
        # All fields are flat scalar values; reject containers before minting.
        if any(type(value) not in {str, bool} for value in payload.values()):
            return refuse()
        if monotonic() >= deadline:
            raise TimeoutError()
        result = handler.server.source_authoring_web.request(
            match[1], match[2], payload, caps[0] if caps else None
        )
    except SourceAuthoringWebError as error:
        return refuse(error.code, error.status)
    except SourceAuthoringOperationError as error:
        code = (
            error.code
            if error.code
            in {
                "session_capacity",
                "operation_capacity",
                "alias_capacity",
                "grant_closed",
            }
            else "CONSENT_REFUSED"
        )
        return refuse(code, HTTPStatus.CONFLICT)
    except (SourceAuthoringOwnersError, SourceAuthoringPolicyError):
        return refuse("SOURCE_UNAVAILABLE", HTTPStatus.CONFLICT)
    except TimeoutError:
        handler.connection.settimeout(2)
        return refuse("REQUEST_TIMEOUT", HTTPStatus.REQUEST_TIMEOUT)
    except (ValueError, UnicodeError, RecursionError):
        return refuse()
    except Exception:  # noqa: BLE001 - no source, adapter, or credential details in errors
        return refuse("SOURCE_UNAVAILABLE", HTTPStatus.CONFLICT)
    return reply(HTTPStatus.OK, result)
