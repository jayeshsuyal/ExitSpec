from __future__ import annotations

import stat
from pathlib import Path

import pytest

import exitspec.routing_evidence_pack as pack_module
from exitspec.routing_evidence_pack import (
    RoutingEvidencePackError,
    load_routing_evidence_demo_context,
    publish_routing_evidence_pack,
    read_routing_evidence_pack_artifact,
)


def _published(tmp_path: Path):
    context = load_routing_evidence_demo_context()
    publication = publish_routing_evidence_pack(
        tmp_path,
        context.contract,
        context.confirmation,
        context.evidence,
        context.result,
        context.receipt,
    )
    return publication, tmp_path / publication.pack_id


def test_direct_helper_returns_exact_bytes_from_valid_pack(tmp_path):
    publication, pack_root = _published(tmp_path)

    assert read_routing_evidence_pack_artifact(
        tmp_path, publication.pack_id, "decision-packet.html"
    ) == (pack_root / "decision-packet.html").read_bytes()


def test_direct_helper_rejects_real_directory_replacement_with_hardlink(
    tmp_path, monkeypatch
):
    publication, pack_root = _published(tmp_path)
    moved = tmp_path / "moved-pack"
    original_open = pack_module.os.open
    swapped = False

    def replace_after_root_open(path, flags, *args, **kwargs):
        nonlocal swapped
        descriptor = original_open(path, flags, *args, **kwargs)
        if not swapped and Path(path) == tmp_path:
            pack_root.rename(moved)
            replacement = tmp_path / publication.pack_id
            replacement.mkdir()
            (replacement / "decision-packet.html").hardlink_to(
                moved / "decision-packet.html"
            )
            swapped = True
        return descriptor

    monkeypatch.setattr(pack_module.os, "open", replace_after_root_open)
    with pytest.raises(RoutingEvidencePackError):
        read_routing_evidence_pack_artifact(
            tmp_path, publication.pack_id, "decision-packet.html"
        )
    assert swapped is True


def test_direct_helper_rejects_mutation_after_initial_fstat(tmp_path, monkeypatch):
    publication, pack_root = _published(tmp_path)
    manifest = pack_root / "artifact-hashes.json"
    original_fstat = pack_module.os.fstat
    mutated = False

    def mutate_after_fstat(descriptor):
        nonlocal mutated
        metadata = original_fstat(descriptor)
        if not mutated and stat.S_ISREG(metadata.st_mode):
            content = manifest.read_bytes()
            manifest.write_bytes(content[:-1] + b" ")
            mutated = True
        return metadata

    monkeypatch.setattr(pack_module.os, "fstat", mutate_after_fstat)
    with pytest.raises(RoutingEvidencePackError, match="changed during read"):
        read_routing_evidence_pack_artifact(
            tmp_path, publication.pack_id, "decision-packet.html"
        )
    assert mutated is True


def test_direct_helper_rejects_pack_symlink(tmp_path):
    publication, pack_root = _published(tmp_path)
    moved = tmp_path / "moved-pack"
    pack_root.rename(moved)
    pack_root.symlink_to(moved, target_is_directory=True)

    with pytest.raises(RoutingEvidencePackError):
        read_routing_evidence_pack_artifact(
            tmp_path, publication.pack_id, "decision-packet.html"
        )
