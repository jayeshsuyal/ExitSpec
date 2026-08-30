"""Process-local, provider-neutral source attachment for draft POCs.

This module accepts only adapter-prepared, redacted, normalized source content.
It attaches that content to a real ACTIVE ``DraftPOCSnapshot`` without mutating
the draft service. Attached candidates remain untrusted ``NEEDS_REVIEW`` input;
this module has no approval, confirmation, contract, execution, evidence, or
verdict authority.

Storage is deliberately process-local, bounded, and non-durable. Records are
lost on restart and are not shared between workers. Source history is
append-only: changed content for an existing external identity must explicitly
name the latest ``source_id`` in ``revises_source_id``.
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
from typing import Any, Callable, Literal, Mapping, Optional, Self, Tuple
import unicodedata
from uuid import uuid4

from pydantic import Field, field_validator, model_validator

from .models import FrozenExitSpecModel, SHA256_PATTERN
from .poc_creation import (
    DraftPOCArchiveState,
    DraftPOCSnapshot,
    POC_ID_PATTERN,
)


SOURCE_ID_PATTERN = r"^src_[a-z0-9][a-z0-9_-]{2,63}$"
CANDIDATE_ID_PATTERN = r"^cand_[a-z0-9][a-z0-9_-]{2,63}$"
EXTERNAL_ID_PATTERN = r"^[a-z][a-z0-9._:-]{2,127}$"
ADAPTER_NAME_PATTERN = r"^[a-z][a-z0-9_.-]{1,63}$"
VERSION_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.+-]{0,63}$"

_POC_ID_RE = re.compile(POC_ID_PATTERN)
_SOURCE_ID_RE = re.compile(SOURCE_ID_PATTERN)
_EXTERNAL_ID_RE = re.compile(EXTERNAL_ID_PATTERN)
_MAX_REDACTED_TEXT_LENGTH = 64_000
_MAX_SOURCE_QUOTE_LENGTH = 4_000
_MAX_NORMALIZED_CLAIM_LENGTH = 2_000
_MAX_PREPARED_CANDIDATES = 64
_MAX_IDEMPOTENCY_KEY_LENGTH = 200
_DEFAULT_MAX_POCS = 1_024
_DEFAULT_MAX_SOURCES_PER_POC = 128
_DEFAULT_MAX_CANDIDATES_PER_SOURCE = 64
_DEFAULT_MAX_IDEMPOTENCY_RECORDS = 16_384
_MAX_CONFIGURABLE_POCS = 10_000
_MAX_CONFIGURABLE_SOURCES_PER_POC = 1_024
_MAX_CONFIGURABLE_IDEMPOTENCY_RECORDS = 100_000

_RAW_EMAIL_RE = re.compile(
    r"(?i)(?<![A-Z0-9._%+-])[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}"
    r"(?![A-Z0-9.-])"
)
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|client[_-]?secret|"
    r"password|passwd|secret)\b\s*[:=]\s*(?!\[SECRET\])\S+"
)
_BEARER_RE = re.compile(r"(?i)\bbearer\s+(?!\[SECRET\])[A-Za-z0-9._~+/=-]{8,}")
_TOKEN_PREFIX_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:sk|fw)_[A-Za-z0-9_-]{10,}(?![A-Za-z0-9])"
)
_PHONE_CANDIDATE_RE = re.compile(
    r"(?<![A-Za-z0-9])\+?\d(?:[\d ()-]{6,}\d)(?![A-Za-z0-9])"
)


class SourceKind(str, Enum):
    """Provider-neutral source categories available to a POC."""

    EMAIL = "EMAIL"
    MEETING = "MEETING"
    DOCUMENT = "DOCUMENT"
    EXISTING_CONTRACT = "EXISTING_CONTRACT"


class CandidateState(str, Enum):
    """A prepared candidate has no state beyond requiring human review."""

    NEEDS_REVIEW = "NEEDS_REVIEW"


class SourceAttachDisposition(str, Enum):
    """Machine-readable result of one source attach request."""

    CREATED = "CREATED"
    IDEMPOTENT_REPLAY = "IDEMPOTENT_REPLAY"
    IDENTITY_REPLAY = "IDENTITY_REPLAY"


class _FrozenPOCSourceModel(FrozenExitSpecModel):
    """Frozen model whose copy helpers cannot bypass boundary validation."""

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        if not update:
            return super().model_copy(deep=deep)
        payload = self.model_dump(mode="python")
        payload.update(dict(update))
        return type(self).model_validate(payload)

    def copy(
        self,
        *,
        include: Any = None,
        exclude: Any = None,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        if include is not None or exclude is not None:
            raise ValueError(
                "include/exclude copies are not supported at this boundary."
            )
        return self.model_copy(update=update, deep=deep)


def _normalize_text(
    value: object,
    *,
    field_name: str,
    max_length: int,
    strip: bool,
) -> str:
    if type(value) is not str:
        raise ValueError("{0} must be text.".format(field_name))
    normalized = unicodedata.normalize(
        "NFC",
        value.replace("\r\n", "\n").replace("\r", "\n"),
    )
    if strip:
        normalized = normalized.strip()
    if not normalized:
        raise ValueError("{0} must contain text.".format(field_name))
    if len(normalized) > max_length:
        raise ValueError("{0} exceeds its bounded size.".format(field_name))
    for character in normalized:
        if character == "\n":
            continue
        if unicodedata.category(character).startswith("C"):
            raise ValueError(
                "{0} contains a forbidden control character.".format(field_name)
            )
    return normalized


def _contains_raw_phone(value: str) -> bool:
    for match in _PHONE_CANDIDATE_RE.finditer(value):
        digit_count = sum(character.isdigit() for character in match.group(0))
        if 8 <= digit_count <= 15:
            return True
    return False


def _require_redacted_text(value: str) -> str:
    if _RAW_EMAIL_RE.search(value):
        raise ValueError("redacted_text contains a raw email; use [EMAIL].")
    if (
        _SECRET_ASSIGNMENT_RE.search(value)
        or _BEARER_RE.search(value)
        or _TOKEN_PREFIX_RE.search(value)
    ):
        raise ValueError("redacted_text contains a raw secret; use [SECRET].")
    if _contains_raw_phone(value):
        raise ValueError("redacted_text contains a raw phone number; use [PHONE].")
    return value


def _require_timezone_aware(
    value: datetime,
    *,
    field_name: str,
) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("{0} must be timezone-aware.".format(field_name))
    return value


class PreparedRequirementCandidate(_FrozenPOCSourceModel):
    """Adapter-prepared proposal bound to a quote in redacted source text."""

    candidate_id: str = Field(pattern=CANDIDATE_ID_PATTERN)
    source_quote: str = Field(
        min_length=1,
        max_length=_MAX_SOURCE_QUOTE_LENGTH,
    )
    normalized_claim: str = Field(
        min_length=1,
        max_length=_MAX_NORMALIZED_CLAIM_LENGTH,
    )
    state: Literal[CandidateState.NEEDS_REVIEW] = CandidateState.NEEDS_REVIEW

    @field_validator("source_quote", mode="before")
    @classmethod
    def normalize_source_quote(cls, value: object) -> str:
        return _normalize_text(
            value,
            field_name="source_quote",
            max_length=_MAX_SOURCE_QUOTE_LENGTH,
            strip=True,
        )

    @field_validator("normalized_claim", mode="before")
    @classmethod
    def normalize_claim(cls, value: object) -> str:
        return _normalize_text(
            value,
            field_name="normalized_claim",
            max_length=_MAX_NORMALIZED_CLAIM_LENGTH,
            strip=True,
        )


class PreparedPOCSource(_FrozenPOCSourceModel):
    """Redacted source output accepted from a separately trusted adapter.

    ``revises_source_id`` is the sole revision mechanism. It must be absent for
    a new external identity. Changed content for an existing identity must name
    the latest attached source, preventing stale or silent overwrite.
    """

    kind: SourceKind
    external_id: str = Field(pattern=EXTERNAL_ID_PATTERN)
    redacted_text: str = Field(
        min_length=1,
        max_length=_MAX_REDACTED_TEXT_LENGTH,
    )
    content_sha256: str = Field(pattern=SHA256_PATTERN)
    candidates: Tuple[PreparedRequirementCandidate, ...] = Field(
        default=(),
        max_length=_MAX_PREPARED_CANDIDATES,
    )
    adapter_name: str = Field(pattern=ADAPTER_NAME_PATTERN)
    adapter_version: str = Field(pattern=VERSION_PATTERN)
    redaction_policy_version: str = Field(pattern=VERSION_PATTERN)
    observed_at: datetime
    revises_source_id: Optional[str] = Field(
        default=None,
        pattern=SOURCE_ID_PATTERN,
    )

    @field_validator("external_id", mode="before")
    @classmethod
    def normalize_external_id(cls, value: object) -> str:
        if type(value) is not str:
            raise ValueError("external_id must be text.")
        normalized = unicodedata.normalize("NFC", value.strip())
        if _EXTERNAL_ID_RE.fullmatch(normalized) is None:
            raise ValueError("external_id has an invalid format.")
        return normalized

    @field_validator("redacted_text", mode="before")
    @classmethod
    def normalize_redacted_text(cls, value: object) -> str:
        normalized = _normalize_text(
            value,
            field_name="redacted_text",
            max_length=_MAX_REDACTED_TEXT_LENGTH,
            strip=False,
        )
        return _require_redacted_text(normalized)

    @field_validator(
        "adapter_name",
        "adapter_version",
        "redaction_policy_version",
        mode="before",
    )
    @classmethod
    def normalize_safe_provenance(cls, value: object) -> str:
        if type(value) is not str:
            raise ValueError("Source provenance fields must be text.")
        return unicodedata.normalize("NFC", value.strip())

    @field_validator("observed_at")
    @classmethod
    def validate_observed_at(cls, value: datetime) -> datetime:
        return _require_timezone_aware(
            value,
            field_name="observed_at",
        )

    @model_validator(mode="after")
    def validate_digest_candidates_and_redaction(self) -> "PreparedPOCSource":
        expected_digest = hashlib.sha256(self.redacted_text.encode("utf-8")).hexdigest()
        if self.content_sha256 != expected_digest:
            raise ValueError("content_sha256 does not match normalized redacted_text.")

        candidate_ids: set[str] = set()
        for candidate in self.candidates:
            if candidate.candidate_id in candidate_ids:
                raise ValueError("Prepared candidate IDs must be unique per source.")
            candidate_ids.add(candidate.candidate_id)
            if candidate.source_quote not in self.redacted_text:
                raise ValueError(
                    "Every candidate source_quote must occur in redacted_text."
                )
            _require_redacted_text(candidate.source_quote)
            _require_redacted_text(candidate.normalized_claim)
        return self


class AttachedRequirementCandidate(_FrozenPOCSourceModel):
    """Immutable review candidate with service-owned source bindings."""

    candidate_id: str = Field(pattern=CANDIDATE_ID_PATTERN)
    poc_id: str = Field(pattern=POC_ID_PATTERN)
    source_id: str = Field(pattern=SOURCE_ID_PATTERN)
    source_sequence: int = Field(ge=1)
    source_quote: str = Field(
        min_length=1,
        max_length=_MAX_SOURCE_QUOTE_LENGTH,
    )
    normalized_claim: str = Field(
        min_length=1,
        max_length=_MAX_NORMALIZED_CLAIM_LENGTH,
    )
    state: Literal[CandidateState.NEEDS_REVIEW] = CandidateState.NEEDS_REVIEW

    @field_validator("source_quote", mode="before")
    @classmethod
    def normalize_source_quote(cls, value: object) -> str:
        return _require_redacted_text(
            _normalize_text(
                value,
                field_name="source_quote",
                max_length=_MAX_SOURCE_QUOTE_LENGTH,
                strip=True,
            )
        )

    @field_validator("normalized_claim", mode="before")
    @classmethod
    def normalize_claim(cls, value: object) -> str:
        return _require_redacted_text(
            _normalize_text(
                value,
                field_name="normalized_claim",
                max_length=_MAX_NORMALIZED_CLAIM_LENGTH,
                strip=True,
            )
        )


class POCSourceSnapshot(_FrozenPOCSourceModel):
    """Immutable, append-only source revision attached beneath one POC."""

    poc_id: str = Field(pattern=POC_ID_PATTERN)
    source_id: str = Field(pattern=SOURCE_ID_PATTERN)
    source_sequence: int = Field(ge=1)
    source_revision: int = Field(ge=1)
    kind: SourceKind
    external_id: str = Field(pattern=EXTERNAL_ID_PATTERN)
    redacted_text: str = Field(
        min_length=1,
        max_length=_MAX_REDACTED_TEXT_LENGTH,
    )
    content_sha256: str = Field(pattern=SHA256_PATTERN)
    candidates: Tuple[AttachedRequirementCandidate, ...] = Field(
        max_length=_MAX_PREPARED_CANDIDATES,
    )
    adapter_name: str = Field(pattern=ADAPTER_NAME_PATTERN)
    adapter_version: str = Field(pattern=VERSION_PATTERN)
    redaction_policy_version: str = Field(pattern=VERSION_PATTERN)
    observed_at: datetime
    attached_at: datetime
    revises_source_id: Optional[str] = Field(
        default=None,
        pattern=SOURCE_ID_PATTERN,
    )

    @field_validator("redacted_text", mode="before")
    @classmethod
    def normalize_redacted_text(cls, value: object) -> str:
        return _require_redacted_text(
            _normalize_text(
                value,
                field_name="redacted_text",
                max_length=_MAX_REDACTED_TEXT_LENGTH,
                strip=False,
            )
        )

    @field_validator("observed_at", "attached_at")
    @classmethod
    def validate_timestamps(cls, value: datetime) -> datetime:
        return _require_timezone_aware(
            value,
            field_name="Source timestamp",
        )

    @model_validator(mode="after")
    def validate_service_owned_bindings(self) -> "POCSourceSnapshot":
        expected_digest = hashlib.sha256(self.redacted_text.encode("utf-8")).hexdigest()
        if self.content_sha256 != expected_digest:
            raise ValueError("content_sha256 does not match normalized redacted_text.")
        if self.source_revision == 1 and self.revises_source_id is not None:
            raise ValueError("A first source revision cannot revise another source.")
        if self.source_revision > 1 and self.revises_source_id is None:
            raise ValueError("A later source revision must name its predecessor.")
        candidate_ids: set[str] = set()
        for candidate in self.candidates:
            if candidate.candidate_id in candidate_ids:
                raise ValueError("Attached candidate IDs must be unique per source.")
            candidate_ids.add(candidate.candidate_id)
            if (
                candidate.poc_id != self.poc_id
                or candidate.source_id != self.source_id
                or candidate.source_sequence != self.source_sequence
            ):
                raise ValueError(
                    "Candidate source bindings must match the source snapshot."
                )
            if candidate.source_quote not in self.redacted_text:
                raise ValueError(
                    "Every candidate source_quote must occur in redacted_text."
                )
        return self


class POCSourceAttachmentResult(_FrozenPOCSourceModel):
    """Attach response distinguishing a new write from safe replay."""

    source: POCSourceSnapshot
    disposition: SourceAttachDisposition

    @property
    def created(self) -> bool:
        return self.disposition == SourceAttachDisposition.CREATED

    @property
    def replayed(self) -> bool:
        return not self.created


class POCSourceServiceSemantics(_FrozenPOCSourceModel):
    """Machine-readable temporary-storage and authority boundaries."""

    storage_scope: Literal["PROCESS_LOCAL"] = "PROCESS_LOCAL"
    survives_process_restart: Literal[False] = False
    shared_across_workers: Literal[False] = False
    append_only_source_history: Literal[True] = True
    prepared_candidates_are_review_input_only: Literal[True] = True
    max_pocs: int = Field(ge=1, le=_MAX_CONFIGURABLE_POCS)
    max_sources_per_poc: int = Field(
        ge=1,
        le=_MAX_CONFIGURABLE_SOURCES_PER_POC,
    )
    max_candidates_per_source: int = Field(
        ge=1,
        le=_MAX_PREPARED_CANDIDATES,
    )
    max_idempotency_records: int = Field(
        ge=1,
        le=_MAX_CONFIGURABLE_IDEMPOTENCY_RECORDS,
    )


class POCSourceError(RuntimeError):
    """Base class for safe source-attachment failures."""


class POCSourceDraftUnavailable(POCSourceError):
    """The draft does not exist or the lookup boundary failed."""


class POCSourceDraftArchived(POCSourceError):
    """An archived draft cannot accept additional sources."""


class POCSourceIdempotencyConflict(POCSourceError):
    """An idempotency key was reused for a different attach request."""


class POCSourceRevisionRequired(POCSourceError):
    """Changed identity content did not explicitly revise the latest source."""


class POCSourceStaleRevision(POCSourceError):
    """A revision attempted to name a non-current predecessor."""


class POCSourceCapacityExceeded(POCSourceError):
    """A bounded process-local capacity was reached."""


class POCSourceNotFound(POCSourceError, KeyError):
    """A requested attached source is absent."""


class DuplicatePOCSourceId(POCSourceError):
    """The injected source ID factory produced an existing identifier."""


@dataclass(frozen=True, slots=True)
class _IdempotencyRecord:
    poc_id: str
    request_sha256: str
    source_id: str


class _AuthoringCommitGuard:
    """A source-owner lock token for an atomic source-bound publication."""

    __slots__ = ()

    def prepare(self) -> None:
        return None

    def commit(self) -> None:
        return None


def _default_clock() -> datetime:
    return datetime.now(timezone.utc)


def _default_source_id_factory() -> str:
    return "src_{0}".format(uuid4().hex)


def _validate_poc_id(poc_id: object) -> str:
    if type(poc_id) is not str or _POC_ID_RE.fullmatch(poc_id) is None:
        raise ValueError("poc_id has an invalid format.")
    return poc_id


def _validate_source_id(source_id: object) -> str:
    if type(source_id) is not str or _SOURCE_ID_RE.fullmatch(source_id) is None:
        raise ValueError("source_id has an invalid format.")
    return source_id


def _idempotency_key_digest(idempotency_key: object) -> str:
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
        b"exitspec-poc-source-idempotency-key-v1\x00" + idempotency_key.encode("utf-8")
    ).hexdigest()


def _request_digest(poc_id: str, source: PreparedPOCSource) -> str:
    canonical = json.dumps(
        {
            "poc_id": poc_id,
            "prepared_source": source.model_dump(mode="json"),
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(
        b"exitspec-poc-source-attach-request-v1\x00" + canonical
    ).hexdigest()


class ProcessLocalPOCSourceService:
    """Thread-safe, bounded, append-only source attachment service."""

    __slots__ = (
        "_by_id",
        "_clock",
        "_draft_lookup",
        "_id_factory",
        "_idempotency",
        "_identity_latest",
        "_lock",
        "_max_candidates_per_source",
        "_max_idempotency_records",
        "_max_pocs",
        "_max_sources_per_poc",
        "_sources_by_poc",
    )

    def __init__(
        self,
        *,
        draft_lookup: Callable[[str], DraftPOCSnapshot],
        max_pocs: int = _DEFAULT_MAX_POCS,
        max_sources_per_poc: int = _DEFAULT_MAX_SOURCES_PER_POC,
        max_candidates_per_source: int = (_DEFAULT_MAX_CANDIDATES_PER_SOURCE),
        max_idempotency_records: int = _DEFAULT_MAX_IDEMPOTENCY_RECORDS,
        clock: Callable[[], datetime] = _default_clock,
        source_id_factory: Callable[[], str] = _default_source_id_factory,
    ) -> None:
        if not callable(draft_lookup):
            raise TypeError("draft_lookup must be callable.")
        if type(max_pocs) is not int or not 1 <= max_pocs <= _MAX_CONFIGURABLE_POCS:
            raise ValueError("max_pocs is outside its supported bounds.")
        if (
            type(max_sources_per_poc) is not int
            or not 1 <= max_sources_per_poc <= _MAX_CONFIGURABLE_SOURCES_PER_POC
        ):
            raise ValueError("max_sources_per_poc is outside its supported bounds.")
        if (
            type(max_candidates_per_source) is not int
            or not 1 <= max_candidates_per_source <= _MAX_PREPARED_CANDIDATES
        ):
            raise ValueError(
                "max_candidates_per_source is outside its supported bounds."
            )
        if (
            type(max_idempotency_records) is not int
            or not 1 <= max_idempotency_records <= _MAX_CONFIGURABLE_IDEMPOTENCY_RECORDS
        ):
            raise ValueError("max_idempotency_records is outside its supported bounds.")
        if not callable(clock):
            raise TypeError("clock must be callable.")
        if not callable(source_id_factory):
            raise TypeError("source_id_factory must be callable.")

        self._draft_lookup = draft_lookup
        self._max_pocs = max_pocs
        self._max_sources_per_poc = max_sources_per_poc
        self._max_candidates_per_source = max_candidates_per_source
        self._max_idempotency_records = max_idempotency_records
        self._clock = clock
        self._id_factory = source_id_factory
        self._sources_by_poc: dict[str, Tuple[POCSourceSnapshot, ...]] = {}
        self._by_id: dict[tuple[str, str], POCSourceSnapshot] = {}
        self._identity_latest: dict[
            tuple[str, SourceKind, str],
            POCSourceSnapshot,
        ] = {}
        self._idempotency: dict[str, _IdempotencyRecord] = {}
        self._lock = RLock()

    @property
    def semantics(self) -> POCSourceServiceSemantics:
        return POCSourceServiceSemantics(
            max_pocs=self._max_pocs,
            max_sources_per_poc=self._max_sources_per_poc,
            max_candidates_per_source=self._max_candidates_per_source,
            max_idempotency_records=self._max_idempotency_records,
        )

    def __len__(self) -> int:
        with self._lock:
            return sum(len(items) for items in self._sources_by_poc.values())

    def _require_active_draft(self, poc_id: str) -> DraftPOCSnapshot:
        try:
            draft = self._draft_lookup(poc_id)
        except Exception as error:
            raise POCSourceDraftUnavailable(
                "Draft POC is unavailable in this process."
            ) from error
        if not isinstance(draft, DraftPOCSnapshot) or draft.poc_id != poc_id:
            raise POCSourceDraftUnavailable(
                "Draft lookup did not return the requested draft POC."
            )
        if draft.archive_state != DraftPOCArchiveState.ACTIVE:
            raise POCSourceDraftArchived("Archived draft POCs cannot accept sources.")
        return draft

    @contextmanager
    def authoring_commit_guard(
        self,
        poc_id: str,
        source_id: str,
        expected_source: POCSourceSnapshot,
    ):
        """Hold the source owner lock while an A3 publication is prepared.

        Source attachment already takes this lock before looking up the draft;
        A3 follows the same ``source -> draft`` order. The expected immutable
        source and latest identity are checked while the source lock is held,
        so a revision cannot pass a final unlocked read and race publication.
        """

        validated_poc_id = _validate_poc_id(poc_id)
        validated_source_id = _validate_source_id(source_id)
        if (
            type(expected_source) is not POCSourceSnapshot
            or expected_source.poc_id != validated_poc_id
            or expected_source.source_id != validated_source_id
        ):
            raise POCSourceStaleRevision(
                "The source owner snapshot does not match the requested source."
            )
        with self._lock:
            self._require_active_draft(validated_poc_id)
            current = self._by_id.get((validated_poc_id, validated_source_id))
            if current is None:
                raise POCSourceNotFound(
                    "Attached source is not present beneath this POC."
                )
            latest = self._identity_latest.get(
                (validated_poc_id, current.kind, current.external_id)
            )
            if (
                current != expected_source
                or latest is None
                or latest.source_id != current.source_id
            ):
                raise POCSourceStaleRevision(
                    "The source changed during assisted authoring."
                )
            yield _AuthoringCommitGuard()

    def attach(
        self,
        poc_id: str,
        prepared_source: PreparedPOCSource,
        idempotency_key: str,
    ) -> POCSourceAttachmentResult:
        """Atomically attach or safely replay one adapter-prepared source."""

        validated_poc_id = _validate_poc_id(poc_id)
        if type(prepared_source) is not PreparedPOCSource:
            raise TypeError("prepared_source must be a PreparedPOCSource.")
        key_digest = _idempotency_key_digest(idempotency_key)
        request_sha256 = _request_digest(
            validated_poc_id,
            prepared_source,
        )

        with self._lock:
            self._require_active_draft(validated_poc_id)

            prior = self._idempotency.get(key_digest)
            if prior is not None:
                if (
                    prior.poc_id != validated_poc_id
                    or prior.request_sha256 != request_sha256
                ):
                    raise POCSourceIdempotencyConflict(
                        "Idempotency key reuse does not match the original "
                        "source attach request."
                    )
                return POCSourceAttachmentResult(
                    source=self._by_id[(prior.poc_id, prior.source_id)],
                    disposition=SourceAttachDisposition.IDEMPOTENT_REPLAY,
                )

            identity_key = (
                validated_poc_id,
                prepared_source.kind,
                prepared_source.external_id,
            )
            latest = self._identity_latest.get(identity_key)

            if (
                latest is not None
                and latest.content_sha256 == prepared_source.content_sha256
            ):
                self._record_replay_key(
                    key_digest=key_digest,
                    poc_id=validated_poc_id,
                    request_sha256=request_sha256,
                    source_id=latest.source_id,
                )
                return POCSourceAttachmentResult(
                    source=latest,
                    disposition=SourceAttachDisposition.IDENTITY_REPLAY,
                )

            self._check_new_write_capacity(
                validated_poc_id,
                candidate_count=len(prepared_source.candidates),
            )
            source_revision = 1
            if latest is None:
                if prepared_source.revises_source_id is not None:
                    raise POCSourceStaleRevision(
                        "A new source identity cannot revise another source."
                    )
            else:
                if prepared_source.revises_source_id is None:
                    raise POCSourceRevisionRequired(
                        "Changed source content requires an explicit revision."
                    )
                if prepared_source.revises_source_id != latest.source_id:
                    raise POCSourceStaleRevision(
                        "Source revision must name the latest source."
                    )
                source_revision = latest.source_revision + 1

            source_id = _validate_source_id(self._id_factory())
            if any(
                existing_source_id == source_id for _, existing_source_id in self._by_id
            ):
                raise DuplicatePOCSourceId("The generated source ID already exists.")
            current_sources = self._sources_by_poc.get(
                validated_poc_id,
                (),
            )
            source_sequence = len(current_sources) + 1
            attached_at = self._clock()
            bound_candidates = tuple(
                AttachedRequirementCandidate(
                    candidate_id=candidate.candidate_id,
                    poc_id=validated_poc_id,
                    source_id=source_id,
                    source_sequence=source_sequence,
                    source_quote=candidate.source_quote,
                    normalized_claim=candidate.normalized_claim,
                    state=CandidateState.NEEDS_REVIEW,
                )
                for candidate in prepared_source.candidates
            )
            snapshot = POCSourceSnapshot(
                poc_id=validated_poc_id,
                source_id=source_id,
                source_sequence=source_sequence,
                source_revision=source_revision,
                kind=prepared_source.kind,
                external_id=prepared_source.external_id,
                redacted_text=prepared_source.redacted_text,
                content_sha256=prepared_source.content_sha256,
                candidates=bound_candidates,
                adapter_name=prepared_source.adapter_name,
                adapter_version=prepared_source.adapter_version,
                redaction_policy_version=(prepared_source.redaction_policy_version),
                observed_at=prepared_source.observed_at,
                attached_at=attached_at,
                revises_source_id=prepared_source.revises_source_id,
            )

            new_sources_by_poc = dict(self._sources_by_poc)
            new_sources_by_poc[validated_poc_id] = current_sources + (snapshot,)
            new_by_id = dict(self._by_id)
            new_by_id[(validated_poc_id, source_id)] = snapshot
            new_identity_latest = dict(self._identity_latest)
            new_identity_latest[identity_key] = snapshot
            new_idempotency = dict(self._idempotency)
            new_idempotency[key_digest] = _IdempotencyRecord(
                poc_id=validated_poc_id,
                request_sha256=request_sha256,
                source_id=source_id,
            )

            self._sources_by_poc = new_sources_by_poc
            self._by_id = new_by_id
            self._identity_latest = new_identity_latest
            self._idempotency = new_idempotency

            return POCSourceAttachmentResult(
                source=snapshot,
                disposition=SourceAttachDisposition.CREATED,
            )

    def _record_replay_key(
        self,
        *,
        key_digest: str,
        poc_id: str,
        request_sha256: str,
        source_id: str,
    ) -> None:
        if len(self._idempotency) >= self._max_idempotency_records:
            raise POCSourceCapacityExceeded(
                "The process-local idempotency store is at capacity."
            )
        new_idempotency = dict(self._idempotency)
        new_idempotency[key_digest] = _IdempotencyRecord(
            poc_id=poc_id,
            request_sha256=request_sha256,
            source_id=source_id,
        )
        self._idempotency = new_idempotency

    def _check_new_write_capacity(
        self,
        poc_id: str,
        *,
        candidate_count: int,
    ) -> None:
        if (
            poc_id not in self._sources_by_poc
            and len(self._sources_by_poc) >= self._max_pocs
        ):
            raise POCSourceCapacityExceeded(
                "The process-local POC source store is at capacity."
            )
        if len(self._sources_by_poc.get(poc_id, ())) >= self._max_sources_per_poc:
            raise POCSourceCapacityExceeded(
                "This draft POC has reached its source capacity."
            )
        if candidate_count > self._max_candidates_per_source:
            raise POCSourceCapacityExceeded(
                "Prepared source candidate count exceeds service capacity."
            )
        if len(self._idempotency) >= self._max_idempotency_records:
            raise POCSourceCapacityExceeded(
                "The process-local idempotency store is at capacity."
            )

    def poc_ids(self) -> Tuple[str, ...]:
        with self._lock:
            return tuple(sorted(self._sources_by_poc))

    def source_ids(self, poc_id: str) -> Tuple[str, ...]:
        validated_poc_id = _validate_poc_id(poc_id)
        with self._lock:
            return tuple(
                source.source_id
                for source in self._sources_by_poc.get(
                    validated_poc_id,
                    (),
                )
            )

    def snapshots(self, poc_id: str) -> Tuple[POCSourceSnapshot, ...]:
        validated_poc_id = _validate_poc_id(poc_id)
        with self._lock:
            return self._sources_by_poc.get(validated_poc_id, ())

    def get(self, poc_id: str, source_id: str) -> POCSourceSnapshot:
        validated_poc_id = _validate_poc_id(poc_id)
        validated_source_id = _validate_source_id(source_id)
        with self._lock:
            try:
                return self._by_id[(validated_poc_id, validated_source_id)]
            except KeyError as error:
                raise POCSourceNotFound(
                    "Attached source is not present beneath this POC."
                ) from error

    def latest_for_identity(
        self,
        poc_id: str,
        kind: SourceKind,
        external_id: str,
    ) -> POCSourceSnapshot:
        validated_poc_id = _validate_poc_id(poc_id)
        if type(kind) is not SourceKind:
            raise TypeError("kind must be a SourceKind.")
        if (
            type(external_id) is not str
            or _EXTERNAL_ID_RE.fullmatch(external_id) is None
        ):
            raise ValueError("external_id has an invalid format.")
        with self._lock:
            try:
                return self._identity_latest[(validated_poc_id, kind, external_id)]
            except KeyError as error:
                raise POCSourceNotFound(
                    "Source identity is not present beneath this POC."
                ) from error


__all__ = [
    "AttachedRequirementCandidate",
    "CandidateState",
    "DuplicatePOCSourceId",
    "POCSourceAttachmentResult",
    "POCSourceCapacityExceeded",
    "POCSourceDraftArchived",
    "POCSourceDraftUnavailable",
    "POCSourceError",
    "POCSourceIdempotencyConflict",
    "POCSourceNotFound",
    "POCSourceRevisionRequired",
    "POCSourceServiceSemantics",
    "POCSourceSnapshot",
    "POCSourceStaleRevision",
    "PreparedPOCSource",
    "PreparedRequirementCandidate",
    "ProcessLocalPOCSourceService",
    "SourceAttachDisposition",
    "SourceKind",
]
