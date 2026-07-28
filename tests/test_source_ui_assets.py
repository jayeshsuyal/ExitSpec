import re
from pathlib import Path


STATIC_ROOT = Path(__file__).resolve().parents[1] / "src" / "exitspec" / "static"


def _sources() -> tuple[str, str, str]:
    return (
        (STATIC_ROOT / "index.html").read_text(encoding="utf-8"),
        (STATIC_ROOT / "styles.css").read_text(encoding="utf-8"),
        (STATIC_ROOT / "app.js").read_text(encoding="utf-8"),
    )


def test_active_palette_is_the_frozen_graphite_orange_system():
    _, css, _ = _sources()
    root = css.split(":root {", 1)[1].split("}", 1)[0].lower()
    expected = {
        "--canvas": "#0b0d0c",
        "--mast": "#101310",
        "--sheet": "#151815",
        "--raised": "#1b1f1b",
        "--rule": "#30362f",
        "--primary": "#f2f0e8",
        "--muted": "#858d84",
        "--signal": "#ff6b3d",
        "--success": "#78d6a3",
    }
    for token, value in expected.items():
        assert re.search(rf"{re.escape(token)}:\s*{value}\s*;", root)
    assert "#000" not in root
    assert "black" not in root
    assert "gradient" not in css.lower()
    assert "backdrop-filter" not in css.lower()


def test_guided_source_layout_is_compact_bounded_and_responsive():
    _, css, _ = _sources()

    assert ".source-intake-panel {" in css
    assert ".source-intake-start {" in css
    assert ".source-summary {" in css
    assert "grid-template-columns: minmax(0, 1fr) auto auto" in css
    assert ".source-summary-technical {" in css
    assert "max-height: 190px" in css
    assert "overflow: auto" in css
    assert "body.email-intake-mode .source-details" in css

    assert "height: 100dvh" in css
    assert "body {\n  margin: 0;\n  overflow: hidden;" in css
    assert ".task-view {" in css
    assert "overflow: auto" in css
    mobile = css.split("@media (max-width: 760px)", 1)[1]
    assert "body {\n    overflow: auto;" in mobile
    assert ".source-summary-technical," in mobile
    assert "position: static" in mobile
    assert "overflow: visible" in mobile


def test_source_controls_have_keyboard_focus_status_and_reduced_motion_contracts():
    html, css, _ = _sources()

    assert 'for="source-fixture-select"' in html
    assert 'id="import-source-fixture" type="button"' in html
    assert 'id="source-intake-status"' in html
    assert 'role="status"' in html
    assert 'aria-live="polite"' in html
    assert "<summary>Source details</summary>" in html
    assert "select:focus-visible" in css
    assert "@media (prefers-reduced-motion: reduce)" in css
    assert "transition: none" in css


def test_email_proposal_copy_and_summary_are_rendered_from_safe_projection_fields():
    _, _, javascript = _sources()
    renderer = javascript.split("function renderCandidates() {", 1)[1].split(
        "function renderRevisionRequest() {", 1
    )[0]
    source_renderer = javascript.split("function renderSourceIntake() {", 1)[1].split(
        "async function loadSourceCatalog()", 1
    )[0]

    assert "Email proposal · synthetic source" in renderer
    assert "Does this match the intended POC?" in javascript
    assert "sourceQuote(draft)" in renderer
    assert "intake.label" in source_renderer
    assert "intake.proposal_count" in source_renderer
    for forbidden in (
        "raw_rfc822",
        "raw_headers",
        "message_id",
        "sender_address",
        "recipient_address",
        "content_sha256",
        "subject",
    ):
        assert forbidden not in source_renderer
