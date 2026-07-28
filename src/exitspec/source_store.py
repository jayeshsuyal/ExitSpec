"""Atomic in-memory storage for frozen Wave 2 synthetic source imports.

The store owns replay checks, source-version allocation, finalization, and
publication.  It deliberately has no parser, filesystem, network, agreement,
measurement, or verdict behavior.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from threading import RLock, local
from types import MappingProxyType
from typing import Callable, Iterator, Mapping, Never

from exitspec.source_models import (
    MANIFEST_ID,
    MANIFEST_VERSION,
    SOURCE_TYPE,
    PreparedSourceImport,
    PrivateSourceSerializationError,
    SourceEnvelope,
    SourceLinkedCandidate,
    SourceModelValidationError,
    SourceThreadBindingError,
    ThreadParentNotFoundError,
    finalize_source_envelope,
    validate_source_thread_binding,
)


class SourceImportOutcome(str, Enum):
    ACCEPTED = "accepted"
    ACCEPTED_NEW_VERSION = "accepted_new_version"
    DUPLICATE_REPLAY = "duplicate_replay"
    SOURCE_IDENTITY_CONFLICT = "source_identity_conflict"
    THREAD_PARENT_NOT_FOUND = "thread_parent_not_found"
    SOURCE_THREAD_BINDING_MISMATCH = "source_thread_binding_mismatch"
    SOURCE_LINK_VIOLATION = "source_link_violation"


class SourceStoreReentrancyError(RuntimeError):
    """Content-free denial for same-thread nested source transactions."""

    code = "source_store_transaction_reentry"

    def __init__(self) -> None:
        super().__init__(self.code)


@dataclass(frozen=True, slots=True)
class SourceImportReceipt:
    """The exact content-free terminal receipt allowed by manifest V1.0.1."""

    source_type: str
    manifest_id: str
    manifest_version: str
    fixture_case_id: str
    outcome_code: str
    source_version: int | None
    candidate_count: int

    def to_dict(self) -> dict[str, str | int | None]:
        return {
            "source_type": self.source_type,
            "manifest_id": self.manifest_id,
            "manifest_version": self.manifest_version,
            "fixture_case_id": self.fixture_case_id,
            "outcome_code": self.outcome_code,
            "source_version": self.source_version,
            "candidate_count": self.candidate_count,
        }

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )


@dataclass(frozen=True, slots=True)
class SourceImportResult:
    """A terminal receipt plus the accepted or replayed immutable envelope."""

    receipt: SourceImportReceipt
    envelope: SourceEnvelope | None


@dataclass(frozen=True, slots=True)
class SourceStoreCounts:
    """Safe numeric state summary; it contains no source identity or content."""

    thread_source_count: int
    source_version_count: int
    candidate_count: int
    idempotency_record_count: int
    accepted_write_transaction_count: int


class _PrivateIdempotencyRecord:
    """Synthetic-only replay fingerprint that refuses public representation."""

    __slots__ = (
        "__message_key",
        "__synthetic_fixture_sha256",
        "__source_id",
        "__source_version",
        "__version_id",
    )

    def __init__(
        self,
        *,
        message_key: str,
        synthetic_fixture_sha256: str,
        source_id: str,
        source_version: int,
        version_id: str,
    ) -> None:
        object.__setattr__(
            self,
            "_PrivateIdempotencyRecord__message_key",
            message_key,
        )
        object.__setattr__(
            self,
            "_PrivateIdempotencyRecord__synthetic_fixture_sha256",
            synthetic_fixture_sha256,
        )
        object.__setattr__(
            self,
            "_PrivateIdempotencyRecord__source_id",
            source_id,
        )
        object.__setattr__(
            self,
            "_PrivateIdempotencyRecord__source_version",
            source_version,
        )
        object.__setattr__(
            self,
            "_PrivateIdempotencyRecord__version_id",
            version_id,
        )

    def __setattr__(self, name: str, value: object) -> Never:
        raise PrivateSourceSerializationError()

    def __delattr__(self, name: str) -> Never:
        raise PrivateSourceSerializationError()

    def __repr__(self) -> str:
        return "_PrivateIdempotencyRecord(<private>)"

    def __str__(self) -> str:
        return repr(self)

    def __getstate__(self) -> Never:
        raise PrivateSourceSerializationError()

    def __reduce__(self) -> Never:
        raise PrivateSourceSerializationError()

    def __reduce_ex__(self, protocol: int) -> Never:
        raise PrivateSourceSerializationError()

    def _matches_fixture(self, synthetic_fixture_sha256: str) -> bool:
        return self.__synthetic_fixture_sha256 == synthetic_fixture_sha256

    def _envelope_key(self) -> tuple[str, int]:
        return self.__source_id, self.__source_version

    def _matches_envelope(self, envelope: SourceEnvelope) -> bool:
        return (
            self.__message_key == envelope.messages[-1].message_key
            and self.__source_id == envelope.source_id
            and self.__source_version == envelope.source_version
            and self.__version_id == envelope.version_id
        )


@dataclass(frozen=True, slots=True)
class _StoreState:
    root_sources: Mapping[str, str]
    latest_by_source: Mapping[str, SourceEnvelope]
    versions: Mapping[tuple[str, int], SourceEnvelope]
    candidates_by_version: Mapping[
        tuple[str, int],
        tuple[SourceLinkedCandidate, ...],
    ]
    idempotency_by_message: Mapping[str, _PrivateIdempotencyRecord]
    accepted_write_transaction_count: int

    @classmethod
    def empty(cls) -> "_StoreState":
        return cls(
            root_sources=MappingProxyType({}),
            latest_by_source=MappingProxyType({}),
            versions=MappingProxyType({}),
            candidates_by_version=MappingProxyType({}),
            idempotency_by_message=MappingProxyType({}),
            accepted_write_transaction_count=0,
        )


def _utc_second_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _format_utc_second(value: datetime) -> str:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
        or value.utcoffset().total_seconds() != 0
        or value.microsecond != 0
    ):
        raise SourceModelValidationError()
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class SourceStore:
    """Copy-on-write source transaction boundary for synthetic imports."""

    __slots__ = (
        "_clock",
        "_finalizer",
        "_lock",
        "_state",
        "_transaction_local",
    )

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] = _utc_second_now,
        finalizer: Callable[..., SourceEnvelope] = finalize_source_envelope,
    ) -> None:
        self._clock = clock
        self._finalizer = finalizer
        self._lock = RLock()
        self._state = _StoreState.empty()
        self._transaction_local = local()

    @contextmanager
    def _transaction_guard(self) -> Iterator[None]:
        if getattr(self._transaction_local, "active", False):
            raise SourceStoreReentrancyError()
        self._transaction_local.active = True
        try:
            yield
        finally:
            self._transaction_local.active = False

    def import_prepared(
        self,
        prepared_import: PreparedSourceImport,
    ) -> SourceImportResult:
        """Import one prepared source under a single atomic transaction."""

        fixture = prepared_import.approved_synthetic_fixture
        fixture_case_id = fixture.fixture_case_id

        with self._transaction_guard(), self._lock:
            state = self._state
            prepared = prepared_import.prepared_envelope
            message_key = prepared.message.message_key
            root_key = prepared_import.thread_root_message_key
            source_id = prepared.source_id

            try:
                validate_source_thread_binding(
                    prepared_import,
                    state.root_sources,
                )
                mapped_root = state.root_sources.get(root_key)
                if (
                    message_key == root_key
                    and mapped_root is not None
                    and mapped_root != source_id
                ):
                    raise SourceThreadBindingError()
            except SourceThreadBindingError:
                return self._refusal(
                    fixture_case_id,
                    SourceImportOutcome.SOURCE_THREAD_BINDING_MISMATCH,
                )
            except ThreadParentNotFoundError:
                return self._refusal(
                    fixture_case_id,
                    SourceImportOutcome.THREAD_PARENT_NOT_FOUND,
                )

            existing_record = state.idempotency_by_message.get(message_key)
            if existing_record is not None:
                if not existing_record._matches_fixture(
                    fixture.synthetic_fixture_sha256
                ):
                    return self._refusal(
                        fixture_case_id,
                        SourceImportOutcome.SOURCE_IDENTITY_CONFLICT,
                    )
                existing = state.versions.get(existing_record._envelope_key())
                if (
                    existing is None
                    or not existing_record._matches_envelope(existing)
                ):
                    return self._refusal(
                        fixture_case_id,
                        SourceImportOutcome.SOURCE_LINK_VIOLATION,
                    )
                return SourceImportResult(
                    receipt=self._receipt(
                        fixture_case_id,
                        SourceImportOutcome.DUPLICATE_REPLAY,
                        source_version=existing.source_version,
                        candidate_count=0,
                    ),
                    envelope=existing,
                )

            prior = state.latest_by_source.get(source_id)
            is_root = message_key == root_key
            if is_root and prior is not None:
                return self._refusal(
                    fixture_case_id,
                    SourceImportOutcome.SOURCE_IDENTITY_CONFLICT,
                )
            if not is_root and prior is None:
                return self._refusal(
                    fixture_case_id,
                    SourceImportOutcome.THREAD_PARENT_NOT_FOUND,
                )

            source_version = 1 if prior is None else prior.source_version + 1
            outcome = (
                SourceImportOutcome.ACCEPTED
                if prior is None
                else SourceImportOutcome.ACCEPTED_NEW_VERSION
            )
            try:
                ingested_at = _format_utc_second(self._clock())
                envelope = self._finalizer(
                    prepared,
                    source_version=source_version,
                    ingested_at=ingested_at,
                    prior_envelope=prior,
                )
                if (
                    envelope.source_id != source_id
                    or envelope.source_version != source_version
                    or envelope.messages[-1].message_key != message_key
                    or len(envelope.candidates)
                    != len(prepared.candidate_drafts)
                ):
                    raise SourceModelValidationError()
            except SourceModelValidationError:
                return self._refusal(
                    fixture_case_id,
                    SourceImportOutcome.SOURCE_LINK_VIOLATION,
                )

            record = _PrivateIdempotencyRecord(
                message_key=message_key,
                synthetic_fixture_sha256=fixture.synthetic_fixture_sha256,
                source_id=envelope.source_id,
                source_version=envelope.source_version,
                version_id=envelope.version_id,
            )
            envelope_key = (envelope.source_id, envelope.source_version)

            next_roots = dict(state.root_sources)
            if is_root:
                next_roots[root_key] = envelope.source_id
            next_latest = dict(state.latest_by_source)
            next_latest[envelope.source_id] = envelope
            next_versions = dict(state.versions)
            next_versions[envelope_key] = envelope
            next_candidates = dict(state.candidates_by_version)
            next_candidates[envelope_key] = envelope.candidates
            next_idempotency = dict(state.idempotency_by_message)
            next_idempotency[message_key] = record

            self._state = _StoreState(
                root_sources=MappingProxyType(next_roots),
                latest_by_source=MappingProxyType(next_latest),
                versions=MappingProxyType(next_versions),
                candidates_by_version=MappingProxyType(next_candidates),
                idempotency_by_message=MappingProxyType(next_idempotency),
                accepted_write_transaction_count=(
                    state.accepted_write_transaction_count + 1
                ),
            )

            return SourceImportResult(
                receipt=self._receipt(
                    fixture_case_id,
                    outcome,
                    source_version=envelope.source_version,
                    candidate_count=len(envelope.candidates),
                ),
                envelope=envelope,
            )

    def latest(self, source_id: str) -> SourceEnvelope | None:
        with self._lock:
            return self._state.latest_by_source.get(source_id)

    def version(
        self,
        source_id: str,
        source_version: int,
    ) -> SourceEnvelope | None:
        with self._lock:
            return self._state.versions.get((source_id, source_version))

    def history(self, source_id: str) -> tuple[SourceEnvelope, ...]:
        with self._lock:
            return tuple(
                envelope
                for (stored_source_id, _), envelope in sorted(
                    self._state.versions.items(),
                    key=lambda item: item[0][1],
                )
                if stored_source_id == source_id
            )

    def counts(self) -> SourceStoreCounts:
        with self._lock:
            state = self._state
            return SourceStoreCounts(
                thread_source_count=len(state.latest_by_source),
                source_version_count=len(state.versions),
                candidate_count=sum(
                    len(candidates)
                    for candidates in state.candidates_by_version.values()
                ),
                idempotency_record_count=len(
                    state.idempotency_by_message
                ),
                accepted_write_transaction_count=(
                    state.accepted_write_transaction_count
                ),
            )

    @staticmethod
    def _receipt(
        fixture_case_id: str,
        outcome: SourceImportOutcome,
        *,
        source_version: int | None,
        candidate_count: int,
    ) -> SourceImportReceipt:
        return SourceImportReceipt(
            source_type=SOURCE_TYPE,
            manifest_id=MANIFEST_ID,
            manifest_version=MANIFEST_VERSION,
            fixture_case_id=fixture_case_id,
            outcome_code=outcome.value,
            source_version=source_version,
            candidate_count=candidate_count,
        )

    @classmethod
    def _refusal(
        cls,
        fixture_case_id: str,
        outcome: SourceImportOutcome,
    ) -> SourceImportResult:
        return SourceImportResult(
            receipt=cls._receipt(
                fixture_case_id,
                outcome,
                source_version=None,
                candidate_count=0,
            ),
            envelope=None,
        )
