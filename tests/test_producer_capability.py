"""Adversarial contract checks for the package-owned PR4 capability registry."""

from __future__ import annotations

import ast
import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

import exitspec.producer_capability as producer_capability_module
from exitspec.canonical import canonical_json_bytes
from exitspec.producer_capability import (
    DECLARED_EXTERNAL_EVIDENCE_PROFILE_ID,
    DECLARED_EXTERNAL_EVIDENCE_PROFILE_VERSION,
    NATIVE_TTFT_METRIC_DEFINITION_ID,
    PRODUCER_CAPABILITY_DESCRIPTOR_SCHEMA_VERSION,
    PRODUCER_CAPABILITY_DIGEST_DOMAIN,
    PRODUCER_CAPABILITY_REQUEST_SCHEMA_VERSION,
    NativeTTFTObservationV1,
    ProducerCapabilityDescriptorV1,
    ProducerCapabilityRejected,
    ProducerCapabilityValidationCode,
    canonical_producer_capability_projection,
    get_producer_capability_descriptor,
    parse_producer_capability_descriptor,
    parse_producer_capability_request,
    producer_capability_digest,
    resolve_producer_capability_request,
    serialize_producer_capability_descriptor,
    verify_producer_capability_descriptor,
)

FIXTURE = (
    Path(__file__).parent / "fixtures" / "producer_capability" / "v1" / "golden.json"
)
GOLDEN_DIGEST = "sha256:1b8732d26a94dadfab984b43a4c67c1fc858ddf39f95ec496f5914f1c08e066b"


def _descriptor() -> ProducerCapabilityDescriptorV1:
    return get_producer_capability_descriptor(
        profile_id=DECLARED_EXTERNAL_EVIDENCE_PROFILE_ID,
        profile_version=DECLARED_EXTERNAL_EVIDENCE_PROFILE_VERSION,
    )


def _request() -> dict[str, str]:
    return {
        "schema_version": PRODUCER_CAPABILITY_REQUEST_SCHEMA_VERSION,
        "profile_id": DECLARED_EXTERNAL_EVIDENCE_PROFILE_ID,
        "profile_version": DECLARED_EXTERNAL_EVIDENCE_PROFILE_VERSION,
    }


def _payload() -> dict[str, Any]:
    return json.loads(FIXTURE.read_bytes())


def _projection_digest(projection: dict[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(
        PRODUCER_CAPABILITY_DIGEST_DOMAIN + canonical_json_bytes(projection)
    ).hexdigest()


def _set_path(value: dict[str, Any], path: tuple[str, ...], replacement: Any) -> None:
    current: dict[str, Any] = value
    for part in path[:-1]:
        current = current[part]
    current[path[-1]] = replacement


def _assert_rejected(
    action: object,
    code: ProducerCapabilityValidationCode,
) -> ProducerCapabilityRejected:
    with pytest.raises(ProducerCapabilityRejected) as raised:
        assert callable(action)
        action()
    assert raised.value.code is code
    return raised.value


def _descriptor_node(
    descriptor: ProducerCapabilityDescriptorV1, node_name: str
) -> object:
    return {
        "profile": descriptor.profile,
        "engine_adapter": descriptor.engine_adapter,
        "available_observations": descriptor.available_observations,
        "native_ttft": descriptor.available_observations.native_ttft,
        "measured_attempt_reliability": (
            descriptor.available_observations.measured_attempt_reliability
        ),
    }[node_name]


def _assert_typed_descriptor_boundary_rejection(
    descriptor: ProducerCapabilityDescriptorV1,
    code: ProducerCapabilityValidationCode,
    attack_value: str,
) -> None:
    assert not verify_producer_capability_descriptor(descriptor)
    for action in (
        lambda: canonical_producer_capability_projection(descriptor),
        lambda: producer_capability_digest(descriptor),
        lambda: serialize_producer_capability_descriptor(descriptor),
    ):
        error = _assert_rejected(action, code)
        assert attack_value not in str(error)


def test_registered_descriptor_declares_only_the_frozen_native_observations():
    descriptor = _descriptor()

    assert descriptor.schema_version == PRODUCER_CAPABILITY_DESCRIPTOR_SCHEMA_VERSION
    assert descriptor.profile.profile_id == DECLARED_EXTERNAL_EVIDENCE_PROFILE_ID
    assert descriptor.profile.profile_version == "v1"
    assert descriptor.engine_adapter.engine_id == "vllm"
    assert descriptor.engine_adapter.engine_version == "0.26.0"
    assert descriptor.engine_adapter.adapter_id == "vllm_bench_serve"
    assert descriptor.available_observations.native_ttft.metric_definition_id == (
        NATIVE_TTFT_METRIC_DEFINITION_ID
    )
    assert descriptor.available_observations.native_ttft.unit == "ns"
    assert descriptor.available_observations.native_ttft.population == (
        "successful_measured_requests_with_observed_ttft"
    )
    assert descriptor.available_observations.native_ttft.reducer_id == "nearest_rank_v1"
    assert descriptor.available_observations.native_ttft.supported_percentile == "p95"
    assert (
        descriptor.available_observations.measured_attempt_reliability
        .source_field
        == "request.outcome.status"
    )
    assert (
        descriptor.available_observations.measured_attempt_reliability
        .reliability_numerator
        == "failed_or_anomalous_native_measured_requests"
    )
    assert (
        descriptor.available_observations.measured_attempt_reliability
        .reliability_denominator
        == "all_measured_requests"
    )
    assert "first_nonempty_choices_delta_content_v1" not in (
        serialize_producer_capability_descriptor(descriptor).decode("utf-8")
    )


def test_registry_module_stays_package_owned_and_provider_neutral():
    source = Path(producer_capability_module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
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

    assert "Inferdrome" not in source
    assert not any("inferdrome" in name.casefold() for name in imported_modules)


def test_checked_in_golden_raw_bytes_are_canonical_and_independently_hashed():
    raw = FIXTURE.read_bytes()
    descriptor = _descriptor()
    payload = json.loads(raw)

    assert raw == serialize_producer_capability_descriptor(descriptor)
    assert raw == canonical_json_bytes(payload)
    assert not raw.endswith(b"\n")
    unsigned = {key: value for key, value in payload.items() if key != "capability_digest"}
    independently_derived = "sha256:" + hashlib.sha256(
        b"exitspec-producer-capability-descriptor-v1\x00"
        + canonical_json_bytes(unsigned)
    ).hexdigest()
    assert independently_derived == GOLDEN_DIGEST
    assert descriptor.capability_digest == GOLDEN_DIGEST
    assert parse_producer_capability_descriptor(raw) == descriptor


@pytest.mark.parametrize(
    ("path", "replacement"),
    [
        (("schema_version",), "exitspec.producer-capability-descriptor.v2"),
        (("registry_version",), "exitspec.producer-capability-registry.v2"),
        (("profile", "profile_id"), "exitspec.external-evidence.other.v1"),
        (("profile", "profile_version"), "v2"),
        (("engine_adapter", "engine_id"), "other-engine"),
        (("engine_adapter", "engine_version"), "0.27.0"),
        (("engine_adapter", "adapter_id"), "other-adapter"),
        (("engine_adapter", "adapter_version"), "2.0.0"),
        (("available_observations", "native_ttft", "observation_id"), "other"),
        (
            ("available_observations", "native_ttft", "metric_definition_id"),
            "other_metric",
        ),
        (("available_observations", "native_ttft", "source_field"), "other.field"),
        (("available_observations", "native_ttft", "unit"), "ms"),
        (("available_observations", "native_ttft", "population"), "other_population"),
        (("available_observations", "native_ttft", "reducer_id"), "other_reducer"),
        (("available_observations", "native_ttft", "supported_percentile"), "p99"),
        (
            (
                "available_observations",
                "measured_attempt_reliability",
                "observation_id",
            ),
            "other",
        ),
        (
            (
                "available_observations",
                "measured_attempt_reliability",
                "source_field",
            ),
            "other.outcome",
        ),
        (
            (
                "available_observations",
                "measured_attempt_reliability",
                "latency_population",
            ),
            "other_population",
        ),
        (
            (
                "available_observations",
                "measured_attempt_reliability",
                "reliability_numerator",
            ),
            "other_numerator",
        ),
        (
            (
                "available_observations",
                "measured_attempt_reliability",
                "reliability_denominator",
            ),
            "other_denominator",
        ),
    ],
)
def test_every_material_unsigned_leaf_mutation_changes_capability_digest(
    path: tuple[str, ...], replacement: Any
):
    payload = _payload()
    unsigned = {key: value for key, value in payload.items() if key != "capability_digest"}
    changed = copy.deepcopy(unsigned)
    _set_path(changed, path, replacement)

    assert _projection_digest(changed) != _descriptor().capability_digest


def test_registered_descriptor_is_deeply_immutable_and_not_shared():
    request = _request()
    first = resolve_producer_capability_request(request)
    request["profile_version"] = "v2"
    second = _descriptor()

    assert first is not second
    assert first.profile is not second.profile
    assert first.profile.profile_version == "v1"
    with pytest.raises(ValidationError):
        first.available_observations.native_ttft.unit = "ms"
    object.__setattr__(first.profile, "profile_version", "v2")
    assert not verify_producer_capability_descriptor(first)
    assert verify_producer_capability_descriptor(second)


def test_public_descriptor_boundaries_revalidate_model_copy_and_construct_bypasses():
    descriptor = _descriptor()
    copied = descriptor.model_copy(update={"capability_digest": "sha256:" + "a" * 64})
    constructed = ProducerCapabilityDescriptorV1.model_construct(
        **descriptor.model_dump(mode="python")
    )
    object.__getattribute__(constructed, "__dict__")["undeclared"] = "ATTACK_VALUE"

    assert not verify_producer_capability_descriptor(copied)
    _assert_rejected(
        lambda: serialize_producer_capability_descriptor(copied),
        ProducerCapabilityValidationCode.INVALID_DIGEST,
    )
    assert not verify_producer_capability_descriptor(constructed)
    error = _assert_rejected(
        lambda: serialize_producer_capability_descriptor(constructed),
        ProducerCapabilityValidationCode.EXTRA_FIELD,
    )
    assert "ATTACK_VALUE" not in str(error)


@pytest.mark.parametrize(
    "node_name",
    (
        "profile",
        "engine_adapter",
        "available_observations",
        "native_ttft",
        "measured_attempt_reliability",
    ),
)
def test_nested_hidden_raw_state_is_rejected_before_any_lossy_projection(
    node_name: str,
):
    descriptor = _descriptor()
    target = _descriptor_node(descriptor, node_name)
    attack_value = "NESTED_RAW_ATTACK_VALUE"
    object.__getattribute__(target, "__dict__")["semantic_ttft"] = attack_value

    assert object.__getattribute__(target, "semantic_ttft") == attack_value
    _assert_typed_descriptor_boundary_rejection(
        descriptor,
        ProducerCapabilityValidationCode.EXTRA_FIELD,
        attack_value,
    )


@pytest.mark.parametrize(
    "node_name",
    (
        "profile",
        "engine_adapter",
        "available_observations",
        "native_ttft",
        "measured_attempt_reliability",
    ),
)
def test_nested_pydantic_extra_state_is_rejected_before_any_lossy_projection(
    node_name: str,
):
    descriptor = _descriptor()
    target = _descriptor_node(descriptor, node_name)
    attack_value = "NESTED_EXTRA_ATTACK_VALUE"
    object.__setattr__(target, "__pydantic_extra__", {"semantic_ttft": attack_value})

    _assert_typed_descriptor_boundary_rejection(
        descriptor,
        ProducerCapabilityValidationCode.EXTRA_FIELD,
        attack_value,
    )


def test_nested_exact_field_subclass_is_rejected_before_any_lossy_projection():
    class NativeTTFTSubclass(NativeTTFTObservationV1):
        @property
        def semantic_ttft(self) -> str:
            return "NESTED_SUBCLASS_ATTACK_VALUE"

    descriptor = _descriptor()
    forged_native = NativeTTFTSubclass(
        **descriptor.available_observations.native_ttft.model_dump(mode="python")
    )
    forged_observations = descriptor.available_observations.model_copy(
        update={"native_ttft": forged_native}
    )
    forged_descriptor = descriptor.model_copy(
        update={"available_observations": forged_observations}
    )

    assert forged_native.semantic_ttft == "NESTED_SUBCLASS_ATTACK_VALUE"
    _assert_typed_descriptor_boundary_rejection(
        forged_descriptor,
        ProducerCapabilityValidationCode.WRONG_TYPE,
        "NESTED_SUBCLASS_ATTACK_VALUE",
    )


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        (
            {
                **_request(),
                "source_text": {
                    "metric_definition_id": "first_nonempty_choices_delta_content_v1"
                },
            },
            ProducerCapabilityValidationCode.EXTRA_FIELD,
        ),
        (
            {
                **_request(),
                "provider_output": {
                    "metric_definition_id": "first_nonempty_choices_delta_content_v1"
                },
            },
            ProducerCapabilityValidationCode.EXTRA_FIELD,
        ),
        (
            {
                **_request(),
                "browser_payload": {
                    "metric_definition_id": "first_nonempty_choices_delta_content_v1"
                },
            },
            ProducerCapabilityValidationCode.EXTRA_FIELD,
        ),
        (
            {
                **_request(),
                "available_observations": {
                    "semantic_ttft": "first_nonempty_choices_delta_content_v1"
                },
            },
            ProducerCapabilityValidationCode.EXTRA_FIELD,
        ),
    ],
)
def test_untrusted_input_cannot_add_or_broaden_profile_capability(
    payload: dict[str, Any], code: ProducerCapabilityValidationCode
):
    error = _assert_rejected(
        lambda: resolve_producer_capability_request(payload),
        code,
    )
    assert "first_nonempty_choices_delta_content_v1" not in str(error)


def test_descriptor_extra_semantic_field_fails_before_any_capability_use():
    payload = _payload()
    payload["available_observations"]["semantic_ttft"] = (
        "first_nonempty_choices_delta_content_v1"
    )

    error = _assert_rejected(
        lambda: parse_producer_capability_descriptor(payload),
        ProducerCapabilityValidationCode.EXTRA_FIELD,
    )
    assert "first_nonempty_choices_delta_content_v1" not in str(error)


@pytest.mark.parametrize(
    ("value", "code"),
    [
        (
            {
                "schema_version": PRODUCER_CAPABILITY_REQUEST_SCHEMA_VERSION,
                "profile_id": "unknown.profile.v1",
                "profile_version": "v1",
            },
            ProducerCapabilityValidationCode.UNKNOWN_PROFILE,
        ),
        (
            {
                "schema_version": PRODUCER_CAPABILITY_REQUEST_SCHEMA_VERSION,
                "profile_id": DECLARED_EXTERNAL_EVIDENCE_PROFILE_ID.upper(),
                "profile_version": "v1",
            },
            ProducerCapabilityValidationCode.UNKNOWN_PROFILE,
        ),
        (
            {
                "schema_version": PRODUCER_CAPABILITY_REQUEST_SCHEMA_VERSION,
                "profile_id": DECLARED_EXTERNAL_EVIDENCE_PROFILE_ID,
                "profile_version": "v2",
            },
            ProducerCapabilityValidationCode.UNKNOWN_PROFILE,
        ),
        (
            {
                "schema_version": PRODUCER_CAPABILITY_REQUEST_SCHEMA_VERSION,
                "profile_id": DECLARED_EXTERNAL_EVIDENCE_PROFILE_ID,
                "profile_version": "latest",
            },
            ProducerCapabilityValidationCode.UNSUPPORTED_PROFILE_VERSION,
        ),
        (
            {
                "schema_version": "exitspec.producer-capability-request.v2",
                "profile_id": DECLARED_EXTERNAL_EVIDENCE_PROFILE_ID,
                "profile_version": "v1",
            },
            ProducerCapabilityValidationCode.WRONG_VERSION,
        ),
    ],
)
def test_unknown_aliased_or_unsupported_profile_requests_fail_closed(
    value: dict[str, str], code: ProducerCapabilityValidationCode
):
    _assert_rejected(lambda: parse_producer_capability_request(value), code)


def test_request_raw_bytes_reject_duplicate_extra_noncanonical_and_bad_json():
    duplicate = (
        b'{"schema_version":"exitspec.producer-capability-request.v1",'
        b'"profile_id":"exitspec.external-evidence.native-ttft-profile.v1",'
        b'"profile_id":"other","profile_version":"v1"}'
    )
    extra = {**_request(), "override": "PRIVATE_OVERRIDE_VALUE"}
    reordered = (
        b'{"profile_version":"v1","profile_id":'
        b'"exitspec.external-evidence.native-ttft-profile.v1",'
        b'"schema_version":"exitspec.producer-capability-request.v1"}'
    )

    _assert_rejected(
        lambda: parse_producer_capability_request(duplicate),
        ProducerCapabilityValidationCode.DUPLICATE_FIELD,
    )
    error = _assert_rejected(
        lambda: parse_producer_capability_request(extra),
        ProducerCapabilityValidationCode.EXTRA_FIELD,
    )
    assert "PRIVATE_OVERRIDE_VALUE" not in str(error)
    _assert_rejected(
        lambda: parse_producer_capability_request(reordered),
        ProducerCapabilityValidationCode.NON_CANONICAL,
    )
    _assert_rejected(
        lambda: parse_producer_capability_request(b"[]"),
        ProducerCapabilityValidationCode.WRONG_TYPE,
    )
    _assert_rejected(
        lambda: parse_producer_capability_request(b"x" * 4096),
        ProducerCapabilityValidationCode.OVERSIZED,
    )
    _assert_rejected(
        lambda: parse_producer_capability_request(b'{"profile_id":1.0}'),
        ProducerCapabilityValidationCode.INVALID_VALUE,
    )


def _deep_mapping() -> dict[str, Any]:
    nested: dict[str, Any] = {}
    current = nested
    for _ in range(14):
        child: dict[str, Any] = {}
        current["nested"] = child
        current = child
    return nested


def _cyclic_request() -> dict[str, Any]:
    value: dict[str, Any] = _request()
    value["profile_id"] = value
    return value


def _cyclic_descriptor() -> dict[str, Any]:
    value = _payload()
    value["profile"] = value
    return value


@pytest.mark.parametrize(
    ("request_value", "code"),
    [
        (
            _cyclic_request,
            ProducerCapabilityValidationCode.INVALID_VALUE,
        ),
        (
            lambda: {**_request(), "profile_id": _deep_mapping()},
            ProducerCapabilityValidationCode.OVERSIZED,
        ),
        (
            lambda: {**_request(), "profile_id": "x" * 513},
            ProducerCapabilityValidationCode.OVERSIZED,
        ),
        (
            lambda: {**_request(), "profile_id": list(range(17))},
            ProducerCapabilityValidationCode.OVERSIZED,
        ),
        (
            lambda: {**_request(), "untrusted_container": {str(index): index for index in range(25)}},
            ProducerCapabilityValidationCode.OVERSIZED,
        ),
    ],
)
def test_request_mapping_boundary_rejects_cycles_and_nested_limits_before_canonicalization(
    request_value: object,
    code: ProducerCapabilityValidationCode,
):
    assert callable(request_value)
    error = _assert_rejected(
        lambda: parse_producer_capability_request(request_value()),
        code,
    )
    assert "RecursionError" not in str(error)


@pytest.mark.parametrize(
    ("descriptor_value", "code"),
    [
        (
            _cyclic_descriptor,
            ProducerCapabilityValidationCode.INVALID_VALUE,
        ),
        (
            lambda: {
                **_payload(),
                "profile": _deep_mapping(),
            },
            ProducerCapabilityValidationCode.OVERSIZED,
        ),
        (
            lambda: {
                **_payload(),
                "profile": "PRIVATE_DESCRIPTOR_STRING" * 32,
            },
            ProducerCapabilityValidationCode.OVERSIZED,
        ),
        (
            lambda: {
                **_payload(),
                "profile": list(range(17)),
            },
            ProducerCapabilityValidationCode.OVERSIZED,
        ),
    ],
)
def test_descriptor_mapping_boundary_rejects_cycles_and_nested_limits_before_canonicalization(
    descriptor_value: object,
    code: ProducerCapabilityValidationCode,
):
    assert callable(descriptor_value)
    error = _assert_rejected(
        lambda: parse_producer_capability_descriptor(descriptor_value()),
        code,
    )
    assert "PRIVATE_DESCRIPTOR_STRING" not in str(error)
    assert "RecursionError" not in str(error)


@pytest.mark.parametrize(
    "bad_digest",
    [
        "sha256:" + "A" * 64,
        "sha256:" + "a" * 63,
        "a" * 64,
    ],
)
def test_descriptor_digest_substitution_and_format_variants_fail_closed(
    bad_digest: str,
):
    payload = _payload()
    payload["capability_digest"] = bad_digest

    _assert_rejected(
        lambda: parse_producer_capability_descriptor(payload),
        ProducerCapabilityValidationCode.INVALID_DIGEST,
    )


@pytest.mark.parametrize(
    ("path", "replacement"),
    [
        (
            ("available_observations", "native_ttft", "metric_definition_id"),
            "first_nonempty_choices_delta_content_v1",
        ),
        (("available_observations", "native_ttft", "unit"), "ms"),
        (("available_observations", "native_ttft", "reducer_id"), "alternate_reducer_v1"),
        (
            (
                "available_observations",
                "measured_attempt_reliability",
                "source_field",
            ),
            "request.outcome.alternate",
        ),
        (
            (
                "available_observations",
                "measured_attempt_reliability",
                "reliability_numerator",
            ),
            "alternate_reliability_numerator",
        ),
    ],
)
def test_self_consistent_capability_replacements_fail_before_capability_use(
    path: tuple[str, ...], replacement: str
):
    payload = _payload()
    _set_path(payload, path, replacement)
    unsigned = {key: value for key, value in payload.items() if key != "capability_digest"}
    payload["capability_digest"] = _projection_digest(unsigned)

    error = _assert_rejected(
        lambda: parse_producer_capability_descriptor(payload),
        ProducerCapabilityValidationCode.INVALID_VALUE,
    )
    assert replacement not in str(error)


def test_descriptor_raw_bytes_reject_duplicate_missing_extra_noncanonical_and_oversized():
    raw = FIXTURE.read_bytes()
    payload = _payload()
    duplicate = raw.replace(
        b'"schema_version":"exitspec.producer-capability-descriptor.v1"',
        b'"schema_version":"exitspec.producer-capability-descriptor.v1",'
        b'"schema_version":"exitspec.producer-capability-descriptor.v1"',
    )
    missing = copy.deepcopy(payload)
    del missing["engine_adapter"]
    extra = copy.deepcopy(payload)
    extra["override"] = "PRIVATE_DESCRIPTOR_OVERRIDE"
    reordered = json.dumps(
        dict(reversed(list(payload.items()))),
        separators=(",", ":"),
    ).encode("utf-8")

    _assert_rejected(
        lambda: parse_producer_capability_descriptor(duplicate),
        ProducerCapabilityValidationCode.DUPLICATE_FIELD,
    )
    _assert_rejected(
        lambda: parse_producer_capability_descriptor(missing),
        ProducerCapabilityValidationCode.MISSING_FIELD,
    )
    error = _assert_rejected(
        lambda: parse_producer_capability_descriptor(extra),
        ProducerCapabilityValidationCode.EXTRA_FIELD,
    )
    assert "PRIVATE_DESCRIPTOR_OVERRIDE" not in str(error)
    _assert_rejected(
        lambda: parse_producer_capability_descriptor(raw + b" "),
        ProducerCapabilityValidationCode.NON_CANONICAL,
    )
    _assert_rejected(
        lambda: parse_producer_capability_descriptor(reordered),
        ProducerCapabilityValidationCode.NON_CANONICAL,
    )
    _assert_rejected(
        lambda: parse_producer_capability_descriptor(b"x" * 9000),
        ProducerCapabilityValidationCode.OVERSIZED,
    )


def test_canonical_projection_digest_and_round_trip_require_registered_descriptor():
    descriptor = _descriptor()

    assert canonical_producer_capability_projection(descriptor) == {
        key: value for key, value in _payload().items() if key != "capability_digest"
    }
    assert producer_capability_digest(descriptor) == GOLDEN_DIGEST
    assert verify_producer_capability_descriptor(descriptor)
    assert parse_producer_capability_descriptor(
        serialize_producer_capability_descriptor(descriptor)
    ) == descriptor
