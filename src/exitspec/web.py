"""A local-only browser demo for ExitSpec's Define -> Prove -> Decide loop.

The server deliberately has no authentication, persistence, provider credentials, or
network integrations. It is a runnable product demo over the synthetic support-agent
fixture, not an authorization service. Human review actions live only in process.
"""

from __future__ import annotations

import json
import mimetypes
import tempfile
import threading
import uuid
import webbrowser
from dataclasses import dataclass, field
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import unquote, urlparse

import yaml

from .adapters.deterministic_tool_selection import DeterministicToolSelectionAdapter
from .authoring import (
    approve_draft,
    assemble_approved_contract,
    load_contract_seed,
    load_discovery_pack,
    reject_draft,
)
from .intake import TranscriptIntakeError, parse_pasted_transcript
from .models import (
    ContractSeed,
    CriterionDraft,
    DiscoveryPack,
    DiscoveryTranscript,
    DraftStatus,
    POCContract,
    ReviewDecision,
    TranscriptSpan,
    VerdictStatus,
)
from .runner import RunResult, run_demo
from .reporting import render_customer_draft


PROJECT_ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_ROOT = PROJECT_ROOT / "examples" / "support-agent"
DEFAULT_DISCOVERY_PATH = EXAMPLE_ROOT / "authoring" / "discovery-pack-v1.json"
DEFAULT_CONTRACT_SEED_PATH = EXAMPLE_ROOT / "authoring" / "contract-seed-v1.json"
DEFAULT_FIXTURE_PATH = EXAMPLE_ROOT / "fixtures" / "tool-selection-200.json"
DEFAULT_RUNS_ROOT = PROJECT_ROOT / "runs"
STATIC_ROOT = Path(__file__).resolve().parent / "static"
MAX_REQUEST_BYTES = 128 * 1024


class DemoStateError(ValueError):
    """A user-visible constraint in the local demo workflow."""


@dataclass
class DemoSession:
    """Ephemeral, synthetic state backing one browser demo session."""

    discovery_pack: DiscoveryPack
    contract_seed: ContractSeed
    fixture_path: Path
    output_root: Path
    reviewed_drafts: List[CriterionDraft] = field(default_factory=list)
    last_run: Optional[RunResult] = None
    customer_draft_path: Optional[Path] = None
    transcript_notice: str = "Built-in synthetic discovery transcript"

    @classmethod
    def synthetic_support_agent(
        cls,
        output_root: Path = DEFAULT_RUNS_ROOT,
    ) -> "DemoSession":
        discovery_pack = load_discovery_pack(DEFAULT_DISCOVERY_PATH)
        return cls(
            discovery_pack=discovery_pack,
            contract_seed=load_contract_seed(DEFAULT_CONTRACT_SEED_PATH),
            fixture_path=DEFAULT_FIXTURE_PATH,
            output_root=output_root,
            reviewed_drafts=list(discovery_pack.drafts),
        )

    @property
    def pending_drafts(self) -> List[CriterionDraft]:
        return [
            draft
            for draft in self.reviewed_drafts
            if draft.status == DraftStatus.NEEDS_REVIEW
        ]

    @property
    def approved_drafts(self) -> List[CriterionDraft]:
        return [
            draft
            for draft in self.reviewed_drafts
            if draft.status == DraftStatus.APPROVED
        ]

    def approved_contract(self) -> Optional[POCContract]:
        """Return the candidate contract only when every visible draft is resolved."""

        if self.pending_drafts or not self.approved_drafts:
            return None
        return assemble_approved_contract(self.contract_seed, self.approved_drafts)

    def review(
        self,
        draft_id: str,
        decision: str,
        reviewer: str,
        rationale: str,
    ) -> CriterionDraft:
        """Apply one explicit human review action; this never auto-resolves ambiguity."""

        if not reviewer.strip():
            raise DemoStateError("A named human reviewer is required.")
        if not rationale.strip():
            raise DemoStateError("A review rationale is required for the audit trail.")
        try:
            requested_decision = ReviewDecision(decision.upper())
        except ValueError as error:
            raise DemoStateError("Decision must be APPROVE or REJECT.") from error

        for index, draft in enumerate(self.reviewed_drafts):
            if draft.id != draft_id:
                continue
            if requested_decision == ReviewDecision.APPROVE:
                reviewed = approve_draft(draft, reviewer=reviewer, rationale=rationale)
            else:
                reviewed = reject_draft(draft, reviewer=reviewer, rationale=rationale)
            self.reviewed_drafts[index] = reviewed
            self.last_run = None
            self.customer_draft_path = None
            return reviewed
        raise DemoStateError("Unknown draft {0}.".format(draft_id))

    def intake(self, pasted_text: str, title: str = "Pasted discovery transcript") -> None:
        """Capture synthetic meeting notes without inventing an executable commitment.

        This local, provider-free demo deliberately creates an unresolved candidate
        from a source line rather than claiming that a model negotiated a complete
        acceptance rule. A future model adapter may propose a richer candidate, but
        the human review requirement remains exactly the same.
        """

        try:
            transcript = parse_pasted_transcript(
                pasted_text,
                transcript_id="pasted-transcript",
                title=title,
            )
        except TranscriptIntakeError as error:
            raise DemoStateError(str(error)) from error

        candidates = _capture_source_candidates(transcript)
        self.discovery_pack = DiscoveryPack(transcript=transcript, drafts=candidates)
        self.reviewed_drafts = list(candidates)
        self.last_run = None
        self.customer_draft_path = None
        self.transcript_notice = (
            "Synthetic pasted meeting notes. ExitSpec captured source candidates; "
            "a human must still define a complete measurable rule."
        )

    def reset_to_synthetic_sample(self) -> None:
        """Restore the deterministic support-agent demonstration without disk writes."""

        fresh = self.synthetic_support_agent(output_root=self.output_root)
        self.discovery_pack = fresh.discovery_pack
        self.contract_seed = fresh.contract_seed
        self.fixture_path = fresh.fixture_path
        self.reviewed_drafts = fresh.reviewed_drafts
        self.last_run = None
        self.customer_draft_path = None
        self.transcript_notice = fresh.transcript_notice

    def prove(self, scenario: str) -> RunResult:
        """Run the deterministic fixture only after explicit review has closed."""

        contract = self.approved_contract()
        if contract is None:
            if self.pending_drafts:
                raise DemoStateError(
                    "Resolve every candidate first. Ambiguous requirements cannot be "
                    "silently dropped before a POC is proved."
                )
            raise DemoStateError(
                "At least one complete, human-approved requirement is needed before proving."
            )
        allowed_scenarios = DeterministicToolSelectionAdapter().scenarios
        if scenario not in allowed_scenarios:
            raise DemoStateError(
                "Unsupported scenario. Choose one of: {0}.".format(
                    ", ".join(allowed_scenarios)
                )
            )

        self.output_root.mkdir(parents=True, exist_ok=True)
        run_id = "web-{0}-{1}".format(scenario, uuid.uuid4().hex[:12])
        with tempfile.TemporaryDirectory(prefix="exitspec-contract-") as temporary_dir:
            contract_path = Path(temporary_dir) / "approved-contract.yaml"
            contract_path.write_text(
                yaml.safe_dump(
                    contract.model_dump(mode="json"),
                    allow_unicode=True,
                    sort_keys=False,
                ),
                encoding="utf-8",
            )
            self.last_run = run_demo(
                contract_path=contract_path,
                fixture_path=self.fixture_path,
                scenario=scenario,
                output_root=self.output_root,
                run_id=run_id,
            )
        return self.last_run

    def create_customer_draft(self) -> Path:
        """Write a share-ready local review draft before the contract is frozen."""

        contract = self.approved_contract()
        if contract is None:
            raise DemoStateError(
                "Resolve every visible candidate before creating a customer review draft."
            )
        self.output_root.mkdir(parents=True, exist_ok=True)
        draft_dir = self.output_root / "customer-draft-{0}".format(uuid.uuid4().hex[:12])
        draft_dir.mkdir()
        draft_path = draft_dir / "customer-review-draft.html"
        draft_path.write_text(render_customer_draft(contract), encoding="utf-8")
        (draft_dir / "proposed-contract.json").write_text(
            json.dumps(contract.model_dump(mode="json"), ensure_ascii=False, indent=2)
            + "\n",
            encoding="utf-8",
        )
        self.customer_draft_path = draft_path
        return draft_path

    def state_payload(self) -> Dict[str, Any]:
        contract = self.approved_contract()
        return {
            "mode": "local_synthetic_demo",
            "safety": {
                "synthetic_only": True,
                "provider_calls": False,
                "authorization": "ExitSpec proves evidence; humans retain every approval decision.",
            },
            "transcript_notice": self.transcript_notice,
            "transcript": self.discovery_pack.transcript.model_dump(mode="json"),
            "drafts": [draft.model_dump(mode="json") for draft in self.reviewed_drafts],
            "contract": None if contract is None else contract.model_dump(mode="json"),
            "ready_to_prove": contract is not None,
            "supported_scenarios": list(
                DeterministicToolSelectionAdapter().scenarios
            ),
            "customer_draft_url": self._customer_draft_url(),
            "proof_pack": self._proof_payload(),
        }

    def _customer_draft_url(self) -> Optional[str]:
        if self.customer_draft_path is None:
            return None
        try:
            relative = self.customer_draft_path.relative_to(self.output_root)
        except ValueError:
            return None
        return "/artifacts/{0}".format(relative.as_posix())

    def _proof_payload(self) -> Optional[Dict[str, Any]]:
        if self.last_run is None:
            return None
        overall = self.last_run.overall_verdict
        criterion = self.last_run.criterion_verdict
        run_name = self.last_run.output_dir.name
        return {
            "overall_verdict": overall.verdict.value,
            "overall_reason": overall.reason,
            "criterion_verdict": criterion.verdict.value,
            "criterion_reason": criterion.reason,
            "observed_rate": criterion.observed_rate,
            "confidence_lower_bound": criterion.confidence_lower_bound,
            "sample_count": criterion.sample_count,
            "contract_hash": self.last_run.contract.canonical_hash,
            "report_url": "/artifacts/{0}/decision-packet.html".format(run_name),
            "manifest_url": "/artifacts/{0}/artifact-hashes.json".format(run_name),
            "next_human_action": _next_human_action(overall.verdict),
        }


def _next_human_action(verdict: VerdictStatus) -> str:
    if verdict == VerdictStatus.PASS:
        return (
            "Review the Proof Pack with the customer. PASS is evidence, not an "
            "automatic ship or authorization decision."
        )
    if verdict == VerdictStatus.NOT_PROVEN:
        return (
            "Keep the POC open and collect sufficient valid evidence before claiming success."
        )
    if verdict == VerdictStatus.BLOCKED:
        return "Resolve the stated external blocker, then rerun the same frozen contract."
    return "Review the failed criterion and decide whether to revise the POC or stop it."


def _capture_source_candidates(transcript: DiscoveryTranscript) -> List[CriterionDraft]:
    """Make source-visible *unresolved* candidates from structured pasted notes.

    This is intentionally not an extraction model. It never manufactures a metric,
    threshold, evaluation set, or approval. The first version keeps the call-to-
    contract boundary honest while leaving room for a later provider-neutral draft
    adapter.
    """

    signal_words = ("must", "need", "require", "at least", "under", "within", "%")
    candidate_lines = [
        line
        for line in transcript.lines
        if any(signal in line.text.lower() for signal in signal_words)
    ]
    if not candidate_lines:
        candidate_lines = [transcript.lines[0]]
    return [
        CriterionDraft(
            id="CALL-CLAIM-{0:02d}".format(index),
            source_span=TranscriptSpan(
                transcript_id=transcript.id,
                start_line=line.line_number,
                end_line=line.line_number,
                speaker=line.speaker,
                quote=line.text,
            ),
            normalized_claim="Review this source statement before treating it as a POC requirement: {0}".format(
                line.text
            ),
            open_questions=[
                "A human must define the metric, threshold, evaluation set, and evidence policy before approval."
            ],
        )
        for index, line in enumerate(candidate_lines, start=1)
    ]


class ExitSpecDemoServer(ThreadingHTTPServer):
    """A loopback-only server with one ephemeral DemoSession."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: Tuple[str, int], session: DemoSession) -> None:
        super().__init__(address, ExitSpecDemoRequestHandler)
        self.session = session
        self.static_root = STATIC_ROOT


class ExitSpecDemoRequestHandler(BaseHTTPRequestHandler):
    server: ExitSpecDemoServer

    def do_GET(self) -> None:  # noqa: N802 - stdlib request handler API
        parsed = urlparse(self.path)
        if parsed.path == "/api/state":
            self._send_json(HTTPStatus.OK, self.server.session.state_payload())
            return
        if parsed.path.startswith("/artifacts/"):
            self._serve_artifact(parsed.path)
            return
        self._serve_static(parsed.path)

    def do_POST(self) -> None:  # noqa: N802 - stdlib request handler API
        parsed = urlparse(self.path)
        try:
            payload = self._read_json()
            if parsed.path == "/api/review":
                reviewed = self.server.session.review(
                    draft_id=_required_string(payload, "draft_id"),
                    decision=_required_string(payload, "decision"),
                    reviewer=_required_string(payload, "reviewer"),
                    rationale=_required_string(payload, "rationale"),
                )
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "reviewed_draft": reviewed.model_dump(mode="json"),
                        "state": self.server.session.state_payload(),
                    },
                )
                return
            if parsed.path == "/api/prove":
                self.server.session.prove(_required_string(payload, "scenario"))
                self._send_json(HTTPStatus.OK, self.server.session.state_payload())
                return
            if parsed.path == "/api/customer-draft":
                self.server.session.create_customer_draft()
                self._send_json(HTTPStatus.OK, self.server.session.state_payload())
                return
            if parsed.path == "/api/reset":
                self.server.session.reset_to_synthetic_sample()
                self._send_json(HTTPStatus.OK, self.server.session.state_payload())
                return
            if parsed.path == "/api/intake":
                self.server.session.intake(
                    pasted_text=_required_string(payload, "transcript"),
                    title=_optional_string(payload, "title")
                    or "Pasted discovery transcript",
                )
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "state": self.server.session.state_payload(),
                        "notice": "Source notes captured. Candidate claims remain unresolved until a human defines a complete measurement rule.",
                    },
                )
                return
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "Unknown API route."})
        except DemoStateError as error:
            self._send_json(HTTPStatus.CONFLICT, {"error": str(error)})
        except ValueError as error:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(error)})

    def _read_json(self) -> Dict[str, Any]:
        content_length = self.headers.get("Content-Length")
        if content_length is None:
            raise ValueError("Content-Length is required.")
        try:
            size = int(content_length)
        except ValueError as error:
            raise ValueError("Content-Length must be an integer.") from error
        if size < 0 or size > MAX_REQUEST_BYTES:
            raise ValueError("Request body is too large.")
        body = self.rfile.read(size)
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("Request body must be valid UTF-8 JSON.") from error
        if not isinstance(payload, dict):
            raise ValueError("Request body must be a JSON object.")
        return payload

    def _serve_static(self, request_path: str) -> None:
        relative = "index.html" if request_path in ("", "/") else request_path.lstrip("/")
        target = _safe_child(self.server.static_root, relative)
        if target is None or not target.is_file():
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "Page not found."})
            return
        self._send_file(target)

    def _serve_artifact(self, request_path: str) -> None:
        relative = request_path.removeprefix("/artifacts/")
        target = _safe_child(self.server.session.output_root, relative)
        if target is None or not target.is_file():
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "Artifact not found."})
            return
        self._send_file(target)

    def _send_file(self, path: Path) -> None:
        content_type, _ = mimetypes.guess_type(str(path))
        data = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _send_json(self, status: HTTPStatus, payload: Dict[str, Any]) -> None:
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: Any) -> None:
        """Keep demo startup clean; HTTP diagnostics are not product output."""


def _required_string(payload: Dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError("{0} must be a non-empty string.".format(key))
    return value.strip()


def _optional_string(payload: Dict[str, Any], key: str) -> Optional[str]:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("{0} must be a string when provided.".format(key))
    return value.strip() or None


def _safe_child(root: Path, relative: str) -> Optional[Path]:
    try:
        decoded = unquote(relative)
        target = (root / decoded).resolve()
        target.relative_to(root.resolve())
    except (OSError, ValueError):
        return None
    return target


def serve_demo(
    host: str = "127.0.0.1",
    port: int = 8765,
    output_root: Path = DEFAULT_RUNS_ROOT,
    open_browser: bool = False,
) -> ExitSpecDemoServer:
    """Start the local-only server. The caller owns ``serve_forever`` lifecycle."""

    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("ExitSpec demo only binds to a loopback address.")
    if not STATIC_ROOT.is_dir():
        raise RuntimeError("ExitSpec static demo assets are unavailable.")
    server = ExitSpecDemoServer((host, port), DemoSession.synthetic_support_agent(output_root))
    if open_browser:
        threading.Timer(
            0.15,
            lambda: webbrowser.open("http://{0}:{1}".format(host, server.server_port)),
        ).start()
    return server
