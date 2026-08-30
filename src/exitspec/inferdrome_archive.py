"""Bounded extraction for checksum-pinned external Inferdrome archives.

This module is intentionally not connected to the browser.  It exists for the
offline conformance harness, where a retained archive is verified before its
bundle directory is passed to the existing no-follow bundle reader.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import shutil
import stat
import tarfile
import unicodedata
from dataclasses import dataclass
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Final, NoReturn

from .inferdrome_profile import (
    CAPTURE_PRODUCER_COMMIT,
    PINNED_ARCHIVE_SHA256,
    PINNED_ARCHIVE_SIZE_BYTES,
    PINNED_BUNDLE_MEMBER_PATH,
    PINNED_BUNDLE_DIGEST,
    PINNED_CAPTURE_MANIFEST_SHA256,
    PINNED_CORRUPTED_MEMBER_PATH,
    PINNED_RUN_ID,
    PINNED_SYNTHETIC_MEMBER_PATH,
)


_TAGGED_SHA256: Final = "sha256:"
_READ_CHUNK_BYTES: Final = 1024 * 1024
_MAX_MEMBER_NAME_CHARACTERS: Final = 4_096
_SELECTED_MEMBER_ROOTS: Final = (
    PINNED_BUNDLE_MEMBER_PATH,
    PINNED_CORRUPTED_MEMBER_PATH,
    PINNED_SYNTHETIC_MEMBER_PATH,
)
_SELECTED_OUTER_MEMBERS: Final = frozenset(
    {
        "capture/capture-manifest.json",
        "capture/support/host-preparation.json",
        "capture/single/real-gpu-5osfyjjl/demo-receipt.json",
    }
)


class InferdromeArchiveErrorCode(str, Enum):
    """Stable failure classes for an untrusted archive transport."""

    UNSAFE_ARCHIVE = "UNSAFE_ARCHIVE"
    ARCHIVE_LIMIT_EXCEEDED = "ARCHIVE_LIMIT_EXCEEDED"
    ARCHIVE_INTEGRITY_MISMATCH = "ARCHIVE_INTEGRITY_MISMATCH"


class InferdromeArchiveRejected(ValueError):
    """The compressed transport cannot be materialized safely."""

    def __init__(self, code: InferdromeArchiveErrorCode, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class InferdromeArchiveLimits:
    """Resource limits applied before any archive member is written."""

    max_compressed_bytes: int = 1_048_576
    max_members: int = 512
    max_files: int = 400
    max_directories: int = 128
    max_file_bytes: int = 1_048_576
    max_expanded_bytes: int = 8_388_608
    max_depth: int = 16

    def __post_init__(self) -> None:
        values = (
            self.max_compressed_bytes,
            self.max_members,
            self.max_files,
            self.max_directories,
            self.max_file_bytes,
            self.max_expanded_bytes,
            self.max_depth,
        )
        if any(type(value) is not int or value <= 0 for value in values):
            raise ValueError("Inferdrome archive limits must be positive integers.")


@dataclass(frozen=True, slots=True)
class ExtractedInferdromeArchive:
    """Paths materialized from one exact, verified archive transport."""

    root: Path
    archive_sha256: str
    bundle_path: Path
    corrupted_bundle_path: Path
    synthetic_bundle_path: Path
    member_count: int
    file_count: int
    directory_count: int
    expanded_bytes: int


@dataclass(frozen=True, slots=True)
class ExtractedInferdromeArchiveMember:
    """One selectively materialized member from a verified archive."""

    root: Path
    archive_sha256: str
    member_path: str
    member: Path
    member_count: int
    file_count: int
    directory_count: int
    expanded_bytes: int


@dataclass(frozen=True, slots=True)
class _ArchiveMember:
    name: str
    size: int
    is_file: bool
    materialize: bool


def extract_pinned_inferdrome_archive(
    archive_path: Path,
    destination: Path,
    *,
    expected_sha256: str = PINNED_ARCHIVE_SHA256,
    expected_size_bytes: int = PINNED_ARCHIVE_SIZE_BYTES,
    limits: InferdromeArchiveLimits | None = None,
) -> ExtractedInferdromeArchive:
    """Verify and manually extract one bounded archive into a new directory."""

    if not isinstance(archive_path, Path) or not isinstance(destination, Path):
        raise TypeError("archive_path and destination must be Path objects.")
    if not destination.is_absolute() or destination.exists():
        raise ValueError("destination must be a new absolute path.")
    _require_tagged_sha256(expected_sha256)
    if type(expected_size_bytes) is not int or expected_size_bytes <= 0:
        raise ValueError("expected_size_bytes must be a positive integer.")
    active_limits = limits if limits is not None else InferdromeArchiveLimits()
    if expected_size_bytes > active_limits.max_compressed_bytes:
        _reject(
            InferdromeArchiveErrorCode.ARCHIVE_LIMIT_EXCEEDED,
            "Pinned Inferdrome archive exceeds the compressed-byte limit.",
        )

    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(archive_path, flags)
    except OSError as error:
        _reject(
            InferdromeArchiveErrorCode.UNSAFE_ARCHIVE,
            "Inferdrome archive is missing, linked, or inaccessible.",
            error,
        )

    created = False
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_size != expected_size_bytes
            or before.st_size > active_limits.max_compressed_bytes
        ):
            _reject(
                InferdromeArchiveErrorCode.ARCHIVE_INTEGRITY_MISMATCH,
                "Inferdrome archive size or file type is invalid.",
            )
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            archive_sha256 = _hash_open_file(handle)
            if not hmac.compare_digest(archive_sha256, expected_sha256):
                _reject(
                    InferdromeArchiveErrorCode.ARCHIVE_INTEGRITY_MISMATCH,
                    "Inferdrome archive SHA-256 does not match its retained pin.",
                )
            handle.seek(0)
            members, counts = _scan_archive(handle, active_limits)
            handle.seek(0)
            destination.mkdir(mode=0o700, parents=False, exist_ok=False)
            created = True
            _materialize_archive(handle, destination, members, active_limits)
        after = os.fstat(descriptor)
        if _file_identity(before) != _file_identity(after):
            _reject(
                InferdromeArchiveErrorCode.UNSAFE_ARCHIVE,
                "Inferdrome archive changed during extraction.",
            )
        if hmac.compare_digest(expected_sha256, PINNED_ARCHIVE_SHA256):
            _verify_pinned_outer_provenance(destination)
    except InferdromeArchiveRejected:
        if created:
            shutil.rmtree(destination, ignore_errors=True)
        raise
    except (OSError, tarfile.TarError, EOFError, ValueError) as error:
        if created:
            shutil.rmtree(destination, ignore_errors=True)
        _reject(
            InferdromeArchiveErrorCode.UNSAFE_ARCHIVE,
            "Inferdrome archive could not be parsed or materialized safely.",
            error,
        )
    finally:
        os.close(descriptor)

    bundle_path = destination.joinpath(*PurePosixPath(PINNED_BUNDLE_MEMBER_PATH).parts)
    corrupted_path = destination.joinpath(
        *PurePosixPath(PINNED_CORRUPTED_MEMBER_PATH).parts
    )
    synthetic_path = destination.joinpath(
        *PurePosixPath(PINNED_SYNTHETIC_MEMBER_PATH).parts
    )
    if not all(path.is_dir() for path in (bundle_path, corrupted_path, synthetic_path)):
        shutil.rmtree(destination, ignore_errors=True)
        _reject(
            InferdromeArchiveErrorCode.ARCHIVE_INTEGRITY_MISMATCH,
            "Inferdrome archive is missing a pinned demonstration bundle.",
        )
    member_count, file_count, directory_count, expanded_bytes = counts
    return ExtractedInferdromeArchive(
        root=destination,
        archive_sha256=archive_sha256,
        bundle_path=bundle_path,
        corrupted_bundle_path=corrupted_path,
        synthetic_bundle_path=synthetic_path,
        member_count=member_count,
        file_count=file_count,
        directory_count=directory_count,
        expanded_bytes=expanded_bytes,
    )


def extract_external_inferdrome_archive(
    archive_path: Path,
    destination: Path,
    *,
    expected_member_path: str,
    expected_sha256: str,
    expected_size_bytes: int,
    limits: InferdromeArchiveLimits | None = None,
) -> ExtractedInferdromeArchiveMember:
    """Verify and selectively extract one exact bundle member.

    The complete tar stream is scanned, including members that are not
    materialized.  Only the requested bundle subtree is written, and all
    existing archive path, type, size, and stable-byte checks remain active.
    """

    if not isinstance(archive_path, Path) or not isinstance(destination, Path):
        raise TypeError("archive_path and destination must be Path objects.")
    if not destination.is_absolute() or destination.exists():
        raise ValueError("destination must be a new absolute path.")
    _require_tagged_sha256(expected_sha256)
    if type(expected_size_bytes) is not int or expected_size_bytes <= 0:
        raise ValueError("expected_size_bytes must be a positive integer.")
    active_limits = limits if limits is not None else InferdromeArchiveLimits()
    if expected_size_bytes > active_limits.max_compressed_bytes:
        _reject(
            InferdromeArchiveErrorCode.ARCHIVE_LIMIT_EXCEEDED,
            "Inferdrome archive exceeds the compressed-byte limit.",
        )
    selected_member_path = _safe_member_name(
        expected_member_path,
        active_limits.max_depth,
    )
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(archive_path, flags)
    except OSError as error:
        _reject(
            InferdromeArchiveErrorCode.UNSAFE_ARCHIVE,
            "Inferdrome archive is missing, linked, or inaccessible.",
            error,
        )
    created = False
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_size != expected_size_bytes
            or before.st_size > active_limits.max_compressed_bytes
        ):
            _reject(
                InferdromeArchiveErrorCode.ARCHIVE_INTEGRITY_MISMATCH,
                "Inferdrome archive size or file type is invalid.",
            )
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            archive_sha256 = _hash_open_file(handle)
            if not hmac.compare_digest(archive_sha256, expected_sha256):
                _reject(
                    InferdromeArchiveErrorCode.ARCHIVE_INTEGRITY_MISMATCH,
                    "Inferdrome archive SHA-256 does not match its retained pin.",
                )
            handle.seek(0)
            members, counts = _scan_archive(
                handle,
                active_limits,
                selected_roots=(selected_member_path,),
                selected_outer_members=frozenset(),
            )
            handle.seek(0)
            destination.mkdir(mode=0o700, parents=False, exist_ok=False)
            created = True
            _materialize_archive(handle, destination, members, active_limits)
        after = os.fstat(descriptor)
        if _file_identity(before) != _file_identity(after):
            _reject(
                InferdromeArchiveErrorCode.UNSAFE_ARCHIVE,
                "Inferdrome archive changed during extraction.",
            )
    except InferdromeArchiveRejected:
        if created:
            shutil.rmtree(destination, ignore_errors=True)
        raise
    except (OSError, tarfile.TarError, EOFError, ValueError) as error:
        if created:
            shutil.rmtree(destination, ignore_errors=True)
        _reject(
            InferdromeArchiveErrorCode.UNSAFE_ARCHIVE,
            "Inferdrome archive could not be parsed or materialized safely.",
            error,
        )
    finally:
        os.close(descriptor)

    member = destination.joinpath(*PurePosixPath(selected_member_path).parts)
    if not member.is_dir():
        shutil.rmtree(destination, ignore_errors=True)
        _reject(
            InferdromeArchiveErrorCode.ARCHIVE_INTEGRITY_MISMATCH,
            "Inferdrome archive is missing the requested bundle member.",
        )
    member_count, file_count, directory_count, expanded_bytes = counts
    return ExtractedInferdromeArchiveMember(
        root=destination,
        archive_sha256=archive_sha256,
        member_path=selected_member_path,
        member=member,
        member_count=member_count,
        file_count=file_count,
        directory_count=directory_count,
        expanded_bytes=expanded_bytes,
    )


def _scan_archive(
    handle: object,
    limits: InferdromeArchiveLimits,
    *,
    selected_roots: tuple[str, ...] = _SELECTED_MEMBER_ROOTS,
    selected_outer_members: frozenset[str] = _SELECTED_OUTER_MEMBERS,
) -> tuple[list[_ArchiveMember], tuple[int, int, int, int]]:
    members: list[_ArchiveMember] = []
    seen: set[str] = set()
    seen_portable: set[str] = set()
    file_count = 0
    directory_count = 0
    expanded_bytes = 0
    with tarfile.open(fileobj=handle, mode="r:gz") as archive:  # type: ignore[arg-type]
        for raw in archive:
            if len(members) >= limits.max_members:
                _reject(
                    InferdromeArchiveErrorCode.ARCHIVE_LIMIT_EXCEEDED,
                    "Inferdrome archive member limit was exceeded.",
                )
            name = _safe_member_name(raw.name, limits.max_depth)
            portable_name = unicodedata.normalize("NFC", name).casefold()
            if name in seen or portable_name in seen_portable:
                _reject(
                    InferdromeArchiveErrorCode.UNSAFE_ARCHIVE,
                    "Inferdrome archive contains a colliding member path.",
                )
            seen.add(name)
            seen_portable.add(portable_name)
            if raw.isfile():
                if raw.sparse:
                    _reject(
                        InferdromeArchiveErrorCode.UNSAFE_ARCHIVE,
                        "Sparse Inferdrome archive members are forbidden.",
                    )
                if raw.size < 0 or raw.size > limits.max_file_bytes:
                    _reject(
                        InferdromeArchiveErrorCode.ARCHIVE_LIMIT_EXCEEDED,
                        "Inferdrome archive file exceeds its member limit.",
                    )
                file_count += 1
                expanded_bytes += raw.size
                if (
                    file_count > limits.max_files
                    or expanded_bytes > limits.max_expanded_bytes
                ):
                    _reject(
                        InferdromeArchiveErrorCode.ARCHIVE_LIMIT_EXCEEDED,
                        "Inferdrome archive expanded-byte limit was exceeded.",
                    )
                is_file = True
            elif raw.isdir():
                directory_count += 1
                if directory_count > limits.max_directories:
                    _reject(
                        InferdromeArchiveErrorCode.ARCHIVE_LIMIT_EXCEEDED,
                        "Inferdrome archive directory limit was exceeded.",
                    )
                is_file = False
            else:
                _reject(
                    InferdromeArchiveErrorCode.UNSAFE_ARCHIVE,
                    (
                        "Inferdrome archive links, devices, and special files "
                        "are forbidden."
                    ),
                )
            members.append(
                _ArchiveMember(
                    name=name,
                    size=raw.size,
                    is_file=is_file,
                    materialize=_selected_member(
                        name,
                        selected_roots=selected_roots,
                        selected_outer_members=selected_outer_members,
                    ),
                )
            )
    counts = (len(members), file_count, directory_count, expanded_bytes)
    return members, counts


def _materialize_archive(
    handle: object,
    destination: Path,
    expected_members: list[_ArchiveMember],
    limits: InferdromeArchiveLimits,
) -> None:
    with tarfile.open(fileobj=handle, mode="r:gz") as archive:  # type: ignore[arg-type]
        materialized = 0
        for expected, raw in zip(expected_members, archive, strict=True):
            materialized += 1
            name = _safe_member_name(raw.name, limits.max_depth)
            if (
                name != expected.name
                or raw.size != expected.size
                or raw.isfile() != expected.is_file
            ):
                _reject(
                    InferdromeArchiveErrorCode.UNSAFE_ARCHIVE,
                    "Inferdrome archive changed between scan and extraction.",
                )
            if not expected.materialize:
                continue
            target = destination.joinpath(*PurePosixPath(name).parts)
            _require_beneath(destination, target)
            if expected.is_file:
                target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                source = archive.extractfile(raw)
                if source is None:
                    _reject(
                        InferdromeArchiveErrorCode.UNSAFE_ARCHIVE,
                        "Inferdrome archive file content is unavailable.",
                    )
                written = 0
                with source, target.open("xb") as output:
                    while True:
                        chunk = source.read(_READ_CHUNK_BYTES)
                        if not chunk:
                            break
                        written += len(chunk)
                        if written > expected.size:
                            _reject(
                                InferdromeArchiveErrorCode.ARCHIVE_LIMIT_EXCEEDED,
                                "Inferdrome archive file exceeded its declared size.",
                            )
                        output.write(chunk)
                if written != expected.size:
                    _reject(
                        InferdromeArchiveErrorCode.UNSAFE_ARCHIVE,
                        "Inferdrome archive file was truncated.",
                    )
                target.chmod(0o600)
            else:
                target.mkdir(mode=0o700, parents=True, exist_ok=True)
        if materialized != len(expected_members):
            _reject(
                InferdromeArchiveErrorCode.UNSAFE_ARCHIVE,
                "Inferdrome archive member count changed during extraction.",
            )


def _safe_member_name(value: str, max_depth: int) -> str:
    if (
        type(value) is not str
        or not value
        or len(value) > _MAX_MEMBER_NAME_CHARACTERS
        or "\\" in value
        or "\x00" in value
        or any(ord(character) < 32 for character in value)
    ):
        _reject(
            InferdromeArchiveErrorCode.UNSAFE_ARCHIVE,
            "Inferdrome archive contains an invalid member name.",
        )
    logical = PurePosixPath(value)
    if (
        logical.is_absolute()
        or len(logical.parts) > max_depth
        or logical.parts[0] != "capture"
        or any(part in {"", ".", ".."} for part in logical.parts)
    ):
        _reject(
            InferdromeArchiveErrorCode.UNSAFE_ARCHIVE,
            "Inferdrome archive member path escapes its capture root.",
        )
    return logical.as_posix()


def _selected_member(
    name: str,
    *,
    selected_roots: tuple[str, ...],
    selected_outer_members: frozenset[str],
) -> bool:
    return name in selected_outer_members or any(
        name == root or name.startswith(f"{root}/") for root in selected_roots
    )


def _verify_pinned_outer_provenance(root: Path) -> None:
    capture_path = root / "capture" / "capture-manifest.json"
    receipt_path = (
        root / "capture" / "single" / "real-gpu-5osfyjjl" / "demo-receipt.json"
    )
    host_path = root / "capture" / "support" / "host-preparation.json"
    capture_bytes = _bounded_outer_bytes(capture_path)
    receipt_bytes = _bounded_outer_bytes(receipt_path)
    host_bytes = _bounded_outer_bytes(host_path)
    if not hmac.compare_digest(
        _sha256_tagged(capture_bytes), PINNED_CAPTURE_MANIFEST_SHA256
    ):
        _reject(
            InferdromeArchiveErrorCode.ARCHIVE_INTEGRITY_MISMATCH,
            "Inferdrome capture manifest digest does not match its handoff pin.",
        )
    capture = _strict_outer_object(capture_bytes)
    receipt = _strict_outer_object(receipt_bytes)
    host = _strict_outer_object(host_bytes)
    single = capture.get("single") if type(capture.get("single")) is dict else {}
    support = capture.get("support") if type(capture.get("support")) is dict else {}
    host_claim = (
        support.get("host_preparation")
        if type(support.get("host_preparation")) is dict
        else {}
    )
    if (
        capture.get("schema_version") != "inferdrome.real-gpu-capture.v1"
        or capture.get("repository_commit") != CAPTURE_PRODUCER_COMMIT
        or capture.get("acceptance_boundary") != "PENDING_EXTERNAL_EXITSPEC"
        or single.get("receipt_path")
        != "single/real-gpu-5osfyjjl/demo-receipt.json"
        or single.get("receipt_sha256") != _sha256_tagged(receipt_bytes)
        or host_claim.get("path") != "support/host-preparation.json"
        or host_claim.get("sha256") != _sha256_tagged(host_bytes)
        or receipt.get("repository_commit") != CAPTURE_PRODUCER_COMMIT
        or receipt.get("acceptance_boundary") != "PENDING_EXTERNAL_EXITSPEC"
        or receipt.get("bundle_digest") != PINNED_BUNDLE_DIGEST
        or receipt.get("run_id") != PINNED_RUN_ID
        or host.get("repository_commit") != CAPTURE_PRODUCER_COMMIT
    ):
        _reject(
            InferdromeArchiveErrorCode.ARCHIVE_INTEGRITY_MISMATCH,
            "Inferdrome outer provenance claims are inconsistent.",
        )


def _bounded_outer_bytes(path: Path) -> bytes:
    try:
        content = path.read_bytes()
    except OSError as error:
        _reject(
            InferdromeArchiveErrorCode.ARCHIVE_INTEGRITY_MISMATCH,
            "Inferdrome outer provenance document is unavailable.",
            error,
        )
    if not content or len(content) > 1_048_576:
        _reject(
            InferdromeArchiveErrorCode.ARCHIVE_LIMIT_EXCEEDED,
            "Inferdrome outer provenance document exceeds its limit.",
        )
    return content


def _strict_outer_object(content: bytes) -> dict[str, object]:
    def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate outer provenance key")
            result[key] = value
        return result

    def reject_constant(value: str) -> NoReturn:
        raise ValueError(f"unsupported numeric constant: {value}")

    def reject_float(value: str) -> NoReturn:
        raise ValueError(f"unsupported decimal: {value}")

    try:
        value = json.loads(
            content,
            object_pairs_hook=unique_object,
            parse_constant=reject_constant,
            parse_float=reject_float,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        _reject(
            InferdromeArchiveErrorCode.ARCHIVE_INTEGRITY_MISMATCH,
            "Inferdrome outer provenance document is invalid JSON.",
            error,
        )
    if type(value) is not dict:
        _reject(
            InferdromeArchiveErrorCode.ARCHIVE_INTEGRITY_MISMATCH,
            "Inferdrome outer provenance document must be an object.",
        )
    return value


def _sha256_tagged(content: bytes) -> str:
    return _TAGGED_SHA256 + hashlib.sha256(content).hexdigest()


def _require_beneath(root: Path, target: Path) -> None:
    try:
        target.relative_to(root)
    except ValueError:
        _reject(
            InferdromeArchiveErrorCode.UNSAFE_ARCHIVE,
            "Inferdrome archive target escapes the extraction root.",
        )


def _hash_open_file(handle: object) -> str:
    digest = hashlib.sha256()
    while True:
        chunk = handle.read(_READ_CHUNK_BYTES)  # type: ignore[attr-defined]
        if not chunk:
            break
        digest.update(chunk)
    return _TAGGED_SHA256 + digest.hexdigest()


def _file_identity(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _require_tagged_sha256(value: str) -> None:
    if (
        type(value) is not str
        or not value.startswith(_TAGGED_SHA256)
        or len(value) != len(_TAGGED_SHA256) + 64
        or any(character not in "0123456789abcdef" for character in value[7:])
    ):
        raise ValueError("expected_sha256 must be a tagged lowercase SHA-256.")


def _reject(
    code: InferdromeArchiveErrorCode,
    message: str,
    cause: BaseException | None = None,
) -> NoReturn:
    error = InferdromeArchiveRejected(code, message)
    if cause is None:
        raise error
    raise error from cause


__all__ = [
    "ExtractedInferdromeArchive",
    "ExtractedInferdromeArchiveMember",
    "InferdromeArchiveErrorCode",
    "InferdromeArchiveLimits",
    "InferdromeArchiveRejected",
    "extract_external_inferdrome_archive",
    "extract_pinned_inferdrome_archive",
]
