from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from exitspec.inferdrome_catalog import (
    InferdromeBundleCatalog,
    InferdromeCatalogNotFound,
)

from tests.inferdrome_helpers import (
    INFERDROME_FIXTURES,
    bind_customer_bundle,
    build_context,
    mutable_bundle_copy,
)


def _eligible_bundle(tmp_path: Path, *, container: str = "runs") -> tuple[Path, Path]:
    context_root = tmp_path / f"context-{container}"
    context_root.mkdir()
    context, _ = build_context(context_root)
    runs_root = tmp_path / container
    runs_root.mkdir()
    bundle = mutable_bundle_copy(runs_root)
    assert context.contract.canonical_hash is not None
    bind_customer_bundle(bundle, context.contract.canonical_hash)
    return runs_root, bundle


def test_unconfigured_catalog_publishes_no_candidates():
    catalog = InferdromeBundleCatalog(None)

    snapshot = catalog.refresh()

    assert snapshot.configured is False
    assert snapshot.entries == ()
    assert snapshot.rejected == ()


def test_catalog_verifies_and_resolves_only_run_id_plus_digest(tmp_path: Path):
    runs_root, bundle = _eligible_bundle(tmp_path)
    catalog = InferdromeBundleCatalog(runs_root.resolve())

    snapshot = catalog.refresh()

    assert snapshot.configured is True
    assert snapshot.rejected == ()
    assert len(snapshot.entries) == 1
    entry = snapshot.entries[0]
    assert entry.run_id == "run-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    assert entry.model == "inferdrome/mock-model"
    assert entry.adapter == "vllm_bench_serve"
    assert entry.measured_requests == 4
    assert catalog.resolve(entry.run_id, entry.bundle_digest).path == bundle

    for run_id, digest in (
        ("../../etc/passwd", entry.bundle_digest),
        (entry.run_id, "sha256:" + "0" * 64),
        (str(bundle), entry.bundle_digest),
    ):
        with pytest.raises(InferdromeCatalogNotFound):
            catalog.resolve(run_id, digest)


def test_catalog_accepts_inferdrome_workspace_bundle_shape(tmp_path: Path):
    context_root = tmp_path / "context"
    context_root.mkdir()
    context, _ = build_context(context_root)
    runs_root = tmp_path / "runs"
    workspace = runs_root / "run-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    bundle = workspace / "bundle"
    bundle.parent.mkdir(parents=True)
    shutil.copytree(INFERDROME_FIXTURES / "vllm-template", bundle)
    for directory, directory_names, filenames in os.walk(bundle):
        Path(directory).chmod(0o700)
        for directory_name in directory_names:
            (Path(directory) / directory_name).chmod(0o700)
        for filename in filenames:
            (Path(directory) / filename).chmod(0o600)
    assert context.contract.canonical_hash is not None
    bind_customer_bundle(bundle, context.contract.canonical_hash)

    snapshot = InferdromeBundleCatalog(runs_root.resolve()).refresh()

    assert len(snapshot.entries) == 1
    assert snapshot.rejected == ()


def test_catalog_rejects_symlink_and_ineligible_bundle(tmp_path: Path):
    runs_root = tmp_path / "runs"
    runs_root.mkdir()
    mutable_bundle_copy(runs_root)
    outside = tmp_path / "outside"
    outside.mkdir()
    (runs_root / "unsafe-link").symlink_to(outside, target_is_directory=True)

    snapshot = InferdromeBundleCatalog(runs_root.resolve()).refresh()

    assert snapshot.entries == ()
    assert {(item.entry, item.code) for item in snapshot.rejected} == {
        ("unsafe-link", "UNSAFE_ENTRY"),
        ("vllm-template", "EVIDENCE_INELIGIBLE"),
    }


def test_catalog_fails_closed_when_bundle_changes_after_listing(tmp_path: Path):
    runs_root, bundle = _eligible_bundle(tmp_path)
    catalog = InferdromeBundleCatalog(runs_root.resolve())
    entry = catalog.refresh().entries[0]
    (bundle / "native" / "stdout.log").write_text("mutated", encoding="utf-8")

    with pytest.raises(InferdromeCatalogNotFound):
        catalog.resolve(entry.run_id, entry.bundle_digest)


def test_duplicate_verified_run_ids_are_all_withheld(tmp_path: Path):
    runs_root, first = _eligible_bundle(tmp_path)
    second = runs_root / "duplicate"
    shutil.copytree(first, second)

    snapshot = InferdromeBundleCatalog(runs_root.resolve()).refresh()

    assert snapshot.entries == ()
    assert [item.code for item in snapshot.rejected] == [
        "DUPLICATE_RUN_ID",
        "DUPLICATE_RUN_ID",
    ]
