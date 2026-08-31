"""Adversarial contract tests for v0.5 serving-subject identity."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError

from exitspec.canonical import canonical_json_bytes
from exitspec.serving_subject import (
    SERVING_SUBJECT_DIGEST_DOMAIN,
    SERVING_SUBJECT_SCHEMA_VERSION,
    ServingSubjectManifestV1,
    ServingSubjectRejected,
    ServingSubjectValidationCode,
    canonical_serving_subject_projection,
    create_serving_subject_manifest,
    parse_serving_subject_manifest,
    serialize_serving_subject_manifest,
    serving_subject_digest,
    verify_serving_subject_manifest,
)

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "tests" / "fixtures" / "serving_subject" / "v1" / "golden.json"
GOLDEN_DIGEST = (
    "sha256:2921dd76c90a5dd4a6131ef8bb7a369f7b4b1a3a829744751e6b38e81dfb988a"
)


def _unsigned_payload() -> dict[str, object]:
    return {
        "schema_version": SERVING_SUBJECT_SCHEMA_VERSION,
        "model": {"component_id": "acme/model-x", "revision": "0123456789abcdef"},
        "tokenizer": {
            "component_id": "acme/tokenizer-x",
            "revision": "abcdef0123456789",
        },
        "engine": {"engine_id": "vllm", "engine_version": "0.26.0"},
        "runtime_artifact_digest": "sha256:" + "a" * 64,
        "runtime_configuration_json": (
            '{"gpu_memory_utilization":90,"scheduler":{"max_num_seqs":8},"seed":42}'
        ),
        "launch_arguments_digest": "sha256:" + "d" * 64,
        "hardware": {"hardware_class": "NVIDIA-H100-SXM5-80GB", "topology": "1x8"},
        "profile": {
            "profile_id": "serving-profile",
            "profile_version": "1.0.0+pin",
            "adapter_id": "bench-adapter",
            "adapter_version": "1.0.0+pin",
        },
        "routing_policy_id": "route-policy",
        "routing_policy_digest": "sha256:" + "b" * 64,
    }


def _manifest_payload() -> dict[str, object]:
    return create_serving_subject_manifest(_unsigned_payload()).model_dump(mode="json")


def _golden_bytes() -> bytes:
    return GOLDEN.read_bytes()


def _parse_error(value: bytes | dict[str, object]) -> ServingSubjectRejected:
    with pytest.raises(ServingSubjectRejected) as raised:
        parse_serving_subject_manifest(value)
    return raised.value


def _create_error(value: dict[str, object]) -> ServingSubjectRejected:
    with pytest.raises(ServingSubjectRejected) as raised:
        create_serving_subject_manifest(value)
    return raised.value


def test_golden_vector_is_exact_and_self_verifying() -> None:
    raw = _golden_bytes()
    fixture = json.loads(raw)
    fixture_digest = fixture.pop("subject_digest")
    expected_digest = (
        "sha256:"
        + hashlib.sha256(
            b"exitspec-serving-subject-manifest-v1\x00" + canonical_json_bytes(fixture)
        ).hexdigest()
    )

    manifest = parse_serving_subject_manifest(raw)

    assert raw == canonical_json_bytes({**fixture, "subject_digest": fixture_digest})
    assert fixture_digest == GOLDEN_DIGEST
    assert expected_digest == GOLDEN_DIGEST
    assert manifest.subject_digest == GOLDEN_DIGEST
    assert serving_subject_digest(manifest) == GOLDEN_DIGEST
    assert serialize_serving_subject_manifest(manifest) == raw
    assert verify_serving_subject_manifest(manifest)
    assert SERVING_SUBJECT_DIGEST_DOMAIN == b"exitspec-serving-subject-manifest-v1\x00"


def test_create_has_one_unsigned_projection_and_canonical_api() -> None:
    manifest = create_serving_subject_manifest(_unsigned_payload())

    projection = canonical_serving_subject_projection(manifest)

    assert "subject_digest" not in projection
    assert set(manifest.model_dump(mode="json")) == {"subject_digest", *projection}
    assert parse_serving_subject_manifest(manifest.model_dump(mode="json")) == manifest
    assert _create_error(
        {**_unsigned_payload(), "subject_digest": GOLDEN_DIGEST}
    ).code == (ServingSubjectValidationCode.EXTRA_FIELD)


@pytest.mark.parametrize(
    ("path", "replacement"),
    [
        (("model", "component_id"), "acme/model-y"),
        (("model", "revision"), "fedcba9876543210"),
        (("tokenizer", "component_id"), "acme/tokenizer-y"),
        (("tokenizer", "revision"), "0123456789abcdef"),
        (("engine", "engine_id"), "tgi"),
        (("engine", "engine_version"), "0.26.1"),
        (("runtime_artifact_digest",), "sha256:" + "c" * 64),
        (
            ("runtime_configuration_json",),
            '{"gpu_memory_utilization":91,"scheduler":{"max_num_seqs":8},"seed":42}',
        ),
        (("launch_arguments_digest",), "sha256:" + "c" * 64),
        (("hardware", "hardware_class"), "NVIDIA-H200-SXM5-141GB"),
        (("hardware", "topology"), "1x4"),
        (("profile", "profile_id"), "serving-profile-2"),
        (("profile", "profile_version"), "1.0.1+pin"),
        (("profile", "adapter_id"), "bench-adapter-2"),
        (("profile", "adapter_version"), "1.0.1+pin"),
        (("routing_policy_id",), "route-policy-2"),
        (("routing_policy_digest",), "sha256:" + "c" * 64),
    ],
)
def test_every_material_field_mutation_changes_identity(
    path: tuple[str, ...], replacement: object
) -> None:
    payload = deepcopy(_unsigned_payload())
    target: dict[str, object] = payload
    for key in path[:-1]:
        target = target[key]  # type: ignore[assignment,index]
    target[path[-1]] = replacement

    assert create_serving_subject_manifest(payload).subject_digest != GOLDEN_DIGEST


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("runtime_artifact_digest", None),
        ("routing_policy_id", None),
        ("routing_policy_digest", None),
    ],
)
def test_optional_material_presence_boundaries_are_explicit(
    field: str, value: object
) -> None:
    payload = _unsigned_payload()
    payload[field] = value

    if field == "runtime_artifact_digest":
        assert create_serving_subject_manifest(payload).subject_digest != GOLDEN_DIGEST
    else:
        assert (
            _parse_error(canonical_json_bytes({**_manifest_payload(), **payload})).code
            == ServingSubjectValidationCode.SEMANTIC_INCONSISTENCY
        )


def test_routing_pair_absent_is_a_distinct_valid_identity() -> None:
    payload = _unsigned_payload()
    payload["routing_policy_id"] = None
    payload["routing_policy_digest"] = None

    manifest = create_serving_subject_manifest(payload)

    assert manifest.subject_digest != GOLDEN_DIGEST
    assert verify_serving_subject_manifest(manifest)
    assert manifest.model_dump(mode="json")["routing_policy_id"] is None
    assert manifest.model_dump(mode="json")["routing_policy_digest"] is None


@pytest.mark.parametrize(
    "field",
    ("runtime_artifact_digest", "routing_policy_id", "routing_policy_digest"),
)
def test_optional_material_fields_must_be_present_even_when_null(field: str) -> None:
    complete = _manifest_payload()
    complete.pop(field)

    assert _parse_error(canonical_json_bytes(complete)).code == (
        ServingSubjectValidationCode.MISSING_FIELD
    )

    unsigned = _unsigned_payload()
    unsigned.pop(field)
    assert _create_error(unsigned).code == ServingSubjectValidationCode.MISSING_FIELD


def test_launch_argument_identity_is_digest_only_and_required() -> None:
    missing = _unsigned_payload()
    missing.pop("launch_arguments_digest")
    assert _create_error(missing).code == ServingSubjectValidationCode.MISSING_FIELD

    raw_arguments = _unsigned_payload()
    raw_arguments["launch_arguments"] = ["--private-path=/do-not-store"]
    assert _create_error(raw_arguments).code == ServingSubjectValidationCode.EXTRA_FIELD


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (
            lambda payload: payload.pop("engine"),
            ServingSubjectValidationCode.MISSING_FIELD,
        ),
        (
            lambda payload: payload.__setitem__("unexpected", "value"),
            ServingSubjectValidationCode.EXTRA_FIELD,
        ),
        (
            lambda payload: payload.__setitem__("hardware", None),
            ServingSubjectValidationCode.WRONG_TYPE,
        ),
        (
            lambda payload: payload["model"].__setitem__("model_id", "alias"),  # type: ignore[index,union-attr]
            ServingSubjectValidationCode.EXTRA_FIELD,
        ),
        (
            lambda payload: payload.__setitem__(
                "runtime_configuration_json", "{" + "x" * 8192 + "}"
            ),
            ServingSubjectValidationCode.OVERSIZED,
        ),
    ],
)
def test_missing_extra_wrong_type_alias_and_oversized_input_fail_closed(
    mutate: object, expected: ServingSubjectValidationCode
) -> None:
    payload = _manifest_payload()
    mutate(payload)  # type: ignore[operator]

    assert _parse_error(canonical_json_bytes(payload)).code == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("sha256:" + "A" * 64, ServingSubjectValidationCode.INVALID_DIGEST),
        ("SHA256:" + "a" * 64, ServingSubjectValidationCode.INVALID_DIGEST),
        ("a" * 64, ServingSubjectValidationCode.INVALID_DIGEST),
        ("sha256:" + "a" * 63, ServingSubjectValidationCode.INVALID_DIGEST),
        ("sha256:" + "a" * 64, ServingSubjectValidationCode.INVALID_DIGEST),
    ],
)
def test_digest_format_and_substitution_fail_closed(
    value: str, expected: ServingSubjectValidationCode
) -> None:
    payload = _manifest_payload()
    payload["subject_digest"] = value

    assert _parse_error(canonical_json_bytes(payload)).code == expected


def test_unsupported_schema_unpinned_revision_and_runtime_domain_fail_closed() -> None:
    payload = _manifest_payload()
    payload["schema_version"] = "exitspec.serving-subject-manifest.v2"
    assert _parse_error(canonical_json_bytes(payload)).code == (
        ServingSubjectValidationCode.WRONG_VERSION
    )

    unpinned = _unsigned_payload()
    unpinned["model"]["revision"] = "latest"  # type: ignore[index]
    with pytest.raises(ServingSubjectRejected) as raised:
        create_serving_subject_manifest(unpinned)
    assert raised.value.code == ServingSubjectValidationCode.INVALID_VALUE

    exact_engine = _unsigned_payload()
    exact_engine["engine"]["engine_version"] = "0.26.0"  # type: ignore[index]
    assert (
        create_serving_subject_manifest(exact_engine).engine.engine_version == "0.26.0"
    )

    for floating_label in ("latest", "main", "master", "head", "default", "stable"):
        floating_engine = _unsigned_payload()
        floating_engine["engine"]["engine_version"] = floating_label  # type: ignore[index]
        assert _create_error(floating_engine).code == (
            ServingSubjectValidationCode.INVALID_VALUE
        )

    for runtime_configuration_json in (
        '{"a":1,"a":2}',
        '{"b":1,"a":2}',
        '{"a":1.0}',
        '{"a":NaN}',
    ):
        invalid = _unsigned_payload()
        invalid["runtime_configuration_json"] = runtime_configuration_json
        with pytest.raises(ServingSubjectRejected):
            create_serving_subject_manifest(invalid)


@pytest.mark.parametrize(
    "runtime_configuration_json",
    (
        '{"nested":{"ApiKey":"value"}}',
        '{"nested":{"PRIVATE-key":"value"}}',
        '{"nested":{"provider.execution":true}}',
        '{"GPUReservation":{"count":1}}',
        '{"deploy":{"enabled":false}}',
        '{"traffic":1}',
        '{"authorizationMode":"none"}',
        '{"api":{"key":"DO-NOT-STORE"}}',
        '{"private":{"key":"DO-NOT-STORE"}}',
        '{"gpu":{"reservation":{"count":1}}}',
        '{"outer":{"api":{"key":"DO-NOT-STORE"}}}',
        '{"API":{"Key":"DO-NOT-STORE"}}',
        '{"safe-api":{"key":"DO-NOT-STORE"}}',
        '{"safe.api":{"key":"DO-NOT-STORE"}}',
        '{"safeApi":{"key":"DO-NOT-STORE"}}',
    ),
)
def test_runtime_configuration_rejects_prohibited_key_semantics_recursively(
    runtime_configuration_json: str,
) -> None:
    payload = _unsigned_payload()
    payload["runtime_configuration_json"] = runtime_configuration_json

    rejected = _create_error(payload)

    assert rejected.code == ServingSubjectValidationCode.INVALID_VALUE
    assert runtime_configuration_json not in str(rejected)


def test_runtime_configuration_allows_harmless_gpu_and_seed_keys() -> None:
    manifest = create_serving_subject_manifest(_unsigned_payload())

    assert manifest.runtime_configuration_json == (
        '{"gpu_memory_utilization":90,"scheduler":{"max_num_seqs":8},"seed":42}'
    )


def test_jcs_preserves_unicode_code_points_without_normalization() -> None:
    literal = _unsigned_payload()
    literal["runtime_configuration_json"] = '{"label":"é"}'
    literal_manifest = create_serving_subject_manifest(literal)

    escaped = _unsigned_payload()
    escaped["runtime_configuration_json"] = r'{"label":"\u00e9"}'
    assert _create_error(escaped).code == ServingSubjectValidationCode.INVALID_VALUE

    decomposed = _unsigned_payload()
    decomposed["runtime_configuration_json"] = '{"label":"e\u0301"}'
    decomposed_manifest = create_serving_subject_manifest(decomposed)
    assert decomposed_manifest.subject_digest != literal_manifest.subject_digest

    non_ascii_identifier = _unsigned_payload()
    non_ascii_identifier["model"]["component_id"] = "acmé/model"  # type: ignore[index]
    assert (
        _create_error(non_ascii_identifier).code
        == ServingSubjectValidationCode.INVALID_VALUE
    )


def test_raw_parser_rejects_duplicate_noncanonical_bad_unicode_and_float() -> None:
    duplicate = (
        b'{"schema_version":"exitspec.serving-subject-manifest.v1",'
        b'"schema_version":"exitspec.serving-subject-manifest.v1"}'
    )
    assert _parse_error(duplicate).code == ServingSubjectValidationCode.DUPLICATE_FIELD

    canonical = _golden_bytes()
    assert (
        _parse_error(b" " + canonical).code
        == ServingSubjectValidationCode.NON_CANONICAL
    )

    payload = json.loads(canonical)
    reordered = {"tokenizer": payload.pop("tokenizer"), **payload}
    assert _parse_error(json.dumps(reordered, separators=(",", ":")).encode()).code == (
        ServingSubjectValidationCode.NON_CANONICAL
    )

    floating = canonical[:-1] + b',"numeric_attack":1.0}'
    assert _parse_error(floating).code == ServingSubjectValidationCode.INVALID_VALUE

    malformed_unicode = canonical.replace(
        b'{\\"gpu_memory_utilization\\":90,\\"scheduler\\":{\\"max_num_seqs\\":8},\\"seed\\":42}',
        b'{\\"a\\":\\"\\ud800\\"}',
    )
    assert (
        _parse_error(malformed_unicode).code
        == ServingSubjectValidationCode.INVALID_VALUE
    )


def test_models_are_immutable_and_typed_verification_revalidates() -> None:
    manifest = parse_serving_subject_manifest(_golden_bytes())

    with pytest.raises(ValidationError):
        manifest.subject_digest = "sha256:" + "c" * 64  # type: ignore[misc]
    with pytest.raises(ValidationError):
        manifest.engine.engine_id = "tgi"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        manifest.launch_arguments_digest = "sha256:" + "c" * 64  # type: ignore[misc]

    substituted = manifest.model_copy(update={"subject_digest": "sha256:" + "c" * 64})
    assert not verify_serving_subject_manifest(substituted)
    with pytest.raises(ServingSubjectRejected) as raised:
        serialize_serving_subject_manifest(substituted)
    assert raised.value.code == ServingSubjectValidationCode.INVALID_DIGEST


def test_source_containers_cannot_mutate_constructed_identity() -> None:
    source = _unsigned_payload()
    manifest = create_serving_subject_manifest(source)
    original = serialize_serving_subject_manifest(manifest)

    source["engine"]["engine_id"] = "tgi"  # type: ignore[index]
    source["profile"]["adapter_id"] = "different-adapter"  # type: ignore[index]
    source["runtime_configuration_json"] = '{"seed":43}'

    assert manifest.engine.engine_id == "vllm"
    assert manifest.profile.adapter_id == "bench-adapter"
    assert serialize_serving_subject_manifest(manifest) == original


def test_public_boundaries_reject_model_copy_and_model_construct_bypasses() -> None:
    manifest = parse_serving_subject_manifest(_golden_bytes())
    substituted_digest = "sha256:" + "c" * 64
    copied = manifest.model_copy(update={"subject_digest": substituted_digest})
    constructed = ServingSubjectManifestV1.model_construct(
        schema_version=manifest.schema_version,
        model=manifest.model,
        tokenizer=manifest.tokenizer,
        engine=manifest.engine,
        runtime_artifact_digest=manifest.runtime_artifact_digest,
        runtime_configuration_json=manifest.runtime_configuration_json,
        launch_arguments_digest=manifest.launch_arguments_digest,
        hardware=manifest.hardware,
        profile=manifest.profile,
        routing_policy_id=manifest.routing_policy_id,
        routing_policy_digest=manifest.routing_policy_digest,
        subject_digest=substituted_digest,
    )

    for bypassed in (copied, constructed):
        assert not verify_serving_subject_manifest(bypassed)
        with pytest.raises(ServingSubjectRejected) as raised:
            serialize_serving_subject_manifest(bypassed)
        assert raised.value.code == ServingSubjectValidationCode.INVALID_DIGEST


def test_public_errors_do_not_echo_attacker_supplied_content() -> None:
    attack_value = "DO-NOT-ECHO-PRIVATE-CONTENT"
    payload = _unsigned_payload()
    payload["runtime_configuration_json"] = (
        '{"nested":{"Password":"' + attack_value + '"}}'
    )

    rejected = _create_error(payload)

    assert rejected.code == ServingSubjectValidationCode.INVALID_VALUE
    assert attack_value not in str(rejected)


def test_split_runtime_path_errors_do_not_echo_attacker_supplied_content() -> None:
    attack_value = "DO-NOT-ECHO-SPLIT-PATH-CONTENT"
    payload = _unsigned_payload()
    payload["runtime_configuration_json"] = '{"safeApi":{"key":"' + attack_value + '"}}'

    rejected = _create_error(payload)

    assert rejected.code == ServingSubjectValidationCode.INVALID_VALUE
    assert attack_value not in str(rejected)
