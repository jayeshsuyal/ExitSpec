"""Bounded streaming latency probe for OpenAI-compatible chat endpoints.

This module is deliberately independent from ExitSpec's contract, runner, and
verdict layers.  It produces deterministic, JSON-serializable measurement
artifacts that a later adapter can validate and translate into typed facts.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import math
import re
import threading
import time
import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Final, Protocol
from urllib.parse import urlsplit

from .canonical import canonical_json_bytes


PROBE_SCHEMA_VERSION: Final = "exitspec.performance-probe.v1"
_USER_AGENT: Final = "ExitSpec/0.1 performance-probe"
_MAX_PROMPTS: Final = 10_000
_MAX_PROMPT_CHARS: Final = 32_768
_MAX_PROMPT_FILE_BYTES: Final = 4 * 1024 * 1024
_MAX_REQUEST_COUNT: Final = 1_000
_MAX_WARMUP_COUNT: Final = 100
_MAX_CONCURRENCY: Final = 32
_MAX_TIMEOUT_SECONDS: Final = 60.0
_MAX_TOKENS: Final = 2_048
_MAX_STREAM_BYTES: Final = 1024 * 1024
_MAX_WORST_CASE_RUN_SECONDS: Final = 15 * 60
_PROMPT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_EXECUTION_ID = re.compile(r"run_[0-9a-f]{32}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class ProbeConfigurationError(ValueError):
    """The requested probe is invalid or exceeds its safety bounds."""


class ProbeProtocolError(RuntimeError):
    """The endpoint did not return a valid terminal OpenAI SSE stream."""


class ProbeEvidenceError(RuntimeError):
    """The generated measurement records are incomplete or inconsistent."""


class ProbePhase(StrEnum):
    WARMUP = "WARMUP"
    MEASURED = "MEASURED"


class ProbeOutcome(StrEnum):
    SUCCESS = "SUCCESS"
    HTTP_ERROR = "HTTP_ERROR"
    TIMEOUT = "TIMEOUT"
    PROTOCOL_ERROR = "PROTOCOL_ERROR"
    TRANSPORT_ERROR = "TRANSPORT_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True, slots=True)
class SyntheticPrompt:
    """One synthetic prompt; its content never enters emitted artifacts."""

    prompt_id: str
    content: str = field(repr=False)

    def __post_init__(self) -> None:
        if type(self.prompt_id) is not str or not _PROMPT_ID.fullmatch(
            self.prompt_id
        ):
            raise ProbeConfigurationError("prompt_id is invalid.")
        if (
            type(self.content) is not str
            or not self.content.strip()
            or len(self.content) > _MAX_PROMPT_CHARS
        ):
            raise ProbeConfigurationError("Prompt content is invalid.")

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.content.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ProbeConfig:
    """Frozen execution inputs for one bounded latency probe."""

    endpoint: str
    model: str
    request_count: int
    concurrency: int
    warmup_count: int = 0
    timeout_seconds: float = 30.0
    max_tokens: int = 64
    max_stream_bytes: int = _MAX_STREAM_BYTES

    def __post_init__(self) -> None:
        _validate_endpoint(self.endpoint)
        if (
            type(self.model) is not str
            or not self.model.strip()
            or self.model != self.model.strip()
            or len(self.model) > 512
        ):
            raise ProbeConfigurationError("model is invalid.")
        _bounded_int(
            self.request_count,
            name="request_count",
            minimum=1,
            maximum=_MAX_REQUEST_COUNT,
        )
        _bounded_int(
            self.concurrency,
            name="concurrency",
            minimum=1,
            maximum=_MAX_CONCURRENCY,
        )
        if self.concurrency > self.request_count:
            raise ProbeConfigurationError(
                "concurrency cannot exceed request_count."
            )
        _bounded_int(
            self.warmup_count,
            name="warmup_count",
            minimum=0,
            maximum=_MAX_WARMUP_COUNT,
        )
        _bounded_float(
            self.timeout_seconds,
            name="timeout_seconds",
            minimum_exclusive=0,
            maximum=_MAX_TIMEOUT_SECONDS,
        )
        _bounded_int(
            self.max_tokens,
            name="max_tokens",
            minimum=1,
            maximum=_MAX_TOKENS,
        )
        _bounded_int(
            self.max_stream_bytes,
            name="max_stream_bytes",
            minimum=1,
            maximum=_MAX_STREAM_BYTES,
        )
        warmup_batches = math.ceil(self.warmup_count / self.concurrency)
        measured_batches = math.ceil(self.request_count / self.concurrency)
        worst_case_seconds = (
            warmup_batches + measured_batches
        ) * float(self.timeout_seconds)
        if worst_case_seconds > _MAX_WORST_CASE_RUN_SECONDS:
            raise ProbeConfigurationError(
                "Worst-case run duration exceeds the 15-minute safety budget."
            )


@dataclass(frozen=True, slots=True)
class PromptDescriptor:
    prompt_id: str
    sha256: str

    def to_dict(self) -> dict[str, str]:
        return {"prompt_id": self.prompt_id, "sha256": self.sha256}


@dataclass(frozen=True, slots=True)
class ProbeManifest:
    """Stable identity for the exact workload and measurement semantics."""

    schema_version: str
    manifest_sha256: str
    endpoint: str
    model: str
    request_count: int
    concurrency: int
    warmup_count: int
    timeout_seconds: float
    max_tokens: int
    max_stream_bytes: int
    first_token_definition: str
    warmup_included_in_measurement: bool
    prompts: tuple[PromptDescriptor, ...]
    prompt_set_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "concurrency": self.concurrency,
            "endpoint": self.endpoint,
            "first_token_definition": self.first_token_definition,
            "manifest_sha256": self.manifest_sha256,
            "max_stream_bytes": self.max_stream_bytes,
            "max_tokens": self.max_tokens,
            "model": self.model,
            "prompt_set_sha256": self.prompt_set_sha256,
            "prompts": [prompt.to_dict() for prompt in self.prompts],
            "request_count": self.request_count,
            "schema_version": self.schema_version,
            "timeout_seconds": self.timeout_seconds,
            "warmup_count": self.warmup_count,
            "warmup_included_in_measurement": (
                self.warmup_included_in_measurement
            ),
        }


@dataclass(frozen=True, slots=True)
class ProbeRecord:
    """One and only one terminal record for an attempted request."""

    schema_version: str
    execution_id: str
    manifest_sha256: str
    request_id: str
    phase: ProbePhase
    ordinal: int
    included_in_measurement: bool
    prompt_id: str
    prompt_sha256: str
    outcome: ProbeOutcome
    http_status: int | None
    ttft_ns: int | None
    duration_ns: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "duration_ns": self.duration_ns,
            "execution_id": self.execution_id,
            "http_status": self.http_status,
            "included_in_measurement": self.included_in_measurement,
            "manifest_sha256": self.manifest_sha256,
            "ordinal": self.ordinal,
            "outcome": self.outcome.value,
            "phase": self.phase.value,
            "prompt_id": self.prompt_id,
            "prompt_sha256": self.prompt_sha256,
            "request_id": self.request_id,
            "schema_version": self.schema_version,
            "ttft_ns": self.ttft_ns,
        }


@dataclass(frozen=True, slots=True)
class ProbeRun:
    execution_id: str
    manifest: ProbeManifest
    records_sha256: str
    records: tuple[ProbeRecord, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "manifest": self.manifest.to_dict(),
            "records_sha256": self.records_sha256,
            "records": [record.to_dict() for record in self.records],
        }


@dataclass(frozen=True, slots=True)
class ProbeRequest:
    """Transport input. The JSON body is intentionally hidden from repr."""

    request_id: str
    endpoint: str
    timeout_seconds: float
    json_body: Mapping[str, Any] = field(repr=False)


@dataclass(slots=True)
class StreamResponse:
    """Status plus raw streaming bytes returned by a transport."""

    status_code: int
    chunks: Iterable[bytes] = field(repr=False)
    content_type: str | None = "text/event-stream"
    _closer: Callable[[], None] = field(
        default=lambda: None,
        repr=False,
    )
    _closed: bool = field(default=False, init=False, repr=False)

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._closer()


class StreamingTransport(Protocol):
    def send(self, request: ProbeRequest) -> StreamResponse:
        """Send exactly one streaming request."""


class OpenAIHTTPTransport:
    """Small stdlib transport with no redirect or response persistence.

    Remote endpoints must use HTTPS. Plain HTTP is accepted only for a local
    vLLM process on a loopback host.
    """

    __slots__ = (
        "__api_key",
        "__connection_factory",
        "__credential_endpoint",
    )

    def __init__(
        self,
        api_key: str | None = None,
        *,
        credential_endpoint: str | None = None,
    ) -> None:
        if api_key is not None:
            if (
                type(api_key) is not str
                or not api_key
                or api_key != api_key.strip()
                or any(character.isspace() for character in api_key)
                or any(
                    ord(character) < 0x21 or ord(character) > 0x7E
                    for character in api_key
                )
            ):
                raise ProbeConfigurationError("api_key is invalid.")
            if credential_endpoint is None:
                raise ProbeConfigurationError(
                    "credential_endpoint is required when api_key is set."
                )
            _validate_endpoint(credential_endpoint)
        elif credential_endpoint is not None:
            raise ProbeConfigurationError(
                "credential_endpoint requires an api_key."
            )
        self.__api_key = api_key
        self.__credential_endpoint = credential_endpoint
        self.__connection_factory = None

    @classmethod
    def _for_testing(
        cls,
        api_key: str | None,
        *,
        credential_endpoint: str | None = None,
        connection_factory: Callable[..., Any],
    ) -> OpenAIHTTPTransport:
        if not callable(connection_factory):
            raise ProbeConfigurationError("connection_factory must be callable.")
        transport = cls(
            api_key,
            credential_endpoint=credential_endpoint,
        )
        transport.__connection_factory = connection_factory
        return transport

    def _uses_bundled_network(self) -> bool:
        return self.__connection_factory is None

    def __repr__(self) -> str:
        return "OpenAIHTTPTransport(api_key=<redacted>)"

    def send(self, request: ProbeRequest) -> StreamResponse:
        if type(request) is not ProbeRequest:
            raise TypeError("request must be ProbeRequest.")
        parts = _validate_endpoint(request.endpoint)
        body = _canonical_json_bytes(dict(request.json_body))
        port = (443 if parts.scheme == "https" else 80) if parts.port is None else parts.port
        path = parts.path or "/"
        connection_factory = self.__connection_factory
        if connection_factory is None:
            connection_factory = (
                http.client.HTTPSConnection
                if parts.scheme == "https"
                else http.client.HTTPConnection
            )
        headers = {
            "Accept": "text/event-stream",
            "Content-Type": "application/json",
            "User-Agent": _USER_AGENT,
        }
        if self.__api_key is not None:
            if request.endpoint != self.__credential_endpoint:
                raise ProbeConfigurationError(
                    "Refusing to send a credential to an unbound endpoint."
                )
            headers["Authorization"] = "Bearer " + self.__api_key

        connection: Any | None = None
        response: Any | None = None
        deadline_monotonic = time.monotonic() + request.timeout_seconds
        try:
            connection = connection_factory(
                parts.hostname,
                port,
                timeout=request.timeout_seconds,
            )
            connect = getattr(connection, "connect", None)
            if callable(connect):
                connect()
            _set_remaining_socket_timeout(
                connection,
                deadline_monotonic,
            )
            connection.request(
                "POST",
                path,
                body=body,
                headers=headers,
            )
            _set_remaining_socket_timeout(
                connection,
                deadline_monotonic,
            )
            response = connection.getresponse()
            _set_remaining_socket_timeout(
                connection,
                deadline_monotonic,
            )
            status = response.status
            if (
                isinstance(status, bool)
                or not isinstance(status, int)
                or not 100 <= status <= 599
            ):
                raise ProbeProtocolError("HTTP status is invalid.")
        except Exception:
            if response is not None:
                try:
                    response.close()
                except Exception:
                    pass
            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    pass
            raise

        get_header = getattr(response, "getheader", None)
        content_type = (
            get_header("Content-Type") if callable(get_header) else None
        )
        owner = _HTTPResponseOwner(
            response,
            connection,
            deadline_monotonic=deadline_monotonic,
        )
        return StreamResponse(
            status_code=status,
            chunks=owner.iter_chunks(),
            content_type=content_type,
            _closer=owner.close,
        )


class _HTTPResponseOwner:
    __slots__ = (
        "__closed",
        "__connection",
        "__deadline_monotonic",
        "__response",
        "__lock",
    )

    def __init__(
        self,
        response: Any,
        connection: Any,
        *,
        deadline_monotonic: float,
    ) -> None:
        self.__response = response
        self.__connection = connection
        self.__deadline_monotonic = deadline_monotonic
        self.__closed = False
        self.__lock = threading.Lock()

    def iter_chunks(self) -> Iterable[bytes]:
        read_chunk = getattr(self.__response, "read1", None)
        if not callable(read_chunk):
            read_chunk = self.__response.read
        while True:
            _set_remaining_socket_timeout(
                self.__connection,
                self.__deadline_monotonic,
            )
            chunk = read_chunk(4096)
            if not chunk:
                break
            yield chunk

    def close(self) -> None:
        with self.__lock:
            if self.__closed:
                return
            self.__closed = True
        pending_error: Exception | None = None
        try:
            self.__response.close()
        except Exception as error:
            pending_error = error
        try:
            self.__connection.close()
        except Exception as error:
            pending_error = pending_error or error
        if pending_error is not None:
            raise pending_error


def _set_remaining_socket_timeout(
    connection: Any,
    deadline_monotonic: float,
) -> float:
    """Apply one absolute network deadline before every blocking phase."""

    remaining = deadline_monotonic - time.monotonic()
    if remaining <= 0:
        raise TimeoutError
    socket = getattr(connection, "sock", None)
    set_timeout = getattr(socket, "settimeout", None)
    if callable(set_timeout):
        set_timeout(remaining)
    return remaining


def load_prompts_jsonl(path: str | Path) -> tuple[SyntheticPrompt, ...]:
    """Load a strict, bounded JSONL file containing id/content objects."""

    source = Path(path)
    try:
        if source.stat().st_size > _MAX_PROMPT_FILE_BYTES:
            raise ProbeConfigurationError("Prompt file exceeds the size limit.")
        text = source.read_text(encoding="utf-8")
    except ProbeConfigurationError:
        raise
    except (OSError, UnicodeError):
        raise ProbeConfigurationError("Prompt file could not be read.") from None

    prompts: list[SyntheticPrompt] = []
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        if not raw_line.strip():
            raise ProbeConfigurationError(
                f"Prompt JSONL line {line_number} is blank."
            )
        try:
            value = json.loads(
                raw_line,
                parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
            )
        except (json.JSONDecodeError, ValueError):
            raise ProbeConfigurationError(
                f"Prompt JSONL line {line_number} is invalid."
            ) from None
        if type(value) is not dict or set(value) != {"id", "content"}:
            raise ProbeConfigurationError(
                f"Prompt JSONL line {line_number} has an invalid shape."
            )
        prompts.append(SyntheticPrompt(value["id"], value["content"]))

    return _normalize_prompts(prompts)


def build_manifest(
    config: ProbeConfig,
    prompts: Sequence[SyntheticPrompt],
) -> ProbeManifest:
    normalized = _normalize_prompts(prompts)
    descriptors = tuple(
        PromptDescriptor(prompt.prompt_id, prompt.sha256) for prompt in normalized
    )
    descriptor_dicts = [descriptor.to_dict() for descriptor in descriptors]
    prompt_set_sha256 = hashlib.sha256(
        _canonical_json_bytes(descriptor_dicts)
    ).hexdigest()
    identity = {
        "concurrency": config.concurrency,
        "endpoint": config.endpoint,
        "first_token_definition": "first_nonempty_choices_delta_content_v1",
        "max_stream_bytes": config.max_stream_bytes,
        "max_tokens": config.max_tokens,
        "model": config.model,
        "prompt_set_sha256": prompt_set_sha256,
        "prompts": descriptor_dicts,
        "request_count": config.request_count,
        "schema_version": PROBE_SCHEMA_VERSION,
        "timeout_seconds": float(config.timeout_seconds),
        "warmup_count": config.warmup_count,
        "warmup_included_in_measurement": False,
    }
    manifest_sha256 = hashlib.sha256(
        _canonical_json_bytes(identity)
    ).hexdigest()
    return ProbeManifest(
        schema_version=PROBE_SCHEMA_VERSION,
        manifest_sha256=manifest_sha256,
        endpoint=config.endpoint,
        model=config.model,
        request_count=config.request_count,
        concurrency=config.concurrency,
        warmup_count=config.warmup_count,
        timeout_seconds=float(config.timeout_seconds),
        max_tokens=config.max_tokens,
        max_stream_bytes=config.max_stream_bytes,
        first_token_definition="first_nonempty_choices_delta_content_v1",
        warmup_included_in_measurement=False,
        prompts=descriptors,
        prompt_set_sha256=prompt_set_sha256,
    )


def run_probe(
    config: ProbeConfig,
    prompts: Sequence[SyntheticPrompt] | str | Path,
    *,
    transport: OpenAIHTTPTransport,
    cancellation: threading.Event | None = None,
) -> ProbeRun:
    """Run a production probe using the bounded HTTP transport and real clock."""

    if type(transport) is not OpenAIHTTPTransport:
        raise ProbeConfigurationError(
            "Production probes require OpenAIHTTPTransport."
        )
    if not transport._uses_bundled_network():
        raise ProbeConfigurationError(
            "Production probes require the bundled network transport."
        )
    return _run_probe(
        config,
        prompts,
        transport=transport,
        cancellation=cancellation,
        clock_ns=time.perf_counter_ns,
    )


def _run_probe(
    config: ProbeConfig,
    prompts: Sequence[SyntheticPrompt] | str | Path,
    *,
    transport: StreamingTransport,
    clock_ns: Callable[[], int] = time.perf_counter_ns,
    cancellation: threading.Event | None = None,
) -> ProbeRun:
    """Internal deterministic seam for transport and clock testing."""

    normalized = (
        load_prompts_jsonl(prompts)
        if isinstance(prompts, (str, Path))
        else _normalize_prompts(prompts)
    )
    if not callable(clock_ns):
        raise ProbeConfigurationError("clock_ns must be callable.")
    if cancellation is not None and not isinstance(
        cancellation, threading.Event
    ):
        raise ProbeConfigurationError("cancellation must be a threading.Event.")
    resolved_execution_id = f"run_{uuid.uuid4().hex}"
    send = getattr(transport, "send", None)
    if not callable(send):
        raise ProbeConfigurationError("transport.send must be callable.")

    manifest = build_manifest(config, normalized)
    records: list[ProbeRecord] = []
    if config.warmup_count:
        records.extend(
            _execute_phase(
                manifest,
                config,
                normalized,
                phase=ProbePhase.WARMUP,
                count=config.warmup_count,
                transport=transport,
                clock_ns=clock_ns,
                cancellation=cancellation,
                execution_id=resolved_execution_id,
            )
        )
    records.extend(
        _execute_phase(
            manifest,
            config,
            normalized,
            phase=ProbePhase.MEASURED,
            count=config.request_count,
            transport=transport,
            clock_ns=clock_ns,
            cancellation=cancellation,
            execution_id=resolved_execution_id,
        )
    )
    ordered = tuple(sorted(records, key=_record_sort_key))
    _validate_probe_records(
        manifest,
        ordered,
        expected_execution_id=resolved_execution_id,
    )
    result = ProbeRun(
        execution_id=resolved_execution_id,
        manifest=manifest,
        records_sha256=_records_sha256(ordered),
        records=ordered,
    )
    validate_probe_run(result)
    return result


def validate_probe_run(probe_run: ProbeRun) -> None:
    """Verify the complete execution envelope and its ordered record artifact."""

    if type(probe_run) is not ProbeRun:
        raise ProbeEvidenceError("Probe run type is invalid.")
    if (
        type(probe_run.execution_id) is not str
        or not _EXECUTION_ID.fullmatch(probe_run.execution_id)
    ):
        raise ProbeEvidenceError("Probe run execution identity is invalid.")
    _validate_probe_records(
        probe_run.manifest,
        probe_run.records,
        expected_execution_id=probe_run.execution_id,
    )
    if (
        type(probe_run.records_sha256) is not str
        or not _SHA256.fullmatch(probe_run.records_sha256)
        or probe_run.records_sha256 != _records_sha256(probe_run.records)
    ):
        raise ProbeEvidenceError("Probe record artifact hash is invalid.")


def _validate_probe_records(
    manifest: ProbeManifest,
    records: Sequence[ProbeRecord],
    *,
    expected_execution_id: str | None = None,
) -> None:
    """Fail closed on duplicate, missing, extra, or malformed records."""

    if type(manifest) is not ProbeManifest:
        raise ProbeEvidenceError("Manifest type is invalid.")
    if expected_execution_id is not None and (
        type(expected_execution_id) is not str
        or not _EXECUTION_ID.fullmatch(expected_execution_id)
    ):
        raise ProbeEvidenceError("Expected execution identity is invalid.")
    if (
        manifest.schema_version != PROBE_SCHEMA_VERSION
        or manifest.first_token_definition
        != "first_nonempty_choices_delta_content_v1"
        or manifest.warmup_included_in_measurement is not False
    ):
        raise ProbeEvidenceError("Manifest measurement semantics are invalid.")
    try:
        ProbeConfig(
            endpoint=manifest.endpoint,
            model=manifest.model,
            request_count=manifest.request_count,
            concurrency=manifest.concurrency,
            warmup_count=manifest.warmup_count,
            timeout_seconds=manifest.timeout_seconds,
            max_tokens=manifest.max_tokens,
            max_stream_bytes=manifest.max_stream_bytes,
        )
    except ProbeConfigurationError:
        raise ProbeEvidenceError("Manifest workload bounds are invalid.") from None
    descriptor_dicts: list[dict[str, str]] = []
    for descriptor in manifest.prompts:
        if type(descriptor) is not PromptDescriptor:
            raise ProbeEvidenceError("Manifest prompt descriptor is invalid.")
        if (
            type(descriptor.prompt_id) is not str
            or not _PROMPT_ID.fullmatch(descriptor.prompt_id)
            or type(descriptor.sha256) is not str
            or not _SHA256.fullmatch(descriptor.sha256)
        ):
            raise ProbeEvidenceError("Manifest prompt descriptor is invalid.")
        descriptor_dicts.append(descriptor.to_dict())
    if not descriptor_dicts:
        raise ProbeEvidenceError("Manifest prompt set is empty.")
    expected_prompt_set_sha256 = hashlib.sha256(
        _canonical_json_bytes(descriptor_dicts)
    ).hexdigest()
    if manifest.prompt_set_sha256 != expected_prompt_set_sha256:
        raise ProbeEvidenceError("Manifest prompt-set hash is invalid.")
    identity = manifest.to_dict()
    identity.pop("manifest_sha256")
    expected_manifest_sha256 = hashlib.sha256(
        _canonical_json_bytes(identity)
    ).hexdigest()
    if manifest.manifest_sha256 != expected_manifest_sha256:
        raise ProbeEvidenceError("Manifest hash is invalid.")

    expected: dict[str, tuple[ProbePhase, int, bool]] = {}
    for phase, count, included in (
        (ProbePhase.WARMUP, manifest.warmup_count, False),
        (ProbePhase.MEASURED, manifest.request_count, True),
    ):
        for ordinal in range(1, count + 1):
            request_id = _request_id(phase, ordinal)
            expected[request_id] = (phase, ordinal, included)

    seen: set[str] = set()
    observed_execution_id: str | None = None
    for record in records:
        if type(record) is not ProbeRecord:
            raise ProbeEvidenceError("Record type is invalid.")
        if record.request_id in seen:
            raise ProbeEvidenceError("Duplicate request record.")
        if (
            type(record.execution_id) is not str
            or not _EXECUTION_ID.fullmatch(record.execution_id)
        ):
            raise ProbeEvidenceError("Record execution identity is invalid.")
        if observed_execution_id is None:
            observed_execution_id = record.execution_id
        elif record.execution_id != observed_execution_id:
            raise ProbeEvidenceError("Records mix multiple executions.")
        if (
            expected_execution_id is not None
            and record.execution_id != expected_execution_id
        ):
            raise ProbeEvidenceError("Record execution identity is unexpected.")
        seen.add(record.request_id)
        if record.request_id not in expected:
            raise ProbeEvidenceError("Unexpected request record.")
        phase, ordinal, included = expected[record.request_id]
        if (
            record.schema_version != manifest.schema_version
            or record.manifest_sha256 != manifest.manifest_sha256
            or record.phase is not phase
            or record.ordinal != ordinal
            or record.included_in_measurement is not included
        ):
            raise ProbeEvidenceError("Record identity does not match manifest.")

        descriptor = manifest.prompts[(ordinal - 1) % len(manifest.prompts)]
        if (
            record.prompt_id != descriptor.prompt_id
            or record.prompt_sha256 != descriptor.sha256
        ):
            raise ProbeEvidenceError("Record prompt does not match manifest.")
        _validate_terminal_record(record)

    if seen != set(expected):
        raise ProbeEvidenceError("One or more terminal records are missing.")
    try:
        _canonical_json_bytes(manifest.to_dict())
        for record in records:
            _canonical_json_bytes(record.to_dict())
    except (TypeError, ValueError):
        raise ProbeEvidenceError("Probe artifacts are not valid JSON.") from None


def manifest_json(manifest: ProbeManifest) -> str:
    return _canonical_json_bytes(manifest.to_dict()).decode("utf-8")


def records_jsonl(records: Sequence[ProbeRecord]) -> str:
    return "\n".join(
        _canonical_json_bytes(record.to_dict()).decode("utf-8")
        for record in sorted(records, key=_record_sort_key)
    )


def _records_sha256(records: Sequence[ProbeRecord]) -> str:
    return hashlib.sha256(records_jsonl(records).encode("utf-8")).hexdigest()


def _execute_phase(
    manifest: ProbeManifest,
    config: ProbeConfig,
    prompts: tuple[SyntheticPrompt, ...],
    *,
    phase: ProbePhase,
    count: int,
    transport: StreamingTransport,
    clock_ns: Callable[[], int],
    cancellation: threading.Event | None,
    execution_id: str,
) -> list[ProbeRecord]:
    records: list[ProbeRecord] = []
    workers = min(config.concurrency, count)
    with ThreadPoolExecutor(
        max_workers=workers,
        thread_name_prefix="exitspec-probe",
    ) as executor:
        futures = [
            executor.submit(
                _execute_attempt,
                manifest,
                config,
                prompts[(ordinal - 1) % len(prompts)],
                phase,
                ordinal,
                transport,
                clock_ns,
                cancellation,
                execution_id,
            )
            for ordinal in range(1, count + 1)
        ]
        for future in as_completed(futures):
            records.append(future.result())
    return records


def _execute_attempt(
    manifest: ProbeManifest,
    config: ProbeConfig,
    prompt: SyntheticPrompt,
    phase: ProbePhase,
    ordinal: int,
    transport: StreamingTransport,
    clock_ns: Callable[[], int],
    cancellation: threading.Event | None,
    execution_id: str,
) -> ProbeRecord:
    request_id = _request_id(phase, ordinal)
    request = ProbeRequest(
        request_id=request_id,
        endpoint=config.endpoint,
        timeout_seconds=float(config.timeout_seconds),
        json_body={
            "max_tokens": config.max_tokens,
            "messages": [{"content": prompt.content, "role": "user"}],
            "model": config.model,
            "stream": True,
            "temperature": 0,
        },
    )
    started_ns = _clock_value(clock_ns)
    deadline_ns = started_ns + int(config.timeout_seconds * 1_000_000_000)
    response: StreamResponse | None = None
    outcome = ProbeOutcome.TRANSPORT_ERROR
    http_status: int | None = None
    ttft_ns: int | None = None
    ended_ns: int | None = None

    try:
        if cancellation is not None and cancellation.is_set():
            ended_ns = _clock_value(clock_ns)
            outcome = ProbeOutcome.CANCELLED
        else:
            candidate = transport.send(request)
            if type(candidate) is not StreamResponse:
                raise ProbeProtocolError("Transport response type is invalid.")
            response = candidate
            http_status = candidate.status_code
            if (
                isinstance(http_status, bool)
                or not isinstance(http_status, int)
                or not 100 <= http_status <= 599
            ):
                raise ProbeProtocolError("HTTP status is invalid.")
            if not 200 <= http_status <= 299:
                ended_ns = _clock_value(clock_ns)
                outcome = ProbeOutcome.HTTP_ERROR
            else:
                content_type = candidate.content_type
                if (
                    type(content_type) is not str
                    or content_type.split(";", 1)[0].strip().lower()
                    != "text/event-stream"
                ):
                    raise ProbeProtocolError(
                        "Streaming response content type is invalid."
                    )
                first_token_at, stream_done_at = _consume_openai_sse(
                    candidate.chunks,
                    clock_ns=clock_ns,
                    max_stream_bytes=config.max_stream_bytes,
                    deadline_ns=deadline_ns,
                    cancellation=cancellation,
                )
                ttft_ns = first_token_at - started_ns
                ended_ns = stream_done_at
                outcome = ProbeOutcome.SUCCESS
    except InterruptedError:
        http_status = None
        ttft_ns = None
        ended_ns = _clock_value(clock_ns)
        outcome = ProbeOutcome.CANCELLED
    except TimeoutError:
        http_status = None
        ttft_ns = None
        ended_ns = _clock_value(clock_ns)
        outcome = ProbeOutcome.TIMEOUT
    except ProbeProtocolError:
        ended_ns = _clock_value(clock_ns)
        outcome = ProbeOutcome.PROTOCOL_ERROR
    except (OSError, http.client.HTTPException):
        ended_ns = _clock_value(clock_ns)
        outcome = ProbeOutcome.TRANSPORT_ERROR
    except Exception:
        ended_ns = _clock_value(clock_ns)
        outcome = ProbeOutcome.INTERNAL_ERROR

    close_failure_outcome: ProbeOutcome | None = None
    if response is not None:
        try:
            response.close()
        except (OSError, http.client.HTTPException):
            close_failure_outcome = ProbeOutcome.TRANSPORT_ERROR
        except Exception:
            close_failure_outcome = ProbeOutcome.INTERNAL_ERROR
    if close_failure_outcome is not None:
        outcome = close_failure_outcome
        ttft_ns = None
        ended_ns = _clock_value(clock_ns)

    if ended_ns is None or ended_ns < started_ns:
        raise ProbeEvidenceError("Monotonic timing evidence is invalid.")
    if ttft_ns is not None and (ttft_ns < 0 or ttft_ns > ended_ns - started_ns):
        raise ProbeEvidenceError("TTFT timing evidence is invalid.")
    return ProbeRecord(
        schema_version=manifest.schema_version,
        execution_id=execution_id,
        manifest_sha256=manifest.manifest_sha256,
        request_id=request_id,
        phase=phase,
        ordinal=ordinal,
        included_in_measurement=phase is ProbePhase.MEASURED,
        prompt_id=prompt.prompt_id,
        prompt_sha256=prompt.sha256,
        outcome=outcome,
        http_status=http_status,
        ttft_ns=ttft_ns,
        duration_ns=ended_ns - started_ns,
    )


def _consume_openai_sse(
    chunks: Iterable[bytes],
    *,
    clock_ns: Callable[[], int],
    max_stream_bytes: int,
    deadline_ns: int,
    cancellation: threading.Event | None,
) -> tuple[int, int]:
    try:
        iterator = iter(chunks)
    except TypeError:
        raise ProbeProtocolError("Stream is not iterable.") from None

    buffer = b""
    event_lines: list[str] = []
    total_bytes = 0
    first_token_at: int | None = None
    done_at: int | None = None

    for chunk in iterator:
        if cancellation is not None and cancellation.is_set():
            raise InterruptedError
        if _clock_value(clock_ns) > deadline_ns:
            raise TimeoutError
        if type(chunk) is not bytes or not chunk:
            raise ProbeProtocolError("Stream chunk is invalid.")
        total_bytes += len(chunk)
        if total_bytes > max_stream_bytes:
            raise ProbeProtocolError("Stream exceeds the byte limit.")
        buffer += chunk
        while b"\n" in buffer:
            raw_line, buffer = buffer.split(b"\n", 1)
            line = _decode_sse_line(raw_line)
            first_token_at, done_at = _process_sse_line(
                line,
                event_lines,
                first_token_at=first_token_at,
                done_at=done_at,
                clock_ns=clock_ns,
            )
            if done_at is not None:
                if first_token_at is None:
                    raise ProbeProtocolError(
                        "Stream ended before emitting content."
                    )
                return first_token_at, done_at

    if cancellation is not None and cancellation.is_set():
        raise InterruptedError
    if _clock_value(clock_ns) > deadline_ns:
        raise TimeoutError
    if buffer:
        line = _decode_sse_line(buffer)
        first_token_at, done_at = _process_sse_line(
            line,
            event_lines,
            first_token_at=first_token_at,
            done_at=done_at,
            clock_ns=clock_ns,
        )
        if done_at is not None:
            if first_token_at is None:
                raise ProbeProtocolError(
                    "Stream ended before emitting content."
                )
            return first_token_at, done_at
    if event_lines:
        first_token_at, done_at = _process_sse_event(
            event_lines,
            first_token_at=first_token_at,
            done_at=done_at,
            clock_ns=clock_ns,
        )
        event_lines.clear()
    if first_token_at is None or done_at is None:
        raise ProbeProtocolError("Stream lacks content or terminal marker.")
    return first_token_at, done_at


def _decode_sse_line(raw_line: bytes) -> str:
    if raw_line.endswith(b"\r"):
        raw_line = raw_line[:-1]
    try:
        return raw_line.decode("utf-8")
    except UnicodeDecodeError:
        raise ProbeProtocolError("SSE is not valid UTF-8.") from None


def _process_sse_line(
    line: str,
    event_lines: list[str],
    *,
    first_token_at: int | None,
    done_at: int | None,
    clock_ns: Callable[[], int],
) -> tuple[int | None, int | None]:
    if line == "":
        if event_lines:
            first_token_at, done_at = _process_sse_event(
                event_lines,
                first_token_at=first_token_at,
                done_at=done_at,
                clock_ns=clock_ns,
            )
            event_lines.clear()
        return first_token_at, done_at
    if line.startswith(":"):
        return first_token_at, done_at
    if not line.startswith("data:"):
        raise ProbeProtocolError("SSE field is unsupported.")
    value = line[5:]
    if value.startswith(" "):
        value = value[1:]
    if not value:
        raise ProbeProtocolError("SSE data field is empty.")
    event_lines.append(value)
    return first_token_at, done_at


def _process_sse_event(
    data_lines: Sequence[str],
    *,
    first_token_at: int | None,
    done_at: int | None,
    clock_ns: Callable[[], int],
) -> tuple[int | None, int | None]:
    data = "\n".join(data_lines)
    if done_at is not None:
        raise ProbeProtocolError("SSE contains data after its terminal marker.")
    if data == "[DONE]":
        if first_token_at is None:
            raise ProbeProtocolError("SSE ended before emitting content.")
        return first_token_at, _clock_value(clock_ns)
    try:
        payload = json.loads(
            data,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
        )
    except (json.JSONDecodeError, ValueError):
        raise ProbeProtocolError("SSE data is not valid JSON.") from None
    if type(payload) is not dict or "choices" not in payload:
        raise ProbeProtocolError("SSE payload shape is invalid.")
    choices = payload["choices"]
    if type(choices) is not list:
        raise ProbeProtocolError("SSE choices shape is invalid.")
    for choice in choices:
        if type(choice) is not dict:
            raise ProbeProtocolError("SSE choice is invalid.")
        delta = choice.get("delta")
        if type(delta) is not dict:
            raise ProbeProtocolError("SSE delta is invalid.")
        content = delta.get("content")
        if content is not None and type(content) is not str:
            raise ProbeProtocolError("SSE content is invalid.")
        if content and first_token_at is None:
            first_token_at = _clock_value(clock_ns)
    return first_token_at, done_at


def _validate_terminal_record(record: ProbeRecord) -> None:
    if (
        isinstance(record.duration_ns, bool)
        or not isinstance(record.duration_ns, int)
        or record.duration_ns < 0
    ):
        raise ProbeEvidenceError("Record duration is invalid.")
    if record.outcome is ProbeOutcome.SUCCESS:
        if (
            record.http_status is None
            or not 200 <= record.http_status <= 299
            or isinstance(record.ttft_ns, bool)
            or not isinstance(record.ttft_ns, int)
            or not 0 <= record.ttft_ns <= record.duration_ns
        ):
            raise ProbeEvidenceError("Successful record is malformed.")
        return
    if record.ttft_ns is not None:
        raise ProbeEvidenceError("Failed record cannot carry TTFT.")
    if record.outcome is ProbeOutcome.HTTP_ERROR:
        if (
            record.http_status is None
            or 200 <= record.http_status <= 299
            or not 100 <= record.http_status <= 599
        ):
            raise ProbeEvidenceError("HTTP error record is malformed.")
    elif record.outcome is ProbeOutcome.TIMEOUT:
        if record.http_status is not None:
            raise ProbeEvidenceError("Timeout record is malformed.")
    elif record.outcome is ProbeOutcome.PROTOCOL_ERROR:
        if record.http_status is not None and not 200 <= record.http_status <= 299:
            raise ProbeEvidenceError("Protocol error record is malformed.")
    elif record.outcome is ProbeOutcome.TRANSPORT_ERROR:
        if record.http_status is not None and not 200 <= record.http_status <= 299:
            raise ProbeEvidenceError("Transport error record is malformed.")
    elif record.outcome is ProbeOutcome.INTERNAL_ERROR:
        if record.http_status is not None and not 200 <= record.http_status <= 299:
            raise ProbeEvidenceError("Internal error record is malformed.")
    elif record.outcome is ProbeOutcome.CANCELLED:
        if record.http_status is not None:
            raise ProbeEvidenceError("Cancelled record is malformed.")
    else:
        raise ProbeEvidenceError("Record outcome is invalid.")


def _normalize_prompts(
    prompts: Sequence[SyntheticPrompt],
) -> tuple[SyntheticPrompt, ...]:
    if isinstance(prompts, (str, bytes)) or not isinstance(prompts, Sequence):
        raise ProbeConfigurationError("prompts must be a sequence.")
    detached = tuple(prompts)
    if not detached or len(detached) > _MAX_PROMPTS:
        raise ProbeConfigurationError("Prompt count is outside the allowed range.")
    if any(type(prompt) is not SyntheticPrompt for prompt in detached):
        raise ProbeConfigurationError("Every prompt must be SyntheticPrompt.")
    prompt_ids = [prompt.prompt_id for prompt in detached]
    if len(prompt_ids) != len(set(prompt_ids)):
        raise ProbeConfigurationError("Prompt identifiers must be unique.")
    return detached


def _validate_endpoint(endpoint: object):
    if type(endpoint) is not str or endpoint != endpoint.strip():
        raise ProbeConfigurationError("endpoint is invalid.")
    try:
        parts = urlsplit(endpoint)
        _ = parts.port
    except (TypeError, ValueError):
        raise ProbeConfigurationError("endpoint is invalid.") from None
    if (
        parts.scheme not in {"http", "https"}
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or parts.query
        or parts.fragment
        or not parts.path.endswith("/chat/completions")
        or (
            parts.scheme == "http"
            and parts.hostname not in {"127.0.0.1", "::1", "localhost"}
        )
    ):
        raise ProbeConfigurationError(
            "endpoint must be a credential-free chat-completions URL; "
            "plain HTTP is loopback-only."
        )
    return parts


def _bounded_int(
    value: object,
    *,
    name: str,
    minimum: int,
    maximum: int,
) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not minimum <= value <= maximum
    ):
        raise ProbeConfigurationError(f"{name} is outside the allowed range.")


def _bounded_float(
    value: object,
    *,
    name: str,
    minimum_exclusive: float,
    maximum: float,
) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or not minimum_exclusive < float(value) <= maximum
    ):
        raise ProbeConfigurationError(f"{name} is outside the allowed range.")


def _clock_value(clock_ns: Callable[[], int]) -> int:
    value = clock_ns()
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ProbeEvidenceError("Clock did not return a valid nanosecond value.")
    return value


def _request_id(phase: ProbePhase, ordinal: int) -> str:
    prefix = "warmup" if phase is ProbePhase.WARMUP else "measured"
    return f"{prefix}-{ordinal:05d}"


def _record_sort_key(record: ProbeRecord) -> tuple[int, int]:
    return (0 if record.phase is ProbePhase.WARMUP else 1, record.ordinal)


def _canonical_json_bytes(value: object) -> bytes:
    return canonical_json_bytes(value)
