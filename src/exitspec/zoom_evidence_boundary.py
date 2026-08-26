"""Versioned separation of Zoom setup attestations and runtime evidence.

This module contains only content-free, operator-authored evidence contracts.
It never reads or decodes Zoom packets and it never grants transport, mapper,
source, contract, measurement, or verdict authority.

The original ``zoom-golden-capture-plan.v1`` contract remains owned by
``zoom_fixture_capture``.  It is a legacy private-custody format with its
original twelve-role inventory.  This module does not reinterpret that format;
it defines the explicit setup/runtime boundary used by new work.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
import hashlib
import hmac
import json
from pathlib import Path
import re
from typing import Any, Final, Literal

from pydantic import ConfigDict, Field, StrictInt, field_validator, model_validator

from .canonical import canonical_json_bytes
from .models import FrozenExitSpecModel, SHA256_PATTERN


ZOOM_LEGACY_CAPTURE_PLAN_VERSION = "exitspec.zoom-golden-capture-plan.v1"
ZOOM_SETUP_ATTESTATION_VERSION = "exitspec.zoom-rtms-setup-attestation.v1"
ZOOM_RUNTIME_EVIDENCE_PLAN_VERSION = "exitspec.zoom-rtms-runtime-evidence.v1"
ZOOM_SETUP_ATTESTATION_AUTHORITY = "ZOOM_APP_SETUP_ATTESTATION_ONLY"
ZOOM_RUNTIME_EVIDENCE_AUTHORITY = "PRIVATE_SYNTHETIC_RUNTIME_EVIDENCE_ONLY"

ZOOM_SETUP_ARTIFACTS: Final[tuple[tuple[str, str], ...]] = (
    ("app_configuration_snapshot", "app-configuration-snapshot.bin"),
    ("endpoint_validation_request", "endpoint-validation-request.bin"),
    ("endpoint_validation_response", "endpoint-validation-response.bin"),
)
ZOOM_RUNTIME_ARTIFACTS: Final[tuple[tuple[str, str], ...]] = (
    ("rtms_started_webhook", "rtms-started-webhook.bin"),
    ("rtms_stopped_webhook", "rtms-stopped-webhook.bin"),
    ("signaling_websocket_handshake", "signaling-websocket-handshake.bin"),
    ("transcript_websocket_handshake", "transcript-websocket-handshake.bin"),
    ("participant_lifecycle_events", "participant-lifecycle-events.bin"),
    ("transcript_packets", "transcript-packets.bin"),
    ("disconnect_reconnect_trace", "disconnect-reconnect-trace.bin"),
    ("duplicate_delivery_trace", "duplicate-delivery-trace.bin"),
    ("timestamp_observations", "timestamp-observations.bin"),
)

_REQUIRED_SCOPES: Final = (
    "meeting:read:meeting_audio",
    "meeting:read:meeting_transcript",
    "meeting:update:participant_rtms_app_status",
)
_PROVIDER_ENFORCED_PREREQUISITE_SCOPES: Final = ("meeting:read:meeting_audio",)
_MAX_ARTIFACT_BYTES = 16 * 1024 * 1024
_MAX_CAPTURE_BYTES = 64 * 1024 * 1024
_MAX_WINDOW_MINUTES = 30
_MAX_JSON_BYTES = 64 * 1024
_SETUP_DOMAIN = b"exitspec-zoom-setup-attestation-v1\x00"
_RUNTIME_DOMAIN = b"exitspec-zoom-runtime-evidence-v1\x00"
_SECRET_OR_ENDPOINT_PATTERN = re.compile(
    r"(?i)(?:\b(?:bearer|basic)\s+[a-z0-9._~+/=-]+|https?://|"
    r"zoommtg:|\b(?:fw|sk|gho|github_pat|xox[baprs])_[a-z0-9_-]{8,}|"
    r"\beyJ[a-z0-9_-]{8,}\.[a-z0-9_-]{8,})"
)


class ZoomEvidenceBoundaryError(RuntimeError):
    """Sanitized refusal that never includes a path or submitted value."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__("The Zoom evidence boundary rejected the submitted contract.")


class _BoundaryModel(FrozenExitSpecModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
        validate_default=True,
    )


class ZoomSetupArtifactRecord(_BoundaryModel):
    """Digest-only record for one private setup artifact."""

    role: str = Field(pattern=r"^[a-z][a-z0-9_]{2,95}$")
    byte_count: StrictInt = Field(gt=0, le=_MAX_ARTIFACT_BYTES)
    sha256: str = Field(pattern=SHA256_PATTERN)


class ZoomSetupAttestation(_BoundaryModel):
    """One-time app/endpoint attestation with no meeting identity."""

    schema_version: Literal[ZOOM_SETUP_ATTESTATION_VERSION] = (
        ZOOM_SETUP_ATTESTATION_VERSION
    )
    attestation_id: str = Field(pattern=r"^zoomsetup_[a-f0-9]{64}$")
    attested_at: datetime
    provider: Literal["ZOOM_RTMS"] = "ZOOM_RTMS"
    app_type: Literal["GENERAL_APP"] = "GENERAL_APP"
    app_configuration_status: Literal["VALIDATED"] = "VALIDATED"
    endpoint_configuration_status: Literal["VALIDATED"] = "VALIDATED"
    crc_validation_status: Literal["VALIDATED"] = "VALIDATED"
    required_scopes: tuple[str, ...]
    provider_enforced_prerequisite_scopes: tuple[str, ...]
    credential_rotation_status: Literal["ROTATED_OR_DISABLED_OUTSIDE_REPO"] = (
        "ROTATED_OR_DISABLED_OUTSIDE_REPO"
    )
    credential_rotation_receipt_id: str = Field(
        pattern=r"^zoomcredrot_[a-f0-9]{64}$"
    )
    artifacts: tuple[ZoomSetupArtifactRecord, ...]
    total_bytes: StrictInt = Field(gt=0, le=_MAX_CAPTURE_BYTES)
    digest_algorithm: Literal["sha256"] = "sha256"
    setup_artifacts_remain_private: Literal[True] = True
    raw_setup_artifacts_parsed_by_this_contract: Literal[False] = False
    may_authorize_runtime_capture: Literal[False] = False
    may_call_zoom: Literal[False] = False
    may_publish_fixture: Literal[False] = False
    may_define_mapper: Literal[False] = False
    may_confirm_contract: Literal[False] = False
    may_freeze_contract: Literal[False] = False
    may_start_measurement: Literal[False] = False
    may_assign_verdict: Literal[False] = False
    authority: Literal[ZOOM_SETUP_ATTESTATION_AUTHORITY] = (
        ZOOM_SETUP_ATTESTATION_AUTHORITY
    )

    @field_validator("attested_at")
    @classmethod
    def normalize_attested_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("attested_at must be timezone-aware.")
        return value.astimezone(timezone.utc)

    @field_validator("required_scopes")
    @classmethod
    def require_exact_scopes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if value != _REQUIRED_SCOPES:
            raise ValueError("required_scopes must match the reviewed scope set.")
        return value

    @field_validator("provider_enforced_prerequisite_scopes")
    @classmethod
    def require_exact_prerequisite_scopes(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        if value != _PROVIDER_ENFORCED_PREREQUISITE_SCOPES:
            raise ValueError("provider prerequisite scopes drifted.")
        return value

    @model_validator(mode="after")
    def validate_attestation(self) -> "ZoomSetupAttestation":
        if tuple(record.role for record in self.artifacts) != tuple(
            role for role, _ in ZOOM_SETUP_ARTIFACTS
        ):
            raise ValueError("setup artifact inventory is invalid.")
        if len({record.role for record in self.artifacts}) != len(self.artifacts):
            raise ValueError("setup artifact inventory contains duplicates.")
        if sum(record.byte_count for record in self.artifacts) != self.total_bytes:
            raise ValueError("setup artifact total is invalid.")
        expected = _digest_identifier(
            "zoomsetup_",
            _SETUP_DOMAIN,
            _model_payload_without(self, "attestation_id"),
        )
        if not hmac.compare_digest(self.attestation_id, expected):
            raise ValueError("setup attestation identity is invalid.")
        return self


class ZoomRuntimeEvidencePlan(_BoundaryModel):
    """Per-meeting runtime inventory bound to one setup attestation."""

    schema_version: Literal[ZOOM_RUNTIME_EVIDENCE_PLAN_VERSION] = (
        ZOOM_RUNTIME_EVIDENCE_PLAN_VERSION
    )
    plan_id: str = Field(pattern=r"^zoomruntime_[a-f0-9]{64}$")
    capture_id: str = Field(pattern=r"^zoomcap_[a-z0-9][a-z0-9_-]{2,95}$")
    setup_attestation_id: str = Field(pattern=r"^zoomsetup_[a-f0-9]{64}$")
    setup_attestation_sha256: str = Field(pattern=SHA256_PATTERN)
    capture_window_start_at: datetime
    capture_window_end_at: datetime
    retention_hours: StrictInt = Field(gt=0, le=24)
    runtime_artifact_roles: tuple[str, ...]
    setup_artifacts_embedded: Literal[False] = False
    raw_runtime_evidence_remains_private: Literal[True] = True
    runtime_evidence_parsed_by_this_contract: Literal[False] = False
    may_publish_fixture: Literal[False] = False
    may_define_mapper: Literal[False] = False
    may_confirm_contract: Literal[False] = False
    may_freeze_contract: Literal[False] = False
    may_start_measurement: Literal[False] = False
    may_assign_verdict: Literal[False] = False
    authority: Literal[ZOOM_RUNTIME_EVIDENCE_AUTHORITY] = (
        ZOOM_RUNTIME_EVIDENCE_AUTHORITY
    )

    @field_validator("capture_window_start_at", "capture_window_end_at")
    @classmethod
    def normalize_window_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("capture window timestamps must be timezone-aware.")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def validate_runtime_plan(self) -> "ZoomRuntimeEvidencePlan":
        duration = (
            self.capture_window_end_at - self.capture_window_start_at
        ).total_seconds()
        if duration <= 0 or duration > _MAX_WINDOW_MINUTES * 60:
            raise ValueError("capture window is outside the bounded runtime limit.")
        expected_roles = tuple(role for role, _ in ZOOM_RUNTIME_ARTIFACTS)
        if self.runtime_artifact_roles != expected_roles:
            raise ValueError("runtime artifact inventory is invalid.")
        expected = _digest_identifier(
            "zoomruntime_",
            _RUNTIME_DOMAIN,
            _model_payload_without(self, "plan_id"),
        )
        if not hmac.compare_digest(self.plan_id, expected):
            raise ValueError("runtime evidence plan identity is invalid.")
        return self


def setup_attestation_sha256(attestation: ZoomSetupAttestation) -> str:
    """Return the digest bound into a runtime plan."""

    return hashlib.sha256(
        canonical_json_bytes(attestation.model_dump(mode="json"))
    ).hexdigest()


def verify_runtime_setup_binding(
    plan: ZoomRuntimeEvidencePlan,
    attestation: ZoomSetupAttestation,
) -> None:
    """Require an exact setup identity and digest before runtime use."""

    if (
        plan.setup_attestation_id != attestation.attestation_id
        or not hmac.compare_digest(
            plan.setup_attestation_sha256,
            setup_attestation_sha256(attestation),
        )
    ):
        raise ZoomEvidenceBoundaryError("ZOOM_SETUP_ATTESTATION_MISMATCH")


def validate_rotation_receipt_id(value: str) -> bool:
    """Accept only the content-free receipt shape used by the operator gate."""

    return bool(re.fullmatch(r"zoomcredrot_[a-f0-9]{64}", value))


def classify_zoom_schema(schema_version: str) -> str:
    """Classify versions explicitly; never silently migrate legacy captures."""

    if schema_version == ZOOM_LEGACY_CAPTURE_PLAN_VERSION:
        return "LEGACY_V1_PRIVATE_CUSTODY_ONLY"
    if schema_version == ZOOM_RUNTIME_EVIDENCE_PLAN_VERSION:
        return "SETUP_BOUND_RUNTIME_EVIDENCE_V1"
    if schema_version == ZOOM_SETUP_ATTESTATION_VERSION:
        return "ONE_TIME_SETUP_ATTESTATION_V1"
    raise ZoomEvidenceBoundaryError("ZOOM_UNSUPPORTED_EVIDENCE_SCHEMA")


def load_zoom_setup_attestation(path: Path) -> ZoomSetupAttestation:
    """Load a bounded, content-free setup attestation."""

    payload = _load_bounded_json(path, "ZOOM_SETUP_ATTESTATION_REJECTED")
    try:
        return ZoomSetupAttestation.model_validate(payload)
    except Exception as exc:
        raise ZoomEvidenceBoundaryError("ZOOM_SETUP_ATTESTATION_REJECTED") from exc


def load_zoom_runtime_evidence_plan(path: Path) -> ZoomRuntimeEvidencePlan:
    """Load a bounded runtime plan without opening any artifact bytes."""

    payload = _load_bounded_json(path, "ZOOM_RUNTIME_PLAN_REJECTED")
    try:
        return ZoomRuntimeEvidencePlan.model_validate(payload)
    except Exception as exc:
        raise ZoomEvidenceBoundaryError("ZOOM_RUNTIME_PLAN_REJECTED") from exc


def _model_payload_without(model: _BoundaryModel, field: str) -> dict[str, Any]:
    payload = model.model_dump(mode="json")
    payload.pop(field, None)
    return payload


def _digest_identifier(prefix: str, domain: bytes, payload: Mapping[str, object]) -> str:
    digest = hashlib.sha256(domain + canonical_json_bytes(payload)).hexdigest()
    return prefix + digest


def _load_bounded_json(path: Path, code: str) -> dict[str, object]:
    try:
        candidate = Path(path)
        if candidate.is_symlink() or not candidate.is_file():
            raise ValueError
        raw = candidate.read_bytes()
        if len(raw) > _MAX_JSON_BYTES:
            raise ValueError
        payload = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_json_constant,
        )
        if not isinstance(payload, dict):
            raise ValueError
        _reject_secret_or_endpoint_values(payload)
        return payload
    except ZoomEvidenceBoundaryError:
        raise
    except Exception as exc:
        raise ZoomEvidenceBoundaryError(code) from exc


def _reject_duplicate_keys(pairs: Sequence[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    del value
    raise ValueError("non-finite JSON value")


def _reject_secret_or_endpoint_values(value: object) -> None:
    if isinstance(value, str) and _SECRET_OR_ENDPOINT_PATTERN.search(value):
        raise ValueError("secret-like or endpoint value")
    if isinstance(value, Mapping):
        for child in value.values():
            _reject_secret_or_endpoint_values(child)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for child in value:
            _reject_secret_or_endpoint_values(child)


__all__ = [
    "ZOOM_LEGACY_CAPTURE_PLAN_VERSION",
    "ZOOM_RUNTIME_EVIDENCE_AUTHORITY",
    "ZOOM_RUNTIME_EVIDENCE_PLAN_VERSION",
    "ZOOM_RUNTIME_ARTIFACTS",
    "ZOOM_SETUP_ARTIFACTS",
    "ZOOM_SETUP_ATTESTATION_AUTHORITY",
    "ZOOM_SETUP_ATTESTATION_VERSION",
    "ZoomEvidenceBoundaryError",
    "ZoomRuntimeEvidencePlan",
    "ZoomSetupArtifactRecord",
    "ZoomSetupAttestation",
    "classify_zoom_schema",
    "load_zoom_runtime_evidence_plan",
    "load_zoom_setup_attestation",
    "setup_attestation_sha256",
    "validate_rotation_receipt_id",
    "verify_runtime_setup_binding",
]
