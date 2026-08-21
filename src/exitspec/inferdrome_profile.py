"""Pinned, independently parsed Inferdrome managed-GPU capability documents.

The JSON documents in ``exitspec/profiles`` are vendored public producer
contracts.  This module deliberately imports no Inferdrome Python code.  It
verifies the producer's RFC 8785 document digests before exposing any value to
the bundle verifier.
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import json
from dataclasses import dataclass
from importlib import resources
from typing import Any, Final, NoReturn

from .canonical import CanonicalizationError, canonical_json_bytes


MANAGED_PROFILE_ID: Final = (
    "inferdrome.managed-vllm-0.26-evidence-profile.v1"
)
MANAGED_PROFILE_SHA256: Final = (
    "sha256:9d03b5d0822ed829ddbfa4c87c75530885b9ad51ee2c0cb7c5e31a075996fe34"
)
LOCAL_GPU_PROOF_SCHEMA_ID: Final = "urn:inferdrome:local-gpu-proof:v1"
LOCAL_GPU_PROOF_SCHEMA_SHA256: Final = (
    "sha256:cf83bbdea2bba4c30b8f0e2c5f34f34a4077501207881fdbdab021571d665547"
)
PUBLICATION_REVIEW_SHA256: Final = (
    "sha256:7f1b3be53695e9e3a2009eb28ce008bb2486ae882e52364e26bece770a6d33ff"
)
HANDOFF_MANIFEST_SHA256: Final = (
    "sha256:bc90ac7d0044b32556ce8e78181635f2a2d218e3de7a793062e5dc2b3d6cd4bd"
)
CAPABILITY_PROFILE_COMMIT: Final = (
    "53a5c55bdb146f29804c5490ce1a020d70f26bb4"
)
CAPTURE_PRODUCER_COMMIT: Final = (
    "c08b46d9fbd87477f45d130aa3c63615937c4dc3"
)
PINNED_ARCHIVE_SHA256: Final = (
    "sha256:f2408fd0649a7c79f5962872003781ebb9c878b802db27d633cf246f13b6f424"
)
PINNED_ARCHIVE_SIZE_BYTES: Final = 689_272
PINNED_CAPTURE_MANIFEST_SHA256: Final = (
    "sha256:1d4ea1e251c5a84a104333ab8579d580838701a70cc38b64b68c88f66266e0cb"
)
PINNED_BUNDLE_DIGEST: Final = (
    "sha256:bae216f2165eb06ae2e0f14d3cd852f8e0ebb381bf1f68c71072769b3c0c1675"
)
PINNED_BUNDLE_MEMBER_PATH: Final = (
    "capture/single/real-gpu-5osfyjjl/runs/"
    "run-533c9f5f783958fb6077069a6c577144/bundle"
)
PINNED_CORRUPTED_MEMBER_PATH: Final = (
    "capture/single/real-gpu-5osfyjjl/corrupted-bundle-copy"
)
PINNED_SYNTHETIC_MEMBER_PATH: Final = (
    "capture/single/real-gpu-5osfyjjl/synthetic-runs/"
    "run-5f8d7617421b9f4d0484f5807baa7849/bundle"
)
PINNED_RUN_ID: Final = "run-533c9f5f783958fb6077069a6c577144"
PINNED_NATIVE_TTFT_P95_NS: Final = 14_797_213

_PROFILE_FILES: Final = {
    "managed_profile": (
        "managed-vllm-0.26-evidence-profile.json",
        MANAGED_PROFILE_SHA256,
    ),
    "local_gpu_proof_schema": (
        "local-gpu-proof.schema.json",
        LOCAL_GPU_PROOF_SCHEMA_SHA256,
    ),
    "publication_review": (
        "a10-publication-review.json",
        PUBLICATION_REVIEW_SHA256,
    ),
    "handoff_manifest": (
        "a10-handoff-manifest.json",
        HANDOFF_MANIFEST_SHA256,
    ),
}
_MAX_PROFILE_DOCUMENT_BYTES: Final = 1_048_576


class InferdromeProfileError(ValueError):
    """A vendored producer capability document failed its consumer pin."""


@dataclass(frozen=True, slots=True)
class PinnedInferdromeProfileDocuments:
    """Detached copies of the four exact producer handoff documents."""

    managed_profile: dict[str, Any]
    local_gpu_proof_schema: dict[str, Any]
    publication_review: dict[str, Any]
    handoff_manifest: dict[str, Any]


def canonical_document_sha256(value: object) -> str:
    """Return ordinary SHA-256 over RFC 8785 canonical JSON bytes."""

    try:
        payload = canonical_json_bytes(value)
    except CanonicalizationError as error:
        raise InferdromeProfileError(
            "Inferdrome profile document cannot be canonicalized."
        ) from error
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def load_pinned_inferdrome_profile_documents() -> PinnedInferdromeProfileDocuments:
    """Load, digest-check, and cross-check the exact vendored handoff."""

    loaded: dict[str, dict[str, Any]] = {}
    root = resources.files("exitspec").joinpath(
        "profiles", "inferdrome", "v1"
    )
    for label, (filename, expected_sha256) in _PROFILE_FILES.items():
        try:
            payload = root.joinpath(filename).read_bytes()
        except (FileNotFoundError, OSError) as error:
            raise InferdromeProfileError(
                "Pinned Inferdrome profile resource is unavailable."
            ) from error
        if not payload or len(payload) > _MAX_PROFILE_DOCUMENT_BYTES:
            raise InferdromeProfileError(
                "Pinned Inferdrome profile resource exceeds its byte contract."
            )
        value = _parse_strict_object(payload, filename)
        actual_sha256 = canonical_document_sha256(value)
        if not hmac.compare_digest(actual_sha256, expected_sha256):
            raise InferdromeProfileError(
                "Pinned Inferdrome profile resource digest is invalid."
            )
        loaded[label] = value

    _require_exact_handoff(loaded)
    return PinnedInferdromeProfileDocuments(
        managed_profile=copy.deepcopy(loaded["managed_profile"]),
        local_gpu_proof_schema=copy.deepcopy(loaded["local_gpu_proof_schema"]),
        publication_review=copy.deepcopy(loaded["publication_review"]),
        handoff_manifest=copy.deepcopy(loaded["handoff_manifest"]),
    )


def _require_exact_handoff(documents: dict[str, dict[str, Any]]) -> None:
    profile = documents["managed_profile"]
    schema = documents["local_gpu_proof_schema"]
    review = documents["publication_review"]
    handoff = documents["handoff_manifest"]
    capability = _object(handoff.get("capability_profile"))
    managed = _object(capability.get("managed_profile"))
    local_schema = _object(capability.get("local_gpu_proof_schema"))
    archive = _object(handoff.get("archive"))
    run = _object(handoff.get("run"))
    ttft = _object(run.get("ttft"))
    history = _object(handoff.get("history_provenance"))
    review_link = _object(handoff.get("publication_review"))
    delivery = _object(handoff.get("fixture_delivery"))
    binding = _object(handoff.get("contract_binding"))
    acceptance = _object(handoff.get("acceptance_boundary"))

    if (
        profile.get("schema_version") != MANAGED_PROFILE_ID
        or profile.get("profile_id") != MANAGED_PROFILE_ID
        or schema.get("$id") != LOCAL_GPU_PROOF_SCHEMA_ID
        or capability.get("commit") != CAPABILITY_PROFILE_COMMIT
        or managed.get("sha256") != MANAGED_PROFILE_SHA256
        or local_schema.get("sha256") != LOCAL_GPU_PROOF_SCHEMA_SHA256
        or review_link.get("sha256") != PUBLICATION_REVIEW_SHA256
        or review.get("publication_status") != "EXTERNAL_ONLY"
        or archive.get("sha256") != PINNED_ARCHIVE_SHA256
        or archive.get("compressed_size_bytes") != PINNED_ARCHIVE_SIZE_BYTES
        or archive.get("capture_manifest_sha256")
        != PINNED_CAPTURE_MANIFEST_SHA256
        or archive.get("bundle_member_path") != PINNED_BUNDLE_MEMBER_PATH
        or run.get("run_id") != PINNED_RUN_ID
        or run.get("bundle_digest") != PINNED_BUNDLE_DIGEST
        or ttft.get("independently_expected_value")
        != PINNED_NATIVE_TTFT_P95_NS
        or ttft.get("definition_id") != "vllm_first_choices_event_v0_26"
        or history.get("capture_producer_commit") != CAPTURE_PRODUCER_COMMIT
        or delivery.get("publication_state")
        != "BLOCKED_PENDING_OWNER_APPROVAL"
        or binding.get("chronology") != "RETROSPECTIVE"
        or binding.get("producer_exitspec_contract_digest") is not None
        or binding.get("required_consumer_mode")
        != "EXTERNAL_RECEIPT_BINDING"
        or acceptance.get("inferdrome_acceptance_verdict") is not None
    ):
        raise InferdromeProfileError(
            "Pinned Inferdrome handoff semantic anchors are inconsistent."
        )


def _parse_strict_object(payload: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(
            payload,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_numeric_constant,
            parse_float=_reject_float,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise InferdromeProfileError(
            f"Pinned Inferdrome profile resource {label} is invalid JSON."
        ) from error
    if type(value) is not dict:
        raise InferdromeProfileError(
            "Pinned Inferdrome profile resource must be a JSON object."
        )
    return value


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON object key")
        value[key] = item
    return value


def _reject_numeric_constant(value: str) -> NoReturn:
    raise ValueError(f"unsupported numeric constant: {value}")


def _reject_float(value: str) -> NoReturn:
    raise ValueError(f"binary JSON number is forbidden: {value}")


def _object(value: object) -> dict[str, Any]:
    return value if type(value) is dict else {}


__all__ = [
    "CAPABILITY_PROFILE_COMMIT",
    "CAPTURE_PRODUCER_COMMIT",
    "HANDOFF_MANIFEST_SHA256",
    "InferdromeProfileError",
    "LOCAL_GPU_PROOF_SCHEMA_ID",
    "LOCAL_GPU_PROOF_SCHEMA_SHA256",
    "MANAGED_PROFILE_ID",
    "MANAGED_PROFILE_SHA256",
    "PINNED_ARCHIVE_SHA256",
    "PINNED_ARCHIVE_SIZE_BYTES",
    "PINNED_BUNDLE_DIGEST",
    "PINNED_BUNDLE_MEMBER_PATH",
    "PINNED_CAPTURE_MANIFEST_SHA256",
    "PINNED_CORRUPTED_MEMBER_PATH",
    "PINNED_NATIVE_TTFT_P95_NS",
    "PINNED_RUN_ID",
    "PINNED_SYNTHETIC_MEMBER_PATH",
    "PUBLICATION_REVIEW_SHA256",
    "PinnedInferdromeProfileDocuments",
    "canonical_document_sha256",
    "load_pinned_inferdrome_profile_documents",
]
