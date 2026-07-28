from __future__ import annotations

import ast
import hashlib
import json
import pickle
import threading
from copy import copy
from dataclasses import FrozenInstanceError, asdict, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import pytest

from exitspec.demo_data import support_agent_email_paths
from exitspec.source_models import (
    ApprovedSyntheticFixture,
    CandidateProjection,
    PartKind,
    PreparedCandidateDraft,
    PreparedSourceEnvelope,
    PreparedSourceImport,
    PrivateSourceSerializationError,
    RedactedHeaders,
    RedactionCounts,
    RedactionSummary,
    SourceEnvelope,
    SourceMessage,
    SourceModelValidationError,
    SourcePart,
    compute_message_key,
    compute_redacted_header_sha256,
    compute_source_id,
    finalize_source_envelope,
)
from exitspec.source_store import (
    SourceImportOutcome,
    SourceImportReceipt,
    SourceStore,
    SourceStoreCounts,
    SourceStoreReentrancyError,
    _PrivateIdempotencyRecord,
)


_OBSERVED_AT = "2026-07-27T19:00:00Z"
_CLOCK_TIME = datetime(2026, 7, 27, 19, 0, 1, tzinfo=timezone.utc)
_OTHER_MESSAGE_KEY = "msg:" + ("f" * 64)
_OTHER_SOURCE_ID = "rfc822:" + ("f" * 64)
_RECEIPT_FIELDS = {
    "source_type",
    "manifest_id",
    "manifest_version",
    "fixture_case_id",
    "outcome_code",
    "source_version",
    "candidate_count",
}

_VECTORS = {
    "thread-root": {
        "message_id": "support-poc-001@customer.example",
        "root_id": "support-poc-001@customer.example",
        "authored_at": "2026-07-27T16:00:00Z",
        "from": "Priya Customer <[EMAIL]>",
        "subject": "[CUSTOMER_TERM] support-agent POC requirements",
        "to": "Alex Engineer <[EMAIL]>",
        "text": (
            "Hello Alex,\n\n"
            "The support agent must select the correct tool in at least "
            "95% of 200 cases.\n"
            "P95 end-to-end latency must remain below 2 seconds.\n"
            "Please leave any unsupported requirement unresolved.\n\n"
            "Customer contact: [EMAIL]\n"
            "Phone: [PHONE]\n"
            "Project codename: [CUSTOMER_TERM].\n"
            "Synthetic credential: [SECRET]\n"
        ),
    },
    "thread-follow-up": {
        "message_id": "support-poc-002@customer.example",
        "root_id": "support-poc-001@customer.example",
        "authored_at": "2026-07-27T16:30:00Z",
        "from": "Priya Customer <[EMAIL]>",
        "subject": "Re: [CUSTOMER_TERM] support-agent POC requirements",
        "to": "Alex Engineer <[EMAIL]>",
        "text": (
            "One follow-up:\n\n"
            "The total model-and-tool cost must stay at or below $0.04 "
            "per resolved case.\n"
            "Keep the original 95% quality target unchanged.\n\n"
            "Customer contact: [EMAIL]\n"
        ),
    },
}


@pytest.fixture(scope="module")
def manifest() -> dict[str, Any]:
    with support_agent_email_paths() as paths:
        return json.loads(paths.manifest.read_text(encoding="utf-8"))


def _record(manifest: dict[str, Any], case_id: str) -> dict[str, Any]:
    return next(
        item
        for item in manifest["fixture_set"]["fixtures"]
        if item["case_id"] == case_id
    )


def _prepared_envelope(
    manifest: dict[str, Any],
    case_id: str,
    *,
    source_id: str | None = None,
) -> PreparedSourceEnvelope:
    vector = _VECTORS[case_id]
    record = _record(manifest, case_id)
    headers = RedactedHeaders(
        authored_at=vector["authored_at"],
        **{"from": vector["from"]},
        subject=vector["subject"],
        to=vector["to"],
    )
    text = vector["text"]
    part = SourcePart(
        part_path="body:text/plain:0",
        kind=PartKind.BODY,
        redacted_text=text,
        redacted_text_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )
    message = SourceMessage(
        message_key=compute_message_key(vector["message_id"]),
        redacted_headers=headers,
        redacted_header_sha256=compute_redacted_header_sha256(headers),
        parts=(part,),
    )
    drafts = tuple(
        PreparedCandidateDraft(
            candidate_type=candidate["candidate_type"],
            state=candidate["state"],
            projection=CandidateProjection(**candidate["projection"]),
            message_key=candidate["message_key"],
            part_path=candidate["part_path"],
            start_byte=candidate["start_byte"],
            end_byte=candidate["end_byte"],
            quote_sha256=candidate["quote_sha256"],
        )
        for candidate in record["expected_candidates"]
    )
    return PreparedSourceEnvelope(
        source_id=source_id or compute_source_id(vector["root_id"]),
        observed_at=_OBSERVED_AT,
        redaction=RedactionSummary(
            counts=RedactionCounts(**record["expected_redaction_counts"])
        ),
        message=message,
        candidate_drafts=drafts,
    )


def _prepared_import(
    manifest: dict[str, Any],
    case_id: str,
    *,
    marker_case_id: str | None = None,
    marker_sha256: str | None = None,
    root_id: str | None = None,
    root_key: str | None = None,
    source_id: str | None = None,
) -> PreparedSourceImport:
    content_case_id = (
        "thread-root" if case_id == "thread-root-mutated" else case_id
    )
    fixture_case_id = marker_case_id or case_id
    fixture_record = _record(manifest, fixture_case_id)
    normalized_root = root_id or _VECTORS[content_case_id]["root_id"]
    return PreparedSourceImport(
        approved_synthetic_fixture=ApprovedSyntheticFixture(
            manifest_id=manifest["manifest_id"],
            manifest_version=manifest["manifest_version"],
            fixture_case_id=fixture_case_id,
            synthetic_fixture_sha256=(
                marker_sha256 or fixture_record["sha256"]
            ),
        ),
        normalized_thread_root_message_id=normalized_root,
        thread_root_message_key=(
            root_key or compute_message_key(normalized_root)
        ),
        prepared_envelope=_prepared_envelope(
            manifest,
            content_case_id,
            source_id=source_id,
        ),
    )


def _distinct_follow_up(
    manifest: dict[str, Any],
    message_id: str,
) -> PreparedSourceImport:
    base = _prepared_import(manifest, "thread-follow-up")
    prepared = base.prepared_envelope
    base_message = prepared.message
    message_key = compute_message_key(message_id)
    message = SourceMessage(
        message_key=message_key,
        redacted_headers=base_message.redacted_headers,
        redacted_header_sha256=base_message.redacted_header_sha256,
        parts=base_message.parts,
    )
    drafts = tuple(
        PreparedCandidateDraft(
            **{
                **draft.model_dump(),
                "message_key": message_key,
            }
        )
        for draft in prepared.candidate_drafts
    )
    envelope = PreparedSourceEnvelope(
        **{
            **prepared.model_dump(),
            "message": message,
            "candidate_drafts": drafts,
        }
    )
    return PreparedSourceImport(
        approved_synthetic_fixture=ApprovedSyntheticFixture(
            manifest_id=manifest["manifest_id"],
            manifest_version=manifest["manifest_version"],
            fixture_case_id="thread-follow-up",
            synthetic_fixture_sha256=_record(
                manifest,
                "thread-follow-up",
            )["sha256"],
        ),
        normalized_thread_root_message_id=_VECTORS[
            "thread-root"
        ]["root_id"],
        thread_root_message_key=compute_message_key(
            _VECTORS["thread-root"]["root_id"]
        ),
        prepared_envelope=envelope,
    )


def _fixed_clock() -> datetime:
    return _CLOCK_TIME


def _store(
    *,
    clock: Callable[[], datetime] = _fixed_clock,
    finalizer: Callable[..., SourceEnvelope] | None = None,
) -> SourceStore:
    if finalizer is None:
        return SourceStore(clock=clock)
    return SourceStore(clock=clock, finalizer=finalizer)


def _assert_empty(store: SourceStore) -> None:
    assert store.counts() == SourceStoreCounts(0, 0, 0, 0, 0)


def test_receipt_has_exact_fields_and_no_browser_timing(
    manifest: dict[str, Any],
) -> None:
    result = _store().import_prepared(
        _prepared_import(manifest, "thread-root")
    )

    assert {field.name for field in fields(SourceImportReceipt)} == _RECEIPT_FIELDS
    assert set(result.receipt.to_dict()) == _RECEIPT_FIELDS
    assert set(json.loads(result.receipt.to_json())) == _RECEIPT_FIELDS
    assert result.receipt.to_dict() == {
        "source_type": "rfc822",
        "manifest_id": manifest["manifest_id"],
        "manifest_version": "1.0.1",
        "fixture_case_id": "thread-root",
        "outcome_code": "accepted",
        "source_version": 1,
        "candidate_count": 2,
    }
    assert "elapsed_ms" not in result.receipt.to_json()
    with pytest.raises(FrozenInstanceError):
        result.receipt.candidate_count = 99
    with pytest.raises(FrozenInstanceError):
        result.envelope = None
    with pytest.raises(FrozenInstanceError):
        _store().counts().candidate_count = 99


def test_root_and_follow_up_match_manifest_vectors(
    manifest: dict[str, Any],
) -> None:
    store = _store()
    root = store.import_prepared(_prepared_import(manifest, "thread-root"))
    follow_up = store.import_prepared(
        _prepared_import(manifest, "thread-follow-up")
    )
    root_record = _record(manifest, "thread-root")
    follow_up_record = _record(manifest, "thread-follow-up")

    assert root.receipt.outcome_code == SourceImportOutcome.ACCEPTED.value
    assert root.envelope is not None
    assert root.envelope.version_id == root_record["expected_version_id"]
    assert root.envelope.content_sha256 == root_record["expected_content_sha256"]
    assert follow_up.receipt.to_dict() == {
        "source_type": "rfc822",
        "manifest_id": manifest["manifest_id"],
        "manifest_version": "1.0.1",
        "fixture_case_id": "thread-follow-up",
        "outcome_code": "accepted_new_version",
        "source_version": 2,
        "candidate_count": 1,
    }
    assert follow_up.envelope is not None
    assert follow_up.envelope.version_id == follow_up_record["expected_version_id"]
    assert (
        follow_up.envelope.content_sha256
        == follow_up_record["expected_content_sha256"]
    )
    assert len(follow_up.envelope.messages) == 2
    assert len(follow_up.envelope.candidates) == 1
    assert store.counts() == SourceStoreCounts(1, 2, 3, 2, 2)


def test_replay_after_later_version_returns_original_without_writes(
    manifest: dict[str, Any],
) -> None:
    store = _store()
    first = store.import_prepared(_prepared_import(manifest, "thread-root"))
    store.import_prepared(_prepared_import(manifest, "thread-follow-up"))
    before_state = store._state
    before_counts = store.counts()

    replay = store.import_prepared(_prepared_import(manifest, "thread-root"))

    assert replay.receipt.outcome_code == "duplicate_replay"
    assert replay.receipt.source_version == 1
    assert replay.receipt.candidate_count == 0
    assert replay.envelope is first.envelope
    assert replay.envelope is store.version(
        _record(manifest, "thread-root")["expected_source_id"],
        1,
    )
    assert store._state is before_state
    assert store.counts() == before_counts


def test_changed_fixture_digest_is_identity_conflict_and_zero_write(
    manifest: dict[str, Any],
) -> None:
    store = _store()
    store.import_prepared(_prepared_import(manifest, "thread-root"))
    before_state = store._state
    mutated = _prepared_import(
        manifest,
        "thread-root-mutated",
        marker_case_id="thread-root-mutated",
    )

    result = store.import_prepared(mutated)

    assert result.receipt.outcome_code == "source_identity_conflict"
    assert result.receipt.source_version is None
    assert result.receipt.candidate_count == 0
    assert result.envelope is None
    assert store._state is before_state
    assert store.counts() == SourceStoreCounts(1, 1, 2, 1, 1)


def test_unknown_parent_precedes_replay_and_is_not_binding_mismatch(
    manifest: dict[str, Any],
) -> None:
    unknown_root = "unknown-thread-root@customer.example"
    request = _prepared_import(
        manifest,
        "thread-follow-up",
        root_id=unknown_root,
        source_id=compute_source_id(unknown_root),
    )
    store = _store()
    before_state = store._state

    result = store.import_prepared(request)

    assert result.receipt.outcome_code == "thread_parent_not_found"
    assert result.receipt.candidate_count == 0
    assert store._state is before_state
    _assert_empty(store)


@pytest.mark.parametrize(
    "request_factory",
    (
        lambda manifest: _prepared_import(
            manifest,
            "thread-root",
            root_key=_OTHER_MESSAGE_KEY,
        ),
        lambda manifest: _prepared_import(
            manifest,
            "thread-root",
            source_id=_OTHER_SOURCE_ID,
        ),
    ),
)
def test_binding_mismatch_is_first_and_consumes_no_version(
    manifest: dict[str, Any],
    request_factory: Callable[[dict[str, Any]], PreparedSourceImport],
) -> None:
    store = _store()
    before_state = store._state

    refused = store.import_prepared(request_factory(manifest))
    accepted = store.import_prepared(_prepared_import(manifest, "thread-root"))

    assert refused.receipt.outcome_code == "source_thread_binding_mismatch"
    assert refused.receipt.source_version is None
    assert refused.receipt.candidate_count == 0
    assert before_state.accepted_write_transaction_count == 0
    assert accepted.receipt.source_version == 1
    assert store.counts() == SourceStoreCounts(1, 1, 2, 1, 1)


def test_existing_root_index_for_wrong_source_is_binding_mismatch(
    manifest: dict[str, Any],
) -> None:
    store = _store()
    request = _prepared_import(manifest, "thread-root")
    state = store._state
    store._state = type(state)(
        root_sources={request.thread_root_message_key: _OTHER_SOURCE_ID},
        latest_by_source=state.latest_by_source,
        versions=state.versions,
        candidates_by_version=state.candidates_by_version,
        idempotency_by_message=state.idempotency_by_message,
        accepted_write_transaction_count=0,
    )
    before_state = store._state

    result = store.import_prepared(request)

    assert result.receipt.outcome_code == "source_thread_binding_mismatch"
    assert store._state is before_state


def test_receipt_json_never_contains_private_or_content_identifiers(
    manifest: dict[str, Any],
) -> None:
    request = _prepared_import(manifest, "thread-root")
    result = _store().import_prepared(request)
    assert result.envelope is not None
    receipt_json = result.receipt.to_json()
    forbidden = {
        request.normalized_thread_root_message_id,
        request.thread_root_message_key,
        request.approved_synthetic_fixture.synthetic_fixture_sha256,
        request.prepared_envelope.message.message_key,
        result.envelope.source_id,
        result.envelope.version_id,
        result.envelope.content_sha256,
        result.envelope.messages[0].redacted_header_sha256,
    }

    assert all(value not in receipt_json for value in forbidden)
    assert "synthetic_fixture_sha256" not in receipt_json
    assert "thread_root_message_key" not in receipt_json
    assert "content_sha256" not in receipt_json


def test_private_record_refuses_repr_serialization_pickle_and_dict_probes() -> None:
    digest = "a" * 64
    source_id = "rfc822:" + ("b" * 64)
    version_id = "srcv:" + ("c" * 64)
    root_key = "msg:" + ("d" * 64)
    record = _PrivateIdempotencyRecord(
        message_key=root_key,
        synthetic_fixture_sha256=digest,
        source_id=source_id,
        source_version=1,
        version_id=version_id,
    )

    rendered = repr(record)
    assert rendered == "_PrivateIdempotencyRecord(<private>)"
    assert all(
        value not in rendered
        for value in (digest, source_id, version_id, root_key)
    )
    with pytest.raises(TypeError):
        vars(record)
    with pytest.raises(TypeError):
        dict(record)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        asdict(record)  # type: ignore[arg-type]
    with pytest.raises(PrivateSourceSerializationError):
        pickle.dumps(record)
    with pytest.raises(PrivateSourceSerializationError):
        copy(record)
    assert not hasattr(record, "model_dump")
    assert not hasattr(record, "to_dict")
    assert not hasattr(record, "to_json")
    assert set(record.__slots__) == {
        "__message_key",
        "__synthetic_fixture_sha256",
        "__source_id",
        "__source_version",
        "__version_id",
    }


def test_finalizer_failure_is_safe_atomic_and_does_not_consume_version(
    manifest: dict[str, Any],
) -> None:
    calls = 0

    def fail_finalizer(*args: Any, **kwargs: Any) -> SourceEnvelope:
        nonlocal calls
        calls += 1
        raise SourceModelValidationError()

    store = _store(finalizer=fail_finalizer)
    before_state = store._state
    refused = store.import_prepared(_prepared_import(manifest, "thread-root"))

    assert calls == 1
    assert refused.receipt.outcome_code == "source_link_violation"
    assert refused.receipt.source_version is None
    assert refused.receipt.candidate_count == 0
    assert refused.envelope is None
    assert store._state is before_state
    _assert_empty(store)

    replacement = _store()
    accepted = replacement.import_prepared(
        _prepared_import(manifest, "thread-root")
    )
    assert accepted.receipt.source_version == 1


@pytest.mark.parametrize("callback_name", ("clock", "finalizer"))
def test_same_thread_callback_reentry_is_rejected_without_writes(
    manifest: dict[str, Any],
    callback_name: str,
) -> None:
    store: SourceStore
    request = _prepared_import(manifest, "thread-root")

    def reentering_clock() -> datetime:
        store.import_prepared(request)
        return _CLOCK_TIME

    def reentering_finalizer(
        *args: Any,
        **kwargs: Any,
    ) -> SourceEnvelope:
        store.import_prepared(request)
        return finalize_source_envelope(*args, **kwargs)

    store = _store(
        clock=(
            reentering_clock
            if callback_name == "clock"
            else _fixed_clock
        ),
        finalizer=(
            reentering_finalizer
            if callback_name == "finalizer"
            else finalize_source_envelope
        ),
    )
    before_state = store._state

    with pytest.raises(
        SourceStoreReentrancyError,
        match="source_store_transaction_reentry",
    ) as caught:
        store.import_prepared(request)

    assert caught.value.args == ("source_store_transaction_reentry",)
    assert store._state is before_state
    _assert_empty(store)


@pytest.mark.parametrize(
    "failure",
    (
        TypeError("programmer bug"),
        ValueError("programmer bug"),
        RuntimeError("programmer bug"),
    ),
)
def test_unexpected_callback_errors_propagate_without_writes(
    manifest: dict[str, Any],
    failure: Exception,
) -> None:
    def fail_finalizer(*args: Any, **kwargs: Any) -> SourceEnvelope:
        raise failure

    store = _store(finalizer=fail_finalizer)
    before_state = store._state

    with pytest.raises(type(failure), match="programmer bug"):
        store.import_prepared(_prepared_import(manifest, "thread-root"))

    assert store._state is before_state
    _assert_empty(store)


def test_duplicate_threads_contend_for_one_transaction_lock(
    manifest: dict[str, Any],
) -> None:
    first_finalizer_entered = threading.Event()
    release_first_finalizer = threading.Event()
    finalizer_calls = 0

    def blocking_finalizer(
        *args: Any,
        **kwargs: Any,
    ) -> SourceEnvelope:
        nonlocal finalizer_calls
        finalizer_calls += 1
        first_finalizer_entered.set()
        assert release_first_finalizer.wait(timeout=5)
        return finalize_source_envelope(*args, **kwargs)

    store = _store(finalizer=blocking_finalizer)
    request = _prepared_import(manifest, "thread-root")
    results: list[Any] = []
    errors: list[BaseException] = []

    def import_request() -> None:
        try:
            results.append(store.import_prepared(request))
        except BaseException as exc:
            errors.append(exc)

    first = threading.Thread(target=import_request)
    second = threading.Thread(target=import_request)
    first.start()
    assert first_finalizer_entered.wait(timeout=5)
    second.start()
    assert second.is_alive()
    assert len(results) == 0
    assert finalizer_calls == 1

    release_first_finalizer.set()
    first.join(timeout=5)
    second.join(timeout=5)

    assert not errors
    assert not first.is_alive()
    assert not second.is_alive()
    assert finalizer_calls == 1
    assert {
        result.receipt.outcome_code for result in results
    } == {"accepted", "duplicate_replay"}
    assert store.counts() == SourceStoreCounts(1, 1, 2, 1, 1)


def test_distinct_follow_ups_race_without_lost_versions(
    manifest: dict[str, Any],
) -> None:
    store = _store()
    root = store.import_prepared(_prepared_import(manifest, "thread-root"))
    assert root.receipt.outcome_code == "accepted"
    requests = (
        _distinct_follow_up(
            manifest,
            "support-poc-concurrent-a@customer.example",
        ),
        _distinct_follow_up(
            manifest,
            "support-poc-concurrent-b@customer.example",
        ),
    )
    barrier = threading.Barrier(2)
    results: list[Any] = []
    errors: list[BaseException] = []

    def import_request(request: PreparedSourceImport) -> None:
        try:
            barrier.wait(timeout=5)
            results.append(store.import_prepared(request))
        except BaseException as exc:
            errors.append(exc)

    threads = [
        threading.Thread(target=import_request, args=(request,))
        for request in requests
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert not errors
    assert all(not thread.is_alive() for thread in threads)
    assert {
        result.receipt.outcome_code for result in results
    } == {"accepted_new_version"}
    assert sorted(
        result.receipt.source_version for result in results
    ) == [2, 3]
    assert [
        envelope.source_version
        for envelope in store.history(
            _record(manifest, "thread-root")["expected_source_id"]
        )
    ] == [1, 2, 3]
    assert store.counts() == SourceStoreCounts(1, 3, 4, 3, 3)


def _run_concurrent_duplicates(
    manifest: dict[str, Any],
    first_actor: str,
    request_case_id: str,
    *,
    seed_root: bool,
) -> tuple[SourceStore, dict[str, Any]]:
    store = _store()
    if seed_root:
        seeded = store.import_prepared(
            _prepared_import(manifest, "thread-root")
        )
        assert seeded.receipt.outcome_code == "accepted"
    barrier = threading.Barrier(2)
    first_done = threading.Event()
    results: dict[str, Any] = {}
    errors: list[BaseException] = []

    def actor(name: str) -> None:
        try:
            request = _prepared_import(manifest, request_case_id)
            barrier.wait(timeout=5)
            if name != first_actor:
                assert first_done.wait(timeout=5)
            results[name] = store.import_prepared(request)
            if name == first_actor:
                first_done.set()
        except BaseException as exc:  # test thread must surface every failure
            errors.append(exc)
            first_done.set()

    threads = [
        threading.Thread(target=actor, args=("import-a",)),
        threading.Thread(target=actor, args=("import-b",)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert not errors
    assert all(not thread.is_alive() for thread in threads)
    return store, results


@pytest.mark.parametrize("first_actor", ("import-a", "import-b"))
def test_manifest_concurrent_root_duplicate_oracle_for_both_commit_orders(
    manifest: dict[str, Any],
    first_actor: str,
) -> None:
    store, results = _run_concurrent_duplicates(
        manifest,
        first_actor,
        "thread-root",
        seed_root=False,
    )

    assert results[first_actor].receipt.outcome_code == "accepted"
    assert {
        result.receipt.outcome_code for result in results.values()
    } == {"accepted", "duplicate_replay"}
    assert sum(
        result.receipt.candidate_count for result in results.values()
    ) == 2
    assert store.counts() == SourceStoreCounts(1, 1, 2, 1, 1)


@pytest.mark.parametrize("first_actor", ("import-a", "import-b"))
def test_concurrent_follow_up_is_one_version_plus_one_replay(
    manifest: dict[str, Any],
    first_actor: str,
) -> None:
    store, results = _run_concurrent_duplicates(
        manifest,
        first_actor,
        "thread-follow-up",
        seed_root=True,
    )

    assert results[first_actor].receipt.outcome_code == "accepted_new_version"
    assert {
        result.receipt.outcome_code for result in results.values()
    } == {"accepted_new_version", "duplicate_replay"}
    assert sorted(
        result.receipt.candidate_count for result in results.values()
    ) == [0, 1]
    assert store.counts() == SourceStoreCounts(1, 2, 3, 2, 2)


def test_read_methods_return_immutable_models_or_safe_numbers_only(
    manifest: dict[str, Any],
) -> None:
    store = _store()
    accepted = store.import_prepared(
        _prepared_import(manifest, "thread-root")
    )
    assert accepted.envelope is not None
    source_id = accepted.envelope.source_id

    assert store.latest(source_id) is accepted.envelope
    assert store.version(source_id, 1) is accepted.envelope
    assert store.history(source_id) == (accepted.envelope,)
    assert isinstance(store.history(source_id), tuple)
    assert isinstance(store.counts(), SourceStoreCounts)
    with pytest.raises(SourceModelValidationError):
        accepted.envelope.source_version = 2
    assert store.latest(_OTHER_SOURCE_ID) is None
    assert store.version(source_id, 99) is None
    assert store.history(_OTHER_SOURCE_ID) == ()


def test_clock_is_called_only_for_new_publications_and_requires_utc_seconds(
    manifest: dict[str, Any],
) -> None:
    calls: list[datetime] = []

    def clock() -> datetime:
        value = _CLOCK_TIME.replace(second=_CLOCK_TIME.second + len(calls))
        calls.append(value)
        return value

    store = _store(clock=clock)
    root = store.import_prepared(_prepared_import(manifest, "thread-root"))
    replay = store.import_prepared(_prepared_import(manifest, "thread-root"))
    follow = store.import_prepared(
        _prepared_import(manifest, "thread-follow-up")
    )

    assert len(calls) == 2
    assert root.envelope is not None
    assert follow.envelope is not None
    assert root.envelope.ingested_at == "2026-07-27T19:00:01Z"
    assert follow.envelope.ingested_at == "2026-07-27T19:00:02Z"
    assert replay.receipt.outcome_code == "duplicate_replay"

    bad_clock_store = _store(
        clock=lambda: _CLOCK_TIME.replace(microsecond=1)
    )
    before_state = bad_clock_store._state
    refused = bad_clock_store.import_prepared(
        _prepared_import(manifest, "thread-root")
    )
    assert refused.receipt.outcome_code == "source_link_violation"
    assert bad_clock_store._state is before_state
    _assert_empty(bad_clock_store)


def test_store_has_no_egress_raw_source_or_authority_surface() -> None:
    source_path = (
        Path(__file__).parents[1] / "src" / "exitspec" / "source_store.py"
    )
    module = ast.parse(source_path.read_text(encoding="utf-8"))
    imported_roots = {
        alias.name.split(".", 1)[0]
        for node in ast.walk(module)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported_roots.update(
        (node.module or "").split(".", 1)[0]
        for node in ast.walk(module)
        if isinstance(node, ast.ImportFrom)
    )
    forbidden_imports = {
        "email",
        "http",
        "imaplib",
        "mailbox",
        "os",
        "pathlib",
        "requests",
        "smtplib",
        "socket",
        "sqlite3",
        "urllib",
    }
    public_methods = {
        name
        for name in dir(SourceStore)
        if not name.startswith("_")
    }
    forbidden_authority_words = {
        "approve",
        "confirm",
        "freeze",
        "measure",
        "pass",
        "fail",
        "verdict",
    }

    assert imported_roots.isdisjoint(forbidden_imports)
    assert public_methods == {
        "counts",
        "history",
        "import_prepared",
        "latest",
        "version",
    }
    assert all(
        word not in method
        for method in public_methods
        for word in forbidden_authority_words
    )
    assert set(SourceStore.__slots__) == {
        "_clock",
        "_finalizer",
        "_lock",
        "_state",
        "_transaction_local",
    }
    assert all(
        token not in " ".join(SourceStore.__slots__)
        for token in (
            "raw",
            "rfc822",
            "message_id",
            "fixture_digest",
            "elapsed",
        )
    )
