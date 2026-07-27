"""Bundled, synthetic inputs for ExitSpec's deterministic demonstration.

Package resources are not guaranteed to be ordinary files. Callers therefore use
``support_agent_demo_paths`` as a context manager and keep all path consumers inside
that context. Wave-2 synthetic email resources use the separate
``support_agent_email_paths`` context manager. Both APIs work for normal wheels and
for importers that extract resources to a temporary directory.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from importlib.resources import as_file, files
from pathlib import Path, PurePosixPath
from types import MappingProxyType


_EMAIL_MANIFEST_FILENAME = "wave-2-acceptance-v1.json"
_EMAIL_MANIFEST_ID = "exitspec-wave-2-synthetic-rfc822-intake"
_EMAIL_MANIFEST_VERSION = "1.0.0"
_EMAIL_MANIFEST_SHA256 = (
    "7f236239d6ce450e074196da25241b6242dfa0134da514c255df81e14e22f466"
)
_EMAIL_FIXTURE_COUNT = 11
_EMAIL_FIXTURE_PATH_PREFIX = ("examples", "support-agent", "email")
_EMAIL_FIXTURE_SET_DOMAIN = b"exitspec-wave2-fixture-set-v1"
_EMAIL_RESOURCE_ERROR = (
    "ExitSpec's bundled Wave-2 email resources failed validation."
)


class SupportAgentEmailResourceError(RuntimeError):
    """The frozen bundled Wave-2 email resource set is unusable."""


def _invalid_email_resources() -> SupportAgentEmailResourceError:
    return SupportAgentEmailResourceError(_EMAIL_RESOURCE_ERROR)


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _valid_case_id(value: object) -> bool:
    if not isinstance(value, str) or not value or value != value.lower():
        return False
    return all(
        part and part.isascii() and part.isalnum() for part in value.split("-")
    )


@dataclass(frozen=True)
class SupportAgentDemoPaths:
    """Filesystem paths materialized for one support-agent demo operation."""

    root: Path
    discovery_pack: Path
    review_plan: Path
    contract_seed: Path
    frozen_contract: Path
    fixture: Path

    @classmethod
    def from_root(cls, root: Path) -> "SupportAgentDemoPaths":
        """Build and validate the fixed resource map below ``root``."""

        resolved = cls(
            root=root,
            discovery_pack=root / "authoring" / "discovery-pack-v1.json",
            review_plan=root / "authoring" / "review-plan-v1.json",
            contract_seed=root / "authoring" / "contract-seed-v1.json",
            frozen_contract=root
            / "contracts"
            / "tool-selection-v1.frozen.yaml",
            fixture=root / "fixtures" / "tool-selection-200.json",
        )
        missing = [
            str(path.relative_to(root))
            for path in (
                resolved.discovery_pack,
                resolved.review_plan,
                resolved.contract_seed,
                resolved.frozen_contract,
                resolved.fixture,
            )
            if not path.is_file()
        ]
        if missing:
            raise FileNotFoundError(
                "ExitSpec's bundled support-agent demo is incomplete: {0}".format(
                    ", ".join(missing)
                )
            )
        return resolved


@dataclass(frozen=True, init=False)
class SupportAgentEmailPaths:
    """Validated materialized paths for the frozen Wave-2 email fixture set."""

    root: Path
    manifest: Path
    fixtures: Mapping[str, Path]

    def __init__(self, *args: object, **kwargs: object) -> None:
        raise TypeError(
            "SupportAgentEmailPaths must be created through from_root()."
        )

    @classmethod
    def _create(
        cls,
        *,
        root: Path,
        manifest: Path,
        fixtures: Mapping[str, Path],
    ) -> "SupportAgentEmailPaths":
        instance = object.__new__(cls)
        object.__setattr__(instance, "root", root)
        object.__setattr__(instance, "manifest", manifest)
        object.__setattr__(
            instance,
            "fixtures",
            MappingProxyType(dict(fixtures)),
        )
        return instance

    @property
    def case_ids(self) -> tuple[str, ...]:
        """Return manifest-approved case IDs in deterministic lexical order."""

        return tuple(self.fixtures)

    def fixture_for(self, case_id: str) -> Path:
        """Return one approved fixture without accepting a caller-supplied path."""

        try:
            return self.fixtures[case_id]
        except (KeyError, TypeError):
            raise KeyError(
                "Wave-2 email case ID is not manifest-approved."
            ) from None

    @classmethod
    def from_root(cls, root: Path) -> "SupportAgentEmailPaths":
        """Build a fail-closed immutable resource map below ``root``."""

        try:
            return cls._validated_from_root(root)
        except SupportAgentEmailResourceError:
            raise
        except (AttributeError, KeyError, OSError, TypeError, UnicodeError, ValueError):
            raise _invalid_email_resources() from None

    @classmethod
    def _validated_from_root(cls, root: Path) -> "SupportAgentEmailPaths":
        if root.is_symlink():
            raise _invalid_email_resources()
        root = root.resolve(strict=True)
        if not root.is_dir():
            raise _invalid_email_resources()

        manifest_path = root / _EMAIL_MANIFEST_FILENAME
        if not manifest_path.is_file() or manifest_path.is_symlink():
            raise _invalid_email_resources()

        manifest_bytes = manifest_path.read_bytes()
        manifest = json.loads(manifest_bytes)
        if not isinstance(manifest, dict):
            raise _invalid_email_resources()
        if (
            manifest.get("manifest_id") != _EMAIL_MANIFEST_ID
            or manifest.get("manifest_version") != _EMAIL_MANIFEST_VERSION
            or manifest.get("status") != "FROZEN"
        ):
            raise _invalid_email_resources()

        fixture_set = manifest.get("fixture_set")
        if not isinstance(fixture_set, dict):
            raise _invalid_email_resources()
        fixture_records = fixture_set.get("fixtures")
        if not isinstance(fixture_records, list):
            raise _invalid_email_resources()
        if (
            fixture_set.get("synthetic_only") is not True
            or fixture_set.get("digest_algorithm") != "sha256"
            or fixture_set.get("case_count") != _EMAIL_FIXTURE_COUNT
            or len(fixture_records) != _EMAIL_FIXTURE_COUNT
        ):
            raise _invalid_email_resources()

        seen_case_ids: set[str] = set()
        seen_filenames: set[str] = set()
        fixture_paths: dict[str, Path] = {}
        digest_projection: list[dict[str, str]] = []
        fixture_identities: list[tuple[Path, str, int]] = []

        for record in fixture_records:
            if not isinstance(record, dict):
                raise _invalid_email_resources()
            case_id = record.get("case_id")
            manifest_relative_path = record.get("path")
            expected_sha256 = record.get("sha256")
            expected_raw_bytes = record.get("raw_bytes")
            if (
                not _valid_case_id(case_id)
                or not isinstance(manifest_relative_path, str)
                or not isinstance(expected_sha256, str)
                or len(expected_sha256) != 64
                or any(
                    character not in "0123456789abcdef"
                    for character in expected_sha256
                )
                or type(expected_raw_bytes) is not int
                or expected_raw_bytes < 0
            ):
                raise _invalid_email_resources()

            fixture_path = PurePosixPath(manifest_relative_path)
            fixture_filename = f"{case_id}.eml"
            if (
                fixture_path.is_absolute()
                or fixture_path.parts
                != (*_EMAIL_FIXTURE_PATH_PREFIX, fixture_filename)
                or any(part in {"", ".", ".."} for part in fixture_path.parts)
                or case_id in seen_case_ids
                or fixture_filename in seen_filenames
            ):
                raise _invalid_email_resources()

            seen_case_ids.add(case_id)
            seen_filenames.add(fixture_filename)
            materialized_path = root / fixture_filename
            fixture_paths[case_id] = materialized_path
            fixture_identities.append(
                (materialized_path, expected_sha256, expected_raw_bytes)
            )
            digest_projection.append(
                {
                    "case_id": case_id,
                    "path": manifest_relative_path,
                    "sha256": expected_sha256,
                }
            )

        entries = tuple(root.iterdir())
        expected_names = {_EMAIL_MANIFEST_FILENAME, *seen_filenames}
        actual_names = {entry.name for entry in entries}
        if (
            len(actual_names) != len(entries)
            or actual_names != expected_names
            or any(
                entry.is_symlink()
                or not entry.is_file()
                or entry.suffix not in {".json", ".eml"}
                for entry in entries
            )
        ):
            raise _invalid_email_resources()

        for fixture_path, expected_sha256, expected_raw_bytes in fixture_identities:
            payload = fixture_path.read_bytes()
            if (
                len(payload) != expected_raw_bytes
                or _sha256(payload) != expected_sha256
            ):
                raise _invalid_email_resources()

        ordered_projection = sorted(
            digest_projection, key=lambda item: item["case_id"]
        )
        fixture_set_sha256 = _sha256(
            _EMAIL_FIXTURE_SET_DOMAIN
            + b"\x00"
            + _canonical_json_bytes(ordered_projection)
        )
        if (
            fixture_set.get("set_digest_domain")
            != _EMAIL_FIXTURE_SET_DOMAIN.decode("ascii")
            or fixture_set.get("set_digest_order") != "ascending case_id"
            or fixture_set.get("set_sha256") != fixture_set_sha256
            or _sha256(manifest_bytes) != _EMAIL_MANIFEST_SHA256
        ):
            raise _invalid_email_resources()

        ordered_paths = dict(sorted(fixture_paths.items()))
        return cls._create(
            root=root,
            manifest=manifest_path,
            fixtures=ordered_paths,
        )


@contextmanager
def support_agent_demo_paths() -> Iterator[SupportAgentDemoPaths]:
    """Materialize every bundled support-agent input for the context lifetime."""

    resource_root = files(__package__).joinpath("support_agent")
    with as_file(resource_root) as materialized_root:
        yield SupportAgentDemoPaths.from_root(materialized_root)


@contextmanager
def support_agent_email_paths() -> Iterator[SupportAgentEmailPaths]:
    """Materialize and validate the frozen Wave-2 email resources."""

    resource_root = (
        files(__package__).joinpath("support_agent").joinpath("email")
    )
    with as_file(resource_root) as materialized_root:
        yield SupportAgentEmailPaths.from_root(materialized_root)


__all__ = [
    "SupportAgentDemoPaths",
    "SupportAgentEmailPaths",
    "SupportAgentEmailResourceError",
    "support_agent_demo_paths",
    "support_agent_email_paths",
]
