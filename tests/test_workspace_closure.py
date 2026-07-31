from datetime import datetime, timezone

import pytest

from exitspec.models import VerdictStatus
from exitspec.workspace_closure import (
    HumanClosureDecision,
    HumanPOCClosureRequest,
    POCClosureBindingMismatch,
    POCClosureCapacityExceeded,
    POCClosureConflict,
    POCClosureEvidenceUnavailable,
    POCClosureIdempotencyConflict,
    ProcessLocalPOCClosureService,
    TerminalEvidenceBinding,
)


NOW = datetime(2026, 7, 29, 20, 0, tzinfo=timezone.utc)


def _binding(
    poc_id: str = "poc_support_agent_demo",
    *,
    evidence_pack_sha256: str = "b" * 64,
) -> TerminalEvidenceBinding:
    return TerminalEvidenceBinding(
        poc_id=poc_id,
        contract_id="support-agent-poc",
        contract_version="1.0.0",
        contract_hash="a" * 64,
        run_id="run_support_agent_pass",
        verdict=VerdictStatus.PASS,
        evidence_pack_url=(
            "/artifacts/run_support_agent_pass/decision-packet.html"
        ),
        evidence_pack_sha256=evidence_pack_sha256,
    )


def _request(
    binding: TerminalEvidenceBinding,
    *,
    decision: HumanClosureDecision = HumanClosureDecision.HANDOFF_COMPLETED,
    rationale: str = "Evidence Pack handed to the customer owner.",
) -> HumanPOCClosureRequest:
    return HumanPOCClosureRequest(
        decision=decision,
        decided_by="field_engineer",
        rationale=rationale,
        evidence_binding=binding,
    )


def _service(
    bindings: dict[str, TerminalEvidenceBinding],
    *,
    max_records: int = 8,
) -> ProcessLocalPOCClosureService:
    return ProcessLocalPOCClosureService(
        evidence_resolver=bindings.get,
        clock=lambda: NOW,
        closure_id_factory=lambda: "poccl_" + "c" * 32,
        max_records=max_records,
    )


def test_closure_records_exact_evidence_and_never_authorizes_shipping():
    binding = _binding()
    service = _service({binding.poc_id: binding})

    result = service.record(
        binding.poc_id,
        _request(binding),
        idempotency_key="close-support-agent-v1",
    )

    assert result.idempotent_replay is False
    assert result.closure.recorded_at == NOW
    assert result.closure.evidence_binding == binding
    assert result.closure.evidence_binding_sha256
    assert result.closure.authorization_scope == "POC_LIFECYCLE_ONLY"
    assert result.closure.shipping_authorized is False
    assert service.get(binding.poc_id) == result.closure


def test_closure_rejects_stale_or_cross_poc_evidence_binding():
    binding = _binding()
    service = _service({binding.poc_id: binding})
    stale = _binding(evidence_pack_sha256="d" * 64)

    with pytest.raises(POCClosureBindingMismatch, match="does not match"):
        service.record(
            binding.poc_id,
            _request(stale),
            idempotency_key="stale-binding",
        )

    other = _binding("poc_other")
    with pytest.raises(POCClosureBindingMismatch, match="does not belong"):
        service.record(
            binding.poc_id,
            _request(other),
            idempotency_key="cross-poc-binding",
        )
    assert service.records() == ()


def test_closure_requires_a_current_terminal_evidence_pack():
    binding = _binding()
    service = _service({})

    with pytest.raises(POCClosureEvidenceUnavailable, match="required"):
        service.record(
            binding.poc_id,
            _request(binding),
            idempotency_key="no-terminal-evidence",
        )
    assert service.records() == ()


def test_exact_replay_is_idempotent_and_conflicting_reuse_fails_closed():
    binding = _binding()
    service = _service({binding.poc_id: binding})
    request = _request(binding)

    first = service.record(
        binding.poc_id,
        request,
        idempotency_key="closure-replay",
    )
    replay = service.record(
        binding.poc_id,
        request,
        idempotency_key="closure-replay",
    )
    assert replay.idempotent_replay is True
    assert replay.closure == first.closure

    with pytest.raises(POCClosureIdempotencyConflict):
        service.record(
            binding.poc_id,
            _request(binding, rationale="A different rationale."),
            idempotency_key="closure-replay",
        )
    with pytest.raises(POCClosureConflict):
        service.record(
            binding.poc_id,
            request,
            idempotency_key="different-terminal-decision",
        )


def test_closure_store_capacity_is_bounded():
    first = _binding("poc_first")
    second = _binding("poc_second")
    service = _service(
        {first.poc_id: first, second.poc_id: second},
        max_records=1,
    )
    service.record(
        first.poc_id,
        _request(first),
        idempotency_key="close-first",
    )

    with pytest.raises(POCClosureCapacityExceeded):
        service.record(
            second.poc_id,
            _request(second),
            idempotency_key="close-second",
        )
