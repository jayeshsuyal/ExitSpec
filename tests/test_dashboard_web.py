import json
import threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest

from exitspec.web import DemoSession, ExitSpecDemoServer


def _running_server(tmp_path: Path):
    session = DemoSession.synthetic_support_agent(output_root=tmp_path / "runs")
    server = ExitSpecDemoServer(("127.0.0.1", 0), session)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    return (
        server,
        worker,
        "http://127.0.0.1:{0}".format(server.server_port),
    )


def _get_json(url: str):
    with urlopen(url, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def _get_bytes(url: str) -> bytes:
    with urlopen(url, timeout=5) as response:
        return response.read()


def test_dashboard_and_seeded_workbench_have_distinct_stable_routes(tmp_path):
    server, worker, base_url = _running_server(tmp_path)
    try:
        dashboard = _get_bytes(base_url + "/app")
        root = _get_bytes(base_url + "/")
        workbench = _get_bytes(
            base_url + "/app/pocs/poc_support_agent_demo"
        )

        assert root == dashboard
        assert b'id="dashboard-main"' in dashboard
        assert b'id="poc-list"' in dashboard
        assert b'id="current-task"' not in dashboard
        assert b'id="current-task"' in workbench
        assert b'id="dashboard-main"' not in workbench
        assert _get_bytes(base_url + "/dashboard.css")
        assert _get_bytes(base_url + "/dashboard.js")
    finally:
        server.shutdown()
        worker.join(timeout=5)
        server.server_close()


@pytest.mark.parametrize(
    "url",
    (
        "/app?mode=recording",
        "/app?intake=email",
        "/app/?mode=recording",
        "/app?source=demo&intake=email",
    ),
)
def test_compatibility_queries_continue_to_open_the_workbench(tmp_path, url):
    server, worker, base_url = _running_server(tmp_path)
    try:
        html = _get_bytes(base_url + url)

        assert b'id="current-task"' in html
        assert b'id="dashboard-main"' not in html
    finally:
        server.shutdown()
        worker.join(timeout=5)
        server.server_close()


def test_unknown_poc_route_fails_closed(tmp_path):
    server, worker, base_url = _running_server(tmp_path)
    try:
        with pytest.raises(HTTPError) as missing:
            _get_bytes(base_url + "/app/pocs/poc_not_in_registry")

        assert missing.value.code == 404
        assert json.loads(missing.value.read()) == {"error": "Page not found."}
    finally:
        server.shutdown()
        worker.join(timeout=5)
        server.server_close()


@pytest.mark.parametrize(
    ("filter_value", "expected_count"),
    (
        ("Active", 1),
        ("Needs%20attention", 1),
        ("Completed", 0),
    ),
)
def test_workspace_api_exposes_only_the_selected_bounded_filter(
    tmp_path,
    filter_value,
    expected_count,
):
    server, worker, base_url = _running_server(tmp_path)
    try:
        workspace = _get_json(
            base_url + "/api/workspace?filter=" + filter_value
        )

        assert workspace["selected_filter"].replace(" ", "%20") == filter_value
        assert len(workspace["pocs"]) == expected_count
        assert workspace["continue_working"]["poc_id"] == (
            "poc_support_agent_demo"
        )
    finally:
        server.shutdown()
        worker.join(timeout=5)
        server.server_close()


def test_workspace_filtering_is_read_only_and_defaults_to_active(tmp_path):
    server, worker, base_url = _running_server(tmp_path)
    try:
        before = _get_bytes(base_url + "/api/state")
        default = _get_json(base_url + "/api/workspace")
        _get_json(base_url + "/api/workspace?filter=Needs%20attention")
        _get_json(base_url + "/api/workspace?filter=Completed")
        after = _get_bytes(base_url + "/api/state")

        assert default["selected_filter"] == "Active"
        assert before == after
    finally:
        server.shutdown()
        worker.join(timeout=5)
        server.server_close()


@pytest.mark.parametrize(
    "query",
    (
        "filter=",
        "filter=Unknown",
        "filter=Active&filter=Completed",
        "view=Active",
        "filter=Active&extra=true",
        "broken",
    ),
)
def test_workspace_api_rejects_unbounded_or_ambiguous_filters(tmp_path, query):
    server, worker, base_url = _running_server(tmp_path)
    try:
        with pytest.raises(HTTPError) as invalid:
            _get_json(base_url + "/api/workspace?" + query)

        assert invalid.value.code == 400
        assert json.loads(invalid.value.read()) == {
            "error": (
                "Workspace filter must be exactly Active, Needs attention, "
                "or Completed."
            )
        }
    finally:
        server.shutdown()
        worker.join(timeout=5)
        server.server_close()
