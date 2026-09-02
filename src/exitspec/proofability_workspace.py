"""Bounded process-local workspace for one package-synthetic PR5 preflight.

This module stores immutable proofability planning reports only.  It has no
provider, execution, evidence, verdict, validity, deployment, traffic, clock,
or persistence collaborator.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from enum import Enum
from threading import RLock
from typing import Any, Final

from .canonical import canonical_json_bytes
from .contracts import contract_digest, verify_contract_digest
from .models import ContractStatus, POCContract
from .poc_creation import (
    DraftPOCArchiveState,
    DraftPOCCommitConflict,
    DraftPOCNotFound,
    DraftPOCSnapshot,
)
from .producer_capability import (
    ProducerCapabilityDescriptorV1,
    get_producer_capability_descriptor,
    producer_capability_digest,
    verify_producer_capability_descriptor,
)
from .proofability import (
    ProofabilityReportV1,
    evaluate_proofability,
    parse_proofability_report,
    proofability_report_digest,
    serialize_proofability_report,
    verify_proofability_report,
)
from .proofability_workspace_fixture import (
    PRODUCTION_FIXTURE_AUTHORITIES,
    PROFILE_ID,
    PROFILE_VERSION,
    ProofabilityFixtureAuthority,
    production_fixture_authority,
)
from .qualification_scope import (
    QualificationContextV1,
    QualificationScopeV1,
    qualification_context_digest,
    qualification_scope_digest,
    verify_qualification_context,
    verify_qualification_scope,
)
from .serving_subject import (
    ServingSubjectManifestV1,
    serving_subject_digest,
    verify_serving_subject_manifest,
)

WORKSPACE_RESPONSE_SCHEMA_VERSION: Final = (
    "exitspec.proofability-workspace-response.v1"
)
_POC_ID_RE: Final = re.compile(r"^poc_[a-z0-9][a-z0-9_-]{2,63}$")
_DIGEST_RE: Final = re.compile(r"^sha256:[a-f0-9]{64}$")
_WRITE_STRIPE_DOMAIN: Final = (
    b"exitspec-proofability-workspace-write-stripe-v1\x00"
)
_IDEMPOTENCY_DOMAIN: Final = (
    b"exitspec-proofability-workspace-idempotency-key-v1\x00"
)
_BINDING_DOMAIN: Final = b"exitspec-proofability-workspace-binding-v1\x00"
_REQUEST_DOMAIN: Final = b"exitspec-proofability-workspace-request-v1\x00"
_RECORD_DOMAIN: Final = b"exitspec-proofability-workspace-record-v1\x00"
_PROFILE_ID_BYTES: Final = PROFILE_ID.encode("ascii")
_PROFILE_VERSION_BYTES: Final = PROFILE_VERSION.encode("ascii")
_WRITE_STRIPE_COUNT: Final = 128


class ProofabilityWorkspaceErrorCode(str, Enum):
    INVALID_REQUEST = "INVALID_REQUEST"
    ORIGIN_FORBIDDEN = "ORIGIN_FORBIDDEN"
    POC_NOT_FOUND = "POC_NOT_FOUND"
    METHOD_NOT_ALLOWED = "METHOD_NOT_ALLOWED"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    PAYLOAD_TOO_LARGE = "PAYLOAD_TOO_LARGE"
    UNSUPPORTED_MEDIA_TYPE = "UNSUPPORTED_MEDIA_TYPE"
    PROFILE_UNSUPPORTED = "PROFILE_UNSUPPORTED"
    CAPACITY_EXHAUSTED = "CAPACITY_EXHAUSTED"
    WORKSPACE_UNAVAILABLE = "WORKSPACE_UNAVAILABLE"


class ProofabilityWorkspaceError(RuntimeError):
    """One closed workspace failure whose public projection is code-only."""

    def __init__(self, code: ProofabilityWorkspaceErrorCode) -> None:
        self.code = ProofabilityWorkspaceErrorCode(code)
        super().__init__(self.code.value)


def _fail(code: ProofabilityWorkspaceErrorCode) -> None:
    raise ProofabilityWorkspaceError(code)


_EDGE_WHITESPACE = frozenset(
    {
        *range(0x0009, 0x000E),
        0x0020,
        0x0085,
        0x00A0,
        0x1680,
        *range(0x2000, 0x200B),
        0x2028,
        0x2029,
        0x202F,
        0x205F,
        0x3000,
    }
)


def validate_workspace_scalar(value: object) -> tuple[str, bytes]:
    """Validate one exact non-normalized scalar and retain its UTF-8 bytes."""

    if type(value) is not str:
        _fail(ProofabilityWorkspaceErrorCode.INVALID_REQUEST)
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        _fail(ProofabilityWorkspaceErrorCode.INVALID_REQUEST)
    if not 1 <= len(encoded) <= 128:
        _fail(ProofabilityWorkspaceErrorCode.INVALID_REQUEST)
    points = tuple(ord(character) for character in value)
    if (
        not points
        or points[0] in _EDGE_WHITESPACE
        or points[-1] in _EDGE_WHITESPACE
        or any(
            point <= 0x001F
            or point == 0x007F
            or 0x0080 <= point <= 0x009F
            or 0xD800 <= point <= 0xDFFF
            for point in points
        )
    ):
        _fail(ProofabilityWorkspaceErrorCode.INVALID_REQUEST)
    return value, encoded


def _validate_poc_id(value: object) -> str:
    if type(value) is not str or _POC_ID_RE.fullmatch(value) is None:
        _fail(ProofabilityWorkspaceErrorCode.INVALID_REQUEST)
    return value


def _digest(domain: bytes, projection: dict[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(
        domain + canonical_json_bytes(projection)
    ).hexdigest()


def proofability_workspace_stripe_index(poc_id: str) -> int:
    """Return the frozen first-eight/big-endian/mod-128 write stripe."""

    validated = _validate_poc_id(poc_id)
    material = _WRITE_STRIPE_DOMAIN + validated.encode("utf-8", errors="strict")
    prefix = hashlib.sha256(material).digest()[:8]
    return int.from_bytes(prefix, byteorder="big", signed=False) % 128


def _key_digest(key: str) -> str:
    return _digest(_IDEMPOTENCY_DOMAIN, {"idempotency_key": key})


def _binding_fingerprint(
    poc_id: str, authority: ProofabilityFixtureAuthority
) -> str:
    return _digest(
        _BINDING_DOMAIN,
        {
            "poc_id": poc_id,
            "subject_digest": authority.expected_subject_digest,
            "scope_digest": authority.expected_scope_digest,
            "qualification_context_digest": (
                authority.expected_qualification_context_digest
            ),
            "profile_id": authority.expected_profile_id,
            "profile_version": authority.expected_profile_version,
            "capability_digest": authority.expected_capability_digest,
        },
    )


def _request_digest(
    poc_id: str,
    profile_id: str,
    profile_version: str,
    binding: str,
) -> str:
    return _digest(
        _REQUEST_DOMAIN,
        {
            "poc_id": poc_id,
            "profile_id": profile_id,
            "profile_version": profile_version,
            "input_binding_fingerprint": binding,
        },
    )


def _record_seal(operation: _Operation) -> str:
    return _digest(
        _RECORD_DOMAIN,
        {
            "poc_id": operation.poc_id,
            "request_digest": operation.request_digest,
            "input_binding_fingerprint": operation.input_binding_fingerprint,
            "proofability_report_digest": operation.proofability_report_digest,
        },
    )


@dataclass(frozen=True, slots=True)
class _WorkspaceLimits:
    latest_pocs: int = 128
    operations: int = 256
    idempotency_entries: int = 256
    pending: int = 64
    report_bytes_per_operation: int = 1_048_576
    aggregate_report_bytes: int = 16_777_216

    def validated(self) -> _WorkspaceLimits:
        production = (128, 256, 256, 64, 1_048_576, 16_777_216)
        values = (
            self.latest_pocs,
            self.operations,
            self.idempotency_entries,
            self.pending,
            self.report_bytes_per_operation,
            self.aggregate_report_bytes,
        )
        if any(type(value) is not int or value < 1 for value in values):
            raise ValueError("Workspace limits must be positive integers.")
        if any(value > maximum for value, maximum in zip(values, production, strict=True)):
            raise ValueError("Test limits may lower but never raise production limits.")
        return self


_PRODUCTION_LIMITS: Final = _WorkspaceLimits()


@dataclass(frozen=True, slots=True)
class _Operation:
    idempotency_key_digest: str
    poc_id: str
    request_digest: str
    input_binding_fingerprint: str
    canonical_report_bytes: bytes
    proofability_report_digest: str
    record_seal: str


@dataclass(frozen=True, slots=True)
class _IdempotencyEntry:
    request_digest: str
    operation: _Operation


@dataclass(frozen=True, slots=True)
class _Reservation:
    owner: object
    idempotency_key_digest: str
    poc_id: str
    request_digest: str
    input_binding_fingerprint: str
    report_bytes: int
    latest_slots: int


DraftGuard = Callable[
    [str, DraftPOCSnapshot], AbstractContextManager[object]
]
DescriptorResolver = Callable[..., ProducerCapabilityDescriptorV1]
Evaluator = Callable[..., ProofabilityReportV1]
Verifier = Callable[..., bool]


class _ProofabilityWorkspace:
    """Private bounded core; production construction is the closed factory below."""

    def __init__(
        self,
        *,
        draft_lookup: Callable[[str], DraftPOCSnapshot],
        draft_commit_guard: DraftGuard,
        fixture_resolver: Callable[[], ProofabilityFixtureAuthority],
        fixture_authorities: tuple[ProofabilityFixtureAuthority, ...],
        descriptor_resolver: DescriptorResolver = get_producer_capability_descriptor,
        evaluator: Evaluator = evaluate_proofability,
        verifier: Verifier = verify_proofability_report,
        limits: _WorkspaceLimits = _PRODUCTION_LIMITS,
    ) -> None:
        if not callable(draft_lookup) or not callable(draft_commit_guard):
            raise TypeError("Draft collaborators must be callable.")
        if not callable(fixture_resolver) or not callable(descriptor_resolver):
            raise TypeError("Fixture and descriptor collaborators must be callable.")
        if not callable(evaluator) or not callable(verifier):
            raise TypeError("PR5 collaborators must be callable.")
        if type(fixture_authorities) is not tuple or not fixture_authorities:
            raise ValueError("At least one immutable fixture authority is required.")
        if any(
            type(authority) is not ProofabilityFixtureAuthority
            for authority in fixture_authorities
        ):
            raise TypeError("Fixture authorities must use the exact frozen type.")
        self._draft_lookup = draft_lookup
        self._draft_commit_guard = draft_commit_guard
        self._fixture_resolver = fixture_resolver
        self._fixture_authorities = fixture_authorities
        self._descriptor_resolver = descriptor_resolver
        self._evaluator = evaluator
        self._verifier = verifier
        self._limits = limits.validated()
        self._write_stripes = tuple(RLock() for _ in range(_WRITE_STRIPE_COUNT))
        self._global_lock = RLock()
        self._operations: dict[str, _Operation] = {}
        self._idempotency: dict[str, _IdempotencyEntry] = {}
        self._latest_by_poc: dict[str, str] = {}
        self._pending: dict[str, _Reservation] = {}
        self._accepted_report_bytes = 0
        self._reserved_operation_slots = 0
        self._reserved_idempotency_slots = 0
        self._reserved_latest_slots = 0
        self._reserved_report_bytes = 0
        if len(self._write_stripes) != _WRITE_STRIPE_COUNT:
            raise RuntimeError("Workspace write-stripe cardinality drifted.")

    @property
    def write_stripe_count(self) -> int:
        return len(self._write_stripes)

    def require_active_poc(self, poc_id: str) -> None:
        """Require one active draft before serving the fixed browser shell."""

        self._resolve_active(_validate_poc_id(poc_id))

    def create(
        self,
        *,
        poc_id: str,
        profile_id: object,
        profile_version: object,
        idempotency_key: object,
    ) -> dict[str, Any]:
        validated_poc_id = _validate_poc_id(poc_id)
        profile_id_text, profile_id_bytes = validate_workspace_scalar(profile_id)
        profile_version_text, profile_version_bytes = validate_workspace_scalar(
            profile_version
        )
        key_text, _ = validate_workspace_scalar(idempotency_key)
        if not hmac.compare_digest(profile_id_bytes, _PROFILE_ID_BYTES) or not (
            hmac.compare_digest(profile_version_bytes, _PROFILE_VERSION_BYTES)
        ):
            _fail(ProofabilityWorkspaceErrorCode.PROFILE_UNSUPPORTED)
        key_hash = _key_digest(key_text)
        stripe = self._write_stripes[
            proofability_workspace_stripe_index(validated_poc_id)
        ]
        with stripe:
            snapshot, authority = self._resolve_active(validated_poc_id)
            descriptor = self._resolve_descriptor(authority)
            binding = _binding_fingerprint(validated_poc_id, authority)
            request_hash = _request_digest(
                validated_poc_id,
                profile_id_text,
                profile_version_text,
                binding,
            )
            replay_operation: _Operation | None = None
            reservation: _Reservation | None = None
            with self._global_lock:
                self._require_invariants_locked()
                prior = self._idempotency.get(key_hash)
                if prior is not None:
                    if not hmac.compare_digest(prior.request_digest, request_hash):
                        _fail(ProofabilityWorkspaceErrorCode.IDEMPOTENCY_CONFLICT)
                    replay_operation = prior.operation
                else:
                    pending = self._pending.get(key_hash)
                    if pending is not None:
                        if not hmac.compare_digest(
                            pending.request_digest, request_hash
                        ):
                            _fail(
                                ProofabilityWorkspaceErrorCode.IDEMPOTENCY_CONFLICT
                            )
                        _fail(ProofabilityWorkspaceErrorCode.WORKSPACE_UNAVAILABLE)
                    reservation = self._reserve_locked(
                        key_hash=key_hash,
                        poc_id=validated_poc_id,
                        request_hash=request_hash,
                        binding=binding,
                        report_bytes=(
                            authority.expected_canonical_report_byte_count
                        ),
                    )
            if replay_operation is not None:
                return self._accepted_replay(
                    snapshot=snapshot,
                    authority=authority,
                    operation=replay_operation,
                    key_hash=key_hash,
                    request_hash=request_hash,
                    binding=binding,
                )
            if reservation is None:  # pragma: no cover - closed decision tree
                _fail(ProofabilityWorkspaceErrorCode.WORKSPACE_UNAVAILABLE)
            return self._fresh(
                snapshot=snapshot,
                authority=authority,
                descriptor=descriptor,
                reservation=reservation,
            )

    def get(self, *, poc_id: str) -> dict[str, Any]:
        validated_poc_id = _validate_poc_id(poc_id)
        snapshot, current_authority = self._resolve_active(validated_poc_id)
        self._resolve_descriptor(current_authority)
        current_binding = _binding_fingerprint(
            validated_poc_id, current_authority
        )
        try:
            with self._draft_commit_guard(validated_poc_id, snapshot):
                with self._global_lock:
                    self._require_invariants_locked()
                    latest_key = self._latest_by_poc.get(validated_poc_id)
                    operation = None
                    if latest_key is not None:
                        operation = self._operations.get(latest_key)
                        entry = self._idempotency.get(latest_key)
                        if (
                            type(operation) is not _Operation
                            or type(entry) is not _IdempotencyEntry
                            or entry.operation is not operation
                        ):
                            _fail(
                                ProofabilityWorkspaceErrorCode.WORKSPACE_UNAVAILABLE
                            )
                if latest_key is None:
                    return self._success_projection(
                        poc_id=validated_poc_id,
                        report=None,
                        needs_replan=False,
                        reported_context_digest=None,
                        resolved_context_digest=(
                            current_authority.expected_qualification_context_digest
                        ),
                    )
                if operation is None:  # pragma: no cover - guarded above
                    _fail(ProofabilityWorkspaceErrorCode.WORKSPACE_UNAVAILABLE)
                report = self._validate_operation(
                    operation,
                    expected_poc_id=validated_poc_id,
                    expected_key=latest_key,
                )
                same_binding = hmac.compare_digest(
                    operation.input_binding_fingerprint, current_binding
                )
                return self._success_projection(
                    poc_id=validated_poc_id,
                    report=report if same_binding else None,
                    needs_replan=not same_binding,
                    reported_context_digest=report.qualification_context_digest,
                    resolved_context_digest=(
                        current_authority.expected_qualification_context_digest
                    ),
                )
        except ProofabilityWorkspaceError:
            raise
        except (DraftPOCCommitConflict, DraftPOCNotFound):
            _fail(ProofabilityWorkspaceErrorCode.WORKSPACE_UNAVAILABLE)
        except Exception:  # noqa: BLE001 - public boundary fails closed
            _fail(ProofabilityWorkspaceErrorCode.WORKSPACE_UNAVAILABLE)

    def _resolve_active(
        self, poc_id: str
    ) -> tuple[DraftPOCSnapshot, ProofabilityFixtureAuthority]:
        try:
            snapshot = self._draft_lookup(poc_id)
        except (DraftPOCNotFound, ValueError):
            _fail(ProofabilityWorkspaceErrorCode.POC_NOT_FOUND)
        except Exception:  # noqa: BLE001 - collaborator failure is sanitized
            _fail(ProofabilityWorkspaceErrorCode.WORKSPACE_UNAVAILABLE)
        if (
            type(snapshot) is not DraftPOCSnapshot
            or snapshot.poc_id != poc_id
            or snapshot.archive_state is not DraftPOCArchiveState.ACTIVE
        ):
            if (
                type(snapshot) is DraftPOCSnapshot
                and snapshot.poc_id == poc_id
                and snapshot.archive_state is DraftPOCArchiveState.ARCHIVED
            ):
                _fail(ProofabilityWorkspaceErrorCode.POC_NOT_FOUND)
            _fail(ProofabilityWorkspaceErrorCode.WORKSPACE_UNAVAILABLE)
        try:
            authority = self._fixture_resolver()
            self._require_known_authority(authority)
        except ProofabilityWorkspaceError:
            raise
        except Exception:  # noqa: BLE001 - fixture failure is sanitized
            _fail(ProofabilityWorkspaceErrorCode.WORKSPACE_UNAVAILABLE)
        return snapshot, authority

    def _resolve_descriptor(
        self, authority: ProofabilityFixtureAuthority
    ) -> ProducerCapabilityDescriptorV1:
        try:
            descriptor = self._descriptor_resolver(
                profile_id=authority.expected_profile_id,
                profile_version=authority.expected_profile_version,
            )
            if (
                type(descriptor) is not ProducerCapabilityDescriptorV1
                or not verify_producer_capability_descriptor(descriptor)
            ):
                _fail(ProofabilityWorkspaceErrorCode.WORKSPACE_UNAVAILABLE)
            actual = (
                descriptor.profile.profile_id,
                descriptor.profile.profile_version,
                producer_capability_digest(descriptor),
                descriptor.engine_adapter.engine_id,
                descriptor.engine_adapter.engine_version,
                descriptor.engine_adapter.adapter_id,
                descriptor.engine_adapter.adapter_version,
            )
            expected = (
                authority.expected_profile_id,
                authority.expected_profile_version,
                authority.expected_capability_digest,
                authority.expected_engine_id,
                authority.expected_engine_version,
                authority.expected_adapter_id,
                authority.expected_adapter_version,
            )
            if not all(
                hmac.compare_digest(left.encode("ascii"), right.encode("ascii"))
                for left, right in zip(actual, expected, strict=True)
            ):
                _fail(ProofabilityWorkspaceErrorCode.WORKSPACE_UNAVAILABLE)
            return descriptor
        except ProofabilityWorkspaceError:
            raise
        except Exception:  # noqa: BLE001 - registry failure is sanitized
            _fail(ProofabilityWorkspaceErrorCode.WORKSPACE_UNAVAILABLE)

    def _require_known_authority(
        self, authority: ProofabilityFixtureAuthority
    ) -> None:
        if type(authority) is not ProofabilityFixtureAuthority or not any(
            authority is candidate for candidate in self._fixture_authorities
        ):
            _fail(ProofabilityWorkspaceErrorCode.WORKSPACE_UNAVAILABLE)
        if (
            type(authority.subject) is not ServingSubjectManifestV1
            or type(authority.scope) is not QualificationScopeV1
            or type(authority.context) is not QualificationContextV1
            or type(authority.contract) is not POCContract
            or authority.contract.status is not ContractStatus.FROZEN
            or not verify_serving_subject_manifest(authority.subject)
            or not verify_qualification_scope(authority.scope)
            or not verify_qualification_context(authority.context)
            or not verify_contract_digest(authority.contract)
        ):
            _fail(ProofabilityWorkspaceErrorCode.WORKSPACE_UNAVAILABLE)
        actual = (
            serving_subject_digest(authority.subject),
            qualification_scope_digest(authority.scope),
            qualification_context_digest(authority.context),
            authority.contract.id,
            "sha256:" + contract_digest(authority.contract),
        )
        expected = (
            authority.expected_subject_digest,
            authority.expected_scope_digest,
            authority.expected_qualification_context_digest,
            authority.expected_contract_id,
            authority.expected_contract_canonical_digest,
        )
        if not all(
            hmac.compare_digest(left.encode("ascii"), right.encode("ascii"))
            for left, right in zip(actual, expected, strict=True)
        ):
            _fail(ProofabilityWorkspaceErrorCode.WORKSPACE_UNAVAILABLE)
        if (
            type(authority.expected_canonical_report_byte_count) is not int
            or not 0 < authority.expected_canonical_report_byte_count <= 1_048_576
        ):
            _fail(ProofabilityWorkspaceErrorCode.WORKSPACE_UNAVAILABLE)

    def _authority_for_binding(
        self, poc_id: str, binding: str
    ) -> ProofabilityFixtureAuthority:
        matches = []
        for authority in self._fixture_authorities:
            self._require_known_authority(authority)
            if hmac.compare_digest(
                _binding_fingerprint(poc_id, authority).encode("ascii"),
                binding.encode("ascii"),
            ):
                matches.append(authority)
        if len(matches) != 1:
            _fail(ProofabilityWorkspaceErrorCode.WORKSPACE_UNAVAILABLE)
        return matches[0]

    def _reserve_locked(
        self,
        *,
        key_hash: str,
        poc_id: str,
        request_hash: str,
        binding: str,
        report_bytes: int,
    ) -> _Reservation:
        latest_slots = 0 if poc_id in self._latest_by_poc else 1
        limits = self._limits
        if (
            report_bytes > limits.report_bytes_per_operation
            or len(self._operations) + self._reserved_operation_slots + 1
            > limits.operations
            or len(self._idempotency) + self._reserved_idempotency_slots + 1
            > limits.idempotency_entries
            or len(self._pending) + 1 > limits.pending
            or len(self._latest_by_poc) + self._reserved_latest_slots + latest_slots
            > limits.latest_pocs
            or self._accepted_report_bytes
            + self._reserved_report_bytes
            + report_bytes
            > limits.aggregate_report_bytes
        ):
            _fail(ProofabilityWorkspaceErrorCode.CAPACITY_EXHAUSTED)
        reservation = _Reservation(
            owner=object(),
            idempotency_key_digest=key_hash,
            poc_id=poc_id,
            request_digest=request_hash,
            input_binding_fingerprint=binding,
            report_bytes=report_bytes,
            latest_slots=latest_slots,
        )
        self._pending[key_hash] = reservation
        self._reserved_operation_slots += 1
        self._reserved_idempotency_slots += 1
        self._reserved_latest_slots += latest_slots
        self._reserved_report_bytes += report_bytes
        self._require_invariants_locked()
        return reservation

    def _fresh(
        self,
        *,
        snapshot: DraftPOCSnapshot,
        authority: ProofabilityFixtureAuthority,
        descriptor: ProducerCapabilityDescriptorV1,
        reservation: _Reservation,
    ) -> dict[str, Any]:
        published = False
        try:
            try:
                report = self._evaluator(
                    authority.subject,
                    authority.scope,
                    authority.context,
                    authority.contract,
                    descriptor,
                )
            except Exception:  # noqa: BLE001 - injected evaluator is untrusted
                _fail(ProofabilityWorkspaceErrorCode.WORKSPACE_UNAVAILABLE)
            try:
                verified = self._verifier(
                    report,
                    authority.subject,
                    authority.scope,
                    authority.context,
                    authority.contract,
                    descriptor,
                )
            except Exception:  # noqa: BLE001 - injected verifier is untrusted
                verified = False
            if verified is not True:
                _fail(ProofabilityWorkspaceErrorCode.WORKSPACE_UNAVAILABLE)
            try:
                canonical = serialize_proofability_report(report)
                parsed = self._validate_golden_report(
                    canonical,
                    report.proofability_report_digest,
                    authority,
                )
            except Exception:  # noqa: BLE001 - typed output fails closed
                _fail(ProofabilityWorkspaceErrorCode.WORKSPACE_UNAVAILABLE)
            if type(report) is not ProofabilityReportV1 or parsed != report:
                _fail(ProofabilityWorkspaceErrorCode.WORKSPACE_UNAVAILABLE)
            operation_without_seal = _Operation(
                idempotency_key_digest=reservation.idempotency_key_digest,
                poc_id=reservation.poc_id,
                request_digest=reservation.request_digest,
                input_binding_fingerprint=(
                    reservation.input_binding_fingerprint
                ),
                canonical_report_bytes=canonical,
                proofability_report_digest=parsed.proofability_report_digest,
                record_seal="sha256:" + "0" * 64,
            )
            operation = _Operation(
                idempotency_key_digest=operation_without_seal.idempotency_key_digest,
                poc_id=operation_without_seal.poc_id,
                request_digest=operation_without_seal.request_digest,
                input_binding_fingerprint=(
                    operation_without_seal.input_binding_fingerprint
                ),
                canonical_report_bytes=operation_without_seal.canonical_report_bytes,
                proofability_report_digest=(
                    operation_without_seal.proofability_report_digest
                ),
                record_seal=_record_seal(operation_without_seal),
            )
            try:
                with self._draft_commit_guard(reservation.poc_id, snapshot):
                    with self._global_lock:
                        self._require_invariants_locked()
                        if self._pending.get(reservation.idempotency_key_digest) is not (
                            reservation
                        ):
                            _fail(
                                ProofabilityWorkspaceErrorCode.WORKSPACE_UNAVAILABLE
                            )
                        if (
                            len(canonical) != reservation.report_bytes
                            or reservation.idempotency_key_digest in self._operations
                            or reservation.idempotency_key_digest in self._idempotency
                        ):
                            _fail(
                                ProofabilityWorkspaceErrorCode.WORKSPACE_UNAVAILABLE
                            )
                        self._operations[reservation.idempotency_key_digest] = operation
                        self._idempotency[reservation.idempotency_key_digest] = (
                            _IdempotencyEntry(
                                request_digest=reservation.request_digest,
                                operation=operation,
                            )
                        )
                        self._latest_by_poc[reservation.poc_id] = (
                            reservation.idempotency_key_digest
                        )
                        self._accepted_report_bytes += reservation.report_bytes
                        self._remove_reservation_locked(reservation)
                        published = True
                        self._require_invariants_locked()
                    return self._success_projection(
                        poc_id=reservation.poc_id,
                        report=parsed,
                        needs_replan=False,
                        reported_context_digest=(
                            parsed.qualification_context_digest
                        ),
                        resolved_context_digest=(
                            authority.expected_qualification_context_digest
                        ),
                        idempotent_replay=False,
                    )
            except (DraftPOCCommitConflict, DraftPOCNotFound):
                _fail(ProofabilityWorkspaceErrorCode.WORKSPACE_UNAVAILABLE)
            except ProofabilityWorkspaceError:
                raise
            except Exception:  # noqa: BLE001 - commit collaborator is sanitized
                _fail(ProofabilityWorkspaceErrorCode.WORKSPACE_UNAVAILABLE)
        finally:
            if not published:
                self._cleanup_owned_reservation(reservation)

    def _accepted_replay(
        self,
        *,
        snapshot: DraftPOCSnapshot,
        authority: ProofabilityFixtureAuthority,
        operation: _Operation,
        key_hash: str,
        request_hash: str,
        binding: str,
    ) -> dict[str, Any]:
        try:
            with self._draft_commit_guard(operation.poc_id, snapshot):
                with self._global_lock:
                    self._require_invariants_locked()
                    entry = self._idempotency.get(key_hash)
                    current = self._operations.get(key_hash)
                    if (
                        type(entry) is not _IdempotencyEntry
                        or current is not operation
                        or entry.operation is not operation
                    ):
                        _fail(
                            ProofabilityWorkspaceErrorCode.WORKSPACE_UNAVAILABLE
                        )
                report = self._validate_operation(
                    operation,
                    expected_poc_id=snapshot.poc_id,
                    expected_key=key_hash,
                    expected_request_digest=request_hash,
                    expected_binding=binding,
                )
                return self._success_projection(
                    poc_id=snapshot.poc_id,
                    report=report,
                    needs_replan=False,
                    reported_context_digest=report.qualification_context_digest,
                    resolved_context_digest=(
                        authority.expected_qualification_context_digest
                    ),
                    idempotent_replay=True,
                )
        except (DraftPOCCommitConflict, DraftPOCNotFound):
            _fail(ProofabilityWorkspaceErrorCode.WORKSPACE_UNAVAILABLE)
        except ProofabilityWorkspaceError:
            raise
        except Exception:  # noqa: BLE001 - replay collaborator is sanitized
            _fail(ProofabilityWorkspaceErrorCode.WORKSPACE_UNAVAILABLE)

    def _validate_operation(
        self,
        operation: _Operation,
        *,
        expected_poc_id: str,
        expected_key: str,
        expected_request_digest: str | None = None,
        expected_binding: str | None = None,
    ) -> ProofabilityReportV1:
        if (
            type(operation) is not _Operation
            or not hmac.compare_digest(operation.poc_id, expected_poc_id)
            or not hmac.compare_digest(
                operation.idempotency_key_digest, expected_key
            )
            or (
                expected_request_digest is not None
                and not hmac.compare_digest(
                    operation.request_digest, expected_request_digest
                )
            )
            or (
                expected_binding is not None
                and not hmac.compare_digest(
                    operation.input_binding_fingerprint, expected_binding
                )
            )
            or not hmac.compare_digest(operation.record_seal, _record_seal(operation))
        ):
            _fail(ProofabilityWorkspaceErrorCode.WORKSPACE_UNAVAILABLE)
        authority = self._authority_for_binding(
            operation.poc_id, operation.input_binding_fingerprint
        )
        return self._validate_golden_report(
            operation.canonical_report_bytes,
            operation.proofability_report_digest,
            authority,
        )

    def _validate_golden_report(
        self,
        canonical: bytes,
        stored_digest: str,
        authority: ProofabilityFixtureAuthority,
    ) -> ProofabilityReportV1:
        if type(canonical) is not bytes or type(stored_digest) is not str:
            _fail(ProofabilityWorkspaceErrorCode.WORKSPACE_UNAVAILABLE)
        actual_count = len(canonical)
        expected_count = authority.expected_canonical_report_byte_count
        if (
            actual_count > 1_048_576
            or expected_count < 0
            or not hmac.compare_digest(
                str(actual_count).encode("ascii"),
                str(expected_count).encode("ascii"),
            )
        ):
            _fail(ProofabilityWorkspaceErrorCode.WORKSPACE_UNAVAILABLE)
        try:
            report = parse_proofability_report(canonical)
            recomputed = proofability_report_digest(report)
        except Exception:  # noqa: BLE001 - stored bytes are untrusted
            _fail(ProofabilityWorkspaceErrorCode.WORKSPACE_UNAVAILABLE)
        digests = (
            stored_digest,
            report.proofability_report_digest,
            recomputed,
            authority.expected_proofability_report_digest,
        )
        if any(_DIGEST_RE.fullmatch(value) is None for value in digests) or not all(
            hmac.compare_digest(value.encode("ascii"), digests[0].encode("ascii"))
            for value in digests[1:]
        ):
            _fail(ProofabilityWorkspaceErrorCode.WORKSPACE_UNAVAILABLE)
        actual = (
            report.subject_digest,
            report.scope_digest,
            report.qualification_context_digest,
            report.protocol_id,
            report.protocol_version,
            report.contract_id,
            report.contract_canonical_digest,
            report.profile_id,
            report.profile_version,
            report.capability_digest,
            report.engine_id,
            report.engine_version,
            report.adapter_id,
            report.adapter_version,
        )
        expected = (
            authority.expected_subject_digest,
            authority.expected_scope_digest,
            authority.expected_qualification_context_digest,
            authority.expected_protocol_id,
            authority.expected_protocol_version,
            authority.expected_contract_id,
            authority.expected_contract_canonical_digest,
            authority.expected_profile_id,
            authority.expected_profile_version,
            authority.expected_capability_digest,
            authority.expected_engine_id,
            authority.expected_engine_version,
            authority.expected_adapter_id,
            authority.expected_adapter_version,
        )
        if not all(
            hmac.compare_digest(left.encode("ascii"), right.encode("ascii"))
            for left, right in zip(actual, expected, strict=True)
        ):
            _fail(ProofabilityWorkspaceErrorCode.WORKSPACE_UNAVAILABLE)
        return report

    def _success_projection(
        self,
        *,
        poc_id: str,
        report: ProofabilityReportV1 | None,
        needs_replan: bool,
        reported_context_digest: str | None,
        resolved_context_digest: str,
        idempotent_replay: bool | None = None,
    ) -> dict[str, Any]:
        report_payload = None if report is None else report.model_dump(mode="json")
        payload: dict[str, Any] = {
            "schema_version": WORKSPACE_RESPONSE_SCHEMA_VERSION,
            "poc_id": poc_id,
            "report": report_payload,
            "needs_replan": needs_replan,
            "reported_context_digest": reported_context_digest,
            "resolved_context_digest": resolved_context_digest,
            "profile_request": {
                "profile_id": PROFILE_ID,
                "profile_version": PROFILE_VERSION,
            },
            "context_source": {
                "kind": "PACKAGE_SYNTHETIC_FIXTURE",
                "fixture_id": (
                    "exitspec.synthetic-proofability-preflight.native-v1"
                ),
                "fixture_version": "v1",
                "poc_derived": False,
            },
            "storage": {
                "scope": "PROCESS_LOCAL",
                "survives_process_restart": False,
                "shared_across_workers": False,
            },
            "authority": {
                "deployment_authorized": False,
                "production_traffic_authorized": False,
                "traffic_expansion_authorized": False,
                "external_authorization_required": True,
            },
        }
        if idempotent_replay is not None:
            payload["idempotent_replay"] = idempotent_replay
        if report is not None and canonical_json_bytes(report_payload) != (
            serialize_proofability_report(report)
        ):
            _fail(ProofabilityWorkspaceErrorCode.WORKSPACE_UNAVAILABLE)
        return payload

    def _cleanup_owned_reservation(self, reservation: _Reservation) -> None:
        with self._global_lock:
            current = self._pending.get(reservation.idempotency_key_digest)
            if current is not reservation:
                return
            try:
                self._require_invariants_locked()
            except ProofabilityWorkspaceError:
                return
            self._remove_reservation_locked(reservation)
            self._require_invariants_locked()

    def _remove_reservation_locked(self, reservation: _Reservation) -> None:
        current = self._pending.get(reservation.idempotency_key_digest)
        if current is not reservation:
            _fail(ProofabilityWorkspaceErrorCode.WORKSPACE_UNAVAILABLE)
        del self._pending[reservation.idempotency_key_digest]
        self._reserved_operation_slots -= 1
        self._reserved_idempotency_slots -= 1
        self._reserved_latest_slots -= reservation.latest_slots
        self._reserved_report_bytes -= reservation.report_bytes

    def _require_invariants_locked(self) -> None:
        limits = self._limits
        if len(self._write_stripes) != _WRITE_STRIPE_COUNT:
            _fail(ProofabilityWorkspaceErrorCode.WORKSPACE_UNAVAILABLE)
        if (
            len(self._latest_by_poc) > limits.latest_pocs
            or len(self._operations) > limits.operations
            or len(self._idempotency) > limits.idempotency_entries
            or len(self._pending) > limits.pending
            or len(self._operations) != len(self._idempotency)
        ):
            _fail(ProofabilityWorkspaceErrorCode.WORKSPACE_UNAVAILABLE)
        accepted_bytes = 0
        for key, operation in self._operations.items():
            entry = self._idempotency.get(key)
            if (
                type(key) is not str
                or _DIGEST_RE.fullmatch(key) is None
                or type(operation) is not _Operation
                or operation.idempotency_key_digest != key
                or type(operation.canonical_report_bytes) is not bytes
                or len(operation.canonical_report_bytes)
                > limits.report_bytes_per_operation
                or type(entry) is not _IdempotencyEntry
                or entry.operation is not operation
                or entry.request_digest != operation.request_digest
            ):
                _fail(ProofabilityWorkspaceErrorCode.WORKSPACE_UNAVAILABLE)
            accepted_bytes += len(operation.canonical_report_bytes)
        for poc_id, key in self._latest_by_poc.items():
            operation = self._operations.get(key)
            if (
                _POC_ID_RE.fullmatch(poc_id) is None
                or type(operation) is not _Operation
                or operation.poc_id != poc_id
            ):
                _fail(ProofabilityWorkspaceErrorCode.WORKSPACE_UNAVAILABLE)
        reserved_latest = 0
        reserved_bytes = 0
        for key, reservation in self._pending.items():
            if (
                type(reservation) is not _Reservation
                or reservation.idempotency_key_digest != key
                or reservation.latest_slots not in {0, 1}
                or reservation.report_bytes < 1
                or reservation.report_bytes > limits.report_bytes_per_operation
                or key in self._operations
                or key in self._idempotency
            ):
                _fail(ProofabilityWorkspaceErrorCode.WORKSPACE_UNAVAILABLE)
            expected_latest = 0 if reservation.poc_id in self._latest_by_poc else 1
            if reservation.latest_slots != expected_latest:
                _fail(ProofabilityWorkspaceErrorCode.WORKSPACE_UNAVAILABLE)
            reserved_latest += reservation.latest_slots
            reserved_bytes += reservation.report_bytes
        if (
            accepted_bytes != self._accepted_report_bytes
            or self._reserved_operation_slots != len(self._pending)
            or self._reserved_idempotency_slots != len(self._pending)
            or self._reserved_latest_slots != reserved_latest
            or self._reserved_report_bytes != reserved_bytes
            or len(self._operations) + self._reserved_operation_slots
            > limits.operations
            or len(self._idempotency) + self._reserved_idempotency_slots
            > limits.idempotency_entries
            or len(self._latest_by_poc) + self._reserved_latest_slots
            > limits.latest_pocs
            or self._accepted_report_bytes + self._reserved_report_bytes
            > limits.aggregate_report_bytes
        ):
            _fail(ProofabilityWorkspaceErrorCode.WORKSPACE_UNAVAILABLE)


def create_production_proofability_workspace(
    *,
    draft_lookup: Callable[[str], DraftPOCSnapshot],
    draft_commit_guard: DraftGuard,
) -> _ProofabilityWorkspace:
    """Construct the closed production workspace with no fixture selector."""

    if len(PRODUCTION_FIXTURE_AUTHORITIES) != 1:
        raise RuntimeError("Production proofability fixture cardinality drifted.")
    return _ProofabilityWorkspace(
        draft_lookup=draft_lookup,
        draft_commit_guard=draft_commit_guard,
        fixture_resolver=production_fixture_authority,
        fixture_authorities=PRODUCTION_FIXTURE_AUTHORITIES,
    )


__all__ = [
    "WORKSPACE_RESPONSE_SCHEMA_VERSION",
    "ProofabilityWorkspaceError",
    "ProofabilityWorkspaceErrorCode",
    "create_production_proofability_workspace",
    "proofability_workspace_stripe_index",
    "validate_workspace_scalar",
]
