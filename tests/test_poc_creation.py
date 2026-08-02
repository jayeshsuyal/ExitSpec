from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

import exitspec.poc_creation as poc_creation
from exitspec.poc_creation import (
    DraftPOCArchiveState,
    DraftPOCCapacityExceeded,
    DraftPOCCreateRequest,
    DraftPOCIdempotencyConflict,
    DraftPOCNotFound,
    DraftPOCSnapshot,
    DuplicateDraftPOCId,
    FirstSourceChoice,
    NextIntakeRoute,
    ProcessLocalDraftPOCService,
    SourceIngestionState,
)


NOW = datetime(2026, 7, 28, 19, 0, tzinfo=timezone.utc)
LATER = NOW + timedelta(minutes=5)


def _request(**updates) -> DraftPOCCreateRequest:
    payload = {
        "display_name": "Support-agent POC",
        "customer_label": "Example customer",
        "use_case": "Verify exact support-tool selection.",
        "owner": "field_engineer",
        "first_source_choice": FirstSourceChoice.EMAIL,
    }
    payload.update(updates)
    return DraftPOCCreateRequest(**payload)


def _service(
    *,
    max_drafts: int = 1_024,
    ids: tuple[str, ...] = ("poc_generated_001",),
    times: tuple[datetime, ...] = (NOW,),
) -> ProcessLocalDraftPOCService:
    id_values = iter(ids)
    time_values = iter(times)
    return ProcessLocalDraftPOCService(
        max_drafts=max_drafts,
        poc_id_factory=lambda: next(id_values),
        clock=lambda: next(time_values),
    )


@pytest.mark.parametrize(
    ("choice", "route"),
    (
        (FirstSourceChoice.EMAIL, NextIntakeRoute.EMAIL),
        (FirstSourceChoice.MEETING, NextIntakeRoute.MEETING),
        (FirstSourceChoice.DOCUMENT, NextIntakeRoute.DOCUMENT),
        (
            FirstSourceChoice.EXISTING_CONTRACT,
            NextIntakeRoute.EXISTING_CONTRACT,
        ),
    ),
)
def test_first_source_choice_only_selects_the_next_intake_route(
    choice: FirstSourceChoice,
    route: NextIntakeRoute,
):
    service = _service()
    result = service.create(
        _request(first_source_choice=choice),
        idempotency_key="create-by-source",
    )

    assert result.draft.first_source_choice == choice
    assert result.draft.next_intake_route == route
    assert (
        result.draft.source_ingestion_state
        == SourceIngestionState.NOT_STARTED
    )
    assert "SourceEnvelope" not in result.draft.__class__.__name__
    assert not hasattr(result.draft, "source_envelope")
    assert not hasattr(result.draft, "source_id")


def test_create_requires_and_normalizes_all_human_metadata():
    result = _service().create(
        _request(
            display_name="  Support-agent POC  ",
            customer_label="  Example customer  ",
            use_case="  Verify exact support-tool selection.  ",
            owner="  field_engineer  ",
        ),
        idempotency_key="normalized-create",
    )

    assert result.draft.display_name == "Support-agent POC"
    assert result.draft.customer_label == "Example customer"
    assert result.draft.use_case == "Verify exact support-tool selection."
    assert result.draft.owner == "field_engineer"


@pytest.mark.parametrize(
    "field",
    ("display_name", "customer_label", "use_case", "owner"),
)
@pytest.mark.parametrize("value", ("", " ", "\n\t"))
def test_required_human_metadata_rejects_blank_values(field: str, value: str):
    with pytest.raises(ValidationError, match="non-whitespace"):
        _request(**{field: value})


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("display_name", "d" * 161),
        ("customer_label", "c" * 161),
        ("use_case", "u" * 501),
        ("owner", "o" * 161),
    ),
)
def test_human_metadata_is_bounded(field: str, value: str):
    with pytest.raises(ValidationError):
        _request(**{field: value})


def test_create_request_rejects_unknown_fields():
    with pytest.raises(ValidationError, match="Extra inputs"):
        DraftPOCCreateRequest(
            **_request().model_dump(),
            contract_status="APPROVED",
        )


def test_generated_and_caller_supplied_ids_are_stable_and_validated():
    generated = _service().create(
        _request(),
        idempotency_key="generated-id",
    ).draft
    supplied = _service().create(
        _request(poc_id="poc_customer_alpha"),
        idempotency_key="supplied-id",
    ).draft

    assert generated.poc_id == "poc_generated_001"
    assert supplied.poc_id == "poc_customer_alpha"
    assert generated.created_at == NOW
    assert generated.updated_at == NOW

    for invalid in (
        "customer_alpha",
        "poc_ABCD",
        "poc_ab",
        "poc_a.b",
        "poc_" + "a" * 65,
    ):
        with pytest.raises(ValidationError):
            _request(poc_id=invalid)

    with pytest.raises(ValueError, match="poc_id"):
        _service(ids=("invalid",)).create(
            _request(),
            idempotency_key="invalid-generated-id",
        )


def test_exact_idempotent_replay_returns_same_draft_without_second_write():
    service = _service()
    request = _request()

    first = service.create(request, idempotency_key="safe-create-key")
    replay = service.create(request, idempotency_key="safe-create-key")

    assert first.idempotent_replay is False
    assert replay.idempotent_replay is True
    assert replay.draft is first.draft
    assert len(service) == 1
    assert service.ids() == ("poc_generated_001",)
    assert "safe-create-key" not in repr(service)
    assert "safe-create-key" not in repr(replay)


@pytest.mark.parametrize(
    "update",
    (
        {"display_name": "Changed POC"},
        {"customer_label": "Different customer"},
        {"use_case": "Different use case"},
        {"owner": "different_owner"},
        {"first_source_choice": FirstSourceChoice.MEETING},
        {"poc_id": "poc_different_001"},
    ),
)
def test_idempotency_key_reuse_with_any_changed_input_conflicts(update):
    service = _service()
    service.create(_request(), idempotency_key="conflict-key")

    with pytest.raises(
        DraftPOCIdempotencyConflict,
        match="does not match",
    ) as error:
        service.create(
            _request(**update),
            idempotency_key="conflict-key",
        )

    assert "conflict-key" not in str(error.value)
    assert len(service) == 1


@pytest.mark.parametrize(
    "key",
    ("", " ", "\n", "x" * 201, None, 123),
)
def test_invalid_idempotency_keys_are_rejected_without_writes(key):
    service = _service()
    with pytest.raises(ValueError, match="idempotency_key"):
        service.create(_request(), idempotency_key=key)
    assert len(service) == 0


def test_different_operation_cannot_reuse_an_existing_poc_id():
    service = _service()
    service.create(
        _request(poc_id="poc_customer_alpha"),
        idempotency_key="first-operation",
    )

    with pytest.raises(DuplicateDraftPOCId, match="already exists"):
        service.create(
            _request(poc_id="poc_customer_alpha"),
            idempotency_key="second-operation",
        )

    assert len(service) == 1


def test_store_capacity_is_bounded_and_replays_still_work_at_capacity():
    service = _service(max_drafts=1)
    request = _request()
    first = service.create(request, idempotency_key="first")

    replay = service.create(request, idempotency_key="first")
    assert replay.idempotent_replay is True
    assert replay.draft == first.draft

    with pytest.raises(DraftPOCCapacityExceeded, match="capacity"):
        service.create(
            _request(poc_id="poc_second_001"),
            idempotency_key="second",
        )


@pytest.mark.parametrize("max_drafts", (0, -1, 10_001, True, 1.5, "10"))
def test_service_capacity_configuration_is_strictly_bounded(max_drafts):
    with pytest.raises(ValueError, match="max_drafts"):
        ProcessLocalDraftPOCService(max_drafts=max_drafts)


def test_snapshots_and_results_are_immutable_and_lookup_is_read_only():
    service = _service()
    result = service.create(_request(), idempotency_key="immutable")

    with pytest.raises(ValidationError):
        result.draft.owner = "another_owner"
    with pytest.raises(ValidationError):
        result.idempotent_replay = True

    assert service.get(result.draft.poc_id) is result.draft
    assert service.snapshots() == (result.draft,)
    assert isinstance(service.snapshots(), tuple)
    assert not hasattr(service, "delete")
    assert not hasattr(service, "reset")


def test_lookup_validates_ids_and_distinguishes_missing_drafts():
    service = _service()
    with pytest.raises(ValueError, match="poc_id"):
        service.get("not-a-poc-id")
    with pytest.raises(DraftPOCNotFound, match="not present"):
        service.get("poc_missing_001")
    with pytest.raises(DraftPOCNotFound, match="not present"):
        service.archive("poc_missing_001")


def test_archive_is_explicit_idempotent_and_does_not_free_capacity():
    service = _service(max_drafts=1, times=(NOW, LATER))
    created = service.create(
        _request(),
        idempotency_key="create-before-archive",
    ).draft
    archived = service.archive(created.poc_id)
    replay = service.archive(created.poc_id)

    assert created.archive_state == DraftPOCArchiveState.ACTIVE
    assert created.archived_at is None
    assert archived.archive_state == DraftPOCArchiveState.ARCHIVED
    assert archived.archived_at == LATER
    assert archived.updated_at == LATER
    assert replay is archived
    assert service.get(created.poc_id) is archived

    create_replay = service.create(
        _request(),
        idempotency_key="create-before-archive",
    )
    assert create_replay.idempotent_replay is True
    assert create_replay.draft is archived

    with pytest.raises(DraftPOCCapacityExceeded):
        service.create(
            _request(poc_id="poc_after_archive"),
            idempotency_key="capacity-is-retained",
        )


def test_process_local_storage_semantics_are_machine_readable():
    service = _service(max_drafts=7)
    semantics = service.semantics

    assert semantics.storage_scope == "PROCESS_LOCAL"
    assert semantics.survives_process_restart is False
    assert semantics.shared_across_workers is False
    assert semantics.archived_records_retained_until_restart is True
    assert semantics.max_drafts == 7
    with pytest.raises(ValidationError):
        semantics.max_drafts = 8


def test_timestamps_must_be_timezone_aware_and_monotonic():
    naive_service = _service(times=(datetime(2026, 7, 28, 19, 0),))
    with pytest.raises(ValidationError, match="timezone-aware"):
        naive_service.create(_request(), idempotency_key="naive-clock")

    service = _service(times=(NOW, NOW - timedelta(seconds=1)))
    draft = service.create(_request(), idempotency_key="valid-clock").draft
    with pytest.raises(ValidationError, match="cannot precede"):
        service.archive(draft.poc_id)


def test_snapshot_rejects_mismatched_route_and_false_archive_claims():
    payload = {
        "poc_id": "poc_boundary_001",
        "display_name": "Boundary POC",
        "customer_label": "Example customer",
        "use_case": "Validate creation boundaries.",
        "owner": "field_engineer",
        "first_source_choice": FirstSourceChoice.EMAIL,
        "next_intake_route": NextIntakeRoute.MEETING,
        "created_at": NOW,
        "updated_at": NOW,
    }
    with pytest.raises(ValidationError, match="must match"):
        DraftPOCSnapshot(**payload)

    payload["next_intake_route"] = NextIntakeRoute.EMAIL
    payload["archive_state"] = DraftPOCArchiveState.ARCHIVED
    with pytest.raises(ValidationError, match="requires archived_at"):
        DraftPOCSnapshot(**payload)


def test_concurrent_exact_replays_create_one_draft():
    service = _service()
    request = _request()

    def create_once(_index: int):
        return service.create(
            request,
            idempotency_key="concurrent-exact-create",
        )

    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(create_once, range(64)))

    assert len(service) == 1
    assert len({result.draft.poc_id for result in results}) == 1
    assert sum(not result.idempotent_replay for result in results) == 1
    assert sum(result.idempotent_replay for result in results) == 63


def test_concurrent_duplicate_ids_allow_exactly_one_create():
    service = _service()

    def create_once(index: int):
        try:
            return service.create(
                _request(poc_id="poc_shared_001"),
                idempotency_key="operation-{0}".format(index),
            )
        except DuplicateDraftPOCId as error:
            return error

    with ThreadPoolExecutor(max_workers=16) as pool:
        outcomes = list(pool.map(create_once, range(32)))

    successes = [
        outcome
        for outcome in outcomes
        if not isinstance(outcome, DuplicateDraftPOCId)
    ]
    conflicts = [
        outcome
        for outcome in outcomes
        if isinstance(outcome, DuplicateDraftPOCId)
    ]
    assert len(successes) == 1
    assert len(conflicts) == 31
    assert len(service) == 1


def test_creation_domain_exposes_zero_downstream_authority():
    forbidden_fields = {
        "approval",
        "approved",
        "confirmation",
        "contract_status",
        "evidence",
        "freeze",
        "frozen",
        "run_status",
        "verdict",
    }
    snapshot_fields = set(DraftPOCSnapshot.model_fields)
    request_fields = set(DraftPOCCreateRequest.model_fields)
    assert forbidden_fields.isdisjoint(snapshot_fields)
    assert forbidden_fields.isdisjoint(request_fields)

    service = _service()
    for authority_method in (
        "approve",
        "confirm",
        "freeze",
        "execute",
        "run",
        "issue_verdict",
    ):
        assert not hasattr(service, authority_method)

    assert not hasattr(poc_creation, "SourceEnvelope")
    assert not hasattr(poc_creation, "ContractStatus")
    assert not hasattr(poc_creation, "RunStatus")
    assert not hasattr(poc_creation, "VerdictStatus")


def test_create_rejects_untyped_requests_and_bad_dependencies():
    service = _service()
    with pytest.raises(TypeError, match="DraftPOCCreateRequest"):
        service.create({}, idempotency_key="untyped")
    with pytest.raises(TypeError, match="clock"):
        ProcessLocalDraftPOCService(clock=None)
    with pytest.raises(TypeError, match="poc_id_factory"):
        ProcessLocalDraftPOCService(poc_id_factory=None)
