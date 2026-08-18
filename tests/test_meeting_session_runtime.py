from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from exitspec.meeting_event_inbox import (
    MeetingEventInboxStorageError,
    SQLiteMeetingEventInbox,
)
from exitspec.meeting_session_runtime import (
    FixedSyntheticMeetingAdapter,
    MEETING_SESSION_DISCLOSURE_ID,
    MEETING_SESSION_NOTICE,
    MeetingSessionError,
    MeetingSessionFailureCode,
    MeetingSessionState,
    ProcessLocalMeetingSessionRuntime,
)
from exitspec.poc_creation import (
    DraftPOCCreateRequest,
    FirstSourceChoice,
    ProcessLocalDraftPOCService,
)
from exitspec.poc_source_intake import ProcessLocalPOCSourceIntake


NOW = datetime(2026, 8, 18, 17, 0, tzinfo=timezone.utc)
POC_ID = "poc_meeting_session_runtime"


class MutableClock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now


class CountingSyntheticAdapter:
    def __init__(self) -> None:
        self.delegate = FixedSyntheticMeetingAdapter()
        self.draft_calls = 0
        self.start_calls = 0

    @property
    def descriptor(self):
        return self.delegate.descriptor

    def prepare(self, **kwargs):
        return self.delegate.prepare(**kwargs)

    def start(self, **kwargs):
        self.start_calls += 1
        return self.delegate.start(**kwargs)

    def draft_events(self, **kwargs):
        self.draft_calls += 1
        return self.delegate.draft_events(**kwargs)


def _runtime(
    tmp_path,
    *,
    source: FirstSourceChoice = FirstSourceChoice.MEETING,
    adapter=None,
    clock=None,
):
    tmp_path.mkdir(parents=True, exist_ok=True)
    selected_clock = clock or (lambda: NOW)
    drafts = ProcessLocalDraftPOCService(clock=selected_clock)
    drafts.create(
        DraftPOCCreateRequest(
            poc_id=POC_ID,
            display_name="Synthetic meeting POC",
            customer_label="Northstar",
            use_case="Draft measurable requirements during a meeting.",
            owner="field_engineer",
            first_source_choice=source,
        ),
        idempotency_key="create-meeting-runtime",
    )
    intake = ProcessLocalPOCSourceIntake(
        draft_lookup=drafts.get,
        clock=selected_clock,
    )
    inbox = SQLiteMeetingEventInbox(
        tmp_path / "meeting-events.sqlite3",
        clock=selected_clock,
    )
    runtime = ProcessLocalMeetingSessionRuntime(
        drafts=drafts,
        source_intake=intake,
        inbox=inbox,
        adapter=adapter,
        clock=selected_clock,
    )
    return runtime, drafts, intake, inbox


def _create(runtime):
    return runtime.create(
        poc_id=POC_ID,
        idempotency_key="meeting-create-operation",
    )


def _consent(runtime, session_id):
    return runtime.record_consent(
        poc_id=POC_ID,
        session_id=session_id,
        disclosure_id=MEETING_SESSION_DISCLOSURE_ID,
        recording_notice_acknowledged=True,
        all_participants_consented=True,
        synthetic_demo_acknowledged=True,
        idempotency_key="meeting-consent-operation",
    )


def test_guided_session_uses_real_inbox_and_creates_review_only_source(tmp_path):
    runtime, _, intake, _ = _runtime(tmp_path)

    disclosure = runtime.disclosure_for(POC_ID)
    created = _create(runtime)
    consented = _consent(runtime, created.session.session_id)
    started = runtime.start(
        poc_id=POC_ID,
        session_id=created.session.session_id,
        idempotency_key="meeting-start-operation",
    )
    drafted = runtime.draft_now(
        poc_id=POC_ID,
        session_id=created.session.session_id,
        idempotency_key="meeting-draft-operation",
    )
    receipts = intake.list_receipts(POC_ID)

    assert disclosure.adapter.provider == "exitspec.synthetic"
    assert disclosure.adapter.provider_connected is False
    assert disclosure.raw_audio_requested is False
    assert disclosure.may_confirm_contract is False
    assert created.session.state is MeetingSessionState.SETUP
    assert created.session.review_state is None
    assert consented.session.state is MeetingSessionState.READY
    assert started.session.state is MeetingSessionState.LIVE
    assert drafted.session.state is MeetingSessionState.DRAFT_READY
    assert drafted.session.review_state == "NEEDS_REVIEW"
    assert drafted.session.proposal_count == 2
    assert drafted.session.review_url == f"/app/pocs/{POC_ID}/review"
    assert drafted.session.may_confirm_contract is False
    assert drafted.session.may_freeze_contract is False
    assert drafted.session.may_start_measurement is False
    assert drafted.session.may_assign_verdict is False
    assert len(receipts) == 1
    assert receipts[0].source_receipt_id == drafted.session.source_receipt_id
    assert receipts[0].status == "NEEDS_REVIEW"

    serialized = json.dumps(
        {
            "disclosure": disclosure.model_dump(mode="json"),
            "session": drafted.session.model_dump(mode="json"),
        }
    )
    assert "participant_synthetic_employee" not in serialized
    assert "participant_synthetic_customer" not in serialized
    assert '"meeting_id"' not in serialized
    assert "p95 response latency" not in serialized.lower()
    assert MEETING_SESSION_NOTICE in serialized


def test_mutations_replay_exactly_and_reject_cross_action_key_reuse(tmp_path):
    runtime, _, _, _ = _runtime(tmp_path)
    first = _create(runtime)
    replay = _create(runtime)

    assert first.idempotent_replay is False
    assert replay.idempotent_replay is True
    assert replay.session == first.session

    with pytest.raises(MeetingSessionError) as captured:
        runtime.start(
            poc_id=POC_ID,
            session_id=first.session.session_id,
            idempotency_key="meeting-create-operation",
        )

    assert captured.value.failure_code is (
        MeetingSessionFailureCode.IDEMPOTENCY_CONFLICT
    )


def test_changed_consent_replay_is_an_idempotency_conflict(tmp_path):
    runtime, _, _, _ = _runtime(tmp_path)
    session_id = _create(runtime).session.session_id
    _consent(runtime, session_id)

    with pytest.raises(MeetingSessionError) as changed:
        runtime.record_consent(
            poc_id=POC_ID,
            session_id=session_id,
            disclosure_id=MEETING_SESSION_DISCLOSURE_ID,
            recording_notice_acknowledged=True,
            all_participants_consented=False,
            synthetic_demo_acknowledged=True,
            idempotency_key="meeting-consent-operation",
        )

    assert changed.value.failure_code is (
        MeetingSessionFailureCode.IDEMPOTENCY_CONFLICT
    )


def test_consent_and_state_machine_fail_closed(tmp_path):
    runtime, _, _, _ = _runtime(tmp_path)
    created = _create(runtime)
    session_id = created.session.session_id

    with pytest.raises(MeetingSessionError) as before_consent:
        runtime.start(
            poc_id=POC_ID,
            session_id=session_id,
            idempotency_key="start-before-consent",
        )
    with pytest.raises(MeetingSessionError) as incomplete_consent:
        runtime.record_consent(
            poc_id=POC_ID,
            session_id=session_id,
            disclosure_id=MEETING_SESSION_DISCLOSURE_ID,
            recording_notice_acknowledged=True,
            all_participants_consented=False,
            synthetic_demo_acknowledged=True,
            idempotency_key="incomplete-consent",
        )
    with pytest.raises(MeetingSessionError) as wrong_disclosure:
        runtime.record_consent(
            poc_id=POC_ID,
            session_id=session_id,
            disclosure_id="old-disclosure",
            recording_notice_acknowledged=True,
            all_participants_consented=True,
            synthetic_demo_acknowledged=True,
            idempotency_key="wrong-disclosure",
        )

    assert before_consent.value.failure_code is (
        MeetingSessionFailureCode.CONSENT_REQUIRED
    )
    assert incomplete_consent.value.failure_code is (
        MeetingSessionFailureCode.CONSENT_REQUIRED
    )
    assert wrong_disclosure.value.failure_code is (
        MeetingSessionFailureCode.DISCLOSURE_MISMATCH
    )


def test_wrong_source_and_archived_drafts_cannot_start_sessions(tmp_path):
    wrong_runtime, _, _, _ = _runtime(
        tmp_path / "wrong-source",
        source=FirstSourceChoice.EMAIL,
    )
    with pytest.raises(MeetingSessionError) as wrong_source:
        _create(wrong_runtime)
    assert wrong_source.value.failure_code is (
        MeetingSessionFailureCode.WRONG_SOURCE_TYPE
    )

    active_runtime, drafts, _, _ = _runtime(tmp_path / "archived")
    drafts.archive(POC_ID)
    with pytest.raises(MeetingSessionError) as archived:
        _create(active_runtime)
    assert archived.value.failure_code is (
        MeetingSessionFailureCode.DRAFT_UNAVAILABLE
    )


def test_failed_start_retry_reuses_exact_adapter_artifact(
    tmp_path,
    monkeypatch,
):
    clock = MutableClock()
    adapter = CountingSyntheticAdapter()
    runtime, _, _, _ = _runtime(
        tmp_path,
        adapter=adapter,
        clock=clock,
    )
    session_id = _create(runtime).session.session_id
    _consent(runtime, session_id)

    original_append = SQLiteMeetingEventInbox.append
    attempts = 0

    def fail_once(inbox, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise MeetingEventInboxStorageError("synthetic failure")
        return original_append(inbox, **kwargs)

    monkeypatch.setattr(SQLiteMeetingEventInbox, "append", fail_once)
    with pytest.raises(MeetingSessionError) as failed:
        runtime.start(
            poc_id=POC_ID,
            session_id=session_id,
            idempotency_key="retryable-start-operation",
        )
    clock.now += timedelta(minutes=5)
    recovered = runtime.start(
        poc_id=POC_ID,
        session_id=session_id,
        idempotency_key="retryable-start-operation",
    )

    assert failed.value.failure_code is MeetingSessionFailureCode.ADAPTER_FAILED
    assert adapter.start_calls == 1
    assert recovered.session.state is MeetingSessionState.LIVE
    assert recovered.session.started_at == NOW


def test_failed_draft_retry_reuses_exact_event_population(
    tmp_path,
    monkeypatch,
):
    clock = MutableClock()
    adapter = CountingSyntheticAdapter()
    runtime, _, _, _ = _runtime(
        tmp_path,
        adapter=adapter,
        clock=clock,
    )
    session_id = _create(runtime).session.session_id
    _consent(runtime, session_id)
    runtime.start(
        poc_id=POC_ID,
        session_id=session_id,
        idempotency_key="draft-retry-start",
    )

    original_append = SQLiteMeetingEventInbox.append
    attempts = 0

    def fail_once(inbox, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise MeetingEventInboxStorageError("synthetic failure")
        return original_append(inbox, **kwargs)

    monkeypatch.setattr(SQLiteMeetingEventInbox, "append", fail_once)
    with pytest.raises(MeetingSessionError) as failed:
        runtime.draft_now(
            poc_id=POC_ID,
            session_id=session_id,
            idempotency_key="retryable-draft-operation",
        )
    clock.now += timedelta(minutes=5)
    recovered = runtime.draft_now(
        poc_id=POC_ID,
        session_id=session_id,
        idempotency_key="retryable-draft-operation",
    )

    assert failed.value.failure_code is MeetingSessionFailureCode.ADAPTER_FAILED
    assert adapter.draft_calls == 1
    assert recovered.session.state is MeetingSessionState.DRAFT_READY
    assert recovered.session.drafted_at == NOW


def test_current_and_session_reads_do_not_revalidate_archived_mutation_state(
    tmp_path,
):
    runtime, drafts, _, _ = _runtime(tmp_path)
    created = _create(runtime)
    drafts.archive(POC_ID)

    assert runtime.current(poc_id=POC_ID) == created.session
    assert runtime.session(
        poc_id=POC_ID,
        session_id=created.session.session_id,
    ) == created.session

    with pytest.raises(MeetingSessionError) as mutation:
        _consent(runtime, created.session.session_id)
    assert mutation.value.failure_code is (
        MeetingSessionFailureCode.DRAFT_UNAVAILABLE
    )
