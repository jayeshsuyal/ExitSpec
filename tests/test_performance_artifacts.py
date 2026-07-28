from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from exitspec.canonical import canonical_json_bytes
from exitspec.performance_artifacts import (
    HASHES_PATH,
    REGISTRY_PATH,
    PerformanceArtifactConflictError,
    PerformanceArtifactInputs,
    PerformanceArtifactIntegrityError,
    PerformanceArtifactValidationError,
    REDACTION_REDACTED,
    persist_performance_artifacts,
    read_and_verify_performance_artifacts,
)


RUN_ID = "run_latency_001"


def json_bytes(**values: object) -> bytes:
    return json.dumps(values, indent=2, sort_keys=False).encode("utf-8")


def inputs(
    **changes: object,
) -> PerformanceArtifactInputs:
    values: dict[str, object] = {
        "contract_json": json_bytes(
            version="1.0.0",
            id="performance-demo",
        ),
        "confirmation_json": json_bytes(
            confirmation_id="cnf_demo",
            affirmed=True,
        ),
        "workload_json": json_bytes(
            request_count=100,
            concurrency=4,
        ),
        "prompt_fixture_jsonl": (
            b'{ "text": "alpha", "prompt_id": "p1" }\n'
            b'{"prompt_id":"p2","text":"beta"}'
        ),
        "preflight_json": json_bytes(
            schema_version="exitspec.performance-preflight.v1",
            outcome="SUCCESS",
        ),
        "probe_manifest_json": json_bytes(
            schema_version="probe.v1",
            manifest_sha256="a" * 64,
        ),
        "records_jsonl": (
            b'{"request_id":"measured-00002","outcome":"SUCCESS"}\n'
            b'{"outcome":"SUCCESS","request_id":"measured-00001"}'
        ),
        "receipt_json": json_bytes(
            receipt_id="prc_" + "b" * 64,
        ),
        "calculations_json": json_bytes(
            p95_ttft_ms=412,
            error_rate=0,
        ),
        "verdicts_json": json_bytes(
            verdict="PASS",
        ),
        "decision_packet_html": (
            b"<!doctype html><html><body><h1>PASS</h1></body></html>"
        ),
        "redaction_states": {
            "prompt-fixture.jsonl": REDACTION_REDACTED,
            "evidence/probe-records.jsonl": REDACTION_REDACTED,
        },
    }
    values.update(changes)
    return PerformanceArtifactInputs(**values)


def persist(tmp_path: Path):
    return persist_performance_artifacts(tmp_path, RUN_ID, inputs())


def test_atomic_persistence_creates_only_fixed_layout_and_returns_exact_bytes(
    tmp_path: Path,
):
    verified = persist(tmp_path)
    relative_files = {
        path.relative_to(verified.run_dir).as_posix()
        for path in verified.run_dir.rglob("*")
        if path.is_file()
    }

    assert relative_files == {
        "contract.json",
        "confirmation.json",
        "workload.json",
        "prompt-fixture.jsonl",
        "evidence/preflight.json",
        "evidence/probe-manifest.json",
        "evidence/probe-records.jsonl",
        "receipt.json",
        "calculations.json",
        "verdicts.json",
        "decision-packet.html",
        REGISTRY_PATH,
        HASHES_PATH,
    }
    assert verified.run_id == RUN_ID
    assert verified.contract_json == canonical_json_bytes(
        {"id": "performance-demo", "version": "1.0.0"}
    )
    assert verified.workload_json == inputs().workload_json
    assert verified.prompt_fixture_jsonl == inputs().prompt_fixture_jsonl
    assert verified.records_jsonl == inputs().records_jsonl
    assert verified.bytes_for("verdicts.json") == b'{"verdict":"PASS"}'
    assert verified.decision_packet_html.startswith(b"<!doctype html>")
    with pytest.raises(TypeError):
        verified.files["verdicts.json"] = b"tampered"


def test_registry_and_hash_inventory_are_strict_and_recomputed(tmp_path: Path):
    verified = persist(tmp_path)
    registry = json.loads(verified.registry_json)
    inventory = json.loads(verified.artifact_hashes_json)

    assert registry["run_id"] == RUN_ID
    assert len(registry["artifacts"]) == 11
    assert len(inventory["artifacts"]) == 12
    assert inventory["algorithm"] == "sha256"
    registry_by_path = {
        entry["path"]: entry for entry in registry["artifacts"]
    }
    hashes_by_path = {
        entry["path"]: entry for entry in inventory["artifacts"]
    }
    assert set(registry_by_path) == set(verified.files) - {
        REGISTRY_PATH,
        HASHES_PATH,
    }
    assert hashes_by_path["prompt-fixture.jsonl"]["redaction_state"] == (
        REDACTION_REDACTED
    )

    for path, entry in hashes_by_path.items():
        exact = verified.files[path]
        assert entry["size_bytes"] == len(exact)
        assert entry["sha256"] == hashlib.sha256(exact).hexdigest()
    assert verified.registry_json == canonical_json_bytes(registry)
    assert verified.artifact_hashes_json == canonical_json_bytes(inventory)


@pytest.mark.parametrize(
    "run_id",
    [
        "",
        ".",
        "..",
        "../escape",
        "nested/run",
        r"nested\run",
        ".hidden",
        "has space",
        "a" * 129,
    ],
)
def test_unsafe_run_ids_are_rejected_before_writing(
    tmp_path: Path,
    run_id: str,
):
    with pytest.raises(
        PerformanceArtifactValidationError,
        match="Run ID",
    ):
        persist_performance_artifacts(tmp_path, run_id, inputs())
    assert list(tmp_path.iterdir()) == []


def test_existing_run_target_is_never_overwritten(tmp_path: Path):
    first = persist(tmp_path)
    original = first.verdicts_json

    with pytest.raises(
        PerformanceArtifactConflictError,
        match="already exists",
    ):
        persist_performance_artifacts(
            tmp_path,
            RUN_ID,
            inputs(verdicts_json=b'{"verdict":"FAIL"}'),
        )

    assert (first.run_dir / "verdicts.json").read_bytes() == original


def test_duplicate_json_fields_are_rejected(tmp_path: Path):
    with pytest.raises(
        PerformanceArtifactValidationError,
        match="duplicate JSON field",
    ):
        persist_performance_artifacts(
            tmp_path,
            RUN_ID,
            inputs(contract_json=b'{"id":"one","id":"two"}'),
        )
    assert not (tmp_path / RUN_ID).exists()


@pytest.mark.parametrize(
    "invalid_html",
    [b"", b"   \n", b"\xff", b"<html>\x00</html>"],
)
def test_decision_packet_requires_nonempty_utf8_html(
    tmp_path: Path,
    invalid_html: bytes,
):
    with pytest.raises(
        PerformanceArtifactValidationError,
        match="UTF-8 HTML",
    ):
        persist_performance_artifacts(
            tmp_path,
            RUN_ID,
            inputs(decision_packet_html=invalid_html),
        )


@pytest.mark.parametrize(
    "field_name",
    ["prompt_fixture_jsonl", "records_jsonl"],
)
def test_jsonl_must_be_nonempty_objects_without_blank_records(
    tmp_path: Path,
    field_name: str,
):
    with pytest.raises(
        PerformanceArtifactValidationError,
        match="non-empty JSON objects",
    ):
        persist_performance_artifacts(
            tmp_path,
            RUN_ID,
            inputs(**{field_name: b'{"id":"one"}\n\n{"id":"two"}'}),
        )


def test_unknown_or_unverified_redaction_state_is_rejected(tmp_path: Path):
    with pytest.raises(
        PerformanceArtifactValidationError,
        match="unknown artifact path",
    ):
        persist_performance_artifacts(
            tmp_path,
            RUN_ID,
            inputs(redaction_states={"../outside": "redacted"}),
        )

    with pytest.raises(
        PerformanceArtifactValidationError,
        match="not supported",
    ):
        persist_performance_artifacts(
            tmp_path,
            RUN_ID,
            inputs(redaction_states={"contract.json": "trust-me"}),
        )

    with pytest.raises(
        PerformanceArtifactValidationError,
        match="not supported",
    ):
        persist_performance_artifacts(
            tmp_path,
            RUN_ID,
            inputs(redaction_states={"contract.json": ["redacted"]}),
        )


@pytest.mark.parametrize(
    "relative_path",
    [
        "contract.json",
        "confirmation.json",
        "workload.json",
        "prompt-fixture.jsonl",
        "evidence/preflight.json",
        "evidence/probe-manifest.json",
        "evidence/probe-records.jsonl",
        "receipt.json",
        "calculations.json",
        "verdicts.json",
        "decision-packet.html",
        REGISTRY_PATH,
    ],
)
def test_tampering_any_hashed_artifact_is_rejected(
    tmp_path: Path,
    relative_path: str,
):
    verified = persist(tmp_path)
    target = verified.run_dir / relative_path
    target.write_bytes(target.read_bytes() + b" ")

    with pytest.raises(
        PerformanceArtifactIntegrityError,
        match="size|hash|canonical",
    ):
        read_and_verify_performance_artifacts(verified.run_dir)


@pytest.mark.parametrize(
    "relative_path",
    [
        "contract.json",
        "evidence/probe-records.jsonl",
        REGISTRY_PATH,
        HASHES_PATH,
    ],
)
def test_missing_required_artifact_is_rejected(
    tmp_path: Path,
    relative_path: str,
):
    verified = persist(tmp_path)
    (verified.run_dir / relative_path).unlink()

    with pytest.raises(
        PerformanceArtifactIntegrityError,
        match="missing or extra artifacts",
    ):
        read_and_verify_performance_artifacts(verified.run_dir)


def test_extra_unregistered_file_and_directory_are_rejected(tmp_path: Path):
    verified = persist(tmp_path)
    extra = verified.run_dir / "evidence" / "unregistered.bin"
    extra.write_bytes(b"not registered")

    with pytest.raises(
        PerformanceArtifactIntegrityError,
        match="missing or extra artifacts",
    ):
        read_and_verify_performance_artifacts(verified.run_dir)

    extra.unlink()
    (verified.run_dir / "unexpected").mkdir()
    with pytest.raises(
        PerformanceArtifactIntegrityError,
        match="missing or extra directories",
    ):
        read_and_verify_performance_artifacts(verified.run_dir)


def test_symlinked_artifact_and_symlinked_run_are_rejected(tmp_path: Path):
    verified = persist(tmp_path)
    contract = verified.run_dir / "contract.json"
    external = tmp_path / "external.json"
    external.write_bytes(contract.read_bytes())
    contract.unlink()
    contract.symlink_to(external)

    with pytest.raises(
        PerformanceArtifactIntegrityError,
        match="cannot be symlinks",
    ):
        read_and_verify_performance_artifacts(verified.run_dir)

    alias = tmp_path / "run_alias"
    alias.symlink_to(verified.run_dir, target_is_directory=True)
    with pytest.raises(
        PerformanceArtifactIntegrityError,
        match="cannot be a symlink",
    ):
        read_and_verify_performance_artifacts(alias)


def test_symlinked_evidence_directory_escape_is_rejected(tmp_path: Path):
    verified = persist(tmp_path)
    evidence = verified.run_dir / "evidence"
    outside = tmp_path / "outside-evidence"
    evidence.rename(outside)
    evidence.symlink_to(outside, target_is_directory=True)

    with pytest.raises(
        PerformanceArtifactIntegrityError,
        match="directories cannot be symlinks",
    ):
        read_and_verify_performance_artifacts(verified.run_dir)


def test_duplicate_registry_ids_are_rejected_even_if_json_is_canonical(
    tmp_path: Path,
):
    verified = persist(tmp_path)
    registry_path = verified.run_dir / REGISTRY_PATH
    registry = json.loads(registry_path.read_bytes())
    registry["artifacts"][1]["artifact_id"] = registry["artifacts"][0][
        "artifact_id"
    ]
    registry_path.write_bytes(canonical_json_bytes(registry))

    with pytest.raises(
        PerformanceArtifactIntegrityError,
        match="unique",
    ):
        read_and_verify_performance_artifacts(verified.run_dir)


def test_duplicate_or_traversal_registry_paths_are_rejected(tmp_path: Path):
    verified = persist(tmp_path)
    registry_path = verified.run_dir / REGISTRY_PATH
    registry = json.loads(registry_path.read_bytes())
    registry["artifacts"][1]["path"] = registry["artifacts"][0]["path"]
    registry_path.write_bytes(canonical_json_bytes(registry))
    with pytest.raises(
        PerformanceArtifactIntegrityError,
        match="unique",
    ):
        read_and_verify_performance_artifacts(verified.run_dir)

    registry = json.loads(verified.registry_json)
    registry["artifacts"][0]["path"] = "../contract.json"
    registry_path.write_bytes(canonical_json_bytes(registry))
    with pytest.raises(
        PerformanceArtifactIntegrityError,
        match="unsafe",
    ):
        read_and_verify_performance_artifacts(verified.run_dir)


def test_partial_write_leaves_no_visible_run_or_temporary_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    from exitspec import performance_artifacts

    real_write = performance_artifacts._write_file
    calls = 0

    def fail_after_three_files(path: Path, payload: bytes) -> None:
        nonlocal calls
        calls += 1
        if calls == 4:
            raise OSError("simulated disk failure")
        real_write(path, payload)

    monkeypatch.setattr(
        performance_artifacts,
        "_write_file",
        fail_after_three_files,
    )

    with pytest.raises(
        PerformanceArtifactValidationError,
        match="could not be published",
    ):
        persist_performance_artifacts(tmp_path, RUN_ID, inputs())

    assert not (tmp_path / RUN_ID).exists()
    assert list(tmp_path.iterdir()) == []


def test_hash_inventory_cannot_hide_an_extra_registered_artifact(
    tmp_path: Path,
):
    verified = persist(tmp_path)
    inventory_path = verified.run_dir / HASHES_PATH
    inventory = json.loads(inventory_path.read_bytes())
    inventory["artifacts"].append(
        {
            "artifact_id": "hidden-extra",
            "artifact_type": "hidden",
            "path": "hidden.json",
            "media_type": "application/json",
            "size_bytes": 2,
            "sha256": hashlib.sha256(b"{}").hexdigest(),
            "redaction_state": "not_assessed",
        }
    )
    inventory_path.write_bytes(canonical_json_bytes(inventory))

    with pytest.raises(
        PerformanceArtifactIntegrityError,
        match="outside the fixed layout",
    ):
        read_and_verify_performance_artifacts(verified.run_dir)


def test_verified_read_reapplies_aggregate_byte_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    from exitspec import performance_artifacts

    verified = persist(tmp_path)
    total = sum(
        len(verified.files[path])
        for path in verified.files
        if path not in {REGISTRY_PATH, HASHES_PATH}
    )
    monkeypatch.setattr(
        performance_artifacts,
        "MAX_TOTAL_PAYLOAD_BYTES",
        total - 1,
    )

    with pytest.raises(
        PerformanceArtifactIntegrityError,
        match="aggregate byte limit",
    ):
        read_and_verify_performance_artifacts(verified.run_dir)


def test_output_root_symlink_is_rejected(tmp_path: Path):
    real_root = tmp_path / "real-root"
    real_root.mkdir()
    alias = tmp_path / "root-alias"
    alias.symlink_to(real_root, target_is_directory=True)

    with pytest.raises(
        PerformanceArtifactValidationError,
        match="cannot be a symlink",
    ):
        persist_performance_artifacts(alias, RUN_ID, inputs())


def test_registered_files_are_regular_files(tmp_path: Path):
    verified = persist(tmp_path)
    records = verified.run_dir / "evidence" / "probe-records.jsonl"
    records.unlink()
    os.mkfifo(records)
    try:
        with pytest.raises(
            PerformanceArtifactIntegrityError,
            match="regular file|cannot be opened safely",
        ):
            read_and_verify_performance_artifacts(verified.run_dir)
    finally:
        records.unlink()
