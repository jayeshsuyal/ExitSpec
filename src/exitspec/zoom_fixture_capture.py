"""Private, opaque-byte custody for the first synthetic Zoom golden fixture.

This module is deliberately outside the live meeting connector.  It validates
an operator-authored synthetic capture plan, creates a git-ignored private
workspace, seals a fixed inventory of opaque artifacts, and records a privacy
review.  It does not call Zoom, parse a Zoom payload, sanitize captured bytes,
freeze a wire schema, or grant any ExitSpec decision authority.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from enum import Enum
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import stat
import sys
from typing import Any, Final, Literal

from pydantic import Field, StrictInt, field_validator, model_validator

from .canonical import canonical_json_bytes
from .models import FrozenExitSpecModel, SHA256_PATTERN


ZOOM_CAPTURE_PLAN_VERSION = "exitspec.zoom-golden-capture-plan.v1"
ZOOM_PREFLIGHT_RECEIPT_VERSION = "exitspec.zoom-golden-preflight-receipt.v1"
ZOOM_CUSTODY_MANIFEST_VERSION = "exitspec.zoom-golden-custody-manifest.v1"
ZOOM_PRIVACY_REVIEW_VERSION = "exitspec.zoom-golden-privacy-review.v1"
ZOOM_PRIVACY_REVIEW_RECEIPT_VERSION = "exitspec.zoom-golden-privacy-review-receipt.v1"

ZOOM_CAPTURE_AUTHORITY = "PRIVATE_SYNTHETIC_FIXTURE_CUSTODY_ONLY"
PRIVATE_CAPTURE_ROOT = ".zoom-fixture-private"
PRIVATE_CAPTURE_IGNORE_RULE = ".zoom-fixture-private/"

_PREFLIGHT_DOMAIN = b"exitspec-zoom-golden-preflight-v1\x00"
_MANIFEST_DOMAIN = b"exitspec-zoom-golden-custody-manifest-v1\x00"
_REVIEW_DOMAIN = b"exitspec-zoom-golden-privacy-review-v1\x00"
_REVIEW_RECEIPT_DOMAIN = b"exitspec-zoom-golden-review-receipt-v1\x00"

_MAX_PLAN_BYTES = 64 * 1024
_MAX_REVIEW_BYTES = 64 * 1024
_MAX_CONTROL_FILE_BYTES = 512 * 1024
_MAX_ARTIFACT_BYTES = 16 * 1024 * 1024
_MAX_CAPTURE_BYTES = 64 * 1024 * 1024
_MAX_CAPTURE_MINUTES = 30
_MAX_RETENTION_HOURS = 24

_CAPTURE_ID_PATTERN = re.compile(r"^zoomcap_[a-z0-9][a-z0-9_-]{2,95}$")
_SECRET_VALUE_PATTERN = re.compile(
    r"(?i)(?:"
    r"\bbearer\s+[a-z0-9._~+/=-]+|"
    r"\bbasic\s+[a-z0-9+/=]+|"
    r"https?://|zoommtg:|"
    r"\b(?:fw|sk|gho|github_pat|xox[baprs])_[a-z0-9_-]{8,}|"
    r"\beyJ[a-z0-9_-]{8,}\.[a-z0-9_-]{8,}"
    r")"
)

_REQUIRED_SCOPES: Final = (
    "meeting:read:meeting_transcript",
    "meeting:update:participant_rtms_app_status",
)
_REQUESTED_MEDIA: Final = ("transcript",)
_EXCLUDED_MEDIA: Final = ("audio", "video", "screen_share", "chat")

# Every role maps to a fixed private filename.  The bytes remain uninterpreted.
ZOOM_CAPTURE_ARTIFACTS: Final[tuple[tuple[str, str], ...]] = (
    ("app_configuration_snapshot", "app-configuration-snapshot.bin"),
    ("endpoint_validation_request", "endpoint-validation-request.bin"),
    ("endpoint_validation_response", "endpoint-validation-response.bin"),
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
_ARTIFACT_FILENAME_BY_ROLE: Final = dict(ZOOM_CAPTURE_ARTIFACTS)
_ARTIFACT_ROLE_BY_FILENAME: Final = {
    filename: role for role, filename in ZOOM_CAPTURE_ARTIFACTS
}


class ZoomFixtureCaptureFailureCode(str, Enum):
    """Stable, content-free refusal codes for the operator utility."""

    PLAN_REJECTED = "ZOOM_FIXTURE_PLAN_REJECTED"
    REPOSITORY_NOT_READY = "ZOOM_FIXTURE_REPOSITORY_NOT_READY"
    WORKSPACE_UNSAFE = "ZOOM_FIXTURE_WORKSPACE_UNSAFE"
    WORKSPACE_CONFLICT = "ZOOM_FIXTURE_WORKSPACE_CONFLICT"
    CAPTURE_INCOMPLETE = "ZOOM_FIXTURE_CAPTURE_INCOMPLETE"
    CAPTURE_INTEGRITY_FAILED = "ZOOM_FIXTURE_CAPTURE_INTEGRITY_FAILED"
    PRIVACY_REVIEW_REJECTED = "ZOOM_FIXTURE_PRIVACY_REVIEW_REJECTED"
    PRIVACY_REVIEW_CONFLICT = "ZOOM_FIXTURE_PRIVACY_REVIEW_CONFLICT"
    LOCAL_IO_FAILED = "ZOOM_FIXTURE_LOCAL_IO_FAILED"


_FAILURE_MESSAGES: Final = {
    ZoomFixtureCaptureFailureCode.PLAN_REJECTED: (
        "The synthetic Zoom capture plan was rejected."
    ),
    ZoomFixtureCaptureFailureCode.REPOSITORY_NOT_READY: (
        "The repository is not ready for private Zoom fixture capture."
    ),
    ZoomFixtureCaptureFailureCode.WORKSPACE_UNSAFE: (
        "The private Zoom capture workspace is unsafe."
    ),
    ZoomFixtureCaptureFailureCode.WORKSPACE_CONFLICT: (
        "The private Zoom capture workspace conflicts with existing state."
    ),
    ZoomFixtureCaptureFailureCode.CAPTURE_INCOMPLETE: (
        "The private Zoom capture does not contain the exact required inventory."
    ),
    ZoomFixtureCaptureFailureCode.CAPTURE_INTEGRITY_FAILED: (
        "The sealed Zoom capture failed independent integrity verification."
    ),
    ZoomFixtureCaptureFailureCode.PRIVACY_REVIEW_REJECTED: (
        "The Zoom fixture privacy review was rejected."
    ),
    ZoomFixtureCaptureFailureCode.PRIVACY_REVIEW_CONFLICT: (
        "The Zoom fixture privacy review conflicts with existing state."
    ),
    ZoomFixtureCaptureFailureCode.LOCAL_IO_FAILED: (
        "The private Zoom capture operation could not complete safely."
    ),
}


class ZoomFixtureCaptureError(RuntimeError):
    """Sanitized local refusal that never includes paths or captured values."""

    retryable = False

    def __init__(self, failure_code: ZoomFixtureCaptureFailureCode) -> None:
        self.failure_code = ZoomFixtureCaptureFailureCode(failure_code)
        self.code = self.failure_code.value
        super().__init__(_FAILURE_MESSAGES[self.failure_code])


class ZoomSyntheticParticipant(FrozenExitSpecModel):
    """One explicitly synthetic, consenting participant in the capture."""

    label: str = Field(pattern=r"^synthetic_[a-z0-9][a-z0-9_-]{2,55}$")
    role: Literal["HOST", "GUEST"]
    consent_recorded: Literal[True] = True
    consented_at: datetime
    disclosure_sha256: str = Field(pattern=SHA256_PATTERN)

    @field_validator("consented_at")
    @classmethod
    def normalize_consented_at(cls, value: datetime) -> datetime:
        return _utc_datetime(value, "consented_at")


class ZoomCapturePreflight(FrozenExitSpecModel):
    """Operator attestations required before a synthetic Zoom capture."""

    general_app_reviewed: Literal[True] = True
    rtms_enabled: Literal[True] = True
    rtms_credits_confirmed: Literal[True] = True
    webhook_capture_ready: Literal[True] = True
    transcript_capture_ready: Literal[True] = True
    required_scopes: tuple[str, ...]
    operator_attestations_only: Literal[True] = True
    provider_state_independently_verified: Literal[False] = False

    @field_validator("required_scopes")
    @classmethod
    def require_exact_scopes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if value != _REQUIRED_SCOPES:
            raise ValueError("required_scopes must match the reviewed scope set.")
        return value


class ZoomGoldenCapturePlan(FrozenExitSpecModel):
    """Bounded local plan; it grants no network or product authority."""

    schema_version: Literal[ZOOM_CAPTURE_PLAN_VERSION] = ZOOM_CAPTURE_PLAN_VERSION
    capture_id: str = Field(pattern=r"^zoomcap_[a-z0-9][a-z0-9_-]{2,95}$")
    provider: Literal["ZOOM_RTMS"] = "ZOOM_RTMS"
    app_type: Literal["GENERAL_APP"] = "GENERAL_APP"
    source_classification: Literal["SYNTHETIC_TWO_PARTICIPANT_ONLY"] = (
        "SYNTHETIC_TWO_PARTICIPANT_ONLY"
    )
    requested_media: tuple[str, ...]
    excluded_media: tuple[str, ...]
    preflight: ZoomCapturePreflight
    participants: tuple[ZoomSyntheticParticipant, ZoomSyntheticParticipant]
    scheduled_start_at: datetime
    scheduled_end_at: datetime
    retention_hours: StrictInt = Field(gt=0, le=_MAX_RETENTION_HOURS)
    public_fixture_requires_privacy_review: Literal[True] = True
    customer_data_prohibited: Literal[True] = True
    capture_kit_grants_network_authority: Literal[False] = False
    capture_kit_grants_mapper_authority: Literal[False] = False
    capture_kit_grants_product_decision_authority: Literal[False] = False

    @field_validator("scheduled_start_at", "scheduled_end_at")
    @classmethod
    def normalize_schedule(cls, value: datetime, info: Any) -> datetime:
        return _utc_datetime(value, info.field_name)

    @field_validator("requested_media")
    @classmethod
    def require_transcript_only(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if value != _REQUESTED_MEDIA:
            raise ValueError("requested_media must be transcript-only.")
        return value

    @field_validator("excluded_media")
    @classmethod
    def require_excluded_media(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if value != _EXCLUDED_MEDIA:
            raise ValueError("excluded_media must match the reviewed exclusion set.")
        return value

    @model_validator(mode="after")
    def validate_synthetic_session(self) -> "ZoomGoldenCapturePlan":
        duration_seconds = (
            self.scheduled_end_at - self.scheduled_start_at
        ).total_seconds()
        if duration_seconds <= 0 or duration_seconds > _MAX_CAPTURE_MINUTES * 60:
            raise ValueError("The capture window must be positive and bounded.")
        if {participant.role for participant in self.participants} != {
            "HOST",
            "GUEST",
        }:
            raise ValueError("The capture requires one synthetic host and one guest.")
        if len({participant.label for participant in self.participants}) != 2:
            raise ValueError("Synthetic participant labels must be distinct.")
        if (
            len({participant.disclosure_sha256 for participant in self.participants})
            != 1
        ):
            raise ValueError("Both participants must consent to one disclosure.")
        if any(
            participant.consented_at > self.scheduled_start_at
            for participant in self.participants
        ):
            raise ValueError("Consent must be recorded before the capture starts.")
        return self


class ZoomFixturePreflightReceipt(FrozenExitSpecModel):
    """Content-free proof that local capture prerequisites were declared."""

    schema_version: Literal[ZOOM_PREFLIGHT_RECEIPT_VERSION] = (
        ZOOM_PREFLIGHT_RECEIPT_VERSION
    )
    receipt_id: str = Field(pattern=r"^zoompreflight_[a-f0-9]{64}$")
    capture_id: str = Field(pattern=r"^zoomcap_[a-z0-9][a-z0-9_-]{2,95}$")
    capture_plan_sha256: str = Field(pattern=SHA256_PATTERN)
    checked_at: datetime
    state: Literal["READY_FOR_OPERATOR_CONTROLLED_SYNTHETIC_CAPTURE"] = (
        "READY_FOR_OPERATOR_CONTROLLED_SYNTHETIC_CAPTURE"
    )
    required_artifact_roles: tuple[str, ...]
    workspace_relative_path: str = Field(
        pattern=r"^\.zoom-fixture-private/zoomcap_[a-z0-9][a-z0-9_-]{2,95}$"
    )
    authority: Literal[ZOOM_CAPTURE_AUTHORITY] = ZOOM_CAPTURE_AUTHORITY
    provider_state_independently_verified: Literal[False] = False
    raw_payloads_parsed: Literal[False] = False
    may_call_zoom: Literal[False] = False
    may_publish_fixture: Literal[False] = False
    may_define_mapper: Literal[False] = False
    may_confirm_contract: Literal[False] = False
    may_freeze_contract: Literal[False] = False
    may_start_measurement: Literal[False] = False
    may_assign_verdict: Literal[False] = False

    @field_validator("checked_at")
    @classmethod
    def normalize_checked_at(cls, value: datetime) -> datetime:
        return _utc_datetime(value, "checked_at")

    @model_validator(mode="after")
    def validate_receipt(self) -> "ZoomFixturePreflightReceipt":
        if self.required_artifact_roles != tuple(_ARTIFACT_FILENAME_BY_ROLE):
            raise ValueError("The preflight receipt inventory is invalid.")
        if self.workspace_relative_path != f"{PRIVATE_CAPTURE_ROOT}/{self.capture_id}":
            raise ValueError("The preflight workspace binding is invalid.")
        expected = _preflight_receipt_id(_model_payload_without(self, "receipt_id"))
        if not hmac.compare_digest(self.receipt_id, expected):
            raise ValueError("The preflight receipt identity is invalid.")
        return self


class ZoomCaptureArtifactRecord(FrozenExitSpecModel):
    """Content-free digest record for one required opaque artifact."""

    role: str = Field(pattern=r"^[a-z][a-z0-9_]{2,95}$")
    byte_count: StrictInt = Field(gt=0, le=_MAX_ARTIFACT_BYTES)
    sha256: str = Field(pattern=SHA256_PATTERN)


class ZoomFixtureCustodyManifest(FrozenExitSpecModel):
    """Hash-sealed inventory; it contains no captured path or payload value."""

    schema_version: Literal[ZOOM_CUSTODY_MANIFEST_VERSION] = (
        ZOOM_CUSTODY_MANIFEST_VERSION
    )
    manifest_id: str = Field(pattern=r"^zoomcustody_[a-f0-9]{64}$")
    capture_id: str = Field(pattern=r"^zoomcap_[a-z0-9][a-z0-9_-]{2,95}$")
    capture_plan_sha256: str = Field(pattern=SHA256_PATTERN)
    sealed_at: datetime
    digest_algorithm: Literal["sha256"] = "sha256"
    artifacts: tuple[ZoomCaptureArtifactRecord, ...]
    total_bytes: StrictInt = Field(gt=0, le=_MAX_CAPTURE_BYTES)
    raw_artifacts_parsed: Literal[False] = False
    raw_artifacts_remain_private: Literal[True] = True
    privacy_review_status: Literal["PENDING"] = "PENDING"
    sanitized_fixture_status: Literal["NOT_CREATED"] = "NOT_CREATED"
    wire_schema_frozen: Literal[False] = False
    authority: Literal[ZOOM_CAPTURE_AUTHORITY] = ZOOM_CAPTURE_AUTHORITY
    may_call_zoom: Literal[False] = False
    may_publish_fixture: Literal[False] = False
    may_define_mapper: Literal[False] = False
    may_confirm_contract: Literal[False] = False
    may_freeze_contract: Literal[False] = False
    may_start_measurement: Literal[False] = False
    may_assign_verdict: Literal[False] = False

    @field_validator("sealed_at")
    @classmethod
    def normalize_sealed_at(cls, value: datetime) -> datetime:
        return _utc_datetime(value, "sealed_at")

    @model_validator(mode="after")
    def validate_manifest(self) -> "ZoomFixtureCustodyManifest":
        if tuple(record.role for record in self.artifacts) != tuple(
            _ARTIFACT_FILENAME_BY_ROLE
        ):
            raise ValueError("The custody manifest inventory is invalid.")
        if len({record.role for record in self.artifacts}) != len(self.artifacts):
            raise ValueError("The custody manifest contains duplicate roles.")
        if sum(record.byte_count for record in self.artifacts) != self.total_bytes:
            raise ValueError("The custody manifest total is invalid.")
        expected = _manifest_id(_model_payload_without(self, "manifest_id"))
        if not hmac.compare_digest(self.manifest_id, expected):
            raise ValueError("The custody manifest identity is invalid.")
        return self


class ZoomFixturePrivacyReview(FrozenExitSpecModel):
    """Manual review input; it can nominate but never publish a fixture."""

    schema_version: Literal[ZOOM_PRIVACY_REVIEW_VERSION] = ZOOM_PRIVACY_REVIEW_VERSION
    capture_id: str = Field(pattern=r"^zoomcap_[a-z0-9][a-z0-9_-]{2,95}$")
    custody_manifest_id: str = Field(pattern=r"^zoomcustody_[a-f0-9]{64}$")
    reviewer_label: str = Field(pattern=r"^[a-z][a-z0-9_-]{2,63}$")
    reviewed_at: datetime
    original_remains_private: Literal[True]
    synthetic_content_verified: Literal[True]
    customer_data_absent: Literal[True]
    provider_secrets_absent_from_candidate: Literal[True]
    provider_identifiers_removed_or_documented: Literal[True]
    secret_scan_completed: Literal[True]
    secret_scan_tool: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{1,63}$")
    transformations_documented: Literal[True]
    transformation_notes: tuple[str, ...] = Field(max_length=20)
    original_signature_after_redaction: Literal["NOT_CLAIMED"]
    decision: Literal["KEEP_PRIVATE", "SANITIZED_CANDIDATE_READY_FOR_REVIEW"]
    candidate_publication_authorized: Literal[False]

    @field_validator("reviewed_at")
    @classmethod
    def normalize_reviewed_at(cls, value: datetime) -> datetime:
        return _utc_datetime(value, "reviewed_at")

    @field_validator("transformation_notes")
    @classmethod
    def validate_notes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(
            not isinstance(note, str)
            or not 1 <= len(note) <= 160
            or _SECRET_VALUE_PATTERN.search(note)
            for note in value
        ):
            raise ValueError("Transformation notes are invalid.")
        return value

    @model_validator(mode="after")
    def require_candidate_notes(self) -> "ZoomFixturePrivacyReview":
        if (
            self.decision == "SANITIZED_CANDIDATE_READY_FOR_REVIEW"
            and not self.transformation_notes
        ):
            raise ValueError("A sanitized candidate requires transformation notes.")
        return self


class ZoomFixturePrivacyReviewReceipt(FrozenExitSpecModel):
    """Content-free receipt for one immutable privacy review."""

    schema_version: Literal[ZOOM_PRIVACY_REVIEW_RECEIPT_VERSION] = (
        ZOOM_PRIVACY_REVIEW_RECEIPT_VERSION
    )
    receipt_id: str = Field(pattern=r"^zoomprivacy_[a-f0-9]{64}$")
    capture_id: str = Field(pattern=r"^zoomcap_[a-z0-9][a-z0-9_-]{2,95}$")
    custody_manifest_id: str = Field(pattern=r"^zoomcustody_[a-f0-9]{64}$")
    privacy_review_sha256: str = Field(pattern=SHA256_PATTERN)
    reviewer_label_sha256: str = Field(pattern=SHA256_PATTERN)
    reviewed_at: datetime
    decision: Literal["KEEP_PRIVATE", "SANITIZED_CANDIDATE_READY_FOR_REVIEW"]
    original_signature_after_redaction: Literal["NOT_CLAIMED"] = "NOT_CLAIMED"
    candidate_publication_authorized: Literal[False] = False
    mapper_implementation_authorized: Literal[False] = False
    network_transport_authorized: Literal[False] = False
    product_decision_authorized: Literal[False] = False
    authority: Literal[ZOOM_CAPTURE_AUTHORITY] = ZOOM_CAPTURE_AUTHORITY

    @field_validator("reviewed_at")
    @classmethod
    def normalize_reviewed_at(cls, value: datetime) -> datetime:
        return _utc_datetime(value, "reviewed_at")

    @model_validator(mode="after")
    def validate_receipt(self) -> "ZoomFixturePrivacyReviewReceipt":
        expected = _privacy_receipt_id(_model_payload_without(self, "receipt_id"))
        if not hmac.compare_digest(self.receipt_id, expected):
            raise ValueError("The privacy review receipt identity is invalid.")
        return self


def load_zoom_capture_plan(path: Path) -> ZoomGoldenCapturePlan:
    """Load one bounded plan while rejecting duplicate keys and secret-like values."""

    payload, _ = _load_json_object(
        Path(path),
        max_bytes=_MAX_PLAN_BYTES,
        failure_code=ZoomFixtureCaptureFailureCode.PLAN_REJECTED,
    )
    _reject_secret_like_values(
        payload,
        ZoomFixtureCaptureFailureCode.PLAN_REJECTED,
    )
    try:
        return ZoomGoldenCapturePlan.model_validate(payload)
    except Exception as exc:
        raise ZoomFixtureCaptureError(
            ZoomFixtureCaptureFailureCode.PLAN_REJECTED
        ) from exc


def initialize_zoom_fixture_capture(
    plan_path: Path,
    repository_root: Path,
    *,
    checked_at: datetime | None = None,
) -> ZoomFixturePreflightReceipt:
    """Create or exactly replay a safe private workspace for one capture plan."""

    plan = load_zoom_capture_plan(plan_path)
    root = _validated_repository_root(repository_root)
    workspace = _prepare_workspace(root, plan.capture_id)
    plan_bytes = canonical_json_bytes(plan.model_dump(mode="json"))
    plan_sha256 = hashlib.sha256(plan_bytes).hexdigest()
    stored_plan_path = workspace / "capture-plan.json"
    stored_receipt_path = workspace / "preflight-receipt.json"
    plan_exists = os.path.lexists(stored_plan_path)
    receipt_exists = os.path.lexists(stored_receipt_path)
    if plan_exists or receipt_exists:
        if not plan_exists or not receipt_exists:
            raise ZoomFixtureCaptureError(
                ZoomFixtureCaptureFailureCode.WORKSPACE_CONFLICT
            )
        stored_plan, stored_plan_bytes = _read_stored_plan(workspace)
        stored_receipt = _read_preflight_receipt(workspace)
        if (
            not hmac.compare_digest(stored_plan_bytes, plan_bytes)
            or stored_plan.capture_id != plan.capture_id
            or stored_receipt.capture_id != plan.capture_id
            or stored_receipt.capture_plan_sha256 != plan_sha256
        ):
            raise ZoomFixtureCaptureError(
                ZoomFixtureCaptureFailureCode.WORKSPACE_CONFLICT
            )
        return stored_receipt
    when = _now_or_supplied(checked_at)
    if when > plan.scheduled_start_at or any(
        participant.consented_at > when for participant in plan.participants
    ):
        raise ZoomFixtureCaptureError(ZoomFixtureCaptureFailureCode.PLAN_REJECTED)

    receipt_payload: dict[str, object] = {
        "schema_version": ZOOM_PREFLIGHT_RECEIPT_VERSION,
        "capture_id": plan.capture_id,
        "capture_plan_sha256": plan_sha256,
        "checked_at": when,
        "state": "READY_FOR_OPERATOR_CONTROLLED_SYNTHETIC_CAPTURE",
        "required_artifact_roles": tuple(_ARTIFACT_FILENAME_BY_ROLE),
        "workspace_relative_path": f"{PRIVATE_CAPTURE_ROOT}/{plan.capture_id}",
        "authority": ZOOM_CAPTURE_AUTHORITY,
        "provider_state_independently_verified": False,
        "raw_payloads_parsed": False,
        "may_call_zoom": False,
        "may_publish_fixture": False,
        "may_define_mapper": False,
        "may_confirm_contract": False,
        "may_freeze_contract": False,
        "may_start_measurement": False,
        "may_assign_verdict": False,
    }
    receipt_payload["receipt_id"] = _preflight_receipt_id(receipt_payload)
    try:
        receipt = ZoomFixturePreflightReceipt.model_validate(receipt_payload)
        receipt_bytes = canonical_json_bytes(receipt.model_dump(mode="json"))
    except Exception as exc:
        raise ZoomFixtureCaptureError(
            ZoomFixtureCaptureFailureCode.PLAN_REJECTED
        ) from exc

    _write_idempotent_control_file(
        stored_plan_path,
        plan_bytes,
        ZoomFixtureCaptureFailureCode.WORKSPACE_CONFLICT,
    )
    _write_idempotent_control_file(
        stored_receipt_path,
        receipt_bytes,
        ZoomFixtureCaptureFailureCode.WORKSPACE_CONFLICT,
    )
    return receipt


def seal_zoom_fixture_capture(
    repository_root: Path,
    capture_id: str,
    *,
    sealed_at: datetime | None = None,
) -> ZoomFixtureCustodyManifest:
    """Seal the exact opaque inventory or verify an already sealed capture."""

    root = _validated_repository_root(repository_root)
    workspace = _existing_workspace(root, capture_id)
    manifest_path = workspace / "custody-manifest.json"
    if os.path.lexists(manifest_path):
        return verify_zoom_fixture_capture(root, capture_id)

    plan, plan_bytes = _read_stored_plan(workspace)
    preflight = _read_preflight_receipt(workspace)
    plan_sha256 = hashlib.sha256(plan_bytes).hexdigest()
    if (
        plan.capture_id != capture_id
        or preflight.capture_id != capture_id
        or preflight.capture_plan_sha256 != plan_sha256
    ):
        raise ZoomFixtureCaptureError(
            ZoomFixtureCaptureFailureCode.CAPTURE_INTEGRITY_FAILED
        )

    records = _inventory_opaque_artifacts(workspace / "raw")
    total_bytes = sum(record.byte_count for record in records)
    manifest_payload: dict[str, object] = {
        "schema_version": ZOOM_CUSTODY_MANIFEST_VERSION,
        "capture_id": capture_id,
        "capture_plan_sha256": plan_sha256,
        "sealed_at": _now_or_supplied(sealed_at),
        "digest_algorithm": "sha256",
        "artifacts": [record.model_dump(mode="json") for record in records],
        "total_bytes": total_bytes,
        "raw_artifacts_parsed": False,
        "raw_artifacts_remain_private": True,
        "privacy_review_status": "PENDING",
        "sanitized_fixture_status": "NOT_CREATED",
        "wire_schema_frozen": False,
        "authority": ZOOM_CAPTURE_AUTHORITY,
        "may_call_zoom": False,
        "may_publish_fixture": False,
        "may_define_mapper": False,
        "may_confirm_contract": False,
        "may_freeze_contract": False,
        "may_start_measurement": False,
        "may_assign_verdict": False,
    }
    manifest_payload["manifest_id"] = _manifest_id(manifest_payload)
    try:
        manifest = ZoomFixtureCustodyManifest.model_validate(manifest_payload)
        manifest_bytes = canonical_json_bytes(manifest.model_dump(mode="json"))
        review_template = canonical_json_bytes(_privacy_review_template(manifest))
    except Exception as exc:
        raise ZoomFixtureCaptureError(
            ZoomFixtureCaptureFailureCode.CAPTURE_INTEGRITY_FAILED
        ) from exc

    _write_idempotent_control_file(
        workspace / "review" / "privacy-review-template.json",
        review_template,
        ZoomFixtureCaptureFailureCode.WORKSPACE_CONFLICT,
    )
    _make_capture_read_only(workspace)
    _write_idempotent_control_file(
        manifest_path,
        manifest_bytes,
        ZoomFixtureCaptureFailureCode.WORKSPACE_CONFLICT,
        read_only=True,
    )
    return verify_zoom_fixture_capture(root, capture_id)


def verify_zoom_fixture_capture(
    repository_root: Path,
    capture_id: str,
) -> ZoomFixtureCustodyManifest:
    """Independently re-read and re-hash an existing sealed capture."""

    root = _validated_repository_root(repository_root)
    workspace = _existing_workspace(root, capture_id)
    plan, plan_bytes = _read_stored_plan(workspace)
    preflight = _read_preflight_receipt(workspace)
    manifest = _read_canonical_model(
        workspace / "custody-manifest.json",
        ZoomFixtureCustodyManifest,
        ZoomFixtureCaptureFailureCode.CAPTURE_INTEGRITY_FAILED,
    )
    if not isinstance(manifest, ZoomFixtureCustodyManifest):
        raise ZoomFixtureCaptureError(
            ZoomFixtureCaptureFailureCode.CAPTURE_INTEGRITY_FAILED
        )
    plan_sha256 = hashlib.sha256(plan_bytes).hexdigest()
    if (
        plan.capture_id != capture_id
        or preflight.capture_id != capture_id
        or manifest.capture_id != capture_id
        or preflight.capture_plan_sha256 != plan_sha256
        or manifest.capture_plan_sha256 != plan_sha256
    ):
        raise ZoomFixtureCaptureError(
            ZoomFixtureCaptureFailureCode.CAPTURE_INTEGRITY_FAILED
        )
    observed_records = _inventory_opaque_artifacts(workspace / "raw")
    if not hmac.compare_digest(
        canonical_json_bytes(
            [record.model_dump(mode="json") for record in observed_records]
        ),
        canonical_json_bytes(
            [record.model_dump(mode="json") for record in manifest.artifacts]
        ),
    ):
        raise ZoomFixtureCaptureError(
            ZoomFixtureCaptureFailureCode.CAPTURE_INTEGRITY_FAILED
        )
    if not _capture_is_read_only(workspace):
        raise ZoomFixtureCaptureError(
            ZoomFixtureCaptureFailureCode.CAPTURE_INTEGRITY_FAILED
        )
    return manifest


def record_zoom_fixture_privacy_review(
    repository_root: Path,
    capture_id: str,
    review_path: Path,
) -> ZoomFixturePrivacyReviewReceipt:
    """Record one complete review without publishing or authorizing a mapper."""

    root = _validated_repository_root(repository_root)
    workspace = _existing_workspace(root, capture_id)
    manifest = verify_zoom_fixture_capture(root, capture_id)
    payload, _ = _load_json_object(
        Path(review_path),
        max_bytes=_MAX_REVIEW_BYTES,
        failure_code=ZoomFixtureCaptureFailureCode.PRIVACY_REVIEW_REJECTED,
    )
    _reject_secret_like_values(
        payload,
        ZoomFixtureCaptureFailureCode.PRIVACY_REVIEW_REJECTED,
    )
    try:
        review = ZoomFixturePrivacyReview.model_validate(payload)
        review_bytes = canonical_json_bytes(review.model_dump(mode="json"))
    except Exception as exc:
        raise ZoomFixtureCaptureError(
            ZoomFixtureCaptureFailureCode.PRIVACY_REVIEW_REJECTED
        ) from exc
    if (
        review.capture_id != capture_id
        or review.custody_manifest_id != manifest.manifest_id
        or review.reviewed_at < manifest.sealed_at
    ):
        raise ZoomFixtureCaptureError(
            ZoomFixtureCaptureFailureCode.PRIVACY_REVIEW_REJECTED
        )

    review_sha256 = hashlib.sha256(_REVIEW_DOMAIN + review_bytes).hexdigest()
    reviewer_label_sha256 = hashlib.sha256(
        review.reviewer_label.encode("utf-8")
    ).hexdigest()
    receipt_payload: dict[str, object] = {
        "schema_version": ZOOM_PRIVACY_REVIEW_RECEIPT_VERSION,
        "capture_id": capture_id,
        "custody_manifest_id": manifest.manifest_id,
        "privacy_review_sha256": review_sha256,
        "reviewer_label_sha256": reviewer_label_sha256,
        "reviewed_at": review.reviewed_at,
        "decision": review.decision,
        "original_signature_after_redaction": "NOT_CLAIMED",
        "candidate_publication_authorized": False,
        "mapper_implementation_authorized": False,
        "network_transport_authorized": False,
        "product_decision_authorized": False,
        "authority": ZOOM_CAPTURE_AUTHORITY,
    }
    receipt_payload["receipt_id"] = _privacy_receipt_id(receipt_payload)
    try:
        receipt = ZoomFixturePrivacyReviewReceipt.model_validate(receipt_payload)
        receipt_bytes = canonical_json_bytes(receipt.model_dump(mode="json"))
    except Exception as exc:
        raise ZoomFixtureCaptureError(
            ZoomFixtureCaptureFailureCode.PRIVACY_REVIEW_REJECTED
        ) from exc

    review_dir = workspace / "review"
    _write_idempotent_control_file(
        review_dir / "privacy-review.json",
        review_bytes,
        ZoomFixtureCaptureFailureCode.PRIVACY_REVIEW_CONFLICT,
        read_only=True,
    )
    _write_idempotent_control_file(
        review_dir / "privacy-review-receipt.json",
        receipt_bytes,
        ZoomFixtureCaptureFailureCode.PRIVACY_REVIEW_CONFLICT,
        read_only=True,
    )
    return receipt


def _utc_datetime(value: datetime, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{field_name} must be timezone-aware.")
    return value.astimezone(timezone.utc)


def _now_or_supplied(value: datetime | None) -> datetime:
    supplied = datetime.now(timezone.utc) if value is None else value
    try:
        return _utc_datetime(supplied, "operation_time")
    except ValueError as exc:
        raise ZoomFixtureCaptureError(
            ZoomFixtureCaptureFailureCode.LOCAL_IO_FAILED
        ) from exc


def _model_payload_without(model: FrozenExitSpecModel, field: str) -> dict[str, Any]:
    payload = model.model_dump(mode="json")
    payload.pop(field)
    return payload


def _digest_identifier(
    prefix: str, domain: bytes, payload: Mapping[str, object]
) -> str:
    json_payload = _json_compatible(dict(payload))
    return (
        prefix + hashlib.sha256(domain + canonical_json_bytes(json_payload)).hexdigest()
    )


def _json_compatible(value: object) -> object:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, dict):
        return {key: _json_compatible(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_compatible(item) for item in value]
    return value


def _preflight_receipt_id(payload: Mapping[str, object]) -> str:
    return _digest_identifier("zoompreflight_", _PREFLIGHT_DOMAIN, payload)


def _manifest_id(payload: Mapping[str, object]) -> str:
    return _digest_identifier("zoomcustody_", _MANIFEST_DOMAIN, payload)


def _privacy_receipt_id(payload: Mapping[str, object]) -> str:
    return _digest_identifier("zoomprivacy_", _REVIEW_RECEIPT_DOMAIN, payload)


def _validated_repository_root(repository_root: Path) -> Path:
    supplied = Path(repository_root)
    try:
        if supplied.is_symlink():
            raise OSError
        root = supplied.resolve(strict=True)
        if not root.is_dir():
            raise OSError
        git_marker = root / ".git"
        if not git_marker.exists() or git_marker.is_symlink():
            raise OSError
        ignore_bytes = _read_bounded_regular_file(
            root / ".gitignore",
            max_bytes=_MAX_CONTROL_FILE_BYTES,
            require_nonempty=True,
        )
        ignore_lines = {
            line.strip()
            for line in ignore_bytes.decode("utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }
        if PRIVATE_CAPTURE_IGNORE_RULE not in ignore_lines:
            raise OSError
        return root
    except (OSError, UnicodeError, ZoomFixtureCaptureError) as exc:
        raise ZoomFixtureCaptureError(
            ZoomFixtureCaptureFailureCode.REPOSITORY_NOT_READY
        ) from exc


def _prepare_workspace(root: Path, capture_id: str) -> Path:
    _validate_capture_id(capture_id)
    private_root = root / PRIVATE_CAPTURE_ROOT
    workspace = private_root / capture_id
    try:
        _ensure_private_directory(private_root, create=True)
        _ensure_private_directory(workspace, create=True)
        _ensure_private_directory(
            workspace / "raw",
            create=True,
            allow_read_only=True,
        )
        _ensure_private_directory(workspace / "review", create=True)
        if workspace.resolve(strict=True).parent != private_root.resolve(strict=True):
            raise OSError
        return workspace
    except (OSError, ZoomFixtureCaptureError) as exc:
        raise ZoomFixtureCaptureError(
            ZoomFixtureCaptureFailureCode.WORKSPACE_UNSAFE
        ) from exc


def _existing_workspace(root: Path, capture_id: str) -> Path:
    _validate_capture_id(capture_id)
    workspace = root / PRIVATE_CAPTURE_ROOT / capture_id
    try:
        _ensure_private_directory(root / PRIVATE_CAPTURE_ROOT, create=False)
        _ensure_private_directory(workspace, create=False)
        _ensure_private_directory(workspace / "raw", create=False, allow_read_only=True)
        _ensure_private_directory(workspace / "review", create=False)
        if workspace.resolve(strict=True).parent != (
            root / PRIVATE_CAPTURE_ROOT
        ).resolve(strict=True):
            raise OSError
        return workspace
    except (OSError, ZoomFixtureCaptureError) as exc:
        raise ZoomFixtureCaptureError(
            ZoomFixtureCaptureFailureCode.WORKSPACE_UNSAFE
        ) from exc


def _validate_capture_id(capture_id: str) -> None:
    if not isinstance(capture_id, str) or not _CAPTURE_ID_PATTERN.fullmatch(capture_id):
        raise ZoomFixtureCaptureError(ZoomFixtureCaptureFailureCode.WORKSPACE_UNSAFE)


def _ensure_private_directory(
    path: Path,
    *,
    create: bool,
    allow_read_only: bool = False,
) -> None:
    if os.path.lexists(path):
        metadata = path.lstat()
        if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            raise OSError
    elif create:
        path.mkdir(mode=0o700)
        metadata = path.lstat()
    else:
        raise OSError
    if metadata.st_mode & 0o077:
        raise OSError
    if not allow_read_only and not metadata.st_mode & stat.S_IWUSR:
        raise OSError


def _load_json_object(
    path: Path,
    *,
    max_bytes: int,
    failure_code: ZoomFixtureCaptureFailureCode,
) -> tuple[dict[str, object], bytes]:
    try:
        raw = _read_bounded_regular_file(
            path,
            max_bytes=max_bytes,
            require_nonempty=True,
        )
        decoded = raw.decode("utf-8")
        value = json.loads(
            decoded,
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
        if not isinstance(value, dict):
            raise ValueError
        return value, raw
    except Exception as exc:
        raise ZoomFixtureCaptureError(failure_code) from exc


def _unique_json_object(pairs: Sequence[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON object key.")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError("Non-finite JSON number.")


def _reject_secret_like_values(
    value: object,
    failure_code: ZoomFixtureCaptureFailureCode,
) -> None:
    stack = [value]
    visited = 0
    while stack:
        current = stack.pop()
        visited += 1
        if visited > 10_000:
            raise ZoomFixtureCaptureError(failure_code)
        if isinstance(current, dict):
            stack.extend(current.values())
        elif isinstance(current, list):
            stack.extend(current)
        elif isinstance(current, str) and _SECRET_VALUE_PATTERN.search(current):
            raise ZoomFixtureCaptureError(failure_code)


def _read_bounded_regular_file(
    path: Path,
    *,
    max_bytes: int,
    require_nonempty: bool,
) -> bytes:
    if path.is_symlink():
        raise OSError
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise OSError
        if metadata.st_size > max_bytes or (require_nonempty and metadata.st_size == 0):
            raise OSError
        chunks: list[bytes] = []
        observed = 0
        while True:
            chunk = os.read(descriptor, min(64 * 1024, max_bytes + 1 - observed))
            if not chunk:
                break
            observed += len(chunk)
            if observed > max_bytes:
                raise OSError
            chunks.append(chunk)
        if require_nonempty and observed == 0:
            raise OSError
        if os.fstat(descriptor).st_size != observed:
            raise OSError
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _write_idempotent_control_file(
    path: Path,
    payload: bytes,
    conflict_code: ZoomFixtureCaptureFailureCode,
    *,
    read_only: bool = False,
) -> None:
    try:
        if os.path.lexists(path):
            existing = _read_bounded_regular_file(
                path,
                max_bytes=_MAX_CONTROL_FILE_BYTES,
                require_nonempty=True,
            )
            if not hmac.compare_digest(existing, payload):
                raise ZoomFixtureCaptureError(conflict_code)
            if read_only:
                os.chmod(path, 0o400)
            return
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags, 0o600)
        try:
            view = memoryview(payload)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise OSError
                view = view[written:]
            os.fsync(descriptor)
            if read_only:
                os.fchmod(descriptor, 0o400)
        except Exception:
            os.close(descriptor)
            descriptor = -1
            try:
                path.unlink()
            except OSError:
                pass
            raise
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        _fsync_directory(path.parent)
    except ZoomFixtureCaptureError:
        raise
    except Exception as exc:
        raise ZoomFixtureCaptureError(
            ZoomFixtureCaptureFailureCode.LOCAL_IO_FAILED
        ) from exc


def _read_stored_plan(workspace: Path) -> tuple[ZoomGoldenCapturePlan, bytes]:
    try:
        payload, raw = _load_json_object(
            workspace / "capture-plan.json",
            max_bytes=_MAX_CONTROL_FILE_BYTES,
            failure_code=ZoomFixtureCaptureFailureCode.CAPTURE_INTEGRITY_FAILED,
        )
        plan = ZoomGoldenCapturePlan.model_validate(payload)
        canonical = canonical_json_bytes(plan.model_dump(mode="json"))
        if not hmac.compare_digest(canonical, raw):
            raise ValueError
        return plan, canonical
    except Exception as exc:
        raise ZoomFixtureCaptureError(
            ZoomFixtureCaptureFailureCode.CAPTURE_INTEGRITY_FAILED
        ) from exc


def _read_preflight_receipt(workspace: Path) -> ZoomFixturePreflightReceipt:
    receipt = _read_canonical_model(
        workspace / "preflight-receipt.json",
        ZoomFixturePreflightReceipt,
        ZoomFixtureCaptureFailureCode.CAPTURE_INTEGRITY_FAILED,
    )
    if not isinstance(receipt, ZoomFixturePreflightReceipt):
        raise ZoomFixtureCaptureError(
            ZoomFixtureCaptureFailureCode.CAPTURE_INTEGRITY_FAILED
        )
    return receipt


def _read_canonical_model(
    path: Path,
    model_type: type[FrozenExitSpecModel],
    failure_code: ZoomFixtureCaptureFailureCode,
) -> FrozenExitSpecModel:
    try:
        payload, raw = _load_json_object(
            path,
            max_bytes=_MAX_CONTROL_FILE_BYTES,
            failure_code=failure_code,
        )
        model = model_type.model_validate(payload)
        canonical = canonical_json_bytes(model.model_dump(mode="json"))
        if not hmac.compare_digest(canonical, raw):
            raise ValueError
        return model
    except Exception as exc:
        raise ZoomFixtureCaptureError(failure_code) from exc


def _inventory_opaque_artifacts(raw_dir: Path) -> tuple[ZoomCaptureArtifactRecord, ...]:
    try:
        _ensure_private_directory(raw_dir, create=False, allow_read_only=True)
        observed_names = {entry.name for entry in os.scandir(raw_dir)}
    except Exception as exc:
        raise ZoomFixtureCaptureError(
            ZoomFixtureCaptureFailureCode.CAPTURE_INCOMPLETE
        ) from exc
    if observed_names != set(_ARTIFACT_ROLE_BY_FILENAME):
        raise ZoomFixtureCaptureError(ZoomFixtureCaptureFailureCode.CAPTURE_INCOMPLETE)

    records: list[ZoomCaptureArtifactRecord] = []
    total_bytes = 0
    try:
        for role, filename in ZOOM_CAPTURE_ARTIFACTS:
            byte_count, digest = _hash_opaque_artifact(raw_dir / filename)
            total_bytes += byte_count
            if total_bytes > _MAX_CAPTURE_BYTES:
                raise OSError
            records.append(
                ZoomCaptureArtifactRecord(
                    role=role,
                    byte_count=byte_count,
                    sha256=digest,
                )
            )
        return tuple(records)
    except Exception as exc:
        raise ZoomFixtureCaptureError(
            ZoomFixtureCaptureFailureCode.CAPTURE_INTEGRITY_FAILED
        ) from exc


def _hash_opaque_artifact(path: Path) -> tuple[int, str]:
    if path.is_symlink():
        raise OSError
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_size <= 0
            or before.st_size > _MAX_ARTIFACT_BYTES
        ):
            raise OSError
        digest = hashlib.sha256()
        observed = 0
        while True:
            chunk = os.read(descriptor, 64 * 1024)
            if not chunk:
                break
            observed += len(chunk)
            if observed > _MAX_ARTIFACT_BYTES:
                raise OSError
            digest.update(chunk)
        after = os.fstat(descriptor)
        if (
            observed != before.st_size
            or after.st_size != before.st_size
            or after.st_mtime_ns != before.st_mtime_ns
        ):
            raise OSError
        return observed, digest.hexdigest()
    finally:
        os.close(descriptor)


def _make_capture_read_only(workspace: Path) -> None:
    try:
        for _, filename in ZOOM_CAPTURE_ARTIFACTS:
            os.chmod(workspace / "raw" / filename, 0o400)
        os.chmod(workspace / "capture-plan.json", 0o400)
        os.chmod(workspace / "preflight-receipt.json", 0o400)
        os.chmod(workspace / "raw", 0o500)
        _fsync_directory(workspace)
    except OSError as exc:
        raise ZoomFixtureCaptureError(
            ZoomFixtureCaptureFailureCode.LOCAL_IO_FAILED
        ) from exc


def _capture_is_read_only(workspace: Path) -> bool:
    paths = [
        workspace / "capture-plan.json",
        workspace / "preflight-receipt.json",
        workspace / "custody-manifest.json",
        *(workspace / "raw" / filename for _, filename in ZOOM_CAPTURE_ARTIFACTS),
    ]
    try:
        if (workspace / "raw").lstat().st_mode & 0o222:
            return False
        return all(not path.lstat().st_mode & 0o222 for path in paths)
    except OSError:
        return False


def _privacy_review_template(
    manifest: ZoomFixtureCustodyManifest,
) -> dict[str, object]:
    return {
        "schema_version": ZOOM_PRIVACY_REVIEW_VERSION,
        "capture_id": manifest.capture_id,
        "custody_manifest_id": manifest.manifest_id,
        "reviewer_label": "replace_with_reviewer_label",
        "reviewed_at": None,
        "original_remains_private": False,
        "synthetic_content_verified": False,
        "customer_data_absent": False,
        "provider_secrets_absent_from_candidate": False,
        "provider_identifiers_removed_or_documented": False,
        "secret_scan_completed": False,
        "secret_scan_tool": "replace_with_tool_version",
        "transformations_documented": False,
        "transformation_notes": [],
        "original_signature_after_redaction": "NOT_CLAIMED",
        "decision": "KEEP_PRIVATE",
        "candidate_publication_authorized": False,
    }


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _public_json(model: FrozenExitSpecModel) -> bytes:
    return canonical_json_bytes(model.model_dump(mode="json")) + b"\n"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m exitspec.zoom_fixture_capture",
        description="Prepare and verify one private synthetic Zoom fixture capture.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    preflight = subparsers.add_parser("preflight")
    preflight.add_argument("--plan", type=Path, required=True)
    preflight.add_argument("--repository-root", type=Path, default=Path.cwd())

    for command in ("seal", "verify"):
        subparser = subparsers.add_parser(command)
        subparser.add_argument("--capture-id", required=True)
        subparser.add_argument("--repository-root", type=Path, default=Path.cwd())

    review = subparsers.add_parser("review")
    review.add_argument("--capture-id", required=True)
    review.add_argument("--review", type=Path, required=True)
    review.add_argument("--repository-root", type=Path, default=Path.cwd())
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the local-only operator CLI with content-free failures."""

    parser = _build_parser()
    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "preflight":
            result = initialize_zoom_fixture_capture(
                arguments.plan,
                arguments.repository_root,
            )
        elif arguments.command == "seal":
            result = seal_zoom_fixture_capture(
                arguments.repository_root,
                arguments.capture_id,
            )
        elif arguments.command == "verify":
            result = verify_zoom_fixture_capture(
                arguments.repository_root,
                arguments.capture_id,
            )
        else:
            result = record_zoom_fixture_privacy_review(
                arguments.repository_root,
                arguments.capture_id,
                arguments.review,
            )
        sys.stdout.buffer.write(_public_json(result))
        return 0
    except ZoomFixtureCaptureError as exc:
        failure = canonical_json_bytes(
            {"error": {"code": exc.code, "message": str(exc)}}
        )
        sys.stderr.buffer.write(failure + b"\n")
        return 2
    except Exception:
        failure = canonical_json_bytes(
            {
                "error": {
                    "code": ZoomFixtureCaptureFailureCode.LOCAL_IO_FAILED.value,
                    "message": _FAILURE_MESSAGES[
                        ZoomFixtureCaptureFailureCode.LOCAL_IO_FAILED
                    ],
                }
            }
        )
        sys.stderr.buffer.write(failure + b"\n")
        return 2


__all__ = [
    "PRIVATE_CAPTURE_IGNORE_RULE",
    "PRIVATE_CAPTURE_ROOT",
    "ZOOM_CAPTURE_ARTIFACTS",
    "ZOOM_CAPTURE_AUTHORITY",
    "ZOOM_CAPTURE_PLAN_VERSION",
    "ZOOM_CUSTODY_MANIFEST_VERSION",
    "ZOOM_PREFLIGHT_RECEIPT_VERSION",
    "ZOOM_PRIVACY_REVIEW_RECEIPT_VERSION",
    "ZOOM_PRIVACY_REVIEW_VERSION",
    "ZoomCaptureArtifactRecord",
    "ZoomCapturePreflight",
    "ZoomFixtureCaptureError",
    "ZoomFixtureCaptureFailureCode",
    "ZoomFixtureCustodyManifest",
    "ZoomFixturePreflightReceipt",
    "ZoomFixturePrivacyReview",
    "ZoomFixturePrivacyReviewReceipt",
    "ZoomGoldenCapturePlan",
    "ZoomSyntheticParticipant",
    "initialize_zoom_fixture_capture",
    "load_zoom_capture_plan",
    "record_zoom_fixture_privacy_review",
    "seal_zoom_fixture_capture",
    "verify_zoom_fixture_capture",
]


if __name__ == "__main__":
    raise SystemExit(main())
