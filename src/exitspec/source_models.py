"""Immutable provider-neutral source models for the frozen Wave 2 contract.

This module deliberately stops at the pure model/finalization boundary.  It
does not parse RFC822, persist state, allocate versions, or perform replay
lookups.  Transaction-owned values are explicit finalizer inputs so a store can
publish the returned envelope atomically.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import datetime
from enum import Enum
from typing import Any, Iterator, Literal, Mapping, Never, Self, Sequence

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)
from pydantic_core import core_schema


SCHEMA_VERSION = "exitspec-source-envelope/1.0"
SOURCE_TYPE = "rfc822"
SOURCE_AUTHORITY = "untrusted_source_only"
REDACTION_POLICY_VERSION = "exitspec-rfc822-redaction/1.0"
MANIFEST_ID = "exitspec-wave-2-synthetic-rfc822-intake"
MANIFEST_VERSION = "1.0.1"

MESSAGE_KEY_DOMAIN = "exitspec-rfc822-message-id-v1"
SOURCE_ID_DOMAIN = "exitspec-rfc822-thread-id-v1"
REDACTED_HEADER_DIGEST_DOMAIN = "exitspec-rfc822-redacted-headers-v1"
VERSION_ID_DOMAIN = "exitspec-source-version-v1"
CONTENT_DIGEST_DOMAIN = "exitspec-source-envelope-content-v1"

_ASCII_WHITESPACE = " \t\r\n\f\v"
_SHA256_PATTERN = r"^[a-f0-9]{64}$"
_MESSAGE_KEY_PATTERN = r"^msg:[a-f0-9]{64}$"
_SOURCE_ID_PATTERN = r"^rfc822:[a-f0-9]{64}$"
_VERSION_ID_PATTERN = r"^srcv:[a-f0-9]{64}$"
_PART_PATH_PATTERN = (
    r"^(body|attachment):"
    r"[a-z0-9!#$&^_.+-]+/[a-z0-9!#$&^_.+-]+:"
    r"(0|[1-9][0-9]*)$"
)
_RFC3339_SECONDS_PATTERN = r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T"
_RFC3339_SECONDS_PATTERN += r"[0-9]{2}:[0-9]{2}:[0-9]{2}Z$"
_RAW_EMAIL_PATTERN = re.compile(
    r"(?i)(?<![A-Z0-9._%+-])[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}"
    r"(?![A-Z0-9.-])"
)
_RAW_SECRET_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_])api_key=[A-Za-z0-9._-]+(?![A-Za-z0-9._-])"
)


class SourceType(str, Enum):
    RFC822 = SOURCE_TYPE


class SourceAuthority(str, Enum):
    UNTRUSTED_SOURCE_ONLY = SOURCE_AUTHORITY


class CandidateState(str, Enum):
    NEEDS_REVIEW = "NEEDS_REVIEW"


class PartKind(str, Enum):
    BODY = "body"
    ATTACHMENT = "attachment"


class SourceThreadBindingError(ValueError):
    """Content-free typed denial raised before any store-owned operation."""

    code = "source_thread_binding_mismatch"

    def __init__(self) -> None:
        super().__init__(self.code)


class ThreadParentNotFoundError(ValueError):
    """Content-free typed denial for a valid but unknown thread root."""

    code = "thread_parent_not_found"

    def __init__(self) -> None:
        super().__init__(self.code)


class PrivateSourceSerializationError(RuntimeError):
    """Refusal to expose request-local source-import provenance."""

    code = "private_source_serialization_forbidden"

    def __init__(self) -> None:
        super().__init__(self.code)


class SourceModelValidationError(RuntimeError):
    """Content-free validation failure for public source models."""

    code = "source_model_validation_failed"

    def __init__(self) -> None:
        super().__init__(self.code)


class PrivateSourceValidationError(RuntimeError):
    """Content-free validation failure for request-local private models."""

    code = "private_source_validation_failed"

    def __init__(self) -> None:
        super().__init__(self.code)


class _FrozenSourceModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
        revalidate_instances="always",
        serialize_by_alias=True,
        strict=True,
        validate_by_alias=True,
        validate_by_name=True,
    )

    @classmethod
    def _safe_validation_error(cls) -> RuntimeError:
        return SourceModelValidationError()

    @classmethod
    def __get_pydantic_core_schema__(
        cls,
        source_type: Any,
        handler: Any,
    ) -> core_schema.CoreSchema:
        schema = handler(source_type)

        def validate_safely(
            value: Any,
            validator: core_schema.ValidatorFunctionWrapHandler,
        ) -> Any:
            try:
                return validator(value)
            except (SourceModelValidationError, PrivateSourceValidationError):
                raise cls._safe_validation_error() from None
            except (ValidationError, ValueError, TypeError, AssertionError):
                raise cls._safe_validation_error() from None

        return core_schema.no_info_wrap_validator_function(
            validate_safely,
            schema,
        )

    def __setattr__(self, name: str, value: Any) -> Never:
        raise SourceModelValidationError()

    def __delattr__(self, name: str) -> Never:
        raise SourceModelValidationError()

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        """Copy safely, revalidating every requested update.

        Pydantic's default ``model_copy(update=...)`` intentionally skips
        validation. That is unsafe at this trust boundary because it can bypass
        redaction, digest, and provenance validators.
        """

        if not update:
            return super().model_copy(deep=deep)
        payload = self.model_dump(mode="python", by_alias=False)
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
        """Keep Pydantic V2's deprecated copy API from bypassing validation."""

        if include is not None or exclude is not None:
            raise SourceModelValidationError()
        return self.model_copy(update=update, deep=deep)


class _SafeSchemaValidatorProxy:
    """Sanitize JSON parse failures that happen before core-schema hooks."""

    __slots__ = ("_delegate", "_error_factory")

    def __init__(
        self,
        delegate: Any,
        error_factory: Any,
    ) -> None:
        self._delegate = delegate
        self._error_factory = error_factory

    def _call(self, method_name: str, *args: Any, **kwargs: Any) -> Any:
        try:
            return getattr(self._delegate, method_name)(*args, **kwargs)
        except (
            ValidationError,
            SourceModelValidationError,
            PrivateSourceValidationError,
            ValueError,
            TypeError,
            AssertionError,
        ):
            raise self._error_factory() from None

    def validate_python(self, *args: Any, **kwargs: Any) -> Any:
        return self._call("validate_python", *args, **kwargs)

    def validate_json(self, *args: Any, **kwargs: Any) -> Any:
        return self._call("validate_json", *args, **kwargs)

    def validate_strings(self, *args: Any, **kwargs: Any) -> Any:
        return self._call("validate_strings", *args, **kwargs)

    def validate_assignment(self, *args: Any, **kwargs: Any) -> Any:
        return self._call("validate_assignment", *args, **kwargs)

    def get_default_value(self, *args: Any, **kwargs: Any) -> Any:
        return self._delegate.get_default_value(*args, **kwargs)

    def isinstance_python(self, *args: Any, **kwargs: Any) -> Any:
        return self._delegate.isinstance_python(*args, **kwargs)

    @property
    def title(self) -> str:
        return self._delegate.title


def _require_rfc3339_seconds(value: str | None) -> str | None:
    if value is None:
        return None
    if re.fullmatch(_RFC3339_SECONDS_PATTERN, value) is None:
        raise ValueError("timestamp must be UTC RFC3339 at second precision")
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise ValueError("timestamp must be a valid UTC calendar value") from exc
    return value


def _require_redacted_text(value: str) -> str:
    if value != unicodedata.normalize("NFC", value):
        raise ValueError("persisted source text must be NFC-normalized")
    if "\r" in value or not value.endswith("\n") or value.endswith("\n\n"):
        raise ValueError("persisted source text must use normalized single-LF form")
    if _RAW_EMAIL_PATTERN.search(value) or _RAW_SECRET_PATTERN.search(value):
        raise ValueError("persisted source text contains an unredacted value")
    return value


def _require_redacted_header(value: str) -> str:
    if not value:
        raise ValueError("redacted header must not be empty")
    if value != unicodedata.normalize("NFC", value) or "\r" in value or "\n" in value:
        raise ValueError("redacted header must be unfolded NFC text")
    if _RAW_EMAIL_PATTERN.search(value) or _RAW_SECRET_PATTERN.search(value):
        raise ValueError("persisted source header contains an unredacted value")
    return value


def _walk_json(value: Any) -> None:
    if value is None or type(value) in (str, int, bool):
        return
    if isinstance(value, list):
        for item in value:
            _walk_json(item)
        return
    if isinstance(value, dict):
        if not all(type(key) is str for key in value):
            raise TypeError("canonical JSON object keys must be strings")
        for item in value.values():
            _walk_json(item)
        return
    raise TypeError("canonical JSON accepts only strings, integers, booleans, null, arrays, and objects")


def canonical_json(value: Mapping[str, Any] | Sequence[Any]) -> bytes:
    """Return the manifest-defined canonical JSON bytes (not RFC 8785)."""

    mutable_value: Any
    if isinstance(value, Mapping):
        mutable_value = dict(value)
    else:
        mutable_value = list(value)
    _walk_json(mutable_value)
    return json.dumps(
        mutable_value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _domain_digest(domain: str, projection: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        domain.encode("ascii") + b"\0" + canonical_json(projection)
    ).hexdigest()


def normalize_message_id(value: str) -> str:
    """Apply the exact V1 stable-identity normalization algorithm."""

    if type(value) is not str:
        raise TypeError("message identifier must be a string")
    normalized = value.strip(_ASCII_WHITESPACE)
    if normalized.startswith("<") and normalized.endswith(">"):
        normalized = normalized[1:-1]
    normalized = "".join(
        chr(ord(character) + 32)
        if "A" <= character <= "Z"
        else character
        for character in normalized
    )
    if not normalized:
        raise ValueError("message identifier must not be empty")
    return normalized


def compute_message_key(normalized_message_id: str) -> str:
    if normalize_message_id(normalized_message_id) != normalized_message_id:
        raise ValueError("message-key input must already be normalized")
    digest = hashlib.sha256(
        MESSAGE_KEY_DOMAIN.encode("ascii")
        + b"\0"
        + normalized_message_id.encode("utf-8")
    ).hexdigest()
    return f"msg:{digest}"


def compute_source_id(normalized_thread_root_message_id: str) -> str:
    if (
        normalize_message_id(normalized_thread_root_message_id)
        != normalized_thread_root_message_id
    ):
        raise ValueError("source-id input must already be normalized")
    digest = hashlib.sha256(
        SOURCE_ID_DOMAIN.encode("ascii")
        + b"\0"
        + normalized_thread_root_message_id.encode("utf-8")
    ).hexdigest()
    return f"rfc822:{digest}"


class RedactionCounts(_FrozenSourceModel):
    customer_term: int = Field(ge=0)
    email: int = Field(ge=0)
    phone: int = Field(ge=0)
    secret: int = Field(ge=0)

    def plus(self, other: "RedactionCounts") -> "RedactionCounts":
        return RedactionCounts(
            customer_term=self.customer_term + other.customer_term,
            email=self.email + other.email,
            phone=self.phone + other.phone,
            secret=self.secret + other.secret,
        )


class RedactionSummary(_FrozenSourceModel):
    policy_version: Literal[REDACTION_POLICY_VERSION] = REDACTION_POLICY_VERSION
    counts: RedactionCounts


class RedactedHeaders(_FrozenSourceModel):
    authored_at: str | None
    from_: str = Field(alias="from", serialization_alias="from")
    subject: str
    to: str

    _authored_at_validator = field_validator("authored_at")(
        _require_rfc3339_seconds
    )
    _header_validator = field_validator("from_", "subject", "to")(
        _require_redacted_header
    )


class SourcePart(_FrozenSourceModel):
    part_path: str = Field(pattern=_PART_PATH_PATTERN)
    kind: PartKind
    media_type: Literal["text/plain"] = "text/plain"
    redacted_text: str = Field(repr=False)
    redacted_text_sha256: str = Field(pattern=_SHA256_PATTERN)
    redacted_filename_sha256: str | None = Field(
        default=None,
        pattern=_SHA256_PATTERN,
    )

    _text_validator = field_validator("redacted_text")(_require_redacted_text)

    @model_validator(mode="after")
    def require_consistent_part(self) -> "SourcePart":
        prefix = f"{self.kind.value}:{self.media_type}:"
        if not self.part_path.startswith(prefix):
            raise ValueError("part_path must bind the declared kind and media_type")
        if self.kind is PartKind.BODY and self.redacted_filename_sha256 is not None:
            raise ValueError("body parts cannot carry a filename digest")
        if (
            self.kind is PartKind.ATTACHMENT
            and self.redacted_filename_sha256 is None
        ):
            raise ValueError("attachment parts require a filename digest")
        expected = hashlib.sha256(self.redacted_text.encode("utf-8")).hexdigest()
        if self.redacted_text_sha256 != expected:
            raise ValueError("redacted_text_sha256 does not match redacted_text")
        return self


class SourceMessage(_FrozenSourceModel):
    message_key: str = Field(pattern=_MESSAGE_KEY_PATTERN)
    redacted_headers: RedactedHeaders
    redacted_header_sha256: str = Field(pattern=_SHA256_PATTERN)
    parts: tuple[SourcePart, ...] = Field(min_length=1)

    @field_validator("parts", mode="before")
    @classmethod
    def normalize_parts(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def require_header_digest_and_unique_parts(self) -> "SourceMessage":
        if self.redacted_header_sha256 != compute_redacted_header_sha256(
            self.redacted_headers
        ):
            raise ValueError(
                "redacted_header_sha256 does not match redacted_headers"
            )
        paths = tuple(part.part_path for part in self.parts)
        if len(paths) != len(set(paths)):
            raise ValueError("message part paths must be unique")
        if sum(part.kind is PartKind.BODY for part in self.parts) != 1:
            raise ValueError("message requires exactly one inline body part")
        next_index: dict[tuple[PartKind, str], int] = {}
        for part in self.parts:
            identity = (part.kind, part.media_type)
            index = next_index.get(identity, 0)
            if part.part_path != f"{part.kind.value}:{part.media_type}:{index}":
                raise ValueError(
                    "part paths must use contiguous accepted MIME-order indexes"
                )
            next_index[identity] = index + 1
        return self


class CandidateProjection(_FrozenSourceModel):
    criterion_type: Literal["numeric_threshold"] = "numeric_threshold"
    metric: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    operator: Literal["gte", "lte", "gt", "lt", "eq"]
    threshold: str = Field(pattern=r"^-?(0|[1-9][0-9]*)(\.[0-9]+)?$")
    unit: str = Field(min_length=1)
    minimum_samples: int | None = Field(default=None, gt=0)


class PreparedCandidateDraft(_FrozenSourceModel):
    candidate_type: Literal["criterion"] = "criterion"
    state: Literal["NEEDS_REVIEW"] = "NEEDS_REVIEW"
    projection: CandidateProjection
    message_key: str = Field(pattern=_MESSAGE_KEY_PATTERN)
    part_path: str = Field(pattern=_PART_PATH_PATTERN)
    start_byte: int = Field(ge=0)
    end_byte: int = Field(gt=0)
    quote_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_nonempty_span(self) -> "PreparedCandidateDraft":
        if self.end_byte <= self.start_byte:
            raise ValueError("candidate span must be a non-empty half-open range")
        return self


class PreparedSourceEnvelope(_FrozenSourceModel):
    schema_version: Literal[SCHEMA_VERSION] = SCHEMA_VERSION
    source_type: Literal[SOURCE_TYPE] = SOURCE_TYPE
    synthetic: Literal[True] = True
    authority: Literal[SOURCE_AUTHORITY] = SOURCE_AUTHORITY
    source_id: str = Field(pattern=_SOURCE_ID_PATTERN)
    observed_at: str
    redaction: RedactionSummary
    message: SourceMessage
    candidate_drafts: tuple[PreparedCandidateDraft, ...]

    @field_validator("candidate_drafts", mode="before")
    @classmethod
    def normalize_candidate_drafts(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value

    _observed_at_validator = field_validator("observed_at")(
        _require_rfc3339_seconds
    )

    @model_validator(mode="after")
    def require_current_message_provenance(self) -> "PreparedSourceEnvelope":
        anchors = tuple(
            (
                draft.message_key,
                draft.part_path,
                draft.start_byte,
                draft.end_byte,
            )
            for draft in self.candidate_drafts
        )
        if len(anchors) != len(set(anchors)):
            raise ValueError("candidate draft source anchors must be unique")
        for draft in self.candidate_drafts:
            if draft.message_key != self.message.message_key:
                raise ValueError("candidate draft must cite the current message")
            _validate_candidate_anchor(draft, self.message)
        return self


class _PrivateSourceModel(_FrozenSourceModel):
    @classmethod
    def _safe_validation_error(cls) -> RuntimeError:
        return PrivateSourceValidationError()

    def __init__(self, **data: Any) -> None:
        try:
            super().__init__(**data)
        except (
            ValidationError,
            SourceModelValidationError,
            PrivateSourceValidationError,
        ):
            raise PrivateSourceValidationError() from None

    @classmethod
    def model_validate(
        cls,
        obj: Any,
        *args: Any,
        **kwargs: Any,
    ) -> Self:
        try:
            return super().model_validate(obj, *args, **kwargs)
        except (
            ValidationError,
            SourceModelValidationError,
            PrivateSourceValidationError,
        ):
            raise PrivateSourceValidationError() from None

    @classmethod
    def model_validate_json(
        cls,
        json_data: str | bytes | bytearray,
        *args: Any,
        **kwargs: Any,
    ) -> Self:
        try:
            return super().model_validate_json(json_data, *args, **kwargs)
        except (
            ValidationError,
            SourceModelValidationError,
            PrivateSourceValidationError,
        ):
            raise PrivateSourceValidationError() from None

    @classmethod
    def model_validate_strings(
        cls,
        obj: Any,
        *args: Any,
        **kwargs: Any,
    ) -> Self:
        try:
            return super().model_validate_strings(obj, *args, **kwargs)
        except (
            ValidationError,
            SourceModelValidationError,
            PrivateSourceValidationError,
        ):
            raise PrivateSourceValidationError() from None

    def __repr__(self) -> str:
        return f"{type(self).__name__}(<private>)"

    def __str__(self) -> str:
        return repr(self)

    def __iter__(self) -> Iterator[tuple[str, Any]]:
        raise PrivateSourceSerializationError()

    def __getstate__(self) -> Never:
        raise PrivateSourceSerializationError()

    def __reduce__(self) -> Never:
        raise PrivateSourceSerializationError()

    def __reduce_ex__(self, protocol: int) -> Never:
        raise PrivateSourceSerializationError()

    def __copy__(self) -> Never:
        raise PrivateSourceSerializationError()

    def __deepcopy__(self, memo: dict[int, Any]) -> Never:
        raise PrivateSourceSerializationError()

    def to_public_dict(self) -> Never:
        raise PrivateSourceSerializationError()

    def dict(self, *args: Any, **kwargs: Any) -> Never:
        raise PrivateSourceSerializationError()

    def copy(self, *args: Any, **kwargs: Any) -> Never:
        raise PrivateSourceSerializationError()

    def json(self, *args: Any, **kwargs: Any) -> Never:
        raise PrivateSourceSerializationError()

    def model_dump(self, *args: Any, **kwargs: Any) -> Never:
        raise PrivateSourceSerializationError()

    def model_dump_json(self, *args: Any, **kwargs: Any) -> Never:
        raise PrivateSourceSerializationError()

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Never:
        raise PrivateSourceSerializationError()


class ApprovedSyntheticFixture(_PrivateSourceModel):
    manifest_id: Literal[MANIFEST_ID] = Field(
        default=MANIFEST_ID,
        repr=False,
        exclude=True,
    )
    manifest_version: Literal[MANIFEST_VERSION] = Field(
        default=MANIFEST_VERSION,
        repr=False,
        exclude=True,
    )
    fixture_case_id: str = Field(
        pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$",
        repr=False,
        exclude=True,
    )
    synthetic_fixture_sha256: str = Field(
        pattern=_SHA256_PATTERN,
        repr=False,
        exclude=True,
    )


class PreparedSourceImport(_PrivateSourceModel):
    approved_synthetic_fixture: ApprovedSyntheticFixture = Field(
        repr=False,
        exclude=True,
    )
    normalized_thread_root_message_id: str = Field(repr=False, exclude=True)
    thread_root_message_key: str = Field(
        pattern=_MESSAGE_KEY_PATTERN,
        repr=False,
        exclude=True,
    )
    prepared_envelope: PreparedSourceEnvelope = Field(repr=False, exclude=True)


class SourceLinkedCandidate(_FrozenSourceModel):
    candidate_type: Literal["criterion"] = "criterion"
    state: Literal["NEEDS_REVIEW"] = "NEEDS_REVIEW"
    projection: CandidateProjection
    source_id: str = Field(pattern=_SOURCE_ID_PATTERN)
    source_version: int = Field(gt=0)
    version_id: str = Field(pattern=_VERSION_ID_PATTERN)
    message_key: str = Field(pattern=_MESSAGE_KEY_PATTERN)
    part_path: str = Field(pattern=_PART_PATH_PATTERN)
    start_byte: int = Field(ge=0)
    end_byte: int = Field(gt=0)
    quote_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_nonempty_span(self) -> "SourceLinkedCandidate":
        if self.end_byte <= self.start_byte:
            raise ValueError("candidate span must be a non-empty half-open range")
        return self


class SourceEnvelope(_FrozenSourceModel):
    schema_version: Literal[SCHEMA_VERSION] = SCHEMA_VERSION
    source_type: Literal[SOURCE_TYPE] = SOURCE_TYPE
    source_id: str = Field(pattern=_SOURCE_ID_PATTERN)
    source_version: int = Field(gt=0)
    version_id: str = Field(pattern=_VERSION_ID_PATTERN)
    observed_at: str
    ingested_at: str
    synthetic: Literal[True] = True
    authority: Literal[SOURCE_AUTHORITY] = SOURCE_AUTHORITY
    redaction: RedactionSummary
    messages: tuple[SourceMessage, ...] = Field(min_length=1)
    content_sha256: str = Field(pattern=_SHA256_PATTERN)
    candidates: tuple[SourceLinkedCandidate, ...]

    @field_validator("messages", "candidates", mode="before")
    @classmethod
    def normalize_collections(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value

    _timestamp_validator = field_validator("observed_at", "ingested_at")(
        _require_rfc3339_seconds
    )

    @model_validator(mode="after")
    def require_final_digest_and_bindings(self) -> "SourceEnvelope":
        if datetime.strptime(
            self.ingested_at,
            "%Y-%m-%dT%H:%M:%SZ",
        ) < datetime.strptime(
            self.observed_at,
            "%Y-%m-%dT%H:%M:%SZ",
        ):
            raise ValueError("ingested_at cannot precede observed_at")
        message_keys = tuple(message.message_key for message in self.messages)
        if len(message_keys) != len(set(message_keys)):
            raise ValueError("cumulative message keys must be unique")
        expected_version_id = compute_version_id(
            self.source_id,
            self.source_version,
            self.messages,
        )
        if self.version_id != expected_version_id:
            raise ValueError("version_id does not match the cumulative projection")
        expected_content_sha256 = compute_content_sha256(
            schema_version=self.schema_version,
            source_type=self.source_type,
            source_id=self.source_id,
            source_version=self.source_version,
            version_id=self.version_id,
            synthetic=self.synthetic,
            authority=self.authority,
            redaction=self.redaction,
            messages=self.messages,
        )
        if self.content_sha256 != expected_content_sha256:
            raise ValueError("content_sha256 does not match the exact projection")
        current_message = self.messages[-1]
        anchors = tuple(
            (
                candidate.message_key,
                candidate.part_path,
                candidate.start_byte,
                candidate.end_byte,
            )
            for candidate in self.candidates
        )
        if len(anchors) != len(set(anchors)):
            raise ValueError("current-version candidate source anchors must be unique")
        for candidate in self.candidates:
            if (
                candidate.source_id != self.source_id
                or candidate.source_version != self.source_version
                or candidate.version_id != self.version_id
                or candidate.message_key != current_message.message_key
            ):
                raise ValueError(
                    "candidate must bind to the current source version and message"
                )
            _validate_candidate_anchor(candidate, current_message)
        return self


def compute_redacted_header_sha256(headers: RedactedHeaders) -> str:
    projection = {
        "authored_at": headers.authored_at,
        "from": headers.from_,
        "subject": headers.subject,
        "to": headers.to,
    }
    return _domain_digest(REDACTED_HEADER_DIGEST_DOMAIN, projection)


def _version_message_projection(message: SourceMessage) -> dict[str, Any]:
    return {
        "message_key": message.message_key,
        "parts": [
            {
                "part_path": part.part_path,
                "redacted_filename_sha256": part.redacted_filename_sha256,
                "redacted_text_sha256": part.redacted_text_sha256,
            }
            for part in message.parts
        ],
        "redacted_header_sha256": message.redacted_header_sha256,
    }


def compute_version_id(
    source_id: str,
    source_version: int,
    messages: Sequence[SourceMessage],
) -> str:
    if re.fullmatch(_SOURCE_ID_PATTERN, source_id) is None:
        raise ValueError("source_id must use the frozen V1 format")
    if type(source_version) is not int or source_version <= 0:
        raise ValueError("source_version must be a positive JSON integer")
    if not messages:
        raise ValueError("version digest requires at least one message")
    projection = {
        "messages": [_version_message_projection(message) for message in messages],
        "source_id": source_id,
        "source_version": source_version,
    }
    return f"srcv:{_domain_digest(VERSION_ID_DOMAIN, projection)}"


def _redaction_projection(redaction: RedactionSummary) -> dict[str, Any]:
    return {
        "policy_version": redaction.policy_version,
        "counts": {
            "customer_term": redaction.counts.customer_term,
            "email": redaction.counts.email,
            "phone": redaction.counts.phone,
            "secret": redaction.counts.secret,
        },
    }


def _content_message_projection(message: SourceMessage) -> dict[str, Any]:
    return {
        "message_key": message.message_key,
        "redacted_headers": {
            "authored_at": message.redacted_headers.authored_at,
            "from": message.redacted_headers.from_,
            "subject": message.redacted_headers.subject,
            "to": message.redacted_headers.to,
        },
        "redacted_header_sha256": message.redacted_header_sha256,
        "parts": [
            {
                "part_path": part.part_path,
                "kind": part.kind.value,
                "media_type": part.media_type,
                "redacted_text": part.redacted_text,
                "redacted_text_sha256": part.redacted_text_sha256,
                "redacted_filename_sha256": part.redacted_filename_sha256,
            }
            for part in message.parts
        ],
    }


def compute_content_sha256(
    *,
    schema_version: str,
    source_type: str,
    source_id: str,
    source_version: int,
    version_id: str,
    synthetic: bool,
    authority: str,
    redaction: RedactionSummary,
    messages: Sequence[SourceMessage],
) -> str:
    if (
        schema_version != SCHEMA_VERSION
        or source_type != SOURCE_TYPE
        or synthetic is not True
        or authority != SOURCE_AUTHORITY
    ):
        raise ValueError("content digest constants must match the frozen V1 contract")
    if re.fullmatch(_SOURCE_ID_PATTERN, source_id) is None:
        raise ValueError("source_id must use the frozen V1 format")
    if type(source_version) is not int or source_version <= 0:
        raise ValueError("source_version must be a positive JSON integer")
    if re.fullmatch(_VERSION_ID_PATTERN, version_id) is None:
        raise ValueError("version_id must use the frozen V1 format")
    if not messages:
        raise ValueError("content digest requires at least one message")
    projection = {
        "schema_version": schema_version,
        "source_type": source_type,
        "source_id": source_id,
        "source_version": source_version,
        "version_id": version_id,
        "synthetic": synthetic,
        "authority": authority,
        "redaction": _redaction_projection(redaction),
        "messages": [_content_message_projection(message) for message in messages],
    }
    return _domain_digest(CONTENT_DIGEST_DOMAIN, projection)


def validate_source_thread_binding(
    prepared_import: PreparedSourceImport,
    stored_root_sources: Mapping[str, str] | None = None,
) -> None:
    """Validate private source/thread provenance without mutating any state."""

    try:
        normalized_root = prepared_import.normalized_thread_root_message_id
        if normalize_message_id(normalized_root) != normalized_root:
            raise SourceThreadBindingError()
        expected_root_key = compute_message_key(normalized_root)
        expected_source_id = compute_source_id(normalized_root)
        envelope = prepared_import.prepared_envelope
        if (
            prepared_import.thread_root_message_key != expected_root_key
            or envelope.source_id != expected_source_id
        ):
            raise SourceThreadBindingError()

        current_message_key = envelope.message.message_key
        is_root = expected_root_key == current_message_key
        if is_root:
            if prepared_import.thread_root_message_key != current_message_key:
                raise SourceThreadBindingError()
        elif (
            stored_root_sources is None
            or expected_root_key not in stored_root_sources
        ):
            raise ThreadParentNotFoundError()
        elif stored_root_sources.get(expected_root_key) != expected_source_id:
            raise SourceThreadBindingError()
    except (SourceThreadBindingError, ThreadParentNotFoundError):
        raise
    except (TypeError, ValueError, UnicodeError):
        raise SourceThreadBindingError() from None


def _validate_candidate_anchor(
    candidate: PreparedCandidateDraft | SourceLinkedCandidate,
    message: SourceMessage,
) -> None:
    matching_parts = tuple(
        part for part in message.parts if part.part_path == candidate.part_path
    )
    if len(matching_parts) != 1:
        raise ValueError("candidate part_path does not identify one current part")
    encoded = matching_parts[0].redacted_text.encode("utf-8")
    if candidate.end_byte > len(encoded):
        raise ValueError("candidate byte range exceeds the persisted part")
    quote = encoded[candidate.start_byte : candidate.end_byte]
    if not quote or hashlib.sha256(quote).hexdigest() != candidate.quote_sha256:
        raise ValueError("candidate quote digest does not match the exact byte range")


def finalize_source_envelope(
    prepared: PreparedSourceEnvelope,
    *,
    source_version: int,
    ingested_at: str,
    prior_envelope: SourceEnvelope | None = None,
) -> SourceEnvelope:
    """Finalize one prepared message using store-owned transaction values."""

    _require_rfc3339_seconds(ingested_at)
    if type(source_version) is not int or source_version <= 0:
        raise ValueError("source_version must be a positive integer")

    if prior_envelope is None:
        if source_version != 1:
            raise ValueError("a first source envelope must use source_version 1")
        messages = (prepared.message,)
        counts = prepared.redaction.counts
    else:
        if prepared.source_id != prior_envelope.source_id:
            raise ValueError("prepared source_id must match the prior source")
        if source_version != prior_envelope.source_version + 1:
            raise ValueError("source_version must advance exactly once")
        if prepared.message.message_key in {
            message.message_key for message in prior_envelope.messages
        }:
            raise ValueError("a new source version requires a new message key")
        messages = prior_envelope.messages + (prepared.message,)
        counts = prior_envelope.redaction.counts.plus(prepared.redaction.counts)

    cumulative_redaction = RedactionSummary(
        policy_version=prepared.redaction.policy_version,
        counts=counts,
    )
    version_id = compute_version_id(prepared.source_id, source_version, messages)
    content_sha256 = compute_content_sha256(
        schema_version=prepared.schema_version,
        source_type=prepared.source_type,
        source_id=prepared.source_id,
        source_version=source_version,
        version_id=version_id,
        synthetic=prepared.synthetic,
        authority=prepared.authority,
        redaction=cumulative_redaction,
        messages=messages,
    )
    candidates = tuple(
        SourceLinkedCandidate(
            candidate_type=draft.candidate_type,
            state=draft.state,
            projection=draft.projection,
            source_id=prepared.source_id,
            source_version=source_version,
            version_id=version_id,
            message_key=draft.message_key,
            part_path=draft.part_path,
            start_byte=draft.start_byte,
            end_byte=draft.end_byte,
            quote_sha256=draft.quote_sha256,
        )
        for draft in prepared.candidate_drafts
    )
    return SourceEnvelope(
        schema_version=prepared.schema_version,
        source_type=prepared.source_type,
        source_id=prepared.source_id,
        source_version=source_version,
        version_id=version_id,
        observed_at=prepared.observed_at,
        ingested_at=ingested_at,
        synthetic=prepared.synthetic,
        authority=prepared.authority,
        redaction=cumulative_redaction,
        messages=messages,
        content_sha256=content_sha256,
        candidates=candidates,
    )


for _source_model in (
    RedactionCounts,
    RedactionSummary,
    RedactedHeaders,
    SourcePart,
    SourceMessage,
    CandidateProjection,
    PreparedCandidateDraft,
    PreparedSourceEnvelope,
    ApprovedSyntheticFixture,
    PreparedSourceImport,
    SourceLinkedCandidate,
    SourceEnvelope,
):
    _source_model.__pydantic_validator__ = _SafeSchemaValidatorProxy(
        _source_model.__pydantic_validator__,
        _source_model._safe_validation_error,
    )
del _source_model
