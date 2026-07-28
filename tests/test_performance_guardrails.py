from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from exitspec.authoring import approve_draft
from exitspec.models import (
    CriterionDraft,
    InferencePerformanceCriterion,
)
from exitspec.reporting import render_customer_draft
from exitspec.runner import _require_brick_one_contract, load_contract


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = (
    PROJECT_ROOT
    / "examples/inference-performance/contracts/vllm-ttft-v1.yaml"
)


def test_performance_customer_draft_exposes_both_exact_rules():
    contract = load_contract(CONTRACT_PATH)

    rendered = render_customer_draft(contract)

    assert "p95 time to first non-empty content" in rendered
    assert "&lt; 500 milliseconds" in rendered
    assert "error rate must be &lt; 1.00%" in rendered
    assert "Both checks must pass" in rendered
    assert "not evidence" in rendered


def test_performance_criterion_can_pass_through_explicit_human_review():
    contract = load_contract(CONTRACT_PATH)
    criterion = contract.criteria[0]
    assert isinstance(criterion, InferencePerformanceCriterion)
    human_added = criterion.model_copy(
        update={
            "source": None,
            "human_added": True,
            "approved": False,
        }
    )
    draft = CriterionDraft(
        id=human_added.id,
        human_added=True,
        human_added_rationale="Customer confirmed this requirement on the call.",
        normalized_claim=human_added.normalized_claim,
        proposed_criterion=human_added,
    )

    approved = approve_draft(
        draft,
        reviewer="vendor_solutions_engineer",
        rationale="The customer confirmed both thresholds.",
    )

    assert isinstance(
        approved.proposed_criterion,
        InferencePerformanceCriterion,
    )
    assert approved.proposed_criterion.approved is True


def test_legacy_runner_rejects_performance_type_before_execution():
    contract = load_contract(CONTRACT_PATH)
    criterion = contract.criteria[0]
    incompatible = criterion.model_copy(
        update={"adapter": "deterministic_tool_selection"}
    )
    incompatible_contract = contract.model_copy(
        update={"criteria": (incompatible,)}
    )

    with pytest.raises(ValueError, match="legacy exact-tool-selection"):
        _require_brick_one_contract(incompatible_contract)


def test_performance_v1_rejects_non_strict_or_vacuous_error_rules():
    contract = load_contract(CONTRACT_PATH)
    payload = contract.criteria[0].model_dump(mode="python")

    payload["error_rate"]["operator"] = "lte"
    with pytest.raises(ValidationError):
        InferencePerformanceCriterion.model_validate(payload)

    payload = contract.criteria[0].model_dump(mode="python")
    payload["error_rate"]["threshold"] = 1.0
    with pytest.raises(ValidationError):
        InferencePerformanceCriterion.model_validate(payload)
