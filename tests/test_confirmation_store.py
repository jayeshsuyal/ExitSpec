from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from threading import Barrier

import pytest

import exitspec.confirmation_store as confirmation_store_module
from exitspec.confirmation_store import (
    ConfirmationDecisionRecord,
    ConfirmationStore,
    ContractBinding,
    ContractBindingMismatch,
    DecisionAlreadyRecorded,
    DecisionWriteResult,
    IdempotencyConflict,
    IdempotencyKeyDigest,
    IdempotencyOperationRecord,
    InMemoryConfirmationStore,
    InvitationConsumed,
    InvitationExpired,
    InvitationIdentityConflict,
    InvitationNotFound,
    OperationDigest,
    RecordDecision,
    RequestDigest,
    ReviewInvitationRecord,
    TokenDigest,
    TokenDigestConflict,
)
from exitspec.confirmations import ConfirmationDecision


FIXED_TIME = datetime(2026, 7, 24, 12, 0, tzinfo=timezone.utc)
BINDING = ContractBinding(
    contract_id="support-agent-poc",
    contract_version="1.0.0",
    confirmation_fingerprint="a" * 64,
)


def make_invitation(
    *,
    invitation_id: str = "review-primary",
    token_digest: TokenDigest | None = None,
    binding: ContractBinding = BINDING,
) -> ReviewInvitationRecord:
    return ReviewInvitationRecord(
        invitation_id=invitation_id,
        binding=binding,
        token_digest=token_digest or TokenDigest("b" * 64),
        token_digest_version="sha256-v1",
        intended_organization_id="customer-org",
        issued_by_subject="seller-subject",
        issued_at=FIXED_TIME,
        expires_at=FIXED_TIME + timedelta(hours=2),
    )


def make_command(
    *,
    operation_digest: OperationDigest | None = None,
    idempotency_key_digest: IdempotencyKeyDigest | None = None,
    request_digest: RequestDigest | None = None,
    token_digest: TokenDigest | None = None,
    confirmation_id: str = "cnf_{0}".format("d" * 64),
    invitation_id: str = "review-primary",
    binding: ContractBinding = BINDING,
    decision: object = ConfirmationDecision.CONFIRM,
    agreement_acknowledged: object = True,
    rationale: object = "The customer confirms the exact agreement.",
    decided_at: datetime = FIXED_TIME,
) -> RecordDecision:
    return RecordDecision(
        operation_digest=operation_digest or OperationDigest("c" * 64),
        idempotency_key_digest=(
            idempotency_key_digest or IdempotencyKeyDigest("e" * 64)
        ),
        request_digest=request_digest or RequestDigest("f" * 64),
        token_digest=token_digest or TokenDigest("b" * 64),
        confirmation_id=confirmation_id,
        invitation_id=invitation_id,
        binding=binding,
        reviewer_issuer="https://identity.example",
        reviewer_subject="customer-subject",
        reviewer_organization_id="customer-org",
        reviewer_display_name_snapshot="Customer Reviewer",
        decision=decision,
        agreement_acknowledged=agreement_acknowledged,
        rationale=rationale,
        decided_at=decided_at,
    )


def ready_store() -> InMemoryConfirmationStore:
    store = InMemoryConfirmationStore()
    store.issue_invitation(make_invitation())
    return store


def test_in_memory_adapter_implements_the_typed_port():
    assert isinstance(InMemoryConfirmationStore(), ConfirmationStore)


def test_digest_value_objects_reject_raw_or_malformed_values():
    for digest_type in (
        TokenDigest,
        OperationDigest,
        IdempotencyKeyDigest,
        RequestDigest,
    ):
        with pytest.raises(ValueError, match="SHA-256"):
            digest_type("raw-secret")


def test_store_lookup_rejects_a_raw_review_token():
    store = ready_store()

    with pytest.raises(TypeError, match="never a raw token"):
        store.resolve_invitation("customer-review-secret", FIXED_TIME)


def test_decision_command_rejects_a_raw_idempotency_key():
    with pytest.raises(TypeError, match="IdempotencyKeyDigest"):
        make_command(idempotency_key_digest="raw-idempotency-key")


def test_sensitive_digest_values_are_not_exposed_by_repr():
    token = TokenDigest("a" * 64)
    key = IdempotencyKeyDigest("b" * 64)

    assert token.value not in repr(token)
    assert key.value not in repr(key)


def test_invitation_round_trip_and_exact_replay_return_original_record():
    store = InMemoryConfirmationStore()
    invitation = make_invitation()

    stored = store.issue_invitation(invitation)
    replayed = store.issue_invitation(replace(invitation))

    assert stored is invitation
    assert replayed is stored
    assert store.get_invitation(invitation.invitation_id) is stored
    assert (
        store.resolve_invitation(invitation.token_digest, FIXED_TIME)
        is stored
    )


def test_active_invitation_resolution_uses_constant_time_digest_comparison(
    monkeypatch,
):
    store = ready_store()
    digest = TokenDigest("b" * 64)
    comparisons = []
    compare_digest = confirmation_store_module.hmac.compare_digest

    def checked_compare(left, right):
        comparisons.append((left, right))
        return compare_digest(left, right)

    monkeypatch.setattr(
        confirmation_store_module.hmac,
        "compare_digest",
        checked_compare,
    )

    invitation = store.resolve_invitation(digest, FIXED_TIME)

    assert invitation.invitation_id == "review-primary"
    assert comparisons == [(digest.value, digest.value)]


def test_invitation_expires_at_the_exact_boundary_without_leaking_digest():
    store = ready_store()
    invitation = store.get_invitation("review-primary")

    assert (
        store.resolve_invitation(
            invitation.token_digest,
            invitation.expires_at - timedelta(microseconds=1),
        )
        is invitation
    )
    with pytest.raises(InvitationExpired) as error:
        store.resolve_invitation(
            invitation.token_digest,
            invitation.expires_at,
        )

    assert invitation.token_digest.value not in str(error.value)


def test_unknown_token_digest_does_not_reveal_an_invitation():
    store = ready_store()

    assert store.resolve_invitation(TokenDigest("9" * 64), FIXED_TIME) is None


def test_resolve_invitation_requires_an_aware_transaction_time():
    store = ready_store()

    with pytest.raises(ValueError, match="timezone-aware"):
        store.resolve_invitation(
            TokenDigest("b" * 64),
            datetime(2026, 7, 24, 12, 0),
        )


def test_invitation_identity_conflict_is_rejected():
    store = InMemoryConfirmationStore()
    original = make_invitation()
    conflicting = replace(
        original,
        expires_at=original.expires_at + timedelta(minutes=1),
    )
    store.issue_invitation(original)

    with pytest.raises(InvitationIdentityConflict):
        store.issue_invitation(conflicting)

    assert store.get_invitation(original.invitation_id) is original


def test_duplicate_token_digest_for_another_invitation_is_rejected():
    store = InMemoryConfirmationStore()
    original = make_invitation()
    conflicting = replace(original, invitation_id="review-secondary")
    store.issue_invitation(original)

    with pytest.raises(TokenDigestConflict):
        store.issue_invitation(conflicting)

    assert store.get_invitation("review-secondary") is None


def test_contract_version_cannot_be_rebound_to_another_fingerprint():
    store = InMemoryConfirmationStore()
    original = make_invitation()
    different_binding = replace(BINDING, confirmation_fingerprint="9" * 64)
    store.issue_invitation(original)

    with pytest.raises(ContractBindingMismatch):
        store.issue_invitation(
            make_invitation(
                invitation_id="review-secondary",
                token_digest=TokenDigest("8" * 64),
                binding=different_binding,
            )
        )


def test_missing_lookups_return_none():
    store = InMemoryConfirmationStore()

    assert store.get_invitation("review-missing") is None
    assert store.resolve_invitation(TokenDigest("7" * 64), FIXED_TIME) is None
    assert store.get_decision(BINDING) is None


def test_first_decision_is_recorded_and_contains_no_raw_idempotency_key():
    store = ready_store()

    result = store.record_decision(make_command())

    assert isinstance(result, DecisionWriteResult)
    assert result.replayed is False
    assert result.decision == store.get_decision(BINDING)
    assert not hasattr(result.decision, "idempotency_key")
    assert not hasattr(result.decision, "idempotency_key_digest")


def test_first_decision_requires_the_exact_invitation_token_digest():
    store = ready_store()
    command = make_command(token_digest=TokenDigest("9" * 64))

    with pytest.raises(InvitationNotFound) as error:
        store.record_decision(command)

    assert command.token_digest.value not in str(error.value)
    assert store.get_decision(BINDING) is None


def test_first_decision_rejects_a_token_owned_by_another_invitation():
    store = ready_store()
    second = make_invitation(
        invitation_id="review-secondary",
        token_digest=TokenDigest("8" * 64),
    )
    store.issue_invitation(second)
    command = make_command(token_digest=second.token_digest)

    with pytest.raises(InvitationNotFound) as error:
        store.record_decision(command)

    assert command.token_digest.value not in str(error.value)
    assert store.get_decision(BINDING) is None


def test_first_decision_rejects_expiry_at_the_exact_boundary():
    store = ready_store()
    invitation = store.get_invitation("review-primary")

    with pytest.raises(InvitationExpired):
        store.record_decision(make_command(decided_at=invitation.expires_at))

    assert store.get_decision(BINDING) is None


def test_identical_operation_and_request_replays_original_record():
    store = ready_store()
    original = make_command()
    first = store.record_decision(original)
    retry = replace(
        original,
        confirmation_id="cnf_{0}".format("1" * 64),
        decided_at=FIXED_TIME + timedelta(hours=3),
    )

    replay = store.record_decision(retry)

    assert replay.replayed is True
    assert replay.decision is first.decision
    assert replay.decision.confirmation_id == original.confirmation_id
    assert replay.decision.decided_at == FIXED_TIME


@pytest.mark.parametrize(
    "changed",
    (
        {
            "agreement_acknowledged": False,
            "decided_at": FIXED_TIME + timedelta(hours=3),
        },
        {
            "rationale": None,
            "decided_at": FIXED_TIME + timedelta(hours=3),
        },
    ),
)
def test_same_operation_changed_invalid_payload_is_idempotency_conflict(changed):
    store = ready_store()
    original = make_command()
    store.record_decision(original)

    with pytest.raises(IdempotencyConflict):
        store.record_decision(replace(original, **changed))


@pytest.mark.parametrize(
    "changed",
    (
        {"decision": "INVALID"},
        {"agreement_acknowledged": False},
        {
            "decision": ConfirmationDecision.REQUEST_CHANGES,
            "agreement_acknowledged": False,
            "rationale": "",
        },
        {"rationale": None},
        {"rationale": "x" * 2001},
    ),
)
def test_fresh_invalid_decision_payload_is_rejected(changed):
    store = ready_store()

    with pytest.raises(ValueError):
        store.record_decision(replace(make_command(), **changed))

    assert store.get_decision(BINDING) is None


@pytest.mark.parametrize(
    "changed",
    (
        {"request_digest": RequestDigest("1" * 64)},
        {
            "request_digest": RequestDigest("2" * 64),
            "rationale": "Changed request payload.",
        },
        {"idempotency_key_digest": IdempotencyKeyDigest("3" * 64)},
        {"binding": replace(BINDING, confirmation_fingerprint="4" * 64)},
    ),
)
def test_same_operation_with_changed_request_is_idempotency_conflict(changed):
    store = ready_store()
    original = make_command()
    store.record_decision(original)

    with pytest.raises(IdempotencyConflict):
        store.record_decision(replace(original, **changed))


def test_same_idempotency_digest_cannot_name_another_operation():
    store = ready_store()
    original = make_command()
    store.record_decision(original)
    conflicting = replace(
        original,
        operation_digest=OperationDigest("5" * 64),
    )

    with pytest.raises(IdempotencyConflict):
        store.record_decision(conflicting)


def test_different_operation_after_terminal_decision_is_rejected():
    store = ready_store()
    first = store.record_decision(make_command())
    second_operation = make_command(
        operation_digest=OperationDigest("5" * 64),
        idempotency_key_digest=IdempotencyKeyDigest("6" * 64),
        request_digest=RequestDigest("7" * 64),
        confirmation_id="cnf_{0}".format("8" * 64),
        rationale="A different terminal decision attempt.",
    )

    with pytest.raises(DecisionAlreadyRecorded):
        store.record_decision(second_operation)

    assert store.get_decision(BINDING) is first.decision


def test_terminal_conflict_precedes_fresh_payload_validation():
    store = ready_store()
    store.record_decision(make_command())
    second_operation = make_command(
        operation_digest=OperationDigest("5" * 64),
        idempotency_key_digest=IdempotencyKeyDigest("6" * 64),
        request_digest=RequestDigest("7" * 64),
        confirmation_id="cnf_{0}".format("8" * 64),
        decision="INVALID",
        rationale=None,
    )

    with pytest.raises(DecisionAlreadyRecorded):
        store.record_decision(second_operation)


def test_decision_requires_the_stored_invitation_binding():
    store = InMemoryConfirmationStore()
    invitation = make_invitation()
    store.issue_invitation(invitation)
    changed_binding = replace(BINDING, confirmation_fingerprint="9" * 64)

    with pytest.raises(ContractBindingMismatch):
        store.record_decision(make_command(binding=changed_binding))


def test_resolve_invitation_does_not_return_a_consumed_invitation():
    store = ready_store()
    command = make_command()
    store.record_decision(command)

    with pytest.raises(InvitationConsumed):
        store.resolve_invitation(command.token_digest, FIXED_TIME)


def test_persisted_decision_record_validates_runtime_invariants():
    store = ready_store()
    decision = store.record_decision(make_command()).decision

    assert isinstance(decision, ConfirmationDecisionRecord)
    with pytest.raises(ValueError, match="ConfirmationDecision"):
        replace(decision, decision="INVALID")
    with pytest.raises(ValueError, match="acknowledgement"):
        replace(decision, agreement_acknowledged=False)
    with pytest.raises(ValueError, match="rationale"):
        replace(
            decision,
            decision=ConfirmationDecision.REQUEST_CHANGES,
            agreement_acknowledged=False,
            rationale="",
        )


def test_persisted_idempotency_record_validates_runtime_invariants():
    command = make_command()
    valid = IdempotencyOperationRecord(
        operation_digest=command.operation_digest,
        contract_id=command.binding.contract_id,
        contract_version=command.binding.contract_version,
        idempotency_key_digest=command.idempotency_key_digest,
        request_digest=command.request_digest,
        confirmation_id=command.confirmation_id,
        created_at=command.decided_at,
    )

    assert isinstance(valid, IdempotencyOperationRecord)
    with pytest.raises(TypeError, match="IdempotencyKeyDigest"):
        replace(valid, idempotency_key_digest="raw-idempotency-key")
    with pytest.raises(ValueError, match="timezone-aware"):
        replace(valid, created_at=datetime(2026, 7, 24, 12, 0))


def test_returned_records_are_frozen_and_cannot_change_store_state():
    store = ready_store()
    invitation = store.get_invitation("review-primary")
    decision = store.record_decision(make_command()).decision

    with pytest.raises(FrozenInstanceError):
        invitation.invitation_id = "changed"
    with pytest.raises(FrozenInstanceError):
        decision.rationale = "changed"

    assert store.get_invitation("review-primary") is invitation
    assert store.get_decision(BINDING) is decision


def test_concurrent_identical_operations_commit_once_then_replay():
    store = ready_store()
    attempts = [
        make_command(
            confirmation_id="cnf_{0}".format(format(index + 1, "064x")),
            decided_at=FIXED_TIME + timedelta(microseconds=index),
        )
        for index in range(24)
    ]
    barrier = Barrier(len(attempts))

    def write(command):
        barrier.wait()
        return store.record_decision(command)

    with ThreadPoolExecutor(max_workers=len(attempts)) as executor:
        results = list(executor.map(write, attempts))

    originals = [result for result in results if not result.replayed]
    replays = [result for result in results if result.replayed]

    assert len(originals) == 1
    assert len(replays) == len(attempts) - 1
    assert all(result.decision is originals[0].decision for result in results)
    assert store.get_decision(BINDING) is originals[0].decision


def test_concurrent_same_operation_changed_request_has_one_request_winner():
    store = ready_store()
    first = make_command(
        request_digest=RequestDigest("1" * 64),
        rationale="First request.",
    )
    second = make_command(
        request_digest=RequestDigest("2" * 64),
        rationale="Conflicting request.",
    )
    attempts = [first, second] * 12
    barrier = Barrier(len(attempts))

    def write(command):
        barrier.wait()
        try:
            return ("stored", store.record_decision(command))
        except IdempotencyConflict:
            return ("conflict", None)

    with ThreadPoolExecutor(max_workers=len(attempts)) as executor:
        outcomes = list(executor.map(write, attempts))

    stored = [result for status, result in outcomes if status == "stored"]
    conflicts = [status for status, _ in outcomes if status == "conflict"]

    assert stored
    assert conflicts
    assert sum(not result.replayed for result in stored) == 1
    assert all(result.decision is stored[0].decision for result in stored)
    assert len(stored) + len(conflicts) == len(attempts)


def test_concurrent_different_operations_allow_one_terminal_decision():
    store = ready_store()
    first = make_command(
        operation_digest=OperationDigest("1" * 64),
        idempotency_key_digest=IdempotencyKeyDigest("2" * 64),
        request_digest=RequestDigest("3" * 64),
        confirmation_id="cnf_{0}".format("4" * 64),
    )
    second = make_command(
        operation_digest=OperationDigest("5" * 64),
        idempotency_key_digest=IdempotencyKeyDigest("6" * 64),
        request_digest=RequestDigest("7" * 64),
        confirmation_id="cnf_{0}".format("8" * 64),
        rationale="A different operation.",
    )
    attempts = [first, second] * 12
    barrier = Barrier(len(attempts))

    def write(command):
        barrier.wait()
        try:
            return ("stored", store.record_decision(command))
        except DecisionAlreadyRecorded:
            return ("terminal-conflict", None)

    with ThreadPoolExecutor(max_workers=len(attempts)) as executor:
        outcomes = list(executor.map(write, attempts))

    stored = [result for status, result in outcomes if status == "stored"]
    conflicts = [
        status for status, _ in outcomes if status == "terminal-conflict"
    ]

    assert stored
    assert conflicts
    assert sum(not result.replayed for result in stored) == 1
    assert all(result.decision is stored[0].decision for result in stored)
    assert len(stored) + len(conflicts) == len(attempts)
