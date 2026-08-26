"""Consent-gated derivation of sanitized synthetic Zoom fixtures.

The pipeline intentionally has no raw-capture reader.  A human privacy review
may inspect a private capture in an isolated environment and submit only the
bounded, content-free observations represented here.  The current private
diagnostic capture cannot enter this module without that explicit consent
receipt and a complete custody descriptor.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import stat
from typing import Any, Final, Literal

from pydantic import ConfigDict, Field, StrictInt, field_validator, model_validator

from .canonical import canonical_json_bytes
from .models import FrozenExitSpecModel, SHA256_PATTERN
from .zoom_evidence_boundary import validate_rotation_receipt_id


ZOOM_PRIVACY_CONSENT_VERSION = "exitspec.zoom-rtms-raw-review-consent.v1"
ZOOM_SANITIZED_FIXTURE_VERSION = "exitspec.zoom-rtms-sanitized-fixture.v1"
ZOOM_SANITIZED_REVIEW_VERSION = "exitspec.zoom-rtms-sanitized-review.v1"
ZOOM_PRIVACY_CONSENT_SCOPE = "SANITIZE_PRIVATE_CAPTURE_TO_SYNTHETIC_CANDIDATE"
ZOOM_FIXTURE_CLASSIFICATION = "SANITIZED_SYNTHETIC_CONFORMANCE_FIXTURE"

_CONSENT_DOMAIN = b"exitspec-zoom-privacy-consent-v1\x00"
_FIXTURE_DOMAIN = b"exitspec-zoom-sanitized-fixture-v1\x00"
_REVIEW_DOMAIN = b"exitspec-zoom-sanitized-review-v1\x00"
_MAX_JSON_BYTES = 256 * 1024
_MAX_OBSERVATIONS = 256
_MAX_CAPTURE_MILLISECONDS = 30 * 60 * 1000
_MAX_PACKET_MILLISECONDS = 10 * 60 * 1000
_MAX_BUNDLE_FILE_BYTES = 512 * 1024
_FORBIDDEN_TEXT_KEYS: Final = frozenset(
    {
        "access_token",
        "client_id",
        "client_secret",
        "meeting_id",
        "participant_id",
        "refresh_token",
        "secret",
        "text",
        "token",
        "url",
    }
)
_SECRET_OR_ENDPOINT_PATTERN = re.compile(
    r"(?i)(?:\b(?:bearer|basic)\s+[a-z0-9._~+/=-]+|https?://|zoommtg:|"
    r"\b(?:fw|sk|gho|github_pat|xox[baprs])_[a-z0-9_-]{8,}|"
    r"\beyJ[a-z0-9_-]{8,}\.[a-z0-9_-]{8,})"
)


class ZoomPrivacyPipelineError(RuntimeError):
    """Sanitized refusal for the privacy gate and fixture bundle."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__("The Zoom privacy pipeline rejected the submitted material.")


class _PrivacyModel(FrozenExitSpecModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
        validate_default=True,
    )


class ZoomRawReviewConsent(_PrivacyModel):
    """Explicit human authorization to inspect one private capture."""

    schema_version: Literal[ZOOM_PRIVACY_CONSENT_VERSION] = (
        ZOOM_PRIVACY_CONSENT_VERSION
    )
    consent_id: str = Field(pattern=r"^zoomprivacyconsent_[a-f0-9]{64}$")
    capture_id: str = Field(pattern=r"^zoomcap_[a-z0-9][a-z0-9_-]{2,95}$")
    custody_manifest_id: str = Field(pattern=r"^zoomcustody_[a-f0-9]{64}$")
    reviewer_label: str = Field(pattern=r"^[a-z][a-z0-9_-]{2,63}$")
    consented_at: datetime
    scope: Literal[ZOOM_PRIVACY_CONSENT_SCOPE] = ZOOM_PRIVACY_CONSENT_SCOPE
    raw_capture_may_be_opened: Literal[True] = True
    raw_bytes_may_be_exported: Literal[False] = False
    raw_transcript_may_be_persisted: Literal[False] = False
    synthetic_content_required: Literal[True] = True
    customer_data_prohibited: Literal[True] = True
    secret_scan_required: Literal[True] = True
    candidate_publication_authorized: Literal[False] = False
    may_decode_provider_payload: Literal[False] = False
    may_create_product_source: Literal[False] = False
    may_confirm_contract: Literal[False] = False
    may_freeze_contract: Literal[False] = False
    may_start_measurement: Literal[False] = False
    may_assign_verdict: Literal[False] = False

    @field_validator("consented_at")
    @classmethod
    def normalize_consented_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("consented_at must be timezone-aware.")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def validate_identity(self) -> "ZoomRawReviewConsent":
        expected = _digest_identifier(
            "zoomprivacyconsent_",
            _CONSENT_DOMAIN,
            _model_payload_without(self, "consent_id"),
        )
        if not hmac.compare_digest(self.consent_id, expected):
            raise ValueError("privacy consent identity is invalid.")
        return self


class ZoomPrivateCaptureDescriptor(_PrivacyModel):
    """Content-free private input supplied by custody verification."""

    capture_id: str = Field(pattern=r"^zoomcap_[a-z0-9][a-z0-9_-]{2,95}$")
    custody_manifest_id: str = Field(pattern=r"^zoomcustody_[a-f0-9]{64}$")
    capture_plan_sha256: str = Field(pattern=SHA256_PATTERN)
    setup_attestation_sha256: str = Field(pattern=SHA256_PATTERN)
    runtime_plan_sha256: str = Field(pattern=SHA256_PATTERN)
    custody_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    credential_rotation_receipt_id: str = Field(
        pattern=r"^zoomcredrot_[a-f0-9]{64}$"
    )

    @field_validator("credential_rotation_receipt_id")
    @classmethod
    def validate_rotation_receipt(cls, value: str) -> str:
        if not validate_rotation_receipt_id(value):
            raise ValueError("credential rotation receipt is invalid.")
        return value


class ZoomSanitizedPacketObservation(_PrivacyModel):
    """Allowed protocol shape with no provider identity or free-form text."""

    observation_id: str = Field(pattern=r"^obs_[a-z0-9][a-z0-9_-]{2,63}$")
    arrival_index: StrictInt = Field(gt=0, le=_MAX_OBSERVATIONS)
    protocol_sequence: StrictInt = Field(gt=0, le=4096)
    wire_media_type: StrictInt = Field(ge=0, le=255)
    wire_message_type: StrictInt = Field(ge=0, le=255)
    message_kind: Literal[
        "RTMS_STARTED",
        "RTMS_STOPPED",
        "PARTICIPANT_JOIN",
        "PARTICIPANT_LEAVE",
        "TRANSCRIPT_PARTIAL",
        "TRANSCRIPT_FINAL",
        "RECONNECT",
        "DUPLICATE_DELIVERY",
    ]
    speaker_slot: Literal["SPEAKER_1", "SPEAKER_2", "SPEAKER_UNKNOWN", "NONE"]
    transcript_finality: Literal["PARTIAL", "FINAL", "NOT_APPLICABLE"]
    start_millisecond: StrictInt = Field(ge=0, le=_MAX_CAPTURE_MILLISECONDS)
    duration_millisecond: StrictInt = Field(ge=0, le=_MAX_PACKET_MILLISECONDS)
    payload_sha256: str = Field(pattern=SHA256_PATTERN)
    payload_classification: Literal[
        "TRANSCRIPT_TEXT_REDACTED",
        "CONTROL_PAYLOAD_REDACTED",
    ]
    is_duplicate: Literal[True, False] = False
    duplicate_of_sequence: StrictInt | None = Field(default=None, gt=0, le=4096)

    @model_validator(mode="after")
    def validate_observation(self) -> "ZoomSanitizedPacketObservation":
        transcript_kind = self.message_kind in {
            "TRANSCRIPT_PARTIAL",
            "TRANSCRIPT_FINAL",
        }
        if transcript_kind:
            expected_finality = (
                "PARTIAL"
                if self.message_kind == "TRANSCRIPT_PARTIAL"
                else "FINAL"
            )
            if self.transcript_finality != expected_finality:
                raise ValueError("transcript finality does not match message kind.")
            if self.payload_classification != "TRANSCRIPT_TEXT_REDACTED":
                raise ValueError("transcript payload must be redacted.")
            if self.speaker_slot == "NONE":
                raise ValueError("transcript observations require a speaker slot.")
        else:
            if self.transcript_finality != "NOT_APPLICABLE":
                raise ValueError("control observations cannot claim transcript finality.")
            if self.payload_classification != "CONTROL_PAYLOAD_REDACTED":
                raise ValueError("control payload must be redacted.")
            if self.speaker_slot != "NONE":
                raise ValueError("control observations cannot claim a speaker.")
        if self.is_duplicate != (self.duplicate_of_sequence is not None):
            raise ValueError("duplicate metadata is inconsistent.")
        return self


class ZoomSanitizedProvenance(_PrivacyModel):
    """Digest-only provenance that cannot identify the private capture."""

    capture_id_sha256: str = Field(pattern=SHA256_PATTERN)
    custody_manifest_id_sha256: str = Field(pattern=SHA256_PATTERN)
    capture_plan_sha256: str = Field(pattern=SHA256_PATTERN)
    custody_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    setup_attestation_sha256: str = Field(pattern=SHA256_PATTERN)
    runtime_plan_sha256: str = Field(pattern=SHA256_PATTERN)
    privacy_consent_sha256: str = Field(pattern=SHA256_PATTERN)
    credential_rotation_receipt_sha256: str = Field(pattern=SHA256_PATTERN)


class ZoomSanitizedFixtureCandidate(_PrivacyModel):
    """Review-pending fixture candidate made only from safe observations."""

    schema_version: Literal[ZOOM_SANITIZED_FIXTURE_VERSION] = (
        ZOOM_SANITIZED_FIXTURE_VERSION
    )
    fixture_id: str = Field(pattern=r"^zoomfixture_[a-f0-9]{64}$")
    fixture_classification: Literal[ZOOM_FIXTURE_CLASSIFICATION] = (
        ZOOM_FIXTURE_CLASSIFICATION
    )
    protocol_semantics_version: Literal["OBSERVED_REVIEW_PENDING_V1"] = (
        "OBSERVED_REVIEW_PENDING_V1"
    )
    provenance: ZoomSanitizedProvenance
    observations: tuple[ZoomSanitizedPacketObservation, ...] = Field(
        min_length=1,
        max_length=_MAX_OBSERVATIONS,
    )
    privacy_review_status: Literal["REVIEW_PENDING"] = "REVIEW_PENDING"
    raw_artifacts_remain_private: Literal[True] = True
    raw_packet_bytes_removed: Literal[True] = True
    transcript_text_removed: Literal[True] = True
    participant_identities_pseudonymized: Literal[True] = True
    names_identifiers_urls_tokens_and_secrets_removed: Literal[True] = True
    candidate_contains_free_form_text: Literal[False] = False
    candidate_publication_authorized: Literal[False] = False
    may_decode_provider_payload: Literal[False] = False
    may_create_product_source: Literal[False] = False
    may_confirm_contract: Literal[False] = False
    may_freeze_contract: Literal[False] = False
    may_start_measurement: Literal[False] = False
    may_assign_verdict: Literal[False] = False

    @model_validator(mode="after")
    def validate_candidate(self) -> "ZoomSanitizedFixtureCandidate":
        arrival_indices = [item.arrival_index for item in self.observations]
        observation_ids = [item.observation_id for item in self.observations]
        if len(set(arrival_indices)) != len(arrival_indices):
            raise ValueError("observation arrival indices must be unique.")
        if len(set(observation_ids)) != len(observation_ids):
            raise ValueError("observation IDs must be unique.")
        expected = _digest_identifier(
            "zoomfixture_",
            _FIXTURE_DOMAIN,
            _model_payload_without(self, "fixture_id"),
        )
        if not hmac.compare_digest(self.fixture_id, expected):
            raise ValueError("sanitized fixture identity is invalid.")
        _assert_safe_public_strings(self.model_dump(mode="json"))
        return self


class ZoomSanitizedFixtureReviewReceipt(_PrivacyModel):
    """Second-person review receipt required before fixture publication."""

    schema_version: Literal[ZOOM_SANITIZED_REVIEW_VERSION] = (
        ZOOM_SANITIZED_REVIEW_VERSION
    )
    receipt_id: str = Field(pattern=r"^zoomfixturereview_[a-f0-9]{64}$")
    fixture_id: str = Field(pattern=r"^zoomfixture_[a-f0-9]{64}$")
    fixture_sha256: str = Field(pattern=SHA256_PATTERN)
    privacy_consent_sha256: str = Field(pattern=SHA256_PATTERN)
    reviewer_label_sha256: str = Field(pattern=SHA256_PATTERN)
    reviewed_at: datetime
    decision: Literal["APPROVED_FOR_DECODER_TESTS", "KEEP_PRIVATE"]
    original_capture_remains_private: Literal[True] = True
    candidate_contains_no_free_form_text: Literal[True] = True
    candidate_contains_no_provider_identifiers: Literal[True] = True
    secret_scan_completed: Literal[True] = True
    secret_scan_tool: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{1,63}$")
    fixture_publication_authorized: bool = False
    may_decode_provider_payload: Literal[False] = False
    may_create_product_source: Literal[False] = False
    may_confirm_contract: Literal[False] = False
    may_freeze_contract: Literal[False] = False
    may_start_measurement: Literal[False] = False
    may_assign_verdict: Literal[False] = False

    @field_validator("reviewed_at")
    @classmethod
    def normalize_reviewed_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("reviewed_at must be timezone-aware.")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def validate_review(self) -> "ZoomSanitizedFixtureReviewReceipt":
        if self.fixture_publication_authorized != (
            self.decision == "APPROVED_FOR_DECODER_TESTS"
        ):
            raise ValueError("fixture publication status does not match the decision.")
        expected = _digest_identifier(
            "zoomfixturereview_",
            _REVIEW_DOMAIN,
            _model_payload_without(self, "receipt_id"),
        )
        if not hmac.compare_digest(self.receipt_id, expected):
            raise ValueError("sanitized review receipt identity is invalid.")
        return self


def derive_sanitized_fixture_candidate(
    *,
    consent: ZoomRawReviewConsent,
    source: ZoomPrivateCaptureDescriptor,
    observations: Sequence[ZoomSanitizedPacketObservation],
) -> ZoomSanitizedFixtureCandidate:
    """Derive a candidate from reviewed observations, never from raw bytes."""

    if not consent.raw_capture_may_be_opened:
        raise ZoomPrivacyPipelineError("ZOOM_PRIVACY_CONSENT_REQUIRED")
    if consent.capture_id != source.capture_id:
        raise ZoomPrivacyPipelineError("ZOOM_PRIVACY_CAPTURE_BINDING_MISMATCH")
    if consent.custody_manifest_id != source.custody_manifest_id:
        raise ZoomPrivacyPipelineError("ZOOM_PRIVACY_MANIFEST_BINDING_MISMATCH")
    if not observations or len(observations) > _MAX_OBSERVATIONS:
        raise ZoomPrivacyPipelineError("ZOOM_PRIVACY_OBSERVATION_LIMIT")

    provenance = ZoomSanitizedProvenance(
        capture_id_sha256=_sha256_text(source.capture_id),
        custody_manifest_id_sha256=_sha256_text(source.custody_manifest_id),
        capture_plan_sha256=source.capture_plan_sha256,
        custody_manifest_sha256=source.custody_manifest_sha256,
        setup_attestation_sha256=source.setup_attestation_sha256,
        runtime_plan_sha256=source.runtime_plan_sha256,
        privacy_consent_sha256=_sha256_model(consent),
        credential_rotation_receipt_sha256=_sha256_text(
            source.credential_rotation_receipt_id
        ),
    )
    payload: dict[str, object] = {
        "schema_version": ZOOM_SANITIZED_FIXTURE_VERSION,
        "fixture_id": "zoomfixture_" + "0" * 64,
        "fixture_classification": ZOOM_FIXTURE_CLASSIFICATION,
        "protocol_semantics_version": "OBSERVED_REVIEW_PENDING_V1",
        "provenance": provenance.model_dump(mode="json"),
        "observations": [item.model_dump(mode="json") for item in observations],
        "privacy_review_status": "REVIEW_PENDING",
        "raw_artifacts_remain_private": True,
        "raw_packet_bytes_removed": True,
        "transcript_text_removed": True,
        "participant_identities_pseudonymized": True,
        "names_identifiers_urls_tokens_and_secrets_removed": True,
        "candidate_contains_free_form_text": False,
        "candidate_publication_authorized": False,
        "may_decode_provider_payload": False,
        "may_create_product_source": False,
        "may_confirm_contract": False,
        "may_freeze_contract": False,
        "may_start_measurement": False,
        "may_assign_verdict": False,
    }
    payload["fixture_id"] = _digest_identifier(
        "zoomfixture_",
        _FIXTURE_DOMAIN,
        {key: value for key, value in payload.items() if key != "fixture_id"},
    )
    try:
        return ZoomSanitizedFixtureCandidate.model_validate(payload)
    except Exception as exc:
        raise ZoomPrivacyPipelineError("ZOOM_PRIVACY_CANDIDATE_REJECTED") from exc


def load_zoom_raw_review_consent(path: Path) -> ZoomRawReviewConsent:
    """Load only the explicit consent receipt; never read a raw capture path."""

    payload = _load_bounded_json(path)
    try:
        return ZoomRawReviewConsent.model_validate(payload)
    except Exception as exc:
        raise ZoomPrivacyPipelineError("ZOOM_PRIVACY_CONSENT_REJECTED") from exc


def approve_sanitized_fixture(
    *,
    candidate: ZoomSanitizedFixtureCandidate,
    consent: ZoomRawReviewConsent,
    reviewer_label: str,
    secret_scan_tool: str,
    decision: Literal["APPROVED_FOR_DECODER_TESTS", "KEEP_PRIVATE"],
    reviewed_at: datetime,
) -> ZoomSanitizedFixtureReviewReceipt:
    """Create an immutable second-person receipt without exposing the source."""

    if candidate.provenance.privacy_consent_sha256 != _sha256_model(consent):
        raise ZoomPrivacyPipelineError("ZOOM_PRIVACY_CONSENT_BINDING_MISMATCH")
    if not re.fullmatch(r"[a-z][a-z0-9_-]{2,63}", reviewer_label):
        raise ZoomPrivacyPipelineError("ZOOM_PRIVACY_REVIEWER_REJECTED")
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{1,63}", secret_scan_tool):
        raise ZoomPrivacyPipelineError("ZOOM_PRIVACY_SCAN_TOOL_REJECTED")
    try:
        reviewed = (
            reviewed_at.astimezone(timezone.utc)
            if reviewed_at.tzinfo is not None and reviewed_at.utcoffset() is not None
            else None
        )
        if reviewed is None:
            raise ValueError
        reviewed_iso = reviewed.isoformat().replace("+00:00", "Z")
        payload: dict[str, object] = {
            "schema_version": ZOOM_SANITIZED_REVIEW_VERSION,
            "receipt_id": "zoomfixturereview_" + "0" * 64,
            "fixture_id": candidate.fixture_id,
            "fixture_sha256": _sha256_model(candidate),
            "privacy_consent_sha256": _sha256_model(consent),
            "reviewer_label_sha256": _sha256_text(reviewer_label),
            "reviewed_at": reviewed_iso,
            "decision": decision,
            "original_capture_remains_private": True,
            "candidate_contains_no_free_form_text": True,
            "candidate_contains_no_provider_identifiers": True,
            "secret_scan_completed": True,
            "secret_scan_tool": secret_scan_tool,
            "fixture_publication_authorized": decision
            == "APPROVED_FOR_DECODER_TESTS",
            "may_decode_provider_payload": False,
            "may_create_product_source": False,
            "may_confirm_contract": False,
            "may_freeze_contract": False,
            "may_start_measurement": False,
            "may_assign_verdict": False,
        }
        payload["receipt_id"] = _digest_identifier(
            "zoomfixturereview_",
            _REVIEW_DOMAIN,
            {key: value for key, value in payload.items() if key != "receipt_id"},
        )
        return ZoomSanitizedFixtureReviewReceipt.model_validate(payload)
    except ZoomPrivacyPipelineError:
        raise
    except Exception as exc:
        raise ZoomPrivacyPipelineError("ZOOM_PRIVACY_REVIEW_REJECTED") from exc


def write_sanitized_fixture_bundle(
    output_directory: Path,
    candidate: ZoomSanitizedFixtureCandidate,
    receipt: ZoomSanitizedFixtureReviewReceipt,
) -> tuple[Path, Path]:
    """Write only approved JSON controls outside the ignored raw workspace."""

    if receipt.fixture_id != candidate.fixture_id:
        raise ZoomPrivacyPipelineError("ZOOM_PRIVACY_REVIEW_BINDING_MISMATCH")
    if receipt.fixture_publication_authorized is not True:
        raise ZoomPrivacyPipelineError("ZOOM_PRIVACY_PUBLICATION_NOT_AUTHORIZED")
    if receipt.fixture_sha256 != _sha256_model(candidate):
        raise ZoomPrivacyPipelineError("ZOOM_PRIVACY_FIXTURE_DIGEST_MISMATCH")
    try:
        directory = Path(output_directory)
        if directory.is_symlink() or ".zoom-fixture-private" in directory.parts:
            raise ValueError
        directory.mkdir(parents=True, exist_ok=True)
        if not directory.is_dir() or directory.stat().st_mode & 0o002:
            raise ValueError
        fixture_path = directory / "fixture.json"
        receipt_path = directory / "privacy-review-receipt.json"
        fixture_bytes = _public_json(candidate)
        receipt_bytes = _public_json(receipt)
        _write_immutable_json(fixture_path, fixture_bytes)
        _write_immutable_json(receipt_path, receipt_bytes)
        return fixture_path, receipt_path
    except ZoomPrivacyPipelineError:
        raise
    except Exception as exc:
        raise ZoomPrivacyPipelineError("ZOOM_PRIVACY_BUNDLE_WRITE_FAILED") from exc


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256_model(model: _PrivacyModel) -> str:
    return hashlib.sha256(canonical_json_bytes(model.model_dump(mode="json"))).hexdigest()


def _model_payload_without(model: _PrivacyModel, field: str) -> dict[str, Any]:
    payload = model.model_dump(mode="json")
    payload.pop(field, None)
    return payload


def _digest_identifier(prefix: str, domain: bytes, payload: Mapping[str, object]) -> str:
    return prefix + hashlib.sha256(
        domain + canonical_json_bytes(payload)
    ).hexdigest()


def _public_json(model: _PrivacyModel) -> bytes:
    payload = model.model_dump(mode="json")
    _assert_safe_public_strings(payload)
    return canonical_json_bytes(payload) + b"\n"


def _assert_safe_public_strings(value: object, key: str | None = None) -> None:
    if key is not None and key in _FORBIDDEN_TEXT_KEYS:
        raise ZoomPrivacyPipelineError("ZOOM_PRIVACY_FORBIDDEN_FIELD")
    if isinstance(value, str) and _SECRET_OR_ENDPOINT_PATTERN.search(value):
        raise ZoomPrivacyPipelineError("ZOOM_PRIVACY_SECRET_LIKE_VALUE")
    if isinstance(value, Mapping):
        for child_key, child in value.items():
            _assert_safe_public_strings(child, str(child_key))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for child in value:
            _assert_safe_public_strings(child)


def _write_immutable_json(path: Path, payload: bytes) -> None:
    if path.is_symlink() or path.exists() and not path.is_file():
        raise ValueError
    if len(payload) > _MAX_BUNDLE_FILE_BYTES:
        raise ValueError
    if path.exists():
        if path.read_bytes() != payload:
            raise ZoomPrivacyPipelineError("ZOOM_PRIVACY_BUNDLE_CONFLICT")
        return
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    descriptor = os.open(path, flags, stat.S_IRUSR | stat.S_IWUSR)
    try:
        remaining = memoryview(payload)
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:
                raise OSError("fixture write made no progress")
            remaining = remaining[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _load_bounded_json(path: Path) -> dict[str, object]:
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
        _assert_safe_public_strings(payload)
        return payload
    except ZoomPrivacyPipelineError:
        raise
    except Exception as exc:
        raise ZoomPrivacyPipelineError("ZOOM_PRIVACY_CONSENT_REJECTED") from exc


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


__all__ = [
    "ZOOM_FIXTURE_CLASSIFICATION",
    "ZOOM_PRIVACY_CONSENT_SCOPE",
    "ZOOM_PRIVACY_CONSENT_VERSION",
    "ZOOM_SANITIZED_FIXTURE_VERSION",
    "ZOOM_SANITIZED_REVIEW_VERSION",
    "ZoomPrivacyPipelineError",
    "ZoomPrivateCaptureDescriptor",
    "ZoomRawReviewConsent",
    "ZoomSanitizedFixtureCandidate",
    "ZoomSanitizedFixtureReviewReceipt",
    "ZoomSanitizedPacketObservation",
    "ZoomSanitizedProvenance",
    "approve_sanitized_fixture",
    "derive_sanitized_fixture_candidate",
    "load_zoom_raw_review_consent",
    "write_sanitized_fixture_bundle",
]
