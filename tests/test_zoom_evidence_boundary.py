from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json

import pytest

from exitspec.canonical import canonical_json_bytes
from exitspec.zoom_evidence_boundary import (
    ZOOM_LEGACY_CAPTURE_PLAN_VERSION,
    ZOOM_RUNTIME_ARTIFACTS,
    ZOOM_RUNTIME_EVIDENCE_PLAN_VERSION,
    ZOOM_SETUP_ARTIFACTS,
    ZOOM_SETUP_ATTESTATION_VERSION,
    ZoomEvidenceBoundaryError,
    ZoomRuntimeEvidencePlan,
    ZoomSetupAttestation,
    classify_zoom_schema,
    load_zoom_setup_attestation,
    setup_attestation_sha256,
    verify_runtime_setup_binding,
)


NOW = datetime(2026, 8, 25, 18, 0, tzinfo=timezone.utc)
DOMAIN_SETUP = b"exitspec-zoom-setup-attestation-v1\x00"
DOMAIN_RUNTIME = b"exitspec-zoom-runtime-evidence-v1\x00"


def _identity(prefix: str, domain: bytes, payload: dict[str, object]) -> str:
    return prefix + hashlib.sha256(
        domain + canonical_json_bytes(payload)
    ).hexdigest()


def _setup_payload(**updates: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": ZOOM_SETUP_ATTESTATION_VERSION,
        "attestation_id": "zoomsetup_" + "0" * 64,
        "attested_at": NOW.isoformat().replace("+00:00", "Z"),
        "provider": "ZOOM_RTMS",
        "app_type": "GENERAL_APP",
        "app_configuration_status": "VALIDATED",
        "endpoint_configuration_status": "VALIDATED",
        "crc_validation_status": "VALIDATED",
        "required_scopes": [
            "meeting:read:meeting_audio",
            "meeting:read:meeting_transcript",
            "meeting:update:participant_rtms_app_status",
        ],
        "provider_enforced_prerequisite_scopes": [
            "meeting:read:meeting_audio",
        ],
        "credential_rotation_status": "ROTATED_OR_DISABLED_OUTSIDE_REPO",
        "credential_rotation_receipt_id": "zoomcredrot_" + "1" * 64,
        "artifacts": [
            {
                "role": role,
                "byte_count": index + 1,
                "sha256": f"{index + 1:064x}",
            }
            for index, (role, _filename) in enumerate(ZOOM_SETUP_ARTIFACTS)
        ],
        "total_bytes": 6,
        "digest_algorithm": "sha256",
        "setup_artifacts_remain_private": True,
        "raw_setup_artifacts_parsed_by_this_contract": False,
        "may_authorize_runtime_capture": False,
        "may_call_zoom": False,
        "may_publish_fixture": False,
        "may_define_mapper": False,
        "may_confirm_contract": False,
        "may_freeze_contract": False,
        "may_start_measurement": False,
        "may_assign_verdict": False,
        "authority": "ZOOM_APP_SETUP_ATTESTATION_ONLY",
    }
    payload.update(updates)
    unsigned = dict(payload)
    unsigned.pop("attestation_id")
    payload["attestation_id"] = _identity("zoomsetup_", DOMAIN_SETUP, unsigned)
    return payload


def _setup() -> ZoomSetupAttestation:
    return ZoomSetupAttestation.model_validate(_setup_payload())


def _runtime_payload(attestation: ZoomSetupAttestation, **updates: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": ZOOM_RUNTIME_EVIDENCE_PLAN_VERSION,
        "plan_id": "zoomruntime_" + "0" * 64,
        "capture_id": "zoomcap_synthetic_runtime_001",
        "setup_attestation_id": attestation.attestation_id,
        "setup_attestation_sha256": setup_attestation_sha256(attestation),
        "capture_window_start_at": NOW.isoformat().replace("+00:00", "Z"),
        "capture_window_end_at": (
            NOW + timedelta(minutes=15)
        ).isoformat().replace("+00:00", "Z"),
        "retention_hours": 24,
        "runtime_artifact_roles": [role for role, _filename in ZOOM_RUNTIME_ARTIFACTS],
        "setup_artifacts_embedded": False,
        "raw_runtime_evidence_remains_private": True,
        "runtime_evidence_parsed_by_this_contract": False,
        "may_publish_fixture": False,
        "may_define_mapper": False,
        "may_confirm_contract": False,
        "may_freeze_contract": False,
        "may_start_measurement": False,
        "may_assign_verdict": False,
        "authority": "PRIVATE_SYNTHETIC_RUNTIME_EVIDENCE_ONLY",
    }
    payload.update(updates)
    unsigned = dict(payload)
    unsigned.pop("plan_id")
    payload["plan_id"] = _identity("zoomruntime_", DOMAIN_RUNTIME, unsigned)
    return payload


def test_setup_attestation_is_one_time_and_excludes_runtime_roles():
    attestation = _setup()

    assert tuple(record.role for record in attestation.artifacts) == tuple(
        role for role, _filename in ZOOM_SETUP_ARTIFACTS
    )
    assert {role for role, _filename in ZOOM_SETUP_ARTIFACTS}.isdisjoint(
        role for role, _filename in ZOOM_RUNTIME_ARTIFACTS
    )
    assert attestation.crc_validation_status == "VALIDATED"
    assert attestation.may_authorize_runtime_capture is False
    assert attestation.may_call_zoom is False


@pytest.mark.parametrize(
    "field,value",
    (
        ("crc_validation_status", "NOT_OBSERVED"),
        ("endpoint_configuration_status", "NOT_OBSERVED"),
        ("credential_rotation_status", "PENDING"),
        (
            "required_scopes",
            ["meeting:read:meeting_transcript"],
        ),
    ),
)
def test_setup_attestation_requires_complete_setup_and_rotation(field, value):
    payload = _setup_payload(**{field: value})

    with pytest.raises(ValueError):
        ZoomSetupAttestation.model_validate(payload)


def test_runtime_plan_contains_only_per_meeting_roles_and_binds_setup():
    attestation = _setup()
    plan = ZoomRuntimeEvidencePlan.model_validate(_runtime_payload(attestation))

    assert plan.setup_artifacts_embedded is False
    assert plan.runtime_artifact_roles == tuple(
        role for role, _filename in ZOOM_RUNTIME_ARTIFACTS
    )
    verify_runtime_setup_binding(plan, attestation)

    changed = attestation.model_copy(
        update={"credential_rotation_receipt_id": "zoomcredrot_" + "2" * 64}
    )
    with pytest.raises(ZoomEvidenceBoundaryError) as exc_info:
        verify_runtime_setup_binding(plan, changed)
    assert exc_info.value.code == "ZOOM_SETUP_ATTESTATION_MISMATCH"


def test_runtime_plan_rejects_setup_roles():
    attestation = _setup()
    payload = _runtime_payload(
        attestation,
        runtime_artifact_roles=[role for role, _filename in ZOOM_SETUP_ARTIFACTS],
    )

    with pytest.raises(ValueError):
        ZoomRuntimeEvidencePlan.model_validate(payload)


def test_legacy_schema_is_explicitly_private_and_not_migrated():
    assert classify_zoom_schema(ZOOM_LEGACY_CAPTURE_PLAN_VERSION) == (
        "LEGACY_V1_PRIVATE_CUSTODY_ONLY"
    )
    assert classify_zoom_schema(ZOOM_SETUP_ATTESTATION_VERSION) == (
        "ONE_TIME_SETUP_ATTESTATION_V1"
    )
    assert classify_zoom_schema(ZOOM_RUNTIME_EVIDENCE_PLAN_VERSION) == (
        "SETUP_BOUND_RUNTIME_EVIDENCE_V1"
    )
    with pytest.raises(ZoomEvidenceBoundaryError) as exc_info:
        classify_zoom_schema("exitspec.zoom-golden-capture-plan.v9")
    assert exc_info.value.code == "ZOOM_UNSUPPORTED_EVIDENCE_SCHEMA"


def test_setup_loader_rejects_duplicate_keys_and_secret_like_values_without_echo(
    tmp_path,
):
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text(
        '{"schema_version":"exitspec.zoom-rtms-setup-attestation.v1",'
        '"schema_version":"exitspec.zoom-rtms-setup-attestation.v1"}',
        encoding="utf-8",
    )
    with pytest.raises(ZoomEvidenceBoundaryError) as duplicate_exc:
        load_zoom_setup_attestation(duplicate)
    assert duplicate_exc.value.code == "ZOOM_SETUP_ATTESTATION_REJECTED"

    secret = "fw_private_synthetic_secret_123456789"
    secret_path = tmp_path / "secret.json"
    payload = _setup_payload(operator_note=secret)
    secret_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ZoomEvidenceBoundaryError) as secret_exc:
        load_zoom_setup_attestation(secret_path)
    assert secret not in str(secret_exc.value)
    assert secret_exc.value.code == "ZOOM_SETUP_ATTESTATION_REJECTED"


def test_setup_loader_rejects_symlinked_control_file(tmp_path):
    real = tmp_path / "real.json"
    real.write_text(json.dumps(_setup_payload()), encoding="utf-8")
    linked = tmp_path / "linked.json"
    linked.symlink_to(real)

    with pytest.raises(ZoomEvidenceBoundaryError) as exc_info:
        load_zoom_setup_attestation(linked)
    assert exc_info.value.code == "ZOOM_SETUP_ATTESTATION_REJECTED"
