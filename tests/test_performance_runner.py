from __future__ import annotations

import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from exitspec.canonical import canonical_json_bytes
from exitspec.confirmations import (
    ConfirmationDecision,
    record_confirmation,
)
from exitspec.contracts import freeze_confirmed_contract
from exitspec.models import TargetSystem, WorkloadReference
from exitspec.performance_operations import (
    PerformanceOperationConflict,
    PerformanceOperationStatus,
)
from exitspec.performance_runner import (
    PerformanceRunnerError,
    run_performance_proof,
)
from exitspec.runner import load_contract


PROJECT_ROOT = Path(__file__).resolve().parents[1]
APPROVED_CONTRACT_PATH = (
    PROJECT_ROOT
    / "examples/inference-performance/contracts/vllm-ttft-v1.yaml"
)
PROMPT_PATH = (
    PROJECT_ROOT
    / "examples/inference-performance/prompts/synthetic-latency-v1.jsonl"
)
WORKLOAD_PATH = (
    PROJECT_ROOT
    / "examples/inference-performance/workloads/concurrency-4-v1.json"
)
FIXED_TIME = datetime(2026, 7, 28, 14, 0, tzinfo=timezone.utc)
CONFIRMATION_KEY = "runner-confirmation-v1"
EXECUTION_KEY = "runner-execution-v1"
API_KEY = "runner-api-key-must-never-persist"
RESPONSE_TEXT = "runner-response-text-must-never-persist"


class _EndpointState:
    def __init__(self, *, fail_request: int | None = None) -> None:
        self.fail_request = fail_request
        self.request_count = 0
        self.active = 0
        self.peak_active = 0
        self.authorization_headers: list[str | None] = []
        self.lock = threading.Lock()

    def begin(self, authorization: str | None) -> int:
        with self.lock:
            self.request_count += 1
            self.active += 1
            self.peak_active = max(self.peak_active, self.active)
            self.authorization_headers.append(authorization)
            return self.request_count

    def end(self) -> None:
        with self.lock:
            self.active -= 1


@contextmanager
def _endpoint(
    *,
    fail_request: int | None = None,
    malformed_request: int | None = None,
):
    state = _EndpointState(fail_request=fail_request)

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self) -> None:
            request_number = state.begin(
                self.headers.get("Authorization")
            )
            try:
                content_length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(content_length)
                request = json.loads(body)
                assert request["stream"] is True
                if request_number == state.fail_request:
                    response = b'{"error":"synthetic unavailable"}'
                    self.send_response(503)
                    self.send_header("Content-Type", "application/json")
                elif request_number == malformed_request:
                    response = b"data: not-json\n\n"
                    self.send_response(200)
                    self.send_header(
                        "Content-Type",
                        "text/event-stream; charset=utf-8",
                    )
                else:
                    response = (
                        b'data: {"choices":[{"delta":{"content":"'
                        + RESPONSE_TEXT.encode()
                        + b'"}}]}\n\n'
                        + b"data: [DONE]\n\n"
                    )
                    self.send_response(200)
                    self.send_header(
                        "Content-Type",
                        "text/event-stream; charset=utf-8",
                    )
                self.send_header("Content-Length", str(len(response)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(response)
                self.wfile.flush()
            finally:
                state.end()

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield (
            "http://127.0.0.1:{0}/v1/chat/completions".format(
                server.server_port
            ),
            state,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _write_bundle(
    root: Path,
    endpoint: str,
    *,
    model: str = "Qwen/Qwen2.5-0.5B-Instruct",
) -> tuple[Path, Path]:
    root.mkdir(parents=True)
    prompt_bytes = PROMPT_PATH.read_bytes()
    (root / "prompts.jsonl").write_bytes(prompt_bytes)

    workload = json.loads(WORKLOAD_PATH.read_bytes())
    workload.update(
        {
            "endpoint": endpoint,
            "model": model,
            "prompt_fixture_path": "prompts.jsonl",
            "prompt_fixture_sha256": hashlib.sha256(
                prompt_bytes
            ).hexdigest(),
        }
    )
    workload_bytes = json.dumps(
        workload,
        indent=2,
        sort_keys=False,
    ).encode("utf-8") + b"\n"
    (root / "workload.json").write_bytes(workload_bytes)

    approved = load_contract(APPROVED_CONTRACT_PATH).model_copy(
        update={
            "target_system": TargetSystem(
                provider="synthetic-local-sse",
                endpoint_class="openai-compatible-chat-completions",
                model=model,
            ),
            "workload": WorkloadReference(
                fixture_path="workload.json",
                sha256=hashlib.sha256(workload_bytes).hexdigest(),
            ),
        }
    )
    confirmation = record_confirmation(
        approved,
        confirmer_identity="synthetic-customer@example.test",
        decision=ConfirmationDecision.CONFIRM,
        agreement_acknowledged=True,
        rationale="The exact synthetic performance test is approved.",
        idempotency_key=CONFIRMATION_KEY,
        decided_at=FIXED_TIME,
    )
    frozen = freeze_confirmed_contract(
        approved,
        confirmation,
        frozen_at=FIXED_TIME,
    )
    contract_path = root / "contract.frozen.json"
    confirmation_path = root / "confirmation.json"
    contract_path.write_bytes(
        json.dumps(
            frozen.model_dump(mode="json"),
            indent=2,
            sort_keys=True,
        ).encode()
        + b"\n"
    )
    confirmation_path.write_bytes(
        json.dumps(
            confirmation.model_dump(mode="json"),
            indent=2,
            sort_keys=True,
        ).encode()
        + b"\n"
    )
    return contract_path, confirmation_path


def _run(
    bundle: Path,
    output: Path,
    contract_path: Path,
    confirmation_path: Path,
):
    endpoint = json.loads(
        (bundle / "workload.json").read_bytes()
    )["endpoint"]
    return run_performance_proof(
        contract_path=contract_path,
        confirmation_path=confirmation_path,
        bundle_root=bundle,
        output_root=output,
        idempotency_key=EXECUTION_KEY,
        api_key=API_KEY,
        credential_endpoint=endpoint,
        authorized_request_count=111,
        clock=lambda: FIXED_TIME,
    )


def test_full_live_sse_loop_returns_only_recomputed_verified_pass(
    tmp_path: Path,
):
    with _endpoint() as (endpoint, state):
        bundle = tmp_path / "bundle"
        output = tmp_path / "runs"
        contract_path, confirmation_path = _write_bundle(
            bundle,
            endpoint,
        )

        first = _run(
            bundle,
            output,
            contract_path,
            confirmation_path,
        )
        calls_after_first = state.request_count
        replay = _run(
            bundle,
            output,
            contract_path,
            confirmation_path,
        )

    assert first.verdict is not None
    assert first.verdict.value == "PASS"
    assert first.operation.status is PerformanceOperationStatus.COMPLETED
    assert first.artifacts is not None
    assert first.artifacts.decision_packet_html.count(b'class="fact-row') == 2
    assert first.artifacts.decision_packet_html == (
        replay.artifacts.decision_packet_html
    )
    assert replay.replayed is True
    assert state.request_count == calls_after_first == 111
    assert state.peak_active <= 4
    assert set(state.authorization_headers) == {"Bearer " + API_KEY}
    persisted = b"".join(first.artifacts.files.values())
    assert API_KEY.encode() not in persisted
    assert RESPONSE_TEXT.encode() not in persisted


def test_exactly_one_measured_error_of_one_hundred_fails_strict_rule(
    tmp_path: Path,
):
    # Request 1 is preflight; 2-11 are warmups; request 12 is measured.
    with _endpoint(fail_request=12) as (endpoint, state):
        bundle = tmp_path / "bundle"
        contract_path, confirmation_path = _write_bundle(
            bundle,
            endpoint,
        )
        result = _run(
            bundle,
            tmp_path / "runs",
            contract_path,
            confirmation_path,
        )

    assert state.request_count == 111
    assert result.verdict is not None
    assert result.verdict.value == "FAIL"
    assert result.decision.performance_verdict.error_count == 1
    assert (
        result.decision.performance_verdict.error_rate.observed_rate
        == Decimal("0.01")
    )


def test_failed_preflight_is_blocked_and_never_starts_measurement(
    tmp_path: Path,
):
    with _endpoint(fail_request=1) as (endpoint, state):
        bundle = tmp_path / "bundle"
        contract_path, confirmation_path = _write_bundle(
            bundle,
            endpoint,
        )
        result = _run(
            bundle,
            tmp_path / "runs",
            contract_path,
            confirmation_path,
        )

    assert state.request_count == 1
    assert result.operation.status is PerformanceOperationStatus.BLOCKED
    assert result.operation.terminal_reason == "ENDPOINT_PREFLIGHT_FAILED"
    assert result.verdict is None
    assert result.artifacts is None


def test_malformed_preflight_is_not_proven_not_external_block(
    tmp_path: Path,
):
    with _endpoint(malformed_request=1) as (endpoint, state):
        bundle = tmp_path / "bundle"
        contract_path, confirmation_path = _write_bundle(
            bundle,
            endpoint,
        )
        result = _run(
            bundle,
            tmp_path / "runs",
            contract_path,
            confirmation_path,
        )

    assert state.request_count == 1
    assert result.operation.status is PerformanceOperationStatus.NOT_PROVEN
    assert result.operation.terminal_reason == "PREFLIGHT_NOT_PROVEN"
    assert result.verdict is None
    assert result.artifacts is None


def test_invalid_api_key_is_rejected_before_reservation(
    tmp_path: Path,
):
    with _endpoint() as (endpoint, state):
        bundle = tmp_path / "bundle"
        output = tmp_path / "runs"
        contract_path, confirmation_path = _write_bundle(
            bundle,
            endpoint,
        )

        with pytest.raises(ValueError, match="api_key"):
            run_performance_proof(
                contract_path=contract_path,
                confirmation_path=confirmation_path,
                bundle_root=bundle,
                output_root=output,
                idempotency_key=EXECUTION_KEY,
                api_key=" invalid ",
                credential_endpoint=endpoint,
                authorized_request_count=111,
                clock=lambda: FIXED_TIME,
            )

    assert state.request_count == 0
    assert not output.exists()


@pytest.mark.parametrize(
    ("credential_endpoint", "authorized_request_count", "message"),
    [
        (
            "https://other.example.test/v1/chat/completions",
            111,
            "exactly match",
        ),
        (None, 111, "credential_endpoint"),
        ("__FROZEN__", 110, "exact planned request count"),
    ],
)
def test_credentialed_execution_requires_exact_egress_authority_before_network(
    tmp_path: Path,
    credential_endpoint: str | None,
    authorized_request_count: int,
    message: str,
):
    with _endpoint() as (endpoint, state):
        bundle = tmp_path / "bundle"
        output = tmp_path / "runs"
        contract_path, confirmation_path = _write_bundle(
            bundle,
            endpoint,
        )
        resolved_credential_endpoint = (
            endpoint
            if credential_endpoint == "__FROZEN__"
            else credential_endpoint
        )

        with pytest.raises(
            (ValueError, PerformanceRunnerError),
            match=message,
        ):
            run_performance_proof(
                contract_path=contract_path,
                confirmation_path=confirmation_path,
                bundle_root=bundle,
                output_root=output,
                idempotency_key=EXECUTION_KEY,
                api_key=API_KEY,
                credential_endpoint=resolved_credential_endpoint,
                authorized_request_count=authorized_request_count,
                clock=lambda: FIXED_TIME,
            )

    assert state.request_count == 0
    assert not output.exists()


def test_credential_free_remote_execution_still_requires_exact_request_authority(
    tmp_path: Path,
):
    endpoint = "https://inference.example.test/v1/chat/completions"
    bundle = tmp_path / "bundle"
    output = tmp_path / "runs"
    contract_path, confirmation_path = _write_bundle(
        bundle,
        endpoint,
    )

    with pytest.raises(
        PerformanceRunnerError,
        match="exact planned request count",
    ):
        run_performance_proof(
            contract_path=contract_path,
            confirmation_path=confirmation_path,
            bundle_root=bundle,
            output_root=output,
            idempotency_key=EXECUTION_KEY,
            clock=lambda: FIXED_TIME,
        )

    assert not output.exists()


def test_confirmation_identity_mismatch_is_rejected_before_reservation_or_network(
    tmp_path: Path,
):
    with _endpoint() as (endpoint, state):
        bundle = tmp_path / "bundle"
        output = tmp_path / "runs"
        contract_path, confirmation_path = _write_bundle(
            bundle,
            endpoint,
        )
        confirmation = json.loads(confirmation_path.read_bytes())
        confirmation["idempotency_key"] = "forged-confirmation-key"
        confirmation_path.write_bytes(canonical_json_bytes(confirmation))

        with pytest.raises(
            PerformanceRunnerError,
            match="confirmation is invalid",
        ):
            _run(
                bundle,
                output,
                contract_path,
                confirmation_path,
            )

    assert state.request_count == 0
    assert not output.exists()


def test_same_key_with_changed_confirmation_conflicts_before_network(
    tmp_path: Path,
):
    with _endpoint() as (endpoint, state):
        bundle = tmp_path / "bundle"
        output = tmp_path / "runs"
        contract_path, confirmation_path = _write_bundle(
            bundle,
            endpoint,
        )
        _run(
            bundle,
            output,
            contract_path,
            confirmation_path,
        )
        calls_before_conflict = state.request_count
        confirmation = json.loads(confirmation_path.read_bytes())
        confirmation["rationale"] = (
            "The customer supplied different confirmation metadata."
        )
        confirmation_path.write_bytes(canonical_json_bytes(confirmation))

        with pytest.raises(PerformanceOperationConflict):
            _run(
                bundle,
                output,
                contract_path,
                confirmation_path,
            )

    assert state.request_count == calls_before_conflict


def test_concurrent_same_key_executes_one_network_loop_and_replays_pack(
    tmp_path: Path,
):
    with _endpoint() as (endpoint, state):
        bundle = tmp_path / "bundle"
        output = tmp_path / "runs"
        contract_path, confirmation_path = _write_bundle(
            bundle,
            endpoint,
        )

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(
                executor.map(
                    lambda _: _run(
                        bundle,
                        output,
                        contract_path,
                        confirmation_path,
                    ),
                    range(2),
                )
            )
        replay = _run(
            bundle,
            output,
            contract_path,
            confirmation_path,
        )

    assert state.request_count == 111
    assert sum(result.verdict is not None for result in results) == 1
    assert replay.verdict is not None
    assert replay.verdict.value == "PASS"


def test_same_key_with_changed_frozen_inputs_conflicts_before_network(
    tmp_path: Path,
):
    with _endpoint() as (endpoint, state):
        first_bundle = tmp_path / "bundle-one"
        second_bundle = tmp_path / "bundle-two"
        output = tmp_path / "runs"
        first_contract, first_confirmation = _write_bundle(
            first_bundle,
            endpoint,
        )
        second_contract, second_confirmation = _write_bundle(
            second_bundle,
            endpoint,
            model="Qwen/Qwen2.5-1.5B-Instruct",
        )
        _run(
            first_bundle,
            output,
            first_contract,
            first_confirmation,
        )
        calls_before_conflict = state.request_count

        with pytest.raises(PerformanceOperationConflict):
            _run(
                second_bundle,
                output,
                second_contract,
                second_confirmation,
            )

    assert state.request_count == calls_before_conflict


def test_fully_rehashed_forged_verdict_is_rejected_on_replay(
    tmp_path: Path,
):
    with _endpoint() as (endpoint, _state):
        bundle = tmp_path / "bundle"
        output = tmp_path / "runs"
        contract_path, confirmation_path = _write_bundle(
            bundle,
            endpoint,
        )
        result = _run(
            bundle,
            output,
            contract_path,
            confirmation_path,
        )

    run_dir = result.artifacts.run_dir
    verdict_path = run_dir / "verdicts.json"
    forged = json.loads(verdict_path.read_bytes())
    forged["verdict"] = "FAIL"
    forged_bytes = canonical_json_bytes(forged)
    verdict_path.write_bytes(forged_bytes)

    registry_path = run_dir / "evidence-artifacts.json"
    registry = json.loads(registry_path.read_bytes())
    verdict_entry = next(
        item
        for item in registry["artifacts"]
        if item["path"] == "verdicts.json"
    )
    verdict_entry["size_bytes"] = len(forged_bytes)
    verdict_entry["sha256"] = hashlib.sha256(forged_bytes).hexdigest()
    registry_bytes = canonical_json_bytes(registry)
    registry_path.write_bytes(registry_bytes)

    inventory_path = run_dir / "artifact-hashes.json"
    inventory = json.loads(inventory_path.read_bytes())
    inventory_verdict = next(
        item
        for item in inventory["artifacts"]
        if item["path"] == "verdicts.json"
    )
    inventory_verdict.update(verdict_entry)
    inventory_registry = next(
        item
        for item in inventory["artifacts"]
        if item["path"] == "evidence-artifacts.json"
    )
    inventory_registry["size_bytes"] = len(registry_bytes)
    inventory_registry["sha256"] = hashlib.sha256(
        registry_bytes
    ).hexdigest()
    inventory_path.write_bytes(canonical_json_bytes(inventory))

    with pytest.raises(
        PerformanceRunnerError,
        match="failed closed|match",
    ):
        _run(
            bundle,
            output,
            contract_path,
            confirmation_path,
        )
