"""Trusted adapters between the browser coordinator and performance authority.

The browser coordinator owns only operation state.  This module pins every
execution input to ExitSpec's bundled performance demo, delegates network work
to the existing authoritative runner, and independently verifies persisted
evidence before returning a local Evidence Pack URL.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from importlib.resources import files
from pathlib import Path
from typing import Any, ContextManager, Final

from .performance_artifacts import (
    VerifiedPerformanceArtifacts,
    read_and_verify_performance_artifacts,
)
from .performance_operations import PerformanceOperationStatus
from .performance_probe import OpenAIHTTPTransport, ProbeRun
from .performance_reporting import render_performance_evidence_pack
from .performance_runner import (
    PerformanceRunResult,
    _reconstruct_decision,
    _run_preflight,
    run_performance_proof,
)
from .performance_web_runtime import (
    PerformanceReadinessResult,
    PerformanceWebExecution,
    PerformanceWebRunnerResult,
    PerformanceWebStatus,
)
from .performance_workspace import (
    PERFORMANCE_CONTRACT_HASH,
    PERFORMANCE_POC_ID,
    load_performance_demo_bundle,
)


_AUTHORIZED_REQUEST_COUNT: Final = 111
_ARTIFACT_URL_PREFIX: Final = "/artifacts/"
_RUN_ID = re.compile(r"run_[a-f0-9]{32}\Z")
_SHA256 = re.compile(r"[a-f0-9]{64}\Z")

_CONTRACT_RESOURCE: Final = (
    "contracts",
    "vllm-ttft-v2.frozen.json",
)
_CONFIRMATION_RESOURCE: Final = (
    "contracts",
    "vllm-ttft-v2.confirmation.json",
)
_WORKLOAD_RESOURCE: Final = (
    "workloads",
    "concurrency-4-v1.json",
)
_PROMPT_RESOURCE: Final = (
    "prompts",
    "synthetic-latency-v1.jsonl",
)

_CONTRACT_PATH: Final = Path(
    "examples/inference-performance/contracts/vllm-ttft-v2.frozen.json"
)
_CONFIRMATION_PATH: Final = Path(
    "examples/inference-performance/contracts/"
    "vllm-ttft-v2.confirmation.json"
)
_WORKLOAD_PATH: Final = Path(
    "examples/inference-performance/workloads/concurrency-4-v1.json"
)
_PROMPT_PATH: Final = Path(
    "examples/inference-performance/prompts/synthetic-latency-v1.jsonl"
)


class PerformanceWebAdapterError(RuntimeError):
    """A trusted adapter failed closed without exposing dependency details."""


@dataclass(frozen=True, slots=True)
class PerformanceBundlePaths:
    """Ephemeral server-owned paths for the exact bundled execution inputs."""

    root: Path
    contract_path: Path
    confirmation_path: Path


@dataclass(frozen=True, slots=True, repr=False)
class PerformanceEvidenceSubject:
    """Opaque binding from one completed runner result to persisted evidence."""

    run_id: str
    artifact_registry_sha256: str
    confirmation_idempotency_key: str = field(repr=False)

    def __post_init__(self) -> None:
        if (
            type(self.run_id) is not str
            or _RUN_ID.fullmatch(self.run_id) is None
        ):
            raise ValueError("Performance evidence run identity is invalid.")
        if (
            type(self.artifact_registry_sha256) is not str
            or _SHA256.fullmatch(self.artifact_registry_sha256) is None
        ):
            raise ValueError(
                "Performance evidence registry identity is invalid."
            )
        if (
            type(self.confirmation_idempotency_key) is not str
            or not self.confirmation_idempotency_key
            or len(self.confirmation_idempotency_key.encode("utf-8")) > 256
            or self.confirmation_idempotency_key
            != self.confirmation_idempotency_key.strip()
            or any(
                ord(character) < 0x20 or ord(character) == 0x7F
                for character in self.confirmation_idempotency_key
            )
        ):
            raise ValueError(
                "Performance evidence confirmation binding is invalid."
            )

    def __repr__(self) -> str:
        return (
            "PerformanceEvidenceSubject(run_id={0!r}, "
            "artifact_registry_sha256={1!r}, "
            "confirmation_idempotency_key=<redacted>)"
        ).format(
            self.run_id,
            self.artifact_registry_sha256,
        )


class TrustedPerformanceReadinessAdapter:
    """Map the existing bounded preflight into browser-safe readiness."""

    __slots__ = ("_preflight", "_transport_factory")

    def __init__(
        self,
        *,
        preflight: Callable[..., object] = _run_preflight,
        transport_factory: Callable[..., object] = OpenAIHTTPTransport,
    ) -> None:
        if not callable(preflight):
            raise TypeError("preflight must be callable.")
        if not callable(transport_factory):
            raise TypeError("transport_factory must be callable.")
        self._preflight = preflight
        self._transport_factory = transport_factory

    def __repr__(self) -> str:
        return "TrustedPerformanceReadinessAdapter()"

    def __call__(
        self,
        execution: PerformanceWebExecution,
    ) -> PerformanceReadinessResult:
        if not _is_exact_execution(execution):
            return PerformanceReadinessResult(
                PerformanceWebStatus.NOT_PROVEN
            )
        try:
            transport = self._transport_factory(
                execution.api_key,
                credential_endpoint=execution.credential_endpoint,
            )
            raw = self._preflight(execution.bundle.context, transport)
            if type(raw) is not tuple or len(raw) != 2:
                raise TypeError
            probe_run, terminal = raw
            if type(probe_run) is not ProbeRun:
                raise TypeError
            if terminal is None:
                return PerformanceReadinessResult(
                    PerformanceWebStatus.COMPLETED
                )
            if type(terminal) is not tuple or len(terminal) != 2:
                raise TypeError
            status, reason = terminal
            if type(reason) is not str or not reason:
                raise TypeError
            if status is PerformanceOperationStatus.BLOCKED:
                return PerformanceReadinessResult(
                    PerformanceWebStatus.BLOCKED
                )
            if status is PerformanceOperationStatus.NOT_PROVEN:
                return PerformanceReadinessResult(
                    PerformanceWebStatus.NOT_PROVEN
                )
            raise TypeError
        except Exception:
            return PerformanceReadinessResult(
                PerformanceWebStatus.NOT_PROVEN
            )


class TrustedPerformanceRunnerAdapter:
    """Delegate one exact execution to ``run_performance_proof``."""

    __slots__ = ("_bundle_factory", "_runner")

    def __init__(
        self,
        *,
        runner: Callable[..., object] = run_performance_proof,
        bundle_factory: Callable[
            [PerformanceWebExecution],
            ContextManager[PerformanceBundlePaths],
        ] = lambda execution: materialized_performance_bundle(execution),
    ) -> None:
        if not callable(runner):
            raise TypeError("runner must be callable.")
        if not callable(bundle_factory):
            raise TypeError("bundle_factory must be callable.")
        self._runner = runner
        self._bundle_factory = bundle_factory

    def __repr__(self) -> str:
        return "TrustedPerformanceRunnerAdapter()"

    def __call__(
        self,
        execution: PerformanceWebExecution,
        idempotency_key: str,
    ) -> PerformanceWebRunnerResult:
        if (
            not _is_exact_execution(execution)
            or not _valid_idempotency_key(idempotency_key)
        ):
            return PerformanceWebRunnerResult(
                PerformanceWebStatus.NOT_PROVEN
            )
        try:
            with self._bundle_factory(execution) as bundle_paths:
                _require_bundle_paths(bundle_paths)
                raw = self._runner(
                    contract_path=bundle_paths.contract_path,
                    confirmation_path=bundle_paths.confirmation_path,
                    bundle_root=bundle_paths.root,
                    output_root=execution.output_root,
                    idempotency_key=idempotency_key,
                    api_key=execution.api_key,
                    credential_endpoint=execution.credential_endpoint,
                    authorized_request_count=(
                        execution.authorized_request_count
                    ),
                    operation_database_path=(
                        execution.operation_database_path
                    ),
                )
            if type(raw) is not PerformanceRunResult:
                raise TypeError
            operation = raw.operation
            status = operation.status
            if status is PerformanceOperationStatus.BLOCKED:
                return PerformanceWebRunnerResult(
                    PerformanceWebStatus.BLOCKED
                )
            if status is not PerformanceOperationStatus.COMPLETED:
                return PerformanceWebRunnerResult(
                    PerformanceWebStatus.NOT_PROVEN
                )
            artifacts = raw.artifacts
            registry_hash = operation.artifact_registry_sha256
            if (
                type(artifacts) is not VerifiedPerformanceArtifacts
                or artifacts.run_id != operation.run_id
                or type(registry_hash) is not str
                or _SHA256.fullmatch(registry_hash) is None
                or not hmac.compare_digest(
                    registry_hash,
                    hashlib.sha256(artifacts.registry_json).hexdigest(),
                )
            ):
                raise TypeError
            subject = PerformanceEvidenceSubject(
                run_id=operation.run_id,
                artifact_registry_sha256=registry_hash,
                confirmation_idempotency_key=(
                    execution.bundle.confirmation.idempotency_key
                ),
            )
            return PerformanceWebRunnerResult(
                PerformanceWebStatus.COMPLETED,
                artifact_subject=subject,
            )
        except Exception:
            return PerformanceWebRunnerResult(
                PerformanceWebStatus.NOT_PROVEN
            )


class TrustedPerformanceEvidenceVerifier:
    """Release a pack URL only after independent byte and semantic checks."""

    __slots__ = ("_output_root", "_reader", "_reconstructor", "_renderer")

    def __init__(
        self,
        *,
        output_root: Path,
        reader: Callable[[Path], object] = (
            read_and_verify_performance_artifacts
        ),
        reconstructor: Callable[..., object] = _reconstruct_decision,
        renderer: Callable[..., object] = render_performance_evidence_pack,
    ) -> None:
        if not isinstance(output_root, Path) or not output_root.is_absolute():
            raise ValueError("output_root must be an absolute pathlib.Path.")
        for dependency, label in (
            (reader, "reader"),
            (reconstructor, "reconstructor"),
            (renderer, "renderer"),
        ):
            if not callable(dependency):
                raise TypeError("{0} must be callable.".format(label))
        self._output_root = output_root
        self._reader = reader
        self._reconstructor = reconstructor
        self._renderer = renderer

    def __repr__(self) -> str:
        return "TrustedPerformanceEvidenceVerifier()"

    def __call__(self, artifact_subject: object) -> str:
        try:
            if type(artifact_subject) is not PerformanceEvidenceSubject:
                raise TypeError
            root = _require_safe_output_root(self._output_root)
            run_dir = root / artifact_subject.run_id
            _require_safe_run_dir(root, run_dir)
            verified = self._reader(run_dir)
            if (
                type(verified) is not VerifiedPerformanceArtifacts
                or verified.run_id != artifact_subject.run_id
                or verified.run_dir != run_dir.resolve(strict=True)
                or not hmac.compare_digest(
                    hashlib.sha256(verified.registry_json).hexdigest(),
                    artifact_subject.artifact_registry_sha256,
                )
            ):
                raise TypeError
            reconstructed = self._reconstructor(
                verified,
                confirmation_idempotency_key=(
                    artifact_subject.confirmation_idempotency_key
                ),
            )
            context = getattr(reconstructed, "context", None)
            decision = getattr(reconstructed, "decision", None)
            probe_run = getattr(reconstructed, "probe_run", None)
            trusted_bundle = load_performance_demo_bundle()
            if context != trusted_bundle.context:
                raise TypeError
            rendered = self._renderer(decision, context, probe_run)
            if (
                type(rendered) is not bytes
                or not hmac.compare_digest(
                    verified.decision_packet_html,
                    rendered,
                )
            ):
                raise TypeError
            return "{0}{1}/decision-packet.html".format(
                _ARTIFACT_URL_PREFIX,
                artifact_subject.run_id,
            )
        except Exception:
            raise PerformanceWebAdapterError(
                "Performance evidence failed independent verification."
            ) from None


@contextmanager
def materialized_performance_bundle(
    execution: PerformanceWebExecution,
) -> Iterator[PerformanceBundlePaths]:
    """Materialize exact package resources under their frozen logical paths."""

    if not _is_exact_execution(execution):
        raise PerformanceWebAdapterError(
            "Bundled performance execution identity is invalid."
        )
    resource_root = files("exitspec.demo_data").joinpath(
        "inference_performance"
    )
    payloads = (
        (_CONTRACT_PATH, _read_resource(resource_root, _CONTRACT_RESOURCE)),
        (
            _CONFIRMATION_PATH,
            _read_resource(resource_root, _CONFIRMATION_RESOURCE),
        ),
        (_WORKLOAD_PATH, _read_resource(resource_root, _WORKLOAD_RESOURCE)),
        (_PROMPT_PATH, _read_resource(resource_root, _PROMPT_RESOURCE)),
    )
    with tempfile.TemporaryDirectory(
        prefix="exitspec-performance-bundle-"
    ) as temporary:
        root = Path(temporary).resolve(strict=True)
        for relative_path, payload in payloads:
            target = root / relative_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
        yield PerformanceBundlePaths(
            root=root,
            contract_path=root / _CONTRACT_PATH,
            confirmation_path=root / _CONFIRMATION_PATH,
        )


def _read_resource(resource_root: Any, parts: tuple[str, str]) -> bytes:
    resource = resource_root.joinpath(*parts)
    return resource.read_bytes()


def _is_exact_execution(execution: object) -> bool:
    if type(execution) is not PerformanceWebExecution:
        return False
    try:
        trusted = load_performance_demo_bundle()
        return (
            execution.poc_id == PERFORMANCE_POC_ID
            and execution.contract_hash == PERFORMANCE_CONTRACT_HASH
            and execution.bundle == trusted
            and execution.authorized_request_count
            == _AUTHORIZED_REQUEST_COUNT
            and execution.output_root.is_absolute()
            and (
                execution.operation_database_path is None
                or execution.operation_database_path.is_absolute()
            )
        )
    except Exception:
        return False


def _valid_idempotency_key(value: object) -> bool:
    if type(value) is not str or not value:
        return False
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return (
        len(encoded) <= 256
        and value == value.strip()
        and not any(
            ord(character) < 0x20 or ord(character) == 0x7F
            for character in value
        )
    )


def _require_bundle_paths(value: object) -> None:
    if type(value) is not PerformanceBundlePaths:
        raise TypeError
    for path in (
        value.root,
        value.contract_path,
        value.confirmation_path,
    ):
        if not isinstance(path, Path) or not path.is_absolute():
            raise TypeError
    if (
        value.contract_path != value.root / _CONTRACT_PATH
        or value.confirmation_path != value.root / _CONFIRMATION_PATH
    ):
        raise ValueError


def _require_safe_output_root(value: Path) -> Path:
    if value.is_symlink():
        raise ValueError
    resolved = value.resolve(strict=True)
    if not resolved.is_dir():
        raise ValueError
    return resolved


def _require_safe_run_dir(root: Path, run_dir: Path) -> None:
    if run_dir.is_symlink():
        raise ValueError
    resolved = run_dir.resolve(strict=True)
    if not resolved.is_dir() or resolved.parent != root:
        raise ValueError


__all__ = [
    "PerformanceBundlePaths",
    "PerformanceEvidenceSubject",
    "PerformanceWebAdapterError",
    "TrustedPerformanceEvidenceVerifier",
    "TrustedPerformanceReadinessAdapter",
    "TrustedPerformanceRunnerAdapter",
    "materialized_performance_bundle",
]
