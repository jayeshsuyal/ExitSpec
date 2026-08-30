"""Bounded discovery for server-configured Inferdrome evidence roots.

Browser callers never provide filesystem paths.  The catalog scans only the
configured root or its direct children, verifies every published candidate,
and resolves an import through a server-issued opaque evidence reference.
The importer verifies the same bytes again before issuing any receipt.
"""

from __future__ import annotations

import os
import hashlib
import re
import secrets
import stat
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Final

from .inferdrome_bundle import (
    InferdromeBundleRejected,
    VerifiedInferdromeBundle,
    verify_inferdrome_bundle,
)

_RUN_ID: Final = re.compile(r"^run-[0-9a-f]{32}$")
_SAFE_ENTRY: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_TAGGED_SHA256: Final = re.compile(r"^sha256:[0-9a-f]{64}$")
_EVIDENCE_REF: Final = re.compile(r"^evref_[a-f0-9]{64}$")
MAX_DISCOVERED_ENTRIES: Final = 1_000


class InferdromeCatalogError(RuntimeError):
    """The configured catalog cannot be scanned safely."""


class InferdromeCatalogNotFound(InferdromeCatalogError, KeyError):
    """No currently verified bundle matches the requested identity."""


@dataclass(frozen=True, slots=True)
class InferdromeCatalogEntry:
    run_id: str
    bundle_digest: str
    model: str
    endpoint: str
    adapter: str
    adapter_version: str
    measured_requests: int
    concurrency: int


@dataclass(frozen=True, slots=True)
class InferdromeCatalogRejection:
    entry: str
    code: str


@dataclass(frozen=True, slots=True)
class InferdromeCatalogSnapshot:
    configured: bool
    entries: tuple[InferdromeCatalogEntry, ...]
    rejected: tuple[InferdromeCatalogRejection, ...]


@dataclass(frozen=True, slots=True)
class ResolvedInferdromeBundle:
    path: Path
    entry: InferdromeCatalogEntry


@dataclass(frozen=True, slots=True)
class _Candidate:
    label: str
    path: Path


class InferdromeBundleCatalog:
    """Read-only, fail-closed index over one explicit local runs root."""

    def __init__(self, runs_root: Path | None) -> None:
        if runs_root is not None and (
            not isinstance(runs_root, Path) or not runs_root.is_absolute()
        ):
            raise ValueError("Inferdrome runs root must be an absolute path.")
        self._runs_root = runs_root
        self._paths_by_identity: dict[tuple[str, str], Path] = {}
        self._identities_by_reference: dict[str, tuple[str, str]] = {}
        self._reference_secret = secrets.token_bytes(32)
        self._lock = RLock()

    @property
    def configured(self) -> bool:
        return self._runs_root is not None

    def refresh(self) -> InferdromeCatalogSnapshot:
        with self._lock:
            if self._runs_root is None:
                self._paths_by_identity = {}
                self._identities_by_reference = {}
                return InferdromeCatalogSnapshot(False, (), ())
            candidates, discovery_rejections = self._candidates(self._runs_root)
            accepted: dict[str, tuple[InferdromeCatalogEntry, _Candidate]] = {}
            duplicate_run_ids: set[str] = set()
            rejected = list(discovery_rejections)
            for candidate in candidates:
                try:
                    verified = verify_inferdrome_bundle(
                        candidate.path,
                        require_customer_eligible=True,
                    )
                    entry = _entry_from_verified(verified)
                except InferdromeBundleRejected as error:
                    rejected.append(
                        InferdromeCatalogRejection(
                            candidate.label,
                            error.code.value,
                        )
                    )
                    continue
                except (OSError, TypeError, ValueError):
                    rejected.append(
                        InferdromeCatalogRejection(
                            candidate.label,
                            "VERIFICATION_FAILED",
                        )
                    )
                    continue
                if entry.run_id in duplicate_run_ids:
                    rejected.append(
                        InferdromeCatalogRejection(
                            candidate.label,
                            "DUPLICATE_RUN_ID",
                        )
                    )
                    continue
                prior = accepted.pop(entry.run_id, None)
                if prior is not None:
                    duplicate_run_ids.add(entry.run_id)
                    rejected.extend(
                        (
                            InferdromeCatalogRejection(
                                prior[1].label,
                                "DUPLICATE_RUN_ID",
                            ),
                            InferdromeCatalogRejection(
                                candidate.label,
                                "DUPLICATE_RUN_ID",
                            ),
                        )
                    )
                    continue
                accepted[entry.run_id] = (entry, candidate)

            ordered = tuple(
                sorted(
                    (value[0] for value in accepted.values()),
                    key=lambda item: item.run_id,
                )
            )
            self._paths_by_identity = {
                (entry.run_id, entry.bundle_digest): accepted[entry.run_id][1].path
                for entry in ordered
            }
            self._identities_by_reference = {
                self._reference_for_identity(entry.run_id, entry.bundle_digest): (
                    entry.run_id,
                    entry.bundle_digest,
                )
                for entry in ordered
            }
            return InferdromeCatalogSnapshot(
                True,
                ordered,
                tuple(sorted(rejected, key=lambda item: (item.entry, item.code))),
            )

    def resolve(
        self,
        run_id: object,
        bundle_digest: object,
    ) -> ResolvedInferdromeBundle:
        if (
            type(run_id) is not str
            or _RUN_ID.fullmatch(run_id) is None
            or type(bundle_digest) is not str
            or _TAGGED_SHA256.fullmatch(bundle_digest) is None
        ):
            raise InferdromeCatalogNotFound("Inferdrome bundle was not found.")
        snapshot = self.refresh()
        path = self._paths_by_identity.get((run_id, bundle_digest))
        entry = next(
            (
                candidate
                for candidate in snapshot.entries
                if candidate.run_id == run_id
                and candidate.bundle_digest == bundle_digest
            ),
            None,
        )
        if path is None or entry is None:
            raise InferdromeCatalogNotFound("Inferdrome bundle was not found.")
        return ResolvedInferdromeBundle(path, entry)

    def evidence_reference(self, run_id: object, bundle_digest: object) -> str:
        """Return an opaque server-catalog reference for one verified entry."""

        resolved = self.resolve(run_id, bundle_digest)
        return self._reference_for_identity(
            resolved.entry.run_id,
            resolved.entry.bundle_digest,
        )

    def resolve_reference(self, reference: object) -> ResolvedInferdromeBundle:
        """Resolve only a server-issued opaque reference; never a caller path."""

        if type(reference) is not str or _EVIDENCE_REF.fullmatch(reference) is None:
            raise InferdromeCatalogNotFound("Inferdrome evidence reference was not found.")
        snapshot = self.refresh()
        with self._lock:
            identity = self._identities_by_reference.get(reference)
        if identity is None or not snapshot.configured:
            raise InferdromeCatalogNotFound("Inferdrome evidence reference was not found.")
        return self.resolve(*identity)

    def _reference_for_identity(self, run_id: str, bundle_digest: str) -> str:
        token = hashlib.sha256(
            b"exitspec-catalog-evidence-reference-v1\x00"
            + self._reference_secret
            + run_id.encode("ascii")
            + b"\x00"
            + bundle_digest.encode("ascii")
        ).hexdigest()
        return "evref_" + token

    @staticmethod
    def _candidates(
        runs_root: Path,
    ) -> tuple[
        tuple[_Candidate, ...],
        tuple[InferdromeCatalogRejection, ...],
    ]:
        if not runs_root.exists():
            return (), ()
        if not _directory_without_follow(runs_root):
            raise InferdromeCatalogError(
                "Inferdrome runs root must be a real directory."
            )
        if _file_without_follow(runs_root / "bundle.json"):
            return ((_Candidate(_safe_label(runs_root.name), runs_root),), ())
        try:
            with os.scandir(runs_root) as iterator:
                entries = []
                for entry in iterator:
                    if len(entries) >= MAX_DISCOVERED_ENTRIES:
                        raise InferdromeCatalogError(
                            "Inferdrome runs root exceeds the entry limit."
                        )
                    entries.append(entry)
                entries.sort(key=lambda item: item.name)
        except InferdromeCatalogError:
            raise
        except OSError as error:
            raise InferdromeCatalogError(
                "Inferdrome runs root could not be scanned."
            ) from error
        candidates: list[_Candidate] = []
        rejected: list[InferdromeCatalogRejection] = []
        for entry in entries:
            label = _safe_label(entry.name)
            try:
                metadata = entry.stat(follow_symlinks=False)
            except OSError:
                rejected.append(InferdromeCatalogRejection(label, "UNSAFE_ENTRY"))
                continue
            if stat.S_ISLNK(metadata.st_mode):
                rejected.append(InferdromeCatalogRejection(label, "UNSAFE_ENTRY"))
                continue
            if not stat.S_ISDIR(metadata.st_mode):
                continue
            child = Path(entry.path)
            if _file_without_follow(child / "bundle.json"):
                candidates.append(_Candidate(label, child))
                continue
            workspace_bundle = child / "bundle"
            if _directory_without_follow(workspace_bundle):
                candidates.append(_Candidate(label, workspace_bundle))
                continue
            if _RUN_ID.fullmatch(entry.name):
                rejected.append(
                    InferdromeCatalogRejection(label, "BUNDLE_UNAVAILABLE")
                )
        return tuple(candidates), tuple(rejected)


def _entry_from_verified(
    verified: VerifiedInferdromeBundle,
) -> InferdromeCatalogEntry:
    descriptor = verified.descriptor
    resolved = verified.resolved_spec
    target = resolved.get("target")
    execution = resolved.get("execution")
    traffic = resolved.get("traffic")
    if not all(isinstance(item, dict) for item in (target, execution, traffic)):
        raise ValueError("Verified Inferdrome projection is incomplete.")
    run_id = descriptor.get("run_id")
    fields = (
        run_id,
        target.get("model"),
        target.get("endpoint"),
        execution.get("adapter"),
        execution.get("adapter_version"),
    )
    if (
        any(type(value) is not str or not value for value in fields)
        or _RUN_ID.fullmatch(str(run_id)) is None
        or type(traffic.get("measured_requests")) is not int
        or type(traffic.get("concurrency")) is not int
    ):
        raise ValueError("Verified Inferdrome identity is incomplete.")
    return InferdromeCatalogEntry(
        run_id=str(run_id),
        bundle_digest=verified.bundle_digest,
        model=str(target["model"]),
        endpoint=str(target["endpoint"]),
        adapter=str(execution["adapter"]),
        adapter_version=str(execution["adapter_version"]),
        measured_requests=int(traffic["measured_requests"]),
        concurrency=int(traffic["concurrency"]),
    )


def _safe_label(value: str) -> str:
    return value if _SAFE_ENTRY.fullmatch(value) else "<unsafe-entry>"


def _directory_without_follow(path: Path) -> bool:
    try:
        metadata = os.lstat(path)
    except OSError:
        return False
    return stat.S_ISDIR(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode)


def _file_without_follow(path: Path) -> bool:
    try:
        metadata = os.lstat(path)
    except OSError:
        return False
    return stat.S_ISREG(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode)


__all__ = [
    "InferdromeBundleCatalog",
    "InferdromeCatalogEntry",
    "InferdromeCatalogError",
    "InferdromeCatalogNotFound",
    "InferdromeCatalogRejection",
    "InferdromeCatalogSnapshot",
    "ResolvedInferdromeBundle",
]
