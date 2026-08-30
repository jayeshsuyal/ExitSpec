"""A6 release closure is an explicit, no-skip GL-11..GL-15 matrix."""

import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = PROJECT_ROOT / (
    "examples/product/request-to-proof-a6-closure-evidence-v1.json"
)


def _proof_file(reference: str) -> Path:
    return PROJECT_ROOT / reference.split("::", 1)[0]


def test_a6_closure_matrix_is_scoped_complete_and_binds_no_skip_release_proof():
    artifact = json.loads(ARTIFACT.read_text(encoding="utf-8"))

    assert set(artifact) == {
        "schema_version",
        "train_slice",
        "status",
        "scope",
        "claims",
        "acceptance_matrix",
        "release_gate",
        "authority_boundary",
        "limitations",
        "frozen_identifiers_or_artifacts_edited",
    }
    assert artifact["schema_version"] == (
        "exitspec.request-to-proof-a6-closure-evidence.v1"
    )
    assert artifact["train_slice"] == "A6"
    assert artifact["scope"] == "GL-11 through GL-15 only"
    assert set(artifact["claims"]) == {"GL-11", "GL-12", "GL-13", "GL-14", "GL-15"}
    assert set(artifact["acceptance_matrix"]) == {
        "fresh_browser_a2_to_a6_pass_pack_handoff",
        "service_successor_displacement_cancel_stale",
        "admitted_not_proven_vs_catalog_ingestion_rejected",
        "http_tamper_rejection",
        "api_authority_injection",
    }
    assert artifact["release_gate"]["kind"] == "NO_SKIP_A6_GL11_GL15"
    assert artifact["frozen_identifiers_or_artifacts_edited"] is False
    assert artifact["authority_boundary"]["shipping_authorized"] is False

    for claim in artifact["claims"].values():
        assert claim["statement"]
        assert claim["proof"]
        assert all(_proof_file(reference).is_file() for reference in claim["proof"])

    for entry in artifact["acceptance_matrix"].values():
        assert entry["requirement"]
        assert entry["proof"]
        assert entry["no_skip"] is True
        assert all(_proof_file(reference).is_file() for reference in entry["proof"])

    allowed_skip = artifact["release_gate"]["allowed_skip"]
    assert allowed_skip["name"] == "optional external A10 exact pinned archive acceptance"
    assert allowed_skip["condition"] == "EXITSPEC_INFERDROME_A10_ARCHIVE is unset"
    assert "intentionally external and not vendored" in allowed_skip["meaning"]
    assert artifact["authority_boundary"]["may_authorize_deployment"] is False
    assert artifact["authority_boundary"]["may_authorize_spending"] is False
    assert artifact["authority_boundary"]["may_authorize_procurement"] is False
    assert artifact["authority_boundary"]["may_authorize_production_traffic"] is False
    assert artifact["authority_boundary"]["may_authorize_shipping"] is False
