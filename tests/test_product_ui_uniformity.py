from html.parser import HTMLParser
from pathlib import Path
import re


STATIC_ROOT = Path(__file__).parents[1] / "src" / "exitspec" / "static"
JOURNEY = ("Define", "Confirm", "Prove")
RETIRED_PROGRESS_LABELS = ("Capture", "Review", "Agree", "Decide")


class _IdAudit(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        attributes = dict(attrs)
        if attributes.get("id"):
            self.ids.append(str(attributes["id"]))


def _read(name: str) -> str:
    return (STATIC_ROOT / name).read_text(encoding="utf-8")


def test_primary_employee_surfaces_share_one_product_identity():
    pages = (
        "dashboard.html",
        "new_poc.html",
        "source_intake.html",
        "proposal_review.html",
        "contract_definition.html",
        "agreement.html",
        "performance.html",
        "proof.html",
        "index.html",
    )

    for name in pages:
        html = _read(name)
        assert "ExitSpec" in html, name
        assert 'href="/app"' in html, name
        assert ">E</span>" in html, name

    customer_review = _read("review.html")
    assert ">E</span>" in customer_review
    assert "Review the POC agreement" in customer_review


def test_primary_workbenches_share_one_application_hierarchy():
    dashboard = _read("dashboard.html")
    support = _read("index.html")
    performance = _read("performance.html")

    for html in (dashboard, support, performance):
        assert 'class="global-header' in html or 'class="app-header global-header"' in html
        assert 'class="global-nav"' in html
        assert 'href="/app"' in html

    for html in (support, performance):
        assert 'class="object-header' in html
        assert 'class="back-link" href="/app">← All POCs</a>' in html

    assert 'class="support-object-header"' not in dashboard
    assert 'class="object-header support-object-header"' in support


def test_product_exposes_one_canonical_three_step_journey():
    for name in (
        "dashboard.html",
        "new_poc.html",
        "performance.html",
        "proof.html",
        "index.html",
    ):
        html = _read(name)
        for label in JOURNEY:
            assert label in html, (name, label)
        for label in RETIRED_PROGRESS_LABELS:
            assert f"<strong>{label}</strong>" not in html, (name, label)
        assert "Capture → Review → Agree → Prove → Decide" not in html, name

    for name in (
        "new_poc.html",
        "source_intake.html",
        "proposal_review.html",
        "contract_definition.html",
    ):
        html = _read(name)
        assert "Step 1 of 3 · Define" in html, name
        assert " of 5 · " not in html, name

    for name in ("agreement.html", "review.html"):
        html = _read(name)
        assert "Step 2 of 3 · Confirm" in html, name
        assert " of 5 · " not in html, name

    for name in ("performance.html", "proof.html"):
        html = _read(name)
        assert "Step 3 of 3 · Prove" in html, name
        assert "Step 5 of 5 · Decide" not in html, name


def test_static_pages_have_no_duplicate_dom_ids():
    for path in sorted(STATIC_ROOT.glob("*.html")):
        audit = _IdAudit()
        audit.feed(path.read_text(encoding="utf-8"))
        duplicates = {
            value for value in audit.ids if audit.ids.count(value) > 1
        }
        assert not duplicates, (path.name, sorted(duplicates))


def test_static_theme_avoids_gimmicks_and_tiny_product_copy():
    css_by_name = {
        path.name: path.read_text(encoding="utf-8")
        for path in STATIC_ROOT.glob("*.css")
    }
    combined = "\n".join(css_by_name.values()).lower()

    assert "#000" not in combined
    assert "gradient" not in combined
    assert "backdrop-filter" not in combined
    assert not re.search(r"font-size:\s*(?:8|9|10)px", combined)

    for name in ("dashboard.css", "styles.css"):
        css = css_by_name[name]
        for value in (
            "#0e141b",
            "#121a23",
            "#18222d",
            "#1d2936",
            "#2a3747",
            "#f1f4f7",
        ):
            assert value in css, (name, value)
        assert ":focus-visible" in css
        assert "@media" in css
