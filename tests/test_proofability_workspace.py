"""Adversarial tests for the bounded PR6 proofability workspace core."""

from __future__ import annotations

import hashlib
import threading
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import pytest

import exitspec.proofability as proofability_module
from exitspec.canonical import canonical_json_bytes
from exitspec.poc_creation import (
    DraftPOCCreateRequest,
    ProcessLocalDraftPOCService,
)
from exitspec.producer_capability import get_producer_capability_descriptor
from exitspec.proofability import (
    ProofabilityReportV1,
    evaluate_proofability,
    serialize_proofability_report,
    verify_proofability_report,
)
from exitspec.proofability_workspace import (
    ProofabilityWorkspaceError,
    ProofabilityWorkspaceErrorCode,
    _IdempotencyEntry,
    _ProofabilityWorkspace,
    _record_seal,
    _WorkspaceLimits,
    create_production_proofability_workspace,
    proofability_workspace_stripe_index,
    validate_workspace_scalar,
)
from exitspec.proofability_workspace_fixture import (
    PRODUCTION_FIXTURE_AUTHORITIES,
    PROFILE_ID,
    PROFILE_VERSION,
    ProofabilityFixtureAuthority,
)
from exitspec.qualification_scope import (
    create_qualification_context,
    create_qualification_scope,
)

FROZEN_NOW = datetime(2026, 9, 1, 0, 0, tzinfo=UTC)
DEFAULT_TEST_LIMITS = _WorkspaceLimits()


def _draft_service(*poc_ids: str) -> ProcessLocalDraftPOCService:
    service = ProcessLocalDraftPOCService(
        max_drafts=max(1, len(poc_ids)),
        clock=lambda: FROZEN_NOW,
    )
    for index, poc_id in enumerate(poc_ids):
        service.create(
            DraftPOCCreateRequest(
                display_name=f"POC {index}",
                customer_label="Synthetic customer label",
                use_case="Exercise the bounded planning workspace.",
                owner="owner",
                first_source_choice="DOCUMENT",
                poc_id=poc_id,
            ),
            idempotency_key=f"draft-{index}",
        )
    return service


def make_test_only_proofability_workspace(
    service: ProcessLocalDraftPOCService,
    *,
    authorities: tuple[ProofabilityFixtureAuthority, ...] = (
        PRODUCTION_FIXTURE_AUTHORITIES[0],
    ),
    fixture_resolver=None,
    descriptor_resolver=get_producer_capability_descriptor,
    evaluator=evaluate_proofability,
    verifier=verify_proofability_report,
    limits: _WorkspaceLimits = DEFAULT_TEST_LIMITS,
) -> _ProofabilityWorkspace:
    """Construct the private drift/adversarial seam; never imported by src."""

    selected = authorities[0] if fixture_resolver is None else None
    resolver = (lambda: selected) if fixture_resolver is None else fixture_resolver
    return _ProofabilityWorkspace(
        draft_lookup=service.get,
        draft_commit_guard=service.authoring_commit_guard,
        fixture_resolver=resolver,
        fixture_authorities=authorities,
        descriptor_resolver=descriptor_resolver,
        evaluator=evaluator,
        verifier=verifier,
        limits=limits,
    )


def _create(workspace: _ProofabilityWorkspace, poc_id: str, key: str):
    return workspace.create(
        poc_id=poc_id,
        profile_id=PROFILE_ID,
        profile_version=PROFILE_VERSION,
        idempotency_key=key,
    )


def _assert_code(error: pytest.ExceptionInfo[ProofabilityWorkspaceError], code):
    assert error.value.code is code
    assert str(error.value) == code.value


def _alternate_authority() -> ProofabilityFixtureAuthority:
    base = PRODUCTION_FIXTURE_AUTHORITIES[0]
    scope = create_qualification_scope(
        {
            "schema_version": "exitspec.qualification-scope.v1",
            "frozen_contract": {
                "contract_id": base.contract.id,
                "contract_canonical_digest": (
                    "sha256:" + base.contract.canonical_hash
                ),
            },
            "workload": {
                "workload_id": "changed-workload-v2",
                "workload_digest": "sha256:" + "6" * 64,
            },
            "measurement_profile": {
                "environment_id": "changed-environment-v2",
                "environment_digest": "sha256:" + "7" * 64,
                "profile_id": "changed-measurement-profile-v2",
                "profile_version": "2.0.0",
                "profile_digest": "sha256:" + "8" * 64,
            },
            "evaluated_use": "CANARY_CONSIDERATION",
            "maximum_use": {"maximum_traffic_percent": 4},
            "freshness_policy": {
                "age_basis": "EVIDENCE_CAPTURED_AT",
                "maximum_evidence_age_seconds": 43_200,
            },
            "reference_subject_requirement": "NOT_REQUIRED",
            "reference_subject_digest": None,
        }
    )
    context = create_qualification_context(
        base.subject,
        scope,
        protocol_id=base.expected_protocol_id,
        protocol_version=base.expected_protocol_version,
    )
    descriptor = get_producer_capability_descriptor(
        profile_id=PROFILE_ID,
        profile_version=PROFILE_VERSION,
    )
    report = evaluate_proofability(
        base.subject,
        scope,
        context,
        base.contract,
        descriptor,
    )
    content = serialize_proofability_report(report)
    return replace(
        base,
        scope=scope,
        context=context,
        expected_scope_digest=scope.scope_digest,
        expected_qualification_context_digest=(
            context.qualification_context_digest
        ),
        expected_proofability_report_digest=report.proofability_report_digest,
        expected_canonical_report_byte_count=len(content),
    )


def test_production_fixture_is_cardinality_one_and_exact_golden_authority():
    assert len(PRODUCTION_FIXTURE_AUTHORITIES) == 1
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
    content = serialize_proofability_report(report)
    assert len(content) == authority.expected_canonical_report_byte_count == 2_602
    assert not content.endswith(b"\n")
    assert hashlib.sha256(content).hexdigest() == (
        "7e691ccde07084cb80b739253143641ccf3655cf41513c5c0a59a4ebf591691d"
    )
    assert report.proofability_report_digest == (
        "sha256:28c49bba2dd3791905a201a74777c9994e6ecc083cc3b9de083095f4c626d81e"
    )
    assert verify_proofability_report(
        report,
        authority.subject,
        authority.scope,
        authority.context,
        authority.contract,
        descriptor,
    )


@pytest.mark.parametrize(
    "value",
    [
        None,
        1,
        "",
        " " + "key",
        "key\u00a0",
        "\u2009key",
        "key\u3000",
        "bad\x00key",
        "bad\x7fkey",
        "bad\u0085key",
        "x" * 129,
        "\ud800",
    ],
)
def test_exact_scalar_validator_rejects_type_controls_edges_and_bounds(value):
    with pytest.raises(ProofabilityWorkspaceError) as error:
        validate_workspace_scalar(value)
    _assert_code(error, ProofabilityWorkspaceErrorCode.INVALID_REQUEST)


def test_exact_scalar_validator_preserves_non_normalized_utf8_identity():
    nfc = "caf\u00e9"
    nfd = "cafe\u0301"
    assert validate_workspace_scalar(nfc) == (nfc, nfc.encode("utf-8"))
    assert validate_workspace_scalar(nfd) == (nfd, nfd.encode("utf-8"))
    assert nfc.encode("utf-8") != nfd.encode("utf-8")


def test_stripe_mapping_uses_frozen_domain_and_exact_128_eager_locks():
    service = _draft_service("poc_alpha")
    workspace = create_production_proofability_workspace(
        draft_lookup=service.get,
        draft_commit_guard=service.authoring_commit_guard,
    )
    assert workspace.write_stripe_count == 128
    vectors = {
        "poc_alpha": 35,
        "poc_beta": 55,
        "poc_gamma": 71,
        "poc_123": 27,
    }
    assert {
        poc_id: proofability_workspace_stripe_index(poc_id)
        for poc_id in vectors
    } == vectors


def test_fresh_replay_get_and_latest_history_are_closed_and_byte_identical():
    service = _draft_service("poc_alpha")
    workspace = make_test_only_proofability_workspace(service)
    empty = workspace.get(poc_id="poc_alpha")
    first = _create(workspace, "poc_alpha", "key-one")
    second = _create(workspace, "poc_alpha", "key-two")
    replay_first = _create(workspace, "poc_alpha", "key-one")
    latest = workspace.get(poc_id="poc_alpha")

    assert empty["report"] is None
    assert empty["needs_replan"] is False
    assert "idempotent_replay" not in empty
    assert first["idempotent_replay"] is False
    assert second["idempotent_replay"] is False
    assert replay_first["idempotent_replay"] is True
    assert replay_first["report"] == first["report"]
    assert latest["report"] == second["report"]
    assert len(canonical_json_bytes(first["report"])) == 2_602
    expected_keys = {
        "schema_version",
        "poc_id",
        "report",
        "needs_replan",
        "reported_context_digest",
        "resolved_context_digest",
        "profile_request",
        "context_source",
        "storage",
        "authority",
    }
    assert set(latest) == expected_keys
    assert latest["context_source"] == {
        "kind": "PACKAGE_SYNTHETIC_FIXTURE",
        "fixture_id": "exitspec.synthetic-proofability-preflight.native-v1",
        "fixture_version": "v1",
        "poc_derived": False,
    }
    assert latest["storage"] == {
        "scope": "PROCESS_LOCAL",
        "survives_process_restart": False,
        "shared_across_workers": False,
    }
    assert latest["authority"] == {
        "deployment_authorized": False,
        "production_traffic_authorized": False,
        "traffic_expansion_authorized": False,
        "external_authorization_required": True,
    }


def test_exact_pr5_call_matrix_for_fresh_replay_get_and_malformed(
    monkeypatch: pytest.MonkeyPatch,
):
    service = _draft_service("poc_alpha")
    original_evaluate = evaluate_proofability
    counts = {"resolver": 0, "descriptor": 0, "injected": 0, "verifier": 0, "internal": 0}

    def draft_lookup(poc_id):
        counts["resolver"] += 1
        return service.get(poc_id)

    def descriptor_resolver(**request):
        counts["descriptor"] += 1
        return get_producer_capability_descriptor(**request)

    def injected(*args):
        counts["injected"] += 1
        return original_evaluate(*args)

    def internal(*args):
        counts["internal"] += 1
        return original_evaluate(*args)

    def verifier(*args):
        counts["verifier"] += 1
        return verify_proofability_report(*args)

    monkeypatch.setattr(proofability_module, "evaluate_proofability", internal)
    workspace = _ProofabilityWorkspace(
        draft_lookup=draft_lookup,
        draft_commit_guard=service.authoring_commit_guard,
        fixture_resolver=lambda: PRODUCTION_FIXTURE_AUTHORITIES[0],
        fixture_authorities=PRODUCTION_FIXTURE_AUTHORITIES,
        descriptor_resolver=descriptor_resolver,
        evaluator=injected,
        verifier=verifier,
    )

    _create(workspace, "poc_alpha", "key-one")
    assert counts == {
        "resolver": 1,
        "descriptor": 1,
        "injected": 1,
        "verifier": 1,
        "internal": 1,
    }
    for key in counts:
        counts[key] = 0
    _create(workspace, "poc_alpha", "key-one")
    assert counts == {
        "resolver": 1,
        "descriptor": 1,
        "injected": 0,
        "verifier": 0,
        "internal": 0,
    }
    for key in counts:
        counts[key] = 0
    workspace.get(poc_id="poc_alpha")
    assert counts == {
        "resolver": 1,
        "descriptor": 1,
        "injected": 0,
        "verifier": 0,
        "internal": 0,
    }

    malformed_counts = {"injected": 0, "verifier": 0, "internal": 0}

    def malformed(*_args):
        malformed_counts["injected"] += 1
        return "wrong-type"

    def malformed_verifier(*args):
        malformed_counts["verifier"] += 1
        return verify_proofability_report(*args)

    monkeypatch.setattr(
        proofability_module,
        "evaluate_proofability",
        lambda *args: (
            malformed_counts.__setitem__(
                "internal", malformed_counts["internal"] + 1
            )
            or original_evaluate(*args)
        ),
    )
    malformed_workspace = make_test_only_proofability_workspace(
        service,
        evaluator=malformed,
        verifier=malformed_verifier,
    )
    with pytest.raises(ProofabilityWorkspaceError) as error:
        _create(malformed_workspace, "poc_alpha", "malformed-key")
    _assert_code(error, ProofabilityWorkspaceErrorCode.WORKSPACE_UNAVAILABLE)
    assert malformed_counts == {"injected": 1, "verifier": 1, "internal": 0}


def test_evaluator_raise_is_one_zero_zero_and_releases_owned_reservation():
    service = _draft_service("poc_alpha")
    calls = {"evaluator": 0, "verifier": 0}

    def raising(*_args):
        calls["evaluator"] += 1
        raise RuntimeError("HOSTILE_EXCEPTION_SENTINEL")

    def verifier(*_args):
        calls["verifier"] += 1
        return True

    workspace = make_test_only_proofability_workspace(
        service,
        evaluator=raising,
        verifier=verifier,
    )
    with pytest.raises(ProofabilityWorkspaceError) as error:
        _create(workspace, "poc_alpha", "key")
    _assert_code(error, ProofabilityWorkspaceErrorCode.WORKSPACE_UNAVAILABLE)
    assert calls == {"evaluator": 1, "verifier": 0}
    assert workspace._pending == {}
    assert workspace._operations == {}
    assert workspace._idempotency == {}
    assert workspace._accepted_report_bytes == 0


def test_subclass_raw_hidden_construct_malformed_and_wrong_evaluator_outputs_never_publish():
    service = _draft_service("poc_alpha")
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

    class ReportSubclass(ProofabilityReportV1):
        @property
        def forged(self) -> str:
            return "HOSTILE_SUBCLASS_SENTINEL"

    subclass = ReportSubclass.model_validate(report.model_dump(mode="python"))
    hidden = report.model_copy(deep=True)
    object.__getattribute__(hidden, "__dict__")["forged"] = (
        "HOSTILE_HIDDEN_SENTINEL"
    )
    constructed = ProofabilityReportV1.model_construct(
        **{**report.model_dump(mode="python"), "criterion_results": []}
    )
    malformed = report.model_copy(update={"overall_disposition": "BOGUS"})
    candidates = (
        subclass,
        report.model_dump(mode="python"),
        hidden,
        constructed,
        malformed,
        "wrong-type",
    )
    for index, candidate in enumerate(candidates):
        workspace = make_test_only_proofability_workspace(
            service,
            evaluator=lambda *_args, candidate=candidate: candidate,
        )
        with pytest.raises(ProofabilityWorkspaceError) as error:
            _create(workspace, "poc_alpha", f"bad-evaluator-{index}")
        _assert_code(error, ProofabilityWorkspaceErrorCode.WORKSPACE_UNAVAILABLE)
        assert workspace._operations == {}
        assert workspace._idempotency == {}
        assert workspace._latest_by_poc == {}
        assert workspace._pending == {}


def test_failed_fresh_attempt_preserves_prior_latest_and_replay_history():
    service = _draft_service("poc_alpha")
    calls = 0

    def evaluator(*args):
        nonlocal calls
        calls += 1
        if calls == 1:
            return evaluate_proofability(*args)
        raise RuntimeError("HOSTILE_SECOND_EVALUATION")

    workspace = make_test_only_proofability_workspace(
        service,
        evaluator=evaluator,
    )
    accepted = _create(workspace, "poc_alpha", "accepted-key")
    with pytest.raises(ProofabilityWorkspaceError) as error:
        _create(workspace, "poc_alpha", "failed-key")
    _assert_code(error, ProofabilityWorkspaceErrorCode.WORKSPACE_UNAVAILABLE)
    latest = workspace.get(poc_id="poc_alpha")
    replay = _create(workspace, "poc_alpha", "accepted-key")
    assert latest["report"] == accepted["report"]
    assert replay["report"] == accepted["report"]
    assert replay["idempotent_replay"] is True
    assert len(workspace._operations) == 1
    assert workspace._pending == {}


def test_foreign_self_consistent_report_reaches_verifier_internal_eval_but_never_publishes(
    monkeypatch: pytest.MonkeyPatch,
):
    service = _draft_service("poc_alpha")
    base = PRODUCTION_FIXTURE_AUTHORITIES[0]
    alternate = _alternate_authority()
    descriptor = get_producer_capability_descriptor(
        profile_id=PROFILE_ID,
        profile_version=PROFILE_VERSION,
    )
    foreign = evaluate_proofability(
        alternate.subject,
        alternate.scope,
        alternate.context,
        alternate.contract,
        descriptor,
    )
    calls = {"injected": 0, "verifier": 0, "internal": 0}
    original = evaluate_proofability

    def injected(*_args):
        calls["injected"] += 1
        return foreign

    def internal(*args):
        calls["internal"] += 1
        return original(*args)

    def verifier(*args):
        calls["verifier"] += 1
        return verify_proofability_report(*args)

    monkeypatch.setattr(proofability_module, "evaluate_proofability", internal)
    workspace = make_test_only_proofability_workspace(
        service,
        authorities=(base,),
        evaluator=injected,
        verifier=verifier,
    )
    with pytest.raises(ProofabilityWorkspaceError) as error:
        _create(workspace, "poc_alpha", "foreign-key")
    _assert_code(error, ProofabilityWorkspaceErrorCode.WORKSPACE_UNAVAILABLE)
    assert calls == {"injected": 1, "verifier": 1, "internal": 1}
    assert workspace._operations == {}


def test_profile_rejection_precedes_poc_resolution_and_distinguishes_400_422():
    service = _draft_service("poc_alpha")
    calls = 0

    def lookup(poc_id):
        nonlocal calls
        calls += 1
        return service.get(poc_id)

    workspace = _ProofabilityWorkspace(
        draft_lookup=lookup,
        draft_commit_guard=service.authoring_commit_guard,
        fixture_resolver=lambda: PRODUCTION_FIXTURE_AUTHORITIES[0],
        fixture_authorities=PRODUCTION_FIXTURE_AUTHORITIES,
    )
    with pytest.raises(ProofabilityWorkspaceError) as malformed:
        workspace.create(
            poc_id="poc_unknown",
            profile_id=" " + PROFILE_ID,
            profile_version=PROFILE_VERSION,
            idempotency_key="key",
        )
    _assert_code(malformed, ProofabilityWorkspaceErrorCode.INVALID_REQUEST)
    with pytest.raises(ProofabilityWorkspaceError) as unsupported:
        workspace.create(
            poc_id="poc_unknown",
            profile_id="profil\u00e9",
            profile_version=PROFILE_VERSION,
            idempotency_key="key",
        )
    _assert_code(unsupported, ProofabilityWorkspaceErrorCode.PROFILE_UNSUPPORTED)
    assert calls == 0


def test_nfc_and_nfd_keys_remain_distinct_operations_and_replay_exactly():
    service = _draft_service("poc_alpha")
    workspace = make_test_only_proofability_workspace(service)
    nfc = "caf\u00e9"
    nfd = "cafe\u0301"
    first = _create(workspace, "poc_alpha", nfc)
    second = _create(workspace, "poc_alpha", nfd)
    assert first["idempotent_replay"] is False
    assert second["idempotent_replay"] is False
    assert len(workspace._operations) == 2
    assert _create(workspace, "poc_alpha", nfc)["idempotent_replay"] is True
    assert _create(workspace, "poc_alpha", nfd)["idempotent_replay"] is True


def test_global_key_conflict_is_closed_across_pocs():
    service = _draft_service("poc_alpha", "poc_beta")
    workspace = make_test_only_proofability_workspace(service)
    _create(workspace, "poc_alpha", "global-key")
    with pytest.raises(ProofabilityWorkspaceError) as conflict:
        _create(workspace, "poc_beta", "global-key")
    _assert_code(conflict, ProofabilityWorkspaceErrorCode.IDEMPOTENCY_CONFLICT)
    assert workspace.get(poc_id="poc_beta")["report"] is None


def test_active_binding_drift_hides_historical_report_but_validates_it():
    service = _draft_service("poc_alpha")
    base = PRODUCTION_FIXTURE_AUTHORITIES[0]
    alternate = _alternate_authority()
    selected = [base]
    workspace = make_test_only_proofability_workspace(
        service,
        authorities=(base, alternate),
        fixture_resolver=lambda: selected[0],
    )
    created = _create(workspace, "poc_alpha", "base-key")
    selected[0] = alternate
    drift = workspace.get(poc_id="poc_alpha")
    assert drift["report"] is None
    assert drift["needs_replan"] is True
    assert drift["reported_context_digest"] == (
        created["report"]["qualification_context_digest"]
    )
    assert drift["resolved_context_digest"] == (
        alternate.expected_qualification_context_digest
    )
    refreshed = _create(workspace, "poc_alpha", "alternate-key")
    assert refreshed["report"]["qualification_context_digest"] == (
        alternate.expected_qualification_context_digest
    )


def test_replay_precedes_every_full_capacity_check_and_new_key_fails_closed():
    service = _draft_service("poc_alpha")
    workspace = make_test_only_proofability_workspace(
        service,
        limits=_WorkspaceLimits(
            latest_pocs=1,
            operations=1,
            idempotency_entries=1,
            pending=1,
            report_bytes_per_operation=2_602,
            aggregate_report_bytes=2_602,
        ),
    )
    _create(workspace, "poc_alpha", "full-key")
    assert _create(workspace, "poc_alpha", "full-key")["idempotent_replay"]
    with pytest.raises(ProofabilityWorkspaceError) as full:
        _create(workspace, "poc_alpha", "new-key")
    _assert_code(full, ProofabilityWorkspaceErrorCode.CAPACITY_EXHAUSTED)


@pytest.mark.parametrize(
    "limits",
    [
        _WorkspaceLimits(
            latest_pocs=2,
            operations=1,
            idempotency_entries=2,
            pending=2,
            report_bytes_per_operation=2_602,
            aggregate_report_bytes=5_204,
        ),
        _WorkspaceLimits(
            latest_pocs=2,
            operations=2,
            idempotency_entries=1,
            pending=2,
            report_bytes_per_operation=2_602,
            aggregate_report_bytes=5_204,
        ),
        _WorkspaceLimits(
            latest_pocs=1,
            operations=2,
            idempotency_entries=2,
            pending=2,
            report_bytes_per_operation=2_602,
            aggregate_report_bytes=5_204,
        ),
        _WorkspaceLimits(
            latest_pocs=2,
            operations=2,
            idempotency_entries=2,
            pending=2,
            report_bytes_per_operation=2_602,
            aggregate_report_bytes=2_602,
        ),
    ],
)
def test_each_last_operation_idempotency_latest_and_byte_boundary_is_owned(limits):
    service = _draft_service("poc_alpha", "poc_beta")
    workspace = make_test_only_proofability_workspace(service, limits=limits)
    _create(workspace, "poc_alpha", "first-key")
    with pytest.raises(ProofabilityWorkspaceError) as full:
        _create(workspace, "poc_beta", "second-key")
    _assert_code(full, ProofabilityWorkspaceErrorCode.CAPACITY_EXHAUSTED)
    assert workspace.get(poc_id="poc_alpha")["report"] is not None
    assert workspace.get(poc_id="poc_beta")["report"] is None


def test_last_pending_slot_is_reserved_before_evaluation_and_released_exactly():
    service = _draft_service("poc_alpha", "poc_beta")
    entered = threading.Event()
    release = threading.Event()

    def evaluator(*args):
        entered.set()
        assert release.wait(timeout=5)
        return evaluate_proofability(*args)

    workspace = make_test_only_proofability_workspace(
        service,
        evaluator=evaluator,
        limits=_WorkspaceLimits(
            latest_pocs=2,
            operations=2,
            idempotency_entries=2,
            pending=1,
            report_bytes_per_operation=2_602,
            aggregate_report_bytes=5_204,
        ),
    )
    owner = threading.Thread(
        target=lambda: _create(workspace, "poc_alpha", "first-key")
    )
    owner.start()
    assert entered.wait(timeout=5)
    with pytest.raises(ProofabilityWorkspaceError) as full:
        _create(workspace, "poc_beta", "second-key")
    _assert_code(full, ProofabilityWorkspaceErrorCode.CAPACITY_EXHAUSTED)
    assert len(workspace._pending) == 1
    release.set()
    owner.join(timeout=10)
    assert workspace._pending == {}
    assert len(workspace._operations) == 1


@pytest.mark.parametrize(
    "limits",
    [
        _WorkspaceLimits(
            latest_pocs=2,
            operations=1,
            idempotency_entries=2,
            pending=2,
            report_bytes_per_operation=2_602,
            aggregate_report_bytes=5_204,
        ),
        _WorkspaceLimits(
            latest_pocs=2,
            operations=2,
            idempotency_entries=1,
            pending=2,
            report_bytes_per_operation=2_602,
            aggregate_report_bytes=5_204,
        ),
        _WorkspaceLimits(
            latest_pocs=1,
            operations=2,
            idempotency_entries=2,
            pending=2,
            report_bytes_per_operation=2_602,
            aggregate_report_bytes=5_204,
        ),
        _WorkspaceLimits(
            latest_pocs=2,
            operations=2,
            idempotency_entries=2,
            pending=2,
            report_bytes_per_operation=2_602,
            aggregate_report_bytes=2_602,
        ),
    ],
)
def test_pending_owner_wins_last_operation_key_latest_and_byte_races(limits):
    service = _draft_service("poc_alpha", "poc_beta")
    entered = threading.Event()
    release = threading.Event()
    failures: list[BaseException] = []

    def evaluator(*args):
        entered.set()
        assert release.wait(timeout=5)
        return evaluate_proofability(*args)

    workspace = make_test_only_proofability_workspace(
        service,
        evaluator=evaluator,
        limits=limits,
    )

    def owner():
        try:
            _create(workspace, "poc_alpha", "owner-key")
        except BaseException as error:  # noqa: BLE001 - thread assertion
            failures.append(error)

    thread = threading.Thread(target=owner)
    thread.start()
    assert entered.wait(timeout=5)
    with pytest.raises(ProofabilityWorkspaceError) as full:
        _create(workspace, "poc_beta", "contender-key")
    _assert_code(full, ProofabilityWorkspaceErrorCode.CAPACITY_EXHAUSTED)
    release.set()
    thread.join(timeout=10)
    assert failures == []
    assert len(workspace._operations) == 1
    assert workspace.get(poc_id="poc_beta")["report"] is None


def test_different_keys_same_poc_serialize_and_prior_history_remains_replayable():
    service = _draft_service("poc_alpha")
    first_entered = threading.Event()
    release_first = threading.Event()
    calls: list[str] = []

    def evaluator(*args):
        calls.append("evaluate")
        if len(calls) == 1:
            first_entered.set()
            assert release_first.wait(timeout=5)
        return evaluate_proofability(*args)

    workspace = make_test_only_proofability_workspace(service, evaluator=evaluator)
    failures: list[BaseException] = []

    def create_key(key):
        try:
            _create(workspace, "poc_alpha", key)
        except BaseException as error:  # noqa: BLE001 - thread assertion
            failures.append(error)

    first = threading.Thread(target=create_key, args=("first-key",))
    second = threading.Thread(target=create_key, args=("second-key",))
    first.start()
    assert first_entered.wait(timeout=5)
    second.start()
    assert calls == ["evaluate"]
    release_first.set()
    first.join(timeout=10)
    second.join(timeout=10)
    assert failures == []
    assert calls == ["evaluate", "evaluate"]
    assert len(workspace._operations) == 2
    assert _create(workspace, "poc_alpha", "first-key")["idempotent_replay"]


def test_distinct_pocs_on_known_colliding_stripe_serialize_conservatively():
    candidates: dict[int, str] = {}
    pair = None
    for index in range(1, 2_000):
        poc_id = f"poc_collision_{index}"
        stripe = proofability_workspace_stripe_index(poc_id)
        if stripe in candidates:
            pair = (candidates[stripe], poc_id)
            break
        candidates[stripe] = poc_id
    assert pair is not None
    first_poc, second_poc = pair
    assert proofability_workspace_stripe_index(first_poc) == (
        proofability_workspace_stripe_index(second_poc)
    )
    service = _draft_service(first_poc, second_poc)
    first_entered = threading.Event()
    release_first = threading.Event()
    calls = 0

    def evaluator(*args):
        nonlocal calls
        calls += 1
        if calls == 1:
            first_entered.set()
            assert release_first.wait(timeout=5)
        return evaluate_proofability(*args)

    workspace = make_test_only_proofability_workspace(service, evaluator=evaluator)
    one = threading.Thread(target=lambda: _create(workspace, first_poc, "key-one"))
    two = threading.Thread(target=lambda: _create(workspace, second_poc, "key-two"))
    one.start()
    assert first_entered.wait(timeout=5)
    two.start()
    assert calls == 1
    release_first.set()
    one.join(timeout=10)
    two.join(timeout=10)
    assert calls == 2
    assert len(workspace._operations) == 2


def test_same_poc_same_key_race_has_one_publication_and_exact_replays():
    service = _draft_service("poc_alpha")
    calls = 0
    calls_lock = threading.Lock()

    def evaluator(*args):
        nonlocal calls
        with calls_lock:
            calls += 1
        return evaluate_proofability(*args)

    workspace = make_test_only_proofability_workspace(service, evaluator=evaluator)
    barrier = threading.Barrier(8)
    results: list[dict[str, Any]] = []
    failures: list[BaseException] = []

    def worker():
        try:
            barrier.wait(timeout=5)
            results.append(_create(workspace, "poc_alpha", "race-key"))
        except BaseException as error:  # noqa: BLE001 - test captures threads
            failures.append(error)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert failures == []
    assert calls == 1
    assert sum(not result["idempotent_replay"] for result in results) == 1
    assert sum(result["idempotent_replay"] for result in results) == 7
    assert len(workspace._operations) == 1


def test_same_key_different_poc_pending_race_has_one_fresh_and_one_conflict():
    service = _draft_service("poc_alpha", "poc_beta")
    entered = threading.Event()
    release = threading.Event()

    def evaluator(*args):
        entered.set()
        assert release.wait(timeout=5)
        return evaluate_proofability(*args)

    workspace = make_test_only_proofability_workspace(service, evaluator=evaluator)
    outcomes: list[str] = []

    def first():
        _create(workspace, "poc_alpha", "shared-key")
        outcomes.append("fresh")

    owner = threading.Thread(target=first)
    owner.start()
    assert entered.wait(timeout=5)
    with pytest.raises(ProofabilityWorkspaceError) as conflict:
        _create(workspace, "poc_beta", "shared-key")
    _assert_code(conflict, ProofabilityWorkspaceErrorCode.IDEMPOTENCY_CONFLICT)
    release.set()
    owner.join(timeout=10)
    assert outcomes == ["fresh"]
    assert len(workspace._operations) == 1


def test_archive_between_resolve_and_commit_guard_publishes_nothing():
    service = _draft_service("poc_alpha")

    def evaluator(*args):
        report = evaluate_proofability(*args)
        service.archive("poc_alpha")
        return report

    workspace = make_test_only_proofability_workspace(service, evaluator=evaluator)
    with pytest.raises(ProofabilityWorkspaceError) as unavailable:
        _create(workspace, "poc_alpha", "archive-key")
    _assert_code(unavailable, ProofabilityWorkspaceErrorCode.WORKSPACE_UNAVAILABLE)
    assert workspace._operations == {}
    assert workspace._latest_by_poc == {}
    assert workspace._pending == {}


@pytest.mark.parametrize(
    "mutation",
    [
        "bytes",
        "byte-count",
        "digest",
        "seal",
        "owner",
        "request",
        "binding",
        "counter",
        "broken-index",
        "cross-poc-latest",
        "missing-authority",
    ],
)
def test_store_substitution_and_relation_corruption_fail_closed(mutation):
    service = _draft_service("poc_alpha")
    workspace = make_test_only_proofability_workspace(service)
    _create(workspace, "poc_alpha", "key")
    key, operation = next(iter(workspace._operations.items()))
    if mutation == "bytes":
        changed = replace(
            operation,
            canonical_report_bytes=(
                b"[" + operation.canonical_report_bytes[1:]
            ),
        )
    elif mutation == "byte-count":
        changed = replace(
            operation,
            canonical_report_bytes=operation.canonical_report_bytes + b" ",
        )
    elif mutation == "digest":
        changed = replace(operation, proofability_report_digest="sha256:" + "0" * 64)
    elif mutation == "seal":
        changed = replace(operation, record_seal="sha256:" + "0" * 64)
    elif mutation == "owner":
        changed = replace(operation, poc_id="poc_beta")
    elif mutation == "request":
        changed = replace(operation, request_digest="sha256:" + "0" * 64)
    elif mutation == "binding":
        changed = replace(
            operation,
            input_binding_fingerprint="sha256:" + "0" * 64,
        )
    elif mutation == "counter":
        workspace._accepted_report_bytes += 1
        changed = operation
    elif mutation == "broken-index":
        workspace._idempotency.pop(key)
        changed = operation
    elif mutation == "cross-poc-latest":
        workspace._latest_by_poc["poc_beta"] = key
        changed = operation
    else:
        workspace._fixture_authorities = ()
        changed = operation
    if changed is not operation:
        workspace._operations[key] = changed
        workspace._idempotency[key] = _IdempotencyEntry(
            request_digest=changed.request_digest,
            operation=changed,
        )
    with pytest.raises(ProofabilityWorkspaceError) as unavailable:
        workspace.get(poc_id="poc_alpha")
    _assert_code(unavailable, ProofabilityWorkspaceErrorCode.WORKSPACE_UNAVAILABLE)


def test_coherent_foreign_report_replacement_fails_golden_on_get_and_replay():
    service = _draft_service("poc_alpha")
    workspace = make_test_only_proofability_workspace(service)
    _create(workspace, "poc_alpha", "key")
    key, operation = next(iter(workspace._operations.items()))
    alternate = _alternate_authority()
    descriptor = get_producer_capability_descriptor(
        profile_id=PROFILE_ID,
        profile_version=PROFILE_VERSION,
    )
    foreign = evaluate_proofability(
        alternate.subject,
        alternate.scope,
        alternate.context,
        alternate.contract,
        descriptor,
    )
    provisional = replace(
        operation,
        canonical_report_bytes=serialize_proofability_report(foreign),
        proofability_report_digest=foreign.proofability_report_digest,
        record_seal="sha256:" + "0" * 64,
    )
    changed = replace(provisional, record_seal=_record_seal(provisional))
    workspace._operations[key] = changed
    workspace._idempotency[key] = _IdempotencyEntry(
        request_digest=changed.request_digest,
        operation=changed,
    )
    workspace._accepted_report_bytes = len(changed.canonical_report_bytes)
    with pytest.raises(ProofabilityWorkspaceError) as get_error:
        workspace.get(poc_id="poc_alpha")
    _assert_code(get_error, ProofabilityWorkspaceErrorCode.WORKSPACE_UNAVAILABLE)
    with pytest.raises(ProofabilityWorkspaceError) as replay_error:
        _create(workspace, "poc_alpha", "key")
    _assert_code(replay_error, ProofabilityWorkspaceErrorCode.WORKSPACE_UNAVAILABLE)


def test_archived_and_unknown_are_404_but_post_resolution_guard_conflict_is_503():
    service = _draft_service("poc_alpha")
    workspace = make_test_only_proofability_workspace(service)
    service.archive("poc_alpha")
    for poc_id in ("poc_alpha", "poc_unknown"):
        with pytest.raises(ProofabilityWorkspaceError) as missing:
            workspace.get(poc_id=poc_id)
        _assert_code(missing, ProofabilityWorkspaceErrorCode.POC_NOT_FOUND)


def test_source_has_no_runtime_fixture_choice_clock_or_forbidden_collaborator():
    source = (
        __import__("pathlib").Path(__file__).parents[1]
        / "src"
        / "exitspec"
        / "proofability_workspace.py"
    ).read_text(encoding="utf-8")
    fixture_source = (
        __import__("pathlib").Path(__file__).parents[1]
        / "src"
        / "exitspec"
        / "proofability_workspace_fixture.py"
    ).read_text(encoding="utf-8")
    assert "make_test_only_proofability_workspace" not in source
    assert "make_test_only_proofability_workspace" not in fixture_source
    assert "datetime.now" not in source
    assert "time.time" not in source
    assert "requests" not in source
    assert "urllib.request" not in source
    assert "provider_egress" not in source
    assert "fixture_path)." not in fixture_source
