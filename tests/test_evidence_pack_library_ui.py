import re
import subprocess
from html.parser import HTMLParser
from pathlib import Path


STATIC_ROOT = Path(__file__).resolve().parents[1] / "src" / "exitspec" / "static"
HTML_PATH = STATIC_ROOT / "evidence_library.html"
CSS_PATH = STATIC_ROOT / "evidence_library.css"
JS_PATH = STATIC_ROOT / "evidence_library.js"


class _Ids(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        identifier = dict(attrs).get("id")
        if identifier:
            self.ids.append(identifier)


def test_library_is_one_compact_accessible_product_surface():
    html = HTML_PATH.read_text(encoding="utf-8")
    css = CSS_PATH.read_text(encoding="utf-8")
    parser = _Ids()
    parser.feed(html)

    assert len(parser.ids) == len(set(parser.ids))
    for required_id in (
        "evidence-library-main",
        "pack-count",
        "authorization-boundary",
        "evidence-list-heading",
        "evidence-list-summary",
        "evidence-pack-list",
        "evidence-empty",
        "evidence-library-error",
    ):
        assert required_id in parser.ids
    assert 'href="#evidence-library-main"' in html
    assert 'aria-current="page">Evidence Packs</a>' in html
    assert 'aria-busy="true"' in html
    assert 'role="alert"' in html
    assert "giant" not in html.lower()
    assert "gradient" not in css
    assert "backdrop-filter" not in css
    assert "overflow: hidden" in css
    assert ".evidence-list-panel" in css
    assert "overflow: auto" in css


def test_browser_accepts_only_exact_local_pack_receipts_and_builds_safe_dom():
    javascript = JS_PATH.read_text(encoding="utf-8")

    assert 'const API_PATH = "/api/evidence-packs"' in javascript
    assert "hasExactKeys(payload, ROOT_KEYS)" in javascript
    assert "hasExactKeys(pack, PACK_KEYS)" in javascript
    assert "payload.packs.length > 2048" in javascript
    assert "safeEvidenceUrl(pack.evidence_pack_url)" in javascript
    assert "parsed.origin === window.location.origin" in javascript
    assert "new Set(identities).size !== identities.length" in javascript
    assert "No artifact link was released" in javascript
    assert "textContent" in javascript
    for unsafe_sink in (
        "innerHTML",
        "outerHTML",
        "insertAdjacentHTML",
        "document.write",
        'target = "_blank"',
    ):
        assert unsafe_sink not in javascript
    assert re.search(
        r"Date\.parse\(payload\.packs\[index - 1\]\.updated_at\)\s*>=",
        javascript,
    )


def test_library_javascript_parses():
    completed = subprocess.run(
        ["node", "--check", str(JS_PATH)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
