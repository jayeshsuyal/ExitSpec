from __future__ import annotations

import hashlib
import json
import stat
import tomllib
from pathlib import Path

import exitspec
from exitspec.routing_evidence_pack import (
    load_routing_evidence_demo_context,
    publish_routing_evidence_pack,
    verify_routing_evidence_pack,
)

ROOT = Path(__file__).resolve().parents[1]


def test_v0_4_release_truth_and_frozen_closure_artifact():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    release = (ROOT / "docs" / "RELEASE_V0_4.md").read_text(encoding="utf-8")
    closure = json.loads(
        (ROOT / "examples/product/routing-evidence-pack-v0_4-acceptance-v1.json").read_text(
            encoding="utf-8"
        )
    )
    assert project["project"]["version"] == "0.4.0"
    assert exitspec.__version__ == "0.4.0"
    assert "review candidate until the parent task tags it" in release
    assert "no tag or GitHub release has been created" in release
    assert "no router execution or provider provisioning" in release
    assert closure["release_version"] == "0.4.0"
    assert closure["status"] == "REVIEW_CANDIDATE"
    assert closure["frozen_context"]["b11_contract_sha256"] == (
        "66a6642ab761e8430e0a955e4b43de4779dda12fa08207ad25bb708c858bd260"
    )
    assert closure["frozen_context"]["b12_receipt_id"] == (
        "rqr_ab83f702d765ce428c88c7deea0a7aa4f46293c098d25117f59633c6f37b5c34"
    )
    assert closure["frozen_context"]["missing_repetition_indices"] == [2]


def test_v0_4_gate_and_ci_require_exact_b13_coverage():
    gate = (ROOT / "scripts/v0_4_release_gate.sh").read_text(encoding="utf-8")
    workflow = (ROOT / ".github/workflows/tests.yml").read_text(encoding="utf-8")
    assert (ROOT / "scripts/v0_4_release_gate.sh").stat().st_mode & stat.S_IXUSR
    assert "v0_3_release_gate.sh" in gate
    assert "tests/test_b13_routing_evidence_pack_browser.py" in gate
    assert "tests/test_routing_evidence_pack.py" in gate
    assert "expected=4" in gate
    assert "expected=16" in gate
    assert "skipped == 0" in gate
    assert "failed == 0" in gate
    assert "v0_4_release_gate.sh" in workflow


def test_v0_4_closure_matches_canonical_demo_pack(tmp_path):
    closure = json.loads(
        (ROOT / "examples/product/routing-evidence-pack-v0_4-acceptance-v1.json").read_text(
            encoding="utf-8"
        )
    )
    context = load_routing_evidence_demo_context()
    publication = publish_routing_evidence_pack(
        tmp_path,
        context.contract,
        context.confirmation,
        context.evidence,
        context.result,
        context.receipt,
    )
    verified = verify_routing_evidence_pack(tmp_path, publication.pack_id)
    pack_root = tmp_path / publication.pack_id
    assert closure["pack"]["pack_id"] == publication.pack_id
    assert closure["pack"]["artifact_hashes"] == verified.artifact_hashes
    assert closure["pack"]["manifest_sha256"] == hashlib.sha256(
        (pack_root / "artifact-hashes.json").read_bytes()
    ).hexdigest()
    assert closure["pack"]["decision_packet_sha256"] == verified.artifact_hashes[
        "decision-packet.html"
    ]
