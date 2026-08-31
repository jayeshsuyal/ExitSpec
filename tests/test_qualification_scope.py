"""Adversarial contract tests for v0.5 qualification scope and context."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError

from exitspec.canonical import canonical_json_bytes
from exitspec.qualification_scope import (
    QUALIFICATION_CONTEXT_DIGEST_DOMAIN,
    QUALIFICATION_CONTEXT_SCHEMA_VERSION,
    QUALIFICATION_SCOPE_DIGEST_DOMAIN,
    QUALIFICATION_SCOPE_SCHEMA_VERSION,
    QualificationContextV1,
    QualificationScopeRejected,
    QualificationScopeV1,
    QualificationScopeValidationCode,
    canonical_qualification_context_projection,
    canonical_qualification_scope_projection,
    create_qualification_context,
    create_qualification_scope,
    parse_qualification_context,
    parse_qualification_scope,
    qualification_context_digest,
    qualification_scope_digest,
    serialize_qualification_context,
    serialize_qualification_scope,
    verify_qualification_context,
    verify_qualification_scope,
)
from exitspec.serving_subject import (
    create_serving_subject_manifest,
    parse_serving_subject_manifest,
    serialize_serving_subject_manifest,
)

ROOT = Path(__file__).resolve().parents[1]
GOLDEN_SUBJECT = ROOT / "tests" / "fixtures" / "serving_subject" / "v1" / "golden.json"
GOLDEN_SCOPE = (
    ROOT / "tests" / "fixtures" / "qualification_scope" / "v1" / "golden-scope.json"
)
GOLDEN_CONTEXT = (
    ROOT
    / "tests"
    / "fixtures"
    / "qualification_scope"
    / "v1"
    / "golden-context.json"
)
GOLDEN_SCOPE_DIGEST = (
    "sha256:5db651e8c2eae05147d2c5fc52bae0b4526ed84508f76d62d41471ac4ca677ab"
)
GOLDEN_CONTEXT_DIGEST = (
    "sha256:9159ac21169d0674b916053e6605a72f6f25e65cfe94b30b708a86f343d0193c"
)


def _subject():
    return parse_serving_subject_manifest(GOLDEN_SUBJECT.read_bytes())


def _scope_unsigned() -> dict[str, object]:
    return {
        "schema_version": QUALIFICATION_SCOPE_SCHEMA_VERSION,
        "frozen_contract": {
            "contract_id": "customer-acceptance-v1",
            "contract_canonical_digest": "sha256:" + "a" * 64,
        },
        "workload": {
            "workload_id": "chat-latency-suite-v1",
            "workload_digest": "sha256:" + "b" * 64,
        },
        "measurement_profile": {
            "environment_id": "lab-h100-sxm5",
            "environment_digest": "sha256:" + "c" * 64,
            "profile_id": "inference-performance-profile",
            "profile_version": "1.0.0",
            "profile_digest": "sha256:" + "d" * 64,
        },
        "evaluated_use": "CANARY_CONSIDERATION",
        "maximum_use": {
            "maximum_traffic_percent": 5,
        },
        "freshness_policy": {
            "age_basis": "EVIDENCE_CAPTURED_AT",
            "maximum_evidence_age_seconds": 86_400,
        },
        "reference_subject_requirement": "NOT_REQUIRED",
        "reference_subject_digest": None,
    }


def _scope() -> QualificationScopeV1:
    return create_qualification_scope(_scope_unsigned())


def _context() -> QualificationContextV1:
    return create_qualification_context(
        _subject(),
        _scope(),
        protocol_id="inference-performance-qualification",
        protocol_version="1.0.0",
    )


def _scope_payload() -> dict[str, object]:
    return _scope().model_dump(mode="json")


def _context_payload() -> dict[str, object]:
    return _context().model_dump(mode="json")


def _scope_parse_error(value: bytes | dict[str, object]) -> QualificationScopeRejected:
    with pytest.raises(QualificationScopeRejected) as raised:
        parse_qualification_scope(value)
    return raised.value


def _scope_create_error(value: dict[str, object]) -> QualificationScopeRejected:
    with pytest.raises(QualificationScopeRejected) as raised:
        create_qualification_scope(value)
    return raised.value


def _context_parse_error(value: bytes | dict[str, object]) -> QualificationScopeRejected:
    with pytest.raises(QualificationScopeRejected) as raised:
        parse_qualification_context(value)
    return raised.value


def test_golden_scope_and_context_vectors_are_exact_and_independently_derived() -> None:
    scope_raw = GOLDEN_SCOPE.read_bytes()
    scope_payload = json.loads(scope_raw)
    scope_digest = scope_payload.pop("scope_digest")
    expected_scope_digest = "sha256:" + hashlib.sha256(
        b"exitspec-qualification-scope-v1\x00" + canonical_json_bytes(scope_payload)
    ).hexdigest()

    context_raw = GOLDEN_CONTEXT.read_bytes()
    context_payload = json.loads(context_raw)
    context_digest = context_payload.pop("qualification_context_digest")
    expected_context_digest = "sha256:" + hashlib.sha256(
        b"exitspec-qualification-context-v1\x00"
        + canonical_json_bytes(context_payload)
    ).hexdigest()

    scope = parse_qualification_scope(scope_raw)
    context = parse_qualification_context(context_raw)

    assert scope_raw == canonical_json_bytes({**scope_payload, "scope_digest": scope_digest})
    assert context_raw == canonical_json_bytes(
        {**context_payload, "qualification_context_digest": context_digest}
    )
    assert scope_digest == GOLDEN_SCOPE_DIGEST == expected_scope_digest
    assert context_digest == GOLDEN_CONTEXT_DIGEST == expected_context_digest
    assert scope.scope_digest == GOLDEN_SCOPE_DIGEST
    assert context.qualification_context_digest == GOLDEN_CONTEXT_DIGEST
    assert context.schema_version == QUALIFICATION_CONTEXT_SCHEMA_VERSION
    assert qualification_scope_digest(scope) == GOLDEN_SCOPE_DIGEST
    assert qualification_context_digest(context) == GOLDEN_CONTEXT_DIGEST
    assert serialize_qualification_scope(scope) == scope_raw
    assert serialize_qualification_context(context) == context_raw
    assert verify_qualification_scope(scope)
    assert verify_qualification_context(context)
    assert QUALIFICATION_SCOPE_DIGEST_DOMAIN == b"exitspec-qualification-scope-v1\x00"
    assert (
        QUALIFICATION_CONTEXT_DIGEST_DOMAIN
        == b"exitspec-qualification-context-v1\x00"
    )


def test_create_exposes_one_unsigned_projection_for_each_artifact() -> None:
    scope = _scope()
    context = _context()

    scope_projection = canonical_qualification_scope_projection(scope)
    context_projection = canonical_qualification_context_projection(context)

    assert "scope_digest" not in scope_projection
    assert set(scope.model_dump(mode="json")) == {"scope_digest", *scope_projection}
    assert "qualification_context_digest" not in context_projection
    assert set(context.model_dump(mode="json")) == {
        "qualification_context_digest",
        *context_projection,
    }
    assert parse_qualification_scope(scope.model_dump(mode="json")) == scope
    assert parse_qualification_context(context.model_dump(mode="json")) == context
    assert _scope_create_error(
        {**_scope_unsigned(), "scope_digest": "sha256:" + "e" * 64}
    ).code == QualificationScopeValidationCode.EXTRA_FIELD


@pytest.mark.parametrize(
    ("path", "replacement"),
    [
        (("frozen_contract", "contract_id"), "customer-acceptance-v2"),
        (("frozen_contract", "contract_canonical_digest"), "sha256:" + "e" * 64),
        (("workload", "workload_id"), "chat-latency-suite-v2"),
        (("workload", "workload_digest"), "sha256:" + "e" * 64),
        (("measurement_profile", "environment_id"), "lab-h200-sxm5"),
        (("measurement_profile", "environment_digest"), "sha256:" + "e" * 64),
        (("measurement_profile", "profile_id"), "alternative-profile"),
        (("measurement_profile", "profile_version"), "1.0.1"),
        (("measurement_profile", "profile_digest"), "sha256:" + "e" * 64),
        (("maximum_use", "maximum_traffic_percent"), 4),
        (("freshness_policy", "maximum_evidence_age_seconds"), 43_200),
    ],
)
def test_every_mutable_material_scope_field_changes_scope_identity(
    path: tuple[str, ...], replacement: object
) -> None:
    payload = deepcopy(_scope_unsigned())
    target: dict[str, object] = payload
    for key in path[:-1]:
        target = target[key]  # type: ignore[assignment,index]
    target[path[-1]] = replacement

    assert create_qualification_scope(payload).scope_digest != _scope().scope_digest


def test_explicit_optional_presence_boundaries_change_identity_without_defaults() -> None:
    no_freshness = _scope_unsigned()
    no_freshness["freshness_policy"] = None
    no_freshness_scope = create_qualification_scope(no_freshness)

    reference = _scope_unsigned()
    reference["reference_subject_requirement"] = "REQUIRED"
    reference["reference_subject_digest"] = "sha256:" + "e" * 64
    reference_scope = create_qualification_scope(reference)

    assert no_freshness_scope.scope_digest != _scope().scope_digest
    assert reference_scope.scope_digest != _scope().scope_digest
    assert no_freshness_scope.freshness_policy is None
    assert reference_scope.reference_subject_digest == "sha256:" + "e" * 64

    another_reference = deepcopy(reference)
    another_reference["reference_subject_digest"] = "sha256:" + "f" * 64
    assert (
        create_qualification_scope(another_reference).scope_digest
        != reference_scope.scope_digest
    )

    for field in (
        "freshness_policy",
        "reference_subject_requirement",
        "reference_subject_digest",
    ):
        missing = _scope_unsigned()
        missing.pop(field)
        assert _scope_create_error(missing).code == (
            QualificationScopeValidationCode.MISSING_FIELD
        )


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (
            lambda payload: payload.pop("workload"),
            QualificationScopeValidationCode.MISSING_FIELD,
        ),
        (
            lambda payload: payload.__setitem__("unexpected", "value"),
            QualificationScopeValidationCode.EXTRA_FIELD,
        ),
        (
            lambda payload: payload.__setitem__("measurement_profile", None),
            QualificationScopeValidationCode.WRONG_TYPE,
        ),
        (
            lambda payload: payload["workload"].__setitem__("alias", "value"),  # type: ignore[index,union-attr]
            QualificationScopeValidationCode.EXTRA_FIELD,
        ),
        (
            lambda payload: payload["maximum_use"].__setitem__(  # type: ignore[index,union-attr]
                "maximum_traffic_percent", 6
            ),
            QualificationScopeValidationCode.INVALID_VALUE,
        ),
        (
            lambda payload: payload["maximum_use"].__setitem__(  # type: ignore[index,union-attr]
                "deployment_authorized", True
            ),
            QualificationScopeValidationCode.EXTRA_FIELD,
        ),
        (
            lambda payload: payload.__setitem__(
                "evaluated_use", "TRAFFIC_AUTHORIZATION"
            ),
            QualificationScopeValidationCode.INVALID_VALUE,
        ),
        (
            lambda payload: payload["freshness_policy"].__setitem__(  # type: ignore[index,union-attr]
                "age_basis", "ISSUED_AT"
            ),
            QualificationScopeValidationCode.INVALID_VALUE,
        ),
        (
            lambda payload: payload.__setitem__(
                "freshness_policy", {"maximum_evidence_age_seconds": 60}
            ),
            QualificationScopeValidationCode.MISSING_FIELD,
        ),
        (
            lambda payload: payload.__setitem__(
                "freshness_policy",
                {"age_basis": "ISSUED_AT", "maximum_evidence_age_seconds": 60},
            ),
            QualificationScopeValidationCode.INVALID_VALUE,
        ),
        (
            lambda payload: payload.__setitem__(
                "freshness_policy",
                {
                    "age_basis": "EVIDENCE_CAPTURED_AT",
                    "maximum_evidence_age_seconds": 60,
                    "expires_after_seconds": 120,
                },
            ),
            QualificationScopeValidationCode.EXTRA_FIELD,
        ),
        (
            lambda payload: payload.__setitem__(
                "reference_subject_requirement", "REQUIRED"
            ),
            QualificationScopeValidationCode.SEMANTIC_INCONSISTENCY,
        ),
    ],
)
def test_scope_missing_extra_wrong_type_authority_and_reference_boundaries_fail_closed(
    mutate: object, expected: QualificationScopeValidationCode
) -> None:
    payload = _scope_unsigned()
    mutate(payload)  # type: ignore[operator]

    assert _scope_create_error(payload).code == expected


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("scope_digest",), "sha256:" + "A" * 64),
        (("scope_digest",), "SHA256:" + "a" * 64),
        (("scope_digest",), "a" * 64),
        (("scope_digest",), "sha256:" + "a" * 63),
        (("scope_digest",), "sha256:" + "a" * 64),
        (("workload", "workload_digest"), "sha256:" + "A" * 64),
    ],
)
def test_scope_digest_format_and_substitution_fail_closed(
    path: tuple[str, ...], value: object
) -> None:
    payload = _scope_payload()
    target: dict[str, object] = payload
    for key in path[:-1]:
        target = target[key]  # type: ignore[assignment,index]
    target[path[-1]] = value

    assert _scope_parse_error(canonical_json_bytes(payload)).code == (
        QualificationScopeValidationCode.INVALID_DIGEST
    )


def test_scope_raw_parser_rejects_duplicate_noncanonical_float_and_oversized_input() -> None:
    duplicate = (
        b'{"schema_version":"exitspec.qualification-scope.v1",'
        b'"schema_version":"exitspec.qualification-scope.v1"}'
    )
    assert _scope_parse_error(duplicate).code == (
        QualificationScopeValidationCode.DUPLICATE_FIELD
    )

    canonical = serialize_qualification_scope(_scope())
    assert _scope_parse_error(b" " + canonical).code == (
        QualificationScopeValidationCode.NON_CANONICAL
    )
    payload = json.loads(canonical)
    reordered = {"workload": payload.pop("workload"), **payload}
    assert _scope_parse_error(json.dumps(reordered, separators=(",", ":")).encode()).code == (
        QualificationScopeValidationCode.NON_CANONICAL
    )
    assert _scope_parse_error(canonical[:-1] + b',"unexpected":1.0}').code == (
        QualificationScopeValidationCode.INVALID_VALUE
    )
    assert _scope_parse_error(b"{" + b"x" * (16 * 1024) + b"}").code == (
        QualificationScopeValidationCode.OVERSIZED
    )


def test_scope_contains_no_authorization_fields() -> None:
    payload = _scope().model_dump(mode="json")

    assert not {
        "deployment_authorized",
        "production_traffic_authorized",
        "traffic_expansion_authorized",
        "external_authorization_required",
    }.intersection(payload)
    assert set(payload["maximum_use"]) == {"maximum_traffic_percent"}


def test_unsupported_schema_floating_version_and_context_fields_fail_closed() -> None:
    scope = _scope_payload()
    scope["schema_version"] = "exitspec.qualification-scope.v2"
    assert _scope_parse_error(canonical_json_bytes(scope)).code == (
        QualificationScopeValidationCode.WRONG_VERSION
    )

    floating = _scope_unsigned()
    floating["measurement_profile"]["profile_version"] = "latest"  # type: ignore[index]
    assert _scope_create_error(floating).code == QualificationScopeValidationCode.INVALID_VALUE

    context = _context_payload()
    context["schema_version"] = "exitspec.qualification-context.v2"
    assert _context_parse_error(canonical_json_bytes(context)).code == (
        QualificationScopeValidationCode.WRONG_VERSION
    )
    context = _context_payload()
    context["protocol_version"] = "main"
    assert _context_parse_error(canonical_json_bytes(context)).code == (
        QualificationScopeValidationCode.INVALID_VALUE
    )
    context = _context_payload()
    context["unknown"] = "value"
    assert _context_parse_error(canonical_json_bytes(context)).code == (
        QualificationScopeValidationCode.EXTRA_FIELD
    )


def test_context_parser_rejects_missing_wrong_type_duplicate_noncanonical_and_oversized() -> None:
    context = _context_payload()
    context.pop("scope_digest")
    assert _context_parse_error(canonical_json_bytes(context)).code == (
        QualificationScopeValidationCode.MISSING_FIELD
    )

    context = _context_payload()
    context["protocol_id"] = 1
    assert _context_parse_error(canonical_json_bytes(context)).code == (
        QualificationScopeValidationCode.WRONG_TYPE
    )

    duplicate = (
        b'{"schema_version":"exitspec.qualification-context.v1",'
        b'"schema_version":"exitspec.qualification-context.v1"}'
    )
    assert _context_parse_error(duplicate).code == (
        QualificationScopeValidationCode.DUPLICATE_FIELD
    )
    canonical = serialize_qualification_context(_context())
    assert _context_parse_error(b" " + canonical).code == (
        QualificationScopeValidationCode.NON_CANONICAL
    )
    assert _context_parse_error(b"{" + b"x" * (4 * 1024) + b"}").code == (
        QualificationScopeValidationCode.OVERSIZED
    )


def test_subject_and_scope_drift_are_independent_and_both_change_context() -> None:
    subject = _subject()
    scope = _scope()
    original_context = create_qualification_context(
        subject,
        scope,
        protocol_id="inference-performance-qualification",
        protocol_version="1.0.0",
    )

    subject_payload = json.loads(serialize_serving_subject_manifest(subject))
    subject_payload["engine"]["engine_id"] = "tgi"
    subject_payload.pop("subject_digest")
    drifted_subject = create_serving_subject_manifest(subject_payload)
    subject_drift_context = create_qualification_context(
        drifted_subject,
        scope,
        protocol_id="inference-performance-qualification",
        protocol_version="1.0.0",
    )

    scope_payload = _scope_unsigned()
    scope_payload["workload"]["workload_digest"] = "sha256:" + "e" * 64  # type: ignore[index]
    drifted_scope = create_qualification_scope(scope_payload)
    scope_drift_context = create_qualification_context(
        subject,
        drifted_scope,
        protocol_id="inference-performance-qualification",
        protocol_version="1.0.0",
    )

    assert subject_drift_context.subject_digest != original_context.subject_digest
    assert subject_drift_context.scope_digest == original_context.scope_digest
    assert scope_drift_context.subject_digest == original_context.subject_digest
    assert scope_drift_context.scope_digest != original_context.scope_digest
    assert (
        subject_drift_context.qualification_context_digest
        != original_context.qualification_context_digest
    )
    assert (
        scope_drift_context.qualification_context_digest
        != original_context.qualification_context_digest
    )
    assert serialize_serving_subject_manifest(subject) == serialize_serving_subject_manifest(
        _subject()
    )


def test_context_protocol_change_and_digest_substitution_fail_closed() -> None:
    context = _context_payload()
    context["protocol_version"] = "1.0.1"
    context.pop("qualification_context_digest")
    changed = parse_qualification_context(
        canonical_json_bytes(
            {
                **context,
                "qualification_context_digest": "sha256:"
                + hashlib.sha256(
                    b"exitspec-qualification-context-v1\x00"
                    + canonical_json_bytes(context)
                ).hexdigest(),
            }
        )
    )
    assert changed.qualification_context_digest != _context().qualification_context_digest

    substituted = _context_payload()
    substituted["qualification_context_digest"] = "sha256:" + "a" * 64
    assert _context_parse_error(canonical_json_bytes(substituted)).code == (
        QualificationScopeValidationCode.INVALID_DIGEST
    )


def test_models_are_immutable_and_public_boundaries_reject_bypasses() -> None:
    scope = _scope()
    context = _context()

    with pytest.raises(ValidationError):
        scope.scope_digest = "sha256:" + "e" * 64  # type: ignore[misc]
    with pytest.raises(ValidationError):
        scope.workload.workload_id = "different"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        context.protocol_id = "different"  # type: ignore[misc]

    bad_scope_digest = "sha256:" + "e" * 64
    bad_context_digest = "sha256:" + "e" * 64
    bypasses = (
        (
            scope.model_copy(update={"scope_digest": bad_scope_digest}),
            QualificationScopeValidationCode.INVALID_DIGEST,
        ),
        (
            QualificationScopeV1.model_construct(
                **{**scope.model_dump(mode="json"), "scope_digest": bad_scope_digest}
            ),
            QualificationScopeValidationCode.INVALID_VALUE,
        ),
    )
    for bypassed, expected in bypasses:
        assert not verify_qualification_scope(bypassed)
        with pytest.raises(QualificationScopeRejected) as raised:
            serialize_qualification_scope(bypassed)
        assert raised.value.code == expected

    context_bypasses = (
        (
            context.model_copy(
                update={"qualification_context_digest": bad_context_digest}
            ),
            QualificationScopeValidationCode.INVALID_DIGEST,
        ),
        (
            QualificationContextV1.model_construct(
                **{
                    **context.model_dump(mode="json"),
                    "qualification_context_digest": bad_context_digest,
                }
            ),
            QualificationScopeValidationCode.INVALID_DIGEST,
        ),
    )
    for bypassed, expected in context_bypasses:
        assert not verify_qualification_context(bypassed)
        with pytest.raises(QualificationScopeRejected) as raised:
            serialize_qualification_context(bypassed)
        assert raised.value.code == expected


def test_source_containers_do_not_mutate_created_scope_or_context() -> None:
    source = _scope_unsigned()
    scope = create_qualification_scope(source)
    original_scope = serialize_qualification_scope(scope)
    source["workload"]["workload_id"] = "mutated"  # type: ignore[index]
    source["maximum_use"]["maximum_traffic_percent"] = 1  # type: ignore[index]

    context = create_qualification_context(
        _subject(),
        scope,
        protocol_id="inference-performance-qualification",
        protocol_version="1.0.0",
    )

    assert scope.workload.workload_id == "chat-latency-suite-v1"
    assert scope.maximum_use.maximum_traffic_percent == 5
    assert serialize_qualification_scope(scope) == original_scope
    assert verify_qualification_context(context)


def test_context_creation_rejects_unverified_subject_or_scope_bypasses() -> None:
    subject = _subject()
    scope = _scope()
    bad_subject = subject.model_copy(update={"subject_digest": "sha256:" + "e" * 64})
    bad_scope = scope.model_copy(update={"scope_digest": "sha256:" + "e" * 64})

    for invalid_subject, invalid_scope in ((bad_subject, scope), (subject, bad_scope)):
        with pytest.raises(QualificationScopeRejected) as raised:
            create_qualification_context(
                invalid_subject,
                invalid_scope,
                protocol_id="inference-performance-qualification",
                protocol_version="1.0.0",
            )
        assert raised.value.code == QualificationScopeValidationCode.INVALID_VALUE


def test_public_errors_do_not_echo_attacker_supplied_content() -> None:
    attack = "DO-NOT-ECHO-PRIVATE-SCOPE-CONTENT!"
    scope = _scope_unsigned()
    scope["frozen_contract"]["contract_id"] = attack  # type: ignore[index]

    rejected = _scope_create_error(scope)

    assert rejected.code == QualificationScopeValidationCode.INVALID_VALUE
    assert attack not in str(rejected)

    context = _context_payload()
    context["protocol_id"] = attack
    rejected = _context_parse_error(canonical_json_bytes(context))
    assert rejected.code == QualificationScopeValidationCode.INVALID_VALUE
    assert attack not in str(rejected)
