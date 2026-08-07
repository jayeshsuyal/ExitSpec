from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sqlite3

import pytest

from exitspec.meeting_connector import (
    MEETING_CONNECTOR_AUTHORITY,
    MeetingCaptureAuthorization,
    MeetingCaptureIntent,
    MeetingConnectorDenied,
    MeetingConnectorPolicy,
    MeetingEventKind,
    MeetingTranscriptEvent,
    MeetingTransportBinding,
    authorize_meeting_capture,
    meeting_identity_sha256,
    seal_meeting_transcript_window,
    stream_identity_sha256,
)
from exitspec.meeting_event_inbox import (
    MeetingEventInboxCapacityError,
    MeetingEventInboxConflict,
    MeetingEventInboxIntegrityError,
    MeetingEventInboxPayloadExpired,
    MeetingInboxDisposition,
    PrivateMeetingEventInboxSerializationError,
    SQLiteMeetingEventInbox,
)
from exitspec.stt_boundary import MeetingConsentAttestation, STTConsentState


NOW = datetime(2026, 8, 6, 20, 0, tzinfo=timezone.utc)
NOTICE_SHA256 = "a" * 64
MEETING_ID = "meeting_zoom_synthetic_001"
STREAM_ID = "stream_zoom_synthetic_001"
EMPLOYEE_ID = "participant_employee_001"
CUSTOMER_ID = "participant_customer_001"
BINDING_ID = "meetbind_" + "b" * 64
ADAPTER_ID = "zoom-rtms-transcript"
ADAPTER_VERSION = "v1"
PRIVATE_MARKER = "private-inbox-marker-7291"


@dataclass
class MutableClock:
    value: datetime

    def __call__(self) -> datetime:
        return self.value


def _policy(**updates: object) -> MeetingConnectorPolicy:
    values: dict[str, object] = {
        "policy_id": "meetpolicy_zoom_synthetic_v1",
        "policy_version": "v1",
        "provider": "zoom",
        "adapter_id": ADAPTER_ID,
        "adapter_version": ADAPTER_VERSION,
        "consent_notice_sha256": NOTICE_SHA256,
        "max_event_count": 100,
        "max_transcript_characters": 10_000,
        "max_window_seconds": 3_600,
        "reviewed_at": NOW - timedelta(days=1),
        "expires_at": NOW + timedelta(days=30),
    }
    values.update(updates)
    return MeetingConnectorPolicy(**values)


def _consent(**updates: object) -> MeetingConsentAttestation:
    values: dict[str, object] = {
        "attestation_id": "consent_zoom_synthetic_001",
        "meeting_id": MEETING_ID,
        "participant_ids": (EMPLOYEE_ID, CUSTOMER_ID),
        "consented_participant_ids": (EMPLOYEE_ID, CUSTOMER_ID),
        "recording_notice_acknowledged": True,
        "consent_notice_sha256": NOTICE_SHA256,
        "state": STTConsentState.GRANTED,
        "attested_by": "employee:demo",
        "attested_at": NOW - timedelta(minutes=2),
    }
    values.update(updates)
    return MeetingConsentAttestation(**values)


def _authorization(
    *,
    policy: MeetingConnectorPolicy | None = None,
) -> MeetingCaptureAuthorization:
    consent = _consent()
    intent = MeetingCaptureIntent(
        request_id="meetreq_zoom_synthetic_001",
        poc_id="poc_zoom_synthetic",
        provider="zoom",
        adapter_id=ADAPTER_ID,
        adapter_version=ADAPTER_VERSION,
        meeting_id=MEETING_ID,
        organizer_participant_id=EMPLOYEE_ID,
        participant_ids=(EMPLOYEE_ID, CUSTOMER_ID),
        consent=consent,
        requested_at=NOW - timedelta(minutes=1),
    )
    return authorize_meeting_capture(policy or _policy(), intent, now=NOW)


def _binding(
    authorization: MeetingCaptureAuthorization | None = None,
    **updates: object,
) -> MeetingTransportBinding:
    authorization = authorization or _authorization()
    values: dict[str, object] = {
        "binding_id": BINDING_ID,
        "authorization_id": authorization.authorization_id,
        "provider": "zoom",
        "adapter_id": ADAPTER_ID,
        "adapter_version": ADAPTER_VERSION,
        "meeting_identity_sha256": meeting_identity_sha256(MEETING_ID),
        "stream_identity_sha256": stream_identity_sha256(STREAM_ID),
        "webhook_event_sha256": "c" * 64,
        "webhook_signature_verified": True,
        "websocket_handshake_authenticated": True,
        "protocol_version": "v1",
        "established_at": NOW + timedelta(seconds=10),
        "expires_at": NOW + timedelta(hours=1),
    }
    values.update(updates)
    return MeetingTransportBinding(**values)


def _event(
    sequence: int,
    kind: MeetingEventKind,
    **updates: object,
) -> MeetingTranscriptEvent:
    values: dict[str, object] = {
        "event_id": f"mev_event_{sequence:03d}",
        "adapter_id": ADAPTER_ID,
        "adapter_version": ADAPTER_VERSION,
        "meeting_id": MEETING_ID,
        "stream_id": STREAM_ID,
        "transport_binding_id": BINDING_ID,
        "sequence": sequence,
        "kind": kind,
        "received_at": NOW + timedelta(seconds=10 + sequence),
    }
    if kind is MeetingEventKind.STREAM_STARTED:
        values["participant_ids"] = (EMPLOYEE_ID, CUSTOMER_ID)
    elif kind is MeetingEventKind.TRANSCRIPT_SEGMENT:
        values.update(
            {
                "participant_id": EMPLOYEE_ID,
                "participant_label": "Synthetic employee",
                "transcript_text": (
                    "Accuracy must be at least 95% across 200 cases."
                ),
                "provider_timestamp_ms": 1_000 + sequence,
                "segment_start_ms": 1_000 * sequence,
                "segment_end_ms": 1_000 * sequence + 500,
            }
        )
    elif kind in {
        MeetingEventKind.PARTICIPANT_JOINED,
        MeetingEventKind.PARTICIPANT_LEFT,
    }:
        values["participant_id"] = CUSTOMER_ID
    elif kind is MeetingEventKind.STREAM_STOPPED:
        values["stop_reason"] = "operator_stopped"
    values.update(updates)
    return MeetingTranscriptEvent(**values)


def _happy_events() -> tuple[MeetingTranscriptEvent, ...]:
    return (
        _event(1, MeetingEventKind.STREAM_STARTED),
        _event(2, MeetingEventKind.TRANSCRIPT_SEGMENT),
        _event(
            3,
            MeetingEventKind.TRANSCRIPT_SEGMENT,
            participant_id=CUSTOMER_ID,
            participant_label="Synthetic customer",
            transcript_text="P95 latency must remain below 500 milliseconds.",
        ),
        _event(4, MeetingEventKind.STREAM_STOPPED),
    )


def _inbox(
    tmp_path: Path,
    clock: MutableClock,
    **updates: object,
) -> SQLiteMeetingEventInbox:
    values: dict[str, object] = {
        "raw_payload_retention_seconds": 3_600,
        "max_ingress_receipts": 100,
        "clock": clock,
    }
    values.update(updates)
    return SQLiteMeetingEventInbox(tmp_path / "meeting-inbox.sqlite3", **values)


def _append(
    inbox: SQLiteMeetingEventInbox,
    event: MeetingTranscriptEvent,
    key: str,
    *,
    authorization: MeetingCaptureAuthorization | None = None,
    binding: MeetingTransportBinding | None = None,
):
    authorization = authorization or _authorization()
    binding = binding or _binding(authorization)
    return inbox.append(
        ingress_idempotency_key=key,
        authorization=authorization,
        binding=binding,
        event=event,
    )


def test_unique_append_is_content_free_and_has_zero_authority(tmp_path: Path):
    clock = MutableClock(NOW + timedelta(minutes=2))
    inbox = _inbox(tmp_path, clock)
    event = _event(
        2,
        MeetingEventKind.TRANSCRIPT_SEGMENT,
        transcript_text=PRIVATE_MARKER,
    )

    receipt = _append(inbox, event, "ingress-unique")

    assert receipt.disposition is MeetingInboxDisposition.ACCEPTED
    assert receipt.idempotent_replay is False
    assert receipt.transcript_authority == MEETING_CONNECTOR_AUTHORITY
    assert receipt.may_create_source is False
    assert receipt.may_confirm_contract is False
    assert receipt.may_freeze_contract is False
    assert receipt.may_start_measurement is False
    assert receipt.may_assign_verdict is False
    assert inbox.counts() == (1, 1, 1, 1, 0)
    public = json.dumps(receipt.model_dump(mode="json"), sort_keys=True)
    assert PRIVATE_MARKER not in public
    assert MEETING_ID not in public
    assert CUSTOMER_ID not in public


def test_same_ingress_key_is_exact_api_replay_with_no_second_write(
    tmp_path: Path,
):
    clock = MutableClock(NOW + timedelta(minutes=2))
    inbox = _inbox(tmp_path, clock)
    event = _happy_events()[0]
    first = _append(inbox, event, "same-api-attempt")
    clock.value += timedelta(minutes=5)

    replay = _append(inbox, event, "same-api-attempt")

    assert replay.idempotent_replay is True
    assert replay.disposition is MeetingInboxDisposition.ACCEPTED
    assert replay.ingress_recorded_at == first.ingress_recorded_at
    assert replay.raw_payload_expires_at == first.raw_payload_expires_at
    assert replay.event_identity_sha256 == first.event_identity_sha256
    assert inbox.counts() == (1, 1, 1, 1, 0)


def test_new_ingress_key_records_exact_provider_duplicate_once(tmp_path: Path):
    clock = MutableClock(NOW + timedelta(minutes=2))
    inbox = _inbox(tmp_path, clock)
    event = _happy_events()[1]
    first = _append(inbox, event, "delivery-a")
    clock.value += timedelta(seconds=1)

    duplicate = _append(inbox, event, "delivery-b")
    duplicate_replay = _append(inbox, event, "delivery-b")

    assert first.disposition is MeetingInboxDisposition.ACCEPTED
    assert duplicate.disposition is MeetingInboxDisposition.EXACT_DUPLICATE
    assert duplicate.idempotent_replay is False
    assert duplicate_replay.idempotent_replay is True
    assert duplicate_replay.disposition is MeetingInboxDisposition.EXACT_DUPLICATE
    assert duplicate.raw_payload_expires_at == first.raw_payload_expires_at
    assert inbox.counts() == (1, 1, 1, 2, 0)


def test_reordered_restart_recovery_seals_same_population(tmp_path: Path):
    clock = MutableClock(NOW + timedelta(minutes=2))
    authorization = _authorization()
    binding = _binding(authorization)
    inbox = _inbox(tmp_path, clock)
    start, first, second, stop = _happy_events()
    delivery = (stop, second, first, start)
    for index, event in enumerate(delivery):
        _append(
            inbox,
            event,
            f"delivery-{index}",
            authorization=authorization,
            binding=binding,
        )
    _append(
        inbox,
        first,
        "delivery-exact-duplicate",
        authorization=authorization,
        binding=binding,
    )

    restarted = _inbox(tmp_path, clock)
    recovered = restarted.recover_stream(
        authorization=authorization,
        binding=binding,
    )
    sealed = seal_meeting_transcript_window(
        authorization,
        binding,
        _consent(),
        recovered.events_for_sealing(),
        now=clock.value,
    )
    direct = seal_meeting_transcript_window(
        authorization,
        binding,
        _consent(),
        (stop, second, first, start, first),
        now=clock.value,
    )

    assert repr(recovered) == "RecoveredMeetingEventStream(<private>)"
    assert recovered.receipt.unique_event_count == 4
    assert recovered.receipt.exact_duplicate_count == 1
    assert recovered.receipt.sequence_contiguous is True
    assert sealed.receipt.event_stream_sha256 == direct.receipt.event_stream_sha256
    assert sealed.receipt.duplicate_event_count == 1
    assert sealed.receipt.transcript_authority == "UNTRUSTED_SOURCE_ONLY"


def test_temporary_gap_is_stored_but_unchanged_sealer_rejects_finalization(
    tmp_path: Path,
):
    clock = MutableClock(NOW + timedelta(minutes=2))
    authorization = _authorization()
    binding = _binding(authorization)
    inbox = _inbox(tmp_path, clock)
    start, _, second, stop = _happy_events()
    for index, event in enumerate((start, second, stop)):
        _append(
            inbox,
            event,
            f"gap-{index}",
            authorization=authorization,
            binding=binding,
        )
    recovered = inbox.recover_stream(
        authorization=authorization,
        binding=binding,
    )

    assert recovered.receipt.sequence_contiguous is False
    with pytest.raises(MeetingConnectorDenied):
        seal_meeting_transcript_window(
            authorization,
            binding,
            _consent(),
            recovered.events_for_sealing(),
            now=clock.value,
        )


def test_consent_is_rechecked_after_recovery_before_sealing(tmp_path: Path):
    clock = MutableClock(NOW + timedelta(minutes=2))
    authorization = _authorization()
    binding = _binding(authorization)
    inbox = _inbox(tmp_path, clock)
    for index, event in enumerate(_happy_events()):
        _append(
            inbox,
            event,
            f"revocation-{index}",
            authorization=authorization,
            binding=binding,
        )
    recovered = inbox.recover_stream(
        authorization=authorization,
        binding=binding,
    )
    revoked = _consent(
        state=STTConsentState.REVOKED,
        revoked_at=NOW + timedelta(minutes=1),
    )

    with pytest.raises(MeetingConnectorDenied):
        seal_meeting_transcript_window(
            authorization,
            binding,
            revoked,
            recovered.events_for_sealing(),
            now=clock.value,
        )


def test_recovered_private_stream_refuses_serialization(tmp_path: Path):
    clock = MutableClock(NOW + timedelta(minutes=2))
    authorization = _authorization()
    binding = _binding(authorization)
    inbox = _inbox(tmp_path, clock)
    _append(
        inbox,
        _happy_events()[0],
        "private-stream",
        authorization=authorization,
        binding=binding,
    )
    recovered = inbox.recover_stream(
        authorization=authorization,
        binding=binding,
    )

    with pytest.raises(PrivateMeetingEventInboxSerializationError):
        recovered.model_dump()
    with pytest.raises(PrivateMeetingEventInboxSerializationError):
        recovered.model_dump_json()


def test_changed_duplicate_durably_taints_stream_across_restart(tmp_path: Path):
    clock = MutableClock(NOW + timedelta(minutes=2))
    authorization = _authorization()
    binding = _binding(authorization)
    inbox = _inbox(tmp_path, clock)
    original = _happy_events()[1]
    changed = _event(
        2,
        MeetingEventKind.TRANSCRIPT_SEGMENT,
        transcript_text="Changed duplicate must never replace evidence.",
    )
    _append(
        inbox,
        original,
        "original-delivery",
        authorization=authorization,
        binding=binding,
    )

    with pytest.raises(MeetingEventInboxConflict):
        _append(
            inbox,
            changed,
            "changed-delivery",
            authorization=authorization,
            binding=binding,
        )

    assert inbox.counts() == (1, 1, 1, 1, 1)
    restarted = _inbox(tmp_path, clock)
    with pytest.raises(MeetingEventInboxConflict):
        restarted.recover_stream(
            authorization=authorization,
            binding=binding,
        )
    with pytest.raises(MeetingEventInboxConflict):
        _append(
            restarted,
            original,
            "original-delivery",
            authorization=authorization,
            binding=binding,
        )


def test_different_event_identity_on_same_sequence_durably_taints_stream(
    tmp_path: Path,
):
    clock = MutableClock(NOW + timedelta(minutes=2))
    inbox = _inbox(tmp_path, clock)
    first = _happy_events()[1]
    collision = _event(
        2,
        MeetingEventKind.TRANSCRIPT_SEGMENT,
        event_id="mev_sequence_collision",
        transcript_text="Different identity, same sequence.",
    )
    _append(inbox, first, "sequence-first")

    with pytest.raises(MeetingEventInboxConflict):
        _append(inbox, collision, "sequence-collision")

    assert inbox.counts() == (1, 1, 1, 1, 1)


def test_ingress_key_reuse_with_changed_input_taints_stream(tmp_path: Path):
    clock = MutableClock(NOW + timedelta(minutes=2))
    inbox = _inbox(tmp_path, clock)
    _append(inbox, _happy_events()[0], "reused-key")

    with pytest.raises(MeetingEventInboxConflict):
        _append(inbox, _happy_events()[1], "reused-key")

    assert inbox.counts()[-1] == 1


def test_invalid_binding_is_rejected_before_idempotency_or_capacity_mutation(
    tmp_path: Path,
):
    clock = MutableClock(NOW + timedelta(minutes=2))
    inbox = _inbox(tmp_path, clock)
    forged = _event(
        1,
        MeetingEventKind.STREAM_STARTED,
        transport_binding_id="meetbind_" + "d" * 64,
    )

    with pytest.raises(MeetingConnectorDenied):
        _append(inbox, forged, "forged-binding")

    assert inbox.counts() == (0, 0, 0, 0, 0)


def test_global_capacity_taints_incomplete_stream_instead_of_truncating(
    tmp_path: Path,
):
    clock = MutableClock(NOW + timedelta(minutes=2))
    authorization = _authorization()
    binding = _binding(authorization)
    inbox = _inbox(tmp_path, clock, max_ingress_receipts=3)
    events = _happy_events()
    for index, event in enumerate(events[:3]):
        _append(
            inbox,
            event,
            f"capacity-{index}",
            authorization=authorization,
            binding=binding,
        )

    with pytest.raises(MeetingEventInboxCapacityError):
        _append(
            inbox,
            events[3],
            "capacity-overflow",
            authorization=authorization,
            binding=binding,
        )

    assert inbox.counts() == (1, 3, 3, 3, 1)
    with pytest.raises(MeetingEventInboxCapacityError):
        inbox.recover_stream(
            authorization=authorization,
            binding=binding,
        )


def test_transcript_character_overflow_taints_stream(tmp_path: Path):
    clock = MutableClock(NOW + timedelta(minutes=2))
    authorization = _authorization(
        policy=_policy(max_transcript_characters=60)
    )
    binding = _binding(authorization)
    inbox = _inbox(tmp_path, clock)
    first = _event(
        2,
        MeetingEventKind.TRANSCRIPT_SEGMENT,
        transcript_text="a" * 40,
    )
    second = _event(
        3,
        MeetingEventKind.TRANSCRIPT_SEGMENT,
        transcript_text="b" * 40,
    )
    _append(
        inbox,
        first,
        "characters-first",
        authorization=authorization,
        binding=binding,
    )

    with pytest.raises(MeetingConnectorDenied):
        _append(
            inbox,
            second,
            "characters-overflow",
            authorization=authorization,
            binding=binding,
        )

    assert inbox.counts() == (1, 1, 1, 1, 1)
    with pytest.raises(MeetingEventInboxCapacityError):
        inbox.recover_stream(
            authorization=authorization,
            binding=binding,
        )


def test_payload_expiry_preserves_receipts_and_cannot_restore_private_text(
    tmp_path: Path,
):
    clock = MutableClock(NOW + timedelta(minutes=2))
    authorization = _authorization()
    binding = _binding(authorization)
    inbox = _inbox(tmp_path, clock, raw_payload_retention_seconds=60)
    event = _event(
        2,
        MeetingEventKind.TRANSCRIPT_SEGMENT,
        transcript_text=PRIVATE_MARKER,
    )
    first = _append(
        inbox,
        event,
        "retention-first",
        authorization=authorization,
        binding=binding,
    )
    assert inbox.counts() == (1, 1, 1, 1, 0)

    clock.value = first.raw_payload_expires_at
    assert inbox.purge_expired_payloads() == 1
    assert inbox.purge_expired_payloads() == 0
    assert inbox.counts() == (1, 1, 0, 1, 0)

    replay = _append(
        inbox,
        event,
        "retention-first",
        authorization=authorization,
        binding=binding,
    )
    duplicate = _append(
        inbox,
        event,
        "retention-new-delivery",
        authorization=authorization,
        binding=binding,
    )
    assert replay.idempotent_replay is True
    assert duplicate.disposition is MeetingInboxDisposition.EXACT_DUPLICATE
    assert inbox.counts() == (1, 1, 0, 2, 0)
    with pytest.raises(MeetingEventInboxPayloadExpired):
        inbox.recover_stream(
            authorization=authorization,
            binding=binding,
        )

    for suffix in ("", "-wal", "-shm"):
        candidate = Path(str(inbox.database_path) + suffix)
        if candidate.exists():
            assert PRIVATE_MARKER.encode() not in candidate.read_bytes()


def test_clock_rollback_cannot_create_a_new_duplicate_receipt(tmp_path: Path):
    clock = MutableClock(NOW + timedelta(minutes=2))
    inbox = _inbox(tmp_path, clock)
    event = _happy_events()[1]
    first = _append(inbox, event, "clock-first")
    clock.value = first.event_persisted_at - timedelta(microseconds=1)

    replay = _append(inbox, event, "clock-first")
    assert replay.idempotent_replay is True
    with pytest.raises(MeetingConnectorDenied):
        _append(inbox, event, "clock-new-delivery")
    assert inbox.counts() == (1, 1, 1, 1, 0)


def test_raw_sql_cannot_update_history_or_delete_private_payload(tmp_path: Path):
    clock = MutableClock(NOW + timedelta(minutes=2))
    inbox = _inbox(tmp_path, clock)
    _append(inbox, _happy_events()[1], "guarded-event")
    connection = sqlite3.connect(inbox.database_path)
    try:
        with pytest.raises(sqlite3.DatabaseError):
            connection.execute(
                "UPDATE meeting_event_inbox_events SET sequence = 9"
            )
        with pytest.raises(sqlite3.DatabaseError):
            connection.execute("DELETE FROM meeting_event_inbox_payloads")
    finally:
        connection.close()
    assert inbox.counts() == (1, 1, 1, 1, 0)


def test_missing_mutation_guard_is_not_silently_repaired(tmp_path: Path):
    clock = MutableClock(NOW + timedelta(minutes=2))
    inbox = _inbox(tmp_path, clock)
    connection = sqlite3.connect(inbox.database_path)
    try:
        connection.execute("DROP TRIGGER meeting_inbox_events_update_guard")
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(MeetingEventInboxIntegrityError):
        _inbox(tmp_path, clock)


def test_payload_corruption_fails_closed_without_private_marker_in_error(
    tmp_path: Path,
):
    clock = MutableClock(NOW + timedelta(minutes=2))
    authorization = _authorization()
    binding = _binding(authorization)
    inbox = _inbox(tmp_path, clock)
    event = _event(
        2,
        MeetingEventKind.TRANSCRIPT_SEGMENT,
        transcript_text=PRIVATE_MARKER,
    )
    _append(
        inbox,
        event,
        "corrupt-payload",
        authorization=authorization,
        binding=binding,
    )
    connection = sqlite3.connect(inbox.database_path)
    try:
        connection.execute("DROP TRIGGER meeting_inbox_payloads_update_guard")
        connection.execute(
            "UPDATE meeting_event_inbox_payloads SET payload = ?",
            (sqlite3.Binary(b"corrupt"),),
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(MeetingEventInboxIntegrityError) as exc_info:
        inbox.recover_stream(
            authorization=authorization,
            binding=binding,
        )
    assert PRIVATE_MARKER not in str(exc_info.value)


def test_reopen_with_changed_retention_or_capacity_fails_closed(tmp_path: Path):
    clock = MutableClock(NOW + timedelta(minutes=2))
    _inbox(tmp_path, clock)

    with pytest.raises(MeetingEventInboxIntegrityError):
        _inbox(tmp_path, clock, raw_payload_retention_seconds=7_200)
    with pytest.raises(MeetingEventInboxIntegrityError):
        _inbox(tmp_path, clock, max_ingress_receipts=101)


def test_concurrent_same_api_attempt_has_one_durable_effect(tmp_path: Path):
    clock = MutableClock(NOW + timedelta(minutes=2))
    inbox = _inbox(tmp_path, clock)
    authorization = _authorization()
    binding = _binding(authorization)
    event = _happy_events()[0]

    def append_once(_: int):
        return _append(
            inbox,
            event,
            "concurrent-same-key",
            authorization=authorization,
            binding=binding,
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        receipts = list(executor.map(append_once, range(8)))

    assert sum(not receipt.idempotent_replay for receipt in receipts) == 1
    assert sum(receipt.idempotent_replay for receipt in receipts) == 7
    assert inbox.counts() == (1, 1, 1, 1, 0)


def test_concurrent_distinct_deliveries_preserve_duplicate_population(
    tmp_path: Path,
):
    clock = MutableClock(NOW + timedelta(minutes=2))
    inbox = _inbox(tmp_path, clock)
    authorization = _authorization()
    binding = _binding(authorization)
    event = _happy_events()[1]

    def append_once(index: int):
        return _append(
            inbox,
            event,
            f"concurrent-delivery-{index}",
            authorization=authorization,
            binding=binding,
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        receipts = list(executor.map(append_once, range(8)))

    assert sum(
        receipt.disposition is MeetingInboxDisposition.ACCEPTED
        for receipt in receipts
    ) == 1
    assert sum(
        receipt.disposition is MeetingInboxDisposition.EXACT_DUPLICATE
        for receipt in receipts
    ) == 7
    recovered = inbox.recover_stream(
        authorization=authorization,
        binding=binding,
    )
    assert recovered.receipt.unique_event_count == 1
    assert recovered.receipt.exact_duplicate_count == 7
    assert len(recovered.events_for_sealing()) == 8
    assert inbox.counts() == (1, 1, 1, 8, 0)


def test_concurrent_sequence_collision_accepts_one_then_taints_stream(
    tmp_path: Path,
):
    clock = MutableClock(NOW + timedelta(minutes=2))
    inbox = _inbox(tmp_path, clock)
    authorization = _authorization()
    binding = _binding(authorization)
    candidates = (
        _happy_events()[1],
        _event(
            2,
            MeetingEventKind.TRANSCRIPT_SEGMENT,
            event_id="mev_concurrent_collision",
            transcript_text="Concurrent collision candidate.",
        ),
    )

    def append_once(index: int) -> str:
        try:
            _append(
                inbox,
                candidates[index],
                f"collision-{index}",
                authorization=authorization,
                binding=binding,
            )
            return "accepted"
        except MeetingEventInboxConflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(append_once, range(2)))

    assert sorted(outcomes) == ["accepted", "conflict"]
    assert inbox.counts() == (1, 1, 1, 1, 1)
    with pytest.raises(MeetingEventInboxConflict):
        inbox.recover_stream(
            authorization=authorization,
            binding=binding,
        )


def test_event_identity_is_scoped_to_authenticated_stream(tmp_path: Path):
    clock = MutableClock(NOW + timedelta(minutes=2))
    inbox = _inbox(tmp_path, clock)
    authorization = _authorization()
    first_binding = _binding(authorization)
    second_stream_id = "stream_zoom_synthetic_002"
    second_binding_id = "meetbind_" + "e" * 64
    second_binding = _binding(
        authorization,
        binding_id=second_binding_id,
        stream_identity_sha256=stream_identity_sha256(second_stream_id),
        webhook_event_sha256="f" * 64,
    )
    first_event = _happy_events()[0]
    second_event = _event(
        1,
        MeetingEventKind.STREAM_STARTED,
        stream_id=second_stream_id,
        transport_binding_id=second_binding_id,
    )

    first = _append(
        inbox,
        first_event,
        "stream-one",
        authorization=authorization,
        binding=first_binding,
    )
    second = _append(
        inbox,
        second_event,
        "stream-two",
        authorization=authorization,
        binding=second_binding,
    )

    assert first.event_identity_sha256 != second.event_identity_sha256
    assert inbox.counts() == (2, 2, 2, 2, 0)


def test_changed_authorization_limits_taint_existing_stream(tmp_path: Path):
    clock = MutableClock(NOW + timedelta(minutes=2))
    inbox = _inbox(tmp_path, clock)
    authorization = _authorization()
    binding = _binding(authorization)
    event = _happy_events()[0]
    _append(
        inbox,
        event,
        "frozen-limits-first",
        authorization=authorization,
        binding=binding,
    )

    payload = authorization.model_dump(mode="python")
    payload["max_event_count"] = 99
    drifted = MeetingCaptureAuthorization(**payload)
    with pytest.raises(MeetingEventInboxConflict):
        _append(
            inbox,
            event,
            "frozen-limits-drift",
            authorization=drifted,
            binding=binding,
        )
    assert inbox.counts()[-1] == 1


@pytest.mark.parametrize(
    "database_path",
    (
        ":memory:",
        "relative.sqlite3",
        "file:/tmp/meeting.sqlite3",
    ),
)
def test_unsafe_database_paths_are_rejected(database_path: str):
    with pytest.raises(ValueError):
        SQLiteMeetingEventInbox(database_path)


def test_database_and_live_sidecars_are_owner_only(tmp_path: Path):
    clock = MutableClock(NOW + timedelta(minutes=2))
    inbox = _inbox(tmp_path, clock)
    _append(inbox, _happy_events()[1], "private-permissions")

    for suffix in ("", "-wal", "-shm"):
        candidate = Path(str(inbox.database_path) + suffix)
        if candidate.exists():
            assert os.stat(candidate).st_mode & 0o777 == 0o600


def test_symlink_database_target_is_rejected(tmp_path: Path):
    target = tmp_path / "target.sqlite3"
    target.touch()
    link = tmp_path / "link.sqlite3"
    link.symlink_to(target)

    with pytest.raises(ValueError):
        SQLiteMeetingEventInbox(link)
