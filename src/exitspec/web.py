"""A local-only browser demo for ExitSpec's Define -> Prove -> Decide loop.

The server deliberately has no authentication or persistence. Optional Fireworks
execution is disabled by default, accepts only the frozen synthetic request, and
never grants provider output lifecycle or verdict authority. This remains a local
prototype, not a production authorization service.
"""

from __future__ import annotations

import hashlib
import json
import math
import mimetypes
import tempfile
import threading
import time
import uuid
import webbrowser
from contextlib import ExitStack
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from functools import wraps
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import parse_qsl, unquote, urlparse

import yaml
from pydantic import ValidationError

from .adapters.deterministic_tool_selection import DeterministicToolSelectionAdapter
from .authoring import (
    approve_draft,
    assemble_approved_contract,
    edit_draft,
    load_contract_seed,
    load_discovery_pack,
    reject_draft,
)
from .assisted_authoring import (
    AssistedAuthoringError,
    build_assisted_discovery_pack,
    build_assisted_discovery_pack_from_result,
)
from .confirmations import (
    ConfirmationDecision,
    ContractConfirmation,
    canonical_confirmation_payload,
    confirmation_matches_contract,
    confirmation_operation_id,
    contract_confirmation_fingerprint,
    record_confirmation,
)
from .contracts import freeze_confirmed_contract, utc_now as contract_utc_now
from .demo_data import support_agent_demo_paths
from .draft_workspace import project_draft_dashboard
from .intake import (
    TranscriptIntakeError,
    TranscriptRedactionSummary,
    redact_and_parse_pasted_transcript,
)
from .models import (
    ContractSeed,
    Criterion,
    CriterionDraft,
    DiscoveryPack,
    DiscoveryTranscript,
    DraftStatus,
    Metric,
    POCContract,
    ProportionRule,
    ReviewDecision,
    TranscriptSpan,
    VerdictStatus,
)
from .performance_workspace import (
    PERFORMANCE_POC_ID,
    performance_poc_detail_payload,
    performance_workspace_record_and_facts,
)
from .performance_web_api import (
    handle_performance_web_api_request,
    is_performance_web_api_target,
)
from .performance_web_runtime import PerformanceWebRuntime
from .performance_web_service import (
    build_trusted_performance_web_runtime,
)
from .poc_contract_definition import ProcessLocalContractDefinitionService
from .poc_contract_definition_web_api import (
    handle_poc_contract_definition_web_api_request,
    is_poc_contract_definition_web_api_target,
)
from .poc_creation import (
    DraftPOCArchiveState,
    DraftPOCCapacityExceeded,
    DraftPOCCreateRequest,
    DraftPOCIdempotencyConflict,
    DraftPOCNotFound,
    DuplicateDraftPOCId,
    ProcessLocalDraftPOCService,
)
from .poc_performance_lifecycle import (
    PerformanceLifecycleError,
    PerformanceLifecycleSnapshot,
    ProcessLocalPerformanceLifecycleService,
)
from .poc_performance_lifecycle_web_api import (
    handle_performance_lifecycle_web_api_request,
    is_performance_lifecycle_web_api_target,
)
from .poc_performance_run import (
    POCPerformanceRunSnapshot,
    POCPerformanceRunStatus,
    ProcessLocalPOCPerformanceRunService,
)
from .poc_performance_run_web_api import (
    handle_poc_performance_run_web_api_request,
    is_poc_performance_run_web_api_target,
)
from .poc_proposal_review import (
    ProcessLocalProposalReviewService,
    ProposalReviewState,
)
from .poc_proposal_web_api import (
    handle_poc_proposal_web_api_request,
    is_poc_proposal_web_api_target,
)
from .poc_source_intake import ProcessLocalPOCSourceIntake
from .poc_source_web_api import (
    handle_poc_source_web_api_request,
    is_poc_source_web_api_target,
)
from .provider_egress import (
    InMemoryProviderEgressAuthorizer,
    ProviderEgressAcknowledgement,
    ProviderEgressAcknowledgementError,
)
from .providers import ProviderError, StructuredJSONRequest
from .runner import RunResult, run_demo
from .reporting import render_customer_draft
from .review_links import (
    CustomerReviewInvitation,
    ReviewInvitationError,
    issue_customer_review_invitation,
)
from .synthetic_assisted_authoring import (
    SYNTHETIC_ASSISTED_ADAPTER,
    SYNTHETIC_ASSISTED_ADAPTER_VERSION,
    SYNTHETIC_ASSISTED_MODEL,
    SYNTHETIC_ASSISTED_POLICY,
    SyntheticAssistedAuthoringExecutor,
    safe_receipt_facts,
)
from .source_web import (
    SourceIntakeRecord,
    SourceWebRefusal,
    SourceWebRequest,
    SourceWebRuntime,
    SourceWebRuntimeError,
    handle_source_web_request,
    is_source_pipeline_target,
    source_import_success_payload,
)
from .wave1_execution import (
    WAVE1_FIREWORKS_ADAPTER,
    WAVE1_FIREWORKS_ADAPTER_VERSION,
    Wave1ProviderExecutionConfiguration,
    wave1_terminal_receipt,
)
from .wave1_runtime import (
    build_frozen_wave1_request,
    frozen_wave1_policy,
    frozen_wave1_source,
    wave1_provider_disclosure,
)
from .workspace import (
    ArchiveState,
    DashboardFilter,
    DashboardProjection,
    POCRegistryEntry,
    POCWorkspaceProjection,
    POCWorkflowFacts,
    ReadOnlyPOCRegistry,
    WorkspaceAction,
    WorkspaceBlocker,
    WorkspaceEvidenceState,
    WorkspacePhase,
    WorkspaceSourceType,
    project_dashboard,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_ROOT = PROJECT_ROOT / "examples" / "support-agent"
DEFAULT_DISCOVERY_PATH = EXAMPLE_ROOT / "authoring" / "discovery-pack-v1.json"
DEFAULT_CONTRACT_SEED_PATH = EXAMPLE_ROOT / "authoring" / "contract-seed-v1.json"
DEFAULT_FIXTURE_PATH = EXAMPLE_ROOT / "fixtures" / "tool-selection-200.json"
DEFAULT_RUNS_ROOT = PROJECT_ROOT / "runs"
STATIC_ROOT = Path(__file__).resolve().parent / "static"
MAX_REQUEST_BYTES = 128 * 1024
SOURCE_SURPLUS_GRACE_SECONDS = 0.02
JSON_MEDIA_TYPE = "application/json"
LOOPBACK_ORIGIN_HOSTS = {"127.0.0.1", "localhost", "::1"}
MAX_WAVE1_PROVIDER_AUTHORIZATION_OPERATIONS = 64
MAX_WAVE1_PROVIDER_EXECUTION_OPERATIONS = 64
UNSUPPORTED_MEDIA_TYPE_ERROR = "Content-Type must be application/json."
FORBIDDEN_ORIGIN_ERROR = "Origin is not allowed."
PROVIDER_ROUTE_PARAMETERS_ERROR = (
    "Provider authority routes do not accept URL parameters."
)
WORKSPACE_FILTER_ERROR = (
    "Workspace filter must be exactly Active, Needs attention, or Completed."
)
DRAFT_POC_ROUTE_PARAMETERS_ERROR = (
    "Draft POC routes do not accept URL parameters."
)
DRAFT_POC_INVALID_REQUEST_ERROR = "Draft POC request is invalid."
DRAFT_POC_CONFLICT_ERROR = (
    "That idempotency key is already bound to a different draft POC request."
)
DRAFT_POC_CAPACITY_ERROR = (
    "Draft POC creation is temporarily unavailable in this local process."
)
DRAFT_POC_NOT_FOUND_ERROR = "Draft POC was not found in this local process."
SUPPORTED_RULE_TEMPLATE = {
    "metric": Metric.EXACT_TOOL_SELECTION_RATE.value,
    "metric_label": "Exact expected support-tool selection",
    "unit": "proportion",
    "aggregation": "exact-match proportion",
    "adapter": DeterministicToolSelectionAdapter.name,
    "adapter_version": DeterministicToolSelectionAdapter.version,
    "confidence_method": "95% Wilson lower bound",
    "evidence_policy": (
        "Persist synthetic case IDs, expected/actual tool names, calculation "
        "inputs, and SHA-256 digests."
    ),
    "limitation": (
        "This deterministic demo can execute one exact support-tool selection "
        "criterion over the bundled fixed fixture. Other tasks must remain context "
        "until a compatible measurement adapter exists."
    ),
}
SYNTHETIC_SUPPORT_AGENT_POC_ID = "poc_support_agent_demo"


class DemoStateError(ValueError):
    """A user-visible constraint in the local demo workflow."""


class ProviderExecutionInProgressError(DemoStateError):
    """A same-key replay timed out locally while its operation is still running."""

    code = "provider_execution_in_progress"


WAVE1_PROVIDER_REPLAY_WAIT_SECONDS = 70.0


def _usd_text(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.01")), "f")


class _Wave1ProviderAuthorization:
    """Server-private capability state with a deliberately content-free repr."""

    __slots__ = (
        "acknowledgement",
        "authorizer",
        "capability_token",
        "disclosure_id",
        "idempotency_key",
        "replaced_previous",
        "request",
    )

    def __init__(
        self,
        *,
        acknowledgement: ProviderEgressAcknowledgement,
        authorizer: InMemoryProviderEgressAuthorizer,
        capability_token: str,
        disclosure_id: str,
        idempotency_key: str,
        replaced_previous: bool,
        request: StructuredJSONRequest[Any],
    ) -> None:
        self.acknowledgement = acknowledgement
        self.authorizer = authorizer
        self.capability_token = capability_token
        self.disclosure_id = disclosure_id
        self.idempotency_key = idempotency_key
        self.replaced_previous = replaced_previous
        self.request = request

    def public_payload(self, *, idempotent_replay: bool) -> Dict[str, Any]:
        return {
            "acknowledgement_id": self.acknowledgement.acknowledgement_id,
            "disclosure_id": self.disclosure_id,
            "issued_at": self.acknowledgement.issued_at.isoformat(),
            "expires_at": self.acknowledgement.expires_at.isoformat(),
            "one_time_use": True,
            "status": "authorization_recorded",
            "idempotent_replay": idempotent_replay,
            "replaced_previous": self.replaced_previous,
        }

    def __repr__(self) -> str:
        return (
            "_Wave1ProviderAuthorization("
            "acknowledgement_id={0!r}, capability=<redacted>, "
            "request=<redacted>)"
        ).format(self.acknowledgement.acknowledgement_id)


class _Wave1ProviderAuthorizationOperation:
    """Content-free idempotency record that cannot reactivate an old capability."""

    __slots__ = (
        "acknowledgement_id",
        "disclosure_id",
        "expires_at",
        "issued_at",
        "replaced_previous",
    )

    def __init__(self, state: _Wave1ProviderAuthorization) -> None:
        self.acknowledgement_id = state.acknowledgement.acknowledgement_id
        self.disclosure_id = state.disclosure_id
        self.issued_at = state.acknowledgement.issued_at.isoformat()
        self.expires_at = state.acknowledgement.expires_at.isoformat()
        self.replaced_previous = state.replaced_previous

    def public_payload(self, *, idempotent_replay: bool) -> Dict[str, Any]:
        return {
            "acknowledgement_id": self.acknowledgement_id,
            "disclosure_id": self.disclosure_id,
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            "one_time_use": True,
            "status": "authorization_recorded",
            "idempotent_replay": idempotent_replay,
            "replaced_previous": self.replaced_previous,
        }

    def __repr__(self) -> str:
        return (
            "_Wave1ProviderAuthorizationOperation("
            "acknowledgement_id={0!r}, content=<redacted>)"
        ).format(self.acknowledgement_id)


class _Wave1ProviderExecutionOperation:
    """Content-free pending/terminal record for safe execution replay."""

    __slots__ = (
        "_completed",
        "_event",
        "acknowledgement_id",
        "attempts",
        "execution_id",
        "next_action",
        "outcome_code",
        "provider_call_attempted",
        "proposals_created",
        "receipt",
        "retryable",
        "reserved_cost_usd",
        "safe_message",
        "status",
    )

    def __init__(
        self,
        *,
        acknowledgement_id: str,
        execution_id: str,
        reserved_cost_usd: Decimal,
    ) -> None:
        self._completed = False
        self._event = threading.Event()
        self.acknowledgement_id = acknowledgement_id
        self.attempts = 0
        self.execution_id = execution_id
        self.next_action = "wait_for_provider_execution"
        self.outcome_code = "in_progress"
        self.provider_call_attempted = False
        self.proposals_created = 0
        self.receipt: Optional[Dict[str, Any]] = None
        self.retryable = False
        self.reserved_cost_usd = reserved_cost_usd
        self.safe_message = "Provider execution is in progress."
        self.status = "in_progress"

    @property
    def completed(self) -> bool:
        return self._completed

    def wait(self, timeout_seconds: float) -> bool:
        return self._event.wait(timeout_seconds)

    def complete(
        self,
        *,
        attempts: int,
        next_action: str,
        outcome_code: str,
        provider_call_attempted: bool,
        proposals_created: int,
        receipt: Dict[str, Any],
        retryable: bool,
        safe_message: str,
        status: str,
    ) -> None:
        if self._completed:
            raise RuntimeError("Provider execution operation is already terminal.")
        self.attempts = attempts
        self.next_action = next_action
        self.outcome_code = outcome_code
        self.provider_call_attempted = provider_call_attempted
        self.proposals_created = proposals_created
        if not isinstance(receipt, dict):
            raise ValueError("A terminal provider receipt is required.")
        self.receipt = json.loads(json.dumps(receipt, allow_nan=False))
        self.retryable = retryable
        self.safe_message = safe_message
        self.status = status
        self._completed = True
        self._event.set()

    def public_payload(self, *, idempotent_replay: bool) -> Dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "acknowledgement_id": self.acknowledgement_id,
            "status": self.status,
            "outcome_code": self.outcome_code,
            "safe_message": self.safe_message,
            "next_action": self.next_action,
            "attempts": self.attempts,
            "retryable": self.retryable,
            "provider_call_attempted": self.provider_call_attempted,
            "proposals_created": self.proposals_created,
            "reserved_cost_usd": _usd_text(self.reserved_cost_usd),
            "receipt": (
                None
                if self.receipt is None
                else json.loads(json.dumps(self.receipt, allow_nan=False))
            ),
            "idempotent_replay": idempotent_replay,
        }

    def __repr__(self) -> str:
        return (
            "_Wave1ProviderExecutionOperation("
            "execution_id={0!r}, outcome_code={1!r}, "
            "completed={2!r}, content=<redacted>)"
        ).format(self.execution_id, self.outcome_code, self._completed)


def _serialized_session(method: Any) -> Any:
    """Serialize one session transaction across the threaded local HTTP server."""

    @wraps(method)
    def locked(self: "DemoSession", *args: Any, **kwargs: Any) -> Any:
        with self._lock:
            return method(self, *args, **kwargs)

    return locked


@dataclass
class DemoSession:
    """Ephemeral, synthetic state backing one browser demo session."""

    discovery_pack: DiscoveryPack
    contract_seed: ContractSeed
    fixture_path: Path
    output_root: Path
    reviewed_drafts: List[CriterionDraft] = field(default_factory=list)
    reviewed_contract: Optional[POCContract] = None
    frozen_contract: Optional[POCContract] = None
    customer_review_invitation: Optional[CustomerReviewInvitation] = None
    customer_review_token: Optional[str] = None
    customer_confirmation: Optional[ContractConfirmation] = None
    confirmation_operations: Dict[str, ContractConfirmation] = field(
        default_factory=dict
    )
    revision_request: Optional[str] = None
    revision_parent_version: Optional[str] = None
    revision_edit_applied_ids: set[str] = field(default_factory=set)
    last_run: Optional[RunResult] = None
    customer_draft_path: Optional[Path] = None
    transcript_notice: str = "Built-in synthetic discovery transcript"
    transcript_redaction: Optional[TranscriptRedactionSummary] = None
    authoring_mode: str = "deterministic"
    authoring_adapter: str = "source_candidate_capture"
    authoring_adapter_version: str = "1"
    authoring_receipt: Optional[Dict[str, Any]] = None
    _source_runtime: SourceWebRuntime = field(
        default_factory=SourceWebRuntime,
        init=False,
        repr=False,
        compare=False,
    )
    _source_intake: Optional[SourceIntakeRecord] = field(
        default=None,
        init=False,
        repr=False,
        compare=False,
    )
    _source_receipt: Optional[Dict[str, Any]] = field(
        default=None,
        init=False,
        repr=False,
        compare=False,
    )
    _wave1_provider_authorization: Optional[_Wave1ProviderAuthorization] = field(
        default=None,
        init=False,
        repr=False,
        compare=False,
    )
    _wave1_provider_authorization_operations: Dict[
        str, _Wave1ProviderAuthorizationOperation
    ] = field(
        default_factory=dict,
        init=False,
        repr=False,
        compare=False,
    )
    _wave1_provider_execution_operations: Dict[
        str, _Wave1ProviderExecutionOperation
    ] = field(
        default_factory=dict,
        init=False,
        repr=False,
        compare=False,
    )
    _wave1_provider_last_execution_key: Optional[str] = field(
        default=None,
        init=False,
        repr=False,
        compare=False,
    )
    _wave1_provider_execution_enabled: bool = field(
        default=False,
        init=False,
        repr=False,
        compare=False,
    )
    _wave1_provider_execution_configured: bool = field(
        default=False,
        init=False,
        repr=False,
        compare=False,
    )
    _wave1_provider_calls: bool = field(
        default=False,
        init=False,
        repr=False,
        compare=False,
    )
    _wave1_provider_reserved_spend_usd: Decimal = field(
        default_factory=lambda: Decimal("0"),
        init=False,
        repr=False,
        compare=False,
    )
    _wave1_workflow_epoch: int = field(
        default=0,
        init=False,
        repr=False,
        compare=False,
    )
    _sample_discovery_pack: Optional[DiscoveryPack] = field(
        default=None,
        repr=False,
        compare=False,
    )
    _sample_contract_seed: Optional[ContractSeed] = field(
        default=None,
        repr=False,
        compare=False,
    )
    _sample_fixture_path: Optional[Path] = field(
        default=None,
        repr=False,
        compare=False,
    )
    _lock: Any = field(
        default_factory=threading.RLock,
        init=False,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        """Keep a reset snapshot without assuming resources live in the checkout."""

        if self._sample_discovery_pack is None:
            self._sample_discovery_pack = self.discovery_pack.model_copy(deep=True)
        if self._sample_contract_seed is None:
            self._sample_contract_seed = self.contract_seed.model_copy(deep=True)
        if self._sample_fixture_path is None:
            self._sample_fixture_path = self.fixture_path

    def _clear_guided_source_boundary(self) -> None:
        """Atomically replace every private and public guided-source fact."""

        self._source_runtime.reset()
        self._source_intake = None
        self._source_receipt = None

    def _guided_source_import_locked(self) -> bool:
        """Return whether customer/downstream work already owns the workflow."""

        return any(
            value is not None
            for value in (
                self.customer_review_invitation,
                self.customer_review_token,
                self.customer_confirmation,
                self.frozen_contract,
                self.last_run,
                self.customer_draft_path,
                self.revision_request,
                self.revision_parent_version,
            )
        )

    @_serialized_session
    def guided_source_catalog_payload(self) -> Dict[str, Any]:
        """Return the exact frozen catalog without exposing resource locations."""

        try:
            return self._source_runtime.catalog_payload()
        except SourceWebRuntimeError:
            raise SourceWebRefusal("source_import_refused") from None

    @_serialized_session
    def import_guided_source_fixture(
        self,
        fixture_case_id: str,
    ) -> Dict[str, Any]:
        """Publish one reviewed source projection under the session transaction."""

        if self._guided_source_import_locked():
            raise SourceWebRefusal("source_import_locked")

        current = self._source_intake
        if current is not None:
            if current.fixture_case_id != fixture_case_id:
                raise SourceWebRefusal("source_change_requires_reset")
            try:
                receipt = self._source_runtime.replay(fixture_case_id)
                return source_import_success_payload(
                    receipt=receipt,
                    intake=current,
                    drafts=self.reviewed_drafts,
                )
            except SourceWebRuntimeError:
                raise SourceWebRefusal("source_import_refused") from None

        try:
            publication = self._source_runtime.import_new(fixture_case_id)
        except SourceWebRuntimeError:
            raise SourceWebRefusal("source_import_refused") from None

        # Every fallible parser/model/projection/store operation completed before
        # these assignments. The session lock makes the publication indivisible.
        self.discovery_pack = publication.discovery_pack
        self.reviewed_drafts = list(publication.discovery_pack.drafts)
        self.revision_request = None
        self.revision_parent_version = None
        self.revision_edit_applied_ids.clear()
        self._invalidate_customer_agreement()
        self.transcript_redaction = None
        self.transcript_notice = (
            "Approved synthetic email was redacted before intake. Every "
            "proposal remains untrusted until explicit human review."
        )
        self.authoring_mode = "deterministic"
        self.authoring_adapter = "synthetic_email_source"
        self.authoring_adapter_version = "wave2-source-web-v1"
        self.authoring_receipt = None
        self._source_intake = publication.intake
        self._source_receipt = publication.receipt.to_dict()
        return source_import_success_payload(
            receipt=publication.receipt,
            intake=publication.intake,
            drafts=self.reviewed_drafts,
        )

    def _clear_wave1_provider_authorization(self) -> None:
        """Drop active authority without erasing replay or spend tombstones."""

        self._wave1_provider_authorization = None
        self._wave1_workflow_epoch += 1

    @_serialized_session
    def configure_wave1_provider_execution(
        self,
        configuration: Wave1ProviderExecutionConfiguration,
    ) -> None:
        """Record only public enablement facts; the credential stays server-owned."""

        if type(configuration) is not Wave1ProviderExecutionConfiguration:
            raise ValueError(
                "Wave-1 provider execution configuration is invalid."
            )
        public = configuration.public_status()
        self._wave1_provider_execution_enabled = public["enabled"]
        self._wave1_provider_execution_configured = public["configured"]

    def _wave1_provider_execution_status_payload(self) -> Dict[str, Any]:
        policy = frozen_wave1_policy()
        limits = policy.request_limits()
        total_limit = Decimal(limits["max_live_smoke_total_cost_usd"])
        remaining = max(
            Decimal("0"),
            total_limit - self._wave1_provider_reserved_spend_usd,
        )
        authorization = self._wave1_provider_authorization
        last_operation = (
            None
            if self._wave1_provider_last_execution_key is None
            else self._wave1_provider_execution_operations.get(
                self._wave1_provider_last_execution_key
            )
        )
        return {
            "enabled": self._wave1_provider_execution_enabled,
            "configured": self._wave1_provider_execution_configured,
            "authorization_active": authorization is not None,
            "execution_available": bool(
                self._wave1_provider_execution_enabled
                and self._wave1_provider_execution_configured
                and authorization is not None
                and remaining >= policy.max_request_cost_usd
                and (
                    len(self._wave1_provider_execution_operations)
                    < MAX_WAVE1_PROVIDER_EXECUTION_OPERATIONS
                )
            ),
            "provider_calls": self._wave1_provider_calls,
            "reserved_spend_usd": _usd_text(
                self._wave1_provider_reserved_spend_usd
            ),
            "remaining_spend_usd": _usd_text(remaining),
            "max_live_smoke_total_cost_usd": _usd_text(total_limit),
            "last_execution": (
                None
                if last_operation is None
                else last_operation.public_payload(
                    idempotent_replay=False
                )
            ),
        }

    def _wave1_disclosure_with_runtime(self) -> Dict[str, Any]:
        disclosure = wave1_provider_disclosure()
        disclosure["runtime"] = self._wave1_provider_execution_status_payload()
        return disclosure

    def _wave1_workflow_publication_guard(self) -> str:
        """Bind an in-flight result to the exact workflow state it started from."""

        review_token_digest = (
            None
            if self.customer_review_token is None
            else hashlib.sha256(
                self.customer_review_token.encode("utf-8")
            ).hexdigest()
        )
        payload = {
            "workflow_epoch": self._wave1_workflow_epoch,
            "discovery_pack": self.discovery_pack.model_dump(mode="json"),
            "reviewed_drafts": [
                draft.model_dump(mode="json")
                for draft in self.reviewed_drafts
            ],
            "reviewed_contract": (
                None
                if self.reviewed_contract is None
                else self.reviewed_contract.model_dump(mode="json")
            ),
            "frozen_contract": (
                None
                if self.frozen_contract is None
                else self.frozen_contract.model_dump(mode="json")
            ),
            "customer_review_invitation": (
                None
                if self.customer_review_invitation is None
                else self.customer_review_invitation.model_dump(mode="json")
            ),
            "customer_review_token_digest": review_token_digest,
            "customer_confirmation": (
                None
                if self.customer_confirmation is None
                else self.customer_confirmation.model_dump(mode="json")
            ),
            "revision_request": self.revision_request,
            "revision_parent_version": self.revision_parent_version,
            "revision_edit_applied_ids": sorted(
                self.revision_edit_applied_ids
            ),
            "last_run": self._proof_payload(),
            "customer_draft_path": (
                None
                if self.customer_draft_path is None
                else str(self.customer_draft_path)
            ),
            "authoring_mode": self.authoring_mode,
            "authoring_adapter": self.authoring_adapter,
            "authoring_adapter_version": self.authoring_adapter_version,
            "authoring_receipt": self.authoring_receipt,
            "source_intake": (
                None
                if self._source_intake is None
                else self._source_intake.public_payload(
                    self.reviewed_drafts
                )
            ),
            "source_receipt": self._source_receipt,
        }
        encoded = json.dumps(
            payload,
            allow_nan=False,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @_serialized_session
    def wave1_provider_disclosure_payload(self) -> Dict[str, Any]:
        """Return frozen terms plus detached local runtime availability."""

        disclosure = self._wave1_disclosure_with_runtime()
        authorization = self._wave1_provider_authorization
        disclosure["authorization"] = (
            None
            if authorization is None
            else authorization.public_payload(idempotent_replay=False)
        )
        return disclosure

    @_serialized_session
    def authorize_wave1_provider_egress(
        self,
        *,
        disclosure_id: str,
        acknowledged: bool,
        idempotency_key: str,
    ) -> Dict[str, Any]:
        """Record one explicit authorization while keeping its capability private."""

        disclosure = self._wave1_disclosure_with_runtime()
        expected_disclosure_id = disclosure["disclosure_id"]
        if (
            not isinstance(disclosure_id, str)
            or disclosure_id != expected_disclosure_id
        ):
            raise DemoStateError(
                "Provider disclosure changed; review the current disclosure again."
            )
        if acknowledged is not True:
            raise DemoStateError(
                "Provider egress requires explicit acknowledgement of the current disclosure."
            )
        if not isinstance(idempotency_key, str) or not idempotency_key.strip():
            raise DemoStateError(
                "Provider authorization requires an idempotency key."
            )
        normalized_idempotency_key = idempotency_key.strip()
        if len(normalized_idempotency_key) > 200:
            raise DemoStateError(
                "Provider authorization idempotency key is too long."
            )

        operation = self._wave1_provider_authorization_operations.get(
            normalized_idempotency_key
        )
        if operation is not None:
            return {
                "authorization": operation.public_payload(idempotent_replay=True),
                "disclosure": disclosure,
            }
        if (
            len(self._wave1_provider_authorization_operations)
            >= MAX_WAVE1_PROVIDER_AUTHORIZATION_OPERATIONS
        ):
            raise DemoStateError(
                "Provider authorization operation limit reached; restart the "
                "local server."
            )

        existing = self._wave1_provider_authorization
        replaced_previous = existing is not None
        policy = frozen_wave1_policy()
        request = build_frozen_wave1_request()
        authorizer = InMemoryProviderEgressAuthorizer(policy)
        acknowledgement, capability_token = authorizer.issue(
            request,
            acknowledged=True,
        )
        state = _Wave1ProviderAuthorization(
            acknowledgement=acknowledgement,
            authorizer=authorizer,
            capability_token=capability_token,
            disclosure_id=expected_disclosure_id,
            idempotency_key=normalized_idempotency_key,
            replaced_previous=replaced_previous,
            request=request,
        )
        self._wave1_provider_authorization = state
        self._wave1_provider_authorization_operations[
            normalized_idempotency_key
        ] = _Wave1ProviderAuthorizationOperation(state)
        disclosure["runtime"] = self._wave1_provider_execution_status_payload()
        public = state.public_payload(idempotent_replay=False)
        return {
            "authorization": public,
            "disclosure": disclosure,
        }

    def execute_wave1_provider_assist(
        self,
        *,
        configuration: Wave1ProviderExecutionConfiguration,
        idempotency_key: str,
    ) -> Dict[str, Any]:
        """Claim, execute, and conditionally publish one frozen synthetic assist."""

        if type(configuration) is not Wave1ProviderExecutionConfiguration:
            raise DemoStateError(
                "Wave-1 provider execution is not configured safely."
            )
        if (
            not isinstance(idempotency_key, str)
            or not idempotency_key
            or idempotency_key != idempotency_key.strip()
            or any(character.isspace() for character in idempotency_key)
        ):
            raise DemoStateError(
                "Provider execution requires one exact idempotency key."
            )
        if len(idempotency_key) > 200:
            raise DemoStateError(
                "Provider execution idempotency key is too long."
            )

        waiter: Optional[_Wave1ProviderExecutionOperation] = None
        operation: Optional[_Wave1ProviderExecutionOperation] = None
        executor = None
        publication_guard = ""
        source: Dict[str, Any] = {}

        with self._lock:
            previous = self._wave1_provider_execution_operations.get(
                idempotency_key
            )
            if previous is not None:
                if previous.completed:
                    return {
                        "execution": previous.public_payload(
                            idempotent_replay=True
                        ),
                        "state": self.state_payload(),
                    }
                waiter = previous
            else:
                if any(
                    not recorded.completed
                    for recorded in self._wave1_provider_execution_operations.values()
                ):
                    raise DemoStateError(
                        "Another provider execution is in progress; wait for "
                        "its terminal result."
                    )
                if (
                    len(self._wave1_provider_execution_operations)
                    >= MAX_WAVE1_PROVIDER_EXECUTION_OPERATIONS
                ):
                    raise DemoStateError(
                        "Provider execution operation limit reached; "
                        "restart the local server."
                    )
                authorization = self._wave1_provider_authorization
                if authorization is None:
                    raise DemoStateError(
                        "Review and authorize the current provider disclosure first."
                    )

                policy = frozen_wave1_policy()
                operation = _Wave1ProviderExecutionOperation(
                    acknowledgement_id=(
                        authorization.acknowledgement.acknowledgement_id
                    ),
                    execution_id="wave1_execution_{0}".format(
                        uuid.uuid4().hex
                    ),
                    reserved_cost_usd=Decimal("0"),
                )
                self._wave1_provider_execution_operations[
                    idempotency_key
                ] = operation
                self._wave1_provider_last_execution_key = idempotency_key

                configuration_status = configuration.public_status()
                expected_status = {
                    "enabled": self._wave1_provider_execution_enabled,
                    "configured": self._wave1_provider_execution_configured,
                }
                if configuration_status != expected_status:
                    operation.complete(
                        attempts=0,
                        next_action="restart_with_provider_configuration",
                        outcome_code="configuration_error",
                        provider_call_attempted=False,
                        proposals_created=0,
                        receipt=wave1_terminal_receipt(
                            policy=policy,
                            outcome_code="configuration_error",
                            attempts=0,
                        ),
                        retryable=False,
                        safe_message=(
                            "Provider execution configuration changed; "
                            "restart the local server."
                        ),
                        status="failed",
                    )
                    return {
                        "execution": operation.public_payload(
                            idempotent_replay=False
                        ),
                        "state": self.state_payload(),
                    }
                if not configuration.configured:
                    operation.complete(
                        attempts=0,
                        next_action="configure_provider",
                        outcome_code="configuration_error",
                        provider_call_attempted=False,
                        proposals_created=0,
                        receipt=wave1_terminal_receipt(
                            policy=policy,
                            outcome_code="configuration_error",
                            attempts=0,
                        ),
                        retryable=False,
                        safe_message=(
                            "Fireworks is disabled or its server credential "
                            "is missing."
                        ),
                        status="failed",
                    )
                    return {
                        "execution": operation.public_payload(
                            idempotent_replay=False
                        ),
                        "state": self.state_payload(),
                    }

                total_limit = Decimal(
                    policy.request_limits()[
                        "max_live_smoke_total_cost_usd"
                    ]
                )
                reservation = policy.max_request_cost_usd
                if (
                    self._wave1_provider_reserved_spend_usd + reservation
                    > total_limit
                ):
                    operation.complete(
                        attempts=0,
                        next_action="restart_after_budget_review",
                        outcome_code="budget_exceeded",
                        provider_call_attempted=False,
                        proposals_created=0,
                        receipt=wave1_terminal_receipt(
                            policy=policy,
                            outcome_code="budget_exceeded",
                            attempts=0,
                        ),
                        retryable=False,
                        safe_message=(
                            "The process-local Wave-1 live-smoke spend "
                            "ceiling has been reached."
                        ),
                        status="failed",
                    )
                    return {
                        "execution": operation.public_payload(
                            idempotent_replay=False
                        ),
                        "state": self.state_payload(),
                    }

                operation.reserved_cost_usd = reservation
                self._wave1_provider_reserved_spend_usd += reservation
                publication_guard = self._wave1_workflow_publication_guard()
                self._wave1_provider_authorization = None
                source = frozen_wave1_source()
                executor = configuration.bind(
                    policy=policy,
                    authorizer=authorization.authorizer,
                    capability_token=authorization.capability_token,
                    request=authorization.request,
                )

        if waiter is not None:
            if not waiter.wait(WAVE1_PROVIDER_REPLAY_WAIT_SECONDS):
                raise ProviderExecutionInProgressError(
                    "Provider execution is still in progress; retry the same "
                    "idempotency key."
                )
            with self._lock:
                return {
                    "execution": waiter.public_payload(
                        idempotent_replay=True
                    ),
                    "state": self.state_payload(),
                }
        if operation is None or executor is None:
            raise AssertionError("Provider execution claim was not created.")

        authored = None
        outcome_code = "internal_error"
        safe_message = (
            "Provider-assisted discovery failed at the local execution boundary."
        )
        next_action = "reset_and_reauthorize"
        attempts = 0
        retryable = False
        status = "failed"

        try:
            provider_result = executor.execute()
            authored = build_assisted_discovery_pack_from_result(
                source["transcript"],
                provider_result=provider_result,
                policy=SYNTHETIC_ASSISTED_POLICY,
                customer_terms=source["customer_terms"],
                transcript_id=source["transcript_id"],
                title=source["title"],
            )
            if any(
                draft.status != DraftStatus.NEEDS_REVIEW
                or draft.review is not None
                or (
                    draft.proposed_criterion is not None
                    and draft.proposed_criterion.approved
                )
                for draft in authored.discovery_pack.drafts
            ):
                authored = None
                safe_message = (
                    "Provider output violated the local review-only boundary."
                )
                outcome_code = "review_boundary_violation"
                next_action = "review_provider_output"
        except AssistedAuthoringError as error:
            outcome_code = error.code
            safe_message = error.safe_message
            next_action = error.next_action
            attempts = error.attempts
            retryable = error.retryable
        except ProviderError as error:
            outcome_code = error.code.value
            safe_message = error.safe_message
            next_action = error.next_action.value
            attempts = error.attempts
            retryable = error.retryable
        except ProviderEgressAcknowledgementError as error:
            outcome_code = error.code
            safe_message = str(error)
            next_action = error.next_action
        except Exception:
            # Never retain or reflect an unexpected provider/request exception.
            authored = None

        if executor.last_receipt is not None:
            attempts = executor.last_receipt.attempts

        proposals_created = 0
        with self._lock:
            self._wave1_provider_calls = bool(
                self._wave1_provider_calls
                or executor.provider_call_attempted
            )
            if (
                authored is not None
                and publication_guard
                != self._wave1_workflow_publication_guard()
            ):
                authored = None
                outcome_code = "stale_workflow"
                safe_message = (
                    "The workflow changed while Fireworks was running; "
                    "the provider result was not published."
                )
                next_action = "review_current_workflow"
                attempts = (
                    0
                    if executor.last_receipt is None
                    else executor.last_receipt.attempts
                )
                retryable = False
                status = "failed"

            if authored is not None:
                proposals_created = len(authored.discovery_pack.drafts)
                self._clear_guided_source_boundary()
                self.discovery_pack = authored.discovery_pack
                self.reviewed_drafts = list(authored.discovery_pack.drafts)
                self.revision_request = None
                self.revision_parent_version = None
                self.revision_edit_applied_ids.clear()
                self._invalidate_customer_agreement()
                self.transcript_redaction = authored.redaction
                self.transcript_notice = (
                    "Fireworks assisted authoring used the approved synthetic "
                    "request under policy {0}. Every proposal remains "
                    "NEEDS_REVIEW."
                ).format(authored.redaction.policy_version)
                self.authoring_mode = "fireworks_assisted"
                self.authoring_adapter = WAVE1_FIREWORKS_ADAPTER
                self.authoring_adapter_version = (
                    WAVE1_FIREWORKS_ADAPTER_VERSION
                )
                self.authoring_receipt = safe_receipt_facts(
                    authored.receipt
                )
                outcome_code = "success"
                safe_message = (
                    "Fireworks proposals passed local schema, redaction, and "
                    "source-link checks and now require human review."
                )
                next_action = "review_provider_proposals"
                attempts = authored.receipt.attempts
                retryable = False
                status = "succeeded_needs_review"

            receipt = wave1_terminal_receipt(
                policy=policy,
                outcome_code=outcome_code,
                attempts=attempts,
                provider_receipt=executor.last_receipt,
            )
            operation.complete(
                attempts=attempts,
                next_action=next_action,
                outcome_code=outcome_code,
                provider_call_attempted=executor.provider_call_attempted,
                proposals_created=proposals_created,
                receipt=receipt,
                retryable=retryable,
                safe_message=safe_message,
                status=status,
            )
            return {
                "execution": operation.public_payload(
                    idempotent_replay=False
                ),
                "state": self.state_payload(),
            }

    @classmethod
    def synthetic_support_agent(
        cls,
        output_root: Path = DEFAULT_RUNS_ROOT,
        *,
        discovery_path: Path = DEFAULT_DISCOVERY_PATH,
        contract_seed_path: Path = DEFAULT_CONTRACT_SEED_PATH,
        fixture_path: Path = DEFAULT_FIXTURE_PATH,
    ) -> "DemoSession":
        discovery_pack = load_discovery_pack(discovery_path)
        contract_seed = load_contract_seed(contract_seed_path)
        return cls(
            discovery_pack=discovery_pack,
            contract_seed=contract_seed,
            fixture_path=fixture_path,
            output_root=output_root,
            reviewed_drafts=list(discovery_pack.drafts),
            _sample_discovery_pack=discovery_pack.model_copy(deep=True),
            _sample_contract_seed=contract_seed.model_copy(deep=True),
            _sample_fixture_path=fixture_path,
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

    @_serialized_session
    def approved_contract(self) -> Optional[POCContract]:
        """Return the candidate contract only when every visible draft is resolved."""

        if self.pending_drafts or not self.approved_drafts:
            return None
        if self.reviewed_contract is None:
            assembled = assemble_approved_contract(
                self.contract_seed, self.approved_drafts
            )
            if self.revision_parent_version is not None:
                payload = assembled.model_dump(mode="python")
                payload["parent_version"] = self.revision_parent_version
                assembled = POCContract.model_validate(payload)
            self.reviewed_contract = assembled
        return self.reviewed_contract

    def _invalidate_customer_agreement(self) -> None:
        """Invalidate every downstream state when the proposed agreement changes."""

        self._clear_wave1_provider_authorization()
        self.reviewed_contract = None
        self.frozen_contract = None
        self.customer_review_invitation = None
        self.customer_review_token = None
        self.customer_confirmation = None
        self.confirmation_operations.clear()
        self.customer_draft_path = None
        self.last_run = None

    @_serialized_session
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
            self._invalidate_customer_agreement()
            return reviewed
        raise DemoStateError("Unknown draft {0}.".format(draft_id))

    def _apply_structured_rule(
        self,
        *,
        draft_id: str,
        title: str,
        threshold_percent: float,
        minimum_samples: int,
        workload_slice: str,
        require_revision: bool,
    ) -> CriterionDraft:
        """Create one supported criterion from human-entered structured fields."""

        if require_revision and self.revision_request is None:
            raise DemoStateError("No customer-requested revision is active.")
        normalized_title = " ".join(title.split())
        normalized_workload = " ".join(workload_slice.split())
        if not normalized_title or not normalized_workload:
            raise DemoStateError("Rule title and workload slice must be non-empty.")
        if not 0 < threshold_percent <= 100:
            raise DemoStateError("Threshold must be greater than 0 and at most 100.")
        if minimum_samples <= 0:
            raise DemoStateError("Minimum samples must be greater than zero.")

        for index, draft in enumerate(self.reviewed_drafts):
            if draft.id != draft_id:
                continue
            if draft.status != DraftStatus.NEEDS_REVIEW:
                raise DemoStateError("Only a draft needing review can be edited.")

            existing = draft.proposed_criterion
            other_executable_drafts = [
                candidate
                for candidate in self.reviewed_drafts
                if candidate.id != draft_id
                and candidate.status != DraftStatus.REJECTED
                and candidate.proposed_criterion is not None
            ]
            if existing is None and other_executable_drafts:
                raise DemoStateError(
                    "This deterministic demo supports exactly one executable "
                    "acceptance rule. Keep this request as context until a compatible "
                    "measurement adapter exists."
                )
            normalized_claim = _generated_tool_selection_claim(
                title=normalized_title,
                threshold_percent=threshold_percent,
                minimum_samples=minimum_samples,
                workload_slice=normalized_workload,
            )
            criterion = Criterion(
                id=(
                    existing.id
                    if existing is not None
                    else _criterion_id_for_draft(draft.id)
                ),
                title=normalized_title,
                must_have=True if existing is None else existing.must_have,
                source=(
                    None
                    if draft.source_span is None
                    else draft.source_span.to_source_reference()
                ),
                human_added=draft.human_added,
                normalized_claim=normalized_claim,
                metric=Metric.EXACT_TOOL_SELECTION_RATE,
                unit=SUPPORTED_RULE_TEMPLATE["unit"],
                aggregation=SUPPORTED_RULE_TEMPLATE["aggregation"],
                rule=ProportionRule(
                    threshold=threshold_percent / 100,
                    minimum_samples=minimum_samples,
                ),
                workload_slice=normalized_workload,
                adapter=SUPPORTED_RULE_TEMPLATE["adapter"],
                adapter_version=SUPPORTED_RULE_TEMPLATE["adapter_version"],
                owner=(
                    existing.owner
                    if existing is not None
                    else "vendor_solutions_engineer"
                ),
                evidence_policy=SUPPORTED_RULE_TEMPLATE["evidence_policy"],
                approved=False,
            )
            revised = edit_draft(
                draft,
                {
                    "normalized_claim": normalized_claim,
                    "proposed_criterion": criterion,
                    "open_questions": [],
                },
            )
            self.reviewed_drafts[index] = revised
            if self.revision_request is not None:
                self.revision_edit_applied_ids.add(draft_id)
            self._invalidate_customer_agreement()
            return revised
        raise DemoStateError("Unknown draft {0}.".format(draft_id))

    @_serialized_session
    def define_draft_rule(
        self,
        *,
        draft_id: str,
        title: str,
        threshold_percent: float,
        minimum_samples: int,
        workload_slice: str,
    ) -> CriterionDraft:
        """Define or correct the currently supported deterministic rule."""

        if (
            self._source_intake is not None
            and not self._source_intake.can_edit_rule(draft_id)
        ):
            raise DemoStateError(
                "This source proposal must remain context until a compatible "
                "measurement adapter exists."
            )
        return self._apply_structured_rule(
            draft_id=draft_id,
            title=title,
            threshold_percent=threshold_percent,
            minimum_samples=minimum_samples,
            workload_slice=workload_slice,
            require_revision=False,
        )

    @_serialized_session
    def intake(
        self,
        pasted_text: str,
        title: str = "Pasted discovery transcript",
        *,
        customer_terms: Sequence[str] = (),
    ) -> None:
        """Capture synthetic meeting notes without inventing an executable commitment.

        This local, provider-free demo deliberately creates an unresolved candidate
        from a source line rather than claiming that a model negotiated a complete
        acceptance rule. A future model adapter may propose a richer candidate, but
        the human review requirement remains exactly the same.
        """

        try:
            intake = redact_and_parse_pasted_transcript(
                pasted_text,
                transcript_id="pasted-transcript",
                title=title,
                customer_terms=customer_terms,
            )
        except TranscriptIntakeError as error:
            raise DemoStateError(str(error)) from error
        finally:
            del pasted_text

        transcript = intake.transcript
        candidates = _capture_source_candidates(transcript)
        discovery_pack = DiscoveryPack(
            transcript=transcript,
            drafts=candidates,
        )
        self._clear_guided_source_boundary()
        self.discovery_pack = discovery_pack
        self.reviewed_drafts = list(candidates)
        self.revision_request = None
        self.revision_parent_version = None
        self.revision_edit_applied_ids.clear()
        self._invalidate_customer_agreement()
        self.transcript_redaction = intake.redaction
        self.transcript_notice = (
            "Synthetic pasted meeting notes were redacted before intake under "
            "policy {0}. ExitSpec captured source candidates; a human must still "
            "define a complete measurable rule."
        ).format(
            intake.redaction.policy_version
        )
        self.authoring_mode = "deterministic"
        self.authoring_adapter = "source_candidate_capture"
        self.authoring_adapter_version = "1"
        self.authoring_receipt = None

    @_serialized_session
    def assisted_intake(
        self,
        pasted_text: str,
        title: str = "Assisted discovery transcript",
        *,
        customer_terms: Sequence[str] = (),
    ) -> None:
        """Create review-only drafts through the local synthetic adapter.

        This is explicit opt-in because it invokes a different authoring path than
        ``/api/intake``. The service redacts first and still owns schema, source
        anchor, and review-only validation boundaries.
        """

        try:
            authored = build_assisted_discovery_pack(
                pasted_text,
                executor=SyntheticAssistedAuthoringExecutor(),
                model=SYNTHETIC_ASSISTED_MODEL,
                policy=SYNTHETIC_ASSISTED_POLICY,
                customer_terms=customer_terms,
                transcript_id="assisted-transcript",
                title=title,
            )
        except (TranscriptIntakeError, AssistedAuthoringError) as error:
            raise DemoStateError(str(error)) from None
        except Exception:
            # Keep unexpected adapter/provider state out of the HTTP error body.
            raise DemoStateError(
                "Provider-assisted discovery could not be completed."
            ) from None
        finally:
            del pasted_text

        self._clear_guided_source_boundary()
        self.discovery_pack = authored.discovery_pack
        self.reviewed_drafts = list(authored.discovery_pack.drafts)
        self.revision_request = None
        self.revision_parent_version = None
        self.revision_edit_applied_ids.clear()
        self._invalidate_customer_agreement()
        self.transcript_redaction = authored.redaction
        self.transcript_notice = (
            "Synthetic assisted authoring redacted meeting notes under policy {0}. "
            "Every generated proposal remains NEEDS_REVIEW; a human must approve "
            "any contract rule before customer confirmation."
        ).format(authored.redaction.policy_version)
        self.authoring_mode = "synthetic_assisted"
        self.authoring_adapter = SYNTHETIC_ASSISTED_ADAPTER
        self.authoring_adapter_version = SYNTHETIC_ASSISTED_ADAPTER_VERSION
        self.authoring_receipt = safe_receipt_facts(authored.receipt)

    @_serialized_session
    def reset_to_synthetic_sample(self) -> None:
        """Restore the deterministic support-agent demonstration without disk writes."""

        if (
            self._sample_discovery_pack is None
            or self._sample_contract_seed is None
            or self._sample_fixture_path is None
        ):
            raise DemoStateError("The bundled sample is unavailable for reset.")
        discovery_pack = self._sample_discovery_pack.model_copy(deep=True)
        contract_seed = self._sample_contract_seed.model_copy(deep=True)
        fixture_path = self._sample_fixture_path
        self._clear_guided_source_boundary()
        self.discovery_pack = discovery_pack
        self.contract_seed = contract_seed
        self.fixture_path = fixture_path
        self.reviewed_drafts = list(self.discovery_pack.drafts)
        self.revision_request = None
        self.revision_parent_version = None
        self.revision_edit_applied_ids.clear()
        self._invalidate_customer_agreement()
        self.transcript_notice = "Built-in synthetic discovery transcript"
        self.transcript_redaction = None
        self.authoring_mode = "deterministic"
        self.authoring_adapter = "source_candidate_capture"
        self.authoring_adapter_version = "1"
        self.authoring_receipt = None

    @_serialized_session
    def prove(self, scenario: str) -> RunResult:
        """Run the deterministic fixture only against the confirmed frozen contract."""

        if self.frozen_contract is None:
            if self.pending_drafts:
                raise DemoStateError(
                    "Resolve every candidate first. Ambiguous requirements cannot be "
                    "silently dropped before a POC is proved."
                )
            raise DemoStateError(
                "Customer confirmation and an explicit contract freeze are required "
                "before proving."
            )
        contract = self.frozen_contract
        allowed_scenarios = DeterministicToolSelectionAdapter().scenarios
        if scenario not in allowed_scenarios:
            raise DemoStateError(
                "Unsupported scenario. Choose one of: {0}.".format(
                    ", ".join(allowed_scenarios)
                )
            )

        self._clear_wave1_provider_authorization()
        self.output_root.mkdir(parents=True, exist_ok=True)
        run_id = "web-{0}-{1}".format(scenario, uuid.uuid4().hex[:12])
        self.last_run = None
        with tempfile.TemporaryDirectory(prefix="exitspec-contract-") as temporary_dir:
            contract_path = Path(temporary_dir) / "frozen-contract.yaml"
            contract_path.write_text(
                yaml.safe_dump(
                    contract.model_dump(mode="json"),
                    allow_unicode=True,
                    sort_keys=False,
                ),
                encoding="utf-8",
            )
            try:
                current_run = run_demo(
                    contract_path=contract_path,
                    fixture_path=self.fixture_path,
                    scenario=scenario,
                    output_root=self.output_root,
                    run_id=run_id,
                )
            except Exception as error:
                self.last_run = None
                raise DemoStateError(
                    "The evidence run failed before a current proof was recorded: "
                    "{0}".format(error)
                ) from error
            self.last_run = current_run
        return self.last_run

    @_serialized_session
    def create_customer_draft(self) -> Path:
        """Write a draft and issue one version-scoped customer review capability."""

        contract = self.approved_contract()
        if contract is None:
            raise DemoStateError(
                "Resolve every visible candidate before creating a customer review draft."
            )
        fingerprint = contract_confirmation_fingerprint(contract)
        if (
            self.customer_review_invitation is not None
            and self.customer_review_token is not None
            and self.customer_review_invitation.contract_id == contract.id
            and self.customer_review_invitation.contract_version == contract.version
            and self.customer_review_invitation.confirmation_fingerprint == fingerprint
            and self.customer_draft_path is not None
            and (
                self.customer_confirmation is not None
                or self.customer_review_invitation.accepts(
                    self.customer_review_token
                )
            )
        ):
            return self.customer_draft_path

        invitation, raw_token = issue_customer_review_invitation(
            contract_id=contract.id,
            contract_version=contract.version,
            confirmation_fingerprint=fingerprint,
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
        self.customer_review_invitation = invitation
        self.customer_review_token = raw_token
        self.customer_confirmation = None
        self.confirmation_operations.clear()
        self.frozen_contract = None
        self.last_run = None
        self.customer_draft_path = draft_path
        self._clear_wave1_provider_authorization()
        return draft_path

    @_serialized_session
    def customer_review_payload(self, token: str) -> Dict[str, Any]:
        """Return only the customer-safe agreement projection for one valid link."""

        invitation = self.customer_review_invitation
        contract = self.approved_contract()
        if invitation is None or contract is None:
            raise ReviewInvitationError("Customer review link is invalid.")
        invitation.require_valid(token)
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

        confirmation = self.customer_confirmation
        if confirmation is None:
            status = "PENDING"
        elif confirmation.decision == ConfirmationDecision.CONFIRM:
            status = "CONFIRMED"
        else:
            status = "CHANGES_REQUESTED"
        customer_criteria = [
            self._customer_criterion_payload(criterion)
            for criterion in agreement["criteria"]
        ]
        decision_payload = self._customer_decision_payload(
            confirmation,
            idempotent_replay=False,
        )
        return {
            "mode": "local_synthetic_demo",
            "safety": {
                "synthetic_only": True,
                "not_evidence": True,
                "not_production_authorization": True,
                "identity_note": (
                    "This local demo records a typed customer identity. A hosted "
                    "deployment still requires authenticated identity and authorization."
                ),
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
                    "target_system": agreement["target_system"],
                    "workload": agreement["workload"],
                    "owners": agreement["owners"],
                    "evidence_retention_policy": agreement[
                        "evidence_retention_policy"
                    ],
                },
                "target_system": agreement["target_system"],
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
                    "display_name": "Customer approver · local synthetic demo",
                    "notice": (
                        "This local demo does not authenticate a real customer. "
                        "A hosted review must bind verified identity and permission "
                        "to this exact contract version."
                    ),
                },
                "local_demo": {
                    "return_url": "/app",
                    "notice": (
                        "Local loopback demo only. A hosted customer review would "
                        "not expose an internal workspace shortcut."
                    ),
                },
                "decision": decision_payload,
            },
            "confirmation": self._confirmation_payload(),
        }

    @staticmethod
    def _customer_criterion_payload(criterion: Dict[str, Any]) -> Dict[str, Any]:
        source = criterion.get("source")
        metric_name = criterion["metric"]
        metric_label = {
            "exact_tool_selection_rate": "Exact tool-selection rate",
        }.get(metric_name, metric_name.replace("_", " ").capitalize())
        workload_label = (
            criterion["workload_slice"]
            .replace("-", " ")
            .replace("_", " ")
            .capitalize()
        )
        rule = criterion["rule"]
        operator = {
            "gte": "at least",
            "gt": "more than",
            "lte": "at most",
            "lt": "less than",
            "eq": "exactly",
        }.get(rule["operator"], rule["operator"])
        threshold = "{0} {1:.2f}%".format(
            operator,
            rule["threshold"] * 100,
        )
        sample = "{0} or more fixed cases".format(
            rule["minimum_samples"]
        )
        return {
            "id": criterion["id"],
            "title": criterion["title"],
            "normalized_claim": criterion["normalized_claim"],
            "plain_language": criterion["normalized_claim"],
            "source": source,
            "source_quote": (
                "Human-added requirement"
                if source is None
                else source["quote"]
            ),
            "metric": metric_label,
            "unit": criterion["unit"],
            "aggregation": criterion["aggregation"],
            "rule": rule,
            "threshold": threshold,
            "sample": sample,
            "workload": workload_label,
            "workload_slice": criterion["workload_slice"],
            "evidence_policy": criterion["evidence_policy"],
            "must_have": criterion["must_have"],
            "required": criterion["must_have"],
            "agreement": criterion,
            "excluded": [],
        }

    @staticmethod
    def _customer_decision_payload(
        confirmation: Optional[ContractConfirmation],
        *,
        idempotent_replay: bool,
    ) -> Optional[Dict[str, Any]]:
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

    @_serialized_session
    def record_customer_decision(
        self,
        token: str,
        *,
        decision: str,
        confirmer: str,
        agreement_acknowledged: bool,
        rationale: str,
        idempotency_key: str,
    ) -> Tuple[ContractConfirmation, bool]:
        """Record one terminal customer decision with idempotent retry behavior."""

        self.customer_review_payload(token)
        contract = self.approved_contract()
        if contract is None:
            raise DemoStateError("The proposed contract is unavailable.")
        try:
            requested_decision = ConfirmationDecision(decision.upper())
        except ValueError as error:
            raise DemoStateError(
                "Decision must be CONFIRM or REQUEST_CHANGES."
            ) from error

        operation_id = confirmation_operation_id(
            contract.id,
            contract.version,
            idempotency_key,
        )
        existing_operation = self.confirmation_operations.get(operation_id)
        if self.customer_confirmation is not None and existing_operation is None:
            raise DemoStateError(
                "Customer review already has a terminal decision. Create a new "
                "contract version before requesting another decision."
            )
        try:
            confirmation = record_confirmation(
                contract,
                confirmer_identity=confirmer,
                decision=requested_decision,
                agreement_acknowledged=agreement_acknowledged,
                rationale=rationale,
                idempotency_key=idempotency_key,
                existing=existing_operation,
            )
        except ValueError as error:
            raise DemoStateError(str(error)) from error

        self.confirmation_operations[operation_id] = confirmation
        self.customer_confirmation = confirmation
        self.frozen_contract = None
        self.last_run = None
        if existing_operation is None:
            self._clear_wave1_provider_authorization()
        return confirmation, existing_operation is not None

    @_serialized_session
    def start_revision(self) -> None:
        """Reopen confirmed criteria under a new version after requested changes."""

        confirmation = self.customer_confirmation
        contract = self.approved_contract()
        if (
            confirmation is None
            or confirmation.decision != ConfirmationDecision.REQUEST_CHANGES
            or contract is None
        ):
            raise DemoStateError(
                "A customer REQUEST_CHANGES decision is required to start revision."
            )

        rationale = confirmation.rationale
        reopened: List[CriterionDraft] = []
        for draft in self.reviewed_drafts:
            if draft.status != DraftStatus.APPROVED:
                reopened.append(draft)
                continue
            criterion = draft.proposed_criterion
            if criterion is None:
                raise DemoStateError(
                    "An approved requirement is missing its structured criterion."
                )
            draft_payload = draft.model_dump(mode="python")
            draft_payload.update(
                {
                    "status": DraftStatus.NEEDS_REVIEW,
                    "proposed_criterion": criterion.model_copy(
                        update={"approved": False}
                    ),
                    "open_questions": [
                        "Customer requested a revision: {0}".format(rationale)
                    ],
                    "review": None,
                }
            )
            reopened.append(CriterionDraft.model_validate(draft_payload))

        prior_version = contract.version
        self.contract_seed = self.contract_seed.model_copy(
            update={
                "version": _next_contract_version(prior_version),
                "created_at": contract_utc_now(),
            }
        )
        self.reviewed_drafts = reopened
        self.revision_request = rationale
        self.revision_parent_version = "{0}@{1}".format(
            contract.id,
            prior_version,
        )
        self.revision_edit_applied_ids.clear()
        self._invalidate_customer_agreement()

    @_serialized_session
    def edit_revision(
        self,
        *,
        draft_id: str,
        title: str,
        threshold_percent: float,
        minimum_samples: int,
        workload_slice: str,
    ) -> CriterionDraft:
        """Apply an explicit structured edit before the revised draft is reviewed."""

        return self._apply_structured_rule(
            draft_id=draft_id,
            title=title,
            threshold_percent=threshold_percent,
            minimum_samples=minimum_samples,
            workload_slice=workload_slice,
            require_revision=True,
        )

    @_serialized_session
    def freeze(self) -> POCContract:
        """Freeze the exact internally reviewed and customer-confirmed contract."""

        if self.frozen_contract is not None:
            return self.frozen_contract
        contract = self.approved_contract()
        if contract is None:
            raise DemoStateError(
                "Resolve internal review before freezing the customer agreement."
            )
        if self.customer_confirmation is None:
            raise DemoStateError(
                "A matching affirmative customer confirmation is required before freeze."
            )
        try:
            self.frozen_contract = freeze_confirmed_contract(
                contract,
                self.customer_confirmation,
            )
        except ValueError as error:
            raise DemoStateError(str(error)) from error
        self.last_run = None
        self._clear_wave1_provider_authorization()
        return self.frozen_contract

    @_serialized_session
    def state_payload(self) -> Dict[str, Any]:
        reviewed_contract = self.approved_contract()
        contract = self.frozen_contract or reviewed_contract
        approved_count = len(self.approved_drafts)
        pending_count = len(self.pending_drafts)
        ready_to_freeze = bool(
            reviewed_contract is not None
            and self.frozen_contract is None
            and self.customer_confirmation is not None
            and confirmation_matches_contract(
                reviewed_contract,
                self.customer_confirmation,
            )
        )
        proof = self._proof_payload()
        return {
            "mode": "local_synthetic_demo",
            "safety": {
                "synthetic_only": True,
                "provider_calls": self._wave1_provider_calls,
                "authorization": "ExitSpec proves evidence; humans retain every approval decision.",
            },
            "provider_execution": self._wave1_provider_execution_status_payload(),
            "transcript_notice": self.transcript_notice,
            "transcript_redaction": (
                None
                if self.transcript_redaction is None
                else self.transcript_redaction.model_dump(mode="json")
            ),
            "authoring": {
                "mode": self.authoring_mode,
                "provider_calls": self._wave1_provider_calls,
                "redaction": (
                    None
                    if self.transcript_redaction is None
                    else self.transcript_redaction.model_dump(mode="json")
                ),
                "adapter": self.authoring_adapter,
                "adapter_version": self.authoring_adapter_version,
                "receipt": self.authoring_receipt,
            },
            "source_intake": (
                None
                if self._source_intake is None
                else self._source_intake.public_payload(
                    self.reviewed_drafts
                )
            ),
            "source_receipt": (
                None
                if self._source_receipt is None
                else dict(self._source_receipt)
            ),
            "transcript": self.discovery_pack.transcript.model_dump(mode="json"),
            "drafts": [draft.model_dump(mode="json") for draft in self.reviewed_drafts],
            "contract": None if contract is None else contract.model_dump(mode="json"),
            "poc_label": (
                contract.use_case
                if contract is not None
                else self.discovery_pack.transcript.title
            ),
            "confirmation": self._confirmation_payload(),
            "revision_request": self.revision_request,
            "revision_edit_applied_ids": sorted(self.revision_edit_applied_ids),
            "approved_criterion_count": approved_count,
            "pending_draft_count": pending_count,
            "supported_rule_template": dict(SUPPORTED_RULE_TEMPLATE),
            "ready_to_prepare_customer_review": reviewed_contract is not None,
            "ready_to_freeze": ready_to_freeze,
            "ready_to_prove": self.frozen_contract is not None,
            "supported_scenarios": list(
                DeterministicToolSelectionAdapter().scenarios
            ),
            "customer_draft_url": self._customer_draft_url(),
            "customer_review_url": self._customer_review_url(),
            "proof_pack": proof,
            "workspace": self._workspace_projection_payload(contract, proof),
        }

    @_serialized_session
    def workspace_payload(
        self,
        selected_filter: DashboardFilter = DashboardFilter.ACTIVE,
    ) -> Dict[str, Any]:
        """Return one bounded dashboard projection without changing POC state."""

        reviewed_contract = self.approved_contract()
        contract = self.frozen_contract or reviewed_contract
        return self._workspace_projection_payload(
            contract,
            self._proof_payload(),
            selected_filter=selected_filter,
        )

    def _workspace_projection_payload(
        self,
        contract: Optional[POCContract],
        proof: Optional[Dict[str, Any]],
        *,
        selected_filter: DashboardFilter = DashboardFilter.ACTIVE,
    ) -> Dict[str, Any]:
        """Project the seeded POC without granting the workspace write authority."""

        source_type = (
            WorkspaceSourceType.EMAIL
            if self._source_intake is not None
            else WorkspaceSourceType.MEETING_TRANSCRIPT
        )
        confirmation = self.customer_confirmation
        updated_at = self._workspace_updated_at(contract)
        owner = self.contract_seed.owners[-1]
        sample_seed = self._sample_contract_seed or self.contract_seed
        record = POCRegistryEntry(
            poc_id=SYNTHETIC_SUPPORT_AGENT_POC_ID,
            display_name="Support-agent POC",
            customer_label=self.contract_seed.customer,
            use_case=self.contract_seed.use_case,
            owner=owner,
            created_at=sample_seed.created_at,
            updated_at=updated_at,
            archive_state=ArchiveState.ACTIVE,
        )
        facts = POCWorkflowFacts(
            source_count=1,
            source_types=(source_type,),
            pending_draft_count=len(self.pending_drafts),
            approved_criterion_count=len(self.approved_drafts),
            active_contract_id=None if contract is None else contract.id,
            active_contract_version=None if contract is None else contract.version,
            contract_status=None if contract is None else contract.status,
            customer_review_issued=self.customer_review_invitation is not None,
            customer_decision=(
                None if confirmation is None else confirmation.decision
            ),
            confirmation_matches_active_contract=(
                None
                if confirmation is None
                else bool(
                    contract is not None
                    and confirmation_matches_contract(contract, confirmation)
                )
            ),
            revision_requested=self.revision_request is not None,
            run_status=(
                None if self.last_run is None else self.last_run.manifest.status
            ),
            verdict=(
                None
                if self.last_run is None
                else self.last_run.overall_verdict.verdict
            ),
            verdict_reason=(
                None
                if self.last_run is None
                else self.last_run.overall_verdict.reason
            ),
            evidence_pack_url=(
                None if proof is None else proof["report_url"]
            ),
            action_since=updated_at,
        )
        performance_record, performance_facts = (
            performance_workspace_record_and_facts()
        )
        dashboard = project_dashboard(
            ReadOnlyPOCRegistry((record, performance_record)),
            {
                record.poc_id: facts,
                performance_record.poc_id: performance_facts,
            },
            current_owner=owner,
            selected_filter=selected_filter,
        )
        return dashboard.model_dump(mode="json")

    def _workspace_updated_at(
        self,
        contract: Optional[POCContract],
    ) -> datetime:
        """Return the latest existing domain timestamp without reading the clock."""

        timestamps = [self.contract_seed.created_at]
        for draft in self.reviewed_drafts:
            if draft.review is not None:
                timestamps.append(draft.review.reviewed_at)
        if contract is not None:
            timestamps.append(contract.created_at)
            if contract.approved_at is not None:
                timestamps.append(contract.approved_at)
            if contract.frozen_at is not None:
                timestamps.append(contract.frozen_at)
        if self.customer_review_invitation is not None:
            timestamps.append(self.customer_review_invitation.created_at)
        if self.customer_confirmation is not None:
            timestamps.append(self.customer_confirmation.decided_at)
        if self.last_run is not None:
            timestamps.append(self.last_run.manifest.ended_at)
        return max(timestamps)

    def _confirmation_payload(self) -> Optional[Dict[str, Any]]:
        confirmation = self.customer_confirmation
        if confirmation is None:
            return None
        return {
            "confirmation_id": confirmation.confirmation_id,
            "contract_id": confirmation.contract_id,
            "contract_version": confirmation.contract_version,
            "contract_fingerprint": confirmation.contract_fingerprint,
            "confirmer_identity": confirmation.confirmer_identity,
            "decision": confirmation.decision.value,
            "agreement_acknowledged": confirmation.agreement_acknowledged,
            "decided_at": confirmation.decided_at.isoformat(),
            "rationale": confirmation.rationale,
        }

    def _customer_draft_url(self) -> Optional[str]:
        if self.customer_draft_path is None:
            return None
        try:
            relative = self.customer_draft_path.relative_to(self.output_root)
        except ValueError:
            return None
        return "/artifacts/{0}".format(relative.as_posix())

    def _customer_review_url(self) -> Optional[str]:
        if self.customer_review_invitation is None or self.customer_review_token is None:
            return None
        return "/review/{0}".format(self.customer_review_token)

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
            "Review the POC Acceptance Evidence Pack with the customer. PASS is "
            "evidence, not an automatic ship or authorization decision."
        )
    if verdict == VerdictStatus.NOT_PROVEN:
        return (
            "Keep the POC open and collect sufficient valid evidence before claiming success."
        )
    if verdict == VerdictStatus.BLOCKED:
        return "Resolve the stated external blocker, then rerun the same frozen contract."
    return "Review the failed criterion and decide whether to revise the POC or stop it."


def _next_contract_version(version: str) -> str:
    """Advance a simple semantic version for the local revision workflow."""

    parts = version.split(".")
    if len(parts) == 3 and all(part.isdigit() for part in parts):
        major, minor, patch = (int(part) for part in parts)
        return "{0}.{1}.{2}".format(major, minor, patch + 1)
    return "{0}-revision-1".format(version)


def _criterion_id_for_draft(draft_id: str) -> str:
    """Derive a stable criterion ID without obscuring its source draft."""

    return "RULE-{0}".format(draft_id)[:64].rstrip("-")


def _generated_tool_selection_claim(
    *,
    title: str,
    threshold_percent: float,
    minimum_samples: int,
    workload_slice: str,
) -> str:
    """Generate the only customer claim accepted by the structured rule editor."""

    rendered_threshold = "{0:.2f}".format(threshold_percent).rstrip("0").rstrip(".")
    return (
        "{0} passes when exact expected support-tool selection reaches at least "
        "{1}% across at least {2} fixed cases in the {3} workload, and the 95% "
        "Wilson lower bound meets the same threshold."
    ).format(
        title.rstrip("."),
        rendered_threshold,
        minimum_samples,
        workload_slice,
    )


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


def _newest_workspace_items_first(
    items: Sequence[POCWorkspaceProjection],
) -> Tuple[POCWorkspaceProjection, ...]:
    """Keep newly created local drafts ahead of older local work."""

    return tuple(
        sorted(
            items,
            key=lambda item: (item.updated_at, item.poc_id),
            reverse=True,
        )
    )


def _unavailable_draft_workspace_projection(
    projected: POCWorkspaceProjection,
) -> POCWorkspaceProjection:
    """Replace an unverifiable source summary with one explicit safe blocker."""

    blocker = WorkspaceBlocker(
        code="draft_source_summary_unavailable",
        message="Source status is unavailable. Reload before continuing.",
    )
    payload = projected.model_dump(mode="python")
    payload.update(
        {
            "source_summary": {
                "count": 0,
                "types": (),
                "label": "Source status unavailable",
            },
            "next_action_code": WorkspaceAction.RESOLVE_BLOCKER,
            "next_human_action": blocker.message,
            "blockers": (blocker,),
            "attention_required": True,
        }
    )
    return POCWorkspaceProjection.model_validate(payload)


def _unavailable_review_workspace_projection(
    projected: POCWorkspaceProjection,
) -> POCWorkspaceProjection:
    """Keep source facts visible while refusing an unknown review state."""

    blocker = WorkspaceBlocker(
        code="draft_proposal_review_unavailable",
        message="Proposal review status is unavailable. Reload before continuing.",
    )
    payload = projected.model_dump(mode="python")
    payload.update(
        {
            "next_action_code": WorkspaceAction.RESOLVE_BLOCKER,
            "next_human_action": blocker.message,
            "blockers": (blocker,),
            "attention_required": True,
        }
    )
    return POCWorkspaceProjection.model_validate(payload)


def _agreement_aware_workspace_projection(
    projected: POCWorkspaceProjection,
    snapshot: PerformanceLifecycleSnapshot,
    run_snapshot: POCPerformanceRunSnapshot | None = None,
) -> POCWorkspaceProjection:
    """Project the exact local agreement lifecycle onto one dashboard row."""

    preparation = snapshot.preparation
    if preparation is None:
        return projected

    payload = projected.model_dump(mode="python")
    payload.update(
        {
            "active_contract_id": preparation.draft_id,
            "active_contract_version": None,
            "derived_phase": WorkspacePhase.DEFINE,
            "next_action_code": WorkspaceAction.CREATE_CUSTOMER_REVIEW,
            "next_human_action": (
                "Review and record customer confirmation for this agreement."
            ),
            "action_since": preparation.prepared_at,
            "updated_at": max(projected.updated_at, preparation.prepared_at),
        }
    )
    if snapshot.confirmation is not None:
        payload.update(
            {
                "next_action_code": (
                    WorkspaceAction.FREEZE_CONFIRMED_CONTRACT
                ),
                "next_human_action": "Freeze confirmed contract.",
                "action_since": snapshot.confirmation.decided_at,
                "updated_at": max(
                    projected.updated_at,
                    snapshot.confirmation.decided_at,
                ),
            }
        )
    if snapshot.frozen_contract is not None:
        frozen = snapshot.frozen_contract
        payload.update(
            {
                "active_contract_id": frozen.id,
                "active_contract_version": str(frozen.version),
                "derived_phase": WorkspacePhase.PROVE,
                "next_action_code": WorkspaceAction.RUN_POC,
                "next_human_action": (
                    "Bind and run this POC against the frozen agreement."
                ),
                "action_since": frozen.frozen_at,
                "updated_at": max(projected.updated_at, frozen.frozen_at),
            }
        )
    if run_snapshot is not None:
        run_status = run_snapshot.status
        if run_status is POCPerformanceRunStatus.RUNNING:
            payload.update(
                {
                    "derived_phase": WorkspacePhase.PROVE,
                    "next_action_code": WorkspaceAction.WAIT_FOR_PROOF,
                    "next_human_action": (
                        "Wait for the active proof run to finish."
                    ),
                }
            )
        elif run_status is POCPerformanceRunStatus.BLOCKED:
            payload.update(
                {
                    "derived_phase": WorkspacePhase.PROVE,
                    "next_action_code": WorkspaceAction.RERUN_POC,
                    "next_human_action": (
                        "Resolve endpoint readiness, then retry the proof."
                    ),
                    "latest_evidence_summary": {
                        "status": WorkspaceEvidenceState.BLOCKED,
                        "reason": (
                            run_snapshot.reason_code
                            or "The proof run was blocked."
                        ),
                        "report_url": None,
                    },
                    "attention_required": True,
                }
            )
        elif run_status is POCPerformanceRunStatus.NOT_PROVEN:
            payload.update(
                {
                    "derived_phase": WorkspacePhase.PROVE,
                    "next_action_code": WorkspaceAction.RERUN_POC,
                    "next_human_action": (
                        "Review the unproven run, then retry safely."
                    ),
                    "latest_evidence_summary": {
                        "status": WorkspaceEvidenceState.NOT_PROVEN,
                        "reason": (
                            run_snapshot.reason_code
                            or "The proof did not produce verified evidence."
                        ),
                        "report_url": None,
                    },
                    "attention_required": True,
                }
            )
        elif run_status is POCPerformanceRunStatus.COMPLETED:
            verdict = run_snapshot.verdict
            if verdict is None or run_snapshot.evidence_pack_url is None:
                raise ValueError(
                    "Completed performance run lacks verified evidence."
                )
            evidence_state = WorkspaceEvidenceState(verdict.value)
            payload.update(
                {
                    "derived_phase": WorkspacePhase.DECIDE,
                    "next_action_code": WorkspaceAction.REVIEW_EVIDENCE,
                    "next_human_action": (
                        "Review the verified {0} Evidence Pack.".format(
                            verdict.value
                        )
                    ),
                    "latest_evidence_summary": {
                        "status": evidence_state,
                        "reason": (
                            "Verified performance decision: {0}.".format(
                                verdict.value
                            )
                        ),
                        "report_url": run_snapshot.evidence_pack_url,
                    },
                    "attention_required": (
                        evidence_state is not WorkspaceEvidenceState.PASS
                    ),
                }
            )
    return POCWorkspaceProjection.model_validate(payload)


class ExitSpecDemoServer(ThreadingHTTPServer):
    """A loopback-only server with one ephemeral DemoSession."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        address: Tuple[str, int],
        session: DemoSession,
        *,
        resource_stack: Optional[ExitStack] = None,
        wave1_provider_execution: Optional[
            Wave1ProviderExecutionConfiguration
        ] = None,
        performance_runtime: Optional[PerformanceWebRuntime] = None,
        performance_fireworks_api_key: object = None,
    ) -> None:
        configuration = (
            Wave1ProviderExecutionConfiguration()
            if wave1_provider_execution is None
            else wave1_provider_execution
        )
        if type(configuration) is not Wave1ProviderExecutionConfiguration:
            raise ValueError(
                "Wave-1 provider execution configuration is invalid."
        )
        self._resource_stack = resource_stack
        self.draft_poc_service = ProcessLocalDraftPOCService()
        self.poc_source_intake = ProcessLocalPOCSourceIntake(
            draft_lookup=self.draft_poc_service.get,
        )
        self.proposal_review_service = ProcessLocalProposalReviewService(
            proposal_lookup=self.poc_source_intake.proposal_inputs,
        )
        self.contract_definition_service = (
            ProcessLocalContractDefinitionService(
                proposal_lookup=self.proposal_review_service.list_proposals,
            )
        )
        performance_prompt_bytes = (
            files("exitspec.demo_data")
            .joinpath(
                "inference_performance",
                "prompts",
                "synthetic-latency-v1.jsonl",
            )
            .read_bytes()
        )
        self.performance_lifecycle_service = (
            ProcessLocalPerformanceLifecycleService(
                draft_lookup=self.draft_poc_service.get,
                proposal_lookup=self.proposal_review_service.list_proposals,
                definition_lookup=self.contract_definition_service.definitions,
                prompt_bytes=performance_prompt_bytes,
            )
        )
        self.poc_performance_run_service = (
            ProcessLocalPOCPerformanceRunService(
                lifecycle=self.performance_lifecycle_service,
                output_root=session.output_root.resolve(),
                fireworks_api_key=performance_fireworks_api_key,
            )
        )
        if (
            performance_runtime is not None
            and type(performance_runtime) is not PerformanceWebRuntime
        ):
            raise ValueError("Performance web runtime is invalid.")
        self.performance_runtime = (
            performance_runtime
            if performance_runtime is not None
            else build_trusted_performance_web_runtime(
                output_root=session.output_root.resolve(),
            )
        )
        super().__init__(address, ExitSpecDemoRequestHandler)
        self.session = session
        self.wave1_provider_execution = configuration
        self.session.configure_wave1_provider_execution(configuration)
        self.static_root = STATIC_ROOT

    def workspace_payload(
        self,
        selected_filter: DashboardFilter,
    ) -> Dict[str, Any]:
        """Merge local drafts into the seeded read-only dashboard projection."""

        seeded = DashboardProjection.model_validate(
            self.session.workspace_payload(selected_filter)
        )
        drafts = self.draft_poc_service.snapshots()
        receipts_by_poc_id = {}
        pending_proposal_counts_by_poc_id = {}
        kept_proposal_counts_by_poc_id = {}
        defined_criterion_counts_by_poc_id = {}
        agreement_snapshots_by_poc_id = {}
        performance_run_snapshots_by_poc_id = {}
        unavailable = []
        for draft in drafts:
            try:
                receipts_by_poc_id[draft.poc_id] = (
                    self.poc_source_intake.list_receipts(draft.poc_id)
                )
            except Exception:
                projected = project_draft_dashboard((draft,), {})
                if projected.continue_working is not None:
                    unavailable.append(
                        _unavailable_draft_workspace_projection(
                            projected.continue_working
                        )
                    )
                continue
            try:
                proposal_items = self.proposal_review_service.list_proposals(
                    draft.poc_id
                )
                pending_proposal_counts_by_poc_id[draft.poc_id] = sum(
                    item.review_state == ProposalReviewState.NEEDS_REVIEW
                    for item in proposal_items
                )
                kept_proposal_counts_by_poc_id[draft.poc_id] = sum(
                    item.review_state
                    == ProposalReviewState.KEEP_FOR_CONTRACT
                    for item in proposal_items
                )
                defined_criterion_counts_by_poc_id[draft.poc_id] = sum(
                    definition.poc_id == draft.poc_id
                    for definition in (
                        self.contract_definition_service.definitions()
                    )
                )
                agreement_snapshot = (
                    self.performance_lifecycle_service.snapshot(draft.poc_id)
                )
                agreement_snapshots_by_poc_id[draft.poc_id] = (
                    agreement_snapshot
                )
                performance_run_snapshots_by_poc_id[draft.poc_id] = (
                    None
                    if agreement_snapshot.frozen_contract is None
                    else self.poc_performance_run_service.snapshot(
                        draft.poc_id
                    )
                )
            except Exception:
                projected = project_draft_dashboard(
                    (draft,),
                    {draft.poc_id: receipts_by_poc_id[draft.poc_id]},
                )
                receipts_by_poc_id.pop(draft.poc_id, None)
                pending_proposal_counts_by_poc_id.pop(draft.poc_id, None)
                kept_proposal_counts_by_poc_id.pop(draft.poc_id, None)
                defined_criterion_counts_by_poc_id.pop(draft.poc_id, None)
                agreement_snapshots_by_poc_id.pop(draft.poc_id, None)
                performance_run_snapshots_by_poc_id.pop(
                    draft.poc_id,
                    None,
                )
                if projected.continue_working is not None:
                    unavailable.append(
                        _unavailable_review_workspace_projection(
                            projected.continue_working
                        )
                    )

        available_drafts = tuple(
            draft
            for draft in drafts
            if (
                draft.poc_id in receipts_by_poc_id
                and draft.poc_id in pending_proposal_counts_by_poc_id
                and draft.poc_id in kept_proposal_counts_by_poc_id
                and draft.poc_id in defined_criterion_counts_by_poc_id
                and draft.poc_id in agreement_snapshots_by_poc_id
                and draft.poc_id in performance_run_snapshots_by_poc_id
            )
        )
        draft_active = project_draft_dashboard(
            available_drafts,
            receipts_by_poc_id,
            pending_proposal_counts_by_poc_id=(
                pending_proposal_counts_by_poc_id
            ),
            kept_proposal_counts_by_poc_id=(
                kept_proposal_counts_by_poc_id
            ),
            defined_criterion_counts_by_poc_id=(
                defined_criterion_counts_by_poc_id
            ),
            selected_filter=DashboardFilter.ACTIVE,
        )
        active_local = _newest_workspace_items_first(
            (
                *(
                    _agreement_aware_workspace_projection(
                        item,
                        agreement_snapshots_by_poc_id[item.poc_id],
                        performance_run_snapshots_by_poc_id[item.poc_id],
                    )
                    for item in draft_active.pocs
                ),
                *unavailable,
            )
        )

        draft_visible = project_draft_dashboard(
            available_drafts,
            receipts_by_poc_id,
            pending_proposal_counts_by_poc_id=(
                pending_proposal_counts_by_poc_id
            ),
            kept_proposal_counts_by_poc_id=(
                kept_proposal_counts_by_poc_id
            ),
            defined_criterion_counts_by_poc_id=(
                defined_criterion_counts_by_poc_id
            ),
            selected_filter=selected_filter,
        )
        visible_local = [
            _agreement_aware_workspace_projection(
                item,
                agreement_snapshots_by_poc_id[item.poc_id],
                performance_run_snapshots_by_poc_id[item.poc_id],
            )
            for item in draft_visible.pocs
        ]
        if selected_filter in {
            DashboardFilter.ACTIVE,
            DashboardFilter.NEEDS_ATTENTION,
        }:
            visible_local.extend(unavailable)
        visible_local = list(_newest_workspace_items_first(visible_local))

        dashboard = DashboardProjection(
            selected_filter=selected_filter,
            available_filters=seeded.available_filters,
            continue_working=(
                active_local[0] if active_local else seeded.continue_working
            ),
            pocs=tuple((*visible_local, *seeded.pocs)),
        )
        return dashboard.model_dump(mode="json")

    def server_close(self) -> None:
        """Release materialized package resources only after the server is closed."""

        try:
            super().server_close()
        finally:
            resource_stack = getattr(self, "_resource_stack", None)
            if resource_stack is not None:
                resource_stack.close()
                self._resource_stack = None


class ExitSpecDemoRequestHandler(BaseHTTPRequestHandler):
    server: ExitSpecDemoServer

    def _source_header_values(self, name: str) -> Sequence[str]:
        return tuple(self.headers.get_all(name) or ())

    def _read_bounded_source_body(
        self,
        declared_length: int,
        maximum_observed_bytes: int,
    ) -> bytes:
        """Read under fixed deadlines and reject promptly pipelined surplus."""

        body = b""
        previous_timeout = self.connection.gettimeout()
        deadline = time.monotonic() + 0.5
        try:
            while len(body) < declared_length:
                remaining_seconds = deadline - time.monotonic()
                if remaining_seconds <= 0:
                    break
                self.connection.settimeout(remaining_seconds)
                try:
                    chunk = self.rfile.read1(
                        min(declared_length - len(body), 8192)
                    )
                except (TimeoutError, OSError, ValueError):
                    break
                if not chunk:
                    break
                body += chunk

            if len(body) >= maximum_observed_bytes:
                return bytes(body[:maximum_observed_bytes])

            # A tiny bounded grace catches a pipelined request arriving just
            # after the declared body. Source connections close after one
            # response, so waiting longer would add latency without granting
            # any additional request-smuggling protection.
            if len(body) == declared_length:
                self.connection.settimeout(SOURCE_SURPLUS_GRACE_SECONDS)
                try:
                    extra = self.rfile.read1(
                        maximum_observed_bytes - len(body)
                    )
                except (TimeoutError, OSError, ValueError):
                    extra = b""
                if extra:
                    body += extra

            self.connection.setblocking(False)
            while len(body) < maximum_observed_bytes:
                try:
                    extra = self.rfile.read1(
                        maximum_observed_bytes - len(body)
                    )
                except (BlockingIOError, OSError, ValueError):
                    break
                if not extra:
                    break
                body += extra
        finally:
            self.connection.settimeout(previous_timeout)
        return bytes(body)

    def _dispatch_source_request(self) -> bool:
        if not is_source_pipeline_target(self.path):
            return False
        # Source requests never share a connection with unread or surplus bytes.
        self.close_connection = True
        request = SourceWebRequest(
            method=self.command,
            target=self.path,
            server_port=self.server.server_port,
            header_values=self._source_header_values,
            read_body=self._read_bounded_source_body,
        )
        response = handle_source_web_request(
            request,
            catalog_payload=(
                self.server.session.guided_source_catalog_payload
            ),
            import_fixture=(
                self.server.session.import_guided_source_fixture
            ),
        )
        if response is None:
            return False
        self._send_json(response.status, response.payload)
        return True

    def _dispatch_performance_read(self) -> bool:
        response = handle_performance_web_api_request(
            method=self.command,
            target=self.path,
            payload=None,
            runtime=self.server.performance_runtime,
        )
        if response is None:
            return False
        self._send_json(response.status, response.payload)
        return True

    def _dispatch_performance_write(self) -> bool:
        if not is_performance_web_api_target(self.path):
            return False
        parsed = urlparse(self.path)
        if parsed.params or parsed.query or parsed.fragment:
            response = handle_performance_web_api_request(
                method=self.command,
                target=self.path,
                payload={},
                runtime=self.server.performance_runtime,
            )
            if response is None:
                return False
            self._send_json(response.status, response.payload)
            return True
        if not self._has_json_media_type():
            self._send_json(
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                {"error": UNSUPPORTED_MEDIA_TYPE_ERROR},
            )
            return True
        if not self._has_allowed_origin(
            require_present=True,
            exact_request_origin=True,
        ):
            self._send_json(
                HTTPStatus.FORBIDDEN,
                {"error": FORBIDDEN_ORIGIN_ERROR},
            )
            return True
        try:
            payload = self._read_json()
        except ValueError:
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"error": "Performance API request is invalid."},
            )
            return True
        response = handle_performance_web_api_request(
            method=self.command,
            target=self.path,
            payload=payload,
            runtime=self.server.performance_runtime,
        )
        if response is None:
            return False
        self._send_json(response.status, response.payload)
        return True

    def _dispatch_poc_source_read(self) -> bool:
        response = handle_poc_source_web_api_request(
            method=self.command,
            target=self.path,
            payload=None,
            runtime=self.server.poc_source_intake,
        )
        if response is None:
            return False
        self._send_json(response.status, response.payload)
        return True

    def _dispatch_poc_source_write(self) -> bool:
        if not is_poc_source_web_api_target(self.path):
            return False
        parsed = urlparse(self.path)
        if parsed.params or parsed.query or parsed.fragment:
            response = handle_poc_source_web_api_request(
                method=self.command,
                target=self.path,
                payload={},
                runtime=self.server.poc_source_intake,
            )
            if response is None:
                return False
            self._send_json(response.status, response.payload)
            return True
        if not self._has_json_media_type():
            self._send_json(
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                {"error": UNSUPPORTED_MEDIA_TYPE_ERROR},
            )
            return True
        if not self._has_allowed_origin(
            require_present=True,
            exact_request_origin=True,
        ):
            self._send_json(
                HTTPStatus.FORBIDDEN,
                {"error": FORBIDDEN_ORIGIN_ERROR},
            )
            return True
        if self.headers.get_all("Idempotency-Key"):
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"error": "Source intake request is invalid."},
            )
            return True
        try:
            payload = self._read_poc_source_json()
        except OverflowError:
            self._send_json(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                {"error": "Source intake request is too large."},
            )
            return True
        except ValueError:
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"error": "Source intake request is invalid."},
            )
            return True
        response = handle_poc_source_web_api_request(
            method=self.command,
            target=self.path,
            payload=payload,
            runtime=self.server.poc_source_intake,
        )
        if response is None:
            return False
        self._send_json(response.status, response.payload)
        return True

    def _dispatch_poc_proposal_read(self) -> bool:
        response = handle_poc_proposal_web_api_request(
            method=self.command,
            target=self.path,
            payload=None,
            runtime=self.server.proposal_review_service,
        )
        if response is None:
            return False
        self._send_json(response.status, response.payload)
        return True

    def _dispatch_poc_proposal_write(self) -> bool:
        if not is_poc_proposal_web_api_target(self.path):
            return False
        parsed = urlparse(self.path)
        if parsed.params or parsed.query or parsed.fragment:
            response = handle_poc_proposal_web_api_request(
                method=self.command,
                target=self.path,
                payload={},
                runtime=self.server.proposal_review_service,
            )
            if response is None:
                return False
            self._send_json(response.status, response.payload)
            return True
        if not self._has_json_media_type():
            self._send_json(
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                {"error": UNSUPPORTED_MEDIA_TYPE_ERROR},
            )
            return True
        if not self._has_allowed_origin(
            require_present=True,
            exact_request_origin=True,
        ):
            self._send_json(
                HTTPStatus.FORBIDDEN,
                {"error": FORBIDDEN_ORIGIN_ERROR},
            )
            return True
        if self.headers.get_all("Idempotency-Key"):
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"error": "Proposal review request is invalid."},
            )
            return True
        try:
            payload = self._read_poc_source_json()
        except OverflowError:
            self._send_json(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                {"error": "Proposal review request is too large."},
            )
            return True
        except ValueError:
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"error": "Proposal review request is invalid."},
            )
            return True
        response = handle_poc_proposal_web_api_request(
            method=self.command,
            target=self.path,
            payload=payload,
            runtime=self.server.proposal_review_service,
        )
        if response is None:
            return False
        self._send_json(response.status, response.payload)
        return True

    def _dispatch_poc_contract_definition_read(self) -> bool:
        poc_id = _contract_definition_api_poc_id(
            urlparse(self.path).path
        )
        if poc_id is not None and not self._allow_active_definition_poc(
            poc_id
        ):
            return True
        response = handle_poc_contract_definition_web_api_request(
            method=self.command,
            target=self.path,
            payload=None,
            definition_runtime=self.server.contract_definition_service,
            proposal_runtime=self.server.proposal_review_service,
        )
        if response is None:
            return False
        self._send_json(response.status, response.payload)
        return True

    def _dispatch_poc_contract_definition_write(self) -> bool:
        if not is_poc_contract_definition_web_api_target(self.path):
            return False
        parsed = urlparse(self.path)
        if parsed.params or parsed.query or parsed.fragment:
            response = handle_poc_contract_definition_web_api_request(
                method=self.command,
                target=self.path,
                payload={},
                definition_runtime=self.server.contract_definition_service,
                proposal_runtime=self.server.proposal_review_service,
            )
            if response is None:
                return False
            self._send_json(response.status, response.payload)
            return True
        poc_id = _contract_definition_api_poc_id(parsed.path)
        if poc_id is not None and not self._allow_active_definition_poc(
            poc_id
        ):
            return True
        if not self._has_json_media_type():
            self._send_json(
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                {"error": UNSUPPORTED_MEDIA_TYPE_ERROR},
            )
            return True
        if not self._has_allowed_origin(
            require_present=True,
            exact_request_origin=True,
        ):
            self._send_json(
                HTTPStatus.FORBIDDEN,
                {"error": FORBIDDEN_ORIGIN_ERROR},
            )
            return True
        if self.headers.get_all("Idempotency-Key"):
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"error": "Contract definition request is invalid."},
            )
            return True
        try:
            payload = self._read_poc_source_json()
        except OverflowError:
            self._send_json(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                {"error": "Contract definition request is too large."},
            )
            return True
        except ValueError:
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"error": "Contract definition request is invalid."},
            )
            return True
        response = handle_poc_contract_definition_web_api_request(
            method=self.command,
            target=self.path,
            payload=payload,
            definition_runtime=self.server.contract_definition_service,
            proposal_runtime=self.server.proposal_review_service,
        )
        if response is None:
            return False
        self._send_json(response.status, response.payload)
        return True

    def _allow_active_definition_poc(self, poc_id: str) -> bool:
        """Keep archived or unknown local drafts outside authoring routes."""

        try:
            draft = self.server.draft_poc_service.get(poc_id)
        except ValueError:
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"error": "Contract definition request is invalid."},
            )
            return False
        except DraftPOCNotFound:
            self._send_json(
                HTTPStatus.NOT_FOUND,
                {"error": "Contract definition was not found."},
            )
            return False
        if draft.archive_state != DraftPOCArchiveState.ACTIVE:
            self._send_json(
                HTTPStatus.NOT_FOUND,
                {"error": "Contract definition was not found."},
            )
            return False
        return True

    def _dispatch_performance_agreement_read(self) -> bool:
        if not is_performance_lifecycle_web_api_target(self.path):
            return False
        poc_id = _performance_agreement_api_poc_id(
            urlparse(self.path).path
        )
        if poc_id is not None and not self._allow_active_agreement_poc(
            poc_id
        ):
            return True
        response = handle_performance_lifecycle_web_api_request(
            method=self.command,
            target=self.path,
            payload=None,
            lifecycle=self.server.performance_lifecycle_service,
            proposals=self.server.proposal_review_service,
            definitions=self.server.contract_definition_service,
        )
        if response is None:
            return False
        self._send_json(response.status, response.payload)
        return True

    def _dispatch_performance_agreement_write(self) -> bool:
        if not is_performance_lifecycle_web_api_target(self.path):
            return False
        parsed = urlparse(self.path)
        if parsed.params or parsed.query or parsed.fragment:
            response = handle_performance_lifecycle_web_api_request(
                method=self.command,
                target=self.path,
                payload={},
                lifecycle=self.server.performance_lifecycle_service,
                proposals=self.server.proposal_review_service,
                definitions=self.server.contract_definition_service,
            )
            if response is None:
                return False
            self._send_json(response.status, response.payload)
            return True
        poc_id = _performance_agreement_api_poc_id(parsed.path)
        if poc_id is not None and not self._allow_active_agreement_poc(
            poc_id
        ):
            return True
        if not self._has_json_media_type():
            self._send_json(
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                {"error": UNSUPPORTED_MEDIA_TYPE_ERROR},
            )
            return True
        if not self._has_allowed_origin(
            require_present=True,
            exact_request_origin=True,
        ):
            self._send_json(
                HTTPStatus.FORBIDDEN,
                {"error": FORBIDDEN_ORIGIN_ERROR},
            )
            return True
        if self.headers.get_all("Idempotency-Key"):
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"error": "Performance agreement request is invalid."},
            )
            return True
        try:
            payload = self._read_poc_source_json()
        except OverflowError:
            self._send_json(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                {"error": "Performance agreement request is too large."},
            )
            return True
        except ValueError:
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"error": "Performance agreement request is invalid."},
            )
            return True
        response = handle_performance_lifecycle_web_api_request(
            method=self.command,
            target=self.path,
            payload=payload,
            lifecycle=self.server.performance_lifecycle_service,
            proposals=self.server.proposal_review_service,
            definitions=self.server.contract_definition_service,
        )
        if response is None:
            return False
        self._send_json(response.status, response.payload)
        return True

    def _allow_active_agreement_poc(self, poc_id: str) -> bool:
        """Keep archived or unknown local drafts outside agreement routes."""

        try:
            draft = self.server.draft_poc_service.get(poc_id)
        except ValueError:
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"error": "Performance agreement request is invalid."},
            )
            return False
        except DraftPOCNotFound:
            self._send_json(
                HTTPStatus.NOT_FOUND,
                {"error": "Performance agreement was not found."},
            )
            return False
        if draft.archive_state != DraftPOCArchiveState.ACTIVE:
            self._send_json(
                HTTPStatus.NOT_FOUND,
                {"error": "Performance agreement was not found."},
            )
            return False
        return True

    def _dispatch_dynamic_performance_run_read(self) -> bool:
        if not is_poc_performance_run_web_api_target(self.path):
            return False
        poc_id = _poc_performance_run_api_poc_id(
            urlparse(self.path).path
        )
        if poc_id is not None and not self._allow_active_performance_run_poc(
            poc_id
        ):
            return True
        response = handle_poc_performance_run_web_api_request(
            method=self.command,
            target=self.path,
            payload=None,
            runtime=self.server.poc_performance_run_service,
        )
        if response is None:
            return False
        self._send_json(response.status, response.payload)
        return True

    def _dispatch_dynamic_performance_run_write(self) -> bool:
        if not is_poc_performance_run_web_api_target(self.path):
            return False
        parsed = urlparse(self.path)
        if parsed.params or parsed.query or parsed.fragment:
            response = handle_poc_performance_run_web_api_request(
                method=self.command,
                target=self.path,
                payload={},
                runtime=self.server.poc_performance_run_service,
            )
            if response is None:
                return False
            self._send_json(response.status, response.payload)
            return True
        poc_id = _poc_performance_run_api_poc_id(parsed.path)
        if poc_id is not None and not self._allow_active_performance_run_poc(
            poc_id
        ):
            return True
        if not self._has_json_media_type():
            self._send_json(
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                {"error": UNSUPPORTED_MEDIA_TYPE_ERROR},
            )
            return True
        if not self._has_allowed_origin(
            require_present=True,
            exact_request_origin=True,
        ):
            self._send_json(
                HTTPStatus.FORBIDDEN,
                {"error": FORBIDDEN_ORIGIN_ERROR},
            )
            return True
        if self.headers.get_all("Idempotency-Key"):
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"error": "Performance run request is invalid."},
            )
            return True
        try:
            payload = self._read_poc_source_json()
        except OverflowError:
            self._send_json(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                {"error": "Performance run request is too large."},
            )
            return True
        except ValueError:
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"error": "Performance run request is invalid."},
            )
            return True
        response = handle_poc_performance_run_web_api_request(
            method=self.command,
            target=self.path,
            payload=payload,
            runtime=self.server.poc_performance_run_service,
        )
        if response is None:
            return False
        self._send_json(response.status, response.payload)
        return True

    def _allow_active_performance_run_poc(self, poc_id: str) -> bool:
        """Keep archived or unknown local drafts outside execution routes."""

        try:
            draft = self.server.draft_poc_service.get(poc_id)
        except ValueError:
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"error": "Performance run request is invalid."},
            )
            return False
        except DraftPOCNotFound:
            self._send_json(
                HTTPStatus.NOT_FOUND,
                {"error": "Performance run was not found."},
            )
            return False
        if draft.archive_state != DraftPOCArchiveState.ACTIVE:
            self._send_json(
                HTTPStatus.NOT_FOUND,
                {"error": "Performance run was not found."},
            )
            return False
        return True

    def send_error(
        self,
        code: int,
        message: str | None = None,
        explain: str | None = None,
    ) -> None:
        """Route arbitrary parsed method tokens through the source gates."""

        if (
            code == HTTPStatus.NOT_IMPLEMENTED
            and hasattr(self, "path")
            and is_source_pipeline_target(self.path)
            and self._dispatch_source_request()
        ):
            return
        if (
            code == HTTPStatus.NOT_IMPLEMENTED
            and hasattr(self, "path")
            and is_performance_web_api_target(self.path)
            and self._dispatch_performance_read()
        ):
            return
        if (
            code == HTTPStatus.NOT_IMPLEMENTED
            and hasattr(self, "path")
            and is_poc_source_web_api_target(self.path)
            and self._dispatch_poc_source_read()
        ):
            return
        if (
            code == HTTPStatus.NOT_IMPLEMENTED
            and hasattr(self, "path")
            and is_poc_proposal_web_api_target(self.path)
            and self._dispatch_poc_proposal_read()
        ):
            return
        if (
            code == HTTPStatus.NOT_IMPLEMENTED
            and hasattr(self, "path")
            and is_poc_contract_definition_web_api_target(self.path)
            and self._dispatch_poc_contract_definition_read()
        ):
            return
        if (
            code == HTTPStatus.NOT_IMPLEMENTED
            and hasattr(self, "path")
            and is_performance_lifecycle_web_api_target(self.path)
            and self._dispatch_performance_agreement_read()
        ):
            return
        if (
            code == HTTPStatus.NOT_IMPLEMENTED
            and hasattr(self, "path")
            and is_poc_performance_run_web_api_target(self.path)
            and self._dispatch_dynamic_performance_run_read()
        ):
            return
        super().send_error(code, message, explain)

    def do_GET(self) -> None:  # noqa: N802 - stdlib request handler API
        if self._dispatch_source_request():
            return
        if self._dispatch_performance_read():
            return
        if self._dispatch_poc_source_read():
            return
        if self._dispatch_poc_proposal_read():
            return
        if self._dispatch_poc_contract_definition_read():
            return
        if self._dispatch_performance_agreement_read():
            return
        if self._dispatch_dynamic_performance_run_read():
            return
        parsed = urlparse(self.path)
        if parsed.path == "/app/pocs/new":
            if parsed.params or parsed.query or parsed.fragment:
                self._send_json(
                    HTTPStatus.BAD_REQUEST,
                    {"error": DRAFT_POC_ROUTE_PARAMETERS_ERROR},
                )
                return
            self._serve_static(parsed.path)
            return
        source_intake_poc_id = _source_intake_page_poc_id(parsed.path)
        if source_intake_poc_id is not None:
            if parsed.params or parsed.query or parsed.fragment:
                self._send_json(
                    HTTPStatus.BAD_REQUEST,
                    {"error": DRAFT_POC_ROUTE_PARAMETERS_ERROR},
                )
                return
            try:
                self.server.draft_poc_service.get(source_intake_poc_id)
            except ValueError:
                self._send_json(
                    HTTPStatus.BAD_REQUEST,
                    {"error": DRAFT_POC_INVALID_REQUEST_ERROR},
                )
                return
            except DraftPOCNotFound:
                self._send_json(
                    HTTPStatus.NOT_FOUND,
                    {"error": DRAFT_POC_NOT_FOUND_ERROR},
                )
                return
            self._serve_static(parsed.path)
            return
        proposal_review_poc_id = _proposal_review_page_poc_id(parsed.path)
        if proposal_review_poc_id is not None:
            if parsed.params or parsed.query or parsed.fragment:
                self._send_json(
                    HTTPStatus.BAD_REQUEST,
                    {"error": DRAFT_POC_ROUTE_PARAMETERS_ERROR},
                )
                return
            try:
                self.server.draft_poc_service.get(proposal_review_poc_id)
            except ValueError:
                self._send_json(
                    HTTPStatus.BAD_REQUEST,
                    {"error": DRAFT_POC_INVALID_REQUEST_ERROR},
                )
                return
            except DraftPOCNotFound:
                self._send_json(
                    HTTPStatus.NOT_FOUND,
                    {"error": DRAFT_POC_NOT_FOUND_ERROR},
                )
                return
            self._serve_static(parsed.path)
            return
        contract_definition_poc_id = _contract_definition_page_poc_id(
            parsed.path
        )
        if contract_definition_poc_id is not None:
            if parsed.params or parsed.query or parsed.fragment:
                self._send_json(
                    HTTPStatus.BAD_REQUEST,
                    {"error": DRAFT_POC_ROUTE_PARAMETERS_ERROR},
                )
                return
            try:
                draft = self.server.draft_poc_service.get(
                    contract_definition_poc_id
                )
            except ValueError:
                self._send_json(
                    HTTPStatus.BAD_REQUEST,
                    {"error": DRAFT_POC_INVALID_REQUEST_ERROR},
                )
                return
            except DraftPOCNotFound:
                self._send_json(
                    HTTPStatus.NOT_FOUND,
                    {"error": DRAFT_POC_NOT_FOUND_ERROR},
                )
                return
            if draft.archive_state != DraftPOCArchiveState.ACTIVE:
                self._send_json(
                    HTTPStatus.NOT_FOUND,
                    {"error": DRAFT_POC_NOT_FOUND_ERROR},
                )
                return
            self._serve_static(parsed.path)
            return
        performance_agreement_poc_id = _performance_agreement_page_poc_id(
            parsed.path
        )
        if performance_agreement_poc_id is not None:
            if parsed.params or parsed.query or parsed.fragment:
                self._send_json(
                    HTTPStatus.BAD_REQUEST,
                    {"error": DRAFT_POC_ROUTE_PARAMETERS_ERROR},
                )
                return
            if not self._allow_active_agreement_poc(
                performance_agreement_poc_id
            ):
                return
            self._serve_static(parsed.path)
            return
        dynamic_proof_poc_id = _dynamic_proof_page_poc_id(parsed.path)
        if dynamic_proof_poc_id is not None:
            if parsed.params or parsed.query or parsed.fragment:
                self._send_json(
                    HTTPStatus.BAD_REQUEST,
                    {"error": DRAFT_POC_ROUTE_PARAMETERS_ERROR},
                )
                return
            try:
                proof_draft = self.server.draft_poc_service.get(
                    dynamic_proof_poc_id
                )
            except (ValueError, DraftPOCNotFound):
                self._send_json(
                    HTTPStatus.NOT_FOUND,
                    {"error": "Page not found."},
                )
                return
            if proof_draft.archive_state != DraftPOCArchiveState.ACTIVE:
                self._send_json(
                    HTTPStatus.NOT_FOUND,
                    {"error": "Page not found."},
                )
                return
            try:
                self.server.performance_lifecycle_service.frozen_bundle(
                    dynamic_proof_poc_id
                )
            except PerformanceLifecycleError:
                self._send_json(
                    HTTPStatus.CONFLICT,
                    {
                        "error": (
                            "Performance proof requires a confirmed "
                            "frozen agreement."
                        )
                    },
                )
                return
            self._serve_static(parsed.path)
            return
        draft_poc_id = _draft_poc_api_id(parsed.path)
        if draft_poc_id is not None:
            if parsed.params or parsed.query or parsed.fragment:
                self._send_json(
                    HTTPStatus.BAD_REQUEST,
                    {"error": DRAFT_POC_ROUTE_PARAMETERS_ERROR},
                )
                return
            try:
                draft = self.server.draft_poc_service.get(draft_poc_id)
            except ValueError:
                self._send_json(
                    HTTPStatus.BAD_REQUEST,
                    {"error": DRAFT_POC_INVALID_REQUEST_ERROR},
                )
                return
            except DraftPOCNotFound:
                self._send_json(
                    HTTPStatus.NOT_FOUND,
                    {"error": DRAFT_POC_NOT_FOUND_ERROR},
                )
                return
            self._send_json(
                HTTPStatus.OK,
                draft.model_dump(mode="json"),
            )
            return
        if parsed.path == "/api/provider/fireworks/disclosure":
            if parsed.params or parsed.query or parsed.fragment:
                self._send_json(
                    HTTPStatus.BAD_REQUEST,
                    {"error": PROVIDER_ROUTE_PARAMETERS_ERROR},
                )
                return
            self._send_json(
                HTTPStatus.OK,
                self.server.session.wave1_provider_disclosure_payload(),
            )
            return
        if parsed.path == "/api/state":
            self._send_json(HTTPStatus.OK, self.server.session.state_payload())
            return
        if parsed.path == "/api/workspace":
            if parsed.params or parsed.fragment:
                self._send_json(
                    HTTPStatus.BAD_REQUEST,
                    {"error": WORKSPACE_FILTER_ERROR},
                )
                return
            try:
                selected_filter = _workspace_filter(parsed.query)
            except ValueError as error:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
                return
            self._send_json(
                HTTPStatus.OK,
                self.server.workspace_payload(selected_filter),
            )
            return
        if parsed.path == "/api/workspace/pocs/{0}".format(PERFORMANCE_POC_ID):
            if parsed.params or parsed.query or parsed.fragment:
                self._send_json(
                    HTTPStatus.BAD_REQUEST,
                    {
                        "error": (
                            "Performance POC detail does not accept URL parameters."
                        )
                    },
                )
                return
            try:
                payload = performance_poc_detail_payload()
            except RuntimeError:
                self._send_json(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    {"error": "Performance POC detail is unavailable."},
                )
                return
            self._send_json(HTTPStatus.OK, payload)
            return
        customer_review_token = _customer_review_api_token(parsed.path)
        if customer_review_token is not None:
            try:
                self._send_json(
                    HTTPStatus.OK,
                    self.server.session.customer_review_payload(
                        customer_review_token
                    ),
                )
            except ReviewInvitationError as error:
                status = (
                    HTTPStatus.GONE
                    if "expired" in str(error).lower()
                    else HTTPStatus.NOT_FOUND
                )
                self._send_json(status, {"error": str(error)})
            return
        if parsed.path.startswith("/artifacts/"):
            self._serve_artifact(parsed.path)
            return
        self._serve_static(parsed.path, parsed.query)

    def do_POST(self) -> None:  # noqa: N802 - stdlib request handler API
        if self._dispatch_source_request():
            return
        if self._dispatch_performance_write():
            return
        if self._dispatch_poc_source_write():
            return
        if self._dispatch_poc_proposal_write():
            return
        if self._dispatch_poc_contract_definition_write():
            return
        if self._dispatch_performance_agreement_write():
            return
        if self._dispatch_dynamic_performance_run_write():
            return
        parsed = urlparse(self.path)
        if parsed.path == "/api/pocs":
            self._create_draft_poc(parsed)
            return
        is_provider_authority_action = parsed.path in {
            "/api/provider/fireworks/authorization",
            "/api/provider/fireworks/execution",
        }
        if (
            is_provider_authority_action
            and (parsed.params or parsed.query or parsed.fragment)
        ):
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"error": PROVIDER_ROUTE_PARAMETERS_ERROR},
            )
            return
        if not self._has_json_media_type():
            self._send_json(
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                {"error": UNSUPPORTED_MEDIA_TYPE_ERROR},
            )
            return
        if not self._has_allowed_origin(
            require_present=is_provider_authority_action,
            exact_request_origin=is_provider_authority_action,
        ):
            self._send_json(
                HTTPStatus.FORBIDDEN,
                {"error": FORBIDDEN_ORIGIN_ERROR},
            )
            return

        try:
            payload = self._read_json()
            if parsed.path == "/api/provider/fireworks/authorization":
                _require_only_fields(
                    payload,
                    {
                        "acknowledged",
                        "disclosure_id",
                        "idempotency_key",
                    },
                )
                authorized = (
                    self.server.session.authorize_wave1_provider_egress(
                        disclosure_id=_required_exact_string(
                            payload,
                            "disclosure_id",
                        ),
                        acknowledged=_optional_boolean(
                            payload,
                            "acknowledged",
                        ),
                        idempotency_key=self._idempotency_key(payload),
                    )
                )
                self._send_json(HTTPStatus.OK, authorized)
                return
            if parsed.path == "/api/provider/fireworks/execution":
                _require_only_fields(payload, set())
                executed = self.server.session.execute_wave1_provider_assist(
                    configuration=self.server.wave1_provider_execution,
                    idempotency_key=(
                        self._provider_execution_idempotency_key()
                    ),
                )
                self._send_json(HTTPStatus.OK, executed)
                return
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
            customer_review_token = _customer_review_decision_token(parsed.path)
            if customer_review_token is not None:
                review_payload = self.server.session.customer_review_payload(
                    customer_review_token
                )["review"]
                _require_matching_optional(
                    payload,
                    "review_id",
                    review_payload["review_id"],
                )
                _require_matching_optional(
                    payload,
                    "contract_id",
                    review_payload["contract_id"],
                )
                _require_matching_optional(
                    payload,
                    "contract_version",
                    review_payload["contract_version"],
                )
                decision = _required_string(payload, "decision")
                agreement_acknowledged = _optional_boolean(
                    payload,
                    "agreement_acknowledged",
                )
                if (
                    decision.upper() == "CONFIRM"
                    and not agreement_acknowledged
                ):
                    raise ValueError(
                        "agreement_acknowledged must be true when confirming "
                        "the agreement."
                    )
                rationale = _optional_string(payload, "rationale")
                if decision.upper() == "REQUEST_CHANGES" and rationale is None:
                    raise DemoStateError(
                        "A rationale is required when requesting changes."
                    )
                if rationale is None:
                    rationale = (
                        "Customer confirmed that this exact contract version "
                        "matches the intended POC agreement."
                    )
                confirmation, replayed = self.server.session.record_customer_decision(
                    customer_review_token,
                    decision=decision,
                    confirmer=(
                        _optional_string(payload, "confirmer")
                        or "Customer approver · local synthetic demo"
                    ),
                    agreement_acknowledged=agreement_acknowledged,
                    rationale=rationale,
                    idempotency_key=self._idempotency_key(payload),
                )
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "confirmation": self.server.session._confirmation_payload(),
                        "decision": self.server.session._customer_decision_payload(
                            confirmation,
                            idempotent_replay=replayed,
                        ),
                        "review": self.server.session.customer_review_payload(
                            customer_review_token
                        )["review"],
                        "confirmation_id": confirmation.confirmation_id,
                        "idempotent_replay": replayed,
                    },
                )
                return
            if parsed.path == "/api/freeze":
                self.server.session.freeze()
                self._send_json(HTTPStatus.OK, self.server.session.state_payload())
                return
            if parsed.path == "/api/revision/start":
                self.server.session.start_revision()
                self._send_json(HTTPStatus.OK, self.server.session.state_payload())
                return
            if parsed.path == "/api/draft/define":
                if "normalized_claim" in payload:
                    raise ValueError(
                        "normalized_claim is generated from the structured rule "
                        "fields and cannot be supplied."
                    )
                defined = self.server.session.define_draft_rule(
                    draft_id=_required_string(payload, "draft_id"),
                    title=_required_string(payload, "title"),
                    threshold_percent=_required_number(
                        payload,
                        "threshold_percent",
                    ),
                    minimum_samples=_required_integer(
                        payload,
                        "minimum_samples",
                    ),
                    workload_slice=_required_string(
                        payload,
                        "workload_slice",
                    ),
                )
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "defined_draft": defined.model_dump(mode="json"),
                        "state": self.server.session.state_payload(),
                    },
                )
                return
            if parsed.path == "/api/revision/edit":
                if "normalized_claim" in payload:
                    raise ValueError(
                        "normalized_claim is generated from the structured rule "
                        "fields and cannot be supplied."
                    )
                revised = self.server.session.edit_revision(
                    draft_id=_required_string(payload, "draft_id"),
                    title=_required_string(payload, "title"),
                    threshold_percent=_required_number(
                        payload,
                        "threshold_percent",
                    ),
                    minimum_samples=_required_integer(
                        payload,
                        "minimum_samples",
                    ),
                    workload_slice=_required_string(
                        payload,
                        "workload_slice",
                    ),
                )
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "revised_draft": revised.model_dump(mode="json"),
                        "state": self.server.session.state_payload(),
                    },
                )
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
                    customer_terms=_optional_string_list(payload, "customer_terms"),
                )
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "state": self.server.session.state_payload(),
                        "notice": (
                            "Synthetic source notes were redacted and captured. "
                            "Candidate claims remain unresolved until a human "
                            "defines a complete measurement rule."
                        ),
                    },
                )
                return
            if parsed.path == "/api/assisted-intake":
                self.server.session.assisted_intake(
                    pasted_text=_required_string(payload, "transcript"),
                    title=_optional_string(payload, "title")
                    or "Assisted discovery transcript",
                    customer_terms=_optional_string_list(payload, "customer_terms"),
                )
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "state": self.server.session.state_payload(),
                        "notice": (
                            "Synthetic assisted authoring created review-only drafts "
                            "after redaction. No provider call, approval, freeze, or "
                            "verdict was performed."
                        ),
                    },
                )
                return
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "Unknown API route."})
        except DemoStateError as error:
            payload = {"error": str(error)}
            error_code = getattr(error, "code", None)
            if isinstance(error_code, str) and error_code:
                payload["code"] = error_code
            self._send_json(HTTPStatus.CONFLICT, payload)
        except ReviewInvitationError as error:
            status = (
                HTTPStatus.GONE
                if "expired" in str(error).lower()
                else HTTPStatus.NOT_FOUND
            )
            self._send_json(status, {"error": str(error)})
        except ValueError as error:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(error)})

    def _create_draft_poc(self, parsed: Any) -> None:
        """Create identity only; this route owns no workflow authority."""

        if parsed.params or parsed.query or parsed.fragment:
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"error": DRAFT_POC_ROUTE_PARAMETERS_ERROR},
            )
            return
        if not self._has_json_media_type():
            self._send_json(
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                {"error": UNSUPPORTED_MEDIA_TYPE_ERROR},
            )
            return
        if not self._has_allowed_origin(
            require_present=True,
            exact_request_origin=True,
        ):
            self._send_json(
                HTTPStatus.FORBIDDEN,
                {"error": FORBIDDEN_ORIGIN_ERROR},
            )
            return

        try:
            payload = self._read_json()
            _require_only_fields(
                payload,
                {
                    "display_name",
                    "customer_label",
                    "use_case",
                    "owner",
                    "first_source_choice",
                    "idempotency_key",
                },
            )
            idempotency_key = self._idempotency_key(payload)
            request = DraftPOCCreateRequest.model_validate(
                {
                    key: value
                    for key, value in payload.items()
                    if key != "idempotency_key"
                }
            )
            result = self.server.draft_poc_service.create(
                request,
                idempotency_key=idempotency_key,
            )
        except DraftPOCIdempotencyConflict:
            self._send_json(
                HTTPStatus.CONFLICT,
                {"error": DRAFT_POC_CONFLICT_ERROR},
            )
            return
        except DuplicateDraftPOCId:
            self._send_json(
                HTTPStatus.CONFLICT,
                {"error": "Draft POC identity is already in use."},
            )
            return
        except DraftPOCCapacityExceeded:
            self._send_json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"error": DRAFT_POC_CAPACITY_ERROR},
            )
            return
        except (TypeError, ValueError, ValidationError):
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"error": DRAFT_POC_INVALID_REQUEST_ERROR},
            )
            return

        response = result.draft.model_dump(mode="json")
        response["idempotent_replay"] = result.idempotent_replay
        self._send_json(
            HTTPStatus.OK if result.idempotent_replay else HTTPStatus.CREATED,
            response,
        )

    def _unsupported_method(self) -> None:
        if self._dispatch_source_request():
            return
        self.send_error(
            HTTPStatus.NOT_IMPLEMENTED,
            "Unsupported method ({0!r})".format(self.command),
        )

    def do_DELETE(self) -> None:  # noqa: N802 - stdlib request handler API
        self._unsupported_method()

    def do_CONNECT(self) -> None:  # noqa: N802 - stdlib request handler API
        self._unsupported_method()

    def do_HEAD(self) -> None:  # noqa: N802 - stdlib request handler API
        self._unsupported_method()

    def do_OPTIONS(self) -> None:  # noqa: N802 - stdlib request handler API
        self._unsupported_method()

    def do_PATCH(self) -> None:  # noqa: N802 - stdlib request handler API
        self._unsupported_method()

    def do_PUT(self) -> None:  # noqa: N802 - stdlib request handler API
        self._unsupported_method()

    def do_TRACE(self) -> None:  # noqa: N802 - stdlib request handler API
        self._unsupported_method()

    def _has_json_media_type(self) -> bool:
        content_types = self.headers.get_all("Content-Type") or []
        if len(content_types) != 1:
            return False

        media_type, separator, parameter = content_types[0].partition(";")
        if media_type.strip().lower() != JSON_MEDIA_TYPE:
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
            and (
                character.isalnum()
                or character in "!#$%&'*+-.^_`|~"
            )
            for character in charset
        )

    def _idempotency_key(self, payload: Dict[str, Any]) -> str:
        header_values = self.headers.get_all("Idempotency-Key") or []
        if len(header_values) > 1:
            raise ValueError("Idempotency-Key must be provided at most once.")
        header_value = header_values[0].strip() if header_values else None
        body_value = _optional_string(payload, "idempotency_key")
        if header_value and body_value and header_value != body_value:
            raise ValueError(
                "Header and body idempotency keys must match when both are provided."
            )
        resolved = header_value or body_value
        if not resolved:
            raise ValueError("An idempotency key is required.")
        if len(resolved) > 200:
            raise ValueError("Idempotency key must be at most 200 characters.")
        return resolved

    def _provider_execution_idempotency_key(self) -> str:
        values = self.headers.get_all("Idempotency-Key") or []
        if len(values) != 1:
            raise ValueError(
                "Provider execution requires one Idempotency-Key header."
            )
        value = values[0]
        if (
            not value
            or len(value) > 200
            or value != value.strip()
            or any(character.isspace() for character in value)
        ):
            raise ValueError(
                "Provider execution Idempotency-Key must be exact and "
                "at most 200 characters."
            )
        return value

    def _has_allowed_origin(
        self,
        *,
        require_present: bool = False,
        exact_request_origin: bool = False,
    ) -> bool:
        origins = self.headers.get_all("Origin") or []
        if not origins:
            return not require_present
        if len(origins) != 1:
            return False

        origin = origins[0]
        if origin != origin.strip():
            return False
        try:
            parsed = urlparse(origin)
            hostname = parsed.hostname
            port = parsed.port
        except ValueError:
            return False

        if (
            parsed.scheme.lower() != "http"
            or hostname not in LOOPBACK_ORIGIN_HOSTS
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path
            or parsed.params
            or parsed.query
            or parsed.fragment
        ):
            return False

        expected_host = "[::1]" if hostname == "::1" else hostname
        expected_authority = (
            expected_host if port is None else "{0}:{1}".format(expected_host, port)
        )
        authority_start = origin.find("://") + 3
        if (
            authority_start < 3
            or origin[authority_start:] != parsed.netloc
            or parsed.netloc.lower() != expected_authority
        ):
            return False
        effective_port = 80 if port is None else port
        if effective_port != self.server.server_port:
            return False
        if not exact_request_origin:
            return True

        hosts = self.headers.get_all("Host") or []
        if len(hosts) != 1:
            return False
        host = hosts[0]
        if host != host.strip():
            return False
        try:
            request_authority = urlparse("http://{0}".format(host))
            request_hostname = request_authority.hostname
            request_port = request_authority.port
        except ValueError:
            return False
        if (
            request_hostname not in LOOPBACK_ORIGIN_HOSTS
            or request_authority.username is not None
            or request_authority.password is not None
            or request_authority.path
            or request_authority.params
            or request_authority.query
            or request_authority.fragment
        ):
            return False
        request_host = (
            "[::1]" if request_hostname == "::1" else request_hostname
        )
        expected_request_authority = (
            request_host
            if request_port is None
            else "{0}:{1}".format(request_host, request_port)
        )
        if request_authority.netloc.lower() != expected_request_authority:
            return False
        request_effective_port = 80 if request_port is None else request_port
        return (
            request_effective_port == self.server.server_port
            and parsed.netloc.lower() == request_authority.netloc.lower()
        )

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

    def _read_poc_source_json(self) -> Dict[str, Any]:
        """Read strict bounded JSON without accepting duplicate object keys."""

        content_length = self.headers.get("Content-Length")
        if content_length is None:
            raise ValueError("Content-Length is required.")
        try:
            size = int(content_length)
        except ValueError as error:
            raise ValueError("Content-Length must be an integer.") from error
        if size < 0:
            raise ValueError("Content-Length must not be negative.")
        if size > MAX_REQUEST_BYTES:
            raise OverflowError("Source intake request body is too large.")
        body = self.rfile.read(size)

        def reject_duplicate_pairs(
            pairs: List[Tuple[str, Any]],
        ) -> Dict[str, Any]:
            parsed_object: Dict[str, Any] = {}
            for key, value in pairs:
                if key in parsed_object:
                    raise ValueError("Duplicate JSON object key.")
                parsed_object[key] = value
            return parsed_object

        def reject_nonfinite(_: str) -> Any:
            raise ValueError("Non-finite JSON number.")

        try:
            payload = json.loads(
                body.decode("utf-8"),
                object_pairs_hook=reject_duplicate_pairs,
                parse_constant=reject_nonfinite,
            )
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError(
                "Request body must be valid UTF-8 JSON."
            ) from error
        if type(payload) is not dict:
            raise ValueError("Request body must be a JSON object.")
        return payload

    def _serve_static(self, request_path: str, query: str = "") -> None:
        if request_path in ("", "/", "/app", "/app/"):
            relative = (
                "index.html"
                if _is_compatibility_workbench_query(query)
                else "dashboard.html"
            )
        elif request_path == "/app/pocs/new":
            relative = "new_poc.html"
        elif _source_intake_page_poc_id(request_path) is not None:
            relative = "source_intake.html"
        elif _proposal_review_page_poc_id(request_path) is not None:
            relative = "proposal_review.html"
        elif _contract_definition_page_poc_id(request_path) is not None:
            relative = "contract_definition.html"
        elif _performance_agreement_page_poc_id(request_path) is not None:
            relative = "agreement.html"
        elif _dynamic_proof_page_poc_id(request_path) is not None:
            relative = "proof.html"
        elif request_path.strip("/") == (
            "app/pocs/{0}".format(SYNTHETIC_SUPPORT_AGENT_POC_ID)
        ):
            relative = "index.html"
        elif request_path.strip("/") == (
            "app/pocs/{0}".format(PERFORMANCE_POC_ID)
        ):
            relative = "performance.html"
        elif _is_customer_review_page(request_path):
            relative = "review.html"
        else:
            relative = request_path.lstrip("/")
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


def _required_exact_string(payload: Dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError("{0} must be a non-empty string.".format(key))
    if value != value.strip():
        raise ValueError(
            "{0} must match exactly without surrounding whitespace.".format(key)
        )
    return value


def _require_only_fields(payload: Dict[str, Any], allowed: set[str]) -> None:
    if not set(payload).issubset(allowed):
        raise ValueError("Request contains unsupported fields.")


def _optional_string(payload: Dict[str, Any], key: str) -> Optional[str]:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("{0} must be a string when provided.".format(key))
    return value.strip() or None


def _required_number(payload: Dict[str, Any], key: str) -> float:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("{0} must be a number.".format(key))
    resolved = float(value)
    if not math.isfinite(resolved):
        raise ValueError("{0} must be finite.".format(key))
    return resolved


def _required_integer(payload: Dict[str, Any], key: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("{0} must be an integer.".format(key))
    return value


def _optional_boolean(
    payload: Dict[str, Any],
    key: str,
    *,
    default: bool = False,
) -> bool:
    if key not in payload:
        return default
    value = payload[key]
    if not isinstance(value, bool):
        raise ValueError("{0} must be a boolean.".format(key))
    return value


def _optional_string_list(payload: Dict[str, Any], key: str) -> List[str]:
    value = payload.get(key)
    if value is None:
        return []
    if not isinstance(value, list) or any(
        not isinstance(item, str) for item in value
    ):
        raise ValueError("{0} must be an array of strings when provided.".format(key))
    return value


def _require_matching_optional(
    payload: Dict[str, Any],
    key: str,
    expected: str,
) -> None:
    value = _optional_string(payload, key)
    if value is not None and value != expected:
        raise DemoStateError(
            "{0} does not match this customer review link.".format(key)
        )


def _safe_child(root: Path, relative: str) -> Optional[Path]:
    try:
        decoded = unquote(relative)
        target = (root / decoded).resolve()
        target.relative_to(root.resolve())
    except (OSError, ValueError):
        return None
    return target


def _workspace_filter(query: str) -> DashboardFilter:
    if not query:
        return DashboardFilter.ACTIVE
    try:
        fields = parse_qsl(query, keep_blank_values=True, strict_parsing=True)
    except ValueError as error:
        raise ValueError(WORKSPACE_FILTER_ERROR) from error
    if len(fields) != 1 or fields[0][0] != "filter":
        raise ValueError(WORKSPACE_FILTER_ERROR)
    try:
        return DashboardFilter(fields[0][1])
    except ValueError as error:
        raise ValueError(WORKSPACE_FILTER_ERROR) from error


def _is_compatibility_workbench_query(query: str) -> bool:
    try:
        fields = parse_qsl(query, keep_blank_values=True)
    except ValueError:
        return False
    return any(
        (key, value) in {("intake", "email"), ("mode", "recording")}
        for key, value in fields
    )


def _is_customer_review_page(request_path: str) -> bool:
    """Match one opaque review-token segment without reflecting it into markup."""

    parts = request_path.strip("/").split("/")
    return len(parts) == 2 and parts[0] == "review" and bool(parts[1])


def _customer_review_api_token(request_path: str) -> Optional[str]:
    parts = request_path.strip("/").split("/")
    if len(parts) != 3 or parts[:2] != ["api", "review"]:
        return None
    token = unquote(parts[2])
    if not token or "/" in token or "\\" in token:
        return None
    return token


def _customer_review_decision_token(request_path: str) -> Optional[str]:
    parts = request_path.strip("/").split("/")
    if (
        len(parts) != 4
        or parts[:2] != ["api", "review"]
        or parts[3] != "decision"
    ):
        return None
    token = unquote(parts[2])
    if not token or "/" in token or "\\" in token:
        return None
    return token


def _draft_poc_api_id(request_path: str) -> Optional[str]:
    """Return one decoded POC identity without accepting nested path authority."""

    parts = request_path.strip("/").split("/")
    if len(parts) != 3 or parts[:2] != ["api", "pocs"]:
        return None
    poc_id = unquote(parts[2])
    if not poc_id or "/" in poc_id or "\\" in poc_id:
        return None
    return poc_id


def _source_intake_page_poc_id(request_path: str) -> Optional[str]:
    """Return the one POC identity in an exact source-intake page route."""

    parts = request_path.strip("/").split("/")
    if (
        len(parts) != 5
        or parts[:2] != ["app", "pocs"]
        or parts[3:] != ["sources", "new"]
    ):
        return None
    poc_id = unquote(parts[2])
    if not poc_id or "/" in poc_id or "\\" in poc_id:
        return None
    return poc_id


def _proposal_review_page_poc_id(request_path: str) -> Optional[str]:
    """Return the one POC identity in an exact proposal-review page route."""

    parts = request_path.strip("/").split("/")
    if (
        len(parts) != 4
        or parts[:2] != ["app", "pocs"]
        or parts[3] != "review"
    ):
        return None
    poc_id = unquote(parts[2])
    if not poc_id or "/" in poc_id or "\\" in poc_id:
        return None
    return poc_id


def _contract_definition_page_poc_id(
    request_path: str,
) -> Optional[str]:
    """Return the one POC identity in an exact definition page route."""

    parts = request_path.strip("/").split("/")
    if (
        len(parts) != 4
        or parts[:2] != ["app", "pocs"]
        or parts[3] != "define"
    ):
        return None
    poc_id = unquote(parts[2])
    if not poc_id or "/" in poc_id or "\\" in poc_id:
        return None
    return poc_id


def _contract_definition_api_poc_id(
    request_path: str,
) -> Optional[str]:
    """Return the POC identity for the exact definition collection API."""

    parts = request_path.strip("/").split("/")
    if (
        len(parts) != 4
        or parts[:2] != ["api", "pocs"]
        or parts[3] != "definitions"
    ):
        return None
    poc_id = unquote(parts[2])
    if not poc_id or "/" in poc_id or "\\" in poc_id:
        return None
    return poc_id


def _performance_agreement_page_poc_id(
    request_path: str,
) -> Optional[str]:
    """Return the POC identity in one exact agreement workbench route."""

    parts = request_path.strip("/").split("/")
    if (
        len(parts) != 4
        or parts[:2] != ["app", "pocs"]
        or parts[3] != "agreement"
    ):
        return None
    poc_id = unquote(parts[2])
    if not poc_id or "/" in poc_id or "\\" in poc_id:
        return None
    return poc_id


def _performance_agreement_api_poc_id(
    request_path: str,
) -> Optional[str]:
    """Return the POC identity in an exact agreement lifecycle API route."""

    parts = request_path.strip("/").split("/")
    if (
        len(parts) not in {4, 5}
        or parts[:2] != ["api", "pocs"]
        or parts[3] != "agreement"
        or (len(parts) == 5 and parts[4] not in {"confirm", "freeze"})
    ):
        return None
    poc_id = unquote(parts[2])
    if not poc_id or "/" in poc_id or "\\" in poc_id:
        return None
    return poc_id


def _poc_performance_run_api_poc_id(
    request_path: str,
) -> Optional[str]:
    """Return the POC identity in one exact dynamic run API route."""

    parts = request_path.strip("/").split("/")
    if (
        len(parts) not in {4, 5}
        or parts[:2] != ["api", "pocs"]
        or parts[3] not in {"runs", "evidence"}
        or (parts[3] == "evidence" and len(parts) != 4)
    ):
        return None
    poc_id = unquote(parts[2])
    if not poc_id or "/" in poc_id or "\\" in poc_id:
        return None
    return poc_id


def _dynamic_proof_page_poc_id(
    request_path: str,
) -> Optional[str]:
    """Return one local draft identity in an exact base POC page route."""

    parts = request_path.strip("/").split("/")
    if len(parts) != 3 or parts[:2] != ["app", "pocs"]:
        return None
    poc_id = unquote(parts[2])
    if (
        not poc_id
        or "/" in poc_id
        or "\\" in poc_id
        or poc_id
        in {SYNTHETIC_SUPPORT_AGENT_POC_ID, PERFORMANCE_POC_ID}
    ):
        return None
    return poc_id


def serve_demo(
    host: str = "127.0.0.1",
    port: int = 8765,
    output_root: Path = DEFAULT_RUNS_ROOT,
    open_browser: bool = False,
    *,
    enable_fireworks: bool = False,
    fireworks_api_key: object = None,
) -> ExitSpecDemoServer:
    """Start the local-only server. The caller owns ``serve_forever`` lifecycle."""

    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("ExitSpec demo only binds to a loopback address.")
    if not STATIC_ROOT.is_dir():
        raise RuntimeError("ExitSpec static demo assets are unavailable.")
    resource_stack = ExitStack()
    try:
        demo_paths = resource_stack.enter_context(support_agent_demo_paths())
        session = DemoSession.synthetic_support_agent(
            output_root,
            discovery_path=demo_paths.discovery_pack,
            contract_seed_path=demo_paths.contract_seed,
            fixture_path=demo_paths.fixture,
        )
        provider_execution = Wave1ProviderExecutionConfiguration(
            enabled=enable_fireworks,
            api_key=fireworks_api_key,
        )
        server = ExitSpecDemoServer(
            (host, port),
            session,
            resource_stack=resource_stack,
            wave1_provider_execution=provider_execution,
            performance_fireworks_api_key=(
                fireworks_api_key if enable_fireworks else None
            ),
        )
    except Exception:
        resource_stack.close()
        raise
    if open_browser:
        threading.Timer(
            0.15,
            lambda: webbrowser.open("http://{0}:{1}".format(host, server.server_port)),
        ).start()
    return server
