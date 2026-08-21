from __future__ import annotations

import hashlib
import json
from datetime import timedelta

import pytest
from pydantic import ValidationError

from exitspec.canonical import canonical_json_bytes
from exitspec.inferdrome_import import (
    INFERDROME_CALCULATION_VERSION,
    InferdromeApplicabilityCode,
    InferdromeImportErrorCode,
    InferdromeImportRejected,
    import_inferdrome_bundle,
    validate_inferdrome_receipt,
)
from exitspec.models import VerdictStatus
from tests.inferdrome_helpers import (
    FIXED_TIME,
    bind_customer_bundle,
    build_context,
    mutable_bundle_copy,
    rehash_manifest,
)


def test_valid_bundle_is_accepted_but_ttft_semantics_are_not_overclaimed(
    tmp_path,
):
    context, confirmation = build_context(tmp_path)
    bundle = mutable_bundle_copy(tmp_path)
    assert context.contract.canonical_hash is not None
    bind_customer_bundle(bundle, context.contract.canonical_hash)

    result = import_inferdrome_bundle(
        bundle,
        context,
        confirmation,
        received_at=FIXED_TIME,
    )

    assert result.performance_verdict.verdict is VerdictStatus.NOT_PROVEN
    assert result.performance_verdict.error_rate.verdict is VerdictStatus.PASS
    assert result.performance_verdict.ttft_p95.verdict is VerdictStatus.NOT_PROVEN
    assert result.applicability.issues == (
        InferdromeApplicabilityCode.TTFT_DEFINITION_MISMATCH,
    )
    assert result.recalculated.attempted_count == 4
    assert result.recalculated.successful_count == 3
    assert result.recalculated.failed_count == 1
    assert str(result.recalculated.error_rate) == "0.25"
    assert result.recalculated.p95_ttft_ns == 14_906_291
    assert result.receipt.import_status == "ACCEPTED"
    assert result.receipt.acceptance_verdict == "NOT_PROVEN"
    assert result.receipt.calculation_version == INFERDROME_CALCULATION_VERSION
    assert result.receipt.bundle_digest.startswith("sha256:")
    assert result.receipt.contract_hash == context.contract.canonical_hash
    assert validate_inferdrome_receipt(result.receipt) == result.receipt


def test_known_reliability_violation_fails_despite_unproven_ttft(tmp_path):
    context, confirmation = build_context(tmp_path, error_threshold=0.2)
    bundle = mutable_bundle_copy(tmp_path)
    assert context.contract.canonical_hash is not None
    bind_customer_bundle(bundle, context.contract.canonical_hash)

    result = import_inferdrome_bundle(
        bundle,
        context,
        confirmation,
        received_at=FIXED_TIME,
    )

    assert result.performance_verdict.error_rate.verdict is VerdictStatus.FAIL
    assert result.performance_verdict.ttft_p95.verdict is VerdictStatus.NOT_PROVEN
    assert result.performance_verdict.verdict is VerdictStatus.FAIL
    assert result.receipt.acceptance_verdict == "FAIL"


def test_anomalous_empty_stream_is_counted_as_a_known_reliability_failure(
    tmp_path,
):
    context, confirmation = build_context(tmp_path, error_threshold=0.2)
    bundle = mutable_bundle_copy(tmp_path)
    assert context.contract.canonical_hash is not None
    bind_customer_bundle(bundle, context.contract.canonical_hash)
    records_path = bundle / "records" / "requests.jsonl"
    records = [json.loads(line) for line in records_path.read_bytes().splitlines()]
    records[2]["outcome"] = {
        "producer_error": None,
        "status": "ANOMALOUS_EMPTY_STREAM",
    }
    records_bytes = b"".join(canonical_json_bytes(record) + b"\n" for record in records)
    records_path.write_bytes(records_bytes)
    measurements_path = bundle / "derived" / "measurements.json"
    measurements = json.loads(measurements_path.read_bytes())
    measurements["request_records_sha256"] = (
        f"sha256:{hashlib.sha256(records_bytes).hexdigest()}"
    )
    measurements_path.write_bytes(canonical_json_bytes(measurements))
    rehash_manifest(
        bundle,
        {"records/requests.jsonl", "derived/measurements.json"},
    )

    result = import_inferdrome_bundle(
        bundle,
        context,
        confirmation,
        received_at=FIXED_TIME,
    )

    assert result.recalculated.anomalous_count == 1
    assert InferdromeApplicabilityCode.ANOMALOUS_RECORD in (result.applicability.issues)
    assert result.performance_verdict.error_rate.verdict is VerdictStatus.FAIL
    assert result.performance_verdict.verdict is VerdictStatus.FAIL


def test_workload_or_target_mismatch_is_valid_but_not_proven(tmp_path):
    context, confirmation = build_context(
        tmp_path,
        prompts=("different prompt",),
        model="different/model",
    )
    bundle = mutable_bundle_copy(tmp_path)
    assert context.contract.canonical_hash is not None
    bind_customer_bundle(bundle, context.contract.canonical_hash)

    result = import_inferdrome_bundle(
        bundle,
        context,
        confirmation,
        received_at=FIXED_TIME,
    )

    assert result.performance_verdict.verdict is VerdictStatus.NOT_PROVEN
    assert InferdromeApplicabilityCode.TARGET_MODEL_MISMATCH in (
        result.applicability.issues
    )
    assert InferdromeApplicabilityCode.WORKLOAD_PROMPT_MISMATCH in (
        result.applicability.issues
    )


def test_valid_partial_environment_is_accepted_as_not_proven(tmp_path):
    context, confirmation = build_context(tmp_path)
    bundle = mutable_bundle_copy(tmp_path)
    assert context.contract.canonical_hash is not None
    bind_customer_bundle(bundle, context.contract.canonical_hash)
    environment_path = bundle / "environment.json"
    descriptor_path = bundle / "bundle.json"
    environment = json.loads(environment_path.read_bytes())
    descriptor = json.loads(descriptor_path.read_bytes())
    model_revision = next(
        field
        for field in environment["fields"]
        if field["name"] == "target.model_revision"
    )
    model_revision.update(
        {"value": None, "provenance": "UNKNOWN", "evidence_path": None}
    )
    environment["completeness"] = "PARTIAL"
    descriptor["environment_completeness"] = "PARTIAL"
    environment_path.write_bytes(canonical_json_bytes(environment))
    descriptor_path.write_bytes(canonical_json_bytes(descriptor))
    rehash_manifest(bundle, {"environment.json", "bundle.json"})

    result = import_inferdrome_bundle(
        bundle,
        context,
        confirmation,
        received_at=FIXED_TIME,
    )

    assert result.performance_verdict.verdict is VerdictStatus.NOT_PROVEN
    assert InferdromeApplicabilityCode.ENVIRONMENT_INCOMPLETE in (
        result.applicability.issues
    )
    assert result.receipt.import_status == "ACCEPTED"


def test_receipt_is_deterministic_and_every_material_field_is_bound(tmp_path):
    context, confirmation = build_context(tmp_path)
    bundle = mutable_bundle_copy(tmp_path)
    assert context.contract.canonical_hash is not None
    bind_customer_bundle(bundle, context.contract.canonical_hash)

    first = import_inferdrome_bundle(
        bundle,
        context,
        confirmation,
        received_at=FIXED_TIME,
    )
    replay = import_inferdrome_bundle(
        bundle,
        context,
        confirmation,
        received_at=FIXED_TIME,
    )

    assert replay.receipt == first.receipt
    assert replay.receipt.receipt_id == first.receipt.receipt_id
    assert first.receipt.receipt_id == (
        "irc_b5b1e4513c16382288faebbc7f2bc37375c825f86a2c84930bf94d9d0b4b0b94"
    )
    receipt_bytes = canonical_json_bytes(first.receipt.model_dump(mode="json"))
    assert hashlib.sha256(receipt_bytes).hexdigest() == (
        "30c8ac80da3937731e98800dc2fe7788b8e6ba35eebf95222ba3a7df88d97df2"
    )
    with pytest.raises(ValidationError, match="receipt_id"):
        validate_inferdrome_receipt(
            first.receipt.model_copy(
                update={"received_at": FIXED_TIME + timedelta(seconds=1)}
            )
        )
    with pytest.raises(ValidationError, match="applicability_codes"):
        validate_inferdrome_receipt(
            first.receipt.model_copy(
                update={
                    "applicability_codes": (
                        *first.receipt.applicability_codes,
                        *first.receipt.applicability_codes,
                    )
                }
            )
        )


def test_import_requires_a_frozen_customer_confirmed_context(tmp_path):
    context, confirmation = build_context(tmp_path)
    bundle = mutable_bundle_copy(tmp_path)
    assert context.contract.canonical_hash is not None
    bind_customer_bundle(bundle, context.contract.canonical_hash)
    wrong_confirmation = confirmation.model_copy(
        update={"confirmation_id": "cnf_" + "0" * 64}
    )

    with pytest.raises(InferdromeImportRejected) as caught:
        import_inferdrome_bundle(
            bundle,
            context,
            wrong_confirmation,
            received_at=FIXED_TIME,
        )

    assert caught.value.code is InferdromeImportErrorCode.CONTEXT_NOT_AUTHORIZED
