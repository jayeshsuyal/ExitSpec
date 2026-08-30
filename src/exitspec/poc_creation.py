"""Process-local draft POC creation with no workflow authority.

This module records only enough information to create and resume a draft POC.
Choosing a first source selects the next intake route; it does not ingest that
source, create a source envelope, approve requirements, confirm or freeze a
contract, execute a run, or issue a verdict.

The service is deliberately process-local. Its records are lost on restart and
are not shared between workers. A durable registry can replace it later while
keeping the immutable request and snapshot boundary models.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
import re
from threading import RLock
from typing import Callable, Literal, Optional, Tuple
from uuid import uuid4

from pydantic import Field, field_validator, model_validator

from .models import FrozenExitSpecModel


POC_ID_PATTERN = r"^poc_[a-z0-9][a-z0-9_-]{2,63}$"
_POC_ID_RE = re.compile(POC_ID_PATTERN)
_DEFAULT_MAX_DRAFTS = 1_024
_MAX_CONFIGURABLE_DRAFTS = 10_000
_MAX_IDEMPOTENCY_KEY_LENGTH = 200


class FirstSourceChoice(str, Enum):
    """How the user intends to supply the first POC input."""

    EMAIL = "EMAIL"
    MEETING = "MEETING"
    DOCUMENT = "DOCUMENT"
    EXISTING_CONTRACT = "EXISTING_CONTRACT"


class NextIntakeRoute(str, Enum):
    """Domain route keys; the web layer may map these to URLs."""

    EMAIL = "email"
    MEETING = "meeting"
    DOCUMENT = "document"
    EXISTING_CONTRACT = "existing_contract"


class SourceIngestionState(str, Enum):
    """A draft choice never claims that source ingestion has happened."""

    NOT_STARTED = "NOT_STARTED"


class DraftPOCArchiveState(str, Enum):
    """Creation-layer lifecycle only; COMPLETED requires outside authority."""

    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"


_ROUTE_BY_SOURCE = {
    FirstSourceChoice.EMAIL: NextIntakeRoute.EMAIL,
    FirstSourceChoice.MEETING: NextIntakeRoute.MEETING,
    FirstSourceChoice.DOCUMENT: NextIntakeRoute.DOCUMENT,
    FirstSourceChoice.EXISTING_CONTRACT: NextIntakeRoute.EXISTING_CONTRACT,
}


def _normalize_required_text(value: str) -> str:
    if type(value) is not str:
        raise ValueError("Value must be text.")
    normalized = value.strip()
    if not normalized:
        raise ValueError("Value must contain non-whitespace text.")
    return normalized


class DraftPOCCreateRequest(FrozenExitSpecModel):
    """Validated intent to create a draft and choose its first intake path."""

    display_name: str = Field(min_length=1, max_length=160)
    customer_label: str = Field(min_length=1, max_length=160)
    use_case: str = Field(min_length=1, max_length=500)
    owner: str = Field(min_length=1, max_length=160)
    first_source_choice: FirstSourceChoice
    poc_id: Optional[str] = Field(default=None, pattern=POC_ID_PATTERN)

    @field_validator(
        "display_name",
        "customer_label",
        "use_case",
        "owner",
        mode="before",
    )
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        return _normalize_required_text(value)


class DraftPOCSnapshot(FrozenExitSpecModel):
    """Immutable current state of a process-local draft POC."""

    poc_id: str = Field(pattern=POC_ID_PATTERN)
    display_name: str = Field(min_length=1, max_length=160)
    customer_label: str = Field(min_length=1, max_length=160)
    use_case: str = Field(min_length=1, max_length=500)
    owner: str = Field(min_length=1, max_length=160)
    first_source_choice: FirstSourceChoice
    next_intake_route: NextIntakeRoute
    source_ingestion_state: Literal[
        SourceIngestionState.NOT_STARTED
    ] = SourceIngestionState.NOT_STARTED
    created_at: datetime
    updated_at: datetime
    archive_state: DraftPOCArchiveState = DraftPOCArchiveState.ACTIVE
    archived_at: Optional[datetime] = None

    @field_validator(
        "display_name",
        "customer_label",
        "use_case",
        "owner",
        mode="before",
    )
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        return _normalize_required_text(value)

    @field_validator("created_at", "updated_at", "archived_at")
    @classmethod
    def require_timezone_aware_timestamps(
        cls,
        value: Optional[datetime],
    ) -> Optional[datetime]:
        if value is not None and (
            value.tzinfo is None or value.utcoffset() is None
        ):
            raise ValueError("Draft POC timestamps must be timezone-aware.")
        return value

    @model_validator(mode="after")
    def enforce_creation_boundaries(self) -> "DraftPOCSnapshot":
        if self.next_intake_route != _ROUTE_BY_SOURCE[self.first_source_choice]:
            raise ValueError(
                "next_intake_route must match the chosen first source."
            )
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot precede created_at.")
        if self.archive_state == DraftPOCArchiveState.ACTIVE:
            if self.archived_at is not None:
                raise ValueError("An active draft cannot have archived_at.")
        elif self.archived_at is None:
            raise ValueError("An archived draft requires archived_at.")
        elif self.archived_at < self.created_at:
            raise ValueError("archived_at cannot precede created_at.")
        elif self.updated_at != self.archived_at:
            raise ValueError(
                "Archiving must update updated_at to the archive timestamp."
            )
        return self


class DraftPOCCreationResult(FrozenExitSpecModel):
    """Create response that distinguishes a new write from an exact replay."""

    draft: DraftPOCSnapshot
    idempotent_replay: bool


class DraftPOCServiceSemantics(FrozenExitSpecModel):
    """Explicit non-durable guarantees of the temporary creation service."""

    storage_scope: Literal["PROCESS_LOCAL"] = "PROCESS_LOCAL"
    survives_process_restart: Literal[False] = False
    shared_across_workers: Literal[False] = False
    archived_records_retained_until_restart: Literal[True] = True
    max_drafts: int = Field(ge=1, le=_MAX_CONFIGURABLE_DRAFTS)


class DraftPOCCreationError(RuntimeError):
    """Base error for process-local draft creation."""


class DraftPOCIdempotencyConflict(DraftPOCCreationError):
    """An idempotency key was reused with a different create request."""


class DuplicateDraftPOCId(DraftPOCCreationError):
    """A different create operation requested an existing POC ID."""


class DraftPOCNotFound(DraftPOCCreationError, KeyError):
    """The requested draft is not present in this process."""


class DraftPOCCommitConflict(DraftPOCCreationError):
    """The draft changed while an owner-bound operation was being prepared."""


class DraftPOCCapacityExceeded(DraftPOCCreationError):
    """The bounded process-local store has reached its configured capacity."""


@dataclass(frozen=True, slots=True)
class _IdempotencyRecord:
    request_sha256: str
    poc_id: str


class _AuthoringCommitGuard:
    """A draft-owner lock token for an atomic source-bound publication."""

    __slots__ = ()

    def prepare(self) -> None:
        return None

    def commit(self) -> None:
        return None


def _default_clock() -> datetime:
    return datetime.now(timezone.utc)


def _default_poc_id_factory() -> str:
    return "poc_{0}".format(uuid4().hex)


def _validate_poc_id(poc_id: str) -> str:
    if type(poc_id) is not str or _POC_ID_RE.fullmatch(poc_id) is None:
        raise ValueError(
            "poc_id must match {0}.".format(POC_ID_PATTERN)
        )
    return poc_id


def _idempotency_key_digest(idempotency_key: str) -> str:
    if (
        type(idempotency_key) is not str
        or not idempotency_key.strip()
        or len(idempotency_key) > _MAX_IDEMPOTENCY_KEY_LENGTH
    ):
        raise ValueError(
            "idempotency_key must contain 1 to {0} characters.".format(
                _MAX_IDEMPOTENCY_KEY_LENGTH
            )
        )
    return hashlib.sha256(
        b"exitspec-draft-poc-idempotency-key-v1\x00"
        + idempotency_key.encode("utf-8")
    ).hexdigest()


def _request_digest(request: DraftPOCCreateRequest) -> str:
    canonical = json.dumps(
        request.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(
        b"exitspec-draft-poc-create-request-v1\x00" + canonical
    ).hexdigest()


class ProcessLocalDraftPOCService:
    """Thread-safe, bounded, non-durable draft POC creation service.

    Archived records remain lookupable and continue to consume capacity until
    process restart. There is intentionally no delete/reset method and no
    approval, customer-confirmation, contract-freeze, execution, or verdict API.
    """

    __slots__ = (
        "_clock",
        "_id_factory",
        "_idempotency",
        "_lock",
        "_max_drafts",
        "_records",
    )

    def __init__(
        self,
        *,
        max_drafts: int = _DEFAULT_MAX_DRAFTS,
        clock: Callable[[], datetime] = _default_clock,
        poc_id_factory: Callable[[], str] = _default_poc_id_factory,
    ) -> None:
        if (
            type(max_drafts) is not int
            or not 1 <= max_drafts <= _MAX_CONFIGURABLE_DRAFTS
        ):
            raise ValueError(
                "max_drafts must be an integer from 1 to {0}.".format(
                    _MAX_CONFIGURABLE_DRAFTS
                )
            )
        if not callable(clock):
            raise TypeError("clock must be callable.")
        if not callable(poc_id_factory):
            raise TypeError("poc_id_factory must be callable.")
        self._max_drafts = max_drafts
        self._clock = clock
        self._id_factory = poc_id_factory
        self._records: dict[str, DraftPOCSnapshot] = {}
        self._idempotency: dict[str, _IdempotencyRecord] = {}
        self._lock = RLock()

    @property
    def semantics(self) -> DraftPOCServiceSemantics:
        return DraftPOCServiceSemantics(max_drafts=self._max_drafts)

    def __len__(self) -> int:
        with self._lock:
            return len(self._records)

    def create(
        self,
        request: DraftPOCCreateRequest,
        *,
        idempotency_key: str,
    ) -> DraftPOCCreationResult:
        if type(request) is not DraftPOCCreateRequest:
            raise TypeError("request must be a DraftPOCCreateRequest.")

        key_digest = _idempotency_key_digest(idempotency_key)
        request_sha256 = _request_digest(request)

        with self._lock:
            prior = self._idempotency.get(key_digest)
            if prior is not None:
                if prior.request_sha256 != request_sha256:
                    raise DraftPOCIdempotencyConflict(
                        "Idempotency key reuse does not match the original "
                        "draft POC create request."
                    )
                return DraftPOCCreationResult(
                    draft=self._records[prior.poc_id],
                    idempotent_replay=True,
                )

            if len(self._records) >= self._max_drafts:
                raise DraftPOCCapacityExceeded(
                    "The process-local draft POC store is at capacity."
                )

            poc_id = _validate_poc_id(
                request.poc_id
                if request.poc_id is not None
                else self._id_factory()
            )
            if poc_id in self._records:
                raise DuplicateDraftPOCId(
                    "A draft POC with the requested ID already exists."
                )

            now = self._clock()
            draft = DraftPOCSnapshot(
                poc_id=poc_id,
                display_name=request.display_name,
                customer_label=request.customer_label,
                use_case=request.use_case,
                owner=request.owner,
                first_source_choice=request.first_source_choice,
                next_intake_route=_ROUTE_BY_SOURCE[
                    request.first_source_choice
                ],
                created_at=now,
                updated_at=now,
            )
            self._records[poc_id] = draft
            self._idempotency[key_digest] = _IdempotencyRecord(
                request_sha256=request_sha256,
                poc_id=poc_id,
            )
            return DraftPOCCreationResult(
                draft=draft,
                idempotent_replay=False,
            )

    def get(self, poc_id: str) -> DraftPOCSnapshot:
        validated_id = _validate_poc_id(poc_id)
        with self._lock:
            try:
                return self._records[validated_id]
            except KeyError as error:
                raise DraftPOCNotFound(
                    "Draft POC is not present in this process."
                ) from error

    def ids(self) -> Tuple[str, ...]:
        with self._lock:
            return tuple(sorted(self._records))

    def snapshots(self) -> Tuple[DraftPOCSnapshot, ...]:
        with self._lock:
            return tuple(self._records[poc_id] for poc_id in sorted(self._records))

    @contextmanager
    def authoring_commit_guard(
        self,
        poc_id: str,
        expected_draft: DraftPOCSnapshot,
    ):
        """Hold the draft owner lock while an A3 publication is prepared.

        This guard is deliberately lock-only: A3 does not mutate draft state.
        The exact immutable snapshot is checked while the lock is held, so an
        archive or other draft update cannot pass a final unlocked read and
        then race the publication.
        """

        validated_id = _validate_poc_id(poc_id)
        if (
            type(expected_draft) is not DraftPOCSnapshot
            or expected_draft.poc_id != validated_id
        ):
            raise DraftPOCCommitConflict(
                "The draft owner snapshot does not match the requested POC."
            )
        with self._lock:
            try:
                current = self._records[validated_id]
            except KeyError as error:
                raise DraftPOCNotFound(
                    "Draft POC is not present in this process."
                ) from error
            if current.archive_state != DraftPOCArchiveState.ACTIVE:
                raise DraftPOCNotFound(
                    "Archived draft POCs cannot accept assisted authoring."
                )
            if current != expected_draft:
                raise DraftPOCCommitConflict(
                    "The draft changed during assisted authoring."
                )
            yield _AuthoringCommitGuard()

    def archive(self, poc_id: str) -> DraftPOCSnapshot:
        """Archive a draft without deleting it or granting completion status."""

        validated_id = _validate_poc_id(poc_id)
        with self._lock:
            try:
                current = self._records[validated_id]
            except KeyError as error:
                raise DraftPOCNotFound(
                    "Draft POC is not present in this process."
                ) from error
            if current.archive_state == DraftPOCArchiveState.ARCHIVED:
                return current

            archived_at = self._clock()
            payload = current.model_dump(mode="python")
            payload.update(
                {
                    "updated_at": archived_at,
                    "archive_state": DraftPOCArchiveState.ARCHIVED,
                    "archived_at": archived_at,
                }
            )
            archived = DraftPOCSnapshot.model_validate(payload)
            self._records[validated_id] = archived
            return archived


__all__ = [
    "DraftPOCArchiveState",
    "DraftPOCCapacityExceeded",
    "DraftPOCCommitConflict",
    "DraftPOCCreateRequest",
    "DraftPOCCreationError",
    "DraftPOCCreationResult",
    "DraftPOCIdempotencyConflict",
    "DraftPOCNotFound",
    "DraftPOCServiceSemantics",
    "DraftPOCSnapshot",
    "DuplicateDraftPOCId",
    "FirstSourceChoice",
    "NextIntakeRoute",
    "ProcessLocalDraftPOCService",
    "SourceIngestionState",
]
