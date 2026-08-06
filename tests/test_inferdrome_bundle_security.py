from __future__ import annotations

import ast
import hashlib
import json
import os
from pathlib import Path

import pytest

import exitspec.inferdrome_bundle as bundle_module
import exitspec.inferdrome_import as import_module
from exitspec.canonical import canonical_json_bytes
from exitspec.inferdrome_bundle import (
    InferdromeBundleErrorCode,
    InferdromeBundleLimits,
    InferdromeBundleRejected,
    verify_inferdrome_bundle,
)
from exitspec.inferdrome_import import import_inferdrome_bundle
from tests.inferdrome_helpers import (
    FIXED_TIME,
    bind_customer_bundle,
    build_context,
    mutable_bundle_copy,
    rehash_manifest,
)


def _customer_bundle(tmp_path: Path) -> tuple[Path, object, object]:
    context, confirmation = build_context(tmp_path)
    bundle = mutable_bundle_copy(tmp_path)
    assert context.contract.canonical_hash is not None
    bind_customer_bundle(bundle, context.contract.canonical_hash)
    return bundle, context, confirmation


def test_importer_has_no_runtime_dependency_on_inferdrome_package():
    for module in (bundle_module, import_module):
        source_path = Path(module.__file__)
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        imported_modules = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imported_modules.update(
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        )
        assert not any(
            name == "inferdrome" or name.startswith("inferdrome.")
            for name in imported_modules
        )


@pytest.mark.parametrize("template", ["fake-template", "vllm-template"])
def test_synthetic_or_ineligible_evidence_is_rejected(template, tmp_path):
    bundle = mutable_bundle_copy(tmp_path, template)

    with pytest.raises(InferdromeBundleRejected) as caught:
        verify_inferdrome_bundle(bundle)

    assert caught.value.code is InferdromeBundleErrorCode.EVIDENCE_INELIGIBLE


def test_changed_artifact_bytes_fail_exact_hash_verification(tmp_path):
    bundle, context, confirmation = _customer_bundle(tmp_path)
    records_path = bundle / "records" / "requests.jsonl"
    records_path.write_bytes(records_path.read_bytes() + b" ")

    with pytest.raises(InferdromeBundleRejected) as caught:
        import_inferdrome_bundle(
            bundle,
            context,
            confirmation,
            received_at=FIXED_TIME,
        )

    assert caught.value.code is InferdromeBundleErrorCode.INTEGRITY_MISMATCH


def test_coherently_rehashed_summary_cannot_override_raw_records(tmp_path):
    bundle, context, confirmation = _customer_bundle(tmp_path)
    measurements_path = bundle / "derived" / "measurements.json"
    measurements = json.loads(measurements_path.read_bytes())
    error_rate = next(
        item for item in measurements["measurements"] if item["metric"] == "error_rate"
    )
    error_rate["value"] = "0.000000"
    measurements_path.write_bytes(canonical_json_bytes(measurements))
    rehash_manifest(bundle, {"derived/measurements.json"})

    with pytest.raises(InferdromeBundleRejected) as caught:
        import_inferdrome_bundle(
            bundle,
            context,
            confirmation,
            received_at=FIXED_TIME,
        )

    assert caught.value.code is InferdromeBundleErrorCode.INTERNAL_INCONSISTENCY
    assert "independent recalculation" in str(caught.value)


def test_duplicate_request_ids_fail_after_coherent_rehash(tmp_path):
    bundle, context, confirmation = _customer_bundle(tmp_path)
    records_path = bundle / "records" / "requests.jsonl"
    records = [json.loads(line) for line in records_path.read_bytes().splitlines()]
    records[1]["request_id"] = records[0]["request_id"]
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

    with pytest.raises(InferdromeBundleRejected) as caught:
        import_inferdrome_bundle(
            bundle,
            context,
            confirmation,
            received_at=FIXED_TIME,
        )

    assert caught.value.code is InferdromeBundleErrorCode.INTERNAL_INCONSISTENCY


def test_timestamp_inversion_fails_after_coherent_rehash(tmp_path):
    bundle, context, confirmation = _customer_bundle(tmp_path)
    execution_path = bundle / "execution.json"
    execution = json.loads(execution_path.read_bytes())
    execution["ended_at"] = "2026-08-05T00:00:00Z"
    execution_path.write_bytes(canonical_json_bytes(execution))
    rehash_manifest(bundle, {"execution.json"})

    with pytest.raises(InferdromeBundleRejected) as caught:
        import_inferdrome_bundle(
            bundle,
            context,
            confirmation,
            received_at=FIXED_TIME,
        )

    assert caught.value.code is InferdromeBundleErrorCode.INTERNAL_INCONSISTENCY


@pytest.mark.parametrize("node_kind", ["symlink", "hardlink"])
def test_symlink_and_hardlink_nodes_are_rejected_before_content_use(
    node_kind,
    tmp_path,
):
    bundle = mutable_bundle_copy(tmp_path)
    stdout = bundle / "native" / "stdout.log"
    stdout.unlink()
    version = bundle / "native" / "producer-version.txt"
    if node_kind == "symlink":
        stdout.symlink_to(version.name)
    else:
        os.link(version, stdout)

    with pytest.raises(InferdromeBundleRejected) as caught:
        verify_inferdrome_bundle(bundle, require_customer_eligible=False)

    assert caught.value.code is InferdromeBundleErrorCode.UNSAFE_BUNDLE


def test_undeclared_file_and_tight_limits_fail_closed(tmp_path):
    undeclared = mutable_bundle_copy(tmp_path)
    (undeclared / "extra.txt").write_text("undeclared", encoding="utf-8")
    with pytest.raises(InferdromeBundleRejected) as caught:
        verify_inferdrome_bundle(undeclared, require_customer_eligible=False)
    assert caught.value.code is InferdromeBundleErrorCode.INTEGRITY_MISMATCH

    limited = mutable_bundle_copy(tmp_path / "limited")
    with pytest.raises(InferdromeBundleRejected) as caught:
        verify_inferdrome_bundle(
            limited,
            require_customer_eligible=False,
            limits=InferdromeBundleLimits(max_files=1),
        )
    assert caught.value.code is InferdromeBundleErrorCode.UNSAFE_BUNDLE

    record_limited = mutable_bundle_copy(tmp_path / "record-limited")
    with pytest.raises(InferdromeBundleRejected) as caught:
        verify_inferdrome_bundle(
            record_limited,
            require_customer_eligible=False,
            limits=InferdromeBundleLimits(max_request_records=3),
        )
    assert caught.value.code is InferdromeBundleErrorCode.UNSAFE_BUNDLE


def test_coherently_rehashed_artifact_classification_is_still_checked(tmp_path):
    bundle = mutable_bundle_copy(tmp_path)
    descriptor_path = bundle / "bundle.json"
    descriptor = json.loads(descriptor_path.read_bytes())
    descriptor["artifacts"][0]["media_type"] = "text/plain"
    descriptor_path.write_bytes(canonical_json_bytes(descriptor))
    rehash_manifest(bundle, {"bundle.json"})

    with pytest.raises(InferdromeBundleRejected) as caught:
        verify_inferdrome_bundle(bundle, require_customer_eligible=False)

    assert caught.value.code is InferdromeBundleErrorCode.SCHEMA_INVALID


def test_coherently_rehashed_invocation_with_extra_option_is_rejected(tmp_path):
    bundle, context, confirmation = _customer_bundle(tmp_path)
    invocation_path = bundle / "native" / "invocation.json"
    invocation = json.loads(invocation_path.read_bytes())
    metadata_index = invocation["argv"].index("--metadata")
    invocation["argv"].insert(metadata_index, "--ignore-eos")
    invocation_path.write_bytes(canonical_json_bytes(invocation))
    rehash_manifest(bundle, {"native/invocation.json"})

    with pytest.raises(InferdromeBundleRejected) as caught:
        import_inferdrome_bundle(
            bundle,
            context,
            confirmation,
            received_at=FIXED_TIME,
        )

    assert caught.value.code is InferdromeBundleErrorCode.INTERNAL_INCONSISTENCY


def test_coherently_rehashed_environment_target_mismatch_is_rejected(tmp_path):
    bundle, context, confirmation = _customer_bundle(tmp_path)
    environment_path = bundle / "environment.json"
    environment = json.loads(environment_path.read_bytes())
    model_revision = next(
        field
        for field in environment["fields"]
        if field["name"] == "target.model_revision"
    )
    model_revision["value"] = "different-model-revision"
    environment_path.write_bytes(canonical_json_bytes(environment))
    rehash_manifest(bundle, {"environment.json"})

    with pytest.raises(InferdromeBundleRejected) as caught:
        import_inferdrome_bundle(
            bundle,
            context,
            confirmation,
            received_at=FIXED_TIME,
        )

    assert caught.value.code is InferdromeBundleErrorCode.INTERNAL_INCONSISTENCY


def test_duplicate_json_keys_and_unsupported_versions_are_distinct_rejections(
    tmp_path,
):
    duplicate = mutable_bundle_copy(tmp_path / "duplicate")
    descriptor_path = duplicate / "bundle.json"
    original = descriptor_path.read_bytes()
    descriptor_path.write_bytes(
        b'{"schema_version":"inferdrome.evidence.v1",' + original[1:]
    )
    rehash_manifest(duplicate, {"bundle.json"})
    with pytest.raises(InferdromeBundleRejected) as caught:
        verify_inferdrome_bundle(duplicate, require_customer_eligible=False)
    assert caught.value.code is InferdromeBundleErrorCode.SCHEMA_INVALID

    unsupported = mutable_bundle_copy(tmp_path / "unsupported")
    unsupported_path = unsupported / "bundle.json"
    descriptor = json.loads(unsupported_path.read_bytes())
    descriptor["schema_version"] = "inferdrome.evidence.v2"
    unsupported_path.write_bytes(canonical_json_bytes(descriptor))
    rehash_manifest(unsupported, {"bundle.json"})
    with pytest.raises(InferdromeBundleRejected) as caught:
        verify_inferdrome_bundle(unsupported, require_customer_eligible=False)
    assert caught.value.code is InferdromeBundleErrorCode.UNSUPPORTED_SCHEMA


def test_retained_bundle_digest_detects_a_coherently_rehashed_diagnostic(tmp_path):
    bundle, _context, _confirmation = _customer_bundle(tmp_path)
    original = verify_inferdrome_bundle(bundle).bundle_digest
    stdout = bundle / "native" / "stdout.log"
    stdout.write_bytes(b"different valid diagnostic\n")
    rehash_manifest(bundle, {"native/stdout.log"})

    with pytest.raises(InferdromeBundleRejected) as caught:
        verify_inferdrome_bundle(bundle, expected_bundle_digest=original)

    assert caught.value.code is InferdromeBundleErrorCode.INTEGRITY_MISMATCH


def test_same_size_rewrite_during_capture_is_detected(tmp_path, monkeypatch):
    bundle = mutable_bundle_copy(tmp_path)
    target = bundle / "bundle.json"
    target_inode = target.stat().st_ino
    original_read = bundle_module.os.read
    changed = False

    def rewriting_read(descriptor: int, size: int) -> bytes:
        nonlocal changed
        content = original_read(descriptor, size)
        if content and not changed and os.fstat(descriptor).st_ino == target_inode:
            changed = True
            replacement = target.read_bytes().replace(b'"VALID"', b'"INVAL"', 1)
            assert len(replacement) == target.stat().st_size
            target.write_bytes(replacement)
        return content

    monkeypatch.setattr(bundle_module.os, "read", rewriting_read)

    with pytest.raises(InferdromeBundleRejected) as caught:
        verify_inferdrome_bundle(bundle, require_customer_eligible=False)

    assert caught.value.code is InferdromeBundleErrorCode.UNSAFE_BUNDLE
