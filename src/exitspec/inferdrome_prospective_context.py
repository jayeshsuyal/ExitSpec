"""Checked, bounded byte snapshots for the fixed existing P1 handoff."""

from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .confirmations import ContractConfirmation
from .inferdrome_bundle import InferdromeBundleLimits, _identity, _SafeBundleReader
from .inferdrome_prospective import (
    PROSPECTIVE_ARTIFACT_MAX_BYTES,
    PROSPECTIVE_CASES,
    PROSPECTIVE_TREE_MAX_BYTES,
    PROSPECTIVE_TREE_MAX_DEPTH,
    PROSPECTIVE_TREE_MAX_FILES,
    ProspectiveHandoffCaseModel,
    confirmation_idempotency_key,
    validate_prospective_handoff,
)
from .models import POCContract
from .performance_serialization import parse_confirmation, parse_contract


class ManifestPinMismatch(ValueError):
    """The captured manifest differs from the required byte pin."""


@dataclass(frozen=True)
class ProspectiveContext:
    manifest_sha256: str
    case: ProspectiveHandoffCaseModel
    contract: POCContract
    confirmation: ContractConfirmation
    source_bytes: bytes
    workload_bytes: bytes
    reader: _SafeBundleReader


def _capture_file(reader: _SafeBundleReader, name: str) -> bytes:
    """Use the existing bounded scan, with nonblocking opens against FIFO swaps."""
    descriptors: list[int] = []
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK
    try:
        fd = os.open(reader.root, directory_flags)
        descriptors.append(fd)
        if _identity(os.fstat(fd)) != reader._root_identity:
            raise ValueError("Handoff root changed.")
        parts = name.split("/")
        for index, part in enumerate(parts[:-1]):
            fd = os.open(part, directory_flags, dir_fd=fd)
            descriptors.append(fd)
            if (
                _identity(os.fstat(fd))
                != reader._directories["/".join(parts[: index + 1])].identity
            ):
                raise ValueError("Handoff directory changed.")
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        descriptors.append(fd)
        identity = reader._files[name].identity
        if _identity(os.fstat(fd)) != identity:
            raise ValueError("Handoff file changed.")
        result = bytearray()
        while len(result) <= identity.size:
            chunk = os.read(fd, min(65536, identity.size + 1 - len(result)))
            if not chunk:
                break
            result.extend(chunk)
        if len(result) != identity.size or _identity(os.fstat(fd)) != identity:
            raise ValueError("Handoff bytes changed.")
        return bytes(result)
    finally:
        for fd in reversed(descriptors):
            os.close(fd)


def capture_prospective_context(
    root: Path, case_id: str, expected_manifest_sha256: str
) -> ProspectiveContext:
    if not root.is_absolute() or root.resolve() != root:
        raise ValueError("Handoff requires an absolute path without symlinks.")
    reader = _SafeBundleReader(
        root,
        InferdromeBundleLimits(
            max_files=PROSPECTIVE_TREE_MAX_FILES,
            max_directories=4,
            max_file_bytes=PROSPECTIVE_ARTIFACT_MAX_BYTES,
            max_total_bytes=PROSPECTIVE_TREE_MAX_BYTES,
            max_depth=PROSPECTIVE_TREE_MAX_DEPTH,
        ),
    )
    expected_files = {
        ".complete",
        "handoff-manifest.json",
        "sources/real-gpu/workload.jsonl",
    }
    for case in PROSPECTIVE_CASES:
        expected_files.update(
            {
                f"contracts/{case.case_id}.frozen.json",
                f"confirmations/{case.case_id}.confirmation.json",
                f"sources/{case.case_id}.yaml",
            }
        )
    if reader.files != expected_files or reader.directories != {
        "contracts",
        "confirmations",
        "sources",
        "sources/real-gpu",
    }:
        raise ValueError("Handoff inventory is not exact P1.")
    captured = {name: _capture_file(reader, name) for name in sorted(reader.files)}
    reader.assert_unchanged()
    digest = hashlib.sha256(captured["handoff-manifest.json"]).hexdigest()
    if digest != expected_manifest_sha256:
        raise ManifestPinMismatch("Handoff manifest does not match its pin.")
    # Existing P1 validation operates only on our private checked byte copy.
    with tempfile.TemporaryDirectory(prefix="exitspec-p1-context-") as temporary:
        snapshot = Path(temporary)
        for name, raw in captured.items():
            path = snapshot / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        validated = validate_prospective_handoff(snapshot)
        check = _SafeBundleReader(snapshot, reader.limits)
        if (
            validated.manifest_sha256 != digest
            or check.files != reader.files
            or check.directories != reader.directories
            or any(_capture_file(check, name) != raw for name, raw in captured.items())
        ):
            raise ValueError("Private context snapshot changed.")
        check.assert_unchanged()
    selected = next(c for c in validated.manifest.cases if c.case_id == case_id)
    # Parse the captured bytes themselves, never reread the original paths.
    contract = parse_contract(captured[selected.contract_artifact_path])
    confirmation = parse_confirmation(
        captured[selected.confirmation_artifact_path],
        idempotency_key=confirmation_idempotency_key(case_id),
    )
    reader.assert_unchanged()
    return ProspectiveContext(
        digest,
        selected,
        contract,
        confirmation,
        captured[selected.source_yaml_artifact_path],
        captured[validated.manifest.workload_artifact_path],
        reader,
    )
