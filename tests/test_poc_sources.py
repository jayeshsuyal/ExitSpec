from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
import unicodedata

import pytest
from pydantic import ValidationError

import exitspec.poc_sources as poc_sources
from exitspec.poc_creation import (
    DraftPOCCreateRequest,
    FirstSourceChoice,
    ProcessLocalDraftPOCService,
)
from exitspec.poc_sources import (
    CandidateState,
    DuplicatePOCSourceId,
    POCSourceCapacityExceeded,
    POCSourceDraftArchived,
    POCSourceDraftUnavailable,
    POCSourceIdempotencyConflict,
    POCSourceNotFound,
    POCSourceRevisionRequired,
    POCSourceStaleRevision,
    PreparedPOCSource,
    PreparedRequirementCandidate,
    ProcessLocalPOCSourceService,
    SourceAttachDisposition,
    SourceKind,
)


NOW = datetime(2026, 7, 28, 20, 0, tzinfo=timezone.utc)
LATER = NOW + timedelta(minutes=5)
SOURCE_TEXT = (
    "Customer [EMAIL] needs p95 time-to-first-token below 500 ms.\n"
    "Call [PHONE]. Credential [SECRET]."
)


def _draft_request(
    *,
    poc_id: str,
    first_source_choice: FirstSourceChoice = FirstSourceChoice.EMAIL,
) -> DraftPOCCreateRequest:
    return DraftPOCCreateRequest(
        poc_id=poc_id,
        display_name="Inference latency POC",
        customer_label="Example customer",
        use_case="Verify one bounded inference-latency claim.",
        owner="field_engineer",
        first_source_choice=first_source_choice,
    )


def _drafts(
    *poc_ids: str,
) -> ProcessLocalDraftPOCService:
    service = ProcessLocalDraftPOCService(
        max_drafts=max(1, len(poc_ids)),
        clock=lambda: NOW,
    )
    for number, poc_id in enumerate(poc_ids, start=1):
        service.create(
            _draft_request(poc_id=poc_id),
            idempotency_key="create-{0}".format(number),
        )
    return service


def _candidate(
    **updates: object,
) -> PreparedRequirementCandidate:
    payload: dict[str, object] = {
        "candidate_id": "cand_ttft_001",
        "source_quote": ("p95 time-to-first-token below 500 ms"),
        "normalized_claim": ("The p95 time-to-first-token should be below 500 ms."),
        "state": CandidateState.NEEDS_REVIEW,
    }
    payload.update(updates)
    return PreparedRequirementCandidate(**payload)


def _prepared(
    *,
    text: str = SOURCE_TEXT,
    candidates: tuple[PreparedRequirementCandidate, ...] | None = None,
    **updates: object,
) -> PreparedPOCSource:
    normalized = unicodedata.normalize(
        "NFC",
        text.replace("\r\n", "\n").replace("\r", "\n"),
    )
    payload: dict[str, object] = {
        "kind": SourceKind.EMAIL,
        "external_id": "email.message-001",
        "redacted_text": text,
        "content_sha256": hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
        "candidates": ((_candidate(),) if candidates is None else candidates),
        "adapter_name": "synthetic_email",
        "adapter_version": "1.0.0",
        "redaction_policy_version": "1.0.0",
        "observed_at": NOW,
    }
    payload.update(updates)
    return PreparedPOCSource(**payload)


def _source_service(
    draft_service: ProcessLocalDraftPOCService,
    *,
    ids: tuple[str, ...] = ("src_generated_001",),
    **updates: object,
) -> ProcessLocalPOCSourceService:
    source_ids = iter(ids)
    options: dict[str, object] = {
        "draft_lookup": draft_service.get,
        "clock": lambda: LATER,
        "source_id_factory": lambda: next(source_ids),
    }
    options.update(updates)
    return ProcessLocalPOCSourceService(**options)


@pytest.mark.parametrize("kind", tuple(SourceKind))
def test_all_source_kinds_attach_beneath_an_active_draft(kind: SourceKind):
    drafts = _drafts("poc_customer_alpha")
    service = _source_service(drafts)

    result = service.attach(
        "poc_customer_alpha",
        _prepared(kind=kind),
        "attach-first-source",
    )

    assert result.disposition == SourceAttachDisposition.CREATED
    assert result.created is True
    assert result.replayed is False
    assert result.source.kind == kind
    assert result.source.poc_id == "poc_customer_alpha"
    assert result.source.source_sequence == 1
    assert result.source.source_revision == 1


def test_prepared_source_normalizes_nfc_and_line_endings_before_digest_check():
    decomposed = "Cafe\u0301 [EMAIL]\r\nClaim is below 500 ms."
    normalized = "Café [EMAIL]\nClaim is below 500 ms."
    source = _prepared(
        text=decomposed,
        candidates=(
            _candidate(
                source_quote="  Claim is below 500 ms.  ",
            ),
        ),
    )

    assert source.redacted_text == normalized
    assert source.candidates[0].source_quote == "Claim is below 500 ms."
    assert (
        source.content_sha256 == hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    )


def test_mismatched_content_digest_is_rejected():
    with pytest.raises(ValidationError, match="content_sha256"):
        _prepared(content_sha256="0" * 64)


@pytest.mark.parametrize(
    "raw_value",
    (
        "Contact person@example.com now.",
        "Call +1 (415) 555-0134 now.",
        "api_key=do-not-store-this",
        "Authorization Bearer abcdefghijklmnop",
        "Credential fw_abcdefghijklmnopqrstuvwxyz",
        "password: plaintext-value",
    ),
)
def test_raw_email_phone_and_secret_content_is_rejected(raw_value: str):
    with pytest.raises(ValidationError, match="raw"):
        _prepared(
            text="Safe prefix. {0}".format(raw_value),
            candidates=(),
        )


def test_explicit_redaction_placeholders_are_accepted():
    source = _prepared(
        text="Contact [EMAIL], [PHONE], credential [SECRET].",
        candidates=(),
    )

    assert source.redacted_text.endswith("credential [SECRET].")


@pytest.mark.parametrize(
    "value",
    (
        "Allowed text\tforbidden tab",
        "Allowed text\x00forbidden null",
        "Allowed text\u202eforbidden bidi override",
    ),
)
def test_control_and_format_characters_are_rejected(value: str):
    with pytest.raises(ValidationError, match="control"):
        _prepared(text=value, candidates=())


def test_candidate_quote_must_be_bound_to_exact_redacted_source():
    with pytest.raises(ValidationError, match="source_quote"):
        _prepared(
            candidates=(_candidate(source_quote="A claim absent from the source."),)
        )


def test_candidate_ids_must_be_unique_per_prepared_source():
    with pytest.raises(ValidationError, match="unique"):
        _prepared(candidates=(_candidate(), _candidate()))


@pytest.mark.parametrize(
    ("model", "extra_field"),
    (
        (_candidate(), {"metric": "ttft"}),
        (_candidate(), {"threshold": 500}),
        (_candidate(), {"approved": True}),
        (_candidate(), {"verdict": "PASS"}),
    ),
)
def test_prepared_candidates_cannot_carry_authority_fields(
    model: PreparedRequirementCandidate,
    extra_field: dict[str, object],
):
    payload = model.model_dump()
    payload.update(extra_field)
    with pytest.raises(ValidationError, match="Extra inputs"):
        PreparedRequirementCandidate(**payload)


@pytest.mark.parametrize(
    "extra_field",
    (
        {"raw_bytes": b"raw"},
        {"file_path": "/tmp/raw.eml"},
        {"credential": "secret"},
        {"provider_body": {"raw": True}},
        {"metadata": {"arbitrary": True}},
        {"contract_status": "FROZEN"},
    ),
)
def test_prepared_source_rejects_raw_and_arbitrary_fields(
    extra_field: dict[str, object],
):
    payload = _prepared().model_dump()
    payload.update(extra_field)
    with pytest.raises(ValidationError, match="Extra inputs"):
        PreparedPOCSource(**payload)


def test_attach_binds_every_candidate_to_service_owned_source_identity():
    drafts = _drafts("poc_customer_alpha")
    service = _source_service(drafts)

    source = service.attach(
        "poc_customer_alpha",
        _prepared(),
        "bind-candidates",
    ).source
    candidate = source.candidates[0]

    assert candidate.poc_id == source.poc_id
    assert candidate.source_id == source.source_id
    assert candidate.source_sequence == source.source_sequence
    assert candidate.state == CandidateState.NEEDS_REVIEW
    assert source.attached_at == LATER
    with pytest.raises(ValidationError):
        candidate.normalized_claim = "Changed"
    with pytest.raises(ValidationError):
        source.source_sequence = 999


def test_validated_copy_cannot_bypass_redaction_or_digest_validation():
    source = _prepared()

    with pytest.raises(ValidationError, match="raw email"):
        source.model_copy(update={"redacted_text": "Raw person@example.com"})
    with pytest.raises(ValidationError, match="content_sha256"):
        source.model_copy(update={"content_sha256": "0" * 64})


def test_reconstructed_attached_models_retain_redaction_and_digest_guards():
    drafts = _drafts("poc_customer_alpha")
    attached = (
        _source_service(drafts)
        .attach(
            "poc_customer_alpha",
            _prepared(),
            "attach",
        )
        .source
    )

    with pytest.raises(ValidationError, match="raw email"):
        attached.model_copy(update={"redacted_text": "Raw person@example.com"})
    with pytest.raises(ValidationError, match="content_sha256"):
        attached.model_copy(update={"content_sha256": "0" * 64})
    with pytest.raises(ValidationError, match="raw secret"):
        attached.candidates[0].model_copy(
            update={"normalized_claim": "api_key=plaintext-value"}
        )


def test_exact_same_key_and_request_is_an_idempotent_replay():
    drafts = _drafts("poc_customer_alpha")
    service = _source_service(drafts)
    prepared = _prepared()

    first = service.attach(
        "poc_customer_alpha",
        prepared,
        "attach-key",
    )
    replay = service.attach(
        "poc_customer_alpha",
        prepared,
        "attach-key",
    )

    assert first.disposition == SourceAttachDisposition.CREATED
    assert replay.disposition == SourceAttachDisposition.IDEMPOTENT_REPLAY
    assert replay.source is first.source
    assert len(service) == 1


def test_same_key_changed_request_conflicts_without_mutating_store():
    drafts = _drafts("poc_customer_alpha")
    service = _source_service(drafts)
    service.attach("poc_customer_alpha", _prepared(), "conflict-key")

    with pytest.raises(
        POCSourceIdempotencyConflict,
        match="does not match",
    ) as error:
        service.attach(
            "poc_customer_alpha",
            _prepared(kind=SourceKind.DOCUMENT),
            "conflict-key",
        )

    assert "conflict-key" not in str(error.value)
    assert SOURCE_TEXT not in str(error.value)
    assert len(service) == 1


def test_different_key_same_identity_and_digest_replays_without_duplicate():
    drafts = _drafts("poc_customer_alpha")
    service = _source_service(drafts)
    first = service.attach(
        "poc_customer_alpha",
        _prepared(),
        "tab-one",
    )

    replay = service.attach(
        "poc_customer_alpha",
        _prepared(
            adapter_version="1.0.1",
            candidates=(),
        ),
        "tab-two",
    )

    assert replay.disposition == SourceAttachDisposition.IDENTITY_REPLAY
    assert replay.source is first.source
    assert len(service) == 1


def test_changed_identity_content_requires_explicit_latest_revision():
    drafts = _drafts("poc_customer_alpha")
    service = _source_service(drafts)
    service.attach("poc_customer_alpha", _prepared(), "initial")

    with pytest.raises(POCSourceRevisionRequired, match="explicit"):
        service.attach(
            "poc_customer_alpha",
            _prepared(
                text="Revised redacted requirement [EMAIL].",
                candidates=(),
            ),
            "changed-without-revision",
        )

    assert len(service) == 1


def test_explicit_revision_appends_history_without_overwriting_prior_source():
    drafts = _drafts("poc_customer_alpha")
    service = _source_service(
        drafts,
        ids=("src_generated_001", "src_generated_002"),
    )
    first = service.attach(
        "poc_customer_alpha",
        _prepared(),
        "initial",
    ).source
    second = service.attach(
        "poc_customer_alpha",
        _prepared(
            text="Revised redacted requirement [EMAIL].",
            candidates=(),
            revises_source_id=first.source_id,
        ),
        "explicit-revision",
    ).source

    assert first.source_revision == 1
    assert first.revises_source_id is None
    assert second.source_revision == 2
    assert second.revises_source_id == first.source_id
    assert second.source_sequence == 2
    assert service.snapshots("poc_customer_alpha") == (first, second)
    assert service.get("poc_customer_alpha", first.source_id) is first
    assert (
        service.latest_for_identity(
            "poc_customer_alpha",
            SourceKind.EMAIL,
            "email.message-001",
        )
        is second
    )


def test_stale_revision_pointer_fails_closed_and_preserves_history():
    drafts = _drafts("poc_customer_alpha")
    service = _source_service(
        drafts,
        ids=("src_generated_001", "src_generated_002"),
    )
    first = service.attach(
        "poc_customer_alpha",
        _prepared(),
        "initial",
    ).source
    second = service.attach(
        "poc_customer_alpha",
        _prepared(
            text="Revision two [EMAIL].",
            candidates=(),
            revises_source_id=first.source_id,
        ),
        "revision-two",
    ).source

    with pytest.raises(POCSourceStaleRevision, match="latest"):
        service.attach(
            "poc_customer_alpha",
            _prepared(
                text="Revision three [EMAIL].",
                candidates=(),
                revises_source_id=first.source_id,
            ),
            "stale-revision",
        )

    assert service.snapshots("poc_customer_alpha") == (first, second)


def test_concurrent_tabs_create_exactly_one_source_write():
    drafts = _drafts("poc_customer_alpha")
    service = _source_service(drafts)
    prepared = _prepared()

    with ThreadPoolExecutor(max_workers=16) as executor:
        results = tuple(
            executor.map(
                lambda number: service.attach(
                    "poc_customer_alpha",
                    prepared,
                    "tab-{0}".format(number),
                ),
                range(32),
            )
        )

    assert sum(result.created for result in results) == 1
    assert {result.source.source_id for result in results} == {"src_generated_001"}
    assert len(service) == 1


def test_cross_poc_idempotency_reuse_cannot_jump_attachment():
    drafts = _drafts("poc_customer_alpha", "poc_customer_beta")
    service = _source_service(drafts)
    first = service.attach(
        "poc_customer_alpha",
        _prepared(),
        "shared-key",
    ).source

    with pytest.raises(POCSourceIdempotencyConflict):
        service.attach(
            "poc_customer_beta",
            _prepared(),
            "shared-key",
        )

    assert service.snapshots("poc_customer_alpha") == (first,)
    assert service.snapshots("poc_customer_beta") == ()


def test_same_external_identity_is_independently_scoped_to_each_poc():
    drafts = _drafts("poc_customer_alpha", "poc_customer_beta")
    service = _source_service(
        drafts,
        ids=("src_generated_001", "src_generated_002"),
    )

    alpha = service.attach(
        "poc_customer_alpha",
        _prepared(),
        "alpha-key",
    ).source
    beta = service.attach(
        "poc_customer_beta",
        _prepared(),
        "beta-key",
    ).source

    assert alpha.poc_id == "poc_customer_alpha"
    assert beta.poc_id == "poc_customer_beta"
    assert alpha.source_id != beta.source_id
    assert service.poc_ids() == (
        "poc_customer_alpha",
        "poc_customer_beta",
    )


def test_nonexistent_wrong_and_archived_draft_lookups_block_attachment():
    drafts = _drafts("poc_customer_alpha")
    service = _source_service(drafts)

    with pytest.raises(POCSourceDraftUnavailable):
        service.attach(
            "poc_missing_001",
            _prepared(),
            "missing",
        )
    assert len(service) == 0

    wrong_lookup = ProcessLocalPOCSourceService(
        draft_lookup=lambda _poc_id: drafts.get("poc_customer_alpha"),
    )
    with pytest.raises(POCSourceDraftUnavailable, match="requested"):
        wrong_lookup.attach(
            "poc_missing_001",
            _prepared(),
            "wrong",
        )

    drafts.archive("poc_customer_alpha")
    with pytest.raises(POCSourceDraftArchived):
        service.attach(
            "poc_customer_alpha",
            _prepared(),
            "archived",
        )
    assert len(service) == 0


def test_attachment_never_mutates_the_draft_service_snapshot():
    drafts = _drafts("poc_customer_alpha")
    before = drafts.get("poc_customer_alpha")
    service = _source_service(drafts)

    service.attach(
        "poc_customer_alpha",
        _prepared(),
        "attach",
    )

    after = drafts.get("poc_customer_alpha")
    assert after is before
    assert after.source_ingestion_state.value == "NOT_STARTED"
    assert not hasattr(after, "sources")


def test_source_candidate_and_poc_capacities_fail_atomically():
    drafts = _drafts("poc_customer_alpha", "poc_customer_beta")
    service = _source_service(
        drafts,
        ids=("src_generated_001", "src_generated_002"),
        max_pocs=1,
        max_sources_per_poc=1,
        max_candidates_per_source=1,
    )
    first = service.attach(
        "poc_customer_alpha",
        _prepared(),
        "first",
    ).source

    with pytest.raises(POCSourceCapacityExceeded, match="source capacity"):
        service.attach(
            "poc_customer_alpha",
            _prepared(
                kind=SourceKind.DOCUMENT,
                external_id="document.notes-001",
            ),
            "second-source",
        )
    with pytest.raises(POCSourceCapacityExceeded, match="POC source"):
        service.attach(
            "poc_customer_beta",
            _prepared(),
            "second-poc",
        )
    assert service.snapshots("poc_customer_alpha") == (first,)
    assert service.snapshots("poc_customer_beta") == ()

    candidate_limited = _source_service(
        drafts,
        max_candidates_per_source=1,
    )
    with pytest.raises(POCSourceCapacityExceeded, match="candidate"):
        candidate_limited.attach(
            "poc_customer_alpha",
            _prepared(
                candidates=(
                    _candidate(),
                    _candidate(
                        candidate_id="cand_errors_001",
                        source_quote="Credential [SECRET]",
                        normalized_claim="Errors should remain bounded.",
                    ),
                )
            ),
            "too-many-candidates",
        )
    assert len(candidate_limited) == 0


def test_idempotency_capacity_blocks_unrecordable_identity_replay():
    drafts = _drafts("poc_customer_alpha")
    service = _source_service(
        drafts,
        max_idempotency_records=1,
    )
    first = service.attach(
        "poc_customer_alpha",
        _prepared(),
        "first",
    ).source

    with pytest.raises(POCSourceCapacityExceeded, match="idempotency"):
        service.attach(
            "poc_customer_alpha",
            _prepared(),
            "new-key",
        )

    assert service.snapshots("poc_customer_alpha") == (first,)


def test_invalid_or_duplicate_generated_source_id_leaves_no_partial_write():
    drafts = _drafts("poc_customer_alpha")
    invalid = _source_service(drafts, ids=("not-a-source-id",))
    with pytest.raises(ValueError, match="source_id"):
        invalid.attach(
            "poc_customer_alpha",
            _prepared(),
            "invalid-id",
        )
    assert len(invalid) == 0

    duplicate = _source_service(
        drafts,
        ids=("src_generated_001", "src_generated_001"),
    )
    first = duplicate.attach(
        "poc_customer_alpha",
        _prepared(),
        "first",
    ).source
    with pytest.raises(DuplicatePOCSourceId):
        duplicate.attach(
            "poc_customer_alpha",
            _prepared(
                kind=SourceKind.DOCUMENT,
                external_id="document.notes-001",
            ),
            "duplicate-id",
        )
    assert duplicate.snapshots("poc_customer_alpha") == (first,)


def test_reads_are_side_effect_free_and_cross_poc_get_fails_closed():
    drafts = _drafts("poc_customer_alpha", "poc_customer_beta")
    service = _source_service(drafts)
    source = service.attach(
        "poc_customer_alpha",
        _prepared(),
        "attach",
    ).source
    before = service.snapshots("poc_customer_alpha")

    assert service.source_ids("poc_customer_alpha") == (source.source_id,)
    assert service.snapshots("poc_missing_001") == ()
    with pytest.raises(POCSourceNotFound):
        service.get("poc_customer_beta", source.source_id)
    with pytest.raises(POCSourceNotFound):
        service.latest_for_identity(
            "poc_customer_beta",
            SourceKind.EMAIL,
            source.external_id,
        )

    assert service.snapshots("poc_customer_alpha") is before
    assert len(service) == 1


def test_machine_readable_semantics_are_explicitly_non_durable():
    drafts = _drafts("poc_customer_alpha")
    service = _source_service(
        drafts,
        max_pocs=2,
        max_sources_per_poc=3,
        max_candidates_per_source=4,
        max_idempotency_records=5,
    )
    semantics = service.semantics

    assert semantics.storage_scope == "PROCESS_LOCAL"
    assert semantics.survives_process_restart is False
    assert semantics.shared_across_workers is False
    assert semantics.append_only_source_history is True
    assert semantics.prepared_candidates_are_review_input_only is True
    assert semantics.max_pocs == 2
    assert semantics.max_sources_per_poc == 3
    assert semantics.max_candidates_per_source == 4
    assert semantics.max_idempotency_records == 5


def test_domain_exposes_zero_workflow_or_decision_authority():
    forbidden_tokens = (
        "approve",
        "confirmation",
        "freeze",
        "run",
        "execute",
        "evidence",
        "verdict",
    )
    public_methods = {
        name for name in dir(ProcessLocalPOCSourceService) if not name.startswith("_")
    }
    model_fields = set(PreparedPOCSource.model_fields)
    model_fields.update(PreparedRequirementCandidate.model_fields)

    for token in forbidden_tokens:
        assert all(token not in name.lower() for name in public_methods)
        assert all(token not in name.lower() for name in model_fields)
    assert CandidateState.__members__ == {"NEEDS_REVIEW": CandidateState.NEEDS_REVIEW}
    assert not hasattr(poc_sources, "ContractStatus")
    assert not hasattr(poc_sources, "VerdictStatus")


def test_timezone_naive_provenance_or_service_clock_is_rejected_atomically():
    with pytest.raises(ValidationError, match="timezone-aware"):
        _prepared(observed_at=datetime(2026, 7, 28, 20, 0))

    drafts = _drafts("poc_customer_alpha")
    service = _source_service(
        drafts,
        clock=lambda: datetime(2026, 7, 28, 20, 5),
    )
    with pytest.raises(ValidationError, match="timezone-aware"):
        service.attach(
            "poc_customer_alpha",
            _prepared(),
            "naive-clock",
        )
    assert len(service) == 0


@pytest.mark.parametrize(
    ("option", "value"),
    (
        ("max_pocs", 0),
        ("max_sources_per_poc", 0),
        ("max_candidates_per_source", 0),
        ("max_candidates_per_source", 65),
        ("max_idempotency_records", 0),
    ),
)
def test_service_capacities_are_strictly_bounded(option: str, value: int):
    with pytest.raises(ValueError, match=option):
        ProcessLocalPOCSourceService(
            draft_lookup=lambda _poc_id: None,
            **{option: value},
        )


@pytest.mark.parametrize(
    "key",
    ("", " ", "\n", "x" * 201, None, 123),
)
def test_invalid_idempotency_keys_never_write(key: object):
    drafts = _drafts("poc_customer_alpha")
    service = _source_service(drafts)

    with pytest.raises(ValueError, match="idempotency_key"):
        service.attach("poc_customer_alpha", _prepared(), key)
    assert len(service) == 0
