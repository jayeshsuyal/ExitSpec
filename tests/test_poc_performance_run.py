from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import pytest

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
from exitspec.poc_performance_contract import PerformanceTargetInput
from exitspec.poc_performance_lifecycle import (
    ProcessLocalPerformanceLifecycleService,
)
from exitspec.poc_performance_run import (
    POCPerformanceRunConflict,
    POCPerformanceRunInvalid,
    POCPerformanceRunStatus,
    ProcessLocalPOCPerformanceRunService,
)
from exitspec.performance_operations import (
    PerformanceOperation,
    PerformanceOperationStatus,
)
from exitspec.performance_runner import PerformanceRunResult
from exitspec.poc_proposal_review import (
    ProcessLocalProposalReviewService,
    ProposalDecision,
    SourceBoundProposal,
)


NOW = datetime(2026, 7, 29, 15, 0, tzinfo=timezone.utc)
POC_ID = "poc_dynamic_performance"
PROMPTS = b'{"id":"dynamic-001","content":"Explain TTFT briefly."}\n'


class _EndpointState:
    def __init__(self, fail_request: int | None = None) -> None:
        self.fail_request = fail_request
        self.request_count = 0
        self.authorization_headers: list[str | None] = []
        self.lock = threading.Lock()

    def next(self, authorization: str | None) -> int:
        with self.lock:
            self.request_count += 1
            self.authorization_headers.append(authorization)
            return self.request_count


@contextmanager
def _endpoint(*, fail_request: int | None = None):
    state = _EndpointState(fail_request)

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self) -> None:
            request_number = state.next(self.headers.get("Authorization"))
            length = int(self.headers.get("Content-Length", "0"))
            request = json.loads(self.rfile.read(length))
            assert request["stream"] is True
            if request_number == state.fail_request:
                payload = b'{"error":"synthetic unavailable"}'
                self.send_response(503)
                self.send_header("Content-Type", "application/json")
            else:
                payload = (
                    b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n'
                    b"data: [DONE]\n\n"
                )
                self.send_response(200)
                self.send_header(
                    "Content-Type",
                    "text/event-stream; charset=utf-8",
                )
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield (
            "http://127.0.0.1:{0}/v1/chat/completions".format(
                server.server_port
            ),
            state,
        )
    finally:
        server.shutdown()
        worker.join(timeout=5)
        server.server_close()


def _lifecycle(
    endpoint: str,
    *,
    freeze: bool = True,
    provider: str = "local-vllm",
) -> ProcessLocalPerformanceLifecycleService:
    drafts = ProcessLocalDraftPOCService(
        clock=lambda: NOW,
        poc_id_factory=lambda: POC_ID,
    )
    drafts.create(
        DraftPOCCreateRequest(
            display_name="Dynamic inference proof",
            customer_label="Northstar",
            use_case="Validate latency and reliability.",
            owner="field_engineer",
            first_source_choice="MEETING",
        ),
        idempotency_key="create-dynamic-performance",
    )
    source = (
        SourceBoundProposal(
            poc_id=POC_ID,
            proposal_id="prop_dynamic_ttft_001",
            source_receipt_id="srcpt_dynamic_performance_001",
            source_kind="MEETING",
            source_quote="P95 TTFT must stay below 500 ms.",
            normalized_claim="P95 TTFT must stay below 500 ms.",
        ),
        SourceBoundProposal(
            poc_id=POC_ID,
            proposal_id="prop_dynamic_error_002",
            source_receipt_id="srcpt_dynamic_performance_001",
            source_kind="MEETING",
            source_quote="Error rate must remain below 1 percent.",
            normalized_claim="Error rate must remain below 1 percent.",
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
            "Keep this exact measurable requirement.",
            f"keep-dynamic-performance-{index}",
        )
    definitions = ProcessLocalContractDefinitionService(
        proposal_lookup=proposals.list_proposals,
        clock=lambda: NOW,
    )
    common = {
        "minimum_samples": 100,
        "concurrency": 4,
        "prompt_tokens_min": 1,
        "prompt_tokens_max": 32,
        "output_tokens_min": 1,
        "output_tokens_max": 8,
        "reviewer": "Jayesh",
        "rationale": "This exact rule is ready for execution.",
    }
    definitions.define(
        POC_ID,
        source[0].proposal_id,
        InferencePerformanceCriterionDefinition(
            metric=InferencePerformanceMetric.TTFT_P95_MS,
            operator=ContractDefinitionOperator.LTE,
            threshold=500,
            **common,
        ),
        idempotency_key="define-dynamic-ttft",
    )
    definitions.define(
        POC_ID,
        source[1].proposal_id,
        InferencePerformanceCriterionDefinition(
            metric=InferencePerformanceMetric.ERROR_RATE_PERCENT,
            operator=ContractDefinitionOperator.LT,
            threshold=1,
            **common,
        ),
        idempotency_key="define-dynamic-error",
    )
    lifecycle = ProcessLocalPerformanceLifecycleService(
        draft_lookup=drafts.get,
        proposal_lookup=proposals.list_proposals,
        definition_lookup=definitions.definitions,
        prompt_bytes=PROMPTS,
        clock=lambda: NOW,
    )
    if freeze:
        lifecycle.prepare(
            POC_ID,
            target=PerformanceTargetInput(
                provider=provider,
                endpoint_class="openai-compatible-chat-completions",
                endpoint=endpoint,
                model="Qwen/Qwen2.5-0.5B-Instruct",
            ),
            reviewer="Jayesh",
            rationale="Bind the exact local target.",
            idempotency_key="prepare-dynamic-performance",
        )
        review_token = lifecycle.customer_review_url(POC_ID).rsplit("/", 1)[-1]
        lifecycle.record_customer_review_decision(
            review_token,
            decision="CONFIRM",
            agreement_acknowledged=True,
            rationale="The exact target and criteria were reviewed.",
            idempotency_key="confirm-dynamic-performance",
        )
        lifecycle.freeze(
            POC_ID,
            idempotency_key="freeze-dynamic-performance",
        )
    return lifecycle


def test_frozen_dynamic_poc_runs_existing_authoritative_proof_loop(tmp_path):
    with _endpoint() as (endpoint, endpoint_state):
        lifecycle = _lifecycle(endpoint)
        service = ProcessLocalPOCPerformanceRunService(
            lifecycle=lifecycle,
            output_root=(tmp_path / "runs").resolve(),
            worker_launcher=lambda target: target(),
        )

        before = service.snapshot(POC_ID)
        first = service.start(
            POC_ID,
            execution_acknowledged=True,
            idempotency_key="run-dynamic-performance",
        )
        calls_after_first = endpoint_state.request_count
        replay = service.start(
            POC_ID,
            execution_acknowledged=True,
            idempotency_key="run-dynamic-performance",
        )
        history_after_replay = service.completed_snapshots(POC_ID)
        first_pack_sha256 = service.verified_evidence_pack_sha256(
            POC_ID,
            first.operation.operation_id,
        )
        second = service.start(
            POC_ID,
            execution_acknowledged=True,
            idempotency_key="run-dynamic-performance-again",
        )
        complete_history = service.completed_snapshots(POC_ID)

    assert before.status is POCPerformanceRunStatus.NOT_STARTED
    assert before.operation_id is None
    assert first.replayed is False
    assert first.operation.status is POCPerformanceRunStatus.COMPLETED
    assert first.operation.verdict.value == "PASS"
    assert first.operation.attempted_count == 100
    assert first.operation.successful_count == 100
    assert first.operation.error_count == 0
    assert first.operation.outcome_counts is not None
    assert first.operation.outcome_counts.success == 100
    assert first.operation.outcome_counts.external_error_count == 0
    assert first.operation.p95_ttft_ms is not None
    assert first.operation.error_rate_percent == "0"
    assert first.operation.evidence_pack_url is not None
    assert calls_after_first == 111
    assert endpoint_state.request_count == 222
    assert set(endpoint_state.authorization_headers) == {None}
    assert replay.replayed is True
    assert replay.operation == first.operation
    assert history_after_replay == (first.operation,)
    assert len(first_pack_sha256) == 64
    assert second.replayed is False
    assert second.operation.status is POCPerformanceRunStatus.COMPLETED
    assert [snapshot.operation_id for snapshot in complete_history] == [
        first.operation.operation_id,
        second.operation.operation_id,
    ]


def test_failed_preflight_is_blocked_without_fake_metrics_or_evidence(tmp_path):
    with _endpoint(fail_request=1) as (endpoint, endpoint_state):
        service = ProcessLocalPOCPerformanceRunService(
            lifecycle=_lifecycle(endpoint),
            output_root=(tmp_path / "runs").resolve(),
            worker_launcher=lambda target: target(),
        )
        result = service.start(
            POC_ID,
            execution_acknowledged=True,
            idempotency_key="run-blocked-performance",
        )

    operation = result.operation
    assert endpoint_state.request_count == 1
    assert operation.status is POCPerformanceRunStatus.BLOCKED
    assert operation.reason_code == "ENDPOINT_PREFLIGHT_FAILED"
    assert operation.verdict is None
    assert operation.p95_ttft_ms is None
    assert operation.error_rate_percent is None
    assert operation.evidence_pack_url is None
    assert operation.terminal_operation is not None
    assert operation.terminal_operation.status is PerformanceOperationStatus.BLOCKED
    assert operation.terminal_operation.terminal_reason == operation.reason_code
    assert len(operation.terminal_operation.input_digest) == 64


def test_independent_artifact_verifier_failure_releases_no_result(tmp_path):
    with _endpoint() as (endpoint, _):
        service = ProcessLocalPOCPerformanceRunService(
            lifecycle=_lifecycle(endpoint),
            output_root=(tmp_path / "runs").resolve(),
            artifact_reader=lambda path: (_ for _ in ()).throw(
                ValueError("forged artifact")
            ),
            worker_launcher=lambda target: target(),
        )
        result = service.start(
            POC_ID,
            execution_acknowledged=True,
            idempotency_key="run-verifier-failure",
        )

    operation = result.operation
    assert operation.status is POCPerformanceRunStatus.NOT_PROVEN
    assert operation.reason_code == "RUNNER_INTERNAL_FAILURE"
    assert operation.verdict is None
    assert operation.p95_ttft_ms is None
    assert operation.error_rate_percent is None
    assert operation.evidence_pack_url is None
    assert operation.terminal_operation is None


def test_fireworks_credential_is_bound_only_to_the_exact_allowed_endpoint(
    tmp_path,
):
    captured = {}

    def blocked_runner(**kwargs):
        captured.update(kwargs)
        operation = PerformanceOperation(
            idempotency_key_digest="a" * 64,
            input_digest="b" * 64,
            run_id="run_" + "c" * 32,
            status=PerformanceOperationStatus.BLOCKED,
            created_at=NOW,
            updated_at=NOW,
            terminal_reason="ENDPOINT_PREFLIGHT_FAILED",
        )
        return PerformanceRunResult(operation=operation, replayed=False)

    endpoint = "https://api.fireworks.ai/inference/v1/chat/completions"
    service = ProcessLocalPOCPerformanceRunService(
        lifecycle=_lifecycle(endpoint, provider="Fireworks AI"),
        output_root=(tmp_path / "runs").resolve(),
        runner=blocked_runner,
        worker_launcher=lambda target: target(),
        fireworks_api_key="server-owned-fireworks-key",
    )
    result = service.start(
        POC_ID,
        execution_acknowledged=True,
        idempotency_key="run-exact-fireworks",
    )

    assert result.operation.status is POCPerformanceRunStatus.BLOCKED
    assert result.operation.terminal_operation is not None
    assert result.operation.terminal_operation.input_digest == "b" * 64
    assert captured["api_key"] == "server-owned-fireworks-key"
    assert captured["credential_endpoint"] == endpoint
    assert captured["authorized_request_count"] == 111


def test_remote_target_outside_allowlist_never_reaches_runner(tmp_path):
    called = False

    def runner(**kwargs):
        nonlocal called
        called = True
        raise AssertionError

    service = ProcessLocalPOCPerformanceRunService(
        lifecycle=_lifecycle(
            "https://internal.example.test/v1/chat/completions",
            provider="Fireworks AI",
        ),
        output_root=(tmp_path / "runs").resolve(),
        runner=runner,
        worker_launcher=lambda target: target(),
        fireworks_api_key="server-owned-fireworks-key",
    )

    with pytest.raises(POCPerformanceRunInvalid):
        service.start(
            POC_ID,
            execution_acknowledged=True,
            idempotency_key="run-disallowed-remote",
        )
    assert called is False


def test_run_requires_explicit_acknowledgement_and_exact_idempotency(tmp_path):
    with _endpoint() as (endpoint, _):
        service = ProcessLocalPOCPerformanceRunService(
            lifecycle=_lifecycle(endpoint),
            output_root=(tmp_path / "runs").resolve(),
            worker_launcher=lambda target: target(),
        )
        with pytest.raises(POCPerformanceRunInvalid):
            service.start(
                POC_ID,
                execution_acknowledged=False,
                idempotency_key="run-without-acknowledgement",
            )
        service.start(
            POC_ID,
            execution_acknowledged=True,
            idempotency_key="run-exact-idempotency",
        )
        with pytest.raises(POCPerformanceRunConflict):
            service.start(
                POC_ID + "_other",
                execution_acknowledged=True,
                idempotency_key="run-exact-idempotency",
            )


def test_run_is_unavailable_before_confirmed_freeze(tmp_path):
    service = ProcessLocalPOCPerformanceRunService(
        lifecycle=_lifecycle(
            "http://127.0.0.1:8000/v1/chat/completions",
            freeze=False,
        ),
        output_root=(tmp_path / "runs").resolve(),
        worker_launcher=lambda target: target(),
    )

    with pytest.raises(POCPerformanceRunConflict):
        service.snapshot(POC_ID)
    with pytest.raises(POCPerformanceRunConflict):
        service.start(
            POC_ID,
            execution_acknowledged=True,
            idempotency_key="run-before-freeze",
        )
