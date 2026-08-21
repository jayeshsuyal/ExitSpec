from __future__ import annotations

import hashlib
import json
import os
import shutil
from html.parser import HTMLParser
from pathlib import Path

import pytest

from exitspec.canonical import canonical_json_bytes
from exitspec.inferdrome_managed_demo import (
    MANAGED_DEMO_CASES,
    build_managed_demo_contract,
    generate_managed_a10_demo,
    verify_managed_demo_directory,
)
from exitspec.inferdrome_reporting_v2 import managed_receipt_id
from exitspec.models import ContractStatus


GOLDEN_ROOT = (
    Path(__file__).resolve().parents[1]
    / "examples"
    / "inference-performance"
    / "inferdrome-a10"
)


def test_checked_in_managed_demo_is_portably_verifiable_without_raw_archive():
    verified = verify_managed_demo_directory(GOLDEN_ROOT)

    assert tuple(case.acceptance_verdict for case in verified.manifest.cases) == (
        "PASS",
        "FAIL",
        "NOT_PROVEN",
    )
    assert tuple(
        case.requested_metric_definition for case in verified.manifest.cases
    ) == (
        "vllm_first_choices_event_v0_26",
        "vllm_first_choices_event_v0_26",
        "first_nonempty_choices_delta_content_v1",
    )
    assert verified.manifest.cases[2].applicability_codes == (
        "METRIC_DEFINITION_MISMATCH",
    )
    assert tuple(
        case.required_configured_max_concurrency for case in verified.manifest.cases
    ) == (4, 4, 4)
    assert tuple(case.recalculated_ttft_p95_ns for case in verified.manifest.cases) == (
        14_797_213,
        14_797_213,
        14_797_213,
    )
    assert tuple(
        (item.reason_code, item.acceptance_verdict, item.receipt_emitted)
        for item in verified.manifest.rejections
    ) == (
        ("INTEGRITY_MISMATCH", None, False),
        ("EVIDENCE_INELIGIBLE", None, False),
    )
    assert verified.manifest.raw_archive_included is False
    assert verified.manifest_sha256 == (
        "sha256:761c955278049f2e03dbd97a5c9ad913286a30b87952a250eef56c0521be6102"
    )
    assert {case.case_id: case.contract_hash for case in verified.manifest.cases} == {
        "pass": "d97779d549a5c227ec65ca66294c0f2ddfdd09c2fdc15505765fc58cb6d75d9d",
        "fail": "1db0c6a347949f00a2bc1400a0dd35e3516928d3794ebe5f165d43f53653a6a1",
        "not-proven": (
            "c3744d8920f4f49574792d40416df037e59392106835493142b7d8264aa17711"
        ),
    }
    assert {case.case_id: case.receipt_sha256 for case in verified.manifest.cases} == {
        "pass": (
            "sha256:c5cfd10a96c77627d8143f2b8b224f1c0aa80cd2ca9273531a04068e5471247d"
        ),
        "fail": (
            "sha256:8c170fe4f581ba6980be75cb3112769c6ad00e7bfdcf37e03b5ceb5d8b3eb458"
        ),
        "not-proven": (
            "sha256:ef074b656b261bdb23de777bc58996d47516600ab8be66472b0aaec8ed112524"
        ),
    }
    assert not any(
        path.suffixes[-2:] == [".tar", ".gz"] for path in GOLDEN_ROOT.rglob("*")
    )


def test_demo_contracts_are_frozen_and_confirmed_before_any_archive_is_needed():
    built = tuple(build_managed_demo_contract(case) for case in MANAGED_DEMO_CASES)

    assert all(contract.status is ContractStatus.FROZEN for contract, _ in built)
    assert all(contract.canonical_hash for contract, _ in built)
    assert all(
        contract.confirmation_id == confirmation.confirmation_id
        for contract, confirmation in built
    )


def test_exact_archive_regenerates_every_checked_in_demo_byte(tmp_path):
    archive = _exact_archive_or_skip()
    generated_root = tmp_path / "generated"

    generated = generate_managed_a10_demo(archive, generated_root)
    checked_in = verify_managed_demo_directory(GOLDEN_ROOT)

    assert generated.manifest == checked_in.manifest
    assert generated.manifest_sha256 == checked_in.manifest_sha256
    assert _directory_bytes(generated_root) == _directory_bytes(GOLDEN_ROOT)


def test_demo_generation_refuses_to_overwrite_an_existing_directory(tmp_path):
    output = tmp_path / "existing"
    output.mkdir()

    with pytest.raises(ValueError, match="must not already exist"):
        generate_managed_a10_demo(tmp_path / "missing.tar.gz", output)


def test_portable_verifier_rejects_artifact_mutation(tmp_path):
    copied = tmp_path / "copied"
    shutil.copytree(GOLDEN_ROOT, copied)
    evidence_pack = copied / "evidence-packs" / "pass.html"
    evidence_pack.write_bytes(evidence_pack.read_bytes() + b" ")

    with pytest.raises(ValueError, match="artifact digest"):
        verify_managed_demo_directory(copied)


def test_portable_verifier_rejects_rehashed_receipt_that_contradicts_contract(
    tmp_path,
):
    copied = tmp_path / "copied"
    shutil.copytree(GOLDEN_ROOT, copied)
    receipt_path = copied / "receipts" / "pass.receipt.json"
    receipt = json.loads(receipt_path.read_bytes())
    receipt["target"]["requested_model"] = "attacker/rewritten-model"
    receipt["target"]["observed_model"] = "attacker/rewritten-model"
    receipt_without_id = dict(receipt)
    receipt_without_id.pop("receipt_id")
    receipt["receipt_id"] = managed_receipt_id(receipt_without_id)
    receipt_bytes = canonical_json_bytes(receipt)
    receipt_path.write_bytes(receipt_bytes)

    manifest_path = copied / "manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    pass_case = manifest["cases"][0]
    receipt_sha256 = f"sha256:{hashlib.sha256(receipt_bytes).hexdigest()}"
    pass_case["receipt_id"] = receipt["receipt_id"]
    pass_case["receipt_sha256"] = receipt_sha256
    pass_case["receipt"]["sha256"] = receipt_sha256
    manifest_path.write_bytes(canonical_json_bytes(manifest))

    with pytest.raises(ValueError, match="criterion fields disagree"):
        verify_managed_demo_directory(copied)


def test_portable_verifier_rejects_symlinked_directory_before_artifact_reads(
    tmp_path,
):
    copied = tmp_path / "copied"
    outside = tmp_path / "outside-contracts"
    shutil.copytree(GOLDEN_ROOT, copied)
    shutil.copytree(copied / "contracts", outside)
    shutil.rmtree(copied / "contracts")
    (copied / "contracts").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="symbolic link"):
        verify_managed_demo_directory(copied)


def test_portable_verifier_rejects_oversized_artifact_before_reading_it(tmp_path):
    copied = tmp_path / "copied"
    shutil.copytree(GOLDEN_ROOT, copied)
    (copied / "evidence-packs" / "pass.html").write_bytes(b"x" * 1_048_577)

    with pytest.raises(ValueError, match="exceeds its byte limit"):
        verify_managed_demo_directory(copied)


def test_customer_evidence_packs_are_bounded_static_documents():
    for verdict in ("pass", "fail", "not-proven"):
        content = (GOLDEN_ROOT / "evidence-packs" / f"{verdict}.html").read_text()
        parser = _EvidencePackParser()
        parser.feed(content)

        assert parser.section_count == 4
        assert "viewport" in parser.meta_names
        assert not parser.forbidden_tags
        assert not parser.external_references
        assert "Frozen customer rule" in parser.text
        assert "Independent recalculation" in parser.text
        assert "Trust and chronology boundary" in parser.text
        assert "Hardware attestation" in parser.text


def _exact_archive_or_skip() -> Path:
    value = os.environ.get("EXITSPEC_INFERDROME_A10_ARCHIVE")
    if value is None:
        pytest.skip("exact external A10 archive is not available")
    return Path(value)


def _directory_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


class _EvidencePackParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.section_count = 0
        self.meta_names: set[str] = set()
        self.forbidden_tags: set[str] = set()
        self.external_references: list[str] = []
        self._text: list[str] = []

    @property
    def text(self) -> str:
        return " ".join(self._text)

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        if tag == "section":
            self.section_count += 1
        if tag in {"script", "iframe", "form", "input", "button", "link"}:
            self.forbidden_tags.add(tag)
        attributes = dict(attrs)
        if tag == "meta" and attributes.get("name"):
            self.meta_names.add(str(attributes["name"]))
        for name in ("href", "src", "action"):
            if attributes.get(name):
                self.external_references.append(str(attributes[name]))

    def handle_data(self, data: str) -> None:
        stripped = data.strip()
        if stripped:
            self._text.append(stripped)
