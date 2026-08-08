from html.parser import HTMLParser
from pathlib import Path
import subprocess


STATIC_ROOT = Path(__file__).resolve().parents[1] / "src" / "exitspec" / "static"
HTML = (STATIC_ROOT / "proof.html").read_text(encoding="utf-8")
CSS = (STATIC_ROOT / "proof.css").read_text(encoding="utf-8")
JAVASCRIPT = (STATIC_ROOT / "proof.js").read_text(encoding="utf-8")


class _Inventory(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: list[str] = []
        self.controls: set[str] = set()
        self.labels: set[str] = set()

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        values = dict(attrs)
        identifier = values.get("id")
        if identifier:
            self.ids.append(identifier)
            if tag in {"input", "button", "select"}:
                self.controls.add(identifier)
        if tag == "label" and values.get("for"):
            self.labels.add(str(values["for"]))


def test_proof_page_is_one_compact_prove_decide_workbench():
    parser = _Inventory()
    parser.feed(HTML)

    assert len(parser.ids) == len(set(parser.ids))
    assert {
        "performance-main",
        "performance-current-task",
        "current-task-heading",
        "requirement-list",
        "run-proof",
        "execution-acknowledged",
        "evidence-verdict",
        "outcome-breakdown",
        "evidence-pack-link",
        "performance-error",
    }.issubset(parser.ids)
    assert "execution-acknowledged" in parser.labels
    assert "inferdrome-bundle" in parser.labels
    assert HTML.count('class="primary-action"') == 1
    assert "Run frozen proof" in HTML
    assert "Evidence Pack" in HTML
    assert "Technical details and limits" in HTML
    assert "<canvas" not in HTML
    assert "<svg" not in HTML


def test_dynamic_route_identity_drives_every_api_and_storage_boundary():
    assert (
        r"^\/app\/pocs\/(poc_[a-z0-9][a-z0-9_-]{2,63})$"
        in JAVASCRIPT
    )
    assert "const pocApi = pocId ? `/api/pocs/${pocId}` : null;" in JAVASCRIPT
    assert "const agreementApi = pocApi ? `${pocApi}/agreement` : null;" in JAVASCRIPT
    assert "const runsApi = pocApi ? `${pocApi}/runs` : null;" in JAVASCRIPT
    assert "const latestRunApi = runsApi ? `${runsApi}/latest` : null;" in JAVASCRIPT
    assert "poc_inference_latency_demo" not in JAVASCRIPT
    assert "exitspec.proof.attempt.v1.${pocId}.${run.contract_hash}" in JAVASCRIPT


def test_run_requires_explicit_exact_request_authorization():
    assert "execution_acknowledged: true" in JAVASCRIPT
    assert "idempotency_key: idempotencyKey" in JAVASCRIPT
    assert "authorized_request_count" in JAVASCRIPT
    assert "1 + value.warmup_requests + value.measured_requests" in JAVASCRIPT
    assert "I authorize this exact ${run.authorized_request_count}-request run" in (
        JAVASCRIPT
    )
    assert "endpoint:" not in JAVASCRIPT.split(
        "body: JSON.stringify({", 1
    )[1].split("}),", 1)[0]
    assert "model:" not in JAVASCRIPT.split(
        "body: JSON.stringify({", 1
    )[1].split("}),", 1)[0]


def test_inferdrome_import_reuses_one_primary_action_and_never_accepts_a_path():
    assert 'id="inferdrome-selection"' in HTML
    assert 'id="inferdrome-bundle"' in HTML
    assert 'type="file"' not in HTML
    assert "const IMPORT_KEYS" in JAVASCRIPT
    assert "trustedImport(importPayload)" in JAVASCRIPT
    assert "import_acknowledged: true" in JAVASCRIPT
    assert "run_id: selectedBundle.run_id" in JAVASCRIPT
    assert "bundle_digest: selectedBundle.bundle_digest" in JAVASCRIPT
    assert "bundle_path" not in JAVASCRIPT
    assert "INGESTION_REJECTED" in JAVASCRIPT
    assert "No acceptance verdict was issued." in JAVASCRIPT
    assert HTML.count('class="primary-action"') == 1


def test_agreement_run_and_evidence_are_cross_bound_before_rendering():
    assert "trustedDraft(draftPayload)" in JAVASCRIPT
    assert "trustedAgreement(agreementPayload)" in JAVASCRIPT
    assert "trustedRun(runPayload)" in JAVASCRIPT
    assert "crossBindingsValid()" in JAVASCRIPT
    assert "run.contract_hash === frozen.canonical_hash" in JAVASCRIPT
    assert "run.endpoint === frozen.endpoint" in JAVASCRIPT
    assert "run.model === frozen.model" in JAVASCRIPT
    assert "run.measured_requests === error.minimum_samples" in JAVASCRIPT
    assert (
        "run.measured_requests === agreement.counting_policy.exact_attempts"
        in JAVASCRIPT
    )


def test_execution_state_never_impersonates_an_evidence_verdict():
    assert "Execution is active. RUNNING is not a verdict." in JAVASCRIPT
    assert "Execution was blocked. No performance verdict exists." in JAVASCRIPT
    assert "VERDICTS.has(value.verdict)" in JAVASCRIPT
    assert "value.status === \"COMPLETED\"" in JAVASCRIPT
    assert "panel.dataset.state = run.verdict" in JAVASCRIPT
    assert "run.verdict.replaceAll(\"_\", \" \")" in JAVASCRIPT
    assert "PACK READY" not in JAVASCRIPT


def test_metrics_are_absent_until_verified_completion():
    running = JAVASCRIPT.split(
        'if (value.status === "RUNNING")', 1
    )[1].split(
        'if (["BLOCKED", "NOT_PROVEN"].includes(value.status))', 1
    )[0]
    for field in (
        "attempted_count",
        "successful_count",
        "error_count",
        "outcome_counts",
        "p95_ttft_ms",
        "error_rate_percent",
        "evidence_pack_url",
    ):
        assert f"value.{field} === null" in running
    assert 'run.status !== "COMPLETED"' in JAVASCRIPT
    assert "Not measured" in JAVASCRIPT


def test_completed_outcome_breakdown_is_exact_and_post_run_only():
    assert "OUTCOME_COUNT_KEYS" in JAVASCRIPT
    assert "countedAttempts === value.attempted_count" in JAVASCRIPT
    assert "counts.success === value.successful_count" in JAVASCRIPT
    assert "externalErrors === value.error_count" in JAVASCRIPT
    assert 'breakdown.hidden = true;' in JAVASCRIPT
    assert 'run.status === "COMPLETED"' in JAVASCRIPT
    assert 'breakdown.hidden = false;' in JAVASCRIPT
    assert "`${run.attempted_count} attempts`" in JAVASCRIPT
    assert "`${counts.success} successful`" in JAVASCRIPT


def test_evidence_link_is_same_origin_and_exactly_shaped():
    assert (
        r"^\/artifacts\/run_[a-f0-9]{32}\/decision-packet\.html$"
        in JAVASCRIPT
    )
    assert "parsed.origin === window.location.origin" in JAVASCRIPT
    assert "parsed.pathname === value" in JAVASCRIPT
    assert "link.href = packUrl;" in JAVASCRIPT
    assert "innerHTML" not in JAVASCRIPT


def test_polling_never_invents_progress_percentage_or_eta():
    assert "No progress percentage or ETA is inferred." in JAVASCRIPT
    assert "MAX_POLLS" in JAVASCRIPT
    assert "Status polling paused safely." in JAVASCRIPT
    for forbidden in ("progress_percent", "estimated_seconds", "requests_done"):
        assert forbidden not in JAVASCRIPT


def test_proof_ui_reuses_graphite_orange_theme_and_accessible_focus():
    assert "var(--orange)" in CSS
    assert "var(--green)" in CSS
    assert "var(--danger)" in CSS
    assert "focus-visible" in CSS
    assert "@media (max-width: 520px)" in CSS
    assert "gradient" not in CSS.lower()
    assert "backdrop-filter" not in CSS.lower()


def test_proof_javascript_parses():
    completed = subprocess.run(
        ["node", "--check", str(STATIC_ROOT / "proof.js")],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
