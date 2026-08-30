"""Completion-marked publication and verification for the generic A6 pack."""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Mapping

from .canonical import canonical_json_bytes


GENERIC_EVIDENCE_PACK_SCHEMA_VERSION = "exitspec.generic-evidence-pack.v1"
_ATTEMPT_ID = re.compile(r"^eatm_[a-f0-9]{32}$")
_ARTIFACTS = (
    "contract.json",
    "confirmation.json",
    "evidence.json",
    "decision-packet.html",
)
_MANIFEST = "artifact-hashes.json"
_COMPLETION_MARKER = ".complete"
_PACK_ENTRIES = frozenset((*_ARTIFACTS, _MANIFEST, _COMPLETION_MARKER))
_MAX_MANIFEST_BYTES = 64 * 1024
_MAX_JSON_ARTIFACT_BYTES = 2 * 1024 * 1024
_MAX_HTML_ARTIFACT_BYTES = 4 * 1024 * 1024


class GenericEvidencePackError(ValueError):
    """The generic Evidence Pack could not be safely published or verified."""


@dataclass(frozen=True, slots=True)
class GenericEvidencePackPublication:
    attempt_id: str
    evidence_pack_url: str
    evidence_pack_sha256: str
    manifest_sha256: str
    decision_packet_sha256: str


def publish_generic_evidence_pack(
    output_root: Path,
    attempt_id: str,
    payload: Mapping[str, object],
) -> GenericEvidencePackPublication:
    """Publish one immutable canonical pack with a completion marker.

    The final directory name is claimed exclusively and its completion marker
    is written last. Readers must verify the marker and exact entry set, so a
    process interrupted during publication is never treated as a pack.
    """

    root = _validated_root(output_root, create=True)
    _validate_attempt_id(attempt_id)
    if type(payload) is not dict:
        raise GenericEvidencePackError("Evidence Pack payload must be one object.")
    try:
        contract = payload["contract"]
        confirmation = payload["confirmation"]
    except KeyError as error:
        raise GenericEvidencePackError("Evidence Pack provenance is incomplete.") from error
    artifacts = {
        "contract.json": canonical_json_bytes(contract),
        "confirmation.json": canonical_json_bytes(confirmation),
        "evidence.json": canonical_json_bytes(dict(payload)),
        "decision-packet.html": _render_decision_packet(payload),
    }
    destination = root / attempt_id
    for relative, content in artifacts.items():
        limit = (
            _MAX_HTML_ARTIFACT_BYTES
            if relative == "decision-packet.html"
            else _MAX_JSON_ARTIFACT_BYTES
        )
        if len(content) > limit:
            raise GenericEvidencePackError("Evidence Pack artifact is too large.")
    temporary: Path | None = None
    claimed_destination = False
    manifest_bytes = b""
    try:
        temporary = Path(tempfile.mkdtemp(prefix=f".{attempt_id}.", dir=root))
        os.chmod(temporary, 0o700)
        for relative, content in artifacts.items():
            target = temporary / relative
            with target.open("xb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            target.chmod(0o600)
        manifest = {
            "schema_version": GENERIC_EVIDENCE_PACK_SCHEMA_VERSION,
            "attempt_id": attempt_id,
            "artifacts": {
                relative: hashlib.sha256(content).hexdigest()
                for relative, content in sorted(artifacts.items())
            },
        }
        manifest_bytes = canonical_json_bytes(manifest)
        manifest_path = temporary / _MANIFEST
        with manifest_path.open("xb") as handle:
            handle.write(manifest_bytes)
            handle.flush()
            os.fsync(handle.fileno())
        manifest_path.chmod(0o600)
        if len(manifest_bytes) > _MAX_MANIFEST_BYTES:
            raise GenericEvidencePackError("Evidence Pack manifest is too large.")
        _fsync_directory(temporary)
        # Claim the final name exclusively.  A rename guarded by lexists() can
        # replace an empty directory created by a racing publisher; mkdir is
        # the no-overwrite primitive for this directory-shaped artifact.
        try:
            destination.mkdir(mode=0o700)
        except FileExistsError as error:
            raise GenericEvidencePackError(
                "Evidence Pack already exists for this attempt."
            ) from error
        claimed_destination = True
        for relative in (*_ARTIFACTS, _MANIFEST):
            source = temporary / relative
            target = destination / relative
            try:
                os.link(source, target)
            except FileExistsError as error:
                raise GenericEvidencePackError(
                    "Evidence Pack destination collision detected."
                ) from error
            source.unlink()
            target.chmod(0o600)
        with (destination / _COMPLETION_MARKER).open("xb") as handle:
            handle.flush()
            os.fsync(handle.fileno())
        _fsync_directory(destination)
        shutil.rmtree(temporary)
        temporary = None
        _fsync_directory(root)
    except GenericEvidencePackError:
        raise
    except (OSError, TypeError, ValueError) as error:
        raise GenericEvidencePackError("Evidence Pack publication failed safely.") from error
    finally:
        if temporary is not None:
            shutil.rmtree(temporary, ignore_errors=True)
        if claimed_destination and temporary is not None:
            shutil.rmtree(destination, ignore_errors=True)
    return GenericEvidencePackPublication(
        attempt_id=attempt_id,
        evidence_pack_url=f"/artifacts/{attempt_id}/decision-packet.html",
        evidence_pack_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
        manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
        decision_packet_sha256=hashlib.sha256(
            artifacts["decision-packet.html"]
        ).hexdigest(),
    )


def verify_generic_evidence_pack(
    output_root: Path,
    attempt_id: str,
) -> GenericEvidencePackPublication:
    """Recheck the exact pack layout and all registered bytes."""

    root = _validated_root(output_root, create=False)
    _validate_attempt_id(attempt_id)
    pack_root = root / attempt_id
    try:
        if not pack_root.is_dir() or pack_root.is_symlink():
            raise GenericEvidencePackError("Evidence Pack directory is unavailable.")
        entries = {entry.name for entry in pack_root.iterdir()}
        if entries != _PACK_ENTRIES:
            raise GenericEvidencePackError("Evidence Pack directory entries are invalid.")
        if any(entry.is_symlink() or not entry.is_file() for entry in pack_root.iterdir()):
            raise GenericEvidencePackError("Evidence Pack contains an unsafe entry.")
        manifest_path = _safe_child(pack_root, _MANIFEST)
        if manifest_path is None:
            raise GenericEvidencePackError("Evidence Pack manifest path is unsafe.")
        manifest_bytes = _bounded_read(manifest_path, _MAX_MANIFEST_BYTES)
        manifest = _strict_json_loads(manifest_bytes)
        if (
            type(manifest) is not dict
            or set(manifest) != {"schema_version", "attempt_id", "artifacts"}
            or manifest["schema_version"] != GENERIC_EVIDENCE_PACK_SCHEMA_VERSION
            or manifest["attempt_id"] != attempt_id
            or type(manifest["artifacts"]) is not dict
            or set(manifest["artifacts"]) != set(_ARTIFACTS)
        ):
            raise GenericEvidencePackError("Evidence Pack manifest is invalid.")
        artifact_bytes: dict[str, bytes] = {}
        for relative, expected in manifest["artifacts"].items():
            target = _safe_child(pack_root, relative)
            limit = (
                _MAX_HTML_ARTIFACT_BYTES
                if relative == "decision-packet.html"
                else _MAX_JSON_ARTIFACT_BYTES
            )
            if (
                target is None
                or type(expected) is not str
                or not re.fullmatch(r"[a-f0-9]{64}", expected)
                or target.is_symlink()
                or not target.is_file()
            ):
                raise GenericEvidencePackError("Evidence Pack bytes failed verification.")
            content = _bounded_read(target, limit)
            if hashlib.sha256(content).hexdigest() != expected:
                raise GenericEvidencePackError("Evidence Pack bytes failed verification.")
            artifact_bytes[relative] = content
        evidence_payload = _strict_json_loads(
            artifact_bytes["evidence.json"]
        )
        contract_payload = _strict_json_loads(
            artifact_bytes["contract.json"]
        )
        confirmation_payload = _strict_json_loads(
            artifact_bytes["confirmation.json"]
        )
        if (
            type(evidence_payload) is not dict
            or evidence_payload.get("contract") != contract_payload
            or evidence_payload.get("confirmation") != confirmation_payload
            or canonical_json_bytes(evidence_payload) != artifact_bytes["evidence.json"]
            or canonical_json_bytes(contract_payload) != artifact_bytes["contract.json"]
            or canonical_json_bytes(confirmation_payload)
            != artifact_bytes["confirmation.json"]
        ):
            raise GenericEvidencePackError("Evidence Pack JSON is not canonical or bound.")
    except GenericEvidencePackError:
        raise
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise GenericEvidencePackError("Evidence Pack verification failed safely.") from error
    return GenericEvidencePackPublication(
        attempt_id=attempt_id,
        evidence_pack_url=f"/artifacts/{attempt_id}/decision-packet.html",
        evidence_pack_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
        manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
        decision_packet_sha256=hashlib.sha256(
            artifact_bytes["decision-packet.html"]
        ).hexdigest(),
    )


def _render_decision_packet(payload: Mapping[str, object]) -> bytes:
    status = html.escape(str(payload.get("status", "UNKNOWN")))
    verdict = html.escape(str(payload.get("overall_verdict", "NOT_PROVEN")))
    reason = html.escape(str(payload.get("reason", "")))
    next_action = html.escape(str(payload.get("next_action", "")))
    customer = html.escape(str(payload.get("customer", "")))
    use_case = html.escape(str(payload.get("use_case", "")))
    serialized = html.escape(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    )
    document = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>ExitSpec Evidence Pack</title>
  <style>
    :root {{ color-scheme: dark; font-family: Inter, ui-sans-serif, sans-serif; color: #f4f1e9; background: #0e141b; }}
    body {{ margin: 0; }}
    main {{ width: min(960px, calc(100% - 36px)); margin: 0 auto; padding: 24px 0 60px; }}
    .bar {{ border-bottom: 1px solid #303a40; padding-bottom: 14px; color: #9ba5a8; letter-spacing: .08em; text-transform: uppercase; font-size: .72rem; }}
    .sheet {{ margin-top: 18px; border: 1px solid #303a40; border-top: 3px solid #ff8b43; border-radius: 14px; background: #18222d; padding: 22px; }}
    h1 {{ margin: 0; color: #ff8b43; font-size: clamp(2.2rem, 6vw, 4.4rem); letter-spacing: -.06em; }}
    h2 {{ margin: 26px 0 8px; font-size: 1rem; color: #ffb17b; }}
    p {{ color: #c7ced0; line-height: 1.5; }}
    code, pre {{ font-family: ui-monospace, SFMono-Regular, monospace; overflow-wrap: anywhere; }}
    pre {{ max-height: 420px; overflow: auto; border: 1px solid #303a40; border-radius: 9px; background: #101619; padding: 14px; color: #d8d6ce; white-space: pre-wrap; }}
    .non-auth {{ border-left: 3px solid #ff8b43; padding-left: 12px; }}
  </style>
</head>
<body><main>
  <div class="bar">ExitSpec / customer evidence pack / {status}</div>
  <section class="sheet">
    <h1>{verdict}</h1>
    <p><strong>Customer:</strong> {customer}<br><strong>Use case:</strong> {use_case}</p>
    <p>{reason}</p>
    <h2>Next action</h2><p>{next_action}</p>
    <h2>Evidence detail</h2><pre>{serialized}</pre>
    <p class="non-auth">PASS is evidence about the approved criterion only. It does not authorize deployment, spending, procurement, production traffic, shipping, or any other external action.</p>
  </section>
</main></body></html>
"""
    return document.encode("utf-8")


def _validated_root(value: Path, *, create: bool) -> Path:
    if not isinstance(value, Path) or not value.is_absolute():
        raise GenericEvidencePackError("Evidence Pack output root must be absolute.")
    if create:
        value.mkdir(parents=True, exist_ok=True)
    elif not value.exists():
        raise GenericEvidencePackError("Evidence Pack output root is unavailable.")
    if value.is_symlink() or not value.is_dir():
        raise GenericEvidencePackError("Evidence Pack output root is unsafe.")
    return value


def _validate_attempt_id(value: object) -> None:
    if type(value) is not str or _ATTEMPT_ID.fullmatch(value) is None:
        raise GenericEvidencePackError("Evidence Pack attempt identity is invalid.")


def _safe_child(root: Path, relative: object) -> Path | None:
    if type(relative) is not str:
        return None
    logical = PurePosixPath(relative)
    if logical.is_absolute() or "\\" in relative or any(
        part in {"", ".", ".."} for part in logical.parts
    ):
        return None
    return root.joinpath(*logical.parts)


def _bounded_read(path: Path, limit: int) -> bytes:
    try:
        stat = path.lstat()
        if not path.is_file() or path.is_symlink() or stat.st_size > limit:
            raise GenericEvidencePackError("Evidence Pack artifact is unsafe or too large.")
        content = path.read_bytes()
    except GenericEvidencePackError:
        raise
    except (OSError, ValueError) as error:
        raise GenericEvidencePackError("Evidence Pack artifact cannot be read safely.") from error
    if len(content) > limit:
        raise GenericEvidencePackError("Evidence Pack artifact is too large.")
    return content


def _strict_json_loads(content: bytes) -> object:
    def reject_duplicate_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise GenericEvidencePackError("Evidence Pack JSON has duplicate keys.")
            result[key] = value
        return result

    try:
        return json.loads(content, object_pairs_hook=reject_duplicate_keys)
    except GenericEvidencePackError:
        raise
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise GenericEvidencePackError("Evidence Pack JSON is invalid.") from error


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


__all__ = [
    "GENERIC_EVIDENCE_PACK_SCHEMA_VERSION",
    "GenericEvidencePackError",
    "GenericEvidencePackPublication",
    "publish_generic_evidence_pack",
    "verify_generic_evidence_pack",
]
