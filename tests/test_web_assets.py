from pathlib import Path


STATIC_ROOT = Path(__file__).resolve().parents[1] / "src" / "exitspec" / "static"


def test_static_demo_assets_exist_and_describe_the_proof_boundary():
    index = STATIC_ROOT / "index.html"
    styles = STATIC_ROOT / "styles.css"
    script = STATIC_ROOT / "app.js"

    assert index.exists()
    assert styles.exists()
    assert script.exists()

    html = index.read_text(encoding="utf-8")
    javascript = script.read_text(encoding="utf-8")

    for phrase in (
        "Define",
        "Prove",
        "Decide",
        "AI may draft.",
        "Humans approve.",
        "PASS is evidence—not authorization.",
        "Paste meeting notes",
        "Capture notes",
        "Synthetic demo only",
        "Customer-ready Proof Pack",
    ):
        assert phrase in html

    for endpoint in (
        "/api/state",
        "/api/intake",
        "/api/review",
        "/api/customer-draft",
        "/api/prove",
        "/api/reset",
    ):
        assert endpoint in javascript

    assert "never fabricates a proof result" in javascript
