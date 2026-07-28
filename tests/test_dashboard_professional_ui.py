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


def test_dashboard_is_a_master_detail_work_queue():
    html, css, javascript = _sources()
    parser = _DashboardStructure()
    parser.feed(html)

    assert {
        "dashboard-main",
        "poc-region",
        "poc-list",
        "continue-region",
        "continue-card",
        "workspace-error",
    }.issubset(parser.ids)
    assert "workspace-layout" in parser.class_names
    assert "POC work queue" in html
    assert "Decision preview" in html
    assert "Read-only summary" in html
    assert "grid-template-columns: minmax(0, 1.72fr)" in css
    assert "renderPreview" in javascript
    assert 'button.setAttribute("aria-pressed", "false")' in javascript
    assert "button.addEventListener(\"click\"" in javascript


def test_dashboard_has_one_compact_create_action_and_safe_rendering():
    html, _, javascript = _sources()

    assert html.count('href="/app/pocs/new"') == 1
    assert html.count("New POC") == 1
    assert 'class="new-poc-link"' in html
    assert "<canvas" not in html
    assert "<svg" not in html
    assert javascript.count('"Open POC"') == 1
    assert "workbenchUrl(poc)" in javascript
    assert "POC_ID_PATTERN.test(pocId)" in javascript
    assert 'poc.next_action_code === "ADD_SOURCE"' in javascript
    assert 'return `${base}/sources/new`;' in javascript
    assert 'poc.next_action_code === "REVIEW_PROPOSALS"' in javascript
    assert 'return `${base}/review`;' in javascript
    assert 'poc.next_action_code === "DEFINE_CRITERIA"' in javascript
    assert 'return `${base}/define`;' in javascript
    for agreement_action in (
        "PREPARE_AGREEMENT",
        "CREATE_CUSTOMER_REVIEW",
        "WAIT_FOR_CUSTOMER",
        "FREEZE_CONFIRMED_CONTRACT",
        "RUN_POC",
    ):
        assert f'"{agreement_action}"' in javascript
    assert 'return `${base}/agreement`;' in javascript
    assert "SEEDED_POC_IDS.has(pocId)" in javascript
    assert "innerHTML" not in javascript
    assert ".textContent =" in javascript
    assert "replaceChildren" in javascript


def test_next_up_prioritizes_and_selects_without_hiding_queue_items():
    _, _, javascript = _sources()

    assert "prioritizedPocs(pocs, nextUpPocId)" in javascript
    assert "visiblePocs.find((poc) => poc.poc_id === nextUpPocId)" in javascript
    assert "pocs.filter((poc) => poc.poc_id !== currentPocId)" not in javascript
    assert "renderPreview(selected)" in javascript


def test_agreement_and_evidence_are_separate_non_invented_boundaries():
    _, css, javascript = _sources()

    assert "agreementSummary(poc)" in javascript
    assert "evidenceSummary(poc)" in javascript
    assert '"boundary-card agreement-boundary"' in javascript
    assert '"boundary-card evidence-boundary"' in javascript
    assert "poc.latest_evidence_summary" in javascript
    assert "poc.active_contract_id" in javascript
    assert ".agreement-boundary" in css
    assert ".evidence-boundary" in css


def test_dashboard_is_bounded_on_desktop_and_reflows_at_320px():
    _, css, _ = _sources()

    desktop = css.split("@media (max-width: 760px)", 1)[0]
    mobile = css.split("@media (max-width: 760px)", 1)[1]
    narrow = css.split("@media (max-width: 520px)", 1)[1]

    assert "height: 100dvh" in desktop
    assert "body {\n  margin: 0;\n  overflow: hidden;" in desktop
    assert ".poc-list-panel {\n  min-height: 0;\n  overflow: auto;" in desktop
    assert "body {\n    overflow: auto;" in mobile
    assert ".workspace-layout {\n    grid-template-columns: 1fr;" in mobile
    assert ".poc-list-panel {\n    overflow: visible;" in mobile
    assert "grid-template-columns: repeat(3, minmax(0, 1fr));" in narrow
    assert "min-width: 320px" in css
