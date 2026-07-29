from html.parser import HTMLParser
from pathlib import Path


STATIC_ROOT = Path(__file__).resolve().parents[1] / "src" / "exitspec" / "static"


class _DashboardStructure(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: set[str] = set()
        self.class_names: set[str] = set()

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        attributes = dict(attrs)
        identifier = attributes.get("id")
        if identifier:
            self.ids.add(identifier)
        classes = attributes.get("class")
        if classes:
            self.class_names.update(classes.split())


def _sources() -> tuple[str, str, str]:
    return (
        (STATIC_ROOT / "dashboard.html").read_text(encoding="utf-8"),
        (STATIC_ROOT / "dashboard.css").read_text(encoding="utf-8"),
        (STATIC_ROOT / "dashboard.js").read_text(encoding="utf-8"),
    )


def test_dashboard_is_a_direct_continuation_work_queue():
    html, css, javascript = _sources()
    parser = _DashboardStructure()
    parser.feed(html)

    assert {
        "dashboard-main",
        "continue-region",
        "continue-card",
        "poc-region",
        "poc-list",
        "workspace-error",
    }.issubset(parser.ids)
    assert "Continue working" in html
    assert "One POC, one next action." in html
    assert "All POCs" in html
    assert "Decision preview" not in html
    assert "Read-only summary" not in html
    assert "Selected POC" not in html
    assert "workspace-layout" not in parser.class_names
    assert "renderContinue" in javascript
    assert "renderPreview" not in javascript
    assert "selectPoc" not in javascript
    assert "selectedPocId" not in javascript
    assert "grid-template-rows: auto auto minmax(0, 1fr);" in css


def test_dashboard_has_one_compact_create_action_and_safe_direct_navigation():
    html, _, javascript = _sources()

    assert html.count('href="/app/pocs/new"') == 1
    assert html.count("New POC") == 1
    assert 'class="new-poc-link"' in html
    assert "<canvas" not in html
    assert "<svg" not in html
    assert "workbenchUrl(poc)" in javascript
    assert "POC_ID_PATTERN.test(pocId)" in javascript
    assert 'poc.next_action_code === "ADD_SOURCE"' in javascript
    assert 'return `${base}/sources/new`;' in javascript
    assert 'poc.next_action_code === "REVIEW_PROPOSALS"' in javascript
    assert 'return `${base}/review`;' in javascript
    assert 'poc.next_action_code === "DEFINE_CRITERIA"' in javascript
    assert 'return `${base}/define`;' in javascript
    assert "CONFIRM_ACTIONS.has(poc.next_action_code)" in javascript
    assert 'return `${base}/agreement`;' in javascript
    assert "PROVE_ACTIONS.has(poc.next_action_code)" in javascript
    assert "poc.next_action_code === \"REVIEW_EVIDENCE\"" in javascript
    assert "return base;" in javascript
    assert "SEEDED_POC_IDS.has(pocId)" in javascript
    assert 'destination ? "a" : "div"' in javascript
    assert "innerHTML" not in javascript
    assert ".textContent =" in javascript
    assert "replaceChildren" in javascript


def test_continue_working_uses_the_authoritative_projection_without_selection():
    _, _, javascript = _sources()

    assert "renderContinue(workspace.continue_working || null)" in javascript
    assert "pocs.forEach((poc) => list.append(renderRow(poc)))" in javascript
    assert 'return `${count} ${noun} ${count === 1 ? "needs" : "need"} attention`;' in javascript
    assert "prioritizedPocs" not in javascript
    assert "aria-pressed" not in javascript.split(
        "function renderRow", 1
    )[1].split("function renderWorkspace", 1)[0]
    assert "addEventListener" not in javascript.split(
        "function renderRow", 1
    )[1].split("function renderWorkspace", 1)[0]


def test_agreement_and_evidence_are_distinct_non_invented_boundaries():
    _, css, javascript = _sources()

    assert "agreementLabel(poc)" in javascript
    assert "poc.latest_evidence_summary?.status" in javascript
    assert 'element("dl", "continue-boundaries")' in javascript
    assert 'element("dt", "", "Agreement")' in javascript
    assert 'element("dt", "", "Evidence")' in javascript
    assert "evidenceValue.dataset.state = evidenceStatus" in javascript
    assert ".continue-boundaries dd[data-state=\"PASS\"]" in css
    assert ".continue-boundaries dd[data-state=\"BLOCKED\"]" in css


def test_dashboard_exposes_one_five_step_human_journey():
    _, _, javascript = _sources()

    for label in ("Capture", "Review", "Confirm", "Prove", "Decide"):
        assert f'label: "{label}"' in javascript
    assert "`Step ${step.number} of 5 · ${step.label}`" in javascript
    assert "`Step ${step.number} · ${step.label}`" in javascript
    assert '"Run the frozen proof."' in javascript
    assert '"Run frozen proof"' in javascript
    assert '"Review the verified decision."' in javascript
    assert '"Review evidence"' in javascript


def test_dashboard_is_bounded_on_desktop_and_reflows_at_320px():
    _, css, _ = _sources()

    desktop = css.split("@media (max-width: 760px)", 1)[0]
    mobile = css.split("@media (max-width: 760px)", 1)[1]
    narrow = css.split("@media (max-width: 620px)", 1)[1]

    assert "height: 100dvh" in desktop
    assert "body {\n  margin: 0;\n  overflow: hidden;" in desktop
    assert ".poc-list-panel {\n  min-height: 0;\n  overflow: auto;" in desktop
    assert "body {\n    overflow: auto;" in mobile
    assert ".poc-list-panel {\n    overflow: visible;" in mobile
    assert "grid-template-columns: repeat(3, minmax(0, 1fr));" in narrow
    assert ".continue-card {\n    grid-template-columns: 1fr;" in narrow
    assert "min-width: 320px" in css
