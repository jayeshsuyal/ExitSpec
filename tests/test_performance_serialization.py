from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from exitspec.canonical import canonical_json_bytes
from exitspec.confirmations import (
    ConfirmationDecision,
    ContractConfirmation,
    record_confirmation,
)
from exitspec.contracts import freeze_confirmed_contract
from exitspec.models import (
    InferencePerformanceCriterion,
    POCContract,
)
from exitspec.performance_probe import (
    PROBE_SCHEMA_VERSION,
    ProbeConfig,
    ProbeOutcome,
    ProbePhase,
    ProbeRecord,
    ProbeRun,
    SyntheticPrompt,
    build_manifest,
    records_jsonl,
)
from exitspec.performance_receipts import (
    InMemoryPerformanceReceiptStore,
    PerformanceExecutionReceipt,
)
from exitspec.performance_serialization import (
    PerformanceSerializationError,
    PerformanceVerdictDisplay,
    parse_confirmation,
    parse_contract,
    parse_performance_receipt,
    parse_performance_verdict_display,
    parse_probe_manifest,
    parse_probe_run,
    recompute_and_compare_performance_verdict,
    serialize_confirmation,
    serialize_contract,
    serialize_performance_receipt,
    serialize_performance_verdict,
    serialize_probe_manifest,
    serialize_probe_records_jsonl,
    serialize_probe_run,
)
from exitspec.performance_verdicts import evaluate_performance_criterion
from exitspec.runner import load_contract


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = (
    PROJECT_ROOT / "examples/inference-performance/contracts/vllm-ttft-v1.yaml"
)
FIXED_TIME = datetime(2026, 7, 28, 12, 0, tzinfo=timezone.utc)
CONFIRMATION_KEY = "serialization-confirmation-v1"
EXECUTION_KEY = "serialization-execution-v1"
EXECUTION_ID = "run_" + "a" * 32


def _confirmation() -> ContractConfirmation:
    approved = load_contract(CONTRACT_PATH)
    return record_confirmation(
        approved,
        confirmer_identity="customer@example.com",
        decision=ConfirmationDecision.CONFIRM,
        agreement_acknowledged=True,
        rationale="The exact performance requirements are correct.",
        idempotency_key=CONFIRMATION_KEY,
        decided_at=FIXED_TIME,
    )


def _frozen_contract() -> POCContract:
    return freeze_confirmed_contract(
        load_contract(CONTRACT_PATH),
        _confirmation(),
        FIXED_TIME,
    )


def _criterion() -> InferencePerformanceCriterion:
    criterion = _frozen_contract().criteria[0]
    assert isinstance(criterion, InferencePerformanceCriterion)
    return criterion


def _probe_run(*, errors: int = 0) -> ProbeRun:
    manifest = build_manifest(
        ProbeConfig(
            endpoint="http://127.0.0.1:8000/v1/chat/completions",
            model="Qwen/Qwen2.5-0.5B-Instruct",
            request_count=100,
            concurrency=4,
            warmup_count=10,
            timeout_seconds=30,
            max_tokens=64,
        ),
        (
            SyntheticPrompt("synthetic-1", "Explain deterministic tests."),
            SyntheticPrompt("synthetic-2", "Name one latency metric."),
        ),
    )
    records: list[ProbeRecord] = []
    for phase, count in (
        (ProbePhase.WARMUP, manifest.warmup_count),
        (ProbePhase.MEASURED, manifest.request_count),
    ):
        for ordinal in range(1, count + 1):
            descriptor = manifest.prompts[(ordinal - 1) % len(manifest.prompts)]
            is_error = (
                phase is ProbePhase.MEASURED
                and ordinal > manifest.request_count - errors
            )
            records.append(
                ProbeRecord(
                    schema_version=PROBE_SCHEMA_VERSION,
                    execution_id=EXECUTION_ID,
                    manifest_sha256=manifest.manifest_sha256,
                    request_id=("warmup" if phase is ProbePhase.WARMUP else "measured")
                    + f"-{ordinal:05d}",
                    phase=phase,
                    ordinal=ordinal,
                    included_in_measurement=phase is ProbePhase.MEASURED,
                    prompt_id=descriptor.prompt_id,
                    prompt_sha256=descriptor.sha256,
                    outcome=(
                        ProbeOutcome.HTTP_ERROR if is_error else ProbeOutcome.SUCCESS
                    ),
                    http_status=429 if is_error else 200,
                    ttft_ns=None if is_error else 100_000_000,
                    duration_ns=110_000_000,
                )
            )
    ordered = tuple(records)
    records_bytes = records_jsonl(ordered).encode("utf-8")
    return ProbeRun(
        execution_id=EXECUTION_ID,
        manifest=manifest,
        records_sha256=hashlib.sha256(records_bytes).hexdigest(),
        records=ordered,
    )


def _receipt(run: ProbeRun) -> PerformanceExecutionReceipt:
    contract = _frozen_contract()
    assert contract.canonical_hash is not None
    return InMemoryPerformanceReceiptStore().record_receipt(
        idempotency_key=EXECUTION_KEY,
        contract_id=contract.id,
        contract_version=contract.version,
        frozen_contract_hash=contract.canonical_hash,
        criterion_id=_criterion().id,
        expected_manifest_sha256=run.manifest.manifest_sha256,
        execution_id=run.execution_id,
        records_sha256=run.records_sha256,
        created_at=FIXED_TIME,
    )


def _canonical_edit(data: bytes, **changes: object) -> bytes:
    payload = json.loads(data)
    payload.update(changes)
    return canonical_json_bytes(payload)


def test_contract_round_trip_is_canonical_and_preserves_digest():
    contract = _frozen_contract()
    serialized = serialize_contract(contract)

    assert serialized == canonical_json_bytes(contract.model_dump(mode="json"))
    assert parse_contract(serialized) == contract


def test_contract_parser_rejects_unknown_coerced_and_tampered_fields():
    serialized = serialize_contract(_frozen_contract())
    payload = json.loads(serialized)

    with pytest.raises(PerformanceSerializationError):
        parse_contract(_canonical_edit(serialized, surprise=True))
    payload["criteria"][0]["ttft_p95"]["threshold"] = "500"
    with pytest.raises(PerformanceSerializationError):
        parse_contract(canonical_json_bytes(payload))
    with pytest.raises(PerformanceSerializationError, match="digest"):
        parse_contract(_canonical_edit(serialized, canonical_hash="0" * 64))


def test_confirmation_round_trip_never_persists_raw_idempotency_key():
    confirmation = _confirmation()
    serialized = serialize_confirmation(confirmation)

    assert CONFIRMATION_KEY.encode() not in serialized
    assert b"idempotency_key" not in serialized
    assert (
        parse_confirmation(
            serialized,
            idempotency_key=CONFIRMATION_KEY,
        )
        == confirmation
    )


def test_confirmation_rejects_wrong_key_duplicate_and_unknown_field():
    serialized = serialize_confirmation(_confirmation())

    with pytest.raises(PerformanceSerializationError, match="supplied"):
        parse_confirmation(serialized, idempotency_key="wrong-key")
    duplicate = serialized[:-1] + b',"rationale":"duplicate"}'
    with pytest.raises(PerformanceSerializationError, match="duplicate"):
        parse_confirmation(duplicate, idempotency_key=CONFIRMATION_KEY)
    with pytest.raises(PerformanceSerializationError, match="fields"):
        parse_confirmation(
            _canonical_edit(serialized, raw_api_key="secret"),
            idempotency_key=CONFIRMATION_KEY,
        )


def test_probe_manifest_round_trip_contains_hashes_but_no_prompt_content():
    run = _probe_run()
    serialized = serialize_probe_manifest(run.manifest)

    assert b"Explain deterministic tests" not in serialized
    assert parse_probe_manifest(serialized) == run.manifest


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("request_count", "100"),
        ("concurrency", True),
        ("warmup_included_in_measurement", 0),
        ("schema_version", "unsupported"),
    ],
)
def test_probe_manifest_rejects_coercions_and_malformed_values(field, value):
    serialized = serialize_probe_manifest(_probe_run().manifest)

    with pytest.raises(PerformanceSerializationError):
        parse_probe_manifest(_canonical_edit(serialized, **{field: value}))


def test_probe_manifest_rejects_unknown_duplicate_nonfinite_and_hash_tamper():
    serialized = serialize_probe_manifest(_probe_run().manifest)

    with pytest.raises(PerformanceSerializationError, match="fields"):
        parse_probe_manifest(_canonical_edit(serialized, unknown=1))
    duplicate = serialized[:-1] + b',"model":"duplicate"}'
    with pytest.raises(PerformanceSerializationError, match="duplicate"):
        parse_probe_manifest(duplicate)
    nonfinite = serialized.replace(b'"timeout_seconds":30', b'"timeout_seconds":NaN')
    with pytest.raises(PerformanceSerializationError, match="non-finite"):
        parse_probe_manifest(nonfinite)
    with pytest.raises(PerformanceSerializationError, match="identity hash"):
        parse_probe_manifest(_canonical_edit(serialized, manifest_sha256="0" * 64))


def test_probe_manifest_rejects_rehashed_duplicate_prompt_identity():
    serialized = serialize_probe_manifest(_probe_run().manifest)
    payload = json.loads(serialized)
    payload["prompts"][1]["prompt_id"] = payload["prompts"][0]["prompt_id"]
    payload["prompt_set_sha256"] = hashlib.sha256(
        canonical_json_bytes(payload["prompts"])
    ).hexdigest()
    identity = dict(payload)
    identity.pop("manifest_sha256")
    payload["manifest_sha256"] = hashlib.sha256(
        canonical_json_bytes(identity)
    ).hexdigest()

    with pytest.raises(PerformanceSerializationError, match="duplicated"):
        parse_probe_manifest(canonical_json_bytes(payload))


def test_probe_run_round_trip_calls_whole_run_validation():
    run = _probe_run()
    manifest_bytes, record_bytes = serialize_probe_run(run)

    parsed = parse_probe_run(
        manifest_bytes,
        record_bytes,
        expected_execution_id=run.execution_id,
        expected_records_sha256=run.records_sha256,
    )

    assert parsed == run
    assert serialize_probe_records_jsonl(parsed) == record_bytes


def test_probe_run_rejects_record_hash_execution_and_manifest_mismatch():
    run = _probe_run()
    manifest_bytes, record_bytes = serialize_probe_run(run)

    with pytest.raises(PerformanceSerializationError, match="artifact hash"):
        parse_probe_run(
            manifest_bytes,
            record_bytes,
            expected_execution_id=run.execution_id,
            expected_records_sha256="0" * 64,
        )
    with pytest.raises(PerformanceSerializationError, match="whole-run"):
        parse_probe_run(
            manifest_bytes,
            record_bytes,
            expected_execution_id="run_" + "b" * 32,
            expected_records_sha256=run.records_sha256,
        )
    first, *rest = record_bytes.splitlines()
    first_payload = json.loads(first)
    first_payload["manifest_sha256"] = "1" * 64
    tampered = b"\n".join([canonical_json_bytes(first_payload), *rest])
    tampered_hash = hashlib.sha256(tampered).hexdigest()
    with pytest.raises(PerformanceSerializationError, match="whole-run"):
        parse_probe_run(
            manifest_bytes,
            tampered,
            expected_execution_id=run.execution_id,
            expected_records_sha256=tampered_hash,
        )


def test_probe_records_reject_unknown_duplicate_enum_coercion_and_order():
    run = _probe_run()
    manifest_bytes, record_bytes = serialize_probe_run(run)
    first, *rest = record_bytes.splitlines()
    payload = json.loads(first)

    payload["unknown"] = True
    unknown = b"\n".join([canonical_json_bytes(payload), *rest])
    with pytest.raises(PerformanceSerializationError, match="fields"):
        parse_probe_run(
            manifest_bytes,
            unknown,
            expected_execution_id=run.execution_id,
            expected_records_sha256=hashlib.sha256(unknown).hexdigest(),
        )

    duplicate_first = first[:-1] + b',"ordinal":1}'
    duplicate = b"\n".join([duplicate_first, *rest])
    with pytest.raises(PerformanceSerializationError, match="duplicate"):
        parse_probe_run(
            manifest_bytes,
            duplicate,
            expected_execution_id=run.execution_id,
            expected_records_sha256=hashlib.sha256(duplicate).hexdigest(),
        )

    payload = json.loads(first)
    payload["outcome"] = "success"
    malformed_enum = b"\n".join([canonical_json_bytes(payload), *rest])
    with pytest.raises(PerformanceSerializationError, match="enum"):
        parse_probe_run(
            manifest_bytes,
            malformed_enum,
            expected_execution_id=run.execution_id,
            expected_records_sha256=hashlib.sha256(malformed_enum).hexdigest(),
        )

    payload = json.loads(first)
    payload["ordinal"] = "1"
    coerced = b"\n".join([canonical_json_bytes(payload), *rest])
    with pytest.raises(PerformanceSerializationError, match="integer"):
        parse_probe_run(
            manifest_bytes,
            coerced,
            expected_execution_id=run.execution_id,
            expected_records_sha256=hashlib.sha256(coerced).hexdigest(),
        )

    reversed_lines = b"\n".join(reversed(record_bytes.splitlines()))
    with pytest.raises(PerformanceSerializationError, match="order"):
        parse_probe_run(
            manifest_bytes,
            reversed_lines,
            expected_execution_id=run.execution_id,
            expected_records_sha256=hashlib.sha256(reversed_lines).hexdigest(),
        )


def test_performance_receipt_round_trip_has_no_raw_key_and_revalidates_identity():
    run = _probe_run()
    receipt = _receipt(run)
    serialized = serialize_performance_receipt(receipt)

    assert EXECUTION_KEY.encode() not in serialized
    assert b'"idempotency_key":' not in serialized
    assert parse_performance_receipt(serialized) == receipt


def test_performance_receipt_rejects_unknown_coercion_and_tampering():
    serialized = serialize_performance_receipt(_receipt(_probe_run()))

    with pytest.raises(PerformanceSerializationError):
        parse_performance_receipt(_canonical_edit(serialized, unknown=True))
    with pytest.raises(PerformanceSerializationError):
        parse_performance_receipt(_canonical_edit(serialized, created_at=123))
    with pytest.raises(PerformanceSerializationError, match="integrity"):
        parse_performance_receipt(_canonical_edit(serialized, records_sha256="f" * 64))


def test_verdict_projection_round_trip_is_display_only_until_recomputed():
    run = _probe_run()
    criterion = _criterion()
    verdict = evaluate_performance_criterion(criterion, run)
    serialized = serialize_performance_verdict(verdict)

    display = parse_performance_verdict_display(serialized)

    assert type(display) is PerformanceVerdictDisplay
    assert not isinstance(display, type(verdict))
    assert (
        recompute_and_compare_performance_verdict(
            display,
            criterion,
            run,
        )
        == verdict
    )


def test_forged_display_verdict_cannot_authorize_itself():
    run = _probe_run(errors=1)
    criterion = _criterion()
    verdict = evaluate_performance_criterion(criterion, run)
    serialized = serialize_performance_verdict(verdict)
    payload = json.loads(serialized)
    payload["verdict"] = "PASS"
    payload["error_rate"]["verdict"] = "PASS"
    forged = parse_performance_verdict_display(canonical_json_bytes(payload))

    with pytest.raises(PerformanceSerializationError, match="recomputation"):
        recompute_and_compare_performance_verdict(
            forged,
            criterion,
            run,
        )


def test_verdict_parser_rejects_duplicate_unknown_coercion_and_bad_enums():
    verdict = evaluate_performance_criterion(_criterion(), _probe_run())
    serialized = serialize_performance_verdict(verdict)

    duplicate = serialized[:-1] + b',"verdict":"PASS"}'
    with pytest.raises(PerformanceSerializationError, match="duplicate"):
        parse_performance_verdict_display(duplicate)
    with pytest.raises(PerformanceSerializationError, match="fields"):
        parse_performance_verdict_display(_canonical_edit(serialized, unknown=True))
    with pytest.raises(PerformanceSerializationError, match="integer"):
        parse_performance_verdict_display(
            _canonical_edit(serialized, attempted_count="100")
        )
    with pytest.raises(PerformanceSerializationError, match="enum"):
        parse_performance_verdict_display(_canonical_edit(serialized, verdict="pass"))


def test_every_serializer_rejects_wrong_domain_type():
    run = _probe_run()
    with pytest.raises(TypeError):
        serialize_contract(object())  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        serialize_confirmation(object())  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        serialize_probe_manifest(object())  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        serialize_probe_records_jsonl(object())  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        serialize_performance_receipt(object())  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        serialize_performance_verdict(object())  # type: ignore[arg-type]
    with pytest.raises(PerformanceSerializationError):
        parse_probe_run(
            serialize_probe_manifest(run.manifest),
            serialize_probe_records_jsonl(run),
            expected_execution_id=run.execution_id,
            expected_records_sha256=123,  # type: ignore[arg-type]
        )
