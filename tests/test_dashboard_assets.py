import re
from html.parser import HTMLParser
from pathlib import Path


STATIC_ROOT = Path(__file__).resolve().parents[1] / "src" / "exitspec" / "static"


class _DashboardHTML(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: list[str] = []
        self.scripts: list[str] = []
        self.stylesheets: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        attributes = dict(attrs)
        if attributes.get("id"):
            self.ids.append(str(attributes["id"]))
        if tag == "script" and attributes.get("src"):
            self.scripts.append(str(attributes["src"]))
        if (
            tag == "link"
            and attributes.get("rel") == "stylesheet"
            and attributes.get("href")
        ):
            self.stylesheets.append(str(attributes["href"]))


def _sources() -> tuple[str, str, str]:
    return (
        (STATIC_ROOT / "dashboard.html").read_text(encoding="utf-8"),
        (STATIC_ROOT / "dashboard.css").read_text(encoding="utf-8"),
        (STATIC_ROOT / "dashboard.js").read_text(encoding="utf-8"),
    )


def test_dashboard_assets_are_bounded_product_specific_and_parseable():
    html, _, javascript = _sources()
    parser = _DashboardHTML()
    parser.feed(html)

    assert parser.scripts == ["/dashboard.js"]
    assert parser.stylesheets == ["/dashboard.css"]
    assert len(parser.ids) == len(set(parser.ids))
    for phrase in (
        "POCs",
        "Customer POCs",
        "Next up",
        "Active",
        "Needs attention",
        "Completed",
        "Local demo",
    ):
        assert phrase in html
    for excluded in (
        "Conversion",
        "Provider spend",
        "Token",
        "Leaderboard",
        "<canvas",
        "gradient",
        "Evidence Packs",
        "Available in the next build",
    ):
        assert excluded not in html
    assert javascript.count("WORKSPACE_API") >= 2
    assert 'const WORKSPACE_API = "/api/workspace";' in javascript
    assert "innerHTML" not in javascript
    assert "replaceChildren" in javascript
    assert "encodeURIComponent(pocId)" in javascript
    assert "No status has been inferred." in javascript
    assert "POC summaries are unavailable." in javascript


def test_dashboard_palette_matches_the_frozen_graphite_orange_contract():
    _, css, _ = _sources()
    root = css.split(":root {", 1)[1].split("}", 1)[0].lower()
    expected = {
        "--canvas": "#0b0d0c",
        "--navigation": "#101310",
        "--panel": "#151815",
        "--raised": "#1b1f1b",
        "--text": "#f2f0e8",
        "--text-secondary": "#bec4ba",
        "--muted": "#858d84",
        "--border": "#30362f",
        "--border-strong": "#4a5248",
        "--orange": "#ff6b3d",
        "--green": "#78d6a3",
    }
    for token, value in expected.items():
        assert re.search(rf"{re.escape(token)}:\s*{value}\s*;", root)
    assert "#000" not in css
    assert "linear-gradient" not in css
    assert "radial-gradient" not in css
    assert "backdrop-filter" not in css


def test_dashboard_has_one_real_create_action_and_three_bounded_filters():
    html, _, javascript = _sources()

    assert 'class="primary-action"' not in html
    assert html.count('class="new-poc-link"') == 1
    assert html.count('href="/app/pocs/new"') == 1
    assert html.count("New POC") == 1
    assert html.count('data-filter="') == 3
    assert (
        'const FILTERS = ["Active", "Needs attention", "Completed"];'
        in javascript
    )
    assert "Open POC" in javascript
    assert "No other active POCs." in javascript


def test_dashboard_desktop_is_fixed_and_narrow_layout_reenables_body_scroll():
    _, css, _ = _sources()

    assert "height: 100dvh" in css
    assert "body {\n  margin: 0;\n  overflow: hidden;" in css
    assert ".poc-list-panel {" in css
    assert "overflow: auto" in css
    mobile = css.split("@media (max-width: 760px)", 1)[1]
    assert "body {\n    overflow: auto;" in mobile
    assert ".poc-list-panel {\n    overflow: visible;" in mobile
    assert "min-width: 320px" in css


def test_dashboard_focus_and_non_color_status_contracts_are_explicit():
    html, css, javascript = _sources()

    assert "a:focus-visible" in css
    assert "button:focus-visible" in css
    assert "@media (prefers-reduced-motion: reduce)" in css
    assert 'role="alert"' in html
    assert 'aria-live="polite"' in html
    assert 'aria-busy="true"' in html
    for status in ("PASS", "FAIL", "BLOCKED", "NOT_PROVEN", "NOT_RUN"):
        assert status in javascript
