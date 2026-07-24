"""A local-only browser demo for ExitSpec's Define -> Prove -> Decide loop.

The server deliberately has no authentication, persistence, provider credentials, or
network integrations. It is a runnable product demo over the synthetic support-agent
fixture, not an authorization service. Human review actions live only in process.
"""

from __future__ import annotations

import json
import math
import mimetypes
import tempfile
import threading
import uuid
import webbrowser
from dataclasses import dataclass, field
from functools import wraps
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import unquote, urlparse

import yaml

from .adapters.deterministic_tool_selection import DeterministicToolSelectionAdapter
from .authoring import (
    approve_draft,
    assemble_approved_contract,
    edit_draft,
    load_contract_seed,
    load_discovery_pack,
    reject_draft,
)
from .confirmations import (
    ConfirmationDecision,
    ContractConfirmation,
    confirmation_matches_contract,
    confirmation_operation_id,
    contract_confirmation_fingerprint,
    record_confirmation,
)
from .contracts import freeze_confirmed_contract, utc_now as contract_utc_now
from .intake import (
    TranscriptIntakeError,
    TranscriptRedactionSummary,
    redact_and_parse_pasted_transcript,
)
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
from .review_links import (
    CustomerReviewInvitation,
    ReviewInvitationError,
    issue_customer_review_invitation,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_ROOT = PROJECT_ROOT / "examples" / "support-agent"
DEFAULT_DISCOVERY_PATH = EXAMPLE_ROOT / "authoring" / "discovery-pack-v1.json"
DEFAULT_CONTRACT_SEED_PATH = EXAMPLE_ROOT / "authoring" / "contract-seed-v1.json"
DEFAULT_FIXTURE_PATH = EXAMPLE_ROOT / "fixtures" / "tool-selection-200.json"
DEFAULT_RUNS_ROOT = PROJECT_ROOT / "runs"
STATIC_ROOT = Path(__file__).resolve().parent / "static"
MAX_REQUEST_BYTES = 128 * 1024
JSON_MEDIA_TYPE = "application/json"
LOOPBACK_ORIGIN_HOSTS = {"127.0.0.1", "localhost", "::1"}
UNSUPPORTED_MEDIA_TYPE_ERROR = "Content-Type must be application/json."
FORBIDDEN_ORIGIN_ERROR = "Origin is not allowed."


class DemoStateError(ValueError):
    """A user-visible constraint in the local demo workflow."""


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
    _lock: Any = field(
        default_factory=threading.RLock,
        init=False,
        repr=False,
        compare=False,
    )

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
        self.discovery_pack = DiscoveryPack(transcript=transcript, drafts=candidates)
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

    @_serialized_session
    def reset_to_synthetic_sample(self) -> None:
        """Restore the deterministic support-agent demonstration without disk writes."""

        fresh = self.synthetic_support_agent(output_root=self.output_root)
        self.discovery_pack = fresh.discovery_pack
        self.contract_seed = fresh.contract_seed
        self.fixture_path = fresh.fixture_path
        self.reviewed_drafts = fresh.reviewed_drafts
        self.revision_request = None
        self.revision_parent_version = None
        self.revision_edit_applied_ids.clear()
        self._invalidate_customer_agreement()
        self.transcript_notice = fresh.transcript_notice
        self.transcript_redaction = None

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

        self.output_root.mkdir(parents=True, exist_ok=True)
        run_id = "web-{0}-{1}".format(scenario, uuid.uuid4().hex[:12])
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
            self.last_run = run_demo(
                contract_path=contract_path,
                fixture_path=self.fixture_path,
                scenario=scenario,
                output_root=self.output_root,
                run_id=run_id,
            )
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
        return draft_path

    @_serialized_session
    def customer_review_payload(self, token: str) -> Dict[str, Any]:
        """Return only the customer-safe agreement projection for one valid link."""

        invitation = self.customer_review_invitation
        contract = self.approved_contract()
        if invitation is None or contract is None:
            raise ReviewInvitationError("Customer review link is invalid.")
        invitation.require_valid(token)
        fingerprint = contract_confirmation_fingerprint(contract)
        if (
            invitation.contract_id != contract.id
            or invitation.contract_version != contract.version
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
            for criterion in contract.criteria
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
                "contract_id": contract.id,
                "contract_version": contract.version,
                "confirmation_fingerprint": fingerprint,
                "customer": contract.customer,
                "use_case": contract.use_case,
                "poc": {
                    "title": contract.use_case,
                    "customer_name": contract.customer,
                },
                "contract": {
                    "id": contract.id,
                    "version": contract.version,
                    "confirmation_fingerprint": fingerprint,
                    "excluded": list(contract.non_goals),
                    "criteria": customer_criteria,
                },
                "target_system": contract.target_system.model_dump(mode="json"),
                "criteria": customer_criteria,
                "non_goals": list(contract.non_goals),
                "evidence_retention_policy": contract.evidence_retention_policy,
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
                "decision": decision_payload,
            },
            "confirmation": self._confirmation_payload(),
        }

    @staticmethod
    def _customer_criterion_payload(criterion: Any) -> Dict[str, Any]:
        source = (
            None
            if criterion.source is None
            else criterion.source.model_dump(mode="json")
        )
        metric_name = criterion.metric.value
        metric_label = {
            "exact_tool_selection_rate": "Exact tool-selection rate",
        }.get(metric_name, metric_name.replace("_", " ").capitalize())
        workload_label = (
            criterion.workload_slice.replace("-", " ").replace("_", " ").capitalize()
        )
        operator = {
            "gte": "at least",
            "gt": "more than",
            "lte": "at most",
            "lt": "less than",
            "eq": "exactly",
        }.get(criterion.rule.operator.value, criterion.rule.operator.value)
        threshold = "{0} {1:.2f}%".format(
            operator,
            criterion.rule.threshold * 100,
        )
        sample = "{0} or more fixed cases".format(
            criterion.rule.minimum_samples
        )
        return {
            "id": criterion.id,
            "title": criterion.title,
            "normalized_claim": criterion.normalized_claim,
            "plain_language": criterion.normalized_claim,
            "source": source,
            "source_quote": (
                "Human-added requirement"
                if criterion.source is None
                else criterion.source.quote
            ),
            "metric": metric_label,
            "unit": criterion.unit,
            "aggregation": criterion.aggregation,
            "rule": criterion.rule.model_dump(mode="json"),
            "threshold": threshold,
            "sample": sample,
            "workload": workload_label,
            "workload_slice": criterion.workload_slice,
            "evidence_policy": criterion.evidence_policy,
            "must_have": criterion.must_have,
            "required": criterion.must_have,
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
        normalized_claim: str,
        threshold_percent: float,
        minimum_samples: int,
        workload_slice: str,
    ) -> CriterionDraft:
        """Apply an explicit structured edit before the revised draft is reviewed."""

        if self.revision_request is None:
            raise DemoStateError("No customer-requested revision is active.")
        if not normalized_claim.strip() or not workload_slice.strip():
            raise DemoStateError("Revision fields must be non-empty.")
        if not 0 < threshold_percent <= 100:
            raise DemoStateError("Threshold must be greater than 0 and at most 100.")
        if minimum_samples <= 0:
            raise DemoStateError("Minimum samples must be greater than zero.")

        for index, draft in enumerate(self.reviewed_drafts):
            if draft.id != draft_id:
                continue
            if draft.status != DraftStatus.NEEDS_REVIEW:
                raise DemoStateError("Only a draft needing review can be revised.")
            criterion = draft.proposed_criterion
            if criterion is None:
                raise DemoStateError(
                    "This request has no structured criterion to revise."
                )
            rule_payload = criterion.rule.model_dump(mode="python")
            rule_payload.update(
                {
                    "threshold": threshold_percent / 100,
                    "minimum_samples": minimum_samples,
                }
            )
            criterion_payload = criterion.model_dump(mode="python")
            criterion_payload.update(
                {
                    "approved": False,
                    "normalized_claim": normalized_claim.strip(),
                    "rule": type(criterion.rule).model_validate(rule_payload),
                    "workload_slice": workload_slice.strip(),
                }
            )
            revised = edit_draft(
                draft,
                {
                    "normalized_claim": normalized_claim.strip(),
                    "proposed_criterion": type(criterion).model_validate(
                        criterion_payload
                    ),
                    "open_questions": [],
                },
            )
            self.reviewed_drafts[index] = revised
            self.revision_edit_applied_ids.add(draft_id)
            self._invalidate_customer_agreement()
            return revised
        raise DemoStateError("Unknown draft {0}.".format(draft_id))

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
        return self.frozen_contract

    @_serialized_session
    def state_payload(self) -> Dict[str, Any]:
        reviewed_contract = self.approved_contract()
        contract = self.frozen_contract or reviewed_contract
        ready_to_freeze = bool(
            reviewed_contract is not None
            and self.frozen_contract is None
            and self.customer_confirmation is not None
            and confirmation_matches_contract(
                reviewed_contract,
                self.customer_confirmation,
            )
        )
        return {
            "mode": "local_synthetic_demo",
            "safety": {
                "synthetic_only": True,
                "provider_calls": False,
                "authorization": "ExitSpec proves evidence; humans retain every approval decision.",
            },
            "transcript_notice": self.transcript_notice,
            "transcript_redaction": (
                None
                if self.transcript_redaction is None
                else self.transcript_redaction.model_dump(mode="json")
            ),
            "transcript": self.discovery_pack.transcript.model_dump(mode="json"),
            "drafts": [draft.model_dump(mode="json") for draft in self.reviewed_drafts],
            "contract": None if contract is None else contract.model_dump(mode="json"),
            "confirmation": self._confirmation_payload(),
            "revision_request": self.revision_request,
            "revision_edit_applied_ids": sorted(self.revision_edit_applied_ids),
            "ready_to_prepare_customer_review": reviewed_contract is not None,
            "ready_to_freeze": ready_to_freeze,
            "ready_to_prove": self.frozen_contract is not None,
            "supported_scenarios": list(
                DeterministicToolSelectionAdapter().scenarios
            ),
            "customer_draft_url": self._customer_draft_url(),
            "customer_review_url": self._customer_review_url(),
            "proof_pack": self._proof_payload(),
        }

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
        self._serve_static(parsed.path)

    def do_POST(self) -> None:  # noqa: N802 - stdlib request handler API
        if not self._has_json_media_type():
            self._send_json(
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                {"error": UNSUPPORTED_MEDIA_TYPE_ERROR},
            )
            return
        if not self._has_allowed_origin():
            self._send_json(
                HTTPStatus.FORBIDDEN,
                {"error": FORBIDDEN_ORIGIN_ERROR},
            )
            return

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
            if parsed.path == "/api/revision/edit":
                revised = self.server.session.edit_revision(
                    draft_id=_required_string(payload, "draft_id"),
                    normalized_claim=_required_string(
                        payload,
                        "normalized_claim",
                    ),
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
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "Unknown API route."})
        except DemoStateError as error:
            self._send_json(HTTPStatus.CONFLICT, {"error": str(error)})
        except ReviewInvitationError as error:
            status = (
                HTTPStatus.GONE
                if "expired" in str(error).lower()
                else HTTPStatus.NOT_FOUND
            )
            self._send_json(status, {"error": str(error)})
        except ValueError as error:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(error)})

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

    def _has_allowed_origin(self) -> bool:
        origins = self.headers.get_all("Origin") or []
        if not origins:
            return True
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
        return effective_port == self.server.server_port

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
        if request_path in ("", "/", "/app", "/app/"):
            relative = "index.html"
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
