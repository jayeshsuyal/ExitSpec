from pathlib import Path
import subprocess


STATIC_ROOT = Path(__file__).parents[1] / "src" / "exitspec" / "static"


def _read(name: str) -> str:
    return (STATIC_ROOT / name).read_text(encoding="utf-8")


def test_terminal_decision_ui_is_available_on_every_proof_surface():
    required_ids = (
        "closure-panel",
        "closure-form",
        "closure-decision",
        "closure-actor",
        "closure-rationale",
        "record-closure",
        "closure-status",
        "closure-receipt",
        "closure-evidence-link",
    )
    for name in ("performance.html", "proof.html"):
        html = _read(name)
        for dom_id in required_ids:
            assert f'id="{dom_id}"' in html, (name, dom_id)
        assert "Step 3 of 3 · Prove" in html
        assert "Step 5 of 5 · Decide" not in html
        assert "Evidence is not authorization." in html
        assert "never authorizes shipping or deployment" in html
        assert 'href="/closure.css"' in html
        assert 'src="/closure.js"' in html

    compatibility_html = _read("index.html")
    compatibility_javascript = _read("closure.js")
    assert 'href="/closure.css"' in compatibility_html
    assert 'src="/closure.js"' in compatibility_html
    assert 'created.id = "closure-panel"' in compatibility_javascript
    for dom_id in required_ids[1:]:
        assert f'id="{dom_id}"' in compatibility_javascript
    assert "Step 3 of 3 · Prove" in compatibility_javascript
    assert "Step 5 of 5 · Decide" not in compatibility_javascript
    assert "Evidence is not authorization." in compatibility_javascript


def test_closure_browser_echoes_only_the_exact_evidence_binding():
    javascript = _read("closure.js")

    for field in (
        "poc_id",
        "contract_id",
        "contract_version",
        "contract_hash",
        "run_id",
        "verdict",
        "evidence_pack_url",
        "evidence_pack_sha256",
    ):
        assert f'"{field}"' in javascript

    for field in (
        "operation_id",
        "runner_run_id",
        "runner_input_digest",
        "run_status",
        "reason_code",
        "terminal_at",
        "run_receipt_sha256",
    ):
        assert f'"{field}"' in javascript

    assert "/api/workspace/pocs/${encodeURIComponent(pocId)}/closure" in javascript
    assert "evidence_binding: eligibleEvidenceBinding" in javascript
    assert "terminal_run_binding: eligibleTerminalRunBinding" in javascript
    assert "eligible_terminal_run_binding" in javascript
    assert 'decisionInput.value = "POC_STOPPED"' in javascript
    assert "handoffOption.disabled = !evidenceAvailable" in javascript
    assert "This terminal run has no Evidence Pack" in javascript
    assert "idempotency_key: idempotencyKey" in javascript
    assert "sessionStorage" not in javascript
    assert "localStorage" not in javascript
    assert "shipping remains a separate human decision" in javascript.lower()


def test_closed_poc_ui_routes_seeded_fixtures_to_evidence_and_hides_reruns():
    closure = _read("closure.js")
    app = _read("app.js")
    proof = _read("proof.js")
    performance = _read("performance.js")

    assert '"poc_support_agent_demo"' in closure
    assert '"poc_inference_latency_demo"' in closure
    assert 'dashboardLink.href = "/app/evidence"' in closure
    assert 'dashboardLink.textContent = "Evidence Packs"' in closure
    assert 'dashboardLink.href = "/app?filter=Completed"' in closure
    assert 'dashboardLink.textContent = "Completed POCs"' in closure
    assert 'new CustomEvent("exitspec:closure-state"' in closure
    assert 'document.body.dataset.pocLifecycle = lifecycle' in closure

    assert 'rerunButton.hidden = pocLifecycleClosed || !hasProof || rerunMode;' in app
    assert 'if (pocLifecycleClosed) {' in app
    assert 'window.addEventListener("exitspec:closure-state"' in app
    assert 'pocLifecycleClosed ||' in proof
    assert 'window.addEventListener("exitspec:closure-state"' in proof
    assert 'state.pocLifecycleClosed ||' in performance
    assert 'window.addEventListener("exitspec:closure-state"' in performance


def test_dashboard_routes_terminal_and_completed_pocs_to_handoff():
    javascript = _read("dashboard.js")

    assert '"RECORD_DECISION_HANDOFF"' in javascript
    assert '"Complete decision"' in javascript
    assert 'poc.archive_state === "COMPLETED"' in javascript
    assert '"View completed POC"' in javascript
    assert "URLSearchParams(window.location.search)" in javascript


def test_closure_assets_follow_the_product_theme_and_parse():
    css = _read("closure.css")
    javascript_path = STATIC_ROOT / "closure.js"

    assert "var(--orange, var(--signal))" in css
    assert "var(--panel, var(--sheet))" in css
    assert ":focus-visible" in css
    assert "@media (max-width: 620px)" in css
    assert "#000" not in css.lower()
    assert "gradient" not in css.lower()

    result = subprocess.run(
        ["node", "--check", str(javascript_path)],
        capture_output=True,
        check=False,
        text=True,
    )
    assert result.returncode == 0, result.stderr
