from pathlib import Path


CONTRACT = (
    Path(__file__).resolve().parents[1]
    / "docs"
    / "LOCAL_E2E_CONTRACT.md"
)


def test_local_e2e_contract_names_every_product_boundary():
    document = CONTRACT.read_text("utf-8")

    for heading in (
        "## Product objects",
        "## Browser route contract",
        "## API contract",
        "## UI contract",
        "## Delivery train",
        "## Local exit gate",
    ):
        assert heading in document

    for route in (
        "/app/pocs/new",
        "POST /api/pocs",
        "POST /api/pocs/{poc_id}/runs",
        "GET  /api/pocs/{poc_id}/runs/{run_id}",
        "/artifacts/{run_path}",
    ):
        assert route in document


def test_local_e2e_contract_keeps_authority_and_integrations_honest():
    document = CONTRACT.read_text("utf-8")

    for required_boundary in (
        "Creation produces a local draft POC only.",
        "No source may approve a requirement",
        "The browser cannot submit a contract path",
        "`COMPLETED` does not imply `PASS`.",
        "External Gmail, Outlook, Zoom, Google Meet",
    ):
        assert required_boundary in document

    assert document.count("| 10 |") == 1
