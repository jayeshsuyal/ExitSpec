from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil

import pytest
from pydantic import ValidationError

import exitspec.inferdrome_prospective as prospective
from exitspec.canonical import canonical_json_bytes
from exitspec.confirmations import ConfirmationDecision
from exitspec.contracts import contract_digest, verify_contract_digest
from exitspec.models import (
    InferdromeEvidenceIdentityV1,
    InferdromeEvidenceIdentityV2,
    InferencePerformanceCriterionV3,
    InferencePerformanceCriterionV4,
)
from exitspec.runner import load_contract


PROJECT_ROOT = Path(__file__).resolve().parents[1]
HANDOFF_FIXTURE = PROJECT_ROOT / "examples/inference-performance/inferdrome-p1"
WORKLOAD_FIXTURE = HANDOFF_FIXTURE / "sources/real-gpu/workload.jsonl"
RETROSPECTIVE_V3_FIXTURE = (
    PROJECT_ROOT
    / "examples/inference-performance/inferdrome-a10/contracts/pass.frozen.json"
)
FIXED_TIME = datetime(2026, 8, 27, tzinfo=timezone.utc)


def _frozen_cases() -> tuple[prospective.FrozenProspectiveCase, ...]:
    return tuple(
        prospective.freeze_prospective_case(
            case.case_id,
            created_at=FIXED_TIME,
            confirmer_identity="process-local-customer-reviewer",
            decided_at=FIXED_TIME,
            frozen_at=FIXED_TIME,
        )
        for case in prospective.PROSPECTIVE_CASES
    )


def _manifest_payload(root: Path) -> dict[str, object]:
    return json.loads((root / "handoff-manifest.json").read_bytes())


def _write_manifest(root: Path, payload: dict[str, object]) -> None:
    (root / "handoff-manifest.json").write_bytes(canonical_json_bytes(payload))


def _copied_handoff(source: Path, destination: Path) -> Path:
    shutil.copytree(source, destination, symlinks=True)
    for directory, names, files in os.walk(destination, followlinks=False):
        os.chmod(directory, 0o700)
        for name in files:
            file_path = Path(directory) / name
            if not file_path.is_symlink():
                os.chmod(file_path, 0o600)
    return destination


@pytest.fixture
def generated_handoff(tmp_path: Path) -> Path:
    root = tmp_path / "generated-handoff"
    prospective.materialize_prospective_handoff(
        root,
        _frozen_cases(),
        workload_bytes=WORKLOAD_FIXTURE.read_bytes(),
    )
    return root


def test_three_distinct_cases_freeze_complete_run_independent_identity():
    cases = _frozen_cases()
    assert [case.case.case_id for case in cases] == [
        "native-p95-under-20ms",
        "native-p95-under-10ms",
        "semantic-first-nonempty-under-20ms",
    ]
    assert len({case.contract.canonical_hash for case in cases}) == 3
    assert len({case.producer_contract_link for case in cases}) == 3
    assert len({case.confirmation.confirmation_id for case in cases}) == 3
    assert all(case.contract.status.value == "FROZEN" for case in cases)
    for case in cases:
        identity = case.contract.criteria[0].evidence_identity
        assert identity.sequence_requirement == (
            "OPERATOR_MUST_FREEZE_BEFORE_MEASUREMENT"
        )
        assert identity.chronology_assurance == "UNAVAILABLE"
        assert identity.expected_execution_fingerprint == (
            prospective.PROSPECTIVE_EXPECTED_EXECUTION_FINGERPRINT
        )
        assert identity.produced_evidence_metric_definition_id == (
            "vllm_first_choices_event_v0_26"
        )
        assert identity.requested_criterion_metric_definition_id == (
            case.case.requested_criterion_metric_definition_id
        )
        assert identity.run_aggregation_policy == ("independent_single_run_no_pooling")
        serialized = json.dumps(identity.model_dump(mode="json"))
        for forbidden in ("request_plan_digest", "run_id", "bundle_digest"):
            assert forbidden not in serialized


def test_canonicalization_binding_distinguishes_bare_hash_and_producer_link():
    identity = _frozen_cases()[0].contract.criteria[0].evidence_identity
    binding = identity.canonicalization
    assert binding.canonical_bytes_encoding == "utf-8_rfc8785_jcs"
    assert binding.hash_algorithm_id == "sha256_v1"
    assert binding.hash_encoding_id == "lowercase_hex_without_prefix"
    assert binding.link_derivation_input == "bare_canonical_hash"
    assert binding.link_derivation_operation == "prefix_sha256_no_second_hash"
    case = _frozen_cases()[0]
    assert len(case.contract_canonical_hash) == 64
    assert case.contract_canonical_hash != case.producer_contract_link
    assert case.producer_contract_link == ("sha256:" + case.contract_canonical_hash)


def test_retrospective_v1_v3_surface_remains_separate_and_unchanged():
    assert "request_plan_digest" in InferdromeEvidenceIdentityV1.model_fields
    assert "chronology" in InferdromeEvidenceIdentityV1.model_fields
    assert InferdromeEvidenceIdentityV1.model_fields[
        "producer_contract_link"
    ].annotation
    assert InferencePerformanceCriterionV3.model_fields["criterion_type"].annotation
    assert InferencePerformanceCriterionV3.model_fields["evidence_identity"].annotation
    assert InferencePerformanceCriterionV4.model_fields["criterion_type"].annotation
    assert prospective.PROSPECTIVE_HANDOFF_SCHEMA_VERSION.endswith(".v1")


def test_retrospective_v3_fixture_hash_and_parse_behavior_remain_unchanged():
    contract = load_contract(RETROSPECTIVE_V3_FIXTURE)
    assert type(contract.criteria[0]) is InferencePerformanceCriterionV3
    assert contract.canonical_hash == (
        "d97779d549a5c227ec65ca66294c0f2ddfdd09c2fdc15505765fc58cb6d75d9d"
    )
    assert contract_digest(contract) == contract.canonical_hash
    assert verify_contract_digest(contract)


@pytest.mark.parametrize(
    "link",
    [
        "sha256:" + "A" * 64,
        "SHA256:" + "a" * 64,
        "sha256:sha256:" + "a" * 64,
        "sha256:" + "a" * 63,
    ],
)
def test_producer_links_require_exact_lowercase_single_prefix(link: str):
    with pytest.raises(prospective.ProspectiveHandoffError):
        prospective.source_yaml_bytes(prospective.PROSPECTIVE_CASES[0], link)
    with pytest.raises(prospective.ProspectiveHandoffError):
        prospective.derive_producer_contract_link(link)


def test_unsupported_link_policy_and_canonicalization_are_rejected():
    with pytest.raises(prospective.ProspectiveHandoffError):
        prospective.derive_producer_contract_link(
            "a" * 64,
            link_derivation_policy_id="future-policy",
        )
    identity = _frozen_cases()[0].contract.criteria[0].evidence_identity
    payload = identity.model_dump(mode="json")
    payload["canonicalization"]["hash_algorithm_id"] = "sha512_v1"
    with pytest.raises(ValidationError):
        InferdromeEvidenceIdentityV2.model_validate_json(
            canonical_json_bytes(payload), strict=True
        )


def test_strict_identity_rejects_retrospective_and_future_run_fields():
    identity = _frozen_cases()[0].contract.criteria[0].evidence_identity
    payload = identity.model_dump(mode="json")
    for field in (
        "request_plan_digest",
        "run_id",
        "bundle_digest",
        "producer_contract_link",
    ):
        mutated = deepcopy(payload)
        mutated[field] = "sha256:" + "a" * 64
        with pytest.raises(ValidationError):
            InferdromeEvidenceIdentityV2.model_validate_json(
                canonical_json_bytes(mutated), strict=True
            )


@pytest.mark.parametrize(
    "field",
    [
        "native_schema_fingerprint",
        "managed_profile_sha256",
        "local_gpu_proof_schema_sha256",
        "workload_digest",
        "expected_execution_fingerprint",
    ],
)
def test_v2_pinned_digest_fields_reject_methodology_drift(field: str):
    payload = (
        _frozen_cases()[0]
        .contract.criteria[0]
        .evidence_identity.model_dump(mode="json")
    )
    payload[field] = "sha256:" + "0" * 64
    with pytest.raises(ValidationError):
        InferdromeEvidenceIdentityV2.model_validate_json(
            canonical_json_bytes(payload), strict=True
        )


def test_source_methodology_is_capture_identity_free_and_pinned():
    case = prospective.PROSPECTIVE_CASES[0]
    source = prospective.source_yaml_bytes(
        case,
        "sha256:" + "a" * 64,
    ).decode()
    assert "execution_fingerprint" not in source
    assert "request_plan_digest" not in source
    assert "run_id" not in source
    assert "bundle_digest" not in source
    assert "inferdrome.source-experiment.v1" in source


def test_manifest_is_exact_inventory_and_binds_transport_digests(
    generated_handoff: Path,
):
    validation = prospective.validate_prospective_handoff(generated_handoff)
    payload = _manifest_payload(generated_handoff)
    assert validation.manifest.acceptance_verdict is None
    assert payload["acceptance_verdict"] is None
    assert payload["workload_artifact_path"] == ("sources/real-gpu/workload.jsonl")
    assert (
        payload["workload_artifact_sha256"] == prospective.PROSPECTIVE_WORKLOAD_DIGEST
    )
    assert payload["completion_marker"] == ".complete"
    assert payload["confirmation_identity_assurance"] == (
        "PROCESS_LOCAL_DECLARED_IDENTITY_NOT_AUTHENTICATED"
    )
    for item in payload["cases"]:
        assert item["contract_artifact_sha256"].startswith("sha256:")
        assert item["confirmation_record_sha256"].startswith("sha256:")
        assert item["source_yaml_artifact_sha256"].startswith("sha256:")
        assert item["contract_canonical_hash"] != item["contract_artifact_sha256"]


@pytest.mark.parametrize("mutation", ["unknown", "missing", "duplicate_case"])
def test_manifest_unknown_missing_and_duplicate_fields_fail_closed(
    generated_handoff: Path, mutation: str
):
    root = _copied_handoff(generated_handoff, generated_handoff.parent / mutation)
    payload = _manifest_payload(root)
    if mutation == "unknown":
        payload["future_field"] = True
    elif mutation == "missing":
        del payload["workload_artifact_sha256"]
    else:
        payload["cases"][1]["case_id"] = payload["cases"][0]["case_id"]
    _write_manifest(root, payload)
    with pytest.raises(prospective.ProspectiveHandoffError):
        prospective.validate_prospective_handoff(root)


def test_strict_json_rejects_duplicate_keys_nonfinite_float_and_unbounded_nodes():
    with pytest.raises(prospective.ProspectiveHandoffError):
        prospective._strict_json_bytes(b'{"a":1,"a":2}', label="test")
    with pytest.raises(prospective.ProspectiveHandoffError):
        prospective._strict_json_bytes(b'{"a":NaN}', label="test")
    with pytest.raises(prospective.ProspectiveHandoffError):
        prospective._strict_json_bytes(b'{"a":1.5}', label="test")
    with pytest.raises(prospective.ProspectiveHandoffError):
        prospective._strict_json_bytes(
            b'{"a":' * (prospective.PROSPECTIVE_MAX_JSON_DEPTH + 2)
            + b"0"
            + b"}" * (prospective.PROSPECTIVE_MAX_JSON_DEPTH + 2),
            label="test",
        )
    with pytest.raises(prospective.ProspectiveHandoffError):
        prospective._strict_json_bytes(
            canonical_json_bytes(
                {"values": list(range(prospective.PROSPECTIVE_MAX_JSON_NODES + 1))}
            ),
            label="test",
        )
    with pytest.raises(prospective.ProspectiveHandoffError):
        prospective._strict_json_bytes(
            b'{"value":2147483648}',
            label="test",
        )


def test_confirmation_requires_affirmative_exact_current_version(tmp_path: Path):
    case = _frozen_cases()[0]
    for index, update in enumerate(
        (
            {
                "decision": ConfirmationDecision.REQUEST_CHANGES,
                "agreement_acknowledged": False,
            },
            {"contract_fingerprint": "0" * 64},
            {"contract_version": "2.0.0"},
        )
    ):
        confirmation = case.confirmation.model_copy(update=update)
        candidate = replace(case, confirmation=confirmation)
        with pytest.raises(prospective.ProspectiveHandoffError):
            prospective._assert_frozen_case(candidate)
        root = tmp_path / f"invalid-confirmation-{index}"
        cases = list(_frozen_cases())
        cases[0] = candidate
        with pytest.raises(prospective.ProspectiveHandoffError):
            prospective.materialize_prospective_handoff(
                root,
                tuple(cases),
                workload_bytes=WORKLOAD_FIXTURE.read_bytes(),
            )


def test_confirmation_and_contract_cross_swaps_are_rejected(generated_handoff: Path):
    root = _copied_handoff(generated_handoff, generated_handoff.parent / "cross-swap")
    payload = _manifest_payload(root)
    first, second = payload["cases"][0], payload["cases"][1]
    first_path = root / first["confirmation_artifact_path"]
    second_path = root / second["confirmation_artifact_path"]
    first_bytes, second_bytes = first_path.read_bytes(), second_path.read_bytes()
    first_path.write_bytes(second_bytes)
    second_path.write_bytes(first_bytes)
    first["confirmation_record_sha256"], second["confirmation_record_sha256"] = (
        second["confirmation_record_sha256"],
        first["confirmation_record_sha256"],
    )
    first["confirmation_id"], second["confirmation_id"] = (
        second["confirmation_id"],
        first["confirmation_id"],
    )
    (
        first["contract_confirmation_fingerprint"],
        second["contract_confirmation_fingerprint"],
    ) = (
        second["contract_confirmation_fingerprint"],
        first["contract_confirmation_fingerprint"],
    )
    _write_manifest(root, payload)
    with pytest.raises(prospective.ProspectiveHandoffError):
        prospective.validate_prospective_handoff(root)

    root = _copied_handoff(
        generated_handoff, generated_handoff.parent / "contract-swap"
    )
    payload = _manifest_payload(root)
    first, second = payload["cases"][0], payload["cases"][1]
    first_path = root / first["contract_artifact_path"]
    second_path = root / second["contract_artifact_path"]
    first_bytes, second_bytes = first_path.read_bytes(), second_path.read_bytes()
    first_path.write_bytes(second_bytes)
    second_path.write_bytes(first_bytes)
    for field in (
        "contract_artifact_sha256",
        "contract_canonical_hash",
        "producer_contract_link",
        "contract_id",
        "contract_version",
        "contract_confirmation_fingerprint",
    ):
        first[field], second[field] = second[field], first[field]
    _write_manifest(root, payload)
    with pytest.raises(prospective.ProspectiveHandoffError):
        prospective.validate_prospective_handoff(root)


def test_source_and_confirmation_bindings_reject_swapped_content_with_updated_digests(
    generated_handoff: Path,
):
    root = _copied_handoff(
        generated_handoff, generated_handoff.parent / "deep-source-swap"
    )
    payload = _manifest_payload(root)
    first, second = payload["cases"][0], payload["cases"][1]
    first_path = root / first["source_yaml_artifact_path"]
    second_path = root / second["source_yaml_artifact_path"]
    first_bytes, second_bytes = first_path.read_bytes(), second_path.read_bytes()
    first_path.write_bytes(second_bytes)
    second_path.write_bytes(first_bytes)
    first["source_yaml_artifact_sha256"], second["source_yaml_artifact_sha256"] = (
        second["source_yaml_artifact_sha256"],
        first["source_yaml_artifact_sha256"],
    )
    _write_manifest(root, payload)
    with pytest.raises(prospective.ProspectiveHandoffError):
        prospective.validate_prospective_handoff(root)


def test_duplicate_hash_link_confirmation_and_record_digest_fail(
    generated_handoff: Path,
):
    for field in (
        "contract_canonical_hash",
        "producer_contract_link",
        "confirmation_id",
        "confirmation_record_sha256",
    ):
        root = _copied_handoff(
            generated_handoff,
            generated_handoff.parent / f"duplicate-{field}",
        )
        payload = _manifest_payload(root)
        payload["cases"][1][field] = payload["cases"][0][field]
        _write_manifest(root, payload)
        with pytest.raises(prospective.ProspectiveHandoffError):
            prospective.validate_prospective_handoff(root)


@pytest.mark.parametrize(
    "field",
    [
        "metric_definition_id",
        "threshold_ns",
        "operator",
        "unit",
        "reducer_id",
        "population",
    ],
)
def test_criterion_semantics_cannot_drift(field: str):
    case = _frozen_cases()[0]
    criterion = case.contract.criteria[0]
    payload = criterion.model_dump(mode="json")
    if field == "metric_definition_id":
        payload["evidence_identity"]["requested_criterion_metric_definition_id"] = (
            "first_nonempty_choices_delta_content_v1"
        )
    elif field == "threshold_ns":
        payload["ttft_p95"]["threshold_ns"] = 10_000_000
    elif field == "operator":
        payload["ttft_p95"]["operator"] = "lte"
    elif field == "unit":
        payload["ttft_p95"]["unit"] = "milliseconds"
    elif field == "reducer_id":
        payload["ttft_p95"]["reducer_id"] = "other"
    else:
        payload["ttft_p95"]["population"] = "all_measured_requests"
    with pytest.raises(ValidationError):
        InferencePerformanceCriterionV4.model_validate_json(
            canonical_json_bytes(payload), strict=True
        )


def test_contract_and_source_mutation_are_detected(generated_handoff: Path):
    root = _copied_handoff(generated_handoff, generated_handoff.parent / "mutation")
    contract = root / "contracts/native-p95-under-20ms.frozen.json"
    payload = json.loads(contract.read_bytes())
    payload["criteria"][0]["evidence_policy"] = "tampered"
    payload["canonical_hash"] = None
    candidate = prospective.parse_contract(canonical_json_bytes(payload))
    payload["canonical_hash"] = contract_digest(candidate)
    contract_bytes = canonical_json_bytes(payload)
    contract.write_bytes(contract_bytes)
    manifest = _manifest_payload(root)
    manifest["cases"][0]["contract_artifact_sha256"] = (
        "sha256:" + hashlib.sha256(contract_bytes).hexdigest()
    )
    manifest["cases"][0]["contract_canonical_hash"] = payload["canonical_hash"]
    manifest["cases"][0]["producer_contract_link"] = (
        "sha256:" + payload["canonical_hash"]
    )
    with pytest.raises(prospective.ProspectiveHandoffError):
        _write_manifest(root, manifest)
        prospective.validate_prospective_handoff(root)

    root = _copied_handoff(
        generated_handoff, generated_handoff.parent / "source-mutation"
    )
    source = root / "sources/native-p95-under-20ms.yaml"
    source.write_bytes(source.read_bytes().replace(b"concurrent", b"request_rate", 1))
    with pytest.raises(prospective.ProspectiveHandoffError):
        prospective.validate_prospective_handoff(root)


def test_tree_rejects_path_escape_symlink_and_extra_file(generated_handoff: Path):
    root = _copied_handoff(generated_handoff, generated_handoff.parent / "tree-extra")
    (root / "unexpected.txt").write_bytes(b"x")
    with pytest.raises(prospective.ProspectiveHandoffError):
        prospective.validate_prospective_handoff(root)

    root = _copied_handoff(generated_handoff, generated_handoff.parent / "tree-symlink")
    source = root / "sources/native-p95-under-20ms.yaml"
    source.unlink()
    os.symlink("real-gpu/workload.jsonl", source)
    with pytest.raises(prospective.ProspectiveHandoffError):
        prospective.validate_prospective_handoff(root)

    root = _copied_handoff(generated_handoff, generated_handoff.parent / "path-escape")
    payload = _manifest_payload(root)
    payload["cases"][0]["source_yaml_artifact_path"] = "../outside.yaml"
    _write_manifest(root, payload)
    with pytest.raises(prospective.ProspectiveHandoffError):
        prospective.validate_prospective_handoff(root)


def test_atomic_stage_is_reaped_and_existing_root_is_never_overwritten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    root = tmp_path / "atomic"
    original_write = prospective._write_new
    calls = 0

    def fail_after_first(path: Path, content: bytes) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise prospective.ProspectiveHandoffError("injected write failure")
        original_write(path, content)

    monkeypatch.setattr(prospective, "_write_new", fail_after_first)
    with pytest.raises(prospective.ProspectiveHandoffError):
        prospective.materialize_prospective_handoff(
            root, _frozen_cases(), workload_bytes=WORKLOAD_FIXTURE.read_bytes()
        )
    assert not root.exists()
    assert not list(tmp_path.glob(".atomic.staging-*"))

    monkeypatch.setattr(prospective, "_write_new", original_write)
    root.mkdir()
    sentinel = root / "sentinel"
    sentinel.write_bytes(b"keep")
    with pytest.raises(prospective.ProspectiveHandoffError):
        prospective.materialize_prospective_handoff(
            root, _frozen_cases(), workload_bytes=WORKLOAD_FIXTURE.read_bytes()
        )
    assert sentinel.read_bytes() == b"keep"

    success_root = tmp_path / "success"
    prospective.materialize_prospective_handoff(
        success_root,
        _frozen_cases(),
        workload_bytes=WORKLOAD_FIXTURE.read_bytes(),
    )
    assert not list(tmp_path.glob(".success.staging-*"))


def test_timestamp_order_is_local_consistency_not_chronology_assurance():
    with pytest.raises(prospective.ProspectiveHandoffError):
        prospective.freeze_prospective_case(
            "native-p95-under-20ms",
            created_at=FIXED_TIME,
            confirmer_identity="process-local-customer-reviewer",
            decided_at=FIXED_TIME - timedelta(seconds=1),
            frozen_at=FIXED_TIME,
        )
    identity = _frozen_cases()[0].contract.criteria[0].evidence_identity
    assert identity.chronology_assurance == "UNAVAILABLE"
    assert identity.claims_assurance == "INTERNAL_CONSISTENCY_ONLY"


def test_final_inventory_recheck_catches_mutation_during_validation(
    generated_handoff: Path, monkeypatch: pytest.MonkeyPatch
):
    original = prospective._validate_source
    injected = False

    def add_file_after_read(path: Path, case: object, link: str) -> None:
        nonlocal injected
        original(path, case, link)
        if not injected:
            injected = True
            (path.parent.parent / "injected.txt").write_bytes(b"late")

    monkeypatch.setattr(prospective, "_validate_source", add_file_after_read)
    with pytest.raises(prospective.ProspectiveHandoffError):
        prospective.validate_prospective_handoff(generated_handoff)


def test_final_identity_snapshot_catches_same_size_source_mutation(
    generated_handoff: Path, monkeypatch: pytest.MonkeyPatch
):
    root = _copied_handoff(generated_handoff, generated_handoff.parent / "same-size")
    original = prospective._validate_source
    mutated = False

    def mutate_after_read(path: Path, case: object, link: str) -> None:
        nonlocal mutated
        original(path, case, link)
        if not mutated:
            content = path.read_bytes()
            path.write_bytes((b"#" if content[:1] != b"#" else b"!") + content[1:])
            assert len(path.read_bytes()) == len(content)
            mutated = True

    monkeypatch.setattr(prospective, "_validate_source", mutate_after_read)
    with pytest.raises(prospective.ProspectiveHandoffError):
        prospective.validate_prospective_handoff(root)


def test_checked_in_handoff_is_sealed_and_hash_pinned():
    validation = prospective.validate_prospective_handoff(HANDOFF_FIXTURE)
    assert validation.manifest_sha256 == (
        "2dfb5808c2b172f0fd17d034421aa8439f96c54f0a578b7c3f42bdcba2b8231c"
    )
    expected = {
        "native-p95-under-20ms": (
            "c73f3fe1127575443bc30baa1cac4a610dfebfcd721ac72a2c998a6bf1c21580",
            "sha256:c73f3fe1127575443bc30baa1cac4a610dfebfcd721ac72a2c998a6bf1c21580",
            "sha256:1927a81005adcfef665221ac515ae528143394ef159a34074cba0595f52c05d9",
            "sha256:5cb2128073c9626d42637e60c7a86155cf33f78d3c730f7c655079a28fb35304",
            "sha256:97a9b6266fec0036d764f78a6670888562abc35ce4da537d58b68dd835a429f5",
        ),
        "native-p95-under-10ms": (
            "6a499cfc2e15245e905ecec8282910536e1a594ca3a4d9117e50394ee4f0d855",
            "sha256:6a499cfc2e15245e905ecec8282910536e1a594ca3a4d9117e50394ee4f0d855",
            "sha256:e5d30b7067fea926956353d8936d1ecf7e49801675c39eb5f40ea3ca8cf59856",
            "sha256:38ca98ac2f018de3f3ef0c38da0021ad98617a590dcb08e5226e9244780f03bd",
            "sha256:a7645e6a74789245c443d3b38d77eca769e57e7460815e6aad9895a33e09d610",
        ),
        "semantic-first-nonempty-under-20ms": (
            "fe776bccedbd5a935480be5808bfaf73b60e17c3f275c2c9b1ba46c2ba9eb248",
            "sha256:fe776bccedbd5a935480be5808bfaf73b60e17c3f275c2c9b1ba46c2ba9eb248",
            "sha256:a946b22825f27e352dd1c0a3453051c7401348ce8576b350b1a96935c9134516",
            "sha256:fe7790ffdbe113db11a07462e11e304c970d89400b1fedd9e5864c650a213e67",
            "sha256:85b1fefc07f1c99237d1810baa17c42dda5320c920da6211e14a56e9d0fbc8f0",
        ),
    }
    for item in validation.manifest.cases:
        assert (
            item.contract_canonical_hash,
            item.producer_contract_link,
            item.contract_artifact_sha256,
            item.confirmation_record_sha256,
            item.source_yaml_artifact_sha256,
        ) == expected[item.case_id]


def test_prospective_module_exports_the_renamed_expected_fingerprint():
    assert "PROSPECTIVE_EXPECTED_EXECUTION_FINGERPRINT" in prospective.__all__
    assert "PROSPECTIVE_EXECUTION_FINGERPRINT" not in prospective.__all__
