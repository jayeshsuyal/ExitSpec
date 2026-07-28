"""Atomic persistence for inference-performance evidence artifacts.

This module is intentionally content-agnostic. It canonicalizes JSON syntax,
publishes a fixed artifact layout atomically, and verifies bytes and filesystem
structure on every read. It does not interpret contracts, receipts,
measurements, or verdicts.
"""

from __future__ import annotations

import errno
import hashlib
import hmac
import json
import os
import re
import shutil
import stat
import tempfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Final, Mapping, Sequence

from .canonical import CanonicalizationError, canonical_json_bytes


ARTIFACT_REGISTRY_SCHEMA_VERSION: Final = (
    "exitspec.performance-artifact-registry.v1"
)
ARTIFACT_HASHES_SCHEMA_VERSION: Final = (
    "exitspec.performance-artifact-hashes.v1"
)

REGISTRY_PATH: Final = "evidence-artifacts.json"
HASHES_PATH: Final = "artifact-hashes.json"

MAX_ARTIFACT_BYTES: Final = 64 * 1024 * 1024
MAX_TOTAL_PAYLOAD_BYTES: Final = 256 * 1024 * 1024

REDACTION_NOT_ASSESSED: Final = "not_assessed"
REDACTION_REDACTED: Final = "redacted"
REDACTION_SYNTHETIC_NO_PII: Final = "synthetic_no_pii"
REDACTION_CONTAINS_CUSTOMER_DATA: Final = "contains_customer_data"
REDACTION_NOT_APPLICABLE: Final = "not_applicable"

_ALLOWED_REDACTION_STATES: Final = frozenset(
    {
        REDACTION_NOT_ASSESSED,
        REDACTION_REDACTED,
        REDACTION_SYNTHETIC_NO_PII,
        REDACTION_CONTAINS_CUSTOMER_DATA,
    }
)
_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")
_ARTIFACT_ID = re.compile(r"[a-z][a-z0-9-]{0,127}\Z")
_SHA256 = re.compile(r"[a-f0-9]{64}\Z")
_ENTRY_KEYS: Final = frozenset(
    {
        "artifact_id",
        "artifact_type",
        "path",
        "media_type",
        "size_bytes",
        "sha256",
        "redaction_state",
    }
)


class PerformanceArtifactError(ValueError):
    """Base error for invalid performance artifact persistence."""


class PerformanceArtifactValidationError(PerformanceArtifactError):
    """Input bytes or metadata are not safe to persist."""


class PerformanceArtifactIntegrityError(PerformanceArtifactError):
    """Persisted files do not match the fixed layout and registry."""


class PerformanceArtifactConflictError(FileExistsError):
    """The requested run target or its publication lock already exists."""


@dataclass(frozen=True, slots=True)
class PerformanceArtifactInputs:
    """Exact serialized inputs for one performance decision.

    Derived JSON objects and evidence records are syntax-validated and
    canonicalized. Byte-bound workload and prompt inputs are validated without
    rewriting so their frozen SHA-256 identities remain exact. The module never
    constructs or interprets ExitSpec domain objects.
    """

    contract_json: bytes
    confirmation_json: bytes
    workload_json: bytes
    prompt_fixture_jsonl: bytes
    preflight_json: bytes
    probe_manifest_json: bytes
    records_jsonl: bytes
    receipt_json: bytes
    calculations_json: bytes
    verdicts_json: bytes
    decision_packet_html: bytes
    redaction_states: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class VerifiedPerformanceArtifacts:
    """The exact bytes returned after complete filesystem verification."""

    run_id: str
    run_dir: Path
    files: Mapping[str, bytes]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "files",
            MappingProxyType(dict(self.files)),
        )

    def bytes_for(self, relative_path: str) -> bytes:
        """Return verified bytes for one fixed-layout path."""

        try:
            return self.files[relative_path]
        except KeyError:
            raise KeyError(
                "No verified performance artifact exists at that path."
            ) from None

    @property
    def contract_json(self) -> bytes:
        return self.files["contract.json"]

    @property
    def confirmation_json(self) -> bytes:
        return self.files["confirmation.json"]

    @property
    def workload_json(self) -> bytes:
        return self.files["workload.json"]

    @property
    def prompt_fixture_jsonl(self) -> bytes:
        return self.files["prompt-fixture.jsonl"]

    @property
    def preflight_json(self) -> bytes:
        return self.files["evidence/preflight.json"]

    @property
    def probe_manifest_json(self) -> bytes:
        return self.files["evidence/probe-manifest.json"]

    @property
    def records_jsonl(self) -> bytes:
        return self.files["evidence/probe-records.jsonl"]

    @property
    def receipt_json(self) -> bytes:
        return self.files["receipt.json"]

    @property
    def calculations_json(self) -> bytes:
        return self.files["calculations.json"]

    @property
    def verdicts_json(self) -> bytes:
        return self.files["verdicts.json"]

    @property
    def decision_packet_html(self) -> bytes:
        return self.files["decision-packet.html"]

    @property
    def registry_json(self) -> bytes:
        return self.files[REGISTRY_PATH]

    @property
    def artifact_hashes_json(self) -> bytes:
        return self.files[HASHES_PATH]


@dataclass(frozen=True, slots=True)
class _ArtifactSpec:
    field_name: str
    artifact_id: str
    artifact_type: str
    path: str
    media_type: str
    serialization: str


_SPECS: Final[tuple[_ArtifactSpec, ...]] = (
    _ArtifactSpec(
        "contract_json",
        "input-contract",
        "frozen_contract",
        "contract.json",
        "application/json",
        "json",
    ),
    _ArtifactSpec(
        "confirmation_json",
        "input-confirmation",
        "customer_confirmation",
        "confirmation.json",
        "application/json",
        "json",
    ),
    _ArtifactSpec(
        "workload_json",
        "input-workload",
        "frozen_workload",
        "workload.json",
        "application/json",
        "exact_json",
    ),
    _ArtifactSpec(
        "prompt_fixture_jsonl",
        "input-prompt-fixture",
        "prompt_fixture",
        "prompt-fixture.jsonl",
        "application/x-ndjson",
        "exact_jsonl",
    ),
    _ArtifactSpec(
        "preflight_json",
        "evidence-preflight",
        "endpoint_readiness_probe",
        "evidence/preflight.json",
        "application/json",
        "json",
    ),
    _ArtifactSpec(
        "probe_manifest_json",
        "evidence-probe-manifest",
        "probe_manifest",
        "evidence/probe-manifest.json",
        "application/json",
        "json",
    ),
    _ArtifactSpec(
        "records_jsonl",
        "evidence-probe-records",
        "probe_records",
        "evidence/probe-records.jsonl",
        "application/x-ndjson",
        "exact_jsonl",
    ),
    _ArtifactSpec(
        "receipt_json",
        "execution-receipt",
        "execution_receipt",
        "receipt.json",
        "application/json",
        "json",
    ),
    _ArtifactSpec(
        "calculations_json",
        "performance-calculations",
        "performance_calculations",
        "calculations.json",
        "application/json",
        "json",
    ),
    _ArtifactSpec(
        "verdicts_json",
        "performance-verdicts",
        "performance_verdicts",
        "verdicts.json",
        "application/json",
        "json",
    ),
    _ArtifactSpec(
        "decision_packet_html",
        "performance-decision-packet",
        "customer_evidence_pack",
        "decision-packet.html",
        "text/html; charset=utf-8",
        "html",
    ),
)
_SPEC_BY_PATH: Final = {spec.path: spec for spec in _SPECS}
_CONTENT_PATHS: Final = frozenset(_SPEC_BY_PATH)
_ALL_FILE_PATHS: Final = _CONTENT_PATHS | {REGISTRY_PATH, HASHES_PATH}
_REGISTRY_SPEC: Final = _ArtifactSpec(
    "registry_json",
    "artifact-registry",
    "artifact_registry",
    REGISTRY_PATH,
    "application/json",
    "json",
)


def persist_performance_artifacts(
    output_root: Path,
    run_id: str,
    inputs: PerformanceArtifactInputs,
) -> VerifiedPerformanceArtifacts:
    """Canonicalize and atomically publish one complete run directory.

    Publication uses a same-parent temporary directory. Every file and
    directory is flushed before a final rename. Existing targets are never
    reused.
    """

    _validate_run_id(run_id)
    if type(inputs) is not PerformanceArtifactInputs:
        raise TypeError("inputs must be PerformanceArtifactInputs.")

    normalized = _normalize_inputs(inputs)
    root = _prepare_output_root(output_root)
    target = root / run_id
    if os.path.lexists(target):
        raise PerformanceArtifactConflictError(
            "Performance run target already exists."
        )

    lock_path = root / (".{0}.publish.lock".format(run_id))
    lock_fd = _acquire_publish_lock(lock_path)
    temporary: Path | None = None
    published = False
    try:
        if os.path.lexists(target):
            raise PerformanceArtifactConflictError(
                "Performance run target already exists."
            )
        temporary = Path(
            tempfile.mkdtemp(
                prefix=".{0}.tmp-".format(run_id),
                dir=root,
            )
        )
        evidence_dir = temporary / "evidence"
        evidence_dir.mkdir(mode=0o700)

        content_entries: list[dict[str, object]] = []
        for spec in _SPECS:
            payload = normalized[spec.path]
            _write_file(temporary / spec.path, payload)
            content_entries.append(
                _entry(
                    spec,
                    payload,
                    inputs.redaction_states.get(
                        spec.path,
                        REDACTION_NOT_ASSESSED,
                    ),
                )
            )

        registry = {
            "schema_version": ARTIFACT_REGISTRY_SCHEMA_VERSION,
            "run_id": run_id,
            "artifacts": content_entries,
        }
        registry_bytes = canonical_json_bytes(registry)
        _write_file(temporary / REGISTRY_PATH, registry_bytes)

        inventory_entries = list(content_entries)
        inventory_entries.append(
            _entry(
                _REGISTRY_SPEC,
                registry_bytes,
                REDACTION_NOT_APPLICABLE,
            )
        )
        hashes = {
            "schema_version": ARTIFACT_HASHES_SCHEMA_VERSION,
            "run_id": run_id,
            "algorithm": "sha256",
            "artifacts": inventory_entries,
        }
        hashes_bytes = canonical_json_bytes(hashes)
        _write_file(temporary / HASHES_PATH, hashes_bytes)

        _fsync_directory(evidence_dir)
        _fsync_directory(temporary)
        _fsync_directory(root)
        if os.path.lexists(target):
            raise PerformanceArtifactConflictError(
                "Performance run target already exists."
            )
        os.rename(temporary, target)
        temporary = None
        published = True
        _fsync_directory(root)
    except (CanonicalizationError, OSError) as exc:
        if isinstance(exc, PerformanceArtifactConflictError):
            raise
        raise PerformanceArtifactValidationError(
            "Performance artifacts could not be published safely."
        ) from exc
    finally:
        if temporary is not None:
            shutil.rmtree(temporary, ignore_errors=True)
        os.close(lock_fd)
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass
        _fsync_directory(root)

    if not published:
        raise PerformanceArtifactValidationError(
            "Performance artifact publication did not complete."
        )
    return read_and_verify_performance_artifacts(target)


def read_and_verify_performance_artifacts(
    run_dir: Path,
) -> VerifiedPerformanceArtifacts:
    """Read a fixed run layout and independently verify every exact byte."""

    supplied = Path(run_dir)
    if supplied.is_symlink():
        raise PerformanceArtifactIntegrityError(
            "Performance run directory cannot be a symlink."
        )
    try:
        resolved = supplied.resolve(strict=True)
    except (FileNotFoundError, OSError) as exc:
        raise PerformanceArtifactIntegrityError(
            "Performance run directory is missing or inaccessible."
        ) from exc
    if not resolved.is_dir():
        raise PerformanceArtifactIntegrityError(
            "Performance run path must be a directory."
        )
    try:
        _validate_run_id(resolved.name)
    except PerformanceArtifactValidationError as exc:
        raise PerformanceArtifactIntegrityError(
            "Performance run directory has an unsafe run ID."
        ) from exc

    _verify_fixed_layout(resolved)
    exact_bytes = {
        path: _read_regular_file(resolved / path)
        for path in sorted(_ALL_FILE_PATHS)
    }
    if (
        sum(len(exact_bytes[path]) for path in _CONTENT_PATHS)
        > MAX_TOTAL_PAYLOAD_BYTES
    ):
        raise PerformanceArtifactIntegrityError(
            "Performance artifact payload exceeds the aggregate byte limit."
        )

    registry = _parse_canonical_document(
        exact_bytes[REGISTRY_PATH],
        "artifact registry",
    )
    hashes = _parse_canonical_document(
        exact_bytes[HASHES_PATH],
        "artifact hash inventory",
    )
    registry_entries = _validate_registry(registry, resolved.name)
    hash_entries = _validate_hash_inventory(hashes, resolved.name)

    expected_registry_paths = set(_CONTENT_PATHS)
    if set(registry_entries) != expected_registry_paths:
        raise PerformanceArtifactIntegrityError(
            "Artifact registry does not contain the exact fixed payload set."
        )
    expected_hash_paths = expected_registry_paths | {REGISTRY_PATH}
    if set(hash_entries) != expected_hash_paths:
        raise PerformanceArtifactIntegrityError(
            "Artifact hash inventory does not contain the exact fixed set."
        )

    for path, registry_entry in registry_entries.items():
        if hash_entries[path] != registry_entry:
            raise PerformanceArtifactIntegrityError(
                "Artifact registry and hash inventory disagree."
            )
    expected_registry_entry = _entry(
        _REGISTRY_SPEC,
        exact_bytes[REGISTRY_PATH],
        REDACTION_NOT_APPLICABLE,
    )
    if hash_entries[REGISTRY_PATH] != expected_registry_entry:
        raise PerformanceArtifactIntegrityError(
            "Artifact registry hash metadata is invalid."
        )

    for path, entry in hash_entries.items():
        payload = exact_bytes[path]
        if entry["size_bytes"] != len(payload):
            raise PerformanceArtifactIntegrityError(
                "Registered artifact size does not match exact bytes."
            )
        digest = hashlib.sha256(payload).hexdigest()
        if not hmac.compare_digest(str(entry["sha256"]), digest):
            raise PerformanceArtifactIntegrityError(
                "Registered artifact hash does not match exact bytes."
            )

    for spec in _SPECS:
        payload = exact_bytes[spec.path]
        if spec.serialization == "json":
            normalized = _canonicalize_json(payload, spec.path)
            if payload != normalized:
                raise PerformanceArtifactIntegrityError(
                    "Persisted artifact is not in its required canonical form."
                )
        elif spec.serialization == "jsonl":
            normalized = _canonicalize_jsonl(payload, spec.path)
            if payload != normalized:
                raise PerformanceArtifactIntegrityError(
                    "Persisted artifact is not in its required canonical form."
                )
        elif spec.serialization == "exact_json":
            if not isinstance(_parse_json(payload, spec.path), dict):
                raise PerformanceArtifactIntegrityError(
                    "Persisted exact JSON artifact must contain one object."
                )
        elif spec.serialization == "exact_jsonl":
            _canonicalize_jsonl(payload, spec.path)
        else:
            _validate_html(payload, spec.path)

    return VerifiedPerformanceArtifacts(
        run_id=resolved.name,
        run_dir=resolved,
        files=exact_bytes,
    )


def read_performance_artifacts(
    run_dir: Path,
) -> VerifiedPerformanceArtifacts:
    """Compatibility spelling for verified reads."""

    return read_and_verify_performance_artifacts(run_dir)


def verify_performance_artifacts(
    run_dir: Path,
) -> VerifiedPerformanceArtifacts:
    """Explicit verification spelling; always returns verified exact bytes."""

    return read_and_verify_performance_artifacts(run_dir)


def _normalize_inputs(
    inputs: PerformanceArtifactInputs,
) -> dict[str, bytes]:
    try:
        states = dict(inputs.redaction_states)
    except (TypeError, ValueError) as exc:
        raise PerformanceArtifactValidationError(
            "Redaction states must be a path-to-state mapping."
        ) from exc
    unknown_paths = set(states) - _CONTENT_PATHS
    if unknown_paths:
        raise PerformanceArtifactValidationError(
            "Redaction states contain an unknown artifact path."
        )
    for state in states.values():
        if type(state) is not str or state not in _ALLOWED_REDACTION_STATES:
            raise PerformanceArtifactValidationError(
                "Artifact redaction state is not supported."
            )

    normalized: dict[str, bytes] = {}
    total = 0
    for spec in _SPECS:
        raw = getattr(inputs, spec.field_name)
        if type(raw) is not bytes:
            raise PerformanceArtifactValidationError(
                "{0} must be exact bytes.".format(spec.field_name)
            )
        if len(raw) > MAX_ARTIFACT_BYTES:
            raise PerformanceArtifactValidationError(
                "A performance artifact exceeds the byte limit."
            )
        if spec.serialization == "json":
            payload = _canonicalize_json(raw, spec.path)
        elif spec.serialization == "jsonl":
            payload = _canonicalize_jsonl(raw, spec.path)
        elif spec.serialization == "exact_json":
            if not isinstance(_parse_json(raw, spec.path), dict):
                raise PerformanceArtifactValidationError(
                    "{0} must contain one JSON object.".format(spec.path)
                )
            payload = raw
        elif spec.serialization == "exact_jsonl":
            _canonicalize_jsonl(raw, spec.path)
            payload = raw
        else:
            _validate_html(raw, spec.path)
            payload = raw
        if len(payload) > MAX_ARTIFACT_BYTES:
            raise PerformanceArtifactValidationError(
                "A canonical performance artifact exceeds the byte limit."
            )
        total += len(payload)
        if total > MAX_TOTAL_PAYLOAD_BYTES:
            raise PerformanceArtifactValidationError(
                "Performance artifact payload exceeds the aggregate byte limit."
            )
        normalized[spec.path] = payload
    return normalized


def _canonicalize_json(raw: bytes, label: str) -> bytes:
    value = _parse_json(raw, label)
    if not isinstance(value, dict):
        raise PerformanceArtifactValidationError(
            "{0} must contain one JSON object.".format(label)
        )
    try:
        return canonical_json_bytes(value)
    except CanonicalizationError as exc:
        raise PerformanceArtifactValidationError(
            "{0} is outside the RFC 8785 JSON domain.".format(label)
        ) from exc


def _canonicalize_jsonl(raw: bytes, label: str) -> bytes:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PerformanceArtifactValidationError(
            "{0} must be UTF-8 JSONL.".format(label)
        ) from exc
    lines = text.splitlines()
    if not lines or any(not line.strip() for line in lines):
        raise PerformanceArtifactValidationError(
            "{0} must contain non-empty JSON objects only.".format(label)
        )
    canonical_lines: list[bytes] = []
    for line in lines:
        value = _parse_json(line.encode("utf-8"), label)
        if not isinstance(value, dict):
            raise PerformanceArtifactValidationError(
                "{0} records must be JSON objects.".format(label)
            )
        try:
            canonical_lines.append(canonical_json_bytes(value))
        except CanonicalizationError as exc:
            raise PerformanceArtifactValidationError(
                "{0} is outside the RFC 8785 JSON domain.".format(label)
            ) from exc
    return b"\n".join(canonical_lines) + b"\n"


def _validate_html(raw: bytes, label: str) -> None:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PerformanceArtifactValidationError(
            "{0} must be UTF-8 HTML.".format(label)
        ) from exc
    if not text.strip() or "\x00" in text:
        raise PerformanceArtifactValidationError(
            "{0} must contain non-empty UTF-8 HTML.".format(label)
        )


def _parse_json(raw: bytes, label: str) -> object:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PerformanceArtifactValidationError(
            "{0} must be UTF-8 JSON.".format(label)
        ) from exc

    def reject_duplicate_pairs(
        pairs: Sequence[tuple[str, object]],
    ) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise PerformanceArtifactValidationError(
                    "{0} contains a duplicate JSON field.".format(label)
                )
            result[key] = value
        return result

    def reject_constant(value: str) -> object:
        raise PerformanceArtifactValidationError(
            "{0} contains a non-finite JSON number: {1}.".format(label, value)
        )

    try:
        return json.loads(
            text,
            object_pairs_hook=reject_duplicate_pairs,
            parse_constant=reject_constant,
        )
    except PerformanceArtifactValidationError:
        raise
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise PerformanceArtifactValidationError(
            "{0} is not valid JSON.".format(label)
        ) from exc


def _entry(
    spec: _ArtifactSpec,
    payload: bytes,
    redaction_state: str,
) -> dict[str, object]:
    if redaction_state not in _ALLOWED_REDACTION_STATES | {
        REDACTION_NOT_APPLICABLE
    }:
        raise PerformanceArtifactValidationError(
            "Artifact redaction state is not supported."
        )
    return {
        "artifact_id": spec.artifact_id,
        "artifact_type": spec.artifact_type,
        "path": spec.path,
        "media_type": spec.media_type,
        "size_bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "redaction_state": redaction_state,
    }


def _prepare_output_root(output_root: Path) -> Path:
    supplied = Path(output_root)
    if supplied.is_symlink():
        raise PerformanceArtifactValidationError(
            "Output root cannot be a symlink."
        )
    try:
        supplied.mkdir(parents=True, exist_ok=True, mode=0o700)
        resolved = supplied.resolve(strict=True)
    except OSError as exc:
        raise PerformanceArtifactValidationError(
            "Output root could not be prepared."
        ) from exc
    if not resolved.is_dir():
        raise PerformanceArtifactValidationError(
            "Output root must be a directory."
        )
    return resolved


def _validate_run_id(run_id: str) -> None:
    if type(run_id) is not str or not _RUN_ID.fullmatch(run_id):
        raise PerformanceArtifactValidationError(
            "Run ID must be one safe path component."
        )
    if PurePosixPath(run_id).name != run_id or "\\" in run_id:
        raise PerformanceArtifactValidationError(
            "Run ID must not contain path traversal."
        )


def _validate_relative_path(path: object) -> str:
    if type(path) is not str or not path:
        raise PerformanceArtifactIntegrityError(
            "Registered artifact path is invalid."
        )
    if "\\" in path:
        raise PerformanceArtifactIntegrityError(
            "Registered artifact path is not canonical."
        )
    candidate = PurePosixPath(path)
    if (
        candidate.is_absolute()
        or candidate.as_posix() != path
        or any(part in {"", ".", ".."} for part in candidate.parts)
    ):
        raise PerformanceArtifactIntegrityError(
            "Registered artifact path is unsafe."
        )
    return path


def _write_file(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        _fsync_descriptor(handle.fileno())


def _acquire_publish_lock(lock_path: Path) -> int:
    try:
        descriptor = os.open(
            lock_path,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            0o600,
        )
    except FileExistsError as exc:
        raise PerformanceArtifactConflictError(
            "Performance run publication is already in progress."
        ) from exc
    try:
        _fsync_descriptor(descriptor)
    except OSError:
        os.close(descriptor)
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass
        raise
    return descriptor


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        if exc.errno in _UNSUPPORTED_FSYNC_ERRORS:
            return
        raise
    try:
        _fsync_descriptor(descriptor)
    finally:
        os.close(descriptor)


_UNSUPPORTED_FSYNC_ERRORS: Final = {
    errno.EBADF,
    errno.EINVAL,
    errno.EISDIR,
    errno.ENOTSUP,
}


def _fsync_descriptor(descriptor: int) -> None:
    try:
        os.fsync(descriptor)
    except OSError as exc:
        if exc.errno not in _UNSUPPORTED_FSYNC_ERRORS:
            raise


def _verify_fixed_layout(run_dir: Path) -> None:
    actual_files: set[str] = set()
    actual_directories: set[str] = set()
    for current, directories, files in os.walk(run_dir, followlinks=False):
        current_path = Path(current)
        for directory in directories:
            candidate = current_path / directory
            relative = candidate.relative_to(run_dir).as_posix()
            if candidate.is_symlink():
                raise PerformanceArtifactIntegrityError(
                    "Performance artifact directories cannot be symlinks."
                )
            actual_directories.add(relative)
        for filename in files:
            candidate = current_path / filename
            relative = candidate.relative_to(run_dir).as_posix()
            if candidate.is_symlink():
                raise PerformanceArtifactIntegrityError(
                    "Performance artifact files cannot be symlinks."
                )
            actual_files.add(relative)
    if actual_directories != {"evidence"}:
        raise PerformanceArtifactIntegrityError(
            "Performance run contains missing or extra directories."
        )
    if actual_files != set(_ALL_FILE_PATHS):
        raise PerformanceArtifactIntegrityError(
            "Performance run contains missing or extra artifacts."
        )


def _read_regular_file(path: Path) -> bytes:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise PerformanceArtifactIntegrityError(
            "Registered artifact cannot be opened safely."
        ) from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise PerformanceArtifactIntegrityError(
                "Registered artifact must be a regular file."
            )
        if metadata.st_size > MAX_ARTIFACT_BYTES:
            raise PerformanceArtifactIntegrityError(
                "Registered artifact exceeds the byte limit."
            )
        chunks: list[bytes] = []
        remaining = metadata.st_size
        while remaining:
            chunk = os.read(descriptor, min(remaining, 1024 * 1024))
            if not chunk:
                raise PerformanceArtifactIntegrityError(
                    "Registered artifact changed while being read."
                )
            chunks.append(chunk)
            remaining -= len(chunk)
        extra = os.read(descriptor, 1)
        if extra:
            raise PerformanceArtifactIntegrityError(
                "Registered artifact changed while being read."
            )
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _parse_canonical_document(raw: bytes, label: str) -> dict[str, object]:
    try:
        value = _parse_json(raw, label)
    except PerformanceArtifactValidationError as exc:
        raise PerformanceArtifactIntegrityError(str(exc)) from exc
    if not isinstance(value, dict):
        raise PerformanceArtifactIntegrityError(
            "{0} must be a JSON object.".format(label)
        )
    try:
        canonical = canonical_json_bytes(value)
    except CanonicalizationError as exc:
        raise PerformanceArtifactIntegrityError(
            "{0} is outside the RFC 8785 JSON domain.".format(label)
        ) from exc
    if raw != canonical:
        raise PerformanceArtifactIntegrityError(
            "{0} is not RFC 8785 canonical JSON.".format(label)
        )
    return value


def _validate_registry(
    document: dict[str, object],
    run_id: str,
) -> dict[str, dict[str, object]]:
    if set(document) != {"schema_version", "run_id", "artifacts"}:
        raise PerformanceArtifactIntegrityError(
            "Artifact registry schema fields are invalid."
        )
    if (
        document["schema_version"] != ARTIFACT_REGISTRY_SCHEMA_VERSION
        or document["run_id"] != run_id
    ):
        raise PerformanceArtifactIntegrityError(
            "Artifact registry identity is invalid."
        )
    return _validate_entries(
        document["artifacts"],
        expected_specs=_SPEC_BY_PATH,
        allow_not_applicable=False,
    )


def _validate_hash_inventory(
    document: dict[str, object],
    run_id: str,
) -> dict[str, dict[str, object]]:
    if set(document) != {
        "schema_version",
        "run_id",
        "algorithm",
        "artifacts",
    }:
        raise PerformanceArtifactIntegrityError(
            "Artifact hash inventory schema fields are invalid."
        )
    if (
        document["schema_version"] != ARTIFACT_HASHES_SCHEMA_VERSION
        or document["run_id"] != run_id
        or document["algorithm"] != "sha256"
    ):
        raise PerformanceArtifactIntegrityError(
            "Artifact hash inventory identity is invalid."
        )
    specs = dict(_SPEC_BY_PATH)
    specs[REGISTRY_PATH] = _REGISTRY_SPEC
    return _validate_entries(
        document["artifacts"],
        expected_specs=specs,
        allow_not_applicable=True,
    )


def _validate_entries(
    raw_entries: object,
    *,
    expected_specs: Mapping[str, _ArtifactSpec],
    allow_not_applicable: bool,
) -> dict[str, dict[str, object]]:
    if type(raw_entries) is not list:
        raise PerformanceArtifactIntegrityError(
            "Artifact entries must be a JSON array."
        )
    entries: dict[str, dict[str, object]] = {}
    seen_ids: set[str] = set()
    for raw_entry in raw_entries:
        if not isinstance(raw_entry, dict) or set(raw_entry) != _ENTRY_KEYS:
            raise PerformanceArtifactIntegrityError(
                "Artifact registry entry fields are invalid."
            )
        artifact_id = raw_entry["artifact_id"]
        if (
            type(artifact_id) is not str
            or not _ARTIFACT_ID.fullmatch(artifact_id)
            or artifact_id in seen_ids
        ):
            raise PerformanceArtifactIntegrityError(
                "Artifact IDs must be safe and unique."
            )
        path = _validate_relative_path(raw_entry["path"])
        if path in entries:
            raise PerformanceArtifactIntegrityError(
                "Artifact paths must be unique."
            )
        spec = expected_specs.get(path)
        if spec is None:
            raise PerformanceArtifactIntegrityError(
                "Artifact entry is outside the fixed layout."
            )
        if (
            artifact_id != spec.artifact_id
            or raw_entry["artifact_type"] != spec.artifact_type
            or raw_entry["media_type"] != spec.media_type
        ):
            raise PerformanceArtifactIntegrityError(
                "Artifact entry metadata does not match the fixed layout."
            )
        size_bytes = raw_entry["size_bytes"]
        if (
            isinstance(size_bytes, bool)
            or not isinstance(size_bytes, int)
            or size_bytes < 0
            or size_bytes > MAX_ARTIFACT_BYTES
        ):
            raise PerformanceArtifactIntegrityError(
                "Artifact size metadata is invalid."
            )
        sha256 = raw_entry["sha256"]
        if type(sha256) is not str or not _SHA256.fullmatch(sha256):
            raise PerformanceArtifactIntegrityError(
                "Artifact SHA-256 metadata is invalid."
            )
        state = raw_entry["redaction_state"]
        allowed_states = set(_ALLOWED_REDACTION_STATES)
        if allow_not_applicable and path == REGISTRY_PATH:
            allowed_states.add(REDACTION_NOT_APPLICABLE)
        if state not in allowed_states:
            raise PerformanceArtifactIntegrityError(
                "Artifact redaction state is invalid."
            )
        seen_ids.add(artifact_id)
        entries[path] = dict(raw_entry)

    if list(entries) != list(expected_specs):
        raise PerformanceArtifactIntegrityError(
            "Artifact entries are not in the fixed canonical order."
        )
    return entries


__all__ = [
    "ARTIFACT_HASHES_SCHEMA_VERSION",
    "ARTIFACT_REGISTRY_SCHEMA_VERSION",
    "HASHES_PATH",
    "PerformanceArtifactConflictError",
    "PerformanceArtifactError",
    "PerformanceArtifactInputs",
    "PerformanceArtifactIntegrityError",
    "PerformanceArtifactValidationError",
    "REDACTION_CONTAINS_CUSTOMER_DATA",
    "REDACTION_NOT_ASSESSED",
    "REDACTION_REDACTED",
    "REDACTION_SYNTHETIC_NO_PII",
    "REGISTRY_PATH",
    "VerifiedPerformanceArtifacts",
    "persist_performance_artifacts",
    "read_and_verify_performance_artifacts",
    "read_performance_artifacts",
    "verify_performance_artifacts",
]
