"""Adversarial tests for the pure v0.5 PR7 prospective handoff boundary."""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

import pytest

import exitspec.prospective_handoff as handoff_module
from exitspec.canonical import canonical_json_bytes
from exitspec.producer_capability import get_producer_capability_descriptor
from exitspec.proofability import (
    OverallProofabilityDisposition,
    evaluate_proofability,
)
from exitspec.proofability_workspace_fixture import (
    PRODUCTION_FIXTURE_AUTHORITIES,
    PROFILE_ID,
    PROFILE_VERSION,
)
from exitspec.prospective_handoff import (
    PROSPECTIVE_HANDOFF_DIGEST_DOMAIN,
    ProspectiveHandoffRejected,
    ProspectiveHandoffV1,
    ProspectiveHandoffValidationCode,
    canonical_prospective_handoff_projection,
    create_prospective_handoff,
    parse_prospective_handoff,
    prospective_handoff_digest,
    serialize_prospective_handoff,
    verify_prospective_handoff,
)


def _inputs():
    authority = PRODUCTION_FIXTURE_AUTHORITIES[0]
    descriptor = get_producer_capability_descriptor(
        profile_id=PROFILE_ID,
        profile_version=PROFILE_VERSION,
    )
    report = evaluate_proofability(
        authority.subject,
        authority.scope,
        authority.context,
        authority.contract,
        descriptor,
    )
    assert report.overall_disposition is OverallProofabilityDisposition.PROVABLE
    return (
        authority.subject,
        authority.scope,
        authority.context,
        authority.contract,
        descriptor,
        report,
    )


def _handoff():
    return create_prospective_handoff(*_inputs())


def _assert_rejected(action: object, code: ProspectiveHandoffValidationCode) -> None:
    with pytest.raises(ProspectiveHandoffRejected) as raised:
        assert callable(action)
        action()
    assert raised.value.code is code


def _rehash(payload: dict[str, object]) -> bytes:
    unsigned = {
        key: value
        for key, value in payload.items()
        if key != "prospective_handoff_digest"
    }
    payload["prospective_handoff_digest"] = "sha256:" + hashlib.sha256(
        PROSPECTIVE_HANDOFF_DIGEST_DOMAIN + canonical_json_bytes(unsigned)
    ).hexdigest()
    return canonical_json_bytes(payload)


def _field_names(value: object) -> set[str]:
    if type(value) is dict:
        return set(value).union(
            *( _field_names(child) for child in value.values())
        )
    if type(value) is list:
        return set().union(*(_field_names(child) for child in value))
    return set()


def test_canonical_handoff_binds_only_prospective_requirements():
    handoff = _handoff()
    raw = serialize_prospective_handoff(handoff)
    payload = json.loads(raw)

    assert raw == canonical_json_bytes(payload)
    assert not raw.endswith(b"\n")
    assert parse_prospective_handoff(raw) == handoff
    assert prospective_handoff_digest(handoff) == handoff.prospective_handoff_digest
    assert verify_prospective_handoff(handoff, *_inputs())
    assert tuple(item.criterion_id for item in handoff.requirements) == (
        "QUAL-TTFT-01",
    )
    assert tuple(
        (observation.observation_kind, observation.observation_id)
        for observation in handoff.requirements[0].required_observations
    ) == (
        ("MEASURED_ATTEMPT_RELIABILITY", "native_measured_request_outcome"),
        ("NATIVE_TTFT", "native_ttft_sample"),
    )
    forbidden = {
        "authority",
        "bundle",
        "credential",
        "dispatch",
        "evidence",
        "execution",
        "measurement",
        "provider",
        "request_plan",
        "run",
        "verdict",
    }
    # ``measurement_profile_*`` names an identity from the frozen scope; it
    # does not contain an observed measurement. The remaining forbidden terms
    # must not appear in any emitted field name.
    serialized_keys = _field_names(payload)
    assert not any(
        term in key.casefold()
        for key in serialized_keys
        for term in forbidden - {"measurement"}
    )


@pytest.mark.parametrize(
    ("path", "replacement"),
    [
        (("subject_digest",), "sha256:" + "a" * 64),
        (("scope_digest",), "sha256:" + "b" * 64),
        (("qualification_context_digest",), "sha256:" + "c" * 64),
        (("contract_id",), "other-frozen-contract"),
        (("contract_canonical_digest",), "sha256:" + "d" * 64),
        (("workload_id",), "other-workload"),
        (("workload_digest",), "sha256:" + "e" * 64),
        (("measurement_profile_id",), "other-measurement-profile"),
        (("measurement_profile_digest",), "sha256:" + "f" * 64),
        (("capability_profile_id",), "other-capability-profile"),
        (("capability_digest",), "sha256:" + "1" * 64),
        (("requirements", 0, "criterion_id"), "QUAL-TTFT-02"),
    ],
)
def test_self_consistent_mutations_never_verify_against_original_bindings(
    path: tuple[object, ...], replacement: object
):
    payload = json.loads(serialize_prospective_handoff(_handoff()))
    current: object = payload
    for key in path[:-1]:
        current = current[key]  # type: ignore[index]
    current[path[-1]] = replacement  # type: ignore[index]

    changed = parse_prospective_handoff(_rehash(payload))
    assert not verify_prospective_handoff(changed, *_inputs())


@pytest.mark.parametrize(
    "forbidden_field",
    (
        "authority",
        "credential",
        "evidence_bundle",
        "execution_request",
        "measurement",
        "provider_configuration",
        "request_plan_digest",
        "run_id",
        "verdict",
    ),
)
def test_forbidden_runtime_or_outcome_fields_are_rejected(forbidden_field: str):
    payload = json.loads(serialize_prospective_handoff(_handoff()))
    payload[forbidden_field] = "untrusted"
    _assert_rejected(
        lambda: parse_prospective_handoff(_rehash(payload)),
        ProspectiveHandoffValidationCode.EXTRA_FIELD,
    )


@pytest.mark.parametrize(
    ("field", "replacement"),
    (
        ("measurement_profile_version", "latest"),
        ("capability_profile_version", "main"),
    ),
)
def test_unpinned_profile_versions_fail_closed(field: str, replacement: str):
    payload = json.loads(serialize_prospective_handoff(_handoff()))
    payload[field] = replacement
    _assert_rejected(
        lambda: parse_prospective_handoff(_rehash(payload)),
        ProspectiveHandoffValidationCode.INVALID_VALUE,
    )


def test_parsing_is_canonical_and_typed_boundaries_reject_hostile_state():
    handoff = _handoff()
    raw = serialize_prospective_handoff(handoff)

    _assert_rejected(
        lambda: parse_prospective_handoff(b" " + raw),
        ProspectiveHandoffValidationCode.NON_CANONICAL,
    )
    _assert_rejected(
        lambda: parse_prospective_handoff(
            b'{"schema_version":"x","schema_version":"x"}'
        ),
        ProspectiveHandoffValidationCode.DUPLICATE_FIELD,
    )
    hidden = handoff.model_copy()
    object.__getattribute__(hidden, "__dict__")["forged"] = "DO-NOT-ECHO"
    _assert_rejected(
        lambda: serialize_prospective_handoff(hidden),
        ProspectiveHandoffValidationCode.SEMANTIC_INCONSISTENCY,
    )
    assert not verify_prospective_handoff(hidden, *_inputs())

    class ExactFieldSubclass(ProspectiveHandoffV1):
        pass

    subclass = ExactFieldSubclass.model_validate(handoff.model_dump(mode="python"))
    _assert_rejected(
        lambda: canonical_prospective_handoff_projection(subclass),
        ProspectiveHandoffValidationCode.WRONG_TYPE,
    )
    assert not verify_prospective_handoff(subclass, *_inputs())


def test_module_has_no_runtime_side_effect_import_surface():
    source = Path(handoff_module.__file__).read_text(encoding="utf-8")
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

    for forbidden in (
        "subprocess",
        "socket",
        "requests",
        "httpx",
        "webbrowser",
        "selenium",
        "playwright",
    ):
        assert forbidden not in imported_modules
    assert "def main(" not in source
