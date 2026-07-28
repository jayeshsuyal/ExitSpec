from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from exitspec.canonical import canonical_json_bytes
from exitspec.performance_receipts import (
    PERFORMANCE_RECEIPT_SCHEMA_VERSION,
    InMemoryPerformanceReceiptStore,
    PerformanceExecutionReceipt,
    PerformanceIdempotencyConflict,
    PerformanceReceiptIntegrityError,
    performance_idempotency_key_digest,
    performance_operation_id,
    performance_receipt_id,
    performance_request_digest,
    validate_performance_receipt,
)


CREATED_AT = datetime(2026, 7, 28, 9, 30, 45, 123456, tzinfo=timezone.utc)
DEFAULTS = {
    "idempotency_key": "latency-demo-001",
    "contract_id": "inference-latency-demo",
    "contract_version": "1.0.0",
    "frozen_contract_hash": "a" * 64,
    "criterion_id": "PERF-LATENCY-01",
    "expected_manifest_sha256": "b" * 64,
    "execution_id": "run_" + "c" * 32,
    "records_sha256": "d" * 64,
    "created_at": CREATED_AT,
}


def record(
    store: InMemoryPerformanceReceiptStore,
    **changes: object,
) -> PerformanceExecutionReceipt:
    inputs = dict(DEFAULTS)
    inputs.update(changes)
    return store.record_receipt(**inputs)


def serialized(receipt: PerformanceExecutionReceipt) -> dict[str, object]:
    return receipt.model_dump(mode="json")


def test_receipt_binds_every_required_field_and_has_exact_identity_shapes():
    receipt = record(InMemoryPerformanceReceiptStore())

    assert serialized(receipt) == {
        "schema_version": PERFORMANCE_RECEIPT_SCHEMA_VERSION,
        "receipt_id": receipt.receipt_id,
        "operation_id": receipt.operation_id,
        "request_digest": receipt.request_digest,
        "contract_id": DEFAULTS["contract_id"],
        "contract_version": DEFAULTS["contract_version"],
        "frozen_contract_hash": DEFAULTS["frozen_contract_hash"],
        "criterion_id": DEFAULTS["criterion_id"],
        "expected_manifest_sha256": DEFAULTS[
            "expected_manifest_sha256"
        ],
        "execution_id": DEFAULTS["execution_id"],
        "records_sha256": DEFAULTS["records_sha256"],
        "idempotency_key_digest": receipt.idempotency_key_digest,
        "created_at": "2026-07-28T09:30:45.123456Z",
    }
    assert len(receipt.receipt_id) == len("prc_") + 64
    assert receipt.receipt_id.startswith("prc_")
    assert len(receipt.operation_id) == len("op_") + 64
    assert receipt.operation_id.startswith("op_")
    assert receipt.execution_id == "run_" + "c" * 32


def test_receipt_is_immutable_and_rejects_unknown_fields():
    receipt = record(InMemoryPerformanceReceiptStore())

    with pytest.raises(ValidationError, match="Instance is frozen"):
        receipt.records_sha256 = "e" * 64

    payload = serialized(receipt)
    payload["undocumented"] = True
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        PerformanceExecutionReceipt.model_validate(payload)


def test_same_key_and_complete_identity_returns_original_receipt():
    store = InMemoryPerformanceReceiptStore()

    original = record(store)
    replay = record(
        store,
        created_at=CREATED_AT + timedelta(days=1),
    )

    assert replay is original
    assert replay.created_at == CREATED_AT
    assert repr(store) == "InMemoryPerformanceReceiptStore(receipt_count=1)"


def test_concurrent_exact_replays_converge_on_one_original_receipt():
    store = InMemoryPerformanceReceiptStore()

    with ThreadPoolExecutor(max_workers=16) as executor:
        receipts = tuple(executor.map(lambda _: record(store), range(64)))

    assert all(receipt is receipts[0] for receipt in receipts)
    assert repr(store) == "InMemoryPerformanceReceiptStore(receipt_count=1)"


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("contract_id", "another-contract"),
        ("contract_version", "2.0.0"),
        ("frozen_contract_hash", "e" * 64),
        ("criterion_id", "PERF-THROUGHPUT-01"),
        ("expected_manifest_sha256", "f" * 64),
        ("execution_id", "run_" + "1" * 32),
        ("records_sha256", "2" * 64),
    ],
)
def test_same_key_with_changed_execution_identity_conflicts(field, replacement):
    store = InMemoryPerformanceReceiptStore()
    record(store)

    with pytest.raises(
        PerformanceIdempotencyConflict,
        match="another request",
    ):
        record(store, **{field: replacement})


def test_different_key_permits_a_new_receipt_for_the_same_execution():
    store = InMemoryPerformanceReceiptStore()

    first = record(store, idempotency_key="latency-demo-001")
    second = record(store, idempotency_key="latency-demo-002")

    assert second is not first
    assert second.request_digest == first.request_digest
    assert second.idempotency_key_digest != first.idempotency_key_digest
    assert second.operation_id != first.operation_id
    assert second.receipt_id != first.receipt_id
    assert repr(store) == "InMemoryPerformanceReceiptStore(receipt_count=2)"


def test_digests_use_domain_separated_rfc8785_canonical_bytes():
    key = str(DEFAULTS["idempotency_key"])
    expected_key_digest = hashlib.sha256(
        b"exitspec-performance-receipt-idempotency-key-v1\x00"
        + canonical_json_bytes({"idempotency_key": key})
    ).hexdigest()
    assert performance_idempotency_key_digest(key) == expected_key_digest

    request_digest = performance_request_digest(
        contract_id=str(DEFAULTS["contract_id"]),
        contract_version=str(DEFAULTS["contract_version"]),
        frozen_contract_hash=str(DEFAULTS["frozen_contract_hash"]),
        criterion_id=str(DEFAULTS["criterion_id"]),
        expected_manifest_sha256=str(
            DEFAULTS["expected_manifest_sha256"]
        ),
        execution_id=str(DEFAULTS["execution_id"]),
        records_sha256=str(DEFAULTS["records_sha256"]),
    )
    differently_ordered = {
        "schema_version": PERFORMANCE_RECEIPT_SCHEMA_VERSION,
        "records_sha256": DEFAULTS["records_sha256"],
        "execution_id": DEFAULTS["execution_id"],
        "frozen_contract_hash": DEFAULTS["frozen_contract_hash"],
        "expected_manifest_sha256": DEFAULTS[
            "expected_manifest_sha256"
        ],
        "criterion_id": DEFAULTS["criterion_id"],
        "contract_version": DEFAULTS["contract_version"],
        "contract_id": DEFAULTS["contract_id"],
    }
    assert request_digest == hashlib.sha256(
        b"exitspec-performance-receipt-request-v1\x00"
        + canonical_json_bytes(differently_ordered)
    ).hexdigest()

    operation_id = performance_operation_id(
        idempotency_key_digest=expected_key_digest,
        request_digest=request_digest,
    )
    assert operation_id.startswith("op_")
    assert operation_id.removeprefix("op_") != request_digest


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("schema_version", "exitspec.performance-execution-receipt.v2"),
        ("contract_id", "tampered-contract"),
        ("contract_version", "9.9.9"),
        ("frozen_contract_hash", "1" * 64),
        ("criterion_id", "PERF-TAMPER-01"),
        ("expected_manifest_sha256", "2" * 64),
        ("execution_id", "run_" + "3" * 32),
        ("records_sha256", "4" * 64),
        ("idempotency_key_digest", "5" * 64),
        ("request_digest", "6" * 64),
        ("operation_id", "op_" + "7" * 64),
        ("receipt_id", "prc_" + "8" * 64),
        (
            "created_at",
            (CREATED_AT + timedelta(seconds=1)).isoformat(),
        ),
    ],
)
def test_reparsing_detects_tampering_of_every_bound_field(field, replacement):
    payload = serialized(record(InMemoryPerformanceReceiptStore()))
    payload[field] = replacement

    with pytest.raises(ValidationError):
        PerformanceExecutionReceipt.model_validate(payload)


def test_integrity_validator_detects_unvalidated_model_copy_tampering():
    receipt = record(InMemoryPerformanceReceiptStore())
    tampered = receipt.model_copy(update={"records_sha256": "1" * 64})

    with pytest.raises(
        PerformanceReceiptIntegrityError,
        match="integrity validation failed",
    ):
        validate_performance_receipt(tampered)


def test_store_refuses_to_replay_a_tampered_internal_receipt():
    store = InMemoryPerformanceReceiptStore()
    receipt = record(store)
    tampered = receipt.model_copy(update={"records_sha256": "1" * 64})
    store._receipts_by_key_digest[receipt.idempotency_key_digest] = tampered

    with pytest.raises(PerformanceReceiptIntegrityError):
        record(store)


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("receipt_id", "prc_" + "A" * 64),
        ("operation_id", "operation_" + "a" * 64),
        ("request_digest", "a" * 63),
        ("frozen_contract_hash", "g" * 64),
        ("expected_manifest_sha256", "A" * 64),
        ("execution_id", "run_" + "a" * 31),
        ("records_sha256", "0x" + "a" * 64),
        ("idempotency_key_digest", "A" * 64),
    ],
)
def test_exact_hash_id_and_run_shapes_are_enforced(field, replacement):
    payload = serialized(record(InMemoryPerformanceReceiptStore()))
    payload[field] = replacement

    with pytest.raises(ValidationError):
        PerformanceExecutionReceipt.model_validate(payload)


def test_naive_created_at_is_rejected_and_offsets_normalize_to_utc():
    with pytest.raises(ValueError, match="timezone-aware"):
        record(
            InMemoryPerformanceReceiptStore(),
            created_at=CREATED_AT.replace(tzinfo=None),
        )

    offset = timezone(timedelta(hours=5, minutes=30))
    receipt = record(
        InMemoryPerformanceReceiptStore(),
        created_at=CREATED_AT.astimezone(offset),
    )
    assert receipt.created_at == CREATED_AT
    assert receipt.created_at.tzinfo == timezone.utc


@pytest.mark.parametrize(
    "idempotency_key",
    [
        "",
        " leading",
        "trailing ",
        "contains\nnewline",
        "x" * 201,
    ],
)
def test_invalid_raw_idempotency_keys_are_rejected(idempotency_key):
    with pytest.raises(ValueError, match="idempotency_key"):
        record(
            InMemoryPerformanceReceiptStore(),
            idempotency_key=idempotency_key,
        )


def test_raw_idempotency_key_is_never_persisted_or_exposed():
    secret = "SUPER-SECRET-IDEMPOTENCY-KEY-42"
    store = InMemoryPerformanceReceiptStore()

    receipt = record(store, idempotency_key=secret)
    public_text = " ".join(
        (
            repr(store),
            repr(receipt),
            str(serialized(receipt)),
            receipt.model_dump_json(),
        )
    )

    assert secret not in public_text
    assert not hasattr(receipt, "idempotency_key")
    assert secret not in repr(store._receipts_by_key_digest)
    assert list(store._receipts_by_key_digest) == [
        performance_idempotency_key_digest(secret)
    ]


def test_conflict_error_never_echoes_the_raw_idempotency_key():
    secret = "CONFLICT-SECRET-KEY"
    store = InMemoryPerformanceReceiptStore()
    record(store, idempotency_key=secret)

    with pytest.raises(PerformanceIdempotencyConflict) as captured:
        record(
            store,
            idempotency_key=secret,
            records_sha256="1" * 64,
        )

    assert secret not in str(captured.value)
    assert secret not in repr(captured.value)


def test_get_receipt_validates_public_id_and_returns_the_original():
    store = InMemoryPerformanceReceiptStore()
    receipt = record(store)

    assert store.get_receipt(receipt.receipt_id) is receipt
    assert store.get_receipt("prc_" + "0" * 64) is None
    with pytest.raises(ValueError, match="receipt_id"):
        store.get_receipt("not-a-receipt")


def test_manual_receipt_requires_all_three_digests_to_match():
    store = InMemoryPerformanceReceiptStore()
    receipt = record(store)
    payload = receipt.model_dump(mode="python")
    payload["request_digest"] = "1" * 64
    payload["operation_id"] = performance_operation_id(
        idempotency_key_digest=receipt.idempotency_key_digest,
        request_digest="1" * 64,
    )
    payload["receipt_id"] = performance_receipt_id(
        **{
            key: value
            for key, value in payload.items()
            if key != "receipt_id"
        }
    )

    with pytest.raises(ValidationError, match="request_digest"):
        PerformanceExecutionReceipt.model_validate(payload)
