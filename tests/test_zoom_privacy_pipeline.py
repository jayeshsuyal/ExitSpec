from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path

import pytest

from exitspec.canonical import canonical_json_bytes
from exitspec.zoom_privacy_pipeline import (
    ZOOM_PRIVACY_CONSENT_SCOPE,
    ZOOM_PRIVACY_CONSENT_VERSION,
    ZoomPrivateCaptureDescriptor,
    ZoomPrivacyPipelineError,
    ZoomRawReviewConsent,
    ZoomSanitizedPacketObservation,
    approve_sanitized_fixture,
    derive_sanitized_fixture_candidate,
    load_zoom_raw_review_consent,
    write_sanitized_fixture_bundle,
)


NOW = datetime(2026, 8, 25, 19, 0, tzinfo=timezone.utc)
CONSENT_DOMAIN = b"exitspec-zoom-privacy-consent-v1\x00"


def _digest(prefix: str, domain: bytes, payload: dict[str, object]) -> str:
    return prefix + hashlib.sha256(domain + canonical_json_bytes(payload)).hexdigest()


def _consent_payload(**updates: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": ZOOM_PRIVACY_CONSENT_VERSION,
        "consent_id": "zoomprivacyconsent_" + "0" * 64,
        "capture_id": "zoomcap_synthetic_private_001",
        "custody_manifest_id": "zoomcustody_" + "1" * 64,
        "reviewer_label": "privacy_reviewer_one",
        "consented_at": NOW.isoformat().replace("+00:00", "Z"),
        "scope": ZOOM_PRIVACY_CONSENT_SCOPE,
        "raw_capture_may_be_opened": True,
        "raw_bytes_may_be_exported": False,
        "raw_transcript_may_be_persisted": False,
        "synthetic_content_required": True,
        "customer_data_prohibited": True,
        "secret_scan_required": True,
        "candidate_publication_authorized": False,
        "may_decode_provider_payload": False,
        "may_create_product_source": False,
        "may_confirm_contract": False,
        "may_freeze_contract": False,
        "may_start_measurement": False,
        "may_assign_verdict": False,
    }
    payload.update(updates)
    unsigned = dict(payload)
    unsigned.pop("consent_id")
    payload["consent_id"] = _digest("zoomprivacyconsent_", CONSENT_DOMAIN, unsigned)
    return payload


def _consent() -> ZoomRawReviewConsent:
    return ZoomRawReviewConsent.model_validate(_consent_payload())


def _source() -> ZoomPrivateCaptureDescriptor:
    return ZoomPrivateCaptureDescriptor(
        capture_id="zoomcap_synthetic_private_001",
        custody_manifest_id="zoomcustody_" + "1" * 64,
        capture_plan_sha256="2" * 64,
        setup_attestation_sha256="3" * 64,
        runtime_plan_sha256="4" * 64,
        custody_manifest_sha256="5" * 64,
        credential_rotation_receipt_id="zoomcredrot_" + "6" * 64,
    )


def _observation(
    observation_id: str,
    arrival_index: int,
    kind: str,
    *,
    duplicate_of_sequence: int | None = None,
) -> ZoomSanitizedPacketObservation:
    transcript = kind in {"TRANSCRIPT_PARTIAL", "TRANSCRIPT_FINAL"}
    return ZoomSanitizedPacketObservation(
        observation_id=observation_id,
        arrival_index=arrival_index,
        protocol_sequence=arrival_index,
        wire_media_type=8 if transcript else 0,
        wire_message_type=17 if transcript else 1,
        message_kind=kind,
        speaker_slot="SPEAKER_1" if transcript else "NONE",
        transcript_finality=(
            "PARTIAL"
            if kind == "TRANSCRIPT_PARTIAL"
            else "FINAL"
            if kind == "TRANSCRIPT_FINAL"
            else "NOT_APPLICABLE"
        ),
        start_millisecond=arrival_index * 1000,
        duration_millisecond=250 if transcript else 0,
        payload_sha256=f"{arrival_index:064x}",
        payload_classification=(
            "TRANSCRIPT_TEXT_REDACTED"
            if transcript
            else "CONTROL_PAYLOAD_REDACTED"
        ),
        is_duplicate=duplicate_of_sequence is not None,
        duplicate_of_sequence=duplicate_of_sequence,
    )


def _observations() -> list[ZoomSanitizedPacketObservation]:
    return [
        _observation("obs_started", 1, "RTMS_STARTED"),
        _observation("obs_joined", 2, "PARTICIPANT_JOIN"),
        _observation("obs_partial", 3, "TRANSCRIPT_PARTIAL"),
        _observation("obs_final", 4, "TRANSCRIPT_FINAL"),
        _observation("obs_stopped", 5, "RTMS_STOPPED"),
    ]


def _candidate():
    return derive_sanitized_fixture_candidate(
        consent=_consent(),
        source=_source(),
        observations=_observations(),
    )


def test_explicit_consent_is_required_before_candidate_derivation():
    consent = _consent().model_copy(update={"raw_capture_may_be_opened": False})

    with pytest.raises(ZoomPrivacyPipelineError) as exc_info:
        derive_sanitized_fixture_candidate(
            consent=consent,
            source=_source(),
            observations=_observations(),
        )
    assert exc_info.value.code == "ZOOM_PRIVACY_CONSENT_REQUIRED"


def test_consent_and_private_descriptor_must_bind_exactly():
    changed_source = _source().model_copy(
        update={"capture_id": "zoomcap_other_private_001"}
    )

    with pytest.raises(ZoomPrivacyPipelineError) as exc_info:
        derive_sanitized_fixture_candidate(
            consent=_consent(),
            source=changed_source,
            observations=_observations(),
        )
    assert exc_info.value.code == "ZOOM_PRIVACY_CAPTURE_BINDING_MISMATCH"


def test_observation_contract_rejects_free_form_text_and_unsupported_shapes():
    payload = _observation("obs_text", 1, "TRANSCRIPT_FINAL").model_dump(mode="json")
    payload["text"] = "must never enter the candidate"
    with pytest.raises(ValueError):
        ZoomSanitizedPacketObservation.model_validate(payload)

    payload = _observation("obs_unknown", 1, "RTMS_STARTED").model_dump(mode="json")
    payload["message_kind"] = "UNSUPPORTED_PROVIDER_EVENT"
    with pytest.raises(ValueError):
        ZoomSanitizedPacketObservation.model_validate(payload)


def test_candidate_contains_protocol_shape_but_no_raw_or_private_values():
    candidate = _candidate()
    serialized = canonical_json_bytes(candidate.model_dump(mode="json")).decode()

    assert candidate.fixture_classification == "SANITIZED_SYNTHETIC_CONFORMANCE_FIXTURE"
    assert candidate.privacy_review_status == "REVIEW_PENDING"
    assert candidate.candidate_contains_free_form_text is False
    assert candidate.transcript_text_removed is True
    assert candidate.observations[2].wire_media_type == 8
    assert candidate.observations[2].wire_message_type == 17
    assert _source().capture_id not in serialized
    assert _source().custody_manifest_id not in serialized
    assert "must never enter the candidate" not in serialized
    assert "https://" not in serialized


def test_duplicate_and_oversized_observations_fail_closed():
    observations = _observations()
    observations[1] = observations[0].model_copy(
        update={"observation_id": "obs_started_duplicate"}
    )
    with pytest.raises(ZoomPrivacyPipelineError) as duplicate_exc:
        derive_sanitized_fixture_candidate(
            consent=_consent(), source=_source(), observations=observations
        )
    assert duplicate_exc.value.code == "ZOOM_PRIVACY_CANDIDATE_REJECTED"

    too_many = [
        _observation(f"obs_{index:03d}", index, "RTMS_STARTED")
        for index in range(1, 257)
    ] + [_observation("obs_extra", 1, "RTMS_STARTED")]
    with pytest.raises(ZoomPrivacyPipelineError) as limit_exc:
        derive_sanitized_fixture_candidate(
            consent=_consent(), source=_source(), observations=too_many
        )
    assert limit_exc.value.code == "ZOOM_PRIVACY_OBSERVATION_LIMIT"


def test_loader_reads_only_consent_and_rejects_duplicates_and_symlinks(tmp_path):
    consent_path = tmp_path / "privacy-consent.json"
    consent_path.write_text(json.dumps(_consent_payload()), encoding="utf-8")
    assert load_zoom_raw_review_consent(consent_path).capture_id == _source().capture_id

    duplicate_path = tmp_path / "duplicate.json"
    duplicate_path.write_text(
        '{"schema_version":"exitspec.zoom-rtms-raw-review-consent.v1",'
        '"schema_version":"exitspec.zoom-rtms-raw-review-consent.v1"}',
        encoding="utf-8",
    )
    with pytest.raises(ZoomPrivacyPipelineError) as duplicate_exc:
        load_zoom_raw_review_consent(duplicate_path)
    assert duplicate_exc.value.code == "ZOOM_PRIVACY_CONSENT_REJECTED"

    linked = tmp_path / "linked.json"
    linked.symlink_to(consent_path)
    with pytest.raises(ZoomPrivacyPipelineError) as symlink_exc:
        load_zoom_raw_review_consent(linked)
    assert symlink_exc.value.code == "ZOOM_PRIVACY_CONSENT_REJECTED"


def test_keep_private_receipt_cannot_publish():
    candidate = _candidate()
    receipt = approve_sanitized_fixture(
        candidate=candidate,
        consent=_consent(),
        reviewer_label="privacy_reviewer_two",
        secret_scan_tool="gitleaks-8.27",
        decision="KEEP_PRIVATE",
        reviewed_at=NOW + timedelta(minutes=1),
    )
    assert receipt.fixture_publication_authorized is False
    with pytest.raises(ZoomPrivacyPipelineError) as exc_info:
        write_sanitized_fixture_bundle(
            Path("/tmp/should-not-write"), candidate, receipt
        )
    assert exc_info.value.code == "ZOOM_PRIVACY_PUBLICATION_NOT_AUTHORIZED"


def test_approved_bundle_is_immutable_provenance_bound_and_replay_safe(tmp_path):
    candidate = _candidate()
    consent = _consent()
    receipt = approve_sanitized_fixture(
        candidate=candidate,
        consent=consent,
        reviewer_label="privacy_reviewer_two",
        secret_scan_tool="gitleaks-8.27",
        decision="APPROVED_FOR_DECODER_TESTS",
        reviewed_at=NOW + timedelta(minutes=1),
    )
    output = tmp_path / "approved-fixture"
    first_paths = write_sanitized_fixture_bundle(output, candidate, receipt)
    replay_paths = write_sanitized_fixture_bundle(output, candidate, receipt)

    assert replay_paths == first_paths
    assert all(path.stat().st_mode & 0o077 == 0 for path in first_paths)
    fixture_text = first_paths[0].read_text("utf-8")
    receipt_text = first_paths[1].read_text("utf-8")
    assert candidate.fixture_id in fixture_text
    assert candidate.provenance.capture_id_sha256 in fixture_text
    assert _source().capture_id not in fixture_text
    assert "privacy_reviewer_two" not in receipt_text
    assert not (output / "raw").exists()

    changed_receipt = approve_sanitized_fixture(
        candidate=candidate,
        consent=consent,
        reviewer_label="privacy_reviewer_three",
        secret_scan_tool="gitleaks-8.27",
        decision="APPROVED_FOR_DECODER_TESTS",
        reviewed_at=NOW + timedelta(minutes=2),
    )
    with pytest.raises(ZoomPrivacyPipelineError) as conflict_exc:
        write_sanitized_fixture_bundle(output, candidate, changed_receipt)
    assert conflict_exc.value.code == "ZOOM_PRIVACY_BUNDLE_CONFLICT"


def test_bundle_cannot_write_inside_private_capture_workspace(tmp_path):
    candidate = _candidate()
    receipt = approve_sanitized_fixture(
        candidate=candidate,
        consent=_consent(),
        reviewer_label="privacy_reviewer_two",
        secret_scan_tool="gitleaks-8.27",
        decision="APPROVED_FOR_DECODER_TESTS",
        reviewed_at=NOW + timedelta(minutes=1),
    )
    private_output = tmp_path / ".zoom-fixture-private" / "candidate"
    with pytest.raises(ZoomPrivacyPipelineError) as exc_info:
        write_sanitized_fixture_bundle(private_output, candidate, receipt)
    assert exc_info.value.code == "ZOOM_PRIVACY_BUNDLE_WRITE_FAILED"
