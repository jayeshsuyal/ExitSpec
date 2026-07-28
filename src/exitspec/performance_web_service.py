"""Composition root for the trusted browser performance runtime."""

from __future__ import annotations

from pathlib import Path

from .performance_web_adapters import (
    TrustedPerformanceEvidenceVerifier,
    TrustedPerformanceReadinessAdapter,
    TrustedPerformanceRunnerAdapter,
)
from .performance_web_runtime import (
    PerformanceWebRuntime,
    PerformanceWebServerConfig,
    WorkerLauncher,
)


def build_trusted_performance_web_runtime(
    *,
    output_root: Path,
    operation_database_path: Path | None = None,
    api_key: str | None = None,
    artifact_url_prefix: str = "/artifacts/",
    max_operations: int = 64,
    worker_launcher: WorkerLauncher | None = None,
) -> PerformanceWebRuntime:
    """Build one server-owned coordinator without performing network work."""

    config = PerformanceWebServerConfig(
        output_root=output_root,
        operation_database_path=operation_database_path,
        api_key=api_key,
        artifact_url_prefix=artifact_url_prefix,
        max_operations=max_operations,
    )
    return PerformanceWebRuntime(
        config=config,
        readiness_probe=TrustedPerformanceReadinessAdapter(),
        runner=TrustedPerformanceRunnerAdapter(),
        evidence_pack_verifier=TrustedPerformanceEvidenceVerifier(
            output_root=config.output_root,
        ),
        worker_launcher=worker_launcher,
    )


__all__ = ["build_trusted_performance_web_runtime"]
