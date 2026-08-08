"""Shared test setup for browser-orchestrated Inferdrome imports."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from exitspec.canonical import canonical_json_bytes
from exitspec.poc_contract_definition import (
    ContractDefinitionOperator,
    InferencePerformanceCriterionDefinition,
    InferencePerformanceMetric,
    ProcessLocalContractDefinitionService,
)
from exitspec.poc_creation import (
    DraftPOCCreateRequest,
    ProcessLocalDraftPOCService,
)
from exitspec.poc_performance_contract import (
    PerformanceEvidenceMethod,
    PerformanceTargetInput,
)
from exitspec.poc_performance_lifecycle import (
    ProcessLocalPerformanceLifecycleService,
)
from exitspec.poc_proposal_review import (
    ProcessLocalProposalReviewService,
    ProposalDecision,
    SourceBoundProposal,
)
from tests.inferdrome_helpers import PROMPTS, bind_customer_bundle, mutable_bundle_copy

NOW = datetime(2026, 8, 7, 12, 0, tzinfo=UTC)
POC_ID = "poc_inferdrome_external"
PROMPT_BYTES = b"".join(
    canonical_json_bytes({"id": f"prompt-{index + 1}", "content": content}) + b"\n"
    for index, content in enumerate(PROMPTS)
)


def build_external_web_services(
    *,
    evidence_method: PerformanceEvidenceMethod = (
        PerformanceEvidenceMethod.INFERDROME_EXTERNAL_BUNDLE
    ),
) -> tuple[
    ProcessLocalPerformanceLifecycleService,
    ProcessLocalDraftPOCService,
    ProcessLocalProposalReviewService,
    ProcessLocalContractDefinitionService,
]:
    drafts = ProcessLocalDraftPOCService(
        clock=lambda: NOW,
        poc_id_factory=lambda: POC_ID,
    )
    drafts.create(
        DraftPOCCreateRequest(
            display_name="Imported inference proof",
            customer_label="Northstar",
            use_case="Validate imported latency and reliability evidence.",
            owner="field_engineer",
            first_source_choice="MEETING",
        ),
        idempotency_key="create-inferdrome-external",
    )
    source = (
        SourceBoundProposal(
            poc_id=POC_ID,
            proposal_id="prop_inferdrome_ttft_001",
            source_receipt_id="srcpt_inferdrome_external_001",
            source_kind="MEETING",
            source_quote="P95 TTFT must stay below 500 ms.",
            normalized_claim="P95 TTFT must stay below 500 ms.",
        ),
        SourceBoundProposal(
            poc_id=POC_ID,
            proposal_id="prop_inferdrome_error_002",
            source_receipt_id="srcpt_inferdrome_external_001",
            source_kind="MEETING",
            source_quote="Error rate must remain below 50 percent.",
            normalized_claim="Error rate must remain below 50 percent.",
        ),
    )
    proposals = ProcessLocalProposalReviewService(
        proposal_lookup=lambda poc_id: source if poc_id == POC_ID else (),
        clock=lambda: NOW,
    )
    for index, proposal in enumerate(source):
        proposals.decide(
            POC_ID,
            proposal.proposal_id,
            ProposalDecision.KEEP_FOR_CONTRACT,
            "Jayesh",
            "Keep this exact imported-evidence requirement.",
            f"keep-inferdrome-external-{index}",
        )
    definitions = ProcessLocalContractDefinitionService(
        proposal_lookup=proposals.list_proposals,
        clock=lambda: NOW,
    )
    common = {
        "minimum_samples": 4,
        "concurrency": 2,
        "prompt_tokens_min": 1,
        "prompt_tokens_max": 256,
        "output_tokens_min": 1,
        "output_tokens_max": 2,
        "reviewer": "Jayesh",
        "rationale": "This exact rule is ready for external evidence.",
    }
    definitions.define(
        POC_ID,
        source[0].proposal_id,
        InferencePerformanceCriterionDefinition(
            metric=InferencePerformanceMetric.TTFT_P95_MS,
            operator=ContractDefinitionOperator.LT,
            threshold=500,
            **common,
        ),
        idempotency_key="define-inferdrome-ttft",
    )
    definitions.define(
        POC_ID,
        source[1].proposal_id,
        InferencePerformanceCriterionDefinition(
            metric=InferencePerformanceMetric.ERROR_RATE_PERCENT,
            operator=ContractDefinitionOperator.LT,
            threshold=50,
            **common,
        ),
        idempotency_key="define-inferdrome-error",
    )
    lifecycle = ProcessLocalPerformanceLifecycleService(
        draft_lookup=drafts.get,
        proposal_lookup=proposals.list_proposals,
        definition_lookup=definitions.definitions,
        prompt_bytes=PROMPT_BYTES,
        clock=lambda: NOW,
    )
    lifecycle.prepare(
        POC_ID,
        target=PerformanceTargetInput(
            provider="vllm-local",
            endpoint_class="openai-compatible-chat-completions",
            endpoint="http://127.0.0.1:18083/v1/chat/completions",
            model="inferdrome/mock-model",
            evidence_method=evidence_method,
        ),
        reviewer="Jayesh",
        rationale="Bind the sealed Inferdrome evidence method.",
        idempotency_key="prepare-inferdrome-external",
    )
    token = lifecycle.customer_review_url(POC_ID).rsplit("/", 1)[-1]
    lifecycle.record_customer_review_decision(
        token,
        decision="CONFIRM",
        agreement_acknowledged=True,
        rationale="The exact external evidence method was reviewed.",
        idempotency_key="confirm-inferdrome-external",
    )
    lifecycle.freeze(
        POC_ID,
        idempotency_key="freeze-inferdrome-external",
    )
    return lifecycle, drafts, proposals, definitions


def build_external_lifecycle(
    *,
    evidence_method: PerformanceEvidenceMethod = (
        PerformanceEvidenceMethod.INFERDROME_EXTERNAL_BUNDLE
    ),
) -> tuple[
    ProcessLocalPerformanceLifecycleService,
    ProcessLocalDraftPOCService,
]:
    lifecycle, drafts, _, _ = build_external_web_services(
        evidence_method=evidence_method
    )
    return lifecycle, drafts


def customer_eligible_bundle(
    tmp_path: Path,
    lifecycle: ProcessLocalPerformanceLifecycleService,
) -> tuple[Path, Path]:
    runs_root = tmp_path / "inferdrome-runs"
    runs_root.mkdir()
    bundle_path = mutable_bundle_copy(runs_root)
    _, _, frozen = lifecycle.frozen_bundle(POC_ID)
    assert frozen.canonical_hash is not None
    bind_customer_bundle(bundle_path, frozen.canonical_hash)
    return runs_root, bundle_path
