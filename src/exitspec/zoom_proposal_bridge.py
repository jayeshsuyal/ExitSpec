"""Zoom transcript handoff into ExitSpec's existing source/proposal spine.

The bridge creates one local draft POC, reuses the existing meeting source
intake and proposal-review models, and records complete digest-only Zoom
provenance. It has no customer-confirmation, freeze, proof, evidence, or
verdict authority, and it does not call a provider.
"""

from __future__ import annotations

from enum import Enum
import hashlib
import re
from threading import RLock
from typing import Any, Literal

from pydantic import ConfigDict, Field, field_validator, model_validator

from .canonical import canonical_json_bytes
from .models import FrozenExitSpecModel, Metric, SHA256_PATTERN
from .poc_creation import (
    DraftPOCCreateRequest,
    DraftPOCCreationError,
    FirstSourceChoice,
    ProcessLocalDraftPOCService,
)
from .poc_proposal_review import SourceBoundProposal
from .poc_source_intake import (
    POCSourceIntakeError,
    ProcessLocalPOCSourceIntake,
)
from .poc_sources import SourceKind
from .zoom_rtms_decoder import (
    ZOOM_RTMS_DECODER_VERSION,
    ZOOM_RTMS_PACKET_SCHEMA_VERSION,
    ZoomNormalizedTranscriptSegment,
)
from .zoom_session_runtime import (
    MAX_SESSION_SEGMENTS,
    ZoomSessionError,
    ZoomSessionProcessingInput,
    ZoomSessionStateMachine,
)


ZOOM_PROPOSAL_BRIDGE_VERSION = "exitspec.zoom-proposal-bridge/1.0"
ZOOM_PROPOSAL_ADAPTER_VERSION = "zoom-rtms-decoder-1.0"
ZOOM_PROPOSAL_SOURCE_PROVIDER = "ZOOM_RTMS"
ZOOM_PROPOSAL_REVIEW_STATE = "NEEDS_REVIEW"
ZOOM_PROPOSAL_EVALUATION_STATE = "NOT_RUN"

_SESSION_ID_PATTERN = r"^zoomsess_[a-f0-9]{64}$"
_POC_ID_PATTERN = r"^poc_[a-z0-9][a-z0-9_-]{2,63}$"
_BRIDGE_DOMAIN = b"exitspec-zoom-proposal-bridge-v1\x00"
_POC_ID_DOMAIN = b"exitspec-zoom-proposal-poc-v1\x00"
_POC_CREATE_KEY_DOMAIN = b"exitspec-zoom-proposal-create-key-v1\x00"
_SOURCE_KEY_DOMAIN = b"exitspec-zoom-proposal-source-key-v1\x00"
_SESSION_COMPLETION_KEY_DOMAIN = b"exitspec-zoom-proposal-completion-key-v1\x00"
_CATALOG_TOOL_SELECTION = re.compile(
    r"(?i)\btool\b.*\bselection\b|\bselection\b.*\btool\b"
)


class ZoomProposalBridgeFailureCode(str, Enum):
    """Stable, content-free bridge refusals."""

    INVALID_REQUEST = "ZOOM_PROPOSAL_BRIDGE_INVALID_REQUEST"
    SESSION_NOT_READY = "ZOOM_PROPOSAL_BRIDGE_SESSION_NOT_READY"
    PROVENANCE_MISMATCH = "ZOOM_PROPOSAL_BRIDGE_PROVENANCE_MISMATCH"
    POC_CREATE_FAILED = "ZOOM_PROPOSAL_BRIDGE_POC_CREATE_FAILED"
    SOURCE_ATTACH_FAILED = "ZOOM_PROPOSAL_BRIDGE_SOURCE_ATTACH_FAILED"
    PROPOSAL_PROJECTION_FAILED = "ZOOM_PROPOSAL_BRIDGE_PROPOSAL_PROJECTION_FAILED"
    COMPLETION_FAILED = "ZOOM_PROPOSAL_BRIDGE_COMPLETION_FAILED"
    EXISTING_POC_UNAVAILABLE = "ZOOM_PROPOSAL_BRIDGE_EXISTING_POC_UNAVAILABLE"


_FAILURE_DETAILS: dict[ZoomProposalBridgeFailureCode, tuple[str, str]] = {
    ZoomProposalBridgeFailureCode.INVALID_REQUEST: (
        "The Zoom proposal bridge request was not accepted.",
        "review_the_zoom_bridge_request",
    ),
    ZoomProposalBridgeFailureCode.SESSION_NOT_READY: (
        "The Zoom session is not ready for proposal processing.",
        "complete_or_recover_the_zoom_session",
    ),
    ZoomProposalBridgeFailureCode.PROVENANCE_MISMATCH: (
        "The normalized Zoom transcript provenance is inconsistent.",
        "stop_and_review_the_zoom_session",
    ),
    ZoomProposalBridgeFailureCode.POC_CREATE_FAILED: (
        "The draft POC could not be created safely.",
        "review_the_draft_poc_request",
    ),
    ZoomProposalBridgeFailureCode.SOURCE_ATTACH_FAILED: (
        "The Zoom source could not be attached safely.",
        "review_the_zoom_source_handoff",
    ),
    ZoomProposalBridgeFailureCode.PROPOSAL_PROJECTION_FAILED: (
        "The source proposals could not be projected for review.",
        "review_the_zoom_source_handoff",
    ),
    ZoomProposalBridgeFailureCode.COMPLETION_FAILED: (
        "The Zoom session could not record its completed handoff.",
        "recover_the_zoom_session_safely",
    ),
    ZoomProposalBridgeFailureCode.EXISTING_POC_UNAVAILABLE: (
        "The selected draft POC cannot accept a Zoom source.",
        "return_to_the_active_meeting_source",
    ),
}


class ZoomProposalBridgeError(RuntimeError):
    """Sanitized bridge refusal with no transcript or provider value."""

    retryable = False

    def __init__(self, failure_code: ZoomProposalBridgeFailureCode) -> None:
        self.failure_code = ZoomProposalBridgeFailureCode(failure_code)
        self.code = self.failure_code.value
        message, next_action = _FAILURE_DETAILS[self.failure_code]
        self.next_action = next_action
        super().__init__(message)


class _FrozenZoomBridgeModel(FrozenExitSpecModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
        validate_default=True,
    )


class ZoomProposalBridgeRequest(_FrozenZoomBridgeModel):
    """Employee-supplied draft metadata; transcript text is not accepted here."""

    session_id: str = Field(pattern=_SESSION_ID_PATTERN)
    display_name: str = Field(min_length=1, max_length=160)
    customer_label: str = Field(min_length=1, max_length=160)
    use_case: str = Field(min_length=1, max_length=500)
    owner: str = Field(min_length=1, max_length=160)


class ZoomSourceProvenance(_FrozenZoomBridgeModel):
    """Complete digest-only binding for one Zoom-sourced POC input."""

    schema_version: Literal[ZOOM_PROPOSAL_BRIDGE_VERSION] = (
        ZOOM_PROPOSAL_BRIDGE_VERSION
    )
    source_provider: Literal[ZOOM_PROPOSAL_SOURCE_PROVIDER] = (
        ZOOM_PROPOSAL_SOURCE_PROVIDER
    )
    session_id: str = Field(pattern=_SESSION_ID_PATTERN)
    decoder_version: Literal[ZOOM_RTMS_DECODER_VERSION] = ZOOM_RTMS_DECODER_VERSION
    packet_schema_version: Literal[ZOOM_RTMS_PACKET_SCHEMA_VERSION] = (
        ZOOM_RTMS_PACKET_SCHEMA_VERSION
    )
    source_classification: Literal[
        "SYNTHETIC_REVIEWED_FIXTURE",
        "PRIVATE_SYNTHETIC_RUNTIME",
    ]
    normalized_transcript_sha256: str = Field(pattern=SHA256_PATTERN)
    fixture_sha256: str = Field(pattern=SHA256_PATTERN)
    capture_plan_sha256: str = Field(pattern=SHA256_PATTERN)
    setup_attestation_sha256: str = Field(pattern=SHA256_PATTERN)
    runtime_plan_sha256: str = Field(pattern=SHA256_PATTERN)
    packet_sha256s: tuple[str, ...] = Field(
        min_length=1,
        max_length=MAX_SESSION_SEGMENTS,
    )
    segment_count: int = Field(gt=0, le=MAX_SESSION_SEGMENTS)
    source_binding_sha256: str = Field(pattern=SHA256_PATTERN)

    @field_validator("packet_sha256s")
    @classmethod
    def validate_packet_digests(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(re.fullmatch(SHA256_PATTERN, digest) is None for digest in value):
            raise ValueError("Zoom source provenance packet digest is invalid.")
        return value

    @model_validator(mode="after")
    def validate_digest_binding(self) -> "ZoomSourceProvenance":
        if (
            self.segment_count != len(self.packet_sha256s)
            or len(set(self.packet_sha256s)) != len(self.packet_sha256s)
        ):
            raise ValueError("Zoom source provenance packet population is invalid.")
        expected = _source_binding_digest(
            session_id=self.session_id,
            source_classification=self.source_classification,
            normalized_transcript_sha256=self.normalized_transcript_sha256,
            fixture_sha256=self.fixture_sha256,
            capture_plan_sha256=self.capture_plan_sha256,
            setup_attestation_sha256=self.setup_attestation_sha256,
            runtime_plan_sha256=self.runtime_plan_sha256,
            packet_sha256s=self.packet_sha256s,
        )
        if expected != self.source_binding_sha256:
            raise ValueError("Zoom source provenance digest is invalid.")
        return self


class ZoomProposalAssessment(_FrozenZoomBridgeModel):
    """Non-authoritative catalog annotation over an existing source proposal."""

    proposal_id: str = Field(min_length=1, max_length=128)
    catalog_metric: Metric | None = None
    review_state: Literal[ZOOM_PROPOSAL_REVIEW_STATE] = ZOOM_PROPOSAL_REVIEW_STATE
    evaluation_state: Literal[ZOOM_PROPOSAL_EVALUATION_STATE] = (
        ZOOM_PROPOSAL_EVALUATION_STATE
    )
    may_confirm_contract: Literal[False] = False
    may_freeze_contract: Literal[False] = False
    may_start_measurement: Literal[False] = False
    may_assign_verdict: Literal[False] = False


class ZoomProposalBridgeResult(_FrozenZoomBridgeModel):
    """The safe output of one Zoom transcript-to-source handoff."""

    bridge_version: Literal[ZOOM_PROPOSAL_BRIDGE_VERSION] = (
        ZOOM_PROPOSAL_BRIDGE_VERSION
    )
    poc_id: str = Field(pattern=_POC_ID_PATTERN)
    source_receipt_id: str = Field(pattern=r"^srcpt_[a-z0-9][a-z0-9_-]{7,95}$")
    source_kind: Literal[SourceKind.MEETING] = SourceKind.MEETING
    source_provider: Literal[ZOOM_PROPOSAL_SOURCE_PROVIDER] = (
        ZOOM_PROPOSAL_SOURCE_PROVIDER
    )
    source_provenance: ZoomSourceProvenance
    proposals: tuple[SourceBoundProposal, ...] = Field(max_length=64)
    assessments: tuple[ZoomProposalAssessment, ...] = Field(max_length=64)
    proposal_count: int = Field(ge=0, le=64)
    review_state: Literal[ZOOM_PROPOSAL_REVIEW_STATE] = ZOOM_PROPOSAL_REVIEW_STATE
    evaluation_state: Literal[ZOOM_PROPOSAL_EVALUATION_STATE] = (
        ZOOM_PROPOSAL_EVALUATION_STATE
    )
    review_url: str = Field(pattern=r"^/app/pocs/[a-z0-9_-]{3,64}/review$")
    idempotent_replay: bool
    may_confirm_contract: Literal[False] = False
    may_freeze_contract: Literal[False] = False
    may_start_measurement: Literal[False] = False
    may_assign_verdict: Literal[False] = False

    @model_validator(mode="after")
    def validate_proposal_projection(self) -> "ZoomProposalBridgeResult":
        if (
            self.proposal_count != len(self.proposals)
            or len(self.assessments) != len(self.proposals)
            or any(
                proposal.poc_id != self.poc_id
                or proposal.source_receipt_id != self.source_receipt_id
                for proposal in self.proposals
            )
            or tuple(item.proposal_id for item in self.assessments)
            != tuple(item.proposal_id for item in self.proposals)
        ):
            raise ValueError("Zoom proposal projection is not source-bound.")
        return self


def _digest(domain: bytes, payload: Any) -> str:
    return hashlib.sha256(domain + canonical_json_bytes(payload)).hexdigest()


def _source_binding_digest(
    *,
    session_id: str,
    source_classification: str,
    normalized_transcript_sha256: str,
    fixture_sha256: str,
    capture_plan_sha256: str,
    setup_attestation_sha256: str,
    runtime_plan_sha256: str,
    packet_sha256s: tuple[str, ...],
) -> str:
    return _digest(
        _BRIDGE_DOMAIN,
        {
            "capture_plan_sha256": capture_plan_sha256,
            "fixture_sha256": fixture_sha256,
            "normalized_transcript_sha256": normalized_transcript_sha256,
            "packet_sha256s": list(packet_sha256s),
            "runtime_plan_sha256": runtime_plan_sha256,
            "session_id": session_id,
            "setup_attestation_sha256": setup_attestation_sha256,
            "source_classification": source_classification,
        },
    )


def _stable_poc_id(session_id: str) -> str:
    return "poc_zoom_" + _digest(_POC_ID_DOMAIN, {"session_id": session_id})[:32]


def _stable_key(domain: bytes, session_id: str) -> str:
    return "zoom-bridge-" + _digest(domain, {"session_id": session_id})[:40]


def _source_text(
    segments: tuple[ZoomNormalizedTranscriptSegment, ...],
) -> str:
    speaker_labels = {
        "SPEAKER_1": "Speaker 1",
        "SPEAKER_2": "Speaker 2",
        "SPEAKER_UNKNOWN": "Speaker unknown",
    }
    return "\n".join(
        "{0}: {1}".format(speaker_labels[segment.speaker_pseudonym], segment.text)
        for segment in segments
    )


def _catalog_metric(proposal: SourceBoundProposal) -> Metric | None:
    if _CATALOG_TOOL_SELECTION.search(proposal.normalized_claim):
        return Metric.EXACT_TOOL_SELECTION_RATE
    return None


def _provenance_from_input(
    processing_input: ZoomSessionProcessingInput,
) -> ZoomSourceProvenance:
    if type(processing_input) is not ZoomSessionProcessingInput:
        raise ZoomProposalBridgeError(
            ZoomProposalBridgeFailureCode.INVALID_REQUEST
        )
    segments = processing_input.segments_for_bridge()
    if (
        not re.fullmatch(_SESSION_ID_PATTERN, processing_input.session_id)
        or not re.fullmatch(SHA256_PATTERN, processing_input.transcript_sha256)
        or type(segments) is not tuple
        or not 1 <= len(segments) <= MAX_SESSION_SEGMENTS
        or any(type(segment) is not ZoomNormalizedTranscriptSegment for segment in segments)
    ):
        raise ZoomProposalBridgeError(
            ZoomProposalBridgeFailureCode.INVALID_REQUEST
        )
    first = segments[0].provenance
    if any(
        (
            segment.provenance.source_classification != first.source_classification
            or segment.provenance.fixture_sha256 != first.fixture_sha256
            or segment.provenance.capture_plan_sha256 != first.capture_plan_sha256
            or segment.provenance.setup_attestation_sha256
            != first.setup_attestation_sha256
            or segment.provenance.runtime_plan_sha256 != first.runtime_plan_sha256
        )
        for segment in segments
    ):
        raise ZoomProposalBridgeError(
            ZoomProposalBridgeFailureCode.PROVENANCE_MISMATCH
        )
    packet_sha256s = tuple(segment.packet_sha256 for segment in segments)
    try:
        return ZoomSourceProvenance(
            session_id=processing_input.session_id,
            source_classification=first.source_classification,
            normalized_transcript_sha256=processing_input.transcript_sha256,
            fixture_sha256=first.fixture_sha256,
            capture_plan_sha256=first.capture_plan_sha256,
            setup_attestation_sha256=first.setup_attestation_sha256,
            runtime_plan_sha256=first.runtime_plan_sha256,
            packet_sha256s=packet_sha256s,
            segment_count=len(segments),
            source_binding_sha256=_source_binding_digest(
                session_id=processing_input.session_id,
                source_classification=first.source_classification,
                normalized_transcript_sha256=processing_input.transcript_sha256,
                fixture_sha256=first.fixture_sha256,
                capture_plan_sha256=first.capture_plan_sha256,
                setup_attestation_sha256=first.setup_attestation_sha256,
                runtime_plan_sha256=first.runtime_plan_sha256,
                packet_sha256s=packet_sha256s,
            ),
        )
    except ValueError:
        raise ZoomProposalBridgeError(
            ZoomProposalBridgeFailureCode.PROVENANCE_MISMATCH
        ) from None


class ZoomProposalBridge:
    """Create one Zoom-sourced draft through existing local services."""

    __slots__ = ("_drafts", "_lock", "_source_intake")

    def __init__(
        self,
        *,
        drafts: ProcessLocalDraftPOCService,
        source_intake: ProcessLocalPOCSourceIntake,
    ) -> None:
        if type(drafts) is not ProcessLocalDraftPOCService:
            raise TypeError("drafts must be a ProcessLocalDraftPOCService.")
        if type(source_intake) is not ProcessLocalPOCSourceIntake:
            raise TypeError("source_intake must be a ProcessLocalPOCSourceIntake.")
        self._drafts = drafts
        self._source_intake = source_intake
        self._lock = RLock()

    def bridge(
        self,
        *,
        session: ZoomSessionStateMachine,
        request: ZoomProposalBridgeRequest,
    ) -> ZoomProposalBridgeResult:
        if (
            type(session) is not ZoomSessionStateMachine
            or type(request) is not ZoomProposalBridgeRequest
        ):
            raise ZoomProposalBridgeError(
                ZoomProposalBridgeFailureCode.INVALID_REQUEST
            )
        if session.snapshot().session_id != request.session_id:
            raise ZoomProposalBridgeError(
                ZoomProposalBridgeFailureCode.INVALID_REQUEST
            )
        try:
            processing_input = session.processing_input()
        except ZoomSessionError:
            raise ZoomProposalBridgeError(
                ZoomProposalBridgeFailureCode.SESSION_NOT_READY
            ) from None
        provenance = _provenance_from_input(processing_input)
        segments = processing_input.segments_for_bridge()
        source_text = _source_text(segments)
        content_sha256 = hashlib.sha256(source_text.encode("utf-8")).hexdigest()
        poc_id = _stable_poc_id(request.session_id)
        create_key = _stable_key(_POC_CREATE_KEY_DOMAIN, request.session_id)
        source_key = _stable_key(_SOURCE_KEY_DOMAIN, request.session_id)
        completion_key = _stable_key(
            _SESSION_COMPLETION_KEY_DOMAIN,
            request.session_id,
        )
        source_external_id = "zoom.rtms." + provenance.source_binding_sha256[:32]
        with self._lock:
            try:
                created = self._drafts.create(
                    DraftPOCCreateRequest(
                        poc_id=poc_id,
                        display_name=request.display_name,
                        customer_label=request.customer_label,
                        use_case=request.use_case,
                        owner=request.owner,
                        first_source_choice=FirstSourceChoice.MEETING,
                    ),
                    idempotency_key=create_key,
                )
            except (DraftPOCCreationError, TypeError, ValueError):
                raise ZoomProposalBridgeError(
                    ZoomProposalBridgeFailureCode.POC_CREATE_FAILED
                ) from None
            return self._attach_and_complete(
                session=session,
                poc_id=poc_id,
                provenance=provenance,
                source_text=source_text,
                content_sha256=content_sha256,
                source_external_id=source_external_id,
                source_key=source_key,
                completion_key=completion_key,
                idempotent_replay=created.idempotent_replay,
            )

    def bridge_into_existing_poc(
        self,
        *,
        session: ZoomSessionStateMachine,
        poc_id: object,
    ) -> ZoomProposalBridgeResult:
        """Attach one completed Zoom session to the active meeting draft.

        The original ``bridge`` method remains the standalone ingest path used
        by the PR5 contract. This additive handoff path is for the existing
        ``/app/pocs/{id}/sources/new`` workflow: it never creates a second POC.
        """

        if type(session) is not ZoomSessionStateMachine or not isinstance(
            poc_id, str
        ) or re.fullmatch(_POC_ID_PATTERN, poc_id) is None:
            raise ZoomProposalBridgeError(
                ZoomProposalBridgeFailureCode.INVALID_REQUEST
            )
        try:
            draft = self._drafts.get(poc_id)
            if (
                draft.archive_state.value != "ACTIVE"
                or draft.first_source_choice is not FirstSourceChoice.MEETING
            ):
                raise ValueError
            processing_input = session.processing_input()
        except (DraftPOCCreationError, ZoomSessionError, TypeError, ValueError):
            raise ZoomProposalBridgeError(
                ZoomProposalBridgeFailureCode.EXISTING_POC_UNAVAILABLE
            ) from None
        provenance = _provenance_from_input(processing_input)
        segments = processing_input.segments_for_bridge()
        source_text = _source_text(segments)
        content_sha256 = hashlib.sha256(source_text.encode("utf-8")).hexdigest()
        source_external_id = "zoom.rtms." + provenance.source_binding_sha256[:32]
        source_key = _stable_key(_SOURCE_KEY_DOMAIN, session.snapshot().session_id)
        completion_key = _stable_key(
            _SESSION_COMPLETION_KEY_DOMAIN,
            session.snapshot().session_id,
        )
        with self._lock:
            return self._attach_and_complete(
                session=session,
                poc_id=poc_id,
                provenance=provenance,
                source_text=source_text,
                content_sha256=content_sha256,
                source_external_id=source_external_id,
                source_key=source_key,
                completion_key=completion_key,
                idempotent_replay=False,
            )

    def _attach_and_complete(
        self,
        *,
        session: ZoomSessionStateMachine,
        poc_id: str,
        provenance: ZoomSourceProvenance,
        source_text: str,
        content_sha256: str,
        source_external_id: str,
        source_key: str,
        completion_key: str,
        idempotent_replay: bool,
    ) -> ZoomProposalBridgeResult:
        try:
            source_receipt = self._source_intake.capture_zoom_rtms_transcript(
                poc_id=poc_id,
                redacted_transcript_text=source_text,
                expected_content_sha256=content_sha256,
                source_external_id=source_external_id,
                adapter_version=ZOOM_PROPOSAL_ADAPTER_VERSION,
                idempotency_key=source_key,
            )
        except (POCSourceIntakeError, TypeError, ValueError):
            raise ZoomProposalBridgeError(
                ZoomProposalBridgeFailureCode.SOURCE_ATTACH_FAILED
            ) from None
        try:
            proposals = tuple(
                proposal
                for proposal in self._source_intake.proposal_inputs(poc_id)
                if proposal.source_receipt_id == source_receipt.source_receipt_id
            )
            assessments = tuple(
                ZoomProposalAssessment(
                    proposal_id=proposal.proposal_id,
                    catalog_metric=_catalog_metric(proposal),
                )
                for proposal in proposals
            )
        except (POCSourceIntakeError, TypeError, ValueError):
            raise ZoomProposalBridgeError(
                ZoomProposalBridgeFailureCode.PROPOSAL_PROJECTION_FAILED
            ) from None
        result = ZoomProposalBridgeResult(
            poc_id=poc_id,
            source_receipt_id=source_receipt.source_receipt_id,
            source_provenance=provenance,
            proposals=proposals,
            assessments=assessments,
            proposal_count=len(proposals),
            review_url=f"/app/pocs/{poc_id}/review",
            idempotent_replay=(idempotent_replay or source_receipt.idempotent_replay),
        )
        result_sha256 = _digest(
            _BRIDGE_DOMAIN,
            result.model_dump(mode="json") | {"idempotent_replay": False},
        )
        try:
            completion = session.processing_succeeded(
                result_sha256=result_sha256,
                idempotency_key=completion_key,
            )
        except ZoomSessionError:
            raise ZoomProposalBridgeError(
                ZoomProposalBridgeFailureCode.COMPLETION_FAILED
            ) from None
        return result.model_copy(
            update={
                "idempotent_replay": (
                    result.idempotent_replay
                    or completion.idempotent_replay
                    or completion.duplicate_suppressed
                )
            }
        )


__all__ = [
    "ZOOM_PROPOSAL_ADAPTER_VERSION",
    "ZOOM_PROPOSAL_BRIDGE_VERSION",
    "ZOOM_PROPOSAL_EVALUATION_STATE",
    "ZOOM_PROPOSAL_REVIEW_STATE",
    "ZOOM_PROPOSAL_SOURCE_PROVIDER",
    "ZoomProposalAssessment",
    "ZoomProposalBridge",
    "ZoomProposalBridgeError",
    "ZoomProposalBridgeFailureCode",
    "ZoomProposalBridgeRequest",
    "ZoomProposalBridgeResult",
    "ZoomSourceProvenance",
]
