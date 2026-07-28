from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import exitspec.cli as cli_module
from exitspec.models import VerdictStatus
from exitspec.performance_operations import (
    PerformanceOperation,
    PerformanceOperationStatus,
)


NOW = datetime(2026, 7, 28, 15, 0, tzinfo=timezone.utc)


def _operation(
    status: PerformanceOperationStatus,
    *,
    reason: str | None = None,
) -> PerformanceOperation:
    completed = status is PerformanceOperationStatus.COMPLETED
    return PerformanceOperation(
        idempotency_key_digest="1" * 64,
        input_digest="2" * 64,
        run_id="run_" + "3" * 32,
        status=status,
        created_at=NOW,
        updated_at=NOW,
        execution_id=("run_" + "4" * 32) if completed else None,
        receipt_id=("prc_" + "5" * 64) if completed else None,
        artifact_registry_sha256="6" * 64 if completed else None,
        terminal_reason=reason,
    )


def _argv(tmp_path: Path) -> list[str]:
    return [
        "performance",
        "--contract",
        str(tmp_path / "contract.json"),
        "--confirmation",
        str(tmp_path / "confirmation.json"),
        "--bundle-root",
        str(tmp_path),
        "--idempotency-key",
        "stable-operation-key",
        "--output-dir",
        str(tmp_path / "runs"),
    ]


def test_performance_cli_reads_api_key_only_from_explicit_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
):
    captured = {}
    secret = "secret-that-must-not-print"

    def fake_run(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            operation=_operation(PerformanceOperationStatus.COMPLETED),
            verdict=VerdictStatus.PASS,
            artifacts=SimpleNamespace(run_dir=tmp_path / "runs" / "run"),
        )

    monkeypatch.setattr(cli_module, "run_performance_proof", fake_run)
    monkeypatch.setenv("SYNTHETIC_ENDPOINT_KEY", secret)

    exit_code = cli_module.main(
        _argv(tmp_path)
        + ["--api-key-env", "SYNTHETIC_ENDPOINT_KEY"]
    )

    output = capsys.readouterr().out
    assert exit_code == 0
    assert captured["api_key"] == secret
    assert secret not in output
    assert "Evidence verdict: PASS" in output
    assert "decision-packet.html" in output


def test_performance_cli_fails_before_runner_when_requested_env_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    called = False

    def fake_run(**kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(cli_module, "run_performance_proof", fake_run)
    monkeypatch.delenv("MISSING_ENDPOINT_KEY", raising=False)

    with pytest.raises(ValueError, match="not set"):
        cli_module.main(
            _argv(tmp_path)
            + ["--api-key-env", "MISSING_ENDPOINT_KEY"]
        )

    assert called is False


def test_performance_cli_surfaces_blocked_without_fabricating_a_pack(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
):
    monkeypatch.setattr(
        cli_module,
        "run_performance_proof",
        lambda **kwargs: SimpleNamespace(
            operation=_operation(
                PerformanceOperationStatus.BLOCKED,
                reason="ENDPOINT_PREFLIGHT_FAILED",
            ),
            verdict=None,
            artifacts=None,
        ),
    )

    exit_code = cli_module.main(_argv(tmp_path))
    output = capsys.readouterr().out

    assert exit_code == 3
    assert "Execution status: BLOCKED" in output
    assert "ENDPOINT_PREFLIGHT_FAILED" in output
    assert "Evidence Pack" not in output
