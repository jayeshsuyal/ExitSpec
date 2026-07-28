from pathlib import Path

import pytest

from exitspec.performance_web_runtime import PerformanceWebStatus
from exitspec.performance_web_service import (
    build_trusted_performance_web_runtime,
)


def test_composition_is_side_effect_free_and_server_owned(tmp_path: Path):
    output_root = tmp_path / "not-created-yet"
    database_path = tmp_path / "operations.sqlite3"

    runtime = build_trusted_performance_web_runtime(
        output_root=output_root,
        operation_database_path=database_path,
        api_key="server-secret",
        max_operations=5,
    )

    assert not output_root.exists()
    assert not database_path.exists()
    assert runtime.readiness_snapshot().status is (
        PerformanceWebStatus.NOT_STARTED
    )
    assert runtime.latest_operation_snapshot().status is (
        PerformanceWebStatus.NOT_STARTED
    )
    assert "server-secret" not in repr(runtime._config)  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    "updates",
    (
        {"output_root": Path("relative")},
        {"operation_database_path": Path("relative.sqlite3")},
        {"api_key": " secret "},
        {"artifact_url_prefix": "https://evil.test/artifacts/"},
        {"max_operations": 0},
    ),
)
def test_invalid_server_configuration_fails_before_runtime_creation(
    tmp_path: Path,
    updates,
):
    arguments = {
        "output_root": tmp_path,
        "operation_database_path": None,
        "api_key": None,
        "artifact_url_prefix": "/artifacts/",
        "max_operations": 64,
    }
    arguments.update(updates)

    with pytest.raises((TypeError, ValueError)):
        build_trusted_performance_web_runtime(**arguments)


def test_custom_worker_launcher_is_injected_without_running(tmp_path: Path):
    calls = 0

    def launcher(target):
        nonlocal calls
        calls += 1

    runtime = build_trusted_performance_web_runtime(
        output_root=tmp_path,
        worker_launcher=launcher,
    )

    assert calls == 0
    assert runtime.latest_operation_snapshot().operation_id is None
