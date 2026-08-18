from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import stat

import pytest

from exitspec.canonical import canonical_json_bytes
from exitspec.zoom_fixture_capture import (
    PRIVATE_CAPTURE_ROOT,
    ZOOM_CAPTURE_ARTIFACTS,
    ZOOM_CAPTURE_AUTHORITY,
    ZoomFixtureCaptureError,
    ZoomFixtureCaptureFailureCode,
    initialize_zoom_fixture_capture,
    load_zoom_capture_plan,
    record_zoom_fixture_privacy_review,
    seal_zoom_fixture_capture,
    verify_zoom_fixture_capture,
    verify_zoom_fixture_preflight,
)


NOW = datetime(2026, 8, 11, 17, 0, tzinfo=timezone.utc)
CAPTURE_ID = "zoomcap_synthetic_two_party_001"
DISCLOSURE_SHA256 = "4" * 64
PRIVATE_MARKER = "synthetic-private-zoom-marker-88421"


def _repository(tmp_path: Path, *, include_ignore: bool = True) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".git").mkdir()
    ignore = ".zoom-fixture-private/\n" if include_ignore else "runs/\n"
    (root / ".gitignore").write_text(ignore, encoding="utf-8")
    return root


def _plan_payload(**updates: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": "exitspec.zoom-golden-capture-plan.v1",
        "capture_id": CAPTURE_ID,
        "provider": "ZOOM_RTMS",
        "app_type": "GENERAL_APP",
        "source_classification": "SYNTHETIC_TWO_PARTICIPANT_ONLY",
        "requested_media": ["transcript"],
        "excluded_media": ["audio", "video", "screen_share", "chat"],
        "preflight": {
            "general_app_reviewed": True,
            "rtms_enabled": True,
            "rtms_credits_confirmed": True,
            "webhook_capture_ready": True,
            "transcript_capture_ready": True,
            "required_scopes": [
                "meeting:read:meeting_audio",
                "meeting:read:meeting_transcript",
                "meeting:update:participant_rtms_app_status",
            ],
            "provider_enforced_prerequisite_scopes": [
                "meeting:read:meeting_audio",
            ],
            "operator_attestations_only": True,
            "provider_state_independently_verified": False,
        },
        "participants": [
            {
                "label": "synthetic_host",
                "role": "HOST",
                "consent_recorded": True,
                "consented_at": (NOW - timedelta(minutes=10)).isoformat(),
                "disclosure_sha256": DISCLOSURE_SHA256,
            },
            {
                "label": "synthetic_guest",
                "role": "GUEST",
                "consent_recorded": True,
                "consented_at": (NOW - timedelta(minutes=9)).isoformat(),
                "disclosure_sha256": DISCLOSURE_SHA256,
            },
        ],
        "scheduled_start_at": NOW.isoformat(),
        "scheduled_end_at": (NOW + timedelta(minutes=15)).isoformat(),
        "retention_hours": 24,
        "public_fixture_requires_privacy_review": True,
        "customer_data_prohibited": True,
        "capture_kit_grants_network_authority": False,
        "capture_kit_grants_mapper_authority": False,
        "capture_kit_grants_product_decision_authority": False,
    }
    payload.update(updates)
    return payload


def _write_plan(root: Path, payload: dict[str, object] | None = None) -> Path:
    path = root / "capture-plan.json"
    path.write_text(
        json.dumps(payload or _plan_payload(), indent=2),
        encoding="utf-8",
    )
    return path


def _preflight(tmp_path: Path) -> tuple[Path, Path]:
    root = _repository(tmp_path)
    plan_path = _write_plan(root)
    initialize_zoom_fixture_capture(
        plan_path,
        root,
        checked_at=NOW - timedelta(minutes=5),
    )
    workspace = root / PRIVATE_CAPTURE_ROOT / CAPTURE_ID
    return root, workspace


def _write_complete_capture(workspace: Path) -> None:
    raw = workspace / "raw"
    for index, (role, filename) in enumerate(ZOOM_CAPTURE_ARTIFACTS):
        marker = PRIVATE_MARKER if index == 0 else "synthetic"
        (raw / filename).write_bytes(f"{marker}:{role}:{index}".encode("utf-8"))


def _sealed_capture(tmp_path: Path) -> tuple[Path, Path, object]:
    root, workspace = _preflight(tmp_path)
    _write_complete_capture(workspace)
    manifest = seal_zoom_fixture_capture(root, CAPTURE_ID, sealed_at=NOW)
    return root, workspace, manifest


def _review_payload(manifest_id: str, **updates: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": "exitspec.zoom-golden-privacy-review.v1",
        "capture_id": CAPTURE_ID,
        "custody_manifest_id": manifest_id,
        "reviewer_label": "synthetic_reviewer",
        "reviewed_at": (NOW + timedelta(minutes=1)).isoformat(),
        "original_remains_private": True,
        "synthetic_content_verified": True,
        "customer_data_absent": True,
        "provider_secrets_absent_from_candidate": True,
        "provider_identifiers_removed_or_documented": True,
        "secret_scan_completed": True,
        "secret_scan_tool": "gitleaks-8.27",
        "transformations_documented": True,
        "transformation_notes": ["synthetic identifiers normalized"],
        "original_signature_after_redaction": "NOT_CLAIMED",
        "decision": "SANITIZED_CANDIDATE_READY_FOR_REVIEW",
        "candidate_publication_authorized": False,
    }
    payload.update(updates)
    return payload


def _write_review(
    root: Path,
    manifest_id: str,
    **updates: object,
) -> Path:
    path = root / "privacy-review.json"
    path.write_text(
        json.dumps(_review_payload(manifest_id, **updates), indent=2),
        encoding="utf-8",
    )
    return path


def _failure_code(exc_info: pytest.ExceptionInfo[ZoomFixtureCaptureError]) -> str:
    return exc_info.value.failure_code.value


def test_preflight_creates_private_content_free_workspace_and_exact_replay(
    tmp_path: Path,
):
    root = _repository(tmp_path)
    plan_path = _write_plan(root)

    first = initialize_zoom_fixture_capture(
        plan_path,
        root,
        checked_at=NOW - timedelta(minutes=5),
    )
    replay = initialize_zoom_fixture_capture(
        plan_path,
        root,
        checked_at=NOW + timedelta(days=1),
    )

    workspace = root / PRIVATE_CAPTURE_ROOT / CAPTURE_ID
    assert replay == first
    assert first.authority == ZOOM_CAPTURE_AUTHORITY
    assert first.provider_state_independently_verified is False
    assert first.may_call_zoom is False
    assert first.may_publish_fixture is False
    assert first.may_define_mapper is False
    assert first.may_assign_verdict is False
    assert tuple(path.name for path in (workspace / "raw").iterdir()) == ()
    assert stat.S_IMODE(workspace.stat().st_mode) == 0o700
    assert stat.S_IMODE((workspace / "raw").stat().st_mode) == 0o700
    public = canonical_json_bytes(first.model_dump(mode="json")).decode("utf-8")
    assert str(root) not in public
    assert PRIVATE_MARKER not in public

    verified = verify_zoom_fixture_preflight(root, CAPTURE_ID, checked_at=NOW)
    assert verified == first


def test_preflight_verification_rejects_canonical_control_file_tampering(
    tmp_path: Path,
):
    root, workspace = _preflight(tmp_path)
    receipt_path = workspace / "preflight-receipt.json"
    payload = json.loads(receipt_path.read_bytes())
    payload["may_call_zoom"] = True
    receipt_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ZoomFixtureCaptureError) as exc_info:
        verify_zoom_fixture_preflight(root, CAPTURE_ID, checked_at=NOW)

    assert _failure_code(exc_info) == "ZOOM_FIXTURE_CAPTURE_INTEGRITY_FAILED"


def test_preflight_verification_expires_after_the_capture_window(tmp_path: Path):
    root, _workspace = _preflight(tmp_path)

    with pytest.raises(ZoomFixtureCaptureError) as exc_info:
        verify_zoom_fixture_preflight(
            root,
            CAPTURE_ID,
            checked_at=NOW + timedelta(minutes=16),
        )

    assert _failure_code(exc_info) == "ZOOM_FIXTURE_PLAN_REJECTED"


def test_preflight_rejects_same_capture_id_with_changed_plan(tmp_path: Path):
    root = _repository(tmp_path)
    first_plan = _write_plan(root)
    initialize_zoom_fixture_capture(first_plan, root, checked_at=NOW)
    changed = _plan_payload(scheduled_end_at=(NOW + timedelta(minutes=16)).isoformat())
    changed_path = root / "changed-plan.json"
    changed_path.write_text(json.dumps(changed), encoding="utf-8")

    with pytest.raises(ZoomFixtureCaptureError) as exc_info:
        initialize_zoom_fixture_capture(changed_path, root, checked_at=NOW)

    assert _failure_code(exc_info) == "ZOOM_FIXTURE_WORKSPACE_CONFLICT"


def test_preflight_requires_recorded_consent_and_future_capture_window(tmp_path: Path):
    root = _repository(tmp_path)
    plan_path = _write_plan(root)

    with pytest.raises(ZoomFixtureCaptureError) as future_consent_exc:
        initialize_zoom_fixture_capture(
            plan_path,
            root,
            checked_at=NOW - timedelta(minutes=11),
        )
    assert _failure_code(future_consent_exc) == "ZOOM_FIXTURE_PLAN_REJECTED"
    assert not (root / PRIVATE_CAPTURE_ROOT / CAPTURE_ID / "capture-plan.json").exists()

    with pytest.raises(ZoomFixtureCaptureError) as stale_plan_exc:
        initialize_zoom_fixture_capture(
            plan_path,
            root,
            checked_at=NOW + timedelta(seconds=1),
        )
    assert _failure_code(stale_plan_exc) == "ZOOM_FIXTURE_PLAN_REJECTED"


def test_repository_example_plan_matches_the_executable_schema():
    example = (
        Path(__file__).parents[1]
        / "examples"
        / "meeting"
        / "zoom-golden-capture-plan-v1.example.json"
    )

    plan = load_zoom_capture_plan(example)

    assert plan.capture_id == CAPTURE_ID
    assert plan.requested_media == ("transcript",)
    assert plan.excluded_media == ("audio", "video", "screen_share", "chat")
    assert plan.preflight.required_scopes == (
        "meeting:read:meeting_audio",
        "meeting:read:meeting_transcript",
        "meeting:update:participant_rtms_app_status",
    )
    assert plan.preflight.provider_enforced_prerequisite_scopes == (
        "meeting:read:meeting_audio",
    )
    assert plan.capture_kit_grants_network_authority is False


@pytest.mark.parametrize(
    "scope_update",
    (
        {
            "required_scopes": [
                "meeting:read:meeting_transcript",
                "meeting:update:participant_rtms_app_status",
            ],
        },
        {"provider_enforced_prerequisite_scopes": []},
    ),
)
def test_capture_plan_rejects_zoom_scope_boundary_drift(
    tmp_path: Path,
    scope_update: dict[str, object],
):
    root = _repository(tmp_path)
    payload = _plan_payload()
    preflight = payload["preflight"]
    assert isinstance(preflight, dict)
    preflight.update(scope_update)
    path = _write_plan(root, payload)

    with pytest.raises(ZoomFixtureCaptureError) as exc_info:
        load_zoom_capture_plan(path)

    assert _failure_code(exc_info) == "ZOOM_FIXTURE_PLAN_REJECTED"


@pytest.mark.parametrize(
    "mutation",
    (
        {"requested_media": ["audio", "transcript"]},
        {"excluded_media": ["video"]},
        {"retention_hours": 25},
        {"capture_kit_grants_network_authority": True},
    ),
)
def test_capture_plan_rejects_unsafe_or_out_of_contract_values(
    tmp_path: Path,
    mutation: dict[str, object],
):
    root = _repository(tmp_path)
    path = _write_plan(root, _plan_payload(**mutation))

    with pytest.raises(ZoomFixtureCaptureError) as exc_info:
        load_zoom_capture_plan(path)

    assert _failure_code(exc_info) == "ZOOM_FIXTURE_PLAN_REJECTED"


def test_capture_plan_requires_prior_matching_consent(tmp_path: Path):
    root = _repository(tmp_path)
    participants = _plan_payload()["participants"]
    assert isinstance(participants, list)
    participants[1]["consented_at"] = (NOW + timedelta(seconds=1)).isoformat()
    participants[1]["disclosure_sha256"] = "5" * 64
    path = _write_plan(root, _plan_payload(participants=participants))

    with pytest.raises(ZoomFixtureCaptureError):
        load_zoom_capture_plan(path)


def test_capture_plan_rejects_secret_like_value_without_echo(tmp_path: Path):
    root = _repository(tmp_path)
    secret = "fw_private_synthetic_secret_123456789"
    payload = _plan_payload(unexpected=secret)
    path = _write_plan(root, payload)

    with pytest.raises(ZoomFixtureCaptureError) as exc_info:
        load_zoom_capture_plan(path)

    assert secret not in str(exc_info.value)
    assert _failure_code(exc_info) == "ZOOM_FIXTURE_PLAN_REJECTED"


def test_capture_plan_rejects_duplicate_json_keys(tmp_path: Path):
    root = _repository(tmp_path)
    path = root / "duplicate-plan.json"
    path.write_text(
        '{"schema_version":"exitspec.zoom-golden-capture-plan.v1",'
        '"schema_version":"exitspec.zoom-golden-capture-plan.v1"}',
        encoding="utf-8",
    )

    with pytest.raises(ZoomFixtureCaptureError):
        load_zoom_capture_plan(path)


def test_preflight_requires_explicit_git_ignore_rule(tmp_path: Path):
    root = _repository(tmp_path, include_ignore=False)
    plan_path = _write_plan(root)

    with pytest.raises(ZoomFixtureCaptureError) as exc_info:
        initialize_zoom_fixture_capture(plan_path, root, checked_at=NOW)

    assert _failure_code(exc_info) == "ZOOM_FIXTURE_REPOSITORY_NOT_READY"
    assert not (root / PRIVATE_CAPTURE_ROOT).exists()


def test_capture_plan_symlink_is_rejected(tmp_path: Path):
    root = _repository(tmp_path)
    real_plan = _write_plan(root)
    linked = root / "linked-plan.json"
    linked.symlink_to(real_plan)

    with pytest.raises(ZoomFixtureCaptureError):
        load_zoom_capture_plan(linked)


def test_complete_capture_seals_content_free_inventory_and_verifies(tmp_path: Path):
    root, workspace = _preflight(tmp_path)
    _write_complete_capture(workspace)

    manifest = seal_zoom_fixture_capture(root, CAPTURE_ID, sealed_at=NOW)
    replay = seal_zoom_fixture_capture(
        root,
        CAPTURE_ID,
        sealed_at=NOW + timedelta(days=1),
    )
    verified = verify_zoom_fixture_capture(root, CAPTURE_ID)

    assert replay == manifest == verified
    assert tuple(record.role for record in manifest.artifacts) == tuple(
        role for role, _ in ZOOM_CAPTURE_ARTIFACTS
    )
    assert manifest.raw_artifacts_parsed is False
    assert manifest.raw_artifacts_remain_private is True
    assert manifest.wire_schema_frozen is False
    assert manifest.may_publish_fixture is False
    assert manifest.may_define_mapper is False
    assert manifest.may_assign_verdict is False
    serialized = (workspace / "custody-manifest.json").read_text("utf-8")
    assert PRIVATE_MARKER not in serialized
    assert str(root) not in serialized
    assert all(
        not (workspace / "raw" / filename).stat().st_mode & 0o222
        for _, filename in ZOOM_CAPTURE_ARTIFACTS
    )
    assert not (workspace / "raw").stat().st_mode & 0o222


def test_preflight_exact_replay_remains_available_after_sealing(tmp_path: Path):
    root = _repository(tmp_path)
    plan_path = _write_plan(root)
    first = initialize_zoom_fixture_capture(
        plan_path,
        root,
        checked_at=NOW - timedelta(minutes=5),
    )
    workspace = root / PRIVATE_CAPTURE_ROOT / CAPTURE_ID
    _write_complete_capture(workspace)
    seal_zoom_fixture_capture(root, CAPTURE_ID, sealed_at=NOW)

    replay = initialize_zoom_fixture_capture(
        plan_path,
        root,
        checked_at=NOW + timedelta(days=1),
    )

    assert replay == first


def test_missing_or_extra_artifact_fails_closed(tmp_path: Path):
    root, workspace = _preflight(tmp_path)
    _write_complete_capture(workspace)
    missing = workspace / "raw" / ZOOM_CAPTURE_ARTIFACTS[-1][1]
    missing.unlink()

    with pytest.raises(ZoomFixtureCaptureError) as missing_exc:
        seal_zoom_fixture_capture(root, CAPTURE_ID, sealed_at=NOW)
    assert _failure_code(missing_exc) == "ZOOM_FIXTURE_CAPTURE_INCOMPLETE"

    missing.write_bytes(b"synthetic-restored")
    (workspace / "raw" / "unexpected.bin").write_bytes(b"synthetic-extra")
    with pytest.raises(ZoomFixtureCaptureError) as extra_exc:
        seal_zoom_fixture_capture(root, CAPTURE_ID, sealed_at=NOW)
    assert _failure_code(extra_exc) == "ZOOM_FIXTURE_CAPTURE_INCOMPLETE"


def test_symlink_or_hardlink_artifact_fails_closed(tmp_path: Path):
    root, workspace = _preflight(tmp_path)
    _write_complete_capture(workspace)
    first = workspace / "raw" / ZOOM_CAPTURE_ARTIFACTS[0][1]
    first.unlink()
    outside = root / "outside.bin"
    outside.write_bytes(b"synthetic-outside")
    first.symlink_to(outside)

    with pytest.raises(ZoomFixtureCaptureError) as symlink_exc:
        seal_zoom_fixture_capture(root, CAPTURE_ID, sealed_at=NOW)
    assert _failure_code(symlink_exc) == "ZOOM_FIXTURE_CAPTURE_INTEGRITY_FAILED"

    first.unlink()
    os.link(outside, first)
    with pytest.raises(ZoomFixtureCaptureError) as hardlink_exc:
        seal_zoom_fixture_capture(root, CAPTURE_ID, sealed_at=NOW)
    assert _failure_code(hardlink_exc) == "ZOOM_FIXTURE_CAPTURE_INTEGRITY_FAILED"


def test_mutation_after_sealing_is_detected(tmp_path: Path):
    root, workspace, _ = _sealed_capture(tmp_path)
    target = workspace / "raw" / ZOOM_CAPTURE_ARTIFACTS[0][1]
    os.chmod(workspace / "raw", 0o700)
    os.chmod(target, 0o600)
    target.write_bytes(b"mutated-private-bytes")

    with pytest.raises(ZoomFixtureCaptureError) as exc_info:
        verify_zoom_fixture_capture(root, CAPTURE_ID)

    assert _failure_code(exc_info) == "ZOOM_FIXTURE_CAPTURE_INTEGRITY_FAILED"


def test_manifest_mutation_is_detected(tmp_path: Path):
    root, workspace, _ = _sealed_capture(tmp_path)
    manifest_path = workspace / "custody-manifest.json"
    os.chmod(manifest_path, 0o600)
    payload = json.loads(manifest_path.read_bytes())
    payload["total_bytes"] += 1
    manifest_path.write_bytes(canonical_json_bytes(payload))
    os.chmod(manifest_path, 0o400)

    with pytest.raises(ZoomFixtureCaptureError) as exc_info:
        verify_zoom_fixture_capture(root, CAPTURE_ID)

    assert _failure_code(exc_info) == "ZOOM_FIXTURE_CAPTURE_INTEGRITY_FAILED"


def test_complete_privacy_review_is_immutable_and_grants_no_authority(tmp_path: Path):
    root, workspace, manifest = _sealed_capture(tmp_path)
    review_path = _write_review(root, manifest.manifest_id)

    receipt = record_zoom_fixture_privacy_review(
        root,
        CAPTURE_ID,
        review_path,
    )
    replay = record_zoom_fixture_privacy_review(
        root,
        CAPTURE_ID,
        review_path,
    )

    assert replay == receipt
    assert receipt.decision == "SANITIZED_CANDIDATE_READY_FOR_REVIEW"
    assert receipt.original_signature_after_redaction == "NOT_CLAIMED"
    assert receipt.candidate_publication_authorized is False
    assert receipt.mapper_implementation_authorized is False
    assert receipt.network_transport_authorized is False
    assert receipt.product_decision_authorized is False
    public = canonical_json_bytes(receipt.model_dump(mode="json")).decode("utf-8")
    assert "synthetic_reviewer" not in public
    assert PRIVATE_MARKER not in public
    assert str(root) not in public
    assert (
        not (workspace / "review" / "privacy-review-receipt.json").stat().st_mode
        & 0o222
    )


@pytest.mark.parametrize(
    "updates",
    (
        {"synthetic_content_verified": False},
        {"customer_data_absent": False},
        {"secret_scan_completed": False},
        {"original_signature_after_redaction": "VERIFIED"},
        {"candidate_publication_authorized": True},
        {"reviewed_at": (NOW - timedelta(minutes=1)).isoformat()},
        {"transformation_notes": []},
    ),
)
def test_incomplete_or_overclaiming_privacy_review_is_rejected(
    tmp_path: Path,
    updates: dict[str, object],
):
    root, _, manifest = _sealed_capture(tmp_path)
    review_path = _write_review(root, manifest.manifest_id, **updates)

    with pytest.raises(ZoomFixtureCaptureError) as exc_info:
        record_zoom_fixture_privacy_review(root, CAPTURE_ID, review_path)

    assert _failure_code(exc_info) == "ZOOM_FIXTURE_PRIVACY_REVIEW_REJECTED"


def test_second_different_privacy_review_conflicts(tmp_path: Path):
    root, _, manifest = _sealed_capture(tmp_path)
    first = _write_review(root, manifest.manifest_id)
    record_zoom_fixture_privacy_review(root, CAPTURE_ID, first)
    changed = root / "changed-review.json"
    changed.write_text(
        json.dumps(
            _review_payload(
                manifest.manifest_id,
                reviewer_label="second_reviewer",
            )
        ),
        encoding="utf-8",
    )

    with pytest.raises(ZoomFixtureCaptureError) as exc_info:
        record_zoom_fixture_privacy_review(root, CAPTURE_ID, changed)

    assert _failure_code(exc_info) == "ZOOM_FIXTURE_PRIVACY_REVIEW_CONFLICT"


def test_failure_messages_do_not_echo_capture_id_path_or_private_value(tmp_path: Path):
    root = _repository(tmp_path)
    unsafe_capture_id = f"zoomcap_{PRIVATE_MARKER}"

    with pytest.raises(ZoomFixtureCaptureError) as exc_info:
        verify_zoom_fixture_capture(root, unsafe_capture_id)

    message = str(exc_info.value)
    assert PRIVATE_MARKER not in message
    assert str(root) not in message
    assert exc_info.value.failure_code in {
        ZoomFixtureCaptureFailureCode.WORKSPACE_UNSAFE,
        ZoomFixtureCaptureFailureCode.REPOSITORY_NOT_READY,
    }
