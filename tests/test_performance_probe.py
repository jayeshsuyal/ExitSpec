from __future__ import annotations

import json
import threading
import time
from dataclasses import replace
from typing import Any

import pytest

from exitspec.performance_probe import (
    OpenAIHTTPTransport,
    ProbeConfig,
    ProbeConfigurationError,
    ProbeEvidenceError,
    ProbeOutcome,
    ProbePhase,
    ProbeRecord,
    ProbeRequest,
    StreamResponse,
    SyntheticPrompt,
    _run_probe as run_probe,
    _validate_probe_records as validate_probe_records,
    build_manifest,
    load_prompts_jsonl,
    manifest_json,
    records_jsonl,
    run_probe as run_live_probe,
    validate_probe_run,
)


ENDPOINT = "http://127.0.0.1:8000/v1/chat/completions"
REMOTE_ENDPOINT = "https://inference.example.test/v1/chat/completions"
SECRET_PROMPT = "Synthetic secret phrase that must never enter evidence."
API_KEY = "test-api-key-never-render"


def sse(*payloads: object, done: bool = True) -> bytes:
    events = [
        "data: " + json.dumps(payload, separators=(",", ":")) + "\n\n"
        for payload in payloads
    ]
    if done:
        events.append("data: [DONE]\n\n")
    return "".join(events).encode("utf-8")


def content_event(content: str | None) -> dict[str, Any]:
    return {"choices": [{"delta": {"content": content}}]}


class IncrementingClock:
    def __init__(self, start: int = 0, step: int = 10) -> None:
        self.value = start
        self.step = step
        self.lock = threading.Lock()

    def __call__(self) -> int:
        with self.lock:
            self.value += self.step
            return self.value


class SuccessfulTransport:
    def __init__(self, *, chunks: list[bytes] | None = None) -> None:
        self.chunks = chunks or [
            sse(
                {"choices": [{"delta": {"role": "assistant"}}]},
                content_event(""),
                content_event("measured but never persisted"),
            )
        ]
        self.requests: list[ProbeRequest] = []
        self.close_calls = 0

    def send(self, request: ProbeRequest) -> StreamResponse:
        self.requests.append(request)
        return StreamResponse(
            status_code=200,
            chunks=list(self.chunks),
            _closer=self._close,
        )

    def _close(self) -> None:
        self.close_calls += 1


def config(**changes: Any) -> ProbeConfig:
    values = {
        "endpoint": ENDPOINT,
        "model": "synthetic/tiny-model",
        "request_count": 1,
        "concurrency": 1,
        "warmup_count": 0,
        "timeout_seconds": 5,
        "max_tokens": 16,
    }
    values.update(changes)
    return ProbeConfig(**values)


def prompts() -> tuple[SyntheticPrompt, ...]:
    return (SyntheticPrompt("prompt-1", SECRET_PROMPT),)


def test_probe_excludes_warmup_and_records_first_nonempty_content_ttft():
    transport = SuccessfulTransport()
    clock = IncrementingClock(start=0, step=100)

    result = run_probe(
        config(warmup_count=1),
        prompts(),
        transport=transport,
        clock_ns=clock,
    )

    assert [record.request_id for record in result.records] == [
        "warmup-00001",
        "measured-00001",
    ]
    warmup, measured = result.records
    assert warmup.phase is ProbePhase.WARMUP
    assert warmup.included_in_measurement is False
    assert measured.phase is ProbePhase.MEASURED
    assert measured.included_in_measurement is True
    assert measured.outcome is ProbeOutcome.SUCCESS
    assert measured.http_status == 200
    assert measured.ttft_ns == 200
    assert measured.duration_ns == 300
    assert result.manifest.warmup_included_in_measurement is False
    assert transport.close_calls == 2


def test_artifacts_are_deterministic_json_and_never_persist_content_or_key():
    transport = SuccessfulTransport()

    result = run_probe(
        config(),
        prompts(),
        transport=transport,
        clock_ns=IncrementingClock(),
    )
    manifest_text = manifest_json(result.manifest)
    records_text = records_jsonl(result.records)
    complete_artifact = manifest_text + records_text + repr(
        OpenAIHTTPTransport(
            API_KEY,
            credential_endpoint=REMOTE_ENDPOINT,
        )
    )

    assert manifest_text == manifest_json(result.manifest)
    assert records_text == records_jsonl(tuple(reversed(result.records)))
    assert json.loads(manifest_text)["schema_version"].endswith(".v1")
    assert SECRET_PROMPT not in complete_artifact
    assert "measured but never persisted" not in complete_artifact
    assert API_KEY not in complete_artifact
    assert result.records[0].prompt_sha256 == prompts()[0].sha256
    assert transport.requests[0].json_body["stream"] is True
    assert SECRET_PROMPT in json.dumps(transport.requests[0].json_body)


def test_manifest_hash_uses_shared_rfc8785_number_canonicalization():
    integer_timeout = build_manifest(config(timeout_seconds=5), prompts())
    float_timeout = build_manifest(config(timeout_seconds=5.0), prompts())

    assert integer_timeout.manifest_sha256 == float_timeout.manifest_sha256
    assert manifest_json(integer_timeout) == manifest_json(float_timeout)


def test_jsonl_prompt_loading_is_strict_and_does_not_emit_content(tmp_path):
    path = tmp_path / "prompts.jsonl"
    path.write_text(
        '{"id":"alpha","content":"first synthetic prompt"}\n'
        '{"id":"beta","content":"second synthetic prompt"}\n',
        encoding="utf-8",
    )

    loaded = load_prompts_jsonl(path)
    manifest = build_manifest(config(), loaded)

    assert [prompt.prompt_id for prompt in loaded] == ["alpha", "beta"]
    assert "first synthetic prompt" not in manifest_json(manifest)
    assert [item.prompt_id for item in manifest.prompts] == ["alpha", "beta"]

    path.write_text(
        '{"id":"alpha","content":"ok","unexpected":"rejected"}\n',
        encoding="utf-8",
    )
    with pytest.raises(ProbeConfigurationError):
        load_prompts_jsonl(path)


@pytest.mark.parametrize(
    "changes",
    [
        {"endpoint": "http://inference.example.test/v1/chat/completions"},
        {"endpoint": "https://user:secret@example.test/v1/chat/completions"},
        {"endpoint": "https://example.test/v1/responses"},
        {"request_count": 0},
        {"request_count": True},
        {"concurrency": 2},
        {"warmup_count": -1},
        {"timeout_seconds": float("nan")},
        {"max_tokens": 0},
        {"request_count": 1000, "concurrency": 1, "timeout_seconds": 60},
    ],
)
def test_configuration_is_bounded_and_remote_plain_http_is_rejected(changes):
    with pytest.raises(ProbeConfigurationError):
        config(**changes)


class TrackingTransport:
    def __init__(self) -> None:
        self.active = 0
        self.maximum_active = 0
        self.lock = threading.Lock()

    def send(self, request: ProbeRequest) -> StreamResponse:
        with self.lock:
            self.active += 1
            self.maximum_active = max(self.maximum_active, self.active)
        time.sleep(0.01)

        def chunks():
            try:
                yield sse(content_event("ok"))
            finally:
                with self.lock:
                    self.active -= 1

        return StreamResponse(status_code=200, chunks=chunks())


def test_concurrency_is_bounded_and_every_attempt_has_one_record():
    transport = TrackingTransport()

    result = run_probe(
        config(request_count=8, concurrency=3),
        prompts(),
        transport=transport,
    )

    assert 1 < transport.maximum_active <= 3
    assert len(result.records) == 8
    assert len({record.request_id for record in result.records}) == 8
    assert all(record.outcome is ProbeOutcome.SUCCESS for record in result.records)


class OutcomeTransport:
    def send(self, request: ProbeRequest) -> StreamResponse:
        if request.request_id == "measured-00001":
            return StreamResponse(status_code=429, chunks=[])
        if request.request_id == "measured-00002":
            raise TimeoutError("sensitive upstream timeout detail")
        if request.request_id == "measured-00003":
            return StreamResponse(
                status_code=200,
                chunks=[sse(content_event("partial"), done=False)],
            )
        raise AssertionError("unexpected request")


class InternalBugTransport:
    def send(self, request: ProbeRequest) -> StreamResponse:
        raise AssertionError("synthetic adapter bug")


def test_unexpected_internal_bug_is_not_mislabeled_as_customer_failure():
    result = run_probe(
        config(),
        prompts(),
        transport=InternalBugTransport(),
        clock_ns=IncrementingClock(),
    )

    assert result.records[0].outcome is ProbeOutcome.INTERNAL_ERROR
    assert result.records[0].http_status is None
    assert result.records[0].ttft_ns is None


@pytest.mark.parametrize(
    ("close_error", "expected_outcome"),
    [
        (OSError("synthetic socket close failure"), ProbeOutcome.TRANSPORT_ERROR),
        (RuntimeError("synthetic cleanup bug"), ProbeOutcome.INTERNAL_ERROR),
    ],
)
def test_response_close_failure_preserves_external_vs_internal_attribution(
    close_error,
    expected_outcome,
):
    def fail_close() -> None:
        raise close_error

    class CloseFailureTransport:
        def send(self, request: ProbeRequest) -> StreamResponse:
            return StreamResponse(
                status_code=200,
                chunks=[sse(content_event("ok"))],
                _closer=fail_close,
            )

    result = run_probe(
        config(),
        prompts(),
        transport=CloseFailureTransport(),
        clock_ns=IncrementingClock(),
    )

    assert result.records[0].outcome is expected_outcome
    assert result.records[0].ttft_ns is None


def test_http_timeout_and_malformed_streams_are_terminal_sanitized_records():
    result = run_probe(
        config(request_count=3, concurrency=1),
        prompts(),
        transport=OutcomeTransport(),
        clock_ns=IncrementingClock(),
    )

    assert [record.outcome for record in result.records] == [
        ProbeOutcome.HTTP_ERROR,
        ProbeOutcome.TIMEOUT,
        ProbeOutcome.PROTOCOL_ERROR,
    ]
    assert [record.http_status for record in result.records] == [429, None, 200]
    assert all(record.ttft_ns is None for record in result.records)
    serialized = records_jsonl(result.records)
    assert "sensitive upstream timeout detail" not in serialized
    assert "partial" not in serialized


@pytest.mark.parametrize(
    "body",
    [
        b"data: not-json\n\ndata: [DONE]\n\n",
        sse({"not_choices": []}),
        sse(content_event(""), done=True),
        sse(content_event("hello"), done=False),
        b"event: message\ndata: {}\n\n",
        b"data: \xff\n\n",
    ],
)
def test_malformed_sse_fails_closed_as_protocol_error(body):
    result = run_probe(
        config(),
        prompts(),
        transport=SuccessfulTransport(chunks=[body]),
        clock_ns=IncrementingClock(),
    )

    record = result.records[0]
    assert record.outcome is ProbeOutcome.PROTOCOL_ERROR
    assert record.ttft_ns is None


def test_done_event_ends_measurement_without_waiting_for_connection_close():
    def keep_alive_stream():
        yield sse(content_event("complete"))
        raise TimeoutError("a persistent SSE connection stayed open")

    class KeepAliveTransport:
        def send(self, request: ProbeRequest) -> StreamResponse:
            return StreamResponse(
                status_code=200,
                chunks=keep_alive_stream(),
            )

    result = run_probe(
        config(),
        prompts(),
        transport=KeepAliveTransport(),
        clock_ns=IncrementingClock(),
    )

    assert result.records[0].outcome is ProbeOutcome.SUCCESS
    assert result.records[0].ttft_ns is not None


def test_successful_http_response_requires_event_stream_content_type():
    class WrongContentTypeTransport:
        def send(self, request: ProbeRequest) -> StreamResponse:
            return StreamResponse(
                status_code=200,
                chunks=[sse(content_event("ok"))],
                content_type="application/json",
            )

    result = run_probe(
        config(),
        prompts(),
        transport=WrongContentTypeTransport(),
        clock_ns=IncrementingClock(),
    )

    assert result.records[0].outcome is ProbeOutcome.PROTOCOL_ERROR
    assert result.records[0].ttft_ns is None


def test_validator_rejects_duplicate_missing_extra_and_malformed_records():
    result = run_probe(
        config(request_count=2, concurrency=1),
        prompts(),
        transport=SuccessfulTransport(),
        clock_ns=IncrementingClock(),
    )
    first, second = result.records

    with pytest.raises(ProbeEvidenceError, match="Duplicate"):
        validate_probe_records(result.manifest, (first, first))
    with pytest.raises(ProbeEvidenceError, match="missing"):
        validate_probe_records(result.manifest, (first,))
    with pytest.raises(ProbeEvidenceError, match="Unexpected"):
        validate_probe_records(
            result.manifest,
            (first, replace(second, request_id="measured-99999")),
        )
    with pytest.raises(ProbeEvidenceError, match="Successful"):
        validate_probe_records(
            result.manifest,
            (replace(first, ttft_ns=None), second),
        )


def test_validator_rejects_tampered_manifest_identity():
    result = run_probe(
        config(),
        prompts(),
        transport=SuccessfulTransport(),
        clock_ns=IncrementingClock(),
    )

    with pytest.raises(ProbeEvidenceError, match="Manifest hash"):
        validate_probe_records(
            replace(result.manifest, endpoint="https://tampered.example.test/v1/chat/completions"),
            result.records,
        )
    with pytest.raises(ProbeEvidenceError, match="prompt-set hash"):
        validate_probe_records(
            replace(result.manifest, prompt_set_sha256="0" * 64),
            result.records,
        )


def test_execution_identity_is_unique_and_mixed_runs_are_rejected():
    first = run_probe(
        config(request_count=2),
        prompts(),
        transport=SuccessfulTransport(),
        clock_ns=IncrementingClock(),
    )
    second = run_probe(
        config(request_count=2),
        prompts(),
        transport=SuccessfulTransport(),
        clock_ns=IncrementingClock(),
    )

    assert first.execution_id != second.execution_id
    with pytest.raises(ProbeEvidenceError, match="multiple executions"):
        validate_probe_records(
            first.manifest,
            (first.records[0], replace(first.records[1], execution_id=second.execution_id)),
        )


def test_public_probe_rejects_test_transports_and_clock_injection():
    with pytest.raises(ProbeConfigurationError, match="OpenAIHTTPTransport"):
        run_live_probe(
            config(),
            prompts(),
            transport=SuccessfulTransport(),
        )
    with pytest.raises(TypeError, match="clock_ns"):
        run_live_probe(
            config(),
            prompts(),
            transport=OpenAIHTTPTransport(),
            clock_ns=IncrementingClock(),  # type: ignore[call-arg]
        )


def test_cancellation_and_hard_deadline_produce_terminal_non_success_records():
    cancellation = threading.Event()
    cancellation.set()
    cancelled_transport = SuccessfulTransport()
    cancelled = run_probe(
        config(request_count=2),
        prompts(),
        transport=cancelled_transport,
        clock_ns=IncrementingClock(),
        cancellation=cancellation,
    )

    assert [record.outcome for record in cancelled.records] == [
        ProbeOutcome.CANCELLED,
        ProbeOutcome.CANCELLED,
    ]
    assert cancelled_transport.requests == []

    timed_out = run_probe(
        config(timeout_seconds=0.000000001),
        prompts(),
        transport=SuccessfulTransport(),
        clock_ns=IncrementingClock(step=10),
    )
    assert timed_out.records[0].outcome is ProbeOutcome.TIMEOUT
    assert timed_out.records[0].ttft_ns is None


def test_run_level_hash_rejects_forged_terminal_outcome():
    result = run_probe(
        config(),
        prompts(),
        transport=OutcomeTransport(),
        clock_ns=IncrementingClock(),
    )
    forged = replace(
        result.records[0],
        outcome=ProbeOutcome.SUCCESS,
        http_status=200,
        ttft_ns=0,
    )

    with pytest.raises(ProbeEvidenceError, match="artifact hash"):
        validate_probe_run(replace(result, records=(forged,)))


class FakeHTTPResponse:
    status = 200

    def __init__(self) -> None:
        self.reads = [
            sse(content_event("provider response that must not persist")),
            b"",
        ]
        self.close_calls = 0

    def read1(self, amount: int) -> bytes:
        assert amount == 4096
        return self.reads.pop(0)

    def getheader(self, name: str) -> str | None:
        assert name == "Content-Type"
        return "text/event-stream; charset=utf-8"

    def close(self) -> None:
        self.close_calls += 1


class FakeHTTPConnection:
    def __init__(self, response: FakeHTTPResponse) -> None:
        self.response = response
        self.requests: list[tuple[Any, ...]] = []
        self.close_calls = 0

    def request(self, *args, **kwargs) -> None:
        self.requests.append((args, kwargs))

    def getresponse(self) -> FakeHTTPResponse:
        return self.response

    def close(self) -> None:
        self.close_calls += 1


class FakeConnectionFactory:
    def __init__(self, connection: FakeHTTPConnection) -> None:
        self.connection = connection
        self.calls: list[tuple[Any, ...]] = []

    def __call__(self, *args, **kwargs) -> FakeHTTPConnection:
        self.calls.append((args, kwargs))
        return self.connection


def test_stdlib_transport_posts_streaming_body_without_following_redirects():
    response = FakeHTTPResponse()
    connection = FakeHTTPConnection(response)
    factory = FakeConnectionFactory(connection)
    transport = OpenAIHTTPTransport._for_testing(
        API_KEY,
        credential_endpoint=REMOTE_ENDPOINT,
        connection_factory=factory,
    )

    result = run_probe(
        config(endpoint=REMOTE_ENDPOINT),
        prompts(),
        transport=transport,
        clock_ns=IncrementingClock(),
    )

    assert result.records[0].outcome is ProbeOutcome.SUCCESS
    assert factory.calls == [
        (("inference.example.test", 443), {"timeout": 5.0})
    ]
    (method, path), request_options = connection.requests[0]
    assert (method, path) == ("POST", "/v1/chat/completions")
    assert request_options["headers"]["Authorization"] == "Bearer " + API_KEY
    request_body = json.loads(request_options["body"])
    assert request_body["stream"] is True
    assert request_body["messages"][0]["content"] == SECRET_PROMPT
    assert response.close_calls == 1
    assert connection.close_calls == 1


def test_stdlib_transport_refuses_credential_endpoint_mismatch_before_connect():
    response = FakeHTTPResponse()
    connection = FakeHTTPConnection(response)
    factory = FakeConnectionFactory(connection)
    transport = OpenAIHTTPTransport._for_testing(
        API_KEY,
        credential_endpoint=REMOTE_ENDPOINT,
        connection_factory=factory,
    )

    with pytest.raises(
        ProbeConfigurationError,
        match="unbound endpoint",
    ):
        transport.send(
            ProbeRequest(
                request_id="credential-mismatch",
                endpoint=(
                    "https://other.example.test/v1/chat/completions"
                ),
                json_body={"stream": True},
                timeout_seconds=5,
            )
        )

    assert factory.calls == []


def test_record_constructor_cannot_smuggle_a_nonterminal_outcome():
    result = run_probe(
        config(),
        prompts(),
        transport=SuccessfulTransport(),
        clock_ns=IncrementingClock(),
    )
    source = result.records[0]
    malformed = ProbeRecord(
        schema_version=source.schema_version,
        execution_id=source.execution_id,
        manifest_sha256=source.manifest_sha256,
        request_id=source.request_id,
        phase=source.phase,
        ordinal=source.ordinal,
        included_in_measurement=source.included_in_measurement,
        prompt_id=source.prompt_id,
        prompt_sha256=source.prompt_sha256,
        outcome="RUNNING",  # type: ignore[arg-type]
        http_status=None,
        ttft_ns=None,
        duration_ns=10,
    )

    with pytest.raises(ProbeEvidenceError, match="outcome"):
        validate_probe_records(result.manifest, (malformed,))
