from pathlib import Path


STATIC_ROOT = (
    Path(__file__).resolve().parents[1] / "src" / "exitspec" / "static"
)


def _asset(name: str) -> str:
    return (STATIC_ROOT / name).read_text("utf-8")


def test_performance_workspace_has_one_guided_operation_action():
    html = _asset("performance.html")

    for element_id in (
        "performance-current-task",
        "current-task-heading",
        "task-guidance",
        "check-readiness",
        "run-proof",
        "agreement-status",
        "execution-status",
        "evidence-status",
        "readiness-status",
        "run-reason",
        "operation-reference",
        "evidence-verdict",
        "evidence-pack-link",
    ):
        assert html.count(f'id="{element_id}"') == 1

    assert html.count('class="primary-action"') == 2
    assert "Check readiness" in html
    assert "Run proof" in html
    assert "Server-controlled" in html
    assert "Verdict / Evidence" in html
    assert "Open Evidence Pack" in html
    evidence_anchor = html.split('id="evidence-pack-link"', 1)[0].rsplit(
        "<a", 1
    )[1]
    assert "href=" not in evidence_anchor
    assert "<canvas" not in html
    assert "<svg" not in html


def test_run_request_sends_only_a_session_idempotency_key():
    javascript = _asset("performance.js")
    start_call = javascript.split(
        "const payload = await requestJson(RUNS_API, {", 1
    )[1].split("\n      });", 1)[0]

    assert 'method: "POST"' in start_call
    assert "idempotency_key: attempt.idempotencyKey" in start_call
    for forbidden_field in (
        "endpoint",
        "model",
        "path",
        "api_key",
        "workload",
        "request_count",
        "concurrency",
        "contract",
        "provider",
    ):
        assert forbidden_field not in start_call


def test_readiness_refresh_is_explicit_and_has_an_empty_json_body():
    javascript = _asset("performance.js")
    readiness_call = javascript.split(
        "const payload = await requestJson(READINESS_API, {", 1
    )[1].split("\n      });", 1)[0]

    assert 'method: "POST"' in readiness_call
    assert "body: JSON.stringify({})" in readiness_call
    assert "loadReadiness()" in javascript
    assert "requestJson(READINESS_API)" in javascript


def test_operation_polling_validates_server_identity_before_url_construction():
    javascript = _asset("performance.js")
    poll_function = javascript.split(
        "async function pollOperation(operationId", 1
    )[1].split("\n  async function refreshReadiness", 1)[0]

    identity_check = poll_function.index("!isOperationId(operationId)")
    url_construction = poll_function.index("`${RUNS_API}/${operationId}`")
    assert identity_check < url_construction
    assert "state.pollCount >= MAX_POLL_REQUESTS" in javascript
    assert "POLL_DELAYS_MS" in javascript
    assert "generation < state.appliedOperationGeneration" in javascript
    assert "snapshot.operationId !== expectedOperationId" in javascript


def test_session_attempt_prevents_duplicate_execution_and_recovers_polling():
    javascript = _asset("performance.js")

    assert "window.sessionStorage.getItem(ATTEMPT_STORAGE_KEY)" in javascript
    assert "window.sessionStorage.setItem(" in javascript
    assert "if (state.actionPending)" in javascript
    assert "if (isOperationId(attempt.operationId))" in javascript
    assert "await pollOperation(attempt.operationId" in javascript
    assert "idempotencyKey: newIdempotencyKey()" in javascript


def test_evidence_link_is_same_origin_terminal_and_not_a_javascript_verdict():
    javascript = _asset("performance.js")
    completed_block = javascript.split(
        'if (operation.status === "COMPLETED") {', 1
    )[1].split('\n    if (operation.status === "BLOCKED")', 1)[0]

    assert "safeEvidencePackUrl(operation.evidencePackUrl)" in completed_block
    assert "link.href = packUrl" in completed_block
    assert "PACK READY" in completed_block
    assert '"PASS"' not in completed_block
    assert "parsed.origin !== window.location.origin" in javascript
    assert "parsed.search" in javascript
    assert "parsed.hash" in javascript
    assert "EVIDENCE_PACK_PATTERN" in javascript
    assert "COMPLETED" in javascript
    assert "BLOCKED" in javascript
    assert "NOT_PROVEN" in javascript
    assert "No verdict has been inferred" in javascript


def test_browser_never_renders_raw_server_errors_or_provider_bodies():
    javascript = _asset("performance.js")

    assert "innerHTML" not in javascript
    assert "payload.error" not in javascript
    assert "error.message" not in javascript
    assert "response.text" not in javascript
    assert "safeReason(" in javascript
    assert "REASON_COPY" in javascript


def test_performance_layout_is_bounded_but_reflows_accessibly():
    css = _asset("performance.css")

    assert "grid-template-rows: auto auto minmax(0, 1fr) auto;" in css
    assert "overflow: hidden;" in css
    assert "@media (max-width: 760px)" in css
    assert "overflow: visible;" in css
    assert "@media (max-height: 680px)" in css
    assert ".primary-action:disabled" in css
    assert ".evidence-pack-link" in css
