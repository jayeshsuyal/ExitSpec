from __future__ import annotations

import hashlib
import io
import os
import tarfile
from pathlib import Path

import pytest

from exitspec.inferdrome_archive import (
    InferdromeArchiveErrorCode,
    InferdromeArchiveLimits,
    InferdromeArchiveRejected,
    extract_pinned_inferdrome_archive,
)
from exitspec.inferdrome_bundle import (
    InferdromeBundleErrorCode,
    InferdromeBundleRejected,
    verify_inferdrome_bundle,
)
from exitspec.inferdrome_profile import (
    PINNED_ARCHIVE_SHA256,
    PINNED_BUNDLE_DIGEST,
)


REQUIRED_DIRECTORIES = (
    "capture",
    "capture/single",
    "capture/single/real-gpu-5osfyjjl",
    "capture/single/real-gpu-5osfyjjl/runs",
    (
        "capture/single/real-gpu-5osfyjjl/runs/"
        "run-533c9f5f783958fb6077069a6c577144"
    ),
    (
        "capture/single/real-gpu-5osfyjjl/runs/"
        "run-533c9f5f783958fb6077069a6c577144/bundle"
    ),
    "capture/single/real-gpu-5osfyjjl/corrupted-bundle-copy",
    "capture/single/real-gpu-5osfyjjl/synthetic-runs",
    (
        "capture/single/real-gpu-5osfyjjl/synthetic-runs/"
        "run-5f8d7617421b9f4d0484f5807baa7849"
    ),
    (
        "capture/single/real-gpu-5osfyjjl/synthetic-runs/"
        "run-5f8d7617421b9f4d0484f5807baa7849/bundle"
    ),
)


def test_bounded_extractor_materializes_only_regular_pinned_fixture_members(
    tmp_path,
):
    payload = _archive_bytes([])
    archive_path = tmp_path / "fixture.tar.gz"
    archive_path.write_bytes(payload)

    result = _extract_test_archive(archive_path, tmp_path / "out")

    assert result.member_count == len(REQUIRED_DIRECTORIES)
    assert result.file_count == 0
    assert result.directory_count == len(REQUIRED_DIRECTORIES)
    assert result.expanded_bytes == 0
    assert result.bundle_path.is_dir()
    assert result.corrupted_bundle_path.is_dir()
    assert result.synthetic_bundle_path.is_dir()


@pytest.mark.parametrize(
    "name",
    [
        "../escape",
        "/absolute",
        "capture/../escape",
        "capture\\windows",
        "other/root",
    ],
)
def test_archive_paths_fail_closed(name, tmp_path):
    payload = _archive_bytes([(name, tarfile.REGTYPE, b"x")])
    archive_path = tmp_path / "unsafe.tar.gz"
    archive_path.write_bytes(payload)

    with pytest.raises(InferdromeArchiveRejected) as caught:
        _extract_test_archive(archive_path, tmp_path / "out")

    assert caught.value.code is InferdromeArchiveErrorCode.UNSAFE_ARCHIVE
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize(
    "member_type",
    [tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE, tarfile.CHRTYPE],
)
def test_archive_links_and_special_nodes_fail_closed(member_type, tmp_path):
    payload = _archive_bytes(
        [("capture/unsafe", member_type, b"")],
    )
    archive_path = tmp_path / "unsafe.tar.gz"
    archive_path.write_bytes(payload)

    with pytest.raises(InferdromeArchiveRejected) as caught:
        _extract_test_archive(archive_path, tmp_path / "out")

    assert caught.value.code is InferdromeArchiveErrorCode.UNSAFE_ARCHIVE


def test_duplicate_archive_members_fail_closed(tmp_path):
    payload = _archive_bytes(
        [
            ("capture/duplicate", tarfile.REGTYPE, b"first"),
            ("capture/duplicate", tarfile.REGTYPE, b"second"),
        ]
    )
    archive_path = tmp_path / "duplicate.tar.gz"
    archive_path.write_bytes(payload)

    with pytest.raises(InferdromeArchiveRejected) as caught:
        _extract_test_archive(archive_path, tmp_path / "out")

    assert caught.value.code is InferdromeArchiveErrorCode.UNSAFE_ARCHIVE


def test_archive_resource_limits_fail_before_materialization(tmp_path):
    payload = _archive_bytes(
        [("capture/oversized", tarfile.REGTYPE, b"12345")]
    )
    archive_path = tmp_path / "oversized.tar.gz"
    archive_path.write_bytes(payload)

    with pytest.raises(InferdromeArchiveRejected) as caught:
        _extract_test_archive(
            archive_path,
            tmp_path / "out",
            limits=InferdromeArchiveLimits(
                max_compressed_bytes=len(payload),
                max_members=64,
                max_files=32,
                max_directories=32,
                max_file_bytes=4,
                max_expanded_bytes=64,
                max_depth=16,
            ),
        )

    assert caught.value.code is InferdromeArchiveErrorCode.ARCHIVE_LIMIT_EXCEEDED
    assert not (tmp_path / "out").exists()


def test_wrong_archive_checksum_rejects_before_extraction(tmp_path):
    payload = _archive_bytes([])
    archive_path = tmp_path / "fixture.tar.gz"
    archive_path.write_bytes(payload)

    with pytest.raises(InferdromeArchiveRejected) as caught:
        extract_pinned_inferdrome_archive(
            archive_path,
            tmp_path / "out",
            expected_sha256="sha256:" + "0" * 64,
            expected_size_bytes=len(payload),
            limits=_limits_for(payload),
        )

    assert caught.value.code is (
        InferdromeArchiveErrorCode.ARCHIVE_INTEGRITY_MISMATCH
    )
    assert not (tmp_path / "out").exists()


def test_exact_external_a10_archive_reproduces_pre_extension_rejection(tmp_path):
    raw_path = os.environ.get("EXITSPEC_INFERDROME_A10_ARCHIVE")
    if raw_path is None:
        pytest.skip("exact external A10 archive is not available")
    archive_path = Path(raw_path)

    extracted = extract_pinned_inferdrome_archive(
        archive_path,
        tmp_path / "a10",
    )

    assert extracted.archive_sha256 == PINNED_ARCHIVE_SHA256
    assert (
        extracted.member_count,
        extracted.file_count,
        extracted.directory_count,
        extracted.expanded_bytes,
    ) == (402, 310, 92, 3_137_959)
    with pytest.raises(InferdromeBundleRejected) as caught:
        verify_inferdrome_bundle(
            extracted.bundle_path,
            expected_bundle_digest=PINNED_BUNDLE_DIGEST,
        )
    assert caught.value.code is InferdromeBundleErrorCode.INTERNAL_INCONSISTENCY
    assert str(caught.value) == (
        "Inferdrome producer invocation field set is unsupported."
    )


def _extract_test_archive(
    archive_path: Path,
    destination: Path,
    *,
    limits: InferdromeArchiveLimits | None = None,
):
    payload = archive_path.read_bytes()
    return extract_pinned_inferdrome_archive(
        archive_path,
        destination,
        expected_sha256="sha256:" + hashlib.sha256(payload).hexdigest(),
        expected_size_bytes=len(payload),
        limits=limits or _limits_for(payload),
    )


def _limits_for(payload: bytes) -> InferdromeArchiveLimits:
    return InferdromeArchiveLimits(
        max_compressed_bytes=max(len(payload), 1),
        max_members=64,
        max_files=32,
        max_directories=32,
        max_file_bytes=1024,
        max_expanded_bytes=4096,
        max_depth=16,
    )


def _archive_bytes(
    additions: list[tuple[str, bytes, bytes]],
) -> bytes:
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as archive:
        for name in REQUIRED_DIRECTORIES:
            info = tarfile.TarInfo(name)
            info.type = tarfile.DIRTYPE
            info.mode = 0o700
            archive.addfile(info)
        for name, member_type, content in additions:
            info = tarfile.TarInfo(name)
            info.type = member_type
            info.mode = 0o600
            info.size = len(content) if member_type == tarfile.REGTYPE else 0
            if member_type in {tarfile.SYMTYPE, tarfile.LNKTYPE}:
                info.linkname = "capture"
            archive.addfile(
                info,
                io.BytesIO(content) if member_type == tarfile.REGTYPE else None,
            )
    return stream.getvalue()
