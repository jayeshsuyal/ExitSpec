from datetime import datetime, timezone
import threading

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
    TerminalRunReceiptBinding,
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


def _run_binding(
    poc_id: str = "poc_blocked",
    *,
    run_receipt_sha256: str = "f" * 64,
) -> TerminalRunReceiptBinding:
    return TerminalRunReceiptBinding(
        poc_id=poc_id,
        contract_id="inference-latency-poc",
        contract_version="1.0.0",
        contract_hash="a" * 64,
        operation_id="prun_" + "1" * 32,
        runner_run_id="run_" + "2" * 32,
        runner_input_digest="d" * 64,
        run_status="BLOCKED",
        reason_code="ENDPOINT_PREFLIGHT_FAILED",
        terminal_at=NOW,
        run_receipt_sha256=run_receipt_sha256,
    )


def _stop_request(
    binding: TerminalRunReceiptBinding,
    *,
    decision: HumanClosureDecision = HumanClosureDecision.POC_STOPPED,
) -> HumanPOCClosureRequest:
    return HumanPOCClosureRequest(
        decision=decision,
        decided_by="field_engineer",
        rationale="Stopped after reviewing the exact blocked-run receipt.",
        terminal_run_binding=binding,
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


def test_blocked_run_receipt_allows_stop_but_never_completed_handoff():
    binding = _run_binding()
    service = ProcessLocalPOCClosureService(
        evidence_resolver={binding.poc_id: binding}.get,
        clock=lambda: NOW,
        closure_id_factory=lambda: "poccl_" + "e" * 32,
    )

    with pytest.raises(POCClosureEvidenceUnavailable, match="Evidence Pack"):
        service.record(
            binding.poc_id,
            _stop_request(
                binding,
                decision=HumanClosureDecision.HANDOFF_COMPLETED,
            ),
            idempotency_key="blocked-handoff-refused",
        )

    result = service.record(
        binding.poc_id,
        _stop_request(binding),
        idempotency_key="blocked-stop-created",
    )

    assert result.closure.evidence_binding is None
    assert result.closure.terminal_run_binding == binding
    assert result.closure.shipping_authorized is False


def test_terminal_run_binding_rejects_tampering_and_cross_poc_use():
    binding = _run_binding()
    service = ProcessLocalPOCClosureService(
        evidence_resolver={binding.poc_id: binding}.get,
    )
    tampered = binding.model_copy(update={"run_receipt_sha256": "0" * 64})

    with pytest.raises(POCClosureBindingMismatch, match="does not match"):
        service.record(
            binding.poc_id,
            _stop_request(tampered),
            idempotency_key="tampered-run-binding",
        )

    with pytest.raises(POCClosureBindingMismatch, match="does not belong"):
        service.record(
            binding.poc_id,
            _stop_request(_run_binding("poc_other")),
            idempotency_key="cross-poc-run-binding",
        )


def test_mutation_guard_leases_refuse_closure_without_serializing_work():
    binding = _binding()
    service = _service({binding.poc_id: binding})
    mutation_started = threading.Event()
    release_mutation = threading.Event()
    closure_finished = threading.Event()
    order = []

    def mutate() -> None:
        def operation() -> None:
            mutation_started.set()
            assert release_mutation.wait(timeout=2)
            order.append("mutation")

        service.run_if_open(binding.poc_id, operation)

    def close() -> None:
        try:
            service.record(
                binding.poc_id,
                _request(binding),
                idempotency_key="close-during-in-flight-mutation",
            )
        except POCClosureConflict:
            order.append("closure_refused")
            closure_finished.set()

    mutation_thread = threading.Thread(target=mutate)
    closure_thread = threading.Thread(target=close)
    mutation_thread.start()
    assert mutation_started.wait(timeout=2)
    closure_thread.start()
    assert closure_finished.wait(timeout=2) is True
    release_mutation.set()
    mutation_thread.join(timeout=2)
    closure_thread.join(timeout=2)

    assert order == ["closure_refused", "mutation"]
    service.record(
        binding.poc_id,
        _request(binding),
        idempotency_key="close-after-in-flight-mutation",
    )
    with pytest.raises(POCClosureConflict, match="closed"):
        service.run_if_open(binding.poc_id, lambda: None)
