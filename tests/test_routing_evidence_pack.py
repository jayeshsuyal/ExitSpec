from __future__ import annotations

import hashlib
import json
from importlib.resources import files
from pathlib import Path

import pytest

from exitspec.canonical import canonical_json_bytes
from exitspec.routing_evidence_pack import (
    EXPECTED_B11_CONFIRMATION_SHA256,
    EXPECTED_B11_CONTRACT_SHA256,
    EXPECTED_B11_EVIDENCE_SHA256,
    EXPECTED_B12_RECEIPT_ID,
    EXPECTED_B12_RECEIPT_SHA256,
    ROUTING_EVIDENCE_PACK_ARTIFACTS,
    ROUTING_EVIDENCE_PACK_COMPLETION_MARKER,
    ROUTING_EVIDENCE_PACK_ENTRIES,
    ROUTING_EVIDENCE_PACK_MANIFEST,
    RoutingEvidencePackError,
    RoutingEvidencePackSummaryV1,
    _render_routing_decision_packet,
    load_routing_evidence_demo_context,
    publish_routing_evidence_pack,
    verify_routing_evidence_pack,
)


def _publish(tmp_path: Path):
    context = load_routing_evidence_demo_context()
    publication = publish_routing_evidence_pack(
        tmp_path,
        context.contract,
        context.confirmation,
        context.evidence,
        context.result,
        context.receipt,
    )
    return context, publication, tmp_path / publication.pack_id


def _manifest(pack_root: Path) -> dict[str, object]:
    return json.loads((pack_root / ROUTING_EVIDENCE_PACK_MANIFEST).read_text())


def _rewrite_manifest(pack_root: Path) -> None:
    manifest = _manifest(pack_root)
    manifest["artifacts"] = {
        name: hashlib.sha256((pack_root / name).read_bytes()).hexdigest()
        for name in ROUTING_EVIDENCE_PACK_ARTIFACTS
    }
    (pack_root / ROUTING_EVIDENCE_PACK_MANIFEST).write_bytes(
        canonical_json_bytes(manifest)
    )


def test_packaged_demo_inputs_match_authoritative_examples():
    package_root = files("exitspec.demo_data").joinpath("routing_qualification")
    example_root = Path(__file__).resolve().parents[1] / "examples" / "routing-qualification"
    relative_paths = (
        "contracts/routing-campaign-reduction-v1.synthetic.json",
        "contracts/routing-campaign-reduction-v1.synthetic.confirmation.json",
        "evidence/routing-campaign-evidence-v1.synthetic.json",
        "receipts/routing-qualification-receipt-v1.synthetic.json",
    )
    for relative in relative_paths:
        parts = relative.split("/")
        assert package_root.joinpath(*parts).read_bytes() == (
            example_root.joinpath(*parts).read_bytes()
        )


def test_frozen_context_and_fixture_are_b13_exact():
    context = load_routing_evidence_demo_context()
    assert context.contract.canonical_hash == EXPECTED_B11_CONTRACT_SHA256
    assert hashlib.sha256(
        canonical_json_bytes(context.confirmation.model_dump(mode="json"))
    ).hexdigest() == EXPECTED_B11_CONFIRMATION_SHA256
    assert hashlib.sha256(
        canonical_json_bytes(context.evidence.model_dump(mode="json"))
    ).hexdigest() == EXPECTED_B11_EVIDENCE_SHA256
    assert context.receipt.receipt_id == EXPECTED_B12_RECEIPT_ID
    assert context.result.campaign_verdict == "NOT_PROVEN"
    assert context.receipt.missing_repetition_indices == (2,)
    assert context.receipt.evidence_use == "TEST_ONLY"
    assert context.receipt.authorization.deployment_authorized is False


def test_publication_is_exact_bounded_pack_and_verifies(tmp_path):
    _context, publication, pack_root = _publish(tmp_path)
    assert {entry.name for entry in pack_root.iterdir()} == ROUTING_EVIDENCE_PACK_ENTRIES
    assert publication.pack_id.startswith("rpk_")
    assert publication.receipt_id == EXPECTED_B12_RECEIPT_ID
    assert publication.verdict == "NOT_PROVEN"
    assert publication.evidence_pack_url.endswith("/decision-packet.html")
    assert publication.artifact_hashes["receipt.json"] == EXPECTED_B12_RECEIPT_SHA256
    verified = verify_routing_evidence_pack(tmp_path, publication.pack_id)
    assert verified == publication
    html = (pack_root / "decision-packet.html").read_text()
    assert "NOT_PROVEN" in html
    assert "TEST ONLY" in html
    assert "Required repetition 2 is missing" in html
    assert "candidate-policy-v1" in html
    assert "baseline-policy-v1" in html
    assert "No deployment" in html or "no deployment" in html
    assert '"schema_version"' not in html


def test_summary_is_synthetic_only_and_zero_authority(tmp_path):
    context, _publication, pack_root = _publish(tmp_path)
    summary = RoutingEvidencePackSummaryV1.model_validate_json(
        (pack_root / "summary.json").read_bytes(), strict=True
    )
    assert summary.evidence_class == "SYNTHETIC_FIXTURE"
    assert summary.evidence_use == "TEST_ONLY"
    assert summary.test_only_label == "TEST ONLY"
    assert summary.missing_repetition_indices == (2,)
    assert summary.authorization == context.receipt.authorization


def test_second_publication_is_a_collision_and_never_overwrites(tmp_path):
    context, _publication, pack_root = _publish(tmp_path)
    before = {
        path.name: path.read_bytes()
        for path in pack_root.iterdir()
        if path.is_file()
    }
    with pytest.raises(RoutingEvidencePackError, match="collision"):
        publish_routing_evidence_pack(
            tmp_path,
            context.contract,
            context.confirmation,
            context.evidence,
            context.result,
            context.receipt,
        )
    assert before == {
        path.name: path.read_bytes()
        for path in pack_root.iterdir()
        if path.is_file()
    }


@pytest.mark.parametrize("mutation", ["missing-marker", "missing-artifact", "extra-entry", "partial"])
def test_missing_extra_and_partial_packs_release_no_identity(tmp_path, mutation):
    _context, publication, pack_root = _publish(tmp_path)
    if mutation == "missing-marker":
        (pack_root / ROUTING_EVIDENCE_PACK_COMPLETION_MARKER).unlink()
    elif mutation == "missing-artifact":
        (pack_root / "summary.json").unlink()
    elif mutation == "extra-entry":
        (pack_root / "unexpected.txt").write_text("unexpected")
    else:
        for entry in tuple(pack_root.iterdir()):
            entry.unlink()
        (pack_root / ROUTING_EVIDENCE_PACK_COMPLETION_MARKER).touch()
    with pytest.raises(RoutingEvidencePackError):
        verify_routing_evidence_pack(tmp_path, publication.pack_id)


def test_symlink_traversal_and_root_safety_fail_closed(tmp_path):
    _context, publication, pack_root = _publish(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("not a pack")
    (pack_root / "summary.json").unlink()
    (pack_root / "summary.json").symlink_to(outside)
    with pytest.raises(RoutingEvidencePackError):
        verify_routing_evidence_pack(tmp_path, publication.pack_id)
    with pytest.raises(RoutingEvidencePackError):
        verify_routing_evidence_pack(tmp_path, "../" + publication.pack_id)
    with pytest.raises(RoutingEvidencePackError):
        publish_routing_evidence_pack(
            Path("relative-runs"),
            load_routing_evidence_demo_context().contract,
            load_routing_evidence_demo_context().confirmation,
            load_routing_evidence_demo_context().evidence,
            load_routing_evidence_demo_context().result,
            load_routing_evidence_demo_context().receipt,
        )


def test_noncanonical_duplicate_and_oversized_manifest_fail_closed(tmp_path):
    _context, publication, pack_root = _publish(tmp_path)
    manifest_path = pack_root / ROUTING_EVIDENCE_PACK_MANIFEST
    manifest_path.write_bytes(b'{"schema_version":"x","schema_version":"y"}')
    with pytest.raises(RoutingEvidencePackError):
        verify_routing_evidence_pack(tmp_path, publication.pack_id)

    _context, publication, pack_root = _publish(tmp_path / "second")
    (pack_root / "summary.json").write_bytes(b"x" * (64 * 1024 + 1))
    with pytest.raises(RoutingEvidencePackError):
        verify_routing_evidence_pack(tmp_path / "second", publication.pack_id)


@pytest.mark.parametrize("artifact", ["confirmation.json", "evidence.json", "result.json", "receipt.json"])
def test_context_artifact_tamper_fails_even_when_manifest_is_rewritten(tmp_path, artifact):
    context, publication, pack_root = _publish(tmp_path)
    path = pack_root / artifact
    payload = json.loads(path.read_text())
    if artifact == "confirmation.json":
        payload["rationale"] = "substituted context"
    elif artifact == "evidence.json":
        payload["evidence_class"] = "EXTERNAL_SEALED_EVIDENCE"
    elif artifact == "result.json":
        payload["campaign_verdict"] = "PASS"
    else:
        payload["verdict"] = "PASS"
    path.write_bytes(canonical_json_bytes(payload))
    _rewrite_manifest(pack_root)
    with pytest.raises(RoutingEvidencePackError):
        verify_routing_evidence_pack(tmp_path, publication.pack_id)
    assert context.receipt.receipt_id == EXPECTED_B12_RECEIPT_ID


def test_summary_html_is_deterministic_escaped_and_has_no_sensitive_projection(tmp_path):
    context = load_routing_evidence_demo_context()
    _context, publication, pack_root = _publish(tmp_path)
    raw_summary = json.loads((pack_root / "summary.json").read_text())
    raw_summary["candidate_policy_id"] = "<script>alert('x')</script>"
    raw_summary["candidate_policy_sha256"] = "a" * 64
    hostile = RoutingEvidencePackSummaryV1.model_validate(raw_summary)
    rendered = _render_routing_decision_packet(
        publication.pack_id, hostile, context.receipt
    ).decode()
    assert "&lt;script&gt;alert(&#x27;x&#x27;)&lt;/script&gt;" in rendered
    assert "<script>" not in rendered
    assert "fixture_path" not in rendered
    assert "prompt" not in rendered.lower()
    assert "response" not in rendered.lower()
    assert "credential" not in rendered.lower()
    assert "secret" not in rendered.lower()
    assert "json.dumps" not in rendered


def test_receipt_result_context_and_authority_bypasses_are_rejected(tmp_path):
    context = load_routing_evidence_demo_context()
    with pytest.raises(RoutingEvidencePackError):
        publish_routing_evidence_pack(
            tmp_path,
            context.contract,
            context.confirmation,
            context.evidence,
            context.result.model_copy(update={"campaign_verdict": "PASS"}),
            context.receipt,
        )
    with pytest.raises(RoutingEvidencePackError):
        publish_routing_evidence_pack(
            tmp_path,
            context.contract,
            context.confirmation.model_copy(update={"contract_id": "wrong-contract"}),
            context.evidence,
            context.result,
            context.receipt,
        )
    with pytest.raises(RoutingEvidencePackError):
        publish_routing_evidence_pack(
            tmp_path,
            context.contract,
            context.confirmation,
            context.evidence,
            context.result,
            context.receipt.model_copy(
                update={
                    "authorization": context.receipt.authorization.model_copy(
                        update={"deployment_authorized": True}
                    )
                }
            ),
        )
